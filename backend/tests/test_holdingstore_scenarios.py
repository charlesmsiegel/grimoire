"""Store-backed counted holdings (Sprint 023): the proving scenario, acceptance cases and failures.

Every write is previewed and committed through the guarded store transaction. All fixtures are
synthetic and no model is ever called (the service never imports a provider).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from grimoire.core.identity import HoldingsRef, InstanceRef
from grimoire.holdings import (
    HOLDINGS_SCHEMA, MAX_FILE, HoldingsCodecError, HoldingsRefused, Line, Operation, holdings_to_payload,
)
from grimoire.holdingstore import HoldingsLibrary
from grimoire.security import Approval, Authorizer, Grant, Permission, ResourcePath, payload_digest
from grimoire.store import LibraryStore, StaleWrite, StoreError, WriteDenied
from grimoire.testing.fileops import snapshot

from holdings_fixtures import CHEST, GREAT, MARA, ORDINARY, STRONG, TOMA, Clock, holdings_library, seed_holders
from store_helpers import OWNER

SRC = Path(__file__).resolve().parents[1] / "src"
MARA_FILE = "campaigns/c1/holdings/character/mara-1.json"
TOMA_FILE = "campaigns/c1/holdings/character/toma-1.json"


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed_holders(opened)
        yield opened


@pytest.fixture
def library(store):
    return holdings_library(store)


def op(op_id, *lines, **kwargs) -> Operation:
    return Operation(op_id, tuple(lines), **kwargs)


def done(library, operation, **kwargs) -> dict[str, str]:
    change = library.apply(operation, **kwargs)
    assert change.outcome == "applied"
    return change.commit()


def refused(library, operation, **kwargs) -> HoldingsRefused:
    with pytest.raises(HoldingsRefused) as error:
        library.apply(operation, **kwargs)
    return error.value


def file_bytes(store, relative: str) -> bytes:
    return (store.root / relative).read_bytes()


def rewrite_payload(store, relative: str, change) -> None:
    path = store.root / relative
    document = json.loads(path.read_bytes())
    change(document)
    path.write_bytes(json.dumps(document).encode())


PROVING = (op("a1", Line.add(MARA, ORDINARY, 8)), op("a2", Line.add(MARA, STRONG, 2)),
           op("c1", Line.consume(MARA, ORDINARY, 3)), op("a3", Line.add(MARA, ORDINARY, 4)))


def test_proving_scenario_survives_reopen(store, tmp_path):
    library = holdings_library(store)
    for step in PROVING:
        done(library, step)
    store.close()
    with LibraryStore.open(tmp_path / "lib") as reopened:
        library = holdings_library(reopened)
        assert library.read(MARA).counts() == {ORDINARY: 9, STRONG: 2}
        before = file_bytes(reopened, MARA_FILE)
        again = library.apply(op("c1", Line.consume(MARA, ORDINARY, 3)))
        assert again.outcome == "already_applied" and again.preview() == {} and again.commit() == {}
        assert again.entries[MARA].after == {ORDINARY: 5}
        assert refused(library, op("c2", Line.consume(MARA, ORDINARY, 15))).reason == "insufficient"
        assert file_bytes(reopened, MARA_FILE) == before
        assert library.read(MARA).counts() == {ORDINARY: 9, STRONG: 2}


def test_move_writes_both_files_in_one_transaction(store, library):
    done(library, op("a1", Line.add(MARA, ORDINARY, 5)))
    change = library.apply(op("m1", Line.move(MARA, TOMA, ORDINARY, 2)))
    assert set(change.preview()) == {MARA_FILE, TOMA_FILE}
    assert [envelope.address for envelope, _ in change.puts] == [HoldingsRef.of(MARA), HoldingsRef.of(TOMA)]
    assert set(change.commit()) == {MARA_FILE, TOMA_FILE}
    assert library.read(MARA).counts() == {ORDINARY: 3} and library.read(TOMA).counts() == {ORDINARY: 2}
    assert library.apply(op("m1", Line.move(MARA, TOMA, ORDINARY, 2))).outcome == "already_applied"
    before = snapshot(store.root)
    assert refused(library, op("m2", Line.move(MARA, TOMA, ORDINARY, 20))).reason == "insufficient"
    assert snapshot(store.root) == before


def test_a_move_creates_the_target_file(store, library):
    done(library, op("a1", Line.add(MARA, ORDINARY, 5)))
    assert library.read(CHEST).etag is None
    done(library, op("m1", Line.move(MARA, CHEST, ORDINARY, 1)))
    assert library.read(CHEST).counts() == {ORDINARY: 1}
    nobody = InstanceRef("c1", "character", "nobody-1")
    assert refused(library, op("m2", Line.move(MARA, nobody, ORDINARY, 1))).reason == "missing_holder"


def test_craft_and_trade_are_atomic(store, library):
    done(library, op("a1", Line.add(MARA, ORDINARY, 8), Line.add(MARA, STRONG, 2), Line.add(TOMA, GREAT, 1)))
    done(library, op("k1", Line.consume(MARA, ORDINARY, 2), Line.consume(MARA, STRONG, 1), Line.add(MARA, GREAT, 1)))
    assert library.read(MARA).counts() == {ORDINARY: 6, STRONG: 1, GREAT: 1}
    done(library, op("t1", Line.move(MARA, TOMA, ORDINARY, 3), Line.move(TOMA, MARA, GREAT, 1)))
    assert library.read(MARA).counts() == {ORDINARY: 3, STRONG: 1, GREAT: 2}
    assert library.read(TOMA).counts() == {ORDINARY: 3}
    before = snapshot(store.root)
    bad = op("t2", Line.move(MARA, TOMA, ORDINARY, 1), Line.move(TOMA, MARA, STRONG, 1))  # toma has no strong
    assert refused(library, bad).reason == "insufficient"
    assert snapshot(store.root) == before


def test_manual_adjust(store, library):
    for step in PROVING:
        done(library, step)
    done(library, op("j1", Line.adjust(MARA, ORDINARY, 9, 7), note="counted the bag again"))
    assert refused(library, op("j2", Line.adjust(MARA, ORDINARY, 9, 5))).reason == "stale_count"
    done(library, op("j3", Line.adjust(MARA, ORDINARY, 7, 0)))
    view = library.read(MARA)
    assert view.counts() == {STRONG: 2} and view.lookup("j3").after == {ORDINARY: 0}
    assert (store.root / MARA_FILE).exists()


def test_concurrent_hand_edit_is_detected(store, library):
    done(library, op("a1", Line.add(MARA, ORDINARY, 5)))
    change = library.apply(op("c1", Line.consume(MARA, ORDINARY, 2)))
    rewrite_payload(store, MARA_FILE, lambda d: d["payload"]["stacks"][0].update(count=4))  # a human recounts
    with pytest.raises(StaleWrite):
        change.commit()
    done(library, op("c1", Line.consume(MARA, ORDINARY, 2)))
    assert library.read(MARA).counts() == {ORDINARY: 2}


def test_unknown_fields_and_owner_survive(store, library):
    done(library, op("a1", Line.add(MARA, ORDINARY, 5)))

    def hand_edit(document):
        document["x-env"] = 1
        document["payload"]["x-pay"] = {"a": 1}
        document.pop("owner", None)

    rewrite_payload(store, MARA_FILE, hand_edit)
    done(library, op("a2", Line.add(MARA, ORDINARY, 1)))
    document = json.loads(file_bytes(store, MARA_FILE))
    assert document["x-env"] == 1 and document["payload"]["x-pay"] == {"a": 1} and "owner" not in document
    assert document["schema"] == HOLDINGS_SCHEMA
    done(library, op("a3", Line.add(TOMA, ORDINARY, 1)))
    assert json.loads(file_bytes(store, TOMA_FILE))["owner"] == OWNER  # new files get the library owner
    rewrite_payload(store, TOMA_FILE, lambda d: d.update(owner="human:other"))
    done(library, op("a4", Line.add(TOMA, ORDINARY, 1)))
    assert json.loads(file_bytes(store, TOMA_FILE))["owner"] == "human:other"  # another owner is kept


def test_approval_gated_commit(store):
    authority = Authorizer([OWNER])
    authority.add_grant(Grant("g", "app:holdings", frozenset({Permission.APPLY}), ResourcePath.parse("campaigns"),
                              OWNER))
    library = HoldingsLibrary(store, authority.handle_for("app:holdings"), clock=Clock(), owner=OWNER)
    change = library.apply(op("a1", Line.add(MARA, ORDINARY, 5)))
    with pytest.raises(WriteDenied):
        library.apply(op("a1", Line.add(MARA, ORDINARY, 5))).commit()
    for number, (path, data) in enumerate(change.preview().items()):
        authority.register_approval(Approval(f"ok-{number}", "app:holdings", Permission.APPLY,
                                             ResourcePath.parse(path), payload_digest(data), OWNER))
    change.commit({MARA_FILE: "ok-0"})
    assert library.read(MARA).counts() == {ORDINARY: 5}
    stale = library.apply(op("a2", Line.add(MARA, ORDINARY, 1)))
    for number, (path, data) in enumerate(stale.preview().items()):
        authority.register_approval(Approval(f"old-{number}", "app:holdings", Permission.APPLY,
                                             ResourcePath.parse(path), payload_digest(data), OWNER))
    rewrite_payload(store, MARA_FILE, lambda d: d.update({"x-touched": True}))
    with pytest.raises(StaleWrite):
        stale.commit({MARA_FILE: "old-0"})
    recreated = library.apply(op("a2", Line.add(MARA, ORDINARY, 1)))  # new bytes: the old approval does not fit
    with pytest.raises(WriteDenied):
        recreated.commit({MARA_FILE: "old-0"})


def test_undecodable_file_blocks_ops_and_is_reported(store, library):
    done(library, op("a1", Line.add(MARA, ORDINARY, 5)))
    rewrite_payload(store, MARA_FILE, lambda d: d["payload"]["stacks"][0].update(count=-1))
    before = file_bytes(store, MARA_FILE)
    with pytest.raises(HoldingsCodecError):
        library.read(MARA)
    with pytest.raises(HoldingsCodecError):
        library.apply(op("a2", Line.add(MARA, ORDINARY, 1)))
    assert file_bytes(store, MARA_FILE) == before
    found = library.campaign_holdings("c1")
    assert [(p.path, p.code) for p in found.problems] == [(MARA_FILE, "undecodable")]
    assert MARA not in found.views


def test_envelope_level_bad_holdings_file_is_reported(store, library):
    done(library, op("a1", Line.add(TOMA, ORDINARY, 5)))
    (store.root / MARA_FILE).write_bytes(b"{not json")
    found = library.campaign_holdings("c1")
    assert [(p.path, p.holder, p.code, p.reason) for p in found.problems] == [
        (MARA_FILE, MARA, "undecodable", "malformed")]
    # a hand-made look-alike; its lowercase twin does not exist, so case-insensitive filesystems keep both apart
    (store.root / "campaigns/c1/holdings/character/Ivo-1.json").write_bytes(b"{}")
    (store.root / "campaigns/c1/holdings/notes.txt").write_bytes(b"x")
    codes = {(p.path, p.code) for p in library.campaign_holdings("c1").problems}
    assert ("campaigns/c1/holdings/notes.txt", "unrecognized") in codes
    assert any(path.endswith("Ivo-1.json") and code == "unrecognized" for path, code in codes)
    assert not [p for p in library.campaign_holdings("c2").problems]
    assert set(found.views) == {TOMA}


def test_unknown_schema_is_never_rewritten(store, library):
    done(library, op("a1", Line.add(MARA, ORDINARY, 5)))
    rewrite_payload(store, MARA_FILE, lambda d: d.update(schema="grimoire.holdings/2"))
    before = file_bytes(store, MARA_FILE)
    with pytest.raises(HoldingsCodecError) as error:
        library.apply(op("a2", Line.add(MARA, ORDINARY, 1)))
    assert error.value.reason == "unsupported_schema" and file_bytes(store, MARA_FILE) == before
    assert [(p.code, p.reason) for p in library.campaign_holdings("c1").problems] == [
        ("undecodable", "unsupported_schema")]


def test_a_newer_envelope_format_is_refused_through_the_service(store, library):
    from grimoire.store import UnsupportedFormat
    done(library, op("a1", Line.add(MARA, ORDINARY, 5)))
    rewrite_payload(store, MARA_FILE, lambda d: d.update(format="grimoire.scope-record/2"))
    before = file_bytes(store, MARA_FILE)
    with pytest.raises(UnsupportedFormat):
        library.apply(op("a2", Line.add(MARA, ORDINARY, 1)))
    assert file_bytes(store, MARA_FILE) == before
    assert [(p.code, p.reason) for p in library.campaign_holdings("c1").problems] == [
        ("undecodable", "unsupported_format")]


def test_a_hand_edit_to_zero_means_none(store, library):
    done(library, op("a1", Line.add(MARA, ORDINARY, 5), Line.add(MARA, STRONG, 1)))
    rewrite_payload(store, MARA_FILE, lambda d: d["payload"]["stacks"][0].update(count=0))  # "she used them all"
    assert library.read(MARA).counts() == {STRONG: 1}
    done(library, op("a2", Line.add(MARA, STRONG, 1)))
    stacks = json.loads(file_bytes(store, MARA_FILE))["payload"]["stacks"]
    assert stacks == [{"item": STRONG, "count": 2}]


def test_cause_alone_distinguishes_operations(library):
    done(library, op("a1", Line.add(MARA, ORDINARY, 5), cause="effect-1"))
    assert refused(library, op("a1", Line.add(MARA, ORDINARY, 5), cause="effect-2")).reason == "op_id_reused"


def test_a_failed_commit_can_be_retried_and_still_refuses(store, library):
    done(library, op("a1", Line.add(MARA, ORDINARY, 5)))
    change = library.apply(op("c1", Line.consume(MARA, ORDINARY, 1)))
    rewrite_payload(store, MARA_FILE, lambda d: d.update({"x-touched": True}))
    for _ in range(2):  # not marked committed by the failure: CAS refuses it again, never "already committed"
        with pytest.raises(StaleWrite):
            change.commit()


def test_campaign_holdings_reads_under_the_store_lock(store, library):
    done(library, op("a1", Line.add(MARA, ORDINARY, 5)))
    owned = []
    real = store.campaign_holdings

    def spy(campaign_id):
        owned.append(store.lock._is_owned())
        return real(campaign_id)

    store.campaign_holdings = spy  # type: ignore[method-assign]
    assert library.campaign_holdings("c1").views[MARA].counts() == {ORDINARY: 5}
    assert owned == [True]


def test_the_service_never_loads_the_whole_library(store, library, monkeypatch):
    done(library, op("a1", Line.add(MARA, ORDINARY, 5)))
    monkeypatch.setattr(type(store), "load", lambda self: (_ for _ in ()).throw(AssertionError("whole load")))
    change = library.apply(op("m1", Line.move(MARA, TOMA, ORDINARY, 2)))
    change.preview()
    change.commit()
    assert library.read(TOMA).counts() == {ORDINARY: 2}
    assert set(library.campaign_holdings("c1").views) == {MARA, TOMA}


def test_an_unstorable_holder_is_missing_not_a_store_error(library):
    for holder in (InstanceRef("c1", "character", "x" * 124), InstanceRef("c1", "con", "x")):
        assert refused(library, op("a1", Line.add(holder, ORDINARY, 1))).reason == "missing_holder"


def test_read_missing_holder(library):
    with pytest.raises(HoldingsRefused) as error:
        library.read(InstanceRef("c1", "character", "nobody-1"))
    assert error.value.reason == "missing_holder"


def test_vanished_holder(store, library):
    done(library, op("a1", Line.add(MARA, ORDINARY, 5)))
    change = library.apply(op("a2", Line.add(MARA, ORDINARY, 1)))
    instance = store.root / "campaigns/c1/instances/character/mara-1.json"
    kept = instance.read_bytes()
    instance.unlink()
    with pytest.raises(StaleWrite):
        change.commit()
    found = library.campaign_holdings("c1")
    assert [(p.path, p.code) for p in found.problems] == [(MARA_FILE, "missing_holder")]
    assert found.views[MARA].counts() == {ORDINARY: 5} and (store.root / MARA_FILE).exists()
    assert library.apply(op("a1", Line.add(MARA, ORDINARY, 5))).commit() == {}  # a landed retry still is one
    assert refused(library, op("a3", Line.add(MARA, ORDINARY, 1))).reason == "missing_holder"
    instance.write_bytes(kept)


def test_a_replaced_holder_is_not_credited_by_a_stale_change(store, library):
    """A change planned for one individual never lands on a different one given the same id since."""
    change = library.apply(op("g1", Line.add(MARA, "gold-coin", 500)))
    instance = store.root / "campaigns/c1/instances/character/mara-1.json"
    instance.unlink()  # deleted by hand, then a new individual gets the id
    seed_holders(store, (MARA,))
    document = json.loads(instance.read_bytes())
    document["payload"] = {"note": "a different individual"}
    instance.write_bytes(json.dumps(document).encode())
    with pytest.raises(StaleWrite, match="holder changed"):
        change.commit()
    assert library.read(MARA).counts() == {}


def test_a_replaced_holder_is_never_retried_through(store, library):
    """The retry contract carries the first plan's pin: re-planning cannot credit the new individual."""
    from grimoire.holdingstore import HolderReplaced
    operation = op("g1", Line.add(MARA, "gold-coin", 500))
    change = library.apply(operation)
    instance = store.root / "campaigns/c1/instances/character/mara-1.json"
    document = json.loads(instance.read_bytes())
    document["payload"] = {"note": "a different individual"}
    instance.write_bytes(json.dumps(document).encode())
    with pytest.raises(HolderReplaced):
        change.commit()
    assert refused(library, operation, holders=change.holder_etags).reason == "holder_replaced"
    assert library.read(MARA).counts() == {}


