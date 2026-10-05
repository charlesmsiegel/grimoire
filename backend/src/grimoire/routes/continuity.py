"""The continuity capstone's reviewed decisions over plot threads and commitments
(spec §21): aliases (a duplicate merged into its canonical), links (a reviewed
relation between two records), and the read that shows both beside what the
effective view made of them.

The read is pure -- no model, no embedding -- and answers whatever shape the
files are in, because it is what a reader opens to find out what is wrong:
records it cannot interpret are reported with a reason rather than raised on,
and a ledger that will not parse makes existence unknown rather than turning
every merge into it into a "broken" row.

The writes are `continuity.review`'s, journalled as ``manual`` and undoable from
the play view's Changes panel. A refusal is a `review.RefusedError`, answered as
``{"kind", "detail", ...}`` so the client can act on the kind.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import store
from ..store.continuity import doc, effective, review
from .models import ContinuityAliasCreate, ContinuityLinkCreate

router = APIRouter()


def _campaign_or_404(cid: str) -> None:
    if not store.campaigns.campaign_exists(cid):
        raise HTTPException(status_code=404, detail="campaign not found")


def _refusal(e: review.RefusedError) -> HTTPException:
    return HTTPException(status_code=e.status,
                         detail={"kind": e.kind, "detail": e.detail, **e.extra})


def _matching() -> str:
    """"semantic" iff an embeddings connection and model resolve (spec §3.3)."""
    try:
        return "semantic" if store.embed_space.resolve() is not None else "basic"
    except Exception:  # noqa: BLE001 -- an unreadable config means basic matching
        return "basic"


def _text(value) -> str:
    return value if isinstance(value, str) else ""


@router.get("/campaigns/{cid}/continuity")
def get_continuity(cid: str):
    _campaign_or_404(cid)
    with store.locks.best_effort_campaign_lock(cid):
        data = doc.read(cid)
        ledgers = effective.Ledgers.load(cid)
        live = effective.live_canon(cid, ledgers)
        diagnostics = effective.diagnostics(cid, ledgers)
        kept = effective.links(cid, ledgers)
        malformed = doc.malformed(cid)
    dangling = {d["ref"]: d["reason"] for d in diagnostics["dangling_aliases"]}
    aliases = []
    for ref in sorted(k for k in data["aliases"] if isinstance(k, str)):
        record = data["aliases"][ref]
        to = _text(record.get("to")) if isinstance(record, dict) else ""
        meta = record if isinstance(record, dict) else {}
        aliases.append({
            "ref": ref, "to": to, "canonical": live.get(ref, ref),
            "title": review.describe(cid, ref, ledgers),
            "to_title": review.describe(cid, to, ledgers) if to else "",
            "created": _text(meta.get("created")), "source": _text(meta.get("source")),
            "note": _text(meta.get("note")),
            "dangling": ref in dangling, "reason": dangling.get(ref, ""),
        })
    links = [{**link, "a_title": review.describe(cid, link["a"], ledgers),
              "b_title": review.describe(cid, link["b"], ledgers)} for link in kept]
    states = {d["id"]: ("broken", d["reason"]) for d in diagnostics["broken_links"]}
    states.update({d["id"]: ("hidden", d["reason"]) for d in diagnostics["hidden_links"]})
    raw_links = []
    for lid in sorted(k for k in data["links"] if isinstance(k, str)):
        record = data["links"][lid]
        meta = record if isinstance(record, dict) else {}
        state, reason = states.get(lid, ("ok", ""))
        raw_links.append({"id": lid, **{k: _text(meta.get(k)) for k in
                                        ("a", "b", "relation", "scene", "note", "created")},
                          "state": state, "reason": reason})
    suppressions = []
    for fp in sorted(k for k in data["suppressions"] if isinstance(k, str)):
        meta = data["suppressions"][fp]
        meta = meta if isinstance(meta, dict) else {}
        refs = meta.get("refs")
        suppressions.append({
            "fingerprint": fp, "kind": _text(meta.get("kind")),
            "refs": [r for r in refs if isinstance(r, str)] if isinstance(refs, list) else [],
            "decision": _text(meta.get("decision")), "created": _text(meta.get("created"))})
    return {"aliases": aliases, "links": links, "raw_links": raw_links,
            "suppressions": suppressions, "diagnostics": diagnostics,
            "malformed": malformed, "unreadable": diagnostics["unreadable"],
            "matching": _matching()}


@router.post("/campaigns/{cid}/continuity/aliases")
def post_alias(cid: str, body: ContinuityAliasCreate):
    _campaign_or_404(cid)
    try:
        return review.create_alias(cid, body.ref, body.to, replace=bool(body.replace),
                                   accept_status_change=bool(body.accept_status_change),
                                   note=body.note or "")
    except review.RefusedError as e:
        raise _refusal(e) from e


@router.delete("/campaigns/{cid}/continuity/aliases")
def delete_alias(cid: str, ref: str):
    """By query (`?ref=`), not by path: refs contain ``:`` and a model-written
    plot id may contain ``/``."""
    _campaign_or_404(cid)
    try:
        return review.remove_alias(cid, ref)
    except review.RefusedError as e:
        raise _refusal(e) from e


@router.post("/campaigns/{cid}/continuity/links")
def post_link(cid: str, body: ContinuityLinkCreate):
    _campaign_or_404(cid)
    try:
        return review.create_link(cid, body.a, body.b, body.relation,
                                  scene=body.scene or "", note=body.note or "")
    except review.RefusedError as e:
        raise _refusal(e) from e


@router.delete("/campaigns/{cid}/continuity/links/{lid}")
def delete_link(cid: str, lid: str):
    _campaign_or_404(cid)
    try:
        return review.remove_link(cid, lid)
    except review.RefusedError as e:
        raise _refusal(e) from e
