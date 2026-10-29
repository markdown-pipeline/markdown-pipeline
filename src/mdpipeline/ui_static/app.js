/* Markdown Pipeline: local UI. Vanilla JS, no build step, no network beyond this server (127.0.0.1).
   Screens: Pipelines (home), Pipeline editor (live dry-run), Activity ("Needs you" + history).
   Layout, states and copy follow the approved prototype (work/designer/phase1-brain-ui/index.html). */
"use strict";

const TOKEN = document.querySelector('meta[name="mdp-token"]').content;
const CLI = "mdpipeline";
const POLL_MS = 2000; // MD-28: external syncs show up within 3 s
const DEFAULTS = {
  target: "raw/{relpath}",      // MD-140: the default for new pipelines; raw/ is not forced (MD-133), empty = the root of the output folder
  include: "**/*.md",
  exclude: [".git/**", "node_modules/**", ".obsidian/**", "**/.*/**"].join("\n"),
  guard: 40,
};
const VARS = [["{pipeline}", "pipeline name"], ["{source}", "label of the source folder"],
  ["{relpath}", "path relative to its source folder"], ["{dir}", "folder part of relpath"],
  ["{name}", "file name without extension"], ["{ext}", "extension, no dot (md)"], ["{mtime:YYYY-MM-DD}", "source modified date"]];
const PILL = { ok: "ok", conflicts: "conflicts", blocked: "blocked", error: "error", needs: "needs you", paused: "paused", syncing: "syncing", never: "not synced yet" };
// MD-136 AC3: when the only problem is a missing source folder (nothing deleted, the person just says where it went) the status is amber "needs you", not red "error".
const shownStatus = (status, x) => (status === "error" && x && x.needs_you ? "needs" : status);

const S = {
  screen: "home", state: null, activity: null, ed: null, view: null, filter: "all", mcpTab: "claude",
  open: new Set(), confirm: null, version: null, lastSyncing: false, syncingNow: new Set(), busy: false,
  usage: null, usageUi: { just: null, drawer: false, noAnswerSent: false },   // MD-115: Settings data; the card's one-line state after a choice
};

/* ---------------------------------------------------------------- helpers */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
/* MD-167 L1: escaped text with `command` spans shown in code style. */
const lingerHtml = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");
const $ = (id) => document.getElementById(id);
/* MD-182: a folder path may use "/" or "\" (Windows): the last name is the text after either. */
const base = (p) => (p || "").trim().replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "";
const slug = (s) => (s || "").toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

async function api(method, path, body) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: { "Content-Type": "application/json", "X-MDP-Token": TOKEN },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) { // the server is gone (stopped or restarting): report it instead of throwing
    return { ok: false, status: 0, data: { error: "Cannot reach the local server. Is `mdpipeline ui` still running?" } };
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* not JSON */ }
  return { ok: res.ok, status: res.status, data };
}

function ago(ts) {
  if (!ts) return "never";
  const s = Math.max(0, (Date.now() - Date.parse(ts)) / 1000);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}
/* MD-121: scheduled sync */
const INTERVALS = [["15m", "every 15 min"], ["1h", "every hour"], ["6h", "every 6 hours"], ["daily", "every day"], ["off", "off"]];
const intervalText = (v) => (INTERVALS.find((i) => i[0] === v) || [v, v])[1];
function clock(epochSeconds) {
  const d = new Date(epochSeconds * 1000), now = new Date();
  const hm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === now.toDateString() ? hm : `${d.toLocaleDateString([], { weekday: "short" })} ${hm}`;
}
function nextText(p) {
  if (p.paused) return "paused";
  if (p.schedule === "off") return "off";
  if (p.next_sync == null) return "after its first sync";
  return p.next_sync * 1000 <= Date.now() + 60000 ? "any moment" : clock(p.next_sync);
}
const scheduleSelect = (p) => `<label class="sched"><span class="muted small">Sync automatically<span class="sr"> (${esc(p.name)})</span></span>
  <select data-schedule="${esc(p.name)}">${INTERVALS.map(([v, t]) => `<option value="${v}"${p.schedule === v ? " selected" : ""}>${t}</option>`).join("")}</select></label>`;
const dur = (ms) => (ms == null ? "" : `${(ms / 1000).toFixed(1)} s`);

let toastTimer;
function toast(message, ms = 3200) {
  const t = $("toast");
  t.textContent = message;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), ms);
}

async function copy(text, what) {
  try { await navigator.clipboard.writeText(text); } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = text; document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); } catch (e2) { /* ignore */ }
    ta.remove();
  }
  toast(`Copied ${what || ""}`.trim());
}

function pill(status, clickable) {
  const label = PILL[status] || status;
  return clickable
    ? `<button class="pill ${status}" data-act="go" data-screen="activity" title="Open in Activity"><span class="dot"></span>${esc(label)}</button>`
    : `<span class="pill ${status}"><span class="dot"></span>${esc(label)}</span>`;
}

function resultSummary(r) {
  const c = (r && r.counts) || {};
  const parts = [];
  if (c.created) parts.push(`${c.created} created`);
  if (c.updated) parts.push(`${c.updated} updated`);
  if (c.deleted) parts.push(`${c.deleted} deleted`);
  if (c.conflicts) parts.push(plural(c.conflicts, "conflict"));
  return parts.join(" · ") || "no changes";
}

function toastForResult(name, r) {
  switch (r.result) {
    case "ok": return `${name} synced: ${resultSummary(r)}`;
    case "conflicts": return `${name} synced with ${plural(r.counts.conflicts, "conflict")}: see Activity`;
    case "blocked": return `${name} blocked by the guard: approve or cancel it in Activity`;
    case "busy": return `${name}: another sync is running, try again in a moment`;
    case "skipped: paused": return `${name} is paused: nothing was synced`;
    case "error": return `${name} finished with an error: see Activity`;
    default: return `${name}: ${r.result}`;
  }
}

/* ---------------------------------------------------------------- data loading */
async function refresh() {
  const st = await api("GET", "/api/state");
  if (st.ok) {
    S.state = st.data;
    S.version = st.data.version;
    S.lastSyncing = st.data.syncing;
    if (st.data.background && st.data.background.running) S.bgStart = null;       // MD-162: it is up (started here or elsewhere): forget a stale "did not start"
  }
  if (S.screen === "activity") {
    const ac = await api("GET", "/api/activity" + (S.actPipeline ? `?pipeline=${encodeURIComponent(S.actPipeline)}` : ""));
    if (ac.ok) S.activity = ac.data;
  }
  if (S.screen === "settings" || ((S.screen === "home" || S.screen === "firstrun") && !S.usage && S.state && S.state.usage && S.state.usage.consent === "unanswered")) {
    const us = await api("GET", "/api/usage");
    if (us.ok) S.usage = us.data;
  }
  if (S.screen === "view" && S.view) {
    await loadView(S.view.name);
    render();
    return loadPlan(false);        // re-planned only when the files of the brain changed since the last plan
  }
  render();
}

async function poll() {
  if (document.hidden || S.busy) return;
  try {
    const v = await api("GET", "/api/version");
    if (v.ok && (v.data.version !== S.version || v.data.syncing !== S.lastSyncing)) {
      if (S.screen === "editor" || (S.screen === "firstrun" && FR && ["connect", "folder", "review"].includes(FR.step))) { // do not disturb the form: just keep the badge fresh
        const st = await api("GET", "/api/state");
        if (st.ok) { S.state = st.data; S.version = st.data.version; S.lastSyncing = st.data.syncing; renderTop(); }
      } else {
        await refresh();
      }
    }
  } catch (e) { /* server restarting: try again next tick */ }
}

/* ---------------------------------------------------------------- shell */
/* MD-157: the header names where the files go with the agreed terms (MM-111): the newest synced pipeline's target; "App data folder" only when no target is known. */
function headerWhere(st) {
  const synced = (st.pipelines || []).filter((p) => p.last_run).sort((a, b) => (b.last_run.ts || "").localeCompare(a.last_run.ts || ""));
  const key = (d) => (d && d.type === "github" ? "github:" + d.repo : d && d.path ? "dir:" + d.path : "");
  const known = synced.filter((p) => key(p.destination));
  if (!known.length) return { label: "App data folder", value: st.brain.path };
  const d = known[0].destination, more = new Set(known.map((p) => key(p.destination))).size - 1;
  const tail = more > 0 ? ` \u00b7 +${more} more` : "";
  return d.type === "github" ? { label: "Your GitHub repo", value: d.repo + tail } : { label: "Output folder", value: d.path + tail };
}

function renderTop() {
  const st = S.state;
  if (!st) return;
  const n = st.badge;
  const synced = (st.pipelines || []).some((p) => p.last_run);   // MD-145: a newcomer sees no brain path or "no pipelines yet" before the first sync
  let summary = synced || n ? esc(st.summary) : "";
  if (st.syncing) summary = "Syncing…";
  else if (n) summary = summary.replace(/(\d+ items? needs? you)/, '<strong style="color:var(--warn)">$1</strong>');
  $("top").innerHTML = `
    <div class="brand"><span class="mark" aria-hidden="true"></span>Markdown Pipeline${synced ? ` <span class="brainpath"><span>${esc(headerWhere(st).label)}</span> ${esc(headerWhere(st).value)}</span>` : ""}</div>
    <nav class="tabs" aria-label="Screens">
      <button data-act="go" data-screen="home" aria-current="${S.screen === "home" || S.screen === "editor" || S.screen === "view" || S.screen === "firstrun" ? "page" : "false"}">Pipelines</button>
      <button data-act="go" data-screen="activity" aria-current="${S.screen === "activity" ? "page" : "false"}">Activity${n ? `<span class="badge" aria-label="${n} need attention">${n}</span>` : ""}</button>
      <button data-act="go" data-screen="settings" aria-current="${S.screen === "settings" ? "page" : "false"}">Settings</button>
    </nav>
    <div class="summary" id="summary">${summary}</div>`;
}

function render() {
  if (!S.state) return;
  if (S.screen === "home" && !S.state.pipelines.length) enterFirstRun();          // MD-118: nothing set up yet -> the guided first run
  if (S.screen === "firstrun" && FR) return renderFirstRun();
  renderTop();
  if (S.screen === "editor" && S.ed) {
    if (!$("f-template")) renderEditor(); else renderEditorLive();
  } else if (S.screen === "view" && S.view) {
    renderView();
  } else if (S.screen === "activity") {
    renderActivity();
  } else if (S.screen === "settings") {
    renderSettings();
  } else {
    renderHome();
  }
}

function go(screen, pipeline) {
  if (screen === "activity") track("activity_viewed", { entry_point: cameFrom(S.screen) });
  S.actPipeline = screen === "activity" ? pipeline || null : null;     // Activity filtered to one pipeline only when asked
  S.activity = null;
  S.screen = screen;
  S.confirm = null;
  if (screen !== "home") S.usageUi.just = null;
  if (screen !== "editor") S.ed = null;
  if (screen !== "firstrun") leaveFirstRun();
  if (screen !== "view") { S.view = null; if (location.hash) history.pushState(null, "", location.pathname); }
  window.scrollTo(0, 0);
  return refresh();
}

/* ---------------------------------------------------------------- home */
/* MD-122: "Last sync: 3 new · 5 changed · 1 deleted" with the item list on click. Titles are shown here only, never sent anywhere. */
function changesHtml(key, lc, id) {
  if (!lc) return "";
  const open = S.open.has("chg:" + key);
  const label = { new: "new", changed: "changed", deleted: "deleted" };
  const list = open ? `<ul class="changes-list">${lc.items.map((i) => `<li><span class="op op-${esc(i.op)}">${esc(label[i.op] || i.op)}</span> ${esc(i.title)}</li>`).join("")}${lc.more ? `<li class="muted">and ${esc(lc.more)} more</li>` : ""}</ul>` : "";
  const toggle = lc.items.length ? ` · <button class="link" data-act="toggle-changes" data-key="${esc(key)}" aria-expanded="${open}">${open ? "Hide" : "Show"}</button>` : "";
  return `<div class="changes"${id ? ` id="${id}"` : ""}><span class="changes-line">Last sync: ${esc(lc.text)}</span>${toggle}${list}</div>`;
}

function renderMcp() {
  const c = S.state.connect;
  const claude = c.claude_code_command;
  const codex = `# ~/.codex/config.toml\n${c.codex_toml.trim()}`;
  const snip = S.mcpTab === "claude" ? claude : codex;
  return `<section class="card mcp" aria-labelledby="mcp-h">
    <div><h2 id="mcp-h">Connect your agent (MCP)</h2>
      <p class="small">Lets your agent search, read and list your files and run a sync. It cannot change your sources or approve a blocked sync. Your agent starts it on demand.</p></div>
    <div>
      <div class="seg" role="group" aria-label="Agent">
        <button data-act="mcptab" data-tab="claude" aria-pressed="${S.mcpTab === "claude"}">Claude Code</button>
        <button data-act="mcptab" data-tab="codex" aria-pressed="${S.mcpTab === "codex"}">Codex</button></div>
      <pre class="snip" id="snip">${esc(snip)}<button class="btn sm copy" data-act="copy-snippet">Copy</button></pre>
      <div class="muted small" style="margin-top:6px">${S.mcpTab === "claude" ? "Run in a terminal, then restart Claude Code." : "Add to the file, then restart Codex."} Same as <code>${CLI} connect</code>.</div>
    </div></section>`;
}

/* ---------------------------------------------------------------- GitHub commit links (MD-68) */
function commitLink(c) {
  return c && c.url ? `<a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer" title="Open the commit on GitHub">${esc(String(c.sha).slice(0, 7))}</a>` : "";
}

function destLine(d) {
  if (d.type === "github") {
    const lc = d.last_commit;
    return `<div class="dest" data-dest="github"><span class="dtag">GitHub</span> <b>${esc(d.repo)}</b> <span class="muted">@ ${esc(d.branch)}</span>${lc ? ` <span class="muted">· last commit</span> ${commitLink(lc)}` : ""}${d.error ? ` <span class="derr">· unreachable</span>` : ""}</div>`;
  }
  return `<div class="dest" data-dest="directory"><span class="dtag">Folder</span> <span class="mono">${esc(d.path || "")}</span></div>`;
}

function errLine(p) {
  if (p.status !== "error" || !p.summary || p.needs_you) return "";      // a missing source is the calm "Needs you" card, not a red line
  if (p.sources.some((s) => s.type === "jira")) return `<div class="errline">${esc(p.summary)} <span class="safe">Nothing it owns was deleted.</span> <span>See <a href="#" data-act="go" data-screen="activity">Activity</a> for the next step.</span></div>`;
  return `<div class="errline">${esc(p.summary)} <span class="safe">Nothing it owns was deleted.</span> <span>Fix the path in <a href="#" data-act="edit" data-name="${esc(p.name)}">Edit</a> or restore the folder, then Sync now.</span></div>`;
}

function renderHome() {
  const st = S.state, ps = st.pipelines;
  const anySync = st.syncing || ps.some((p) => p.syncing) || S.syncingNow.size > 0;
  const active = ps.filter((p) => !p.paused).length;
  let body;
  if (!ps.length) {
    body = `<div class="card empty">
      <h1>No pipelines yet</h1>
      <p>A pipeline copies Markdown from one or more project folders into your output folder, following your rules. The source folders are never modified.</p>
      <button class="btn primary" data-act="new">+ New pipeline</button>
      <p class="muted small">Or from the terminal: <code>${CLI} pipeline add</code></p></div>`;
  } else {
    body = `<ul class="plist card">${ps.map((p) => {
      const syncing = S.syncingNow.has(p.name) || S.syncingNow.has("*") || p.syncing;
      const status = syncing ? "syncing" : p.status;
      const sub = syncing ? `<div class="muted small">syncing…</div>`
        : p.paused ? `<div class="muted small">paused · last sync ${esc(ago(p.last_sync_ts))}</div>`
        : `<div class="muted small">${esc(ago(p.last_sync_ts))}${p.summary && p.status !== "never" ? " · " + esc(p.summary) : ""}</div>`;
      const when = syncing ? "" : `<div class="muted small schedline" data-next>Next sync: ${esc(nextText(p))}${p.schedule !== "off" && !p.paused ? " · " + esc(intervalText(p.schedule)) : ""}</div>`;
      const dst = p.destination || {};
      const tip = p.sources.map((s) => `${s.label}: ${s.path}`).join("\n") + "\n→ " + p.target + "\n→ " + destSummary(dst.type === "github" ? dst : { path: dst.path });
      const blocked = p.status === "blocked" && p.pending_approval;
      return `<li class="prow ${p.paused ? "is-paused" : ""}" data-pipeline="${esc(p.name)}">
        <div class="name"><a href="#/pipeline/${encodeURIComponent(p.name)}" data-act="view" data-name="${esc(p.name)}" title="Open ${esc(p.name)} in view mode">${esc(p.name)}</a>${p.sample ? ` <span class="sample-tag">Sample</span>` : ""}</div>
        <div class="route" title="${esc(tip)}"><div>${esc(p.folders_text)}</div><div><span class="arrow">→ </span>${esc(p.target)}</div>${destLine(dst)}</div>
        <div class="status">${pill(shownStatus(status, p), ["conflicts", "blocked", "error", "needs"].includes(shownStatus(status, p)))}${sub}${when}</div>
        <div class="acts">
          <button class="btn sm" data-act="sync" data-name="${esc(p.name)}" ${p.paused || syncing || blocked || st.syncing ? "disabled" : ""}
            title="${blocked ? "Approve or cancel the blocked sync in Activity first" : p.paused ? "Paused: resume to sync" : ""}">Sync now</button>
          <button class="btn ghost sm" data-act="edit" data-name="${esc(p.name)}">Edit</button>
          <button class="btn ghost sm" data-act="${p.paused ? "resume" : "pause"}" data-name="${esc(p.name)}" ${syncing ? "disabled" : ""}>${p.paused ? "Resume" : "Pause"}</button>
        </div>
        <div class="schedrow">${p.sample ? "" : scheduleSelect(p)}</div>
        ${syncing ? "" : changesHtml(p.name, p.last_changes)}
        ${p.sample ? `<div class="sample-acts muted small">Made-up data, nothing from your accounts. <button class="link" data-act="sample-later">Show a later sync</button> · <button class="link" data-act="sample-remove">Remove the sample</button> · <button class="link" data-act="own-source">Connect your own source</button></div>` : ""}
        ${p.status === "error" ? errLine(p) : ""}
      </li>`;
    }).join("")}</ul>${ps.some((p) => p.last_changes) && agentConnected() ? `<p class="muted small ask-hint">Your connected agent can answer this. Ask it: what changed since yesterday</p>` : ""}`;
  }
  $("main").innerHTML = `${renderNeedsYou()}${renderAnnouncement()}${renderLingerNotice()}${renderNotRunning()}${renderNextSync()}${renderConsentCard()}${fbPrompt()}
    <div class="bar"><h1 class="grow" tabindex="-1">Pipelines</h1>
      ${ps.length ? `<button class="btn" data-act="sync-all" ${anySync || !active ? "disabled" : ""}>Sync all${ps.some((p) => p.paused) ? ` <span class="muted small">(${active} active)</span>` : ""}</button>
      <button class="btn primary" data-act="new">+ New pipeline</button>` : ""}
    </div>${body}${renderHomeAgents()}`;
}

