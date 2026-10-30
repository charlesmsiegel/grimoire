"""Text normalisation for intent matching.

Rules match on normalised forms; payloads always come from the trimmed
original text, so nothing the player said is rewritten.
"""

from __future__ import annotations

import re
from typing import Literal

Lane = Literal["command", "director"]

_CURLY = str.maketrans({"“": '"', "”": '"', "„": '"', "‟": '"',
                        "‘": "'", "’": "'", "‛": "'"})
_SPACE = re.compile(r"\s+")
_GRAMMAR_PUNCT = re.compile(r"[,.!?;:]")
_MARKER = re.compile(r"^(grimoire|director|gm|narrator|ooc)(?![\w'])(\s*[,:])?")
_ORIGINAL_MARKER = re.compile(
    r"^(grimoire|director|gm|narrator|ooc)(?![\w'’])\s*[,:]?\s*", re.IGNORECASE)
_LANES: dict[str, Lane] = {"grimoire": "command", "director": "director", "gm": "director",
                           "narrator": "director", "ooc": "director"}


def straighten(text: str) -> str:
    """Curly quotes and apostrophes to straight ones, nothing else."""
    return text.translate(_CURLY)


def norm(text: str) -> str:
    return _SPACE.sub(" ", straighten(text).lower()).strip()


def grammar_form(text: str) -> str:
    form = _SPACE.sub(" ", _GRAMMAR_PUNCT.sub("", norm(text))).strip()
    return form.removeprefix("please ")


def first_word(text: str) -> str:
    words = norm(text).split(" ", 1)
    return words[0].rstrip(",:")


def _double_wrapped(normalised: str) -> bool:
    return len(normalised) >= 4 and normalised.startswith("((") and normalised.endswith("))")


def marker_of(text: str) -> tuple[Lane | None, bool]:
    """Lane of a leading marker, and whether a `,`/`:` separates it."""
    normalised = norm(text)
    if _double_wrapped(normalised):
        return "director", True
    match = _MARKER.match(normalised)
    if match is None:
        return None, False
    return _LANES[match.group(1)], match.group(2) is not None


def strip_marker(text: str) -> str:
    """Trimmed original text without its leading lane marker."""
    trimmed = text.strip()
    if _double_wrapped(norm(trimmed)):
        return trimmed[2:-2].strip()
    return _ORIGINAL_MARKER.sub("", trimmed, count=1).strip()


def wrapped(text: str, open_: str, close: str) -> str | None:
    """Inner text (possibly empty) when the whole utterance is one wrapped span, else None."""
    trimmed = straighten(text.strip())
    if len(trimmed) < 2 or not (trimmed.startswith(open_) and trimmed.endswith(close)):
        return None
    inner = trimmed[len(open_):-len(close)]
    if open_ in inner or close in inner:
        return None
    return inner.strip()


def split_sentences(text: str) -> list[str]:
    """Split normalised text at `.!?` runs followed by space, outside quotes."""
    normalised = norm(text)
    sentences: list[str] = []
    start = 0
    quoted = False
    index = 0
    while index < len(normalised):
        char = normalised[index]
        if char == '"':
            quoted = not quoted
        elif char in ".!?" and not quoted:
            end = index
            while end + 1 < len(normalised) and normalised[end + 1] in ".!?":
                end += 1
            if end + 1 < len(normalised) and normalised[end + 1] == " ":
                sentences.append(normalised[start:end + 1])
                start = end + 2
            index = end
        index += 1
    sentences.append(normalised[start:])
    return [sentence.strip() for sentence in sentences if sentence.strip()]
