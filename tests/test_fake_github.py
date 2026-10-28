"""MD-59: the fake GitHub used by every GitHub-target test (in-memory repo with refs, scriptable races and failures)."""
import base64
import json

import pytest

from fake_github import FakeGitHub


def call(fake, method, path, body=None, token="tok", query=""):
    url = f"https://api.github.com{path}{query}"
    status, headers, data = fake(method, url, {"Authorization": f"Bearer {token}"}, json.dumps(body).encode() if body is not None else None)
    return status, json.loads(data) if data else None


@pytest.fixture
def fake():
    return FakeGitHub(repo="acme/brain", branch="main", token="tok", files={"wiki/a.md": b"wiki"})


def commit_files(fake, files, deletes=(), parent=None):
    """Create a commit through the REST calls, as a client would (returns the commit sha, ref not moved)."""
    head = parent or call(fake, "GET", "/repos/acme/brain/git/ref/heads/main")[1]["object"]["sha"]
    base = call(fake, "GET", f"/repos/acme/brain/git/commits/{head}")[1]["tree"]["sha"]
    entries = []
    for path, data in files.items():
        blob = call(fake, "POST", "/repos/acme/brain/git/blobs", {"content": base64.b64encode(data).decode(), "encoding": "base64"})[1]["sha"]
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob})
    entries += [{"path": p, "mode": "100644", "type": "blob", "sha": None} for p in deletes]
    tree = call(fake, "POST", "/repos/acme/brain/git/trees", {"base_tree": base, "tree": entries})[1]["sha"]
    return call(fake, "POST", "/repos/acme/brain/git/commits", {"message": "m", "tree": tree, "parents": [head]})[1]["sha"]


def test_unknown_token_is_401(fake):
    assert call(fake, "GET", "/user", token="bad")[0] == 401


def test_user_and_repo_report_the_token_owner_and_push_permission(fake):
    assert call(fake, "GET", "/user")[1]["login"] == "tester"
    repo = call(fake, "GET", "/repos/acme/brain")[1]
    assert repo["full_name"] == "acme/brain" and repo["permissions"]["push"] is True


def test_a_repo_the_token_cannot_see_is_404(fake):
    assert call(fake, "GET", "/repos/other/thing")[0] == 404


def test_branch_reports_head_and_protection(fake):
    fake.protect("main")
    b = call(fake, "GET", "/repos/acme/brain/branches/main")[1]
    assert b["protected"] is True and b["commit"]["sha"] == fake.head()
    assert call(fake, "GET", "/repos/acme/brain/branches/nope")[0] == 404


def test_tree_lists_files_recursively_and_blob_returns_content(fake):
    commit = call(fake, "GET", f"/repos/acme/brain/git/commits/{fake.head()}")[1]
    tree = call(fake, "GET", f"/repos/acme/brain/git/trees/{commit['tree']['sha']}", query="?recursive=1")[1]
    entry = next(e for e in tree["tree"] if e["path"] == "wiki/a.md")
    blob = call(fake, "GET", f"/repos/acme/brain/git/blobs/{entry['sha']}")[1]
    assert base64.b64decode(blob["content"]) == b"wiki" and tree["truncated"] is False


def test_a_fast_forward_ref_update_moves_the_branch(fake):
    new = commit_files(fake, {"raw/x.md": b"x"})
    status, _ = call(fake, "PATCH", "/repos/acme/brain/git/refs/heads/main", {"sha": new, "force": False})
    assert status == 200 and fake.head() == new and fake.read("raw/x.md") == b"x"


def test_deleting_through_a_null_sha_removes_the_path(fake):
    new = commit_files(fake, {}, deletes=["wiki/a.md"])
    call(fake, "PATCH", "/repos/acme/brain/git/refs/heads/main", {"sha": new})
    assert fake.read("wiki/a.md") is None


def test_a_non_fast_forward_update_is_422_and_never_applied(fake):
    old = fake.head()
    mine = commit_files(fake, {"raw/x.md": b"x"})
    fake.commit_as_other({"wiki/b.md": b"b"}, "someone else")
    status, body = call(fake, "PATCH", "/repos/acme/brain/git/refs/heads/main", {"sha": mine})
    assert status == 422 and "fast forward" in body["message"].lower()
    assert fake.read("raw/x.md") is None and fake.read("wiki/b.md") == b"b" and fake.head() != old


