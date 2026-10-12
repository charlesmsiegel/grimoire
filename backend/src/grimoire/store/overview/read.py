"""The overview entry points, and the in-process layer above each projection.

Every projection here is remembered in-process by `statcache.memo_stamped`,
never `memo`: its key is `stamp`, which carries ctime, so an in-place same-size
rewrite with a restored mtime is noticed on POSIX. All of them live in one
named pool, `_OVERVIEW_POOL`, never the shared FIFO (spec 04 section 3.6).

`scene_summary` is in-process only (spec 04 section 3.4): it folds the
campaign's scene HEADS, read head-only through `scenes.read._scene_row`, so a
transcript body is never opened to answer it.
"""

from __future__ import annotations

import os
from pathlib import Path

from .. import readstats, statcache
from ..campaigns import paths as campaigns_paths
from ..campaigns import read as campaigns_read
from ..paths import safe_id
from ..scenes import paths as scenes_paths
from ..scenes import read as scenes_read
from . import kinds

#: One entry per campaign per projection, so sized like the scene-head pool
#: it sits beside rather than the shared FIFO of 4096. A structural bound, to
#: be tuned against real libraries later.
POOL_ENTRIES = scenes_read.POOL_ENTRIES
_OVERVIEW_POOL: dict = {}

#: The last stamp each scene member was folded under, by path. Lets the fold
#: notice a head rewritten in place with its mtime restored -- the stamp moved
#: (ctime) while the head memo's `signature` did not -- and re-read that head
#: rather than believe `_SCENE_POOL` (spec 04 section 3.4, the plan's choice).
_MEMBER_STAMPS: dict = {}


def _fresh_head(path: Path, stamp: statcache.Stamp) -> None:
    previous = _MEMBER_STAMPS.get(os.fspath(path))
    if previous is not None and previous != stamp and (
            (previous[1], previous[3], previous[4]) == (stamp[1], stamp[3], stamp[4])):
        # Same mtime, size and inode, different ctime: the head memo would
        # answer from the old bytes, so drop its entry before reading.
        sig = ((os.fspath(path), stamp[1], stamp[3], stamp[4]),)
        scenes_read._SCENE_POOL.pop(("scene_head", sig), None)
    statcache._put(_MEMBER_STAMPS, os.fspath(path), stamp, POOL_ENTRIES)


def _empty() -> dict:
    return {"count": 0, "unreadable": 0, "absorbed": 0, "rows": [], "open": [], "stamps": []}


def _fold(cid: str, d: Path) -> tuple[dict, tuple | None]:
    """The summary and the stamps that vouch for it, each taken before what it
    vouches for is read: the directory before its listing, a member before its
    head. An absent `scenes/` is vouched for by the campaign root, whose mtime
    its creation moves."""
    here = statcache.stamp(d)
    if here is None:
        root = statcache.stamp(campaigns_paths.campaign_root(cid))
        if d.exists():   # created between the stamp and now: do not remember
            return _fold_listed(d, None)
        return _empty(), (root,) if root is not None else None
    return _fold_listed(d, here)


def _fold_listed(d: Path, here: statcache.Stamp | None) -> tuple[dict, tuple | None]:
    stamps: list[statcache.Stamp] = [] if here is None else [here]
    vouched = here is not None
    rows: list[dict] = []
    count = unreadable = 0
    # The enumeration `list_scenes` makes: `*.md` whose stem `safe_id` accepts.
    # A listing that fails raises, exactly as `list_scenes` would.
    members = [p for p in d.glob("*.md") if safe_id(p.stem)]
    for p in members:
        count += 1
        member = statcache.stamp(p)
        if member is None:
            unreadable += 1
            vouched = False
            continue
        _fresh_head(p, member)
        try:
            row = {"id": p.stem, **scenes_read._scene_row(p)}
        except (UnicodeDecodeError, OSError):
            # One scene that cannot be read degrades that scene, never the
            # page: counted, not absorbed, not open, no title, no stamp. And
            # not remembered, so the next read tries again.
            readstats.bump("overview.unreadable_scene")
            unreadable += 1
            vouched = False
            continue
        stamps.append(member)
        rows.append(row)
    # `updated` descending, then `sid` ascending: deterministic across devices,
    # where `list_scenes`' stable sort over directory order is not.
    rows.sort(key=lambda r: r["id"])
    rows.sort(key=lambda r: r["updated"], reverse=True)
    scenes_read._resolve_groups(rows)
    value = {
        "count": count,
        "unreadable": unreadable,
        "absorbed": sum(1 for r in rows if r["done"]),
        "rows": [{"sid": r["id"], "title": r["title"], "updated": r["updated"], "done": r["done"],
                  "closed_by": dict(r["closed_by"]) if r.get("closed_by") else None} for r in rows],
        "open": [{"sid": r["id"], "title": r["title"]} for r in rows
                 if not r["done"] and not r.get("closed_by")],
        # The clock-free half of `best_stamp`: `GET /campaigns` folds these
        # through it live, so the remembered value never depends on "now".
        "stamps": sorted({r["updated"] for r in rows if campaigns_read.well_formed_stamp(r["updated"])},
                         reverse=True),
    }
    return value, tuple(stamps) if vouched else None


