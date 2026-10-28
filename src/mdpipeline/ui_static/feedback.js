/* Markdown Pipeline: request a source and one question at three moments (MD-123). Loaded after app.js and firstrun.js.
   Everything here is sent only when the person presses a Send button, even with usage sharing off, and is labelled "goes to the team".
   Typing and choosing send nothing. Copy follows work/designer/first-run/wireframes.md (Start: request chips, Home: one question). */

const FB = { pick: "", other: "", email: "", sent: "", busy: false, error: "", prompt: null, picked: "", comment: "", thanks: false, checked: false, dcSent: false };
const FB_TOOLS = [["confluence", "Confluence"], ["notion", "Notion"], ["google_drive", "Google Drive"], ["github_issues", "GitHub Issues"],
  ["linear", "Linear"], ["slack", "Slack"], ["sharepoint", "SharePoint"], ["other", "Something else"]];
const fbName = (id) => (FB_TOOLS.find((t) => t[0] === id) || [id, id])[1];
const fbErr = (id, msg) => (msg ? `<div class="vneed err" id="${id}" role="alert"><span aria-hidden="true">✕</span> ${esc(msg)}</div>` : "");

function fbRerender() {
  if (S.screen === "home" || (S.screen === "firstrun" && FR && FR.step === "start")) render();
}

/* ---------------------------------------------------------------- request a source (AC1) */
function fbRequestBlock() {
  const chips = FB_TOOLS.map(([id, name]) => `<button type="button" class="chip" data-fb="pick" data-id="${id}" aria-pressed="${FB.pick === id}">${name}</button>`).join("");
  return `<h2 id="fb-chips-h">Use another tool? Tell us which</h2>
    <div class="fb-chips" role="group" aria-labelledby="fb-chips-h">${chips}</div>${FB.pick ? fbPanel() : ""}`;
}

function fbPanel() {
  const name = fbName(FB.pick), other = FB.pick === "other";
  if (FB.sent === FB.pick) {
    return `<div class="card fb-panel" id="fb-request" role="status"><p>✓ Request ${other ? "" : `for ${esc(name)} `}sent. We count requests to choose what to build next.
      We can't promise which tools or when. Meanwhile you can start with Jira or a folder.</p></div>`;
  }
  return `<form class="card fb-panel" id="fb-request" data-fb-form="request" novalidate>
    <p>${other ? "Request <b>another tool</b>." : `Request <b>${esc(name)}</b>.`} This sends one request to the team, even if you don't share usage.
      <span class="dtag">goes to the team</span></p>
    ${other ? `<label class="tf"><span>Which tool?</span><input type="text" data-fb-bind="other" value="${esc(FB.other)}" maxlength="100" autocomplete="off"></label>` : ""}
    <label class="tf"><span>Email me if we add it (optional)</span><input type="text" inputmode="email" data-fb-bind="email" value="${esc(FB.email)}" autocomplete="email"></label>
    ${fbErr("fb-error", FB.error)}
    <p><button type="submit" class="btn primary" ${FB.busy ? "disabled" : ""}>Send request</button></p></form>`;
}

async function fbSendRequest(sourceId, other, email) {
  return api("POST", "/api/source-request", { source_id: sourceId, other_source: other || "", email: email || "" });
}

async function fbSubmitRequest() {
  FB.busy = true; FB.error = ""; fbRerender();
  const r = await fbSendRequest(FB.pick, FB.other, FB.email);
  FB.busy = false;
  const d = r.data || {};
  if (r.ok && d.ok) { FB.sent = FB.pick; FB.email = ""; FB.other = ""; }
  else FB.error = d.error || "We couldn't send this right now. Check your connection and try again.";
  fbRerender();
  focusSoon(FB.error ? "#fb-error" : "#fb-request");
}

/* the "Jira Data Center or Server" error on the Connect step offers the same request in one click */
function fbDataCenterButton() {
  if (FB.dcSent) return ` <span>✓ Request sent. We can't promise which tools or when.</span>`;
  return ` <button type="button" class="btn sm" data-fb="request-dc">Request Jira Data Center</button>`;
}

