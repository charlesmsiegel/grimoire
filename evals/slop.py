"""Detectors for the natural-prose block, and the lists they read.

Eval-owned rather than production-owned, and deliberately: the app has no
opinion about slop. templates/scene/sections/natural_prose.j2 is prescriptive
and feed-forward, so there is no production constant to borrow -- the same
situation as graders.COLLAPSE_RATIO, which is eval-owned because "the app has
no opinion about a reply being too SHORT". If a production store/slop_drift.py
is ever built, its thresholds become the codebase's definition of "this reply
reads flat" and this module borrows them, as grade_length borrows
length_drift.TRIM.

Every threshold here is a named module constant justified structurally -- from
what the templates and the resolved budget do -- and never from a measurement
over stored content. Each should be tuned against real prompts later.

The sentence splitter is a declared heuristic, not a claim of correctness. It
misses initials, honorifics outside _ABBREVIATIONS, and ellipsis used as a
fragment. That is acceptable because every threshold is calibrated through this
same splitter, so its errors are systematic rather than random.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

from grimoire.store import length_drift, scenes

# --------------------------------------------------------------- sample floors

# `cinematic` targets 900 words. At even 25 words per sentence that is ~36
# sentences, so 12 is a third of a low estimate: high enough for a coefficient
# of variation to mean anything, low enough not to gate a compact reply. Tune
# against real prompts later.
MIN_SENTENCES = 12
# `cinematic` permits at most 7 blocks, and block boundaries are paragraph
# boundaries, so 4 sits comfortably under the ceiling. Tune later.
MIN_PARAGRAPHS = 4

_ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "prof", "st", "lt", "capt", "sgt", "sr", "jr",
    "vs", "etc", "e.g", "i.e",
})

# A terminator, any closing quotes/brackets that follow it, whitespace, then an
# opening quote or a capital. The lookahead is what keeps `"Go." Mara left.`
# from splitting inside the quotation.
#
# Built from chr() calls by codepoint rather than typed characters: straight
# and curly quotes look identical or near-identical in most editors/fonts, so
# a literal paste is how a straight quote silently ends up standing in for a
# curly one (and vice versa) with no visible diff. Each class must contain all
# SIX distinct quote/apostrophe codepoints -- straight and curly, double and
# single -- or LLM prose (which routinely uses curly quotes) merges sentences
# silently. `]` is listed first in _CLOSERS because that is the one character
# in either class that regex treats specially inside a `[...]` class; every
# other character here (including `(` and `[`) is already a literal there.
_CLOSERS = "]" + chr(0x22) + chr(0x201D) + chr(0x2019) + chr(0x27) + ")"
_OPENERS = chr(0x22) + chr(0x201C) + chr(0x2018) + chr(0x27) + "(" + "["
_SENTENCE_BREAK = re.compile(
    "([.!?" + chr(0x2026) + "]+[" + _CLOSERS + "]*)" + r"\s+(?=[" + _OPENERS + r"]*[A-Z])"
)


def normalize(text: str, players: frozenset[str]) -> tuple[str, list[str]]:
    """Split one model reply into (prose, speaker names).

    Both halves come from scenes.split_reply, the production parser: measuring
    `**Mara:**` as a sentence would be nonsense, and a stock name used as a
    speaker label would be invisible if only bodies were returned.

    Block boundaries become paragraph boundaries, which is why the bodies are
    rejoined with a blank line.
    """
    segments = scenes.split_reply(text, players)
    bodies = [length_drift.prose(s["content"]) for s in segments]
    names = [s["speaker"] for s in segments if s["speaker"]]
    return "\n\n".join(b for b in bodies if b.strip()), names


def word_count(s: str) -> int:
    return len(s.split())


def sentences(prose: str) -> list[str]:
    """Split prose into sentences. Declared heuristic -- see module docstring."""
    out: list[str] = []
    start = 0
    for m in _SENTENCE_BREAK.finditer(prose):
        head = prose[start:m.end(1)]
        tokens = head.split()
        # Strip quotes and brackets from BOTH ends before the terminator, so
        # `"Dr.` inside a quotation is still recognised as an abbreviation.
        # Reuses _CLOSERS/_OPENERS (codepoint-built, not typed characters) --
        # together they cover every quote/bracket this needs to strip, with
        # the straight quote and apostrophe harmlessly duplicated between the
        # two sets.
        last = (
            tokens[-1].strip(_CLOSERS + _OPENERS).rstrip(".!?" + chr(0x2026)).lower()
            if tokens else ""
        )
        if last in _ABBREVIATIONS:
            continue
        out.append(head.strip())
        start = m.end()
    tail = prose[start:].strip()
    if tail:
        out.append(tail)
    return [s for s in out if word_count(s)]


def paragraphs(prose: str) -> list[str]:
    """Runs of text between blank lines, dropping any with no word tokens."""
    blocks, current = [], []
    for line in prose.splitlines():
        if line.strip():
            current.append(line)
        elif current:
            blocks.append("\n".join(current))
            current = []
    if current:
        blocks.append("\n".join(current))
    return [b.strip() for b in blocks if word_count(b)]


def coefficient_of_variation(counts: list[int]) -> float:
    """Population standard deviation over the mean. 0.0 for a degenerate list,
    which is the flattest possible answer and the right one."""
    mean = sum(counts) / len(counts)
    if not mean:
        return 0.0
    return statistics.pstdev(counts) / mean