/* MD-147: Home offers the same one-click connect as the Ready screen; the raw command stays under "Another agent". */
function renderHomeAgents() {
  const row = S.state.pipelines[0];
  if (!row) return renderMcp();
  agEnsure(row.name);
  return `<section class="card" id="home-agents" aria-labelledby="home-agents-h"><h2 id="home-agents-h">Connect your agent</h2>
    <p class="small">Lets your agent search, read and list your files and run a sync. It cannot change your sources or approve a blocked sync. Your agent starts it on demand.</p>${agPanel("home")}</section>`;
}

const agentConnected = () => !!(AG.view && Object.keys(AG.view.connected || {}).length);

/* MD-137: "Sign in again" wherever the app says the GitHub sign-in expired. The same device flow as Edit, in place: no editor round trip. */
const reauthTimers = {};
function reauthRepo(name) {
  const row = S.state && S.state.pipelines.find((p) => p.name === name);
  return (row && row.destination && row.destination.repo) || "";
}
function needsSignin(name) {
  const row = S.state && S.state.pipelines.find((p) => p.name === name);
  return !!(row && row.needs_signin);
}
function reauthHtml(name) {
  const r = (S.reauth || {})[name], attrs = `data-name="${esc(name)}"`;
  if (!r) return `<button class="btn sm primary" data-act="reauth-start" ${attrs}>Sign in again</button>`;
  if (r.step === "starting") return `<span class="muted small" data-reauth>Starting the sign-in…</span>`;
  if (r.step === "code") return `<div class="reauth" data-reauth role="status">Open <a href="${esc(r.flow.verification_uri)}" target="_blank" rel="noopener noreferrer">github.com/login/device</a> and enter
      <b class="mono" data-reauth-code>${esc(r.flow.user_code)}</b>. This page continues by itself. <button class="btn ghost sm" data-act="reauth-cancel" ${attrs}>Cancel</button></div>`;
  if (r.step === "missing") return `<div class="reauth msg warn" data-reauth-missing role="status">▲ <span>The GitHub App is not installed on <b>${esc(r.repo)}</b>.
      <a href="${esc(r.install_url)}" target="_blank" rel="noopener noreferrer">Install it on that repository</a>, then <button type="button" class="link" data-act="reauth-recheck" ${attrs}>check again</button>.
      Nothing was changed.</span></div>`;
  if (r.step === "done") return `<div class="reauth msg ok" data-reauth-done role="status">✓ <span>Signed in again as <b>${esc(r.login)}</b>. Nothing was deleted.</span>
      <button class="btn sm primary" data-act="reauth-sync" ${attrs}>Sync now</button></div>`;
  return `<div class="reauth derr" data-reauth role="alert">✕ ${esc(r.message || "The sign-in did not work.")} <button class="btn sm" data-act="reauth-start" ${attrs}>Try again</button></div>`;
}

async function reauthStart(name) {
  clearTimeout(reauthTimers[name]);
  S.reauth = { ...(S.reauth || {}), [name]: { step: "starting" } };
  render();
  const r = await api("POST", "/api/signin/start", {});
  if (!r.ok) { S.reauth[name] = { step: "error", message: (r.data && r.data.error) || "Could not start the sign-in." }; return render(); }
  const st = { step: "code", flow: r.data };
  S.reauth[name] = st;
  render();
  const poll = async () => {
    if (S.reauth[name] !== st) return undefined;
    const p = await api("POST", "/api/signin/poll", { flow: st.flow.flow });
    if (S.reauth[name] !== st) return undefined;
    if (!p.ok) { S.reauth[name] = { step: "error", message: (p.data && p.data.error) || "The sign-in failed." }; return render(); }
    if (p.data.status === "pending") { reauthTimers[name] = setTimeout(poll, (p.data.interval || st.flow.interval || 5) * 1000); return undefined; }
    if (p.data.status !== "ok") { S.reauth[name] = { step: "error", message: p.data.message }; return render(); }
    return reauthFinish(name, p.data.pending, p.data.repos || []);
  };
  reauthTimers[name] = setTimeout(poll, (st.flow.interval || 5) * 1000);
  return undefined;
}

async function reauthFinish(name, pending, repos) {
  const repo = reauthRepo(name);
  if (repo && !repos.some((x) => x.toLowerCase() === repo.toLowerCase())) {
    S.reauth[name] = { step: "missing", pending, repo, install_url: (S.state.github_app || {}).install_url || "#" };
    return render();
  }
  const s = await api("POST", `/api/pipelines/${encodeURIComponent(name)}/sign-in`, { pending });
  if (!s.ok) {
    const d = s.data || {};
    S.reauth[name] = { step: "error", message: (d.errors && d.errors[0] && d.errors[0].message) || d.error || "The sign-in was not accepted." };
    return render();
  }
  S.reauth[name] = { step: "done", login: s.data.login };
  await refresh();
  return undefined;
}

async function reauthRecheck(name) {
  const r = S.reauth[name];
  const p = await api("POST", "/api/signin/repos", { pending: r.pending });
  if (!p.ok) { S.reauth[name] = { step: "error", message: (p.data && p.data.error) || "Could not check the repositories." }; return render(); }
  return reauthFinish(name, r.pending, p.data.repos || []);
}

/* MD-136: a source folder that is gone. Calm (amber), safety first, two ways forward. Used by Home, view mode and Activity. */
const sentence = (s) => (/[.!?]$/.test(s) ? s : s + ".");   // MD-146: reasons from the planner are complete sentences

function missingSourceHtml(pipeline, label, path, wrapClass) {
  const key = pipeline + "/" + label, m = S.msrc && S.msrc.key === key ? S.msrc : null;
  const attrs = `data-name="${esc(pipeline)}" data-label="${esc(label)}"`;
  const form = m ? `<div class="msrc-form"><label class="tf"><span>New folder for <b>${esc(label)}</b></span>
      <span class="fr-pathrow"><input type="text" class="mono" id="msrc-path" data-msrc-path ${attrs} value="${esc(m.path)}" spellcheck="false" autocomplete="off" aria-label="New folder path for ${esc(label)}">${brBrowseButton("msrc-path", "source", "the new folder")}</span></label>
      <button class="btn primary sm" data-act="msrc-save" ${attrs} ${m.busy ? "disabled" : ""}>Use this folder</button>
      <button class="btn ghost sm" data-act="msrc-cancel">Cancel</button>
      ${m.error ? `<div class="derr small" role="alert">${esc(m.error)}</div>` : ""}</div>` : "";
  return `<div class="vneed warn ${wrapClass || ""}" data-need="source_missing" ${wrapClass === "plan-need" ? 'data-problem="source_missing"' : ""} data-pipeline="${esc(pipeline)}" data-label="${esc(label)}" role="status"><span aria-hidden="true">▲</span>
    <span>Source folder not found: <code>${esc(path)}</code>. <span class="safe">Nothing was deleted; your files are kept.</span></span>
    <button class="btn sm" data-act="msrc-choose" ${attrs} data-path="${esc(path)}">Choose new location</button>
    <button class="btn ghost sm" data-act="msrc-remove" ${attrs}>Remove this source</button>${form}</div>`;
}

/* "Needs you" at the top of Home (MD-121 AC4): one card per problem, safety first, then the cause, then the one fix. Names the pipeline only. */
function renderNeedsYou() {
  const list = (S.state && S.state.notices) || [];
  if (!list.length) return "";
  const act = (kind, name) => ({
    sync_failed: needsSignin(name) ? reauthHtml(name) : `<button class="btn sm" data-act="view-activity" data-name="${esc(name)}">See why</button> <button class="btn sm" data-act="sync" data-name="${esc(name)}">Try now</button>`,
    stale: `<button class="btn sm" data-act="view-activity" data-name="${esc(name)}">See why</button> <button class="btn sm" data-act="sync" data-name="${esc(name)}">Try now</button>`,
    source_missing: `<button class="btn sm" data-act="view" data-name="${esc(name)}">Fix the source</button>`,
    guard_blocked: `<button class="btn sm" data-act="view-activity" data-name="${esc(name)}">Review changes</button>`,
  })[kind] || "";
  const text = (kind, n, rawName) => ({
    sync_failed: needsSignin(rawName) ? `<b>${n}: your GitHub sign-in expired or was revoked.</b> Nothing was deleted. Your agent still reads the files from the last good sync.` : `<b>${n} couldn't sync the last 2 times.</b> Nothing was deleted. Your agent still reads the files from the last good sync.`,
    source_missing: `<b>A source of ${n} is missing.</b> Nothing was deleted: files are never removed when a source can't be read.`,
    stale: `<b>${n} hasn't synced for over 24 hours.</b> Nothing was deleted. Your agent still reads the files from the last good sync.`,
    guard_blocked: `<b>${n} is waiting for your OK.</b> Its next sync would change many files, so nothing was changed yet.`,
  })[kind] || `<b>${n} needs you.</b>`;
  const card = (x) => x.kind === "guard_blocked" || x.kind === "source_missing"
    ? `<div class="vneed warn need-card" role="alert" data-notice="${esc(x.kind)}" data-pipeline="${esc(x.pipeline)}"><span aria-hidden="true">▲</span> ${text(x.kind, esc(x.pipeline), x.pipeline)} ${act(x.kind, x.pipeline)}</div>`
    : `<div class="vneed err need-card" role="alert" data-notice="${esc(x.kind)}" data-pipeline="${esc(x.pipeline)}"><span aria-hidden="true">✕</span> ${text(x.kind, esc(x.pipeline), x.pipeline)} ${act(x.kind, x.pipeline)}</div>`;
  return `<section class="needs" id="needs-you" aria-label="Needs you"><h2 class="needs-h">Needs you</h2>${list.map(card).join("")}</section>`;
}

/* The first time the background process turns on, say so in plain words (MD-121 AC7). The words come from the saved state, so they stay
   on screen across reloads until the person answers (OK, Change or Turn off): a login item is never left unannounced. */
function renderAnnouncement() {
  const bg = S.state && S.state.background;
  if (!bg || !bg.announce_pending) return "";
  return `<div class="banner ok announce" id="bg-announce" role="status">${esc(bg.announcement)}
    <button class="btn sm" data-act="bg-change">Change</button> <button class="btn sm" data-act="bg-off">Turn off</button>
    <button class="btn sm ghost" data-act="bg-ok">OK</button></div>`;
}

/* MD-166: on a server (no desktop session) with linger off, background sync stops at logout and waits for a login after a reboot. Say so on
   Home, with the commands, until linger is on or the person hides it. The app never runs the command. */
function renderLingerNotice() {
  const bg = S.state && S.state.background;
  if (!bg || !bg.linger_notice || !bg.linger_text) return "";
  return `${noteLinger("home")}<p class="muted small" id="bg-linger-home" role="status">${lingerHtml(bg.linger_text)} <button class="btn sm ghost" data-act="linger-hide">Hide</button></p>`;
}

/* Quiet, only when it matters: background sync is on (by choice) but nothing is running, so files will go stale unseen.
   MD-162: pressing "Start it" shows progress (the button is disabled), then the line gives way to the next sync time; if the process does not
   come up in time the line says so in plain words with one next step. It never stays as it was, as if nothing had happened. */
function renderNotRunning() {
  const bg = S.state && S.state.background;
  if (!bg || !bg.enabled || bg.running) return "";
  if (S.bgStart === "starting") {
    return `<p class="muted small notrunning" id="bg-not-running" role="status">Starting background sync…
    <button class="btn sm" disabled>Starting…</button></p>`;
  }
  if (S.bgStart === "failed") {
    return `<p class="muted small notrunning" id="bg-not-running" role="alert">Background sync did not start. Open a terminal and run <code>${CLI} background</code>, or try again.
    <button class="btn sm" data-act="bg-set" data-on="true">Try again</button></p>`;
  }
  if (!S.state.pipelines.some((p) => p.last_sync && p.schedule !== "off" && !p.paused)) return "";
  return `<p class="muted small notrunning" id="bg-not-running">Background sync is on but not running, so nothing syncs by itself right now.
    <button class="btn sm" data-act="bg-set" data-on="true">Start it</button></p>`;
}

/* MD-162: once the background process runs, Home says when the next scheduled sync is (the earliest of the pipelines that sync by themselves). */
function renderNextSync() {
  const bg = S.state && S.state.background;
  if (!bg || !bg.enabled || !bg.running) return "";
  const due = S.state.pipelines.filter((p) => p.schedule !== "off" && !p.paused && p.next_sync != null).map((p) => p.next_sync);
  if (!due.length) return "";
  const when = nextText({ paused: false, schedule: "on", next_sync: Math.min(...due) });
  return `<p class="muted small" id="bg-next" role="status">Background sync is on. Next sync ${when === "any moment" ? "in a moment" : "at " + esc(when)}.</p>`;
}

function focusSoon(selector) { setTimeout(() => { const el = document.querySelector(selector); if (el) el.focus(); }, 0); }

let scheduleTimer;
function scheduleChosen(name, interval) {            // arrow keys step through the choices: save the one the person stops on
  clearTimeout(scheduleTimer);
  scheduleTimer = setTimeout(() => setSchedule(name, interval), 400);
}

async function setSchedule(name, interval) {
  const r = await api("POST", `/api/pipelines/${encodeURIComponent(name)}/schedule`, { interval });
  toast(r.ok ? (interval === "off" ? `${name}: automatic sync is off` : `${name}: syncs ${intervalText(interval)}`) : (r.data && r.data.error) || "could not change the interval");
  await refresh();
  focusSoon(`select[data-schedule="${CSS.escape(name)}"]`);
}
async function setBackground(enabled) {
  if (enabled && S.bgStart === "starting") return;                 // MD-162: no second press while it starts
  if (enabled) { S.bgStart = "starting"; render(); }
  const r = await api("POST", "/api/background", { enabled });
  if (!r.ok) {
    S.bgStart = null;
    toast((r.data && r.data.error) || "could not change background sync");
    return render();
  }
  S.state.background = r.data;
  if (!enabled) {
    S.bgStart = null;
    toast("Background sync is off: nothing syncs by itself while this window is closed");
    return render();
  }
  if (r.data.running) { S.bgStart = null; toast("Background sync is running"); return render(); }
  render();
  return watchBackgroundStart();
}
/* The process takes a moment to come up: look every second or so, and update Home the moment it runs (no reload). window.__bgStartWaitMs is a test hook. */
async function watchBackgroundStart() {
  const wait = window.__bgStartWaitMs || 12000, began = Date.now();
  while (Date.now() - began < wait) {
    await new Promise((resolve) => setTimeout(resolve, 700));
    const st = await api("GET", "/api/state");
    if (!st.ok) continue;
    S.state = st.data; S.version = st.data.version; S.lastSyncing = st.data.syncing;
    if (st.data.background.running) { S.bgStart = null; toast("Background sync is running"); return render(); }
    if (!st.data.background.enabled) { S.bgStart = null; return render(); }
  }
  S.bgStart = "failed";
  render();
}

async function doSync(name) {
  S.syncingNow.add(name); S.busy = true; render();
  const r = await api("POST", `/api/pipelines/${encodeURIComponent(name)}/sync`);
  S.syncingNow.delete(name); S.busy = false;
  toast(r.ok ? toastForResult(name, r.data) : (r.data && r.data.error) || "sync failed");
  await refresh();
}
async function doSyncAll() {
  S.syncingNow.add("*"); S.busy = true; render();
  const r = await api("POST", "/api/sync-all");
  S.syncingNow.delete("*"); S.busy = false;
  if (r.ok) {
    const results = r.data.results;
    const bad = results.filter((x) => !["ok", "skipped: paused"].includes(x.result));
    toast(bad.length ? `Synced ${results.length}: ${bad.map((x) => `${x.pipeline} ${x.result}`).join(", ")}` : `Synced ${plural(results.length, "pipeline")}`);
  } else toast((r.data && r.data.error) || "sync failed");
  await refresh();
}
async function doPause(name, pause) {
  const r = await api("POST", `/api/pipelines/${encodeURIComponent(name)}/${pause ? "pause" : "resume"}`);
  toast(r.ok ? (pause ? `${name} paused: every trigger (UI, CLI, MCP) skips it until resumed` : `${name} resumed`) : "failed");
  await refresh();
}


/* ---------------------------------------------------------------- view mode (MD-92): read-only, own URL #/pipeline/NAME */
async function loadView(name) {
  const r = await api("GET", `/api/pipelines/${encodeURIComponent(name)}/view`);
  if (!S.view || S.view.name !== name) return;
  S.view.loading = false;
  if (r.ok) { S.view.data = r.data; S.view.error = null; S.view.notFound = false; }
  else { S.view.notFound = r.status === 404; S.view.error = (r.data && r.data.error) || "Could not load the pipeline"; }
}

/* The next-sync dry run (MD-93): runs when the view opens, again after the pipeline's files change (a sync from anywhere), and on Refresh. */
async function loadPlan(force) {
  const v = S.view;
  if (!v || v.notFound || v.planLoading) return;
  if (!force && v.plan && v.planVersion === S.version) return;
  v.planLoading = true; renderViewPlan();
  const r = await api("GET", `/api/pipelines/${encodeURIComponent(v.name)}/plan`);
  if (S.view !== v) return;
  v.planLoading = false; v.planVersion = S.version;
  v.plan = r.ok ? r.data : null;
  v.planError = r.ok ? null : (r.data && r.data.error) || "Could not plan";
  render();
}

async function saveMissingSource(name, label) {
  const m = S.msrc;
  if (!m || m.key !== name + "/" + label) return undefined;
  m.busy = true; m.error = ""; render();
  const r = await api("POST", `/api/pipelines/${encodeURIComponent(name)}/source-path`, { label, path: m.path });
  m.busy = false;
  if (!r.ok) { m.error = (r.data && (r.data.error || r.data.message)) || "Could not save that folder."; return render(); }
  S.msrc = null;
  toast(`${label}: new location saved. Check the plan below before the next sync.`);
  return openView(name);                 // the plan shows what the next sync will do; nothing was written
}

async function removeMissingSource(name, label) {
  const r = await api("POST", `/api/pipelines/${encodeURIComponent(name)}/remove-source`, { label });
  if (!r.ok) { toast((r.data && (r.data.error || r.data.message)) || "Could not remove that source."); return undefined; }
  S.msrc = null;
  toast(`${label} removed from ${name}. ${plural(r.data.kept, "file")} kept in your output folder; this pipeline no longer updates them.`);
  return S.screen === "view" ? openView(name) : refresh();
}

function onMsrcInput(ev) {
  const el = ev.target;
  if (el.matches && el.matches("input[data-msrc-path]") && S.msrc) S.msrc.path = el.value;
}
document.addEventListener("input", onMsrcInput);

async function openView(name, fromRoute) {
  track("view_mode_opened", { entry_point: fromRoute ? "other" : cameFrom(S.screen) });
  S.screen = "view"; S.ed = null; S.confirm = null;
  S.view = { name, data: null, error: null, notFound: false, loading: true, plan: null, planLoading: false, planVersion: null, planError: null, filter: "all" };
  const hash = `#/pipeline/${encodeURIComponent(name)}`;
  if (!fromRoute && location.hash !== hash) history.pushState(null, "", hash);
  window.scrollTo(0, 0);
  if (S.state) render();
  await refresh();             // loads the viewed pipeline along with the home data
  return loadPlan(false);
}

