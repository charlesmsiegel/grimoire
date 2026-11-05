"""Sync under the 006 fault harness (019 S9-S11): crashes, torn writes, errors, interleaves, meddles.

Two replica roots (``desktop/`` and ``phone/``) live in one harness root. The action is
``sync_until_quiet``; recovery is what a restarted app does: reopen both libraries (010 recovery)
and sync once.

**Run budget (step 0, measured on Linux):** one sync of this fixture is about 800 file operations,
under 80 of them mutating, in six 010 commits; a full harness run (setup, action, two recoveries that each
sync) takes about 0.5 s, so exploring every point at every timing would take minutes per test and
far longer on Windows runners. The store's own tests already crash, tear and fail every operation
inside a commit (``test_store_recovery``, ``test_store_replica``) and prove that any crash inside
one commit resolves, at recovery, to "the commit happened" or "it did not". What sync adds is
*which commits happened*, so these tests use a **commit-outcome reduction**: for each of the six
commits of a sync, crash just before its commit point (the journal replace; it did not happen)
and just after it (it happened; a crash after its first publish resolves the same way, so it is not
explored again), and inject an I/O error at its commit point and at its first publish (a live error
mid-settle is a different case).

Interleaves use an **observation-gap reduction**: two positions for an edit of an existing file F
behave the same unless an operation between them observes F. An edit of an existing file changes
no directory listing, so only ``read_bytes``/``exists`` of F and mutations of F observe it; one
position per gap is explored (F's own ``replace`` excepted: 010's pinned R7 window). Every edit
target is explored on both devices, because the apply order is not symmetric even though the
decisions are. Not explored here: interleaves that create or delete a file.
"""

from __future__ import annotations

import functools
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from grimoire.store import LibraryStore, ProcessLocker, etag
from grimoire.sync import identify, sync, sync_until_quiet
from grimoire.testing import (
    CrashScenario, FileOp, Interleave, Invariant, explore_crashes, idempotent_recovery, no_residue,
    preserves_interleave, preserves_meddle, run_plans,
)
from grimoire.testing.fileops import snapshot

import pytest

from grimoire.core.identity import InstanceRef

from store_helpers import EXHAUSTIVE_RECOVERY, WREN_R1, drop_empty_dirs, instance, owner_handle
from sync_helpers import (
    DESKTOP, PATHS, PHONE, WREN_A, WREN_B, chat_record, converged, edit_instance, handle, handles, make_pair,
    new_revision, put,
)

DEVICES = (DESKTOP, PHONE)
RESIDUE = no_residue(*(f"{device}/.txn/{name}" for device in DEVICES for name in ("*.new", "journal.json")))


