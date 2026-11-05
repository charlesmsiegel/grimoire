"""Deterministic lore triggering: whole-word key matching over audience-visible text (031).

Text is NFKC-normalized and casefolded, then split into word runs: maximal runs of letters, marks,
digits and connector punctuation (Unicode categories ``L*``, ``M*``, ``N*`` and ``Pc``). A key
matches a whole word sequence wherever it occurs: ``ash`` does not match ``ashen``, ``Black-Eyed``
matches ``black eyed``, ``C-3PO`` matches ``c 3po``, and ``Mara's`` contains the word ``mara``.
Combining marks stay inside their word, so a Devanagari or Arabic word with marks is one word;
compatibility forms fold (fullwidth ``ＦＥＲＲＹ`` is ``ferry``); and the dot that casefolding leaves
on a Turkish dotted capital I is dropped, so ``İstanbul`` matches ``istanbul``. No model, no
stemming, no embeddings: the same texts and keys always give the same answer.

**Cost:** a ``SceneWords`` hashes every window of each key length in use once (a rolling hash
over word ids), so a lookup costs the key's own length, whatever the texts or the other keys hold.

**Limits:** scripts written without spaces (Chinese, Japanese, Thai) form one word run per stretch
of text, so a key there matches only a whole run; likewise a Korean noun with a particle attached
(``서울에서``) is one word, which the bare noun does not match. Segmentation is a later milestone.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from collections.abc import Callable, Iterable

_DOT_ABOVE = "̇"  # what casefold leaves after the i of a dotted capital I
_MAX_CODE_POINT = 0x10FFFF
_ASCII_WORD = re.compile(r"[a-z0-9_]+")
_PATTERN: re.Pattern[str] | None = None
#: the rolling hash: a Mersenne prime modulus and a fixed base, so the answer never depends on a seed
_MODULUS = (1 << 61) - 1
_BASE = 1_000_003


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
    if text.isascii():  # the common case: NFKC and casefold are plain lowercasing here
        return tuple(_ASCII_WORD.findall(text.lower()))
    folded = unicodedata.normalize("NFKC", text).casefold().replace("i" + _DOT_ABOVE, "i")
    return tuple(_pattern().findall(unicodedata.normalize("NFKC", folded)))


@functools.lru_cache(maxsize=65536)
def key_words(key: str) -> tuple[str, ...]:
    """``words`` for a lore key, remembered: one lorebook's keys are asked again for every actor."""
    return words(key)


class SceneWords:
    """Several audience-visible texts, indexed once for many key lookups.

    Each text is matched on its own, so a two-word key never spans the end of one text and the start
    of the next. Words become ids; for each key length asked, every window of that length in every
    text is hashed once, and a lookup confirms its hash against the window it names, so a hash
    collision can only cost a scan, never a wrong answer.
    """

    def __init__(self, texts: Iterable[str]) -> None:
        self._ids: dict[str, int] = {}
        self._texts: list[list[int]] = []
        for text in texts:
            self._texts.append([self._ids.setdefault(word, len(self._ids) + 1) for word in words(text)])
        self._windows: dict[int, dict[int, tuple[int, int]]] = {}
        self._seen: dict[tuple[str, ...], bool] = {}

    @staticmethod
    def _hash(ids: list[int] | tuple[int, ...]) -> int:
        value = 0
        for item in ids:
            value = (value * _BASE + item) % _MODULUS
        return value

    def _table(self, size: int) -> dict[int, tuple[int, int]]:
        """Every window of ``size`` words: its hash -> where it first occurs (text, start)."""
        table = self._windows.get(size)
        if table is None:
            table = self._windows[size] = {}
            top = pow(_BASE, size - 1, _MODULUS)
            for number, sequence in enumerate(self._texts):
                if len(sequence) < size:
                    continue
                value = self._hash(sequence[:size])
                table.setdefault(value, (number, 0))
                for start in range(1, len(sequence) - size + 1):
                    value = ((value - sequence[start - 1] * top) * _BASE + sequence[start + size - 1]) % _MODULUS
                    table.setdefault(value, (number, start))
        return table

    def _scan(self, wanted: tuple[int, ...]) -> bool:
        """The exact search, used only when two different windows share a hash."""
        size = len(wanted)
        return any(tuple(sequence[start:start + size]) == wanted
                   for sequence in self._texts for start in range(len(sequence) - size + 1))

    def _lookup(self, wanted: tuple[str, ...]) -> bool:
        if not wanted or any(word not in self._ids for word in wanted):
            return False
        ids = tuple(self._ids[word] for word in wanted)
        place = self._table(len(ids)).get(self._hash(ids))
        if place is None:
            return False
        number, start = place
        if tuple(self._texts[number][start:start + len(ids)]) == ids:
            return True
        return self._scan(ids)

    def _has(self, wanted: tuple[str, ...]) -> bool:
        found = self._seen.get(wanted)
        if found is None:
            found = self._seen[wanted] = self._lookup(wanted)
        return found

    def contains(self, key: str) -> bool:
        """Whether ``key``'s words occur contiguously in one of the texts."""
        return self._has(key_words(key))

    def hits(self, keys: Iterable[str]) -> int:
        """How many distinct keys occur. Keys that normalize alike (``Ferry``, ``ferry``) count once."""
        return sum(1 for wanted in dict.fromkeys(key_words(key) for key in keys) if self._has(wanted))


__all__ = ["SceneWords", "key_words", "words"]
