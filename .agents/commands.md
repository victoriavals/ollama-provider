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
uv run pytest                                    # semua test (79), Ollama di-mock, tanpa jaringan
uv run pytest tests/test_proxy.py -q             # satu file
uv run pytest -k stream -q                       # filter nama test
```

Lint (belum ada konfigurasi di repo; ini perintah ad-hoc yang dipakai saat pengembangan):

```powershell
uv run --no-project --with ruff ruff check gateway tests --select E,F,W,B,SIM,I --ignore E501,SIM105,B008 --target-version py311
```

Validasi script PowerShell (syntax + ASCII-only):

```powershell
Get-ChildItem scripts\*.ps1 | ForEach-Object { $t=$null; $e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$t, [ref]$e); $n=@([IO.File]::ReadAllBytes($_.FullName) | ? { $_ -gt 127 }).Count; "{0} errors={1} non_ascii={2}" -f $_.Name, $e.Count, $n }
```

## Database

CLI (langsung ke `data/gateway.db`, tanpa admin key; perubahan key berlaku di service ≤ 30 dtk):

```powershell
.venv\Scripts\python.exe -m gateway.cli create-key --name client-a [--rpm 60] [--max-concurrent 2]
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
Invoke-RestMethod -Method Post http://127.0.0.1:8001/admin/backup -Headers $h      # -> data\backups\
```

Query read-only langsung:

```powershell
.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('file:data/gateway.db?mode=ro', uri=True); print(c.execute('select * from usage_logs order by id desc limit 5').fetchall())"
```

Migrasi: tidak ada tool. Schema dibuat otomatis saat start (lihat [database.md](database.md#migration-strategy)).

## Build

Tidak ada build step. Python dijalankan langsung dari source (`python -m gateway`), dan dependency dikunci di `uv.lock`.

## Deployment

| Tugas | Perintah |
|---|---|
| Status service | `Get-Service OllamaServe, OllamaGateway, NgrokTunnel, Cloudflared` |
| Deploy perubahan kode gateway | `Restart-Service OllamaGateway` (service menjalankan source di folder project) |
| Deploy perubahan dependency | `uv sync`, lalu `Restart-Service OllamaGateway` |
| Ubah setting gateway | edit `.env`, lalu `Restart-Service OllamaGateway` |
| Ubah env Ollama (`NUM_PARALLEL`, context) | `scripts\install-ollama.ps1 -SkipDownload -NumParallel 2 -ContextLength 8192`, lalu samakan `GLOBAL_CONCURRENCY` di `.env` dan restart gateway |
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
- Uji kapasitas (2026-10-18) juga memakai script sementara. Script mengirim N stream `/v1/chat/completions` bersamaan, langsung ke `127.0.0.1:11434/v1` dan lewat gateway `127.0.0.1:8000/v1`. Key sementara dibuat dengan `gateway.cli create-key --name loadtest-... --max-concurrent 16` lalu dicabut di blok `finally`. Metrik: TTFT, tok/dtk per stream, total tok/dtk, kode error, VRAM (`nvidia-smi`). Akhiri dengan memuat ulang `gemma3:4b`. Hasil: [operations.md#kapasitas](operations.md#kapasitas).

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
