"""Campaign settings routes: routing, tiers, imagegen, summaries, etc."""

from __future__ import annotations

import contextlib
from typing import Any

from fastapi import APIRouter, HTTPException

from grimoire.api.deps import LLMGatewayDep, StateStoreDep

from .helpers import (
    _load_campaign_config,
    _read_routing_blocks,
    _require_campaign_row,
    _write_campaign_config,
)
from .schemas import (
    AdvancedSettingsPayload,
    ExpressionsSettingsPayload,
    GenerationSettingsPayload,
    ImageGenSettingsPayload,
    IntegratedDeltasPayload,
    NarratorSettingsPayload,
    RoutingPayload,
    StorageSettingsPayload,
    SummariesSettingsPayload,
    TierSettingsPayload,
)

router = APIRouter()


@router.get("/{campaign_id}/routing")
async def get_campaign_routing(campaign_id: str, state_store: StateStoreDep) -> Any:
    await _require_campaign_row(state_store, campaign_id)
    return _read_routing_blocks(state_store, campaign_id)


@router.put("/{campaign_id}/routing")
async def set_campaign_routing(
    campaign_id: str,
    payload: RoutingPayload,
    state_store: StateStoreDep,
    gateway: LLMGatewayDep,
) -> Any:
    await _require_campaign_row(state_store, campaign_id)

    async def _apply(block: dict[str, str | None], kind: str) -> None:
        for task, value in block.items():
            if value is None or value == "":
                with contextlib.suppress(ValueError):
                    await gateway.clear_route(task, campaign_id=campaign_id, kind=kind)
            else:
                try:
                    await gateway.set_route(task, value, campaign_id=campaign_id, kind=kind)
                except ValueError as exc:
                    raise HTTPException(
                        status_code=422,
                        detail=f"invalid {kind} route for task {task!r}: {exc}",
                    ) from exc

    await _apply(payload.llm, "llm")
    await _apply(payload.embedding, "embedding")
    await _apply(payload.imagegen, "imagegen")
    return _read_routing_blocks(state_store, campaign_id)


@router.get("/{campaign_id}/tiers")
async def get_campaign_tiers(
    campaign_id: str,
    state_store: StateStoreDep,
) -> Any:
    await _require_campaign_row(state_store, campaign_id)
    from grimoire.files.yaml_io import load_yaml

    data_root = getattr(state_store, "data_root", None)
    tiers: dict[str, str | None] = {"heavy": None, "light": None, "embedding": None}
    if data_root is None:
        return tiers
    yaml_path = data_root / "campaigns" / campaign_id / "campaign.yaml"
    if not yaml_path.is_file():
        return tiers
    try:
        raw = load_yaml(yaml_path)
    except Exception:
        return tiers
    if not isinstance(raw, dict):
        return tiers
    block = raw.get("model_tiers") or {}
    if isinstance(block, dict):
        for k in ("heavy", "light", "embedding"):
            v = block.get(k)
            if isinstance(v, str) and v:
                tiers[k] = v
    return tiers


