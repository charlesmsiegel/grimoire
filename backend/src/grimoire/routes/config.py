"""Application-wide settings: config, LLM connections, styles, response
presets and the global response scope, plus the calendar-provider and climate
catalogues that worlds and campaigns select from."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import llm, store
from ..llm_errors import LLMError
from ..openai_compatible import OpenAICompatibleClient
from . import runs
from .common import (
    _dump,
    _llm_http_error,
    _response_body,
    _write_response,
    get_openai_compatible_client,
)
from .models import (
    ConfigUpdate,
    ConnectionCreate,
    ConnectionUpdate,
    DataDirUpdate,
    PromptLayoutUpdate,
    ResponsePresetCreate,
    ResponsePresetUpdate,
    ResponseSettings,
    StyleCreate,
    StyleUpdate,
)

router = APIRouter()


# ---- config ----
def _public_config(cfg: dict[str, str]) -> dict:
    active = store.llm_connections.get_active()
    setup_done, first_run = _setup_state(cfg)
    return {"theme": cfg["theme"], "system_prompt": cfg.get("system_prompt", ""),
            "quote_color": cfg.get("quote_color", "off"),
            "user_label": cfg.get("user_label", "You"),
            "assistant_label": cfg.get("assistant_label", "Grimoire"),
            "llm_timeout": cfg.get("llm_timeout", store.config.DEFAULT_LLM_TIMEOUT),
            "absorb_budget": cfg.get("absorb_budget", store.config.DEFAULT_ABSORB_BUDGET),
            "absorb_concurrency": cfg.get("absorb_concurrency",
                                          store.config.DEFAULT_ABSORB_CONCURRENCY),
            "llm_call_budget": cfg.get("llm_call_budget",
                                       store.config.DEFAULT_LLM_CALL_BUDGET),
            "llm_retries": cfg.get("llm_retries", store.config.DEFAULT_LLM_RETRIES),
            "fallback_connection_id": cfg.get("fallback_connection_id",
                                              store.config.DEFAULT_FALLBACK_CONNECTION_ID),
            "context_budget": cfg.get("context_budget", store.config.DEFAULT_CONTEXT_BUDGET),
            "context_scan_depth": cfg.get("context_scan_depth", store.config.DEFAULT_SCAN_DEPTH),
            "archive_depth": cfg.get("archive_depth", store.config.DEFAULT_ARCHIVE_DEPTH),
            "prompt_log_depth": cfg.get("prompt_log_depth",
                                        store.config.DEFAULT_PROMPT_LOG_DEPTH),
            "turnstate_depth": cfg.get("turnstate_depth", store.config.DEFAULT_TURNSTATE_DEPTH),
            "promote_streak": cfg.get("promote_streak", store.config.DEFAULT_PROMOTE_STREAK),
            "rolling_summary_every": cfg.get("rolling_summary_every",
                                             store.config.DEFAULT_ROLLING_SUMMARY_EVERY),
            "scene_break_every": cfg.get("scene_break_every",
                                         store.config.DEFAULT_SCENE_BREAK_EVERY),
            "offscene_known_limit": cfg.get("offscene_known_limit",
                                            store.config.DEFAULT_OFFSCENE_KNOWN_LIMIT),
            "embeddings_connection_id": cfg.get("embeddings_connection_id",
                                                store.config.DEFAULT_EMBEDDINGS_CONNECTION_ID),
            "embeddings_model": cfg.get("embeddings_model", store.config.DEFAULT_EMBEDDINGS_MODEL),
            "semantic_recall_depth": cfg.get("semantic_recall_depth",
                                             store.config.DEFAULT_SEMANTIC_RECALL_DEPTH),
            "semantic_recall_threshold": cfg.get("semantic_recall_threshold",
                                                 store.config.DEFAULT_SEMANTIC_RECALL_THRESHOLD),
            "prompt_layout_enabled": cfg.get("prompt_layout_enabled",
                                             store.config.DEFAULT_PROMPT_LAYOUT_ENABLED),
            "speaker_turn_taking": cfg.get("speaker_turn_taking",
                                           store.config.DEFAULT_SPEAKER_TURN_TAKING),
            "backup_enabled": cfg.get("backup_enabled", store.config.DEFAULT_BACKUP_ENABLED),
            "backup_interval_hours": cfg.get("backup_interval_hours",
                                             store.config.DEFAULT_BACKUP_INTERVAL_HOURS),
            "backup_keep": cfg.get("backup_keep", store.config.DEFAULT_BACKUP_KEEP),
            "backup_dir": cfg.get("backup_dir", store.config.DEFAULT_BACKUP_DIR),
            "active_connection_id": active["id"] if active else "",
            # `model` rides along because the global status bar names the model
            # every scene will use, and that is only ever this connection's --
            # there is no per-campaign override. Reading it here keeps the bar
            # off /llm-connections/{id}, whose payload carries key_set and the
            # base URL it has no business fetching to print one string. It is
            # the *effective* model: a Claude connection with none configured
            # still generates, on the dispatcher's fallback, so reporting the
            # bare "" would show a dash for a connection that is about to run.
            "active_connection": ({"id": active["id"], "kind": active["kind"], "name": active["name"],
                                   "model": llm.effective_model(active)}
                                   if active else None),
            "ready": _connection_ready(active),
            "setup_done": setup_done,
            "first_run": first_run,
            # Which store this config describes. `first_run` is a statement
            # about one library, so a client caching any decision derived from
            # it needs to know when the library underneath changed (#194).
            "data_dir": str(store.home())}


def _setup_state(cfg: dict[str, str]) -> tuple[str, bool]:
    """`(setup_done, first_run)` for this store (#194).

    The frontend redirects `/` to the setup wizard on a true `first_run`, so
    the two ways to be wrong are not symmetric: showing the wizard to someone
    who already has a library hijacks their app, while missing a genuinely
    fresh install only costs them the tour. Every uncertain case therefore
    resolves to False.

    The recorded flag is authoritative once set -- finishing *or* dismissing
    the wizard sets it, so neither deleting every world later nor clearing a
    key brings the wizard back. It is only when nothing has been recorded that
    the store itself is asked, and a store that already holds worlds or
    campaigns has its answer written down: without that backfill, every
    install predating this key would re-run the scan on every config read
    forever, because "no flag" is indistinguishable from "never asked".

    The backfill is a write from a GET, which is worth the oddness: it is
    idempotent, happens at most once per store, and the alternative (a startup
    migration) cannot cover a data dir switched mid-session. Both halves of the
    answer come from here so a response can never report the flag as unset
    while this call has just written it -- the caller was handed `cfg` before
    the backfill, and reading `setup_done` back off it would contradict the
    file on exactly the request that fixed it.
    """
    recorded = cfg.get("setup_done", store.config.DEFAULT_SETUP_DONE)
    if recorded == "on":
        return "on", False
    try:
        if store.worlds.has_worlds() or store.campaigns.has_campaigns():
            store.write_config(setup_done="on")
            return "on", False
    except OSError:
        # Could not look, so cannot claim this is a fresh install -- and
        # nothing was recorded, so the flag is still whatever the file says.
        return recorded, False
    return recorded, True


def _connection_ready(conn: dict | None) -> bool:
    if conn is None:
        return False
    if conn["kind"] == "openrouter":
        return bool(conn["api_key"])
    if conn["kind"] == "openai_compatible":
        return bool(conn["base_url"])
    return True  # claude never needs a key


@router.get("/config")
def get_config():
    return _public_config(store.read_config())


@router.put("/config")
def put_config(update: ConfigUpdate):
    fields = {k: v for k, v in _dump(update).items() if v is not None}
    return _public_config(store.write_config(**fields))


# ---- prompt layout (#29) ----
def _layout_body(stored: list[dict]) -> dict:
    """The editor's view: the toggle, and every catalog section in the order it
    would render — including the switched-off ones, or there would be no way
    back on.

    `layout.describe` builds it from the same merge `_render_sections` walks,
    so the editor cannot claim an order the prompt does not use.
    """
    return {"enabled": store.context.layout.enabled(),
            "sections": store.context.layout.describe(store.context.SECTIONS, stored)}


@router.get("/prompt-layout")
def get_prompt_layout():
    return _layout_body(store.context.layout.read_layout())


@router.put("/prompt-layout")
def put_prompt_layout(update: PromptLayoutUpdate):
    # The whole list replaces the stored one; an empty list is Reset.
    return _layout_body(
        store.context.layout.write_layout([_dump(s) for s in update.sections]))


@router.get("/config/data-dir")
def get_data_dir():
    return store.data_dir_info()


@router.put("/config/data-dir")
def put_data_dir(update: DataDirUpdate, request: Request):
    # Not under a campaign lock, deliberately: there is no campaign to lock --
    # the root itself is moving, and a lock taken in the old tree would not name
    # anything in the new one. This is a narrow refusal rather than a
    # guarantee, and it is the honest one available: a run reserved in the gap
    # is a race the store's own docs already place outside what any lock here
    # can promise (`store/locks.py`), while the case this actually closes -- a
    # player wandering to Configuration mid-turn -- is now reachable for the
    # first time and was not before.
    runs.require_store_free(request.app)
    try:
        store.set_data_dir(update.data_dir)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"detail": str(exc), "kind": "data_dir"})
    return store.data_dir_info()


# ---- backups (#32) ----
def _backups_body() -> dict:
    """Where the archives live and what is in there, newest first. The
    directory rides along because it is a *setting* — the answer to "why is
    this list empty" is often "you moved it"."""
    return {"dir": str(store.backups.backup_dir()),
            "backups": store.backups.list_backups()}


@router.get("/backups")
def get_backups():
    try:
        return _backups_body()
    except OSError as exc:
        # Not an empty list: "no restore points" and "could not look" send a
        # reader in opposite directions, and this one is read right before
        # somebody decides whether they are covered.
        raise HTTPException(status_code=500, detail=f"could not list backups: {exc}")


@router.post("/backups")
def post_backup():
    """Back up now, then apply retention. Returns the refreshed listing, so the
    caller needs no second request to show what it just made.

    The two steps report separately on purpose. Under one `try` a failed sweep
    surfaced as "could not write a backup" — telling the user the opposite of
    what happened, about the half of the operation they care about, and
    throwing away the listing that would have shown them the archive sitting
    there. A backup that landed is a success with a retention problem attached.
    """
    try:
        made = store.backups.create_backup()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"could not write a backup: {exc}")
    swept: list[str] = []
    retention_error = None
    try:
        swept = store.backups.sweep()
    except OSError as exc:
        retention_error = f"backup written, but old archives could not be removed: {exc}"
    return {**_backups_body(), "created": made.name, "swept": swept,
            "retention_error": retention_error}


