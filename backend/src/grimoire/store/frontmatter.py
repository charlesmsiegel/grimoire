"""`---`-fenced frontmatter with string-scalar values (dependency-light)."""

from __future__ import annotations

import re
from pathlib import Path

#: The closing fence: a line that is exactly `---`. A line, not a prefix --
#: `----` is a body's first line (or a horizontal rule inside a block nobody
#: closed), and both parsers below ask this same question so a list endpoint
#: and a full read cannot disagree about one file.
_CLOSING_FENCE = re.compile(r"^---$", re.MULTILINE)


def _is_fence_line(line: str) -> bool:
    return line.rstrip("\n") == "---"


#: Every character `str.splitlines` ends a line at -- which is how
#: `parse_frontmatter` splits the block, so a value holding one would be read
#: back as two lines, the second a key of the writer's choosing.
#: `test_frontmatter_line_breaks` holds this to `splitlines` itself.
LINE_BREAKS = frozenset("\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")


def breaks_line(value: str) -> bool:
    """Whether `value` holds a line boundary the parser would split on."""
    return any(ch in LINE_BREAKS for ch in value)


def _one_line(value: str) -> str:
    """`value` with every line boundary made a space: the writer's backstop.
    A caller that takes text from a request refuses a break with a 400 first
    (`inference.settings._string`, the provider routes); this keeps any other
    writer from putting a key into the block, where the value would otherwise
    have been cut at the break on read anyway."""
    if not breaks_line(value):
        return value
    return "".join(" " if ch in LINE_BREAKS else ch for ch in value)


def _needs_quotes(value: str) -> bool:
    if value == "":
        return True
    if value != value.strip():
        return True
    return any(c in value for c in ":#'\"")


def _quote(value: str) -> str:
    if not _needs_quotes(value):
        return value
    return "'" + value.replace("'", "''") + "'"


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split `---`-fenced frontmatter from the body. String scalars only."""
    if not text.startswith("---\n"):
        return {}, text
    rest = text[4:]
    # `^` in MULTILINE also matches at rest[0], which is where an EMPTY block's
    # closing fence sits: the opening fence's own newline doubles as the
    # separator, and a search for "\n---" found nothing there.
    fence = _CLOSING_FENCE.search(rest)
    if fence is None:
        return {}, text
    block = rest[:fence.start()]
    after = rest[fence.end():]
    after = after.removeprefix("\n")
    after = after.removeprefix("\n")
    meta: dict[str, str] = {}
    for line in block.splitlines():
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        meta[key.strip()] = _unquote(value)
    return meta, after


def parse_frontmatter_head(path: Path) -> dict[str, str]:
    """parse_frontmatter's meta dict, reading only the header block from disk.
    List endpoints use this so a scene with a megabyte of transcript costs a
    few buffered lines, not a whole-file read. Same shape as the full parser:
    {} when the fence is missing or never terminated."""
    meta: dict[str, str] = {}
    # universal newlines, same as the read_text the full parser sees
    with open(path, encoding="utf-8") as f:
        if f.readline() != "---\n":
            return {}
        for line in f:
            if _is_fence_line(line):
                return meta
            if not line.strip():
                continue
            key, sep, value = line.partition(":")
            if sep:
                meta[key.strip()] = _unquote(value)
    return {}  # unterminated block: the full parser treats this as no frontmatter


def dump_frontmatter(meta: dict[str, str], body: str) -> str:
    lines = ["---"]
    for key, value in meta.items():
        lines.append(f"{key}: {_quote(_one_line('' if value is None else str(value)))}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + "\n" + body
