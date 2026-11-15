import { AppShell } from "@/components/app-shell";
import { Models } from "@/views/models";
export default function Page() {
  return (
    <AppShell
      title="Model"
      description="Siapkan model yang tepat untuk setiap kebutuhan aplikasi."
    >
      <Models />
    </AppShell>
  );
}
