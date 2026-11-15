import { AppShell } from "@/components/app-shell";
import { Overview } from "@/views/overview";
export default function Page() {
  return (
    <AppShell
      title="Ringkasan provider"
      description="Semua yang Anda perlukan untuk menjaga model tetap berjalan."
    >
      <Overview />
    </AppShell>
  );
}