def _copy(summary: dict) -> dict:
    return {**summary,
            "rows": [{**r, "closed_by": dict(r["closed_by"]) if r["closed_by"] else None}
                     for r in summary["rows"]],
            "open": [dict(r) for r in summary["open"]],
            "stamps": list(summary["stamps"])}


def scene_summary(cid: str) -> dict:
    """The campaign's scene heads folded into what the campaign card, the
    shell and Todo share (spec 04 section 3.4)::

        {"count": int,        # every listed member, readable or not
         "unreadable": int,   # members whose head could not be read or decoded
         "absorbed": int,     # readable rows with done
         "rows": [{sid, title, updated, done, closed_by|None}, ...],
         "open": [{sid, title}, ...],   # not done and not closed, row order
         "stamps": [str, ...]}          # distinct well-formed `updated`, newest first

    Raises `CampaignNotFound` for an id that names no campaign, and the
    listing's `OSError` for a `scenes/` that cannot be listed -- the two
    failures `list_scenes` raises, so callers keep their mappings. A warm read
    costs one stat of the directory and one per member, and no listing.
    """
    scenes_paths._require_campaign(cid)
    d = scenes_paths._scenes_dir(cid)
    summary = statcache.memo_stamped(("overview.scene_summary", os.fspath(d)), lambda: _counted(cid, d),
                                     pool=_OVERVIEW_POOL, max_entries=POOL_ENTRIES)
    return _copy(summary)


def _counted(cid: str, d: Path) -> tuple[dict, tuple | None]:
    readstats.bump("overview.computed.scene_summary")
    return _fold(cid, d)


def continuity_summary(cid: str) -> dict:
    """The campaign's ledger figures for the shell's badge and Todo's `owed`
    and continuity chores (`kinds.continuity_live` for the shape), computed
    from the bytes of the five files it reads and remembered in-process on
    their stamps -- an absent file vouched for by the campaign root's stamp,
    whose mtime its creation moves (spec 04 section 3.6).

    A file that cannot be read (anything but absence) answers through the
    live path for this request and nothing is remembered. Raises only
    `campaign_root`'s `CampaignNotFound` for an id that cannot name a
    campaign; every other failure is already part of the value."""
    root = campaigns_paths.campaign_root(cid)
    value = statcache.memo_stamped(("overview.continuity_summary", os.fspath(root)),
                                   lambda: _continuity(cid, root),
                                   pool=_OVERVIEW_POOL, max_entries=POOL_ENTRIES)
    return dict(value)


def _continuity(cid: str, root: Path) -> tuple[dict, tuple | None]:
    readstats.bump("overview.computed.continuity_summary")
    here = statcache.stamp(root)
    files: dict[str, bytes | None] = {}
    stamps: list[statcache.Stamp] = []
    vouched = True
    absent = False
    for name, path in kinds.continuity_paths(cid).items():
        observed = statcache.stamp(path)   # before the bytes it vouches for
        if observed is None:
            files[name], absent = None, True
            continue
        try:
            files[name] = path.read_bytes()
        except FileNotFoundError:
            files[name], absent, vouched = None, True, False   # gone since its stamp
            continue
        except OSError:
            return kinds.continuity_live(cid), None
        stamps.append(observed)
    value = kinds.continuity(cid, files)
    if absent:
        if here is None:
            vouched = False
        else:
            stamps.insert(0, here)
    return value, tuple(stamps) if vouched else None
