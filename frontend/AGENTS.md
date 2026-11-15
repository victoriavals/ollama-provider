# Dashboard frontend

Baca `../.agents/memory.md` dan `../.agents/conventions.md` untuk konteks gateway. Redesign React/Next.js ini telah diotorisasi pemilik; aturan vanilla lama berlaku hanya pada fallback.

- Baca dokumentasi yang cocok dengan versi Next.js di `node_modules/next/dist/docs/` sebelum memakai API framework baru.
- Gunakan admin API yang sudah ada. Browser tidak memanggil Ollama atau API publik secara langsung.
- Pertahankan auth per tab, pembatalan request/logout, PATCH parsial, dan batas log prompt. Secret baru hanya di state sementara; jangan masukkan ke log, URL, atau storage persisten.
- Semua teks dari API dirender sebagai teks React. Jangan memakai `dangerouslySetInnerHTML` atau memuat script/font dari CDN.
- Gunakan primitive shadcn dan token CSS semantik. Target sentuh minimal 44 px. Overlay native menjaga focus trap, Escape, inert, dan CSP ketat.
- Animasi menghormati preferensi pengguna/sistem dan berhenti ketika tab disembunyikan. Library/aset berat dimuat secara malas.
- Setelah perubahan: typecheck, lint, format check, build, lalu Playwright. Perubahan penyajian aset juga wajib lolos pytest dari root.
- Jangan me-restart service produksi, commit, atau push sebagai bagian dari pemeriksaan frontend.
