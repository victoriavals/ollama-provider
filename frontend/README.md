# Dashboard Ollama Provider

Frontend React 19 + Next.js 16 (App Router, TypeScript), Tailwind CSS 4, shadcn/ui, TanStack Query, Motion, GSAP, dan komponen React Bits. Semua halaman diekspor statis; produksi tetap memakai FastAPI pada port admin yang sudah ada.

## Build dan preview

Dari root repository, jalankan `uv sync` untuk dependency Python. Kemudian:

```powershell
cd frontend
npm ci
npm run build
npm run preview
```

Buka **http://127.0.0.1:18001/admin/ui** dengan admin key **`demo-admin`**. Preview ini memakai data simulasi dan perubahan di memori. Tidak membaca `.env`, database, prompt, key, atau Ollama produksi. Matikan dengan Ctrl+C.

`npm run dev` membuka server Next.js lokal di port 13000 untuk pengembangan layout. Gunakan build + preview untuk pengujian lengkap terhadap API simulasi dan CSP produksi. Proxy API tidak disediakan pada mode Next dev; verifikasi alur melalui preview.

## Pemeriksaan

```powershell
npm run typecheck
npm run lint
npm run format:check
npm run build
npm test
# dari root repository:
uv run pytest -q
```

Playwright memakai Microsoft Edge yang sudah terpasang. Konfigurasinya menjalankan preview sendiri pada port 18001, jadi hentikan preview manual sebelum `npm test`. Screenshot dan trace tersedia di `test-results/`, laporan HTML di `playwright-report/`.

## Struktur dan kontrak

- `app/`: enam halaman statis dan tema.
- `views/`: alur Ringkasan, API key, Pemakaian, Request, Prompt, dan Model.
- `components/`: shell, grafik, dialog native dengan primitive judul shadcn, serta kontrol bersama.
- `lib/`: tipe API, client error, dan pemformatan WIB. Agregasi harian dari server tetap UTC dan diberi label.
- `scripts/export.mjs`: menyalin ekspor ke release baru di `gateway/static/dashboard/build/`, menghitung hash skrip bootstrap, lalu mengganti pointer `active.json` secara atomik.
- `scripts/preview.py`: server demo loopback dengan API simulasi, hanya untuk pengembangan dan QA.

API publik, schema database, dan kebijakan gateway tidak berubah. Auth tetap Bearer admin key pada browser, disimpan dalam sesi tab. Logout membatalkan request dan membersihkan cache. Query prompt tidak ikut polling/focus/reconnect; daftar 50 baris per halaman dibatasi 500 baris dan detail diambil ketika dibuka.

## CSP dan rilis

`gateway/dashboard.py` hanya melayani halaman serta aset yang ada dalam manifest. Skrip inline hasil build mendapat SHA-256 CSP masing-masing halaman; `unsafe-inline` dan `unsafe-eval` tidak digunakan. Font disimpan lokal. Dialog memakai focus/inert native browser untuk menghindari injeksi stylesheet scroll-lock pada runtime.

Hasil build tidak masuk Git. Jalankan build pada mesin tujuan atau kirim seluruh direktori `build/` sebagai artifact yang sama dengan source-nya. Pertahankan release lama untuk rollback. Muat ulang tab yang terbuka setelah mengganti build aktif. Jangan mengedit file hasil build. Checkout tanpa build menggunakan dashboard vanilla yang lama sebagai fallback. Setelah preview tervalidasi, aktivasi produksi dilakukan melalui prosedur restart gateway yang sudah ada; pekerjaan redesign ini tidak me-restart service.

## Toolkit dan aset animasi

Motion dipakai untuk transisi panel, GSAP untuk ilustrasi GPU, dan SpotlightCard dari React Bits untuk respons pointer. Semua berhenti atau berkurang sesuai preferensi animasi. Runtime Lottie dan Rive tersedia sebagai dependency proyek untuk aset lanjutan; keduanya tidak dimuat oleh dashboard awal. Saat menambahkan aset tersebut, simpan aset/WASM lokal, gunakan dynamic import, dan verifikasi kebijakan WASM/CSP sebelum mengaktifkannya.

Komponen shadcn dibuat melalui CLI dengan registry Radix (`new-york`). SpotlightCard ditambahkan dari registry `@react-bits/SpotlightCard-TS-CSS`; sumbernya [React Bits](https://github.com/DavidHDev/react-bits), berlisensi MIT + Commons Clause. Perubahan lokal: token aplikasi, hit target, serta pembaruan ref dalam effect. Komponen shadcn mengikuti [lisensi MIT](https://github.com/shadcn-ui/ui/blob/main/LICENSE.md).

Override `minimatch >=11.0.1` hanya pada tooling `@typescript-eslint/typescript-estree` menutup GHSA-g3hv-75wg-m249; API `minimatch()` yang dipakai parser tetap sama dan diverifikasi oleh lint. Node 24 direkomendasikan. Paket runtime frontend dan lockfile tetap per proyek, sedangkan CLI/skill/MCP dipasang global pada akun pengguna.

Skill global: `motion`, `shadcn`, `react-best-practices`, `frontend-design`, dan `ui-ux-pro-max`. MCP global: `motion`, `shadcn`, `playwright`, `context7`. Motion dan Context7 memerlukan penyelesaian OAuth melalui `codex mcp login motion` / `codex mcp login context7`; Rive lokal tidak memerlukan login. Buka sesi Codex baru untuk memuat toolkit global yang baru dipasang.