function routeFromHash() {
  const m = /^#\/pipeline\/(.+)$/.exec(location.hash);
  if (m) return openView(decodeURIComponent(m[1]), true);
  if (S.screen === "view") { S.view = null; S.screen = "home"; return refresh(); }
  return undefined;
}

function credText(label, c) {
  if (!c) return `<div>${esc(label)}: <span class="muted">not set</span></div>`;
  const how = { vault: "app vault", env: "environment variable", keychain: "OS keychain", invalid: "not a valid reference" }[c.method] || c.method;
  const state = c.set === true ? "set" : c.set === false ? "not set" : c.locked ? "vault locked" : "not checked";
  return `<div>${esc(label)}: ${esc(how)}${c.name ? ` · <span class="mono">${esc(c.name)}</span>` : ""} · <b class="${c.set === false ? "derr" : ""}">${state}</b></div>`;
}

function jiraCheck(s) {
  const pl = S.view && S.view.plan, c = pl && pl.sources && pl.sources[s.label];
  if (!c) return `<div class="small muted" data-check="${esc(s.label)}">${S.view && S.view.planLoading ? "Checking Jira…" : "Checked by the dry run below"}</div>`;
  return `<div class="small ${c.ok ? "okline" : "derr"}" data-check="${esc(s.label)}">${c.ok ? "✓" : "✕"} ${esc(c.message)}</div>`;
}

function viewSource(s) {
  const off = s.enabled === false ? ` <span class="dtag offtag" data-off>Off</span>` : "";
  if (s.type === "jira") {
    return `<li class="vsrc${off ? " off" : ""}" data-source="${esc(s.label)}"><b>${esc(s.label)}</b> <span class="dtag">Jira</span>${off} <span class="mono">${esc(s.site)}</span>
      <div class="mono small">${esc(s.jql)}</div>
      <div class="small">${credText("Email", s.credentials.email)}${credText("API token", s.credentials.token)}</div>
      ${jiraCheck(s)}</li>`;
  }
  const c = s.check;
  return `<li class="vsrc${off ? " off" : ""}" data-source="${esc(s.label)}"><b>${esc(s.label)}</b> <span class="dtag">Folder</span>${off} <span class="mono">${esc(s.path)}</span>
    <div class="small ${c.ok ? "okline" : c.missing ? "muted" : "derr"}" data-check="${esc(s.label)}">${c.ok ? "✓" : c.missing ? "▲" : "✕"} ${esc(c.message)}${c.missing ? ". Nothing was deleted." : ""}</div></li>`;
}

function viewDest(d) {
  if (d.type === "github") {
    return `<div data-dest="github"><span class="dtag">GitHub</span> <b>${esc(d.repo)}</b> <span class="muted">@ ${esc(d.branch)}</span>
      <div class="small">Sign-in method: ${d.auth === "sign-in" ? "Sign in with GitHub" : "personal access token"}${d.login ? ` · signed in as <b>${esc(d.login)}</b>` : ""}</div>
      <div class="small">${credText("Credential", d.credential)}</div>
      ${d.credential_conflict ? `<div class="small" style="color:var(--bad,#b00020)" data-credential-conflict>${esc(d.credential_conflict.message)}</div>` : ""}
      <div class="small">Local clone: ${d.clone ? `<span class="mono">${esc(d.clone)}</span>` : '<span class="muted">none</span>'}</div></div>`;
  }
  return `<div data-dest="directory"><span class="dtag">Folder</span> <span class="mono">${esc(d.path)}</span></div>`;
}

function viewPolicyText(np) {
  const label = (NAME_POLICIES.find(([v]) => v === np.policy) || [np.policy, np.policy])[1];
  return `${esc(label)}${np.policy === "custom" ? ` · <span class="mono">${esc(np.pattern)}</span>` : ""}${np.example ? `<div class="small muted">Example: <span class="mono">${esc(np.example)}</span></div>` : ""}`;
}


