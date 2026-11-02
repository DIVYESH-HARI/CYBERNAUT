"use strict";
const $ = (sel, root = document) => root.querySelector(sel);
const viewerTz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
let dataVersion = null, token = null, lastNotices = [];

// ---- small helpers (all text goes through textContent, never innerHTML)
function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) node.append(kid instanceof Node ? kid : document.createTextNode(kid ?? ""));
  return node;
}
function store(key, value) { try { value === undefined ? localStorage.removeItem(key) : localStorage.setItem(key, value); } catch {} }
function load(key) { try { return localStorage.getItem(key); } catch { return null; } }

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (token) headers["X-Operator-Token"] = token;
  if (opts.json) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
  const res = await fetch(path, { ...opts, headers });
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {}
    throw Object.assign(new Error(msg), { status: res.status });
  }
  return res.json();
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), 6000);
}
const fmtViewer = (iso) => new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short", timeZone: viewerTz }).format(new Date(iso));

function countdown(n) {
  if (n.deadline_state === "unknown") return { text: "Deadline not published", cls: "due-unknown" };
  const ms = new Date(n.due_at) - Date.now(), abs = Math.abs(ms);
  const d = Math.floor(abs / 864e5), h = Math.floor(abs / 36e5) % 24, m = Math.floor(abs / 6e4) % 60;
  const span = d > 0 ? `${d} d ${h} h` : h > 0 ? `${h} h ${m} min` : `${m} min`;
  if (ms <= 0) return { text: `Closed ${span} ago`, cls: "due-unknown" };
  return { text: `${span} left`, cls: ms < 7 * 864e5 ? "due-soon" : "" };
}

// ---- tabs
const TABS = ["find", "timeline", "alerts", "help", "operator"];
function showTab(name, focus = true) {
  for (const t of TABS) {
    $(`#tab-${t}`).setAttribute("aria-selected", String(t === name));
    $(`#panel-${t}`).hidden = t !== name;
  }
  if (name === "timeline") renderTimeline();
  if (name === "alerts") loadAlerts();
  if (name === "operator") unlockWithSaved();
  if (focus) $(`#tab-${name}`).focus();
  history.replaceState(null, "", "#" + name);
}
for (const t of TABS) $(`#tab-${t}`).addEventListener("click", () => showTab(t));
$(".tabs").addEventListener("keydown", (e) => {
  const i = TABS.findIndex((t) => $(`#tab-${t}`).getAttribute("aria-selected") === "true");
  if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
    showTab(TABS[(i + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length]); e.preventDefault();
  }
});

// ---- freshness banner
async function loadStatus() {
  const s = await api("/api/status");
  const box = $("#freshness");
  box.className = `fresh fresh-${s.state}`;
  box.dataset.icon = { fresh: "✔", stale: "⚠", degraded: "✖", never: "⚠" }[s.state];
  box.textContent = s.label;
  $("#help-interval").textContent = s.refresh_every_min;
  $("#demo-flag").textContent = s.demo_mode ? "Demo mode is on: the two “Demo” sources are fictional." : "";
  $("#demo-card").hidden = !s.demo_mode;
  // Plain-language problem strip when official sites cannot be reached (e.g. no internet).
  const off = $("#offline");
  off.hidden = s.state !== "degraded";
  if (!off.hidden) {
    const data = s.notice_count ? `Showing ${s.notice_count} notices last verified ${new Date(s.newest_verified).toLocaleString()}.`
                                : "No scholarships could be loaded yet.";
    const when = s.retry_in_min ? ` ScholarTrack tries again automatically within ${s.retry_in_min} min.` : "";
    $("#offline-text").textContent = (s.all_failing
      ? "Can't reach the official scholarship websites - please check your internet connection. "
      : `Can't reach ${s.failing.join(", ")} right now. `) + data + when;
  }
  return s;
}
$("#retry-now").addEventListener("click", async () => {
  const b = $("#retry-now"); b.disabled = true;
  try { toast((await api("/api/retry", { method: "POST" })).message); } catch (e) { toast("Retry failed: " + e.message); }
  setTimeout(() => { b.disabled = false; poll(); }, 20000);
});
async function loadSources() {
  const { sources } = await api("/api/sources");
  const total = sources.reduce((a, s) => a + s.notice_count, 0);
  $("#source-summary").textContent = sources.length
    ? `${total} notices from ${sources.length} official source${sources.length === 1 ? "" : "s"}` : "";
  $("#sources-list").replaceChildren(...sources.map((s) => el("li", {},
    el("a", { href: s.url, target: "_blank", rel: "noopener noreferrer" }, s.name),
    ` — ${s.notice_count} notices · last checked ${s.checked}${s.reachable ? "" : " · unreachable at last check"}`,
    s.is_demo ? el("span", { class: "pill pill-warn" }, "demo / fictional") : null)));
}

