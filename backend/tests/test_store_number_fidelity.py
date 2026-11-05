"""No store write silently changes a stored number.

A number's text that a ``float`` cannot hold exactly (excess digits, underflow to zero, a huge
value) still reads, rounded, and still syncs byte for byte. A rewrite that would put the rounded
value back is refused (``imprecise_number``) and the file is left as it was.
"""

from __future__ import annotations

import pytest

from grimoire.store import ImpreciseNumber, LibraryStore, RecordEnvelope, etag

from store_helpers import WREN_A, owner_handle

PATH = "campaigns/c1/instances/character/wren-a.json"
IMPRECISE = {
    "x-precise": "0.1000000000000000055511151231257827021181583404541015625001",
    "x-tiny": "1e-400",
    "x-big": "123456789012345678901234567890.5",
    "x-negative-tiny": "-1e-400",
}
PRECISE = {"x-trailing-zero": "1.50", "x-exponent": "1e2", "x-tenth": "0.1", "x-int": "1" + "0" * 40}


def _library(root) -> LibraryStore:
    store = LibraryStore.create(root)
    txn = store.transaction()
    txn.put(RecordEnvelope(WREN_A, {"hp": 1}), None)
    txn.commit(owner_handle())
    return store


def _hand_add(root, fields: dict[str, str]) -> bytes:
    """A person adds unknown authored fields as raw JSON text."""
    path = root / PATH
    text = path.read_text().rstrip().rstrip("}").rstrip()
    text += "".join(f',\n  "{key}": {value}' for key, value in fields.items()) + "\n}\n"
    path.write_text(text)
    return path.read_bytes()


def _rewrite(store, **payload):
    stored = store.read(WREN_A)
    txn = store.transaction()
    txn.put(RecordEnvelope(WREN_A, payload or {"hp": 2}, extra=stored.envelope.extra), stored.etag)
    return txn.commit(owner_handle())


@pytest.mark.parametrize("key", sorted(IMPRECISE))
def test_a_rewrite_that_would_round_a_number_is_refused(tmp_path, key):
    with _library(tmp_path / "lib") as store:
        before = _hand_add(tmp_path / "lib", {key: IMPRECISE[key]})
        assert store.load().problems == ()  # the file still reads
        with pytest.raises(ImpreciseNumber) as refused:
            _rewrite(store)
        assert refused.value.reason == "imprecise_number"
        assert refused.value.where == {PATH: (f"/{key}",)}
        assert (tmp_path / "lib" / PATH).read_bytes() == before


def test_precise_spellings_and_integers_rewrite_fine(tmp_path):
    with _library(tmp_path / "lib") as store:
        _hand_add(tmp_path / "lib", PRECISE)
        _rewrite(store)
        assert store.read(WREN_A).envelope.payload == {"hp": 2}
        extra = store.read(WREN_A).envelope.extra
        assert (extra["x-trailing-zero"], extra["x-exponent"], extra["x-tenth"]) == (1.5, 100.0, 0.1)
        assert extra["x-int"] == 10 ** 40


def test_dropping_or_changing_the_imprecise_value_is_a_deliberate_write(tmp_path):
    with _library(tmp_path / "lib") as store:
        _hand_add(tmp_path / "lib", {"x-tiny": IMPRECISE["x-tiny"], "x-big": IMPRECISE["x-big"]})
        stored = store.read(WREN_A)
        extra = {key: value for key, value in stored.envelope.extra.items() if key != "x-tiny"}
        extra["x-big"] = 7
        txn = store.transaction()
        txn.put(RecordEnvelope(WREN_A, {"hp": 3}, extra=extra), stored.etag)
        txn.commit(owner_handle())
        assert store.read(WREN_A).envelope.extra["x-big"] == 7


def test_an_exact_copy_of_a_file_with_an_imprecise_number_is_byte_identical(tmp_path):
    with _library(tmp_path / "a") as a, _library(tmp_path / "b") as b:
        data = _hand_add(tmp_path / "a", IMPRECISE)
        txn = b.transaction()  # replaces b's own copy, as sync does: verbatim, nothing re-encoded
        txn.put_exact(PATH, data, b.read(WREN_A).etag)
        txn.commit(owner_handle())
        assert (tmp_path / "b" / PATH).read_bytes() == data
        assert b.read(WREN_A).etag == etag(data)
        replaced = _hand_add(tmp_path / "b", {"x-more": "2e-400"})  # and an exact copy replaces it too
        txn = a.transaction()
        txn.put_exact(PATH, replaced, a.read(WREN_A).etag)
        txn.commit(owner_handle())
        assert (tmp_path / "a" / PATH).read_bytes() == replaced


def test_a_new_revision_that_would_round_the_head_it_was_edited_from_is_refused(tmp_path):
    """A definition is edited by writing a new revision, so it is compared with the head it replaces."""
    from store_helpers import HARBOR, WREN_R1, WREN_R2, harbor_manifest, seed

    root = tmp_path / "lib"
    with LibraryStore.create(root) as store:
        seed(store)
        r1 = next(root.rglob("r1.json"))
        text = r1.read_text().rstrip().rstrip("}").rstrip()
        r1.write_text(text + f',\n  "x-weight": {IMPRECISE["x-precise"]}\n}}\n')
        old = store.read(WREN_R1)
        source = store.read_source(HARBOR)
        txn = store.transaction()
        txn.put(RecordEnvelope(WREN_R2, {**old.envelope.payload, "bio": "a captain"}, name="Wren",
                               extra=old.envelope.extra), None)
        txn.put(harbor_manifest({"character/wren": "r2"}), source.etag)
        with pytest.raises(ImpreciseNumber) as refused:
            txn.commit(owner_handle())
        assert list(refused.value.where.values()) == [("/x-weight",)]
        assert not r1.with_name("r2.json").exists()
