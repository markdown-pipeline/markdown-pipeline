"""MD-215 diagnostics: when a first-run write test fails, the report says where the files actually are (the page said "N files in ..."; the test checked one folder)."""
import json
import os
import time

from wherefiles import report


def test_the_report_lists_every_file_under_the_test_folder_with_its_age(tmp_path):
    (tmp_path / "out" / "raw").mkdir(parents=True)
    (tmp_path / "out" / "raw" / "a.md").write_text("a")
    old = tmp_path / "old.md"
    old.write_text("o")
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    text = report(tmp_path, home=None, brain=None, now=time.time())
    assert "out/raw/a.md" in text and "old.md" in text
    assert "(fresh)" in text.split("out/raw/a.md")[1].splitlines()[0] and "(fresh)" not in text.split("old.md")[1].splitlines()[0]


def test_the_report_lists_the_default_folder_under_the_test_home_and_says_when_it_is_missing(tmp_path):
    home = tmp_path / "isolated-home"
    (home / "markdown-pipeline" / "team-wiki" / "raw").mkdir(parents=True)
    (home / "markdown-pipeline" / "team-wiki" / "raw" / "n0.md").write_text("n")
    assert "markdown-pipeline/team-wiki/raw/n0.md" in report(tmp_path, home=home, brain=None, now=time.time())
    assert "does not exist" in report(tmp_path, home=tmp_path / "nowhere", brain=None, now=time.time())


def test_the_report_shows_the_last_log_lines_of_the_brain_so_the_servers_view_sits_next_to_the_pages(tmp_path):
    class Brain:
        config_dir = tmp_path / "brain" / ".mdpipeline"

    Brain.config_dir.mkdir(parents=True)
    lines = [{"type": "run", "pipeline": "p", "result": "ok", "destination": {"type": "directory", "path": "/x/out"}, "n": n} for n in range(8)]
    (Brain.config_dir / "log.jsonl").write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    text = report(tmp_path, home=None, brain=Brain, now=time.time())
    assert '"/x/out"' in text and '"n": 7' in text and '"n": 0' not in text          # the last few lines only


def test_a_huge_tree_is_cut_short_not_dumped(tmp_path):
    for n in range(300):
        (tmp_path / f"f{n}.md").write_text("x")
    text = report(tmp_path, home=None, brain=None, now=time.time())
    assert "more not shown" in text and len(text.splitlines()) < 260