@router.put("/{campaign_id}/tiers")
async def set_campaign_tiers(
    campaign_id: str,
    payload: TierSettingsPayload,
    state_store: StateStoreDep,
    gateway: LLMGatewayDep,
) -> Any:
    from grimoire.llm_gateway.routing import Route
    from grimoire.llm_gateway.tiers import Tier

    await _require_campaign_row(state_store, campaign_id)
    for value in (payload.heavy, payload.light, payload.embedding):
        if value is None:
            continue
        try:
            Route.parse(value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    for tier, value in (
        (Tier.HEAVY, payload.heavy),
        (Tier.LIGHT, payload.light),
        (Tier.EMBEDDING, payload.embedding),
    ):
        if value is None:
            await gateway.clear_tier_route(campaign_id, tier)
        else:
            await gateway.set_tier_route(campaign_id, tier, value)

    return {
        "heavy": payload.heavy,
        "light": payload.light,
        "embedding": payload.embedding,
    }


@router.get("/{campaign_id}/imagegen")
async def get_campaign_imagegen(campaign_id: str, state_store: StateStoreDep) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    imagegen = cfg.get("imagegen") or {}
    return {
        "backend": imagegen.get("backend"),
        "preset": imagegen.get("preset"),
        "sampler_defaults": imagegen.get("sampler_defaults"),
    }


@router.put("/{campaign_id}/imagegen")
async def set_campaign_imagegen(
    campaign_id: str,
    payload: ImageGenSettingsPayload,
    state_store: StateStoreDep,
) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    cfg["imagegen"] = {
        "backend": payload.backend or None,
        "preset": payload.preset or None,
        "sampler_defaults": payload.sampler_defaults,
    }
    await _write_campaign_config(state_store, campaign_id, cfg)
    return cfg["imagegen"]


@router.get("/{campaign_id}/summaries")
async def get_campaign_summaries(
    campaign_id: str,
    state_store: StateStoreDep,
) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    block = cfg.get("summaries") or {}
    return {
        "running_every_n_posts": int(block.get("running_every_n_posts", 5)),
        "final_on_close": bool(block.get("final_on_close", True)),
    }


@router.put("/{campaign_id}/summaries")
async def set_campaign_summaries(
    campaign_id: str,
    payload: SummariesSettingsPayload,
    state_store: StateStoreDep,
) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    cfg["summaries"] = {
        "running_every_n_posts": int(payload.running_every_n_posts),
        "final_on_close": bool(payload.final_on_close),
    }
    await _write_campaign_config(state_store, campaign_id, cfg)
    return cfg["summaries"]


@router.get("/{campaign_id}/integrated-deltas")
async def get_integrated_deltas(
    campaign_id: str,
    state_store: StateStoreDep,
) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    return {"enabled": bool(cfg.get("integrated_deltas", False))}


@router.put("/{campaign_id}/integrated-deltas")
async def set_integrated_deltas(
    campaign_id: str,
    payload: IntegratedDeltasPayload,
    state_store: StateStoreDep,
) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    cfg["integrated_deltas"] = payload.enabled
    await _write_campaign_config(state_store, campaign_id, cfg)
    return {"enabled": payload.enabled}


@router.get("/{campaign_id}/storage")
async def get_campaign_storage(campaign_id: str, state_store: StateStoreDep) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    storage = cfg.get("storage") or {}
    return {
        "schedule": storage.get("schedule", "off"),
        "retention_days": int(storage.get("retention_days", 30)),
    }


@router.put("/{campaign_id}/storage")
async def set_campaign_storage(
    campaign_id: str,
    payload: StorageSettingsPayload,
    state_store: StateStoreDep,
) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    cfg["storage"] = {
        "schedule": payload.schedule,
        "retention_days": int(payload.retention_days),
    }
    await _write_campaign_config(state_store, campaign_id, cfg)
    return cfg["storage"]


@router.get("/{campaign_id}/advanced")
async def get_campaign_advanced(campaign_id: str, state_store: StateStoreDep) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    advanced = cfg.get("advanced") or {}
    return {
        "debug_log": bool(advanced.get("debug_log", False)),
        "per_task_prompts": dict(advanced.get("per_task_prompts") or {}),
    }


@router.put("/{campaign_id}/advanced")
async def set_campaign_advanced(
    campaign_id: str,
    payload: AdvancedSettingsPayload,
    state_store: StateStoreDep,
) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    cfg["advanced"] = {
        "debug_log": bool(payload.debug_log),
        "per_task_prompts": {
            k: v for k, v in payload.per_task_prompts.items() if isinstance(v, str)
        },
    }
    await _write_campaign_config(state_store, campaign_id, cfg)
    return cfg["advanced"]


@router.get("/{campaign_id}/generation")
async def get_campaign_generation(campaign_id: str, state_store: StateStoreDep) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    gen = cfg.get("generation") or {}
    return {
        "max_tokens": gen.get("max_tokens"),
        "temperature": gen.get("temperature"),
    }


@router.put("/{campaign_id}/generation")
async def set_campaign_generation(
    campaign_id: str,
    payload: GenerationSettingsPayload,
    state_store: StateStoreDep,
) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    block: dict[str, Any] = {}
    if payload.max_tokens is not None:
        block["max_tokens"] = int(payload.max_tokens)
    if payload.temperature is not None:
        block["temperature"] = float(payload.temperature)
    cfg["generation"] = block
    await _write_campaign_config(state_store, campaign_id, cfg)
    return {
        "max_tokens": block.get("max_tokens"),
        "temperature": block.get("temperature"),
    }


@router.get("/{campaign_id}/narrator")
async def get_campaign_narrator(campaign_id: str, state_store: StateStoreDep) -> Any:
    from grimoire.scenes.narrator_mode import DEFAULT_RESPONSE_MODE, normalize_response_mode

    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    narrator = cfg.get("narrator") or {}
    mode = normalize_response_mode(narrator.get("response_mode")) or DEFAULT_RESPONSE_MODE
    return {"response_mode": mode}


@router.put("/{campaign_id}/narrator")
async def set_campaign_narrator(
    campaign_id: str,
    payload: NarratorSettingsPayload,
    state_store: StateStoreDep,
) -> Any:
    from grimoire.scenes.narrator_mode import RESPONSE_MODES, normalize_response_mode

    mode = normalize_response_mode(payload.response_mode)
    if mode is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"response_mode must be one of {list(RESPONSE_MODES)}, "
                f"got {payload.response_mode!r}"
            ),
        )
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    cfg["narrator"] = {"response_mode": mode}
    await _write_campaign_config(state_store, campaign_id, cfg)
    return cfg["narrator"]


@router.get("/{campaign_id}/expressions")
async def get_expressions_settings(
    campaign_id: str,
    state_store: StateStoreDep,
) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    block = cfg.get("expressions") or {}
    chars = block.get("enabled_characters") or []
    return {"enabled_characters": [c for c in chars if isinstance(c, str)]}


@router.put("/{campaign_id}/expressions")
async def set_expressions_settings(
    campaign_id: str,
    payload: ExpressionsSettingsPayload,
    state_store: StateStoreDep,
) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    cfg["expressions"] = {"enabled_characters": list(payload.enabled_characters)}
    await _write_campaign_config(state_store, campaign_id, cfg)
    return {"enabled_characters": list(payload.enabled_characters)}
