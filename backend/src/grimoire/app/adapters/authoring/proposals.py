"""087's world authoring proposals on the Authoring page: ``authoring.build``, ``authoring.suggest_plan``,
``authoring.suggest``, ``authoring.review``, ``authoring.draft``, ``authoring.apply`` and ``authoring.reject``.

**A proposal** is built by hand (``build``: candidates and links the person typed, links to existing records only by
a pinned ref ``context(source)`` offers, exactly as 087's ``parse_proposal`` checks a model's reply; its origin is
the library owner) or suggested by the model (``suggest_plan`` and ``suggest``; origin ``model:planning``). Either
way the proposal is kept **here, in the app's memory**, under the proposal id the server chose: the page names it
and never sends a proposal's content back, so a client cannot present its own candidates as the model's. 087 keeps
proposals in memory only: a restart loses every proposal that was not accepted.

**The one call.** Off unless the model settings' ``authoring`` is 1 (Models and costs, *World suggestions*); off,
the plan answers ``not_enabled`` and ``suggest`` refuses it. ``suggest_plan`` builds the exact messages 087 would
send (``authoring_messages`` over the world's ``context``) and answers the app's estimate (as Prep's scene
preparation estimates: the prompt counted as its bytes, the reply cap and the configured prices) with a review
token over the request, the context, the model settings and the estimate. Nothing is sent. ``suggest`` takes that
token, builds everything again, refuses ``changed`` if anything moved, and only then runs 087's
``AuthoringGenerator``: one optional ``planning`` call under the settings' authoring policy, through 013's adapter to
the configured endpoint, on an untrusted handle of its own (``app:world-author``) whose every attempt is approved for
its exact payload while that one call is made. The call is one 048 trace, ``world_authoring``. The same request id
asked the same again answers the proposal it gave and sends nothing; asked differently it is ``request_conflict``.

**Review, decide, write.** ``review`` is 087's ``review`` (duplicates, link problems; writes nothing). ``draft``
turns the person's decision (candidates accepted, flagged duplicates kept ``distinct``) into 087's
``AcceptanceDraft``: the exact change set, every file it writes and its preconditions, shown byte for byte, and a
digest of it. It writes nothing; a world that moved since the review refuses ``stale_review`` and the page reviews
again. ``apply`` takes the digest the person approved and applies exactly that draft through 043 (``conflict`` if the
world moved since the draft: nothing is written); retried, it answers what it wrote and writes nothing again.
``reject`` ends the proposal and writes nothing. Brainstorms and unaccepted candidates are never written.
"""

from __future__ import annotations

import hashlib
import json
import uuid
import weakref
from collections.abc import Mapping
from dataclasses import dataclass
from typing import NamedTuple

from grimoire.app.adapter import AdapterContext
from grimoire.app.config import ConfigError, ModelConfig, authoring_policy
from grimoire.authoring import (
    MAX_CANDIDATES,
    AuthoringContext,
    AuthoringError,
    AuthoringGenerator,
    AuthoringRequest,
    Decision,
    Proposal,
    authoring_messages,
    parse_proposal,
)
from grimoire.authoring.model import BRIEF_MAX, MAX_CONSTRAINTS, TEXT_MAX
from grimoire.authoringstore import AcceptanceDraft, AuthoringLibrary
from grimoire.changesets import ChangeSetError
from grimoire.changesetstore.library import ChangeSetConflict
from grimoire.core.identity import SourceRef
from grimoire.play.tracing import record_calls, traced
from grimoire.providers import EnvironmentCredentials, ProviderRegistry, RouteChoice
from grimoire.providers.adapter import ProviderAdapter
from grimoire.providers.transport import HttpTransport
from grimoire.reviewapi.views import file_views
from grimoire.security import (
    Approval,
    Authorizer,
    Grant,
    Permission,
    ResourcePath,
    payload_digest,
)
from grimoire.store import WriteDenied
from grimoire.textapi import ApiRefused

from .. import providers
from ..prep.preparation import (  # the app's one estimate and usage of a call
    _estimate,
    _usage,
)
from .common import (
    MAX_PROPOSALS,
    keys_of,
    memory,
    reason_of,
    refused,
    segment,
    source_of,
    text_of,
    whole,
    world_only,
)

PRINCIPAL = "app:world-author"
#: The 048 trace operation a suggestion's call is recorded under.
OPERATION = "world_authoring"
HAND_BRIEF = "Records built by hand on the Authoring page."
SAVED = ("Nothing here is stored until you accept it: a proposal is kept in this app's memory only, and a restart "
         "loses it. Only the candidates you accept are written, as one reviewed change set; a brainstorm never is.")

