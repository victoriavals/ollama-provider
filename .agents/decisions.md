# Technical Decision Records

Keputusan #1–#20 diambil pada sesi desain dan implementasi 2026-09-27; #21–#26 pada 2026-10-15 s.d. 2026-10-18 (lihat field `Date` masing-masing). Sumbernya permintaan pemilik project, hasil pengujian di mesin ini, dan kode di repository. Tambahkan entri baru di bagian bawah dengan format yang sama.

---

## 1. Gateway FastAPI di depan Ollama

Date: 2026-09-27

Context: Ollama tidak punya API key, rate limit, maupun usage log, sehingga tidak aman dibuka langsung ke internet.

Decision: Membangun gateway Python (FastAPI + httpx + uvicorn, dikelola `uv`) yang meneruskan request ke endpoint OpenAI-compat milik Ollama (`/v1/*`).

Reason: Python 3.11 dan uv sudah terpasang. FastAPI + httpx mendukung streaming async dengan baik.

Impact: Semua trafik klien lewat `gateway/`. Ollama tetap di loopback.

Alternative Considered: Mengekspos Ollama langsung (ditolak: tanpa auth).

---

## 2. Hanya format API OpenAI-compatible

Date: 2026-09-27

Context: Pemilik ditanya antara OpenAI-compatible, Ollama native, atau keduanya.

Decision: Hanya `/v1/models`, `/v1/chat/completions`, `/v1/completions`, `/v1/embeddings`.

Reason: Pilihan pemilik. Format ini langsung bisa dipakai SDK OpenAI dan LangChain.

Impact: Endpoint native `/api/*` tidak diekspos. Gateway memakainya secara internal saja (`ollama.py`).

Alternative Considered: Ollama native, atau keduanya.

---

## 3. Eksposur internet lewat Cloudflare Tunnel (token, remotely-managed)

Date: 2026-09-27

Context: API harus bisa diakses publik tanpa membuka port router.

Decision: `cloudflared service install <token>`. Public hostname di-routing ke `http://127.0.0.1:8000` dengan path regex `^/(v1|health)(/.*)?$`, ditambah WAF rule (blokir `/v1` tanpa header Authorization), rate limit per IP, serta Bot Fight Mode dan Browser Integrity Check dimatikan.

Reason: Pilihan pemilik. HTTPS gratis, tanpa port forwarding, dan instalasi service token paling sederhana di Windows.

Impact: Request non-stream harus selesai < 100 dtk (524). IP client diambil dari `CF-Connecting-IP`. **Status: script siap, belum dipasang (domain belum ada).**

Alternative Considered: ngrok (sudah terpasang di `C:\ngrok`), port forwarding + Caddy, tunnel locally-managed dengan `config.yml`.

---

## 4. Jalan sebagai Windows Service

Date: 2026-09-27

Context: Layanan harus hidup setelah reboot tanpa login.

Decision: Tiga service: `OllamaServe`, `OllamaGateway` (bergantung pada `OllamaServe`), dan `Cloudflared`.

Reason: Pilihan pemilik. Akses GPU langsung tanpa overhead WSL2/Docker.

Impact: Deploy lewat script PowerShell admin di `scripts/`.

Alternative Considered: Docker Compose (Docker Desktop + WSL2 GPU), script manual.

---

## 5. Admin API di port terpisah yang hanya loopback

Date: 2026-09-27

Context: Admin endpoint tidak boleh bisa dijangkau dari internet.

Decision: App admin terpisah di `127.0.0.1:8001` dalam proses yang sama, tidak pernah dirouting tunnel, dan dilindungi `ADMIN_KEY` (`hmac.compare_digest`).

Reason: Isolasi jaringan lebih kuat daripada filter header. Satu proses memungkinkan state bersama.

Impact: Admin jarak jauh harus lewat RDP atau hostname terpisah + Cloudflare Access (belum dibuat).

Alternative Considered: Menolak request admin yang membawa header `Cf-Connecting-Ip` + flag `ADMIN_ALLOW_REMOTE` (rancangan awal, dibatalkan saat review).

---

## 6. Satu proses, state in-memory

Date: 2026-09-27

Context: Rate limit, antrean GPU, dan cache key butuh state bersama.

Decision: `python -m gateway` menjalankan 2 `uvicorn.Server` dalam satu event loop, dengan 1 worker. State ada di memori (`limits.py`, `auth.KeyCache`).

