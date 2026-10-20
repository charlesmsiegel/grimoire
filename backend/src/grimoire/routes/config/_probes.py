"""config: probes responsibilities."""

from __future__ import annotations

import asyncio
from typing import NamedTuple

from fastapi import Depends, Header, HTTPException, Request

from ... import (
    decisions,
    embeddings,
    health,
    llm,
    llm_errors,
    llm_sampling,
    llm_usage,
    store,
    tool_calls,
    wire,
)
from ...llm import LLMClient
from ...llm_errors import LLMError
from ...store.inference import facts, providers
from ...store.inference import resolve as inference
from .. import runs
from ..common import _bounded_call, get_health, get_llm, refuse_newer
from ..models import HealthCheck, ModelTestPreview, ModelTestRun
from ._router import router as router

#: How long a health check may take, whatever `llm_call_budget` says (#146).
#: Generous enough for the Claude path, which spawns a CLI and generates a
#: word, and short enough that a wedged one gives the reader an answer rather
#: than a spinner. Not a setting: the thing it protects against is a setting
#: that can be turned off.
HEALTH_CHECK_CEILING = 45.0

#: How long one probe of the model test may take, whatever `llm_call_budget`
#: says -- the health check's reasoning (#146, #272) for the same kind of
#: button: the test dialog is somebody watching, `0` there means "no ceiling
#: at all", and a wedged Claude CLI (a subprocess, no transport bound) would
#: otherwise hold the run -- and, through it, `PUT /config/data-dir` -- for
#: good. Longer than the health check's: a probe generates a short reply
#: (and the vision one reads an image) rather than a single word. An overrun
#: is `timeout`, which `_halts` treats as answering for the probes after it.
MODEL_TEST_CEILING = 90.0


#: The adapters whose health check GENERATES (one short message, which a
#: subscription counts): sent only on `confirm: true` (spec 6.5, rule 1).
GENERATING_CHECK_KINDS: frozenset[str] = frozenset({"claude"})

#: The 400 an unconfirmed generating check gets, before anything is sent.
HEALTH_UNCONFIRMED = ("This check sends one short message and may use your "
                      "subscription — confirm to run it.")


@router.post("/llm-connections/{conn_id}/health")
async def post_connection_health(
    conn_id: str, body: HealthCheck | None = None, client: LLMClient = Depends(get_llm),
    registry: health.ProviderHealth = Depends(get_health),
):
    """Ask this connection's provider whether it can serve, right now (#146).

    **Always 200**, with the verdict in the body. The failure being reported is
    the *provider's*, and this request — "tell me about that connection" —
    succeeded in every case where there is something to tell: a 502 here would
    make a working health check indistinguishable from a broken one, and would
    put the frontend's error banner in front of the answer the reader asked
    for. A missing connection is still a 404, because that request could not be
    answered at all.

    A connection with nothing to check with is answered without a network call,
    from the same `inference.problem` rule that turns a keyless connection
    into a 409 on the generation routes: a request that is going to be rejected
    for having no credential teaches the reader nothing that the missing
    credential does not.

    The verdict is filed in the registry either way, so the status bar reflects
    the check for as long as it is the freshest thing known.

    A check that generates (`GENERATING_CHECK_KINDS`: the Claude subscription)
    is **refused unless `confirm` is JSON `true`** (`HEALTH_UNCONFIRMED`), and
    nothing is sent or recorded -- no client can spend on it unasked. The free
    checks take no confirmation.

    `conn_id`, never `cid`: the activity middleware reads `cid` as a campaign.
    """
    try:
        conn = store.llm_connections.read_connection_raw(conn_id)
    except store.llm_connections.ConnectionNotFound as exc:
        raise HTTPException(status_code=404, detail="connection not found") from exc
    if conn.get("kind") in GENERATING_CHECK_KINDS and (body is None or body.confirm is not True):
        raise HTTPException(status_code=400, detail=HEALTH_UNCONFIRMED)
    target = inference.provider_target(conn)
    problem = inference.problem(conn)
    if problem is not None:
        return _health_body(registry.record(target, LLMError("missing_key", problem)))
    try:
        # A ceiling of its own, NOT the one-shot generation budget (#272).
        # `llm_call_budget` supports `0` for "no ceiling at all", which is a
        # reasonable thing to ask of a slow local model and an unreasonable
        # thing to ask of a button: the HTTP probes carry a tight transport
        # bound, but the Claude path is a subprocess with no httpx client to
        # configure, so on that setting a wedged CLI would hold this request —
        # and the reader's spinner — open forever, on exactly the connection
        # they already suspect. Bounded here at a value nobody can switch off,
        # and an overrun arrives as `timeout`: a health verdict like any other.
        await _bounded_call(client.check(target), ceiling=HEALTH_CHECK_CEILING)
    except LLMError as exc:
        return _health_body(registry.record(target, exc))
    return _health_body(registry.record(target))


