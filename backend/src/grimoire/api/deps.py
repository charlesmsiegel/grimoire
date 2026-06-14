"""FastAPI dependency helpers.

Each helper pulls a service from the app's :class:`ServiceContainer` and
raises ``503 Service Unavailable`` when it's not wired up. Routers use the
``Annotated`` aliases at the bottom (``library: LibraryDep``) rather than
``Depends(...)`` in default positions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, HTTPException, Request, status

from grimoire.api.container import ServiceContainer

if TYPE_CHECKING:
    from grimoire.api.stream import StreamManager
    from grimoire.characters import CharactersService
    from grimoire.characters.import_preview import ImportPreviewCache
    from grimoire.continuity import ContinuityRegistry
    from grimoire.export.service import ExportService
    from grimoire.extras import ExtrasService as _ExtrasService
    from grimoire.hud.config import HudConfigService
    from grimoire.hud.service import HudService
    from grimoire.imagegen import ImageGenService
    from grimoire.library import LibraryService
    from grimoire.llm_gateway.gateway import LLMGatewayService
    from grimoire.mechanics import MechanicsService
    from grimoire.observability.service import ObservabilityService
    from grimoire.orchestrator.service import OrchestratorService
    from grimoire.plugins import PluginsService
    from grimoire.scenes import SceneManager
    from grimoire.scenes.ledger import SceneLedger
    from grimoire.state_store import StateStore
    from grimoire.time_engine.service import TimeEngineService
    from grimoire.transient_state import TransientStateService
    from grimoire.watcher.watcher import FileWatcher
    from grimoire.world import WorldService
    from grimoire.world.calendar_service import CalendarService


def get_container(request: Request) -> ServiceContainer:
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="service container not initialised",
        )
    return container


def _require(container: ServiceContainer, name: str) -> Any:
    service = getattr(container, name, None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{name} service not configured",
        )
    return service


def get_library(request: Request) -> LibraryService:
    return _require(get_container(request), "library")


def get_world(request: Request) -> WorldService:
    return _require(get_container(request), "world")


def get_characters(request: Request) -> CharactersService:
    return _require(get_container(request), "characters")


def get_scenes(request: Request) -> SceneManager:
    return _require(get_container(request), "scenes")


def get_scene_ledger(request: Request) -> SceneLedger:
    return _require(get_container(request), "scene_ledger")


def get_continuity(request: Request) -> ContinuityRegistry:
    return _require(get_container(request), "continuity")


def get_time_engine(request: Request) -> TimeEngineService:
    return _require(get_container(request), "time_engine")


def get_imagegen(request: Request) -> ImageGenService:
    return _require(get_container(request), "imagegen")


def get_export(request: Request) -> ExportService:
    return _require(get_container(request), "export")


def get_mechanics(request: Request) -> MechanicsService:
    return _require(get_container(request), "mechanics")


def get_plugins(request: Request) -> PluginsService:
    return _require(get_container(request), "plugins")


def get_state_store(request: Request) -> StateStore:
    return _require(get_container(request), "state_store")


def get_orchestrator(request: Request) -> OrchestratorService:
    return _require(get_container(request), "orchestrator")


def get_observability(request: Request) -> ObservabilityService:
    return _require(get_container(request), "observability")


def get_hud(request: Request) -> HudService:
    return _require(get_container(request), "hud")


def get_hud_config(request: Request) -> HudConfigService:
    return _require(get_container(request), "hud_config")


def get_import_preview_cache(request: Request) -> ImportPreviewCache:
    return _require(get_container(request), "import_preview_cache")


def get_stream(request: Request) -> StreamManager:
    return _require(get_container(request), "stream")


def get_transient_state(request: Request) -> TransientStateService:
    return _require(get_container(request), "transient_state")


def get_inventory(request: Request) -> Any:
    return _require(get_container(request), "inventory")


def get_extras_service(request: Request) -> _ExtrasService:
    return _require(get_container(request), "extras_service")


def get_calendar(request: Request) -> CalendarService:
    return _require(get_container(request), "calendar")


def get_llm_gateway(request: Request) -> LLMGatewayService:
    return _require(get_container(request), "llm_gateway")


def get_file_watcher(request: Request) -> FileWatcher:
    return _require(get_container(request), "file_watcher")


# Annotated aliases use Any at runtime because the concrete types are
# imported under TYPE_CHECKING (to avoid circular imports) and Annotated
# evaluates at import time.  Type safety comes from the get_X() return
# annotations, which are lazy-evaluated via ``from __future__ import
# annotations``.
ContainerDep = Annotated[ServiceContainer, Depends(get_container)]
LibraryDep = Annotated[Any, Depends(get_library)]
WorldDep = Annotated[Any, Depends(get_world)]
CharactersDep = Annotated[Any, Depends(get_characters)]
ScenesDep = Annotated[Any, Depends(get_scenes)]
SceneLedgerDep = Annotated[Any, Depends(get_scene_ledger)]
ContinuityDep = Annotated[Any, Depends(get_continuity)]
TimeEngineDep = Annotated[Any, Depends(get_time_engine)]
ImageGenDep = Annotated[Any, Depends(get_imagegen)]
ExportDep = Annotated[Any, Depends(get_export)]
MechanicsDep = Annotated[Any, Depends(get_mechanics)]
PluginsDep = Annotated[Any, Depends(get_plugins)]
StateStoreDep = Annotated[Any, Depends(get_state_store)]
OrchestratorDep = Annotated[Any, Depends(get_orchestrator)]
ObservabilityDep = Annotated[Any, Depends(get_observability)]
HudDep = Annotated[Any, Depends(get_hud)]
HudConfigDep = Annotated[Any, Depends(get_hud_config)]
ImportPreviewCacheDep = Annotated[Any, Depends(get_import_preview_cache)]
StreamDep = Annotated[Any, Depends(get_stream)]
TransientStateDep = Annotated[Any, Depends(get_transient_state)]
InventoryDep = Annotated[Any, Depends(get_inventory)]
ExtrasServiceDep = Annotated[Any, Depends(get_extras_service)]
LLMGatewayDep = Annotated[Any, Depends(get_llm_gateway)]
FileWatcherDep = Annotated[Any, Depends(get_file_watcher)]
CalendarDep = Annotated[Any, Depends(get_calendar)]


__all__ = [
    "CalendarDep",
    "CharactersDep",
    "ContainerDep",
    "ContinuityDep",
    "ExportDep",
    "ExtrasServiceDep",
    "FileWatcherDep",
    "HudConfigDep",
    "HudDep",
    "ImageGenDep",
    "ImportPreviewCacheDep",
    "InventoryDep",
    "LLMGatewayDep",
    "LibraryDep",
    "MechanicsDep",
    "ObservabilityDep",
    "OrchestratorDep",
    "PluginsDep",
    "SceneLedgerDep",
    "ScenesDep",
    "StateStoreDep",
    "StreamDep",
    "TimeEngineDep",
    "TransientStateDep",
    "WorldDep",
    "get_calendar",
    "get_characters",
    "get_container",
    "get_continuity",
    "get_export",
    "get_extras_service",
    "get_file_watcher",
    "get_hud",
    "get_hud_config",
    "get_imagegen",
    "get_import_preview_cache",
    "get_inventory",
    "get_library",
    "get_llm_gateway",
    "get_mechanics",
    "get_observability",
    "get_orchestrator",
    "get_plugins",
    "get_scene_ledger",
    "get_scenes",
    "get_state_store",
    "get_stream",
    "get_time_engine",
    "get_transient_state",
    "get_world",
]
