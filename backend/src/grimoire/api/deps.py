"""FastAPI dependency helpers.

Each helper pulls a service from the app's :class:`ServiceContainer` and
raises ``503 Service Unavailable`` when it's not wired up. Routers use the
``Annotated`` aliases at the bottom (``library: LibraryDep``) rather than
``Depends(...)`` in default positions.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status

from grimoire.api.container import ServiceContainer


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


def get_library(request: Request) -> Any:
    return _require(get_container(request), "library")


def get_setting(request: Request) -> Any:
    return _require(get_container(request), "setting")


def get_characters(request: Request) -> Any:
    return _require(get_container(request), "characters")


def get_scenes(request: Request) -> Any:
    return _require(get_container(request), "scenes")


def get_continuity(request: Request) -> Any:
    return _require(get_container(request), "continuity")


def get_time_engine(request: Request) -> Any:
    return _require(get_container(request), "time_engine")


def get_imagegen(request: Request) -> Any:
    return _require(get_container(request), "imagegen")


def get_export(request: Request) -> Any:
    return _require(get_container(request), "export")


def get_mechanics(request: Request) -> Any:
    return _require(get_container(request), "mechanics")


def get_plugins(request: Request) -> Any:
    return _require(get_container(request), "plugins")


def get_state_store(request: Request) -> Any:
    return _require(get_container(request), "state_store")


def get_orchestrator(request: Request) -> Any:
    return _require(get_container(request), "orchestrator")


def get_stream(request: Request) -> Any:
    return _require(get_container(request), "stream")


ContainerDep = Annotated[ServiceContainer, Depends(get_container)]
LibraryDep = Annotated[Any, Depends(get_library)]
SettingDep = Annotated[Any, Depends(get_setting)]
CharactersDep = Annotated[Any, Depends(get_characters)]
ScenesDep = Annotated[Any, Depends(get_scenes)]
ContinuityDep = Annotated[Any, Depends(get_continuity)]
TimeEngineDep = Annotated[Any, Depends(get_time_engine)]
ImageGenDep = Annotated[Any, Depends(get_imagegen)]
ExportDep = Annotated[Any, Depends(get_export)]
MechanicsDep = Annotated[Any, Depends(get_mechanics)]
PluginsDep = Annotated[Any, Depends(get_plugins)]
StateStoreDep = Annotated[Any, Depends(get_state_store)]
OrchestratorDep = Annotated[Any, Depends(get_orchestrator)]
StreamDep = Annotated[Any, Depends(get_stream)]


__all__ = [
    "CharactersDep",
    "ContainerDep",
    "ContinuityDep",
    "ExportDep",
    "ImageGenDep",
    "LibraryDep",
    "MechanicsDep",
    "OrchestratorDep",
    "PluginsDep",
    "ScenesDep",
    "SettingDep",
    "StateStoreDep",
    "StreamDep",
    "TimeEngineDep",
    "get_characters",
    "get_container",
    "get_continuity",
    "get_export",
    "get_imagegen",
    "get_library",
    "get_mechanics",
    "get_orchestrator",
    "get_plugins",
    "get_scenes",
    "get_setting",
    "get_state_store",
    "get_stream",
    "get_time_engine",
]
