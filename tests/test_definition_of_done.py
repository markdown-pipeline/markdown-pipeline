"""MD-6 Definition of Done, enforced statically on the source tree (the dynamic parts live in test_data_loss.py).

2. No write outside the brain's raw/: only a short allow-list of modules may write, create or delete files at all, and
   they are the bookkeeping modules (config, state, log, lock) plus writer.py, the one writer of brain content.
4. No network calls except from the allow-listed network modules (MD-65): only source connectors and the GitHub target talk
   to the network, only over HTTPS and only to configured hosts. A local-only brain with folder sources makes zero calls.
"""
import re
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "mdpipeline"
WRITE_PATTERNS = re.compile(
    r"\.write_text\(|\.write_bytes\(|\bos\.replace\(|\bos\.rename\(|\.unlink\(|\.rmdir\(|\bshutil\.|\bos\.remove\(|"
    r"\.mkdir\(|open\([^)]*['\"][wax+]b?['\"]|os\.O_(?:WRONLY|RDWR|CREAT)|\.touch\(")
ALLOWED_WRITERS = {
    "sample.py": "copies the bundled sample into the app data folder and edits/removes only that copy (MD-124)",
    "writer.py": "the only writer of brain content (raw/)",
    "config.py": "saves .mdpipeline/config.yaml",
    "state.py": "saves .mdpipeline/state.json",
    "log.py": "appends .mdpipeline/log.jsonl",
    "locking.py": "the .mdpipeline/lock file",
    "brain.py": "registers the brain: creates .mdpipeline/",
    "github_signin.py": "writes only the app settings file (the public GitHub App client ID) in the app data directory",
    "vault.py": "the app vault: one encrypted file in the app data directory, never inside a brain (MD-79)",
    "usage.py": "usage-data consent, install id, event queue and log: one JSON file in the app data directory, never inside a brain (MD-115)",
    "install_info.py": "the install script's one-word marker (how the app was installed) in the app data directory, never inside a brain (MD-117)",
    "autostart.py": "the login item that starts the background sync: one file in the person's own startup folder, removable any time (MD-121)",
    "background.py": "the background process's single-instance lock file in the app data directory (MD-121)",
    "schedulestore.py": "scheduled-sync settings and runtime bookkeeping: one JSON file in the app data directory, never inside a brain (MD-121)",
    "firstrun.py": "guided first run: where the person stopped (no token) in the app data directory (MD-118); the destination folder is created only on 'Write N files'",
    "agents.py": "connect your agent (MD-120): the instructions file (AGENTS.md) in the folder the files went to, only on the person's click, never overwritten without asking; which agents it connected, in the app data directory",
    "folderbrowser.py": "the folder browser (MD-142): creates ONE empty folder, only when the person presses Create in 'New folder' (output case), with a single plain name; never overwrites, never writes a file",
    "feedback.py": "which one-question prompts were shown or answered (MD-123): one small JSON file in the app data directory, never inside a brain",
}
NETWORK_MODULES = {
    "github_api.py": "the GitHub target's REST client: https only, only the configured host, token only in the Authorization header",
    "github_signin.py": "Sign in with GitHub: only the two github.com login endpoints, https only, no client secret (MD-83)",
    "jira_api.py": "the Jira connector's REST client: read-only allow-list, https only, only the configured Atlassian site host",
    "usage.py": "anonymous usage data: https only, only the analytics host, nothing sent without consent (MD-115)",
}
LOCAL_SOCKET_MODULES: dict[str, str] = {}
NETWORK = re.compile(r"^\s*(?:import|from)\s+(?:socket|urllib\.request|urllib3|http\.client|requests|httpx|aiohttp|ftplib|smtplib|websockets?)\b", re.M)


def test_only_allow_listed_modules_can_write_create_or_delete_files():
    offenders = {}
    for path in sorted(SRC.glob("*.py")):
        if path.name in ALLOWED_WRITERS:
            continue
        hits = WRITE_PATTERNS.findall(path.read_text(encoding="utf-8"))
        if hits:
            offenders[path.name] = hits
    assert offenders == {}


def test_every_allow_listed_writer_exists():
    assert all((SRC / name).exists() for name in ALLOWED_WRITERS)


def test_brain_content_writes_go_through_the_safe_writer_only():
    """Modules that touch brain files (engine, removal, ...) must use SafeWriter, not raw file calls."""
    for name in ("engine.py", "removal.py", "planner.py", "service.py", "uiapi.py", "uieditor.py", "uiactivity.py",
                 "brainquery.py", "mcp_server.py", "sources.py"):
        assert not WRITE_PATTERNS.search((SRC / name).read_text(encoding="utf-8")), name
    assert "SafeWriter" in (SRC / "engine.py").read_text(encoding="utf-8") and "SafeWriter" in (SRC / "removal.py").read_text(encoding="utf-8")


def test_no_module_imports_a_network_client():
    offenders = [p.name for p in SRC.glob("*.py") if p.name not in NETWORK_MODULES and p.name not in LOCAL_SOCKET_MODULES and NETWORK.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_a_local_socket_module_can_only_open_unix_sockets():
    for name in LOCAL_SOCKET_MODULES:
        text = (SRC / name).read_text(encoding="utf-8")
        assert "AF_UNIX" in text and "AF_INET" not in text and "create_connection" not in text, name


def test_every_allow_listed_network_module_enforces_https_and_a_host_rule():
    for name in NETWORK_MODULES:
        text = (SRC / name).read_text(encoding="utf-8")
        assert 'scheme != "https"' in text and "allowed_host" in text, name


# MD-164 K1: the Jira token hint shows this address as plain text for the person to open themselves (not a link, nothing is fetched).
SHOWN_TEXT_ONLY = "https://id.atlassian.com/manage-profile/security/api-tokens"


def test_the_ui_assets_reference_no_external_hosts():
    for asset in (p for p in (SRC / "ui_static").glob("*") if p.suffix in (".js", ".css", ".html")):
        text = asset.read_text(encoding="utf-8")
        assert not re.search(r"(?:https?:)?//(?!127\.0\.0\.1|localhost)[a-z0-9.-]+\.[a-z]{2,}/", text.replace("http://www.w3.org", "").replace(SHOWN_TEXT_ONLY, "")), asset


def test_the_mcp_server_has_no_tool_that_writes_brain_content():
    text = (SRC / "mcp_server.py").read_text(encoding="utf-8")
    names = re.findall(r'@server\.tool\(name="([a-z_]+)"', text)
    assert sorted(names) == ["list", "read", "recent_changes", "search", "sync", "sync_status"]
