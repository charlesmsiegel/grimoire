"""047 prose extraction, deterministic review and explicit proposal acceptance.

Only this independent extraction call sees the director's fact vocabulary. Its results never become
an actor's memories, a player contribution or a resolution. Keeping an exception keeps prose only.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace

from grimoire.core.authority import CONTRIBUTION_DOMAINS
from grimoire.core.inference import InferenceStage, OptionalRequest
from grimoire.narration_review import ExtractionRuntime, Vocabulary, extraction_messages, parse_extraction
from grimoire.providers.cancel import CancelToken
from grimoire.referee import referee
from grimoire.runtime.fence import cancel_when_stale
from grimoire.scene import CallRecord
from grimoire.scene.codec import turn_to_payload
from grimoire.scene.review import LineReview, review_to_payload
from grimoire.security import Permission
from grimoire.store.layout import address_parts
from .continuity_facts import authority_for, canonical, digest, snapshot
from .scene import _refuse

PHASE = "continuity"
PURPOSE = "extract narration claims"
HELD_STATES = frozenset({"needs_review", "failed", "unchecked", "stale"})


def planned(runner, plan):
    if not runner.policy.enabled(InferenceStage.CRITIQUE):
        return plan
    if runner.extractor is None or runner.extract_handle is None:
        raise _refuse("no_extractor", "enabled continuity review needs an independent extractor")
    if (not isinstance(runner.extractor, ExtractionRuntime) or not runner.authority.issued(runner.extract_handle)
            or getattr(runner.extract_handle, "trusted", None) is not False):
        raise _refuse("bad_extractor", "continuity extraction requires independent issued untrusted handles")
    reserved = {getattr(getattr(runner.runtime, "_handle", None), "principal", None),
                getattr(runner.adjudication, "principal", None)}
    if runner.extract_handle.principal in reserved or runner.extractor._handle.principal in reserved:
        raise _refuse("bad_extractor", "the extractor cannot use a narrator or adjudicator identity")
    return plan.add_phase(PHASE, optional_requests=(OptionalRequest(InferenceStage.CRITIQUE, None, PURPOSE),))


def token_for(record, take) -> str:
    return digest({"chat": str(record.chat), "turn": record.turn_id, "take": take.take,
                   "snapshot": take.snapshot_fingerprint,
                   "lines": [{"id": line.line_id, "text": line.text, "review": line.review,
                              "detail": None if line.review_detail is None else review_to_payload(line.review_detail)}
                             for line in take.lines]})


def _vocabulary(captured, scene):
    facts = captured.facts
    items = {key for holding in facts.holdings for key in holding.stacks}
    # Spent-out item keys remain meaningful vocabulary: zero must not make the key disappear.
    for holding in facts.holdings:
        for entry in holding.log:
            items.update(line.item for line in entry.lines)
    return Vocabulary(facts.campaign_id, tuple(scene.cast), captured.targets, items=tuple(sorted(items)),
                      propositions=tuple((p.proposition_id, p.text) for p in facts.knowledge.propositions),
                      attempts=tuple((r.attempt_id, r.result) for r in facts.resolutions),
                      obstacles=tuple(o.obstacle.obstacle_id for o in facts.obstacles),
                      tags=tuple(sorted({tag for choice in facts.intentions for goal in choice.agenda.goals
                                         for approach in goal.approaches for tag in approach.tags})),
                      names=captured.names,
                      approaches=tuple(sorted({a.approach_id for o in facts.obstacles for a in o.obstacle.approaches})))


def _failed(take, reason):
    return replace(take, lines=tuple(line if line.review == "blocked" else replace(line, review="failed",
                   review_detail=LineReview.make(line.text, reason=reason)) for line in take.lines))


def extract(runner, record, take, meter, full_plan, captured, scene, fence):
    """Exactly one independently executed call; every exit retains its booked attempts."""
    meter.extend(full_plan)
    calls = [call for call in full_plan.calls if call.stage is InferenceStage.CRITIQUE and call.call_id.phase == PHASE]
    if not calls:
        return take
    (call,) = calls
    status, reason = "not_issued", "no_prose"
    result = replace(take, snapshot_fingerprint=captured.fingerprint)
    if take.lines and take.state == "proposed" and not fence.stale:
        cancel = CancelToken()
        unwatch = cancel_when_stale(fence, cancel)
        try:
            vocab = _vocabulary(captured, scene)
            texts = {line.line_id: line.text for line in take.lines}
            reply = runner.extractor.extract(meter, call, extraction_messages(texts, vocab), cancel=cancel)
            status, reason = reply.status, reply.reason
            if status == "decoded":
                labels = parse_extraction(reply.decoded, texts, vocab)
                by_id = {label.line_id: label for label in labels}
                authority = authority_for(record, scene)
                offsets, claims, authorship_labels = {}, [], []
                offset = 0
                for line in take.lines:
                    offsets[line.line_id] = offset
                    label = by_id[line.line_id]
                    claims.extend(replace(claim, span=(claim.span[0] + offset, claim.span[1] + offset))
                                  for claim in label.claims)
                    for anchored in label.authorship:
                        claim = anchored.claim
                        for contribution in authority.contributions:
                            if (claim.subject_id == contribution.actor_id
                                    and claim.domain in CONTRIBUTION_DOMAINS[contribution.kind]
                                    and claim.text.strip() == contribution.text.strip()):
                                claim = replace(claim, expresses=contribution.contribution_id)
                                break
                        authorship_labels.append(replace(anchored, claim=claim,
                                                         span=(anchored.span[0] + offset, anchored.span[1] + offset)))
                    offset += len(line.text) + 1
                # One take is one narration: cumulative possessions must not reset at a speaker boundary.
                report = referee("\n".join(line.text for line in take.lines), facts=captured.facts,
                                 claims=claims, authorship=authorship_labels, authority=authority)
                rejected = () if report.authorship is None else report.authorship.rejected
                reviewed = []
                for line in take.lines:
                    label, offset = by_id[line.line_id], offsets[line.line_id]
                    ids = {claim.claim_id for claim in label.claims} | {item.claim.claim_id for item in label.authorship}
                    findings = tuple(replace(item, span=(item.span[0] - offset, item.span[1] - offset))
                                     for item in report.findings if item.claim_id in ids)
                    routed = tuple(replace(item, span=(item.span[0] - offset, item.span[1] - offset))
                                   for item in report.mislabels if item.claim_id in ids)
                    denied = tuple(item for item in rejected if item.claim.claim_id in ids)
                    authorship_spans = {item.claim.claim_id: item.span for item in label.authorship}
                    authorship = [{"claim": item.claim.claim_id, "reason": item.reason,
                                   "quote": item.claim.text, "span": authorship_spans[item.claim.claim_id]}
                                  for item in denied]
                    unchecked = [item for item in report.unchecked if item[0] in ids]
                    if captured.stage_unavailable and any(c.kind in ("present", "reaches", "sees", "hears")
                                                          for c in label.claims):
                        unchecked.append(("stage", "stale_stage"))
                    state = ("blocked" if denied or routed else
                             "unchecked" if not label.complete else
                             "needs_review" if unchecked or any(f.classification in ("contradiction", "uncertain")
                                                                for f in findings) else "reviewed")
                    detail = LineReview.make(line.text, complete=label.complete,
                                             findings=canonical(findings), unchecked=unchecked,
                                             authorship=authorship, mislabels=canonical(routed))
                    reviewed.append(replace(line, review=state, review_detail=detail))
                    result = replace(result, lines=tuple(reviewed) + take.lines[len(reviewed):])
                result = replace(result, lines=tuple(reviewed))
                status, reason = "accepted", None
            else:
                result = _failed(result, reason or status)
                if status == "unparseable":
                    status = "rejected"
        except Exception:
            # Neither provider content nor a hidden fact is copied into exception diagnostics.
            status, reason = "rejected", "invalid_extraction"
            result = _failed(result, reason)
        finally:
            unwatch()
    attempts = tuple(a for a in meter.ledger.attempts if a.call_id == call.call_id)
    recorded = CallRecord(call.call_id.phase, call.stage, call.call_id.actor_id, call.call_id.ordinal,
                          status, reason, attempts)
    result = replace(result, calls=tuple(c for c in take.calls if c.phase != PHASE) + (recorded,))
    return replace(result, review_token=token_for(record, result))


def current_fingerprint(runner, chat):
    return snapshot(runner, runner._context(chat)).fingerprint


def unchanged(runner, chat, fingerprint):
    """Unreadable current facts are not fresh; a billed call must still land its accounting."""
    try:
        return current_fingerprint(runner, chat) == fingerprint
    except Exception:
        return False


def stale(take):
    return replace(take, lines=tuple(line if line.review == "blocked" else
                                    replace(line, review="stale") for line in take.lines))


def proposable(runner, record) -> bool:
    payload = json.dumps(turn_to_payload(record), sort_keys=True, separators=(",", ":")).encode()
    return runner.extract_handle.authorize(Permission.PROPOSE, "/".join(address_parts(record.ref)), payload).allowed


def prepare_keep(runner, record, take, principal, acknowledge, turn, *, lines=None, aggregate=False):
    """Check proposal identity/authority; final freshness is also checked inside the commit lock."""
    chosen = tuple(take.shown() if lines is None else lines)
    if any(line.review == "blocked" for line in chosen):
        raise _refuse("authorship_blocked", "the proposal carries protected player authorship or a possible mislabel")
    if take.snapshot_fingerprint is not None:
        if not unchanged(runner, record.chat, take.snapshot_fingerprint):
            raise _refuse("review_stale", "continuity facts changed; generate a fresh reviewed take")
        if take.review_token != token_for(record, take):
            raise _refuse("review_changed", "the reviewed proposal changed")
    held = any(line.review in HELD_STATES or (aggregate and line.review != "unlabelled") for line in chosen)
    if held:
        if principal not in runner.authority.owners:
            raise _refuse("not_owner", "only the owner may accept a continuity exception")
        if turn != record.turn_id or acknowledge != take.review_token or acknowledge is None:
            raise _refuse("review_required", "review this exact proposal and acknowledge its review token")
    elif acknowledge is not None and (turn != record.turn_id or acknowledge != take.review_token):
        raise _refuse("review_changed", "the acknowledgment names another proposal")
    kept = []
    for line in chosen:
        if aggregate and line.review_detail is not None:
            detail = line.review_detail
            report = detail.report()
            report["reason"] = "aggregate_unchecked"
            kept.append(replace(line, review="exception", review_detail=LineReview(
                detail.source_sha256, False, report)))
        else:
            kept.append(replace(line, review="exception") if line.review in HELD_STATES else line)
    return tuple(kept)


def report_view(runner, chat, by, turn, take):
    scene = runner._context(chat).scene
    principal = runner._principal(scene, by)
    if principal not in runner.authority.owners:
        raise _refuse("not_owner", "continuity evidence is director-side")
    record = next((record for record in scene.records if record.turn_id == turn), None)
    found = None if record is None else next((item for item in record.takes if item.take == take), None)
    if found is None:
        raise _refuse("unknown_take", "that take is not on this chat")
    return {"chat": str(chat), "turn": turn, "take": take, "review_token": found.review_token,
            "snapshot_fingerprint": found.snapshot_fingerprint,
            "lines": [{"line": line.line_id, "review": line.review,
                       "detail": None if line.review_detail is None else review_to_payload(line.review_detail)}
                      for line in found.lines]}


class ReviewStale(Exception):
    pass


class ReviewDenied(Exception):
    pass