// ---- filters
const F = { q: "#f-q", level: "#f-level", discipline: "#f-discipline", category: "#f-category", within: "#f-within",
            from: "#f-from", to: "#f-to", sort: "#f-sort" };
function queryString() {
  const p = new URLSearchParams();
  const v = (k) => $(F[k]).value;
  if (v("q")) p.set("q", v("q"));
  for (const k of ["level", "discipline", "category"]) if (v(k)) p.set(k, v(k));
  if (v("within")) p.set("within_days", v("within"));
  if (v("from")) p.set("due_from", v("from"));
  if (v("to")) p.set("due_to", v("to"));
  p.set("tz", viewerTz); p.set("sort", v("sort"));
  p.set("include_unknown", $("#f-unknown").checked);
  p.set("include_withdrawn", $("#f-withdrawn").checked);
  p.set("include_closed", $("#f-closed").checked);
  return p.toString();
}
async function loadFilters() {
  const f = await api("/api/filters");
  // Only values that exist in the data are offered, each with how many are open right now.
  const fill = (sel, items) => {
    const s = $(sel), keep = s.value;
    const name = (v) => (v === "Unspecified" ? "Level not stated by source" : v);
    s.replaceChildren(s.options[0], ...items.map((i) => el("option", { value: i.value },
      i.open ? `${name(i.value)} (${i.open} open)` : `${name(i.value)} (closed for now)`)));
    s.value = items.some((i) => i.value === keep) ? keep : "";
  };
  fill(F.level, f.levels); fill(F.discipline, f.disciplines); fill(F.category, f.categories);
}
let searchTimer;
async function loadNotices() {
  const data = await api("/api/notices?" + queryString());
  lastNotices = data.notices;
  const plural = (k) => `${k} scholarship${k === 1 ? "" : "s"}`;
  $("#results-title").textContent = !data.count ? "No scholarships to show yet"
    : data.exact ? `${plural(data.count)} found` : `Closest matches: ${plural(data.count)}`;
  const soon = data.notices.filter((n) => n.deadline_state === "open" && new Date(n.due_at) - Date.now() < 7 * 864e5).length;
  $("#stat-line").textContent = soon ? `${soon} closing within 7 days` : "";
  const box = $("#results"); box.replaceChildren();
  if (!data.exact && data.count) {
    box.append(el("div", { class: "relaxed", role: "note" },
      el("b", {}, "No scholarship matches every filter you chose, so here are the nearest ones. "),
      `To find them we ${data.relaxed.join(", then ")}.`,
      el("button", { class: "link", type: "button", onclick: resetFilters }, "Start over")));
  }
  if (!data.count) {
    box.append(el("div", { class: "empty" },
      el("p", {}, "No scholarships have been collected yet. ScholarTrack is checking the official websites - this page fills in by itself as soon as they answer (usually within a minute)."),
      el("button", { class: "btn", type: "button", onclick: () => $("#retry-now").click() }, "Try again now")));
  }
  for (const n of data.notices) box.append(noticeCard(n));
  if ($("#panel-timeline").hidden === false) renderTimeline();
}
function resetFilters() {
  $("#filters").reset(); $("#f-unknown").checked = true; syncChips(); loadNotices();
}
$("#filters").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadNotices, 250); });
$("#filters").addEventListener("submit", (e) => { e.preventDefault(); closeSuggest(); clearTimeout(searchTimer); loadNotices().then(() => $("#results-title").scrollIntoView({ behavior: "smooth", block: "start" })); });
function syncChips() {
  document.querySelectorAll(".chip").forEach((c) => {
    const [id, val] = c.dataset.set.split("=");
    c.classList.toggle("active", $("#" + id).value === val);
    c.setAttribute("aria-pressed", String(c.classList.contains("active")));
  });
}
document.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => {
  const [id, val] = c.dataset.set.split("="), box = $("#" + id);
  box.value = box.value === val ? "" : val;
  syncChips(); loadNotices();
}));
$("#filters").addEventListener("change", syncChips);
$("#reset-filters").addEventListener("click", resetFilters);
$("#tz-note").textContent = `Dates in the filters use your time zone (${viewerTz}). A notice with no published deadline is hidden whenever you filter by date.`;

