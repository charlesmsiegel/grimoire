from pathlib import Path

import pytest

from grimoire.files.frontmatter import (
    FrontmatterError,
    ParsedDocument,
    parse_frontmatter,
    read_markdown,
    render_markdown,
    write_markdown,
)


def test_parses_frontmatter_and_body() -> None:
    text = (
        "---\n"
        "id: alistair\n"
        "name: Alistair\n"
        "tags: [vampire, ventrue]\n"
        "---\n"
        "# Alistair\n\nProse here.\n"
    )
    doc = parse_frontmatter(text)
    assert doc.frontmatter == {
        "id": "alistair",
        "name": "Alistair",
        "tags": ["vampire", "ventrue"],
    }
    assert doc.body == "# Alistair\n\nProse here.\n"


def test_no_frontmatter_returns_whole_body() -> None:
    text = "# Just a markdown file\n\nNo frontmatter.\n"
    doc = parse_frontmatter(text)
    assert doc.frontmatter == {}
    assert doc.body == text


def test_empty_frontmatter_block() -> None:
    text = "---\n---\nbody\n"
    doc = parse_frontmatter(text)
    assert doc.frontmatter == {}
    assert doc.body == "body\n"


def test_missing_closing_fence_raises() -> None:
    text = "---\nid: x\nname: Y\n# Body without closer\n"
    with pytest.raises(FrontmatterError):
        parse_frontmatter(text)


def test_non_mapping_frontmatter_raises() -> None:
    text = "---\n- one\n- two\n---\nbody\n"
    with pytest.raises(FrontmatterError):
        parse_frontmatter(text)


def test_invalid_yaml_raises() -> None:
    text = "---\nid: [unclosed\n---\nbody\n"
    with pytest.raises(FrontmatterError):
        parse_frontmatter(text)


def test_crlf_line_endings_supported() -> None:
    text = "---\r\nid: x\r\n---\r\nbody\r\n"
    doc = parse_frontmatter(text)
    assert doc.frontmatter == {"id": "x"}
    assert doc.body == "body\r\n"


def test_leading_bom_tolerated() -> None:
    text = "﻿---\nid: x\n---\nbody\n"
    doc = parse_frontmatter(text)
    assert doc.frontmatter == {"id": "x"}


def test_render_round_trip() -> None:
    doc = ParsedDocument(
        frontmatter={"id": "x", "tags": ["a", "b"]},
        body="# Title\n\nHello.\n",
    )
    rendered = render_markdown(doc)
    reparsed = parse_frontmatter(rendered)
    assert reparsed.frontmatter == doc.frontmatter
    assert reparsed.body == doc.body


def test_render_omits_fence_when_empty() -> None:
    doc = ParsedDocument(frontmatter={}, body="# Plain\n")
    assert render_markdown(doc) == "# Plain\n"


def test_read_and_write_markdown(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "card.md"
    doc = ParsedDocument(
        frontmatter={"id": "alistair", "name": "Alistair"},
        body="# Alistair\n\nProse.\n",
    )
    write_markdown(path, doc)
    assert path.read_text(encoding="utf-8").startswith("---\n")
    loaded = read_markdown(path)
    assert loaded.frontmatter == doc.frontmatter
    assert loaded.body == doc.body


def test_read_markdown_error_includes_path(tmp_path: Path) -> None:
    path = tmp_path / "broken.md"
    path.write_text("---\nid: x\nno closer\n", encoding="utf-8")
    with pytest.raises(FrontmatterError) as info:
        read_markdown(path)
    assert str(path) in str(info.value)


def test_utf8_unicode_preserved(tmp_path: Path) -> None:
    path = tmp_path / "u.md"
    doc = ParsedDocument(frontmatter={"name": "café"}, body="résumé\n")
    write_markdown(path, doc)
    assert read_markdown(path) == doc
