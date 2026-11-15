"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Pause, Play, RefreshCw } from "lucide-react";
import {
  Button,
  Failure,
  Loading,
  NoData,
  Panel,
  RequestStatus,
  TextField,
} from "@/components/common";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { useAuth } from "@/components/providers";
import { clock, dateTime, latency, number } from "@/lib/format";
import type { KeyList, RequestRow } from "@/lib/types";

export function Requests() {
  const { request } = useAuth();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [paused, setPaused] = useState(false);
  const keys = useQuery({
    queryKey: ["keys"],
    queryFn: ({ signal }) =>
      request<KeyList>("/keys?include_revoked=true", { signal }),
  });
  const query = useQuery({
    queryKey: ["recent", 200],
    queryFn: ({ signal }) =>
      request<{ data: RequestRow[] }>("/usage/recent?limit=200", { signal }),
    refetchInterval: paused ? false : 10000,
  });
  const names = new Map(keys.data?.data.map((key) => [key.id, key.name]));
  const keyData = keys.data;
  const rows = useMemo(() => {
    const keyNames = new Map(keyData?.data.map((key) => [key.id, key.name]));
    return (query.data?.data || [])
      .filter(
        (row) =>
          (status === "all" ||
            (status === "error"
              ? row.status_code >= 400
              : row.status_code < 400)) &&
          `${keyNames.get(row.key_id) || row.key_id} ${row.model} ${row.endpoint} ${row.client_ip} ${row.status_code}`
            .toLowerCase()
            .includes(search.toLowerCase()),
      )
      .slice(0, 100);
  }, [query.data, keyData, status, search]);
  return (
    <>
      <div className="overview-actions">
        <span className="period-label">
          <span
            className={paused ? "connection-dot attention" : "connection-dot"}
          />
          {paused ? "Pembaruan dijeda" : "Pembaruan otomatis setiap 10 detik"}
        </span>
        <div>
          <Button
            variant="outline"
            onClick={() => setPaused((value) => !value)}
          >
            {paused ? (
              <Play data-icon="inline-start" />
            ) : (
              <Pause data-icon="inline-start" />
            )}
            {paused ? "Lanjutkan" : "Jeda"}
          </Button>
          <Button
            variant="outline"
            disabled={query.isFetching}
            onClick={() => void query.refetch()}
          >
            <RefreshCw data-icon="inline-start" />
            Perbarui
          </Button>
        </div>
      </div>
      <Failure error={query.error} retry={() => void query.refetch()} />
      <Failure error={keys.error} retry={() => void keys.refetch()} />
      <Panel
        title="Lalu lintas terbaru"
        description="Filter berlaku pada 200 request terakhir; maksimal 100 hasil ditampilkan."
      >
        <FieldGroup className="filter-toolbar">
          <TextField
            id="request-search"
            label="Cari request"
            placeholder="Client, model, IP, atau status…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <Field>
            <FieldLabel htmlFor="request-status">Status</FieldLabel>
            <NativeSelect
              id="request-status"
              value={status}
              onChange={(event) => setStatus(event.target.value)}
            >
              <NativeSelectOption value="all">Semua status</NativeSelectOption>
              <NativeSelectOption value="error">
                Hanya yang gagal
              </NativeSelectOption>
              <NativeSelectOption value="ok">Berhasil</NativeSelectOption>
            </NativeSelect>
          </Field>
        </FieldGroup>
        {!query.data ? (
          <Loading />
        ) : rows.length ? (
          <>
            <div className="desktop-table table-scroll">
              <table>
                <caption className="sr-only">Request terbaru</caption>
                <thead>
                  <tr>
                    <th scope="col">Waktu (WIB)</th>
                    <th scope="col">Client</th>
                    <th scope="col">Model / endpoint</th>
                    <th scope="col">Status</th>
                    <th scope="col">Token</th>
                    <th scope="col">Latensi</th>
                    <th scope="col">IP</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.id}>
                      <td>{dateTime(row.ts)}</td>
                      <td>{names.get(row.key_id) || `Key #${row.key_id}`}</td>
                      <td>
                        <strong>{row.model || "Tanpa model"}</strong>
                        <span className="cell-secondary">
                          {row.endpoint}
                          {row.stream ? " · stream" : ""}
                        </span>
                      </td>
                      <td>
                        <RequestStatus code={row.status_code} />
                      </td>
                      <td>{number(row.total_tokens)}</td>
                      <td>{latency(row.latency_ms)}</td>
                      <td className="mono">{row.client_ip || "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mobile-records">
              {rows.map((row) => (
                <article key={row.id} className="record-card">
                  <div className="row-between">
                    <h3>{row.model || "Tanpa model"}</h3>
                    <RequestStatus code={row.status_code} />
                  </div>
                  <p>{names.get(row.key_id) || `Key #${row.key_id}`}</p>
                  <p className="muted">{dateTime(row.ts)}</p>
                  <div className="row-between">
                    <span>{number(row.total_tokens)} token</span>
                    <span>{latency(row.latency_ms)}</span>
                  </div>
                  <details>
                    <summary>Detail request</summary>
                    <dl className="detail-grid">
                      <div>
                        <dt>Endpoint</dt>
                        <dd>{row.endpoint}</dd>
                      </div>
                      <div>
                        <dt>IP</dt>
                        <dd>{row.client_ip || "–"}</dd>
                      </div>
                      <div>
                        <dt>Streaming</dt>
                        <dd>{row.stream ? "Ya" : "Tidak"}</dd>
                      </div>
                    </dl>
                  </details>
                </article>
              ))}
            </div>
          </>
        ) : (
          <NoData
            title="Tidak ada request untuk filter ini"
            description="Coba kata pencarian lain atau tampilkan semua status."
          />
        )}
        <div className="table-footer">
          <span>{number(rows.length)} request ditampilkan</span>
          <span>
            {query.dataUpdatedAt
              ? `Terakhir ${clock(query.dataUpdatedAt)} WIB`
              : "Belum diperbarui"}
          </span>
        </div>
      </Panel>
    </>
  );
}
