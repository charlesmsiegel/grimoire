"""One eager router; preserve original route objects and registration order."""

from fastapi import APIRouter

from .._route_order import RouteKey, order_routes

router = APIRouter()

# These original keys order the existing APIRoutes, never rebuild them.
_ORDER: tuple[RouteKey, ...] = (
    (('GET',), '/todo', 'get_todo'),
    (('GET',), '/todo/{chore_id}/items', 'get_todo_items'),
    (('PUT',), '/todo/{chore_id}/ignored', 'put_todo_ignored'),
)


def finalize() -> None:
    """Refuse an incomplete roster before reordering the same route list."""
    order_routes(router, _ORDER)
