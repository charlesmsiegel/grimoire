"""The transcript store family (Sprint 036): create-only pages, read per chat, never loaded, synced as immutable.

Every fixture is synthetic under ``tmp_path``. No provider is imported.
"""

from __future__ import annotations

import shutil

import pytest

from grimoire.chats import Launch
from grimoire.chatstore import campaign_taken_ids
from grimoire.core.identity import ChatRef, InstanceRef, TranscriptRef
from grimoire.store import ImmutableRevision, LibraryStore, RecordNotFound, ScopeRecord, StaleWrite
from grimoire.store.errors import TakenIdsUnlisted
from grimoire.store.jsonfmt import thaw
from grimoire.store.layout import address_parts, classify
from grimoire.sync import syncable
from grimoire.sync.model import CAMPAIGN_LOCAL, IMMUTABLE_KINDS, SYNCED_KINDS
from grimoire.transcripts import (
    TRANSCRIPT_SCHEMA, Post, Speaker, TranscriptError, TranscriptPage, Variant, page_from_payload,
    page_to_payload,
)

from chat_fixtures import MARA, chat_library, seed_sources
from store_helpers import OWNER, owner_handle


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed_sources(opened)
        yield opened


@pytest.fixture
def chat(store) -> ChatRef:
    return chat_library(store).start_chat(Launch("character", MARA)).commit().chat


def page(chat: ChatRef, seq: int, *texts: str, actor: InstanceRef | None = None) -> ScopeRecord:
    ref = TranscriptRef(chat.campaign_id, chat.chat_id, seq)
    posts = tuple(Post(f"p{seq}-{i}", Speaker("character", actor, "Mara"), (Variant(text),), 0)
                  for i, text in enumerate(texts))
    return ScopeRecord(ref, TRANSCRIPT_SCHEMA, page_to_payload(TranscriptPage(ref, posts)), owner=OWNER)


def put(store, *records, expected=None):
    txn = store.transaction()
    for record in records:
        txn.put(record, expected)
    return txn.commit(owner_handle())


def test_a_ref_round_trips_and_names_its_file(chat):
    ref = TranscriptRef(chat.campaign_id, chat.chat_id, 7)
    assert TranscriptRef.parse(str(ref)) == ref
    assert str(ref).endswith(f"#transcript/{chat.chat_id}/0000000007")
    parts = address_parts(ref)
    assert parts == ("campaigns", chat.campaign_id, "transcripts", chat.chat_id, "0000000007.json")
    assert classify(parts) == ("transcript", ref)
    assert ref.chat == chat and ref.scope == chat.scope


@pytest.mark.parametrize("seq", [0, -1, 10**10, True, "1"])
def test_a_ref_refuses_a_page_number_outside_its_spelling(seq):
    with pytest.raises((ValueError, TypeError)):
        TranscriptRef("c", "t", seq)  # type: ignore[arg-type]


def test_classify_ignores_names_that_are_not_pages():
    assert classify(("campaigns", "c", "transcripts", "t", "0000000000.json")) is None
    assert classify(("campaigns", "c", "transcripts", "t", "1.json")) is None
    assert classify(("campaigns", "c", "transcripts", "t", "notes.json")) is None


def test_pages_are_create_only(store, chat):
    first = page(chat, 1, "Hello.")
    put(store, first)
    stored = store.read(first.address)
    assert page_from_payload(first.address, stored.record.schema, stored.record.payload).posts[0].text == "Hello."
    with pytest.raises(ImmutableRevision):
        put(store, page(chat, 1, "Rewritten."), expected=stored.etag)
    with pytest.raises(StaleWrite):
        put(store, page(chat, 1, "Rewritten."))
    assert store.read(first.address).etag == stored.etag


def test_a_chat_reads_its_pages_in_order_and_load_never_reads_them(store, chat):
    put(store, page(chat, 2, "Second."), page(chat, 1, "First."), page(chat, 10, "Tenth."))
    found = store.chat_transcript(chat)
    assert found.problems == ()
    assert [ref.seq for ref in found.records] == [1, 2, 10]
    other = ChatRef(chat.campaign_id, "elsewhere")
    assert store.chat_transcript(other).records == {}
    assert len(store.campaign_transcripts(chat.campaign_id).records) == 3
    view = store.load()
    assert view.problems == ()
    assert not any("transcript" in str(key) for key in view.chats)


def test_a_damaged_page_is_contained_and_reported(store, chat):
    put(store, page(chat, 1, "First."))
    folder = store.root / "campaigns" / chat.campaign_id / "transcripts" / chat.chat_id
    (folder / "0000000002.json").write_text("{not json", encoding="utf-8")
    (folder / "stray.txt").write_text("x", encoding="utf-8")
    found = store.chat_transcript(chat)
    assert [ref.seq for ref in found.records] == [1]
    assert {problem.path.rsplit("/", 1)[-1] for problem in found.problems} >= {"0000000002.json", "stray.txt"}
    assert store.load().problems == ()  # load() does not read transcripts


