"""Scene creation, renaming, deletion, and the id re-pad that follows a
campaign outgrowing its number width.

The only file in the package that reaches `audit.capture_baseline` and
`scene_refs.repoint` for anything but a datetime stamp. `repad` lives here
rather than beside the other id helpers for exactly that reason: it calls
`scene_refs.repoint`, and `scene_refs` imports `chronicle`, which reads
`serialize.TRANSITION_SPEAKER` — keeping `repad` in `serialize.py` would close
that loop.
"""

from __future__ import annotations

import errno

from .. import (
    alternates,
    atomic,
    calendars,
    commits,
    pending_reviews,
    pins,
    prompt_log,
    scene_ids,
    scene_refs,
    turnstate,
)
from ..audit import baselines
from ..campaigns import paths as campaigns_paths
from ..frontmatter import dump_frontmatter, parse_frontmatter
from ..llm_connections import get_active as _get_active_connection
from ..paths import now_iso, safe_id, slugify, uniquify
from . import identity, locking, paths, serialize


@locking._serialized
def repad(cid: str, width: int) -> None:
    """Re-pad every scene number to `width` digits (renames files, repoints all
    referencing stores). Keeps widths uniform so lexicographic order stays exact."""
    mapping, claimed = {}, set()
    for p in sorted(paths._scenes_dir(cid).glob("*.md")):
        parsed = scene_ids.parse_sid(p.stem)
        if parsed and parsed["width"] != width:
            base = scene_ids.format_sid(
                parsed["number"], width, parsed["date_slug"], parsed["title_slug"])
            # Two targets can be the same id, and the rename below would then
            # overwrite one transcript with the other rather than fail. Numbering
            # keeps app-created scenes apart — distinct numbers, distinct
            # prefixes, whatever the titles truncate to — but a store is plain
            # files the user owns, and two hand-placed transcripts carrying the
            # same number at different widths land on one id here. Take the next
            # free id instead: a suffixed id is a small surprise, a lost scene is
            # not.
            # Transcripts only, deliberately NOT `_sid_taken`: an orphaned
            # sidecar on the width-normalised id is repad's to clear (it is the
            # one path that cannot skip a taken id), and skipping it here would
            # strand the scene at a suffixed id forever. A target always differs
            # in width from every source, so it can only collide with a scene
            # that is not moving.
            # Case-folded, because a planned target is compared against other
            # *planned* targets — nothing is on disk yet for the filesystem to
            # answer for. On a case-insensitive volume `0001--Saltmarch` and
            # `0001--saltmarch` are one file, so comparing exactly would let
            # both through and the second rename would land on the first.
            new_sid = uniquify(base, lambda c: c.casefold() in claimed
                               or paths._scene_path(cid, c).exists())
            claimed.add(new_sid.casefold())
            mapping[p.stem] = new_sid
    # Before a single transcript moves. The destinations are orphaned sidecars
    # on ids about to change hands, and clearing one can fail — a read-only
    # file, a sharing violation. Left to `scene_refs.repoint` at the end, that
    # failure lands with every scene already renamed and the other stores still
    # pointing at the old ids; here it costs nothing but the request.
    alternates.clear_destinations(cid, set(mapping.values()))
    # ...and the review sidecar, for the same reason and a sharper one: an
    # inherited review carries a commit token, so saving it would write a dead
    # scene's summary into the chronicle under this scene's id.
    pending_reviews.clear_destinations(cid, set(mapping.values()))
    for old, new in mapping.items():
        paths._scene_path(cid, old).rename(paths._scene_path(cid, new))
    scene_refs.repoint(cid, mapping)


def create_scene(cid: str, title: str, suggested_date: str | None = None,
                 pcless: bool = False) -> str:
    paths._require_campaign(cid)   # before _date_hint: no calendar plugin runs for a
                                   # campaign that doesn't exist. Re-checked under the
                                   # lock, which is where it actually has to hold.
    # The date hint is normalized before the lock, not inside it — see _date_hint.
    return _create_scene(cid, title, pcless, _date_hint(cid, suggested_date))


