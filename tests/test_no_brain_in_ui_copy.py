"""MD-146: no user-facing string says "brain". The place the files go is the "output folder".

Same pattern as the MD-139 "no bare identifier" test: it scans the sources, so a new string that says "brain" fails here.
Exempt: CLI text and flags (cli.py, brain.py's CLI error), the MCP server's tool text for agents (mcp_server.py, brainquery.py),
logs, code identifiers and comments.
"""
import ast
import re
from pathlib import Path

PKG = Path(__file__).parent.parent / "src" / "mdpipeline"
WORD = re.compile(r"\bbrains?\b|brain-|-brain", re.I)
NOT_UI = {"cli.py", "brain.py", "mcp_server.py", "brainquery.py", "snippets.py", "log.py"}


def python_prose():
    for path in sorted(PKG.glob("*.py")):
        if path.name in NOT_UI:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body
                      and isinstance(n.body[0], ast.Expr) and isinstance(getattr(n.body[0], "value", None), ast.Constant)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                if WORD.search(node.value) and " " in node.value.strip():
                    yield f"{path.name}:{node.lineno}: {node.value[:90]!r}"


def javascript_prose():
    for path in sorted((PKG / "ui_static").glob("*.js")):
        text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), path.read_text(encoding="utf-8"), flags=re.S)   # block comments
        for number, line in enumerate(text.splitlines(), 1):
            code = re.sub(r"^\s*//.*|\s//\s.*$", "", line)
            for m in re.finditer(r"(?<![.\w/])brains?\b|\bBrain\b|\w-brain\b|\bbrain-\w", code, re.I):
                context = code[max(0, m.start() - 25):m.end() + 25]
                if re.search(r"brain\.\w|brain:|\{brain|brain\}|brain\)|\(brain|, brain|brain,", context) and not re.search(
                        r"\b(the|this|your|in|from) brain|brain('s| copy| files| edits)", context):
                    continue    # a code identifier such as S.state.brain.path, not prose
                yield f"{path.name}:{number}: ...{context}..."


def test_no_user_facing_python_string_says_brain():
    assert list(python_prose()) == []


def test_no_user_facing_ui_string_says_brain():
    assert list(javascript_prose()) == []


def test_no_visible_label_in_the_page_is_just_the_word_brain():
    """MD-146 reopened: `<span>brain</span> ${esc(st.brain.path)}` slipped past the identifier filter above because the same line holds `brain.path`."""
    found = []
    for path in sorted((PKG / "ui_static").glob("*.js")) + sorted((PKG / "ui_static").glob("*.html")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r">\s*brains?\s*<", line, re.I):
                found.append(f"{path.name}:{number}: {line.strip()[:100]}")
    assert found == []
