"""MD-118: the guided first run, backend. Defaults everywhere, a review before anything is written, resume state."""
import os

from mdpipeline import firstrun


def test_default_folder_is_under_markdown_pipeline_with_a_cli_friendly_name():
    assert firstrun.default_folder("Acme Jira!") == "~/markdown-pipeline/acme-jira"


def test_default_folder_never_has_an_empty_name():
    assert firstrun.default_folder("???") == "~/markdown-pipeline/source"


def test_folder_draft_takes_every_default_and_names_the_pipeline_from_the_source(tmp_path):
    src = tmp_path / "team-wiki"
    src.mkdir()
    d = firstrun.build_draft({"source": "folder", "path": str(src)})
    assert d["name"] == "team-wiki"
    assert d["sources"] == [{"path": str(src), "label": "team-wiki"}]
    assert d["destination"] == {"type": "directory", "path": os.path.expanduser("~/markdown-pipeline/team-wiki")}


def test_a_chosen_destination_replaces_the_default(tmp_path):
    src = tmp_path / "notes"
    src.mkdir()
    d = firstrun.build_draft({"source": "folder", "path": str(src), "dest": str(tmp_path / "out")})
    assert d["destination"]["path"] == str(tmp_path / "out")


def test_jira_draft_uses_the_site_label_and_the_90_day_default_query():
    d = firstrun.build_draft({"source": "jira", "site": "https://Acme.atlassian.net/jira/", "email": "d@acme.dev", "token": "t"})
    assert d["name"] == "acme-jira"
    s = d["sources"][0]
    assert (s["type"], s["site"], s["jql"]) == ("jira", "https://acme.atlassian.net", firstrun.DEFAULT_JQL)
    assert firstrun.DEFAULT_JQL == "updated >= -90d ORDER BY updated DESC"


# review: what will be written, shown before anything is written ------------------------------------
import json  # noqa: E402

import pytest  # noqa: E402

from fake_jira import FakeJira  # noqa: E402
from mdpipeline import jira_api  # noqa: E402
from test_jira_sync import EMAIL, TOKEN  # noqa: E402
from uihelper import running_ui  # noqa: E402


@pytest.fixture
def notes(make_source):
    return make_source("team-wiki", {f"n{i}.md": f"# Note {i}\nbody {i}\n" for i in range(7)})


def test_review_of_a_folder_counts_the_files_lists_five_titles_and_shows_an_example(brain, notes, tmp_path):
    dest = tmp_path / "out"
    with running_ui(brain) as ui:
        status, res = ui.post("/api/first-run/review", {"source": "folder", "path": str(notes), "dest": str(dest)})
    assert status == 200 and res["ok"] is True
    assert (res["count"], res["noun"], res["more"]) == (7, "files", 2)
    assert res["first"] == [f"n{i}.md" for i in range(5)]
    assert res["dest"] == {"path": str(dest), "exists": False}
    assert res["read_only"] == "Read-only: we never change anything in this folder."
    assert res["example"]["name"] == "n0.md" and "# Note 0" in res["example"]["text"]
    assert "source_label: team-wiki" in res["example"]["text"]          # the example is the file as it will be written, provenance block included


def test_review_writes_nothing(brain, notes, tmp_path):
    dest = tmp_path / "out"
    with running_ui(brain) as ui:
        ui.post("/api/first-run/review", {"source": "folder", "path": str(notes), "dest": str(dest)})
    assert not dest.exists() and not list(brain.root.glob("raw/*"))
    assert [p.name for p in brain.config_dir.iterdir() if p.suffix == ".yaml"] == ["config.yaml"] and "pipelines: []" in (brain.config_dir / "config.yaml").read_text()


def test_review_of_a_missing_folder_says_so_instead_of_previewing(brain, tmp_path):
    with running_ui(brain) as ui:
        _, res = ui.post("/api/first-run/review", {"source": "folder", "path": str(tmp_path / "nope")})
    assert res["ok"] is False and res["errors"] and all(e["message"] for e in res["errors"])


