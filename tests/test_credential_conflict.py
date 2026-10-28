"""MD-97: two pipelines on the same GitHub repo + branch never silently share a credential one of them did not choose."""
import json

import pytest

from fake_github import FakeGitHub
from mdpipeline import github_api
from mdpipeline.config import Pipeline, load_config, save_config
from mdpipeline.validation import validate_pipeline


def dest(credential: str, branch: str = "main", repo: str = "acme/brain") -> dict:
    return {"type": "github", "repo": repo, "branch": branch, "credential": credential, "auth": "token"}


def pipeline(name: str, src, credential: str, **kw) -> Pipeline:
    return Pipeline.new(name, [str(src)], destination=dest(credential, **kw))


@pytest.fixture
def two(brain, make_source):
    """Saved pipeline 'first' on acme/brain@main with vault:aaa; returns a helper that builds a second one."""
    save_config(brain, load_config(brain).__class__([pipeline("first", make_source("s1", {"a.md": "A"}), "vault:aaa")]))
    config = load_config(brain)
    return brain, config, make_source("s2", {"b.md": "B"})


def test_a_second_pipeline_on_the_same_repo_and_branch_with_another_credential_is_refused(two):
    brain, config, src = two
    res = validate_pipeline(brain, config, pipeline("second", src, "vault:bbb"))
    [issue] = [i for i in res.errors if i.code == "credential-conflict"]
    assert "'first'" in issue.message and "same credential" in issue.message and "another branch" in issue.message
    assert issue.field == "destination.credential"


def test_the_same_credential_on_the_same_repo_and_branch_is_allowed(two):
    brain, config, src = two
    assert not [i for i in validate_pipeline(brain, config, pipeline("second", src, "vault:aaa")).errors if i.code == "credential-conflict"]


def test_another_branch_or_another_repo_with_another_credential_is_allowed(two):
    brain, config, src = two
    for kw in ({"branch": "other"}, {"repo": "acme/other"}):
        res = validate_pipeline(brain, config, pipeline("second", src, "vault:bbb", **kw))
        assert not [i for i in res.errors if i.code == "credential-conflict"]


def test_editing_the_only_pipeline_never_conflicts_with_itself(two):
    brain, config, _ = two
    res = validate_pipeline(brain, config, pipeline("first", config.get("first").sources[0].path, "vault:bbb"), editing="first")
    assert not [i for i in res.errors if i.code == "credential-conflict"]


# --- an existing config that already conflicts -----------------------------------------------------------------------------
from conftest import tree_snapshot
from mdpipeline.cli import main
from mdpipeline.engine import sync_pipeline
from mdpipeline.status import brain_status
from mdpipeline.uiactivity import activity_view
from mdpipeline import vault as vault_mod
from uihelper import running_ui

PAT_A, PAT_B = "ghp_AAAAAAAASECRET0123456789", "ghp_BBBBBBBBSECRET0123456789"


@pytest.fixture
def conflicting(brain, make_source, app_data):
    """Two pipelines saved (bypassing validation, as an older version allowed) on one repo + branch, each naming its own vault credential."""
    gh = FakeGitHub(repo="acme/brain", branch="main", token=PAT_A, files={"wiki/w.md": b"W"})
    github_api.set_transport(gh)
    vault = vault_mod.default_vault()
    a, b = vault.add("github-token", "token A", PAT_A), vault.add("github-token", "token B", PAT_B)
    cfg = load_config(brain)
    cfg.pipelines += [pipeline("alpha", make_source("sa", {"a.md": "A"}), f"vault:{a}"), pipeline("beta", make_source("sb", {"b.md": "B"}), f"vault:{b}")]
    save_config(brain, cfg)
    yield type("W", (), {"brain": brain, "gh": gh, "dir": app_data.dir})
    github_api.set_transport(None)


@pytest.mark.parametrize("name", ["alpha", "beta"])
def test_a_conflicting_pipeline_does_not_sync_and_writes_nothing(conflicting, name):
    head, files = conflicting.gh.head(), dict(conflicting.gh.files())
    res = sync_pipeline(conflicting.brain, name, trigger="cli")
    assert res.result == "error" and res.category == "credential-conflict"
    other = "beta" if name == "alpha" else "alpha"
    assert f"'{other}'" in res.message and PAT_A not in res.message and PAT_B not in res.message
    assert (conflicting.gh.head(), dict(conflicting.gh.files())) == (head, files)


def test_the_conflict_shows_as_a_needs_you_item_on_both_pipelines_without_any_sync(conflicting):
    statuses = {s["name"]: s for s in brain_status(conflicting.brain)["pipelines"]}
    assert statuses["alpha"]["status"] == statuses["beta"]["status"] == "error"
    act = activity_view(conflicting.brain)
    cards = [c for c in act["needs_you"] if c.get("category") == "credential-conflict"]
    assert sorted(c["pipeline"] for c in cards) == ["alpha", "beta"] and act["badge"] == 2
    assert "same credential" in cards[0]["message"] and "another branch" in cards[0]["message"]
    assert [c["pipeline"] for c in activity_view(conflicting.brain, pipeline="alpha")["needs_you"] if c.get("category") == "credential-conflict"] == ["alpha"]


