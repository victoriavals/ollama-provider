# ollama-provider

Gateway API **OpenAI-compatible** di depan Ollama lokal, dengan API key per client, rate limit, usage log, dan admin API. Semua komponen berjalan sebagai Windows Service dan diekspos ke internet lewat Cloudflare Tunnel.

```
Client (SDK OpenAI) --HTTPS--> Cloudflare (WAF, TLS) --> cloudflared [service]
   --> 127.0.0.1:8000  gateway publik  (/v1/*, /health)      } satu proses Python
       127.0.0.1:8001  gateway admin   (/admin/*, lokal saja) } [service OllamaGateway]
   --> 127.0.0.1:11434 ollama serve [service OllamaServe]
```

Port 8001 dan 11434 tidak pernah dibuka lewat tunnel.

## Hardware & kapasitas

| | |
|---|---|
| CPU | Intel i5-13400F (10C/16T) |
| GPU | RTX 4060 Ti 8 GB VRAM |
| RAM | 32 GB DDR5-5600 |
| Model | `gemma3:4b` (utama) dan `qwen3.5:4b` (tools/JSON), masing-masing ~3 GB dan muat penuh di GPU. Hanya satu yang dimuat sekaligus (Ollama 0.40.5) |

Default: 4 request diproses paralel di GPU (`OLLAMA_NUM_PARALLEL=4` = `GLOBAL_CONCURRENCY=4`). Request berikutnya menunggu maksimal 20 detik, lalu dibalas `503 server_busy`.

## Development

```powershell
uv sync                                    # .venv + dependency
copy .env.example .env                     # isi ADMIN_KEY: python -m gateway.cli gen-admin-key
uv run pytest                              # test (Ollama di-mock)
uv run python -m gateway                   # jalankan (butuh Ollama di 127.0.0.1:11434)
uv run python -m gateway.cli create-key --name dev
```

## Instalasi produksi (Windows Service)

Jalankan dari **PowerShell sebagai Administrator**, di folder project:

```powershell
# 1. Ollama standalone -> D:\ollama, model disalin ke D:\ollama\models, service OllamaServe
powershell -ExecutionPolicy Bypass -File scripts\install-ollama.ps1
#    (opsional) -UninstallTrayApp  untuk menghapus tray app, bukan hanya mematikan autostart-nya
#    Kalau output tidak menunjukkan "100% GPU": ulangi dengan -NumParallel 2 -SkipDownload
#    lalu set GLOBAL_CONCURRENCY=2 di .env

# 2. Gateway -> service OllamaGateway (.env + ADMIN_KEY dibuat otomatis)
powershell -ExecutionPolicy Bypass -File scripts\install-gateway.ps1

# 3. Tunnel ke internet: pilih SATU
#  a) ngrok, gratis tanpa domain (lihat "Tunnel gratis: ngrok" di bawah)
powershell -ExecutionPolicy Bypass -File scripts\install-ngrok.ps1 -AuthToken <TOKEN> -Domain <nama>.ngrok-free.app
#  b) Cloudflare Tunnel, butuh domain di Cloudflare (token dari Zero Trust > Networks > Tunnels)
powershell -ExecutionPolicy Bypass -File scripts\install-cloudflared.ps1 -Token <TOKEN>
#     tambahkan -Http2 jika jaringan memblokir UDP 7844
```

### Tunnel gratis: ngrok

1. Daftar gratis di <https://dashboard.ngrok.com>.
2. Salin **authtoken** (*Getting Started → Your Authtoken*).
3. Klaim **domain statis gratis** (*Universal Gateway → Domains*), misalnya `nama-kamu.ngrok-free.app`. URL ini tetap, tidak berubah saat restart.
4. Jalankan `install-ngrok.ps1` seperti di atas. Script akan:
   - memasang service `NgrokTunnel` (WinSW: auto-start dan restart saat crash);
   - menyimpan token di `D:\ollama\ngrok\ngrok.yml` (ber-ACL);
   - menambahkan `NgrokTunnel` ke health monitor;
   - mengecek `https://<domain>/health` dari sisi publik.

