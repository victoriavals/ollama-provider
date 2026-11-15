"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Box,
  Download,
  HardDrive,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
import {
  Button,
  Failure,
  Loading,
  NoData,
  Overlay,
  Panel,
  StatusPill,
  SubmitButton,
  TextField,
} from "@/components/common";
import { FieldGroup } from "@/components/ui/field";
import { useAuth, useTask } from "@/components/providers";
import { bytes, number } from "@/lib/format";
import type { Model, PullJob } from "@/lib/types";

export function Models() {
  const { request } = useAuth();
  const client = useQueryClient();
  const [pullOpen, setPullOpen] = useState(false);
  const [name, setName] = useState("");
  const [enable, setEnable] = useState(false);
  const [deleting, setDeleting] = useState<Model | null>(null);
  const [notice, setNotice] = useState("");
  const [pendingToggle, setPendingToggle] = useState<{
    name: string;
    value: boolean;
  } | null>(null);
  const pullTask = useTask();
  const changeTask = useTask();
  const deleteTask = useTask();
  const jobs = useQuery({
    queryKey: ["pulls"],
    queryFn: ({ signal }) =>
      request<{ data: PullJob[] }>("/models/pull", { signal }),
    refetchInterval: (query) =>
      query.state.data?.data.some((job) => job.status === "running")
        ? 2000
        : false,
  });
  const running =
    jobs.data?.data.some((job) => job.status === "running") || false;
  const models = useQuery({
    queryKey: ["models"],
    queryFn: ({ signal }) => request<{ data: Model[] }>("/models", { signal }),
    refetchInterval: running ? 2000 : false,
  });
  const completed = jobs.data?.data
    .filter((job) => job.status === "success")
    .map((job) => job.id)
    .join(",");
  useEffect(() => {
    if (completed) {
      void client.invalidateQueries({ queryKey: ["models"] });
      void client.invalidateQueries({ queryKey: ["info"] });
    }
  }, [completed, client]);
  async function invalidate() {
    await Promise.all([
      client.invalidateQueries({ queryKey: ["models"] }),
      client.invalidateQueries({ queryKey: ["info"] }),
      client.invalidateQueries({ queryKey: ["status"] }),
    ]);
  }
  async function pull(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!name.trim()) return;
    await pullTask.run(async () => {
      await request("/models/pull", {
        method: "POST",
        body: { model: name.trim(), enable },
      });
      setName("");
      setEnable(false);
      setPullOpen(false);
      setNotice("Unduhan dimulai. Pantau progres di bawah.");
      await client.invalidateQueries({ queryKey: ["pulls"] });
    });
  }
  const installed = models.data?.data.filter((model) => model.installed) || [];
  const total = installed.reduce(
    (sum, model) => sum + (model.size_bytes || 0),
    0,
  );
  return (
    <>
      <div className="overview-actions">
        <span className="period-label">
          <HardDrive />
          {number(installed.length)} model terpasang · {bytes(total)}
        </span>
        <div>
          <Button
            variant="outline"
            disabled={models.isFetching || jobs.isFetching}
            onClick={() => {
              void invalidate();
              void jobs.refetch();
            }}
          >
            <RefreshCw data-icon="inline-start" />
            Perbarui
          </Button>
          <Button onClick={() => setPullOpen(true)}>
            <Plus data-icon="inline-start" />
            Unduh model
          </Button>
        </div>
      </div>
      {notice && (
        <p className="success-note" role="status">
          {notice}
        </p>
      )}
      <Failure error={models.error} retry={() => void models.refetch()} />
      <Failure error={jobs.error} retry={() => void jobs.refetch()} />
      <Failure error={changeTask.error} />
      <div className="model-notice">
        <Box />
        <p>
          Satu model memegang GPU pada satu waktu. Pergantian model dapat
          menambah waktu tunggu request pertama.
        </p>
      </div>
      {!models.data ? (
        <Loading />
      ) : models.data.data.length ? (
        <div className="model-grid">
          {models.data.data.map((model) => (
            <Panel
              key={model.name}
              title={model.name}
              description={
                model.installed
                  ? `${model.parameter_size || "Ukuran parameter tidak tersedia"} · ${model.quantization || "Kuantisasi tidak tersedia"}`
                  : "Belum terpasang di server"
              }
              action={
                <span className="model-initial">
                  {model.name.slice(0, 2).toUpperCase()}
                </span>
              }
            >
              <div className="model-card-status">
                <StatusPill
                  kind={
                    !model.installed
                      ? "absent"
                      : model.enabled
                        ? "ok"
                        : "absent"
                  }
                >
                  {model.installed
                    ? model.enabled
                      ? "Tersedia untuk client"
                      : "Akses client nonaktif"
                    : "Belum terpasang"}
                </StatusPill>
                <span>{bytes(model.size_bytes)}</span>
              </div>
              <div className="model-card-footer">
                <label className="check-control">
                  <input
                    type="checkbox"
                    checked={
                      pendingToggle?.name === model.name
                        ? pendingToggle.value
                        : model.enabled
                    }
                    disabled={
                      !model.installed ||
                      changeTask.pending ||
                      deleteTask.pending
                    }
                    aria-label={`Aktifkan ${model.name}`}
                    onChange={(event) => {
                      const enabled = event.target.checked;
                      setPendingToggle({ name: model.name, value: enabled });
                      void changeTask
                        .run(async () => {
                          await request(
                            `/models/${encodeURIComponent(model.name)}`,
                            { method: "PATCH", body: { enabled } },
                          );
                          setNotice(
                            `${model.name} ${enabled ? "diaktifkan" : "dinonaktifkan"}.`,
                          );
                          await invalidate();
                        })
                        .finally(() => setPendingToggle(null));
                    }}
                  />
                  {model.enabled ? "Aktif" : "Nonaktif"}
                </label>
                {model.installed ? (
                  <Button
                    variant="ghost"
                    size="icon"
                    disabled={changeTask.pending || deleteTask.pending}
                    aria-label={`Hapus ${model.name}`}
                    onClick={() => setDeleting(model)}
                  >
                    <Trash2 />
                  </Button>
                ) : (
                  <Button
                    variant="outline"
                    onClick={() => {
                      setName(model.name);
                      setPullOpen(true);
                    }}
                  >
                    Unduh
                  </Button>
                )}
              </div>
            </Panel>
          ))}
        </div>
      ) : (
        <NoData
          title="Siapkan model pertama Anda"
          description="Unduh model Ollama, lalu aktifkan agar dapat dipakai client."
          action={
            <Button onClick={() => setPullOpen(true)}>Unduh model</Button>
          }
        />
      )}
      <Panel
        title="Aktivitas unduhan"
        description="Progres diperbarui setiap 2 detik selama ada unduhan berjalan."
      >
        {jobs.data?.data.length ? (
          <div className="download-list">
            {jobs.data.data
              .slice(-5)
              .reverse()
              .map((job) => (
                <div key={job.id}>
                  <div className="row-between">
                    <strong>{job.model}</strong>
                    <StatusPill
                      kind={
                        job.status === "success"
                          ? "ok"
                          : job.status === "running"
                            ? "absent"
                            : "down"
                      }
                    >
                      {{
                        running: "Mengunduh",
                        success: "Selesai",
                        error: "Gagal",
                        cancelled: "Dibatalkan",
                      }[job.status] || job.status}
                    </StatusPill>
                  </div>
                  {job.status === "running" && (
                    <>
                      <progress
                        max={100}
                        value={job.percent ?? undefined}
                        aria-label={`Progres unduhan ${job.model}`}
                      />
                      <p className="muted">
                        {job.percent == null
                          ? "Menyiapkan unduhan…"
                          : `${number(job.percent)}%`}
                      </p>
                    </>
                  )}
                  {job.error && <p className="error-text">{job.error}</p>}
                </div>
              ))}
          </div>
        ) : (
          <div className="downloads-empty">
            <Download />
            <p>Belum ada aktivitas unduhan pada sesi server ini.</p>
          </div>
        )}
      </Panel>
      <Overlay
        side
        open={pullOpen}
        onOpenChange={(open) => {
          if (!pullTask.pending) setPullOpen(open);
        }}
        title="Unduh model Ollama"
        description="Masukkan nama dan tag model yang ingin disediakan untuk aplikasi."
      >
        <form onSubmit={(event) => void pull(event)}>
          <FieldGroup>
            <TextField
              id="pull-model"
              label="Nama model"
              required
              maxLength={200}
              spellCheck={false}
              placeholder="mis. gemma3:4b"
              value={name}
              onChange={(event) => setName(event.target.value)}
              error={pullTask.fields.model}
            />
            <label className="check-control">
              <input
                type="checkbox"
                checked={enable}
                onChange={(event) => setEnable(event.target.checked)}
              />
              Aktifkan untuk client setelah selesai
            </label>
            <p className="muted">
              Pastikan kapasitas disk dan VRAM cukup untuk model yang dipilih.
            </p>
            <Failure error={pullTask.error} />
            <div className="dialog-actions">
              <Button
                type="button"
                variant="outline"
                disabled={pullTask.pending}
                onClick={() => setPullOpen(false)}
              >
                Batal
              </Button>
              <SubmitButton pending={pullTask.pending}>
                <Download data-icon="inline-start" />
                Mulai unduhan
              </SubmitButton>
            </div>
          </FieldGroup>
        </form>
      </Overlay>
      <Overlay
        open={!!deleting}
        onOpenChange={(open) => {
          if (!open && !deleteTask.pending) setDeleting(null);
        }}
        title={`Hapus ${deleting?.name || "model"}?`}
        description="File model dihapus dari disk dan akses client dihentikan. Untuk memakainya lagi, model harus diunduh ulang."
      >
        <Failure error={deleteTask.error} />
        <div className="dialog-actions">
          <Button
            variant="outline"
            disabled={deleteTask.pending}
            onClick={() => setDeleting(null)}
          >
            Batal
          </Button>
          <Button
            variant="destructive"
            disabled={deleteTask.pending}
            onClick={() => {
              if (deleting)
                void deleteTask
                  .run(async () => {
                    await request(
                      `/models/${encodeURIComponent(deleting.name)}`,
                      { method: "DELETE" },
                    );
                    setDeleting(null);
                    setNotice("Model dihapus dari server.");
                    await invalidate();
                  })
                  .finally(() => setPendingToggle(null));
            }}
          >
            {deleteTask.pending ? "Menghapus…" : "Hapus model"}
          </Button>
        </div>
      </Overlay>
    </>
  );
}
