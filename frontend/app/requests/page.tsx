import { AppShell } from "@/components/app-shell";
import { Requests } from "@/views/requests";
export default function Page() {
  return (
    <AppShell
      title="Request"
      description="Pantau lalu lintas dan temukan request yang perlu diperhatikan."
    >
      <Requests />
    </AppShell>
  );
}
