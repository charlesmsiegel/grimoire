"""107's actor allocator never reuses an instance id that has a holdings file (Sprint 023).

A hand-deleted holder's id must not go to a different individual, who would silently inherit its
holdings (as 017 established for relation endpoints). Both allocation paths are covered, and the
commit-time recheck too. The id ``mara-2`` belongs to no roster of the chat being extended.
"""

from __future__ import annotations

import pytest

from grimoire.chats import Launch
from grimoire.core import InstanceRef
from grimoire.core.identity import ChatRef, ScopeRef
from grimoire.holdings import Line, Operation
from grimoire.holdingstore import holdings_holder_ids
from grimoire.store import LibraryStore, RecordEnvelope, StaleWrite

from chat_fixtures import ANYA, MARA, chat_library, seed_sources
from holdings_fixtures import holdings_library
from store_helpers import OWNER, owner_handle

Q = "q-1"
MARA_2 = InstanceRef(Q, "character", "mara-2")
HOLDINGS = ("campaigns", Q, "holdings", "character", "mara-2.json")


def _setup(store: LibraryStore, holdings: str | None) -> ChatRef:
    """Scope q-1 with chat t-1 (Mara, actor mara-1), a hand-made instance mara-2 (maybe holding gold), chat t-2."""
    seed_sources(store)
    chats = chat_library(store)
    chats.start_chat(Launch("character", MARA)).commit()
    txn = store.transaction()
    txn.put(RecordEnvelope(MARA_2, {"note": "synthetic"}), None)
    txn.commit(owner_handle())
    if holdings is not None:
        holdings_library(store).apply(Operation("g1", (Line.add(MARA_2, "gold-coin", 500),))).commit()
        if holdings == "undecodable":
            store.root.joinpath(*HOLDINGS).write_bytes(b"{not json")
    store.root.joinpath("campaigns", Q, "instances", "character", "mara-2.json").unlink()  # a hand deletion
    return chats.start_chat(Launch("character", ANYA), scope=ScopeRef(Q)).commit().chat


@pytest.mark.parametrize("holdings, expected", [
    (None, "mara-2"),  # control: with no holdings file, the freed id is reused (the test can fail)
    ("decodable", "mara-3"),
    ("undecodable", "mara-3"),  # a holder file the codec or the store cannot read still claims its id
])
def test_attach_source_never_reuses_a_holder_id(tmp_path, holdings, expected):
    with LibraryStore.create(tmp_path / "lib") as store:
        second = _setup(store, holdings)
        chats = chat_library(store)
        chats._new_id.counts.update({"q": 1, "t": 2})
        _, etag = chats.read_chat(second)
        change = chats.attach_source(second, MARA, "character", expected=etag)
        assert [actor.instance_id for actor in change.new_actors] == [expected]


@pytest.mark.parametrize("holdings, expected", [(None, "mara-2"), ("decodable", "mara-3")])
def test_start_chat_in_an_existing_scope_never_reuses_a_holder_id(tmp_path, holdings, expected):
    with LibraryStore.create(tmp_path / "lib") as store:
        _setup(store, holdings)
        chats = chat_library(store)
        chats._new_id.counts.update({"q": 1, "t": 2})
        change = chats.start_chat(Launch("character", MARA), scope=ScopeRef(Q))
        assert [actor.instance_id for actor in change.new_actors] == [expected]


def test_a_chat_commit_rechecks_holder_ids(tmp_path):
    """An id that gained a holdings file between a chat's preview and its commit is not taken by the chat."""
    with LibraryStore.create(tmp_path / "lib") as store:
        second = _setup(store, None)
        chats = chat_library(store)
        chats._new_id.counts.update({"q": 1, "t": 2})
        _, etag = chats.read_chat(second)
        change = chats.attach_source(second, MARA, "character", expected=etag)
        assert [actor.instance_id for actor in change.new_actors] == ["mara-2"]
        store.root.joinpath(*HOLDINGS).parent.mkdir(parents=True, exist_ok=True)
        store.root.joinpath(*HOLDINGS).write_bytes(b"{}")  # any holder file claims the id
        assert "mara-2" in holdings_holder_ids(store, Q)
        with pytest.raises(StaleWrite):
            change.commit()


