"""Effective snapshots over the real native store and 107 chats (Sprint 022 proving scenario).

Every fixture is synthetic under ``tmp_path``. No model is called.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace

import pytest
from chat_fixtures import Clock, Ids
from portrayal_fixtures import (
    BASE, BASE_SECTIONS, CAST, MODERN, MODERN_MARA, NAMESAKE, NOTES, VAMPIRE, VAMPIRE_MARA, derived, derives, envelope,
    manifest, seed_elderfen,
)
from store_helpers import OWNER, owner_handle

from grimoire.chats import Launch
from grimoire.chatstore import ChatLibrary
from grimoire.core.entity import EntityDefinition
from grimoire.core.identity import ChatRef
from grimoire.core.facets import Agent, FacetSet
from grimoire.core.portrayal import ChainNode, PortrayalError
from grimoire.core.snapshot import SelectedSource, SourceSelection
from grimoire.effective import chat_selection, chat_snapshot, entity_decoder, selection_snapshot
from grimoire.store import LibraryStore, RecordEnvelope, RecordNotFound
from grimoire.testing.fileops import snapshot as files_of

SRC = os.path.join(os.path.dirname(__file__), "..", "src")


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed_elderfen(opened, owner_handle())
        yield opened


@pytest.fixture
def chats(store):
    return ChatLibrary(store, owner_handle(), clock=Clock(), new_id=Ids(), owner=OWNER)


def reason(call):
    with pytest.raises(PortrayalError) as caught:
        call()
    return caught.value


def selected(*sources, world=None):
    return SourceSelection(world, tuple(SelectedSource(ref, role) for ref, role in sources))


def put(store, *envelopes, expected=None):
    txn = store.transaction()
    for item in envelopes:
        txn.put(item, (expected or {}).get(item.address if isinstance(item, RecordEnvelope) else item.source))
    txn.commit(owner_handle())


# -- the proving scenario ------------------------------------------------------------------------

def test_elderfen_through_the_store(store):
    before = files_of(store.root)
    view = store.load()
    assert view.problems == () and view.broken == ()
    snap = selection_snapshot(view, selected((VAMPIRE_MARA, "character"), (MODERN_MARA, "persona")))
    vampire, modern = snap.effective(VAMPIRE_MARA), snap.effective(MODERN_MARA)
    assert vampire.chain == (ChainNode(VAMPIRE_MARA, view.index.resolve(VAMPIRE_MARA).etag),
                             ChainNode(BASE, view.index.resolve(BASE).etag))
    assert vampire.section("species") == "Vampire, turned in 1891." and vampire.section("mortal-family") is None
    assert "biological" not in vampire.traits and "biological" in modern.traits
    assert modern.section("occupation") == "Data librarian."
    assert modern.section("personality") == vampire.section("personality") == BASE_SECTIONS["personality"]
    nodes = {node.ref: node for node in vampire.chain + modern.chain}
    assert nodes[BASE].etag == view.index.resolve(BASE).etag and nodes[MODERN_MARA].etag is not None
    for effective in (vampire, modern):  # every field names an exact stored revision and its bytes
        assert {entry.source for entry in effective.provenance} <= set(effective.chain)
        assert all(entry.source.etag == view.index.resolve(entry.source.ref).etag for entry in effective.provenance)
    assert vampire.origin("section:species").source == nodes[VAMPIRE_MARA]
    assert vampire.origin("section:personality").source == nodes[BASE]
    assert files_of(store.root) == before  # nothing copied or rewritten


def test_two_snapshots_through_the_decoder_agree(store):
    view = store.load()
    selection = selected((VAMPIRE_MARA, "character"))
    assert selection_snapshot(view, selection).effective(VAMPIRE_MARA) == \
        selection_snapshot(view, selection).effective(VAMPIRE_MARA)


def test_a_removed_manifest_link_is_unlinked_derivation(store):
    put(store, manifest(MODERN), expected={MODERN: store.read_source(MODERN).etag})
    snap = selection_snapshot(store.load(), selected((MODERN_MARA, "character")))
    assert reason(lambda: snap.effective(MODERN_MARA)).reason == "unlinked_derivation"


def test_a_third_party_link_excludes_its_declarer_and_injects_nothing(store):
    folder = store.root / "sources" / "addon" / "fan" / "_source"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({
        "format": "grimoire.source/1", "source": "addon:fan", "owner": OWNER, "heads": {}, "previous": None,
        "links": [{"subject": str(NAMESAKE), "relation": "derived_variant", "object": str(BASE)}]}))
    view = store.load()
    assert "addon:fan" in view.excluded
    assert [p.code for p in view.problems if p.source == "addon:fan"] == ["ownership"]
    standalone = selection_snapshot(view, selected((NAMESAKE, "character"))).effective(NAMESAKE)
    assert [node.ref for node in standalone.chain] == [NAMESAKE] and standalone.ignored_links == ()
    assert selection_snapshot(view, selected((VAMPIRE_MARA, "character"))).effective(VAMPIRE_MARA).ignored_links == ()


def test_a_non_entity_record_is_raw_only(store):
    snap = selection_snapshot(store.load(), selected((NOTES, "lorebook")))
    assert snap.resolve(NOTES).envelope.payload["entries"] == ("the ferry runs at dusk",)
    assert reason(lambda: snap.effective(NOTES)).reason == "not_entity_definition"


def test_an_undecodable_entity_payload_carries_the_codec_reason(store):
    bad = MODERN_MARA.at("r3")
    put(store, RecordEnvelope(bad, {"name": "Mara", "nature": "individual",
                                    "traits": [{"facet": "physical", "colour": "red"}]},
                              name="Mara", extra={"schema": "grimoire.entity/0"}),
        manifest(MODERN, derives(MODERN_MARA), heads={"character/mara": "r1"}),
        expected={MODERN: store.read_source(MODERN).etag})
    snap = selection_snapshot(store.load(), selected((bad, "character")))
    error = reason(lambda: snap.effective(bad))
    assert (error.reason, error.detail) == ("undecodable", "unknown_facet_field")


def test_malformed_sections_never_break_the_load(store):
    broken = EntityDefinition(NAMESAKE.at("r2"), "Mara", "individual", FacetSet([Agent()]), "",
                              {"sections": {"age": 34}})
    put(store, envelope(broken), manifest("world:elsewhere", heads={"character/mara": "r2"}),
        expected={"world:elsewhere": store.read_source("world:elsewhere").etag})
    view = store.load()
    assert view.problems == () and view.broken == ()
    snap = selection_snapshot(view, selected((NAMESAKE.unpinned(), "character")))
    standalone = snap.effective(NAMESAKE.unpinned())  # derives nothing: usable, sections left out visibly
    assert standalone.sections == () and [n.code for n in standalone.notices] == ["invalid_sections"]
    put(store, envelope(derived(MODERN_MARA.at("r2"), sections={"age": 34})),
        manifest(MODERN, derives(MODERN_MARA), derives(MODERN_MARA.at("r2")), heads={"character/mara": "r2"}),
        expected={MODERN: store.read_source(MODERN).etag})
    view = store.load()
    assert view.problems == ()
    snap = selection_snapshot(view, selected((MODERN_MARA.unpinned(), "character")))
    assert reason(lambda: snap.effective(MODERN_MARA.unpinned())).reason == "invalid_sections"


def test_entity_decoder_refuses_other_values():
    with pytest.raises(TypeError):
        entity_decoder("catalog:x/character/y@r1")


# -- 107 chats -----------------------------------------------------------------------------------

def test_a_world_less_chat_snapshot(store, chats):
    started = chats.start_chat(Launch("character", VAMPIRE_MARA), attach=[(NOTES, "lorebook")]).commit()
    [actor] = started.actors
    captured = chat_snapshot(store.load(), started.chat)
    assert captured.problems == () and captured.scope.world is None and captured.snapshot.world is None
    assert [(r.source.ref, r.source.role, r.source.actor) for r in captured.snapshot.sources()] == [
        (VAMPIRE_MARA, "character", actor), (NOTES, "lorebook", None)]
    entity = captured.snapshot.entity(actor)
    assert entity.resolved_source == VAMPIRE_MARA and "biological" not in entity.facets
    assert captured.snapshot.instance_etag(actor) == store.read(actor).etag


def test_an_absent_chat_is_record_not_found(store, chats):
    started = chats.start_chat(Launch("character", VAMPIRE_MARA)).commit()
    view = store.load()
    with pytest.raises(RecordNotFound):
        chat_snapshot(view, ChatRef(started.chat.campaign_id, "nope"))


def test_a_chat_snapshot_is_frozen_for_the_whole_turn(store, chats):
    started = chats.start_chat(Launch("lorebook", NOTES), attach=[(MODERN_MARA.unpinned(), "character")]).commit()
    [actor] = started.actors
    first = store.load()
    turn = chat_snapshot(first, started.chat).snapshot

    # mid-turn: a new head revision, an edited actor file and a detached launch binding
    put(store, envelope(derived(MODERN_MARA.at("r2"), sections={"age": "Thirty."})),
        manifest(MODERN, derives(MODERN_MARA), derives(MODERN_MARA.at("r2")), heads={"character/mara": "r2"}),
        expected={MODERN: store.read_source(MODERN).etag})
    stored = store.read(actor)
    put(store, replace(stored.envelope, payload={**stored.envelope.payload, "local_name": "Mara the Elder"}),
        expected={actor: stored.etag})
    _, etag = chats.read_chat(started.chat)
    chats.detach_source(started.chat, NOTES, "lorebook", expected=etag).commit()

    for check in (turn, chat_snapshot(first, started.chat).snapshot):  # two snapshots of one view agree
        assert check.effective(MODERN_MARA.unpinned()).ref == MODERN_MARA
        assert check.effective(MODERN_MARA.unpinned()).section("age") == "Twenty-nine."
        assert check.entity(actor).name == "Mara"
        assert check.resolve(NOTES).ref == NOTES

    later = chat_snapshot(store.load(), started.chat).snapshot
    assert later.effective(MODERN_MARA.unpinned()).ref == MODERN_MARA.at("r2")
    assert later.effective(MODERN_MARA.unpinned()).section("age") == "Thirty."
    assert later.entity(actor).name == "Mara the Elder"
    assert reason(lambda: later.resolve(NOTES)).reason == "not_selected"  # the launch is provenance only
    [resolution] = later.sources()
    assert [(n.code, n.detail) for n in resolution.notices] == [("binding_drift", "r1 -> r2")]


def test_a_reused_actor_rebound_at_another_revision(store, chats):
    started = chats.start_chat(Launch("character", MODERN_MARA.unpinned())).commit()
    [actor] = started.actors
    put(store, envelope(derived(MODERN_MARA.at("r2"), sections={"age": "Thirty."})),
        manifest(MODERN, derives(MODERN_MARA), derives(MODERN_MARA.at("r2")), heads={"character/mara": "r1"}),
        expected={MODERN: store.read_source(MODERN).etag})
    _, etag = chats.read_chat(started.chat)
    chats.detach_source(started.chat, MODERN_MARA.unpinned(), "character", expected=etag).commit()
    _, etag = chats.read_chat(started.chat)
    chats.attach_source(started.chat, MODERN_MARA.at("r2"), "character", expected=etag, actor=actor).commit()
    captured = chat_snapshot(store.load(), started.chat)
    assert captured.snapshot.instance(actor).source == MODERN_MARA.unpinned()  # never rewritten on reuse
    assert captured.snapshot.entity(actor).resolved_source == MODERN_MARA.at("r2")


def test_a_detached_actor_is_not_selected(store, chats):
    started = chats.start_chat(Launch("lorebook", NOTES), attach=[(VAMPIRE_MARA, "character")]).commit()
    [actor] = started.actors
    _, etag = chats.read_chat(started.chat)
    chats.detach_source(started.chat, VAMPIRE_MARA, "character", expected=etag).commit()
    captured = chat_snapshot(store.load(), started.chat)
    assert reason(lambda: captured.snapshot.entity(actor)).reason == "not_selected"
    record, _ = chats.read_chat(started.chat)
    assert chat_selection(record, captured.scope) == captured.snapshot.selection


def test_a_missing_actor_file_is_a_problem_not_a_failure(store, chats):
    started = chats.start_chat(Launch("character", VAMPIRE_MARA)).commit()
    [actor] = started.actors
    (store.root / "campaigns" / actor.campaign_id / "instances" / "character" / f"{actor.instance_id}.json").unlink()
    captured = chat_snapshot(store.load(), started.chat)
    assert captured.problems == (f"missing_actor:{actor}",)
    assert reason(lambda: captured.snapshot.entity(actor)).reason == "unknown_instance"


def test_the_effective_package_loads_no_provider():
    code = ("import json, sys; import grimoire.effective; "
            "print(json.dumps(sorted(m for m in sys.modules if m.split('.')[0] == 'grimoire')))")
    loaded = json.loads(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                                       env={**os.environ, "PYTHONPATH": SRC}).stdout)
    assert "grimoire.store" in loaded and "grimoire.core.snapshot" in loaded  # the check is not vacuous
    assert not [m for m in loaded if m.startswith(("grimoire.providers", "grimoire.capture", "grimoire.evals",
                                                   "grimoire.testing"))]


def actor_file(store, actor):
    return store.root / "campaigns" / actor.campaign_id / "instances" / actor.kind / f"{actor.instance_id}.json"


@pytest.mark.parametrize("damage", ["garbage", "payload"])
def test_an_unreadable_actor_is_a_problem(store, chats, damage):
    started = chats.start_chat(Launch("character", VAMPIRE_MARA)).commit()
    [actor] = started.actors
    if damage == "garbage":
        actor_file(store, actor).write_text("{not json")
    else:
        stored = store.read(actor)
        put(store, replace(stored.envelope, payload={**stored.envelope.payload, "state": [{"state": "mood"}]}),
            expected={actor: stored.etag})
    captured = chat_snapshot(store.load(), started.chat)
    assert captured.problems == (f"undecodable_actor:{actor}",)


def test_a_missing_or_unreadable_scope_is_a_problem(store, chats):
    started = chats.start_chat(Launch("character", VAMPIRE_MARA)).commit()
    scope_file = store.root / "campaigns" / started.scope.campaign_id / "scope.json"
    scope_file.write_text("{not json")
    damaged = chat_snapshot(store.load(), started.chat)
    assert damaged.problems == ("undecodable_scope",) and damaged.scope is None and damaged.snapshot.world is None
    scope_file.unlink()
    orphan = chat_snapshot(store.load(), started.chat)
    assert orphan.problems == ("orphan_chat",)
    [actor] = started.actors
    assert orphan.snapshot.entity(actor).resolved_source == VAMPIRE_MARA  # still usable


def test_a_reverse_link_from_the_base_source_surfaces_as_unlinked_derivation(store):
    """The loader drops the portrayal's cycle-closing link (STORAGE link order): visible, never silent."""
    put(store, manifest(CAST, derives(BASE, MODERN_MARA)), expected={CAST: store.read_source(CAST).etag})
    view = store.load()
    assert [(p.code, p.source) for p in view.problems] == [("bad_link", MODERN)]
    snap = selection_snapshot(view, selected((MODERN_MARA, "character")))
    assert reason(lambda: snap.effective(MODERN_MARA)).reason == "unlinked_derivation"


