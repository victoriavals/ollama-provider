import type { Metadata } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import { Providers } from "@/components/providers";
import "./globals.css";
import "./dashboard.css";

export const metadata: Metadata = {
  title: "Kontrol Ollama Provider",
  description:
    "Dashboard admin lokal untuk model, akses API, dan pemakaian Ollama.",
  robots: { index: false, follow: false },
  icons: { icon: "/admin/ui/icon.svg" },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="id" suppressHydrationWarning>
      <body className={`${GeistSans.variable} ${GeistMono.variable}`}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
