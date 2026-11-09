# Database

## Overview

| | |
|---|---|
| Engine | SQLite 3 (modul `sqlite3` bawaan Python lewat **aiosqlite**) |
| File | `data/gateway.db` (setting `DB_PATH`; path relatif di-resolve terhadap root project) |
| Koneksi | Dua koneksi `aiosqlite` per proses (`Database` di `gateway/db.py`): **utama** (`conn`, semua tulis + query request API; query diserialisasi) dan **`_reader`** (read-only, `file:...?mode=ro`, hanya untuk `list_prompts`/`get_prompt` lewat `_read_all`). CLI membuka koneksinya sendiri |
| PRAGMA | Koneksi utama: `journal_mode=WAL`, `busy_timeout=5000`, `synchronous=NORMAL`, `secure_delete=FAST`. Reader: `busy_timeout=5000`, `mmap_size=268435456` (256 MB). Di-set tiap `connect()` |
| Schema | Konstanta `SCHEMA` di `gateway/db.py`, dieksekusi setiap start (`CREATE ... IF NOT EXISTS`), lalu migrasi aditif `KEY_MIGRATIONS` |
| ORM | Tidak ada. SQL mentah dengan parameter `?`, hasil `aiosqlite.Row` → `dict` |
| Akses file | ACL folder `data/`: SYSTEM, Administrators, dan akun installer saja (`scripts/install-gateway.ps1`) |

## Main tables

| Table | Purpose | Relationship | Important Fields |
|---|---|---|---|
| `api_keys` | API key client (hanya hash) | Direferensikan oleh `usage_logs.key_id` (logis, **tanpa** FK constraint) | `key_hash` (UNIQUE, SHA-256 hex), `prefix` (10 karakter pertama key), `rpm_limit`, `max_concurrent` (NULL = default dari setting), `revoked_at` (soft delete), kebijakan per key: `daily_token_limit`, `monthly_token_limit`, `expires_at`, `allowed_models`, `priority`, `log_prompts` |
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

Stream yang berakhir sebelum chunk usage (client putus, upstream error) tetapi sudah menghasilkan output dicatat dengan token **perkiraan**: `prompt_tokens` = `estimate_prompt_tokens(payload)` (jumlah karakter teks / 4 + 1, ditambah 256 per gambar), `completion_tokens` = jumlah chunk berisi output (Ollama ~1 token per chunk). Baris seperti ini tetap masuk kuota.

### `prompt_logs`