def test_an_unreadable_holder_is_pinned_by_its_bytes(store, library):
    from grimoire.holdingstore import HolderReplaced
    instance = store.root / "campaigns/c1/instances/character/mara-1.json"
    instance.write_bytes(b"{individual A, broken")
    library.apply(op("g1", Line.add(MARA, "gold-coin", 1))).commit()  # unchanged unreadable bytes: commits
    change = library.apply(op("g2", Line.add(MARA, "gold-coin", 1)))
    instance.write_bytes(b"{individual B, also broken")
    with pytest.raises(HolderReplaced):
        change.commit()
    assert library.read(MARA).counts() == {"gold-coin": 1}


def test_kind_reuse_does_not_inherit(store, library):
    done(library, op("a1", Line.add(MARA, ORDINARY, 5)))
    item_mara = InstanceRef("c1", "item", "mara-1")
    seed_holders(store, (item_mara,))
    assert library.read(item_mara).counts() == {} and library.read(MARA).counts() == {ORDINARY: 5}


def test_stale_change_after_another_commit(library):
    done(library, op("a1", Line.add(MARA, ORDINARY, 5)))
    first = library.apply(op("c1", Line.consume(MARA, ORDINARY, 1)))
    second = library.apply(op("c2", Line.consume(MARA, ORDINARY, 1)))
    first.commit()
    with pytest.raises(StaleWrite):
        second.commit()
    done(library, op("c2", Line.consume(MARA, ORDINARY, 1)))
    assert library.read(MARA).counts() == {ORDINARY: 3}