Catatan paket gratis ngrok:
- Ada kuota bulanan (transfer data dan jumlah request). Pantau di dashboard ngrok bagian *Usage*.
- Request dari **browser** akan menampilkan halaman peringatan ngrok terlebih dahulu. SDK dan curl tidak terpengaruh. Client berbasis browser perlu menambahkan header `ngrok-skip-browser-warning: 1`.
- Hanya port 8000 (API publik) yang diekspos. Admin API (8001) dan Ollama (11434) tetap lokal.
- Tidak ada WAF seperti di Cloudflare. Perlindungan bergantung pada API key, rate limit, dan limit gagal-auth per IP di gateway. IP client dibaca dari `X-Forwarded-For` (`CLIENT_IP_HEADERS`).

Kenapa bukan Tailscale Funnel: satu PC hanya bisa aktif di **satu** akun Tailscale pada satu waktu, dan Tailscale di PC ini sudah dipakai akun lain.

### Konfigurasi dashboard Cloudflare (hanya untuk opsi b)

1. **Public hostname** pada tunnel: `llm.<domain>` → Service `HTTP` `127.0.0.1:8000`, Path `^/(v1|health)(/.*)?$`.
2. **WAF custom rule** (Block):
   ```
   (http.host eq "llm.<domain>" and starts_with(http.request.uri.path, "/v1")
    and not any(lower(http.request.headers.names[*])[*] eq "authorization"))
   ```
3. **Rate limiting rule**: misalnya 120 request / 10 detik per IP untuk `llm.<domain>`.
4. **Matikan Bot Fight Mode**, lalu buat Configuration Rule untuk `llm.<domain>` dengan **Browser Integrity Check: Off**. Tanpa ini, SDK akan menerima halaman challenge HTML.

### Pengaturan host

```powershell
powercfg /change standby-timeout-ac 0      # jangan sleep
powercfg /change hibernate-timeout-ac 0
```

Atur juga *Active hours* di Windows Update supaya restart otomatis tidak terjadi saat jam sibuk. Semua service sudah auto-start setelah reboot, tanpa perlu login.

## API key

```powershell
.venv\Scripts\python.exe -m gateway.cli create-key --name client-a --rpm 60 --max-concurrent 2
.venv\Scripts\python.exe -m gateway.cli list-keys
.venv\Scripts\python.exe -m gateway.cli revoke-key 3
.venv\Scripts\python.exe -m gateway.cli usage --days 30 --group-by key
```

Key hanya ditampilkan sekali. Database hanya menyimpan hash SHA-256 dan prefix-nya. Perubahan lewat CLI berlaku paling lambat 30 detik (TTL cache auth). Perubahan lewat admin API berlaku langsung.

## Pemakaian dari client

```bash
curl https://llm.<domain>/v1/chat/completions \
  -H "Authorization: Bearer sk-..." -H "Content-Type: application/json" \
  -d '{"model":"gemma3:4b","messages":[{"role":"user","content":"Halo!"}]}'
```

```python
from openai import OpenAI

client = OpenAI(base_url="https://llm.<domain>/v1", api_key="sk-...")
stream = client.chat.completions.create(
    model="gemma3:4b",
    messages=[{"role": "user", "content": "Jelaskan fotosintesis singkat."}],
    stream=True,
)
for chunk in stream:
    if chunk.choices:
        print(chunk.choices[0].delta.content or "", end="")
```

LangChain: `ChatOpenAI(base_url="https://llm.<domain>/v1", api_key="sk-...", model="gemma3:4b")`.

### Endpoint publik

| Endpoint | Keterangan |
|---|---|
| `GET /v1/models` | model yang di-enable |
| `POST /v1/chat/completions` | stream & non-stream, gambar (`image_url`, maks 2) |
| `POST /v1/completions` | `prompt` string (hanya untuk model tanpa mode berpikir, mis. `gemma3:4b`) |
| `POST /v1/embeddings` | hanya untuk model embedding (gemma3 → 400) |
| `GET /health`, `GET /health/ready` | tanpa auth |

Field yang diteruskan ke Ollama hanya: `model, messages, prompt, input, stream, stream_options, max_tokens` (`max_completion_tokens` dipetakan ke sini), `temperature, top_p, stop, seed, presence_penalty, frequency_penalty, response_format, user`, serta `reasoning_effort` untuk model yang bisa berpikir. `tools` hanya diteruskan jika modelnya mendukung. Field lain (`options`, `keep_alive`, `num_ctx`, `think`, ...) dibuang. `max_tokens` di-cap ke `MAX_TOKENS_CAP` (default 2048) supaya request non-stream selesai sebelum batas 100 detik Cloudflare.

### Memilih model

