"""Library + world + plugin/mechanics rescan REST routes.

These wrap :class:`grimoire.library.service.LibraryService`,
:class:`grimoire.world.service.WorldService`, the mechanics registry, and
the plugins registry. All endpoints stick to the surface defined in spec 14
§Backend contract.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from grimoire.api.deps import (
    LibraryDep,
    MechanicsDep,
    PluginsDep,
    WorldDep,
)
from grimoire.api.util import map_lookup_errors, to_payload

router = APIRouter()


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #


class CreateWorldPayload(BaseModel):
    id: str
    meta: dict[str, Any] = Field(default_factory=dict)
    source: str = "user"


class UpdateWorldPayload(BaseModel):
    patch: dict[str, Any]
    source: str = "user"


class ForkWorldPayload(BaseModel):
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


class CreateStyleGuidePayload(BaseModel):
    id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    pacing: list[str] = Field(default_factory=list)
    voice: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    source: str = "user"


class UpdateStyleGuidePayload(BaseModel):
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    pacing: list[str] | None = None
    voice: list[str] | None = None
    themes: list[str] | None = None
    avoid: list[str] | None = None
    source: str = "user"


# --------------------------------------------------------------------------- #
# Worlds
# --------------------------------------------------------------------------- #


@router.get("/library/worlds")
async def list_worlds(library: LibraryDep) -> Any:
    return to_payload(await library.list_worlds())


@router.post("/library/worlds", status_code=201)
async def create_world(
    payload: CreateWorldPayload,
    world: WorldDep,
) -> Any:
    try:
        result = await world.create_world(payload.id, payload.meta or None)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


@router.get("/library/worlds/{world_id}")
async def get_world_route(world_id: str, library: LibraryDep) -> Any:
    try:
        return to_payload(await library.get_world(world_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/library/worlds/{world_id}/diff")
async def diff_world(
    world_id: str,
    library: LibraryDep,
    from_: Annotated[int, Query(alias="from")] = 0,
    to: int | None = None,
) -> Any:
    """Synthesize a flat diff between two versions of a world.

    Returns ``{added, removed, changed: [{path, before, after}]}`` so the
    composition view's upgrade banner can preview what an upgrade would
    pull in. The ``from`` query param is the version the composition is
    currently bound to; ``to`` defaults to the world's latest version.
    """
    try:
        return await library.world_diff(world_id, from_, to)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.patch("/library/worlds/{world_id}")
async def update_world(
    world_id: str,
    payload: UpdateWorldPayload,
    world: WorldDep,
) -> Any:
    try:
        return to_payload(await world.update_world_meta(world_id, payload.patch))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.delete("/library/worlds/{world_id}", status_code=204)
async def delete_world(world_id: str, world: WorldDep) -> None:
    try:
        await world.delete_world(world_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.post("/library/worlds/{world_id}/fork", status_code=201)
async def fork_world(
    world_id: str,
    payload: ForkWorldPayload,
    world: WorldDep,
) -> Any:
    try:
        return to_payload(await world.fork_world(world_id, payload.target_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


# --------------------------------------------------------------------------- #
# World entities (characters / items / locations / lore / factions / greetings)
# --------------------------------------------------------------------------- #


@router.get("/library/worlds/{world_id}/{kind}")
async def list_world_entities(
    world_id: str,
    kind: str,
    library: LibraryDep,
) -> Any:
    try:
        if kind == "greetings":
            return to_payload(await library.list_greetings(world_id))
        return to_payload(await library.list_in_world(world_id, kind))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.post("/library/worlds/{world_id}/{kind}", status_code=201)
async def create_world_entity(
    world_id: str,
    kind: str,
    payload: CreateEntityPayload,
    library: LibraryDep,
) -> Any:
    try:
        result = await library.create_entity(
            world_id,
            kind,
            payload.id,
            payload.frontmatter,
            payload.body,
            source=payload.source,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


@router.get("/library/worlds/{world_id}/{kind}/{entity_id}")
async def get_world_entity(
    world_id: str,
    kind: str,
    entity_id: str,
    library: LibraryDep,
) -> Any:
    try:
        if kind == "greetings":
            return to_payload(await library.get_greeting(world_id, entity_id))
        return to_payload(await library.get_entity(world_id, kind, entity_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.patch("/library/worlds/{world_id}/{kind}/{entity_id}")
async def update_world_entity(
    world_id: str,
    kind: str,
    entity_id: str,
    payload: UpdateEntityPayload,
    library: LibraryDep,
) -> Any:
    try:
        return to_payload(
            await library.update_entity(
                world_id,
                kind,
                entity_id,
                payload.frontmatter_patch,
                payload.body,
                source=payload.source,
            )
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.delete("/library/worlds/{world_id}/{kind}/{entity_id}", status_code=204)
async def delete_world_entity(
    world_id: str,
    kind: str,
    entity_id: str,
    library: LibraryDep,
    source: str = "user",
) -> None:
    try:
        await library.delete_entity(world_id, kind, entity_id, source=source)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/library/worlds/{world_id}/{kind}/{entity_id}/dependents")
async def entity_dependents(
    world_id: str,
    kind: str,
    entity_id: str,
    library: LibraryDep,
) -> Any:
    try:
        return to_payload(await library.dependents(world_id, kind, entity_id))
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


@router.post("/library/style-guides", status_code=201)
async def create_style_guide(
    payload: CreateStyleGuidePayload,
    library: LibraryDep,
) -> Any:
    try:
        result = await library.create_style_guide(
            payload.id,
            name=payload.name,
            description=payload.description,
            tags=payload.tags,
            pacing=payload.pacing,
            voice=payload.voice,
            themes=payload.themes,
            avoid=payload.avoid,
            source=payload.source,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


@router.get("/library/style-guides/{guide_id}")
async def get_style_guide(guide_id: str, library: LibraryDep) -> Any:
    try:
        return to_payload(await library.get_style_guide(guide_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/library/style-guides/{guide_id}/edit")
async def get_style_guide_edit(guide_id: str, library: LibraryDep) -> Any:
    """Return a style guide parsed into the structured shape the edit form uses."""
    try:
        return to_payload(await library.parse_style_guide(guide_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.patch("/library/style-guides/{guide_id}")
async def update_style_guide(
    guide_id: str,
    payload: UpdateStyleGuidePayload,
    library: LibraryDep,
) -> Any:
    try:
        result = await library.update_style_guide(
            guide_id,
            name=payload.name,
            description=payload.description,
            tags=payload.tags,
            pacing=payload.pacing,
            voice=payload.voice,
            themes=payload.themes,
            avoid=payload.avoid,
            source=payload.source,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


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


@router.get("/mechanics/{module_id}/sheets/{kind}")
def mechanics_sheet_schema(
    module_id: str,
    kind: str,
    mechanics: MechanicsDep,
) -> Any:
    """Return the JSON Schema for ``kind`` sheets under ``module_id``.

    Lets the Frontend's sheet widget render without first binding a
    campaign to the module.
    """
    schema = mechanics.sheet_schema_for_module(module_id, kind)
    if schema is None:
        raise HTTPException(
            status_code=404,
            detail=f"no sheet schema for module {module_id!r} kind {kind!r}",
        )
    return schema


@router.get("/mechanics/{module_id}/theme.css")
def mechanics_theme_css(module_id: str, mechanics: MechanicsDep) -> Response:
    """Return the raw CSS body declared in ``ui.theme_css``.

    Returns ``""`` with 200 when the module has no theme CSS so the
    Frontend can render without branching on ``theme_css`` being unset.
    """
    css = mechanics.theme_css_for_module(module_id)
    return Response(content=css, media_type="text/css")


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


@router.get("/plugins/discovery-errors")
async def plugin_discovery_errors(plugins: PluginsDep) -> Any:
    """Return per-directory errors from the most recent discovery pass.

    A malformed ``manifest.yaml`` would otherwise vanish into a generic
    ``failed`` entry on the rescan report keyed by directory name. This
    endpoint exposes the underlying parse error so the Installed Plugins
    view can render an actionable message.
    """
    getter = getattr(plugins, "discovery_errors", None)
    if not callable(getter):
        return []
    return [{"plugin_dir": str(err.plugin_dir), "message": err.message} for err in getter()]


@router.get("/plugins/{plugin_id}/config")
async def get_plugin_config(plugin_id: str, plugins: PluginsDep) -> Any:
    """Return the saved config for a plugin, with secret fields redacted.

    The frontend uses this when opening a provider's worlds panel so the
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


