"""Whole-actor authority, materialization and campaign-local actor lifecycle."""

from __future__ import annotations

import shutil
from pathlib import Path

from .. import atomic, characters, journal, knowledge, locks, pcs
from ..appearances import paths as appearances_paths
from . import _copying, _identity, _ledgers, _record_paths, _transitions, _views


def actor_root(cid: str, kind: str, aid: str, *, v: _views.View | None = None) -> Path:
    """Root whose <kind>/<aid> dir is authoritative for meta + version files.
    Tombstoned actors resolve to the campaign, where the caller's read raises
    its usual NotFound."""
    v = _views._view(cid, v)
    croot = v.croot
    if (croot / kind / aid / _record_paths._actor_meta(kind)).exists():
        return croot
    if _record_paths.flat_ref(kind, aid) in v.gone:
        return croot
    return v.wroot


def char_root(cid: str, aid: str, *, v: _views.View | None = None) -> Path:
    return actor_root(cid, "characters", aid, v=v)


def pc_root(cid: str, aid: str, *, v: _views.View | None = None) -> Path:
    return actor_root(cid, "pcs", aid, v=v)


def materialize_actor(cid: str, kind: str, aid: str) -> None:
    """Copy meta + every version file (never assets or sidecars) from the world
    and record the whole-actor sync base. No-op when already materialized."""
    croot, wroot = _record_paths.croot_of(cid), _record_paths.wroot_of(cid)
    if (croot / kind / aid / _record_paths._actor_meta(kind)).exists():
        return
    # one read of the world actor: the base and the bytes it covers, so the two
    # cannot disagree even if the world moves mid-copy (Codex review)
    taken = (characters.snapshot if kind == "characters" else pcs.snapshot)(wroot, aid)
    if taken is None or _record_paths.flat_ref(kind, aid) in _ledgers.deleted(cid):
        raise _record_paths._actor_not_found(kind, aid)
    base, files = taken
    meta_name = _record_paths._actor_meta(kind)
    dst = croot / kind / aid
    dst.mkdir(parents=True, exist_ok=True)
    # No meta here means no materialized actor, so any version file in the
    # campaign dir is residue from an interrupted copy. Drop it: overwriting
    # only what the world still has would resurrect a version purged in
    # between, and the base we are about to record excludes it (Codex review).
    ext = "json" if kind == "characters" else "md"
    for p in dst.glob(f"*.{ext}"):
        if p.name != meta_name:
            p.unlink()
    if kind == "pcs":
        # ...and the same for history: a campaign copy's edit trail is residue
        # once the copy is gone, and must not reappear on the fresh one (#67).
        pcs.forget_history(croot, aid)
    with _copying._recorded_base(cid, _record_paths.flat_ref(kind, aid), base, dst / meta_name):
        # the meta is the single commit point, so it goes last -- `snapshot`
        # puts it first, and for a PC it is an *.md sibling of the versions
        for name, text in files:
            if name != meta_name:
                atomic.write_text(dst / name, text)
        atomic.write_text(dst / meta_name, dict(files)[meta_name])


def ensure_actor_writable(cid: str, kind: str, aid: str) -> Path:
    """Materialize an inherited actor and return the campaign root writes target.

    Stamps campaign activity, because this is the chokepoint every
    campaign-scoped actor write already passes through to obtain a writable
    root -- character default-version, version create/update/delete, and the
    PC routes. Putting it here rather than in each of those eight routes is
    deliberate: an enumerated list of mutators is what left `activity` claiming
    more than it delivered three times over, and the same lesson is already
    written down for the campaign lock domain in locks.py.

    The trade is that this runs *before* the caller's write, so an edit that
    then 404s still records activity. That is the acceptable direction: asking
    to make an actor writable is itself campaign work -- on the first call it
    materializes files that were not there -- and an over-eager ordering hint
    costs a row's position in a list, where a missed one loses work the user
    can see they did.
    """
    croot = _record_paths.croot_of(cid)
    if not (croot / kind / aid / _record_paths._actor_meta(kind)).exists():
        materialize_actor(cid, kind, aid)
    return croot


