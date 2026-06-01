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
from grimoire.util import canonicalize_character_ref

log = logging.getLogger(__name__)


def _character_ref_components(ref: str) -> tuple[str | None, str]:
    """Best-effort parse of a character_ref into ``(world_id, asset_id)``.

    Returns ``(world_id, asset_id)`` for library refs and
    ``(None, asset_id)`` for emergent ones. We accept the same shapes
    ``grimoire.characters.service._parse_character_ref`` handles.
    """
    # Normalize first so over-qualified world-PC refs (e.g.
    # ``<world>/worlds/<world>/characters/<id>``) parse correctly instead of
    # falling through to ``(None, <raw ref>)`` and showing the ref as the name.
    ref = canonicalize_character_ref(ref)
    if ref.startswith("campaign:emergent/"):
        _, _, rest = ref.partition("campaign:emergent/")
        parts = rest.strip("/").split("/")
        asset = parts[-1] if parts else ""
        return None, asset
    if ref.startswith("emergent/"):
        parts = ref.split("/")
        return None, parts[-1]
    if ref.startswith("library:"):
        _, _, path = ref.partition("library:")
        parts = path.strip("/").split("/")
        if len(parts) >= 4 and parts[0] == "worlds" and parts[2] in {"characters", "character"}:
            return parts[1], parts[3]
    parts = ref.strip("/").split("/")
    if len(parts) >= 4 and parts[0] == "worlds" and parts[2] in {"characters", "character"}:
        return parts[1], parts[3]
    return None, ref


async def _resolve_character_name(
    library: Any, world_id: str | None, asset_id: str, campaign_id: str | None = None
) -> str:
    if library is None:
        return asset_id
    if world_id is not None:
        try:
            entity = await library.get_entity(world_id, "characters", asset_id)
            fm = getattr(entity, "frontmatter", {}) or {}
            return str(fm.get("name") or asset_id)
        except Exception as e:
            log.debug("library.get_entity failed for %s/%s: %s", world_id, asset_id, e)
            return asset_id
    if campaign_id is not None:
        # Emergent (campaign-local) characters — PCs and spawned NPCs — take
        # precedence per the read cascade and are NOT returned by
        # list_for_composition, which only walks world refs. Their display name
        # lives in the emergent content, so resolve that first.
        store = getattr(library, "store", None)
        if store is not None:
            try:
                emergent = await store.get_emergent(campaign_id, "character", asset_id)
                if emergent:
                    fm = emergent.get("frontmatter") or {}
                    if fm.get("name"):
                        return str(fm["name"])
            except Exception as exc:
                log.debug("get_emergent failed for %s/%s: %s", campaign_id, asset_id, exc)
        try:
            entities = await library.list_for_composition(campaign_id, "characters")
            for e in entities:
                if getattr(e, "asset_id", None) == asset_id:
                    fm = getattr(e, "frontmatter", {}) or {}
                    return str(fm.get("name") or asset_id)
        except Exception as exc:
            log.debug("list_for_composition failed for %s: %s", campaign_id, exc)
    return asset_id


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
            name = await _resolve_character_name(library, world_id, asset_id, campaign_id)
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


def _active_threads_fetcher():
    async def fetch(_w: HudWidget, _c: str, scene: Any, _o: Any) -> dict[str, Any]:
        if scene is None:
            return {"items": []}
        introduced = getattr(scene, "threads_introduced", None) or []
        raw_paid = getattr(scene, "threads_paid_off", None) or []
        paid_off = {getattr(t, "text", "") for t in raw_paid}
        items = [
            {"text": getattr(t, "text", str(t))}
            for t in introduced
            if getattr(t, "text", str(t)) not in paid_off
        ]
        return {"items": items}

    return fetch


