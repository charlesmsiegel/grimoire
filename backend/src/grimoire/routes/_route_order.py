"""Restore a package router's original route order before the facade exposes it.

FastAPI matches in declaration order. A route package's leaves interleave the
responsibilities of the module it replaced, so leaf import order cannot
preserve that contract; each package's `_router` keeps the original
(methods, path, endpoint name) keys and sorts its existing route objects by
them. This module imports no route package, so every `_router` can share it.
"""

from typing import cast

from fastapi import APIRouter
from fastapi.routing import APIRoute

RouteKey = tuple[tuple[str, ...], str, str]


def route_key(route: APIRoute) -> RouteKey:
    return tuple(sorted(route.methods or ())), route.path, route.name


def order_routes(router: APIRouter, order: tuple[RouteKey, ...]) -> None:
    """Order the original route objects in place, refusing an incomplete roster.

    The roster is checked before anything moves, so a refused router is left
    untouched: a caller never observes a partial sort.
    """
    # A package router contains only HTTP declarations, never mounts or websockets.
    routes = cast(list[APIRoute], router.routes)
    keys = [route_key(route) for route in routes]
    if len(keys) != len(set(keys)):
        raise RuntimeError("duplicate route key during package initialization")
    expected, actual = set(order), set(keys)
    if actual != expected:
        raise RuntimeError(f"route roster moved: missing={expected - actual!r}, "
                           f"extra={actual - expected!r}")
    positions = {key: index for index, key in enumerate(order)}
    ordered = sorted(routes, key=lambda route: positions[route_key(route)])
    router.routes[:] = ordered
