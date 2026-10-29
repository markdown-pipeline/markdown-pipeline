"""Make the PUBLIC repo's tree equal to this (private) source, for a release (MD-198 finding, product order 2026-10-29: part of every release).

    uv run python scripts/sync_public.py PUBLIC_CLONE            # show what would change (changes nothing)
    uv run python scripts/sync_public.py PUBLIC_CLONE --apply    # copy and delete files in the clone; you then review, commit and push it

The public tree is every file tracked here, except the workflows that must not be public yet (the Windows runner, and the Registry publish workflow until
product says so), plus the three root copies the public README links to (install.sh, usage-events.md, what-we-send.md). Nothing is committed or pushed here.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
WITHHELD = {".github/workflows/windows-tests.yml", ".github/workflows/publish-registry.yml"}       # publish-pypi.yml went public 2026-10-29 (owner steps done)
ROOT_COPIES = {"install.sh": "scripts/install.sh", "usage-events.md": "docs/usage-events.md", "what-we-send.md": "docs/what-we-send.md"}     # public path -> source


def tracked(app: Path = APP) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=app, capture_output=True, check=True).stdout.decode()
    return [p for p in out.split("\0") if p]


def wanted(app: Path = APP) -> dict[str, str]:
    """public path -> source path (both relative)."""
    files = {p: p for p in tracked(app) if p not in WITHHELD}
    files.update({public: source for public, source in ROOT_COPIES.items() if source in files})
    return files


def plan(public: Path, app: Path = APP) -> tuple[list[str], list[str]]:
    """(paths to write, paths to delete) in the public clone."""
    want = wanted(app)
    write = [p for p, src in sorted(want.items()) if not (public / p).is_file() or (public / p).read_bytes() != (app / src).read_bytes()]
    have = [p for p in subprocess.run(["git", "ls-files", "-z"], cwd=public, capture_output=True, check=True).stdout.decode().split("\0") if p]
    return write, sorted(p for p in have if p not in want and (public / p).is_file())      # a file already deleted but not yet committed is done


def apply(public: Path, app: Path = APP) -> tuple[list[str], list[str]]:
    write, delete = plan(public, app)
    want = wanted(app)
    for p in write:
        (public / p).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(app / want[p], public / p)
    for p in delete:
        (public / p).unlink()
    return write, delete


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("public", type=Path, help="a clone of the public repository")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if not (args.public / ".git").exists():
        print("not a git clone:", args.public)
        return 2
    write, delete = (apply if args.apply else plan)(args.public)
    print(f"{'wrote' if args.apply else 'would write'} {len(write)} file(s), {'deleted' if args.apply else 'would delete'} {len(delete)}")
    for p in write:
        print("  write ", p)
    for p in delete:
        print("  delete", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
