"""Plan one generation turn from the campaign's control, never from a caller's say-so."""

from __future__ import annotations

from collections.abc import Iterable

from grimoire.core.authority import DIRECTOR, AuthorityError, controller_of
from grimoire.core.inference import (
    InferencePolicy, InferenceStage, OptionalRequest, TurnInferencePlan, plan_turn,
)
from grimoire.core.model import ControlAssignment

from .errors import GenerationError

#: The planned purpose of the optional scene-level reply.
DIRECTOR_PURPOSE = "director reply"


def _texts(values: object, label: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise GenerationError("bad_input", f"{label} must be a collection of ids, not one string")
    try:
        values = tuple(values)  # type: ignore[arg-type]
    except TypeError:
        raise GenerationError("bad_input", f"{label} must be a collection of ids") from None
    if not all(isinstance(value, str) for value in values):
        raise GenerationError("bad_input", f"{label} must hold string ids")
    return values


def checked_controls(controls: Iterable[ControlAssignment]) -> tuple[ControlAssignment, ...]:
    """``controls`` as a tuple of ControlAssignment values, or ``bad_controls``."""
    if isinstance(controls, (str, bytes)):
        raise GenerationError("bad_controls", "controls must be a collection of ControlAssignment")
    try:
        controls = tuple(controls)
    except TypeError:
        raise GenerationError("bad_controls", "controls must be a collection of ControlAssignment") from None
    if not all(isinstance(control, ControlAssignment) for control in controls):
        raise GenerationError("bad_controls", "controls must be ControlAssignment values")
    return controls


def controller(campaign_id: str, actor_id: str, controls: tuple[ControlAssignment, ...]) -> str | None:
    """The actor's one controller, or ``None``; two different controllers is ``ambiguous_control``."""
    try:
        return controller_of(campaign_id, actor_id, controls)
    except AuthorityError:
        raise GenerationError("ambiguous_control", f"{actor_id} has more than one controller") from None


def require_director(campaign_id: str, actor_id: str, controls: tuple[ControlAssignment, ...]) -> None:
    """Only a director-controlled NPC is voiced: a player character or an unassigned actor is refused."""
    found = controller(campaign_id, actor_id, controls)
    if found is None:
        raise GenerationError("unassigned_speaker", f"{actor_id} has no controller, so nobody voices it")
    if found != DIRECTOR:
        raise GenerationError("player_speaker", f"{actor_id} is a player character; its player speaks for it")


def plan_generation_turn(campaign_id: str, turn_id: str, *, present: Iterable[str], speakers: Iterable[str],
                         controls: Iterable[ControlAssignment], director_reply: bool = False,
                         policy: InferencePolicy | None = None, phase: str = "main") -> TurnInferencePlan:
    """One planned ``npc_generation`` call per speaking NPC, and at most one optional director reply.

    Who is player-controlled is derived from ``controls``: every present actor whose controller is
    a human. A speaker must be present and director-controlled. A silent present NPC costs nothing.
    ``director_reply`` requests the optional ``director_generation`` call; a policy without a cap for
    it declines the request as data (``plan.declined``), it is never planned implicitly.
    """
    controls = checked_controls(controls)
    present = _texts(present, "present")
    speakers = _texts(speakers, "speakers")
    if not isinstance(director_reply, bool):
        raise GenerationError("bad_input", "director_reply must be true or false")
    owners = {actor_id: controller(campaign_id, actor_id, controls) for actor_id in present}
    for speaker in speakers:
        if speaker not in owners:
            raise GenerationError("absent_speaker", f"{speaker} is not present this turn")
        require_director(campaign_id, speaker, controls)
    players = [actor_id for actor_id, owner in owners.items() if owner not in (None, DIRECTOR)]
    requests = ((OptionalRequest(InferenceStage.DIRECTOR_GENERATION, None, DIRECTOR_PURPOSE),)
                if director_reply else ())
    return plan_turn(campaign_id, turn_id, present_actor_ids=present, speaking_npc_ids=speakers,
                     player_controlled_ids=players, optional_requests=requests, phase=phase, policy=policy)
