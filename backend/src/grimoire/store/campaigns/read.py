"""Campaign meta reads, enumeration and the world reference each one carries."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from pathlib import Path

from .. import atomic
from ..frontmatter import dump_frontmatter, parse_frontmatter
from ..worlds import paths as worlds_paths
from ..paths import any_child_record, ensure_home, now_iso, safe_id
from . import paths

# A campaign may record no world at all, and every world-side read still wants
# a path it can treat as empty. That path has to be one nothing can occupy: any
# sentinel *directory* is one a restored or hand-managed store may already
# contain, and then a world-less campaign inherits whatever is inside it. So
# absence resolves below the campaign's own campaign.md -- a regular file, so
# the filesystem itself guarantees no child of it can ever exist.
_NO_WORLD = "(no world)"


def read_campaign(cid: str) -> dict:
    mp = paths.campaign_meta_path(cid)
    if not mp.exists():
        raise paths.CampaignNotFound(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    return {"meta": {"id": cid, **meta}, "body": body}


def slim_pending(cid: str) -> bool:
    """True while `ensure_campaign_slim` still has this campaign to migrate.

    Writers ask because the manifest means two different things either side of
    that migration, and the difference decides which way an interrupted write
    has to fail (#270). To the overlay layout `sync.md` names the records the
    campaign has materialized, so a ref whose copy is missing is a record that
    was never copied -- harmless, and the next materialization overwrites it.
    To the pending migration the same file is the pre-overlay full copy's
    inventory, where every ref *had* a copy, so a missing one can only be a
    record the user deleted -- and it tombstones it.

    Both readings are correct for their own layout, and neither can tell
    interrupted-write residue from the state it describes. So while this is
    True the ref must never outlive its copy: writers that produce the pair
    drop the ref first here and the copy first everywhere else (#247).

    A campaign with no campaign.md has no migration pending -- there is nothing
    for `ensure_campaign_slim` to migrate, and it raises for that id rather
    than reading anything.
    """
    mp = paths.campaign_meta_path(cid)
    if not mp.exists():
        return False
    meta, _ = parse_frontmatter(mp.read_text(encoding="utf-8"))
    return meta.get("world_copy") != "overlay"


def world_root_of(cid: str) -> Path:
    """The root of the campaign's world, or an unoccupiable path if it has none.

    A stored `world` the guard refuses to resolve — a restored or hand-edited
    campaign can carry one — counts as "no world" rather than raising: a world
    directory that has been deleted already reads as inheriting nothing, and a
    reference that cannot name one is no different. Raises CampaignNotFound
    for a campaign that isn't there. Callers holding a world id they know is
    set should use `worlds_paths.world_root` directly.
    """
    wid = read_campaign(cid)["meta"].get("world", "")
    try:
        return worlds_paths.world_root(wid)
    except worlds_paths.WorldNotFound:
        return paths.campaign_meta_path(cid) / _NO_WORLD


def has_campaigns() -> bool:
    """Whether the store holds at least one campaign, without reading any.

    The cheap counterpart to `list_campaigns()`, for the same reason
    `worlds.read.has_worlds` exists: first-run detection only needs to know
    whether the store is empty.
    """
    ensure_home()
    return any_child_record(paths._campaigns_dir(), "campaign.md")


def _first_paragraph(body: str) -> str:
    """The opening paragraph of a campaign.md body, blank-line delimited.

    Markdown headings and rules are skipped rather than returned: a campaign
    whose body opens with `# Saltmarch` would otherwise blurb its own title.
    """
    for block in body.split("\n\n"):
        text = " ".join(line.strip() for line in block.strip().splitlines() if line.strip())
        if not text or text.startswith("#") or set(text) <= set("-=*_ "):
            continue
        return text
    return ""


def list_campaigns() -> list[dict]:
    ensure_home()
    out: list[dict] = []
    base = paths._campaigns_dir()
    if base.exists():
        for d in sorted(base.iterdir()):
            mp = d / "campaign.md"
            # see worlds.list_worlds: enumeration agrees with the resolvers, so a
            # stray directory can't abort a listing -- or the startup migration
            if not d.is_dir() or not mp.exists() or not safe_id(d.name):
                continue
            meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
            out.append({
                "id": d.name,
                "name": meta.get("name", d.name),
                "world": meta.get("world", ""),
                "created": meta.get("created", ""),
                "updated": meta.get("updated", ""),
                # The pitch the campaign was started from. The list has always
                # parsed the body and thrown it away; the campaigns page shows
                # it as each card's blurb, so a shelf of campaigns reads as a
                # shelf of books rather than a list of slugs. First paragraph
                # only -- this is a card, not the record.
                "blurb": _first_paragraph(body),
            })
    out.sort(key=lambda m: m["updated"], reverse=True)
    return out


def world_refs() -> list[tuple[str, str, str | None]]:
    """(campaign id, campaign name, referenced world id) for *every* campaign
    on disk.

    Deliberately unfiltered, unlike `list_campaigns`. This backs
    `worlds.delete_world`'s in-use check, and a campaign that is unusable as an
    id still pins the world it references: filtering it out of the check is
    what would make that world deletable out from under it (#259 review).
    Enumeration may hide a record from the UI; it must never hide it from a
    referential-integrity check.

    A world id of ``None`` means "this campaign's reference could not be read".
    Undecodable bytes in the *body* must not cost us a reference sitting in
    perfectly good frontmatter, so the read is retried lossily first; only a
    file that cannot be read at all yields ``None``. Callers must treat that as
    "may reference anything" -- skipping it is how "we could not tell" turns
    into "nothing uses this world", which deletes it (#259 review).

    The id comes first because the tolerance is the point: this is the only
    enumeration that survives a campaign nobody can read, so anything that has
    to act on *each* campaign of a world -- `overlay.forget_world_record`, as
    well as the in-use check -- needs to address them from here. The two
    consumers want opposite things from a campaign this cannot read, and both
    have to be able to say so: the in-use check counts it as a user, the sweep
    leaves it alone.
    """
    out: list[tuple[str, str, str | None]] = []
    base = paths._campaigns_dir()
    if not base.exists():
        return out
    for d in sorted(base.iterdir()):
        mp = d / "campaign.md"
        if not d.is_dir() or not mp.exists():
            continue
        try:
            text = mp.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:   # frontmatter survives a bad byte in the body
                text = mp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                out.append((d.name, d.name, None))
                continue
        except OSError:
            out.append((d.name, d.name, None))
            continue
        meta, _ = parse_frontmatter(text)
        out.append((d.name, meta.get("name", d.name), meta.get("world", "")))
    return out


def touch(cid: str) -> None:
    mp = paths.campaign_meta_path(cid)
    if not mp.exists():
        raise paths.CampaignNotFound(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta["updated"] = now_iso()
    atomic.write_text(mp, dump_frontmatter(meta, body))


#: The format now_iso() writes. Parsed rather than pattern-matched: a regex
#: accepts "9999-99-99T99:99:99Z", which is not merely odd but self-sealing --
#: it outranks every real timestamp lexically, and `touch_quietly` then refuses
#: to replace it because each genuine stamp compares older, pinning that
#: campaign to the top of Recent until someone repairs the file by hand.
_STAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _valid_stamp(text: str) -> bool:
    """True only for a stamp `now_iso()` could itself have written.

    Round-tripped, not merely parsed: `strptime` accepts variable-width
    components, so `2026-8-07T01:02:03Z` parses fine -- and a value that parses
    but is not zero-padded is worse here than one that doesn't, because the
    caller compares these *lexically*. `2026-8-07...` sorts above every stamp
    from October onwards, so a hand edit or a sync artifact in that shape pins
    its campaign to the top of Recent for months. Reformatting the parsed value
    and demanding it match is what rejects it; a stamp this file wrote is
    unchanged by the round trip.
    """
    try:
        parsed = datetime.strptime(text, _STAMP_FORMAT)
    except ValueError:
        return False
    if parsed.strftime(_STAMP_FORMAT) != text:
        return False
    return not _implausibly_future(parsed)


#: How far ahead of this machine's clock a stamp may sit and still be believed.
#:
#: There has to be a ceiling, because a canonical, perfectly parseable stamp is
#: the *last* remaining shape of the same self-sealing trap: "9999-12-31T23:59:59Z"
#: survives every check above, outranks every real timestamp lexically, and then
#: `_publish_stamp` declines to replace it because each genuine stamp compares
#: older. A store synced from a device whose clock was wrong, or written while
#: this machine's own clock was ahead and later corrected, gets there without
#: anyone editing a file.
#:
#: A day, not a minute. The stamps in this file come from whichever device wrote
#: them, and a synced library legitimately carries stamps from a phone whose
#: clock is off by a timezone -- refusing those would discard real activity to
#: catch a hypothetical one. A day of slack costs at most one campaign sitting
#: high in Recent for a day, which the next write repairs; the unbounded case
#: never repairs itself at all.
_FUTURE_TOLERANCE = timedelta(days=1)


def _implausibly_future(parsed: datetime) -> bool:
    """Whether `parsed` sits further ahead than any clock skew explains.

    Reads the clock through `now_iso` rather than `datetime.now` so the whole
    module has one source of time -- the same one `_publish_stamp` writes from,
    which is what keeps "accepted" and "publishable" from drifting apart. A
    clock this function cannot parse its own format from is not a reason to
    reject a stored stamp, so that case believes the file.
    """
    try:
        now = datetime.strptime(now_iso(), _STAMP_FORMAT)
    except ValueError:  # pragma: no cover -- now_iso writes _STAMP_FORMAT
        return False
    return parsed > now + _FUTURE_TOLERANCE


def best_stamp(*candidates: str) -> str:
    """The latest candidate this module would believe, or "" if none of them.

    The campaign's activity is a lexical max over stamps from three different
    files, and validating only the one this module writes was arbitrary: a
    scene's `updated` is the same kind of value, read out of the same kind of
    hand-editable, synced file, and folded into the same comparison. A `zzzz`
    or a year-9999 in any one of them outranks every genuine timestamp and then
    blocks its own replacement, pinning the campaign in Recent until that
    particular file is repaired. Every input gets the same bar; a bad one is
    dropped rather than allowed to win.

    Callers pass *every* scene stamp, not the newest -- `list_scenes` sorts by
    the very field that may be bogus, so element zero is only the latest if the
    sort key was trustworthy, which is the thing in question.
    """
    return max((c for c in candidates if _valid_stamp(c)), default="")


def read_activity(cid: str) -> str:
    """The campaign's activity stamp, or "" if there isn't a readable one.

    `Exception`, not `OSError`: a store is plain files the user owns and syncs,
    so this one can come back with non-UTF-8 bytes from a bad restore or a
    half-written sync, and `UnicodeDecodeError` is not an `OSError`. Every
    campaign in `GET /campaigns` goes through here, so letting one damaged
    ranking hint escape would 500 the whole listing -- blanking the campaigns
    page and the sidebar over a file whose entire job is to order five rows.
    Unreadable and absent mean the same thing here: fall back to the stamps in
    campaign.md and the scenes.
    """
    try:
        stamp = paths.campaign_activity_path(cid).read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001 -- see docstring: an ordering hint may not 500 the list
        return ""
    # Shape-checked, not just decodable. The caller folds this into a lexical
    # max against real timestamps, so arbitrary text does not merely rank
    # oddly -- anything sorting above "9" (a stray "zzzz" from a bad sync or a
    # hand edit) outranks every genuine stamp, and keeps outranking them until
    # that campaign is written again. Same treatment as unreadable: absent.
    return stamp if _valid_stamp(stamp) else ""


#: One lock per campaign, serializing the read-compare-write below. Process-local
#: on purpose: this closes the window between two requests in the same backend,
#: which is the reachable one, without putting a cross-process acquire in the
#: path of every campaign write for a hint that orders five rows. Two *processes*
#: can still interleave; that is the residual, and it costs at most a stamp.
#:
#: Per campaign rather than one global lock, because the middleware holds the
#: response's status line until this returns. Two campaigns' stamps are separate
#: files that cannot race each other, so a shared lock would make a slow stamp on
#: a synced or removable store -- an atomic replace plus fsync, plus Windows
#: sharing-violation retries -- delay every *other* campaign's mutation for the
#: length of a write it has nothing to do with.
_STAMP_LOCKS: dict[str, threading.Lock] = {}
#: Guards the dict, not the files: held only long enough to hand out a lock, so
#: it is never held across an fsync. `setdefault` on a plain dict is atomic under
#: the GIL today, but that is an implementation detail to inherit rather than
#: depend on, and this costs a few microseconds per campaign write.
_STAMP_LOCKS_GUARD = threading.Lock()


def _stamp_lock(cid: str) -> threading.Lock:
    """This campaign's stamp lock, created on first use.

    Never evicted. The dict is bounded by the number of campaigns a process has
    written to -- a few dozen entries at most, each a bare mutex -- and evicting
    one would mean deciding it is unheld at a moment when checking that is
    exactly the race being prevented.
    """
    with _STAMP_LOCKS_GUARD:
        return _STAMP_LOCKS.setdefault(cid, threading.Lock())


def touch_quietly(cid: str) -> None:
    """Record that something happened in this campaign, and never fail for it.

    Writes the standalone activity stamp, NOT campaign.md. That distinction is
    load-bearing: this fires from every campaign-scoped write in the app, and
    `touch` republishes the whole meta file from a copy it read a moment
    earlier, so routing these through it would race `rename_campaign` and
    `set_campaign_response` and silently restore the name or response settings
    the user had just changed. locks.py records that hazard for `touch` and
    notes it was only reachable from `appearances`; this would have made it
    reachable from everywhere. See `campaign_activity_path`.

    Never raises. Every caller reaches this *after* the mutation it records has
    committed, so raising would turn work the user already has into a reported
    failure -- a rename that 500s while the file has moved leaves the caller
    holding a sid that now 404s. A lost stamp costs a campaign its place in a
    list until the next write, which is by far the cheaper loss.

    Deliberately broad, because every failure here has that shape: a read-only
    or full disk, a campaign directory that has gone missing. Narrowing it
    would only be choosing which of them is allowed to destroy the caller's
    work.
    """
    try:
        with _stamp_lock(cid):
            _publish_stamp(cid)
    except Exception:  # noqa: BLE001 -- see docstring: nothing here may propagate
        pass


def _publish_stamp(cid: str) -> None:
    """The stamp itself. Split out so the lock above wraps the whole
    read-compare-write rather than only the write."""
    stamp = now_iso()
    # Never publish a stamp older than the one already there. Two writes can
    # overlap -- each reads the clock before its own fsync, so a slower
    # older one can land last and drag the high-water mark backwards,
    # misordering Recent until the next mutation. Skipping instead also
    # drops the write entirely when several mutations share a second, which
    # is the common case.
    #
    # The compare and the write are one critical section (this campaign's
    # `_stamp_lock`, which is all it needs -- the value being compared is this
    # campaign's file and no other writer touches it), so two
    # requests in this process cannot both read the older value and both
    # publish. Two *processes* still can -- that residual is deliberate, and
    # the alternative is a cross-process acquire on every campaign write.
    if stamp <= read_activity(cid):
        return
    atomic.write_text(paths.campaign_activity_path(cid), stamp + "\n")
