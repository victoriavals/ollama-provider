"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "./common";
import { number } from "@/lib/format";
import type { UsageRow } from "@/lib/types";

export function ActivityChart({
  rows,
  start,
}: {
  rows: UsageRow[];
  start: number;
}) {
  const [selected, setSelected] = useState(23);
  const root = useRef<HTMLElement>(null);
  const [width, setWidth] = useState(740);
  useEffect(() => {
    if (!root.current) return;
    const observer = new ResizeObserver((entries) =>
      setWidth(Math.max(280, entries[0].contentRect.width)),
    );
    observer.observe(root.current);
    return () => observer.disconnect();
  }, []);
  const step = (width - 48) / 24;
  const indexed = new Map(rows.map((row) => [row.hour, row]));
  const buckets = Array.from({ length: 24 }, (_, index) => {
    const date = new Date(start + index * 3600000);
    return { date, value: indexed.get(date.toISOString().slice(0, 13)) };
  });
  const maximum = Math.max(
    4,
    ...buckets.map((bucket) => bucket.value?.requests || 0),
  );
  const ceiling = Math.ceil(maximum / 4) * 4;
  const picked = buckets[selected];
  const hour = (date: Date) =>
    date.toLocaleTimeString("id-ID", {
      timeZone: "Asia/Jakarta",
      hour: "2-digit",
      minute: "2-digit",
    });
  return (
    <figure ref={root} className="activity-figure">
      <div className="chart-summary">
        <div>
          <span className="legend-dot" />
          Request
        </div>
        <span>24 jam terakhir (WIB)</span>
      </div>
      <svg
        viewBox={`0 0 ${width} 214`}
        role="img"
        aria-label="Aktivitas request selama 24 jam. Gunakan pemilih jam atau buka tabel data untuk rincian."
        onPointerDown={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          setSelected(
            Math.min(
              23,
              Math.max(
                0,
                Math.floor(
                  (((event.clientX - rect.left) / rect.width) * width - 42) /
                    step,
                ),
              ),
            ),
          );
        }}
      >
        {[0, 1, 2, 3, 4].map((index) => (
          <g key={index}>
            <line
              x1="42"
              x2={width - 6}
              y1={174 - index * 38}
              y2={174 - index * 38}
              className="chart-grid"
            />
            <text
              x="30"
              y={178 - index * 38}
              textAnchor="end"
              className="chart-axis"
            >
              {number((ceiling * index) / 4)}
            </text>
          </g>
        ))}
        {buckets.map((bucket, index) => {
          const height = ((bucket.value?.requests || 0) / ceiling) * 152;
          return (
            <g key={index}>
              <rect
                x={42 + index * step + step * 0.18}
                y={174 - height}
                width={step * 0.64}
                height={height}
                rx="3"
                className={
                  index === selected ? "chart-bar selected" : "chart-bar"
                }
              />
              {index % (width < 450 ? 6 : 4) === 0 && (
                <text
                  x={42 + index * step + step / 2}
                  y="202"
                  textAnchor="middle"
                  className="chart-axis"
                >
                  {hour(bucket.date).slice(0, 2)}.00
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <div className="chart-inspector">
        <Button
          variant="ghost"
          size="icon"
          aria-label="Jam sebelumnya"
          disabled={selected === 0}
          onClick={() => setSelected((value) => value - 1)}
        >
          <ChevronLeft />
        </Button>
        <label className="sr-only" htmlFor="chart-hour">
          Pilih jam pada grafik
        </label>
        <input
          id="chart-hour"
          type="range"
          min="0"
          max="23"
          value={selected}
          onChange={(event) => setSelected(Number(event.target.value))}
          aria-valuetext={`${hour(picked.date)} WIB, ${picked.value?.requests || 0} request`}
        />
        <Button
          variant="ghost"
          size="icon"
          aria-label="Jam berikutnya"
          disabled={selected === 23}
          onClick={() => setSelected((value) => value + 1)}
        >
          <ChevronRight />
        </Button>
      </div>
      <figcaption className="chart-caption" aria-live="polite">
        <strong>{hour(picked.date)} WIB</strong>
        <span>{number(picked.value?.requests || 0)} request</span>
        <span>{number(picked.value?.errors || 0)} gagal</span>
        <span>{number(picked.value?.total_tokens || 0)} token</span>
      </figcaption>
      <details className="data-disclosure">
        <summary>Lihat tabel data per jam</summary>
        <div className="table-scroll">
          <table>
            <caption className="sr-only">
              Request per jam selama 24 jam terakhir
            </caption>
            <thead>
              <tr>
                <th scope="col">Jam (WIB)</th>
                <th scope="col">Request</th>
                <th scope="col">Gagal</th>
                <th scope="col">Token</th>
              </tr>
            </thead>
            <tbody>
              {buckets.map((bucket, index) => (
                <tr key={index}>
                  <td>{hour(bucket.date)}</td>
                  <td>{number(bucket.value?.requests || 0)}</td>
                  <td>{number(bucket.value?.errors || 0)}</td>
                  <td>{number(bucket.value?.total_tokens || 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}
