"""One eager router; preserve original route objects and registration order."""

from typing import cast

from fastapi import APIRouter
from fastapi.routing import APIRoute

router = APIRouter()

# These original keys order the existing APIRoutes, never rebuild them.
_ORDER: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (('GET',), '/todo', 'get_todo'),
    (('GET',), '/todo/{chore_id}/items', 'get_todo_items'),
    (('PUT',), '/todo/{chore_id}/ignored', 'put_todo_ignored'),
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
