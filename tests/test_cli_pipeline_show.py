"""MD-95: `pipeline show NAME [--json]` prints one pipeline's configuration and status, read-only, with no secret values."""
import json

import pytest

from conftest import tree_snapshot
from fake_github import FakeGitHub
from mdpipeline import github_api, vault as vault_mod
from mdpipeline.cli import main
from mdpipeline.config import Pipeline, Source
from mdpipeline.engine import sync_pipeline
from mdpipeline.service import set_paused
from mdpipeline.targetconfig import GitHubSpec
from secret_search import files_containing
from test_jira_sync import SITE, jira_source


def run(capsys, brain, *argv):
    code = main(["--brain", str(brain.root), *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def folder_pipeline(brain, add_pipeline, make_source):
    src = make_source("docs", {"1.md": "one", "2.md": "two"})
    add_pipeline(Pipeline.new("p", [Source(str(src), "docs")], name_policy="date-name", on_source_delete="keep", guard_threshold_percent=25, guard_min_files=3))
    sync_pipeline(brain, "p", trigger="cli")
    return src


def test_json_has_the_view_modes_fields_plus_status(brain, folder_pipeline, capsys):
    code, out, err = run(capsys, brain, "pipeline", "show", "p", "--json")
    data = json.loads(out)
    assert code == 0 and err == ""
    assert data["schema"] == 1 and data["name"] == "p" and data["paused"] is False
    assert data["sources"][0]["type"] == "folder" and data["sources"][0]["label"] == "docs" and data["sources"][0]["check"]["md_files"] == 2
    assert data["include"] == ["**/*.md"] and data["target"] == "raw/{pipeline}/{source}/{relpath}" and data["on_source_delete"] == "keep"
    assert data["guard"] == {"threshold_percent": 25, "min_files": 3} and data["name_policy"]["policy"] == "date-name" and data["name_policy"]["example"]
    assert data["destination"]["type"] == "directory"
    assert data["status"]["status"] == "ok" and data["status"]["owned_files"] == 2 and data["status"]["last_run"]["trigger"] == "cli"
    assert data["status"]["summary"] == "2 created" and data["status"]["last_sync_ts"]


def test_the_json_keys_are_the_documented_ones(brain, folder_pipeline, capsys):
    data = json.loads(run(capsys, brain, "pipeline", "show", "p", "--json")[1])
    assert sorted(data) == ["destination", "exclude", "guard", "include", "name", "name_policy", "on_source_delete", "paused", "schema", "sources", "status", "target"]
    assert sorted(data["status"]) == ["destination", "last_changes", "last_run", "last_sync", "last_sync_ts", "needs_you", "next_sync", "owned_files", "paused", "pending_approval", "schedule", "status", "summary"]


def test_the_text_output_names_every_setting_and_the_status(brain, folder_pipeline, capsys):
    code, out, _ = run(capsys, brain, "pipeline", "show", "p")
    assert code == 0
    for text in ("p  [ok]", "docs", str(folder_pipeline), "Folder found · 2 .md files", "raw/{pipeline}/{source}/{relpath}", "date-name", "Example:",
                 "**/*.md", ".git/**", "keep", "25%", "at least 3 files", "files owned: 2", f"directory {brain.root}", "sync p --dry-run"):
        assert text in out, text


def test_a_paused_pipeline_says_so(brain, folder_pipeline, capsys):
    set_paused(brain, "p", True, trigger="cli")
    code, out, _ = run(capsys, brain, "pipeline", "show", "p")
    assert code == 0 and "[paused]" in out and "paused" in json.loads(run(capsys, brain, "pipeline", "show", "p", "--json")[1])["status"]["status"]


def test_unknown_name_is_a_clear_error_with_a_non_zero_exit_and_no_output(brain, folder_pipeline, capsys):
    code, out, err = run(capsys, brain, "pipeline", "show", "nope")
    assert code == 2 and out == "" and "No pipeline named 'nope'" in err


def test_show_writes_nothing(brain, folder_pipeline, capsys, app_data):
    before = tree_snapshot(brain.root, folder_pipeline)
    for flags in ([], ["--json"]):
        assert run(capsys, brain, "pipeline", "show", "p", *flags)[0] == 0
    assert tree_snapshot(brain.root, folder_pipeline) == before


def test_jira_credentials_are_method_set_and_name_only(brain, add_pipeline, monkeypatch, capsys):
    monkeypatch.setenv("JIRA_EMAIL", "me@example.com")
    monkeypatch.setenv("JIRA_TOKEN", "ATATT-SHOW-SECRET")
    add_pipeline(Pipeline("j", [jira_source("project = MD", "tickets")]))
    code, out, _ = run(capsys, brain, "pipeline", "show", "j")
    assert code == 0 and SITE in out and "project = MD" in out and "JIRA_TOKEN" in out and "environment variable" in out and "set" in out
    js = run(capsys, brain, "pipeline", "show", "j", "--json")[1]
    assert json.loads(js)["sources"][0]["credentials"]["token"] == {"method": "env", "name": "JIRA_TOKEN", "set": True}
    assert "ATATT-SHOW-SECRET" not in out + js and "me@example.com" not in out + js


def test_github_target_fields_and_no_secret(brain, add_pipeline, make_source, capsys):
    token = "ghp_SHOWSECRET0123456789"
    fake = FakeGitHub(repo="acme/brain", branch="main", token=token, files={"wiki/w.md": b"W"})
    github_api.set_transport(fake)
    try:
        sid = vault_mod.default_vault().add("github-token", "GitHub token for g", token)
        dest = GitHubSpec("acme/brain", f"vault:{sid}", "main").to_dict() | {"auth": "token", "clone": "/home/x/clone"}
        add_pipeline(Pipeline.new("g", [str(make_source("docs", {"a.md": "A"}))], destination=dest))
        sync_pipeline(brain, "g")
        fake.requests.clear()
        code, out, _ = run(capsys, brain, "pipeline", "show", "g")
        js = run(capsys, brain, "pipeline", "show", "g", "--json")[1]
        methods = {m for m, _ in fake.requests}
    finally:
        github_api.set_transport(None)
    assert code == 0 and "acme/brain" in out and "main" in out and "/home/x/clone" in out and 'app vault "GitHub token for g"' in out and "last commit" in out
    assert json.loads(js)["destination"]["credential"] == {"method": "vault", "name": "GitHub token for g", "set": True}
    assert token not in out + js and files_containing(token, brain.root) == [] and methods <= {"GET"}


def test_help_lists_show(capsys):
    with pytest.raises(SystemExit):
        main(["pipeline", "show", "--help"])
    assert "--json" in capsys.readouterr().out


def test_the_readme_documents_show_its_json_keys_and_the_dry_run_and_its_commands_parse():
    import re
    import shlex
    from pathlib import Path

    from mdpipeline.cli import build_parser

    readme = (Path(__file__).parent.parent / "docs" / "reference.md").read_text()
    section = re.search(r"### Looking at one pipeline without editing it: `pipeline show`.*?(?=^## )", readme, re.S | re.M)
    assert section, "README needs the 'pipeline show' section"
    text = section.group(0)
    for needle in ("--json", "--dry-run", "schema", "sources[]", "credentials", "name_policy", "guard", "status{", "never prints a secret", "exits with `2`"):
        assert needle in text, needle
    assert "`pipeline show NAME [--json]`" in readme
    for line in re.findall(r"^(mdpipeline [^\n#]+)", text, re.M):
        build_parser().parse_args(shlex.split(line)[1:])