@pytest.fixture
def jira(monkeypatch):
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    for n in range(6):
        f.add_issue("ACME", f"Issue number {n}")
    jira_api.set_transport(f, sleep=lambda s: None)
    yield f
    jira_api.set_transport(None)


def test_review_of_jira_shows_issue_titles_and_a_real_example_file(brain, jira):
    with running_ui(brain) as ui:
        _, res = ui.post("/api/first-run/review", {"source": "jira", "site": "acme", "email": EMAIL, "token": TOKEN})
    assert res["ok"] is True and (res["count"], res["noun"]) == (6, "issues")
    assert len(res["first"]) == 5 and res["first"][0].startswith("ACME-1 ") and ".md" not in res["first"][0]
    assert res["read_only"] == "Read-only: we never change anything in Jira."
    assert "ACME-1" in res["example"]["text"] and TOKEN not in json.dumps(res)


# write: the one click that creates the folder and the pipeline and runs the first sync -------------------
from mdpipeline.config import load_config  # noqa: E402


def test_write_creates_the_folder_the_pipeline_and_the_files_with_default_schedule(brain, notes, tmp_path):
    dest = tmp_path / "new" / "wiki"
    with running_ui(brain) as ui:
        status, res = ui.post("/api/first-run/write", {"source": "folder", "path": str(notes), "dest": str(dest)})
    assert status == 200 and res["ok"] is True
    assert (res["files"], res["folder"], res["pipeline"]) == (7, str(dest), "team-wiki")
    assert len(list(dest.rglob("*.md"))) == 7
    assert res["schedule"] == "1h" and res["next_sync"] > 0
    p = load_config(brain).get("team-wiki")
    assert p.schedule == "1h" and p.destination["path"] == str(dest)


