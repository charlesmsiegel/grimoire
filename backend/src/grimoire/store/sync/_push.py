"""sync: push responsibilities."""

from __future__ import annotations

from pathlib import Path

from .. import atomic, entities, locks, overlay
from ..appearances import paths as appearances_paths
from ..campaigns import lifecycle as campaigns_lifecycle
from ..campaigns import paths as campaigns_paths
from ..campaigns import read as campaigns_read
from ..worlds import paths as worlds_paths
from . import _move_policy, _publication, _read_shapes

# ---- push -----------------------------------------------------------------

def diverged(cid: str) -> list[dict]:
    """Every materialized flat record whose copy no longer matches its base —
    the inverse of `incoming`, and the set `push` can act on.

    Detached refs are excluded: their base is gone and the world record of that
    id is a stranger, so there is nothing they could be pushed *back* into."""
    manifest = campaigns_paths.read_manifest(cid)
    gone = overlay.detached(cid)
    out: list[dict] = []
    for ref in sorted(set(manifest) - gone):
        kind, _, eid = ref.partition("/")
        if kind not in entities.SYNCED_KINDS:
            continue     # actors carry their base elsewhere; see `push`
        text = overlay.record_text(cid, kind, eid)
        if text is None or entities.content_hash(text) == manifest.get(ref):
            continue
        out.append({"ref": {"kind": kind, "id": eid},
                    "name": _read_shapes._blob_name(text, eid)})
    return out


def library_status(cid: str, kind: str, eid: str) -> dict:
    """Where one campaign record stands relative to the library, in the three
    facts an editor needs to decide which action to offer.

    Computed here rather than pieced together client-side out of a world
    listing and a `diverged` sweep: which of promote and push applies is the
    same rule those two functions enforce, and a second copy of it in the
    frontend would drift into offering the button that 409s.
    """
    _move_policy._safe_ref(kind, eid)
    # This is a read, so a campaign whose world is gone is answered rather than
    # refused -- but `world_alive` still gates both actions, because a move into
    # a world that is not there is exactly what `_require_world` refuses. An
    # editor offering a button for that call would be the drift this function
    # exists to prevent.
    wroot = campaigns_read.world_root_of(cid)
    world_alive = worlds_paths.names_its_directory(wroot) and (wroot / "world.md").exists()
    ref = _read_shapes._ref_str(kind, eid)
    actor = kind in appearances_paths.ACTOR_KINDS
    text: str | None = None
    actor_diverged = False
    if actor:
        mine = overlay.actor_snapshot(cid, kind, eid)
        has_own = mine is not None
        in_world = _move_policy._world_holds_actor(wroot, kind, eid)
        # Compared even though actors cannot be pushed. Reporting a materialized
        # and edited actor as `diverged: False` made the panel say "in sync with
        # the library" about a card this campaign had rewritten -- a claim about
        # two records, and a false one (Codex review). Push stays unavailable;
        # what changes is that the editor stops asserting the opposite.
        if has_own and in_world:
            actor_diverged = mine[0] != campaigns_paths.read_manifest(cid).get(ref)
    else:
        _move_policy._flat_kind(kind)
        text = overlay.record_text(cid, kind, eid)
        has_own = text is not None
        in_world = _move_policy._world_holds_flat(wroot, kind, eid)
    detached_here = ref in overlay.detached(cid)
    diverged_here = False
    if text is not None and in_world and not detached_here:
        base = campaigns_paths.read_manifest(cid).get(ref)
        diverged_here = base is None or entities.content_hash(text) != base
    return {
        "in_library": in_world and not detached_here,
        "diverged": (diverged_here or actor_diverged) and not detached_here,
        # Exactly `promote`'s precondition, so the button is never offered for a
        # call that would refuse -- including the detached record whose slug the
        # library has since handed to somebody else.
        "can_promote": has_own and not in_world and world_alive,
        # Actors are deliberately absent from push (#53 option B), and `text` is
        # None for them, so `diverged_here` already says no.
        "can_push": diverged_here and world_alive,
    }


def push(cid: str, kind: str, eid: str, *, force: bool = False) -> None:
    """Save a campaign's override of a library record back into the library.

    Clears the override by construction: the world file becomes the campaign's
    bytes and the base advances to match, so `mine == world == base` holds and
    `incoming` has nothing to say about the ref.

    Refuses when the world has moved since the campaign's base — that is a push
    conflict, the mirror of the pull conflict `incoming` reports — unless the
    caller has seen it and forced past it.
    """
    if kind in appearances_paths.ACTOR_KINDS:
        # A locked actor's base lives in appearances.json and pushing one means
        # minting a NEW world version (#53 option B) rather than overwriting the
        # current one. Saying so beats doing the wrong one of those silently.
        raise _move_policy.NotPushableError(
            f"{kind} cannot be saved back to the library yet — "
            "promote an emergent one, or edit the library copy directly")
    _move_policy._flat_kind(kind)
    _move_policy._safe_ref(kind, eid)
    campaigns_lifecycle.ensure_campaign_slim(cid)
    with locks.campaign_lock(cid):      # see `promote` on why the manifest needs it
        wroot = _move_policy._require_world(campaigns_read.world_root_of(cid))
        _push_locked(cid, kind, eid, wroot, force=force)


