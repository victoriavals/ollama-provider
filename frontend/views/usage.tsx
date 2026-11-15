"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  Layers3,
  Radio,
  RefreshCw,
  TriangleAlert,
} from "lucide-react";
import {
  Button,
  Choices,
  Failure,
  Loading,
  NoData,
  Panel,
} from "@/components/common";
import { useAuth } from "@/components/providers";
import { latency, number } from "@/lib/format";
import type { Usage as UsageData } from "@/lib/types";

export function Usage() {
  const { request } = useAuth();
  const [days, setDays] = useState("7");
  const [group, setGroup] = useState("key");
  const [windowEnd, setWindowEnd] = useState(() => Date.now());
  const since = new Date(windowEnd - Number(days) * 86400000).toISOString();
  const query = useQuery({
    queryKey: ["usage", days, group, since],
    queryFn: ({ signal }) =>
      request<UsageData>(
        `/usage?group_by=${group}&since=${encodeURIComponent(since)}`,
        { signal },
      ),
  });
  const rows = useMemo(
    () =>
      (query.data?.data || [])
        .map((row) => ({
          ...row,
          label:
            group === "key"
              ? row.key_name || `Key #${row.key}`
              : group === "model"
                ? row.model || "Tanpa model"
                : row.day || "–",
        }))
        .sort((a, b) =>
          group === "day"
            ? a.label.localeCompare(b.label)
            : b.requests - a.requests,
        ),
    [query.data, group],
  );
  const sum = (field: "requests" | "errors" | "total_tokens") =>
    rows.reduce((total, row) => total + row[field], 0);
  const maximum = Math.max(1, ...rows.map((row) => row.requests));
  return (
    <>
      <div className="filters-line">
        <Choices
          value={days}
          onChange={(value) => {
            setDays(value);
            setWindowEnd(Date.now());
          }}
          label="Rentang pemakaian"
          options={[
            { value: "1", label: "24 jam" },
            { value: "7", label: "7 hari" },
            { value: "30", label: "30 hari" },
          ]}
        />
        <Choices
          value={group}
          onChange={setGroup}
          label="Kelompok pemakaian"
          options={[
            { value: "key", label: "Per key" },
            { value: "model", label: "Per model" },
            { value: "day", label: "Per hari (UTC)" },
          ]}
        />
        <Button
          variant="outline"
          disabled={query.isFetching}
          onClick={() => setWindowEnd(Date.now())}
        >
          <RefreshCw data-icon="inline-start" />
          Perbarui
        </Button>
      </div>
      <Failure error={query.error} retry={() => void query.refetch()} />
      {!query.data ? (
        <Loading />
      ) : (
        <>
          <div className="metrics-grid three">
            {[
              { label: "Request", value: sum("requests"), icon: Radio },
              { label: "Token", value: sum("total_tokens"), icon: Layers3 },
              {
                label: "Request gagal",
                value: sum("errors"),
                icon: TriangleAlert,
              },
            ].map((metric) => (
              <div className="metric" key={metric.label}>
                <div className="metric-title">
                  <span>{metric.label}</span>
                  <metric.icon />
                </div>
                <strong>{number(metric.value)}</strong>
                <span className="metric-note">
                  Dalam {days === "1" ? "24 jam" : `${days} hari`} terakhir
                </span>
              </div>
            ))}
          </div>
          <Panel
            title="Distribusi pemakaian"
            description={
              group === "day"
                ? "Batas hari mengikuti UTC sesuai agregasi server."
                : "Diurutkan berdasarkan jumlah request."
            }
            action={<BarChart3 className="panel-icon" />}
          >
            {rows.length ? (
              <>
                <div className="usage-bars">
                  {rows.slice(0, 12).map((row) => (
                    <div key={row.label}>
                      <div className="row-between">
                        <strong>{row.label}</strong>
                        <span>{number(row.requests)} request</span>
                      </div>
                      <progress
                        max={maximum}
                        value={row.requests}
                        aria-label={`Request ${row.label}`}
                      />
                    </div>
                  ))}
                </div>
                <div className="desktop-table table-scroll">
                  <table>
                    <caption className="sr-only">
                      Rincian pemakaian{" "}
                      {group === "day" ? "per hari UTC" : `per ${group}`}
                    </caption>
                    <thead>
                      <tr>
                        <th scope="col">
                          {group === "key"
                            ? "Client"
                            : group === "model"
                              ? "Model"
                              : "Hari (UTC)"}
                        </th>
                        <th scope="col">Request</th>
                        <th scope="col">Gagal</th>
                        <th scope="col">Token input</th>
                        <th scope="col">Token output</th>
                        <th scope="col">Latensi rata-rata</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((row) => (
                        <tr key={row.label}>
                          <td>
                            <strong>{row.label}</strong>
                          </td>
                          <td>{number(row.requests)}</td>
                          <td>{number(row.errors)}</td>
                          <td>{number(row.prompt_tokens)}</td>
                          <td>{number(row.completion_tokens)}</td>
                          <td>{latency(row.avg_latency_ms)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="mobile-records">
                  {rows.map((row) => (
                    <article key={row.label} className="record-card">
                      <h3>{row.label}</h3>
                      <dl className="detail-grid">
                        <div>
                          <dt>Request</dt>
                          <dd>{number(row.requests)}</dd>
                        </div>
                        <div>
                          <dt>Gagal</dt>
                          <dd>{number(row.errors)}</dd>
                        </div>
                        <div>
                          <dt>Token input</dt>
                          <dd>{number(row.prompt_tokens)}</dd>
                        </div>
                        <div>
                          <dt>Token output</dt>
                          <dd>{number(row.completion_tokens)}</dd>
                        </div>
                        <div>
                          <dt>Latensi rata-rata</dt>
                          <dd>{latency(row.avg_latency_ms)}</dd>
                        </div>
                      </dl>
                    </article>
                  ))}
                </div>
              </>
            ) : (
              <NoData
                title="Belum ada pemakaian"
                description="Request aplikasi akan muncul di sini setelah diproses. Coba rentang waktu yang lebih panjang."
              />
            )}
          </Panel>
        </>
      )}
    </>
  );
}
