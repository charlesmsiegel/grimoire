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

from .. import (
    characters,
    compiled,
    covers,
    entities,
    greetings,
    ledger_texts,
    pcs,
    readstats,
    statcache,
)
from ..appearances import cast
from ..campaigns import paths as campaigns_paths
from ..campaigns import read as campaigns_read
from ..paths import ensure_home, safe_id
from ..scenes import paths as scenes_paths
from ..scenes import read as scenes_read
from ..worlds import paths as worlds_paths
from ..worlds import read as worlds_read
from . import kinds, persist

#: One entry per campaign per projection, so sized like the scene-head pool
#: it sits beside rather than the shared FIFO of 4096. A structural bound, to
#: be tuned against real libraries later.
POOL_ENTRIES = scenes_read.POOL_ENTRIES
_OVERVIEW_POOL: dict = {}

#: Below this many bytes a scene's turn count is never persisted: parsing the
#: transcript costs less than a compiled lookup's fixed cost (the bench's
#: per-kind measurement, `scripts/bench_overview.py`), so a short scene keeps
#: only the in-process memo. Admission starts at twice the crossover the
#: synthetic profiles showed, as `tokens.PERSIST_MIN_CHARS` does; a structural
#: bound, to be tuned against real prompts later, in conversation.
SCENE_TURNS_PERSIST_MIN_BYTES = 384 * 1024

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
    """The in-process miss: every input stamped first (the root too, which
    vouches for an absent one), then the value through the persisted layer."""
    here = statcache.stamp(root)
    paths = kinds.continuity_paths(cid)
    observed = {name: statcache.stamp(path) for name, path in paths.items()}
    failed: list[bool] = []

    def live() -> dict:
        # Today's code, fed the bytes it would have read. A read that fails
        # (anything but absence) answers through the path readers instead,
        # and nothing is remembered.
        files: dict[str, bytes | None] = {}
        for name, path in paths.items():
            try:
                files[name] = path.read_bytes()
            except FileNotFoundError:
                files[name] = None
            except OSError:
                failed.append(True)
                return kinds.continuity_live(cid)
        return kinds.continuity(cid, files)

    value = persist.derive(compiled.OVERVIEW_CONTINUITY,
                           [compiled.Input(name, path) for name, path in paths.items()], {},
                           lambda files: kinds.continuity(cid, files), live)
    stamps = [s for s in observed.values() if s is not None]
    vouched = not failed
    if len(stamps) < len(observed):   # an absent file: the root vouches for it
        if here is None:
            vouched = False
        else:
            stamps.insert(0, here)
    return value, tuple(stamps) if vouched else None


def scene_turns(cid: str, sid: str) -> int | None:
    """`scenes._model_blocks`' count over one scene's transcript, remembered
    in-process on the scene file's stamp and the sorted names of its seated
    players (which decide which messages are user-side), so a cast change
    misses rather than answering for the old cast (spec 04 section 3.6).

    Raises what the shell's read raised -- `SceneNotFound`, `CampaignNotFound`,
    and the player or scene read's `OSError` -- and the shell keeps mapping
    each to `None`. A transcript whose bytes do not decode is `None`, a
    property of those bytes, and remembered as such."""
    p = scenes_paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise scenes_paths.SceneNotFound(sid)
    players = tuple(sorted(cast.player_names(cid, sid)))
    return statcache.memo_stamped(("overview.scene_turns", os.fspath(p), players),
                                  lambda: _turns(cid, sid, p, players),
                                  pool=_OVERVIEW_POOL, max_entries=POOL_ENTRIES)


def _turns(cid: str, sid: str, p: Path,
           players: tuple[str, ...]) -> tuple[int | None, tuple | None]:
    """The in-process miss: the file stamped first, then the count through the
    persisted layer (the byte-fed `kinds.scene_turns`), whose live path is
    `read_scene` and `_model_blocks`, exactly as the shell counted before 04.
    A read that raises is not remembered."""
    observed = statcache.stamp(p)   # before the bytes it vouches for

    def live() -> int | None:
        try:
            return kinds.scene_turns_live(cid, sid)
        except UnicodeDecodeError:
            return None   # a property of the bytes: remembered
    if observed is None or observed[3] < SCENE_TURNS_PERSIST_MIN_BYTES:
        readstats.bump("overview.computed.scene_turns")
        return live(), (observed,) if observed is not None else None
    value = persist.derive(compiled.OVERVIEW_SCENE_TURNS, [compiled.Input("scene", p)],
                           {"players": list(players)},
                           lambda files: kinds.scene_turns(files["scene"] or b"", players), live)
    return value, (observed,) if observed is not None else None


# ---- card rows (04-C1a) ----------------------------------------------------

