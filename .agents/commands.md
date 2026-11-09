# Commands

Semua perintah dijalankan dari root project `D:\ML\ollama-provider`, kecuali disebut lain. Shell default mesin ini adalah PowerShell 5.1. Script deploy butuh **PowerShell sebagai Administrator**.

## Installation

### Setup lingkungan dev

```powershell
uv sync                                          # buat .venv + dependency (termasuk dev: pytest, respx)
copy .env.example .env                           # jika belum ada
.venv\Scripts\python.exe -m gateway.cli gen-admin-key   # salin hasilnya ke ADMIN_KEY di .env
```

`.venv` dibuat dari `C:\Program Files\Python311\python.exe`. Kalau membuat ulang: `uv venv --python "C:\Program Files\Python311\python.exe"; uv sync`.

### Produksi (Windows Service), urut

```powershell
# 1. Ollama standalone -> D:\ollama, salin model, service OllamaServe (WinSW)
powershell -ExecutionPolicy Bypass -File scripts\install-ollama.ps1
#    parameter: -NumParallel 4 -ContextLength 8192 -SkipDownload -UninstallTrayApp
#               -Root D:\ollama -SourceModels C:\Users\User\.ollama\models

# 2. Gateway -> service OllamaGateway (.env + ADMIN_KEY dibuat bila belum ada, ACL, WinSW)
powershell -ExecutionPolicy Bypass -File scripts\install-gateway.ps1
#    parameter: -Root D:\ollama -Python "C:\Program Files\Python311\python.exe"

# 3a. Tunnel AKTIF: ngrok (gratis, domain statis *.ngrok-free.dev) -> service NgrokTunnel (WinSW)
#     Juga mengisi WATCH_SERVICES dan PUBLIC_BASE_URL di .env, lalu restart gateway.
powershell -ExecutionPolicy Bypass -File scripts\install-ngrok.ps1 -AuthToken <TOKEN> -Domain <domain-statis>
#     parameter: -Root D:\ollama -SourceExe C:\ngrok\ngrok.exe

# 3b. Alternatif berbayar (butuh domain di Cloudflare). Pilih SATU tunnel saja.
powershell -ExecutionPolicy Bypass -File scripts\install-cloudflared.ps1 -Token <TOKEN>
#     parameter: -Http2 (jika UDP 7844 diblokir) -LogRoot D:\ollama\logs
```

Authtoken ngrok jangan ditempel di chat atau commit; script menyimpannya di `D:\ollama\ngrok\ngrok.yml` (ber-ACL). Domain publik hanya disimpan di `.env` (`PUBLIC_BASE_URL`), tidak di repo.

Semua script idempoten: menjalankan ulang akan menghapus dan membuat ulang service.

## Development

```powershell
# Service produksi sudah memakai port 8000/8001. Instance dev harus memakai port, DB, dan log lain:
$env:PUBLIC_PORT = '18000'; $env:ADMIN_PORT = '18001'
$env:DB_PATH = "$env:TEMP\ollama-provider-dev.db"; $env:LOG_DIR = "$env:TEMP\ollama-provider-dev-logs"
$env:ADMIN_KEY = 'dev'; $env:HEALTH_INTERVAL_S = '5'
uv run python -m gateway                         # Ctrl+C untuk stop (graceful)
```

Instance dev tetap memakai Ollama service di `127.0.0.1:11434` dan ikut menghitung slot GPU yang sama secara fisik.

Contoh panggilan:

```powershell
curl.exe http://127.0.0.1:18000/v1/chat/completions -H "Authorization: Bearer sk-..." -H "Content-Type: application/json" -d '{\"model\":\"gemma3:4b\",\"messages\":[{\"role\":\"user\",\"content\":\"Halo\"}]}'
```

## Testing

