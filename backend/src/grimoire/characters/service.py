"""Concrete Characters service (spec 08).

Behavior facade over Library/World storage. The Library owns the on-disk
``worlds/<world>/characters/<id>.md`` files; this service adds the
character-specific behaviors: voice anchors and drift detection, context
tier recommendation, PC roles and multi-PC coordination, compressed card
views, campaign-scoped relationships, mechanical capability surfacing, and
imports.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from grimoire.event_bus import Event, EventBus
from grimoire.library import LibraryService
from grimoire.mechanics.service import MechanicsService
from grimoire.state_store import StateStore
from grimoire.state_store.indexers import make_library_id
from grimoire.types.characters import (
    CapsuleDraft,
    Character,
    CharacterData,
    CharacterFilter,
    CharacterImage,
    CharacterRole,
    DriftReport,
    ImportResult,
    IngestedCharacterCard,
    IngestOptions,
    LoreOverride,
    PCEntry,
    PromotionProposal,
    RelationshipEvent,
    RelationshipState,
    ResolvedCharacter,
    VoiceAnchor,
)
from grimoire.types.common import CampaignId, CharacterRef, PostId, Scope
from grimoire.types.composition import LibraryEntity, ResolutionLayer, ResolutionSource
from grimoire.types.mechanics import Capability
from grimoire.types.scene import Post, Scene
from grimoire.types.state import CharacterState, ContextTier, DeltaKind, StateDelta
from grimoire.util import new_id, now_iso, slugify_id

from .config import CharactersConfig
from .drift import (
    DriftChecker,
    DriftEventSink,
    LLMCallable,
)
from .drift_service import CharacterDriftService
from .errors import (
    CharacterNotFoundError,
    CharactersError,
)
from .ingest import LLMEnrichCallable
from .pc_profile import (
    PCProfile,
    PCProfileRevision,
    read_pc_profile,
    write_pc_profile,
)
from .pc_profile import (
    list_pc_profile_revisions as _list_profile_revisions,
)
from .pc_profile import (
    read_pc_profile_revision as _read_profile_revision,
)
from .promoter import CharacterPromoter
from .protocols import SheetMigrator
from .sheet_manager import (
    CharacterSheetManager,
    character_from_entity,
    character_from_frontmatter,
    frontmatter_from_payload,
)
from .view_cache import CharacterViewCache
from .views import (
    render_capsule,
    render_compressed,
    render_full,
    render_full_pc,
    render_voice_only,
)

_log = logging.getLogger(__name__)

PostFetcher = Callable[[str], Awaitable[list[Post]]]

LLMCapsuleDrafter = Callable[[CharacterData], Awaitable[CapsuleDraft]]

LLMVoiceAnchorDrafter = Callable[[Character, list[Post]], Awaitable[VoiceAnchor]]


def _branch_for(campaign_id: str, branch_id: str | None) -> str:
    return branch_id or f"{campaign_id}:main"


class CharactersService:
    """Spec 08 implementation — facade delegating to collaborators."""

    def __init__(
        self,
        library: LibraryService,
        mechanics: MechanicsService,
        *,
        config: CharactersConfig | None = None,
        event_bus: EventBus | None = None,
        post_fetcher: PostFetcher | None = None,
        drift_checker: DriftChecker | None = None,
        drift_event_sink: DriftEventSink | None = None,
        ingest_llm: LLMEnrichCallable | None = None,
        auto_capsule_llm: LLMCapsuleDrafter | None = None,
        voice_anchor_llm: LLMVoiceAnchorDrafter | None = None,
        sheet_migrator: SheetMigrator | None = None,
    ) -> None:
        self.library = library
        self.mechanics = mechanics
        self.store: StateStore = library.store
        self._config = config or CharactersConfig()
        self._voice_anchor_llm = voice_anchor_llm

        # Collaborators
        self._cache = CharacterViewCache(
            max_view_entries=self._config.cache.max_size,
            max_active_pc=256,
        )
        self._drift = CharacterDriftService(
            config=self._config.drift,
            post_fetcher=post_fetcher,
            drift_checker=drift_checker,
            drift_event_sink=drift_event_sink,
        )
        self._sheets = CharacterSheetManager(
            library=library,
            store=self.store,
            cache=self._cache,
            ingest_llm=ingest_llm,
            auto_capsule_llm=auto_capsule_llm,
        )
        self._promoter = CharacterPromoter(
            library=library,
            store=self.store,
            sheet_migrator=sheet_migrator,
        )
        if event_bus is not None:
            event_bus.subscribe("library_entity_changed", self._on_entity_changed)

    def _on_entity_changed(self, event: Event) -> None:
        kind = event.payload.get("kind")
        if kind == "character":
            self._cache.view_invalidate()

    @property
    def _active_pc(self):
        return self._cache._active_pc

    @property
    def _view_cache(self):
        return self._cache._view_cache

    # ------------------------------------------------------------------ #
    # CRUD (delegated to sheet_manager)
    # ------------------------------------------------------------------ #

    async def list_in_world(self, world_id: str) -> list[Character]:
        return await self._sheets.list_in_world(world_id)

    async def get(self, world_id: str, character_id: str) -> Character:
        return await self._sheets.get(world_id, character_id)

    async def create(self, world_id: str, payload: CharacterData) -> Character:
        return await self._sheets.create(world_id, payload)

    async def update(self, world_id: str, character_id: str, patch: dict) -> Character:
        return await self._sheets.update(world_id, character_id, patch)

    async def delete(self, world_id: str, character_id: str) -> None:
        return await self._sheets.delete(world_id, character_id)

    # ------------------------------------------------------------------ #
    # Emergent (campaign-local) + override
    # ------------------------------------------------------------------ #

    async def create_emergent(
        self,
        campaign_id: CampaignId,
        payload: CharacterData,
        *,
        source: str = "characters:emergent",
    ) -> str:
        return await self._sheets.create_emergent(campaign_id, payload, source=source)

    async def update_emergent(
        self,
        campaign_id: CampaignId,
        character_id: str,
        patch: dict,
        *,
        source: str = "characters:emergent-update",
    ) -> Character:
        return await self._sheets.update_emergent(campaign_id, character_id, patch, source=source)

    async def delete_emergent(self, campaign_id: CampaignId, character_id: str) -> None:
        return await self._sheets.delete_emergent(campaign_id, character_id)

    async def upsert_override(
        self,
        campaign_id: CampaignId,
        character_ref: str,
        patch: dict,
        *,
        source: str = "characters:override",
    ) -> None:
        return await self._sheets.upsert_override(campaign_id, character_ref, patch, source=source)

    # ------------------------------------------------------------------ #
    # Resolution
    # ------------------------------------------------------------------ #

    async def resolve(self, character_ref: str, campaign_id: CampaignId) -> ResolvedCharacter:
        ref_entity = _parse_character_ref(character_ref)
        if ref_entity.is_emergent:
            row = await self.store.get_emergent(campaign_id, "character", ref_entity.asset_id)
            if row is None:
                raise CharacterNotFoundError(
                    f"emergent character {ref_entity.asset_id!r} not found"
                )
            character = character_from_frontmatter(
                row.get("frontmatter") or {},
                row.get("body") or "",
                world_id=None,
            )
            chain = [
                ResolutionSource(
                    layer=ResolutionLayer.EMERGENT,
                    scope="campaign-local",
                    library_id=None,
                    world_id=None,
                    override_applied=False,
                )
            ]
            overrides_applied: list[str] = []
        else:
            entity = await self.library.resolve(
                f"worlds/{ref_entity.world_id}/characters/{ref_entity.asset_id}",
                campaign_id,
            )
            character = character_from_frontmatter(
                entity.frontmatter, entity.body, world_id=entity.world_id
            )
            chain = list(entity.source_chain)
            overrides_applied = list(entity.overrides_applied)

        state = await self._load_state(character.id, character_ref, campaign_id)
        capabilities = await self.capabilities_of(character_ref, campaign_id)
        return ResolvedCharacter(
            character=character,
            current_state=state,
            capabilities=[c.model_dump() for c in capabilities],
            source_chain=[s.model_dump() for s in chain],
            overrides_applied=overrides_applied,
        )

    async def list_for_campaign(
        self,
        campaign_id: CampaignId,
        filter: CharacterFilter | None = None,
    ) -> list[ResolvedCharacter]:
        out: list[ResolvedCharacter] = []
        composed = await self.library.list_for_composition(campaign_id, "character")
        for ent in composed:
            if not _passes_filter(ent, filter):
                continue
            ref = f"library:worlds/{ent.world_id}/characters/{ent.asset_id}"
            try:
                out.append(await self.resolve(ref, campaign_id))
            except CharacterNotFoundError:
                continue
        emergent_rows = await self.store.list_emergent(campaign_id, "character")
        for row in emergent_rows:
            ref = f"campaign:emergent/character/{row['asset_id']}"
            try:
                resolved = await self.resolve(ref, campaign_id)
            except CharacterNotFoundError:
                continue
            if filter is None or _passes_resolved_filter(resolved, filter):
                out.append(resolved)
        return out

    # ------------------------------------------------------------------ #
    # Cross-world variants
    # ------------------------------------------------------------------ #

    async def cross_world_lookup(
        self, character_id: str, exclude_world: str | None = None
    ) -> list[Character]:
        lookup_id = character_id
        if not self._config.cross_world_lookup.case_sensitive:
            lookup_id = slugify_id(character_id, fallback=character_id.lower())
        rows = await self.library.variants_of(lookup_id, "character")
        if exclude_world:
            rows = [r for r in rows if r.world_id != exclude_world]
        return [character_from_entity(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Compressed views (delegated to sheet_manager)
    # ------------------------------------------------------------------ #

    async def get_full_card(
        self, ref: str, campaign_id: CampaignId, *, seed: int | None = None
    ) -> str:
        cached = self._cache.view_get(ref, campaign_id, "full", seed)
        if cached is not None:
            return cached
        resolved = await self.resolve(ref, campaign_id)
        if resolved.character.role == CharacterRole.PC:
            asset_id = _asset_id_for_ref(ref)
            profile = read_pc_profile(self.store.data_root, campaign_id, asset_id)
            capabilities = await self.capabilities_of(ref, campaign_id)
            rendered = render_full_pc(
                resolved.character,
                profile=profile,
                capabilities=capabilities or None,
                seed=seed,
            )
        else:
            rendered = render_full(resolved.character, seed=seed)
        self._cache.view_set(ref, campaign_id, "full", seed, rendered)
        return rendered

    async def get_compressed_card(
        self, ref: str, campaign_id: CampaignId, *, seed: int | None = None
    ) -> str:
        cached = self._cache.view_get(ref, campaign_id, "compressed", seed)
        if cached is not None:
            return cached
        resolved = await self.resolve(ref, campaign_id)
        rendered = render_compressed(resolved.character, seed=seed)
        self._cache.view_set(ref, campaign_id, "compressed", seed, rendered)
        return rendered

    async def get_voice_only(
        self, ref: str, campaign_id: CampaignId, *, seed: int | None = None
    ) -> str:
        cached = self._cache.view_get(ref, campaign_id, "voice_only", seed)
        if cached is not None:
            return cached
        resolved = await self.resolve(ref, campaign_id)
        rendered = render_voice_only(resolved.character, seed=seed)
        self._cache.view_set(ref, campaign_id, "voice_only", seed, rendered)
        return rendered

    async def get_capsule(
        self, ref: str, campaign_id: CampaignId, *, seed: int | None = None
    ) -> str:
        cached = self._cache.view_get(ref, campaign_id, "capsule", seed)
        if cached is not None:
            return cached
        resolved = await self.resolve(ref, campaign_id)
        rendered = render_capsule(resolved.character, seed=seed)
        self._cache.view_set(ref, campaign_id, "capsule", seed, rendered)
        return rendered

    # ------------------------------------------------------------------ #
    # Tier management
    # ------------------------------------------------------------------ #

    async def recommend_tiers(
        self,
        scene: Scene,
        campaign_id: CampaignId | None = None,
        *,
        recent_posts: list[Post] | None = None,
        commitments_targeting_pcs: set[CharacterRef] | None = None,
    ) -> dict[CharacterRef, ContextTier]:
        target_campaign = campaign_id or scene.campaign_id
        out: dict[CharacterRef, ContextTier] = {}

        present = set(scene.present_character_refs)

        if recent_posts:
            recent_turn_ids = [p.turn_id for p in recent_posts]
            joined_body = "\n".join(p.body for p in recent_posts)
            resolved_list = await self.list_for_campaign(target_campaign)

            for resolved in resolved_list:
                ref = _ref_from_resolved(resolved)
                if ref in present:
                    continue
                last_seen = resolved.current_state.last_screen_time_turn
                turns_off_screen = _turns_since(last_seen, recent_turn_ids)
                if turns_off_screen is None:
                    continue
                if turns_off_screen >= self._config.tiers.demote_to_archive_after_turns:
                    out[ref] = ContextTier.ARCHIVE
                elif turns_off_screen >= self._config.tiers.demote_to_background_after_turns:
                    out[ref] = ContextTier.BACKGROUND

            for resolved in resolved_list:
                ref = _ref_from_resolved(resolved)
                if ref in present:
                    continue
                if _mentions_character(joined_body, resolved.character) and _tier_rank(
                    out.get(ref)
                ) < _tier_rank(ContextTier.BACKGROUND):
                    out[ref] = ContextTier.BACKGROUND

        if commitments_targeting_pcs:
            for ref in commitments_targeting_pcs:
                if ref in present:
                    continue
                if _tier_rank(out.get(ref)) < _tier_rank(ContextTier.BACKGROUND):
                    out[ref] = ContextTier.BACKGROUND

        for ref in scene.present_character_refs:
            out[ref] = ContextTier.SPOTLIGHT

        pins = await self.store.list_tier_pins(
            campaign_id=target_campaign,
            branch_id=_branch_for(target_campaign, None),
        )
        for ref in list(out.keys()):
            pin_value = pins.get(ref)
            if pin_value:
                out[ref] = ContextTier(pin_value)
        return out

    async def pin_tier(self, ref: CharacterRef, campaign_id: CampaignId, tier: ContextTier) -> None:
        state = await self._load_state(_asset_id_for_ref(ref), ref, campaign_id)
        state.tier_pin = tier
        await self._save_state(
            ref, campaign_id, state, source="characters:tier-pin", record_in_delta_log=False
        )
        self._cache.view_invalidate(ref=ref, campaign_id=campaign_id)

    # ------------------------------------------------------------------ #
    # Drift (delegated to drift_service)
    # ------------------------------------------------------------------ #

    async def check_drift(
        self,
        ref: CharacterRef,
        campaign_id: CampaignId,
        *,
        window: int = 10,
        recent_posts: list[Post] | None = None,
    ) -> DriftReport:
        resolved = await self.resolve(ref, campaign_id)
        report = await self._drift.check_drift(
            ref,
            campaign_id,
            resolved.character,
            resolved.current_state,
            window=window,
            recent_posts=recent_posts,
        )
        state = resolved.current_state
        state.drift_score = report.drift_score
        await self._save_state(ref, campaign_id, state, source="characters:drift-check")
        return report

    async def maybe_check_drift(
        self,
        ref: CharacterRef,
        campaign_id: CampaignId,
        *,
        recent_posts: list[Post] | None = None,
        force: bool = False,
    ) -> DriftReport | None:
        state = await self._load_state(_asset_id_for_ref(ref), ref, campaign_id)
        if not self._drift.should_check(state, force=force):
            return None

        report = await self.check_drift(ref, campaign_id, recent_posts=recent_posts)
        state = await self._load_state(_asset_id_for_ref(ref), ref, campaign_id)
        state.appearances_since_last_drift_check = 0
        await self._save_state(ref, campaign_id, state, source="characters:drift-cadence-reset")
        return report

    async def drift_corrective_context(
        self,
        ref: CharacterRef,
        campaign_id: CampaignId,
        *,
        inject_corrective_context: bool = True,
    ) -> str:
        if not inject_corrective_context:
            return ""
        state = await self._load_state(_asset_id_for_ref(ref), ref, campaign_id)
        if state.drift_score < self._config.drift.threshold:
            return ""
        resolved = await self.resolve(ref, campaign_id)
        return self._drift.corrective_text(resolved.character)

    def set_drift_checker(self, checker: DriftChecker | LLMCallable) -> None:
        self._drift.set_drift_checker(checker)

    # ------------------------------------------------------------------ #
    # Voice anchor drafting (spec 2026-05-17 §11)
    # ------------------------------------------------------------------ #

    async def draft_voice_anchor(
        self,
        character_ref: CharacterRef,
        campaign_id: CampaignId,
        *,
        sample_window: int = 10,
    ) -> VoiceAnchor:
        if self._voice_anchor_llm is None:
            raise CharactersError("draft_voice_anchor requires a voice_anchor_llm to be configured")
        resolved = await self.resolve(character_ref, campaign_id)
        posts: list[Post] = []
        if self._drift._post_fetcher is not None:
            scene_id = resolved.current_state.current_scene_id
            if scene_id:
                fetched = await self._drift._post_fetcher(scene_id)
                posts = list(fetched)
        matching = [p for p in posts if _mentions_character(p.body, resolved.character)]
        trimmed = matching[-sample_window:] if sample_window > 0 else matching
        return await self._voice_anchor_llm(resolved.character, trimmed)

    # ------------------------------------------------------------------ #
    # State
    # ------------------------------------------------------------------ #

    async def update_state(
        self,
        ref: CharacterRef,
        campaign_id: CampaignId,
        state: CharacterState,
        *,
        source: str = "characters:state-update",
    ) -> None:
        await self._save_state(ref, campaign_id, state, source=source)
        self._cache.view_invalidate(ref=ref, campaign_id=campaign_id)

    async def mark_screen_time(
        self, ref: CharacterRef, campaign_id: CampaignId, turn_id: str
    ) -> None:
        state = await self._load_state(_asset_id_for_ref(ref), ref, campaign_id)
        state.last_screen_time_turn = turn_id
        state.appearances_since_last_drift_check += 1
        await self._save_state(
            ref, campaign_id, state, source="characters:screen-time", turn_id=turn_id
        )

    async def get_state(self, ref: CharacterRef, campaign_id: CampaignId) -> CharacterState:
        return await self._load_state(_asset_id_for_ref(ref), ref, campaign_id)

    # ------------------------------------------------------------------ #
    # PCs
    # ------------------------------------------------------------------ #

    async def list_pcs(self, campaign_id: CampaignId) -> list[PCEntry]:
        rows = await self.store.list_pcs(campaign_id)
        active_ref = self._cache.seed_active_pc_from_rows(campaign_id, rows)
        out: list[PCEntry] = []
        for row in rows:
            char_ref = row["character_ref"]
            current_scene_id: str | None = None
            current_location_ref: str | None = None
            try:
                state = await self._load_state(_asset_id_for_ref(char_ref), char_ref, campaign_id)
                current_scene_id = state.current_scene_id
                current_location_ref = state.location_ref
            except Exception:
                pass
            last_played_at = _parse_iso_dt(row.get("last_played_at"))
            out.append(
                PCEntry(
                    character_ref=char_ref,
                    name=row["display_name"],
                    owner=row["owner"],
                    active=active_ref == char_ref,
                    current_scene_id=current_scene_id,
                    current_location_ref=current_location_ref,
                    last_played_at=last_played_at,
                )
            )
        return out

    async def add_pc(
        self,
        campaign_id: CampaignId,
        character_ref: CharacterRef,
        name: str,
        owner: str = "local",
    ) -> PCEntry:
        await self.store.add_pc(
            campaign_id=campaign_id,
            character_ref=character_ref,
            display_name=name,
            owner=owner,
        )
        if self._cache.get_active_pc(campaign_id) is None:
            self._cache.cache_active_pc(campaign_id, character_ref)
        return PCEntry(
            character_ref=character_ref,
            name=name,
            owner=owner,
            active=self._cache.get_active_pc(campaign_id) == character_ref,
        )

    async def remove_pc(self, campaign_id: CampaignId, character_ref: CharacterRef) -> None:
        await self.store.remove_pc(campaign_id=campaign_id, character_ref=character_ref)
        if self._cache.get_active_pc(campaign_id) == character_ref:
            self._cache.pop_active_pc(campaign_id)

    async def set_active_pc(self, campaign_id: CampaignId, character_ref: CharacterRef) -> None:
        pcs = await self.store.list_pcs(campaign_id)
        refs = {p["character_ref"] for p in pcs}
        if character_ref not in refs:
            raise CharactersError(f"{character_ref!r} is not a PC in campaign {campaign_id!r}")
        await self.store.set_active_pc(campaign_id=campaign_id, character_ref=character_ref)
        self._cache.cache_active_pc(campaign_id, character_ref)

    async def active_pc(self, campaign_id: CampaignId) -> CharacterRef | None:
        if self._cache.get_active_pc(campaign_id) is not None:
            return self._cache.get_active_pc(campaign_id)
        pcs = await self.store.list_pcs(campaign_id)
        return self._cache.seed_active_pc_from_rows(campaign_id, pcs)

    # ------------------------------------------------------------------ #
    # Per-PC current scene
    # ------------------------------------------------------------------ #

    async def current_scene_for_pc(
        self, campaign_id: CampaignId, character_ref: CharacterRef
    ) -> str | None:
        state = await self._load_state(_asset_id_for_ref(character_ref), character_ref, campaign_id)
        return state.current_scene_id

    async def set_current_scene_for_pc(
        self,
        campaign_id: CampaignId,
        character_ref: CharacterRef,
        scene_id: str,
    ) -> None:
        state = await self._load_state(_asset_id_for_ref(character_ref), character_ref, campaign_id)
        state.current_scene_id = scene_id
        await self._save_state(character_ref, campaign_id, state, source="characters:set-scene")

    # ------------------------------------------------------------------ #
    # Multi-PC turn semantics
    # ------------------------------------------------------------------ #

    async def present_pcs_in_scene(
        self, scene: Scene, campaign_id: CampaignId | None = None
    ) -> list[PCEntry]:
        target_campaign = campaign_id or scene.campaign_id
        pcs = await self.list_pcs(target_campaign)
        present_refs = set(scene.present_pc_refs) | {
            ref for ref in scene.present_character_refs if ref in {p.character_ref for p in pcs}
        }
        return [pc for pc in pcs if pc.character_ref in present_refs]

    async def should_auto_respond(
        self, scene: Scene, campaign_id: CampaignId | None = None
    ) -> bool:
        present = await self.present_pcs_in_scene(scene, campaign_id)
        return len(present) <= 1

    async def pending_pc_inputs_since_last_advance(
        self,
        scene: Scene,
        posts: list[Post],
    ) -> list[Post]:
        threshold = scene.last_advance_at_post or 0
        return [p for p in posts if p.order_in_scene > threshold and p.is_player]

    # ------------------------------------------------------------------ #
    # Mechanical capabilities
    # ------------------------------------------------------------------ #

    async def capabilities_of(self, ref: CharacterRef, campaign_id: CampaignId) -> list[Capability]:
        asset_id = _asset_id_for_ref(ref)
        return await self.mechanics.capabilities_of(
            campaign_id, f"character:{asset_id}", entity_kind="character"
        )

    # ------------------------------------------------------------------ #
    # PC profiles (campaign-scoped overlay)
    # ------------------------------------------------------------------ #

    async def get_pc_profile(self, campaign_id: CampaignId, ref: CharacterRef) -> PCProfile | None:
        asset_id = _asset_id_for_ref(ref)
        return read_pc_profile(self.store.data_root, campaign_id, asset_id)

    async def save_pc_profile(
        self, campaign_id: CampaignId, ref: CharacterRef, profile: PCProfile
    ) -> None:
        asset_id = _asset_id_for_ref(ref)
        write_pc_profile(self.store.data_root, campaign_id, asset_id, profile)
        self._cache.view_invalidate(ref, campaign_id)

    async def list_pc_profile_revisions(
        self, campaign_id: CampaignId, ref: CharacterRef
    ) -> list[PCProfileRevision]:
        asset_id = _asset_id_for_ref(ref)
        return _list_profile_revisions(
            self.store.data_root,
            campaign_id,
            asset_id,
        )

    async def get_pc_profile_revision(
        self, campaign_id: CampaignId, ref: CharacterRef, timestamp: str
    ) -> PCProfileRevision | None:
        asset_id = _asset_id_for_ref(ref)
        return _read_profile_revision(
            self.store.data_root,
            campaign_id,
            asset_id,
            timestamp,
        )

    # ------------------------------------------------------------------ #
    # Relationships (campaign-scoped)
    # ------------------------------------------------------------------ #

    async def get_relationships(
        self, ref: CharacterRef, campaign_id: CampaignId, *, branch_id: str | None = None
    ) -> list[dict]:
        branch = _branch_for(campaign_id, branch_id)
        rows = await self.store.db.fetchall(
            """
            SELECT * FROM relationships
            WHERE campaign_id = ?
              AND branch_id = ?
              AND (from_character_ref = ? OR to_character_ref = ?)
            """,
            (campaign_id, branch, ref, ref),
        )
        return [_relationship_row_to_dict(r) for r in rows]

    async def update_relationship(
        self,
        from_ref: CharacterRef,
        to_ref: CharacterRef,
        campaign_id: CampaignId,
        delta: dict,
        *,
        branch_id: str | None = None,
        types: list[str] | None = None,
        turn_id: str | None = None,
        in_post: PostId | None = None,
        summary: str | None = None,
    ) -> dict:
        branch = _branch_for(campaign_id, branch_id)
        existing = await self.store.db.fetchone(
            """
            SELECT * FROM relationships
            WHERE campaign_id = ? AND branch_id = ?
              AND from_character_ref = ? AND to_character_ref = ?
            """,
            (campaign_id, branch, from_ref, to_ref),
        )
        if existing is None:
            state = RelationshipState()
            existing_types = types or []
            row_id = new_id("rel")
            history = []
        else:
            state = _relationship_state_from_json(existing["state"])
            existing_types = json.loads(existing["types"]) if existing["types"] else []
            if types:
                merged = list(dict.fromkeys(existing_types + types))
                existing_types = merged
            row_id = existing["id"]
            history = _relationship_history_from_json(existing["history"])

        merged_state = state.model_dump()
        for key in ("affection", "trust", "dominance", "intimacy"):
            if key in delta:
                merged_state[key] = int(merged_state.get(key) or 0) + int(delta[key])
        for key in ("awareness",):
            if key in delta:
                merged_state[key] = delta[key]
        if "custom" in delta and isinstance(delta["custom"], dict):
            merged_state["custom"] = {**(merged_state.get("custom") or {}), **delta["custom"]}

        if summary:
            event = RelationshipEvent(
                in_post=in_post,
                summary=summary,
                delta=dict(delta),
                at=now_iso(),
            )
            history.append(event.model_dump())

        await self.store.db.execute(
            """
            INSERT INTO relationships (
              id, campaign_id, branch_id, from_character_ref, to_character_ref,
              types, state, updated_at_turn, history
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              types = excluded.types,
              state = excluded.state,
              updated_at_turn = excluded.updated_at_turn,
              history = excluded.history
            """,
            (
                row_id,
                campaign_id,
                branch,
                from_ref,
                to_ref,
                json.dumps(existing_types),
                json.dumps(merged_state, default=str),
                turn_id or now_iso(),
                json.dumps(history, default=str),
            ),
        )
        return {
            "id": row_id,
            "campaign_id": campaign_id,
            "branch_id": branch,
            "from_ref": from_ref,
            "to_ref": to_ref,
            "types": existing_types,
            "state": merged_state,
            "history": history,
        }

    async def get_relationship_history(
        self,
        from_ref: CharacterRef,
        to_ref: CharacterRef,
        campaign_id: CampaignId,
        *,
        branch_id: str | None = None,
    ) -> list[dict]:
        branch = _branch_for(campaign_id, branch_id)
        row = await self.store.db.fetchone(
            """
            SELECT history FROM relationships
            WHERE campaign_id = ? AND branch_id = ?
              AND from_character_ref = ? AND to_character_ref = ?
            """,
            (campaign_id, branch, from_ref, to_ref),
        )
        if row is None:
            return []
        return _relationship_history_from_json(row["history"])

    # ------------------------------------------------------------------ #
    # Promotion (delegated to promoter)
    # ------------------------------------------------------------------ #

    async def propose_promotion(
        self,
        campaign_id: CampaignId,
        character_id: str,
        target_world_id: str,
        *,
        target_character_id: str | None = None,
    ) -> PromotionProposal:
        return await self._promoter.propose_promotion(
            campaign_id,
            character_id,
            target_world_id,
            target_character_id=target_character_id,
        )

    async def promote_to_library(
        self,
        campaign_id: CampaignId,
        character_id: str,
        target_world_id: str,
        *,
        source: str = "characters:promote",
        delete_emergent: bool = False,
        target_character_id: str | None = None,
        confirm: bool = False,
        proposal: PromotionProposal | None = None,
    ) -> str:
        return await self._promoter.promote_to_library(
            campaign_id,
            character_id,
            target_world_id,
            source=source,
            delete_emergent=delete_emergent,
            target_character_id=target_character_id,
            confirm=confirm,
            proposal=proposal,
        )

    # ------------------------------------------------------------------ #
    # Imports (delegated to sheet_manager)
    # ------------------------------------------------------------------ #

    async def import_sillytavern(
        self,
        card: bytes,
        target_world_id: str,
        *,
        options: IngestOptions | None = None,
    ) -> ImportResult:
        return await self._sheets.import_sillytavern(card, target_world_id, options=options)

    async def import_charx(
        self,
        charx_bytes: bytes,
        target_world_id: str,
        *,
        options: IngestOptions | None = None,
    ) -> ImportResult:
        return await self._sheets.import_charx(charx_bytes, target_world_id, options=options)

    async def import_plaintext(self, text: str, target_world_id: str) -> ImportResult:
        return await self._sheets.import_plaintext(text, target_world_id)

    async def import_character_card(
        self,
        payload: bytes,
        target_world_id: str,
        *,
        options: IngestOptions | None = None,
    ) -> tuple[ImportResult, IngestedCharacterCard]:
        return await self._sheets.import_character_card(payload, target_world_id, options=options)

    async def add_character_image(
        self,
        world_id: str,
        character_id: str,
        image: CharacterImage,
        *,
        image_bytes: bytes | None = None,
        source: str = "characters:add-image",
    ) -> Character:
        return await self._sheets.add_character_image(
            world_id, character_id, image, image_bytes=image_bytes, source=source
        )

    async def _ingest(
        self, payload: bytes, *, options: IngestOptions | None
    ) -> IngestedCharacterCard:
        return await self._sheets._ingest(payload, options=options)

    async def _finalize_import(
        self,
        target_world_id: str,
        ingested: IngestedCharacterCard,
        *,
        options: IngestOptions | None = None,
        lore_overrides: list[LoreOverride] | None = None,
    ) -> ImportResult:
        return await self._sheets._finalize_import(
            target_world_id, ingested, options=options, lore_overrides=lore_overrides
        )

    # ------------------------------------------------------------------ #
    # Search
    # ------------------------------------------------------------------ #

    async def search(
        self,
        query: str,
        world_id: str | None = None,
        scope: str = "all",
        campaign_id: CampaignId | None = None,
    ) -> list[Character]:
        q = (query or "").strip().lower()
        if not q:
            return []
        rows: list[LibraryEntity] = []
        if scope in {"world"} and world_id is not None:
            rows = await self.library.list_in_world(world_id, "character")
        elif scope == "library" or (scope == "all" and world_id is None and campaign_id is None):
            rows = await self.store.db.fetchall(
                "SELECT * FROM library_index WHERE kind = 'character' ORDER BY name"
            )
            rows = [_entity_from_row_dict(r) for r in rows]
        elif scope == "campaign" or campaign_id is not None:
            rows = await self.library.list_for_composition(campaign_id, "character")
        elif world_id is not None:
            rows = await self.library.list_in_world(world_id, "character")

        results: list[Character] = []
        for r in rows:
            character = character_from_entity(r) if isinstance(r, LibraryEntity) else r
            if _matches_query(character, q):
                results.append(character)
        return results

    # ------------------------------------------------------------------ #
    # Internal state plumbing
    # ------------------------------------------------------------------ #

    async def _load_state(
        self,
        asset_id: str,
        ref: CharacterRef,
        campaign_id: CampaignId,
        branch_id: str | None = None,
    ) -> CharacterState:
        branch = _branch_for(campaign_id, branch_id)
        row = await self.store.resolve_character_state(character_ref=ref, branch_id=branch)
        if row is None:
            return CharacterState(
                character_ref=ref,
                campaign_id=campaign_id,
                branch_id=branch,
            )
        return CharacterState(
            character_ref=row["character_ref"],
            campaign_id=row["campaign_id"],
            branch_id=row["branch_id"],
            location_ref=row.get("location_ref"),
            emotional_state=row.get("emotional_state") or "",
            physical_state=row.get("physical_state") or "",
            immediate_intent=row.get("immediate_intent") or "",
            knowledge_state=row.get("knowledge_state") or {},
            last_action=row.get("last_action"),
            last_screen_time_turn=row.get("last_screen_time_turn"),
            visible_to_pc=bool(row.get("visible_to_pc")),
            drift_score=float(row.get("drift_score") or 0.0),
            tier_pin=(ContextTier(row["tier_pin"]) if row.get("tier_pin") else None),
            current_scene_id=row.get("current_scene_id"),
            updated_at_turn=row.get("updated_at_turn"),
            appearances_since_last_drift_check=int(
                row.get("appearances_since_last_drift_check") or 0
            ),
        )

    async def _save_state(
        self,
        ref: CharacterRef,
        campaign_id: CampaignId,
        state: CharacterState,
        *,
        source: str,
        turn_id: str | None = None,
        record_in_delta_log: bool = True,
    ) -> None:
        branch = state.branch_id or _branch_for(campaign_id, None)
        after = {
            "character_ref": ref,
            "campaign_id": campaign_id,
            "branch_id": branch,
            "location_ref": state.location_ref,
            "emotional_state": state.emotional_state,
            "physical_state": state.physical_state,
            "immediate_intent": state.immediate_intent,
            "knowledge_state": state.knowledge_state or {},
            "last_action": state.last_action,
            "last_screen_time_turn": state.last_screen_time_turn,
            "visible_to_pc": bool(state.visible_to_pc),
            "drift_score": float(state.drift_score),
            "tier_pin": state.tier_pin.value if state.tier_pin else None,
            "current_scene_id": state.current_scene_id,
            "updated_at_turn": turn_id or state.updated_at_turn or now_iso(),
            "appearances_since_last_drift_check": int(state.appearances_since_last_drift_check),
        }

        if record_in_delta_log:
            delta = StateDelta(
                kind=DeltaKind.CHARACTER_STATE_UPDATE,
                target_scope=Scope.CAMPAIGN_SQLITE,
                target_id=ref,
                target_table="character_state",
                after=after,
                confidence=1.0,
                source=source,
            )
            await self.store.apply_delta(
                delta=delta,
                source=source,
                turn_id=turn_id,
                branch_id=branch,
                campaign_id=campaign_id,
            )
            return

        await self.store.db.execute(
            """
            INSERT INTO character_state (
              character_ref, campaign_id, branch_id, location_ref,
              emotional_state, physical_state, immediate_intent,
              knowledge_state, last_action, last_screen_time_turn,
              visible_to_pc, drift_score, tier_pin, current_scene_id,
              updated_at_turn, appearances_since_last_drift_check
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(character_ref, branch_id) DO UPDATE SET
              campaign_id = excluded.campaign_id,
              location_ref = excluded.location_ref,
              emotional_state = excluded.emotional_state,
              physical_state = excluded.physical_state,
              immediate_intent = excluded.immediate_intent,
              knowledge_state = excluded.knowledge_state,
              last_action = excluded.last_action,
              last_screen_time_turn = excluded.last_screen_time_turn,
              visible_to_pc = excluded.visible_to_pc,
              drift_score = excluded.drift_score,
              tier_pin = excluded.tier_pin,
              current_scene_id = excluded.current_scene_id,
              updated_at_turn = excluded.updated_at_turn,
              appearances_since_last_drift_check = excluded.appearances_since_last_drift_check
            """,
            (
                after["character_ref"],
                after["campaign_id"],
                after["branch_id"],
                after["location_ref"],
                after["emotional_state"],
                after["physical_state"],
                after["immediate_intent"],
                json.dumps(after["knowledge_state"], default=str),
                after["last_action"],
                after["last_screen_time_turn"],
                1 if after["visible_to_pc"] else 0,
                after["drift_score"],
                after["tier_pin"],
                after["current_scene_id"],
                after["updated_at_turn"],
                after["appearances_since_last_drift_check"],
            ),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Re-export for backward compatibility
_character_from_entity = character_from_entity
_character_from_frontmatter = character_from_frontmatter
_frontmatter_from_payload = frontmatter_from_payload


class _CharacterRefView:
    def __init__(self, is_emergent: bool, world_id: str | None, asset_id: str) -> None:
        self.is_emergent = is_emergent
        self.world_id = world_id
        self.asset_id = asset_id


def _parse_character_ref(ref: str) -> _CharacterRefView:
    if not ref:
        raise CharactersError("empty character_ref")
    if ref.startswith("campaign:emergent/"):
        _, _, rest = ref.partition("campaign:emergent/")
        parts = rest.strip("/").split("/")
        if parts[0] == "character" and len(parts) == 2:
            return _CharacterRefView(True, None, parts[1])
        if len(parts) == 1:
            return _CharacterRefView(True, None, parts[0])
    if ref.startswith("emergent/"):
        parts = ref.split("/")
        return _CharacterRefView(True, None, parts[-1])
    if ref.startswith("library:"):
        _, _, path = ref.partition("library:")
        parts = path.strip("/").split("/")
        if len(parts) >= 4 and parts[0] == "worlds" and parts[2] in {"characters", "character"}:
            return _CharacterRefView(False, parts[1], parts[3])
    parts = ref.strip("/").split("/")
    if len(parts) >= 4 and parts[0] == "worlds" and parts[2] in {"characters", "character"}:
        return _CharacterRefView(False, parts[1], parts[3])
    raise CharactersError(f"unrecognized character_ref {ref!r}")


def _asset_id_for_ref(ref: CharacterRef) -> str:
    return _parse_character_ref(ref).asset_id


def _library_id_from_ref(ref: CharacterRef) -> str:
    view = _parse_character_ref(ref)
    if view.is_emergent or view.world_id is None:
        raise CharactersError(f"cannot derive library_id from emergent ref {ref!r}")
    return make_library_id(view.world_id, "character", view.asset_id)


def _ref_from_resolved(resolved: ResolvedCharacter) -> CharacterRef:
    char = resolved.character
    if char.world_id:
        return f"library:worlds/{char.world_id}/characters/{char.id}"
    return f"campaign:emergent/character/{char.id}"


def _entity_from_row_dict(row: Any) -> LibraryEntity:
    if isinstance(row, LibraryEntity):
        return row
    raw = dict(row)
    fm = raw.get("frontmatter")
    if isinstance(fm, str):
        try:
            fm = json.loads(fm) if fm else {}
        except json.JSONDecodeError:
            fm = {}
    return LibraryEntity(
        id=raw.get("id") or "",
        world_id=raw.get("world_id"),
        kind="character",
        asset_id=raw.get("asset_id") or "",
        name=raw.get("name") or raw.get("asset_id") or "",
        path=raw.get("path") or "",
        frontmatter=fm or {},
        body=raw.get("body") or "",
        body_compressed=raw.get("body_compressed"),
        tags=_maybe_list(raw.get("tags")),
        keywords=_maybe_list(raw.get("keywords")),
        content_hash=raw.get("content_hash") or "",
        version=int(raw.get("version") or 0),
    )


def _maybe_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _matches_query(character: Character, q: str) -> bool:
    haystack = " ".join(
        [character.name, *(character.aliases or []), *(character.tags or [])]
    ).lower()
    return q in haystack


def _passes_filter(entity: LibraryEntity, filter: CharacterFilter | None) -> bool:
    if filter is None:
        return True
    if filter.world_ids and entity.world_id not in filter.world_ids:
        return False
    if filter.tags and not (set(entity.tags or []) & set(filter.tags)):
        return False
    if filter.name_contains and filter.name_contains.lower() not in entity.name.lower():
        return False
    if filter.roles:
        role = entity.frontmatter.get("role") if entity.frontmatter else None
        try:
            r = CharacterRole(role or "major_npc")
        except ValueError:
            r = CharacterRole.MAJOR_NPC
        if r not in filter.roles:
            return False
    return True


def _passes_resolved_filter(resolved: ResolvedCharacter, filter: CharacterFilter) -> bool:
    c = resolved.character
    if filter.roles and c.role not in filter.roles:
        return False
    if filter.tags and not (set(c.tags) & set(filter.tags)):
        return False
    if filter.world_ids and c.world_id not in filter.world_ids:
        return False
    return not (filter.name_contains and filter.name_contains.lower() not in c.name.lower())


def _relationship_state_from_json(value: Any) -> RelationshipState:
    if not value:
        return RelationshipState()
    try:
        data = value if isinstance(value, dict) else json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return RelationshipState()
    return RelationshipState.model_validate(data) if isinstance(data, dict) else RelationshipState()


def _relationship_history_from_json(value: Any) -> list[dict]:
    if not value:
        return []
    try:
        data = value if isinstance(value, list) else json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _relationship_row_to_dict(row: Any) -> dict:
    types = row["types"]
    try:
        types = json.loads(types) if types else []
    except (TypeError, json.JSONDecodeError):
        types = []
    state = _relationship_state_from_json(row["state"]).model_dump()
    try:
        history_raw = row["history"]
    except (IndexError, KeyError):
        history_raw = None
    return {
        "id": row["id"],
        "campaign_id": row["campaign_id"],
        "branch_id": row["branch_id"],
        "from_ref": row["from_character_ref"],
        "to_ref": row["to_character_ref"],
        "types": types,
        "state": state,
        "updated_at_turn": row["updated_at_turn"],
        "history": _relationship_history_from_json(history_raw),
    }


def _parse_iso_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


_TIER_RANK: dict[ContextTier | None, int] = {
    None: -1,
    ContextTier.ARCHIVE: 0,
    ContextTier.BACKGROUND: 1,
    ContextTier.SPOTLIGHT: 2,
    ContextTier.LOCK_IN: 3,
}


def _tier_rank(tier: ContextTier | None) -> int:
    return _TIER_RANK.get(tier, -1)


def _turns_since(last_seen: str | None, recent_turn_ids: list[str]) -> int | None:
    if not recent_turn_ids:
        return None
    if last_seen is None:
        return len(recent_turn_ids)
    try:
        idx = recent_turn_ids.index(last_seen)
    except ValueError:
        return len(recent_turn_ids)
    return len(recent_turn_ids) - 1 - idx


def _mentions_character(text: str, character: Character) -> bool:
    needles = [character.name, *character.aliases]
    haystack = text.lower()
    for needle in needles:
        n = needle.strip().lower()
        if not n:
            continue
        i = 0
        while True:
            i = haystack.find(n, i)
            if i < 0:
                break
            before_ok = i == 0 or not haystack[i - 1].isalnum()
            end = i + len(n)
            after_ok = end == len(haystack) or not haystack[end].isalnum()
            if before_ok and after_ok:
                return True
            i = end
    return False
