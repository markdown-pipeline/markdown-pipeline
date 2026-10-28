/* Markdown Pipeline: the guided first run (MD-118). Loaded after app.js and shares its globals (S, api, esc, track, render, ...).
   Start -> Connect Jira | Choose folder -> Review -> Ready. Layout and copy follow work/designer/first-run/wireframes.md. */
"use strict";

const FR_LARGE = 2000;
const FR_ABANDON_STEP = { start: "choose_source", connect: "authorize", scope: "choose_scope", folder: "choose_source", target: "choose_target", review: "preview" };
const FR_STEP_EVENT = { start: ["choose_source"], connect: ["authorize"], scope: ["choose_scope"], folder: [], target: ["choose_target"], review: ["preview"], ready: ["first_sync"] };
const FR_ORDER = { sample: ["start", "review", "ready"], folder: ["start", "folder", "target", "review", "ready"], jira: ["start", "connect", "scope", "target", "review", "ready"] };
const FR_LABEL = { start: "Source", connect: "Connect Jira", scope: "Projects", folder: "Choose folder", target: "Where files go", review: "Review", ready: "Ready" };
let FR = null;                                              // null: the first run is not showing

const frHome = (p) => (p || "").replace(/^\/home\/[^/]+|^\/Users\/[^/]+/, "~");
const frOrder = (source) => FR_ORDER[source] || FR_ORDER.jira;
const frStepLabels = (source) => frOrder(source).map((s) => FR_LABEL[s]);
const frStepIndex = (step, source) => frOrder(source).indexOf(step);

/* ---------------------------------------------------------------- entering and leaving */
function enterFirstRun() {
  if (FR) { S.screen = "firstrun"; return; }
  S.screen = "firstrun";
  FR = { step: "start", source: "", site: "", email: "", token: "", pending: null, user: "", path: "", dest: "", jql: "", review: null, folder: null,
    target: "", mode: "all", window: 90, suggested: "", destCheck: null, reviewKey: "", began: Date.now(), error: null, busy: false, result: null, resume: null, changeDest: false, changeSchedule: false, since: Date.now(), reviewing: false, vault: null, vpass: "", vconfirm: "", vshow: false, vtracked: false };
  frViewed("start");
  api("GET", "/api/first-run/progress").then((r) => {
    if (r.ok && FR) { FR.defaultJql = r.data.defaults.jql; FR.tokenPage = r.data.defaults.token_page; FR.vault = r.data.vault || null; if (FR.step === "connect" && FR.vault && !FR.vault.ready) renderFirstRun(); }
    if (r.ok && r.data.progress && FR && FR.step === "start") { FR.resume = r.data.progress; if (S.screen === "firstrun") renderFirstRun(); }
  });
}

function leaveFirstRun() { FR = null; if (S.screen === "firstrun") S.screen = "home"; }

function frViewed(step) { (FR_STEP_EVENT[step] || []).forEach((s) => track("onboarding_step_viewed", { step: s })); FR.since = Date.now(); }
function frCompleted(step) {
  const ms = Math.min(Date.now() - FR.since, 3_000_000_000);
  const extra = step === "target" ? { target: frIsGithub() ? "github" : "directory", dest_changed: !frIsGithub() && !!FR.dest && FR.dest !== FR.suggested } : {};     // kinds only, never a path or a repo name
  (FR_STEP_EVENT[step] || []).forEach((s) => track("onboarding_step_completed", { step: s, ms_on_step: ms, ...extra }));
}

function frGo(step) {
  FR.step = step; FR.error = null; FR.busy = false;
  frViewed(step);
  frSave();
  window.scrollTo(0, 0);
  renderFirstRun();
  focusSoon("#main h1");
  if (step === "scope") { frLoadReview(); if (!FR.proj) frLoadProjects(false); }
  if (step === "target") { frTargetEnter(); frLoadReview(); }
  if (step === "review") frLoadReview();
}

let frSaveTimer;
function frSaveSoon() { clearTimeout(frSaveTimer); frSaveTimer = setTimeout(frSave, 400); }

function frSave() {                                         // where the person is; the token is never sent here
  if (!FR || FR.step === "ready" || FR.source === "sample") return;       // the sample has nothing to resume: one click
  api("POST", "/api/first-run/progress", { step: FR.step, source: FR.source, site: FR.site, email: FR.email, path: FR.path, dest: FR.dest });
}

/* ---------------------------------------------------------------- rendering */
function frStepper() {
  const at = frStepIndex(FR.step, FR.source);
  return `<ol class="fr-steps" aria-label="Setup steps">${frStepLabels(FR.source).map((t, i) =>
    `<li${i === at ? ' aria-current="step"' : ""} class="${i < at ? "done" : ""}">${i < at ? "✓ " : ""}${t}</li>`).join("")}</ol>`;
}

const frBad = (field) => (FR.error && FR.error.field === field ? 'aria-invalid="true"' : "");

function frError() {
  if (!FR.error) return "";
  const help = FR.error.help ? ` <a href="${esc(FR.error.help)}" target="_blank" rel="noopener noreferrer" data-fr="help">Troubleshooting: Jira token</a>` : "";
  return `<div class="vneed err" id="fr-error" role="alert"><span aria-hidden="true">✕</span> <b>${esc(FR.error.headline)}</b> ${esc(FR.error.fix || "")}${help}${FR.error.kind === "data_center" ? fbDataCenterButton() : ""}</div>`;
}

function renderFirstRun() {
  if (!FR) return;
  const a = document.activeElement, atTop = !a || a === document.body || (a.matches && a.matches("#main h1"));     // a background read re-renders: keep the focus on the heading
  renderTop();
  const body = { start: frStart, connect: frConnect, scope: frScope, folder: frFolder, target: frTarget, review: frReview, ready: frReady }[FR.step]();
  $("main").innerHTML = `<div class="fr">${FR.step === "ready" ? "" : frStepper()}${body}</div>`;
  if (atTop) { const h = document.querySelector("#main h1"); if (h) h.focus(); }
  frVaultTrack();
}

function frStart() {
  const r = FR.resume;
  const at = r && ({ connect: "Connect Jira", scope: "Projects", folder: "Choose a folder", target: "Where files go", review: "Review" })[r.step];
  const resume = at ? `<div class="banner ok" id="fr-resume" role="status"><b>Welcome back.</b> You stopped at ${at}.${r.site || r.path ? " What you typed is kept, except the token." : ""}
    <button class="btn sm primary" data-fr="resume">Continue where I stopped</button> <button class="btn sm" data-fr="start-over">Start over</button></div>` : "";
  const row = (act, mark, name, reads, needs, time) => `<button class="fr-row" data-fr="${act}"><span class="fr-mark" aria-hidden="true">${mark}</span>
    <span class="fr-main"><b>${name}</b><span class="muted">${reads}</span></span><span class="fr-need"><span>${needs}</span><span class="muted">${time}</span></span></button>`;
  return `<h1 class="fr-title" tabindex="-1">Connect your first source</h1>
    <p class="fr-lede">We turn it into Markdown files in a folder on this computer, and keep them current. Your agent reads them from there.</p>
    ${resume}${renderConsentCard()}${fbPrompt()}
    <h2>Available now</h2>
    <div class="fr-rows">
      ${row("pick-jira", "J", "Jira Cloud", "Issues, comments and statuses from *.atlassian.net", "Needs an API token", "about 2 min")}
      ${row("pick-folder", "▤", "Folder on this computer", "Markdown and text files, e.g. notes or exported docs", "No sign-in", "about 1 min")}
      ${S.state && S.state.pipelines.some((p) => p.sample) ? "" : row("pick-sample", "◇", "Try with sample data", "A made-up engineering team's Jira issues and docs, to see what your agent gets", "No sign-in, no network", "about 30 sec")}
    </div>
    ${fbRequestBlock()}
    <p class="muted small fr-adv">Set up several sources, filters or a GitHub repository at once? <button class="link" data-act="new">Advanced setup</button></p>`;
}

