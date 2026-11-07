"""035's turn records over a real store: the scenestore reader and writer, taken ids, and what sync needs.

The store-level guarantees every scope-record family shares (paths, CAS, authorization, collisions, links,
containment, recovery) are covered with 021's families in ``test_store_effect_records.py``. Synthetic only.
"""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

import pytest

from grimoire.chatstore import campaign_taken_ids
from grimoire.core.identity import ChatRef, InstanceRef, TurnRef
from grimoire.scene import TURN_SCHEMA, Post, SceneRefused, TurnRecord, turn_id, turn_to_payload
from grimoire.scenestore import SceneLibrary, turn_instance_ids
from grimoire.store import LibraryStore, ScopeRecord, TakenIdsUnlisted
from grimoire.sync import identify, sync

from store_helpers import OWNER, owner_handle, seed
from sync_helpers import DESKTOP, PHONE, handles

C = "c1"
CHAT, OTHER = ChatRef(C, "chat-a"), ChatRef(C, "chat-b")
PC, GHOST = InstanceRef(C, "character", "pc"), InstanceRef(C, "character", "ghost")


def post_turn(chat: ChatRef, number: int, text: str = "Hello.", audience=(PC,), device=None) -> TurnRecord:
    return TurnRecord(chat, number, "post", Post("human:ana", PC, "speech", text, audience, "speech"), device=device)


def tag(device: str) -> str:
    """028's documented tag: the first eight hex digits of the SHA-256 of the device ID."""
    return sha256(device.encode()).hexdigest()[:8]


def scenes(store) -> SceneLibrary:
    return SceneLibrary(store, owner_handle("campaigns"), owner=OWNER)


@pytest.fixture
def library(tmp_path):
    store = LibraryStore.create(tmp_path / "lib")
    seed(store)
    yield store
    store.close()


def test_turns_read_in_order_and_writes_are_create_only_then_cas(library):
    lib = scenes(library)
    first = lib.write(post_turn(CHAT, 1), None)
    lib.write(post_turn(CHAT, 2, "Again."), None)
    lib.write(post_turn(OTHER, 1, "Elsewhere."), None)
    found = lib.turns(CHAT)
    assert found.complete and [t.record.number for t in found.turns] == [1, 2]
    assert found.last.record.post.text == "Again."
    with pytest.raises(SceneRefused) as error:  # two posts can never both become turn 1
        lib.write(post_turn(CHAT, 1, "Racing."), None)
    assert error.value.reason == "turn_changed"
    changed = lib.write(post_turn(CHAT, 1, "Edited."), first)
    with pytest.raises(SceneRefused):
        lib.write(post_turn(CHAT, 1, "Stale."), first)
    assert lib.turns(CHAT).turns[0].etag == changed.etag


def test_a_rewrite_keeps_the_envelope_owner_and_unknown_fields(library):
    lib = scenes(library)
    first = lib.write(post_turn(CHAT, 1), None)
    txn = library.transaction()  # a later version wrote an envelope field this one does not know
    txn.put(ScopeRecord(first.record.ref, "grimoire.scene-turn/1", library.campaign_turns(C).records[
        first.record.ref].record.payload, owner="human:someone-else", extra={"x-later": {"kept": True}}), first.etag)
    txn.commit(owner_handle("campaigns"))
    (stored,) = lib.turns(CHAT).turns
    lib.write(post_turn(CHAT, 1, "Edited."), stored)
    envelope = library.campaign_turns(C).records[first.record.ref].record
    assert envelope.owner == "human:someone-else" and dict(envelope.extra) == {"x-later": {"kept": True}}


def test_a_new_turn_waits_for_the_previous_one_as_read(library):
    lib = scenes(library)
    first = lib.write(post_turn(CHAT, 1), None)
    lib.write(post_turn(CHAT, 1, "Paused meanwhile."), first)
    with pytest.raises(SceneRefused) as error:
        lib.write(post_turn(CHAT, 2), None, after=first)
    assert error.value.reason == "turn_changed" and [t.record.number for t in lib.turns(CHAT).turns] == [1]
    lib.write(post_turn(CHAT, 2), None, after=lib.turns(CHAT).last)


def test_before_commit_raising_writes_nothing(library):
    lib = scenes(library)

    def refuse():
        raise RuntimeError("fence lost")

    with pytest.raises(RuntimeError):
        lib.write(post_turn(CHAT, 1), None, before_commit=refuse)
    assert lib.turns(CHAT).turns == ()


def test_a_gap_or_a_damaged_turn_makes_the_chat_incomplete(library):
    lib = scenes(library)
    lib.write(post_turn(CHAT, 1), None)
    lib.write(post_turn(CHAT, 3), None)
    assert ("campaign:c1#chat/chat-a", "turn_gap") in lib.turns(CHAT).problems
    folder = library.root / "campaigns" / C / "turns"
    (folder / f"{turn_id(CHAT, 2)}.json").write_bytes(b"{not json")
    assert not lib.turns(CHAT).complete


def test_a_damaged_turn_of_another_chat_never_blocks_this_one(library):
    lib = scenes(library)
    lib.write(post_turn(CHAT, 1), None)
    folder = library.root / "campaigns" / C / "turns"
    (folder / f"{turn_id(OTHER, 1)}.json").write_bytes(b"{not json")
    assert lib.turns(CHAT).complete
    assert not lib.turns(OTHER).complete
    (folder / "stray.json").write_bytes(b"{}")  # a name that is no chat's turn counts against every chat
    assert not lib.turns(CHAT).complete


