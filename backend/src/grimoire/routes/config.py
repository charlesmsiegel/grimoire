"""Application-wide settings: config, LLM connections, styles, response
presets and the global response scope, plus the entity-kind, calendar-provider
and climate catalogues that worlds, campaigns and the import dialogs select
from."""

from __future__ import annotations

import asyncio
import logging
import unicodedata
import uuid
from datetime import datetime
from typing import Literal, NamedTuple

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from .. import catalog, embeddings, health, llm, llm_errors, llm_sampling, llm_usage, store
from ..llm import LLMClient
from ..llm_errors import LLMError
from ..store.inference import capabilities, controls, facts, providers
from ..store.inference import migrate as inference_migrate
from ..store.inference import resolve as inference
from ..store.inference import settings as inference_settings
from . import runs
from .common import (
    NEWER_FORMAT,
    _bounded_call,
    _dump,
    _llm_http_error,
    _response_body,
    _write_response,
    get_health,
    get_llm,
    legacy_refused,
    refuse_newer,
    refuse_unmigrated,
    run_error,
)
from .models import (
    CatalogProbe,
    ConfigUpdate,
    ConnectionCreate,
    ConnectionUpdate,
    DataDirUpdate,
    FactsUpdate,
    HealthCheck,
    ModelTestPreview,
    ModelTestRun,
    PromptLayoutUpdate,
    ResponseSettings,
    SamplerImportBody,
    SamplerPresetBody,
    StyleCreate,
    StyleUpdate,
)

router = APIRouter()
log = logging.getLogger(__name__)

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


# ---- config ----
class _Chat(NamedTuple):
    """What chat would run on with no campaign in front of it, for the header:
    the primary attempt's connection (None when nothing resolves), whether
    the resolution could be made at all, and whether the seam would serve it
    (`inference.refusal`, the one decision -- the header's `ready`)."""

    conn: dict | None
    resolved: bool
    ready: bool = False


def _chat() -> _Chat:
    """The GLOBAL chat route's resolution -- what a turn in a campaign with no
    choice of its own runs on, and so what the header names (its connection,
    readiness and health) and what `send_images_reach` asks about.

    One resolve per config read, and a display: it must never refuse, and a
    failure is not worth a 500 on a read every navigation makes -- it reports
    no connection, and `send_images_reach` says "unknown"."""
    try:
        # routing-ok: a display of where chat would run; it must never refuse
        resolved = inference.resolve("chat")
        return _Chat(resolved.conn, True, inference.refusal(resolved) is None)
    except Exception:    # a display read; see the docstring
        # Logged at error: the resolver refuses nothing by design, so reaching
        # here is a bug or a broken store, and the header alone would only
        # show "not ready".
        log.exception("could not resolve where chat runs for the header")
        return _Chat(None, False)


def _send_images_reach(chat: _Chat) -> str:
    """Whether post images would reach the model (#377) chat runs on (`_chat`).
    Answers "unknown" rather than failing the config read: this is a hint
    beside a checkbox, not something worth a 500."""
    if not chat.resolved:
        return "unknown"
    try:
        return store.post_images.reach(chat.conn)
    except Exception:  # noqa: BLE001 - a display hint; see the docstring
        return "unknown"


