"""The speaker rendering contract: one renderer invocation per speaking NPC.

V0.2.1's quality baseline is a separate generation for each NPC contribution.
This contract makes that shape the only one available: a renderer receives a
single ``SpeakerRequest`` (one actor, its fragment text, its cue) and nothing
about what screening removed. It is not an epistemic boundary for the cue,
which callers (027 with 012) must screen themselves. No provider lives here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from grimoire.core.authority import DIRECTOR, AuthorityError, controller_of
from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.core.model import ControlAssignment
from grimoire.evals.text import has_words

from .errors import InvalidVoice, VoiceMismatch
from .fragment import SpeakerFragment


@dataclass(frozen=True, slots=True)
class SpeakerRequest:
    """Everything one renderer call sees."""

    actor: InstanceRef
    fragment_text: str
    cue: str


class SpeakerRenderer(Protocol):
    def __call__(self, request: SpeakerRequest) -> str: ...


@dataclass(frozen=True, slots=True)
class SpokenLine:
    """One rendered contribution, attributed to its fragment's actor by construction."""

    actor: InstanceRef
    portrayal: SourceRef | None
    text: str
    call_index: int


def render_speakers(speakers: Iterable[tuple[SpeakerFragment, str]], renderer: SpeakerRenderer,
                    controls: Iterable[ControlAssignment]) -> tuple[SpokenLine, ...]:
    """Render each NPC speaker by its own call; refuse the whole batch before any call if unsafe.

    Only director-controlled actors are voiced. A player character, an actor
    nobody controls and ambiguous control are all refused: delegated PC speech
    is out of scope here.
    """
    batch = tuple(speakers)
    controls = tuple(controls)
    seen: set[InstanceRef] = set()
    for entry in batch:
        if not (isinstance(entry, tuple) and len(entry) == 2
                and isinstance(entry[0], SpeakerFragment) and isinstance(entry[1], str)):
            raise InvalidVoice("bad_speaker", "each speaker is a (SpeakerFragment, cue) pair")
        actor = entry[0].actor
        if actor in seen:
            raise VoiceMismatch("duplicate_speaker", f"{actor} appears twice in one batch")
        seen.add(actor)
        if controller_of(actor.campaign_id, actor.instance_id, controls) != DIRECTOR:
            raise AuthorityError(f"{actor} is not a director-controlled NPC")

    lines = []
    for index, (fragment, cue) in enumerate(batch):
        text = renderer(SpeakerRequest(fragment.actor, fragment.text, cue))
        if not isinstance(text, str):
            raise InvalidVoice("renderer_result", f"renderer returned no text for {fragment.actor}")
        if not has_words(text):
            raise InvalidVoice("silent_render", f"renderer returned a silent line for {fragment.actor}")
        lines.append(SpokenLine(fragment.actor, fragment.portrayal, text, index))
    return tuple(lines)
