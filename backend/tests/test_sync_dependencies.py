"""Sync's dependency rule, end to end (brutal-review rounds 8-9): each rule here is pinned by a test.

A copy lands only if what it pins and names holds on the destination (docs/SYNC.md, "Head
dependency"), rechecked to a fixpoint after refused units and denials; knowledge logs move as one
unit; resolve refuses what sync would hold; an accepted folder holds relations in any spelling.
"""

from __future__ import annotations

import pytest

from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.store import LibraryStore, RecordEnvelope, SourceManifest, etag
from grimoire.sync import ResolveRefused, identify, resolve, sync

from store_helpers import OWNER, instance, owner_handle
from sync_helpers import DESKTOP, PHONE, handle, handles, make_pair, put


def _chat_pair(tmp_path):
    from chat_fixtures import MARA, chat_library, seed_sources
    from grimoire.chats.model import Launch
    desktop, phone = LibraryStore.create(tmp_path / DESKTOP), LibraryStore.create(tmp_path / PHONE)
    seed_sources(desktop)
    identify(desktop, DESKTOP)
    identify(phone, PHONE)
    sync(desktop, phone, handles())
    return desktop, phone


def test_d4_a_chat_never_lands_without_an_actor_that_collided(tmp_path):
    """Kills D4 (actor check removed): the actor is a name_collision, so the chat must wait."""
    from chat_fixtures import MARA, chat_library
    from grimoire.chats.model import Launch
    desktop, phone = _chat_pair(tmp_path)
    try:
        started = chat_library(desktop).start_chat(Launch("character", MARA)).commit()
        folder = phone.root / "campaigns" / "q-1" / "instances" / "character"
        folder.mkdir(parents=True)
        (folder / "Mara-1.json").write_bytes(b"{}")
        report = sync(desktop, phone, handles())
        actor = "campaigns/q-1/instances/character/mara-1.json"
        chat = "campaigns/q-1/chats/t-1.json"
        assert report.conflict(actor).code == "name_collision"
        assert report.conflict(chat).code == "head_dependency", report.conflicts
        assert not (phone.root / chat).exists()
    finally:
        desktop.close()
        phone.close()


def test_d3_two_hop_dependency_chat_actor_revision(tmp_path):
    """Kills D3 (one pass, no fixpoint): actor pins a revision that differs; the chat names the actor."""
    from chat_fixtures import CARDS, MARA, chat_library
    from grimoire.chats.model import Launch
    desktop, phone = _chat_pair(tmp_path)
    try:
        txn = desktop.transaction()
        txn.put(SourceManifest(CARDS, OWNER, "Cards", {"scenario/storm": "r2", "character/mara": "r1"}),
                desktop.read_source(CARDS).etag)
        txn.commit(owner_handle())
        sync(desktop, phone, handles())
        for store, bio in ((desktop, "a pirate"), (phone, "a sailor")):  # r2 differs, heads untouched
            txn = store.transaction()
            txn.put(RecordEnvelope(SourceRef(CARDS, "character", "mara", "r2"), {"bio": bio}, name="Mara"), None)
            txn.commit(owner_handle())
        chat_library(desktop).start_chat(Launch("character", MARA)).commit()  # chat binds r1 (agreed)
        r2 = SourceRef(CARDS, "character", "mara", "r2")
        put(desktop, instance(InstanceRef("q-1", "character", "mara-1"), r2, base=r2))  # actor pins r2
        report = sync(desktop, phone, handles())
        actor = "campaigns/q-1/instances/character/mara-1.json"
        chat = "campaigns/q-1/chats/t-1.json"
        assert report.conflict(actor).code == "head_dependency"
        assert report.conflict(chat).code == "head_dependency", report.conflicts
        assert not (phone.root / chat).exists()
    finally:
        desktop.close()
        phone.close()


def _source(store, source, entity_kind="character"):
    txn = store.transaction()
    txn.put(SourceManifest(source, OWNER, None, {f"{entity_kind}/x": "r1"}), None)
    txn.put(RecordEnvelope(SourceRef(source, entity_kind, "x", "r1"), {"n": 1}), None)
    txn.commit(handle())


