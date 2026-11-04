"""Sync's dependency rule, end to end (brutal-review round 8): each test fails if one rule is removed.

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
    """Kills R1 (resolve's named-actor check removed)."""
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
        with pytest.raises(ResolveRefused):
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