| Model | Pakai untuk | Catatan |
|---|---|---|
| `gemma3:4b` | Percakapan umum bahasa Indonesia (default) | Bahasa paling natural di uji kami; tanpa tools. Dimuat sejak start |
| `qwen3.5:4b` | Tools/function calling, output JSON terstruktur, gambar | Mode berpikir **mati secara default** (`reasoning_effort: "none"`), sehingga jawaban cepat. Akurasi fakta & nuansa bahasa Indonesia lebih lemah dari gemma3 |

- VRAM hanya cukup untuk satu model; request pertama setelah berpindah model butuh ~4 detik untuk memuat.
- `reasoning_effort` (khusus `qwen3.5:4b`): `none` (default), `medium`, atau `high`. Saat berpikir dinyalakan, beri `max_tokens` besar (mis. 2048), karena token berpikir ikut dihitung. `medium` kadang berpikir sampai batas token dan tidak menjawab; `low` ditolak karena di uji kami selalu begitu.
- `qwen3.5:4b` tidak mendukung `/v1/completions` (Ollama mengembalikan teks kosong untuk model berpikir); gunakan `/v1/chat/completions`.

### Error & limit

| Status | `code` | Penyebab |
|---|---|---|
| 401 | `invalid_api_key` | key tidak ada / salah / di-revoke |
| 404 | `model_not_found` | model tidak di-enable |
| 400 | `model_not_supported`, dll. | request tidak valid |
| 413 | `request_too_large` | body > `MAX_BODY_BYTES` (4 MB) |
| 429 | `rate_limit_exceeded` | melewati RPM key (`Retry-After`) |
| 429 | `concurrency_limit_exceeded` | request paralel per key melewati batas |
| 429 | `auth_rate_limited` | terlalu banyak auth gagal dari satu IP |
| 503 | `server_busy` | semua slot GPU penuh > 20 detik |
| 503 | `upstream_unavailable` | Ollama mati |

Pada streaming, header sudah terkirim duluan (ada ping `: ping` setiap 15 detik saat antre). Karena itu error dikirim sebagai event `data: {"error": {...}}`, dan SDK OpenAI akan me-raise-nya sebagai exception. Setiap respons membawa header `x-request-id` dan `x-ratelimit-*-requests`.

## Dashboard (lokal)

Buka **http://127.0.0.1:8001/admin/ui** di browser **pada PC server**, lalu masuk dengan `ADMIN_KEY` dari `.env`:

```powershell
Select-String '^ADMIN_KEY=' .env
```

Isinya: status dependency + meter slot GPU, pemakaian hari ini, grafik request per jam, cara akses untuk client (URL publik dari `PUBLIC_BASE_URL` di `.env`), kelola API key (buat/ubah batas/cabut), pemakaian per key/model/hari, request terbaru (otomatis diperbarui), serta unduh/aktifkan/hapus model.

Dashboard **tidak bisa dibuka dari internet**: ia berjalan di port admin 8001 yang hanya mendengarkan `127.0.0.1` dan tidak dilewatkan ngrok. Halaman menolak Host header asing (anti DNS rebinding), memakai CSP ketat tanpa script pihak ketiga, dan admin key hanya disimpan di `sessionStorage` tab tersebut. Untuk membukanya dari jauh, pakai remote desktop ke PC server.

## Admin API (lokal, `http://127.0.0.1:8001`)

Header: `Authorization: Bearer <ADMIN_KEY dari .env>`. Dokumentasi interaktif tersedia di `http://127.0.0.1:8001/admin/docs`.

