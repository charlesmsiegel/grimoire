"""Explicit admission and consumption, never an inference job or truth writer."""
from __future__ import annotations

from collections.abc import Iterable

from grimoire.core.authority import DIRECTOR, AuthorityError, controller_of
from grimoire.core.entity import effective_entity, is_agent
from grimoire.core.identity import InstanceRef
from grimoire.core.index import DefinitionIndex
from grimoire.core.knowledge import Knowledge
from grimoire.core.model import ControlAssignment
from grimoire.core.perspective import actor_knowledge
from grimoire.core.roster import CampaignRoster

from .model import (MAX_RECALL, Hypothesis, HypothesisError, HypothesisLedger, TacticChoice,
                    probability, sequence, text)


def admit(ledger: HypothesisLedger, hypothesis: Hypothesis, *, knowledge: Knowledge,
          roster: CampaignRoster, definitions: DefinitionIndex,
          controls: Iterable[ControlAssignment], authored_by: str,
          expected_revision: int) -> HypothesisLedger:
    """Admit one NPC conjecture from a consistent caller-owned current snapshot.

    No claim text is semantically certified: director-authored conjecture can be
    mistaken. Evidence admission proves access and attribution, never entailment.
    Exact retry rechecks present authority/identity/evidence before returning the
    same value. Persistence/transaction locking belongs to a future adapter.
    """
    if (type(ledger) is not HypothesisLedger or type(hypothesis) is not Hypothesis
            or type(knowledge) is not Knowledge or type(roster) is not CampaignRoster
            or not isinstance(definitions, DefinitionIndex)):
        raise HypothesisError('invalid_input')
    sequence(expected_revision)
    campaign = ledger.campaign_id
    if any(c != campaign for c in (knowledge.campaign_id, roster.campaign_id, hypothesis.owner.campaign_id)):
        raise HypothesisError('campaign_mismatch')
    for actor in (hypothesis.owner, *hypothesis.inferred_actors):
        try:
            entity = effective_entity(roster.instance(actor), definitions)
        except (ValueError, LookupError, TypeError):
            raise HypothesisError('unresolved_actor') from None
        if not is_agent(entity):
            raise HypothesisError('not_agent')
    controls = tuple(controls)
    if not all(type(c) is ControlAssignment for c in controls):
        raise HypothesisError('invalid_control')
    try:
        controller = controller_of(campaign, hypothesis.owner.instance_id, controls)
    except AuthorityError:
        raise HypothesisError('ambiguous_control') from None
    if authored_by != DIRECTOR or controller != DIRECTOR:
        raise HypothesisError('npc_authority_required')
    own = actor_knowledge(knowledge, hypothesis.owner, as_of=hypothesis.moment.sequence)
    available = {e.evidence_id for e in own.evidence}
    if not set(hypothesis.evidence_ids) <= available:
        raise HypothesisError('unavailable_evidence')
    for original_revision, existing in enumerate(ledger.records):
        if existing.hypothesis_id == hypothesis.hypothesis_id:
            if existing != hypothesis or expected_revision != original_revision:
                raise HypothesisError('retry_conflict')
            return ledger
    if expected_revision != ledger.revision:
        raise HypothesisError('stale_revision')
    return HypothesisLedger(campaign, (*ledger.records, hypothesis))


def _current(ledger: HypothesisLedger, owner: InstanceRef, as_of: int) -> tuple[Hypothesis, ...]:
    if type(ledger) is not HypothesisLedger or type(owner) is not InstanceRef:
        raise HypothesisError('invalid_input')
    sequence(as_of)
    if owner.campaign_id != ledger.campaign_id:
        raise HypothesisError('campaign_mismatch')
    visible = tuple(h for h in ledger.records if h.owner == owner and h.moment.sequence <= as_of)
    superseded = {h.supersedes for h in visible}
    return tuple(sorted((h for h in visible if h.hypothesis_id not in superseded),
                        key=lambda h: (-h.moment.sequence, h.hypothesis_id)))


def recall(ledger: HypothesisLedger, owner: InstanceRef, *, as_of: int,
           limit: int = MAX_RECALL) -> tuple[Hypothesis, ...]:
    """Exact-owner private recall, latest first with stable ID tie-breaks.

    This pure projection is not an authentication boundary. A future controller
    API must authorize the requested owner before returning its projection.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_RECALL:
        raise HypothesisError('invalid_recall_limit')
    return _current(ledger, owner, as_of)[:limit]


def choose_tactic(ledger: HypothesisLedger, owner: InstanceRef, hypothesis_id: str, *,
                  as_of: int, minimum_confidence: float, credited_tactic: str,
                  fallback_tactic: str) -> TacticChoice:
    """A supplied salient conjecture changes a proposed tactic, never an action.

    Selection requires a current hypothesis of this exact owner. No other
    actor's knowledge or truth is read and no language-model call is made.
    """
    probability(minimum_confidence)
    text(credited_tactic)
    text(fallback_tactic)
    selected = next((h for h in _current(ledger, owner, as_of) if h.hypothesis_id == hypothesis_id), None)
    if selected is None:
        raise HypothesisError('not_current_hypothesis')
    credited = selected.confidence >= minimum_confidence
    return TacticChoice(owner, hypothesis_id, selected.confidence, selected.evidence_ids,
                        credited_tactic if credited else fallback_tactic, credited)