def test_d1_an_instance_pinning_a_refused_unit_stays_behind(tmp_path):
    """Kills D1 (no dependency re-check after _admit_units refuses a unit)."""
    desktop, phone = make_pair(tmp_path)
    try:
        _source(desktop, "catalog:shared", "character")
        _source(phone, "catalog:shared", "item")
        pinned = SourceRef("catalog:shared", "character", "x", "r1")
        zed = "campaigns/c1/instances/character/zed.json"
        put(desktop, instance(InstanceRef("c1", "character", "zed"), pinned, base=pinned), None)
        report = sync(desktop, phone, handles())
        assert report.conflict("sources/catalog/shared/_source/records/character/x/r1.json").code == "unapplicable"
        assert report.conflict(zed).code == "head_dependency", report.conflicts
        assert not (phone.root / zed).exists()
    finally:
        desktop.close()
        phone.close()


def test_k_a_denied_knowledge_record_holds_back_its_whole_append(tmp_path):
    """Kills K2+K3 together: the phone may write evidence/ but not events/; the batch must not land alone."""
    from grimoire.core import Event
    from grimoire.evidencestore import EvidenceLibrary
    from evidence_fixtures import C, m
    from test_evidencestore_scenarios import seed_actors
    from test_sync_scenarios import _grants
    desktop = LibraryStore.create(tmp_path / DESKTOP)
    seed_actors(desktop)
    phone = LibraryStore.create(tmp_path / PHONE)
    identify(desktop, DESKTOP)
    identify(phone, PHONE)
    sync(desktop, phone, handles())
    try:
        lib = lambda store: EvidenceLibrary(store, owner_handle("sources", "campaigns", "kept"), owner=OWNER)
        lib(desktop).append(C, [Event(C, "e-desk", "desk event", m(1), "director")]).commit()
        limited = _grants("sources", f"campaigns/{C}/evidence", "kept")
        report = sync(desktop, phone, {DESKTOP: handle(), PHONE: limited})
        landed = [path for path, device in report.copied if device == PHONE]
        assert not [p for p in landed if "/events/" in p or "/evidence/" in p], landed
        assert lib(phone).read(C, requested_by="director").problems == ()
    finally:
        desktop.close()
        phone.close()


def test_r1_resolve_refuses_a_chat_whose_actor_the_receiver_lacks(tmp_path):
    """A look-alike of the actor on the receiver: its canonical path is blocked (`shadowed`), so this pins
    the blocked branch of resolve's named-actor check (`test_sync_guards.py` pins the plain absent one)."""
    from chat_fixtures import ANYA, MARA, chat_library
    from grimoire.chats.model import Launch
    desktop, phone = _chat_pair(tmp_path)
    try:
        started = chat_library(desktop).start_chat(Launch("character", MARA)).commit()
        sync(desktop, phone, handles())
        chat = started.chat
        on_desktop, on_phone = chat_library(desktop), chat_library(phone)
        on_desktop.set_label(chat, "Harbor night", expected=on_desktop.read_chat(chat)[1]).commit()
        added = on_phone.attach_source(chat, ANYA, "character", expected=on_phone.read_chat(chat)[1]).commit()
        actor = added.actors[0]
        folder = desktop.root / "campaigns" / actor.campaign_id / "instances" / "character"
        (folder / (actor.instance_id.capitalize() + ".json")).write_bytes(b"{}")  # look-alike on the receiver
        report = sync(desktop, phone, handles())
        path = f"campaigns/{chat.campaign_id}/chats/{chat.chat_id}.json"
        assert report.conflict(path).code == "concurrent"
        mine, theirs = (desktop.root / path).read_bytes(), (phone.root / path).read_bytes()
        with pytest.raises(ResolveRefused, match="missing"):
            resolve(desktop, phone, path, etag(theirs), {DESKTOP: etag(mine), PHONE: etag(theirs)}, handles())
        assert (desktop.root / path).read_bytes() == mine
    finally:
        desktop.close()
        phone.close()