Reason: Kapasitas dibatasi oleh satu GPU, jadi skala horizontal tidak diperlukan. Tanpa dependency Redis.

Impact: Restart menghapus counter rate limit dan job pull. Tidak boleh multi-worker.

Alternative Considered: Multi-worker + Redis.

---

## 7. SQLite + aiosqlite tanpa ORM dan tanpa tool migrasi

Date: 2026-09-27

Context: Perlu penyimpanan key, allowlist model, dan usage.

Decision: SQLite (WAL) dengan satu koneksi aiosqlite, SQL mentah di `db.py`, dan schema `CREATE ... IF NOT EXISTS`. Usage ditulis lewat antrean batch (`UsageWriter`).

Reason: Volume kecil, satu mesin, zero-ops.

Impact: Perubahan schema pada DB yang sudah ada butuh `ALTER TABLE` manual.

Alternative Considered: PostgreSQL, ORM.

---

## 8. Allowlist field request

Date: 2026-09-27

Context: Client bisa mengirim `options.num_ctx` besar atau `keep_alive` untuk menghabiskan VRAM.

Decision: `sanitize.py` hanya meneruskan field OpenAI yang dikenal, meng-cap `max_tokens` (`MAX_TOKENS_CAP`=2048), menolak `n>1`, dan hanya meloloskan `tools` jika model punya capability `tools`. `max_completion_tokens` dipetakan ke `max_tokens`.

Reason: Keamanan dan prediktabilitas kapasitas GPU. Cap token menjaga batas 100 dtk Cloudflare.

Impact: Fitur Ollama khusus tidak bisa dipakai client.

Alternative Considered: Passthrough penuh.

---

## 9. Desain streaming: ping, injeksi usage, dan finalizer

Date: 2026-09-27

Context: Cloudflare memutus koneksi (524) kalau byte pertama tidak datang dalam 100 dtk. Token usage perlu dicatat walau client tidak meminta. Client yang putus harus menghentikan generasi.

Decision: Kirim `: connected` segera, lalu `: ping` tiap 15 dtk selama antre. Selalu kirim `stream_options.include_usage=true` ke upstream dan buang chunk usage jika client tidak memintanya. `ClosingStreamingResponse` selalu `aclose()` generator, dan `Finalizer` idempoten melepas slot serta mencatat usage (499 untuk disconnect).

Reason: Diverifikasi: `include_usage` didukung Ollama 0.34. Uji disconnect menunjukkan GPU langsung idle.

Impact: Error setelah header dikirim sebagai event SSE `data: {"error":...}`.

Alternative Considered: Tanpa ping (berisiko 524), tanpa usage untuk stream.

---

## 10. Semaphore GPU global dengan batas tunggu

