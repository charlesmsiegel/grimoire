"""WorldContextResolver — location, weather, factions, calendar."""

from __future__ import annotations

import logging
from typing import Any

from grimoire.context.config import ContextBuilderConfig
from grimoire.context.types import TierItem, make_source_id
from grimoire.templates import render as render_template
from grimoire.types.common import CampaignId
from grimoire.types.context import ContextSource
from grimoire.types.inclusion_reasons import InclusionReason
from grimoire.types.state import ContextTier

logger = logging.getLogger(__name__)


class WorldContextResolver:
    def __init__(
        self,
        *,
        world: Any,
        library: Any,
        time_engine: Any | None = None,
        config: ContextBuilderConfig,
    ) -> None:
        self._world = world
        self._library = library
        self._time_engine = time_engine
        self._config = config

    async def resolve_world(
        self,
        *,
        scene: Any,
        campaign_id: CampaignId,
        branch_id: str | None,
    ) -> tuple[list[TierItem], list[TierItem]]:
        spotlight: list[TierItem] = []
        background: list[TierItem] = []
        if scene is None:
            return spotlight, background

        location_ref = getattr(scene, "location_ref", None)
        if location_ref:
            world_id, location_id = _parse_location_ref(location_ref)
        else:
            world_id, location_id = None, None

        if world_id and location_id and self._world is not None:
            try:
                location = await self._world.get_location(world_id, location_id)
            except Exception:
                location = None
            if location is not None:
                desc = _render_location(location)
                location_owner = f"library:worlds/{world_id}/locations/{location_id}"
                spotlight.append(
                    TierItem(
                        tier=ContextTier.SPOTLIGHT,
                        section="location",
                        text=desc,
                        priority=8,
                        source=ContextSource(
                            kind="location",
                            scope="library",
                            owner_id=location_owner,
                            tier=ContextTier.SPOTLIGHT,
                            summary=location.name,
                            source_id=make_source_id("location", location_owner),
                            inclusion_reasons=[InclusionReason.SCENE_ANCHOR],
                        ),
                    )
                )

                try:
                    weather = await self._world.weather_for(
                        world_id,
                        location_id,
                        getattr(scene, "in_game_start", None),
                        campaign_id,
                        branch_id=branch_id,
                    )
                except Exception:
                    weather = None
                if weather is not None:
                    spotlight.append(
                        TierItem(
                            tier=ContextTier.SPOTLIGHT,
                            section="weather",
                            text=f"Weather: {weather.summary or weather.kind}",
                            priority=4,
                            source=ContextSource(
                                kind="weather",
                                scope="campaign-local",
                                owner_id=campaign_id,
                                tier=ContextTier.SPOTLIGHT,
                                summary=str(weather.kind),
                                source_id=make_source_id(
                                    "weather", f"{campaign_id}:{world_id}:{location_id}"
                                ),
                                inclusion_reasons=[InclusionReason.SCENE_ANCHOR],
                            ),
                        )
                    )

                try:
                    adjacent = await self._world.adjacent_locations(
                        f"library:worlds/{world_id}/locations/{location_id}",
                        campaign_id,
                    )
                except Exception:
                    adjacent = []
                if adjacent:
                    names = ", ".join(loc.name for loc in adjacent if getattr(loc, "name", ""))
                    if names:
                        background.append(
                            TierItem(
                                tier=ContextTier.BACKGROUND,
                                section="adjacent_locations",
                                text=f"Adjacent: {names}",
                                priority=3,
                                source=ContextSource(
                                    kind="location",
                                    scope="library",
                                    owner_id=f"library:worlds/{world_id}",
                                    tier=ContextTier.BACKGROUND,
                                    summary="adjacency",
                                    source_id=make_source_id(
                                        "adjacency", f"library:worlds/{world_id}"
                                    ),
                                    inclusion_reasons=[InclusionReason.SCENE_ANCHOR],
                                ),
                            )
                        )

        summary = getattr(scene, "running_summary", None) or ""
        if summary:
            spotlight.append(
                TierItem(
                    tier=ContextTier.SPOTLIGHT,
                    section="scene_summary",
                    text=f"Scene summary so far:\n{summary}",
                    priority=6,
                    source=ContextSource(
                        kind="scene",
                        scope="campaign-local",
                        owner_id=campaign_id,
                        tier=ContextTier.SPOTLIGHT,
                        summary="running summary",
                        source_id=make_source_id("scene_summary", campaign_id),
                        inclusion_reasons=[InclusionReason.SCENE_ANCHOR],
                    ),
                )
            )

        return spotlight, background

    async def resolve_factions(
        self,
        *,
        scene: Any,
        campaign_id: CampaignId,
        branch_id: str | None,
    ) -> list[TierItem]:
        if self._world is None:
            return []
        faction_refs = await self._faction_refs_for_scene(scene, campaign_id)
        if not faction_refs:
            return []
        getter = getattr(self._world, "faction_state", None)
        if getter is None:
            return []
        items: list[TierItem] = []
        for ref in faction_refs[: self._config.faction_state_limit]:
            try:
                state = await getter(ref, campaign_id, branch_id=branch_id)
            except TypeError:
                try:
                    state = await getter(ref, campaign_id)
                except Exception:
                    continue
            except Exception:
                continue
            if state is None:
                continue
            text = _render_faction_state(ref, state)
            if not text:
                continue
            items.append(
                TierItem(
                    tier=ContextTier.BACKGROUND,
                    section="faction_state",
                    text=text,
                    priority=3,
                    source=ContextSource(
                        kind="faction",
                        scope="library" if ref.startswith("library:") else "campaign-local",
                        owner_id=ref if ref.startswith("library:") else campaign_id,
                        tier=ContextTier.BACKGROUND,
                        summary=ref,
                        source_id=make_source_id("faction", ref),
                        inclusion_reasons=[InclusionReason.COMPOSITION_DEFAULT],
                    ),
                )
            )
        return items

    async def _faction_refs_for_scene(self, scene: Any, campaign_id: CampaignId) -> list[str]:
        lister = getattr(self._world, "list_factions", None)
        if lister is None:
            return []
        try:
            composition = await self._library.get_composition(campaign_id)
        except Exception:
            return []
        if composition is None or not composition.worlds:
            return []
        refs: list[str] = []
        for wref in composition.worlds:
            try:
                factions = await lister(wref.world_id)
            except Exception:
                continue
            for f in factions or []:
                asset_id = getattr(f, "asset_id", None) or getattr(f, "id", None)
                if not asset_id:
                    continue
                refs.append(f"library:worlds/{wref.world_id}/factions/{asset_id}")
        return refs

    async def resolve_calendar(
        self,
        *,
        scene: Any,
        campaign_id: CampaignId,
        branch_id: str | None,
    ) -> list[TierItem]:
        if self._world is None and self._time_engine is None:
            return []

        when = None
        if self._time_engine is not None:
            try:
                when = await self._time_engine.current(campaign_id, branch_id=branch_id)
            except TypeError:
                try:
                    when = await self._time_engine.current(campaign_id)
                except Exception:
                    when = None
            except Exception:
                when = None
        if when is None and scene is not None:
            when = getattr(scene, "in_game_start", None)
        if when is None:
            return []

        season = await _safe_call(self._world.season_for, when, campaign_id)
        holiday = await _safe_call(self._world.holiday_at, when, campaign_id)
        upcoming: list[Any] = []
        if self._time_engine is not None:
            try:
                upcoming = list(
                    await self._time_engine.upcoming_events(campaign_id, branch_id=branch_id)
                )
            except TypeError:
                try:
                    upcoming = list(await self._time_engine.upcoming_events(campaign_id))
                except Exception:
                    upcoming = []
            except Exception:
                upcoming = []

        lines: list[str] = []
        when_str = _format_when(when)
        if when_str:
            lines.append(f"Current in-game time: {when_str}")
        if season is not None:
            name = getattr(season, "name", None) or getattr(season, "id", None) or str(season)
            lines.append(f"Season: {name}")
        if holiday is not None:
            name = getattr(holiday, "name", None) or getattr(holiday, "id", None) or str(holiday)
            lines.append(f"Holiday: {name}")
        if upcoming:
            event_names: list[str] = []
            for ev in upcoming[:3]:
                title = (
                    getattr(ev, "title", None)
                    or getattr(ev, "label", None)
                    or getattr(ev, "id", None)
                    or "event"
                )
                event_names.append(str(title))
            lines.append("Upcoming: " + ", ".join(event_names))
        if not lines:
            return []
        text = "Calendar\n" + "\n".join(lines)
        return [
            TierItem(
                tier=ContextTier.BACKGROUND,
                section="calendar",
                text=text,
                priority=2,
                source=ContextSource(
                    kind="calendar",
                    scope="campaign-local",
                    owner_id=campaign_id,
                    tier=ContextTier.BACKGROUND,
                    summary="world-time",
                    source_id=make_source_id("calendar", campaign_id),
                    inclusion_reasons=[InclusionReason.COMPOSITION_DEFAULT],
                ),
            )
        ]


