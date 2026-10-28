#!/bin/sh
# Markdown Pipeline: one-command install for Linux (tested). macOS: same steps, untested. Windows: use WSL 2 (see the README).
#
#   curl -fsSL https://raw.githubusercontent.com/markdown-pipeline/markdown-pipeline/main/install.sh | sh
#   MDPIPELINE_SOURCE=/path/to/app sh install.sh   # install from a folder, a wheel, a git URL or another package name
#   MDPIPELINE_RELEASES_URL=<repo url> sh install.sh   # take the latest release from another repository (a mirror, a test)
#
# What it does: makes sure `uv` is there (installing it from astral.sh if not), finds the latest public release on GitHub (no account,
# token or invite: it only follows the public "latest release" link), installs the app as a uv tool with its own Python 3.12
# (nothing system-wide, no administrator rights, everything under your home folder), and leaves a one-word note that this install used the script.
# Run it again any time to repair or update. Remove everything with `mdpipeline uninstall`.
set -eu

SOURCE="${MDPIPELINE_SOURCE:-}"
RELEASES_URL="${MDPIPELINE_RELEASES_URL:-https://github.com/markdown-pipeline/markdown-pipeline}"

case "$(uname -s)" in
  Linux|Darwin) ;;
  *)
    echo "This installer is for Linux (macOS is untested). On Windows, use WSL 2 (Ubuntu) and run the same command inside it: the app needs a POSIX system." >&2
    exit 1
    ;;
esac

if ! command -v uv >/dev/null 2>&1; then
  if ! command -v curl >/dev/null 2>&1; then
    echo "Neither 'uv' nor 'curl' was found. Install uv first (https://docs.astral.sh/uv/) or install curl, then run this again." >&2
    exit 1
  fi
  echo "Installing uv (the Python tool manager) from astral.sh ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  PATH="$HOME/.local/bin:$PATH"
  export PATH
fi

if [ -z "$SOURCE" ]; then
  if ! command -v curl >/dev/null 2>&1; then
    echo "'curl' was not found. Install curl, then run this again." >&2
    exit 1
  fi
  # "releases/latest" redirects to ".../releases/tag/<tag>"; the package is the wheel attached to that release.
  FINAL="$(curl -fsSL -o /dev/null -w '%{url_effective}' "$RELEASES_URL/releases/latest")" || {
    echo "Could not look up the latest release at $RELEASES_URL. Check your network and try again." >&2
    exit 1
  }
  case "$FINAL" in
    */releases/tag/*) ;;
    *)
      echo "No release was found at $RELEASES_URL yet. Nothing was installed." >&2
      exit 1
      ;;
  esac
  TAG="${FINAL##*/}"
  SOURCE="$RELEASES_URL/releases/download/$TAG/mdpipeline-${TAG#v}-py3-none-any.whl"
fi

echo "Installing Markdown Pipeline ..."
uv tool install --force --python 3.12 "$SOURCE"

mdpipeline install-note script >/dev/null 2>&1 || true

echo
echo "Installed. Open it with:  mdpipeline"
echo "If your shell says 'command not found', run:  uv tool update-shell   and open a new terminal."
