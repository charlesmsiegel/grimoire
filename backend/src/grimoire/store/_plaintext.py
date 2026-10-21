"""#136: labelled prose is a draft, not a card import that already wrote files.

This route helper stays private to preserve the frozen store facade.
Only known headings divide fields; everything else stays prose. AI refines the
same six-field proposal on explicit request, never a stored character. Adding
another heading spelling belongs in ALIASES; neither parser needs a new flow.
"""

from __future__ import annotations

import bisect
import re
import unicodedata
from collections.abc import Callable
from functools import partial
from pathlib import PurePosixPath

from markdown_it import MarkdownIt
from markdown_it.rules_inline import StateInline, backtick, emphasis, html_inline, image, link

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


def _record_delimiters(state: StateInline, silent: bool) -> bool:
    """CommonMark's own emphasis rule, noting where each delimiter sits.

    It pushes one delimiter per star, in source order from `state.pos`; once
    the line is parsed, pairing has set each matched opener's `end` to its
    closer. Recording the positions lets `_inline_value` remove exactly the
    stars CommonMark pairs with the label's, and nothing else (#136).
    """
    start, before = state.pos, len(state.delimiters)
    matched = emphasis.tokenize(state, silent)
    if matched and not silent:
        # The line's own parse reaches its first star before any nested parse
        # (an image's alt text) can: keep that root state, not the last one.
        state.env.setdefault("state", state)
        positions = state.env.setdefault("positions", {})
        for index in range(before, len(state.delimiters)):
            positions[(id(state.delimiters), index)] = start + index - before
    return matched


_EMPHASIS = MarkdownIt("commonmark")
_EMPHASIS.inline.ruler.at("emphasis", _record_delimiters)


def _inline_value(content: str, label: str, value: str) -> str:
    """Only a label's emphasis delimiters belong to the parser.

    Stripping arbitrary stars from a value corrupts authored Markdown such
    as `Greeting: *{{char}} waves.*`, including a value made only of stars.
    A label that opens emphasis (`**Name`, `*Name`) closes it wherever
    CommonMark says it does: `**Name:** Mara`, `**Name: Mara**`,
    `**Personality: stern** but *kind*`, or inside a run that closes a
    nested span too (`**Greeting: *{{char}} waves.***`). CommonMark decides
    because every hand-written approximation of it here missed a case: a
    code span's literal stars, a nested pair of the same width, a combined
    closing run. Only the stars paired with the line's leading run are
    removed, and an opener nothing closes is left alone.
    """
    if not label.startswith("*"):
        return value.lstrip()
    lead = len(content) - len(content.lstrip("*"))
    env: dict = {}
    _EMPHASIS.parseInline(content, env)
    removed: set[int] = set()
    if "state" in env:
        delimiters = env["state"].delimiters
        positions = env["positions"]
        for index, delimiter in enumerate(delimiters):
            at = positions.get((id(delimiters), index))
            if at is not None and at < lead and delimiter.end != -1:
                removed |= {at, positions[(id(delimiters), delimiter.end)]}
    begin = len(label) + 1
    return "".join(ch for at, ch in enumerate(content[begin:], begin) if at not in removed).lstrip()


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
            first = _newlines_before(state, start) + 1
            state.tokens[-1].meta["literal_lines"] = (first, first + span.count("\n"))
    return matched


def _newlines_before(state: StateInline, at: int) -> int:
    """How many line breaks precede `at` in this inline source.

    Counting `state.src[:at]` per span rescanned an ever longer prefix, so a
    long source full of multi-line spans was quadratic. The offsets are found
    once per source and kept on the parse's env, beside the source itself so
    a reused id can never hand back another source's offsets (#136).
    """
    cache = state.env.setdefault("_newlines", {})
    entry = cache.get(id(state.src))
    if entry is None or entry[0] is not state.src:
        entry = (state.src, [at for at, ch in enumerate(state.src) if ch == "\n"])
        cache[id(state.src)] = entry
    return bisect.bisect_left(entry[1], at)


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
    value = _inline_value(content, label, value)
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


