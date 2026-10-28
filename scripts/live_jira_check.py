"""Live verification of the Jira connector against a REAL Jira Cloud site (MD-74).

Read-only: it never changes Jira (the connector's client cannot send anything but the allow-listed reads). Three modes:

    uv run python scripts/live_jira_check.py check  --site https://acme.atlassian.net --email you@acme.com --jql "project in (MD, MP)"
    uv run python scripts/live_jira_check.py record --site ... --email ... --jql ... --out tests/fixtures/jira/real --limit 25
    uv run python scripts/live_jira_check.py perf   --site ... --email ... --jql ... --changed 20 --backfill 500

The API token is read from a hidden prompt (or one line of stdin with --stdin, or a vault secret with --use-secret ID); never an argument.
``check`` runs the checklist of docs/jira-connector-spike.md that can be automated and prints PASS / FAIL / MANUAL per item; every FAIL or
shape difference from the synthetic corpus is a divergence to file as a bug. ``record`` writes a SANITISED corpus (people, e-mails, account
ids, urls and all free text are replaced; the ADF structure is kept) that is safe to commit as real-schema fixtures. ``perf`` measures MD-50 AC5.
"""
from __future__ import annotations

import argparse
import contextlib
import getpass
import json
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mdpipeline import jira_api  # noqa: E402
from mdpipeline.brain import init_brain  # noqa: E402
from mdpipeline.config import Pipeline, Source, load_config, save_config  # noqa: E402
from mdpipeline.engine import sync_pipeline  # noqa: E402
from mdpipeline.jira_api import JiraApi  # noqa: E402
from mdpipeline.jira_render import render_issue  # noqa: E402
from mdpipeline.sources import SourceError  # noqa: E402
from mdpipeline.state import State  # noqa: E402

PASS, FAIL, MANUAL = "PASS", "FAIL", "MANUAL"
EMAIL_VAR, TOKEN_VAR = "MDP_LIVE_JIRA_EMAIL", "MDP_LIVE_JIRA_TOKEN"        # set for this process only
KNOWN_NODES = {
    "doc", "paragraph", "heading", "bulletList", "orderedList", "listItem", "taskList", "taskItem", "decisionList", "decisionItem", "codeBlock",
    "blockquote", "panel", "rule", "table", "tableRow", "tableHeader", "tableCell", "mediaSingle", "mediaGroup", "media", "expand", "nestedExpand",
    "blockCard", "layoutSection", "layoutColumn", "text", "hardBreak", "mention", "emoji", "date", "status", "inlineCard", "mediaInline"}
LIST_FIELDS = "summary,created,updated"


@dataclass
class Result:
    item: str
    title: str
    status: str
    detail: str = ""
    divergences: list[str] = field(default_factory=list)


def say(results: list[Result], item: str, title: str, status: str, detail: str = "", divergences: list[str] | None = None) -> None:
    results.append(Result(item, title, status, detail, divergences or []))
    print(f"[{status}] {item} {title}" + (f": {detail}" if detail else ""), flush=True)
    for d in divergences or []:
        print(f"        DIVERGENCE: {d}", flush=True)


_KEY = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")


def failures(res, limit: int = 20) -> list[str]:
    """MD-151 AC1: one line per issue that failed in a sync: its key and the reason, never its content (keys only)."""
    out = []
    for e in res.errors:
        found = _KEY.search(str(e.get("path", ""))) or _KEY.search(str(e.get("error", "")))
        reason = _KEY.sub("", str(e.get("error", "")))
        reason = re.sub(r"\s+", " ", reason).strip(" :")[:120]
        out.append(f"{found.group(0) if found else 'unknown key'}: {e.get('category') or 'file-error'}: {reason}")
    more = len(out) - limit
    return out[:limit] + ([f"... and {more} more"] if more > 0 else [])