def test_a_payload_this_version_cannot_read_is_a_problem_not_a_turn(library):
    lib = scenes(library)
    ref = TurnRef(C, turn_id(CHAT, 1))
    txn = library.transaction()
    txn.put(ScopeRecord(ref, "grimoire.scene-turn/9", {"future": True}, owner=OWNER), None)
    txn.commit(owner_handle("campaigns"))
    found = lib.turns(CHAT)
    assert found.turns == () and (str(ref), "unsupported_schema") in found.problems


def test_actors_named_only_by_turns_stay_taken(library):
    scenes(library).write(post_turn(CHAT, 1, audience=(PC, GHOST)), None)
    assert {"pc", "ghost"} <= turn_instance_ids(library, C)
    assert "ghost" in campaign_taken_ids(library, C)  # a deleted actor's id never goes to a newcomer


def test_taken_ids_fail_closed_on_an_unreadable_turn(library):
    scenes(library).write(post_turn(CHAT, 1), None)
    (library.root / "campaigns" / C / "turns" / "broken.json").write_bytes(b"{not json")
    with pytest.raises(TakenIdsUnlisted):
        turn_instance_ids(library, C)


def test_sync_never_lands_a_turn_without_its_chat_and_scope_and_never_hides_it(tmp_path):
    """028: turns sync, but a turn is history of one chat. Here no device holds the chat record or the play scope
    (a bare store), so neither device's turn 1 lands on the other: each is a named ``head_dependency``
    conflict, nothing is copied, and each device still reads its own turn."""
    desktop, phone = LibraryStore.create(tmp_path / DESKTOP), LibraryStore.create(tmp_path / PHONE)
    try:
        seed(desktop)
        identify(desktop, DESKTOP)
        identify(phone, PHONE)
        sync(desktop, phone, handles())
        scenes(desktop).write(post_turn(CHAT, 1, "From the desk.", device=tag(DESKTOP)), None)
        scenes(phone).write(post_turn(CHAT, 1, "From the phone.", device=tag(PHONE)), None)
        report = sync(desktop, phone, handles())
        paths = [f"campaigns/{C}/turns/{turn_id(CHAT, 1, tag(device))}.json" for device in (DESKTOP, PHONE)]
        assert not [copied for copied, _ in report.copied if "/turns/" in copied]
        for path in paths:
            conflict = report.conflict(path)
            assert conflict.code == "head_dependency" and f"campaigns/{C}/chats/{CHAT.chat_id}.json" in conflict.detail
            assert f"campaigns/{C}/scope.json" in conflict.detail
        assert scenes(desktop).turns(CHAT).last.record.post.text == "From the desk."
        assert scenes(phone).turns(CHAT).last.record.post.text == "From the phone."
    finally:
        desktop.close()
        phone.close()


def test_a_turn_this_version_cannot_read_arriving_before_a_create_refuses_it(library):
    """028: sync can bring a peer's turn this version cannot decode at any time; a create (or a pause) checked
    against a chat read before it arrived is refused, never written beside a turn n it could not see."""
    lib = scenes(library)
    first = lib.write(post_turn(CHAT, 1), None)
    read = lib.turns(CHAT)
    txn = library.transaction()
    txn.put(ScopeRecord(TurnRef(C, turn_id(CHAT, 2, tag(PHONE))), "grimoire.scene-turn/9", {"future": True},
                        owner=OWNER), None)
    txn.commit(owner_handle("campaigns"))
    for call in (lambda: lib.write(post_turn(CHAT, 2, "Racing."), None, after=read.last),
                 lambda: lib.write(post_turn(CHAT, 1, "Pause."), first, last=True)):
        with pytest.raises(SceneRefused) as error:
            call()
        assert error.value.reason == "turn_changed"
    assert [t.record.number for t in lib.turns(CHAT).turns] == [1]


def test_a_last_turn_write_waits_for_no_later_turn(library):
    lib = scenes(library)
    first = lib.write(post_turn(CHAT, 1), None)
    lib.write(post_turn(CHAT, 2), None, after=first)  # play went on before the pause landed
    with pytest.raises(SceneRefused) as error:
        lib.write(post_turn(CHAT, 1, "Paused."), first, last=True)
    assert error.value.reason == "turn_changed"
    assert lib.turns(CHAT).turns[0].record.post.text == "Hello."


def test_a_play_write_over_an_existing_turn_waits_for_no_pause_anywhere_in_the_chat(library):
    """016: a keep, a pass or a run's start on the open turn is refused under the lock while any turn of the
    chat holds a pause written since it was read (by sync, say, on an earlier turn); a mark still goes on."""
    lib = scenes(library)
    first = lib.write(post_turn(CHAT, 1), None)
    second = lib.write(post_turn(CHAT, 2), None, after=first)
    txn = library.transaction()
    paused = replace(first.record, paused=True, paused_by=OWNER)
    txn.put(ScopeRecord(first.record.ref, TURN_SCHEMA, turn_to_payload(paused), owner=first.owner), first.etag)
    txn.commit(owner_handle("campaigns"))
    with pytest.raises(SceneRefused) as error:
        lib.write(post_turn(CHAT, 2, "Kept over a pause."), second, unpaused=True)
    assert error.value.reason == "turn_changed"
    lib.write(post_turn(CHAT, 2, "A mark."), second)
    assert lib.turns(CHAT).paused
