"""config: providers responsibilities."""

from __future__ import annotations

import asyncio
import unicodedata
import uuid
from typing import Literal

from fastapi import Depends, Header, HTTPException, Request
from pydantic import BaseModel

from ... import catalog, health, llm, llm_sampling, store
from ...llm import LLMClient
from ...llm_errors import LLMError
from ...store import inference_retired as retired
from ...store.inference import capabilities, controls, facts, limits, providers
from ...store.inference import resolve as inference
from ...store.inference import settings as inference_settings
from .. import runs
from ..common import (
    NEWER_FORMAT,
    _dump,
    _llm_http_error,
    get_health,
    get_llm,
    refuse_newer,
    refuse_unmigrated,
    run_error,
)
from ..models import CatalogProbe, ConnectionCreate, ConnectionUpdate, FactsUpdate
from . import _probes
from ._router import router as router


# ---- llm connections ----
def _picker_models(conn: dict) -> dict:
    """A connection detail as a picker reads it: only the models a chat picker
    may offer. The sidecar keeps every row (embedding-only ones included) for
    the readers that want them; this is the one place the detail is narrowed."""
    return {**conn, "models": catalog.listable(conn["models"])}


def _with_effective(conn: dict) -> dict:
    """One connection as the client needs it: plus the model it will actually
    run on.

    `GET /config` has always reported this for the ACTIVE connection, because
    the status bar names the model every scene will use and a `claude`
    connection with none configured still generates on the dispatcher's
    substitute. Every other connection reported its raw stored model, so the
    reroll route picker (#77) — which has to tell the reader what an empty
    model box will run for a connection that is not the active one — briefly
    carried a copy of the effective-model rule AND of
    `CLAUDE_DEFAULT_MODEL`, pinned by a test that scraped a `.tsx` file with a
    regex. Reporting the answer here deletes the rule, the constant and the
    scrape together, and a fourth kind that substitutes a model is then one
    change in `facts.model_of` -- the rule every target's model is built by --
    rather than two in two languages. Only the model is read, so no target is
    built: that would parse the provider's catalog per connection listed.

    Added beside `model` rather than replacing it: the connection editor edits
    the stored value, and a form that round-tripped the effective one would
    write the substitute into the file the substitution exists to avoid needing.
    """
    return {**conn, "effective_model": facts.model_of(conn)}


@router.get("/llm-connections")
def get_connections(registry: health.ProviderHealth = Depends(get_health)):
    # Each entry carries what its provider last did (#146). On the list rather
    # than only on the detail read because the two places a reader chooses
    # between connections -- the Connections rail and the Configuration page's
    # picker -- both have a list and neither has a detail, and "key set" is
    # exactly the claim #146 is about.
    return [{**_with_effective(conn), "health": registry.status(conn["id"], conn["rev"])}
            for conn in store.llm_connections.list_connections()]


#: The provider fields written as text into the connection's frontmatter.
_TEXT_FIELDS = ("name", "base_url", "api_key", "model", "sampler_preset", "preset", "billing")


def _check_one_line(fields: dict) -> None:
    """400 for a provider text field holding a line boundary the frontmatter
    parser splits on (`frontmatter.breaks_line`, U+2028 included) or another
    control character: written, the rest of the line would be read back as a
    key of its own -- `kind`, `api_key`, anything."""
    for key in _TEXT_FIELDS:
        value = fields.get(key)
        if isinstance(value, str) and (
                store.frontmatter.breaks_line(value)
                or any(unicodedata.category(ch) == "Cc" for ch in value)):
            raise HTTPException(status_code=400, detail=(
                f"{key} must be one line of text, without control characters"))


