"""Fork a campaign — a second copy that plays forward on its own (#72).

Two shapes, one function. **From now**: the campaign directory is duplicated
and given a new id, a new name and a `parent` line naming the campaign it came
from. **From an earlier turn**: the same copy, then every scene after the one
named is removed from the *copy* — through the machinery that already knows how
to take a scene back, not a hand-rolled truncation.

Why this is a copy and nothing cleverer. A campaign directory is the campaign:
`campaign.md`, `sync.md`, `scenes/`, the play state (`chronicle.json`,
`plot.json`, `changes.json`, `relationships.json`,
`relationship_history.json`, `timeline.md`, per-NPC `state.md` and dossiers)
and whatever records have materialized out of the world. Everything it does
*not* hold it inherits from its world through
`store/overlay.py`, keyed by the `world` line in `campaign.md` and the bases in
`sync.md` — both of which the copy carries verbatim, so the fork inherits from
the same world in the same way and nothing has to be re-resolved. The version
locks in `appearances.json` travel for the same reason: a fork that re-derived
them could pick a different character version than the campaign it forked from,
which is the one thing a branch must not do.

Why it lives here and not in `store/campaigns/`. Cutting the copy back to an
earlier turn needs `cascade` and `scenes`, and `scenes` imports back into
`campaigns.paths`; a fork module inside that package would be a leaf reaching
up over its own importers. `cascade.py` sits at this level for the same reason
and composes the same way — this module is its sibling, not its dependent.

**The source is never written to.** Not the meta file, not the manifest, not
`activity.txt`; the copy is read-only on one side by construction. The one
apparent exception is the source's lock, which is held for the length of the
copy so the snapshot is not taken across somebody else's write.

## What "from an earlier turn" actually restores

The honest answer is: exactly what `store/cascade.py` can take back, which is
what carries the scene's id. Rather than reimplement that, `_cut_after` drives
`cascade.delete_from(<fork>, sid, 0)` — the whole transcript — and then
`scenes.delete_scene`, once per scene after the cut, newest first. Newest first
because `undo` is a compare-and-swap against the record's *current* value: two
scenes that touched one record have to come off in the order they went on.

That buys the change journal (`store/journal.py`), which holds the actual prior
value of every write-back an absorb landed and refuses when the record has moved
since — so `state.md`, dossiers and lore edits go back to what they held, and
where they cannot the refusal is reported rather than guessed at. It also buys
the scene-keyed stores: chronicle records, plot and commitment beats, change
rows and provenance citations for those scenes are removed, and `delete_scene`
takes the prompt log, the commit ledger, the turn-state ledger, the reader's
pins and any parked alternates with them.

What it does **not** restore is the same list `cascade` declines, and for the
same reasons — `timeline.md` (append-only, no scene field), `facts.json`
(supersession, not deletion), `rolls.json` (an append-only ledger), the
`appearances` cast lists and their version locks, `audit` baselines,
`scene_ideas`, and any relationship the journal no longer reaches (retention
bounds it). A record a removed scene *created* is reported, not deleted.

So a retrospective fork is an approximation, and the report says so: `records`
counts what was put back, `refused` names what could not be and why, and
`failed` names a step that could not run at all. The UI shows all three rather
than claiming the branch is the past exactly.
"""

from __future__ import annotations

import json
import logging
import shutil

from . import atomic, cascade, locks, revision
from .campaigns import paths as campaigns_paths
from .campaigns import read as campaigns_read
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import ensure_home, now_iso, slugify, uniquify
from .scenes import lifecycle as scenes_lifecycle
from .scenes import paths as scenes_paths
from .scenes import read as scenes_read

log = logging.getLogger(__name__)

#: The two lineage keys this module writes into `campaign.md`, declared one
#: layer down beside the listing that reads them back (`campaigns.read`).
#:
#: `parent` is an id rather than a name, so a rename on either side leaves the
#: lineage intact and readers resolve the name from the listing. A `parent`
#: naming a campaign that is no longer there is not an error — the child simply
#: reads as a root again, which is the only answer a deleted parent leaves.
#:
#: `forked_from_scene` is a LABEL, not a live reference: `store/scene_refs.py`
#: does not fan out to frontmatter, so a later `repad` or title rename in the
#: fork moves the scene and leaves this naming the id it had at the fork.
#: Deliberate, and safe because nothing dereferences it: the shelf reads it as
#: a yes/no ("cut at a scene" vs "forked from now") and never resolves it to a
#: scene. It records what the branch was cut at, which is a fact about the
#: moment of forking rather than a pointer anything has to keep current.
PARENT_KEY = campaigns_read.PARENT_KEY
FORKED_AT_KEY = campaigns_read.FORKED_AT_KEY

