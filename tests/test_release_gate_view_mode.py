"""MD-96: the release gate of pipeline view mode (PV-S5): read-only, GET-only, secret-free, and honest about failures. Run alone with:

    uv run pytest tests/test_release_gate_view_mode.py

Everything runs against the fake GitHub, the fake Jira and a fake keychain: no real network. Together with tests/test_data_loss.py (MD-30),
tests/test_data_loss_names.py (MD-41), tests/test_data_loss_github.py (MD-69) and tests/test_release_gate_p31.py (MD-89) this is the gate.
Acceptance criteria numbers match MD-96.

The one thing a read may change is the vault's per-secret "last used" date (at most once a day, metadata only, never a value): the home page
and every plan already do that. The gate checks that no secret VALUE and no secret entry changes.
"""
import json
import re
import urllib.request

import pytest

from conftest import tree_snapshot
from fake_github import FakeGitHub
from fake_jira import FakeJira
from mdpipeline import github_api, github_signin as gs, jira_api, vault as vault_mod
from mdpipeline.cli import main
from mdpipeline.config import Pipeline, Source, load_config, save_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.service import set_paused
from mdpipeline.targetconfig import GitHubSpec
from secret_search import files_containing, repo_containing
from test_jira_sync import EMAIL, SITE
from uihelper import running_ui

PAT = "ghp_VIEWGATEPATSECRET0123456789"
JIRA_TOKEN = "ATATT-VIEWGATE-JIRA-SECRET"
CLIENT_ID = "Iv1.viewgate"
PIPELINES = ("dir_folder", "dir_jira", "gh_folder", "gh_jira")


@pytest.fixture
def world(brain, tmp_path, make_source, app_data, monkeypatch):
    """Four synced pipelines: {directory, GitHub} x {folder, Jira}. Credentials live in the vault (PAT, sign-in tokens, Jira email and token)."""
    gh = FakeGitHub(repo="acme/brain", branch="main", token=PAT, files={"wiki/index.md": b"curated"})
    gh.enable_app(client_id=CLIENT_ID, slug="view-gate")
    gh.install_app(["acme/brain"])
    github_api.set_transport(gh)
    gs.save_app_settings(CLIENT_ID, "view-gate")
    jira = FakeJira(site="acme", email=EMAIL, token=JIRA_TOKEN)
    for n in range(4):
        jira.add_issue("MD", f"issue {n}")
    jira_api.set_transport(jira, sleep=lambda s: None)
    vault = vault_mod.default_vault()
    pat_id = vault.add("github-token", "view gate pat", PAT)
    code = gs.request_device_code(CLIENT_ID)
    gh.authorize_device()
    tokens = gs.wait_for_tokens(CLIENT_ID, code, sleep=lambda s: None)
    tokens.login = "tester"
    si_id = vault.add("github-sign-in", "view gate sign-in", tokens.to_json())
    email_id, token_id = vault.add("jira-email", "view gate jira email", EMAIL), vault.add("jira-token", "view gate jira token", JIRA_TOKEN)
    pat_dest = GitHubSpec("acme/brain", f"vault:{pat_id}", "main").to_dict()
    gh._refs["signin"] = gh.head()                  # pipelines with the same repo and branch share ONE target (and its credential): give the sign-in one its own branch
    si_dest = GitHubSpec("acme/brain", f"vault:{si_id}", "signin", auth="sign-in").to_dict() | {"auth": "sign-in"}
    jira_src = lambda: Source(SITE, "jira", type="jira", options={"site": SITE, "jql": "project = MD", "email_ref": f"vault:{email_id}", "token_ref": f"vault:{token_id}"})  # noqa: E731
    folder_src = lambda name: make_source(name + "src", {f"{i}.md": f"{name} {i}" for i in range(6)})  # noqa: E731
    cfg = load_config(brain)
    cfg.pipelines += [
        Pipeline.new("dir_folder", [Source(str(folder_src("dir_folder")), "docs")], name_policy="original"),
        Pipeline("dir_jira", [jira_src()], name_policy="date-name", guard_threshold_percent=100),
        Pipeline.new("gh_folder", [Source(str(folder_src("gh_folder")), "docs")], destination=pat_dest, name_policy="original"),
        Pipeline("gh_jira", [jira_src()], destination=si_dest, name_policy="date-name", guard_threshold_percent=100),
    ]
    save_config(brain, cfg)
    for name in PIPELINES:
        assert sync_pipeline(brain, name, trigger="cli").result == "ok", name
    w = type("World", (), {"brain": brain, "gh": gh, "jira": jira, "tokens": tokens, "pat_id": pat_id, "si_id": si_id, "token_id": token_id, "tmp": tmp_path,
                           "keychain": app_data.keychain, "app_dir": app_data.dir, "sources": tmp_path / "sources"})
    yield w
    github_api.set_transport(None)
    jira_api.set_transport(None)


