#!/bin/bash
# MD-196 AC7: background sync set up through `uvx` still runs after `uv cache clean mdpipeline`. Runs in a clean container (image with uv, no system python).
#   scripts/uvx_cache_clean_check.sh [IMAGE]      (default image: mdp-ea-test:latest; builds the wheel from this source first)
set -eu
APP="$(cd "$(dirname "$0")/.." && pwd)"; IMAGE="${1:-mdp-ea-test:latest}"; W="$(mktemp -d)"; chmod 755 "$W"
(cd "$APP" && uv build --wheel -o "$W" >/dev/null 2>&1)
cat > "$W/inner.sh" <<'INNER'
export PATH=$HOME/.local/bin:$PATH MDPIPELINE_POSTHOG_KEY="" MDPIPELINE_INTERNAL=1
WHL=$(ls /w/*.whl); mkdir -p ~/src ~/brain && echo "# hi" > ~/src/a.md; M="uvx --from $WHL mdpipeline"
MDPIPELINE_NO_BACKGROUND_START=1 $M init ~/brain >/dev/null 2>&1; cd ~/brain; MDPIPELINE_NO_BACKGROUND_START=1 $M pipeline add p --source ~/src >/dev/null 2>&1
$M sync p >/dev/null 2>&1; sleep 3                                   # the first sync turns background sync on (login item + process)
EXECP=$(grep '^Exec=' ~/.config/autostart/mdpipeline-background.desktop | cut -d= -f2 | cut -d' ' -f1)
uv cache clean mdpipeline >/dev/null 2>&1; pkill -f "mdpipeline background"; sleep 1; echo x > ~/src/b.md
( $EXECP -m mdpipeline background --started-by login_item >/dev/null 2>&1 & ); sleep 8     # a login after the clean
pgrep -f "mdpipeline background" >/dev/null && $EXECP -m mdpipeline status | grep -q "result: ok" && echo "PASS: background sync runs after uv cache clean mdpipeline" || { echo "FAIL"; exit 1; }
INNER
chmod -R a+rX "$W"; docker run --rm -v "$W:/w:ro" "$IMAGE" bash /w/inner.sh