/* MD-163: a machine with no OS keychain keeps its secrets in a file only the user can read, so the first run never asks for a passphrase.
   Review asks only in the two leftover cases, and only when this write stores a secret (the Jira token, a GitHub sign-in):
   "unlock": a vault made by v0.1.1-v0.1.3 (its passphrase, once; the secrets then move to the user-only file) and "unsafe": the secrets file is
   readable by others (a calm message with the command that fixes it). The tested token is kept by the server meanwhile. */
const frVaultOpen = () => !!(FR.vault && !FR.vault.ready);
const frSecrets = () => [FR.source === "jira" ? "Jira token" : "", frIsGithub() ? "GitHub sign-in" : ""].filter(Boolean);
const frNeedsSecret = () => frSecrets().length > 0;
const FR_FILE_STORE_NOTE = (what) => `This computer has no system keychain, so your ${what} is kept in a file only your user can read, the way \`gh\` and the AWS CLI do it. To use a keychain instead, install libsecret-tools.`;
const frFileStore = () => !!(FR.vault && FR.vault.store === "file" && FR.vault.ready);
function frVaultBlock() {
  if (!frVaultOpen() || !frNeedsSecret()) return "";
  if (FR.vault.mode === "unsafe") {
    return `<div class="fr-vault" id="fr-vault" role="group" aria-labelledby="fr-vault-label"><b id="fr-vault-label">Your saved-secrets file needs a quick fix</b>
      <p class="small">${esc(FR.vault.message || "")}</p>
      <button type="button" class="btn sm" data-fr="recheck-vault">I fixed it, check again</button></div>`;
  }
  return `<div class="fr-vault" id="fr-vault" role="group" aria-labelledby="fr-vault-label"><b id="fr-vault-label">Enter your passphrase</b>
    <p class="small">An earlier version protected your tokens with a passphrase. Enter it once: we move them to a file only your user can read, and you will not be asked again, even after a restart.</p>
    <label for="fr-vpass" class="small">Passphrase</label>
    <span class="fr-site"><input id="fr-vpass" data-fr-bind="vpass" type="${FR.vshow ? "text" : "password"}" value="${esc(FR.vpass)}" autocomplete="current-password" ${frBad("vault")}>
      <button type="button" class="btn sm" data-fr="show-vault" aria-pressed="${FR.vshow}">${FR.vshow ? "Hide" : "Show"}</button></span></div>`;
}
function frVaultTrack() {
  if (FR.step === "review" && frNeedsSecret() && FR.vault && FR.vault.store && !FR.vtracked) { FR.vtracked = true; track("secret_store_used", { store: FR.vault.store }); }
}
/* true when the vault is usable (or was just unlocked / fixed); false when the person must act first (a calm message is shown) */
async function frVaultSettle() {
  if (!frVaultOpen() || !frNeedsSecret()) return true;
  const bad = (headline) => { FR.error = { headline, fix: "", field: "vault" }; renderFirstRun(); focusSoon("#fr-vpass"); return false; };
  if (FR.vault.mode === "unsafe") {
    const p = await api("GET", "/api/first-run/progress");
    if (p.ok && p.data.vault) FR.vault = p.data.vault;
    if (frVaultOpen()) return bad(FR.vault.message || "The saved-secrets file is still open to other users.");
    FR.error = null; return true;
  }
  if (!FR.vpass) return bad("Enter your passphrase to continue.");
  const r = await api("POST", "/api/vault/unlock", { passphrase: FR.vpass });
  if (!r.ok) return bad((r.data && (r.data.error || (r.data.errors && r.data.errors[0] && r.data.errors[0].message))) || "That passphrase didn't work.");
  FR.vault = { ready: true, mode: null, store: "file" }; FR.vpass = ""; FR.error = null;
  return true;
}

function frConnect() {
  return `<h1 class="fr-title" tabindex="-1">Connect Jira</h1>
    <p class="fr-lede">About 2 minutes if you have a token. Not ready to create a token? <button class="link" data-fr="pick-folder">Start with a folder</button></p>
    <div class="fr-trust"><p><b>Read-only</b> We read issues, comments and statuses. We never change anything in Jira.</p>
      <p><b>Stays here</b> Your token ${frFileStore() ? "stays" : "is encrypted"} on this computer and is only sent to Atlassian.</p></div>
    <form class="card fr-form" data-fr-form="connect" novalidate>
      <div class="field"><label for="fr-site">Jira site</label>
        <span class="fr-site"><input id="fr-site" data-fr-bind="site" value="${esc(FR.site)}" autocomplete="off" placeholder="acme" ${frBad("site")}><span class="muted">.atlassian.net</span></span>
        <span class="hint">Paste the full address if that is easier</span></div>
      <div class="field"><label for="fr-email">Your Atlassian email</label>
        <input id="fr-email" data-fr-bind="email" type="email" value="${esc(FR.email)}" autocomplete="off" ${frBad("credentials")}></div>
      <div class="field"><label for="fr-token">API token</label>
        <span class="hint"><a href="${esc(FR.tokenPage)}" target="_blank" rel="noopener noreferrer">Create a token at Atlassian ↗</a> Choose <b>Create API token</b> (not "with scopes"), name it "Markdown Pipeline".</span>
        <span class="fr-site"><input id="fr-token" data-fr-bind="token" type="password" value="${esc(FR.token)}" autocomplete="off" ${frBad("credentials")}><button type="button" class="btn sm" data-fr="show-token" aria-pressed="false">Show</button></span></div>
      ${frFileStore() ? `<p class="hint" id="fr-store-note">${esc(FR_FILE_STORE_NOTE("token"))}</p>` : ""}
      ${frError()}
      <div class="fr-actions"><button type="button" class="btn" data-fr="back">Back</button>
        <button type="submit" class="btn primary" ${FR.busy ? "disabled" : ""}>${FR.busy ? "Testing…" : "Test and continue"}</button></div>
    </form>`;
}

function frFolder() {
  const c = FR.folder;
  const msg = !c ? "" : !c.exists ? `<span class="msg err">✕ We can't find that folder.</span>` : !c.is_dir ? `<span class="msg err">✕ That is a file, not a folder.</span>`
    : c.md_files ? `<span class="msg ok">✓ Folder found · ${plural(c.md_files, "Markdown file")}</span>` : `<span class="msg warn">▲ Folder found, but it has no Markdown or text files yet. Choose a folder that has some.</span>`;
  return `<h1 class="fr-title" tabindex="-1">Choose a folder</h1>
    <p class="fr-lede">We read the Markdown and text files in it, and in its subfolders. We never change them.</p>
    <form class="card fr-form" data-fr-form="folder" novalidate>
      <div class="field"><label for="fr-path">Folder path</label>
        <span class="fr-pathrow"><input id="fr-path" data-fr-bind="path" value="${esc(FR.path)}" placeholder="~/notes/team-wiki" autocomplete="off" spellcheck="false">${brBrowseButton("fr-path", "source", "the folder")}</span>
        <span id="fr-folder-msg" aria-live="polite">${msg}</span>
        <span class="hint">Tip: drag the folder from your file manager into this field.</span></div>
      ${frError()}
      <div class="fr-actions"><button type="button" class="btn" data-fr="back">Back</button>
        <button type="submit" class="btn primary" ${c && c.exists && c.is_dir && c.md_files ? "" : "disabled"}>Continue</button></div>
    </form>`;
}

/* ---------------------------------------------------------------- Projects (Jira), MD-159 */
const frWindowText = (w) => (w ? `updated in the last ${w} days` : "all time");
function frScopeText() {
  if (FR.jqlEdited) return "your query";
  const keys = frTicked();
  const projects = FR.mode === "some" && keys.length ? `${plural(keys.length, "project")} (${keys.join(", ")})` : "all projects you can see";
  return `${projects} · ${frWindowText(FR.window)}`;
}

