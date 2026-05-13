"""Concrete :class:`ContextBuilder` for spec 02.

The builder orchestrates the seven-step pipeline:

    0. resolve composition
    1. resolve scene state
    2. resolve cast (with tier promotion)
    3. resolve setting (location, adjacent, weather, factions, lore)
    4. resolve continuity (commitments, recent facts)
    5. archive retrieval (vector + keyword)
    6. budget allocation
    7. canonical assembly

The module dependencies are duck-typed at construction so unit tests can
substitute lightweight stubs for any subset of services. Production wiring
passes the concrete services from :mod:`grimoire.library`,
:mod:`grimoire.characters`, :mod:`grimoire.setting`,
:mod:`grimoire.scenes`, :mod:`grimoire.continuity`, and
:mod:`grimoire.llm_gateway`.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from grimoire.context.config import ContextBuilderConfig
from grimoire.context.errors import LockInOverflowError
from grimoire.context.tokens import TokenEstimator, cheap_estimator, estimate_tokens
from grimoire.templates import render as render_template
from grimoire.types.common import CampaignId
from grimoire.types.composition import Composition
from grimoire.types.context import AssembledPrompt, BudgetEstimate, ContextSource
from grimoire.types.llm import Message, MessageRole, ModelParams
from grimoire.types.mechanics import MechanicsResult
from grimoire.types.state import ContextTier

logger = logging.getLogger(__name__)


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
        setting: Any,
        scenes: Any,
        continuity: Any,
        mechanics: Any | None = None,
        gateway: Any | None = None,
        state_store: Any | None = None,
        config: ContextBuilderConfig | None = None,
    ) -> None:
        self._library = library
        self._characters = characters
        self._setting = setting
        self._scenes = scenes
        self._continuity = continuity
        self._mechanics = mechanics
        self._gateway = gateway
        self._store = state_store
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
    ) -> AssembledPrompt:
        ctx = await self._build_context(
            player_input=player_input,
            campaign_id=campaign_id,
            mechanics_results=mechanics_results or [],
            extra=extra,
            branch_id=branch_id,
            pc_ref=pc_ref,
        )
        return await self._assemble(ctx, player_input)

    async def estimate(
        self,
        player_input: str,
        campaign_id: CampaignId,
        *,
        branch_id: str | None = None,
        pc_ref: str | None = None,
    ) -> BudgetEstimate:
        """Dry-run the pipeline and report what each tier would consume."""
        ctx = await self._build_context(
            player_input=player_input,
            campaign_id=campaign_id,
            mechanics_results=[],
            extra=None,
            branch_id=branch_id,
            pc_ref=pc_ref,
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

    async def _build_context(
        self,
        *,
        player_input: str,
        campaign_id: CampaignId,
        mechanics_results: list[MechanicsResult],
        extra: str | None,
        branch_id: str | None,
        pc_ref: str | None,
    ) -> _BuiltContext:
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
        spotlight_items, background_items, voice_corrective = await self._resolve_cast(
            scene=scene,
            campaign_id=campaign_id,
            active_pc_ref=active_pc_ref,
            recent_posts=recent_posts,
        )

        # Step 3 — setting
        setting_spotlight, setting_background = await self._resolve_setting(
            scene=scene, campaign_id=campaign_id, branch_id=branch_id
        )
        spotlight_items.extend(setting_spotlight)
        background_items.extend(setting_background)

        # Step 4 — continuity
        commitments_block, commitments_source = await self._render_commitments(
            campaign_id, active_pc_ref
        )
        background_items.extend(await self._continuity_background(campaign_id))

        # Step 5 — archive retrieval
        archive_items = await self._retrieve_archive(
            player_input=player_input,
            campaign_id=campaign_id,
            scene=scene,
            recent_posts=recent_posts,
        )

        # Step 5a — lore keyword triggers (campaign-scoped) — archive tier
        archive_items.extend(await self._lore_triggers(player_input, campaign_id))

        # Mechanics block (lock-in)
        mechanics_block = self._render_mechanics(mechanics_results)

        # Build full sources list (active PC card + commitments + tier items)
        sources: list[ContextSource] = []
        if active_pc_source is not None:
            sources.append(active_pc_source)
        if commitments_source is not None:
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
        if composition is None or not composition.settings:
            return ""
        names: list[str] = []
        for ref in sorted(composition.settings, key=lambda r: r.priority):
            try:
                meta = await self._library.get_setting(ref.setting_id)
            except Exception:
                continue
            names.append(f"{meta.name} ({ref.setting_id})")
        if not names:
            return ""
        return "Settings in play: " + "; ".join(names)

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
        )
        return card, source

    async def _resolve_cast(
        self,
        *,
        scene: Any,
        campaign_id: CampaignId,
        active_pc_ref: str | None,
        recent_posts: list[Any],
    ) -> tuple[list[_TierItem], list[_TierItem], str]:
        spotlight_items: list[_TierItem] = []
        background_items: list[_TierItem] = []

        present_refs: list[str] = []
        if scene is not None:
            present_refs = list(getattr(scene, "present_character_refs", []) or [])

        # Spotlight = present chars (excluding active PC; that goes in lock-in).
        seen: set[str] = set()
        if active_pc_ref:
            seen.add(active_pc_ref)
        for ref in present_refs:
            if ref in seen:
                continue
            seen.add(ref)
            card = await self._try_full_card(ref, campaign_id)
            if not card:
                continue
            spotlight_items.append(
                _TierItem(
                    tier=ContextTier.SPOTLIGHT,
                    section="cast",
                    text=card,
                    priority=10,
                    source=self._character_source(ref, ContextTier.SPOTLIGHT, campaign_id),
                )
            )

        # Background = chars named in the last few posts but not present, plus
        # user-pinned background chars from the Characters tier recommendation.
        mentioned = self._mentions_in_posts(recent_posts) - seen
        budget = self._config.background_character_limit
        for ref in list(mentioned)[:budget]:
            text = await self._try_compressed_card(ref, campaign_id)
            if not text:
                continue
            background_items.append(
                _TierItem(
                    tier=ContextTier.BACKGROUND,
                    section="cast",
                    text=text,
                    priority=5,
                    source=self._character_source(ref, ContextTier.BACKGROUND, campaign_id),
                )
            )
            seen.add(ref)

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
        self, ref: str, tier: ContextTier, campaign_id: CampaignId
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
        )

    # -- setting -------------------------------------------------------- #

    async def _resolve_setting(
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
            setting_id, location_id = _parse_location_ref(location_ref)
        else:
            setting_id, location_id = None, None

        if setting_id and location_id and self._setting is not None:
            try:
                location = await self._setting.get_location(setting_id, location_id)
            except Exception:
                location = None
            if location is not None:
                desc = _render_location(location)
                spotlight.append(
                    _TierItem(
                        tier=ContextTier.SPOTLIGHT,
                        section="location",
                        text=desc,
                        priority=8,
                        source=ContextSource(
                            kind="location",
                            scope="library",
                            owner_id=f"library:settings/{setting_id}/locations/{location_id}",
                            tier=ContextTier.SPOTLIGHT,
                            summary=location.name,
                        ),
                    )
                )

                # Weather (spotlight context block)
                try:
                    weather = await self._setting.weather_for(
                        setting_id,
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
                            ),
                        )
                    )

                # Adjacent locations into background
                try:
                    adjacent = await self._setting.adjacent_locations(
                        setting_id, location_id, campaign_id
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
                                    owner_id=f"library:settings/{setting_id}",
                                    tier=ContextTier.BACKGROUND,
                                    summary="adjacency",
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
                    ),
                )
            )

        return spotlight, background

    # -- continuity ----------------------------------------------------- #

    async def _render_commitments(
        self, campaign_id: CampaignId, active_pc_ref: str | None
    ) -> tuple[str, ContextSource | None]:
        if self._continuity is None:
            return "", None
        try:
            commitments = await self._continuity.open_commitments(limit=20)
        except Exception:
            return "", None
        if not commitments:
            return "", None
        lines: list[str] = []
        for c in commitments[:10]:
            text = getattr(c, "text", "") or ""
            due = getattr(c, "due_by", None)
            due_part = f" (due {due.day_count})" if due is not None else ""
            lines.append(f"- {text}{due_part}")
        block = "Active commitments:\n" + "\n".join(lines)
        source = ContextSource(
            kind="commitment",
            scope="campaign-local",
            owner_id=campaign_id,
            tier=ContextTier.LOCK_IN,
            summary=f"{len(commitments)} open",
        )
        return block, source

    async def _continuity_background(self, campaign_id: CampaignId) -> list[_TierItem]:
        if self._continuity is None:
            return []
        try:
            facts = await self._continuity.facts_about(limit=8)
        except Exception:
            return []
        items: list[_TierItem] = []
        for fact in facts:
            text = getattr(fact, "text", "")
            if not text:
                continue
            items.append(
                _TierItem(
                    tier=ContextTier.BACKGROUND,
                    section="facts",
                    text=f"Fact: {text}",
                    priority=2,
                    source=ContextSource(
                        kind="fact",
                        scope="campaign-local",
                        owner_id=campaign_id,
                        tier=ContextTier.BACKGROUND,
                        summary=getattr(fact, "id", ""),
                    ),
                )
            )
        return items

    # -- archive retrieval --------------------------------------------- #

    async def _retrieve_archive(
        self,
        *,
        player_input: str,
        campaign_id: CampaignId,
        scene: Any,
        recent_posts: list[Any],
    ) -> list[_TierItem]:
        items: list[_TierItem] = []
        query = self._build_retrieval_query(player_input, scene, recent_posts)
        if not query:
            return items

        # Vector
        vector_hits = await self._vector_search(query, campaign_id)
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
                    ),
                )
            )

        # Keyword
        keyword_hits = await self._keyword_search(query, campaign_id)
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
                    ),
                )
            )
            seen_refs.add(hit.ref or "")
        return items

    async def _lore_triggers(self, player_input: str, campaign_id: CampaignId) -> list[_TierItem]:
        if self._setting is None or not player_input:
            return []
        try:
            triggered = await self._setting.lore_for_post(player_input, campaign_id)
        except Exception:
            return []
        items: list[_TierItem] = []
        for lore in triggered:
            body = getattr(lore, "body", "") or ""
            title = getattr(lore, "title", "") or ""
            text = f"[lore: {title}] {body[:400]}".strip()
            setting_id = getattr(lore, "setting_id", "")
            lore_id = getattr(lore, "id", "")
            items.append(
                _TierItem(
                    tier=ContextTier.ARCHIVE,
                    section="lore",
                    text=text,
                    priority=4,
                    source=ContextSource(
                        kind="lore",
                        scope="library",
                        owner_id=f"library:settings/{setting_id}/lore/{lore_id}",
                        tier=ContextTier.ARCHIVE,
                        summary=title,
                    ),
                )
            )
        return items

    async def _vector_search(self, query: str, campaign_id: CampaignId) -> list[Any]:
        if self._gateway is None or self._store is None:
            return []
        try:
            vectors = await self._gateway.embed(
                self._config.retrieval.embedding_task, [query], campaign_id=campaign_id
            )
        except Exception:
            return []
        if not vectors:
            return []
        try:
            return await self._store.vector_search(
                query_vector=vectors[0],
                campaign_id=campaign_id,
                include_library=self._config.retrieval.include_library,
                top_k=self._config.retrieval.vector_top_k,
            )
        except Exception:
            return []

    async def _keyword_search(self, query: str, campaign_id: CampaignId) -> list[Any]:
        if self._store is None:
            return []
        try:
            return await self._store.keyword_search(
                query=query,
                campaign_id=campaign_id,
                kinds=self._config.retrieval.keyword_kinds,
                top_k=self._config.retrieval.keyword_top_k,
            )
        except Exception:
            return []

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
        # Sort high-priority first; drop low-priority when budget is exhausted.
        sorted_items = sorted(items, key=lambda it: -it.priority)
        used = 0
        packed: list[str] = []
        for item in sorted_items:
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
    """Parse ``library:settings/<s>/locations/<id>``-style refs."""
    if not ref:
        return None, None
    raw = ref
    if raw.startswith("library:"):
        raw = raw[len("library:") :]
    parts = raw.strip("/").split("/")
    if len(parts) >= 4 and parts[0] == "settings" and parts[2] in {"locations", "location"}:
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


def _hash_messages(messages: list[Message]) -> str:
    h = hashlib.sha256()
    for msg in messages:
        h.update(msg.role.encode("utf-8"))
        h.update(b"\x00")
        h.update(msg.content.encode("utf-8"))
        h.update(b"\x01")
    return h.hexdigest()


__all__ = ["ContextBuilderService"]
