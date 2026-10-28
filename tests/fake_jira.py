"""A fake Jira Cloud for tests (MD-44): an in-memory site behind the same ``transport(method, url, headers, body)`` call the
real client uses, so no test touches the network. It serves only the READ endpoints the connector needs:

* ``GET  /rest/api/3/myself``                      the token's user
* ``GET  /rest/api/3/search/jql``                  JQL search (cursor pagination: ``nextPageToken``/``isLast``)
* ``POST /rest/api/3/search/approximate-count``    how many issues a JQL matches
* ``GET  /rest/api/3/project/search``              the projects the account can read (MD-141): ``query``, ``startAt``/``maxResults``, ``isLast``
* ``GET  /rest/api/3/issue/{id|key}``              one issue (ADF description, people with display names, links, attachments)
* ``GET  /rest/api/3/issue/{id|key}/comment``      comments, paged with startAt/maxResults/total

Semantics that matter to the connector: the issue ``id`` is stable while the ``key`` changes when an issue is moved (the old key
still resolves); adding/editing/deleting a comment or updating a field bumps ``updated``; deleted and hidden issues look the same
to the searching user; a JQL error answers 400 with Jira's own text. Tests script changes (``add_issue``, ``update_issue``,
``add_comment``, ``edit_comment``, ``delete_comment``, ``delete_issue``, ``move_issue``, ``hide``) and failures (``fail``,
``rate_limit``, ``fail_page``), and read what happened (``requests``, ``write_attempts``). The JQL subset: ``project``, ``key``,
``status``, ``statusCategory``, ``issuetype``, ``labels`` with ``=``, ``!=``, ``in``, ``not in``; ``updated >= -Nd``; ``AND``/``OR``;
``ORDER BY`` is ignored.
"""
from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit


# --- ADF builders (the document format Jira Cloud uses for rich text) ----------------------------------
def adf_text(text: str, *marks: str) -> dict:
    node = {"type": "text", "text": text}
    if marks:
        node["marks"] = [{"type": m} if isinstance(m, str) else m for m in marks]
    return node


def adf_p(*content) -> dict:
    return {"type": "paragraph", "content": [adf_text(c) if isinstance(c, str) else c for c in content]}


def adf_doc(*content) -> dict:
    return {"version": 1, "type": "doc", "content": list(content)}