def test_find_by_name_ignores_the_envelopes_display_name(store):
    stored = store.read(VAMPIRE_MARA.at("r1"))
    renamed = EntityDefinition(VAMPIRE_MARA.at("r2"), "Mira", "individual", FacetSet([Agent()]), "",
                               {"derivation": {"base": str(BASE)}})
    put(store, replace(envelope(renamed), name=None),
        manifest(VAMPIRE, derives(VAMPIRE_MARA), derives(VAMPIRE_MARA.at("r2")), heads={"character/mara": "r2"}),
        expected={VAMPIRE: store.read_source(VAMPIRE).etag})
    assert stored.envelope.name == "Mara"
    snap = selection_snapshot(store.load(), selected((VAMPIRE_MARA.unpinned(), "character")))
    assert snap.effective(VAMPIRE_MARA.unpinned()).name == "Mira"
    assert snap.find_by_name("Mira") == (VAMPIRE_MARA.unpinned(),) and snap.find_by_name("") == ()


def test_a_chat_with_a_world_still_exposes_only_its_bindings(store, chats):
    started = chats.start_chat(Launch("character", MODERN_MARA)).commit()
    scope, etag = chats.read_scope(started.scope)
    chats.attach_world(started.scope, VAMPIRE, expected=etag).commit()
    captured = chat_snapshot(store.load(), started.chat)
    assert captured.problems == () and captured.snapshot.world == VAMPIRE
    assert reason(lambda: captured.snapshot.resolve(VAMPIRE_MARA)).reason == "not_selected"
    assert captured.snapshot.effective(MODERN_MARA).ref == MODERN_MARA