# -- sanitiser ------------------------------------------------------------------------------------------------------------
class Sanitizer:
    """Replaces everything personal or sensitive in a Jira REST answer and keeps its structure: names, e-mails, account ids, urls and all text."""

    def __init__(self) -> None:
        self.people: dict[str, str] = {}
        self.urls: dict[str, str] = {}

    def person(self, name: str) -> str:
        return self.people.setdefault(name, f"Person {len(self.people) + 1}")

    def url(self, value: str) -> str:
        return self.urls.setdefault(value, f"https://example.invalid/r/{len(self.urls) + 1}")

    @staticmethod
    def text(value: str) -> str:
        """Same length and punctuation, letters and digits replaced (so list/table/code shapes survive)."""
        return re.sub(r"[A-Za-z]", lambda m: "x" if m.group().islower() else "X", re.sub(r"\d", "0", value))

    def walk(self, node, key: str = "", parent_type: str = ""):
        if isinstance(node, dict):
            out = {}
            node_type = node.get("type", "") if isinstance(node.get("type"), str) else parent_type          # an "attrs" map belongs to its node
            for k, v in node.items():
                if k in ("emailAddress", "avatarUrls", "accountId", "timeZone", "self", "avatarId", "ari", "accountType"):
                    if k == "accountId":
                        out[k] = self.person(str(v)).replace("Person", "acct")
                    elif k == "emailAddress":
                        out[k] = "person@example.invalid"
                    elif k == "self":
                        out[k] = self.url(str(v))
                    elif k != "avatarUrls":
                        out[k] = v
                    continue
                if k in ("displayName", "name") and isinstance(v, str) and ("accountId" in node or "emailAddress" in node):
                    out[k] = self.person(v)
                elif k == "text" and node_type == "text" and isinstance(v, str):
                    out[k] = self.text(v)
                elif k == "text" and node_type in ("mention", "emoji", "status") and isinstance(v, str):
                    out[k] = "@" + self.person(v.lstrip("@")) if node_type == "mention" else self.text(v)
                elif k in ("href", "url") and isinstance(v, str):
                    out[k] = self.url(v)
                elif k in ("summary", "title", "alt", "filename", "description", "name", "environment") and isinstance(v, str) and k != "name":
                    out[k] = self.text(v)
                elif k == "filename" and isinstance(v, str):
                    out[k] = self.text(v)
                else:
                    out[k] = self.walk(v, k, node_type)
            return out
        if isinstance(node, list):
            return [self.walk(v, key, parent_type) for v in node]
        if isinstance(node, str):
            if re.match(r"https?://", node):
                return self.url(node)                          # any address (attachment content, thumbnails, links) becomes an opaque one
            if key in ("summary", "value", "label", "body"):
                return self.text(node)
        return node


# -- helpers -----------------------------------------------------------------------------------------------------------------
def adf_node_types(doc) -> dict[str, int]:
    """How many nodes of each ADF type a document has (nodes only: marks and attribute values are not node types)."""
    counts: dict[str, int] = {}

    def walk(node):
        if isinstance(node, dict) and isinstance(node.get("type"), str):
            counts[node["type"]] = counts.get(node["type"], 0) + 1
            for child in node.get("content") or []:
                walk(child)

    walk(doc)
    return counts


def keys_missing(issue: dict) -> list[str]:
    """Keys the connector reads from an issue answer that are absent (a divergence from the synthetic corpus)."""
    f = issue.get("fields") or {}
    required = {"id": "id" in issue, "key": "key" in issue, "fields.summary": "summary" in f, "fields.created": "created" in f, "fields.updated": "updated" in f,
                "fields.issuetype": "issuetype" in f, "fields.status": "status" in f, "fields.project": "project" in f}
    return [k for k, present in required.items() if not present]


class Live:
    def __init__(self, site: str, email: str, token: str, jql: str, transport=None, sleep=None) -> None:
        self.site, self.email, self.token, self.jql = site.rstrip("/"), email, token, jql
        if transport:
            jira_api.set_transport(transport, sleep=sleep or (lambda s: None))
        self.api = JiraApi(self.site, email, token)
        self.requests = 0
        os.environ[EMAIL_VAR], os.environ[TOKEN_VAR] = email, token

    def source(self) -> Source:
        return Source(self.site, "jira", type="jira", options={"site": self.site, "jql": self.jql, "email_ref": f"env:{EMAIL_VAR}", "token_ref": f"env:{TOKEN_VAR}"})

    def brain(self, name: str, tmp: Path, jql: str | None = None):
        root = tmp / name
        (root / "raw").mkdir(parents=True, exist_ok=True)
        brain = init_brain(root)
        src = self.source()
        if jql:
            src.options["jql"] = jql
        cfg = load_config(brain)
        cfg.pipelines.append(Pipeline("p", [src], name_policy="date-name", guard_threshold_percent=100, guard_min_files=5))
        save_config(brain, cfg)
        return brain

    def done(self) -> None:
        os.environ.pop(EMAIL_VAR, None)
        os.environ.pop(TOKEN_VAR, None)
        jira_api.set_transport(None)


