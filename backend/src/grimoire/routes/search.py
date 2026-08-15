"""Keyword search across the library (#33).

One literal route, so it must be registered before the generic ``/{kind}``
catch-alls in ``entities`` -- see ``routes/__init__``, which keeps that order,
and ``tests/test_route_order.py``, which checks it.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import store

router = APIRouter()


@router.get("/search")
def search(q: str = "", scope: str = "", root: str = "",
           kinds: str = "", limit: int = store.search.DEFAULT_LIMIT):
    """Ranked hits for `q` across every world and campaign in the store.

    `scope` is `world` or `campaign`; `root` narrows to one of them by id and
    is refused without a scope, because a bare id is ambiguous -- a world and a
    campaign may both be called `saltmarch`, and answering with whichever the
    walk reached first would be a silent wrong answer rather than a 400.

    `kinds` is a comma-separated subset of `store.search.KINDS`; an unknown one
    is a 400 rather than a silently empty page, since the only way to send one
    is a client that has drifted from the vocabulary.

    An empty `q` returns an empty result. A search box is empty far more often
    than it is wrong, and a 400 before the first keystroke would be this route
    shouting at its own UI.
    """
    if root and not scope:
        raise HTTPException(status_code=400,
                            detail="root needs a scope: a bare id could name "
                                   "either a world or a campaign")
    kind_list = tuple(k.strip() for k in kinds.split(",") if k.strip())
    try:
        return store.search.search(q, scope=scope, root=root, kinds=kind_list, limit=limit)
    except store.search.BadScope:
        raise HTTPException(status_code=400,
                            detail=f"unknown scope: {scope} "
                                   f"(expected one of {', '.join(store.search.SCOPES)})")
    except store.search.BadKind as exc:
        raise HTTPException(status_code=400, detail=f"unknown kind(s): {exc}")