def test_an_undecodable_scope_has_no_etag(store, chats):
    started = chats.start_chat(Launch("character", VAMPIRE_MARA)).commit()
    path = store.root / "campaigns" / started.scope.campaign_id / "scope.json"
    document = json.loads(path.read_text())
    document["schema"] = "grimoire.play-scope/99"  # a future version this one cannot interpret
    path.write_text(json.dumps(document))
    captured = chat_snapshot(store.load(), started.chat)
    assert captured.problems == ("undecodable_scope",) and (captured.scope, captured.scope_etag) == (None, None)


def test_an_excluded_base_source_is_reported_as_excluded(store):
    """The base exists on disk but its source is excluded: the fix is its manifest, not a restore."""
    path = store.root / "sources" / "catalog" / "elderfen-cast" / "_source" / "manifest.json"
    document = json.loads(path.read_text())
    document["links"] = [{"subject": str(NAMESAKE), "relation": "alternate_portrayal", "object": str(BASE)}]
    path.write_text(json.dumps(document))
    view = store.load()
    assert CAST in view.excluded
    error = reason(lambda: selection_snapshot(view, selected((VAMPIRE_MARA, "character"))).effective(VAMPIRE_MARA))
    assert (error.reason, error.detail) == ("missing_base", "excluded_source")


