"""One shared, cached cross-source exclusion check (Sprint 018 follow-up, also used by 107).

``LibraryStore.check_source``/``check_definition`` scan one source; whole-library exclusions
(an ``identity_conflict`` that only a build over every source can see) come from
``LibraryStore.excluded_sources``. It runs ``load()``'s own exclusion algorithm and is cached
by a stat-only fingerprint of ``sources/``, so the chat and image services fail closed on such
a source without loading the whole library on every call. Synthetic fixtures only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.chats import Launch
from grimoire.chatstore import InvalidBinding
from grimoire.core.identity import IdentityConflict, ImageRef, SourceRef
from grimoire.core.index import DefinitionIndex
from grimoire.images import Placement
from grimoire.imagestore import ImageLibrary, InvalidImage
from grimoire.store import LibraryStore, RecordEnvelope, SourceManifest, StoredSource, loader

from chat_fixtures import chat_library
from image_fixtures import PNG, handle
from store_helpers import OWNER

A, B = "catalog:a", "catalog:b"
HERO_A = SourceRef(A, "character", "hero")
HERO_B = SourceRef(B, "character", "hero")


class _ConflictingIndex(DefinitionIndex):
    """Simulates a conflict only a whole-library build sees: ``b`` clashes with ``a``, never alone."""

    def __init__(self, sources, definitions, heads=None, links=()):
        sources = list(sources)
        if {A, B} <= {source.source_id for source in sources}:
            raise IdentityConflict(f"{B} conflicts with {A}", (B,))
        super().__init__(sources, definitions, heads, links)


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        txn = opened.transaction()
        for source, hero in ((A, HERO_A), (B, HERO_B)):
            txn.put(SourceManifest(source, OWNER), None)
            txn.put(RecordEnvelope(hero.at("r1"), {}, name="Hero"), None)
        txn.commit(handle())
        images = ImageLibrary(opened, handle(), owner=OWNER)
        batch = images.change()
        image = batch.add_image(B, "hero", PNG)
        batch.place(HERO_B, "avatar", Placement("set", "avatar", image), None)
        batch.commit()
        yield opened


@pytest.fixture
def conflict(store, monkeypatch):
    monkeypatch.setattr(loader, "DefinitionIndex", _ConflictingIndex)
    store._scans, store._exclusions = {}, None  # the algorithm changed under the cache (tests only)


def test_one_source_checks_agree_with_load_on_a_cross_source_conflict(store, conflict):
    assert store.load().excluded == (B,)
    assert store.excluded_sources() == frozenset({B})
    assert store.check_source(B) == "excluded_source"
    assert store.check_definition(HERO_B) == "excluded_source"
    assert isinstance(store.check_source(A), StoredSource)
    assert store.check_definition(HERO_A).ref == HERO_A.at("r1")


def test_one_source_checks_agree_with_load_on_ordinary_damage(store):
    (store.root / "sources/catalog/b/_source/manifest.json").write_text("{broken")
    stray = store.root / "sources/catalog/c/_source/records/character/x/r1.json"
    stray.parent.mkdir(parents=True)
    stray.write_text(json.dumps({"format": "grimoire.record/1", "address": "catalog:c/character/x@r1",
                                 "payload": {}}))
    excluded = set(store.load().excluded)
    assert excluded == {B, "catalog:c"} == set(store.excluded_sources())
    for source in (A, B, "catalog:c", "catalog:absent"):
        verdict = store.check_source(source)
        expected = "excluded_source" if source in excluded else (
            "missing_source" if source == "catalog:absent" else None)
        assert (verdict if isinstance(verdict, str) else None) == expected


def _count(monkeypatch, name):
    calls = []
    real = getattr(loader, name)

    def counting(access, source_id):
        calls.append(source_id)
        return real(access, source_id)
    monkeypatch.setattr(loader, name, counting)
    return calls


def _cached(store) -> dict:
    """The store's cached scan object per source (identity shows whether a source was rescanned)."""
    store.excluded_sources()
    return {source_id: entry[1] for source_id, entry in store._scans.items()}


def test_only_changed_sources_are_scanned_again(store):
    before = _cached(store)
    assert _cached(store) == before and all(_cached(store)[s] is before[s] for s in before)
    revision = store.root / "sources/catalog/a/_source/records/character/hero/r1.json"
    revision.write_text(revision.read_text().replace('"payload": {}', '"payload": {"x": 1}'))  # a hand edit
    after = _cached(store)
    assert after[A] is not before[A] and after[B] is before[B]  # only the edited source
    txn = store.transaction()  # a commit of a new definition in b
    txn.put(RecordEnvelope(SourceRef(B, "character", "other", "r1"), {}), None)
    txn.commit(handle())
    later = _cached(store)
    assert later[A] is after[A] and later[B] is not after[B]
    (store.root / "sources/catalog/new/_source").mkdir(parents=True)  # a new source folder
    newest = _cached(store)
    assert set(newest) == {A, B, "catalog:new"} and newest[A] is later[A] and newest[B] is later[B]