def test_h1_an_accepted_folder_spelled_differently_still_holds_relations(tmp_path):
    """Kills H1 (casefold dropped). docs/SYNC.md: 'whose accepted/ folder, in any spelling'."""
    from test_sync_families import _accept, _effects_pair
    from effects_fixtures import C
    desktop, phone, keys, relations = _effects_pair(tmp_path)
    try:
        _accept(desktop, keys)  # changes a relation in the same commit as the event
        folder = desktop.root / "campaigns" / C
        (folder / "accepted").rename(folder / "Accepted")  # a case-only rename by another program
        report = sync(desktop, phone, handles())
        assert not [path for path, _ in report.copied if "/relations/" in path], report.copied
    finally:
        desktop.close()
        phone.close()


# -- round 9: a path that is blocked is unknown, never "absent" ----------------------------------------

@pytest.mark.parametrize("where", ["campaign", "campaigns"])
def test_resolve_never_writes_a_held_relation_after_a_listing_error(tmp_path, monkeypatch, where):
    """resolve decides the hold from the same scans it writes from; an unlistable campaign folder holds."""
    from grimoire.store import engine
    from test_sync_families import EFFECTS_C, _accept, _effects_pair
    desktop, phone, keys, _ = _effects_pair(tmp_path)
    try:
        path = f"campaigns/{EFFECTS_C}/relations/r-seed-1.json"
        _accept(desktop, keys)
        assert path in sync(desktop, phone, handles()).skipped[DESKTOP]
        mine, theirs = (desktop.root / path).read_bytes(), (phone.root / path).read_bytes()
        folder = ("campaigns", EFFECTS_C) if where == "campaign" else ("campaigns",)
        real, fired = engine.Access.list_dir, []

        def busy_once(self, parts):
            if tuple(parts) == folder and not fired and self.root == desktop._access.root:
                fired.append(1)
                raise PermissionError(13, "held by another program")
            return real(self, parts)
        monkeypatch.setattr(engine.Access, "list_dir", busy_once)
        with pytest.raises(ResolveRefused, match="held back"):
            resolve(desktop, phone, path, etag(mine), {DESKTOP: etag(mine), PHONE: etag(theirs)}, handles())
        monkeypatch.undo()
        assert (phone.root / path).read_bytes() == theirs
    finally:
        desktop.close()
        phone.close()


def test_an_instance_pinning_a_revision_blocked_on_the_origin_waits(tmp_path, monkeypatch):
    from grimoire.store import engine
    from store_helpers import WREN
    from sync_helpers import PATHS, WREN_A, new_revision
    desktop, phone = make_pair(tmp_path)
    try:
        new_revision(desktop, "r2", "a captain")
        put(desktop, instance(WREN_A, WREN.at("r2"), base=WREN.at("r2")))
        real, folder = engine.Access.list_dir, tuple(PATHS["r2"].split("/")[:-1])

        def busy(self, parts):
            if tuple(parts) == folder and self.root == desktop._access.root:
                raise PermissionError(13, "held by another program")
            return real(self, parts)
        monkeypatch.setattr(engine.Access, "list_dir", busy)
        report = sync(desktop, phone, handles())
        monkeypatch.undo()
        assert report.conflict(PATHS["wren_a"]).code == "head_dependency"
        assert phone.read(WREN_A).envelope.base == str(WREN.at("r1"))
    finally:
        desktop.close()
        phone.close()


def test_an_unlistable_library_root_blocks_every_path_it_holds(tmp_path, monkeypatch):
    from grimoire.store import engine
    desktop, phone = make_pair(tmp_path)
    try:
        real = engine.Access.list_dir

        def busy(self, parts):
            if tuple(parts) == () and self.root == desktop._access.root:
                raise PermissionError(13, "held by another program")
            return real(self, parts)
        monkeypatch.setattr(engine.Access, "list_dir", busy)
        found = desktop.scan()
        monkeypatch.undo()
        assert found.blocked_reason("campaigns/c1/instances/character/wren-a.json") == "unreadable"
    finally:
        desktop.close()
        phone.close()



