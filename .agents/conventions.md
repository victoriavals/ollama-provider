# Conventions

Konvensi di bawah diambil dari kode yang ada. Ikuti pola ini saat menambah kode.

## Naming rules

| Hal | Konvensi | Contoh di repo |
|---|---|---|
| Modul Python | lowercase satu kata | `proxy.py`, `limits.py`, `health.py` |
| Class | PascalCase, kata benda | `Gateway`, `UsageWriter`, `ConcurrencyGate`, `HealthMonitor` |
| Fungsi/method | snake_case, kata kerja | `lookup_key`, `try_acquire_key`, `sanitize_chat` |
| Helper privat | awalan `_` | `_generate`, `_bearer`, `_purge`, `_log_transitions` |
| Konstanta modul | UPPER_SNAKE | `CACHE_TTL_S`, `QUIET_PATHS`, `REWARM_MIN_INTERVAL_S` |
| Satuan di nama | sufiks `_s`, `_ms`, `_gb`, `_c`, `_bytes` | `queue_timeout_s`, `latency_ms`, `disk_warn_gb`, `max_body_bytes` |
| Setting | field snake_case di `Settings` = env UPPER_SNAKE | `global_concurrency` ↔ `GLOBAL_CONCURRENCY` |
| Logger | `logging.getLogger("gateway.<area>")` | `gateway.proxy`, `gateway.auth`, `gateway.admin`, `gateway.health`, `gateway.access` |
| Kode error OpenAI | snake_case | `rate_limit_exceeded`, `server_busy`, `model_not_found`, `upstream_unavailable` |
| API key | `sk-` + `token_urlsafe(32)` | `auth.generate_key()` |
| Admin key | `adm-` + `token_urlsafe(32)` | `cli gen-admin-key` |
| Service Windows | PascalCase | `OllamaServe`, `OllamaGateway`, `Cloudflared` |
| Script PowerShell | kebab-case, kata kerja di depan | `install-gateway.ps1`, `uninstall-services.ps1` |
| Fungsi PowerShell | Verb-Noun | `Stop-ServiceSafe`, `Install-WinSWService`, `Get-WinSW` |
| Log file | `<prefix>-YYYY-MM-DD.log` | `access-2026-09-27.log` |

## Folder rules & file organization

```
gateway/            paket aplikasi (satu modul = satu tanggung jawab)
gateway/apps/       FastAPI app factory + route (tipis; panggil modul di gateway/)
scripts/            PowerShell deploy/ops; semua dot-source common.ps1
tests/              pytest; fixtures/ berisi respons asli Ollama 0.34 dan chat_stream_ollama040.txt (0.40, ada field `timings`)
gateway/static/     dashboard admin statis (lihat Dashboard rules)
data/  logs/        runtime, gitignored, jangan di-commit
.agents/            memory untuk AI agent (dokumen ini)
```

- Kode baru yang dipakai kedua app → modul di `gateway/`, lalu diakses lewat `Gateway` bila perlu state.
- Route baru → `gateway/apps/public.py` (publik) atau `gateway/apps/admin.py` (admin, otomatis di bawah `require_admin`).
- Helper PowerShell yang dipakai lebih dari satu script → `scripts/common.ps1`.
- Jangan membuat file konfigurasi baru. Tambahkan field ke `Settings` dan `.env.example`.

## Function/class rules (Python)