def _fmt(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000+0000")


class FakeJira:
    def __init__(self, site: str = "acme", email: str = "me@example.com", token: str = "tok", display_name: str = "Mia Owner") -> None:
        self.host = f"{site}.atlassian.net"
        self.email, self.token, self.display_name = email, token, display_name
        self.now = datetime(2026, 10, 1, 9, 0, 0, tzinfo=timezone.utc)
        self.requests: list[tuple[str, str]] = []
        self.write_attempts = 0
        self._issues: dict[str, dict] = {}
        self._numbers: dict[str, int] = {}
        self._next_id = 10000
        self._next_comment = 100
        self._failures: list[dict] = []
        self._rate = [0, 1]
        self._page_failure: int | None = None
        self._pages_served = 0
        self._cursors: dict[str, int] = {}
        self._projects: dict[str, str] = {}

    # -- scripting -------------------------------------------------------------------
    def _tick(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now

    def add_issue(self, project: str, summary: str, *, description=None, comments=(), issuetype="Task", status="To Do",
                  status_category="To Do", priority="Medium", labels=(), components=(), assignee: str | None = None,
                  parent: str | None = None, links=(), attachments=(), fix_versions=(), sprints=(), due: str | None = None,
                  created: datetime | None = None, extra_fields: dict | None = None) -> str:
        self._numbers[project] = self._numbers.get(project, 0) + 1
        self._next_id += 1
        issue_id = str(self._next_id)
        moment = self._tick()
        record = {"id": issue_id, "keys": [f"{project}-{self._numbers[project]}"], "project": project, "summary": summary,
                  "description": description, "issuetype": issuetype, "status": status, "status_category": status_category,
                  "priority": priority, "labels": list(labels), "components": list(components), "assignee": assignee,
                  "parent": parent, "links": list(links), "attachments": list(attachments), "fix_versions": list(fix_versions),
                  "sprints": list(sprints), "due": due, "created": created or moment, "updated": moment, "resolved": None,
                  "comments": [], "hidden": False, "extra": dict(extra_fields or {})}
        self._issues[issue_id] = record
        for body in comments:
            self.add_comment(record["keys"][-1], body, bump=False)
        return record["keys"][-1]

    def add_project(self, key: str, name: str) -> None:
        self._projects[key] = name

    def _project_search(self, query: dict):
        known = {**{r["project"]: r["project"] for r in self._issues.values()}, **self._projects}
        text = (query.get("query") or [""])[0].lower()
        rows = sorted(({"key": k, "name": n} for k, n in known.items() if text in k.lower() or text in n.lower()), key=lambda p: (p["name"].lower(), p["key"]))
        start = int((query.get("startAt") or ["0"])[0])
        size = min(int((query.get("maxResults") or ["50"])[0]), 100)
        return self._reply(200, {"startAt": start, "maxResults": size, "total": len(rows), "isLast": start + size >= len(rows),
                                 "values": rows[start:start + size]})

    def _find(self, ref: str) -> dict | None:
        if ref in self._issues:
            return self._issues[ref]
        return next((r for r in self._issues.values() if ref in r["keys"]), None)

    def issue_id(self, key: str) -> str:
        return self._find(key)["id"]

    def update_issue(self, ref: str, **changes) -> None:
        record = self._find(ref)
        record.update(changes)
        record["updated"] = self._tick()

    def add_comment(self, ref: str, body, author: str | None = None, bump: bool = True) -> str:
        record = self._find(ref)
        self._next_comment += 1
        moment = self._tick()
        record["comments"].append({"id": str(self._next_comment), "author": author or self.display_name, "body": body, "created": moment, "updated": moment})
        if bump:
            record["updated"] = moment
        return str(self._next_comment)

    def edit_comment(self, ref: str, comment_id: str, body) -> None:
        record = self._find(ref)
        comment = next(c for c in record["comments"] if c["id"] == comment_id)
        comment["body"], comment["updated"] = body, self._tick()
        record["updated"] = comment["updated"]

    def delete_comment(self, ref: str, comment_id: str) -> None:
        record = self._find(ref)
        record["comments"] = [c for c in record["comments"] if c["id"] != comment_id]
        record["updated"] = self._tick()

    def delete_issue(self, ref: str) -> None:
        del self._issues[self._find(ref)["id"]]

    def hide(self, ref: str) -> None:
        self._find(ref)["hidden"] = True

    def move_issue(self, ref: str, project: str) -> str:
        record = self._find(ref)
        self._numbers[project] = self._numbers.get(project, 0) + 1
        record["keys"].append(f"{project}-{self._numbers[project]}")
        record["project"] = project
        record["updated"] = self._tick()
        return record["keys"][-1]

    def fail(self, method: str, path_contains: str, status: int = 500, times: int = 1, message: str = "scripted failure") -> None:
        self._failures.append({"method": method, "path": path_contains, "status": status, "times": times, "message": message})

    def rate_limit(self, times: int = 1, retry_after: int = 1) -> None:
        self._rate = [times, retry_after]

    def fail_page(self, number: int) -> None:
        """The ``number``-th search page request (counted from now, 1 = the next one) answers 500."""
        self._page_failure, self._pages_served = number, 0

    # -- the transport ---------------------------------------------------------------
    @staticmethod
    def _reply(status: int, body, headers: dict | None = None):
        return status, headers or {}, json.dumps(body).encode()

    def __call__(self, method: str, url: str, headers: dict, body: bytes | None):
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname != self.host:
            raise AssertionError(f"the fake only serves https://{self.host}, got {url}")
        self.requests.append((method, parts.path))
        if self._rate[0] > 0:
            self._rate[0] -= 1
            return self._reply(429, {"message": "rate limited"}, {"Retry-After": str(self._rate[1])})
        for f in self._failures:
            if f["method"] == method and f["path"] in parts.path and f["times"] > 0:
                f["times"] -= 1
                return self._reply(f["status"], {"errorMessages": [f["message"]]})
        auth = {k.lower(): v for k, v in headers.items()}.get("authorization", "")
        expected = "Basic " + base64.b64encode(f"{self.email}:{self.token}".encode()).decode()
        if auth != expected:
            # real Jira Cloud (MD-174, measured 2026-10-22): rejected basic auth is served as an ANONYMOUS request, so search, the
            # project list and the count answer 200 with nothing; only /myself and the issue reads answer 401
            if method == "GET" and parts.path == "/rest/api/3/search/jql":
                return self._reply(200, {"issues": [], "isLast": True})
            if method == "GET" and parts.path == "/rest/api/3/project/search":
                return self._reply(200, {"self": url, "maxResults": 50, "startAt": 0, "total": 0, "isLast": True, "values": []})
            if method == "POST" and parts.path == "/rest/api/3/search/approximate-count":
                return self._reply(200, {"count": 0})
            return self._reply(401, {"message": "Client must be authenticated to access this resource."})
        query = parse_qs(parts.query)
        data = json.loads(body) if body else None
        path = parts.path
        if method == "GET" and path == "/rest/api/3/myself":
            return self._reply(200, {"accountId": "acct-1", "displayName": self.display_name, "emailAddress": self.email})
        if method == "GET" and path == "/rest/api/3/project/search":
            return self._project_search(query)
        if method == "GET" and path == "/rest/api/3/search/jql":
            return self._search(query)
        if method == "POST" and path == "/rest/api/3/search/approximate-count":
            matched, error = self._match((data or {}).get("jql", ""))
            return self._reply(400, {"errorMessages": [error]}) if error else self._reply(200, {"count": len(matched)})
        m = re.fullmatch(r"/rest/api/3/issue/([^/]+)(/comment)?", path)
        if method == "GET" and m:
            record = self._find(m.group(1))
            if record is None or record["hidden"]:
                return self._reply(404, {"errorMessages": ["Issue does not exist or you do not have permission to see it."]})
            if m.group(2):
                start, size = int((query.get("startAt") or ["0"])[0]), int((query.get("maxResults") or ["50"])[0])
                chosen = record["comments"][start:start + size]
                return self._reply(200, {"startAt": start, "maxResults": size, "total": len(record["comments"]),
                                         "comments": [self._comment_json(c) for c in chosen]})
            return self._reply(200, self._issue_json(record, (query.get("fields") or ["*all"])[0]))
        if method != "GET":
            self.write_attempts += 1
        return self._reply(404, {"errorMessages": ["Not Found"]})

    # -- JQL -------------------------------------------------------------------------
    def _match(self, jql: str):
        text = re.sub(r"(^|\s+)order\s+by\s+.*$", "", jql.strip(), flags=re.I)
        visible = [r for r in self._issues.values() if not r["hidden"]]
        if not text:
            return visible, None
        try:
            clauses = [[c.strip() for c in re.split(r"\s+or\s+", part, flags=re.I)] for part in re.split(r"\s+and\s+", text, flags=re.I)]
            return [r for r in visible if all(any(self._clause(r, c) for c in ors) for ors in clauses)], None
        except ValueError as exc:
            return [], str(exc) if str(exc).startswith("Date value") else f"Error in the JQL Query: {exc}"

    def _clause(self, record: dict, clause: str) -> bool:
        m = re.fullmatch(r'(\w+)\s+(=|!=|>=|not\s+in|in)\s+(.+)', clause, flags=re.I)
        if not m:
            raise ValueError(f"The query is not valid near '{clause}'.")
        field, op, raw = m.group(1).lower(), re.sub(r"\s+", " ", m.group(2).lower()), m.group(3).strip()
        if field == "updated":
            if op != ">=" or not re.fullmatch(r"-\d+d", raw):
                raise ValueError(f"Unsupported updated clause '{clause}'.")
            if int(raw[1:-1]) >= 36500:          # real Jira Cloud (2026-10-16): -36500d is invalid, -3650d is fine
                raise ValueError(f"Date value '{raw}' for field 'updated' is invalid")
            return record["updated"] >= self.now - timedelta(days=int(raw[1:-1]))
        values = [v.strip().strip('"\'') for v in raw.strip("()").split(",")] if op in ("in", "not in") else [raw.strip('"\'')]
        if any(not v or re.search(r"[=!<>]", v) for v in values):
            raise ValueError(f"The query is not valid near '{clause}'.")
        actual = {"project": [record["project"]], "key": record["keys"][-1:], "status": [record["status"]],
                  "statuscategory": [record["status_category"]], "issuetype": [record["issuetype"]], "labels": record["labels"]}.get(field)
        if actual is None:
            raise ValueError(f"Field '{field}' does not exist or you do not have permission to view it.")
        hit = any(a.lower() == v.lower() for a in actual for v in values)
        return hit if op in ("=", "in") else not hit

    def _search(self, query: dict):
        self._pages_served += 1
        if self._page_failure is not None and self._pages_served == self._page_failure:
            return self._reply(500, {"errorMessages": ["page failure"]})
        matched, error = self._match((query.get("jql") or [""])[0])
        if error:
            return self._reply(400, {"errorMessages": [error], "errors": {}})
        size = int((query.get("maxResults") or ["50"])[0])
        token = (query.get("nextPageToken") or [""])[0]
        start = self._cursors.get(token, 0) if token else 0
        chosen = matched[start:start + size]
        fields = (query.get("fields") or ["*all"])[0]
        out = {"issues": [self._issue_json(r, fields) for r in chosen], "isLast": start + size >= len(matched)}
        if not out["isLast"]:
            cursor = f"cursor-{len(self._cursors) + 1}"
            self._cursors[cursor] = start + size
            out["nextPageToken"] = cursor
        return self._reply(200, out)

    # -- JSON shapes -----------------------------------------------------------------
    def _person(self, name: str | None):
        return {"accountId": f"acct-{abs(hash(name)) % 10**6}", "displayName": name, "emailAddress": "hidden@example.com"} if name else None

    def _comment_json(self, c: dict) -> dict:
        return {"id": c["id"], "author": self._person(c["author"]), "created": _fmt(c["created"]), "updated": _fmt(c["updated"]),
                "body": c["body"] if isinstance(c["body"], dict) else adf_doc(adf_p(c["body"]))}

    def _issue_json(self, r: dict, fields: str) -> dict:
        full = {
            "summary": r["summary"], "description": r["description"], "issuetype": {"name": r["issuetype"]},
            "project": {"key": r["project"]}, "status": {"name": r["status"], "statusCategory": {"name": r["status_category"]}},
            "priority": {"name": r["priority"]}, "labels": r["labels"], "components": [{"name": c} for c in r["components"]],
            "assignee": self._person(r["assignee"]), "reporter": self._person(self.display_name),
            "parent": {"key": r["parent"]} if r["parent"] else None, "created": _fmt(r["created"]), "updated": _fmt(r["updated"]),
            "resolutiondate": _fmt(r["resolved"]) if r["resolved"] else None, "duedate": r["due"],
            "fixVersions": [{"name": v} for v in r["fix_versions"]],
            "attachment": [{"filename": a[0], "size": a[1], "content": f"https://{self.host}/rest/api/3/attachment/content/{i}"} for i, a in enumerate(r["attachments"], 1)],
            "subtasks": [{"key": k["keys"][-1], "fields": {"summary": k["summary"], "status": {"name": k["status"]}}}
                         for k in self._issues.values() if k["parent"] in r["keys"] and not k["hidden"]],
            "issuelinks": [dict(l) for l in r["links"]], "customfield_10020": [{"name": s, "boardId": 1, "state": "active"} for s in r["sprints"]] or None,
            "comment": {"total": len(r["comments"])}, **r["extra"],
        }
        wanted = full if fields in ("*all", "") else {k: v for k, v in full.items() if k in fields.split(",")}
        return {"id": r["id"], "key": r["keys"][-1], "self": f"https://{self.host}/rest/api/3/issue/{r['id']}", "fields": wanted}
