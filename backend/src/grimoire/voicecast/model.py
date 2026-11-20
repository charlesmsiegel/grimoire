"""Who speaks with which synthesized voice (083): one library's voice casting, as pure values.

A ``CastEntry`` binds one speech voice (an engine and one of its voices) to one target:

- ``narrator``: the narrator of every campaign (``campaign`` ``None``) or of one campaign;
- ``character``: a character definition, at **any** revision (an unpinned ref) or at exactly one revision or
  portrayal (a pinned ref: a disguise, an outfit, an alternate portrayal);
- ``actor``: one campaign's individual, overriding whatever its definition is cast as.

Every entry carries the voice's **rights**: ``engine`` (a voice the engine ships, under the engine's own licence),
``licensed`` (a third-party voice used under a licence the note names) or ``consented`` (a voice of, or modelled on,
a real person, used with the consent the note records). A ``licensed`` or ``consented`` voice without a note is
refused: the casting never holds a voice whose right to be used nobody wrote down.

A casting changes no canonical text. It is the person's own configuration of how text is read aloud, never a
fact of the fiction, and nothing here reads or writes a source, a campaign or a turn.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from grimoire.core.identity import InstanceRef, SourceRef, validate_segment

TARGETS = ("narrator", "character", "actor")
RIGHTS = ("engine", "licensed", "consented")
FORMAT = "grimoire.voice-casting/1"
#: Enough for a large library's cast; a casting is one small file.
MAX_ENTRIES = 1000
MAX_NOTE = 500
_ID = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")


class CastingError(ValueError):
    """A casting value or document that is not one; ``reason`` is machine-readable."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _fail(reason: str, message: str) -> CastingError:
    return CastingError(reason, message)