// ---- notice card
function verifPill(v) {
  const cls = { verified: "pill-ok", stale: "pill-warn", unreachable: "pill-bad", withdrawn: "pill-bad", unlisted: "pill-warn" }[v.code];
  const icon = { verified: "✔ ", stale: "⚠ ", unreachable: "✖ ", withdrawn: "✖ ", unlisted: "⚠ " }[v.code];
  return el("span", { class: `pill ${cls}` }, icon + v.label);
}
function noticeCard(n) {
  const c = countdown(n);
  const dueLine = n.deadline_state === "unknown"
    ? el("span", { class: "deadline due-unknown" }, "Deadline: not published by the source",
        n.due_raw ? ` (source says: "${n.due_raw}")` : "")
    : el("span", { class: "deadline" }, `Deadline: ${n.due_local}`);
  const assumed = [];
  if (n.tz_assumed) assumed.push("time zone assumed from the source");
  if (n.time_assumed) assumed.push("end of day assumed");
  if (n.year_inferred) assumed.push("year worked out from the weekday given");

  const detail = el("details", { class: "more" }, el("summary", {}, "Eligibility, benefits and change history"),
    el("dl", {},
      el("dt", {}, "Benefit"), el("dd", {}, n.benefit_text || "Not stated"),
      el("dt", {}, "Eligibility (as published)"), el("dd", {}, n.eligibility_excerpt || "Not stated"),
      el("dt", {}, "Residency rule"), el("dd", {}, n.residency_rule || "Not stated"),
      el("dt", {}, "Region"), el("dd", {}, n.region || "Not stated"),
      el("dt", {}, "Keywords found"), el("dd", {}, n.keywords.length ? n.keywords.join(", ") : "None"),
      el("dt", {}, "Source"), el("dd", {}, n.source_name),
      el("dt", {}, "Captured"), el("dd", {}, fmtViewer(n.captured_at)),
      el("dt", {}, "Last verified"), el("dd", {}, fmtViewer(n.verified_at))),
    el("p", { class: "hint" }, "Keywords only highlight words in the notice. They are not an eligibility decision."),
    el("div", { class: "history-box" }));
  detail.addEventListener("toggle", async () => {
    if (!detail.open || detail.dataset.loaded) return;
    detail.dataset.loaded = "1";
    const full = await api(`/api/notices/${n.notice_id}`);
    const box = $(".history-box", detail);
    box.append(el("h4", {}, "Change history"));
    if (!full.history.length) box.append(el("p", { class: "hint" }, "No changes recorded since this notice was first captured."));
    else box.append(el("ul", { class: "history" }, full.history.map((h) =>
      el("li", {}, `${fmtViewer(h.detected_at)} - ${h.change_type.replace(/_/g, " ")}` +
        (h.previous_local || h.updated_local ? `: ${h.previous_local || "none"} → ${h.updated_local || "none"}` : "")))));
  });

  const urgency = n.deadline_state === "unknown" ? "nodate" : n.deadline_state === "closed" ? "closed"
    : (new Date(n.due_at) - Date.now()) < 7 * 864e5 ? "urgent" : (new Date(n.due_at) - Date.now()) < 30 * 864e5 ? "soon" : "later";
  const badge = daysBadge(n, urgency);
  return el("article", { class: `notice ${urgency === "closed" ? "nodate" : urgency}${n.notice_status === "withdrawn" ? " is-withdrawn" : ""}`, "aria-label": n.title },
    el("div", { class: "n-top" },
      el("div", {}, el("h3", {}, n.title), el("p", { class: "meta" }, [n.provider, n.region].filter(Boolean).join(" \u00B7 "))),
      badge),
    el("div", { class: "row" },
      el("span", { class: "pill" }, n.education_level), el("span", { class: "pill" }, n.funding_category),
      n.discipline ? el("span", { class: "pill" }, n.discipline) : null,
      n.version > 1 ? el("span", { class: "pill pill-warn" }, "Updated by source") : null,
      n.deadline_state === "closed" ? el("span", { class: "pill pill-bad" }, "Closed for this cycle") : null,
      n.notice_status === "withdrawn" ? el("span", { class: "pill pill-bad" }, "Withdrawn") : null),
    n.deadline_state === "unknown"
      ? el("div", { class: "dueline unknown" }, "\uD83D\uDCC5 Deadline not published yet", n.due_raw ? ` (source says: "${n.due_raw}")` : "")
      : el("div", {}, el("div", { class: "dueline" }, `\uD83D\uDCC5 ${n.due_local}`),
          el("div", { class: "hint" }, `Your time zone (${viewerTz}): ${fmtViewer(n.due_at)}`),
          assumed.length ? el("span", { class: "pill pill-warn" }, "assumed: " + assumed.join(", ")) : null),
    el("div", { class: "row" }, verifPill(n.verification)),
    el("div", { class: "actions-row" },
      el("a", { class: "official", href: n.url, target: "_blank", rel: "noopener noreferrer" },
        /\.pdf($|\?)/i.test(n.url) ? "Official guidelines (PDF) \u2197" : "Open official page \u2197"),
      n.listing_url && n.listing_url !== n.url
        ? el("a", { class: "source-link", href: n.listing_url, target: "_blank", rel: "noopener noreferrer" }, `Apply / view on ${shortHost(n.listing_url)} \u2197`)
        : null),
    el("p", { class: "via" }, `Source: ${n.source_name}`),
    detail);
}
function shortHost(url) { try { return new URL(url).hostname.replace(/^www\d?\./, ""); } catch { return "source"; } }
function daysBadge(n, urgency) {
  if (urgency === "nodate") return el("div", { class: "days nodate" }, el("b", {}, "TBA"), el("span", {}, "no deadline"));
  const ms = new Date(n.due_at) - Date.now(), abs = Math.abs(ms);
  const d = Math.floor(abs / 864e5), h = Math.floor(abs / 36e5);
  const [num, unit] = d >= 1 ? [d, d === 1 ? "day" : "days"] : h >= 1 ? [h, h === 1 ? "hour" : "hours"] : [Math.max(1, Math.floor(abs / 6e4)), "min"];
  return el("div", { class: `days ${urgency}` }, el("b", {}, String(num)), el("span", {}, ms <= 0 ? `${unit} ago` : `${unit} left`));
}