function fmtTime(ts) {
  const d = new Date(ts);
  return isNaN(d) ? "" : `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function viewPlanHtml(v, row) {
  const head = (extra) => `<div class="phead"><div class="t"><h2 id="vp-h" style="margin:0">Next sync (dry run)</h2><span class="muted small">nothing is written · grouped by source${extra}</span></div>
    <button class="btn sm" data-act="plan-refresh" ${v.planLoading ? "disabled" : ""}>Refresh</button></div>`;
  if (v.planLoading && !v.plan) return head("") + `<div class="pempty" id="plan-loading">Planning the next sync…</div>`;
  if (v.planError) return head("") + `<div class="banner err" role="alert">✕ ${esc(v.planError)}</div>`;
  const pl = v.plan;
  if (!pl) return head("");
  const stamp = ` · planned at <b id="planned-at">${esc(fmtTime(pl.planned_at))}</b>${v.planLoading ? " · updating…" : ""}`;
  const problems = pl.problems.map((x) => x.kind === "target" && x.category === "sign-in-expired"
    ? (((v.data && v.data.needs_you) || []).some((n) => n.kind === "target") ? "" : `<div class="banner warn" role="status" data-problem="target">▲ <b>${esc(x.title)}</b> ${esc(x.message)} <span class="safe">Nothing was written or deleted.</span> ${reauthHtml(v.name)}</div>`)
    : x.kind === "source_missing" ? (((v.data && v.data.needs_you) || []).some((n) => n.kind === "source_missing" && n.label === x.label) ? "" : missingSourceHtml(v.name, x.label, x.path, "plan-need")) : `<div class="banner err" role="alert" data-problem="${esc(x.kind)}">✕ <b>${esc(x.title)}</b> ${esc(x.message)} ${esc(x.next_step)}
    <a href="#" data-act="go" data-screen="activity">Open Activity</a></div>`).join("");
  const pv = pl.preview, counts = pv.counts, groups = pv.groups, guard = pv.guard;
  if (pl.target_error) return head(stamp) + problems + `<div class="pempty">The plan needs to read the target. Nothing was written or deleted.</div>`;
  const btn = (k, label) => `<button data-act="vfilter" data-k="${k}" aria-pressed="${v.filter === k}"><b>${k === "all" ? counts.all : counts[k]}</b> ${label}</button>`;
  const pills = `<div class="counts" role="group" aria-label="Filter by status">${btn("all", "all")}${btn("create", "create")}${btn("update", "update")}${counts.rename ? btn("rename", "rename") : ""}${btn("delete", "delete")}${btn("skip", "skip")}${btn("conflict", "conflict")}${counts.collision ? btn("collision", "collision") : ""}</div>`;
  const del = counts.delete, upd = counts.update;
  const blockedNow = row && row.status === "blocked" && row.pending_approval;
  const banners = `
    ${blockedNow ? `<div class="banner warn" data-banner="blocked">▲ The last sync stopped at the guard and waits for your decision. Approve or cancel it in <a href="#" data-act="go" data-screen="activity">Activity (Needs you)</a>.</div>` : ""}
    ${guard && guard.blocked ? `<div class="banner warn" data-banner="guard">▲ The next sync would ${del ? `delete ${del}` : ""}${del && upd ? " and " : ""}${upd ? `overwrite ${upd}` : ""} of ${guard.total} files (${guard.percent}%). It will stop at the guard and ask for approval in <a href="#" data-act="go" data-screen="activity">Activity (Needs you)</a>.</div>` : ""}
    ${pv.rename_note ? `<div class="banner warn">▲ ${plural(counts.rename, "file")} will be renamed (old path → new path is in the Target column). ${esc(pv.rename_note)}</div>` : ""}
    ${counts.conflict ? `<div class="banner warn">▲ ${plural(counts.conflict, "conflict")} will be skipped and reported, never overwritten.</div>` : ""}`;
  const table = groups.length
    ? `<div class="tablewrap"><table><thead><tr><th style="width:92px">Status</th><th>Source (relative to its folder)</th><th>Target</th></tr></thead><tbody>${planRows(groups, v.filter)}</tbody></table></div>`
    : `<div class="pempty">No source can be read, so there is nothing to plan.</div>`;
  const none = counts.all === 0 && !problems ? `<div class="pempty">0 files match these rules. Nothing would be synced.</div>` : "";
  return head(stamp) + problems + banners + (groups.length ? pills : "") + table + none;
}

function renderViewPlan() {
  const box = $("view-plan");
  if (!box || !S.view) return;
  const row = S.state && S.state.pipelines.find((p) => p.name === S.view.name);
  box.innerHTML = viewPlanHtml(S.view, row);
  const next = $("v-about-next");
  if (next) next.innerHTML = aboutNextHtml(S.view);          // MD-135: the summary's second line follows the plan
}


function viewStatusHtml(d, syncing) {
  const st = d.status, dest = st.destination || {};
  const status = syncing ? "syncing" : st.status;
  const last = st.last_run;
  const clone = dest.clone_status;
  const shown = shownStatus(status, st);
  return `<div class="vrow"><dt>Status</dt><dd>${pill(shown, ["conflicts", "blocked", "error", "needs"].includes(shown))} ${status === "never" ? "" : `<span id="v-summary">${esc(syncing ? "syncing…" : st.summary)}</span>`}</dd></div>
    <div class="vrow"><dt>Last sync</dt><dd id="v-last">${last ? (st.needs_you ? `${esc(ago(st.last_sync_ts))} · last run: stopped, ${st.vault_locked ? "the vault is locked (unlock it on Home)" : "source folder missing"}; nothing changed` : `${esc(ago(st.last_sync_ts))} · last run ${esc(last.result)} by ${esc(last.trigger.toUpperCase())}: ${esc(resultSummary(last))}`) : "never synced"}</dd></div>
    ${st.last_changes ? `<div class="vrow"><dt>Changes</dt><dd>${changesHtml("view:" + d.config.name, st.last_changes, "v-changes").replace("Last sync: ", "")}</dd></div>` : ""}
    <div class="vrow"><dt>Next sync</dt><dd id="v-next">${esc(nextText({ paused: st.paused, schedule: st.schedule, next_sync: st.next_sync }))}${st.schedule !== "off" && !st.paused ? " · " + esc(intervalText(st.schedule)) : ""}</dd></div>
    <div class="vrow"><dt>Files owned</dt><dd id="v-owned">${esc(st.owned_files)}</dd></div>
    ${dest.type === "github" ? `<div class="vrow"><dt>GitHub</dt><dd>${dest.last_commit ? `last commit ${commitLink(dest.last_commit)} <span class="muted small">${esc(ago(dest.last_commit.ts))}</span>` : '<span class="muted">no commit yet</span>'}
      ${dest.error ? `<div class="derr small">✕ ${esc(dest.error)}</div>` : ""}
      ${dest.clone ? `<div class="small">Local clone: ${clone ? `${esc(clone.status)}${clone.reason ? " · " + esc(clone.reason) : ""}` : '<span class="muted">not updated yet</span>'}</div>` : ""}</dd></div>` : ""}`;
}

function needSummary(n) {
  const name = n.pipeline;
  const go = `<button class="btn sm" data-act="view-activity" data-name="${esc(name)}">Handle in Activity</button>`;
  if (n.kind === "source_missing") return missingSourceHtml(name, n.label, n.path);
  if (n.kind === "guard") return `<div class="vneed warn" data-need="guard">▲ <b>Sync blocked by the guard:</b> it would delete or overwrite ${n.count} of ${n.total} files (${n.percent}%). Approve or cancel it in Activity. ${go}</div>`;
  if (n.kind === "conflicts") return `<div class="vneed warn" data-need="conflicts">▲ <b>${plural(n.items.length, "conflict")} skipped</b> (nothing was overwritten): ${n.items.slice(0, 3).map((c) => `<code>${esc(c.path)}</code>`).join(", ")}${n.items.length > 3 ? ", …" : ""} ${go}</div>`;
  if (n.kind === "error") return `<div class="vneed err" data-need="error">✕ <b>${esc(n.title)}</b> ${[...(n.connectors || []).map((c) => `${esc(c.error)} ${esc(c.next_step)}`), ...(n.folders || []).map((f) => `Source ${esc(f.label)} was not found or is unreadable.`), ...(n.file_errors || []).map((f) => `${esc(f.path)}: ${esc(f.error)}`)].join(" ")} <span class="safe">Nothing it owns was deleted.</span> ${go}</div>`;
  if (n.kind === "target" && n.category === "sign-in-expired") return `<div class="vneed warn" data-need="target">▲ <b>${esc(n.title)}</b> ${esc(n.message)} <span class="safe">Nothing was written or deleted.</span> ${reauthHtml(name)} ${go}</div>`;
  if (n.kind === "target") return `<div class="vneed err" data-need="target">✕ <b>${esc(n.title)}</b> ${esc(n.message)} ${esc(n.next_step)} <button class="btn sm" data-act="view-edit" data-name="${esc(name)}">Open the target in Edit</button> ${go}</div>`;
  if (n.kind === "clone") return `<div class="vneed err" data-need="clone">✕ <b>${esc(n.title)}</b> ${esc(n.reason)} ${esc(n.next_step)} ${go}</div>`;
  if (n.kind === "import") return `<div class="vneed warn" data-need="import">▲ <b>${esc(n.title)}</b> ${esc(n.message)} ${go}</div>`;
  return `<div class="vneed err" data-need="${esc(n.kind)}">✕ <b>${esc(n.title || n.kind)}</b> ${esc(n.message || "")} ${go}</div>`;
}

function viewNeedsHtml(d) {
  const items = d.needs_you;
  return `<h2>Needs you</h2>${items.length ? items.map(needSummary).join("") : `<div class="muted small" id="v-noneed">Nothing needs you for this pipeline.</div>`}`;
}

function viewRunsHtml(d, name) {
  const runs = d.runs;
  return `<div class="phead"><div class="t"><h2 id="vr-h" style="margin:0">${runs.length === 0 ? "No sync has run yet" : runs.length === 1 ? "Last run" : `Last ${runs.length} runs`}</h2></div>
      <button class="btn sm" data-act="view-activity" data-name="${esc(name)}">See all in Activity</button></div>
    ${runs.length ? `<div style="overflow:auto"><table class="runs"><thead><tr><th style="width:120px">Time</th><th>Pipeline</th><th>Trigger</th><th>Changes</th><th>Result</th><th>Duration</th></tr></thead><tbody>${runRows(runs, "v")}</tbody></table></div>`
      : `<div class="pempty" id="v-noruns">Press Sync now to run the first one.</div>`}`;
}

/* MD-135: the first panel. Line 1 is generated by the server from the configuration; line 2 comes from the dry run (or the Needs-you text). */
function nextSyncLine(v) {
  const needs = (v.data && v.data.needs_you) || [];
  const problem = v.plan && v.plan.problems && v.plan.problems[0];
  const need = needs[0];
  if (problem || need) {
    const t = (problem && problem.title) || (need && (need.title || need.kind));
    return `Needs you: ${t}.`;
  }
  if (v.planError) return "Next sync: could not be planned. See below.";
  if (!v.plan) return "Next sync: checking…";
  const c = v.plan.preview.counts, n = (c.create || 0) + (c.update || 0) + (c.rename || 0) + (c.delete || 0);
  if (!n) return "Next sync: nothing to change.";
  const parts = [`${c.create || 0} new`, `${c.update || 0} updated`];
  if (c.rename) parts.push(`${c.rename} renamed`);
  parts.push(`${c.delete || 0} removed`);
  return `Next sync: ${parts.join(", ")}.`;
}

const AFTER_THAT = { "15m": "it checks every 15 minutes", "1h": "it checks every hour", "6h": "it checks every 6 hours", daily: "it checks once a day", off: "it runs only when you sync it" };

function neverSyncedLine(v) {          // MD-147: the one next step of a pipeline that has not synced yet
  const st = v.data.status, needs = (v.data.needs_you || []).length || (v.plan && v.plan.problems && v.plan.problems.length);
  if (st.paused) return "Not synced yet. Resume the pipeline, then press Sync now.";
  if (needs || v.planError) return null;
  const c = v.plan && v.plan.preview.counts, n = c ? (c.create || 0) + (c.update || 0) : null;
  const what = n === null ? "your files" : plural(n, "file");
  return `Not synced yet. Press <b>Sync now</b> to write ${what}; after that ${AFTER_THAT[st.schedule] || "it keeps them current"}.`;
}

function aboutNextHtml(v) {
  const never = v.data.status.status === "never" ? neverSyncedLine(v) : null;
  return never !== null ? never : esc(nextSyncLine(v));
}

function viewSummaryHtml(v) {
  return `<section class="card vsummary" id="v-about" aria-label="What this pipeline does"><p class="vsum-what" id="v-about-what">${esc(v.data.summary || "")}</p>
    <p class="vsum-next" id="v-about-next" aria-live="polite">${aboutNextHtml(v)}</p></section>`;
}

function renderView() {
  const v = S.view, main = $("main");
  const crumb = `<div class="crumb"><button data-act="go" data-screen="home">Pipelines</button> / ${esc(v.name)}</div>`;
  if (v.notFound) {
    main.innerHTML = `${crumb}<div class="card empty" id="view-notfound"><h1>Pipeline not found</h1>
      <p>There is no pipeline named <code>${esc(v.name)}</code> in this app.</p><button class="btn" data-act="go" data-screen="home">Back to the pipelines</button></div>`;
    return;
  }
  if (!v.data) {
    main.innerHTML = `${crumb}<div class="card empty"><p class="muted">${v.error ? "✕ " + esc(v.error) : "Loading…"}</p></div>`;
    return;
  }
  const c = v.data.config;
  const row = S.state.pipelines.find((p) => p.name === v.name);
  const syncing = !!row && (S.syncingNow.has(v.name) || S.syncingNow.has("*") || row.syncing);
  const status = syncing ? "syncing" : row ? row.status : "never";
  const blocked = !!row && row.status === "blocked" && row.pending_approval;
  const firstSync = status === "never" && !c.paused;          // MD-147: Sync now is the primary button until the first sync has run
  const dl = (k, html, id) => `<div class="vrow"${id ? ` id="${id}"` : ""}><dt>${esc(k)}</dt><dd>${html}</dd></div>`;
  main.innerHTML = `${crumb}
    <div class="bar"><h1 class="grow">${esc(c.name)} <span class="vmode" id="view-mode-tag">view only</span> ${pill(shownStatus(status, row), ["conflicts", "blocked", "error", "needs"].includes(shownStatus(status, row)))}</h1>
      <button class="btn${firstSync ? " primary" : ""}" data-act="sync" data-name="${esc(c.name)}" ${c.paused || syncing || blocked || S.state.syncing ? "disabled" : ""}
        title="${blocked ? "Approve or cancel the blocked sync in Activity first" : c.paused ? "Paused: resume to sync" : ""}">Sync now</button>
      <button class="btn" data-act="${c.paused ? "resume" : "pause"}" data-name="${esc(c.name)}" ${syncing ? "disabled" : ""}>${c.paused ? "Resume" : "Pause"}</button>
      <button class="btn${firstSync ? "" : " primary"}" data-act="view-edit" data-name="${esc(c.name)}">Edit</button>
      <button class="btn danger" data-rm="start" data-name="${esc(c.name)}" ${syncing ? "disabled" : ""} title="${syncing ? "Wait for the sync to finish" : ""}">Remove pipeline</button>
      <button class="btn ghost" data-act="go" data-screen="home">Back to home</button></div>
    ${rmCardHtml(c.name)}
    <div class="view" id="view">
      ${viewSummaryHtml(v)}
      <section class="card vcfg" aria-label="Status"><h2>Status</h2><dl>${viewStatusHtml(v.data, syncing)}</dl></section>
      <section class="card vcfg" id="v-needs" aria-label="Needs you">${viewNeedsHtml(v.data)}</section>
      <section class="card vcfg" aria-label="Configuration"><h2>Configuration</h2><dl>
        ${dl("State", c.paused ? "Paused: every trigger (UI, CLI, MCP) skips it until resumed" : "Active")}
        ${dl("Sources", `<ul class="vsrcs">${c.sources.map(viewSource).join("")}</ul>`, "v-sources")}
        ${dl("Where the files land", viewDest(c.destination), "v-dest")}
        ${dl("Target path", `<span class="mono">${esc(c.target)}</span>`)}
        ${dl("File names", viewPolicyText(c.name_policy))}
        ${dl("Include", `<span class="mono">${c.include.map(esc).join("<br>") || "—"}</span>`)}
        ${dl("Exclude", `<span class="mono">${c.exclude.map(esc).join("<br>") || "—"}</span>`)}
        ${dl("When a source file is deleted", c.on_source_delete === "keep" ? "keep the copy" : "delete the copy")}
        ${dl("Big-change guard", `${esc(c.guard.threshold_percent)}% of the files (at least ${esc(c.guard.min_files)} files)`)}
      </dl></section>
      <section class="card preview" id="view-plan" aria-labelledby="vp-h"></section>
      <section class="card preview" id="view-runs" aria-labelledby="vr-h">${viewRunsHtml(v.data, c.name)}</section>
    </div>`;
  renderViewPlan();
}

/* ---------------------------------------------------------------- editor */
// The kinds of source the editor can add (MD-99). A new connector adds one entry here and its row renderer.
const SOURCE_TYPES = [["add-source", "Folder"], ["add-jira", "Jira"]];
const NAME_POLICIES = [["date-name", "Date + name (default)"], ["name", "Name only"], ["source-name", "Folder label + name"],
  ["date-source-name", "Date + folder label + name"], ["original", "Original (keep the source file name)"], ["custom", "Custom pattern"]];

function newDraft() {
  return {
    editing: null, name: "", nameTouched: false,
    sources: [],                                   // MD-99: no source row until the user adds one (Folder or Jira)
    target: DEFAULTS.target, include: DEFAULTS.include, exclude: DEFAULTS.exclude,
    onDelete: "delete", namePolicy: "date-name", namePattern: "", guard: DEFAULTS.guard, guardMin: 5, removed: [], owned: {},
    checks: {}, pv: null, saving: false, blocked: null, serverErrors: null, seq: 0,
    dest: newDest(),
  };
}

/* ---------------------------------------------------------------- target step (MD-82) */
function newDest() {
  const app = S.state && S.state.github_app;
  return { kind: "folder", dir: "", repo: "", branch: "", auth: app && app.configured ? "sign-in" : "token", token: "", newToken: "", pending: null, clone: "", credential: "",
    existing: false, test: null, busy: false, needPass: false, passphrase: "", tokenNote: "",
    flow: null, login: "", repos: null, signMsg: "" };
}

/* MD-143: the branches of the typed repository as a list (default preselected); typing a name stays available. */
function branchListHtml(x) {
  const b = x.branches;
  if (!b) return "";
  if (b.loading) return `<div id="d-branchlist" class="muted small" role="status">Looking up the branches…</div>`;
  if (b.error) return `<div id="d-branchlist" class="msg warn" role="status">▲ <span>${esc(b.error)}</span></div>`;
  const current = (x.branch || "").trim();
  const rows = b.list.map((i) => `<li><label><input type="radio" name="dbr" data-bind="dest-branch-pick" value="${esc(i.name)}" ${(current ? i.name === current : i.default) ? "checked" : ""}> <span>${esc(i.name)}</span>${i.default ? ` <span class="muted small">default</span>` : ""}${i.protected ? ` <span class="muted small">protected: direct pushes are blocked</span>` : ""}</label></li>`).join("");
  return `<div id="d-branchlist"><ul class="fr-plist">${rows}</ul>${b.more ? `<small class="muted">Showing the first ${b.list.length}. Type a name above to use another.</small>` : ""}</div>`;
}

function renderBranchList() { const w = $("d-branchwrap"); if (w) w.innerHTML = branchListHtml(S.ed.dest); }

function destFromSaved(d) {
  const x = newDest();
  if (!d) return x;
  if (d.type === "github") Object.assign(x, { kind: "github", repo: d.repo || "", branch: d.branch || "", auth: d.auth || "token", clone: d.clone || "", credential: d.credential || "", existing: true });
  else if (d.type === "directory") Object.assign(x, { kind: "folder", dir: d.path || "", existing: true });
  return x;
}

let signinTimer;
async function startSignin(again) {
  const e = S.ed, x = e.dest;
  x.signMsg = ""; x.test = null;
  const r = await api("POST", "/api/signin/start", {});
  if (!r.ok) { x.signMsg = "✕ " + ((r.data && r.data.error) || "could not start the sign-in"); return renderDest(); }
  x.flow = r.data; renderDest();
  const poll = async () => {
    if (S.ed !== e || !x.flow) return;
    const p = await api("POST", "/api/signin/poll", { flow: x.flow.flow });
    if (S.ed !== e || !x.flow) return;
    if (!p.ok) { x.flow = null; x.signMsg = "✕ " + ((p.data && p.data.error) || "the sign-in failed"); return renderDest(); }
    if (p.data.status === "pending") { signinTimer = setTimeout(poll, (p.data.interval || x.flow.interval || 5) * 1000); return undefined; }
    x.flow = null;
    if (p.data.status !== "ok") { x.signMsg = "✕ " + p.data.message; return renderDest(); }
    x.login = p.data.login; x.pending = p.data.pending; x.repos = p.data.repos;
    if (e.editing) {
      const s = await api("POST", `/api/pipelines/${encodeURIComponent(e.editing)}/sign-in`, { pending: x.pending });
      x.pending = null;
      x.signMsg = s.ok ? `Signed in again as ${p.data.login}.` : "✕ " + ((s.data && ((s.data.errors && s.data.errors[0] && s.data.errors[0].message) || s.data.error)) || "the sign-in was not accepted");
      if (s.ok) x.credential = x.credential || "set";
      renderDest(); await refresh(); return undefined;
    }
    if (x.repos.length === 1 && !x.repo.trim()) x.repo = x.repos[0];
    renderDest(); renderEditorLive(); return schedulePreview(0);
  };
  clearTimeout(signinTimer);
  signinTimer = setTimeout(poll, (x.flow.interval || 5) * 1000);
  return undefined;
}

function destPayload() {
  const x = S.ed.dest;
  if (x.kind === "github") {
    const out = { type: "github", repo: x.repo.trim(), branch: x.branch.trim(), auth: x.auth, clone: x.clone.trim() };
    if (x.credential) out.credential = x.credential;
    if (x.pending) out.pending = x.pending;
    return out;
  }
  return x.dir.trim() ? { type: "directory", path: x.dir.trim() } : null;
}

function appForm() {
  const f = S.appForm, app = (S.state && S.state.github_app) || {};
  const err = (k) => (f.errors || []).filter((e) => e.field === k).map((e) => `<div class="msg err" role="alert">✕ <span>${esc(e.message)}</span></div>`).join("");
  return `<div class="tf appform" id="d-appform">
    <label class="tf"><span>Client ID of the GitHub App</span><input type="text" id="a-client" data-bind="app-client_id" value="${esc(f.client_id)}" placeholder="Iv23liXXXXXXXXXXXX" spellcheck="false" autocomplete="off"><small class="muted">On github.com: Settings, Developer settings, GitHub Apps, your app, "Client ID". Public, not a secret.</small>${err("client_id")}</label>
    <label class="tf"><span>App slug</span><input type="text" id="a-slug" data-bind="app-slug" value="${esc(f.slug)}" placeholder="my-markdown-pipeline" spellcheck="false" autocomplete="off"><small class="muted">The last part of the app address on github.com (github.com/apps/<b>slug</b>).</small>${err("slug")}</label>
    <div class="row-acts"><button type="button" class="btn sm primary" data-act="app-save" ${f.busy ? "disabled" : ""}>Save</button>${app.configured ? `<button type="button" class="btn ghost sm" data-act="app-toggle">Cancel</button>` : ""}</div></div>`;
}

function signinBlock(x, saved) {
  const app = (S.state && S.state.github_app) || {};
  S.appForm = S.appForm || { open: false, client_id: "", slug: "", errors: [], busy: false };
  if (!app.configured) {
    return `<div class="msg warn" role="status">▲ <span><b>Sign in with GitHub is not set up on this computer yet.</b> It needs the <b>Client ID</b> of a GitHub App that you register once on github.com (about 5 minutes).
      <a href="/github-app-registration.md" target="_blank" rel="noopener noreferrer">Open the step-by-step</a>, then paste the Client ID and the app slug below. Until then, choose <b>Personal access token</b> above: it works without any of this.</span></div>${appForm()}`;
  }
  const origin = app.source === "default" ? `<span id="d-appsource">shipped default</span>` : `<span id="d-appsource">your own app</span>`;
  const reset = app.source === "user" && app.has_default ? ` <button type="button" class="link" data-act="app-reset">Use the shipped default</button>` : "";
  const settings = S.appForm.open ? appForm() : `<div class="muted small" id="d-appline">GitHub App: <code>${esc(app.client_id)}</code> (${esc(app.slug)}) · ${origin} <button type="button" class="link" data-act="app-toggle">Change</button>${reset}</div>`;
  return signinCore(x, saved, app) + settings;
}

function signinCore(x, saved, app) {
  const note = x.signMsg ? `<div class="msg ${x.signMsg.startsWith("✕") ? "err" : "ok"}" role="status"><span>${esc(x.signMsg)}</span></div>` : "";
  if (x.flow) {
    return `<div class="tf signin" id="d-signin"><span>Sign in with GitHub</span>
      <div>1. Open <a href="${esc(x.flow.verification_uri)}" target="_blank" rel="noopener noreferrer">${esc(x.flow.verification_uri)}</a></div>
      <div>2. Enter this code: <b class="usercode" id="d-code">${esc(x.flow.user_code)}</b></div>
      <div class="muted small" role="status">Waiting for you to approve it on GitHub…</div>
      <div class="row-acts"><button type="button" class="btn ghost sm" data-act="signin-cancel">Cancel</button></div></div>`;
  }
  if (x.pending && x.login) {
    const repos = x.repos || [];
    const picked = repos.includes(x.repo);
    const picker = repos.length
      ? `<label class="tf"><span>Repositories the app is installed on</span><select data-bind="dest-repo-pick" id="d-repopick"><option value="">Choose a repository…</option>${repos.map((r) => `<option value="${esc(r)}" ${r === x.repo ? "selected" : ""}>${esc(r)}</option>`).join("")}</select></label>` : "";
    const missing = x.repo.trim() && !picked ? `<div class="msg warn" role="status">▲ <span>The GitHub App is not installed on <b>${esc(x.repo)}</b>. <a href="${esc(app.install_url)}" target="_blank" rel="noopener noreferrer">Install it on that repository</a>, then <button type="button" class="link" data-act="signin-recheck">check again</button>.</span></div>` : "";
    return `<div class="tf signin" id="d-signin"><div class="msg ok" role="status">✓ <span>Signed in as <b>${esc(x.login)}</b>. Nothing is stored until you save the pipeline.</span></div>${picker}${missing}${note}</div>`;
  }
  if (saved) {
    return `<div class="tf signin" id="d-signin"><span>Sign in</span><div class="muted small" id="d-tokstate">${x.credential ? `✓ signed in${x.login ? " as " + esc(x.login) : ""} (the sign-in is kept in the app vault; never shown)` : "✕ not signed in: syncs stop until you sign in"}</div>
      <div class="row-acts"><button type="button" class="btn sm" data-act="signin-start">${x.credential ? "Sign in again" : "Sign in with GitHub"}</button>
      ${x.credential ? `<button type="button" class="btn ghost sm" data-act="signin-out">Sign out</button>` : ""}</div>${note}</div>`;
  }
  return `<div class="tf signin" id="d-signin"><div class="row-acts"><button type="button" class="btn sm primary" data-act="signin-start">Sign in with GitHub</button></div>
    <small class="muted">You approve the app on github.com with a short code. Install the app only on the repositories you choose. No token to create or paste.</small>${note}</div>`;
}

function destSummary(d) {
  if (!d) return "your output folder";
  if (d.type === "github") return `${d.repo} @ ${d.branch}`;
  return d.path || "your output folder";
}

function renderDest() {
  const box = $("dest-box");
  if (!box || !S.ed) return;
  const x = S.ed.dest, gh = x.kind === "github", saved = x.existing;
  const t = x.test;
  let result = "";
  if (x.busy) result = `<div class="msg" role="status">Checking…</div>`;
  else if (t && t.ok && t.connection) {
    const c = t.connection;
    result = `<div class="msg ok" role="status">✓ <span>Connected as <b>${esc(c.user)}</b> · ${esc(c.repo)} @ ${esc(c.branch)} · head <code>${esc(c.head.slice(0, 7))}</code> · write ${c.write_allowed ? "allowed" : "NOT allowed"}. Nothing was written.</span></div>`;
  } else if (t && t.ok && t.directory) result = `<div class="msg ok" role="status">✓ <span>${esc(t.directory.path)} is writable. Nothing was written.</span></div>`;
  else if (t) result = (t.errors || []).map((er) => `<div class="msg err" role="alert">✕ <span>${esc(er.message)}</span></div>`).join("");
  const pass = x.needPass ? `<div class="msg warn" role="alert">▲ <span>The app's vault is from an earlier version and needs its passphrase once. Your tokens then move to a file only your user can read.</span>
      <label class="tf"><span>Vault passphrase</span><input type="password" id="d-pass" data-bind="dest-pass" autocomplete="off"></label>
      <button type="button" class="btn sm" data-act="vault-unlock">Unlock</button></div>` : "";
  const kind = `<div class="radios" role="radiogroup" aria-labelledby="l-dest">
      <label><input type="radio" name="dk" data-bind="dest-kind" value="folder" ${gh ? "" : "checked"} ${saved ? "disabled" : ""}> Folder</label>
      <label><input type="radio" name="dk" data-bind="dest-kind" value="github" ${gh ? "checked" : ""} ${saved ? "disabled" : ""}> GitHub repository</label></div>`;
  const f = (key, label, hint, value, ph, extra = "", after = "") => `<label class="tf"><span>${label}</span><span class="fr-pathrow"><input type="text" id="d-${key}" data-bind="dest-${key}" value="${esc(value)}" placeholder="${esc(ph || "")}" spellcheck="false" autocomplete="off" ${extra}>${after}</span><small class="muted">${hint}</small></label>`;
  let body;
  if (!gh) {
    body = f("dir", "Target folder", `The folder your files go to. Inside it, files follow "Where the files go" below. Leave empty to use your output folder (${esc(S.state ? S.state.brain.path : "")}).`, x.dir, "empty = your output folder", saved ? "readonly" : "", saved ? "" : brBrowseButton("d-dir", "output", "a folder to write to"));
  } else {
    const authRadios = `<div class="radios" role="radiogroup" aria-label="How to authenticate">
        <label><input type="radio" name="da" data-bind="dest-auth" value="sign-in" ${x.auth === "sign-in" ? "checked" : ""} ${saved ? "disabled" : ""}> Sign in with GitHub</label>
        <label><input type="radio" name="da" data-bind="dest-auth" value="token" ${x.auth === "token" ? "checked" : ""} ${saved ? "disabled" : ""}> Personal access token</label></div>`;
    const tokenBlock = x.auth === "sign-in" ? signinBlock(x, saved) : saved
      ? `<div class="tf"><span>Token</span><div class="muted small" id="d-tokstate">${x.credential ? "✓ a token is set (kept in the app vault; never shown)" : "✕ no token is set: syncs stop until you enter one"}</div>
          <input type="password" id="d-newtoken" data-bind="dest-newtoken" autocomplete="new-password" placeholder="new token (typing is hidden)" spellcheck="false">
          <div class="row-acts"><button type="button" class="btn sm" data-act="dest-replace-token" ${x.busy ? "disabled" : ""}>${x.credential ? "Replace token" : "Set token"}</button>
          ${x.credential ? `<button type="button" class="btn ghost sm" data-act="dest-remove-token">Remove token</button>` : ""}</div>${x.tokenNote ? `<div class="msg ${x.tokenNote.startsWith("✕") ? "err" : "ok"}" role="status"><span>${esc(x.tokenNote)}</span></div>` : ""}</div>`
      : `<label class="tf"><span>Personal access token</span><input type="password" id="d-token" data-bind="dest-token" autocomplete="new-password" placeholder="${x.pending ? "checked: not shown again" : "github_pat_… (typing is hidden)"}" spellcheck="false" ${x.pending ? "disabled" : ""}>
          <small class="muted">A fine-grained token for this one repository with Contents read and write. It goes once to this local app, is checked, and is kept in the app vault on this computer. It is never shown again.</small></label>`;
    body = `${f("repo", "Repository", "OWNER/REPO", x.repo, "acme/team-notes", saved ? "readonly" : "")}
      ${f("branch", "Branch", "empty = the repository's default branch; use a dedicated branch such as md-sync if the default is protected", x.branch, "main", saved ? "readonly" : "", saved ? "" : `<button type="button" class="btn sm" data-act="dest-branches">Choose a branch…</button>`)}
      ${saved ? "" : `<div id="d-branchwrap">${branchListHtml(x)}</div>`}
      ${authRadios}${tokenBlock}
      ${f("clone", "Local clone (optional)", "a git clone of the repository whose origin is its https URL; fast-forwarded after each sync when safe, so agents can search it", x.clone, "/path/to/clone")}`;
  }
  const testBtn = `<div class="row-acts"><button type="button" class="btn sm" data-act="dest-test" ${x.busy ? "disabled" : ""}>Test connection</button>
    ${saved && S.ed.editing ? `<button type="button" class="btn ghost sm" data-act="dest-change">Change target…</button>` : ""}</div>`;
  const changing = S.ed.changing ? `<div class="msg warn" role="status">▲ <span>Moving this pipeline to a different target. The old target keeps its files untouched (nothing there is changed or deleted); the preview shows what the new target needs.
      <button type="button" class="link" data-act="dest-change-cancel">Cancel the change</button></span></div>` : "";
  box.innerHTML = `${changing}${kind}<div class="tform">${body}${testBtn}${result}${pass}</div>`;
}

async function openEditor(name, returnTo) {
  if (!name) { S.ed = newDraft(); S.screen = "editor"; render(); schedulePreview(0); return; }
  const r = await api("GET", `/api/pipelines/${encodeURIComponent(name)}`);
  if (!r.ok) { toast("Could not load the pipeline"); return; }
  const d = r.data.draft, e = newDraft();
  e.editing = name; e.name = d.name; e.nameTouched = true; e.returnTo = returnTo || null;
  e.sources = d.sources.map((s) => (s.type === "jira"
    ? { type: "jira", label: s.label, site: s.site, jql: s.jql, email_ref: s.email_ref, token_ref: s.token_ref, path: "", lblTouched: true, orig: s.label, enabled: s.enabled !== false }
    : { type: "folder", path: s.path, label: s.label, lblTouched: true, orig: s.label, enabled: s.enabled !== false }));
  e.target = d.target; e.include = d.include.join("\n"); e.exclude = d.exclude.join("\n");
  e.namePolicy = d.name_policy || "original"; e.namePattern = d.name_pattern || "";
  e.onDelete = d.on_source_delete; e.guard = d.guard_threshold_percent; e.guardMin = d.guard_min_files;
  e.owned = r.data.owned || {};
  e.dest = destFromSaved(d.destination);
  e.dest.existing = true;                       // an edit shows the saved target read-only; moving it is Change target…
  if (r.data.signin) e.dest.login = r.data.signin.login;
  S.ed = e; S.screen = "editor"; render(); schedulePreview(0);
}

function editorDraft() {
  const e = S.ed;
  return {
    destination: destPayload(),
    name: e.name, target: e.target, name_policy: e.namePolicy, name_pattern: e.namePattern, include: e.include, exclude: e.exclude, on_source_delete: e.onDelete,
    guard_threshold_percent: Number(e.guard) || DEFAULTS.guard, guard_min_files: Number(e.guardMin) || 5,
    sources: e.sources.map((s) => (s.type === "jira"
      ? { type: "jira", label: s.label, site: s.site, jql: s.jql, email_ref: s.email_ref, token_ref: s.token_ref, email: s.email || "", token: s.token || "", pending: s.pending || "", enabled: s.enabled !== false }
      : { path: s.path, label: s.label, enabled: s.enabled !== false })),
  };
}

let previewTimer;
function schedulePreview(delay = 250) {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(runPreview, delay);
}

async function runPreview() {
  const e = S.ed;
  if (!e) return;
  const seq = ++e.seq;
  e.loading = true;
  if ((e.destination && e.destination.type === "github") || e.sources.some((s) => s.type === "jira")) renderPreview();   // reading GitHub or Jira takes a moment
  const body = { draft: editorDraft(), editing: e.editing, delete_labels: e.removed.filter((x) => x.del).map((x) => x.label) };
  const [pv, ...checks] = await Promise.all([
    api("POST", "/api/preview", body),
    ...e.sources.map((s) => (s.type !== "jira" && s.path.trim() ? api("POST", "/api/folder-check", { path: s.path }) : Promise.resolve(null))),
  ]);
  if (S.ed !== e || e.seq !== seq) return; // a newer request superseded this one
  e.loading = false;
  e.pv = pv.ok ? pv.data : null;
  const shown = e.pv && e.pv.preview && e.pv.preview.counts;
  if (shown && shown.all > 0 && !e.dryTracked) { e.dryTracked = true; track("dry_run_viewed", { items_bucket: bucketOf(shown.all), would_delete_bucket: bucketOf(shown.deleted || 0) }); }
  e.checks = {};
  e.sources.forEach((s, i) => { e.checks[i] = checks[i] && checks[i].ok ? checks[i].data : null; });
  e.blocked = null; e.serverErrors = null;
  renderEditorLive();
}

function closeAddMenu() { const m = $("addmenu"); if (m) m.open = false; }

function suggestName() {
  const e = S.ed;
  if (e.nameTouched || e.editing) return;
  const paths = e.sources.filter((s) => s.type !== "jira").map((s) => s.path.trim().replace(/[\\/]+$/, "")).filter(Boolean);
  if (!paths.length) { e.name = slug((e.sources.find((s) => (s.label || "").trim()) || {}).label || ""); return; }     // no folder: the first source's label
  if (paths.length === 1) { e.name = slug(base(paths[0])); return; }
  const parts = paths.map((p) => p.split(/[\\/]/));
  let i = 0;
  while (parts.every((x) => x[i] !== undefined && x[i] === parts[0][i])) i++;
  e.name = slug(parts[0][i - 1] || "pipeline");
}

function renderEditor() {
  const e = S.ed;
  $("main").innerHTML = `
    <div class="crumb"><button data-act="go" data-screen="home">Pipelines</button> / ${e.returnTo ? `<button data-act="editor-cancel">${esc(e.returnTo)}</button> / ` : ""}${e.editing ? "Edit " + esc(e.editing) : "New pipeline"}</div>
    <div class="bar"><h1 class="grow">${e.editing ? "Edit pipeline" : "New pipeline"}</h1></div>
    <div class="editor">
      <form class="card fields" onsubmit="return false" novalidate>
        <div class="field"><span class="lbl" id="l-srcs">Sources</span>
          <div class="srcs" id="srcs" role="list" aria-labelledby="l-srcs"></div>
          <div id="m-sources"></div>
          <div id="m-removed"></div>
          <details class="addmenu" id="addmenu"><summary class="btn sm addsrc">+ Add source</summary>
            <div class="addopts" role="group" aria-label="Kind of source">${SOURCE_TYPES.map(([act, label]) => `<button type="button" class="btn sm" data-act="${act}">${esc(label)}</button>`).join("")}</div></details>
          <div class="hint">Read only: folders and Jira are never modified. The rules below apply to all sources.</div></div>
        <div class="field" id="w-name"><label for="f-name">Name</label>
          <input type="text" id="f-name" data-bind="name" value="${esc(e.name)}" spellcheck="false" autocomplete="off" ${e.editing ? "readonly" : ""}>
          <div id="m-name"></div></div>
        <div class="field" id="w-dest"><span class="lbl" id="l-dest">Where the files land</span>
          <div id="dest-box"></div><div id="m-dest"></div></div>
        <div class="field" id="w-target"><label for="f-template">Where the files go <span class="muted small">(optional; empty = the root of the output folder)</span></label>
          <input type="text" id="f-template" data-bind="target" value="${esc(e.target)}" placeholder="e.g. raw/{relpath}" spellcheck="false" autocomplete="off">
          <div class="chips" aria-label="Insert variable">${VARS.map(([v, d]) => `<button type="button" class="chip" title="${esc(d)}" data-act="chip" data-var="${esc(v)}">${esc(v)}</button>`).join("")}</div>
          <div id="m-target"></div><div class="example" id="ex"></div></div>
        <div class="field" id="w-name_policy"><label for="f-policy">File names</label>
          <select id="f-policy" data-bind="namePolicy">${NAME_POLICIES.map(([v, d]) => `<option value="${esc(v)}" ${e.namePolicy === v ? "selected" : ""}>${esc(d)}</option>`).join("")}</select>
          <input type="text" id="f-pattern" data-bind="namePattern" value="${esc(e.namePattern)}" placeholder="{date}_{slug}" spellcheck="false" autocomplete="off" ${e.namePolicy === "custom" ? "" : "hidden"} aria-label="Custom file-name pattern">
          <div class="hint">Only the file name changes; folders come from the target path. Variables: {date} {slug} {name} {source} {pipeline}. Changing it on a pipeline that already has files renames them (shown in the plan).</div>
          <div id="m-name_policy"></div></div>
        <div class="field" id="w-include"><label for="f-include">Include <span class="muted small">(one glob per line)</span></label>
          <textarea id="f-include" data-bind="include" rows="2" spellcheck="false">${esc(e.include)}</textarea>
          <div id="m-include"></div></div>
        <div class="field"><label for="f-exclude">Exclude</label>
          <textarea id="f-exclude" data-bind="exclude" rows="4" spellcheck="false">${esc(e.exclude)}</textarea></div>
        <div class="field"><span class="lbl" id="l-del">When a source file is deleted</span>
          <div class="radios" role="radiogroup" aria-labelledby="l-del">
            <label><input type="radio" name="od" data-bind="onDelete" value="delete" ${e.onDelete === "delete" ? "checked" : ""}> delete</label>
            <label><input type="radio" name="od" data-bind="onDelete" value="keep" ${e.onDelete === "keep" ? "checked" : ""}> keep</label></div>
          <div class="hint" id="h-del"></div></div>
        <details class="adv"><summary>Advanced</summary>
          <div class="field"><label for="f-guard">Big-change guard</label>
            <div class="guardin"><input type="number" id="f-guard" data-bind="guard" min="1" max="100" value="${esc(e.guard)}"> <span>%</span></div>
            <div class="hint">Stop and ask for approval if a sync would delete or overwrite more than this share of the pipeline's files, across all its folders (and at least ${esc(e.guardMin)} files).</div></div>
        </details>
        <div class="formacts" id="acts"></div>
      </form>
      <section class="card preview" aria-labelledby="pv-h" id="pv"></section>
    </div>`;
  renderSourceRows();
  renderDest();
  renderEditorLive();
}

function jiraCredentials(s, i) {
  const vaulted = (r) => (r || "").startsWith("vault:");
  const legacy = (s.email_ref && !vaulted(s.email_ref)) || (s.token_ref && !vaulted(s.token_ref));
  const saved = !!(s.email_ref || s.token_ref);
  const checked = !!s.pending;
  const field = (key, type, label, hint, ph, dis) => `<label class="jf"><span>${label}</span><input type="${type}" data-bind="jira-${key}" data-i="${i}" id="j-${key}${i}" value="${type === "password" ? "" : esc(s[key] || "")}" placeholder="${esc(ph)}" spellcheck="false" autocomplete="${type === "password" ? "new-password" : "off"}" ${dis ? "disabled" : ""}><small class="muted">${hint}</small></label>`;
  const note = legacy ? `<div class="msg warn" role="status">▲ <span>This source still reads its credentials from <code>${esc([s.email_ref, s.token_ref].filter(Boolean).join(", "))}</code> (an older setup). It keeps working; Activity → Needs you offers to move them into the app vault. You can also type new ones here.</span></div>`
    : saved ? `<div class="msg ok" role="status">✓ <span>Email and API token are kept in the app vault (never shown). Type a new value only to replace it.</span></div>` : "";
  return `${note}${field("email", "email", saved ? "New email (optional)" : "Atlassian email", "your Atlassian account email; kept in the app vault", "you@company.com", false)}
      ${field("token", "password", saved ? "New API token (optional)" : "API token", "Create one at https://id.atlassian.com/manage-profile/security/api-tokens. It goes once to this local app, is checked against Jira and kept in the app vault on this computer; it is never shown again.", checked ? "checked: not shown again" : "typing is hidden", checked)}`;
}

function srcSwitch(s, i) {
  const on = s.enabled !== false;
  return `<label class="swtch" title="${on ? "On: this source is read" : "Off: not read, its files are kept"}"><input type="checkbox" role="switch" data-bind="src-enabled" data-i="${i}" aria-label="Source ${esc(s.label || (s.type === "jira" ? "Jira" : "folder " + (i + 1)))} on or off" ${on ? "checked" : ""}> <span>${on ? "On" : "Off"}</span></label>`;
}

/* Jira project picker in the editor (MD-141): after a good token check, tick projects instead of typing a key. */
function jiraPicker(s, i) {
  const canPick = !!s.pending || !!(s.email_ref && s.token_ref);
  if (!canPick || !s.site) return "";
  if (!s.proj) return `<div class="row-acts"><button type="button" class="btn sm" data-act="jira-projects" data-i="${i}">Choose projects</button> <span class="muted small">instead of typing a key or JQL</span></div>`;
  const p = s.proj;
  if (p.notice) {
    return `<div class="jpick"><div class="msg warn" role="status">▲ <span><b>${esc(p.notice.headline)}</b> ${esc(p.notice.fix)}
      ${p.notice.kind === "missing_scope" ? ` <a href="${esc(p.notice.help)}" target="_blank" rel="noopener noreferrer">Fix the token</a>` : ""}</span></div>
      <small class="muted">The JQL above still works. Write your own there.</small></div>`;
  }
  const rows = p.items.map((x) => `<li><label><input type="checkbox" data-bind="jpick" data-i="${i}" data-key="${esc(x.key)}" ${p.ticked[x.key] ? "checked" : ""}> <span>${esc(x.name)} (${esc(x.key)})</span></label></li>`).join("");
  const ask = p.replace ? `<div class="msg warn jpick-replace" role="alertdialog" aria-label="Replace your query">▲ <span>Picking projects will replace the query you wrote.</span>
      <button type="button" class="btn sm" data-act="jira-pick-replace" data-i="${i}">Replace my query</button> <button type="button" class="btn sm" data-act="jira-pick-keep" data-i="${i}">Keep my query</button></div>` : "";
  return `<div class="jpick"><p class="small muted">Leave all unticked to include every project you can see.</p><label class="small" for="jp-search${i}">Search projects</label>
      <input type="search" id="jp-search${i}" data-bind="jpick-search" data-i="${i}" value="${esc(p.query)}" autocomplete="off" spellcheck="false">
      <ul class="fr-plist">${rows || `<li class="muted small">No project matches.</li>`}</ul>
      ${p.isLast ? "" : `<button type="button" class="btn sm" data-act="jira-projects-more" data-i="${i}">Show more projects</button>`}${ask}
      ${Object.keys(p.ticked).length && p.line ? `<p class="small jpick-line">${esc(p.line)}</p>` : ""}</div>`;
}

async function jiraLoadProjects(s, more) {
  const p = (s.proj = s.proj || { items: [], ticked: {}, query: "", isLast: true, next: 0, notice: null, line: "", replace: false });
  const r = await api("POST", "/api/jira/projects", { site: s.site, email_ref: s.email_ref || "", token_ref: s.token_ref || "", email: s.email || "", token: s.token || "", pending: s.pending || "", query: p.query, start_at: more ? p.next : 0 });
  const d = r.ok ? r.data : null;
  if (!d || (!d.ok && !d.headline)) p.notice = { kind: "other", headline: "We couldn't list your projects.", fix: "", help: "/jira-token-help.md" };
  else if (!d.ok || d.kind === "no_projects") p.notice = { kind: d.kind, headline: d.headline, fix: d.fix, help: d.help || "/jira-token-help.md" };
  else { p.notice = null; p.items = more ? p.items.concat(d.projects) : d.projects; p.isLast = d.is_last; p.next = d.next_start; }
  const keep = document.activeElement && document.activeElement.id;
  renderSourceRows();
  if (keep && $(keep) && $(keep).setSelectionRange) { $(keep).focus(); $(keep).setSelectionRange($(keep).value.length, $(keep).value.length); }
}

async function jiraApplyPicks(s) {
  const p = s.proj, keys = Object.keys(p.ticked).sort();
  p.replace = false;
  if (!keys.length) { p.line = ""; return renderSourceRows(); }
  const r = await api("POST", "/api/jira/build-jql", { keys });
  if (!r.ok) { toast((r.data && r.data.error) || "Could not build the query."); return renderSourceRows(); }
  s.jql = r.data.jql; s.builtJql = r.data.jql; p.line = r.data.line; s.jtest = null;
  renderSourceRows(); renderEditorLive(); return schedulePreview(0);
}

function jiraRow(s, i, e) {
  const t = s.jtest;
  const result = s.testing ? `<div class="msg" role="status">Checking Jira…</div>`
    : t && t.ok ? `<div class="msg ok">✓ <span>Connected as <b>${esc(t.user)}</b> · ${plural(t.matches, "issue")} match this JQL. Nothing was written.</span></div>`
    : t ? (t.errors || []).map((x) => `<div class="msg err" role="alert">✕ <span>${esc(x.message)}</span></div>`).join("") : "";
  const f = (key, label, hint, ph) => `<label class="jf"><span>${label}</span><input type="text" data-bind="jira-${key}" data-i="${i}" id="j-${key}${i}" value="${esc(s[key] || "")}" placeholder="${esc(ph || "")}" spellcheck="false" autocomplete="off"><small class="muted">${hint}</small></label>`;
  return `<div class="srcrow jirarow${s.enabled === false ? " off" : ""}" role="listitem" id="w-src${i}">
      <div class="jhead"><b>Jira source</b><span class="muted small">one JQL query is one source; each issue becomes one file</span>${srcSwitch(s, i)}
        <button type="button" class="btn ghost rm" data-act="remove-source" data-i="${i}" aria-label="Remove Jira source" title="Remove">✕</button></div>
      ${f("label", "Label", "becomes {source} in the target path", "jira")}
      ${f("site", "Site", "https://yourcompany.atlassian.net", "https://yourcompany.atlassian.net")}
      ${f("jql", "JQL", "which issues, e.g. project in (MD, MP)", "project = MD")}
      ${jiraPicker(s, i)}
      ${jiraCredentials(s, i)}
      <div class="row-acts"><button type="button" class="btn sm" data-act="jira-test" data-i="${i}" ${s.testing ? "disabled" : ""}>Test connection</button></div>
      <div class="m" id="m-src${i}">${result}</div></div>`;
}

function renderSourceRows() {
  const e = S.ed;
  const head = e.sources.some((s) => s.type !== "jira") ? `<div class="srclbl" aria-hidden="true"><span>Folder path</span><span>Label → {source}</span><span></span></div>` : "";
  $("srcs").innerHTML = head + e.sources.map((s, i) => s.type === "jira" ? jiraRow(s, i, e) : `<div class="srcrow${s.enabled === false ? " off" : ""}" role="listitem" id="w-src${i}">
      <input type="text" id="f-src${i}" data-bind="src-path" data-i="${i}" aria-label="Folder ${i + 1} path" value="${esc(s.path)}" placeholder="~/projects/my-project/docs" spellcheck="false" autocomplete="off">
      <input type="text" id="l-src${i}" data-bind="src-label" data-i="${i}" aria-label="Folder ${i + 1} label" value="${esc(s.label)}" placeholder="label" spellcheck="false" autocomplete="off">
      <button type="button" class="btn ghost rm" data-act="remove-source" data-i="${i}" aria-label="Remove folder ${i + 1}" title="Remove folder">✕</button>
      <div class="m swrow">${srcSwitch(s, i)}${brBrowseButton("f-src" + i, "source", "folder " + (i + 1))}</div>
      <div class="m" id="m-src${i}"></div></div>`).join("");
}

function msgHtml(cls, sym, text) {
  return `<div class="msg ${cls}" ${cls === "err" ? 'role="alert"' : ""}>${sym} <span>${esc(text)}</span></div>`;
}

function renderEditorLive() {
  const e = S.ed;
  if (!e || !$("pv")) return;
  const pv = e.pv, v = pv && pv.validation;
  const errors = v ? v.errors : [], warnings = v ? v.warnings : [];
  const setMsg = (k, html, bad) => {
    const m = $("m-" + k); if (m) m.innerHTML = html || "";
    const w = $("w-" + k); if (w) w.classList.toggle("invalid", !!bad);
  };
  const first = (field) => errors.find((x) => x.field === field);
  const destErr = errors.filter((x) => (x.field || "").startsWith("destination"));
  { const m = $("m-dest"); if (m) m.innerHTML = destErr.map((x) => msgHtml("err", "✕", x.message)).join(""); const w = $("w-dest"); if (w) w.classList.toggle("invalid", destErr.length > 0); }
  for (const field of ["name", "target", "name_policy", "include"]) {
    const err = field === "name" && !e.nameTouched && !e.editing ? null : first(field);     // MD-147: a fresh form has no red; the name check shows once the field was touched
    const warn = warnings.find((x) => x.field === field);
    setMsg(field, err ? msgHtml("err", "✕", err.message) : warn ? msgHtml("warn", "▲", warn.message) : "", !!err);
  }
  if (!first("name") && e.name && !e.editing) {
    $("m-name").innerHTML = `<div class="hint">Used for <code>{pipeline}</code> and in CLI/MCP: <code>${CLI} sync ${esc(e.name)}</code></div>`;
  }
  e.sources.forEach((s, i) => {
    if (s.type === "jira") {
      const box = $("m-src" + i);
      if (box && !s.testing && !s.jtest) {
        const own = errors.filter((x) => x.field === `sources[${i}]`);
        box.innerHTML = own.map((x) => msgHtml("err", "✕", x.message)).join("");
      }
      return;
    }
    const parts = [];
    const check = e.checks[i];
    const pathErr = errors.filter((x) => x.field === `sources[${i}].path`);
    const lblErr = errors.filter((x) => x.field === `sources[${i}].label`);
    if (pathErr.length) parts.push(msgHtml("err", "✕", pathErr[0].message));
    else if (check && check.exists && !check.is_dir) parts.push(msgHtml("err", "✕", `${check.path} is not a folder.`));
    else if (check && !check.exists && s.path.trim()) parts.push(msgHtml("warn", "▲", `Folder not found: ${check.path}. You can save; it will sync when the folder exists.`));
    else if (check && check.is_dir) parts.push(msgHtml("ok", "✓", `Folder found · ${check.md_files} .md files`));
    if (lblErr.length) parts.push(msgHtml("err", "✕", lblErr[0].message));
    setMsg("src" + i, parts.join(""), pathErr.length || lblErr.length);
  });
  const srcBox = $("m-sources");
  if (srcBox) {
    const none = errors.find((x) => x.code === "all-sources-off");
    srcBox.innerHTML = none ? msgHtml("err", "✕", none.message) : !e.sources.length ? `<div class="hint" id="src-start">Start by adding a source: a folder on this computer or a Jira site.</div>` : "";
  }
  const rmBox = $("m-removed");
  if (rmBox) {
    rmBox.innerHTML = e.removed.map((x, k) => {
      const n = x.owned || 0;
      return `<div class="removed">
        <div>Removed <b>${esc(x.label)}</b> <code>${esc(x.path)}</code>: its ${plural(n, "file")} ${x.del ? "will be deleted on Save (through the guard)." : "are kept in your output folder; this pipeline no longer updates them (reported in Activity)."}</div>
        <label><input type="checkbox" data-bind="removed-del" data-k="${k}" ${x.del ? "checked" : ""}> Also delete its ${plural(n, "file")} from your output folder</label></div>`;
    }).join("");
  }
  const delHint = $("h-del");
  if (delHint) delHint.textContent = e.onDelete === "delete" ? "Remove its copy from your output folder (only files this pipeline created). A missing folder never deletes anything." : "Keep its files in your output folder; this pipeline no longer updates them.";
  const ex = $("ex");
  if (ex) {
    const example = pv && pv.preview.example;
    ex.innerHTML = example && !first("target") ? `e.g. ${esc(example.source)}<br>→ ${esc(example.target)}` : "";
  }
  const blocking = errors.length;
  const canSave = v ? v.can_save : false, canSync = v ? v.can_save_and_sync : false;
  const acts = $("acts");
  if (acts) {
    acts.innerHTML = `<button type="button" class="btn ghost" data-act="editor-cancel">Cancel</button>
      ${e.editing ? `<button type="button" class="btn danger" data-rm="start" data-name="${esc(e.editing)}">Remove pipeline</button>` : ""}
      <button type="button" class="btn" data-act="save" ${!canSave || e.saving ? "disabled" : ""}>${e.changing ? "Move and save" : "Save"}</button>
      <button type="button" class="btn primary" data-act="save-sync" ${!canSync || e.saving ? "disabled" : ""}>${e.changing ? "Move, save &amp; sync" : "Save &amp; sync"}</button>
      ${blocking ? `<div class="why">Fix ${plural(blocking, "field")} above to save.</div>` : v && !canSync ? `<div class="why" style="color:var(--warn)">Nothing to sync yet.</div>` : ""}
      ${e.blocked ? blockedConfirm(e.blocked) : ""}
      ${e.serverErrors ? `<div class="why">${e.serverErrors.map(esc).join("<br>")}</div>` : ""}`;
  }
  renderPreview();
}

function blockedConfirm(b) {
  const files = (b.affected || []).slice(0, 6).map((f) => `<code>${esc(f)}</code>`).join(", ");
  return `<div class="confirm inline-confirm" role="alert" style="flex-basis:100%">${esc(b.message)} Files: ${files}${(b.affected || []).length > 6 ? ", …" : ""}
    <button type="button" class="btn sm danger" data-act="save-approve">Approve and delete</button>
    <button type="button" class="btn sm" data-act="dismiss-blocked">Cancel</button></div>`;
}

const OP_ORDER = { collision: 0, conflict: 1, delete: 2, update: 3, create: 4, skip: 5 };

function planRows(groups, filter) {
  return groups.map((g) => {
      const rows = g.items.slice().sort((a, b) => OP_ORDER[a.op] - OP_ORDER[b.op]).filter((x) => filter === "all" || x.op === filter);
      const gc = Object.entries(g.counts).filter(([, n]) => n).map(([k, n]) => `${n} ${k}`).join(" · ");
      let head;
      if (g.removed) head = `<tr class="grp"><td colspan="3"><b>${esc(g.label)}</b> · removed from this pipeline: ${g.delete ? "its files will be deleted (guarded)" : "its files are kept and become unowned"}<span class="gc">${esc(gc)}</span></td></tr>`;
      else if (g.status === "off") head = `<tr class="grp off"><td colspan="3"><b>${esc(g.label)}</b> · Off: not read; its files are kept and never deleted<span class="gc">${esc(gc)}</span></td></tr>`;
      else if (g.status !== "ok" && g.category === "source-missing") head = `<tr class="grp warn"><td colspan="3"><b>${esc(g.label || "folder")}</b> <span class="mono">${esc(g.path)}</span> · ▲ Folder not found. Nothing was deleted; its files are kept.<span class="gc">${esc(gc)}</span></td></tr>`;
      else if (g.status !== "ok") head = `<tr class="grp bad"><td colspan="3"><b>${esc(g.label || "folder")}</b> <span class="mono">${esc(g.path)}</span> · ✕ ${esc(g.error)} Nothing it owns will be deleted.<span class="gc">${esc(gc)}</span></td></tr>`;
      else head = `<tr class="grp"><td colspan="3"><b>${esc(g.label)}</b> <span class="mono">${esc(g.path)}/</span><span class="gc">${g.md_files} .md · ${esc(gc)}</span></td></tr>`;
      return head + rows.map((x) => `<tr><td><span class="op ${x.op}">${x.op}</span></td><td class="p">${esc(x.source || "—")}</td><td class="p">${esc(x.target || "—")}${x.note ? `<span class="note">${esc(x.note)}</span>` : ""}</td></tr>`).join("");
  }).join("");
}

function renderPreview() {
  const e = S.ed, pv = e.pv, box = $("pv");
  if (!box) return;
  const preview = pv && pv.preview, v = pv && pv.validation;
  const groups = preview ? preview.groups : [];
  const hasPaths = e.sources.some((s) => s.path.trim());
  const templateErr = v && v.errors.find((x) => x.field === "target" && !/both resolve to/.test(x.message));
  const collisionErr = v && v.errors.find((x) => /both resolve to/.test(x.message));
  const counts = preview ? preview.counts : null;
  const btn = (k, label) => `<button data-act="filter" data-k="${k}" aria-pressed="${S.filter === k}"><b>${k === "all" ? counts.all : counts[k]}</b> ${label}</button>`;
  let inner;
  if (!pv) inner = `<div class="pempty">${e.loading && e.destination && e.destination.type === "github" ? "Reading the repository…" : e.loading && e.sources.some((s) => s.type === "jira") ? "Reading Jira…" : "Loading the preview…"}</div>`;
  else if (pv.change_error) inner = `<div class="banner err" role="alert">✕ ${esc(pv.change_error)}</div>`;
  else if (pv.target_pending) inner = `<div class="pempty">Test the connection of the GitHub target (or enter its token and test) to see what this pipeline would do.</div>`;
  else if (pv.target_error) inner = `<div class="banner err" role="alert">✕ ${esc(pv.target_error.message)}</div><div class="pempty">The preview needs to read the repository. Fix the problem above (see Activity), then edit anything to try again.</div>`;
  else if (templateErr) inner = `<div class="banner err">✕ ${esc(templateErr.message)}</div><div class="pempty">Fix the target path to see the preview.</div>`;
  else if (!groups.length || groups.every((g) => !g.removed && g.status !== "ok" && g.status !== "off")) {
    inner = `<div class="pempty">${hasPaths ? "No source folder can be read, so there is nothing to preview." : "Enter a source folder to see which files would be copied."}</div>`;
  } else {
    const guard = preview.guard;
    const body = planRows(groups, S.filter);
    const del = counts.delete, upd = counts.update;
    const ch = pv.change;
    inner = `
      ${ch ? `<div class="banner ${ch.differing.length || ch.missing.length ? "warn" : "ok"}" id="change-note">Moving from ${esc(ch.from)} to ${esc(ch.to)}: ${plural(ch.owned_files, "owned file")}, ${ch.owned_files - ch.missing.length - ch.differing.length} already there with the same bytes, ${ch.missing.length} missing (the next sync creates them), ${ch.differing.length} differ (conflicts, never overwritten). ${esc(ch.stays_behind_text)}</div>` : ""}
      ${preview.rename_note ? `<div class="banner warn">▲ ${plural(counts.rename, "file")} will be renamed (old path → new path is in the Target column). ${esc(preview.rename_note)}</div>` : ""}
      ${collisionErr ? `<div class="banner err">✕ ${esc(collisionErr.message)} Add {source} to the target path. Save is blocked.</div>` : ""}
      ${guard && guard.blocked ? `<div class="banner warn">▲ This would ${del ? `delete ${del}` : ""}${del && upd ? " and " : ""}${upd ? `overwrite ${upd}` : ""} of ${guard.total} files (${guard.percent}%). The sync will stop at the guard and ask for approval in Activity.</div>` : ""}
      ${counts.conflict ? `<div class="banner warn">▲ ${plural(counts.conflict, "conflict")} will be skipped and reported, never overwritten.</div>` : ""}
      ${v.warnings.find((w) => w.code === "no-files") ? `<div class="banner warn">▲ 0 files match these rules. Nothing would be synced.</div>` : ""}
      <div class="tablewrap"><table><thead><tr><th style="width:92px">Status</th><th>Source (relative to its folder)</th><th>Target</th></tr></thead><tbody>${body}</tbody></table></div>`;
  }
  const pills = counts && !templateErr && groups.length
    ? `<div class="counts" role="group" aria-label="Filter by status">${btn("all", "all")}${btn("create", "create")}${btn("update", "update")}${counts.rename ? btn("rename", "rename") : ""}${btn("delete", "delete")}${btn("skip", "skip")}${btn("conflict", "conflict")}${counts.collision ? btn("collision", "collision") : ""}</div>` : "";
  box.innerHTML = `<div class="phead"><div class="t"><h2 id="pv-h" style="margin:0">Dry-run preview</h2><span class="muted small">live · nothing is written · grouped by source${e.loading && pv ? " · updating…" : ""}</span></div>${pills}</div>${inner}`;
}

async function saveEditor(andSync, approve) {
  const e = S.ed;
  if (!e || e.saving) return;
  e.saving = true; e.serverErrors = null; renderEditorLive();
  const draft = editorDraft();
  let r;
  if (e.editing) {
    const relabels = {};
    e.sources.forEach((s) => { if (s.orig && s.orig !== s.label) relabels[s.orig] = s.label; });
    const put = {
      draft, relabels, sync: andSync, approve: !!approve,
      remove_folders: e.removed.map((x) => ({ label: x.label, delete_files: !!x.del })),
    };
    if (e.changing) {
      put.change_target = true;
      if (e.dest.kind === "github") { if (e.dest.pending) put.pending = e.dest.pending; else if (e.dest.auth === "token" && e.dest.token) put.token = e.dest.token; }
    }
    r = await api("PUT", `/api/pipelines/${encodeURIComponent(e.editing)}`, put);
  } else {
    const body = { draft, sync: andSync };
    if (e.dest.kind === "github") { if (e.dest.pending) body.pending = e.dest.pending; else if (e.dest.auth === "token" && e.dest.token) body.token = e.dest.token; }
    r = await api("POST", "/api/pipelines", body);
    if (r.ok) e.dest.token = "";
  }
  e.saving = false;
  if (r.ok) {
    const d = r.data;
    toast(d.sync ? `${e.name} saved. ${toastForResult(e.name, d.sync)}` : d.sync_skipped ? `${e.name} saved. ${d.sync_skipped}` : `${e.name} saved`);
    const back = e.returnTo;
    S.ed = null; S.screen = "home";
    if (back) return openView(back);
    await refresh();
    return;
  }
  if (r.status === 409 && r.data && r.data.needs_passphrase) { e.dest.needPass = true; renderDest(); e.serverErrors = [r.data.error]; }
  else if (r.status === 409 && r.data && r.data.result === "blocked") e.blocked = { message: r.data.error, affected: r.data.affected };
  else if (r.status === 422 && r.data) { e.serverErrors = (r.data.errors || []).map((x) => x.message); trackError("editor", "bad-input"); }
  else e.serverErrors = [(r.data && r.data.error) || "Could not save"];
  renderEditorLive();
}

/* ---------------------------------------------------------------- activity */
function conflictExplain(c, pipeline) {
  if (c.type === "a" && c.next_step) {
    return `${esc(c.reason)} <a href="#" data-act="sync-from-activity" data-name="${esc(pipeline)}">Sync now</a> once you have moved or deleted your copy.`;
  }
  return c.type === "a"
    ? `A file you wrote by hand is already at this path. The pipeline will never touch it. To sync the source file, move or rename yours, or change the target path, then <a href="#" data-act="sync-from-activity" data-name="${esc(pipeline)}">Sync now</a>.`
    : `${c.source_path ? `Source: <code>${esc(c.source_path)}</code>. ` : ""}${sentence(c.reason || "Edited in your output folder since the last sync")} Force replaces your edits in the output folder with the source version.`;
}

function needsCards(items) {
  const brainPath = S.state.brain.path;
  return items.map((n, idx) => {
    if (n.kind === "source_missing") return `<article class="card ecard">${missingSourceHtml(n.pipeline, n.label, n.path)}</article>`;
    if (n.kind === "error") {
      const connectors = (n.connectors || []).map((c) => `<p>Source <b>${esc(c.label)}</b> (Jira <code>${esc(c.site)}</code>): ${esc(c.error)} <span class="safe">Nothing it owns was deleted.</span></p><p class="muted small">${esc(c.next_step)}</p>`).join("");
      const folders = n.folders.length
        ? `<p>${n.folders.map((f) => `Source <b>${esc(f.label)}</b> (<code>${esc(f.path)}</code>) was not found or is unreadable.`).join(" ")}
            <span class="safe">Nothing ${n.total_folders > 1 ? "it owns " : ""}was deleted${n.kept_files ? `: ${plural(n.kept_files, "file")} kept in your output folder; this pipeline no longer updates them` : ""}.</span>
            ${n.other_labels.length && n.folders.length < n.total_folders ? ` The other folder${n.other_labels.length > 1 ? "s" : ""} (${esc(n.other_labels.join(", "))}) synced normally: ${esc(n.other_synced)}.` : ""}</p>
           <p class="muted small">Restore or remount the folder, or fix its path in Edit, then Sync now. You can also save other changes meanwhile. Removing the folder from the pipeline keeps its files in your output folder; this pipeline no longer updates them, unless you choose to delete them.</p>`
        : n.file_errors.length ? `<p>${n.file_errors.map((f) => `<code>${esc(f.path)}</code>: ${esc(f.error)}`).join("<br>")}</p><p>${plural(n.file_errors.length, "file")} skipped${n.files_written ? `; the other ${plural(n.files_written, "file")} ${n.files_written === 1 ? "was" : "were"} written` : ""}. <span class="safe">Nothing was deleted.</span></p><p class="muted small">${esc(n.file_errors_next_step)}</p>` : "";
      return `<article class="card ecard"><h3>✕ ${esc(n.pipeline)}: ${esc(n.title)} <span class="meta">triggered by ${esc(n.trigger.toUpperCase())} · ${esc(ago(n.ts))}</span></h3>
        ${connectors}${folders}
        <div class="row-acts"><button class="btn sm" data-act="edit" data-name="${esc(n.pipeline)}">Edit pipeline</button><button class="btn sm" data-act="sync" data-name="${esc(n.pipeline)}">Sync now</button></div></article>`;
    }
    if (n.kind === "target") {
      return `<article class="card ecard"><h3>✕ ${esc(n.pipeline)}: ${esc(n.title)} <span class="meta">triggered by ${esc(n.trigger.toUpperCase())} · ${esc(ago(n.ts))}</span></h3>
        <p>${esc(n.message)} <span class="safe">The repository was not changed.</span></p><p class="muted small">${esc(n.next_step)}</p>
        ${n.category === "vault-locked" ? `<div class="tform"><label class="tf"><span>Vault passphrase (from an earlier version; asked this once)</span><input type="password" id="vp" autocomplete="off"></label><div class="row-acts"><button class="btn sm" data-act="vault-unlock">Unlock the vault</button></div></div>` : ""}
        ${n.category === "sign-in-expired" ? `<div class="row-acts">${reauthHtml(n.pipeline)}</div>` : ""}
        <div class="row-acts"><button class="btn sm" data-act="edit" data-name="${esc(n.pipeline)}">Open the pipeline's target</button><button class="btn sm" data-act="sync" data-name="${esc(n.pipeline)}">Sync now</button></div></article>`;
    }
    if (n.kind === "import") {
      return `<article class="card ecard" data-card="import"><h3>▲ ${esc(n.title)}</h3><p>${esc(n.message)}</p>
        <ul>${n.items.map((i) => `<li><code>${esc(i.ref)}</code> (${esc(i.secret_kind || "token")}) used by ${esc(i.pipelines.join(", "))}</li>`).join("")}</ul>
        <div class="row-acts"><button class="btn sm primary" data-act="vault-import">Import into the vault</button></div></article>`;
    }
    if (n.kind === "clone") {
      return `<article class="card ecard"><h3>✕ ${esc(n.title)} <span class="meta">${esc(ago(n.ts))}</span></h3>
        <p>${esc(n.reason)} <span class="safe">Nothing in the clone was touched.</span></p><p class="muted small">${esc(n.next_step)}</p>
        <div class="row-acts"><button class="btn sm" data-act="copy-path" data-path="${esc(n.path)}">Copy clone path</button></div></article>`;
    }
    if (n.kind === "state") {
      const pv = S.restorePv;
      const line = (c) => Object.entries(c).filter(([, v]) => v).map(([k, v]) => `${v} ${k}`).join(", ") || "no changes";
      const pvHtml = pv ? `<p>Backup ${pv.generation} records <b>${pv.owned_files}</b> owned files. The next sync would do: ${Object.entries(pv.pipelines).map(([k, v]) => `<b>${esc(k)}</b>: ${esc(line(v.counts))}`).join("; ")}.</p>
        <div class="row-acts"><button class="btn warn" data-act="restore-apply">Restore this backup</button></div>` : "";
      return `<article class="card ecard"><h3>✕ ${esc(n.title)}</h3><p>${esc(n.message)}</p>
        <p class="muted small">${n.backups} usable backup${n.backups === 1 ? "" : "s"} in .mdpipeline/backups/. Syncing stays stopped until the state is restored; nothing was deleted.</p>
        <div class="row-acts"><button class="btn sm" data-act="restore-preview" ${n.backups ? "" : "disabled"}>Show what a restore would do</button></div>${pvHtml}</article>`;
    }
    if (n.kind === "guard") {
      return `<article class="card gcard" aria-labelledby="g${idx}">
        <h3 id="g${idx}">■ ${esc(n.pipeline)}: sync blocked by the guard <span class="meta">${n.trigger ? `triggered by ${esc(n.trigger.toUpperCase())} · ${esc(ago(n.ts))}` : ""}</span></h3>
        <p class="big">This sync would <strong>delete or overwrite ${n.count} of ${n.total} files (${n.percent}%)</strong>${n.will_create.length ? ` and create ${n.will_create.length}` : ""}. Guard threshold: ${n.threshold}%.</p>
        <div class="lists">
          <div class="lst"><h4 style="color:var(--danger)">Will delete (${n.will_delete.length})${n.will_update.length ? ` · overwrite (${n.will_update.length})` : ""}</h4>${n.will_delete.map((f) => `<div>${esc(f)}</div>`).join("")}${n.will_update.map((f) => `<div>~ ${esc(f)}</div>`).join("")}</div>
          <div class="lst"><h4 style="color:var(--ok)">Will create (${n.will_create.length})</h4>${n.will_create.map((f) => `<div>${esc(f)}</div>`).join("") || '<div class="muted">none</div>'}</div>
          ${n.will_rename && n.will_rename.length ? `<div class="lst"><h4 style="color:var(--accent)">Will rename (${n.will_rename.length})</h4>${n.will_rename.map((r) => `<div>${esc(r.from)} → ${esc(r.to)}</div>`).join("")}<div class="muted">Links inside raw files are not rewritten.</div></div>` : ""}
        </div>
        <div class="row-acts"><button class="btn warn" data-act="approve" data-name="${esc(n.pipeline)}">Approve &amp; sync</button><button class="btn" data-act="cancel" data-name="${esc(n.pipeline)}">Cancel</button>
          <span class="muted small">Approval covers exactly these files; if the sources change first, the sync is re-planned and asks again. Or in a terminal: <code>${esc(n.cli_hint)}</code>. An agent (MCP) cannot approve.</span></div></article>`;
    }
    return `<article class="card ccard"><h3>▲ ${esc(n.pipeline)}: ${plural(n.items.length, "conflict")} skipped <span class="meta">nothing was overwritten</span></h3>
      ${n.items.map((c) => `<div class="citem">
        <div><div class="kind">${c.type === "a" ? "Target exists and is not owned by this pipeline" : "Edited in your output folder since the last sync"}</div><div class="path">${esc(c.path)}</div></div>
        <div class="row-acts">${c.open_path !== null ? `<button class="btn sm" data-act="open-file" data-path="${esc(c.open_path || c.path)}">Open</button>` : ""}${c.abs_path !== null ? `<button class="btn ghost sm" data-act="copy-path" data-path="${esc(c.abs_path || brainPath + "/" + c.path)}">Copy path</button>` : ""}
          ${c.type === "b" ? `<button class="btn sm danger" data-act="force-ask" data-path="${esc(c.path)}">Force…</button>` : ""}</div>
        <div class="expl">${conflictExplain(c, n.pipeline)}</div>
        ${S.confirm === c.path ? `<div class="confirm" role="alert">Overwrite your edits in <code>${esc(c.path.split("/").pop())}</code> with the source version? This cannot be undone by the app.
          <button class="btn sm danger" data-act="force" data-name="${esc(n.pipeline)}" data-path="${esc(c.path)}">Overwrite</button><button class="btn sm" data-act="force-cancel">Keep my edits</button></div>` : ""}
      </div>`).join("")}</article>`;
  }).join("");
}