def test_art_and_campaign_writes_never_invalidate_the_check(store, monkeypatch):
    before = _cached(store)
    exclusions = store._exclusions
    images = ImageLibrary(store, handle(), owner=OWNER)
    images.add_image(A, "first", PNG).commit()  # a's first picture creates its images/ folder
    for n in range(3):
        batch = images.change()
        image = batch.add_image(B, f"more-{n}", PNG)
        batch.place(HERO_B, f"gallery-{n}", Placement("set", "gallery", image), None)
        batch.commit()
    chats = chat_library(store)
    chats.start_chat(Launch("character", HERO_A)).commit()
    after = _cached(store)
    assert store._exclusions is exclusions  # the whole-library result was never recomputed
    assert all(after[source] is before[source] for source in before)


def test_a_batch_or_resolution_verifies_the_exclusions_once(store, monkeypatch):
    prints = _count(monkeypatch, "source_fingerprint")
    images = ImageLibrary(store, handle(), owner=OWNER)
    images.effective_placements(HERO_B)
    assert len(prints) == 2  # one fingerprint per source, once for the whole resolution
    prints.clear()
    batch = images.change()
    batch.add_image(B, "x", PNG)
    batch.place(HERO_B, "portrait", Placement("set", "portrait", ImageRef(B, "x")), None)
    assert len(prints) == 2  # once for the whole batch while nothing is committed
    prints.clear()
    batch.commit()
    assert len(prints) == 2  # re-validation of the whole batch: once


def test_a_cached_verdict_flips_when_another_source_changes(store, monkeypatch):
    class _RivalIndex(DefinitionIndex):
        """b conflicts only while a holds a definition named Rival (a whole-library-only conflict)."""

        def __init__(self, sources, definitions, heads=None, links=()):
            definitions = list(definitions)
            sources = list(sources)
            if B in {source.source_id for source in sources} and any(
                    d.ref.source == A and d.name == "Rival" for d in definitions):
                raise IdentityConflict(f"{B} conflicts with a's Rival", (B,))
            super().__init__(sources, definitions, heads, links)
    monkeypatch.setattr(loader, "DefinitionIndex", _RivalIndex)
    store._scans, store._exclusions = {}, None  # the algorithm changed under the cache (tests only)
    assert isinstance(store.check_source(B), StoredSource)
    revision = store.root / "sources/catalog/a/_source/records/character/hero/r1.json"
    revision.write_text(revision.read_text().replace('"name": "Hero"', '"name": "Rival"'))  # b's files unchanged
    assert store.check_source(B) == "excluded_source"
    assert store.load().excluded == (B,)
    revision.write_text(revision.read_text().replace('"name": "Rival"', '"name": "Hero"'))
    assert isinstance(store.check_source(B), StoredSource)


def test_a_commit_inside_a_read_pass_makes_the_next_check_verify_again(store, monkeypatch):
    class _RivalIndex(DefinitionIndex):
        def __init__(self, sources, definitions, heads=None, links=()):
            definitions, sources = list(definitions), list(sources)
            if B in {source.source_id for source in sources} and any(
                    d.ref.source == A and d.name == "Rival" for d in definitions):
                raise IdentityConflict(f"{B} conflicts with a's Rival", (B,))
            super().__init__(sources, definitions, heads, links)
    monkeypatch.setattr(loader, "DefinitionIndex", _RivalIndex)
    store._scans, store._exclusions = {}, None  # the algorithm changed under the cache (tests only)
    with store.read_pass():
        assert isinstance(store.check_source(B), StoredSource)
        txn = store.transaction()  # a's new head revision is a Rival: b becomes excluded
        txn.put(SourceManifest(A, OWNER, heads={"character/hero": "r2"}), store.read_source(A).etag)
        txn.put(RecordEnvelope(HERO_A.at("r2"), {}, name="Rival"), None)
        txn.commit(handle())
        assert store.check_source(B) == "excluded_source"


def test_cached_scans_hold_no_payloads(store):
    store.excluded_sources()
    for _, scan in store._scans.values():
        for files in scan.revisions.values():
            assert all(found is None or found.envelope is None for found in files.values())


