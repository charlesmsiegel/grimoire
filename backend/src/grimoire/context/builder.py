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

import contextlib
import logging
from typing import Any

from grimoire.context.archive import ArchiveRetriever
from grimoire.context.assembler import (
    PromptAssembler,
    _hash_messages,
    _resolve_runtime_macros,
    render_mechanics,
    render_recent_posts,
)
from grimoire.context.cast import CastResolver
from grimoire.context.config import ContextBuilderConfig
from grimoire.context.continuity_context import ContinuityContextResolver
from grimoire.context.tokens import TokenEstimator, cheap_estimator
from grimoire.context.types import BuiltContext, PinSet, TierItem, make_source_id
from grimoire.context.world_context import WorldContextResolver
from grimoire.observability.metrics import NULL_METRICS, MetricsRegistryProtocol
from grimoire.types.common import CampaignId, TurnId
from grimoire.types.composition import Composition
from grimoire.types.context import (
    AssembledPrompt,
    BudgetEstimate,
    ContextSource,
)
from grimoire.types.extraction_modes import ExtractionMode
from grimoire.types.inclusion_reasons import InclusionReason
from grimoire.types.llm import Message, MessageRole, ModelParams
from grimoire.types.mechanics import MechanicsResult
from grimoire.types.state import ContextTier