function runRows(history, prefix) {
  const resultPill = (r) => {
    if (r.kind === "event") {
      const m = { pause: ["paused", "paused"], resume: ["ok", "resumed"], approve: ["ok", "approved"], cancel: ["blocked", "cancelled"], force: ["conflicts", "forced"], remove: ["never", "removed"], remove_source: ["never", "folder removed"], move: ["ok", "moved to GitHub"] }[r.action] || ["never", r.action];
      return `<span class="pill ${m[0]}"><span class="dot"></span>${esc(m[1])}</span>`;
    }
    const m = { ok: "ok", conflicts: "conflicts", blocked: "blocked", cancelled: "blocked", error: r.needs_you ? "needs" : "error", "skipped: paused": "paused", busy: "paused" }[r.result] || "ok";
    const label = r.needs_you ? "needs you" : r.result === "skipped: paused" ? "skipped" : r.result;
    return `<span class="pill ${m}"><span class="dot"></span>${esc(label)}</span>`;
  };
  const cnt = (r) => {
    if (r.kind === "event") return '<span class="muted">—</span>';
    const c = r.counts, sym = [["created", "+"], ["updated", "~"], ["deleted", "−"], ["skipped", "="], ["conflicts", "!"]];
    return sym.filter(([k]) => c[k]).map(([k, s]) => `<span title="${k}">${s}${c[k]}</span>`).join("") || '<span class="muted">—</span>';
  };
  const rows = history.map((r) => {
    const key = prefix + String(r.id), open = S.open.has(key);
    let detail;
    if (r.kind === "event") detail = `<div style="font-family:var(--sans)">${esc(r.text)}</div>`;
    else {
      const note = r.message ? `<div style="font-family:var(--sans);margin-bottom:4px">${esc(r.message)}</div>` : "";
      const errs = (r.errors || []).map((x) => `<div style="font-family:var(--sans);color:var(--danger)">${esc(x.source)}${x.path ? ": " + esc(x.path) : ""}: ${esc(x.error)}</div>`).join("");
      const commit = r.commit ? `<div style="font-family:var(--sans);margin-bottom:4px">Commit ${commitLink(r.commit)}</div>` : "";
      const cl = r.clone ? `<div style="font-family:var(--sans);margin-bottom:4px">Local clone: ${esc(r.clone.status)}${r.clone.reason ? " · " + esc(r.clone.reason) : ""}</div>` : "";
      const files = r.details.length ? r.details.map((f) => `<div><span class="op ${f.op}">${esc(f.op)}</span> ${esc(f.target)}${f.note ? ` <span class="muted">(${esc(f.note)})</span>` : ""}</div>`).join("") : "";
      detail = note + commit + cl + errs + (files || (note || errs || commit ? "" : '<div class="muted">No file changes (all unchanged).</div>'));
    }
    return `<tr class="r ${open ? "open" : ""}" data-act="toggle-run" data-key="${key}" aria-expanded="${open}">
        <td><span class="caret">▸</span> ${esc(ago(r.ts))}</td><td class="mono">${esc(r.pipeline || "—")}</td><td>${r.trigger ? `<span class="trig">${esc(r.trigger.toUpperCase())}</span>` : ""}</td>
        <td class="cnt">${cnt(r)}</td><td>${resultPill(r)}</td><td class="muted mono">${r.kind === "run" ? esc(dur(r.duration_ms)) : ""}</td></tr>
        ${open ? `<tr class="files"><td colspan="6">${detail}</td></tr>` : ""}`;
  }).join("");
  return rows;
}

