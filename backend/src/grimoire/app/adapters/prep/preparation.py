"""064's scenario preparation on the Prep page: ``prep.preparation``, ``prep.prepare_plan``, ``prep.prepare`` and
``prep.prepare_review``. The director's alone: every op refuses ``director_only`` in the table's view.

**The evidence** is assembled from what the library already holds for one chat of the campaign, read again on every
call (never cached), and handed to 064 as its ``Snapshot``:

- *secret*: each proposition of the campaign's 020 ledger, as the director's truth has it (not refuted), that none
  of the chat's player characters believes, was told or sent; named ``campaign:<id>#proposition/<id>``;
- *goal*: each goal of the 039 agendas play runs for an NPC in the chat's cast (the startup agendas and the model
  settings' agendas, as 112 composes them); named by the NPC;
- *calendar*: the campaign clock (055) and its deadlines not yet flagged. 055 keeps the clock in the campaign's scope
  record, which no 064 source type can name, so an item is labelled ``campaign:<id>/clock/now`` or
  ``campaign:<id>/clock/<deadline id>``: a label for that entry of the scope record, not a stored instance;
- *choice*: the latest posts the chat's player characters made on turns the table kept: what the players chose to do;
  named by the character who posted.

The cast is the chat's cast; the locations are the campaign's instances of kind ``location`` or ``place``. Nothing
is inferred and nothing is written; a category with nothing in it is 064's ``missing``. A ledger that cannot be read
in full refuses (``knowledge_damaged``) rather than call a secret missing.

**The one call.** ``prepare_plan`` builds the exact prompt 064 would send and answers its estimate (the app's
deterministic token estimate, the reply cap and the configured prices) with a review token over the evidence, the
brief and the model settings. Nothing is sent. ``prepare`` takes that token, builds everything again, refuses
``changed`` if anything moved, and only then runs 064's ``PreparationGenerator``: one optional ``planning`` call under
a policy that allows exactly one, through 013's adapter to the endpoint the app's model settings name (112's file,
with its key variable and transport), on an untrusted handle of its own (``app:scene-preparer``) whose every attempt
is approved for its exact payload. 064's ``PreparationSession`` keeps the result: the same request id, brief and
evidence again is answered from it, and nothing is sent again; the same id with another brief or newer evidence is
064's ``request_conflict``.

**Nothing is saved.** The session is in memory, one per chat, for as long as the app runs. Accepting a reviewed
proposal records no scene, event, belief, player action or outcome: it is 064's ``PreparedScene``, gone with the app.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from decimal import Decimal

from grimoire.app.adapter import AdapterContext
from grimoire.app.config import ConfigError, ModelConfig
from grimoire.app.pricing import cost_text, cost_view, price_table
from grimoire.calendars import CalendarError
from grimoire.calendarstore import CalendarLibrary
from grimoire.chats.codec import ChatCodecError
from grimoire.context.model import estimate_tokens
from grimoire.core import DIRECTOR
from grimoire.core.identity import ChatRef, InstanceRef, KnowledgeRef, ScopeRef
from grimoire.core.inference import InferencePolicy
from grimoire.core.perspective import actor_knowledge, director_knowledge
from grimoire.play.scene import NOW
from grimoire.preparation import (
    Evidence, PreparationError, PreparationGenerator, PreparationSession, Review, Snapshot, preparation_messages,
)
from grimoire.providers import EnvironmentCredentials, ProviderRegistry, RouteChoice
from grimoire.providers.adapter import ProviderAdapter
from grimoire.providers.config import route_label
from grimoire.providers.transport import HttpTransport
from grimoire.security import Approval, Authorizer, Grant, Permission, ResourcePath, payload_digest
from grimoire.textapi import ApiRefused

from .. import providers
from ..campaign import hub_of
from .common import chat_of, director, segment

PREP_PRINCIPAL = "app:scene-preparer"
#: The kinds of campaign instance offered as a scene's location.
LOCATION_KINDS = ("location", "place")
#: At most this many items of each kind are offered (064 takes at most 64 evidence items in all).
LIMITS = {"secret": 24, "goal": 16, "calendar": 8, "choice": 8}
MAX_BRIEF = 2000
MAX_EVIDENCE_TEXT = 4000
#: The words each message of a prompt is counted with beside its text, in the estimate.
MESSAGE_OVERHEAD = 4
_ATTRIBUTE = "_prep_sessions"
_REASON = "abcdefghijklmnopqrstuvwxyz_"
SAVED = ("Nothing here is saved: a prepared scene is a proposal kept in this app's memory until it stops. It is never "
         "canon, an event, a belief or a player's choice.")


def _reason(text: str, default: str) -> str:
    """A 064 refusal code as a machine-readable reason (``incomplete:length`` is ``incomplete_length``)."""
    found = str(text).replace(":", "_").replace("-", "_")
    return found if found and found[0].isalpha() and all(c in _REASON or c.isdigit() for c in found) else default


# -- the evidence -----------------------------------------------------------------------------------------------
def _clip(text: str) -> str:
    text = " ".join(str(text).split()) if "\n" not in str(text) else str(text).strip()
    return text if len(text) <= MAX_EVIDENCE_TEXT else text[:MAX_EVIDENCE_TEXT - 1] + "…"


def _agendas(ctx: AdapterContext):  # type: ignore[no-untyped-def]
    """The agendas play runs: the campaign startup's, then the model settings' (as 112 composes them)."""
    config = providers.table(ctx.api).config
    return (*ctx.api.startup_agendas, *(() if config is None else config.agendas))


def assemble(ctx: AdapterContext, chat: ChatRef) -> tuple[Snapshot, dict]:
    """064's snapshot of what the library holds for ``chat`` now, and what the page shows beside it."""
    campaign = chat.campaign_id
    hub = hub_of(ctx)
    scene = hub.runner.open(chat)
    names = scene.names
    view = ctx.store.load()
    omitted = {kind: 0 for kind in LIMITS}
    found: dict[str, list[tuple[str, InstanceRef | KnowledgeRef, str, dict]]] = {kind: [] for kind in LIMITS}

    # secrets: the director's truth that no player character believes, was told or sent
    stored = hub.evidence.read(campaign, requested_by=DIRECTOR)
    if stored.quarantined or stored.problems:
        raise ApiRefused("knowledge_damaged", "the campaign's knowledge cannot be read in full, so its secrets cannot "
                                              "be told apart; nothing was sent")
    knowledge = stored.knowledge
    held: set[str] = set()
    for actor in scene.cast:
        if scene.controller(actor) in (None, DIRECTOR):
            continue
        try:
            known = actor_knowledge(knowledge, actor, as_of=NOW)
        except ValueError:
            raise ApiRefused("knowledge_damaged", "a player character's knowledge cannot be read, so the secrets cannot "
                                                  "be told apart; nothing was sent") from None
        # a proposition a player character believes, was told or sent is no secret from the table
        held.update(belief.proposition_id for belief in (*known.current, *known.history))
        held.update(item.proposition_id for item in known.evidence if item.proposition_id is not None)
        held.update(item.proposition_id for item in known.sent)
    truth = director_knowledge(knowledge, as_of=NOW, requested_by=DIRECTOR)
    for standing in sorted(truth.propositions, key=lambda each: (-each.proposition.moment.sequence,
                                                                each.proposition.proposition_id)):
        proposition = standing.proposition
        if standing.standing == "refuted" or proposition.proposition_id in held:
            continue
        try:
            secret = KnowledgeRef(campaign, "proposition", proposition.proposition_id)
        except ValueError:
            omitted["secret"] += 1
            continue
        found["secret"].append((str(proposition.text), secret, f"{standing.standing}@{proposition.moment.sequence}",
                                {"standing": standing.standing}))

    # goals: the agendas play runs for this chat's NPCs
    for agenda in _agendas(ctx):
        actor = agenda.actor
        if actor.campaign_id != campaign or actor not in scene.cast or scene.controller(actor) != DIRECTOR:
            continue
        who = names.get(actor) or actor.instance_id
        for goal in sorted(agenda.goals, key=lambda each: (-each.priority, each.goal_id)):
            found["goal"].append((f"{who} wants: {goal.aim}", actor, f"{goal.goal_id}@{goal.priority}",
                                  {"goal": goal.goal_id, "priority": goal.priority}))

    # calendar: the clock and its deadlines not yet flagged
    try:
        clock = CalendarLibrary(ctx.store, ctx.handle).read(ScopeRef(campaign))
    except (CalendarError, ChatCodecError) as error:
        raise ApiRefused(getattr(error, "reason", None) or "calendar_unreadable", str(error)) from None
    state = clock.state
    if state is not None:
        calendar = state.calendar

        def when(moment) -> str:  # type: ignore[no-untyped-def]
            hours, minutes = divmod(moment.minute, 60)
            return f"{calendar.format(moment.fixed_day)} {hours:02d}:{minutes:02d}"

        found["calendar"].append((f"The campaign's date is {when(state.now)}.", InstanceRef(campaign, "clock", "now"),
                                  clock.etag, {}))
        for deadline in sorted(state.deadlines, key=lambda each: (each.due, each.deadline_id)):
            if deadline.triggered_by is not None:
                continue
            try:
                entry = InstanceRef(campaign, "clock", deadline.deadline_id)
            except ValueError:
                omitted["calendar"] += 1
                continue
            what = "is due" if deadline.kind == "deadline" else f"({deadline.kind}) is set for"
            found["calendar"].append((f"{deadline.label} {what} {when(deadline.due)}.", entry, clock.etag,
                                      {"deadline": deadline.deadline_id, "deadline_kind": deadline.kind}))

    # choices: what the players' characters did on the turns the table kept, newest first
    etags = scene.turns.etags()
    for record in reversed(scene.records):
        if record.kept is None:
            continue
        for post in record.posts:
            if scene.controller(post.actor) in (None, DIRECTOR):
                continue
            who = names.get(post.actor) or post.actor.instance_id
            found["choice"].append((f"{who} (turn {record.number}): {post.text}", post.actor,
                                    etags.get(record.turn_id) or f"turn-{record.number}", {"turn": record.number}))

    evidence: list[Evidence] = []
    shown: list[dict] = []
    for kind in ("secret", "goal", "calendar", "choice"):
        items = found[kind]
        omitted[kind] += max(0, len(items) - LIMITS[kind])
        items = items[:LIMITS[kind]]
        if kind == "choice":
            items = list(reversed(items))  # oldest first, as they were played
        for number, (text, source, revision, extra) in enumerate(items, 1):
            made = Evidence(f"{kind}-{number}", kind, source, str(revision), _clip(text))  # type: ignore[arg-type]
            evidence.append(made)
            shown.append({"evidence": made.evidence_id, "kind": kind, "source": str(source),
                          "name": names.get(source) if isinstance(source, InstanceRef) else None,
                          "revision": made.revision, "text": made.text, **extra})
    cast = tuple(scene.cast)[:64]
    locations = tuple(sorted((ref for ref in view.instances if ref.campaign_id == campaign
                              and ref.kind in LOCATION_KINDS), key=str))[:64]

    def name_of(ref: InstanceRef) -> str | None:
        if ref in names:
            return names[ref]
        record = view.instances.get(ref)
        payload = None if record is None else record.envelope.payload
        local = payload.get("local_name") if isinstance(payload, Mapping) else None
        return local if isinstance(local, str) else (None if record is None else record.envelope.name)

    body = {"evidence": [[e.evidence_id, e.kind, str(e.source), e.revision, e.text] for e in evidence],
            "cast": [str(ref) for ref in cast], "locations": [str(ref) for ref in locations]}
    revision = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
    snapshot = Snapshot(campaign, revision, tuple(evidence), cast, locations)
    return snapshot, {"revision": revision, "evidence": shown, "missing": list(snapshot.missing),
                      "omitted": {kind: count for kind, count in omitted.items() if count},
                      "cast": [{"actor": str(ref), "name": name_of(ref)} for ref in cast],
                      "locations": [{"actor": str(ref), "name": name_of(ref)} for ref in locations]}


