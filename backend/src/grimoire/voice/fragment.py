"""One speaker's bounded voice brief, screened against what that speaker has not learned.

Screening fails closed twice over: an example tagged with any fact outside
``known`` is dropped (allowlist), and any item whose text states a marker of a
secret outside ``known`` is dropped (denylist). Markers match through the
story-quality gate's own normaliser, so the brief and the gate agree on what
"states the secret" means. ``omitted`` records ids for audit; it never reaches
a renderer (see ``render.SpeakerRequest``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import InitVar, dataclass
from typing import Literal

from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.evals.text import contains_marker

from .binding import ResolvedVoice
from .errors import InvalidVoice
from .profile import VoiceAnchor, VoiceExample, check_id

DEFAULT_MAX_CHARS = 1200
MIN_MAX_CHARS = 200
HEADER = "Voice of {name}. Write only {name}'s own words."
EXAMPLES_HEADER = "Style examples (reference only; never repeat them verbatim):"
_SPACE = re.compile(r"\s+")
_SEAL = object()


def _one_line(text: str) -> str:
    return _SPACE.sub(" ", text).strip()


@dataclass(frozen=True, slots=True)
class SecretFact:
    """A fact some speakers have not learned, with the phrases that would state it."""

    fact_id: str
    markers: tuple[str, ...]

    def __post_init__(self) -> None:
        check_id(self.fact_id, "fact_id")
        if isinstance(self.markers, str):
            raise InvalidVoice("bad_markers", f"secret {self.fact_id} markers must be a sequence")
        markers = tuple(self.markers)
        if not markers or not all(isinstance(m, str) and m.strip() for m in markers):
            raise InvalidVoice("bad_markers", f"secret {self.fact_id} needs nonblank markers")
        if len(set(markers)) != len(markers):
            raise InvalidVoice("bad_markers", f"secret {self.fact_id} repeats a marker")
        object.__setattr__(self, "markers", markers)


@dataclass(frozen=True, slots=True)
class Omission:
    """An item left out of one fragment, by id and reason only."""

    kind: Literal["anchor", "example"]
    item_id: str
    reason: Literal["unknown_fact", "secret_marker", "budget"]
    subject: str | None = None  # the fact id for knowledge omissions


@dataclass(frozen=True, slots=True)
class SpeakerFragment:
    """The screened voice brief for exactly one speaker. Only ``speaker_fragment`` builds one."""

    actor: InstanceRef
    portrayal: SourceRef | None
    name: str
    anchors: tuple[VoiceAnchor, ...]
    examples: tuple[VoiceExample, ...]
    omitted: tuple[Omission, ...]
    text: str
    _seal: InitVar[object] = None

    def __post_init__(self, _seal: object) -> None:
        # Guards against accidental hand-built or edited fragments, not a hostile caller.
        if _seal is not _SEAL:
            raise InvalidVoice("unsealed_fragment", "build fragments with speaker_fragment()")


def _known_ids(known: Iterable[str]) -> frozenset[str]:
    if isinstance(known, str):
        raise InvalidVoice("bad_known", "known must be a collection of fact ids, not one string")
    known = tuple(known)
    for fact_id in known:
        check_id(fact_id, "known fact id")
    return frozenset(known)


def _screen(item: VoiceAnchor | VoiceExample, known: frozenset[str],
            hidden: tuple[SecretFact, ...]) -> Omission | None:
    kind = "anchor" if isinstance(item, VoiceAnchor) else "example"
    for fact_id in getattr(item, "reveals", ()):
        if fact_id not in known:
            return Omission(kind, item.item_id, "unknown_fact", fact_id)
    for secret in hidden:
        if any(contains_marker(item.text, marker) for marker in secret.markers):
            return Omission(kind, item.item_id, "secret_marker", secret.fact_id)
    return None


def speaker_fragment(voice: ResolvedVoice, *, secrets: Iterable[SecretFact], known: Iterable[str],
                     max_chars: int = DEFAULT_MAX_CHARS) -> SpeakerFragment:
    """Screen and render one speaker's voice. ``secrets`` and ``known`` have no default on purpose.

    Anchors then examples, in authored order, each included whole while the text
    stays within ``max_chars``; the examples header counts with the first example.
    """
    if not isinstance(voice, ResolvedVoice):
        raise InvalidVoice("bad_voice", "speaker_fragment needs a ResolvedVoice")
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < MIN_MAX_CHARS:
        raise InvalidVoice("bad_budget", f"max_chars must be an integer of at least {MIN_MAX_CHARS}")
    secrets = tuple(secrets)
    if not all(isinstance(secret, SecretFact) for secret in secrets):
        raise InvalidVoice("bad_secrets", "secrets must be SecretFact values")
    known_ids = _known_ids(known)
    hidden = tuple(secret for secret in secrets if secret.fact_id not in known_ids)

    name = _one_line(voice.name)
    text = HEADER.format(name=name)
    if len(text) > max_chars:
        raise InvalidVoice("name_exceeds_budget", "the speaker's name leaves no room for a voice")
    omitted: list[Omission] = []
    anchors: list[VoiceAnchor] = []
    examples: list[VoiceExample] = []
    for item in (*voice.anchors, *voice.examples):
        omission = _screen(item, known_ids, hidden)
        if omission is None:
            if isinstance(item, VoiceAnchor):
                addition = f"\n- {item.aspect}: {_one_line(item.text)}"
            else:
                addition = ("" if examples else f"\n{EXAMPLES_HEADER}") + f"\n> {_one_line(item.text)}"
            if len(text) + len(addition) <= max_chars:
                text += addition
                (anchors if isinstance(item, VoiceAnchor) else examples).append(item)
                continue
            omission = Omission("anchor" if isinstance(item, VoiceAnchor) else "example",
                                item.item_id, "budget")
        omitted.append(omission)
    return SpeakerFragment(voice.actor, voice.portrayal, name, tuple(anchors), tuple(examples),
                           tuple(omitted), text, _SEAL)