def test_a_newer_entity_schema_is_undecodable_not_a_lorebook(store):
    """A record a newer client wrote is an entity this version cannot read: it is never ancestry-free."""
    stored = store.read(VAMPIRE_MARA)
    path = store.root / "sources" / "world" / "elderfen-vampire" / "_source" / "records" / "character" / "mara" / "r1.json"
    document = json.loads(path.read_text())
    document["schema"] = "grimoire.entity/1"
    document["name"] = "Countess Mara"
    path.write_text(json.dumps(document))
    assert stored.envelope.extra["schema"] == "grimoire.entity/0"
    snap = selection_snapshot(store.load(), selected((VAMPIRE_MARA, "character")))
    error = reason(lambda: snap.effective(VAMPIRE_MARA))
    assert (error.reason, error.detail) == ("undecodable", "unsupported_schema")
    assert reason(lambda: snap.resolve(BASE)).reason == "ancestry_unavailable"
    assert snap.find_by_name("Countess Mara") == ()


def test_an_unreadable_base_revision_is_not_reported_missing(store):
    path = store.root / "sources" / "catalog" / "elderfen-cast" / "_source" / "records" / "character" / "mara" / "r1.json"
    put(store, envelope(EntityDefinition(BASE.at("r2"), "Mara", "individual", FacetSet([Agent()]))),
        manifest(CAST, heads={"character/mara": "r2"}), expected={CAST: store.read_source(CAST).etag})
    path.write_text("{not json")
    view = store.load()
    assert [p.code for p in view.problems] == ["malformed"]
    error = reason(lambda: selection_snapshot(view, selected((MODERN_MARA, "character"))).effective(MODERN_MARA))
    assert (error.reason, error.detail) == ("missing_base", "unreadable_revision")