# -- the session, kept in memory per chat ---------------------------------------------------------------------
class _Prep:
    """One chat's 064 session and what the page shows of it; in memory only."""

    def __init__(self, api, chat: ChatRef) -> None:  # type: ignore[no-untyped-def]
        self.chat = chat
        self.context = AdapterContext(api)
        self.generator: PreparationGenerator | None = None
        self.session = PreparationSession(chat.campaign_id, self._read, self._propose)
        self.proposals: dict = {}  # request id -> 064 Proposal, as the session answered it
        self.outcomes: dict[str, str] = {}  # request id -> accepted / rejected
        self.usage: dict[str, dict] = {}  # request id -> what its call used
        self.spent: list[dict] = []  # every call this session sent, refused replies included

    def _read(self) -> Snapshot:
        return assemble(self.context, self.chat)[0]

    def _propose(self, snapshot: Snapshot, brief: str):  # type: ignore[no-untyped-def]
        if self.generator is None:  # only ``prepare``, after its review, sets one
            raise PreparationError("not_approved")
        return self.generator(snapshot, brief)


def _prep(ctx: AdapterContext, chat: ChatRef) -> _Prep:
    table = getattr(ctx.api, _ATTRIBUTE, None)
    if table is None:
        table = {}
        setattr(ctx.api, _ATTRIBUTE, table)
    found = table.get(str(chat))
    if found is None:
        found = table[str(chat)] = _Prep(ctx.api, chat)
    return found


