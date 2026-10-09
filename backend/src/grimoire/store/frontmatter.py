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


class RecordUnreadableError(OSError):
    """A `config.md` or `campaign.md` a writer would rewrite that is there but
    holds no record: zero bytes, or a frontmatter block that is unfenced or
    never closed (a sync placeholder mid-download, a conflict stub, a hand edit
    that lost its closing `---`). `parse_frontmatter` reads all of those as
    `{}`, and a migration that took that for a record with no settings would
    publish a marker-only file over it -- which a sync client can then upload
    over the real one -- and mark it, so the settings it held are never read
    again. The rule `llm_connections`' strict read keeps for a connection.

    Moved here from `inference.migrate` (slice I), so the migration and
    retirement -- which the migration calls, and so cannot import it --
    share one strict read (`read_record`). A writer never replaces a record
    that raised it."""


def read_record(path: Path, what: str, *,
                require: str | None = None) -> tuple[dict[str, str], str]:
    """`path`'s frontmatter and body, read strictly, for a writer that will
    rewrite it: `RecordUnreadableError` when it holds no record (see there)
    -- empty or whitespace, unfenced, never closed, or a block with no keys,
    since every record this app writes has keys -- and, with `require`, when
    that key is missing or empty in it (a `config.md` with no format marker
    is not one retirement may write).

    An absent file raises `FileNotFoundError`, and the read's own `OSError` /
    `UnicodeDecodeError` pass through: every caller catches them beside
    `RecordUnreadableError`. `what` names the file in the message."""
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    if not meta:
        raise RecordUnreadableError(f"{what} holds no settings (empty, or unfenced); "
                                    "it is left as it is and retried on the next start")
    if require is not None and not str(meta.get(require, "") or "").strip():
        raise RecordUnreadableError(f"{what} holds no {require}; "
                                    "it is left as it is and retried on the next start")
    return meta, body