```powershell
uv run pytest                                    # semua test (155), Ollama di-mock, tanpa jaringan, ~6 dtk
uv run pytest tests/test_proxy.py -q             # satu file
uv run pytest -k stream -q                       # filter nama test
uv run pytest tests/test_scheduler.py tests/test_key_policy.py tests/test_prompt_log.py -q   # antrean, kebijakan key, log prompt
node --check gateway/static/dashboard/app.js     # syntax dashboard (Node 20 terpasang di mesin ini)
```

| File test | Cakupan |
|---|---|
| `test_proxy.py` | Forward JSON/SSE, chunk usage, completions/embeddings, 404/413/400/429/503, error upstream, disconnect 499, fixture Ollama 0.40 |
| `test_scheduler.py` | `GpuScheduler`: kapasitas, giliran, batas tunggu, antrean penuh, adil per key, prioritas + slot cadangan, shutdown, uji invarian acak deterministik |
| `test_key_policy.py` | Migrasi kolom `api_keys`, kuota (termasuk pergantian periode dengan jam palsu), masa berlaku, model per key, admin/CLI |
| `test_prompt_log.py` | Penangkapan, privasi, penolakan, stream, opt-out, query plan kursor, koneksi reader, prune, regresi QA |
| `test_sanitize.py` | Allowlist field, gambar, tools, `reasoning_effort`, kunci persis |
| `test_admin.py` | Admin API, dashboard (header ketat, tanpa `innerHTML`), Host header asing |
| `test_auth.py`, `test_limits.py` | Auth, gagal-auth per IP, `X-Forwarded-For`, route tak dikenal, limiter |
| `test_logging.py` | Access log, file log harian, transisi health, re-warm, disk, service Windows |

Lint (belum ada konfigurasi di repo; perintah ad-hoc):

```powershell
uv run --no-project --with ruff ruff check gateway tests --select F,E9,B --ignore B008 --target-version py311
```

Baseline 2026-11-03: satu temuan, `B017` di `tests/test_prompt_log.py` (`pytest.raises(Exception)` untuk penulisan ke koneksi read-only), tidak berdampak. `B008` diabaikan karena `Depends(...)` di argumen default adalah pola FastAPI. Aturan gaya yang lebih luas (`E,W,SIM,I`) belum bersih (nama variabel `l`, titik koma, urutan import) dan tidak dijadikan syarat.

Validasi script PowerShell (syntax + ASCII-only):

```powershell
Get-ChildItem scripts\*.ps1 | ForEach-Object { $t=$null; $e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$t, [ref]$e); $n=@([IO.File]::ReadAllBytes($_.FullName) | ? { $_ -gt 127 }).Count; "{0} errors={1} non_ascii={2}" -f $_.Name, $e.Count, $n }
```

## Database

CLI (langsung ke `data/gateway.db`, tanpa admin key; perubahan key berlaku di service ≤ 30 dtk):

```powershell
.venv\Scripts\python.exe -m gateway.cli create-key --name client-a [--rpm 60] [--max-concurrent 2]
.venv\Scripts\python.exe -m gateway.cli create-key --name teman-a --daily-tokens 100000 --monthly-tokens 2000000 --expires 2026-12-31 --models gemma3:4b --priority 0
.venv\Scripts\python.exe -m gateway.cli create-key --name pribadi --no-prompt-log   # prompt key ini tidak dicatat
#   --expires: tanggal (hari terakhir berlaku, WIB) atau datetime ISO; --models dipisah koma; --priority 0-9
# CLI membuka DB langsung dan ikut menjalankan migrasi kolom baru: backup dulu setelah upgrade kode
.venv\Scripts\python.exe -m gateway.cli list-keys
.venv\Scripts\python.exe -m gateway.cli revoke-key <id>
.venv\Scripts\python.exe -m gateway.cli list-models
.venv\Scripts\python.exe -m gateway.cli enable-model <name>
.venv\Scripts\python.exe -m gateway.cli disable-model <name>
.venv\Scripts\python.exe -m gateway.cli usage [--days 7] [--group-by day|key|model] [--json]
.venv\Scripts\python.exe -m gateway.cli gen-admin-key
```

