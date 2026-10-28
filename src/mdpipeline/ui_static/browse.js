/* The folder browser (MD-142): one dialog used by every folder field. Folder names only, from this computer, through the local server.
   A button with data-browse="<input id>" and data-browse-mode="source|output" opens it; picking sets the field and fires its `input`
   event, so each screen's own handling (check, preview, state) runs exactly as if the path had been typed. Typing stays as the fallback. */
"use strict";
let BR = null;                                    // { input, mode, data, hidden, opener, via, creating, note, loading, name }
const BR_TYPED_SEEN = new Set();

const brBrowseButton = (inputId, mode, label) =>
  `<button type="button" class="btn sm browse-btn" data-browse="${esc(inputId)}" data-browse-mode="${mode}" aria-label="Browse for ${esc(label)}">Browse…</button>`;

function brTrack(method) { track("folder_pick", { method }); }

async function brLoad(path, more) {
  BR.loading = true; brRender();
  const r = await api("POST", "/api/folder-browse", { path, show_hidden: BR.hidden, start: more ? BR.data.next_start : 0 });
  if (!BR) return;
  BR.loading = false;
  if (r.ok && r.data && r.data.ok) {
    const prev = more && BR.data ? BR.data.dirs : [];
    BR.data = { ...r.data, dirs: prev.concat(r.data.dirs) };
    BR.note = r.data.note || null;
  } else {
    BR.note = { kind: "other", message: "We couldn't read that folder list.", next: "Cancel, and type the path instead." };
  }
  brRender();
}

function brOpen(button) {
  const input = document.getElementById(button.dataset.browse);
  if (!input) return;
  BR = { input, mode: button.dataset.browseMode === "output" ? "output" : "source", data: null, hidden: false, opener: button, via: "browse", creating: false, note: null, loading: true, name: "" };
  brLoad(input.value || "", false);
}

function brClose(restore) {
  const opener = BR && BR.opener;
  BR = null;
  const el = document.getElementById("br-overlay");
  if (el) el.remove();
  if (restore !== false && opener && document.contains(opener)) opener.focus();
}

function brRender() {
  if (!BR) return;
  let overlay = document.getElementById("br-overlay");
  if (!overlay) { overlay = document.createElement("div"); overlay.id = "br-overlay"; overlay.className = "br-overlay"; document.body.appendChild(overlay); }
  const d = BR.data;
  const output = BR.mode === "output";
  const crumbs = d ? d.crumbs.map((c, i) => `<button type="button" class="link" data-br-go="${esc(c.path)}" ${i === d.crumbs.length - 1 ? 'aria-current="location"' : ""}>${esc(c.name)}</button>`).join('<span aria-hidden="true"> / </span>') : "";
  const shortcuts = d ? d.shortcuts.map((s) => `<li><button type="button" class="btn ghost sm" data-br-go="${esc(s.path)}" data-br-via="${s.id === "recent" ? "recent" : "browse"}" title="${esc(s.path)}">${esc(s.label)}</button></li>`).join("") : "";
  const dirs = d ? d.dirs.map((x) => x.readable
    ? `<li><button type="button" class="br-dir" data-br-go="${esc(x.path)}"><svg class="br-ico" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false"><path d="M1.5 3.5A1 1 0 0 1 2.5 2.5h3.2l1.4 1.5h6.4a1 1 0 0 1 1 1v7.5a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1z" fill="currentColor" opacity=".55"/></svg> ${esc(x.name)}</button></li>`
    : `<li><button type="button" class="br-dir" disabled aria-disabled="true"><svg class="br-ico" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false"><path d="M1.5 3.5A1 1 0 0 1 2.5 2.5h3.2l1.4 1.5h6.4a1 1 0 0 1 1 1v7.5a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1z" fill="currentColor" opacity=".55"/></svg> ${esc(x.name)} <span class="muted small">${esc(x.reason)}</span></button></li>`).join("") : "";
  const empty = d && !d.dirs.length ? `<li class="muted small">No folders in here. You can select this folder, or go up.</li>` : "";
  const note = BR.note ? `<div class="br-note msg warn" role="status">▲ <span><b>${esc(BR.note.message)}</b> ${esc(BR.note.next || "")}</span></div>` : "";
  const create = output ? (BR.creating
    ? `<div class="br-new"><label class="small" for="br-newname">New folder name</label><input type="text" id="br-newname" value="${esc(BR.name)}" spellcheck="false" autocomplete="off">
        <button type="button" class="btn sm primary" data-br="create">Create</button> <button type="button" class="btn sm" data-br="create-cancel">Cancel</button></div>`
    : `<button type="button" class="btn sm" data-br="new">New folder</button>`) : "";
  overlay.innerHTML = `<div class="br-dialog card" role="dialog" aria-modal="true" aria-labelledby="br-title" aria-busy="${BR.loading ? "true" : "false"}">
      <h2 id="br-title" tabindex="-1">Choose a folder</h2>
      <p class="muted small">${output ? "Where the Markdown files will be written." : "The folder to read from. We never change it."}</p>
      ${d ? `<nav aria-label="Path" class="br-crumbs">${crumbs}</nav><p class="br-path mono" id="br-path">${esc(d.path)}</p>` : `<p class="muted" role="status">Looking…</p>`}
      <div class="br-bar"><button type="button" class="btn sm" data-br-go="${d && d.parent ? esc(d.parent) : ""}" ${d && d.parent ? "" : "disabled"} aria-label="Up">↑ Up</button>
        <label class="small"><input type="checkbox" data-br="hidden" ${BR.hidden ? "checked" : ""}> Show hidden</label>${create}</div>
      ${shortcuts ? `<ul class="br-shortcuts" aria-label="Places">${shortcuts}</ul>` : ""}
      ${note}
      <ul class="br-list" aria-label="Folders">${dirs}${empty}</ul>
      ${d && !d.is_last ? `<button type="button" class="btn sm" data-br="more">Show more folders</button>` : ""}
      <div class="br-actions"><button type="button" class="btn" data-br="cancel">Cancel</button>
        <button type="button" class="btn primary" data-br="select" ${d ? "" : "disabled"}>Select this folder</button></div>
    </div>`;
  const focus = BR.focus; BR.focus = null;
  const target = focus ? overlay.querySelector(focus) : overlay.querySelector("#br-title");
  if (target) target.focus();
}

