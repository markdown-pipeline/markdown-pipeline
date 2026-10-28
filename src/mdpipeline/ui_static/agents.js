/* Markdown Pipeline: connect your agent (MD-120). Loaded after app.js and firstrun.js; shares their globals (S, FR, api, esc, copy, toast, ...).
   One panel, two places: the Ready screen of the first run ("Now ask your agent") and Settings -> Agents. Layout and copy follow
   work/designer/first-run/wireframes.md section 4. Nothing here changes an agent's config except the "Connect" click. */

const AG = { pipeline: "", view: null, busy: "", error: "", conflict: null, chosen: "", manual: false, loadingFor: "", manualOpen: false };
// MD-147: a re-render (switching the Claude Code / Codex tab, a poll) must not fold "Another agent" under the person
document.addEventListener("toggle", (ev) => { if (ev.target.classList && ev.target.classList.contains("ag-other")) AG.manualOpen = ev.target.open; }, true);
let agTimer = null;

/* ---------------------------------------------------------------- loading and polling */
async function agLoad(pipeline) {
  if (pipeline !== undefined) AG.pipeline = pipeline;
  AG.loadingFor = AG.pipeline;
  const r = await api("GET", `/api/agents?pipeline=${encodeURIComponent(AG.pipeline)}`);
  AG.loadingFor = "";
  AG.view = r.ok ? r.data : { folder: "", found: [], connected: {}, question: null, commands: {}, manual: {} };      // a failed look must not loop
  agRender();
  agPollIfWaiting();
}

function agEnsure(pipeline) {                                   // called while drawing: load once per pipeline, never in a loop
  if (AG.loadingFor === pipeline || (AG.view && AG.pipeline === pipeline)) return;
  AG.view = null; AG.pipeline = pipeline;
  agLoad(pipeline);
}

function agVisible() { return (S.screen === "firstrun" && FR && FR.step === "ready" && FR.result) || S.screen === "settings" || S.screen === "home"; }

function agRender() {
  if (S.screen === "firstrun" && FR && FR.step === "ready") renderFirstRun();
  else if (S.screen === "settings") renderSettings();
  else if (S.screen === "home") renderHome();
}

function agPollIfWaiting() {                                    // the read confirmation: ask the app every 2 s until the agent has read something
  clearInterval(agTimer); agTimer = null;
  const waiting = AG.view && Object.values(AG.view.connected).some((c) => c.reads === 0);
  if (!waiting || !agVisible()) return;
  agTimer = setInterval(() => { if (!agVisible()) { clearInterval(agTimer); agTimer = null; } else agLoad(); }, 2000);
}

/* ---------------------------------------------------------------- rendering */
const agMono = (lines) => `<pre class="snip ag-exact">${lines.map(esc).join("\n")}</pre>`;
const agFolderName = (v) => esc((v.folder || "").split(/[\\/]/).filter(Boolean).pop() || "your folder");

function agConnectedRow(agent, c) {
  return `<div class="card ag-card ag-on" data-agent="${esc(agent)}"><p><b>✓ ${esc(c.label)} connected</b>
      <button class="btn sm" data-ag="disconnect" data-agent="${esc(agent)}">Disconnect</button></p>
    <p class="muted small">Changed on this computer:</p>${agMono(c.changes)}</div>`;
}

function agQuestion(v) {
  const q = v.question;
  if (!q) return "";
  const agent = Object.keys(v.connected)[0];
  const who = v.connected[agent].label;
  const cmd = v.commands[agent];
  const seen = Object.values(v.connected).reduce((n, c) => n + c.reads, 0);
  const only = Object.values(v.connected).length === 1 ? who : "Your agent";
  const wait = seen > 0 ? `<p class="ag-read" role="status">✓ ${esc(only)} read your files · ${seen === 1 ? "1 read" : `${seen} reads`} through the app</p>`
    : `<p class="ag-wait" role="status"><span aria-hidden="true">●</span> Waiting for ${esc(only)} to read your files…</p>`;
  return `<div class="ag-ask" id="ag-ask"><div class="small muted">ASK THIS FIRST</div>
    <p class="ag-q"><b>${esc(q.question)}</b></p>${q.reason ? `<p class="muted small" id="ag-reason">${esc(q.reason)}</p>` : ""}
    <p><code id="ag-cmd">${esc(cmd)}</code> <button class="btn sm" data-ag="copy-q" data-agent="${esc(agent)}">Copy</button></p>
    <p class="muted small">Paste it in a terminal. ${esc(who)} opens and answers from your files.</p>${wait}</div>`;
}

function agConflict() {
  const c = AG.conflict;
  if (!c) return "";
  return `<div class="vneed warn" id="ag-conflict" role="alert"><span aria-hidden="true">▲</span> <b>${esc(c.file)} already exists in this folder.</b>
    Nothing has been changed yet. <button class="btn sm" data-ag="connect-keep" data-agent="${esc(c.agent)}">Keep yours, add ours as ${esc(c.alternative)}</button>
    <button class="btn sm" data-ag="connect-replace" data-agent="${esc(c.agent)}">Replace yours (a copy is kept)</button></div>`;
}

