"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import {
  Activity,
  ArrowUpRight,
  Blocks,
  ChartNoAxesCombined,
  ChevronRight,
  Cpu,
  KeyRound,
  LayoutDashboard,
  LogOut,
  Menu,
  MessageSquareText,
  Settings2,
  ShieldCheck,
  Sparkles,
  Zap,
} from "lucide-react";
import {
  useAuth,
  useInfo,
  usePreferences,
  useStatus,
  useTask,
} from "./providers";
import {
  Button,
  Failure,
  Loading,
  Overlay,
  StatusPill,
  SubmitButton,
  TextField,
} from "./common";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { clock } from "@/lib/format";
import { cn } from "@/lib/utils";

const navigation = [
  { path: "/", label: "Ringkasan", icon: LayoutDashboard },
  { path: "/keys/", label: "API key", icon: KeyRound },
  { path: "/usage/", label: "Pemakaian", icon: ChartNoAxesCombined },
  { path: "/requests/", label: "Request", icon: Activity },
  { path: "/prompts/", label: "Log prompt", icon: MessageSquareText },
  { path: "/models/", label: "Model", icon: Blocks },
];
const legacy: Record<string, string> = {
  overview: "/",
  keys: "/keys/",
  usage: "/usage/",
  requests: "/requests/",
  prompts: "/prompts/",
  models: "/models/",
};

export function Brand() {
  return (
    <span className="brand-lockup">
      <span className="brand-mark">
        <Cpu aria-hidden="true" />
      </span>
      <span>
        ollama<span className="brand-sub">provider</span>
      </span>
    </span>
  );
}

function Login() {
  const { login, notice } = useAuth();
  const task = useTask();
  const [key, setKey] = useState("");
  return (
    <main className="login-page">
      <section className="login-story">
        <Brand />
        <div>
          <span className="login-symbol">
            <Cpu strokeWidth={1} />
          </span>
          <h1>
            Model lokal.
            <br />
            Kemungkinan luas.
          </h1>
          <p>
            Satu tempat untuk menjaga model tetap siap, mengatur akses, dan
            memahami setiap request.
          </p>
        </div>
        <p className="login-footnote">
          <ShieldCheck />
          Dashboard admin lokal
        </p>
      </section>
      <section className="login-form-side">
        <div className="login-card">
          <span className="login-kicker">
            <Zap />
            Workspace Anda
          </span>
          <h2>Selamat datang kembali.</h2>
          <p className="muted">Masuk untuk mengelola Ollama Provider.</p>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void task.run(() => login(key.trim()));
            }}
          >
            <FieldGroup>
              <TextField
                label="Admin key"
                id="admin-key"
                type="password"
                autoComplete="off"
                spellCheck={false}
                required
                value={key}
                onChange={(event) => setKey(event.target.value)}
                placeholder="Masukkan admin key Anda"
              />
              <Failure error={task.error || notice} />
              <SubmitButton pending={task.pending}>
                Masuk ke dashboard
                <ArrowUpRight data-icon="inline-end" />
              </SubmitButton>
            </FieldGroup>
          </form>
          <details className="help-details">
            <summary>Di mana saya mendapatkan admin key?</summary>
            <p>
              Gunakan nilai <code>ADMIN_KEY</code> dari file <code>.env</code>{" "}
              di komputer server. Untuk akses dari perangkat lain, gunakan SSH
              port forwarding ke port admin.
            </p>
          </details>
          <div className="login-security">
            <ShieldCheck />
            <p>
              Key disimpan selama sesi tab ini.
              <br />
              Tutup tab atau keluar untuk mengakhiri sesi.
            </p>
          </div>
        </div>
      </section>
    </main>
  );
}

