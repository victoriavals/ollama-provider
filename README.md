# ollama-provider

Gateway API **OpenAI-compatible** di depan Ollama lokal. Fiturnya: API key per client, rate limit, usage log, admin API, dashboard, log pemantauan, dan health monitor. Semua komponen berjalan sebagai Windows Service (otomatis menyala saat boot, tanpa login) dan diekspos ke internet lewat **ngrok** (gratis). Cloudflare Tunnel tersedia sebagai alternatif berbayar.

```
Client (SDK OpenAI) --HTTPS--> ngrok edge --> ngrok agent [service NgrokTunnel]
   --> 127.0.0.1:8000  gateway publik  (/v1/*, /health)                  } satu proses Python
       127.0.0.1:8001  gateway admin + dashboard (/admin/*, lokal saja)  } [service OllamaGateway]
   --> 127.0.0.1:11434 ollama serve [service OllamaServe] -> gemma3:4b | qwen3.5:4b
Pemilik --SSH (port forward 8001)--> dashboard
```

Port 8001 (admin) dan 11434 (Ollama) tidak pernah dibuka lewat tunnel.

**Daftar isi:** [Hardware](#hardware--kapasitas) · [Untuk client](#untuk-client-cara-memakai-api) · [Instalasi](#instalasi-produksi-windows-service) · [Mengelola provider](#mengelola-provider) · [Monitoring](#monitoring--log) · [Operasional](#operasional) · [Keamanan](#keamanan) · [Konfigurasi](#konfigurasi-env) · [Troubleshooting](#troubleshooting) · [Development](#development)

## Hardware & kapasitas

| | |
|---|---|
| CPU / RAM | Intel i5-13400F (10C/16T), 32 GB DDR5-5600 |
| GPU | RTX 4060 Ti 8 GB VRAM |
| Ollama | 0.40.5 (`D:\ollama\bin`, model di `D:\ollama\models`) |
| Model | `gemma3:4b` (utama) dan `qwen3.5:4b` (tools/JSON), ~3 GB masing-masing, 100% di GPU. Hanya satu yang dimuat sekaligus |

- **Kapasitas** (uji beban 2026-10-18):
  - `gemma3:4b` memproses **4 request bersamaan** (`OLLAMA_NUM_PARALLEL=4` = `GLOBAL_CONCURRENCY=4`): ~69 token/detik per request, total ~265 token/detik.
  - `qwen3.5:4b` hanya **1 request sekaligus**, karena arsitekturnya belum mendukung paralel di Ollama 0.40.5: ~73 token/detik.
  - Request lain mengantre. Yang menunggu slot lebih dari 20 detik dibalas `503 server_busy`.
  - Setiap key default maksimal 2 request bersamaan; lebih dari itu dibalas `429`.
  - Detail: `.agents/operations.md` bagian Kapasitas.
- Dari 8 GB VRAM, sekitar 2 GB dipakai desktop Windows dan aplikasi lain, sehingga ruang realistis untuk model ~6 GB. Model kelas 8–9B (bobot > 6 GB) akan tumpah ke CPU dan jauh lebih lambat; `qwen3.5:9b` hanya 87% di GPU dan 41 token/detik.

## Untuk client: cara memakai API

Client butuh dua hal:
- **Base URL:** `https://<domain-ngrok>/v1`. Nilainya ada di `.env` (`PUBLIC_BASE_URL`) dan di dashboard (*Ringkasan → Cara akses*).
- **API key:** satu per client, dibuat lewat dashboard atau CLI (lihat [API key](#api-key)).

```bash
curl https://<domain-ngrok>/v1/chat/completions \
  -H "Authorization: Bearer sk-..." -H "Content-Type: application/json" \
  -d '{"model":"gemma3:4b","messages":[{"role":"user","content":"Halo!"}]}'
```

```python
from openai import OpenAI

client = OpenAI(base_url="https://<domain-ngrok>/v1", api_key="sk-...")

# percakapan biasa (streaming)
stream = client.chat.completions.create(
    model="gemma3:4b",
    messages=[{"role": "user", "content": "Jelaskan fotosintesis singkat."}],
    stream=True,
)
for chunk in stream:
    if chunk.choices:
        print(chunk.choices[0].delta.content or "", end="")

# function calling (hanya qwen3.5:4b)
tools = [{"type": "function", "function": {
    "name": "cek_cuaca", "description": "Cek cuaca sebuah kota",
    "parameters": {"type": "object", "properties": {"kota": {"type": "string"}}, "required": ["kota"]}}}]
r = client.chat.completions.create(model="qwen3.5:4b", tools=tools,
                                   messages=[{"role": "user", "content": "Bagaimana cuaca di Bandung?"}])
print(r.choices[0].message.tool_calls)   # -> cek_cuaca(kota="Bandung")
```

LangChain: `ChatOpenAI(base_url="https://<domain-ngrok>/v1", api_key="sk-...", model="gemma3:4b")`.

Client berbasis **browser** perlu menambahkan header `ngrok-skip-browser-warning: 1`, karena paket gratis ngrok menampilkan halaman peringatan untuk browser. SDK dan curl tidak terpengaruh.

### Memilih model

| Model | Pakai untuk | Catatan |
|---|---|---|
| `gemma3:4b` | Percakapan umum bahasa Indonesia (default) | Bahasa paling natural di uji kami; tanpa tools. Dimuat sejak start |
| `qwen3.5:4b` | Tools/function calling, output JSON terstruktur, gambar | Mode berpikir **mati secara default** (`reasoning_effort: "none"`), sehingga jawaban cepat. Akurasi fakta & nuansa bahasa Indonesia lebih lemah dari gemma3 |

- VRAM hanya cukup untuk satu model; request pertama setelah berpindah model butuh ~4 detik untuk memuat. Hindari memakai kedua model secara bersamaan, karena model akan dimuat bergantian dan request saling mengantre.
- `qwen3.5:4b` hanya memproses satu request sekaligus. Untuk beban paralel, pakai `gemma3:4b`.
- `reasoning_effort` (khusus `qwen3.5:4b`): `none` (default), `medium`, atau `high`. Saat berpikir dinyalakan, beri `max_tokens` besar (mis. 2048), karena token berpikir ikut dihitung. `medium` kadang berpikir sampai batas token tanpa menjawab; `low` ditolak karena di uji kami selalu begitu.
- `qwen3.5:4b` tidak mendukung `/v1/completions` (Ollama mengembalikan teks kosong untuk model berpikir); gunakan `/v1/chat/completions`.
- Hasil uji perbandingan (10 soal bahasa Indonesia, 3 model) dan alasannya ada di `.agents/decisions.md` #24.

### Endpoint publik

| Endpoint | Keterangan |
|---|---|
| `GET /v1/models` | model yang di-enable |
| `POST /v1/chat/completions` | stream & non-stream, gambar base64 (`image_url` berisi `data:image/...;base64,...`, maks 2), tools (bila model mendukung) |
| `POST /v1/completions` | `prompt` string, hanya untuk model tanpa mode berpikir (mis. `gemma3:4b`) |
| `POST /v1/embeddings` | hanya untuk model embedding (belum ada yang terpasang; gemma3/qwen → 400) |
| `GET /health`, `GET /health/ready` | tanpa auth |

Field yang diteruskan ke Ollama hanya: `model, messages, prompt, input, stream, stream_options, max_tokens` (`max_completion_tokens` dipetakan ke sini), `temperature, top_p, stop, seed, presence_penalty, frequency_penalty, response_format, user`, serta `reasoning_effort` untuk model yang bisa berpikir. `tools` hanya diteruskan jika modelnya mendukung. Field lain (`options`, `keep_alive`, `num_ctx`, `think`, ...) dibuang supaya client tidak bisa menghabiskan VRAM. `max_tokens` di-cap ke `MAX_TOKENS_CAP` (default 2048). URL gambar `http(s)://` atau `file://` ditolak (mencegah SSRF).

### Error & limit

| Status | `code` | Penyebab |
|---|---|---|
| 400 | `model_not_supported`, dll. | request tidak valid / fitur tidak didukung model |
| 401 | `invalid_api_key` | key tidak ada / salah / dicabut |
| 404 | `model_not_found` | model tidak di-enable |
| 413 | `request_too_large` | body > `MAX_BODY_BYTES` (4 MB) |
| 429 | `rate_limit_exceeded` | melewati batas request per menit key (`Retry-After`) |
| 429 | `concurrency_limit_exceeded` | request paralel per key melewati batas |
| 429 | `auth_rate_limited` | terlalu banyak auth gagal dari satu IP (diblokir 60 detik) |
| 500 | `internal_error` | error tak terduga di gateway (detail hanya di log) |
| 502 / 504 | `bad_gateway` / `upstream_timeout` | koneksi ke Ollama gagal / terlalu lama |
| 503 | `server_busy` | semua slot GPU penuh > 20 detik |
| 503 | `upstream_unavailable` | Ollama mati (biasanya pulih sendiri dalam beberapa detik) |

Pada streaming, header sudah terkirim duluan (ada ping `: ping` setiap 15 detik saat antre). Karena itu error dikirim sebagai event `data: {"error": {...}}`, dan SDK OpenAI akan me-raise-nya sebagai exception. Setiap respons membawa header `x-request-id` dan `x-ratelimit-*-requests`. Client yang memutus stream dicatat dengan status 499.

## Instalasi produksi (Windows Service)

Jalankan dari **PowerShell sebagai Administrator**, di folder project:

```powershell
# 1. Ollama standalone -> D:\ollama, model disalin ke D:\ollama\models, service OllamaServe
powershell -ExecutionPolicy Bypass -File scripts\install-ollama.ps1
#    (opsional) -UninstallTrayApp  untuk menghapus tray app, bukan hanya mematikan autostart-nya
#    Kalau output tidak menunjukkan "100% GPU": ulangi dengan -NumParallel 2 -SkipDownload
#    lalu set GLOBAL_CONCURRENCY=2 di .env

# 2. Gateway -> service OllamaGateway (akun LocalService; .env + ADMIN_KEY dibuat otomatis)
powershell -ExecutionPolicy Bypass -File scripts\install-gateway.ps1

# 3. Tunnel ke internet: pilih SATU
#  a) ngrok, gratis tanpa domain (dipakai sekarang; lihat langkah di bawah)
powershell -ExecutionPolicy Bypass -File scripts\install-ngrok.ps1 -AuthToken <TOKEN> -Domain <domain-statis-ngrok>
#  b) Cloudflare Tunnel, butuh domain di Cloudflare (token dari Zero Trust > Networks > Tunnels)
powershell -ExecutionPolicy Bypass -File scripts\install-cloudflared.ps1 -Token <TOKEN>
#     tambahkan -Http2 jika jaringan memblokir UDP 7844
```

Semua script idempoten: menjalankan ulang akan menghapus dan membuat ulang service.

### Tunnel gratis: ngrok

1. Daftar gratis di <https://dashboard.ngrok.com>. Di layar awal pilih **Share Localhost**. Menu *Gateway*, *AI Gateway*, dan *Team Members* tidak diperlukan.
2. Salin **authtoken** di <https://dashboard.ngrok.com/get-started/your-authtoken>. Tidak perlu menjalankan `ngrok config add-authtoken`, karena script yang memasangnya.
3. Klaim **domain statis gratis** di <https://dashboard.ngrok.com/domains>. URL ini tetap dan tidak berubah saat restart.
4. Jalankan `install-ngrok.ps1` seperti di atas. Script akan:
   - memasang service `NgrokTunnel` (WinSW: auto-start dan restart saat crash);
   - menyimpan token di `D:\ollama\ngrok\ngrok.yml` (hanya bisa dibaca admin) dan mematikan inspector lokal ngrok (port 4040, yang merekam API key dan prompt);
   - mengisi `WATCH_SERVICES` dan `PUBLIC_BASE_URL` di `.env`, lalu me-restart gateway;
   - mengecek `https://<domain>/health` dari sisi publik.

**Jangan menempel authtoken di chat atau commit.** Kalau terlanjur, reset di dashboard ngrok lalu jalankan ulang script dengan token baru.

Catatan paket gratis ngrok:
- Ada kuota bulanan (transfer data dan jumlah request). Pantau di dashboard ngrok bagian *Usage*.
- Tidak ada WAF seperti di Cloudflare. Perlindungan bergantung pada API key, rate limit, dan limit gagal-auth per IP di gateway. IP client dibaca dari `X-Forwarded-For` (`CLIENT_IP_HEADERS`).
- Kenapa bukan Tailscale Funnel: satu PC hanya bisa aktif di **satu** akun Tailscale pada satu waktu, dan Tailscale di PC ini sudah dipakai akun lain.

### Konfigurasi dashboard Cloudflare (hanya untuk opsi b)

1. **Public hostname** pada tunnel: `llm.<domain>` → Service `HTTP` `127.0.0.1:8000`, Path `^/(v1|health)(/.*)?$`.
2. **WAF custom rule** (Block):
   ```
   (http.host eq "llm.<domain>" and starts_with(http.request.uri.path, "/v1")
    and not any(lower(http.request.headers.names[*])[*] eq "authorization"))
   ```
3. **Rate limiting rule**: misalnya 120 request / 10 detik per IP untuk `llm.<domain>`.
4. **Matikan Bot Fight Mode**, lalu buat Configuration Rule untuk `llm.<domain>` dengan **Browser Integrity Check: Off**. Tanpa ini, SDK akan menerima halaman challenge HTML.

Pakai satu tunnel saja: kalau pindah ke Cloudflare, hapus `NgrokTunnel` (`scripts\uninstall-services.ps1 -IncludeNgrok` menghapus semua service; atau hapus service-nya saja).

### Pengaturan host

```powershell
powercfg /change standby-timeout-ac 0      # jangan sleep
powercfg /change hibernate-timeout-ac 0
```

Atur juga **Settings → Windows Update → Advanced options → Active hours**. Windows Update pernah me-restart PC ini tengah malam (API mati ~4 menit). Semua service menyala lagi sendiri setelah reboot, tanpa perlu login.

## Mengelola provider

### Dashboard

Dashboard ada di **`http://127.0.0.1:8001/admin/ui`** dan hanya bisa dibuka dari PC server. Dari jauh, pakai **SSH port forwarding**:

```bash
# di laptop (bukan di server), biarkan terminal ini terbuka:
ssh -N -L 8001:127.0.0.1:8001 dev@<alamat-server>
# lalu buka http://127.0.0.1:8001/admin/ui di browser laptop
```

Di VS Code Remote-SSH: panel **Ports** → **Forward a Port** → `8001`. Login memakai `ADMIN_KEY`:

```powershell
Select-String '^ADMIN_KEY=' D:\ML\ollama-provider\.env
```

| Menu | Isi |
|---|---|
| Bagian atas | Lampu status Ollama / GPU / Tunnel / Disk + meter slot GPU (dipakai/antre) |
| Ringkasan | Request, gagal, dan token hari ini; grafik request per jam (24 jam); **Cara akses** (URL publik + contoh kode, tombol salin); status semua dependency |
| API key | Buat key (ditampilkan sekali), ubah batas per menit & request paralel, cabut |
| Pemakaian | Per key / model / hari, rentang 24 jam / 7 hari / 30 hari |
| Request | 100 request terakhir (waktu, key, model, status, token, latensi, IP), diperbarui otomatis, filter "hanya yang gagal" |
| Model | Unduh model (dengan progres), aktifkan/nonaktifkan, hapus |

Dashboard tidak memuat script dari luar, memakai CSP ketat, menolak Host header asing (anti DNS rebinding), dan menyimpan admin key hanya selama tab terbuka. Tombol start/stop service sengaja tidak ada (gateway berjalan dengan hak rendah); gunakan PowerShell.

### API key

```powershell
.venv\Scripts\python.exe -m gateway.cli create-key --name client-a --rpm 60 --max-concurrent 2
.venv\Scripts\python.exe -m gateway.cli list-keys
.venv\Scripts\python.exe -m gateway.cli revoke-key 3
.venv\Scripts\python.exe -m gateway.cli usage --days 30 --group-by key
```

Key hanya ditampilkan sekali; database hanya menyimpan hash SHA-256 dan prefix-nya. Perubahan lewat CLI berlaku paling lambat 30 detik (cache auth), sedangkan lewat dashboard atau admin API berlaku langsung.

### Model

Cara aman menambah model (lewat dashboard menu *Model*, atau admin API):
1. Unduh **tanpa diaktifkan**: `POST /admin/models/pull {"model":"<nama>","enable":false}`. Client tidak terpengaruh.
2. Cek muat 100% di GPU (`curl.exe http://127.0.0.1:11434/api/ps`, `size_vram` = `size`) dan kemampuannya (`/api/show` → `capabilities`).
3. Uji dengan pertanyaan bahasa Indonesia. Untuk model "thinking", uji juga `reasoning_effort`.
4. Aktifkan: `PATCH /admin/models/<nama> {"enabled":true}`.

Model utama (yang dimuat saat start) adalah model aktif pertama menurut abjad, yaitu `gemma3:4b`.

### Admin API (lokal, `http://127.0.0.1:8001`)

Header: `Authorization: Bearer <ADMIN_KEY>`. Dokumentasi interaktif: `http://127.0.0.1:8001/admin/docs`.

| Endpoint | |
|---|---|
| `POST /admin/keys` `{name, rpm_limit?, max_concurrent?}` | buat key |
| `GET /admin/keys` · `PATCH /admin/keys/{id}` · `DELETE /admin/keys/{id}` | list / ubah / cabut |
| `GET /admin/usage?since=&until=&key_id=&group_by=hour\|day\|key\|model` | ringkasan pemakaian |
| `GET /admin/usage/recent?limit=50` | log request terbaru |
| `GET /admin/models` · `PATCH /admin/models/{name}` `{enabled}` | allowlist model |
| `POST /admin/models/pull` `{model, enable}` → `GET /admin/models/pull/{job}` | unduh model di background |
| `DELETE /admin/models/{name}` | hapus model |
| `GET /admin/status` | Ollama, model yang dimuat, GPU, service, disk, antrean |
| `GET /admin/info` | URL publik, batas default, model aktif |
| `POST /admin/backup` | salin DB ke `data\backups\` |
| `GET /admin/ui` | dashboard |

```powershell
$h = @{ Authorization = "Bearer $((Select-String '^ADMIN_KEY=(.*)' .env).Matches.Groups[1].Value)" }
Invoke-RestMethod http://127.0.0.1:8001/admin/status -Headers $h | ConvertTo-Json -Depth 5
```

## Monitoring & log

Ringkasan satu layar (service, health dependency, GPU, usage hari ini, request terakhir, warning terbaru):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\status.ps1            # sekali
powershell -ExecutionPolicy Bypass -File scripts\status.ps1 -Watch 5   # refresh tiap 5 detik
```

| File | Isi |
|---|---|
| `logs\access-YYYY-MM-DD.log` | **Aktivitas**: satu baris JSON per request: `ip` (IP asli client dari header tunnel), `key`, `key_name`, `endpoint`, `model`, `status`, `duration_ms`, token, `error`. Status yang dicatat adalah hasil sebenarnya (stream putus 499, antrean penuh 503) |
| `logs\gateway-YYYY-MM-DD.log` | Event aplikasi, **audit admin/CLI** (key dibuat/diubah/dicabut, model enable/pull/hapus, backup), auth gagal, transisi health |
| `logs\health-YYYY-MM-DD.log` | **Dependency**: perubahan status (`is down` → `recovered`) + snapshot JSON lengkap tiap 5 menit |
| `logs\OllamaGateway.err.log` | crash / traceback gateway (WinSW) |
| `D:\ollama\logs\OllamaServe.err.log` | log Ollama (load model, error CUDA, request) |
| `D:\ollama\logs\ngrok\NgrokTunnel.out.log` | log tunnel ngrok |
| `D:\ollama\logs\cloudflared\cloudflared.log` | log tunnel Cloudflare (bila dipasang) |

Health monitor mengecek setiap 30 detik:
- Ollama: bisa dihubungi, dan model 100% di GPU;
- service `OllamaServe` dan `NgrokTunnel`;
- GPU: suhu ≥ 85 °C = warn;
- sisa disk: < 10 GB = warn.

Setelah Ollama restart, model otomatis dimuat ulang. Data yang sama tersedia live di `GET /admin/status` dan di dashboard. Log disimpan 30 hari (`LOG_RETENTION_DAYS`), usage di DB 90 hari (`USAGE_RETENTION_DAYS`). Belum ada notifikasi otomatis (misalnya Telegram); masalah hanya tercatat di log dan dashboard.

```powershell
Get-Content logs\access-$(Get-Date -Format yyyy-MM-dd).log -Wait -Tail 20                                   # aktivitas live
Get-Content logs\access-$(Get-Date -Format yyyy-MM-dd).log | ConvertFrom-Json | Where-Object status -ge 400 | Format-Table
Select-String logs\health-*.log -Pattern WARNING                                                            # warning dependency
```

## Operasional

| | |
|---|---|
| Status service | `Get-Service OllamaServe, OllamaGateway, NgrokTunnel` |
| Deploy perubahan kode / `.env` | `Restart-Service OllamaGateway` |
| Restart Ollama | `. .\scripts\common.ps1; Stop-ServiceSafe OllamaGateway; Stop-ServiceSafe OllamaServe; Start-Service OllamaServe, OllamaGateway` |
| Upgrade Ollama / ngrok / cloudflared | `scripts\upgrade.ps1 [-Component ollama\|ngrok\|cloudflared]`. Versi lama Ollama disimpan di `D:\ollama\bin.previous`, dan otomatis dipulihkan bila versi baru gagal start |
| Hapus service | `scripts\uninstall-services.ps1 [-IncludeNgrok] [-IncludeCloudflared]` |
| Definisi service (WinSW) | `D:\ollama\services\*.xml` (dibuat script; jangan diedit manual) |

Service dijalankan dengan [WinSW](https://github.com/winsw/winsw): stop graceful (Ctrl+C, lalu kill setelah timeout) dan restart otomatis saat crash (5 / 10 / 30 detik). NSSM tidak dipakai karena di Windows 11 ini ia macet di `STOP_PENDING` dan tidak me-restart app yang crash. **Jangan mematikan `ollama.exe` dengan `Stop-Process`**: proses runner-nya (`llama-server.exe`) akan tertinggal dan tetap memakan VRAM. Hentikan lewat service.

### Setelah PC restart

Tidak ada langkah manual. `OllamaServe`, `OllamaGateway`, `NgrokTunnel`, SSH, dan Tailscale menyala sendiri, lalu `gemma3:4b` dimuat (~40 detik; lebih lama sekali setelah update driver NVIDIA). URL ngrok tetap sama, dan API key serta data pemakaian tetap utuh. Ini sudah teruji saat Windows Update me-restart PC. Cek dengan `scripts\status.ps1` atau buka `https://<domain-ngrok>/health` dari HP.

Yang **tidak** ikut menyala tanpa login akun "User": Docker Desktop, aplikasi desktop lain di akun itu, dan **Tailscale** (bila belum *Run unattended*).

### Akses jarak jauh & restart dari jauh

Pemilik masuk lewat **SSH di atas Tailscale**. Profil Tailscale di PC ini terikat ke akun Windows "User" dan belum diatur *Run unattended*, jadi Tailscale baru tersambung setelah "User" login. Restart oleh Windows Update kebetulan otomatis login; restart manual tidak dijamin.

- **Sebelum me-restart PC dari jauh**, aktifkan **Tailscale → Settings → Run unattended** dari akun "User" (di depan PC atau lewat Remote Desktop). Tanpa itu, SSH bisa terputus sampai ada yang login di PC.
- Kalau terpaksa: `shutdown /r /t 120` (batalkan dengan `shutdown /a`). API tetap pulih sendiri; pantau lewat `https://<domain-ngrok>/health`.

## Keamanan

- **API publik:** API key per client (hanya hash SHA-256 yang disimpan), rate limit per menit dan per request paralel, blokir IP setelah 20 auth gagal per menit, batas ukuran body / jumlah pesan / gambar, allowlist field, dan cap `max_tokens`.
- **Hanya port 8000 yang terekspos.** Admin API, dashboard (8001), dan Ollama (11434) hanya mendengarkan `127.0.0.1`. Dokumentasi OpenAPI publik dimatikan.
- **Hak akses Windows:**
  - Gateway berjalan sebagai **LocalService** (bukan SYSTEM), karena folder project bisa diedit akun lain.
  - `D:\ollama` (dijalankan sebagai SYSTEM) hanya bisa diubah Administrator.
  - `.env`, `data\`, dan `ngrok.yml` hanya bisa dibaca SYSTEM, Administrators, dan akun installer.
- **Log tidak pernah memuat** header Authorization, isi request/prompt, maupun API key.
- **Repo ini publik:** `.env`, `data\`, dan `logs\` ada di `.gitignore`. Jangan menulis domain ngrok, token, atau key di file yang di-commit.
- **Diketahui dan diterima:**
  - ngrok (seperti semua layanan tunnel) membuka HTTPS di servernya, jadi dapat melihat lalu lintas.
  - Paket gratis ngrok tidak punya WAF.
  - Program lokal di PC bisa memakai Ollama 11434 tanpa password (bawaan Ollama).

## Konfigurasi (`.env`)

Daftar lengkap ada di `.env.example`. Yang paling sering disentuh:

| Setting | Default | Fungsi |
|---|---|---|
| `ADMIN_KEY` | (dibuat installer) | Kunci admin API & dashboard |
| `PUBLIC_BASE_URL` | (diisi `install-ngrok.ps1`) | URL publik yang ditampilkan di dashboard |
| `DEFAULT_RPM` / `DEFAULT_MAX_CONCURRENT` | 60 / 2 | Batas default per key (bisa diubah per key) |
| `GLOBAL_CONCURRENCY` | 4 | Slot GPU; samakan dengan `OLLAMA_NUM_PARALLEL` |
| `QUEUE_TIMEOUT_S` | 20 | Lama antre sebelum `503 server_busy` |
| `MAX_TOKENS_CAP` | 2048 | Batas maksimal `max_tokens` |
| `DEFAULT_REASONING_EFFORT` | `none` | Mode berpikir default untuk model "thinking" (`none`/`medium`/`high`/kosong) |
| `SEED_MODELS` | `gemma3:4b` | Model yang otomatis masuk allowlist saat start pertama |
| `CLIENT_IP_HEADERS` | `cf-connecting-ip,x-forwarded-for` | Header IP asli client dari tunnel |
| `WATCH_SERVICES` | `OllamaServe,Cloudflared` (diganti `OllamaServe,NgrokTunnel` oleh `install-ngrok.ps1`) | Service yang dipantau health monitor |
| `LOG_RETENTION_DAYS` / `USAGE_RETENTION_DAYS` | 30 / 90 | Lama penyimpanan log / usage |

Setelah mengubah `.env`, jalankan `Restart-Service OllamaGateway`. Env var milik service (mis. `LOG_CONSOLE=false`) menang atas `.env`.

## Troubleshooting

| Gejala | Penyebab / solusi |
|---|---|
| URL publik tidak merespons | Cek `Get-Service NgrokTunnel, OllamaGateway, OllamaServe` dan `D:\ollama\logs\ngrok\`. Bisa karena kuota ngrok habis atau authtoken dicabut |
| Client browser menerima halaman HTML ngrok | Paket gratis; kirim header `ngrok-skip-browser-warning: 1` |
| Jawaban `qwen3.5:4b` kosong (`finish_reason: length`) | Mode berpikir menghabiskan token. Pastikan `DEFAULT_REASONING_EFFORT=none`; bila client menyalakan berpikir, beri `max_tokens` besar |
| `/v1/completions` ke qwen ditolak 400 | Disengaja (Ollama mengembalikan teks kosong untuk model berpikir); pakai `/v1/chat/completions` |
| Request pertama lambat (~4 detik) setelah ganti model | Normal: hanya satu model muat di VRAM |
| Respons `qwen3.5:4b` lambat atau `503 server_busy` saat ramai | qwen hanya memproses 1 request sekaligus, sisanya mengantre. Arahkan beban paralel ke `gemma3:4b` |
| Request pertama sangat lambat (~15–40 detik) setelah update driver/install | Kompilasi kernel CUDA untuk akun SYSTEM, sekali per model dan ukuran prompt (cache di `C:\Windows\System32\config\systemprofile\AppData\Roaming\NVIDIA\ComputeCache`) |
| VRAM penuh padahal hanya satu model | Runner `llama-server` yatim. `Get-Process llama-server` (harus 1); bersihkan dengan `. .\scripts\common.ps1; Remove-OrphanOllamaRunners` |
| Model tidak 100% GPU (`/admin/status`) | Turunkan `OLLAMA_NUM_PARALLEL` / context (`install-ollama.ps1 -SkipDownload -NumParallel 2`), lalu samakan `GLOBAL_CONCURRENCY`; atau pakai model lebih kecil |
| API mati beberapa menit tengah malam | Windows Update me-restart PC. Atur *Active hours* |
| SSH tidak bisa masuk setelah restart manual | Tailscale menunggu akun "User" login. Aktifkan *Run unattended* (lihat [Akses jarak jauh](#akses-jarak-jauh--restart-dari-jauh)) |
| Port 11434 bentrok setelah login | Tray app Ollama hidup lagi. Pastikan tidak ada `Ollama.lnk` di folder Startup akun "User" |
| `git` menolak repo ("dubious ownership") | `git config --global --add safe.directory D:/ML/ollama-provider` |
| Cloudflare: 524 / client menerima HTML (bila memakai opsi b) | Request non-stream terlalu lama (turunkan `MAX_TOKENS_CAP` atau pakai stream) / Bot Fight Mode atau Browser Integrity Check masih aktif |

## Development

```powershell
uv sync                                    # .venv + dependency (termasuk pytest, respx)
copy .env.example .env                     # isi ADMIN_KEY: python -m gateway.cli gen-admin-key
uv run pytest                              # test (Ollama di-mock, tanpa jaringan)
```

Service produksi sudah memakai port 8000/8001, jadi instance dev harus memakai port, DB, dan log lain:

```powershell
$env:PUBLIC_PORT='18000'; $env:ADMIN_PORT='18001'; $env:DB_PATH="$env:TEMP\dev.db"; $env:LOG_DIR="$env:TEMP\dev-logs"; $env:ADMIN_KEY='dev'
uv run python -m gateway                   # Ctrl+C untuk berhenti
```

Perubahan kode langsung aktif di produksi setelah `Restart-Service OllamaGateway`, karena service menjalankan source dari folder ini.

### Dokumentasi untuk AI agent

Konteks lengkap untuk AI coding agent ada di folder `.agents/` (`CLAUDE.md` memuatnya otomatis untuk Claude Code):

| File | Isi |
|---|---|
| `memory.md` | ringkasan, jebakan teknis, aturan |
| `architecture.md` | arsitektur |
| `database.md` | database |
| `conventions.md` | konvensi kode |
| `decisions.md` | keputusan teknis beserta alasannya |
| `commands.md` | perintah |
| `features.md` | status fitur |
| `operations.md` | runbook, insiden, pekerjaan tertunda |
