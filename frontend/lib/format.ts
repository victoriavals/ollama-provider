import type { ApiKey, Info } from "./types";

export const number = (value: number | null | undefined) =>
  value == null
    ? "–"
    : new Intl.NumberFormat("id-ID", { maximumFractionDigits: 1 }).format(
        value,
      );
export const compact = (value: number) =>
  new Intl.NumberFormat("id-ID", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
export const latency = (value: number) =>
  value >= 1000 ? `${number(value / 1000)} dtk` : `${number(value)} ms`;
export const bytes = (value: number | null) =>
  value == null ? "–" : `${number(value / 2 ** 30)} GB`;
export const dateTime = (value: string | null) =>
  value
    ? `${new Date(value).toLocaleString("id-ID", { timeZone: "Asia/Jakarta", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })} WIB`
    : "Belum pernah";
export const clock = (value: number) =>
  new Date(value).toLocaleTimeString("id-ID", {
    timeZone: "Asia/Jakarta",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
export function todayWib() {
  const shifted = new Date(Date.now() + 7 * 3600000);
  return new Date(
    Date.UTC(
      shifted.getUTCFullYear(),
      shifted.getUTCMonth(),
      shifted.getUTCDate(),
    ) -
      7 * 3600000,
  ).toISOString();
}
export function lastValidDate(value: string | null) {
  return value
    ? new Date(new Date(value).getTime() - 1000 + 7 * 3600000)
        .toISOString()
        .slice(0, 10)
    : "";
}
export function keyState(key: ApiKey): {
  kind: "ok" | "warn" | "down" | "absent";
  text: string;
} {
  if (key.revoked_at) return { kind: "absent", text: "Dicabut" };
  if (key.expired) return { kind: "down", text: "Kedaluwarsa" };
  if (
    (key.daily_token_limit != null &&
      key.usage.day_tokens >= key.daily_token_limit) ||
    (key.monthly_token_limit != null &&
      key.usage.month_tokens >= key.monthly_token_limit)
  )
    return { kind: "warn", text: "Kuota habis" };
  return { kind: "ok", text: "Aktif" };
}
export function clientMessage(key: ApiKey, info: Info, secret?: string) {
  const models = key.allowed_models || info.models;
  return [
    `Akses API LLM untuk ${key.name} (kompatibel OpenAI)`,
    "",
    `Base URL: ${info.public_base_url || "(belum diatur)"}`,
    `API key: ${secret || "(key yang sudah diberikan sebelumnya)"}`,
    `Model: ${models.join(", ")}`,
    `Batas: ${key.rpm_limit_effective} request/menit, ${key.max_concurrent_effective} request paralel`,
    `Kuota harian: ${key.daily_token_limit == null ? "tanpa batas" : `${number(key.daily_token_limit)} token`}`,
    `Kuota bulanan: ${key.monthly_token_limit == null ? "tanpa batas" : `${number(key.monthly_token_limit)} token`}`,
    "Kuota reset pukul 00.00 / tanggal 1 waktu server (WIB).",
    key.expires_at
      ? `Berlaku sampai: ${lastValidDate(key.expires_at)} WIB`
      : "Masa berlaku: tanpa batas",
    `Prioritas antrean: ${key.priority || 0}`,
    `Pencatatan prompt: ${key.log_prompts === 0 ? "dimatikan" : "aktif"}`,
    "",
    "Rahasiakan API key ini. Contoh Python (pip install openai):",
    "from openai import OpenAI",
    `client = OpenAI(base_url=${JSON.stringify(info.public_base_url || "<base-url>")}, api_key=${JSON.stringify(secret || "sk-...")})`,
    `r = client.chat.completions.create(model=${JSON.stringify(models[0] || "gemma3:4b")}, messages=[{"role": "user", "content": "Halo!"}])`,
    "print(r.choices[0].message.content)",
    info.client_guide_url ? `\nPanduan: ${info.client_guide_url}` : "",
  ]
    .filter((line) => line !== undefined)
    .join("\n");
}
