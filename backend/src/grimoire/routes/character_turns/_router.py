"""One eager router; restore its original order before the facade exposes it."""

from typing import cast

from fastapi import APIRouter
from fastapi.routing import APIRoute

router = APIRouter()

# FastAPI matches in declaration order. Responsibilities interleave in the
# original module, so leaf import order cannot preserve that contract.
# These are the original (methods, path, endpoint name) keys, not new routes.
_ORDER: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (('GET',), '/campaigns/{cid}/scenes/{sid}/group', 'get_group'),
    (('PUT',), '/campaigns/{cid}/scenes/{sid}/group', 'put_group'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/responses/{rid}', 'get_response'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/responses/{rid}/swipe', 'get_response_swipe'),
    (('DELETE',), '/campaigns/{cid}/scenes/{sid}/responses/{rid}', 'delete_response'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/responses/{rid}/variants/{vid}/activate', 'activate_response'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/responses/{rid}/regenerate', 'regenerate_response'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/responses/{rid}/extend', 'extend_response'),
)


def _route_key(route: APIRoute) -> tuple[tuple[str, ...], str, str]:
    return tuple(sorted(route.methods or ())), route.path, route.name


def finalize() -> None:
    """Order the original route objects in place, refusing an incomplete roster."""
    # This router contains only HTTP declarations, never mounts or websockets.
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
