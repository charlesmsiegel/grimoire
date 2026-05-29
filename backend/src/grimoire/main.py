import asyncio
import logging
import os
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from grimoire import __version__
from grimoire.api.alternates import router as alternates_router
from grimoire.api.auxiliary import router as auxiliary_router
from grimoire.api.calendars import router as calendars_router
from grimoire.api.campaigns import router as campaigns_router
from grimoire.api.config import router as config_router
from grimoire.api.container import ServiceContainer
from grimoire.api.context import router as context_router
from grimoire.api.expressions import router as expressions_router
from grimoire.api.extras import router as extras_router
from grimoire.api.health import router as health_router
from grimoire.api.hud import router as hud_router
from grimoire.api.imagegen import router as imagegen_router
from grimoire.api.imports import router as imports_router
from grimoire.api.library import router as library_router
from grimoire.api.observability import router as observability_router
from grimoire.api.setup import router as setup_router
from grimoire.api.stream import StreamManager
from grimoire.api.templates import router as templates_router
from grimoire.api.transient_state import router as transient_state_router
from grimoire.api.ws import router as ws_router
from grimoire.characters import CharactersService
from grimoire.characters.integration import CharactersIntegration
from grimoire.config import settings
from grimoire.context.builder import ContextBuilderService
from grimoire.context.inspector import ContextInspector
from grimoire.continuity import (
    ContinuityConfig,
    ContinuityRegistry,
    ContinuityRegistryExportAdapter,
    make_judge_request_factory,
)
from grimoire.event_bus import EventBus
from grimoire.export.epub import EpubAdapter
from grimoire.export.service import ExportService, ExportServiceConfig
from grimoire.export.sources import DataSources
from grimoire.extractor.llm_strategy import parse_llm_payload
from grimoire.extractor.schema import output_schema
from grimoire.extractor.service import ExtractorService
from grimoire.imagegen import (
    BackendRegistry,
    ImageGenConfig,
    ImageGenHealthProber,
    ImageGenIntegration,
    ImageGenService,
)
from grimoire.library import LibraryConfig, LibraryService
from grimoire.llm_gateway.gateway import LLMGatewayService
from grimoire.mechanics import MechanicsConfig, MechanicsService
from grimoire.mechanics.file_watcher import MechanicsFileWatcher
from grimoire.observability.replayer import TurnReplayerService
from grimoire.observability.service import ObservabilityService
from grimoire.orchestrator.service import OrchestratorService
from grimoire.plugins import PluginsConfig, PluginsService
from grimoire.scenes import SceneManager
from grimoire.scenes.analysis import make_adaptive_scene_analyzer
from grimoire.scenes.default_summarizers import (
    make_default_final_summarizer,
    make_default_running_summarizer,
)
from grimoire.scenes.indexer import SceneIndexer
from grimoire.scenes.summary_jobs import RunningSummaryWorker
from grimoire.state_store import (
    BackupScheduler,
    BodySummarizer,
    EmbeddingWorker,
    RetentionSweeper,
    StateStore,
    StateStoreConfig,
    reenqueue_missing_embeddings,
)
from grimoire.storage import Database, apply_migrations
from grimoire.time_engine.service import TimeEngineService
from grimoire.time_engine.subscriber import TimeEngineSubscriber
from grimoire.watcher.watcher import FileWatcher
from grimoire.world import WorldConfig, WorldService

log = logging.getLogger(__name__)

_SEED_ROOT = Path(__file__).resolve().parent / "seed" / "library"


class _ImageGenCoverGenerator:
    """Adapter from :class:`ImageGenService` to the CoverGenerator protocol.

    EPUB auto-cover (§6) calls ``generate_sync`` on the active backend for
    the campaign; failures are swallowed and reported back as ``None`` so
    the export falls through to the plain title page.
    """

    def __init__(self, imagegen: ImageGenService) -> None:
        self._imagegen = imagegen

    async def generate_cover(self, campaign_id: str, prompt: str) -> bytes | None:
        from grimoire.types.imagegen import GenerationRequest

        try:
            result = await self._imagegen.generate_sync(
                campaign_id,
                GenerationRequest(prompt=prompt, width=1024, height=1536),
            )
        except Exception as exc:
            log.warning("cover generation failed: %r", exc)
            return None
        if result.error:
            return None
        return result.image_bytes or None


