"""MD-14: merge a provenance block into YAML front-matter without touching the author's keys."""
from pathlib import Path

import pytest
import yaml

from mdpipeline.frontmatter import (
    FrontMatterError,
    build_provenance,
    content_hash,
    merge_provenance,
    parse_document,
)

PROV = {
    "source": "local",
    "pipeline": "biz",
    "source_label": "docs",
    "source_path": "a/b.md",
    "source_root": "/src/docs",
    "source_modified": "2026-09-30T14:02:00Z",
    "synced_at": "2026-10-01T09:00:00Z",
    "content_hash": "sha256:abc",
}


def merged(text: str | bytes, prov=PROV) -> str:
    data = text.encode() if isinstance(text, str) else text
    return merge_provenance(data, prov).decode()


def front(text: str) -> dict:
    meta, _ = parse_document(text)
    return meta


def test_file_without_front_matter_gets_one_and_body_is_unchanged():
    body = "# Title\n\ntext  \nlast line no newline"
    out = merged(body)
    assert out.startswith("---\nprovenance:\n")
    meta, rest = parse_document(out)
    assert meta == {"provenance": PROV}
    assert rest == body


def test_all_the_authors_keys_and_values_are_preserved():
    src = ('---\ntitle: "Pricing: draft"\ntags: [a, b]\nnested:\n  k: 1\n  list:\n    - x\n    - y\n'
           'multi: |\n  line1\n  line2\nflag: true\nn: 007\ndate: 2026-01-02\n---\n# Body\n')
    out = merged(src)
    original = yaml.safe_load(src.split("---\n")[1])
    meta = front(out)
    assert {k: v for k, v in meta.items() if k != "provenance"} == original
    assert meta["provenance"] == PROV


def test_the_authors_front_matter_text_is_kept_verbatim_including_comments():
    fm = "# my comment\ntitle:   spaced   # trailing\nkeys: {a: 1}\n"
    out = merged(f"---\n{fm}---\nbody\n")
    assert out.startswith(f"---\n{fm}provenance:\n")
    assert out.endswith("---\nbody\n")


def test_body_is_copied_byte_for_byte_with_crlf_and_trailing_whitespace():
    body = "line1\r\n\r\nline2   \r\n\t\r\n"
    out = merge_provenance(("---\ntitle: x\n---\n" + body).encode(), PROV)
    assert out.endswith(body.encode())


def test_crlf_front_matter_gets_crlf_block():
    out = merge_provenance(b"---\r\ntitle: x\r\n---\r\nbody\r\n", PROV)
    assert b"provenance:\r\n" in out
    assert b"\r\n" in out and b"\n" not in out.replace(b"\r\n", b"")


def test_unicode_survives():
    out = merged("---\ntitle: שלום ✓\n---\nגוף\n")
    assert front(out)["title"] == "שלום ✓"
    assert out.endswith("גוף\n")


def test_empty_front_matter_is_valid():
    out = merged("---\n---\nbody\n")
    assert front(out) == {"provenance": PROV}


def test_bom_is_preserved():
    out = merge_provenance("﻿---\ntitle: x\n---\nbody".encode(), PROV).decode()
    assert out.startswith("﻿---\ntitle: x\n")


def test_merge_is_deterministic():
    assert merged("---\na: 1\n---\nx") == merged("---\na: 1\n---\nx")


@pytest.mark.parametrize(
    "bad",
    [
        "---\ntitle: x\nno closing delimiter\n",
        "---\nkey: [unclosed\n---\nbody",
        "---\n- just\n- a list\n---\nbody",
        "---\nplain scalar\n---\nbody",
        "---\nprovenance:\n  pipeline: someone-else\n---\nbody",
        "---\na: 1\n  b: bad indent\n---\nx",
    ],
)
def test_malformed_or_conflicting_front_matter_raises_a_clear_error(bad):
    with pytest.raises(FrontMatterError):
        merge_provenance(bad.encode(), PROV)


def test_invalid_utf8_raises():
    with pytest.raises(FrontMatterError, match="UTF-8"):
        merge_provenance(b"\xff\xfe bad", PROV)


def test_content_hash_is_sha256_of_the_source_bytes():
    import hashlib

    assert content_hash(b"abc") == "sha256:" + hashlib.sha256(b"abc").hexdigest()


def test_build_provenance_fields_and_formats():
    p = build_provenance(pipeline="biz", source_label="docs", source_path="a/b.md", source_root="/src/docs",
                         source_mtime=1790776920.0, synced_at=1790845200.0, source_bytes=b"x")
    assert list(p) == list(PROV)
    assert p["source"] == "local"
    assert p["source_modified"].endswith("Z") and "T" in p["source_modified"]
    assert p["synced_at"].endswith("Z")
    assert p["content_hash"] == content_hash(b"x")


def test_parse_document_of_text_without_front_matter():
    assert parse_document("# hi\n") == ({}, "# hi\n")


def test_parse_document_tolerates_broken_front_matter():
    meta, body = parse_document("---\nkey: [oops\n---\nbody")
    assert meta == {}


SAMPLE_FILES = {
    "raw/a.md": "---\ntitle: A\nauthor: Dana Reviewer\ntags: [x, y]\n---\nBody A\n",
    "wiki/b.md": "---\nauthor: Mia Owner\nreviewers:\n  - Dana Reviewer\n---\n# B\n\ntext\n",
    "work/c.md": "No front matter at all.\n",
    "work/d.md": "---\nkey: [oops\n---\nbroken\n",
}


def test_sample_files_never_lose_an_author_key(tmp_path):
    for rel, text in SAMPLE_FILES.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(text.encode())
    checked = 0
    for path in sorted(tmp_path.glob("**/*.md")):
        data = path.read_bytes()
        try:
            out = merge_provenance(data, PROV)
        except FrontMatterError:
            continue  # reported and skipped by the engine, never half-merged
        before, _ = parse_document(data.decode())
        after, body = parse_document(out.decode())
        assert {k: v for k, v in after.items() if k != "provenance"} == before, path
        assert out.decode().endswith(parse_document(data.decode())[1]), path
        checked += 1
    assert checked >= 3  # the broken file is skipped; the others are proven