def test_an_unlistable_records_folder_is_never_cached_and_answers_consistently(store):
    import os
    folder = store.root / "sources/catalog/a/_source/records/character"
    os.chmod(folder, 0)
    try:
        if os.access(folder, os.R_OK):
            pytest.skip("permissions are not enforced here (root or Windows)")
        first = store.excluded_sources()
        assert store.excluded_sources() == first == frozenset(store.load().excluded)
        assert A not in store._scans  # an I/O error may not repeat: never cached
    finally:
        os.chmod(folder, 0o755)
    assert A not in store.excluded_sources()


def test_the_image_service_fails_closed_on_a_source_excluded_only_across_sources(store, conflict):
    images = ImageLibrary(store, handle(), owner=OWNER)
    result = images.effective_placements(HERO_B)
    assert result.slots == () and [p.code for p in result.problems] == ["excluded_source"]
    with pytest.raises(InvalidImage) as refused:
        images.read_image(ImageRef(B, "hero"))
    assert refused.value.reason == "excluded_source"
    with pytest.raises(InvalidImage) as refused:
        images.add_image(B, "again", PNG)
    assert refused.value.reason == "missing_scope"
    assert images.effective_placements(HERO_A).problems == ()


def test_the_chat_service_fails_closed_on_a_source_excluded_only_across_sources(store, conflict):
    chats = chat_library(store)
    with pytest.raises(InvalidBinding) as refused:
        chats.start_chat(Launch("character", HERO_B))
    assert refused.value.reason == "excluded_source"
    chats.start_chat(Launch("character", HERO_A)).commit()


# -- round-1 review regressions ------------------------------------------------------------------

def test_one_source_checks_never_walk_art(store, monkeypatch):
    images = ImageLibrary(store, handle(), owner=OWNER)
    batch = images.change()
    for n in range(20):
        batch.add_image(B, f"pic-{n}", PNG + bytes([n]))
    batch.commit()
    walks = []
    real = loader.walk_assets
    monkeypatch.setattr(loader, "walk_assets", lambda *args, **kwargs: walks.append(1) or real(*args, **kwargs))
    store._scans, store._views, store._exclusions = {}, {}, None
    store.check_source(B)
    store.check_definition(HERO_B)
    assert walks == []  # admission never depends on art
    store.load()
    assert walks  # load() still reports stray names under images/ and placements/


def test_validating_many_subjects_scans_each_source_once(store, monkeypatch):
    txn = store.transaction()
    txn.put(SourceManifest(A, OWNER, heads={}), store.read_source(A).etag)
    for n in range(30):
        txn.put(RecordEnvelope(SourceRef(A, "character", f"c{n}", "r1"), {}, name=f"C{n}"), None)
    txn.commit(handle())
    scans = []
    real = loader.contained_scan
    monkeypatch.setattr(loader, "contained_scan",
                        lambda access, source_id, **kwargs: scans.append(source_id) or real(access, source_id, **kwargs))
    facts = store.source_facts()
    for n in range(30):
        assert store.check_definition(SourceRef(A, "character", f"c{n}"), facts).ref.revision == "r1"
        assert isinstance(store.check_source(A, facts), StoredSource)
    assert scans.count(A) <= 1  # once for the changed source, then cached views


def test_a_transient_read_error_is_never_cached(store, monkeypatch):
    store._scans, store._views, store._exclusions = {}, {}, None
    ops = store._access.ops
    real = ops.read_bytes
    failed = []

    def flaky(path):
        if Path(path).parts[-4:] == ("catalog", "a", "_source", "manifest.json") and not failed:
            failed.append(1)
            raise OSError(32, "sharing violation")
        return real(path)
    monkeypatch.setattr(ops, "read_bytes", flaky)
    assert A in store.excluded_sources()  # the one failing call
    assert A not in store.excluded_sources() and isinstance(store.check_source(A), StoredSource)
    assert store.load().excluded == ()


def test_stale_facts_still_return_the_current_definition(store):
    facts = store.source_facts()
    assert store.check_definition(HERO_A, facts).envelope.name == "Hero"
    revision = store.root / "sources/catalog/a/_source/records/character/hero/r1.json"
    revision.write_text(revision.read_text().replace('"name": "Hero"', '"name": "Hera"'))  # facts predate it
    found = store.check_definition(HERO_A, facts)
    assert found.envelope.name == "Hera" and found.name == "Hera"


