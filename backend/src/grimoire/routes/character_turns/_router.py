"""One eager router; restore its original order before the facade exposes it."""

from fastapi import APIRouter

from .._route_order import RouteKey, order_routes

router = APIRouter()

# FastAPI matches in declaration order. Responsibilities interleave in the
# original module, so leaf import order cannot preserve that contract.
# These are the original (methods, path, endpoint name) keys, not new routes.
_ORDER: tuple[RouteKey, ...] = (
    (('GET',), '/campaigns/{cid}/scenes/{sid}/group', 'get_group'),
    (('PUT',), '/campaigns/{cid}/scenes/{sid}/group', 'put_group'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/responses/{rid}', 'get_response'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/responses/{rid}/swipe', 'get_response_swipe'),
    (('DELETE',), '/campaigns/{cid}/scenes/{sid}/responses/{rid}', 'delete_response'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/responses/{rid}/variants/{vid}/activate', 'activate_response'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/responses/{rid}/regenerate', 'regenerate_response'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/responses/{rid}/extend', 'extend_response'),
)


def finalize() -> None:
    """Order the original route objects in place, refusing an incomplete roster."""
    order_routes(router, _ORDER)
