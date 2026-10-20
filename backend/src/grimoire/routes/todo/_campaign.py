"""todo: campaign responsibilities."""

from __future__ import annotations

from ... import store
from . import _context, _sources
from ._router import router as router


def _chore_unreviewed(ctx: _context._Ctx) -> dict | None:
    cid = ctx.cid
    # Off the same sidecar walk the expansion uses, so the count on the row
    # and the rows behind it cannot disagree about what is waiting. The walk,
    # not the expansion itself: the chore needs no titles, and the titles are
    # a scene listing the expansion pays only when something is waiting.
    waiting = _sources._review_sidecars(cid)
    n = sum(len(edits) for _sid, edits in waiting)
    sids = [sid for sid, _edits in waiting]
    if not n:
        return None
    return {
        "id": "unreviewed", "scope": "campaign", "group": "Wrap-up", "severity": "alert", "n": n,
        "what": f"{n} absorb proposal{'s' if n != 1 else ''} unreviewed",
        "why": "A scene was absorbed but never reviewed. Until it is, none of what "
               "it found has reached the world.",
        "fix": f"/campaigns/{cid}/scenes/{sids[0]}" if sids else f"/campaigns/{cid}/scenes",
        "fix_label": "Open wrap-up",
    }


def _chore_open_scenes(ctx: _context._Ctx) -> dict | None:
    cid, summary = ctx.cid, ctx.scene_summary()
    # A closed branch (a sibling was absorbed) is read-only, not unfinished;
    # the summary's `open` already leaves it out.
    n = len(summary["open"]) if summary else 0
    if not n:
        return None
    return {
        "id": "open-scenes", "scope": "campaign", "group": "Scenes", "severity": "note", "n": n,
        "what": f"{n} incomplete scene{'s' if n != 1 else ''}",
        "why": "Each one holds part of the campaign's present. Wrapping one up is "
               "what moves the chronicle forward.",
        "fix": f"/campaigns/{cid}/scenes", "fix_label": "See the scenes",
    }


def _chore_sheets(ctx: _context._Ctx) -> dict | None:
    cid = ctx.cid
    try:
        cov = ctx.coverage()
    except (OSError, KeyError, ValueError):
        return None
    n = sum(k["total"] - k["sheeted"] for k in cov.values()) if cov else 0
    if not n:
        return None
    return {
        "id": "sheets", "scope": "campaign", "group": "Game mechanics", "severity": "warn", "n": n,
        "what": f"{n} cast member{'s' if n != 1 else ''} without a sheet",
        "why": "A character with no sheet cannot be rolled for, so the module this "
               "campaign binds does not apply to them.",
        "fix": f"/campaigns/{cid}/sheets", "fix_label": "Sheet coverage",
    }


def _chore_anchors(ctx: _context._Ctx) -> dict | None:
    cid = ctx.cid
    who = ctx.character_gaps()[1]
    n = len(who)
    if not n:
        return None
    return {
        "id": "anchors", "scope": "campaign", "group": "Character & voice", "severity": "warn", "n": n,
        "what": f"{n} character{'s' if n != 1 else ''} with no voice anchor",
        "why": "Without one nothing measures whether a reply still sounds like them, "
               "so drift goes unreported rather than absent.",
        "fix": f"/campaigns/{cid}/world", "fix_label": "The cast",
    }


def _chore_taglines(ctx: _context._Ctx) -> dict | None:
    cid = ctx.cid
    who = ctx.character_gaps()[0]
    n = len(who)
    if not n:
        return None
    return {
        "id": "taglines", "scope": "campaign", "group": "Character & voice", "severity": "note", "n": n,
        "what": f"{n} character{'s' if n != 1 else ''} with no tagline",
        "why": "The tagline is what a browse grid and a scene suggestion have to go "
               "on before anything else is read.",
        "fix": f"/campaigns/{cid}/world", "fix_label": "The cast",
    }


def _chore_avatars(ctx: _context._Ctx) -> dict | None:
    who = ctx.avatar_gaps()
    if not who:
        return None
    n = len(who)
    return {
        "id": "avatars", "scope": "campaign", "group": "Character & voice", "severity": "note", "n": n,
        "what": f"{n} character{'s' if n != 1 else ''} without an avatar",
        "why": "The default version has no portrait to show in the cast grid.",
        "fix": f"/campaigns/{ctx.cid}/world", "fix_label": "The cast",
    }


def _chore_cover(ctx: _context._Ctx) -> dict | None:
    if not _sources._items_cover(ctx.cid):
        return None
    return {
        "id": "cover", "scope": "campaign", "group": "Artwork", "severity": "note", "n": 1,
        "what": "Campaign without a cover",
        "why": "The campaign has no cover for its shelf thumbnail or exported book.",
        "fix": f"/campaigns/{ctx.cid}", "fix_label": "Campaign settings",
    }


def _items_unreviewed(cid: str) -> list[dict]:
    """One row per waiting scene, naming what it is holding."""
    out = []
    waiting = _sources._review_sidecars(cid)
    if not waiting:
        return []
    # The titles are a listing of every scene, paid only once a sidecar has
    # been found: on demand, and live (spec 04 section 3.8).
    titles = {s["id"]: s["title"] for s in store.scenes.read.list_scenes(cid)}
    for sid, edits in waiting:
        # The kinds it is proposing, which is what tells the reader whether
        # this is a dossier rewrite or one fact nobody cited.
        kinds: dict[str, int] = {}
        for e in edits:
            if isinstance(e, dict):
                k = str(e.get("kind", "?"))
                kinds[k] = kinds.get(k, 0) + 1
        detail = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]))
        out.append({"id": sid, "label": titles.get(sid, sid),
                    "detail": detail or f"{len(edits)} proposals",
                    # The same count the chore's headline sums off
                    # `_review_sidecars`. Harmless on the wire; the page
                    # ignores it.
                    "proposals": len(edits),
                    "fix": f"/campaigns/{cid}/scenes/{sid}"})
    return out


def _items_open_scenes(cid: str) -> list[dict]:
    out = []
    for sc in store.scenes.read.list_scenes(cid):
        if sc["done"] or sc.get("closed_by"):
            continue
        where = " · ".join(x for x in (sc.get("date"), sc.get("place")) if x)
        out.append({"id": sc["id"], "label": sc["title"],
                    "detail": where or "no time or place set",
                    "fix": f"/campaigns/{cid}/scenes/{sc['id']}"})
    return out


def _items_sheets(cid: str) -> list[dict]:
    """Cast members with no sheet, by kind.

    `roster` rather than `coverage`: a tally cannot say WHICH, and a chore that
    offers to fix a gap has to name it.
    """
    try:
        roster = store.sheets.roster(cid)
    except (OSError, KeyError, ValueError):
        return []
    out = []
    for kind, rows in sorted(roster.items()):
        for r in rows:
            if r.get("sheeted"):
                continue
            out.append({"id": f"{kind}:{r['id']}", "label": r.get("name", r["id"]),
                        "detail": kind, "fix": f"/campaigns/{cid}/sheets"})
    return out


def _items_anchors(cid: str) -> list[dict]:
    return [{**c, "fix": f"/campaigns/{cid}/world"} for c in _sources._character_gaps(cid)[1]]


def _items_taglines(cid: str) -> list[dict]:
    return [{**c, "fix": f"/campaigns/{cid}/world"} for c in _sources._character_gaps(cid)[0]]
