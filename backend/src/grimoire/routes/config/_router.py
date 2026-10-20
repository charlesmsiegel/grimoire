"""One eager router; preserve original route objects and registration order."""

from typing import cast

from fastapi import APIRouter
from fastapi.routing import APIRoute

router = APIRouter()

# These original keys order the existing APIRoutes, never rebuild them.
_ORDER: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (('GET',), '/config', 'get_config'),
    (('PUT',), '/config', 'put_config'),
    (('GET',), '/play-decisions', 'get_play_decisions'),
    (('GET',), '/campaigns/{cid}/play-decisions', 'get_campaign_play_decisions'),
    (('PUT',), '/campaigns/{cid}/play-decisions', 'put_campaign_play_decisions'),
    (('GET',), '/prompt-layout', 'get_prompt_layout'),
    (('PUT',), '/prompt-layout', 'put_prompt_layout'),
    (('GET',), '/config/data-dir', 'get_data_dir'),
    (('PUT',), '/config/data-dir', 'put_data_dir'),
    (('GET',), '/backups', 'get_backups'),
    (('POST',), '/backups', 'post_backup'),
    (('POST',), '/backups/images', 'post_image_backup'),
    (('GET',), '/store/conflicts', 'get_store_conflicts'),
    (('GET',), '/llm-connections', 'get_connections'),
    (('POST',), '/llm-connections', 'post_connection'),
    (('GET',), '/llm-connections/{id}', 'get_connection'),
    (('PUT',), '/llm-connections/{id}', 'put_connection'),
    (('DELETE',), '/llm-connections/{id}', 'delete_connection_route'),
    (('POST',), '/llm-connections/{id}/models/refresh', 'post_connection_models_refresh'),
    (('POST',), '/model-catalog', 'post_model_catalog'),
    (('GET',), '/providers/presets', 'get_provider_presets'),
    (('POST',), '/llm-connections/{conn_id}/health', 'post_connection_health'),
    (('POST',), '/llm-connections/{conn_id}/test/preview', 'post_connection_test_preview'),
    (('POST',), '/llm-connections/{conn_id}/test', 'post_connection_test'),
    (('GET',), '/styles', 'get_styles'),
    (('POST',), '/styles', 'post_style'),
    (('GET',), '/styles/{sid}', 'get_style'),
    (('PUT',), '/styles/{sid}', 'put_style'),
    (('DELETE',), '/styles/{sid}', 'delete_style'),
    (('POST',), '/styles/{sid}/duplicate', 'post_style_duplicate'),
    (('GET',), '/response', 'get_global_response'),
    (('PUT',), '/response', 'put_global_response'),
    (('GET',), '/sampler-presets', 'get_sampler_presets'),
    (('POST',), '/sampler-presets', 'post_sampler_preset'),
    (('POST',), '/sampler-presets/import', 'post_sampler_preset_import'),
    (('GET',), '/sampler-presets/{pid}', 'get_sampler_preset'),
    (('PUT',), '/sampler-presets/{pid}', 'put_sampler_preset'),
    (('DELETE',), '/sampler-presets/{pid}', 'delete_sampler_preset'),
    (('GET',), '/llm-connections/{conn_id}/capabilities', 'get_connection_capabilities'),
    (('GET',), '/llm-connections/{conn_id}/facts', 'get_connection_facts'),
    (('PUT',), '/llm-connections/{conn_id}/facts', 'put_connection_facts'),
    (('POST',), '/inference/controls', 'post_inference_controls'),
    (('GET',), '/entity-kinds', 'get_entity_kinds'),
    (('GET',), '/calendars/providers', 'get_calendar_providers'),
    (('GET',), '/calendars/{provider}/year', 'get_calendar_year'),
    (('GET',), '/climates', 'get_climates'),
    (('GET',), '/climates/{climate_id}', 'get_climate'),
    (('PUT',), '/climates/{climate_id}', 'put_climate'),
    (('GET',), '/climates/{climate_id}/referrers', 'get_climate_referrers'),
    (('DELETE',), '/climates/{climate_id}', 'delete_climate'),
)


def _route_key(route: APIRoute) -> tuple[tuple[str, ...], str, str]:
    return tuple(sorted(route.methods or ())), route.path, route.name


def finalize() -> None:
    """Refuse an incomplete roster before reordering the same route list."""
    # This router contains HTTP declarations, never mounts or websockets.
    routes = cast(list[APIRoute], router.routes)
    keys = [_route_key(route) for route in routes]
    if len(keys) != len(set(keys)):
        raise RuntimeError("duplicate route key during package initialization")
    expected, actual = set(_ORDER), set(keys)
    if actual != expected:
        raise RuntimeError(f"route roster moved: missing={expected - actual!r}, "
                           f"extra={actual - expected!r}")
    positions = {key: index for index, key in enumerate(_ORDER)}
    ordered = sorted(routes, key=lambda route: positions[_route_key(route)])
    router.routes[:] = ordered
