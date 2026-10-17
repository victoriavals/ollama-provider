# Operations

Kondisi mesin, jalur akses, prosedur rutin, penanganan insiden, dan konteks pemilik. Pelengkap [memory.md](memory.md) (jebakan teknis) dan [commands.md](commands.md) (perintah persis).

> Repo ini **publik**. File ini sengaja tidak memuat IP, domain ngrok, token, atau isi `.env`. Nilai-nilai itu ada di mesin: `.env` (`ADMIN_KEY`, `PUBLIC_BASE_URL`) dan `D:\ollama\ngrok\ngrok.yml`.

## Mesin *(observasi 2026-10-18)*

| | |
|---|---|
| CPU / RAM | Intel i5-13400F (10C/16T), 32 GB DDR5-5600 |
| GPU | RTX 4060 Ti 8 GB (8188 MiB), driver 610.88 (diperbarui otomatis oleh Windows Update 2026-10-17; sebelumnya 591.86), mode WDDM |
| OS | Windows 11 Pro build 26200, tanpa monitor; diakses lewat SSH |
| Disk | C: sisa ~53 GB, D: sisa ~135 GB. Model Ollama ~6,2 GB di `D:\ollama\models` |
| Akun aktif | `dev` (Administrator; dipakai agent dan SSH pemilik), `User` (Administrator; sesi desktop console yang selalu login, pemilik profil Tailscale dan tray app Ollama), `CodexSandboxOffline`/`CodexSandboxOnline` (bukan admin; dipakai agent Codex) |
| Software lain (di luar project) | Docker Desktop (container MongoDB untuk project lain, autostart saat "User" login), project Python lain di sesi "User" (memakai GPU ~0,4 GB), PostgreSQL, OpenSSH Server, Tailscale, VS Code, Office |
| Agent lain | Codex juga mengedit repo ini; cek proses `codex` dan `.git/*.lock` sebelum operasi git |

Beberapa program lain di PC mendengarkan di `0.0.0.0` (terjangkau dari LAN/Tailscale). Program-program itu tidak diekspos ngrok dan berada di luar scope project.

## Anggaran VRAM

| Pemakai | VRAM |
|---|---|
| Ollama + `gemma3:4b` (cache 4×8192 token, q8_0) | ~4,3 GB (total VRAM terpakai ~5,3 GB) |
| Ollama + `qwen3.5:4b` | ~3,9 GB (total VRAM terpakai ~4,9 GB) |
| `dwm.exe` (desktop sesi "User") | ~1,2 GB |
| explorer, project Python lain, aplikasi desktop | ~1 GB |

- Ruang realistis untuk model: **~5,9 GB**, sehingga bobot model sebaiknya ≤ ~4–5 GB. `qwen3.5:9b` (bobot 6,6 GB) hanya 87% di GPU dan 41 tok/dtk.
- Sign out "User" akan membebaskan ~1,5 GB, tetapi juga memutus Tailscale (SSH pemilik), Docker, dan project lain. **Jangan dilakukan dari jauh.**
- Runner `llama-server.exe` yatim pernah memakan ~2,2 GB. Cek dengan `Get-Process llama-server` (harus 1).

## Kapasitas

Uji beban 2026-10-18: request stream dengan prompt bahasa Indonesia dan `max_tokens` 256–1024. Uji dijalankan langsung ke Ollama dan lewat gateway, memakai key sementara yang langsung dicabut setelahnya.

| Skenario | Hasil |
|---|---|
| `gemma3:4b`, 1 request | 82 tok/dtk, TTFT 0,1–0,35 dtk |
| `gemma3:4b`, 2 / 4 bersamaan | 77 / 69 tok/dtk per request; total 150 / ~265 tok/dtk |
| `gemma3:4b`, 8 / 16 bersamaan (256 token) | Semua sukses. 4 berjalan, sisanya antre per gelombang ~3,8 dtk (TTFT maks 4 / 11,6 dtk); total tetap ~265 tok/dtk |
| `gemma3:4b`, 12 bersamaan (1024 token, ~15 dtk per jawaban) | 8 sukses, **4 mendapat `server_busy`** (menunggu slot > 20 dtk) |
| `qwen3.5:4b`, 1–8 bersamaan | **Diproses berurutan**, total tetap ~71–73 tok/dtk. Lewat gateway (8 × 512 token): 6 sukses (TTFT terakhir 39 dtk), 2 `server_busy` |
| Campuran 2 gemma + 2 qwen | Model dimuat bergantian (~4 dtk setiap pindah); total turun ke ~41 tok/dtk, TTFT terakhir 22 dtk |
| Satu key dengan batas default, 3 bersamaan | 2 sukses, 1 langsung `429 concurrency_limit_exceeded` |

VRAM tetap ~5,1 GB selama uji (cache KV dialokasikan saat model dimuat), dan utilisasi GPU ~90%.

