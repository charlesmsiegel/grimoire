"""071: chats and actors written before the role table still read, compose and take edits.

``fixtures/chats_071/library`` is a small synthetic library the chat library wrote before 071 folded
its role decisions into ``ROLES``: a character launch with a persona, a lorebook, a second character
and a detached scenario; a scenario-launched chat of the same campaign reusing the first chat's Mara;
and two same-species goblins under the generic actor role, one detached by its instance. It is a
supported on-disk compatibility fixture: never regenerate it to get this test green. Each test works
on a copy, so the fixture itself is never written.
"""

import json
import shutil
from pathlib import Path

import pytest

from grimoire.chatstore import ChatLibrary
from grimoire.chats import CAMPAIGN_CONTINUITY, NOT_WITNESSED, Launch, chat_to_payload
from grimoire.core.identity import ChatRef, InstanceRef, ScopeRef
from grimoire.effective import chat_snapshot
from grimoire.store import LibraryStore
from grimoire.testing.fileops import snapshot

from chat_fixtures import ANYA, MARA, Clock, Ids, chat_library
from party_fixtures import GRUB, SNIK, SPECIES
from store_helpers import OWNER, owner_handle

FIXTURE = Path(__file__).parent / "fixtures" / "chats_071" / "library"
FIRST, SECOND, CAMP = (ChatRef.parse(text) for text in
                       ("campaign:q-1#chat/t-1", "campaign:q-1#chat/t-2", "campaign:c#chat/t-3"))
MARA_1, ANYA_1, SAM_1 = (InstanceRef("q-1", "character", name) for name in ("mara-1", "anya-1", "sam-1"))


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "v2"
    shutil.copytree(FIXTURE, path)
    return path


def test_every_stored_chat_reads_unchanged_and_composes(root):
    before = snapshot(root)
    with LibraryStore.open(root) as store:
        chats = chat_library(store)
        for chat in (FIRST, SECOND, CAMP):
            view = chats.open_chat(chat)
            assert view.broken == () and view.problems == ()
            stored = json.loads((root / "campaigns" / chat.campaign_id / "chats" / f"{chat.chat_id}.json").read_text())
            assert chat_to_payload(view.record) == stored["payload"]  # nothing reinterpreted or dropped
            assert chat_snapshot(store.load(), chat).problems == ()
        first, second, camp = (chats.open_chat(chat).record for chat in (FIRST, SECOND, CAMP))
        assert {(b.role, b.actor) for b in first.active_bindings() if b.actor} == {
            ("character", MARA_1), ("character", ANYA_1), ("persona", SAM_1)}
        assert [b.role for b in first.bindings if not b.active] == ["scenario"]
        assert second.roster_entry(MARA_1).prior_history == CAMPAIGN_CONTINUITY
        assert [b.actor for b in camp.active_bindings("actor")] == [SNIK]
        assert {entry.actor for entry in camp.roster} == {SNIK, GRUB}
        assert chat_snapshot(store.load(), CAMP).snapshot.entity(SNIK).name == "Snik"  # its typed record composes
        assert set(chats.navigation().by_source(MARA)) == {FIRST, SECOND}
    assert {path: tree for path, tree in snapshot(root).items() if not path.startswith(".txn")} == before


def later_ids() -> Ids:
    """Ids past the fixture's own (q-1, t-1..t-3), as the device that wrote it would continue."""
    ids = Ids()
    ids.counts.update({"q": 1, "t": 3})
    return ids


def test_stored_chats_take_new_edits_under_the_same_rules(root):
    with LibraryStore.open(root) as store:
        chats = ChatLibrary(store, owner_handle(), clock=Clock(), new_id=later_ids(), owner=OWNER)
        untouched = store.read(FIRST).etag
        # A source bound again without a reuse gets a new actor of its own kind, beside the stored ones.
        (new_anya,) = chats.attach_source(SECOND, ANYA, "character", expected=chats.open_chat(SECOND).etag).commit().actors
        assert new_anya.kind == "character" and new_anya not in (MARA_1, ANYA_1)
        assert chats.open_chat(SECOND).record.roster_entry(new_anya).prior_history == NOT_WITNESSED
        # The same-species goblins: Grub returns by its instance, Snik leaves by its instance.
        chats.attach_source(CAMP, SPECIES.ref, "actor", actor=GRUB, expected=chats.open_chat(CAMP).etag).commit()
        chats.detach_source(CAMP, SPECIES.ref, "actor", actor=SNIK, expected=chats.open_chat(CAMP).etag).commit()
        assert [b.actor for b in chats.open_chat(CAMP).record.active_bindings("actor")] == [GRUB]
        # A new chat of the old campaign continues the stored Mara.
        third = chats.start_chat(Launch("character", MARA), scope=ScopeRef("q-1"),
                                 reuse_actors={("character", MARA.entity_key): MARA_1}).commit()
        assert third.actors == ()
        assert chats.open_chat(third.chat).record.roster_entry(MARA_1).prior_history == CAMPAIGN_CONTINUITY
        assert store.read(FIRST).etag == untouched  # a chat nobody edited is never rewritten
    with LibraryStore.open(root) as store:
        chats = chat_library(store)
        for chat in (FIRST, SECOND, CAMP, third.chat):
            assert chats.open_chat(chat).broken == ()
