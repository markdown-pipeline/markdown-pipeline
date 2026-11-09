"""MD-216: no screen loses what the person is typing when it redraws. The guard from MD-215 AC8 (value, focus and cursor of a field the person changed
survive a redraw of the SAME screen) lives in the shared render, so it covers Home's "Needs you", the other polled screens and the editor's target box.
Every test forces the redraw (deterministic, no waiting for the 2 s poll)."""
import shutil

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from mdpipeline.config import Pipeline, Source  # noqa: E402
from mdpipeline.engine import sync_pipeline  # noqa: E402
from test_ui_browser import browser, open_activity, open_app, page  # noqa: E402,F401
from test_ui_browser_targets import TOKEN, fake, new_github_pipeline  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402

FOCUS = "(() => { const a = document.activeElement; return { id: a.id, start: a.selectionStart, end: a.selectionEnd, value: a.value }; })()"


@pytest.fixture
def gone(brain, add_pipeline, make_source):
    a = make_source("A", {"a1.md": "# a1\n"})
    add_pipeline(Pipeline.new("p", [Source(str(a), "A")], target="{relpath}", name_policy="original"))
    sync_pipeline(brain, "p")
    shutil.rmtree(a)
    sync_pipeline(brain, "p")


def open_choose(page, ui, screen):
    """The moved-folder form on a screen the poll redraws: the pipeline's view, Activity's "Needs you" or Home's."""
    open_app(page, ui)
    if screen == "view":
        page.evaluate("location.hash = '#/pipeline/p'")
    elif screen == "activity":
        open_activity(page)
    page.get_by_role("button", name="Choose new location").first.click()


@pytest.mark.parametrize("screen", ["view", "activity"])
def test_the_moved_folder_field_keeps_value_focus_and_cursor_over_a_redraw(page, brain, gone, screen):
    with running_ui(brain) as ui:
        open_choose(page, ui, screen)
        field = page.locator("#msrc-path")
        field.fill("")
        field.press_sequentially("/moved/place")
        page.evaluate("() => { const f = document.getElementById('msrc-path'); f.setSelectionRange(3, 3); }")
        page.evaluate("render()")                                                  # the poll's redraw, forced
        assert page.evaluate(FOCUS) == {"id": "msrc-path", "start": 3, "end": 3, "value": "/moved/place"}
        page.keyboard.type("X")                                                    # and typing still lands where the cursor was
        assert page.locator("#msrc-path").input_value() == "/moXved/place"


@pytest.mark.parametrize("screen", ["view", "activity"])
def test_a_redraw_with_a_conflicting_state_value_does_not_replace_what_was_typed(page, brain, gone, screen):
    with running_ui(brain) as ui:
        open_choose(page, ui, screen)
        page.locator("#msrc-path").fill("/typed/by/the/person")
        page.evaluate("() => { document.getElementById('msrc-path'); S.msrc.path = '/from/somewhere/else'; render(); }")
        assert page.locator("#msrc-path").input_value() == "/typed/by/the/person"


def test_a_redraw_of_another_screen_still_draws_that_screen_from_its_own_state(page, brain, gone):
    """The guard is for the SAME screen only: going to Settings and back never carries a typed value over."""
    with running_ui(brain) as ui:
        open_choose(page, ui, "view")
        page.locator("#msrc-path").fill("/typed")
        page.get_by_role("button", name="Settings").click()
        expect(page.locator("#bg-settings")).to_be_visible()
        assert page.locator("#msrc-path").count() == 0


