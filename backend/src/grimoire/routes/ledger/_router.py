"""One eager router; preserve original route objects and registration order."""

from fastapi import APIRouter

from .._route_order import RouteKey, order_routes

router = APIRouter()

# These original keys order the existing APIRoutes, never rebuild them.
_ORDER: tuple[RouteKey, ...] = (
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


def finalize() -> None:
    """Refuse an incomplete roster before reordering the same route list."""
    order_routes(router, _ORDER)