function renderActivity() {
  const a = S.activity;
  if (!a) { $("main").innerHTML = `<div class="pempty">Loading…</div>`; return; }
  const f = S.actPipeline;
  const cards = needsCards(a.needs_you);          // already limited to the pipeline by the server when filtered
  const rows = runRows(a.history, "");
  const history = a.history.length
    ? `<div class="card" style="overflow:auto"><table class="runs"><thead><tr><th style="width:120px">Time</th><th>Pipeline</th><th>Trigger</th><th>Changes</th><th>Result</th><th>Duration</th></tr></thead><tbody>${rows}</tbody></table></div>
       <p class="muted small">Legend: +created ~updated −deleted =skipped !conflicts. Pause and resume are logged too. Same log as <code>${CLI} log</code>. Stored locally only.</p>`
    : `<div class="card empty"><h1>No syncs yet</h1><p class="muted">Every sync from the UI, CLI or your agent (MCP) will be listed here.</p></div>`;
  const filterBar = f ? `<div class="bar"><span class="muted">Filtered to pipeline <b>${esc(f)}</b></span> <button class="btn sm" data-act="act-clear">Show all</button> <button class="btn ghost sm" data-act="view" data-name="${esc(f)}">Back to ${esc(f)}</button></div>` : "";
  $("main").innerHTML = `${filterBar}${cards ? `<h2>Needs you</h2><div class="attn">${cards}</div>` : ""}<h2>Sync history</h2>${history}`;
}