def _check_preset_field(fields: dict, stored: str = "") -> None:
    """A connection's `sampler_preset` names a preset, or is blank -- refused on
    write and tolerated on read, as the Models settings write treats one. Here rather than in
    `llm_connections`, which `sampler_presets` imports: checking from the store
    side would close a cycle.

    A value equal to what is already `stored` is not a decision and is not
    checked: deleting a preset deliberately leaves connections naming it, and
    the editor resends every field on every save -- so checking it would make a
    connection whose preset was deleted impossible to rename."""
    pid = (fields.get("sampler_preset") or "").strip()
    if pid and pid != stored and not store.sampler_presets.exists(pid):
        raise HTTPException(status_code=400, detail=f"no such sampler preset: {pid!r}")


def _connection_sampling(conn_id: str) -> dict | None:
    """What this connection's OWN preset sends on it, for the editor's sidebar.

    Task-less, so a route's preset never shows here: the connection editor
    describes the connection, and each route row on the Models screen says
    what that route sends (`inference.own_target`). None for an unreadable
    connection."""
    try:
        conn = store.llm_connections.read_connection_raw(conn_id)
    except store.llm_connections.ConnectionNotFound:
        return None
    return llm_sampling.report(inference.own_target(conn))


#: The 400 for a legacy model field written to a provider at format 2.
MODEL_FIELD_MOVED = "set this on the model, not the provider"
#: The 400 for a base URL that differs from a locked preset's.
ADDRESS_FIXED = "this provider's address is fixed"


def _model_fields_refused(exc: store.llm_connections.ModelFieldsRefusedError) -> HTTPException:
    if exc.newer:
        return HTTPException(status_code=409, detail=NEWER_FORMAT)
    return HTTPException(status_code=400, detail=MODEL_FIELD_MOVED)


def _check_billing(value: str | None) -> None:
    if value and value not in providers.BILLINGS:
        raise HTTPException(status_code=400, detail=(
            f"billing must be one of {', '.join(providers.BILLINGS)}, not {value!r}"))


def _named_preset(pid: str, kind: str) -> providers.Preset:
    """The provider preset `pid`, on adapter `kind`, or a 400."""
    preset = providers.PRESETS.get(pid)
    if preset is None:
        raise HTTPException(status_code=400, detail=f"no such provider preset: {pid!r}")
    if preset.kind != kind:
        raise HTTPException(status_code=400, detail=(
            f"the {preset.label} preset is a {preset.kind} provider, not {kind}"))
    return preset


def _same_url(a: str, b: str) -> bool:
    return a.strip().rstrip("/") == b.strip().rstrip("/")


def _apply_preset(fields: dict) -> None:
    """A create body with its named preset applied, in place (spec 4.1, 6.1):
    the adapter must be the preset's; a locked preset's address is the one it
    has (a different one is a 400), and an editable one's pre-fills a blank
    one; billing defaults to the preset's.

    A body naming no preset is stamped with the one `providers.infer` reads it
    as, and that preset's billing unless the body names one. Its address is
    kept as given: what changes is that the preset is written at birth, so a
    locked one fixes the address from now on, and a later edit naming the same
    preset is judged against it rather than against a blank."""
    fields["preset"] = (fields.get("preset") or "").strip()
    fields["billing"] = (fields.get("billing") or "").strip()
    _check_billing(fields["billing"])
    if not fields["preset"]:
        inferred = providers.infer(fields)
        fields["preset"] = inferred.id
        fields["billing"] = fields["billing"] or inferred.billing
        return
    preset = _named_preset(fields["preset"], fields["kind"])
    url = (fields.get("base_url") or "").strip()
    if preset.url_locked and url and not _same_url(url, preset.base_url):
        raise HTTPException(status_code=400, detail=ADDRESS_FIXED)
    if preset.url_locked or not url:
        fields["base_url"] = preset.base_url
    fields["billing"] = fields["billing"] or preset.billing