def _public_config(cfg: dict[str, str], registry: health.ProviderHealth) -> dict:
    """Everything the Configuration page reads, enumerated by hand.

    By hand because it is not a passthrough: some keys are derived rather than
    stored (the active connection and its health, `first_run`, the data dir),
    one is a constant the client must not duplicate, and the rest name the
    default this response falls back to. What that costs is a key that is
    writable and stored while never being reported -- a setting that saves,
    takes effect, and redisplays its default forever (#410). Two tests in
    `test_routes.py` hold this dict to `ConfigUpdate`: one that every writable
    key is named here, one that each is answered from what was stored.

    The header's connection (`active_connection`, `ready`, `health`) is what
    chat would run on (`_chat`): the resolver's answer, not the legacy
    `active_connection_id`. `inference` summarises the roles without resolving
    anything (`settings.summary`).
    """
    chat = _chat()
    active = chat.conn
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
            # A CONSTANT, not a setting: the character editor warns above it,
            # and a duplicated TypeScript literal would drift from the
            # backend's truncation silently. Counted in CODE POINTS, which the
            # editor must match with [...text].length rather than .length --
            # JavaScript counts UTF-16 units, so an anchor of astral characters
            # reads as double and would warn at half the real cap.
            "voice_anchor_cap": store.voice_anchors.VOICE_ANCHOR_CAP,
            "context_scan_depth": cfg.get("context_scan_depth", store.config.DEFAULT_SCAN_DEPTH),
            # The EFFECTIVE depth, not the stored string: a hand-edited value
            # past the cap is read as the cap, and the page should show the
            # number a turn actually runs with.
            "lore_recursion_depth": str(store.config.lore_recursion_depth(cfg)),
            "archive_depth": cfg.get("archive_depth", store.config.DEFAULT_ARCHIVE_DEPTH),
            "prompt_log_depth": cfg.get("prompt_log_depth",
                                        store.config.DEFAULT_PROMPT_LOG_DEPTH),
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
            "tracker": cfg.get("tracker", store.config.DEFAULT_TRACKER),
            "perception_rider": cfg.get("perception_rider",
                                        store.config.DEFAULT_PERCEPTION_RIDER),
            "send_images": cfg.get("send_images", store.config.DEFAULT_SEND_IMAGES),
            "send_images_limit": cfg.get("send_images_limit",
                                         store.config.DEFAULT_SEND_IMAGES_LIMIT),
            "send_images_reach": _send_images_reach(chat),
            "backup_enabled": cfg.get("backup_enabled", store.config.DEFAULT_BACKUP_ENABLED),
            "backup_interval_hours": cfg.get("backup_interval_hours",
                                             store.config.DEFAULT_BACKUP_INTERVAL_HOURS),
            "backup_keep": cfg.get("backup_keep", store.config.DEFAULT_BACKUP_KEEP),
            "backup_dir": cfg.get("backup_dir", store.config.DEFAULT_BACKUP_DIR),
            # Both fork nudges. `replay_fork_threshold` has been in
            # `_CONFIG_KEYS` since #80 and reachable through `ConfigUpdate`, so
            # a PUT stored it -- but it was never reported here, which is the
            # half of the round trip nothing was checking: the Configuration
            # page fell back to the default on every load and showed an empty
            # box to whoever had set it. Added with `advance_fork_threshold`
            # (#107) rather than after it, so the pair cannot disagree about
            # whether a threshold is a thing the client can read back.
            "replay_fork_threshold": cfg.get("replay_fork_threshold",
                                             store.config.DEFAULT_REPLAY_FORK_THRESHOLD),
            "advance_fork_threshold": cfg.get("advance_fork_threshold",
                                              store.config.DEFAULT_ADVANCE_FORK_THRESHOLD),
            # The STORED setting, which is not necessarily the level in force:
            # a value the vocabulary does not recognize is narrowed to the
            # default by `logs.level_name`, and `GET /logs/level` is what
            # reports what is actually being recorded.
            "log_level": cfg.get("log_level", store.config.DEFAULT_LOG_LEVEL),
            # The connection chat runs on (`_chat`), not the stored legacy key:
            # at format 2 that key is frozen for older builds and names
            # nothing that plays. Before the migration the two agree unless a
            # legacy `route_scene` moves chat -- and then this says where it
            # went (the legacy picker that writes the key goes with Task 13).
            "active_connection_id": active["id"] if active else "",
            # `model` rides along because the global status bar names the model
            # a scene with no campaign choice of its own will use (a campaign's
            # own is the scene header's business). Reading it here keeps the
            # bar off /llm-connections/{id}, whose payload carries key_set and
            # the base URL it has no business fetching to print one string. It
            # is the *effective* model: a Claude connection with none configured
            # still generates, on the dispatcher's fallback, so reporting the
            # bare "" would show a dash for a connection that is about to run.
            "active_connection": ({"id": active["id"], "kind": active["kind"], "name": active["name"],
                                   "model": llm.effective_model(active)}
                                   if active else None),
            # The seam's own decision (`inference.refusal`): a key or an
            # address missing, or a model known unable to generate -- never a
            # third copy of the credential rule that misses the second half.
            "ready": chat.ready,
            # What the active provider last actually did (#146), so the status
            # dot can stop meaning "a key string is present" and start meaning
            # "this worked, or here is how it failed". Read from the registry
            # rather than checked here: a config read happens on every
            # navigation, and a network call per navigation is a poller nobody
            # asked for. `unknown` until something -- a real turn, or the
            # reader pressing Test connection -- has an answer.
            "health": registry.status(active["id"], active["rev"]) if active else None,
            # The roles, named, for the header and the Models link -- the
            # cascade alone, so it adds no resolve to this read.
            "inference": inference_settings.summary(),
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


@router.get("/config")
def get_config(registry: health.ProviderHealth = Depends(get_health)):
    return _public_config(store.read_config(), registry)


def _recursion_depth_ok(value: str | None) -> bool:
    """Absent, blank, or a whole number from 0 to the cap."""
    if value is None or not value.strip():
        return True
    try:
        depth = int(value.strip())
    except ValueError:
        return False
    return 0 <= depth <= store.config.LORE_RECURSION_MAX


#: Config keys whose only meaningful values are "on" and "off".
_ON_OFF_KEYS = ("tracker", "perception_rider", "send_images")


#: The legacy keys that pick the Embedding role on a store not yet at format 2.
_EMBEDDING_LEGACY_KEYS = ("embeddings_connection_id", "embeddings_model")


