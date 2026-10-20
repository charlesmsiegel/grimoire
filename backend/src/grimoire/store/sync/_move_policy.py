"""sync: move policy responsibilities."""

from __future__ import annotations

from pathlib import Path

from .. import characters, entities, group_refs, overlay, pcs
from .. import frontmatter as _store_frontmatter
from .. import paths as _store_paths
from ..appearances import versions as appearances_versions
from ..worlds import paths as worlds_paths
from . import _read_shapes

# ---------------------------------------------------------------------------
# Campaign → world: promote, push, demote (#52, #53, #60)
# ---------------------------------------------------------------------------

class LibraryMoveError(Exception):
    """Base for every refusal below, so a route can map the family in one place."""


class PromoteConflictError(LibraryMoveError):
    """The world already holds that id, so promoting would overwrite a stranger."""


class PushConflictError(LibraryMoveError):
    """The world moved since the campaign's base — the mirror of a pull conflict."""


class NotDivergedError(LibraryMoveError):
    """Nothing to push: the campaign has no copy, or its copy matches the world."""


class NotInLibraryError(LibraryMoveError):
    """Nothing to push *to*: the record is campaign-local. That is a promote."""


class NotPushableError(LibraryMoveError):
    """A kind this operation deliberately does not carry."""


class NotDemotableError(LibraryMoveError):
    """A kind demote deliberately does not carry."""


class DanglingReferenceError(LibraryMoveError):
    """Promoting would publish a library record pointing at campaign-local content."""


class UnknownTargetError(LibraryMoveError):
    """A demote named a campaign that is not a dependent of this record."""


def _flat_kind(kind: str) -> None:
    if kind not in entities.SYNCED_KINDS:
        raise entities.UnknownKind(kind)


def _safe_ref(kind: str, eid: str) -> None:
    """Refuse an id before it is ever joined onto a world root.

    `kind` and `eid` reach here as path parameters, and a path parameter can
    carry an encoded slash. Everything below writes to the WORLD, which no
    campaign resolver guards, so the check belongs at the top of each move
    rather than in whatever happens to run first: the campaign-side readers do
    refuse an unsafe id today, and relying on that would make these functions
    safe only for as long as the order of their own checks never changes."""
    if not _store_paths.safe_id(eid):
        raise entities.EntityNotFound(_read_shapes._ref_str(kind, eid))


def _require_world(wroot: Path) -> Path:
    """The world root, once it is known to be a world that still exists.

    `campaigns.read.world_root_of` answers with a path whether or not anything
    is there — a world deleted before the guard against that existed, or a
    campaign that records no world at all. Every move below then does
    `mkdir(parents=True)` on the way to its write, which would REBUILD that
    directory around the record it is writing: a tree with no world.md, which
    nothing lists as a world and no route can reach, holding the only copy of a
    record the campaign has just recorded a sync base for."""
    if not worlds_paths.names_its_directory(wroot) or not (wroot / "world.md").exists():
        raise worlds_paths.WorldNotFound(wroot.name)
    return wroot


def _world_file(wroot: Path, kind: str, eid: str) -> Path:
    return wroot / kind / f"{eid}.md"


def _world_holds_flat(wroot: Path, kind: str, eid: str) -> bool:
    """Is that id claimed in the world? The record DIRECTORY counts, exactly as
    it does for `overlay.create_entity`'s uniquify: it holds the assets a
    promoted record would silently adopt."""
    return _world_file(wroot, kind, eid).exists() or (wroot / kind / eid).is_dir()


def _actor_meta_name(kind: str) -> str:
    return "character.md" if kind == "characters" else "pc.md"


def _world_holds_actor(wroot: Path, kind: str, aid: str) -> bool:
    """Is there an ACTOR under that id in the world?

    Keyed on the meta file rather than on the directory, and that is the whole
    of an interrupted promotion's retry. The directory is created before the
    version files and the meta is written last, so a kill in between leaves a
    directory holding no actor -- `overlay.actor_root`, `_record_present` and
    every reader agree that is not one. Asking `is_dir()` here would call that
    residue an existing library actor and refuse every retry with
    `PromoteConflictError`, permanently, which is exactly the stuck state the
    base-first ordering exists to avoid (Codex review).
    """
    return (wroot / kind / aid / _actor_meta_name(kind)).exists()


