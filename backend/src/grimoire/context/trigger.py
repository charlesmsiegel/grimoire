"""Deterministic lore triggering: whole-word key matching over audience-visible text (031).

Text is NFKC-normalized and casefolded, invisible format characters (Unicode ``Cf``: soft hyphen,
zero-width space and joiners, bidi marks) are dropped, and the rest is split into word runs: maximal
runs of letters, marks, digits and connector punctuation (``L*``, ``M*``, ``N*`` and ``Pc``). A key
matches a whole word sequence wherever it occurs: ``ash`` does not match ``ashen``, ``Black-Eyed``
matches ``black eyed``, ``C-3PO`` matches ``c 3po``, and ``Mara's`` contains the word ``mara``.
Combining marks stay inside their word, so a Devanagari or Arabic word with marks is one word;
compatibility forms fold (fullwidth ``ＦＥＲＲＹ`` is ``ferry``); a soft hyphen inside a word does not
split it; and the dot that casefolding leaves on a Turkish dotted capital I is dropped, so
``İstanbul`` matches ``istanbul``. No model, no stemming, no embeddings: the same texts and keys
always give the same answer.

**Cost:** ``SceneWords.prime`` takes a batch of keys, hashes them into one small table per key
length, and makes one rolling-hash pass over the texts per length: time is the texts' words times
the distinct key lengths asked, plus the keys; memory is the texts' words plus the keys, never a
table of every window.

**Limits:** scripts written without spaces (Chinese, Japanese, Thai) form one word run per stretch
of text, so a key there matches only a whole run; likewise a Korean noun with a particle attached
(``서울에서``) is one word, which the bare noun does not match. Segmentation is a later milestone.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

_DOT_ABOVE = "̇"  # what casefold leaves after the i of a dotted capital I
_ASCII_WORD = re.compile(r"[a-z0-9_]+")
#: the rolling hash: a Mersenne prime modulus and a fixed base, so the answer never depends on a seed
_MODULUS = (1 << 61) - 1
_BASE = 1_000_003


class _WordTable(dict[int, str | None]):
    """A ``str.translate`` table filled on demand: a word character stays, a format character goes,
    anything else becomes a space. Each code point is classified once, then looked up in C."""

    def __missing__(self, point: int) -> str | None:
        char = chr(point)
        category = unicodedata.category(char)
        value = None if category == "Cf" else char if category[0] in "LMN" or category == "Pc" else " "
        self[point] = value
        return value


_TABLE = _WordTable()


def words(text: str) -> tuple[str, ...]:
    """The normalized word sequence of ``text``."""
    if text.isascii():  # the common case: NFKC and casefold are plain lowercasing here
        return tuple(_ASCII_WORD.findall(text.lower()))
    folded = unicodedata.normalize("NFKC", text).casefold().replace("i" + _DOT_ABOVE, "i")
    return tuple(unicodedata.normalize("NFKC", folded).translate(_TABLE).split())


def key_words(key: str) -> tuple[str, ...]:
    """``words`` for a lore key. ``LoreEntry`` computes it once per key and keeps it."""
    return words(key)


class SceneWords:
    """Several audience-visible texts, indexed once for many key lookups.

    Each text is matched on its own, so a two-word key never spans the end of one text and the start
    of the next. Words become ids. ``prime`` answers a batch of keys at once: the keys are hashed
    into one table per length, then each text is rolled over once per length, and every hash hit is
    confirmed against the window itself, so a collision costs a comparison, never a wrong answer.
    Answers are remembered; a key asked outside a batch is primed on its own.
    """

    def __init__(self, texts: Iterable[str]) -> None:
        self._ids: dict[str, int] = {}
        self._texts: list[list[int]] = []
        for text in texts:
            self._texts.append([self._ids.setdefault(word, len(self._ids) + 1) for word in words(text)])
        self._seen: dict[tuple[str, ...], bool] = {}

    @staticmethod
    def _hash(ids: list[int] | tuple[int, ...]) -> int:
        value = 0
        for item in ids:
            value = (value * _BASE + item) % _MODULUS
        return value

    def prime(self, sequences: Iterable[tuple[str, ...]]) -> None:
        """Answer every normalized key in ``sequences`` that is not yet known, in one pass per length."""
        pending: dict[int, dict[int, dict[tuple[int, ...], tuple[str, ...]]]] = {}
        for wanted in sequences:
            if wanted in self._seen:
                continue
            self._seen[wanted] = False
            if not wanted or any(word not in self._ids for word in wanted):
                continue
            ids = tuple(self._ids[word] for word in wanted)
            pending.setdefault(len(ids), {}).setdefault(self._hash(ids), {})[ids] = wanted
        for size, by_hash in pending.items():
            top = pow(_BASE, size - 1, _MODULUS)
            for sequence in self._texts:
                if not by_hash:
                    break
                if len(sequence) < size:
                    continue
                value = self._hash(sequence[:size])
                for start in range(len(sequence) - size + 1):
                    if start:
                        value = ((value - sequence[start - 1] * top) * _BASE + sequence[start + size - 1]) % _MODULUS
                    group = by_hash.get(value)
                    if group is None:
                        continue
                    wanted = group.pop(tuple(sequence[start:start + size]), None)
                    if wanted is not None:
                        self._seen[wanted] = True
                        if not group:
                            del by_hash[value]

    def count(self, sequences: Iterable[tuple[str, ...]]) -> int:
        """How many distinct normalized keys occur (primed first if needed)."""
        distinct = tuple(dict.fromkeys(sequences))
        self.prime(distinct)
        return sum(1 for wanted in distinct if self._seen[wanted])

    def contains(self, key: str) -> bool:
        """Whether ``key``'s words occur contiguously in one of the texts."""
        return self.count((key_words(key),)) == 1

    def hits(self, keys: Iterable[str]) -> int:
        """How many distinct keys occur. Keys that normalize alike (``Ferry``, ``ferry``) count once."""
        return self.count(key_words(key) for key in keys)


__all__ = ["SceneWords", "key_words", "words"]
