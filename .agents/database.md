# Database

## Overview

| | |
|---|---|
| Engine | SQLite 3 (modul `sqlite3` bawaan Python lewat **aiosqlite**) |
| File | `data/gateway.db` (setting `DB_PATH`; path relatif di-resolve terhadap root project) |
| Koneksi | Satu koneksi `aiosqlite` bersama per proses (`Database` di `gateway/db.py`); query diserialisasi. CLI membuka koneksinya sendiri |
| PRAGMA | `journal_mode=WAL`, `busy_timeout=5000`, `synchronous=NORMAL` (di-set tiap `connect()`) |
| Schema | Konstanta `SCHEMA` di `gateway/db.py`, dieksekusi setiap start (`CREATE ... IF NOT EXISTS`) |
| ORM | Tidak ada. SQL mentah dengan parameter `?`, hasil `aiosqlite.Row` → `dict` |
| Akses file | ACL folder `data/`: SYSTEM, Administrators, dan akun installer saja (`scripts/install-gateway.ps1`) |

## Main tables

| Table | Purpose | Relationship | Important Fields |
|---|---|---|---|
| `api_keys` | API key client (hanya hash) | Direferensikan oleh `usage_logs.key_id` (logis, **tanpa** FK constraint) | `key_hash` (UNIQUE, SHA-256 hex), `prefix` (10 karakter pertama key), `rpm_limit`, `max_concurrent` (NULL = default dari setting), `revoked_at` (soft delete), kebijakan per key: `daily_token_limit`, `monthly_token_limit`, `expires_at`, `allowed_models`, `priority` |
| `models` | Allowlist model yang boleh dipakai client | Berdiri sendiri. `usage_logs.model` menyimpan nama model tanpa FK | `name` (PK, mis. `gemma3:4b`), `enabled` (0/1) |
| `usage_logs` | Satu baris per request generasi (sukses maupun ditolak setelah auth) | `key_id` → `api_keys.id` | `endpoint` (`chat`/`completions`/`embeddings`), `status_code` (termasuk 499), token, `latency_ms`, `stream`, `client_ip` |
| `prompt_logs` | Log prompt & jawaban untuk audit (sejak 2026-11-03): satu baris per request generasi yang body-nya terbaca, dari key dengan `log_prompts=1` | `key_id` → `api_keys.id` (logis); `key_name` disalin agar tetap terbaca bila key diganti nama | `ts` (waktu **selesai**, UTC), `request_id`, `client_ip`, `end_user` (field `user` dari client), `endpoint`, `model`, `stream`, `status_code`, `total_tokens`, `prompt` (JSON `[{role, content}]`, sudah dipotong/disamarkan), `prompt_preview` (≤ 200 karakter, pesan user terakhir), `prompt_chars` (panjang asli), `response` (teks jawaban + tool call, dipotong) |

### `api_keys`

| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `name` | TEXT NOT NULL | Label client (1–100 karakter, divalidasi di admin API) |
| `key_hash` | TEXT NOT NULL UNIQUE | `sha256(key).hexdigest()`. Plaintext **tidak pernah** disimpan |
| `prefix` | TEXT NOT NULL | `key[:10]`, mis. `sk-5iQF4RH`, untuk ditampilkan dan di log |
| `rpm_limit` | INTEGER NULL | NULL → `DEFAULT_RPM` |
| `max_concurrent` | INTEGER NULL | NULL → `DEFAULT_MAX_CONCURRENT` |
| `created_at` | TEXT NOT NULL | ISO-8601 UTC, presisi detik |
| `last_used_at` | TEXT NULL | Diperbarui oleh `UsageWriter` per batch |
| `revoked_at` | TEXT NULL | Terisi = key tidak aktif (lookup memakai `revoked_at IS NULL`) |
| `daily_token_limit` | INTEGER NULL | Kuota total token per hari lokal (WIB). NULL = tanpa batas. *Migrasi 2026-10-18* |
| `monthly_token_limit` | INTEGER NULL | Kuota total token per bulan lokal. NULL = tanpa batas. *Migrasi 2026-10-18* |
| `expires_at` | TEXT NULL | Saat pertama key **tidak** berlaku (ISO UTC; tanggal dari dashboard/CLI = 00:00 WIB hari berikutnya). NULL = tidak kedaluwarsa. *Migrasi 2026-10-18* |
| `allowed_models` | TEXT NULL | JSON array nama model (diurutkan, unik). NULL = semua model aktif. `db.py` meng-encode/decode, jadi kode Python melihat `list[str]` atau `None`. *Migrasi 2026-10-18* |
| `priority` | INTEGER NOT NULL DEFAULT 0 | Prioritas antrean GPU (0–9; dashboard memakai 0/1). PATCH `null` → 0. *Migrasi 2026-10-18* |
| `log_prompts` | INTEGER NOT NULL DEFAULT 1 | 0 = prompt & jawaban key ini tidak masuk `prompt_logs`. PATCH `null` → 1. *Migrasi 2026-11-03* |