_libraries: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


class _Probe:
    """What 048's ``traced`` needs of a runner: the app runner's tracer and clock. The call is one trace,
    ``world_authoring`` (as Prep's is ``scene_preparation``)."""

    def __init__(self, tracer, clock) -> None:  # type: ignore[no-untyped-def]
        self.tracer = tracer
        self.clock = clock

    @traced(OPERATION)
    def run(self, step):  # type: ignore[no-untyped-def]
        return step()


def library(ctx: AdapterContext) -> AuthoringLibrary:
    """The open library's one 087 service: a draft is applied (or rejected) through the change-set service that
    built it, so one is kept per text API, over the owner's trusted handle."""
    found = _libraries.get(ctx.api)
    if found is None or found.store is not ctx.store:
        found = _libraries[ctx.api] = AuthoringLibrary(ctx.store, ctx.handle)
    return found


def _authoring(call, default: str = "authoring_refused"):  # type: ignore[no-untyped-def]
    """Run one 087 call; its refusal is answered with 087's own reason."""
    try:
        return call()
    except AuthoringError as error:
        raise refused(error, default, f"087 refused it: {error.reason}") from None


@dataclass
class Entry:
    """One proposal held in memory and where it stands: ``open``, ``applied`` or ``rejected``."""

    source: str
    proposal: Proposal
    kind: str  # hand | model
    names: dict[str, str]  # an existing record's pinned ref -> its name, as the context offered it
    review: object = None  # 087's ProposalReview, the latest
    draft: AcceptanceDraft | None = None
    digest: str | None = None
    state: str = "open"
    applied: dict | None = None
    applied_digest: str | None = None
    usage: dict | None = None


def entry_of(ctx: AdapterContext, args: Mapping[str, object]) -> Entry:
    source = source_of(args)
    token = segment(args, "proposal")
    found = memory(ctx).proposals.get(token)  # type: ignore[arg-type]
    if found is None or found.source != source:  # type: ignore[attr-defined]
        raise ApiRefused("unknown_proposal", "no such proposal in this app's memory: it was never made here, or the "
                                             "app restarted or forgot it since. Build or suggest it again")
    return found  # type: ignore[return-value]


def _open(entry: Entry) -> None:
    if entry.state == "rejected":
        raise ApiRefused("rejected", "that proposal was discarded")
    if entry.state == "applied":
        raise ApiRefused("already_applied", "that proposal was accepted and written: build or suggest a new one")


def _drop_draft(ctx: AdapterContext, entry: Entry) -> None:
    """End a draft the person can no longer approve (a new review or decision): 043 rejects it, writing nothing."""
    if entry.draft is not None and entry.draft.changes.state in ("open", "failed"):
        try:
            library(ctx).reject(entry.draft, ctx.authority)
        except (ChangeSetError, AuthoringError):
            pass  # already ended: nothing to revoke
    entry.draft, entry.digest = None, None


def _end(ctx: AdapterContext, made: AcceptanceDraft) -> None:
    """End a draft 043 refused to write (it conflicted, or cannot apply): its approvals are revoked and it can never
    be applied later. One already decided is left as it is."""
    try:
        library(ctx).reject(made, ctx.authority)
    except (ChangeSetError, AuthoringError):
        pass  # already_decided: nothing left to revoke
    # an authority that fails now: 043 withdraws the approvals on a later reject, and the person is answered with
    # the write's own refusal, not this one
    except Exception:  # noqa: BLE001, S110
        pass


def _reviewed(ctx: AdapterContext, entry: Entry) -> None:
    _drop_draft(ctx, entry)
    entry.review = _authoring(lambda: library(ctx).review(entry.proposal))


def _keep(ctx: AdapterContext, entry: Entry) -> Entry:
    memory(ctx).keep(memory(ctx).proposals, entry.proposal.proposal_id, entry, MAX_PROPOSALS)
    return entry


def _names(context: AuthoringContext) -> dict[str, str]:
    return {str(record.ref): record.name for record in context.records}


# -- what the page shows -------------------------------------------------------------------------------------------
def draft_digest(draft: AcceptanceDraft) -> str:
    """What one approval is for: every file's exact bytes and every precondition of the draft's change set."""
    changes = draft.changes
    body = {"files": [[path, hashlib.sha256(data).hexdigest()] for path, data in sorted(changes.preview().items())],
            "preconditions": [[p.kind, p.entity_id, p.revision, p.etag] for p in changes.changeset.preconditions],
            "manifest": changes.manifest_etag}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:32]


