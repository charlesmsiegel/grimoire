"""A chat's bookmarks: the pure record and its codec, the ``bookmarks/`` family over a store, its writer
under the fault harness, and two devices that each add one offline. Synthetic content only.
"""

from __future__ import annotations

import functools
import json
import tempfile
from pathlib import Path

import pytest

from grimoire.core.identity import BookmarkRef, ChatRef, InstanceRef
from grimoire.scene import Post, SceneLine, SceneRefused, Take, TurnRecord
from grimoire.scene.bookmarks import (
    BOOKMARK_SCHEMA, MAX_BOOKMARK_LABEL, Bookmark, bookmark_chat_id, bookmark_from_payload, bookmark_to_payload,
    clean_label, new_bookmark_id,
)
from grimoire.scenestore import SceneLibrary
from grimoire.scenestore.bookmarks import BookmarkLibrary
from grimoire.store import LibraryStore, ProcessLocker
from grimoire.store.layout import classify
from grimoire.testing import (
    CrashScenario, Target, atomic_outcome, completes_after, explore_crashes, idempotent_recovery, no_residue,
)
from grimoire.testing.fileops import snapshot

from store_helpers import OWNER, drop_empty_dirs, explore_mode, owner_handle

LIB = "lib"
CHAT = ChatRef("q-1", "t-1")
OTHER = ChatRef("q-1", "t-2")
PC, NPC = InstanceRef("q-1", "character", "pc"), InstanceRef("q-1", "character", "npc")
TURN = "t-1.000001"


def turn(chat: ChatRef = CHAT, number: int = 1, follows: str | None = None) -> TurnRecord:
    post = Post("human:ana", PC, "speech", "Hello?", (NPC, PC), "speech")
    line = SceneLine(f"{chat.chat_id}.{number:06d}.1.1", NPC, "Npc", "Evening.", (NPC, PC))
    take = Take(1, "kept", "generated", (NPC, PC), lines=(line,), by="human:ana")
    return TurnRecord(chat, number, "post", post, (take,), follows=follows)


def counter():
    drawn = iter(f"{n:012x}" for n in range(1, 10_000))
    return lambda: next(drawn)


@pytest.fixture
def library(tmp_path):
    with LibraryStore.create(tmp_path / LIB) as store:
        scenes = SceneLibrary(store, owner_handle(), owner=OWNER)
        scenes.write(turn(), None)
        scenes.write(turn(OTHER), None)
        yield BookmarkLibrary(scenes, unique=counter())


def refused(call, *args, **kwargs) -> str:
    with pytest.raises(SceneRefused) as caught:
        call(*args, **kwargs)
    return caught.value.reason


# -- the record and its codec ------------------------------------------------------------------------------

def test_a_bookmark_round_trips_through_its_payload():
    bookmark = Bookmark(CHAT, "t-1.bm-0a1b2c3d-000000000001", TURN, "The forged seal")
    payload = bookmark_to_payload(bookmark)
    assert payload == {"chat": "campaign:q-1#chat/t-1", "turn": TURN, "label": "The forged seal", "removed": False}
    assert bookmark_from_payload(bookmark.ref, BOOKMARK_SCHEMA, json.loads(json.dumps(payload))) == bookmark
    assert str(bookmark.ref) == "campaign:q-1#bookmark/t-1.bm-0a1b2c3d-000000000001"
    assert BookmarkRef.parse(str(bookmark.ref)) == bookmark.ref
    assert classify(("campaigns", "q-1", "bookmarks", "t-1.bm-0a1b2c3d-000000000001.json")) == ("bookmark", bookmark.ref)


def test_a_label_is_trimmed_to_one_line_and_blank_is_none():
    assert clean_label("  the   forged\n seal ") == "the forged seal"
    assert clean_label("   ") is None and clean_label(None) is None
    assert clean_label("x" * MAX_BOOKMARK_LABEL) == "x" * MAX_BOOKMARK_LABEL
    for bad in ("x" * (MAX_BOOKMARK_LABEL + 1), 7, ["a"], "bell\x07"):
        assert refused(clean_label, bad) == "invalid_field"


def test_a_bookmark_id_belongs_to_one_chat_and_differs_by_device():
    assert new_bookmark_id(CHAT, None, "abc") == "t-1.bm-abc"
    assert new_bookmark_id(CHAT, "0a1b2c3d", "abc") == "t-1.bm-0a1b2c3d-abc"
    dotted = ChatRef("q-1", "t-1.side")
    assert bookmark_chat_id(new_bookmark_id(dotted, None, "abc")) == "t-1.side"
    assert bookmark_chat_id("nodot") is None and bookmark_chat_id(7) is None
    assert refused(new_bookmark_id, CHAT, None, "a.b") == "invalid_field"