async function brCreate() {
  const name = (document.getElementById("br-newname") || {}).value || "";
  BR.name = name;
  const r = await api("POST", "/api/folder-browse/new", { parent: BR.data.path, name });
  if (!BR) return;
  if (!r.ok) { BR.note = { kind: "name", message: (r.data && r.data.error) || "Could not create that folder.", next: "" }; BR.focus = "#br-newname"; return brRender(); }
  BR.data.path = r.data.path;
  return brPick("browse");
}

function brPick(via) {
  const input = BR.input, path = BR.data.path;
  brTrack(via);
  input.value = path;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
  brClose(false);
  const live = document.getElementById(input.id) || input;
  if (live && document.contains(live)) live.focus();
}

document.addEventListener("click", (ev) => {
  const open = ev.target.closest && ev.target.closest("[data-browse]");
  if (open) { ev.preventDefault(); return brOpen(open); }
  if (!BR) return undefined;
  const go = ev.target.closest("[data-br-go]");
  if (go && go.dataset.brGo) { BR.via = go.dataset.brVia === "recent" ? "recent" : "browse"; BR.focus = go.classList.contains("br-dir") ? "#br-title" : null; return brLoad(go.dataset.brGo, false); }
  const act = ev.target.closest("[data-br]");
  if (!act) return undefined;
  switch (act.dataset.br) {
    case "cancel": return brClose(true);
    case "select": return brPick(BR.via);
    case "more": return brLoad(BR.data.path, true);
    case "new": BR.creating = true; BR.focus = "#br-newname"; return brRender();
    case "create-cancel": BR.creating = false; BR.note = null; return brRender();
    case "create": return brCreate();
    default: return undefined;
  }
});

document.addEventListener("change", (ev) => {
  if (BR && ev.target.dataset && ev.target.dataset.br === "hidden") { BR.hidden = ev.target.checked; BR.focus = '[data-br="hidden"]'; brLoad(BR.data.path, false); }
});

document.addEventListener("keydown", (ev) => {
  if (!BR) return;
  const overlay = document.getElementById("br-overlay");
  if (ev.key === "Escape") { ev.preventDefault(); return brClose(true); }
  if (ev.key === "Enter" && ev.target.id === "br-newname") { ev.preventDefault(); return brCreate(); }
  if (ev.key !== "Tab" || !overlay) return;
  const items = [...overlay.querySelectorAll("button:not([disabled]), input, [tabindex='-1']")].filter((e) => e.offsetParent !== null);
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1];
  if (!overlay.contains(document.activeElement)) { ev.preventDefault(); first.focus(); }
  else if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
  else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
});

// A path the person typed into a field that also has a Browse button: reported once per field (a method, never the path).
document.addEventListener("change", (ev) => {
  const el = ev.target;
  if (!ev.isTrusted || !el.id || !el.value || BR) return;
  if (!document.querySelector(`[data-browse="${CSS.escape(el.id)}"]`) || BR_TYPED_SEEN.has(el.id)) return;
  BR_TYPED_SEEN.add(el.id);
  brTrack("typed");
});