function frCountBox() {
  const v = FR.review;
  if (FR.mode === "some" && !frTicked().length && !FR.jqlEdited) return `<p class="muted" id="fr-count" role="status" aria-live="polite">Tick at least one project, or choose All projects.</p>`;
  if (!v || FR.reviewing) return `<p class="muted" id="fr-count" role="status" aria-live="polite"><span id="fr-activity">Counting issues in Jira…</span></p>`;
  if (!v.ok) return `<div class="vneed err" id="fr-count" role="alert"><span aria-hidden="true">✕</span> ${v.errors.map((e) => esc(e.message)).join(" ")}</div>`;
  if (!v.count) {
    return `<div class="vneed warn" id="fr-count" role="status" aria-live="polite"><span aria-hidden="true">▲</span> <b>No issues match.</b> Nothing was updated in ${FR.mode === "some" ? "these projects" : "your projects"} ${FR.window ? `in the last ${FR.window} days` : "at all"}.
      ${FR.window ? `<button type="button" class="btn sm" data-fr="include-all">Include all time</button>` : ""}</div>`;
  }
  return `<p id="fr-count" role="status" aria-live="polite"><b>${plural(v.count, "issue")}</b> → ${plural(v.count, "Markdown file")}, one per issue, with its comments</p>`;
}

function frLargeWarning(where) {
  const v = FR.review;
  if (!v || !v.ok || v.count <= FR_LARGE) return "";
  const act = where === "scope" ? `<button type="button" class="btn sm" data-fr="last-30">Only the last 30 days</button>` : (FR.source === "jira" ? `<button type="button" class="btn sm" data-fr="narrow">Narrow it</button>` : "");
  return `<div class="vneed warn" id="fr-large" role="status"><span aria-hidden="true">▲</span> The first write of ${v.count.toLocaleString("en-US")} ${v.noun} takes a few minutes. Later syncs only fetch what changed. ${act}</div>`;
}

function frScope() {
  const v = FR.review, p = FR.proj, some = FR.mode === "some";
  const k = p && p.items ? (p.isLast ? String(p.items.length) : p.items.length + "+") : "";
  const opt = (value, on, title, extra) => `<label class="fr-opt${on ? " on" : ""}"><input type="radio" name="fr-scope" value="${value}" data-fr-scope ${on ? "checked" : ""}> <span><b>${title}</b>${extra}</span></label>`;
  const win = [[30, "30 days"], [90, "90 days"], [null, "All time"]].map(([w, t]) => `<label class="fr-seg${FR.window === w ? " on" : ""}"><input type="radio" name="fr-window" value="${w === null ? "all" : w}" data-fr-window ${FR.window === w ? "checked" : ""}> ${t}</label>`).join("");
  const ready = v && v.ok && v.count > 0 && !FR.reviewing && !(some && !frTicked().length && !FR.jqlEdited);
  const label = v && v.ok && !FR.reviewing ? `Continue with ${plural(v.count, "issue")}` : "Continue";
  return `<h1 class="fr-title" tabindex="-1">Which Jira issues should become files?</h1>
    <p class="fr-lede">Connected as ${esc(FR.user)}. Start with everything you can see, or pick some projects. You can change this later.</p>
    <div class="card fr-card" id="fr-scope">
      <fieldset class="fr-fs"><legend>Projects</legend>
        ${opt("all", !some, "All projects you can see", k ? ` <span class="muted">· ${esc(k)} ${k === "1" ? "project" : "projects"}</span>` : "")}
        ${opt("some", some, "Only some projects", "")}
        ${some || (p && p.notice) ? frProjectsHtml() : ""}</fieldset>
      <fieldset class="fr-fs"><legend>Updated in the last</legend><div class="fr-segs">${win}</div></fieldset>
      <div class="fr-countbox">${frCountBox()}</div>
      ${frLargeWarning("scope")}
      <details class="fr-advanced" id="fr-adv" ${FR.advOpen || FR.jqlEdited ? "open" : ""}><summary>Write your own query (JQL) <span class="muted small">optional, replaces the choices above</span></summary>
        <div class="field"><label for="fr-jql">Which issues (JQL)</label><textarea id="fr-jql" data-fr-bind="jql" rows="2" spellcheck="false">${esc(FR.jql || (v && v.jql) || "")}</textarea></div></details>
      <div class="fr-actions"><button class="btn" data-fr="back">Back</button>
        <button class="btn primary" data-fr="scope-next" ${ready ? "" : "disabled"}>${label}</button></div>
    </div>`;
}

/* ---------------------------------------------------------------- Where should your files go?, MD-159 */
async function frTargetEnter() {
  FR.destCheck = null;
  const r = await api("POST", "/api/first-run/dest-check", { ...frBodyCore(), dest: FR.dest });
  if (!FR || FR.step !== "target" || !r.ok) return;
  FR.suggested = r.data.suggested;
  if (!FR.dest) FR.dest = FR.suggested;
  FR.destCheck = r.data;
  if (FR.dest !== FR.suggested) frCheckDestSoon(); else renderFirstRun();
}

let frDestTimer;
function frCheckDestSoon() {
  clearTimeout(frDestTimer);
  FR.destCheck = null; frPaintDest();
  frDestTimer = setTimeout(async () => {
    const dest = FR.dest;
    const r = await api("POST", "/api/first-run/dest-check", { ...frBodyCore(), dest });
    if (!FR || FR.step !== "target" || FR.dest !== dest) return;
    FR.destCheck = r.ok ? r.data : null;
    frPaintDest();
  }, 250);
}

function frDestMessage() {
  const c = FR.destCheck;
  if (!c) return "";
  if (!c.ok) return `<span class="msg err">✕ We can't write there: ${esc(c.reason)}${/choose another folder/i.test(c.reason) ? "" : " Choose another folder."}</span>`;
  return c.exists ? `<span class="msg ok">✓ This folder exists. We add our files next to yours and never change yours.</span>` : `<span class="msg ok">✓ New folder. We create it when you press Write.</span>`;
}
function frPaintDest() {
  const msg = $("fr-dest-msg"), go = document.querySelector('[data-fr="use-folder"]');
  if (msg) msg.innerHTML = frDestMessage();
  if (go) go.disabled = !(FR.destCheck && FR.destCheck.ok);
}

function frGithubCardBody() {
  const g = frGh(), app = (S.state && S.state.github_app) || {};
  const note = g.msg ? `<div class="msg err" role="status"><span>${esc(g.msg)}</span></div>` : "";
  const intro = `<p>Your team's agents read the same files. Changes arrive as one commit per sync, in a repo you choose.</p>
    <p class="muted small">You approve the app on github.com with a short code, then choose which repos it can use. No token to create or paste.</p>`;
  if (g.flow) {
    return `${intro}<div class="fr-gh" id="fr-gh"><p>1. Open <a href="${esc(g.flow.verification_uri)}" target="_blank" rel="noopener noreferrer">${esc(g.flow.verification_uri)}</a></p>
      <p>2. Enter this code: <b class="usercode" id="fr-gh-code">${esc(g.flow.user_code)}</b></p>
      <p class="muted small" role="status">Waiting for you to approve it on GitHub…</p>
      <button type="button" class="btn ghost sm" data-fr="gh-cancel">Cancel</button></div>`;
  }
  if (!g.pending) {
    return `${intro}<div class="fr-gh" id="fr-gh"><div class="fr-cardact"><button type="button" class="btn" data-fr="gh-signin">Sign in with GitHub</button><span class="muted small">GitHub sign-in</span></div>${note}</div>`;
  }
  const repos = g.repos || [];
  const signed = `<div class="msg ok" role="status">✓ <span>Signed in as <b>${esc(g.login)}</b>.</span></div>`;
  if (!repos.length) {
    return `<div class="fr-gh" id="fr-gh">${signed}
      <div class="msg warn" role="status">▲ <span>The app isn't installed on any of your repos yet. <a href="${esc(app.install_url)}" target="_blank" rel="noopener noreferrer">Install it on a repo ↗</a>
      (choose <b>Only select repositories</b>), then come back.</span></div>
      <div class="fr-cardact"><button type="button" class="btn" data-fr="gh-recheck">Check again</button></div>${note}</div>`;
  }
  const ready = !!g.repo;
  return `<div class="fr-gh" id="fr-gh">${signed}
    <label class="tf"><span>Repository</span><select data-fr-gh-repo id="fr-gh-repo"><option value="">Choose a repository…</option>${repos.map((r) => `<option value="${esc(r)}" ${r === g.repo ? "selected" : ""}>${esc(r)}</option>`).join("")}</select></label>
    <p class="hint">Only repos the app is installed on are listed.${g.branch ? ` Branch: ${esc(g.branch)} (the default).` : ""} Nothing is committed until you press Commit on the next screen.</p>
    <div class="fr-cardact"><button type="button" class="btn ${FR.target === "github" ? "" : "primary"}" data-fr="use-repo" ${ready ? "" : 'disabled aria-describedby="fr-repo-why"'}>Use this repo</button>
      ${ready ? "" : `<span class="muted small" id="fr-repo-why">Choose a repository first.</span>`}</div>${note}</div>`;
}

