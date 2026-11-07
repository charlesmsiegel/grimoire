"""Mislabel review (Sprint 047, first slice): the path 003's correction flow lacks.

003's ``apply_corrections`` lets a player strike or flag a claim about their own player character only. Two
labelling errors escape it (docs/PLAYER_SOVEREIGNTY.md, known limits): a claim whose subject is someone else
but which carries the PC's own words or acts (refused ``not_controller``), and PC speech bundled into an
outcome (refused ``not_player_authority``). ``mislabels`` routes both to review from the labeller's
``voiced_by``; ``flag_mislabel`` lets that PC's controller, and nobody else, flag one.

A flag is a report for the referee and the director, never a strike and never a veto: it grants no
correction right over claims about other actors and changes nothing in 003's review.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..core.authority import (
    DIRECTOR, AuthorityError, ControlAssignment, Correction, CorrectionRefusal, controller_of,
)
from .model import LabelledClaim, Mislabel, RefereeError, RefereeReport, _tuple


def _human(controller: str | None) -> bool:
    return controller not in (None, DIRECTOR)


def mislabels(labelled: Iterable[LabelledClaim], controls: Iterable[ControlAssignment]) -> tuple[Mislabel, ...]:
    """The authorship claims whose labels may be wrong, by claim id.

    ``speaks_for_pc``: the claim carries a player character's own words or acts (``voiced_by``) but is labelled
    about someone else (or about no one). ``speech_in_outcome``: an outcome claim carries a player character's
    words or acts. Ambiguous control of the voice routes the claim too: it fails toward review.
    """
    controls = tuple(controls)
    found = []
    for item in labelled:
        claim, voice = item.claim, item.voiced_by
        if voice is None:
            continue
        try:
            human = _human(controller_of(claim.campaign_id, voice, controls))
        except AuthorityError:
            human = True
        if not human:
            continue
        if claim.domain == "outcome":
            reason = "speech_in_outcome"
        elif claim.subject_id != voice:
            reason = "speaks_for_pc"
        else:
            continue
        found.append(Mislabel(claim.campaign_id, claim.claim_id, reason, voice, item.span, item.quote))
    return tuple(sorted(found, key=lambda m: (m.span, m.claim_id)))


@dataclass(frozen=True, slots=True)
class MislabelFlag:
    """A player's report that a routed claim is labelled wrong: for review, still accepted as it stands."""

    mislabel: Mislabel
    correction: Correction


def flag_mislabel(report: RefereeReport, corrections: Iterable[Correction],
                  controls: Iterable[ControlAssignment],
                  ) -> tuple[tuple[MislabelFlag, ...], tuple[CorrectionRefusal, ...]]:
    """Flag routed mislabels on behalf of the player character's own controller.

    A correction naming a claim the report did not route is ``unknown_claim``; one from anyone but the
    current controller of the PC whose words the claim carries is ``not_controller`` (ambiguous or no control
    included). Nothing is struck: 003's review of the claim is unchanged.
    """
    if type(report) is not RefereeReport:
        raise RefereeError("invalid_field")
    controls, corrections = _tuple(controls, ControlAssignment), _tuple(corrections, Correction)
    routed = {(m.campaign_id, m.claim_id): m for m in report.mislabels}
    flags, refused = [], []
    for correction in corrections:
        mislabel = routed.get((correction.campaign_id, correction.claim_id))
        if mislabel is None:
            refused.append(CorrectionRefusal(correction, "unknown_claim"))
            continue
        try:
            controller = controller_of(correction.campaign_id, mislabel.actor_id, controls)
        except AuthorityError:
            controller = None
        if not _human(controller) or correction.corrected_by != controller:
            refused.append(CorrectionRefusal(correction, "not_controller"))
            continue
        flags.append(MislabelFlag(mislabel, correction))
    return tuple(flags), tuple(refused)


__all__ = ["MislabelFlag", "flag_mislabel", "mislabels"]
