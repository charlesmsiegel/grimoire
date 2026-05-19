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
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from grimoire.library import LibraryService
from grimoire.mechanics.service import MechanicsService
from grimoire.state_store import StateStore
from grimoire.state_store.indexers import make_library_id
from grimoire.state_store.paths import library_path
from grimoire.types.characters import (
    CapsuleDraft,
    Character,
    CharacterData,
    CharacterFilter,
    CharacterImage,
    CharacterImageKind,
    CharacterRole,
    DriftReport,
    ImagePromptTemplate,
    ImportResult,
    IngestedCharacterCard,
    IngestOptions,
    PCEntry,
    PromotionProposal,
    RelationshipEvent,
    RelationshipState,
    ResolvedCharacter,
    StructuralRelationship,
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
    CallableDriftChecker,
    DriftChecker,
    DriftEvent,
    DriftEventSink,
    DriftInput,
    HeuristicDriftChecker,
    LLMCallable,
)
from .errors import (
    CharacterNotFoundError,
    CharactersError,
    PromotionError,
)
from .imports import parse_plaintext
from .ingest import LLMEnrichCallable, enrich_with_llm, ingest_character_card_v2
from .protocols import SheetMigrator
from .views import (
    render_capsule,
    render_compressed,
    render_full,
    render_voice_only,
)

_log = logging.getLogger(__name__)

PostFetcher = Callable[[str], Awaitable[list[Post]]]

LLMCapsuleDrafter = Callable[[CharacterData], Awaitable[CapsuleDraft]]
"""Async hook used by :meth:`CharactersService.create_emergent`.

When the caller injects a ``LLMCapsuleDrafter`` and the emergent payload
is "sparse" (empty ``description`` and ``tags``), the service awaits the
drafter and writes the returned :class:`CapsuleDraft` back via
``update_emergent`` (spec 2026-05-17 §10).
"""

LLMVoiceAnchorDrafter = Callable[[Character, list[Post]], Awaitable[VoiceAnchor]]
"""Async hook used by :meth:`CharactersService.draft_voice_anchor`.

Receives the resolved character and the recent posts that mention them
(filtered to the configured ``sample_window``) and returns a draft
:class:`VoiceAnchor` for the caller to review (spec 2026-05-17 §11).
"""


def _branch_for(campaign_id: str, branch_id: str | None) -> str:
    return branch_id or f"{campaign_id}:main"


