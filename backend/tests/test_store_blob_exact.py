"""C2 store side: ``put_blob_exact`` (a lazily loaded, create-only blob copy), ``verify_blob`` and
``read_blob_exact``, and image objects staying create-only through ``put_exact``."""

from __future__ import annotations

import hashlib

import pytest

from grimoire.core.identity import ImageRef
from grimoire.imagestore import ImageLibrary
from grimoire.store import (
    BlobCorrupt, ImmutableImage, InvalidBlob, InvalidEnvelope, LibraryStore, RecordNotFound, StaleWrite, etag,
)
from grimoire.store.layout import blob_parts

from image_fixtures import GIF, PNG, PNG_2, handle, seed
from store_helpers import OWNER

PATH = "/".join(blob_parts(hashlib.sha256(PNG).hexdigest(), "png"))
H = handle("sources", "campaigns", "assets", "kept")


@pytest.fixture
def store(tmp_path):
    library = LibraryStore.create(tmp_path / "lib")
    seed(library)
    yield library
    library.close()


class Reader:
    def __init__(self, data: bytes | None) -> None:
        self.data, self.calls = data, 0

    def __call__(self) -> bytes | None:
        self.calls += 1
        return self.data


def test_a_lazy_blob_is_loaded_only_when_the_commit_needs_it(store):
    reader = Reader(PNG)
    txn = store.transaction()
    key = txn.put_blob_exact(PATH, reader, size=len(PNG))
    txn.check()  # preflight never loads the bytes
    assert reader.calls == 0 and txn.staged() == (key,)
    result = txn.commit(H)
    assert result.etags[key] == etag(PNG) and 0 < reader.calls <= 2  # authorize, stage: one at a time
    assert (store.root / PATH).read_bytes() == PNG
    assert store.verify_blob(PATH) == etag(PNG)


def test_preview_loads_a_lazy_blob(store):
    reader = Reader(PNG)
    txn = store.transaction()
    key = txn.put_blob_exact(PATH, reader, size=len(PNG))
    assert txn.preview() == {key: PNG} and reader.calls == 1


@pytest.mark.parametrize("loaded", [PNG_2, PNG + b"x", None, GIF, b"\x89PNG\r\n\x1a\n" + b"\x00" * (len(PNG) - 8)],
                         ids=["other-bytes", "wrong-size", "vanished", "wrong-ext", "wrong-hash"])
def test_a_load_that_is_not_the_planned_blob_is_stale_and_writes_nothing(store, loaded):
    txn = store.transaction()
    txn.put_blob_exact(PATH, Reader(loaded), size=len(PNG))
    with pytest.raises(StaleWrite):
        txn.commit(H)
    assert not (store.root / PATH).exists()
    assert not [path for path in (store.root / ".txn").iterdir() if path.name.endswith(".new")]  # no temp left


def test_a_reader_that_changes_between_authorization_and_staging_is_stale(store):
    calls = []

    def flaky() -> bytes:
        calls.append(1)
        return PNG if len(calls) == 1 else PNG_2
    txn = store.transaction()
    txn.put_blob_exact(PATH, flaky, size=len(PNG))
    with pytest.raises(StaleWrite):
        txn.commit(H)
    assert not (store.root / PATH).exists()


def test_put_blob_exact_refuses_what_is_no_blob_copy(store):
    txn = store.transaction()
    with pytest.raises(InvalidEnvelope) as caught:
        txn.put_blob_exact("campaigns/c1/images/wren.json", Reader(PNG), size=len(PNG))
    assert caught.value.code == "not_canonical"
    for size, reason in ((0, "empty"), (-1, "empty"), (33 * 1024 * 1024, "too_large")):
        with pytest.raises(InvalidBlob) as refused:
            txn.put_blob_exact(PATH, Reader(PNG), size=size)
        assert refused.value.reason == reason
    txn.put_blob_exact(PATH, Reader(PNG), size=len(PNG))
    with pytest.raises(InvalidEnvelope, match="twice"):
        txn.put_blob_exact(PATH, Reader(PNG), size=len(PNG))


