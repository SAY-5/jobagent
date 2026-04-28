// JobAgent · Dossier review console.
// Vanilla ES modules, no build step. Hits the FastAPI server.

const API = "";

const root = document.getElementById("app");

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "html") e.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") {
      e.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (v != null) e.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null) continue;
    e.append(typeof c === "string" || typeof c === "number"
      ? document.createTextNode(String(c))
      : c);
  }
  return e;
}

async function api(path, opts) {
  const r = await fetch(API + path, opts);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

let state = {
  runs: [],
  activeId: null,
  detail: null,
};

async function loadRuns() {
  const r = await api("/v1/runs");
  state.runs = r.items || [];
  if (!state.activeId && state.runs.length > 0) state.activeId = state.runs[0].id;
  if (state.activeId) await loadDetail(state.activeId);
  render();
}

async function loadDetail(id) {
  state.detail = await api(`/v1/runs/${id}`);
  render();
}

function render() {
  root.innerHTML = "";
  root.append(topbar(), main());
}

function topbar() {
  const total = state.runs.length;
  const gated = state.runs.filter((r) => r.status === "gated").length;
  return el(
    "div",
    { class: "topbar" },
    el(
      "div",
      { class: "brand" },
      el("span", {}, el("span", { class: "dot" }), "JobAgent"),
      el("small", {}, "Application Dossier · v0.1"),
    ),
    el(
      "div",
      { class: "eyebrow" },
      "Confidential · Internal Working Document",
    ),
    el(
      "div",
      { class: "case-num" },
      "CASE FILE",
      el("b", {}, state.activeId ? state.activeId.slice(-6).toUpperCase() : "—"),
      el("div", { style: "font-family: var(--mono); font-size: 10px; color: var(--ink-mute); margin-top: 2px;" },
        `${total} ON FILE · ${gated} REQUIRE REVIEW`),
    ),
  );
}

function main() {
  return el(
    "main",
    {},
    cabinet(),
    state.detail ? dossier(state.detail) : emptyState(),
  );
}

function cabinet() {
  return el(
    "aside",
    { class: "cabinet" },
    el("h3", {}, "Recent Cases"),
    el("ul", { class: "runs" }, state.runs.map(runLi)),
  );
}

function runLi(r) {
  return el(
    "li",
    {
      class: r.id === state.activeId ? "active" : "",
      onclick: () => {
        state.activeId = r.id;
        loadDetail(r.id);
      },
    },
    el(
      "div",
      { class: "run-title" },
      el("span", { class: `status-tag ${r.status}` }, r.status.replace(/_/g, " ")),
      r.title || extractCompany(r.job_url),
    ),
    el("div", { class: "run-meta" },
      `${shortDate(r.started_at)} · ${(r.mode || "shadow").toUpperCase()}`,
    ),
  );
}

function dossier(d) {
  const stamp = d.status === "submitted" ? "green" : d.status === "gated" ? "amber" : "";
  const stampLabel = d.status === "submitted" ? "FILED" :
                     d.status === "gated"     ? "HOLD"  :
                     d.status === "failed"    ? "FAIL"  : "OPEN";
  const total = d.steps.reduce((n, s) => n + s.fields.length, 0);
  const reviewed = d.steps.flatMap((s) => s.fields).filter((f) => f.decision?.action === "review").length;
  return el(
    "section",
    { class: "dossier" },
    el(
      "div",
      { class: "header" },
      el("h1", {}, d.title || extractCompany(d.job_url) || "Untitled Application"),
      el(
        "div",
        { class: "h1-sub" },
        `Filed ${shortDate(d.started_at)} · ${total} fields detected · ${reviewed} pending review`,
      ),
    ),
    el("div", { class: `stamp ${stamp}` }, stampLabel,
       el("small", {}, d.mode || "shadow")),
    d.steps.map((s, i) => stepBlock(s, i)),
  );
}

function stepBlock(s, i) {
  const label = `STEP ${String(i + 1).padStart(2, "0")} · HASH ${s.html_hash}`;
  return el(
    "div",
    {},
    el("hr", { class: "stitch", "data-label": label }),
    s.fields.map(fieldRow),
  );
}

function fieldRow(f) {
  const dec = f.decision || {};
  const cls = f.classification || {};
  const valueEl = dec.value
    ? el("div", { class: "field-value" }, dec.value)
    : el("div", { class: "field-value empty" }, "(blank)");
  const conf = (cls.confidence ?? 0).toFixed(2);
  const pillClass = `pill ${dec.action || "skip"}`;
  return el(
    "div",
    { class: "field-row" },
    el(
      "div",
      { class: "field-label" },
      f.label,
      el("small", {},
        f.kind.toUpperCase() + (f.required ? " · REQUIRED" : "") +
        (cls.section ? ` → ${cls.section}` : "")),
    ),
    valueEl,
    el(
      "div",
      { class: "field-action" },
      el("span", { class: pillClass }, (dec.action || "?").toUpperCase()),
      el("span", { class: "conf" },
        `confidence ${conf} · ${cls.source || "—"}`),
    ),
  );
}

function emptyState() {
  return el(
    "section",
    { class: "dossier" },
    el(
      "div",
      { class: "empty-state" },
      el("div", { class: "eyebrow" }, "No file selected"),
      "Select a case from the cabinet to review.",
    ),
  );
}

function shortDate(s) {
  if (!s) return "—";
  try {
    return new Date(s).toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return s;
  }
}

function extractCompany(url) {
  if (!url) return "—";
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url.slice(0, 40);
  }
}

loadRuns().catch((err) => {
  root.innerHTML = `<div class="dossier"><div class="empty-state"><div class="eyebrow">Connection failed</div>${err.message}</div></div>`;
});
