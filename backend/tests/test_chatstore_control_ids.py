"""Actor ids named by the 010b sovereignty ledger stay taken (``campaign_taken_ids``).

The ledger (``authority/campaigns/<campaign>/``) stores control changes and delegations by bare
``actor_id``. If a PC's instance file and chat are deleted by hand, a new actor given the same id
would silently become that player's PC. The ledger is scanned raw (no replay, no chain check), and a
ledger folder or batch that cannot be listed or read fails allocation closed.
"""

from __future__ import annotations

import json

import pytest

from grimoire.chats import Launch
from grimoire.chatstore import campaign_taken_ids
from grimoire.core.identity import InstanceRef, ScopeRef
from grimoire.core.model import ControlAssignment
from grimoire.ledgers import ControlChange, SovereigntyLedger, sovereignty_actor_ids
from grimoire.security import Grant, Permission, ResourcePath
from grimoire.store import LibraryStore, TakenIdsUnlisted

from chat_fixtures import MARA, chat_library, seed_sources
from ledger_helpers import OWNER, open_authority
from store_helpers import make_dir_link

Q = "q-1"
P1 = "human:p1"
LEDGER = ("authority", "campaigns", Q)


def _start(store: LibraryStore):
    seed_sources(store)
    started = chat_library(store).start_chat(Launch("character", MARA)).commit()
    assert started.actors == (InstanceRef(Q, "character", "mara-1"),)
    return started


def _make_pc(lib, actor_id: str = "mara-1") -> None:
    with LibraryStore.open(lib) as store:
        authority = open_authority(store)
        SovereigntyLedger(authority).record(Q, ControlChange(Q, actor_id, P1), authority.handle_for(OWNER, trusted=True))
        authority.close()


def _hand_delete_chat_and_actor(store: LibraryStore, started) -> None:
    root = store.root / "campaigns" / Q
    (root / "chats" / f"{started.chat.chat_id}.json").unlink()
    (root / "instances" / "character" / "mara-1.json").unlink()


def _batch(entries: list[dict], seq: int = 1) -> bytes:
    """A hand-written batch: the scan reads it raw, so no chain or schema is needed."""
    return json.dumps({"format": "grimoire.ledger/1", "ledger": f"campaigns/{Q}", "seq": seq,
                       "previous": "sha256:not-the-real-previous", "entries": entries}).encode()


def _entry(kind: str, **value: object) -> dict:
    return {"schema": 1, "kind": kind, "recorded_at": "2026-01-01T00:00:00+00:00", "recorded_by": P1,
            "value": value}


def _write(store: LibraryStore, parts: tuple[str, ...], data: bytes) -> None:
    path = store.root.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_a_hand_deleted_pc_id_is_never_given_to_a_new_actor(tmp_path):
    lib = tmp_path / "lib"
    with LibraryStore.create(lib) as store:
        started = _start(store)
    _make_pc(lib)
    with LibraryStore.open(lib) as store:
        authority = open_authority(store)
        authority.add_grant(Grant("g1", OWNER, frozenset({Permission.APPLY}), ResourcePath.parse("campaigns"), OWNER))
        _hand_delete_chat_and_actor(store, started)
        assert "mara-1" in campaign_taken_ids(store, Q)  # the ledger still names it
        chats = chat_library(store, authority.handle_for(OWNER, trusted=True))
        chats._new_id.counts.update({"q": 1, "t": 1})
        (new,) = chats.start_chat(Launch("character", MARA), scope=ScopeRef(Q)).commit().actors
        assert new == InstanceRef(Q, "character", "mara-2")
        controls = SovereigntyLedger(authority).load(Q).controls
        assert controls == (ControlAssignment(Q, "mara-1", P1),)  # the new actor is no one's PC
        authority.close()


def test_every_actor_id_field_of_this_campaign_is_read_raw(tmp_path):
    """Delegations name actors too; a broken chain or an undecodable value still protects its ids."""
    with LibraryStore.create(tmp_path / "lib") as store:
        _start(store)
        _write(store, LEDGER + ("0000000001.json",), _batch([
            _entry("control", campaign_id=Q, actor_id="zed-1", controller_id=P1),
            _entry("delegation", campaign_id=Q, actor_id="anya-4", delegation_id="d1", granted_by=P1),
            _entry("control", campaign_id="q-9", actor_id="other-1", controller_id=P1),  # another campaign
            _entry("revocation", campaign_id=Q, delegation_id="d1", revoked_by=P1),
        ]))
        assert sovereignty_actor_ids(store, Q) == {"zed-1", "anya-4"}
        assert {"zed-1", "anya-4"} <= campaign_taken_ids(store, Q)
        assert "other-1" not in campaign_taken_ids(store, Q)


def test_a_stray_file_beside_the_batches_never_blocks_allocation(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as store:
        _start(store)
        _write(store, LEDGER + ("notes.txt",), b"not a batch")
        assert sovereignty_actor_ids(store, Q) == frozenset()


def test_no_ledger_reserves_nothing(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as store:
        _start(store)
        assert sovereignty_actor_ids(store, Q) == frozenset()


DAMAGE = ["malformed batch", "linked batch", "linked folder", "unlistable folder", "look-alike folder",
          "look-alike batch"]


@pytest.mark.parametrize("damage", DAMAGE)
def test_an_unreadable_ledger_fails_allocation_closed(tmp_path, monkeypatch, damage):
    with LibraryStore.create(tmp_path / "lib") as store:
        _start(store)
        good = _batch([_entry("control", campaign_id=Q, actor_id="zed-1", controller_id=P1)])
        outside = tmp_path / "outside"
        outside.mkdir()
        if damage == "malformed batch":
            _write(store, LEDGER + ("0000000001.json",), b"{not json")
        elif damage == "linked batch":
            (outside / "batch.json").write_bytes(good)
            store.root.joinpath(*LEDGER).mkdir(parents=True)
            try:
                store.root.joinpath(*LEDGER, "0000000001.json").symlink_to(outside / "batch.json")
            except OSError:
                pytest.skip("file symlinks are not available")
        elif damage == "linked folder":
            (outside / "0000000001.json").write_bytes(good)
            store.root.joinpath(*LEDGER[:-1]).mkdir(parents=True)
            make_dir_link(store.root.joinpath(*LEDGER), outside)
        elif damage == "unlistable folder":
            _write(store, LEDGER + ("0000000001.json",), good)
            real = store._access.list_dir

            def failing(parts):
                if tuple(parts) == LEDGER:
                    raise PermissionError("denied")
                return real(parts)

            monkeypatch.setattr(store._access, "list_dir", failing)
        elif damage == "look-alike folder":
            if (store.root / "authority").exists():
                pytest.skip("the library already has an authority folder")
            _write(store, ("authority", "campaigns", "Q-1", "0000000001.json"), good)
            if store.root.joinpath(*LEDGER).exists():
                pytest.skip("a case-insensitive filesystem: Q-1 is the real folder")
        else:
            _write(store, LEDGER + ("0000000001.JSON",), good)
        with pytest.raises(TakenIdsUnlisted) as caught:
            sovereignty_actor_ids(store, Q)
        assert caught.value.paths
        chats = chat_library(store)
        chats._new_id.counts.update({"q": 1, "t": 1})
        before = sorted(path.name for path in (store.root / "campaigns" / Q / "chats").iterdir())
        with pytest.raises(TakenIdsUnlisted):
            chats.start_chat(Launch("character", MARA), scope=ScopeRef(Q))
        assert sorted(path.name for path in (store.root / "campaigns" / Q / "chats").iterdir()) == before
