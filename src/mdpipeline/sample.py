"""Try with sample data (MD-124): a bundled, fictional Jira project and a few docs that sync like a real source, with no network and no credentials.

The sample is an ordinary folder source. Its files are copied from the package into the app's data folder (never into the person's
own folders), so the usual pipeline, sync and removal code handles it. Everything is labelled "Sample": the pipeline name, the source
label, the destination folder and a ``sample: true`` line in every file's front matter.
"""
from __future__ import annotations

import shutil
from pathlib import Path

NAME = "sample"                    # the pipeline name and the default folder name
LABEL = "Sample"                   # the source label, written into every file's provenance
DATA = Path(__file__).parent / "sample_data"
LATER_DIR = "_later"               # bundled "a day later" versions; never part of the source
# What happens when the person asks to see a later sync: one issue is edited and one is deleted.
LATER = {"edit": "ACME/ACME-9 Logo upload fails for SVG files.md", "delete": "ACME/ACME-14 Compare two PDF libraries (spike).md"}


def source_dir() -> Path:
    """The sample source folder in the app's data directory, rebuilt to the bundled state on every call."""
    from .vault import data_dir

    target = data_dir() / "sample-source"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(DATA, target, ignore=shutil.ignore_patterns(LATER_DIR, "__pycache__"))
    return target


def is_sample(pipeline) -> bool:
    """True only for the pipeline this module created: the sample name, the sample label and the bundled source folder.

    A person's own pipeline that happens to be called "sample" is never treated as the sample (it must not be removable here).
    """
    from .vault import data_dir

    folder = str(data_dir() / "sample-source")
    return pipeline.name == NAME and len(pipeline.sources) == 1 and pipeline.sources[0].label == LABEL and pipeline.sources[0].path == folder


def find(brain):
    """The sample pipeline, or None."""
    from .config import load_config

    return next((p for p in load_config(brain).pipelines if is_sample(p)), None)


def later(brain, sync_fn) -> dict:
    """Show what a later sync does: one issue is edited and one is deleted in the sample source, then the pipeline syncs."""
    if find(brain) is None:
        raise KeyError(NAME)
    root = Path(find(brain).sources[0].path)
    shutil.copyfile(DATA / LATER_DIR / Path(LATER["edit"]).name, root / LATER["edit"])
    (root / LATER["delete"]).unlink(missing_ok=True)
    return sync_fn(NAME)


def remove(brain) -> dict:
    """Remove the sample in one step: its files (unedited ones), the pipeline and the source copy. Nothing else is touched."""
    from .removal import remove_pipeline

    pipeline = find(brain)
    if pipeline is None:
        raise KeyError(NAME)
    count = _owned_files(brain)
    result = remove_pipeline(brain, NAME, trigger="ui", purge=True, confirm_count=count)
    shutil.rmtree(Path(pipeline.sources[0].path), ignore_errors=True)
    return {"removed": True, "deleted": result.deleted, "left": len(result.left_edited)}


def _owned_files(brain) -> int:
    from .destinations import bind_target
    from .state import State

    pipeline = find(brain)
    return len(State.load(bind_target(brain, pipeline)).pipeline(NAME).files)