def _seed_defaults(data_root: Path) -> None:
    """Copy bundled default library assets into the user's data root.

    Skips files the user already has (size > 0) so edits and additions are
    preserved. A zero-byte file at the destination is treated as a leftover
    from a previous crashed copy and overwritten. The copy itself writes to
    a sibling temp file and ``os.replace``s it into place so a crash mid-
    write cannot leave a partial seed file behind. Called once per startup
    before the initial library scan.
    """
    if not _SEED_ROOT.is_dir():
        return
    library_root = data_root / "library"
    for src in _SEED_ROOT.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(_SEED_ROOT)
        dst = library_root / rel
        if dst.exists() and dst.stat().st_size > 0:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f".{dst.name}.tmp")
        try:
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
        finally:
            if tmp.exists():
                with suppress(OSError):
                    tmp.unlink()
        log.info("seeded default %s", rel)


async def _background_reconcile(container: ServiceContainer, *, scan_library: bool = True) -> None:
    """Run backfill + scan_now in the background after the server starts serving."""
    await asyncio.sleep(1.0)
    errors: list[str] = []
    try:
        scene_indexer = container.scene_indexer
        if scene_indexer is not None:
            try:
                await scene_indexer.backfill()
            except Exception as exc:
                log.exception("background scene indexer backfill failed")
                errors.append(f"backfill: {exc}")

        if scan_library:
            file_watcher = container.file_watcher
            if file_watcher is not None:
                try:
                    await file_watcher.scan_now()
                except Exception as exc:
                    log.exception("background library scan failed")
                    errors.append(f"scan: {exc}")

            if (
                container.state_store_config is not None
                and container.state_store_config.library.embed_on_index
                and file_watcher is not None
            ):
                try:
                    await reenqueue_missing_embeddings(
                        container.state_store, file_watcher.embedding_queue
                    )
                except Exception as exc:
                    log.exception("reenqueue_missing_embeddings failed")
                    errors.append(f"reenqueue: {exc}")

        container.sync_status = "ready"
        container.sync_error = "; ".join(errors) if errors else None
        suffix = f" with errors: {errors}" if errors else ""
        log.info("background reconciliation complete%s", suffix)
    except Exception as exc:
        log.exception("background reconciliation failed")
        container.sync_status = "ready"
        container.sync_error = f"{type(exc).__name__}: {exc}"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    container: ServiceContainer | None = getattr(app.state, "container", None)
    _prewired = container is not None
    # If the caller pre-wired a container with a state_store, reuse its db
    # so any already-attached services (transient_state, library, …) keep
    # pointing at the same connection pool. Otherwise opening a fresh db
    # here would split-brain against those pre-wired services.
    if container is not None and container.state_store is not None:
        db = container.state_store.db
        owned_db = False
    else:
        db = Database(
            settings.resolved_database_path,
            pool_size=settings.db_pool_size,
            enable_wal=settings.enable_wal,
        )
        await db.connect()
        owned_db = True
    try:
        # Wrap every step of startup so a failure anywhere (migration error,
        # malformed seed file, service init bug) still closes the connection
        # pool and aclose()'s any services we managed to construct.
        await apply_migrations(db)
        app.state.db = db

        if container is None:
            container = ServiceContainer(db=db)
        container.db = db
        if container.event_bus is None:
            container.event_bus = EventBus()
        if container.stream is None:
            container.stream = StreamManager(event_bus=container.event_bus)

        data_root = settings.data_root
        for sub in ("library", "mechanics", "plugins", "config/plugins", "templates"):
            (data_root / sub).mkdir(parents=True, exist_ok=True)

        # State Store config: layered (file overlaid on bootstrap settings).
        # Stashed on the container so the embedding worker / backup scheduler /
        # retention sweep can read it without re-parsing.
        state_store_config = StateStoreConfig.from_yaml(
            data_root / "config" / "state_store.yaml",
            data_root=data_root,
            database_path=settings.resolved_database_path,
            enable_wal=settings.enable_wal,
        )
        container.state_store_config = state_store_config

        # User-supplied prompt template variants live under {data_root}/templates
        # and take precedence over the bundled defaults so a user can drop in a
        # new variant and select it without rebuilding the package.
        from grimoire.templates import registry as template_registry

        template_registry.register_search_path(data_root / "templates", prepend=True)

        if container.state_store is None:
            container.state_store = StateStore(
                db=db, data_root=data_root, event_bus=container.event_bus
            )
            await container.state_store.validate_schema()
        if container.transient_state is None:
            from grimoire.transient_state import TransientStateService
            from grimoire.transient_state.config import TransientStateConfig
            from grimoire.transient_state.triggers import attach_triggers

            ts_cfg = TransientStateConfig.from_yaml(data_root / "config" / "transient.yaml")
            container.transient_state = TransientStateService(container.state_store, config=ts_cfg)
            if container.event_bus is not None:
                attach_triggers(container.transient_state, container.event_bus)
        library_cfg = LibraryConfig.from_yaml(data_root / "config" / "library.yaml")
        if container.library is None:
            container.library = LibraryService(container.state_store, config=library_cfg)
        # ExtrasService needs both library + a real state_store (for the
        # SQLite mirror). API tests that hand-wire a fake library without
        # a store leave state_store as None; skip construction there and
        # let the routes return 503 via the standard ExtrasServiceDep.
        if container.extras_service is None and container.state_store is not None:
            from grimoire.extras import ExtrasService

            container.extras_service = ExtrasService(
                library=container.library,
                store=container.state_store,
            )
        if container.world is None:
            world_cfg = WorldConfig.from_yaml(data_root / "config" / "world.yaml")
            container.world = WorldService(container.library, config=world_cfg)
        if container.calendar is None:
            from grimoire.world.calendar_service import CalendarService

            container.calendar = CalendarService(container.library)
        if container.mechanics is None:
            container.mechanics = MechanicsService(
                MechanicsConfig.for_data_root(data_root),
                state_store=container.state_store,
                event_bus=container.event_bus,
            )
            try:
                await container.mechanics.rescan()
                container.mechanics_rescan_error = None
            except Exception as exc:
                # An empty MechanicsService is still installed; without a
                # surfaced signal beyond the log line, endpoints look like
                # the user simply hasn't installed any modules. Stash the
                # error so /health / debug endpoints can report it.
                log.exception("mechanics rescan failed at startup")
                container.mechanics_rescan_error = f"{type(exc).__name__}: {exc}"
        if container.plugins is None:
            container.plugins = PluginsService(
                PluginsConfig.for_data_root(data_root),
                event_bus=container.event_bus,
            )
            try:
                await container.plugins.rescan()
                container.plugins_rescan_error = None
            except Exception as exc:
                log.exception("plugins rescan failed at startup")
                container.plugins_rescan_error = f"{type(exc).__name__}: {exc}"
            # Kick off the periodic health loop so plugin_health_changed
            # events flow even when no UI request triggers a probe.
            try:
                await container.plugins.start_periodic_health()
            except Exception:
                log.exception("plugins periodic health loop start failed")
        if container.characters is None:
            container.characters = CharactersService(
                container.library, container.mechanics, event_bus=container.event_bus
            )
        if container.scenes is None:
            # Continuity registry is wired below; SceneManager picks it up
            # via the attribute set after that block so the pre-scene
            # briefing can run.
            container.scenes = SceneManager(
                data_root,
                event_bus=container.event_bus,
                state_store=container.state_store,
            )
        # §464: wire the pending-cast-change store so the Scene Manager can
        # queue/confirm/dismiss cast changes detected during play.
        if container.scenes is not None:
            from grimoire.scenes.cast_changes import CastChangeStore

            container.scenes.set_cast_change_store(CastChangeStore(db))
        # Scene indexer keeps the SQLite scenes/posts tables in sync with the
        # markdown + sidecar source-of-truth. Subscribed to manager events
        # post-construction; backfill walks the disk once to catch any
        # direct-edit-while-down deltas.
        if container.scene_indexer is None and container.state_store is not None:
            scene_indexer = SceneIndexer(
                container.scenes, container.state_store.db, container.event_bus
            )
            scene_indexer.start()
            container.scene_indexer = scene_indexer
        if container.scene_ledger is None:
            from grimoire.scenes.ledger import SceneLedger

            container.scene_ledger = SceneLedger(db)
        if container.continuity is None:
            # The registry hands out one ContinuityService per
            # campaign_id backed by SqliteContinuityStore so
            # facts survive restart. HybridFactSearchIndex + (when the
            # gateway is available) LLMContradictionJudge are wired in
            # below once the gateway is constructed.
            continuity_config = ContinuityConfig()
            container.continuity = ContinuityRegistry(
                db=db,
                config=continuity_config,
                event_bus=container.event_bus,
            )
        # Hand the (possibly already-constructed) SceneManager the
        # registry so it can attach a pre-scene briefing to
        # ``scene_started`` events. SceneManager itself accepts a
        # ``continuity`` kwarg at construction but the bag is built
        # earlier — patching the attribute here keeps the wiring linear.
        if container.scenes is not None and getattr(container.scenes, "_continuity", None) is None:
            container.scenes._continuity = container.continuity
        if container.inventory is None:
            from grimoire.inventory import InventoryService

            container.inventory = InventoryService(
                store=container.state_store,
                event_bus=container.event_bus,
            )
        if container.imagegen is None:
            # No image-generation backends registered. /images endpoints (read)
            # work against the SQLite index; queue_generation / active_backend
            # will raise NoBackendAvailableError until a backend plugin is
            # installed and registered with the registry. Routes that need a
            # backend should catch that and 503 with a clear message.
            imagegen_cfg = ImageGenConfig.from_yaml(data_root / "config" / "imagegen.yaml")
            container.imagegen = ImageGenService(
                store=container.state_store,
                registry=BackendRegistry(),
                default_backend_id=None,
                event_bus=container.event_bus,
                config=imagegen_cfg,
            )
        if container.imagegen_integration is None and container.event_bus is not None:
            integration = ImageGenIntegration(container.imagegen, container.event_bus)
            integration.start()
            container.imagegen_integration = integration
        if container.imagegen_health_prober is None:
            prober = ImageGenHealthProber(container.imagegen, interval_seconds=30.0)
            prober.start()
            container.imagegen_health_prober = prober
        # §8 Reload any jobs that were queued at shutdown.
        try:
            await container.imagegen.reload_pending_jobs()
        except Exception:
            log.exception("imagegen: reload_pending_jobs failed at startup")

        # LLM-adjacent services: gateway + extractor + context builder are
        # the substrate the orchestrator drives. They're wired even when no
        # LLM provider plugin is installed — calls that route through the
        # gateway will raise a clear "no provider" error rather than blowing
        # up at construction time, so the rest of the routes (library,
        # images, worlds, etc.) keep working.
        if container.observability is None:
            obs = ObservabilityService(db=db, event_bus=container.event_bus)
            container.observability = obs
            # Backfill producers constructed before the observability service.
            # See §4.3 of the design — _NullMetrics() is harmless if left in
            # place but the real registry is what makes data show up.
            if container.state_store is not None:
                container.state_store._metrics = obs.metrics()
            if container.scenes is not None:
                container.scenes._metrics = obs.metrics()
            if container.imagegen is not None:
                container.imagegen._metrics = obs.metrics()
        else:
            obs = container.observability

        # Scene HUD aggregator + config persistence. The fetcher registry
        # is populated lazily by the wiring callers; the bare service is
        # enough to expose config CRUD and ``/widgets/available``.
        if container.hud_config is None:
            from grimoire.hud.config import HudConfigService

            container.hud_config = HudConfigService(data_root=data_root / "campaigns")
        if container.hud is None:
            from grimoire.hud.service import HudService

            container.hud = HudService(config_service=container.hud_config)
        try:
            from grimoire.hud.fetchers import register_default_fetchers

            register_default_fetchers(
                container.hud,
                hud_config=container.hud_config,
                library=container.library,
                extras=container.extras_service,
                continuity=container.continuity,
                scenes=container.scenes,
                world=container.world,
                store=container.state_store,
            )
        except Exception:
            log.exception("hud: failed to register default fetchers")
        # §11: register imagegen backends with the observability health
        # monitor. The LLM gateway registers itself via
        # ``register_with_health_monitor`` below; embedding providers are
        # registered by the same gateway call.
        if container.imagegen is not None:
            try:
                container.imagegen.register_with_health_monitor(obs.health_monitor)
            except Exception:
                log.exception("imagegen register_with_health_monitor failed")
        if container.llm_gateway is None:
            container.llm_gateway = LLMGatewayService(
                plugins=container.plugins,
                db=db,
                config=settings.llm_gateway.to_gateway_config(),
                data_root=settings.data_root,
                event_bus=container.event_bus,
                health_monitor=obs.health_monitor,
                metrics=obs.metrics(),
            )
            await container.llm_gateway.register_with_health_monitor()
            # Wizard-configured plugins ship with an `active_model` but nothing
            # else wires them into the gateway's per-task routing — bridge the
            # gap so wizard-only installs can post turns / embed text / etc.
            # without the user also setting env vars or a campaign YAML.
            try:
                await container.llm_gateway.register_provider_defaults()
            except Exception:
                log.exception("register_provider_defaults failed at startup")
        llm_gateway = container.llm_gateway
        # ImageGenService is constructed earlier (before the gateway exists);
        # wire the gateway in now so per-task imagegen routing works.
        if container.imagegen is not None:
            container.imagegen.set_gateway(llm_gateway)
        if obs.replayer is None:
            obs.replayer = TurnReplayerService(
                audit_store=obs.audit_store,
                gateway=llm_gateway,
            )
        await obs.start()
        await obs.health_monitor.start_periodic()
        await obs.retention.start_periodic()
        # §3 wire the gateway into WorldService so create_world can
        # auto-generate atmosphere blocks. WorldService is constructed
        # before the gateway exists, so we patch the attribute here.
        if container.world is not None and getattr(container.world, "gateway", None) is None:
            container.world.gateway = llm_gateway
        # §4/§5 default summarizers — only attach if the caller didn't already
        # inject custom ones. The gateway only raises at call-time when no
        # provider plugin is registered, so wiring is safe even on a bare
        # install.
        if getattr(container.scenes, "_summarizer", None) is None:
            container.scenes._summarizer = make_default_running_summarizer(
                llm_gateway,
                max_tokens=container.scenes.config.running_summary.max_tokens,
                model=container.scenes.config.running_summary.model or "default",
            )
        if getattr(container.scenes, "_final_summarizer", None) is None:
            container.scenes._final_summarizer = make_default_final_summarizer(
                llm_gateway,
                max_tokens=container.scenes.config.running_summary.max_tokens,
                model=container.scenes.config.running_summary.model or "default",
            )
        if getattr(container.scenes, "_scene_analyzer", None) is None:
            container.scenes.set_scene_analyzer(
                make_adaptive_scene_analyzer(
                    llm_gateway,
                    extraction_schema_fn=output_schema,
                    payload_parser=parse_llm_payload,
                )
            )
        # Background worker drains running_summary_due events so a slow LLM
        # call doesn't block the next append. Coalesces per-scene FIFO.
        if container.scene_summary_worker is None:
            worker = RunningSummaryWorker(container.scenes, container.event_bus)
            worker.start()
            container.scene_summary_worker = worker
        if container.extractor is None:
            container.extractor = ExtractorService(gateway=llm_gateway, metrics=obs.metrics())
        extractor = container.extractor
        # §3: Now that the gateway exists, wire it through the
        # ContinuityRegistry so per-campaign services get a real LLM
        # judge (instead of the always-uncertain stub) and a vector
        # embedder for HybridFactSearchIndex. Construct in place so the
        # already-instantiated registry — and any cached services it has
        # already handed out — pick up the change on first use; the
        # registry only caches services that have actually been
        # requested, and api/lifespan order means none have yet.
        if isinstance(container.continuity, ContinuityRegistry):
            registry = container.continuity
            registry._embedder = llm_gateway
            registry._judge_gateway = llm_gateway
            registry._judge_request_factory = make_judge_request_factory(
                registry.config.contradiction_check.model_route
            )
        if container.context_builder is None:
            container.context_builder = ContextBuilderService(
                library=container.library,
                characters=container.characters,
                world=container.world,
                scenes=container.scenes,
                continuity=container.continuity,
                mechanics=container.mechanics,
                gateway=llm_gateway,
                state_store=container.state_store,
                transient_state=container.transient_state,
                metrics=obs.metrics(),
            )
        context_builder = container.context_builder

        if container.context_inspector is None:
            container.context_inspector = ContextInspector(
                builder=context_builder,
                store=container.state_store,
                observability=obs,
            )

        # Time engine: every dep is already constructed above.
        if container.time_engine is None:
            container.time_engine = TimeEngineService(
                store=container.state_store,
                world=container.world,
                characters=container.characters,
                mechanics=container.mechanics,
                continuity=container.continuity,
                event_bus=container.event_bus,
                metrics=obs.metrics(),
            )
        # §1 (time-engine remaining): wire the orchestrator's
        # ``turn_complete`` event to drive Time Engine advances. The
        # subscription lives on the container extras so shutdown can
        # disengage it cleanly.
        if container.time_engine_subscriber is None:
            subscriber = TimeEngineSubscriber(
                time_engine=container.time_engine,
                event_bus=container.event_bus,
            )
            subscriber.start()
            container.time_engine_subscriber = subscriber

        # Export: scenes is the only required source; others use the bundled
        # services as duck-typed sources. Adapters come from the plugin
        # registry, so installing an export plugin makes it available to
        # /export without further wiring.
        if container.export is None:
            cover_generator = _ImageGenCoverGenerator(container.imagegen)
            continuity_export: Any = container.continuity
            if isinstance(continuity_export, ContinuityRegistry):
                continuity_export = ContinuityRegistryExportAdapter(continuity_export)
            sources = DataSources(
                scenes=container.scenes,
                characters=container.characters,
                world=container.world,
                continuity=continuity_export,
                images=container.imagegen,
                cover_generator=cover_generator,
                data_root=data_root,
            )
            export_cfg = settings.export
            epub_adapter = EpubAdapter(
                sources,
                config=export_cfg.adapters.epub,
                filter_defaults=export_cfg.filters,
            )
            # Spec 13 names EPUB as the v1 priority format; we ship it as a
            # built-in alongside any plugin adapters the registry loaded.
            container.export = ExportService(
                sources=sources,
                adapters=[epub_adapter, *container.plugins.export_adapters()],
                config=ExportServiceConfig.from_export_config(export_cfg),
                state_store=container.state_store,
            )

        # Orchestrator ties the play loop together. ws_push forwards
        # streaming tokens and lifecycle events to subscribed WebSocket
        # clients via StreamManager.
        if container.orchestrator is None:
            container.orchestrator = OrchestratorService(
                event_bus=container.event_bus,
                scene_manager=container.scenes,
                llm_gateway=llm_gateway,
                context_builder=context_builder,
                extractor=extractor,
                state_store=container.state_store,
                mechanics=container.mechanics,
                world=container.world,
                continuity=container.continuity,
                transient_state=container.transient_state,
                inventory=container.inventory,
                ws_push=container.stream.push,
                metrics=obs.metrics(),
            )

        if obs.replayer is not None:
            obs.replayer.set_forker(container.orchestrator)

        # Characters drift fan-out: subscribe to turn_complete and sample
        # drift checks on present characters. The cadence gate inside
        # CharactersService.maybe_check_drift is the source of truth.
        if (
            container.characters_integration is None
            and container.event_bus is not None
            and container.characters is not None
        ):
            chars_integration = CharactersIntegration(
                container.characters,
                container.scenes,
                container.event_bus,
            )
            chars_integration.start()
            container.characters_integration = chars_integration

        # Seed default library assets (style guides, etc.) and run one scan
        # so the library_index is populated. We don't start the live
        # watchdog observer here — file changes during runtime won't
        # auto-index, but in-app writes go through StateStore which updates
        # the index directly.
        _seed_defaults(data_root)
        if library_cfg.watch:
            file_watcher = FileWatcher(
                data_root=data_root,
                store=container.state_store,
                bus=container.event_bus,
                scene_manager=container.scenes,
                config=library_cfg,
            )
            container.file_watcher = file_watcher

        # State Store background workers — embedding drainer (§1), auto-backup
        # (§3), retention sweep (§4), body_compressed summarizer (§5). All
        # honor `state_store_config`; the embedding worker and summarizer
        # quietly back off when no LLM provider is registered yet.
        embedding_worker = EmbeddingWorker(
            store=container.state_store,
            gateway=llm_gateway,
            queue=file_watcher.embedding_queue,
            bus=container.event_bus,
            config=state_store_config.library,
        )
        if state_store_config.library.embed_on_index:
            await embedding_worker.start()
        else:
            log.info("embedding worker disabled (embed_on_index=false)")
        container.embedding_worker = embedding_worker

        body_summarizer = BodySummarizer(
            store=container.state_store,
            gateway=llm_gateway,
            queue=file_watcher.summary_queue,
            bus=container.event_bus,
        )
        if library_cfg.indexing.summarize_on_index:
            body_summarizer.start()
        else:
            log.info("body summarizer disabled (summarize_on_index=false)")
        container.body_summarizer = body_summarizer

        retention_sweeper = RetentionSweeper(
            db=db,
            config=state_store_config.retention,
            bus=container.event_bus,
        )
        await retention_sweeper.start()
        container.retention_sweeper = retention_sweeper

        backup_scheduler = BackupScheduler(
            data_root=data_root,
            database_path=settings.resolved_database_path,
            config=state_store_config.auto_backup,
            bus=container.event_bus,
        )
        backup_scheduler.start()
        container.backup_scheduler = backup_scheduler

        mechanics_watcher: MechanicsFileWatcher | None = None
        if container.mechanics.config.reload_on_file_change:
            mechanics_watcher = MechanicsFileWatcher(container.mechanics)
            try:
                await mechanics_watcher.start()
            except Exception:
                log.exception("mechanics file watcher failed to start")
                mechanics_watcher = None
        app.state.mechanics_watcher = mechanics_watcher

        app.state.container = container

        _bg_task: asyncio.Task | None = None
        _scan_library = library_cfg.scan_on_startup
        if _prewired:
            await _background_reconcile(container, scan_library=_scan_library)
        else:
            _bg_task = asyncio.create_task(
                _background_reconcile(container, scan_library=_scan_library),
                name="background-reconcile",
            )
    except Exception:
        # Tear down anything we managed to construct before re-raising,
        # otherwise the connection pool stays open and any partially-built
        # service that holds resources (imagegen workers, stream subscribers)
        # leaks for the life of the process.
        await _shutdown(container, db, close_db=owned_db)
        raise

    try:
        yield
    finally:
        if _bg_task is not None and not _bg_task.done():
            _bg_task.cancel()
            with suppress(asyncio.CancelledError):
                await _bg_task
        await _stop_mechanics_watcher(app)
        await _shutdown(container, db, close_db=owned_db)