def test_a_forced_update_is_recorded_so_tests_can_prove_none_happened(fake):
    new = commit_files(fake, {"raw/x.md": b"x"})
    call(fake, "PATCH", "/repos/acme/brain/git/refs/heads/main", {"sha": new, "force": True})
    assert fake.forced_updates == 1


def test_protected_branch_refuses_direct_updates(fake):
    fake.protect("main")
    new = commit_files(fake, {"raw/x.md": b"x"})
    assert call(fake, "PATCH", "/repos/acme/brain/git/refs/heads/main", {"sha": new})[0] == 403


def test_read_only_token_cannot_create_blobs(fake):
    fake.set_permission("read")
    assert call(fake, "POST", "/repos/acme/brain/git/blobs", {"content": "eA==", "encoding": "base64"})[0] == 403


def test_commit_as_other_hook_runs_before_the_matching_request(fake):
    fake.before("PATCH", lambda: fake.commit_as_other({"wiki/c.md": b"c"}, "race"))
    new = commit_files(fake, {"raw/x.md": b"x"})
    assert call(fake, "PATCH", "/repos/acme/brain/git/refs/heads/main", {"sha": new})[0] == 422


def test_scripted_failures_and_rate_limits(fake):
    fake.fail("GET", "/user", status=500, times=1)
    assert call(fake, "GET", "/user")[0] == 500 and call(fake, "GET", "/user")[0] == 200
    fake.rate_limit(times=1)
    assert call(fake, "GET", "/user")[0] == 403


def test_history_lists_commits_touching_a_path_newest_first(fake):
    a = fake.commit_as_other({"s/state.json": b"1"}, "one")
    b = fake.commit_as_other({"s/state.json": b"2", "other": b"o"}, "two")
    fake.commit_as_other({"other": b"p"}, "three")
    status, body = call(fake, "GET", "/repos/acme/brain/commits", query="?path=s/state.json&sha=main&per_page=10")
    assert [c["sha"] for c in body] == [b, a]


def test_tree_truncation_is_scriptable(fake):
    fake.max_tree_entries = 1
    fake.commit_as_other({"a": b"a", "b": b"b"}, "many")
    commit = call(fake, "GET", f"/repos/acme/brain/git/commits/{fake.head()}")[1]
    assert call(fake, "GET", f"/repos/acme/brain/git/trees/{commit['tree']['sha']}", query="?recursive=1")[1]["truncated"] is True


def test_history_of_the_branch_is_linear_for_the_fast_forward_proof(fake):
    fake.commit_as_other({"a": b"a"}, "x")
    assert fake.is_linear_history()


def test_a_branch_can_be_created_and_deleted_and_rate_limit_is_readable(fake):
    head = call(fake, "GET", "/repos/acme/brain/git/ref/heads/main")[1]["object"]["sha"]
    status, ref = call(fake, "POST", "/repos/acme/brain/git/refs", {"ref": "refs/heads/work", "sha": head})
    assert status == 201 and ref["ref"] == "refs/heads/work" and fake.head("work") == head
    assert call(fake, "POST", "/repos/acme/brain/git/refs", {"ref": "refs/heads/work", "sha": head})[0] == 422        # already exists
    assert call(fake, "DELETE", "/repos/acme/brain/git/refs/heads/work")[0] == 204
    assert call(fake, "GET", "/repos/acme/brain/git/ref/heads/work")[0] == 404
    status, limits = call(fake, "GET", "/rate_limit")
    assert status == 200 and limits["resources"]["core"]["remaining"] <= limits["resources"]["core"]["limit"]


def test_responses_carry_rate_limit_headers_like_github(fake):
    status, headers, _ = fake("GET", "https://api.github.com/user", {"Authorization": "Bearer tok"}, None)
    assert status == 200 and int(headers["x-ratelimit-remaining"]) >= 0 and "x-ratelimit-limit" in headers