def _refuse_unconfirmed_config_reembed(fields: dict[str, str]):
    """`put_config`'s guard: 400 `confirm_embedding` for a change of the legacy
    embedding keys that moves the Embedding role's vector space (format 1;
    at format 2 the keys are refused as legacy before this runs). Called in
    the `config_lock` hold that writes, with the config the write replaces,
    so the comparison is against what is actually there (CLAUDE.md, "a
    settings surface never spends unasked" -- the server's rule, whatever
    the format)."""
    moves = {k: fields[k] for k in _EMBEDDING_LEGACY_KEYS if k in fields}

    def guard(cfg: dict[str, str]) -> None:
        if not moves or not store.embed_space.config_moved(cfg, {**cfg, **moves}):
            return
        conn_id = str(moves.get("embeddings_connection_id",
                                cfg.get("embeddings_connection_id", "")) or "").strip()
        try:
            name = store.llm_connections.read_connection_raw(conn_id).get("name") or conn_id
        except (store.llm_connections.ConnectionNotFound, OSError, UnicodeDecodeError):
            name = conn_id
        raise HTTPException(status_code=400, detail={
            "kind": "confirm_embedding",
            "detail": inference_settings.EMBEDDING_CONFIRM.format(provider=name)})
    return guard


@router.put("/config")
def put_config(update: ConfigUpdate, registry: health.ProviderHealth = Depends(get_health)):
    fields = {k: v for k, v in _dump(update).items() if v is not None}
    confirmed = fields.pop("confirm_embedding", None) is True
    # The legacy inference keys are model settings: a newer store refuses
    # them, and one at format 2 refuses them in the hold that writes (below).
    if any(k in store.inference_keys.LEGACY_GLOBAL_KEYS for k in fields):
        refuse_newer()
    # The two tracker switches are a choice, not a string: the Settings
    # checkbox and `store.config` both read anything but "off" as on, so a
    # stored "maybe" would be a setting nobody chose. Refused as the campaign's
    # own tracker setting is (`PUT /campaigns/{cid}/tracker`), before anything
    # is written.
    for key in _ON_OFF_KEYS:
        if key in fields and fields[key] not in ("on", "off"):
            raise HTTPException(status_code=400, detail=f"{key} must be 'on' or 'off'")
    # A bound, refused rather than clamped: the cap in `config.lore_recursion_depth`
    # is for a hand edit, and a value typed into the page that silently became
    # another one would be a setting nobody chose. Blank clears to the default.
    if not _recursion_depth_ok(fields.get("lore_recursion_depth")):
        raise HTTPException(status_code=400,
                            detail=f"lore_recursion_depth must be 0-{store.config.LORE_RECURSION_MAX}")
    try:
        saved = store.config.write_config_refusing_legacy(
            guard=None if confirmed else _refuse_unconfirmed_config_reembed(fields), **fields)
    except store.config.LegacyKeysRefusedError as exc:
        raise legacy_refused(exc) from exc
    # `store.logs` holds the threshold in module state rather than reading the
    # config per row -- `record` is on the path of everything the app does --
    # so the write is what has to push it. Unconditional rather than guarded on
    # `"log_level" in fields`: re-applying an unchanged level costs one config
    # read on a route that has already done several, and a guard is one more
    # place for the two to drift apart.
    store.logs.apply_level()
    return _public_config(saved, registry)


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
    # anything in the new one. The exclusion that IS available is the run
    # registry's own, and it is held across the move rather than consulted
    # before it: checking and then moving leaves a window for a send to reserve,
    # and that run's setup writes into the old tree while its terminal write
    # resolves against the new one. What remains outside any lock here is
    # another *process* sharing the store, which `store/locks.py` already places
    # outside what this can promise.
    #
    # The inference-settings migration is not a run, so the registry cannot
    # see it; its own lock is held across the move instead, TRIED rather than
    # waited on -- a migration begins with a backup of the whole library.
    with runs.store_held_still(request.app), inference_migrate.held_still() as free:
        if not free:
            raise HTTPException(status_code=409, detail={
                "kind": "busy",
                "detail": "the model settings are being upgraded; try again when "
                          "that has finished"})
        try:
            store.set_data_dir(update.data_dir)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400,
                                detail={"detail": str(exc), "kind": "data_dir"})
    # The log's size cache is keyed by absolute path, so a root that moved
    # leaves byte counts charged against files the new tree does not have --
    # which would cap a fresh log at the old one's size (`logs.forget_file_sizes`).
    store.logs.forget_file_sizes()
    # Refs a continuity trigger left for a sweep name records in the OLD tree;
    # a move is refused only while a run is live, and these can wait with none.
    runs.drop_pending_touched(request.app)
    # And the threshold belongs to the store, not to the process: `log_level`
    # lives in the config of whichever library is open, so a root that moved
    # without this kept writing at the OLD tree's floor while `GET /config`
    # (reading the new tree) reported the new one -- two endpoints disagreeing
    # about one setting, with rows going to disk under the wrong one.
    store.logs.apply_level()
    # The new root may be a legacy store: migrate it, in the background, as
    # startup would have (spec 11.2). After the move, outside every hold above.
    request.app.state.start_inference_migration()
    return store.data_dir_info()


