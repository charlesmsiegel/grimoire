"""A SillyTavern chat import survives every crash, I/O error and recovery crash (Sprint 036).

The commit of ``StImporter.plan`` writes the play scope, the chat with its bindings, two new actors
(the character and the persona) and the transcript page as one 107 store transaction. Under the 006
fault harness, recovery always yields either the library before the import or the completed import,
never a chat without its transcript or a page without its chat, with no residue; recovering again
changes nothing, and the bound sources are never touched. V0.2.1 transcripts are written by 026's
apply and explored in ``test_import_v1_recovery``. Clocks are rebuilt inside every run, so the
action is deterministic.
"""

from __future__ import annotations

import functools
import json
import tempfile
from pathlib import Path

import pytest

from grimoire.archaeologystore import StImporter
from grimoire.store import LibraryStore, ProcessLocker
from grimoire.testing import (
    CrashScenario, Target, atomic_outcome, completes_after, explore_crashes, idempotent_recovery, no_residue,
    untouched,
)
from grimoire.testing.fileops import snapshot

from chat_fixtures import MARA, SAM, Clock, seed_sources
from store_helpers import EXHAUSTIVE_RECOVERY, FAIL_RECOVERY, OWNER, drop_empty_dirs, explore_mode, owner_handle

LIB = "lib"
JOURNAL = Target("replace", target="lib/.txn/journal.json")
RESIDUE = no_residue("lib/.txn/*.new", "lib/.txn/journal.json")
#: A header, a character line with an unchosen swipe and a user line: the smallest export that writes
#: every record kind of the import (scope, chat, two actors, a transcript page). Synthetic text.
ROWS = [{"user_name": "Sam", "chat_metadata": {"note_prompt": "n"}},
        {"name": "Mara", "is_user": False, "mes": "Hello.", "swipes": ["Hello.", "Hi."], "swipe_id": 0},
        {"name": "Sam", "is_user": True, "mes": "Hi Mara."}]
DATA = ("\n".join(json.dumps(row) for row in ROWS) + "\n").encode("utf-8")


@functools.cache
def _template() -> tuple[tuple[str, bytes | None], ...]:
    """The bound definitions in an otherwise empty library, built once by the real store."""
    with tempfile.TemporaryDirectory() as folder:
        with LibraryStore.create(Path(folder) / LIB, locker=ProcessLocker()) as store:
            seed_sources(store)
        return tuple(snapshot(folder).items())


def _setup(ops, root):
    for key, data in _template():
        if data is None:
            ops.make_dirs(root / key)
        else:
            ops.write_bytes(root / key, data)


def scenario() -> CrashScenario:
    locker = ProcessLocker()

    def action(ops, root):
        with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
            importer = StImporter(store, owner_handle(), clock=Clock(), owner=OWNER)
            speakers = {s.id: (MARA if s.role == "character" else SAM) for s in importer.scan(DATA).speakers}
            importer.plan(DATA, speakers=speakers).commit()

    def recover(ops, root):
        LibraryStore.open(root / LIB, ops=ops, locker=locker).close()

    return CrashScenario(
        name="st chat import", setup=_setup, action=action, recover=recover,
        invariants=(atomic_outcome(), idempotent_recovery(), RESIDUE, completes_after(JOURNAL),
                    untouched("lib/sources/*")),
        normalize=drop_empty_dirs)


#: ``crashes-before``, ``crashes-after`` and ``fails`` split ``errors`` (crash and I/O-error plans) in three
#: disjoint parts, as in test_import_v1_recovery, so none nears the 120 s watchdog on slower Windows runners.
#: Recovery crashes run only with GRIMOIRE_EXHAUSTIVE_RECOVERY=1: the five-record commit settles through
#: the same journal engine that test_chatstore_recovery explores with recovery crashes on every run.
MODES = {
    "crashes-before": {**explore_mode("crashes"), "select": lambda op: {"before"}},
    "crashes-after": {**explore_mode("crashes"), "select": lambda op: {"after", "torn"}},
    "fails": {"errors": True, "select": lambda op: {"fail"}},
    "recovery-crashes": explore_mode("recovery-crashes"),
    FAIL_RECOVERY: explore_mode(FAIL_RECOVERY),
}


@pytest.mark.parametrize("mode", [
    "crashes-before", "crashes-after", "fails",
    *(pytest.param(mode, marks=EXHAUSTIVE_RECOVERY) for mode in ("recovery-crashes", FAIL_RECOVERY)),
])
def test_a_sillytavern_import_survives_every_crash_error_and_recovery_crash(tmp_path, mode):
    report = explore_crashes(scenario(), tmp_path, **MODES[mode])
    report.assert_ok()
    assert report.points_explored > 20
    if mode in ("recovery-crashes", FAIL_RECOVERY):
        assert any(run.recover_trace for run in report.runs[1:])