def test_a_look_alike_transcripts_folder_is_reported(store, chat):
    put(store, page(chat, 1, "First."))
    campaign = store.root / "campaigns" / chat.campaign_id
    if (campaign / "TRANSCRIPTS").exists():
        pytest.skip("this file system folds case, so no look-alike folder can exist")
    shutil.copytree(campaign / "transcripts", campaign / "Transcripts")
    found = store.chat_transcript(chat)
    assert any("Transcripts" in problem.path for problem in found.problems)


def test_pages_reserve_the_instance_ids_they_name(store, chat):
    ghost = InstanceRef(chat.campaign_id, "character", "ghost-1")
    put(store, page(chat, 1, "Boo.", actor=ghost))
    assert "ghost-1" in campaign_taken_ids(store, chat.campaign_id)


def test_an_unreadable_page_fails_allocation_closed(store, chat):
    folder = store.root / "campaigns" / chat.campaign_id / "transcripts" / chat.chat_id
    folder.mkdir(parents=True)
    (folder / "0000000001.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(TakenIdsUnlisted):
        campaign_taken_ids(store, chat.campaign_id)


def test_sync_carries_pages_as_immutable_campaign_records(chat):
    path = "/".join(address_parts(TranscriptRef(chat.campaign_id, chat.chat_id, 1)))
    assert syncable(path) == "transcript"
    assert "transcript" in SYNCED_KINDS and "transcript" in CAMPAIGN_LOCAL and "transcript" in IMMUTABLE_KINDS


def test_a_payload_that_repeats_its_identity_is_refused(store, chat):
    ref = TranscriptRef(chat.campaign_id, chat.chat_id, 1)
    record = page(chat, 1, "Hi.")
    payload = {**thaw(record.payload), "seq": 1}
    put(store, ScopeRecord(ref, TRANSCRIPT_SCHEMA, payload, owner=OWNER))  # the store never interprets payloads
    stored = store.read(ref)
    with pytest.raises(ValueError) as caught:
        page_from_payload(ref, stored.record.schema, stored.record.payload)
    assert caught.value.reason == "identity_in_payload"  # type: ignore[attr-defined]


@pytest.mark.parametrize("role", [{}, [], ["user"], 1, None])
def test_a_speaker_role_that_is_not_text_is_refused_not_raised(chat, role):
    ref = TranscriptRef(chat.campaign_id, chat.chat_id, 1)
    payload = thaw(page(chat, 1, "Hi.").payload)
    payload["posts"][0]["speaker"]["role"] = role
    with pytest.raises(TranscriptError):  # a ValueError a reader contains, never a TypeError
        page_from_payload(ref, TRANSCRIPT_SCHEMA, payload)
    with pytest.raises(TranscriptError):
        Speaker(role)


def test_reading_a_missing_page_is_not_found(store, chat):
    with pytest.raises(RecordNotFound):
        store.read(TranscriptRef(chat.campaign_id, chat.chat_id, 1))


def test_sync_never_delivers_a_page_without_its_chat(tmp_path):
    from sync_helpers import DESKTOP, PHONE, handles
    from grimoire.sync import identify, sync
    desktop, phone = LibraryStore.create(tmp_path / DESKTOP), LibraryStore.create(tmp_path / PHONE)
    try:
        seed_sources(desktop)
        identify(desktop, DESKTOP)
        identify(phone, PHONE)
        sync(desktop, phone, handles())
        chat = chat_library(desktop).start_chat(Launch("character", MARA)).commit().chat
        put(desktop, page(chat, 1, "Hi."))
        folder = phone.root / "campaigns" / chat.campaign_id / "chats"
        folder.mkdir(parents=True)
        (folder / f"{chat.chat_id}.json").mkdir()  # a folder where the chat goes: it cannot land on the phone
        sync(desktop, phone, handles())
        assert not (phone.root / "campaigns" / chat.campaign_id / "chats" / f"{chat.chat_id}.json").is_file()
        assert not (phone.root / "campaigns" / chat.campaign_id / "transcripts").exists()
    finally:
        desktop.close()
        phone.close()


def test_sync_never_delivers_a_page_whose_chat_the_origin_lost(tmp_path):
    from sync_helpers import DESKTOP, PHONE, handles
    from grimoire.sync import identify, sync
    desktop, phone = LibraryStore.create(tmp_path / DESKTOP), LibraryStore.create(tmp_path / PHONE)
    try:
        seed_sources(desktop)
        identify(desktop, DESKTOP)
        identify(phone, PHONE)
        sync(desktop, phone, handles())
        chat = chat_library(desktop).start_chat(Launch("character", MARA)).commit().chat
        put(desktop, page(chat, 1, "Hi."))
        (desktop.root / "campaigns" / chat.campaign_id / "chats" / f"{chat.chat_id}.json").unlink()
        sync(desktop, phone, handles())
        assert not (phone.root / "campaigns" / chat.campaign_id / "transcripts").exists()
    finally:
        desktop.close()
        phone.close()

