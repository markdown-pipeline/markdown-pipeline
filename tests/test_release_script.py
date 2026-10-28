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
                   runner=lambda cmd: calls.append(cmd) or ((1, "release not found") if cmd[:3] == ["gh", "release", "view"] else (0, "")))
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
                   runner=lambda cmd: calls.append(cmd) or ((1, "not found") if cmd[:3] == ["gh", "release", "view"] else (0, "")))
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


# the public repo's own files: README, install script, "what we send" (no source code) -------------------------------------------------------
def test_the_public_repo_gets_only_the_readme_the_install_script_and_the_plain_words_pages(rel):
    files = rel.public_files()
    assert set(files) == {"README.md", "install.sh", "what-we-send.md", "usage-events.md"}
    assert all(src.exists() for src in files.values())
    assert not any(name.endswith((".py", ".js", ".toml", ".lock")) for name in files)


def test_every_relative_link_in_the_public_pages_resolves_inside_the_public_repo(rel):
    import re

    files = rel.public_files()
    for name, src in files.items():
        if not name.endswith(".md"):
            continue
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", src.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:", "../../")):
                continue
            # MD-186: the public repo now holds the whole source tree too, so a link may also point at a file that ships in it
            assert target in files or (APP / target).exists(), f"{name} links to {target}, which the public repo does not have"


def test_syncing_the_public_files_clones_copies_commits_and_pushes_only_when_publishing(rel, tmp_path):
    calls = []

    def runner(cmd):
        calls.append(cmd)
        return 0, (" M README.md" if "status" in cmd else "")

    rel.sync_public_files("o/r", runner, workdir=tmp_path, publish=False)
    assert calls == []                                              # a dry run touches nothing
    rel.sync_public_files("o/r", runner, workdir=tmp_path, publish=True)
    assert calls[0][:3] == ["gh", "repo", "clone"] and "o/r" in calls[0]
    assert any(c[0] == "git" and "commit" in c for c in calls) and any(c[0] == "git" and "push" in c for c in calls)
    assert (tmp_path / "repo" / "install.sh").exists() and (tmp_path / "repo" / "README.md").exists()
    assert not list((tmp_path / "repo").glob("*.py"))


def test_nothing_is_committed_when_the_public_files_did_not_change(rel, tmp_path):
    calls = []

    def runner(cmd):
        calls.append(cmd)
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return 0, ""                                            # nothing changed
        return 0, ""

    rel.sync_public_files("o/r", runner, workdir=tmp_path, publish=True)
    assert not any(c[0] == "git" and "commit" in c for c in calls)


# MM-121: the repo's own settings the public README depends on (Issues on, marketing's description) -------------------------------------------------------
def test_the_repo_settings_turn_issues_on_and_set_marketings_description(rel):
    cmd = rel.repo_settings_command("o/r")
    assert cmd[:3] == ["gh", "repo", "edit"] and "o/r" in cmd and "--enable-issues" in cmd
    assert cmd[cmd.index("--description") + 1] == ("Your team's tools as clean Markdown files, kept current for your AI agents, with a local MCP server. "
                                                    "Early access: Jira Cloud and local folders, Linux. Apache-2.0.")


def test_publishing_applies_the_repo_settings_before_the_files_and_a_dry_run_does_not(rel, tmp_path):
    calls = []
    wheel = make_wheel(tmp_path, GOOD)
    rel.run(["--repo", "o/r", "--wheel", str(wheel), "--version", "0.1.0"], runner=lambda cmd: calls.append(cmd) or (0, ""))
    assert not any(c[:3] == ["gh", "repo", "edit"] for c in calls)
    calls.clear()
    rel.run(["--repo", "o/r", "--wheel", str(wheel), "--version", "0.1.0", "--publish"],
            runner=lambda cmd: calls.append(cmd) or ((1, "") if cmd[:3] == ["gh", "release", "view"] else (0, " M README.md" if "status" in cmd else "")))
    kinds = [c[:3] for c in calls]
    assert ["gh", "repo", "edit"] in kinds and kinds.index(["gh", "repo", "edit"]) < kinds.index(["gh", "repo", "clone"])


def test_the_public_files_go_first_because_an_empty_repository_cannot_have_a_release(rel, tmp_path):
    """Found publishing for real: GitHub answers 'Repository is empty' (HTTP 422) to a release on a repo with no commit."""
    calls = []
    wheel = make_wheel(tmp_path, GOOD)
    rel.run(["--repo", "o/r", "--wheel", str(wheel), "--version", "0.1.0", "--publish"],
            runner=lambda cmd: calls.append(cmd) or ((1, "") if cmd[:3] == ["gh", "release", "view"] else (0, " M README.md" if "status" in cmd else "")))
    kinds = [c[:3] for c in calls]
    assert kinds.index(["gh", "repo", "edit"]) < kinds.index(["gh", "repo", "clone"]) < kinds.index(["gh", "release", "create"])
    push = next(i for i, c in enumerate(calls) if c[0] == "git" and "push" in c)
    assert push < kinds.index(["gh", "release", "create"])