def test_a_knowledge_record_held_by_a_denied_pin_holds_back_its_whole_append(tmp_path):
    """A record held by the dependency rule in a partial commit (it pins a denied revision) keeps its batch back."""
    from grimoire.core import Event
    from grimoire.evidencestore import EvidenceLibrary
    from evidence_fixtures import C, m
    from store_helpers import seed
    from sync_helpers import new_revision
    from test_evidencestore_scenarios import seed_actors
    from test_sync_scenarios import _grants
    desktop, phone = LibraryStore.create(tmp_path / DESKTOP), LibraryStore.create(tmp_path / PHONE)
    try:
        seed(desktop, handle())
        seed_actors(desktop)
        identify(desktop, DESKTOP)
        identify(phone, PHONE)
        sync(desktop, phone, handles())
        new_revision(desktop, "r2", "a captain")

        def log(store):
            return EvidenceLibrary(store, owner_handle("sources", "campaigns", "kept"), owner=OWNER)
        log(desktop).append(C, [Event(C, "e-desk", "world:harbor/character/wren@r2", m(1), "director")]).commit()
        report = sync(desktop, phone, {DESKTOP: handle(), PHONE: _grants("campaigns", "kept")})
        assert not [path for path, device in report.copied if device == PHONE and "/evidence/" in path]
        log(phone).append(C, [Event(C, "e-phone", "phone event", m(2), "director")]).commit()  # still healthy
    finally:
        desktop.close()
        phone.close()


# -- round 10 -------------------------------------------------------------------------------------------

def test_a_manifest_head_naming_an_unstorable_revision_never_stops_the_sync(tmp_path):
    from grimoire.store.jsonfmt import parse_strict, render, thaw
    from sync_helpers import PATHS, WREN_B, edit_instance
    desktop, phone = make_pair(tmp_path)
    try:
        path = desktop.root / PATHS["manifest"]
        document = thaw(parse_strict(path.read_bytes()))
        document["heads"] = {"character/wren": "con"}  # a reserved device name: no library can store it
        path.write_bytes(render(document))
        edit_instance(phone, WREN_B, 9)
        report = sync(desktop, phone, handles())
        assert report.conflict(PATHS["manifest"]).code == "head_dependency"
        assert (PATHS["wren_b"], DESKTOP) in report.copied
    finally:
        desktop.close()
        phone.close()


def test_one_unreadable_kept_slot_never_stops_the_sync(tmp_path, monkeypatch):
    from grimoire.store import engine
    from sync_helpers import PATHS, WREN_A, WREN_B, edit_instance
    desktop, phone = make_pair(tmp_path)
    try:
        edit_instance(desktop, WREN_A, 7)
        edit_instance(phone, WREN_A, 8)
        sync(desktop, phone, handles())  # a conflict: each device keeps the other's version
        edit_instance(phone, WREN_A, 9)  # the desktop must keep a new version...
        edit_instance(phone, WREN_B, 5)  # ...and an unrelated edit must still land
        real = engine.Access.read

        def locked(self, parts):
            if ".conflicts" in parts and self.root == desktop._access.root:
                raise PermissionError(13, "held by another program")
            return real(self, parts)
        monkeypatch.setattr(engine.Access, "read", locked)
        report = sync(desktop, phone, handles())
        monkeypatch.undo()
        assert (PATHS["wren_b"], DESKTOP) in report.copied
        assert dict(report.conflict(PATHS["wren_a"]).kept)[DESKTOP] is False
    finally:
        desktop.close()
        phone.close()


@pytest.mark.parametrize("how", ["lookalike", "denied"])
def test_020_knowledge_naming_an_actor_the_destination_lacks_stays_behind(tmp_path, how):
    """020 never writes knowledge about an unknown actor; sync never creates that state either (and a
    later local actor with the same id never inherits another device's percepts)."""
    from grimoire.core import Event, Perception
    from grimoire.evidencestore import EvidenceLibrary
    from evidence_fixtures import C, m
    from test_evidencestore_scenarios import seed_actors
    from test_sync_scenarios import _grants
    vex = InstanceRef(C, "character", "vex")
    desktop = LibraryStore.create(tmp_path / DESKTOP)
    seed_actors(desktop)
    phone = LibraryStore.create(tmp_path / PHONE)
    try:
        identify(desktop, DESKTOP)
        identify(phone, PHONE)
        sync(desktop, phone, handles())
        put(desktop, RecordEnvelope(vex, {}), None)
        EvidenceLibrary(desktop, owner_handle("sources", "campaigns", "kept"), owner=OWNER).append(C, [
            Event(C, "e-x", "a secret meeting", m(1), "director"),
            Perception(C, "v-vex", vex, "e-x", "witnessed", "SECRET-PERCEPT", m(1), "director", confirmed=True),
        ]).commit()
        grants = handles()
        if how == "lookalike":
            (phone.root / "campaigns" / C / "instances" / "character").mkdir(parents=True, exist_ok=True)
            (phone.root / "campaigns" / C / "instances" / "character" / "Vex.json").write_bytes(b"{}")
        else:
            grants = {DESKTOP: handle(), PHONE: _grants("sources", f"campaigns/{C}/events",
                                                         f"campaigns/{C}/evidence", "kept")}
        report = sync(desktop, phone, grants)
        assert not [path for path, device in report.copied if device == PHONE and ("/events/" in path
                                                                               or "/evidence/" in path)]
    finally:
        desktop.close()
        phone.close()