### `models`

| Kolom | Tipe | Keterangan |
|---|---|---|
| `name` | TEXT PK | Nama model Ollama |
| `enabled` | INTEGER NOT NULL DEFAULT 1 | Hanya model `enabled=1` yang muncul di `/v1/models` dan boleh dipakai |
| `added_at` | TEXT NOT NULL | Dipakai sebagai `created` (epoch) di `/v1/models` |

Seed: `SEED_MODELS` (default `gemma3:4b`) di-insert dengan `INSERT OR IGNORE` setiap start, jadi model seed yang sudah di-disable tetap disabled.

### `usage_logs`

| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `key_id` | INTEGER NULL | ID key (NULL tidak dipakai saat ini; request tanpa auth tidak dicatat di tabel ini) |
| `ts` | TEXT NOT NULL | ISO-8601 UTC |
| `request_id` | TEXT | Sama dengan header `x-request-id` dan field access log |
| `endpoint` | TEXT NOT NULL | `chat` / `completions` / `embeddings` |
| `model` | TEXT NULL | NULL jika request ditolak sebelum model terbaca |
| `prompt_tokens`, `completion_tokens`, `total_tokens` | INTEGER NULL | NULL jika tidak diketahui (error, 499) |
| `latency_ms` | INTEGER | `0` untuk penolakan sebelum proxy (429/404/400) |
| `status_code` | INTEGER NOT NULL | Status sebenarnya, termasuk 499 (client putus) dan error stream setelah header 200 |
| `stream` | INTEGER NOT NULL DEFAULT 0 | 0/1 |
| `client_ip` | TEXT | Dari header di `CLIENT_IP_HEADERS` (`CF-Connecting-IP`, lalu entri terakhir `X-Forwarded-For`), atau IP socket bila tidak ada |

## Index

| Index | Kolom | Dipakai untuk |
|---|---|---|
| `idx_usage_ts` | `usage_logs(ts)` | Filter rentang waktu, prune retensi |
| `idx_usage_key_ts` | `usage_logs(key_id, ts)` | Ringkasan per key, kuota token (`token_usage_since`, `token_usage_by_key`) |
| `idx_prompt_ts` | `prompt_logs(ts)` | Daftar log prompt per rentang waktu (selalu dibatasi), prune retensi |
| `idx_prompt_key_ts` | `prompt_logs(key_id, ts)` | Filter log prompt per key |
| (implisit) | `api_keys.key_hash` UNIQUE | Lookup auth |
| (implisit) | `models.name` PK | Cek allowlist |

## Entity relationship

```
api_keys (1) ──< usage_logs (N)      via usage_logs.key_id = api_keys.id   (tanpa FK constraint)
models                                berdiri sendiri; usage_logs.model menyimpan nama sebagai teks
```

`usage_summary()` memakai `LEFT JOIN api_keys` agar baris usage tetap muncul walau key-nya tidak ada.

## Data flow

