"""Link suggestions from names a card mentions (Sprint 037's proving scenario). Pure; never applied.

A card that says its character belongs to "the Silver Guild" of "Port Vell" should let the preview
*suggest* the library's existing group and location of those names. A suggestion is a candidate
list for a human: nothing is linked, no relation is written and no identity is decided, because a
name is never identity. Several entities sharing the name make the suggestion ``ambiguous``.

Text is read in NFC; combining marks belong to their word; a possessive "'s" is also tried
without it. Scripts written without spaces between words are not segmented. A phrase is a run of
one to ``MAX_WORDS`` words, taken exactly as written (so "Saint-Malo",
"St. Ives" and "Order of the Silver Hand" keep their punctuation and inner small words), that
starts with a word whose first character is not lowercase (proper names, and scripts without
case). Words join across a short gap and at most one ``-``, ``.``, ``&``, ``,`` or Unicode hyphen or
dash between them; a phrase followed by ``.`` is also tried with it ("U.S.A."). A
name that starts with a lowercase particle ("von Krieg") is found only from its capitalized part.
Each distinct phrase is looked up through ``DefinitionIndex.find_by_name``'s own normalization, and
one written with a Unicode hyphen or dash also with ``-`` in its place.

The scan is bounded. Fields are read in priority order (the description, personality, scenario
and greetings before lore entries). Past ``MAX_PHRASES`` distinct phrases, or ``MAX_SCANNED``
phrase occurrences in all, the scan stops: the result says so and names the fields that were not
read in full.
"""

from __future__ import annotations

import re
import unicodedata
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass

from grimoire.core.identity import SourceRef

MAX_WORDS = 6
MAX_PHRASES = 20_000
#: phrase occurrences read in all (repeats included), so a long repetitive text stays bounded too
MAX_SCANNED = 500_000
_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+){0,8}")
#: what may stand between two words of one name: a short gap (no line break), at most one of ``-.&,``.
#: Bounded and unambiguous, so matching is linear however long the gap.
_JOIN = re.compile(r"[^\S\n]{0,8}(?:[-.&,\u2010-\u2013\u2212\ufe63\uff0d][^\S\n]{0,8})?")
#: Unicode hyphens and dashes a name may be written with where the library writes ``-``
_DASHES = re.compile("[\u2010-\u2013\u2212\ufe63\uff0d]")
_POSSESSIVE = ("'s", "’s", "'S", "’S")
#: fields read first, by prefix; everything else follows, lore entries last
_FIRST = ("data.description", "setting", "texts.setting", "data.personality", "texts.personality", "premise",
          "data.scenario", "greeting:", "opening:", "texts.")
_LAST = ("data.character_book.", "lorebook.", "lorebook_file.", "data.entries")


@dataclass(frozen=True)
class LinkSuggestion:
    """Existing definitions whose name a card mentions. ``fields`` are where; nothing is applied."""

    phrase: str
    candidates: tuple[SourceRef, ...]
    fields: tuple[str, ...]

    @property
    def ambiguous(self) -> bool:
        return len(self.candidates) > 1


@dataclass(frozen=True)
class Suggestions:
    links: tuple[LinkSuggestion, ...]
    truncated: bool
    #: fields not read in full because the phrase bound was reached
    skipped: tuple[str, ...] = ()


def _words(text: str) -> Iterator[tuple[int, int]]:
    """Word spans; combining marks belong to the word they follow (Devanagari vowel signs, NFD accents)."""
    current: tuple[int, int] | None = None
    for match in _WORD.finditer(text):
        start, end = match.span()
        while end < len(text) and unicodedata.category(text[end])[0] == "M":
            end += 1
        if current is not None and start == current[1]:
            current = (current[0], end)
            continue
        if current is not None:
            yield current
        current = (start, end)
    if current is not None:
        yield current


def _phrases(text: str) -> Iterator[str]:
    """Each phrase of ``text`` in order, lazily (a window of ``MAX_WORDS`` words, never the whole list).

    A phrase whose last word is possessive ("Port Vell's") also yields the name without it.
    """
    window: deque[tuple[int, int]] = deque()

    def starting_at_first() -> Iterator[str]:
        first = window[0]
        if text[first[0]].islower():
            return
        for end in range(len(window)):
            if end and not _JOIN.fullmatch(text, window[end - 1][1], window[end][0]):
                return
            phrase = text[first[0]:window[end][1]]
            yield phrase
            if text.startswith(".", window[end][1]):
                yield phrase + "."  # "U.S.A." ends with its own full stop
            if phrase.endswith(_POSSESSIVE) and len(phrase) > 2:
                yield phrase[:-2]

    for word in _words(text):
        window.append(word)
        if len(window) == MAX_WORDS:
            yield from starting_at_first()
            window.popleft()
    while window:
        yield from starting_at_first()
        window.popleft()


def _rank(field: str) -> tuple[int, int, str]:
    for index, prefix in enumerate(_FIRST):
        if field == prefix or field.startswith(prefix):
            return (0, index, field)
    if field.startswith(_LAST):
        return (2, 0, field)
    return (1, 0, field)


def suggest_links(texts: Mapping[str, str], find: Callable[[str], tuple[SourceRef, ...]], *,
                  exclude: Callable[[SourceRef], bool] = lambda ref: False) -> Suggestions:
    """Suggestions for every phrase of ``texts`` that names an existing definition (``find``).

    ``exclude`` drops candidates (the import's own source). Deterministic: sorted by phrase, then
    candidate.
    """
    where: dict[str, set[str]] = {}
    skipped: list[str] = []
    budget = MAX_SCANNED
    for field in sorted(texts, key=_rank):
        if skipped:
            skipped.append(field)  # past the bound: the remaining fields are not read
            continue
        for phrase in _phrases(unicodedata.normalize("NFC", texts[field])):
            budget -= 1
            if budget < 0 or (phrase not in where and len(where) >= MAX_PHRASES):
                skipped.append(field)
                break
            where.setdefault(phrase, set()).add(field)
    by_entities: dict[tuple[SourceRef, ...], tuple[str, set[str]]] = {}
    for phrase in sorted(where):
        found = find(phrase)
        plain = _DASHES.sub("-", phrase)
        if plain != phrase:  # "Saint\u2010Malo" also names a library's "Saint-Malo"
            found = tuple(sorted(set(found) | set(find(plain)), key=str))
        candidates = tuple(ref for ref in found if not exclude(ref))
        if not candidates:
            continue
        # one suggestion per distinct candidate set: "Port Vell" and "port vell" are one mention
        if candidates in by_entities:
            by_entities[candidates][1].update(where[phrase])
        else:
            by_entities[candidates] = (phrase, set(where[phrase]))
    links = tuple(sorted((LinkSuggestion(phrase, candidates, tuple(sorted(fields)))
                          for candidates, (phrase, fields) in by_entities.items()),
                         key=lambda link: (link.phrase, tuple(map(str, link.candidates)))))
    return Suggestions(links, bool(skipped), tuple(skipped))