// ---- timeline (list + bar; works without colour or a mouse)
function renderTimeline() {
  const box = $("#timeline"); box.replaceChildren();
  const now = Date.now(), groups = [["Closing within 7 days", []], ["Next 30 days", []], ["Later", []], ["No published deadline", []], ["Closed", []]];
  for (const n of lastNotices) {
    if (n.deadline_state === "unknown") groups[3][1].push(n);
    else if (n.deadline_state === "closed") groups[4][1].push(n);
    else { const d = (new Date(n.due_at) - now) / 864e5; groups[d <= 7 ? 0 : d <= 30 ? 1 : 2][1].push(n); }
  }
  if (!lastNotices.length) box.append(el("p", { class: "empty" }, "Nothing to show. Adjust the filters on the first tab."));
  for (const [title, items] of groups) {
    if (!items.length) continue;
    box.append(el("h3", { class: "tl-group" }, `${title} (${items.length})`));
    box.append(el("ol", { class: "tl" }, items.map((n) => {
      const c = countdown(n), days = n.due_at ? Math.max(0, Math.min(90, (new Date(n.due_at) - now) / 864e5)) : 0;
      return el("li", {},
        el("a", { href: n.url, target: "_blank", rel: "noopener noreferrer" }, n.title),
        el("div", { class: `bar${days <= 7 && n.due_at ? " soon" : ""}`, "aria-hidden": "true" },
          n.deadline_state === "open" ? el("span", { style: `width:${Math.max(3, (days / 90) * 100)}%` }) : null),
        el("span", { class: `countdown ${c.cls}` }, n.deadline_state === "unknown" ? "Not published" : `${c.text} – ${n.due_local}`));
    })));
  }
}