def test_write_uses_the_review_defaults_when_nothing_is_changed(brain, notes, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    with running_ui(brain) as ui:
        _, res = ui.post("/api/first-run/write", {"source": "folder", "path": str(notes)})
    assert res["folder"] == str(tmp_path / "home" / "markdown-pipeline" / "team-wiki")


def test_write_picks_a_free_name_when_the_source_name_is_taken(brain, notes, tmp_path):
    other = tmp_path / "elsewhere" / "team-wiki"
    other.mkdir(parents=True)
    (other / "x.md").write_text("x")
    with running_ui(brain) as ui:
        ui.post("/api/first-run/write", {"source": "folder", "path": str(notes), "dest": str(tmp_path / "a")})
        _, res = ui.post("/api/first-run/write", {"source": "folder", "path": str(other), "dest": str(tmp_path / "b")})
    assert res["ok"] is True and res["pipeline"] == "team-wiki-2"


def test_write_of_a_missing_folder_changes_nothing_and_says_why(brain, tmp_path):
    dest = tmp_path / "out"
    with running_ui(brain) as ui:
        _, res = ui.post("/api/first-run/write", {"source": "folder", "path": str(tmp_path / "nope"), "dest": str(dest)})
    assert res["ok"] is False and res["errors"] and not dest.exists() and load_config(brain).pipelines == []


def test_write_from_jira_keeps_the_token_in_the_vault_not_in_the_config(brain, jira, tmp_path):
    with running_ui(brain) as ui:
        _, res = ui.post("/api/first-run/write", {"source": "jira", "site": "acme", "email": EMAIL, "token": TOKEN, "dest": str(tmp_path / "j")})
    assert res["ok"] is True and res["files"] == 6
    assert TOKEN not in (brain.config_dir / "config.yaml").read_text() and TOKEN not in json.dumps(res)


# resume: leave and come back ---------------------------------------------------------------------------
def test_progress_is_empty_until_something_is_saved(brain):
    with running_ui(brain) as ui:
        _, res = ui.get("/api/first-run/progress")
    assert res["progress"] is None


def test_progress_keeps_the_step_and_what_was_typed_but_never_the_token(brain, tmp_path):
    body = {"step": "connect", "source": "jira", "site": "acme", "email": "d@acme.dev", "token": TOKEN, "password": "x", "unknown": 1}
    with running_ui(brain) as ui:
        ui.post("/api/first-run/progress", body)
        _, res = ui.get("/api/first-run/progress")
    assert res["defaults"] == {"jql": firstrun.DEFAULT_JQL, "token_page": "https://id.atlassian.com/manage-profile/security/api-tokens"}
    assert res["progress"] == {"step": "connect", "source": "jira", "site": "acme", "email": "d@acme.dev"}
    assert TOKEN not in (firstrun.progress_path()).read_text()


def test_start_over_forgets_the_progress(brain):
    with running_ui(brain) as ui:
        ui.post("/api/first-run/progress", {"step": "review", "source": "folder", "path": "/x"})
        ui.post("/api/first-run/progress", {"clear": True})
        _, res = ui.get("/api/first-run/progress")
    assert res["progress"] is None


def test_a_successful_write_ends_the_first_run_so_nobody_is_welcomed_back(brain, notes, tmp_path):
    with running_ui(brain) as ui:
        ui.post("/api/first-run/progress", {"step": "review", "source": "folder", "path": str(notes)})
        ui.post("/api/first-run/write", {"source": "folder", "path": str(notes), "dest": str(tmp_path / "o")})
        _, res = ui.get("/api/first-run/progress")
    assert res["progress"] is None


# the Ready screen: Open folder, and the background announcement is not shown twice ---------------------
def test_open_folder_opens_the_destination_of_that_pipeline_only(brain, notes, tmp_path):
    opened = []
    dest = tmp_path / "out"
    with running_ui(brain, opener=opened.append) as ui:
        ui.post("/api/first-run/write", {"source": "folder", "path": str(notes), "dest": str(dest)})
        _, res = ui.post("/api/first-run/open-folder", {"pipeline": "team-wiki"})
        status, _ = ui.post("/api/first-run/open-folder", {"pipeline": "nope"})
    assert res["ok"] is True and opened == [str(dest)] and status == 404


def test_the_first_write_acknowledges_the_background_announcement_because_the_ready_line_says_it(brain, notes, tmp_path):
    with running_ui(brain) as ui:
        ui.post("/api/first-run/write", {"source": "folder", "path": str(notes), "dest": str(tmp_path / "o")})
        _, state = ui.get("/api/state")
    assert state["background"]["announce_pending"] is False


# MD-119: the guided Jira step: one specific, actionable message per failure -----------------------------
def jira_body(**kw):
    return {"site": "acme", "email": EMAIL, "token": TOKEN, **kw}


def test_a_good_jira_token_returns_the_account_name_and_a_handle_never_the_token(brain, jira):
    with running_ui(brain) as ui:
        _, res = ui.post("/api/first-run/jira-test", jira_body())
    assert res["ok"] is True and res["user"] == "Mia Owner" and res["pending"] and TOKEN not in json.dumps(res)


def test_a_bad_token_or_wrong_email_gets_one_combined_message(brain, jira):
    with running_ui(brain) as ui:
        _, res = ui.post("/api/first-run/jira-test", jira_body(token="wrong"))
    assert (res["ok"], res["kind"], res["field"]) == (False, "auth", "credentials")
    assert res["headline"] == "Atlassian didn't accept this email and token together."
    assert "created the token" in res["fix"] and "wrong" not in json.dumps(res)


def test_a_site_that_does_not_exist_names_the_address_and_says_how_to_find_it(brain, jira):
    jira.fail("GET", "/myself", status=404, times=5)
    with running_ui(brain) as ui:
        _, res = ui.post("/api/first-run/jira-test", jira_body())
    assert (res["kind"], res["field"]) == ("site", "site")
    assert "acme.atlassian.net" in res["headline"] and "paste the full address" in res["fix"].lower()


def test_an_account_that_sees_no_project_says_who_is_signed_in(brain, monkeypatch):
    empty = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    jira_api.set_transport(empty, sleep=lambda s: None)
    try:
        with running_ui(brain) as ui:
            _, res = ui.post("/api/first-run/jira-test", jira_body())
    finally:
        jira_api.set_transport(None)
    assert res["kind"] == "no_projects" and "Mia Owner" in res["headline"] and "Browse projects" in res["fix"]


def test_a_non_atlassian_address_is_called_out_as_data_center_or_server(brain, jira):
    with running_ui(brain) as ui:
        _, res = ui.post("/api/first-run/jira-test", jira_body(site="jira.example.com"))
    assert res["kind"] == "data_center" and "Jira Cloud only" in res["fix"]


def test_no_internet_says_nothing_was_saved(brain):
    def offline(method, url, headers, body):
        raise OSError("unreachable")

    jira_api.set_transport(offline, sleep=lambda s: None)
    try:
        with running_ui(brain) as ui:
            _, res = ui.post("/api/first-run/jira-test", jira_body())
    finally:
        jira_api.set_transport(None)
    assert res["kind"] == "offline" and "Nothing was saved" in res["fix"]


def test_an_atlassian_outage_is_not_blamed_on_the_connection(brain, jira):
    jira.fail("GET", "/myself", status=503, times=9)
    with running_ui(brain) as ui:
        _, res = ui.post("/api/first-run/jira-test", jira_body())
    assert res["kind"] == "unavailable" and "not available" in res["headline"]


def test_every_jira_failure_carries_the_troubleshooting_topic(brain, jira):
    with running_ui(brain) as ui:
        _, res = ui.post("/api/first-run/jira-test", jira_body(token="wrong"))
        status, doc = ui.get("/jira-token-help.md")
    assert res["help"] == "/jira-token-help.md" and status == 200 and "Create API token" in doc


# MD-153: Review is fast (no per-issue body fetch) and its count is the number of issues the query matches ----
def _body_fetches(fake):
    """Requests that read ONE issue's body or comments (the search and count calls are not these)."""
    return [p for _, p in fake.requests if "/rest/api/3/issue/" in p]


def test_review_of_500_issues_fetches_no_issue_bodies_except_the_one_example(brain, monkeypatch):
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    for n in range(500):
        f.add_issue("ACME", f"Issue number {n}")
    jira_api.set_transport(f, sleep=lambda s: None)
    try:
        with running_ui(brain) as ui:
            _, res = ui.post("/api/first-run/review", {"source": "jira", "site": "acme", "email": EMAIL, "token": TOKEN})
    finally:
        jira_api.set_transport(None)
    assert res["ok"] is True and res["count"] == 500                         # the count is what the query matches
    assert len(_body_fetches(f)) <= 2, _body_fetches(f)[:5]                  # one issue + its comments, for the example file
    assert res["example"] and "ACME-" in res["example"]["text"]
    assert len(res["first"]) == 5 and res["more"] == 495


def test_review_count_after_a_pick_is_the_number_the_query_matches_and_write_writes_that_many(brain, tmp_path):
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    for n in range(4):
        f.add_issue("AAA", f"A {n}")
    for n in range(7):
        f.add_issue("BBB", f"B {n}")
    jira_api.set_transport(f, sleep=lambda s: None)
    body = {"source": "jira", "site": "acme", "email": EMAIL, "token": TOKEN, "jql": "project = BBB ORDER BY updated DESC", "dest": str(tmp_path / "out")}
    try:
        with running_ui(brain) as ui:
            _, rev = ui.post("/api/first-run/review", body)
            _, res = ui.post("/api/first-run/write", body)
    finally:
        jira_api.set_transport(None)
    assert rev["count"] == 7 and rev["jql"] == body["jql"]
    assert res["files"] == 7 and len(list((tmp_path / "out").rglob("*.md"))) == 7