def test_holder_ids_come_from_file_names_alone(tmp_path, monkeypatch):
    """No holder file is opened: the cost of reserving ids never grows with holdings history."""
    import grimoire.store.loader as loader
    with LibraryStore.create(tmp_path / "lib") as store:
        _setup(store, "decodable")

        real_read = loader.read_scope_record

        def no_holder_reads(access, parts, *args, **kwargs):
            if len(parts) > 2 and parts[2] == "holdings":
                raise AssertionError("a holder file was read")
            return real_read(access, parts, *args, **kwargs)

        monkeypatch.setattr(loader, "read_scope_record", no_holder_reads)
        assert holdings_holder_ids(store, Q) == frozenset({"mara-2"})
        chats = chat_library(store)
        chats._new_id.counts.update({"q": 1, "t": 2})
        change = chats.start_chat(Launch("character", MARA), scope=ScopeRef(Q))
        assert [actor.instance_id for actor in change.new_actors] == ["mara-3"]


def _damage(store, tmp_path, monkeypatch, damage):
    from store_helpers import make_dir_link
    folder = ("campaigns", Q, "holdings")
    if damage in ("linked folder", "linked kind folder"):
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        store.root.joinpath(*HOLDINGS).unlink()
        store.root.joinpath(*folder, "character").rmdir()
        if damage == "linked folder":
            store.root.joinpath(*folder).rmdir()
            make_dir_link(store.root.joinpath(*folder), elsewhere)
        else:
            make_dir_link(store.root.joinpath(*folder, "character"), elsewhere)
    elif damage in ("look-alike folder", "look-alike kind folder"):  # a case-only rename, as a sync tool might
        if damage == "look-alike folder":
            store.root.joinpath(*folder).rename(store.root.joinpath("campaigns", Q, "Holdings"))
        else:
            store.root.joinpath(*folder, "character").rename(store.root.joinpath(*folder, "Character"))
    elif damage == "uninspectable kind folder":  # listed, but lstat fails (permissions on the parent)
        import grimoire.store.loader as loader
        real_lstat = loader.lstat_or_none

        def failing_lstat(path):
            if str(path).replace("\\", "/").endswith("holdings/character"):
                raise PermissionError("denied")
            return real_lstat(path)

        monkeypatch.setattr(loader, "lstat_or_none", failing_lstat)
    else:
        blocked = folder if damage == "unreadable folder" else folder + ("character",)
        real = store._access.list_dir

        def failing(parts):
            if tuple(parts) == blocked:
                raise PermissionError("denied")
            return real(parts)

        monkeypatch.setattr(store._access, "list_dir", failing)


DAMAGE = ["unreadable folder", "unreadable kind folder", "uninspectable kind folder", "linked folder",
          "linked kind folder", "look-alike folder", "look-alike kind folder"]


@pytest.mark.parametrize("damage", DAMAGE)
def test_an_unlistable_holdings_folder_fails_allocation_closed(tmp_path, monkeypatch, damage):
    from grimoire.holdingstore import HoldingsUnlisted
    with LibraryStore.create(tmp_path / "lib") as store:
        _setup(store, "decodable")
        _damage(store, tmp_path, monkeypatch, damage)
        chats_folder = store.root / "campaigns" / Q / "chats"
        before = sorted(path.name for path in chats_folder.iterdir())
        chats = chat_library(store)
        chats._new_id.counts.update({"q": 1, "t": 2})
        with pytest.raises(HoldingsUnlisted):
            chats.start_chat(Launch("character", MARA), scope=ScopeRef(Q))
        assert sorted(path.name for path in chats_folder.iterdir()) == before