function frTarget() {
  const n = FR.review && FR.review.ok ? FR.review.count : null;
  const lede = n === null ? "We're still counting what to write. Meanwhile, pick where the files go. Your agents read them from there."
    : `${plural(n, "Markdown file")} ${n === 1 ? "is" : "are"} ready to write. Pick where they go. Your agents read them from there.`;
  const dir = FR.target === "directory", gh = FR.target === "github", on = frGithubOffered();
  const folder = `<section class="card fr-pick${dir ? " chosen" : ""}" data-card="folder" aria-labelledby="fr-h-folder">
      <h3 id="fr-h-folder"><span class="fr-ico" aria-hidden="true">▤</span>A folder on this computer</h3>${dir ? `<span class="fr-tag">Your choice</span>` : ""}
      <p>Your agent on this computer reads the files from here.</p>
      <label for="fr-dest">Output folder</label>
      <span class="fr-pathrow"><input id="fr-dest" data-fr-bind="dest" value="${esc(FR.dest)}" autocomplete="off" spellcheck="false">${brBrowseButton("fr-dest", "output", "the output folder")}</span>
      <span id="fr-dest-msg" aria-live="polite">${frDestMessage()}</span>
      <div class="fr-cardact"><button type="button" class="btn" data-fr="use-folder" ${FR.destCheck && FR.destCheck.ok ? "" : "disabled"}>${dir ? "Continue with this folder" : "Use this folder"}</button><span class="muted small">No sign-in</span></div></section>`;
  const repo = on
    ? `<section class="card fr-pick${gh ? " chosen" : ""}" data-card="github" aria-labelledby="fr-h-gh">
      <h3 id="fr-h-gh"><span class="fr-ico" aria-hidden="true">⎇</span>Your team's GitHub repo</h3>${gh ? `<span class="fr-tag">Your choice</span>` : ""}${frGithubCardBody()}</section>`
    : `<section class="card fr-pick disabled" data-card="github" aria-labelledby="fr-h-gh" aria-disabled="true">
      <h3 id="fr-h-gh"><span class="fr-ico" aria-hidden="true">⎇</span>Your team's GitHub repo</h3>
      <p><b>Not available in this build:</b> GitHub sign-in isn't set up. Use a folder now; you can add a repo later in Advanced setup.</p>
      <div class="fr-cardact"><button type="button" class="btn" disabled>Sign in with GitHub</button></div></section>`;
  return `<h1 class="fr-title" tabindex="-1">Where should your files go?</h1>
    <p class="fr-lede" aria-live="polite">${lede}</p>
    <div class="fr-cards" role="group" aria-label="Where the files go">${folder}${repo}</div>
    <div class="fr-actions fr-left"><button class="btn" data-fr="back">Back</button></div>`;
}

/* ---------------------------------------------------------------- Review: confirmation only, MD-159 */
function frReviewView() {
  const v = FR.review;
  if (!v || !v.ok || FR.source === "sample") return v;
  if (frIsGithub()) { const g = frGh(); return { ...v, target: "github", dest: { repo: g.repo, branch: g.branch || "main", path: "", exists: true } }; }
  const c = FR.destCheck;
  return { ...v, target: "directory", dest: c ? { path: c.path, exists: c.exists } : v.dest };
}

function frReview() {
  const v = frReviewView(), jira = FR.source === "jira", sample = FR.source === "sample";
  const head = (lede) => `<h1 class="fr-title" tabindex="-1">Review before we write anything</h1><p class="fr-lede">${lede}</p>`;
  const gh = frIsGithub();
  const verb = gh ? "commit" : "write";
  if (!v || FR.reviewing) {
    return `${head(`Check the three lines below, then press ${gh ? "Commit" : "Write"}. Nothing is ${verb === "commit" ? "committed" : "written"} until you do.`)}
      <div class="card fr-card" id="fr-review"><h2 class="fr-count">Counting what to write…</h2>
      <div class="fr-progress" role="progressbar" aria-label="Counting" aria-busy="true"><span></span></div>
      <p class="muted" role="status"><span id="fr-activity">Looking at ${jira ? "Jira" : "the folder"}…</span></p></div>`;
  }
  if (!v.ok) {
    return `${head("Something needs fixing before we can write.")}<div class="card fr-card" id="fr-review"><div class="vneed err" id="fr-error" role="alert"><span aria-hidden="true">✕</span> ${v.errors.map((e) => esc(e.message)).join(" ")}</div>
      <div class="fr-actions"><button class="btn" data-fr="back">Back</button></div></div>`;
  }
  const n = v.count;
  if (n === 0 && !sample) {
    return `${head("Nothing is written until you press the button.")}<div class="card fr-card" id="fr-review"><h2 class="fr-count">Nothing to write yet</h2>
      <div class="vneed warn" role="status"><span aria-hidden="true">▲</span> ${jira ? "No issue matches." : "This folder has no Markdown or text files."}</div>
      <div class="fr-actions"><button class="btn" data-fr="back">Back</button>
        <button class="btn primary" data-fr="${jira ? "change-from" : "change-from"}">${jira ? "Change what's included" : "Choose another folder"}</button></div></div>`;
  }
  const title = sample ? `${plural(n, "Sample file")} will be written` : jira ? `${n} Jira ${n === 1 ? "issue" : "issues"} → ${plural(n, "Markdown file")}` : `${plural(n, "file")} → ${plural(n, "Markdown file")}`;
  const change = (what) => (sample ? "" : `<button type="button" class="btn sm" data-fr="change-${what}">Change</button>`);
  const from = sample ? "Sample data bundled with the app: a made-up team's Jira issues and docs" : jira ? `${esc(frSiteHost())} · ${esc(frScopeText())}` : esc(frHome(FR.path));
  const where = gh ? `<span class="mono">${esc(v.dest.repo)}</span>` : `<span class="mono">${esc(frHome(v.dest.path))}</span>`;
  const toSub = gh ? `Your GitHub repo · branch ${esc(v.dest.branch)} · one commit per sync. Your team's agents read the files from there.`
    : `Your output folder · ${v.dest.exists ? "already exists" : "new, created when you press Write"}. Your agent on this computer reads the files from here.`;
  const then = sample ? "The sample doesn't sync by itself. You can remove it in one click."
    : gh ? "Checks for changes every hour and commits them, including deletions. It keeps running when this window is closed and starts when you log in to your desktop. You can turn it off any time."
      : "Checks for changes every hour, including deletions. It keeps running when this window is closed and starts when you log in to your desktop. You can turn it off any time.";
  const first = v.first.map((t, i) => `<li>${esc(t)}${v.paths && v.paths[i] ? ` <span class="mono muted small">→ ${esc(v.paths[i])}</span>` : ""}</li>`).join("");
  const example = v.example ? `<div><h3 class="fr-h3">Example file: ${esc(v.example.name)}</h3><pre id="fr-example" class="snip">${esc(v.example.text)}</pre>
      <p class="muted small">The top block tells your agent where each file came from.</p></div>` : "";
  const label = sample ? `Write ${plural(n, "file")}` : gh ? `Commit ${plural(n, "file")} to ${esc(v.dest.repo)}` : `Write ${plural(n, "file")} to ${esc(frHome(v.dest.path))}`;
  const ok = n && !FR.busy && (sample || gh || (FR.destCheck ? FR.destCheck.ok : true)) && (!gh || v.dest.repo);
  return `${head(`Check the three lines below, then press ${gh ? "Commit" : "Write"}. Nothing is ${gh ? "committed" : "written"} until you do.`)}
    <div id="fr-review"><div class="card fr-card" id="fr-summary">
      <h2 class="fr-count">${title}</h2>${frLargeWarning("review")}
      <div class="fr-rows3">
        <div class="fr-r3" data-row="from"><span class="fr-k">From</span><div>${from}<div class="fr-sub ok">✓ ${esc(v.read_only)}</div></div>${change("from")}</div>
        <div class="fr-r3" data-row="to"><span class="fr-k">To</span><div>${sample ? `<span class="mono">${esc(frHome(v.dest.path))}</span>` : where}<div class="fr-sub">${sample ? "Your output folder" : toSub}</div></div>${change("to")}</div>
        <div class="fr-r3" data-row="then"><span class="fr-k">Then</span><div>${then}</div><span></span></div>
      </div>
      ${frVaultBlock()}${frVaultOpen() && frNeedsSecret() ? frError() : ""}${frFileStore() && frIsGithub() && FR.source !== "jira" ? `<p class="hint" id="fr-store-note">${esc(FR_FILE_STORE_NOTE("GitHub sign-in"))}</p>` : ""}
      <div class="fr-actions"><button class="btn" data-fr="back">Back</button>
        <button class="btn primary fr-write" data-fr="write" ${ok ? "" : "disabled"}>${label}</button></div>
    </div>
    <section class="fr-preview" aria-labelledby="fr-prev-h"><h2 class="fr-h3" id="fr-prev-h">Preview: what your agent gets</h2>
      <div class="fr-prevgrid"><div><h3 class="fr-h3">First ${Math.min(n, 5)} of ${n}</h3><ul class="fr-first">${first}</ul>
        <p class="muted small">${v.more ? `and ${v.more} more. ` : ""}${sample ? "Issues and docs, one file each." : jira ? "One file per issue, with its comments." : "One file per source file."}</p></div>${example}</div></section></div>`;
}

