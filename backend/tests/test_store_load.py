"""Reopening a library: resolution, pinned versus following instances, and contained damage."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.store import (
    InvalidSourceState, LibraryStore, LinkSpec, RecordEnvelope, SourceManifest, StoredDefinition, etag,
)
from grimoire.testing import RootedFileOps
from grimoire.testing.fileops import snapshot

from store_helpers import (
    ADDON, HARBOR, OWNER, WREN, WREN_A, WREN_B, WREN_R1, WREN_R2, harbor_manifest, instance, make_dir_link,
    owner_handle, seed, wren,
)

HARBOR_DIR = Path("sources/world/harbor/_source")
ALT = SourceRef(ADDON, "character", "wren-alt", "1")


@pytest.fixture
def library(tmp_path):
    store = LibraryStore.create(tmp_path / "lib")
    seed(store)
    yield store
    store.close()


def _commit(store, *puts):
    txn = store.transaction()
    for envelope, expected in puts:
        txn.put(envelope, expected)
    return txn.commit(owner_handle())


def _codes(view):
    return sorted((problem.code, problem.source) for problem in view.problems)


def _write(root: Path, relative: str, document) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(document if isinstance(document, bytes) else json.dumps(document).encode())


def test_a_clean_library_resolves_with_nothing_reported(library):
    _commit(library, (instance(InstanceRef("c6", "character", "wren-c"), WREN_R1,
                               base_etag=library.read(WREN_R1).etag), None))
    view = library.load()
    assert view.problems == () and view.broken == () and view.excluded == ()
    definition = view.index.resolve(WREN)
    assert isinstance(definition, StoredDefinition) and definition.name == "Wren"
    assert definition.envelope.payload["bio"] == "a sailor"
    assert set(view.instances) == {WREN_A, WREN_B, InstanceRef("c6", "character", "wren-c")}
    assert view.index.find_by_name("wren") == (WREN,)


def test_a_world_change_never_rewrites_a_pinned_instance(library):
    root = library.root
    before = {name: (root / name).read_bytes() for name in (
        "campaigns/c1/instances/character/wren-a.json", "campaigns/c2/instances/character/wren-b.json",
        str(HARBOR_DIR / "records/character/wren/r1.json"))}
    _commit(library, (wren("r2", "a captain"), None),
            (harbor_manifest({"character/wren": "r2"}), library.read_source(HARBOR).etag))
    with LibraryStore.open(root, writable=False) as reopened:
        view = reopened.load()
        assert view.problems == ()
        a, b = view.instances[WREN_A].envelope, view.instances[WREN_B].envelope
        assert view.index.resolve(SourceRef.parse(a.derived_from)).envelope.payload["bio"] == "a sailor"
        assert view.index.resolve(SourceRef.parse(b.derived_from)).ref == WREN_R2
        assert b.base == str(WREN_R1)  # the retained common ancestor is untouched
    assert {name: (root / name).read_bytes() for name in before} == before


def test_removing_an_add_on_reports_missing_source_and_changes_no_byte(library):
    root = library.root
    _commit(library,
            (SourceManifest(ADDON, "human:fan"), None),
            (RecordEnvelope(ALT, {"art": "fan"}, name="Wren (fan)"), None),
            (harbor_manifest(links=[LinkSpec(str(WREN), "alternate_portrayal", str(ALT))]),
             library.read_source(HARBOR).etag),
            (instance(InstanceRef("c3", "character", "alt"), ALT, base=ALT), None))
    assert library.load().broken == ()
    shutil.rmtree(root / "sources" / "addon" / "fan")  # the add-on's whole folder (and any sub-sources)
    before = snapshot(root)
    view = library.load()
    assert snapshot(root) == before
    assert sorted((b.field, b.text, b.reason) for b in view.broken) == [
        ("base", str(ALT), "missing_source"), ("derived_from", str(ALT), "missing_source"),
        ("links.object", str(ALT), "missing_source")]
    assert view.index.resolve(WREN).ref == WREN_R1


@pytest.mark.parametrize("smuggle", ["foreign-subject", "declared-by", "foreign-record"])
def test_an_add_on_cannot_speak_for_another_source(library, smuggle):
    root = library.root
    manifest = {"format": "grimoire.source/1", "source": ADDON, "owner": "human:fan", "heads": {}, "links": []}
    if smuggle == "foreign-subject":
        manifest["links"] = [{"subject": str(WREN_R1), "relation": "overrides", "object": str(ALT)}]
    elif smuggle == "declared-by":
        manifest["links"] = [{"subject": str(ALT), "relation": "same_individual", "object": str(WREN),
                              "declared_by": HARBOR}]
    else:
        forged = json.loads((root / HARBOR_DIR / "records/character/wren/r1.json").read_bytes())
        forged["address"] = str(WREN.at("r9"))
        _write(root, "sources/addon/fan/_source/records/character/wren/r9.json", forged)
    _write(root, "sources/addon/fan/_source/manifest.json", manifest)
    view = library.load()
    assert view.excluded == (ADDON,)
    assert ("ownership", ADDON) in _codes(view)
    assert view.index.resolve(WREN).ref == WREN_R1  # harbor keeps its own head


def test_head_problems_drop_only_their_entity(library):
    root = library.root
    rival = SourceRef(HARBOR, "character", "rival", "r1")
    _commit(library, (RecordEnvelope(rival, {}, name="Rival"), None))
    # hand edits: a second wren revision without a head, and a head naming a missing revision
    second = json.loads((root / HARBOR_DIR / "records/character/wren/r1.json").read_bytes())
    second["address"] = str(WREN_R2)
    _write(root, str(HARBOR_DIR / "records/character/wren/r2.json"), second)
    view = library.load()
    assert ("missing_head", HARBOR) in _codes(view)
    assert view.index.resolve(rival.unpinned()).ref == rival
    assert WREN in view.dropped
    reasons = {(b.field, b.reason) for b in view.broken}
    assert ("derived_from", "entity_dropped") in reasons
    manifest = json.loads((root / HARBOR_DIR / "manifest.json").read_bytes())
    manifest["heads"] = {"character/wren": "r7", "character/ghost": "r1"}
    _write(root, str(HARBOR_DIR / "manifest.json"), manifest)
    assert [code for code, _ in _codes(library.load())].count("bad_head") == 2


def test_a_malformed_revision_still_counts_toward_needing_a_head(library):
    _write(library.root, str(HARBOR_DIR / "records/character/wren/r0.json"), b"{not json")
    view = library.load()
    assert ("malformed", HARBOR) in _codes(view) and ("missing_head", HARBOR) in _codes(view)
    assert WREN in view.dropped  # r1 does not silently become the head


def test_kind_reuse_drops_both_entities(library):
    item = SourceRef(HARBOR, "item", "wren", "r1")
    _write(library.root, str(HARBOR_DIR / "records/item/wren/r1.json"),
           {"format": "grimoire.record/1", "address": str(item), "payload": {}})
    view = library.load()
    assert ("kind_reuse", HARBOR) in _codes(view)
    assert {WREN, item.unpinned()} <= set(view.dropped)


def test_commits_refuse_new_problems_but_allow_unrelated_repairs(library):
    with pytest.raises(InvalidSourceState, match="no head"):
        _commit(library, (wren("r2"), None))
    with pytest.raises(InvalidSourceState, match="missing or unreadable"):
        _commit(library, (harbor_manifest({"character/wren": "r5"}), library.read_source(HARBOR).etag))
    with pytest.raises(InvalidSourceState, match="reuses entity id"):
        _commit(library, (RecordEnvelope(SourceRef(HARBOR, "item", "wren", "r1"), {}), None))
    with pytest.raises(InvalidSourceState, match="no manifest"):
        _commit(library, (RecordEnvelope(SourceRef("world:nowhere", "k", "e", "r1"), {}), None))
    # a pre-existing hand-made problem elsewhere does not block an unrelated write
    _write(library.root, str(HARBOR_DIR / "records/item/rival/r1.json"),
           {"format": "grimoire.record/1", "address": f"{HARBOR}/item/rival@r1", "payload": {}})
    _write(library.root, str(HARBOR_DIR / "records/character/rival/r1.json"),
           {"format": "grimoire.record/1", "address": f"{HARBOR}/character/rival@r1", "payload": {}})
    _commit(library, (RecordEnvelope(SourceRef(HARBOR, "character", "gull", "r1"), {}), None))
    assert library.load().index.resolve(SourceRef(HARBOR, "character", "gull")).ref.revision == "r1"


def test_a_cross_source_link_closing_a_cycle_is_dropped_not_the_source(library):
    root = library.root
    _commit(library, (SourceManifest(ADDON, "human:fan"), None), (RecordEnvelope(ALT, {}), None))
    _commit(library, (harbor_manifest(links=[LinkSpec(str(WREN_R1), "derived_variant", str(ALT))]),
                      library.read_source(HARBOR).etag))
    addon = json.loads((root / "sources/addon/fan/_source/manifest.json").read_bytes())
    addon["links"] = [{"subject": str(ALT), "relation": "derived_variant", "object": str(WREN_R1)}]
    _write(root, "sources/addon/fan/_source/manifest.json", addon)
    view = library.load()
    assert view.excluded == ()
    dropped = [p for p in view.problems if p.code == "bad_link"]
    # documented order: cross-source links register by declaring source, so addon:fan's comes first
    assert [p.source for p in dropped] == [HARBOR] and "cycle" in dropped[0].detail
    assert len(view.index.links_for(WREN)) == 1


@pytest.mark.parametrize("link", [
    {"subject": str(WREN), "relation": "same_individual", "object": str(WREN_R1)},
    {"subject": str(WREN), "relation": "rival_of", "object": str(ALT)},
    {"subject": str(WREN), "relation": "derived_variant", "object": str(ALT)},
    {"subject": "Wren", "relation": "same_individual", "object": str(ALT)},
], ids=["self", "unknown-relation", "unpinned-directed", "unparsable"])
def test_bad_links_are_dropped_and_reported(library, link):
    manifest = json.loads((library.root / HARBOR_DIR / "manifest.json").read_bytes())
    manifest["links"] = [link, dict(link)] if link["relation"] == "same_individual" else [link]
    _write(library.root, str(HARBOR_DIR / "manifest.json"), manifest)
    view = library.load()
    assert view.excluded == () and ("bad_link", HARBOR) in _codes(view)


def test_duplicate_links_keep_one(library):
    link = {"subject": str(WREN), "relation": "same_individual", "object": str(ALT)}
    manifest = json.loads((library.root / HARBOR_DIR / "manifest.json").read_bytes())
    manifest["links"] = [link, dict(link)]
    _write(library.root, str(HARBOR_DIR / "manifest.json"), manifest)
    view = library.load()
    assert [p.code for p in view.problems] == ["bad_link"] and len(view.index.links_for(WREN)) == 1
    assert [(b.reason, b.text) for b in view.broken] == [("missing_source", str(ALT))]


@pytest.mark.parametrize("content, code", [
    (b'{"a": 1, "a": 1}', "malformed"),
    (b"\xef\xbb\xbf{}", "malformed"),
    (b'{"format": "grimoire.record/1", "address": "campaign:c1/character/wren-a", "payload": NaN}', "malformed"),
    (b'{"format": "grimoire.record/2", "address": "campaign:c1/character/wren-a", "payload": 1}',
     "unsupported_format"),
    (b'{"format": "grimoire.record/1", "address": "campaign:c1/character/other", "payload": 1}',
     "address_mismatch"),
])
def test_bad_instance_files_are_reported_and_never_rewritten(library, content, code):
    path = library.root / "campaigns/c1/instances/character/wren-a.json"
    path.write_bytes(content)
    view = library.load()
    assert [p.code for p in view.problems] == [code] and WREN_A not in view.instances
    assert path.read_bytes() == content
    with pytest.raises(Exception) as error:
        library.read(WREN_A)
    assert getattr(error.value, "etag", None) == etag(content)


def test_a_malformed_instance_can_be_replaced_with_its_exact_etag(library):
    path = library.root / "campaigns/c1/instances/character/wren-a.json"
    path.write_bytes(b"{broken")
    _commit(library, (instance(WREN_A, WREN_R1, hp=5), etag(b"{broken")))
    assert library.read(WREN_A).envelope.payload["hp"] == 5


def test_manifest_problems_exclude_the_source(library):
    manifest = json.loads((library.root / HARBOR_DIR / "manifest.json").read_bytes())
    manifest["source"] = "world:elsewhere"
    _write(library.root, str(HARBOR_DIR / "manifest.json"), manifest)
    view = library.load()
    assert view.excluded == (HARBOR,)
    assert {b.reason for b in view.broken} == {"excluded_source"}


def test_reference_reasons_are_precise(library):
    root = library.root
    _write(root, "campaigns/c4/instances/character/x.json", {
        "format": "grimoire.record/1", "address": "campaign:c4/character/x", "payload": {},
        "derived_from": "Wren the Sailor", "base": f"{HARBOR}/character/nobody@r1"})
    _write(root, "campaigns/c5/instances/character/y.json", {
        "format": "grimoire.record/1", "address": "campaign:c5/character/y", "payload": {},
        "derived_from": str(WREN.at("r9")), "base": str(WREN_R1), "base_etag": "sha256:" + "0" * 64})
    reasons = {(b.path.split("/")[1], b.field, b.reason) for b in library.load().broken}
    assert reasons == {("c4", "derived_from", "invalid_reference"), ("c4", "base", "missing_entity"),
                       ("c5", "derived_from", "missing_revision"), ("c5", "base_etag", "base_drift")}


def test_noise_reserved_and_empty_entries_are_quiet_and_strangers_are_reported(library):
    root = library.root
    for name in (".DS_Store", "sources/.DS_Store", "campaigns/c1/instances/character/wren-a.json~",
                 "authority/grants.json", "campaigns/c1/events/e1.json"):
        _write(root, name, b"x")
    (root / "campaigns" / "c9" / "instances" / "character").mkdir(parents=True)  # empty: not state
    assert library.load().problems == ()
    _write(root, "notes.md", b"mine")
    # no existing look-alike: on a case-insensitive filesystem `C1` would *be* `c1`
    _write(root, "campaigns/C8/instances/character/x.json", b"{}")
    _write(root, "sources/world/_misc/readme.md", b"x")
    codes = {(p.path, p.code) for p in library.load().problems}
    assert codes == {("notes.md", "unrecognized"), ("campaigns/C8", "name_mismatch"),
                     ("sources/world/_misc", "unrecognized")}


def test_load_never_follows_a_link(library, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "_source").mkdir(parents=True)
    (elsewhere / "_source" / "manifest.json").write_text("{}")
    make_dir_link(library.root / "sources" / "addon", elsewhere)
    ops = RootedFileOps(library.root, record=False)  # raises on any operation through a link
    with LibraryStore.open(library.root, ops=ops, writable=False) as reader:
        view = reader.load()
    assert ops.sandbox_violation is None
    assert [(p.path, p.code) for p in view.problems] == [("sources/addon", "link")]


# -- regressions from the implementation review ------------------------------------------

@pytest.mark.parametrize("stray", ["record-is-dir", "manifest-is-dir", "source-dir-is-file", "instance-is-dir",
                                   "fifo"])
def test_a_stray_entry_in_an_add_on_never_crashes_or_hangs_a_load(library, stray):
    root = library.root
    _commit(library, (SourceManifest(ADDON, "human:fan"), None), (RecordEnvelope(ALT, {}), None))
    addon = root / "sources/addon/fan/_source"
    if stray == "record-is-dir":
        (addon / "records/character/wren-alt/2.json").mkdir()
        (addon / "records/character/wren-alt/2.json/x").write_text("x")
    elif stray == "manifest-is-dir":
        (addon / "manifest.json").unlink()
        (addon / "manifest.json").mkdir()
        (addon / "manifest.json" / "x").write_text("x")
    elif stray == "source-dir-is-file":
        shutil.rmtree(addon)
        addon.write_text("not a directory")
    elif stray == "instance-is-dir":
        (root / "campaigns/c1/instances/character/odd.json").mkdir()
        (root / "campaigns/c1/instances/character/odd.json/x").write_text("x")
    else:
        if not hasattr(__import__("os"), "mkfifo"):
            pytest.skip("no FIFOs here")
        __import__("os").mkfifo(addon / "records/character/wren-alt/2.json")
    view = library.load()
    assert view.index.resolve(WREN).ref == WREN_R1  # the user's world is unaffected
    assert view.problems


@pytest.mark.parametrize("name", ["sources", "campaigns"])
def test_a_root_entry_that_is_a_file_is_reported_not_fatal(tmp_path, name):
    store = LibraryStore.create(tmp_path / "lib")
    (tmp_path / "lib" / name).write_text("oops")
    assert [(p.path, p.code) for p in store.load().problems] == [(name, "unrecognized")]
    store.close()


def test_a_root_level_link_is_never_listed(library, tmp_path):
    evil = tmp_path / "evil"
    (evil / "world" / "evil" / "_source").mkdir(parents=True)
    (evil / "world" / "evil" / "_source" / "manifest.json").write_text("{}")
    shutil.rmtree(library.root / "sources")
    make_dir_link(library.root / "sources", evil)
    ops = RootedFileOps(library.root, record=False)
    with LibraryStore.open(library.root, ops=ops, writable=False) as reader:
        view = reader.load()
    assert ops.sandbox_violation is None
    assert [(p.path, p.code) for p in view.problems] == [("sources", "link")] and view.excluded == ()


@pytest.mark.parametrize("heads", [{"wren": "r1"}, {"character/wren": "R1!"}])
def test_a_mistyped_head_key_can_be_repaired_through_the_store(library, heads):
    path = library.root / HARBOR_DIR / "manifest.json"
    manifest = json.loads(path.read_bytes())
    manifest["heads"] = heads
    path.write_text(json.dumps(manifest))
    assert library.load().excluded == (HARBOR,)
    _commit(library, (harbor_manifest({"character/wren": "r1"}), etag(path.read_bytes())))
    view = library.load()
    assert view.excluded == () and view.index.resolve(WREN).ref == WREN_R1


@pytest.mark.parametrize("case", ["duplicate-of-existing", "reversed-symmetric", "cycle-within",
                                  "cycle-against-other-source"])
def test_new_links_the_loader_would_drop_are_refused(library, case):
    rival = SourceRef(HARBOR, "character", "rival", "r1")
    _commit(library, (SourceManifest(ADDON, "human:fan"), None), (RecordEnvelope(ALT, {}), None),
            (RecordEnvelope(rival, {}), None))
    symmetric = LinkSpec(str(WREN), "same_individual", str(ALT))
    base = [symmetric]
    _commit(library, (harbor_manifest(links=base), library.read_source(HARBOR).etag))
    if case == "duplicate-of-existing":
        links = base + [LinkSpec(str(WREN), "same_individual", str(ALT))]
    elif case == "reversed-symmetric":
        links = base + [LinkSpec(str(WREN), "same_individual", str(ALT.unpinned()))]
    elif case == "cycle-within":
        links = base + [LinkSpec(str(WREN_R1), "derived_variant", str(rival)),
                        LinkSpec(str(rival), "derived_variant", str(WREN_R1))]
    else:
        _commit(library, (SourceManifest(ADDON, "human:fan", links=(
            LinkSpec(str(ALT), "derived_variant", str(WREN_R1)),)), library.read_source(ADDON).etag))
        links = base + [LinkSpec(str(WREN_R1), "derived_variant", str(ALT))]
    with pytest.raises(InvalidSourceState):
        _commit(library, (harbor_manifest(links=links), library.read_source(HARBOR).etag))
    # a dropped link that already exists on disk does not block an unrelated change
    if case == "cycle-against-other-source":
        path = library.root / HARBOR_DIR / "manifest.json"
        manifest = json.loads(path.read_bytes())
        manifest["links"].append({"subject": str(WREN_R1), "relation": "derived_variant", "object": str(ALT)})
        path.write_text(json.dumps(manifest))
        kept = [LinkSpec(**link) for link in manifest["links"]]
        _commit(library, (harbor_manifest(links=kept, **{"x-notes": "edited"}), etag(path.read_bytes())))


def test_links_of_a_source_the_loader_excludes_do_not_block_a_commit(library):
    zed = SourceRef(HARBOR, "character", "zed", "r1")
    why = SourceRef(ADDON, "character", "why", "r1")
    _commit(library, (RecordEnvelope(zed, {}), None),
            (SourceManifest(ADDON, "human:fan", links=(LinkSpec(str(why), "derived_variant", str(WREN_R1)),)),
             None), (RecordEnvelope(why, {}), None))
    _commit(library, (harbor_manifest(links=[LinkSpec(str(zed), "derived_variant", str(why))]),
                      library.read_source(HARBOR).etag))
    path = library.root / "sources/addon/fan/_source/manifest.json"
    addon = json.loads(path.read_bytes())
    addon["heads"] = {"why": "r1"}  # a hand-made bad head key: the loader excludes the add-on
    path.write_text(json.dumps(addon))
    links = [LinkSpec(str(zed), "derived_variant", str(why)), LinkSpec(str(WREN_R1), "derived_variant", str(zed))]
    _commit(library, (harbor_manifest(links=links), library.read_source(HARBOR).etag))
    assert library.load().excluded == (ADDON,)


def test_in_source_links_register_before_cross_source_ones(library):
    """A world's own link survives an add-on cycle; the dropped link is a cross-source one."""
    a, b = SourceRef(HARBOR, "character", "a", "r1"), SourceRef(HARBOR, "character", "b", "r1")
    c = SourceRef(ADDON, "character", "c", "r1")
    _commit(library, (RecordEnvelope(a, {}), None), (RecordEnvelope(b, {}), None),
            (SourceManifest(ADDON, "human:fan"), None), (RecordEnvelope(c, {}), None))
    _commit(library, (harbor_manifest(links=[LinkSpec(str(b), "derived_variant", str(c)),
                                             LinkSpec(str(a), "derived_variant", str(b))]),
                      library.read_source(HARBOR).etag))
    path = library.root / "sources/addon/fan/_source/manifest.json"
    addon = json.loads(path.read_bytes())
    addon["links"] = [{"subject": str(c), "relation": "derived_variant", "object": str(a)}]
    path.write_text(json.dumps(addon))
    view = library.load()
    kept = {(str(link.subject), str(link.object)) for link in view.index.links_for(a.unpinned())}
    assert (str(a), str(b)) in kept
    assert [p.code for p in view.problems] == ["bad_link"]


