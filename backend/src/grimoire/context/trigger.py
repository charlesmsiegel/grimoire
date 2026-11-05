"""Deterministic lore triggering: whole-word key matching over audience-visible text (031).

Text is NFC-normalized, casefolded and split into Unicode word runs (``\\w+``), so a key matches a
whole word sequence wherever it occurs: ``ash`` does not match ``ashen``, ``Black-Eyed`` matches
``black eyed``, ``C-3PO`` matches ``c 3po``, and ``Mara's`` contains the word ``mara``. No model,
no stemming, no embeddings: the same texts and keys always give the same answer.

**Limit:** scripts written without spaces (Chinese, Japanese, Thai) form one word run per stretch
of text, so a key there matches only a whole run. Segmenting them is a later milestone.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

_WORD = re.compile(r"\w+")


def words(text: str) -> tuple[str, ...]:
    """The normalized word sequence of ``text``."""
    return tuple(_WORD.findall(unicodedata.normalize("NFC", text).casefold()))


class SceneWords:
    """Several audience-visible texts, indexed once for many key lookups.

    Each text is matched on its own, so a two-word key never spans the end of one text and the start
    of the next.
    """

    def __init__(self, texts: Iterable[str]) -> None:
        self._texts = tuple(words(text) for text in texts)
        self._positions: list[dict[str, list[int]]] = []
        for sequence in self._texts:
            positions: dict[str, list[int]] = {}
            for position, word in enumerate(sequence):
                positions.setdefault(word, []).append(position)
            self._positions.append(positions)

    def _has(self, wanted: tuple[str, ...]) -> bool:
        if not wanted:
            return False
        size = len(wanted)
        return any(sequence[start:start + size] == wanted
                   for sequence, positions in zip(self._texts, self._positions)
                   for start in positions.get(wanted[0], ()))

    def contains(self, key: str) -> bool:
        """Whether ``key``'s words occur contiguously in one of the texts."""
        return self._has(words(key))

    def hits(self, keys: Iterable[str]) -> int:
        """How many distinct keys occur. Keys that normalize alike (``Ferry``, ``ferry``) count once."""
        return sum(1 for wanted in dict.fromkeys(words(key) for key in keys) if self._has(wanted))


__all__ = ["SceneWords", "words"]