- Kapasitas nyata: **4 request bersamaan untuk gemma, 1 untuk qwen.** Antrean menampung lebih banyak selama waktu tunggunya ≤ 20 dtk. Throughput gemma ~265 tok/dtk, atau sekitar 50 jawaban 300 token per menit.
- Key pemilik memakai `max_concurrent` default (2). Naikkan lewat dashboard bila aplikasinya butuh lebih banyak request bersamaan.
- Peluang perbaikan (belum dilakukan, perlu persetujuan pemilik):
  - batas konkurensi per model di gateway (qwen = 1), agar request qwen antre di gateway dengan timeout dan tidak memakan slot gemma;
  - uji `OLLAMA_NUM_PARALLEL=8` untuk gemma (throughput naik hampir linear dari 1 ke 4 request, sisa VRAM ~3 GB);
  - warm-up dengan prompt yang lebih panjang.

## Jalur akses

| Siapa | Jalur |
|---|---|
| Client API | `PUBLIC_BASE_URL` (domain statis ngrok, lihat `.env`) → port 8000. API key per client |
| Pemilik (shell) | SSH ke akun `dev` lewat **Tailscale** |
| Pemilik (dashboard) | SSH port forward `-L 8001:127.0.0.1:8001`, lalu `http://127.0.0.1:8001/admin/ui`, login dengan `ADMIN_KEY` |
| Admin API / CLI | Lokal di server (`127.0.0.1:8001`, `.venv\Scripts\python.exe -m gateway.cli`) |

**Risiko akses:** profil Tailscale terikat ke akun "User" dan belum *Run unattended*. Setelah restart, SSH pemilik hanya kembali bila "User" login. Itu terjadi otomatis setelah restart Windows Update, tetapi tidak dijamin untuk restart manual. Jangan me-restart dari jauh sebelum *Run unattended* diaktifkan.

## Runbook

**Memberi akses client baru**
1. Dashboard → *API key* → *Buat key* (nama client, batas opsional), atau `gateway.cli create-key --name <client>`. Key hanya tampil sekali.
2. Berikan ke client: `PUBLIC_BASE_URL`, key, dan panduan model: `gemma3:4b` untuk percakapan (default), `qwen3.5:4b` untuk tools/JSON (tanpa `/v1/completions`).
3. Pantau di dashboard (*Pemakaian*, *Request*) atau `logs\access-*.log`.

**Mencabut client:** dashboard → *Cabut* (langsung berlaku). Lewat CLI, perubahan berlaku ≤ 30 dtk.

**Menambah / mengganti model**
1. Pull dengan `enable:false` (client tidak terpengaruh).
2. Cek `capabilities` (`/api/show`) dan pastikan 100% GPU (`/api/ps`).
3. Uji dengan prompt bahasa Indonesia, uji beban 4 paralel (pastikan benar-benar paralel: log Ollama tidak boleh berisi `does not currently support parallel requests`), dan perilaku mode berpikir.
4. Aktifkan. Jika model **utama** berganti, warm-up masih memilih model aktif pertama secara alfabet, jadi perlu setting `PRIMARY_MODEL` (belum dibuat) atau nonaktifkan model lama dan ubah `SEED_MODELS` di `.env`.

**Upgrade Ollama:** `scripts\upgrade.ps1 -Component ollama` (downtime ~1 menit, rollback otomatis). Setelahnya rekam ulang respons OpenAI-compat dan bandingkan dengan `tests/fixtures/`, jalankan `uv run pytest`, lalu smoke test lewat URL publik dengan key sementara (cabut setelahnya).

**Cek harian:** `scripts\status.ps1`, atau dashboard *Ringkasan*. Health monitor mencatat setiap perubahan status ke `logs\health-*.log`.