def test_a_chat_waits_while_its_actor_is_blocked_on_the_origin(tmp_path):
    from chat_fixtures import MARA, chat_library, seed_sources
    from grimoire.chats.model import Launch
    desktop, phone = LibraryStore.create(tmp_path / DESKTOP), LibraryStore.create(tmp_path / PHONE)
    try:
        seed_sources(desktop)
        identify(desktop, DESKTOP)
        identify(phone, PHONE)
        sync(desktop, phone, handles())
        chat_library(desktop).start_chat(Launch("character", MARA)).commit()
        actor = desktop.root / "campaigns" / "q-1" / "instances" / "character" / "mara-1.json"
        actor.unlink()
        actor.mkdir()  # the origin's own actor file cannot be read: unknown, never "absent on both"
        report = sync(desktop, phone, handles())
        assert not [path for path, _ in report.copied if "/chats/" in path]
    finally:
        desktop.close()
        phone.close()


def test_an_ignored_marker_keeps_a_both_absent_path_uncertain(tmp_path):
    """Archives and markers damaged (ignored, still naming their paths), the record then deleted on both:
    a later restore is a conflict. (Bytes that name nothing cannot be remembered: a true first sync.)"""
    from sync_helpers import PATHS
    desktop, phone = make_pair(tmp_path)
    try:
        path = PATHS["wren_b"]
        old = (phone.root / path).read_bytes()
        sync(desktop, phone, handles())  # leaves a (now empty) pending marker on each device
        for store, peer in ((desktop, PHONE), (phone, DESKTOP)):
            damaged = (phone.root / ".sync" / "peers" / DESKTOP / "pending.json").read_bytes()
            for name in ("archive.json", "pending.json"):  # damaged: the check fails, the paths still read
                state = store.root / ".sync" / "peers" / peer / name
                data = state.read_bytes() if state.exists() else damaged  # (a marker on both devices)
                state.write_bytes(data.replace(b'"check": "sha256:', b'"check": "sha256:0'))
            (store.root / path).unlink()
        sync(desktop, phone, handles())
        (phone.root / path).write_bytes(old)  # restored from a backup on the phone
        report = sync(desktop, phone, handles())
        assert report.copied == () and report.conflict(path).code == "missing_ancestor"
    finally:
        desktop.close()
        phone.close()


def _chat_knowledge(tmp_path, chat):
    """A started chat on the desktop, then a 020 batch that came from it (or from no chat)."""
    from chat_fixtures import MARA, chat_library
    from grimoire.chats.model import Launch
    from grimoire.core import Event
    from grimoire.core.identity import ChatRef
    from grimoire.evidencestore import EvidenceLibrary
    from evidence_fixtures import m
    desktop, phone = _chat_pair(tmp_path)
    chat_library(desktop).start_chat(Launch("character", MARA)).commit()
    if chat == "deleted":
        sync(desktop, phone, handles())
        (phone.root / "campaigns" / "q-1" / "chats" / "t-1.json").unlink()  # the phone user deletes it
    EvidenceLibrary(desktop, owner_handle("sources", "campaigns", "kept"), owner=OWNER).append(
        "q-1", [Event("q-1", "e-1", "a meeting in the chat", m(1), "director")],
        chat=None if chat == "none" else ChatRef("q-1", "t-1")).commit()
    return desktop, phone


