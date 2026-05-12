"""Markdown + YAML frontmatter parser.

Library and campaign entity cards are markdown files with a YAML frontmatter
block delimited by ``---`` lines (see specs/18-library.md). This module
parses, renders, reads and writes that format.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.files.yaml_io import YamlError, dump_yaml, parse_yaml

_FENCE = "---"
_BOM = "﻿"
_OPENING_RE = re.compile(r"\A---[ \t]*\r?\n")
_CLOSING_RE = re.compile(r"(?:\A|\r?\n)---[ \t]*(?:\r?\n|\Z)")


class FrontmatterError(ValueError):
    """Raised when a document's frontmatter is malformed."""


@dataclass(slots=True)
class ParsedDocument:
    """A markdown document split into structured frontmatter and prose body."""

    frontmatter: dict[str, Any] = field(default_factory=dict)
    body: str = ""


def parse_frontmatter(text: str) -> ParsedDocument:
    """Split a string into ``(frontmatter, body)``.

    A document without a leading ``---`` fence parses as an empty frontmatter
    and the whole text as the body. A document with an opening fence but no
    closing fence is an error.
    """
    if text.startswith(_BOM):
        text = text[len(_BOM) :]
    opener = _OPENING_RE.match(text)
    if not opener:
        return ParsedDocument(frontmatter={}, body=text)

    rest = text[opener.end() :]
    closer = _CLOSING_RE.search(rest)
    if not closer:
        raise FrontmatterError("frontmatter opening fence has no matching closing fence")

    yaml_block = rest[: closer.start()]
    body = rest[closer.end() :]

    try:
        loaded = parse_yaml(yaml_block) if yaml_block.strip() else {}
    except YamlError as exc:
        raise FrontmatterError(f"invalid YAML in frontmatter: {exc}") from exc

    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise FrontmatterError(f"frontmatter must be a YAML mapping, got {type(loaded).__name__}")

    return ParsedDocument(frontmatter=loaded, body=body)


def render_markdown(doc: ParsedDocument) -> str:
    """Render a ``ParsedDocument`` back to a string.

    An empty frontmatter dict is omitted entirely; otherwise a fenced YAML
    block precedes the body. The body is left untouched (no trimming, no
    newline normalization) so round-trips of unchanged content are exact.
    """
    if not doc.frontmatter:
        return doc.body

    yaml_text = dump_yaml(doc.frontmatter)
    if not yaml_text.endswith("\n"):
        yaml_text += "\n"

    body = doc.body
    if body and not body.startswith("\n"):
        body = "\n" + body
    elif not body:
        body = "\n"

    return f"{_FENCE}\n{yaml_text}{_FENCE}{body}"


def read_markdown(path: str | Path) -> ParsedDocument:
    """Read and parse a markdown file with optional YAML frontmatter."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        return parse_frontmatter(text)
    except FrontmatterError as exc:
        raise FrontmatterError(f"{path}: {exc}") from exc


def write_markdown(path: str | Path, doc: ParsedDocument) -> None:
    """Write a ``ParsedDocument`` to ``path`` as UTF-8, creating parents."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_markdown(doc), encoding="utf-8")
