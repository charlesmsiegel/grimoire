"""Library + setting + plugin/mechanics rescan REST routes.

These wrap :class:`grimoire.library.service.LibraryService`,
:class:`grimoire.setting.service.SettingService`, the mechanics registry, and
the plugins registry. All endpoints stick to the surface defined in spec 14
§Backend contract.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from grimoire.api.deps import (
    LibraryDep,
    MechanicsDep,
    PluginsDep,
    SettingDep,
)
from grimoire.api.util import map_lookup_errors, to_payload

router = APIRouter()


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #


class CreateSettingPayload(BaseModel):
    id: str
    meta: dict[str, Any] = Field(default_factory=dict)
    source: str = "user"


class UpdateSettingPayload(BaseModel):
    patch: dict[str, Any]
    source: str = "user"


class ForkSettingPayload(BaseModel):
    target_id: str


class CreateEntityPayload(BaseModel):
    id: str
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    body: str = ""
    source: str = "user"


class UpdateEntityPayload(BaseModel):
    frontmatter_patch: dict[str, Any] | None = None
    body: str | None = None
    source: str = "user"


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


@router.get("/library/settings")
def list_settings(library: LibraryDep) -> Any:
    return to_payload(library.list_settings())


@router.post("/library/settings", status_code=201)
def create_setting(
    payload: CreateSettingPayload,
    setting: SettingDep,
) -> Any:
    try:
        result = setting.create_setting(payload.id, payload.meta or None)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


@router.get("/library/settings/{setting_id}")
def get_setting_route(setting_id: str, library: LibraryDep) -> Any:
    try:
        return to_payload(library.get_setting(setting_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.patch("/library/settings/{setting_id}")
def update_setting(
    setting_id: str,
    payload: UpdateSettingPayload,
    setting: SettingDep,
) -> Any:
    try:
        return to_payload(setting.update_setting_meta(setting_id, payload.patch))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.delete("/library/settings/{setting_id}", status_code=204)
def delete_setting(setting_id: str, setting: SettingDep) -> None:
    try:
        setting.delete_setting(setting_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.post("/library/settings/{setting_id}/fork", status_code=201)
def fork_setting(
    setting_id: str,
    payload: ForkSettingPayload,
    setting: SettingDep,
) -> Any:
    try:
        return to_payload(setting.fork_setting(setting_id, payload.target_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


# --------------------------------------------------------------------------- #
# Setting entities (characters / items / locations / lore / factions / greetings)
# --------------------------------------------------------------------------- #


@router.get("/library/settings/{setting_id}/{kind}")
def list_setting_entities(
    setting_id: str,
    kind: str,
    library: LibraryDep,
) -> Any:
    if kind == "greetings":
        try:
            return to_payload(library.list_greetings(setting_id))
        except Exception as exc:
            raise map_lookup_errors(exc) from exc
    try:
        return to_payload(library.list_in_setting(setting_id, kind))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.post("/library/settings/{setting_id}/{kind}", status_code=201)
def create_setting_entity(
    setting_id: str,
    kind: str,
    payload: CreateEntityPayload,
    library: LibraryDep,
) -> Any:
    try:
        result = library.create_entity(
            setting_id,
            kind,
            payload.id,
            payload.frontmatter,
            payload.body,
            source=payload.source,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


@router.get("/library/settings/{setting_id}/{kind}/{entity_id}")
def get_setting_entity(
    setting_id: str,
    kind: str,
    entity_id: str,
    library: LibraryDep,
) -> Any:
    try:
        if kind == "greetings":
            return to_payload(library.get_greeting(setting_id, entity_id))
        return to_payload(library.get_entity(setting_id, kind, entity_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.patch("/library/settings/{setting_id}/{kind}/{entity_id}")
def update_setting_entity(
    setting_id: str,
    kind: str,
    entity_id: str,
    payload: UpdateEntityPayload,
    library: LibraryDep,
) -> Any:
    try:
        return to_payload(
            library.update_entity(
                setting_id,
                kind,
                entity_id,
                payload.frontmatter_patch,
                payload.body,
                source=payload.source,
            )
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.delete("/library/settings/{setting_id}/{kind}/{entity_id}", status_code=204)
def delete_setting_entity(
    setting_id: str,
    kind: str,
    entity_id: str,
    library: LibraryDep,
    source: str = "user",
) -> None:
    try:
        library.delete_entity(setting_id, kind, entity_id, source=source)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/library/settings/{setting_id}/{kind}/{entity_id}/dependents")
def entity_dependents(
    setting_id: str,
    kind: str,
    entity_id: str,
    library: LibraryDep,
) -> Any:
    try:
        return to_payload(library.dependents(setting_id, kind, entity_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/library/variants/{kind}/{asset_id}")
def variants(kind: str, asset_id: str, library: LibraryDep) -> Any:
    try:
        return to_payload(library.variants_of(asset_id, kind))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


# --------------------------------------------------------------------------- #
# Style guides / image presets
# --------------------------------------------------------------------------- #


@router.get("/library/style-guides")
def list_style_guides(library: LibraryDep) -> Any:
    return to_payload(library.list_style_guides())


@router.get("/library/style-guides/{guide_id}")
def get_style_guide(guide_id: str, library: LibraryDep) -> Any:
    try:
        return to_payload(library.get_style_guide(guide_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/library/image-presets")
def list_image_presets(library: LibraryDep) -> Any:
    return to_payload(library.list_image_presets())


@router.get("/library/image-presets/{preset_id}")
def get_image_preset(preset_id: str, library: LibraryDep) -> Any:
    try:
        return to_payload(library.get_image_preset(preset_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


# --------------------------------------------------------------------------- #
# Installed mechanics / plugins
# --------------------------------------------------------------------------- #


@router.get("/mechanics/installed")
def installed_mechanics(mechanics: MechanicsDep) -> Any:
    return to_payload(mechanics.modules())


@router.post("/mechanics/rescan")
def rescan_mechanics(mechanics: MechanicsDep) -> Any:
    try:
        return to_payload(mechanics.rescan())
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/plugins/installed")
def installed_plugins(
    plugins: PluginsDep,
    kind: str | None = None,
    installed_only: bool = False,
) -> Any:
    try:
        return to_payload(plugins.list_plugins(kind=kind, installed_only=installed_only))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.post("/plugins/rescan")
def rescan_plugins(plugins: PluginsDep) -> Any:
    try:
        return to_payload(plugins.rescan())
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.post("/plugins/{plugin_id}/config")
def configure_plugin(
    plugin_id: str,
    plugins: PluginsDep,
    config: Annotated[dict[str, Any], Body()],
) -> Any:
    try:
        return to_payload(plugins.configure_plugin(plugin_id, config))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/plugins/{plugin_id}/health")
def plugin_health(plugin_id: str, plugins: PluginsDep) -> Any:
    try:
        return to_payload(plugins.health_check(plugin_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


# --------------------------------------------------------------------------- #
# Misc
# --------------------------------------------------------------------------- #


@router.get("/library/health")
def library_health(library: LibraryDep) -> dict[str, Any]:
    # Cheap smoke check: count of settings.
    try:
        count = len(library.list_settings())
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"status": "ok", "settings": count}


__all__ = ["router"]