@pytest.mark.parametrize("change", [
    {"chat": "campaign:q-2#chat/t-1"},  # another campaign's chat
    {"chat": "t-1"}, {"chat": 4},
    {"turn": "t-2.000001"},  # another chat's turn
    {"turn": "t-1"}, {"turn": None},
    {"label": "  padded "}, {"label": 5}, {"label": "x" * (MAX_BOOKMARK_LABEL + 1)},
    {"removed": "no"}, {"removed": None},
    {"extra": 1},
])
def test_a_malformed_bookmark_is_refused_with_a_reason(change):
    ref = BookmarkRef("q-1", "t-1.bm-000000000001")
    payload = {"chat": str(CHAT), "turn": TURN, "label": None, "removed": False, **change}
    assert refused(bookmark_from_payload, ref, BOOKMARK_SCHEMA, payload) == "malformed"


def test_a_missing_key_another_schema_or_a_wrong_shape_is_refused():
    ref = BookmarkRef("q-1", "t-1.bm-000000000001")
    whole = {"chat": str(CHAT), "turn": TURN, "label": None, "removed": False}
    for key in whole:
        assert refused(bookmark_from_payload, ref, BOOKMARK_SCHEMA, {k: v for k, v in whole.items() if k != key}) == "malformed"
    assert refused(bookmark_from_payload, ref, "grimoire.chat-bookmark/2", whole) == "unsupported_schema"
    for shape in (None, [], "text", 3):
        assert refused(bookmark_from_payload, ref, BOOKMARK_SCHEMA, shape) == "malformed"
    # the id says one chat, the record another
    assert refused(bookmark_from_payload, BookmarkRef("q-1", "t-2.bm-000000000001"), BOOKMARK_SCHEMA, whole) == "malformed"


# -- the family over a store -------------------------------------------------------------------------------

def test_add_rename_and_remove_write_one_small_file_and_no_turn(library):
    store = library.store
    turns_before = {path: data for path, data in snapshot(store.root).items() if "/turns/" in f"/{path}"}
    added = library.add(CHAT, TURN, "  The forged   seal ")
    assert added.bookmark == Bookmark(CHAT, "t-1.bm-000000000001", TURN, "The forged seal")
    path = store.root / "campaigns" / "q-1" / "bookmarks" / "t-1.bm-000000000001.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schema"] == BOOKMARK_SCHEMA and document["payload"]["turn"] == TURN
    assert [item.bookmark.label for item in library.bookmarks(CHAT).bookmarks] == ["The forged seal"]

    renamed = library.rename(CHAT, added.bookmark.bookmark_id, "Seal", expected=added.etag)
    assert renamed.bookmark.label == "Seal" and renamed.etag != added.etag
    same = library.rename(CHAT, added.bookmark.bookmark_id, " Seal ", expected=renamed.etag)
    assert same.etag == renamed.etag  # nothing to change: nothing written
    cleared = library.rename(CHAT, added.bookmark.bookmark_id, "  ", expected=renamed.etag)
    assert cleared.bookmark.label is None

    library.remove(CHAT, added.bookmark.bookmark_id, expected=cleared.etag)
    listed = library.bookmarks(CHAT)
    assert listed.bookmarks == () and listed.removed == 1 and listed.problems == ()
    assert json.loads(path.read_text(encoding="utf-8"))["payload"]["removed"] is True  # marked, never deleted
    assert turns_before == {p: d for p, d in snapshot(store.root).items() if "/turns/" in f"/{p}"}


def test_a_stale_etag_an_unknown_bookmark_and_an_unknown_turn_are_refused(library):
    added = library.add(CHAT, TURN, "First")
    renamed = library.rename(CHAT, added.bookmark.bookmark_id, "Second", expected=added.etag)
    assert refused(library.rename, CHAT, added.bookmark.bookmark_id, "Third", expected=added.etag) == "changed"
    assert refused(library.remove, CHAT, added.bookmark.bookmark_id, expected=added.etag) == "changed"
    assert library.bookmarks(CHAT).bookmarks[0].bookmark.label == "Second"  # nothing half-written
    assert refused(library.rename, CHAT, "t-1.bm-nope", "x", expected=renamed.etag) == "unknown_bookmark"
    assert refused(library.rename, OTHER, added.bookmark.bookmark_id, "x", expected=renamed.etag) == "unknown_bookmark"
    for missing in ("t-1.000009", "t-2.000001", "nonsense", None):
        assert refused(library.add, CHAT, missing) == "unknown_turn"
    assert refused(library.add, CHAT, TURN, "x" * (MAX_BOOKMARK_LABEL + 1)) == "invalid_field"
    library.remove(CHAT, added.bookmark.bookmark_id, expected=renamed.etag)
    assert refused(library.remove, CHAT, added.bookmark.bookmark_id, expected=renamed.etag) == "unknown_bookmark"


def test_each_chat_lists_only_its_own_bookmarks(library):
    library.add(CHAT, TURN, "Ours")
    library.add(OTHER, "t-2.000001", "Theirs")
    assert [item.bookmark.label for item in library.bookmarks(CHAT).bookmarks] == ["Ours"]
    assert [item.bookmark.label for item in library.bookmarks(OTHER).bookmarks] == ["Theirs"]


