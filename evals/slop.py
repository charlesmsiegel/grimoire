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
    # Defensive: every span _SENTENCE_BREAK produces ends in a live terminator
    # character, which is never whitespace, so word_count(s) is currently
    # always truthy here. Kept against a future change to the splitter
    # grammar that could yield a whitespace-only span.
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


# ------------------------------------------------------------------- the lists
#
# Each entry pairs a `source` -- a literal substring that must still appear in
# the rendered template, which is all the drift guard checks -- with the
# `pattern` the detector actually applies. The split is what lets a beat group
# match inflections the template never spells out without the guard failing on
# its own list.

# The template itself uses only STRAIGHT apostrophes (U+0027) — verified: it
# contains eight of them and no U+2019. Every `source` below must therefore be
# written with a straight apostrophe or the drift guard fails on its own list.
# The `pattern` side admits both, because a model may well type the curly one.
# Built via chr() rather than a typed curly character -- see module docstring
# and _CLOSERS/_OPENERS above for why: a literal paste is how ambiguous quote
# characters silently swap for their look-alikes with no visible diff.
_AP = "['" + chr(0x2019) + "]"


@dataclass(frozen=True)
class Entry:
    source: str
    pattern: re.Pattern[str]


def _lit(source: str, pattern: str | None = None) -> Entry:
    return Entry(source, re.compile(pattern or (r"\b" + re.escape(source) + r"\b"),
                                    re.IGNORECASE))


# Template: "Phrases -- never use." Only the unconditional bans.
LITERAL_PHRASES: tuple[Entry, ...] = (
    _lit("barely above a whisper"),
    _lit("barely audible"),
    _lit("the air thick with"),
    _lit("a smile playing on", r"\ba smile playing on\b"),
    _lit("eyes never leaving"),
    _lit("couldn't help but", r"\bcouldn" + _AP + r"t help but\b"),
    _lit("couldn't shake the feeling",
         r"\bcouldn" + _AP + r"t shake the feeling\b"),
    # `\w+ ` is OPTIONAL on both tails: the template's own wording is "heart
    # pounding or hammering in a chest or against ribs", so requiring an
    # intervening possessive would miss the bare form the template spells out.
    _lit("heart pounding or hammering",
         r"\bheart (?:pounding|hammering)\b[^.!?…]{0,40}?"
         r"(?:in (?:\w+ )?chest|against (?:\w+ )?ribs)"),
    _lit("casting long shadows"),
    _lit("something else entirely"),
    _lit("spreading across her face",
         r"\bspreading across \w+ face\b"),
    _lit("one last time"),
    _lit("a testament to"),
    _lit("a tapestry, symphony, or dance of anything",
         r"\ba (?:tapestry|symphony|dance) of\b"),
    _lit("ministrations"),
    _lit("the ghost of a smile"),
    _lit("shivers down the spine",
         r"\bshivers? down \w+ spine\b"),
    _lit("knuckles whitening", r"\bknuckles whiten(?:ing|ed)?\b"),
    _lit("the smell of ozone"),
    _lit("an unreadable expression"),
    _lit("lips swollen with kisses"),
    _lit("delve"),
    _lit("nestled"),
    _lit("moreover"),
    _lit("furthermore"),
    _lit("indeed"),
    _lit("albeit"),
)

# Qualifier-dependent: the template bans the reflexive use, not the phrase.
# Carried as drift sources; never matched. Listed in the spec's ungraded table.
JUDGMENT_ONLY: tuple[Entry, ...] = (
    _lit("said in a low voice as a reflex tag"),
    _lit("a deep breath as filler"),
    _lit("foreheads pressed together as the default tender gesture"),
)

# Template: "Never reach for the stock AI pool". Word-bounded, so `Aria` does
# not match inside a longer name.
STOCK_NAMES: tuple[Entry, ...] = tuple(
    _lit(n) for n in ("Elara", "Lyra", "Kael", "Aria", "Seraphina", "Selene",
                      "Thorne", "Voss", "Vance", "Blackwood", "Ashford"))

