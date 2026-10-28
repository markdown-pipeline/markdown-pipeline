"""Test helpers: seed brain files the way a previous sync would have left them."""
from __future__ import annotations

from pathlib import Path

from mdpipeline.frontmatter import build_provenance, content_hash, merge_provenance
from mdpipeline.state import FileRecord


def seed_owned(brain, state, pipeline, label, relpath, source_bytes, target, source_root="/x", brain_edit: bytes | None = None):
    """Write `target` into the brain as if synced earlier, and record ownership in `state`."""
    prov = build_provenance(pipeline=pipeline, source_label=label, source_path=relpath, source_root=source_root,
                            source_mtime=1.0, synced_at=2.0, source_bytes=source_bytes)
    written = merge_provenance(source_bytes, prov)
    path = Path(brain.root) / target
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(brain_edit if brain_edit is not None else written)
    state.pipeline(pipeline).files[target] = FileRecord(label, relpath, content_hash(source_bytes), content_hash(written))
    return written
