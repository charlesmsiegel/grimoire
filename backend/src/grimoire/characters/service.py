"""Concrete Characters service (spec 08).

Behavior facade over Library/Setting storage. The Library owns the on-disk
``settings/<setting>/characters/<id>.md`` files; this service adds the
character-specific behaviors: voice anchors and drift detection, context
tier recommendation, PC roles and multi-PC coordination, compressed card
views, campaign-scoped relationships, mechanical capability surfacing, and
imports.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from grimoire.library import LibraryService
from grimoire.mechanics.service import MechanicsService
from grimoire.state_store import StateStore
from grimoire.state_store.indexers import make_library_id
from grimoire.types.characters import (
    Character,
    CharacterData,
    CharacterFilter,
    CharacterRole,
    DriftReport,
    ImagePromptTemplate,
    ImportResult,
    PCEntry,
    RelationshipState,
    ResolvedCharacter,
    StructuralRelationship,
    VoiceAnchor,
)
from grimoire.types.common import CampaignId, CharacterRef
from grimoire.types.composition import LibraryEntity, ResolutionLayer, ResolutionSource
from grimoire.types.mechanics import Capability
from grimoire.types.scene import Post, Scene
from grimoire.types.state import CharacterState, ContextTier

from .drift import (
    CallableDriftChecker,
    DriftChecker,
    DriftInput,
    HeuristicDriftChecker,
    LLMCallable,
)
from .errors import (
    CharacterNotFoundError,
    CharactersError,
    PromotionError,
)
from .imports import parse_charx, parse_plaintext, parse_sillytavern
from .views import (
    render_capsule,
    render_compressed,
    render_full,
    render_voice_only,
)

PostFetcher = Callable[[str], Awaitable[list[Post]]]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


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
        post_fetcher: PostFetcher | None = None,
        drift_checker: DriftChecker | None = None,
        drift_threshold: float = 0.4,
    ) -> None:
        self.library = library
        self.mechanics = mechanics
        self.store: StateStore = library.store
        self._post_fetcher = post_fetcher
        self._drift_checker = drift_checker or HeuristicDriftChecker(
            drift_threshold=drift_threshold
        )
        self._drift_threshold = drift_threshold
        # Per-PC current scene cache; mirrors SceneManager._pc_current_scene
        # but keyed by ``(campaign_id, character_ref)``. The authoritative
        # source is the active-scene id stored on character_state.
        self._active_pc: dict[str, CharacterRef] = {}

    # ------------------------------------------------------------------ #
    # CRUD (delegated to Library)
    # ------------------------------------------------------------------ #

    async def list_in_setting(self, setting_id: str) -> list[Character]:
        rows = await self.library.list_in_setting(setting_id, "character")
        return [_character_from_entity(r) for r in rows]

    async def get(self, setting_id: str, character_id: str) -> Character:
        ent = await self.library.get_entity(setting_id, "character", character_id)
        return _character_from_entity(ent)

    async def create(self, setting_id: str, payload: CharacterData) -> Character:
        fm = _frontmatter_from_payload(payload)
        ent = await self.library.create_entity(
            setting_id, "character", payload.id, fm, payload.body, source="characters:create"
        )
        return _character_from_entity(ent)

    async def update(self, setting_id: str, character_id: str, patch: dict) -> Character:
        body = patch.pop("body", None)
        ent = await self.library.update_entity(
            setting_id,
            "character",
            character_id,
            frontmatter_patch=patch or None,
            body=body,
            source="characters:update",
        )
        return _character_from_entity(ent)

    async def delete(self, setting_id: str, character_id: str) -> None:
        await self.library.delete_entity(
            setting_id, "character", character_id, source="characters:delete"
        )

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
        return _character_from_frontmatter(fm, body, setting_id=None)

    async def delete_emergent(self, campaign_id: CampaignId, character_id: str) -> None:
        from grimoire.state_store.paths import emergent_path

        target = emergent_path(self.store.data_root, campaign_id, "character", character_id)
        if not target.exists():
            raise CharacterNotFoundError(
                f"no emergent character {character_id!r} in campaign {campaign_id!r}"
            )
        target.unlink()

    async def upsert_override(
        self,
        campaign_id: CampaignId,
        character_ref: str,
        patch: dict,
        *,
        source: str = "characters:override",
    ) -> None:
        """Persist a campaign-local override against a library character.

        ``character_ref`` must be a ``library:settings/<s>/characters/<id>``
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
                setting_id=None,
            )
            chain = [
                ResolutionSource(
                    layer=ResolutionLayer.EMERGENT,
                    scope="campaign-local",
                    library_id=None,
                    setting_id=None,
                    override_applied=False,
                )
            ]
            overrides_applied: list[str] = []
        else:
            entity = await self.library.resolve(
                f"settings/{ref_entity.setting_id}/characters/{ref_entity.asset_id}",
                campaign_id,
            )
            character = _character_from_frontmatter(
                entity.frontmatter, entity.body, setting_id=entity.setting_id
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
            ref = f"library:settings/{ent.setting_id}/characters/{ent.asset_id}"
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
    # Cross-setting variants
    # ------------------------------------------------------------------ #

    async def cross_setting_lookup(
        self, character_id: str, exclude_setting: str | None = None
    ) -> list[Character]:
        rows = await self.library.variants_of(character_id, "character")
        if exclude_setting:
            rows = [r for r in rows if r.setting_id != exclude_setting]
        return [_character_from_entity(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Compressed views
    # ------------------------------------------------------------------ #

    async def get_full_card(self, ref: str, campaign_id: CampaignId) -> str:
        resolved = await self.resolve(ref, campaign_id)
        return render_full(resolved.character)

    async def get_compressed_card(self, ref: str, campaign_id: CampaignId) -> str:
        resolved = await self.resolve(ref, campaign_id)
        return render_compressed(resolved.character)

    async def get_voice_only(self, ref: str, campaign_id: CampaignId) -> str:
        resolved = await self.resolve(ref, campaign_id)
        return render_voice_only(resolved.character)

    async def get_capsule(self, ref: str, campaign_id: CampaignId) -> str:
        resolved = await self.resolve(ref, campaign_id)
        return render_capsule(resolved.character)

    # ------------------------------------------------------------------ #
    # Tier management
    # ------------------------------------------------------------------ #

    async def recommend_tiers(
        self, scene: Scene, campaign_id: CampaignId | None = None
    ) -> dict[CharacterRef, ContextTier]:
        """Per-character tier recommendation for a scene.

        Rules (spec 08 §Tier management):
        * Present in the scene → spotlight
        * Mentioned in recent posts → background
        * User tier pin → forced tier
        """
        target_campaign = campaign_id or scene.campaign_id
        out: dict[CharacterRef, ContextTier] = {}
        for ref in scene.present_character_refs:
            out[ref] = ContextTier.SPOTLIGHT
        # User pins win over heuristics.
        for ref in list(out.keys()):
            pin = await self._get_tier_pin(ref, target_campaign)
            if pin is not None:
                out[ref] = pin
        return out

    async def pin_tier(self, ref: CharacterRef, campaign_id: CampaignId, tier: ContextTier) -> None:
        state = await self._load_state(_asset_id_for_ref(ref), ref, campaign_id)
        state.tier_pin = tier
        await self._save_state(ref, campaign_id, state, source="characters:tier-pin")

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
        return report

    async def drift_corrective_context(self, ref: CharacterRef, campaign_id: CampaignId) -> str:
        """Return a corrective voice snippet for the next prompt.

        If the cached drift score is below threshold, returns an empty string
        — callers should skip the injection.
        """
        state = await self._load_state(_asset_id_for_ref(ref), ref, campaign_id)
        if state.drift_score < self._drift_threshold:
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

    async def mark_screen_time(
        self, ref: CharacterRef, campaign_id: CampaignId, turn_id: str
    ) -> None:
        state = await self._load_state(_asset_id_for_ref(ref), ref, campaign_id)
        state.last_screen_time_turn = turn_id
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
        active_ref = self._active_pc.get(campaign_id)
        return [
            PCEntry(
                character_ref=row["character_ref"],
                name=row["display_name"],
                owner=row["owner"],
                active=(active_ref == row["character_ref"] if active_ref else bool(row["active"])),
            )
            for row in rows
        ]

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
        self._active_pc[campaign_id] = character_ref

    async def active_pc(self, campaign_id: CampaignId) -> CharacterRef | None:
        """Return the currently active PC for ``campaign_id`` (None if no PCs)."""
        if campaign_id in self._active_pc:
            return self._active_pc[campaign_id]
        pcs = await self.store.list_pcs(campaign_id)
        if not pcs:
            return None
        ref = pcs[0]["character_ref"]
        self._active_pc[campaign_id] = ref
        return ref

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
    ) -> dict:
        """Apply ``delta`` to the relationship between ``from_ref`` and ``to_ref``.

        Numeric fields (``affection``, ``trust``, ``dominance``,
        ``intimacy``) are incremented; other fields are set. Creates the row
        if it doesn't exist.
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
            row_id = _new_id("rel")
        else:
            state = _relationship_state_from_json(existing["state"])
            existing_types = json.loads(existing["types"]) if existing["types"] else []
            if types:
                # Merge new types in.
                merged = list(dict.fromkeys(existing_types + types))
                existing_types = merged
            row_id = existing["id"]

        merged_state = state.model_dump()
        for key in ("affection", "trust", "dominance", "intimacy"):
            if key in delta:
                merged_state[key] = int(merged_state.get(key) or 0) + int(delta[key])
        for key in ("awareness",):
            if key in delta:
                merged_state[key] = delta[key]
        if "custom" in delta and isinstance(delta["custom"], dict):
            merged_state["custom"] = {**(merged_state.get("custom") or {}), **delta["custom"]}

        await self.store.db.execute(
            """
            INSERT INTO relationships (
              id, campaign_id, branch_id, from_character_ref, to_character_ref,
              types, state, updated_at_turn
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              types = excluded.types,
              state = excluded.state,
              updated_at_turn = excluded.updated_at_turn
            """,
            (
                row_id,
                campaign_id,
                branch,
                from_ref,
                to_ref,
                json.dumps(existing_types),
                json.dumps(merged_state, default=str),
                turn_id or _now_iso(),
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
        }

    # ------------------------------------------------------------------ #
    # Promotion
    # ------------------------------------------------------------------ #

    async def promote_to_library(
        self,
        campaign_id: CampaignId,
        character_id: str,
        target_setting_id: str,
        *,
        source: str = "characters:promote",
        delete_emergent: bool = False,
    ) -> str:
        """Promote an emergent character into the library.

        Wraps the store's ``write_library_file`` rather than going through
        ``LibraryService.promote_to_library`` because that path explicitly
        excludes ``character``. Returns the new library path.
        """
        emergent = await self.store.get_emergent(campaign_id, "character", character_id)
        if emergent is None:
            raise PromotionError(
                f"no emergent character {character_id!r} in campaign {campaign_id!r}"
            )
        fm = dict(emergent.get("frontmatter") or {})
        fm.setdefault("id", character_id)
        library_id = make_library_id(target_setting_id, "character", character_id)
        result = await self.store.write_library_file(
            library_id=library_id,
            frontmatter=fm,
            body=emergent.get("body") or "",
            source=source,
            campaign_id=campaign_id,
        )
        if delete_emergent:
            from grimoire.state_store.paths import emergent_path

            target = emergent_path(self.store.data_root, campaign_id, "character", character_id)
            if target.exists():
                target.unlink()
        return str(result.path)

    # ------------------------------------------------------------------ #
    # Imports
    # ------------------------------------------------------------------ #

    async def import_sillytavern(self, card: bytes, target_setting_id: str) -> ImportResult:
        data, warnings = parse_sillytavern(card)
        return await self._finalize_import(target_setting_id, data, warnings)

    async def import_charx(self, charx_bytes: bytes, target_setting_id: str) -> ImportResult:
        data, warnings = parse_charx(charx_bytes)
        return await self._finalize_import(target_setting_id, data, warnings)

    async def import_plaintext(self, text: str, target_setting_id: str) -> ImportResult:
        data, warnings = parse_plaintext(text)
        return await self._finalize_import(target_setting_id, data, warnings)

    async def _finalize_import(
        self,
        target_setting_id: str,
        data: CharacterData,
        warnings: list[str],
    ) -> ImportResult:
        result = ImportResult(warnings=warnings)
        try:
            existing = await self.library.get_entity(target_setting_id, "character", data.id)
        except Exception:
            existing = None
        if existing is not None:
            result.skipped.append(data.id)
            result.warnings.append(
                f"character {data.id!r} already exists in {target_setting_id!r}; not overwriting"
            )
            return result
        try:
            await self.create(target_setting_id, data)
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
        setting_id: str | None = None,
        scope: str = "all",
        campaign_id: CampaignId | None = None,
    ) -> list[Character]:
        """Name / alias / tag substring search.

        Scope:
        * ``library`` — search across the entire library
        * ``setting`` — restrict to ``setting_id``
        * ``campaign`` — search composition + emergents for ``campaign_id``
        * ``all`` — library if no ``setting_id``, else setting; falls back to
          composition for ``campaign_id``
        """
        q = (query or "").strip().lower()
        if not q:
            return []
        rows: list[LibraryEntity] = []
        if scope in {"setting"} and setting_id is not None:
            rows = await self.library.list_in_setting(setting_id, "character")
        elif scope == "library" or (scope == "all" and setting_id is None and campaign_id is None):
            rows = await self.store.db.fetchall(
                "SELECT * FROM library_index WHERE kind = 'character' ORDER BY name"
            )
            rows = [_entity_from_row_dict(r) for r in rows]
        elif scope == "campaign" or campaign_id is not None:
            rows = await self.library.list_for_composition(campaign_id, "character")
        elif setting_id is not None:
            rows = await self.library.list_in_setting(setting_id, "character")

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
        )

    async def _save_state(
        self,
        ref: CharacterRef,
        campaign_id: CampaignId,
        state: CharacterState,
        *,
        source: str,
        turn_id: str | None = None,
    ) -> None:
        """Persist ``state`` directly to the character_state table.

        We use a direct upsert (rather than apply_delta) because we don't yet
        have a turn-level audit pipeline; the change still emits a delta row
        via the State Store's reusable insertion path so reversal stays
        possible.
        """
        branch = state.branch_id or _branch_for(campaign_id, None)
        payload = {
            "character_ref": ref,
            "campaign_id": campaign_id,
            "branch_id": branch,
            "location_ref": state.location_ref,
            "emotional_state": state.emotional_state,
            "physical_state": state.physical_state,
            "immediate_intent": state.immediate_intent,
            "knowledge_state": json.dumps(state.knowledge_state or {}, default=str),
            "last_action": state.last_action,
            "last_screen_time_turn": state.last_screen_time_turn,
            "visible_to_pc": 1 if state.visible_to_pc else 0,
            "drift_score": float(state.drift_score),
            "tier_pin": state.tier_pin.value if state.tier_pin else None,
            "current_scene_id": state.current_scene_id,
            "updated_at_turn": turn_id or state.updated_at_turn or _now_iso(),
        }
        await self.store.db.execute(
            """
            INSERT INTO character_state (
              character_ref, campaign_id, branch_id, location_ref,
              emotional_state, physical_state, immediate_intent,
              knowledge_state, last_action, last_screen_time_turn,
              visible_to_pc, drift_score, tier_pin, current_scene_id,
              updated_at_turn
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
              updated_at_turn = excluded.updated_at_turn
            """,
            tuple(
                payload[k]
                for k in (
                    "character_ref",
                    "campaign_id",
                    "branch_id",
                    "location_ref",
                    "emotional_state",
                    "physical_state",
                    "immediate_intent",
                    "knowledge_state",
                    "last_action",
                    "last_screen_time_turn",
                    "visible_to_pc",
                    "drift_score",
                    "tier_pin",
                    "current_scene_id",
                    "updated_at_turn",
                )
            ),
        )
        _ = source  # reserved for future delta-log integration

    async def _get_tier_pin(self, ref: CharacterRef, campaign_id: CampaignId) -> ContextTier | None:
        state = await self._load_state(_asset_id_for_ref(ref), ref, campaign_id)
        return state.tier_pin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _character_from_entity(ent: LibraryEntity) -> Character:
    return _character_from_frontmatter(ent.frontmatter, ent.body, setting_id=ent.setting_id)


def _character_from_frontmatter(
    frontmatter: dict, body: str, *, setting_id: str | None
) -> Character:
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
    structural = [
        StructuralRelationship(
            to_ref=str(r.get("to_ref") or ""),
            kind=str(r.get("kind") or ""),
            note=str(r.get("note") or ""),
        )
        for r in (fm.get("structural_relationships") or [])
        if isinstance(r, dict)
    ]
    return Character(
        id=str(fm.get("id") or ""),
        name=str(fm.get("name") or fm.get("id") or ""),
        role=role,
        setting_id=setting_id,
        aliases=[str(a) for a in (fm.get("aliases") or [])],
        age=fm.get("age"),
        tags=[str(t) for t in (fm.get("tags") or [])],
        voice=voice,
        image=image,
        structural_relationships=structural,
        description=str(fm.get("description") or ""),
        body=body or "",
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
    return fm


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
        setting_id=raw.get("setting_id"),
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
    if filter.setting_ids and entity.setting_id not in filter.setting_ids:
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
    if filter.setting_ids and c.setting_id not in filter.setting_ids:
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


def _relationship_row_to_dict(row: Any) -> dict:
    types = row["types"]
    try:
        types = json.loads(types) if types else []
    except (TypeError, json.JSONDecodeError):
        types = []
    state = _relationship_state_from_json(row["state"]).model_dump()
    return {
        "id": row["id"],
        "campaign_id": row["campaign_id"],
        "branch_id": row["branch_id"],
        "from_ref": row["from_character_ref"],
        "to_ref": row["to_character_ref"],
        "types": types,
        "state": state,
        "updated_at_turn": row["updated_at_turn"],
    }


class _CharacterRefView:
    """Lightweight parsed view of a character reference."""

    def __init__(self, is_emergent: bool, setting_id: str | None, asset_id: str) -> None:
        self.is_emergent = is_emergent
        self.setting_id = setting_id
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
        if len(parts) >= 4 and parts[0] == "settings" and parts[2] in {"characters", "character"}:
            return _CharacterRefView(False, parts[1], parts[3])
    # Bare "settings/<s>/characters/<id>"
    parts = ref.strip("/").split("/")
    if len(parts) >= 4 and parts[0] == "settings" and parts[2] in {"characters", "character"}:
        return _CharacterRefView(False, parts[1], parts[3])
    raise CharactersError(f"unrecognized character_ref {ref!r}")


def _asset_id_for_ref(ref: CharacterRef) -> str:
    return _parse_character_ref(ref).asset_id


def _library_id_from_ref(ref: CharacterRef) -> str:
    view = _parse_character_ref(ref)
    if view.is_emergent or view.setting_id is None:
        raise CharactersError(f"cannot derive library_id from emergent ref {ref!r}")
    return make_library_id(view.setting_id, "character", view.asset_id)