@functools.cache
def _fixture() -> tuple[tuple[tuple[str, bytes | None], ...], tuple[tuple[str, bytes], ...]]:
    """(the diverged two-library tree, the canonical files both devices agreed on before diverging)."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        desktop, phone = make_pair(root)
        agreed = tuple(sorted(desktop.scan().files.items()))
        edit_instance(desktop, WREN_A, 9)             # copied to the phone
        put(desktop, instance(InstanceRef("c5", "character", "zed"), WREN_R1), None)  # a new folder there
        edit_instance(phone, WREN_B, 5)               # copied to the desktop
        new_revision(phone, "r2", "a captain")        # a source unit: revision + head move
        put(desktop, chat_record(["character:wren", "character:mara"]))  # the conflict
        put(phone, chat_record(["character:wren"], world="world:harbor"))
        desktop.close(), phone.close()
        return tuple(snapshot(root).items()), agreed


def _setup(ops, root):
    for key, data in _fixture()[0]:
        if data is None:
            ops.make_dirs(root / key)
        else:
            ops.write_bytes(root / key, data)


def _with_both(function):
    """Open both libraries (each open is 010 recovery), run ``function``, always release both locks."""
    def phase(ops, root, locker):
        desktop = LibraryStore.open(root / DESKTOP, ops=ops, locker=locker)
        try:
            phone = LibraryStore.open(root / PHONE, ops=ops, locker=locker)
            try:
                function(desktop, phone)
            finally:
                phone.close()
        finally:
            desktop.close()
    return phase


_until_quiet = _with_both(lambda desktop, phone: sync_until_quiet(desktop, phone, handles()))
_once = _with_both(lambda desktop, phone: sync(desktop, phone, handles()))
_recover = _once


def _scenario(invariants, *, meddle=None, name="sync", quiet=True):
    """``quiet``: the action syncs until a sync writes nothing (needed when an interleave makes the
    first sync stop). Without external edits one sync is already followed by a quiet one, so the
    crash-only S9 scenarios use a single sync: the same fault points at a fraction of the cost."""
    locker = ProcessLocker()  # one per scenario: a lock leaked by a crashed phase is visible
    action = _until_quiet if quiet else _once
    return CrashScenario(name, _setup, lambda ops, root: action(ops, root, locker),
                         lambda ops, root: _recover(ops, root, locker), invariants,
                         meddle=meddle, normalize=drop_empty_dirs)


# -- scenario invariants --------------------------------------------------------------------------

def _canonical(tree, device):
    prefix = f"{device}/"
    return {key[len(prefix):]: value for key, value in tree.items()
            if key.startswith(prefix) and value is not None and key.split("/")[1] in ("sources", "campaigns")}


def _converges(outcome):
    """S9: after any crash, a restart and one sync reach exactly what an uninterrupted sync reaches."""
    return converged(outcome.recovered, outcome.completed)


def _keeps_both(outcome):
    """Wherever the devices still hold different present versions, each keeps the other's."""
    desktop, phone = _canonical(outcome.recovered, DESKTOP), _canonical(outcome.recovered, PHONE)
    kept = {device: {value for key, value in outcome.recovered.items()
                     if key.startswith(f"{device}/.conflicts/") and value is not None} for device in DEVICES}
    for path in sorted(set(desktop) & set(phone)):
        if desktop[path] != phone[path]:
            if phone[path] not in kept[DESKTOP] or desktop[path] not in kept[PHONE]:
                return f"{path} differs but is not kept on both devices"
    return None


