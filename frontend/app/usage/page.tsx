import { AppShell } from "@/components/app-shell";
import { Usage } from "@/views/usage";
export default function Page() {
  return (
    <AppShell
      title="Pemakaian"
      description="Pahami ke mana request dan token Anda mengalir."
    >
      <Usage />
    </AppShell>
  );
}
