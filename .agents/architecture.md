# Architecture

## System overview

`ollama-provider` adalah **satu proses Python** (`python -m gateway`) yang menjalankan dua server uvicorn di event loop yang sama:

| Server | Bind | App factory | Diekspos ke internet? |
|---|---|---|---|
| Publik | `127.0.0.1:8000` | `create_public_app()` di `gateway/apps/public.py` | Ya, lewat Cloudflare Tunnel (path `^/(v1\|health)(/.*)?$`) |
| Admin | `127.0.0.1:8001` | `create_admin_app()` di `gateway/apps/admin.py` | **Tidak pernah** |

Upstream-nya Ollama di `127.0.0.1:11434` (service terpisah). Tidak ada frontend.

```
                  +------------------------ proses OllamaGateway ------------------------+
Client -> CF -> cloudflared -> :8000 public app --+                                        |
                                                  +--> Gateway (core.py) --> OllamaClient --+--> Ollama :11434
Operator (lokal) ---------> :8001 admin app ------+     |  db (SQLite) | limits | keys      |
                                                        |  pulls | health | usage writer    |
                  +---------------------------------------------------------------------+
```

## Entry point & lifecycle (`gateway/__main__.py`)

1. `get_settings()`, lalu `setup_logging(settings)` (`logs.py`).
2. `Gateway(settings)` dan `await gw.start()`:
   - `db.connect()` (buat schema bila belum ada), lalu `db.seed_models(SEED_MODELS)`
   - `UsageWriter.start()` (antrean batch penulisan usage)
   - task `_prune_loop` (hapus usage lebih tua dari `USAGE_RETENTION_DAYS`, sekali sehari)
   - task `warmup` (load model + inferensi 1 token) jika `WARMUP=true`
   - task `HealthMonitor.run` jika `HEALTH_INTERVAL_S > 0`
3. Dua `uvicorn.Server` dijalankan dengan `asyncio.gather`. `capture_signals` di-override menjadi no-op, dan SIGINT/SIGTERM/SIGBREAK ditangani sekali untuk kedua server (menyetel `should_exit`).
4. Saat shutdown, `gw.stop()` membatalkan task, mem-flush usage writer, menutup client httpx, lalu DB.

Konfigurasi uvicorn: `lifespan="off"`, `access_log=False`, `log_config=None`, `server_header=False`, `proxy_headers=False`, `timeout_graceful_shutdown=10`. Hanya server publik yang memakai `limit_concurrency=LIMIT_CONCURRENCY` (64).

## Komponen & hubungan

| Komponen | File | Tanggung jawab | Dipakai oleh |
|---|---|---|---|
| `Settings` | `config.py` | Semua konfigurasi dari env / `.env` | Semua |
| `Gateway` | `core.py` | Container state bersama + background task | Kedua app (`app.state.gw`) |
| `Database`, `UsageRecord`, `UsageWriter` | `db.py` | Schema, query, penulisan usage secara batch | `Gateway`, CLI |
| `OllamaClient` | `ollama.py` | `httpx.AsyncClient` ke Ollama, cache capability, pull/delete/warm-up | proxy, admin, health |
| `SlidingWindow`, `FailedAuthLimiter`, `ConcurrencyGate`, `RateResult` | `limits.py` | RPM per key, gagal-auth per IP, slot per key + global | public app, proxy |
| `KeyCache`, `require_api_key`, `require_admin` | `auth.py` | Lookup key (hash SHA-256), dependency FastAPI | public/admin app |
| `sanitize_*`, `read_json_body` | `sanitize.py` | Allowlist field, validasi, cap `max_tokens`, batas body | public app |
| `forward_json`, `stream_sse`, `Finalizer`, `ClosingStreamingResponse` | `proxy.py` | Meneruskan request ke Ollama `/v1/*` | public app |
| `APIError`, `install_handlers` | `errors.py` | Format error OpenAI + exception handler | Semua |
| `RequestContextMiddleware` | `middleware.py` | `x-request-id` + satu baris access log JSON | Kedua app |
| `HealthMonitor` | `health.py` | Cek dependency berkala, log transisi, snapshot | `Gateway`, `/admin/status` |
| `PullManager` | `core.py` | Job `ollama pull` di background | admin app |
| `DailyFileHandler`, `setup_logging` | `logs.py` | File log harian | `__main__`, CLI |
| CLI | `cli.py` | Manajemen key/model/usage langsung ke DB | Operator |

## Alur request publik (`POST /v1/chat/completions`)