# ---- backups (#32) ----
def _backups_body() -> dict:
    """Where the archives live and what is in there, newest first. The
    directory rides along because it is a *setting* — the answer to "why is
    this list empty" is often "you moved it"."""
    return {"dir": str(store.backups.backup_dir()),
            "backups": store.backups.list_backups(),
            "image_backups": store.backups.list_image_backups()}


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
def post_backup(request: Request):
    """Back up now, then apply retention. Returns the refreshed listing, so the
    caller needs no second request to show what it just made.

    The two steps report separately on purpose. Under one `try` a failed sweep
    surfaced as "could not write a backup" — telling the user the opposite of
    what happened, about the half of the operation they care about, and
    throwing away the listing that would have shown them the archive sitting
    there. A backup that landed is a success with a retention problem attached.

    The archive is built holding the store against image-store maintenance
    (`maintenance_running` while a migration or collection is live), so it never
    zips a tree a pass is moving files out of.
    """
    try:
        with runs.maintenance_excluded(request.app):
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


@router.post("/backups/images")
def post_image_backup(request: Request):
    """Create a separate image archive from the currently resolved store.

    Held against image-store maintenance while it is built, as `post_backup`
    is: a collection deleting objects under it would archive placements whose
    images are already gone."""
    try:
        with runs.maintenance_excluded(request.app):
            made = store.backups.create_image_backup()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"could not back up images: {exc}") from exc
    try:
        return {**_backups_body(), "created": made.name, "listing_error": None}
    except OSError as exc:
        # The archive has already landed. Tell the caller where it is without
        # claiming that the write failed or inventing an empty listing.
        return {"dir": str(made.parent), "created": made.name,
                "listing_error": f"image archive written, but backups could not be listed: {exc}"}


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
    carried a copy of `llm.effective_model`'s rule AND of
    `CLAUDE_DEFAULT_MODEL`, pinned by a test that scraped a `.tsx` file with a
    regex. Reporting the answer here deletes the rule, the constant and the
    scrape together, and a fourth kind that substitutes a model is then one
    change in `llm.effective_model` rather than two in two languages.

    Added beside `model` rather than replacing it: the connection editor edits
    the stored value, and a form that round-tripped the effective one would
    write the substitute into the file the substitution exists to avoid needing.
    """
    return {**conn, "effective_model": llm.effective_model(conn)}


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
    what that route sends. None for an unreadable connection."""
    try:
        conn = store.llm_connections.read_connection_raw(conn_id)
    except store.llm_connections.ConnectionNotFound:
        return None
    return llm_sampling.report(inference.own_sampling(conn))


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
    return {"id": conn_id}


@router.get("/llm-connections/{id}")
def get_connection(id: str, registry: health.ProviderHealth = Depends(get_health)):
    try:
        conn = _with_effective(_picker_models(store.llm_connections.read_connection(id)))
    except store.llm_connections.ConnectionNotFound:
        raise HTTPException(status_code=404, detail="connection not found")
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
    write that turns the Embedding role on. Runs in the hold that writes,
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
            _check_preset_field(fields, stored.get("sampler_preset", ""))
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
    except store.llm_connections.ConnectionNotFound:
        raise HTTPException(status_code=404, detail="connection not found")


@router.delete("/llm-connections/{id}")
def delete_connection_route(id: str, registry: health.ProviderHealth = Depends(get_health)):
    refuse_newer()
    try:
        store.llm_connections.delete_connection(id)
    except store.llm_connections.ConnectionNotFound:
        raise HTTPException(status_code=404, detail="connection not found")
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
    except store.llm_connections.ConnectionNotFound:
        raise HTTPException(status_code=404, detail="connection not found")
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
            models = await client.list_models(conn)
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
        return {"models": catalog.listable(await client.list_models(conn))}
    except LLMError as exc:
        raise _llm_http_error(exc) from exc


#: The adapters whose health check GENERATES (one short message, which a
#: subscription counts): sent only on `confirm: true` (spec 6.5, rule 1).
GENERATING_CHECK_KINDS: frozenset[str] = frozenset({"claude"})

#: The 400 an unconfirmed generating check gets, before anything is sent.
HEALTH_UNCONFIRMED = ("This check sends one short message and may use your "
                      "subscription — confirm to run it.")


