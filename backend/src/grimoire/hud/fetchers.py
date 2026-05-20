"""Default widget fetchers for the Scene HUD.

Each fetcher is a thin adapter from a :class:`grimoire.types.hud.HudWidget`
to the canonical owner service. They're registered on a constructed
:class:`HudService` during application startup so the in-process dispatch
path (see ``HudService._fetch``) can hand off without re-entering
FastAPI's HTTP stack.

Owner services that aren't wired (e.g. on a bare test install) cause the
relevant fetcher to short-circuit to ``None``-or-empty data; the
aggregator surfaces those as ``status="error"`` snapshots, which the
frontend renders as a benign empty row.
"""

from __future__ import annotations

import logging
from typing import Any

from grimoire.hud.config import HudConfigService
from grimoire.hud.service import HudService
from grimoire.types.common import EntityKind
from grimoire.types.hud import HudWidget

log = logging.getLogger(__name__)


def _character_ref_components(ref: str) -> tuple[str | None, str]:
    """Best-effort parse of a character_ref into ``(world_id, asset_id)``.

    Returns ``(world_id, asset_id)`` for library refs and
    ``(None, asset_id)`` for emergent ones. We accept the same shapes
    ``grimoire.characters.service._parse_character_ref`` handles.
    """
    if ref.startswith("campaign:emergent/"):
        _, _, rest = ref.partition("campaign:emergent/")
        parts = rest.strip("/").split("/")
        asset = parts[-1] if parts else ""
        return None, asset
    if ref.startswith("library:"):
        _, _, path = ref.partition("library:")
        parts = path.strip("/").split("/")
        if len(parts) >= 4 and parts[0] == "worlds" and parts[2] in {"characters", "character"}:
            return parts[1], parts[3]
    parts = ref.strip("/").split("/")
    if len(parts) >= 4 and parts[0] == "worlds" and parts[2] in {"characters", "character"}:
        return parts[1], parts[3]
    return None, ref


async def _resolve_character_name(library: Any, world_id: str | None, asset_id: str) -> str:
    if library is None or world_id is None:
        return asset_id
    try:
        entity = await library.get_entity(world_id, "characters", asset_id)
    except Exception as e:
        log.debug("library.get_entity failed for %s/%s: %s", world_id, asset_id, e)
        return asset_id
    fm = getattr(entity, "frontmatter", {}) or {}
    name = fm.get("name") or asset_id
    return str(name)


async def _pinned_extras_for_character(
    extras: Any,
    hud_config: HudConfigService,
    campaign_id: str,
    world_id: str | None,
    asset_id: str,
) -> list[dict[str, Any]]:
    if extras is None:
        return []
    try:
        cfg = hud_config.load(campaign_id)
    except Exception as e:
        log.debug("hud_config.load failed for %s: %s", campaign_id, e)
        return []
    keys = cfg.pinned_extras.by_character.get(asset_id, [])
    if not keys:
        return []
    try:
        resolved = await extras.get(
            entity_kind=EntityKind.CHARACTER,
            entity_id=asset_id,
            campaign_id=campaign_id,
            world_id=world_id,
        )
    except Exception as e:
        log.debug("extras.get failed for %s: %s", asset_id, e)
        return []
    out: list[dict[str, Any]] = []
    for key in keys:
        extra = resolved.get(key)
        if extra is None:
            continue
        out.append({"key": key, "value": extra.value, "scope": extra.scope.value})
    return out


def _present_cast_fetcher(library: Any, extras: Any, hud_config: HudConfigService):
    async def fetch(_w: HudWidget, campaign_id: str, scene: Any, _observer: Any) -> dict[str, Any]:
        if scene is None:
            return {"chips": []}
        refs = getattr(scene, "present_character_refs", None) or []
        chips: list[dict[str, Any]] = []
        for ref in refs:
            try:
                world_id, asset_id = _character_ref_components(str(ref))
            except Exception:
                continue
            name = await _resolve_character_name(library, world_id, asset_id)
            pinned = await _pinned_extras_for_character(
                extras, hud_config, campaign_id, world_id, asset_id
            )
            chips.append(
                {
                    "character_id": asset_id,
                    "character_ref": str(ref),
                    "name": name,
                    "portrait_url": None,
                    "source": "library" if world_id else "campaign-local",
                    "pinned_extras": pinned,
                }
            )
        return {"chips": chips}

    return fetch


