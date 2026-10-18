"""Search hides authored notes while keeping literal Markdown code searchable."""

from __future__ import annotations

import re

from markdown_it import MarkdownIt
from markdown_it.rules_inline import StateInline, backtick


def _remove_comments(text: str, parser: MarkdownIt | None = None) -> str:
    """Cut complete comments once, preserving code spans and escaped openers.

    CommonMark owns backtick pairing, including different delimiter lengths
    and multiline spans. Its cache also avoids rescanning unmatched runs.
    A missing comment closer ends the scan: half a note stays authored text,
    just as it does in the frontend's comment plugin.
    """
    if "<!--" not in text:
        return text
    state = StateInline(text, parser, {}, []) if parser else None
    parts: list[str] = []
    kept = cursor = 0
    while cursor < len(text):
        if state and text[cursor] == "\\":
            cursor += 2
        elif state and text[cursor] == "`":
            state.pos = cursor
            backtick(state, True)
            cursor = state.pos
        elif state and text.startswith(("<!-->", "<!--->"), cursor):
            # These are separate visible inline tokens in the frontend. A later
            # closer must not turn the text between them into a hidden note.
            cursor += 6 if text.startswith("<!--->", cursor) else 5
        elif text.startswith("<!--", cursor):
            end = text.find("-->", cursor + 4)
            if end == -1:
                break
            parts.append(text[kept:cursor])
            kept = cursor = end + 3
        else:
            cursor += 1
    parts.append(text[kept:])
    return "".join(parts)


def without_comments(text: str) -> str:
    """Retain source bytes outside hidden comments, including literal code.

    Most stored posts need only the linear scan. When code or escapes are
    possible, CommonMark's block maps distinguish fenced/indented code from
    paragraphs. Each paragraph gets its own backtick state: an unmatched
    opener must not swallow a later paragraph's hidden response metadata.

    Inline parsing is deliberately disabled in the block pass. Its HTML
    rule uses a lazy comment regex, reintroducing the repeated-unterminated-
    opener cost this extractor must avoid. The scanner handles comments;
    only the existing backtick rule is needed inside an inline block.
    """
    if "<!--" not in text:
        return text
    if not any(marker in text for marker in ("`", "~~~", "    ", "\t", "\\", "<!-->", "<!--->")):
        return _remove_comments(text)

    parser = MarkdownIt("commonmark").disable("inline")
    offsets = [0]
    offsets.extend(match.end() for match in re.finditer(r"\r\n?|\n", text))
    if offsets[-1] != len(text):
        offsets.append(len(text))
    parts: list[str] = []
    cursor = 0
    for token in parser.parse(text):
        if token.map is None or token.type not in ("inline", "fence", "code_block"):
            continue
        start, end = (offsets[line] for line in token.map)
        parts.append(_remove_comments(text[cursor:start]))
        source = text[start:end]
        parts.append(_remove_comments(source, parser) if token.type == "inline" else source)
        cursor = end
    parts.append(_remove_comments(text[cursor:]))
    return "".join(parts)