# -- check -------------------------------------------------------------------------------------------------------------------
def run_check(live: Live, limit: int) -> list[Result]:
    results: list[Result] = []
    try:
        me = live.api.myself()
    except SourceError as exc:
        say(results, "J1", "authentication (GET /myself)", FAIL, str(exc))
        return results
    say(results, "J1", "authentication (GET /myself)", PASS if me.get("displayName") else FAIL, f"display name present: {bool(me.get('displayName'))}",
        [] if me.get("displayName") else ["/myself has no displayName (test connection shows it)"])
    try:
        JiraApi(live.site, live.email, live.token + "x").myself()
        say(results, "J1b", "a wrong token is refused as auth", FAIL, "a wrong token was accepted")
    except SourceError as exc:
        say(results, "J1b", "a wrong token is refused as auth", PASS if exc.category == "auth" else FAIL, f"category={exc.category}",
            [] if exc.category == "auth" else [f"a wrong token gave '{exc.category}', not 'auth' (the connector maps 401 to auth)"])
    try:
        issues = live.api.list_issues(live.jql, LIST_FIELDS, 100)
        small = live.api.list_issues(live.jql, LIST_FIELDS, 7)
        total = live.api.count(live.jql)
    except SourceError as exc:
        say(results, "J2", "JQL listing, paging and approximate count", FAIL, str(exc))
        return results
    ok = {i["id"] for i in issues} == {i["id"] for i in small}
    say(results, "J2", "JQL listing: pages of 100 and of 7 return the same issues; approximate count", PASS if ok else FAIL,
        f"{len(issues)} issues; count says {total}", [] if ok else ["different pages gave different issue sets: cursor paging is not what the connector assumes"])
    sample = issues[:limit]
    warnings, unknown, histogram, shape = 0, set(), {}, []
    for it in sample:
        key = str(it.get("key") or it["id"])
        try:
            full = live.api.issue(it["id"])
            comments = live.api.comments(it["id"])
            shape += [f"{full.get('key', it['id'])}: missing {m}" for m in keys_missing(full)]
            docs = [(full.get("fields") or {}).get("description")] + [c.get("body") for c in comments]
            for doc in docs:
                for name, n in adf_node_types(doc).items():
                    histogram[name] = histogram.get(name, 0) + n
                    if name not in KNOWN_NODES:
                        unknown.add(name)
            warnings += render_issue(full, comments, live.site).warnings
        except Exception as exc:          # one bad issue is a named FAIL line, never a crash (MD-151 AC1)
            shape.append(f"{key}: {type(exc).__name__}: {_KEY.sub('', str(exc))[:120]}")
    say(results, "J3", f"issue + comments of {len(sample)} issues render to Markdown", PASS if not shape else FAIL, f"conversion warnings={warnings}; node types seen: " +
        ", ".join(f"{k}:{v}" for k, v in sorted(histogram.items())), shape[:10])
    say(results, "J3b", "every ADF node type seen is one the converter handles", PASS if not unknown else FAIL, f"unknown: {sorted(unknown) or 'none'}",
        [f"ADF node type '{n}' is not handled (kept as marked text): add it with a golden test" for n in sorted(unknown)])
    tmp = Path(tempfile.mkdtemp(prefix="mdp-jira-live-"))
    try:
        brain = live.brain("b", tmp)
        first = sync_pipeline(brain, "p", trigger="cli")
        before = live.api.__class__  # noqa: F841 (the second sync is measured through the request counter below)
        second = sync_pipeline(brain, "p", trigger="cli", dry_run=True)
        say(results, "J4", "a first sync, then a plan with nothing changed fetches no issue bodies", PASS if first.result in ("ok", "conflicts") and second.counts()["created"] + second.counts()["updated"] == 0 else FAIL,
            f"first={first.result} {first.counts()['created']} created; second plan: updated={second.counts()['updated']} created={second.counts()['created']}",
            failures(first))
        empty = live.brain("b0", tmp, jql="key = ZZZNOPE-1")
        sync_pipeline(empty, "p", trigger="cli")
        owned = State.load(brain).pipeline("p").files
        say(results, "J5", "a query that matches 0 issues is a scope the guard questions (not 'everything deleted')", MANUAL if not owned else PASS,
            "needs a first sync with at least 5 files to trigger the guard; the local logic is covered by the fake")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    say(results, "J6", "an issue deleted in Jira disappears from the next listing", MANUAL, "in a scratch project: sync, delete one issue in Jira, sync again: expect 1 deleted (or the guard)")
    say(results, "J7", "a project move keeps the same file (one rename)", MANUAL, "move one issue to another project in your scope: expect 1 rename, no delete + create")
    say(results, "J8", "an issue the account loses permission for looks deleted (documented limit)", MANUAL, "restrict one issue for this account: confirm it follows the on-delete policy")
    say(results, "J9", "rate limit: a real HTTP 429 carries Retry-After", MANUAL, "only visible when the limit is hit; if it happens, note the real headers (Retry-After, RateLimit-Reason)")
    return results


