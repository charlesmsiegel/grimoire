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
_RUN = re.compile(r"\w+")
#: Typographic variants NFKC keeps distinct, folded so ``duke’s`` matches ``duke's``.
_FOLD = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201b": "'", "\u02bc": "'", "\u2032": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2033": '"',
    "\u200b": " ", "\u2060": " ",  # zero-width space / word joiner separate words
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2212": "-",
})
#: Scripts written without spaces between words: no word-boundary test at a marker edge there.
_UNSPACED_SCRIPTS = ("CJK", "IDEOGRAPHIC", "HIRAGANA", "KATAKANA", "THAI", "LAO", "KHMER", "MYANMAR", "TIBETAN")


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


#: Characters a word averages in a script written without spaces, for ``count_words``.
UNSPACED_CHARS_PER_WORD = 2


def count_words(text: str) -> int:
    """About how many words ``text`` has: each spaced run of word characters is one, and a run in a
    script written without spaces counts one per ``UNSPACED_CHARS_PER_WORD`` characters (rounded up),
    since such a run is a whole phrase or sentence, not one word."""
    count = 0
    for run in _RUN.findall(normalize(text)):
        if _unspaced(run[0]):
            count += -(-len(run) // UNSPACED_CHARS_PER_WORD)
        else:
            count += 1
    return count


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


#: A sentence ends at ``.``, ``!``, ``?`` or ``…`` (and any closing quotes after it) before a space.
_SENTENCE_END = re.compile(r"""[.!?\u2026]+["'\u00bb\u300d\u300f)\]]*(?=\s|$)""")
#: Quoted speech: a double quote or guillemet/corner bracket anywhere, or a single quote that opens
#: a word (``'They``), so an apostrophe (``duke's``, ``guards'``) is not a quotation.
_QUOTATION = re.compile(r"""["\u00ab\u00bb\u300c\u300d\u300e\u300f]|(?<!\w)'(?=\w)""")
_TOKEN = re.compile(r"\w+")
#: Verbs that report speech, every form spelled out so the verdict never depends on a stemmer.
#: Deliberately narrow: forms that are as often non-speech words (``calls``, ``cries``, ``adds``,
#: ``barks``, ``admitted``, ``gossip``, ``promise``, ``warning``, ``demands``, ``remarks``) are left
#: out, and quoted speech is caught by the quotes.
SPEECH_VERBS = frozenset("""
    say says said saying tell tells told telling ask asks asked asking
    reply replies replied replying answer answers answered answering
    respond responds responded responding retort retorts retorted retorting
    speak speaks spoke spoken speaking talk talks talked talking
    mutter mutters muttered muttering murmur murmurs murmured murmuring
    whisper whispers whispered whispering mumble mumbles mumbled mumbling
    grumble grumbles grumbled grumbling shout shouts shouted shouting yell yells yelled yelling
    growl growls growled growling snarl snarls snarled snarling hiss hisses hissed hissing
    drawl drawls drawled drawling quip quips quipped quipping
    exclaim exclaims exclaimed exclaiming declare declares declared declaring
    announce announces announced announcing proclaim proclaims proclaimed proclaiming
    explain explains explained explaining insist insists insisted insisting
    remarked remarking mentioned mentioning confess confesses confessed confessing
    warn warns warned inform informs informing demanded demanding
    interject interjects interjected interjecting gossiped gossiping
    sing sings sang sung singing chanted chanting recite recites recited reciting
""".split())


def sentences(text: str) -> list[str]:
    """``text`` normalised and cut into sentences (a quoted sentence ends with its quote)."""
    normalized = normalize(text)
    parts, start = [], 0
    for end in _SENTENCE_END.finditer(normalized):
        parts.append(normalized[start:end.end()].strip())
        start = end.end()
    parts.append(normalized[start:].strip())
    return [part for part in parts if part]


def speech_evidence(sentence: str) -> str | None:
    """``"quotation"`` or ``"speech_verb"`` when ``sentence`` reports speech, else ``None``."""
    normalized = normalize(sentence)
    if _QUOTATION.search(normalized):
        return "quotation"
    if any(token in SPEECH_VERBS for token in _TOKEN.findall(normalized)):
        return "speech_verb"
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
