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
commits of a sync, crash just before its commit point (the journal replace; it did not happen),
just after it (it happened) and just after its first publish (it happened, recovery finishes it),
and inject an I/O error at its commit point and at its first publish.

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
from grimoire.sync import sync, sync_until_quiet
from grimoire.testing import (
    CrashScenario, FileOp, Interleave, Invariant, explore_crashes, idempotent_recovery, no_residue,
    preserves_interleave, preserves_meddle, run_plans,
)
from grimoire.testing.fileops import snapshot

import pytest

from grimoire.core.identity import InstanceRef

from store_helpers import WREN_R1, drop_empty_dirs, instance
from sync_helpers import (
    DESKTOP, PATHS, PHONE, WREN_A, WREN_B, chat_record, edit_instance, handles, make_pair, new_revision, put,
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
    if outcome.recovered == outcome.completed:
        return None
    keys = sorted(key for key in set(outcome.recovered) | set(outcome.completed)
                  if outcome.recovered.get(key, "absent") != outcome.completed.get(key, "absent"))
    return f"differs from the uninterrupted sync at {keys[:4]}"


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
    """Per commit: before/after its commit point and after its first publish (errors at both)."""
    chosen: dict[int, frozenset[str]] = {}
    for op in trace:
        if not _is_journal_replace(op):
            continue
        chosen[op.index] = frozenset({"before", "after", "fail"})
        publish = next((later for later in trace[op.index + 1:]
                        if later.kind == "replace" and "/.txn/" in later.path), None)
        if publish is not None:
            chosen[publish.index] = frozenset({"after", "fail"})
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
    assert report.points_explored == sum(len(timings - {"fail"}) for timings in points.values()) == 18


def test_s9_an_io_error_at_any_commit_boundary_converges(tmp_path):
    points = commit_boundaries(_baseline_trace(quiet=False))
    report = explore_crashes(_scenario(S9, quiet=False), tmp_path, errors=True, torn=False,
                             select=_select(points, {"fail"}))
    report.assert_ok()
    assert report.points_explored == 12


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
