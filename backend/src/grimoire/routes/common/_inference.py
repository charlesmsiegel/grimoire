"""common: inference responsibilities."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields

from fastapi import HTTPException

from ... import decisions, store, wire
from ...llm_errors import LLMError
from ...store.inference import cascade as inference_cascade
from ...store.inference import facts as inference_facts
from ...store.inference import migrate as inference_migrate
from ...store.inference import resolve as inference
from ...store.inference.resolved import ResolvedInference


@dataclass(frozen=True)
class UsableInference(ResolvedInference):
    """A resolution the seam has checked can send: its `chain` is never None.

    What `require_inference` and `override_inference` hand back. The same
    fields, narrowed in the one place the narrowing is true -- after the
    refusals -- so a call site's `.chain` is the `wire.Chain` the facade takes
    rather than an Optional every caller would have to re-check.
    """

    @property
    def chain(self) -> wire.Chain:
        """What the facade is sent, typed (`ResolvedInference.chain`): never
        None here. Its `primary` is where a call site reads the display facts
        of the attempt it asks for first -- `.kind`, `.model`, `.provider_id`."""
        chain = super().chain
        assert chain is not None, "a usable resolution always resolved an attempt"
        return chain


def _narrowed(resolved: ResolvedInference) -> UsableInference:
    """`resolved` as a `UsableInference`; the caller has refused a resolution
    with no attempt (a None `chain`)."""
    assert resolved.attempts, "narrowed a resolution that resolved nothing"
    return UsableInference(**{f.name: getattr(resolved, f.name) for f in fields(resolved)})


def _raise(refused: inference.Refusal | None) -> None:
    """Raise a store refusal (`inference.refusal` and its two halves) as the
    HTTP error it names; nothing when there is none."""
    if refused is not None:
        status, detail = refused
        raise HTTPException(status_code=status, detail=detail)


def _usable(resolved: ResolvedInference) -> UsableInference:
    """`resolved`, refused with the seam's 409 if it cannot send, or cannot do
    what its route needs -- `inference.refusal`, the one decision the settings
    view reports as well."""
    _raise(inference.refusal(resolved))
    return _narrowed(resolved)


#: The 409 body a model-settings write gets on a store a newer build wrote.
NEWER_FORMAT = {
    "kind": "newer_format",
    "detail": "A newer version of grimoire has changed this library's model "
              "settings. Update grimoire to change them here."}

#: The 400 a legacy inference key gets once the store is at format 2 (spec
#: 11.3): it would reach older builds only and change nothing here.
LEGACY_MOVED = "this setting moved to Models"


def refuse_newer() -> None:
    """409 `newer_format` when a newer build has written this store's model
    settings (spec 11.3): this build would overwrite settings it does not
    understand. Every model-settings write asks this first; play is not
    refused, because the frozen legacy keys still resolve."""
    if store.inference_keys.is_newer(store.read_config()):
        raise HTTPException(status_code=409, detail=NEWER_FORMAT)


def legacy_refused(exc: store.config.LegacyKeysRefusedError) -> HTTPException:
    """The answer to a legacy-key write the store refused in the hold that
    writes: 409 `newer_format` on a newer store, 409 `not_migrated` for a
    marked campaign on a store the migration has not finished
    (`refuse_unmigrated`'s answer), else 400 `LEGACY_MOVED`."""
    if exc.newer:
        return HTTPException(status_code=409, detail=NEWER_FORMAT)
    if exc.unmigrated:
        return _not_migrated()
    return HTTPException(status_code=400, detail=LEGACY_MOVED)


def _not_migrated() -> HTTPException:
    """The 409 `not_migrated`, carrying `migrate.status()`."""
    return HTTPException(status_code=409, detail={
        "kind": "not_migrated",
        "detail": "Model settings are being moved to the new layout. "
                  "Try again once that has finished.",
        "status": inference_migrate.status().as_dict()})


def refuse_unmigrated() -> None:
    """409 `not_migrated`, carrying the migration status, while the store's
    global layout is not the current one (spec 11.1, 12): a new-layout key
    written into a format-1 store is ignored by the resolver, so the write
    would answer 200 and change nothing. A newer store is refused as such
    first -- it is not waiting for a migration, this build cannot do it.

    The status is `migrate.status()`: `pending`, `running`, or `failed` with
    the reason (a safety backup that could not be taken)."""
    refuse_newer()
    if store.inference_keys.is_current(store.read_config()):
        return
    raise _not_migrated()


#: What a pricing link says while a model's own rates cannot be written: the
#: pricing table prices any model meanwhile. One sentence per reason, because
#: a store a newer build wrote is already past the upgrade and this version
#: will never write its rates -- "after the upgrade" would be untrue there.
RATES_AFTER_UPGRADE = ("A model's own rates can be set after the upgrade to the new model "
                       "settings; until then your pricing table can price it.")
RATES_NEWER_FORMAT = ("A newer version of grimoire wrote this library's model settings, so "
                      "this version cannot set a model's own rates; your pricing table can "
                      "still price it.")
RATES_UNREADABLE = ("A model's own rates cannot be set while the settings cannot be read; "
                    "your pricing table can price it.")

#: What stops `config.md` from being read for `rates_block`.
_CONFIG_UNREADABLE = (OSError, UnicodeDecodeError, ValueError, store.locks.StoreBusy)


def rates_block() -> str | None:
    """Why a model's own rates cannot be written now, as the sentence a pricing
    link carries, or None when they can -- the test `refuse_unmigrated` makes
    for `PUT .../facts`, so a "Set rates" link never opens an editor that
    cannot save. A store a newer build wrote (`RATES_NEWER_FORMAT`) and one the
    migration has not reached (`RATES_AFTER_UPGRADE`) are not current, and a
    link to the pricing table works on either. A `config.md` that cannot be
    read is blocked too (`RATES_UNREADABLE`), which costs the rates link and
    never sends anyone to a dead form."""
    try:
        meta = store.read_config()
    except _CONFIG_UNREADABLE:
        return RATES_UNREADABLE
    if store.inference_keys.is_newer(meta):
        return RATES_NEWER_FORMAT
    return None if store.inference_keys.is_current(meta) else RATES_AFTER_UPGRADE


def rates_editable() -> bool:
    """Whether a model's own rates can be written now (`rates_block`)."""
    return rates_block() is None


def _refuse_incapable(resolved: ResolvedInference) -> None:
    """The 409 for a primary KNOWN unable to do what its route needs
    (`inference.incapable`). Raised apart from the key check by
    `override_inference`, whose key refusals differ by what the body named."""
    _raise(inference.incapable(resolved))


def _refuse_unusable(resolved: ResolvedInference) -> None:
    """The 409 that says why this resolution cannot send, if it cannot
    (`inference.unusable`). Shared by the seam and by `override_inference`,
    whose `model`-only reroll drives the standing route and must be refused on
    exactly the same terms."""
    _raise(inference.unusable(resolved))


def require_inference(task: str = "", cid: str = "", *,
                      operation: str = "generate") -> UsableInference:
    """Where this generation runs, or a 409 saying why it cannot run anywhere.

    The one seam every LLM call site in `routes/` resolves through: the
    resolver (`store.inference.resolve`) answers, and this refuses what cannot
    send -- a missing key or connection first, then a primary known unable to
    do what the route needs (`inference.refusal`, pure, so the settings view
    reports the same decision). `.chain` is what the facade is sent, and
    `.chain.primary` the attempt a call site reads its display facts off.

    `task` is the same string the call site meters under (`store.usage.meter`),
    and `store/routing.py` maps it to a route; `cid` lets a campaign override
    that route. Both default to "unrouted", which resolves to the active
    connection -- but `test_routing_guard.py` fails a call site in `routes/`
    that leaves `task` off, so the default covers callers outside the route
    layer rather than being an option for one inside it. The same guard holds
    `operation` to the route's, and fails a reference to this function that is
    not a call (a task handed to it through `run_in_threadpool` is a literal
    the guard cannot see).
    """
    return _usable(inference.resolve(  # routing-ok: this IS the seam the guard watches
        task, cid, operation=operation))


def override_inference(body, task: str = "", cid: str = "", *,
                       operation: str = "generate") -> tuple[UsableInference, bool]:
    """Where ONE call runs, and whether that is somewhere it would not have gone
    anyway (#77).

    Returns `(resolved, routed)` — always the resolution that serves, never a
    sentinel the caller has to re-resolve:

        resolved, routed = override_inference(body, "regenerate", cid)
        chain = resolved.chain

    A tuple rather than "None means no override", which is what this was and
    which review broke in two ways at once. It could not say *both* "the caller
    named something" and "that something differs from the standing route", so
    the second meaning silently took over the first; and answering None for a
    named-but-identical connection made the caller re-read the active one,
    opening a window where another tab repointing it between the two reads
    served an explicitly pinned reroll from somewhere else entirely — the exact
    invisible substitution the refusals below exist to prevent.

    `routed` is compared on the provider and the EFFECTIVE model, not the
    stored one. They differ for exactly one kind: a `claude` connection with no
    model configured still runs `llm.CLAUDE_DEFAULT_MODEL`, so naming that
    model — which is what the picker now advertises in its placeholder — is
    naming the standing route, not leaving it. When the call names a preset
    (either layout), the preset the standing route would have run with is
    compared as well.

    The fields compose. `connection_id` (read as `provider`, which wins when
    both are sent) alone runs the named connection at its own model; `model`
    alone runs the STANDING connection at that model; both is the named
    connection driven at the named model. Neither expresses the other's case,
    which is why there are two: a bare model id cannot reach a different
    provider (the credentials, base URL and prompt post-processing that makes
    possible live on a connection), and a bare connection id cannot say "the
    same provider, its bigger model".

    In the roles format (spec 5.6) a provider alone keeps the STANDING model on
    the new provider -- a provider has no model of its own to fall back to --
    so with no standing selection it is a **400** asking for a model; a store
    still on the legacy keys keeps the meaning above. On either layout
    `preset` replaces the route's sampler preset for the primary attempt only
    (`PRESET_CLEAR` is "no preset"), and one that names no preset is a
    **400** as well: a store whose migration keeps failing stays on the legacy
    keys, and a preset the reader picked is not one to drop silently there.

    Seven refusals, and which one fires matters to the caller:

    - a model id longer than `alternates.MAX_MODEL_CHARS` is a **400**. Bounded
      where it arrives rather than at each place it is recorded: the same body
      string reaches the alternates sidecar, `prompt_log`'s index (read on every
      listing) and the usage ledger, and the latter two cannot be clamped
      downstream without changing what is actually sent.
    - a `preset` naming no preset is a **400**, checked
      before the connection's own refusals: the reroll would otherwise run on
      the route's preset and say nothing of the one the reader picked.
    - an id naming no connection is a **400**, not a 404. The routes that take
      an override are scene routes whose 404 already means "this scene is gone"
      and is acted on as such by the client (it stops the turn and re-reads the
      rail); spending the same status on a bad body field would send it
      hunting for a scene that is fine. An id that is not a safe path segment
      lands here too — `llm_connections` refuses it rather than joining it onto
      a path (#240), so it simply names no connection.
    - a provider alone, in the roles format, with no standing model to keep
      (no standing selection, or one on its provider's default model) is a
      **400** asking for a model. The body's fault, so it comes before the key.
    - a connection that cannot send is the same **409/missing_key**
      `require_inference` raises, because it is the same setup mistake and the
      frontend already routes that kind to the Connections page. Checked
      through the shared `store.inference.resolve.problem`, so an override is
      held to exactly the standard the standing connection is.
    - a model the override lands on that is KNOWN unable to do what the route
      needs is the same **409** `require_inference` raises for it
      (`_refuse_incapable`), checked after the key -- the override is held to
      the standing route's terms here too.
    - an override is NOT rescued by falling back to the standing connection
      when it is unusable. Quietly serving "reroll this on the local endpoint"
      from OpenRouter is the failure mode the explicit 409 exists to prevent,
      and it would be invisible: the reply reads like any other.

    The standing connection is deliberately not required when `connection_id`
    names another one. Rerolling onto a working local endpoint is exactly the
    thing to do while the OpenRouter key is missing, and requiring the standing
    one first would refuse the request that fixes the session. A `model`-only
    override does still require it — that is the connection it is overriding.

    `operation` is `require_inference`'s: a reroll generates, and an eval
    that forces a decide case onto another model (`evals/run.py --live
    --provider/--model`) resolves it as the decide operation its route is.

    What this does NOT change is the fallback (#144). An override picks which
    connection is *primary*; the fallback is the route's policy about what
    happens when a primary is exhausted, and silently suspending it for one
    call would make a reroll the one turn a rate limit can simply lose. The
    resolution carries it (`wire.Chain.fallback`), with the sampling it would have
    had without the override; a fallback on the override's own provider is
    dropped there (`llm._same_route`'s rule), so "reroll this on the fallback"
    does not double up. A task whose code policy is `fallback="none"`
    (`routing.TaskPolicy`) carries none, overridden or not.
    """
    conn_id, model, preset = _override_fields(body)
    if len(model) > store.alternates.MAX_MODEL_CHARS:
        raise HTTPException(
            status_code=400,
            detail="That is too long to be a model id — check it and try again.")
    # ONE resolution answers every question below: what serves, what it is
    # refused on, and where the call would have gone without the override
    # (`standing`). A second read -- the standing route first, then the
    # override resolved again -- opens a window in which the copy refused and
    # the copy served are different reads: a key cleared between them served a
    # keyless connection with no 409, and a repoint sent "the same provider,
    # its bigger model" to another provider entirely. The resolver reads each
    # connection once per resolution (`resolve.connection_lookup`), so
    # everything here is decided on the target that is handed to the facade.
    override = (inference_cascade.Selection(conn_id, model, preset)
                if conn_id or model or preset else None)
    resolved = inference.resolve(  # routing-ok: this IS the seam, for a per-call override
        task, cid, operation=operation, override=override)
    primary = resolved.attempts[0].target if resolved.attempts else None
    if preset and primary is not None and primary.sampling.scope != "override":
        # The preset was named and the resolver found nothing by that name (it
        # fell back to the route's), so the reroll would run on a preset the
        # reader did not pick. A body error, so before the connection's own
        # refusals below. (A StoreBusy or OSError swallowed by `_sampling` lands
        # here too, and is reported as a missing preset.)
        raise HTTPException(
            status_code=400,
            detail="That preset no longer exists — pick another, "
                   "or reroll with the route's.")
    _refuse_override_target(resolved, primary, conn_id=conn_id, model=model)
    # Then what the call needs, on the attempt that would serve it: an override
    # onto a model known unable to do the job is refused like the standing one.
    _refuse_incapable(resolved)
    served = _narrowed(resolved)
    return served, not _same_route(resolved, served, preset)


def _body_field(body, name: str) -> str:
    """One override field off the request body, stripped; "" when absent."""
    return (getattr(body, name, None) or "").strip()


def _override_fields(body) -> tuple[str, str, str]:
    """The body's `(connection id, model, preset)` for `override_inference`,
    each "" when not sent (or with no body at all). `provider` wins over
    `connection_id` when both are sent."""
    if not body:
        return "", "", ""
    conn_id = _body_field(body, "provider") or _body_field(body, "connection_id")
    return conn_id, _body_field(body, "model"), _body_field(body, "preset")


def _refuse_override_target(resolved: ResolvedInference, primary: wire.Target | None, *,
                            conn_id: str, model: str) -> None:
    """`override_inference`'s refusals of the connection an override lands on,
    in its order: the seam's own when no connection was named, else a gone
    connection, a provider alone with no standing model to keep, and a named
    connection that cannot send."""
    if not conn_id:
        # The seam's refusals, on the copy that serves. A model alone drives
        # the STANDING provider, so this is the standing route's own refusal:
        # `problem` does not depend on the model, and `via`/`route`
        # describe the standing choice -- a routed connection that cannot
        # send says so here too.
        _refuse_unusable(resolved)
        return
    if primary is None:
        # Written for the banner it lands in, not for a log. `errorText`
        # renders `detail` verbatim, and the reader's next move is the one
        # worth naming: the connection they picked is gone (deleted in
        # another tab, most likely), and the reroll is still available.
        raise HTTPException(
            status_code=400,
            detail="That connection no longer exists — pick another, "
                   "or reroll on the campaign's.")
    if not resolved.legacy and not model and not _has_standing_model(resolved, primary):
        # A provider alone keeps the standing model, and there is none to keep.
        # The body's fault, so before the connection's own refusals. (A
        # format-1 store's provider alone runs its own model, spec 5.6.)
        raise HTTPException(
            status_code=400,
            detail="Name a model for this provider — there is no standing model to keep.")
    problem = inference.target_problem(primary)
    if problem is not None:
        # NAMED, unlike `require_inference`'s copy of this. There the
        # connection is the one the whole app is using and needs no
        # introduction; here it is one the reader picked for a single
        # reroll, and the bare "OpenRouter key not set" reads as though
        # the connection they have been playing on has broken.
        raise HTTPException(
            status_code=409,
            detail={"detail": f"{primary.provider_name}: {problem}", "kind": "missing_key"})


def _same_route(resolved: ResolvedInference, served: UsableInference, preset: str) -> bool:
    """Whether the override serves where the call would have gone anyway --
    `override_inference`'s `routed`, negated."""
    standing = resolved.standing
    # Same provider means the same connection, so the standing model's
    # effective value is read on its kind with only the model swapped back.
    sent = served.chain.primary
    same = (standing is not None and sent.provider_id == standing.provider
            and sent.model == inference_facts.model_of({"kind": sent.kind,
                                                        "model": standing.model}))
    if same and preset:
        # The preset is the third thing a route is: the same provider and model
        # under another preset is another route. Effective on both sides, so
        # naming the preset the route already runs (or "none" against a route
        # that runs none) is no override.
        same = served.attempts[0].preset_id == (resolved.standing_preset or "")
    return same


def _has_standing_model(resolved: ResolvedInference, primary: wire.Target) -> bool:
    """Whether a provider-only override on a current layout has a model to run:
    the standing selection's, or the provider IS the standing one (nothing to
    keep, nothing changed)."""
    standing = resolved.standing
    return standing is not None and (primary.provider_id == standing.provider
                                     or bool(standing.model))


def _soft_resolved(resolve: Callable[[], UsableInference]
                   ) -> tuple[UsableInference | None, str, str]:
    """A resolution that may fail without failing its caller, or why it has
    none: `(None, reason, kind)` in place of the seam's raised refusal, and
    `(resolved, "", "")` when it resolved.

    `reason` is the refusal's sentence, which names the provider and model;
    `kind` is its fixed vocabulary (`missing_key`, `incapable`, ...; "" for a
    refusal that carries none), which is what a log line may say.

    `_soft_inference` is this without the kind. A caller that wants the kind
    too reads it here -- the scene-break title, which runs only after a
    verdict has been stored, and whose failure must leave that verdict
    standing with no title rather than lose it. Takes a THUNK for
    `_soft_inference`'s reason: the task stays a literal at the call site,
    where `test_routing_guard.py` reads it.
    """
    try:
        return resolve(), "", ""
    except HTTPException as exc:
        detail = exc.detail
        if isinstance(detail, dict):
            return None, str(detail.get("detail")), str(detail.get("kind") or "")
        return None, str(detail), ""


def _soft_inference(resolve: Callable[[], UsableInference]
                    ) -> tuple[UsableInference | None, str]:
    """A SECONDARY absorb phase's resolution, or why it has none (#142).

    `(None, reason)` rather than a raised 409, because the three phases below
    each promise never to fail an absorb: a dossier refresh routed at a
    connection with no key is that phase reporting `failed` with a reason the
    inspector shows, not the extraction losing its result over a setting it does
    not use. The extraction itself keeps the 409 -- it is the phase whose failure
    is fatal anyway.

    Takes a THUNK rather than a task name, so the task stays a literal at the
    call site. `test_routing_guard.py` reads those literals, and a helper that
    forwarded `task` would hide all three calls from it behind one unroutable
    one -- which is how a routing map goes stale without anything failing.
    """
    resolved, why, _kind = _soft_resolved(resolve)
    return (None, why) if resolved is None else (resolved, "")


def escalation_inference(task: str, cid: str = "") -> tuple[UsableInference | None, str]:
    """Where `task`'s escalation hop runs (roadmap 01d §5.2), or why it has
    none: `(resolved, "")`, or `(None, sentence)` -- never a raised 409.

    The seam a decide call site's escalator stands on, and the only one:

        escalation=lambda: run_in_threadpool(
            lambda: common.escalation_inference("continuity-identity", cid))

    It resolves the task through its code policy's `escalate_to` ROLE
    (`resolve(..., role=)`), so the hop sends that role's own selection and
    its own sampling preset -- never the decide route's, which was chosen
    for the route's model. It holds that role's model to the base route's
    `requires` (`escalation_refusal`, whose `incapable` sentence names the
    route) and refuses a missing key as the seam does, both through
    `_soft_resolved`: a lost second opinion costs only itself, so a refusal
    is the sentence every triggered item is skipped with, never a third way
    of failing softly.

    A task whose policy does not escalate to a role (none, or
    `routing.CALLER`, which brings its own resolver) is a `ValueError`: a
    caller bug, which `test_routing_guard.py` also fails statically. That
    guard holds every call to a literal task, as it holds
    `require_inference`'s; the thunk is what keeps the literal at the call
    site and the file reads in the threadpool."""
    policy = store.routing.policy(task)
    if policy.escalate_to not in store.routing.ESCALATION_ROLES:
        raise ValueError(f"{task!r}'s policy escalates to no role "
                         f"({policy.escalate_to!r}); there is no hop to resolve")

    def held() -> UsableInference:
        got = inference.resolve(  # routing-ok: the escalation seam, the policy's own role
            task, cid, operation="decide", role=policy.escalate_to)
        _raise(inference.escalation_refusal(got, task))
        return _narrowed(got)

    resolved, why, _kind = _soft_resolved(held)
    return resolved, why


def _decide_error(decision: decisions.Decision, qid: str) -> LLMError | None:
    """The provider error a decided batch must report as its own failure, or
    None when there is none to report (M12).

    None when some item's `qid` answer `was_read` -- the batch answered
    something, and a chunk that failed beside it leaves only its own items
    unanswered -- or when no item's answer carries reason ``error``, so
    nothing failed and the call site reads the batch as it is. Otherwise every
    chunk failed or was garbled, and at least one failed: the first failed
    chunk's own error (`Decision.errors`, the one `decide` itself raises when
    no chunk answered), so a chunk error beside a garbled chunk is reported as
    the error it was, never as an unreadable reply.

    It is read off the decision rather than watched through `around` because
    only `decide` knows which call was a chunk's last word: a chunk whose
    routes refused the structured field is re-sent once per refusing route
    (spec M-4), so a re-send that failed may be followed by one that answered
    (that chunk did not fail), and a chunk whose re-sends all failed reports
    every route's failure composed, which no single call raised (M1, M2). An
    `LLMError` keeps the status and `retry_after` the same error has when it
    fails the whole call; `BudgetRefused`, never sent, is one too."""
    answers = [result.answers.get(qid) for result in decision.items]
    if any(a is not None and decisions.was_read(a) for a in answers):
        return None
    if not any(a is not None and a.reason == "error" for a in answers):
        return None
    first = decision.errors[0] if decision.errors else None
    return first if isinstance(first, LLMError) else LLMError("error")
