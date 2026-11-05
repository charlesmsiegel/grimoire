"""Accepted-effect writes survive every crash, I/O error and recovery crash (Sprint 021, F11).

``accept`` writes the accepted-event file and its relation changes in one transaction; ``revoke``
(also partial) rewrites the event and retracts/reopens relations; 017's ``reopen`` rewrites one
relation. Under the 006 fault harness, recovery always yields the state before the action or the
completed one, with no residue, and recovering again changes nothing. Clocks, ids and handles are
rebuilt inside every run, so each action is deterministic; instances and chats are never touched.
"""

from __future__ import annotations

import functools
import tempfile
from pathlib import Path

import pytest

from grimoire.core import DIRECTOR
from grimoire.core.identity import AcceptedRef, RelationRef
from grimoire.graphstore import RelationLibrary
from grimoire.store import LibraryStore, ProcessLocker
from grimoire.testing import (
    CrashScenario, Invariant, Target, atomic_outcome, completes_after, explore_crashes, idempotent_recovery,
    no_residue, untouched,
)
from grimoire.testing.fileops import snapshot

from chat_fixtures import Clock
from effects_fixtures import C, CONTROLS, Handles, contribution, effects_library, give, seed
from graph_fixtures import seed_campaign
from holdings_fixtures import holdings_library
from parts_fixtures import KNIFE, ORIN, hop, seed_holdings
from store_helpers import EXHAUSTIVE_RECOVERY, FAIL_RECOVERY, OWNER, drop_empty_dirs, explore_mode

LIB = "lib"
JOURNAL = Target("replace", target="lib/.txn/journal.json")
RESIDUE = no_residue("lib/.txn/*.new", "lib/.txn/journal.json")
EVENT = AcceptedRef(C, "e-1")
SEED_KEY1 = RelationRef(C, "r-seed-1")


def _accept(store: LibraryStore, handles: Handles) -> None:
    proposal = give()
    effects_library(store, handles).accept(proposal, controls=CONTROLS, contributions=[contribution()],
                                           endorsements=[handles.endorse(proposal)]).commit()


def _hop(store: LibraryStore, handles: Handles) -> None:
    effects_library(store, handles).accept(hop(), controls=CONTROLS).commit()


def _break(store: LibraryStore, handles: Handles) -> None:
    relations = RelationLibrary(store, handles.app, clock=Clock(), new_id=lambda prefix: "r-hand", owner=OWNER)
    relations.reopen(SEED_KEY1, provenance="editor", expected=relations.read(SEED_KEY1)[1]).commit()


@functools.cache
def _template(stage: str) -> tuple[tuple[str, bytes | None], ...]:
    """``base``: the seeded campaign; ``accepted``: plus e-1; ``broken``: plus a hand reopen of e-1's ending;
    ``holders``: the campaign with Orin's fuel, Wren's coins and Tam's knife; ``hopped``: plus Orin's hop as e-1."""
    with tempfile.TemporaryDirectory() as folder:
        with LibraryStore.create(Path(folder) / LIB, locker=ProcessLocker()) as store:
            handles = Handles()
            seed(store, handles)
            if stage in ("holders", "hopped"):
                seed_campaign(store, (ORIN, KNIFE))
                seed_holdings(store, holdings_library(store))
                if stage == "hopped":
                    _hop(store, handles)
            elif stage != "base":
                _accept(store, handles)
            if stage == "broken":
                _break(store, handles)
        return tuple(snapshot(folder).items())


def _setup(stage):
    def setup(ops, root):
        for key, data in _template(stage):
            if data is None:
                ops.make_dirs(root / key)
            else:
                ops.write_bytes(root / key, data)
    return setup


def accept(ops, root, locker):
    with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
        _accept(store, Handles())


def _revoke(ops, root, locker, partial):
    with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
        handles = Handles()
        library = effects_library(store, handles)
        _, etag = library.read(EVENT, requested_by=DIRECTOR)
        library.revoke(EVENT, reason="rewind", expected=etag, endorsements=[handles.undo(EVENT)],
                       partial=partial).commit()


def revoke(ops, root, locker):
    _revoke(ops, root, locker, False)


def revoke_partial(ops, root, locker):
    _revoke(ops, root, locker, True)


def accept_hop(ops, root, locker):
    with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
        _hop(store, Handles())