async def _shutdown(
    container: ServiceContainer | None, db: Database, *, close_db: bool = True
) -> None:
    if container is not None:
        summary_worker = container.scene_summary_worker
        if summary_worker is not None:
            try:
                await summary_worker.stop()
            except Exception:
                log.exception("scene summary worker stop failed during shutdown")
        scene_indexer = container.scene_indexer
        if scene_indexer is not None:
            try:
                await scene_indexer.stop()
            except Exception:
                log.exception("scene indexer stop failed during shutdown")
        # Disengage the Time Engine ``turn_complete`` subscriber so the bus
        # doesn't keep forwarding events into a torn-down engine after the
        # lifespan ends.
        time_engine_subscriber = container.time_engine_subscriber
        if time_engine_subscriber is not None:
            try:
                time_engine_subscriber.stop()
            except Exception:
                log.exception("time engine subscriber stop failed during shutdown")

        # State Store background workers — stop before closing the DB so they
        # don't hit a closed connection mid-loop. Each is independently
        # try/excepted so one failure doesn't strand the others.
        backup_scheduler = container.backup_scheduler
        if backup_scheduler is not None:
            try:
                backup_scheduler.stop()
            except Exception:
                log.exception("backup_scheduler stop failed during shutdown")
        retention_sweeper = container.retention_sweeper
        if retention_sweeper is not None:
            try:
                await retention_sweeper.stop()
            except Exception:
                log.exception("retention_sweeper stop failed during shutdown")
        body_summarizer = container.body_summarizer
        if body_summarizer is not None:
            try:
                await body_summarizer.stop()
            except Exception:
                log.exception("body_summarizer stop failed during shutdown")
        embedding_worker = container.embedding_worker
        if embedding_worker is not None:
            try:
                await embedding_worker.stop()
            except Exception:
                log.exception("embedding_worker stop failed during shutdown")

        if container.observability is not None:
            try:
                await container.observability.shutdown()
            except Exception:
                log.exception("observability shutdown failed during shutdown")
        imagegen_integration = container.imagegen_integration
        if imagegen_integration is not None:
            try:
                imagegen_integration.stop()
            except Exception:
                log.exception("imagegen integration stop failed during shutdown")
        characters_integration = container.characters_integration
        if characters_integration is not None:
            try:
                characters_integration.stop()
            except Exception:
                log.exception("characters integration stop failed during shutdown")
        imagegen_health_prober = container.imagegen_health_prober
        if imagegen_health_prober is not None:
            try:
                await imagegen_health_prober.stop()
            except Exception:
                log.exception("imagegen health prober stop failed during shutdown")
        if container.plugins is not None:
            try:
                await container.plugins.stop_periodic_health()
            except Exception:
                log.exception("plugins periodic health stop failed during shutdown")
        if container.imagegen is not None:
            try:
                await container.imagegen.aclose()
            except Exception:
                log.exception("imagegen aclose failed during shutdown")
        if container.stream is not None:
            try:
                await container.stream.aclose()
            except Exception:
                log.exception("stream aclose failed during shutdown")
    if close_db:
        try:
            await db.close()
        except Exception:
            log.exception("db close failed during shutdown")


