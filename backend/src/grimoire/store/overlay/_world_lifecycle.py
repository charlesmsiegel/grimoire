"""Complete campaign-source barriers and cleanup before world identity can be reused."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel

from .. import (
    commits,
    entities,
    greetings,
    group_proposals,
    group_refs,
    journal,
    knowledge,
    locks,
    revision,
)
from .. import paths as library_paths
from ..appearances import paths as appearances_paths
from ..campaigns import paths as campaigns_paths
from ..campaigns import read as campaigns_read
from ..worlds import paths as worlds_paths
from . import _actors, _identity, _ledgers, _record_paths, _transitions


def dematerialize(cid: str, ref: str) -> None:
    """Remove the campaign copy of `ref` so the record reverts to inherited —
    the ref-level inverse of materialization, over all three shapes sync.md
    names. The manifest is the caller's: drop the ref before calling this while
    `campaigns.read.slim_pending` holds, and after it otherwise (#247, #270)."""
    with locks.campaign_lock(cid):
        _ledgers.require_identity_ledgers(cid)
        journal.require_readable(cid)
        kind, _, eid = ref.partition("/")
        with revision.stamp_on_failure(cid):
            if ref == "plotmap":
                (_record_paths.croot_of(cid) / "plotmap.json").unlink(missing_ok=True)
            elif kind in appearances_paths.ACTOR_KINDS:
                _actors.dematerialize_actor(cid, kind, eid)
            else:
                _transitions._retire_owned_reviews(cid, kind, eid)
                journal.retire_membership_targets(cid, kind, eid)
                _record_paths._flat_path(_record_paths.croot_of(cid), kind, eid).unlink(missing_ok=True)


class WorldIdentityScope(BaseModel):
    captured: tuple[str, ...]
    bound: tuple[str, ...]


@contextmanager
def world_identity_scope(wroot: Path) -> Iterator[WorldIdentityScope]:
    """Capture the complete current/historical domain under the source barrier.

    Root came from a host World resolver, never ledger metadata. Registration
    uses this same outer barrier, so a newly created campaign cannot claim the
    old source between discovery and its removal (spec07 section4.5).
    """
    with library_paths.library_guard(), library_paths.pin_library(wroot.parent.parent), locks.world_lifecycle_lock(library_paths.home(), wroot):
        source = str(wroot.resolve())
        try:
            current = set(dependent_campaigns(wroot))
            historical = {cid for cid in campaigns_paths.campaign_ids(strict=True)
                if group_proposals.references_world(_record_paths.croot_of(cid), source) or commits.references_world(cid, source)}
        except OSError as exc:
            raise group_refs.GroupIdentityConflict("cannot enumerate complete membership source claims before changing the World") from exc
        captured = tuple(sorted(current | historical))
        with locks.hold_all(captured):
            bound = tuple(bound_dependents(wroot, captured))
            for cid in captured:
                # Strict account reads also reach historical claims whose
                # campaign now binds elsewhere. Only bound records are moved.
                group_proposals.require_readable(_record_paths.croot_of(cid))
                commits.require_readable(cid)
                _ledgers.require_identity_ledgers(cid)
                journal.require_readable(cid)
            yield WorldIdentityScope(captured=captured, bound=bound)


def retire_world_scope_reviews(wroot: Path, captured: Iterable[str]) -> None:
    source = str(wroot.resolve())
    for cid in captured:
        group_proposals.retire_world(cid, _record_paths.croot_of(cid), source)
        commits.retire_world_claims(cid, source)


def retire_world_reviews(wroot: Path, kind: str, rid: str, captured: Iterable[str]) -> None:
    """Fence inherited work before removal; independent owned copies survive."""
    source = str(wroot.resolve())
    for cid in captured:
        if kind == "groups":
            refs = group_proposals.matching_refs(_record_paths.croot_of(cid), wroot, rid, world=source)
            group_proposals.retire(cid, _record_paths.croot_of(cid), refs)
        commits.retire_membership_targets(cid, kind, rid, root=wroot, world=source)


@contextmanager
def world_identity_change(wroot: Path, *, record: tuple[str, str] | None = None
                          ) -> Iterator[tuple[str, ...]]:
    """Preflight every dependent before a World operation's first effect.

    The outer barrier closes registration/discovery races; the sorted campaign
    domain covers account fencing, removal and cleanup. External editors are
    outside the cooperative process-lock promise (spec07).
    """
    with world_identity_scope(wroot) as scope:
        if record is not None:
            _identity.require_world_identity_scope(wroot, *record, scope.bound)
            kind, rid = record
            if kind in entities.ENTITY_KINDS:
                rid = entities.require_entity(wroot, kind, rid).resolve().stem
                retire_world_reviews(wroot, kind, rid, scope.captured)
            if kind in ("characters", "groups"):
                # Only an actor or a group can be a knowledge audience, and
                # `_forget_in_campaign` prunes only a bound campaign that
                # inherits the record (one holding its own copy keeps its
                # statements): preflight exactly those, before anything is
                # removed (spec 11 §7.5).
                for cid in scope.bound:
                    if not _record_paths._record_present(_record_paths.croot_of(cid), kind, rid):
                        knowledge.require_readable(cid)
        yield scope.bound



def bound_dependents(wroot: Path, captured: Iterable[str]) -> list[str]:
    """Current members of an already held dependent domain, before any effects.

    Campaign ids can be reused while the sorted hold is waiting. Even strict
    preflight of a replacement bound elsewhere would let that stranger block
    this World's operation; cleanup or suspension would mutate it (spec 07).
    Callers hold the captured domain and never rediscover or acquire outsiders.
    """
    bound = []
    for cid in captured:
        try:
            world = campaigns_read.read_campaign(cid)["meta"].get("world", "")
        except campaigns_paths.CampaignNotFound:
            continue
        if worlds_paths.references_world(world, wroot):
            bound.append(cid)
    return bound


def dependent_campaigns(wroot: Path) -> list[str]:
    """Ids of the campaigns that inherit from `wroot`.

    `world_refs`, not `list_campaigns`, because it reads each campaign.md
    independently: `list_campaigns` raises out of its own loop on the first
    undecodable one, and a single corrupt campaign — of *any* world — must not
    cost every healthy dependent its sweep (Codex review).

    That tolerance points the other way from `worlds.delete_world`'s in-use
    check, which shares this enumeration. There, a campaign whose reference
    could not be read counts as a user, because "we could not tell" must not
    become "nothing uses this world". Here it is skipped for the same reason
    read the other way: we would be deleting that campaign's state on a guess
    that it depends on this world at all, and a sweep that does not happen only
    leaves the pre-#225 behaviour.
    """
    out = []
    for cid, _name, w in campaigns_read.world_refs():
        if w is None:
            _record_paths.log.warning("cannot read which world campaign %s belongs to -- leaving its "
                        "state alone; a record recreated in %s may inherit it", cid, wroot)
            continue
        if worlds_paths.references_world(w, wroot) and campaigns_paths.campaign_exists(cid):
            out.append(cid)
    return out


def forget_world_record(wroot: Path, kind: str, rid: str, *,
                        cids: Iterable[str] | None = None) -> None:
    """Sweep what a world-side delete of `<kind>/<rid>` leaves reachable only
    by its id. Call it *after* the delete, from every world route that removes
    an inheritable record.

    Two places keep such leftovers:

    - the world's own `_record_dir` — `entities.delete_entity` and
      `greetings.delete_greeting` unlink the `.md` and nothing else, so the
      record's images survive it — the actor deletes already `rmtree` the
      directory, because for an actor the directory *is* the record;
    - each dependent campaign's `_record_dir`, holding state the campaign filed
      against a record it only ever *inherited*. That state is campaign-local by
      definition, so no sync ever removes it, and a world-side delete leaves it
      unreachable but intact — until the next same-name create hands the id
      back and it re-attaches to a record it knows nothing about (#225).

    A campaign that materialized its own copy keeps it: its record did not go
    anywhere, so its state is still the state of something it has. Deleting a
    world record has never removed a campaign's copy of it (`sync.incoming`
    skips world-side deletions), and this is not the change that starts.

    But that copy is no longer a copy *of* anything, and its `sync.md` base
    still says otherwise — a base is the claim "this world record and mine share
    an ancestor". Left standing, it is the same bug through a different door: a
    recreated slug arrives as an `update`, and accepting it overwrites the
    campaign's record with an unrelated one while its state.md stays put (Codex
    review). So the base goes, which is exactly what makes a record
    campaign-local — the state a campaign-side create leaves it in.

    Two limits, both deliberate. What this does NOT reach is campaign-local
    state keyed by the record id from *outside* the record's directory:
    `sheets/`, `relationships.json`, `appearances.json`, and the `deleted.json`
    tombstone that now hides a recreated record rather than adopting it. Each is
    a separate store with its own semantics, and sweeping them is the dependents
    design #52 carries.

    And being driven by the delete, this only sees campaigns that exist when the
    delete happens: state restored from a backup taken before it, or carried in
    from a store that never saw it, re-attaches exactly as it used to. Closing
    that needs identity rather than an event -- #225's other suggestion, a
    creation nonce stamped into the record and its state -- which is a store
    format change, and a much larger one than the bug in front of us.

    World operation owners pass the campaign set captured by their sorted hold.
    Re-enumerating under that hold could acquire a newly created campaign in
    reverse lock order (spec 07 native review). Standalone cleanup retains its
    historical best-effort discovery; it holds no surrounding campaign set.

    The enumeration is best-effort for the same reason the removal is: a store
    holding one campaign nobody can read must not make a world record
    undeletable, and the delete has already happened by the time we get here.
    """
    if _record_paths._record_present(wroot, kind, rid):
        # Someone took the slug back between the delete and here. Everything
        # below is aimed at a record that exists again, and cannot tell its
        # state from the dead one's: sweeping would delete state written for
        # the NEW record, and detach a campaign that just materialized it.
        # Standing down leaves the pre-#225 behaviour, which is the harmless
        # side of a race this module cannot serialize (Codex review).
        _record_paths.log.warning("%s/%s was recreated before its delete could be swept -- "
                    "dependent campaigns keep what they filed against the old "
                    "record, and it may re-attach to the new one", kind, rid)
        return
    _record_paths._drop_record_dir(wroot, kind, rid)
    if cids is None:
        try:
            cids = dependent_campaigns(wroot)
        except (OSError, UnicodeDecodeError) as exc:
            _record_paths.log.warning("could not enumerate the campaigns of %s (%s) -- state they "
                        "filed against %s/%s stays, and a record recreated under that "
                        "id will inherit it", wroot, exc, kind, rid)
            return
    for cid in cids:
        try:
            _forget_in_campaign(cid, kind, rid, wroot)
        except (OSError, ValueError, locks.StoreBusy) as exc:
            # Per campaign, not per sweep: an unreadable sync.md used to raise
            # through the loop, so one damaged campaign cost every LATER
            # dependent its cleanup -- and the route 500'd on a delete that had
            # already happened, whose retry 404s without sweeping (Codex review).
            # ValueError, not UnicodeDecodeError: a malformed plotmap.json
            # reaches `json.loads` here and raises JSONDecodeError, which is a
            # ValueError and not a decode error (Codex review). The wider catch
            # covers both, since both mean "this campaign's file is garbage".
            #
            # StoreBusy joins them now that `add_detached`/`_undetach` take the
            # campaign lock: a campaign busy for LOCK_TIMEOUT would otherwise
            # abort the sweep for every campaign after it and 500 a delete that
            # has already happened -- reintroducing, by a new route, exactly the
            # failure the paragraph above records fixing. Skipping one campaign
            # leaves it at the pre-#225 behaviour, which `dependent_campaigns`
            # already names as the acceptable direction for a sweep that cannot
            # run.
            _record_paths.log.warning("could not finish sweeping campaign %s after %s/%s was deleted "
                        "(%s) -- a record recreated under that id may inherit its state",
                        cid, kind, rid, exc)


def _forget_in_campaign(cid: str, kind: str, rid: str, wroot: Path) -> None:
    with locks.campaign_lock(cid):
        # Campaign ids are reusable, and the sweep mutates one campaign at a time:
        # between the enumeration and this call the campaign could have been deleted
        # and its slug taken by a new one on a different world, which never depended
        # on `wroot` at all (Codex review). Re-asking is cheap; being wrong deletes
        # a stranger's state.
        try:
            world = campaigns_read.read_campaign(cid)["meta"].get("world", "")
        except campaigns_paths.CampaignNotFound:
            return   # deleted between the enumeration and its turn: nothing to sweep
        if not worlds_paths.references_world(world, wroot):
            return
        _, gone = _ledgers.require_identity_ledgers(cid)
        croot, ref = _record_paths.croot_of(cid), _record_paths.flat_ref(kind, rid)
        if _record_paths._record_present(croot, kind, rid):
            # The campaign owns a copy. It keeps it -- and stops sharing an identity
            # with whatever claims the slug next, in BOTH directions the id reaches:
            # the per-file overlays `detached` governs, and the sync base.
            #
            # Marker first, base second, the same ordering argument `_recorded_base`
            # makes for #247: the two writes cannot be made one, so a crash between
            # them has to land on the harmless side. *Marker, no base drop* -- sync
            # already ignores a detached ref, so the stale base is inert. *Base
            # dropped, no marker* -- the record looks campaign-local to sync while
            # every per-file resolver still reads the world, so the next slug owner
            # supplies its images, tagline and voice anchor with nothing to notice
            # (Codex review).
            _ledgers.add_detached(cid, ref)
            _transitions._drop_manifest_ref(cid, ref)
            return
        _record_paths._drop_record_dir(croot, kind, rid)
        # Statements about an inherited actor or group go with it, as its
        # campaign-local state does. A campaign that kept its own copy returned
        # above and keeps them, as it keeps the record. A world route stamps no
        # campaign, and this wrote one.
        if kind in ("characters", "groups") and knowledge.forget_audience(cid, f"{kind}:{rid}"):
            revision.bump(cid)
        # Per-asset tombstones outlive the record they hid, and they hide by slot:
        # `assets/characters/mara/default/avatar` would blank the NEXT Mara's avatar
        # in this campaign, for a deletion aimed at a record that is gone (Codex
        # review). The whole-record tombstone keeps its meaning and stays.
        _ledgers._drop_deleted(cid, {r for r in gone if r.startswith(f"assets/{kind}/{rid}/")})
        if kind == "greetings":
            _drop_plotmap_edges(cid, croot, rid)


def _drop_plotmap_edges(cid: str, croot: Path, gid: str) -> None:
    """Unwire a deleted greeting from a campaign's OWN plot map.

    `greetings.delete_greeting` unwires it from the world's, and a campaign
    without a plotmap.json reads that one — but a campaign that materialized its
    map keeps a private copy `read_plotmap` prefers, and the deleted greeting's
    node and every `leads_to`/`excludes` pointing at it survive there. Recreate
    the slug and those edges are the new greeting's (Codex review).

    Only for a campaign that already has its own map: materializing one here
    would fork a campaign off the world's plot map as a side effect of a delete
    it had nothing to do with.
    """
    if not (croot / "plotmap.json").exists():
        return
    greetings.remove_from_plotmap(croot, gid)
    # A map that only ever matched the world's still matches it after both got
    # the same edit -- but sync.md holds the pre-delete hash, so the campaign
    # would be walked through a "conflict" whose two sides are identical (Codex
    # review). Advance the base to what both now are.
    wroot = _record_paths.wroot_of(cid)
    manifest = campaigns_paths.read_manifest(cid)
    world_h = greetings.plotmap_hash(wroot) if wroot.exists() else None
    if "plotmap" in manifest and world_h is not None and greetings.plotmap_hash(croot) == world_h:
        manifest["plotmap"] = world_h
        campaigns_paths.write_manifest(cid, manifest)