def _previous(data: bytes) -> str | None:
    try:
        value = json.loads(data).get("previous")
    except (ValueError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def _versions_survive(outcome):
    """No version is lost unless a surviving version descends from it.

    The candidates are every canonical file in ``before``, ``after_meddle`` and what interleaves
    wrote. A version is *superseded* when a surviving version (canonical or kept, on either
    device) names it, directly or through other candidates, as its ``previous``: that is an edit
    of it, as a human or the store makes one. The pre-divergence agreed versions are superseded
    by construction (the fixture's own edits).
    """
    agreed = set(dict(_fixture()[1]).values())
    candidates = {value for tree in (outcome.before, outcome.after_meddle, outcome.interleaved)
                  for key, value in tree.items()
                  if value is not None and key.split("/")[1:2] in (["sources"], ["campaigns"])}
    survivors = {value for value in outcome.recovered.values() if value is not None}
    by_etag = {etag(value): value for value in candidates | survivors}
    superseded, frontier = set(), [_previous(value) for value in survivors]
    while frontier:
        tag = frontier.pop()
        if tag is None or tag in superseded:
            continue
        superseded.add(tag)
        if tag in by_etag:
            frontier.append(_previous(by_etag[tag]))
    for value in sorted(candidates - survivors - agreed):
        if etag(value) not in superseded:
            return f"a version was lost: {value[:60]!r}"
    return None


CONVERGES = Invariant("converges", _converges)
KEEPS_BOTH = Invariant("keeps_both", _keeps_both)
VERSIONS_SURVIVE = Invariant("versions_survive", _versions_survive)


# -- point selection --------------------------------------------------------------------------------

def _is_journal_replace(op: FileOp) -> bool:
    return op.kind == "replace" and (op.target or "").endswith("/.txn/journal.json")


def commit_boundaries(trace) -> dict[int, frozenset[str]]:
    """Per commit: crashes before/after its commit point; I/O errors there and at its first publish."""
    chosen: dict[int, frozenset[str]] = {}
    for op in trace:
        if not _is_journal_replace(op):
            continue
        chosen[op.index] = frozenset({"before", "after", "fail"})
        publish = next((later for later in trace[op.index + 1:]
                        if later.kind == "replace" and "/.txn/" in later.path), None)
        if publish is not None:  # a crash there resolves like "after" the commit point: only an error is new
            chosen[publish.index] = frozenset({"fail"})
    return chosen


def _select(points: dict[int, frozenset[str]], timings: set[str]) -> Callable[[FileOp], object]:
    return lambda op: points.get(op.index, frozenset()) & timings


def _recovery_boundary(op: FileOp) -> object:
    return {"before", "after"} if _is_journal_replace(op) else False


@functools.cache
def _baseline_trace(quiet: bool = True) -> tuple[FileOp, ...]:
    with tempfile.TemporaryDirectory() as folder:
        report = run_plans(_scenario((), quiet=quiet), [], Path(folder))
        report.assert_ok()
        return report.runs[0].action_trace


# -- S9 crash convergence ------------------------------------------------------------------------------

S9 = (CONVERGES, VERSIONS_SURVIVE, KEEPS_BOTH, idempotent_recovery(), RESIDUE)


def test_s9_the_baseline_sync_does_everything_once():
    trace = _baseline_trace(quiet=False)
    commits = [op for op in trace if _is_journal_replace(op)]
    assert len(commits) == 6  # data(a), data(b), archive(b), archive(a), pending(b), pending(a)
    assert [op.target.split("/")[0] for op in commits] == [DESKTOP, PHONE, PHONE, DESKTOP, PHONE, DESKTOP]
    assert sum(op.mutating for op in trace) < 80


def test_s9_a_crash_at_any_commit_boundary_converges(tmp_path):
    points = commit_boundaries(_baseline_trace(quiet=False))
    report = explore_crashes(_scenario(S9, quiet=False), tmp_path, torn=False, select=_select(points, {"before", "after"}))
    report.assert_ok()
    assert report.points_explored == sum(len(timings - {"fail"}) for timings in points.values()) == 12


def test_s9_an_io_error_at_any_commit_boundary_converges(tmp_path):
    points = commit_boundaries(_baseline_trace(quiet=False))
    report = explore_crashes(_scenario(S9, quiet=False), tmp_path, errors=True, torn=False,
                             select=_select(points, {"fail"}))
    report.assert_ok()
    assert report.points_explored == 12


@EXHAUSTIVE_RECOVERY  # about half a minute on Linux
def test_s9_a_crash_while_recovering_from_an_io_error_still_converges(tmp_path):
    """An I/O error at each commit boundary, then a crash before and after each commit point of the
    sync that recovers from it (the recovery after a crash is the test below)."""
    points = commit_boundaries(_baseline_trace(quiet=False))
    report = explore_crashes(_scenario(S9, quiet=False), tmp_path, errors=True, torn=False, recovery_crashes=True,
                             select=_select(points, {"fail"}), recovery_select=_recovery_boundary)
    report.assert_ok()
    assert any(" > recover:" in run.point and "@fail > " in run.point for run in report.runs)


def test_s9_a_crash_while_recovering_still_converges(tmp_path):
    trace = _baseline_trace(quiet=False)
    commits = [op.index for op in trace if _is_journal_replace(op)]
    # crash right after the phone's data commit point (the desktop's data already published), and
    # right before the desktop's archive commit point (the phone's archive already written); then
    # crash the recovering sync before and after each of its own commit points
    chosen = {commits[1]: frozenset({"after"}), commits[3]: frozenset({"before"})}
    report = explore_crashes(_scenario(S9, quiet=False), tmp_path, torn=False, recovery_crashes=True,
                             select=_select(chosen, {"before", "after"}), recovery_select=_recovery_boundary)
    report.assert_ok()
    assert any(run.recover_trace for run in report.runs[1:])
    assert report.points_explored >= 8


# -- S9u: crash safety when the uncertainty lives only in an archive (brutal-review round 3) --------

TABLET = "tablet"  # the desktop, re-identified: its old-pair archive is ignored, the phone has none


@functools.cache
def _uncertain_fixture() -> tuple[tuple[str, bytes | None], ...]:
    """Every base is ``uncertain``, and only the archives say so; the desktop deleted wren-b."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        desktop, phone = make_pair(root)
        identify(desktop, TABLET, replace=True)
        edit_instance(desktop, WREN_A, 9)
        (desktop.root / PATHS["wren_b"]).unlink()
        desktop.close(), phone.close()
        return tuple(snapshot(root).items())


def _uncertain_setup(ops, root):
    for key, data in _uncertain_fixture():
        if data is None:
            ops.make_dirs(root / key)
        else:
            ops.write_bytes(root / key, data)


def _never_recreated(outcome):
    """The deletion is never undone, and the edit never copied, whatever crashed where."""
    if outcome.recovered.get(f"{DESKTOP}/{PATHS['wren_b']}") is not None:
        return "wren-b, deleted on the desktop, was re-created"
    desktop, phone = (outcome.recovered.get(f"{device}/{PATHS['wren_a']}") for device in DEVICES)
    if desktop == phone:
        return "wren-a was copied although no ancestor was proven"
    return None


_TABLET_HANDLES = {PHONE: handle(), TABLET: handle()}
_uncertain_once = _with_both(lambda desktop, phone: sync(desktop, phone, _TABLET_HANDLES))
_uncertain_recover = _with_both(lambda desktop, phone: [sync(desktop, phone, _TABLET_HANDLES) for _ in range(2)])
S9U = (CONVERGES, Invariant("never_recreated", _never_recreated), idempotent_recovery(), RESIDUE)


def _uncertain_scenario():
    locker = ProcessLocker()
    return CrashScenario("sync-uncertain", _uncertain_setup, lambda ops, root: _uncertain_once(ops, root, locker),
                         lambda ops, root: _uncertain_recover(ops, root, locker), S9U, normalize=drop_empty_dirs)


@functools.cache
def _uncertain_trace() -> tuple[FileOp, ...]:
    with tempfile.TemporaryDirectory() as folder:
        report = run_plans(_uncertain_scenario(), [], Path(folder))
        report.assert_ok()
        return report.runs[0].action_trace


def test_s9u_a_crash_or_error_at_any_commit_boundary_never_forgets_archive_borne_uncertainty(tmp_path):
    points = commit_boundaries(_uncertain_trace())
    commits = [op for op in _uncertain_trace() if _is_journal_replace(op)]
    # no copies, but each device's data commit records the uncertainty before any archive is rewritten
    # marker(a), marker(b), archive(b), archive(a); the markers already say it all, so the last step
    # writes only (028) each device's record of the versions it now keeps
    assert len(commits) == 6
    crashes = explore_crashes(_uncertain_scenario(), tmp_path / "crash", torn=False,
                              select=_select(points, {"before", "after"}))
    crashes.assert_ok()
    errors = explore_crashes(_uncertain_scenario(), tmp_path / "error", errors=True, torn=False,
                             select=_select(points, {"fail"}))
    errors.assert_ok()
    assert crashes.points_explored >= 8 and errors.points_explored >= 4


# -- S10 edits during sync -------------------------------------------------------------------------------

def _edit(target: str, data_of: Callable[[bytes], bytes]):
    def action(ops, root):
        ops.write_bytes(root / target, data_of(ops.read_bytes(root / target)))
    return action


def _human(data: bytes) -> bytes:
    """An edit of ``data`` that records its lineage, as the store's own ``put`` would."""
    document = json.loads(data)
    document["payload"] = {**document["payload"], "edited": "by a human"}
    document["previous"] = etag(data)
    return (json.dumps(document, indent=1) + "\n").encode()


def _gap_positions(trace, target: str, *, after: int | None = None, before: int | None = None) -> list[int]:
    """One interleave position per gap between operations that observe ``target`` (R7 excluded)."""
    observing = [op.index for op in trace if op.path == target or op.target == target]
    own_replace = {op.index for op in trace if op.kind == "replace" and op.target == target}
    positions = sorted(set(observing) - own_replace)
    return [index for index in positions
            if (after is None or index > after) and (before is None or index <= before)]


def _s10_targets(trace):
    old_phone_wren_a = dict(_fixture()[1])[PATHS["wren_a"]]
    phone_wren_a = f"{PHONE}/{PATHS['wren_a']}"
    publish = next(op.index for op in trace if op.kind == "replace" and op.target == phone_wren_a)
    verified = next(op.index for op in trace[publish + 1:] if op.kind == "read_bytes" and op.path == phone_wren_a)
    return {
        "phone copy target": (phone_wren_a, _human, None, None),
        "phone copy origin": (f"{PHONE}/{PATHS['wren_b']}", _human, None, None),
        "phone conflict side": (f"{PHONE}/{PATHS['chat']}", _human, None, None),
        "desktop copy target": (f"{DESKTOP}/{PATHS['wren_b']}", _human, None, None),
        "desktop copy origin": (f"{DESKTOP}/{PATHS['wren_a']}", _human, None, None),
        "desktop conflict side": (f"{DESKTOP}/{PATHS['chat']}", _human, None, None),
        # ABA: the human restores the exact pre-sync bytes after the copy landed and before the
        # sync verified it. (Once verified, both devices held the copy: a later restore is an
        # ordinary edit and is synchronized as one. The crash variant is test_f10.)
        "aba restore": (phone_wren_a, lambda data: old_phone_wren_a, publish, verified),
    }


S10 = (preserves_interleave(), VERSIONS_SURVIVE, KEEPS_BOTH, idempotent_recovery(), RESIDUE)


def _s10(tmp_path, names):
    trace = _baseline_trace()
    plans, total = [], 0
    for name in names:
        target, data_of, after, before = _s10_targets(trace)[name]
        positions = _gap_positions(trace, target, after=after, before=before)
        assert positions, name
        plans += [[Interleave(index, _edit(target, data_of))] for index in positions]
        total += len(positions)
    report = run_plans(_scenario(S10), plans, tmp_path)
    report.assert_ok()
    assert len(report.runs) == total + 1


def test_s10_external_edits_of_a_copy_target_and_an_aba_restore_are_kept(tmp_path):
    _s10(tmp_path, ["phone copy target", "desktop copy target", "aba restore"])


def test_s10_external_edits_of_a_copy_origin_and_a_conflict_side_are_kept(tmp_path):
    _s10(tmp_path, ["desktop copy origin", "phone copy origin", "phone conflict side", "desktop conflict side"])


def _meddle(ops, root):
    target = root / PHONE / PATHS["wren_b"]
    ops.write_bytes(target, _human(ops.read_bytes(target)).replace(b"by a human", b"while the app was down"))


def test_s10_an_interleave_and_a_meddle_under_exploration_keep_both(tmp_path):
    trace = _baseline_trace()
    phone_commit = [op.index for op in trace if _is_journal_replace(op)][1]
    edit = Interleave(phone_commit, _edit(f"{PHONE}/{PATHS['wren_a']}", _human))
    scenario = _scenario(S10 + (preserves_meddle(),), meddle=_meddle)
    raced = run_plans(scenario, [[edit]], tmp_path / "trace").runs[1]  # the interleaved baseline's trace
    assert "WriteConflict" not in (raced.action_error or "") and raced.action_outcome == "returned"
    points = commit_boundaries(raced.action_trace)
    report = explore_crashes(
        scenario, tmp_path / "explore", interleaves=[edit],
        select=_select(points, {"before"}))
    report.assert_ok()
    assert any(run.meddled_paths for run in report.runs)
    assert report.points_explored == len([op for op in raced.action_trace if _is_journal_replace(op)]) >= 7


# -- S11 integrity ---------------------------------------------------------------------------------------

def test_s11_a_file_changed_after_publication_stops_the_sync_before_its_archive(tmp_path):
    trace = _baseline_trace()
    target = f"{PHONE}/{PATHS['wren_a']}"
    publish = next(op.index for op in trace if op.kind == "replace" and op.target == target)
    verify = next(op.index for op in trace[publish + 1:] if op.kind == "read_bytes" and op.path == target)
    report = run_plans(_scenario(S10), [[Interleave(verify, _edit(target, _human))]], tmp_path)
    report.assert_ok()
    run = report.runs[1]
    assert run.action_outcome == "returned"  # sync_until_quiet retried after the interruption
    edited = run.interleaved[target]
    assert run.recovered[target] == edited  # verification failed: no archive claimed the copy landed
    kept_on_desktop = {value for key, value in run.recovered.items() if key.startswith(f"{DESKTOP}/.conflicts/")}
    assert edited in kept_on_desktop  # the retry saw an uncertain base: a keep-both conflict


# -- unreduced, opt-in -----------------------------------------------------------------------

EXHAUSTIVE = pytest.mark.skipif(not os.environ.get("GRIMOIRE_EXHAUSTIVE_SYNC"),
                                reason="unreduced exploration: set GRIMOIRE_EXHAUSTIVE_SYNC=1 (minutes)")


@EXHAUSTIVE
def test_exhaustive_every_mutating_operation_crashed_torn_and_failed(tmp_path):
    """No commit-outcome reduction: every mutating operation, every timing (about 220 points)."""
    report = explore_crashes(_scenario(S9, quiet=False), tmp_path, errors=True)
    report.assert_ok()
    assert report.points_explored > 200


@EXHAUSTIVE
def test_exhaustive_an_edit_at_every_operation(tmp_path):
    """No observation-gap reduction: an edit before every operation except the file's own replace."""
    trace = _baseline_trace()
    for name in ("phone copy target", "desktop conflict side"):
        target, data_of, _, _ = _s10_targets(trace)[name]
        own = {op.index for op in trace if op.kind == "replace" and op.target == target}
        plans = [[Interleave(op.index, _edit(target, data_of))] for op in trace if op.index not in own]
        run_plans(_scenario(S10), plans, tmp_path / name.replace(" ", "-")).assert_ok()


def test_an_aborted_copy_is_dropped_never_parked_as_a_kept_version(tmp_path):
    """Sync passes ``regenerable=True``: when 010 keeps a human's racing edit, the copy is dropped, not kept."""
    from grimoire.sync import SyncInterrupted
    from grimoire.testing import RootedFileOps
    from sync_helpers import kept
    target = f"{PHONE}/{PATHS['wren_a']}"
    trace = _baseline_trace(quiet=False)
    commit = [op.index for op in trace if _is_journal_replace(op)][1]  # data(phone)'s commit point
    guard = next(op.index for op in trace[commit + 1:] if op.kind == "read_bytes" and op.path == target)
    root = tmp_path / "root"
    root.mkdir()
    _setup(RootedFileOps(root), root)
    copied = (root / DESKTOP / PATHS["wren_a"]).read_bytes()  # the desktop's version the sync copies
    ops, locker = RootedFileOps(root, plan=[Interleave(guard, _edit(target, _human))]), ProcessLocker()
    desktop = LibraryStore.open(root / DESKTOP, ops=ops, locker=locker)
    phone = LibraryStore.open(root / PHONE, ops=ops, locker=locker)
    with pytest.raises(SyncInterrupted):  # 010 kept the human's file (WriteConflict)
        sync(desktop, phone, handles())
    assert ops.fired
    desktop.close()
    phone.close()
    desktop, phone = LibraryStore.open(root / DESKTOP), LibraryStore.open(root / PHONE)
    try:
        edit_instance(desktop, WREN_A, 2)  # the desktop moves on before the next sync
        sync(desktop, phone, handles())
        assert copied not in kept(phone)
    finally:
        desktop.close()
        phone.close()


# -- resolve under the harness (brutal-review round 10): every commit boundary ---------------------------

R9 = PATHS["r1"].replace("r1.json", "r9.json")


@functools.cache
def _resolve_fixture(kind: str):
    """Two synced devices whose ``path`` differs: an instance edit, or a same-id definition revision."""
    from grimoire.core.identity import SourceRef
    from grimoire.store import RecordEnvelope, SourceManifest
    from sync_helpers import handle
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        desktop, phone = make_pair(root)
        if kind == "instance":
            edit_instance(desktop, WREN_A, 9)
            edit_instance(phone, WREN_A, 4)
            path = PATHS["wren_a"]
        else:
            for store, bio in ((desktop, "captain"), (phone, "pirate")):
                txn = store.transaction()
                txn.put(RecordEnvelope(SourceRef("world:harbor", "character", "wren", "r9"), {"bio": bio}), None)
                current = store.read_source("world:harbor")
                txn.put(SourceManifest(current.manifest.source, current.manifest.owner, current.manifest.title,
                                       {"character/wren": "r1"}, current.manifest.links,
                                       extra=current.manifest.extra), current.etag)
                txn.commit(handle())
            path = R9
        sync(desktop, phone, handles())
        mine, theirs = (desktop.root / path).read_bytes(), (phone.root / path).read_bytes()
        desktop.close(), phone.close()
        return tuple(snapshot(root).items()), path, mine, theirs


def _resolve_scenario(kind: str):
    from grimoire.sync import resolve
    tree, path, mine, theirs = _resolve_fixture(kind)
    locker = ProcessLocker()

    def setup(ops, root):
        for key, data in tree:
            ops.make_dirs(root / key) if data is None else ops.write_bytes(root / key, data)

    # toward the phone's version: the desktop receives it and must keep its own (a hold)
    action = _with_both(lambda desktop, phone: resolve(desktop, phone, path, etag(theirs),
                                                       {DESKTOP: etag(mine), PHONE: etag(theirs)}, handles()))

    def survives(outcome):
        if not any(value == mine for value in outcome.recovered.values() if value is not None):
            return "the desktop's replaced version is gone from every device"
        if any(outcome.recovered.get(f"{device}/{path}") is None for device in DEVICES):
            return f"{path} vanished"
        return None
    return CrashScenario(f"resolve-{kind}", setup, lambda ops, root: action(ops, root, locker),
                         lambda ops, root: _recover(ops, root, locker),
                         (Invariant("replaced_version_survives", survives), idempotent_recovery(), RESIDUE),
                         normalize=drop_empty_dirs)


@EXHAUSTIVE_RECOVERY
def test_s9u_a_crash_while_recovering_from_an_io_error_never_forgets_uncertainty(tmp_path):
    points = commit_boundaries(_uncertain_trace())
    report = explore_crashes(_uncertain_scenario(), tmp_path, errors=True, torn=False, recovery_crashes=True,
                             select=_select(points, {"fail"}), recovery_select=_recovery_boundary)
    report.assert_ok()
    assert any(run.recover_trace for run in report.runs[1:])


@EXHAUSTIVE_RECOVERY
@pytest.mark.parametrize("kind", ["instance", "revision"])
def test_resolve_survives_a_crash_while_recovering_from_an_io_error(tmp_path, kind):
    with tempfile.TemporaryDirectory() as folder:
        baseline = run_plans(_resolve_scenario(kind), [], Path(folder))
        baseline.assert_ok()
        trace = baseline.runs[0].action_trace
    report = explore_crashes(_resolve_scenario(kind), tmp_path, errors=True, torn=False, recovery_crashes=True,
                             select=_select(commit_boundaries(trace), {"fail"}), recovery_select=_recovery_boundary)
    report.assert_ok()
    assert any(run.recover_trace for run in report.runs[1:])


@pytest.mark.parametrize("kind", ["instance", "revision"])
def test_resolve_survives_a_crash_or_error_at_every_commit_boundary(tmp_path, kind):
    with tempfile.TemporaryDirectory() as folder:
        baseline = run_plans(_resolve_scenario(kind), [], Path(folder))
        baseline.assert_ok()
        trace = baseline.runs[0].action_trace
    points = commit_boundaries(trace)
    report = explore_crashes(_resolve_scenario(kind), tmp_path, errors=True, torn=False,
                             select=_select(points, {"before", "after", "fail"}))
    report.assert_ok()
    assert report.points_explored >= 8  # data, archive(b), archive(a), pending(b), pending(a)


# -- C2: a sync copying 018 art (one blob, one image object, one placement) under the harness ------------

_ART_HANDLES = {device: owner_handle("sources", "campaigns", "assets", "kept") for device in DEVICES}


@functools.cache
def _art_fixture() -> tuple[tuple[str, bytes | None], ...]:
    """The desktop adds a campaign picture and places it; the phone (synced before) lacks all three."""
    from grimoire.images import Placement
    from grimoire.imagestore import ImageLibrary
    from image_fixtures import PNG, WREN_A as ART_SUBJECT, seed as seed_art
    from store_helpers import OWNER
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        desktop, phone = LibraryStore.create(root / DESKTOP), LibraryStore.create(root / PHONE)
        seed_art(desktop)
        identify(desktop, DESKTOP)
        identify(phone, PHONE)
        assert sync_until_quiet(desktop, phone, _ART_HANDLES).conflicts == ()
        batch = ImageLibrary(desktop, _ART_HANDLES[DESKTOP], owner=OWNER).change()
        ref = batch.add_image("campaign:c1", "wren", PNG)
        batch.place(ART_SUBJECT, "avatar", Placement("set", "avatar", ref), None)
        batch.commit()
        desktop.close(), phone.close()
        return tuple(snapshot(root).items())


def _art_setup(ops, root):
    for key, data in _art_fixture():
        if data is None:
            ops.make_dirs(root / key)
        else:
            ops.write_bytes(root / key, data)


def _art_whole(outcome):
    """Art never lands by halves: a placement's image and blob are there, and a blob holds its name's bytes."""
    import hashlib
    from grimoire.store.layout import classify
    for key, value in outcome.recovered.items():
        device, _, path = key.partition("/")
        found = classify(tuple(path.split("/")))
        if value is None or found is None:
            continue
        if found[0] == "blob" and hashlib.sha256(value).hexdigest() != found[1][0]:
            return f"{key} does not hold the bytes its name says"
        if found[0] == "placement" and (outcome.recovered.get(f"{device}/campaigns/c1/images/wren.json") is None or not [
                other for other in outcome.recovered if other.startswith(f"{device}/assets/blobs/")]):
            return f"{key} landed without its image or blob"
    return None


_art_once = _with_both(lambda desktop, phone: sync(desktop, phone, _ART_HANDLES))


def _art_scenario():
    locker = ProcessLocker()
    return CrashScenario("sync-art", _art_setup, lambda ops, root: _art_once(ops, root, locker),
                         lambda ops, root: _art_once(ops, root, locker),
                         (CONVERGES, Invariant("art_whole", _art_whole), idempotent_recovery(), RESIDUE),
                         normalize=drop_empty_dirs)


def test_c2_a_crash_or_error_at_any_commit_boundary_of_an_art_copy_converges(tmp_path):
    """The blob is loaded lazily inside the data commit (about 5 s on Linux: not behind an opt-in)."""
    with tempfile.TemporaryDirectory() as folder:
        baseline = run_plans(_art_scenario(), [], Path(folder))
        baseline.assert_ok()
        trace = baseline.runs[0].action_trace
    points = commit_boundaries(trace)
    assert len([op for op in trace if _is_journal_replace(op)]) >= 3  # data(phone), archives, markers
    report = explore_crashes(_art_scenario(), tmp_path, errors=True, torn=False,
                             select=_select(points, {"before", "after", "fail"}))
    report.assert_ok()
    assert report.points_explored >= 9
