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
from typing import Any

from grimoire.mechanics.config import MechanicsConfig
from grimoire.mechanics.discovery import DiscoveryError, discover
from grimoire.mechanics.loader import load_module
from grimoire.mechanics.null import NULL_MECHANICS_ID, NullMechanicsModule
from grimoire.mechanics.registry import MechanicsRegistry, RegisteredModule
from grimoire.mechanics.rng import derive_roll_seed
from grimoire.state_store.errors import NotFoundError
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
    Capability,
    ModuleManifest,
    NarratedEvent,
    ProposedRoll,
    Roll,
    RollResult,
    TickContext,
)
from grimoire.types.protocols import MechanicsModule
from grimoire.types.scene import SceneContext

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
    ) -> None:
        self._config = config
        self._state_store = state_store
        self._registry = MechanicsRegistry()
        self._discovery_errors: list[DiscoveryError] = []
        self._failed: dict[str, list[str]] = {}
        self._null = NullMechanicsModule()

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

        for d in discovered:
            module_id = d.raw_manifest.get("id") if isinstance(d.raw_manifest, dict) else None
            if not isinstance(module_id, str):
                module_id = d.module_dir.name
            seen.add(module_id)
            result = load_module(d)
            if result.ok and result.manifest is not None and result.instance is not None:
                self._registry.register(
                    result.manifest, result.instance, module_dir=d.module_dir
                )
                loaded.append(module_id)
            else:
                self._registry.unregister(module_id)
                reason = "; ".join(result.errors) or "unknown load error"
                failed.append((module_id, reason))
                new_failed[module_id] = list(result.errors)

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
        return module.sheet_schema(entity_kind)

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
        branch_id: str | None = None,
    ) -> RollResult:
        """Resolve a roll with a seed derived from the branch's RNG seed."""
        module = await self.active_module(campaign_id)
        if module is None:
            return self._null.resolve_roll(roll, roll.seed)
        branch_seed = await self._branch_seed(campaign_id, branch_id)
        derived = derive_roll_seed(branch_seed, roll.seed, roll.id)
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
            branch_id=f"{campaign_id}:main",
            duration=duration,
        )
        return module.time_tick(entity_ref, sheet or {}, duration, ctx)

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

    async def _branch_seed(
        self,
        campaign_id: CampaignId,
        branch_id: str | None,
    ) -> int:
        if not self._config.rng.per_branch_seed:
            return 0
        target_id = branch_id or f"{campaign_id}:main"
        row = await self._state_store.db.fetchone(
            "SELECT rng_seed FROM branches WHERE id = ?",
            (target_id,),
        )
        if row is None:
            # Built-in hash() is per-process randomized, which would
            # defeat replay determinism — derive the fallback from
            # SHA-256 instead.
            digest = hashlib.sha256(target_id.encode("utf-8")).digest()
            return int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF
        return int(row["rng_seed"])


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