def _apply_preset_update(fields: dict, stored: dict) -> bool:
    """An update body checked against the provider preset, in place; returns
    whether it repoints the connection at a newly named locked preset.

    A named preset must be on the connection's own adapter, and moving to a
    locked one points the connection at that preset's address. Under a locked
    preset (named here, or stored) a different address is a 400, and a blank
    one, the preset's own or the one already stored is no change -- so a
    connection stored with no address (an older OpenRouter one) is not
    "repointed", which would drop its key (`llm_connections._update`), and one
    whose stored address its preset's never matched (stamped by inference,
    at its create or by the migration) still saves when the editor resends it.

    A move is judged against the preset the stored connection is ON
    (`providers.infer`), never the string stored: a connection no build has
    stamped stores none, and naming the preset it is already on is a stamp,
    not a move."""
    _check_billing(fields.get("billing"))
    if "preset" in fields:
        fields["preset"] = (fields["preset"] or "").strip()
        if fields["preset"]:
            _named_preset(fields["preset"], stored["kind"])
    # A connection with no preset stored -- one no build stamped, or one whose
    # preset a body cleared -- is on the preset it infers (`providers.infer`),
    # which is what every reader reports it on; so that is the lock it is
    # judged against, not none. Otherwise a `preset: ""` riding along with a
    # new address would walk a locked connection past its lock, and every
    # later edit with it.
    pid = fields.get("preset") or stored.get("preset") or providers.infer(stored).id
    preset = providers.PRESETS.get(pid) if pid else None
    if preset is None or not preset.url_locked:
        return False
    url = fields.pop("base_url", None)
    if (url is not None and url.strip() and not _same_url(url, preset.base_url)
            and not _same_url(url, stored.get("base_url") or "")):
        raise HTTPException(status_code=400, detail=ADDRESS_FIXED)
    moved = bool(fields.get("preset")) and fields["preset"] != providers.infer(stored).id
    if moved and not _same_url(stored.get("base_url") or "", preset.base_url):
        fields["base_url"] = preset.base_url
        return True
    return False


@router.post("/llm-connections")
def post_connection(body: ConnectionCreate):
    refuse_newer()
    fields = _dump(body)
    _check_one_line(fields)
    try:
        # The preset check and the create in one model-settings hold
        # (`llm_connections.LOCK`, cross-process): a preset another server
        # deletes after the check cannot be named by the record it writes.
        with store.llm_connections.LOCK:
            _check_preset_field(fields)
            _apply_preset(fields)
            kind = fields.pop("kind")
            name = fields.pop("name")
            conn_id = store.llm_connections.create_connection(
                kind, name, refuse_model_fields=True, **fields)
    except store.llm_connections.ModelFieldsRefusedError as exc:
        raise _model_fields_refused(exc) from exc
    except retired.RecordUnreadableError as exc:
        # The create forgets a dead provider's record entry under the id it
        # claims, and the record cannot be read: nothing was written.
        raise HTTPException(status_code=409, detail={
            "kind": "retirement_unreadable",
            "detail": inference_settings.retirement_unreadable(exc)}) from None
    return {"id": conn_id}


@router.get("/llm-connections/{id}")
def get_connection(id: str, registry: health.ProviderHealth = Depends(get_health)):
    try:
        conn = _with_effective(_picker_models(store.llm_connections.read_connection(id)))
    except store.llm_connections.ConnectionNotFound as exc:
        raise HTTPException(status_code=404, detail="connection not found") from exc
    # The editor shows this beside the key it is about, which is the one place
    # a reader can act on it. Riding on the detail read rather than a route of
    # its own because it is never wanted without the rest: the panel that would
    # ask for it has already asked for this.
    # `used_by` reads every campaign's settings, so it is on the detail only.
    return {**conn, "health": registry.status(id, conn["rev"]),
            "sampling": _connection_sampling(id),
            "used_by": inference_settings.used_by(id)}


def _refuse_unconfirmed_reembed(before: dict, after: dict) -> None:
    """`put_connection`'s guard: 400 `confirm_embedding` for an edit that moves
    the Embedding role's vector space. Runs in the hold that writes, under
    `config_lock`, so the role it reads is the one the write lands beside."""
    if store.embed_space.moved_by(store.read_config(), before, after):
        raise HTTPException(status_code=400, detail={
            "kind": "confirm_embedding",
            "detail": inference_settings.EMBEDDING_MOVE_CONFIRM.format(
                provider=before.get("name") or before.get("id", ""))})


