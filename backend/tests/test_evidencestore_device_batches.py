"""028: an identified library names its knowledge-log batches for its device, and appends extend every head.

Two devices that append to one campaign offline must never write the same batch path, or sync can only
keep both versions of it as a conflict. A library that was never identified has never synced and writes
exactly the batches it always did. Synthetic fixtures only.
"""

from __future__ import annotations

import pytest

from grimoire.chatstore import device_tag
from grimoire.core import Event
from grimoire.evidence import BATCH_SCHEMA, JOIN_BATCH_SCHEMA
from grimoire.evidencestore import EvidenceLibrary, EvidenceWriteError
from grimoire.store import LibraryStore, StaleWrite
from grimoire.sync import identify

from evidence_fixtures import C, m
from store_helpers import OWNER, owner_handle
from test_evidencestore_scenarios import seed_actors

BATCHES = f"campaigns/{C}/evidence/batch"


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed_actors(opened)
        yield opened


def _evidence(store):
    return EvidenceLibrary(store, owner_handle(), owner=OWNER)


def _event(event_id: str, sequence: int) -> Event:
    return Event(C, event_id, f"the bell rings ({event_id})", m(sequence), "director")


def _batches(store) -> list[str]:
    return sorted(path.name for path in (store.root / BATCHES).iterdir())


def test_a_library_never_identified_writes_untagged_batches_as_before(store):
    _evidence(store).append(C, [_event("e1", 1)]).commit()
    _evidence(store).append(C, [_event("e2", 2)]).commit()
    assert _batches(store) == ["0000000001.json", "0000000002.json"]
    assert '"previous"' in (store.root / BATCHES / "0000000002.json").read_text()


def test_an_identified_library_tags_its_batches_with_its_device(store):
    _evidence(store).append(C, [_event("e1", 1)]).commit()  # before identify: stays untagged
    identify(store, "desktop")
    tag = device_tag(store)[1:]
    _evidence(store).append(C, [_event("e2", 2)]).commit()
    assert _batches(store) == ["0000000001.json", f"0000000002-{tag}.json"]
    log = _evidence(store).read(C, requested_by="director")
    assert log.problems == () and log.quarantined == ()
    assert {event.event_id for event in log.knowledge.events} == {"e1", "e2"}
    _evidence(store).append(C, [_event("e3", 3)]).commit()  # one head: still the single-parent form
    assert f"0000000003-{tag}.json" in _batches(store)
    assert BATCH_SCHEMA in (store.root / BATCHES / f"0000000003-{tag}.json").read_text()
    assert JOIN_BATCH_SCHEMA not in (store.root / BATCHES / f"0000000003-{tag}.json").read_text()


@pytest.mark.parametrize("damage", ["not json", "folder"])
def test_a_damaged_identity_refuses_the_append_rather_than_an_untagged_batch(store, damage):
    identify(store, "desktop")
    identity = store.root / ".sync" / "device.json"
    if damage == "not json":
        identity.write_bytes(b"{not json")
    else:
        identity.unlink()
        identity.mkdir()
    with pytest.raises(EvidenceWriteError) as caught:
        _evidence(store).append(C, [_event("e1", 1)])
    assert caught.value.reason == "device_identity"
    assert not (store.root / BATCHES).exists()


def test_an_append_planned_before_identify_is_stale_at_commit(store):
    change = _evidence(store).append(C, [_event("e1", 1)])
    identify(store, "desktop")
    with pytest.raises(StaleWrite):
        change.commit()
    assert not (store.root / BATCHES).exists()
    _evidence(store).append(C, [_event("e1", 1)]).commit()
    assert _batches(store) == [f"0000000001-{device_tag(store)[1:]}.json"]
