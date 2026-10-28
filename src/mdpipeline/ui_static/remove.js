/* Markdown Pipeline: remove a pipeline from the app (MD-134). Loaded after app.js; shares its globals (S, api, esc, toast, ...).
   One confirmation with two choices: keep the files (the default) or delete the unedited files this pipeline created. The numbers come from
   the server before anything is deleted; the removal itself is the same code as `mdpipeline pipeline remove`. */

const RM = { name: "", pv: null, choice: "keep", busy: false, error: "" };

async function rmStart(name) {
  if (S.screen === "editor") await openView(name);
  RM.name = name; RM.pv = null; RM.choice = "keep"; RM.busy = false; RM.error = "";
  render();
  const r = await api("GET", `/api/pipelines/${encodeURIComponent(name)}/remove-preview`);
  if (RM.name !== name) return;
  if (r.ok) RM.pv = r.data; else RM.error = (r.data && r.data.error) || "We couldn't check what removing would do. Nothing was removed.";
  render();
  focusSoon("#remove-card input[name=rm-choice]:checked");
}

function rmClose() { RM.name = ""; RM.pv = null; RM.error = ""; render(); }

function rmFiles(n) { return `${n} ${n === 1 ? "file" : "files"}`; }

function rmCardHtml(name) {
  if (RM.name !== name) return "";
  const pv = RM.pv;
  if (!pv) return `<section class="card" id="remove-card" aria-label="Remove pipeline"><p class="muted">${RM.error ? "✕ " + esc(RM.error) : "Checking…"}</p>
    ${RM.error ? `<button class="btn ghost" data-rm="close">Close</button>` : ""}</section>`;
  const edited = pv.edited.length;
  const editedNote = edited ? `<p class="muted small" id="remove-edited">${rmFiles(edited)} you edited ${edited === 1 ? "is" : "are"} always kept: ${pv.edited.slice(0, 5).map((p) => `<code>${esc(p)}</code>`).join(", ")}${edited > 5 ? ` and ${edited - 5} more` : ""}.</p>` : "";
  const creds = [
    ...pv.credentials_removed.map((n) => `The saved sign-in “${esc(n)}” is removed from this computer.`),
    ...pv.credentials_kept.map((c) => `The saved sign-in “${esc(c.name)}” stays, because ${c.used_by.map(esc).join(", ")} still ${c.used_by.length === 1 ? "uses" : "use"} it.`),
  ];
  return `<section class="card" id="remove-card" role="group" aria-labelledby="remove-h">
    <h2 id="remove-h">Remove “${esc(name)}”?</h2>
    <p class="muted">The pipeline stops syncing now. Your source folders and accounts are not touched.</p>
    <fieldset class="rm-choices"><legend class="sr-only">What happens to the files</legend>
      <label class="rm-choice"><input type="radio" name="rm-choice" value="keep" data-rm="choice" ${RM.choice === "keep" ? "checked" : ""}>
        <span><strong>Keep the files</strong> (default)<br><span class="muted small">${rmFiles(pv.owned)} ${pv.owned === 1 ? "stays" : "stay"} where ${pv.owned === 1 ? "it is" : "they are"}. ${pv.owned === 1 ? "It becomes" : "They become"} yours. Nothing will update or delete ${pv.owned === 1 ? "it" : "them"} again.</span></span></label>
      <label class="rm-choice"><input type="radio" name="rm-choice" value="delete" data-rm="choice" ${RM.choice === "delete" ? "checked" : ""}>
        <span><strong>Delete the ${rmFiles(pv.deletable)} this pipeline created</strong><br><span class="muted small">Only files you did not edit. They are deleted for good.</span></span></label>
    </fieldset>
    ${editedNote}
    ${creds.length ? `<ul class="small" id="remove-creds">${creds.map((c) => `<li>${c}</li>`).join("")}</ul>` : ""}
    ${RM.error ? `<div class="why" role="alert">${esc(RM.error)}</div>` : ""}
    <div class="acts"><button class="btn${RM.choice === "delete" ? " danger" : ""}" data-rm="confirm" ${RM.busy ? "disabled" : ""}>${RM.busy ? "Removing…" : RM.choice === "delete" ? `Remove and delete ${rmFiles(pv.deletable)}` : "Remove pipeline"}</button>
      <button class="btn ghost" data-rm="close" ${RM.busy ? "disabled" : ""}>Cancel</button></div></section>`;
}

async function rmConfirm() {
  if (RM.busy || !RM.pv) return;
  const name = RM.name, del = RM.choice === "delete";
  RM.busy = true; RM.error = ""; render();
  const r = await api("POST", `/api/pipelines/${encodeURIComponent(name)}/remove`, del ? { delete_files: true, confirm_count: RM.pv.owned } : {});
  RM.busy = false;
  if (!r.ok) {
    RM.error = (r.data && r.data.error) || "We couldn't remove this pipeline. Nothing was changed.";
    if (r.status === 409) { RM.pv = null; rmStart(name); }          // the numbers changed under us: show the new ones before deleting
    else render();
    return;
  }
  const d = r.data;
  const credNote = d.credentials_removed.length ? ` The saved sign-in “${d.credentials_removed.join("”, “")}” was removed from this computer.` : "";
  RM.name = ""; RM.pv = null;
  await refresh();
  go("home");
  toast(d.message + credNote, 8000);
}

document.addEventListener("click", (ev) => {
  const el = ev.target.closest("[data-rm]");
  if (!el) return;
  switch (el.dataset.rm) {
    case "start": return rmStart(el.dataset.name);
    case "close": return rmClose();
    case "confirm": return rmConfirm();
    case "choice": RM.choice = el.value; return render();
    default: return undefined;
  }
});
