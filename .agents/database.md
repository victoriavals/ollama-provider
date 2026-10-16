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
| `api_keys` | API key client (hanya hash) | Direferensikan oleh `usage_logs.key_id` (logis, **tanpa** FK constraint) | `key_hash` (UNIQUE, SHA-256 hex), `prefix` (10 karakter pertama key), `rpm_limit`, `max_concurrent` (NULL = default dari setting), `revoked_at` (soft delete) |
| `models` | Allowlist model yang boleh dipakai client | Berdiri sendiri. `usage_logs.model` menyimpan nama model tanpa FK | `name` (PK, mis. `gemma3:4b`), `enabled` (0/1) |
| `usage_logs` | Satu baris per request generasi (sukses maupun ditolak setelah auth) | `key_id` → `api_keys.id` | `endpoint` (`chat`/`completions`/`embeddings`), `status_code` (termasuk 499), token, `latency_ms`, `stream`, `client_ip` |

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
| `idx_usage_key_ts` | `usage_logs(key_id, ts)` | Ringkasan per key |
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
4. **Retensi**: `prune_usage(USAGE_RETENTION_DAYS)` saat start lalu setiap 24 jam.
5. **Backup**: `POST /admin/backup` → `VACUUM INTO data/backups/gateway-YYYYmmdd-HHMMSS.db` (manual, belum dijadwalkan).

## Migration strategy

- **Tidak ada tool migrasi.** Schema dibuat oleh `Database.connect()` dengan `CREATE TABLE/INDEX IF NOT EXISTS`.
- `IF NOT EXISTS` **tidak mengubah tabel yang sudah ada**. Perubahan kolom pada DB yang sudah berjalan harus memakai `ALTER TABLE` eksplisit (mis. dicek lewat `PRAGMA table_info` di `connect()`).
- Lakukan backup (`POST /admin/backup`) sebelum mengubah schema di mesin produksi.

## Database rules

1. Semua SQL hanya ada di `gateway/db.py`. Route/modul lain memanggil method `Database`.
2. Selalu gunakan parameter `?`. Nama kolom dinamis hanya boleh dari whitelist (contoh: `update_key` memfilter `{"name","rpm_limit","max_concurrent"}`, `usage_summary` memakai dict `GROUP_BY`).
3. Timestamp disimpan sebagai TEXT ISO-8601 **UTC** lewat `utcnow()` / `days_ago()`. Pengelompokan `group_by=day` memakai tanggal UTC.
4. Jangan pernah menyimpan API key plaintext. Jangan hard-delete `api_keys`; pakai `revoked_at`.
5. Setelah mengubah key di DB dari dalam proses gateway, panggil `gw.keys.clear()`.
6. Penulisan usage tidak boleh di jalur request secara sinkron; gunakan `UsageWriter.submit()`.
7. Perubahan schema bersifat aditif (kolom baru nullable/berdefault) demi backward compatibility dengan DB yang sudah ada dan CLI.
8. File DB dan `-wal`/`-shm` bersifat sensitif (hash key, IP client) dan tidak boleh di-commit (`data/` ada di `.gitignore`).

## Isi saat ini *(observasi mesin, 2026-09-27)*

- `api_keys`: 2 baris (key uji `smoke-test`, `smoke-test-2`, keduanya sudah di-revoke). Belum ada key client produksi.
- `models`: `gemma3:4b` (enabled).
- `usage_logs`: 4 baris dari uji end-to-end.