// ---- alerts
const dismissed = () => new Set(JSON.parse(load("dismissedAlerts") || "[]"));
async function loadAlerts() {
  const { alerts } = await api("/api/alerts");
  const hidden = dismissed(), box = $("#alerts"); box.replaceChildren();
  const visible = alerts.filter((a) => !hidden.has(a.alert_id));
  updateAlertBadge(visible.length);
  if (!visible.length) box.append(el("p", { class: "empty" }, alerts.length ? "All alerts dismissed." : "No changes detected yet. Alerts appear when a source revises a deadline or withdraws a notice."));
  for (const a of visible) {
    box.append(el("article", { class: `alert type-${a.change_type}` },
      el("p", {}, el("b", {}, a.message)),
      a.previous_local || a.updated_local ? el("p", {}, `${a.previous_local || "no deadline"} → ${a.updated_local || "no deadline"}`) : null,
      el("p", { class: "when" }, `Detected ${fmtViewer(a.created_at)} · Source: ${a.src_name}`),
      el("div", { class: "row" }, verifPill(a.verification)),
      el("div", { class: "actions-row" },
        el("a", { class: "official", href: a.url, target: "_blank", rel: "noopener noreferrer" }, "Check the official announcement ↗"),
        el("button", { class: "link", type: "button", onclick: () => { const s = dismissed(); s.add(a.alert_id); store("dismissedAlerts", JSON.stringify([...s])); loadAlerts(); } }, "Dismiss on this device"))));
  }
}
function updateAlertBadge(n) { const b = $("#alert-count"); b.textContent = n; b.hidden = !n; }