def test_looking_at_a_conflict_writes_nothing_and_leaks_no_secret(conflicting):
    before = tree_snapshot(conflicting.brain.root)
    head = conflicting.gh.head()
    with running_ui(conflicting.brain) as ui:
        views = [ui.get(p)[1] for p in ("/api/state", "/api/activity", "/api/pipelines/alpha/view", "/api/pipelines/beta/view")]
    from mdpipeline.pipelineview import show_view

    views.append(show_view(conflicting.brain, "alpha"))
    text = json.dumps(views)
    assert PAT_A not in text and PAT_B not in text
    assert tree_snapshot(conflicting.brain.root) == before and conflicting.gh.head() == head
    assert views[2]["status"]["status"] == "error" and "different credential" in views[2]["status"]["summary"]


def test_pipeline_show_names_the_credential_in_use_and_the_conflict(conflicting, capsys):
    code = main(["--brain", str(conflicting.brain.root), "pipeline", "show", "alpha"])
    out = capsys.readouterr().out
    assert code == 0 and "token A" in out and "conflict" in out.lower() and "'beta'" in out and PAT_A not in out
    code = main(["--brain", str(conflicting.brain.root), "pipeline", "show", "alpha", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["destination"]["credential_conflict"]["with"] == ["beta"]


def test_resolving_the_conflict_unblocks_both(conflicting):
    cfg = load_config(conflicting.brain)
    cfg.get("beta").destination["credential"] = cfg.get("alpha").destination["credential"]
    save_config(conflicting.brain, cfg)
    assert sync_pipeline(conflicting.brain, "alpha", trigger="cli").result == "ok"
    assert sync_pipeline(conflicting.brain, "beta", trigger="cli").result == "ok"


# --- the save paths ----------------------------------------------------------------------------------------------------------
def test_the_cli_refuses_a_second_pipeline_with_a_new_token_on_the_same_repo_and_branch(conflicting, make_source, capsys, monkeypatch):
    import io

    cfg = load_config(conflicting.brain)
    cfg.pipelines = [p for p in cfg.pipelines if p.name == "alpha"]
    save_config(conflicting.brain, cfg)
    secrets_before = len(vault_mod.default_vault().list())
    monkeypatch.setattr("sys.stdin", io.StringIO(PAT_A + "\n"))
    code = main(["--brain", str(conflicting.brain.root), "pipeline", "add", "gamma", "--source", str(make_source("sg", {"g.md": "G"})),
                 "--github", "acme/brain", "--branch", "main", "--stdin"])
    out = capsys.readouterr()
    assert code != 0 and load_config(conflicting.brain).get("gamma") is None
    assert "'alpha'" in out.err and "another branch" in out.err and PAT_A not in out.out + out.err
    assert len(vault_mod.default_vault().list()) == secrets_before                  # nothing left behind in the vault


def test_the_cli_allows_a_second_pipeline_that_reuses_the_same_secret(conflicting, make_source, capsys):
    cfg = load_config(conflicting.brain)
    cfg.pipelines = [p for p in cfg.pipelines if p.name == "alpha"]
    save_config(conflicting.brain, cfg)
    secret_id = cfg.get("alpha").destination["credential"].split(":", 1)[1]
    code = main(["--brain", str(conflicting.brain.root), "pipeline", "add", "gamma", "--source", str(make_source("sg", {"g.md": "G"})),
                 "--github", "acme/brain", "--branch", "main", "--use-secret", secret_id])
    assert code == 0, capsys.readouterr().err


def test_the_ui_refuses_the_same_conflict_and_saves_nothing(conflicting, make_source):
    cfg = load_config(conflicting.brain)
    cfg.pipelines = [p for p in cfg.pipelines if p.name == "alpha"]
    save_config(conflicting.brain, cfg)
    draft = {"name": "gamma", "sources": [{"path": str(make_source("sg", {"g.md": "G"})), "label": "docs"}], "target": "raw/{pipeline}/{source}/{relpath}",
             "include": ["**/*.md"], "exclude": [".git/**"], "on_source_delete": "delete", "guard_threshold_percent": 40, "guard_min_files": 5,
             "name_policy": "original", "destination": {"type": "github", "repo": "acme/brain", "branch": "main", "auth": "token"}}
    n = len(vault_mod.default_vault().list())
    with running_ui(conflicting.brain) as ui:
        status, res = ui.post("/api/pipelines", {"draft": draft, "token": PAT_A})
    assert status == 422 and "credential-conflict" in json.dumps(res) and PAT_A not in json.dumps(res)
    assert load_config(conflicting.brain).get("gamma") is None and len(vault_mod.default_vault().list()) == n


def test_view_mode_page_shows_the_conflict_text(conflicting):
    with running_ui(conflicting.brain) as ui:
        view = ui.get("/api/pipelines/alpha/view")[1]
        js = ui.get("/app.js")[1]
    assert view["config"]["destination"]["credential_conflict"]["with"] == ["beta"] and "credential_conflict" in js
