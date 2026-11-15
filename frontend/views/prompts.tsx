"use client";

import { useMemo, useState, type FormEvent } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { ChevronRight, Filter, ShieldCheck } from "lucide-react";
import {
  Button,
  CopyButton,
  Failure,
  Loading,
  NoData,
  Overlay,
  Panel,
  RequestStatus,
  TextField,
} from "@/components/common";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { useAuth, useInfo } from "@/components/providers";
import { dateTime, number } from "@/lib/format";
import type { KeyList, PromptDetail, PromptList } from "@/lib/types";

const ranges: Record<string, number> = {
  "1h": 3600000,
  "24h": 86400000,
  "7d": 7 * 86400000,
  "30d": 30 * 86400000,
};
const roles: Record<string, string> = {
  system: "Sistem",
  user: "User",
  assistant: "Asisten",
  tool: "Hasil tool",
  tools: "Daftar tools",
  prompt: "Prompt",
  input: "Input",
};

export function Prompts() {
  const { request } = useAuth();
  const info = useInfo();
  const keys = useQuery({
    queryKey: ["keys"],
    queryFn: ({ signal }) =>
      request<KeyList>("/keys?include_revoked=true", { signal }),
  });
  const [filters, setFilters] = useState({
    range: "24h",
    key: "",
    model: "",
    status: "all",
    q: "",
  });
  const [applied, setApplied] = useState(() => ({
    ...filters,
    since: new Date(Date.now() - ranges["24h"]).toISOString(),
  }));
  const [detail, setDetail] = useState<number | null>(null);
  const query = useInfiniteQuery({
    queryKey: ["prompts", applied],
    initialPageParam: null as string | null,
    queryFn: ({ signal, pageParam }) => {
      const params = new URLSearchParams({ since: applied.since, limit: "50" });
      if (applied.key) params.set("key_id", applied.key);
      if (applied.model) params.set("model", applied.model);
      if (applied.status !== "all") params.set("status", applied.status);
      if (applied.q) params.set("q", applied.q);
      if (pageParam) params.set("before", pageParam);
      return request<PromptList>(`/prompts?${params}`, { signal });
    },
    getNextPageParam: (lastPage, pages) =>
      pages.length >= 10 ? undefined : lastPage.next || undefined,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    gcTime: 0,
  });
  const rows = useMemo(
    () => query.data?.pages.flatMap((page) => page.data).slice(0, 500) || [],
    [query.data],
  );
  const meta = query.data?.pages[0];
  const dirty = (["range", "key", "model", "status", "q"] as const).some(
    (field) => filters[field] !== applied[field],
  );
  const set = (field: keyof typeof filters, value: string) =>
    setFilters((previous) => ({ ...previous, [field]: value }));
  function apply(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const search =
      event.currentTarget.querySelector<HTMLInputElement>("#prompt-search");
    if (filters.q.trim() && filters.q.trim().length < 3) {
      search?.setCustomValidity("Masukkan minimal 3 huruf untuk mencari teks.");
      search?.reportValidity();
      return;
    }
    setApplied({
      ...filters,
      q: filters.q.trim(),
      since: new Date(Date.now() - ranges[filters.range]).toISOString(),
    });
  }
  return (
    <>
      <div className="privacy-note">
        <ShieldCheck />
        <p>
          Prompt dan jawaban hanya tersedia untuk admin.{" "}
          {meta
            ? meta.enabled
              ? `Retensi ${meta.retention_days} hari.`
              : "Pencatatan sedang dimatikan; daftar berisi catatan lama."
            : "Memeriksa konfigurasi retensi…"}{" "}
          Daftar diperbarui saat Anda menerapkan filter.
        </p>
      </div>
      <Panel
        title="Telusuri percakapan"
        description="Cari dengan rentang waktu yang sesuai; isi lengkap dibuka ketika dibutuhkan."
      >
        <form onSubmit={apply}>
          <FieldGroup className="prompt-filters">
            <Field>
              <FieldLabel htmlFor="prompt-range">Rentang</FieldLabel>
              <NativeSelect
                id="prompt-range"
                value={filters.range}
                onChange={(event) => set("range", event.target.value)}
              >
                <NativeSelectOption value="1h">
                  1 jam terakhir
                </NativeSelectOption>
                <NativeSelectOption value="24h">
                  24 jam terakhir
                </NativeSelectOption>
                <NativeSelectOption value="7d">
                  7 hari terakhir
                </NativeSelectOption>
                <NativeSelectOption value="30d">
                  30 hari terakhir
                </NativeSelectOption>
              </NativeSelect>
            </Field>
            <Field>
              <FieldLabel htmlFor="prompt-key">Client</FieldLabel>
              <NativeSelect
                id="prompt-key"
                value={filters.key}
                onChange={(event) => set("key", event.target.value)}
              >
                <NativeSelectOption value="">Semua key</NativeSelectOption>
                {keys.data?.data.map((key) => (
                  <NativeSelectOption key={key.id} value={key.id}>
                    {key.name}
                    {key.revoked_at ? " (dicabut)" : ""}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            </Field>
            <Field>
              <FieldLabel htmlFor="prompt-model">Model</FieldLabel>
              <NativeSelect
                id="prompt-model"
                value={filters.model}
                onChange={(event) => set("model", event.target.value)}
              >
                <NativeSelectOption value="">Semua model</NativeSelectOption>
                {info.data?.models.map((model) => (
                  <NativeSelectOption key={model} value={model}>
                    {model}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            </Field>
            <Field>
              <FieldLabel htmlFor="prompt-status">Status</FieldLabel>
              <NativeSelect
                id="prompt-status"
                value={filters.status}
                onChange={(event) => set("status", event.target.value)}
              >
                <NativeSelectOption value="all">
                  Semua status
                </NativeSelectOption>
                <NativeSelectOption value="ok">Berhasil</NativeSelectOption>
                <NativeSelectOption value="error">
                  Gagal / ditolak
                </NativeSelectOption>
              </NativeSelect>
            </Field>
            <TextField
              id="prompt-search"
              label="Cari teks"
              maxLength={200}
              value={filters.q}
              onChange={(event) => {
                event.target.setCustomValidity("");
                set("q", event.target.value);
              }}
              placeholder="Minimal 3 huruf…"
            />
            <Button
              type="submit"
              disabled={query.isFetching && !query.isFetchingNextPage}
            >
              <Filter data-icon="inline-start" />
              Terapkan
            </Button>
          </FieldGroup>
        </form>
        {dirty && (
          <p className="filter-dirty" role="status">
            Filter berubah. Klik Terapkan untuk memperbarui hasil.
          </p>
        )}
      </Panel>
      <Failure error={query.error} retry={() => void query.refetch()} />
      <Failure error={keys.error} retry={() => void keys.refetch()} />
      <Panel
        title="Log prompt"
        description={`${number(rows.length)} percakapan ditampilkan. Cuplikan tidak memuat seluruh percakapan.`}
      >
        {!query.data ? (
          <Loading />
        ) : rows.length ? (
          <>
            <div className="desktop-table table-scroll">
              <table>
                <caption className="sr-only">
                  Cuplikan log prompt dan jawaban
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Client / waktu</th>
                    <th scope="col">Model</th>
                    <th scope="col">Prompt</th>
                    <th scope="col">Jawaban</th>
                    <th scope="col">Status</th>
                    <th scope="col">
                      <span className="sr-only">Detail</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.id}>
                      <td>
                        <strong>{row.key_name || `Key #${row.key_id}`}</strong>
                        <span className="cell-secondary">
                          {dateTime(row.ts)}
                        </span>
                        <span className="cell-secondary mono">
                          {row.client_ip || "–"}
                          {row.end_user ? ` / ${row.end_user}` : ""}
                        </span>
                      </td>
                      <td>
                        {row.model || "–"}
                        <span className="cell-secondary">
                          {number(row.total_tokens)} token
                        </span>
                      </td>
                      <td>
                        <span className="text-preview">
                          {row.prompt_preview || "–"}
                        </span>
                      </td>
                      <td>
                        <span className="text-preview">
                          {row.response_preview || "–"}
                        </span>
                      </td>
                      <td>
                        <RequestStatus code={row.status_code} />
                      </td>
                      <td>
                        <Button
                          variant="ghost"
                          onClick={() => setDetail(row.id)}
                          aria-label={`Lihat prompt ${row.id}`}
                        >
                          Lihat
                          <ChevronRight data-icon="inline-end" />
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mobile-records">
              {rows.map((row) => (
                <article key={row.id} className="record-card">
                  <div className="row-between">
                    <h3>{row.key_name || `Key #${row.key_id}`}</h3>
                    <RequestStatus code={row.status_code} />
                  </div>
                  <p className="muted">
                    {row.model} · {dateTime(row.ts)}
                  </p>
                  <p className="text-preview">
                    {row.prompt_preview || "Prompt kosong"}
                  </p>
                  <div className="row-between">
                    <span>{number(row.total_tokens)} token</span>
                    <Button
                      variant="outline"
                      onClick={() => setDetail(row.id)}
                      aria-label={`Lihat prompt ${row.id}`}
                    >
                      Lihat percakapan
                    </Button>
                  </div>
                </article>
              ))}
            </div>
          </>
        ) : (
          <NoData
            title="Tidak ada prompt untuk filter ini"
            description="Coba rentang lebih panjang, client lain, atau kata pencarian yang berbeda."
          />
        )}
        <div className="load-more">
          {query.hasNextPage && rows.length < 500 && (
            <Button
              variant="outline"
              disabled={query.isFetching}
              onClick={() => void query.fetchNextPage({ cancelRefetch: false })}
            >
              {query.isFetchingNextPage ? "Memuat…" : "Muat 50 lagi"}
            </Button>
          )}
          <span className="muted">
            {rows.length >= 500
              ? "Batas 500 baris tercapai. Persempit filter untuk melihat hasil lainnya."
              : `${number(rows.length)} prompt ditampilkan`}
          </span>
        </div>
      </Panel>
      {detail != null && (
        <PromptViewer id={detail} onClose={() => setDetail(null)} />
      )}
    </>
  );
}

function PromptViewer({ id, onClose }: { id: number; onClose: () => void }) {
  const { request } = useAuth();
  const query = useQuery({
    queryKey: ["prompt", id],
    queryFn: ({ signal }) =>
      request<PromptDetail>(`/prompts/${id}`, { signal }),
    gcTime: 0,
  });
  const row = query.data;
  const text = row
    ? [
        ...row.prompt.map(
          (message) =>
            `[${roles[message.role] || message.role}]\n${message.content}`,
        ),
        `[Jawaban]\n${row.response ?? "(tidak ada jawaban)"}`,
      ].join("\n\n")
    : "";
  return (
    <Overlay
      side
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={
        row
          ? `Percakapan ${row.key_name || `key #${row.key_id}`}`
          : "Memuat percakapan"
      }
      description="Isi lengkap prompt dan jawaban. Hanya terlihat oleh administrator."
    >
      <Failure error={query.error} retry={() => void query.refetch()} />
      {row ? (
        <>
          <dl className="detail-grid">
            <div>
              <dt>Waktu</dt>
              <dd>{dateTime(row.ts)}</dd>
            </div>
            <div>
              <dt>Model</dt>
              <dd>{row.model || "–"}</dd>
            </div>
            <div>
              <dt>Status</dt>
              <dd>
                <RequestStatus code={row.status_code} />
              </dd>
            </div>
            <div>
              <dt>Token</dt>
              <dd>{number(row.total_tokens)}</dd>
            </div>
            <div>
              <dt>IP / user</dt>
              <dd>
                {row.client_ip || "–"} / {row.end_user || "–"}
              </dd>
            </div>
            <div>
              <dt>Request ID</dt>
              <dd className="mono">{row.request_id || "–"}</dd>
            </div>
            <div>
              <dt>Endpoint</dt>
              <dd>
                {row.endpoint}
                {row.stream ? " (stream)" : ""}
              </dd>
            </div>
          </dl>
          {row.prompt.map((message, index) => (
            <section key={index} className="message-section">
              <h3>{roles[message.role] || message.role}</h3>
              <pre tabIndex={0} className="code-block wrap">
                {message.content || "(kosong)"}
              </pre>
            </section>
          ))}
          <section className="message-section answer-section">
            <h3>Jawaban model</h3>
            <pre tabIndex={0} className="code-block wrap">
              {row.response ??
                (row.status_code >= 400
                  ? "(ditolak sebelum sampai ke model)"
                  : "(tidak ada jawaban)")}
            </pre>
          </section>
          <CopyButton text={text} label="Salin percakapan" />
        </>
      ) : (
        !query.error && <Loading />
      )}
    </Overlay>
  );
}