class CharactersService:
    """Spec 08 implementation.

    Construct with a :class:`LibraryService` (which owns character files) and
    a :class:`MechanicsService` (for capability surfacing). Per-campaign
    state (drift scores, tier pins, relationships, active PC) lives in
    SQLite; the service writes through the State Store.

    ``post_fetcher`` is the optional hook the drift detector uses to fetch
    recent posts for a character. The Scene Manager exposes
    ``get_posts(scene_id)`` once task #17 is wired; tests inject a stub.

    ``drift_checker`` is the pluggable drift evaluator. Defaults to the
    cheap heuristic implementation; production injects an LLM-backed one.
    """

    def __init__(
        self,
        library: LibraryService,
        mechanics: MechanicsService,
        *,
        config: CharactersConfig | None = None,
        # Hook-shaped dependencies stay as ctor kwargs; only numeric /
        # boolean / string knobs live on :class:`CharactersConfig`.
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
        self._post_fetcher = post_fetcher
        self._drift_checker = drift_checker or HeuristicDriftChecker(
            drift_threshold=self._config.drift.threshold
        )
        self._drift_event_sink = drift_event_sink
        self._ingest_llm = ingest_llm
        self._auto_capsule_llm = auto_capsule_llm
        self._voice_anchor_llm = voice_anchor_llm
        self._sheet_migrator = sheet_migrator
        # Per-PC current scene cache; mirrors SceneManager._pc_current_scene
        # but keyed by ``(campaign_id, character_ref)``. The authoritative
        # source is the active-scene id stored on character_state.
        self._active_pc: dict[str, CharacterRef] = {}
        # Compressed-view LRU (spec 2026-05-17 §5). Key is
        # ``(ref, campaign_id, view, seed)``; value is the rendered string.
        # We rely solely on the in-process invalidation hooks below — the
        # Library exposes mtime but consulting it would require a DB
        # roundtrip per cache check, defeating the cache. All mutation
        # paths through this service call ``_view_cache_invalidate`` (or
        # ``clear`` for cross-campaign library writes), so staleness is
        # impossible as long as Library writes go through ``self.library``.
        self._view_cache: OrderedDict[tuple[str, str, str, int | None], str] = OrderedDict()

    # ------------------------------------------------------------------ #
    # CRUD (delegated to Library)
    # ------------------------------------------------------------------ #

    async def list_in_world(self, world_id: str) -> list[Character]:
        rows = await self.library.list_in_world(world_id, "character")
        return [_character_from_entity(r) for r in rows]

    async def get(self, world_id: str, character_id: str) -> Character:
        ent = await self.library.get_entity(world_id, "character", character_id)
        return _character_from_entity(ent)

    async def create(self, world_id: str, payload: CharacterData) -> Character:
        fm = _frontmatter_from_payload(payload)
        ent = await self.library.create_entity(
            world_id, "character", payload.id, fm, payload.body, source="characters:create"
        )
        return _character_from_entity(ent)

    async def update(self, world_id: str, character_id: str, patch: dict) -> Character:
        body = patch.pop("body", None)
        ent = await self.library.update_entity(
            world_id,
            "character",
            character_id,
            frontmatter_patch=patch or None,
            body=body,
            source="characters:update",
        )
        # Pragmatic compromise (spec 2026-05-17 §5): a library write affects
        # every campaign whose composition pulls in this world. We don't keep
        # a campaign→world index here, so clear the entire cache instead of
        # walking bindings. Library writes are rare relative to view reads.
        self._view_cache_invalidate()
        return _character_from_entity(ent)

    async def delete(self, world_id: str, character_id: str) -> None:
        await self.library.delete_entity(
            world_id, "character", character_id, source="characters:delete"
        )
        # Same compromise as ``update`` above — clear the whole cache.
        self._view_cache_invalidate()

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
        fm = _frontmatter_from_payload(payload)
        await self.store.write_emergent(
            campaign_id=campaign_id,
            kind="character",
            entity_id=payload.id,
            frontmatter=fm,
            body=payload.body,
            source=source,
        )
        # Spec 2026-05-17 §10: when the payload is sparse (no description
        # and no tags) and an auto-capsule drafter is wired, ask the LLM to
        # draft a summary line + tags and write the result back through
        # ``update_emergent`` so the standard persistence path handles it.
        # We await synchronously (option (a) in the spec) — the alternative
        # ``asyncio.create_task`` background path makes error handling and
        # ordering against subsequent reads harder to reason about; the
        # awaited form keeps the contract obvious for v1.
        if self._auto_capsule_llm is not None and _is_sparse_payload(payload):
            try:
                draft = await self._auto_capsule_llm(payload)
            except Exception:  # pragma: no cover - drafter failures shouldn't block create
                draft = None
            if draft is not None:
                patch = _capsule_draft_to_patch(draft)
                if patch:
                    await self.update_emergent(
                        campaign_id,
                        payload.id,
                        patch,
                        source="characters:auto-capsule",
                    )
        return f"campaign:emergent/character/{payload.id}"

    async def update_emergent(
        self,
        campaign_id: CampaignId,
        character_id: str,
        patch: dict,
        *,
        source: str = "characters:emergent-update",
    ) -> Character:
        existing = await self.store.get_emergent(campaign_id, "character", character_id)
        if existing is None:
            raise CharacterNotFoundError(
                f"no emergent character {character_id!r} in campaign {campaign_id!r}"
            )
        fm = dict(existing.get("frontmatter") or {})
        body = patch.pop("body", existing.get("body") or "")
        fm.update(patch or {})
        fm["id"] = character_id
        await self.store.write_emergent(
            campaign_id=campaign_id,
            kind="character",
            entity_id=character_id,
            frontmatter=fm,
            body=body,
            source=source,
        )
        emergent_ref = f"campaign:emergent/character/{character_id}"
        self._view_cache_invalidate(ref=emergent_ref, campaign_id=campaign_id)
        return _character_from_frontmatter(fm, body, world_id=None)

    async def delete_emergent(self, campaign_id: CampaignId, character_id: str) -> None:
        from grimoire.state_store.paths import emergent_path

        target = emergent_path(self.store.data_root, campaign_id, "character", character_id)
        if not target.exists():
            raise CharacterNotFoundError(
                f"no emergent character {character_id!r} in campaign {campaign_id!r}"
            )
        target.unlink()
        emergent_ref = f"campaign:emergent/character/{character_id}"
        self._view_cache_invalidate(ref=emergent_ref, campaign_id=campaign_id)

    async def upsert_override(
        self,
        campaign_id: CampaignId,
        character_ref: str,
        patch: dict,
        *,
        source: str = "characters:override",
    ) -> None:
        """Persist a campaign-local override against a library character.

        ``character_ref`` must be a ``library:worlds/<s>/characters/<id>``
        reference. The override file lives under the campaign's
        ``overrides/`` tree.
        """
        library_id = _library_id_from_ref(character_ref)
        await self.store.write_override(
            campaign_id=campaign_id,
            library_id=library_id,
            patch=patch,
            source=source,
        )
        self._view_cache_invalidate(ref=character_ref, campaign_id=campaign_id)

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
            character = _character_from_frontmatter(
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
            character = _character_from_frontmatter(
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
        """Resolved characters reachable through the campaign composition + emergents."""
        out: list[ResolvedCharacter] = []
        # Library / composition path
        composed = await self.library.list_for_composition(campaign_id, "character")
        for ent in composed:
            if not _passes_filter(ent, filter):
                continue
            ref = f"library:worlds/{ent.world_id}/characters/{ent.asset_id}"
            try:
                out.append(await self.resolve(ref, campaign_id))
            except CharacterNotFoundError:
                continue
        # Emergent path
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
        return [_character_from_entity(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Compressed views
    # ------------------------------------------------------------------ #

    async def get_full_card(
        self, ref: str, campaign_id: CampaignId, *, seed: int | None = None
    ) -> str:
        cached = self._view_cache_get(ref, campaign_id, "full", seed)
        if cached is not None:
            return cached
        resolved = await self.resolve(ref, campaign_id)
        rendered = render_full(resolved.character, seed=seed)
        self._view_cache_set(ref, campaign_id, "full", seed, rendered)
        return rendered

    async def get_compressed_card(
        self, ref: str, campaign_id: CampaignId, *, seed: int | None = None
    ) -> str:
        cached = self._view_cache_get(ref, campaign_id, "compressed", seed)
        if cached is not None:
            return cached
        resolved = await self.resolve(ref, campaign_id)
        rendered = render_compressed(resolved.character, seed=seed)
        self._view_cache_set(ref, campaign_id, "compressed", seed, rendered)
        return rendered

    async def get_voice_only(
        self, ref: str, campaign_id: CampaignId, *, seed: int | None = None
    ) -> str:
        cached = self._view_cache_get(ref, campaign_id, "voice_only", seed)
        if cached is not None:
            return cached
        resolved = await self.resolve(ref, campaign_id)
        rendered = render_voice_only(resolved.character, seed=seed)
        self._view_cache_set(ref, campaign_id, "voice_only", seed, rendered)
        return rendered

    async def get_capsule(
        self, ref: str, campaign_id: CampaignId, *, seed: int | None = None
    ) -> str:
        cached = self._view_cache_get(ref, campaign_id, "capsule", seed)
        if cached is not None:
            return cached
        resolved = await self.resolve(ref, campaign_id)
        rendered = render_capsule(resolved.character, seed=seed)
        self._view_cache_set(ref, campaign_id, "capsule", seed, rendered)
        return rendered

    # ------------------------------------------------------------------ #
    # View cache helpers (spec 2026-05-17 §5)
    # ------------------------------------------------------------------ #

    def _view_cache_get(
        self,
        ref: CharacterRef,
        campaign_id: CampaignId,
        view: str,
        seed: int | None,
    ) -> str | None:
        key = (ref, campaign_id, view, seed)
        try:
            value = self._view_cache[key]
        except KeyError:
            return None
        self._view_cache.move_to_end(key)  # LRU touch
        return value

    def _view_cache_set(
        self,
        ref: CharacterRef,
        campaign_id: CampaignId,
        view: str,
        seed: int | None,
        value: str,
    ) -> None:
        key = (ref, campaign_id, view, seed)
        self._view_cache[key] = value
        self._view_cache.move_to_end(key)
        while len(self._view_cache) > self._config.cache.max_size:
            self._view_cache.popitem(last=False)

    def _view_cache_invalidate(
        self,
        ref: CharacterRef | None = None,
        campaign_id: CampaignId | None = None,
    ) -> None:
        """Drop all cache entries matching ``(ref, campaign_id)``.

        ``view`` and ``seed`` are always cleared together — invalidation is
        coarse on purpose. ``None`` for either field means "any".
        """
        if ref is None and campaign_id is None:
            self._view_cache.clear()
            return
        doomed = [
            key
            for key in self._view_cache
            if (ref is None or key[0] == ref) and (campaign_id is None or key[1] == campaign_id)
        ]
        for key in doomed:
            del self._view_cache[key]

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
        """Per-character tier recommendation for a scene.

        Spec 08 §Tier management lists four rules. They compose, with the
        later rules in this list overriding earlier ones:

        * Inactivity → demote (BACKGROUND after `tier_demote_to_background_after_turns`
          turns of silence; ARCHIVE after `tier_demote_to_archive_after_turns`).
        * Mentioned in recent posts (by name or alias) → at least BACKGROUND.
        * Open commitments to a PC (caller-provided) → at least BACKGROUND.
        * Present in the scene → SPOTLIGHT.
        * User `tier_pin` → forced tier (wins over all the above).
        """
        target_campaign = campaign_id or scene.campaign_id
        out: dict[CharacterRef, ContextTier] = {}

        present = set(scene.present_character_refs)

        # Inactivity demotion + mention upgrade both need the full set of
        # campaign characters. Skip the lookup if there are no posts to
        # measure recency or matches against; otherwise fetch once and
        # iterate twice.
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

            # Mentioned in recent posts → at least BACKGROUND. Already-demoted
            # entries are upgraded back to BACKGROUND because being talked
            # about is a stronger signal than time-since-screen.
            for resolved in resolved_list:
                ref = _ref_from_resolved(resolved)
                if ref in present:
                    continue
                if _mentions_character(joined_body, resolved.character) and _tier_rank(
                    out.get(ref)
                ) < _tier_rank(ContextTier.BACKGROUND):
                    out[ref] = ContextTier.BACKGROUND

        # Open commitments to a PC → at least BACKGROUND. Caller passes the
        # set; we don't reach into Continuity here.
        if commitments_targeting_pcs:
            for ref in commitments_targeting_pcs:
                if ref in present:
                    continue
                if _tier_rank(out.get(ref)) < _tier_rank(ContextTier.BACKGROUND):
                    out[ref] = ContextTier.BACKGROUND

        # Presence overrides any demotion.
        for ref in scene.present_character_refs:
            out[ref] = ContextTier.SPOTLIGHT

        # User pins win over heuristics. Batch-fetch all pins for the
        # campaign in one query (instead of one query per character).
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
        # Tier pins are a UI choice and must survive undo_turn replay, so we
        # skip the delta log here (spec characters-remaining §8).
        await self._save_state(
            ref, campaign_id, state, source="characters:tier-pin", record_in_delta_log=False
        )
        # tier_pin is rendered into the full card via current_state — bust.
        self._view_cache_invalidate(ref=ref, campaign_id=campaign_id)

    # ------------------------------------------------------------------ #
    # Drift
    # ------------------------------------------------------------------ #

    async def check_drift(
        self,
        ref: CharacterRef,
        campaign_id: CampaignId,
        *,
        window: int = 10,
        recent_posts: list[Post] | None = None,
    ) -> DriftReport:
        """Compute a drift score for ``ref`` over the last ``window`` posts.

        ``recent_posts`` may be supplied directly; otherwise the service
        falls back to the injected ``post_fetcher``. With no fetcher and no
        posts, drift is 0.

        When the computed ``drift_score`` meets or exceeds
        ``drift_threshold`` and a ``drift_event_sink`` is configured, a
        :class:`DriftEvent` is dispatched (spec characters-remaining §4).
        Sink failures are logged and swallowed.
        """
        resolved = await self.resolve(ref, campaign_id)
        posts: list[Post] = recent_posts or []
        if not posts and self._post_fetcher is not None:
            scene_id = resolved.current_state.current_scene_id
            if scene_id:
                fetched = await self._post_fetcher(scene_id)
                posts = list(fetched[-window:])
        report = await self._drift_checker.evaluate(
            DriftInput(character=resolved.character, recent_posts=posts, window=window)
        )
        # Persist the drift score on character_state.
        state = resolved.current_state
        state.drift_score = report.drift_score
        await self._save_state(ref, campaign_id, state, source="characters:drift-check")

        if (
            self._drift_event_sink is not None
            and report.drift_score >= self._config.drift.threshold
        ):
            event = DriftEvent(
                character_ref=ref,
                campaign_id=campaign_id,
                drift_score=report.drift_score,
                threshold=self._config.drift.threshold,
                report=report,
            )
            try:
                await self._drift_event_sink(event)
            except Exception:  # sink must not block extraction
                _log.warning(
                    "drift_event_sink raised for %s in %s", ref, campaign_id, exc_info=True
                )
        return report

    async def maybe_check_drift(
        self,
        ref: CharacterRef,
        campaign_id: CampaignId,
        *,
        recent_posts: list[Post] | None = None,
        force: bool = False,
    ) -> DriftReport | None:
        """Run :meth:`check_drift` on the configured appearance cadence.

        Spec characters-remaining §3: the Orchestrator's post-turn fan-out
        calls this for each present character. The counter on
        ``CharacterState.appearances_since_last_drift_check`` (bumped by
        :meth:`mark_screen_time`) gates the actual drift check; the counter
        resets to zero through ``_save_state`` so the reset itself goes into
        the delta log. Returns ``None`` when the cadence threshold has not
        been reached and ``force`` is false.
        """
        state = await self._load_state(_asset_id_for_ref(ref), ref, campaign_id)
        threshold = self._config.drift.check_every_n_appearances
        if not force and threshold > 0 and state.appearances_since_last_drift_check < threshold:
            return None

        report = await self.check_drift(ref, campaign_id, recent_posts=recent_posts)
        # Reset the counter through the standard persist path so undo_turn
        # can reverse it. Re-load to capture drift_score written by
        # check_drift, then zero the counter on top.
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
        """Return a corrective voice snippet for the next prompt.

        Spec characters-remaining §4 — when the cached drift score on
        ``character_state`` is at or above ``drift_threshold``, return a
        non-empty voice-anchor reminder; the Context Builder prepends the
        result to the next prompt featuring ``ref`` so the model gets
        explicit corrective guidance. Below threshold returns an empty
        string so callers skip the injection.

        Pass ``inject_corrective_context=False`` to short-circuit the lookup
        entirely (returns ``""``) — useful for callers that have an
        out-of-band reason to suppress the injection on a given turn (e.g.
        a regenerate flow that's already including a stronger guidance
        block).

        Context Builder integration is wired separately — this method is
        the source of truth for the snippet content.
        """
        if not inject_corrective_context:
            return ""
        state = await self._load_state(_asset_id_for_ref(ref), ref, campaign_id)
        if state.drift_score < self._config.drift.threshold:
            return ""
        resolved = await self.resolve(ref, campaign_id)
        # Use the heuristic checker's renderer; it works on any character.
        from .drift import _corrective_text

        return _corrective_text(resolved.character, [])

    def set_drift_checker(self, checker: DriftChecker | LLMCallable) -> None:
        """Swap in a different drift checker after construction."""
        if callable(checker) and not hasattr(checker, "evaluate"):
            self._drift_checker = CallableDriftChecker(checker)  # type: ignore[arg-type]
        else:
            self._drift_checker = checker  # type: ignore[assignment]

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
        """Ask the configured LLM to propose a :class:`VoiceAnchor`.

        Pulls the character's recent dialogue from their current scene via
        the injected ``post_fetcher`` (same pattern as :meth:`check_drift`),
        filters to posts that mention the character by name or alias, and
        hands the trimmed window to the drafter. The returned anchor is a
        *proposal* — the caller routes it through ``update_emergent`` or
        ``upsert_override`` to accept it (spec 2026-05-17 §11).

        Raises :class:`CharactersError` when no ``voice_anchor_llm`` was
        wired at construction.
        """
        if self._voice_anchor_llm is None:
            raise CharactersError("draft_voice_anchor requires a voice_anchor_llm to be configured")
        resolved = await self.resolve(character_ref, campaign_id)
        posts: list[Post] = []
        if self._post_fetcher is not None:
            scene_id = resolved.current_state.current_scene_id
            if scene_id:
                fetched = await self._post_fetcher(scene_id)
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
        self._view_cache_invalidate(ref=ref, campaign_id=campaign_id)

    async def mark_screen_time(
        self, ref: CharacterRef, campaign_id: CampaignId, turn_id: str
    ) -> None:
        state = await self._load_state(_asset_id_for_ref(ref), ref, campaign_id)
        state.last_screen_time_turn = turn_id
        # Drift-cadence counter (spec characters-remaining §3); reset by
        # ``maybe_check_drift`` once it actually runs the checker.
        state.appearances_since_last_drift_check += 1
        await self._save_state(
            ref, campaign_id, state, source="characters:screen-time", turn_id=turn_id
        )

    async def get_state(self, ref: CharacterRef, campaign_id: CampaignId) -> CharacterState:
        return await self._load_state(_asset_id_for_ref(ref), ref, campaign_id)

    # ------------------------------------------------------------------ #
    # PCs
    # ------------------------------------------------------------------ #

    def _seed_active_pc_from_rows(
        self, campaign_id: CampaignId, rows: list[dict]
    ) -> CharacterRef | None:
        """Return the cached active PC ref, hydrating from ``rows`` if needed.

        Picks the first row whose ``active`` flag is truthy (rows are returned
        by the store in ``added_at`` order); falls back to the first row when
        no row carries an active bit. The choice is cached so subsequent calls
        within the process are stable, matching ``set_active_pc`` semantics.
        Returns ``None`` only when ``rows`` is empty.
        """
        cached = self._active_pc.get(campaign_id)
        if cached is not None:
            return cached
        if not rows:
            return None
        chosen = next(
            (row["character_ref"] for row in rows if bool(row["active"])),
            rows[0]["character_ref"],
        )
        self._active_pc[campaign_id] = chosen
        return chosen

    async def list_pcs(self, campaign_id: CampaignId) -> list[PCEntry]:
        rows = await self.store.list_pcs(campaign_id)
        active_ref = self._seed_active_pc_from_rows(campaign_id, rows)
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
                # State load is best-effort; falling back to bare PC
                # entry keeps the switcher functional when the per-PC
                # state row hasn't been created yet (fresh campaign).
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
        if campaign_id not in self._active_pc:
            self._active_pc[campaign_id] = character_ref
        return PCEntry(
            character_ref=character_ref,
            name=name,
            owner=owner,
            active=self._active_pc.get(campaign_id) == character_ref,
        )

    async def remove_pc(self, campaign_id: CampaignId, character_ref: CharacterRef) -> None:
        await self.store.remove_pc(campaign_id=campaign_id, character_ref=character_ref)
        if self._active_pc.get(campaign_id) == character_ref:
            self._active_pc.pop(campaign_id, None)

    async def set_active_pc(self, campaign_id: CampaignId, character_ref: CharacterRef) -> None:
        pcs = await self.store.list_pcs(campaign_id)
        refs = {p["character_ref"] for p in pcs}
        if character_ref not in refs:
            raise CharactersError(f"{character_ref!r} is not a PC in campaign {campaign_id!r}")
        # Persist to DB so the choice survives restart. The in-memory cache
        # stays as a fast-path for list_pcs/active_pc within the same process.
        await self.store.set_active_pc(campaign_id=campaign_id, character_ref=character_ref)
        self._active_pc[campaign_id] = character_ref

    async def active_pc(self, campaign_id: CampaignId) -> CharacterRef | None:
        """Return the currently active PC for ``campaign_id`` (None if no PCs)."""
        if campaign_id in self._active_pc:
            return self._active_pc[campaign_id]
        pcs = await self.store.list_pcs(campaign_id)
        return self._seed_active_pc_from_rows(campaign_id, pcs)

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
        """True when there is exactly one present PC.

        When 2+ PCs are present in the same scene, the Orchestrator should
        wait for an explicit advance trigger (spec 08 §PC role and multi-PC).
        Single-PC scenes get the normal auto-response flow.
        """
        present = await self.present_pcs_in_scene(scene, campaign_id)
        return len(present) <= 1

    async def pending_pc_inputs_since_last_advance(
        self,
        scene: Scene,
        posts: list[Post],
    ) -> list[Post]:
        """Return PC-authored posts after the scene's ``last_advance_at_post``."""
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
        """Apply ``delta`` to the relationship between ``from_ref`` and ``to_ref``.

        Numeric fields (``affection``, ``trust``, ``dominance``,
        ``intimacy``) are incremented; other fields are set. Creates the row
        if it doesn't exist.

        When the caller supplies a non-empty ``summary``, a
        :class:`RelationshipEvent` is appended to the relationship's
        ``history`` log (with optional ``in_post`` and the applied
        ``delta``) so a "relationship timeline" view can reconstruct what
        drove each shift. Calls without a summary leave the log alone,
        avoiding empty/noise entries for background tooling that only
        nudges the rolling state.
        """
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
                # Merge new types in.
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
        """Return the chronological :class:`RelationshipEvent` log.

        Empty list if no relationship exists (or it has no recorded
        events). Events are returned in the order they were appended,
        which matches the wall-clock order of the originating
        ``update_relationship`` calls.
        """
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
    # Promotion
    # ------------------------------------------------------------------ #

    async def propose_promotion(
        self,
        campaign_id: CampaignId,
        character_id: str,
        target_world_id: str,
        *,
        target_character_id: str | None = None,
    ) -> PromotionProposal:
        """Render a preview of the library write without executing it.

        Caller shows the proposal to the user; once confirmed, the same
        args plus ``confirm=True`` go to :meth:`promote_to_library`. See
        spec ``2026-05-17-characters-remaining-design`` §9.

        Warnings call out conditions the UI should highlight before commit:
        id collision in the target world, missing voice anchor, missing
        description. Empty warnings = safe to commit unattended.
        """
        emergent = await self.store.get_emergent(campaign_id, "character", character_id)
        if emergent is None:
            raise PromotionError(
                f"no emergent character {character_id!r} in campaign {campaign_id!r}"
            )
        target_id = target_character_id or character_id
        fm = dict(emergent.get("frontmatter") or {})
        fm["id"] = target_id
        body = emergent.get("body") or ""
        library_id = make_library_id(target_world_id, "character", target_id)
        target_path = library_path(self.store.data_root, library_id)

        warnings: list[str] = []
        # Id collision: a real character already lives at this target slot.
        try:
            await self.library.get_entity(target_world_id, "character", target_id)
        except Exception:
            pass
        else:
            warnings.append(
                f"target world {target_world_id!r} already has a character "
                f"with id {target_id!r}; promotion would overwrite it"
            )
        # Voice anchor sanity — at minimum a summary or at least one sample.
        voice = fm.get("voice") or {}
        if not isinstance(voice, dict) or not (
            str(voice.get("summary") or "").strip() or (voice.get("samples") or [])
        ):
            warnings.append("character has no voice anchor (summary or samples)")
        # Description sanity.
        if not str(fm.get("description") or "").strip():
            warnings.append("character has no description")

        return PromotionProposal(
            campaign_id=campaign_id,
            character_id=character_id,
            target_world_id=target_world_id,
            target_library_id=library_id,
            target_path=str(target_path),
            frontmatter=fm,
            body=body,
            warnings=warnings,
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
        """Promote an emergent character into the library.

        Two flows (spec ``2026-05-17-characters-remaining-design`` §9):

        * ``confirm=False`` (default): generate a proposal and raise
          :class:`PromotionError` if any warnings would fire — the
          two-step UI flow forces the caller to acknowledge them.
        * ``confirm=True``: commit the write. Programmatic / single-shot
          callers (tests, batch tools) opt in here.

        Pass ``proposal`` to reuse a previously generated preview verbatim;
        otherwise a fresh proposal is rendered from the current emergent
        data.

        When ``self._sheet_migrator`` is wired (§13), ``migrate_sheet`` is
        invoked after the markdown lands. Failures bubble up as
        :class:`PromotionError` — silent swallow would leave the new
        library character without the mechanics the user expected.

        Wraps the store's ``write_library_file`` rather than going through
        ``LibraryService.promote_to_library`` because that path explicitly
        excludes ``character``. Returns the new library path.
        """
        if proposal is None:
            proposal = await self.propose_promotion(
                campaign_id,
                character_id,
                target_world_id,
                target_character_id=target_character_id,
            )
        if not confirm:
            if proposal.warnings:
                raise PromotionError(
                    "promotion has unresolved warnings; resolve or call with "
                    f"confirm=True: {proposal.warnings}"
                )
            raise PromotionError(
                "promote_to_library requires confirm=True; use propose_promotion "
                "first to preview the write"
            )

        result = await self.store.write_library_file(
            library_id=proposal.target_library_id,
            frontmatter=dict(proposal.frontmatter),
            body=proposal.body,
            source=source,
            campaign_id=campaign_id,
        )

        if self._sheet_migrator is not None:
            emergent_ref = f"campaign:emergent/character/{character_id}"
            try:
                await self._sheet_migrator.migrate_sheet(
                    campaign_id,
                    emergent_ref,
                    proposal.target_library_id,
                )
            except Exception as exc:
                raise PromotionError(f"sheet migration failed for {character_id!r}: {exc}") from exc

        if delete_emergent:
            from grimoire.state_store.paths import emergent_path

            target = emergent_path(self.store.data_root, campaign_id, "character", character_id)
            if target.exists():
                target.unlink()
        return str(result.path)

    # ------------------------------------------------------------------ #
    # Imports
    # ------------------------------------------------------------------ #

    async def import_sillytavern(
        self,
        card: bytes,
        target_world_id: str,
        *,
        options: IngestOptions | None = None,
    ) -> ImportResult:
        """Ingest a Character Card V2/V3 payload into ``target_world_id``.

        Accepts JSON bytes (the canonical envelope or just the data
        object) as well as PNG bytes with an embedded ``chara``/``ccv3``
        tEXt chunk and ``.charx`` zip bundles. When ``options.enrich_with_llm``
        is true and a ``ingest_llm`` callable was supplied at construction,
        the parse is enriched before the character is written.
        """
        ingested = await self._ingest(card, options=options)
        return await self._finalize_import(target_world_id, ingested)

    async def import_charx(
        self,
        charx_bytes: bytes,
        target_world_id: str,
        *,
        options: IngestOptions | None = None,
    ) -> ImportResult:
        ingested = await self._ingest(charx_bytes, options=options)
        return await self._finalize_import(target_world_id, ingested)

    async def import_plaintext(self, text: str, target_world_id: str) -> ImportResult:
        data, warnings = parse_plaintext(text)
        return await self._finalize_import(
            target_world_id,
            IngestedCharacterCard(data=data, warnings=warnings),
        )

    async def import_character_card(
        self,
        payload: bytes,
        target_world_id: str,
        *,
        options: IngestOptions | None = None,
    ) -> tuple[ImportResult, IngestedCharacterCard]:
        """Like :meth:`import_sillytavern` but also returns the full ingest.

        Useful for UI flows that want to surface the creator notes, the
        alternate greetings, or the embedded character book before
        committing the character to disk.
        """
        ingested = await self._ingest(payload, options=options)
        result = await self._finalize_import(target_world_id, ingested)
        return result, ingested

    async def _ingest(
        self,
        payload: bytes,
        *,
        options: IngestOptions | None,
    ) -> IngestedCharacterCard:
        opts = options or IngestOptions()
        ingested = ingest_character_card_v2(payload, options=opts)
        if opts.enrich_with_llm and self._ingest_llm is not None:
            ingested = await enrich_with_llm(ingested, self._ingest_llm, options=opts)
        return ingested

    async def add_character_image(
        self,
        world_id: str,
        character_id: str,
        image: CharacterImage,
        *,
        image_bytes: bytes | None = None,
        source: str = "characters:add-image",
    ) -> Character:
        """Append ``image`` to ``character_id``'s gallery.

        When ``image_bytes`` is supplied, the bytes are written to disk
        next to the character markdown (the path is normalized to
        ``library/worlds/<world>/characters/<id>/<filename>``).
        Callers can pass any
        :class:`grimoire.types.characters.CharacterImage` — generated
        images from ImageGen, manually uploaded references, expression
        sheets, etc.
        """
        ent = await self.library.get_entity(world_id, "character", character_id)
        existing = list(_character_from_entity(ent).images)
        stored = image
        if image_bytes is not None:
            stored = await self._write_image_bytes(
                world_id=world_id,
                character_id=character_id,
                image=image,
                payload=image_bytes,
            )
        existing.append(stored)
        fm = dict(ent.frontmatter or {})
        fm["images"] = [_image_to_dict(img) for img in existing]
        updated = await self.library.update_entity(
            world_id,
            "character",
            character_id,
            frontmatter_patch=fm,
            body=None,
            source=source,
        )
        return _character_from_entity(updated)

    async def _write_image_bytes(
        self,
        *,
        world_id: str,
        character_id: str,
        image: CharacterImage,
        payload: bytes,
    ) -> CharacterImage:
        from grimoire.state_store.paths import library_root, relative_to_root

        # Default file name uses the image kind to keep multi-image
        # galleries scannable on disk.
        filename = image.path or f"{image.kind.value}.png"
        if "/" in filename:
            filename = filename.rsplit("/", 1)[-1]
        target_dir = (
            library_root(self.store.data_root) / "worlds" / world_id / "characters" / character_id
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        target.write_bytes(payload)
        return image.model_copy(update={"path": relative_to_root(self.store.data_root, target)})

    async def _finalize_import(
        self,
        target_world_id: str,
        ingested: IngestedCharacterCard,
    ) -> ImportResult:
        data = ingested.data
        result = ImportResult(warnings=list(ingested.warnings))
        try:
            existing = await self.library.get_entity(target_world_id, "character", data.id)
        except Exception:
            existing = None
        if existing is not None:
            result.skipped.append(data.id)
            result.warnings.append(
                f"character {data.id!r} already exists in {target_world_id!r}; not overwriting"
            )
            return result

        # Persist the embedded avatar (if any) before writing the markdown,
        # so the CharacterImage path on the card already points at a real
        # file rather than the placeholder we filled in during parsing.
        if ingested.avatar_bytes and data.images:
            avatar_index = next(
                (i for i, img in enumerate(data.images) if img.source == "embedded_avatar"),
                None,
            )
            if avatar_index is not None:
                placeholder = data.images[avatar_index]
                stored = await self._write_image_bytes(
                    world_id=target_world_id,
                    character_id=data.id,
                    image=placeholder,
                    payload=ingested.avatar_bytes,
                )
                images = list(data.images)
                images[avatar_index] = stored
                data = data.model_copy(update={"images": images})

        try:
            await self.create(target_world_id, data)
            result.created.append(data.id)
        except Exception as exc:  # pragma: no cover - defensive
            result.errors.append(str(exc))
        return result

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
        """Name / alias / tag substring search.

        Scope:
        * ``library`` — search across the entire library
        * ``world`` — restrict to ``world_id``
        * ``campaign`` — search composition + emergents for ``campaign_id``
        * ``all`` — library if no ``world_id``, else world; falls back to
          composition for ``campaign_id``
        """
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
            character = _character_from_entity(r) if isinstance(r, LibraryEntity) else r
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
        """Persist ``state`` to ``character_state``.

        Per spec characters-remaining §8, writes route through
        ``state_store.apply_delta`` so undo_turn can reverse them. The State
        Store's apply_delta already writes the row via ``upsert_row`` for
        campaign-sqlite targets, so there's no separate direct-write step.

        Callers that should not be reversible by undo (currently only
        ``pin_tier`` — UI choices must survive replay) pass
        ``record_in_delta_log=False`` and we fall back to a direct upsert.
        """
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

        # record_in_delta_log=False: direct upsert, no delta row (pin_tier).
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

    async def _get_tier_pin(self, ref: CharacterRef, campaign_id: CampaignId) -> ContextTier | None:
        state = await self._load_state(_asset_id_for_ref(ref), ref, campaign_id)
        return state.tier_pin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _character_from_entity(ent: LibraryEntity) -> Character:
    return _character_from_frontmatter(ent.frontmatter, ent.body, world_id=ent.world_id)


def _character_from_frontmatter(frontmatter: dict, body: str, *, world_id: str | None) -> Character:
    fm: dict[str, Any] = dict(frontmatter or {})
    try:
        role = CharacterRole(fm.get("role") or "major_npc")
    except ValueError:
        role = CharacterRole.MAJOR_NPC
    voice_data = fm.get("voice") or {}
    voice = VoiceAnchor(
        summary=str(voice_data.get("summary") or ""),
        voice_register=str(voice_data.get("register") or voice_data.get("voice_register") or ""),
        samples=[str(s) for s in (voice_data.get("samples") or [])],
        speech_patterns=[str(s) for s in (voice_data.get("speech_patterns") or [])],
        address_terms=dict(voice_data.get("address_terms") or {}),
        dos=[str(s) for s in (voice_data.get("dos") or [])],
        donts=[str(s) for s in (voice_data.get("donts") or [])],
    )
    image_data = fm.get("image")
    image = (
        ImagePromptTemplate(
            base_prompt=str(image_data.get("base_prompt") or ""),
            negative_prompt=str(image_data.get("negative_prompt") or ""),
            canonical_seed=image_data.get("canonical_seed"),
            extra={
                k: v
                for k, v in image_data.items()
                if k not in {"base_prompt", "negative_prompt", "canonical_seed"}
            },
        )
        if isinstance(image_data, dict)
        else None
    )
    images = [_image_from_dict(img) for img in (fm.get("images") or []) if isinstance(img, dict)]
    structural = [
        StructuralRelationship(
            to_ref=str(r.get("to_ref") or ""),
            kind=str(r.get("kind") or ""),
            note=str(r.get("note") or ""),
        )
        for r in (fm.get("structural_relationships") or [])
        if isinstance(r, dict)
    ]
    household_raw = fm.get("household_id")
    household_id = str(household_raw) if household_raw else None
    return Character(
        id=str(fm.get("id") or ""),
        name=str(fm.get("name") or fm.get("id") or ""),
        role=role,
        world_id=world_id,
        aliases=[str(a) for a in (fm.get("aliases") or [])],
        age=fm.get("age"),
        tags=[str(t) for t in (fm.get("tags") or [])],
        voice=voice,
        image=image,
        images=images,
        structural_relationships=structural,
        description=str(fm.get("description") or ""),
        body=body or "",
        household_id=household_id,
    )


def _frontmatter_from_payload(payload: CharacterData) -> dict:
    voice_dict = payload.voice.model_dump()
    # Map our internal field name back to the on-disk convention.
    voice_dict["register"] = voice_dict.pop("voice_register", "") or voice_dict.pop("register", "")
    fm: dict[str, Any] = {
        "id": payload.id,
        "name": payload.name,
        "role": payload.role.value,
        "aliases": list(payload.aliases),
        "tags": list(payload.tags),
        "description": payload.description,
        "voice": voice_dict,
    }
    if payload.age:
        fm["age"] = payload.age
    if payload.image is not None:
        img = payload.image
        fm["image"] = {
            "base_prompt": img.base_prompt,
            "negative_prompt": img.negative_prompt,
            "canonical_seed": img.canonical_seed,
            **(img.extra or {}),
        }
    if payload.images:
        fm["images"] = [_image_to_dict(img) for img in payload.images]
    if payload.structural_relationships:
        fm["structural_relationships"] = [
            {"to_ref": r.to_ref, "kind": r.kind, "note": r.note}
            for r in payload.structural_relationships
        ]
    if payload.household_id:
        fm["household_id"] = payload.household_id
    return fm


def _image_to_dict(image: CharacterImage) -> dict:
    out: dict[str, Any] = {
        "path": image.path,
        "kind": image.kind.value,
        "description": image.description,
        "tags": list(image.tags),
        "source": image.source,
    }
    if image.seed is not None:
        out["seed"] = image.seed
    if image.prompt_used:
        out["prompt_used"] = image.prompt_used
    if image.created_at is not None:
        out["created_at"] = image.created_at.isoformat()
    if image.extra:
        out["extra"] = dict(image.extra)
    return out


def _image_from_dict(raw: dict) -> CharacterImage:
    try:
        kind = CharacterImageKind(str(raw.get("kind") or "portrait"))
    except ValueError:
        kind = CharacterImageKind.PORTRAIT
    created_at_raw = raw.get("created_at")
    created_at: datetime | None = None
    if isinstance(created_at_raw, str) and created_at_raw:
        try:
            created_at = datetime.fromisoformat(created_at_raw)
        except ValueError:
            created_at = None
    elif isinstance(created_at_raw, datetime):
        created_at = created_at_raw
    return CharacterImage(
        path=str(raw.get("path") or ""),
        description=str(raw.get("description") or ""),
        kind=kind,
        tags=[str(t) for t in (raw.get("tags") or []) if t],
        seed=raw.get("seed"),
        prompt_used=str(raw.get("prompt_used") or ""),
        source=str(raw.get("source") or ""),
        created_at=created_at,
        extra=dict(raw.get("extra") or {}),
    )


def _entity_from_row_dict(row: Any) -> LibraryEntity:
    """Project a ``library_index`` row (from db.fetchall) into a LibraryEntity."""
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
    """Decode the ``history`` JSON column into a list of event dicts.

    Returns an empty list on missing / malformed payloads; non-list JSON
    is also rejected to keep callers from surfacing scalar / object
    garbage as a "timeline".
    """
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
    # Older rows (pre-migration 015) may not have a ``history`` column at
    # all if a caller hand-built the row dict; guard the lookup so reads
    # stay backward-compatible.
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


class _CharacterRefView:
    """Lightweight parsed view of a character reference."""

    def __init__(self, is_emergent: bool, world_id: str | None, asset_id: str) -> None:
        self.is_emergent = is_emergent
        self.world_id = world_id
        self.asset_id = asset_id


def _parse_character_ref(ref: str) -> _CharacterRefView:
    """Accept library:/campaign: prefixed refs as well as bare emergent paths."""
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
        # emergent/<kind>/<asset> or emergent/<asset>
        return _CharacterRefView(True, None, parts[-1])
    if ref.startswith("library:"):
        _, _, path = ref.partition("library:")
        parts = path.strip("/").split("/")
        if len(parts) >= 4 and parts[0] == "worlds" and parts[2] in {"characters", "character"}:
            return _CharacterRefView(False, parts[1], parts[3])
    # Bare "worlds/<s>/characters/<id>"
    parts = ref.strip("/").split("/")
    if len(parts) >= 4 and parts[0] == "worlds" and parts[2] in {"characters", "character"}:
        return _CharacterRefView(False, parts[1], parts[3])
    raise CharactersError(f"unrecognized character_ref {ref!r}")


def _asset_id_for_ref(ref: CharacterRef) -> str:
    return _parse_character_ref(ref).asset_id


def _parse_iso_dt(value: Any) -> datetime | None:
    """Best-effort ISO-8601 -> datetime, tolerating None / bad strings."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


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
    """How many recent turns post-date `last_seen`.

    Returns `None` when we cannot answer (no last_seen recorded, or the
    recent-turn window is empty). When `last_seen` is older than every id
    in the window we return `len(recent_turn_ids)` — i.e. "at least the
    whole window."
    """
    if not recent_turn_ids:
        return None
    if last_seen is None:
        return len(recent_turn_ids)
    try:
        idx = recent_turn_ids.index(last_seen)
    except ValueError:
        # last_seen falls outside the window — treat as fully aged-out.
        return len(recent_turn_ids)
    return len(recent_turn_ids) - 1 - idx


def _is_sparse_payload(payload: CharacterData) -> bool:
    """Spec 2026-05-17 §10: "sparse" = no description AND no tags.

    The voice anchor's contents are intentionally ignored — an emergent
    NPC introduced mid-scene almost always lacks a fleshed-out voice
    block, so requiring it would never trigger.
    """
    return not (payload.description or "").strip() and not payload.tags


def _capsule_draft_to_patch(draft: CapsuleDraft) -> dict[str, object]:
    """Translate a :class:`CapsuleDraft` into an ``update_emergent`` patch.

    Empty fields are skipped so a partial draft (e.g. summary only) does
    not blow away any defaults the caller provided.
    """
    patch: dict[str, object] = {}
    summary = (draft.summary_line or "").strip()
    if summary:
        patch["description"] = summary
    if draft.tags:
        patch["tags"] = [t for t in draft.tags if t]
    return patch


def _mentions_character(text: str, character: Character) -> bool:
    """Whole-word match against the character's name or any alias.

    Matching is case-insensitive (spec characters-remaining §1 doesn't pin
    case-sensitivity here; the related cross-world lookup flag in §7 is what
    governs id matching, not mention scanning).
    """
    needles = [character.name, *character.aliases]
    haystack = text.lower()
    for needle in needles:
        n = needle.strip().lower()
        if not n:
            continue
        # Word-boundary check — substring would let "Tom" match "Tomato".
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
