# CLAUDE.md

Gateway API OpenAI-compatible (FastAPI) di depan Ollama lokal. Berjalan sebagai Windows Service (WinSW) di mesin ini dan diekspos ke internet lewat Cloudflare Tunnel. Balas dan tulis dokumentasi dalam Bahasa Indonesia; komentar kode dalam Bahasa Inggris.

## Memory project (wajib)

@.agents/memory.md

Baca dokumen `.agents/` lain sebelum menyentuh area terkait:

| Sebelum... | Baca |
|---|---|
| menambah endpoint/fitur, atau mengubah `proxy.py`, `limits.py`, `middleware.py` | [.agents/architecture.md](.agents/architecture.md) |
| mengubah schema atau query SQLite | [.agents/database.md](.agents/database.md) |
| menulis kode, test, atau script PowerShell | [.agents/conventions.md](.agents/conventions.md) |
| mengganti keputusan yang sudah ada (WinSW vs NSSM, base Python vs launcher venv, winget, format log, ...) | [.agents/decisions.md](.agents/decisions.md) |
| menjalankan dev/test/deploy/debug | [.agents/commands.md](.agents/commands.md) |
| menilai status suatu fitur | [.agents/features.md](.agents/features.md) |

## Perintah cepat

```powershell
uv run pytest                                              # wajib hijau setelah perubahan kode
Restart-Service OllamaGateway                              # deploy perubahan kode gateway (shell admin)
powershell -ExecutionPolicy Bypass -File scripts\status.ps1   # cek service, dependency, log terbaru
```

Instance dev harus memakai port, DB, dan log lain, karena service produksi memegang 8000/8001 (lihat `.agents/commands.md#development`).

## Sebelum menyelesaikan tugas

- `uv run pytest` hijau. Untuk perubahan script PowerShell, lakukan parse-check dan pastikan ASCII-only.
- Perubahan besar → perbarui `.agents/` (dan `README.md` bila berdampak ke operator). Keputusan teknis baru → tambahkan entri di `.agents/decisions.md`.
- Jangan commit/push kecuali diminta. Jangan memasang/menghapus service, menghentikan proses milik user lain, atau mengubah ACL tanpa konfirmasi pemilik.
