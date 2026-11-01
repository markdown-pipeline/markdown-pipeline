"""MD-199 AC5: the MCP client half of scripts/registry_entry_check.sh (runs inside the clean container). See that script for the steps."""
import asyncio
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

T0 = time.time()
ENTRY = json.load(open("/w/server.json", encoding="utf-8"))
# the package comes from a wheel of this source (before a release), or from the REAL PyPI at the exact version server.json names (FROM_PYPI=1)
_wheels = glob.glob("/w/*.whl")
WHEEL = f"mdpipeline=={ENTRY['packages'][0]['version']}" if os.environ.get("FROM_PYPI") else _wheels[0]
PACKAGE = ENTRY["packages"][0]


def step(text):
    print(f"[{time.time() - T0:5.1f}s] {text}", flush=True)


def package_arguments():
    out = []
    for arg in PACKAGE["packageArguments"]:
        out += [arg["name"], arg["value"]] if arg["type"] == "named" else [arg["value"]]
    return out


def text_of(result):
    return "".join(getattr(c, "text", "") for c in result.content) if result.content else json.dumps(result.structured_content)


def start_ui():
    proc = subprocess.Popen(["uvx", "--from", WHEEL, "mdpipeline", "start", "--no-browser"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in proc.stdout:
        found = re.search(r"(http://127\.0\.0\.1:\d+)", line)
        if found:
            return proc, found.group(1)
    raise SystemExit("FAIL: `start` printed no address")


def first_sync(url):
    os.makedirs(os.path.expanduser("~/notes"), exist_ok=True)
    open(os.path.expanduser("~/notes/pricing.md"), "w").write("# Pricing\nusage based pricing for teams\n")
    page = urllib.request.urlopen(url + "/").read().decode()
    token = re.search(r'name="mdp-token" content="([^"]+)"', page).group(1)
    draft = {"name": "notes", "sources": [{"path": os.path.expanduser("~/notes"), "label": "notes"}], "target": "raw/{pipeline}/{source}/{relpath}",
             "include": ["**/*.md"], "exclude": [".git/**"], "on_source_delete": "delete", "guard_threshold_percent": 40, "guard_min_files": 5,
             "name_policy": "original"}
    request = urllib.request.Request(url + "/api/pipelines", data=json.dumps({"draft": draft, "sync": True}).encode(),
                                     headers={"X-MDP-Token": token, "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(request).read())


async def main():
    # the command a Registry client builds from the entry: runtimeHint + identifier + packageArguments (the wheel stands in for the PyPI download)
    assert PACKAGE["runtimeHint"] == "uvx" and PACKAGE["identifier"] == "mdpipeline"
    params = StdioServerParameters(command="uvx", args=["--from", WHEEL, PACKAGE["identifier"], *package_arguments()], env=dict(os.environ), cwd=os.path.expanduser("~"))
    step(f"client starts: uvx {PACKAGE['identifier']} {' '.join(package_arguments())}")
    async with Client(params) as c:
        tools = sorted(t.name for t in (await c.list_tools()).tools)
        step(f"handshake ok, tools: {tools}")
        first = text_of(await c.call_tool("list", {}))
        step(f"first tool call -> {first}")
        assert "setup_needed" in first and "`uvx mdpipeline start`" in first and "`mdpipeline start`" not in first, "FAIL: setup mode / one next step"
        proc, url = start_ui()
        try:
            step(f"followed the next step: `uvx mdpipeline start` is up at {url}")
            done = first_sync(url)
            step(f"first sync: {done['sync']['result']}, created {done['sync']['counts']['created']}")
            assert done["sync"]["result"] == "ok"
        finally:
            proc.terminate()
        after = text_of(await c.call_tool("search", {"query": "pricing"}))         # the same session: no restart
        step(f"same session, tool call -> {after[:300]}")
        assert "setup_needed" not in after and "pricing" in after.lower() and "provenance" in after.lower(), "FAIL: no Markdown with provenance"
    marker = open(os.path.expanduser("~/.local/share/mdpipeline/install-method")).read().strip() if os.path.exists(os.path.expanduser("~/.local/share/mdpipeline/install-method")) else "?"
    step(f"PASS: registry entry -> setup mode -> first sync -> provenance, install method remembered as {marker!r}, total {time.time() - T0:.0f}s")


asyncio.run(main())