def _health_body(status: dict) -> dict:
    """One recorded verdict as the check route's answer.

    The registry's shape and the route's are deliberately not the same one.
    A status describes what is known about a connection *whenever* it is asked
    — hence `state`, which has an "unknown" — while a check has just happened
    and can only be a yes or a no, which is what `ok` says. `checked_at` is the
    same instant `at` names, spelled for a reader who just pressed the button.
    """
    return {"ok": status["state"] == health.OK, "kind": status["kind"],
            "detail": status["detail"], "checked_at": status["at"]}


# ---- the model test call (spec 6.4) ----
#: The 400 a test without `confirm: true` gets, before anything is sent or
#: metered. The standing rule is "always ask before spending money", and this
#: is the one route whose whole purpose is to spend some.
TEST_UNCONFIRMED = ("This test sends a request to the provider and may cost money — "
                    "confirm to run it.")

#: The client the `embed` probe goes through: its own instance, as each store
#: module that embeds has one (`semantic._CLIENT`), so a test can put one over
#: a `MockTransport` in its place. A route module's, unlike those, so the
#: lifespan that serves this router closes its pool (`close_clients`).
_EMBEDDINGS = embeddings.EmbeddingsClient()


def close_clients() -> None:
    """Close this module's own connection pool (`_EMBEDDINGS`); called by the
    app's lifespan on the way out. It reopens on its next use."""
    _EMBEDDINGS.close()


def _test_plan(conn_id: str, body: ModelTestPreview | ModelTestRun) -> tuple[dict, str, tuple[str, ...]]:
    """`(connection, model, capabilities)` for a test or its preview, or the
    refusal that costs nothing to make -- each before any request is built.

    Shared, so the preview can never show a confirmation for a test the run
    would refuse: an unknown connection (404); no capabilities, or one with no
    probe (400); one the provider's preset rules out (`never`, 400 -- the wire
    protocol cannot, so a test would be money spent learning what is already
    known); no model (400); and a connection that cannot send at all (the
    seam's 409 `missing_key`)."""
    try:
        raw = store.llm_connections.read_connection_raw(conn_id)
    except store.llm_connections.ConnectionNotFound as exc:
        raise HTTPException(status_code=404, detail="connection not found") from exc
    probes = store.inference.probes
    if not body.capabilities:
        raise HTTPException(status_code=400, detail="name at least one capability to test")
    untestable = sorted({c for c in body.capabilities if c not in probes.PROBES})
    if untestable:
        raise HTTPException(status_code=400, detail=(
            f"there is no test for {', '.join(untestable)}; testable: "
            f"{', '.join(probes.ordered(probes.PROBES))}"))
    caps = probes.ordered(body.capabilities)
    preset = store.inference.providers.infer(raw)
    ruled_out = [c for c in caps if c in preset.never]
    if ruled_out:
        raise HTTPException(status_code=400, detail=(
            f"{preset.label} cannot do {', '.join(ruled_out)} on any model, so there "
            "is nothing to test"))
    # The model the record runs (`facts.model_of`, a pure rule): nothing is
    # built before the refusals below and the run's `confirm` refusal.
    model = body.model.strip() or facts.model_of(raw)
    if not model:
        raise HTTPException(status_code=400, detail="name a model to test")
    if len(model) > store.alternates.MAX_MODEL_CHARS:
        raise HTTPException(status_code=400, detail="model id is too long")
    problem = inference.problem(raw)
    if problem is not None:
        raise HTTPException(status_code=409, detail={"detail": problem, "kind": "missing_key"})
    return raw, model, caps


def _probe_target(raw: dict, model: str) -> wire.Target:
    """The target every probe of a test of `model` on `raw` is sent on (and
    its preview describes): the probe's reply cap and no preset of the
    connection's own (`probes.sampling`), with the model's facts as a target
    outside any route sends them (`inference.facts_for`) -- built by the
    resolver's own builder (`inference.target_for`)."""
    return inference.target_for(raw, model, store.inference.probes.sampling(),
                                model_facts=inference.facts_for(raw, model))


