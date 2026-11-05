"""020 respects 021's accepted-event ids: under an accepted id, only that event's own bridge is stored.

An accepted event's 012 ``Event`` (``as_knowledge_event``) is stored under the same id, so 020's
``append`` refuses a different event there (``accepted_id``), the bridge of a revoked event
(``revoked``), and fails closed when the accepted events cannot be read (``accepted_unreadable``).
Revoking is never refused because a bridge exists. All names are synthetic; nothing calls a model.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from grimoire.core import DIRECTOR, Moment
from grimoire.core.knowledge import Event, Proposition
from grimoire.effects import as_knowledge_event
from grimoire.evidencestore import EvidenceLibrary, EvidenceWriteError
from grimoire.store import LibraryStore, StaleWrite
from grimoire.testing.fileops import snapshot

from effects_fixtures import C, CONTROLS, KEY2, TAM, Handles, contribution, effects_library, give, seed
from store_helpers import OWNER

SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture
def world(tmp_path):
    handles = Handles()
    with LibraryStore.create(tmp_path / "lib") as store:
        seed(store, handles)
        yield store, handles, effects_library(store, handles), EvidenceLibrary(store, handles.app, owner=OWNER)


def accepted(library, handles):
    proposal = give()
    ref = library.accept(proposal, controls=CONTROLS, contributions=[contribution()],
                         endorsements=[handles.endorse(proposal)]).commit().ref
    event, etag = library.read(ref, requested_by=DIRECTOR)
    return ref, event, etag


def foreign(event_id: str = "e-1") -> Event:
    return Event(C, event_id, "Tam steals both keys.", Moment(7, "s1", "t7"), "narration")


def refusal(call) -> tuple[str, tuple[str, ...]]:
    with pytest.raises(EvidenceWriteError) as caught:
        call()
    return caught.value.reason, caught.value.records


def test_a_different_event_under_an_accepted_id_is_refused_and_the_bridge_then_appends(world):
    store, handles, library, evidence = world
    ref, event, _ = accepted(library, handles)
    before = snapshot(store.root)
    assert refusal(lambda: evidence.append(C, [foreign(ref.event_id)])) == ("accepted_id", ("event:e-1",))
    assert snapshot(store.root) == before
    evidence.append(C, [as_knowledge_event(event)]).commit()
    stored = evidence.read(C, requested_by=DIRECTOR).knowledge.events
    assert [(e.event_id, e.summary) for e in stored] == [("e-1", event.summary)]
    assert library.continuity(C, as_of=8, requested_by=DIRECTOR).problems == ()


def test_revoking_a_bridged_event_is_never_refused(world):
    store, handles, library, evidence = world
    ref, event, etag = accepted(library, handles)
    evidence.append(C, [as_knowledge_event(event)]).commit()
    library.revoke(ref, reason="reroll", expected=etag, endorsements=[handles.undo(ref)]).commit()
    codes = [p.code for p in library.continuity(C, as_of=8, requested_by=DIRECTOR).problems]
    assert codes == ["revoked_but_bridged"]


def test_the_bridge_of_a_revoked_event_is_refused(world):
    store, handles, library, evidence = world
    ref, event, etag = accepted(library, handles)
    bridge = as_knowledge_event(event)  # computed while it was active, kept by a slow caller
    library.revoke(ref, reason="reroll", expected=etag, endorsements=[handles.undo(ref)]).commit()
    assert refusal(lambda: evidence.append(C, [bridge])) == ("revoked", ("event:e-1",))


def test_an_unrelated_event_id_still_appends(world):
    store, handles, library, evidence = world
    accepted(library, handles)
    evidence.append(C, [foreign("ev-harbor")]).commit()


@pytest.mark.parametrize("damage", ["unlistable", "undecodable"])
def test_unreadable_accepted_events_fail_closed(world, damage):
    store, handles, library, evidence = world
    ref, event, _ = accepted(library, handles)
    folder = store.root / f"campaigns/{C}/accepted"
    if damage == "unlistable":
        (folder / "e-9.json").write_bytes(b"{broken")  # it could be the file that holds this id
        call = lambda: evidence.append(C, [foreign("ev-harbor")])  # noqa: E731
    else:
        document = json.loads((folder / "e-1.json").read_bytes())
        document["schema"] = "grimoire.accepted-event/9"  # a newer build's record: cannot be compared
        (folder / "e-1.json").write_bytes(json.dumps(document).encode())
        call = lambda: evidence.append(C, [as_knowledge_event(event)])  # noqa: E731
    before = snapshot(store.root)
    assert refusal(call)[0] == "accepted_unreadable"
    assert snapshot(store.root) == before


def test_an_append_without_events_is_not_blocked_by_accepted_damage(world):
    store, handles, library, evidence = world
    accepted(library, handles)
    (store.root / f"campaigns/{C}/accepted/e-9.json").write_bytes(b"{broken")
    evidence.append(C, [Proposition(C, "p-harbor", "The harbor is closed.", Moment(8), "director")]).commit()


def test_an_acceptance_committed_between_preview_and_commit_makes_the_append_stale(world):
    store, handles, library, evidence = world
    snatch = give(KEY2, to=TAM, agent=TAM, backed=None, sequence=8, summary="Tam snatches the brass key.")
    pending = library.accept(snatch, controls=CONTROLS)  # allocates e-1; 020's events/e-1 is still free
    change = evidence.append(C, [foreign(pending.ref.event_id)])  # nothing is accepted under e-1 yet
    pending.commit()
    before = snapshot(store.root)
    with pytest.raises(StaleWrite):
        change.commit()
    assert snapshot(store.root) == before
    assert refusal(lambda: evidence.append(C, [foreign(pending.ref.event_id)]))[0] == "accepted_id"


def test_021_never_loads_the_020_service():
    """021 reads 020 event files only through the store and 020's pure codec, never ``grimoire.evidencestore``."""
    code = "import sys, grimoire.effectstore; print('grimoire.evidencestore' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                         env={**os.environ, "PYTHONPATH": str(SRC)})
    assert out.stdout.strip() == "False"