def test_put_blob_exact_only_creates(store):
    ImageLibrary(store, H, owner=OWNER).add_image("campaign:c1", "wren", PNG).commit()
    txn = store.transaction()
    txn.put_blob_exact(PATH, Reader(PNG), size=len(PNG))
    with pytest.raises(StaleWrite):
        txn.check()


def test_a_denied_blob_copy_loads_nothing_after_its_denial(store):
    reader = Reader(PNG)
    txn = store.transaction()
    key = txn.put_blob_exact(PATH, reader, size=len(PNG))
    result = txn.commit(handle("sources", "campaigns"), on_denied=lambda keys: {})
    assert key in result.denied and not (store.root / PATH).exists() and reader.calls == 1  # authorize only


def test_verify_blob_and_read_blob_exact(store):
    with pytest.raises(RecordNotFound):
        store.verify_blob(PATH)
    assert store.read_blob_exact(PATH, len(PNG)) is None
    ImageLibrary(store, H, owner=OWNER).add_image("campaign:c1", "wren", PNG).commit()
    assert store.verify_blob(PATH) == etag(PNG)
    assert store.read_blob_exact(PATH, len(PNG)) == PNG
    assert store.read_blob_exact(PATH, len(PNG) + 1) is None  # another size: not what was planned
    (store.root / PATH).write_bytes(PNG[:-1] + b"X")  # corrupt in place, same size
    with pytest.raises(BlobCorrupt):
        store.verify_blob(PATH)
    assert store.read_blob_exact(PATH, len(PNG)) == PNG[:-1] + b"X"  # unverified: the put verifies each load
    with pytest.raises(ValueError):
        store.verify_blob("campaigns/c1/images/wren.json")


def test_put_exact_keeps_image_objects_create_only(store):
    ImageLibrary(store, H, owner=OWNER).add_image("campaign:c1", "wren", PNG).commit()
    path = "campaigns/c1/images/wren.json"
    data = (store.root / path).read_bytes()
    txn = store.transaction()
    with pytest.raises(ImmutableImage):
        txn.put_exact(path, data.replace(b"test", b"else"), etag(data))
    txn = store.transaction()  # a create of an absent one is accepted, validated as a record
    txn.put_exact("campaigns/c1/images/other.json", data.replace(b"#image/wren", b"#image/other"), None,
                  regenerable=True)
    txn.commit(H)
    assert ImageLibrary(store, H).read_image_bytes(ImageRef("campaign:c1", "other")) == PNG


def test_a_reader_refusing_at_staging_leaves_no_temp_or_directory(tmp_path):
    """A lazy blob reader may raise its own store's refusal when staging loads it (an origin library
    awaiting recovery): the refused commit still removes its staged files and new directories."""
    from grimoire.core.identity import InstanceRef
    from grimoire.store import PendingRecovery, RecordEnvelope

    store = LibraryStore.create(tmp_path / "lib")
    seed(store)
    calls = []

    def read():
        calls.append(1)
        if len(calls) >= 2:  # authorization loaded it; staging's load meets the refusal
            raise PendingRecovery("origin awaits recovery")
        return PNG

    txn = store.transaction()
    txn.put(RecordEnvelope(InstanceRef("zz-new", "character", "late"), {"n": 1}), None)
    txn.put_blob_exact("/".join(blob_parts(hashlib.sha256(PNG).hexdigest(), "png")), read, size=len(PNG))
    with pytest.raises(PendingRecovery):
        txn.commit(handle("sources", "campaigns", "assets", "kept"))
    try:
        assert len(calls) == 2
        assert [path.name for path in (store.root / ".txn").iterdir() if path.name.endswith(".new")] == []
        assert not (store.root / "campaigns" / "zz-new").exists()
    finally:
        store.close()