async function fbRequestDataCenter() {
  const r = await fbSendRequest("jira_data_center", "", "");
  if (r.ok && r.data && r.data.ok) FB.dcSent = true;
  else toast((r.data && r.data.error) || "We couldn't send this right now.");
  if (FR) renderFirstRun();
}

/* ---------------------------------------------------------------- one question (AC2, AC3) */
function fbEnsure() {                                       // once per page load; the server marks the prompt as shown
  if (FB.checked) return;
  FB.checked = true;
  api("GET", "/api/prompt").then((r) => { if (r.ok && r.data.prompt) { FB.prompt = r.data.prompt; fbRerender(); } });
}

/* MD-172: the card is on screen. Reported once per question per page load, however often the screen redraws (choosing, typing, other
   screens and back). Only the question id travels, and only with usage sharing on (track() checks it; the server checks it again). */
const fbShown = new Set();
function fbNoteShown(questionId) {
  if (!fbShown.has(questionId)) { fbShown.add(questionId); track("feedback_shown", { question_id: questionId }); }
}

function fbPrompt() {
  fbEnsure();
  if (FB.thanks) return `<div class="banner ok" id="fb-thanks" role="status">✓ Thanks. Your answer went to the team.</div>`;
  const p = FB.prompt;
  if (!p) return "";
  fbNoteShown(p.question_id);
  const options = p.options.map((o) => `<button type="button" class="btn sm" data-fb="answer" data-answer="${esc(o.answer)}" aria-pressed="${FB.picked === o.answer}">${esc(o.label)}</button>`).join(" ");
  return `<section class="card fb-prompt" id="fb-prompt" aria-labelledby="fb-q"><div class="small muted">ONE QUESTION</div>
    <h2 id="fb-q">${esc(p.text)}</h2><div role="group" aria-labelledby="fb-q">${options}</div>
    <label class="tf"><span>${esc(p.comment_label)} <span class="dtag">goes to the team</span></span>
      <textarea data-fb-bind="comment" rows="2" maxlength="500">${esc(FB.comment)}</textarea></label>
    ${fbErr("fb-prompt-error", FB.error)}
    <p><button type="button" class="btn primary" data-fb="send-answer" ${FB.picked && !FB.busy ? "" : "disabled"}>Send</button>
      <button type="button" class="btn ghost" data-fb="dismiss">Dismiss</button></p></section>`;
}

async function fbSendAnswer() {
  const p = FB.prompt;
  if (!p || !FB.picked) return;
  FB.busy = true; FB.error = ""; fbRerender();
  const r = await api("POST", "/api/prompt/answer", { question_id: p.question_id, answer: FB.picked, comment: FB.comment });
  FB.busy = false;
  const d = r.data || {};
  if (r.ok && d.ok) { FB.prompt = null; FB.picked = ""; FB.comment = ""; FB.thanks = true; }
  else FB.error = d.error || "We couldn't send this right now. Check your connection and try again.";
  fbRerender();
}

async function fbDismiss() {
  const p = FB.prompt;
  FB.prompt = null; FB.picked = ""; FB.comment = ""; FB.error = "";
  fbRerender();
  if (p) api("POST", "/api/prompt/dismiss", { question_id: p.question_id });
}

/* ---------------------------------------------------------------- events */
document.addEventListener("click", (ev) => {
  if (FB.thanks && ev.target.closest("[data-fr],[data-act]")) FB.thanks = false;          // the thank-you lasts until the person moves on
  const el = ev.target.closest("[data-fb]");
  if (!el) return;
  switch (el.dataset.fb) {
    case "pick": FB.pick = FB.pick === el.dataset.id ? "" : el.dataset.id; FB.error = ""; fbRerender(); return focusSoon("#fb-request input");
    case "request-dc": return fbRequestDataCenter();
    case "answer": FB.picked = el.dataset.answer; fbRerender(); return undefined;
    case "send-answer": return fbSendAnswer();
    case "dismiss": return fbDismiss();
    default: return undefined;
  }
}, true);

document.addEventListener("submit", (ev) => {
  if (ev.target.closest("[data-fb-form=request]")) { ev.preventDefault(); fbSubmitRequest(); }
});

document.addEventListener("input", (ev) => {
  const bind = ev.target.dataset && ev.target.dataset.fbBind;
  if (bind) FB[bind] = ev.target.value;                       // typed words stay on the page until Send
});
