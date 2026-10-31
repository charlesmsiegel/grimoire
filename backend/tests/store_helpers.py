"""Synthetic fixtures for native-store tests: a harbor world, its add-on and two campaigns."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.security import Authorizer, Grant, Permission, ResourcePath
from grimoire.store import LibraryStore, LinkSpec, ProcessLocker, RecordEnvelope, SourceManifest

OWNER = "human:owner"
HARBOR = "world:harbor"
ADDON = "addon:fan"
WREN = SourceRef(HARBOR, "character", "wren")
WREN_R1 = WREN.at("r1")
WREN_R2 = WREN.at("r2")
WREN_A = InstanceRef("c1", "character", "wren-a")
WREN_B = InstanceRef("c2", "character", "wren-b")


def authorizer(*scopes: str) -> Authorizer:
    authority = Authorizer([OWNER])
    for number, scope in enumerate(scopes or ("sources", "campaigns")):
        authority.add_grant(Grant(f"g{number}", OWNER, frozenset({Permission.APPLY}),
                                  ResourcePath.parse(scope), OWNER))
    return authority


def owner_handle(*scopes: str):
    return authorizer(*scopes).handle_for(OWNER, trusted=True)


def harbor_manifest(heads=None, links=(), **extra) -> SourceManifest:
    return SourceManifest(HARBOR, OWNER, "Harbor", heads or {}, tuple(links),
                          extra=extra or {"x-notes": "kept verbatim"})


def wren(revision: str = "r1", bio: str = "a sailor") -> RecordEnvelope:
    return RecordEnvelope(WREN.at(revision), {"bio": bio, "tags": ["sea"]}, name="Wren",
                          extra={"x-author-note": {"draft": True}})


def instance(ref: InstanceRef, derived_from, base=WREN_R1, hp: int = 3, base_etag=None) -> RecordEnvelope:
    return RecordEnvelope(ref, {"hp": hp}, derived_from=derived_from, base=base, base_etag=base_etag)


def seed(store: LibraryStore, handle=None) -> dict[str, str]:
    """World harbor with wren@r1 and two instances: c1 pinned to r1, c2 following the head."""
    txn = store.transaction()
    txn.put(harbor_manifest(), None)
    txn.put(wren("r1"), None)
    txn.put(instance(WREN_A, WREN_R1), None)
    txn.put(instance(WREN_B, WREN), None)
    return dict(txn.commit(handle or owner_handle()).etags)


def new_library(path: Path, **kwargs) -> LibraryStore:
    return LibraryStore.create(path, **kwargs)


def drop_empty_dirs(tree: dict) -> dict:
    """Directories with no file below them are not state."""
    files = [key for key, value in tree.items() if value is not None]
    return {key: value for key, value in tree.items()
            if value is not None or any(name.startswith(key + "/") for name in files)}


def make_dir_link(link: Path, target: Path) -> None:
    """A directory symlink, or a junction on Windows; skip where neither is possible."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        try:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
            return
        except (OSError, ImportError):
            pass
    pytest.skip("cannot create directory links here")


def shared_locker() -> ProcessLocker:
    return ProcessLocker()


__all__ = [
    "ADDON", "HARBOR", "LinkSpec", "OWNER", "WREN", "WREN_A", "WREN_B", "WREN_R1", "WREN_R2", "authorizer",
    "drop_empty_dirs", "harbor_manifest", "instance", "make_dir_link", "new_library", "owner_handle", "seed",
    "shared_locker", "wren",
]
