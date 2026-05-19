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
from dataclasses import dataclass, field
from typing import Any

from grimoire.context.config import ContextBuilderConfig
from grimoire.context.errors import LockInOverflowError
from grimoire.context.tokens import TokenEstimator, cheap_estimator, estimate_tokens
from grimoire.continuity.registry import resolve_continuity
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


# --------------------------------------------------------------------------- #
# Internal data shapes
# --------------------------------------------------------------------------- #


@dataclass
class _TierItem:
    """One piece of structured content destined for a tier."""

    tier: ContextTier
    section: str  # 'cast' | 'location' | 'commitments' | ...
    text: str
    source: ContextSource
    priority: int = 0  # higher = packed first
    pinned: bool = False  # exempt from budget-driven eviction


@dataclass
class _PinSet:
    """Active context pins / excludes for a single build.

    ``pinned_source_ids`` and ``excluded_source_ids`` reference
    ``ContextSource.source_id`` values directly. ``pinned_entities`` and
    ``excluded_entities`` are ``(kind, ref)`` tuples that match a source
    via ``(source.kind, source.owner_id)``.
    """

    pinned_source_ids: set[str] = field(default_factory=set)
    excluded_source_ids: set[str] = field(default_factory=set)
    pinned_entities: set[tuple[str, str]] = field(default_factory=set)
    excluded_entities: set[tuple[str, str]] = field(default_factory=set)

    def is_excluded(self, source: ContextSource) -> bool:
        if source.source_id and source.source_id in self.excluded_source_ids:
            return True
        return (source.kind, source.owner_id or "") in self.excluded_entities

    def is_pinned(self, source: ContextSource) -> bool:
        if source.source_id and source.source_id in self.pinned_source_ids:
            return True
        return (source.kind, source.owner_id or "") in self.pinned_entities