@pytest.mark.parametrize("damage", ["linked folder", "linked kind folder", "unreadable folder"])
def test_a_damaged_holdings_folder_blocks_only_allocation(tmp_path, monkeypatch, damage):
    """Binding a lorebook or scenario, or reusing an actor, allocates no id, so it never lists holdings."""
    from chat_fixtures import FOG, LORE
    from grimoire.holdingstore import HoldingsUnlisted
    with LibraryStore.create(tmp_path / "lib") as store:
        second = _setup(store, "decodable")
        _damage(store, tmp_path, monkeypatch, damage)
        chats = chat_library(store)
        chats._new_id.counts.update({"q": 1, "t": 2})
        _, etag = chats.read_chat(second)
        chats.attach_source(second, LORE, "lorebook", expected=etag).commit()
        chats.start_chat(Launch("scenario", FOG), scope=ScopeRef(Q)).commit()
        mara_1 = InstanceRef(Q, "character", "mara-1")
        _, etag = chats.read_chat(second)
        chats.attach_source(second, MARA, "character", expected=etag, actor=mara_1).commit()
        _, etag = chats.read_chat(second)
        with pytest.raises(HoldingsUnlisted):  # a new actor id is the one thing that needs the listing
            chats.attach_source(second, ANYA, "character", expected=etag)


def test_one_start_chat_allocates_distinct_ids_around_a_holder(tmp_path):
    """Two same-base actors in one call: the lazily computed taken set keeps what it adds."""
    from chat_fixtures import NAMESAKE
    with LibraryStore.create(tmp_path / "lib") as store:
        _setup(store, "decodable")
        chats = chat_library(store)
        chats._new_id.counts.update({"q": 1, "t": 2})
        change = chats.start_chat(Launch("character", MARA), attach=[(NAMESAKE, "character")], scope=ScopeRef(Q))
        assert [actor.instance_id for actor in change.new_actors] == ["mara-3", "mara-4"]
        change.commit()


def test_set_persona_reuse_never_lists_holdings(tmp_path, monkeypatch):
    from chat_fixtures import SAM
    from grimoire.holdingstore import HoldingsUnlisted
    with LibraryStore.create(tmp_path / "lib") as store:
        second = _setup(store, "decodable")
        chats = chat_library(store)
        chats._new_id.counts.update({"q": 1, "t": 2})
        _, etag = chats.read_chat(second)
        chats.set_persona(second, SAM, expected=etag).commit()  # sam-1, while holdings are still listable
        _damage(store, tmp_path, monkeypatch, "linked folder")
        _, etag = chats.read_chat(second)
        sam_1 = InstanceRef(Q, "character", "sam-1")
        chats.set_persona(second, SAM, expected=etag).commit()  # the same persona keeps its body
        _, etag = chats.read_chat(second)
        chats.set_persona(second, MARA, expected=etag, actor=InstanceRef(Q, "character", "mara-1")).commit()
        _, etag = chats.read_chat(second)
        with pytest.raises(HoldingsUnlisted):  # a fresh persona body needs a new id
            chats.set_persona(second, SAM, expected=etag)
        assert sam_1 in store.campaign_instances(Q)


def test_holder_and_accepted_event_ids_are_both_taken(tmp_path):
    """021's accepted-event ids and 023's holder ids are one union: neither family hides the other."""
    from grimoire.core.identity import AcceptedRef
    from grimoire.store import ScopeRecord
    with LibraryStore.create(tmp_path / "lib") as store:
        _setup(store, "decodable")  # mara-2 holds gold, and its instance file is gone
        txn = store.transaction()  # a raw accepted event naming mara-3, which never had an instance file
        txn.put(ScopeRecord(AcceptedRef(Q, "e-1"), "test.effect/1", {"subject": f"campaign:{Q}/character/mara-3"},
                            owner=OWNER), None)
        txn.commit(owner_handle())
        chats = chat_library(store)
        chats._new_id.counts.update({"q": 1, "t": 2})
        change = chats.start_chat(Launch("character", MARA), scope=ScopeRef(Q))
        assert [actor.instance_id for actor in change.new_actors] == ["mara-4"]