```
RequestContextMiddleware  (buat request_id, state.access = {})
  -> require_api_key       (blokir IP dengan >= 20 gagal/menit -> 429; lookup hash via KeyCache/DB -> 401)
  -> _generate()  [apps/public.py]
       1. gw.rpm.hit(key)                  -> 429 rate_limit_exceeded (+ Retry-After)
       2. read_json_body (hitung byte)     -> 413 / 400
       3. require_model + is_model_enabled -> 404 model_not_found
       4. ollama.capabilities(model)       -> /api/show (cache 10 menit)
       5. sanitize_chat/completion/embeddings -> 400 (payload hanya berisi field allowlist)
       6. gate.try_acquire_key             -> 429 concurrency_limit_exceeded
          (setiap APIError di langkah 1-6 -> UsageRecord dengan status error)
       7a. stream=false -> forward_json():
             gate.acquire_global(QUEUE_TIMEOUT_S) -> 503 server_busy
             POST Ollama /v1/...  -> JSONResponse (status upstream diteruskan)
       7b. stream=true  -> stream_sse() -> ClosingStreamingResponse(_sse generator):
             yield ": connected"; tunggu slot global sambil yield ": ping" tiap 15 dtk
             upstream stream; baca per baris; ambil usage dari chunk terakhir (choices=[])
             buang chunk usage jika client tidak minta include_usage
             error setelah header -> data: {"error": ...}
       Finalizer() (tepat sekali): lepas slot global + slot key, submit UsageRecord,
                                    tulis status/token nyata ke state.access
  <- middleware menulis baris JSON ke access-YYYY-MM-DD.log setelah response selesai
```

Pemetaan error transport (`proxy.map_transport_error`): ConnectError/ConnectTimeout → 503 `upstream_unavailable`, timeout lain → 504 `upstream_timeout`, error transport lain → 502 `bad_gateway`. Client putus di tengah stream → status 499 (`client_disconnected`); generator di-`aclose()` sehingga stream upstream ikut tertutup dan Ollama berhenti generate.

## Alur data

| Data | Asal | Tujuan |
|---|---|---|
| Hash API key | admin API / CLI | tabel `api_keys` → `KeyCache` (TTL 30 dtk) |
| Allowlist model | `SEED_MODELS` (start), admin API, CLI | tabel `models` (dibaca setiap request) |
| Usage per request | `Finalizer` / jalur error `_generate` | `UsageWriter` (antrean, batch ≤200) → `usage_logs` |
| Access log | `RequestContextMiddleware` + `state.access` | `logs/access-YYYY-MM-DD.log` (JSON lines) |
| Audit | `gateway.admin`, `gateway.cli`, `gateway.auth` logger | `logs/gateway-YYYY-MM-DD.log` |
| Health | `HealthMonitor` | `logs/health-*.log` (+ transisi ke `gateway-*.log`), `/admin/status` |

## API structure

### Publik (port 8000)

| Method | Path | Auth | Handler |
|---|---|---|---|
| GET | `/health` | tidak | `health()` → `{"status":"ok"}` |
| GET | `/health/ready` | tidak | `ready()` → 200 `ready` / 503 `unavailable` (cek `/api/version`) |
| GET | `/v1/models` | Bearer API key | model `enabled=1` dari DB (tidak dihitung ke RPM) |
| POST | `/v1/chat/completions` | Bearer API key | `_generate(..., "chat")` |
| POST | `/v1/completions` | Bearer API key | `_generate(..., "completions")` |
| POST | `/v1/embeddings` | Bearer API key | `_generate(..., "embeddings")`, hanya untuk model dengan capability `embedding` |

`docs_url`, `redoc_url`, dan `openapi_url` di-nonaktifkan. Path lain → 404 format OpenAI. Header respons: `x-request-id`, dan untuk POST generasi juga `x-ratelimit-limit-requests`, `x-ratelimit-remaining-requests`, `x-ratelimit-reset-requests`.

### Admin (port 8001, prefix `/admin`, semua butuh `Authorization: Bearer <ADMIN_KEY>`)

| Method | Path | Fungsi |
|---|---|---|
| POST | `/admin/keys` | Buat key (`name`, `rpm_limit?`, `max_concurrent?`). Plaintext dikembalikan sekali |
| GET | `/admin/keys?include_revoked=` | Daftar key (tanpa hash) + `*_effective` |
| PATCH | `/admin/keys/{id}` | Ubah `name`/`rpm_limit`/`max_concurrent` (`null` = kembali ke default) |
| DELETE | `/admin/keys/{id}` | Revoke (soft delete) dan kosongkan `KeyCache` |
| GET | `/admin/usage?since&until&key_id&group_by=day\|key\|model` | Ringkasan (default 7 hari) |
| GET | `/admin/usage/recent?limit&key_id` | Baris `usage_logs` terbaru |
| GET | `/admin/models` | Gabungan `/api/tags` dan allowlist DB |
| PATCH | `/admin/models/{name:path}` | `{"enabled": bool}`. Enable hanya untuk model yang terinstal |
| POST | `/admin/models/pull` | `{"model", "enable"}` → job 202 (background) |
| GET | `/admin/models/pull`, `/admin/models/pull/{job}` | Status job pull (disimpan di memori, maks 50) |
| DELETE | `/admin/models/{name:path}` | Hapus di Ollama + hapus dari allowlist |
| GET | `/admin/status` | `HealthMonitor.check()` live |
| POST | `/admin/backup` | `VACUUM INTO data/backups/gateway-<ts>.db` |