def test_the_editor_target_box_keeps_a_typed_token_value_and_focus_over_a_redraw_and_copies_it_nowhere(page, brain, fake, make_source, app_data):
    src = make_source("docs", {"a.md": "A"})
    requests = []
    page.on("request", lambda r: requests.append(r))
    with running_ui(brain) as ui:
        new_github_pipeline(page, ui, src)
        page.locator("#d-token").fill(TOKEN)
        copies = "(t) => JSON.stringify([S, localStorage, sessionStorage, document.cookie]).split(t).length - 1"
        before, mark = page.evaluate(copies, TOKEN), len(requests)                 # the typed value is held in the editor state by the input handler, by design
        page.evaluate("renderDest(); renderDest()")                                # forced, twice
        assert page.evaluate(FOCUS)["id"] == "d-token" and page.locator("#d-token").input_value() == TOKEN
        assert page.evaluate(copies, TOKEN) == before, "the redraw added a copy of the token to page state or storage"
        assert not [r for r in requests[mark:] if TOKEN in (r.post_data or "") or TOKEN in r.url], "the token was sent by the redraw"


def test_activity_keeps_a_typed_vault_passphrase_and_focus_over_a_redraw_without_copying_it(page, brain, fake, make_source, app_data):
    from mdpipeline.config import load_config, save_config
    from mdpipeline.targetconfig import GitHubSpec
    from mdpipeline.targetsetup import store_token

    spec, _ = store_token(GitHubSpec("acme/brain", "x", "main"), TOKEN)
    cfg = load_config(brain)
    cfg.pipelines.append(Pipeline.new("p", [str(make_source("d", {"a.md": "A"}))], destination=spec.to_dict()))
    save_config(brain, cfg)
    app_data.keychain.lose()
    sync_pipeline(brain, "p", trigger="ui")
    secret = "correct horse battery staple"
    with running_ui(brain) as ui:
        open_app(page, ui, "activity")
        page.locator("#vp").fill(secret)
        copies = "(t) => JSON.stringify([S, localStorage, sessionStorage, document.cookie]).split(t).length - 1"
        before = page.evaluate(copies, secret)
        page.evaluate("render(); render()")
        assert page.evaluate(FOCUS)["id"] == "vp" and page.locator("#vp").input_value() == secret
        assert page.evaluate(copies, secret) == before


# --- a field removed by one redraw and re-created by a later one gets its value and focus back, once ---------------------------------------------------
PROBE = """
(secret) => {
  const root = document.createElement('div'); root.id = 'probe'; document.body.appendChild(root);
  const draw = (withField) => { const t = captureTyped(root); root.innerHTML = withField ? '<input id="x" type="password">' : '<p>counting</p>'; restoreTyped(root, t); };
  draw(true); const i = root.querySelector('#x'); i.value = secret; i.focus(); i.setSelectionRange(2, 2);
  draw(false);                                                                  // the field is gone
  const waiting = (orphanTyped.probe || []).length;
  draw(true);                                                                   // and back
  const x = root.querySelector('#x');
  const out = { waiting, value: x.value, focused: document.activeElement === x, cursor: x.selectionStart, left: (orphanTyped.probe || []).length,
                copies: JSON.stringify([S, localStorage, sessionStorage, document.cookie]).split(secret).length - 1 };
  root.remove(); delete orphanTyped.probe; return out;
}
"""


def test_a_typed_field_removed_by_a_redraw_and_re_created_later_gets_value_focus_and_cursor_back_once(page, brain, gone):
    with running_ui(brain) as ui:
        open_app(page, ui)
        out = page.evaluate(PROBE, "hunter2-correct-horse")
    assert out == {"waiting": 1, "value": "hunter2-correct-horse", "focused": True, "cursor": 2, "left": 0, "copies": 0}


def test_what_waits_for_a_re_creation_is_forgotten_when_the_screen_changes(page, brain, gone):
    with running_ui(brain) as ui:
        open_app(page, ui)
        left = page.evaluate("""() => { const root = document.createElement('div'); root.id = 'probe2'; document.body.appendChild(root);
          root.innerHTML = '<input id="y">'; const i = root.firstChild; i.value = 'typed'; i.focus();
          const t = captureTyped(root); root.innerHTML = ''; restoreTyped(root, t); const before = orphanTyped.probe2.length;
          forgetTyped(root); const after = (orphanTyped.probe2 || []).length; root.remove(); return [before, after]; }""")
    assert left == [1, 0]
