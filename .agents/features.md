# Feature Documentation

Status: **Completed** (ada di kode dan lulus test), **In Progress**, **Planned**, **Tidak dipakai** (kode ada, sengaja tidak di-deploy), atau **Unknown**. Keterangan "diuji live" berarti sudah dicoba ke Ollama/service sungguhan di mesin ini pada 2026-09-27.

## Ringkasan

| Fitur | Status |
|---|---|
| Chat completions (stream & non-stream) | Completed (diuji live) |
| Text completions | Completed (diuji live) |
| Embeddings | Completed (belum ada model embedding terpasang) |
| Model kedua `qwen3.5:4b` + kontrol mode berpikir | Completed (diuji live 2026-10-17) |
| Daftar model `/v1/models` | Completed (diuji live) |
| Input gambar (vision) | Completed (diuji live 2026-10-17: gemma3 & qwen3.5 lewat URL publik) |
| Tools / function calling passthrough | Completed (kondisional, belum bisa diuji: gemma3 tanpa `tools`) |
| API key auth | Completed (diuji live) |
| Rate limit per key (RPM) | Completed (diuji live) |
| Kontrol konkurensi (per key + antrean GPU) | Completed (diuji live) |
| Limit gagal-auth per IP | Completed |
| Sanitasi request | Completed (diuji live) |
| Usage logging ke DB | Completed (diuji live) |
| Log aktivitas, audit & health | Completed (diuji live) |
| Health monitor + auto re-warm | Completed (diuji live) |
| Warm-up model | Completed (diuji live) |
| Health endpoint publik | Completed |
| Admin: manajemen key | Completed (diuji live) |
| Admin: laporan usage | Completed (diuji live) |
| Admin: manajemen model (list/enable/pull/delete) | Completed (pull/delete hanya diuji dengan mock) |
| Admin: status | Completed (diuji live) |
| Admin: backup DB | Completed (manual, belum dijadwalkan) |
| CLI manajemen | Completed (diuji live) |
| Deploy Windows Service (Ollama + gateway) | Completed (terpasang & diuji) |
| Script status `status.ps1` | Completed (diuji live) |
| Dashboard admin web (lokal) | Completed (diuji live + screenshot desktop/ponsel/gelap) |
| Script upgrade | Completed (dipakai 2026-10-17: Ollama 0.34.4 → 0.40.5) |
| Script uninstall | Completed (belum pernah dijalankan) |
| Tunnel gratis ngrok | Completed (terpasang & diuji live 2026-10-16) |
| Cloudflare Tunnel | Tidak dipakai: script siap sebagai alternatif berbayar (pemilik memilih ngrok gratis) |
| Retensi usage (prune) | Completed |
| Lihat [Planned](#planned) | Planned |

---

## Chat Completions

Purpose: `POST /v1/chat/completions` format OpenAI, stream (SSE) maupun non-stream.

Status: Completed. Diuji live dengan SDK OpenAI Python: TTFB stream sekitar 0.05 dtk (setara Ollama langsung), 5 stream paralel dengan limit 2 → antre dengan benar, disconnect → 499 dan GPU langsung idle.

Location: `gateway/apps/public.py` (`chat_completions`, `_generate`), `gateway/proxy.py`

Important Files: `proxy.py` (`forward_json`, `stream_sse`, `_sse`, `Finalizer`, `ClosingStreamingResponse`), `sanitize.py` (`sanitize_chat`)

Dependencies: Ollama `/v1/chat/completions`, `/api/show`. Fitur auth, rate limit, dan konkurensi.

Notes: Chunk usage (`choices: []`) hanya diteruskan jika client mengirim `stream_options.include_usage=true`. Ping `: ping` tiap 15 dtk saat antre. Error setelah header dikirim sebagai `data: {"error":...}`.

## Text Completions

Purpose: `POST /v1/completions` dengan `prompt` string.

Status: Completed (diuji live).

Location: `gateway/apps/public.py` (`completions`)

Important Files: `sanitize.py` (`sanitize_completion`), `proxy.py`

Dependencies: Ollama `/v1/completions`

Notes: `prompt` berupa array ditolak (400). Fixture stream (`tests/fixtures/completions_stream.txt`) memperlihatkan keanehan Ollama: `usage` bernilai 0 di setiap chunk.

## Model Kedua qwen3.5:4b dan Kontrol Mode Berpikir

Purpose: Menyediakan model dengan tools/function calling dan output terstruktur di samping `gemma3:4b`, tanpa jawaban kosong akibat mode berpikir.

Status: Completed (2026-10-17). Diuji live lewat URL publik dengan SDK OpenAI: chat, stream (TTFT 0,13 dtk), gambar, tools (`cek_cuaca(kota="Bandung")`), 4 request bersamaan (sukses, tetapi uji kapasitas 2026-10-18 menunjukkan Ollama memprosesnya berurutan, lihat Notes), perpindahan model (~4 dtk), penolakan `/v1/completions` dan `reasoning_effort` tidak valid.

Location: `gateway/sanitize.py` (`REASONING_EFFORTS`, `sanitize_chat`, `sanitize_completion`), `gateway/config.py` (`default_reasoning_effort`)

Important Files: `sanitize.py`, `tests/test_sanitize.py`, `tests/fixtures/chat_stream_ollama040.txt`

Dependencies: Ollama ≥ 0.40.5 (format OpenAI-compat dengan field `reasoning`), capability `thinking` dari `/api/show`

Notes: Hasil uji perbandingan (10 prompt Indonesia): gemma3:4b paling natural; qwen3.5:4b unggul di tools/format tapi lebih lemah di fakta & nuansa bahasa; qwen3.5:9b paling akurat tapi 87% GPU dan 2× lebih lambat, sehingga dihapus. Lihat [decisions.md #24](decisions.md#24-model-gemma34b-utama--qwen354b-untuk-tools-mode-berpikir-mati-secara-default). Ollama 0.40.5 hanya menjalankan **1 request qwen sekaligus** (arsitektur `qwen35`), lihat [operations.md#kapasitas](operations.md#kapasitas).

## Embeddings

Purpose: `POST /v1/embeddings` untuk RAG.

Status: Completed. Endpoint dan validasi berfungsi, tetapi belum ada model embedding terpasang.

Location: `gateway/apps/public.py` (`embeddings`)

Important Files: `sanitize.py` (`sanitize_embeddings`)

Dependencies: Model dengan capability `embedding` (mis. `nomic-embed-text`) harus di-pull dan di-enable.

Notes: Dengan `gemma3:4b` → 400 `model_not_supported` (diuji live). Maks `MAX_EMBEDDING_INPUTS` (256) input.

## Daftar Model

Purpose: `GET /v1/models` berisi model yang di-enable.

Status: Completed (diuji live).

Location: `gateway/apps/public.py` (`list_models`)

Important Files: `db.py` (`enabled_models`)

Dependencies: Tabel `models`

Notes: Dibaca dari DB, bukan dari Ollama. Tidak dihitung ke RPM.

## Input Gambar (Vision)

Purpose: Konten `image_url` di pesan chat untuk model ber-capability `vision`.

Status: Completed. Unit test `test_images_need_vision_and_are_capped` + `test_image_url_must_be_inline_base64`; diuji live 2026-10-17 dengan screenshot dashboard (base64) ke `gemma3:4b` dan `qwen3.5:4b` lewat URL publik.

Location: `gateway/sanitize.py` (`sanitize_chat`)

Important Files: `sanitize.py`

Dependencies: `gemma3:4b` punya capability `completion` dan `vision` (dari `/api/show`).

Notes: Maks `MAX_IMAGES` (2) per request. Body maks 4 MB (base64 ikut terhitung).

## Tools / Function Calling

Purpose: Meneruskan `tools` dan `tool_choice`.

Status: Completed (kondisional).

Location: `gateway/sanitize.py`

Important Files: `sanitize.py`

Dependencies: Model dengan capability `tools`.

Notes: `gemma3:4b` tidak punya `tools` → 400. Belum bisa diuji live.

## API Key Auth

Purpose: Setiap client memakai `Authorization: Bearer sk-...`.

Status: Completed (diuji live: tanpa key → 401, key salah → 401, key di-revoke → 401).

Location: `gateway/auth.py`, `gateway/core.py` (`lookup_key`)

Important Files: `auth.py` (`require_api_key`, `KeyCache`, `generate_key`, `hash_key`)

Dependencies: Tabel `api_keys`

Notes: Hanya hash SHA-256 yang disimpan. Cache 30 dtk (termasuk hasil negatif). Revoke via admin API langsung mengosongkan cache.

## Rate Limit per Key

Purpose: Membatasi request per menit per key.

Status: Completed (diuji live: 429 + `Retry-After`).

Location: `gateway/limits.py` (`SlidingWindow`, `RateResult`), `apps/public.py`

Important Files: `limits.py`

Dependencies: `DEFAULT_RPM` (60) atau `api_keys.rpm_limit`

Notes: Sliding window 60 dtk, in-memory (reset saat restart). Header `x-ratelimit-*-requests`. Hanya POST generasi yang dihitung.

## Kontrol Konkurensi & Antrean GPU

Purpose: Mencegah GPU kelebihan beban dan membagi slot secara adil.

Status: Completed (diuji live: puncak 2 aktif / 3 menunggu dengan limit 2).

Location: `gateway/limits.py` (`ConcurrencyGate`), `gateway/proxy.py`

Important Files: `limits.py`, `proxy.py`

Dependencies: `GLOBAL_CONCURRENCY` (= `OLLAMA_NUM_PARALLEL` = 4), `QUEUE_TIMEOUT_S` (20), `DEFAULT_MAX_CONCURRENT` (2)

Notes: Slot key penuh → 429 langsung. Slot global penuh > 20 dtk → 503 `server_busy`. Gateway belum membatasi per model, padahal `qwen3.5:4b` hanya punya 1 slot di Ollama. Hasil uji kapasitas: [operations.md#kapasitas](operations.md#kapasitas).

## Limit Gagal-Auth per IP

Purpose: Meredam brute force dan flood 401.

Status: Completed (unit/integration test).

Location: `gateway/limits.py` (`FailedAuthLimiter`), `gateway/auth.py`

Important Files: `limits.py`, `auth.py`

Dependencies: `FAILED_AUTH_PER_IP_PER_MIN` (20), `CLIENT_IP_HEADERS`

Notes: Terblokir → 429 `auth_rate_limited`, `Retry-After: 60`.

## Sanitasi Request

Purpose: Hanya meneruskan field OpenAI yang aman.

Status: Completed (diuji live: `options.num_ctx` dan `keep_alive` diabaikan, context tetap sesuai server).

Location: `gateway/sanitize.py`

Important Files: `sanitize.py` (`CHAT_FIELDS`, `COMPLETION_FIELDS`, `EMBEDDING_FIELDS`)

Dependencies: `MAX_TOKENS_CAP`, `MAX_BODY_BYTES`, `MAX_MESSAGES`, `MAX_IMAGES`

Notes: Lihat [decisions.md #8](decisions.md#8-allowlist-field-request).

## Usage Logging (DB)

Purpose: Mencatat setiap request generasi (token, latency, status, IP) per key.

Status: Completed (diuji live).

Location: `gateway/db.py` (`UsageRecord`, `UsageWriter`), `proxy.Finalizer`, `apps/public._generate`

Important Files: `db.py`

Dependencies: Tabel `usage_logs`

Notes: Ditulis batch di background. Retensi `USAGE_RETENTION_DAYS` (90), di-prune tiap 24 jam.

## Log Aktivitas, Audit & Health

Purpose: Memantau aktivitas dan dependency (permintaan eksplisit pemilik project).

Status: Completed (diuji live).

Location: `gateway/logs.py`, `gateway/middleware.py`, `gateway/health.py`, `apps/admin.py`, `cli.py`

Important Files: `logs.py` (`DailyFileHandler`, `setup_logging`), `middleware.py` (`RequestContextMiddleware`)

Dependencies: `LOG_DIR`, `LOG_RETENTION_DAYS` (30), `LOG_CONSOLE`

Notes: `logs/access-*.log` (JSON per request, status nyata termasuk 499/503 pada stream), `logs/gateway-*.log` (event + audit admin/CLI + auth gagal), `logs/health-*.log`. Probe `/health` yang sukses dan admin GET yang sukses tidak dicatat.

## Health Monitor & Auto Re-warm

Purpose: Mengecek Ollama, service Windows, GPU, dan disk secara berkala; mencatat perubahan status; memuat ulang model setelah Ollama restart.

Status: Completed (diuji live: kill `ollama.exe` → `down` → `recovered` → warm-up otomatis tanpa request client).

Location: `gateway/health.py`

Important Files: `health.py` (`HealthMonitor`, `service_states` via ctypes, `gpu_stats`, `disk_usage`)

Dependencies: `nvidia-smi`, Windows SCM, `WATCH_SERVICES`, `HEALTH_INTERVAL_S` (30), `GPU_TEMP_WARN_C` (85), `DISK_WARN_GB` (10)

Notes: Re-warm maks 1× per 10 menit (masa tenggang 60 dtk setelah start).

## Warm-up Model

Purpose: Model siap sebelum request pertama.

Status: Completed (diuji live: request pertama setelah restart 0.6 dtk, sebelumnya 28 dtk).

Location: `gateway/ollama.py` (`warmup`), `gateway/core.py` (`Gateway.warmup`)

Important Files: `ollama.py`

Dependencies: `WARMUP=true`, model enabled pertama

Notes: Inferensi 1 token lewat `/api/generate`. `keep_alive` mengikuti `OLLAMA_KEEP_ALIVE=-1`.

## Health Endpoint Publik

Purpose: `GET /health` (liveness) dan `GET /health/ready` (cek Ollama) untuk pemantauan dari luar.

Status: Completed.

Location: `gateway/apps/public.py`

Important Files: `apps/public.py`

Dependencies: Ollama `/api/version`

Notes: Tanpa auth dan tanpa detail internal.

## Admin: Manajemen Key

Purpose: Buat, daftar, ubah, dan revoke API key.

Status: Completed (diuji live).

Location: `gateway/apps/admin.py` (`/admin/keys`)

Important Files: `apps/admin.py`, `db.py`

Dependencies: `ADMIN_KEY`

Notes: Plaintext key hanya dikembalikan saat dibuat. Semua aksi tercatat di audit log.

## Admin: Laporan Usage

Purpose: Ringkasan per hari/key/model dan request terbaru.

Status: Completed (diuji live via `status.ps1`).

Location: `gateway/apps/admin.py` (`/admin/usage`, `/admin/usage/recent`)

Important Files: `db.py` (`usage_summary`, `recent_usage`)

Dependencies: Tabel `usage_logs`, `api_keys`

Notes: Default 7 hari terakhir. `group_by=day` memakai tanggal UTC.

## Admin: Manajemen Model

Purpose: Melihat model terinstal vs allowlist, enable/disable, pull di background, dan hapus.

Status: Completed. List/enable diuji live. Pull/delete hanya diuji dengan mock.

Location: `gateway/apps/admin.py` (`/admin/models*`), `gateway/core.py` (`PullManager`)

Important Files: `core.py`, `ollama.py` (`pull`, `delete`, `tags`)

Dependencies: Ollama `/api/tags`, `/api/pull`, `/api/delete`

Notes: Enable hanya untuk model yang terinstal. Job pull disimpan di memori (maks 50) dan hilang saat restart.

## Admin: Status

Purpose: Kesehatan semua dependency secara live.

Status: Completed (diuji live).

Location: `gateway/apps/admin.py` (`/admin/status`)

Important Files: `health.py`

Dependencies: `HealthMonitor.check()`

Notes: Sumber data untuk `scripts/status.ps1`.

## Admin: Backup DB

Purpose: Salinan konsisten `gateway.db`.

Status: Completed (manual).

Location: `gateway/apps/admin.py` (`/admin/backup`), `db.py` (`backup`)

Important Files: `db.py`

Dependencies: SQLite `VACUUM INTO`

Notes: Output di `data\backups\`. Belum ada jadwal otomatis.

## CLI Manajemen

Purpose: Kelola key, model, dan usage tanpa admin key (langsung ke DB).

Status: Completed (diuji live).

Location: `gateway/cli.py`

Important Files: `cli.py`

Dependencies: Akses baca/tulis ke `data/` dan `.env` (ACL)

Notes: Perubahan key berlaku di service ≤ 30 dtk. Aksi yang mengubah state tercatat di `gateway-*.log` (`gateway.cli`).

## Deploy Windows Service

Purpose: Ollama dan gateway berjalan saat boot tanpa login, restart otomatis saat crash.

Status: Completed. Terpasang dan diuji: stop graceful < 1 dtk, kill → hidup lagi 5–7 dtk. Reboot teruji 2026-10-17 23:06–23:09: Windows Update me-restart PC 3×, kelima service (sshd, Tailscale, OllamaServe, OllamaGateway, NgrokTunnel) start otomatis pukul 23:10:01; warm-up gemma3 39 dtk; URL publik pulih.

Location: `scripts/install-ollama.ps1`, `scripts/install-gateway.ps1`, `scripts/common.ps1`

Important Files: `common.ps1` (`Install-WinSWService`, `Stop-ServiceSafe`, `Get-WinSW`)

Dependencies: WinSW v2.12.0, zip Ollama dari GitHub releases, Python di `C:\Program Files\Python311`

Notes: Lihat [decisions.md #13](decisions.md#13-winsw-menggantikan-nssm) dan [#14](decisions.md#14-service-gateway-memakai-base-python--pythonpath).

## Dashboard Admin Web

Purpose: Mengontrol provider dari browser di PC server: status dependency + meter slot GPU, ringkasan hari ini, grafik request per jam, cara akses client, kelola API key, pemakaian, request terbaru, kelola model.

Status: Completed (2026-10-16). Diuji: 74 test, screenshot Edge headless (desktop terang/gelap, ponsel 390px, tanpa overflow, console bersih).

Location: `gateway/static/dashboard/` (`index.html`, `app.css`, `app.js`), route `GET /admin/ui` di `gateway/apps/admin.py`

Important Files: `app.js` (vanilla JS, DOM via `h()`/`textContent`, tanpa `innerHTML`), `apps/admin.py` (`UI_HEADERS`, `TrustedHostMiddleware`, `/admin/info`)

Dependencies: Admin API, `ADMIN_KEY`, `PUBLIC_BASE_URL` (opsional, di `.env`), `ADMIN_ALLOWED_HOSTS`

Notes: Hanya di port 8001 (loopback). CSP `script-src 'self'` tanpa CDN/inline, `frame-ancestors 'none'`, `no-store`. Admin key di `sessionStorage`. Host header selain `127.0.0.1`/`localhost` → 400 (anti DNS rebinding). `group_by=hour` ditambahkan ke `/admin/usage` untuk grafik.

## Script Status

Purpose: Ringkasan pemantauan di terminal.

Status: Completed (diuji live).

Location: `scripts/status.ps1`

Important Files: `status.ps1`

Dependencies: Admin API, `ADMIN_KEY` dari `.env`, file log

Notes: `-Watch <detik>` untuk refresh otomatis. Tidak butuh admin.

## Script Upgrade

Purpose: Upgrade Ollama dan cloudflared (pengganti auto-update tray app).

Status: Completed. Dipakai 2026-10-17 untuk Ollama 0.34.4 → 0.40.5 (downtime ~1 menit, format OpenAI-compat tetap kompatibel; 0.40 hanya menambah field `timings` di chunk stream).

Location: `scripts/upgrade.ps1`

Important Files: `upgrade.ps1`, `common.ps1`

Dependencies: GitHub releases

Notes: Unduh dulu, lalu `Stop-ServiceSafe` gateway + Ollama (membersihkan runner yatim), rename `bin` → `bin.previous`, ekstrak ke `bin` bersih, start; jika gagal → rollback otomatis ke `bin.previous`. Cek GPU di akhir.

## Script Uninstall

Purpose: Menghapus service (file, model, DB, dan `.env` tetap ada).

Status: Completed, **belum pernah dijalankan**.

Location: `scripts/uninstall-services.ps1`

Important Files: `uninstall-services.ps1`

Dependencies: `common.ps1`

Notes: `-IncludeCloudflared` untuk ikut menghapus tunnel.

## Tunnel Gratis ngrok

Purpose: Eksposur publik HTTPS gratis dengan URL tetap (domain statis `*.ngrok-free.app`), tanpa membeli domain.

Status: Completed. Terpasang 2026-10-16 dan diuji dari publik: `/health` ok, tanpa key → 401, `/admin` & `/api` → 404, non-stream 1.6 dtk, stream TTFB 0.13 dtk sampai `[DONE]`, IP client asli tercatat dari `X-Forwarded-For`, inspector 4040 mati.

Location: `scripts/install-ngrok.ps1`

Important Files: `install-ngrok.ps1`, `common.ps1` (`Install-WinSWService`), config `D:\ollama\ngrok\ngrok.yml` (dibuat script, berisi token, ber-ACL)

Dependencies: Akun ngrok gratis, binary `C:\ngrok\ngrok.exe` (v3.36, disalin ke `D:\ollama\ngrok`), `CLIENT_IP_HEADERS` (`x-forwarded-for`)

Notes: Service `NgrokTunnel` menjalankan `ngrok start --all --config ... --log stdout`. Script mengganti `WATCH_SERVICES` di `.env` menjadi `OllamaServe,NgrokTunnel` lalu me-restart gateway. Paket gratis punya kuota bulanan, dan browser mendapat halaman peringatan (header `ngrok-skip-browser-warning` untuk melewatinya). Tidak ada WAF. Lihat [decisions.md #21](decisions.md#21-tunnel-gratis-ngrok-bukan-tailscale-funnel).

## Cloudflare Tunnel

Purpose: Eksposur publik via HTTPS tanpa port forwarding.

Status: Tidak dipakai (alternatif). Script siap dan lolos parse-check, tetapi pemilik tidak mau membeli domain; jalur publik memakai ngrok. Pakai script ini hanya bila nanti ada domain di Cloudflare (lalu hapus `NgrokTunnel`, pilih satu tunnel saja).

Location: `scripts/install-cloudflared.ps1`

Important Files: `install-cloudflared.ps1`, `common.ps1` (`Install-CloudflaredMsi`, `Find-Cloudflared`)

Dependencies: Domain di Cloudflare, token tunnel, konfigurasi dashboard (hostname + path regex, WAF, rate limit, Bot Fight Mode OFF)

Notes: Script menambahkan `--loglevel info --log-directory D:\ollama\logs\cloudflared` (dan opsional `--protocol http2`) ke ImagePath service. Belum diuji.

---

## Planned

Belum ada kode. Sumber: sesi desain 2026-09-27 dan permintaan pemilik sesudahnya. Detail siapa/kapan di [operations.md#pekerjaan-tertunda](operations.md#pekerjaan-tertunda).

| Rencana | Catatan |
|---|---|
| Kuota token / billing per key | Baru ada RPM + konkurensi |
| Admin jarak jauh tanpa SSH | Saat ini lewat SSH port forward. Alternatif: hostname terpisah + Cloudflare Access (jangan membuka port 8001 lewat ngrok) |
| Model embedding | Kandidat `qwen3-embedding:0.6b` (0,64 GB, 100+ bahasa). Dengan `OLLAMA_MAX_LOADED_MODELS=1` setiap embedding mengusir model chat; evaluasi VRAM untuk `=2` |
| Multi-node | Butuh state limit terpusat (tidak kompatibel dengan desain in-memory saat ini) |
| Jadwal backup DB | `POST /admin/backup` sudah ada; pemilik menolak penjadwalan untuk saat ini |
| Notifikasi Telegram | HealthMonitor → Telegram Bot API via `httpx` (tanpa dependency baru); token bot di `.env`. Menunggu keputusan pemilik |
| Setting `PRIMARY_MODEL` | Diperlukan bila model utama bukan model aktif pertama secara alfabet (warm-up & contoh kode dashboard) |
| Panduan client 1 halaman | Opsional |
