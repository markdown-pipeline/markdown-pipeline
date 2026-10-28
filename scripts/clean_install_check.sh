#!/bin/bash
# MD-117 AC3 / MD-131 AC2+AC4+AC6: time "install -> first screen" with the real installer, in a throw-away home (nothing of yours is touched).
#   scripts/clean_install_check.sh [cold|warm] [release|public|checkout|upgrade]
#     cold = an empty package cache (downloads everything), warm = your cache
#     release  (default) builds the wheel from this source and serves it from a local stand-in of the GitHub releases pages, so the REAL
#              install.sh finds "the latest release" and installs it with no login, token or invite (the public repo is not needed yet)
#     public   uses the real public repository (markdown-pipeline/markdown-pipeline) exactly as a stranger would
#     checkout installs from this folder (the old way, for development)
#     upgrade  installs release 0.1.0, then a newer release over it, and checks that settings and pipelines are kept
# The install is marked internal (MD-154: MDPIPELINE_INTERNAL=1) so these runs never count as users. Needs network for "cold".
# Prints the seconds from the start of the install until the UI answers 200 on its first page.
set -u
MODE="${1:-cold}"
HOW="${2:-release}"
APP="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d)"
export HOME="$T/home" XDG_CONFIG_HOME="$T/home/.config" MDPIPELINE_DATA_DIR="$T/data" MDPIPELINE_POSTHOG_KEY=""
export UV_TOOL_DIR="$T/tools" UV_TOOL_BIN_DIR="$T/bin" MDPIPELINE_NO_BACKGROUND_START=1 MDPIPELINE_INTERNAL=1
unset MDPIPELINE_SOURCE MDPIPELINE_RELEASES_URL GITHUB_TOKEN GH_TOKEN
mkdir -p "$HOME"
[ "$MODE" = "cold" ] && export UV_CACHE_DIR="$T/cache"
export PATH="$T/bin:$PATH"
SERVER_PID=""
cleanup() { [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null; return 0; }
trap cleanup EXIT

serve() {   # serve DIR TAG -> sets MDPIPELINE_RELEASES_URL to a local stand-in of the releases pages
  cleanup
  python3 "$APP/scripts/local_releases_server.py" "$1" "$2" > "$T/server.out" &
  SERVER_PID=$!
  for _ in $(seq 1 50); do [ -s "$T/server.out" ] && break; sleep 0.1; done
  export MDPIPELINE_RELEASES_URL="$(head -1 "$T/server.out")"
}
build() {   # build DIR_OF_APP_COPY OUTDIR
  (cd "$1" && uv build --wheel --out-dir "$2" >/dev/null 2>&1) || { echo "BUILD FAILED"; exit 1; }
}

case "$HOW" in
  checkout) export MDPIPELINE_SOURCE="$APP" ;;
  public) ;;
  release|upgrade)
    mkdir -p "$T/rel1"; build "$APP" "$T/rel1"
    serve "$T/rel1" "v$(sed -n 's/^version *= *"\(.*\)"/\1/p' "$APP/pyproject.toml" | head -1)"
    ;;
  *) echo "unknown way: $HOW"; exit 2 ;;
esac

start=$(date +%s.%N)
sh "$APP/scripts/install.sh" > "$T/install.out" 2>&1 || { cat "$T/install.out"; echo "INSTALL FAILED"; exit 1; }
installed=$(date +%s.%N)
mdpipeline start --no-browser --port 0 > "$T/start.out" 2>&1 &
PID=$!
url=""
for _ in $(seq 1 100); do
  url=$(grep -o 'http://127.0.0.1:[0-9]*/' "$T/start.out" | head -1)
  [ -n "$url" ] && break
  sleep 0.1
done
code=$(curl -s -o /dev/null -w '%{http_code}' "$url")
done_at=$(date +%s.%N)
kill "$PID" 2>/dev/null; wait "$PID" 2>/dev/null
printf 'mode=%s  how=%s  install=%.1fs  first-screen=%.1fs  http=%s  default-brain=%s\n' "$MODE" "$HOW" "$(echo "$installed - $start" | bc)" "$(echo "$done_at - $start" | bc)" "$code" "$( [ -f "$HOME/markdown-pipeline/.mdpipeline/config.yaml" ] && echo made || echo MISSING)"
echo "marker: $(cat "$MDPIPELINE_DATA_DIR/install-method" 2>/dev/null)   installed: $(uv tool list 2>/dev/null | grep -i '^mdpipeline' | head -1)   tool dir: $T"

if [ "$HOW" = "upgrade" ]; then
  # AC6: a pipeline and a setting exist, a newer release is published, the same command moves to it and keeps both
  SRC="$T/notes"; mkdir -p "$SRC"; echo "# hello" > "$SRC/a.md"
  mkdir -p "$T/out"; mdpipeline --brain "$HOME/markdown-pipeline" pipeline add keepme --source "$SRC" --directory "$T/out" > "$T/add.out" 2>&1 || { cat "$T/add.out"; echo "UPGRADE CHECK: could not add a pipeline"; exit 1; }
  before="$(mdpipeline --brain "$HOME/markdown-pipeline" pipeline list 2>&1 | grep -c keepme)"
  COPY="$T/app2"; mkdir -p "$COPY"; (cd "$APP" && tar --exclude=.git --exclude=.venv --exclude=__pycache__ --exclude=tests -cf - .) | (cd "$COPY" && tar -xf -)
  sed -i 's/^version *= *"[^"]*"/version = "9.9.9"/' "$COPY/pyproject.toml"
  mkdir -p "$T/rel2"; build "$COPY" "$T/rel2"
  serve "$T/rel2" "v9.9.9"
  sh "$APP/scripts/install.sh" > "$T/install2.out" 2>&1 || { cat "$T/install2.out"; echo "UPGRADE FAILED"; exit 1; }
  after="$(mdpipeline --brain "$HOME/markdown-pipeline" pipeline list 2>&1 | grep -c keepme)"
  echo "upgrade: version now $(uv tool list 2>/dev/null | grep -i mdpipeline | head -1)  pipeline kept: before=$before after=$after"
fi
