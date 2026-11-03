// Ollama Provider admin dashboard. Plain DOM, no dependencies (the page holds the admin key,
// so no third-party code is loaded). All dynamic text goes through textContent, never innerHTML.

const KEY_STORE = "ollama-provider-admin-key";
const REFRESH_MS = 10_000;
const $ = (sel) => document.querySelector(sel);

const state = { key: null, info: null, keys: [], view: "overview", usageRange: 7, usageGroup: "key", timer: null, pullTimer: null };

// ---------- DOM helpers ----------
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) el.append(c instanceof Node ? c : String(c));
  return el;
}
const SVG_NS = "http://www.w3.org/2000/svg";
function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  for (const c of children) el.append(c);
  return el;
}
const ICONS = {
  ok: "M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13Zm3.2 4.6-3.8 4.6a.75.75 0 0 1-1.1.05L4.6 9.05a.75.75 0 1 1 1.06-1.06l1.12 1.12 3.27-3.96a.75.75 0 1 1 1.15.95Z",
  warn: "M8.9 2.2a1 1 0 0 0-1.8 0L1.3 12.5A1 1 0 0 0 2.2 14h11.6a1 1 0 0 0 .9-1.5L8.9 2.2ZM8 5.5a.75.75 0 0 1 .75.75v3a.75.75 0 0 1-1.5 0v-3A.75.75 0 0 1 8 5.5Zm0 6.75a.9.9 0 1 1 0-1.8.9.9 0 0 1 0 1.8Z",
  down: "M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13Zm2.6 8.04a.75.75 0 1 1-1.06 1.06L8 9.06l-1.54 1.54a.75.75 0 0 1-1.06-1.06L6.94 8 5.4 6.46A.75.75 0 0 1 6.46 5.4L8 6.94 9.54 5.4a.75.75 0 0 1 1.06 1.06L9.06 8l1.54 1.54Z",
  absent: "M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13ZM5 7.25h6a.75.75 0 0 1 0 1.5H5a.75.75 0 0 1 0-1.5Z",
};
const STATUS_TEXT = { ok: "Normal", warn: "Perlu dicek", down: "Mati", absent: "Tidak terpasang" };
function icon(status) {
  const kind = ICONS[status] ? status : "down";
  return s("svg", { class: `icon s-${kind}`, viewBox: "0 0 16 16", "aria-hidden": "true" }, s("path", { d: ICONS[kind], fill: "currentColor" }));
}
function statusBadge(status, text) {
  return h("span", { class: "badge" }, icon(status), text ?? STATUS_TEXT[status] ?? status);
}