def _push_locked(cid: str, kind: str, eid: str, wroot: Path, *, force: bool) -> None:
    ref = _read_shapes._ref_str(kind, eid)
    text = overlay.record_text(cid, kind, eid)
    if text is None:
        raise _move_policy.NotDivergedError(f"{ref} is still inherited from the library — there is nothing to save")
    # The record FILE, not `_world_holds_flat`'s wider "is this id claimed?".
    # Directory occupancy is right for blocking a promote -- a leftover assets
    # directory is content a promotion would adopt -- but for push it would
    # report a record whose world file is gone as a conflict, and the forced
    # retry then recreates it around the dead record's images (Codex review).
    if not _move_policy._world_file(wroot, kind, eid).exists():
        raise _move_policy.NotInLibraryError(f"{ref} is campaign-local; promote it into the library instead")
    # Strict only where membership is published (5.5): there an unreadable
    # ledger is a GroupIdentityConflict before any write. A record that
    # publishes no actor ref keeps #225's ordinary fail-soft read, so a corrupt
    # detached.json does not newly block pushing an unrelated location.
    severed = (overlay.require_identity_ledgers(cid)[0] if _move_policy._publishes_membership(kind, text)
               else overlay.detached(cid))
    if overlay.record_is_marked(cid, kind, eid, severed):
        # Its world original was deleted and something else took the slug. The
        # two records share nothing but an id, and overwriting a stranger is
        # the exact harm detaching exists to prevent (#225).
        raise _move_policy.NotInLibraryError(
            f"{ref} no longer belongs to the library record of that name")
    overlay.require_world_identity_scope(wroot, kind, eid, (cid,))
    _move_policy._require_world_members(cid, wroot, text, kind, eid)
    if kind == "greetings":
        # The same referential check promote runs. A campaign can edit an
        # inherited greeting's cast to name an emergent character, and pushing
        # those bytes publishes a greeting every sibling campaign inherits and
        # none of them can start (Codex review).
        _move_policy._require_world_character(cid, wroot, text, eid)
    base = campaigns_paths.read_manifest(cid).get(ref)
    world_h = entities.entity_hash(wroot, kind, eid)
    mine = entities.content_hash(text)
    if mine == world_h:
        # The two sides already agree, so there is nothing to write and nothing
        # to conflict over -- whatever the base says. Checked BEFORE the
        # conflict tests on purpose: this is exactly the residue push's own
        # write ordering chooses (world written, base not), and answering a
        # retry of it with "changed in the library" would be a conflict message
        # about two identical files, with no way out but a force that overwrites
        # the world with what it already holds.
        if base == mine:
            raise _move_policy.NotDivergedError(f"{ref} already matches the library")
        _publication._put_base(cid, ref, mine)    # clearing the override IS the whole ask
        # Stamped here as well: the interrupted push that left this state failed
        # before its own touch, so without this the world keeps its pre-edit
        # `updated` forever even though the edit landed (Codex review).
        _publication._touch_world(wroot)
        return
    if base is None and not force:
        # A copy with no base: nothing proves the two share an ancestor, so
        # this is a conflict rather than a clean save.
        raise _move_policy.PushConflictError(f"{ref} has no recorded common version with the library")
    if base is not None and world_h != base and not force:
        raise _move_policy.PushConflictError(f"{ref} changed in the library since this campaign copied it")
    # World first, base second. The residue of a crash between them is a
    # conflict whose two sides happen to be identical -- noisy, and rejecting it
    # advances the base and clears it. The other order records a base for
    # content the world does not have, and sync then offers to overwrite this
    # campaign's edit with the library's older text.
    # Re-read immediately before publishing. Two campaigns pushing the same
    # world record hold DIFFERENT campaign locks, so nothing above excludes
    # them from each other and the later write would silently replace a push
    # that had already returned success (Codex review). This narrows that to
    # the gap between the check and the write rather than closing it: closing
    # it needs a world-scoped lock, and this store has none for anything --
    # world create, rename and delete are all unserialized today, so inventing
    # one here would add a second lock domain, and a new ordering pair to
    # deadlock on, for one call site. Named in the design doc as a known gap.
    if not force and entities.entity_hash(wroot, kind, eid) != world_h:
        raise _move_policy.PushConflictError(f"{ref} changed in the library while this save was being made")
    atomic.write_text(_move_policy._world_file(wroot, kind, eid), text)
    _publication._put_base(cid, ref, mine)   # the bytes we wrote, not a re-read of where they went
    _publication._touch_world(wroot)
