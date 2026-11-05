"""Deterministic lore triggering: whole-word key matching over audience-visible text (031).

Text is NFC-normalized and casefolded, then split into word runs: maximal runs of letters, marks,
digits and connector punctuation (Unicode categories ``L*``, ``M*``, ``N*`` and ``Pc``). A key
matches a whole word sequence wherever it occurs: ``ash`` does not match ``ashen``, ``Black-Eyed``
matches ``black eyed``, ``C-3PO`` matches ``c 3po``, and ``Mara's`` contains the word ``mara``.
Combining marks stay inside their word, so a Devanagari or Arabic word with marks is one word, and
the dot that casefolding leaves on a Turkish dotted capital I is dropped, so ``İstanbul`` matches
``istanbul``. No model, no stemming, no embeddings: the same texts and keys always give the same
answer.

**Limit:** scripts written without spaces (Chinese, Japanese, Thai) form one word run per stretch
of text, so a key there matches only a whole run. Segmenting them is a later milestone.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from collections.abc import Callable, Iterable

_DOT_ABOVE = "\u0307"  # what casefold leaves after the i of a dotted capital I
_PATTERN: re.Pattern[str] | None = None
_MAX_CODE_POINT = 0x10FFFF


def _ranges(wanted: Callable[[str], bool]) -> str:
    """A regex class body for every code point ``wanted`` accepts, as escaped ranges."""
    found: list[str] = []
    start = None
    for point in range(_MAX_CODE_POINT + 2):
        inside = point <= _MAX_CODE_POINT and wanted(chr(point))
        if inside and start is None:
            start = point
        elif not inside and start is not None:
            found.append(f"\\U{start:08x}-\\U{point - 1:08x}")
            start = None
    return "".join(found)


def _pattern() -> re.Pattern[str]:
    """``\\w`` (letters, digits, ``_``) plus every mark and connector, built once on first use."""
    global _PATTERN
    if _PATTERN is None:
        extra = _ranges(lambda char: unicodedata.category(char)[0] == "M" or unicodedata.category(char) == "Pc")
        _PATTERN = re.compile(f"[\\w{extra}]+")
    return _PATTERN


def words(text: str) -> tuple[str, ...]:
    """The normalized word sequence of ``text``."""
    folded = unicodedata.normalize("NFC", text).casefold().replace("i" + _DOT_ABOVE, "i")
    return tuple(_pattern().findall(unicodedata.normalize("NFC", folded)))


@functools.lru_cache(maxsize=65536)
def _key_words(key: str) -> tuple[str, ...]:
    """``words`` for a lore key, remembered: one lorebook's keys are asked again for every actor."""
    return words(key)


class SceneWords:
    """Several audience-visible texts, indexed once for many key lookups.

    Each text is matched on its own, so a two-word key never spans the end of one text and the start
    of the next. A lookup checks first that every word of the key occurs in a text, then scans only
    the occurrences of the key's rarest word there, and is remembered for the next entry asking it.
    """

    def __init__(self, texts: Iterable[str]) -> None:
        self._texts = tuple(words(text) for text in texts)
        self._positions: list[dict[str, list[int]]] = []
        for sequence in self._texts:
            positions: dict[str, list[int]] = {}
            for position, word in enumerate(sequence):
                positions.setdefault(word, []).append(position)
            self._positions.append(positions)
        self._seen: dict[tuple[str, ...], bool] = {}

    def _scan(self, wanted: tuple[str, ...]) -> bool:
        size = len(wanted)
        for sequence, positions in zip(self._texts, self._positions):
            if any(word not in positions for word in wanted):
                continue
            anchor = min(range(size), key=lambda k: (len(positions[wanted[k]]), k))
            for at in positions[wanted[anchor]]:
                start = at - anchor
                if start >= 0 and sequence[start:start + size] == wanted:
                    return True
        return False

    def _has(self, wanted: tuple[str, ...]) -> bool:
        if not wanted:
            return False
        found = self._seen.get(wanted)
        if found is None:
            found = self._seen[wanted] = self._scan(wanted)
        return found

    def contains(self, key: str) -> bool:
        """Whether ``key``'s words occur contiguously in one of the texts."""
        return self._has(_key_words(key))

    def hits(self, keys: Iterable[str]) -> int:
        """How many distinct keys occur. Keys that normalize alike (``Ferry``, ``ferry``) count once."""
        return sum(1 for wanted in dict.fromkeys(_key_words(key) for key in keys) if self._has(wanted))


__all__ = ["SceneWords", "words"]