def test_a_damaged_campaign_folder_never_raises_a_raw_os_error(store):
    from grimoire.store import AssetRecord, InvalidSourceState
    folder = store.root / "campaigns/c9"
    folder.mkdir(parents=True)
    (folder / "instances").write_text("a file, not a folder")
    assert store.campaign_exists("c9") is False
    blob_free = AssetRecord(ImageRef("campaign:c9", "x"), "s", {})
    txn = store.transaction()
    txn.put(blob_free, None)
    with pytest.raises(InvalidSourceState):
        txn.commit(handle())


# -- round-2 review regressions ------------------------------------------------------------------

def test_a_manifest_edit_that_keeps_its_stat_is_still_seen(store, monkeypatch):
    txn = store.transaction()
    txn.put(SourceManifest(A, OWNER, heads={"character/hero": "r1"}), store.read_source(A).etag)
    txn.put(RecordEnvelope(HERO_A.at("r2"), {}, name="Hero"), None)
    txn.commit(handle())
    # simulate a filesystem whose stat keys miss the edit (coarse times, Windows creation-time ctime)
    monkeypatch.setattr(loader, "source_fingerprint", lambda access, source_id: ("frozen", source_id))
    store._scans, store._views, store._exclusions = {}, {}, None
    assert store.check_definition(HERO_A).ref.revision == "r1"
    manifest = store.root / "sources/catalog/a/_source/manifest.json"
    manifest.write_text(manifest.read_text().replace('"r1"', '"r2"'))  # same size, a hand-made head move
    assert store.check_definition(HERO_A).ref.revision == "r2"


def test_the_fingerprint_records_the_real_spelling_of_manifest_and_records(store):
    import os
    access = store._access
    manifest = store.root / "sources/catalog/a/_source/manifest.json"
    info = manifest.stat()
    before = loader.source_fingerprint(access, A)
    renamed = manifest.with_name("Manifest.json")
    manifest.rename(renamed)  # a case-only rename: same bytes, and the original times restored
    os.utime(renamed, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert renamed.stat().st_size == info.st_size
    assert loader.source_fingerprint(access, A) != before


def test_an_art_only_commit_does_not_rescan_the_source(store, monkeypatch):
    images = ImageLibrary(store, handle(), owner=OWNER)
    images.add_image(B, "warm", PNG).commit()  # warms the cached views
    scans = []
    real = loader.scan_source
    monkeypatch.setattr(loader, "scan_source",
                        lambda access, source_id, **kwargs: scans.append(source_id) or real(access, source_id, **kwargs))
    images.add_image(B, "next", PNG + b"x").commit()
    assert scans == []  # the commit used the cached verdict, not a re-read of every revision


# -- round-3 review regressions ------------------------------------------------------------------

def test_an_art_only_commit_into_a_source_its_own_files_exclude_is_refused(store):
    from grimoire.store import AssetRecord, ImageBlob, InvalidSourceState
    from grimoire.testing.fileops import snapshot
    (store.root / "sources/catalog/a/_source/manifest.json").write_text("{broken")
    before = snapshot(store.root)
    blob = ImageBlob(PNG + b"own-scan")
    txn = store.transaction()
    txn.put(blob, None)
    txn.put(AssetRecord(ImageRef(A, "x"), "s", {"blob": blob.sha256}), None)
    with pytest.raises(InvalidSourceState):
        txn.commit(handle())
    assert snapshot(store.root) == before


def test_an_art_commit_touching_many_sources_verifies_the_library_once(store, monkeypatch):
    from grimoire.store import AssetRecord, ImageBlob
    store.excluded_sources()
    prints = []
    real = loader.source_fingerprint
    monkeypatch.setattr(loader, "source_fingerprint", lambda access, source_id: prints.append(source_id)
                        or real(access, source_id))
    txn = store.transaction()
    for source in (A, B):
        blob = ImageBlob(PNG + source.encode())
        txn.put(blob, None)
        txn.put(AssetRecord(ImageRef(source, "x"), "s", {}), None)
    txn.commit(handle())
    assert len(prints) == 2  # one verification (two sources), not one per touched source


def test_blob_names_accept_exactly_the_sniffers_formats():
    from grimoire.store import MEDIA
    from grimoire.store.layout import BLOB_EXTS, blob_parts
    assert set(BLOB_EXTS) == set(MEDIA)
    for ext in MEDIA:
        assert blob_parts("a" * 64, ext)[-1].endswith("." + ext)


def test_an_empty_folder_with_a_placement_name_is_reported(store):
    folder = store.root / "sources/catalog/b/_source/placements/character/hero/portrait.json"
    folder.mkdir(parents=True)
    assert ("sources/catalog/b/_source/placements/character/hero/portrait.json", "unrecognized") in {
        (p.path, p.code) for p in store.load().problems}
