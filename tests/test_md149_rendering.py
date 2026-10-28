"""MD-149: two small rendering fixes. The folder icon needs no font; the suggested agent command needs no awkward shell quoting."""
from pathlib import Path

import pytest

from mdpipeline import agents

STATIC = Path(__file__).parent.parent / "src" / "mdpipeline" / "ui_static"


def test_the_browse_dialog_draws_folder_icons_as_inline_svg_not_an_emoji_font_glyph():
    js = (STATIC / "browse.js").read_text()
    assert "\U0001F4C1" not in js
    assert js.count('<svg class="br-ico"') >= 2          # the listed folder and the folder that cannot be opened


@pytest.mark.parametrize("pipeline", ["team-wiki", "acme-jira", "sample", "p"])
@pytest.mark.parametrize("agent", ["claude_code", "codex"])
def test_the_suggested_command_for_the_decisions_question_has_no_escaped_apostrophe(brain, add_pipeline, tmp_path, pipeline, agent):
    from mdpipeline.config import Pipeline

    out = tmp_path / "out" / pipeline
    out.mkdir(parents=True)
    (out / "a.md").write_text("# Roadmap\nOpen decision: pricing.\n")
    p = Pipeline.new(pipeline, [])
    p.destination = {"type": "directory", "path": str(out)}
    add_pipeline(p)
    question = agents.suggest_question(brain, pipeline)["question"]
    assert "'" not in question
    assert "'\"'\"'" not in agents.start_command(agent, question)


@pytest.mark.parametrize("question", [agents.JIRA_FALLBACK, agents.FOLDER_FALLBACK])
def test_the_fallback_questions_have_no_apostrophe_either(question):
    assert "'" not in question