Admin API (lokal):

```powershell
$h = @{ Authorization = "Bearer $((Select-String '^ADMIN_KEY=(.*)' .env).Matches.Groups[1].Value)" }
Invoke-RestMethod http://127.0.0.1:8001/admin/keys -Headers $h
Invoke-RestMethod -Method Post http://127.0.0.1:8001/admin/keys -Headers $h -ContentType 'application/json' -Body '{"name":"client-a","rpm_limit":60}'
Invoke-RestMethod -Method Delete http://127.0.0.1:8001/admin/keys/3 -Headers $h
Invoke-RestMethod "http://127.0.0.1:8001/admin/usage?group_by=key&since=2026-09-01" -Headers $h
Invoke-RestMethod -Method Post http://127.0.0.1:8001/admin/backup -Headers $h      # -> data\backups\ (ikut berisi prompt_logs)

# Kebijakan per key (PATCH hanya mengubah field yang dikirim; null = kembali ke default/tanpa batas)
Invoke-RestMethod -Method Patch http://127.0.0.1:8001/admin/keys/3 -Headers $h -ContentType 'application/json' -Body '{"daily_token_limit":100000,"monthly_token_limit":2000000,"expires_at":"2026-12-31","allowed_models":["gemma3:4b"]}'
Invoke-RestMethod -Method Patch http://127.0.0.1:8001/admin/keys/3 -Headers $h -ContentType 'application/json' -Body '{"priority":1}'
Invoke-RestMethod -Method Patch http://127.0.0.1:8001/admin/keys/3 -Headers $h -ContentType 'application/json' -Body '{"log_prompts":false}'
(Invoke-RestMethod http://127.0.0.1:8001/admin/keys -Headers $h).data | ft id, name, priority, log_prompts, expired, @{n='today';e={$_.usage.day_tokens}}

# Log prompt (selalu terbatas rentang; default 24 jam, maks 100 baris per panggilan)
(Invoke-RestMethod "http://127.0.0.1:8001/admin/prompts?limit=20" -Headers $h).data | ft id, ts, key_name, client_ip, end_user, model, status_code, prompt_preview
Invoke-RestMethod "http://127.0.0.1:8001/admin/prompts?since=2026-11-01&key_id=7&status=error&q=password" -Headers $h
Invoke-RestMethod "http://127.0.0.1:8001/admin/prompts?before=<nilai next dari halaman sebelumnya>" -Headers $h
Invoke-RestMethod http://127.0.0.1:8001/admin/prompts/123 -Headers $h | ConvertTo-Json -Depth 5   # isi lengkap satu baris

# Antrean GPU live
(Invoke-RestMethod http://127.0.0.1:8001/admin/status -Headers $h).gateway.concurrency | ConvertTo-Json -Depth 5
```

`$h` memuat `ADMIN_KEY` dari `.env` ke variabel sesi saja. Jangan mencetaknya, dan jangan menempelnya ke chat atau log. Script uji di scratchpad menerimanya lewat env var.

Query read-only langsung:

```powershell
.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('file:data/gateway.db?mode=ro', uri=True); print(c.execute('select * from usage_logs order by id desc limit 5').fetchall())"
# jumlah log prompt per key + rentang waktu (tanpa membaca isi prompt)
.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('file:data/gateway.db?mode=ro', uri=True); print(c.execute('select key_name, count(*), min(ts), max(ts) from prompt_logs group by key_name').fetchall())"
# kolom api_keys setelah migrasi
.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('file:data/gateway.db?mode=ro', uri=True); print([r[1] for r in c.execute('pragma table_info(api_keys)')])"
```

Saat memeriksa isi `prompt_logs` dari shell, jangan menyalin isi prompt client ke chat, commit, atau file di luar `data/`. Untuk statistik, cukup hitung baris.