// ---------- Formatting ----------
const nf = new Intl.NumberFormat("id-ID");
const num = (v) => (v == null ? "–" : nf.format(v));
const ms = (v) => (v == null ? "–" : v >= 1000 ? `${(v / 1000).toLocaleString("id-ID", { maximumFractionDigits: 1 })} dtk` : `${v} ms`);
const gb = (bytes) => (bytes == null ? "–" : `${(bytes / 2 ** 30).toLocaleString("id-ID", { maximumFractionDigits: 1 })} GB`);
function time(iso) {
  if (!iso) return "–";
  return new Date(iso).toLocaleString("id-ID", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function ago(iso) {
  if (!iso) return "Belum pernah";
  const sec = (Date.now() - new Date(iso).getTime()) / 1000;
  if (sec < 60) return "Baru saja";
  if (sec < 3600) return `${Math.floor(sec / 60)} menit lalu`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} jam lalu`;
  return `${Math.floor(sec / 86400)} hari lalu`;
}

// ---------- Feedback ----------
let toastTimer;
function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("error", isError);
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), isError ? 6000 : 3000);
}
function showDialog(title, body, actions) {
  const dlg = $("#dialog");
  $("#dialog-title").textContent = title;
  $("#dialog-body").replaceChildren(...[body].flat());
  $("#dialog-actions").replaceChildren(...actions.map(({ label, kind = "", value, onClick }) =>
    h("button", { type: "button", class: `btn ${kind}`, onclick: async () => {
      if (onClick && (await onClick()) === false) return;
      dlg.close(value);
    } }, label)));
  dlg.showModal();
}
function confirmDialog(title, message, confirmLabel, danger = true) {
  return new Promise((resolve) => {
    // Esc closes the dialog without a button: treat it as "Batal" (resolve is idempotent).
    $("#dialog").addEventListener("close", () => resolve(false), { once: true });
    showDialog(title, h("p", {}, message), [
      { label: "Batal", onClick: () => resolve(false) },
      { label: confirmLabel, kind: danger ? "primary danger-fill" : "primary", onClick: () => resolve(true) },
    ]);
  });
}
async function copy(text, what = "Teks") {
  try {
    await navigator.clipboard.writeText(text);
    toast(`${what} disalin`);
  } catch {
    toast("Gagal menyalin. Pilih teksnya lalu tekan Ctrl+C.", true);
  }
}

// ---------- API ----------
class AuthError extends Error {}
async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(path, {
    method,
    headers: { Authorization: `Bearer ${state.key}`, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  if (res.status === 401) throw new AuthError("Admin key salah atau sudah diganti.");
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error?.message || `Server membalas HTTP ${res.status}`);
  return data;
}
function handleError(err) {
  if (err instanceof AuthError) return logout(err.message);
  toast(err.message || "Terjadi kesalahan", true);
}

// ---------- Auth ----------
async function login(key) {
  state.key = key;
  state.info = await api("/admin/info");
  try { sessionStorage.setItem(KEY_STORE, key); } catch { /* storage blocked: stay in memory */ }
  $("#login").hidden = true;
  $("#app").hidden = false;
  showView(location.hash.slice(1) || "overview");
  startPolling();
}
function logout(message) {
  state.key = null;
  try { sessionStorage.removeItem(KEY_STORE); } catch { /* ignore */ }
  clearInterval(state.timer);
  clearInterval(state.pullTimer);
  $("#app").hidden = true;
  $("#login").hidden = false;
  const err = $("#login-error");
  err.hidden = !message;
  err.textContent = message || "";
  $("#admin-key").value = "";
  $("#admin-key").focus();
}

// ---------- Navigation & polling ----------
const VIEWS = { overview: loadOverview, keys: loadKeys, usage: loadUsage, requests: loadRequests,
  prompts: () => loadPrompts(), models: loadModels };
function showView(name) {
  if (!VIEWS[name]) name = "overview";
  state.view = name;
  history.replaceState(null, "", `#${name}`);
  for (const sec of document.querySelectorAll(".view")) sec.hidden = sec.id !== `view-${name}`;
  for (const btn of document.querySelectorAll(".nav")) {
    if (btn.dataset.view === name) btn.setAttribute("aria-current", "page");
    else btn.removeAttribute("aria-current");
  }
  refresh();
}
async function refresh() {
  try {
    await Promise.all([loadStatus(), VIEWS[state.view]()]);
  } catch (err) {
    handleError(err);
  }
}
function startPolling() {
  clearInterval(state.timer);
  state.timer = setInterval(() => {
    // Only views that change by themselves auto-refresh; forms are never reset under the user.
    if (!document.hidden && state.key && ["overview", "requests"].includes(state.view)) refresh();
    else if (!document.hidden && state.key) loadStatus().catch(handleError);
  }, REFRESH_MS);
}

// ---------- Top bar: lamps + GPU slot meter ----------
const LAMP_GROUPS = [
  ["Ollama", (c) => worst([c.ollama, c.model_gpu])],
  ["GPU", (c) => c.gpu],
  ["Tunnel", (c) => tunnelOf(c)],
  ["Disk", (c) => worst(Object.entries(c).filter(([k]) => k.startsWith("disk:")).map(([, v]) => v))],
];
const RANK = { down: 3, warn: 2, ok: 1, absent: 0 };
function worst(list) {
  const items = list.filter(Boolean);
  if (!items.length) return null;
  return items.reduce((a, b) => (RANK[b.status] > RANK[a.status] ? b : a));
}
function tunnelOf(c) {
  const tunnels = Object.entries(c).filter(([k]) => k.startsWith("service:") && k !== "service:OllamaServe").map(([, v]) => v);
  const installed = tunnels.filter((t) => t.status !== "absent");
  return installed.length ? worst(installed) : { status: "absent", detail: "Tidak ada tunnel terpasang" };
}
async function loadStatus() {
  const st = await api("/admin/status");
  state.status = st;
  const c = st.components;
  $("#lamps").replaceChildren(...LAMP_GROUPS.map(([label, pick]) => {
    const comp = pick(c) || { status: "absent", detail: "" };
    return h("li", { class: "lamp", title: comp.detail }, icon(comp.status), h("strong", {}, label), STATUS_TEXT[comp.status]);
  }));
  const g = st.gateway.concurrency;
  // The meter follows the model that owns the GPU (qwen3.5 has 1 slot, gemma3 4).
  const turn = g.turn && (g.lanes || []).find((l) => l.model === g.turn.model);
  const cap = turn ? turn.capacity : g.global_limit;
  const used = turn ? turn.running : g.active;
  const meter = h("span", { class: "meter", "aria-hidden": "true" },
    Array.from({ length: cap }, (_, i) => h("span", { class: i < used ? "on" : "" })));
  const label = `GPU ${used}/${cap} dipakai` + (turn ? ` · ${turn.model}` : "") + (g.waiting ? `, ${g.waiting} antre` : "");
  $("#slots").replaceChildren(meter, h("span", {}, label));
  if (state.view === "overview") {
    renderComponents(c);
    renderLanes(g);
  }
}
const secs = (v) => `${(v || 0).toLocaleString("id-ID", { maximumFractionDigits: 1 })} dtk`;
function laneStatus(lane, g) {
  if (g.turn && g.turn.model === lane.model) {
    if (lane.running && g.turn.yielding) return statusBadge("warn", "Menyelesaikan, lalu gantian");
    return statusBadge("ok", lane.running ? "Memakai GPU" : "Giliran aktif");
  }
  return lane.waiting ? statusBadge("warn", "Menunggu giliran") : statusBadge("absent", "Siaga");
}
function renderLanes(g) {
  const lanes = g.lanes || [];
  $("#lanes tbody").replaceChildren(...(lanes.length ? lanes.map((l) => h("tr", {},
    h("td", {}, l.model),
    h("td", { class: "num" }, `${l.running}/${l.capacity}`),
    h("td", { class: "num" }, num(l.waiting)),
    h("td", { class: "num" }, l.waiting ? secs(l.oldest_wait_s) : "–"),
    h("td", {}, laneStatus(l, g)))) : [h("tr", {}, h("td", { class: "empty", colspan: 5 },
    "Belum ada request sejak gateway dijalankan"))]));
  const st = g.stats || {};
  $("#queue-meta").textContent = g.max_queue == null ? "" :
    `Maks ${num(g.max_queue)} antre · gantian model setelah ${secs(g.switch_after_s)} · ` +
    `${num(st.switches)}× ganti model, ${num(st.expired)} habis waktu, ${num(st.rejected)} ditolak`;
}
const COMPONENT_NAMES = { ollama: "Server Ollama", model_gpu: "Model di GPU", gpu: "Kartu grafis" };
function componentName(key) {
  if (COMPONENT_NAMES[key]) return COMPONENT_NAMES[key];
  if (key.startsWith("service:")) return `Service ${key.slice(8)}`;
  if (key.startsWith("disk:")) return `Disk ${key.slice(5)}`;
  return key;
}
function renderComponents(c) {
  $("#components tbody").replaceChildren(...Object.entries(c).map(([k, v]) =>
    h("tr", {}, h("td", {}, componentName(k)), h("td", {}, statusBadge(v.status)), h("td", {}, v.detail))));
}

// ---------- Overview ----------
function startOfToday() {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d;
}
async function loadOverview() {
  const since24 = new Date(Date.now() - 23 * 3600e3);
  since24.setMinutes(0, 0, 0);
  const [today, hourly] = await Promise.all([
    api(`/admin/usage?group_by=key&since=${encodeURIComponent(startOfToday().toISOString())}`),
    api(`/admin/usage?group_by=hour&since=${encodeURIComponent(since24.toISOString())}`),
  ]);
  const sum = (f) => today.data.reduce((a, r) => a + (r[f] || 0), 0);
  $("#today").replaceChildren(
    h("div", {}, h("dt", {}, "Request"), h("dd", {}, num(sum("requests")))),
    h("div", {}, h("dt", {}, "Gagal"), h("dd", {}, num(sum("errors")))),
    h("div", {}, h("dt", {}, "Token"), h("dd", {}, num(sum("total_tokens")))),
  );
  renderAccess();
  renderHourly(hourly.data, since24);
}
function renderAccess() {
  const base = state.info.public_base_url;
  const model = state.info.models[0] || "gemma3:4b";
  if (!base) {
    $("#access").replaceChildren(h("p", { class: "muted" },
      "URL publik belum diatur. Tambahkan PUBLIC_BASE_URL=https://<domain>/v1 di .env, lalu restart service OllamaGateway."));
    return;
  }
  const snippet = `from openai import OpenAI\n\nclient = OpenAI(base_url="${base}", api_key="sk-...")\nr = client.chat.completions.create(\n    model="${model}",\n    messages=[{"role": "user", "content": "Halo!"}],\n)\nprint(r.choices[0].message.content)`;
  $("#access").replaceChildren(
    h("div", { class: "url-row" }, h("span", { class: "url" }, base),
      h("button", { type: "button", class: "btn small", onclick: () => copy(base, "URL") }, "Salin URL")),
    h("p", { class: "hint" }, "Berikan URL ini dan sebuah API key ke client. Contoh Python:"),
    h("pre", { class: "code" }, snippet),
    h("button", { type: "button", class: "btn small", onclick: () => copy(snippet, "Contoh kode") }, "Salin contoh kode"),
  );
}
function renderHourly(rows, start) {
  const byHour = new Map(rows.map((r) => [r.hour, r]));
  const buckets = Array.from({ length: 24 }, (_, i) => {
    const d = new Date(start.getTime() + i * 3600e3);
    const r = byHour.get(d.toISOString().slice(0, 13)) || {};
    return { d, requests: r.requests || 0, errors: r.errors || 0, tokens: r.total_tokens || 0 };
  });
  const total = buckets.reduce((a, b) => a + b.requests, 0);
  $("#chart-total").textContent = `${num(total)} request`;
  // Draw at the container's real width so axis text keeps its size on phones.
  const W = Math.max(280, $("#hourly").clientWidth || 720), H = 200, padL = 36, padB = 22, padT = 8;
  const labelEvery = W < 520 ? 6 : 3;
  const max = Math.max(4, ...buckets.map((b) => b.requests));
  const nice = Math.ceil(max / 4) * 4;
  const plotW = W - padL, plotH = H - padB - padT, slot = plotW / 24, barW = Math.max(4, slot - 2);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": `Grafik batang request per jam selama 24 jam terakhir, total ${total} request` });
  for (let i = 0; i <= 4; i++) {
    const y = padT + plotH - (plotH * i) / 4;
    svg.append(s("line", { class: "grid", x1: padL, x2: W, y1: y, y2: y }));
    svg.append(s("text", { class: "axis", x: padL - 6, y: y + 4, "text-anchor": "end" }, String((nice * i) / 4)));
  }
  buckets.forEach((b, i) => {
    const x = padL + i * slot + 1;
    const hgt = (b.requests / nice) * plotH;
    const y = padT + plotH - hgt;
    const hit = s("rect", { class: "hit", x: padL + i * slot, y: padT, width: slot, height: plotH });
    const r = Math.min(4, barW / 2, hgt);
    const bar = s("path", { class: "bar", d: hgt <= 0 ? "" :
      `M${x},${padT + plotH} V${y + r} Q${x},${y} ${x + r},${y} H${x + barW - r} Q${x + barW},${y} ${x + barW},${y + r} V${padT + plotH} Z` });
    const label = `${b.d.toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" })}: ${num(b.requests)} request, ${num(b.errors)} gagal, ${num(b.tokens)} token`;
    hit.addEventListener("mousemove", (e) => tip(e, label));
    hit.addEventListener("mouseleave", () => tip());
    svg.append(hit, bar);
    if (i % labelEvery === 0) {
      svg.append(s("text", { class: "axis", x: x + barW / 2, y: H - 6, "text-anchor": "middle" },
        b.d.toLocaleTimeString("id-ID", { hour: "2-digit" })));
    }
  });
  $("#hourly").replaceChildren(svg);
}
function tip(e, text) {
  const el = $("#tooltip");
  if (!e) { el.hidden = true; return; }
  el.textContent = text;
  el.hidden = false;
  el.style.left = `${Math.min(e.clientX + 12, window.innerWidth - 250)}px`;
  el.style.top = `${e.clientY + 12}px`;
}

// ---------- API keys ----------
// ---------- Key policy: token quota, expiry, models, priority ----------
const pad2 = (n) => String(n).padStart(2, "0");
// expires_at is the first moment the key is no longer valid; people think in "last valid day".
const lastValidDay = (iso) => new Date(new Date(iso).getTime() - 1000);
function dateInputValue(iso) {
  if (!iso) return "";
  const d = lastValidDay(iso);
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}
const dayLabel = (iso) => lastValidDay(iso).toLocaleDateString("id-ID", { day: "numeric", month: "short", year: "numeric" });
const intOrNull = (v) => (v ? Number(v) : null);
function policyFields(k = {}) {
  const uid = k.id ? `p${k.id}` : "pnew";
  const daily = h("input", { type: "number", min: 1, inputmode: "numeric", id: `${uid}-daily`, placeholder: "tanpa batas", value: k.daily_token_limit ?? "" });
  const monthly = h("input", { type: "number", min: 1, inputmode: "numeric", id: `${uid}-monthly`, placeholder: "tanpa batas", value: k.monthly_token_limit ?? "" });
  const expires = h("input", { type: "date", id: `${uid}-expires`, value: dateInputValue(k.expires_at) });
  const allowed = k.allowed_models || [];
  const boxes = [...new Set([...state.info.models, ...allowed])].map((m) =>
    h("input", { type: "checkbox", value: m, checked: allowed.includes(m) }));
  const priority = h("input", { type: "checkbox", id: `${uid}-priority`, checked: (k.priority || 0) > 0 });
  const logPrompts = h("input", { type: "checkbox", id: `${uid}-logprompts`, checked: k.log_prompts !== 0 });
  const nodes = [
    h("div", { class: "form-row" },
      h("div", { class: "field" }, h("label", { for: daily.id }, "Kuota token / hari"), daily),
      h("div", { class: "field" }, h("label", { for: monthly.id }, "Kuota token / bulan"), monthly),
      h("div", { class: "field" }, h("label", { for: expires.id }, "Berlaku sampai (hari terakhir)"), expires)),
    h("fieldset", { class: "models-pick" }, h("legend", {}, "Model yang boleh dipakai"),
      ...boxes.map((b) => h("label", { class: "check" }, b, b.value)),
      h("p", { class: "hint" }, "Tidak ada yang dicentang berarti semua model aktif boleh dipakai.")),
    h("label", { class: "check" }, priority, "Prioritas: didahulukan di antrean GPU"),
    h("label", { class: "check" }, logPrompts, "Catat prompt & jawaban (menu Prompt)"),
    h("p", { class: "hint" }, "Kuota menghitung total token (input + output) dan reset pukul 00:00 / tanggal 1 waktu server. " +
      "Kuota bersifat lunak: request yang sedang berjalan boleh melewati sisanya."),
  ];
  const read = () => {
    const picked = boxes.filter((b) => b.checked).map((b) => b.value);
    return {
      daily_token_limit: intOrNull(daily.value), monthly_token_limit: intOrNull(monthly.value),
      expires_at: expires.value || null, allowed_models: picked.length ? picked : null,
      priority: priority.checked ? Math.max(1, k.priority || 0) : 0,
      log_prompts: logPrompts.checked ? null : false,
    };
  };
  // For editing: only the fields the admin actually changed, so an untouched datetime expiry is not
  // rounded to a whole day and a list naming a since-deleted model does not block other edits.
  const initial = {
    daily_token_limit: k.daily_token_limit ?? null, monthly_token_limit: k.monthly_token_limit ?? null,
    expires_at: dateInputValue(k.expires_at) || null, allowed_models: allowed.length ? allowed : null,
    priority: k.priority || 0, log_prompts: k.log_prompts === 0 ? false : null,
  };
  const same = (a, b) => (Array.isArray(a) || Array.isArray(b)
    ? [...(a || [])].sort().join("\n") === [...(b || [])].sort().join("\n") : a === b);
  const changed = () => Object.fromEntries(Object.entries(read()).filter(([f, v]) => !same(v, initial[f])));
  return { nodes, read, changed, models: state.info.models.join("\n") };
}
const quotaUsedUp = (k) =>
  (k.daily_token_limit != null && k.usage.day_tokens >= k.daily_token_limit) ||
  (k.monthly_token_limit != null && k.usage.month_tokens >= k.monthly_token_limit);
function keyStatus(k) {
  if (k.revoked_at) return statusBadge("absent", "Dicabut");
  if (k.expired) return statusBadge("down", "Kedaluwarsa");
  if (quotaUsedUp(k)) return statusBadge("warn", "Kuota habis");
  return statusBadge("ok", "Aktif");
}
function limitsCell(k) {
  const dflt = (set) => (set ? "" : h("span", { class: "muted" }, " (default)"));
  return [h("div", {}, `${num(k.rpm_limit_effective)}/menit`, dflt(k.rpm_limit)),
    h("div", {}, `${num(k.max_concurrent_effective)} paralel`, dflt(k.max_concurrent))];
}
function quotaCell(k) {
  const u = k.usage || { day_tokens: 0, month_tokens: 0 };
  const rows = [["Hari ini", u.day_tokens, k.daily_token_limit], ["Bulan ini", u.month_tokens, k.monthly_token_limit]]
    .filter(([, , limit]) => limit != null);
  if (!rows.length) return h("span", { class: "muted" }, `Tanpa batas · hari ini ${num(u.day_tokens)}`);
  return h("div", { class: "quota" }, ...rows.map(([label, used, limit]) => {
    const bar = h("span", { class: used >= limit ? "full" : "" });
    bar.style.width = `${Math.min(100, (used / limit) * 100)}%`;
    return h("div", {}, `${label}: ${num(used)} / ${num(limit)}`, h("span", { class: "progress", "aria-hidden": "true" }, bar));
  }));
}
function accessCell(k) {
  return [h("div", {}, k.allowed_models ? k.allowed_models.join(", ") : "Semua model"),
    k.expires_at ? h("div", { class: "muted" }, `${k.expired ? "Berakhir" : "Berlaku s.d."} ${dayLabel(k.expires_at)}`) : "",
    k.priority > 0 ? h("span", { class: "tag" }, "Prioritas") : "",
    k.log_prompts === 0 ? h("span", { class: "tag muted-tag" }, "Prompt tidak dicatat") : ""];
}
function clientMessage(k, key) {
  const base = state.info.public_base_url || "<URL dari penyedia>";
  const models = k.allowed_models || state.info.models;
  const quota = [k.daily_token_limit != null && `${num(k.daily_token_limit)} token/hari`,
    k.monthly_token_limit != null && `${num(k.monthly_token_limit)} token/bulan`].filter(Boolean);
  const lines = [
    "Akses API LLM (kompatibel OpenAI)", "",
    `Base URL : ${base}`,
    `API key  : ${key || "(key yang sudah diberikan sebelumnya)"}`,
    `Model    : ${models.join(", ")}`,
    `Batas    : ${k.rpm_limit_effective} request/menit, ${k.max_concurrent_effective} request bersamaan`,
  ];
  if (quota.length) lines.push(`Kuota    : ${quota.join(", ")} (reset 00:00 / tanggal 1 WIB)`);
  if (k.expires_at) lines.push(`Berlaku  : sampai ${dayLabel(k.expires_at)}`);
  lines.push("", "Rahasiakan API key ini. Contoh Python (pip install openai):", "",
    "from openai import OpenAI",
    `client = OpenAI(base_url="${base}", api_key="${key || "sk-..."}")`,
    `r = client.chat.completions.create(model="${models[0] || "gemma3:4b"}", messages=[{"role": "user", "content": "Halo!"}])`,
    "print(r.choices[0].message.content)");
  if (state.info.client_guide_url) lines.push("", `Panduan lengkap: ${state.info.client_guide_url}`);
  return lines.join("\n");
}
function showClientMessage(k) {
  const msg = clientMessage(k, null);
  showDialog(`Pesan untuk ${k.name}`, [
    h("p", { class: "hint" }, "Key asli tidak bisa ditampilkan lagi; pesan ini merujuk ke key yang sudah diberikan."),
    h("pre", { class: "code wrap" }, msg),
  ], [
    { label: "Salin pesan", onClick: () => { copy(msg, "Pesan"); return false; } },
    { label: "Tutup", kind: "primary" },
  ]);
}

function resetNewPolicy() {
  state.newPolicy = policyFields();
  $("#key-policy").replaceChildren(...state.newPolicy.nodes);
}
async function loadKeys() {
  const d = state.info.defaults;
  $("#key-defaults").textContent = `Kosongkan batas untuk memakai default: ${d.rpm} request/menit dan ${d.max_concurrent} request paralel.`;
  // Built once (and again only when the model list changes or after a key is created), so a
  // half-filled form survives refreshes such as returning to the browser tab.
  if (!state.newPolicy || state.newPolicy.models !== state.info.models.join("\n")) resetNewPolicy();
  const res = await api("/admin/keys?include_revoked=true");
  state.keys = res.data;
  renderKeys();
}
function renderKeys() {
  const showRevoked = $("#show-revoked").checked;
  const rows = state.keys.filter((k) => showRevoked || !k.revoked_at);
  const tbody = $("#keys-table tbody");
  if (!rows.length) {
    tbody.replaceChildren(h("tr", {}, h("td", { class: "empty", colspan: 8 },
      "Belum ada key aktif. Buat key pertama di atas, lalu berikan ke client.")));
    return;
  }
  tbody.replaceChildren(...rows.map((k) => h("tr", { class: k.revoked_at ? "revoked" : "" },
    h("td", {}, k.name),
    h("td", { class: "mono" }, `${k.prefix}…`),
    h("td", {}, limitsCell(k)),
    h("td", {}, quotaCell(k)),
    h("td", {}, accessCell(k)),
    h("td", { title: time(k.last_used_at) }, ago(k.last_used_at)),
    h("td", {}, keyStatus(k)),
    h("td", { class: "actions" }, k.revoked_at ? "" : [
      h("button", { type: "button", class: "btn small", onclick: () => editKey(k) }, "Ubah"),
      " ",
      h("button", { type: "button", class: "btn small", onclick: () => showClientMessage(k) }, "Pesan"),
      " ",
      h("button", { type: "button", class: "btn small danger", onclick: () => revokeKey(k) }, "Cabut"),
    ]),
  )));
}
async function createKey(e) {
  e.preventDefault();
  const name = $("#key-name").value.trim();
  const rpm = $("#key-rpm").value, conc = $("#key-conc").value;
  try {
    const k = await api("/admin/keys", { method: "POST", body: {
      name, rpm_limit: intOrNull(rpm), max_concurrent: intOrNull(conc), ...state.newPolicy.read() } });
    e.target.reset();
    resetNewPolicy();
    const msg = clientMessage(k, k.key);
    showDialog(`Key untuk ${k.name} sudah dibuat`, [
      h("p", {}, "Salin dan simpan sekarang. Key ini tidak akan ditampilkan lagi."),
      h("div", { class: "secret" }, k.key),
      h("p", { class: "hint" }, "Pesan siap kirim untuk client (berisi key, jadi kirim lewat jalur pribadi):"),
      h("pre", { class: "code wrap" }, msg),
    ], [
      { label: "Salin key", onClick: () => { copy(k.key, "Key"); return false; } },
      { label: "Salin pesan", onClick: () => { copy(msg, "Pesan"); return false; } },
      { label: "Sudah disimpan", kind: "primary" },
    ]);
    await loadKeys();
  } catch (err) { handleError(err); }
}
function editKey(k) {
  const rpm = h("input", { type: "number", min: 1, max: 100000, id: "edit-rpm", value: k.rpm_limit ?? "" });
  const conc = h("input", { type: "number", min: 1, max: 64, id: "edit-conc", value: k.max_concurrent ?? "" });
  const policy = policyFields(k);
  const d = state.info.defaults;
  showDialog(`Ubah key ${k.name}`, [
    h("div", { class: "form-row" },
      h("div", { class: "field" }, h("label", { for: "edit-rpm" }, "Batas request per menit"), rpm),
      h("div", { class: "field" }, h("label", { for: "edit-conc" }, "Request paralel"), conc)),
    h("p", { class: "hint" }, `Kosongkan untuk memakai default (${d.rpm}/menit, ${d.max_concurrent} paralel).`),
    ...policy.nodes,
  ], [
    { label: "Batal" },
    { label: "Simpan perubahan", kind: "primary", onClick: async () => {
      try {
        await api(`/admin/keys/${k.id}`, { method: "PATCH", body: {
          rpm_limit: intOrNull(rpm.value), max_concurrent: intOrNull(conc.value), ...policy.changed() } });
        toast(`Key ${k.name} disimpan`);
        await loadKeys();
      } catch (err) { handleError(err); return false; }
    } },
  ]);
}
async function revokeKey(k) {
  const ok = await confirmDialog(`Cabut key ${k.name}?`,
    `Client yang memakai key ${k.prefix}… langsung ditolak. Tindakan ini tidak bisa dibatalkan; buat key baru jika perlu.`, "Cabut key");
  if (!ok) return;
  try {
    await api(`/admin/keys/${k.id}`, { method: "DELETE" });
    toast(`Key ${k.name} dicabut`);
    await loadKeys();
  } catch (err) { handleError(err); }
}

// ---------- Usage ----------
const GROUP_LABEL = { key: "Key", model: "Model", day: "Tanggal (UTC)" };
async function loadUsage() {
  if (!state.keys.length) state.keys = (await api("/admin/keys?include_revoked=true")).data;
  const since = new Date(Date.now() - state.usageRange * 86400e3).toISOString();
  const res = await api(`/admin/usage?group_by=${state.usageGroup}&since=${encodeURIComponent(since)}`);
  const g = state.usageGroup;
  const rows = res.data.map((r) => ({ ...r, label: g === "key" ? (r.key_name || `key #${r.key}`) : g === "model" ? (r.model || "(tanpa model)") : r.day }));
  $("#usage-col").textContent = GROUP_LABEL[g];
  if (g !== "day") rows.sort((a, b) => b.requests - a.requests);
  const max = Math.max(1, ...rows.map((r) => r.requests));
  $("#usage-chart").replaceChildren(...(rows.length ? rows.slice(0, 12).map((r) => {
    const fill = h("div", { class: "fill" });
    fill.style.width = `${(r.requests / max) * 100}%`;
    return h("div", { class: "hbar" }, h("span", { class: "label", title: r.label }, r.label),
      h("div", { class: "track" }, fill), h("span", {}, `${num(r.requests)} request`));
  }) : [h("p", { class: "muted" }, "Belum ada pemakaian pada rentang ini.")]));
  $("#usage-table tbody").replaceChildren(...rows.map((r) => h("tr", {},
    h("td", {}, r.label), h("td", { class: "num" }, num(r.requests)), h("td", { class: "num" }, num(r.errors)),
    h("td", { class: "num" }, num(r.prompt_tokens)), h("td", { class: "num" }, num(r.completion_tokens)),
    h("td", { class: "num" }, ms(r.avg_latency_ms)))));
}
function setupSegmented(id, attr, onPick) {
  $(id).addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    for (const b of $(id).querySelectorAll("button")) b.setAttribute("aria-pressed", String(b === btn));
    onPick(btn.dataset[attr]);
  });
}