def _tools_choice(raw: dict, target: wire.Target) -> str:
    """The `tool_choice` the tools probe asks of `target` (`probes.tool_choice`),
    from the provider's preset, the model's catalog features and the thinking
    the probe's body really sends -- one answer for the preview and the run,
    so the confirmation describes the call that goes out."""
    sent = llm_sampling.effective(target)["effective"].get("thinking")
    return store.inference.probes.tool_choice(providers.infer(raw).id, target.kind,
                                              target.model_features, sent, target.model)


def _embed_endpoint(raw: dict) -> str:
    """Where the `embed` probe goes: the endpoint the Embedding path resolves
    for this provider -- OpenRouter's fixed URL (its adapter ignores a stored
    one), else the connection's own base URL. The kinds with no embeddings
    route never get here: their preset's `never` refused the test. On a
    legacy-format store this can record `embed: yes` for an OpenRouter model
    that the Embedding role will still not use (OpenRouter embeddings are
    new-layout only); the explicit, confirmed test call is allowed to probe it
    anyway."""
    if raw.get("kind", "openrouter") == "openrouter":
        return store.inference.providers.PRESETS["openrouter"].base_url
    return str(raw.get("base_url") or "")


#: What the embed probe reports when its model's embedding options cannot be
#: judged (01h): it sends nothing, since what it sent would not be what the
#: Embedding role sends. Neither a verdict nor a halt.
_OPTIONS_NOT_SENT = {
    "held": ("options_unreadable", ("Not sent: this provider's model facts file is held "
             "by another program, so the model's embedding options could not be read.")),
    "mangled": ("options_unreadable", ("Not sent: this provider's model facts file is not "
                "valid JSON, so the model's embedding options could not be read.")),
    "invalid": ("options_invalid", ("Not sent: the model's embedding options are invalid; "
                "fix them in its facts first.")),
}


async def _embed_probe(raw: dict, target: wire.Target, model: str,
                       options: wire.EmbedOptions | None = None) -> dict:
    """The `embed` probe: one fixed string, once, metered under `model-test`
    with `operation: "embed"`, on the provider under test (ruling 8: it is not
    an embed task and resolves no role).

    The holder is stamped as `llm._stamp` stamps a chat attempt, which is what
    makes the meter file a row at all (an unstamped holder means "never
    sent", `store.usage.sent`),
    and the client folds into it whatever counts the endpoint reports (a
    prompt count it did not report is estimated, `embed.estimate_prompt`).
    What served it -- the provider id and the `embed` operation -- is filed
    from the probe's `target` through `llm_usage.account` (slice E, M9). A
    failure is finished as the embed operation finishes one
    (`inference.embed.record_failure`): its recorded detail is the kind and HTTP status
    only, because a redirect's `Location` can carry a key. The verdict still
    reads the exception's own detail (`_probe`, through `probes.scrub`).

    `options` are the model's stated embedding options, read strictly
    (`inference.strict_embed_options`, in `_probe`): the probe sends what the
    Embedding role would, so a confirmed test verifies them (01h §3.4)."""
    probes = store.inference.probes
    # `model=` because the client owns the holder's `model` key: it clears it
    # at entry and refills it only with what the endpoint named.
    with store.usage.meter("model-test", model=model) as m:
        m.usage.update({"model": model, "connection": raw.get("name") or raw["id"],
                        "provider": raw.get("kind", "openrouter"), "attempts": 1,
                        "requested_model": model})
        llm_usage.account(m.usage, target.with_account(operation="embed"))
        try:
            # Off the loop: the embeddings client is synchronous by design.
            vectors = await asyncio.to_thread(lambda: _EMBEDDINGS.embed(
                [probes.EMBED_TEXT], model, raw.get("api_key", ""), _embed_endpoint(raw),
                usage=m.usage, options=options))
        except Exception as exc:
            store.inference.embed.record_failure(m, exc)
            raise
        # A prompt count the endpoint did not report is estimated locally, as
        # the embed operation estimates one (`embed.estimate_prompt`) -- in a
        # worker too, since a loaded encoder counts synchronously.
        await asyncio.to_thread(store.inference.embed.estimate_prompt, m.usage,
                                embeddings.prepare_inputs([probes.EMBED_TEXT], options, 0))
    return {"ok": True, "dims": len(vectors[0])}


#: The failure kinds that answer for every probe still to be sent: the
#: credential was refused or is missing, the SDK is not installed, or the
#: provider could not be reached in time. Whatever was asked next would fail
#: the same way and say no more, so the rest are not sent (`_halts`).
_HALTING_KINDS = frozenset({"auth", "missing_key", "missing_dependency", "network", "timeout"})