- Python 3.11: pakai `X | None`, `list[str]`, `dict[str, Any]`, dan type hint di semua signature publik.
- `@dataclass` untuk struktur data sederhana (`ApiKey`, `RateResult`, `ModelInfo`, `QuotaStatus` frozen; `UsageRecord`, `PromptRecord`, `RequestContext` mutable karena dilengkapi `Finalizer`).
- Seluruh I/O async. Blocking (subprocess, ctypes, `shutil.disk_usage`) → `asyncio.to_thread`.
- Background task → `gw.spawn(coro, name)` (bukan `asyncio.create_task` langsung), supaya dibatalkan saat stop.
- Akses state di route: `request.app.state.gw` (lihat `_gw()` di admin). Akses setting: `gw.settings`, bukan `get_settings()` di dalam route.
- Hindari import sirkular: gunakan `TYPE_CHECKING` untuk type `Gateway` di modul level bawah (`proxy.py`, `health.py`).
- Docstring modul singkat menjelaskan tanggung jawab dan *mengapa*. Komentar hanya untuk hal yang tidak jelas dari kode. Semua komentar dalam Bahasa Inggris.
- Validasi body admin memakai model Pydantic (`Field(min_length=..., ge=...)`). Validasi body publik manual di `sanitize.py`, karena harus menerima struktur OpenAI yang longgar dan membuang field tak dikenal.
- State in-memory yang dibagi antar request (`GpuScheduler`, `QuotaTracker`, `ConcurrencyGate`, `SlidingWindow`) hanya diubah di method sinkron, tanpa `await` di tengah perubahan. Dari thread lain (handler sinyal) pakai `loop.call_soon_threadsafe`.
- Kode audit/observasi (log prompt, pengumpul jawaban) tidak boleh mematahkan request: bungkus dengan `try/except Exception`, catat lewat `log.exception`/`log.debug`, lalu lanjutkan.

## Error handling

- Error ke client: `raise APIError(status, message, code=..., param=..., headers=...)`. Jangan `HTTPException` dan jangan `JSONResponse` error buatan sendiri.
- `type` diisi otomatis dari status (`_STATUS_TYPES` di `errors.py`); `code` wajib diisi untuk error yang bisa ditangani client.
- 429 dan 503 selalu menyertakan `Retry-After`.
- Pada streaming, setelah header terkirim: tangkap error, set `fin.record.status_code`, set `access["error"]`, lalu `yield sse_event(err.body())`. Jangan raise.
- Error upstream Ollama 4xx/5xx diteruskan apa adanya (status + body).
- Error transport httpx dipetakan lewat `map_transport_error`.
- Exception tak terduga ditangani oleh handler global (500 `Internal server error`) dengan `log.exception` yang sudah di-mask.
- Background loop menangkap `Exception` dan melakukan `log.exception`, tetapi selalu me-raise ulang `asyncio.CancelledError`.

## Aturan keamanan