def _refuse_unconfirmed_facts(conn: dict, model: str) -> facts.Guard:
    """`put_connection_facts`' guard: 400 `confirm_embedding` for a facts
    write that moves the Embedding role's vector space -- turns it on, or
    changes its model's document-side embedding options (01h;
    `embed_space.facts_moved`). Runs in the hold that writes,
    under `config_lock`, so the role it reads is the one the write lands
    beside."""
    def guard(before: dict, after: dict) -> None:
        if store.embed_space.facts_moved(store.read_config(), conn["id"], model,
                                         before, after):
            raise HTTPException(status_code=400, detail={
                "kind": "confirm_embedding",
                "detail": inference_settings.EMBEDDING_FACTS_CONFIRM.format(
                    provider=conn.get("name") or conn["id"])})
    return guard


@router.put("/llm-connections/{id}")
def put_connection(id: str, body: ConnectionUpdate,
                   registry: health.ProviderHealth = Depends(get_health)):
    """Edit a provider. An edit that moves the Embedding role's vector space --
    a new key or address on the provider that role embeds through, which
    restamps its `rev` -- re-embeds the library, so it is refused with 400
    `confirm_embedding` unless the body says `confirm_embedding: true`
    (CLAUDE.md, "a settings surface never spends unasked"). Compared in the
    hold that writes (`update_connection`'s `guard`)."""
    refuse_newer()
    fields = {k: v for k, v in _dump(body).items() if v is not None}
    confirmed = fields.pop("confirm_embedding", None) is True
    _check_one_line(fields)
    try:
        # The read the checks are made against, the checks and the write in
        # one model-settings hold (`llm_connections.LOCK`, cross-process): the
        # preset decisions below are about the record as stored, and another
        # server's edit landing after this read would otherwise be judged as
        # the record it replaced.
        with store.llm_connections.LOCK:
            stored = store.llm_connections.read_connection_raw(id)
            _check_preset_field(fields, store.llm_connections.own_preset(stored))
            repointed = _apply_preset_update(fields, stored)
            before = stored["rev"]
            prefill_before = bool(stored.get("prefill"))
            try:
                # A preset move between two addresses on one host (z.ai's two
                # plans) stays on the account its key is for: the key is kept
                # unless the body gives another. Any other repoint drops it.
                store.llm_connections.update_connection(
                    id, refuse_model_fields=True, keep_key_on_same_host=repointed,
                    guard=None if confirmed else _refuse_unconfirmed_reembed, **fields)
            except store.llm_connections.ModelFieldsRefusedError as exc:
                raise _model_fields_refused(exc) from exc
            after = store.llm_connections.read_connection_raw(id)
        # An edit invalidates the verdict as surely as a delete does: the
        # failure on record was this connection's *previous* key, base URL or
        # model, and keeping it would report the setting the reader just
        # changed as still broken -- exactly when they are watching to see
        # whether their fix took.
        #
        # Judged by whether the REV moved, not by which keys the body named:
        # the editor sends every field on every save, and a save that only
        # changed the sampler preset keeps the rev (`llm_connections
        # .SAMPLER_FIELDS`) -- the verdict is about the provider, which a
        # preset does not change.
        #
        # `prefill` is the one rev-neutral field that can earn a verdict: a
        # model that refuses a trailing assistant message fails the call over
        # the switch itself. It stays rev-neutral so the catalog survives the
        # toggle, and clears the verdict here instead -- otherwise unticking it
        # leaves the dot red until some later call happens to succeed.
        if after["rev"] != before or bool(after.get("prefill")) != prefill_before:
            registry.forget(id)
        # Inside the `try`, where it has always been: a connection deleted
        # between the write and this read is a 404, not a 500.
        fresh = store.llm_connections.read_connection(id)
        return {**_with_effective(_picker_models(fresh)), "health": registry.status(id, fresh["rev"]),
                "sampling": _connection_sampling(id)}
    except store.llm_connections.ConnectionNotFound as err:
        raise HTTPException(status_code=404, detail="connection not found") from err