def test_noop_and_double_commit(library):
    change = library.apply(op("a1", Line.add(MARA, ORDINARY, 5)))
    change.commit()
    with pytest.raises(StoreError):
        change.commit()
    noop = library.apply(op("a1", Line.add(MARA, ORDINARY, 5)))
    assert noop.commit() == {}
    with pytest.raises(StoreError):
        noop.commit()


def test_partial_op_is_refused_and_repaired_with_a_fresh_op(store, library):
    done(library, op("a1", Line.add(MARA, ORDINARY, 5)))
    done(library, op("m1", Line.move(MARA, TOMA, ORDINARY, 2)))
    rewrite_payload(store, TOMA_FILE, lambda d: (d["payload"].update(ops=[], stacks=[])))  # one-sided by hand
    error = refused(library, op("m1", Line.move(MARA, TOMA, ORDINARY, 2)))
    assert error.reason == "partial_op" and "toma-1" in error.detail
    done(library, op("fix-1", Line.adjust(TOMA, ORDINARY, 0, 2), note="completed m1 by hand"))
    assert refused(library, op("m1", Line.move(MARA, TOMA, ORDINARY, 2))).reason == "partial_op"


def test_retry_after_eviction_needs_a_basis(store, library):
    basis = {MARA: library.read(MARA).holdings.seq}  # recorded when the operation is first created: 0
    done(library, op("c0", Line.add(MARA, ORDINARY, 1)), basis=basis)
    rewrite_payload(store, MARA_FILE, lambda d: d["payload"].update(ops=[], evicted_through=1))  # evicted
    before = file_bytes(store, MARA_FILE)
    assert refused(library, op("c0", Line.add(MARA, ORDINARY, 1)), basis=basis).reason == "beyond_history"
    assert file_bytes(store, MARA_FILE) == before and library.read(MARA).counts() == {ORDINARY: 1}


