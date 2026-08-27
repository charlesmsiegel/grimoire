"""The list of everything the app noticed, and the ignore that silences one.

Every entry is derived on the way out. There is no chore table on disk and
nothing is cached: a chore whose count is zero does not appear, and the number
in its label is the number this request computed. That is what makes the list
worth opening twice -- a to-do list that can go stale teaches the reader to
distrust it, and then the one entry that mattered is the one they scroll past.

The cost rule from `routes/shell.py` applies here for the same reason: a chore
that cannot be counted cheaply is not offered. This page is opened casually and
answers about a whole library, so a count proportional to the library's age
would make it a page nobody opens. Where a useful chore has no cheap source it
waits for one rather than shipping a guess.

`store.chores` owns the ignore set; everything else here is a read of the
stores the rest of the app already reads.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from .. import store
from .common import _dump  # noqa: F401  (kept for the pydantic-agnostic rule)

router = APIRouter()


def _slugish(name: str) -> str:
    """`name` as it would most obviously slugify, for deciding whether an id is
    worth showing beside it. Not `paths.safe_id` -- this is a display question
    ("does the id tell the reader anything the name did not"), not an
    addressing one, and being approximate here costs nothing."""
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")


def _character_gaps(cid: str) -> tuple[list[dict], list[dict]]:
    """(no tagline, no voice anchor) across the campaign's EFFECTIVE roster.

    Through `overlay.list_characters`, and that is the whole point of this
    function rather than an implementation note. A campaign is copy-on-write
    over its world, so reading the world root directly gets three things wrong
    at once, and this got all three:

    - a character the campaign DELETED is still in the world, so it was
      reported as missing a tagline in a campaign it is not in;
    - `tagline.md` and `voice_anchor.md` are sidecars that overlay per file, so
      a tagline written campaign-side did not count and the character was
      reported as lacking one;
    - a materialized actor is authoritative for its own meta, so a campaign
      copy's tagline was invisible behind the world's.

    `list_characters` already resolves both fields -- `tagline` per file and
    `has_voice_anchor` off one directory scan rather than a read per row -- so
    this is also cheaper than the walk it replaces.
    """
    try:
        rows = store.overlay.list_characters(cid)
    except (store.CampaignNotFound, OSError):
        return [], []
    no_tagline: list[dict] = []
    no_anchor: list[dict] = []
    for row in rows:
        name = str(row.get("name") or row["id"])
        # The slug, when it is not just the name lowercased. It is what
        # addresses the character in a ref and what names the file, so it is
        # the one fact that turns a list of names into something you can act
        # on -- and a roster with two Maras is exactly when it matters.
        slug = row["id"] if row["id"] != _slugish(name) else ""
        if not str(row.get("tagline") or "").strip():
            no_tagline.append({"id": row["id"], "label": name, "detail": slug})
        if not row.get("has_voice_anchor"):
            no_anchor.append({"id": row["id"], "label": name, "detail": slug})
    return no_tagline, no_anchor


def _chore_unreviewed(cid: str, scenes: list[dict]) -> dict | None:
    # Off the same walk the expansion uses, so the count on the row and the
    # rows behind it cannot disagree about what is waiting.
    waiting = _items_unreviewed(cid)
    n = sum(int(w.get("proposals", 0)) for w in waiting)
    sids = [w["id"] for w in waiting]
    if not n:
        return None
    return {
        "id": "unreviewed", "group": "Waiting on you", "severity": "alert", "n": n,
        "what": f"{n} absorb proposal{'s' if n != 1 else ''} unreviewed",
        "why": "A scene was absorbed but never reviewed. Until it is, none of what "
               "it found has reached the world.",
        "fix": f"/campaigns/{cid}/scenes/{sids[0]}" if sids else f"/campaigns/{cid}/scenes",
        "fix_label": "Open wrap-up",
    }


def _chore_open_scenes(cid: str, scenes: list[dict]) -> dict | None:
    n = sum(1 for s in scenes if not s["done"])
    if n < 2:
        return None
    return {
        "id": "open-scenes", "group": "Continuity", "severity": "note", "n": n,
        "what": f"{n} scenes are open at once",
        "why": "Each one holds part of the campaign's present. Wrapping one up is "
               "what moves the chronicle forward.",
        "fix": f"/campaigns/{cid}/scenes", "fix_label": "See the scenes",
    }


def _chore_sheets(cid: str, scenes: list[dict]) -> dict | None:
    try:
        cov = store.sheets.coverage(cid)
    except (OSError, KeyError, ValueError):
        return None
    n = sum(k["total"] - k["sheeted"] for k in cov.values()) if cov else 0
    if not n:
        return None
    return {
        "id": "sheets", "group": "World content", "severity": "warn", "n": n,
        "what": f"{n} cast member{'s' if n != 1 else ''} without a sheet",
        "why": "A character with no sheet cannot be rolled for, so the module this "
               "campaign binds does not apply to them.",
        "fix": f"/campaigns/{cid}/sheets", "fix_label": "Sheet coverage",
    }


def _chore_anchors(cid: str, scenes: list[dict]) -> dict | None:
    who = _character_gaps(cid)[1]
    n = len(who)
    if not n:
        return None
    return {
        "id": "anchors", "group": "Voice & character", "severity": "warn", "n": n,
        "what": f"{n} character{'s' if n != 1 else ''} with no voice anchor",
        "why": "Without one nothing measures whether a reply still sounds like them, "
               "so drift goes unreported rather than absent.",
        "fix": f"/campaigns/{cid}/world", "fix_label": "The cast",
    }


def _chore_taglines(cid: str, scenes: list[dict]) -> dict | None:
    who = _character_gaps(cid)[0]
    n = len(who)
    if not n:
        return None
    return {
        "id": "taglines", "group": "World content", "severity": "note", "n": n,
        "what": f"{n} character{'s' if n != 1 else ''} with no tagline",
        "why": "The tagline is what a browse grid and a scene suggestion have to go "
               "on before anything else is read.",
        "fix": f"/campaigns/{cid}/world", "fix_label": "The cast",
    }


def _chore_owed(cid: str, scenes: list[dict]) -> dict | None:
    try:
        owed = [c for c in store.commitments.open_commitments(cid) if c.get("due")]
    except (OSError, ValueError):
        return None
    if not owed:
        return None
    return {
        "id": "owed", "group": "Continuity", "severity": "warn", "n": len(owed),
        "what": f"{len(owed)} open thread{'s' if len(owed) != 1 else ''} with a deadline",
        "why": "A promise with a date is the kind the campaign is expected to answer, "
               "and the ledger is where it is still waiting.",
        "fix": f"/campaigns/{cid}/ledger", "fix_label": "The ledger",
    }


def _chore_unpriced(cid: str, scenes: list[dict]) -> dict | None:
    """Models the ledger holds that no pricing entry matches.

    The chore exists because the failure is silent: a table is matched by model
    string, so an entry whose key does not match what was actually recorded
    prices nothing and says nothing. Naming the strings is the whole fix -- the
    reader can then see that their entry reads `z.ai/...` where the ledger says
    `z-ai/...`, which no amount of staring at a rollup would reveal.
    """
    try:
        models = store.usage.unpriced_models()
    except (OSError, ValueError):
        return None
    if not models:
        return None
    calls = sum(m["calls"] for m in models)
    names = ", ".join(m["model"] for m in models[:3])
    more = "" if len(models) <= 3 else f", and {len(models) - 3} more"
    return {
        "id": "unpriced", "group": "Housekeeping", "severity": "warn", "n": calls,
        "what": f"{calls} call{'s' if calls != 1 else ''} no pricing entry matches",
        "why": f"Nobody reported a price for these and your table has no rate that "
               f"matches them, so they are counted rather than costed: {names}{more}. "
               f"The model string has to match exactly.",
        "fix": "/config", "fix_label": "Pricing",
    }


#: One builder per chore, in the order they are worth doing. Proposals holding
#: the world back lead regardless of count -- they are the only thing here that
#: blocks play. Adding a chore is one entry and one function.
BUILDERS = (_chore_unreviewed, _chore_open_scenes, _chore_sheets,
            _chore_anchors, _chore_taglines, _chore_owed, _chore_unpriced)

#: Every id `BUILDERS` can emit. The ignore route checks against this rather
#: than accepting an id blind: a set that accumulates ids nothing emits grows
#: forever and silences things nobody can name.
KNOWN = frozenset({"unreviewed", "open-scenes", "sheets", "anchors",
                   "taglines", "owed", "unpriced"})


def _chores(cid: str) -> list[dict]:
    """Every chore with a non-zero count. A chore at zero is not in the list."""
    if not cid:
        return []
    try:
        scenes = store.scenes.read.list_scenes(cid)
    except (store.CampaignNotFound, OSError):
        return []
    return [c for c in (b(cid, scenes) for b in BUILDERS) if c]


#: How many instances one chore will list. A chore can cover a whole roster,
#: and a page that renders every one of several hundred is a page that stops
#: being a to-do list. What is dropped is REPORTED (`truncated`), because a
#: silently short list reads as a complete one -- the same rule the rest of the
#: app keeps about capped reads.
ITEM_CAP = 200


def _items_unreviewed(cid: str) -> list[dict]:
    """One row per waiting scene, naming what it is holding."""
    out = []
    d = store.scenes.paths._scenes_dir(cid)   # paths-ok: the resolver itself
    if not d.exists():
        return []
    titles = {s["id"]: s["title"] for s in store.scenes.read.list_scenes(cid)}
    for path in sorted(d.glob("*.review.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            edits = rec.get("review", {}).get("edits", [])
        except (OSError, ValueError, AttributeError):
            continue
        sid = path.name[: -len(".review.json")]
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
                    # Carried so the chore's headline count comes off this same
                    # walk. Harmless on the wire; the page ignores it.
                    "proposals": len(edits),
                    "fix": f"/campaigns/{cid}/scenes/{sid}"})
    return out


def _items_open_scenes(cid: str) -> list[dict]:
    out = []
    for sc in store.scenes.read.list_scenes(cid):
        if sc["done"]:
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
    return [{**c, "fix": f"/campaigns/{cid}/world"} for c in _character_gaps(cid)[1]]


def _items_taglines(cid: str) -> list[dict]:
    return [{**c, "fix": f"/campaigns/{cid}/world"} for c in _character_gaps(cid)[0]]


def _items_owed(cid: str) -> list[dict]:
    try:
        owed = [c for c in store.commitments.open_commitments(cid) if c.get("due")]
    except (OSError, ValueError):
        return []
    return [{"id": c["id"], "label": c["title"],
             "detail": " · ".join(x for x in (f"due {c['due']}", c.get("kind"),
                                              c.get("latest_beat")) if x),
             "fix": f"/campaigns/{cid}/ledger"} for c in owed]


def _items_unpriced(cid: str) -> list[dict]:
    try:
        models = store.usage.unpriced_models()
    except (OSError, ValueError):
        return []
    return [{"id": m["model"], "label": m["model"],
             "detail": f"{m['calls']} calls that a rate would price",
             "fix": "/config"} for m in models]


#: What expanding a chore shows. Deliberately a SECOND pass rather than part of
#: the list: naming every instance of every chore on every read is the cost the
#: list is built to avoid, and the reader only ever expands one.
ITEMS = {
    "unreviewed": _items_unreviewed,
    "open-scenes": _items_open_scenes,
    "sheets": _items_sheets,
    "anchors": _items_anchors,
    "taglines": _items_taglines,
    "owed": _items_owed,
    "unpriced": _items_unpriced,
}


def live(cid: str) -> dict:
    """The chore list split into what counts and what has been waved off."""
    off = store.chores.ignored()
    every = _chores(cid)
    return {
        "chores": [c for c in every if c["id"] not in off],
        "ignored": [c for c in every if c["id"] in off],
        # The badge number, and it is the one the reader still cares about:
        # an ignored chore is not counted anywhere.
        "count": sum(1 for c in every if c["id"] not in off),
    }


@router.get("/todo")
def get_todo(campaign: str = ""):
    return live(campaign)


@router.get("/todo/{chore_id}/items")
def get_todo_items(chore_id: str, campaign: str = ""):
    """The instances behind one chore's count.

    On demand, because this is the expensive half: `sheets` sweeps the cast and
    `taglines` walks the roster, and doing that for every chore whenever the
    page opens is what would make the page not worth opening. The reader
    expands one.
    """
    if chore_id not in KNOWN:
        raise HTTPException(400, f"unknown chore: {chore_id}")
    if not campaign:
        return {"items": [], "total": 0, "truncated": False}
    items = ITEMS[chore_id](campaign)
    return {"items": items[:ITEM_CAP], "total": len(items),
            # Said out loud rather than left to be inferred from a short list:
            # a cap nobody mentions reads as "that is all of them".
            "truncated": len(items) > ITEM_CAP}


@router.put("/todo/{chore_id}/ignored")
def put_todo_ignored(chore_id: str, body: dict):
    """Wave a chore off, or take it back.

    The id is checked against the chores this store can actually produce rather
    than accepted blind: an ignore set that accumulates ids nothing emits is a
    file that grows forever and silences things nobody can name.
    """
    if chore_id not in KNOWN:
        raise HTTPException(400, f"unknown chore: {chore_id}")
    on = bool(body.get("ignored"))
    store.chores.set_ignored(chore_id, on)
    return {"ok": True, "ignored": sorted(store.chores.ignored())}