def dematerialize_actor(cid: str, kind: str, aid: str) -> None:
    """Remove meta + version files so the actor reverts to inherited. Sidecars
    (tagline/dossier/state) and assets stay — they overlay per file. PCs carry
    no sidecar .md files, so all *.md go.

    The meta file goes FIRST, and that ordering is the whole safety of this
    function. It is the commit point `actor_root` keys on, so while it is there
    the campaign claims to have materialized this actor. Unlinking the versions
    first and it last meant a kill between the two left a directory that still
    said "materialized" and held no version to read: `read_character` raises,
    while `list_characters` skips version-less dirs so the world's copy shows
    through the overlay and the actor stays in the roster. A character that
    lists but cannot be opened, edited or re-materialized — and nothing repairs
    it, because the migration only revisits refs sync.md still holds and this
    one's ref is already gone (Codex review, #270).

    Meta-first inverts that into a state the store already understands: version
    files with no meta read as inherited, exactly as an interrupted *copy*
    leaves them, and `materialize_actor` purges them on the next copy."""
    d = _record_paths.croot_of(cid) / kind / aid
    if not d.exists():
        return
    meta = _record_paths._actor_meta(kind)
    rest = (list(d.glob("*.json")) if kind == "characters"
            else [p for p in d.glob("*.md") if p.name != meta])
    for p in [d / meta, *rest]:
        if p.exists():
            p.unlink()
    if kind == "pcs":
        # The copy's history goes with the copy (#67). Left behind, it would
        # list against the world text now showing through, and restoring an
        # entry would re-materialize and write the old campaign text straight
        # over the world change this revert just accepted.
        pcs.forget_history(_record_paths.croot_of(cid), aid)
    if not any(d.iterdir()):
        d.rmdir()


def _mark_campaign_owned(cid: str, kind: str, aid: str) -> None:
    """Record a campaign-created actor as the campaign's own outright (#225).

    The `taken` guards below allocate the id against the world *as it stands*,
    so a campaign-created id names no world record at birth -- which is exactly
    what makes a world record claiming that id **later** a stranger. That is
    the same position a spared copy is in once its world original is deleted,
    and `detached` is the word this module already has for it; only the end the
    collision arrives from is different.

    Without this, a campaign's invented Winifred took a later world Winifred's
    avatar, tagline, voice anchor and sync updates by coincidence of slug --
    every item `detached`'s own docstring lists -- and
    `appearances.actor_source` read her as that stranger's record diverged
    rather than as the campaign's own (Codex review on #99).

    A no-op until such a collision happens: with nothing under the id in the
    world there is nothing for the per-file resolvers to inherit and nothing
    for sync to offer. That is what keeps the blast radius to the bug.

    After the create, never before. A marker orphaned by a failed create would
    suppress inheritance for whatever holds that id next -- failure *adding*
    content, the one direction a user cannot spot by looking, which is the
    argument `detached` itself makes. Crashing between the two leaves an
    unmarked campaign actor: what every one of them was until now.

    Callers hold `campaign_lock` across both halves, which is what makes "after
    the create" a window rather than a race. `add_detached` is an unserialized
    read-modify-replace, so two concurrent creates in one campaign would each
    read the same ledger and the later write would drop the earlier marker --
    silently un-fixing one actor (Codex review). The lock also closes the older
    hazard underneath it: `uniquify` allocates against the filesystem, so those
    same two creates could hand out one id twice.

    Which is the boundary: this marks actors created from here on, and there is
    deliberately no startup backfill for the ones already on disk. The rule a
    backfill would need -- "the world has no actor under this id, so detach" --
    cannot tell a campaign-created actor from a store whose `worlds/` has not
    finished syncing, and `store.home()` is pointed at a synced folder on
    purpose (CLAUDE.md). Detaching is one-way, so guessing wrong there would
    permanently cut a campaign off from its own library to pre-empt a
    collision that needs the user to name a world character after one their
    campaign invented. `appearances.actor_source` still reads an unmarked
    campaign actor correctly until such a collision, through the world-side
    existence check it keeps as a fallback for exactly this data.

    One consequence to carry forward: promoting a campaign-made character into
    the library (#60) now has to CLEAR this marker as well as advance the sync
    base. A promoted character does have a world record behind her, and leaving
    her detached would cut her off from the very library she was just added to.
    """
    _ledgers.add_detached(cid, _record_paths.flat_ref(kind, aid))


