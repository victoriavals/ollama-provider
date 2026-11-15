"use client";

import { useMemo, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  KeyRound,
  MessageSquareText,
  Pencil,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import {
  Button,
  CopyButton,
  Failure,
  Loading,
  NoData,
  Overlay,
  Panel,
  StatusPill,
  SubmitButton,
  TextField,
} from "@/components/common";
import {
  Field,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { Badge } from "@/components/ui/badge";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { useAuth, useInfo, useTask } from "@/components/providers";
import {
  clientMessage,
  dateTime,
  keyState,
  lastValidDate,
  number,
} from "@/lib/format";
import type { ApiKey, CreatedKey, Info, KeyList } from "@/lib/types";

export function Keys() {
  const { request } = useAuth();
  const info = useInfo();
  const client = useQueryClient();
  const keys = useQuery({
    queryKey: ["keys"],
    queryFn: ({ signal }) =>
      request<KeyList>("/keys?include_revoked=true", { signal }),
  });
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("active");
  const [editor, setEditor] = useState<ApiKey | "new" | null>(null);
  const [created, setCreated] = useState<CreatedKey | null>(null);
  const [message, setMessage] = useState<ApiKey | null>(null);
  const [revoking, setRevoking] = useState<ApiKey | null>(null);
  const [success, setSuccess] = useState("");
  const revokeTask = useTask();
  const rows = useMemo(
    () =>
      (keys.data?.data || []).filter(
        (key) =>
          (filter !== "active" || !key.revoked_at) &&
          (filter !== "revoked" || key.revoked_at) &&
          `${key.name} ${key.prefix}`
            .toLowerCase()
            .includes(search.toLowerCase()),
      ),
    [keys.data, filter, search],
  );
  async function invalidate() {
    await client.invalidateQueries({ queryKey: ["keys"] });
  }
  return (
    <>
      <div className="overview-actions">
        <p className="muted">
          {keys.data
            ? `${number(keys.data.data.filter((key) => !key.revoked_at).length)} key belum dicabut`
            : "Kelola akses setiap aplikasi secara terpisah."}
        </p>
        <div>
          <Button
            variant="outline"
            disabled={keys.isFetching}
            onClick={() => void keys.refetch()}
          >
            <RefreshCw data-icon="inline-start" />
            Perbarui
          </Button>
          <Button onClick={() => setEditor("new")} disabled={!info.data}>
            <Plus data-icon="inline-start" />
            Buat API key
          </Button>
        </div>
      </div>
      {success && (
        <p className="success-note" role="status">
          <ShieldCheck />
          {success}
        </p>
      )}
      <Failure error={keys.error} retry={() => void keys.refetch()} />
      <Failure error={info.error} retry={() => void info.refetch()} />
      <Panel
        title="Akses aplikasi"
        description="Atur batas, masa berlaku, dan model untuk setiap client."
      >
        <FieldGroup className="filter-toolbar">
          <TextField
            id="key-search"
            label="Cari key"
            placeholder="Nama client atau prefix…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <Field>
            <label htmlFor="key-status" className="field-caption">
              Status key
            </label>
            <NativeSelect
              id="key-status"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            >
              <NativeSelectOption value="active">
                Belum dicabut
              </NativeSelectOption>
              <NativeSelectOption value="all">Semua key</NativeSelectOption>
              <NativeSelectOption value="revoked">Dicabut</NativeSelectOption>
            </NativeSelect>
          </Field>
        </FieldGroup>
        {!keys.data ? (
          <Loading />
        ) : rows.length ? (
          <div className="responsive-records">
            <div className="desktop-table table-scroll">
              <table>
                <caption className="sr-only">Daftar API key aplikasi</caption>
                <thead>
                  <tr>
                    <th scope="col">Client</th>
                    <th scope="col">Status</th>
                    <th scope="col">Batas akses</th>
                    <th scope="col">Pemakaian token</th>
                    <th scope="col">Terakhir dipakai</th>
                    <th scope="col">
                      <span className="sr-only">Aksi</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((key) => (
                    <tr key={key.id}>
                      <td>
                        <strong>{key.name}</strong>
                        <span className="cell-secondary mono">
                          {key.prefix}…
                        </span>
                        {key.priority > 0 && (
                          <Badge variant="secondary">
                            Prioritas {key.priority}
                          </Badge>
                        )}
                      </td>
                      <td>
                        <StatusPill kind={keyState(key).kind}>
                          {keyState(key).text}
                        </StatusPill>
                      </td>
                      <td>
                        <span>
                          {number(key.rpm_limit_effective)}/menit ·{" "}
                          {key.max_concurrent_effective} paralel
                        </span>
                        <span className="cell-secondary">
                          {key.allowed_models?.join(", ") ||
                            "Semua model aktif"}
                        </span>
                        {key.expires_at && (
                          <span className="cell-secondary">
                            Sampai {lastValidDate(key.expires_at)}
                          </span>
                        )}
                        {key.log_prompts === 0 && (
                          <Badge variant="secondary">
                            Prompt tidak dicatat
                          </Badge>
                        )}
                      </td>
                      <td>
                        <Quota keyData={key} />
                      </td>
                      <td>{dateTime(key.last_used_at)}</td>
                      <td>
                        <div className="row-actions">
                          <Button
                            variant="ghost"
                            size="icon"
                            disabled={!!key.revoked_at}
                            aria-label={`Ubah ${key.name}`}
                            onClick={() => setEditor(key)}
                          >
                            <Pencil />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={`Pesan untuk ${key.name}`}
                            onClick={() => setMessage(key)}
                          >
                            <MessageSquareText />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            disabled={!!key.revoked_at}
                            aria-label={`Cabut ${key.name}`}
                            onClick={() => setRevoking(key)}
                          >
                            <Trash2 />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mobile-records">
              {rows.map((key) => (
                <article key={key.id} className="record-card">
                  <div className="row-between">
                    <div>
                      <h3>{key.name}</h3>
                      <code>{key.prefix}…</code>
                    </div>
                    <StatusPill kind={keyState(key).kind}>
                      {keyState(key).text}
                    </StatusPill>
                  </div>
                  <p>{key.allowed_models?.join(", ") || "Semua model aktif"}</p>
                  <p className="muted">
                    {number(key.rpm_limit_effective)}/menit ·{" "}
                    {key.max_concurrent_effective} paralel · prioritas{" "}
                    {key.priority}
                  </p>
                  <Quota keyData={key} />
                  <details>
                    <summary>Detail akses</summary>
                    <p>Terakhir dipakai: {dateTime(key.last_used_at)}</p>
                    <p>
                      Masa berlaku:{" "}
                      {key.expires_at
                        ? lastValidDate(key.expires_at)
                        : "Tanpa batas"}
                    </p>
                    <p>
                      Log prompt:{" "}
                      {key.log_prompts === 0 ? "Dimatikan" : "Aktif"}
                    </p>
                  </details>
                  <div className="row-actions">
                    <Button
                      variant="outline"
                      disabled={!!key.revoked_at}
                      onClick={() => setEditor(key)}
                    >
                      Ubah
                    </Button>
                    <Button variant="outline" onClick={() => setMessage(key)}>
                      Pesan
                    </Button>
                    <Button
                      variant="ghost"
                      disabled={!!key.revoked_at}
                      onClick={() => setRevoking(key)}
                    >
                      Cabut
                    </Button>
                  </div>
                </article>
              ))}
            </div>
          </div>
        ) : (
          <NoData
            title={
              search ? "Key tidak ditemukan" : "Belum ada key pada daftar ini"
            }
            description={
              search
                ? "Coba nama atau prefix lain, atau ubah filter status."
                : "Buat API key pertama untuk menghubungkan aplikasi Anda."
            }
            action={
              !search && (
                <Button onClick={() => setEditor("new")} disabled={!info.data}>
                  <KeyRound data-icon="inline-start" />
                  Buat API key
                </Button>
              )
            }
          />
        )}
        <div className="table-footer">
          <span>{number(rows.length)} key ditampilkan</span>
          <span>
            {keys.data
              ? `Kuota harian reset ${dateTime(keys.data.quota_resets.day)}`
              : "Kuota mengikuti waktu server."}
          </span>
        </div>
      </Panel>
      {editor && info.data && (
        <KeyEditor
          key={editor === "new" ? "new" : editor.id}
          value={editor === "new" ? undefined : editor}
          info={info.data}
          onClose={() => setEditor(null)}
          onSaved={async (result) => {
            setEditor(null);
            if (result) setCreated(result);
            else setSuccess("Perubahan key disimpan.");
            await invalidate();
          }}
        />
      )}
      <Overlay
        open={!!created}
        onOpenChange={(open) => {
          if (!open) setCreated(null);
        }}
        title={`Key untuk ${created?.name || "client"} siap digunakan`}
        description="Salin dan simpan sekarang. Secret key ini hanya ditampilkan satu kali."
      >
        {created && info.data && (
          <>
            <div className="secret-box">
              <code>{created.key}</code>
            </div>
            <CopyButton text={created.key} label="Salin key" />
            <p className="field-caption">Pesan siap kirim untuk client</p>
            <pre className="code-block wrap" tabIndex={0}>
              {clientMessage(created, info.data, created.key)}
            </pre>
            <CopyButton
              text={clientMessage(created, info.data, created.key)}
              label="Salin pesan"
            />
            <Button onClick={() => setCreated(null)}>Sudah disimpan</Button>
          </>
        )}
      </Overlay>
      <Overlay
        side
        open={!!message}
        onOpenChange={(open) => {
          if (!open) setMessage(null);
        }}
        title={`Pesan untuk ${message?.name || "client"}`}
        description="Pesan ini merujuk ke key yang sudah diberikan. Secret key lama tidak dapat ditampilkan kembali."
      >
        {message && info.data && (
          <>
            <pre className="code-block wrap" tabIndex={0}>
              {clientMessage(message, info.data)}
            </pre>
            <CopyButton
              text={clientMessage(message, info.data)}
              label="Salin pesan"
            />
          </>
        )}
      </Overlay>
      <Overlay
        open={!!revoking}
        onOpenChange={(open) => {
          if (!open && !revokeTask.pending) setRevoking(null);
        }}
        title={`Cabut key ${revoking?.name || ""}?`}
        description={`Client dengan prefix ${revoking?.prefix || ""}… akan langsung kehilangan akses. Key yang dicabut tidak dapat dipulihkan.`}
      >
        <Failure error={revokeTask.error} />
        <div className="dialog-actions">
          <Button
            variant="outline"
            disabled={revokeTask.pending}
            onClick={() => setRevoking(null)}
          >
            Batal
          </Button>
          <Button
            variant="destructive"
            disabled={revokeTask.pending}
            aria-busy={revokeTask.pending}
            onClick={() => {
              if (revoking)
                void revokeTask.run(async () => {
                  await request(`/keys/${revoking.id}`, { method: "DELETE" });
                  setRevoking(null);
                  setSuccess("Key dicabut.");
                  await invalidate();
                });
            }}
          >
            {revokeTask.pending ? "Mencabut…" : "Cabut key"}
          </Button>
        </div>
      </Overlay>
    </>
  );
}

function Quota({ keyData }: { keyData: ApiKey }) {
  return (
    <div className="quota-list">
      {[
        ["Hari", keyData.usage.day_tokens, keyData.daily_token_limit],
        ["Bulan", keyData.usage.month_tokens, keyData.monthly_token_limit],
      ].map(([label, used, limit]) => (
        <div key={label}>
          <div className="row-between">
            <span>{label}</span>
            <span>
              {number(used as number)}
              {limit != null
                ? ` / ${number(limit as number)}`
                : " · tanpa batas"}
            </span>
          </div>
          {limit != null && (
            <progress
              value={Math.min(used as number, limit as number)}
              max={limit as number}
              aria-label={`Kuota token per ${String(label).toLowerCase()}`}
            />
          )}
        </div>
      ))}
    </div>
  );
}

function KeyEditor({
  value,
  info,
  onClose,
  onSaved,
}: {
  value?: ApiKey;
  info: Info;
  onClose: () => void;
  onSaved: (created?: CreatedKey) => Promise<void>;
}) {
  const { request } = useAuth();
  const task = useTask();
  const initial = {
    name: value?.name || "",
    rpm_limit: value?.rpm_limit?.toString() || "",
    max_concurrent: value?.max_concurrent?.toString() || "",
    daily_token_limit: value?.daily_token_limit?.toString() || "",
    monthly_token_limit: value?.monthly_token_limit?.toString() || "",
    expires_at: lastValidDate(value?.expires_at || null),
    allowed_models: value?.allowed_models || [],
    all_models: !value?.allowed_models,
    priority: String(value?.priority || 0),
    log_prompts: value?.log_prompts !== 0,
  };
  const [form, setForm] = useState(initial);
  const [modelsError, setModelsError] = useState("");
  const models = [
    ...new Set([...info.models, ...(value?.allowed_models || [])]),
  ];
  const set = (key: keyof typeof form, input: string | boolean | string[]) =>
    setForm((previous) => ({ ...previous, [key]: input }));
  const intOrNull = (input: string) => (input === "" ? null : Number(input));
  const serialize = (state: typeof form) => ({
    name: state.name.trim(),
    rpm_limit: intOrNull(state.rpm_limit),
    max_concurrent: intOrNull(state.max_concurrent),
    daily_token_limit: intOrNull(state.daily_token_limit),
    monthly_token_limit: intOrNull(state.monthly_token_limit),
    expires_at: state.expires_at || null,
    allowed_models: state.all_models ? null : [...state.allowed_models].sort(),
    priority: Number(state.priority),
    log_prompts: state.log_prompts,
  });
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!form.name.trim()) {
      const input =
        event.currentTarget.querySelector<HTMLInputElement>("#client-name");
      input?.setCustomValidity("Masukkan nama client.");
      input?.reportValidity();
      return;
    }
    if (!form.all_models && !form.allowed_models.length) {
      setModelsError("Pilih minimal satu model, atau izinkan semua model.");
      document.getElementById("model-access")?.focus();
      return;
    }
    setModelsError("");
    const body = serialize(form);
    const before = serialize(initial);
    const changed = Object.fromEntries(
      Object.entries(body).filter(
        ([field, next]) =>
          JSON.stringify(next) !==
          JSON.stringify(before[field as keyof typeof before]),
      ),
    );
    await task.run(async () => {
      if (value) {
        if (Object.keys(changed).length)
          await request(`/keys/${value.id}`, {
            method: "PATCH",
            body: changed,
          });
        await onSaved();
      } else {
        const result = await request<CreatedKey>("/keys", {
          method: "POST",
          body,
        });
        await onSaved(result);
      }
    });
  }
  return (
    <Overlay
      side
      open
      onOpenChange={(open) => {
        if (!open && !task.pending) onClose();
      }}
      title={value ? `Ubah key ${value.name}` : "Buat API key"}
      description="Berikan akses yang sesuai untuk aplikasi Anda. Batas yang kosong mengikuti default server."
    >
      <form onSubmit={(event) => void submit(event)}>
        <FieldGroup>
          <FieldSet>
            <FieldLegend>Identitas dan batas request</FieldLegend>
            <TextField
              label="Nama client"
              id="client-name"
              required
              maxLength={100}
              value={form.name}
              onChange={(event) => {
                event.target.setCustomValidity("");
                set("name", event.target.value);
              }}
              placeholder="mis. aplikasi-chat"
              error={task.fields.name}
            />
            <div className="form-columns">
              <TextField
                label="Batas per menit"
                id="rpm-limit"
                type="number"
                min={1}
                max={100000}
                inputMode="numeric"
                value={form.rpm_limit}
                onChange={(event) => set("rpm_limit", event.target.value)}
                hint={`Default ${number(info.defaults.rpm)} request/menit`}
                error={task.fields.rpm_limit}
              />
              <TextField
                label="Request paralel"
                id="concurrent-limit"
                type="number"
                min={1}
                max={64}
                inputMode="numeric"
                value={form.max_concurrent}
                onChange={(event) => set("max_concurrent", event.target.value)}
                hint={`Default ${info.defaults.max_concurrent} paralel`}
                error={task.fields.max_concurrent}
              />
            </div>
          </FieldSet>
          <FieldSet>
            <FieldLegend>Kuota dan masa berlaku</FieldLegend>
            <div className="form-columns">
              <TextField
                label="Kuota token per hari"
                id="daily-tokens"
                type="number"
                min={1}
                max={1000000000000}
                value={form.daily_token_limit}
                onChange={(event) =>
                  set("daily_token_limit", event.target.value)
                }
                placeholder="Tanpa batas"
                error={task.fields.daily_token_limit}
              />
              <TextField
                label="Kuota token per bulan"
                id="monthly-tokens"
                type="number"
                min={1}
                max={1000000000000}
                value={form.monthly_token_limit}
                onChange={(event) =>
                  set("monthly_token_limit", event.target.value)
                }
                placeholder="Tanpa batas"
                error={task.fields.monthly_token_limit}
              />
            </div>
            <TextField
              label="Berlaku sampai"
              id="key-expiry"
              type="date"
              min="2000-01-01"
              max="2099-12-31"
              value={form.expires_at}
              onChange={(event) => set("expires_at", event.target.value)}
              hint="Hari terakhir key dapat dipakai (waktu server). Kosongkan untuk tanpa batas."
              error={task.fields.expires_at}
            />
            <FieldDescription>
              Kuota input + output reset pukul 00.00 / tanggal 1 WIB. Request
              yang sedang berjalan dapat melewati sisa kuota.
            </FieldDescription>
          </FieldSet>
          <FieldSet
            data-invalid={!!modelsError || !!task.fields.allowed_models}
          >
            <FieldLegend>Akses model</FieldLegend>
            <label className="check-control">
              <input
                id="model-access"
                type="checkbox"
                checked={form.all_models}
                onChange={(event) => set("all_models", event.target.checked)}
              />
              Izinkan semua model aktif
            </label>
            {!form.all_models && (
              <div className="model-options">
                {models.map((model) => (
                  <label className="check-control" key={model}>
                    <input
                      type="checkbox"
                      checked={form.allowed_models.includes(model)}
                      onChange={(event) =>
                        set(
                          "allowed_models",
                          event.target.checked
                            ? [...form.allowed_models, model]
                            : form.allowed_models.filter(
                                (item) => item !== model,
                              ),
                        )
                      }
                    />
                    {model}
                  </label>
                ))}
              </div>
            )}
            {(modelsError || task.fields.allowed_models) && (
              <FieldError>
                {modelsError || task.fields.allowed_models}
              </FieldError>
            )}
          </FieldSet>
          <FieldSet>
            <FieldLegend>Antrean dan privasi</FieldLegend>
            <TextField
              label="Prioritas antrean (0–9)"
              id="key-priority"
              type="number"
              min={0}
              max={9}
              required
              value={form.priority}
              onChange={(event) => set("priority", event.target.value)}
              hint="0 untuk normal. Nilai lebih tinggi didahulukan dan mendapat jalur prioritas."
              error={task.fields.priority}
            />
            <label className="check-control">
              <input
                type="checkbox"
                checked={form.log_prompts}
                onChange={(event) => set("log_prompts", event.target.checked)}
              />
              Catat prompt dan jawaban
            </label>
            <FieldDescription>
              Log hanya tersedia untuk admin dan mengikuti retensi server.
            </FieldDescription>
          </FieldSet>
          <Failure error={task.error} />
          <div className="dialog-actions">
            <Button
              type="button"
              variant="outline"
              disabled={task.pending}
              onClick={onClose}
            >
              Batal
            </Button>
            <SubmitButton pending={task.pending}>
              {value ? "Simpan perubahan" : "Buat key"}
            </SubmitButton>
          </div>
        </FieldGroup>
      </form>
    </Overlay>
  );
}