def test_bad_inputs_are_refused_with_their_own_errors(store, library):
    with pytest.raises(TypeError):
        library.read("campaign:c1/character/mara-1")  # type: ignore[arg-type]
    add = op("a1", Line.add(MARA, ORDINARY, 1))
    for basis in ({MARA: "3"}, {MARA: True}, {MARA: -1}, {TOMA: 0}):
        assert refused(library, add, basis=basis).reason == "invalid_field"
    done(library, add)
    assert refused(library, add, basis={TOMA: 0}).reason == "invalid_field"  # checked before replay too
    with pytest.raises(ValueError):
        HoldingsLibrary(store, None, clock=Clock(), owner="")


def test_a_concurrent_commit_cannot_split_the_read(store, library):
    """M1: apply reads every holder under the store lock, so a commit from another thread waits."""
    import threading
    done(library, op("a1", Line.add(MARA, ORDINARY, 5)))
    move = op("x", Line.move(MARA, TOMA, ORDINARY, 2))
    pending = holdings_library(store).apply(move)
    reader = holdings_library(store)
    real_view = reader._view
    landed = threading.Event()
    owned = []

    def slow_view(holder):
        owned.append(store.lock._is_owned())  # deterministic: every holder is read while this thread holds it
        found = real_view(holder)
        if holder == MARA and not landed.is_set():  # between the two holders: another thread commits
            other = threading.Thread(target=lambda: (pending.commit(), landed.set()))
            other.start()
            other.join(timeout=0.5)
        return found

    reader._view = slow_view  # type: ignore[method-assign]
    change = reader.apply(move)  # without the lock this saw toma's half only: a false partial_op
    assert owned == [True, True]
    assert change.outcome in ("applied", "already_applied")
    assert landed.wait(timeout=10)
    assert reader.apply(move).outcome == "already_applied"
    assert library.read(MARA).counts() == {ORDINARY: 3} and library.read(TOMA).counts() == {ORDINARY: 2}