def test_a_bases_only_revision_unreadable_is_unreadable_not_dropped(store):
    """The loader drops an entity with no readable revision; the fix is still that one file, not a restore."""
    path = store.root / "sources" / "catalog" / "elderfen-cast" / "_source" / "records" / "character" / "mara" / "r1.json"
    path.write_text("{not json")
    view = store.load()
    assert BASE.unpinned() in [ref.unpinned() for ref in view.dropped]  # the loader did drop the entity
    error = reason(lambda: selection_snapshot(view, selected((MODERN_MARA, "character"))).effective(MODERN_MARA))
    assert (error.reason, error.detail) == ("missing_base", "unreadable_revision")


MARA_FILES = ("sources", "catalog", "elderfen-cast", "_source", "records", "character", "mara")


def add_base_r2(store, heads):
    """A second revision of the base entity, with ``heads`` as the manifest's declared heads."""
    put(store, envelope(EntityDefinition(BASE.at("r2"), "Mara", "individual", FacetSet([Agent()]))),
        manifest(CAST, heads=heads), expected={CAST: store.read_source(CAST).etag})


def base_explanation(store):
    view = store.load()
    error = reason(lambda: selection_snapshot(view, selected((MODERN_MARA, "character"))).effective(MODERN_MARA))
    assert error.reason == "missing_base"
    return error.detail


