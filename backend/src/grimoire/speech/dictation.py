"""080's speech-to-draft revision: what one dictated utterance does to the composer's unsent text.

Pure and deterministic: no recognizer, no file, no library. The page sends the composer's draft, the passage it
last dictated into it and the names in the scene; this answers the new draft and what changed, for the page to
show. Nothing here posts, advances a scene or writes anything: the draft is only ever the player's own unsent
text, and Post stays the one way it leaves the composer (docs/SPEECH.md, "Speech to draft").

Transcription fidelity comes first. What the recognizer heard is always returned verbatim beside the revision,
and only a few spoken edits change the draft other than by appending what was said:

- **a correction** ("no, I meant the west gate", "I meant ...", "correction ..."), applied only when the words
  line up with part of the last dictated passage ("the east gate"); the passage is otherwise left alone and the
  words are kept as said, with the correction offered as the other reading;
- **scratch that** (or "delete that", "strike that"), which removes the last dictated passage;
- **post it** (or "send it", "submit"...), which never posts: voice cannot submit a fictional action.

Each of these carries the verbatim reading as its ``alternative``, because the player may have been dictating a
character's line ("No, I meant what I said") rather than editing. Appended speech gets a capital at a sentence's
start and a capital "I", and nothing else: no punctuation, rephrasing or name is ever inserted silently.
Names from the scene that sound like part of what was heard are only *suggested*.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

#: The composer's own limit (the chat page's maxlength): a revision never makes a longer draft.
MAX_DRAFT = 20000
#: A recognizer confidence below this flags the passage for checking. Untuned: pocketsphinx's posterior is a rough
#: guide, and it should be tuned against real speech later.
LOW_CONFIDENCE = 0.5
MAX_NAMES = 64
MAX_NAME = 80
MAX_SUGGESTIONS = 3
#: How much is searched, so a long draft or a long recording costs about what a spoken sentence does: a correction
#: is lined up within the last words of the passage, names are looked for in its first words, and a name of more
#: words than this is matched by its parts.
MAX_CORRECTION_WORDS = 24
MAX_ALIGN_WORDS = 120
MAX_SUGGEST_WORDS = 300
MAX_NAME_WORDS = 4
#: The most words a correction takes out ("the big west gate" for "the gate").
MAX_DROPPED = 4

_WORD = re.compile(r"[\w']+")
_FILLERS = frozenset({"no", "sorry", "wait", "oh", "um", "uh", "er", "actually"})
_SCRATCH = frozenset({("scratch", "that"), ("delete", "that"), ("strike", "that")})
_SUBMIT_VERBS = frozenset({"post", "send", "submit"})
_SUBMIT_OBJECTS = frozenset({(), ("it",), ("that",), ("this",), ("my", "turn"), ("the", "turn"), ("my", "reply"),
                             ("the", "reply")})
_POLITE = frozenset({"please", "now"})
#: Only "please" is trimmed from a correction: "no, I meant we leave now" means now.
_COURTESY = frozenset({"please"})
#: Words that do not anchor a correction or a name on their own.
_STOP = frozenset({"a", "an", "the", "to", "of", "and", "or", "in", "on", "at", "for", "by", "with", "from", "i",
                   "we", "you", "he", "she", "it", "they", "is", "are", "was", "my", "our", "your", "his", "her",
                   "its", "their", "that", "this", "me", "us", "him", "them", "so", "but", "as", "be", "do"})
#: Words that open a noun phrase: two of them side by side ("my a road") is never a correction.
_DETERMINERS = frozenset({"a", "an", "the", "my", "our", "your", "his", "her", "its", "their", "this", "that"})
#: Soundex-style groups: letters a recognizer readily swaps for one another in a name it does not know.
_GROUPS = {**dict.fromkeys("bfpv", "p"), **dict.fromkeys("cgjkqsxz", "k"), **dict.fromkeys("dt", "t"),
           **dict.fromkeys("mn", "m"), "l": "l", "r": "r"}


@dataclass(frozen=True)
class Suggestion:
    """A scene name that sounds like ``heard``, a span of the new passage, offered to replace it on a tap."""

    heard: str
    name: str

    def as_dict(self) -> dict:
        return {"heard": self.heard, "name": self.name}


@dataclass(frozen=True)
class Revision:
    """One utterance's effect on the draft.

    ``kind``: ``added`` (appended as said), ``corrected``, ``scratched``, ``not_posted`` (a request to post, which
    voice never does), ``nothing`` (nothing heard) or ``too_long`` (the draft would pass the composer's limit).
    ``text`` is the whole draft afterwards, ``passage`` the dictated passage a following correction or scratch
    would act on (``None`` when there is none). ``before``/``after`` name what a correction or scratch changed.
    ``alternative`` is the other reading of the same words, for the player to choose instead."""

    kind: str
    text: str
    passage: str | None
    heard: str
    before: str | None = None
    after: str | None = None
    uncertain: bool = False
    suggestions: tuple[Suggestion, ...] = ()
    alternative: Revision | None = None

    def as_dict(self) -> dict:
        return {"kind": self.kind, "text": self.text, "passage": self.passage, "heard": self.heard,
                "before": self.before, "after": self.after, "uncertain": self.uncertain,
                "suggestions": [suggestion.as_dict() for suggestion in self.suggestions],
                "alternative": self.alternative.as_dict() if self.alternative is not None else None}


def _tokens(text: str) -> list[tuple[str, int, int]]:
    return [(match.group().lower(), match.start(), match.end()) for match in _WORD.finditer(text)]


def key(text: str) -> str:
    """A rough sound key: the first letter, then consonant groups, vowels and h/w/y dropped, repeats collapsed."""
    letters = re.sub(r"[^a-z]", "", text.lower()).replace("ph", "f").replace("ck", "k")
    if not letters:
        return ""
    out = letters[0]
    last = _GROUPS.get(letters[0], "")
    for letter in letters[1:]:
        group = _GROUPS.get(letter, "")
        if group and group != last:
            out += group
        if letter not in "hw":  # h and w do not separate a repeated sound; a vowel does
            last = group
    return out


def _ratio(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right).ratio() if left and right else 0.0


def _speech(words: list[str]) -> list[str]:
    """Leading fillers ("no", "sorry", "wait") dropped, and a trailing "please"."""
    at = 0
    while at < len(words) and words[at] in _FILLERS:
        at += 1
    rest = words[at:]
    while rest and rest[-1] in _POLITE:
        rest = rest[:-1]
    return rest


def _correction(heard: str) -> str | None:
    """The replacement a spoken correction asks for, as heard, or ``None`` when this is not one."""
    tokens = _tokens(heard)
    words = [word for word, _start, _end in tokens]
    at = 0
    while at < len(words) and words[at] in _FILLERS:
        at += 1
    said_no = "no" in words[:at]
    if words[at:at + 2] == ["i", "meant"] or (said_no and words[at:at + 2] == ["i", "mean"]):
        begin = at + 2
    elif words[at:at + 1] == ["correction"]:
        begin = at + 1
    else:
        return None
    end = len(tokens)
    while end > begin and words[end - 1] in _COURTESY:
        end -= 1  # "... the west gate please": the courtesy is not part of what is put in
    if begin >= end:
        return None
    return heard[tokens[begin][1]:tokens[end - 1][2]].strip(" ,.;:!?-")


def _scratch(heard: str) -> bool:
    return tuple(_speech([word for word, _s, _e in _tokens(heard)])) in _SCRATCH


def _submit(heard: str) -> bool:
    words = _speech([word for word, _s, _e in _tokens(heard)])
    return bool(words) and words[0] in _SUBMIT_VERBS and tuple(words[1:]) in _SUBMIT_OBJECTS


def _stops(words: list[str]) -> bool:
    return all(word in _STOP for word in words)


def _content(words: list[str]) -> bool:
    return not any(word in _STOP for word in words)


def _fits(window: list[str], wanted: list[str], before: str | None, after: str | None) -> int | None:
    """How many words ``window`` and ``wanted`` share when ``wanted`` is plainly a correction of ``window``, else
    ``None``. ``before`` and ``after`` are the passage's words just outside the window (``None`` at its ends).

    Plainly means one edit, around shared words of which one is not a stop word:

    - a swap in place, of content words for as many or more content words ("east" for "west", "orc" for "goblin
      chief"), or of words for words of the same kind one by one ("an old" for "the west");
    - new words put in between shared words ("the gate" for "the west gate"), at the passage's start ("Gate is
      shut" for "the west gate"), or content words put in at the window's start right after a stop word ("gate"
      for "west gate" in "to the gate");
    - content words taken out from between shared words ("the big west gate" for "the gate").

    Anything else (a clause dropped, an article swapped for a phrase, words put in beside a content word, or two
    determiners left side by side, "my a road") is not applied: the words are kept as said and replacing the whole passage is offered, since fidelity comes first."""
    edits = [op for op in SequenceMatcher(None, window, wanted, autojunk=False).get_opcodes() if op[0] != "equal"]
    if len(edits) != 1:
        return None
    tag, start, end, begin, stop = edits[0]
    old, new = window[start:end], wanted[begin:stop]
    shared = [word for at, word in enumerate(window) if not start <= at < end]
    if _stops(shared):
        return None
    if tag == "replace":
        alike = len(old) == len(new) and all((left in _STOP) == (right in _STOP) for left, right in zip(old, new))
        if not (alike or (_content(old) and _content(new) and len(new) >= len(old))):
            return None
    elif tag == "insert":
        if start == len(window) or (start == 0 and before is not None and (not _content(new) or before not in _STOP)):
            return None
        if _stops(new):
            return None
    elif tag == "delete" and (start == 0 or end == len(window) or not _content(old)):
        return None
    if tag != "delete" and ((start == 0 and before in _DETERMINERS and new[0] in _DETERMINERS)
                            or (end == len(window) and after in _DETERMINERS and new[-1] in _DETERMINERS)):
        return None
    return len(shared)


def _repeats(words: list[str]) -> set[tuple[str, ...]]:
    """Each run of one to three words that comes twice in a row ("the the", "and the and the")."""
    return {tuple(words[at:at + 2 * size]) for size in (1, 2, 3) for at in range(len(words) - 2 * size + 1)
            if words[at:at + size] == words[at + size:at + 2 * size]}


def _align(passage: str, replacement: str) -> tuple[int, int] | None:
    """The character span of ``passage`` that ``replacement`` corrects, or ``None`` when nothing lines up.

    A span lines up when the replacement is plainly a correction of it (``_fits``), or, both made only of content
    words and sharing none, sounds like it as a whole (a misheard name: "Marabel" for "Mara Vell"). The most shared
    words win, then the tightest span, then the latest, since a correction usually follows what it corrects; a
    span whose correction would leave words said twice in a row that neither the passage nor the replacement says
    twice ("the the guards", "for for dawn") is passed over. Only the passage's last words are searched, a
    correction drops at most a few words, and a long replacement is not a correction to line up."""
    tokens = _tokens(passage)
    offset = max(0, len(tokens) - MAX_ALIGN_WORDS)
    words = tokens[offset:]
    wanted = [word for word, _s, _e in _tokens(replacement)]
    if not words or not wanted or len(wanted) > MAX_CORRECTION_WORDS:
        return None
    spoken = [word for word, _s, _e in tokens]
    if any(spoken[at:at + len(wanted)] == wanted for at in range(len(spoken))):
        return None  # already said just so: there is nothing to correct
    anchors = {word for word in wanted if word not in _STOP}
    wanted_key = key("".join(wanted))
    found: list[tuple[float, int, int, int, int]] = []
    for size in range(1, min(len(wanted) + MAX_DROPPED, len(words)) + 1):
        for at in range(offset, len(spoken) - size + 1):
            window = spoken[at:at + size]
            if anchors.intersection(window):
                shared = _fits(window, wanted, spoken[at - 1] if at > 0 else None,
                               spoken[at + size] if at + size < len(spoken) else None)
                if shared is None:
                    continue
                score: tuple[float, int, int] = (float(shared), -size, at)
            elif abs(size - len(wanted)) > 1 or not (_content(window) and _content(wanted)):
                continue
            else:
                heard_key = key("".join(window))
                sound = _ratio(heard_key, wanted_key) if min(len(heard_key), len(wanted_key)) >= 3 else 0.0
                if sound < 0.85:
                    continue
                score = (sound, -abs(size - len(wanted)), at)
            found.append((*score, at, at + size))
    for *_score, first, past in sorted(found, reverse=True):
        left, right = spoken[max(0, first - 6):first], spoken[past:past + 6]  # all a three-word repeat can reach
        if _repeats(left + wanted + right) <= _repeats(left + spoken[first:past] + right) | _repeats(wanted):
            return tokens[first][1], tokens[past - 1][2]
    return None


def _sentence_start(prefix: str) -> bool:
    stripped = prefix.rstrip(" \t")
    return not stripped.strip() or stripped.endswith(("\n", ".", "!", "?"))


def _capital_i(text: str) -> str:
    return re.sub(r"\bi\b(?='(?:m|ll|d|ve)\b|\s|$|[,.!?;:])", "I", text)


def _format(passage: str, prefix: str) -> str:
    """Capital "I", and a capital first letter at a sentence's start: the only change made to what was heard."""
    passage = _capital_i(passage.strip())
    if passage and _sentence_start(prefix):
        passage = passage[0].upper() + passage[1:]
    return passage


def _join(prefix: str, passage: str) -> str:
    if not prefix or prefix[-1].isspace():
        return prefix + passage
    return prefix + " " + passage


def _uncertain(confidence: float | None) -> bool:
    return confidence is not None and confidence < LOW_CONFIDENCE


def _names(names: tuple[str, ...]) -> list[str]:
    """Each name, then each word of a longer name ("Mara Vell", then "Mara", "Vell"), once, longest first."""
    found: list[str] = []
    for name in names:
        whole = (" ".join(name.split()),) if len(name.split()) <= MAX_NAME_WORDS else ()
        for part in (*whole, *name.split()):
            if len(key(part)) >= 3 and part.lower() not in _STOP and part not in found:
                found.append(part)
    return sorted(found, key=lambda part: -len(part.split()))  # stable: scene order within a length


def _close(left: str, right: str, at_least: float) -> float:
    """``_ratio`` of the two, or 0 when their lengths alone rule out reaching ``at_least``."""
    if not left or not right or 2 * min(len(left), len(right)) < at_least * (len(left) + len(right)):
        return 0.0
    matcher = SequenceMatcher(None, left, right)
    if matcher.quick_ratio() < at_least:
        return 0.0
    ratio = matcher.ratio()
    return ratio if ratio >= at_least else 0.0


def suggest(passage: str, names: tuple[str, ...]) -> tuple[Suggestion, ...]:
    """Spans of ``passage`` that sound like a scene name without being it: one suggestion per span and name.
    Each span's sound and spelling are worked out once, and only spans the length of a name are compared."""
    words = _tokens(passage)[:MAX_SUGGEST_WORDS]
    present = {word for word, _s, _e in words}
    parts = _names(names)
    longest = max((len(_tokens(part)) for part in parts), default=0)
    spans: dict[int, list[tuple[int, int, str, str]]] = {}
    for size in range(1, longest + 2):
        for at in range(len(words) - size + 1):
            spoken = [word for word, _s, _e in words[at:at + size]]
            if not all(word in _STOP for word in spoken):
                joined = "".join(spoken)
                spans.setdefault(size, []).append((words[at][1], words[at + size - 1][2], key(joined), joined))
    taken: list[tuple[int, int]] = []
    found: list[Suggestion] = []
    for name in parts:
        name_words = [word for word, _s, _e in _tokens(name)]
        if all(word in present for word in name_words):
            continue  # the name is already spelled out
        name_key, name_letters = key(name), re.sub(r"[^a-z]", "", name.lower())
        best: tuple[float, int, int] | None = None
        for size in range(1, len(name_words) + 2):
            for start, end, sound_key, letters in spans.get(size, ()):
                if any(start < stop and begin < end for begin, stop in taken):
                    continue
                sound = _close(sound_key, name_key, 0.85)
                spelling = _close(letters, name_letters, 0.7) if sound else 0.0
                if sound and spelling and (best is None or sound + spelling > best[0]):
                    best = (sound + spelling, start, end)
        if best is not None:
            taken.append((best[1], best[2]))
            found.append(Suggestion(passage[best[1]:best[2]], name))
        if len(found) >= MAX_SUGGESTIONS:
            break
    return tuple(found)


def _added(draft: str, heard: str, confidence: float | None, names: tuple[str, ...]) -> Revision:
    passage = _format(heard, draft)
    text = _join(draft, passage)
    if len(text) > MAX_DRAFT:
        return Revision("too_long", draft, None, heard)
    return Revision("added", text, passage, heard, uncertain=_uncertain(confidence), suggestions=suggest(passage, names))


def _last(draft: str, last: str | None) -> int:
    return draft.rfind(last) if last else -1


def revise(draft: str, last: str | None, heard: str, *, confidence: float | None = None,
           names: tuple[str, ...] = ()) -> Revision:
    """What saying ``heard`` does to ``draft``, whose most recently dictated passage is ``last`` (``None`` when
    nothing was dictated, or the player has since removed it). ``names`` are the scene's, for suggestions."""
    if len(draft) > MAX_DRAFT:
        return Revision("too_long", draft, last, heard)
    heard = heard.strip()
    if not _tokens(heard):
        return Revision("nothing", draft, last, heard)
    verbatim = _added(draft, heard, confidence, names)
    at = _last(draft, last)
    other = verbatim if verbatim.kind != "too_long" else None  # a reading that cannot be had is not offered
    if _submit(heard):
        return Revision("not_posted", draft, last, heard, alternative=other)
    if _scratch(heard):
        if at < 0:
            return verbatim
        head = draft[:at]
        tail = draft[at + len(last):]
        text = (head.rstrip(" ") if not tail.strip() else head) + tail
        return Revision("scratched", text, None, heard, before=last, alternative=other)
    replacement = _correction(heard)
    if replacement is None or at < 0:
        return verbatim
    span = _align(last, replacement)
    if span is None:
        # Nothing lines up: keep the words as said, and offer replacing the whole last passage instead.
        whole = _format(replacement, draft[:at])
        text = draft[:at] + whole + draft[at + len(last):]
        if len(text) > MAX_DRAFT:
            return verbatim
        offered = Revision("corrected", text, whole, heard, before=last, after=whole,
                           uncertain=_uncertain(confidence), suggestions=suggest(whole, names))
        return Revision(verbatim.kind, verbatim.text, verbatim.passage, heard, uncertain=verbatim.uncertain,
                        suggestions=verbatim.suggestions, alternative=offered)
    start, end = span
    replaced = last[start:end]
    put = _format(replacement, draft[:at] + last[:start])
    passage = last[:start] + put + last[end:]
    text = draft[:at] + passage + draft[at + len(last):]
    if len(text) > MAX_DRAFT:
        return Revision("too_long", draft, last, heard)
    return Revision("corrected", text, passage, heard, before=replaced, after=put, uncertain=_uncertain(confidence),
                    suggestions=suggest(put, names), alternative=other)


__all__ = ["LOW_CONFIDENCE", "MAX_ALIGN_WORDS", "MAX_CORRECTION_WORDS", "MAX_DRAFT", "MAX_NAME", "MAX_NAMES", "Revision", "Suggestion", "key", "revise",
           "suggest"]
