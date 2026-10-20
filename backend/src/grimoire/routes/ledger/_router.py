"""One eager router; preserve original route objects and registration order."""

from typing import cast

from fastapi import APIRouter
from fastapi.routing import APIRoute

router = APIRouter()

# These original keys order the existing APIRoutes, never rebuild them.
_ORDER: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (('POST',), '/campaigns/{cid}/ledger/threads', 'post_thread'),
    (('PUT',), '/campaigns/{cid}/ledger/threads/{pid}', 'put_thread'),
    (('DELETE',), '/campaigns/{cid}/ledger/threads/{pid}', 'delete_thread'),
    (('POST',), '/campaigns/{cid}/ledger/commitments', 'post_commitment'),
    (('PUT',), '/campaigns/{cid}/ledger/commitments/{mid}', 'put_commitment'),
    (('DELETE',), '/campaigns/{cid}/ledger/commitments/{mid}', 'delete_commitment'),
    (('POST',), '/campaigns/{cid}/ledger/facts', 'post_fact'),
    (('PUT',), '/campaigns/{cid}/ledger/facts/{fid}', 'put_fact'),
    (('POST',), '/campaigns/{cid}/ledger/facts/{fid}/retire', 'post_fact_retire'),
    (('DELETE',), '/campaigns/{cid}/ledger/facts/{fid}', 'delete_fact'),
    (('PUT',), '/campaigns/{cid}/ledger/relationships', 'put_relationship'),
    (('DELETE',), '/campaigns/{cid}/ledger/relationships', 'delete_relationship'),
    (('PUT',), '/campaigns/{cid}/ledger/chronicle/{sid}', 'put_chronicle_line'),
    (('GET',), '/campaigns/{cid}/ledger/knowledge/scenes/{sid}', 'get_knowledge_scene'),
    (('GET',), '/campaigns/{cid}/ledger/knowledge', 'get_knowledge'),
    (('POST',), '/campaigns/{cid}/ledger/knowledge', 'post_knowledge'),
    (('PUT',), '/campaigns/{cid}/ledger/knowledge/{kid}', 'put_knowledge'),
    (('DELETE',), '/campaigns/{cid}/ledger/knowledge/{kid}', 'delete_knowledge'),
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
