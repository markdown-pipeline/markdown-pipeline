"""MD-237: `mdpipeline recipe <id>`: a built-in public Jira project, synced in one command, with the window pinned at the first run."""
import sys
from datetime import date
from pathlib import Path

import pytest

from fake_jira import FakeJira
from mdpipeline import jira_api, recipes
from mdpipeline.cli import main
from mdpipeline.config import load_config

HHH = "jira-hibernate-hhh"
TODAY = date(2026, 10, 1)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(recipes, "today", lambda: TODAY)


@pytest.fixture
def hibernate():
    f = FakeJira(site="hibernate")
    f.public = True
    for n in range(3):
        f.add_issue("HHH", f"issue {n}")
    jira_api.set_transport(f, sleep=lambda s: None)
    yield f
    jira_api.set_transport(None)


def recipe(capsys, *argv, brain=None):
    code = main([*(["--brain", str(brain.root)] if brain else []), "recipe", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_the_allowlist_is_exactly_the_three_decided_recipes():
    assert {r.id: (r.site, r.jql_project, r.window_days) for r in recipes.RECIPES.values()} == {
        "jira-ecosystem-frge": ("https://ecosystem.atlassian.net", "FRGE", 30),
        "jira-hibernate-hhh": ("https://hibernate.atlassian.net", "HHH", 14),
        "jira-sakaiproject-sak": ("https://sakaiproject.atlassian.net", "SAK", 14)}


def test_the_window_is_pinned_to_a_fixed_date():
    assert recipes.pinned_jql(recipes.RECIPES[HHH], TODAY) == 'project = HHH AND updated >= "2026-09-17"'


def test_recipe_list_shows_ids_projects_and_windows(capsys):
    code, out, _ = recipe(capsys, "list")
    assert code == 0 and HHH in out and "HHH" in out and "14 days" in out and "jira-ecosystem-frge" in out and "30 days" in out


def test_an_unknown_id_prints_the_list_and_exits_non_zero(capsys):
    code, out, err = recipe(capsys, "nope")
    assert code != 0 and 'There\'s no recipe called "nope". Pick one from the list above.' in err and HHH in out


def test_the_list_ends_with_a_concrete_example_command(capsys):
    _, out, _ = recipe(capsys, "list")
    assert "Recipes you can run:" in out and "Run one, for example: mdpipeline recipe jira-hibernate-hhh" in out


def test_the_sakai_questions_are_the_approved_ones():
    q = recipes.RECIPES["jira-sakaiproject-sak"].questions
    assert q[2] == "What was fixed in the last two weeks? Group it by tool."


@pytest.mark.parametrize("seconds,shown", [(0, "(0s)"), (57, "(57s)"), (60, "(1m 00s)"), (65, "(1m 05s)")])
def test_the_duration_is_seconds_under_a_minute(seconds, shown):
    from mdpipeline.recipe_run import duration
    assert duration(seconds) == shown


def test_a_rerun_says_the_window_was_set_at_the_first_run(brain, hibernate, capsys, monkeypatch):
    _, first, _ = recipe(capsys, HHH, brain=brain)
    assert "(the last 14 days)" in first
    monkeypatch.setattr(recipes, "today", lambda: date(2026, 10, 20))
    _, again, _ = recipe(capsys, HHH, brain=brain)
    assert "Syncing Hibernate ORM (HHH): issues updated since 2026-09-17 (the window set at your first run)." in again
    assert "the last 14 days" not in again


def test_on_windows_the_next_step_is_two_lines(brain, hibernate, capsys, monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    _, out, _ = recipe(capsys, HHH, brain=brain)
    folder = brain.root / HHH
    assert f'  cd "{folder}"\n  claude\n' in out and "&& claude" not in out


def test_without_a_brain_the_default_folder_is_created_and_named(hibernate, capsys, tmp_path):
    code, out, err = recipe(capsys, HHH)
    assert code == 0, err
    default = Path.home() / "mdpipeline-recipes"
    assert f"Created a folder for recipes: {default}" in out and (default / ".mdpipeline" / "config.yaml").is_file()


def test_with_a_brain_it_says_it_uses_it(brain, hibernate, capsys):
    code, out, err = recipe(capsys, HHH, brain=brain)
    assert code == 0, err
    assert f"Using your Markdown folder: {brain.root}" in out


def test_the_pipeline_is_marked_as_a_recipe_public_and_pinned(brain, hibernate, capsys):
    recipe(capsys, HHH, brain=brain)
    src = load_config(brain).get(HHH).sources[0]
    assert src.options == {"site": "https://hibernate.atlassian.net", "jql": 'project = HHH AND updated >= "2026-09-17"',
                           "auth_mode": "public", "recipe_id": HHH}


def test_it_writes_the_files_into_the_recipe_folder_and_says_so(brain, hibernate, capsys):
    code, out, err = recipe(capsys, HHH, brain=brain)
    folder = brain.root / HHH
    assert code == 0, err and len(list(folder.rglob("*.md"))) == 3
    assert f"Done: 3 files in {folder}" in out
    step = f'cd "{folder}"\n  claude' if sys.platform == "win32" else f'cd "{folder}" && claude'      # Windows prints two lines (PowerShell 5.1 has no `&&`)
    assert step in out


def test_the_output_follows_the_copy_spec(brain, hibernate, capsys):
    _, out, _ = recipe(capsys, HHH, brain=brain)
    assert "Syncing Hibernate ORM (HHH): issues updated since 2026-09-17 (the last 14 days)." in out
    assert "No login needed: this is a public tracker, read directly by your machine." in out
    assert "Run the same command again any time to get new and updated issues." in out
    assert f"Remove it: mdpipeline pipeline remove {HHH}   (add --purge to delete the files too)" in out
    assert "Do the same for your team's Jira: mdpipeline start" in out
    assert out.count("  • ") == 3


def test_running_it_again_adds_no_second_pipeline_and_keeps_the_pinned_window(brain, hibernate, capsys, monkeypatch):
    recipe(capsys, HHH, brain=brain)
    monkeypatch.setattr(recipes, "today", lambda: date(2026, 10, 20))
    code, out, err = recipe(capsys, HHH, brain=brain)
    pipelines = load_config(brain).pipelines
    assert code == 0, err and [p.name for p in pipelines] == [HHH]
    assert pipelines[0].sources[0].options["jql"] == 'project = HHH AND updated >= "2026-09-17"'
    assert len(list((brain.root / HHH).rglob("*.md"))) == 3


def test_an_issue_that_fell_out_of_a_rolling_window_is_not_deleted_on_a_rerun(brain, hibernate, capsys, monkeypatch):
    recipe(capsys, HHH, brain=brain)
    hibernate.now = hibernate.now.replace(year=2027)         # time passes; nobody touches the issues
    monkeypatch.setattr(recipes, "today", lambda: date(2027, 1, 1))
    code, _, err = recipe(capsys, HHH, brain=brain)
    assert code == 0, err and len(list((brain.root / HHH).rglob("*.md"))) == 3


def test_a_sync_that_stops_part_way_finishes_on_the_rerun_without_duplicates(brain, hibernate, capsys):
    real = hibernate.__call__
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 9:                              # during the reading of the third issue, after two files were written
            raise OSError("connection dropped")
        return real(*a, **k)

    jira_api.set_transport(flaky, sleep=lambda s: None)
    code, out, err = recipe(capsys, HHH, brain=brain)
    assert code != 0 and "Stopped after 2 of 3 issues. Run the same command again to finish." in err
    jira_api.set_transport(hibernate, sleep=lambda s: None)
    code, out, err = recipe(capsys, HHH, brain=brain)
    assert code == 0, err and len(list((brain.root / HHH).rglob("*.md"))) == 3 and [p.name for p in load_config(brain).pipelines] == [HHH]


def test_no_network_says_what_happened_then_the_one_thing_to_do(brain, hibernate, capsys):
    def down(*a, **k):
        raise OSError("down")

    jira_api.set_transport(down, sleep=lambda s: None)
    code, _, err = recipe(capsys, HHH, brain=brain)
    assert code != 0 and "Couldn't reach hibernate.atlassian.net. Check your internet connection, then run the same command again." in err


def test_a_site_that_stops_being_public_says_so_and_points_to_the_list(brain, hibernate, capsys):
    hibernate.public = False
    code, _, err = recipe(capsys, HHH, brain=brain)
    assert code != 0 and "hibernate.atlassian.net no longer lets anyone read HHH without a login, so this recipe can't run. Try another one: mdpipeline recipe list" in err


def test_a_recipe_pipeline_is_an_ordinary_pipeline_afterwards(brain, hibernate, capsys):
    recipe(capsys, HHH, brain=brain)
    hibernate.add_issue("HHH", "a new one")
    assert main(["--brain", str(brain.root), "sync", HHH]) == 0
    assert len(list((brain.root / HHH).rglob("*.md"))) == 4


# -- AC11: "no setup questions" still keeps the one-time usage-consent question (FR-S1) -------------------------------------------------------------

@pytest.fixture
def first_run(monkeypatch, hibernate):
    """An interactive first run: nothing answered yet. Returns the log of what happened, in order."""
    import json

    from mdpipeline import cli, usage

    (usage.data_dir() / "usage.json").write_text(json.dumps({"counter_eligible": True}), encoding="utf-8")
    log: list[str] = []
    monkeypatch.setattr(cli, "interactive", lambda: True)
    monkeypatch.setattr(cli, "stderr_visible", lambda: True)
    answers = iter(["s"])
    monkeypatch.setattr("builtins.input", lambda *a: log.append("asked") or next(answers, "s"))
    real = hibernate.__call__
    monkeypatch.setattr(type(hibernate), "__call__", lambda self, *a, **k: (log.append("jira") if "jira" not in log else None, real(*a, **k))[1])
    return log


def test_an_interactive_first_run_asks_the_consent_question_once_before_the_sync(brain, first_run, capsys):
    recipe(capsys, HHH, brain=brain)
    assert first_run.count("asked") == 1 and first_run.index("asked") < first_run.index("jira")
    recipe(capsys, HHH, brain=brain)
    assert first_run.count("asked") == 1


def test_a_no_to_the_consent_question_sends_nothing(brain, first_run, capsys, monkeypatch):
    from test_usage_cli_events import queued

    monkeypatch.setattr("builtins.input", lambda *a: "d")
    code, _, err = recipe(capsys, HHH, brain=brain)
    assert code == 0, err and queued() == []


def test_a_yes_to_the_consent_question_lets_events_through(brain, first_run, capsys):
    from test_usage_cli_events import queued

    recipe(capsys, HHH, brain=brain)
    assert queued("app_opened")