// ---------- Requests ----------
const ENDPOINT_TEXT = { chat: "Chat", completions: "Completion", embeddings: "Embedding" };
function statusOf(code) {
  if (code < 400) return statusBadge("ok", String(code));
  if (code === 499) return statusBadge("absent", "499 client putus");
  if (code === 429) return statusBadge("warn", "429 dibatasi");
  if (code >= 500) return statusBadge("down", String(code));
  return statusBadge("warn", String(code));
}
async function loadRequests() {
  if (!state.keys.length) state.keys = (await api("/admin/keys?include_revoked=true")).data;
  const names = new Map(state.keys.map((k) => [k.id, k.name]));
  const res = await api("/admin/usage/recent?limit=200");
  const onlyErrors = $("#only-errors").checked;
  const rows = res.data.filter((r) => !onlyErrors || r.status_code >= 400).slice(0, 100);
  const tbody = $("#requests-table tbody");
  if (!rows.length) {
    tbody.replaceChildren(h("tr", {}, h("td", { class: "empty", colspan: 8 },
      onlyErrors ? "Tidak ada request gagal. Semua berjalan normal." : "Belum ada request dari client.")));
    return;
  }
  tbody.replaceChildren(...rows.map((r) => h("tr", {},
    h("td", {}, time(r.ts)),
    h("td", {}, names.get(r.key_id) ?? `#${r.key_id}`),
    h("td", {}, (ENDPOINT_TEXT[r.endpoint] || r.endpoint) + (r.stream ? " (stream)" : "")),
    h("td", {}, r.model ?? "–"),
    h("td", {}, statusOf(r.status_code)),
    h("td", { class: "num" }, num(r.total_tokens)),
    h("td", { class: "num" }, ms(r.latency_ms)),
    h("td", { class: "mono" }, r.client_ip ?? "–"),
  )));
}