export function AppShell({
  children,
  title,
  description,
  action,
}: {
  children: ReactNode;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  const { ready, authenticated, logout } = useAuth();
  const { theme, setTheme, animations, setAnimations } = usePreferences();
  const status = useStatus();
  const info = useInfo();
  const router = useRouter();
  const pathname = usePathname();
  const [mobileNav, setMobileNav] = useState(false);
  const [settings, setSettings] = useState(false);
  const [healthOpen, setHealthOpen] = useState(false);
  useEffect(() => {
    function restoreHash() {
      const hash = window.location.hash.slice(1);
      if (legacy[hash]) router.replace(legacy[hash]);
    }
    restoreHash();
    window.addEventListener("hashchange", restoreHash);
    return () => window.removeEventListener("hashchange", restoreHash);
  }, [router]);
  useEffect(() => {
    setMobileNav(false);
  }, [pathname]);
  if (!ready)
    return (
      <main className="session-loading">
        <Brand />
        <Loading label="Memeriksa sesi" />
      </main>
    );
  if (!authenticated) return <Login />;
  const components = Object.entries(status.data?.components || {});
  const issue = components.filter(
    ([, component]) =>
      component.status === "warn" || component.status === "down",
  ).length;
  const active =
    navigation.find(
      (item) =>
        item.path === pathname ||
        (item.path !== "/" && pathname.endsWith(item.path)),
    ) || navigation[0];
  const navItems = (
    <nav aria-label="Menu utama" className="nav-list">
      {navigation.map((item) => {
        const Icon = item.icon;
        return (
          <Link
            key={item.path}
            aria-label={item.label}
            href={item.path}
            className={cn(
              "nav-item",
              item.label === active.label && "nav-active",
            )}
            aria-current={item.label === active.label ? "page" : undefined}
            onClick={() => setMobileNav(false)}
          >
            <Icon aria-hidden="true" />
            <span>{item.label}</span>
            {item.label === active.label && <span className="nav-active-dot" />}
          </Link>
        );
      })}
    </nav>
  );
  return (
    <div className="dashboard">
      <a href="#main-content" className="skip-link">
        Langsung ke konten
      </a>
      <aside className="desktop-sidebar">
        <Link href="/" aria-label="Ollama Provider, ringkasan">
          <Brand />
        </Link>
        <p className="nav-heading">Workspace</p>
        {navItems}
        <div className="sidebar-bottom">
          <div className="sidebar-note">
            <span className="sidebar-note-icon">
              <Sparkles />
            </span>
            <strong>
              Tenaga lokal.
              <br />
              Kendali penuh.
            </strong>
            <p>Model Anda, di infrastruktur Anda.</p>
          </div>
          <Button variant="ghost" onClick={() => setSettings(true)}>
            <Settings2 data-icon="inline-start" />
            Preferensi
          </Button>
          <div className="operator">
            <span className="operator-avatar">A</span>
            <span>
              <strong>Administrator</strong>
              <small>Akses lokal</small>
            </span>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Keluar"
              onClick={() => logout()}
            >
              <LogOut />
            </Button>
          </div>
        </div>
      </aside>
      <div className="workspace">
        <header className="app-header">
          <div className="breadcrumb">
            <Button
              variant="ghost"
              size="icon"
              className="mobile-menu"
              aria-label="Buka menu"
              onClick={() => setMobileNav(true)}
            >
              <Menu />
            </Button>
            <span className="breadcrumb-parent">Workspace</span>
            <ChevronRight aria-hidden="true" />
            <span>{active.label}</span>
          </div>
          <div className="header-actions">
            <Button
              variant="ghost"
              className="health-trigger"
              onClick={() => setHealthOpen(true)}
            >
              <span
                className={cn(
                  "connection-dot",
                  (!status.data || status.isError || issue > 0) && "attention",
                )}
              />
              {status.isError
                ? "Koneksi terputus"
                : !status.data
                  ? "Memeriksa status"
                  : issue
                    ? `${issue} perlu dicek`
                    : "Sistem normal"}
            </Button>
            <span className="local-label">
              <ShieldCheck />
              {info.data?.demo_mode ? "Demo" : "Lokal"}
            </span>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Preferensi tampilan"
              onClick={() => setSettings(true)}
            >
              <Settings2 />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="mobile-menu"
              aria-label="Keluar"
              onClick={() => logout()}
            >
              <LogOut />
            </Button>
          </div>
        </header>
        <main id="main-content" tabIndex={-1} className="main-content">
          <div className="page-heading">
            <div>
              <h1>{title}</h1>
              <p>{description}</p>
            </div>
            {action}
          </div>
          {status.isError && (
            <Failure error={status.error} retry={() => void status.refetch()} />
          )}
          <div className="page-content">{children}</div>
          <footer className="workspace-footer">
            <span>
              Ollama Provider<span className="footer-dot">/</span>Workspace
              lokal
            </span>
            <span>
              {status.dataUpdatedAt
                ? `Status terakhir ${clock(status.dataUpdatedAt)} WIB`
                : "Memeriksa status…"}
            </span>
          </footer>
        </main>
      </div>
      <Overlay
        side
        open={mobileNav}
        onOpenChange={setMobileNav}
        title="Menu workspace"
        description="Navigasi dashboard Ollama Provider."
      >
        <div className="mobile-navigation">{navItems}</div>
      </Overlay>
      <Overlay
        open={settings}
        onOpenChange={setSettings}
        title="Preferensi tampilan"
        description="Tersimpan pada perangkat ini dan berlaku untuk dashboard Anda."
      >
        <FieldGroup>
          <Field>
            <FieldLabel htmlFor="theme">Tema</FieldLabel>
            <NativeSelect
              id="theme"
              value={theme}
              onChange={(event) => setTheme(event.target.value)}
            >
              <NativeSelectOption value="system">
                Ikuti sistem
              </NativeSelectOption>
              <NativeSelectOption value="light">Terang</NativeSelectOption>
              <NativeSelectOption value="dark">Gelap</NativeSelectOption>
            </NativeSelect>
          </Field>
          <label className="check-control">
            <input
              type="checkbox"
              checked={animations}
              onChange={(event) => setAnimations(event.target.checked)}
            />
            Aktifkan animasi
          </label>
          <p className="muted">
            Preferensi pengurangan gerakan dari sistem selalu dihormati.
          </p>
        </FieldGroup>
      </Overlay>
      <Overlay
        side
        open={healthOpen}
        onOpenChange={setHealthOpen}
        title="Kesehatan sistem"
        description="Status dependency dan koneksi terakhir dari server."
      >
        {status.data ? (
          <div className="health-list">
            {components.map(([name, component]) => (
              <div key={name}>
                <div className="row-between">
                  <strong>{componentName(name)}</strong>
                  <StatusPill kind={component.status} />
                </div>
                <p>{component.detail}</p>
              </div>
            ))}
          </div>
        ) : (
          <Loading />
        )}
        <Button variant="outline" onClick={() => void status.refetch()}>
          Periksa kembali
        </Button>
      </Overlay>
    </div>
  );
}

export function componentName(name: string) {
  const names: Record<string, string> = {
    ollama: "Server Ollama",
    model_gpu: "Model di GPU",
    gpu: "Kartu grafis",
  };
  return (
    names[name] ||
    name.replace(/^service:/, "Service ").replace(/^disk:/, "Disk ")
  );
}
