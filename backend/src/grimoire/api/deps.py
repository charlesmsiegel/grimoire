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
    from grimoire.continuity import ContinuityRegistry
    from grimoire.export.service import ExportService
    from grimoire.extras import ExtrasService as _ExtrasService
    from grimoire.imagegen import ImageGenService
    from grimoire.library import LibraryService
    from grimoire.llm_gateway.gateway import LLMGatewayService
    from grimoire.mechanics import MechanicsService
    from grimoire.observability.service import ObservabilityService
    from grimoire.orchestrator.service import OrchestratorService
    from grimoire.plugins import PluginsService
    from grimoire.scenes import SceneManager
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


def get_stream(request: Request) -> StreamManager:
    return _require(get_container(request), "stream")


def get_transient_state(request: Request) -> TransientStateService:
    return _require(get_container(request), "transient_state")


def get_extras_service(request: Request) -> _ExtrasService:
    return _require(get_container(request), "extras_service")


def get_calendar(request: Request) -> CalendarService:
    return _require(get_container(request), "calendar")


def get_llm_gateway(request: Request) -> LLMGatewayService:
    return _require(get_container(request), "llm_gateway")


def get_file_watcher(request: Request) -> FileWatcher:
    return _require(get_container(request), "file_watcher")


ContainerDep = Annotated[ServiceContainer, Depends(get_container)]
LibraryDep = Annotated[LibraryService, Depends(get_library)]
WorldDep = Annotated[WorldService, Depends(get_world)]
CharactersDep = Annotated[CharactersService, Depends(get_characters)]
ScenesDep = Annotated[SceneManager, Depends(get_scenes)]
ContinuityDep = Annotated[ContinuityRegistry, Depends(get_continuity)]
TimeEngineDep = Annotated[TimeEngineService, Depends(get_time_engine)]
ImageGenDep = Annotated[ImageGenService, Depends(get_imagegen)]
ExportDep = Annotated[ExportService, Depends(get_export)]
MechanicsDep = Annotated[MechanicsService, Depends(get_mechanics)]
PluginsDep = Annotated[PluginsService, Depends(get_plugins)]
StateStoreDep = Annotated[StateStore, Depends(get_state_store)]
OrchestratorDep = Annotated[OrchestratorService, Depends(get_orchestrator)]
ObservabilityDep = Annotated[ObservabilityService, Depends(get_observability)]
StreamDep = Annotated[StreamManager, Depends(get_stream)]
TransientStateDep = Annotated[TransientStateService, Depends(get_transient_state)]
ExtrasServiceDep = Annotated[_ExtrasService, Depends(get_extras_service)]
LLMGatewayDep = Annotated[LLMGatewayService, Depends(get_llm_gateway)]
FileWatcherDep = Annotated[FileWatcher, Depends(get_file_watcher)]
CalendarDep = Annotated[CalendarService, Depends(get_calendar)]


__all__ = [
    "CalendarDep",
    "CharactersDep",
    "ContainerDep",
    "ContinuityDep",
    "ExportDep",
    "ExtrasServiceDep",
    "FileWatcherDep",
    "ImageGenDep",
    "LLMGatewayDep",
    "LibraryDep",
    "MechanicsDep",
    "ObservabilityDep",
    "OrchestratorDep",
    "PluginsDep",
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
    "get_imagegen",
    "get_library",
    "get_llm_gateway",
    "get_mechanics",
    "get_observability",
    "get_orchestrator",
    "get_plugins",
    "get_scenes",
    "get_state_store",
    "get_stream",
    "get_time_engine",
    "get_transient_state",
    "get_world",
]