**Setelah restart PC:** tidak ada langkah manual. Lihat [commands.md#setelah-pc-restart](commands.md#setelah-pc-restart).

## Insiden: gejala → tindakan

| Gejala | Tindakan |
|---|---|
| URL publik tidak merespons | Cek `Get-Service NgrokTunnel, OllamaGateway, OllamaServe` dan `D:\ollama\logs\ngrok\`. Kemungkinan: kuota ngrok habis, authtoken dicabut, atau service mati. WinSW me-restart otomatis service yang crash |
| `503 upstream_unavailable` | Ollama mati atau sedang restart; WinSW menghidupkannya kembali. Cek `D:\ollama\logs\OllamaServe.err.log` |
| `503 server_busy` | Keempat slot GPU penuh > 20 dtk, atau antrean qwen (hanya 1 request sekaligus). Beban tinggi: arahkan beban paralel ke gemma, pertimbangkan batas per key atau `MAX_TOKENS_CAP` lebih kecil. Lihat [Kapasitas](#kapasitas) |
| Respons lambat | Client bergantian model (muat ulang ~4 dtk), beberapa request qwen bersamaan (diproses berurutan), model sebagian di CPU (cek `/api/ps`), runner yatim, atau client menyalakan `reasoning_effort` |
| Jawaban qwen kosong | Mode berpikir menghabiskan token. Pastikan `DEFAULT_REASONING_EFFORT=none` |
| Banyak 401 dari satu IP | `FailedAuthLimiter` otomatis memblokir (429) selama 60 dtk. Cek `ip` di access log |
| API key client bocor | Cabut di dashboard, buat key baru untuk client itu |
| Authtoken ngrok bocor | Reset di dashboard ngrok, lalu jalankan ulang `install-ngrok.ps1` dengan token baru |
| Disk hampir penuh (health `warn`) | Hapus model tak terpakai lewat dashboard. Log terhapus otomatis setelah 30 hari, usage DB setelah 90 hari |
| GPU panas (health `warn` ≥ 85 °C) | Cek beban dan pendinginan; health monitor mencatat transisinya |
| API mati beberapa menit tengah malam | Windows Update me-restart PC (Event 1074). Atur *Active hours* |

## Konteks & preferensi pemilik

- Komunikasi dan dokumentasi dalam **Bahasa Indonesia**.
- Hanya memakai solusi **gratis**. Pemilik menolak membeli domain, sehingga ngrok dipakai, bukan Cloudflare.
- Sering jauh dari PC. Akses lewat SSH, dan PC tidak memakai monitor.
- Pemilik melakukan **commit/push sendiri**. Agent tidak commit kecuali diminta.
- Pernah menempel authtoken ngrok di chat (sudah di-reset). Ingatkan untuk tidak menempel rahasia; arahkan agar menjalankan script sendiri.
- Minta konfirmasi dulu sebelum: memasang/menghapus service, mengubah ACL, menghentikan proses milik user lain, me-restart PC, atau aksi apa pun yang bisa memutus akses jarak jauh.
- Keputusan yang sudah diambil pemilik: tray app hanya dimatikan autostart-nya; gateway berjalan sebagai LocalService; `gemma3:4b` utama + `qwen3.5:4b`; `qwen3.5:9b` dihapus; Docker Desktop tidak ditutup (MongoDB project lain); backup DB terjadwal ditolak untuk saat ini.

## Pekerjaan tertunda

| Item | Siapa | Status |
|---|---|---|
| Aktifkan Tailscale *Run unattended* dari akun "User" | Pemilik (di depan PC / RDP) | Tertunda; prasyarat restart dari jauh |
| Notifikasi Telegram (HealthMonitor + Telegram Bot API via `httpx`, tanpa dependency baru) | Pemilik membuat bot (@BotFather), token disimpan di `.env`; agent mengimplementasi | Sudah dijelaskan, menunggu keputusan |
| Atur *Active hours* Windows Update | Pemilik | Disarankan |
| Backup DB terjadwal | - | Ditolak untuk saat ini; `POST /admin/backup` tersedia |
| Model embedding (mis. `qwen3-embedding:0.6b`) | Sesuai kebutuhan client | Belum; perlu evaluasi `OLLAMA_MAX_LOADED_MODELS` |
| Setting `PRIMARY_MODEL` | Bila model utama berganti | Belum diperlukan |
| Batas konkurensi per model di gateway (qwen = 1) | Agent, bila pemilik setuju | Usulan dari uji kapasitas 2026-10-18 |
| Uji `OLLAMA_NUM_PARALLEL=8` untuk gemma | Agent, bila beban sering > 4 bersamaan | Usulan; perlu restart `OllamaServe` (downtime ~1 menit) |
| Warm-up dengan prompt lebih panjang | Agent | Usulan kecil (kompilasi CUDA setelah update driver) |
| Panduan singkat untuk client | Opsional | Belum |
| Uji `scripts\uninstall-services.ps1` | - | Belum pernah dijalankan |

## Riwayat perubahan

| Tanggal | Perubahan |
|---|---|
| 2026-09-27 | Cek spesifikasi mesin dan desain. Gateway v1 (auth, rate limit, usage, admin API, CLI). Ollama standalone + gateway sebagai service. NSSM diganti WinSW. Logging, health monitor, `status.ps1` |
| 2026-10-15 | Memilih ngrok (gratis, bukan Tailscale Funnel). `CLIENT_IP_HEADERS` (entri terakhir `X-Forwarded-For`) |
| 2026-10-16 | Audit keamanan: ACL `D:\ollama`, gateway LocalService, `image_url` hanya base64, inspector ngrok mati. `NgrokTunnel` live. Dashboard admin. `.agents/` + `CLAUDE.md`. Repo di-push publik oleh pemilik |
| 2026-10-17 | Runner yatim dibersihkan (2,2 GB VRAM). Riset model. Ollama 0.34.4 → 0.40.5 (backup/rollback). Uji 3 model. `qwen3.5:4b` ditambahkan + kontrol `reasoning_effort`. `qwen3.5:9b` dihapus. Windows Update me-restart PC malam hari (uji reboot lulus) |
| 2026-10-18 | Dokumentasi `.agents/` dilengkapi (file ini, pembaruan architecture/commands/memory/features/database/conventions). Uji kapasitas: gemma 4 request bersamaan, qwen hanya 1 (batasan Ollama untuk arsitektur `qwen35`) |
