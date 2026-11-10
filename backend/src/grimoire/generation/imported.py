"""Voice profiles derived from an imported character revision's own authored text (026, 037).

An imported character has no hand-authored ``VoiceProfile``, yet its revision already holds how it
speaks: a card's ``mes_example`` demonstrates the voice, and its personality and the speech-bearing
sentences of its description describe it. ``derive_voice`` turns exactly that text into a profile for
exactly that pinned revision, deterministically, so the same revision always speaks the same way and a
new revision gets its own profile. Nothing is stored: the profile is rebuilt from the immutable
revision whenever a catalog is built, so a library imported before this existed speaks too.

A reader turns one revision into plain ``VoiceTexts``. ``card_voice_texts`` reads a 037 card
character's payload. A 026 V1 portrayal keeps its card as verbatim JSON, which this pure package may
not parse, so its reader is an adapter (``grimoire.import_v1.v1_voice_texts``) the caller passes in.

Instructions (system prompt, post-history instructions), the scenario, greetings and creator notes
are never read: they are not how the character sounds, and some are private. Example lines carry no
``reveals``; 014's marker screening still removes any that state a secret the speaker has not learned.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace

from grimoire.core.entity import EntityDefinition
from grimoire.core.identity import LegacyId, SourceRef
from grimoire.core.json_value import thaw
from grimoire.evals.text import has_words, normalize
from grimoire.voice import (
    MAX_ANCHOR_CHARS, MAX_EXAMPLE_CHARS, MAX_ITEMS, ImportedVoiceSource, InvalidVoice, VoiceAnchor, VoiceExample,
    VoiceProfile,
)

CARD_FORMAT = "chara_card_v3"
#: What ``{{user}}`` becomes in an example line: the line is addressed to whoever the speaker talks to.
USER_STAND_IN = "you"
#: Why an imported revision has no profile at all.
NO_VOICE_TEXT = "no_voice_text"
#: Reasons a ``VoiceNote`` records.
NOTE_REASONS = ("truncated", "blank", "duplicate", "over_cap", "description_fallback", "extensions_not_carried",
                "card_unreadable")

_START = re.compile(r"<start>", re.IGNORECASE)
_CHAR_MACRO = re.compile(r"\{\{\s*char\s*\}\}|<bot>", re.IGNORECASE)
_USER_MACRO = re.compile(r"\{\{\s*user\s*\}\}|<user>", re.IGNORECASE)
_MACRO = r"\{\{\s*\w+\s*\}\}|<\w+>"
_NOT_CHAR = r"(?!\{\{\s*char\s*\}\}|<bot>)"
#: Characters that may wrap a speaker: markup, brackets, quotes and a ``>`` quote mark. Each run of them
#: is one character class, never ``\s*[*_]*\s*``: adjacent overlapping runs backtrack quadratically.
_OPEN = r"[\s*_>\[(\"'“”‘’«【「『〔［]*"
_CLOSE = r"[\s*_\])\"'“”‘’»】」』〕］]*"
_ASIDE = r"(?:\([^)\n]{0,200}\)[\s*_]*)?"
#: Wrapping, a speaker, wrapping, an optional ``(aside)`` and an ASCII or full-width colon. A speaker is
#: a macro or one to four words ("Captain of the Guard"). Any such label starts a new turn.
_LABEL = re.compile("^" + _OPEN + "(" + _MACRO + r"|[^\W_][\w'.-]*(?: [^\W_][\w'.-]*){0,3})" + _CLOSE
                    + _ASIDE + r"[:：](?:[*_]+(?=\s|$))?")
#: Another speaker's macro label inside the labelled line ("Hi. [{{user}}]: ...") ends the turn there.
_OTHER_INLINE = re.compile(r"[\[(\"'“‘«【「『〔［]?" + _NOT_CHAR + "(?:" + _MACRO + ")" + _CLOSE + _ASIDE + "[:：]",
                           re.IGNORECASE)
#: Anything in a continuation line that might be someone else's: a macro or an angle tag anywhere; a
#: colon, a lookalike colon (꞉ ˸ ∶), a pipe, a ``>`` or a dash separator early in the line ("User - ...",
#: "Anon | ..."); a wrapped token opening the line followed by a separator ("[Anon] - ..."); or a
#: bracketed token opening the line at all ("【Anon】 ...", "[Anon] ..."). Lines are NFKC-normalised for
#: this decision, so full-width and presentation-form colons (：︓﹕) read as ``:``.
_ANY_MACRO = re.compile(r"\{\{|\}\}|<\s*/?[A-Za-z]")
_EARLY = 64
_EARLY_SEPARATOR = re.compile(r"[:꞉˸∶|>]|\s[-–—]+\s|[–—]")
_WRAPPED_OPENING = re.compile(r"^[\s*_>]*(?:[\[(\"'“‘«「『][^\])\"'”’»」』\n]{1,80}[\])\"'”’»」』][\s*_]*(?:[:꞉˸∶|>]|[-–—])"
                              r"|[\[【〔「『][^\]】〕」』\n]{1,80}[\]】〕」』])")
#: Lines are cut before matching: no turn keeps more than 600 characters, so nothing past twice that
#: can change what is kept, and a huge line costs no more than a short one.
_LINE_CAP = 2 * MAX_EXAMPLE_CHARS
_SENTENCE_END = re.compile(r"(?<=[.!?…])([\"'”’)\]]*)\s+")  # group 1: closing quotes, kept
#: Description sentences that say something about speech; the rest of a description is biography,
#: which the brief already shows as context. Only words that are about talking count, so a "quiet
#: village" or a "formal education" does not become a voice rule.
_SPEECH = re.compile(
    r"\b(speak\w*|spoke\w*|speech|voice\w*|says?|said|talk\w*|accents?|dialect|slang|words?|vocabulary|"
    r"swear\w*|curs\w*|mutter\w*|whisper\w*|drawl\w*|stammer\w*|stutter\w*|lisp\w*|phrases?|"
    r"sarcas\w*|laconic|terse|verbose|chatter\w*)\b", re.IGNORECASE)
#: Aspect rules, first match wins; an unmatched anchor keeps its field's default aspect.
_ASPECTS = (
    ("avoid", re.compile(r"\b(never|avoids?|avoiding|refus\w*|doesn't|does not|won't|will not|don't)\b", re.I)),
    ("cadence", re.compile(r"\b(sentences?|clipped|rhythm\w*|paus\w*|short|terse|halting|measured|slow\w*|"
                           r"quick\w*|rambl\w*|cadence|drawl\w*|stammer\w*|stutter\w*|laconic|verbose)\b", re.I)),
    ("register", re.compile(r"\b(formal\w*|informal|casual\w*|polite\w*|register|tone|respectful\w*|crude\w*|"
                            r"blunt\w*|warm\w*|cold\w*|dry|wry|sarcas\w*|gentle|curt\w*|courteous\w*)\b", re.I)),
    ("diction", re.compile(r"\b(words?|vocabulary|jargon|slang|swear\w*|curs\w*|archaic|idiom\w*|accents?|"
                           r"dialect|diction|phrases?|technical|metaphors?)\b", re.I)),
)


@dataclass(frozen=True, slots=True)
class VoiceNote:
    """Why one piece of imported text was cut, skipped or used as a fallback; never the text itself.

    ``field`` is ``mes_example``, ``personality``, ``description``, ``extensions`` or ``card``; ``index`` is the
    1-based position of the turn or sentence in that field (0: the whole field).
    """

    field: str
    index: int
    reason: str


@dataclass(frozen=True)
class DerivedVoice:
    """The profile one imported revision speaks with, or ``None`` with ``reason`` ``no_voice_text``."""

    portrayal: SourceRef
    profile: VoiceProfile | None
    notes: tuple[VoiceNote, ...] = ()
    texts: VoiceTexts | None = None

    @property
    def reason(self) -> str | None:
        return NO_VOICE_TEXT if self.profile is None else None


@dataclass(frozen=True)
class VoiceTexts:
    """The voice-bearing texts of one imported revision as plain strings, whatever importer wrote it."""

    examples: str = ""
    personality: str = ""
    description: str = ""
    imported: ImportedVoiceSource | None = None
    notes: tuple[VoiceNote, ...] = ()
    #: The name ``examples`` label the character's own turns with: the revision they were authored
    #: for. ``None`` is the speaking name; a derived portrayal re-reading its base's examples keeps
    #: the base's, so a rename never drops its base's turns nor keeps another speaker's.
    speaker: str | None = None


#: Reads one revision's ``VoiceTexts``, or ``None`` when the revision is not of its shape.
VoiceReader = Callable[[EntityDefinition], "VoiceTexts | None"]


def text_of(value: object) -> str:
    return value if isinstance(value, str) else ""


def _mapping(value: object) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def imported_source(fmt: object, card_version: LegacyId | None, examples: str,
                    extensions: object) -> tuple[ImportedVoiceSource | None, tuple[VoiceNote, ...]]:
    """A V3 card's voice fields kept exactly for export (014's ``ImportedVoiceSource``); ``None`` otherwise."""
    if fmt != CARD_FORMAT:
        return None, ()
    plain = thaw(extensions) if isinstance(extensions, Mapping) else {}
    try:
        return ImportedVoiceSource(CARD_FORMAT, card_version, examples, None, plain), ()
    except InvalidVoice:  # an extension 014 cannot freeze: the verbatim import record still holds it
        return (ImportedVoiceSource(CARD_FORMAT, card_version, examples, None, {}),
                (VoiceNote("extensions", 0, "extensions_not_carried"),))


def card_voice_texts(definition: EntityDefinition) -> VoiceTexts | None:
    """The voice texts of a 037 card character (``card.format`` ``chara_card_v3``); ``None`` otherwise."""
    extras = definition.extras
    card = extras.get("card")
    if not (isinstance(card, Mapping) and card.get("format") == CARD_FORMAT):
        return None
    examples = text_of(_mapping(card.get("texts")).get("example-messages"))
    personality = (text_of(_mapping(extras.get("sections")).get("personality"))
                   or text_of(_mapping(card.get("unsectioned")).get("personality")))
    imported, notes = imported_source(CARD_FORMAT, None, examples, card.get("extensions"))
    return VoiceTexts(examples, personality, definition.description, imported, notes, _speaker(card))


def _speaker(card: Mapping) -> str | None:
    """``example_speaker``: the name the examples label the character's turns with, kept by 109's editor
    when it renames an imported character; ``None`` (the speaking name) otherwise."""
    speaker = card.get("example_speaker")
    return speaker if isinstance(speaker, str) and speaker.strip() else None


def _clip(text: str, cap: int) -> str:
    """``text`` cut to at most ``cap`` characters: after the last sentence that fits, else at a space."""
    if len(text) <= cap:
        return text
    prefix = text[:cap]
    ends = [match.end(1) for match in _SENTENCE_END.finditer(prefix + " ")]
    if ends and ends[-1] >= cap // 2:
        return prefix[:ends[-1]].rstrip()
    space = prefix.rfind(" ")
    return prefix[:space].rstrip() if space >= cap // 2 else prefix


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _sentences(text: str) -> list[str]:
    """``text`` as one-line sentences, each keeping its stop and closing quotes."""
    flat, found, start = _one_line(text), [], 0
    for match in _SENTENCE_END.finditer(flat):
        found.append(flat[start:match.end(1)])
        start = match.end()
    found.append(flat[start:])
    return [sentence for sentence in found if sentence]


def _aspect(text: str, default: str) -> str:
    return next((aspect for aspect, rule in _ASPECTS if rule.search(text)), default)


def example_lines(examples: str, name: str, speaker: str | None = None) -> list[str]:
    """The character's own turns in a ``mes_example``, in order, with macros filled in.

    Blocks are split on ``<START>``. A turn begins at a speaker label (see ``_LABEL``) and runs over the
    unlabelled lines after it; only turns labelled ``{{char}}``, ``<BOT>`` or the character's own name
    are kept (``speaker``'s name when given, else ``name``). The rule fails closed by construction, because a line wrongly kept puts someone else's
    words, maybe a secret, in this voice: a continuation line is kept only when it holds no macro or
    angle tag at all, no colon or dash separator in its first 64 characters, and does not open with a
    wrapped token and a separator (see ``_continues``). Any other line ends the turn and what follows
    is dropped, never appended. The labelled line itself is cut at another speaker's macro label. One
    limit is inherent: another speaker's line with no label and no separator ("I tell you the code is
    4471") cannot be told from the character's own continuation, and is kept. So
    some of the character's own lines are dropped too ("Remember: the tide waits.", "*{{char}}
    smiles.*"): that is the price. Text before any label is not a turn. ``{{char}}`` becomes the name
    and ``{{user}}`` becomes ``you``.
    """
    own = normalize(name if speaker is None else speaker)
    turns: list[str] = []
    for block in _START.split(examples.replace("\r\n", "\n").replace("\r", "\n")):
        current: list[str] | None = None
        for line in block.split("\n"):
            line = line[:_LINE_CAP]
            label = _LABEL.match(line)
            if label is not None:
                if current is not None:
                    turns.append(" ".join(current))
                speaker = label.group(1)
                mine = bool(_CHAR_MACRO.fullmatch(speaker)) or normalize(speaker) == own
                current = None
                if mine:
                    rest = line[label.end():]
                    other = _OTHER_INLINE.search(rest)
                    current = [rest[:other.start()].strip() if other else rest.strip()]
                    if other:
                        turns.append(" ".join(current))
                        current = None
            elif current is not None:
                if _continues(line):
                    current.append(line.strip())
                else:
                    turns.append(" ".join(current))
                    current = None
        if current is not None:
            turns.append(" ".join(current))
    return [_one_line(_USER_MACRO.sub(USER_STAND_IN, _CHAR_MACRO.sub(lambda _: name, turn))) for turn in turns]


def _continues(line: str) -> bool:
    """Whether an unlabelled line may join the turn above it: only when nothing in it could be a label."""
    probe = unicodedata.normalize("NFKC", line)  # for the decision only: the kept text stays as written
    return not (_ANY_MACRO.search(probe) or _EARLY_SEPARATOR.search(probe[:_EARLY])
                or _WRAPPED_OPENING.match(probe))


class _Builder:
    def __init__(self, notes: tuple[VoiceNote, ...]) -> None:
        self.anchors: list[VoiceAnchor] = []
        self.examples: list[VoiceExample] = []
        self.notes = list(notes)
        self.seen: set[str] = set()

    def _accept(self, field: str, index: int, text: str, cap: int, count: int) -> str | None:
        clipped = _clip(text, cap)
        if not has_words(clipped):
            self.notes.append(VoiceNote(field, index, "blank"))
            return None
        key = normalize(clipped)
        if key in self.seen:
            self.notes.append(VoiceNote(field, index, "duplicate"))
            return None
        if count >= MAX_ITEMS:
            self.notes.append(VoiceNote(field, index, "over_cap"))
            return None
        if clipped != text:
            self.notes.append(VoiceNote(field, index, "truncated"))
        self.seen.add(key)
        return clipped

    def anchor(self, field: str, index: int, text: str, default: str) -> None:
        kept = self._accept(field, index, text, MAX_ANCHOR_CHARS, len(self.anchors))
        if kept is not None:
            self.anchors.append(VoiceAnchor(f"{field}-{index}", _aspect(kept, default), kept))

    def example(self, index: int, text: str) -> None:
        kept = self._accept("mes_example", index, text, MAX_EXAMPLE_CHARS, len(self.examples))
        if kept is not None:
            self.examples.append(VoiceExample(f"example-{index}", kept))


def _packed(sentences: list[str], cap: int) -> list[str]:
    """Consecutive sentences joined into as few pieces of at most ``cap`` characters as they fit."""
    pieces: list[str] = []
    for sentence in sentences:
        if pieces and len(pieces[-1]) + 1 + len(sentence) <= cap:
            pieces[-1] = f"{pieces[-1]} {sentence}"
        else:
            pieces.append(sentence)
    return pieces


def derive_profile(ref: SourceRef, name: str, texts: VoiceTexts) -> DerivedVoice:
    """The voice of revision ``ref`` (speaking as ``name``) from ``texts``.

    Order, within each field in authored order: the personality packed into anchors (``register``),
    description sentences about speech (``habit``), then the example turns. Caps are 014's: at most 32
    anchors and 32 examples, anchors of 400 and examples of 600 characters. A longer item is cut at its
    last whole sentence (else a space) and noted ``truncated``; a blank or repeated item is skipped
    (``blank``, ``duplicate``) and one past the count cap is skipped (``over_cap``). With nothing else
    to speak from, the description's opening is one ``register`` anchor (``description_fallback``): a
    character with any authored text speaks. Only one with no usable text has no profile.
    """
    build = _Builder(texts.notes)
    for index, piece in enumerate(_packed(_sentences(texts.personality), MAX_ANCHOR_CHARS), 1):
        build.anchor("personality", index, piece, "register")
    described = _sentences(texts.description)
    for index, sentence in enumerate(described, 1):
        if _SPEECH.search(sentence):
            build.anchor("description", index, sentence, "habit")
    texts = texts if texts.speaker is not None else replace(texts, speaker=name)
    for index, line in enumerate(example_lines(texts.examples, name, texts.speaker), 1):
        build.example(index, line)
    if not build.anchors and not build.examples and described:
        opening = _packed(described, MAX_ANCHOR_CHARS)[0]
        build.notes.append(VoiceNote("description", 0, "description_fallback"))
        build.anchor("description", 0, opening, "register")
    if not build.anchors and not build.examples:
        return DerivedVoice(ref, None, tuple(build.notes), texts)
    profile = VoiceProfile(ref, tuple(build.anchors), tuple(build.examples), texts.imported)
    return DerivedVoice(ref, profile, tuple(build.notes), texts)


def derive_voice(definition: EntityDefinition, readers: Iterable[VoiceReader] = ()) -> DerivedVoice | None:
    """The voice of one imported character revision from its own text; ``None`` if it is not imported.

    ``readers`` are tried in order before ``card_voice_texts``; the first that recognises the revision
    supplies its texts (see ``derive_profile`` for what they become).
    """
    if not isinstance(definition, EntityDefinition) or definition.ref.kind != "character":
        return None
    for reader in (*readers, card_voice_texts):
        texts = reader(definition)
        if texts is not None:
            return derive_profile(definition.ref, definition.name, texts)
    return None


__all__ = ["CARD_FORMAT", "NO_VOICE_TEXT", "NOTE_REASONS", "USER_STAND_IN", "DerivedVoice", "VoiceNote",
           "VoiceReader", "VoiceTexts", "card_voice_texts", "derive_profile", "derive_voice", "example_lines",
           "imported_source", "text_of"]