@router.get("/store/conflicts")
def get_store_conflicts():
    """Sync-tool conflict artifacts sitting unread in the store (#35).

    Its own route rather than a field on GET /config: this costs a directory
    walk of the whole library, and /config is read on nearly every page. The
    Storage section asks for it when it is shown, which is where the answer is
    actionable.

    A scan that could not run is a 500, deliberately -- `store.external.scan`
    already absorbs the per-directory failures a synced volume produces, so
    anything reaching here failed at the root, and reporting an empty list for
    that would tell the user their library is clean when nobody looked.
    """
    try:
        return store.external.scan()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"could not scan the store: {exc}")


# ---- llm connections ----
@router.get("/llm-connections")
def get_connections():
    return store.llm_connections.list_connections()


@router.post("/llm-connections")
def post_connection(body: ConnectionCreate):
    fields = _dump(body)
    kind = fields.pop("kind")
    name = fields.pop("name")
    return {"id": store.llm_connections.create_connection(kind, name, **fields)}


@router.get("/llm-connections/{id}")
def get_connection(id: str):
    try:
        return store.llm_connections.read_connection(id)
    except store.llm_connections.ConnectionNotFound:
        raise HTTPException(status_code=404, detail="connection not found")


@router.put("/llm-connections/{id}")
def put_connection(id: str, body: ConnectionUpdate):
    fields = {k: v for k, v in _dump(body).items() if v is not None}
    try:
        store.llm_connections.update_connection(id, **fields)
        return store.llm_connections.read_connection(id)
    except store.llm_connections.ConnectionNotFound:
        raise HTTPException(status_code=404, detail="connection not found")