#: The HTTP statuses that do the same: out of credits (402) and a request the
#: server timed out (408). Every 5xx joins them in `_halts` -- the provider
#: failing, which no other probe sent to it would get past.
_HALTING_STATUSES = frozenset({402, 408})


def _records(exc: LLMError, *, native: bool = False) -> bool:
    """Whether a probe's failure is a verdict on the MODEL, and may be filed.

    Only a provider refusing THIS request (`llm.REJECTED_STATUSES`, the set
    `_resilient` already reads as "refused what it was sent") says that. A
    filed failure is shown on the model's row as unverified with its error
    (`capabilities._stated`), outranking the catalog, so everything else is
    reported to whoever asked and never filed: no status at all (a transport failure, a malformed stream), a 402,
    a 408, a 429, a 5xx. The chat adapters map most of those to
    `bad_response`, so the kind cannot make this call. Nor is an account
    limit (`llm_errors.account_limit`): a spend limit the user set answers a
    400, which is otherwise exactly the status a refusal has.

    Nor is a refusal of the probe's OWN setting. `llm._preset_refusal` reads a
    400 naming a parameter the request sent -- here the reply cap, the only
    one a probe sends -- as that parameter refused, and raises it as
    `llm.PresetRefusalError`; a provider that refused the cap has said nothing
    about whether the model can do what was asked.

    `native` is a native decision probe, whose endpoint also refuses with 403
    (`llm.NATIVE_REJECTED_STATUSES`, ruling 11): a key without access to that
    endpoint is a refusal of the probe, not a verdict on the key, which a
    chat probe's 403 would be."""
    refused = llm.NATIVE_REJECTED_STATUSES if native else llm.REJECTED_STATUSES
    return (exc.status in refused and not isinstance(exc, llm.PresetRefusalError)
            and not llm_errors.account_limit(exc))


def _halts(exc: LLMError) -> bool:
    """Whether a failure answers for every probe after it, so none is sent
    (`_HALTING_KINDS`, `_HALTING_STATUSES`, any 5xx, and an account limit --
    `llm_errors.account_limit`, the same test `_records` reads: a spend limit
    every further probe would hit and pay nothing to learn). A refusal, a
    refused cap, a rate limit or an unexplained bad response is this probe's
    own answer, and the next probe asks a different question."""
    return (exc.kind in _HALTING_KINDS or exc.status in _HALTING_STATUSES
            or (exc.status or 0) >= 500 or llm_errors.account_limit(exc))


class _Outcome(NamedTuple):
    #: What the run reports for the probe.
    result: dict
    #: Whether `result` is a verdict to file (`_records`).
    records: bool
    #: Whether no further probe is sent (`_halts`).
    halts: bool


async def _probe(client: LLMClient, cap: str, raw: dict, target: wire.Target,
                 model: str) -> _Outcome:
    """One probe's outcome. Its result is `{"ok": True}` (plus `dims` for
    embed), or `{"ok": False, "kind", "error"}` with the error scrubbed of any
    picture and of the connection's key. One attempt, never retried, never
    fallen back. A success is always a verdict; a failure only when `_records`
    says so."""
    probes = store.inference.probes
    probe = probes.PROBES[cap]
    offer = probes.offer(cap, _tools_choice(raw, target)) if cap == "tools" else {}
    # Checked BEFORE the meter opens, so a refused offer never reaches one.
    # A holder carrying only the collector is not a sent call either
    # (`store.usage.sent`: `tool_calls.KEY` is a pre-send key), so a refusal
    # after it is installed would file no row; this keeps the order plain.
    tool_calls.check(offer.get("tools"), offer.get("tool_choice"))
    try:
        if probe.operation == "embed":
            _known, options, why = await asyncio.to_thread(
                inference.strict_embed_options, raw, model)
            if why:
                kind, error = _OPTIONS_NOT_SENT[why]
                return _Outcome({"ok": False, "kind": kind, "error": error}, False, False)
            return _Outcome(await _embed_probe(raw, target, model, options), True, False)
        with store.usage.meter("model-test") as m:
            # Completed is accepted; the text is not read. The row names the
            # probe's operation (M9) on a new target: `target` serves every
            # probe (`_probe_target`).
            if probe.operation == "decide":
                # One native request, one attempt: any `ItemResult` is a body
                # the endpoint accepted and the adapter normalised. The row
                # says it was native, which the ledger never models.
                await _bounded_call(client.decide_native(
                    probes.PROBE_ITEM,
                    target.with_account(operation=probe.operation,
                                        decision_mode=decisions.NATIVE_BACKEND),
                    m.usage, retries=0), ceiling=MODEL_TEST_CEILING)
            else:
                calls = _collecting(m.usage) if offer else None
                await _bounded_call(client.single(
                    probes.messages(cap),
                    target.with_account(operation=probe.operation),
                    m.usage, **offer), ceiling=MODEL_TEST_CEILING)
                if calls is not None and not calls.called:
                    return _missed_call(calls)
    except embeddings.DimensionsMismatchError as exc:
        # A verdict on the model's stated options at this endpoint (01h §4.4):
        # filed as a failed `embed` probe -- `unknown`, never `no` -- with both
        # widths, so the panel can say the endpoint ignores the field. It
        # halts nothing: the other probes ask other questions.
        return _Outcome({"ok": False, "kind": exc.kind, "code": exc.code,
                         "error": exc.detail, "requested_dims": exc.requested_dims,
                         "returned_dims": exc.returned_dims}, True, False)
    except LLMError as exc:
        return _Outcome({"ok": False, "kind": exc.kind,
                         "error": probes.scrub(exc.detail, [str(raw.get("api_key") or "")])},
                        _records(exc, native=probe.operation == "decide"), _halts(exc))
    return _Outcome({"ok": True}, True, False)