1. **Auth**: `Gateway.lookup_key()` → `KeyCache` (TTL 30 dtk, termasuk hasil negatif) → `get_active_key_by_hash()`.
2. **Allowlist**: `is_model_enabled()` dipanggil setiap request generasi (tanpa cache).
3. **Usage**: `UsageWriter.submit()` (sinkron, `put_nowait`) → task background → `insert_usage()` batch ≤200 baris + update `last_used_at` → commit.
4. **Kuota token**: `QuotaTracker` (`quota.py`) mengisi total hari/bulan per key dari `token_usage_since()` saat pertama dicek di suatu periode, lalu menambah di memori lewat `Finalizer` (tidak menunggu `UsageWriter`). Daftar key di admin memakai `token_usage_by_key(day_start, month_start)` (satu query, `SUM(CASE ...)`). Batas periode = 00:00 / tanggal 1 waktu lokal mesin, dikonversi ke UTC ISO agar bisa dibandingkan dengan `usage_logs.ts`.
5. **Retensi**: `prune_usage(USAGE_RETENTION_DAYS)` dan `prune_prompts(PROMPT_LOG_RETENTION_DAYS)` saat start lalu setiap 24 jam.
   **Log prompt**: `PromptRecord` dibuat di `_generate`, dilengkapi `Finalizer`, lalu ditulis lewat `UsageWriter` yang sama (`insert_prompts`).
   - `list_prompts()` selalu memakai rentang `ts` + `LIMIT` dan hanya mengembalikan cuplikan; `get_prompt()` memuat isi lengkap satu baris.
   - Keduanya berjalan di **koneksi read-only kedua** (`Database._reader`, `mode=ro`, `mmap_size` 256 MB). Dengan WAL, koneksi itu paralel dengan koneksi utama, sehingga pencarian berat tidak menunda query request API.
   - Kursor halaman ditulis `ts <= ? AND (ts < ? OR id < ?)`, agar tetap satu range scan index (dijaga oleh test `EXPLAIN QUERY PLAN`).
   - Pengukuran pada 50 ribu baris (179 MB): halaman 1–2 ms, pencarian terburuk 30 hari ~0,2 dtk.
   - `PRAGMA secure_delete=FAST` di koneksi utama, supaya baris yang sudah di-prune tidak tertinggal di halaman file.
   - **File backup (`data/backups/`) ikut memuat `prompt_logs`** dan tidak dipangkas otomatis: hapus backup lama sesuai janji retensi 30 hari.
6. **Backup**: `POST /admin/backup` → `VACUUM INTO data/backups/gateway-YYYYmmdd-HHMMSS.db` (manual, belum dijadwalkan).

## Migration strategy

- **Tanpa tool migrasi.** Schema dasar dibuat oleh `Database.connect()` dengan `CREATE TABLE/INDEX IF NOT EXISTS`.
- Kolom yang ditambahkan setelah rilis pertama didaftarkan di `KEY_MIGRATIONS` (`db.py`). `Database._migrate()` membaca `PRAGMA table_info(api_keys)` dan menjalankan `ALTER TABLE api_keys ADD COLUMN ...` untuk kolom yang belum ada, lalu mencatat `database migrated: api_keys.<kolom> added` di log. Ini berlaku untuk DB baru maupun lama; data lama tetap utuh.
- Kolom baru harus aditif: nullable atau punya `DEFAULT`. Tambahkan juga ke `KEY_UPDATABLE` bila boleh diubah lewat `update_key`.
- **Migrasi berjalan di setiap proses yang membuka DB**, termasuk `python -m gateway.cli`. Lakukan backup (`POST /admin/backup`) sebelum men-deploy kode yang menambah kolom, dan sebelum menjalankan CLI versi baru.
- Kode lama tetap bisa membaca DB yang sudah dimigrasi, karena semua `SELECT` memakai daftar kolom eksplisit (`KEY_COLUMNS`). Rollback kode tidak perlu menghapus kolom.

## Database rules

1. Semua SQL hanya ada di `gateway/db.py`. Route/modul lain memanggil method `Database`.
2. Selalu gunakan parameter `?`. Nama kolom dinamis hanya boleh dari whitelist (contoh: `update_key` memfilter dengan `KEY_UPDATABLE`, `usage_summary` memakai dict `GROUP_BY`).
3. Timestamp disimpan sebagai TEXT ISO-8601 **UTC** lewat `utcnow()` / `days_ago()`. Pengelompokan `group_by=day` memakai tanggal UTC.
4. Jangan pernah menyimpan API key plaintext. Jangan hard-delete `api_keys`; pakai `revoked_at`.
5. Setelah mengubah key di DB dari dalam proses gateway, panggil `gw.keys.clear()`.
6. Penulisan usage tidak boleh di jalur request secara sinkron; gunakan `UsageWriter.submit()`.
7. Perubahan schema bersifat aditif (kolom baru nullable/berdefault) demi backward compatibility dengan DB yang sudah ada dan CLI.
8. File DB dan `-wal`/`-shm` bersifat sensitif (hash key, IP client) dan tidak boleh di-commit (`data/` ada di `.gitignore`).

## Isi saat ini *(observasi mesin, 2026-10-18)*

- `api_keys`: 9 baris. **1 key aktif** (milik pemilik, dipakai dari luar lewat ngrok); 8 lainnya key uji agent (`smoke-test*`, `security-probe`, `acl-check*`, `public-check`, `upgrade-check`, `model-e2e`) yang sudah dicabut. Agent membuat key uji sementara lalu langsung mencabutnya, dan pola itu dipertahankan.
- `models`: `gemma3:4b` (enabled) dan `qwen3.5:4b` (enabled).
- `usage_logs`: ~40 baris (uji agent + pemakaian awal pemilik).
