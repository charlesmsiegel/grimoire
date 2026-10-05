"""Branching a scene into a sibling (play controls III).

`branch_scene` is the one primitive both "Branch from here" and replay-in-a-
branch (#151) build on: it makes a sibling of an unabsorbed scene holding the
transcript through one post, and returns the sibling's id.

A scene is a markdown file plus records keyed three ways -- by file name, by
identity token, and by scene id inside campaign files. The sibling is built by
writing a remapped copy of the transcript, copying each keyed record through a
helper owned by that record's module, then cutting with the ordinary
`scenes.write.delete_from`, so the cut's history rewind and `turn_sizes` clamp
are reused rather than reimplemented. What is copied, and why, is the table in
`docs/superpowers/specs/2026-10-05-play-controls-branching-design.md`.

**The source is never written.** A scene's group is its `branch_group`, else
its own identity, so the first branch needs no key on the scene it was taken
from; `scenes.read.list_scenes` resolves membership both ways. Closedness is
derived there too, from the group's `done` flags.

**A failure leaves no half-built sibling.** Everything after the transcript
lands runs inside one `try`; on any exception the sibling is deleted through
`scenes.lifecycle.delete_scene` (which drops its ledger scope, presence and
tracker records), its audit baseline is dropped, and the campaign's revision is
bumped -- roll entries appended by the last step cannot be taken back out of an
append-only log, and the middleware only stamps a request that succeeded.
"""

from __future__ import annotations

import logging
import uuid

from . import atomic, checks, dice, locks, responses, revision, rolls, scene_ids
from .appearances import paths as appearances_paths
from .audit import baselines
from .frontmatter import dump_frontmatter
from .paths import now_iso, slugify, uniquify
from .scenes import identity as scenes_identity
from .scenes import lifecycle as scenes_lifecycle
from .scenes import paths as scenes_paths
from .scenes import read as scenes_read
from .scenes import serialize as scenes_serialize
from .scenes import write as scenes_write
from .tracker import records as tracker_records
from .tracker import walk as tracker_walk

log = logging.getLogger(__name__)

#: Frontmatter a sibling inherits. Never `done`, `one_line`, `summary`,
#: `greeting`, or the rolling-summary and scene-break keys: those describe a
#: transcript the sibling does not have, and are recomputed.
COPIED_KEYS = ("model", "location_history", "time_history", "suggested_date", "pcless",
               "turn_sizes", "dismissed", *scenes_write.RESPONSE_FIELDS)


class BranchRefused(Exception):  # noqa: N818 - a refusal, named like ResponseConflict
    """The scene cannot be branched. `kind` is the route's 409 kind."""

    def __init__(self, kind: str, detail: str):
        self.kind, self.detail = kind, detail
        super().__init__(detail)


def match_rolls(entries: list[dict], lines: list[str]) -> list[str]:
    """The ids of the roll-log entries that produced `lines`, in line order.

    For each line, the earliest entry not already taken whose
    `dice.format_roll(result, label)` IS the line; failing that, the earliest
    whose label and formatted dice segment are both in it -- a check line
    (`checks.format_check_roll`) drops the `` `notation` → `` head, so it is
    matched that way. Two identical lines (a `1d20` that rolled the same face
    twice) take one entry each, in log order. A line no entry accounts for gets
    none, and an entry whose result cannot be formatted matches nothing.
    """
    taken: set[int] = set()
    out: list[str] = []
    for line in lines:
        free = [i for i in range(len(entries)) if i not in taken]
        found = next((i for i in free if _formatted(entries[i]) == line), None)
        if found is None:
            found = next((i for i in free if _check_match(entries[i], line)), None)
        if found is not None:
            taken.add(found)
            out.append(entries[found]["id"])
    return out


def _formatted(entry: dict) -> str | None:
    try:
        return dice.format_roll(entry["result"], entry.get("label"))
    except Exception:  # noqa: BLE001 - a malformed entry is one that matches nothing
        return None


def _check_match(entry: dict, line: str) -> bool:
    label = entry.get("label")
    if not label or label not in line:
        return False
    try:
        return checks.dice_segment(entry["result"]) in line
    except Exception:  # noqa: BLE001 - as `_formatted`
        return False


def _title(cid: str, source_title: str, title: str) -> str:
    if title.strip():
        return title.strip()
    taken = {r["title"] for r in scenes_read.list_scenes(cid)}
    base = f"{source_title} (branch)"
    candidate, n = base, 1
    while candidate in taken:
        n += 1
        candidate = f"{base} {n}"
    return candidate


def _sid(cid: str, sid: str, meta: dict, title: str) -> str:
    """The source's number at the source id's width and its date slug, so the
    sibling sorts directly after it; uniquified against every id a file or a
    sidecar holds AND every scene the roll log names, so a recycled id never
    inherits a deleted sibling's entries."""
    parsed = scene_ids.parse_sid(sid)
    if parsed:
        base = scene_ids.format_sid(parsed["number"], parsed["width"], parsed["date_slug"],
                                    slugify(title))
    else:   # a legacy id: rename_scene's created-date scheme
        base = scene_ids.fit_sid(f"{str(meta.get('created') or now_iso())[:10]}-", slugify(title))
    logged = {e.get("scene") for e in rolls.read(cid)}
    return uniquify(base, lambda c: scenes_paths._sid_taken(cid, c) or c in logged)