// ---- operator
async function unlockWithSaved() {
  if (token || !load("opToken")) return;
  token = load("opToken"); await loadOperator();
}
async function loadOperator() {
  try {
    const o = await api("/api/operator/overview");
    $("#op-body").hidden = false; $("#op-msg").textContent = "Unlocked.";
    table($("#op-sources"), ["Source", "Status", "Last checked", "Failures", "Notices", "Last error", "Actions"],
      o.sources.map((s) => [s.name,
        el("span", { class: `pill ${s.last_status === "ok" ? "pill-ok" : s.last_status === "never" ? "pill-warn" : "pill-bad"}` }, s.needs_review ? "needs review: " + s.last_status : s.last_status),
        s.checked_ago, String(s.consecutive_failures), String(s.notice_count), s.last_error || "-",
        el("button", { class: "btn-secondary", type: "button", onclick: () => doRefresh(s.source_id) }, "Refresh")]));
    table($("#op-runs"), ["When (UTC)", "Source", "Result", "HTTP", "Tries", "Found", "New", "Changed", "Problems"],
      o.runs.map((r) => [r.started_at, r.source_name, r.status, r.http_status ?? "-", String(r.attempts), String(r.notices_found), String(r.notices_new), String(r.notices_changed), r.error || r.warnings || "-"]));
    table($("#op-deliveries"), ["When (UTC)", "Channel", "Result", "Message"], o.deliveries.map((d) => [d.at, d.channel, d.detail ? `${d.status}: ${d.detail}` : d.status, d.message]));
    if (!$("#demo-card").hidden) {
      const items = await api("/api/operator/demo-items"), sel = $("#demo-key"), keep = sel.value;
      sel.replaceChildren(...items.items.map((i) => el("option", { value: i.key }, `${i.title} [${i.status}] – ${i.deadline}`)));
      if (keep) sel.value = keep;
    }
  } catch (e) {
    if (e.status === 401) { token = null; store("opToken"); $("#op-body").hidden = true; $("#op-msg").textContent = "That token was not accepted. Check the terminal output or data/operator_token.txt."; }
    else $("#op-msg").textContent = "Error: " + e.message;
  }
}
function table(t, heads, rows) {
  const caption = $("caption", t); t.replaceChildren(caption,
    el("thead", {}, el("tr", {}, heads.map((h) => el("th", { scope: "col" }, h)))),
    el("tbody", {}, rows.length ? rows.map((r) => el("tr", {}, r.map((c) => el("td", {}, c)))) : [el("tr", {}, el("td", { colspan: heads.length }, "Nothing yet."))]));
}
$("#token-form").addEventListener("submit", async (e) => {
  e.preventDefault(); token = $("#token").value.trim(); store("opToken", token); await loadOperator();
});
async function doRefresh(sourceId) {
  const buttons = document.querySelectorAll("#refresh-all, #op-sources button");
  buttons.forEach((b) => (b.disabled = true));
  try {
    const r = await api("/api/operator/refresh" + (sourceId ? `?source_id=${encodeURIComponent(sourceId)}` : ""), { method: "POST" });
    toast(r.results.map((x) => `${x.source_id}: ${x.status}${x.error ? " (" + x.error.slice(0, 80) + ")" : ""}`).join(" | "));
  } catch (e) { toast("Refresh failed: " + e.message); }
  await Promise.all([loadOperator(), refreshAll()]);
  buttons.forEach((b) => (b.disabled = false));
}
$("#refresh-all").addEventListener("click", () => doRefresh());

const DEMO = {
  set_deadline: () => ({ action: "set_deadline", key: $("#demo-key").value, text: $("#demo-text").value }),
  withdraw: () => ({ action: "withdraw", key: $("#demo-key").value }),
  restore: () => ({ action: "restore", key: $("#demo-key").value }),
  "add-a": () => ({ action: "add", portal: "a", title: "Mentor Live Scholarship A", text: "31 Dec 2030, 09:00 Europe/London" }),
  "add-b": () => ({ action: "add", portal: "b", title: "Mentor Live Fellowship B", text: "To be announced" }),
  "down-b": () => ({ action: "set_mode", portal: "b", mode: "down" }),
  "layout-a": () => ({ action: "set_mode", portal: "a", mode: "layout" }),
  fix: null, reset: () => ({ action: "reset" }),
};
$("#demo-card").addEventListener("click", async (e) => {
  const key = e.target.dataset?.demo; if (!key) return;
  try {
    if (key === "fix") {
      await api("/api/operator/demo", { method: "POST", json: { action: "set_mode", portal: "a", mode: "ok" } });
      await api("/api/operator/demo", { method: "POST", json: { action: "set_mode", portal: "b", mode: "ok" } });
    } else await api("/api/operator/demo", { method: "POST", json: DEMO[key]() });
    toast("Demo source changed. Now press “Refresh all now”.");
    loadOperator();
  } catch (err) { toast("Demo action failed: " + err.message); }
});

