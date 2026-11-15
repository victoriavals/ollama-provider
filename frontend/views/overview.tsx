"use client";

import Link from "next/link";
import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowDownToLine,
  ArrowUpRight,
  Braces,
  CheckCircle2,
  Clock3,
  Cpu,
  KeyRound,
  Layers3,
  Plus,
  Radio,
  Terminal,
} from "lucide-react";
import {
  Button,
  Choices,
  CopyButton,
  Failure,
  Loading,
  NoData,
  Overlay,
  Panel,
  RequestStatus,
  StatusPill,
} from "@/components/common";
import {
  useAuth,
  useInfo,
  usePreferences,
  useStatus,
} from "@/components/providers";
import { componentName } from "@/components/app-shell";
import { ActivityChart } from "@/components/activity-chart";
import SpotlightCard from "@/components/SpotlightCard";
import { compact, dateTime, latency, number, todayWib } from "@/lib/format";
import type { RequestRow, Usage } from "@/lib/types";

const GpuCircuit = dynamic(() => import("@/components/gpu-circuit"), {
  ssr: false,
});

export function Overview() {
  const { request } = useAuth();
  const status = useStatus();
  const info = useInfo();
  const { motionEnabled } = usePreferences();
  const [access, setAccess] = useState(false);
  const [language, setLanguage] = useState("python");
  const [since, setSince] = useState(todayWib);
  const [start, setStart] = useState(
    () => Math.floor(Date.now() / 3600000) * 3600000 - 23 * 3600000,
  );
  useEffect(() => {
    const timer = setInterval(() => {
      setSince(todayWib());
      setStart(Math.floor(Date.now() / 3600000) * 3600000 - 23 * 3600000);
    }, 10000);
    return () => clearInterval(timer);
  }, []);
  const today = useQuery({
    queryKey: ["today", since],
    queryFn: ({ signal }) =>
      request<Usage>(`/usage?group_by=key&since=${encodeURIComponent(since)}`, {
        signal,
      }),
    refetchInterval: 10000,
  });
  const hourly = useQuery({
    queryKey: ["hourly", start],
    queryFn: ({ signal }) =>
      request<Usage>(
        `/usage?group_by=hour&since=${encodeURIComponent(new Date(start).toISOString())}`,
        { signal },
      ),
    refetchInterval: 10000,
  });
  const recent = useQuery({
    queryKey: ["recent", 5],
    queryFn: ({ signal }) =>
      request<{ data: RequestRow[] }>("/usage/recent?limit=5", { signal }),
    refetchInterval: 10000,
  });
  const sum = (field: "requests" | "errors" | "total_tokens") =>
    today.data
      ? today.data.data.reduce((total, row) => total + row[field], 0)
      : null;
  const g = status.data?.gateway.concurrency;
  const turn = g?.lanes.find((lane) => lane.model === g.turn?.model);
  const used = turn?.running ?? g?.active ?? 0;
  const capacity = turn?.capacity ?? g?.global_limit ?? 0;
  const heroContent = (
    <>
      <div className="gpu-hero-copy">
        <div className="hero-caption">
          <span className="hero-led" />
          Antrean GPU
        </div>
        <h2>
          {g?.active
            ? "Model Anda sedang bekerja."
            : "Ruang untuk ide berikutnya."}
        </h2>
        <p>
          {g?.turn?.model ? (
            <>
              <span className="hero-model">{g.turn.model}</span> sedang memegang
              GPU.
            </>
          ) : (
            "GPU siap menerima request dari aplikasi Anda."
          )}
        </p>
        <div className="gpu-hero-stats">
          <div>
            <strong>
              {g ? used : "–"}
              <span> / {capacity || "–"}</span>
            </strong>
            <span>slot terpakai</span>
          </div>
          <div>
            <strong>{g ? number(g.waiting) : "–"}</strong>
            <span>request mengantre</span>
          </div>
          <div>
            <strong>{g ? number(g.stats.switches || 0) : "–"}</strong>
            <span>pergantian model</span>
          </div>
        </div>
        <Link href="/models/" className="hero-link">
          Kelola model
          <ArrowUpRight aria-hidden="true" />
        </Link>
      </div>
      <GpuCircuit active={!!g?.active} />
    </>
  );
  const model = info.data?.models[0] || "gemma3:4b";
  const base = info.data?.public_base_url || "<base-url>";
  const snippets: Record<string, string> = {
    python: `from openai import OpenAI\n\nclient = OpenAI(\n    base_url=${JSON.stringify(base)},\n    api_key="sk-...",\n)\nresponse = client.chat.completions.create(\n    model=${JSON.stringify(model)},\n    messages=[{"role": "user", "content": "Halo!"}],\n)\nprint(response.choices[0].message.content)`,
    javascript: `import OpenAI from "openai";\n\nconst client = new OpenAI({\n  baseURL: ${JSON.stringify(base)},\n  apiKey: "sk-...",\n});\nconst response = await client.chat.completions.create({\n  model: ${JSON.stringify(model)},\n  messages: [{ role: "user", content: "Halo!" }],\n});\nconsole.log(response.choices[0].message.content);`,
    curl: `curl ${JSON.stringify(`${base}/chat/completions`)} \\\n  -H "Authorization: Bearer sk-..." \\\n  -H "Content-Type: application/json" \\\n  -d '${JSON.stringify({ model, messages: [{ role: "user", content: "Halo!" }] })}'`,
  };
  return (
    <>
      <div className="overview-actions">
        <span className="period-label">
          <Clock3 />
          Hari ini, sejak 00.00 WIB
        </span>
        <div>
          <Button variant="outline" onClick={() => setAccess(true)}>
            <Terminal data-icon="inline-start" />
            Panduan akses
          </Button>
          <Button asChild>
            <Link href="/keys/">
              <Plus data-icon="inline-start" />
              Buat API key
            </Link>
          </Button>
        </div>
      </div>
      <Failure error={today.error} retry={() => void today.refetch()} />
      <div className="metrics-grid">
        {[
          {
            label: "Total request",
            value: sum("requests"),
            icon: Radio,
            note: "Semua request hari ini",
          },
          {
            label: "Token diproses",
            value: sum("total_tokens"),
            icon: Layers3,
            note: "Input dan jawaban model",
          },
          {
            label: "Request gagal",
            value: sum("errors"),
            icon: ArrowDownToLine,
            note: sum("requests")
              ? `${number(((sum("errors") || 0) / (sum("requests") || 1)) * 100)}% dari total request`
              : "Belum ada request hari ini",
          },
          {
            label: "Dalam antrean",
            value: g?.waiting ?? null,
            icon: Clock3,
            note: "Menunggu slot GPU",
          },
        ].map((metric) => (
          <div key={metric.label} className="metric">
            <div className="metric-title">
              <span>{metric.label}</span>
              <metric.icon aria-hidden="true" />
            </div>
            <strong>
              {metric.value == null ? "–" : compact(metric.value)}
            </strong>
            <span className="metric-note">{metric.note}</span>
          </div>
        ))}
      </div>
      {motionEnabled ? (
        <SpotlightCard
          className="gpu-hero"
          spotlightColor="#8ce8d8"
          intensity={0.12}
          ambient={false}
          flare={false}
          smoothing={0}
        >
          {heroContent}
        </SpotlightCard>
      ) : (
        <div className="gpu-hero">{heroContent}</div>
      )}
      <div className="overview-middle">
        <Panel
          title="Aktivitas request"
          description="Lihat bagaimana aplikasi Anda menggunakan provider."
          action={<span className="subtle-badge">24 jam</span>}
        >
          <Failure error={hourly.error} retry={() => void hourly.refetch()} />
          {hourly.data ? (
            <ActivityChart rows={hourly.data.data} start={start} />
          ) : (
            <Loading />
          )}
        </Panel>
        <Panel
          title="Jalur model"
          description="Kapasitas dan giliran setiap model."
          action={<Cpu className="panel-icon" />}
        >
          {g?.lanes.length ? (
            <div className="lane-list">
              {g.lanes.map((lane) => (
                <div key={lane.model} className="lane">
                  <div className="row-between">
                    <strong className="mono">{lane.model}</strong>
                    <StatusPill
                      kind={
                        g.turn?.model === lane.model
                          ? "ok"
                          : lane.waiting
                            ? "warn"
                            : "absent"
                      }
                    >
                      {g.turn?.model === lane.model
                        ? g.turn.yielding
                          ? "Bergantian"
                          : "Memakai GPU"
                        : lane.waiting
                          ? "Menunggu giliran"
                          : "Siaga"}
                    </StatusPill>
                  </div>
                  <div
                    className="lane-slot-track"
                    role="img"
                    aria-label={`${lane.running} dari ${lane.capacity} slot terpakai`}
                  >
                    {Array.from({ length: lane.capacity }, (_, index) => (
                      <span
                        key={index}
                        className={index < lane.running ? "slot-filled" : ""}
                      />
                    ))}
                  </div>
                  <div className="lane-meta">
                    <span>
                      {lane.running}/{lane.capacity} slot
                    </span>
                    <span>{lane.waiting} antre</span>
                  </div>
                  {lane.waiting > 0 && (
                    <p className="lane-wait">
                      Tunggu terlama {number(lane.oldest_wait_s)} dtk
                    </p>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <NoData
              title="GPU masih senggang"
              description="Jalur model muncul setelah request pertama diterima."
            />
          )}
          <div className="queue-note">
            {g ? (
              <>
                Maks. {g.max_queue} antre · giliran {g.switch_after_s} dtk
                <br />
                {number(g.stats.expired || 0)} habis waktu ·{" "}
                {number(g.stats.rejected || 0)} ditolak
              </>
            ) : (
              "Menunggu status antrean…"
            )}
          </div>
        </Panel>
      </div>
      <div className="overview-bottom">
        <Panel
          title="Request terbaru"
          description="Aktivitas terakhir yang diterima provider."
          action={
            <Button variant="ghost" asChild>
              <Link href="/requests/">
                Lihat semua
                <ArrowUpRight data-icon="inline-end" />
              </Link>
            </Button>
          }
        >
          <Failure error={recent.error} retry={() => void recent.refetch()} />
          {recent.data ? (
            recent.data.data.length ? (
              <div className="recent-list">
                {recent.data.data.map((row) => (
                  <div key={row.id} className="recent-item">
                    <span className="recent-icon">
                      <Braces />
                    </span>
                    <div>
                      <strong>{row.model || "Tanpa model"}</strong>
                      <span>
                        Key #{row.key_id} · {dateTime(row.ts)}
                      </span>
                    </div>
                    <div className="recent-result">
                      <RequestStatus code={row.status_code} />
                      <span>{latency(row.latency_ms)}</span>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <NoData
                title="Belum ada aktivitas"
                description="Buat API key dan mulai kirim request pertama Anda."
              />
            )
          ) : (
            <Loading />
          )}
        </Panel>
        <Panel
          title="Kesehatan provider"
          description="Dependency yang menjaga layanan tetap tersedia."
          action={<CheckCircle2 className="panel-icon" />}
        >
          <div className="dependency-list">
            {Object.entries(status.data?.components || {}).map(
              ([name, component]) => (
                <div key={name}>
                  <span>{componentName(name)}</span>
                  <StatusPill kind={component.status} />
                </div>
              ),
            )}
          </div>
          <p className="health-footnote">
            <KeyRound />
            Akses admin hanya melalui koneksi lokal.
          </p>
        </Panel>
      </div>
      <Overlay
        side
        open={access}
        onOpenChange={setAccess}
        title="Hubungkan aplikasi Anda"
        description="Gunakan SDK OpenAI dengan base URL dan API key provider."
      >
        <Failure error={info.error} retry={() => void info.refetch()} />
        {info.data?.public_base_url ? (
          <>
            <label className="field-caption">Base URL</label>
            <div className="url-copy">
              <code>{base}</code>
              <CopyButton text={base} label="Salin URL" />
            </div>
          </>
        ) : (
          <p className="notice">
            URL publik belum diatur. Atur PUBLIC_BASE_URL di konfigurasi server.
          </p>
        )}
        <Choices
          label="Bahasa contoh kode"
          value={language}
          onChange={setLanguage}
          options={[
            { value: "python", label: "Python" },
            { value: "javascript", label: "JavaScript" },
            { value: "curl", label: "cURL" },
          ]}
        />
        <pre className="code-block" tabIndex={0}>
          {snippets[language]}
        </pre>
        <CopyButton text={snippets[language]} label="Salin contoh kode" />
        <p className="muted">
          Untuk client browser, tambahkan header{" "}
          <code>ngrok-skip-browser-warning: 1</code> bila memakai ngrok. Simpan
          API key aplikasi di server aplikasi Anda.
        </p>
        {info.data?.client_guide_url && (
          <Button variant="outline" asChild>
            <a
              href={info.data.client_guide_url}
              target="_blank"
              rel="noreferrer"
            >
              Buka panduan client
              <ArrowUpRight data-icon="inline-end" />
            </a>
          </Button>
        )}
      </Overlay>
    </>
  );
}
