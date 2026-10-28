"""Live check of a SCOPED Atlassian API token against the Jira gateway (MD-127). Run it yourself; the token never leaves your machine.

    uv run python scripts/live_scoped_token_check.py --site https://acme.atlassian.net --email you@acme.com --label "read:jira-work only"

Run it once per token you want to compare (e.g. one with read:jira-work only, one with read:jira-work + read:jira-user).
The token is read from a hidden prompt (or one stdin line with --stdin); never an argument, never printed, never written to disk.

Calls (all GET, plus the one read-only POST the connector uses, plus ONE write probe that cannot change anything (an empty-body POST that Jira rejects):
  1. cloudId from the site (unauthenticated ``/_edge/tenant_info``)
  2. gateway  GET  /myself
  3. gateway  GET  /search/jql
  4. gateway  POST /search/approximate-count
  5. gateway  GET  /issue/{id}         (an id found in step 3)
  6. gateway  GET  /issue/{id}/comment
  7. the same reads on the site URL, shown as INFO (docs say scoped tokens need the gateway; a first run showed /myself works on the site URL)
  8. gateway  POST /issue with an EMPTY body (cannot create anything: Jira answers 400 to an empty body after the permission check;
     401/403 = the token is refused for writes = read-only; 400 = the token may write)

Output is the HTTP status per step and PASS / FAIL against the expectation. No response body is printed (it may hold names and e-mails).
"""
from __future__ import annotations

import argparse
import base64
import getpass
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

GATEWAY = "https://api.atlassian.com/ex/jira"


def call(method: str, url: str, auth: str | None, body: dict | None = None) -> tuple[int, dict | None]:
    headers = {"Accept": "application/json", "User-Agent": "mdpipeline-scoped-token-check"}
    if auth:
        headers["Authorization"] = f"Basic {auth}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as error:
        return error.code, None
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        return 0, {"error": type(error).__name__}


def report(step: str, status: int, ok: bool) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {step:<52} HTTP {status or 'no response'}")
    return ok


def report_info(step: str, status: int) -> None:
    print(f"INFO  {step:<52} HTTP {status or 'no response'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site", required=True, help="https://<name>.atlassian.net")
    parser.add_argument("--email", required=True)
    parser.add_argument("--label", default="(unlabelled)", help="which scopes this token has, for the printed report")
    parser.add_argument("--jql", default="updated >= -90d ORDER BY updated DESC")
    parser.add_argument("--stdin", action="store_true", help="read the token from one line of stdin instead of a hidden prompt")
    args = parser.parse_args()

    site = urllib.parse.urlsplit(args.site)
    if site.scheme != "https" or not site.hostname or not site.hostname.endswith(".atlassian.net"):
        print("The site must look like https://<name>.atlassian.net", file=sys.stderr)
        return 2
    token = sys.stdin.readline().strip() if args.stdin else getpass.getpass("Scoped API token (hidden): ").strip()
    if not token:
        print("No token given.", file=sys.stderr)
        return 2
    auth = base64.b64encode(f"{args.email}:{token}".encode()).decode()
    site_url = f"https://{site.hostname}"

    print(f"Token scopes under test: {args.label}\n")
    status, info = call("GET", f"{site_url}/_edge/tenant_info", None)
    cloud_id = (info or {}).get("cloudId")
    if not report("1 cloudId from /_edge/tenant_info", status, bool(cloud_id)):
        return 1
    base = f"{GATEWAY}/{cloud_id}/rest/api/3"

    results = []
    status, _ = call("GET", f"{base}/myself", auth)
    results.append(report("2 gateway GET /myself (200 expected)", status, status == 200))

    query = urllib.parse.urlencode({"jql": args.jql, "fields": "updated", "maxResults": 5})
    status, page = call("GET", f"{base}/search/jql?{query}", auth)
    issues = (page or {}).get("issues") or []
    results.append(report("3 gateway GET /search/jql (200 expected)", status, status == 200))
    if status == 200 and not issues:
        print("      note: the search returned no issues; steps 5 and 6 are skipped (widen --jql)")

    status, _ = call("POST", f"{base}/search/approximate-count", auth, {"jql": args.jql})
    results.append(report("4 gateway POST /search/approximate-count (200)", status, status == 200))

    if issues:
        issue_id = urllib.parse.quote(str(issues[0]["id"]))
        status, _ = call("GET", f"{base}/issue/{issue_id}?fields=*all", auth)
        results.append(report("5 gateway GET /issue/{id} (200 expected)", status, status == 200))
        status, _ = call("GET", f"{base}/issue/{issue_id}/comment?maxResults=5", auth)
        results.append(report("6 gateway GET /issue/{id}/comment (200 expected)", status, status == 200))

    # The same reads on the SITE URL (what the connector uses today). Informational: the docs say scoped tokens need the gateway, but a
    # first live run showed /myself answering 200 here, so every call the connector makes is tried.
    site = f"{site_url}/rest/api/3"
    status, _ = call("GET", f"{site}/myself", auth)
    report_info("7a site URL GET /myself", status)
    status, _ = call("GET", f"{site}/search/jql?{query}", auth)
    report_info("7b site URL GET /search/jql", status)
    status, _ = call("POST", f"{site}/search/approximate-count", auth, {"jql": args.jql})
    report_info("7c site URL POST /search/approximate-count", status)
    if issues:
        status, _ = call("GET", f"{site}/issue/{issue_id}?fields=*all", auth)
        report_info("7d site URL GET /issue/{id}", status)
        status, _ = call("GET", f"{site}/issue/{issue_id}/comment?maxResults=5", auth)
        report_info("7e site URL GET /issue/{id}/comment", status)

    # Write probe: POST /issue with an EMPTY ``fields``. Jira rejects it with 400 (validation) after the permission check, so it can never create
    # anything. 401/403 = this token is refused for writes (read-only). 400 = the token is allowed to write: it is NOT read-only.
    status, _ = call("POST", f"{base}/issue", auth, {"fields": {}})
    results.append(report("8 gateway POST /issue, empty body (401/403 = read-only)", status, status in (401, 403)))
    status, _ = call("POST", f"{site}/issue", auth, {"fields": {}})
    report_info("8b site URL POST /issue, empty body (401/403 = read-only)", status)

    print("\nSend back this output (it holds only step names and HTTP codes). A FAIL on step 2 with the others passing means /myself needs read:jira-user.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
