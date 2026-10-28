"""The Jira Cloud source (MD-46..MD-52): one JQL query is one source; each issue is one item of the source contract.

``scan`` lists the whole scope cheaply (id, key, summary, created, updated: about 100 issues per request), derives each item's
identity (the numeric issue id: stable across summary changes and project moves), its display path ``<PROJECT>/<KEY> <summary>.md`` and
its version (``updated`` plus the renderer version). The content (issue + every comment page, rendered by ``jira_render``) is fetched
lazily, only for items the engine decides it needs. Any failure of the listing makes the source unreadable (nothing is deleted).
"""
from __future__ import annotations

import re
import threading
from datetime import datetime, timezone

from .credentials import CredentialError, parse_ref, resolve_token
from .jira_api import JiraApi, site_host
from .jira_render import RENDER_VERSION, RenderError, render_issue
from .sources import Source, SourceEntry, SourceError, SourceScan, register_source_type

LIST_FIELDS = "summary,created,updated"
MAX_NAME = 120


def clean_title(text: str) -> str:
    """A file-name-safe version of an issue summary (no path separators or control characters, single spaces, bounded)."""
    text = re.sub(r"[\x00-\x1f\x7f/\\]+", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip().strip(".")
    return text[:MAX_NAME].strip() or "untitled"


def _epoch(value: str) -> float:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z").astimezone(timezone.utc).timestamp()
    except (ValueError, TypeError):
        return 0.0


def api_for(source: Source) -> JiraApi:
    """A client whose credentials are resolved on first use, from references only (never stored values)."""
    o = source.options
    # "_email" / "_token" are typed, not yet saved values (a preview or a test before the vault has them): used from memory, never persisted
    return JiraApi(o["site"], o["_email"] if o.get("_email") else (lambda: resolve_token(o["email_ref"])),
                   o["_token"] if o.get("_token") else (lambda: resolve_token(o["token_ref"])))


_WARN_LOCK = threading.Lock()


class JiraSource:
    page_size = 100          # search page size (a test lowers it to exercise pagination)

    def __init__(self, source: Source, api: JiraApi | None = None) -> None:
        self.source = source
        self.options = source.options
        self._api = api

    @property
    def api(self) -> JiraApi:
        if self._api is None:
            self._api = api_for(self.source)
        return self._api

    def scan(self, include: list[str], exclude: list[str], known: dict[str, str] | None = None) -> SourceScan:
        src = self.source
        result = SourceScan(label=src.label, path=self.options["site"], kind="jira", guard_empty=True)
        try:
            issues = self.api.list_issues(self.options["jql"], LIST_FIELDS, self.page_size)
        except SourceError as exc:
            result.status, result.error, result.category, result.entries = "unreadable", str(exc), exc.category, []
            return result
        except Exception as exc:                  # noqa: BLE001 - e.g. a missing credential: the source is unreadable, never empty
            category = getattr(exc, "category", "error")
            result.status, result.error, result.category, result.entries = "unreadable", str(exc), category, []
            return result
        site = self.api.site
        for issue in issues:
            try:
                result.entries.append(self._entry(issue, site, result))
            except (KeyError, TypeError, AttributeError):
                result.status, result.error, result.category, result.entries = "unreadable", "Jira answered a search with an unexpected shape.", "malformed", []
                return result
        result.entries.sort(key=lambda e: e.identity)
        return result

    def _entry(self, issue: dict, site: str, result: SourceScan) -> SourceEntry:
        fields, key, issue_id = issue["fields"], issue["key"], str(issue["id"])
        project = key.rsplit("-", 1)[0]
        updated = fields["updated"]
        api = self.api

        def content() -> bytes:
            full = api.issue(issue_id)
            try:
                rendered = render_issue(full, api.comments(issue_id), site)
            except RenderError as exc:      # this issue fails alone, named by its key (the others and the source go on)
                raise RenderError(f"issue {key} could not be converted to Markdown ({exc})") from exc
            with _WARN_LOCK:               # bodies are fetched on several threads (MD-153)
                result.warnings += rendered.warnings
            return rendered.text.encode("utf-8")

        return SourceEntry(relpath=f"{project}/{key} {clean_title(fields.get('summary'))}.md", abs_path="", mtime=_epoch(updated), size=0,
                           identity=issue_id, created=_epoch(fields.get("created")) or None, version=f"{updated}|r{RENDER_VERSION}",
                           provenance={"site": site, "issue_id": issue_id, "issue_key": key, "issue_url": f"{site}/browse/{key}",
                                       "source_updated": updated},
                           content=content)

    def test_connection(self) -> dict:
        """The account the credentials belong to and how many issues the JQL matches. Read-only."""
        me = self.api.myself()
        return {"user": me.get("displayName") or "unknown user", "matches": self.api.count(self.options["jql"]), "site": self.api.site}


register_source_type("jira", JiraSource)


def check_jira_source(source: Source, remote: bool) -> tuple[list[tuple[str, str]], int | None]:
    """Save-time validation of a Jira source. Returns ([(code, message)], match count or None).

    The static checks always run; with ``remote`` the site, the credentials and the JQL are also checked against Jira (read-only):
    wrong token, no permission, site not found, outage and invalid JQL (Jira's own text) each give a plain message."""
    options, errors = source.options, []
    try:
        site_host(options.get("site", ""))
    except ValueError:
        errors.append(("bad-jira-site", "The Jira site must be an https URL like https://yourcompany.atlassian.net."))
    if not (options.get("jql") or "").strip():
        errors.append(("bad-jira-jql", "The JQL is empty: say which issues to bring in, for example: project = MD"))
    for key, name in (("email_ref", "email"), ("token_ref", "API token")):
        try:
            parse_ref(options.get(key, ""))
        except CredentialError as exc:
            errors.append(("bad-jira-credential", f"The {name} reference is not usable. {exc}"))
    if errors or not remote:
        return errors, None
    try:
        return [], JiraSource(source).test_connection()["matches"]
    except SourceError as exc:
        return [(f"jira-{exc.category}", str(exc))], None
    except CredentialError as exc:
        return [("jira-auth", str(exc))], None