async function afterAction(r, name, verb) {
  toast(r.ok ? (r.data.result ? toastForResult(name, r.data) : `${name} ${verb}`) : (r.data && r.data.error) || "failed");
  await refresh();
}

/* ---------------------------------------------------------------- usage data (MD-115) */
// MD-116: usage events the page reports. Fire-and-forget; nothing is even requested unless the user chose to share.
function track(event, properties) {
  if (!S.state || !S.state.usage || !S.state.usage.sharing) return;
  api("POST", "/api/usage/event", { event, properties: properties || {} });      // api() never throws and nothing waits for it
}
// MD-168: the linger line was drawn on a screen. The server reports it at most once per screen per day; this only avoids asking on every redraw. Returns "" so it can sit in a template.
const lingerSeen = new Set();
function noteLinger(surface) {
  if (!lingerSeen.has(surface)) { lingerSeen.add(surface); track("notice_shown", { kind: "linger", surface }); }
  return "";
}
const bucketOf = (n) => (n <= 0 ? "0" : n <= 10 ? "1-10" : n <= 100 ? "11-100" : n <= 1000 ? "101-1000" : "1000+");
const ERROR_CLASS = { auth: "auth", "vault-locked": "auth", "vault-unsafe": "auth", "rate-limited": "rate_limit", network: "network", "protected-branch": "target_write",
  "head-moved": "target_write", "concurrent-change": "target_write", "credential-conflict": "validation" };
const errorClassOf = (category) => (/^bad-/.test(category || "") ? "validation" : ERROR_CLASS[category] || "unknown");
const cameFrom = (screen) => ({ home: "home", view: "view", editor: "editor", activity: "activity", settings: "settings", firstrun: "onboarding" }[screen] || "other");
const trackError = (area, category) => track("error_shown", { ui_area: area, error_class: errorClassOf(category) });

const EXAMPLE_EVENT = '{"event": "first_sync_completed", "properties": {"source_type": "jira", "ms_since_first_open": 4100}}';

// Properties exactly as the app sends them (Python-style separators), not re-spaced by the browser.
const jsonLine = (v) => JSON.stringify(v).replace(/("(?:[^"\\]|\\.)*")|([,:])/g, (m, str, sep) => (str ? str : sep + " "));
const bullets = (items) => `<ul>${(items || []).map((t) => `<li>${esc(t)}</li>`).join("")}</ul>`;
const usageLists = (u) => `<div class="ulists"><div><h3>What we send</h3>${bullets(u.what_we_send)}</div><div><h3>Never sent</h3>${bullets(u.never_sent)}</div></div>`;
const usageBtn = (choice, label, cls, pressed) =>
  `<button${cls ? ` class="${cls}"` : ""} data-act="usage-choose" data-choice="${choice}"${pressed === null ? "" : ` aria-pressed="${pressed}"`}>${label}</button>`;
// The two answers have the same size and style and neither is pre-selected: neither is "the good one".
const usageButtons = () => `<div class="ubtns">${usageBtn("share", "Share usage", "btn", null)}${usageBtn("decline", "Don't share", "btn", null)}</div>`;

function renderConsentCard() {
  const us = S.state.usage || {}, just = S.usageUi.just;
  if (just) {
    const text = just === "share" ? "Sharing anonymous usage" : "Not sharing anonymous usage";
    return `<div class="card usage-card collapsed" id="usage-card" role="status">✓ ${text} · Change any time in Settings → Privacy &amp; usage ·
      <button class="link" data-act="usage-choose" data-choice="unanswered">Undo</button></div>`;
  }
  if (us.consent !== "unanswered") return "";
  const u = S.usage;
  return `<section class="card usage-card" id="usage-card" aria-labelledby="usage-h">
    <h2 id="usage-h">Help improve Markdown Pipeline</h2>
    <p>Share anonymous usage: which steps you complete, errors, counts and timings. Never your content, titles, file names, paths, queries or tokens.</p>
    ${usageButtons()}
    <p class="muted small">You can skip this. If you don't choose, nothing is shared.</p>
    <details id="usage-details" ${S.usageUi.drawer ? "open" : ""}><summary>See exactly what we send</summary>
      <div id="usage-drawer">${u ? usageLists(u) : ""}
        <p class="small">${esc(u ? u.always_sent : "")}</p>
        <p class="small"><b>Example</b> (illustrative):</p><pre class="snip">${esc(EXAMPLE_EVENT)}</pre>
        <p class="small muted">${esc(u ? u.sent_on_purpose : "")}</p>
        ${usageButtons()}</div></details></section>`;
}

function renderSettings() {
  const u = S.usage;
  if (!u) { $("main").innerHTML = `<div class="bar"><h1 class="grow">Settings</h1></div><p class="muted">Loading…</p>`; return; }
  const status = u.consent === "unanswered" ? "You haven't chosen yet, so nothing is shared."
    : u.sharing ? "Sharing anonymous usage." : "Nothing is sent while sharing is off.";
  const rows = u.log.map((e) => `<tr><td class="p">${esc(new Date(e.at).toLocaleTimeString())}</td><td class="p">${esc(e.event)}${e.chosen ? ` <span class="dtag" title="You pressed Send">goes to the team</span>` : ""}</td>
    <td class="p">${esc(jsonLine(e.properties))}</td></tr>`).join("");
  const empty = u.sharing ? "Nothing sent yet." : "Nothing is sent while sharing is off. This log stays empty.";
  $("main").innerHTML = `<div class="bar"><h1 class="grow">Settings</h1></div>
    ${renderBackgroundSettings()}
    ${renderAgentsSettings()}
    <section class="card usage" id="usage-settings" aria-labelledby="usage-set-h">
      <h2 id="usage-set-h">Privacy &amp; usage</h2>
      <p>Anonymous usage helps us see where setup gets hard. You can change this any time; it takes effect immediately.</p>
      <div class="seg" role="group" aria-label="Share anonymous usage">${usageBtn("share", "Share usage", "", u.sharing)}${usageBtn("decline", "Don't share", "", !u.sharing)}</div>
      <p class="usage-status" role="status">${esc(status)}</p>
      ${usageLists(u)}
      <p class="small">${esc(u.always_sent)}</p>
      <p class="small muted">${esc(u.sent_on_purpose)}</p>
      <h3>Last events sent from this computer <span class="muted small">last ${u.log_limit}, newest first</span></h3>
      <div class="tablewrap"><table id="usage-log"><thead><tr><th>Time</th><th>Event</th><th>Properties (exactly as sent)</th></tr></thead>
        <tbody>${rows || `<tr><td colspan="3" class="muted">${esc(empty)}</td></tr>`}</tbody></table></div>
      <p class="small">Install ID <code>${esc(u.install_id)}</code> <button class="btn sm" data-act="usage-reset">Reset</button>
        <span class="muted"> · In a terminal: <code>${esc(u.command || CLI)} usage off</code></span></p>
    </section>`;
}

function renderBackgroundSettings() {
  const bg = (S.state && S.state.background) || { enabled: true, running: false };
  const btn = (on, label) => `<button data-act="bg-set" data-on="${on}" aria-pressed="${bg.enabled === on}">${label}</button>`;
  return `<section class="card usage" id="bg-settings" aria-labelledby="bg-set-h">
    <h2 id="bg-set-h">Background sync</h2>
    <p>Keep syncing when this window is closed (starts when you log in to your desktop). How often each pipeline syncs is set on Home.</p>
    <div class="seg" role="group" aria-label="Background sync">${btn(true, "On")}${btn(false, "Off")}</div>
    <p class="usage-status" role="status">${bg.enabled ? (bg.running ? "On, and running now." : "On, but not running right now. Opening the app or running any mdpipeline command starts it again.") : "Off. Nothing syncs by itself. Use Sync now, or turn this on."}</p>
    ${bg.enabled && bg.linger_hint && bg.linger_text ? `${noteLinger("settings")}<p class="small" id="bg-linger">${lingerHtml(bg.linger_text)}</p>` : ""}
    <p class="small muted">In a terminal: <code>${CLI} schedule background off</code></p></section>`;
}

async function usageChoose(choice) {
  const r = await api("POST", "/api/usage/consent", { choice });
  if (!r.ok) return toast((r.data && r.data.error) || "Could not save your choice");
  S.usage = r.data;
  S.state.usage = { consent: r.data.consent, sharing: r.data.sharing };
  S.usageUi.just = (S.screen === "home" || S.screen === "firstrun") && ["share", "decline"].includes(choice) ? choice : null;
  S.usageUi.drawer = false;
  return render();
}

// Carrying on without answering is a (third) outcome, counted once; the card never blocks.
function noteNoAnswer() {
  if (S.usageUi.noAnswerSent || !S.state || !S.state.usage || S.state.usage.consent !== "unanswered") return;
  S.usageUi.noAnswerSent = true;
  api("POST", "/api/usage/consent", { choice: "no_answer" });
}

/* ---------------------------------------------------------------- events */
document.addEventListener("toggle", (ev) => { if (ev.target.id === "usage-details") S.usageUi.drawer = ev.target.open; }, true);

document.addEventListener("change", (ev) => { const el = ev.target; if (el && el.dataset && el.dataset.schedule) scheduleChosen(el.dataset.schedule, el.value); });