// ---- guided quick start
$("#quickstart").addEventListener("click", (e) => {
  const step = e.target.closest("button[data-step]")?.dataset.step; if (!step) return;
  if (step === "1") { $("#f-level").focus(); $("#f-level").scrollIntoView({ block: "center", behavior: "smooth" }); }
  if (step === "2") showTab("timeline");
  if (step === "3") { showTab("find", false); const d = $("#results details"); if (d) { d.open = true; d.scrollIntoView({ block: "center", behavior: "smooth" }); } $("#results .official")?.focus(); }
});
$("#hide-guide").addEventListener("click", () => { $("#quickstart").hidden = true; store("hideGuide", "1"); });
if (load("hideGuide") === "1") $("#quickstart").hidden = true;

// ---- search box: tap-to-pick topics (each one is checked server-side to have results)
let suggestionData = { searches: [], providers: [] };
async function loadSuggestions() { suggestionData = await api("/api/suggestions"); renderSuggestions(); }
function renderSuggestions() {
  const typed = $("#f-q").value.trim().toLowerCase();
  const match = (x) => !typed || x.label.toLowerCase().includes(typed) || x.q.toLowerCase().includes(typed);
  const make = (x) => el("button", { type: "button", class: "sug", onclick: () => pickSuggestion(x.q) },
    x.label, el("span", { class: "n" }, `(${x.count})`));
  const s = suggestionData.searches.filter(match), p = suggestionData.providers.filter(match);
  $("#suggest-searches").replaceChildren(...s.map(make));
  $("#suggest-providers").replaceChildren(...p.map(make));
  $("#suggest-searches").previousElementSibling.hidden = !s.length;
  $("#suggest-providers").previousElementSibling.hidden = !p.length;
  return s.length + p.length;
}
function openSuggest() {
  if (!renderSuggestions()) return closeSuggest();
  $("#suggest").hidden = false;
  for (const x of ["#f-q", "#browse-topics"]) $(x).setAttribute("aria-expanded", "true");
}
function closeSuggest() {
  $("#suggest").hidden = true;
  for (const x of ["#f-q", "#browse-topics"]) $(x).setAttribute("aria-expanded", "false");
}
function pickSuggestion(q) {
  $("#f-q").value = q; closeSuggest(); clearTimeout(searchTimer);
  loadNotices().then(() => $("#results-title").scrollIntoView({ behavior: "smooth", block: "start" }));
}
$("#f-q").addEventListener("focus", openSuggest);
$("#f-q").addEventListener("click", openSuggest);
$("#f-q").addEventListener("input", openSuggest);
$("#browse-topics").addEventListener("click", () => ($("#suggest").hidden ? openSuggest() : closeSuggest()));
document.addEventListener("click", (e) => { if (!e.target.closest(".sb-q")) closeSuggest(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#suggest").hidden) closeSuggest(); });

// ---- refresh loop: reload when the server's data changes (so two browser sessions stay in sync)
// allSettled: one failing request must never stop the rest of the page (or the polling) from working.
async function refreshAll() {
  await Promise.allSettled([loadStatus(), loadFilters(), loadNotices(), loadAlertBadge(), loadSources(), loadSuggestions()]);
}
async function loadAlertBadge() {
  const { alerts } = await api("/api/alerts"); const h = dismissed();
  updateAlertBadge(alerts.filter((a) => !h.has(a.alert_id)).length);
  if (!$("#panel-alerts").hidden) loadAlerts();
}
async function poll() {
  try {
    const s = await loadStatus();
    if (dataVersion !== null && s.data_version !== dataVersion) { toast("Notices were updated - showing the latest data."); await Promise.allSettled([loadFilters(), loadNotices(), loadAlertBadge(), loadSources(), loadSuggestions()]); }
    dataVersion = s.data_version;
  } catch { $("#freshness").className = "fresh fresh-degraded"; $("#freshness").textContent = "Cannot reach the ScholarTrack server"; }
}
(async () => {
  const start = location.hash.slice(1); if (TABS.includes(start)) showTab(start, false);
  await refreshAll();
  try { dataVersion = (await api("/api/status")).data_version; } catch { dataVersion = null; }
  setInterval(poll, 15000); setInterval(() => { loadNotices().catch(() => {}); }, 60000);
})();