#: Where a fork records the idempotency key it was made for, and the report that
#: was returned for it: `<fork>/fork.json`, holding
#: `{"key", "parent", "at", "report"}`.
#:
#: Beside the fork's own metadata rather than in a global sidecar, and that is
#: the whole of the storage design. A shelf-wide `key -> cid` index is a second
#: file to keep in step with a directory that the user can delete, rename or
#: sync out from under it -- and its failure mode is the expensive one: an index
#: naming a fork that is no longer there replays a report for a campaign that
#: does not exist. Here the record cannot outlive the thing it describes,
#: because it is INSIDE it.
#:
#: The cost is that finding one is a scan. Paid only when a key is sent, and
#: only against the campaigns that name this one as `parent` -- `list_campaigns`
#: already reads every campaign's frontmatter for the shelf, so this is one
#: existing read plus a small JSON file per child.
MARKER = "fork.json"

#: The longest key this will record. Refused rather than truncated, deliberately:
#: two keys that differ only past the cap would truncate onto each other and one
#: caller's retry would be answered with the other's fork. Far past a UUID or any
#: composite a caller has reason to build.
KEY_LIMIT = 200


def _nothing_cut() -> dict:
    """The cut half of the report when there was no cut.

    Spelled out rather than left absent, so a client reads the same keys either
    way and cannot mistake "forked from now" for "the report was truncated". A
    function rather than a module constant because the value is handed to a
    caller: a shared literal is one edit away from being a shared mutable.
    """
    return {"removed_scenes": [], "records": 0, "refused": [], "failed": []}


