"""#136: labelled prose is a draft, not a card import that already wrote files.

This route helper stays private to preserve the frozen store facade.
Only known headings divide fields; everything else stays prose. AI refines the
same six-field proposal on explicit request, never a stored character. Adding
another heading spelling belongs in ALIASES; neither parser needs a new flow.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import partial
from pathlib import PurePosixPath

from markdown_it import MarkdownIt
from markdown_it.rules_inline import StateInline, backtick, html_inline, image, link

from .. import prompts, schemas
from . import cards, paths

FIELDS = ("name", "description", "personality", "scenario", "first_mes", "mes_example")
# Bound both parsing and the optional prompt, without silently clipping a
# source and then presenting its partial parse as complete. A character dump
# has room for all six prose fields; a whole book needs a different importer.
MAX_TEXT_CHARS = 64000
# A title/name guess also remains in description. Even an unchanged proposal
# can therefore contain the whole bounded source twice (#136).
MAX_DRAFT_CHARS = MAX_TEXT_CHARS * 2
# A saved character's folder is named from its name, and a filesystem refuses
# a component past 255 bytes. Guesses are clipped to this; an explicit name
# over it is refused, so the reviewer sees why rather than a failed save.
MAX_NAME_CHARS = 200
ALIASES = {
    "name": "name", "character name": "name",
    "description": "description", "appearance": "description",
    "background": "description", "backstory": "description",
    "personality": "personality", "scenario": "scenario",
    "first message": "first_mes", "first mes": "first_mes", "greeting": "first_mes",
    "example dialogue": "mes_example", "example dialog": "mes_example",
    "example messages": "mes_example", "mes example": "mes_example",
}
SCHEMA = {"type": "object", "properties": {key: {"type": "string"} for key in FIELDS},
          "required": list(FIELDS), "additionalProperties": False}
_HEADING = re.compile(r"^#{1,6}\s+(.+?)(?:\s+#+)?$")
# A bullet marker in front of a label: `- **Name:** Mara`. Requires the space,
# so a bold label's own `**` is never mistaken for a bullet.
_LIST_MARKER = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
_STARS = re.compile(r"\*+")


def validate_text(text: str) -> None:
    if not _clean(text):
        raise cards.CardParseError("Enter character text to parse.")
    if len(text) > MAX_TEXT_CHARS:
        raise cards.CardParseError(f"Character text must be at most {MAX_TEXT_CHARS} characters.")


def _clean(text: str) -> str:
    """A BOM is an encoding artifact, never content: the blank check, the
    first-line fallback and the saved name must all agree about it (#136)."""
    return text.replace("\ufeff", "").strip()


def _label(text: str) -> str:
    return text.strip().strip("* ").replace("_", " ").casefold()


def _inline_value(label: str, value: str) -> str:
    """Only a label's emphasis delimiters belong to the parser.

    Stripping arbitrary stars from a value corrupts authored Markdown such
    as `Greeting: *{{char}} waves.*`, including a value made only of stars.
    A label that opens emphasis (`**Name`, `*Name`) and does not close it
    before the colon closes it somewhere in the value: `**Name:** Mara`,
    `**Name: Mara**`, `**Personality: stern** but *kind*`. That closer is the
    first run of the label's width that can close (it follows a non-space)
    while no run of that width opened inside the value is still open -- the
    CommonMark flanking rule, counting runs at least that wide. So an authored
    `*Greeting: *{{char}} waves.*` keeps its own pair, later emphasis never
    hides the label's closer, and a run that closes both at once gives up
    only the label's width (#136).
    """
    if not label.startswith("*") or label.rstrip().endswith("*"):
        return value.lstrip()
    width = 2 if label.startswith("**") else 1
    depth = 0
    for run in _STARS.finditer(value):
        if len(run.group()) < width:
            continue
        # The value starts right after the label's colon, which is no space.
        before = value[run.start() - 1] if run.start() else ":"
        after = value[run.end():run.end() + 1]
        if not before.isspace():
            if not depth:
                # A longer run closes the inner emphasis first and the label's
                # last (`**Greeting: *{{char}} waves.***`), so the label's
                # width comes off its end.
                return (value[:run.end() - width] + value[run.end():]).lstrip()
            depth -= 1
        elif after and not after.isspace():
            depth += 1
    return value.lstrip()


def _literal_span(rule: Callable[[StateInline, bool], bool], state: StateInline, silent: bool) -> bool:
    """Record lines before CommonMark normalizes or nests inline content.

    The existing rules own matching and escapes. Recording a whole image/link
    also covers code nested in alt text, whose child offsets are local to that
    alt text. Keep only continuation lines literal: the opening line can still
    carry a real `Description:` label (#136).
    """
    start = state.pos
    matched = rule(state, silent)
    if matched and not silent and state.tokens:
        span = state.src[start:state.pos]
        if "\n" in span:
            first = state.src[:start].count("\n") + 1
            state.tokens[-1].meta["literal_lines"] = (first, first + span.count("\n"))
    return matched


def _literal_lines(text: str) -> set[int]:
    """CommonMark owns literal scope, fence length and closing rules (#136).

    Block line maps preserve code and raw HTML; inline tokens need the rule above
    because their rendered content has lost its source line breaks/positions.
    """
    # Reference definitions normally disappear from the token stream. Ask
    # CommonMark to retain their source maps too, including duplicates (#136).
    parser = MarkdownIt("commonmark", {"inline_definitions": True})
    for name, rule in (("backticks", backtick), ("html_inline", html_inline), ("image", image), ("link", link)):
        parser.inline.ruler.at(name, partial(_literal_span, rule))
    lines: set[int] = set()
    for token in parser.parse(text):
        if token.map is None:
            continue
        start, end = token.map
        if token.type in ("fence", "code_block", "html_block", "definition"):
            lines.update(range(start, end))
        elif token.type == "inline":
            for child in token.children or []:
                first, last = child.meta.get("literal_lines", (0, 0))
                lines.update(range(start + first, start + last))
    return lines


def _label_of(line: str, stripped: str, heading: re.Match[str] | None,
              *, name_free: bool) -> tuple[str | None, str, str]:
    """The field a line's known label names, its value and its colon, if any.

    `name_free` says whether a `Name:` label may still count. A later one is
    prose: a second character's, or a speaker's in example dialogue (#136).
    """
    content = heading[1] if heading else _LIST_MARKER.sub("", line.lstrip(), count=1)
    label, colon, value = content.partition(":")
    value = _inline_value(label, value)
    field = ALIASES.get(_label(label))
    if field == "name" and not name_free:
        field = None
    if field and (colon or heading or stripped.startswith("**")):
        return field, value, colon
    return None, value, colon


def folder_fits(name: str) -> bool:
    """The folder a character is saved under is named from the SLUG, and case
    folding can grow a name (`İ` lowercases to two code points), so the limit
    is on the slug, not on the name's length (#136)."""
    return len(paths.slugify(name)) <= MAX_NAME_CHARS


def _clip(text: str) -> str:
    """Shorten a guessed name until its folder fits. The reviewer still has the
    whole text in the description, so a guess may be clipped; an explicit name
    may not.

    Capping to MAX_NAME_CHARS first bounds the shrink loop to at most that many
    slugifications of a short string, however long the heading or first line
    is; re-slugifying every prefix of the whole line was quadratic (#136).
    """
    text = text[:MAX_NAME_CHARS]
    while text and not folder_fits(text):
        text = text[:-1]
    return text


def _resolve_name(explicit: str, title: str, filename: str, lines: list[str]) -> str:
    """An explicit name wins, then a document title, then a filename, then the
    first line. Only an explicit name is refused past the folder limit: a guess
    is clipped, and the reviewer still has the full text in the description."""
    if not folder_fits(explicit):
        raise cards.CardParseError(
            f"A character name must be at most {MAX_NAME_CHARS} characters once it is folded for a folder name.")
    fallback = _clip(PurePosixPath(filename.replace("\\", "/")).stem.strip())
    first = next(line.strip() for line in lines if line.strip())
    return explicit or title or fallback or _clip(first)


def parse(text: str, filename: str = "") -> dict:
    """Split headings, not sentences; retain unlabelled input for review.

    An explicit name wins, then a document title, then a filename, then the
    first line. Guessed titles and first lines stay in description: guessing a name
    is never permission to delete the only evidence the reviewer has.
    """
    validate_text(text)
    source = text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = source.split("\n")
    literal_lines = _literal_lines(source)
    parts: dict[str, list[str]] = {key: [] for key in FIELDS}
    current = "description"
    title = ""
    name_seen = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if index in literal_lines:
            parts[current].append(line)
            continue
        heading = _HEADING.fullmatch(stripped)
        # A name label counts once, and never inside example dialogue (#136).
        field, value, colon = _label_of(line, stripped, heading,
                                        name_free=not name_seen and current != "mes_example")
        if field:
            current = field
            name_seen = name_seen or field == "name"
            # Two labelled sections in one field must not run together.
            if parts[current] and parts[current][-1].strip():
                parts[current].append("")
            if colon and value:
                parts[current].append(value)
                if current == "name":
                    current = "description"
            continue
        if heading:
            current = "description"
            if not title and not any("".join(value).strip() for value in parts.values()):
                title = _clip(heading[1].strip())
        parts[current].append(line)
        if current == "name" and stripped:
            current = "description"
    # Blank edge lines are separators, but horizontal whitespace can carry
    # Markdown meaning (indented code, hard breaks). Keep it in prose fields.
    data = {key: "\n".join(value).strip("\n") for key, value in parts.items()}
    data["name"] = _resolve_name(data["name"].strip(), title, filename, lines)
    return cards.to_v3(data)


def draft_data(card: dict) -> dict[str, str]:
    """Only editable character fields reach the refinement prompt (#136).

    A caller cannot use this draft to inject system prompts, assets or other
    card metadata. Those have no source in a plaintext import's review form.
    """
    data = card.get("data")
    if not isinstance(data, dict) or any(not isinstance(data.get(key), str) for key in FIELDS):
        raise cards.CardParseError("The draft must contain all six character text fields.")
    result = {key: data[key] for key in FIELDS}
    if not result["name"].strip():
        raise cards.CardParseError("Give the draft a character name.")
    if not folder_fits(result["name"]):
        raise cards.CardParseError(
            f"A character name must be at most {MAX_NAME_CHARS} characters once it is folded for a folder name.")
    if sum(map(len, result.values())) > MAX_DRAFT_CHARS:
        raise cards.CardParseError(f"The draft must be at most {MAX_DRAFT_CHARS} characters.")
    return result


def build_prompt(text: str, card: dict) -> list[dict]:
    validate_text(text)
    data = draft_data(card)
    return [{"role": "system", "content": prompts.render("plaintext_import/system.j2", schema=SCHEMA)},
            {"role": "user", "content": prompts.render("plaintext_import/user.j2", text=text, draft=data)}]


def parse_output(text: str) -> dict:
    value = schemas.find_value(text)
    if not isinstance(value, dict):
        raise cards.CardParseError("AI refinement did not return a character object. Your draft is unchanged.")
    return cards.to_v3(draft_data({"data": value}))
