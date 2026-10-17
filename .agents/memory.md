# Project Memory: ollama-provider

> **Baca file ini sebelum mengubah apa pun.** Ini ringkasan tertinggi. Detail ada di file lain di `.agents/` (lihat [Dokumentasi lain](#dokumentasi-lain)).
> Terakhir diperbarui: 2026-09-27. Semua isi berasal dari kode di repository, kecuali bagian yang ditandai *(observasi mesin)*.

## Ringkasan project

| | |
|---|---|
| Nama | `ollama-provider` (`pyproject.toml`) |
| Tujuan | Menyajikan model Ollama lokal sebagai **API OpenAI-compatible** yang bisa diakses dari internet, dengan API key per client, rate limit, usage log, admin API, dan log pemantauan |
| Jenis | Backend API gateway / reverse proxy (tanpa frontend) |
| Target pengguna | Aplikasi client yang memakai SDK OpenAI, LangChain, atau curl (README). Identitas pengguna akhir: *Unknown - information not available in repository* |
| Bahasa | Python 3.11 (gateway), PowerShell 5.1 (script deploy) |
| Framework | FastAPI 0.141 / Starlette 1.7, uvicorn 0.54, httpx 0.28, pydantic-settings 2.15 |
| Database | SQLite (via aiosqlite 0.22), file `data/gateway.db` |
| Upstream | Ollama (`http://127.0.0.1:11434`), memakai endpoint OpenAI-compat `/v1/*` dan native `/api/*` |
| Infrastruktur | Windows 11, GPU NVIDIA (RTX 4060 Ti 8 GB), Windows Service via **WinSW v2.12**, Cloudflare Tunnel |
| Package manager | `uv` (`uv.lock`) |
| Test | pytest + pytest-asyncio (`asyncio_mode = "auto"`) + respx, **74 test** |
| Bahasa dokumentasi | Indonesia (README, `.agents/`), komentar kode dalam Bahasa Inggris |

## Arsitektur singkat

```
Client --HTTPS--> Cloudflare edge --> cloudflared [service Cloudflared]
  --> 127.0.0.1:8000  app publik  (/v1/*, /health)   } satu proses Python, dua uvicorn.Server
      127.0.0.1:8001  app admin   (/admin/*)         } [service OllamaGateway]
  --> 127.0.0.1:11434 ollama serve [service OllamaServe]
```

- Kedua app berbagi satu objek `Gateway` (`gateway/core.py`): DB, client Ollama, rate limiter, gate konkurensi GPU, cache key, pull manager, dan health monitor.
- Port 8001 dan 11434 **tidak pernah** diekspos lewat tunnel.
- Detail lengkap: [architecture.md](architecture.md).

## Status deployment *(observasi mesin, 2026-09-27)*

| Komponen | Status |
|---|---|
| `OllamaServe` (Ollama 0.34.4, `D:\ollama\bin`, model di `D:\ollama\models`) | Terpasang, Running, Automatic |
| `OllamaGateway` | Terpasang, Running, Automatic |
| `NgrokTunnel` | Terpasang 2026-10-16, Running, Automatic. Domain statis gratis (`*.ngrok-free.dev`) ada di `D:\ollama\ngrok\ngrok.yml`; **sengaja tidak ditulis di repo publik** |
| `Cloudflared` | **Belum terpasang**. Alternatif berbayar (butuh domain); script tetap tersedia |
| Model | `gemma3:4b` saja (100% GPU, context 8192) |
| Git | Branch `main`, remote publik `github.com/victoriavals/ollama-provider`. Agent Codex juga bekerja di repo ini: cek `.git/*.lock` dan proses `codex` sebelum operasi git |
| Tray app Ollama (milik user Windows "User") | Autostart dimatikan (`Ollama.lnk` → `Ollama.lnk.disabled`), tidak di-uninstall |

## Pengetahuan kritis (jebakan yang pernah terjadi)

1. **Satu proses, satu worker.** Rate limit, antrean GPU, dan cache key disimpan di memori (`limits.py`, `auth.py`). Jangan menambah `--workers` atau menjalankan lebih dari satu instance di port yang sama.
2. **Jangan memakai `BaseHTTPMiddleware` atau `GZipMiddleware`**, karena keduanya membuffer streaming SSE. Middleware harus pure ASGI (lihat `gateway/middleware.py`).
3. **Streaming:** header 200 dikirim segera, disusul ping `: ping` setiap 15 detik saat antre (menghindari 524 Cloudflare). Error yang terjadi setelah header terkirim dikirim sebagai `data: {"error":...}`. Status sebenarnya dicatat lewat `request.state.access["status"]`.
4. **Slot konkurensi wajib dilepas.** Semua jalur request generasi melewati `Finalizer` (`proxy.py`), yang idempoten dan melepas slot key + slot GPU serta mencatat usage. `ClosingStreamingResponse` memastikan generator di-`aclose()` saat client putus. Endpoint generasi baru harus memakai pola yang sama.
5. **Service gateway menjalankan base Python** (`C:\Program Files\Python311\python.exe -m gateway`) dengan `PYTHONPATH=<project>;<project>\.venv\Lib\site-packages`, **bukan** `.venv\Scripts\python.exe`. Launcher venv membuat proses kedua yang merusak stop/restart service. Akibatnya file `.pth` di venv tidak diproses (saat ini hanya ada `_virtualenv.pth`, tidak dibutuhkan). Service gateway berjalan sebagai **`NT AUTHORITY\LocalService`** (bukan SYSTEM), karena folder project bisa diedit akun lain (termasuk sandbox Codex). Akun di-set lewat `Win32_Service.Change` (WinSW v2 mengabaikan `<serviceaccount><username>`, dan `sc.exe config obj= ... password= ''` gagal di PS 5.1).
6. **Wrapper service adalah WinSW, bukan NSSM.** NSSM 2.24 di Windows 11 ini macet di `STOP_PENDING` dan tidak me-restart app yang crash. Di script, hentikan service dengan `Stop-ServiceSafe` (`scripts/common.ps1`), jangan `Stop-Service -Force` (bisa menggantung).
7. **`OllamaGateway` bergantung pada `OllamaServe`.** SCM menolak menghentikan `OllamaServe` selama gateway masih jalan, jadi hentikan gateway dulu.
8. **Env var menang atas `.env`** (pydantic-settings). Service men-set `LOG_CONSOLE=false`. `.env` dan `data/` di-ACL hanya untuk SYSTEM, Administrators, dan akun yang menjalankan installer. LocalService mendapat: baca `.env` dan kode, Modify di `data/` dan `logs/`. `D:\ollama` dikunci (`Protect-AdminOnlyTree`): hanya SYSTEM/Administrators yang bisa menulis, karena isinya dijalankan sebagai SYSTEM.
9. **Cache key 30 detik.** Revoke lewat admin API langsung berlaku (`gw.keys.clear()`), sedangkan lewat CLI baru berlaku paling lambat 30 detik.
10. **Capability model diambil dari `/api/show`**, bukan `/api/tags` (tags tidak memuat `vision`). Cache-nya 10 menit (`ollama.py`).
11. **Keanehan Ollama:** stream `/v1/completions` menyertakan `usage` bernilai 0 di setiap chunk. Usage sebenarnya ada di chunk terakhir dengan `choices: []`. Gateway selalu meminta `include_usage` ke upstream, lalu membuang chunk usage kalau client tidak memintanya.
12. **Windows:** gunakan `127.0.0.1`, jangan `localhost` (localhost mencoba IPv6 dulu). Script PowerShell harus **ASCII-only** (PS 5.1 membaca file tanpa BOM sebagai ANSI).
13. **Inferensi CUDA pertama untuk akun SYSTEM** setelah install atau upgrade driver bisa memakan sekitar 30 detik (sekali saja, lalu ter-cache). Setelah itu, restart Ollama hanya menambah sekitar 3 detik, dan biaya itu sudah dibayar oleh warm-up (inferensi 1 token).
14. **Folder repo dimiliki user Windows "User"**, sedangkan shell berjalan sebagai "dev". Git butuh `safe.directory` (sudah ditambahkan di config global user dev).
15. **Pengelompokan usage per hari memakai tanggal UTC** (`substr(ts,1,10)`), sedangkan log file memakai waktu lokal (WIB, +07:00).
16. **Jangan `Stop-Process` langsung pada `ollama.exe`.** Runner anaknya (`llama-server.exe`) tertinggal sebagai proses yatim dan tetap memegang VRAM (pernah 2 runner yatim memakan ~2.2 GB). Hentikan lewat service (`Stop-ServiceSafe OllamaServe`, yang otomatis memanggil `Remove-OrphanOllamaRunners`).

## Aturan wajib untuk AI Agent

1. Selalu membaca `.agents/memory.md` sebelum melakukan perubahan.
2. Memahami arsitektur ([architecture.md](architecture.md)) sebelum menambahkan fitur.
3. Mengikuti pola yang sudah ada ([conventions.md](conventions.md)).
4. Tidak menambah dependency baru tanpa alasan. Kalau terpaksa, pakai `uv add`, lalu restart service `OllamaGateway`.
5. Tidak melakukan refactor besar tanpa analisis dampak, terutama pada `proxy.py`, `limits.py`, `middleware.py`, dan script service.
6. Tidak menghapus fitur tanpa konfirmasi pemilik project.
7. Menjaga backward compatibility: format API OpenAI, format error, nama field log JSON, schema DB, dan nama service Windows.
8. Memperhatikan keamanan: jangan pernah mencatat header, body, atau API key, dan jangan mengekspos port 8001/11434. Lihat [conventions.md#aturan-keamanan](conventions.md#aturan-keamanan).
9. Bertanya jika informasi tidak tersedia. Jangan menebak domain, akun Cloudflare, atau identitas client.
10. Memperbarui dokumentasi (`.agents/`, README) ketika terjadi perubahan besar. Tambahkan entri di [decisions.md](decisions.md) untuk keputusan teknis baru.

Aturan operasional tambahan:
- Menginstal/menghapus service, menghentikan proses milik user lain, atau mengubah ACL **harus dikonfirmasi pemilik** dulu.
- Jalankan `uv run pytest` setelah perubahan kode. Semua test harus hijau.
- Jangan commit atau push kecuali diminta.

## File penting

| File | Peran | Catatan |
|---|---|---|
| `gateway/proxy.py` | Forward JSON & SSE ke Ollama, slot, usage | **Paling kritis**, ubah dengan sangat hati-hati |
| `gateway/apps/public.py` | Route publik `/v1/*`, alur `_generate` | Semua validasi terjadi sebelum slot diambil |
| `gateway/apps/admin.py` | Admin API (port 8001) + audit log | |
| `gateway/core.py` | Container `Gateway`, `PullManager`, warm-up, prune | |
| `gateway/limits.py` | RPM, gagal-auth per IP, konkurensi per key & global | State in-memory |
| `gateway/sanitize.py` | Allowlist field & validasi request | Menjaga agar client tidak bisa mengubah VRAM/context |
| `gateway/middleware.py` | Request id + access log JSON | Pure ASGI |
| `gateway/health.py` | Health monitor dependency | |
| `gateway/logs.py` | Log file harian | Tanpa rename (aman di-tail di Windows) |
| `gateway/db.py` | Schema, query, `UsageWriter` | Satu-satunya tempat SQL |
| `gateway/config.py` | Semua setting (env / `.env`) | |
| `scripts/common.ps1` | Helper deploy (WinSW, Stop-ServiceSafe, download) | Dipakai semua script |
| `.env` | Secret (`ADMIN_KEY`) + konfigurasi | **Sensitif**, gitignored, ber-ACL |
| `data/gateway.db` | Hash API key + usage | **Sensitif**, gitignored |
| `D:\ollama\services\*.xml` | Definisi service WinSW (dibuat script, di luar repo) | Jangan diedit manual; jalankan ulang script install |

## Pedoman pengembangan

- Logika bisnis ditempatkan di modul `gateway/*.py`, sedangkan route di `gateway/apps/*` dibuat tipis.
- Semua error ke client dilempar lewat `APIError` (format OpenAI), bukan `HTTPException`.
- I/O blocking (subprocess, disk, ctypes) dijalankan lewat `asyncio.to_thread`.
- Setting baru ditambahkan ke `Settings` (`config.py`) **dan** `.env.example`.
- Test baru memakai fixture di `tests/conftest.py` (Ollama di-mock lewat respx, tanpa jaringan).
- Perubahan pada script service: parse-check dengan PowerShell parser, pastikan ASCII-only, dan uji stop/start/kill.

## Dokumentasi lain

| File | Isi |
|---|---|
| [architecture.md](architecture.md) | Alur request, komponen, API, integrasi eksternal, aturan arsitektur |
| [database.md](database.md) | Schema SQLite, tabel, index, strategi migrasi, aturan DB |
| [conventions.md](conventions.md) | Penamaan, struktur, error handling, keamanan, performa, test, PowerShell |
| [decisions.md](decisions.md) | Technical Decision Records |
| [commands.md](commands.md) | Perintah install, dev, test, DB, deploy, debugging |
| [features.md](features.md) | Daftar fitur beserta status dan lokasi |
| `../README.md` | Panduan pengguna/operator (Indonesia) |
| `../CLAUDE.md` | Dimuat otomatis oleh Claude Code; mengimpor file ini lewat `@.agents/memory.md` dan menunjuk ke dokumen di atas |
