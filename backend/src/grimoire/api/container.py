"""Service container for the REST + WebSocket API surface.

Holds references to the long-lived service instances the routers need. Services
are wired in :func:`grimoire.main.create_app` and looked up by FastAPI
dependencies in each router. Services are optional so tests can populate only
what they need; an endpoint that requires an absent service returns ``503``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from grimoire.api.stream import StreamManager
    from grimoire.characters import CharactersService
    from grimoire.characters.integration import CharactersIntegration
    from grimoire.context.builder import ContextBuilderService
    from grimoire.context.inspector import ContextInspector
    from grimoire.continuity import ContinuityRegistry
    from grimoire.event_bus import EventBus
    from grimoire.export.service import ExportService
    from grimoire.expressions.service import ExpressionStateService
    from grimoire.extractor.service import ExtractorService
    from grimoire.extras import ExtrasService as _ExtrasService
    from grimoire.hud.config import HudConfigService
    from grimoire.hud.service import HudService
    from grimoire.imagegen import (
        ImageGenHealthProber,
        ImageGenIntegration,
        ImageGenService,
    )
    from grimoire.inventory import InventoryService
    from grimoire.library import LibraryService
    from grimoire.lifecycle import LifecycleManager
    from grimoire.llm_gateway.gateway import LLMGatewayService
    from grimoire.mechanics import MechanicsService
    from grimoire.observability.service import ObservabilityService
    from grimoire.orchestrator.service import OrchestratorService
    from grimoire.plugins import PluginsService
    from grimoire.scenes import SceneManager
    from grimoire.scenes.indexer import SceneIndexer
    from grimoire.scenes.ledger import SceneLedger
    from grimoire.scenes.summary_jobs import RunningSummaryWorker
    from grimoire.state_store import (
        BackupScheduler,
        BodySummarizer,
        EmbeddingWorker,
        RetentionSweeper,
        StateStore,
        StateStoreConfig,
    )
    from grimoire.storage import Database
    from grimoire.time_engine.service import TimeEngineService
    from grimoire.time_engine.subscriber import TimeEngineSubscriber
    from grimoire.transient_state import TransientStateService
    from grimoire.watcher.watcher import FileWatcher
    from grimoire.world import WorldService
    from grimoire.world.calendar_service import CalendarService


@dataclass
class ServiceContainer:
    """Bag of services available to API routers."""

    # Infrastructure
    db: Database | None = None
    event_bus: EventBus | None = None
    stream: StreamManager | None = None
    lifecycle: LifecycleManager | None = None

    # Core domain services
    library: LibraryService | None = None
    world: WorldService | None = None
    characters: CharactersService | None = None
    scenes: SceneManager | None = None
    continuity: ContinuityRegistry | None = None
    time_engine: TimeEngineService | None = None
    imagegen: ImageGenService | None = None
    export: ExportService | None = None
    mechanics: MechanicsService | None = None
    plugins: PluginsService | None = None
    state_store: StateStore | None = None
    orchestrator: OrchestratorService | None = None
    observability: ObservabilityService | None = None
    hud: HudService | None = None
    hud_config: HudConfigService | None = None
    transient_state: TransientStateService | None = None
    inventory: InventoryService | None = None
    extras_service: _ExtrasService | None = None
    """``grimoire.extras.ExtrasService`` -- narrative extras CRUD + search."""
    calendar: CalendarService | None = None
    """``grimoire.world.calendar_service.CalendarService``: multi-calendar + holiday surface."""

    # LLM-adjacent services (previously in extras dict)
    llm_gateway: LLMGatewayService | None = None
    extractor: ExtractorService | None = None
    context_builder: ContextBuilderService | None = None

    # Background workers (previously in extras dict)
    file_watcher: FileWatcher | None = None
    scene_indexer: SceneIndexer | None = None
    scene_ledger: SceneLedger | None = None
    embedding_worker: EmbeddingWorker | None = None
    body_summarizer: BodySummarizer | None = None
    retention_sweeper: RetentionSweeper | None = None
    backup_scheduler: BackupScheduler | None = None
    scene_summary_worker: RunningSummaryWorker | None = None

    # Integration subscribers (previously in extras dict)
    imagegen_integration: ImageGenIntegration | None = None
    imagegen_health_prober: ImageGenHealthProber | None = None
    characters_integration: CharactersIntegration | None = None
    time_engine_subscriber: TimeEngineSubscriber | None = None

    # Config / diagnostics (previously in extras dict)
    state_store_config: StateStoreConfig | None = None
    mechanics_rescan_error: str | None = None
    plugins_rescan_error: str | None = None
    sync_status: str = "syncing"
    sync_error: str | None = None

    # Lazy-init services (previously in extras dict)
    expressions: ExpressionStateService | None = None
    context_inspector: ContextInspector | None = None


__all__ = ["ServiceContainer"]