def _date_hint(cid: str, suggested_date: str | None) -> str:
    """The creation-time date hint in canonical form, resolved OUTSIDE the
    campaign lock (see `_serialized`).

    `get_provider` imports every user-authored provider in
    `<home>/calendars/` and `normalize` then runs that provider's own code.
    None of it touches the scene file, and nothing bounds how long a
    hand-written plugin takes — running it under a campaign-wide lock would
    let one bad calendar stall every writer in the campaign.

    Only a hint: a bad one is dropped, never an error.
    """
    if not suggested_date:
        return ""
    try:
        provider = calendars.get_provider(
            calendars.read_calendar(campaigns_paths.campaign_root(cid))["primary"])
        return calendars.normalize(provider, suggested_date)
    except (calendars.CalendarError, KeyError):
        return ""


def create_would_repad(cid: str) -> bool:
    """Whether the next scene created here would widen the whole campaign.

    Asked by the route inside the campaign-lock hold that also covers the
    create, because a repad renames every scene in the campaign and a live turn
    holds the path it captured.

    **An unreadable directory RAISES rather than answering ``False``.** They are
    not the same answer and review caught the difference: a transient `OSError`
    here reported "no repad needed", the guard was skipped, and `_create_scene`
    then succeeded on its own read and repadded every scene anyway -- so a live
    turn kept its old path and discarded its finished reply at the identity
    fence. Failing the create is the recoverable direction; the caller can try
    again, and nothing has moved.

    A missing campaign still answers ``False``: there is nothing to widen, and
    `_create_scene` raises its own `CampaignNotFound` a moment later, which is
    the 404 the route already knows how to give.
    """
    try:
        number, width = serialize._numbering(cid)
    except campaigns_paths.CampaignNotFound:
        return False        # nothing to widen; the create reports it properly
    return len(str(number)) > width


@locking._serialized
def _create_scene(cid: str, title: str, pcless: bool, date_hint: str) -> str:
    paths._require_campaign(cid)
    d = paths._scenes_dir(cid)
    d.mkdir(parents=True, exist_ok=True)
    number, width = serialize._numbering(cid)
    if len(str(number)) > width:  # 999 -> 1000: widen the whole campaign first
        width = len(str(number))
        repad(cid, width)
    now = now_iso()
    base = scene_ids.format_sid(number, width, None, slugify(title))
    sid = uniquify(base, lambda c: paths._sid_taken(cid, c))
    active = _get_active_connection()
    meta = {"title": title, "model": active["model"] if active else "",
             "created": now, "updated": now,
             # Minted here and never reused. A `sid` is neither stable (a rename
             # moves it) nor unique over time (`_numbering` recycles a deleted
             # scene's number), so this is the only thing a long-running turn can
             # compare to know it is still publishing onto the scene it started
             # on. See `identity.py`.
             "identity": identity.mint()}
    if pcless:
        meta["pcless"] = "true"
    if date_hint:
        meta["suggested_date"] = date_hint
    atomic.write_text(paths._scene_path(cid, sid), dump_frontmatter(meta, ""))
    baselines.capture_baseline(cid, sid)
    return sid


@locking._serialized
def rename_scene(cid: str, sid: str, title: str) -> str:
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["title"] = title
    parsed = scene_ids.parse_sid(sid)
    if parsed:  # keep number and date sections verbatim; only the title re-slugs
        base = scene_ids.format_sid(
            parsed["number"], parsed["width"], parsed["date_slug"], slugify(title))
    else:  # legacy (pre-migration) id: keep the old created-date prefix scheme
        base = scene_ids.fit_sid(f"{meta.get('created', now_iso())[:10]}-", slugify(title))
    new_sid = uniquify(base, lambda c: c != sid and paths._sid_taken(cid, c))
    atomic.write_text(p, dump_frontmatter(meta, body))
    if new_sid != sid:
        p.rename(paths._scene_path(cid, new_sid))
        # a scene's id is its filename: carry every store's references across
        scene_refs.repoint(cid, {sid: new_sid})
    # A rename deliberately leaves the scene's own `updated` alone: the
    # transcript did not change. Campaign activity is recorded at the request
    # boundary (main._CampaignActivityStamp), not here.
    return new_sid


