"""Service container for the REST + WebSocket API surface.

Holds references to the long-lived service instances the routers need. Services
are wired in :func:`grimoire.main.create_app` and looked up by FastAPI
dependencies in each router. Services are optional so tests can populate only
what they need; an endpoint that requires an absent service returns ``503``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from grimoire.api.stream import StreamManager
    from grimoire.event_bus import EventBus
    from grimoire.storage import Database


@dataclass
class ServiceContainer:
    """Bag of services available to API routers.

    Each attribute is typed loosely (``Any``) to avoid pulling heavy module
    imports into the API layer; the routers depend on duck-typed protocols.
    """

    db: Database | None = None
    event_bus: EventBus | None = None
    stream: StreamManager | None = None

    library: Any = None
    world: Any = None
    characters: Any = None
    scenes: Any = None
    continuity: Any = None
    time_engine: Any = None
    imagegen: Any = None
    export: Any = None
    mechanics: Any = None
    plugins: Any = None
    state_store: Any = None
    orchestrator: Any = None
    observability: Any = None
    hud: Any = None
    hud_config: Any = None

    extras: dict[str, Any] = field(default_factory=dict)


__all__ = ["ServiceContainer"]