def test_real_measure_matches_written_bytes(store, library):
    from grimoire.holdingstore.library import _StoreSizer
    done(library, op("a1", Line.add(MARA, ORDINARY, 5), note="ünïcode"))
    view = library.read(MARA)
    written = file_bytes(store, MARA_FILE)
    sizer = _StoreSizer({MARA: (view.envelope.owner, view.envelope.extra, json.loads(written)["previous"])})
    assert sizer.document(view.holdings) == len(written)
    done(library, op("a2", Line.add(MARA, ORDINARY, 1)))  # a rewrite records the etag it replaced
    written = file_bytes(store, MARA_FILE)
    sizer = _StoreSizer({MARA: (OWNER, {}, json.loads(written)["previous"])})
    assert sizer.document(library.read(MARA).holdings) == len(written)


def test_huge_envelope_extra_is_too_large_at_plan_time(store, library):
    done(library, op("a1", Line.add(MARA, ORDINARY, 5)))
    rewrite_payload(store, MARA_FILE, lambda d: d.update({"x-blob": "z" * (MAX_FILE + 100_000)}))
    with pytest.raises(HoldingsRefused) as error:
        library.apply(op("a2", Line.add(MARA, ORDINARY, 1)))
    assert error.value.reason == "too_large"


def test_no_api_erases_holdings(store, library):
    public = {name for name in dir(HoldingsLibrary) if not name.startswith("_")}
    assert public == {"apply", "campaign_holdings", "read"}
    done(library, op("a1", Line.add(MARA, ORDINARY, 5)))
    done(library, op("j1", Line.adjust(MARA, ORDINARY, 5, 0)))
    assert (store.root / MARA_FILE).exists() and library.read(MARA).lookup("a1") is not None


def test_the_holdings_payload_is_the_codec_output(store, library):
    done(library, op("a1", Line.add(MARA, ORDINARY, 5)))
    view = library.read(MARA)
    assert json.loads(file_bytes(store, MARA_FILE))["payload"] == holdings_to_payload(view.holdings)


def _loaded(module: str) -> list[str]:
    code = (f"import json, sys; import {module}; "
            "print(json.dumps(sorted(m for m in sys.modules if m.split('.')[0] == 'grimoire')))")
    output = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                            env={**os.environ, "PYTHONPATH": str(SRC)}).stdout
    return json.loads(output)


def test_the_pure_package_loads_no_store_and_the_service_no_provider():
    assert not [m for m in _loaded("grimoire.holdings")
                if m.startswith(("grimoire.providers", "grimoire.store", "grimoire.holdingstore"))]
    loaded = _loaded("grimoire.holdingstore")
    assert "grimoire.store" in loaded  # the check is not vacuous
    assert not [m for m in loaded if m.startswith(("grimoire.providers", "grimoire.capture", "grimoire.evals"))]
