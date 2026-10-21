"""One eager router; restore its original order before the facade exposes it."""

from fastapi import APIRouter

from .._route_order import RouteKey, order_routes

router = APIRouter()

# FastAPI matches in declaration order. Responsibilities interleave in the
# original module, so leaf import order cannot preserve that contract.
# These are the original (methods, path, endpoint name) keys, not new routes.
_ORDER: tuple[RouteKey, ...] = (
    (('GET',), '/campaigns/{cid}/scene-by-identity', 'get_scene_by_identity'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/run', 'get_scene_run'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/runs/{run_id}', 'get_run'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/runs/{run_id}/cancel', 'post_run_cancel'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/attempt-cancel', 'post_attempt_cancel'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/attempt-state', 'get_attempt_state'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/runs/{run_id}/stream', 'get_run_stream'),
    (('GET',), '/campaigns/{cid}/runs', 'get_campaign_runs'),
    (('GET',), '/campaigns/{cid}/runs/{run_id}', 'get_campaign_run'),
    (('GET',), '/campaigns/{cid}/runs/{run_id}/stream', 'get_campaign_run_stream'),
    (('POST',), '/campaigns/{cid}/runs/{run_id}/cancel', 'post_campaign_run_cancel'),
    (('GET',), '/worlds/{wid}/runs', 'get_world_runs'),
    (('GET',), '/worlds/{wid}/runs/{run_id}', 'get_world_run'),
    (('GET',), '/worlds/{wid}/runs/{run_id}/stream', 'get_world_run_stream'),
    (('POST',), '/worlds/{wid}/runs/{run_id}/cancel', 'post_world_run_cancel'),
    (('GET',), '/runs', 'get_global_runs'),
    (('GET',), '/runs/{run_id}', 'get_global_run'),
    (('GET',), '/runs/{run_id}/stream', 'get_global_run_stream'),
    (('POST',), '/runs/{run_id}/cancel', 'post_global_run_cancel'),
)


def finalize() -> None:
    """Order the original route objects in place, refusing an incomplete roster."""
    order_routes(router, _ORDER)