// ---------- Prompt log ----------
// Kept light on purpose: a bounded time range, 50 rows per page, previews only (full text is fetched
// per row), no auto-refresh, and at most PROMPT_MAX_ROWS rows in the table.
const PROMPT_RANGE_DAYS = { "1h": 1 / 24, "24h": 1, "7d": 7, "30d": 30 };
const PROMPT_MAX_ROWS = 500;
const ROLE_TEXT = { system: "Sistem", user: "User", assistant: "Asisten", tool: "Hasil tool", tools: "Daftar tools",
  prompt: "Prompt", input: "Input" };
state.prompts = { rows: [], next: null, params: null, seq: 0 };
function fillPromptSelects() {
  const keySel = $("#pf-key"), modelSel = $("#pf-model");
  const keyVal = keySel.value, modelVal = modelSel.value;
  keySel.replaceChildren(h("option", { value: "" }, "Semua key"),
    ...state.keys.map((k) => h("option", { value: String(k.id) }, k.revoked_at ? `${k.name} (dicabut)` : k.name)));
  modelSel.replaceChildren(h("option", { value: "" }, "Semua model"),
    ...state.info.models.map((m) => h("option", { value: m }, m)));
  keySel.value = keyVal;
  modelSel.value = modelVal;
}
function promptParams() {
  const days = PROMPT_RANGE_DAYS[$("#pf-range").value] ?? 1;
  const p = new URLSearchParams({ since: new Date(Date.now() - days * 864e5).toISOString(), limit: "50" });
  if ($("#pf-key").value) p.set("key_id", $("#pf-key").value);
  if ($("#pf-model").value) p.set("model", $("#pf-model").value);
  if ($("#pf-status").value !== "all") p.set("status", $("#pf-status").value);
  const q = $("#pf-q").value.trim();
  if (q) p.set("q", q);
  return p;
}
async function loadPrompts(more = false) {
  const seq = ++state.prompts.seq; // a newer load (e.g. a filter change) makes this one's answer stale
  if (!state.keys.length) state.keys = (await api("/admin/keys?include_revoked=true")).data;
  if (!more) {
    fillPromptSelects();
    const q = $("#pf-q").value.trim();
    if (q && q.length < 3) { toast("Kata pencarian minimal 3 huruf.", true); return; }
    state.prompts.params = promptParams();
  }
  const params = new URLSearchParams(state.prompts.params);
  if (more && state.prompts.next) params.set("before", state.prompts.next);
  const moreBtn = $("#prompts-more");
  moreBtn.disabled = true;
  let res;
  try {
    res = await api(`/admin/prompts?${params}`);
  } finally {
    moreBtn.disabled = false;
  }
  if (seq !== state.prompts.seq) return;
  state.prompts.rows = more ? [...state.prompts.rows, ...res.data] : res.data;
  state.prompts.next = res.next;
  $("#prompt-note").textContent = res.enabled
    ? `Prompt & jawaban disimpan ${res.retention_days} hari dan hanya terlihat di dashboard admin. Daftar berisi cuplikan; klik "Lihat" untuk isi lengkap.`
    : "Pencatatan prompt sedang dimatikan (PROMPT_LOG_ENABLED=false). Yang tampil hanya catatan lama.";
  renderPrompts();
}
function clipped(text) {
  return text ? h("span", { class: "clip", title: text }, text) : h("span", { class: "muted" }, "–");
}
function renderPrompts() {
  const rows = state.prompts.rows;
  const tbody = $("#prompts-table tbody");
  tbody.replaceChildren(...(rows.length ? rows.map((r) => h("tr", {},
    h("td", {}, time(r.ts)),
    h("td", {}, r.key_name ?? `#${r.key_id}`),
    h("td", {}, h("div", { class: "mono" }, r.client_ip ?? "–"), r.end_user ? h("div", { class: "muted" }, r.end_user) : ""),
    h("td", {}, r.model ?? "–"),
    h("td", {}, statusOf(r.status_code)),
    h("td", { class: "num" }, num(r.total_tokens)),
    h("td", {}, clipped(r.prompt_preview)),
    h("td", {}, clipped(r.response_preview)),
    h("td", { class: "actions" }, h("button", { type: "button", class: "btn small", onclick: () => showPrompt(r.id) }, "Lihat")),
  )) : [h("tr", {}, h("td", { class: "empty", colspan: 9 }, "Tidak ada prompt untuk filter ini."))]));
  const full = rows.length >= PROMPT_MAX_ROWS;
  $("#prompts-more").hidden = !state.prompts.next || full;
  $("#prompts-count").textContent = rows.length
    ? `${num(rows.length)} prompt ditampilkan` + (full && state.prompts.next ? " (batas tampilan; persempit filter untuk melihat yang lain)" : "")
    : "";
}
async function showPrompt(id) {
  try {
    const p = await api(`/admin/prompts/${id}`);
    const meta = [["Waktu", time(p.ts)], ["Key", p.key_name ?? `#${p.key_id}`], ["IP", p.client_ip ?? "–"],
      ["User (dari client)", p.end_user ?? "–"], ["Model", p.model ?? "–"],
      ["Endpoint", (ENDPOINT_TEXT[p.endpoint] || p.endpoint) + (p.stream ? " (stream)" : "")],
      ["Status", String(p.status_code)], ["Token", num(p.total_tokens)], ["Request ID", p.request_id ?? "–"]];
    const asText = p.prompt.map((m) => `[${ROLE_TEXT[m.role] || m.role}]\n${m.content}`).join("\n\n") +
      `\n\n[Jawaban]\n${p.response ?? "(tidak ada)"}`;
    showDialog(`Prompt dari ${p.key_name ?? "key #" + p.key_id}`, [
      h("dl", { class: "meta" }, ...meta.map(([dt, dd]) => h("div", {}, h("dt", {}, dt), h("dd", {}, dd)))),
      ...p.prompt.map((m) => h("div", { class: "msg" }, h("div", { class: "msg-role" }, ROLE_TEXT[m.role] || m.role),
        h("pre", { class: "code wrap" }, m.content || "(kosong)"))),
      h("div", { class: "msg" }, h("div", { class: "msg-role" }, "Jawaban model"),
        h("pre", { class: "code wrap" }, p.response ?? (p.status_code >= 400 ? "(ditolak sebelum sampai ke model)" : "(tidak ada)"))),
    ], [
      { label: "Salin teks", onClick: () => { copy(asText, "Prompt"); return false; } },
      { label: "Tutup", kind: "primary" },
    ]);
    const dlg = $("#dialog");
    dlg.classList.add("wide");
    dlg.addEventListener("close", () => dlg.classList.remove("wide"), { once: true });
  } catch (err) { handleError(err); }
}