function agFoundCard(f, primary) {
  if (!primary) return `<div class="card ag-card" data-agent="${esc(f.agent)}"><p><b>${esc(f.label)}</b> <span class="muted small">✓ found</span>
      <button class="btn sm" data-ag="choose" data-agent="${esc(f.agent)}">Use ${esc(f.label)} instead</button></p></div>`;
  const busy = AG.busy === f.agent;
  return `<div class="card ag-card ag-primary" data-agent="${esc(f.agent)}"><p><b>${esc(f.label)}</b> <span class="muted small">✓ found</span></p>
    <p class="small">Registers our MCP server with ${esc(f.label)}, so it can search, read and refresh these files. It never changes your sources. It also adds an AGENTS.md to your folder. Only when you click.</p>
    <details><summary>Exact change</summary>${agMono(f.exact)}</details>
    <p><button class="btn primary" data-ag="connect" data-agent="${esc(f.agent)}" ${busy ? "disabled" : ""}>${busy ? "Connecting…" : `Connect ${esc(f.label)}`}</button></p></div>`;
}

function agManual(intro) {
  return `<div class="ag-manual" id="ag-manual"><p>${intro}</p>${renderMcp()}
    <p><button class="btn sm" data-ag="recheck">Installed it just now? Check again</button></p></div>`;
}

function agPanel(where) {
  const v = AG.view;
  if (!v) return `<section class="ag" id="ag-panel"><p class="muted small">Looking for your agents…</p></section>`;
  const connected = Object.entries(v.connected);
  const rest = v.found.filter((f) => !v.connected[f.agent]);
  const error = AG.error ? `<div class="vneed err" id="ag-error" role="alert"><span aria-hidden="true">✕</span> ${esc(AG.error)}</div>` : "";
  let body = connected.map(([a, c]) => agConnectedRow(a, c)).join("") + (connected.length && where !== "home" ? agQuestion(v) : "");
  if ((where === "ready" || where === "home") && !connected.length) {
    const first = rest.find((f) => f.agent === AG.chosen) || rest[0];
    body += rest.map((f) => agFoundCard(f, f === first)).join("");
    if (rest.length === 1) body += `<p class="muted small">${rest[0].agent === "claude_code" ? "Codex" : "Claude Code"}: not found.</p>`;
  } else if (where === "settings") {
    body += rest.map((f) => `<div class="card ag-card" data-agent="${esc(f.agent)}"><p><b>${esc(f.label)}</b> <span class="muted small">found, not connected</span>
        <button class="btn sm" data-ag="connect" data-agent="${esc(f.agent)}">Connect ${esc(f.label)}</button></p>
        <details><summary>Exact change</summary>${agMono(f.exact)}</details></div>`).join("");
  }
  if (!v.found.length && !connected.length) body += agManual("We didn't find Claude Code or Codex on this computer. Add this to your agent's MCP settings:");
  else body += `<details class="ag-other" ${AG.manualOpen ? "open" : ""}><summary>Another agent (Cursor, …)</summary>${agManual("Add this to the agent's MCP settings:")}</details>`;
  return `<section class="ag" id="ag-panel">${error}${agConflict()}${body}</section>`;
}

function renderAgentsSettings() {
  const row = S.state && S.state.pipelines && S.state.pipelines[0];
  if (!row) return `<section class="card usage" id="ag-settings" aria-labelledby="ag-set-h"><h2 id="ag-set-h">Agents</h2>
    <p class="muted">Connect an agent after your first files are written.</p></section>`;
  agEnsure(row.name);
  return `<section class="card usage" id="ag-settings" aria-labelledby="ag-set-h"><h2 id="ag-set-h">Agents</h2>
    <p>Point Claude Code or Codex at the files of <b>${esc(row.name)}</b>. You can disconnect any time; that removes exactly what was added.</p>${agPanel("settings")}</section>`;
}

/* ---------------------------------------------------------------- actions */
async function agConnect(agent, how) {
  AG.busy = agent; AG.error = ""; AG.conflict = null; agRender();
  const body = { agent, pipeline: AG.pipeline };
  if (how) body.agents_md = how;
  const r = await api("POST", "/api/agents/connect", body);
  AG.busy = "";
  const d = r.data || {};
  if (r.ok && d.conflict) AG.conflict = { agent, ...d.conflict };
  else if (!r.ok || !d.ok) AG.error = d.error || "We couldn't connect this agent.";
  await agLoad();
}

async function agDisconnect(agent) {
  AG.error = "";
  const r = await api("POST", "/api/agents/disconnect", { agent });
  if (!r.ok || !(r.data && r.data.ok)) AG.error = (r.data && r.data.error) || "We couldn't disconnect this agent.";
  await agLoad();
}

document.addEventListener("click", (ev) => {
  const el = ev.target.closest("[data-ag]");
  if (!el) return;
  const agent = el.dataset.agent;
  switch (el.dataset.ag) {
    case "connect": return agConnect(agent);
    case "connect-keep": return agConnect(agent, "keep");
    case "connect-replace": return agConnect(agent, "replace");
    case "disconnect": return agDisconnect(agent);
    case "choose": AG.chosen = agent; return agRender();
    case "recheck": return agLoad();
    case "copy-q": return copy(AG.view.commands[agent], "the command");
    default: return undefined;
  }
});
