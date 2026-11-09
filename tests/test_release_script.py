"""MD-131 AC1/AC5: scripts/release.py builds the package from the private source and publishes it as a GitHub Release on the public repo."""
import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
SCRIPT = APP / "scripts" / "release.py"
DEFAULTS = json.dumps({"github_app": {"client_id": "Iv23li87elyNUpdxbmkx", "slug": "markdown-pipeline"}})


@pytest.fixture
def rel():
    spec = importlib.util.spec_from_file_location("release_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["release_script"] = module
    spec.loader.exec_module(module)
    module.check_server_json = lambda version, path=None: None        # DEV-11: the repo's real server.json carries the current version; tests use made-up ones
    return module


def make_wheel(tmp_path, files: dict[str, str]) -> Path:
    path = tmp_path / "mdpipeline-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return path


GOOD = {"mdpipeline/__init__.py": "", "mdpipeline/defaults.json": DEFAULTS, "mdpipeline-0.1.0.dist-info/METADATA": "Name: mdpipeline\n",
        "mdpipeline/usage_events.py": '"install_completed": Event(...)\n', "mdpipeline/usage.py": '"is_internal": internal_flag()\n'}


def test_a_clean_wheel_has_no_problems(rel, tmp_path):
    assert rel.wheel_problems(make_wheel(tmp_path, GOOD)) == []


@pytest.mark.parametrize("name", ["tests/test_x.py", ".env", "mdpipeline/.env", "mdpipeline/server.pem", "id_rsa", ".git/config", "mdpipeline/__pycache__/a.cpython-312.pyc",
                                   "uv.lock", "docs/usage-events.md", "scripts/install.sh"])
def test_dev_only_and_secret_files_are_refused_by_name(rel, tmp_path, name):
    problems = rel.wheel_problems(make_wheel(tmp_path, {**GOOD, name: "x"}))
    assert any(name in p for p in problems), problems


@pytest.mark.parametrize("secret", ["ghp_" + "a" * 36, "github_pat_" + "A1b2" * 10, "ATATT3xFfGF0" + "a" * 40, "-----BEGIN RSA PRIVATE KEY-----", "AKIA" + "A" * 16,
                                     "xoxb-1234567890-abcdef"])
def test_a_token_or_private_key_inside_a_file_is_refused_without_printing_it(rel, tmp_path, secret):
    problems = rel.wheel_problems(make_wheel(tmp_path, {**GOOD, "mdpipeline/leak.py": f"X = '{secret}'\n"}))
    assert problems and all(secret not in p for p in problems) and any("leak.py" in p for p in problems)


def test_the_public_posthog_project_key_is_allowed_because_it_ships_on_purpose(rel, tmp_path):
    assert rel.wheel_problems(make_wheel(tmp_path, {**GOOD, "mdpipeline/transport.py": "KEY = 'phc_" + "a" * 40 + "'\n"})) == []


def test_the_public_github_app_client_id_must_be_in_the_package(rel, tmp_path):
    files = {k: v for k, v in GOOD.items() if k != "mdpipeline/defaults.json"}
    assert any("defaults.json" in p for p in rel.wheel_problems(make_wheel(tmp_path, files)))
    empty = {**GOOD, "mdpipeline/defaults.json": json.dumps({"github_app": {"client_id": "", "slug": ""}})}
    assert any("Client ID" in p for p in rel.wheel_problems(make_wheel(tmp_path, empty)))


@pytest.mark.parametrize("name,what", [("mdpipeline/usage_events.py", "install_completed"), ("mdpipeline/usage.py", "is_internal")])
def test_the_release_build_must_carry_the_install_event_and_the_internal_mark(rel, tmp_path, name, what):
    """MD-154 AC4: launch-day funnel needs install_completed in the release build, and our own installs tagged."""
    files = {**GOOD, name: "# nothing\n"}
    assert any(what in p for p in rel.wheel_problems(make_wheel(tmp_path, files)))


def test_the_tag_comes_from_the_package_version(rel):
    assert rel.tag_for("0.1.0") == "v0.1.0"
    assert rel.version_from_pyproject('[project]\nname = "x"\nversion = "1.4.2"\n') == "1.4.2"


def test_a_first_release_is_created_with_the_wheel_attached_on_the_public_repo(rel, tmp_path):
    wheel = make_wheel(tmp_path, GOOD)
    plan = rel.release_plan("0.1.0", "markdown-pipeline/markdown-pipeline", wheel, exists=False)
    assert plan[0][:4] == ["gh", "release", "create", "v0.1.0"] and "--repo" in plan[0] and "markdown-pipeline/markdown-pipeline" in plan[0]
    assert str(wheel) in plan[0] and "--latest" in plan[0] and len(plan) == 1


def test_running_it_again_for_the_same_version_refuses_unless_asked_to_replace(rel, tmp_path):
    wheel = make_wheel(tmp_path, GOOD)
    with pytest.raises(rel.ReleaseError, match="already exists"):
        rel.release_plan("0.1.0", "o/r", wheel, exists=True)
    plan = rel.release_plan("0.1.0", "o/r", wheel, exists=True, replace=True)
    assert plan[0][:4] == ["gh", "release", "upload", "v0.1.0"] and "--clobber" in plan[0]


def test_without_publish_nothing_is_run_but_the_checks_and_the_plan_are_printed(rel, tmp_path, capsys):
    calls = []
    wheel = make_wheel(tmp_path, GOOD)
    code = rel.run(["--repo", "o/r", "--wheel", str(wheel), "--version", "0.1.0"], runner=lambda cmd: calls.append(cmd) or (0, ""))
    out = capsys.readouterr().out
    assert code == 0 and "DRY RUN" in out and "gh release create v0.1.0" in out
    assert not any(c[:3] == ["gh", "release", "create"] for c in calls)


def test_publish_runs_the_plan_and_a_problem_wheel_stops_before_anything_is_published(rel, tmp_path):
    calls = []
    bad = make_wheel(tmp_path, {**GOOD, ".env": "SECRET=1"})
    code = rel.run(["--repo", "o/r", "--wheel", str(bad), "--version", "0.1.0", "--publish"], runner=lambda cmd: calls.append(cmd) or (0, ""))
    assert code == 1 and not any(c[:3] == ["gh", "release", "create"] for c in calls)
    good = make_wheel(tmp_path, GOOD)
    calls.clear()
    code = rel.run(["--repo", "o/r", "--wheel", str(good), "--version", "0.1.0", "--publish"],
                   runner=publish_runner(calls))
    assert code == 0 and any(c[:3] == ["gh", "release", "create"] for c in calls)


# MD-169 AC2: uv.lock travels with the version bump, so `uv run` never has to rewrite it ---------------------------------------------------
PYPROJECT = '[project]\nname = "mdpipeline"\nversion = "0.1.9"\n'


def lock_text(version: str) -> str:
    return f'version = 1\n\n[[package]]\nname = "pyyaml"\nversion = "6.0.3"\n\n[[package]]\nname = "mdpipeline"\nversion = "{version}"\nsource = {{ editable = "." }}\n'


def test_a_lockfile_in_step_with_the_version_has_no_problem(rel):
    assert rel.lock_problem(PYPROJECT, lock_text("0.1.9")) is None


def test_a_lockfile_with_an_older_version_is_a_problem_that_names_the_fix(rel):
    problem = rel.lock_problem(PYPROJECT, lock_text("0.1.8"))
    assert "0.1.8" in problem and "0.1.9" in problem and "uv lock" in problem


def test_a_lockfile_without_the_package_is_a_problem(rel):
    assert "uv lock" in rel.lock_problem(PYPROJECT, 'version = 1\n\n[[package]]\nname = "pyyaml"\nversion = "6.0.3"\n')


def test_the_release_stops_before_building_when_the_lockfile_is_stale(rel, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(rel, "lock_problem", lambda pyproject, lock: "uv.lock says 0.0.1 but pyproject.toml says 0.1.9: run `uv lock`")
    calls = []
    code = rel.run(["--repo", "o/r", "--wheel", str(make_wheel(tmp_path, GOOD)), "--version", "0.1.0", "--publish"], runner=lambda cmd: calls.append(cmd) or (0, ""))
    assert code == 1 and calls == [] and "uv lock" in capsys.readouterr().err


# release notes from a file (v0.1.9): the prepared notes are what gets published --------------------------------------------------------------
def test_the_plan_uses_the_given_release_notes_instead_of_the_one_liner(rel, tmp_path):
    plan = rel.release_plan("0.1.9", "o/r", make_wheel(tmp_path, GOOD), exists=False, notes="What is new\n\n- one thing")
    create = plan[0]
    assert create[create.index("--notes") + 1] == "What is new\n\n- one thing"


def test_without_notes_the_one_liner_is_kept(rel, tmp_path):
    create = rel.release_plan("0.1.9", "o/r", make_wheel(tmp_path, GOOD), exists=False)[0]
    assert create[create.index("--notes") + 1].startswith("Markdown Pipeline 0.1.9.")


def test_publish_with_a_notes_file_creates_the_release_with_its_text(rel, tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text("Fixes\n\n- a fix\n", encoding="utf-8")
    calls = []
    code = rel.run(["--repo", "o/r", "--wheel", str(make_wheel(tmp_path, GOOD)), "--version", "0.1.0", "--notes-file", str(notes), "--publish"],
                   runner=publish_runner(calls))
    create = next(c for c in calls if c[:3] == ["gh", "release", "create"])
    assert code == 0 and create[create.index("--notes") + 1] == "Fixes\n\n- a fix\n"


def test_a_missing_notes_file_stops_before_anything_is_published(rel, tmp_path, capsys):
    calls = []
    code = rel.run(["--repo", "o/r", "--wheel", str(make_wheel(tmp_path, GOOD)), "--version", "0.1.0", "--notes-file", str(tmp_path / "nope.md"), "--publish"],
                   runner=lambda cmd: calls.append(cmd) or (0, ""))
    assert code == 1 and calls == [] and "notes" in capsys.readouterr().err


def test_the_real_built_package_passes_every_check():
    done = subprocess.run([sys.executable, str(SCRIPT), "--repo", "o/r"], capture_output=True, text=True, timeout=300, cwd=APP)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "package checks: OK" in done.stdout


# the public tree equals the tag's tree (DEV-11): the whole sync_public.apply of this checkout, not four files -----------------------------------------
import subprocess as _sp


def sync_runner(calls, changed=" M README.md"):
    """A runner whose `gh repo clone` makes a real, empty git repository, so sync_public can plan against it; the git commands that need a remote are recorded."""
    def runner(cmd):
        calls.append(cmd)
        if cmd[:3] == ["gh", "repo", "clone"]:
            _sp.run(["git", "init", "-q", cmd[4]], check=True, capture_output=True)
            return 0, ""
        if cmd[:1] == ["git"] and "status" in cmd:
            return 0, changed
        return 0, ""
    return runner


def publish_runner(calls):
    """A whole publish: the clone is a real git repository, a changed tree, and no release exists yet."""
    base = sync_runner(calls)
    return lambda cmd: ((calls.append(cmd) or (1, "release not found")) if cmd[:3] == ["gh", "release", "view"] else base(cmd))


def test_the_public_repo_gets_the_whole_tracked_tree_without_the_workflows_and_the_windows_installer(rel, tmp_path):
    calls = []
    rel.sync_public_files("o/r", sync_runner(calls), workdir=tmp_path, publish=True, version="0.1.0")
    clone = tmp_path / "repo"
    assert (clone / "README.md").exists() and (clone / "install.sh").exists() and (clone / "src/mdpipeline/agents.py").exists()
    assert (clone / "what-we-send.md").exists() and (clone / "usage-events.md").exists()
    assert not (clone / ".github").exists() and not (clone / "install.ps1").exists()


def test_the_commit_names_the_version_and_a_run_without_changes_commits_nothing(rel, tmp_path):
    calls = []
    rel.sync_public_files("o/r", sync_runner(calls), workdir=tmp_path / "a", publish=True, version="0.1.19")
    commit = next(c for c in calls if c[0] == "git" and "commit" in c)
    assert commit[commit.index("-m") + 1] == "Source and public files at v0.1.19"
    assert any(c[0] == "git" and "push" in c for c in calls)
    calls.clear()
    rel.sync_public_files("o/r", sync_runner(calls, changed=""), workdir=tmp_path / "b", publish=True, version="0.1.19")
    assert not any(c[0] == "git" and ("commit" in c or "push" in c) for c in calls)


def test_a_dry_run_prints_the_plan_and_commits_or_pushes_nothing(rel, tmp_path, capsys):
    calls = []
    assert rel.sync_public_files("o/r", sync_runner(calls), workdir=tmp_path, publish=False, version="0.1.0") is False
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "would write" in out and "would delete 0" in out and "README.md" in out
    assert not any(c[0] == "git" for c in calls)


def test_a_dry_run_that_cannot_clone_still_says_what_would_be_synced(rel, tmp_path, capsys):
    rel.sync_public_files("o/r", lambda cmd: (1, "no access"), workdir=tmp_path, publish=False, version="0.1.0")
    assert "could not clone" in capsys.readouterr().out


def test_a_file_removed_from_the_tag_is_deleted_in_the_public_clone_and_a_public_only_workflow_stays(rel, tmp_path):
    calls = []

    def runner(cmd):
        out = sync_runner(calls)(cmd)
        if cmd[:3] == ["gh", "repo", "clone"]:
            clone = Path(cmd[4])
            for name, text in {"old-removed.md": "gone", ".github/workflows/verify-channels.yml": "public only"}.items():
                (clone / name).parent.mkdir(parents=True, exist_ok=True)
                (clone / name).write_text(text)
            _sp.run(["git", "add", "-A"], cwd=clone, check=True, capture_output=True)
        return out

    rel.sync_public_files("o/r", runner, workdir=tmp_path, publish=True, version="0.1.0")
    assert not (tmp_path / "repo/old-removed.md").exists() and (tmp_path / "repo/.github/workflows/verify-channels.yml").read_text() == "public only"


def test_every_relative_link_in_the_public_pages_resolves_inside_the_public_repo(rel):
    import re

    files = {name: APP / src for name, src in rel.sync_public_module().wanted(APP).items() if name in ("README.md", "what-we-send.md", "usage-events.md")}
    wanted = rel.sync_public_module().wanted(APP)
    for name, src in files.items():
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", src.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:", "../../")):
                continue
            assert target in wanted or (APP / target).exists(), f"{name} links to {target}, which the public repo does not have"


def test_the_public_files_go_first_because_an_empty_repository_cannot_have_a_release(rel, tmp_path):
    """Found publishing for real: GitHub answers 'Repository is empty' (HTTP 422) to a release on a repo with no commit."""
    calls = []
    wheel = make_wheel(tmp_path, GOOD)
    rel.run(["--repo", "o/r", "--wheel", str(wheel), "--version", "0.1.0", "--publish"],
            runner=publish_runner(calls))
    kinds = [c[:3] for c in calls]
    assert kinds.index(["gh", "repo", "edit"]) < kinds.index(["gh", "repo", "clone"]) < kinds.index(["gh", "release", "create"])
    push = next(i for i, c in enumerate(calls) if c[0] == "git" and "push" in c)
    assert push < kinds.index(["gh", "release", "create"])


# MD-183 AC5: the Windows installer is published next to install.sh, but only once it is no longer withheld (Windows released after Gate 2) -----------
def test_install_ps1_is_not_published_while_it_is_withheld(rel):
    assert "install.ps1" not in rel.sync_public_module().wanted(APP)


def test_once_released_install_ps1_is_published_at_the_public_root(rel, monkeypatch):
    sync = rel.sync_public_module()           # the same module release.py asks, so one switch (sync_public.WITHHELD) governs both scripts
    monkeypatch.setattr(sync, "WITHHELD", {p for p in sync.WITHHELD if p != "scripts/install.ps1"})
    assert sync.wanted(APP)["install.ps1"] == "scripts/install.ps1"


# DEV-11: CI publishes with a token that cannot edit repo settings, and the Registry workflow reads server.json from the release --------------------
def make_server_json(tmp_path, top="0.1.0", package="0.1.0"):
    path = tmp_path / "server.json"
    path.write_text(json.dumps({"name": "io.github.x/y", "version": top, "packages": [{"registryType": "pypi", "identifier": "mdpipeline", "version": package}]}), encoding="utf-8")
    return path


def test_server_json_must_carry_the_release_version_in_both_places(rel, tmp_path):
    assert rel.server_json_problem(make_server_json(tmp_path).read_text(), "0.1.0") is None
    assert "version" in rel.server_json_problem(make_server_json(tmp_path, top="0.0.9").read_text(), "0.1.0")
    assert "packages[0]" in rel.server_json_problem(make_server_json(tmp_path, package="0.0.9").read_text(), "0.1.0")
    assert rel.server_json_problem("not json", "0.1.0")
    assert rel.server_json_problem("{}", "0.1.0")


def test_the_real_server_json_matches_the_package_version(rel):
    assert rel.server_json_problem((APP / "server.json").read_text(encoding="utf-8"), rel.version_from_pyproject((APP / "pyproject.toml").read_text(encoding="utf-8"))) is None


def test_a_first_release_attaches_server_json_in_the_same_create_call(rel, tmp_path):
    wheel, sj = make_wheel(tmp_path, GOOD), make_server_json(tmp_path)
    plan = rel.release_plan("0.1.0", "o/r", wheel, exists=False, server_json=sj)
    assert len(plan) == 1 and plan[0][:4] == ["gh", "release", "create", "v0.1.0"] and plan[0][4:6] == [str(wheel), str(sj)]


def test_replace_uploads_both_files_with_clobber(rel, tmp_path):
    wheel, sj = make_wheel(tmp_path, GOOD), make_server_json(tmp_path)
    plan = rel.release_plan("0.1.0", "o/r", wheel, exists=True, replace=True, server_json=sj)
    assert plan == [["gh", "release", "upload", "v0.1.0", str(wheel), str(sj), "--repo", "o/r", "--clobber"]]


def test_a_version_mismatch_in_server_json_stops_everything_before_any_command(rel, tmp_path, capsys):
    spec = importlib.util.spec_from_file_location("release_script_real", SCRIPT)
    real = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real)
    calls = []
    code = real.run(["--repo", "o/r", "--wheel", str(make_wheel(tmp_path, GOOD)), "--version", "0.1.0", "--publish", "--server-json", str(make_server_json(tmp_path, top="0.0.9"))],
                    runner=lambda cmd: calls.append(cmd) or (0, ""))
    assert code == 1 and calls == [] and "server.json" in capsys.readouterr().err


