"""Mechanics module (spec 06).

The Mechanics module owns the game-system layer: discovers and loads
mechanics modules from ``data/mechanics/``, exposes the
:class:`MechanicsModule` protocol they implement, and provides a façade
that the Orchestrator / Extractor / Time Engine / Context Builder call
into per campaign.

No mechanics modules ship with Grimoire — this package is the API
contract and the loader, not a system implementation.
"""

from grimoire.mechanics.config import (
    DefaultsConfig,
    MechanicsConfig,
    RngConfig,
    ValidationConfig,
)
from grimoire.mechanics.discovery import (
    DiscoveredModule,
    DiscoveryError,
    discover,
)
from grimoire.mechanics.loader import (
    DEFAULT_ENTRY_CANDIDATES,
    LoadResult,
    load_module,
    satisfies_mechanics_protocol,
)
from grimoire.mechanics.null import NULL_MECHANICS_ID, NullMechanicsModule
from grimoire.mechanics.registry import MechanicsRegistry, RegisteredModule
from grimoire.mechanics.rng import derive_roll_seed
from grimoire.mechanics.service import (
    ActiveModuleResolver,
    MechanicsService,
    RescanReport,
)

__all__ = [
    "DEFAULT_ENTRY_CANDIDATES",
    "NULL_MECHANICS_ID",
    "ActiveModuleResolver",
    "DefaultsConfig",
    "DiscoveredModule",
    "DiscoveryError",
    "LoadResult",
    "MechanicsConfig",
    "MechanicsRegistry",
    "MechanicsService",
    "NullMechanicsModule",
    "RegisteredModule",
    "RescanReport",
    "RngConfig",
    "ValidationConfig",
    "derive_roll_seed",
    "discover",
    "load_module",
    "satisfies_mechanics_protocol",
]
