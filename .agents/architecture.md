# Architecture

## System overview

`ollama-provider` adalah **satu proses Python** (`python -m gateway`) yang menjalankan dua server uvicorn di event loop yang sama:

| Server | Bind | App factory | Diekspos ke internet? |
|---|---|---|---|
| Publik | `127.0.0.1:8000` | `create_public_app()` di `gateway/apps/public.py` | Ya, lewat tunnel **ngrok** (aktif, service `NgrokTunnel`). Cloudflare Tunnel tersedia sebagai alternatif berbayar (butuh domain, path `^/(v1\|health)(/.*)?$`) |
| Admin | `127.0.0.1:8001` | `create_admin_app()` di `gateway/apps/admin.py` | **Tidak pernah**. Pemilik membukanya lewat SSH port forward |

Upstream-nya Ollama di `127.0.0.1:11434` (service terpisah). Satu-satunya frontend adalah dashboard admin statis di `/admin/ui` (port 8001, lihat [Dashboard](#dashboard-frontend)).

```
                       +--------------------- proses OllamaGateway (LocalService) -----------------+
Client -HTTPS-> ngrok edge -> ngrok agent -> :8000 public app --+                                    |
                (NgrokTunnel, SYSTEM)                           +--> Gateway (core.py) --> OllamaClient --+--> Ollama :11434
Pemilik -SSH (Tailscale) -L 8001-> :8001 admin app + /admin/ui -+     db | limits | keys | pulls      |    (OllamaServe, SYSTEM)
                                                                      scheduler | quota | health      |
                                                                      usage writer (usage + prompt)   |
                       +----------------------------------------------------------------------------+
```

## Topologi runtime (Windows Service)

| Service | Akun | Executable | Bergantung pada | Log |
|---|---|---|---|---|
| `OllamaServe` | LocalSystem | `D:\ollama\bin\ollama.exe serve` (runner anak: `llama-server.exe`) | - | `D:\ollama\logs\OllamaServe.*.log` |
| `OllamaGateway` | **NT AUTHORITY\LocalService** | `C:\Program Files\Python311\python.exe -m gateway` + `PYTHONPATH` ke `.venv\Lib\site-packages` | `OllamaServe` | `logs\gateway-/access-/health-*.log`, `logs\OllamaGateway.*.log` |
| `NgrokTunnel` | LocalSystem | `D:\ollama\ngrok\ngrok.exe start --all --config D:\ollama\ngrok\ngrok.yml` | - | `D:\ollama\logs\ngrok\` |
| `Cloudflared` | LocalSystem | *(belum terpasang; alternatif)* | - | `D:\ollama\logs\cloudflared\` |

- Semua dibungkus **WinSW v2.12** (exe + XML per service di `D:\ollama\services`), start otomatis, restart saat crash (5/10/30 dtk), stop graceful lewat Ctrl+C.
- `D:\ollama` hanya bisa ditulis SYSTEM/Administrators (`Protect-AdminOnlyTree`). Gateway memakai akun hak rendah karena folder project bisa diedit akun lain.
- Setelah boot, semua service start sendiri tanpa login (teruji 2026-10-17 saat Windows Update me-restart PC). Warm-up memuat `gemma3:4b` (~40 dtk pada boot pertama setelah update).

## Model & mode berpikir

- Allowlist aktif (tabel `models`): `gemma3:4b` (utama) dan `qwen3.5:4b` (tools/JSON). `OLLAMA_MAX_LOADED_MODELS=1`: hanya satu model di VRAM, dan berpindah model memicu muat ulang ~4 dtk.
- Warm-up dan re-warm memilih model aktif pertama secara alfabet (`enabled_models()` diurutkan nama), yaitu `gemma3:4b`. Kalau model utama berganti, perlu setting eksplisit (`PRIMARY_MODEL` belum ada).
- Capability dari `/api/show` menentukan perilaku `sanitize.py`: `vision` (gambar base64), `tools`, `thinking` (`reasoning_effort` disuntik `DEFAULT_REASONING_EFFORT`; `/v1/completions` ditolak), `embedding`.

## Dashboard (frontend)

- File statis di `gateway/static/dashboard/` (`index.html`, `app.css`, `app.js`), disajikan app admin di `/admin/ui` tanpa auth. Halaman meminta `ADMIN_KEY` lalu memanggil admin API dengan header Bearer.
- Vanilla JS (ES module) tanpa framework, build step, atau CDN. DOM dibangun lewat helper `h()`/`s()` dengan `textContent`; `innerHTML` dilarang (dicek test). Grafik SVG buatan sendiri.
- Keamanan: CSP `default-src 'none'; script-src 'self'; style-src 'self'; ... frame-ancestors 'none'`, `X-Frame-Options: DENY`, `Cache-Control: no-store`, `TrustedHostMiddleware` (`ADMIN_ALLOWED_HOSTS`, anti DNS rebinding), admin key di `sessionStorage`.
- Menu (`data-view`): *Ringkasan* (status dependency, panel *Antrean GPU per model* dari `concurrency.lanes`), *API key* (buat/ubah/cabut, *Batas lanjutan* untuk kebijakan per key, pesan untuk client), *Pemakaian* (`/admin/usage`), *Request* (`/admin/usage/recent`), *Prompt* (log prompt), dan *Model* (allowlist, pull, hapus).
- Polling: status tiap 10 dtk (`REFRESH_MS`). Halaman Ringkasan dan Request diperbarui otomatis; job pull diperbarui tiap 2 dtk selama ada yang berjalan. Form tidak pernah di-reset saat polling atau saat tab kembali aktif (`visibilitychange`). Form kebijakan key baru hanya di-reset saat daftar model berubah atau setelah key dibuat. Admin GET yang sukses tidak dicatat di access log.
- Menu *Prompt* sengaja **tidak** ikut auto-refresh maupun `visibilitychange`. Isinya:
  - rentang waktu wajib (1 jam s.d. 30 hari);
  - 50 baris per permintaan, tombol *Muat 50 lagi* (kursor `next`), maksimal `PROMPT_MAX_ROWS` = 500 baris di tabel;
  - penjaga `state.prompts.seq`: jawaban dari permintaan lama dibuang bila filter sudah berubah, dan tombol dinonaktifkan selama memuat;
  - isi lengkap baru diambil (`/admin/prompts/{id}`) saat *Lihat* diklik, ditampilkan di `dialog.wide`.
- Dialog *Ubah* key hanya mengirim field yang berubah (`policyFields.changed()`), sehingga PATCH tidak menimpa nilai yang diubah di tempat lain.

## Resolusi IP client

`RequestContextMiddleware` menghitung IP sekali per request (`request.state.client_ip`) dari header pertama yang ada di `CLIENT_IP_HEADERS` (default `cf-connecting-ip,x-forwarded-for`). Untuk `X-Forwarded-For` dipakai entri **terakhir**, yaitu yang ditambahkan agen tunnel (ngrok). Header ini hanya dipercaya karena port 8000 cuma bisa dicapai proses lokal.

## Entry point & lifecycle (`gateway/__main__.py`)

1. `get_settings()`, lalu `setup_logging(settings)` (`logs.py`).
2. `Gateway(settings)` dan `await gw.start()`:
   - Konstruktor membuat `Database`, `OllamaClient`, `SlidingWindow` (RPM), `FailedAuthLimiter`, `ConcurrencyGate` (slot per key), `GpuScheduler` (`GLOBAL_CONCURRENCY`, `MAX_QUEUE`, `MODEL_SWITCH_AFTER_S`, `PRIORITY_RESERVED_SLOTS`), `QuotaTracker`, `KeyCache`, `PullManager`, `UsageWriter`, `HealthMonitor`
   - `db.connect()`: PRAGMA, schema (`CREATE ... IF NOT EXISTS`), migrasi aditif `KEY_MIGRATIONS`, lalu membuka koneksi read-only kedua (`_reader`). Setelah itu `db.seed_models(SEED_MODELS)`
   - `UsageWriter.start()` (antrean batch penulisan `UsageRecord` dan `PromptRecord`)
   - task `_prune_loop`: hapus `usage_logs` lebih tua dari `USAGE_RETENTION_DAYS` dan `prompt_logs` lebih tua dari `PROMPT_LOG_RETENTION_DAYS`, saat start lalu setiap 24 jam
   - task `warmup` (load model + inferensi 1 token, lewat scheduler dengan timeout 0) jika `WARMUP=true`
   - task `HealthMonitor.run` jika `HEALTH_INTERVAL_S > 0`
3. Dua `uvicorn.Server` dijalankan dengan `asyncio.gather`. `capture_signals` di-override menjadi no-op, dan SIGINT/SIGTERM/SIGBREAK ditangani sekali untuk kedua server: menyetel `should_exit` dan menjadwalkan `gw.scheduler.close()` lewat `loop.call_soon_threadsafe`.
4. Saat shutdown: request yang antre langsung mendapat 503 "the server is restarting", request yang berjalan diberi `timeout_graceful_shutdown` 10 dtk, lalu `gw.stop()` membatalkan task, mem-flush usage writer (usage + log prompt yang tersisa), menutup client httpx, lalu kedua koneksi DB.

Konfigurasi uvicorn: `lifespan="off"`, `access_log=False`, `log_config=None`, `server_header=False`, `proxy_headers=False`, `timeout_graceful_shutdown=10`. Hanya server publik yang memakai `limit_concurrency=LIMIT_CONCURRENCY` (64).

## Komponen & hubungan

| Komponen | File | Tanggung jawab | Dipakai oleh |
|---|---|---|---|
| `Settings` | `config.py` | Semua konfigurasi dari env / `.env` | Semua |
| `Gateway` | `core.py` | Container state bersama + background task | Kedua app (`app.state.gw`) |
| `Database`, `UsageRecord`, `PromptRecord`, `UsageWriter` | `db.py` | Schema + migrasi aditif, query, penulisan usage dan log prompt secara batch. Dua koneksi: utama (baca/tulis) dan `_reader` (read-only, khusus bacaan log prompt) | `Gateway`, CLI |
| `OllamaClient`, `ModelInfo` | `ollama.py` | `httpx.AsyncClient` ke Ollama, cache capability + arsitektur (`/api/show`), pull/delete/warm-up | proxy, admin, health |
| `SlidingWindow`, `FailedAuthLimiter`, `ConcurrencyGate`, `RateResult` | `limits.py` | RPM per key, gagal-auth per IP, slot per key (fail fast) | public app, proxy |
| `GpuScheduler`, `Ticket`, `resolve_capacity` | `scheduler.py` | Antrean GPU sadar-model: jalur per model (urutan: prioritas → key dengan request berjalan paling sedikit → paling lama), kapasitas per model, giliran satu model, batas antrean & waktu tunggu | proxy, warm-up, `/admin/status` |
| `QuotaTracker`, `QuotaStatus`, `parse_expiry` | `quota.py` | Kuota token harian/bulanan per key (waktu lokal), header `x-quota-*`, 429 `insufficient_quota`; parsing masa berlaku | public app, proxy (`Finalizer`), admin, CLI |
| `capture_prompt`, `new_record`, `placeholder_record`, `estimate_prompt_tokens`, `ResponseCollector` | `promptlog.py` | Log prompt & jawaban: isi dipotong, gambar diganti ukuran, `sk-...` disamarkan; jawaban dikumpulkan dari JSON/chunk SSE (tool call dikelompokkan per id). Juga perkiraan token prompt untuk stream yang terputus | public app, proxy (`Finalizer`) |
| `KeyCache`, `require_api_key`, `require_admin` | `auth.py` | Lookup key (hash SHA-256), dependency FastAPI | public/admin app |
| `sanitize_*`, `read_json_body`, `_clean_message`, `_clean_tools` | `sanitize.py` | Allowlist field, validasi, cap `max_tokens`, batas body; pesan/bagian konten/tool call/tools dibangun ulang dari kunci yang persis | public app |
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
  -> require_api_key       (blokir IP dengan >= 20 gagal/menit -> 429; lookup hash via KeyCache/DB -> 401;
                            key kedaluwarsa -> 401 api_key_expired, tanpa strike gagal-auth)
  -> _generate()  [apps/public.py]
       1. gw.rpm.hit(key)                  -> 429 rate_limit_exceeded (+ Retry-After)
          quota.status(key) (bila key punya kuota) -> 429 insufficient_quota (+ Retry-After, x-should-retry: false);
          header x-quota-tokens-{day,month}-{limit,remaining} ikut ke semua response
       2. read_json_body (hitung byte)     -> 413 / 400
       3. require_model; bila PROMPT_LOG_ENABLED dan key.log_prompts: promptlog.new_record(body) -> prompt
          (body mentah dulu, supaya request yang ditolak di bawah tetap tercatat; bila penangkapan
           melempar exception -> placeholder_record "[prompt tidak dapat dibaca untuk log]")
          key.may_use + is_model_enabled -> 404 model_not_found
          (APIError setelah titik ini: PromptRecord ikut disubmit dengan status error)
       4. ollama.model_info(model)         -> /api/show (cache 10 menit): capabilities + arsitektur
       5. sanitize_chat/completion/embeddings -> 400 (payload hanya berisi field allowlist;
          pesan, bagian konten, tool call, dan tools dibangun ulang dari kunci yang persis;
          model `thinking` diberi reasoning_effort default, /v1/completions ditolak untuknya)
          prompt direkam ulang dari payload bersih (capture_prompt(payload)): log = yang diterima model
       6. ctx.gpu_capacity = resolve_capacity(model, arsitektur)  (MODEL_PARALLEL > qwen35 -> 1 > 4)
          gate.try_acquire_key             -> 429 concurrency_limit_exceeded (langkah terakhir yang boleh gagal)
          (setiap APIError di langkah 1-6 -> UsageRecord dengan status error, latency 0)
          RequestContext(..., prompt, prompt_tokens_est = estimate_prompt_tokens(payload))
       7. scheduler.enqueue(model, kapasitas, timeout, owner=key.id, priority=key.priority) -> Ticket (dipegang Finalizer)
          timeout = PRIORITY_QUEUE_TIMEOUT_S bila key.priority > 0, selain itu QUEUE_TIMEOUT_S
          antrean penuh (MAX_QUEUE, hanya untuk key biasa) -> 503 server_busy langsung (juga stream: HTTP 503 asli)
          scheduler sudah ditutup (shutdown) -> 503 "the server is restarting", Retry-After: 15
       7a. stream=false -> forward_json():
             ticket.wait() -> False (habis waktu) -> 503 server_busy
             POST Ollama /v1/...  -> JSONResponse (status upstream diteruskan)
       7b. stream=true  -> stream_sse() -> ClosingStreamingResponse(_sse generator):
             yield ": connected"; tunggu ticket sambil yield ": ping queue=N" tiap 15 dtk
             habis waktu -> data: {"error": server_busy}
             upstream stream; baca per baris; ambil usage dari chunk terakhir (choices=[])
             hitung chunk yang berisi output (fin.chunks), umpankan chunk ke ResponseCollector (_collect)
             buang chunk usage jika client tidak minta include_usage
             error setelah header -> data: {"error": ...}
       Finalizer() (tepat sekali): ticket.close() (keluar antrean / lepas slot GPU) + queue_ms, lepas slot key,
                                    stream tanpa usage tapi ada output -> token diestimasi
                                      (prompt_tokens_est + jumlah chunk), submit UsageRecord, quota.add(token),
                                    lengkapi + submit PromptRecord (status, token, jawaban dari ResponseCollector,
                                      ts = waktu selesai), tulis status/stream/token ke state.access
  <- middleware menulis baris JSON ke access-YYYY-MM-DD.log setelah response selesai
```

Pemetaan error transport (`proxy.map_transport_error`): ConnectError/ConnectTimeout → 503 `upstream_unavailable`, timeout lain → 504 `upstream_timeout`, error transport lain → 502 `bad_gateway`. Client putus di tengah stream → status 499 (`client_disconnected`); generator di-`aclose()` sehingga stream upstream ikut tertutup dan Ollama berhenti generate.

## Alur data

| Data | Asal | Tujuan |
|---|---|---|
| Hash API key | admin API / CLI | tabel `api_keys` → `KeyCache` (TTL 30 dtk) |
| Allowlist model | `SEED_MODELS` (start), admin API, CLI | tabel `models` (dibaca setiap request) |
| Usage per request | `Finalizer` / jalur error `_generate` | `UsageWriter` (antrean, batch ≤200) → `usage_logs` |
| Total token per key per hari/bulan | `usage_logs` (diisi sekali per key per periode) + `Finalizer` (`quota.add`) | `QuotaTracker` (memori) → cek 429, header `x-quota-*`; dashboard memakai `token_usage_by_key` |
| Prompt & jawaban | `_generate` (`new_record`, lalu `capture_prompt(payload)`), `Finalizer` (`ResponseCollector`) | `UsageWriter` → `prompt_logs` → `/admin/prompts` (koneksi `_reader`) → menu *Prompt* |
| Kebijakan per key | admin API / CLI | kolom `api_keys` → `KeyCache` → `ApiKey` (`may_use`, `is_expired`, `priority`, `log_prompts`) |
| Access log | `RequestContextMiddleware` + `state.access` | `logs/access-YYYY-MM-DD.log` (JSON lines) |
| Audit | `gateway.admin`, `gateway.cli`, `gateway.auth` logger | `logs/gateway-YYYY-MM-DD.log` |
| Health | `HealthMonitor` | `logs/health-*.log` (+ transisi ke `gateway-*.log`), `/admin/status` |

## API structure

### Publik (port 8000)

| Method | Path | Auth | Handler |
|---|---|---|---|
| GET | `/health` | tidak | `health()` → `{"status":"ok"}` |
| GET | `/health/ready` | tidak | `ready()` → 200 `ready` / 503 `unavailable` (cek `/api/version`) |
| GET | `/v1/models` | Bearer API key | model `enabled=1` dari DB, difilter `allowed_models` key (tidak dihitung ke RPM) |
| POST | `/v1/chat/completions` | Bearer API key | `_generate(..., "chat")` |
| POST | `/v1/completions` | Bearer API key | `_generate(..., "completions")` |
| POST | `/v1/embeddings` | Bearer API key | `_generate(..., "embeddings")`, hanya untuk model dengan capability `embedding` |

`docs_url`, `redoc_url`, dan `openapi_url` di-nonaktifkan. Path lain → 404 format OpenAI. Header respons: `x-request-id`, dan untuk POST generasi juga `x-ratelimit-limit-requests`, `x-ratelimit-remaining-requests`, `x-ratelimit-reset-requests`.

### Admin (port 8001, prefix `/admin`, semua butuh `Authorization: Bearer <ADMIN_KEY>`)

| Method | Path | Fungsi |
|---|---|---|
| POST | `/admin/keys` | Buat key (`name`, `rpm_limit?`, `max_concurrent?`, `daily_token_limit?`, `monthly_token_limit?`, `expires_at?`, `allowed_models?` (≤ 50), `priority?` (0–9), `log_prompts?` (default `true`)). Plaintext dikembalikan sekali. `allowed_models` divalidasi terhadap tabel `models`. `expires_at`: tanggal = hari terakhir berlaku (lokal); datetime ISO juga diterima; tahun di luar 2000–2100 → 400 |
| GET | `/admin/keys?include_revoked=` | Daftar key (tanpa hash) + `*_effective`, `expired`, `usage` (token hari/bulan ini) + `quota_resets` |
| PATCH | `/admin/keys/{id}` | Ubah field mana pun di atas; hanya field yang dikirim yang berubah (`null` = kembali ke default / tanpa batas / semua model / prioritas 0 / log prompt aktif). Mengosongkan `KeyCache`, jadi langsung berlaku |
| DELETE | `/admin/keys/{id}` | Revoke (soft delete) dan kosongkan `KeyCache` |
| GET | `/admin/usage?since&until&key_id&group_by=hour\|day\|key\|model` | Ringkasan (default 7 hari) |
| GET | `/admin/usage/recent?limit&key_id` | Baris `usage_logs` terbaru |
| GET | `/admin/prompts?since&until&key_id&model&status&q&before&limit` | Log prompt, terbaru dulu, **cuplikan saja** (`prompt_preview`, `response_preview` 200 karakter, `prompt_chars`, `response_chars`). `since` default 24 jam lalu; `status` = `all`/`ok`/`error`; `q` 3–200 karakter (LIKE dengan escape, dicari di `prompt`, `response`, `end_user`); `limit` 1–100 (default 50); `before` = kursor `ts\|id` dari field `next`. Respons: `{data, next, enabled, retention_days}` |
| GET | `/admin/prompts/{id}` | Isi lengkap satu baris log prompt (`prompt` sebagai list `{role, content}` + `response`), 404 bila tidak ada |
| GET | `/admin/models` | Gabungan `/api/tags` dan allowlist DB |
| PATCH | `/admin/models/{name:path}` | `{"enabled": bool}`. Enable hanya untuk model yang terinstal |
| POST | `/admin/models/pull` | `{"model", "enable"}` → job 202 (background) |
| GET | `/admin/models/pull`, `/admin/models/pull/{job}` | Status job pull (disimpan di memori, maks 50) |
| DELETE | `/admin/models/{name:path}` | Hapus di Ollama + hapus dari allowlist |
| GET | `/admin/status` | `HealthMonitor.check()` live: komponen dependency + `gateway.concurrency` (`global_limit`, `active`, `waiting`, `closed`, `reserved_slots`, `max_queue`, `switch_after_s`, `turn{model, age_s, yielding}`, `lanes[{model, capacity, reserved, running, waiting, oldest_wait_s, priority_waiting}]`, `stats{granted, expired, rejected, switches}`, `per_key_in_flight`) |
| POST | `/admin/backup` | `VACUUM INTO data/backups/gateway-<ts>.db` (termasuk `prompt_logs`) |
| GET | `/admin/info` | `public_base_url`, `client_guide_url`, `defaults{rpm, max_concurrent, global_concurrency, max_tokens_cap}`, `models` (aktif) |
| GET | `/admin/ui`, `/admin/ui/app.js`, `/admin/ui/app.css` | Dashboard statis (tanpa auth; halaman meminta `ADMIN_KEY`), header CSP ketat. `GET /` → redirect ke sini |

Swagger admin tersedia di `/admin/docs` (tanpa auth, tapi hanya bisa diakses dari loopback).

## Middleware & error handling

Urutan stack Starlette: `ServerErrorMiddleware` → `RequestContextMiddleware` → `ExceptionMiddleware` → router.

- Handler untuk `APIError`, `StarletteHTTPException`, dan `RequestValidationError` (→ 400) berjalan di `ExceptionMiddleware`. Semuanya lewat `_respond()`, yang menulis `access["error"]`.
- Handler `Exception` (500) berjalan di `ServerErrorMiddleware`, di **luar** middleware access log. Karena itu `RequestContextMiddleware` menangkap exception yang lewat, menulis `access["error"] = "internal_error"`, lalu me-raise ulang. Baris access log-nya menjadi status 500 + `error: internal_error`.
- Format error selalu `{"error": {"message", "type", "param", "code"}}`. `mask_secrets()` mengganti pola `sk-...` dengan `sk-***`.

## Access log (`logs/access-YYYY-MM-DD.log`)

Satu baris JSON per request, ditulis `RequestContextMiddleware` setelah response selesai. Nama field termasuk kontrak backward compatibility (dipakai `status.ps1`, perintah debugging, dan kebiasaan operator), jadi jangan diganti nama.

| Field | Diisi oleh | Isi |
|---|---|---|
| `ts` | middleware | Waktu lokal (WIB, `+07:00`), presisi milidetik |
| `app` | middleware | `public` / `admin` |
| `request_id` | middleware | Sama dengan header `x-request-id`, `usage_logs.request_id`, dan `prompt_logs.request_id` |
| `ip` | middleware | IP client (lihat [Resolusi IP client](#resolusi-ip-client)) |
| `method`, `path` | middleware | Tanpa query string |
| `status` | middleware / `Finalizer` | Status **akhir**: stream bisa berakhir 499/503 walau header 200 sudah terkirim (`access["status"]`) |
| `duration_ms` | middleware | Sampai byte terakhir terkirim |
| `key_id`, `key`, `key_name` | `require_api_key` | `key` = prefix 10 karakter, tidak pernah key utuh |
| `endpoint`, `model` | `_generate` | `chat` / `completions` / `embeddings`; model bila sudah terbaca |
| `queue_ms` | `Finalizer` | Lama menunggu di scheduler (0 bila langsung jalan) |
| `stream`, `prompt_tokens`, `completion_tokens`, `total_tokens` | `Finalizer` | Token dari Ollama, atau estimasi untuk stream yang terputus |
| `error` | handler error / proxy | Kode error (`rate_limit_exceeded`, `server_busy`, `upstream_error`, `client_disconnected`, `internal_error`, ...) |

Tidak dicatat: `/health`, `/health/ready`, dan admin GET yang sukses (mengurangi noise polling). Header, API key, dan body tidak pernah ditulis.

## Health monitor (`gateway/health.py`)

Setiap `HEALTH_INTERVAL_S` (30 dtk), monitor mengumpulkan data secara paralel (`asyncio.gather` + `to_thread`):
- Ollama: `/api/version` dan `/api/ps` → komponen `ollama` dan `model_gpu` (warn jika `size_vram < size`)
- Service Windows (`WATCH_SERVICES`) lewat **ctypes advapi32** (`OpenSCManagerW`/`QueryServiceStatus`), tanpa subprocess → `service:<nama>` (`ok`/`down`/`absent`)
- GPU lewat `nvidia-smi --query-gpu` → `gpu` (warn jika suhu ≥ `GPU_TEMP_WARN_C`)
- Disk (`MONITOR_PATHS`, default drive DB + system drive) → `disk:<drive>` (warn jika sisa < `DISK_WARN_GB`)

Yang dicatat hanya **perubahan** status (`is down` / `recovered`). Snapshot JSON lengkap ditulis tiap `HEALTH_SNAPSHOT_INTERVAL_S`, termasuk `concurrency` (`scheduler.snapshot()` + `gate.snapshot()`). Jika Ollama hidup tetapi tidak ada model yang dimuat, warm-up dijalankan ulang (paling sering sekali per 600 dtk, dengan masa tenggang 60 dtk setelah start). Re-warm **dilewati** selama scheduler punya request berjalan atau antre, karena saat model berganti Ollama sesaat melaporkan "tidak ada model dimuat".

## External services / integrasi

| Integrasi | Cara | Lokasi kode |
|---|---|---|
| Ollama OpenAI-compat | `POST /v1/chat/completions`, `/v1/completions`, `/v1/embeddings` | `proxy.py` |
| Ollama native | `/api/version`, `/api/tags`, `/api/ps`, `/api/show`, `/api/pull`, `/api/delete`, `/api/generate` (warm-up) | `ollama.py` |
| NVIDIA driver | subprocess `nvidia-smi` | `health.gpu_stats` |
| Windows SCM | ctypes `advapi32` | `health.service_states` |
| **ngrok (aktif)** | Service `NgrokTunnel`, domain statis gratis `*.ngrok-free.dev` (config `D:\ollama\ngrok\ngrok.yml` berisi authtoken, ber-ACL, `web_addr: false`). IP client dari `X-Forwarded-For` (entri terakhir) | `scripts/install-ngrok.ps1`, `middleware.scope_client_ip` |
| Cloudflare Tunnel (alternatif, belum terpasang) | Service `cloudflared` (token, remotely-managed). Header `CF-Connecting-IP` dipercaya untuk IP client | `scripts/install-cloudflared.ps1` |
| Tailscale (di luar project) | Dipakai pemilik untuk SSH ke PC ini. Profil terikat akun Windows "User" tanpa *Run unattended* | - |
| WinSW v2.12 | Wrapper Windows Service (XML di `D:\ollama\services`) | `scripts/common.ps1` |

Timeout `httpx`: connect 5 dtk, read 300 dtk, write 30 dtk, pool 30 dtk. Limit koneksi 100 (keep-alive 20).

## Design patterns

- **Shared state container**: satu `Gateway` disimpan di `app.state.gw` untuk kedua app.
- **Dependency injection FastAPI** untuk auth (`Depends(require_api_key)`, dependency router admin).
- **Idempotent finalizer**: `Finalizer.__call__` dengan flag `_done`, dipanggil dari generator `finally` **dan** dari `ClosingStreamingResponse.__call__` (untuk kasus generator yang tidak pernah dimulai).
- **Ticket antrean tanpa race**: `GpuScheduler` hanya mengubah state di method sinkron (enqueue, `Ticket.close`, callback expire), dan setiap perubahan memanggil `_dispatch()`. Future ticket hanya diselesaikan scheduler; pemanggil menunggu dengan `asyncio.wait` tanpa membatalkannya, lalu `Finalizer` menutup ticket tepat sekali.
- **Shutdown**: handler sinyal menjadwalkan `scheduler.close()` lewat `loop.call_soon_threadsafe` (bukan langsung, karena sinyal bisa memotong perubahan state). Semua waiter langsung dilepas dengan 503 `server_busy` "the server is restarting" (`Retry-After: 15`), dan request baru ditolak. Request yang berjalan diberi `timeout_graceful_shutdown` 10 dtk.
- **Warm-up oportunistik**: warm-up hanya berjalan bila slot langsung tersedia (`enqueue(..., timeout=0)` lalu cek `ticket.granted`). Warm-up tidak pernah antre, sehingga tidak memaksa model yang sedang berjalan untuk gantian.
- **Giliran model**: satu model memegang GPU; giliran pindah bila model lain menunggu dan giliran sudah ≥ `MODEL_SWITCH_AFTER_S` (umur giliran dihitung ulang saat model itu mulai lagi dari GPU senggang). Request yang berjalan diselesaikan dulu, sehingga request dikelompokkan per model (detail: [decisions.md #26](decisions.md#26-scheduler-gpu-sadar-model-in-process)). Serah terima giliran mendahulukan jalur yang punya waiter prioritas.
- **Adil per key + prioritas** ([decisions.md #27](decisions.md#27-kebijakan-per-key-kuota-token-masa-berlaku-model-prioritas)): `_Lane.next_waiter()` memilih minimum `(-priority, running_by_owner[owner], enqueued_at)`. `waiters` tetap berurutan sesuai kedatangan (untuk `oldest_wait_s`); `position()` mengurutkan dengan kunci yang sama.
- **Jalur cepat prioritas** ([decisions.md #28](decisions.md#28-jalur-cepat-key-prioritas-slot-cadangan-dan-gantian-model-seketika)):
  - `_grantable()`: request biasa hanya boleh dijalankan bila `running < capacity - _reserved(lane)`, sedangkan `_reserved` = `reserved_slots` untuk jalur dengan kapasitas lebih besar;
  - `enqueue()` hanya menolak request biasa saat antrean penuh (`_normal_waiting()`);
  - `_yielding()` langsung `True` bila jalur lain punya waiter dengan prioritas lebih tinggi daripada waiter jalur aktif;
  - batas tunggu prioritas diberikan `proxy._enqueue` (`PRIORITY_QUEUE_TIMEOUT_S`).
- **Kuota lunak di memori**: `QuotaTracker` diisi dari DB per key per periode, lalu ditambah `Finalizer` tepat saat request selesai, sehingga tidak bergantung pada `UsageWriter` yang asinkron.
- **Allowlist, bukan denylist**, untuk field request.
- **Background tasks** lewat `Gateway.spawn()`, yang menyimpan referensi task dan membatalkannya saat stop.
- **Write-behind queue** untuk usage dan log prompt (`UsageWriter`, satu antrean, dipilah per tipe saat ditulis).
- **Pembaca terpisah**: bacaan admin yang berat (log prompt) memakai koneksi SQLite read-only kedua, sehingga tidak antre di belakang/di depan query request API ([decisions.md #30](decisions.md#30-koneksi-sqlite-read-only-kedua-dan-kursor-index-friendly-untuk-log-prompt)).
- **Audit tidak boleh mematahkan request**: setiap langkah log prompt (penangkapan, pengumpulan jawaban, `text()`) dibungkus `try/except`; kegagalan menghasilkan baris pengganti atau `response = NULL`, bukan error ke client.
- **Transition logging** untuk health: log hanya saat status berubah.

## Konfigurasi (`gateway/config.py`)

Semua setting adalah field `Settings` (pydantic-settings) yang dibaca dari env var lalu `.env`. **Env var menang atas `.env`.** Nama env = nama field dalam huruf besar. Setting baru wajib ditambahkan ke `Settings` **dan** `.env.example`. Kolom *Produksi* hanya diisi bila berbeda dari default; nilai rahasia tidak ditulis di sini.

| Setting | Default | Produksi | Fungsi / catatan |
|---|---|---|---|
| **Upstream** | | | |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | | Alamat Ollama. Pakai `127.0.0.1`, jangan `localhost` |
| `UPSTREAM_CONNECT_TIMEOUT` | `5` | | Detik. Connect gagal → 503 `upstream_unavailable` |
| `UPSTREAM_READ_TIMEOUT` | `300` | | Detik tanpa data dari Ollama → 504 `upstream_timeout` |
| **Listener** | | | |
| `BIND_HOST` | `127.0.0.1` | | Jangan diganti `0.0.0.0` |
| `PUBLIC_PORT` / `ADMIN_PORT` | `8000` / `8001` | | Instance dev wajib memakai port lain |
| `ADMIN_KEY` | kosong | rahasia | Kosong → admin API 503 `admin_disabled`. Dibandingkan dengan `hmac.compare_digest` |
| `LIMIT_CONCURRENCY` | `64` | | `limit_concurrency` uvicorn untuk server publik (koneksi bersamaan) |
| `CLIENT_IP_HEADERS` | `cf-connecting-ip,x-forwarded-for` | | Header IP client; untuk XFF dipakai entri terakhir |
| `PUBLIC_BASE_URL` | kosong | domain ngrok (rahasia repo) | Ditampilkan di dashboard dan pesan untuk client |
| `CLIENT_GUIDE_URL` | kosong | tautan `CLIENT.md` di GitHub | Tautan panduan di pesan untuk client |
| `ADMIN_ALLOWED_HOSTS` | `127.0.0.1,localhost` | | `TrustedHostMiddleware` app admin (anti DNS rebinding) |
| **Penyimpanan** | | | |
| `DB_PATH` | `data/gateway.db` | | Path relatif di-resolve terhadap root project |
| `USAGE_RETENTION_DAYS` | `90` | | Harus ≥ 31, karena kuota bulanan dihitung dari `usage_logs` |
| `PROMPT_LOG_ENABLED` | `true` | | Kill switch global log prompt (per key: `api_keys.log_prompts`) |
| `PROMPT_LOG_RETENTION_DAYS` | `30` | | ≥ 1. Dijanjikan ke client di `CLIENT.md#privasi`; ubah keduanya bersamaan |
| `PROMPT_LOG_MAX_CHARS` | `20000` | | ≥ 1. Batas karakter per prompt dan per jawaban (sisanya `… [dipotong N karakter]`) |
| `SEED_MODELS` | `gemma3:4b` | | Di-insert ke allowlist saat start (`INSERT OR IGNORE`) |
| **Batas default per key** | | | |
| `DEFAULT_RPM` | `60` | | Dipakai bila `api_keys.rpm_limit` NULL |
| `DEFAULT_MAX_CONCURRENT` | `2` | | Dipakai bila `api_keys.max_concurrent` NULL |
| **Antrean GPU** ([decisions.md #26](decisions.md#26-scheduler-gpu-sadar-model-in-process), [#28](decisions.md#28-jalur-cepat-key-prioritas-slot-cadangan-dan-gantian-model-seketika)) | | | |
| `GLOBAL_CONCURRENCY` | `4` | | Wajib = `OLLAMA_NUM_PARALLEL` di XML `OllamaServe` |
| `QUEUE_TIMEOUT_S` | `60` | `60` (ditulis eksplisit) | Batas tunggu key biasa, lalu 503 `server_busy`. Angka terhingga ≥ 0 |
| `MAX_QUEUE` | `32` | | Jumlah request biasa yang boleh antre (semua model). Lebih → 503 langsung |
| `MODEL_SWITCH_AFTER_S` | `10` | | Umur giliran minimum sebelum model lain boleh mengambil GPU |
| `MODEL_PARALLEL` | kosong | | Override kapasitas per model, format `nama:tag=N,...` (N ≥ 1, divalidasi saat start) |
| `SERIAL_ARCHITECTURES` | `qwen35` | | Arsitektur (`general.architecture`) yang dipaksa 1 slot |
| `PRIORITY_RESERVED_SLOTS` | `1` | | Slot per jalur yang disisihkan untuk key prioritas (hanya jalur dengan kapasitas > nilai ini) |
| `PRIORITY_QUEUE_TIMEOUT_S` | `300` | | Batas tunggu key prioritas |
| `STREAM_PING_INTERVAL_S` | `15` | | Jarak `: ping queue=N` selama stream antre |
| **Model** | | | |
| `DEFAULT_REASONING_EFFORT` | `none` | | Untuk model `thinking` bila client tidak mengirim; nilai `""`/`none`/`medium`/`high` |
| `WARMUP` | `true` | | Muat model aktif pertama (alfabet) saat start |
| **Batas request** | | | |
| `MAX_TOKENS_CAP` | `2048` | | Cap `max_tokens` / `max_completion_tokens` |
| `MAX_BODY_BYTES` | `4000000` | | Dihitung saat body mengalir → 413 |
| `MAX_MESSAGES` / `MAX_IMAGES` / `MAX_EMBEDDING_INPUTS` | `100` / `2` / `256` | | |
| `FAILED_AUTH_PER_IP_PER_MIN` | `20` | | Lebih → 429 `auth_rate_limited` 60 dtk |
| **Logging** | | | |
| `LOG_LEVEL` | `INFO` | | |
| `LOG_DIR` | `logs` | | Relatif terhadap root project |
| `LOG_RETENTION_DAYS` | `30` | | File log harian lebih tua dihapus |
| `LOG_CONSOLE` | `true` | `false` (env di XML service) | Log ke stdout (di service ditangkap WinSW) |
| **Health monitor** | | | |
| `HEALTH_INTERVAL_S` | `30` | | `0` = monitor mati (dipakai test) |
| `HEALTH_SNAPSHOT_INTERVAL_S` | `300` | | Snapshot JSON lengkap ke `health-*.log` |
| `WATCH_SERVICES` | `OllamaServe,Cloudflared` | `OllamaServe,NgrokTunnel` (diisi `install-ngrok.ps1`) | Service Windows yang dipantau |
| `MONITOR_PATHS` | kosong | | Kosong = drive DB + drive sistem |
| `DISK_WARN_GB` / `GPU_TEMP_WARN_C` | `10` / `85` | | Ambang `warn` |

Env service di luar `Settings` (dibuat script install, di XML WinSW `D:\ollama\services\`):
- **`OllamaServe`:** `OLLAMA_HOST=127.0.0.1:11434`, `OLLAMA_MODELS=D:\ollama\models`, `OLLAMA_KEEP_ALIVE=-1`, `OLLAMA_NUM_PARALLEL=4`, `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_MAX_QUEUE=16`, `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`, `OLLAMA_CONTEXT_LENGTH=8192`.
- **`OllamaGateway`:** `PYTHONUNBUFFERED=1`, `PYTHONPATH=<project>;<project>\.venv\Lib\site-packages`, `LOG_CONSOLE=false`.

## Aturan arsitektur

1. Semua endpoint yang meneruskan ke Ollama wajib: auth → RPM → validasi/sanitasi → slot key → `GpuScheduler.enqueue` (antrean per model dengan batas tunggu) → `Finalizer` yang memegang ticket-nya. Pemanggilan Ollama yang memakai GPU di luar request client (warm-up) juga lewat scheduler.
2. Jangan menambah listener yang bind ke `0.0.0.0`. Semua bind ke `127.0.0.1`; akses luar hanya lewat agen tunnel (ngrok, atau cloudflared bila dipasang).
3. Route admin tidak boleh dipasang di app publik, begitu pula sebaliknya.
4. `GLOBAL_CONCURRENCY` harus sama dengan `OLLAMA_NUM_PARALLEL` (di XML service `OllamaServe`). Scheduler mengasumsikan `OLLAMA_MAX_LOADED_MODELS=1` (satu model berjalan sekaligus). Model yang dipaksa 1 slot oleh Ollama harus tercakup `SERIAL_ARCHITECTURES` atau `MODEL_PARALLEL`.
5. Request non-stream sebaiknya selesai < 100 dtk (batas first-byte Cloudflare; dijaga lewat `MAX_TOKENS_CAP` dan `QUEUE_TIMEOUT_S` 60 dtk + generasi ≤ ~30 dtk), supaya tetap aman bila tunnel dipindah ke Cloudflare.
6. Kode baru yang blocking harus lewat `asyncio.to_thread`. Event loop dipakai bersama oleh kedua server.
7. Dashboard tetap statis dan tanpa dependency pihak ketiga. Fitur baru memakai admin API yang ada, bukan endpoint publik.
8. Endpoint generasi baru juga wajib: cek kuota setelah RPM, cek `key.may_use(model)`, dan menangkap prompt (`new_record` setelah body terbaca, lalu `capture_prompt(payload)` setelah sanitasi). Usage, kuota (`quota.add`), dan log prompt hanya dicatat lewat `Finalizer` atau jalur `except APIError` di `_generate`, supaya tidak ada request yang lolos dari kuota maupun audit.
9. Bacaan admin yang bisa berat (rentang waktu panjang, pencarian teks) memakai `Database._read_all` (koneksi read-only) dan selalu dibatasi rentang waktu + `LIMIT`. Semua penulisan tetap di koneksi utama. Daftar hanya mengembalikan cuplikan; isi lengkap diambil per baris.
