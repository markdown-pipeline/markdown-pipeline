"""The Jira fixture corpus (MD-44): ADF documents covering every node type the connector must handle, and a populated fake site.

SYNTHETIC: built from Atlassian's published ADF and REST schemas, not recorded from the owner's projects (no API token was available
when this was written). Replace or extend with sanitised recordings once a token is at hand; the tests only depend on the shapes.
"""
from __future__ import annotations

from fake_jira import FakeJira, adf_doc, adf_p, adf_text


def t(text, *marks):
    return adf_text(text, *marks)


def li(*content):
    return {"type": "listItem", "content": list(content)}


def cell(kind, *content):
    return {"type": kind, "attrs": {}, "content": list(content) or [adf_p("")]}


def rich_description() -> dict:
    """One document that uses every ADF node and mark the renderer must support (plus text marks combined)."""
    return adf_doc(
        {"type": "heading", "attrs": {"level": 1}, "content": [t("Heading one")]},
        {"type": "heading", "attrs": {"level": 3}, "content": [t("Heading three")]},
        adf_p(t("bold", "strong"), t(" "), t("italic", "em"), t(" "), t("struck", "strike"), t(" "), t("code", "code"), t(" "),
              t("underlined", "underline"), t(" "), t("link", {"type": "link", "attrs": {"href": "https://example.com/a?b=1"}}),
              t(" "), t("both", "strong", "em")),
        adf_p(t("line one"), {"type": "hardBreak"}, t("line two")),
        {"type": "bulletList", "content": [li(adf_p(t("one")), {"type": "bulletList", "content": [li(adf_p(t("nested")))]}), li(adf_p(t("two")))]},
        {"type": "orderedList", "attrs": {"order": 1}, "content": [li(adf_p(t("first"))), li(adf_p(t("second")))]},
        {"type": "taskList", "attrs": {"localId": "t1"}, "content": [
            {"type": "taskItem", "attrs": {"localId": "a", "state": "DONE"}, "content": [t("done task")]},
            {"type": "taskItem", "attrs": {"localId": "b", "state": "TODO"}, "content": [t("open task")]}]},
        {"type": "codeBlock", "attrs": {"language": "python"}, "content": [t("print('hi')\nprint(\"there\")")]},
        {"type": "blockquote", "content": [adf_p(t("quoted text"))]},
        {"type": "panel", "attrs": {"panelType": "warning"}, "content": [adf_p(t("careful"))]},
        {"type": "rule"},
        {"type": "table", "attrs": {"isNumberColumnEnabled": False, "layout": "default"}, "content": [
            {"type": "tableRow", "content": [cell("tableHeader", adf_p(t("Name"))), cell("tableHeader", adf_p(t("Value")))]},
            {"type": "tableRow", "content": [cell("tableCell", adf_p(t("a | b"))), cell("tableCell", adf_p(t("1"), {"type": "hardBreak"}, t("2")))]}]},
        adf_p(t("Hi "), {"type": "mention", "attrs": {"id": "acct-9", "text": "@Dana Reviewer"}}, t(" "),
              {"type": "emoji", "attrs": {"shortName": ":smile:", "text": "😄"}}, t(" due "),
              {"type": "date", "attrs": {"timestamp": "1790000000000"}}, t(" "),
              {"type": "status", "attrs": {"text": "In review", "color": "blue"}}, t(" "),
              {"type": "inlineCard", "attrs": {"url": "https://acme.atlassian.net/browse/MD-2"}}),
        {"type": "mediaSingle", "attrs": {"layout": "center"}, "content": [{"type": "media", "attrs": {"type": "file", "id": "m1", "collection": "", "alt": "diagram.png"}}]},
        {"type": "expand", "attrs": {"title": "Details"}, "content": [adf_p(t("hidden detail"))]},
        {"type": "blockCard", "attrs": {"url": "https://example.com/doc"}},
        {"type": "decisionList", "attrs": {"localId": "d1"}, "content": [
            {"type": "decisionItem", "attrs": {"localId": "d", "state": "DECIDED"}, "content": [t("we decided")]}]},
    )


def unknown_node_description() -> dict:
    """A node type the renderer does not know: its text must survive with a visible marker."""
    return adf_doc(adf_p(t("before")), {"type": "futureWidget", "attrs": {"x": 1}, "content": [adf_p(t("text inside the widget"))]}, adf_p(t("after")))


def populate(fake: FakeJira) -> dict[str, str]:
    """A small site shaped like the owner's MD and MP projects; returns {label: key}."""
    keys = {}
    keys["epic"] = fake.add_issue("MD", "P3 Jira connector", issuetype="Epic", description=adf_doc(adf_p("An epic.")), labels=["phase-3"])
    keys["rich"] = fake.add_issue("MD", "Rich description: everything", description=rich_description(), comments=["first comment", "second comment"],
                                  labels=["a", "b"], components=["engine"], assignee="Dana Reviewer", priority="High", status="In Progress",
                                  status_category="In Progress", parent=keys["epic"], due="2026-11-01", fix_versions=["1.0"], sprints=["Sprint 7"],
                                  attachments=[("diagram.png", 20480), ("notes.txt", 12)])
    keys["sub"] = fake.add_issue("MD", "A sub-task", parent=keys["rich"], status="Done", status_category="Done")
    keys["linked"] = fake.add_issue("MD", "Linked issue", links=[])
    fake.update_issue(keys["rich"], links=[{"type": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
                                            "outwardIssue": {"key": keys["linked"], "fields": {"summary": "Linked issue"}}}])
    keys["plain"] = fake.add_issue("MD", "Plain issue, no description")
    keys["unknown"] = fake.add_issue("MD", "Unknown node", description=unknown_node_description())
    keys["thread"] = fake.add_issue("MP", "Long comment thread", description=adf_doc(adf_p("Many comments follow.")),
                                    comments=[f"comment number {n}" for n in range(1, 121)])
    return keys