@router.delete("/llm-connections/{id}")
def delete_connection_route(id: str, registry: health.ProviderHealth = Depends(get_health)):
    refuse_newer()
    try:
        store.llm_connections.delete_connection(id)
    except store.llm_connections.ConnectionNotFound as err:
        raise HTTPException(status_code=404, detail="connection not found") from err
    except retired.RecordUnreadableError as exc:
        # The retirement record, read before anything is written, cannot be
        # read: nothing was written -- no reference cleared, no file removed.
        # The sentence names the file and says whether waiting will do.
        raise HTTPException(status_code=409, detail={
            "kind": "retirement_unreadable",
            "detail": inference_settings.retirement_unreadable(exc)}) from None
    # Ids are slugs, and a slug is reusable: deleting "Endpoint" and creating
    # another connection by that name lands on the same id, which would
    # otherwise inherit the dead connection's last failure.
    registry.forget(id)
    return {"ok": True}


@router.post("/llm-connections/{id}/models/refresh", status_code=202)
def post_connection_models_refresh(
    id: str, request: Request, client: LLMClient = Depends(get_llm),
    x_grimoire_attempt: str | None = Header(default=None),
):
    """Re-fetch a saved connection's catalog from its own provider (#149).

    Every listable kind, not just custom endpoints. The picker used to fetch
    OpenRouter's catalog from the browser against a hardcoded URL whichever
    connection was open, so an OpenRouter connection's models came from
    OpenRouter whether or not that was the provider configured, and the key was
    never presented. Both halves are fixed by the fetch happening here.

    Detached like the other drafts, and the one whose subject is `global`: the
    catalog is stored beside the connection, which no world or campaign owns.
    Not itself a generation — it is a listing — but it is a provider round trip
    against a host that may be slow or unreachable, which is the same reason
    the others could not survive a locked phone.

    The cache write happens in the RUN, after the fetch, which is what makes
    backgrounding this survivable: the point of the refresh is the sidecar it
    leaves behind, so a client that never comes back still gets it.

    The cache is model settings -- capability resolution reads it -- so a
    newer store is refused here (409 `newer_format`), and again in the hold
    the run writes in (`set_cached_models`), where a switch landing after
    this check is answered as the same refusal on the run.
    """
    refuse_newer()
    try:
        conn = store.llm_connections.read_connection_raw(id)
    except store.llm_connections.ConnectionNotFound as err:
        raise HTTPException(status_code=404, detail="connection not found") from err
    if conn["kind"] not in llm.LISTABLE_KINDS:
        raise HTTPException(status_code=400, detail="model listing not supported for this connection kind")
    rev = conn["rev"]
    # Minted HERE rather than left to `reserve_draft`, which would mint the same
    # thing but keep it to itself: the work has to stamp the sidecar with the
    # attempt that wrote it, so the id has to be known before the closure is
    # built. `reserve_draft` uses a non-None id verbatim, so this is the run's.
    attempt = x_grimoire_attempt or uuid.uuid4().hex

    async def work():
        try:
            models = await client.list_models(inference.provider_target(conn))
        except LLMError as exc:
            return {"state": "failed", "error": run_error(_llm_http_error(exc))}
        fetched_at = store.now_iso()
        # The only durable trace a draft leaves anywhere, and it exists for one
        # question: a client whose run was reaped asks the store whether ITS
        # refresh landed. "Newer than it was" cannot answer that -- a second tab
        # refreshing the same connection moves the timestamp too.
        try:
            # Off the loop: the write waits on the cross-process hold.
            await asyncio.to_thread(store.llm_connections.set_cached_models,
                                    id, models, rev, attempt=attempt)
        except store.config.NewerFormatError:
            return {"state": "failed",
                    "error": run_error(HTTPException(status_code=409, detail=NEWER_FORMAT))}
        return {"state": "landed",
                "result": {"models": catalog.listable(models), "fetched_at": fetched_at, "rev": rev}}

    return runs.run_draft(request.app, runs.GLOBAL_SUBJECT, "models-refresh",
                          attempt, work)


