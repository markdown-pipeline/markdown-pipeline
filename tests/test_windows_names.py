"""MD-182 AC2: a name Windows cannot create (CON, NUL, AUX, PRN, COM1-9, LPT1-9, any case, with any extension) gets a fixed change on EVERY OS.

The change is one underscore after the reserved stem (`con.md` -> `con_.md`, folder `aux` -> `aux_`), so a brain copied between machines
keeps the same names. A name the brain already holds is never renamed (no churn on Linux).
"""
import pytest

from conftest import write
from mdpipeline import naming
from mdpipeline.config import Pipeline
from mdpipeline.engine import sync_pipeline


@pytest.mark.parametrize("name,expected", [
    ("con.md", "con_.md"), ("CON.md", "CON_.md"), ("Nul.MD", "Nul_.MD"), ("aux", "aux_"), ("prn.tar.md", "prn_.tar.md"),
    ("com1.md", "com1_.md"), ("COM9.md", "COM9_.md"), ("lpt3.md", "lpt3_.md"), ("con .md", "con _.md"),
])
def test_a_reserved_name_gets_an_underscore_after_its_stem(name, expected):
    assert naming.windows_safe_segment(name) == expected


@pytest.mark.parametrize("name", ["console.md", "con-1.md", "com0.md", "com10.md", "lpt.md", "a.md", "xcon.md", "auxiliary", "2026-10-26_con.md", ".md"])
def test_other_names_are_untouched(name):
    assert naming.windows_safe_segment(name) == name


def test_every_segment_of_a_path_is_checked():
    assert naming.windows_safe_target("raw/p/aux/sub/con.md") == "raw/p/aux_/sub/con_.md"


def test_it_is_idempotent():
    once = naming.windows_safe_target("raw/nul/prn.md")
    assert naming.windows_safe_target(once) == once


def test_a_segment_under_a_prefix_the_brain_already_holds_is_left_alone():
    owned = naming.owned_prefixes(["raw/p/aux/a.md", "raw/p/con.md"])
    assert naming.windows_safe_target("raw/p/aux/b.md", owned) == "raw/p/aux/b.md"          # the folder exists: keep it
    assert naming.windows_safe_target("raw/p/con.md", owned) == "raw/p/con.md"              # the file exists: keep it
    assert naming.windows_safe_target("raw/p/nul/b.md", owned) == "raw/p/nul_/b.md"         # new: changed
    assert naming.windows_safe_target("raw/p/aux/con.md", owned) == "raw/p/aux/con_.md"     # old folder, new reserved file


# --- through a real sync ----------------------------------------------------------------------------------------
@pytest.fixture
def reserved_source(make_source):
    return make_source("docs", {"con.md": "C", "aux/a.md": "A", "readme.md": "R"})


def targets(brain):
    return sorted(p.relative_to(brain.root).as_posix() for p in (brain.root / "raw").rglob("*.md"))


def test_a_sync_writes_reserved_names_with_the_underscore_on_any_os(brain, add_pipeline, reserved_source):
    add_pipeline(Pipeline.new("p", [str(reserved_source)], name_policy="original"))
    assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    assert targets(brain) == ["raw/p/docs/aux_/a.md", "raw/p/docs/con_.md", "raw/p/docs/readme.md"]


def test_a_second_sync_changes_nothing(brain, add_pipeline, reserved_source):
    add_pipeline(Pipeline.new("p", [str(reserved_source)], name_policy="original"))
    sync_pipeline(brain, "p", trigger="cli")
    again = sync_pipeline(brain, "p", trigger="cli", dry_run=True).counts()
    assert (again["created"], again["updated"], again["renamed"], again["deleted"]) == (0, 0, 0, 0) and again["unchanged"] == 3


def test_a_file_the_brain_already_holds_under_a_reserved_name_is_not_renamed(brain, add_pipeline, reserved_source, monkeypatch):
    add_pipeline(Pipeline.new("p", [str(reserved_source)], name_policy="original"))
    with monkeypatch.context() as m:                                    # the brain as an older version of the app made it
        m.setattr(naming, "windows_safe_target", lambda target, owned=frozenset(): target)
        m.setattr("mdpipeline.planner.windows_safe_target", lambda target, owned=frozenset(): target, raising=False)
        assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    assert targets(brain) == ["raw/p/docs/aux/a.md", "raw/p/docs/con.md", "raw/p/docs/readme.md"]
    write(reserved_source / "con.md", "C changed")
    write(reserved_source / "aux" / "b.md", "B")
    res = sync_pipeline(brain, "p", trigger="cli")
    c = res.counts()
    assert (c["renamed"], c["deleted"]) == (0, 0) and c["updated"] == 1 and c["created"] == 1
    assert targets(brain) == ["raw/p/docs/aux/a.md", "raw/p/docs/aux/b.md", "raw/p/docs/con.md", "raw/p/docs/readme.md"]


def test_a_name_policy_slug_equal_to_a_reserved_name_is_changed(brain, add_pipeline, make_source):
    src = make_source("docs", {"CON.md": "C", "nul.md": "N"})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="name"))
    assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    assert targets(brain) == ["raw/p/docs/con_.md", "raw/p/docs/nul_.md"]


def test_a_clash_with_a_real_file_of_the_safe_name_uses_the_existing_clash_rule(brain, add_pipeline, make_source):
    src = make_source("docs", {"con.md": "C", "con_.md": "X"})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="name"))
    assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    names = [t.rsplit("/", 1)[1] for t in targets(brain)]
    assert len(names) == 2 and len(set(n.lower() for n in names)) == 2 and "con_.md" in names     # the loser got the 6-hex suffix
