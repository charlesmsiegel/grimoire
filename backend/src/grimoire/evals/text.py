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


def _fold_keeping_case(text: str) -> str:
    """``normalize`` without the casefold, so a sentence cut can see a capital letter."""
    text = unicodedata.normalize("NFKC", text).translate(_FOLD)
    text = "".join(char for char in text if unicodedata.category(char) != "Cf")
    return _SPACE.sub(" ", text).strip()


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
#: A quotation is a matched pair: double quotes, guillemets, corner brackets, or single quotes
#: whose opening quote starts a word and whose closing quote ends one. So an apostrophe
#: (``duke's``, ``guards'``) or an elision (``'til dawn``) is not a quotation.
_QUOTATION = re.compile(
    r"""".+?"|\u00ab.+?\u00bb|\u300c.+?\u300d|\u300e.+?\u300f|(?<!\w)'(?=\w).*?(?<=\S)'(?!\w)""")
#: Words, keeping an inner apostrophe or hyphen: ``oda's``, ``doesn't``, ``npc-oda`` are one token.
_TOKEN = re.compile(r"\w+(?:['-]\w+)*")
#: Verb forms that report speech when they follow a named speaker, every form spelled out so the
#: verdict never depends on a stemmer. Heuristic and deliberately narrow: forms as often used as
#: nouns or for non-speech (``talk``, ``answer``, ``reply``, ``call``, ``cry``, ``sing``,
#: ``joke``, ``boast``, ``promise``) are left out, and ``tell`` counts only through ``_tells``.
SPEECH_VERBS = frozenset("""
    say says said saying ask asks asked asking
    respond responds responded responding retort retorts retorted retorting
    speak speaks spoke spoken speaking
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
    chanted chanting recite recites recited reciting
    confide confides confided confiding blurt blurts blurted blurting
    admit admits admitted admitting reveal reveals revealed revealing add adds added adding
    breathe breathes breathed breathing croon croons crooned crooning
    beg begs begged begging jokes joked joking boasts boasted boasting
    plead pleads pleaded pled pleading threaten threatens threatened threatening
    promises promised promising
""".split())
#: Two-word speech verbs, as token pairs: ``answer`` and ``reply`` are speech only before ``that``
#: ("answers the door", "replies with a shrug" are not), ``call`` only with ``out``.
SPEECH_PHRASES = frozenset({
    ("lets", "slip"), ("let", "slip"), ("letting", "slip"),
    ("answers", "that"), ("answered", "that"), ("replies", "that"), ("replied", "that"),
    ("calls", "out"), ("called", "out"), ("calling", "out"),
})
#: ``tell`` is speech only before who is told or a told thing ("tells Wren", "tells a story"),
#: and not as perceiving ("can tell", "will tell") or in the passive ("is told").
TELL_FORMS = frozenset({"tell", "tells", "told", "telling"})
_TOLD = frozenset({"him", "her", "them", "me", "us", "you", "everyone", "that"})
_TALES = frozenset({"story", "tale", "joke", "lie", "truth"})
_NOT_TELLING = frozenset({"can", "could", "will", "would", "to", "be", "am", "is", "are", "was",
                          "were", "been", "being"})
#: Words that deny the speech they stand next to ("never says", "says nothing").
NEGATIONS = frozenset({"not", "never", "nothing", "cannot"})


def quotation_spans(text: str) -> list[tuple[int, int]]:
    """``(start, end)`` of each quotation in ``text`` (already normalised)."""
    return [match.span() for match in _QUOTATION.finditer(text)]


def sentences(text: str) -> list[str]:
    """``text`` normalised and cut into sentences.

    A sentence ends at ``.``, ``!``, ``?`` or ``…`` before a space, never inside a quotation, so a
    quoted speech of several sentences stays in one. A quotation's own closing punctuation does
    not end the sentence when a lower-case word follows (``"Encore!" cry the voices``) or a speech
    verb or a word and a speech verb does (``"…!" Oda hisses``, ``"…?" asks Wren``,
    ``"…," Oda tells Wren``): what follows belongs to the quotation.
    """
    kept = _fold_keeping_case(text)
    spans = quotation_spans(kept)
    parts, start = [], 0
    for end in _SENTENCE_END.finditer(kept):
        cut = end.end()
        if any(left < end.start() and cut < right for left, right in spans):
            continue  # inside a quotation
        if any(right == cut for _left, right in spans) and _continues(kept[cut:]):
            continue
        parts.append(kept[start:cut])
        start = cut
    parts.append(kept[start:])
    return [normalize(part) for part in parts if part.strip()]


def _continues(rest: str) -> bool:
    """Whether ``rest``, after a quotation's closing punctuation, still belongs to its sentence."""
    if rest.lstrip()[:1].islower():
        return True
    after = _TOKEN.findall(normalize(rest))[:3]
    return any(speech_verb_at(after, index) or after[index] in TELL_FORMS
               for index in range(min(2, len(after))))


def is_negation(token: str) -> bool:
    return token in NEGATIONS or token.endswith("n't")


def speech_verb_at(words: list[str], index: int, names: frozenset[str] = frozenset()) -> int:
    """How many tokens of a speech verb start at ``words[index]`` (0 when none does).

    ``names`` are the tokens that name actors, so ``tells Wren`` counts as telling.
    """
    if tuple(words[index:index + 2]) in SPEECH_PHRASES:
        return 2
    word = words[index]
    if word in SPEECH_VERBS:
        return 1
    return 1 if word in TELL_FORMS and _tells(words, index, names) else 0


def _tells(words: list[str], index: int, names: frozenset[str]) -> bool:
    """Whether the ``tell`` at ``index`` reports speech: who is told or a told tale follows."""
    if index and words[index - 1] in _NOT_TELLING:
        return False
    following = words[index + 1:index + 4]
    if not following:
        return False
    if following[0] in names or following[0] in _TOLD:
        return True
    # "tells a story", "tells the bawdy tale": one adjective may stand before the tale
    return following[0] in ("a", "the") and any(word in _TALES for word in following[1:3])


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