def _scene_summary_fetcher():
    async def fetch(_w: HudWidget, _campaign_id: str, scene: Any, _observer: Any) -> dict[str, Any]:
        if scene is None:
            return {"text": ""}
        return {"text": str(getattr(scene, "summary", "") or "")}

    return fetch


def _scene_location_fetcher():
    async def fetch(_w: HudWidget, _campaign_id: str, scene: Any, _observer: Any) -> dict[str, Any]:
        if scene is None:
            return {"value": "—"}
        loc = getattr(scene, "location_ref", None) or getattr(scene, "location", None) or "—"
        return {"value": str(loc)}

    return fetch


def _commitments_fetcher(continuity: Any):
    async def fetch(_w: HudWidget, campaign_id: str, _scene: Any, _observer: Any) -> dict[str, Any]:
        if continuity is None:
            return {"items": []}
        try:
            commitments = await continuity.list_commitments(campaign_id, status="active")
        except Exception as e:
            log.debug("continuity.list_commitments failed: %s", e)
            return {"items": []}
        items = [{"text": getattr(c, "text", str(c))} for c in commitments[:5]]
        return {"items": items}

    return fetch


def _recent_facts_fetcher(continuity: Any):
    async def fetch(_w: HudWidget, campaign_id: str, _scene: Any, _observer: Any) -> dict[str, Any]:
        if continuity is None:
            return {"items": []}
        try:
            facts = await continuity.list_recent_facts(campaign_id, limit=5)
        except Exception as e:
            log.debug("continuity.list_recent_facts failed: %s", e)
            return {"items": []}
        return {"items": [{"text": getattr(f, "text", str(f))} for f in facts]}

    return fetch


def _empty_fetcher_with(default: Any):
    async def fetch(_w: HudWidget, _c: str, _s: Any, _o: Any) -> Any:
        return default

    return fetch


def register_default_fetchers(
    hud: HudService,
    *,
    hud_config: HudConfigService,
    library: Any = None,
    extras: Any = None,
    continuity: Any = None,
    scenes: Any = None,
) -> None:
    """Register the canonical owner fetchers on ``hud``.

    Best-effort: any widget whose owner service is absent gets an
    empty-payload fetcher so the snapshot is ``ok`` (with empty data)
    rather than ``error`` (no fetcher registered).
    """
    hud.register_fetcher("core.in-game-date", _empty_fetcher_with({"value": "—"}))
    hud.register_fetcher("core.in-game-time", _empty_fetcher_with({"value": "—"}))
    hud.register_fetcher("core.weather", _empty_fetcher_with({"value": "—"}))
    hud.register_fetcher("core.temperature", _empty_fetcher_with({"value": "—"}))
    hud.register_fetcher("core.location", _scene_location_fetcher())
    hud.register_fetcher("core.present-cast", _present_cast_fetcher(library, extras, hud_config))
    hud.register_fetcher("core.recent-events", _recent_facts_fetcher(continuity))
    hud.register_fetcher("core.active-commitments", _commitments_fetcher(continuity))
    hud.register_fetcher("core.scene-summary", _scene_summary_fetcher())
    hud.register_fetcher("core.drift-alerts", _empty_fetcher_with({"items": []}))
    hud.register_fetcher("core.review-queue", _empty_fetcher_with({"count": 0}))
    hud.register_fetcher("core.active-threads", _empty_fetcher_with({"items": []}))

    # The HudService also needs a current_scene hook so the aggregator
    # can pass the scene into fetchers' ``scene`` arg.
    if scenes is not None and hud.current_scene is None:

        async def _current_scene(campaign_id: str) -> Any:
            try:
                return await scenes.active_scene_for_campaign(campaign_id)
            except Exception as e:
                log.debug("active_scene_for_campaign failed for %s: %s", campaign_id, e)
                return None

        hud.current_scene = _current_scene


__all__ = ["register_default_fetchers"]
