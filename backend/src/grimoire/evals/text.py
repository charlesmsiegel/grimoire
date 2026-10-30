"""Deterministic text normalisation, marker matching and voice profiles."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from functools import lru_cache

_SPACE = re.compile(r"\s+")
_WORD = re.compile(r"\w")
#: Typographic variants NFKC keeps distinct, folded so ``duke’s`` matches ``duke's``.
_FOLD = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201b": "'", "\u02bc": "'", "\u2032": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2033": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2212": "-",
})
#: Scripts written without spaces between words: no word-boundary test at a marker edge there.
_UNSPACED_SCRIPTS = ("CJK", "HIRAGANA", "KATAKANA", "THAI", "LAO", "KHMER", "MYANMAR", "TIBETAN")


def normalize(text: str) -> str:
    """NFKC, fold quote/dash variants, drop format characters, casefold, NFKC, collapse spaces."""
    text = unicodedata.normalize("NFKC", text).translate(_FOLD)
    text = "".join(char for char in text if unicodedata.category(char) != "Cf")
    folded = unicodedata.normalize("NFKC", text.casefold())
    return _SPACE.sub(" ", folded).strip()


def has_words(text: str) -> bool:
    """Whether ``text`` says anything: blank or punctuation-only text does not."""
    return _WORD.search(normalize(text)) is not None


def _unspaced(char: str) -> bool:
    return unicodedata.name(char, "").startswith(_UNSPACED_SCRIPTS)


@lru_cache(maxsize=1024)
def _marker_pattern(marker: str) -> re.Pattern[str]:
    normalized = normalize(marker)
    body = r"\s+".join(re.escape(token) for token in normalized.split(" "))
    start = "" if _unspaced(normalized[0]) else r"(?<!\w)"
    end = "" if _unspaced(normalized[-1]) else r"(?!\w)"
    return re.compile(start + body + end)


def contains_marker(text: str, marker: str) -> bool:
    """Whole-phrase, case- and width-insensitive match; ``duke`` does not match ``dukedom``."""
    return _marker_pattern(marker).search(normalize(text)) is not None


def first_marker(text: str, markers: tuple[str, ...]) -> int | None:
    """Index of the first marker present in ``text``, if any."""
    for index, marker in enumerate(markers):
        if contains_marker(text, marker):
            return index
    return None


def trigram_counts(text: str) -> Counter[str]:
    padded = f" {normalize(text)} "
    return Counter(padded[index:index + 3] for index in range(len(padded) - 2))


def cosine(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(count * right[gram] for gram, count in left.items())
    norm = math.sqrt(sum(v * v for v in left.values())) * math.sqrt(sum(v * v for v in right.values()))
    return dot / norm if norm else 0.0


def longest_shared_run(left: str, right: str) -> int:
    """Length of the longest contiguous substring the normalised texts share."""
    a, b = normalize(left), normalize(right)
    return SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b)).size