@router.post("/model-catalog")
async def post_model_catalog(body: CatalogProbe, client: LLMClient = Depends(get_llm)):
    """The catalog for a connection that has been *described* but not saved.

    Its counterpart above answers for a stored connection and caches what it
    gets; this one answers for a form the reader is still filling in and caches
    nothing — there is no `rev` to tag a cache entry with, and the next
    keystroke in the base-URL field would invalidate it anyway.

    Not merged into the route above as an optional body, though the fetch is
    the same one: the two differ in every other respect. One takes credentials
    off disk and the other off the wire, one writes a sidecar and the other
    must not, and one 404s for an id that does not exist while the other has no
    id to be wrong about.
    """
    conn = {**_dump(body), "model": ""}
    if conn["kind"] not in llm.LISTABLE_KINDS:
        raise HTTPException(status_code=400, detail="model listing not supported for this connection kind")
    try:
        return {"models": catalog.listable(
            await client.list_models(inference.provider_target(conn)))}
    except LLMError as exc:
        raise _llm_http_error(exc) from exc


@router.get("/providers/presets")
def get_provider_presets():
    """The provider presets a new provider is made from (spec 6.1), in table
    order, each with whether its health check generates (and so needs
    `confirm`)."""
    return [{**capabilities.preset_body(p), "generating_check": p.kind in _probes.GENERATING_CHECK_KINDS}
            for p in providers.PRESETS.values()]


# ---- inference read APIs: what a model can do, and what a preset sends ----
class InferenceControlsBody(BaseModel):
    """A sampler preset previewed on a provider's model. Defined beside its one
    route, not in `models.py`, so this block stays in one place. `preset_id` ""
    is no preset (provider defaults); `model` "" is the provider's own.
    `operation` "decide" previews it as a decision, which a model answered
    natively takes no sampling for; anything else previews generation."""

    preset_id: str = ""
    provider: str
    model: str = ""
    operation: str = ""


@router.get("/llm-connections/{conn_id}/capabilities")
def get_connection_capabilities(
        conn_id: str, need: Literal["generate", "vision", "embed", "decide", "tools"] = "generate",
        model: str = ""):
    """The connection's models grouped for a role that needs `need`
    (`capabilities.grouped`): every catalog row, embedding-only ones included
    (the Embedding picker lists those), so this is not narrowed by
    `catalog.listable`. Calls no provider and reserves no run.

    `conn_id`, never `cid`: `cid` is a campaign id in every other path, and
    the activity middleware reads it as one."""
    try:
        conn = store.llm_connections.read_connection_raw(conn_id)
    except store.llm_connections.ConnectionNotFound:
        raise HTTPException(status_code=404, detail="connection not found") from None
    return capabilities.grouped(conn, need, model or None)


def _facts_conn(conn_id: str) -> dict:
    try:
        return store.llm_connections.read_connection_raw(conn_id)
    except store.llm_connections.ConnectionNotFound:
        raise HTTPException(status_code=404, detail="connection not found") from None


