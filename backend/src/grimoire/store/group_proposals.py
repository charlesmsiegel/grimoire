"""Host group identity leases, separate from authoritative membership (spec07).

Capture registers before generation. Saves validate an existing nonce rather
than adopting a submitted slug. Stored provenance is compared with host roots;
it is never a path to open. Higher owners coordinate the World barrier.
"""

from __future__ import annotations

import copy
import json
import re
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from . import atomic, entity_schema, group_refs, locks, paths, revision
from .campaigns import paths as campaigns_paths


class GroupIdentity(BaseModel):
    store: str
    campaign: str
    layer: Literal["world", "campaign"]
    world: str | None


class GroupLease(BaseModel):
    token: str
    source: GroupIdentity
    retired: bool = False
    copied_from: GroupIdentity | None = None


_NONCE = re.compile(r"[0-9a-f]{32}\Z")


def identity_data(identity: GroupIdentity) -> dict:
    return {"store": identity.store, "campaign": identity.campaign,
            "layer": identity.layer, "world": identity.world}


def _lease_data(lease: GroupLease) -> dict:
    return {"token": lease.token, "source": identity_data(lease.source),
            "retired": lease.retired,
            "copied_from": identity_data(lease.copied_from) if lease.copied_from else None}


def valid_identity(value) -> bool:
    return bool(isinstance(value, dict) and set(value) == {"store", "campaign", "layer", "world"}
            and all(isinstance(value.get(key), str) and value[key] for key in ("store", "campaign"))
            and ((value.get("layer") == "campaign" and value.get("world") is None)
                 or (value.get("layer") == "world" and isinstance(value.get("world"), str) and value["world"])))


def _valid_ref(ref: str) -> bool:
    kind, sep, eid = ref.partition(":")
    return bool(sep and kind == "groups" and entity_schema.referenceable(eid))


