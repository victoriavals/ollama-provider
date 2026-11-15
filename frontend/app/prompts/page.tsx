import { AppShell } from "@/components/app-shell";
import { Prompts } from "@/views/prompts";
export default function Page() {
  return (
    <AppShell
      title="Log prompt"
      description="Telusuri percakapan, dari input sampai jawaban."
    >
      <Prompts />
    </AppShell>
  );
}
