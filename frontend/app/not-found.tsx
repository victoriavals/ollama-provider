import Link from "next/link";
export default function NotFound() {
  return (
    <main className="not-found">
      <h1>Halaman tidak ditemukan</h1>
      <p>Buka ringkasan untuk kembali mengelola provider.</p>
      <Link href="/">Kembali ke ringkasan</Link>
    </main>
  );
}