def create_character(cid: str, name: str, version_name: str = "default",
                     card: dict | None = None) -> tuple[str, str]:
    """Create a character this campaign owns. UNDER `campaign_lock` -- see
    `_mark_campaign_owned`, and `set_description` above for why new code in
    this module takes it rather than riding `locks.UNREVIEWED`."""
    with locks.campaign_lock(cid):
        # Inside the hold, all of it: the id is allocated against what `taken`
        # sees, so reading the world dir and the tombstones outside the lock
        # would be the same check-then-act the lock is here to close.
        _, gone = _ledgers.require_identity_ledgers(cid)
        wroot = _record_paths.wroot_of(cid)

        def taken(aid: str) -> bool:
            return ((wroot / "characters" / aid).is_dir()
                    or _record_paths.flat_ref("characters", aid) in gone)

        made = characters.create_character(_record_paths.croot_of(cid), name, version_name, card, taken=taken)
        _mark_campaign_owned(cid, "characters", made[0])
    return made


def delete_actor(cid: str, kind: str, aid: str) -> None:
    """Delete an actor campaign-side — the actor twin of `delete_entity`.

    The campaign-scoped create routes have existed for PCs for a while and for
    characters since #60, and neither had a delete: `deleteCharacter` targets
    the world, and version-delete refuses the last one, so an NPC invented by
    mistake could not be removed at all (Codex review).

    Same three cases `delete_entity` distinguishes, for the same reasons:

    - **Inherited from the world** — nothing to unlink, so the tombstone IS the
      delete; it keeps the world's actor from showing back through.
    - **A campaign copy of a world actor** — the copy goes and the tombstone
      goes on, so the world's does not resurface.
    - **Campaign-local** (the emergent NPC this exists for) — the directory
      goes and no tombstone is written, because there is nothing to hide.

    The appearance record goes with it either way. It holds a version lock and
    a per-version sync base for an actor that no longer exists here, and
    `_actor_incoming` reads that record in preference to sync.md -- left
    behind, it offers updates for a deleted actor forever.
    """
    with locks.campaign_lock(cid):
        _ledgers.require_identity_ledgers(cid)
        if kind == "characters":
            # Before anything is removed: the prune below must be able to run.
            knowledge.require_readable(cid)
            journal.require_readable(cid)
        aid = _identity._canonical_record_id(cid, kind, aid)
        ref = _record_paths.flat_ref(kind, aid)
        source_refs = _identity.capture_record_refs(cid, ref)
        in_world = _identity._inherits_world(cid, ref) and _record_paths._record_present(_record_paths.wroot_of(cid), kind, aid)
        mine = _record_paths._record_dir(_record_paths.croot_of(cid), kind, aid)
        if not _record_paths._record_present(_record_paths.croot_of(cid), kind, aid) and not in_world:
            raise _record_paths._actor_not_found(kind, aid)
        if mine.is_dir():
            shutil.rmtree(mine, ignore_errors=True)
        _transitions._drop_manifest_ref(cid, ref, source_refs=source_refs)
        appearances_paths.forget(cid, kind, aid)
        if kind == "characters":
            # The same rule for what the user said this actor knows (spec 11
            # §7.5); a PC is never an audience, since nothing writes its words.
            knowledge.forget_audience(cid, f"characters:{aid}")
        if in_world:
            _ledgers.add_deleted(cid, ref)
        _transitions._undetach(cid, ref, source_refs=source_refs)


def create_pc(cid: str, name: str, tags: list[str], version_name: str = "default",
              persona: dict | None = None) -> tuple[str, str]:
    """The PC counterpart of `create_character`, lock and all."""
    with locks.campaign_lock(cid):
        _, gone = _ledgers.require_identity_ledgers(cid)
        wroot = _record_paths.wroot_of(cid)

        def taken(pid: str) -> bool:
            return (wroot / "pcs" / pid).is_dir() or _record_paths.flat_ref("pcs", pid) in gone

        made = pcs.create_pc(_record_paths.croot_of(cid), name, tags, version_name, persona, taken=taken)
        _mark_campaign_owned(cid, "pcs", made[0])
    return made