logger = logging.getLogger(__name__)


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
        self._archive = ArchiveRetriever(
            state_store=state_store,
            gateway=gateway,
            world=world,
            mechanics=mechanics,
            scenes=scenes,
            config=self._config,
        )
        self._assembler = PromptAssembler(
            config=self._config,
            estimator=self._estimator,
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
        pc_ref: str | None = None,
        turn_id: TurnId | None = None,
        extractor_mode: ExtractionMode = ExtractionMode.SEPARATE,
        auxiliary_task: object | None = None,
    ) -> AssembledPrompt:
        if auxiliary_task is not None:
            return await self._build_auxiliary(
                campaign_id=campaign_id,
                task=auxiliary_task,
                pc_ref=pc_ref,
            )
        pins = await self._load_pins(campaign_id, turn_id)
        ctx = await self._build_context(
            player_input=player_input,
            campaign_id=campaign_id,
            mechanics_results=mechanics_results or [],
            extra=extra,
            pc_ref=pc_ref,
            turn_id=turn_id,
            pins=pins,
        )
        prompt = await self._assembler.assemble(ctx, player_input)
        return self._assembler.apply_extractor_mode(
            prompt, extractor_mode=extractor_mode, auxiliary_task=auxiliary_task
        )

    async def estimate(
        self,
        player_input: str,
        campaign_id: CampaignId,
        *,
        pc_ref: str | None = None,
        turn_id: TurnId | None = None,
    ) -> BudgetEstimate:
        """Dry-run the pipeline and report what each tier would consume."""
        pins = await self._load_pins(campaign_id, turn_id)
        ctx = await self._build_context(
            player_input=player_input,
            campaign_id=campaign_id,
            mechanics_results=[],
            extra=None,
            pc_ref=pc_ref,
            turn_id=turn_id,
            pins=pins,
        )
        assembled = await self._assembler.assemble(ctx, player_input)
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
        turn_id: TurnId | None,
    ) -> PinSet:
        pins = PinSet()
        if self._store is None:
            return pins
        lister = getattr(self._store, "list_active_context_pins", None)
        if lister is None:
            return pins
        try:
            rows = await lister(
                campaign_id=campaign_id,
                current_turn_id=turn_id,
            )
        except Exception as exc:
            # Fail loud on pin-load failure: silently returning an empty
            # ``PinSet`` would leak user-excluded content into the next
            # prompt. Surface the error to operators and bubble up so the
            # turn can be retried (or the cause investigated) instead of
            # quietly sending suppressed entities to the LLM.
            logger.error(
                "context-builder: list_active_context_pins failed for campaign=%s: %s",
                campaign_id,
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
        scene = await self._safe_call(self._scenes.active_scene_for_campaign, campaign_id)
        recent_posts = await self._recent_posts(scene)
        scene_header = self._cast.render_scene_header(scene)

        # Step 2 — cast
        present_pcs = list(getattr(scene, "present_pc_refs", []) or []) if scene else []
        pc_absent = scene is not None and not present_pcs

        if pc_absent:
            scene_mode = (
                "This is an NPC-only scene. The player is directing the scene but has "
                "no character present. Write all characters freely — there are no PC "
                "agency restrictions. The player's input is scene direction, not "
                "character dialogue."
            )
        else:
            scene_mode = (
                "You are narrating a scene where the player acts through their character. "
                "Never write the player character's dialogue, actions, or internal thoughts. "
                "Stop at decision points and wait for the player."
            )

        if pc_absent:
            active_pc_ref = None
            active_pc_card = ""
            active_pc_source = None
            active_pc_name = ""
        else:
            if pc_ref is None:
                active_pc_ref = await self._safe_call(self._characters.active_pc, campaign_id)
            else:
                active_pc_ref = pc_ref
            active_pc_card, active_pc_source = await self._cast.active_pc_card(
                active_pc_ref, campaign_id
            )
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

        if pc_absent and scene is not None:
            present_set = set(getattr(scene, "present_pc_refs", []) or [])
            absent_pc_refs = pc_refs - present_set
            for ref in sorted(absent_pc_refs):
                compressed = await self._cast._try_compressed_card(ref, campaign_id)
                if compressed:
                    background_items.append(
                        TierItem(
                            tier=ContextTier.BACKGROUND,
                            section="absent_pc",
                            text=compressed,
                            priority=4,
                            source=ContextSource(
                                kind="character",
                                scope="campaign-local",
                                owner_id=ref,
                                tier=ContextTier.BACKGROUND,
                                summary=f"absent-pc:{ref}",
                                source_id=make_source_id("absent_pc", ref),
                            ),
                        )
                    )

        # Step 3 — world
        world_spotlight, world_background = await self._world_ctx.resolve_world(
            scene=scene, campaign_id=campaign_id
        )
        spotlight_items.extend(world_spotlight)
        background_items.extend(world_background)
        background_items.extend(
            await self._world_ctx.resolve_factions(scene=scene, campaign_id=campaign_id)
        )
        background_items.extend(
            await self._world_ctx.resolve_calendar(scene=scene, campaign_id=campaign_id)
        )

        # Step 4 — continuity
        as_of = await self._continuity_ctx.current_in_game_time(campaign_id, scene)
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
            )
        )

        # Step 5 — archive retrieval
        archive_items = await self._archive.retrieve_archive(
            player_input=player_input,
            campaign_id=campaign_id,
            scene=scene,
            recent_posts=recent_posts,
            turn_id=turn_id,
            composition=composition,
        )

        lore_spotlight, lore_background, lore_archive = await self._archive.lore_triggers(
            player_input, campaign_id, turn_id=turn_id
        )
        spotlight_items.extend(lore_spotlight)
        background_items.extend(lore_background)
        archive_items.extend(lore_archive)

        archive_items.extend(await self._archive.scene_refs_from_input(player_input, campaign_id))

        archive_items.extend(
            await self._archive.power_definition_archive(
                campaign_id=campaign_id,
                scene=scene,
                active_pc_ref=active_pc_ref,
            )
        )

        # Mechanics block (lock-in)
        mechanics_block = render_mechanics(mechanics_results)

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
            active_pc_source.text = active_pc_card
            if pins.is_pinned(active_pc_source) and (
                InclusionReason.PINNED_BY_USER not in active_pc_source.inclusion_reasons
            ):
                active_pc_source.inclusion_reasons.append(InclusionReason.PINNED_BY_USER)
            sources.append(active_pc_source)
        if commitments_source is not None and not pins.is_excluded(commitments_source):
            commitments_source.text = commitments_block
            if pins.is_pinned(commitments_source) and (
                InclusionReason.PINNED_BY_USER not in commitments_source.inclusion_reasons
            ):
                commitments_source.inclusion_reasons.append(InclusionReason.PINNED_BY_USER)
            sources.append(commitments_source)
        for item in spotlight_items + background_items + archive_items:
            sources.append(item.source)

        # Recent posts as text (last N)
        recent_posts_text = render_recent_posts(recent_posts)

        # Narrator response mode and NPC list for response format template
        from grimoire.scenes.narrator_mode import effective_response_mode

        campaign_row = None
        if self._store is not None:
            with contextlib.suppress(Exception):
                campaign_row = await self._store.get_campaign(campaign_id)
        narrator_mode = effective_response_mode(
            scene_override=getattr(scene, "narrator_response_mode", None),
            campaign_row=campaign_row,
        )

        present_npcs: list[dict] = []
        if scene is not None:
            pc_refs = set(getattr(scene, "present_pc_refs", []))
            for ref in getattr(scene, "present_character_refs", []):
                if ref in pc_refs:
                    continue
                name = ref.rsplit("/", 1)[-1].replace("-", " ").title()
                try:
                    entity = await self._library.get_entity(ref)
                    name = getattr(entity, "name", name)
                except Exception:
                    pass
                present_npcs.append({"name": name, "ref": ref})

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
            narrator_response_mode=narrator_mode,
            present_npcs=present_npcs,
            pc_absent=pc_absent,
            scene_mode=scene_mode,
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

    # ------------------------------------------------------------------ #
    # Auxiliary build path
    # ------------------------------------------------------------------ #

    async def _build_auxiliary(
        self,
        *,
        campaign_id: CampaignId,
        task: Any,
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

        scene = await self._safe_call(self._scenes.active_scene_for_campaign, campaign_id)

        active_pc_ref = pc_ref
        if active_pc_ref is None:
            active_pc_ref = await self._safe_call(self._characters.active_pc, campaign_id)
        if active_pc_ref and not (task.extra_params or {}).get("active_pc_ref"):
            task.extra_params = {**(task.extra_params or {}), "active_pc_ref": active_pc_ref}

        scene_header = self._cast.render_scene_header(scene) if budget.include_scene_header else ""

        active_pc_card = ""
        if budget.include_active_pc_card:
            active_pc_card, _ = await self._cast.active_pc_card(active_pc_ref, campaign_id)

        voice_refs = resolve_voice_targets(task, scene)
        voice_lines: list[str] = []
        for ref in voice_refs:
            if not ref:
                continue
            anchor = await self._cast._voice_anchor(ref, campaign_id)
            if anchor:
                voice_lines.append(f"# Voice anchor — {ref}\n{anchor}")

        recent_posts_text = ""
        if budget.recent_posts_count > 0 and scene is not None:
            try:
                recent = await self._scenes.recent_posts(scene.id, n=budget.recent_posts_count)
            except Exception:
                recent = []
            recent_posts_text = render_recent_posts(list(recent or []))

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

    # -- helpers -------------------------------------------------------- #

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


__all__ = ["ContextBuilderService"]
