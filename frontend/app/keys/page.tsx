import { AppShell } from "@/components/app-shell";
import { Keys } from "@/views/keys";
export default function Page() {
  return (
    <AppShell
      title="API key"
      description="Akses yang tepat, untuk setiap aplikasi."
    >
      <Keys />
    </AppShell>
  );
}