def secret_values(w) -> list[str]:
    stored = gs.SignInTokens.from_json(vault_mod.default_vault().get(w.si_id))
    return [PAT, JIRA_TOKEN, EMAIL, stored.access_token, stored.refresh_token]


def vault_entries() -> dict:
    v = vault_mod.default_vault()
    return {s.id: (s.name, s.kind, v.get(s.id)) for s in v.list()}


def read_only_calls(w) -> bool:
    """GitHub: only GET. Jira: only GET plus the allow-listed approximate-count read."""
    return ({m for m, _ in w.gh.requests} <= {"GET"}
            and [r for r in w.jira.requests if r[0] != "GET" and not r[1].endswith("approximate-count")] == [])


def cli_text(capsys, brain, *argv):
    code = main(["--brain", str(brain.root), *argv])
    out = capsys.readouterr()
    return code, out.out + out.err


# AC1 -------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", PIPELINES)
def test_01_opening_and_refreshing_view_mode_writes_nothing_and_only_reads(world, name):
    if name.endswith("folder"):
        (world.sources / f"{name}src" / "extra.md").write_text("a pending change the plan will show")
    roots = [world.brain.root, world.sources]
    before = tree_snapshot(*roots)
    repo_before = (world.gh.head(), world.gh.history(), dict(world.gh.files()))
    entries_before = vault_entries()
    world.gh.requests.clear()
    world.jira.requests.clear()
    with running_ui(world.brain) as ui:
        for _ in range(3):                                           # open + two Refresh
            assert ui.get(f"/api/pipelines/{name}/view")[0] == 200
            assert ui.get(f"/api/pipelines/{name}/plan")[0] == 200
            assert ui.get(f"/api/activity?pipeline={name}")[0] == 200
            assert ui.get("/api/state")[0] == 200
    assert tree_snapshot(*roots) == before                           # config, state, log, backups, target files, sources: byte-identical
    assert (world.gh.head(), world.gh.history(), dict(world.gh.files())) == repo_before       # 0 commits, repository byte-identical
    assert vault_entries() == entries_before                         # no secret added, removed or changed
    assert read_only_calls(world)
    assert world.gh.requests or world.jira.requests or name.startswith("dir_folder")          # the check really went through the fakes


def test_01b_the_view_routes_have_no_write_methods(world):
    with running_ui(world.brain) as ui:
        for path in ("/api/pipelines/dir_folder/view", "/api/pipelines/dir_folder/plan"):
            for method in ("POST", "PUT", "DELETE"):
                assert ui.request(method, path, {})[0] == 404
        assert ui.get("/api/pipelines/dir_folder/view", token=False)[0] == 200       # reads need no session token, writes do
        assert ui.request("POST", "/api/pipelines/dir_folder/sync", {}, token=False)[0] == 403


def test_01c_pipeline_show_writes_nothing_and_only_reads(world, capsys):
    before = tree_snapshot(world.brain.root, world.sources)
    repo_before = (world.gh.head(), world.gh.history())
    world.gh.requests.clear()
    world.jira.requests.clear()
    for name in PIPELINES:
        for flags in ((), ("--json",)):
            assert cli_text(capsys, world.brain, "pipeline", "show", name, *flags)[0] == 0
    assert tree_snapshot(world.brain.root, world.sources) == before and (world.gh.head(), world.gh.history()) == repo_before and read_only_calls(world)


# AC2 -------------------------------------------------------------------------------------------------------------------
def everything_view_mode_shows(w, capsys) -> str:
    seen = []
    with running_ui(w.brain) as ui:
        for path in ("/", "/app.js", "/app.css", "/api/state", "/api/vault"):
            req = urllib.request.Request(ui.base + path.lstrip("/"))
            with urllib.request.urlopen(req) as resp:
                seen.append(resp.read().decode())
        for name in PIPELINES:
            for kind in ("view", "plan"):
                seen.append(json.dumps(ui.get(f"/api/pipelines/{name}/{kind}")[1]))
            seen.append(json.dumps(ui.get(f"/api/activity?pipeline={name}")[1]))
            seen.append(json.dumps(ui.get(f"/api/pipelines/{name}")[1]))
    for name in PIPELINES:
        for flags in ((), ("--json",)):
            seen.append(cli_text(capsys, w.brain, "pipeline", "show", name, *flags)[1])
    return "\n".join(seen)