// ---------- Models ----------
async function loadModels() {
  const [models, jobs] = await Promise.all([api("/admin/models"), api("/admin/models/pull")]);
  const tbody = $("#models-table tbody");
  tbody.replaceChildren(...(models.data.length ? models.data.map((m) => {
    const toggle = h("label", { class: "check" },
      h("input", { type: "checkbox", checked: m.enabled, disabled: !m.installed,
        onchange: (e) => setModel(m.name, e.target.checked, e.target) }),
      m.enabled ? "Aktif" : "Nonaktif");
    return h("tr", {},
      h("td", { class: "mono" }, m.name, m.installed ? "" : " (belum terpasang)"),
      h("td", {}, toggle),
      h("td", { class: "num" }, gb(m.size_bytes)),
      h("td", {}, m.parameter_size ?? "–"),
      h("td", {}, m.quantization ?? "–"),
      h("td", { class: "actions" }, m.installed
        ? h("button", { type: "button", class: "btn small danger", onclick: () => deleteModel(m.name) }, "Hapus")
        : ""));
  }) : [h("tr", {}, h("td", { class: "empty", colspan: 6 }, "Belum ada model. Unduh model di atas."))]));
  renderJobs(jobs.data);
}
async function setModel(name, enabled, input) {
  try {
    await api(`/admin/models/${encodeURIComponent(name).replace(/%3A/g, ":").replace(/%2F/g, "/")}`, { method: "PATCH", body: { enabled } });
    toast(`${name} ${enabled ? "diaktifkan" : "dinonaktifkan"}`);
    state.info = await api("/admin/info");
  } catch (err) {
    input.checked = !enabled;
    handleError(err);
  }
  loadModels().catch(handleError);
}
async function deleteModel(name) {
  const ok = await confirmDialog(`Hapus ${name}?`,
    "File model dihapus dari disk dan client tidak bisa memakainya lagi. Untuk memakainya kembali, model harus diunduh ulang.", "Hapus model");
  if (!ok) return;
  try {
    await api(`/admin/models/${name}`, { method: "DELETE" });
    toast(`${name} dihapus`);
    await loadModels();
  } catch (err) { handleError(err); }
}
async function pullModel(e) {
  e.preventDefault();
  const model = $("#pull-name").value.trim();
  try {
    await api("/admin/models/pull", { method: "POST", body: { model, enable: $("#pull-enable").checked } });
    e.target.reset();
    toast(`Mulai mengunduh ${model}`);
    await loadModels();
  } catch (err) { handleError(err); }
}
const JOB_TEXT = { running: "Mengunduh", success: "Selesai", error: "Gagal", cancelled: "Dibatalkan" };
function renderJobs(jobs) {
  const recent = jobs.slice(-5).reverse();
  $("#pull-jobs").replaceChildren(...recent.map((j) => {
    const bar = h("span");
    bar.style.width = `${j.percent ?? (j.status === "success" ? 100 : 0)}%`;
    const st = j.status === "success" ? "ok" : j.status === "running" ? "absent" : "down";
    return h("li", { class: "job" },
      h("div", {}, statusBadge(st, `${JOB_TEXT[j.status] || j.status} ${j.model}`),
        j.status === "running" && j.percent != null ? ` ${j.percent}%` : "",
        j.error ? h("span", { class: "error" }, ` ${j.error}`) : ""),
      j.status === "running" ? h("div", { class: "progress", "aria-hidden": "true" }, bar) : "");
  }));
  const running = jobs.some((j) => j.status === "running");
  clearInterval(state.pullTimer);
  if (running) state.pullTimer = setInterval(() => { if (state.view === "models") loadModels().catch(handleError); }, 2000);
}