def test_an_unreadable_source_is_excluded_and_the_rest_still_loads(library, monkeypatch):
    import os as _os
    _commit(library, (SourceManifest(ADDON, "human:fan"), None), (RecordEnvelope(ALT, {}), None))
    real = _os.lstat
    denied = str(library.root / "sources" / "addon" / "fan" / "_source" / "manifest.json")

    def lstat(path, *args, **kwargs):  # a `_source` folder the reader may list but not search
        if str(path) == denied:
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, *args, **kwargs)
    monkeypatch.setattr(_os, "lstat", lstat)
    view = library.load()
    assert view.excluded == (ADDON,) and view.index.resolve(WREN).ref == WREN_R1


def test_a_linked_records_folder_is_never_listed(library, tmp_path):
    _commit(library, (SourceManifest(ADDON, "human:fan"), None), (RecordEnvelope(ALT, {}), None))
    records = library.root / "sources/addon/fan/_source/records"
    shutil.move(str(records), str(tmp_path / "elsewhere"))
    make_dir_link(records, tmp_path / "elsewhere")
    ops = RootedFileOps(library.root, record=False)  # raises on any operation through a link
    with LibraryStore.open(library.root, ops=ops, writable=False) as reader:
        view = reader.load()
    assert ops.sandbox_violation is None
    assert ("sources/addon/fan/_source/records", "link") in {(p.path, p.code) for p in view.problems}


