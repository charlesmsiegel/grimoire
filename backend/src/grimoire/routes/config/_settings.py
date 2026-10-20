"""config: settings responsibilities."""

from __future__ import annotations

import logging
import math
from typing import NamedTuple

from fastapi import Depends, HTTPException

from ... import health, store, wire
from ... import inference as operations
from ...store.inference import resolve as inference
from ...store.inference import settings as inference_settings
from ..common import (
    _dump,
    _soft_resolved,
    get_health,
    legacy_refused,
    refuse_newer,
    require_inference,
)
from ..models import CampaignPlayDecisions, ConfigUpdate, PromptLayoutUpdate
from ._router import router as router

log = logging.getLogger('grimoire.routes.config')


# ---- config ----
class _Chat(NamedTuple):
    """What chat would run on with no campaign in front of it, for the header:
    the primary attempt's target (None when nothing resolves), whether the
    resolution could be made at all, and whether the seam would serve it
    (`inference.refusal`, the one decision -- the header's `ready`)."""

    target: wire.Target | None
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
        chain = resolved.chain
        return _Chat(chain.primary if chain is not None else None, True,
                     inference.refusal(resolved) is None)
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
        return store.post_images.reach(chat.target)
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
    active = chat.target
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
            "history_recall_depth": cfg.get("history_recall_depth", store.config.DEFAULT_HISTORY_RECALL_DEPTH),
            "history_recall_budget": cfg.get("history_recall_budget", store.config.DEFAULT_HISTORY_RECALL_BUDGET),
            "history_recall_threshold": cfg.get("history_recall_threshold", store.config.DEFAULT_HISTORY_RECALL_THRESHOLD),
            "history_recall_rerank": cfg.get("history_recall_rerank", store.config.DEFAULT_HISTORY_RECALL_RERANK),
            "history_plan": cfg.get("history_plan", store.config.DEFAULT_HISTORY_PLAN),
            "history_plan_repair": cfg.get("history_plan_repair", store.config.DEFAULT_HISTORY_PLAN_REPAIR),
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
            # The play-decision switches (roadmap 02 §3.6), reported as
            # stored so a write reads back; Settings, Play decisions edits
            # them (02-S6).
            "decide_speaker_sampling": cfg.get("decide_speaker_sampling",
                                               store.config.DEFAULT_DECIDE_SPEAKER_SAMPLING),
            "decide_turn_plan": cfg.get("decide_turn_plan",
                                        store.config.DEFAULT_DECIDE_TURN_PLAN),
            "decide_play_tool": cfg.get("decide_play_tool",
                                        store.config.DEFAULT_DECIDE_PLAY_TOOL),
            # Spec 11's Decision stage (11-S6): reported so a write reads
            # back, and drawn by no control until its play gate is ratified.
            "epistemic_decide": cfg.get("epistemic_decide",
                                        store.config.DEFAULT_EPISTEMIC_DECIDE),
            # Roadmap 12's investigation switches and spend ceiling (§3, §7.1):
            # flipping one spends nothing; each entry point is a reader's
            # action on a play surface.
            "investigation_continuity": cfg.get(
                "investigation_continuity", store.config.DEFAULT_INVESTIGATION_CONTINUITY),
            "investigation_question": cfg.get(
                "investigation_question", store.config.DEFAULT_INVESTIGATION_QUESTION),
            "investigation_spend_ceiling": cfg.get(
                "investigation_spend_ceiling",
                store.config.DEFAULT_INVESTIGATION_SPEND_CEILING),
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
            "active_connection_id": active.provider_id if active else "",
            # `model` rides along because the global status bar names the model
            # a scene with no campaign choice of its own will use (a campaign's
            # own is the scene header's business). Reading it here keeps the
            # bar off /llm-connections/{id}, whose payload carries key_set and
            # the base URL it has no business fetching to print one string. It
            # is the *effective* model (the target's): a Claude connection with
            # none configured still generates, on the dispatcher's fallback, so
            # reporting the bare "" would show a dash for a connection that is
            # about to run.
            "active_connection": ({"id": active.provider_id, "kind": active.kind,
                                   "name": active.provider_name, "model": active.model}
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
            "health": registry.status(active.provider_id, active.rev) if active else None,
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


def _ceiling_ok(value: str | None) -> bool:
    """An investigation spend ceiling (roadmap 12 §7.1): blank, which is no
    spend axis, or a finite dollar amount above zero -- refused otherwise
    rather than read as none, since a ceiling typed and silently ignored is
    exactly the limit a reader thought they had."""
    if value is None or not value.strip():
        return True
    try:
        amount = float(value)
    except ValueError:
        return False
    return math.isfinite(amount) and amount > 0


#: Config keys whose only meaningful values are "on" and "off".
_ON_OFF_KEYS = ("tracker", "perception_rider", "send_images",
                "decide_speaker_sampling", "decide_play_tool", "epistemic_decide",
                "investigation_continuity", "investigation_question")


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
    if ("decide_turn_plan" in fields
            and fields["decide_turn_plan"] not in store.play_decisions.TURN_PLAN_MODES):
        raise HTTPException(status_code=400,
                            detail="decide_turn_plan must be 'off', 'intent' or 'plan'")
    # A bound, refused rather than clamped: the cap in `config.lore_recursion_depth`
    # is for a hand edit, and a value typed into the page that silently became
    # another one would be a setting nobody chose. Blank clears to the default.
    if not _ceiling_ok(fields.get("investigation_spend_ceiling")):
        raise HTTPException(status_code=400, detail=(
            "investigation_spend_ceiling must be blank (no ceiling) or a dollar "
            "amount above zero"))
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


# --- play decisions (roadmap 02 §3.6) -----------------------------------------

#: The campaign route's field for each switch's config key.
_PLAY_FIELDS = {"speaker_sampling": store.play_decisions.SPEAKER_SAMPLING_KEY,
                "turn_plan": store.play_decisions.TURN_PLAN_KEY,
                "play_tool": store.play_decisions.PLAY_TOOL_KEY}


@router.get("/play-decisions")
def get_play_decisions():
    """What the Settings section says beside the switches: whether the global
    Decision model reports a distribution for the speaker pick (§5.6), None
    when the pick resolves to nothing. Soft and free: nothing is sent."""
    resolved, _why, _kind = _soft_resolved(
        lambda: require_inference("response-selector", operation="decide"))
    return {"pick_samples": (None if resolved is None
                             else operations.reports_distribution(resolved))}


def _play_body(cid: str) -> dict:
    try:
        own = store.play_decisions.own(cid)
        effective = store.play_decisions.effective(cid)
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    return {field: own[key] for field, key in _PLAY_FIELDS.items()} | {
        "effective": {field: effective[key] for field, key in _PLAY_FIELDS.items()}}


@router.get("/campaigns/{cid}/play-decisions")
def get_campaign_play_decisions(cid: str):
    return _play_body(cid)


@router.put("/campaigns/{cid}/play-decisions")
def put_campaign_play_decisions(cid: str, body: CampaignPlayDecisions):
    # The campaign first, as the tracker's route: a 400 about a value is an
    # answer about a campaign, and one that does not exist is a 404.
    _play_body(cid)
    values = {key: value for field, key in _PLAY_FIELDS.items()
              if (value := getattr(body, field)) is not None}
    try:
        store.play_decisions.set_campaign(cid, values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    return _play_body(cid)


@router.get("/prompt-layout")
def get_prompt_layout():
    return _layout_body(store.context.layout.read_layout())


@router.put("/prompt-layout")
def put_prompt_layout(update: PromptLayoutUpdate):
    # The whole list replaces the stored one; an empty list is Reset.
    return _layout_body(
        store.context.layout.write_layout([_dump(s) for s in update.sections]))
