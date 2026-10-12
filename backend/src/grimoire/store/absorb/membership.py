"""Host membership proposals: one actor CAS against a captured group lifetime.

Unsaved reviews use host leases; reserved original positions use durable commit
identity targets. Neither membership text nor client-supplied roots confer identity.
The first safe copy consumes only the current row's actor consent (spec07).
"""

from __future__ import annotations

import copy
import re
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path

from pydantic import BaseModel

from .. import commits, entities, entity_schema, group_proposals, group_refs, locks, overlay
from .. import membership as membership_index
from .. import paths as library_paths
from ..campaigns import paths as campaigns_paths
from ..campaigns import read as campaigns_read

PRESENT = "member"
ABSENT = "not a member"
_NONCE = re.compile(r"[0-9a-f]{32}\Z")


class Capture(BaseModel):
    snapshot: membership_index.GroupSnapshot
    leases: dict[str, group_proposals.GroupLease]
    actors: tuple[str, ...]
    store: str
    campaign: str
    world: str


@contextmanager
def held(cid: str):
    store = library_paths.home()
    croot = campaigns_paths.campaign_root(cid)
    world = campaigns_read.world_root_from(croot, store_root=store)
    with library_paths.library_guard(), library_paths.pin_library(store), locks.world_lifecycle_lock(store, world), locks.campaign_lock(cid):
        if str(campaigns_read.world_root_from(croot, store_root=store).resolve()) != str(world.resolve()):
            raise group_refs.GroupIdentityConflict("The campaign's World changed; prepare a new review.")
        overlay.require_identity_ledgers(cid)
        commits.require_readable(cid)
        group_proposals.require_readable(croot)
        yield


def retire_library_inherited(root: Path) -> None:
    """Host discovery and World barriers precede the sorted campaign holds.

    No serialized source path is opened. Owned groups and trusted retained
    copies survive changing libraries; unresolved World proposals do not.
    """
    with library_paths.library_guard(), library_paths.pin_library(root), ExitStack() as stack:
        base = root / "worlds"
        try:
            children = list(base.iterdir())
        except FileNotFoundError:
            children = []
        worlds = []
        for child in children:
            if library_paths.safe_id(child.name):
                try:
                    if stat.S_ISDIR(child.stat().st_mode):
                        worlds.append(child)
                except FileNotFoundError:
                    continue
        for world in sorted(worlds, key=lambda path: str(path.resolve())):
            stack.enter_context(locks.world_lifecycle_lock(root, world))
        campaigns = campaigns_paths.campaign_ids(strict=True)
        with locks.hold_all(campaigns):
            for cid in campaigns:
                group_proposals.require_readable(campaigns_paths.campaign_root(cid))
                commits.require_readable(cid)
            for cid in campaigns:
                group_proposals.retire_inherited(cid, campaigns_paths.campaign_root(cid))
                commits.retire_inherited_claims(cid)


def capture(cid: str) -> Capture:
    inputs = overlay.group_capture(cid)
    snapshot = membership_index.campaign_snapshot(cid, capture=inputs)
    store, croot, world = library_paths.home(), inputs.roots[0], inputs.roots[1]
    identities = {f"groups:{source.id}": group_proposals.GroupIdentity(
        store=str(store.resolve()), campaign=str(croot.resolve()), layer=source.layer,
        world=str(world.resolve()) if source.layer == "world" else None)
        for source in inputs.sources
        if membership_index.prompt_visible(snapshot.index.groups[f"groups:{source.id}"], secret_framed=True)}
    group_proposals.observe_scope(cid, croot, str(store.resolve()), str(world.resolve()))
    leases = group_proposals.register(cid, croot, identities)
    actors = tuple(f"{kind}:{aid}" for kind in ("characters", "pcs") for aid in overlay.cast_ids(cid, kind))
    return Capture(snapshot=snapshot, leases=leases, actors=actors, store=str(store.resolve()),
                   campaign=str(croot.resolve()), world=str(world.resolve()))


