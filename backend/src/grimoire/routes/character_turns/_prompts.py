"""Each assigned response composes or replays its frozen prompt and captures the target actually sent."""

from __future__ import annotations

import dataclasses
import json

from ... import decisions, store, wire
from ...llm import fallback_sampling
from ...model_guidance import PreparedMessages
from .. import decision_capture
from ..common import _record_prompt, model_window
from . import _planning, _state
from ._router import router as router


def _compose(cid, sid, round_record, actor, resolved, appended=(), plan=None,
             history_view=None):
    # The prompt must offer the same remaining slots that the engine accepts.
    # Offering the current/used actors teaches an invalid handoff; omitting the
    # narrator hides a valid slot. Explicit one-response requests have no next,
    # and neither does a List/Natural/Manual round: its plan decides, and any
    # handoff block it writes is ignored. With automatic rounds remaining, a
    # Directed handoff to someone who already spoke this round is valid -- it
    # ends the round and that character leads the next (`_successor`) -- so
    # only the current actor is withheld.
    offer = round_record["automatic"] and round_record.get("mode", "directed") == "directed"
    if offer and round_record.get("auto_remaining", 0) > 0:
        used = {actor}
    else:
        used = {*round_record["used"], actor}
    pool = _planning._handoff_pool(cid, sid, round_record) if offer else []
    # Whoever already spoke is marked, so the handoff text can say what
    # naming them does instead of claiming they are excluded.
    candidates = [{**entry, "responded": entry["ref"] in round_record["used"]}
                  for entry in [*pool, {"ref": "grimoire", "name": "Grimoire"}]
                  if offer and entry["ref"] not in used]
    kwargs = {
        "turn": round_record.get("turn"),
        "actor_ref": actor,
        "eligible_speakers": candidates,
        "successor_by_decision": _planning._decision_successor(round_record),
        "describe": store.prompt_log.capturing(),
        "model": resolved.chain.primary.model,
        "images": store.post_images.images_for(resolved.chain.primary),
        "history_view": history_view,
    }
    if appended:
        kwargs["appended"] = appended
    if plan:
        # A play decision's record (roadmap 02 §6.5): its block follows the
        # history. Absent, the call is exactly what it was before 02.
        kwargs["plan"] = plan
    if round_record.get("note"):
        return store.context.compose_director_turn(cid, sid, round_record["note"], **kwargs)
    return store.context.compose_turn(cid, sid, **kwargs)


def _stored_view(cid, sid, run, stored):
    """The history view a pending response was composed with (spec 11
    §5.5), for its roll resume, or None. Bound to the scene's sid now: a
    rename between the pause and the resume moves the sid, never the
    identity, and a view for another identity is no view at all (compose
    would refuse it)."""
    view = store.context.epistemic.from_record(
        (stored.get("snapshot") or {}).get("history_view"))
    if view is None:
        return None
    identity = getattr(run, "scene_identity", None) or store.scenes.identity.scene_identity(
        cid, sid)
    if not identity or view.perspective.identity != identity \
            or view.perspective.cid != cid:
        return None
    return dataclasses.replace(
        view, perspective=dataclasses.replace(view.perspective, sid=sid))


def _prepare(claim, round_record, actor, resolved, appended, *, plan=None,
             response_id="", history_view=None, epistemic=None):
    """Compose (or resume) the contribution and record it.

    `plan` is the turn decision's record asked for THIS contribution
    (`_turn_plan`), stored on the new response as `intent` and composed into
    its prompt; `response_id` is the id that decision was metered under. A roll
    resume asks nothing: it recomposes with the record the pending response
    already holds (roadmap 02 §3.1 rule 5), so the resume snapshot -- and Keep
    writing after it -- carries the same block. A retry replays its snapshot,
    which already carries it.

    `history_view` rides the snapshot (spec 11 §5.5), so a roll resume in a
    later request -- which holds no view of its own -- recomposes with the
    one the response was composed with, and asks nothing again; `epistemic`
    is the Decision stage's record, stored beside `intent`."""
    cid, sid = claim.cid, claim.sid
    with store.locks.campaign_lock(cid):
        claim.fence()
        pending = round_record.get("pending_response")
        if pending:
            record = store.responses.get(cid, sid, pending, private=True)
            if record["status"] != "complete" and not appended:
                messages = PreparedMessages.from_snapshot(
                    record.get("resume_snapshot") or record["snapshot"],
                    resolved.chain.primary.model, campaign=cid,
                )
                _capture(
                    cid,
                    sid,
                    "continuation" if round_record.get("continuation") else "retry",
                    messages,
                    resolved.chain,
                )
                if record.get("resume_snapshot"):
                    return record, messages, "resume", record.get("resume_settings")
                return record, messages, "primary", None
        if pending and appended:
            stored = store.responses.get(cid, sid, pending, private=True)
            plan = stored.get("intent")
            if history_view is None:
                history_view = _stored_view(cid, sid, claim.run, stored)
        messages, _breakdown = _compose(cid, sid, round_record, actor, resolved, appended,
                                        plan=plan, history_view=history_view)
        # The round's eligible first, then the whole present cast: an explicit
        # pick a talkativeness roll filtered out is still who they are.
        speaker = next(
            (r["name"] for r in [*round_record["eligible"], *_state.roster(cid, sid)]
             if r["ref"] == actor),
            "Grimoire",
        )
        if pending and appended:
            record = store.responses.get(cid, sid, pending, private=True)
            store.responses.save_resume_prompt(
                cid, sid, pending, messages.snapshot(), getattr(messages, "settings", None)
            )
            # The original pre-response snapshot remains the reroll boundary.
        else:
            snapshot = messages.snapshot()
            if history_view is not None:
                snapshot["history_view"] = store.context.epistemic.to_record(history_view)
            record = store.responses.prepare(
                cid, sid, round_record["id"], actor, speaker, snapshot,
                getattr(messages, "settings", None),
                **({"response_id": response_id} if response_id else {}),
                **({"intent": plan} if plan is not None else {}),
                **({"epistemic": epistemic} if epistemic is not None else {}),
            )
        _capture(cid, sid, "continuation" if appended else "chat", messages, resolved.chain)
        if pending and appended:
            return record, messages, "resume", getattr(messages, "settings", None)
        return record, messages, "primary", None


