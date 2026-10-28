"""Post-check after the GitHub App moves to the markdown-pipeline org and is made public (MD-129). No credentials, nothing written.

    uv run python scripts/github_app_postcheck.py [--org markdown-pipeline]

C1  the build ships a Client ID (packaged defaults.json)
C2  GitHub shows the app publicly (anonymous API), owned by the org, with that same Client ID
C3  a clean machine (empty home and app-data folder) uses the shipped default and is not asked to register an app

Exit code 0 when every item passes. A FAIL names the next step.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

API = "https://api.github.com/apps/{slug}"


@dataclass
class Result:
    item: str
    title: str
    status: str
    detail: str = ""


def run_checks(shipped: dict, org: str, fetch: Callable[[str], tuple[int, str]], show: Callable[[], str]) -> list[Result]:
    out: list[Result] = []
    client_id, slug = shipped.get("client_id", ""), shipped.get("slug", "")
    out.append(Result("C1", "the build ships the app's Client ID", "PASS" if client_id and slug else "FAIL",
                      f"client_id={client_id or '(none)'} slug={slug or '(none)'}" if client_id and slug else
                      "src/mdpipeline/defaults.json has no github_app: put the public App's Client ID and slug there, then rebuild."))
    status, body = fetch(API.format(slug=slug or "markdown-pipeline"))
    if status != 200:
        out.append(Result("C2", "the app is public, owned by the org, with the shipped Client ID", "FAIL",
                          f"GitHub answered {status} for an anonymous request: the app is still private or the slug changed. Transfer it, then Make public (Advanced > Danger zone)."))
    else:
        data = json.loads(body or "{}")
        owner, live_id = (data.get("owner") or {}).get("login", ""), data.get("client_id", "")
        problems = []
        if owner.lower() != org.lower():
            problems.append(f"owner is {owner!r}, expected {org!r}: finish the transfer")
        if live_id != client_id:
            problems.append(f"live Client ID {live_id!r} differs from the shipped one {client_id!r}: update src/mdpipeline/defaults.json, rebuild and release")
        out.append(Result("C2", "the app is public, owned by the org, with the shipped Client ID", "FAIL" if problems else "PASS",
                          "; ".join(problems) or f"owner {owner}, client_id {live_id}"))
    try:
        text = show()
    except subprocess.TimeoutExpired as exc:
        out.append(Result("C3", "a clean machine uses the shipped default, no app to register", "FAIL",
                          f"mdpipeline github-app show did not answer within {int(exc.timeout)} s \u2192 run it by hand with an empty home "
                          "(HOME=$(mktemp -d) MDPIPELINE_DATA_DIR=$(mktemp -d) mdpipeline github-app show) and see what it waits for"))
        return out
    ok = f"Client ID: {client_id}" in text and "shipped default" in text and "not set up" not in text
    out.append(Result("C3", "a clean machine uses the shipped default, no app to register", "PASS" if ok else "FAIL",
                      "" if ok else "mdpipeline github-app show on an empty home did not report the shipped default: " + text.strip()[:200]))
    return out


def _fetch(url: str) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "mdpipeline-postcheck"})
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:        # noqa: S310 - fixed https://api.github.com URL
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""


def _clean_machine_show() -> str:
    with tempfile.TemporaryDirectory(prefix="mdp-postcheck-") as home:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("MDPIPELINE", "XDG_"))}
        env.update(HOME=home, XDG_CONFIG_HOME=home, XDG_DATA_HOME=home, APPDATA=home, MDPIPELINE_DATA_DIR=home, MDPIPELINE_NO_BACKGROUND_START="1")
        done = subprocess.run([sys.executable, "-m", "mdpipeline", "github-app", "show"], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        return done.stdout + done.stderr


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--org", default="markdown-pipeline")
    args = parser.parse_args(argv)
    from mdpipeline.github_signin import shipped_github_app

    results = run_checks(shipped_github_app(), args.org, _fetch, _clean_machine_show)
    for r in results:
        print(f"[{r.status}] {r.item} {r.title}" + (f": {r.detail}" if r.detail else ""))
    return 0 if all(r.status == "PASS" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
