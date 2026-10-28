"""MD-44: the fixture corpus is well formed and covers every ADF node type the connector must render."""
from fake_jira import FakeJira
from jira_fixtures import populate, rich_description


def node_types(node, found=None):
    found = set() if found is None else found
    if isinstance(node, dict):
        if "type" in node:
            found.add(node["type"])
        for mark in node.get("marks", []):
            found.add("mark:" + mark["type"])
        for child in node.get("content", []):
            node_types(child, found)
    return found


REQUIRED = {"paragraph", "heading", "text", "hardBreak", "bulletList", "orderedList", "listItem", "taskList", "taskItem", "codeBlock",
            "blockquote", "panel", "rule", "table", "tableRow", "tableHeader", "tableCell", "mention", "emoji", "date", "status",
            "inlineCard", "mediaSingle", "media", "expand", "blockCard", "decisionList", "decisionItem",
            "mark:strong", "mark:em", "mark:strike", "mark:code", "mark:underline", "mark:link"}


def test_the_rich_description_covers_every_node_type_the_renderer_must_handle():
    assert REQUIRED <= node_types(rich_description())


def test_the_populated_site_has_the_shapes_the_stories_need():
    import base64

    fake = FakeJira()
    keys = populate(fake)
    auth = "Basic " + base64.b64encode(b"me@example.com:tok").decode()
    status, _, _ = fake("GET", f"https://acme.atlassian.net/rest/api/3/issue/{keys['rich']}?fields=*all", {"Authorization": auth}, None)
    assert status == 200
    assert len(fake._find(keys["thread"])["comments"]) == 120
    assert fake._find(keys["sub"])["parent"] == keys["rich"] and fake._find(keys["rich"])["attachments"]