def _publishes_membership(kind: str, text: str) -> bool:
    """Whether publishing these bytes adopts World actor identities (spec 07 5.5).

    A group always does; any other flat kind only while it carries a
    well-formed dormant Members or Leader ref. A record with neither -- a
    location, a lore entry -- publishes no membership, so it takes on no new
    strict ledger-read prerequisite and keeps the publication it had.
    """
    return kind == "groups" or bool(group_refs.group_slots(_store_frontmatter.parse_frontmatter(text)[0]))


def _require_world_members(cid: str, wroot: Path, text: str, kind: str, eid: str) -> None:
    """Publishing Members and Leader adopts the destination World identities.

    Force resolves a text conflict, never a missing or detached actor. Dormant
    actor fields on other flat kinds get the same check before their bytes can
    later become a group through reclassification (spec 07). A non-group
    record with no well-formed actor ref is a no-op here: no ledger is read.
    """
    if not _publishes_membership(kind, text):
        return
    ref = _read_shapes._ref_str(kind, eid)
    severed, _ = overlay.require_identity_ledgers(cid)
    meta, _ = _store_frontmatter.parse_frontmatter(text)
    slots = group_refs.group_slots(meta)
    severed = overlay.actor_detachments(overlay.croot_of(cid), wroot,
                                       (actor for _, actor in slots), severed)
    for field, actor in sorted(slots):
        akind, aid = actor.split(":", 1)
        if (_read_shapes._ref_str(akind, aid) in severed or not _world_holds_actor(wroot, akind, aid)
                or not overlay.world_identity_matches_owned(cid, akind, aid, wroot)):
            raise DanglingReferenceError(
                f"{ref} {field} names {actor}, which is not this campaign's World actor "
                "— promote that actor into the library first, or remove them from the group")


def _require_world_character(cid: str, wroot: Path, text: str, gid: str) -> None:
    """A greeting names the character it belongs to, so the library needs that
    character too -- otherwise promoting publishes a greeting pointing at
    nothing, and the campaign that promoted it is the only place it reads.

    A *detached* character fails this for the opposite reason: the world does
    hold that slug, but detachment is the statement that whatever holds it is a
    stranger to this campaign's character of the same id (#225). Promoting the
    greeting would file it against that stranger -- which is worse than a
    dangling reference, because it reads as working."""
    meta, _ = _store_frontmatter.parse_frontmatter(text)
    detached_here = overlay.detached(cid)
    char = str(meta.get("character", "")).strip()
    if char:
        _require_world_actor(wroot, detached_here, char, gid, "belongs to")
        # The VERSION too, not just the actor. `start_from_greeting` seats the
        # owner at `g["version"]`, so a greeting pinned to a campaign-only
        # version of an otherwise-shared character publishes fine and then
        # raises in every sibling campaign (Codex review).
        vid = str(meta.get("version", "")).strip()
        if (vid and _store_paths.safe_id(vid)
                and appearances_versions.actor_hash(wroot, "characters", char, vid) is None):
            raise DanglingReferenceError(
                f"{gid} is pinned to {char}'s {vid} version, which is not in the library")
    # `present` names the rest of the cast the greeting opens with, and
    # `playing.start_from_greeting` reads every one of them. A campaign-local
    # name there publishes a greeting that raises the moment anyone starts it
    # -- in a sibling campaign, where nothing explains why (Codex review).
    for name in (x.strip() for x in str(meta.get("present", "")).split(",")):
        if not name:
            continue
        # Stripped, because `greetings._tags_list` now strips too. These two
        # must agree: validating a different id than the reader resolves either
        # passes a greeting that cannot seat its cast, or -- as when neither
        # stripped -- quietly skips the check because " winifred" fails
        # `safe_id` and this returns early (Codex review).
        _require_world_actor(wroot, detached_here, name, gid, "opens with")


def _require_world_actor(wroot: Path, detached_here: set[str], char: str,
                         gid: str, role: str) -> None:
    if not _store_paths.safe_id(char):
        return   # hand-edited: nothing this check can say about it
    if not (wroot / "characters" / char / "character.md").exists():
        raise DanglingReferenceError(
            f"{gid} {role} {char}, which is not in the library yet — promote it first")
    if _read_shapes._ref_str("characters", char) in detached_here:
        raise DanglingReferenceError(
            f"{gid} {role} this campaign's own {char}, which is not the "
            f"library's {char} — promoting it would file the greeting against a stranger")


def _actor_missing(kind: str, aid: str) -> Exception:
    return (characters.CharacterNotFound(aid) if kind == "characters"
            else pcs.PCNotFound(aid))