def test_an_unreadable_declared_head_base_is_unreadable_not_dropped(store):
    """The base is the declared head and unreadable: the loader's bad_head is only its consequence."""
    add_base_r2(store, {"character/mara": "r1"})
    store.root.joinpath(*MARA_FILES, "r1.json").write_text("{not json")
    assert base_explanation(store) == "unreadable_revision"


def test_an_unreadable_base_under_a_head_naming_another_revision_is_dropped(store):
    """Repairing the base alone would not bring it back (the head names a deleted r2), so the drop wins."""
    add_base_r2(store, {"character/mara": "r2"})
    r1 = store.root.joinpath(*MARA_FILES, "r1.json")
    good = r1.read_text()
    store.root.joinpath(*MARA_FILES, "r2.json").unlink()
    r1.write_text("{not json")
    assert base_explanation(store) == "entity_dropped"
    r1.write_text(good)  # the file is fine again; the entity is still dropped for its head
    assert base_explanation(store) == "entity_dropped"


def test_an_unreadable_base_beside_another_revision_and_no_head_is_dropped(store):
    """Two revision files and no head is the loader's missing_head, whatever state r1 is in."""
    add_base_r2(store, {"character/mara": "r2"})
    path = store.root.joinpath("sources", "catalog", "elderfen-cast", "_source", "manifest.json")
    document = json.loads(path.read_text())
    document["heads"] = {}
    path.write_text(json.dumps(document))
    store.root.joinpath(*MARA_FILES, "r1.json").write_text("{not json")
    view = store.load()
    assert "missing_head" in [problem.code for problem in view.problems]
    assert base_explanation(store) == "entity_dropped"


def test_an_unreadable_base_whose_id_is_reused_by_another_kind_is_dropped(store):
    """kind_reuse drops the entity whatever its files hold, so repairing the base alone would not help."""
    other = store.root.joinpath(*MARA_FILES[:-2], "lorebook", "mara")
    other.mkdir(parents=True)
    other.joinpath("r1.json").write_text("{not json")
    r1 = store.root.joinpath(*MARA_FILES, "r1.json")
    good = r1.read_text()
    r1.write_text("{not json")
    assert "kind_reuse" in [problem.code for problem in store.load().problems]
    assert base_explanation(store) == "entity_dropped"
    r1.write_text(good)
    assert base_explanation(store) == "entity_dropped"


def test_a_missing_head_elsewhere_in_the_source_is_read_conservatively(store):
    """The base's only revision is unreadable, but another entity of its source reports missing_head,
    which cannot be told from one for the base without parsing prose: the true, less specific reason."""
    zed = store.root.joinpath(*MARA_FILES[:-1], "zed")
    zed.mkdir(parents=True)
    zed.joinpath("r1.json").write_text("{not json")
    zed.joinpath("r2.json").write_text("{not json")
    store.root.joinpath(*MARA_FILES, "r1.json").write_text("{not json")
    assert "missing_head" in [problem.code for problem in store.load().problems]
    assert base_explanation(store) == "entity_dropped"