# -- the call ------------------------------------------------------------------------------------------------
def _model(ctx: AdapterContext) -> tuple[ModelConfig | None, str | None, dict | None]:
    """The model settings 112 holds, their etag, and why no call can be sent now (or ``None``)."""
    inference = providers.table(ctx.api)
    shown = inference.call("models", {})
    config = inference.config
    if config is None:
        problem = inference.problem or {}
        why = problem.get("message") or ("the app was started with a model nothing here configures: preparation "
                                         "goes only to the model of the app's settings file")
        return None, shown["etag"], {"reason": "no_model", "message": f"No model is set up for preparation: {why}"}
    if config.key_env is not None and not inference.environ.get(config.key_env):
        return config, shown["etag"], {"reason": "key_missing",
                                       "message": f"The key variable {config.key_env} is not set where the app runs."}
    return config, shown["etag"], None


def _generator(ctx: AdapterContext, config: ModelConfig, prep: _Prep) -> PreparationGenerator:
    """064's generator over 013's adapter to the configured endpoint, on its own untrusted handle; each attempt is
    approved for its exact payload, and only while ``prep`` holds this generator (``prepare`` after its review)."""
    inference = providers.table(ctx.api)
    authorizer = Authorizer({ctx.owner})
    scope = ResourcePath(("providers", config.endpoint.provider_id))
    authorizer.add_grant(Grant("app-scene-preparer", PREP_PRINCIPAL, frozenset({Permission.EXECUTE}), scope, ctx.owner))
    handle = authorizer.handle_for(PREP_PRINCIPAL, trusted=False)
    credentials = EnvironmentCredentials({config.endpoint.provider_id: config.key_env} if config.key_env else {})
    transport = inference.transport if inference.transport is not None else HttpTransport()
    adapter = ProviderAdapter(ProviderRegistry([config.endpoint]), credentials, authorizer, transport)
    made: dict[str, PreparationGenerator] = {}

    def approve(route: str, attempt: int, payload: bytes) -> str | None:
        if prep.generator is not made.get("generator"):  # an approval outlives no review
            return None
        approval_id = "prepare-" + uuid.uuid4().hex
        authorizer.register_approval(Approval(approval_id, PREP_PRINCIPAL, Permission.EXECUTE, scope,
                                              payload_digest(payload), ctx.owner))
        return approval_id

    policy = InferencePolicy({"planning": 1}, max_attempts_per_call=config.attempts)  # type: ignore[arg-type]
    routes = tuple(RouteChoice(config.endpoint.provider_id, model) for model in config.models)
    generator = PreparationGenerator(adapter, routes, handle, approvals=approve, policy=policy,
                                     max_output_tokens=config.max_output_tokens)
    made["generator"] = generator
    return generator