async def _stop_mechanics_watcher(app: FastAPI) -> None:
    watcher = getattr(app.state, "mechanics_watcher", None)
    if watcher is None:
        return
    try:
        await watcher.stop()
    except Exception:
        log.exception("mechanics watcher stop failed")


_DEFAULT_CORS_ORIGINS: tuple[str, ...] = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
)


def _resolve_cors_origins() -> list[str]:
    """Build the CORS allowlist from the env vars run.sh already exposes.

    GRIMOIRE_FRONTEND_ORIGINS wins outright (comma-separated). Otherwise we
    keep the defaults plus, if the user overrode the frontend host/port,
    the matching ``http://<host>:<port>`` so a non-default Vite dev server
    isn't blocked at the browser without any signal.
    """
    explicit = os.environ.get("GRIMOIRE_FRONTEND_ORIGINS")
    if explicit:
        origins = [o.strip() for o in explicit.split(",") if o.strip()]
        if origins:
            return origins
    origins = list(_DEFAULT_CORS_ORIGINS)
    host = os.environ.get("GRIMOIRE_FRONTEND_HOST")
    port = os.environ.get("GRIMOIRE_FRONTEND_PORT")
    if host or port:
        host = host or "127.0.0.1"
        port = port or "5173"
        extra = f"http://{host}:{port}"
        if extra not in origins:
            origins.append(extra)
    return origins


