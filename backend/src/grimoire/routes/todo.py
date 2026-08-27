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


def _character_gaps(cid: str) -> tuple[int, int]:
    """(no tagline, no voice anchor) across the campaign's world roster.

    One frontmatter head read and one stat per character -- deliberately not
    `characters.list_characters`, which also loads every card summary and image
    listing to build a browse grid. That is the right read for a page showing
    the roster and the wrong one for counting two fields.
    """
    try:
        root = store.campaigns.read.world_root_of(cid)
    except (store.CampaignNotFound, OSError):
        return 0, 0
    d = root / "characters"
    if not d.exists():
        return 0, 0
    no_tagline = no_anchor = 0
    for cd in sorted(p for p in d.iterdir() if p.is_dir()):
        meta_path = cd / "character.md"
        if not meta_path.exists() or not store.paths.safe_id(cd.name):
            continue
        try:
            meta = store.frontmatter.parse_frontmatter_head(meta_path)
        except OSError:
            continue
        if not str(meta.get("tagline", "")).strip():
            no_tagline += 1
        # `anchor_path` refuses an id it would not serve back; an unusable one
        # is not a character missing an anchor, so it is skipped rather than
        # counted.
        try:
            if not store.voice_anchors.anchor_path(root, cd.name).exists():
                no_anchor += 1
        except (ValueError, OSError):
            pass
    return no_tagline, no_anchor


def _pending_reviews(cid: str) -> tuple[int, list[str]]:
    """Proposals waiting, and the scenes holding them. A listing, not a scan."""
    total, sids = 0, []
    d = store.scenes.paths._scenes_dir(cid)   # paths-ok: the resolver itself
    if not d.exists():
        return 0, []
    for p in sorted(d.glob("*.review.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            total += len(rec.get("review", {}).get("edits", []))
        except (OSError, ValueError, AttributeError):
            continue
        sids.append(p.name[: -len(".review.json")])
    return total, sids


def _chore_unreviewed(cid: str, scenes: list[dict]) -> dict | None:
    n, sids = _pending_reviews(cid)
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
    n = _character_gaps(cid)[1]
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
    n = _character_gaps(cid)[0]
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


#: One builder per chore, in the order they are worth doing. Proposals holding
#: the world back lead regardless of count -- they are the only thing here that
#: blocks play. Adding a chore is one entry and one function.
BUILDERS = (_chore_unreviewed, _chore_open_scenes, _chore_sheets,
            _chore_anchors, _chore_taglines, _chore_owed)

#: Every id `BUILDERS` can emit. The ignore route checks against this rather
#: than accepting an id blind: a set that accumulates ids nothing emits grows
#: forever and silences things nobody can name.
KNOWN = frozenset({"unreviewed", "open-scenes", "sheets", "anchors",
                   "taglines", "owed"})


def _chores(cid: str) -> list[dict]:
    """Every chore with a non-zero count. A chore at zero is not in the list."""
    if not cid:
        return []
    try:
        scenes = store.scenes.read.list_scenes(cid)
    except (store.CampaignNotFound, OSError):
        return []
    return [c for c in (b(cid, scenes) for b in BUILDERS) if c]


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