#: The prompt-log section a decision's outcome is filed under -- defined in
#: `decision_capture`, which files every decision now, and kept here for the
#: callers (and the pin in `test_character_turns`) that name it from here.
OUTCOME_SECTION_ID = decision_capture.OUTCOME_SECTION_ID


def _capture(cid, sid, task, messages, sent: wire.Chain | wire.Target,
             outcome: dict | None = None, *, vouched: bool = True):
    """Record one call's prompt in the prompt log. `outcome` is a decision's
    record of what it decided (`inference.Capture`), filed as a last
    `decision` section beside the messages built from plain ones, and left
    out of `total_tokens`: it was never sent.

    `sent` is what the call was sent on: a generation's chain -- whose
    fallback, should it answer, is captured as the variant it was sent
    (`_variant_target`) -- or one attempt's `wire.Target` (a decision's, or
    that fallback variant's own). A native decision is sent no sampler
    preset, so its capture reports none (`outcome`'s `mode`); nor does a
    variant whose sampling nobody can vouch for (`vouched`, `_variant_target`'s
    relabel)."""
    if not store.prompt_log.capturing():
        return
    # A prompt that carries its own breakdown (`PreparedMessages`) is filed
    # as it is, and `outcome` is dropped: `decide` never hands one over (its
    # messages are plain), so only the plain branch below files a decision.
    breakdown = getattr(messages, "breakdown", None)
    if breakdown is None:
        # The plain sections a decision capture builds too, one definition.
        rows = decision_capture.message_sections(messages)
        total = sum(row["tokens"] for row in rows)
        if outcome is not None:
            rows.append({"id": OUTCOME_SECTION_ID, "label": "decision",
                         "text": json.dumps(outcome, indent=2, ensure_ascii=False),
                         "tier": "lock-in", "dropped": False, "pinned": False,
                         "trimmed": 0, "tokens": 0})
        breakdown = {
            "sections": rows,
            "total_tokens": total,
            "dropped_tokens": 0,
            "budget_tokens": store.context.budget_tokens(cid),
        }
    # The window of the attempt this names (01i), here as well as in
    # `_record_prompt`, which is handed no `conn` for a native decision or an
    # unvouched variant.
    breakdown = {**breakdown, "model_window": model_window(
        sent if isinstance(sent, wire.Target) else sent.primary)}
    if isinstance(sent, wire.Target):
        # One attempt: a decision, or a fallback variant (below), whose
        # messages are a plain list -- there is no variant of it to hook.
        native = (outcome or {}).get("mode") == decisions.NATIVE_BACKEND
        _record_prompt(cid, sid, task, breakdown, model=sent.model, kind=sent.kind,
                       messages=messages, conn=sent if vouched and not native else None)
        return
    primary = sent.primary
    _record_prompt(cid, sid, task, breakdown, model=primary.model, kind=primary.kind,
                   messages=messages, conn=sent)
    if isinstance(messages, PreparedMessages):
        # Steered frozen variants have no historical section accounting. Capture
        # their exact rendered messages at actual fallback dispatch instead.
        # `for_target`: a tailed prompt (Keep writing) ends the way the
        # fallback's own attempt was sent; for any other it is `for_model`.
        def capture_variant(model, _breakdown):
            variant = _variant_target(sent, model)
            _capture(cid, sid, task, messages.for_target(variant), variant,
                     vouched=sent.fallback is not None)
        messages.on_variant = capture_variant


def _variant_target(chain: wire.Chain, model: str) -> wire.Target:
    """The attempt a fallback variant was sent on, as its capture names it.

    The fallback this call carries (`wire.Chain.fallback`, the resolver's
    own), with the route's sampler preset under `llm.fallback_sampling`'s
    rule -- not the primary relabelled with the fallback's model, which would
    record the primary's preset and catalog list against a request that never
    carried them. Falls back to that relabel only when the call carries no
    fallback (a chain built by hand), minus the sampling and the catalog list
    it cannot vouch for -- and its capture files no sampler report
    (`_capture`'s `vouched`).
    """
    if chain.fallback is not None:
        return dataclasses.replace(fallback_sampling(chain.primary, chain.fallback),
                                   model=model, requested_model=model)
    return dataclasses.replace(chain.primary, model=model, requested_model=model,
                               sampling=wire.Sampling(), model_params=None)