def _facts_body(conn: dict, model: str) -> dict:
    """One model's facts on a provider (`facts.of`: verified results only for
    its current rev) and every capability as it resolves with them.

    Read strictly, as `GET /pricing` reads its table: a facts file that exists
    and cannot be read -- held by another program, or not parseable --
    answers `unreadable: true` with nothing stated, rather than reading as a
    model nothing was said of -- the panel must not offer a save over the
    user's word it could not read (the write would be refused anyway,
    `put_connection_facts`). `unreadable_reason` says which: `held` clears on
    its own, `mangled` needs the file fixed by hand.

    `embedding` is the model's embedding options block as stored (01h), and
    `embedding_invalid` says whether the validator refuses it -- a block that
    turns the Embedding role off for this model -- with
    `embedding_invalid_reason`, the validator's fixed sentence, when it does.

    `limits` is the model's size as a call would use it (01i): `limits.of`
    over the same cached row and facts a resolved attempt reads, each value
    `{value, source}` plus the catalog's own figure (`catalog`, null when the
    row states none), so a stated value the listing disagrees with shows
    beside it. The stated values themselves are the top-level
    `context_window` / `max_output` (from `facts.of`), which the form edits."""
    caps = capabilities.caps_for(conn, model)
    reason = ""
    try:
        known = facts.of(conn["id"], model, conn["rev"], strict=True)
    except facts.FactsUnreadableError as exc:
        known = facts.of(conn["id"], model, conn["rev"])
        reason = "mangled" if isinstance(exc, facts.FactsMangledError) else "held"
    row = store.llm_connections.cached_row(conn["id"], model)
    sizes, listed = limits.of(row, known), limits.of(row, {})
    try:
        facts.embed_options(known)
        options_invalid = ""
    except ValueError as exc:
        # A fixed sentence from the validator, never the block's own text.
        options_invalid = str(exc)
    body = {"provider": conn["id"], "model": model, **known, "unreadable": bool(reason),
            "embedding_invalid": bool(options_invalid),
            "capabilities": {n: capabilities.cap_body(c) for n, c in caps.items()},
            "limits": {name: {**limits.limit_body(getattr(sizes, name)),
                              "catalog": getattr(listed, name).value}
                       for name in ("window", "max_output")}}
    if reason:
        body["unreadable_reason"] = reason
    if options_invalid:
        body["embedding_invalid_reason"] = options_invalid
    return body


@router.get("/llm-connections/{conn_id}/facts")
def get_connection_facts(conn_id: str, model: str = ""):
    """What is known of `model` on this provider (spec 4.2): the user's
    statements, the test results for the provider's current rev, and what
    each capability resolves to. `model` blank is the provider's own."""
    conn = _facts_conn(conn_id)
    return _facts_body(conn, model.strip() or facts.model_of(conn))


#: A facts write that could not read the file it merges onto (`facts
#: .FactsUnreadableError`): refused rather than written over the models it
#: could not see.
FACTS_UNREADABLE = ("This provider's model facts could not be read just now "
                    "(another program may hold the file); nothing was saved. Try again.")

#: A facts write onto a file that was read and does not parse (`facts
#: .FactsMangledError`): refused as 409 `facts_unreadable` rather than written
#: over every other model's rates, verdicts and overrides. Unlike the 503 it
#: will not clear on its own, so it says what a person has to do.
FACTS_MANGLED = ("This provider's model facts file is not valid JSON (it may have been "
                 "edited by hand); nothing was saved. Fix or remove the file, then try "
                 "again.")