@router.get("/providers/presets")
def get_provider_presets():
    """The provider presets a new provider is made from (spec 6.1), in table
    order, each with whether its health check generates (and so needs
    `confirm`)."""
    return [{**capabilities.preset_body(p), "generating_check": p.kind in GENERATING_CHECK_KINDS}
            for p in providers.PRESETS.values()]


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
    problem = inference.problem(conn)
    if problem is not None:
        return _health_body(registry.record(conn, LLMError("missing_key", problem)))
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
        await _bounded_call(client.check(conn), ceiling=HEALTH_CHECK_CEILING)
    except LLMError as exc:
        return _health_body(registry.record(conn, exc))
    return _health_body(registry.record(conn))


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
    model = body.model.strip() or llm.effective_model(raw)
    if not model:
        raise HTTPException(status_code=400, detail="name a model to test")
    if len(model) > store.alternates.MAX_MODEL_CHARS:
        raise HTTPException(status_code=400, detail="model id is too long")
    problem = inference.problem(raw)
    if problem is not None:
        raise HTTPException(status_code=409, detail={"detail": problem, "kind": "missing_key"})
    return raw, model, caps


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


async def _embed_probe(raw: dict, conn: dict, model: str) -> dict:
    """The `embed` probe: one fixed string, once, metered under `model-test`
    with `operation: "embed"`, on the provider under test (ruling 8: it is not
    an embed task and resolves no role).

    The holder is stamped as `llm._stamp` stamps a chat attempt, which is what
    makes the meter file a row at all (an empty holder means "never sent"),
    and the client folds into it whatever counts the endpoint reports (a
    prompt count it did not report is estimated, `embed.estimate_prompt`).
    What served it -- the provider id and the `embed` operation -- is filed
    from the probe's lowered `conn` through `llm_usage.account` (slice E, M9). A
    failure is finished as the embed operation finishes one
    (`inference.embed.record_failure`): its recorded detail is the kind and HTTP status
    only, because a redirect's `Location` can carry a key. The verdict still
    reads the exception's own detail (`_probe`, through `probes.scrub`)."""
    probes = store.inference.probes
    # `model=` because the client owns the holder's `model` key: it clears it
    # at entry and refills it only with what the endpoint named.
    with store.usage.meter("model-test", model=model) as m:
        m.usage.update({"model": model, "connection": raw.get("name") or raw["id"],
                        "provider": raw.get("kind", "openrouter"), "attempts": 1,
                        "requested_model": model})
        llm_usage.account(m.usage, llm_usage.with_account(conn, operation="embed"))
        try:
            # Off the loop: the embeddings client is synchronous by design.
            vectors = await asyncio.to_thread(lambda: _EMBEDDINGS.embed(
                [probes.EMBED_TEXT], model, raw.get("api_key", ""), _embed_endpoint(raw),
                usage=m.usage))
        except Exception as exc:
            store.inference.embed.record_failure(m, exc)
            raise
        # A prompt count the endpoint did not report is estimated locally, as
        # the embed operation estimates one (`embed.estimate_prompt`).
        store.inference.embed.estimate_prompt(m.usage, [probes.EMBED_TEXT])
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


