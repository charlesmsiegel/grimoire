"""Synthetic fixtures for native-store tests: a harbor world, its add-on and two campaigns."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.security import Authorizer, Grant, Permission, ResourcePath
from grimoire.store import LibraryStore, LinkSpec, ProcessLocker, RecordEnvelope, SourceManifest, etag
from grimoire.testing import CrashReport, Interleave
from grimoire.testing.faults import FileOp, nth_match

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


#: 028's deletion fixtures (``test_store_delete.py`` and ``test_store_deletion_recovery.py``): the scopes a
#: deletion is authorized on and the seeded records it deletes.
DELETE_SCOPES = ("sources", "campaigns", "kept")
WREN_A_PATH = "campaigns/c1/instances/character/wren-a.json"
WREN_B_PATH = "campaigns/c2/instances/character/wren-b.json"
R1_PATH = "sources/world/harbor/_source/records/character/wren/r1.json"
MANIFEST_PATH = "sources/world/harbor/_source/manifest.json"


def delete_held(store: LibraryStore, *paths: str, handle=None) -> None:
    """Delete each record at ``paths`` in one transaction, each with a hold of exactly its bytes."""
    txn = store.transaction()
    for path in paths:
        data = (store.root / path).read_bytes()
        txn.keep_version(path, data, regenerable=False)
        txn.delete_exact(path, etag(data))
    txn.commit(handle or owner_handle(*DELETE_SCOPES))


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


#: An exploration mode: every injected I/O error, and every crash of the recovery that follows it.
#: (``errors`` alone stops at the first recovery; ``recovery-crashes`` crashes the recovery after a crash.)
FAIL_RECOVERY = "fail-recovery-crashes"
#: Explorations too slow for the CI shards (docs/TESTING.md#ci-gate) run only with this set.
EXHAUSTIVE_RECOVERY = pytest.mark.skipif(
    not os.environ.get("GRIMOIRE_EXHAUSTIVE_RECOVERY"),
    reason="slow exploration: set GRIMOIRE_EXHAUSTIVE_RECOVERY=1 to run it")


def explore_mode(mode: str) -> dict[str, object]:
    """``explore_crashes`` keywords for ``crashes``, ``errors``, ``recovery-crashes`` or `FAIL_RECOVERY`.

    `FAIL_RECOVERY` faults only with ``Fail`` (the crash plans and their recovery crashes are
    the ``recovery-crashes`` mode's), so it adds exactly the combinations the other two miss."""
    if mode not in ("crashes", "errors", "recovery-crashes", FAIL_RECOVERY):
        raise ValueError(f"unknown exploration mode {mode!r}")
    return {"errors": mode in ("errors", FAIL_RECOVERY),
            "recovery_crashes": mode in ("recovery-crashes", FAIL_RECOVERY),
            "select": (lambda op: {"fail"}) if mode == FAIL_RECOVERY else None}


def distinct_crashes(trace: Sequence[FileOp], select: Callable[[FileOp], object] | None = None, *,
                     interleaves: Sequence[Interleave] = (), torn: bool = False,
                     errors: bool = False) -> Callable[[FileOp], object]:
    """``select`` (every fault, if None) without each ``before`` crash that leaves the same files as an
    ``after`` crash it keeps, for exploring recovery crashes (docs/TESTING.md#ci-gate).

    The crashed process does no more I/O, and nothing but a mutating operation changes a file, so a
    crash before a mutating operation leaves exactly what a crash after the mutating operation before
    it leaves, unless an interleave fires in between: recovering from both, and crashing each of those
    recoveries, only repeats the same runs. ``torn`` and ``errors`` must be what the exploration
    passes, and ``trace`` is its baseline action trace."""
    fired = {nth_match(trace, fault.at) for fault in interleaves}
    chosen: dict[int, frozenset[str]] = {}
    previous: FileOp | None = None
    for op in trace:
        if not op.mutating:
            continue
        wanted = select(op) if select is not None else True
        if wanted is True:
            wanted = {"before", "after"} | ({"torn"} if torn and op.kind == "write_bytes" and op.size else set()) \
                | ({"fail"} if errors else set())
        timings = set(wanted or ())
        if ("before" in timings and previous is not None and "after" in chosen.get(previous.index, ())
                and not any(index is not None and previous.index < index <= op.index for index in fired)):
            timings.discard("before")
        chosen[op.index] = frozenset(timings)
        previous = op
    return lambda op: chosen.get(op.index, frozenset()) or False


_POINT = re.compile(r"action:#(\d+) .* @(before|after|torn|fail)")


def assert_distinct_crashes_repeat(report: CrashReport, select: Callable[[FileOp], object]) -> int:
    """`distinct_crashes`'s claim, checked on an exploration of every crash (recovery not crashed):
    each ``before`` crash that ``select`` drops left the files the ``after`` crash just before it left,
    and recovered to the same. Returns how many it compared."""
    runs = {(int(found.group(1)), found.group(2)): run for run in report.runs[1:]
            for found in [_POINT.fullmatch(run.point)] if found}
    mutating = [op for op in report.runs[0].action_trace if op.mutating]
    compared = 0
    for previous, op in zip(mutating, mutating[1:]):
        dropped = (op.index, "before")
        if dropped in runs and "before" not in (select(op) or ()):
            kept = runs[(previous.index, "after")]
            assert "after" in (select(previous) or ()), f"no kept crash repeats {runs[dropped].point}"
            assert (runs[dropped].after_meddle, runs[dropped].recovered) == (kept.after_meddle, kept.recovered), \
                f"{runs[dropped].point} left other files than {kept.point}"
            compared += 1
    return compared


def distinct_recovery_crashes(*, torn: bool = False) -> Callable[[FileOp], object]:
    """A ``recovery_select`` for the same reason as `distinct_crashes`: a crash of the recovery before
    its first mutating operation leaves the files whose recovery the plan itself checks, and before any
    later one what a crash after the mutating operation before it leaves (nothing interleaves there)."""
    timings = frozenset({"after", "torn"} if torn else {"after"})
    return lambda op: timings


__all__ = [
    "ADDON", "DELETE_SCOPES", "EXHAUSTIVE_RECOVERY", "FAIL_RECOVERY", "HARBOR", "LinkSpec", "MANIFEST_PATH", "OWNER",
    "R1_PATH", "WREN", "WREN_A", "WREN_A_PATH", "WREN_B", "WREN_B_PATH", "WREN_R1", "WREN_R2",
    "assert_distinct_crashes_repeat", "authorizer", "delete_held", "distinct_crashes", "distinct_recovery_crashes",
    "drop_empty_dirs", "explore_mode", "harbor_manifest", "instance", "make_dir_link", "new_library", "owner_handle", "seed",
    "shared_locker", "wren",
]