| Endpoint | |
|---|---|
| `POST /admin/keys` `{name, rpm_limit?, max_concurrent?}` | buat key |
| `GET /admin/keys` · `PATCH /admin/keys/{id}` · `DELETE /admin/keys/{id}` | list / ubah / revoke |
| `GET /admin/usage?since=&until=&key_id=&group_by=day\|key\|model` | ringkasan pemakaian |
| `GET /admin/usage/recent?limit=50` | log request terbaru |
| `GET /admin/models` · `PATCH /admin/models/{name}` `{enabled}` | allowlist model |
| `POST /admin/models/pull` `{model, enable}` → `GET /admin/models/pull/{job}` | pull model di background |
| `DELETE /admin/models/{name}` | hapus model |
| `GET /admin/status` | Ollama, model yang sedang dimuat, GPU (nvidia-smi), antrean |
| `POST /admin/backup` | salin DB ke `data\backups\` |

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
| `logs\access-YYYY-MM-DD.log` | **Aktivitas**: satu baris JSON per request (publik & admin), berisi `ip` (IP asli dari Cloudflare), `key`, `key_name`, `endpoint`, `model`, `status`, `duration_ms`, token, `error`. Status yang dicatat adalah hasil sebenarnya: stream yang putus dicatat 499, antrean penuh 503. |
| `logs\gateway-YYYY-MM-DD.log` | Event aplikasi, **audit admin/CLI** (key dibuat/diubah/dicabut, model enable/pull/hapus, backup), auth gagal, transisi health |
| `logs\health-YYYY-MM-DD.log` | **Dependency**: perubahan status (misalnya `service:Cloudflared is down` → `recovered`) + snapshot JSON lengkap tiap 5 menit |
| `logs\OllamaGateway.err.log` | crash / traceback gateway (WinSW) |
| `D:\ollama\logs\OllamaServe.err.log` | log Ollama (load model, error CUDA, request) |
| `D:\ollama\logs\cloudflared\cloudflared.log` | log tunnel (koneksi ke edge Cloudflare) |

Health monitor mengecek setiap 30 detik: Ollama (bisa dihubungi, model 100% di GPU), service `OllamaServe` & `Cloudflared`, GPU (suhu ≥ 85 °C = warn), dan sisa disk (< 10 GB = warn). Setelah Ollama restart, model otomatis di-load ulang. Data yang sama tersedia live di `GET /admin/status`. Log disimpan 30 hari (`LOG_RETENTION_DAYS`), usage di DB 90 hari (`USAGE_RETENTION_DAYS`).

```powershell
# pantau aktivitas live
Get-Content logs\access-$(Get-Date -Format yyyy-MM-dd).log -Wait -Tail 20
# semua request error hari ini
Get-Content logs\access-$(Get-Date -Format yyyy-MM-dd).log | ConvertFrom-Json | Where-Object status -ge 400 | Format-Table
# warning dependency
Select-String logs\health-*.log -Pattern WARNING
```

## Operasional

| | |
|---|---|
| Status service | `Get-Service OllamaServe, OllamaGateway, NgrokTunnel, Cloudflared` |
| Restart | `Restart-Service OllamaGateway` (Ollama: stop gateway dulu, karena gateway bergantung padanya) |
| Upgrade Ollama / ngrok / cloudflared | `scripts\upgrade.ps1 [-Component ollama\|ngrok\|cloudflared]` |
| Hapus service | `scripts\uninstall-services.ps1 [-IncludeNgrok] [-IncludeCloudflared]` |
| Definisi service (WinSW) | `D:\ollama\services\OllamaServe.xml`, `OllamaGateway.xml` |

Service dijalankan dengan [WinSW](https://github.com/winsw/winsw): stop graceful (Ctrl+C, lalu kill setelah timeout) dan restart otomatis saat crash (5 s / 10 s / 30 s). NSSM tidak dipakai karena di Windows 11 ini ia macet di `STOP_PENDING` dan tidak me-restart app yang crash.

Menambah model: `POST /admin/models/pull {"model":"qwen3:8b"}`, lalu `PATCH /admin/models/qwen3:8b {"enabled":true}`. Karena `OLLAMA_MAX_LOADED_MODELS=1`, berpindah model berarti swap VRAM, dan request pertama setelah swap akan lebih lambat.

### Troubleshooting

- **Error 524 dari Cloudflare**: request non-stream terlalu lama. Turunkan `MAX_TOKENS_CAP`, atau minta client memakai `stream: true`.
- **Client menerima HTML, bukan JSON**: Bot Fight Mode atau Browser Integrity Check masih aktif.
- **Model tidak 100% GPU** (`GET /admin/status` → `loaded_models`): turunkan `OLLAMA_NUM_PARALLEL` / `OLLAMA_CONTEXT_LENGTH` (jalankan ulang `install-ollama.ps1 -SkipDownload -NumParallel 2`), lalu samakan `GLOBAL_CONCURRENCY`.
- **Port 11434 bentrok setelah login**: tray app Ollama masih auto-start. Pastikan tidak ada `Ollama.lnk` di folder Startup user.
- **Request pertama setelah install/upgrade driver sangat lambat (~30 detik)**: kompilasi kernel CUDA untuk akun SYSTEM, hanya sekali (cache di `C:\Windows\System32\config\systemprofile\AppData\Roaming\NVIDIA\ComputeCache`).