def _token(value: object, label: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise _fail("invalid_voice", f"{label} is 1-64 lower-case letters, digits, '.', '_' or '-'")
    return value


@dataclass(frozen=True, slots=True)
class SpeechVoice:
    """One voice of one speech engine, and the right to use it."""

    engine: str
    voice: str
    rights: str = "engine"
    note: str | None = None

    def __post_init__(self) -> None:
        _token(self.engine, "engine")
        _token(self.voice, "voice")
        if self.rights not in RIGHTS:
            raise _fail("invalid_rights", f"rights is one of {RIGHTS}")
        if self.note is not None and (not isinstance(self.note, str) or len(self.note) > MAX_NOTE):
            raise _fail("invalid_rights", f"a rights note is text of at most {MAX_NOTE} characters")
        if self.note is not None and not self.note.strip():
            object.__setattr__(self, "note", None)
        if self.rights != "engine" and self.note is None:
            raise _fail("rights_unrecorded", f"a {self.rights} voice needs a note recording the licence or consent")

    @property
    def key(self) -> str:
        return f"{self.engine}/{self.voice}"


@dataclass(frozen=True, slots=True)
class CastEntry:
    """One target's voice. ``ref`` is a ``SourceRef`` for a character, an ``InstanceRef`` for an actor, and
    ``None`` for the narrator, whose ``campaign`` is ``None`` (every campaign) or one campaign id."""

    target: str
    voice: SpeechVoice
    ref: SourceRef | InstanceRef | None = None
    campaign: str | None = None

    def __post_init__(self) -> None:
        if self.target not in TARGETS:
            raise _fail("invalid_target", f"a cast target is one of {TARGETS}")
        if type(self.voice) is not SpeechVoice:
            raise _fail("invalid_voice", "a cast entry's voice is a SpeechVoice")
        if self.target == "narrator":
            if self.ref is not None:
                raise _fail("invalid_target", "the narrator is cast by campaign, not by reference")
            if self.campaign is not None:
                try:
                    validate_segment(self.campaign, "campaign")
                except ValueError as error:
                    raise _fail("invalid_target", str(error)) from None
        elif self.campaign is not None:
            raise _fail("invalid_target", "only the narrator is cast per campaign; an actor names its own")
        elif self.target == "character" and (type(self.ref) is not SourceRef or self.ref.kind != "character"):
            raise _fail("invalid_target", "a character is cast by a character definition reference")
        elif self.target == "actor" and type(self.ref) is not InstanceRef:
            raise _fail("invalid_target", "an actor is cast by its campaign instance reference")

    @property
    def key(self) -> tuple[str, str]:
        """What the entry is cast for; a casting holds one entry per key."""
        if self.target == "narrator":
            return ("narrator", self.campaign or "")
        return (self.target, str(self.ref))

    def describe(self) -> str:
        """Where this choice was made, in words the person can check."""
        if self.target == "narrator":
            return "the narrator of every campaign" if self.campaign is None else f"the narrator of {self.campaign}"
        if self.target == "actor":
            return f"the campaign's own choice for {self.ref}"
        assert isinstance(self.ref, SourceRef)
        if self.ref.pinned:
            return f"{self.ref.unpinned()} at revision {self.ref.revision} only"
        return f"{self.ref} at any revision"


@dataclass(frozen=True, slots=True)
class Casting:
    """Every entry of one library's casting, at most one per target key, in key order."""

    entries: tuple[CastEntry, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.entries, (str, bytes)) or not isinstance(self.entries, Iterable):
            raise _fail("invalid_casting", "a casting's entries are a sequence of CastEntry")
        entries = tuple(self.entries)
        if not all(type(entry) is CastEntry for entry in entries):
            raise _fail("invalid_casting", "a casting's entries are CastEntry values")
        if len(entries) > MAX_ENTRIES:
            raise _fail("too_many_entries", f"a casting holds at most {MAX_ENTRIES} entries")
        keys = [entry.key for entry in entries]
        if len(set(keys)) != len(keys):
            raise _fail("duplicate_entry", "a target is cast twice")
        object.__setattr__(self, "entries", tuple(sorted(entries, key=lambda entry: entry.key)))

    def lookup(self) -> Mapping[tuple[str, str], CastEntry]:
        return {entry.key: entry for entry in self.entries}

    def get(self, target: str, ref: SourceRef | InstanceRef | None = None, campaign: str | None = None
            ) -> CastEntry | None:
        if target == "narrator":
            if campaign is not None:  # "" is no campaign: it never names every campaign's narrator
                try:
                    validate_segment(campaign, "campaign")
                except (ValueError, TypeError) as error:
                    raise _fail("invalid_target", str(error)) from None
            key = ("narrator", campaign or "")
        else:
            key = (target, str(ref))
        return self.lookup().get(key)

    def with_entry(self, entry: CastEntry) -> Casting:
        """The casting with ``entry`` added, or replacing the entry for the same target."""
        return Casting((*(held for held in self.entries if held.key != entry.key), entry))

    def without(self, target: str, ref: SourceRef | InstanceRef | None = None, campaign: str | None = None
                ) -> Casting:
        probe = self.get(target, ref, campaign)
        if probe is None:
            raise _fail("not_cast", "that target has no voice to remove")
        return Casting(tuple(held for held in self.entries if held.key != probe.key))


# -- the document --------------------------------------------------------------------------------------------------
_ENTRY_KEYS = frozenset({"target", "ref", "campaign", "engine", "voice", "rights", "note"})


def entry_to_document(entry: CastEntry) -> dict[str, object]:
    return {"target": entry.target, "ref": None if entry.ref is None else str(entry.ref),
            "campaign": entry.campaign, "engine": entry.voice.engine, "voice": entry.voice.voice,
            "rights": entry.voice.rights, "note": entry.voice.note}


def entry_from_document(document: object) -> CastEntry:
    if not isinstance(document, Mapping):
        raise _fail("malformed", "a cast entry is an object")
    unknown = set(document) - _ENTRY_KEYS
    if unknown:
        raise _fail("unknown_field", f"unknown cast entry fields: {sorted(unknown)}")
    target = document.get("target")
    ref_text = document.get("ref")
    ref: SourceRef | InstanceRef | None = None
    if ref_text is not None:
        if not isinstance(ref_text, str):
            raise _fail("invalid_target", "a cast entry's ref is text")
        try:
            ref = InstanceRef.parse(ref_text) if target == "actor" else SourceRef.parse(ref_text)
        except ValueError as error:
            raise _fail("invalid_target", str(error)) from None
    campaign = document.get("campaign")
    if campaign is not None and not isinstance(campaign, str):
        raise _fail("invalid_target", "a narrator's campaign is text")
    note = document.get("note")
    voice = SpeechVoice(document.get("engine"), document.get("voice"),  # type: ignore[arg-type]
                        document.get("rights", "engine"), note)  # type: ignore[arg-type]
    return CastEntry(target, voice, ref, campaign)  # type: ignore[arg-type]


def casting_to_document(casting: Casting) -> dict[str, object]:
    return {"format": FORMAT, "cast": [entry_to_document(entry) for entry in casting.entries]}


def casting_from_document(document: object) -> Casting:
    if not isinstance(document, Mapping):
        raise _fail("malformed", "a casting is an object")
    if document.get("format") != FORMAT:
        raise _fail("unsupported_format", f"not a {FORMAT} document")
    unknown = set(document) - {"format", "cast"}
    if unknown:
        raise _fail("unknown_field", f"unknown casting fields: {sorted(unknown)}")
    cast = document.get("cast")
    if not isinstance(cast, list):
        raise _fail("malformed", "a casting's cast is a list")
    if len(cast) > MAX_ENTRIES:
        raise _fail("too_many_entries", f"a casting holds at most {MAX_ENTRIES} entries")
    return Casting(tuple(entry_from_document(item) for item in cast))


__all__ = ["FORMAT", "MAX_ENTRIES", "MAX_NOTE", "RIGHTS", "TARGETS", "CastEntry", "Casting", "CastingError",
           "SpeechVoice", "casting_from_document", "casting_to_document", "entry_from_document", "entry_to_document"]
