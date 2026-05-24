"""ArchiveRetriever — vector search, keyword search, lore triggers, scene refs."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any

from grimoire.context.config import ContextBuilderConfig
from grimoire.context.types import TierItem, make_source_id
from grimoire.types.common import CampaignId, TurnId
from grimoire.types.composition import Composition
from grimoire.types.context import ContextSource
from grimoire.types.inclusion_reasons import InclusionReason
from grimoire.types.state import ContextTier

logger = logging.getLogger(__name__)


class ArchiveRetriever:
    def __init__(
        self,
        *,
        state_store: Any | None = None,
        gateway: Any | None = None,
        world: Any | None = None,
        mechanics: Any | None = None,
        scenes: Any | None = None,
        config: ContextBuilderConfig,
    ) -> None:
        self._store = state_store
        self._gateway = gateway
        self._world = world
        self._mechanics = mechanics
        self._scenes = scenes
        self._config = config

    async def retrieve_archive(
        self,
        *,
        player_input: str,
        campaign_id: CampaignId,
        scene: Any,
        recent_posts: list[Any],
        turn_id: TurnId | None = None,
        composition: Composition | None = None,
    ) -> list[TierItem]:
        items: list[TierItem] = []
        query = self._build_retrieval_query(player_input, scene, recent_posts)
        if not query:
            return items

        priority_hints = self._priority_hints(composition)

        vector_hits = await self._vector_search(query, campaign_id, turn_id, priority_hints)
        for hit in vector_hits:
            text = getattr(hit, "text", "") or ""
            if not text:
                continue
            items.append(
                TierItem(
                    tier=ContextTier.ARCHIVE,
                    section="retrieved",
                    text=f"[retrieved · {hit.source_kind}] {text}",
                    priority=int(hit.score * 10),
                    source=ContextSource(
                        kind=hit.source_kind or "post",
                        scope=hit.scope or "campaign-local",
                        owner_id=hit.ref,
                        tier=ContextTier.ARCHIVE,
                        summary=f"score={hit.score:.3f}",
                        source_id=make_source_id("retrieved", f"{hit.source_kind}:{hit.ref}"),
                        inclusion_reasons=[InclusionReason.KEYWORD_TRIGGERED],
                    ),
                )
            )

        keyword_hits = await self._keyword_search(query, campaign_id, priority_hints)
        seen_refs: set[str] = {item.source.owner_id or "" for item in items}
        for hit in keyword_hits:
            if (hit.ref or "") in seen_refs:
                continue
            text = getattr(hit, "text", "") or ""
            if not text:
                continue
            items.append(
                TierItem(
                    tier=ContextTier.ARCHIVE,
                    section="keyword",
                    text=f"[fact-match] {text}",
                    priority=int(hit.score * 5),
                    source=ContextSource(
                        kind=hit.source_kind or "fact",
                        scope=hit.scope or "campaign-local",
                        owner_id=hit.ref,
                        tier=ContextTier.ARCHIVE,
                        summary=f"score={hit.score:.3f}",
                        source_id=make_source_id("keyword", f"{hit.source_kind}:{hit.ref}"),
                        inclusion_reasons=[InclusionReason.KEYWORD_TRIGGERED],
                    ),
                )
            )
            seen_refs.add(hit.ref or "")
        return items

    async def power_definition_archive(
        self,
        *,
        campaign_id: CampaignId,
        scene: Any,
        active_pc_ref: str | None,
    ) -> list[TierItem]:
        if self._mechanics is None:
            return []
        refs: list[str] = []
        if active_pc_ref:
            refs.append(active_pc_ref)
        if scene is not None:
            refs.extend(getattr(scene, "present_character_refs", []) or [])
        if not refs:
            return []
        seen: set[str] = set()
        ordered_refs = [r for r in refs if not (r in seen or seen.add(r))]

        capability_ids: list[str] = []
        seen_caps: set[str] = set()
        for ref in ordered_refs:
            try:
                caps = await self._mechanics.capabilities_of(campaign_id, ref)
            except Exception as exc:
                logger.debug("capabilities_of(%s) failed: %s", ref, exc)
                continue
            for cap in caps or []:
                cap_id = cap.get("id") if isinstance(cap, dict) else getattr(cap, "id", None)
                if not cap_id or cap_id in seen_caps:
                    continue
                seen_caps.add(cap_id)
                capability_ids.append(cap_id)

        items: list[TierItem] = []
        for cap_id in capability_ids:
            try:
                power = await self._mechanics.power_definition(campaign_id, cap_id)
            except Exception as exc:
                logger.debug("power_definition(%s) failed: %s", cap_id, exc)
                continue
            if power is None:
                continue
            name = getattr(power, "name", cap_id) or cap_id
            description = getattr(power, "description", "") or ""
            effect = getattr(power, "effect", "") or ""
            rating = getattr(power, "rating", None)
            header = f"[power: {name}" + (f" ({rating})" if rating is not None else "") + "]"
            body = " ".join(p for p in (description, effect) if p).strip()
            text = f"{header} {body}".strip()
            items.append(
                TierItem(
                    tier=ContextTier.ARCHIVE,
                    section="power",
                    text=text,
                    priority=3,
                    source=ContextSource(
                        kind="power",
                        scope="library",
                        owner_id=f"power:{cap_id}",
                        tier=ContextTier.ARCHIVE,
                        summary=name,
                        source_id=make_source_id("power", cap_id),
                        inclusion_reasons=[InclusionReason.MECHANICS_RELEVANT],
                    ),
                )
            )
        return items

    async def lore_triggers(
        self,
        player_input: str,
        campaign_id: CampaignId,
        *,
        turn_id: TurnId | None = None,
    ) -> tuple[list[TierItem], list[TierItem], list[TierItem]]:
        if self._world is None or not player_input:
            return [], [], []
        try:
            triggered = await self._world.lore_for_post(player_input, campaign_id, turn_id=turn_id)
        except TypeError:
            try:
                triggered = await self._world.lore_for_post(player_input, campaign_id)
            except Exception:
                return [], [], []
        except Exception:
            return [], [], []
        spotlight: list[TierItem] = []
        background: list[TierItem] = []
        archive: list[TierItem] = []
        for lore in triggered:
            item = _route_lore_to_tier(lore)
            if item is None:
                continue
            if item.tier == ContextTier.SPOTLIGHT:
                spotlight.append(item)
            elif item.tier == ContextTier.BACKGROUND:
                background.append(item)
            else:
                archive.append(item)
        return spotlight, background, archive

    async def _vector_search(
        self,
        query: str,
        campaign_id: CampaignId,
        turn_id: TurnId | None = None,
        priority_hints: dict[str, int] | None = None,
    ) -> list[Any]:
        if self._gateway is None or self._store is None:
            return []
        try:
            vectors = await self._gateway.embed(
                self._config.retrieval.embedding_task,
                [query],
                campaign_id=campaign_id,
                turn_id=turn_id,
            )
        except Exception:
            return []
        if not vectors:
            return []
        kwargs: dict[str, Any] = {
            "query_vector": vectors[0],
            "campaign_id": campaign_id,
            "include_library": self._config.retrieval.include_library,
            "top_k": self._config.retrieval.vector_top_k,
        }
        if priority_hints:
            kwargs["priority_hints"] = priority_hints
        return await self._invoke_store_search(self._store.vector_search, kwargs)

    async def _keyword_search(
        self,
        query: str,
        campaign_id: CampaignId,
        priority_hints: dict[str, int] | None = None,
    ) -> list[Any]:
        if self._store is None:
            return []
        kwargs: dict[str, Any] = {
            "query": query,
            "campaign_id": campaign_id,
            "kinds": self._config.retrieval.keyword_kinds,
            "top_k": self._config.retrieval.keyword_top_k,
        }
        if priority_hints:
            kwargs["priority_hints"] = priority_hints
        return await self._invoke_store_search(self._store.keyword_search, kwargs)

    async def _invoke_store_search(self, fn: Any, kwargs: dict[str, Any]) -> list[Any]:
        try:
            return await fn(**kwargs)
        except TypeError as exc:
            if "priority_hints" in kwargs and "priority_hints" in str(exc):
                kwargs = {k: v for k, v in kwargs.items() if k != "priority_hints"}
                try:
                    return await fn(**kwargs)
                except Exception:
                    return []
            return []
        except Exception:
            return []

    def _priority_hints(self, composition: Composition | None) -> dict[str, int]:
        if not self._config.retrieval.enable_priority_weighting:
            return {}
        if composition is None or not composition.worlds:
            return {}
        return {wref.world_id: wref.priority for wref in composition.worlds}

    async def scene_refs_from_input(
        self, player_input: str, campaign_id: CampaignId
    ) -> list[TierItem]:
        if not player_input or self._scenes is None:
            return []
        matches = re.findall(r"scene:([A-Za-z0-9_\-:.]+)", player_input)
        if not matches:
            return []
        seen: set[str] = set()
        items: list[TierItem] = []
        getter = getattr(self._scenes, "get_scene", None)
        for raw in matches:
            scene_id = raw.strip(".,;:!?)]}").strip()
            if not scene_id or scene_id in seen:
                continue
            seen.add(scene_id)
            if len(items) >= self._config.scene_ref_limit:
                break
            scene = None
            if getter is not None:
                try:
                    scene = await getter(scene_id)
                except Exception:
                    scene = None
            text = _render_scene_reference(scene_id, scene)
            items.append(
                TierItem(
                    tier=ContextTier.ARCHIVE,
                    section="scene_ref",
                    text=text,
                    priority=20,
                    source=ContextSource(
                        kind="scene",
                        scope="campaign-local",
                        owner_id=campaign_id,
                        tier=ContextTier.ARCHIVE,
                        summary=f"scene:{scene_id}",
                        source_id=make_source_id("scene_ref", scene_id),
                        inclusion_reasons=[InclusionReason.SCENE_ANCHOR],
                    ),
                )
            )
        return items

    def build_retrieval_query(
        self, player_input: str, scene: Any, recent_posts: Iterable[Any]
    ) -> str:
        parts: list[str] = []
        if player_input:
            parts.append(player_input)
        if scene is not None:
            present = list(getattr(scene, "present_character_refs", []) or [])
            if present:
                parts.append(" ".join(present))
            if getattr(scene, "location_ref", None):
                parts.append(str(scene.location_ref))
        last_n_bodies = [getattr(p, "body", "") for p in list(recent_posts)[-3:]]
        if last_n_bodies:
            parts.append(" ".join(last_n_bodies))
        return " ".join(p for p in parts if p).strip()

    # Keep _build_retrieval_query as private alias for internal use
    _build_retrieval_query = build_retrieval_query


# ------------------------------------------------------------------ #
# Module-level helpers
# ------------------------------------------------------------------ #


def _route_lore_to_tier(lore: Any) -> TierItem | None:
    body = getattr(lore, "body", "") or ""
    title = getattr(lore, "title", "") or ""
    text = f"[lore: {title}] {body[:400]}".strip()
    world_id = getattr(lore, "world_id", "") or ""
    lore_id = getattr(lore, "id", "") or ""
    lore_owner = f"library:worlds/{world_id}/lore/{lore_id}"
    position = getattr(lore, "position", None)
    position_value = getattr(position, "value", position) or "archive"

    if position_value == "before_cast":
        tier = ContextTier.SPOTLIGHT
        section = "lore-before"
        priority = 8
        reasons = [InclusionReason.LORE_ARCHIVE, InclusionReason.KEYWORD_TRIGGERED]
    elif position_value == "after_cast":
        tier = ContextTier.BACKGROUND
        section = "lore-after"
        priority = 5
        reasons = [InclusionReason.LORE_ARCHIVE, InclusionReason.KEYWORD_TRIGGERED]
    elif position_value == "at_depth":
        tier = ContextTier.BACKGROUND
        depth = getattr(lore, "at_depth", None)
        section = f"lore-depth-{depth}" if depth is not None else "lore-depth"
        priority = 7
        reasons = [InclusionReason.LORE_ARCHIVE, InclusionReason.KEYWORD_TRIGGERED]
    else:
        tier = ContextTier.ARCHIVE
        section = "lore-archive" if position_value == "archive" else "lore"
        priority = 2 if position_value == "archive" else 4
        reasons = [InclusionReason.LORE_ARCHIVE, InclusionReason.KEYWORD_TRIGGERED]

    return TierItem(
        tier=tier,
        section=section,
        text=text,
        priority=priority,
        source=ContextSource(
            kind="lore",
            scope="library",
            owner_id=lore_owner,
            tier=tier,
            summary=title,
            source_id=make_source_id("lore", lore_owner),
            inclusion_reasons=reasons,
        ),
    )


def _render_scene_reference(scene_id: str, scene: Any | None) -> str:
    if scene is None:
        return f"[explicit scene reference] scene:{scene_id} (not found)"
    bits: list[str] = []
    title = getattr(scene, "title", None) or getattr(scene, "slug", None) or scene_id
    bits.append(f"[explicit scene reference] {title} (scene:{scene_id})")
    if getattr(scene, "location_ref", None):
        bits.append(f"Location: {scene.location_ref}")
    final = getattr(scene, "final_summary", None) or ""
    running = getattr(scene, "running_summary", None) or ""
    summary = final or running
    if summary:
        bits.append(f"Summary: {summary}")
    return "\n".join(bits)