def _collecting(usage: dict) -> tool_calls.Collector:
    """A fresh collector in `usage`, which the facade keeps across its stamp,
    for the tools probe to read the reply's calls from."""
    calls = tool_calls.Collector()
    usage[tool_calls.KEY] = calls
    return calls


def _missed_call(calls: tool_calls.Collector) -> _Outcome:
    """The tools probe's outcome when the reply called nothing. Cut by the
    cap first (`length`), it says nothing about the model: reported, never
    filed (`_records`' rule). Otherwise the model answered without calling,
    which is a failed test -- filed, and read as `unknown`, never `no`."""
    probes = store.inference.probes
    if calls.finish_reason == "length":
        return _Outcome({"ok": False, "kind": "capped", "error": probes.CAPPED}, False, False)
    return _Outcome({"ok": False, "kind": "no_tool_call", "error": probes.NO_CALL}, True, False)


#: What a probe's filed verdict keeps of its result: `dims` from a passed
#: `embed` probe, and both widths from one whose endpoint ignored a requested
#: `dimensions` (01h §4.4).
_VERDICT_KEYS = ("ok", "error", "dims", "requested_dims", "returned_dims")


async def _probe_all(client: LLMClient, caps: tuple[str, ...], raw: dict,
                     target: wire.Target, model: str) -> tuple[dict[str, dict], dict[str, dict]]:
    """`(results, verdicts)` for `caps`, probed in order.

    After a failure that answers for every probe (`_halts`) the rest are NOT
    sent -- each would be a request, and maybe money, spent learning what the
    first already said. They are reported as `not_sent`, naming that failure,
    and nothing is filed for them."""
    results: dict[str, dict] = {}
    verdicts: dict[str, dict] = {}
    stopped: str | None = None
    for cap in caps:
        if stopped is not None:
            results[cap] = {"ok": False, "kind": "not_sent", "error": f"not sent: {stopped}"}
            continue
        outcome = await _probe(client, cap, raw, target, model)
        results[cap] = outcome.result
        if outcome.records:
            verdicts[cap] = {k: outcome.result[k] for k in _VERDICT_KEYS
                             if k in outcome.result}
        if outcome.halts:
            stopped = f"the {cap} probe failed first ({outcome.result['error']})"
    return results, verdicts


def _record_verdicts(conn_id: str, model: str, rev: str, verdicts: dict[str, dict]) -> bool:
    """File `verdicts` under the `rev` the test STARTED on, unless that rev
    has moved or the connection is gone: an edit that landed while the probes
    were out describes a different endpoint, and its verdict would replace
    whatever the new rev already holds. That compare-and-write is
    `facts.record_verified`'s, under the lock every connection write holds --
    a rev read here, before it, would leave an edit room to land between.
    `verdicts` holds only what `_records` let through. Whether anything was
    filed is the answer."""
    if not verdicts:
        return False
    try:
        return store.inference.facts.record_verified(conn_id, model, rev, verdicts)
    except (store.locks.StoreBusy, OSError, UnicodeDecodeError):
        return False