# -- record ------------------------------------------------------------------------------------------------------------------
def run_record(live: Live, out: Path, limit: int) -> list[Result]:
    results: list[Result] = []
    san = Sanitizer()
    issues = live.api.list_issues(live.jql, LIST_FIELDS, 100)[:limit]
    out.mkdir(parents=True, exist_ok=True)
    (out / "issues").mkdir(exist_ok=True)
    (out / "comments").mkdir(exist_ok=True)
    (out / "search.json").write_text(json.dumps(san.walk({"issues": issues}), indent=1, sort_keys=True), encoding="utf-8")
    types: dict[str, int] = {}
    for n, it in enumerate(issues, 1):
        full, comments = live.api.issue(it["id"]), live.api.comments(it["id"])
        (out / "issues" / f"{n:03d}.json").write_text(json.dumps(san.walk(full), indent=1, sort_keys=True), encoding="utf-8")
        (out / "comments" / f"{n:03d}.json").write_text(json.dumps(san.walk({"comments": comments}), indent=1, sort_keys=True), encoding="utf-8")
        for doc in [(full.get("fields") or {}).get("description")] + [c.get("body") for c in comments]:
            for name, count in adf_node_types(doc).items():
                types[name] = types.get(name, 0) + count
    (out / "README.md").write_text("# Sanitised real-schema Jira fixtures (MD-74)\n\nRecorded read-only from a real site by `scripts/live_jira_check.py record`. People, e-mails, account ids, "
                                   "urls and all free text are replaced; the structure (field names, ADF node types, nesting, marks) is real. ADF node types seen: "
                                   + ", ".join(f"{k} ({v})" for k, v in sorted(types.items())) + ".\n", encoding="utf-8")
    leaks = [str(p) for p in out.rglob("*.json") if live.email in p.read_text() or live.token in p.read_text() or live.site in p.read_text()]
    say(results, "R1", f"recorded {len(issues)} issues with comments into {out}", PASS if not leaks else FAIL, f"ADF node types: {len(types)}", [f"the e-mail, token or site host is still in {p}" for p in leaks])
    return results


# -- perf --------------------------------------------------------------------------------------------------------------------
class Kill(BaseException):
    """The process is killed mid-backfill (not caught by `except Exception`)."""