# Template: "Beat words -- ration." A cap, not a ban: these are ordinary words.
# `source` is the template's own spelling; `pattern` carries the inflections.
BEAT_WORDS: tuple[Entry, ...] = (
    _lit("Flickered", r"\bflicker(?:s|ed|ing)?\b"),
    _lit("leaned", r"\blean(?:s|ed|ing)?\b"),
    _lit("murmured", r"\bmurmur(?:s|ed|ing)?\b"),
    _lit("muttered", r"\bmutter(?:s|ed|ing)?\b"),
    _lit("nodded", r"\bnod(?:s|ded|ding)?\b"),
    _lit("gaze", r"\bgaz(?:e|es|ed|ing)\b"),
    _lit("grinned", r"\bgrin(?:s|ned|ning)?\b"),
    _lit("gestured", r"\bgestur(?:e|es|ed|ing)\b"),
    _lit("glinted", r"\bglint(?:s|ed|ing)?\b"),
    _lit("hesitated", r"\bhesitat(?:e|es|ed|ing)\b"),
    _lit("whispered", r"\bwhisper(?:s|ed|ing)?\b"),
    _lit("blinked", r"\bblink(?:s|ed|ing)?\b"),
    _lit("hummed", r"\bhum(?:s|med|ming)?\b"),
    _lit("smirked", r"\bsmirk(?:s|ed|ing)?\b"),
    _lit("faintly", r"\bfaintly\b"),
)

# Template: 'Constructions -- never use. "Not X, but Y" in every disguise'.
# The spans use a class that EXCLUDES sentence terminators: a length bound
# alone does not stop a match running across two short sentences, which is how
# a construction detector starts reporting contrasts nobody wrote. This is a
# FLOOR on the family, not a decision procedure for it -- "every disguise" is
# not implementable, and the hypothesis row claims only these four forms.
NOT_X_BUT_Y: tuple[Entry, ...] = (
    Entry('"Not X, but Y" in every disguise',
          re.compile(r"\bnot\s+[^.!?…]{1,60}?,\s*but\s+", re.IGNORECASE)),
    Entry("it wasn't just X — it was Y",
          re.compile(r"\b(?:it|he|she|they)\s+(?:wasn|weren)" + _AP +
                     r"t\s+just\s+[^.!?…]{1,60}?["
                     + chr(0x2014) + chr(0x2013) + r"]\s*"
                     r"(?:it|he|she|they)\s+(?:was|were)\b", re.IGNORECASE)),
    Entry("she didn't X; she Y'd",
          re.compile(r"\b(?:he|she|they|it)\s+didn" + _AP +
                     r"t\s+[^.!?…;]{1,60}?;\s*(?:he|she|they|it)\s+\w",
                     re.IGNORECASE)),
    Entry("no longer X; now Y",
          re.compile(r"\bno longer\s+[^.!?…;]{1,60}?;\s*now\b",
                     re.IGNORECASE)),
)

# The rhythm checks grade instructions too, so they carry drift sources of
# their own -- otherwise the template could lose the rule while the graders
# went on scoring replies against it.
RHYTHM_SOURCES: tuple[Entry, ...] = (
    _lit("Vary sentence length and paragraph shape"),
    _lit("if the last paragraph used one, the next doesn't"),
)

ALL_ENTRIES: tuple[Entry, ...] = (
    LITERAL_PHRASES + JUDGMENT_ONLY + STOCK_NAMES + BEAT_WORDS
    + NOT_X_BUT_Y + RHYTHM_SOURCES)


def _flat(text: str) -> str:
    """Collapse all whitespace to single spaces.

    `templates/` is hard-wrapped at ~72 columns, so a multi-word source like
    "spreading across her face" is split by a newline in the render and would
    never match verbatim. Six of the sources above span a wrap point. Both
    sides are flattened before comparison, which also makes the guard immune to
    a pure re-wrap of the template — an edit that changes no instruction.
    """
    return " ".join(text.split())


def missing_sources(rendered: str) -> list[str]:
    """Entries whose literal source has left the template. The one-way guard."""
    flat = _flat(rendered)
    return [e.source for e in ALL_ENTRIES if _flat(e.source) not in flat]