def test_a_file_that_is_not_a_bookmark_is_reported_and_never_dropped_or_written_over(library):
    good = library.add(CHAT, TURN, "Good")
    folder = library.store.root / "campaigns" / "q-1" / "bookmarks"
    envelope = json.loads((folder / "t-1.bm-000000000001.json").read_text(encoding="utf-8"))
    (folder / "t-1.bm-broken.json").write_text("{not json", encoding="utf-8")
    newer = {**envelope, "address": "campaign:q-1#bookmark/t-1.bm-newer", "schema": "grimoire.chat-bookmark/2"}
    (folder / "t-1.bm-newer.json").write_text(json.dumps(newer), encoding="utf-8")
    wrong = {**envelope, "address": "campaign:q-1#bookmark/t-1.bm-wrong",
             "payload": {**envelope["payload"], "turn": "t-2.000001"}}
    (folder / "t-1.bm-wrong.json").write_text(json.dumps(wrong), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in folder.iterdir()}

    listed = library.bookmarks(CHAT)
    assert [item.bookmark.label for item in listed.bookmarks] == ["Good"]
    codes = {path.rsplit("/", 1)[-1]: code for path, code in listed.problems}
    assert codes["t-1.bm-newer.json"] == "unsupported_schema" and codes["t-1.bm-wrong.json"] == "malformed"
    assert "t-1.bm-broken.json" in codes
    assert library.bookmarks(OTHER).problems == ()  # plainly this chat's files: not the other chat's problem

    library.rename(CHAT, good.bookmark.bookmark_id, "Still good", expected=good.etag)
    library.add(CHAT, TURN, "Another")
    after = {path.name: path.read_bytes() for path in folder.iterdir()}
    for name in ("t-1.bm-broken.json", "t-1.bm-newer.json", "t-1.bm-wrong.json"):
        assert after[name] == before[name]


def test_a_new_id_never_lands_on_an_existing_file(tmp_path):
    with LibraryStore.create(tmp_path / LIB) as store:
        scenes = SceneLibrary(store, owner_handle(), owner=OWNER)
        scenes.write(turn(), None)
        drawn = iter(["same", "same", "same", "fresh"])
        library = BookmarkLibrary(scenes, unique=lambda: next(drawn))
        first = library.add(CHAT, TURN, "One")
        second = library.add(CHAT, TURN, "Two")
        assert (first.bookmark.bookmark_id, second.bookmark.bookmark_id) == ("t-1.bm-same", "t-1.bm-fresh")
        assert [item.bookmark.label for item in library.bookmarks(CHAT).bookmarks] == ["Two", "One"]


# -- under the fault harness -------------------------------------------------------------------------------

JOURNAL = Target("replace", target="lib/.txn/journal.json")
RESIDUE = no_residue("lib/.txn/*.new", "lib/.txn/journal.json")


@functools.cache
def template():
    with tempfile.TemporaryDirectory() as folder:
        with LibraryStore.create(Path(folder) / LIB, locker=ProcessLocker()) as store:
            scenes = SceneLibrary(store, owner_handle(), owner=OWNER)
            scenes.write(turn(), None)
            BookmarkLibrary(scenes, unique=lambda: "seed").add(CHAT, TURN, "Seed")
        return tuple(snapshot(folder).items())


def _setup(ops, root):
    for key, data in template():
        if data is None:
            ops.make_dirs(root / key)
        else:
            ops.write_bytes(root / key, data)


def _open(ops, root, locker):
    store = LibraryStore.open(root / LIB, ops=ops, locker=locker)
    return store, BookmarkLibrary(SceneLibrary(store, owner_handle(), owner=OWNER), unique=lambda: "next")


def add(ops, root, locker):
    store, library = _open(ops, root, locker)
    with store:
        library.add(CHAT, TURN, "Added")


def rename(ops, root, locker):
    store, library = _open(ops, root, locker)
    with store:
        (seed,) = library.bookmarks(CHAT).bookmarks
        library.rename(CHAT, seed.bookmark.bookmark_id, "Renamed", expected=seed.etag)


def remove(ops, root, locker):
    store, library = _open(ops, root, locker)
    with store:
        (seed,) = library.bookmarks(CHAT).bookmarks
        library.remove(CHAT, seed.bookmark.bookmark_id, expected=seed.etag)


@pytest.mark.parametrize("action, mode", [(action, mode) for action in (add, rename, remove)
                                          for mode in ("errors", "recovery-crashes")],
                         ids=lambda value: getattr(value, "__name__", value))
def test_bookmark_writes_survive_every_crash_error_and_recovery_crash(tmp_path, action, mode):
    locker = ProcessLocker()

    def recover(ops, root):
        LibraryStore.open(root / LIB, ops=ops, locker=locker).close()

    scenario = CrashScenario(name=f"bookmark {action.__name__}", setup=_setup,
                             action=lambda ops, root: action(ops, root, locker), recover=recover,
                             invariants=(atomic_outcome(), idempotent_recovery(), RESIDUE, completes_after(JOURNAL)),
                             normalize=drop_empty_dirs)
    report = explore_crashes(scenario, tmp_path, **explore_mode(mode))
    report.assert_ok()
    assert report.points_explored > 20