def _read(croot: Path) -> dict[str, GroupLease]:
    try:
        doc = json.loads((croot / "membership_review_tokens.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise group_refs.GroupIdentityConflict("membership_review_tokens.json cannot be read; repair it before capture or saving.") from exc
    if (not isinstance(doc, dict) or set(doc) != {"v", "groups"}
            or type(doc.get("v")) is not int or doc["v"] != 1 or not isinstance(doc.get("groups"), dict)):
        raise group_refs.GroupIdentityConflict("membership_review_tokens.json has an invalid account; repair it before capture or saving.")
    out = {}
    for ref, row in doc["groups"].items():
        if (not _valid_ref(ref) or not isinstance(row, dict)
                or set(row) != {"token", "source", "retired", "copied_from"}
                or not isinstance(row.get("token"), str) or not _NONCE.fullmatch(row["token"])
                or type(row.get("retired")) is not bool or not valid_identity(row.get("source"))
                or (row.get("copied_from") is not None and not valid_identity(row["copied_from"]))):
            raise group_refs.GroupIdentityConflict("membership_review_tokens.json has an invalid group identity; repair the complete account.")
        out[ref] = GroupLease(**row)
    return out


def _write(croot: Path, leases: Mapping[str, GroupLease]) -> None:
    atomic.write_text(croot / "membership_review_tokens.json", json.dumps(
        {"v": 1, "groups": {ref: _lease_data(lease) for ref, lease in leases.items()}}, indent=2) + "\n")


@contextmanager
def _hold(cid: str, croot: Path):
    # The explicit root came from the host snapshot. Refuse a stale-root call
    # rather than write A while campaign_lock has selected the OS lock in B.
    # Pin every resolver through this hold; stored provenance is never opened.
    with paths.pin_library(paths.home()):
        try:
            same_root = campaigns_paths.campaign_root(cid).samefile(croot)
        except OSError as exc:
            raise group_refs.GroupIdentityConflict("The campaign store cannot be verified; prepare a new review.") from exc
        if not same_root:
            raise group_refs.GroupIdentityConflict("The campaign store changed; prepare a new review.")
        with locks.campaign_lock(cid):
            yield


def register(cid: str, croot: Path, identities: Mapping[str, GroupIdentity]) -> dict[str, GroupLease]:
    """Host capture only: callers hold each inherited source's World barrier."""
    for ref, identity in identities.items():
        if not _valid_ref(ref):
            raise group_refs.GroupIdentityConflict("Cannot capture an invalid group reference.")
        if identity.layer == "world" and not locks.world_lifecycle_held(identity.store, identity.world):
            raise RuntimeError("membership capture requires the World lifecycle barrier before campaign holds")
    with _hold(cid, croot):
        leases = _read(croot)
        changed = False
        for ref, identity in identities.items():
            old = leases.get(ref)
            if old is None or old.retired or old.source != identity:
                leases[ref] = GroupLease(token=uuid.uuid4().hex, source=identity)
                changed = True
        if changed:
            _write(croot, leases)
            # The absorb start that captures is `@computes_only`, so the
            # activity middleware stamps nothing for it: a durable lease write
            # bumps the campaign's revision itself, inside the hold that wrote
            # it, so an expectation read before this absorb no longer passes.
            revision.bump(cid)
        return copy.deepcopy({ref: leases[ref] for ref in identities})


def validate(cid: str, croot: Path, ref: str, token: str, expected: GroupIdentity) -> GroupLease:
    """Unknown/retired identities cannot be adopted by a first saver."""
    with _hold(cid, croot):
        leases = _read(croot)
        lease = leases.get(ref)
        if lease is None or lease.token != token or lease.retired:
            raise group_refs.GroupIdentityConflict("This group's review identity expired; prepare a new review.")
        if lease.source != expected:
            # Observation is durable: switching back cannot revive this nonce.
            lease.retired = True
            _write(croot, leases)
            raise group_refs.GroupIdentityConflict("The group's source or store changed; prepare a new review.")
        return copy.deepcopy(lease)


def retire(cid: str, croot: Path, refs: set[str]) -> None:
    with _hold(cid, croot):
        leases = _read(croot)
        changed = False
        for ref in refs:
            lease = leases.get(ref)
            if lease is not None and not lease.retired:
                lease.retired = True
                changed = True
        if changed:
            _write(croot, leases)


def retire_world(cid: str, croot: Path, world: str) -> None:
    with _hold(cid, croot):
        leases = _read(croot)
        retire(cid, croot, {ref for ref, lease in leases.items()
            if lease.source.layer == "world" and lease.source.world == world})


def copied(cid: str, croot: Path, ref: str, token: str, owned: GroupIdentity) -> None:
    """Trusted copy receipt only; called by the final-copy owner after it lands."""
    with _hold(cid, croot):
        leases = _read(croot)
        lease = leases.get(ref)
        if (lease is None or lease.retired or lease.token != token or owned.layer != "campaign"
                or (lease.source.store, lease.source.campaign) != (owned.store, owned.campaign)):
            raise group_refs.GroupIdentityConflict("The original group copy identity changed; prepare a new review.")
        if lease.source.layer == "world":
            lease.copied_from = lease.source
            lease.source = owned
            _write(croot, leases)


def references_world(croot: Path, world: str) -> bool:
    """Strict historical-source discovery; never follow a stored path."""
    return any(not lease.retired and lease.source.layer == "world" and lease.source.world == world
               for lease in _read(croot).values())


def require_readable(croot: Path) -> None:
    _read(croot)


def matching_refs(croot: Path, root: Path, rid: str, *, world: str | None = None) -> set[str]:
    """Select by a host namespace before unlink/move destroys native proof."""
    selected = set()
    for ref, lease in _read(croot).items():
        if lease.retired or (world is not None and (lease.source.layer != "world" or lease.source.world != world)):
            continue
        eid = ref.partition(":")[2]
        if eid == rid:
            selected.add(ref)
            continue
        try:
            if (root / "groups" / f"{eid}.md").samefile(root / "groups" / f"{rid}.md"):
                selected.add(ref)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise group_refs.GroupIdentityConflict("cannot verify group review identity before changing its record") from exc
    return selected


def retained_copy(cid: str, croot: Path, refs: set[str], source: GroupIdentity, owned: GroupIdentity) -> None:
    """A host copy owner calls only after final bytes land; never infer from bytes."""
    with _hold(cid, croot):
        leases = _read(croot)
        for ref in refs:
            lease = leases.get(ref)
            if lease is not None and not lease.retired and lease.source == source:
                copied(cid, croot, ref, lease.token, owned)


def observe_scope(cid: str, croot: Path, store: str, world: str) -> None:
    """An observed root/binding change cannot revive after A->B->A."""
    with _hold(cid, croot):
        leases = _read(croot)
        retire(cid, croot, {ref for ref, lease in leases.items() if not lease.retired
            and (lease.source.store != store or lease.source.campaign != str(croot.resolve())
                 or (lease.source.layer == "world" and lease.source.world != world))})


def retire_inherited(cid: str, croot: Path) -> None:
    with _hold(cid, croot):
        leases = _read(croot)
        retire(cid, croot, {ref for ref, lease in leases.items() if lease.source.layer == "world"})


def retire_nonce(cid: str, croot: Path, nonce: str) -> None:
    """Retire exactly the observed lifetime, leaving a newer capture alone."""
    with _hold(cid, croot):
        leases = _read(croot)
        retire(cid, croot, {ref for ref, lease in leases.items() if lease.token == nonce})