def _records(exc: LLMError) -> bool:
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
    about whether the model can do what was asked."""
    return (exc.status in llm.REJECTED_STATUSES and not isinstance(exc, llm.PresetRefusalError)
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


async def _probe(client: LLMClient, cap: str, raw: dict, conn: dict, model: str) -> _Outcome:
    """One probe's outcome. Its result is `{"ok": True}` (plus `dims` for
    embed), or `{"ok": False, "kind", "error"}` with the error scrubbed of any
    picture and of the connection's key. One attempt, never retried, never
    fallen back. A success is always a verdict; a failure only when `_records`
    says so."""
    probes = store.inference.probes
    try:
        probe = probes.PROBES[cap]
        if probe.operation == "embed":
            return _Outcome(await _embed_probe(raw, conn, model), True, False)
        with store.usage.meter("model-test") as m:
            # Completed is accepted; the text is not read. The row names the
            # probe's operation (M9) on a copy: `conn` serves every probe.
            await _bounded_call(client.single(
                probes.messages(cap), llm_usage.with_account(conn, operation=probe.operation),
                m.usage), ceiling=MODEL_TEST_CEILING)
    except LLMError as exc:
        return _Outcome({"ok": False, "kind": exc.kind,
                         "error": probes.scrub(exc.detail, [str(raw.get("api_key") or "")])},
                        _records(exc), _halts(exc))
    return _Outcome({"ok": True}, True, False)


async def _probe_all(client: LLMClient, caps: tuple[str, ...], raw: dict, conn: dict,
                     model: str) -> tuple[dict[str, dict], dict[str, dict]]:
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
        outcome = await _probe(client, cap, raw, conn, model)
        results[cap] = outcome.result
        if outcome.records:
            verdicts[cap] = {k: outcome.result[k] for k in ("ok", "error", "dims")
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
    capped = "max_tokens" in llm_sampling.sent_names(inference.lower(raw, probes.sampling(), model))
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
            "sends": [{"capability": c, "description": probes.describe(c, capped)}
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
    conn = inference.lower(raw, store.inference.probes.sampling(), model)

    async def work():
        results, verdicts = await _probe_all(client, caps, raw, conn, model)
        # Off the loop, as `_embed_probe` sends: filing reads the connection
        # and writes the facts file under its lock, and the lifespan loop is
        # the one every other run streams through.
        recorded = await asyncio.to_thread(_record_verdicts, conn_id, model, rev, verdicts)
        return {"state": "landed",
                "result": {"provider": conn_id, "model": model, "rev": rev,
                           "results": results, "recorded": recorded}}

    return runs.run_draft(request.app, runs.GLOBAL_SUBJECT, "model-test",
                          x_grimoire_attempt, work)


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


# ---- phase-specific response controls ----
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


# ---- sampler presets ----
def _preset_or_404(pid: str) -> dict:
    got = store.sampler_presets.read_preset(pid)
    if got is None:
        raise HTTPException(status_code=404, detail="sampler preset not found")
    return got


@router.get("/sampler-presets")
def get_sampler_presets():
    """Every preset, plus the parameter table a form is rendered from -- so the
    labels and bounds have one source, `llm_sampling.PARAMS`."""
    return {"presets": store.sampler_presets.list_presets(), "params": llm_sampling.table()}


@router.post("/sampler-presets")
def post_sampler_preset(body: SamplerPresetBody):
    refuse_newer()
    fields = _dump(body)
    try:
        pid = store.sampler_presets.create_preset(fields["name"], fields.get("params"),
                                                  fields.get("notes") or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return store.sampler_presets.read_preset(pid)


@router.post("/sampler-presets/import")
def post_sampler_preset_import(body: SamplerImportBody):
    """A SillyTavern preset file, mapped where it maps, saved, and reported.

    Saved even when nothing mapped: an empty preset is a valid one, and the
    report says that is what happened rather than refusing a file that is
    exactly what SillyTavern wrote."""
    refuse_newer()
    fields = _dump(body)
    try:
        params, report = store.sampler_presets.from_sillytavern(
            fields.get("data"), include_max_tokens=bool(fields.get("include_max_tokens")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    name = (fields.get("name") or "").strip() or "Imported preset"
    try:
        pid = store.sampler_presets.create_preset(name[:store.sampler_presets.NAME_MAX],
                                                  params, source="sillytavern")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"preset": store.sampler_presets.read_preset(pid), "report": report}


@router.get("/sampler-presets/{pid}")
def get_sampler_preset(pid: str):
    return _preset_or_404(pid)


@router.put("/sampler-presets/{pid}")
def put_sampler_preset(pid: str, body: SamplerPresetBody):
    refuse_newer()
    fields = _dump(body)
    try:
        store.sampler_presets.update_preset(pid, fields["name"], fields.get("params"),
                                            fields.get("notes") or "")
    except store.sampler_presets.PresetNotFoundError:
        raise HTTPException(status_code=404, detail="sampler preset not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _preset_or_404(pid)


@router.delete("/sampler-presets/{pid}")
def delete_sampler_preset(pid: str):
    refuse_newer()
    try:
        store.sampler_presets.delete_preset(pid)
    except store.sampler_presets.PresetNotFoundError:
        raise HTTPException(status_code=404, detail="sampler preset not found") from None
    return {"ok": True}


# ---- inference read APIs: what a model can do, and what a preset sends ----
class InferenceControlsBody(BaseModel):
    """A sampler preset previewed on a provider's model. Defined beside its one
    route, not in `models.py`, so this block stays in one place. `preset_id` ""
    is no preset (provider defaults); `model` "" is the provider's own."""

    preset_id: str = ""
    provider: str
    model: str = ""


