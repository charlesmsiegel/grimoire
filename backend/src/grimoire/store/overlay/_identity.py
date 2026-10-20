"""Native identity proofs and distinct owned/world source keys, without publishing effects."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from pydantic import BaseModel

from .. import entities, greetings, group_refs
from ..appearances import paths as appearances_paths
from ..campaigns import paths as campaigns_paths
from ..paths import safe_id
from . import _ledgers, _record_paths


class RecordIdentityRefs(BaseModel):
    """Pre-effect identity keys, separated by the namespace that owns them."""

    owned: frozenset[str]
    world: frozenset[str]


def _inherits_world(cid: str, ref: str) -> bool:
    """Would this campaign read the world's record of that id, if it had none?

    False once the ref is tombstoned, and false once it is detached. The second
    is what keeps a delete honest: a tombstone exists to stop the world's copy
    showing through, and a detached record has no world copy to stop -- whatever
    holds the id now is a stranger. Tombstoning on its way out would hide that
    stranger from the campaign permanently, which deleting an un-detached
    campaign-local record never does (Codex review)."""
    gone, severed = _ledgers.deleted(cid), _ledgers.detached(cid)
    if ref in gone or ref in severed:
        return False
    kind, _, eid = ref.partition("/")
    return (not record_is_marked(cid, kind, eid, gone, roots=(_record_paths.wroot_of(cid), _record_paths.wroot_of(cid)))
            and not record_is_marked(cid, kind, eid, severed))


def _same_actor_slot(root: Path, kind: str, aid: str, other: str) -> bool:
    """Native identity proof, never a guessed case-folded id comparison."""
    try:
        return (root / kind / aid).samefile(root / kind / other)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise group_refs.GroupIdentityConflict(
            "Cannot determine detached actor identity; repair or restore the actor paths.") from exc


def actor_detachments(croot: Path, wroot: Path, actor_refs: Iterable[str],
                      severed: set[str]) -> set[str]:
    """Augment detachments with proven native aliases of the queried raw refs.

    Preserve stored ids and raw field spelling. Windows may resolve MARA to
    mara, but a case-sensitive directory may hold distinct actors: samefile,
    like World reference matching (#259), is the proof. Only directory identity
    is probed, never actor content. The shared group_refs filter stays pure.
    Exact markers keep their meaning even while both actor paths are absent.
    """
    matched = set(severed)
    for actor in actor_refs:
        parsed = group_refs.actor_ref(actor)
        if parsed is None:
            continue
        ref = parsed.replace(":", "/", 1)
        if ref in matched:
            continue
        kind, aid = parsed.split(":", 1)
        other_ids = (r.split("/", 1)[1] for r in severed if r.startswith(f"{kind}/"))
        # An owned source's namespace is authoritative. Another root may alias
        # two files that are distinct here (mixed native policies, spec 07).
        roots = (croot,) if _record_paths._record_present(croot, kind, aid) else (croot, wroot)
        if any(_same_actor_slot(root, kind, aid, other)
               for other in other_ids for root in roots):
            matched.add(ref)
    return matched


def _unresolved_group_slots(croot: Path, wroot: Path, meta: Mapping[str, object],
                            severed: set[str]) -> frozenset[tuple[str, str]]:
    """Foreign actor identities, including every valid dormant Leader token.

    Existence is the ordinary World actor resolver's meta-file boundary, not
    directory occupancy: a stranded image directory is not an actor. This
    one-group check deliberately does not build the later inverse index.
    """
    return frozenset((field, actor) for field, actor in group_refs.group_slots(meta)
        if actor.replace(":", "/", 1) in severed
        or not _record_paths._record_present(wroot, *actor.split(":", 1))
        or not _world_identity_matches_owned(croot, wroot, *actor.split(":", 1)))


def group_unresolved(cid: str, meta: Mapping[str, object]) -> frozenset[tuple[str, str]]:
    """Slots needing explicit review before these World bytes become owned."""
    severed, _ = _ledgers.require_identity_ledgers(cid)
    croot, wroot = _record_paths.croot_of(cid), _record_paths.wroot_of(cid)
    severed = actor_detachments(croot, wroot,
        (actor for _, actor in group_refs.group_slots(meta)), severed)
    return _unresolved_group_slots(croot, wroot, meta, severed)


def _require_group_copy(croot: Path, wroot: Path, source: str, final: str, severed: set[str],
                        reviewed_slots: frozenset[tuple[str, str]]) -> None:
    original_meta, _ = entities.parse_frontmatter(source)
    final_meta, _ = entities.parse_frontmatter(final)
    severed = actor_detachments(croot, wroot,
        (actor for _, actor in group_refs.group_slots(original_meta)), severed)
    # Consent is per retained slot. Removing a token resolves it; adding a new
    # authored token does not review an old slot elsewhere in the same field.
    blocked = (_unresolved_group_slots(croot, wroot, original_meta, severed)
               & group_refs.group_slots(final_meta)) - reviewed_slots
    if blocked:
        raise group_refs.GroupIdentityConflict(
            "Review unresolved Members and Leader references before making a campaign copy.",
            slots=tuple(sorted(blocked)))


def has_own_copy(cid: str, kind: str, eid: str) -> bool:
    """Does this campaign hold its own file for `<kind>/<eid>`, rather than
    reading the world's? The question a world-side sweep has to ask before it
    touches a campaign: a campaign that never materialized the record has
    nothing to move, and materializing one now would fork it off the world for
    a change it agreed with."""
    return kind in entities.ENTITY_KINDS and safe_id(eid) and _record_paths._flat_path(_record_paths.croot_of(cid), kind, eid).exists()


def record_is_marked(cid: str, kind: str, eid: str, marks: set[str], *,
                     roots: tuple[Path, Path] | None = None) -> bool:
    """Match a retained-copy marker by exact ref or proven native identity.

    A submitted uppercase id may reach the same file on Windows. Casefolding
    would also merge distinct files on case-sensitive stores; samefile is the
    narrower proof, shared by World moves and take-world/pin protection. Only
    identity is probed, never actor bodies or a full roster (spec 07).
    A same-read View may supply its captured roots so matching a tombstone
    cannot resolve another World halfway through that read's diagnostics.
    """
    if _record_paths.flat_ref(kind, eid) in marks:
        return True
    if (kind not in _record_paths.INHERITED_KINDS and kind != "plotmap") or not safe_id(eid):
        return False
    roots = (_record_paths.croot_of(cid), _record_paths.wroot_of(cid)) if roots is None else roots
    identity_roots = roots[:1] if _record_paths._record_present(roots[0], kind, eid) else roots
    # A World alias cannot merge distinct owned sources.
    for ref in marks:
        other_kind, _, other_id = ref.partition("/")
        if other_kind != kind or not safe_id(other_id):
            continue
        for root in identity_roots:
            try:
                if _record_paths._identity_path(root, kind, eid).samefile(_record_paths._identity_path(root, kind, other_id)):
                    return True
            except (FileNotFoundError, NotADirectoryError):
                continue
            except OSError as exc:
                raise group_refs.GroupIdentityConflict("cannot verify retained record identity; repair access before changing it") from exc
    return False



def world_identity_matches_owned(cid: str, kind: str, eid: str, wroot: Path) -> bool:
    """Verify a held campaign's source identity against its selected World."""
    return _world_identity_matches_owned(_record_paths.croot_of(cid), wroot, kind, eid)


def _world_identity_matches_owned(croot: Path, wroot: Path, kind: str, eid: str) -> bool:
    """A native alias may not select a different original across namespaces.

    Copies have different inodes, so compare canonical IDs within each root,
    never the copy's inode to its World ancestor. The forward proof protects
    an insensitive campaign over a sensitive World; the reverse proof keeps a
    distinct sensitive campaign actor from collapsing into a World alias.
    No owned candidate means there is no authored identity to change (spec07).
    """
    if kind not in _record_paths.INHERITED_KINDS or not safe_id(eid):
        return False
    if not _record_paths._record_present(croot, kind, eid):
        return True
    owned, world = _record_paths._identity_path(croot, kind, eid), _record_paths._identity_path(wroot, kind, eid)
    try:
        owned_path, world_path = owned.resolve(strict=True), world.resolve(strict=True)
        owned_id = owned_path.name if kind in appearances_paths.ACTOR_KINDS else owned_path.stem
        world_id = world_path.name if kind in appearances_paths.ACTOR_KINDS else world_path.stem
        if not world.samefile(_record_paths._identity_path(wroot, kind, owned_id)):
            return False
        return (not _record_paths._record_present(croot, kind, world_id)
                or owned.samefile(_record_paths._identity_path(croot, kind, world_id)))
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError as exc:
        raise group_refs.GroupIdentityConflict(
            "Cannot verify this record's World identity; repair access before changing it.") from exc



def require_world_identity_scope(wroot: Path, kind: str, rid: str, cids: Iterable[str]) -> None:
    """Validate held cleanup/adoption identities while the World source exists.

    After removal there is no native proof left. Let the actual owner diagnose
    invalid/absent targets; a real source must be unambiguous in every reached
    campaign before publication, suspension, copy-down or removal (spec07).
    """
    if kind not in _record_paths.INHERITED_KINDS or not safe_id(rid) or not _record_paths._record_present(wroot, kind, rid):
        return
    for cid in cids:
        if not world_identity_matches_owned(cid, kind, rid, wroot):
            raise group_refs.GroupIdentityConflict(
                "This World operation would select a different campaign record through an alias.")


def _canonical_record_id(cid: str, kind: str, eid: str) -> str:
    """Native spelling of an owned source, before a locked removal.

    Validate BEFORE probing. An inherited source retains its submitted id:
    canonicalizing its World spelling could reselect a distinct owned file in
    another namespace. Missing ids retain the owning NotFound refusal (spec 07).
    """
    if kind not in _record_paths.INHERITED_KINDS:
        raise entities.UnknownKind(kind)
    if not safe_id(eid):
        if kind in appearances_paths.ACTOR_KINDS:
            raise _record_paths._actor_not_found(kind, eid)
        if kind == "greetings":
            raise greetings.GreetingNotFound(eid)
        raise entities.EntityNotFound(f"{kind}/{eid}")
    croot = _record_paths.croot_of(cid)
    if _record_paths._record_present(croot, kind, eid):
        path = _record_paths._identity_path(croot, kind, eid).resolve(strict=True)
        return path.name if kind in appearances_paths.ACTOR_KINDS else path.stem
    return eid


def capture_record_refs(cid: str, ref: str, *, extra_refs: Iterable[str] = ()) -> RecordIdentityRefs:
    """Freeze keys in the namespace that gives each ledger its meaning.

    Off and sync bases describe the owned original; Gone hides the World
    original. Neither namespace may collapse identities proved distinct in the
    other. Keep both pre-effect sets: movement destroys the source paths that
    proved them, and their union would lose this ownership distinction (spec 07).
    """
    extras = set(extra_refs)
    if not all(_ledgers._valid_ledger_ref(r, visibility=False) for r in extras | {ref}):
        raise group_refs.GroupIdentityConflict("invalid record reference for identity capture")
    severed, gone = _ledgers.require_identity_ledgers(cid)
    owned = severed | extras | {r for r in campaigns_paths.read_manifest(cid)
                               if _ledgers._valid_ledger_ref(r, visibility=False)}
    world = extras | {r for r in gone if not r.startswith("assets/")}
    world.update("/".join(r.split("/")[1:3]) for r in gone
                 if r.startswith("assets/") and not r.startswith("assets/library/"))
    kind, _, eid = ref.partition("/")
    croot, wroot = _record_paths.croot_of(cid), _record_paths.wroot_of(cid)
    return RecordIdentityRefs(
        owned=frozenset({ref} | {r for r in owned
            if record_is_marked(cid, kind, eid, {r}, roots=(croot, croot))}),
        world=frozenset({ref} | {r for r in world
            if record_is_marked(cid, kind, eid, {r}, roots=(wroot, wroot))}))


def independent_owned_record(cid: str, kind: str, eid: str) -> bool:
    """A detached owned original cannot follow a new World holder of its slug."""
    if not has_own_copy(cid, kind, eid):
        return False
    severed, _ = _ledgers.require_identity_ledgers(cid)
    return record_is_marked(cid, kind, eid, severed)
