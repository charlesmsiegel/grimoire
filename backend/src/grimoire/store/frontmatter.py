"""`---`-fenced frontmatter with string-scalar values (dependency-light)."""

from __future__ import annotations

from pathlib import Path


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
    if rest.startswith("---"):
        # empty block: the opening fence's own newline doubles as the
        # separator, so the closing fence sits at rest[0] with no leading
        # "\n" for the usual "\n---" search to find.
        block, after = "", rest[3:]
    else:
        end = rest.find("\n---")
        if end == -1:
            return {}, text
        block = rest[:end]
        after = rest[end + 4:]
    if after.startswith("\n"):
        after = after[1:]
    if after.startswith("\n"):
        after = after[1:]
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
            if line.startswith("---"):
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
        lines.append(f"{key}: {_quote('' if value is None else str(value))}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + "\n" + body