def fork_campaign(cid: str, name: str, from_scene: str | None = None,
                  key: str = "") -> dict:
    """Copy campaign `cid` into a new campaign called `name`, and return a
    report of what that took.

    `from_scene` names a scene of the source to fork *at*: that scene is kept
    whole and every scene after it is taken off the copy. `None` forks from
    where the campaign stands.

    "After" is a lexicographic comparison of scene ids, which is play order
    because ids are number-first and every number is padded to one width
    (`store/scene_ids.py`; `scenes.lifecycle.repad` is what keeps the width
    uniform when a campaign outgrows it). That last clause is the whole of the
    invariant: a hand-placed `999--x` beside a `1000--y` would sort backwards,
    and nothing here would notice. `export.py` orders scenes the same way and
    on the same footing.

    Raises `campaigns.CampaignNotFound` for an unknown source and
    `scenes.SceneNotFound` for a `from_scene` that is not one of its scenes.
    Both are checked before anything is created — and the second of them after a
    `key` has been looked up, so a repeat is answered even when the scene the
    first call was cut at has since been deleted from the source.

    `key` is an optional idempotency key (#409). A repeat with the same key, from
    the same source, is answered with the fork the first call made and its report
    verbatim rather than copying again — because "the write committed and the
    response was lost" and "the write never happened" are the same thing to a
    caller, and a `copytree` is not a retry to guess wrong about. The key wins
    over everything else in the request: a repeat under a different `name` or a
    different `from_scene` still replays the first fork, because a key names an
    *operation* and the second call is the same operation asked again. Keys are
    scoped to the source, so two campaigns cannot collide on one.

    The record lands last, inside the same lock hold as the copy, so anybody who
    can see the fork can see what key made it. What it cannot cover is the gap
    the same way round: a crash between the copy and the record leaves a fork
    with no key, and the retry copies again — which is the pre-#409 behaviour for
    that one case, and visible on the shelf rather than silent, since
    `uniquify(slugify(name))` lands the second copy under its own suffixed id.

    Raises `ValueError` for a key past `KEY_LIMIT`.

    The report is `{"id", "from_scene", "removed_scenes", "records", "refused",
    "failed", "replayed"}`. All but `id` and `replayed` are always present and
    carry nothing at all for a fork from now — see `_nothing_cut`. `replayed`
    says which of the two things happened: a copy, or a key being answered.
    """
    ensure_home()
    if len(key) > KEY_LIMIT:
        raise ValueError(f"an idempotency key may be at most {KEY_LIMIT} characters")
    if not campaigns_paths.campaign_meta_path(cid).exists():
        raise campaigns_paths.CampaignNotFound(cid)
    new_cid = uniquify(slugify(name), lambda c: campaigns_paths.campaign_root(c).exists())
    # Both locks, through the one function allowed to hold two (#267): the
    # source's so the copy is not taken across a scene being written, and the
    # fork's for the same reason `create_campaign` holds one — `campaign.md` is
    # what makes a directory a campaign to `list_campaigns`, so the fork is
    # visible to another process the moment `copytree` lands it, and a
    # retrospective fork is still deleting scenes for a while afterwards.
    # Holding through the cut is what stops anyone seeing the branch with the
    # future still in it. Everything inside is file work this package owns; no
    # calendar provider or other plugin code runs under it.
    with locks.hold_all([cid, new_cid]):
        # The key is looked up INSIDE the hold, and that is what makes it a
        # check rather than a hint: the source's lock is held from here through
        # the copy and the record, so a second call with the same key either
        # finds the first one's record or waits for it and then does. Ahead of
        # the claim below because a replay creates nothing and should leave
        # nothing to clean up.
        if key:
            done = _replay(cid, key)
            if done is not None:
                return {**done, "replayed": True}
        if from_scene is not None:
            # Against the campaign's own enumeration rather than a bare
            # `exists()`: `list_scenes` drops ids the resolvers would refuse, and
            # a cut has to mean a scene the campaign will actually show you.
            #
            # AFTER the replay, which is the ordering a repeat depends on. The
            # scene named here belongs to the SOURCE and the source keeps
            # playing, so a scene a keyed fork was cut at can be deleted from it
            # afterwards -- and validating first would then answer a retry of
            # that fork with a 404 while the fork it made, and its record, are
            # both still sitting there. A key names an operation that already
            # happened; nothing about the request that repeats it needs to be
            # true a second time.
            if from_scene not in {s["id"] for s in scenes_read.list_scenes(cid)}:
                raise scenes_paths.SceneNotFound(from_scene)
        # Claim the id with an empty directory FIRST, and outside the block that
        # cleans up -- this is the one step whose failure must not delete
        # anything. `uniquify` ran before the lock, so the id can have been taken
        # in between; `mkdir` is what loses that race, and it loses it against a
        # directory somebody else's campaign is living in. Letting `copytree`'s
        # own `FileExistsError` be the guard instead would put that campaign
        # inside the cleanup below, which would `rmtree` it. Same claim-then-fill
        # order as `create_campaign`, and for a sharper reason.
        campaigns_paths.campaign_root(new_cid).mkdir(parents=True)
        try:
            _copy(cid, new_cid, name, from_scene)
        except BaseException:
            # Everything past the claim is ours to undo. `copytree` publishes
            # `campaign.md` partway through a copy that can still fail after it
            # -- a full disk, an unreadable file, a symlink with no target --
            # and that file is what makes a directory a campaign to
            # `list_campaigns`. Without this, a failed fork leaves a phantom on
            # the shelf: partial content under the SOURCE's name, with no
            # `parent` to mark it as a copy and nothing to tell the user it is
            # one.
            #
            # Best-effort, and the original error wins. A cleanup that fails
            # leaves exactly the debris there would have been anyway, and
            # replacing a full disk with "could not remove directory" would hide
            # the reason the fork failed.
            _discard(new_cid)
            raise
        report = _cut_after(new_cid, from_scene) if from_scene else _nothing_cut()
        out = {"id": new_cid, "from_scene": from_scene or "", **report}
        if key:
            _record(cid, new_cid, key, out)
        # LAST, after the cut and the record: the fork's own write token (#409).
        # `_copy` removed the one it inherited rather than minting a fresh one
        # here, because from `copytree` onwards this directory is a campaign to
        # every reader -- and none of them takes this lock, since a read route
        # does not. A token minted before the cut would be handed to a reader
        # mid-cut and still be current once the cut had finished, which is a
        # stale reading certified as good. Minted here, everything a reader can
        # see mid-cut reads as `INITIAL` and every expectation built on it is
        # refused.
        revision.bump(new_cid)
    return {**out, "replayed": False}