def _draft_view(ctx: AdapterContext, entry: Entry) -> dict | None:
    draft = entry.draft
    if draft is None:
        return None
    changes = draft.changes
    about = {item.path: item for item in changes.files}
    rows = file_views(changes.preview(), ctx.store.read_exact, {item.path: item.diff for item in changes.files})
    files = [{**row, "change": about[row["path"]].action, "target": about[row["path"]].target,
              "summary": about[row["path"]].summary} for row in rows]
    selection = draft.selection
    changeset = changes.changeset
    return {
        "digest": entry.digest, "changeset": changeset.changeset_id, "title": changeset.title,
        "author": changeset.author, "rationale": changeset.rationale, "files": files,
        "preconditions": [{"kind": p.kind, "entity_id": p.entity_id, "revision": p.revision, "etag": p.etag,
                           "ref": str(SourceRef(entry.source, p.kind, p.entity_id, p.revision))}
                          for p in changeset.preconditions],
        "records": {key: str(ref) for key, ref in draft.records.items()},
        "topology": None if draft.topology is None else str(draft.topology),
        "accepted": [candidate.key for candidate in selection.accepted],
        "distinct": list(draft.decision.distinct),
        "kept": [link.link_id for link in selection.links],
        "dropped": [{"link": link.link_id, "reason": reason} for link, reason in selection.dropped],
        "surfaced": [candidate for candidate in dict.fromkeys(d.candidate for d in selection.surfaced)],
        "discarded": [candidate.key for candidate in selection.discarded],
        "conflicts": [{"target": c.target, "reason": c.reason, "detail": c.detail} for c in changes.conflicts],
        "blocked": list(changes.blocked), "applicable": changes.applicable,
        "needs_approval": list(changes.needs_approval),
    }


def proposal_view(ctx: AdapterContext, entry: Entry) -> dict:
    proposal = entry.proposal
    review = entry.review
    duplicates: dict[str, list[dict]] = {}
    problems: dict[str, str] = {}
    if review is not None:
        for item in review.duplicates:  # type: ignore[attr-defined]
            duplicates.setdefault(item.candidate, []).append(
                {"existing": None if item.existing is None else str(item.existing),
                 "existing_name": item.existing_name or None, "other": item.other})
        problems = {item.link_id: item.reason for item in review.problems}  # type: ignore[attr-defined]
    keys = {candidate.key for candidate in proposal.candidates}

    def endpoint(value: object) -> dict:
        if type(value) is SourceRef:
            return {"record": str(value), "name": entry.names.get(str(value))}
        return {"candidate": value} if value in keys else {"unknown": str(value)}

    request = proposal.request
    return {
        "proposal": proposal.proposal_id, "source": entry.source, "origin": proposal.origin, "kind": entry.kind,
        "state": entry.state, "rationale": proposal.rationale,
        "request": {"request": request.request_id, "brief": request.brief, "constraints": list(request.constraints),
                    "max_candidates": request.max_candidates},
        "candidates": [{"key": c.key, "kind": c.kind, "name": c.name, "role": c.role, "description": c.description,
                        "storable": c.storable, "place": c.is_place, "duplicates": duplicates.get(c.key, [])}
                       for c in proposal.candidates],
        "links": [{"link": link.link_id, "relation": link.relation, "subject": link.subject,
                   "object": endpoint(link.object), "problem": problems.get(link.link_id)} for link in proposal.links],
        "reviewed": review is not None, "basis": None if review is None else review.basis,  # type: ignore[attr-defined]
        "draft": _draft_view(ctx, entry), "applied": entry.applied, "usage": entry.usage, "note": SAVED,
    }