@router.post("/llm-connections/{conn_id}/test/preview")
def post_connection_test_preview(conn_id: str, body: ModelTestPreview):
    """What `POST .../test` would send, and roughly what it would cost. Sends
    nothing, meters nothing, starts no run.

    `estimated_cost_usd` is the probes' stated token guesses
    (`probes.PROBES`) priced from the first of three sources that can price the
    whole test, and `estimate_basis` names it: `catalog` (the model's cached
    catalog prices, plus the vision probe's one image at the row's per-image
    price), else `rates` (the user's -- the model's own, else `pricing.json`,
    by `pricing.rate_for_call` -- with the vision probe priced from its token
    guess, as the ledger prices images). A catalog row that states token prices
    but no image price leaves only the catalog source unknown. Null with a null
    basis when none of them prices it -- "a price nobody reported is never
    rendered as zero"; the confirmation then says the cost is unknown.

    The user's rates are tried only for a provider that does not report its
    own price (`reports_price`): the ledger never prices such a provider's
    calls from them, since what it reports always wins, so a zero default
    meant for local models would otherwise read "≈ $0.00 at your rates" for a
    call that will be billed. Without a catalog price, such a provider's
    test is "cost unknown"."""
    raw, model, caps = _test_plan(conn_id, body)
    probes = store.inference.probes
    target = _probe_target(raw, model)
    capped = "max_tokens" in llm_sampling.sent_names(target)
    choice = _tools_choice(raw, target) if "tools" in caps else "required"
    # A missing or mangled sidecar is "no price known" (`cached_row` never
    # raises), not a failed preview.
    estimate = probes.estimate_usd(store.llm_connections.cached_row(conn_id, model), caps)
    basis = "catalog" if estimate is not None else None
    if estimate is None and not store.inference.providers.infer(raw).reports_price:
        entry = store.pricing.rate_for_call(
            store.pricing.read_pricing(), store.pricing.provider_rates(),
            provider_id=conn_id, model=model)
        estimate = probes.estimate_from_rates(entry, caps)
        basis = "rates" if estimate is not None else None
    return {"provider": raw.get("name") or conn_id, "provider_id": conn_id, "model": model,
            "sends": [{"capability": c, "description": probes.describe(c, capped, choice)}
                      for c in caps],
            "estimated_cost_usd": estimate, "estimate_basis": basis}


@router.post("/llm-connections/{conn_id}/test", status_code=202)
def post_connection_test(
    conn_id: str, body: ModelTestRun, request: Request, client: LLMClient = Depends(get_llm),
    x_grimoire_attempt: str | None = Header(default=None),
):
    """Test what `model` can do on this connection: one probe per capability.

    **Refused unless `confirm` is JSON `true`**, before anything is sent or
    metered (`TEST_UNCONFIRMED`) -- the preview is how a client learns what it
    is confirming. Every other refusal is `_test_plan`'s and comes first, so a
    confirmed request for something untestable is still told why.

    A `global` draft, the model-catalog refresh's twin: what it leaves behind
    is stored beside the connection, which no world or campaign owns, and a
    client that never comes back still gets it. Each probe is ONE attempt
    (`LLMClient.single`): no retry, no fallback, no text-only re-send, each
    metered under `model-test` with no campaign. The chat probes are sent
    through the connection as the resolver lowers it, carrying the probe's
    reply cap and no preset of the connection's own.

    Only what the model answered is a verdict: a success, or the provider
    refusing the probe itself (`_records`); an outage, a rate limit or a
    refused key is reported and not filed, and stops the probes after it when
    it answers for them too (`_halts`). The verdicts are filed in the run's
    terminal step under the `rev` captured here, unless it moved meanwhile
    (`_record_verdicts`).
    """
    refuse_newer()
    raw, model, caps = _test_plan(conn_id, body)
    if body.confirm is not True:
        raise HTTPException(status_code=400, detail=TEST_UNCONFIRMED)
    rev = raw["rev"]
    target = _probe_target(raw, model)

    async def work():
        results, verdicts = await _probe_all(client, caps, raw, target, model)
        # Off the loop, as `_embed_probe` sends: filing reads the connection
        # and writes the facts file under its lock, and the lifespan loop is
        # the one every other run streams through.
        recorded = await asyncio.to_thread(_record_verdicts, conn_id, model, rev, verdicts)
        return {"state": "landed",
                "result": {"provider": conn_id, "model": model, "rev": rev,
                           "results": results, "recorded": recorded}}

    return runs.run_draft(request.app, runs.GLOBAL_SUBJECT, "model-test",
                          x_grimoire_attempt, work)