def _discard(new_cid: str) -> None:
    """Remove a fork that never finished being made. Never raises.

    Only ever called for a directory THIS call created, in the `mkdir` above,
    and published to nobody -- the lock is still held, so no other writer has
    reached it. That ownership is the whole licence for an `rmtree` here, where
    `delete_campaign` needs a canonical-name check before one: this must never
    be reachable for a directory the fork merely found.

    `ignore_errors` so a cleanup failure cannot displace the error that caused
    it -- but the survival is checked and logged rather than passed over in
    silence, because a fork directory left on the shelf is a phantom campaign
    and the only trace of it would otherwise be the user finding it.
    """
    try:
        root = campaigns_paths.campaign_root(new_cid)
        shutil.rmtree(root, ignore_errors=True)
        if root.exists():
            log.warning("fork: the partial copy at %s could not be removed", new_cid)
    except Exception:   # `campaign_root` can still refuse the id
        log.warning("fork: could not discard the partial copy at %s", new_cid, exc_info=True)


def _marker_path(new_cid: str):
    return campaigns_paths.campaign_root(new_cid) / MARKER


def _replay(cid: str, key: str) -> dict | None:
    """The report of an existing fork of `cid` made under `key`, or None.

    Reads the children rather than an index — see `MARKER`. `list_campaigns`
    is the same enumeration the shelf uses, so a directory it would not show is
    not a fork this will replay either; the `parent` filter it applies first is
    what keeps this to the campaigns actually descended from this one.

    Answers None for anything it cannot read cleanly: a truncated or hand-edited
    marker, a key that does not match, a report missing a field or holding the
    wrong type in one. A marker this cannot read means a second copy, which is
    exactly what a caller with no key gets and is recoverable by deleting the
    extra campaign. Believing a damaged one would hand back a report describing a
    fork whose actual contents nobody has checked.

    The shape is checked WHOLE rather than by its `id` alone, and that is not
    belt-and-braces: this value is returned as the route's body, and the client
    reads every field of it -- `forkNotes` walks `refused` and `failed` as
    arrays. A half-written marker carrying nothing but the right id would
    therefore replay as a success and fail in the reader's browser, where the
    documented recoverable path is a second copy on the shelf.
    """
    for row in campaigns_read.list_campaigns():
        if row.get("parent") != cid:
            continue
        try:
            raw = json.loads(_marker_path(row["id"]).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 -- unreadable means "no record at all"
            continue
        if not isinstance(raw, dict) or raw.get("key") != key:
            continue
        report = raw.get("report")
        if isinstance(report, dict) and _whole(report) and report["id"] == row["id"]:
            return report
        # A marker that names a key but cannot answer for it is the one damaged
        # shape worth a line in the log -- an id disagreeing with the directory
        # the marker is IN is what a hand-copied campaign directory looks like,
        # and a missing field is a write that did not finish.
        log.warning("fork: the marker in %s does not describe it", row["id"])
    return None


#: Every field of a fork report, and the type the client reads it as. Kept
#: beside `_whole` rather than derived from `_nothing_cut`, because the two
#: answer different questions: that one builds a report, this one refuses to
#: believe a file claiming to be one.
_REPORT_FIELDS: dict[str, type | tuple[type, ...]] = {
    "id": str, "from_scene": str, "removed_scenes": list,
    "records": int, "refused": list, "failed": list,
}

#: What each list field carries, checked down to its leaves. A container check
#: alone let `{"refused": [null]}` through, and `forkNotes` reads `.label` off
#: every element -- so a marker that got past the shape check still broke in the
#: reader's browser instead of taking the recoverable path (Codex review). Past
#: this there is nothing deeper: every leaf is a string.
_REPORT_ROWS: dict[str, tuple[str, ...]] = {
    "removed_scenes": (), "failed": (), "refused": ("label", "reason"),
}


def _whole(report: dict) -> bool:
    """Whether `report` is a fork report all the way down.

    Every field, its type, and for the three lists the shape of each element --
    because this value is returned as the route's body and the client reads all
    of it. Anything short of that is a file we did not write, and believing one
    hands back a report describing a fork whose contents nobody has checked.

    `bool` is excluded from `records` deliberately: it is an `int` to
    `isinstance` and is not a count, and a marker holding `true` there is a file
    nobody wrote from this code.
    """
    if not all(isinstance(report.get(f), t) for f, t in _REPORT_FIELDS.items()):
        return False
    if isinstance(report["records"], bool):
        return False
    return all(_row(row, keys) for f, keys in _REPORT_ROWS.items() for row in report[f])


def _row(row: object, keys: tuple[str, ...]) -> bool:
    """One element of a report's list: a bare string, or an object with the keys
    the client reads off it -- each of them a string too."""
    if not keys:
        return isinstance(row, str)
    return isinstance(row, dict) and all(isinstance(row.get(k), str) for k in keys)


def _record(cid: str, new_cid: str, key: str, report: dict) -> None:
    """Record which key made this fork, and what was reported for it. Never raises.

    The fork exists by the time this runs, and this module's whole posture is
    that a fork already on disk is not turned into a 500 by a step that comes
    after it — the same call `_cut_after` makes for a scene that will not come
    off. What a lost record costs is one duplicate copy on a retry, which is the
    behaviour every caller had before the key existed.
    """
    try:
        atomic.write_text(_marker_path(new_cid), json.dumps(
            {"key": key, "parent": cid, "at": now_iso(), "report": report},
            indent=2) + "\n")
    except Exception:   # see docstring: the fork is already made
        log.warning("fork %s: could not record the idempotency key", new_cid,
                    exc_info=True)


def _copy(cid: str, new_cid: str, name: str, from_scene: str | None) -> None:
    """Duplicate the campaign directory and re-stamp the copy's `campaign.md`.

    `copytree` rather than a per-file walk on purpose: what a campaign holds
    grows (weather overrides, the commit ledger, the turn-state ledger and the
    scene ledger all arrived after this issue was written), and a fork that
    enumerated the parts would silently stop copying the newest one. The rule
    is "everything, then fix up what is identity" — so a part added tomorrow
    travels without anybody remembering this file exists.

    Identity is three things: the name, the timestamps and the lineage. Three
    files are *removed* rather than rewritten, and they have three different
    reasons.

    `revision.txt` is the write token a caller compares a priced operation
    against (#409), and it is a statement about a write history the copy does
    not have — carried over, the fork would answer "unchanged" to a reading
    taken from its parent. Removed here and minted at the END of the fork
    (`fork_campaign`), never here: `copytree` has already published
    `campaign.md`, so the fork is a campaign to `list_campaigns` and to every
    read route from this line on, while a retrospective fork is still deleting
    scenes for a while afterwards. A token minted now would be handed to a
    reader mid-cut and then still be current when the cut finished.

    `fork.json` is the source's own idempotency record, dropped so a copy of a
    copy does not claim to have been made for a key that named an earlier
    operation on another campaign.

    `activity.txt` is the campaign's "something happened here" high-water mark. Copied, it would rank a fork
    made a second ago by when its parent was last played, which for an old
    campaign is the bottom of Recent. Absent, `read_activity` answers "" and
    `best_stamp` falls back to the `updated` stamped here: now, which is when
    this campaign started existing.

    Everything else in the frontmatter travels as it stands, including a
    half-finished slim migration's `slim_pruning` marker. That is deliberate
    rather than overlooked: the marker means "the decisions are made, only the
    deleting is left", and since the copy is a byte-for-byte duplicate of the
    tree those decisions were made about, it is as true of the fork as of the
    source. `ensure_campaign_slim` then resumes on the fork the way it would
    resume after a crash, which is the one path that does not attribute a
    second time.
    """
    root = campaigns_paths.campaign_root(cid)
    new_root = campaigns_paths.campaign_root(new_cid)
    # A whole-directory publication, so no `atomic` temp-and-replace applies.
    # `dirs_exist_ok` because the caller has already claimed this id with an
    # empty directory — that `mkdir`, not this call, is what fails if the id was
    # taken between `uniquify` and the lock, which is what keeps somebody else's
    # campaign out of the cleanup path (see `fork_campaign`).
    #
    # Symlinks are FOLLOWED (`symlinks` left False), and that is the setting
    # this module's central claim rests on: copied as links, every file in the
    # fork would still be the source's file, and the first write to the branch
    # would land in the campaign it was forked from. A store is plain files the
    # user owns and syncs, so a symlink in there is not hypothetical. Following
    # them costs the bytes and can loop or dangle; both fail loudly and the
    # caller's cleanup removes what was made.
    shutil.copytree(root, new_root, dirs_exist_ok=True)
    mp = campaigns_paths.campaign_meta_path(new_cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    now = now_iso()
    meta["name"] = name
    meta["created"] = now
    meta["updated"] = now
    # Overwrites whatever the source carried: lineage is a link to the campaign
    # forked FROM, not to the root of the tree. Likewise the cut marker, which
    # belongs to one fork and not to its descendants -- a fork of a
    # retrospective fork starts from now again unless it says otherwise.
    meta[PARENT_KEY] = cid
    if from_scene:
        meta[FORKED_AT_KEY] = from_scene
    else:
        meta.pop(FORKED_AT_KEY, None)
    atomic.write_text(mp, dump_frontmatter(meta, body))
    campaigns_paths.campaign_activity_path(new_cid).unlink(missing_ok=True)
    _marker_path(new_cid).unlink(missing_ok=True)
    campaigns_paths.campaign_root(new_cid).joinpath(revision.FILENAME).unlink(missing_ok=True)


def _cut_after(cid: str, from_scene: str) -> dict:
    """Take every scene after `from_scene` off `cid`, newest first.

    Runs on the FORK, always — the caller passes the new id. Nothing here
    checks that, and it is worth saying out loud because the whole safety of
    this module rests on the caller not passing the source.

    Each scene goes in two steps, and both are needed: `cascade.delete_from`
    at index 0 empties the transcript and reverses what the scene wrote, and
    `scenes.delete_scene` then removes the scene itself along with the stores
    keyed by its id.

    **An empty scene is asked about rather than inferred from an exception.**
    `delete_from` refuses an index that removes nothing, so a postless scene
    (an interrupted one, or a created-but-never-played one) raises `IndexError`
    at index 0 — and catching that would have been the short way to write this.
    It is also wrong: `delete_from` runs `read_scene`, `alternates.state` and
    `commits.retire_scene` before it cuts anything, and an `IndexError` out of
    any of those — a malformed transcript, a hand-edited commit ledger — reads
    identically. The scene would then be deleted with nothing reversed and
    reported as cleanly removed, which is the one outcome this must never
    produce silently. So emptiness is a question, and every exception is a
    failure.

    Nothing here raises for a scene that would not come off. `delete_from`'s
    contract is `SceneNotFound` or `IndexError` and nothing else, and both are
    handled; `delete_scene` can still fail on a file the OS will not unlink,
    and the fork is already on disk by then. Naming the scene in `failed` and
    carrying on is the same posture `cascade` takes after its own cut: a branch
    with one stubborn scene left on it is recoverable by hand, a fork that
    500s halfway through is not.

    Every entry in `failed` is scoped to the scene it happened in, `<sid>` for
    the scene itself or `<sid>/<step>` for one of the cleanups inside it —
    `cascade` reports a bare step name because it only ever handles one scene,
    which here would leave a reader unable to tell which of a dozen it meant.
    """
    later = sorted(s["id"] for s in scenes_read.list_scenes(cid) if s["id"] > from_scene)
    removed, records, refused, failed = [], 0, [], []
    for sid in reversed(later):
        try:
            # The read is `cascade.delete_from`'s own first step, repeated here
            # so the empty case can be told apart from a failure (see above).
            # Every non-empty scene therefore parses its transcript twice --
            # accepted, because the alternative is a silent wrong answer and
            # the pass is already doing a journal reversal and five store
            # rewrites per scene.
            report = (cascade.delete_from(cid, sid, 0)
                      if scenes_read.read_scene(cid, sid)["messages"] else {})
        except Exception:       # see docstring: one scene may not sink the fork
            log.warning("fork %s: could not revert scene %s", cid, sid, exc_info=True)
            failed.append(sid)
            continue
        records += report.get("records", 0)
        refused.extend(report.get("refused", []))
        failed.extend(f"{sid}/{step}" for step in report.get("failed", []))
        try:
            scenes_lifecycle.delete_scene(cid, sid)
        except Exception:       # as above
            log.warning("fork %s: could not delete scene %s", cid, sid, exc_info=True)
            failed.append(sid)
            continue
        removed.append(sid)
    removed.reverse()           # report in play order, not the order they went
    return {"removed_scenes": removed, "records": records,
            "refused": refused, "failed": failed}