@dataclass
class _BuiltContext:
    composition: Composition | None
    style_text: str
    content_boundaries: str
    system_meta: str
    scene_header: str
    active_pc_card: str
    mechanics_block: str
    commitments_block: str
    spotlight_items: list[_TierItem] = field(default_factory=list)
    background_items: list[_TierItem] = field(default_factory=list)
    archive_items: list[_TierItem] = field(default_factory=list)
    recent_posts_text: str = ""
    voice_corrective: str = ""
    sources: list[ContextSource] = field(default_factory=list)
    extra: str | None = None


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
    ) -> _PinSet:
        pins = _PinSet()
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
            # ``_PinSet`` would leak user-excluded content into the next
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
        pins: _PinSet | None = None,
    ) -> _BuiltContext:
        pins = pins or _PinSet()
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
        scene_header = self._render_scene_header(scene)

        # Step 2 — cast
        if pc_ref is None:
            active_pc_ref = await self._safe_call(self._characters.active_pc, campaign_id)
        else:
            active_pc_ref = pc_ref
        active_pc_card, active_pc_source = await self._active_pc_card(active_pc_ref, campaign_id)

        # Open commitments are reused for both the lock-in commitments block
        # and the tier-recommendation hint (`commitments_targeting_pcs`).
        open_commitments = await self._open_commitments(campaign_id)
        pc_refs = await self._pc_refs(campaign_id)
        commitments_targeting_pcs = self._commitments_targeting_pcs(open_commitments, pc_refs)

        spotlight_items, background_items, voice_corrective = await self._resolve_cast(
            scene=scene,
            campaign_id=campaign_id,
            active_pc_ref=active_pc_ref,
            recent_posts=recent_posts,
            commitments_targeting_pcs=commitments_targeting_pcs,
        )

        # Step 3 — world
        world_spotlight, world_background = await self._resolve_world(
            scene=scene, campaign_id=campaign_id, branch_id=branch_id
        )
        spotlight_items.extend(world_spotlight)
        background_items.extend(world_background)
        background_items.extend(
            await self._resolve_factions(scene=scene, campaign_id=campaign_id, branch_id=branch_id)
        )
        background_items.extend(
            await self._resolve_calendar(scene=scene, campaign_id=campaign_id, branch_id=branch_id)
        )

        # Step 4 — continuity
        commitments_block, commitments_source = self._render_commitments_block(
            campaign_id, open_commitments
        )
        background_items.extend(
            await self._continuity_background(campaign_id, active_pc_ref, recent_posts)
        )
        # Relationship deltas (compact, lock-in tier): short summary lines of
        # the most recent relationship updates between the active PC and any
        # present cast member.
        background_items.extend(
            await self._relationship_deltas(
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

        # Step 5a — lore keyword triggers (campaign-scoped) — archive tier
        archive_items.extend(await self._lore_triggers(player_input, campaign_id))

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
        def _filter_items(items: list[_TierItem]) -> list[_TierItem]:
            kept: list[_TierItem] = []
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

        return _BuiltContext(
            composition=composition,
            style_text=style_text,
            content_boundaries=boundaries or "",
            system_meta=system_meta,
            scene_header=scene_header,
            active_pc_card=active_pc_card,
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

    # -- scene / cast --------------------------------------------------- #

    def _render_scene_header(self, scene: Any) -> str:
        if scene is None:
            return "No active scene."
        lines = [f"Scene: {getattr(scene, 'title', None) or getattr(scene, 'slug', '')}"]
        if getattr(scene, "location_ref", None):
            lines.append(f"Location: {scene.location_ref}")
        igt = getattr(scene, "in_game_start", None)
        if igt is not None:
            lines.append(f"In-game start: {igt}")
        if getattr(scene, "mood", None):
            lines.append(f"Mood: {scene.mood}")
        present = list(getattr(scene, "present_character_refs", []) or [])
        if present:
            lines.append("Present cast: " + ", ".join(present))
        return "\n".join(lines)

    async def _active_pc_card(
        self, pc_ref: str | None, campaign_id: CampaignId
    ) -> tuple[str, ContextSource | None]:
        if not pc_ref:
            return "", None
        try:
            card = await self._characters.get_full_card(pc_ref, campaign_id)
        except Exception:
            return "", None
        if not card:
            return "", None
        source = ContextSource(
            kind="character",
            scope="campaign-local",
            owner_id=campaign_id,
            tier=ContextTier.LOCK_IN,
            summary=f"Active PC: {pc_ref}",
            source_id=_make_source_id("pc_card", pc_ref),
            inclusion_reasons=[InclusionReason.PC_CARD],
        )
        return card, source

    async def _resolve_cast(
        self,
        *,
        scene: Any,
        campaign_id: CampaignId,
        active_pc_ref: str | None,
        recent_posts: list[Any],
        commitments_targeting_pcs: set[str] | None = None,
    ) -> tuple[list[_TierItem], list[_TierItem], str]:
        spotlight_items: list[_TierItem] = []
        background_items: list[_TierItem] = []

        present_refs: list[str] = []
        if scene is not None:
            present_refs = list(getattr(scene, "present_character_refs", []) or [])

        # Reasons accumulator: each ref accumulates the set of reasons that
        # contributed to its inclusion. Composed when a character is, e.g.,
        # both present and has an open commitment to a PC.
        reasons_by_ref: dict[str, set[InclusionReason]] = {}

        def _add_reason(ref: str, reason: InclusionReason) -> None:
            reasons_by_ref.setdefault(ref, set()).add(reason)

        for ref in present_refs:
            _add_reason(ref, InclusionReason.PRESENT_IN_SCENE)

        mentioned_refs = self._mentions_in_posts(recent_posts)
        for ref in mentioned_refs:
            _add_reason(ref, InclusionReason.MENTIONED_IN_RECENT_POSTS)

        for ref in commitments_targeting_pcs or set():
            _add_reason(ref, InclusionReason.COMMITMENT_OPEN_TO_PC)

        # Ask Characters for its tier recommendation. This bakes in: presence
        # → spotlight, mentioned in recent posts → background, open commitment
        # with a PC → background, inactivity → demote, and user pins → forced
        # tier (spec characters §Tier management; remaining design §1/§2).
        # If the caller (or test) wires a Characters stub without this method
        # we fall back to a body-token scan of recent posts.
        tier_map = await self._recommend_tiers(
            scene=scene,
            campaign_id=campaign_id,
            recent_posts=recent_posts,
            commitments_targeting_pcs=commitments_targeting_pcs,
        )

        seen: set[str] = set()
        if active_pc_ref:
            seen.add(active_pc_ref)

        # Spotlight tier: present chars (excluding active PC) plus anyone
        # else recommended to SPOTLIGHT by Characters (e.g. user pin).
        spotlight_refs: list[str] = []
        for ref in present_refs:
            if ref in seen:
                continue
            spotlight_refs.append(ref)
            seen.add(ref)
        for ref, tier in tier_map.items():
            if ref in seen or tier != ContextTier.SPOTLIGHT:
                continue
            spotlight_refs.append(ref)
            seen.add(ref)

        for ref in spotlight_refs:
            card = await self._try_full_card(ref, campaign_id)
            if not card:
                continue
            ref_reasons = sorted(reasons_by_ref.get(ref, set()), key=lambda r: r.value)
            # §8: prepend a world-id-aware header so duplicate names in two
            # worlds render distinctly. Library refs only — campaign-local
            # entities don't have a world prefix to surface.
            spotlight_items.append(
                _TierItem(
                    tier=ContextTier.SPOTLIGHT,
                    section="cast",
                    text=_with_cast_header(ref, card),
                    priority=10,
                    source=self._character_source(
                        ref, ContextTier.SPOTLIGHT, campaign_id, reasons=ref_reasons
                    ),
                )
            )
            # §9 — Voice anchor: emit a separate spotlight item carrying just
            # the voice snippet, distinct from the full card.
            if self._config.enable_voice_anchor:
                voice_text = await self._voice_anchor(ref, campaign_id)
                if voice_text:
                    spotlight_items.append(
                        _TierItem(
                            tier=ContextTier.SPOTLIGHT,
                            section="voice_anchor",
                            text=f"# Voice anchor — {ref}\n{voice_text}",
                            priority=9,
                            source=ContextSource(
                                kind="character",
                                scope="library" if ref.startswith("library:") else "campaign-local",
                                owner_id=ref if ref.startswith("library:") else campaign_id,
                                tier=ContextTier.SPOTLIGHT,
                                summary=f"voice:{ref}",
                                source_id=_make_source_id("voice", ref),
                                inclusion_reasons=list(ref_reasons),
                            ),
                        )
                    )
            # Transient stanza: spotlight-tier compact "current state" block
            # (mood / intent / action / thinking). Privacy-filtered upstream.
            stanza_item = await self._maybe_transient_stanza_item(
                ref=ref,
                campaign_id=campaign_id,
                active_pc_ref=active_pc_ref,
            )
            if stanza_item is not None:
                spotlight_items.append(stanza_item)

            # § Narrative extras stanza. Fits between voice anchor
            # (priority 9) and recent dialogue (7). Demotes to a
            # keys-only breadcrumb in BACKGROUND on overflow.
            if self._config.enable_extras_stanza:
                spot, bg = await self._extras_tier_items(ref, campaign_id)
                spotlight_items.extend(spot)
                background_items.extend(bg)

            # §10 — recent direct dialogue per spotlighted speaker.
            dialogue = self._recent_dialogue_for(ref, recent_posts)
            if dialogue:
                spotlight_items.append(
                    _TierItem(
                        tier=ContextTier.SPOTLIGHT,
                        section="recent_dialogue",
                        text=f"# Recent dialogue — {ref}\n{dialogue}",
                        priority=7,
                        source=ContextSource(
                            kind="post",
                            scope="campaign-local",
                            owner_id=campaign_id,
                            tier=ContextTier.SPOTLIGHT,
                            summary=f"dialogue:{ref}",
                            source_id=_make_source_id("dialogue", ref),
                            inclusion_reasons=list(ref_reasons),
                        ),
                    )
                )

        # Background tier: anyone recommended to BACKGROUND, plus the
        # body-token fallback for stubs that don't implement recommend_tiers.
        background_refs: list[str] = []
        for ref, tier in tier_map.items():
            if ref in seen or tier != ContextTier.BACKGROUND:
                continue
            background_refs.append(ref)
            seen.add(ref)

        if not tier_map:
            # Legacy fallback when Characters lacks recommend_tiers.
            mentioned = self._mentions_in_posts(recent_posts) - seen
            for ref in mentioned:
                background_refs.append(ref)
                seen.add(ref)

        for ref in background_refs[: self._config.background_character_limit]:
            text = await self._try_compressed_card(ref, campaign_id)
            if not text:
                continue
            ref_reasons = sorted(reasons_by_ref.get(ref, set()), key=lambda r: r.value)
            background_items.append(
                _TierItem(
                    tier=ContextTier.BACKGROUND,
                    section="cast",
                    text=_with_cast_header(ref, text),
                    priority=5,
                    source=self._character_source(
                        ref, ContextTier.BACKGROUND, campaign_id, reasons=ref_reasons
                    ),
                )
            )

        # Drift corrective for the spotlight chars (or active PC).
        corrective_lines: list[str] = []
        for ref in [r for r in [active_pc_ref, *present_refs] if r]:
            try:
                snippet = await self._characters.drift_corrective_context(ref, campaign_id)
            except Exception:
                snippet = ""
            if snippet:
                corrective_lines.append(snippet)
        return spotlight_items, background_items, "\n\n".join(corrective_lines)

    async def _recommend_tiers(
        self,
        *,
        scene: Any,
        campaign_id: CampaignId,
        recent_posts: list[Any],
        commitments_targeting_pcs: set[str] | None,
    ) -> dict[str, ContextTier]:
        """Wrap ``CharactersService.recommend_tiers`` with graceful fallback.

        Returns an empty dict when:
        * the characters service does not expose ``recommend_tiers``
        * the scene is None (no presence/commitment signal to feed the rule
          engine)
        * the call raises (we log at DEBUG and degrade to the legacy
          body-token fallback in ``_resolve_cast``)
        """
        if scene is None:
            return {}
        recommend = getattr(self._characters, "recommend_tiers", None)
        if recommend is None:
            return {}
        try:
            out = await recommend(
                scene,
                campaign_id,
                recent_posts=list(recent_posts),
                commitments_targeting_pcs=commitments_targeting_pcs or set(),
            )
        except TypeError:
            # Stubs / older signatures.
            try:
                out = await recommend(scene)
            except Exception:
                return {}
        except Exception as exc:
            logger.debug("recommend_tiers failed: %s", exc)
            return {}
        return dict(out or {})

    async def _voice_anchor(self, ref: str, campaign_id: CampaignId) -> str:
        getter = getattr(self._characters, "get_voice_only", None)
        if getter is None:
            return ""
        try:
            return (await getter(ref, campaign_id)) or ""
        except Exception as exc:
            logger.debug("get_voice_only(%s) failed: %s", ref, exc)
            return ""

    async def _maybe_transient_stanza_item(
        self,
        *,
        ref: str,
        campaign_id: CampaignId,
        active_pc_ref: str | None,
    ) -> _TierItem | None:
        """Render the optional spotlight-tier transient-state stanza.

        Returns ``None`` when no transient service is wired or no
        stanza-eligible fields are present. The active PC's owner sees
        their own thoughts unconditionally; everyone else reads through
        the privacy filter.
        """
        if self._transient_state is None:
            return None
        from grimoire.transient_state.stanza import render_transient_stanza
        from grimoire.types.transient import EntityKind, ObserverKind

        observer = ObserverKind.PC_OWNER if ref == active_pc_ref else ObserverKind.OTHER_PC
        try:
            bundle = await self._transient_state.get(
                campaign_id,
                EntityKind.CHARACTER,
                ref,
                for_observer=observer,
            )
        except Exception as exc:
            logger.debug("transient_state.get(%s) failed: %s", ref, exc)
            return None
        if not bundle:
            return None
        if not isinstance(bundle, dict):
            return None
        name = await self._character_display_name(ref, campaign_id)
        text = render_transient_stanza(name or ref, bundle)
        if not text:
            return None
        return _TierItem(
            tier=ContextTier.SPOTLIGHT,
            section="transient",
            text=text,
            priority=8,
            source=ContextSource(
                kind="character",
                scope="campaign-local",
                owner_id=campaign_id,
                tier=ContextTier.SPOTLIGHT,
                summary=f"transient:{ref}",
            ),
        )

    async def _character_display_name(self, ref: str, campaign_id: CampaignId) -> str:
        getter = getattr(self._characters, "get_display_name", None)
        if getter is None:
            return ref
        try:
            return (await getter(ref, campaign_id)) or ref
        except Exception:
            return ref

    def _recent_dialogue_for(self, ref: str, posts: list[Any]) -> str:
        """Pull the last ``recent_dialogue_per_speaker`` posts authored by ref.

        We match against both PC and NPC author refs. Posts authored by
        ``narrator`` / ``system`` are skipped — those are not dialogue.
        """
        n = self._config.recent_dialogue_per_speaker
        if n <= 0 or not ref:
            return ""
        lines: list[str] = []
        for post in reversed(list(posts)):
            pc_ref = getattr(post, "author_pc_ref", None)
            npc_ref = getattr(post, "author_npc_ref", None)
            if pc_ref != ref and npc_ref != ref:
                continue
            body = (getattr(post, "body", "") or "").strip()
            if not body:
                continue
            lines.append(f"- {body}")
            if len(lines) >= n:
                break
        if not lines:
            return ""
        return "\n".join(reversed(lines))

    async def _extras_tier_items(
        self, ref: str, campaign_id: CampaignId
    ) -> tuple[list[_TierItem], list[_TierItem]]:
        """Render the narrative-extras stanza for one character.

        Returns ``(spotlight_items, background_items)``. The spotlight item
        carries the full ``key: value`` listing; on overflow (token estimate
        exceeds ``extras_demote_to_breadcrumb_threshold_tokens``) the
        stanza becomes a keys-only breadcrumb in BACKGROUND, with the
        spotlight item dropped.
        """
        try:
            resolved = await self._library.resolve(ref, campaign_id)
        except Exception as exc:
            logger.debug("library.resolve(%s) for extras failed: %s", ref, exc)
            return ([], [])
        fm = getattr(resolved, "frontmatter", None) or {}
        raw = fm.get("extras") or {}
        if not isinstance(raw, dict) or not raw:
            return ([], [])

        name = getattr(resolved, "name", None) or ref
        rendered = _format_extras_stanza(name, raw)
        if not rendered:
            return ([], [])

        token_estimate = await estimate_tokens(rendered, self._estimator)
        source = self._character_source(ref, ContextTier.SPOTLIGHT, campaign_id)
        if token_estimate <= self._config.extras_demote_to_breadcrumb_threshold_tokens:
            return (
                [
                    _TierItem(
                        tier=ContextTier.SPOTLIGHT,
                        section="extras",
                        text=rendered,
                        priority=self._config.extras_spotlight_priority,
                        source=ContextSource(
                            kind="character",
                            scope=source.scope,
                            owner_id=source.owner_id,
                            tier=ContextTier.SPOTLIGHT,
                            summary=f"extras:{ref}",
                        ),
                    )
                ],
                [],
            )

        breadcrumb = _format_extras_breadcrumb(name, raw)
        return (
            [],
            [
                _TierItem(
                    tier=ContextTier.BACKGROUND,
                    section="extras",
                    text=breadcrumb,
                    priority=3,
                    source=ContextSource(
                        kind="character",
                        scope=source.scope,
                        owner_id=source.owner_id,
                        tier=ContextTier.BACKGROUND,
                        summary=f"extras-breadcrumb:{ref}",
                    ),
                )
            ],
        )

    async def _try_full_card(self, ref: str, campaign_id: CampaignId) -> str:
        try:
            return await self._characters.get_full_card(ref, campaign_id)
        except Exception as exc:
            logger.debug("get_full_card(%s) failed: %s", ref, exc)
            return ""

    async def _try_compressed_card(self, ref: str, campaign_id: CampaignId) -> str:
        try:
            return await self._characters.get_compressed_card(ref, campaign_id)
        except Exception as exc:
            logger.debug("get_compressed_card(%s) failed: %s", ref, exc)
            return ""

    def _character_source(
        self,
        ref: str,
        tier: ContextTier,
        campaign_id: CampaignId,
        *,
        reasons: list[InclusionReason] | None = None,
    ) -> ContextSource:
        # Library-prefixed refs are library-sourced; otherwise campaign-local.
        if ref.startswith("library:"):
            scope = "library"
            owner = ref
        else:
            scope = "campaign-local"
            owner = campaign_id
        return ContextSource(
            kind="character",
            scope=scope,
            owner_id=owner,
            tier=tier,
            summary=ref,
            source_id=_make_source_id("character", ref),
            inclusion_reasons=list(reasons or []),
        )

    # -- world -------------------------------------------------------- #

    async def _resolve_world(
        self,
        *,
        scene: Any,
        campaign_id: CampaignId,
        branch_id: str | None,
    ) -> tuple[list[_TierItem], list[_TierItem]]:
        spotlight: list[_TierItem] = []
        background: list[_TierItem] = []
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
                    _TierItem(
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
                            source_id=_make_source_id("location", location_owner),
                            inclusion_reasons=[InclusionReason.SCENE_ANCHOR],
                        ),
                    )
                )

                # Weather (spotlight context block)
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
                        _TierItem(
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
                                source_id=_make_source_id(
                                    "weather", f"{campaign_id}:{world_id}:{location_id}"
                                ),
                                inclusion_reasons=[InclusionReason.SCENE_ANCHOR],
                            ),
                        )
                    )

                # Adjacent locations into background
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
                            _TierItem(
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
                                    source_id=_make_source_id(
                                        "adjacency", f"library:worlds/{world_id}"
                                    ),
                                    inclusion_reasons=[InclusionReason.SCENE_ANCHOR],
                                ),
                            )
                        )

        # Running summary (spotlight)
        summary = getattr(scene, "running_summary", None) or ""
        if summary:
            spotlight.append(
                _TierItem(
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
                        source_id=_make_source_id("scene_summary", campaign_id),
                        inclusion_reasons=[InclusionReason.SCENE_ANCHOR],
                    ),
                )
            )

        return spotlight, background

    async def _resolve_factions(
        self,
        *,
        scene: Any,
        campaign_id: CampaignId,
        branch_id: str | None,
    ) -> list[_TierItem]:
        """§3 — politically relevant faction state in the background tier.

        Politically relevant = any faction declared in the active composition.
        We pull faction state via the World service when available, cap the
        number we surface, and render each as a short compact entry.
        """
        if self._world is None:
            return []
        faction_refs = await self._faction_refs_for_scene(scene, campaign_id)
        if not faction_refs:
            return []
        getter = getattr(self._world, "faction_state", None)
        if getter is None:
            return []
        items: list[_TierItem] = []
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
                _TierItem(
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
                        source_id=_make_source_id("faction", ref),
                        inclusion_reasons=[InclusionReason.COMPOSITION_DEFAULT],
                    ),
                )
            )
        return items

    async def _faction_refs_for_scene(self, scene: Any, campaign_id: CampaignId) -> list[str]:
        """Enumerate faction refs the builder can surface for this scene.

        Strategy: ask World for the factions declared in each composition
        world. Falls back to an empty list if the service does not expose
        ``list_factions``.
        """
        lister = getattr(self._world, "list_factions", None)
        if lister is None:
            return []
        composition = await self._safe_call(self._library.get_composition, campaign_id)
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

    async def _resolve_calendar(
        self,
        *,
        scene: Any,
        campaign_id: CampaignId,
        branch_id: str | None,
    ) -> list[_TierItem]:
        """§4 — calendar / world-time context for the background tier.

        Renders an at-most-one item summarising the current in-game date,
        season, and any imminent scheduled events.
        """
        if self._world is None and self._time_engine is None:
            return []

        # Prefer the time engine's current() (authoritative campaign clock).
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

        season = await self._safe_call(self._world.season_for, when, campaign_id)
        holiday = await self._safe_call(self._world.holiday_at, when, campaign_id)
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
            _TierItem(
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
                    source_id=_make_source_id("calendar", campaign_id),
                    inclusion_reasons=[InclusionReason.COMPOSITION_DEFAULT],
                ),
            )
        ]

    # -- continuity ----------------------------------------------------- #

    async def _open_commitments(self, campaign_id: CampaignId) -> list[Any]:
        continuity = resolve_continuity(self._continuity, campaign_id)
        if continuity is None:
            return []
        try:
            return list(await continuity.open_commitments(limit=20))
        except Exception:
            return []

    async def _pc_refs(self, campaign_id: CampaignId) -> set[str]:
        """The set of every PC ref registered with this campaign."""
        lister = getattr(self._characters, "list_pcs", None)
        if lister is None:
            return set()
        try:
            entries = await lister(campaign_id)
        except Exception:
            return set()
        out: set[str] = set()
        for entry in entries or []:
            ref = getattr(entry, "character_ref", None) or getattr(entry, "ref", None)
            if ref:
                out.add(ref)
        return out

    def _commitments_targeting_pcs(self, commitments: list[Any], pc_refs: set[str]) -> set[str]:
        """Set of non-PC refs that owe a commitment to a PC.

        Mirrors the contract expected by ``CharactersService.recommend_tiers``:
        the caller passes the refs of NPCs whose commitments target the
        active PCs, and those NPCs are promoted to at least BACKGROUND.
        """
        if not pc_refs:
            return set()
        out: set[str] = set()
        for c in commitments:
            from_id = getattr(c, "from_id", None)
            to_id = getattr(c, "to_id", None)
            if from_id and from_id not in pc_refs and to_id in pc_refs:
                out.add(from_id)
        return out

    def _render_commitments_block(
        self, campaign_id: CampaignId, commitments: list[Any]
    ) -> tuple[str, ContextSource | None]:
        # §8: honour ``surface_overdue_in_context`` (default True) so
        # OVERDUE rows get an ``[OVERDUE]`` suffix instead of being
        # silently mixed in with active ones. The builder doesn't carry
        # the current in-game time, so we split on commitment.status —
        # the aging engine promotes due-passed items to OVERDUE on time
        # advance.
        config = getattr(self._continuity, "_config", None)
        surface_overdue = getattr(config, "surface_overdue_in_context", True)
        overdue_ids: set[str] = (
            {
                getattr(c, "id", "")
                for c in commitments
                if getattr(getattr(c, "status", None), "value", "") == "overdue"
            }
            if surface_overdue
            else set()
        )
        if not commitments:
            return "", None
        lines: list[str] = []
        for c in commitments[:10]:
            text = getattr(c, "text", "") or ""
            due = getattr(c, "due_by", None)
            due_part = f" (due {due.day_count})" if due is not None else ""
            overdue_part = " [OVERDUE]" if getattr(c, "id", "") in overdue_ids else ""
            lines.append(f"- {text}{due_part}{overdue_part}")
        block = "Active commitments:\n" + "\n".join(lines)
        source = ContextSource(
            kind="commitment",
            scope="campaign-local",
            owner_id=campaign_id,
            tier=ContextTier.LOCK_IN,
            summary=f"{len(commitments)} open",
            source_id=_make_source_id("commitments", campaign_id),
            inclusion_reasons=[InclusionReason.COMMITMENT_OPEN_TO_PC],
        )
        return block, source

    async def _continuity_background(
        self,
        campaign_id: CampaignId,
        active_pc_ref: str | None = None,
        recent_posts: list[Any] | None = None,
    ) -> list[_TierItem]:
        """§5/§6/§7 — recent facts (POV-filtered + keyword-driven).

        Pulls the most recent facts the active PC knows about (§6) and
        augments them with any extra facts whose keywords overlap with
        proper nouns in the recent posts (§7). With no active PC we
        keep the omniscient narrator view. Total characters are capped
        at ``recent_facts_char_cap`` to keep background tight.
        """
        continuity = resolve_continuity(self._continuity, campaign_id)
        if continuity is None:
            return []
        try:
            limit = self._config.recent_facts_limit
            if active_pc_ref and hasattr(continuity, "facts_known_by"):
                facts = await continuity.facts_known_by(active_pc_ref, limit=limit)
            else:
                facts = await continuity.facts_about(limit=limit)
        except Exception:
            return []
        # §7 keyword-driven retrieval: pull additional facts whose
        # keywords overlap with proper nouns in the recent posts. Dedup
        # by fact id so we don't surface the same fact twice.
        if recent_posts and hasattr(continuity, "facts_for_terms"):
            seen_ids = {getattr(f, "id", "") for f in facts}
            try:
                terms = _proper_noun_terms(recent_posts)
                if terms:
                    extra = await continuity.facts_for_terms(terms, limit=5)
                    for f in extra:
                        fid = getattr(f, "id", "")
                        if fid and fid in seen_ids:
                            continue
                        seen_ids.add(fid)
                        facts.append(f)
            except Exception:
                pass
        if not facts:
            return []
        lines: list[str] = []
        char_cap = self._config.recent_facts_char_cap
        used = 0
        for fact in facts:
            text = (getattr(fact, "text", "") or "").strip()
            if not text:
                continue
            line = f"- {text}"
            cost = len(line) + 1
            if used + cost > char_cap:
                break
            lines.append(line)
            used += cost
        if not lines:
            return []
        block = "Recent facts:\n" + "\n".join(lines)
        return [
            _TierItem(
                tier=ContextTier.BACKGROUND,
                section="facts",
                text=block,
                priority=2,
                source=ContextSource(
                    kind="fact",
                    scope="campaign-local",
                    owner_id=campaign_id,
                    tier=ContextTier.BACKGROUND,
                    summary=f"{len(lines)} facts",
                    source_id=_make_source_id("facts", campaign_id),
                    inclusion_reasons=[InclusionReason.KEYWORD_TRIGGERED],
                ),
            )
        ]

    async def _relationship_deltas(
        self,
        *,
        active_pc_ref: str | None,
        scene: Any,
        campaign_id: CampaignId,
        branch_id: str | None,
    ) -> list[_TierItem]:
        """§6 — compact relationship deltas since the last scene.

        For each present character (excluding the active PC), we pull the
        tail of the relationship history with the active PC and render the
        most recent event as a single line. Calls are best-effort: missing
        APIs, empty history, or thrown exceptions silently produce nothing.
        """
        if not active_pc_ref or scene is None:
            return []
        getter = getattr(self._characters, "get_relationship_history", None)
        if getter is None:
            return []
        present = list(getattr(scene, "present_character_refs", []) or [])
        lines: list[str] = []
        for other in present:
            if other == active_pc_ref:
                continue
            try:
                history = await getter(active_pc_ref, other, campaign_id, branch_id=branch_id)
            except TypeError:
                try:
                    history = await getter(active_pc_ref, other, campaign_id)
                except Exception:
                    history = []
            except Exception:
                history = []
            if not history:
                continue
            event = history[-1]
            summary = (
                event.get("summary") if isinstance(event, dict) else getattr(event, "summary", "")
            )
            delta = event.get("delta") if isinstance(event, dict) else getattr(event, "delta", {})
            if not summary and not delta:
                continue
            delta_str = _format_delta(delta or {})
            text_parts = []
            if delta_str:
                text_parts.append(delta_str)
            if summary:
                text_parts.append(str(summary))
            line = f"- {active_pc_ref} ↔ {other}: " + " — ".join(text_parts)
            lines.append(line)
        if not lines:
            return []
        block = "Relationship deltas since last scene:\n" + "\n".join(lines)
        return [
            _TierItem(
                tier=ContextTier.BACKGROUND,
                section="relationship_deltas",
                text=block,
                priority=4,
                source=ContextSource(
                    kind="relationship",
                    scope="campaign-local",
                    owner_id=campaign_id,
                    tier=ContextTier.BACKGROUND,
                    summary=f"{len(lines)} deltas",
                    source_id=_make_source_id("relationship_deltas", campaign_id),
                    inclusion_reasons=[InclusionReason.RELATIONSHIP_TO_PRESENT],
                ),
            )
        ]

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
    ) -> list[_TierItem]:
        items: list[_TierItem] = []
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
                _TierItem(
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
                _TierItem(
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
    ) -> list[_TierItem]:
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

        items: list[_TierItem] = []
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
                _TierItem(
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

    async def _lore_triggers(self, player_input: str, campaign_id: CampaignId) -> list[_TierItem]:
        if self._world is None or not player_input:
            return []
        try:
            triggered = await self._world.lore_for_post(player_input, campaign_id)
        except Exception:
            return []
        items: list[_TierItem] = []
        for lore in triggered:
            body = getattr(lore, "body", "") or ""
            title = getattr(lore, "title", "") or ""
            text = f"[lore: {title}] {body[:400]}".strip()
            world_id = getattr(lore, "world_id", "")
            lore_id = getattr(lore, "id", "")
            lore_owner = f"library:worlds/{world_id}/lore/{lore_id}"
            items.append(
                _TierItem(
                    tier=ContextTier.ARCHIVE,
                    section="lore",
                    text=text,
                    priority=4,
                    source=ContextSource(
                        kind="lore",
                        scope="library",
                        owner_id=lore_owner,
                        tier=ContextTier.ARCHIVE,
                        summary=title,
                        source_id=_make_source_id("lore", lore_owner),
                        inclusion_reasons=[
                            InclusionReason.LORE_ARCHIVE,
                            InclusionReason.KEYWORD_TRIGGERED,
                        ],
                    ),
                )
            )
        return items

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
    ) -> list[_TierItem]:
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
        items: list[_TierItem] = []
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
                _TierItem(
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

    async def _assemble(self, ctx: _BuiltContext, player_input: str) -> AssembledPrompt:
        messages: list[Message] = []
        budget_used: dict[ContextTier, int] = {t: 0 for t in ContextTier}

        # System block — never compressed, never dropped
        system_text = await self._system_block(ctx)
        if system_text:
            messages.append(Message(role=MessageRole.SYSTEM, content=system_text))

        # Lock-in: scene header + active PC + commitments + mechanics + last 2 posts verbatim
        lock_in_text = await self._lock_in_block(ctx)
        lock_in_tokens = await self._tokens(lock_in_text)
        lock_budget = self._config.tiers[ContextTier.LOCK_IN].max_tokens
        if lock_in_tokens > lock_budget:
            raise LockInOverflowError(used=lock_in_tokens, budget=lock_budget)
        if lock_in_text:
            messages.append(Message(role=MessageRole.SYSTEM, content=lock_in_text))
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
                    messages.append(Message(role=MessageRole.SYSTEM, content=older))

        # Player input
        if player_input:
            messages.append(Message(role=MessageRole.USER, content=player_input))

        if ctx.extra:
            messages.append(Message(role=MessageRole.USER, content=ctx.extra))

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

    async def _system_block(self, ctx: _BuiltContext) -> str:
        return render_template(
            "context_system_block",
            style_text=ctx.style_text,
            content_boundaries=ctx.content_boundaries,
            system_meta=ctx.system_meta,
            voice_corrective=ctx.voice_corrective,
        ).strip()

    async def _lock_in_block(self, ctx: _BuiltContext) -> str:
        return render_template(
            "context_lock_in_block",
            scene_header=ctx.scene_header,
            active_pc_card=ctx.active_pc_card,
            commitments_block=ctx.commitments_block,
            mechanics_block=ctx.mechanics_block,
            verbatim_posts=self._lock_in_verbatim_posts(ctx),
        ).strip()

    def _lock_in_verbatim_posts(self, ctx: _BuiltContext) -> str:
        # ctx.recent_posts_text is all rendered posts; we just slice the tail.
        if not ctx.recent_posts_text:
            return ""
        all_posts = ctx.recent_posts_text.split("\n\n")
        tail = all_posts[-self._config.lock_in_verbatim_posts :]
        return "\n\n".join(tail)

    def _render_older_recent(self, ctx: _BuiltContext, verbatim: int) -> str:
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
        items: list[_TierItem],
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
        messages.append(Message(role=MessageRole.SYSTEM, content=content))
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

    def _summary(self, ctx: _BuiltContext, budget_used: dict[ContextTier, int]) -> str:
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


def _parse_location_ref(ref: str | None) -> tuple[str | None, str | None]:
    """Parse ``library:worlds/<s>/locations/<id>``-style refs."""
    if not ref:
        return None, None
    raw = ref
    if raw.startswith("library:"):
        raw = raw[len("library:") :]
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


def _format_delta(delta: dict) -> str:
    """Render a ``{trust: +2, affection: -1}`` style delta into one line."""
    if not delta:
        return ""
    parts: list[str] = []
    for key in ("affection", "trust", "dominance", "intimacy"):
        if key not in delta:
            continue
        try:
            val = int(delta[key])
        except (TypeError, ValueError):
            continue
        sign = "+" if val >= 0 else ""
        parts.append(f"{key} {sign}{val}")
    return ", ".join(parts)


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
        return when.isoformat()  # type: ignore[no-any-return]
    except Exception:
        return str(when)


def _render_faction_state(ref: str, state: Any) -> str:
    """Compact one-paragraph dump of a FactionStateData-shaped object."""
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


def _with_cast_header(ref: str, card: str) -> str:
    """Prepend a `[world:<world_id>]` header on a cast card.

    §8 — when two referenced worlds carry a character with the same name,
    the model needs the world prefix to tell them apart. Campaign-local
    refs get no prefix.
    """
    if not ref.startswith("library:"):
        return card
    raw = ref[len("library:") :]
    parts = raw.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "worlds":
        world_id = parts[1]
        return f"[world:{world_id}]\n{card}"
    return card


def _format_extras_stanza(name: str, extras: dict) -> str:
    """Render the per-character extras stanza for the spotlight tier.

    Empty/None values are omitted. Lists are joined with ``; ``. Dicts are
    rendered ``key=value`` comma-separated. ExtraValue dicts (carrying
    metadata) project to their ``value`` field.
    """
    lines: list[str] = []
    for key, raw in extras.items():
        value = _project_extras_value(raw)
        rendered = _render_extras_value(value)
        if rendered is None or rendered == "":
            continue
        lines.append(f"  {key}: {rendered}")
    if not lines:
        return ""
    header = f"{name} — extras:"
    return "\n".join([header, *lines])


def _format_extras_breadcrumb(name: str, extras: dict) -> str:
    """Keys-only breadcrumb for the background tier on overflow."""
    keys = [
        key
        for key, raw in extras.items()
        if _render_extras_value(_project_extras_value(raw)) not in (None, "")
    ]
    if not keys:
        return ""
    return f"{name} — extras: {', '.join(keys)}"


def _project_extras_value(raw: Any) -> Any:
    """ExtraValue dicts on disk look like ``{value: ..., set_at: ..., ...}``."""
    if isinstance(raw, dict) and "value" in raw and "set_at" in raw:
        return raw.get("value")
    return raw


def _render_extras_value(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float, str)):
        return str(value)
    if isinstance(value, list):
        rendered = [_render_extras_value(v) for v in value if _render_extras_value(v) is not None]
        return "; ".join(r for r in rendered if r) or None
    if isinstance(value, dict):
        rendered = [
            f"{k}={_render_extras_value(v)}"
            for k, v in value.items()
            if _render_extras_value(v) is not None
        ]
        return ", ".join(rendered) or None
    return str(value)


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


_PROPER_NOUN_RE = __import__("re").compile(r"\b[A-Z][a-zA-Z]{2,}\b")


def _proper_noun_terms(recent_posts: Iterable[Any]) -> list[str]:
    """Pull capitalised tokens (likely proper nouns) from the recent
    posts so Continuity's keyword retrieval has something to anchor on.

    Filters out single-letter capitals and obvious sentence starters by
    requiring at least 3 chars; collisions with sentence-initial words
    are tolerable since the retrieval scorer requires multiple matches
    to surface anything.
    """
    seen: set[str] = set()
    out: list[str] = []
    for post in recent_posts:
        text = getattr(post, "content", "") or getattr(post, "text", "")
        if not isinstance(text, str):
            continue
        for match in _PROPER_NOUN_RE.findall(text):
            if match not in seen:
                seen.add(match)
                out.append(match)
    return out


def _make_source_id(kind: str, owner: str | None) -> str:
    """Stable id for a ``ContextSource``.

    Deterministic across builds with identical inputs so the inspector's
    diff can pair up the same logical chunk between two previews. The hash
    is short (12 hex chars) — enough to keep collisions negligible for the
    ~hundreds of sources per turn we expect.
    """
    raw = f"{kind}:{owner or ''}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"src_{digest}"


def _hash_messages(messages: list[Message]) -> str:
    h = hashlib.sha256()
    for msg in messages:
        h.update(msg.role.encode("utf-8"))
        h.update(b"\x00")
        h.update(msg.content.encode("utf-8"))
        h.update(b"\x01")
    return h.hexdigest()


__all__ = ["ContextBuilderService"]