def _snap(messages: list[dict], through: int) -> int:
    """`through` moved forward to the last part of the response it lands in --
    a response's parts are one reply, and a branch that kept half of one would
    hold a reply nobody wrote."""
    rid = messages[through].get("response_id")
    while rid and through + 1 < len(messages) and messages[through + 1].get("response_id") == rid:
        through += 1
    return through


def _locked(kept: list[dict], rid_map: dict[str, str]) -> set[str]:
    """New ids of the responses a kept roll line follows -- the lock a manual
    roll puts on every response before it (`responses.mark_applied`)."""
    last_roll = max((i for i, m in enumerate(kept)
                     if m.get("speaker") == scenes_serialize.ROLL_SPEAKER), default=-1)
    first: dict[str, int] = {}
    for i, m in enumerate(kept):
        if m.get("response_id") in rid_map:
            first.setdefault(m["response_id"], i)
    return {rid_map[old] for old, i in first.items() if i < last_roll}


def branch_scene(cid: str, sid: str, through: int, *, title: str = "") -> str:
    """Make a sibling of `sid` holding `messages[: through + 1]`; return its id.

    `BranchRefused("absorbed_use_fork")` for an absorbed scene (its past lives
    in campaign files, so only a fork of the campaign can hold two),
    `BranchRefused("branch_closed")` for a scene a sibling's absorb closed, and
    `IndexError` for a point outside the transcript. `through` at the last
    post means no cut. The whole build is one campaign-lock hold.
    """
    with locks.campaign_lock(cid):
        scene = scenes_read.read_scene(cid, sid)
        meta, messages = scene["meta"], scene["messages"]
        if str(meta.get("done", "")).lower() == "true":
            raise BranchRefused("absorbed_use_fork",
                                "an absorbed scene branches by forking the campaign")
        closed = scenes_read.closed_by(cid, sid)
        if closed:
            raise BranchRefused("branch_closed",
                                f"a sibling branch of this scene was absorbed ({closed['title']})")
        if not 0 <= through < len(messages):
            raise IndexError(through)
        through = _snap(messages, through)
        # Read, not minted: the source is never written. Only a scene from
        # before identities existed (which the startup backfill normally
        # reaches first) is given one here.
        src_ident = (scenes_identity.scene_identity(cid, sid)
                     or scenes_identity.ensure_identity(cid, sid))
        group = str(meta.get("branch_group") or src_ident)
        new_title = _title(cid, str(meta.get("title", sid)), title)
        new_sid = _sid(cid, sid, meta, new_title)

        kept = messages[: through + 1]
        rid_map: dict[str, str] = {}
        for m in kept:
            if m.get("response_id") and m["response_id"] not in rid_map:
                rid_map[m["response_id"]] = uuid.uuid4().hex
        locked = _locked(kept, rid_map)
        copied = [{**m, "response_id": rid_map[m["response_id"]]}
                  if m.get("response_id") in rid_map else dict(m) for m in messages]
        now = now_iso()
        new_ident = scenes_identity.mint()
        new_meta = {"title": new_title, "created": now, "updated": now, "identity": new_ident,
                    **{k: meta[k] for k in COPIED_KEYS if meta.get(k) not in (None, "")},
                    "branch_group": group, "branch_of": src_ident}
        atomic.write_text(scenes_paths._scene_path(cid, new_sid),
                          dump_frontmatter(new_meta, scenes_serialize._serialize_messages(copied)))
        try:
            # Appearances before the cut: the cut reads the player cast.
            appearances_paths.join_branch(cid, sid, new_sid, through)
            responses.clone_for_branch(cid, sid, new_sid, rid_map, locked)
            baselines.copy_baseline(cid, sid, new_sid)
            tracker_records.clone(cid, src_ident, new_ident, rid_map)
            if through < len(messages) - 1:
                scenes_write.delete_from(cid, new_sid, through + 1)
                tracker_walk.prune(cid, new_sid)
            # step 5: copy the scene's author's note here (spec gate 16)
            # Last: an appended roll entry is the one write the cleanup below
            # cannot take back.
            source_rolls = [e for e in rolls.read(cid) if e.get("scene") == sid]
            lines = [m["content"] for m in kept
                     if m.get("speaker") == scenes_serialize.ROLL_SPEAKER]
            rolls.copy_for_branch(cid, sid, new_sid, match_rolls(source_rolls, lines))
        except BaseException:
            _discard(cid, new_sid)
            raise
        return new_sid


def _discard(cid: str, sid: str) -> None:
    """Remove a half-built sibling. Each step is fail-soft and logged: the
    exception that brought us here is the one the caller needs to see."""
    try:
        scenes_lifecycle.delete_scene(cid, sid)
    except Exception:
        log.warning("branch: could not delete the half-built sibling %s/%s", cid, sid,
                    exc_info=True)
    try:
        baselines.drop_baseline(cid, sid)
    except Exception:
        log.warning("branch: could not drop the baseline of %s/%s", cid, sid, exc_info=True)
    revision.bump(cid)