// ---------- Wire up ----------
function init() {
  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = e.target.querySelector("button");
    btn.disabled = true;
    try {
      await login($("#admin-key").value.trim());
    } catch (err) {
      state.key = null;
      const el = $("#login-error");
      el.hidden = false;
      el.textContent = err instanceof AuthError ? "Admin key salah. Periksa lagi baris ADMIN_KEY di .env." : `Tidak bisa terhubung: ${err.message}`;
    } finally {
      btn.disabled = false;
    }
  });
  $("#logout").addEventListener("click", () => logout());
  for (const btn of document.querySelectorAll(".nav")) btn.addEventListener("click", () => showView(btn.dataset.view));
  $("#key-form").addEventListener("submit", createKey);
  $("#show-revoked").addEventListener("change", renderKeys);
  $("#pull-form").addEventListener("submit", pullModel);
  $("#only-errors").addEventListener("change", () => loadRequests().catch(handleError));
  $("#prompt-filter").addEventListener("submit", (e) => { e.preventDefault(); loadPrompts().catch(handleError); });
  $("#prompts-more").addEventListener("click", () => loadPrompts(true).catch(handleError));
  setupSegmented("#usage-range", "range", (v) => { state.usageRange = Number(v); loadUsage().catch(handleError); });
  setupSegmented("#usage-group", "group", (v) => { state.usageGroup = v; loadUsage().catch(handleError); });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden || !state.key) return;
    // The Prompt view keeps the pages already loaded; only the status bar is refreshed.
    if (state.view === "prompts") loadStatus().catch(handleError);
    else refresh();
  });

  let saved = null;
  try { saved = sessionStorage.getItem(KEY_STORE); } catch { /* ignore */ }
  if (saved) login(saved).catch(() => logout());
  else logout();
}
init();