def _inventory_fetcher(store: Any):
    async def fetch(_w: HudWidget, campaign_id: str, _scene: Any, _observer: Any) -> dict[str, Any]:
        if store is None:
            return {"items": []}
        # Read the per-campaign toggle through the store's public config API
        # rather than importing the inventory module (no module-map arrow
        # hud -> inventory). Default off when unset.
        cfg = await store.get_campaign_config(campaign_id) or {}
        if not (cfg.get("inventory") or {}).get("enabled", False):
            return {"items": []}
        rows = await store.list_inventory_holdings(campaign_id)
        grouped: dict[str, list[str]] = {}
        for r in rows:
            label = f"{r['holder_kind']}:{r['holder_id']}"
            qty = f" x{r['quantity']}" if r["quantity"] != 1 else ""
            eq = " (equipped)" if r["equipped"] else ""
            grouped.setdefault(label, []).append(f"{r['item_name']}{qty}{eq}")
        items = [{"text": f"{label}: {', '.join(things)}"} for label, things in grouped.items()]
        return {"items": items}

    return fetch


def _empty_fetcher_with(default: Any):
    async def fetch(_w: HudWidget, _c: str, _s: Any, _o: Any) -> Any:
        return default

    return fetch


def _scene_in_game_dt(scene: Any) -> Any:
    """Best-effort extraction of a datetime from ``scene.in_game_start``.

    SceneRecord stores it as a plain ``datetime`` after YAML parses an
    ISO string; some pathways may wrap it in an ``InGameTime``. Accept
    either, return ``None`` on anything else.
    """
    val = getattr(scene, "in_game_start", None) if scene is not None else None
    if val is None:
        return None
    if hasattr(val, "moment"):
        return val.moment
    return val


async def _primary_world_id(library: Any, campaign_id: str) -> str | None:
    """Return the highest-priority (lowest priority number) world_id, or None."""
    if library is None:
        return None
    try:
        comp = await library.get_composition(campaign_id)
    except Exception as e:
        log.debug("library.get_composition failed for %s: %s", campaign_id, e)
        return None
    refs = list(getattr(comp, "worlds", []) or [])
    if not refs:
        return None
    refs.sort(key=lambda r: getattr(r, "priority", 0))
    return getattr(refs[0], "world_id", None)


def _format_date(dt: Any, calendar: Any) -> str:
    """Format ``dt`` as a human-readable in-game date.

    Uses the world calendar's month + weekday names when configured,
    otherwise falls back to ISO date.
    """
    if dt is None:
        return "—"
    months = list(getattr(calendar, "months", []) or []) if calendar else []
    weekdays = list(getattr(calendar, "week_day_names", []) or []) if calendar else []
    month_name = months[dt.month - 1].name if 0 < dt.month <= len(months) else f"M{dt.month}"
    weekday = ""
    if weekdays:
        # Python weekday(): Monday=0..Sunday=6; if the calendar lists 7 names
        # in Mon..Sun order, line up directly.
        idx = dt.weekday() % len(weekdays)
        weekday = f"{weekdays[idx]}, "
    return f"{weekday}{month_name} {dt.day}, {dt.year}"


def _format_time(dt: Any) -> dict[str, Any]:
    """Return both clock time and a coarse time-of-day band."""
    if dt is None:
        return {"value": "—", "clock": "—", "band": "—"}
    hour = dt.hour
    if 5 <= hour < 12:
        band = "morning"
    elif 12 <= hour < 17:
        band = "afternoon"
    elif 17 <= hour < 21:
        band = "evening"
    else:
        band = "night"
    clock = f"{dt.hour:02d}:{dt.minute:02d}"
    return {"value": f"{clock} ({band})", "clock": clock, "band": band}


def _in_game_date_fetcher(world: Any):
    async def fetch(_w: HudWidget, campaign_id: str, scene: Any, _o: Any) -> dict[str, Any]:
        dt = _scene_in_game_dt(scene)
        if dt is None:
            return {"value": "—"}
        cal = None
        if world is not None:
            try:
                cal = await world.calendar_for_campaign(campaign_id)
            except Exception as e:
                log.debug("world.calendar_for_campaign failed: %s", e)
        return {
            "value": _format_date(dt, cal),
            "iso": dt.isoformat() if hasattr(dt, "isoformat") else str(dt),
        }

    return fetch


def _in_game_time_fetcher():
    async def fetch(_w: HudWidget, _c: str, scene: Any, _o: Any) -> dict[str, Any]:
        return _format_time(_scene_in_game_dt(scene))

    return fetch


