"""Which NPC a ``/3`` director line gives speech to (pure, heuristic; no I/O).

The director may describe NPCs but not speak for them: only an NPC's own call may produce its
words. ``voiced`` reads each narration sentence for an NPC that the sentence makes speak, by a
speech verb that is the NPC's own or by a quotation the sentence hands to it. It is an eval
check, not a boundary: it reads short English narration word by word, and the misses it accepts
are listed under Limitations in ``docs/evals/STORY_QUALITY.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .format import Actor, Scenario
from .text import SPEECH_VERBS, is_negation, normalize, quotation_spans, speech_verb_at

#: How many words after an NPC's name its speech verb may stand. An object's words count too
#: ("Oda leans over the counter toward Wren and whispers" is eight).
SPEECH_WINDOW = 8
#: Stands for a quotation among the words, so quoted text never names a speaker or a verb.
QUOTE = "<q>"
#: Words (keeping an inner apostrophe or hyphen), and the punctuation that shapes a clause.
_PIECE = re.compile(r"\w+(?:['-]\w+)*|[,;:]|-+")
#: A word after one of these is a noun ("hears a shout", "finds the answer"), not a verb.
_DETERMINERS = frozenset({"a", "an", "the", "his", "her", "their", "its", "my", "your", "our",
                          "no", "some", "any", "this", "that", "each", "every"})
#: After one of these a name is a common noun ("her quill", "the quill"), not an actor.
_NOUN_MARKERS = _DETERMINERS - {"this", "that"}
#: After one of these a name is an object ("turns to Wren and says"): it neither speaks nor takes
#: the verb from the subject.
_PREPOSITIONS = frozenset({"to", "toward", "towards", "at", "with", "beside", "near", "for", "from",
                           "past", "behind", "about", "of", "by", "against", "around", "among",
                           "between", "beyond", "without", "into", "upon", "over"})
#: Words that open another clause ("Oda listens as the crowd murmurs"): a verb after them is not
#: the earlier name's, unless the clause is a bracketed aside ("Oda, as ever, mutters").
_CLAUSE_BREAKS = frozenset({"as", "while", "when", "whenever", "who", "whom", "whose", "which",
                            "where", "because", "though", "although", "if", "unless", "whereas"})
#: After one of these a name starts its own clause ("Oda nods and Wren says"), unless it joins an
#: object ("asks Oda and Hask").
_CONJUNCTIONS = frozenset({"and", "then", "but", "or", "nor", "yet"})
_PUNCTUATION = frozenset({",", ";", ":", "-", QUOTE})
#: A speech verb's search never crosses these: past them is another clause or the speech itself.
_STOPS = frozenset({";", ":", QUOTE})
#: Punctuation that hands the quotation after it to the subject before it (``Oda leans in: "…"``).
_LEADS = frozenset({":", ";", "-"})
#: Speech verbs that, followed by ``at``, are often a sound and not words ("growls at nobody"):
#: they count only when ``that`` or the speech itself follows.
_DIRECTED = frozenset({"at"})
#: Things that carry words without anyone speaking them: a quotation next to them is their text
#: ("taps a sign reading, \"No credit.\"", "strikes up a new tune, \"The Ballad…\"").
_TEXT_NOUNS = frozenset({"sign", "signs", "board", "notice", "plaque", "placard", "poster",
                         "banner", "inscription", "label", "slate", "page", "letter", "note",
                         "scroll", "card", "menu", "tune", "song", "ballad", "melody", "title"})
#: Before ``voice`` these make it a crowd's, not the previous subject's.
_NUMBER_WORDS = frozenset({"two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
                           "dozen", "dozens", "score", "hundred", "hundreds", "many", "several",
                           "few"})
#: Words of a multi-word name that never identify the actor on their own.
_NAME_FILLER = frozenset({"a", "an", "the", "of", "de", "la", "le", "van", "von"})


@dataclass(frozen=True, slots=True)
class _Mention:
    """An actor name in a sentence: the actors it may name, its last token, and its role."""

    ids: frozenset[str]
    end: int
    subject: bool


@dataclass(frozen=True, slots=True)
class _Names:
    npcs: frozenset[str]
    by_actor: tuple[tuple[str, frozenset[str]], ...]
    tokens: frozenset[str]

    def ids(self, word: str) -> frozenset[str]:
        return frozenset(actor_id for actor_id, tokens in self.by_actor if word in tokens)


def name_tokens(actor: Actor) -> frozenset[str]:
    """The tokens that name ``actor``: each word of its name, and its id."""
    words = _words(actor.name)
    names = {word for word in words if word not in _NAME_FILLER} or set(words)
    return frozenset(names | set(_words(actor.actor_id)))


def _words(text: str) -> list[str]:
    """The words of ``text``, without punctuation."""
    return [piece for piece in _PIECE.findall(normalize(text)) if piece not in _PUNCTUATION
            and not piece.startswith("-")]


def _pieces(text: str) -> list[str]:
    """The words and clause punctuation of ``text``, each quotation one ``QUOTE``."""
    normalized = normalize(text)
    found: list[str] = []
    last = 0
    for left, right in quotation_spans(normalized):
        found += _PIECE.findall(normalized[last:left])
        found.append(QUOTE)
        last = right
    found += _PIECE.findall(normalized[last:])
    return ["-" if piece.startswith("-") else piece for piece in found]


def _mentions(words: list[str], names: _Names) -> dict[int, _Mention]:
    """Actor names in ``words``, by first token, with their role in the clause.

    A name right after a determiner is a common noun ("her quill") and no mention at all. A name is
    an object right after a preposition ("to Wren"), and right after any other word once its clause
    already has an actor subject ("Oda pours Wren a drink"); otherwise it is a subject. After ``and`` a
    name shares the role of the name just before ("Oda and Hask", "asks Oda and Hask"). A clause
    starts again at punctuation, a quotation, a clause break and ``that`` ("Wren whispers that Oda
    keeps the ledger": Oda is that clause's subject).
    """
    found: dict[int, _Mention] = {}
    by_end: dict[int, int] = {}
    has_subject = False
    for index, word in enumerate(words):
        if word in _PUNCTUATION or word in _CLAUSE_BREAKS or word == "that":
            has_subject = False
            continue
        ids = names.ids(word)
        previous = words[index - 1] if index else ""
        if not ids or previous in _NOUN_MARKERS:
            continue
        if index - 1 in by_end and found[by_end[index - 1]].ids & ids:
            start = by_end.pop(index - 1)  # the next word of the same name ("Oda Venn")
            found[start] = _Mention(found[start].ids, index, found[start].subject)
            by_end[index] = start
            continue
        if previous in _PREPOSITIONS:
            subject = False
        elif previous in _CONJUNCTIONS and index - 2 in by_end:
            subject = found[by_end[index - 2]].subject
        else:
            subject = not (previous and previous not in _CONJUNCTIONS and has_subject)
        found[index] = _Mention(ids, index, subject)
        by_end[index] = index
        has_subject = has_subject or subject
    return found


def _speaks(words: list[str], at: int, mentions: dict[int, _Mention], names: _Names) -> bool:
    """Whether a speech verb after the subject name at ``at`` is that subject's.

    The search runs ``SPEECH_WINDOW`` words and stops at ``;``, ``:``, a quotation, an unbracketed
    clause break, a negation and another subject (unless joined by ``and``: "Oda and Hask
    whisper"). An aside bracketed by commas right after the name is skipped
    ("Oda, who never forgets a debt, mutters"). A verb right after an object, or after an object
    and ``to``, is the object's ("Oda waits for Wren to say something").
    """
    name = mentions[at]
    index, counted = name.end + 1, 0
    while index < len(words) and counted < SPEECH_WINDOW:
        word = words[index]
        if word in _STOPS or word in _CLAUSE_BREAKS or is_negation(word):
            return False
        aside = index + 1 < len(words) and words[index + 1] in _CLAUSE_BREAKS
        if word == "," and index == name.end + 1 and aside:
            close = _closing_comma(words, index + 1)
            if close is None:
                return False
            index = close + 1
            continue
        if word in (",", "-"):
            index += 1
            continue
        mention = mentions.get(index)
        if mention is not None:
            if mention.subject and not (index == name.end + 2 and words[index - 1] == "and"):
                return False
            if not mention.subject and _object_acts(words, mention.end, names):
                return False
            counted += mention.end - index + 1
            index = mention.end + 1
            continue
        counted += 1
        width = speech_verb_at(words, index, names.tokens)
        if width:
            verdict = _verb_is_subjects(words, index, width, name.end)
            if verdict is not None:
                return verdict
        index += 1
    return False


def _closing_comma(words: list[str], start: int) -> int | None:
    for index in range(start, len(words)):
        if words[index] == ",":
            return index
        if words[index] in _STOPS:
            return None
    return None


def _object_acts(words: list[str], end: int, names: _Names) -> bool:
    """Whether the object name ending at ``end`` takes the verb after it ("lets Wren speak",
    "waits for Wren to say", "nods at Wren to go on")."""
    after = words[end + 1:end + 3]
    if not after:
        return False
    if speech_verb_at(words, end + 1, names.tokens):
        return True
    return (after[0] == "to" and len(after) > 1 and after[1] not in _DETERMINERS
            and not names.ids(after[1]) and after[1] not in _PUNCTUATION)


def _verb_is_subjects(words: list[str], index: int, width: int, name_end: int) -> bool | None:
    """Whether the speech verb at ``index`` is the subject's (True), ends the search (False), or
    is not a verb of the subject's and the search goes on (None)."""
    previous = words[index - 1]
    if previous in _DETERMINERS:
        return None  # a noun: "hears a shout"
    if words[index].endswith("ing") and previous in _PREPOSITIONS:
        return None  # "leaves without saying"
    if previous == "to" and any(word in _DETERMINERS or word in _PREPOSITIONS
                                for word in words[max(name_end + 1, index - 4):index - 1]):
        return None  # someone else's infinitive: "waits for the guard to speak"
    if _noun_subject(words, index, name_end):
        return False  # "and the crowd murmurs": the verb has its own subject
    follower = words[index + width] if index + width < len(words) else ""
    if is_negation(follower):
        return False  # "says nothing"
    if follower in _DIRECTED and not _speech_follows(words, index + width):
        return None  # "growls at nobody in particular"
    return True


def _noun_subject(words: list[str], index: int, name_end: int) -> bool:
    """Whether a noun phrase right before the verb at ``index`` is its subject ("the crowd
    murmurs"), rather than a place the subject is in ("Oda by the hearth murmurs")."""
    for back in range(index - 1, max(name_end, index - 4), -1):
        word = words[back]
        if word in _PUNCTUATION or word in _CONJUNCTIONS:
            return False
        if word in _DETERMINERS:
            return words[back - 1] not in _PREPOSITIONS
    return False


def _speech_follows(words: list[str], start: int) -> bool:
    for word in words[start:]:
        if word == ";":
            return False
        if word in ("that", ":", QUOTE):
            return True
    return False


def _subjects(words: list[str], mentions: dict[int, _Mention]) -> tuple[int | None, set[str]]:
    """The first subject name in ``words`` and every actor any subject name may be."""
    subjects = [index for index, mention in mentions.items() if mention.subject]
    ids: set[str] = set().union(*(mentions[index].ids for index in subjects))
    return (min(subjects) if subjects else None), ids


def _lone_npc(words: list[str], mentions: dict[int, _Mention], npcs: frozenset[str]) -> str | None:
    """The NPC that is the only subject actor of ``words``, if there is one."""
    _first, ids = _subjects(words, mentions)
    return next(iter(ids)) if len(ids) == 1 and ids <= npcs else None


def _lead_voices(words: list[str], quote: int, tail_spoken: bool, names: _Names) -> str | None:
    """The NPC the words before the first quotation hand it to (``Oda leans close: "…"``).

    The lead's only subject actor gets the quotation when the lead is that name alone or ends in
    ``:``, ``;`` or a dash; or when it ends in a comma, the name has at most two plain words after
    it ("Oda leans in, \"…\"") and nothing but attribution follows the quotation. A lead that
    mentions a sign, a tune or the like quotes that thing instead.
    """
    lead = words[:quote]
    mentions = _mentions(lead, names)
    first, ids = _subjects(lead, mentions)
    if first is None or len(ids) != 1 or not ids <= names.npcs or _TEXT_NOUNS & set(lead):
        return None
    tail = [word for word in lead[mentions[first].end + 1:] if word not in _PUNCTUATION]
    if not tail or lead[-1] in _LEADS:
        return next(iter(ids))
    plain = len(tail) <= 2 and not any(word in _DETERMINERS or names.ids(word) for word in tail)
    return next(iter(ids)) if lead[-1] == "," and plain and tail_spoken else None


def _attributed(words: list[str], names: _Names) -> bool:
    """Whether the words outside a quotation say it is spoken: a speech verb, or one voice
    ("comes a low voice"; not "cry a dozen voices")."""
    for index, word in enumerate(words):
        if speech_verb_at(words, index, names.tokens):
            return True
        if word == "voice" and (index == 0 or words[index - 1] not in _NUMBER_WORDS):
            return True
    return False


def voiced(scenario: Scenario, parts: list[str]) -> list[tuple[int, str, str]]:
    """``(sentence, npc_id, evidence)`` for each narration sentence that gives an NPC speech.

    ``speech_verb``: a subject name of the NPC is followed, as ``_speaks`` reads it, by its own
    speech verb. ``quotation``: the sentence has a quotation and either a speech verb stands right
    before the NPC's name ("mutters Oda"); or the words before the quotation hand it to the NPC
    (``_lead_voices``); or the NPC is the only subject of the sentence just before, which names no
    sign or tune, and the words outside the quotation name no other subject and are none, or have a
    speech verb or a single ``voice`` (``Oda leans in. "…"``). Player characters are never voiced
    here: restating a PC's action is narration's job.
    """
    names = _Names(frozenset(scenario.director_ids()),
                   tuple((actor.actor_id, name_tokens(actor)) for actor in scenario.actors),
                   frozenset().union(*(name_tokens(actor) for actor in scenario.actors)))
    found: list[tuple[int, str, str]] = []
    previous: str | None = None
    for number, part in enumerate(parts):
        words = _pieces(part)
        mentions = _mentions(words, names)
        voices: dict[str, str] = {}
        for index, mention in mentions.items():
            if mention.subject and _speaks(words, index, mentions, names):
                for actor_id in sorted(mention.ids & names.npcs):
                    voices.setdefault(actor_id, "speech_verb")
        if QUOTE in words:
            first_quote, last_quote = words.index(QUOTE), len(words) - 1 - words[::-1].index(QUOTE)
            for index, mention in mentions.items():
                if mention.subject and index and words[index - 1] in SPEECH_VERBS:
                    for actor_id in sorted(mention.ids & names.npcs):
                        voices.setdefault(actor_id, "quotation")  # "…," mutters Oda
            tail = words[last_quote + 1:]
            tail_spoken = all(word in _PUNCTUATION for word in tail) or (
                _attributed(tail, names) and not _subjects(tail, _mentions(tail, names))[1])
            lead = _lead_voices(words, first_quote, tail_spoken, names)
            if lead is not None:
                voices.setdefault(lead, "quotation")
            _first, others = _subjects(words, mentions)
            plain = [word for word in words if word not in _PUNCTUATION]
            if previous is not None and others <= {previous} and (
                    not plain or _attributed(plain, names)):
                voices.setdefault(previous, "quotation")
        found.extend((number, actor_id, evidence) for actor_id, evidence in sorted(voices.items()))
        previous = None if _TEXT_NOUNS & set(words) else _lone_npc(words, mentions, names.npcs)
    return found