def _estimate(config: ModelConfig | None, messages) -> dict:  # type: ignore[no-untyped-def]
    prompt = sum(estimate_tokens(body) + MESSAGE_OVERHEAD for _role, body in messages)
    found: dict = {"stage": "planning", "calls": 1, "prompt_tokens": prompt, "measurement": "estimated"}
    if config is None:
        return {**found, "reply_tokens_at_most": None, "attempts_at_most": None, "route": None,
                "cost_at_most": None, "cost_state": "unpriced", "local": None}
    found.update(reply_tokens_at_most=config.max_output_tokens, attempts_at_most=config.attempts,
                 route=route_label(config.endpoint, config.model), local=config.endpoint.loopback_literal,
                 fallbacks=list(config.fallbacks))
    if not config.priced:
        return {**found, "cost_at_most": None, "cost_state": "unpriced"}
    assert config.price_prompt is not None and config.price_reply is not None
    one = (Decimal(prompt) * config.price_prompt + Decimal(config.max_output_tokens) * config.price_reply) \
        / Decimal(1_000_000)
    # a retry goes to a fallback model, which has no price: only the configured model's attempts are priced
    priced_attempts = 1 if config.fallbacks else config.attempts
    return {**found, "cost_at_most": cost_text(one * priced_attempts), "cost_state": "estimated",
            "unpriced_attempts_at_most": config.attempts - priced_attempts}