@router.delete("/llm-connections/{id}")
def delete_connection_route(id: str):
    try:
        store.llm_connections.delete_connection(id)
    except store.llm_connections.ConnectionNotFound:
        raise HTTPException(status_code=404, detail="connection not found")
    return {"ok": True}


@router.post("/llm-connections/{id}/models/refresh")
async def post_connection_models_refresh(
    id: str, client: OpenAICompatibleClient = Depends(get_openai_compatible_client),
):
    try:
        conn = store.llm_connections.read_connection_raw(id)
    except store.llm_connections.ConnectionNotFound:
        raise HTTPException(status_code=404, detail="connection not found")
    if conn["kind"] != "openai_compatible":
        raise HTTPException(status_code=400, detail="model listing not supported for this connection kind")
    rev = conn["rev"]
    try:
        models = await client.list_models(conn["base_url"], conn["api_key"])
    except LLMError as exc:
        raise _llm_http_error(exc) from exc
    fetched_at = store.now_iso()
    store.llm_connections.set_cached_models(id, models, rev)
    return {"models": models, "fetched_at": fetched_at, "rev": rev}


# ---- styles ----
@router.get("/styles")
def get_styles():
    return store.styles.list_styles()


@router.post("/styles")
def post_style(body: StyleCreate):
    return {"id": store.styles.create_style(body.name, body.description, body.tags, body.body)}