document.addEventListener("click", async (ev) => {
  const el = ev.target.closest("[data-act]");
  if (!el) return;
  const act = el.dataset.act, name = el.dataset.name;
  if (el.tagName === "A") ev.preventDefault();
  if (["new", "edit", "view", "sync", "sync-all"].includes(act)) noteNoAnswer();
  switch (act) {
    case "usage-choose": return usageChoose(el.dataset.choice);
    case "bg-set": return setBackground(el.dataset.on === "true");
    case "bg-off": await setBackground(false); return focusSoon("main h1");
    case "linger-hide": { const r = await api("POST", "/api/background/linger-hide", {}); if (r.ok) S.state.background = r.data; render(); return undefined; }
    case "bg-ok": { const r = await api("POST", "/api/background/ack", {}); if (r.ok) S.state.background = r.data; render(); return focusSoon("main h1"); }
    case "bg-change": { const r = await api("POST", "/api/background/ack", {}); if (r.ok) S.state.background = r.data; render(); return focusSoon("select[data-schedule]"); }
    case "usage-reset": { const r = await api("POST", "/api/usage/reset-id", {}); if (r.ok) { S.usage = r.data; render(); toast("New install ID"); } return undefined; }
    case "go": return go(el.dataset.screen);
    case "view-activity": return go("activity", name);
    case "act-clear": return go("activity");
    case "mcptab": S.mcpTab = el.dataset.tab; return render();
    case "copy-snippet": { track("agent_snippet_copied", { agent: S.mcpTab === "claude" ? "claude_code" : "codex" }); const c = S.state.connect; return copy(S.mcpTab === "claude" ? c.claude_code_command : `# ~/.codex/config.toml\n${c.codex_toml.trim()}`, "MCP config"); }
    case "new": return openEditor(null);
    case "own-source": return enterOwnSource();
    case "sample-remove": return removeSample();
    case "sample-later": { const r = await api("POST", "/api/sample/later", {}); toast(r.ok ? `Sample synced: ${resultSummary(r.data)}` : (r.data && r.data.error) || "could not run the sample sync"); return refresh(); }
    case "edit": return openEditor(name);
    case "view": return openView(name);
    case "plan-refresh": return loadPlan(true);
    case "vfilter": S.view.filter = el.dataset.k; return renderViewPlan();
    case "reauth-start": return reauthStart(name);
    case "reauth-cancel": clearTimeout(reauthTimers[name]); delete S.reauth[name]; return render();
    case "reauth-recheck": return reauthRecheck(name);
    case "reauth-sync": delete S.reauth[name]; return doSync(name);
    case "msrc-choose": S.msrc = { key: name + "/" + el.dataset.label, path: el.dataset.path || "", error: "", busy: false }; render(); return focusSoon("input[data-msrc-path]");
    case "msrc-cancel": S.msrc = null; return render();
    case "msrc-save": return saveMissingSource(name, el.dataset.label);
    case "msrc-remove": return removeMissingSource(name, el.dataset.label);
    case "view-edit": return openEditor(name, name);
    case "editor-cancel": return S.ed && S.ed.returnTo ? openView(S.ed.returnTo) : go("home");
    case "sync": return doSync(name);
    case "sync-from-activity": await doSync(name); return go("home");
    case "sync-all": return doSyncAll();
    case "pause": return doPause(name, true);
    case "resume": return doPause(name, false);
    case "approve": { S.busy = true; const r = await api("POST", `/api/pipelines/${encodeURIComponent(name)}/approve`); S.busy = false; return afterAction(r, name, "approved"); }
    case "cancel": { const r = await api("POST", `/api/pipelines/${encodeURIComponent(name)}/cancel`); toast(r.ok ? `${name}: sync cancelled. It stays blocked until the next sync checks again` : (r.data && r.data.error) || "failed"); return refresh(); }
    case "restore-preview": { const r = await api("POST", "/api/state/restore", {}); if (r.ok) { S.restorePv = r.data; render(); } else toast((r.data && r.data.error) || "failed"); return; }
    case "restore-apply": { const r = await api("POST", "/api/state/restore", { apply: true }); S.restorePv = null; toast(r.ok ? "State restored from the backup" : (r.data && r.data.error) || "failed"); return refresh(); }
    case "force-ask": S.confirm = el.dataset.path; return render();
    case "force-cancel": S.confirm = null; return render();
    case "force": { S.confirm = null; S.busy = true; const r = await api("POST", `/api/pipelines/${encodeURIComponent(name)}/force`, { paths: [el.dataset.path] }); S.busy = false; return afterAction(r, name, "forced"); }
    case "open-file": {
      const r = await api("POST", "/api/open", { path: el.dataset.path });
      if (r.ok && r.data.ok) toast(`Opened ${r.data.absolute_path} in your default app`);
      else { const p = (r.data && r.data.absolute_path) || el.dataset.path; toast("Could not open the file here. The path was copied instead."); copy(p, "path"); }
      return undefined;
    }
    case "copy-path": return copy(el.dataset.path, "path");
    case "toggle-changes": { const k = "chg:" + el.dataset.key; if (S.open.has(k)) S.open.delete(k); else S.open.add(k); return render(); }
    case "toggle-run": { const k = el.dataset.key; if (S.open.has(k)) S.open.delete(k); else S.open.add(k); return S.screen === "view" ? render() : renderActivity(); }
    // editor
    case "add-jira": { track("source_add_started", { source_type: "jira", entry_point: S.state.pipelines.length ? "editor" : "empty_state" }); closeAddMenu(); S.ed.sources.push({ type: "jira", label: S.ed.sources.some((x) => x.label === "jira") ? "" : "jira", site: "", jql: "", email_ref: "", token_ref: "", path: "", lblTouched: true, orig: null }); suggestName(); if ($("f-name")) $("f-name").value = S.ed.name; renderSourceRows(); renderEditorLive(); return schedulePreview(0); }
    case "jira-test": {
      const s = S.ed.sources[Number(el.dataset.i)];
      s.testing = true; s.jtest = null; renderSourceRows();
      const r = await api("POST", "/api/jira-test", { label: s.label, site: s.site, jql: (s.jql || "").trim() || "ORDER BY updated DESC",       // blank: check the account sees issues, so the picker is usable before any JQL is typed
       email_ref: s.email_ref, token_ref: s.token_ref, email: s.email || "", token: s.token || "", pending: s.pending || "" });
      s.testing = false; s.jtest = r.ok ? r.data : { ok: false, errors: [{ message: (r.data && r.data.error) || "the check failed" }] };
      if (!s.jtest.ok) trackError("source", ((s.jtest.errors || [])[0] || {}).category);
      if (s.jtest.ok && s.jtest.pending) { s.pending = s.jtest.pending; s.email = ""; s.token = ""; }
      renderSourceRows(); return renderEditorLive();
    }
    case "jira-projects": return jiraLoadProjects(S.ed.sources[Number(el.dataset.i)], false);
    case "jira-projects-more": return jiraLoadProjects(S.ed.sources[Number(el.dataset.i)], true);
    case "jira-pick-replace": return jiraApplyPicks(S.ed.sources[Number(el.dataset.i)]);
    case "jira-pick-keep": { const s = S.ed.sources[Number(el.dataset.i)]; s.proj.ticked = {}; s.proj.replace = false; return renderSourceRows(); }
    case "add-source": { track("source_add_started", { source_type: "folder", entry_point: S.state.pipelines.length ? "editor" : "empty_state" }); S.ed.sources.push({ type: "folder", path: "", label: "", lblTouched: false, orig: null }); closeAddMenu(); renderSourceRows(); renderEditorLive(); $("f-src" + (S.ed.sources.length - 1)).focus(); return undefined; }
    case "remove-source": {
      const e = S.ed, i = Number(el.dataset.i);
      const [gone] = e.sources.splice(i, 1);
      if (gone.orig) e.removed.push({ label: gone.orig, path: gone.type === "jira" ? gone.site : gone.path, del: false, owned: e.owned[gone.orig] || 0 });
      suggestName(); renderSourceRows(); if ($("f-name")) $("f-name").value = e.name; renderEditorLive(); return schedulePreview(0);
    }
    case "chip": {
      const input = $("f-template"), v = el.dataset.var;
      const start = input.selectionStart ?? input.value.length, end = input.selectionEnd ?? start;
      input.value = input.value.slice(0, start) + v + input.value.slice(end);
      input.focus(); input.setSelectionRange(start + v.length, start + v.length);
      S.ed.target = input.value; return schedulePreview(0);
    }
    case "dest-test": {
      const x = S.ed.dest;
      x.busy = true; x.test = null; renderDest();
      const body = { destination: destPayload() };
      if (x.kind === "github" && x.auth === "token" && !x.existing && !x.pending && x.token) body.token = x.token;
      const r = await api("POST", "/api/target/test", body);
      x.busy = false;
      x.test = r.ok ? r.data : { ok: false, errors: [{ message: (r.data && r.data.error) || "the check failed" }] };
      if (!x.test.ok) trackError("target", ((x.test.errors || [])[0] || {}).category);
      if (x.test.needs_passphrase) x.needPass = true;
      if (x.test.ok && x.test.pending) { x.pending = x.test.pending; x.token = ""; if (x.kind === "github" && !x.branch) x.branch = x.test.connection.branch; }
      renderDest(); renderEditorLive(); return schedulePreview(0);
    }
    case "dest-replace-token": {
      const x = S.ed.dest;
      if (!x.newToken) { x.tokenNote = "✕ Enter the new token first."; return renderDest(); }
      x.busy = true; renderDest();
      const r = await api("POST", `/api/pipelines/${encodeURIComponent(S.ed.editing)}/token`, { token: x.newToken });
      x.busy = false; x.newToken = "";
      if (r.ok) { x.credential = x.credential || "set"; x.tokenNote = "Token replaced and checked."; }
      else { x.tokenNote = "✕ " + ((r.data && ((r.data.errors && r.data.errors[0] && r.data.errors[0].message) || r.data.error)) || "the token was not accepted") + " The old token is unchanged."; if (r.data && r.data.needs_passphrase) x.needPass = true; }
      renderDest(); return refresh();
    }
    case "dest-remove-token": {
      const r = await api("DELETE", `/api/pipelines/${encodeURIComponent(S.ed.editing)}/token`);
      const x = S.ed.dest;
      if (r.ok) { x.credential = ""; x.tokenNote = "Token removed. Syncs stop until you set a new one."; } else x.tokenNote = "✕ " + ((r.data && r.data.error) || "could not remove the token");
      renderDest(); return refresh();
    }
    case "dest-branches": {
      const x = S.ed.dest;
      const ready = x.auth === "token" ? !!(x.token || x.pending) : !!x.pending;
      if (!x.repo.trim()) { x.branches = { error: "Type the repository (OWNER/REPO) first." }; return renderBranchList(); }
      if (!ready) { x.branches = { error: x.auth === "sign-in" ? "Sign in with GitHub first; the list needs to read the repository." : "Enter a token first; the list needs to read the repository." }; return renderBranchList(); }
      x.branches = { loading: true }; renderBranchList();
      const body = { destination: destPayload() };
      if (x.auth === "token" && !x.pending && x.token) body.token = x.token;
      const r = await api("POST", "/api/github/branches", body);
      const d = r.ok ? r.data : null;
      x.branches = d && d.ok ? { list: d.branches, more: d.more } : { error: (d && d.errors && d.errors[0] && d.errors[0].message) || "We couldn't list the branches. You can still type one." };
      return renderBranchList();
    }
    case "dest-change": { const e = S.ed; e.changing = true; e.oldDest = e.dest; e.dest = newDest(); renderDest(); renderEditorLive(); return schedulePreview(0); }
    case "dest-change-cancel": return openEditor(S.ed.editing);
    case "vault-import": {
      const r = await api("POST", "/api/vault/import", {});
      if (!r.ok) return toast((r.data && r.data.error) || "could not import");
      toast(r.data.results.map((x) => x.message).join(" "));
      return refresh();
    }
    case "app-reset": { const r = await api("POST", "/api/github-app/reset", {}); if (r.ok) { S.state.github_app = r.data; S.appForm.open = false; toast("Using the shipped GitHub App again"); return renderDest(); } return toast("Could not reset"); }
    case "app-toggle": { const f = S.appForm, app = S.state.github_app; f.open = !f.open; f.errors = []; f.client_id = app.client_id || ""; f.slug = app.slug || ""; return renderDest(); }
    case "app-save": {
      const f = S.appForm;
      f.busy = true; f.errors = []; renderDest();
      const r = await api("POST", "/api/github-app", { client_id: f.client_id, slug: f.slug });
      f.busy = false;
      if (!r.ok) { f.errors = (r.data && r.data.errors) || [{ field: "client_id", message: (r.data && r.data.error) || "could not save" }]; return renderDest(); }
      S.state.github_app = r.data; f.open = false;
      toast("GitHub App saved. Sign in with GitHub is ready."); renderDest(); return refresh();
    }
    case "signin-start": return startSignin(false);
    case "signin-cancel": { const x = S.ed.dest; clearTimeout(signinTimer); x.flow = null; renderDest(); return undefined; }
    case "signin-recheck": {
      const x = S.ed.dest;
      const r = await api("POST", "/api/signin/repos", { pending: x.pending });
      if (r.ok) { x.repos = r.data.repos; x.signMsg = ""; } else x.signMsg = "✕ " + ((r.data && r.data.error) || "could not check");
      renderDest(); return schedulePreview(0);
    }
    case "signin-out": {
      const r = await api("DELETE", `/api/pipelines/${encodeURIComponent(S.ed.editing)}/sign-in`);
      const x = S.ed.dest;
      if (r.ok) { x.credential = ""; x.login = ""; x.signMsg = r.data.message; } else x.signMsg = "✕ " + ((r.data && r.data.error) || "could not sign out");
      renderDest(); return refresh();
    }
    case "vault-unlock": {
      const pass = ($("d-pass") || $("vp") || {}).value || "";
      const r = await api("POST", "/api/vault/unlock", { passphrase: pass });
      if (r.ok) { toast("Vault unlocked"); if (S.ed) { S.ed.dest.needPass = false; renderDest(); } return refresh(); }
      return toast((r.data && ((r.data.errors && r.data.errors[0] && r.data.errors[0].message) || r.data.error)) || "could not unlock");
    }
    case "filter": S.filter = el.dataset.k; return renderPreview();
    case "save": return saveEditor(false);
    case "save-sync": return saveEditor(true);
    case "save-approve": return saveEditor(false, true);
    case "dismiss-blocked": S.ed.blocked = null; return renderEditorLive();
    default: return undefined;
  }
});

document.addEventListener("input", (ev) => {
  const el = ev.target, bind = el.dataset && el.dataset.bind;
  if (!bind || !S.ed) return;
  const e = S.ed;
  if (bind === "name") { e.name = el.value; e.nameTouched = true; }
  else if (bind === "target") e.target = el.value;
  else if (bind === "include") e.include = el.value;
  else if (bind === "exclude") e.exclude = el.value;
  else if (bind === "guard") e.guard = el.value;
  else if (bind === "namePattern") e.namePattern = el.value;
  else if (bind.startsWith("app-")) { S.appForm[bind.slice(4)] = el.value; return undefined; }
  else if (bind.startsWith("dest-")) {
    const x = e.dest, k = bind.slice(5);
    if (k === "dir") x.dir = el.value; else if (k === "repo") { x.repo = el.value; x.pending = null; x.branches = null; renderBranchList(); } else if (k === "branch") { x.branch = el.value; x.pending = null; }
    else if (k === "clone") x.clone = el.value; else if (k === "token") { x.token = el.value; return; } else if (k === "newtoken") { x.newToken = el.value; return; }
    else if (k === "pass") return;
    x.test = null; const m = $("m-dest"); if (m) m.innerHTML = "";
  } else if (bind === "src-path") {
    const s = e.sources[Number(el.dataset.i)];
    s.path = el.value;
    if (!s.lblTouched) { s.label = base(el.value); const l = $("l-src" + el.dataset.i); if (l) l.value = s.label; }
    suggestName(); if ($("f-name") && !e.nameTouched) $("f-name").value = e.name;
  } else if (bind === "src-label") { const s = e.sources[Number(el.dataset.i)]; s.label = el.value; s.lblTouched = true;
  } else if (bind === "jpick-search") {
    const s = e.sources[Number(el.dataset.i)];
    s.proj.query = el.value; clearTimeout(s.projTimer); s.projTimer = setTimeout(() => jiraLoadProjects(s, false), 300);
    return undefined;
  } else if (bind.startsWith("jira-")) {
    const s = e.sources[Number(el.dataset.i)], k = bind.slice(5);
    s[k] = el.value; s.jtest = null;
    if (k === "label") { suggestName(); if ($("f-name") && !e.nameTouched) $("f-name").value = e.name; }
    if (k === "jql" && s.proj) {                                          // a query written by hand wins: the ticks no longer describe it
      s.proj.ticked = {}; s.proj.line = ""; s.proj.replace = false;
      document.querySelectorAll(`#w-src${el.dataset.i} [data-bind=jpick]`).forEach((c) => { c.checked = false; });
      if (!s.advTracked) { s.advTracked = true; track("jira_project_pick", { method: "advanced_jql" }); }
    }
    if (["site", "email"].includes(k) || (k === "jql" && !s.proj)) s.pending = null;          // a changed site or email must be tested again
    const m = $("m-src" + el.dataset.i); if (m) m.innerHTML = "";
    if (k === "token") return undefined;                                // typing a token never triggers a preview
  }
  schedulePreview();
});

document.addEventListener("change", (ev) => {
  const el = ev.target, bind = el.dataset && el.dataset.bind;
  if (!bind || !S.ed) return;
  if (bind === "jpick") {
    const s = S.ed.sources[Number(el.dataset.i)], p = s.proj;
    if (el.checked) p.ticked[el.dataset.key] = true; else delete p.ticked[el.dataset.key];
    if (el.checked && s.jql.trim() && s.jql !== s.builtJql && !p.replace) { p.replace = true; return renderSourceRows(); }     // their own query wins until they say so
    return jiraApplyPicks(s);
  }
  if (bind === "src-enabled") { S.ed.sources[Number(el.dataset.i)].enabled = el.checked; renderSourceRows(); renderEditorLive(); return schedulePreview(0); }
  if (bind === "dest-branch-pick") {
    const x = S.ed.dest, list = (x.branches && x.branches.list) || [], chosen = list.find((i) => i.name === el.value);
    x.branch = chosen && chosen.default ? "" : el.value; x.test = null;
    const input = $("d-branch"); if (input) input.value = x.branch;
    track("branch_pick", { method: "picker" });
    renderBranchList(); schedulePreview(0);
  } else if (bind === "dest-branch") { if (el.value.trim()) track("branch_pick", { method: "typed" }); }
  else if (bind === "dest-repo-pick") { const x = S.ed.dest; x.repo = el.value; x.branches = null; x.test = null; const r = $("d-repo"); if (r) r.value = x.repo; renderDest(); schedulePreview(0); }
  else if (bind === "dest-kind") { const x = S.ed.dest; x.kind = el.value; x.test = null; x.pending = null; renderDest(); schedulePreview(0); }
  else if (bind === "dest-auth") { const x = S.ed.dest; x.auth = el.value; x.test = null; x.pending = null; renderDest(); schedulePreview(0); }
  else if (bind === "namePolicy") { S.ed.namePolicy = el.value; const p = $("f-pattern"); if (p) p.hidden = el.value !== "custom"; schedulePreview(0); }
  else if (bind === "onDelete") { S.ed.onDelete = el.value; renderEditorLive(); schedulePreview(0); }
  else if (bind === "removed-del") { S.ed.removed[Number(el.dataset.k)].del = el.checked; renderEditorLive(); schedulePreview(0); }
});

/* ---------------------------------------------------------------- boot */
window.addEventListener("popstate", routeFromHash);
// Start only when every script has run: firstrun.js, agents.js ... load after this file, and an early server answer would call their functions before they exist.
function boot() { refresh().then(() => { if (/^#\/pipeline\//.test(location.hash)) routeFromHash(); setInterval(poll, POLL_MS); }); }
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