def _weather_fetcher(world: Any, library: Any):
    async def fetch(_w: HudWidget, campaign_id: str, scene: Any, _o: Any) -> dict[str, Any]:
        dt = _scene_in_game_dt(scene)
        loc = getattr(scene, "location_ref", None) if scene is not None else None
        if world is None or dt is None or not loc:
            return {"value": "—"}
        world_id = await _primary_world_id(library, campaign_id)
        if not world_id:
            return {"value": "—"}
        try:
            from grimoire.types.common import InGameTime

            weather = await world.weather_for(world_id, loc, InGameTime(moment=dt), campaign_id)
        except Exception as e:
            log.debug("world.weather_for failed: %s", e)
            return {"value": "—"}
        summary = getattr(weather, "summary", "") or str(getattr(weather, "kind", "") or "—")
        return {
            "value": summary,
            "kind": str(getattr(weather, "kind", "")) or None,
            "palette": getattr(weather, "palette", "") or None,
        }

    return fetch


def _temperature_fetcher(world: Any, library: Any):
    async def fetch(_w: HudWidget, campaign_id: str, scene: Any, _o: Any) -> dict[str, Any]:
        dt = _scene_in_game_dt(scene)
        loc = getattr(scene, "location_ref", None) if scene is not None else None
        if world is None or dt is None or not loc:
            return {"value": "—"}
        world_id = await _primary_world_id(library, campaign_id)
        if not world_id:
            return {"value": "—"}
        try:
            from grimoire.types.common import InGameTime

            weather = await world.weather_for(world_id, loc, InGameTime(moment=dt), campaign_id)
        except Exception:
            return {"value": "—"}
        temp = getattr(weather, "temperature_c", None)
        if temp is None:
            return {"value": "—"}
        return {"value": f"{temp:.0f}°C", "celsius": temp}

    return fetch


def register_default_fetchers(
    hud: HudService,
    *,
    hud_config: HudConfigService,
    library: Any = None,
    extras: Any = None,
    continuity: Any = None,
    scenes: Any = None,
    world: Any = None,
    store: Any = None,
) -> None:
    """Register the canonical owner fetchers on ``hud``.

    Best-effort: any widget whose owner service is absent gets an
    empty-payload fetcher so the snapshot is ``ok`` (with empty data)
    rather than ``error`` (no fetcher registered).
    """
    hud.register_fetcher("core.in-game-date", _in_game_date_fetcher(world))
    hud.register_fetcher("core.in-game-time", _in_game_time_fetcher())
    hud.register_fetcher("core.weather", _weather_fetcher(world, library))
    hud.register_fetcher("core.temperature", _temperature_fetcher(world, library))
    hud.register_fetcher("core.location", _scene_location_fetcher())
    hud.register_fetcher("core.present-cast", _present_cast_fetcher(library, extras, hud_config))
    hud.register_fetcher("core.recent-events", _recent_facts_fetcher(continuity))
    hud.register_fetcher("core.active-commitments", _commitments_fetcher(continuity))
    hud.register_fetcher("core.scene-summary", _scene_summary_fetcher())
    hud.register_fetcher("core.drift-alerts", _empty_fetcher_with({"items": []}))
    hud.register_fetcher("core.review-queue", _empty_fetcher_with({"count": 0}))
    hud.register_fetcher("core.active-threads", _active_threads_fetcher())
    hud.register_fetcher("core.inventory", _inventory_fetcher(store))

    # The HudService also needs scene hooks so the aggregator can pass the
    # scene into fetchers' ``scene`` arg.
    if scenes is not None:
        if hud.current_scene is None:

            async def _current_scene(campaign_id: str) -> Any:
                try:
                    return await scenes.active_scene_for_campaign(campaign_id)
                except Exception as e:
                    log.debug("active_scene_for_campaign failed for %s: %s", campaign_id, e)
                    return None

            hud.current_scene = _current_scene

        if hud.get_scene is None:

            async def _get_scene(scene_id: str) -> Any:
                try:
                    return await scenes.get_scene(scene_id)
                except Exception as e:
                    log.debug("get_scene(%s) failed: %s", scene_id, e)
                    return None

            hud.get_scene = _get_scene


__all__ = ["register_default_fetchers"]