Dibuat 2026-11-03 ([decisions.md #29](decisions.md#29-log-prompt--jawaban-untuk-audit)). Ditulis oleh `UsageWriter` (`insert_prompts`), dibaca lewat koneksi `_reader`.

| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | Ikut kursor halaman (`ts\|id`) |
| `ts` | TEXT NOT NULL | ISO-8601 UTC, waktu request **selesai** (diisi `Finalizer`), sehingga urutannya sama dengan `id`. Untuk request yang ditolak di `_generate`: waktu penolakan |
| `request_id` | TEXT | Sama dengan `x-request-id`, `usage_logs.request_id`, dan access log |
| `key_id` | INTEGER | `api_keys.id` (logis, tanpa FK) |
| `key_name` | TEXT | Salinan nama key saat request, agar tetap terbaca bila key diganti nama |
| `client_ip` | TEXT | Sama dengan `usage_logs.client_ip` |
| `end_user` | TEXT | Field `user` dari body client (dipotong 200 karakter), NULL bila tidak dikirim |
| `endpoint` | TEXT NOT NULL | `chat` / `completions` / `embeddings` |
| `model` | TEXT | Nama model yang diminta |
| `stream` | INTEGER NOT NULL DEFAULT 0 | 0/1 |
| `status_code` | INTEGER NOT NULL | Status akhir, sama dengan `usage_logs.status_code` (termasuk 404/400/429/503 penolakan dan 499) |
| `total_tokens` | INTEGER | Dari Ollama, atau perkiraan untuk stream yang terputus; NULL untuk penolakan |
| `prompt` | TEXT NOT NULL | JSON `[{"role", "content"}]` dari payload yang **sudah disanitasi**: teks dipotong total `PROMPT_LOG_MAX_CHARS`, gambar → `[gambar ~N KB]`, tool call → `[tool call] nama(argumen)`, daftar tools → pesan `role: "tools"` berisi nama fungsi, `sk-...` → `sk-***`. Completion: `[{"role": "prompt", ...}]`; embedding: `[{"role": "input", ...}]`. Penangkapan gagal → `[prompt tidak dapat dibaca untuk log]` |
| `prompt_preview` | TEXT NOT NULL | ≤ 200 karakter dari pesan user terakhir (untuk tabel dashboard) |
| `prompt_chars` | INTEGER NOT NULL | Panjang teks asli sebelum dipotong |
| `response` | TEXT | Teks jawaban + `[tool call] ...` (stream digabung dari chunk, tool call dikelompokkan per id), `[N embedding]` untuk embedding, dipotong `PROMPT_LOG_MAX_CHARS`. NULL untuk penolakan atau bila pengumpulan gagal |

Yang **tidak** disimpan: header, API key, data gambar, field sampling (`temperature` dan seterusnya).

## Index

| Index | Kolom | Dipakai untuk |
|---|---|---|
| `idx_usage_ts` | `usage_logs(ts)` | Filter rentang waktu, prune retensi |
| `idx_usage_key_ts` | `usage_logs(key_id, ts)` | Ringkasan per key, kuota token (`token_usage_since`, `token_usage_by_key`) |
| `idx_prompt_ts` | `prompt_logs(ts)` | Daftar log prompt per rentang waktu (selalu dibatasi), prune retensi |
| `idx_prompt_key_ts` | `prompt_logs(key_id, ts)` | Filter log prompt per key |
| (implisit) | `prompt_logs.id` PK | `get_prompt(id)`, bagian `id` dari kursor |
| (implisit) | `api_keys.key_hash` UNIQUE | Lookup auth |
| (implisit) | `models.name` PK | Cek allowlist |

## Entity relationship

```
api_keys (1) ──< usage_logs (N)      via usage_logs.key_id = api_keys.id   (tanpa FK constraint)
api_keys (1) ──< prompt_logs (N)     via prompt_logs.key_id = api_keys.id  (tanpa FK; key_name disalin)
usage_logs ·· prompt_logs            satu request = satu baris di masing-masing, dicocokkan lewat request_id
models                                berdiri sendiri; usage_logs.model / prompt_logs.model / api_keys.allowed_models
                                      menyimpan nama sebagai teks
```

`usage_summary()` memakai `LEFT JOIN api_keys` agar baris usage tetap muncul walau key-nya tidak ada.

## Data flow

1. **Auth**: `Gateway.lookup_key()` → `KeyCache` (TTL 30 dtk, termasuk hasil negatif) → `get_active_key_by_hash()`.
2. **Allowlist**: `is_model_enabled()` dipanggil setiap request generasi (tanpa cache).
3. **Usage**: `UsageWriter.submit()` (sinkron, `put_nowait`) → task background → `insert_usage()` batch ≤200 baris + update `last_used_at` → commit.
4. **Kuota token**: `QuotaTracker` (`quota.py`) mengisi total hari/bulan per key dari `token_usage_since()` saat pertama dicek di suatu periode, lalu menambah di memori lewat `Finalizer` (tidak menunggu `UsageWriter`). Daftar key di admin memakai `token_usage_by_key(day_start, month_start)` (satu query, `SUM(CASE ...)`). Batas periode = 00:00 / tanggal 1 waktu lokal mesin, dikonversi ke UTC ISO agar bisa dibandingkan dengan `usage_logs.ts`.
5. **Log prompt (tulis)**: `PromptRecord` dibuat di `_generate` setelah body terbaca (`new_record`), isinya diganti dengan versi payload yang sudah disanitasi (`capture_prompt(payload)`), lalu dilengkapi `Finalizer` (status, token, jawaban, `ts` selesai). Penulisan lewat `UsageWriter` yang sama dengan usage: satu antrean, batch ≤ 200, `insert_prompts` terpisah dari `insert_usage`. Gagal tulis hanya di-log (`failed to write N prompt-log rows`), tidak memengaruhi request. Request yang ditolak setelah body terbaca ikut disubmit dari jalur `except APIError`.
6. **Log prompt (baca)**:
   - `list_prompts()` selalu memakai rentang `ts` (default 24 jam) + `LIMIT` (≤ 100) dan hanya mengembalikan cuplikan (`prompt_preview`, `substr(response, 1, 200)`, panjang). `get_prompt()` memuat isi lengkap satu baris dan mem-parse `prompt` dari JSON.
   - Keduanya berjalan di **koneksi read-only kedua** (`Database._reader` lewat `_read_all`). Dengan WAL, koneksi itu paralel dengan koneksi utama, sehingga pencarian berat tidak menunda query request API ([decisions.md #30](decisions.md#30-koneksi-sqlite-read-only-kedua-dan-kursor-index-friendly-untuk-log-prompt)).
   - Kursor halaman ditulis `ts <= ? AND (ts < ? OR id < ?)`, agar tetap satu range scan `idx_prompt_ts` tanpa sort sementara (dijaga oleh test `EXPLAIN QUERY PLAN`). Bentuk `ts < ? OR (ts = ? AND id < ?)` memicu OR multi-index + temp B-tree (~200 ms per halaman pada 50 ribu baris).
   - Pencarian `q`: `LIKE ... ESCAPE '\'` dengan `%`, `_`, dan `\` di-escape. Untuk kolom `prompt` (JSON), kata kunci lebih dulu di-encode seperti `json.dumps` (kutip dan baris baru jadi `\"`, `\n`), sehingga teks berkutip/multi-baris tetap ketemu. Kolom `response` dan `end_user` dicari sebagai teks biasa. Pencarian selalu full scan dalam rentang waktu; tidak ada FTS.
7. **Retensi**: `prune_usage(USAGE_RETENTION_DAYS)` dan `prune_prompts(PROMPT_LOG_RETENTION_DAYS)` saat start lalu setiap 24 jam (`_prune_loop`). `PRAGMA secure_delete=FAST` di koneksi utama menimpa isi baris yang dihapus dengan nol selama tidak menambah I/O, sehingga prompt yang sudah di-prune hilang dari halaman b-tree (jejak masih mungkin ada di halaman freelist sampai dipakai ulang). Ruang file tidak dikembalikan ke disk (tanpa `VACUUM`), tetapi dipakai ulang oleh baris baru.
8. **Backup**: `POST /admin/backup` → `VACUUM INTO data/backups/gateway-YYYYmmdd-HHMMSS.db` (manual, belum dijadwalkan). **File backup ikut memuat `prompt_logs`** dan tidak dipangkas otomatis, jadi hapus backup yang lebih tua dari 30 hari sesuai janji retensi di `CLIENT.md#privasi`.

## Ukuran & pertumbuhan

| Ukuran | Angka |
|---|---|
| Baris `prompt_logs` nyata (uji QA, chat pendek) | ~1,2 KB rata-rata (`prompt` + `response` + cuplikan) |
| Baris sintetis uji performa | 50 ribu baris = 179 MB (~3,6 KB per baris, termasuk index) |
| Batas atas satu baris | ~2 × `PROMPT_LOG_MAX_CHARS` karakter (prompt + jawaban, masing-masing ≤ 20.000), ditambah escape JSON |
| Perkiraan 1.000 request/hari × 30 hari | 30 ribu baris ≈ 40–110 MB |

Performa terukur (50 ribu baris, rentang 30 hari): halaman pertama 1–2 ms, 10 halaman berturut-turut 14 ms, pencarian terburuk 30 hari ~190 ms (sebelum `mmap` ~430 ms), dan query API (`/v1/models`) selama pencarian 1,5 ms (sebelum koneksi reader: 6 → 390 ms). Bila tabel tumbuh jauh di atas angka ini dan pencarian terasa lambat, pertimbangkan FTS5 (belum diperlukan).

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
8. File DB, `-wal`/`-shm`, dan `data/backups/` bersifat sensitif (hash key, IP client, **isi prompt & jawaban**) dan tidak boleh di-commit (`data/` ada di `.gitignore`).
9. Query yang bisa berat (rentang panjang, `LIKE`) hanya di koneksi `_reader` lewat `_read_all`, selalu dengan rentang waktu + `LIMIT`. Daftar tidak pernah memilih kolom `prompt`/`response` utuh. Bentuk kursor baru wajib dicek dengan `EXPLAIN QUERY PLAN` (contoh di `tests/test_prompt_log.py`).
10. Menghapus baris `prompt_logs` secara manual (di luar prune) adalah aksi yang tidak bisa dibatalkan: backup dulu dan minta persetujuan pemilik (lihat [operations.md](operations.md#runbook)).

## Isi saat ini *(observasi mesin, 2026-11-03)*

- File `data/gateway.db` (+ `-wal`/`-shm`): ~0,9 MB.
- `api_keys`: 41 baris. **1 key aktif**: `naufalnaufal` (id 7, milik pemilik): rpm 100.000, `max_concurrent` 64, tanpa kuota/masa berlaku/batasan model, prioritas 9, `log_prompts` 1. 40 lainnya key uji agent yang sudah dicabut (`smoke-test*`, `security-probe`, `loadtest-*`, `qa-*`, dan lain-lain). Agent membuat key uji sementara lalu langsung mencabutnya, dan pola itu dipertahankan.
- `models`: `gemma3:4b` (enabled) dan `qwen3.5:4b` (enabled).
- `usage_logs`: 1.380 baris (2026-09-27 s.d. 2026-11-03), sebagian besar uji beban/QA agent.
- `prompt_logs`: 251 baris, semuanya 2026-11-03 dari key uji QA: `qa-soak` 224, `qa-plog-edge` 22, `qa-plog-on` 3, `qa-fix-live` 2. Belum ada prompt dari client sungguhan. Penghapusan baris uji menunggu keputusan pemilik; tanpa tindakan, terhapus otomatis sekitar 2026-12-03.
- `data/backups/`: `gateway-20261018-133759.db` (sebelum migrasi #27) dan `gateway-20261103-081048.db` (sebelum migrasi #29).