> **Digantikan oleh [#26](#26-scheduler-gpu-sadar-model-in-process) (2026-10-18).** Slot per key tetap; semaphore global diganti scheduler sadar-model.

Date: 2026-09-27

Context: VRAM 8 GB, `OLLAMA_NUM_PARALLEL=4`.

Decision: `ConcurrencyGate`: slot global = `GLOBAL_CONCURRENCY` (4) dengan tunggu maks `QUEUE_TIMEOUT_S` (20 dtk), lalu 503 `server_busy`. Slot per key (default 2) langsung ditolak dengan 429 bila penuh. `OLLAMA_MAX_QUEUE=16` sebagai lapisan terakhir.

Reason: Antrean dikendalikan gateway (jelas bagi client dan tercatat di log), tidak menunggu tanpa batas di Ollama.

Impact: `GLOBAL_CONCURRENCY` wajib sama dengan `OLLAMA_NUM_PARALLEL`.

Alternative Considered: Mengandalkan antrean Ollama saja.

---

## 11. Ollama standalone di `D:\ollama` (bukan binary tray app)

Date: 2026-09-27

Context: Tray app terpasang per-user di `C:\Users\User\...`. Updater-nya bisa mengganti atau menghapus binary.

Decision: `ollama-windows-amd64.zip` diekstrak ke `D:\ollama\bin`, model disalin (robocopy) ke `D:\ollama\models`, dan env service diset di XML WinSW.

Reason: Service tidak bergantung pada profil user. Drive D: punya ruang lebih besar.

Impact: Tidak ada auto-update, jadi pakai `scripts/upgrade.ps1`. Model asli di `C:\Users\User\.ollama\models` tetap ada.

Alternative Considered: Menjalankan binary tray app sebagai service.

---

## 12. Tray app hanya dimatikan autostart-nya

Date: 2026-09-27

Context: Tray app milik user Windows lain ("User") akan berebut port 11434 dengan service.

Decision: Proses dihentikan dan `Ollama.lnk` di folder Startup di-rename menjadi `Ollama.lnk.disabled`. Opsi `-UninstallTrayApp` tersedia tetapi tidak dipakai.

Reason: Pilihan pemilik, karena bisa dibalik.

Impact: Jika user "User" menjalankan tray app manual, akan terjadi konflik port.

Alternative Considered: Uninstall penuh.

---

## 13. WinSW menggantikan NSSM

Date: 2026-09-27

Context: Dengan NSSM 2.24-101 di Windows 11 build 26200: (a) stop service macet di `STOP_PENDING` ketika app punya proses child (launcher venv, runner Ollama); (b) setelah app crash, NSSM tidak me-restart (thread macet setelah "Killing process tree"). Keduanya terbukti lewat Event Log.

Decision: Memakai WinSW v2.12.0 (`WinSW-x64.exe`, self-contained). Satu salinan exe + XML per service di `D:\ollama\services`, restart lewat failure action SCM (5 dtk/10 dtk/30 dtk, reset 1 jam), dan `stoptimeout` + `stopparentprocessfirst`.

Reason: Diuji: stop graceful < 1 dtk, kill → hidup lagi 5–7 dtk, tanpa proses yatim.

Impact: `scripts/common.ps1` punya `Install-WinSWService`, `Stop-ServiceSafe` (fallback `taskkill /T /F`), dan `Remove-ServiceIfExists`.

Alternative Considered: NSSM (dibuang), Task Scheduler (tanpa stop graceful/dependency), pywin32 service.

---

## 14. Service gateway memakai base Python + PYTHONPATH

Date: 2026-09-27

Context: `.venv\Scripts\python.exe` adalah launcher yang menjalankan proses Python kedua, sehingga menyulitkan stop graceful dan deteksi crash.

Decision: Executable service = `home` dari `.venv\pyvenv.cfg` (`C:\Program Files\Python311\python.exe`), dengan `PYTHONPATH=<project>;<project>\.venv\Lib\site-packages`.

Reason: Satu proses. Python di Program Files bisa dibaca LocalSystem.

Impact: File `.pth` di venv tidak diproses. Dependency baru yang butuh `.pth` harus dievaluasi.

Alternative Considered: Launcher venv (dibuang).

---

## 15. Unduh langsung, bukan winget

Date: 2026-09-27

Context: winget di mesin ini gagal (`0x8a15000f: Data required by the source is missing`), bahkan setelah `winget source update`.

Decision: WinSW dan zip Ollama diunduh dari GitHub releases. cloudflared memakai MSI dari GitHub releases (`msiexec /qn`).

Reason: `winget source reset --force` terlalu invasif untuk konfigurasi user.

Impact: Upgrade cloudflared = instal ulang MSI (`upgrade.ps1`).

Alternative Considered: Memperbaiki winget.

---

## 16. File log harian bernama tanggal + access log JSON

Date: 2026-09-27

Context: Pemilik meminta log untuk memantau aktivitas dan dependency. Di Windows, rotasi dengan rename gagal kalau file sedang di-tail (`Get-Content -Wait`).

Decision: `DailyFileHandler` menulis `<prefix>-YYYY-MM-DD.log` (tanpa rename, retensi `LOG_RETENTION_DAYS`=30). Ada tiga file: `gateway-` (event + audit), `access-` (JSON per request, ditulis middleware setelah response selesai, termasuk status nyata stream), dan `health-`. Service men-set `LOG_CONSOLE=false`.

Reason: Aman di-tail, mudah di-parse (`ConvertFrom-Json`), dan tidak duplikat dengan stdout WinSW.

Impact: Admin GET yang sukses dan probe `/health` sukses tidak dicatat di access log (mengurangi noise dari `status.ps1 -Watch`).

Alternative Considered: `TimedRotatingFileHandler`, satu log teks per request.

---

## 17. Health monitor in-process dengan log transisi + re-warm

Date: 2026-09-27

Context: Pemilik perlu memantau dependency (Ollama, service, GPU, disk).

Decision: `HealthMonitor` setiap 30 dtk memeriksa status service lewat ctypes SCM (tanpa subprocess), GPU lewat `nvidia-smi`, dan disk lewat `shutil`. Yang dicatat hanya perubahan status, plus snapshot JSON tiap 5 menit. Jika Ollama hidup tanpa model dimuat, warm-up dijalankan ulang (maks 1× per 10 menit).

Reason: Tanpa tool monitoring eksternal. Restart Ollama yang cepat pun tertangani karena yang dicek adalah "tidak ada model dimuat", bukan transisi down.

Impact: `/admin/status` dan `scripts/status.ps1` memakai data yang sama.

Alternative Considered: Prometheus/Grafana, hanya memicu re-warm saat transisi (bisa terlewat).

---

## 18. Warm-up dengan inferensi 1 token

Date: 2026-09-27

Context: Load model saja membuat inferensi pertama tetap lebih lambat sekitar 3 dtk. Inferensi CUDA pertama untuk akun SYSTEM pernah memakan 28 dtk (kompilasi kernel sekali jalan).

Decision: `OllamaClient.warmup()` memanggil `/api/generate` dengan `prompt:"hi"` dan `num_predict:1`. `keep_alive` diserahkan ke `OLLAMA_KEEP_ALIVE=-1`.

Reason: Terukur: request klien pertama setelah restart turun dari 28 dtk menjadi 0.6 dtk.

Impact: Startup gateway sekitar 3 dtk lebih lama di background (tidak memblokir).

Alternative Considered: Load tanpa inferensi.

---

## 19. ACL `.env` dan `data/` memakai SID

Date: 2026-09-27

Context: `.env` berisi `ADMIN_KEY`, dan `data/` berisi hash key + IP. Awalnya `icacls` dengan `WORKGROUP\dev` gagal, dan `/T` dengan flag `(OI)(CI)` pada file sempat membuat DACL kosong.

Decision: Grant ke `*S-1-5-18` (SYSTEM), `*S-1-5-32-544` (Administrators), dan SID user installer. Folder dengan `(OI)(CI)`, lalu `icacls data\* /reset /T` agar file mewarisi ACL folder.

Reason: Nama akun lokal tidak ter-resolve dengan domain `WORKGROUP`.

Impact: User non-admin lain tidak bisa membaca `.env`.

Alternative Considered: Tanpa ACL.

---

## 20. Hanya `gemma3:4b` di v1

Date: 2026-09-27

Context: VRAM 8 GB. Pemilik memilih memakai model yang sudah ada.

Decision: `SEED_MODELS=gemma3:4b`, `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_CONTEXT_LENGTH=8192`. `/v1/embeddings` tetap ada tetapi membalas 400 `model_not_supported` untuk model tanpa capability `embedding`.

Reason: Model muat 100% di GPU (sekitar 2.9 GB).

Impact: Menambah model lewat `POST /admin/models/pull` + enable. Dengan `MAX_LOADED_MODELS=1`, pindah model berarti swap VRAM.

Alternative Considered: Qwen3 8B, qwen2.5-coder, model embedding (ditawarkan, belum dipilih).

---

## 21. Tunnel gratis: ngrok, bukan Tailscale Funnel

Date: 2026-10-15

Context: Pemilik tidak mau membayar domain untuk Cloudflare Tunnel. Opsi gratis yang ada di mesin: Tailscale (v1.102, sudah login dengan akun milik user Windows "User") dan ngrok (v3.36, `C:\ngrok`). Pemilik hanya mau Tailscale jika satu PC bisa terhubung ke dua akun sekaligus.

Decision: Memakai ngrok dengan domain statis gratis sebagai service WinSW `NgrokTunnel` (`scripts/install-ngrok.ps1`). Gateway membaca IP client dari header yang bisa diatur (`CLIENT_IP_HEADERS`, default `cf-connecting-ip,x-forwarded-for`, memakai entri **terakhir** `X-Forwarded-For`).

Reason: Satu perangkat Tailscale hanya bisa aktif di satu akun pada satu waktu ("switch account" berpindah, bukan paralel), jadi syarat pemilik tidak terpenuhi. ngrok gratis memberi URL HTTPS tetap dan mendukung streaming. Cloudflare Quick Tunnel ditolak karena URL acak dan, menurut dokumentasi Cloudflare, tidak mendukung SSE.

Impact: Ada kuota bulanan paket gratis ngrok dan halaman peringatan untuk browser. Tidak ada WAF/rate limit di edge, jadi perlindungan sepenuhnya di gateway. Tanpa penyesuaian IP, semua client akan terlihat sebagai 127.0.0.1 dan limit gagal-auth bisa memblokir semuanya sekaligus; itu sudah ditangani lewat `CLIENT_IP_HEADERS`.

Alternative Considered: Tailscale Funnel (URL `*.ts.net` tetap, gratis, tetapi butuh mengganti akun Tailscale PC ini), Cloudflare Quick Tunnel, Cloudflare Tunnel + domain berbayar (script tetap dipertahankan), LAN saja.

---

## 22. Hardening keamanan sebelum go-live publik

Date: 2026-10-16

Context: Audit keamanan menyeluruh sebelum mengekspos API lewat ngrok. Temuan: (a) `D:\ollama` dan folder project bisa diubah oleh *Authenticated Users* padahal dijalankan sebagai SYSTEM; mesin punya akun non-admin `CodexSandboxOffline`/`CodexSandboxOnline` → eskalasi ke SYSTEM; (b) `image_url` tidak divalidasi (Ollama 0.34 kebetulan menolak URL, tapi gateway tidak menjamin); (c) inspector lokal ngrok (127.0.0.1:4040) merekam header Authorization dan prompt.

Decision: (a) `Protect-AdminOnlyTree D:\ollama` (SYSTEM/Admins Full, Authenticated Users + LocalService RX; file ber-ACL protected seperti `ngrok.yml` dipertahankan) dan service `OllamaGateway` dijalankan sebagai `NT AUTHORITY\LocalService` (baca kode/`.env`, Modify `data/`+`logs/`); (b) `sanitize_chat` hanya menerima `data:image/...;base64,`; (c) `web_addr: false` di config ngrok.

Reason: Pilihan pemilik ("kunci + gateway akun rendah"): Codex tetap bisa mengedit project, tetapi kode yang diubah tidak berjalan dengan hak SYSTEM.

Impact: Diverifikasi: gateway berjalan sebagai LOCAL SERVICE, health monitor (nvidia-smi, SCM, disk), tulis DB, log, stop graceful 0.5 dtk, kill → kembali 7 dtk. Sisa risiko yang diterima: kode project yang diubah akun lain tetap berjalan sebagai LocalService (bisa membaca `ADMIN_KEY`/DB); Ollama di 127.0.0.1:11434 tetap tanpa auth untuk proses lokal.

Alternative Considered: Kunci folder project juga (berisiko mematikan alur kerja Codex), deploy ke salinan terkunci (langkah deploy tambahan), biarkan.

---

## 23. Dashboard admin web: lokal, tanpa dependency

Date: 2026-10-16

Context: Pemilik meminta dashboard untuk mengontrol provider dengan lebih mudah.

Decision: Halaman statis (`gateway/static/dashboard/`) disajikan oleh app admin di `127.0.0.1:8001/admin/ui`, memakai admin API yang sudah ada (+ `/admin/info`, `group_by=hour`). Vanilla JS/CSS tanpa framework, tanpa CDN, tanpa build step. CSP ketat, `TrustedHostMiddleware` (`ADMIN_ALLOWED_HOSTS`), admin key di `sessionStorage`. URL publik dibaca dari `PUBLIC_BASE_URL` di `.env` (diisi `install-ngrok.ps1`), supaya domain tidak masuk repo.

Reason: Halaman ini memegang `ADMIN_KEY`, jadi tidak boleh memuat kode pihak ketiga. Dengan tetap di port loopback, dashboard tidak menambah permukaan serangan dari internet. Tanpa build step, cocok dengan deploy "restart service".

Impact: Akses dari jarak jauh harus lewat remote desktop. Kontrol service (start/stop) sengaja tidak ada di dashboard, karena gateway berjalan sebagai LocalService tanpa hak mengatur service; gunakan script PowerShell.

Alternative Considered: Framework SPA + Chart.js dari CDN, mengekspos dashboard lewat ngrok dengan login.

---

## 24. Model: gemma3:4b utama + qwen3.5:4b untuk tools, mode berpikir mati secara default

Date: 2026-10-17

Context: Riset web merekomendasikan `qwen3.5:4b` (skor benchmark tinggi, tools, Apache-2.0). Pemilik juga bertanya kenapa bukan `qwen3.5:9b`. Ollama di-upgrade 0.34.4 → 0.40.5 (`scripts/upgrade.ps1`, kini dengan backup `bin.previous` + rollback otomatis) dan tiga model diuji dengan 10 prompt bahasa Indonesia + uji beban 4 paralel.

Decision: `gemma3:4b` tetap model utama (warm-up), `qwen3.5:4b` diaktifkan sebagai model kedua untuk tools/JSON/gambar, `qwen3.5:9b` dihapus. Gateway menyuntik `reasoning_effort` = `DEFAULT_REASONING_EFFORT` (`none`) untuk model ber-capability `thinking`, memvalidasi nilai client (`none`/`medium`/`high`), dan menolak `/v1/completions` untuk model berpikir.

Reason: Hasil terukur: gemma3:4b 100% GPU, 84 tok/dtk, bahasa Indonesia paling natural (meski salah fakta ibu kota Kaltim). qwen3.5:4b 100% GPU, 76 tok/dtk, VRAM lebih hemat, tools berfungsi, tetapi tanpa berpikir lebih sering salah nuansa (“postponed” → “dibatalkan”, arti “PHP”) dan sesekali berhalusinasi; dengan berpikir default 7/10 jawaban kosong karena token habis. `reasoning_effort: "low"` selalu berpikir sampai 2048 token tanpa jawaban; `medium` 2/5 macet. qwen3.5:9b paling akurat tetapi hanya 87% di GPU (VRAM total 7,3 GB), 41 tok/dtk.

Impact: Client memilih model per request. Berpindah model memicu muat ulang ~4 dtk karena `OLLAMA_MAX_LOADED_MODELS=1` (dua model tidak muat bersamaan). Client yang ingin berpikir harus mengirim `reasoning_effort` + `max_tokens` besar. Uji hanya 10 soal sekali jalan, jadi hasilnya arah, bukan kepastian. Uji kapasitas 2026-10-18 menemukan Ollama 0.40.5 hanya menjalankan 1 request qwen sekaligus (arsitektur `qwen35`), sehingga beban paralel sebaiknya diarahkan ke gemma3 ([operations.md#kapasitas](operations.md#kapasitas)).

Alternative Considered: Ganti total ke qwen3.5:4b, tetap gemma3 saja, qwen3.5:9b (spill ke CPU), `DEFAULT_REASONING_EFFORT=low/medium` (macet).

---

## 25. Pembersihan runner yatim dan upgrade Ollama dengan rollback

Date: 2026-10-17

Context: Dua proses `llama-server.exe` yatim (sisa uji crash 2026-09-27, ketika `ollama.exe` di-kill paksa) memegang ~2,2 GB VRAM selama tiga minggu. Script upgrade lama juga menimpa `D:\ollama\bin` tanpa cadangan.

Decision: `Remove-OrphanOllamaRunners` (`scripts/common.ps1`) membunuh `llama-server.exe` yang induknya sudah mati, dan otomatis dipanggil oleh `Stop-ServiceSafe OllamaServe`. `upgrade.ps1` mengunduh dulu, me-rename `bin` → `bin.previous`, mengekstrak ke `bin` bersih, dan rollback otomatis bila Ollama tidak merespons.

Reason: VRAM adalah sumber daya paling langka di mesin ini (8 GB). Upgrade harus bisa dibatalkan tanpa akses fisik, karena pemilik sering jauh.

Impact: Upgrade 0.34.4 → 0.40.5 berjalan dengan downtime ~1 menit. Tidak boleh lagi `Stop-Process` langsung pada `ollama.exe` (memory.md #16).

Alternative Considered: Membersihkan secara manual saat ditemukan; backup manual sebelum upgrade.

---

## 26. Scheduler GPU sadar-model in-process

Date: 2026-10-18

Context: Uji kapasitas (operations.md#kapasitas) menunjukkan tiga masalah semaphore global (#10):
- `qwen3.5:4b` hanya berjalan 1 request sekaligus di Ollama (arsitektur `qwen35`), tetapi gateway meloloskan 4. Tiga di antaranya antre di dalam Ollama tanpa batas waktu, tidak bisa dibatalkan, dan memegang slot milik gemma.
- Beban campuran dua model membuat Ollama bolak-balik memuat model (~4 dtk sekali muat), dan throughput turun dari ~265 ke ~41 tok/dtk.
- Antrean menunggu 20 dtk lalu gagal, bahkan saat jelas tidak akan kebagian slot.

Pemilik bertanya apakah perlu antrean dengan worker terpisah per request.

Decision: `GpuScheduler` (`gateway/scheduler.py`) menggantikan semaphore global, tetap di dalam proses gateway:
- satu jalur FIFO per model;
- kapasitas per model: `MODEL_PARALLEL`, lalu 1 untuk `SERIAL_ARCHITECTURES` (default `qwen35`, dibaca dari `general.architecture` di `/api/show`), selain itu `GLOBAL_CONCURRENCY`;
- satu model memegang GPU dalam satu waktu (giliran, sesuai `OLLAMA_MAX_LOADED_MODELS=1`). Giliran baru pindah bila model lain menunggu dan giliran sudah ≥ `MODEL_SWITCH_AFTER_S` (10 dtk), setelah request yang berjalan selesai;
- `MAX_QUEUE` (32): antrean penuh langsung dibalas 503;
- `QUEUE_TIMEOUT_S` naik 20 → 60 dtk.

Request masuk antrean lewat `Ticket` yang dipegang `Finalizer`. Ping stream menyertakan posisi antrean (`: ping queue=N`), access log mendapat field `queue_ms`, dan `/admin/status` menampilkan jalur per model (`lanes`, `turn`, `stats`). Warm-up juga lewat scheduler.

Reason:
- Worker atau broker terpisah (Redis/Celery) tidak menambah kapasitas, karena GPU adalah leher botolnya dan slot Ollama sudah berperan sebagai worker.
- Broker berarti dependency dan service baru (Redis tidak native di Windows), IPC untuk streaming token, dan pola submit-lalu-polling yang memutus kompatibilitas SDK OpenAI. Ia juga bertentangan dengan #6.
- Dengan scheduler, semua penantian terjadi di gateway (terbatas, bisa dibatalkan, terlihat), Ollama hanya menerima yang bisa langsung dijalankan, dan request dikelompokkan per model.
- Batas tunggu 60 dtk menutup kasus terburuk beban campuran: sisa giliran ≤ 10 dtk + menyelesaikan request berjalan ≤ ~30 dtk + muat model ~4 dtk.

Impact:
- Tidak ada perubahan schema DB, format error, maupun nama field log (hanya `queue_ms` yang ditambah).
- `ConcurrencyGate` kini hanya mengurus slot per key.
- Antrean penuh pada stream menjadi HTTP 503 asli (sebelumnya 200 + event error).
- Request non-stream yang client-nya putus saat antre tetap dijalankan, karena uvicorn tidak membatalkan handler (sama seperti sebelumnya).
- Slot per key ikut tertahan lebih lama saat antre (≤ 60 dtk), sehingga key dengan batas 2 lebih cepat mendapat `429 concurrency_limit_exceeded`.
- Setiap upgrade Ollama: cek ulang log `does not currently support parallel requests` dan sesuaikan `SERIAL_ARCHITECTURES`.
- Re-warm health monitor (#17) dilewati selama scheduler punya request berjalan atau antre, karena saat model berganti Ollama sesaat melaporkan "tidak ada model dimuat".
- Hasil uji ulang (operations.md#setelah-scheduler-sadar-model-2026-10-18): 12 gemma × 1024 token 12/12 sukses (sebelumnya 8/12), 8 qwen × 512 token 8/8 sukses (sebelumnya 6/8), soak campuran 2 menit 70/70 sukses.

Alternative Considered:
- Redis/Celery atau proses worker terpisah: ditolak dengan alasan di atas.
- Mengandalkan antrean Ollama (`OLLAMA_MAX_QUEUE`): tanpa batas waktu, tidak bisa dibatalkan, tidak terlihat, dan urutan antar model kacau.
- Semaphore per model tanpa giliran: tetap bolak-balik memuat model pada beban campuran.
- Mendukung `OLLAMA_MAX_LOADED_MODELS` > 1: belum perlu, karena dua model tidak muat di 8 GB.