@router.get("/llm-connections/{conn_id}/capabilities")
def get_connection_capabilities(
        conn_id: str, need: Literal["generate", "vision", "embed", "decide"] = "generate",
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
    and cannot be read answers `unreadable: true` with nothing stated, rather
    than reading as a model nothing was said of -- the panel must not offer a
    save over the user's word it could not read (the write would be refused
    anyway, `put_connection_facts`)."""
    caps = capabilities.caps_for(conn, model)
    try:
        known = facts.of(conn["id"], model, conn["rev"], strict=True)
        unreadable = False
    except facts.FactsUnreadableError:
        known, unreadable = facts.of(conn["id"], model, conn["rev"]), True
    return {"provider": conn["id"], "model": model, **known, "unreadable": unreadable,
            "capabilities": {n: capabilities.cap_body(c) for n, c in caps.items()}}


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


@router.put("/llm-connections/{conn_id}/facts")
def put_connection_facts(conn_id: str, body: FactsUpdate):
    """State `vision`, `prefill`, `post_process`, capability `overrides`
    (`{cap: "" | "yes" | "no"}`, "" removing one) and `rates` (the model's own
    per-token price; `{}` removing it) for one model; a field left out (or
    null) is left as it is. 400 for a value the store refuses -- a partial or
    unknown-field rate included -- before anything is written; 404 for a
    provider that does not exist -- or stopped existing before the write, which
    `facts.state` checks under the connection lock.

    A model-settings write like any other (spec 11.2, 12): 409 `not_migrated`
    until the store is at format 2, where the lowering reads facts rather than
    the connection's legacy fields -- a write before then would answer 200 and
    change nothing, and the migration would merge the legacy values over it --
    and 409 `newer_format` on a store a newer build wrote.

    A write that turns the Embedding role on -- the user's `embed: yes` over a
    known `no` for the model that role embeds with -- embeds the library from
    scratch, so it is refused with 400 `confirm_embedding` unless the body says
    `confirm_embedding: true` (CLAUDE.md, "a settings surface never spends
    unasked"), compared in the hold that writes (`facts.state`'s `guard`)."""
    refuse_unmigrated()
    conn = _facts_conn(conn_id)
    fields = _dump(body)
    confirmed = fields.pop("confirm_embedding", None) is True
    model = (fields.get("model") or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="name a model")
    if len(model) > store.alternates.MAX_MODEL_CHARS:
        raise HTTPException(status_code=400, detail="model id is too long")
    try:
        facts.state(conn_id, model, vision=fields.get("vision"),
                    prefill=fields.get("prefill"), post_process=fields.get("post_process"),
                    overrides=fields.get("overrides"), rates=fields.get("rates"),
                    guard=None if confirmed else _refuse_unconfirmed_facts(conn, model))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except store.llm_connections.ConnectionNotFound:
        raise HTTPException(status_code=404, detail="connection not found") from None
    except facts.FactsUnreadableError:
        # Held by another program: nothing was written, and a retry may land.
        raise HTTPException(status_code=503, detail=FACTS_UNREADABLE) from None
    return _facts_body(_facts_conn(conn_id), model)


@router.post("/inference/controls")
def post_inference_controls(body: InferenceControlsBody):
    """What sampler preset `preset_id` sends on `provider` serving `model`:
    `{requested, effective, controls}` (`controls.preview`), the same answer an
    attempt resolved for that provider, model and preset carries."""
    try:
        conn = store.llm_connections.read_connection_raw(body.provider)
    except store.llm_connections.ConnectionNotFound:
        raise HTTPException(status_code=404, detail="connection not found") from None
    if body.preset_id and store.sampler_presets.read_preset(body.preset_id) is None:
        raise HTTPException(status_code=404, detail="sampler preset not found")
    return controls.preview(body.preset_id, conn, body.model)


# ---- the entity kinds an import may route a row to (#138) ----
@router.get("/entity-kinds")
def get_entity_kinds():
    """The categories a review-table row may be reclassified to.

    `store.entities.ENTITY_KINDS` itself, in its own order, so the import
    dialogs stop keeping a second copy of the list: the per-row Category
    dropdown is built from this, and a kind added to the tuple reaches it
    without either dialog being edited.

    What the dropdown shows is this list INTERSECTED with the bundle's own
    (`useEntityKinds`), not this list outright. A kind the bundle has never
    heard of has no tab, label or editor there, so offering it would let a user
    file a row somewhere they could not then look at it -- correctly written
    and effectively lost. So the endpoint is not a way to introduce a kind
    ahead of the frontend; it is the half of the answer that keeps the dialog
    from offering a category this server would refuse.

    Not "with no frontend edit at all" -- adding a kind still means adding it
    to the frontend's own `ENTITY_KINDS`, which the tabs, labels and per-kind
    field table are keyed by and which
    `test_entities_store.py::test_the_frontend_ships_the_same_kind_list`
    requires. What this removes is a *hand-kept list of options*, in two
    components, that had to be found and edited each time; and what it adds is
    the one case that edit cannot cover -- a bundle older than the backend
    serving it, which still offers exactly the categories that backend accepts
    instead of the ones it shipped believing in.

    Deliberately NOT world-scoped (the issue floated
    `/worlds/{wid}/entity-kinds`). The kinds are a property of the code, not of
    a world, and a world-scoped path would promise a per-world answer that does
    not exist. That is the whole argument -- route order is NOT part of it:
    `routes.__init__` includes this module well before `entities`, so a
    world-scoped handler declared here would sit ahead of the generic
    `/worlds/{wid}/{kind}` catch-all for free, and `test_route_order.py` would
    say so if it did not.

    The issue offered two shapes for this and named the other one first:
    put the kinds *on the parse response* instead. That variant is genuinely
    cheaper here -- the list would arrive with the rows it applies to, from the
    process that will validate the commit, so it could not disagree with them
    in either direction, and the fallback, `kindOptions` and the whole
    bundle-skew case downstream would all be unnecessary. It was not taken for
    two reasons. `ScenarioProposal` is both the parse response AND the body the
    reviewer edits and posts back to `/scenario/import`, so a `kinds` field on
    it would be a key that is not part of the proposal, hung on the model
    anyway and lifted back out before use -- the same wart `art` already is
    (mirrored: `art` rides inbound and `post_scenario_import` pops it, `kinds`
    would ride outbound), and that one carries a comment apologising for
    itself. And the list has readers that never go
    through a parse at all: #27 wants this review table driven from a stored
    card, #119 wants it after the fact on committed records. A standalone GET
    serves those without either of them growing a parse step. The cost is real
    and is paid in the frontend, where `useEntityKinds` has a fallback and
    `kindOptions` has a seam that the parse-response shape would not need.

    `lorebook.commit` and `scenario.apply` validate an incoming category
    against the same tuple, so what this offers is exactly what they accept;
    `test_every_offered_kind_is_a_category_both_imports_accept` is that
    guarantee, taken against both commit paths rather than argued from the
    shared constant.
    """
    return {"kinds": list(store.entities.ENTITY_KINDS)}


@router.get("/calendars/providers")
def get_calendar_providers():
    return {"providers": store.calendars.list_providers()}


@router.get("/calendars/{provider}/year")
def get_calendar_year(provider: str, year: int = 0, region: str = ""):
    """One calendar's months and observances for a year, as a reference view.

    A calendar on its own has no holidays. They fall out of a *configured*
    calendar: `gregorian` produces none until it is given a region, `hebrew`
    switches its set on Israel-vs-diaspora, and every provider adds whatever
    `custom_holidays` its config carries. The Library has neither a world nor a
    campaign to take that config from, so it supplies one here -- which is why
    this route takes `region` and why the page has a picker for it.

    Read-only, and deliberately builds a throwaway config rather than reading
    any world's: this answers "what does this calendar do", not "what is that
    world's new year". Nothing is written.

    Holidays are asked for across the whole year in ONE call, because that is
    the shape the protocol offers (`holidays(start_fixed, end_fixed)`) and
    asking month by month would make a provider that scans days -- `hebrew`
    does -- do it twelve times over.
    """
    try:
        cal = store.calendars.get_provider({"provider": provider, "region": region})
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    # `year` defaults to THIS calendar's current year, not to a Gregorian one.
    # A Hebrew year is around 5786 and a homebrew calendar's could be anything,
    # so a hardcoded default is a date most calendars cannot represent -- which
    # is exactly how this route first failed on `hebrew`.
    if not year:
        try:
            # The reader's own day, deliberately: "what year is it in this
            # calendar" is a wall-clock question about where they are, not a
            # UTC one. `astimezone()` takes the local zone explicitly so this
            # is a stated choice rather than a naive `today()`.
            today = datetime.now().astimezone().date()
            year = int(cal.describe(today.toordinal())["year"])
        except (store.calendars.CalendarError, KeyError, TypeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        months = cal.months(year)
        # The year's bounds in fixed days, taken from the calendar's own first
        # and last day rather than assumed: a provider's year is not
        # necessarily 365 days or twelve months, which is the entire reason
        # `months()` exists.
        first = cal.parse(f"{year}-{months[0]['key']}-01")
        last = cal.parse(f"{year}-{months[-1]['key']}-{months[-1]['days']:02d}")
        found = cal.holidays(first, last)
    except (store.calendars.CalendarError, IndexError, KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Each observance placed in the calendar's own terms, and carrying the key
    # of the month it lands in.
    #
    # `month_key`, not `describe()["month"]`, and the difference is the whole
    # reason this loop is not two lines. The protocol's two halves disagree
    # about what names a month: `months()` yields a `key` (`"01"`, `"Tishrei"`)
    # and `describe()` yields a `month` NUMBER (8, 12). A reader grouping
    # observances under months by that number finds no month with that key and
    # silently renders a year with no holidays in it -- the worst kind of
    # wrong, because it looks like an answer.
    #
    # Matched on the display name, which both halves do agree on, with the
    # month's own key carried through. `fixed` rides along so a caller that
    # wants to sort or diff has the number rather than a string to parse back.
    by_name = {str(m.get("name", "")): m.get("key") for m in months}
    out = []
    for h in found:
        try:
            d = cal.describe(h["fixed"])
        except store.calendars.CalendarError:
            continue
        key = by_name.get(str(d.get("month_name", "")))
        if key is None:
            # A month `describe` names that `months` does not list. Nothing in
            # the shipped calendars does this; a plugin could. Dropping it
            # would hide it, so it is carried with no key and the reader can
            # still show it outside the month grid.
            key = ""
        out.append({"name": h["name"], "fixed": h["fixed"],
                    "month_key": key,
                    "month": d.get("month"), "month_name": d.get("month_name", ""),
                    "day": d.get("day"), "friendly": d.get("friendly", "")})

    names = {p["id"]: p["name"] for p in store.calendars.list_providers()}
    return {"id": provider, "name": names.get(provider, provider),
            "year": year, "region": region,
            "months": months, "holidays": out}


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