# -- build by hand -------------------------------------------------------------------------------------------------
def build(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """A proposal of the person's own candidates and links, kept in memory and reviewed. Writes nothing."""
    source = source_of(args)
    world_only(source, "World authoring")
    brief = text_of(args, "brief", BRIEF_MAX, required=False) or HAND_BRIEF
    if not brief.strip():
        brief = HAND_BRIEF
    rationale = text_of(args, "rationale", TEXT_MAX, required=False)
    raw = {"candidates": args.get("candidates"), "links": args.get("links", []), "rationale": rationale}
    context = _authoring(lambda: library(ctx).context(source))
    proposal_id = "hand-" + uuid.uuid4().hex[:12]
    request = _authoring(lambda: AuthoringRequest(proposal_id, source, brief, (), MAX_CANDIDATES))
    # the same checks as a model's reply: a link to an existing record must name a pinned ref the context offered
    proposal = _authoring(lambda: parse_proposal(raw, request, context, proposal_id=proposal_id, origin=ctx.owner))
    entry = Entry(source, proposal, "hand", _names(context))
    _reviewed(ctx, entry)
    return {"proposal": proposal_view(ctx, _keep(ctx, entry))}


# -- suggest: the one metered call ---------------------------------------------------------------------------------
def model_state(ctx: AdapterContext) -> tuple[ModelConfig | None, str | None, dict | None]:
    """The model settings 112 holds, their etag, and why no suggestion can be sent now (or ``None``)."""
    inference = providers.table(ctx.api)
    shown = inference.call("models", {})
    config = inference.config
    if config is None:
        problem = inference.problem or {}
        why = problem.get("message") or ("the app was started with a model nothing here configures: a suggestion goes "
                                         "only to the model of the app's settings file")
        return None, shown["etag"], {"reason": "no_model", "message": f"No model is set up for suggestions: {why}"}
    if not config.authoring_calls:
        return config, shown["etag"], {"reason": "not_enabled",
                                       "message": "World suggestions are off in Models and costs, so nothing can be "
                                                  "sent from here. Switch them on there to allow one billed call for "
                                                  "each suggestion you approve. You can still build a proposal by "
                                                  "hand."}
    if config.key_env is not None and not inference.environ.get(config.key_env):
        return config, shown["etag"], {"reason": "key_missing",
                                       "message": f"The key variable {config.key_env} is not set where the app runs."}
    return config, shown["etag"], None


def _generator(ctx: AdapterContext, config: ModelConfig, armed: dict) -> AuthoringGenerator:
    """087's generator over 013's adapter to the configured endpoint, on its own untrusted handle; each attempt is
    approved for its exact payload, only while ``armed`` (the one reviewed call is being made)."""
    inference = providers.table(ctx.api)
    authorizer = Authorizer({ctx.owner})
    scope = ResourcePath(("providers", config.endpoint.provider_id))
    authorizer.add_grant(Grant("app-world-author", PRINCIPAL, frozenset({Permission.EXECUTE}), scope, ctx.owner))
    handle = authorizer.handle_for(PRINCIPAL, trusted=False)
    credentials = EnvironmentCredentials({config.endpoint.provider_id: config.key_env} if config.key_env else {})
    transport = inference.transport if inference.transport is not None else HttpTransport()
    adapter = ProviderAdapter(ProviderRegistry([config.endpoint]), credentials, authorizer, transport)

    def approve(route: str, attempt: int, payload: bytes) -> str | None:
        if not armed.get("on"):  # an approval outlives no review
            return None
        approval_id = "suggest-" + uuid.uuid4().hex
        authorizer.register_approval(Approval(approval_id, PRINCIPAL, Permission.EXECUTE, scope,
                                              payload_digest(payload), ctx.owner))
        return approval_id

    routes = tuple(RouteChoice(config.endpoint.provider_id, model) for model in config.models)
    return AuthoringGenerator(adapter, routes, handle, approvals=approve, policy=authoring_policy(config),
                              max_output_tokens=config.max_output_tokens)


def _constraints(args: Mapping[str, object]) -> tuple[str, ...]:
    value = args.get("constraints", [])
    if type(value) is not list or len(value) > MAX_CONSTRAINTS or any(type(item) is not str for item in value):
        raise ApiRefused("invalid_field", f"constraints must be a list of at most {MAX_CONSTRAINTS} lines of text")
    return tuple(item for item in value if item.strip())


class _Planned(NamedTuple):
    source: str
    request: AuthoringRequest
    context: AuthoringContext
    config: ModelConfig | None
    blocked: dict | None
    estimate: dict
    review: str
    replay: str | None  # the proposal this request already gave: nothing is sent again


def _planned(ctx: AdapterContext, args: Mapping[str, object]) -> _Planned:
    source = source_of(args)
    world_only(source, "World authoring")
    request_id = segment(args, "request_id")
    brief = text_of(args, "brief", BRIEF_MAX)
    constraints = _constraints(args)
    bound = whole(args, "max_candidates", 1, MAX_CANDIDATES, 8)
    request = _authoring(lambda: AuthoringRequest(request_id, source, brief, constraints, bound))  # type: ignore[arg-type]
    held = memory(ctx)
    ask = (source, brief, constraints, bound)
    replay = None
    earlier = held.requests.get(request_id)  # type: ignore[arg-type]
    if earlier is not None:
        if earlier["ask"] != ask:
            raise ApiRefused("request_conflict", "that request was made with another brief: suggest again under a new "
                                                 "request")
        if earlier["proposal"] is not None:
            if earlier["proposal"] not in held.proposals:
                raise ApiRefused("forgotten", "this app no longer holds what that request suggested: suggest again "
                                              "under a new request")
            replay = earlier["proposal"]
    context = _authoring(lambda: library(ctx).context(source))
    config, etag, blocked = model_state(ctx)
    messages = _authoring(lambda: authoring_messages(request, context))
    estimate = _estimate(config, messages)
    unkept = len(earlier["spent"]) if earlier is not None else 0
    offered = [[str(record.ref), record.kind, record.name] for record in context.records]
    body = {"request": [request_id, *ask[:2], list(constraints), bound], "basis": context.basis, "title": context.title,
            "records": offered, "truncated": context.truncated, "settings": etag, "estimate": estimate, "sent": unkept,
            "nonce": held.nonce}  # salted: a token is had only from an estimate, never worked out by a client
    review = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]
    return _Planned(source, request, context, config, blocked, estimate, review, replay)


