"""Authored voice evidence for one portrayal revision.

A voice says how a portrayal *sounds* in text: descriptive anchors plus example
lines. It is deliberately not a campaign individual's mood, knowledge or
relationships, so a profile has no field for them. Physical speaking voice
(timbre, TTS) is a different contract. Caps are provisional first-slice values.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from grimoire.core.identity import LegacyId, SourceRef, validate_segment
from grimoire.evals.text import has_words

from .errors import InvalidVoice

ASPECTS = ("register", "cadence", "diction", "habit", "avoid")
MAX_ANCHOR_CHARS = 400
MAX_EXAMPLE_CHARS = 600
MAX_ITEMS = 32
CARD_FORMATS = ("chara_card_v3",)
MAX_EXTENSION_DEPTH = 64


def check_id(value: object, label: str) -> None:
    """Identity segment grammar; the message never echoes the value (it may be private text)."""
    try:
        validate_segment(value, label)
    except ValueError:
        raise InvalidVoice("bad_id", f"{label} is not a valid id") from None


def _text(value: object, label: str, cap: int) -> None:
    if not isinstance(value, str) or not has_words(value):
        raise InvalidVoice("blank_text", f"{label} must contain words")
    if len(value) > cap:
        raise InvalidVoice("text_too_long", f"{label} is longer than {cap} characters")


@dataclass(frozen=True, slots=True)
class VoiceAnchor:
    """A descriptive rule ("clipped; never uses contractions") that examples can only imply."""

    anchor_id: str
    aspect: str
    text: str

    def __post_init__(self) -> None:
        check_id(self.anchor_id, "anchor_id")
        if self.aspect not in ASPECTS:
            raise InvalidVoice("bad_aspect", f"anchor {self.anchor_id} has an unknown aspect")
        _text(self.text, f"anchor {self.anchor_id}", MAX_ANCHOR_CHARS)

    @property
    def item_id(self) -> str:
        return self.anchor_id


@dataclass(frozen=True, slots=True)
class VoiceExample:
    """One line this portrayal could say: a style reference, never text to reproduce.

    ``reveals`` names the facts the line presupposes; a speaker who has not
    learned all of them is never shown it.
    """

    example_id: str
    text: str
    reveals: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        check_id(self.example_id, "example_id")
        _text(self.text, f"example {self.example_id}", MAX_EXAMPLE_CHARS)
        if not isinstance(self.reveals, (list, tuple)):  # ordered, so audit subjects are stable
            raise InvalidVoice("bad_reveals", f"example {self.example_id} reveals must be a list or tuple")
        reveals = tuple(self.reveals)
        for fact_id in reveals:
            check_id(fact_id, f"example {self.example_id} reveals")
        if len(set(reveals)) != len(reveals):
            raise InvalidVoice("duplicate_id", f"example {self.example_id} repeats a revealed fact")
        object.__setattr__(self, "reveals", reveals)

    @property
    def item_id(self) -> str:
        return self.example_id


VoiceItem = VoiceAnchor | VoiceExample


def validate_items(anchors: object, examples: object, label: str) -> tuple[tuple[VoiceAnchor, ...],
                                                                         tuple[VoiceExample, ...]]:
    """Typed, capped, uniquely identified anchors and examples; shared by profiles and bindings."""
    if isinstance(anchors, str) or isinstance(examples, str):
        raise InvalidVoice("bad_items", f"{label} items must be sequences")
    anchors, examples = tuple(anchors), tuple(examples)
    if not all(isinstance(item, VoiceAnchor) for item in anchors):
        raise InvalidVoice("bad_items", f"{label} anchors must be VoiceAnchor values")
    if not all(isinstance(item, VoiceExample) for item in examples):
        raise InvalidVoice("bad_items", f"{label} examples must be VoiceExample values")
    if len(anchors) > MAX_ITEMS or len(examples) > MAX_ITEMS:
        raise InvalidVoice("too_many_items", f"{label} has more than {MAX_ITEMS} anchors or examples")
    ids = [item.item_id for item in (*anchors, *examples)]
    if len(set(ids)) != len(ids):
        raise InvalidVoice("duplicate_id", f"{label} repeats an item id")
    return anchors, examples


# --- imported card fields ----------------------------------------------------

def _freeze(value: object, path: str, depth: int = 0) -> object:
    """Lossless, immutable JSON-shaped copy; problems are located by index path, never key text."""
    if depth > MAX_EXTENSION_DEPTH:
        raise InvalidVoice("bad_extension", f"{path} nests deeper than {MAX_EXTENSION_DEPTH} levels")
    if isinstance(value, Mapping):
        frozen = {}
        for position, (key, item) in enumerate(value.items()):
            if not isinstance(key, str):
                raise InvalidVoice("bad_extension", f"{path}[{position}] has a non-text key")
            frozen[key] = _freeze(item, f"{path}[{position}]", depth + 1)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item, f"{path}[{position}]", depth + 1) for position, item in enumerate(value))
    if isinstance(value, float) and not math.isfinite(value):
        raise InvalidVoice("bad_extension", f"{path} is not a finite number")
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise InvalidVoice("bad_extension", f"{path} is not a JSON value")


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class ImportedVoiceSource:
    """Card voice fields kept exactly for a loss-aware import and export; never rendered.

    Sprint 026 decides how ``example_messages`` and ``anchor_text`` become native
    examples and anchors. Tuples in ``extensions`` thaw back to lists. Not
    hashable (nor is a profile carrying one): extensions are read-only mappings.
    """

    __hash__ = None  # type: ignore[assignment]

    format: str
    card_version: LegacyId | None = None
    example_messages: str = ""
    anchor_text: str | None = None
    extensions: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.format not in CARD_FORMATS:
            raise InvalidVoice("bad_format", "imported voice format is not supported")
        if self.card_version is not None and not isinstance(self.card_version, LegacyId):
            raise InvalidVoice("bad_card_version", "card_version must be a LegacyId")
        if not isinstance(self.example_messages, str):
            raise InvalidVoice("bad_imported_text", "example_messages must be text")
        if self.anchor_text is not None and not isinstance(self.anchor_text, str):
            raise InvalidVoice("bad_imported_text", "anchor_text must be text")
        if not isinstance(self.extensions, Mapping):
            raise InvalidVoice("bad_extension", "extensions must be a mapping")
        object.__setattr__(self, "extensions", _freeze(self.extensions, "extensions"))

    def thaw(self) -> dict:
        return _thaw(self.extensions)


# --- profiles ----------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class VoiceProfile:
    """The authored voice of exactly one pinned portrayal revision."""

    portrayal: SourceRef
    anchors: tuple[VoiceAnchor, ...] = ()
    examples: tuple[VoiceExample, ...] = ()
    imported: ImportedVoiceSource | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.portrayal, SourceRef) or not self.portrayal.pinned:
            raise InvalidVoice("unpinned_portrayal", "a voice belongs to one pinned portrayal revision")
        if self.portrayal.kind != "character":
            raise InvalidVoice("bad_kind", "this slice voices character portrayals only")
        anchors, examples = validate_items(self.anchors, self.examples, "profile")
        if not anchors and not examples:
            raise InvalidVoice("empty_voice", "a voice needs at least one anchor or example")
        if self.imported is not None and not isinstance(self.imported, ImportedVoiceSource):
            raise InvalidVoice("bad_imported", "imported must be an ImportedVoiceSource")
        object.__setattr__(self, "anchors", anchors)
        object.__setattr__(self, "examples", examples)

    def items(self) -> Iterator[VoiceItem]:
        yield from self.anchors
        yield from self.examples


class VoiceCatalog:
    """Immutable profiles keyed by exact pinned revision; no head-following, no namesakes."""

    __slots__ = ("_profiles",)

    def __init__(self, profiles: Iterable[VoiceProfile]) -> None:
        found: dict[SourceRef, VoiceProfile] = {}
        for profile in profiles:
            if not isinstance(profile, VoiceProfile):
                raise InvalidVoice("bad_profile", "a catalog holds VoiceProfile values")
            if profile.portrayal in found:
                raise InvalidVoice("duplicate_profile", f"two voices for {profile.portrayal}")
            found[profile.portrayal] = profile
        self._profiles = MappingProxyType(found)

    def get(self, ref: SourceRef) -> VoiceProfile | None:
        return self._profiles.get(ref) if isinstance(ref, SourceRef) and ref.pinned else None

    def __iter__(self) -> Iterator[VoiceProfile]:
        return iter(self._profiles.values())

    def __len__(self) -> int:
        return len(self._profiles)
