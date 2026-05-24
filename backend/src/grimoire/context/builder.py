"""Concrete :class:`ContextBuilder` for spec 02.

The builder orchestrates the seven-step pipeline:

    0. resolve composition
    1. resolve scene state
    2. resolve cast (with tier promotion)
    3. resolve world (location, adjacent, weather, factions, lore)
    4. resolve continuity (commitments, recent facts)
    5. archive retrieval (vector + keyword)
    6. budget allocation
    7. canonical assembly

The module dependencies are duck-typed at construction so unit tests can
substitute lightweight stubs for any subset of services. Production wiring
passes the concrete services from :mod:`grimoire.library`,
:mod:`grimoire.characters`, :mod:`grimoire.world`,
:mod:`grimoire.scenes`, :mod:`grimoire.continuity`, and
:mod:`grimoire.llm_gateway`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable
from typing import Any

from grimoire.context.cast import CastResolver
from grimoire.context.config import ContextBuilderConfig
from grimoire.context.continuity_context import ContinuityContextResolver
from grimoire.context.world_context import WorldContextResolver
from grimoire.context.errors import LockInOverflowError
from grimoire.context.tokens import TokenEstimator, cheap_estimator, estimate_tokens
from grimoire.context.types import BuiltContext, ContextBuildRequest, PinSet, TierItem, make_source_id
from grimoire.observability.metrics import NULL_METRICS, MetricsRegistryProtocol
from grimoire.templates import render as render_template
from grimoire.types.common import CampaignId, TurnId
from grimoire.types.composition import Composition
from grimoire.types.context import (
    AssembledPrompt,
    BudgetEstimate,
    ContextSource,
    ToolDeclarationSpec,
)
from grimoire.types.extraction_modes import ExtractionMode
from grimoire.types.inclusion_reasons import InclusionReason
from grimoire.types.llm import Message, MessageRole, ModelParams
from grimoire.types.mechanics import MechanicsResult
from grimoire.types.state import ContextTier

logger = logging.getLogger(__name__)


# Default branch suffix for callers that omit ``branch_id``. The Inspector
# panel exposes this same default via :class:`_InspectorConfig` so writes
# from the UI line up with reads from the builder.
DEFAULT_BRANCH_SUFFIX = "main"


def _route_lore_to_tier(lore: Any) -> TierItem | None:
    """Build a ``TierItem`` for one triggered lore entry.

    Routes by the entry's ``position`` field per spec
    ``2026-05-19-card-imports-design.md`` §4:

    * ``before_cast`` → SPOTLIGHT (priority 8, ``"lore-before"``)
    * ``after_cast`` → BACKGROUND (priority 5, ``"lore-after"``)
    * ``at_depth``  → BACKGROUND (priority 7, ``"lore-depth-N"``)
    * ``archive`` (or unknown) → ARCHIVE (priority 2, ``"lore-archive"``)

    Stubs without a ``position`` attribute fall through to the ARCHIVE
    branch, preserving backwards compatibility with older world stubs in
    test fixtures.
    """
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
            source_id=_make_source_id("lore", lore_owner),
            inclusion_reasons=reasons,
        ),
    )


def _resolve_runtime_macros(messages: list[Message], active_pc_name: str) -> list[Message]:
    """Substitute ``{{user}}`` with the active PC's name (or ``"the player"``).

    Applied at the end of :meth:`ContextBuilderService._assemble` so card
    imports can preserve ``{{user}}`` literally at ingest and resolve it
    here against the runtime PC. Pure string replace; idempotent.
    """
    pc_name = active_pc_name.strip() if active_pc_name else ""
    pc_name = pc_name or "the player"
    return [
        m.model_copy(update={"content": m.content.replace("{{user}}", pc_name)}) for m in messages
    ]


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #


class ContextBuilderService:
    """The concrete Context Builder.

    Construction takes the five domain modules plus the LLM gateway. Each
    is typed loosely (``Any``) so tests can pass minimal stubs; the
    methods we actually call are documented inline at each call site.
    """

    def __init__(
        self,
        *,
        library: Any,
        characters: Any,
        world: Any,
        scenes: Any,
        continuity: Any,
        mechanics: Any | None = None,
        gateway: Any | None = None,
        state_store: Any | None = None,
        time_engine: Any | None = None,
        transient_state: Any | None = None,
        config: ContextBuilderConfig | None = None,
        metrics: MetricsRegistryProtocol = NULL_METRICS,
    ) -> None:
        self._library = library
        self._characters = characters
        self._world = world
        self._scenes = scenes
        self._continuity = continuity
        self._mechanics = mechanics
        self._gateway = gateway
        self._store = state_store
        self._time_engine = time_engine
        self._transient_state = transient_state
        self._config = config or ContextBuilderConfig()
        self._estimator: TokenEstimator = self._make_estimator()
        self._metrics: MetricsRegistryProtocol = metrics
        self._cast = CastResolver(
            characters=characters,
            library=library,
            transient_state=transient_state,
            config=self._config,
            estimator=self._estimator,
        )
        self._world_ctx = WorldContextResolver(
            world=world,
            library=library,
            time_engine=time_engine,
            config=self._config,
        )
        self._continuity_ctx = ContinuityContextResolver(
            continuity=continuity,
            characters=characters,
            time_engine=time_engine,
            config=self._config,
        )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def build(
        self,
        player_input: str,
        campaign_id: CampaignId,
        mechanics_results: list[MechanicsResult] | None = None,
        extra: str | None = None,
        *,
        branch_id: str | None = None,
        pc_ref: str | None = None,
        turn_id: TurnId | None = None,
        extractor_mode: ExtractionMode = ExtractionMode.SEPARATE,
        auxiliary_task: object | None = None,
    ) -> AssembledPrompt:
        async with self._metrics.measure("context_builder", "build"):
            return await self._build_inner(
                player_input,
                campaign_id,
                mechanics_results,
                extra,
                branch_id=branch_id,
                pc_ref=pc_ref,
                turn_id=turn_id,
                extractor_mode=extractor_mode,
                auxiliary_task=auxiliary_task,
            )

    async def _build_inner(
        self,
        player_input: str,
        campaign_id: CampaignId,
        mechanics_results: list[MechanicsResult] | None = None,
        extra: str | None = None,
        *,
        branch_id: str | None = None,
        pc_ref: str | None = None,
        turn_id: TurnId | None = None,
        extractor_mode: ExtractionMode = ExtractionMode.SEPARATE,
        auxiliary_task: object | None = None,
    ) -> AssembledPrompt:
        if auxiliary_task is not None:
            return await self._build_auxiliary(
                campaign_id=campaign_id,
                task=auxiliary_task,
                branch_id=branch_id,
                pc_ref=pc_ref,
            )
        pins = await self._load_pins(campaign_id, branch_id, turn_id)
        ctx = await self._build_context(
            player_input=player_input,
            campaign_id=campaign_id,
            mechanics_results=mechanics_results or [],
            extra=extra,
            branch_id=branch_id,
            pc_ref=pc_ref,
            turn_id=turn_id,
            pins=pins,
        )
        prompt = await self._assemble(ctx, player_input)
        return self._apply_extractor_mode(
            prompt, extractor_mode=extractor_mode, auxiliary_task=auxiliary_task
        )

    async def estimate(
        self,
        player_input: str,
        campaign_id: CampaignId,
        *,
        branch_id: str | None = None,
        pc_ref: str | None = None,
        turn_id: TurnId | None = None,
    ) -> BudgetEstimate:
        """Dry-run the pipeline and report what each tier would consume."""
        pins = await self._load_pins(campaign_id, branch_id, turn_id)
        ctx = await self._build_context(
            player_input=player_input,
            campaign_id=campaign_id,
            mechanics_results=[],
            extra=None,
            branch_id=branch_id,
            pc_ref=pc_ref,
            turn_id=turn_id,
            pins=pins,
        )
        assembled = await self._assemble(ctx, player_input)
        return BudgetEstimate(
            total_budget=self._config.total_budget,
            reserve_for_response=self._config.reserve_for_response,
            per_tier=dict(assembled.budget_used),
            sources_preview=list(assembled.sources),
        )

    # ------------------------------------------------------------------ #
    # Internals — context gathering
    # ------------------------------------------------------------------ #

    async def _load_pins(
        self,
        campaign_id: CampaignId,
        branch_id: str | None,
        turn_id: TurnId | None,
    ) -> PinSet:
        pins = PinSet()
        if self._store is None:
            return pins
        lister = getattr(self._store, "list_active_context_pins", None)
        if lister is None:
            return pins
        bid = branch_id or f"{campaign_id}:{DEFAULT_BRANCH_SUFFIX}"
        try:
            rows = await lister(
                campaign_id=campaign_id,
                branch_id=bid,
                current_turn_id=turn_id,
            )
        except Exception as exc:
            # Fail loud on pin-load failure: silently returning an empty
            # ``PinSet`` would leak user-excluded content into the next
            # prompt. Surface the error to operators and bubble up so the
            # turn can be retried (or the cause investigated) instead of
            # quietly sending suppressed entities to the LLM.
            logger.error(
                "context-builder: list_active_context_pins failed for campaign=%s branch=%s: %s",
                campaign_id,
                bid,
                exc,
            )
            raise
        for row in rows or []:
            kind = row.get("kind")
            target_kind = row.get("target_kind")
            if target_kind == "source":
                sid = row.get("target_source_id") or ""
                if not sid:
                    continue
                if kind == "exclude":
                    pins.excluded_source_ids.add(sid)
                else:
                    pins.pinned_source_ids.add(sid)
            elif target_kind == "entity":
                ek = row.get("target_entity_kind") or ""
                eid = row.get("target_entity_id") or ""
                if not (ek and eid):
                    continue
                if kind == "exclude":
                    pins.excluded_entities.add((ek, eid))
                else:
                    pins.pinned_entities.add((ek, eid))
        return pins

    async def _build_context(
        self,
        *,
        player_input: str,
        campaign_id: CampaignId,
        mechanics_results: list[MechanicsResult],
        extra: str | None,
        branch_id: str | None,
        pc_ref: str | None,
        turn_id: TurnId | None = None,
        pins: PinSet | None = None,
    ) -> BuiltContext:
        pins = pins or PinSet()
        # Step 0 — composition
        composition = await self._safe_call(self._library.get_composition, campaign_id)
        style_text = await self._resolve_style_guide(composition)
        boundaries = composition.content_boundaries if composition else ""
        system_meta = await self._render_system_meta(composition)

        # Step 1 — scene state
        scene = await self._safe_call(
            self._scenes.active_scene_for_campaign, campaign_id, branch_id or "main"
        )
        recent_posts = await self._recent_posts(scene)
        scene_header = self._cast.render_scene_header(scene)

        # Step 2 — cast
        if pc_ref is None:
            active_pc_ref = await self._safe_call(self._characters.active_pc, campaign_id)
        else:
            active_pc_ref = pc_ref
        active_pc_card, active_pc_source = await self._cast.active_pc_card(active_pc_ref, campaign_id)
        active_pc_name = await self._cast.active_pc_name(active_pc_ref, campaign_id)

        # Open commitments are reused for both the lock-in commitments block
        # and the tier-recommendation hint (`commitments_targeting_pcs`).
        open_commitments = await self._continuity_ctx.open_commitments(campaign_id)
        pc_refs = await self._continuity_ctx.pc_refs(campaign_id)
        commitments_targeting_pcs = self._continuity_ctx.commitments_targeting_pcs(
            open_commitments, pc_refs
        )

        spotlight_items, background_items, voice_corrective = await self._cast.resolve(
            scene=scene,
            campaign_id=campaign_id,
            active_pc_ref=active_pc_ref,
            recent_posts=recent_posts,
            commitments_targeting_pcs=commitments_targeting_pcs,
        )

        # Step 3 — world
        world_spotlight, world_background = await self._world_ctx.resolve_world(
            scene=scene, campaign_id=campaign_id, branch_id=branch_id
        )
        spotlight_items.extend(world_spotlight)
        background_items.extend(world_background)
        background_items.extend(
            await self._world_ctx.resolve_factions(
                scene=scene, campaign_id=campaign_id, branch_id=branch_id
            )
        )
        background_items.extend(
            await self._world_ctx.resolve_calendar(
                scene=scene, campaign_id=campaign_id, branch_id=branch_id
            )
        )

        # Step 4 — continuity
        as_of = await self._continuity_ctx.current_in_game_time(campaign_id, branch_id, scene)
        overdue_commitments = await self._continuity_ctx.overdue_commitments(campaign_id, as_of)
        stale_commitments_list = await self._continuity_ctx.stale_commitments(campaign_id)
        commitments_block, commitments_source = self._continuity_ctx.render_commitments_block(
            campaign_id,
            open_commitments,
            overdue=overdue_commitments,
            stale=stale_commitments_list,
        )
        background_items.extend(
            await self._continuity_ctx.continuity_background(
                campaign_id, active_pc_ref, recent_posts
            )
        )
        background_items.extend(
            await self._continuity_ctx.relationship_deltas(
                active_pc_ref=active_pc_ref,
                scene=scene,
                campaign_id=campaign_id,
                branch_id=branch_id,
            )
        )

        # Step 5 — archive retrieval
        archive_items = await self._retrieve_archive(
            player_input=player_input,
            campaign_id=campaign_id,
            scene=scene,
            recent_posts=recent_posts,
            turn_id=turn_id,
            composition=composition,
        )

        # Step 5a — lore keyword triggers (campaign-scoped); routed to the
        # tier indicated by each entry's ``position`` field.
        lore_spotlight, lore_background, lore_archive = await self._lore_triggers(
            player_input, campaign_id, turn_id=turn_id
        )
        spotlight_items.extend(lore_spotlight)
        background_items.extend(lore_background)
        archive_items.extend(lore_archive)

        # Step 5b — explicit scene references from the player input. These
        # bypass the vector/keyword budget — the player asked for them.
        archive_items.extend(await self._scene_refs_from_input(player_input, campaign_id))

        # Step 5c — power-definition lookups for capabilities named by
        # spotlight characters (archive tier so they don't crowd the
        # spotlight budget).
        archive_items.extend(
            await self._power_definition_archive(
                campaign_id=campaign_id,
                scene=scene,
                active_pc_ref=active_pc_ref,
            )
        )

        # Mechanics block (lock-in)
        mechanics_block = self._render_mechanics(mechanics_results)

        # Apply exclude pins and mark pinned items before assembly.
        def _filter_items(items: list[TierItem]) -> list[TierItem]:
            kept: list[TierItem] = []
            for it in items:
                if pins.is_excluded(it.source):
                    continue
                if pins.is_pinned(it.source):
                    it.pinned = True
                    if InclusionReason.PINNED_BY_USER not in it.source.inclusion_reasons:
                        it.source.inclusion_reasons.append(InclusionReason.PINNED_BY_USER)
                kept.append(it)
            return kept

        spotlight_items = _filter_items(spotlight_items)
        background_items = _filter_items(background_items)
        archive_items = _filter_items(archive_items)

        # Build full sources list (active PC card + commitments + tier items)
        sources: list[ContextSource] = []
        if active_pc_source is not None and not pins.is_excluded(active_pc_source):
            if pins.is_pinned(active_pc_source) and (
                InclusionReason.PINNED_BY_USER not in active_pc_source.inclusion_reasons
            ):
                active_pc_source.inclusion_reasons.append(InclusionReason.PINNED_BY_USER)
            sources.append(active_pc_source)
        if commitments_source is not None and not pins.is_excluded(commitments_source):
            if pins.is_pinned(commitments_source) and (
                InclusionReason.PINNED_BY_USER not in commitments_source.inclusion_reasons
            ):
                commitments_source.inclusion_reasons.append(InclusionReason.PINNED_BY_USER)
            sources.append(commitments_source)
        for item in spotlight_items + background_items + archive_items:
            sources.append(item.source)

        # Recent posts as text (last N)
        recent_posts_text = self._render_recent_posts(recent_posts)

        return BuiltContext(
            composition=composition,
            style_text=style_text,
            content_boundaries=boundaries or "",
            system_meta=system_meta,
            scene_header=scene_header,
            active_pc_card=active_pc_card,
            active_pc_name=active_pc_name,
            mechanics_block=mechanics_block,
            commitments_block=commitments_block,
            spotlight_items=spotlight_items,
            background_items=background_items,
            archive_items=archive_items,
            recent_posts_text=recent_posts_text,
            voice_corrective=voice_corrective,
            sources=sources,
            extra=extra,
        )

    # -- composition / system block ------------------------------------ #

    async def _resolve_style_guide(self, composition: Composition | None) -> str:
        if composition is None:
            return ""
        if composition.inline_style_guide:
            return composition.inline_style_guide.strip()
        if composition.style_guide_id and self._library is not None:
            try:
                guide = await self._library.get_style_guide(composition.style_guide_id)
            except Exception:
                return ""
            body = (getattr(guide, "body", "") or "").strip()
            return body
        return ""

    async def _render_system_meta(self, composition: Composition | None) -> str:
        if composition is None or not composition.worlds:
            return ""
        names: list[str] = []
        for ref in sorted(composition.worlds, key=lambda r: r.priority):
            try:
                meta = await self._library.get_world(ref.world_id)
            except Exception:
                continue
            names.append(f"{meta.name} ({ref.world_id})")
        if not names:
            return ""
        return "Worlds in play: " + "; ".join(names)

    # -- archive retrieval --------------------------------------------- #

    async def _retrieve_archive(
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

        # Vector
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
                        source_id=_make_source_id("retrieved", f"{hit.source_kind}:{hit.ref}"),
                        inclusion_reasons=[InclusionReason.KEYWORD_TRIGGERED],
                    ),
                )
            )

        # Keyword
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
                        source_id=_make_source_id("keyword", f"{hit.source_kind}:{hit.ref}"),
                        inclusion_reasons=[InclusionReason.KEYWORD_TRIGGERED],
                    ),
                )
            )
            seen_refs.add(hit.ref or "")
        return items

    async def _power_definition_archive(
        self,
        *,
        campaign_id: CampaignId,
        scene: Any,
        active_pc_ref: str | None,
    ) -> list[TierItem]:
        """Surface power definitions for capabilities held by spotlight chars.

        For each unique capability id collected across the spotlight cast,
        ask the mechanics module for its ``PowerDefinition``; format any
        hits as archive-tier items.
        """
        if self._mechanics is None:
            return []
        refs: list[str] = []
        if active_pc_ref:
            refs.append(active_pc_ref)
        if scene is not None:
            refs.extend(getattr(scene, "present_character_refs", []) or [])
        if not refs:
            return []
        # Dedupe while preserving order.
        seen: set[str] = set()
        ordered_refs = [r for r in refs if not (r in seen or seen.add(r))]

        # Collect capability ids across all spotlight characters.
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
                        source_id=_make_source_id("power", cap_id),
                        inclusion_reasons=[InclusionReason.MECHANICS_RELEVANT],
                    ),
                )
            )
        return items

    async def _lore_triggers(
        self,
        player_input: str,
        campaign_id: CampaignId,
        *,
        turn_id: TurnId | None = None,
    ) -> tuple[list[TierItem], list[TierItem], list[TierItem]]:
        """Return (spotlight, background, archive) lore items by position."""
        if self._world is None or not player_input:
            return [], [], []
        try:
            triggered = await self._world.lore_for_post(player_input, campaign_id, turn_id=turn_id)
        except TypeError:
            # Older WorldService signatures that don't accept turn_id —
            # fall back gracefully so this caller never breaks the build.
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
        """Call a store search with optional ``priority_hints`` retry.

        If the store doesn't yet accept the priority kwarg we drop it and
        retry once — the builder must keep working against older stores
        (spec context-builder-remaining §13 is store-gated).
        """
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
        """Build a ``{world_id: priority}`` hint dict for the store.

        Returns an empty dict (no hint) when weighting is disabled or the
        composition is missing — the store should fall back to its own
        ranking.
        """
        if not self._config.retrieval.enable_priority_weighting:
            return {}
        if composition is None or not composition.worlds:
            return {}
        return {wref.world_id: wref.priority for wref in composition.worlds}

    async def _scene_refs_from_input(
        self, player_input: str, campaign_id: CampaignId
    ) -> list[TierItem]:
        """§7 — explicit past-scene references.

        Scan the player input for ``scene:<id>`` tokens and emit one archive
        item per matched scene. These bypass retrieval budget because the
        player asked for them by ref.
        """
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
                    priority=20,  # explicit ref wins over vector/keyword hits
                    source=ContextSource(
                        kind="scene",
                        scope="campaign-local",
                        owner_id=campaign_id,
                        tier=ContextTier.ARCHIVE,
                        summary=f"scene:{scene_id}",
                        source_id=_make_source_id("scene_ref", scene_id),
                        inclusion_reasons=[InclusionReason.SCENE_ANCHOR],
                    ),
                )
            )
        return items

    def _build_retrieval_query(
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
        # Names mentioned in the last few posts contribute terms too.
        last_n_bodies = [getattr(p, "body", "") for p in list(recent_posts)[-3:]]
        if last_n_bodies:
            parts.append(" ".join(last_n_bodies))
        return " ".join(p for p in parts if p).strip()

    # -- mechanics ------------------------------------------------------ #

    def _render_mechanics(self, results: list[MechanicsResult]) -> str:
        if not results:
            return ""
        lines = ["Mechanical results for this turn (treat as authoritative; do not contradict):"]
        for r in results:
            roll = r.roll
            res = r.result
            actor = roll.actor_ref or "?"
            target = f" vs {roll.target_ref}" if roll.target_ref else ""
            outcome = res.outcome or ("success" if res.successes > 0 else "failure")
            lines.append(
                f"- {actor} attempted {roll.kind}{target} (pool {roll.pool}). "
                f"Result: {res.successes} successes ({outcome})."
            )
            if r.summary:
                lines.append(f"  {r.summary}")
        lines.append("The narrative should reflect these outcomes.")
        return "\n".join(lines)

    # -- posts ---------------------------------------------------------- #

    async def _recent_posts(self, scene: Any) -> list[Any]:
        if scene is None:
            return []
        scene_id = getattr(scene, "id", None)
        if scene_id is None:
            return []
        n = self._config.recent_posts_n
        try:
            return await self._scenes.recent_posts(scene_id, n=n)
        except Exception:
            return []

    def _render_recent_posts(self, posts: list[Any]) -> str:
        if not posts:
            return ""
        rendered: list[str] = []
        for post in posts:
            label = getattr(post, "author_label", None)
            if callable(label):
                label = label()
            if not label:
                label = "narrator"
            body = (getattr(post, "body", "") or "").strip()
            rendered.append(f"{label}: {body}")
        return "\n\n".join(rendered)

    def _mentions_in_posts(self, posts: list[Any]) -> set[str]:
        """Best-effort mention extraction: any token that looks like a ref."""
        mentioned: set[str] = set()
        for post in posts:
            body = getattr(post, "body", "") or ""
            for tok in body.split():
                if tok.startswith(("library:", "campaign:")):
                    mentioned.add(tok.strip(".,;:!?"))
        return mentioned

    # ------------------------------------------------------------------ #
    # Internals — assembly + budgeting
    # ------------------------------------------------------------------ #

    async def _assemble(self, ctx: BuiltContext, player_input: str) -> AssembledPrompt:
        messages: list[Message] = []
        budget_used: dict[ContextTier, int] = {t: 0 for t in ContextTier}

        # System block — never compressed, never dropped
        system_text = await self._system_block(ctx)
        if system_text:
            messages.append(
                Message(
                    role=MessageRole.SYSTEM,
                    content=system_text,
                    metadata={"tier": "system"},
                )
            )

        # Lock-in: scene header + active PC + commitments + mechanics + last 2 posts verbatim
        lock_in_text = await self._lock_in_block(ctx)
        lock_in_tokens = await self._tokens(lock_in_text)
        lock_budget = self._config.tiers[ContextTier.LOCK_IN].max_tokens
        if lock_in_tokens > lock_budget:
            raise LockInOverflowError(used=lock_in_tokens, budget=lock_budget)
        if lock_in_text:
            messages.append(
                Message(
                    role=MessageRole.SYSTEM,
                    content=lock_in_text,
                    metadata={"tier": ContextTier.LOCK_IN.value},
                )
            )
            budget_used[ContextTier.LOCK_IN] = lock_in_tokens

        # Spotlight + Background tiers
        budget_used[ContextTier.SPOTLIGHT] = await self._pack_tier(
            ctx.spotlight_items,
            ContextTier.SPOTLIGHT,
            messages,
            label="Spotlight",
        )
        budget_used[ContextTier.BACKGROUND] = await self._pack_tier(
            ctx.background_items,
            ContextTier.BACKGROUND,
            messages,
            label="Background",
        )
        budget_used[ContextTier.ARCHIVE] = await self._pack_tier(
            ctx.archive_items,
            ContextTier.ARCHIVE,
            messages,
            label="Archive",
        )

        # Recent posts beyond the lock-in's verbatim window
        verbatim = self._config.lock_in_verbatim_posts
        if verbatim > 0 and ctx.recent_posts_text:
            # The text rendered for lock-in already contains the last ``verbatim``
            # posts; here we add the older ones up to ``recent_posts_n``.
            older = self._render_older_recent(ctx, verbatim)
            if older:
                older_tokens = await self._tokens(older)
                if older_tokens <= self._config.recent_posts_budget:
                    messages.append(
                        Message(
                            role=MessageRole.SYSTEM,
                            content=older,
                            metadata={"tier": "recent-posts"},
                        )
                    )

        # Player input
        if player_input:
            messages.append(
                Message(
                    role=MessageRole.USER,
                    content=player_input,
                    metadata={"tier": "player-input"},
                )
            )

        if ctx.extra:
            messages.append(
                Message(role=MessageRole.USER, content=ctx.extra, metadata={"tier": "extra"})
            )

        messages = _resolve_runtime_macros(messages, ctx.active_pc_name)

        params = ModelParams(
            temperature=self._config.default_temperature,
            max_tokens=self._config.default_max_tokens,
        )
        sources = list(ctx.sources)
        summary = self._summary(ctx, budget_used)
        composition_snapshot = self._composition_snapshot(ctx.composition)
        return AssembledPrompt(
            messages=messages,
            params=params,
            budget_used=budget_used,
            sources=sources,
            summary=summary,
            composition_snapshot=composition_snapshot,
            messages_hash=_hash_messages(messages),
        )

    async def _build_auxiliary(
        self,
        *,
        campaign_id: CampaignId,
        task: Any,
        branch_id: str | None,
        pc_ref: str | None,
    ) -> AssembledPrompt:
        """Assemble an auxiliary-task prompt directly from a budget plan.

        Bypasses the canonical tier-pack pipeline: no tracker block, no
        tool declarations, no mechanics, no drift correction, no archive
        retrieval. Only the slabs the per-task budget asks for.
        """
        from grimoire.auxiliary.budgets import budget_for, resolve_voice_targets
        from grimoire.auxiliary.prompts import load_template
        from grimoire.auxiliary.types import TaskKind

        kind: TaskKind = task.kind
        budget = budget_for(kind)

        scene = await self._safe_call(
            self._scenes.active_scene_for_campaign, campaign_id, branch_id or "main"
        )

        active_pc_ref = pc_ref
        if active_pc_ref is None:
            active_pc_ref = await self._safe_call(self._characters.active_pc, campaign_id)
        if active_pc_ref and not (task.extra_params or {}).get("active_pc_ref"):
            task.extra_params = {**(task.extra_params or {}), "active_pc_ref": active_pc_ref}

        scene_header = self._render_scene_header(scene) if budget.include_scene_header else ""

        active_pc_card = ""
        if budget.include_active_pc_card:
            active_pc_card, _ = await self._active_pc_card(active_pc_ref, campaign_id)

        voice_refs = resolve_voice_targets(task, scene)
        voice_lines: list[str] = []
        for ref in voice_refs:
            if not ref:
                continue
            anchor = await self._voice_anchor(ref, campaign_id)
            if anchor:
                voice_lines.append(f"# Voice anchor — {ref}\n{anchor}")

        recent_posts_text = ""
        if budget.recent_posts_count > 0 and scene is not None:
            try:
                recent = await self._scenes.recent_posts(scene.id, n=budget.recent_posts_count)
            except Exception:
                recent = []
            recent_posts_text = self._render_recent_posts(list(recent or []))

        pc_name = self._aux_display_name(active_pc_ref) or (active_pc_ref or "")
        target_name = self._aux_display_name(task.target_character_ref) or (
            task.target_character_ref or ""
        )

        original_text = ""
        if task.target_post_id and scene is not None:
            for post in getattr(scene, "posts", None) or []:
                if getattr(post, "id", None) == task.target_post_id:
                    original_text = (getattr(post, "body", "") or "").strip()
                    break

        system_text = (
            load_template(kind)
            .render(
                pc_name=pc_name,
                character_name=target_name,
                scene_summary=scene_header,
                steering_hint=task.steering_hint or "",
                original_text=original_text,
                edit_instruction=task.edit_instruction or "",
                snippet=task.snippet or "",
                target_language=task.target_language or "",
            )
            .strip()
        )

        messages: list[Message] = []
        if system_text:
            messages.append(Message(role=MessageRole.SYSTEM, content=system_text))
        if active_pc_card:
            messages.append(Message(role=MessageRole.SYSTEM, content=active_pc_card))
        if voice_lines:
            messages.append(Message(role=MessageRole.SYSTEM, content="\n\n".join(voice_lines)))
        if recent_posts_text:
            messages.append(Message(role=MessageRole.SYSTEM, content=recent_posts_text))

        messages = _resolve_runtime_macros(messages, pc_name)

        params = ModelParams(
            temperature=self._config.default_temperature,
            max_tokens=self._config.default_max_tokens,
        )
        return AssembledPrompt(
            messages=messages,
            params=params,
            budget_used={},
            sources=[],
            summary=f"auxiliary:{kind.value}",
            composition_snapshot={},
            messages_hash=_hash_messages(messages),
        )

    def _aux_display_name(self, ref: str | None) -> str:
        if not ref:
            return ""
        getter = getattr(self._characters, "display_name", None)
        if getter is None:
            return ref
        try:
            return getter(ref) or ref
        except Exception:
            return ref

    def _apply_extractor_mode(
        self,
        prompt: AssembledPrompt,
        *,
        extractor_mode: ExtractionMode,
        auxiliary_task: object | None,
    ) -> AssembledPrompt:
        """Append tracker instructions or attach tool declarations per mode.

        Auxiliary tasks (`auxiliary_task` non-None, or `NONE` mode) get
        neither the tracker nor the tool declarations — they don't
        produce state and the extractor is skipped entirely upstream.
        """
        if auxiliary_task is not None or extractor_mode == ExtractionMode.NONE:
            return prompt
        if extractor_mode == ExtractionMode.TOGETHER:
            instruction = self._tracker_instruction_text()
            new_messages = list(prompt.messages)
            new_messages.append(Message(role=MessageRole.SYSTEM, content=instruction))
            return prompt.model_copy(update={"messages": new_messages})
        if extractor_mode == ExtractionMode.TOOL_USE:
            from grimoire.extractor.tool_use import ALL_TOOLS

            tools = [
                ToolDeclarationSpec(
                    name=t.name, description=t.description, parameters=dict(t.schema)
                )
                for t in ALL_TOOLS
            ]
            return prompt.model_copy(update={"tools": tools})
        return prompt

    def _tracker_instruction_text(self) -> str:
        from grimoire.extractor.together import DELIMITER_CLOSE, DELIMITER_OPEN

        return (
            "After your prose, emit a JSON tracker block delimited by "
            f"{DELIMITER_OPEN} and {DELIMITER_CLOSE}. The JSON object must "
            "have the keys: facts (list of {text, confidence?, about?, tags?}), "
            "character_updates (list of {character_id, field, after, before?, "
            "confidence?}). Optional keys: location_updates, faction_updates, "
            "commitments_added, commitments_resolved, new_entities, advance_time, "
            "change_location. Position the tracker after the prose; do not "
            "interleave it with narrative text."
        )

    async def _system_block(self, ctx: BuiltContext) -> str:
        return render_template(
            "context_system_block",
            style_text=ctx.style_text,
            content_boundaries=ctx.content_boundaries,
            system_meta=ctx.system_meta,
            voice_corrective=ctx.voice_corrective,
        ).strip()

    async def _lock_in_block(self, ctx: BuiltContext) -> str:
        return render_template(
            "context_lock_in_block",
            scene_header=ctx.scene_header,
            active_pc_card=ctx.active_pc_card,
            commitments_block=ctx.commitments_block,
            mechanics_block=ctx.mechanics_block,
            verbatim_posts=self._lock_in_verbatim_posts(ctx),
        ).strip()

    def _lock_in_verbatim_posts(self, ctx: BuiltContext) -> str:
        # ctx.recent_posts_text is all rendered posts; we just slice the tail.
        if not ctx.recent_posts_text:
            return ""
        all_posts = ctx.recent_posts_text.split("\n\n")
        tail = all_posts[-self._config.lock_in_verbatim_posts :]
        return "\n\n".join(tail)

    def _render_older_recent(self, ctx: BuiltContext, verbatim: int) -> str:
        if not ctx.recent_posts_text:
            return ""
        all_posts = ctx.recent_posts_text.split("\n\n")
        if len(all_posts) <= verbatim:
            return ""
        older = all_posts[: len(all_posts) - verbatim]
        if not older:
            return ""
        return render_template("context_recent_older_block", posts=older)

    async def _pack_tier(
        self,
        items: list[TierItem],
        tier: ContextTier,
        messages: list[Message],
        *,
        label: str,
    ) -> int:
        budget = self._config.tiers[tier].max_tokens
        if not items or budget <= 0:
            return 0
        # Pinned items pack first and are exempt from budget truncation —
        # they survive when ``used`` is over budget. Non-pinned items
        # then pack high-priority first; drop the rest when over budget.
        pinned_items = [it for it in items if it.pinned]
        normal_items = sorted(
            (it for it in items if not it.pinned),
            key=lambda it: -it.priority,
        )
        used = 0
        packed: list[str] = []
        for item in pinned_items:
            cost = await self._tokens(item.text)
            packed.append(item.text)
            item.source.tokens = cost
            used += cost
        for item in normal_items:
            cost = await self._tokens(item.text)
            if used + cost > budget:
                continue
            packed.append(item.text)
            item.source.tokens = cost
            used += cost
        if not packed:
            return 0
        content = render_template("context_tier_block", label=label, items=packed)
        messages.append(
            Message(
                role=MessageRole.SYSTEM,
                content=content,
                metadata={"tier": tier.value},
            )
        )
        return used

    # -- helpers -------------------------------------------------------- #

    async def _tokens(self, text: str) -> int:
        return await estimate_tokens(text, self._estimator)

    def _make_estimator(self) -> TokenEstimator:
        gateway = self._gateway
        if gateway is not None and hasattr(gateway, "estimate_tokens"):

            async def _gateway_estimator(text: str) -> int:
                if not text:
                    return 0
                try:
                    return int(await gateway.estimate_tokens(text))
                except Exception:
                    return max(1, len(text) // self._config.chars_per_token)

            return _gateway_estimator
        return cheap_estimator(self._config.chars_per_token)

    async def _safe_call(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            logger.debug("context builder: %s failed: %s", getattr(fn, "__name__", fn), exc)
            return None

    def _summary(self, ctx: BuiltContext, budget_used: dict[ContextTier, int]) -> str:
        parts: list[str] = []
        for tier in ContextTier:
            parts.append(f"{tier.value}={budget_used.get(tier, 0)}")
        return f"Context Builder turn budget: {' '.join(parts)} (sources={len(ctx.sources)})"

    def _composition_snapshot(self, composition: Composition | None) -> dict:
        if composition is None:
            return {}
        return json.loads(composition.model_dump_json())


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _render_scene_reference(scene_id: str, scene: Any | None) -> str:
    """One-paragraph reference card for a scene the player called out."""
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


_make_source_id = make_source_id


def _hash_messages(messages: list[Message]) -> str:
    h = hashlib.sha256()
    for msg in messages:
        h.update(msg.role.encode("utf-8"))
        h.update(b"\x00")
        h.update(msg.content.encode("utf-8"))
        h.update(b"\x01")
    return h.hexdigest()


__all__ = ["ContextBuilderService"]