def create_app() -> FastAPI:
    app = FastAPI(title="Grimoire", version=__version__, lifespan=lifespan)

    # CORS allowlist: built from the same env vars that run.sh exposes so
    # pointing Vite at a non-default host or port (or via tunnel) doesn't
    # silently fall through to the default origins and 403 every request
    # without a hint as to why. Set GRIMOIRE_FRONTEND_ORIGINS to override
    # entirely (comma-separated).
    cors_origins = _resolve_cors_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router, prefix="/api")
    app.include_router(setup_router, prefix="/api")
    app.include_router(config_router, prefix="/api")
    app.include_router(library_router, prefix="/api")
    app.include_router(calendars_router, prefix="/api")
    app.include_router(imports_router, prefix="/api")
    app.include_router(templates_router, prefix="/api")
    app.include_router(campaigns_router, prefix="/api")
    app.include_router(alternates_router, prefix="/api")
    app.include_router(auxiliary_router, prefix="/api")
    app.include_router(imagegen_router, prefix="/api")
    app.include_router(hud_router, prefix="/api")
    app.include_router(expressions_router, prefix="/api")
    app.include_router(observability_router, prefix="/api")
    app.include_router(transient_state_router, prefix="/api")
    app.include_router(context_router, prefix="/api")
    app.include_router(extras_router, prefix="/api")
    # WebSocket routes mount under /ws so the Vite dev server's `ws: true`
    # proxy block forwards upgrade requests correctly. The HTTP health probe
    # in the same router lands at /ws/health.
    app.include_router(ws_router, prefix="/ws")
    return app


app = create_app()


__all__ = ["app", "create_app", "settings"]