1. **Jangan pernah mencatat** header `Authorization` atau API key di mana pun. Body request/respons **tidak boleh** masuk file log teks (`logs/`). Satu-satunya tempat isi prompt/jawaban adalah tabel `prompt_logs` (log prompt audit, [decisions.md #29](decisions.md#29-log-prompt--jawaban-untuk-audit)), yang dibangun lewat `gateway/promptlog.py`: dipotong, gambar diganti ukuran, `sk-...` disamarkan, dan bisa dimatikan per key. Logger `httpx`, `httpcore`, dan `uvicorn.access` dipaksa ke WARNING (`logs.setup_logging`).
2. Pesan error melewati `mask_secrets()`. API key tidak pernah di-echo balik (lihat `test_invalid_key_is_401`).
3. Simpan hash SHA-256 saja. Tampilkan hanya `prefix`.
4. Bandingkan admin key dengan `hmac.compare_digest`.
5. Semua server bind `127.0.0.1`. Port 8001 (admin) dan 11434 (Ollama) tidak boleh dirouting tunnel. Jalur publik dibatasi oleh path regex Cloudflare `^/(v1|health)(/.*)?$`.
6. Header IP client (`CLIENT_IP_HEADERS`, default `cf-connecting-ip,x-forwarded-for`) hanya dipercaya karena port 8000 cuma bisa dicapai proses lokal (agen tunnel). Untuk `X-Forwarded-For` dipakai entri **terakhir** (entri awal bisa dipalsukan client). IP di-resolve sekali di `RequestContextMiddleware` (`request.state.client_ip`). Jangan ubah bind ke `0.0.0.0`.
7. Pertahankan **allowlist** di `sanitize.py`. Field Ollama (`options`, `keep_alive`, `num_ctx`, `think`) harus tetap dibuang. `max_tokens` di-cap. `n>1` ditolak. `tools` hanya lolos kalau model punya capability `tools`. `image_url` hanya boleh `data:image/...;base64,...`; URL `http(s)`/`file` ditolak (cegah SSRF). `reasoning_effort` hanya untuk model ber-capability `thinking` (nilai `none`/`medium`/`high`), default dari `DEFAULT_REASONING_EFFORT`.
8. Batas: body ≤ `MAX_BODY_BYTES` (dihitung saat stream masuk), gambar ≤ `MAX_IMAGES`, pesan ≤ `MAX_MESSAGES`, input embedding ≤ `MAX_EMBEDDING_INPUTS`, stop ≤ 4 × 100 karakter.
9. Struktur bersarang yang diteruskan ke Ollama (pesan, bagian konten, tool call, tools) dibangun ulang dari kunci yang persis diizinkan (`sanitize._clean_message`, `_clean_tools`). Ollama (Go) mencocokkan kunci tanpa peduli huruf besar-kecil, jadi kunci varian seperti `Content` tidak boleh ikut diteruskan.
10. Brute force: `FailedAuthLimiter` (20 gagal/menit per IP → 429). Docs OpenAPI publik dimatikan.
11. File sensitif `.env` dan `data/` di-ACL (SID SYSTEM `S-1-5-18`, Administrators `S-1-5-32-544`, user installer). Di `icacls`, pakai SID, bukan nama (`WORKGROUP\user` gagal untuk akun lokal).
12. Kode yang dijalankan SYSTEM (`D:\ollama`: binary Ollama/ngrok, XML & exe WinSW) tidak boleh writable oleh non-admin; jalankan `Protect-AdminOnlyTree` setelah menambah file di sana. Service yang menjalankan kode dari folder yang bisa diedit (gateway) memakai akun hak rendah (`-ServiceAccount 'NT AUTHORITY\LocalService'`).
13. ngrok: `agent.web_addr: false` (inspector lokal 4040 merekam header Authorization & prompt). `ngrok.yml` berisi authtoken, ber-ACL protected.
14. Isi `prompt_logs` adalah data pribadi client. Agent tidak menyalinnya ke chat, commit, file di luar `data/`, atau script uji; untuk statistik cukup hitung baris. Fitur yang menyimpan isi request baru harus memakai aturan privasi `promptlog.py` (potong, gambar → ukuran, `mask_secrets`, opt-out per key, retensi) dan diumumkan di `CLIENT.md#privasi`.
15. Key kedaluwarsa, model terlarang, dan model tak dikenal dijawab persis seperti OpenAI (401 `api_key_expired`, 404 `model_not_found` dengan pesan yang sama), supaya keberadaan model lain tidak bocor.

## Performance rules

1. Satu worker. Slot GPU dibagikan oleh `GpuScheduler` (`scheduler.py`): jalur per model, `GLOBAL_CONCURRENCY` = `OLLAMA_NUM_PARALLEL`, batas tunggu `QUEUE_TIMEOUT_S`, batas antrean `MAX_QUEUE`. Jangan memanggil endpoint generasi Ollama tanpa ticket scheduler. State scheduler hanya diubah lewat method sinkron (tanpa `await` di tengah perubahan state).
2. Middleware harus pure ASGI. Jangan `BaseHTTPMiddleware` atau `GZipMiddleware`, dan jangan set `Content-Length` pada SSE.
3. SSE: teruskan setiap baris segera (`aiter_lines`) dengan header `Cache-Control: no-cache` dan `X-Accel-Buffering: no`.
4. Pakai satu `httpx.AsyncClient` bersama (`gw.ollama.http`) dengan timeout eksplisit. Jangan membuat client per request.
5. Cache: key 30 dtk, capability model 10 menit. Invalidasi saat revoke/pull/delete.
6. Usage ditulis batch di background. Access log ditulis sekali per request, setelah response selesai.
7. Warm-up menjalankan inferensi 1 token (bukan hanya load), supaya request pertama tidak lambat.
8. Request non-stream harus selesai < 100 dtk (Cloudflare 524); jaga dengan `MAX_TOKENS_CAP`.
9. Query admin yang bisa berat (log prompt) memakai koneksi read-only (`Database._read_all`), selalu dengan rentang waktu + `LIMIT`, dan daftar hanya mengembalikan cuplikan. Kursor halaman memakai bentuk index-friendly `ts <= ? AND (ts < ? OR id < ?)`. Cek SQL baru dengan `EXPLAIN QUERY PLAN`.
10. Jangan menambah query DB per request di jalur generasi bila datanya bisa dihitung di memori (contoh: kuota diisi sekali per key per periode, lalu ditambah `Finalizer`).

## Logging rules

- Event aplikasi → logger `gateway.<area>` → `gateway-*.log`.
- Aksi admin/CLI yang mengubah state wajib dicatat di audit log (`audit.info(...)` di `admin.py` / `cli.py`).
- Data per request → tambahkan field ke `request.state.access` (dict), jangan menulis log per request sendiri. Nama field access log adalah kontrak ([architecture.md#access-log](architecture.md#access-log-logsaccess-yyyy-mm-ddlog)): tambah boleh, ganti nama jangan.
- Kesehatan dependency → komponen baru ditambahkan di `HealthMonitor._evaluate()` dengan status `ok`/`warn`/`down`/`absent`.
- File log tidak pernah di-rename (`DailyFileHandler`), karena rename gagal di Windows saat file sedang di-tail.

## Testing rules

- `uv run pytest` harus hijau (saat ini 155 test, ~6 dtk). `asyncio_mode = "auto"`, jadi test async cukup ditulis sebagai `async def`.
- Ollama selalu di-mock: fixture `ollama` (respx `MockRouter`) dipasang ke `Gateway(..., ollama_transport=httpx.MockTransport(router.async_handler))`.
- Gunakan fixture: `gw`, `public`, `admin`, `api_key`, `auth`, dan helper di `tests/conftest.py`:
  - `new_key(gw, name, **limits)`: key dengan kebijakan apa pun (`daily_token_limit`, `allowed_models`, `priority`, `log_prompts`, ...);
  - `hold_slots(gw, model, n)`: mengisi slot GPU sebuah model (ticket dilepas dengan `close()`);
  - `scheduler_idle(gw)`: cek tidak ada request berjalan/antre setelah test;
  - `flush_usage(gw)`: menunggu `UsageWriter` menulis lalu mengembalikan baris `usage_logs`.
- Mock Ollama `/api/show` memakai tabel `CAPS` (capability) dan `ARCH` (`general.architecture`, `qwen3.5:4b` = `qwen35`). Model baru di test harus ditambahkan ke keduanya.
- `make_settings()` memakai `priority_reserved_slots=0`; test yang menguji slot cadangan men-set nilainya sendiri. Tanpa itu, `hold_slots()` tidak bisa mengisi slot cadangan.
- Setting test via `make_settings()`: `health_interval_s=0`, `watch_services=""`, `log_dir=tmp_path/"logs"`, `warmup=False`. Test tidak boleh menyentuh service, GPU, atau jaringan sungguhan (kecuali `test_service_states_on_windows`, yang di-skip di non-Windows).
- Respons Ollama baru → simpan respons asli di `tests/fixtures/`.
- Waktu: `GpuScheduler(..., clock=)` dan `QuotaTracker(db, now=)` menerima jam palsu (contoh: `test_quota_periods_roll_over_in_local_time`); jangan `sleep` panjang untuk menguji giliran atau pergantian hari. Untuk nilai yang dibulatkan (mis. `oldest_wait_s`), beri jeda cukup (≥ 0,15 dtk) agar tidak flaky.
- Test yang membuat `Database` sendiri wajib `await db.close()` di `finally` (dua koneksi aiosqlite; tanpa itu proses test menggantung).
- Setiap perbaikan bug diberi test regresi, lalu diuji mutasi: kembalikan perbaikan sementara dan pastikan test itu gagal.
- Query daftar baru: tambahkan test `EXPLAIN QUERY PLAN` seperti `test_prompt_list_queries_stay_on_the_ts_index`.
- Periksa access log lewat `caplog` pada logger `gateway.access`.

## Dashboard rules (`gateway/static/dashboard/`)

- Vanilla HTML/CSS/JS tanpa framework, build step, CDN, atau font eksternal. CSP `script-src 'self'; style-src 'self'` melarang script/style inline, jadi semua JS ada di `app.js` dan CSS di `app.css`. Style dinamis hanya lewat CSSOM (`el.style.width = ...`), bukan atribut `style`.
- Bangun DOM dengan helper `h()` / `s()` dan `textContent`. **Dilarang** `innerHTML` / `insertAdjacentHTML` (dicek `test_dashboard_is_served_with_strict_headers`).
- Warna, ukuran, dan spasi hanya dari token CSS di `:root` (`--surface-*`, `--ink*`, `--accent*`, status `--good/--warning/--serious/--critical`), dengan varian `prefers-color-scheme: dark`. Status selalu ikon + teks, tidak pernah warna saja.
- Copy UI dalam Bahasa Indonesia, sentence case; tombol menyebut aksinya ("Buat key", "Cabut key").
- Semua panggilan lewat `api()` (Bearer admin key dari `sessionStorage`); 401 → kembali ke layar login. Data baru dari backend ditambahkan ke admin API, bukan diambil langsung dari Ollama.
- File baru harus didaftarkan di `UI_FILES` (`apps/admin.py`); route aset memakai whitelist.
- `app.js` adalah ES module: fungsi tidak tersedia di `window`. Uji UI lewat interaksi DOM (klik, isi form), bukan memanggil fungsi langsung.
- Menu yang datanya bisa besar (contoh: *Prompt*) tidak ikut auto-refresh maupun `visibilitychange`, wajib punya filter rentang waktu, memuat per halaman (`next`), membatasi jumlah baris di layar, dan mengambil detail hanya saat diminta.
- Setiap pemuatan async yang bisa tumpang tindih memakai penjaga urutan (`seq`): jawaban lama dibuang bila sudah ada permintaan baru. Tombol dinonaktifkan selama memuat.
- Form yang sedang diisi tidak boleh di-reset oleh polling atau `visibilitychange`. Dialog ubah hanya mengirim field yang berubah (PATCH parsial).
- Tabel lebar dibungkus `.table-wrap` (`overflow-x: auto; position: relative`); kolom dialog `minmax(0, 1fr)` dan teks panjang memakai `pre.code.wrap`, supaya halaman ponsel tidak bisa digeser ke samping.
- Setelah mengubah tampilan: `node --check gateway/static/dashboard/app.js`, lalu screenshot desktop 1366 px / ponsel 390 px / mode gelap (Edge headless via Playwright `channel="msedge"` atau CDP langsung), dan pastikan `scrollWidth` tidak melebihi lebar layar serta tidak ada error console.

## PowerShell script rules

- **ASCII-only** (cek: tidak ada byte > 127) dan lolos `[System.Management.Automation.Language.Parser]::ParseFile`.
- Diawali comment-based help (`.SYNOPSIS`/`.EXAMPLE`), `[CmdletBinding()]`, `param(...)`, lalu `. "$PSScriptRoot\common.ps1"` dan `Assert-Admin`. Pengecualian: `status.ps1` berdiri sendiri, read-only, dan tidak butuh admin.
- Kompatibel Windows PowerShell 5.1: tidak ada `&&`, `??`, `?.`, atau ternary.
- `$ErrorActionPreference = 'Stop'` (dari `common.ps1`). Cek `$LASTEXITCODE` setelah exe native.
- Hentikan service lewat `Stop-ServiceSafe`, buat service lewat `Install-WinSWService` (env di-pass sebagai `[ordered]@{}`).
- Unduh langsung (winget di mesin ini rusak). Jangan menambah ketergantungan ke winget.
- Script harus idempoten: aman dijalankan ulang (hapus lalu buat ulang service, robocopy inkremental).