@router.get("/styles/{sid}")
def get_style(sid: str):
    try:
        return store.styles.read_style(sid)
    except store.styles.StyleNotFound:
        raise HTTPException(status_code=404, detail="style not found")


@router.put("/styles/{sid}")
def put_style(sid: str, body: StyleUpdate):
    try:
        store.styles.update_style(sid, name=body.name, description=body.description,
                                  tags=body.tags, body=body.body)
    except store.styles.StyleNotFound:
        raise HTTPException(status_code=404, detail="style not found")
    except store.styles.BuiltInStyleImmutable:
        raise HTTPException(status_code=400, detail="built-in styles can't be edited — duplicate it first")
    return {"ok": True}


@router.delete("/styles/{sid}")
def delete_style(sid: str):
    try:
        store.styles.delete_style(sid)
    except store.styles.StyleNotFound:
        raise HTTPException(status_code=404, detail="style not found")
    except store.styles.BuiltInStyleImmutable:
        raise HTTPException(status_code=400, detail="built-in styles can't be deleted")
    return {"ok": True}


@router.post("/styles/{sid}/duplicate")
def post_style_duplicate(sid: str):
    try:
        return {"id": store.styles.duplicate_style(sid)}
    except store.styles.StyleNotFound:
        raise HTTPException(status_code=404, detail="style not found")


# ---- response presets ----
@router.get("/response-presets")
def get_response_presets():
    return store.response_presets.list_presets()


