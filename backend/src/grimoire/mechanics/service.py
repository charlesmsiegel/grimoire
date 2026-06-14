"""The :class:`MechanicsService` — façade over the active mechanics module.

The service is what the Orchestrator, Extractor, Context Builder and Time
Engine talk to. It picks the active module for a campaign (from the
campaigns table's ``mechanics_module`` column), delegates queries to the
module, and persists sheet writes through the State Store.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from grimoire.mechanics.config import MechanicsConfig
from grimoire.mechanics.discovery import DiscoveryError, discover
from grimoire.mechanics.loader import load_module
from grimoire.mechanics.null import NULL_MECHANICS_ID, NullMechanicsModule
from grimoire.mechanics.registry import MechanicsRegistry, RegisteredModule
from grimoire.mechanics.rng import derive_roll_seed
from grimoire.state_store.errors import ConflictError, NotFoundError
from grimoire.state_store.store import StateStore
from grimoire.types.common import (
    CampaignId,
    CharacterRef,
    Duration,
    JsonSchema,
    MechanicsModuleId,
    ValidationResult,
)
from grimoire.types.mechanics import (
    BulkSheetCreateResult,
    Capability,
    CreationStep,
    MechanicsSwitchResult,
    MissingSheet,
    ModuleManifest,
    NarratedEvent,
    PowerDefinition,
    ProposedRoll,
    Roll,
    RollResult,
    SheetRef,
    TickContext,
)
from grimoire.types.protocols import MechanicsModule
from grimoire.types.scene import SceneContext
from grimoire.validation.validator import validate

if TYPE_CHECKING:
    from grimoire.mechanics.authoring import MechanicsAuthor

logger = logging.getLogger(__name__)

# Type alias for the campaign → mechanics-id resolver. Default is the
# State Store; tests inject a lambda.
ActiveModuleResolver = Callable[[CampaignId], "object"]


@dataclass(frozen=True)
class RescanReport:
    """Outcome of a discovery pass.

    ``loaded`` and ``failed`` mirror the structure of the plugin module's
    rescan report so the Frontend can render both with the same component.
    """

    discovered: list[str]
    loaded: list[str]
    failed: list[tuple[str, str]]
    removed: list[str]


_SHEET_KIND_DEFAULT = "character"


class MechanicsService:
    """The app-facing implementation of the ``Mechanics`` protocol.

    Constructed once at startup with a :class:`MechanicsConfig` and the
    shared :class:`StateStore`. Call :meth:`rescan` to discover modules
    from disk; subsequent queries route to the registered module that
    matches a campaign's ``mechanics_module`` column.
    """

    def __init__(
        self,
        config: MechanicsConfig,
        state_store: StateStore,
        *,
        event_bus: Any | None = None,
    ) -> None:
        self._config = config
        self._state_store = state_store
        self._registry = MechanicsRegistry()
        self._discovery_errors: list[DiscoveryError] = []
        self._failed: dict[str, list[str]] = {}
        self._warnings: dict[str, list[str]] = {}
        self._event_bus = event_bus
        self._null = NullMechanicsModule()
        self._author: MechanicsAuthor | None = None

    @property
    def config(self) -> MechanicsConfig:
        return self._config

    @property
    def author(self) -> MechanicsAuthor:
        """Lazily-constructed authoring write path for this service."""
        if self._author is None:
            from grimoire.mechanics.authoring import MechanicsAuthor

            self._author = MechanicsAuthor(self)
        return self._author

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    async def rescan(self) -> RescanReport:
        """Re-discover modules under ``config.root`` and (re-)load them."""
        previous_ids = set(self._registry.ids()) | set(self._failed)
        discovered, errors = discover(roots=[self._config.root])
        self._discovery_errors = errors

        seen: set[str] = set()
        loaded: list[str] = []
        failed: list[tuple[str, str]] = []
        new_failed: dict[str, list[str]] = {}

        for derr in errors:
            failed.append((derr.module_dir.name, derr.message))

        new_warnings: dict[str, list[str]] = {}
        for d in discovered:
            module_id = d.raw_manifest.get("id") if isinstance(d.raw_manifest, dict) else None
            if not isinstance(module_id, str):
                module_id = d.module_dir.name
            seen.add(module_id)
            result = load_module(d)
            if result.warnings:
                new_warnings[module_id] = list(result.warnings)
            if result.ok and result.manifest is not None and result.instance is not None:
                self._registry.register(
                    result.manifest,
                    result.instance,
                    module_dir=result.module_dir,
                    sheet_schemas=result.sheet_schemas,
                    content_schemas=result.content_schemas,
                    theme_css=result.theme_css,
                )
                loaded.append(module_id)
            else:
                self._registry.unregister(module_id)
                reason = "; ".join(result.errors) or "unknown load error"
                failed.append((module_id, reason))
                new_failed[module_id] = list(result.errors)
        self._warnings = new_warnings

        removed = sorted(previous_ids - seen)
        for module_id in removed:
            self._registry.unregister(module_id)
            self._failed.pop(module_id, None)

        self._failed = new_failed
        return RescanReport(
            discovered=[_id_or_dirname(d) for d in discovered],
            loaded=loaded,
            failed=failed,
            removed=removed,
        )

    def discovery_errors(self) -> list[DiscoveryError]:
        return list(self._discovery_errors)

    def failed_modules(self) -> dict[str, list[str]]:
        return {mid: list(errs) for mid, errs in self._failed.items()}

    def module_warnings(self) -> dict[str, list[str]]:
        """Per-module non-fatal load warnings (e.g. missing sheet/content/theme)."""
        return {mid: list(ws) for mid, ws in self._warnings.items()}

    # ------------------------------------------------------------------
    # Direct registry access — useful for in-memory tests and helpers
    # ------------------------------------------------------------------

    def register_module(self, manifest: ModuleManifest, instance: MechanicsModule) -> None:
        """Register an instance constructed in-process (e.g. by tests)."""
        self._registry.register(manifest, instance)

    def get_module(self, module_id: MechanicsModuleId) -> MechanicsModule | None:
        record = self._registry.get(module_id)
        return record.instance if record else None

    # ------------------------------------------------------------------
    # Active-module lookup
    # ------------------------------------------------------------------

    async def active_module(self, campaign_id: CampaignId) -> MechanicsModule | None:
        """Return the module bound to ``campaign_id`` or ``None`` for null."""
        module_id = await self._campaign_mechanics_id(campaign_id)
        if module_id is None or module_id == NULL_MECHANICS_ID:
            return None
        record = self._registry.get(module_id)
        if record is None:
            logger.warning(
                "campaign %s references mechanics module %r which is not loaded",
                campaign_id,
                module_id,
            )
            return None
        return record.instance

    async def _active_or_null(self, campaign_id: CampaignId) -> MechanicsModule:
        module = await self.active_module(campaign_id)
        return module if module is not None else self._null

    async def _campaign_mechanics_id(self, campaign_id: CampaignId) -> MechanicsModuleId | None:
        row = await self._state_store.db.fetchone(
            "SELECT mechanics_module FROM campaigns WHERE id = ?",
            (campaign_id,),
        )
        if row is None:
            raise NotFoundError(f"campaign {campaign_id!r} not found")
        value = row["mechanics_module"]
        if value is None or value == "" or value == NULL_MECHANICS_ID:
            return None
        return value

    # ------------------------------------------------------------------
    # Convenience pass-throughs
    # ------------------------------------------------------------------

    async def sheet_schema(
        self,
        campaign_id: CampaignId,
        entity_kind: str,
    ) -> JsonSchema | None:
        module = await self.active_module(campaign_id)
        if module is None:
            return None
        # Explicit overrides on the instance win over the disk cache, so a
        # module can compute schemas dynamically; fall back to the loader's
        # ``sheets/<kind>.json`` when the instance returns None.
        schema = module.sheet_schema(entity_kind)
        if schema is not None:
            return schema
        module_id = getattr(module, "id", None)
        if isinstance(module_id, str):
            record = self._registry.get(module_id)
            if record is not None:
                cached = record.sheet_schemas.get(entity_kind)
                if cached is not None:
                    return cached
        return None

    async def get_sheet(
        self,
        campaign_id: CampaignId,
        entity_ref: str,
        entity_kind: str | None = None,
    ) -> dict | None:
        """Return the stored sheet for ``entity_ref`` under the active module.

        ``entity_kind`` falls back to a best-effort parse of the ref
        (e.g. ``"character:hyde"`` ⇒ ``"character"``). If the campaign has
        ``mechanics: null``, returns ``None``.
        """
        module_id = await self._campaign_mechanics_id(campaign_id)
        if module_id is None:
            return None
        kind, entity_id = _parse_entity_ref(entity_ref, fallback_kind=entity_kind)
        return await self._state_store.get_sheet(
            campaign_id=campaign_id,
            kind=kind,
            entity_id=entity_id,
            mechanics_id=module_id,
        )

    async def update_sheet(
        self,
        campaign_id: CampaignId,
        entity_ref: str,
        patch: dict,
        *,
        entity_kind: str | None = None,
        source: str = "user",
        turn_id: str | None = None,
    ) -> dict:
        """Merge ``patch`` into the sheet for ``entity_ref`` and persist it.

        Validates the merged result against the active module's schema
        when ``config.validation.strict_sheets`` is on. Raises
        :class:`ValueError` on validation failure so the caller can
        surface errors to the UI.
        """
        module_id = await self._campaign_mechanics_id(campaign_id)
        if module_id is None:
            raise ValueError(f"campaign {campaign_id!r} has mechanics: null; sheets are not stored")
        kind, entity_id = _parse_entity_ref(entity_ref, fallback_kind=entity_kind)
        module = await self._active_or_null(campaign_id)

        current = await self._state_store.get_sheet(
            campaign_id=campaign_id,
            kind=kind,
            entity_id=entity_id,
            mechanics_id=module_id,
        )
        if current is None:
            current = module.initialize_sheet(kind, entity_id)
        merged = _deep_merge(current, patch)

        if self._config.validation.strict_sheets:
            result = module.validate_sheet(kind, merged)
            if not result.valid:
                raise ValueError("sheet failed validation: " + "; ".join(result.errors))

        await self._state_store.write_sheet(
            campaign_id=campaign_id,
            kind=kind,
            entity_id=entity_id,
            mechanics_id=module_id,
            sheet=merged,
            source=f"mechanics:{module_id}" if source == "mechanics" else source,
            turn_id=turn_id,
        )
        return merged

    async def capabilities_of(
        self,
        campaign_id: CampaignId,
        entity_ref: CharacterRef,
        entity_kind: str | None = None,
    ) -> list[Capability]:
        module_id = await self._campaign_mechanics_id(campaign_id)
        if module_id is None:
            return []
        module = await self._active_or_null(campaign_id)
        kind, entity_id = _parse_entity_ref(entity_ref, fallback_kind=entity_kind)
        sheet = await self._state_store.get_sheet(
            campaign_id=campaign_id,
            kind=kind,
            entity_id=entity_id,
            mechanics_id=module_id,
        )
        return module.capabilities_of(entity_ref, sheet or {})

    async def evaluate_pre_roll(
        self,
        campaign_id: CampaignId,
        player_input: str,
        scene: SceneContext,
    ) -> list[ProposedRoll]:
        module = await self.active_module(campaign_id)
        if module is None:
            return []
        return module.evaluate_pre_roll(player_input, scene)

    async def resolve_roll(
        self,
        campaign_id: CampaignId,
        roll: Roll,
    ) -> RollResult:
        """Resolve a roll with a seed derived from the campaign's RNG seed."""
        module = await self.active_module(campaign_id)
        if module is None:
            return self._null.resolve_roll(roll, roll.seed)
        campaign_seed = await self._campaign_seed(campaign_id)
        derived = derive_roll_seed(campaign_seed, roll.seed, roll.id)
        return module.resolve_roll(roll, derived)

    async def validate_narrated_event(
        self,
        campaign_id: CampaignId,
        event: NarratedEvent,
        scene: SceneContext,
    ) -> ValidationResult:
        module = await self.active_module(campaign_id)
        if module is None:
            return ValidationResult(valid=True)
        result = module.validate_narrated_event(event, scene)
        if not result.valid and not self._config.validation.strict_events:
            # Lenient mode: downgrade errors to warnings so the turn
            # still proceeds. Caller still sees the report.
            return ValidationResult(
                valid=True,
                warnings=list(result.warnings) + list(result.errors),
                proposed_deltas=list(result.proposed_deltas),
            )
        return result

    async def time_tick(
        self,
        campaign_id: CampaignId,
        entity_ref: CharacterRef,
        duration: Duration,
        context: TickContext | None = None,
        *,
        entity_kind: str | None = None,
    ) -> list[Any]:
        module_id = await self._campaign_mechanics_id(campaign_id)
        if module_id is None:
            return []
        module = await self._active_or_null(campaign_id)
        kind, entity_id = _parse_entity_ref(entity_ref, fallback_kind=entity_kind)
        sheet = await self._state_store.get_sheet(
            campaign_id=campaign_id,
            kind=kind,
            entity_id=entity_id,
            mechanics_id=module_id,
        )
        ctx = context or TickContext(
            campaign_id=campaign_id,
            duration=duration,
        )
        return module.time_tick(entity_ref, sheet or {}, duration, ctx)

    # ------------------------------------------------------------------
    # Content schemas + content instances (§2)
    # ------------------------------------------------------------------

    async def list_content_kinds(self, campaign_id: CampaignId) -> list[str]:
        module = await self.active_module(campaign_id)
        if module is None:
            return []
        kinds = list(module.list_content_kinds() or [])
        if kinds:
            return kinds
        module_id = getattr(module, "id", None)
        if isinstance(module_id, str):
            record = self._registry.get(module_id)
            if record is not None:
                return sorted(record.content_schemas)
        return []

    async def content_schema(self, campaign_id: CampaignId, kind: str) -> JsonSchema | None:
        module = await self.active_module(campaign_id)
        if module is None:
            return None
        schema = module.content_schema(kind)
        if schema:
            return schema
        module_id = getattr(module, "id", None)
        if isinstance(module_id, str):
            record = self._registry.get(module_id)
            if record is not None:
                return record.content_schemas.get(kind)
        return None

    async def list_content(self, campaign_id: CampaignId, kind: str) -> list[dict]:
        module_id = await self._campaign_mechanics_id(campaign_id)
        if module_id is None:
            return []
        return await self._state_store.list_content(
            campaign_id=campaign_id, kind=kind, mechanics_id=module_id
        )

    async def get_content(self, campaign_id: CampaignId, kind: str, content_id: str) -> dict | None:
        module_id = await self._campaign_mechanics_id(campaign_id)
        if module_id is None:
            return None
        return await self._state_store.get_content(
            campaign_id=campaign_id,
            kind=kind,
            content_id=content_id,
            mechanics_id=module_id,
        )

    async def put_content(
        self,
        campaign_id: CampaignId,
        kind: str,
        content_id: str,
        payload: dict,
        *,
        source: str = "user",
        turn_id: str | None = None,
    ) -> dict:
        """Validate and persist a content instance for the active module.

        Raises :class:`ValueError` on validation failure when
        ``config.validation.strict_content`` is on (default).
        """
        module_id = await self._campaign_mechanics_id(campaign_id)
        if module_id is None:
            raise ValueError(f"campaign {campaign_id!r} has mechanics: null; content is not stored")
        if self._config.validation.strict_content:
            schema = await self.content_schema(campaign_id, kind)
            if schema:
                result = validate(payload, schema)
                if not result.ok:
                    raise ValueError(
                        "content failed validation: " + "; ".join(e.message for e in result.errors)
                    )
        await self._state_store.write_content(
            campaign_id=campaign_id,
            kind=kind,
            content_id=content_id,
            mechanics_id=module_id,
            payload=payload,
            source=f"mechanics:{module_id}" if source == "mechanics" else source,
            turn_id=turn_id,
        )
        return payload

    # ------------------------------------------------------------------
    # Character creation (§4)
    # ------------------------------------------------------------------

    async def character_creation_steps(
        self, campaign_id_or_module_id: CampaignId | MechanicsModuleId
    ) -> list[CreationStep]:
        """Return creation steps. Accepts either a campaign_id or a module_id."""
        module = await self._resolve_module_or_campaign(campaign_id_or_module_id)
        if module is None:
            return []
        steps = module.character_creation_steps() or []
        # Coerce raw dicts into CreationStep instances so the response is typed.
        normalised: list[CreationStep] = []
        for step in steps:
            if isinstance(step, CreationStep):
                normalised.append(step)
            elif isinstance(step, dict):
                normalised.append(CreationStep.model_validate(step))
        return normalised

    async def finalize_character_creation(
        self,
        campaign_id: CampaignId,
        character_ref: str,
        step_outputs: dict,
        *,
        source: str = "user",
        turn_id: str | None = None,
    ) -> dict:
        """Compose, validate, and persist a starting sheet from step outputs."""
        module_id = await self._campaign_mechanics_id(campaign_id)
        if module_id is None:
            raise ValueError(
                f"campaign {campaign_id!r} has mechanics: null; cannot finalize creation"
            )
        module = await self._active_or_null(campaign_id)
        if not isinstance(step_outputs, dict):
            raise ValueError("step_outputs must be a mapping of step_id → form data")

        steps = await self.character_creation_steps(campaign_id)
        merged: dict = {}
        for step in steps:
            data = step_outputs.get(step.id)
            if data is None:
                if step.optional:
                    continue
                raise ValueError(f"missing output for required step {step.id!r}")
            if not isinstance(data, dict):
                raise ValueError(f"step {step.id!r} output must be a JSON object")
            step_result = validate(data, step.step_schema)
            if not step_result.ok:
                raise ValueError(
                    f"step {step.id!r} failed validation: "
                    + "; ".join(e.message for e in step_result.errors)
                )
            merged = _deep_merge(merged, data)

        kind, entity_id = _parse_entity_ref(character_ref, fallback_kind="character")
        sheet_result = module.validate_sheet(kind, merged)
        if not sheet_result.valid:
            raise ValueError("composed sheet failed validation: " + "; ".join(sheet_result.errors))

        await self._state_store.write_sheet(
            campaign_id=campaign_id,
            kind=kind,
            entity_id=entity_id,
            mechanics_id=module_id,
            sheet=merged,
            source=f"mechanics:{module_id}" if source in ("user", "mechanics") else source,
            turn_id=turn_id,
        )
        return merged

    # ------------------------------------------------------------------
    # Power definitions (§9)
    # ------------------------------------------------------------------

    async def power_definitions(self, campaign_id: CampaignId) -> list[PowerDefinition]:
        module = await self.active_module(campaign_id)
        if module is None:
            return []
        defs = module.power_definitions() or []
        return [
            d if isinstance(d, PowerDefinition) else PowerDefinition.model_validate(d) for d in defs
        ]

    async def power_definition(
        self, campaign_id: CampaignId, power_id: str
    ) -> PowerDefinition | None:
        module = await self.active_module(campaign_id)
        if module is None:
            return None
        result = module.power_definition(power_id)
        if result is None:
            return None
        if isinstance(result, PowerDefinition):
            return result
        return PowerDefinition.model_validate(result)

    # ------------------------------------------------------------------
    # Mid-campaign module switching (§6)
    # ------------------------------------------------------------------

    async def switch_module(
        self,
        campaign_id: CampaignId,
        new_mechanics_id: MechanicsModuleId | None,
        source: str = "user",
    ) -> MechanicsSwitchResult:
        """Change a campaign's bound mechanics module.

        Records the transition in ``campaign_mechanics_history`` and
        returns the list of PC sheets that exist for the previous module
        but lack one under the new module.
        """
        row = await self._state_store.db.fetchone(
            "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
        )
        if row is None:
            raise NotFoundError(f"campaign {campaign_id!r} not found")
        current = dict(row)
        previous = current.get("mechanics_module")
        # Normalise '' → None so callers don't have to distinguish.
        target = new_mechanics_id or None

        await self._state_store.upsert_campaign(
            campaign_id=campaign_id,
            name=current["name"],
            description=current.get("description"),
            mechanics_module=target,
            style_guide_id=current.get("style_guide_id"),
            image_preset_id=current.get("image_preset_id"),
            inline_style_guide=current.get("inline_style_guide"),
            content_boundaries=current.get("content_boundaries"),
            greeting_id=current.get("greeting_id"),
        )
        await self._state_store.record_mechanics_switch(
            campaign_id=campaign_id,
            previous=previous,
            current=target,
            source=source,
        )

        missing = await self._compute_missing_sheets(campaign_id, target)

        if self._event_bus is not None:
            try:
                from grimoire import events  # local import — avoid cycles
                from grimoire.event_bus import Event

                await self._event_bus.emit(
                    Event(
                        type=events.MECHANICS_SWITCHED,
                        payload={
                            "campaign_id": campaign_id,
                            "previous": previous,
                            "current": target,
                            "missing_count": len(missing),
                        },
                    )
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("event emit failed for mechanics_switched: %s", exc)

        return MechanicsSwitchResult(previous=previous, current=target, missing_sheets=missing)

    async def bulk_create_missing_sheets(
        self,
        campaign_id: CampaignId,
        *,
        characters: Any,
        world: Any,
        source: str = "api:bulk-create-missing",
    ) -> BulkSheetCreateResult:
        """Initialise a sheet for every campaign entity that lacks one.

        Walks the campaign's mechanics module's ``sheet_kinds``, enumerates
        the entities of each kind (cast for ``character``, world entities
        otherwise), and writes an initial sheet for any that don't already
        have one. ``characters`` and ``world`` are the owning services,
        injected by the caller so this module stays decoupled from them.

        Raises :class:`NotFoundError` (campaign or module absent) or
        :class:`ConflictError` (no module bound). A failure enumerating
        entities propagates rather than being silently treated as empty, so
        callers can tell "no entities" from "the listing broke".
        """
        row = await self._state_store.db.fetchone(
            "SELECT mechanics_module FROM campaigns WHERE id = ?", (campaign_id,)
        )
        if row is None:
            raise NotFoundError(f"campaign {campaign_id!r} not found")
        module_id = row["mechanics_module"]
        if not module_id or module_id == NULL_MECHANICS_ID:
            raise ConflictError(f"campaign {campaign_id!r} has no mechanics module bound")
        module = self.get_module(module_id)
        if module is None:
            raise NotFoundError(f"mechanics module {module_id!r} is not loaded")

        sheet_kinds: list[str] = list(getattr(module, "sheet_kinds", None) or [])
        if not sheet_kinds:
            manifest = await self.module_info(module_id)
            sheet_kinds = list(manifest.sheet_kinds) if manifest else []
        if not sheet_kinds:
            sheet_kinds = [_SHEET_KIND_DEFAULT]

        inventory: dict[str, list[str]] = {}
        for kind in sheet_kinds:
            if kind == _SHEET_KIND_DEFAULT:
                rows = await characters.list_for_campaign(campaign_id)
                inventory[kind] = [r.character.id for r in rows]
            else:
                entries = await world.list_for_campaign(campaign_id, kind)
                ids = [getattr(e, "asset_id", None) or getattr(e, "id", None) for e in entries]
                inventory[kind] = [i for i in ids if i]

        created: list[SheetRef] = []
        skipped: list[SheetRef] = []
        for kind, entity_ids in inventory.items():
            existing = await self._state_store.list_sheet_entity_ids(
                campaign_id=campaign_id,
                kind=kind,
                mechanics_id=module_id,
            )
            for entity_id in entity_ids:
                if entity_id in existing:
                    skipped.append(SheetRef(kind=kind, entity_id=entity_id))
                    continue
                initial = module.initialize_sheet(kind, entity_id)
                await self._state_store.write_sheet(
                    campaign_id=campaign_id,
                    kind=kind,
                    entity_id=entity_id,
                    mechanics_id=module_id,
                    sheet=initial,
                    source=source,
                )
                created.append(SheetRef(kind=kind, entity_id=entity_id))

        return BulkSheetCreateResult(created=created, skipped=skipped)

    async def _compute_missing_sheets(
        self,
        campaign_id: CampaignId,
        new_mechanics_id: MechanicsModuleId | None,
    ) -> list[MissingSheet]:
        if new_mechanics_id is None:
            return []
        # PCs for the campaign.
        pcs = await self._state_store.list_pcs(campaign_id)
        if not pcs:
            return []
        from grimoire.state_store.paths import campaigns_root

        sheets_dir = (
            campaigns_root(self._state_store.data_root) / campaign_id / "sheets" / "characters"
        )
        missing: list[MissingSheet] = []
        for pc in pcs:
            ref = pc.get("character_ref") or ""
            # Parse to the entity_id used in filenames.
            _, entity_id = _parse_entity_ref(ref, fallback_kind="character")
            new_sheet_file = sheets_dir / f"{entity_id}.{new_mechanics_id}.yaml"
            if new_sheet_file.exists():
                continue
            # Detect "previous-module sheet exists" by scanning the dir.
            has_other = False
            if sheets_dir.is_dir():
                prefix = f"{entity_id}."
                for entry in sheets_dir.iterdir():
                    if not entry.is_file():
                        continue
                    matches_entity = entry.name.startswith(prefix) and entry.name.endswith(".yaml")
                    if matches_entity and entry.name != new_sheet_file.name:
                        has_other = True
                        break
            if has_other:
                missing.append(
                    MissingSheet(
                        kind="character",
                        entity_id=entity_id,
                        character_name=pc.get("display_name") or pc.get("name"),
                    )
                )
        return missing

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _resolve_module_or_campaign(self, ref: str) -> MechanicsModule | None:
        """Accept either a campaign_id or a module_id; return the module instance."""
        # Try as campaign_id first (campaigns have prefixed style ids in practice,
        # but the only way to tell is to look it up).
        try:
            row = await self._state_store.db.fetchone(
                "SELECT mechanics_module FROM campaigns WHERE id = ?", (ref,)
            )
        except Exception:
            row = None
        if row is not None:
            value = row["mechanics_module"]
            if value is None or value == "" or value == NULL_MECHANICS_ID:
                return None
            record = self._registry.get(value)
            return record.instance if record is not None else None
        # Fall back to a direct module_id lookup.
        record = self._registry.get(ref)
        return record.instance if record is not None else None

    # ------------------------------------------------------------------
    # Registry introspection
    # ------------------------------------------------------------------

    async def list_installed_modules(self) -> list[ModuleManifest]:
        return [r.manifest for r in self._registry.list()]

    async def module_info(self, module_id: MechanicsModuleId) -> ModuleManifest | None:
        record = self._registry.get(module_id)
        return record.manifest if record else None

    def installed(self) -> list[RegisteredModule]:
        return self._registry.list()

    # ------------------------------------------------------------------
    # Module-scoped lookups (not bound to a campaign) — used by the
    # Frontend so it can render sheets without first picking a campaign.
    # ------------------------------------------------------------------

    def sheet_schema_for_module(
        self,
        module_id: MechanicsModuleId,
        entity_kind: str,
    ) -> JsonSchema | None:
        """Return ``entity_kind``'s sheet schema for ``module_id`` directly.

        Unlike :meth:`sheet_schema` this skips the campaign lookup so the
        Frontend can render a schema before a campaign has bound the
        module.
        """
        record = self._registry.get(module_id)
        if record is None:
            return None
        return record.instance.sheet_schema(entity_kind)

    def theme_css_for_module(self, module_id: MechanicsModuleId) -> str:
        """Return the raw CSS body for ``module_id``'s ``ui.theme_css``.

        Returns ``""`` when the module has no ``theme_css`` declared, the
        referenced file is missing, or the module is not registered. The
        path is resolved relative to the module directory recorded at load
        time; tests that ``register_module`` directly without a directory
        get an empty string back.
        """
        record = self._registry.get(module_id)
        if record is None:
            return ""
        ui = record.manifest.ui or {}
        ref = ui.get("theme_css") if isinstance(ui, dict) else None
        if not isinstance(ref, str) or not ref:
            return ""
        module_dir = record.module_dir
        if module_dir is None:
            return ""
        target = (module_dir / ref).resolve()
        # Guard against ``..`` escaping the module directory.
        try:
            target.relative_to(module_dir.resolve())
        except ValueError:
            return ""
        if not target.is_file():
            return ""
        try:
            return target.read_text(encoding="utf-8")
        except OSError:
            return ""

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _campaign_seed(
        self,
        campaign_id: CampaignId,
    ) -> int:
        if not self._config.rng.per_campaign_seed:
            return 0
        # SHA-256 of the campaign id gives a deterministic per-campaign seed
        # that survives process restarts (Python's hash() does not).
        digest = hashlib.sha256(campaign_id.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF


def _id_or_dirname(discovered: Any) -> str:
    """``raw_manifest["id"]`` if it's a non-empty string, else the dir name.

    ``dict.get(key, default)`` only uses ``default`` when the key is absent
    — a manifest with ``id: null`` would otherwise yield ``None``.
    """
    raw = getattr(discovered, "raw_manifest", None) or {}
    value = raw.get("id") if isinstance(raw, dict) else None
    if isinstance(value, str) and value:
        return value
    return discovered.module_dir.name


def _parse_entity_ref(
    entity_ref: str,
    fallback_kind: str | None = None,
) -> tuple[str, str]:
    """Best-effort split of an entity_ref into ``(kind, entity_id)``.

    Accepted forms (in order of preference):

    * ``"<kind>:<entity_id>"`` — e.g. ``"character:hyde-smythe"``
    * ``"<kind>/<entity_id>"`` — e.g. ``"character/hyde-smythe"``
    * ``"library:worlds/<world>/<plural-kind>/<asset>"`` —
      handled via :class:`EntityRef.parse`.
    * Plain id with ``fallback_kind`` supplied.
    """
    if ":" in entity_ref and not entity_ref.startswith(("library:", "campaign:")):
        kind, _, rest = entity_ref.partition(":")
        if kind and rest:
            return kind, rest
    if "/" in entity_ref and not entity_ref.startswith(("library:", "campaign:")):
        kind, _, rest = entity_ref.partition("/")
        if kind and rest:
            return kind, rest
    if entity_ref.startswith(("library:", "campaign:")):
        from grimoire.types.common import EntityRef

        parsed = EntityRef.parse(entity_ref)
        return parsed.kind.value, parsed.asset_id
    if fallback_kind is not None:
        return fallback_kind, entity_ref
    return _SHEET_KIND_DEFAULT, entity_ref


def _deep_merge(base: dict, patch: dict) -> dict:
    """Deep-merge ``patch`` onto ``base`` and return a new dict.

    Nested dicts are merged recursively. Lists and scalars from ``patch``
    replace the value in ``base``.
    """
    if not isinstance(base, dict):
        return dict(patch) if isinstance(patch, dict) else patch
    out = dict(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


__all__ = [
    "ActiveModuleResolver",
    "MechanicsService",
    "RescanReport",
]