def _brief(args: Mapping[str, object]) -> str:
    value = args.get("brief")
    if type(value) is not str or not value.strip() or len(value) > MAX_BRIEF:
        raise ApiRefused("invalid_field", f"the brief must be text of at most {MAX_BRIEF} characters")
    return value


def _review(chat: ChatRef, request: str, brief: str, revision: str, etag: str | None, estimate: dict,
            sent: int) -> str:
    """The token one approval is for. ``sent`` counts the calls already sent under this request whose reply was
    not kept, so an approval is spent by its call: sending again needs the estimate reviewed again."""
    body = {"chat": str(chat), "request": request, "brief": brief, "snapshot": revision, "settings": etag,
            "estimate": estimate, "sent": sent}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]


def _planned(ctx: AdapterContext, args: Mapping[str, object]):  # type: ignore[no-untyped-def]
    director(args, "scenario preparation")
    chat = chat_of(args)
    request = segment(args, "request_id")
    brief = _brief(args)
    prep = _prep(ctx, chat)
    if prep.outcomes.get(request) == "rejected":
        raise ApiRefused("rejected", "that request was discarded; prepare again under a new request")
    snapshot, shown = assemble(ctx, chat)
    previous = prep.proposals.get(request)
    replay = previous is not None and previous.brief == brief and previous.snapshot == snapshot
    if previous is not None and not replay:
        raise ApiRefused("request_conflict", "that request was prepared from another brief or older evidence; "
                                             "prepare again under a new request")
    config, etag, blocked = _model(ctx)
    try:
        messages = preparation_messages(snapshot, brief)
    except PreparationError as error:
        raise ApiRefused(_reason(error.reason, "invalid_preparation"), "the brief or the evidence cannot be sent") \
            from None
    if blocked is None and not snapshot.evidence:
        blocked = {"reason": "no_evidence", "message": "The library holds no secret, goal, calendar or choice for "
                                                       "this chat: nothing could ground a scene."}
    if blocked is None and not snapshot.locations:
        blocked = {"reason": "no_location", "message": "This campaign has no location or place instance to set a "
                                                       "scene in."}
    estimate = _estimate(config, messages)
    return chat, request, brief, prep, snapshot, shown, config, blocked, estimate, \
        _review(chat, request, brief, snapshot.revision, etag, estimate,
                sum(1 for item in prep.spent if item["request"] == request and not item["kept"])), replay