def test_02_secret_search_over_html_api_responses_and_pipeline_show_finds_nothing(world, capsys):
    secrets = secret_values(world)
    out = everything_view_mode_shows(world, capsys)
    assert "gh_jira" in out and "acme/brain" in out and "view gate pat" in out and len(out) > 20000      # it searched real output, which names the vault entries
    for secret in secrets:
        assert secret not in out, secret[:8]


def test_02b_secret_search_also_holds_after_failures_and_over_files(world, capsys):
    secrets = secret_values(world)
    world.keychain.lose()                                            # locked vault: its error paths print no secret either
    out = everything_view_mode_shows(world, capsys)
    world.keychain.key = None
    for secret in secrets:
        assert secret not in out, secret[:8]
        assert files_containing(secret, world.brain.root, world.sources, world.app_dir) == []
        assert repo_containing(secret, world.gh) == []


# AC3 + AC4 ---------------------------------------------------------------------------------------------------------------
def break_it(w, how, monkeypatch):
    """Put one pipeline into a failure state; returns (pipeline name, expected problem kind, expected category or None)."""
    if how == "folder-moved":
        (w.sources / "dir_foldersrc").rename(w.tmp / "moved")
        return "dir_folder", "source_missing", None   # MD-136: a missing folder is a calm notice, not an error
    if how == "github-auth-failed":
        vault_mod.default_vault().replace(w.pat_id, "ghp_WRONGTOKEN0000000")
        return "gh_folder", "target", "auth"
    if how == "vault-locked":
        w.keychain.lose()
        return "gh_folder", "target", "vault-locked"
    if how == "sign-in-expired":
        w.gh.revoke_tokens()
        stored = gs.SignInTokens.from_json(vault_mod.default_vault().get(w.si_id))
        stored.expires_at = 1.0
        vault_mod.default_vault().replace(w.si_id, stored.to_json())
        return "gh_jira", "target", "sign-in-expired"
    if how == "jira-auth-failed":
        vault_mod.default_vault().replace(w.token_id, "ATATT-WRONG-TOKEN-VALUE")
        return "dir_jira", "source", "auth"
    raise AssertionError(how)


FAILURES = ["folder-moved", "github-auth-failed", "vault-locked", "sign-in-expired", "jira-auth-failed"]


@pytest.mark.parametrize("how", FAILURES)
def test_03_a_failure_is_shown_as_a_problem_and_the_plan_deletes_nothing(world, how, monkeypatch, capsys):
    name, kind, category = break_it(world, how, monkeypatch)
    owned_before = {p: len(r) for p, r in [(n, load_state_files(world, n)) for n in PIPELINES]} if how != "vault-locked" else None
    with running_ui(world.brain) as ui:
        status, plan = ui.get(f"/api/pipelines/{name}/plan")
        vstatus, page = ui.get(f"/api/pipelines/{name}/view")
    assert status == 200 and vstatus == 200
    [problem] = [p for p in plan["problems"] if p["kind"] == kind]
    assert problem["title"] and (problem["next_step"] or kind == "source_missing") and (category is None or problem["category"] == category)
    assert plan["preview"]["counts"]["delete"] == 0
    assert all(i["op"] != "delete" for g in plan["preview"]["groups"] for i in g["items"])
    assert not (plan["preview"]["guard"] or {}).get("blocked")
    assert page["config"]["name"] == name                                       # the page itself still opens
    code, text = cli_text(capsys, world.brain, "pipeline", "show", name)
    assert code == 0 and name in text
    if owned_before is not None:
        assert {n: len(load_state_files(world, n)) for n in PIPELINES} == owned_before         # nothing it owns was dropped by looking


def load_state_files(world, name):
    from mdpipeline.destinations import bind_target
    from mdpipeline.state import State

    p = load_config(world.brain).get(name)
    return State.load(bind_target(world.brain, p)).pipelines[name].files


