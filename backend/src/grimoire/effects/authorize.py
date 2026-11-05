"""Who may make an effect playable truth (Sprint 021): the pure part of two-key authority.

The **outcome key** is the director's: every acceptance is a director decision, made by trusted
application code holding the library's handle (checked by the service). The **authorship key** is
the player's, for every operation whose agent the player controls: it needs that player's own 003
action contribution for this turn **and** the player's endorsement of the exact operations (a
trusted human handle, also checked by the service). Narration never holds either key, and a
player alone can never make their character's action succeed.

Each holdings line is an act like an operation (its ``agent`` and ``backed_by``). Each ability use is
an act of its attempt's actor: when a player controls it, its backing is the contribution whose id is
the attempt id (025: a player's ``attempt_id`` is their action's ``contribution_id``).

``authorize`` decides the domains and which contributions need endorsing. It reads no files.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from grimoire.core.authority import DIRECTOR, PlayerContribution, controller_of
from grimoire.core.identity import InstanceRef
from grimoire.core.model import ControlAssignment

from .model import (
    CONTRIBUTION_TEXT_MAX, TEXT_MAX, ContributionSnapshot, ControlSnapshot, EffectError, EffectProposal,
)


@dataclass(frozen=True, slots=True)
class Authorization:
    """Per-operation domains, and the contributions whose submitters must endorse the operations."""

    domains: tuple[str, ...]
    contributions: tuple[ContributionSnapshot, ...]
    endorsers: Mapping[str, str]  # contribution_id -> the principal who must endorse
    controls: tuple[ControlSnapshot, ...]
    holdings_domains: tuple[str, ...] = ()  # one per holdings line
    ability_domains: tuple[str, ...] = ()  # one per ability use, in the proposal's order


def _snapshot(contribution: PlayerContribution) -> ContributionSnapshot:
    """003 puts no length cap on a contribution; an audit record does, and says so by name."""
    if len(contribution.text) > CONTRIBUTION_TEXT_MAX or any(
            len(value) > TEXT_MAX for value in (contribution.contribution_id, contribution.submitted_by,
                                                contribution.scene_id, contribution.turn_id)):
        raise EffectError("contribution_too_long", (contribution.contribution_id[:64],))
    return ContributionSnapshot(contribution.contribution_id, contribution.actor_id, contribution.submitted_by,
                                contribution.kind, contribution.scene_id, contribution.turn_id, contribution.text)


def authorize(proposal: EffectProposal, controls: Iterable[ControlAssignment],
              contributions: Iterable[PlayerContribution] = ()) -> Authorization:
    """Domains per operation and the endorsements required; refusals are ``EffectError``.

    Ambiguous control of an agent raises 003's ``AuthorityError``, as everywhere else.
    """
    if type(proposal) is not EffectProposal:
        raise EffectError("invalid_field")
    controls = tuple(controls)
    if not all(type(c) is ControlAssignment for c in controls):
        raise EffectError("invalid_field")
    by_id: dict[str, PlayerContribution] = {}
    for contribution in contributions:
        if type(contribution) is not PlayerContribution:
            raise EffectError("invalid_field")
        if contribution.contribution_id in by_id:
            raise EffectError("invalid_expression", (contribution.contribution_id,))
        by_id[contribution.contribution_id] = contribution
    campaign, moment = proposal.campaign_id, proposal.moment
    used: dict[str, PlayerContribution] = {}
    endorsers: dict[str, str] = {}
    snapshots: dict[str, str] = {}

    def domain_of(agent: InstanceRef | None, backed_by: str | None, *, implied: bool = False) -> str:
        """``implied``: an ability use, whose backing is the attempt's own contribution when a player acts."""
        controller = None if agent is None else controller_of(campaign, agent.instance_id, controls)
        if agent is not None and controller is not None:
            snapshots[agent.instance_id] = controller
        if controller is None or controller == DIRECTOR:
            if backed_by is not None and not implied:
                raise EffectError("unexpected_backing", (backed_by,))
            return "director"
        if backed_by is None or backed_by not in by_id:
            raise EffectError("pc_authorship", (agent.instance_id,))  # type: ignore[union-attr]
        contribution = by_id[backed_by]
        if (contribution.campaign_id, contribution.scene_id, contribution.turn_id, contribution.actor_id,
                contribution.kind, contribution.submitted_by) != (
                campaign, moment.scene_id, moment.turn_id, agent.instance_id, "action", controller):  # type: ignore[union-attr]
            raise EffectError("invalid_expression", (contribution.contribution_id,))
        used[contribution.contribution_id] = contribution
        endorsers[contribution.contribution_id] = controller
        return "voluntary_action"

    domains = tuple(domain_of(op.agent, op.backed_by) for op in proposal.operations)
    lines = () if proposal.holdings is None else proposal.holdings.lines
    holdings_domains = tuple(domain_of(item.agent, item.backed_by) for item in lines)
    ability_domains = tuple(domain_of(attempt.actor, attempt.attempt_id, implied=True)
                            for attempt in proposal.ability_attempts)
    return Authorization(
        domains, tuple(_snapshot(used[cid]) for cid in sorted(used)), dict(sorted(endorsers.items())),
        tuple(ControlSnapshot(actor, controller) for actor, controller in sorted(snapshots.items())),
        holdings_domains, ability_domains)