@router.patch("/plugins/{plugin_id}/config")
async def patch_plugin_config(
    plugin_id: str,
    plugins: PluginsDep,
    patch: Annotated[dict[str, Any], Body()],
) -> Any:
    """Merge ``patch`` into the saved plugin config and save the result.

    Used by inline editors (e.g. the Providers tab's model picker) that
    want to change one field without re-supplying secrets the UI never
    received in the first place.
    """
    manifest = await plugins.get_manifest(plugin_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"plugin {plugin_id!r} not loaded")
    try:
        current = await plugins.get_config(plugin_id)
        merged = {**current, **patch}
        # Drop keys the manifest no longer declares. This makes field
        # renames (e.g. default_model -> active_model) graceful: the
        # stale on-disk key is filtered out instead of tripping the
        # ``additionalProperties: false`` validator.
        schema = manifest.config_schema or {}
        properties = schema.get("properties") if isinstance(schema, dict) else None
        if isinstance(properties, dict) and schema.get("additionalProperties") is False:
            merged = {k: v for k, v in merged.items() if k in properties}
        await plugins.set_config(plugin_id, merged)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}


@router.get("/plugins/{plugin_id}/health")
async def plugin_health(plugin_id: str, plugins: PluginsDep) -> Any:
    try:
        return to_payload(await plugins.health_check(plugin_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/plugins/{plugin_id}/models")
async def plugin_models(plugin_id: str, plugins: PluginsDep) -> Any:
    """List models advertised by an LLM- or embedding-provider plugin.

    Used by the frontend plugin-config form to populate a searchable model
    picker for fields annotated with ``x-source: models``. Returns 404 if
    the plugin is not loaded or does not advertise a model catalog; a
    plugin whose provider raises (e.g. missing API key) is surfaced as a
    409 so the UI can show "configure the plugin first".
    """
    provider: Any = plugins.get_llm_provider(plugin_id) or plugins.get_embedding_provider(plugin_id)
    if provider is None or not hasattr(provider, "list_models"):
        raise HTTPException(
            status_code=404,
            detail=f"plugin {plugin_id!r} does not advertise a model catalog",
        )
    try:
        models = await provider.list_models()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return to_payload(models)


def _secret_property_names(schema: dict[str, Any]) -> set[str]:
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict):
        return set()
    return {name for name, prop in props.items() if isinstance(prop, dict) and prop.get("secret")}


def _is_configured(schema: dict[str, Any], config: dict[str, Any], secret_names: set[str]) -> bool:
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
        count = len(await library.list_worlds())
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"status": "ok", "worlds": count}


__all__ = ["router"]
