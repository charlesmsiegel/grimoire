"""One eager router; preserve original route objects and registration order."""

from fastapi import APIRouter

from .._route_order import RouteKey, order_routes

router = APIRouter()

# These original keys order the existing APIRoutes, never rebuild them.
_ORDER: tuple[RouteKey, ...] = (
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


def finalize() -> None:
    """Refuse an incomplete roster before reordering the same route list."""
    order_routes(router, _ORDER)