@pytest.mark.parametrize("case", ["chat_denied", "chat_deleted", "campaign_denied"])
def test_020_knowledge_never_lands_without_its_chat_or_campaign(tmp_path, case):
    """EVIDENCE.md step 3: the campaign and the chat a batch came from exist before its knowledge does;
    sync never creates the state 020 itself refuses (a chat, deleted on the phone, stays a conflict)."""
    from test_sync_scenarios import _grants
    desktop, phone = _chat_knowledge(tmp_path, {"chat_deleted": "deleted", "campaign_denied": "none"}.get(case, "t-1"))
    try:
        grants = handles()
        if case == "chat_denied":  # the campaign's scope, actors and knowledge, but not its chats
            grants = {DESKTOP: handle(), PHONE: _grants("sources", "campaigns/q-1/scope.json", "campaigns/q-1/instances",
                                                         "campaigns/q-1/events", "campaigns/q-1/evidence", "kept")}
        elif case == "campaign_denied":  # only the knowledge folders
            grants = {DESKTOP: handle(), PHONE: _grants("sources", "campaigns/q-1/events",
                                                         "campaigns/q-1/evidence", "kept")}
        report = sync(desktop, phone, grants)
        assert not (phone.root / "campaigns" / "q-1" / "chats" / "t-1.json").exists()
        landed = [path for path, device in report.copied if device == PHONE]
        assert not [path for path in landed if "/events/" in path or "/evidence/" in path], landed
        knowledge = sorted(path for path in desktop.scan().files if "/events/" in path or "/evidence/" in path)
        assert knowledge and all(report.conflict(path) is not None or path in report.skipped[DESKTOP]
                                 for path in knowledge)  # reported: the dependency, or held with its unit
        assert any(report.conflict(path) is not None and report.conflict(path).code == "head_dependency"
                   for path in knowledge) or case != "chat_deleted"
    finally:
        desktop.close()
        phone.close()


def test_resolve_refuses_a_record_pinning_a_revision_blocked_on_the_receiver(tmp_path):
    """The winner pins a revision it does not hold; the receiver's copy of it is a look-alike spelling
    (blocked): "absent on both" is never read from a blocked path."""
    from store_helpers import WREN
    from sync_helpers import PATHS, WREN_A, edit_instance
    desktop, phone = make_pair(tmp_path)
    try:
        edit_instance(desktop, WREN_A, 9)
        put(phone, instance(WREN_A, WREN.at("r9"), base=WREN.at("r9")))  # pins a revision the phone lacks
        r9 = desktop.root / PATHS["r1"].replace("r1.json", "R9.json")
        r9.write_bytes((desktop.root / PATHS["r1"]).read_bytes())
        report = sync(desktop, phone, handles())
        path = PATHS["wren_a"]
        assert report.conflict(path).code == "concurrent"
        mine, theirs = (desktop.root / path).read_bytes(), (phone.root / path).read_bytes()
        with pytest.raises(ResolveRefused, match="pins"):
            resolve(desktop, phone, path, etag(theirs), {DESKTOP: etag(mine), PHONE: etag(theirs)}, handles())
        assert (desktop.root / path).read_bytes() == mine
    finally:
        desktop.close()
        phone.close()


def _linked_harbor(desktop, catalog_kind="character"):
    """The desktop moves wren to r2 and links r2 to catalog:shared's x (one commit), then edits an
    unrelated instance that must still sync whatever happens to the sources."""
    from grimoire.store import LinkSpec
    from store_helpers import wren
    from sync_helpers import WREN_A, edit_instance
    target = str(SourceRef("catalog:shared", catalog_kind, "x", "r1"))
    txn = desktop.transaction()
    txn.put(wren("r2", "a captain"), None)
    txn.put(SourceManifest("world:harbor", OWNER, "Harbor", {"character/wren": "r2"},
                           (LinkSpec(str(SourceRef("world:harbor", "character", "wren", "r2")), "same_individual",
                                     target),)), desktop.read_source("world:harbor").etag)
    txn.commit(handle())
    edit_instance(desktop, WREN_A, 8)