def revoke_hop(ops, root, locker):
    with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
        library = effects_library(store, Handles())
        _, etag = library.read(EVENT, requested_by=DIRECTOR)
        library.revoke(EVENT, reason="rewind", expected=etag).commit()


def charged_once(fuel: int, retry) -> Invariant:
    """From the recovered state, retrying the action leaves Orin with ``fuel``: the hop is charged once, never twice.

    Atomic recovery leaves only a few distinct states, so each is retried once (the exploration stays in budget)."""
    seen: dict[tuple, str | None] = {}

    def check(outcome) -> str | None:  # type: ignore[no-untyped-def]
        state = tuple(sorted(outcome.recovered.items()))
        if state not in seen:
            seen[state] = _retried(state)
        return seen[state]

    def _retried(state: tuple) -> str | None:
        with tempfile.TemporaryDirectory() as folder:
            for key, data in state:
                path = Path(folder) / key
                if data is None:
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
            with LibraryStore.open(Path(folder) / LIB) as store:
                retry(store)
                found = holdings_library(store).read(ORIN).counts()
        return None if found == {"fuel": fuel} else f"after a retry Orin has {found}, not {fuel} fuel"
    return Invariant("charged_once", check)


def _retry_hop(store: LibraryStore) -> None:
    effects_library(store, Handles()).accept(hop(), controls=CONTROLS).commit()  # a duplicate when it landed


def _retry_revoke(store: LibraryStore) -> None:
    library = effects_library(store, Handles())
    event, etag = library.read(EVENT, requested_by=DIRECTOR)
    if event.active:
        library.revoke(EVENT, reason="rewind", expected=etag).commit()


RETRIES = {accept_hop: charged_once(2, _retry_hop), revoke_hop: charged_once(3, _retry_revoke)}


def reopen(ops, root, locker):
    with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
        _break(store, Handles())


STAGES = {accept: "base", revoke: "accepted", revoke_partial: "broken", reopen: "accepted", accept_hop: "holders",
          revoke_hop: "hopped"}


def scenario(action):
    locker = ProcessLocker()

    def recover(ops, root):
        LibraryStore.open(root / LIB, ops=ops, locker=locker).close()

    return CrashScenario(
        name=f"effect {action.__name__}", setup=_setup(STAGES[action]),
        action=lambda ops, root: action(ops, root, locker), recover=recover,
        invariants=(atomic_outcome(), idempotent_recovery(), RESIDUE, completes_after(JOURNAL),
                    untouched("lib/campaigns/c/instances/*"), untouched("lib/campaigns/c/chats/*"),
                    *((RETRIES[action],) if action in RETRIES else ())),
        normalize=drop_empty_dirs)


@pytest.mark.parametrize("action, mode", [
    (accept, "errors"), (accept, "recovery-crashes"), (revoke, "errors"), (revoke_partial, "errors"),
    (reopen, "errors"), pytest.param(revoke, "recovery-crashes", marks=EXHAUSTIVE_RECOVERY),
    (revoke_partial, "recovery-crashes"),
    (reopen, "recovery-crashes"),
    *(pytest.param(action, FAIL_RECOVERY, marks=EXHAUSTIVE_RECOVERY) for action in (accept, revoke, revoke_partial)),
    (reopen, FAIL_RECOVERY),
    (accept_hop, "errors"), (accept_hop, "recovery-crashes"), (revoke_hop, "errors"),
    (revoke_hop, "recovery-crashes"),
], ids=["accept-errors", "accept-recovery-crashes", "revoke-errors", "revoke-partial-errors", "reopen-errors",
        "revoke-recovery-crashes", "revoke-partial-recovery-crashes", "reopen-recovery-crashes",
        "accept-fail-recovery-crashes", "revoke-fail-recovery-crashes", "revoke-partial-fail-recovery-crashes",
        "reopen-fail-recovery-crashes", "accept-hop-errors", "accept-hop-recovery-crashes", "revoke-hop-errors",
        "revoke-hop-recovery-crashes"])
def test_effect_writes_survive_every_crash_error_and_recovery_crash(tmp_path, action, mode):
    report = explore_crashes(scenario(action), tmp_path, **explore_mode(mode))
    report.assert_ok()
    assert report.points_explored > 10
    if mode != "errors":
        assert any(run.recover_trace for run in report.runs[1:])
