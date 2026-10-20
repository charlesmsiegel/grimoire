"""One eager router; preserve original route objects and registration order."""

from typing import cast

from fastapi import APIRouter
from fastapi.routing import APIRoute

router = APIRouter()

# These original keys order the existing APIRoutes, never rebuild them.
_ORDER: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (('GET',), '/campaigns/{cid}/tracker', 'get_campaign_tracker'),
    (('PUT',), '/campaigns/{cid}/tracker', 'put_campaign_tracker'),
    (('GET',), '/worlds/{wid}/tracker-fields', 'get_world_tracker_fields'),
    (('PUT',), '/worlds/{wid}/tracker-fields', 'put_world_tracker_fields'),
    (('GET',), '/campaigns/{cid}/tracker-fields', 'get_campaign_tracker_fields'),
    (('PUT',), '/campaigns/{cid}/tracker-fields', 'put_campaign_tracker_fields'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/tracker-fields', 'get_scene_tracker_fields'),
    (('PUT',), '/campaigns/{cid}/scenes/{sid}/tracker-fields', 'put_scene_tracker_fields'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/tracker', 'get_scene_tracker'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/tracker/records/{key}', 'get_tracker_record'),
    (('PUT',), '/campaigns/{cid}/scenes/{sid}/tracker/records/{key}', 'put_tracker_record'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/tracker/records/{key}/retry', 'post_tracker_retry'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/tracker/records/{key}/rerun-from', 'post_tracker_rerun_from'),
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
