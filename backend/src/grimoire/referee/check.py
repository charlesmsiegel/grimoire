"""The referee (Sprint 047, first slice): check labelled claims of a proposed narration against the records.

``referee`` reads the narration only to confirm each claim's anchor, checks every claim against the ``Facts``
of its category and reports what it found, with the records each finding rests on. It never changes the
narration, decides nothing, writes nothing and calls no model.

The checks are deterministic and director-side:

- **presence** (041): an actor the stage has off stage, or that is not in the scene's cast, cannot act there.
- **staging** (041): ``reaches``, ``sees`` and ``hears`` ask the stage's own questions (``reach``, ``sees``,
  ``hears``), so the referee and a physical change never disagree about a locked door.
- **possession** (023, 034): a counted item cannot be spent past its count; a unique item is wielded only from
  the actor's own custody.
- **knowledge** (012): an actor reveals only a proposition it holds (a report received or sent, a belief of
  its own), as 027's ``known_ids`` reads it.
- **outcome** (029, 040): narration agrees with a recorded resolution, and an obstacle is overcome only when
  its derived state says so; an escalation that prevents the approach narrated is cited.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from ..core import InstanceRef, KnowledgeError, Subject
from ..core.authority import AuthorityError, review_narration
from ..core.perspective import actor_knowledge, knowledge_about, standing
from ..staging import StagingRefused, hears, reach, sees, where
from .mislabel import mislabels
from .model import (
    MAX_CLAIMS, Authority, ContinuityClaim, Evidence, Facts, Finding, LabelledClaim, RefereeError,
    RefereeReport, _tuple, check_text,
)

#: Answers from 041 that mean the label, not the narration, is wrong: reported unchecked.
_LABEL_ERRORS = frozenset({"is_self", "is_actor", "holds_actor"})
#: How far a unique item's custody is followed through containers (a sword in a satchel Mara carries).
_CUSTODY_DEPTH = 16


class _Problem:
    """A claim the facts do not bear out: why, the records, the assumed staging, and whether it is sure."""

    __slots__ = ("reason", "evidence", "assumed", "sure")

    def __init__(self, reason: str, evidence: Iterable[Evidence], assumed: Iterable[str] = (), *,
                 sure: bool = True) -> None:
        self.reason = reason
        self.evidence = tuple(evidence)
        self.assumed = tuple(sorted(set(assumed)))
        self.sure = sure


class _Unchecked:
    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        self.reason = reason


# -- staging ------------------------------------------------------------------------------------------------

def _whereabouts(stage, who: InstanceRef) -> tuple[Evidence, str | None]:
    found = where(stage, who)
    if found.status == "off_stage":
        if found.absent == who:
            absence = stage.absence(who)
            via = absence.via if absence is not None and absence.via is not None else "-"
            return Evidence("stage", f"absence:{who}", f"off_stage:via={via}"), None
        return Evidence("stage", f"placement:{who}", f"off_stage:with={found.absent}"), None
    if found.status == "held":
        return Evidence("stage", f"placement:{who}", f"held:{found.holder}:in={found.area}"), found.area
    if found.status == "in_area":
        return Evidence("stage", f"placement:{who}", f"in_area:{found.area}"), found.area
    return Evidence("stage", f"placement:{who}", "untracked"), None


def _staging_evidence(stage, first: InstanceRef, second: InstanceRef) -> list[Evidence]:
    (one, area_one), (two, area_two) = _whereabouts(stage, first), _whereabouts(stage, second)
    found = [one, two]
    if area_one is not None and area_two is not None and area_one != area_two:
        ways = stage.ways_between(area_one, area_two)
        found += [Evidence("stage", f"way:{way.way_id}", f"{way.state}:{way.basis}") for way in ways]
        if not ways:
            found.append(Evidence("stage", f"areas:{area_one}:{area_two}", "no_way_between"))
    return found


def _not_cast(stage, actor: InstanceRef) -> _Problem | None:
    if actor in stage.actors:
        return None
    return _Problem("not_cast", (Evidence("stage", f"cast:{stage.scene_id}", f"not_in_cast:{actor}"),))


def _check_present(claim: ContinuityClaim, facts: Facts):
    stage = facts.stage
    missing = _not_cast(stage, claim.actor)
    if missing is not None:
        return missing
    found = where(stage, claim.actor)
    if found.status == "off_stage":
        return _Problem("off_stage", (_whereabouts(stage, claim.actor)[0],), found.assumed)
    return None


def _check_staging(claim: ContinuityClaim, facts: Facts):
    stage = facts.stage
    missing = _not_cast(stage, claim.actor)
    if missing is not None:
        return missing
    if claim.kind == "reaches":
        answer = reach(stage, claim.actor, claim.target)
    elif claim.kind == "sees":
        answer = sees(stage, claim.actor, claim.target)
    else:
        answer = hears(stage, claim.actor, claim.target, claim.channel)
    if answer.reason in _LABEL_ERRORS or answer.reason == "not_actor":  # 041 answers is_self with ok=True
        return _Unchecked(f"bad_label:{answer.reason}")
    if answer.ok:
        return None
    if answer.reason == "untracked":
        return _Unchecked("untracked")
    return _Problem(answer.reason, _staging_evidence(stage, claim.actor, claim.target), answer.assumed)


# -- possessions --------------------------------------------------------------------------------------------

def _custody(facts: Facts, holder):
    """The holders of ``holder`` up through 034 custody, or ``None`` when the chain loops or runs too deep."""
    chain, seen = [holder], {holder}
    for _ in range(_CUSTODY_DEPTH + 1):  # up to _CUSTODY_DEPTH containers, then the holder's own lookup
        container = facts.possession(holder)
        if container is None:
            return chain
        holder = container.state.holder
        if holder in seen:
            return None
        chain.append(holder)
        seen.add(holder)
    return None


def _carried_stacks(facts: Facts, actor: InstanceRef, item: str) -> tuple[int, list[Evidence]]:
    """Stacks of ``item`` held by something the actor carries (draughts in her satchel), per 034 custody."""
    total, found = 0, []
    for holdings in facts.holdings:
        if holdings.holder == actor or not holdings.stacks.get(item, 0):
            continue
        chain = _custody(facts, holdings.holder)
        if chain is not None and actor in chain[1:]:
            total += holdings.stacks[item]
            found.append(Evidence("holdings", f"{holdings.holder}#{item}", f"count={holdings.stacks[item]}:carried"))
    return total, found


def _check_spends(claim: ContinuityClaim, facts: Facts, before: int = 0, doubtful: int = 0):
    """``before`` is what earlier sure claims spent of the stack; ``doubtful`` what earlier unsure ones did."""
    holdings = facts.holdings_of(claim.actor)
    if holdings is None:
        return _Unchecked("no_holdings")
    have, needs = holdings.stacks.get(claim.item, 0), before + doubtful + claim.count
    if have >= needs:
        return None
    found = [Evidence("holdings", f"{claim.actor}#{claim.item}", f"count={have}:needs={needs}")]
    if before or doubtful:
        found.append(Evidence("narration", f"spent_before:{claim.item}", f"count={before}:unsure={doubtful}"))
    spent = next((entry for entry in reversed(holdings.log)
                  if any(line.item == claim.item and line.holder == claim.actor and line.kind in ("consume", "move")
                         for line in entry.lines)), None)
    if spent is not None:
        found.append(Evidence("holdings", f"op:{spent.op}", f"after={spent.after.get(claim.item, 0)}"))
    carried, cited = _carried_stacks(facts, claim.actor, claim.item)
    # Sure only when even the sure spends alone, against every stack the actor carries, run short.
    return _Problem("insufficient", found + cited, sure=have + carried < before + claim.count)


def _check_wields(claim: ContinuityClaim, facts: Facts):
    record = facts.possession(claim.target)
    if record is None:
        return _Unchecked("no_possession")
    chain = _custody(facts, record.state.holder)
    if chain is None:  # inconsistent records prove nothing either way
        return _Unchecked("custody_unreadable")
    if claim.actor in chain:
        return None
    found = [Evidence("possession", f"{claim.target}@rev{record.rev}", f"holder={record.state.holder}")]
    found += [Evidence("possession", f"{container}", f"holder={facts.possession(container).state.holder}")
              for container in chain if facts.possession(container) is not None]
    if record.log:
        last = record.log[-1]
        found.append(Evidence("possession", f"op:{last.op}", f"holder={last.after.holder}"))
    return _Problem("not_holder", found)


# -- knowledge ----------------------------------------------------------------------------------------------

def _held(view) -> frozenset[str]:
    """027's ``known_ids`` rule: reports received or sent, and the actor's own beliefs, current or not."""
    return frozenset({item.proposition_id for item in view.evidence if item.proposition_id is not None}
                     | {report.proposition_id for report in view.sent}
                     | {belief.proposition_id for belief in (*view.current, *view.history)})


def _items(view) -> tuple[str, ...]:
    return tuple(sorted({item.evidence_id for item in view.evidence} | {report.evidence_id for report in view.sent}
                        | {belief.belief_id for belief in (*view.current, *view.history)}))


def _check_knows(claim: ContinuityClaim, facts: Facts):
    knowledge = facts.knowledge
    if knowledge is None:
        return _Unchecked("no_knowledge")
    try:
        proposition = knowledge.proposition(claim.proposition_id)
    except KnowledgeError:
        return _Unchecked("unknown_proposition")
    view = actor_knowledge(knowledge, claim.actor, as_of=facts.as_of)
    if claim.proposition_id in _held(view):
        return None
    cited = Evidence("knowledge", f"proposition:{claim.proposition_id}",
                     f"standing={standing(knowledge, claim.proposition_id, as_of=facts.as_of)}")
    if (claim.actor, claim.proposition_id) in facts.heard:
        return _Problem("heard_unadmitted", (cited, Evidence("knowledge", f"actor:{claim.actor}", "heard_this_turn")),
                        sure=False)
    unconfirmed = sorted(item.evidence_id for item in knowledge.evidence_held_by(claim.actor)
                         if not item.confirmed and item.moment.sequence <= facts.as_of
                         and getattr(item, "proposition_id", None) == claim.proposition_id)
    if unconfirmed:  # an overheard or unconfirmed line: not knowledge yet, and not a secret either
        return _Problem("unconfirmed_evidence", (cited, *(Evidence("knowledge", f"evidence:{item}", "unconfirmed")
                                                          for item in unconfirmed)), sure=False)
    related: set[str] = set()
    for subject in proposition.about:
        related.update(_items(knowledge_about(knowledge, claim.actor, subject, as_of=facts.as_of)))
    if related:
        return _Problem("related_evidence", (cited, *(Evidence("knowledge", f"evidence:{item}", "about_a_subject")
                                                      for item in sorted(related))), sure=False)
    return _Problem("never_told", (cited, Evidence("knowledge", f"actor:{claim.actor}",
                                                   f"holds_nothing_of_it:as_of={facts.as_of}")))


# -- outcomes -----------------------------------------------------------------------------------------------

def _check_resolves(claim: ContinuityClaim, facts: Facts):
    recorded = facts.results(claim.attempt_id)
    if recorded is None:
        return _Unchecked("no_resolution")
    cited = tuple(Evidence("resolution", f"attempt:{claim.attempt_id}", f"result={result}")
                  for result in sorted(recorded))
    if len(recorded) > 1:
        return _Problem("conflicting_resolutions", cited, sure=False)
    if claim.result in recorded:
        return None
    return _Problem("contradicts_resolution", cited)


def _bearing(fact, approach_id: str) -> str | None:
    bearings = dict(fact.bearings)
    return bearings.get(approach_id, bearings.get("*"))


def _check_overcomes(claim: ContinuityClaim, facts: Facts):
    state = facts.obstacle(claim.obstacle_id)
    if state is None:
        return _Unchecked("no_obstacle")
    cited = [Evidence("obstacle", f"obstacle:{claim.obstacle_id}@rev{state.revision}", f"status={state.status}")]
    cited += [Evidence("obstacle", f"attempt:{a.attempt.attempt_id}", f"{a.attempt.approach.approach_id}:{a.status}")
              for a in state.attempts]
    if state.status == "diverged":
        return _Problem("diverged", cited, sure=False)
    if state.status == "overcome":
        winner = next((a for a in state.attempts if a.attempt.attempt_id == state.overcome_by), None)
        if winner is None or winner.frame is None:
            return _Unchecked("no_winning_frame")
        if winner.frame.actor != claim.actor:
            return _Problem("other_actor", cited + [Evidence("obstacle", f"attempt:{winner.attempt.attempt_id}",
                                                            f"actor={winner.frame.actor}")])
        if claim.approach_id is not None and winner.attempt.approach.approach_id != claim.approach_id:
            return _Problem("other_approach", cited)
        return None
    if claim.approach_id is not None:
        prevents = [fact for fact in state.facts if _bearing(fact, claim.approach_id) == "prevents"]
        if prevents:
            return _Problem("prevented", cited + [Evidence("obstacle", f"fact:{fact.fact_id}", f"{fact.kind}:prevents")
                                                  for fact in prevents])
    return _Problem("not_overcome", cited)


_CHECKS = {
    "present": ("stage", _check_present), "reaches": ("stage", _check_staging), "sees": ("stage", _check_staging),
    "hears": ("stage", _check_staging), "spends": (None, _check_spends), "wields": (None, _check_wields),
    "knows": (None, _check_knows), "resolves": (None, _check_resolves), "overcomes": (None, _check_overcomes),
}


def _classify(claim: ContinuityClaim, problem: _Problem) -> str:
    if claim.mode == "figurative":
        return "metaphor"
    if claim.mode == "unreliable":
        return "unreliable"
    if not (problem.sure and claim.certain) or problem.assumed:
        return "uncertain"
    return "contradiction"


def _anchored(text: str, span: tuple[int, int], quote: str) -> bool:
    return span[1] <= len(text) and text[span[0]:span[1]] == quote


def referee(text: str, *, facts: Facts, claims: Iterable[ContinuityClaim] = (),
            authorship: Iterable[LabelledClaim] = (), authority: Authority | None = None) -> RefereeReport:
    """Review one proposed narration's labelled claims against ``facts``; the narration is left as it is.

    ``claims`` are continuity claims; ``authorship`` are 003 authorship claims, reviewed with ``authority``
    (which they need) by ``review_narration`` against ``facts.resolutions``, and routed to mislabel review
    where a player character's words are carried by a claim about someone else or by an outcome.
    """
    text = check_text(text)
    if type(facts) is not Facts:
        raise RefereeError("invalid_field")
    claims, authorship = _tuple(claims, ContinuityClaim), _tuple(authorship, LabelledClaim)
    if len(claims) + len(authorship) > MAX_CLAIMS:
        raise RefereeError("too_many_claims")
    ids = [claim.claim_id for claim in claims] + [item.claim.claim_id for item in authorship]
    if len(ids) != len(set(ids)):
        raise RefereeError("duplicate_id")
    if any(claim.campaign_id != facts.campaign_id for claim in claims) or \
            any(item.claim.campaign_id != facts.campaign_id for item in authorship):
        raise RefereeError("campaign_mismatch")
    if authorship and (authority is None or type(authority) is not Authority
                       or authority.context.campaign_id != facts.campaign_id):
        raise RefereeError("needs_authority")

    findings, clean, unchecked = [], [], []
    spent: dict[tuple, int] = {}  # (actor, item, certain): what the narration already spent, in reading order
    for claim in sorted(claims, key=lambda claim: (claim.span, claim.claim_id)):
        needs, check = _CHECKS[claim.kind]
        if not _anchored(text, claim.span, claim.quote):
            unchecked.append((claim.claim_id, "bad_anchor"))
            continue
        if needs == "stage" and facts.stage is None:
            unchecked.append((claim.claim_id, "no_stage"))
            continue
        try:
            if claim.kind == "spends":
                key = (claim.actor, claim.item, claim.certain)
                outcome = check(claim, facts, spent.get((*key[:2], True), 0), spent.get((*key[:2], False), 0))
                if claim.mode == "literal":
                    spent[key] = spent.get(key, 0) + claim.count
            else:
                outcome = check(claim, facts)
        except (StagingRefused, KnowledgeError):  # a reference the records cannot take: never a pass
            unchecked.append((claim.claim_id, "unreadable"))
            continue
        if outcome is None:
            clean.append(claim.claim_id)
        elif isinstance(outcome, _Unchecked):
            unchecked.append((claim.claim_id, outcome.reason))
        else:
            findings.append(Finding(claim.claim_id, claim.category, claim.kind, _classify(claim, outcome),
                                    outcome.reason, claim.span, claim.quote, outcome.evidence, outcome.assumed))

    review, routed = None, ()
    if authorship:
        anchored = tuple(sorted((item for item in authorship if _anchored(text, item.span, item.quote)),
                                key=lambda item: (item.span, item.claim.claim_id)))
        unchecked += [(item.claim.claim_id, "bad_anchor") for item in authorship if item not in anchored]
        try:
            review = review_narration(authority.context, (item.claim for item in anchored), authority.controls,
                                      authority.contributions, authority.delegations, authority.revocations,
                                      authority.conditions, facts.resolutions)
        except (AuthorityError, ValueError):  # 003's messages name actors: carry the reason only
            raise RefereeError("invalid_authority") from None
        routed = mislabels(anchored, authority.controls)
    findings.sort(key=lambda finding: (finding.span, finding.claim_id))
    return RefereeReport(hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest(), tuple(findings), tuple(sorted(clean)),
                         tuple(sorted(unchecked)), review, routed)


__all__ = ["referee"]
