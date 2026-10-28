"""MD-146: plain words for where the files go (the "output folder"), and copy that describes the real behaviour."""
from pathlib import Path

JS = (Path(__file__).parent.parent / "src" / "mdpipeline" / "ui_static" / "app.js").read_text()


def test_target_folder_hint_describes_where_files_go_not_a_fixed_raw_folder():
    assert "Its <code>raw/</code> folder receives the files" not in JS
    assert "The folder your files go to. Inside it, files follow" in JS and "Where the files go" in JS


def test_conflict_card_uses_complete_sentences_about_the_output_folder():
    assert "Edited in your output folder since the last sync" in JS
    assert "${sentence(c.reason || \"Edited in your output folder since the last sync\")} Force replaces" in JS
    assert "Force replaces your edits in the output folder with the source version." in JS


def test_removal_texts_say_the_files_are_kept_and_no_longer_updated():
    assert JS.count("kept in your output folder; this pipeline no longer updates them") >= 2


def test_mcp_card_says_what_the_agent_can_and_cannot_do():
    assert ("Lets your agent search, read and list your files and run a sync. It cannot change your sources or approve a blocked sync.") in JS