def _row(kind: compiled.Kind, mp: Path, parse) -> dict:
    """One meta file's row, remembered in-process on its stamp (taken before
    the read) and answered through the persisted layer below that. A file that
    cannot be read raises exactly as the live read does, remembering nothing."""
    def compute() -> tuple[dict, tuple | None]:
        observed = statcache.stamp(mp)
        value = persist.derive(kind, [compiled.Input("meta", mp)], {},
                               lambda files: parse(ledger_texts.decode(files["meta"] or b"")),
                               lambda: parse(mp.read_text(encoding="utf-8")))
        return value, (observed,) if observed is not None else None
    return dict(statcache.memo_stamped((kind.name, os.fspath(mp)), compute,
                                       pool=_OVERVIEW_POOL, max_entries=POOL_ENTRIES))


def world_row(mp: Path) -> dict:
    """`world.md`'s card row (`kinds.world_row`), `name` None when absent."""
    return _row(compiled.OVERVIEW_WORLD_ROW, mp, kinds.world_row)


def campaign_row(mp: Path) -> dict:
    """`campaign.md`'s card row (`kinds.campaign_row`), `name` None when absent."""
    return _row(compiled.OVERVIEW_CAMPAIGN_ROW, mp, kinds.campaign_row)


def _named(row: dict, dirname: str) -> dict:
    """The row with the directory-name fallback applied, outside the cache --
    so a present-but-empty name stays "" as `meta.get("name", d.name)` kept it."""
    return {**row, "name": dirname if row["name"] is None else row["name"]}


def world_rows() -> list[dict]:
    """`GET /worlds`' rows: the live listing (`worlds.read._listed`), each
    world's card row, its nine LIVE counts (spec 03 section 7: member counts
    are listings, and caching a listing saves nothing) and its live cover
    token, newest first as the shelf reads."""
    out = []
    for d, mp in worlds_read._listed():
        row = _named(world_row(mp), d.name)
        out.append({"id": d.name, "name": row["name"], "created": row["created"],
                    "updated": row["updated"], "genre": row["genre"], "module": row["module"],
                    "counts": {**entities.entity_counts(d), "characters": characters.character_count(d),
                               "pcs": pcs.pc_count(d), "greetings": greetings.greeting_count(d)},
                    "cover": covers.world_cover_version(d.name)})
    return worlds_read._newest_first(out)


def campaign_rows() -> list[dict]:
    """Every campaign's row with its id, in `list_campaigns`' enumeration and
    order (newest `updated` first, directory order under a tie). The store's
    `list_campaigns` is left exactly as it is: it feeds a write (spec 04
    section 3.8)."""
    ensure_home()
    base = campaigns_paths._campaigns_dir()   # paths-ok: the resolver itself
    out: list[dict] = []
    if base.exists():
        for d in sorted(base.iterdir()):
            mp = d / "campaign.md"
            if not d.is_dir() or not mp.exists() or not safe_id(d.name):
                continue
            out.append({"id": d.name, **_named(campaign_row(mp), d.name)})
    out.sort(key=lambda m: m["updated"], reverse=True)
    return out


def campaign_meta(cid: str) -> dict:
    """One campaign's row, named. Raises `CampaignNotFound` as `read_campaign`
    does for an id that names no campaign."""
    if not campaigns_paths.campaign_exists(cid):
        raise campaigns_paths.CampaignNotFound(cid)
    return {"id": cid, **_named(campaign_row(campaigns_paths.campaign_meta_path(cid)), cid)}


def world_name(wid: str) -> str | None:
    """`worlds.read.world_name` off the world's card row: the display name,
    or None for an id that names no world."""
    if not worlds_paths.world_exists(wid):
        return None
    return _named(world_row(worlds_paths.world_meta_path(wid)), wid)["name"]


def _world_module(wid: str) -> str:
    """The world's raw `module` default, or "" when the world cannot be read."""
    try:
        mp = worlds_paths.world_meta_path(wid)
        return world_row(mp)["module"] if mp.exists() else ""
    except (worlds_paths.WorldNotFound, OSError, UnicodeDecodeError):
        return ""


def campaign_cards() -> list[dict]:
    """`GET /campaigns`' cards: each campaign's row, its scene summary, and
    three small live reads (the activity stamp, `best_stamp` over it, the cover
    token). `module` is the chip the card draws (`kinds.card_module`)."""
    out = []
    for c in campaign_rows():
        # One undecodable scene degrades that scene's row, never the shelf.
        scenes = scene_summary(c["id"])
        setting = c.pop("module")
        out.append({**c, "module": kinds.card_module(setting, _world_module(c["world"]) if not setting else ""),
                    "scenes": scenes["count"],
                    "cover": covers.cover_version(c["id"]),
                    "last_scene": scenes["rows"][0]["title"] if scenes["rows"] else "",
                    "absorbed": scenes["absorbed"],
                    # Every scene stamp through `best_stamp`, live: the
                    # summary keeps the clock-free half, and the future check
                    # reads the clock.
                    "activity": campaigns_read.best_stamp(
                        c["updated"], campaigns_read.read_activity(c["id"]), *scenes["stamps"])})
    return out