@pytest.mark.parametrize("how", ["refused", "denied"])
def test_a_manifest_dropped_for_its_link_takes_its_head_revision_and_the_rest_still_syncs(tmp_path, how):
    """Holding a manifest back (its link target was refused, or denied) leaves its new head revision
    alone in the source: that is refused too, and the unrelated edit lands. One bad record never
    stops the pair."""
    from sync_helpers import PATHS
    from store_helpers import owner_handle as grants
    desktop, phone = make_pair(tmp_path)
    try:
        _source(desktop, "catalog:shared", "character")
        if how == "refused":
            _source(phone, "catalog:shared", "item")  # S7: the same source made offline on both
            keys = handles()
        else:
            keys = {DESKTOP: handle(), PHONE: grants("sources/world", "campaigns", "kept")}  # not the catalog
        _linked_harbor(desktop)
        for _ in range(2):  # and again: nothing is stuck
            report = sync(desktop, phone, keys)
            assert phone.read_exact(PATHS["wren_a"]) == desktop.read_exact(PATHS["wren_a"])
            assert report.conflict(PATHS["manifest"]) is not None and report.conflict(PATHS["r2"]) is not None
            assert not (phone.root / PATHS["r2"]).exists()
            assert b'"r2"' not in (phone.root / PATHS["manifest"]).read_bytes()  # still headed at r1
    finally:
        desktop.close()
        phone.close()


def test_two_units_of_one_source_that_refuse_each_other_never_stop_the_sync(tmp_path):
    """One entity id under two kinds (a hand-copied file): each revision is acceptable alone, not both.
    Units of a failing source are admitted cumulatively, so one lands, the other is `unapplicable`."""
    from sync_helpers import PATHS, WREN_A, edit_instance
    (tmp_path / "pair").mkdir()
    (tmp_path / "scratch").mkdir()
    desktop, phone = make_pair(tmp_path / "pair")
    scratch, spare = make_pair(tmp_path / "scratch")
    try:
        txn = desktop.transaction()
        txn.put(RecordEnvelope(SourceRef("world:harbor", "item", "zed", "r1"), {"n": 1}), None)
        txn.commit(handle())
        txn = scratch.transaction()
        txn.put(RecordEnvelope(SourceRef("world:harbor", "location", "zed", "r1"), {"n": 2}), None)
        txn.commit(handle())
        location = "sources/world/harbor/_source/records/location/zed/r1.json"
        (desktop.root / location).parent.mkdir(parents=True)
        (desktop.root / location).write_bytes((scratch.root / location).read_bytes())  # by another tool
        edit_instance(desktop, WREN_A, 8)
        report = sync(desktop, phone, handles())
        assert phone.read_exact(PATHS["wren_a"]) == desktop.read_exact(PATHS["wren_a"])
        item = "sources/world/harbor/_source/records/item/zed/r1.json"
        landed = [path for path in (item, location) if (phone.root / path).exists()]
        assert len(landed) == 1
        assert {report.conflict(path).code for path in (item, location) if path not in landed} == {"unapplicable"}
    finally:
        for store in (desktop, phone, scratch, spare):
            store.close()


def test_resolve_that_the_receivers_grant_refuses_is_a_resolve_refusal(tmp_path):
    from sync_helpers import PATHS, WREN_A, edit_instance
    from store_helpers import owner_handle as grants
    desktop, phone = make_pair(tmp_path)
    try:
        edit_instance(desktop, WREN_A, 8)
        edit_instance(phone, WREN_A, 9)
        assert sync(desktop, phone, handles()).conflict(PATHS["wren_a"]).code == "concurrent"
        mine = etag((desktop.root / PATHS["wren_a"]).read_bytes())
        theirs = etag((phone.root / PATHS["wren_a"]).read_bytes())
        with pytest.raises(ResolveRefused, match="cannot be resolved"):
            resolve(desktop, phone, PATHS["wren_a"], mine, {DESKTOP: mine, PHONE: theirs},
                    {DESKTOP: handle(), PHONE: grants("sources")})
        assert etag((phone.root / PATHS["wren_a"]).read_bytes()) == theirs
    finally:
        desktop.close()
        phone.close()