@router.post("/response-presets")
def post_response_preset(body: ResponsePresetCreate):
    try:
        return {"id": store.response_presets.create_preset(
            body.name, body.description, body.style_id, body.length_preset, body.knobs)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/response-presets/{pid}")
def get_response_preset(pid: str):
    # An unreadable file comes back as a damaged RECORD (validity.valid false),
    # not an error: a scope can still be configured to it, and the view has to
    # be able to show the row and say why it supplies nothing.
    try:
        return store.response_presets.read_preset(pid)
    except store.response_presets.PresetNotFound:
        raise HTTPException(status_code=404, detail="response preset not found")


@router.put("/response-presets/{pid}")
def put_response_preset(pid: str, body: ResponsePresetUpdate):
    try:
        store.response_presets.update_preset(
            pid, name=body.name, description=body.description, style_id=body.style_id,
            length_preset=body.length_preset, knobs=body.knobs)
    except store.response_presets.PresetNotFound:
        raise HTTPException(status_code=404, detail="response preset not found")
    except store.response_presets.BuiltInPresetImmutable:
        raise HTTPException(status_code=400,
                            detail="built-in presets can't be edited — duplicate it first")
    except (OSError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="response preset file could not be read")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.delete("/response-presets/{pid}")
def delete_response_preset(pid: str):
    try:
        store.response_presets.delete_preset(pid)
    except store.response_presets.PresetNotFound:
        raise HTTPException(status_code=404, detail="response preset not found")
    except store.response_presets.BuiltInPresetImmutable:
        raise HTTPException(status_code=400, detail="built-in presets can't be deleted")
    return {"ok": True}


@router.post("/response-presets/{pid}/duplicate")
def post_response_preset_duplicate(pid: str):
    try:
        return {"id": store.response_presets.duplicate_preset(pid)}
    except store.response_presets.PresetNotFound:
        raise HTTPException(status_code=404, detail="response preset not found")
    except (store.response_presets.PresetUnreadable, OSError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="response preset file could not be read")


@router.get("/response-presets/{pid}/usage")
def get_response_preset_usage(pid: str):
    # usage() reports individual unreadable campaigns/scenes in `unevaluated`
    # (the caller must not render a partial list as a complete one); this is the
    # backstop for a store-wide read failure. It must be a handled error, not a
    # 500 — the caller renders this immediately before an irreversible delete
    # and has to be able to tell "no impact" from "impact unknown".
    try:
        return store.response_presets.usage(pid)
    except (OSError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="preset usage could not be computed")


@router.get("/response")
def get_global_response():
    cfg = store.read_config()
    return _response_body({}, {}, cfg, cfg)


@router.put("/response")
def put_global_response(body: ResponseSettings):
    fields = {k: v for k, v in _dump(body).items() if v is not None}
    _write_response(lambda f: store.write_config(**f), fields,
                    style_key="default_style_id")
    return {"ok": True}


@router.get("/length-presets")
def get_length_presets():
    return store.lengths.PRESETS


@router.get("/calendars/providers")
def get_calendar_providers():
    return {"providers": store.calendars.list_providers()}


# ---- climates (#40) ----

@router.get("/climates")
def get_climates():
    """The merged list, each entry carrying *both* tier flags.

    `builtin` and `custom` rather than one label: a single `custom` tag cannot
    distinguish a custom climate that shadows a preset from one that stands
    alone, and the editor needs that to choose between *Revert to preset* and
    *Delete*, and to know whether deleting frees the id.
    """
    return {"climates": store.climates.list_climates()}


@router.get("/climates/{climate_id}")
def get_climate(climate_id: str):
    doc = store.climates.get(climate_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="climate not found")
    return {"climate": doc, "builtin": store.climates.is_builtin(climate_id),
            "custom": store.climates.custom_path(climate_id).exists()}


@router.put("/climates/{climate_id}")
def put_climate(climate_id: str, body: dict):
    """Write a climate to the private tier, copying a preset on first edit.

    Validation is strict here on purpose. The resolver is lenient so a bad
    document can never take a turn down, which makes this the only place a
    mistake can be reported at all.
    """
    doc = dict(body or {})
    doc["id"] = climate_id  # the route is authoritative; a mismatched body id
                            # would write to a file the editor cannot reopen
    try:
        return {"climate": store.climates.save(doc)}
    except store.climates.ClimateError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"could not write climate: {e}")


def _climate_referrers(climate_id: str) -> dict:
    """Which campaigns default to this climate, and which locations name it.

    Disclosed rather than blocking. Deleting a custom-only climate silently
    moves every *untagged* location in a campaign that defaults to it, and the
    editor can only warn about that if it is told.
    """
    campaigns_using, locations_using = [], []
    # Not caught: an unreadable campaign list is an *unknown* result, and
    # returning an empty one would tell the editor nothing uses the climate —
    # defeating the fail-closed guard on the other side of the call.
    rows = store.campaigns.list_campaigns()
    for row in rows:
        cid = row["id"]
        # The *effective* default, not whatever the file literally says: a
        # campaign with no stored default (every one predating the weather work)
        # or an unreadable one still falls back to the shipped preset, and
        # reading the file here reported such a campaign as using nothing at
        # all — precisely the case the editor needs warning about.
        if store.campaign_climate.resolve_default(cid)["id"] == climate_id:
            campaigns_using.append({"id": cid, "name": row.get("name", cid)})
        # Not caught: one unreadable location file aborting the scan
        # would drop every valid reference in that campaign, and the editor
        # would report no impact for a climate those locations use.
        for loc in store.overlay.list_entities(cid, "locations"):
            if loc.get("climate") == climate_id:
                locations_using.append({"campaign": cid, "id": loc["id"],
                                        "name": loc.get("name", loc["id"])})
    return {"campaigns": campaigns_using, "locations": locations_using}


@router.get("/climates/{climate_id}/referrers")
def get_climate_referrers(climate_id: str):
    try:
        return _climate_referrers(climate_id)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"could not scan campaigns: {e}")


@router.delete("/climates/{climate_id}")
def delete_climate(climate_id: str):
    """Drop the private copy, reverting to the preset if there is one."""
    try:
        referrers = _climate_referrers(climate_id)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"could not scan campaigns: {e}")
    try:
        removed = store.climates.remove(climate_id)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"could not delete climate: {e}")
    if not removed:
        raise HTTPException(status_code=404, detail="no custom climate to delete")
    return {"ok": True, "reverted_to_preset": store.climates.is_builtin(climate_id),
            "referrers": referrers}