def test_skip_repo_settings_leaves_out_gh_repo_edit_and_the_rest_still_runs(rel, tmp_path):
    calls = []
    argv = ["--repo", "o/r", "--wheel", str(make_wheel(tmp_path, GOOD)), "--version", "0.1.0", "--publish", "--server-json", str(make_server_json(tmp_path))]
    runner = publish_runner(calls)
    assert rel.run([*argv, "--skip-repo-settings"], runner=runner) == 0
    kinds = [c[:3] for c in calls]
    assert ["gh", "repo", "edit"] not in kinds and ["gh", "repo", "clone"] in kinds and ["gh", "release", "create"] in kinds
    calls.clear()
    assert rel.run(argv, runner=runner) == 0 and ["gh", "repo", "edit"] in [c[:3] for c in calls]


def test_the_dry_run_says_the_repo_settings_are_skipped(rel, tmp_path, capsys):
    rel.run(["--repo", "o/r", "--wheel", str(make_wheel(tmp_path, GOOD)), "--version", "0.1.0", "--skip-repo-settings"], runner=lambda cmd: (0, ""))
    assert "skipped" in capsys.readouterr().out.lower()


def test_the_default_runner_reports_a_missing_program_instead_of_raising(rel):
    code, text = rel._run(["no-such-program-mdp"])
    assert code == 127 and "no-such-program-mdp" in text