def _prepare_plan(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """What preparing would send and cost, and the review token ``prepare`` takes. Nothing is sent."""
    chat, request, _brief_, _prep_, _snapshot, shown, _config, blocked, estimate, review, replay = _planned(ctx, args)
    return {"chat": str(chat), "plan": {"request": request, "expected": review, "replay": replay,
                                        "blocked": None if replay else blocked, "call": estimate,
                                        "snapshot": shown["revision"]},
            "evidence": shown, "saved": False, "note": SAVED}


def _usage(generator: PreparationGenerator, config: ModelConfig) -> dict:
    attempts = tuple(attempt for meter in generator.meters for attempt in meter.ledger.attempts)
    sums: dict[str, int] = {}
    for attempt in attempts:
        usage = getattr(attempt, "usage", None)
        for name in ("input_tokens", "output_tokens"):
            value = getattr(usage, name, None) if usage is not None else None
            if isinstance(value, int):
                sums[name] = sums.get(name, 0) + value
    sent = sum(1 for attempt in attempts if attempt.outcome != "not_sent")
    return {"attempts": len(attempts), "sent": sent, "tokens": sums, **cost_view(attempts, price_table(config))}


def _prepare(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Make the one reviewed call (or answer the request already prepared under this id, sending nothing)."""
    chat, request, brief, prep, snapshot, _shown, config, blocked, _estimate_, review, replay = _planned(ctx, args)
    if args.get("expected") != review:
        raise ApiRefused("changed" if type(args.get("expected")) is str else "review_required",
                         "the evidence, the brief or the model settings changed since the estimate; nothing was sent")
    if not replay:
        if blocked is not None:
            raise ApiRefused(blocked["reason"], blocked["message"])
        assert config is not None
        try:
            prep.generator = _generator(ctx, config, prep)
        except (ConfigError, ValueError) as error:
            raise ApiRefused("no_model", str(error)) from None
    generator, kept = prep.generator, False
    try:
        proposal = prep.session.prepare(request, brief)
        kept = True
    except ApiRefused:
        raise
    except PreparationError as error:
        raise ApiRefused(_reason(error.reason, "preparation_refused"),
                         f"064 refused the reply ({error.reason}); nothing was kept") from None
    except ValueError as error:
        raise ApiRefused(_reason(str(error), "invalid_preparation"), str(error)) from None
    finally:
        prep.generator = None
        used = None if generator is None else _usage(generator, config)  # type: ignore[arg-type]
        if used is not None and used["attempts"]:  # what was spent is said, whether or not a reply was kept
            prep.spent.append({"request": request, "kept": kept, **used})
    if used is not None and used["attempts"]:
        prep.usage[request] = used
    prep.proposals[request] = proposal
    return {"chat": str(chat), "done": {"request": request, "replay": replay},
            "preparation": _view(ctx, chat, prep)}


def _prepare_review(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """The director's review of one proposal: accept it (all three acknowledgements) or discard it. In memory."""
    director(args, "scenario preparation")
    chat = chat_of(args)
    request = segment(args, "request_id")
    prep = _prep(ctx, chat)
    proposal = prep.proposals.get(request)
    if proposal is None:
        raise ApiRefused("unknown_proposal", "no proposal under that request in this app's memory")
    decision = args.get("decision")
    try:
        if decision == "reject":
            prep.session.reject(proposal)
            prep.outcomes[request] = "rejected"
        elif decision == "accept":
            marks = [args.get(name) for name in ("credible_stakes", "open_agency", "grounded")]
            if not all(type(mark) is bool for mark in marks):
                raise ApiRefused("invalid_field", "credible_stakes, open_agency and grounded are true or false")
            prep.session.accept(Review(proposal, *marks))  # type: ignore[arg-type]
            prep.outcomes[request] = "accepted"
        else:
            raise ApiRefused("invalid_field", "decision must be accept or reject")
    except ApiRefused:
        raise
    except ValueError as error:
        reason = str(error)
        if reason == "stale":
            raise ApiRefused("changed", "the evidence changed since this was prepared: prepare again from what the "
                                        "library holds now") from None
        raise ApiRefused(_reason(reason, "invalid_preparation"), reason) from None
    return {"chat": str(chat), "done": {"request": request, "decision": decision},
            "preparation": _view(ctx, chat, prep)}


# -- what the page shows ------------------------------------------------------------------------------------------
def _draft_view(proposal, names) -> dict:  # type: ignore[no-untyped-def]
    kinds = {item.evidence_id: item for item in proposal.snapshot.evidence}

    def note(item) -> dict:  # type: ignore[no-untyped-def]
        return {"text": item.text, "evidence": [{"evidence": ref, "kind": kinds[ref].kind,
                                                 "source": str(kinds[ref].source)} for ref in item.evidence]}

    draft = proposal.draft
    return {"premise": note(draft.premise),
            "cast": [{"actor": str(ref), "name": names.get(ref)} for ref in draft.cast],
            "location": {"actor": str(draft.location), "name": names.get(draft.location)},
            **{field: [note(item) for item in getattr(draft, field)]
               for field in ("questions", "clues", "tensions", "stakes", "directions")}}


def _view(ctx: AdapterContext, chat: ChatRef, prep: _Prep) -> dict:
    snapshot, shown = assemble(ctx, chat)
    names = {}
    for item in (*shown["cast"], *shown["locations"]):
        names[InstanceRef.parse(item["actor"])] = item["name"]
    _config, _etag, blocked = _model(ctx)
    proposals = []
    for request, proposal in reversed(list(prep.proposals.items())):
        proposals.append({"request": request, "brief": proposal.brief, "snapshot": proposal.snapshot.revision,
                          "current": proposal.snapshot == snapshot, "outcome": prep.outcomes.get(request),
                          "usage": prep.usage.get(request), "draft": _draft_view(proposal, names),
                          "evidence": [{"evidence": e.evidence_id, "kind": e.kind, "source": str(e.source),
                                        "text": e.text} for e in proposal.snapshot.evidence]})
    return {"chat": str(chat), "evidence": shown, "model": blocked, "proposals": proposals,
            "spent": prep.spent, "saved": False, "note": SAVED}


def _preparation(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    director(args, "scenario preparation")
    chat = chat_of(args)
    return _view(ctx, chat, _prep(ctx, chat))


__all__ = ["LOCATION_KINDS", "PREP_PRINCIPAL", "SAVED", "assemble"]