def test_links_are_validated_against_every_manifest_in_the_transaction(library):
    a, b = SourceRef("world:a", "k", "x", "r1"), SourceRef("world:b", "k", "y", "r1")
    with pytest.raises(InvalidSourceState, match="cycle"):
        _commit(library,
                (SourceManifest("world:a", OWNER, links=(LinkSpec(str(a), "derived_variant", str(b)),)), None),
                (SourceManifest("world:b", OWNER, links=(LinkSpec(str(b), "derived_variant", str(a)),)), None),
                (RecordEnvelope(a, {}), None), (RecordEnvelope(b, {}), None))


def test_links_of_a_source_excluded_for_a_foreign_record_do_not_block_a_commit(library):
    a, b = SourceRef("world:a", "k", "x", "r1"), SourceRef("world:b", "k", "y", "r1")
    _commit(library, (SourceManifest("world:a", OWNER, links=(LinkSpec(str(a), "derived_variant", str(b)),)), None),
            (RecordEnvelope(a, {}), None), (SourceManifest("world:b", OWNER), None), (RecordEnvelope(b, {}), None))
    forged = {"format": "grimoire.record/1", "address": str(WREN.at("r9")), "payload": {}}
    _write(library.root, "sources/world/a/_source/records/k/z/r1.json", forged)  # claims harbor's entity
    assert "world:a" in library.load().excluded
    _commit(library, (SourceManifest("world:b", OWNER, links=(LinkSpec(str(b), "derived_variant", str(a)),)),
                      library.read_source("world:b").etag))
