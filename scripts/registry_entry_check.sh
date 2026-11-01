#!/bin/bash
# MD-199 AC5: from the Registry entry only (server.json, no README), in a clean container:
#   1. an MCP client starts the server with the exact command server.json describes  -> handshake + tools
#   2. a tool call answers with setup mode and its one next step                        -> `uvx mdpipeline start`
#   3. follow that step to a first sync (the UI the step opens, driven over its local API instead of a browser)
#   4. the SAME client session, no restart, calls a tool                                 -> Markdown with provenance
# A fresh HOME is used: the test image has an older `mdpipeline` install under its own home, which must not be seen.
# The only difference from a real Registry install: the package comes from a wheel built from this source (it is not on PyPI yet), via `uvx --from`.
#   scripts/registry_entry_check.sh [IMAGE]       (default image: mdp-ea-test:latest, which has uv and no system Python)
#   FROM_PYPI=1 scripts/registry_entry_check.sh   the package is downloaded from the REAL PyPI at the version server.json names (no local wheel)
set -eu
APP="$(cd "$(dirname "$0")/.." && pwd)"; IMAGE="${1:-mdp-ea-test:latest}"; W="$(mktemp -d)"; chmod 755 "$W"
[ -n "${FROM_PYPI:-}" ] || (cd "$APP" && uv build --wheel -o "$W" >/dev/null 2>&1); cp "$APP/server.json" "$W/server.json"; cp "$APP/scripts/registry_entry_client.py" "$W/client.py"
chmod -R a+rX "$W"
docker run --rm -e FROM_PYPI="${FROM_PYPI:-}" -v "$W:/w:ro" "$IMAGE" bash -c 'export PATH=$HOME/.local/bin:$PATH MDPIPELINE_POSTHOG_KEY=""; export HOME=$(mktemp -d); uv run --no-project --python 3.12 --with mcp python /w/client.py'
