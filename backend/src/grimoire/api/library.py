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
async def list_settings(library: LibraryDep) -> Any:
    return to_payload(await library.list_settings())


@router.post("/library/settings", status_code=201)
async def create_setting(
    payload: CreateSettingPayload,
    setting: SettingDep,
) -> Any:
    try:
        result = await setting.create_setting(payload.id, payload.meta or None)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


@router.get("/library/settings/{setting_id}")
async def get_setting_route(setting_id: str, library: LibraryDep) -> Any:
    try:
        return to_payload(await library.get_setting(setting_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.patch("/library/settings/{setting_id}")
async def update_setting(
    setting_id: str,
    payload: UpdateSettingPayload,
    setting: SettingDep,
) -> Any:
    try:
        return to_payload(await setting.update_setting_meta(setting_id, payload.patch))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.delete("/library/settings/{setting_id}", status_code=204)
async def delete_setting(setting_id: str, setting: SettingDep) -> None:
    try:
        await setting.delete_setting(setting_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.post("/library/settings/{setting_id}/fork", status_code=201)
async def fork_setting(
    setting_id: str,
    payload: ForkSettingPayload,
    setting: SettingDep,
) -> Any:
    try:
        return to_payload(await setting.fork_setting(setting_id, payload.target_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


# --------------------------------------------------------------------------- #
# Setting entities (characters / items / locations / lore / factions / greetings)
# --------------------------------------------------------------------------- #


@router.get("/library/settings/{setting_id}/{kind}")
async def list_setting_entities(
    setting_id: str,
    kind: str,
    library: LibraryDep,
) -> Any:
    try:
        if kind == "greetings":
            return to_payload(await library.list_greetings(setting_id))
        return to_payload(await library.list_in_setting(setting_id, kind))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.post("/library/settings/{setting_id}/{kind}", status_code=201)
async def create_setting_entity(
    setting_id: str,
    kind: str,
    payload: CreateEntityPayload,
    library: LibraryDep,
) -> Any:
    try:
        result = await library.create_entity(
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
async def get_setting_entity(
    setting_id: str,
    kind: str,
    entity_id: str,
    library: LibraryDep,
) -> Any:
    try:
        if kind == "greetings":
            return to_payload(await library.get_greeting(setting_id, entity_id))
        return to_payload(await library.get_entity(setting_id, kind, entity_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.patch("/library/settings/{setting_id}/{kind}/{entity_id}")
async def update_setting_entity(
    setting_id: str,
    kind: str,
    entity_id: str,
    payload: UpdateEntityPayload,
    library: LibraryDep,
) -> Any:
    try:
        return to_payload(
            await library.update_entity(
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
async def delete_setting_entity(
    setting_id: str,
    kind: str,
    entity_id: str,
    library: LibraryDep,
    source: str = "user",
) -> None:
    try:
        await library.delete_entity(setting_id, kind, entity_id, source=source)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/library/settings/{setting_id}/{kind}/{entity_id}/dependents")
async def entity_dependents(
    setting_id: str,
    kind: str,
    entity_id: str,
    library: LibraryDep,
) -> Any:
    try:
        return to_payload(await library.dependents(setting_id, kind, entity_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/library/variants/{kind}/{asset_id}")
async def variants(kind: str, asset_id: str, library: LibraryDep) -> Any:
    try:
        return to_payload(await library.variants_of(asset_id, kind))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


# --------------------------------------------------------------------------- #
# Style guides / image presets
# --------------------------------------------------------------------------- #


@router.get("/library/style-guides")
async def list_style_guides(library: LibraryDep) -> Any:
    return to_payload(await library.list_style_guides())


@router.get("/library/style-guides/{guide_id}")
async def get_style_guide(guide_id: str, library: LibraryDep) -> Any:
    try:
        return to_payload(await library.get_style_guide(guide_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/library/image-presets")
async def list_image_presets(library: LibraryDep) -> Any:
    return to_payload(await library.list_image_presets())


@router.get("/library/image-presets/{preset_id}")
async def get_image_preset(preset_id: str, library: LibraryDep) -> Any:
    try:
        return to_payload(await library.get_image_preset(preset_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


# --------------------------------------------------------------------------- #
# Installed mechanics / plugins
# --------------------------------------------------------------------------- #


@router.get("/mechanics/installed")
def installed_mechanics(mechanics: MechanicsDep) -> Any:
    # ``installed()`` is the sync accessor; returns RegisteredModule list.
    return to_payload(mechanics.installed())


@router.post("/mechanics/rescan")
async def rescan_mechanics(mechanics: MechanicsDep) -> Any:
    try:
        return to_payload(await mechanics.rescan())
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/plugins/installed")
async def installed_plugins(plugins: PluginsDep) -> Any:
    try:
        return to_payload(await plugins.list_installed())
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.post("/plugins/rescan")
async def rescan_plugins(plugins: PluginsDep) -> Any:
    try:
        return to_payload(await plugins.rescan())
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/plugins/{plugin_id}/config")
async def get_plugin_config(plugin_id: str, plugins: PluginsDep) -> Any:
    """Return the saved config for a plugin, with secret fields redacted.

    The frontend uses this when opening a provider's settings panel so the
    user can see which LLMs are configured. We never echo back stored
    secrets — only a presence flag — so the response is safe to display.
    """
    manifest = await plugins.get_manifest(plugin_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"plugin {plugin_id!r} not loaded")
    try:
        config = await plugins.get_config(plugin_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    schema = manifest.config_schema or {}
    secret_names = _secret_property_names(schema)
    values: dict[str, Any] = {}
    secrets_set: dict[str, bool] = {}
    for name, value in config.items():
        if name in secret_names:
            secrets_set[name] = bool(value)
        else:
            values[name] = value
    # Surface secret keys the schema declares but the user has not filled in
    # yet, so the UI can render an empty input with a "(not set)" hint.
    for name in secret_names:
        secrets_set.setdefault(name, False)
    return {
        "plugin_id": plugin_id,
        "values": values,
        "secrets_set": secrets_set,
        "configured": _is_configured(schema, config, secret_names),
    }


@router.post("/plugins/{plugin_id}/config")
async def configure_plugin(
    plugin_id: str,
    plugins: PluginsDep,
    config: Annotated[dict[str, Any], Body()],
) -> Any:
    try:
        await plugins.set_config(plugin_id, config)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}


@router.get("/plugins/{plugin_id}/health")
async def plugin_health(plugin_id: str, plugins: PluginsDep) -> Any:
    try:
        return to_payload(await plugins.health_check(plugin_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


def _secret_property_names(schema: dict[str, Any]) -> set[str]:
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict):
        return set()
    return {
        name
        for name, prop in props.items()
        if isinstance(prop, dict) and prop.get("secret")
    }


def _is_configured(
    schema: dict[str, Any], config: dict[str, Any], secret_names: set[str]
) -> bool:
    required = schema.get("required") if isinstance(schema, dict) else None
    if not isinstance(required, list):
        return bool(config)
    for name in required:
        value = config.get(name)
        if name in secret_names:
            if not value:
                return False
        elif value in (None, ""):
            return False
    return True


# --------------------------------------------------------------------------- #
# Misc
# --------------------------------------------------------------------------- #


@router.get("/library/health")
async def library_health(library: LibraryDep) -> dict[str, Any]:
    try:
        count = len(await library.list_settings())
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"status": "ok", "settings": count}


__all__ = ["router"]