Swagger admin tersedia di `/admin/docs` (tanpa auth, tapi hanya bisa diakses dari loopback).

## Middleware & error handling

Urutan stack Starlette: `ServerErrorMiddleware` → `RequestContextMiddleware` → `ExceptionMiddleware` → router.

- Handler untuk `APIError`, `StarletteHTTPException`, dan `RequestValidationError` (→ 400) berjalan di `ExceptionMiddleware`. Semuanya lewat `_respond()`, yang menulis `access["error"]`.
- Handler `Exception` (500) berjalan di `ServerErrorMiddleware`, di **luar** middleware access log. Karena itu `RequestContextMiddleware` menangkap exception yang lewat, menulis `access["error"] = "internal_error"`, lalu me-raise ulang. Baris access log-nya menjadi status 500 + `error: internal_error`.
- Format error selalu `{"error": {"message", "type", "param", "code"}}`. `mask_secrets()` mengganti pola `sk-...` dengan `sk-***`.

## Health monitor (`gateway/health.py`)

Setiap `HEALTH_INTERVAL_S` (30 dtk), monitor mengumpulkan data secara paralel (`asyncio.gather` + `to_thread`):
- Ollama: `/api/version` dan `/api/ps` → komponen `ollama` dan `model_gpu` (warn jika `size_vram < size`)
- Service Windows (`WATCH_SERVICES`) lewat **ctypes advapi32** (`OpenSCManagerW`/`QueryServiceStatus`), tanpa subprocess → `service:<nama>` (`ok`/`down`/`absent`)
- GPU lewat `nvidia-smi --query-gpu` → `gpu` (warn jika suhu ≥ `GPU_TEMP_WARN_C`)
- Disk (`MONITOR_PATHS`, default drive DB + system drive) → `disk:<drive>` (warn jika sisa < `DISK_WARN_GB`)

Yang dicatat hanya **perubahan** status (`is down` / `recovered`). Snapshot JSON lengkap ditulis tiap `HEALTH_SNAPSHOT_INTERVAL_S`. Jika Ollama hidup tetapi tidak ada model yang dimuat, warm-up dijalankan ulang (paling sering sekali per 600 dtk, dengan masa tenggang 60 dtk setelah start).

## External services / integrasi

| Integrasi | Cara | Lokasi kode |
|---|---|---|
| Ollama OpenAI-compat | `POST /v1/chat/completions`, `/v1/completions`, `/v1/embeddings` | `proxy.py` |
| Ollama native | `/api/version`, `/api/tags`, `/api/ps`, `/api/show`, `/api/pull`, `/api/delete`, `/api/generate` (warm-up) | `ollama.py` |
| NVIDIA driver | subprocess `nvidia-smi` | `health.gpu_stats` |
| Windows SCM | ctypes `advapi32` | `health.service_states` |
| Cloudflare Tunnel | Service `cloudflared` (token, remotely-managed). Header `CF-Connecting-IP` dipercaya untuk IP client | `scripts/install-cloudflared.ps1`, `middleware.scope_client_ip` |
| WinSW v2.12 | Wrapper Windows Service (XML di `D:\ollama\services`) | `scripts/common.ps1` |

Timeout `httpx`: connect 5 dtk, read 300 dtk, write 30 dtk, pool 30 dtk. Limit koneksi 100 (keep-alive 20).

## Design patterns

- **Shared state container**: satu `Gateway` disimpan di `app.state.gw` untuk kedua app.
- **Dependency injection FastAPI** untuk auth (`Depends(require_api_key)`, dependency router admin).
- **Idempotent finalizer**: `Finalizer.__call__` dengan flag `_done`, dipanggil dari generator `finally` **dan** dari `ClosingStreamingResponse.__call__` (untuk kasus generator yang tidak pernah dimulai).
- **Allowlist, bukan denylist**, untuk field request.
- **Background tasks** lewat `Gateway.spawn()`, yang menyimpan referensi task dan membatalkannya saat stop.
- **Write-behind queue** untuk usage (`UsageWriter`).
- **Transition logging** untuk health: log hanya saat status berubah.

## Aturan arsitektur

1. Semua endpoint yang meneruskan ke Ollama wajib: auth → RPM → validasi/sanitasi → slot key → slot global (dengan batas tunggu) → `Finalizer`.
2. Jangan menambah listener yang bind ke `0.0.0.0`. Semua bind ke `127.0.0.1`; akses luar hanya lewat cloudflared.
3. Route admin tidak boleh dipasang di app publik, begitu pula sebaliknya.
4. `GLOBAL_CONCURRENCY` harus sama dengan `OLLAMA_NUM_PARALLEL` (di XML service `OllamaServe`).
5. Request non-stream harus selesai < 100 dtk (batas first-byte Cloudflare), yang dijaga lewat `MAX_TOKENS_CAP`.
6. Kode baru yang blocking harus lewat `asyncio.to_thread`. Event loop dipakai bersama oleh kedua server.