def name_problem(name: str) -> str | None:
    """Why a character name cannot be saved, or None when it can.

    A name is one line: the review form's single-line input would hide a
    break that Save still sends, and the stored card and any baked `{{char}}`
    would keep it while the folder's front matter flattened it (#136).
    """
    if not folder_fits(name):
        return f"A character name must be at most {MAX_NAME_CHARS} characters once it is folded for a folder name."
    if any(unicodedata.category(ch) in ("Cc", "Zl", "Zp") for ch in name):
        return "A character name must be a single line of text."
    return None


def _one_line(text: str) -> str:
    """A name taken from the source keeps its words, never a control or break
    character: `name_problem` would refuse the unchanged draft at Refine and
    Save, and the review form can render one as invisible whitespace (#136)."""
    return "".join(" " if unicodedata.category(ch) in ("Cc", "Zl", "Zp") else ch for ch in text).strip()


def _clip(text: str) -> str:
    """Shorten a guessed name until its folder fits. The reviewer still has the
    whole text in the description, so a guess may be clipped; an explicit name
    may not.

    Capping to MAX_NAME_CHARS first bounds the shrink loop to at most that many
    slugifications of a short string, however long the heading or first line
    is; re-slugifying every prefix of the whole line was quadratic (#136).
    """
    text = _one_line(text)[:MAX_NAME_CHARS]
    while text and not folder_fits(text):
        text = text[:-1]
    return text


def _resolve_name(explicit: str, title: str, filename: str, lines: list[str]) -> str:
    """An explicit name wins, then a document title, then a filename, then the
    first line. Only an explicit name is refused past the folder limit: a guess
    is clipped, and the reviewer still has the full text in the description."""
    explicit = _one_line(explicit)
    if problem := name_problem(explicit):
        raise cards.CardParseError(problem)
    fallback = _clip(PurePosixPath(filename.replace("\\", "/")).stem.strip())
    first = next(line.strip() for line in lines if line.strip())
    return explicit or title or fallback or _clip(first)


class _Split:
    """`parse`'s running state: which field lines go to, the guessed title, and
    whether a name label or any text has been seen yet."""

    def __init__(self) -> None:
        self.parts: dict[str, list[str]] = {key: [] for key in FIELDS}
        self.current = "description"
        self.title = ""
        self.name_seen = False
        # Whether any field holds text yet. Rejoining every field per heading to
        # ask that again was quadratic in a source of many headings (#136).
        self.content_seen = False

    @property
    def name_free(self) -> bool:
        # A name label counts once, and never inside example dialogue (#136).
        return not self.name_seen and self.current != "mes_example"

    def add(self, line: str) -> None:
        """Append `line` to the current field, noting whether it carries text."""
        self.parts[self.current].append(line)
        self.content_seen = self.content_seen or bool(line.strip())

    def open_field(self, field: str, value: str, colon: str) -> None:
        """A known label starts `field`; a value after its colon is its first line."""
        self.current = field
        self.name_seen = self.name_seen or field == "name"
        # Two labelled sections in one field must not run together.
        if self.parts[field] and self.parts[field][-1].strip():
            self.parts[field].append("")
        if colon and value:
            self.add(value)
            if field == "name":
                self.current = "description"

    def prose(self, line: str, heading: re.Match[str] | None) -> None:
        """An unlabelled line. An unknown heading returns to description and may
        title the document; a name takes only its first line of text."""
        if heading:
            self.current = "description"
            if not self.title and not self.content_seen:
                self.title = _clip(heading[1].strip())
        self.add(line)
        if self.current == "name" and line.strip():
            self.current = "description"


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
    split = _Split()
    for index, line in enumerate(lines):
        if index in literal_lines:
            split.add(line)
            continue
        stripped = line.strip()
        heading = _HEADING.fullmatch(stripped)
        field, value, colon = _label_of(line, stripped, heading, name_free=split.name_free)
        if field:
            split.open_field(field, value, colon)
        else:
            split.prose(line, heading)
    # Blank edge lines are separators, but horizontal whitespace can carry
    # Markdown meaning (indented code, hard breaks). Keep it in prose fields.
    data = {key: "\n".join(value).strip("\n") for key, value in split.parts.items()}
    data["name"] = _resolve_name(data["name"].strip(), split.title, filename, lines)
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
    if problem := name_problem(result["name"]):
        raise cards.CardParseError(problem)
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