Migrasi: tidak ada tool. Schema dibuat otomatis saat start (lihat [database.md](database.md#migration-strategy)).

## Build

Tidak ada build step. Python dijalankan langsung dari source (`python -m gateway`), dan dependency dikunci di `uv.lock`.

## Deployment

| Tugas | Perintah |
|---|---|
| Status service | `Get-Service OllamaServe, OllamaGateway, NgrokTunnel, Cloudflared` |
| Deploy perubahan kode gateway | `Restart-Service OllamaGateway` (service menjalankan source di folder project) |
| Deploy perubahan dependency | `uv sync`, lalu `Restart-Service OllamaGateway` |
| Ubah setting gateway | edit `.env`, lalu `Restart-Service OllamaGateway`. Daftar setting: [architecture.md#konfigurasi](architecture.md#konfigurasi-gatewayconfigpy) |
| Deploy kode yang menambah kolom DB | `POST /admin/backup` dulu, lalu `Restart-Service OllamaGateway`; cek `database migrated: api_keys.<kolom> added` di `logs\gateway-*.log` |
| Atur log prompt | `.env`: `PROMPT_LOG_ENABLED`, `PROMPT_LOG_RETENTION_DAYS`, `PROMPT_LOG_MAX_CHARS`, lalu restart. Per key: dashboard *Ubah* → *Catat prompt & jawaban*. Ubah retensi → perbarui `CLIENT.md#privasi` |
| Ubah env Ollama (`NUM_PARALLEL`, context) | `scripts\install-ollama.ps1 -SkipDownload -NumParallel 2 -ContextLength 8192`, lalu samakan `GLOBAL_CONCURRENCY` di `.env` dan restart gateway |
| Atur antrean GPU | `.env`: `QUEUE_TIMEOUT_S`, `MAX_QUEUE`, `MODEL_SWITCH_AFTER_S`, `MODEL_PARALLEL` (mis. `nama:tag=1`), `SERIAL_ARCHITECTURES`, `PRIORITY_RESERVED_SLOTS`, `PRIORITY_QUEUE_TIMEOUT_S`, lalu `Restart-Service OllamaGateway`. Cek arsitektur model: `Invoke-RestMethod -Method Post http://127.0.0.1:11434/api/show -Body '{"model":"<nama>"}' -ContentType application/json \| % { $_.model_info.'general.architecture' }` |
| Restart Ollama | `. .\scripts\common.ps1; Stop-ServiceSafe OllamaGateway; Stop-ServiceSafe OllamaServe; Start-Service OllamaServe, OllamaGateway` (jangan `Stop-Process ollama.exe`: runner yatim memegang VRAM) |
| Upgrade Ollama / ngrok / cloudflared | `scripts\upgrade.ps1 [-Component ollama\|ngrok\|cloudflared\|all]` (Ollama: versi lama disimpan di `D:\ollama\bin.previous`, rollback otomatis bila gagal) |
| Rollback Ollama manual | `Stop-ServiceSafe OllamaGateway; Stop-ServiceSafe OllamaServe; Rename-Item D:\ollama\bin bin.failed; Rename-Item D:\ollama\bin.previous bin; Start-Service OllamaServe, OllamaGateway` |
| Hapus service | `scripts\uninstall-services.ps1 [-IncludeNgrok] [-IncludeCloudflared]` |
| Kembalikan tray app | rename `C:\Users\User\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\Ollama.lnk.disabled` → `Ollama.lnk` (setelah service dihapus) |
| Host tidak sleep | `powercfg /change standby-timeout-ac 0; powercfg /change hibernate-timeout-ac 0` |

## Akses jarak jauh (pemilik lewat SSH)

Pemilik masuk dari jauh lewat **SSH di atas Tailscale**. Dashboard admin hanya mendengarkan `127.0.0.1:8001`, jadi diakses lewat port forwarding:

```bash
# di laptop pemilik (bukan di server); lalu buka http://127.0.0.1:8001/admin/ui
ssh -N -L 8001:127.0.0.1:8001 dev@<alamat-tailscale-server>
# VS Code Remote-SSH: panel Ports -> Forward a Port -> 8001
```

**Jangan me-restart PC dari jauh** sebelum Tailscale disetel *Run unattended* dari akun Windows "User". Tanpa itu, Tailscale (dan SSH pemilik) baru tersambung setelah "User" login. API tetap pulih karena berjalan sebagai service. Kalau terpaksa restart: `shutdown /r /t 120` (batalkan dengan `shutdown /a`), lalu cek dari luar lewat `https://<domain>/health`.

## Model

```powershell
# lewat dashboard (menu Model) atau admin API:
Invoke-RestMethod -Method Post http://127.0.0.1:8001/admin/models/pull -Headers $h -ContentType 'application/json' -Body '{"model":"qwen3.5:4b","enable":false}'
Invoke-RestMethod http://127.0.0.1:8001/admin/models/pull -Headers $h                       # progres job
Invoke-RestMethod -Method Patch 'http://127.0.0.1:8001/admin/models/qwen3.5:4b' -Headers $h -ContentType 'application/json' -Body '{"enabled":true}'
Invoke-RestMethod -Method Delete 'http://127.0.0.1:8001/admin/models/qwen3.5:9b' -Headers $h
curl.exe -s http://127.0.0.1:11434/api/show -d '{\"model\":\"qwen3.5:4b\"}'                # capabilities
```

- Cek muat 100% di GPU setelah menambah model: `curl.exe http://127.0.0.1:11434/api/ps` (`size_vram` harus sama dengan `size`).
- Uji mode berpikir model baru: `reasoning_effort` `none`/`medium`/`high` lewat `/v1/chat/completions`, dengan `max_tokens` besar. Lihat [decisions.md #24](decisions.md#24-model-gemma34b-utama--qwen354b-untuk-tools-mode-berpikir-mati-secara-default).
- Uji perbandingan model dijalankan dengan script sementara di scratchpad (bukan di repo). Metodenya: prompt Indonesia per model berurutan, `/api/chat` native untuk `eval_count`/`eval_duration`, uji beban 4 paralel, lalu warm-up `gemma3:4b` lagi.
- Uji kapasitas (2026-10-18) juga memakai script sementara. Script mengirim N stream `/v1/chat/completions` bersamaan, langsung ke `127.0.0.1:11434/v1` dan lewat gateway `127.0.0.1:8000/v1`. Varian lain: mode soak (N worker berulang selama S detik) dan client yang putus saat antre. Selama uji, `/admin/status` di-sampling untuk melihat jumlah request berjalan per jalur; `ADMIN_KEY` diteruskan lewat env var, tidak pernah dicetak. Key sementara dibuat dengan `gateway.cli create-key --name loadtest-... --max-concurrent 16` lalu dicabut di blok `finally`. Metrik: TTFT, tok/dtk per stream, total tok/dtk, kode error, VRAM (`nvidia-smi`). Akhiri dengan memuat ulang `gemma3:4b`. Hasil: [operations.md#kapasitas](operations.md#kapasitas).

## QA & uji performa

Script uji tidak disimpan di repo (sekali pakai, di scratchpad agent). Pola yang dipakai, supaya bisa diulang:

| Uji | Cara |
|---|---|
| Uji live fitur | Key sementara `qa-<fitur>` lewat admin API (`ADMIN_KEY` dari env var), kirim request ke `127.0.0.1:8000/v1` dan ke URL publik, periksa `/admin/keys`, `/admin/prompts`, access log. Key **dicabut di blok `finally`**. Log prompt key uji ikut tersimpan, jadi catat jumlahnya di [operations.md](operations.md#pekerjaan-tertunda) |
| Soak | N worker async mengirim campuran stream/non-stream/putus-di-tengah selama S menit; hitung sukses, 499, `server_busy`, waktu tunggu p50/p95, memori proses gateway, dan (bila log prompt aktif) apakah setiap request punya baris |
| Uji performa dashboard / DB | Jalankan **instance dev** (port 18000/18001, `DB_PATH` dan `LOG_DIR` di `%TEMP%`, lihat [Development](#development)), isi DB dev dengan baris sintetis (mis. 50 ribu baris `prompt_logs` tersebar 30 hari lewat `Database.insert_prompts`), lalu ukur `/admin/prompts` (halaman pertama, 10 halaman kursor, pencarian kata yang tidak ada) sambil mengukur `/v1/models` secara paralel. **Jangan** mengisi data sintetis ke DB produksi |
| Query plan | `EXPLAIN QUERY PLAN` untuk SQL baru; tidak boleh ada `USE TEMP B-TREE` di query daftar (contoh: `test_prompt_list_queries_stay_on_the_ts_index`) |
| Uji mutasi | Kembalikan sementara satu perbaikan (salin file, ubah, jalankan test terkait, pulihkan), pastikan test regresinya gagal |
| UI dashboard | Edge headless (Playwright `channel="msedge"`, atau CDP langsung lewat `msedge.exe --headless=new --remote-debugging-port=...`): login dengan admin key dari env var, buka setiap menu, screenshot desktop 1366 px / ponsel 390 px / gelap, cek `document.documentElement.scrollWidth` (tanpa overflow) dan error console |

## Setelah PC restart

Tidak ada langkah manual. Kelima service (sshd, Tailscale, OllamaServe, OllamaGateway, NgrokTunnel) start otomatis, dan warm-up memuat `gemma3:4b` (~40 dtk). Verifikasi:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\status.ps1
(Get-CimInstance Win32_OperatingSystem).LastBootUpTime
Get-WinEvent -FilterHashtable @{LogName='System'; Id=1074} -MaxEvents 3 | Format-List TimeCreated, Message   # siapa yang me-restart
```

Yang **tidak** ikut menyala tanpa login "User": Docker Desktop (container MongoDB project lain), aplikasi desktop lain di sesi "User", dan Tailscale (bila belum *Run unattended*).

## Debugging

```powershell
powershell -ExecutionPolicy Bypass -File scripts\status.ps1            # ringkasan satu layar
powershell -ExecutionPolicy Bypass -File scripts\status.ps1 -Watch 5   # refresh tiap 5 dtk

Get-Content logs\access-$(Get-Date -Format yyyy-MM-dd).log -Wait -Tail 20                  # aktivitas live
Get-Content logs\access-$(Get-Date -Format yyyy-MM-dd).log | ConvertFrom-Json | ? status -ge 400 | ft
Select-String logs\gateway-*.log -Pattern ' (WARNING|ERROR) '                            # warning/error
Select-String logs\health-*.log -Pattern 'down|warn|recovered'                            # dependency
Get-Content logs\OllamaGateway.err.log -Tail 50                                           # crash gateway
Get-Content D:\ollama\logs\OllamaServe.err.log -Tail 50                                   # log Ollama
Get-Content logs\OllamaGateway.wrapper.log, D:\ollama\logs\OllamaServe.wrapper.log -Tail 20   # WinSW
Get-Content D:\ollama\logs\ngrok\NgrokTunnel.out.log -Tail 50                             # tunnel ngrok (aktif)
Get-Content D:\ollama\logs\cloudflared\cloudflared.log -Tail 50                           # tunnel cloudflared (bila dipasang)

Invoke-RestMethod http://127.0.0.1:8001/admin/status -Headers $h | ConvertTo-Json -Depth 6  # health live
curl.exe http://127.0.0.1:11434/api/ps                                                     # model dimuat + VRAM
nvidia-smi
sc.exe qfailure OllamaGateway                                                              # failure action
Get-Process llama-server                                                                   # harus 1 (runner model aktif)
. .\scripts\common.ps1; Remove-OrphanOllamaRunners                                         # bunuh runner yatim (induk mati)
# VRAM per proses (nvidia-smi menampilkan N/A di mode WDDM):
(Get-Counter '\GPU Process Memory(*)\Dedicated Usage').CounterSamples | ? CookedValue -gt 50MB | sort CookedValue -Desc | select InstanceName, @{n='MB';e={[int]($_.CookedValue/1MB)}}
```

Masalah yang pernah terjadi:

| Gejala | Penyebab / solusi |
|---|---|
| Service macet `StopPending` | Terjadi saat masih memakai NSSM. Sudah diganti WinSW. Di script, pakai `Stop-ServiceSafe` |
| Request pertama sekitar 30 dtk | Kompilasi kernel CUDA sekali untuk akun SYSTEM (cache di `C:\Windows\System32\config\systemprofile\AppData\Roaming\NVIDIA\ComputeCache`) |
| Model tidak 100% GPU | Turunkan `-NumParallel`/`-ContextLength` di `install-ollama.ps1`, lalu samakan `GLOBAL_CONCURRENCY` |
| Port 11434 bentrok | Tray app Ollama hidup lagi. Cek folder Startup user "User" |
| Klien menerima HTML dari Cloudflare | Bot Fight Mode / Browser Integrity Check masih aktif |
| 524 dari Cloudflare | Request non-stream terlalu lama. Turunkan `MAX_TOKENS_CAP` atau pakai `stream: true` |
| `git` menolak repo ("dubious ownership") | `git config --global --add safe.directory D:/ML/ollama-provider` |
| VRAM penuh padahal hanya satu model | Runner `llama-server` yatim setelah `ollama.exe` di-kill paksa. Jalankan `Remove-OrphanOllamaRunners` |
| Jawaban qwen kosong / `finish_reason: length` | Mode berpikir menghabiskan token. Pastikan `DEFAULT_REASONING_EFFORT=none`; client yang menyalakan berpikir perlu `max_tokens` besar |
| `/v1/completions` ke qwen ditolak 400 | Disengaja: Ollama mengembalikan teks kosong untuk model berpikir. Pakai `/v1/chat/completions` |
| Client browser menerima halaman peringatan ngrok | Paket gratis ngrok; client browser kirim header `ngrok-skip-browser-warning: 1` (SDK/curl tidak terpengaruh) |
| ngrok berhenti melayani | Kuota bulanan paket gratis habis, atau authtoken dicabut. Cek dashboard ngrok (*Usage*) dan `D:\ollama\logs\ngrok\` |
| API mati beberapa menit tengah malam | Windows Update me-restart PC (Event ID 1074 oleh `TrustedInstaller.exe`). Atur *Active hours* |
| Request pertama setelah pindah model ~4 dtk | Normal: `OLLAMA_MAX_LOADED_MODELS=1`, model lain harus dimuat dulu |
| `503 server_busy` "the server is restarting" | Normal saat service gateway di-restart (antrean dilepas, `Retry-After: 15`) |
| `503 server_busy` "the request queue is full" | Lebih dari `MAX_QUEUE` (32) request biasa antre. Cek panel antrean / `concurrency.lanes` |
| `429 insufficient_quota` / `401 api_key_expired` | Kebijakan per key bekerja. Ubah kuota/masa berlaku key di dashboard |
| Admin API `503 admin_disabled` | `ADMIN_KEY` kosong di `.env` (instance dev: set `$env:ADMIN_KEY`) |
| Menu *Prompt* kosong | Rentang waktu terlalu sempit, `PROMPT_LOG_ENABLED=false`, atau key dengan `log_prompts=0`. Lihat [operations.md](operations.md#insiden-gejala--tindakan) |
| Test menggantung setelah membuat `Database` sendiri | Panggil `await db.close()` di `finally` (aiosqlite memakai thread, dan ada dua koneksi) |