def test_03b_a_failed_sync_followed_by_looking_still_shows_no_deletes(world):
    (world.sources / "dir_foldersrc").rename(world.tmp / "moved")
    assert sync_pipeline(world.brain, "dir_folder", trigger="mcp").result == "error"
    with running_ui(world.brain) as ui:
        _, page = ui.get("/api/pipelines/dir_folder/view")
        _, plan = ui.get("/api/pipelines/dir_folder/plan")
    assert page["status"]["status"] == "error" and [c["kind"] for c in page["needs_you"]] == ["source_missing"]
    assert plan["preview"]["counts"]["delete"] == 0 and (world.brain.root / "raw/dir_folder/docs/0.md").exists()


def test_04_paused_never_synced_error_and_guard_states(world, capsys):
    set_paused(world.brain, "dir_folder", True, trigger="cli")
    src = world.sources / "dir_foldersrc"
    (src / "new.md").write_text("n")
    cfg = load_config(world.brain)
    cfg.pipelines.append(Pipeline.new("fresh", [Source(str(world.tmp / "fresh"), "f")]))
    (world.tmp / "fresh").mkdir()
    (world.tmp / "fresh" / "a.md").write_text("a")
    save_config(world.brain, cfg)
    with running_ui(world.brain) as ui:
        paused = {"view": ui.get("/api/pipelines/dir_folder/view")[1], "plan": ui.get("/api/pipelines/dir_folder/plan")[1]}
        never = {"view": ui.get("/api/pipelines/fresh/view")[1], "plan": ui.get("/api/pipelines/fresh/plan")[1]}
    assert paused["view"]["status"]["status"] == "paused" and paused["plan"]["preview"]["counts"]["create"] == 1
    assert never["view"]["status"]["status"] == "never" and never["view"]["runs"] == [] and never["plan"]["preview"]["counts"]["create"] == 1
    assert never["plan"]["preview"]["counts"]["delete"] == 0
    assert "[paused]" in cli_text(capsys, world.brain, "pipeline", "show", "dir_folder")[1] and "[never]" in cli_text(capsys, world.brain, "pipeline", "show", "fresh")[1]


def test_04b_a_guard_that_would_trip_is_predicted_and_a_blocked_one_points_to_needs_you(world):
    src = world.sources / "dir_foldersrc"
    for i in range(5):
        (src / f"{i}.md").unlink()
    with running_ui(world.brain) as ui:
        predicted = ui.get("/api/pipelines/dir_folder/plan")[1]
    assert predicted["preview"]["guard"]["blocked"] is True and predicted["preview"]["counts"]["delete"] == 5
    assert sync_pipeline(world.brain, "dir_folder", trigger="mcp").result == "blocked"
    before = tree_snapshot(world.brain.root, world.sources)
    with running_ui(world.brain) as ui:
        _, page = ui.get("/api/pipelines/dir_folder/view")
    assert page["status"]["status"] == "blocked" and page["status"]["pending_approval"] is True and [c["kind"] for c in page["needs_you"]] == ["guard"]
    assert tree_snapshot(world.brain.root, world.sources) == before and (world.brain.root / "raw/dir_folder/docs/0.md").exists()


# the same, in a real browser ---------------------------------------------------------------------------------------------
playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from test_ui_browser import browser, open_app, page  # noqa: E402,F401


def test_05_the_rendered_page_never_contains_a_secret_and_shows_no_input(world, page):
    secrets = secret_values(world)
    with running_ui(world.brain) as ui:
        open_app(page, ui)
        for name in PIPELINES:
            page.locator("li.prow", has_text=name).get_by_role("link", name=name).click()
            expect(page.locator("#view-plan table, #view-plan .pempty").first).to_be_visible()
            expect(page.locator("#view input, #view textarea, #view select")).to_have_count(0)
            html = page.content()
            for secret in secrets:
                assert secret not in html, (name, secret[:8])
            page.get_by_role("button", name="Back to home").click()


@pytest.mark.parametrize("how,problem", [("folder-moved", "source_missing"), ("github-auth-failed", "target"), ("sign-in-expired", "target"), ("jira-auth-failed", "source")])
def test_06_the_browser_shows_the_problem_and_no_delete_rows(world, page, how, problem, monkeypatch):
    name, kind, _ = break_it(world, how, monkeypatch)
    with running_ui(world.brain) as ui:
        open_app(page, ui)
        page.locator("li.prow", has_text=name).get_by_role("link", name=name).click()
        expect(page.locator(f'#view-plan [data-problem="{problem}"]')).to_be_visible()
        expect(page.locator("#view-plan .op.delete")).to_have_count(0)
        for secret in ("ghp_WRONGTOKEN", "ATATT-WRONG", PAT, JIRA_TOKEN):
            assert secret not in page.content()