def _context_view(context: AuthoringContext) -> dict:
    return {"records": len(context.records), "truncated": context.truncated, "basis": context.basis}


def suggest_plan(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """What suggesting would send and cost, and the review token ``suggest`` takes. Nothing is sent."""
    planned = _planned(ctx, args)
    return {"source": planned.source,
            "plan": {"request": planned.request.request_id, "expected": planned.review,
                     "replay": planned.replay is not None, "blocked": None if planned.replay else planned.blocked,
                     "call": planned.estimate, "context": _context_view(planned.context)},
            "note": SAVED}


def suggest(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Make the one reviewed call (or answer what this request already suggested, sending nothing)."""
    planned = _planned(ctx, args)
    held = memory(ctx)
    if args.get("expected") != planned.review:
        raise ApiRefused("changed" if type(args.get("expected")) is str else "review_required",
                         "the world, the request or the model settings changed since the estimate; nothing was sent")
    if planned.replay is not None:
        entry = held.proposals[planned.replay]
        return {"source": planned.source, "done": {"request": planned.request.request_id, "replay": True},
                "proposal": proposal_view(ctx, entry)}  # type: ignore[arg-type]
    if planned.blocked is not None:
        raise ApiRefused(planned.blocked["reason"], planned.blocked["message"])
    if planned.review in held.consumed:
        raise ApiRefused("changed", "that estimate was already used for a call; estimate again to approve another")
    config = planned.config
    assert config is not None
    armed: dict = {}
    try:
        generator = _generator(ctx, config, armed)
    except (ConfigError, ValueError) as error:
        raise ApiRefused("no_model", str(error)) from None
    request_id = planned.request.request_id

    def step() -> Proposal:
        try:
            return generator.propose(planned.request, planned.context)
        finally:  # every attempt the call booked, in the trace like a turn's
            for meter in generator.meters:
                record_calls(meter)

    kept = False
    asked = (planned.source, planned.request.brief, planned.request.constraints, planned.request.max_candidates)
    record = held.request(request_id, asked)
    held.consume(planned.review)  # one approval, one send: never taken again, whatever comes back
    armed["on"] = True
    try:
        runner = ctx.api.runner
        proposal = _Probe(runner.tracer, runner.clock).run(step)
        kept = True
    except AuthoringError as error:
        raise ApiRefused(reason_of(error.reason, "authoring_refused"),
                         f"087 refused the reply ({error.reason}); nothing was kept") from None
    finally:
        armed["on"] = False
        used = _usage(generator, config)  # type: ignore[arg-type]
        if used["attempts"] and not kept:  # what was spent is said, whether or not a reply was kept
            record["spent"].append(used)
    # the paid reply is kept, and the request answers it, before anything else can fail: a review that fails leaves
    # a kept proposal with no review (the page reviews it again), never a lost reply
    entry = Entry(planned.source, proposal, "model", _names(planned.context), usage=used if used["attempts"] else None)
    _keep(ctx, entry)
    record["proposal"] = proposal.proposal_id
    done = {"request": request_id, "replay": False, "review_problem": None}
    try:
        _reviewed(ctx, entry)
    except ApiRefused as error:
        entry.review = None
        done["review_problem"] = error.reason
    return {"source": planned.source, "done": done, "proposal": proposal_view(ctx, entry)}


# -- review, decide, write -----------------------------------------------------------------------------------------
def review(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Judge the proposal against the world as it is now (087). Writes nothing; an earlier draft is ended."""
    entry = entry_of(ctx, args)
    _open(entry)
    _reviewed(ctx, entry)
    return {"proposal": proposal_view(ctx, entry)}


def draft(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """The exact change set the person's decision would apply, shown byte for byte. Writes nothing."""
    entry = entry_of(ctx, args)
    _open(entry)
    if entry.review is None:
        raise ApiRefused("review_required", "review the proposal first")
    accept, distinct = keys_of(args, "accept"), keys_of(args, "distinct")
    topology = segment(args, "topology", required=False)
    decision = _authoring(lambda: Decision(accept, ctx.owner, distinct))
    _drop_draft(ctx, entry)
    made = _authoring(lambda: library(ctx).draft(entry.review, decision, topology=topology))  # type: ignore[arg-type]
    entry.draft, entry.digest = made, draft_digest(made)
    return {"proposal": proposal_view(ctx, entry)}


def _created(ctx: AdapterContext, entry: Entry, accepted) -> dict:  # type: ignore[no-untyped-def]
    proposal = entry.proposal
    names = {candidate.key: candidate for candidate in proposal.candidates}
    return {"records": [{"key": key, "ref": str(ref), "name": names[key].name, "kind": ref.kind}
                        for key, ref in accepted.records.items()],
            "topology": None if accepted.topology is None else str(accepted.topology),
            "links": [{"link": link.link_id, "relation": link.relation, "subject": link.subject,
                       "object": str(link.object)} for link in entry.draft.selection.links],  # type: ignore[union-attr]
            "changeset": accepted.changeset.changeset_id,
            "written": sorted(accepted.changeset.etags)}


def apply(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Write exactly the draft whose digest the person approved; a retry writes nothing again."""
    entry = entry_of(ctx, args)
    digest = args.get("draft")
    if type(digest) is not str:
        raise ApiRefused("review_required", "name the draft you approved")
    if entry.state == "applied" and entry.applied_digest == digest:
        return {"done": {"already_applied": True}, "proposal": proposal_view(ctx, entry)}
    _open(entry)
    if entry.draft is None or entry.digest != digest:
        raise ApiRefused("changed", "that is not the draft this proposal holds now; nothing was written. Draft it again")
    authoring = library(ctx)
    made = entry.draft
    try:
        approvals = (authoring.approve(made, ctx.authority, approved_by=ctx.owner,
                                       new_id=lambda: "authoring-" + uuid.uuid4().hex)
                     if made.changes.needs_approval else None)  # this op is the person's own press on the draft
        accepted = authoring.apply(made, approvals)
    except ChangeSetConflict as error:
        _end(ctx, made)
        entry.draft, entry.digest = None, None
        raise ApiRefused("conflict", "the world changed since the draft; nothing was written. Review the proposal "
                                     "again: " + "; ".join(f"{c.target} {c.reason}" for c in error.conflicts)) from None
    except WriteDenied:
        raise ApiRefused("denied", "the library's authority refused this write; nothing was written") from None
    except ChangeSetError as error:  # not_applicable from approve among them: the draft can never be written
        _end(ctx, made)
        entry.draft, entry.digest = None, None
        raise refused(error, "changeset_refused") from None
    except AuthoringError as error:
        raise refused(error, "authoring_refused") from None
    entry.applied = _created(ctx, entry, accepted)
    entry.state, entry.applied_digest = "applied", digest
    return {"done": {"already_applied": False}, "proposal": proposal_view(ctx, entry)}


def reject(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Discard the proposal: nothing is written, and nothing of it is kept but this note that it was discarded."""
    entry = entry_of(ctx, args)
    if entry.state == "applied":
        raise ApiRefused("already_applied", "that proposal was accepted and written")
    _drop_draft(ctx, entry)
    entry.state = "rejected"
    return {"proposal": proposal_view(ctx, entry)}


def proposals_of(ctx: AdapterContext, source: str) -> list[dict]:
    """The proposals this app holds for ``source``, newest first."""
    return [proposal_view(ctx, entry) for entry in reversed(list(memory(ctx).proposals.values()))  # type: ignore[arg-type]
            if entry.source == source]  # type: ignore[attr-defined]


__all__ = ["OPERATION", "PRINCIPAL", "SAVED", "apply", "build", "draft", "library", "model_state", "proposals_of",
           "reject", "review", "suggest", "suggest_plan"]
