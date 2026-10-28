"""MD-182 AC3: a target path longer than Windows' 260-character limit either works, or fails ALONE with a clear error.

Whatever the OS allows: the other files still sync, nothing already synced is deleted, and no file disappears because of it.
"""
from conftest import write
from mdpipeline.config import Pipeline
from mdpipeline.engine import sync_pipeline

DEEP = "/".join(["d" * 50] * 6)            # 6 x 50 + separators = 305 characters under the source, well over 260 with the brain path


def synced_names(brain):
    return sorted(p.name for p in (brain.root / "raw").rglob("*.md"))


def test_a_path_over_260_characters_works_or_fails_alone_and_never_deletes(brain, add_pipeline, make_source):
    src = make_source("docs", {"keep.md": "K", "other.md": "O"})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="original"))
    assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    before = {p: p.read_bytes() for p in (brain.root / "raw").rglob("*.md")}
    write(src / DEEP / "deep.md", "D")
    res = sync_pipeline(brain, "p", trigger="cli")
    print("LONGPATH result", res.result, res.counts(), [str(e)[:300] for e in res.errors])
    after = {p: p.read_bytes() for p in (brain.root / "raw").rglob("*.md") if p.name in ("keep.md", "other.md")}
    assert after == before                                              # siblings untouched, nothing deleted
    assert res.counts()["deleted"] == 0
    deep_written = "deep.md" in synced_names(brain)
    assert deep_written or res.counts()["errors"] == 1                  # written, or exactly this one file reported
    if not deep_written:
        text = " ".join(str(e) for e in res.errors).lower()
        assert "too long" in text or "path" in text                      # a clear reason


def test_the_next_sync_after_a_too_long_file_still_deletes_nothing(brain, add_pipeline, make_source):
    src = make_source("docs", {"keep.md": "K"})
    write(src / DEEP / "deep.md", "D")
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="original"))
    sync_pipeline(brain, "p", trigger="cli")
    again = sync_pipeline(brain, "p", trigger="cli")
    assert again.counts()["deleted"] == 0 and "keep.md" in synced_names(brain)


def test_when_the_os_refuses_a_long_path_only_that_file_fails_and_nothing_is_deleted(brain, add_pipeline, make_source, monkeypatch):
    import os

    src = make_source("docs", {"keep.md": "K", "other.md": "O"})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="original"))
    assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    before = {p: p.read_bytes() for p in (brain.root / "raw").rglob("*.md")}
    write(src / DEEP / "deep.md", "D")
    write(src / "third.md", "T")
    real_replace, real_mkdir = os.replace, os.mkdir

    def refuse_long(path, *a, **k):                      # what a Windows without long paths does (WinError 206 / 3)
        if len(str(path)) > 260:
            raise FileNotFoundError(3, "The system cannot find the path specified (path too long)", str(path))
        return real_mkdir(path, *a, **k)

    def refuse_long_replace(src_, dst, *a, **k):
        if len(str(dst)) > 260:
            raise FileNotFoundError(3, "The system cannot find the path specified (path too long)", str(dst))
        return real_replace(src_, dst, *a, **k)

    with monkeypatch.context() as m:
        m.setattr(os, "mkdir", refuse_long)
        m.setattr(os, "replace", refuse_long_replace)
        res = sync_pipeline(brain, "p", trigger="cli")
    names = synced_names(brain)
    assert "third.md" in names and "deep.md" not in names                 # the siblings still sync
    assert all(p.read_bytes() == data for p, data in before.items())      # nothing synced was touched or deleted
    assert res.counts()["deleted"] == 0
    assert res.counts()["errors"] == 1
    assert "deep.md" in " ".join(str(e) for e in res.errors)              # the error names the file
    assert sync_pipeline(brain, "p", trigger="cli").counts()["deleted"] == 0
