"""MD-48: each Jira issue becomes one clean Markdown file (fields, description, sub-tasks, links, attachments, comments) with no silent content loss."""
import base64
import json
import os
from pathlib import Path

import pytest
import yaml

from fake_jira import FakeJira, adf_doc, adf_p, adf_text
from jira_fixtures import populate, rich_description, unknown_node_description
from mdpipeline.frontmatter import parse_document
from mdpipeline.jira_render import RenderError, adf_to_markdown, render_issue

GOLDEN = Path(__file__).parent / "fixtures" / "jira" / "golden"
SITE = "https://acme.atlassian.net"


def md(doc):
    return adf_to_markdown(doc)[0]


def t(text, *marks):
    return adf_text(text, *marks)


def test_headings_paragraphs_and_marks():
    out = md(rich_description())
    assert "# Heading one\n" in out and "### Heading three\n" in out
    assert "**bold** *italic* ~~struck~~ `code` underlined [link](https://example.com/a?b=1) ***both***" in out


def test_hard_break_keeps_both_lines():
    assert "line one  \nline two" in md(rich_description())


def test_nested_bullet_ordered_and_task_lists():
    out = md(rich_description())
    assert "- one\n  - nested\n- two" in out
    assert "1. first\n2. second" in out
    assert "- [x] done task\n- [ ] open task" in out


def test_code_block_keeps_language_and_text_and_survives_backticks():
    out = md(rich_description())
    assert "```python\nprint('hi')\nprint(\"there\")\n```" in out
    tricky = adf_doc({"type": "codeBlock", "attrs": {"language": "md"}, "content": [t("a ``` b")]})
    assert md(tricky) == "````md\na ``` b\n````"


def test_quote_panel_rule():
    out = md(rich_description())
    assert "> quoted text" in out
    assert "> **Warning**\n>\n> careful" in out
    assert "\n---\n" in out


def test_table_has_a_header_and_escapes_pipes_and_breaks():
    out = md(rich_description())
    assert "| Name | Value |\n| --- | --- |\n| a \\| b | 1<br>2 |" in out


def test_inline_nodes_become_text_and_people_are_display_names_only():
    out = md(rich_description())
    assert "Hi @Dana Reviewer 😄 due 2026-09-21 \\[In review\\] <https://acme.atlassian.net/browse/MD-2>" in out
    assert "acct-9" not in out


def test_media_expand_card_and_decisions_are_never_dropped():
    out = md(rich_description())
    assert "[media: diagram.png]" in out
    assert "**Details**\n\nhidden detail" in out
    assert "<https://example.com/doc>" in out
    assert "- Decision: we decided" in out


def test_an_unknown_node_keeps_its_text_with_a_visible_marker_and_is_counted():
    out, warnings = adf_to_markdown(unknown_node_description())
    assert "text inside the widget" in out and "unsupported" in out.lower() and "futureWidget" in out and warnings == 1


def test_special_characters_are_escaped_but_snake_case_is_left_alone():
    out = md(adf_doc(adf_p(t("a*b [c] <d> `e` snake_case_word _x_ # not heading"))))
    assert "a\\*b \\[c\\] \\<d\\> \\`e\\` snake_case_word \\_x\\_" in out
    assert md(adf_doc(adf_p(t("# starts with hash")))).startswith("\\# starts with hash")


def test_output_is_deterministic():
    assert md(rich_description()) == md(rich_description())


def test_a_malformed_document_is_a_render_error_not_a_silent_drop():
    with pytest.raises(RenderError):
        adf_to_markdown({"type": "doc", "content": "not a list"})
    with pytest.raises(RenderError):
        adf_to_markdown({"type": "paragraph"})                      # not a document
    assert adf_to_markdown("plain text from an old API *kept*") == ("plain text from an old API \\*kept\\*", 0)


def fetch(fake, key):
    auth = "Basic " + base64.b64encode(b"me@example.com:tok").decode()
    issue = json.loads(fake("GET", f"{SITE}/rest/api/3/issue/{fake.issue_id(key)}?fields=*all", {"Authorization": auth}, None)[2])
    comments, start = [], 0
    while True:
        page = json.loads(fake("GET", f"{SITE}/rest/api/3/issue/{fake.issue_id(key)}/comment?startAt={start}&maxResults=100", {"Authorization": auth}, None)[2])
        comments += page["comments"]
        start += len(page["comments"])
        if start >= page["total"]:
            return issue, comments