@router.put("/llm-connections/{conn_id}/facts")
def put_connection_facts(conn_id: str, body: FactsUpdate,
                         registry: health.ProviderHealth = Depends(get_health)):
    """State `vision`, `prefill`, `post_process`, capability `overrides`
    (`{cap: "" | "yes" | "no"}`, "" removing one), `rates` (the model's own
    per-token price; `{}` removing it) and the model's size, `context_window`
    and `max_output` (01i: a positive int, `0` removing it), for one model; a
    field left out (or null) is left as it is. A stated max output above a
    stated window -- judged on the merged entry, in the hold that writes -- is
    a 400. A size spends nothing, moves no vector space and changes no health
    verdict, so it asks no `confirm_embedding` and forgets nothing. 400 for a value the store refuses -- a partial or
    unknown-field rate included -- before anything is written; 404 for a
    provider that does not exist -- or stopped existing before the write, which
    `facts.state` checks under the connection lock. The facts file it merges
    onto is never replaced: 503 while another program holds it (try again),
    409 `facts_unreadable` when it does not parse (a person fixes it).

    A model-settings write like any other (spec 11.2, 12): 409 `not_migrated`
    until the store is at format 2, where the resolver reads facts rather than
    the connection's legacy fields -- a write before then would answer 200 and
    change nothing, and the migration would merge the legacy values over it --
    and 409 `newer_format` on a store a newer build wrote.

    `embedding` states the model's embedding options (01h; `{}` removes
    them): a block the store refuses is a 400 naming why in a fixed sentence,
    before anything is written.

    A write that moves the Embedding role's vector space -- the user's
    `embed: yes` over a known `no` for the model that role embeds with, or a
    change of that model's document-side embedding options, moving back to
    none included -- embeds the library from scratch, so it is refused with
    400 `confirm_embedding` unless the body says `confirm_embedding: true`
    (CLAUDE.md, "a settings surface never spends unasked"), compared in the
    hold that writes (`facts.state`'s `guard`). A query-side-only options
    change (a query prefix alone) moves nothing and asks nothing."""
    refuse_unmigrated()
    conn = _facts_conn(conn_id)
    fields = _dump(body)
    confirmed = fields.pop("confirm_embedding", None) is True
    model = (fields.get("model") or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="name a model")
    if len(model) > store.alternates.MAX_MODEL_CHARS:
        raise HTTPException(status_code=400, detail="model id is too long")
    refuse = None if confirmed else _refuse_unconfirmed_facts(conn, model)
    prefill_moved: list[bool] = []

    def guard(before: dict, after: dict) -> None:
        # Always runs, confirmed or not: it is also how this route learns, from
        # the very hold that writes, whether the write moved `prefill`.
        if refuse is not None:
            refuse(before, after)
        prefill_moved.append(bool(after["prefill"]) != bool(before["prefill"]))

    try:
        facts.state(conn_id, model, vision=fields.get("vision"),
                    prefill=fields.get("prefill"), post_process=fields.get("post_process"),
                    overrides=fields.get("overrides"), rates=fields.get("rates"),
                    context_window=fields.get("context_window"),
                    max_output=fields.get("max_output"), embedding=fields.get("embedding"),
                    guard=guard)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except store.llm_connections.ConnectionNotFound:
        raise HTTPException(status_code=404, detail="connection not found") from None
    except facts.FactsMangledError:
        # Read, and not a facts document: a write would replace every other
        # model's word with this one entry. Nothing was written, and a retry
        # will not help until a person fixes the file.
        raise HTTPException(status_code=409, detail={
            "kind": "facts_unreadable", "detail": FACTS_MANGLED}) from None
    except facts.FactsUnreadableError:
        # Held by another program: nothing was written, and a retry may land.
        raise HTTPException(status_code=503, detail=FACTS_UNREADABLE) from None
    # `put_connection`'s rule for the legacy field, narrowed to the model's
    # fact (user ruling 2026-10-09): `prefill` is what a refused trailing
    # assistant message earns a verdict over, and it is rev-neutral, so a
    # change to it must forget that verdict -- otherwise unticking it leaves
    # the dot red until some later call happens to succeed. Only a verdict
    # earned on THIS model, and of a kind its facts can change: one about
    # another model, or about the provider (`auth`, `missing_key`, ...), is
    # still true (`ProviderHealth.forget_model`). Any other fact leaves it alone.
    if prefill_moved and prefill_moved[-1]:
        registry.forget_model(conn_id, model)
    return _facts_body(_facts_conn(conn_id), model)


@router.post("/inference/controls")
def post_inference_controls(body: InferenceControlsBody):
    """What sampler preset `preset_id` sends on `provider` serving `model`:
    `{requested, effective, controls}` (`controls.preview`), the same answer an
    attempt resolved for that provider, model and preset carries -- for
    `operation`, so a decision on a model answered natively is all `n/a`."""
    try:
        conn = store.llm_connections.read_connection_raw(body.provider)
    except store.llm_connections.ConnectionNotFound:
        raise HTTPException(status_code=404, detail="connection not found") from None
    if body.preset_id and store.sampler_presets.read_preset(body.preset_id) is None:
        raise HTTPException(status_code=404, detail="sampler preset not found")
    return controls.preview(body.preset_id, conn, body.model, body.operation)