def group_ref(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.startswith(("groups:", "groups/")):
        value = value[7:]
    elif ":" in value or "/" in value:
        return None
    return f"groups:{value}" if entity_schema.referenceable(value) else None


def actor_ref(value: object) -> str | None:
    """A model-supplied actor in either spelling -- `characters/<id>` (the
    Present line's) or `characters:<id>` -- as the stored `<kind>:<id>` ref, or
    None when it names no referenceable character or PC."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    kind, sep, rest = value.partition("/")
    return group_refs.actor_ref(f"{kind}:{rest}" if sep and ":" not in value else value)


def _original_id(edit: dict) -> object:
    """The group id the row was staged against: a resumed row's target may have
    followed a relocation, but its reserved identity remembers the original."""
    reserved = edit.get("_membership_target")
    if isinstance(reserved, dict) and isinstance(reserved.get("ref"), str):
        return reserved["ref"].partition(":")[2]
    target = edit.get("target")
    return target.get("id") if isinstance(target, dict) else None


def shape(edit: dict) -> tuple[str, str]:
    target, payload = edit.get("target"), edit.get("payload")
    if (not isinstance(target, dict) or target.get("kind") != "groups"
            or not isinstance(target.get("id"), str) or not entity_schema.referenceable(target["id"])
            or not isinstance(payload, dict) or set(payload) != {"group", "actor", "change", "group_lifecycle_token"}
            or payload.get("group") != _original_id(edit)
            or group_refs.actor_ref(payload.get("actor")) != payload.get("actor")
            or not isinstance(payload.get("actor"), str)
            or not isinstance(payload.get("change"), str) or payload["change"] not in {"join", "leave"}
            or not isinstance(payload.get("group_lifecycle_token"), str)
            or not _NONCE.fullmatch(payload["group_lifecycle_token"])
            or edit.get("field") != f"members:{payload['actor']}"
            or not isinstance(edit.get("before"), str) or edit["before"] not in {PRESENT, ABSENT}
            or edit["before"] == edit.get("after")
            or edit.get("after") != (PRESENT if payload["change"] == "join" else ABSENT)):
        raise group_refs.GroupIdentityConflict("This membership row has an invalid actor, change or host token; prepare a new review.")
    return payload["actor"], payload["change"]


#: Why a row that named a group or actor it can no longer reach did not land.
GROUP_GONE = "that group no longer exists in this campaign"
ACTOR_GONE = "that character no longer exists in this campaign"
UNAVAILABLE = ("the group this row was prepared against is no longer available; "
               "record the change in the group editor")


def strip_private(edits: list) -> list:
    """The request's edits without this module's private `_membership_*`
    bookkeeping keys: those are set on the server's own copy, never taken
    from a client."""
    return [{k: v for k, v in edit.items() if not k.startswith("_membership")}
            if isinstance(edit, dict) else edit for edit in edits]


def _shape_error(edit: dict) -> str | None:
    try:
        shape(edit)
    except group_refs.GroupIdentityConflict as exc:
        return str(exc)
    return None


def initial_targets(cid: str, edits: list[dict]) -> tuple[dict, dict[str, str]]:
    """Reserve each well-formed row's original group identity, by position.

    One row's failure is that row's (spec 07 sections 4.5 and 9.4): a forged
    shape, a group deleted since the review, or a lease that was retired or
    never issued fails only its own position, which `resumed_edits` marks so
    `apply` reports it without any record, citation or reversal write. The
    rest of the save proceeds.
    """
    croot = campaigns_paths.campaign_root(cid)
    targets: dict = {}
    failures: dict[str, str] = {}
    for position, edit in enumerate(edits):
        if not isinstance(edit, dict) or edit.get("kind") != "membership" or _shape_error(edit):
            continue
        actor, _change = shape(edit)
        rid, nonce = edit["target"]["id"], edit["payload"]["group_lifecycle_token"]
        try:
            _record, context = overlay.read_group_membership(cid, rid)
            current = group_proposals.GroupIdentity(store=str(library_paths.home().resolve()),
                campaign=str(croot.resolve()), layer=context.layer,
                world=str(overlay.wroot_of(cid).resolve()) if context.layer == "world" else None)
            lease = group_proposals.validate(cid, croot, f"groups:{rid}", nonce, current)
        except entities.EntityNotFound:
            failures[str(position)] = GROUP_GONE
            continue
        except group_refs.GroupIdentityConflict:
            failures[str(position)] = UNAVAILABLE
            continue
        source = lease.copied_from or lease.source
        target = {"kind": "groups", "id": rid, "ref": f"groups:{rid}", "actor": actor,
                  "token": nonce, "source": group_proposals.identity_data(source), "retired": False}
        if lease.copied_from is not None:
            target["copy_receipt"] = {"token": nonce, "source": group_proposals.identity_data(source),
                                      "owned": group_proposals.identity_data(lease.source)}
        targets[str(position)] = target
    return targets, failures


def resumed_edits(edits: list[dict], targets: dict, failures: dict[str, str] | None = None) -> list[dict]:
    """The reviewed batch with each membership row bound to its reserved
    identity. A forged row is marked to be skipped; a row with no usable
    reserved identity is marked failed with its reason. Neither raises, so one
    row never refuses the save."""
    out = copy.deepcopy(edits)
    for position, edit in enumerate(out):
        if not isinstance(edit, dict) or edit.get("kind") != "membership":
            continue
        problem = _shape_error(edit)
        if problem is not None:
            edit["_membership_skip"] = problem
            continue
        saved = targets.get(str(position))
        if (not isinstance(saved, dict) or not isinstance(saved.get("kind"), str)
                or saved["kind"] not in entities.ENTITY_KINDS
                or not isinstance(saved.get("id"), str) or not entity_schema.referenceable(saved["id"])
                or saved.get("actor") != edit["payload"]["actor"]
                or saved.get("token") != edit["payload"]["group_lifecycle_token"]
                or not group_proposals.valid_identity(saved.get("source"))):
            edit["_membership_failed"] = (failures or {}).get(str(position), UNAVAILABLE)
            continue
        edit["target"] = {"kind": saved["kind"], "id": saved["id"]}
        edit["_membership_target"] = copy.deepcopy(saved)
        edit["_membership_position"] = str(position)
    return out


def verify(cid: str, target: dict) -> None:
    if target.get("retired") or "relocating" in target or target.get("kind") != "groups":
        raise group_refs.GroupIdentityConflict("This original membership target was removed or is no longer an available group.")
    source = target.get("source")
    if not group_proposals.valid_identity(source) or not isinstance(source, dict):
        raise group_refs.GroupIdentityConflict("The original membership source cannot be verified.")
    croot = campaigns_paths.campaign_root(cid)
    scope = (str(library_paths.home().resolve()), str(croot.resolve()))
    if (source["store"], source["campaign"]) != scope:
        raise group_refs.GroupIdentityConflict("The membership review belongs to a different store or campaign.")
    receipt = target.get("copy_receipt")
    if source["layer"] == "campaign" or receipt:
        if receipt and (receipt.get("source") != source or receipt.get("token") != target.get("token")
                or receipt.get("owned") != {"store": scope[0], "campaign": scope[1], "layer": "campaign", "world": None}):
            raise group_refs.GroupIdentityConflict("The original group copy receipt cannot be verified.")
        overlay.require_owned_entity(cid, "groups", target["id"])
    elif (str(overlay.wroot_of(cid).resolve()) != source["world"]
            or overlay.has_own_copy(cid, "groups", target["id"])):
        raise group_refs.GroupIdentityConflict("The inherited group source changed without a trusted retained-copy receipt.")
    overlay.require_identity_ledgers(cid)


def observe_targets(cid: str, token: str, targets: dict) -> dict:
    """Durably refuse an observed unavailable original before resuming effects."""
    croot = campaigns_paths.campaign_root(cid)
    for position, target in targets.items():
        if target.get("retired") or "relocating" in target or target.get("kind") != "groups":
            continue
        try:
            verify(cid, target)
            # Read through the overlay to distinguish a missing original from
            # an available inherited target; provenance itself is never opened.
            overlay.read_group_membership(cid, target["id"])
        except (entities.EntityNotFound, group_refs.GroupIdentityConflict):
            commits.retire_membership_position(cid, token, position)
            group_proposals.retire_nonce(cid, croot, target["token"])
    entry = commits.lookup(cid, token)
    return entry["progress"].get("membership_targets", {}) if entry is not None else targets


def current(cid: str, edit: dict) -> str:
    if edit.get("_membership_target") is not None:
        verify(cid, edit["_membership_target"])
    actor, _change = shape(edit)
    _record, context = overlay.read_group_membership(cid, edit["target"]["id"])
    return PRESENT if actor in context.members else ABSENT


def _usable(edit: object) -> bool:
    """A membership row the batch may act on: well-formed and bound to an
    identity, not one `resumed_edits` marked skipped or failed."""
    return (isinstance(edit, dict) and edit.get("kind") == "membership"
            and "_membership_skip" not in edit and "_membership_failed" not in edit
            and _shape_error(edit) is None)


def _actor_exists(cid: str, actor: str) -> bool:
    kind, _sep, aid = actor.partition(":")
    try:
        root = overlay.actor_root(cid, kind, aid)
    except (OSError, ValueError):
        return False
    metadata = "character.md" if kind == "characters" else "pc.md"
    return (root / kind / aid / metadata).is_file()


def neutral_actors(cid: str, edits: list[dict]) -> dict[tuple[str, str], set[str]]:
    """Per group, the approved actors whose filtered raw tokens a first copy may
    drop (spec 07 section 9.4.1). Only validated rows count, and a join only
    while its actor still exists: no forged, failed or missing-actor join
    supplies preparation authority. `apply` intersects this with the group's
    filtered members, so nobody's effective membership changes by it."""
    groups: dict[tuple[str, str], set[str]] = {}
    for edit in edits:
        if not _usable(edit):
            continue
        actor, change = shape(edit)
        if change == "join" and not _actor_exists(cid, actor):
            continue
        target = edit["target"]
        groups.setdefault((target["kind"], target["id"]), set()).add(actor)
    return groups


def apply_order(cid: str, edits: list[dict]) -> list[int]:
    """Processing order for the batch: original order, except that within one
    inherited group a dangling (unresolved, not detached) member's approved
    leave runs first, so the first copy does not carry the unresolved slot.
    Candidates go in canonical `(kind, group, actor, change)` order with the
    original position as the tie-breaker; they take the slots the group's rows
    already held, so rows outside the group keep their order. Outcomes stay
    keyed by original position."""
    order = list(range(len(edits)))
    by_group: dict[tuple[str, str], list[int]] = {}
    for position, edit in enumerate(edits):
        if _usable(edit):
            by_group.setdefault((edit["target"]["kind"], edit["target"]["id"]), []).append(position)
    for (kind, gid), positions in by_group.items():
        try:
            _record, context = overlay.read_group_membership(cid, gid)
        except (entities.EntityNotFound, group_refs.GroupIdentityConflict, OSError):
            continue
        if context.layer != "world":
            continue
        def canonical(position: int, kind: str = kind, gid: str = gid) -> tuple:
            actor, change = shape(edits[position])
            return (kind, gid, actor, change, position)
        first = sorted((p for p in positions
                        if shape(edits[p])[1] == "leave"
                        and shape(edits[p])[0] in context.unresolved_members
                        and shape(edits[p])[0] not in context.filtered_members), key=canonical)
        rest = [p for p in positions if p not in first]
        for slot, position in zip(positions, first + rest, strict=True):
            order[slot] = position
    return order


def apply(cid: str, edit: dict, absent_actors: set[str], *, sid: str | None, undo_store) -> dict:
    eid = edit.get("id", "") if isinstance(edit.get("id"), str) else ""
    if "_membership_skip" in edit:
        return {"state": "skipped"}       # a forged or corrupt row: nothing was lost
    if "_membership_failed" in edit:
        return {"state": "failed", "id": eid, "kind": "conflict", "reason": edit["_membership_failed"]}
    target = edit.get("_membership_target")
    if not isinstance(target, dict):
        raise group_refs.GroupIdentityConflict("Membership writes require an original reserved identity target.")
    verify(cid, target)
    actor, change = shape(edit)
    try:
        record, context = overlay.read_group_membership(cid, target["id"])
    except entities.EntityNotFound:
        return {"state": "failed", "id": eid, "kind": "conflict", "reason": GROUP_GONE}
    before = actor in context.members
    desired = change == "join"
    if before == desired:
        return {"state": "settled", "id": eid}
    if desired and not _actor_exists(cid, actor):
        return {"state": "failed", "id": eid, "kind": "conflict", "reason": ACTOR_GONE}
    raw = record["meta"].get("members")
    if context.layer == "world":
        for other in set(context.filtered_members) & absent_actors:
            raw = group_refs.edited_line(raw, other, "leave")
    final = group_refs.edited_line(raw, actor, "join" if desired else "leave")
    # One final copy, original sync base, and only THIS row's Members consent.
    edit["_membership_attempted"] = True
    overlay.update_entity(cid, "groups", target["id"], fields={"members": final},
        reviewed_slots=frozenset({("members", actor)}) if desired else frozenset())
    source = target["source"]
    receipt = target.get("copy_receipt")
    if context.layer == "world":
        receipt = {"token": target["token"], "source": copy.deepcopy(source), "owned": {
            "store": str(library_paths.home().resolve()),
            "campaign": str(campaigns_paths.campaign_root(cid).resolve()), "layer": "campaign", "world": None}}
        target["copy_receipt"] = receipt
    reversal = {"w": "membership", "kind": "groups", "id": target["id"], "actor": actor,
        "token": target["token"], "scope": {"store_root": source["store"], "campaign_root": source["campaign"], "layer": "campaign"},
        # A join's reviewer approved this exact actor's slot. A leave approved a
        # removal, not a binding -- except a dangling cleanup leave, whose
        # reviewer saw that very unresolved slot (spec 07 section 17.4). An
        # ordinary leave's Undo revalidates the World actor instead.
        "bindings": [actor] if desired or actor in context.unresolved_members else [],
        "inherited": [actor] if source["layer"] == "world" and actor in group_refs.parse_members(record["meta"]) else [],
        "actor_world_root": source["world"], "retired": False}
    row = {"kind": "membership", "ref": {"kind": "groups", "id": target["id"]},
        "field": edit["field"], "label": edit.get("label", actor),
        "before": PRESENT if before else ABSENT, "after": PRESENT if desired else ABSENT,
        "membership_position": edit.get("_membership_position"),
        "undo": undo_store.seal(cid, reversal, before)}
    row["why"] = "" if row["undo"] else "The original membership could not be sealed for Undo."
    # No `recorded` write-back delta: the changes log is for bodies, and
    # membership is not in `changes.BROWSABLE_KINDS` (spec 07 section 9.5).
    return {"state": "applied", "id": eid, "journalled": row, "membership_copy": receipt}


def fence_publication(cid: str, progress: dict) -> None:
    """Recheck deferred positions before publishing citations, diffs or Undo."""
    for position, target in progress.get("membership_targets", {}).items():
        available = True
        try:
            verify(cid, target)
        except (entities.EntityNotFound, group_refs.GroupIdentityConflict, OSError):
            available = False
        original = target.get("ref", f"groups:{target.get('id', '')}").replace(":", "/", 1)
        landed = f"{target.get('kind')}/{target.get('id')}"
        commits.move_actor_deltas(progress, {original, landed},
                                  f"members:{target.get('actor', '')}", landed, available)
        for row in progress.get("journalled", []):
            if row.get("membership_position") != position:
                continue
            commits.retarget_journal_row(row, target)
            if not available:
                row["undo"] = None
                row["why"] = "The original membership target is no longer available."