@pytest.fixture
def site():
    fake = FakeJira()
    return fake, populate(fake)


def render(site, label):
    fake, keys = site
    issue, comments = fetch(fake, keys[label])
    return render_issue(issue, comments, SITE)


def test_front_matter_has_the_jira_mapping_with_display_names_only(site):
    rendered = render(site, "rich")
    meta, body = parse_document(rendered.text)
    j = meta["jira"]
    assert j["key"] == "MD-2" and j["type"] == "Task" and j["status"] == "In Progress" and j["status_category"] == "In Progress"
    assert j["priority"] == "High" and j["labels"] == ["a", "b"] and j["components"] == ["engine"]
    assert j["assignee"] == "Dana Reviewer" and j["reporter"] == "Mia Owner" and j["parent"] == "MD-1"
    assert j["created"].endswith("Z") and j["updated"].endswith("Z") and j["due"] == "2026-11-01"
    assert j["fix_versions"] == ["1.0"] and j["sprints"] == ["Sprint 7"]
    assert "hidden@example.com" not in rendered.text and "acct-" not in rendered.text


def test_body_order_title_description_subtasks_links_attachments_comments(site):
    text = render(site, "rich").text
    positions = [text.index(x) for x in ("# MD-2: Rich description: everything", "# Heading one", "## Sub-tasks", "## Links", "## Attachments", "## Comments")]
    assert positions == sorted(positions)
    assert "- MD-3: A sub-task (Done)" in text
    assert "- blocks MD-4: Linked issue" in text
    assert "- diagram.png (20.0 KB)" in text and "- notes.txt (12 B)" in text


def test_comments_are_oldest_first_with_author_and_utc_time_and_edits_are_marked(site):
    fake, keys = site
    cid = fake.add_comment(keys["plain"], "original text")
    fake.add_comment(keys["plain"], "second")
    fake.edit_comment(keys["plain"], cid, "edited text")
    text = render(site, "plain").text
    assert text.index("edited text") < text.index("second")
    assert "### Mia Owner · 2026-10-01 09:00:0" in text.replace("\n", " ") or "Mia Owner · 2026-10-01" in text
    assert "(edited)" in text and text.count("(edited)") == 1 and " UTC" in text


def test_an_issue_without_a_description_or_comments_has_only_what_it_has(site):
    text = render(site, "plain").text
    assert "## Comments" not in text and "## Sub-tasks" not in text and text.startswith("---\njira:")
    _, body = parse_document(text)
    assert body.strip().startswith("# MD-5: Plain issue, no description")


def test_all_comment_pages_are_included(site):
    text = render(site, "thread").text
    assert "comment number 1\n" in text and "comment number 120" in text and text.count("### Mia Owner") == 120


def test_an_unknown_node_in_a_description_counts_a_warning_and_keeps_the_text(site):
    r = render(site, "unknown")
    assert r.warnings == 1 and "text inside the widget" in r.text


def test_same_issue_data_gives_the_same_bytes(site):
    assert render(site, "rich").text == render(site, "rich").text


def test_golden_files(site):
    """The rendered corpus matches the reviewed golden files. Regenerate with UPDATE_GOLDEN=1 and review the diff."""
    for label in ("rich", "plain", "unknown"):
        actual = render(site, label).text
        path = GOLDEN / f"{label}.md"
        if os.environ.get("UPDATE_GOLDEN"):
            path.write_text(actual, encoding="utf-8")
        assert actual == path.read_text(encoding="utf-8"), label


def test_the_rendered_file_accepts_the_provenance_block(site):
    from mdpipeline.frontmatter import build_provenance, merge_provenance

    text = render(site, "rich").text
    prov = build_provenance(pipeline="p", source_label="jira", source_path="10002", source_root=SITE, source_mtime=0.0, synced_at=0.0,
                            source_bytes=text.encode(), source_type="jira", extra={"issue_key": "MD-2"})
    meta, body = parse_document(merge_provenance(text.encode(), prov).decode())
    assert meta["jira"]["key"] == "MD-2" and meta["provenance"]["source"] == "jira" and meta["provenance"]["issue_key"] == "MD-2"
    assert body.lstrip().startswith("# MD-2:")
