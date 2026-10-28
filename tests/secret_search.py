"""Find a secret value anywhere it must never be (MD-79 AC6, MD-89): files on disk, the repository (content, messages), captured output."""
from __future__ import annotations

from pathlib import Path


def files_containing(secret: str, *roots: Path) -> list[str]:
    needle = secret.encode()
    hits = []
    for root in roots:
        for path in sorted(Path(root).rglob("*")):
            if path.is_file() and not path.is_symlink():
                try:
                    if needle in path.read_bytes():
                        hits.append(str(path))
                except OSError:
                    continue
    return hits


def repo_containing(secret: str, fake) -> list[str]:
    """Every file of every commit, and every commit message, of the fake repository."""
    needle = secret.encode()
    hits = []
    for sha in fake.history():
        if secret in fake.commit_message(sha):
            hits.append(f"message of {sha[:7]}")
        for path, data in fake.commit_files_changed(sha).items():
            if data is not None and needle in data:
                hits.append(f"{path}@{sha[:7]}")
    return hits