# ------------------------------------------------------------------ #
# Module-level helpers
# ------------------------------------------------------------------ #


async def _safe_call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return await fn(*args, **kwargs)
    except Exception as exc:
        logger.debug("world context: %s failed: %s", getattr(fn, "__name__", fn), exc)
        return None


def _parse_location_ref(ref: str | None) -> tuple[str | None, str | None]:
    if not ref:
        return None, None
    raw = ref
    if raw.startswith("library:"):
        raw = raw[len("library:"):]
    parts = raw.strip("/").split("/")
    if len(parts) >= 4 and parts[0] == "worlds" and parts[2] in {"locations", "location"}:
        return parts[1], parts[3]
    return None, None


def _render_location(location: Any) -> str:
    return render_template(
        "context_location",
        name=getattr(location, "name", ""),
        description=getattr(location, "description", ""),
        body=getattr(location, "body", ""),
        features=getattr(location, "permanent_features", None) or [],
    ).strip()


def _format_when(when: Any) -> str:
    if when is None:
        return ""
    moment = getattr(when, "moment", None)
    if moment is not None:
        try:
            return moment.isoformat()
        except Exception:
            return str(moment)
    try:
        return when.isoformat()
    except Exception:
        return str(when)


def _render_faction_state(ref: str, state: Any) -> str:
    parts: list[str] = []
    focus = getattr(state, "current_focus", None) or ""
    perception = getattr(state, "public_perception", None) or ""
    goals = list(getattr(state, "goals", []) or [])
    resources = getattr(state, "resources", None) or {}
    if focus:
        parts.append(f"focus: {focus}")
    if perception:
        parts.append(f"perception: {perception}")
    if goals:
        goal_strs: list[str] = []
        for g in goals[:3]:
            if isinstance(g, dict):
                goal_strs.append(g.get("text") or g.get("summary") or str(g))
            else:
                goal_strs.append(getattr(g, "text", None) or getattr(g, "summary", None) or str(g))
        parts.append("goals: " + "; ".join(goal_strs))
    if resources and isinstance(resources, dict):
        rkeys = [str(k) for k in list(resources.keys())[:4]]
        if rkeys:
            parts.append("resources: " + ", ".join(rkeys))
    if not parts:
        return ""
    return f"Faction state — {ref}: " + " | ".join(parts)