function agReady() { agEnsure(FR.pipeline); return agPanel("ready"); }

function frSiteHost() { return (FR.site || "").replace(/^https?:\/\//, "").replace(/\/.*$/, "").replace(/^([^.]+)$/, "$1.atlassian.net"); }

function frReady() {
  const r = FR.result;
  if (!r) {
    return `<div id="fr-ready"><h1 class="fr-title" tabindex="-1">Writing your files</h1>
      <p class="fr-lede">${FR.source === "jira" ? "Read-only in Jira. " : ""}You can close this window; the sync continues.</p>
      <div class="fr-progress" role="progressbar" aria-label="Writing your files" aria-busy="true"><span></span></div>
      <p class="muted" role="status"><span id="fr-activity">Starting…</span></p></div>`;
  }
  const gh = r.target === "github";
  const folder = esc(gh ? r.repo : frHome(r.folder));
  const openIt = gh ? `<a class="btn" href="${esc(r.repo_url)}" target="_blank" rel="noopener noreferrer">Open on GitHub</a>` : `<button class="btn" data-fr="open-folder">Open folder</button>`;
  if (r.result === "error" || r.files === 0) {
    const why = (r.errors && r.errors[0] && r.errors[0].error) || r.message || "";
    return `<div id="fr-ready"><h1 class="fr-title" tabindex="-1">We couldn't finish writing your files</h1>
      <p class="fr-lede">${r.files ? `${plural(r.files, "file")} ${r.files === 1 ? "is" : "are"} in <span class="mono">${folder}</span>. ` : ""}Nothing was deleted. Trying again continues from here.</p>
      ${why ? `<p class="muted small">${esc(why)}</p>` : ""}
      <div class="fr-actions"><button class="btn primary" data-fr="retry">Try again</button> ${openIt}
        <button class="btn ghost" data-fr="home">Go to Home</button></div></div>`;
  }
  const partial = r.errors && r.errors.length ? `<div class="vneed warn" role="status"><span aria-hidden="true">▲</span> ${plural(r.errors.length, "item")} couldn't be read. The others are written. See Activity for which.</div>` : "";
  const next = r.sample ? "This is made-up data, labelled Sample. It doesn't sync by itself." : r.schedule === "off" ? `Automatic sync is off. Use Sync now on Home when you want fresh files. <button class="link" data-fr="sched-change">Turn on</button>`
    : `Stays current: next check at ${esc(clock(r.next_sync))}, then ${esc(intervalText(r.schedule))}. <button class="link" data-fr="sched-change">Change</button> · <button class="link" data-fr="sched-off">Turn off</button>`;
  const picker = FR.changeSchedule ? `<label class="sched"><span class="muted small">Sync automatically</span>
      <select data-fr-schedule>${INTERVALS.filter((i) => i[0] !== "off").map(([v, t]) => `<option value="${v}"${r.schedule === v ? " selected" : ""}>${t}</option>`).join("")}</select></label>` : "";
  const sampleNext = r.sample ? `<div class="fr-sample" id="fr-sample-next"><h2>Now connect your own source</h2>
      <div class="fr-actions"><button class="btn primary" data-fr="own-source">Connect my own source</button>
        <button class="btn" data-fr="remove-sample">Remove the sample</button></div></div>` : "";
  return `<div id="fr-ready"><h1 class="fr-title" tabindex="-1">✓ ${plural(r.files, r.sample ? "Sample file" : "file")} in <span class="mono">${folder}</span></h1>${partial}
    <p>${openIt}</p>
    <p class="fr-next">${next}</p>${r.sample ? "" : picker}
    ${!r.sample && r.schedule !== "off" && S.state && S.state.background && S.state.background.linger_notice ? `${noteLinger("ready")}<p class="muted small" id="fr-linger" role="status">${lingerHtml(S.state.background.linger_text)}</p>` : ""}
    ${gh ? `<h2>Where your agents read them</h2><p id="fr-gh-agents">Your team's agents read these files from the repo. The agent on this computer searches a local folder, so to use it with this repo add a local clone in <b>Edit</b>.</p>`
      : `<h2>Now ask your agent</h2>${agReady()}`}${sampleNext}
    <div class="fr-actions"><button class="btn ${r.sample ? "" : "primary"}" data-fr="home">Go to Home</button></div></div>`;
}

/* ---------------------------------------------------------------- actions */
function frPick(source) {
  noteNoAnswer();                                           // choosing a source without answering the usage card means "don't share"
  frCompleted("start");
  if (source === "sample") track("try_started", {});
  if (FR.source !== source) { FR.dest = ""; FR.suggested = ""; FR.destCheck = null; FR.target = ""; FR.gh = null; FR.jql = ""; FR.mode = "all"; FR.window = 90; FR.review = null; FR.reviewKey = ""; FR.pending = null; FR.proj = null; FR.jqlEdited = false; }
  FR.source = source; FR.error = null;
  if (source === "sample") { frGo("review"); return frLoadReview(); }
  frGo(source === "jira" ? "connect" : "folder");
  return undefined;
}

function enterOwnSource() { FR = null; enterFirstRun(); render(); }                  // from the sample or Home: the same Start screen, nothing prefilled

async function removeSample() {
  const r = await api("POST", "/api/sample/remove", {});
  toast(r.ok ? "Sample removed" : (r.data && r.data.error) || "could not remove the sample");
  if (FR) FR = null;
  await refresh();
  render();
}

function frBack() {
  const order = frOrder(FR.source), i = order.indexOf(FR.step);
  frGo(i > 0 ? order[i - 1] : "start");
}

async function frTestConnection() {
  if (FR.busy) return;
  if (FR.pending && !FR.token) { frCompleted("connect"); return frGo("scope"); }      // tested before: Back and forward needs no retyping
  if (!FR.site.trim() || !FR.email.trim() || !FR.token.trim()) {
    FR.error = { headline: "Fill in the site, your email and the token.", fix: "Then press Test and continue.", field: "none" };
    return renderFirstRun();
  }
  FR.busy = true; FR.error = null; renderFirstRun();
  const r = await api("POST", "/api/first-run/jira-test", { site: FR.site, email: FR.email.trim(), token: FR.token, pending: FR.pending });
  FR.busy = false;
  if (!r.ok || !r.data.ok) {
    const d = r.data || {};
    FR.error = d.headline ? { headline: d.headline, fix: d.fix, field: d.field, help: d.help, kind: d.kind } : { headline: d.error || "We couldn't check this Jira connection.", fix: "", field: "none" };
    trackError("source", d.kind === "auth" ? "auth" : d.kind === "offline" || d.kind === "unavailable" ? "network" : "bad-input");
    return renderFirstRun();
  }
  FR.vault = r.data.vault || FR.vault;                                  // only an old passphrase vault or an unsafe secrets file is asked about, on Review (the tested token is kept meanwhile)
  FR.user = r.data.user; FR.pending = r.data.pending || null; FR.token = ""; FR.proj = null; FR.jqlEdited = false; FR.advTracked = false; FR.review = null; FR.reviewKey = "";
  frCompleted("connect");
  return frGo("scope");
}

function frSiteForApi() {
  const raw = FR.site.trim();
  if (/^https?:\/\//i.test(raw)) { try { return `https://${new URL(raw).hostname.toLowerCase()}`; } catch (e) { return raw; } }
  const host = raw.replace(/\/.*$/, "").toLowerCase();
  return `https://${host.includes(".") ? host : host + ".atlassian.net"}`;
}

const frBodyCore = () => FR.source === "sample" ? { source: "sample", dest: FR.dest } : FR.source === "jira"
  ? { source: "jira", site: frSiteForApi(), email: FR.email.trim(), token: FR.token, pending: FR.pending, jql: FR.jql, dest: FR.dest }
  : { source: "folder", path: FR.path.trim(), dest: FR.dest };
const frBody = () => {
  const b = frBodyCore();
  if (b.dest === FR.suggested) b.dest = "";                       // the suggested folder is the server's default
  if (frIsGithub()) { const g = frGh(); Object.assign(b, { target: "github", repo: g.repo, branch: g.branch, gh_pending: g.pending }); }
  return b;
};

/* ---------------------------------------------------------------- GitHub target (MD-152) */
const frGh = () => (FR.gh = FR.gh || { flow: null, login: "", pending: null, repos: null, repo: "", branch: "", msg: "", timer: null });
const frGithubOffered = () => FR.source !== "sample" && !!(S.state && S.state.github_app && S.state.github_app.configured);      // only when "Sign in with GitHub" works here
const frIsGithub = () => FR.target === "github" && frGithubOffered();

async function frGhSignIn() {
  const g = frGh();
  g.msg = "";
  const r = await api("POST", "/api/signin/start", {});
  if (!r.ok) { g.msg = "✕ " + ((r.data && r.data.error) || "could not start the sign-in"); return renderFirstRun(); }
  g.flow = r.data; renderFirstRun();
  const poll = async () => {
    if (!FR || FR.gh !== g || !g.flow) return undefined;
    const p = await api("POST", "/api/signin/poll", { flow: g.flow.flow });
    if (!FR || FR.gh !== g || !g.flow) return undefined;
    if (!p.ok) { g.flow = null; g.msg = "✕ " + ((p.data && p.data.error) || "the sign-in failed"); return renderFirstRun(); }
    if (p.data.status === "pending") { g.timer = setTimeout(poll, (p.data.interval || g.flow.interval || 5) * 1000); return undefined; }
    g.flow = null;
    if (p.data.status !== "ok") { g.msg = "✕ " + p.data.message; return renderFirstRun(); }
    g.login = p.data.login; g.pending = p.data.pending; g.repos = p.data.repos || [];
    renderFirstRun();
    return g.repos.length === 1 ? frGhPick(g.repos[0]) : undefined;
  };
  clearTimeout(g.timer);
  g.timer = setTimeout(poll, (g.flow.interval || 5) * 1000);
  return undefined;
}

async function frGhPick(repo) {
  const g = frGh();
  g.repo = repo; g.branch = "";
  renderFirstRun();
  if (!repo) return undefined;
  const b = await api("POST", "/api/github/branches", { destination: { type: "github", repo, auth: "sign-in" }, pending: g.pending });
  if (FR && FR.gh === g && b.ok && b.data.ok) g.branch = b.data.default || "";
  return FR && FR.gh === g ? renderFirstRun() : undefined;
}

async function frGhRecheck() {
  const g = frGh();
  const r = await api("POST", "/api/signin/repos", { pending: g.pending });
  if (r.ok && r.data.repos) { g.repos = r.data.repos; if (g.repos.length === 1) return frGhPick(g.repos[0]); }
  return renderFirstRun();
}

function frUseFolder() {
  if (!(FR.destCheck && FR.destCheck.ok)) return;
  if (FR.gh) clearTimeout(FR.gh.timer);
  FR.target = "directory"; FR.gh = null;              // the GitHub choice is discarded; nothing was written
  frCompleted("target");
  frGo("review");
}
function frUseRepo() {
  const g = frGh();
  if (!g.repo) return;
  FR.target = "github";
  frCompleted("target");
  frGo("review");
}

/* ---------------------------------------------------------------- Jira project picker (MD-141) */
const frTicked = () => Object.keys((FR.proj && FR.proj.ticked) || {}).sort();

function frProjectsHtml() {
  const p = FR.proj;
  if (!p) return `<section class="fr-projects" id="fr-projects" aria-label="Projects"><p class="muted small" role="status">Looking for your projects…</p></section>`;
  if (p.notice) {
    return `<section class="fr-projects" id="fr-projects" aria-label="Projects"><div class="vneed warn" role="status"><span aria-hidden="true">▲</span> <b>${esc(p.notice.headline)}</b> ${esc(p.notice.fix)}
      ${p.notice.kind === "missing_scope" ? ` <a href="${esc(p.notice.help)}" target="_blank" rel="noopener noreferrer" data-fr="help">Fix the token</a>` : ""}</div>
      <p class="muted small">The default query still works. Write your own under Advanced.</p></section>`;
  }
  const rows = p.items.map((x) => `<li><label><input type="checkbox" data-fr-pick="${esc(x.key)}" ${p.ticked[x.key] ? "checked" : ""}> <span>${esc(x.name)} (${esc(x.key)})</span></label></li>`).join("");
  const ticked = frTicked();
  const built = ticked.length && p.line ? `<label class="small muted" for="fr-jql-built">What we will read</label>
      <textarea id="fr-jql-built" readonly rows="2" class="mono">${esc(FR.jql)}</textarea><p id="fr-projects-line" class="small">${esc(p.line)}</p>` : "";
  const ask = p.replace ? `<div class="vneed warn" id="fr-replace-jql" role="alertdialog" aria-label="Replace your query"><span aria-hidden="true">▲</span> Picking projects will replace the query you wrote.
      <button class="btn sm" data-fr="replace-jql">Replace my query</button> <button class="btn sm" data-fr="keep-jql">Keep my query</button></div>` : "";
  return `<section class="fr-projects" id="fr-projects" aria-label="Projects">
    <div class="fr-psearch"><label class="small" for="fr-psearch">Search projects</label>
      <input id="fr-psearch" type="search" data-fr-psearch value="${esc(p.query)}" autocomplete="off" spellcheck="false">
      <button type="button" class="btn sm" data-fr="select-all-projects" ${p.items.length ? "" : "disabled"}>Select all</button></div>
    <ul class="fr-plist">${rows || `<li class="muted small">No project matches.</li>`}</ul>
    <p class="muted small" id="fr-projects-hint">${ticked.length ? `${ticked.length} ticked.` : "Tick at least one project, or choose All projects."}</p>
    ${p.isLast ? "" : `<button type="button" class="btn sm" data-fr="more-projects">Show more projects</button>`}${ask}${built}</section>`;
}

async function frLoadProjects(more) {
  const p = (FR.proj = FR.proj || { items: [], ticked: {}, query: "", isLast: true, next: 0, notice: null, line: "", replace: null });
  const r = await api("POST", "/api/jira/projects", { site: frSiteForApi(), email: FR.email.trim(), token: FR.token, pending: FR.pending, query: p.query, start_at: more ? p.next : 0 });
  if (!FR || FR.proj !== p) return;
  const d = r.ok ? r.data : null;
  if (!d || (!d.ok && !d.headline)) p.notice = { kind: "other", headline: "We couldn't list your projects.", fix: "The default query still works.", help: "/jira-token-help.md" };
  else if (!d.ok || d.kind === "no_projects") p.notice = { kind: d.kind, headline: d.headline, fix: d.fix, help: d.help || "/jira-token-help.md" };
  else { p.notice = null; p.items = more ? p.items.concat(d.projects) : d.projects; p.isLast = d.is_last; p.next = d.next_start; }
  if (d && d.pending) FR.pending = d.pending;
  if (FR.step === "scope") renderFirstRun();
  const search = $("fr-psearch");
  if (search && p.refocus) { search.focus(); search.setSelectionRange(search.value.length, search.value.length); p.refocus = false; }
}

let frProjectTimer;
function frSearchProjectsSoon(value) {
  const p = FR.proj;
  p.query = value; p.refocus = true;
  clearTimeout(frProjectTimer);
  frProjectTimer = setTimeout(() => frLoadProjects(false), 300);
}

async function frApplyScope() {
  const p = FR.proj, keys = FR.mode === "some" ? frTicked() : [];
  p.replace = null;
  FR.jqlEdited = false; FR.destMsg = "";
  if (FR.mode === "some" && !keys.length) { FR.jql = ""; p.line = ""; FR.review = null; FR.reviewKey = ""; FR.reviewing = false; return renderFirstRun(); }
  const r = await api("POST", "/api/jira/build-jql", { keys, days: FR.window });
  if (!r.ok) { toast((r.data && r.data.error) || "Could not build the query."); return renderFirstRun(); }
  FR.jql = r.data.jql; p.line = r.data.line;
  FR.review = null; FR.reviewKey = "";
  renderFirstRun();
  return frLoadReview();
}

function frPickChanged(key, on) {
  const p = FR.proj;
  if (on) p.ticked[key] = true; else delete p.ticked[key];
  if (FR.jqlEdited && on && !p.replace) { p.replace = true; return renderFirstRun(); }       // the query they wrote wins until they say so
  return frApplyScope();
}

// MD-153: while a long step runs, say what it is doing and how far ("Reading issues from Jira… 120 of 210"), never a static line.
function frPollActivity() {
  let on = true;
  const tick = async () => {
    if (!on) return;
    const r = await api("GET", "/api/first-run/activity");
    const el = document.getElementById("fr-activity");
    if (on && el && r.ok && r.data.activity && r.data.activity.text) el.textContent = r.data.activity.text;
    if (on) setTimeout(tick, 700);
  };
  setTimeout(tick, 400);
  return () => { on = false; };
}

function frReviewKey() { return JSON.stringify([FR.source, FR.site, FR.path, FR.jql, FR.pending ? 1 : 0]); }

/* MD-159: the count and the preview are read in the background as soon as the source is scoped, so Review normally opens with the numbers ready. */
async function frLoadReview(force) {
  const key = frReviewKey();
  if (!force && FR.reviewKey === key && FR.review) { frTrackDryRun(); return; }
  if (FR.reviewing && FR.reviewKey === key) return;
  FR.reviewKey = key; FR.review = null; FR.reviewing = true;
  if (FR.step === "scope" || FR.step === "review") renderFirstRun();
  const stopPolling = frPollActivity();
  const r = await api("POST", "/api/first-run/review", frBody());
  stopPolling();
  if (!FR || FR.reviewKey !== key) return;                              // the scope changed meanwhile: a newer read is on its way
  FR.reviewing = false;
  FR.review = r.ok ? r.data : { ok: false, errors: [{ message: (r.data && r.data.error) || "We couldn't read the source." }] };
  frTrackDryRun();
  const keep = document.activeElement && document.activeElement.id;
  renderFirstRun();
  if (keep && $(keep) && $(keep).setSelectionRange) { const el = $(keep); el.focus(); el.setSelectionRange(el.value.length, el.value.length); }
}

function frTrackDryRun() {
  if (FR.step === "review" && FR.review && FR.review.ok && FR.dryTracked !== FR.reviewKey) {
    FR.dryTracked = FR.reviewKey;
    track("dry_run_viewed", { items_bucket: bucketOf(FR.review.count), would_delete_bucket: "0" });
  }
}

let frDebounce;
function frReviewSoon() { clearTimeout(frDebounce); FR.reviewKey = ""; frDebounce = setTimeout(() => frLoadReview(true), 450); }

let frFolderTimer;
function frCheckFolderSoon() {
  clearTimeout(frFolderTimer);
  frFolderTimer = setTimeout(async () => {
    const r = await api("POST", "/api/folder-check", { path: FR.path });
    if (!FR || FR.step !== "folder") return;
    FR.folder = r.ok ? r.data : null;
    const msg = $("fr-folder-msg"), go = document.querySelector('[data-fr-form="folder"] button[type=submit]');
    const c = FR.folder;
    if (msg) msg.innerHTML = !c ? "" : !c.exists ? `<span class="msg err">✕ We can't find that folder.</span>` : !c.is_dir ? `<span class="msg err">✕ That is a file, not a folder.</span>`
      : c.md_files ? `<span class="msg ok">✓ Folder found · ${plural(c.md_files, "Markdown file")}</span>` : `<span class="msg warn">▲ Folder found, but it has no Markdown or text files yet. Choose a folder that has some.</span>`;
    if (go) go.disabled = !(c && c.exists && c.is_dir && c.md_files);
  }, 250);
}

async function frWrite() {
  if (FR.busy) return;
  if (!(await frVaultSettle())) return undefined;                        // only when this write stores a secret (Jira token, GitHub sign-in)
  FR.busy = true; frCompleted("review");
  FR.step = "ready"; FR.result = null; frViewed("ready");
  renderFirstRun(); focusSoon("#main h1");
  const stopPolling = frPollActivity();
  const r = await api("POST", "/api/first-run/write", frBody());
  stopPolling();
  FR.busy = false;
  if (!r.ok && r.data && r.data.needs_passphrase) {                         // MD-155: e.g. a GitHub sign-in token; nothing was written, everything typed is kept
    const p = await api("GET", "/api/first-run/progress");
    FR.vault = (p.ok && p.data.vault && !p.data.vault.ready) ? p.data.vault : { ready: false, mode: "unlock" };
    FR.step = "review"; FR.result = null; FR.error = null; renderFirstRun(); return focusSoon("#fr-vpass");
  }
  if (!r.ok || !r.data.ok) {
    FR.step = "review"; FR.result = null; FR.reviewKey = "";
    FR.review = { ok: false, errors: (r.data && r.data.errors) || [{ message: (r.data && r.data.error) || "We couldn't write the files." }] };
    return renderFirstRun();
  }
  FR.result = r.data; FR.pipeline = r.data.pipeline;
  frCompleted("ready");
  if (r.data.sample) track("try_completed", { duration_s: Math.min(Math.round((Date.now() - FR.began) / 1000), 3600) });
  await refresh();
  renderFirstRun(); focusSoon("#main h1");
}

async function frRetry() {
  const prev = FR.result;
  FR.result = null; renderFirstRun();
  const r = await api("POST", `/api/pipelines/${encodeURIComponent(FR.pipeline)}/sync`, {});
  const run = r.data || {};
  FR.result = { ...prev, result: run.result || "error", errors: run.errors || [], message: run.message || (r.data && r.data.error) || "", files: (run.counts || {}).created || prev.files };
  await refresh();
  const p = S.state.pipelines.find((x) => x.name === FR.pipeline);
  if (p) { FR.result.schedule = p.schedule; FR.result.next_sync = p.next_sync; }
  renderFirstRun(); focusSoon("#main h1");
}

async function frSetSchedule(interval) {
  const r = await api("POST", `/api/pipelines/${encodeURIComponent(FR.pipeline)}/schedule`, { interval });
  if (!r.ok) return toast((r.data && r.data.error) || "could not change the interval");
  await refresh();
  const p = S.state.pipelines.find((x) => x.name === FR.pipeline);
  FR.result.schedule = interval; FR.result.next_sync = p ? p.next_sync : null; FR.changeSchedule = interval !== "off" && FR.changeSchedule;
  renderFirstRun();
}

async function frStartOver() { await api("POST", "/api/first-run/progress", { clear: true }); FR.resume = null; renderFirstRun(); }

function frResume() {
  const r = FR.resume;
  FR.source = r.source || ""; FR.site = r.site || ""; FR.email = r.email || ""; FR.path = r.path || ""; FR.dest = r.dest || ""; FR.resume = null;
  if (FR.source === "jira") return frGo("connect");                       // the token was not kept: a Jira run starts again from Connect
  if (!FR.path) return frGo("start");
  if (r.step === "target" || r.step === "review") { frGo("target"); return frCheckFolderSoon(); }
  frGo("folder");
  return frCheckFolderSoon();
}

/* ---------------------------------------------------------------- events */
document.addEventListener("click", (ev) => {
  const el = ev.target.closest("[data-fr]");
  if (!el || !FR) return;
  switch (el.dataset.fr) {
    case "help": return track("help_opened", { topic_id: "jira_token" });
    case "pick-jira": return frPick("jira");
    case "pick-folder": return frPick("folder");
    case "pick-sample": return frPick("sample");
    case "own-source": return enterOwnSource();
    case "remove-sample": return removeSample();
    case "back": return frBack();
    case "resume": return frResume();
    case "start-over": return frStartOver();
    case "show-token": { const t = $("fr-token"); t.type = t.type === "password" ? "text" : "password"; el.textContent = t.type === "password" ? "Show" : "Hide"; el.setAttribute("aria-pressed", String(t.type === "text")); return undefined; }
    case "scope-next": frCompleted("scope"); return frGo("target");
    case "use-folder": return frUseFolder();
    case "use-repo": return frUseRepo();
    case "change-from": return frGo(FR.source === "jira" ? "scope" : "folder");
    case "change-to": return frGo("target");
    case "narrow": return frGo("scope");
    case "recheck-vault": return frVaultSettle().then(() => { renderFirstRun(); });
    case "show-vault": FR.vshow = !FR.vshow; renderFirstRun(); return focusSoon("#fr-vpass");
    case "include-all": FR.window = null; return frApplyScope();
    case "last-30": FR.window = 30; return frApplyScope();
    case "select-all-projects": FR.proj.items.forEach((x) => { FR.proj.ticked[x.key] = true; }); return frApplyScope();
    case "more-projects": return frLoadProjects(true);
    case "replace-jql": FR.proj.replace = null; return frApplyScope();
    case "keep-jql": FR.proj.ticked = {}; FR.proj.replace = null; return renderFirstRun();
    case "gh-signin": return frGhSignIn();
    case "gh-cancel": { const g = frGh(); clearTimeout(g.timer); g.flow = null; return renderFirstRun(); }
    case "gh-recheck": return frGhRecheck();
    case "write": return frWrite();
    case "retry": return frRetry();
    case "open-folder": return api("POST", "/api/first-run/open-folder", { pipeline: FR.pipeline }).then((r) => { if (!r.ok || !r.data.ok) toast("Could not open the folder. It is at " + (FR.result ? FR.result.folder : "")); });
    case "sched-change": FR.changeSchedule = true; if (FR.result && FR.result.schedule === "off") return frSetSchedule("1h"); renderFirstRun(); return focusSoon("select[data-fr-schedule]");
    case "sched-off": return frSetSchedule("off");
    case "home": leaveFirstRun(); return go("home");
    default: return undefined;
  }
});

document.addEventListener("submit", (ev) => {
  const form = ev.target.closest("[data-fr-form]");
  if (!form || !FR) return;
  ev.preventDefault();
  if (form.dataset.frForm === "connect") frTestConnection();
  else if (FR.folder && FR.folder.exists && FR.folder.is_dir && FR.folder.md_files) { frCompleted("folder"); frGo("target"); }
});

document.addEventListener("input", (ev) => {
  const el = ev.target, bind = el.dataset && el.dataset.frBind;
  if (!bind || !FR) return;
  FR[bind] = el.value;
  if (bind !== "token" && bind !== "jql" && bind !== "vpass") frSaveSoon();
  if (bind === "path") { FR.folder = null; frCheckFolderSoon(); }
  if (bind === "site" || bind === "email") FR.pending = null;                     // a changed site or email must be tested again
  if (bind === "jql") {
    FR.jqlEdited = true;
    if (FR.proj) { FR.proj.ticked = {}; FR.proj.line = ""; FR.proj.replace = null; }
    if (!FR.advTracked) { FR.advTracked = true; track("jira_project_pick", { method: "advanced_jql" }); }
  }
  if (bind === "dest") frCheckDestSoon();
  if (bind === "jql") frReviewSoon();
});

document.addEventListener("input", (ev) => { if (FR && ev.target.matches && ev.target.matches("[data-fr-psearch]")) frSearchProjectsSoon(ev.target.value); });
document.addEventListener("toggle", (ev) => { if (FR && ev.target.id === "fr-adv") FR.advOpen = ev.target.open; }, true);
document.addEventListener("change", (ev) => { const el = ev.target; if (FR && FR.proj && el.matches && el.matches("[data-fr-pick]")) frPickChanged(el.dataset.frPick, el.checked); });
document.addEventListener("change", (ev) => { const el = ev.target; if (FR && el.matches && el.matches("select[data-fr-schedule]")) frSetSchedule(el.value); });
document.addEventListener("change", (ev) => {
  const el = ev.target;
  if (!FR || !el.matches) return;
  if (el.matches("input[data-fr-scope]")) { FR.mode = el.value; frApplyScope(); }
  if (el.matches("input[data-fr-window]")) { FR.window = el.value === "all" ? null : Number(el.value); frApplyScope(); }
});
document.addEventListener("change", (ev) => { const el = ev.target; if (FR && el.matches && el.matches("select[data-fr-gh-repo]")) frGhPick(el.value); });

// Leaving before the first files exist is the key drop-off signal: the step, nothing else (MD-118).
window.addEventListener("pagehide", () => {
  if (!FR || FR.step === "ready") return;
  // MD-168: remembered on this computer whether or not usage is shared, so the next open can ask what stopped them (nothing is sent by this).
  try { fetch("/api/feedback/first-run-ended", { method: "POST", keepalive: true, headers: { "Content-Type": "application/json", "X-MDP-Token": TOKEN }, body: "{}" }); } catch (e) { /* the page is closing */ }
  if (!S.state || !S.state.usage || !S.state.usage.sharing) return;
  const last = FR_ABANDON_STEP[FR.step];
  if (last) try { fetch("/api/usage/event", { method: "POST", keepalive: true, headers: { "Content-Type": "application/json", "X-MDP-Token": TOKEN },
    body: JSON.stringify({ event: "session_ended_before_first_sync", properties: { last_step: last } }) }); } catch (e) { /* the page is closing */ }
});
