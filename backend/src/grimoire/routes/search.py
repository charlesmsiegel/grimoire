"""Search across the library — keyword (#33) and semantic (#34) on one route.

One literal route, so it must be registered before the generic ``/{kind}``
catch-alls in ``entities`` -- see ``routes/__init__``, which keeps that order,
and ``tests/test_route_order.py``, which checks it.

`mode` picks the ranking, not the corpus: both modes walk the same documents
(`store.search.walk`), answer in the same envelope, and refuse the same
vocabulary. That is what makes the fallback below honest -- a semantic request
that cannot be served is answered by the *other ranking of the same question*,
not by a different question.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import store

router = APIRouter()

KEYWORD = "keyword"

#: The rankings on offer. `semantic` needs an embeddings connection and model
#: (Configuration); without one it answers in `keyword` and says so.
MODES: tuple[str, ...] = (KEYWORD, store.semsearch.MODE)


@router.get("/search")
def search(q: str = "", scope: str = "", root: str = "", kinds: str = "",
           mode: str = KEYWORD, limit: int = store.search.DEFAULT_LIMIT):
    """Ranked hits for `q` across every world and campaign in the store.

    `scope` is `world` or `campaign`; `root` narrows to one of them by id and
    is refused without a scope, because a bare id is ambiguous -- a world and a
    campaign may both be called `saltmarch`, and answering with whichever the
    walk reached first would be a silent wrong answer rather than a 400.

    `kinds` is a comma-separated subset of `store.search.KINDS`; an unknown one
    is a 400 rather than a silently empty page, since the only way to send one
    is a client that has drifted from the vocabulary. So is an unknown `mode`.

    Every answer carries `mode` -- the ranking that actually produced it --
    beside `requested_mode` and a `note`. They differ when semantic search was
    asked for and could not run: no embeddings connection, no model, or an
    endpoint that would not answer. The reader gets the keyword results and the
    note says why, which is the degradation #34 asks for; erroring instead
    would turn a missing optional setting into a broken search box.

    An empty `q` returns an empty result. A search box is empty far more often
    than it is wrong, and a 400 before the first keystroke would be this route
    shouting at its own UI.
    """
    if mode not in MODES:
        raise HTTPException(status_code=400,
                            detail=f"unknown mode: {mode} "
                                   f"(expected one of {', '.join(MODES)})")
    if root and not scope:
        raise HTTPException(status_code=400,
                            detail="root needs a scope: a bare id could name "
                                   "either a world or a campaign")
    kind_list = tuple(k.strip() for k in kinds.split(",") if k.strip())
    note = ""
    try:
        if mode == store.semsearch.MODE:
            try:
                out = store.semsearch.search_semantic(q, scope=scope, root=root,
                                                      kinds=kind_list, limit=limit)
                return {**out, "mode": store.semsearch.MODE, "requested_mode": mode,
                        "note": ""}
            except store.semsearch.Unavailable as exc:
                note = str(exc)
        out = store.search.search(q, scope=scope, root=root, kinds=kind_list, limit=limit)
    except store.search.BadScope:
        raise HTTPException(status_code=400,
                            detail=f"unknown scope: {scope} "
                                   f"(expected one of {', '.join(store.search.SCOPES)})")
    except store.search.BadKind as exc:
        raise HTTPException(status_code=400, detail=f"unknown kind(s): {exc}")
    return {**out, "mode": KEYWORD, "requested_mode": mode, "note": note}