def run_perf(live: Live, changed: int, backfill: int) -> list[Result]:
    results: list[Result] = []
    tmp = Path(tempfile.mkdtemp(prefix="mdp-jira-perf-"))
    try:
        matches = live.api.count(live.jql)
        brain = live.brain("perf", tmp)
        started = time.monotonic()
        first = sync_pipeline(brain, "p", trigger="cli")
        full_seconds = time.monotonic() - started
        say(results, "P0", "a full first sync", PASS if first.result in ("ok", "conflicts") else FAIL, f"{first.counts()['created']} files in {full_seconds:.1f}s (count says {matches})", failures(first))
        state = State.load(brain)
        ps = state.pipeline("p")
        stale = list(ps.files.items())[:changed]
        for _, rec in stale:
            rec.source_version = "stale"                      # as if Jira had changed these issues: they are fetched and rendered again
        state.save()
        started = time.monotonic()
        res = sync_pipeline(brain, "p", trigger="cli")
        seconds = time.monotonic() - started
        say(results, "P1", f"an incremental sync that re-reads {len(stale)} changed issues takes under 30 s", PASS if seconds < 30 and res.result in ("ok", "conflicts") else FAIL,
            f"{seconds:.1f}s for {len(stale)} issues ({res.result})", failures(res))
        if matches > backfill * 1.5:
            say(results, "P2", f"resumable backfill of {backfill} issues", MANUAL, f"the JQL matches {matches} issues; narrow it to about {backfill} (for example add 'AND created >= ...') and re-run")
        else:
            fresh = live.brain("resume", tmp)
            writes = {"n": 0}

            def killer(item):
                writes["n"] += 1
                if writes["n"] == max(1, matches // 2):
                    raise Kill()

            try:
                sync_pipeline(fresh, "p", trigger="cli", after_write=killer)
            except Kill:
                pass
            partial = len(list((fresh.root / "raw").rglob("*.md")))
            res = sync_pipeline(fresh, "p", trigger="cli")
            files = [p for p in (fresh.root / "raw").rglob("*.md")]
            keys = [re.search(r"issue_key: (\S+)", p.read_text()).group(1) for p in files if re.search(r"issue_key: (\S+)", p.read_text())]
            ok = res.result in ("ok", "conflicts") and len(keys) == len(set(keys)) == matches and res.counts()["conflicts"] == 0
            say(results, "P2", f"a killed backfill of {matches} issues resumes with no duplicate or lost file", PASS if ok else FAIL,
                f"after the kill {partial} files; after the resume {len(files)} files, {len(set(keys))} distinct keys, {res.counts()['conflicts']} conflicts", failures(res))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return results


# -- main --------------------------------------------------------------------------------------------------------------------
def read_token(args) -> str:
    if args.use_secret:
        from mdpipeline import vault

        return vault.default_vault().get(args.use_secret)
    if args.stdin:
        return sys.stdin.readline().strip()
    return getpass.getpass("Jira API token (typing is hidden): ")


@contextlib.contextmanager
def no_scheduler_registration():
    """MD-169: a live check syncs scratch brains in temp folders. Registering them would put them in the REAL user's scheduler.json,
    where the background process then retried (and failed) them every hour. Undone when the script ends."""
    from mdpipeline import engine

    saved = engine.register_brain
    engine.register_brain = lambda brain: None
    try:
        yield
    finally:
        engine.register_brain = saved


def main(argv=None, transport=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["check", "record", "perf"])
    parser.add_argument("--site", required=True, metavar="https://NAME.atlassian.net")
    parser.add_argument("--email", required=True)
    parser.add_argument("--jql", required=True)
    parser.add_argument("--stdin", action="store_true")
    parser.add_argument("--use-secret", metavar="ID")
    parser.add_argument("--limit", type=int, default=25, help="issues to examine / record (default 25)")
    parser.add_argument("--out", default="tests/fixtures/jira/real", help="record: the folder to write")
    parser.add_argument("--changed", type=int, default=20)
    parser.add_argument("--backfill", type=int, default=500)
    parser.add_argument("--report", metavar="FILE")
    args = parser.parse_args(argv)
    live = Live(args.site, args.email, read_token(args), args.jql, transport)
    try:
        with no_scheduler_registration():
            results = {"check": lambda: run_check(live, args.limit), "record": lambda: run_record(live, Path(args.out), args.limit),
                       "perf": lambda: run_perf(live, args.changed, args.backfill)}[args.mode]()
    finally:
        live.done()
    if args.report:
        Path(args.report).write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")
    failed = [r for r in results if r.status == FAIL]
    print(f"\n{sum(r.status == PASS for r in results)} PASS, {len(failed)} FAIL, {sum(r.status == MANUAL for r in results)} MANUAL; "
          f"{sum(len(r.divergences) for r in results)} divergence(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