@locking._serialized
def delete_scene(cid: str, sid: str) -> None:
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    # FIRST, ahead of every destructive step, because it is the one here that can
    # refuse. Ids are recycled, so prompt snapshots left behind are adopted by the
    # next scene to take this id and listed as its own -- and unlike the sidecars
    # below, this is rows inside a shared index, so "the delete half-worked" is not
    # a state it can be left in. It therefore raises rather than swallowing, which
    # is only safe from here: run after `retire_scene` or the sidecar unlink, a
    # refusal would report a FAILED delete while the surviving scene had already
    # lost its commit-ledger state and its parked alternates for good. Failing at
    # the top costs nothing but the request.
    prompt_log.forget_scene(cid, sid)
    # A scene id is recycled -- the numbering reuses the highest deleted number,
    # so remaking a scene under the same title can hand it this very id (see
    # _already_absorbed). Retire the commit ledger's state for it, or a review
    # or unfinished reservation left over from this scene matches the
    # replacement and writes the old summary, timeline and edits into it.
    #
    # BEFORE the unlink, which is the irreversible half: a ledger write that
    # fails afterwards would leave the scene gone and its id un-retired, which is
    # exactly the state this prevents. Failing first means the delete raises with
    # the scene still there. The opposite order of failure -- retired, then the
    # unlink fails -- costs an open review of a surviving scene a 409 it clears
    # by re-absorbing.
    commits.retire_scene(cid, sid)
    # The per-turn state ledger goes for the same reason and in the same place:
    # it is keyed by scene id, so a recycled id would hand the replacement scene
    # a dead one's moods -- and at the low post indices a young scene's decay
    # window covers, which is the worst case rather than a harmless one. Before
    # the unlink, so a failure here leaves the scene intact rather than deleted
    # with its ledger still claiming it.
    turnstate.drop_scene(cid, sid)
    # The reader's pins and excludes for this scene (#129), for exactly the
    # recycled-id reason above: a rule left behind would be adopted by the next
    # scene to take this number and force one scene's lore -- or silence -- into
    # another's prompt. Campaign-scoped rules stay; they were never about this
    # scene. Before the unlink, same as the two above.
    pins.drop_scene(cid, sid)
    # The two per-scene sidecars go FIRST. Deleting the transcript is what frees
    # the id for reuse, so a crash between the unlinks must not be able to leave
    # a sidecar without one: that orphan would be adopted by the next scene to
    # take this id -- handing it someone else's parked transcripts, or someone
    # else's end-of-scene review, complete with a commit token that would save
    # the dead scene's summary onto the new one. The other order is recoverable
    # in the harmless direction: a scene that still exists merely loses them.
    _unlink_sidecar(paths._review_path(cid, sid))
    _unlink_sidecar(paths._alts_path(cid, sid))
    p.unlink()
    # AFTER the unlink, so a delete that raised records nothing. Deleting the
    # newest scene would otherwise drag the campaign's derived activity
    # *backwards* onto an older survivor -- a campaign you just edited sinking
    # down the recents list, or off it. Deleting is working on it too. Non-fatal
    # for the same reason as the rename: the scene is already gone.


def _unlink_sidecar(path) -> None:
    """Remove one of a scene's sidecars, tolerating only the name it cannot have.

    `missing_ok` swallows only FileNotFoundError. A store written before ids
    were capped can hold a sid whose `.md` fits its directory entry and whose
    sidecar name does not, and the OS reports that as ENAMETOOLONG on the unlink
    itself -- refusing to delete a scene over a sidecar that cannot exist would
    be the worst reading of it.

    ONLY that one. Every other OSError is a sidecar that does exist and would
    not go: a read-only attribute, a sharing violation, a failing disk.
    Swallowing those reports the delete as done while the scene's parked replies
    -- or its pending review -- stay on disk forever; `_sid_taken` stops the
    orphan being adopted, but nothing ever completes what the caller asked for.
    Windows reports the overlong name as ERROR_FILENAME_EXCED_RANGE, which does
    not always reach errno.
    """
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        if exc.errno != errno.ENAMETOOLONG and getattr(exc, "winerror", None) != 206:
            raise
