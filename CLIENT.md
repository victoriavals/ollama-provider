# Panduan client

API ini **kompatibel dengan OpenAI**. Kode yang sudah memakai SDK OpenAI, LangChain, atau library sejenis cukup diganti *base URL* dan *API key*-nya. Modelnya berjalan di server pribadi dengan satu GPU, jadi baca bagian [Batas](#batas) dan [Antrean](#antrean) sebelum mengirim banyak request sekaligus.

## Akses

Dari penyedia, kamu menerima:

| | Contoh |
|---|---|
| Base URL | `https://<domain-dari-penyedia>/v1` |
| API key | `sk-...` |
| Model yang boleh dipakai | mis. `gemma3:4b`, `qwen3.5:4b` |
| Batas | request per menit, request bersamaan, kuota token, masa berlaku |

Rahasiakan API key: jangan ditaruh di kode frontend, repo publik, atau aplikasi yang dibagikan. Kalau key bocor, minta key baru ke penyedia; key lama akan dicabut.

## Contoh

**curl**

```bash
curl https://<domain>/v1/chat/completions \
  -H "Authorization: Bearer sk-..." \
  -H "Content-Type: application/json" \
  -d '{"model": "gemma3:4b", "messages": [{"role": "user", "content": "Halo!"}]}'
```

**Python** (`pip install openai`)

```python
from openai import OpenAI

client = OpenAI(base_url="https://<domain>/v1", api_key="sk-...")

r = client.chat.completions.create(
    model="gemma3:4b",
    messages=[{"role": "user", "content": "Jelaskan fotosintesis dalam 3 kalimat."}],
    max_tokens=300,
)
print(r.choices[0].message.content)

# Streaming: jawaban muncul sedikit demi sedikit
for chunk in client.chat.completions.create(model="gemma3:4b", stream=True,
                                            messages=[{"role": "user", "content": "Ceritakan lelucon."}]):
    print(chunk.choices[0].delta.content or "", end="", flush=True)
```

**JavaScript / TypeScript** (`npm install openai`)

```js
import OpenAI from "openai";

const client = new OpenAI({ baseURL: "https://<domain>/v1", apiKey: "sk-..." });
const r = await client.chat.completions.create({
  model: "gemma3:4b",
  messages: [{ role: "user", content: "Halo!" }],
});
console.log(r.choices[0].message.content);
```

Jalankan kode ini di server atau backend, bukan di browser. Kalau terpaksa memanggil dari browser, tambahkan header `ngrok-skip-browser-warning: 1`.

## Model

Daftar model yang boleh dipakai key-mu: `GET /v1/models`.

| Model | Cocok untuk | Catatan |
|---|---|---|
| `gemma3:4b` | Percakapan umum bahasa Indonesia, ringkasan, terjemahan | Bisa membaca gambar. Tidak mendukung *tools* |
| `qwen3.5:4b` | *Tools / function calling*, output JSON terstruktur, gambar | Hanya memproses **1 request sekaligus** di server. Mode berpikir mati secara default |

- Untuk `qwen3.5:4b`, mode berpikir bisa dinyalakan dengan `reasoning_effort: "medium"` atau `"high"`. Beri `max_tokens` besar (mis. 2048), karena token berpikir ikut terhitung.
- Gambar dikirim sebagai **data URL base64** (`data:image/png;base64,...`), maksimal 2 gambar per request. URL gambar biasa ditolak.
- Hanya satu model yang bisa aktif di GPU. Request pertama setelah berganti model butuh beberapa detik ekstra untuk memuat model.

## Endpoint

| Endpoint | Fungsi |
|---|---|
| `GET /v1/models` | Daftar model untuk key-mu |
| `POST /v1/chat/completions` | Chat (stream maupun tidak) |
| `POST /v1/completions` | Completion teks lama (tidak untuk `qwen3.5:4b`) |
| `POST /v1/embeddings` | Embedding (bila penyedia mengaktifkan model embedding) |

Field yang diteruskan: `model`, `messages`, `stream`, `stream_options`, `max_tokens`, `temperature`, `top_p`, `stop`, `seed`, `presence_penalty`, `frequency_penalty`, `response_format`, `tools`, `tool_choice`, `reasoning_effort`, `user`. Field lain diabaikan, dan `max_tokens` dibatasi oleh server.

## Batas

Setiap key punya batas sendiri. Tanyakan angkanya ke penyedia.

| Batas | Saat terlampaui |
|---|---|
| Request per menit | `429 rate_limit_exceeded`, coba lagi sesuai header `Retry-After` |
| Request bersamaan (default 2) | `429 concurrency_limit_exceeded`, tunggu request sebelumnya selesai |
| Kuota token harian / bulanan | `429 insufficient_quota`, kuota reset pukul 00:00 / tanggal 1 WIB |
| Masa berlaku | `401 api_key_expired`, minta perpanjangan ke penyedia |

- Kuota menghitung **total token** (input + output). Request yang sedang berjalan boleh melewati sisa kuota; request berikutnya baru ditolak.
- Stream yang kamu putus sebelum selesai tetap dihitung, memakai perkiraan token yang sudah dihasilkan.
- Saat kuota habis, server mengirim `x-should-retry: false` supaya SDK tidak mengulang percuma, dan `Retry-After` berisi detik sampai kuota reset.

**Header yang bisa dipantau**

| Header | Isi |
|---|---|
| `x-ratelimit-limit-requests`, `x-ratelimit-remaining-requests` | Batas dan sisa request per menit |
| `x-quota-tokens-day-limit`, `x-quota-tokens-day-remaining` | Kuota harian dan sisanya (hanya bila key punya kuota harian) |
| `x-quota-tokens-month-limit`, `x-quota-tokens-month-remaining` | Kuota bulanan dan sisanya |
| `x-request-id` | ID request, sebutkan saat melapor masalah |

Sisa kuota di header dihitung saat request dimulai, belum termasuk token dari request itu sendiri.

## Antrean

Server memakai satu GPU, jadi request diantre:

- `gemma3:4b` memproses hingga 4 request sekaligus, `qwen3.5:4b` satu per satu. Sisanya menunggu giliran.
- Request yang menunggu lebih dari **60 detik** dibalas `503 server_busy`. Begitu juga kalau antrean sedang penuh.
- Untuk request stream, header `200` datang seketika, lalu token pertama muncul saat giliranmu tiba. Atur timeout client minimal 120 detik.
- Saat server di-restart, request yang sedang antre dibalas `503 server_busy` dengan `Retry-After: 15`.
- SDK OpenAI otomatis mengulang request yang gagal karena `503`. Kalau memakai HTTP biasa, ulangi sendiri dengan jeda sesuai `Retry-After`.

Tips supaya cepat:
- Pakai satu model untuk seluruh aplikasi. Bergantian model membuat server memuat ulang model.
- Isi `max_tokens` sesuai kebutuhan, jangan selalu maksimal.
- Pakai `stream: true` untuk aplikasi chat, supaya pengguna melihat jawaban mulai muncul lebih cepat.

## Error

Format error mengikuti OpenAI: `{"error": {"message", "type", "code", "param"}}`.

| Status | `code` | Arti |
|---|---|---|
| 400 | `model_not_supported`, lainnya | Request tidak valid (field, gambar, model tidak cocok untuk endpoint) |
| 401 | `invalid_api_key` | Key salah, tidak dikirim, atau sudah dicabut |
| 401 | `api_key_expired` | Masa berlaku key habis |
| 404 | `model_not_found` | Model tidak ada atau tidak boleh dipakai key-mu |
| 413 | `request_too_large` | Body terlalu besar (maks. 4 MB) |
| 429 | `rate_limit_exceeded` | Batas request per menit |
| 429 | `concurrency_limit_exceeded` | Terlalu banyak request bersamaan |
| 429 | `insufficient_quota` | Kuota token harian/bulanan habis |
| 429 | `auth_rate_limited` | Terlalu banyak percobaan key salah dari IP-mu (tunggu 60 detik) |
| 503 | `server_busy` | Antrean penuh, menunggu > 60 detik, atau server sedang restart |
| 503 | `upstream_unavailable` | Server model sedang memulai ulang; coba lagi sebentar |
| 502 / 504 | `bad_gateway` / `upstream_timeout` | Gangguan koneksi ke server model |

Pada request stream, error yang terjadi setelah header `200` terkirim datang sebagai event `data: {"error": {...}}`.

## Privasi

Untuk keamanan dan mencegah penyalahgunaan, penyedia **mencatat isi prompt dan jawaban** beserta API key, IP, waktu, model, jumlah token, dan field `user` bila kamu mengirimnya.

- Catatan disimpan **30 hari**, lalu dihapus otomatis.
- Hanya penyedia (admin server) yang bisa melihatnya.
- Gambar tidak disimpan, hanya ukurannya.
- Teks yang sangat panjang dipotong.
- Teks yang mirip API key disamarkan.

Jangan kirim data rahasia atau data pribadi yang tidak diperlukan.

Kalau aplikasimu dipakai banyak orang, kirim field `user` (mis. ID pengguna di aplikasimu) di setiap request, agar penyedia bisa membedakan pengguna saat ada penyalahgunaan dan tidak perlu mencabut key seluruh aplikasimu.

Model berjalan di server penyedia dan tidak dikirim ke layanan AI pihak ketiga.
