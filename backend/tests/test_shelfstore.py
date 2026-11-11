"""The shelf's files (docs/SHELF.md): one record per entry, chat and saved filter, etag-checked writes, files that
are not records reported and left alone, and every write explored under the fault harness (docs/TESTING.md)."""

from __future__ import annotations

import json
import os

import pytest
from store_helpers import FAIL_RECOVERY, drop_empty_dirs, explore_mode

from grimoire.core.identity import ChatRef, SourceRef
from grimoire.shelf import MAX_FILTERS, SavedFilter, ShelfError, record_name
from grimoire.shelfstore import MAX_RECORD_BYTES, ShelfStore
from grimoire.store import StaleWrite, etag
from grimoire.testing import (
    CrashScenario, Interleave, Target, atomic_outcome, completes_after, explore_crashes, idempotent_recovery,
    no_residue, preserves_interleave,
)

WREN = SourceRef.parse("world:harbor/character/wren")
TESS = SourceRef.parse("world:harbor/character/tess")
CHAT = ChatRef.parse("campaign:quay#chat/main")
ENTRY = f"config/shelf/entries/{record_name(WREN)}.json"


def _refused(reason: str, work) -> ShelfError:  # type: ignore[no-untyped-def]
    with pytest.raises(ShelfError) as refused:
        work()
    assert refused.value.reason == reason, str(refused.value)
    return refused.value


def _files(root) -> list[str]:  # type: ignore[no-untyped-def]
    return sorted(str(path.relative_to(root)).replace(os.sep, "/") for path in root.rglob("*") if path.is_file())


# -- records ---------------------------------------------------------------------------------------------
def test_a_library_with_no_shelf_reads_as_empty_and_nothing_is_made_by_reading(tmp_path):
    view = ShelfStore(tmp_path).view()
    assert (dict(view.entries), dict(view.chats), dict(view.filters), view.problems) == ({}, {}, {}, ())
    assert list(tmp_path.iterdir()) == []


def test_tags_and_pins_are_one_small_file_per_entry_and_per_chat(tmp_path):
    shelf = ShelfStore(tmp_path)
    tagged = shelf.set_tags(WREN, ["noir", "Crew"], None)
    pinned = shelf.pin_entry(WREN, True, tagged.etag)
    shelf.pin_entry(TESS, True, None)
    shelf.pin_chat(CHAT, True, None)
    assert _files(tmp_path) == sorted([ENTRY, f"config/shelf/entries/{record_name(TESS)}.json",
                                       f"config/shelf/chats/{record_name(CHAT)}.json"])
    assert json.loads((tmp_path / ENTRY).read_text(encoding="utf-8")) == {
        "format": "grimoire.shelf.entry/1", "ref": str(WREN), "tags": ["Crew", "noir"], "pinned": True}
    view = ShelfStore(tmp_path).view()
    assert view.entries[WREN] == pinned and pinned.etag == etag((tmp_path / ENTRY).read_bytes())
    assert view.entries[TESS].value.pinned and view.chats[CHAT].value.pinned and view.problems == ()


def test_the_same_marks_are_the_same_bytes_whoever_writes_them(tmp_path):
    one, two = ShelfStore(tmp_path / "desk"), ShelfStore(tmp_path / "phone")
    first = one.pin_entry(WREN, True, one.set_tags(WREN, ["noir", "crew"], None).etag)
    second = two.set_tags(WREN, ["crew", "noir"], two.pin_entry(WREN, True, None).etag)
    assert first.etag == second.etag  # no time, no device, no order: two devices agree by content


def test_marks_that_say_nothing_any_more_leave_no_file(tmp_path):
    shelf = ShelfStore(tmp_path)
    tagged = shelf.set_tags(WREN, ["crew"], None)
    assert shelf.set_tags(WREN, [], tagged.etag) is None and _files(tmp_path) == []
    pinned = shelf.pin_chat(CHAT, True, None)
    assert shelf.pin_chat(CHAT, False, pinned.etag) is None and _files(tmp_path) == []
    assert shelf.pin_entry(WREN, False, None) is None and shelf.edit_tags(WREN, [], ["crew"], None) is None


def test_editing_tags_leaves_the_others_and_the_pin(tmp_path):
    shelf = ShelfStore(tmp_path)
    start = shelf.pin_entry(WREN, True, shelf.set_tags(WREN, ["crew", "noir"], None).etag)
    edited = shelf.edit_tags(WREN, ["harbor", "CREW"], ["Noir"], start.etag)
    assert edited.value.tags == ("crew", "harbor") and edited.value.pinned is True


def test_a_stale_write_is_refused_and_nothing_changes(tmp_path):
    shelf = ShelfStore(tmp_path)
    seen = shelf.set_tags(WREN, ["crew"], None)
    shelf.pin_entry(WREN, True, seen.etag)  # another page, or the other device, moved it on
    before = (tmp_path / ENTRY).read_bytes()
    for write in (lambda: shelf.set_tags(WREN, ["noir"], seen.etag), lambda: shelf.pin_entry(WREN, False, seen.etag),
                  lambda: shelf.set_tags(WREN, ["noir"], None), lambda: shelf.edit_tags(WREN, ["x"], [], seen.etag)):
        with pytest.raises(StaleWrite):
            write()
    assert (tmp_path / ENTRY).read_bytes() == before
    with pytest.raises(StaleWrite):  # a record the client saw that is gone now
        shelf.pin_entry(TESS, True, seen.etag)
    with pytest.raises(StaleWrite):
        shelf.pin_chat(CHAT, True, seen.etag)
    _refused("invalid_field", lambda: shelf.pin_entry(TESS, True, "not-an-etag"))
    assert _files(tmp_path) == [ENTRY]


def test_marks_never_name_a_revision_and_bad_tags_write_nothing(tmp_path):
    shelf = ShelfStore(tmp_path)
    _refused("invalid_ref", lambda: shelf.pin_entry(WREN.at("r3"), True, None))
    _refused("invalid_tag", lambda: shelf.set_tags(WREN, ["fine", "a,b"], None))
    _refused("invalid_tag", lambda: shelf.edit_tags(WREN, ["  "], [], None))
    _refused("too_many_tags", lambda: shelf.set_tags(WREN, [f"t{n}" for n in range(40)], None))
    assert _files(tmp_path) == []


# -- saved filters ---------------------------------------------------------------------------------------
def test_saved_filters_are_kept_renamed_and_deleted_at_the_etag_read(tmp_path):
    shelf = ShelfStore(tmp_path)
    crew = shelf.save_filter(SavedFilter("f-crew", "Crew", role="character", tags=("crew",)))
    shelf.save_filter(SavedFilter("f-cards", "Cards by name", shelf="cards", sort="name"))
    assert set(ShelfStore(tmp_path).view().filters) == {"f-crew", "f-cards"}
    _refused("filter_exists", lambda: shelf.save_filter(SavedFilter("f-crew", "Another")))
    _refused("filter_name_taken", lambda: shelf.save_filter(SavedFilter("f-3", "  crew ")))
    _refused("filter_name_taken", lambda: shelf.rename_filter("f-cards", "CREW", shelf.view().filters["f-cards"].etag))
    renamed = shelf.rename_filter("f-crew", "The crew", crew.etag)
    assert renamed.value.name == "The crew" and renamed.value.tags == ("crew",)
    with pytest.raises(StaleWrite):
        shelf.rename_filter("f-crew", "Again", crew.etag)
    with pytest.raises(StaleWrite):
        shelf.delete_filter("f-crew", crew.etag)
    _refused("invalid_name", lambda: shelf.rename_filter("f-crew", " ", renamed.etag))
    shelf.delete_filter("f-crew", renamed.etag)
    assert set(shelf.view().filters) == {"f-cards"}
    _refused("no_filter", lambda: shelf.delete_filter("f-crew", renamed.etag))
    _refused("no_filter", lambda: shelf.rename_filter("f-gone", "Name", None))
    _refused("invalid_filter", lambda: shelf.delete_filter("../escape", None))


def test_there_is_a_most_of_saved_filters(tmp_path):
    shelf = ShelfStore(tmp_path)
    for number in range(MAX_FILTERS):
        shelf.save_filter(SavedFilter(f"f-{number}", f"Filter {number}"))
    _refused("too_many_filters", lambda: shelf.save_filter(SavedFilter("f-more", "One more")))


# -- files that are not records --------------------------------------------------------------------------
def test_a_file_that_is_not_a_record_is_reported_and_the_rest_still_reads(tmp_path):
    shelf = ShelfStore(tmp_path)
    shelf.pin_entry(TESS, True, None)
    entries = tmp_path / "config" / "shelf" / "entries"
    (entries / f"{record_name(WREN)}.json").write_bytes(b'{"format": "grimoire.shelf.entry/1", "ref": ')  # cut short
    (entries / ("0" * 32 + ".json")).write_bytes(json.dumps(
        {"format": "grimoire.shelf.entry/1", "ref": str(WREN), "tags": [], "pinned": True}).encode())
    (entries / ("1" * 32 + ".json")).write_bytes(json.dumps(
        {"format": "grimoire.shelf.entry/7", "ref": str(WREN), "tags": [], "pinned": True, "colour": "red"}).encode())
    (entries / ("2" * 32 + ".json")).write_bytes(b"x" * (MAX_RECORD_BYTES + 1))
    (entries / "notes.txt").write_text("mine", encoding="utf-8")
    (entries / (record_name(TESS) + ".sync-conflict-2.json")).write_bytes(b"{}")  # what another sync tool leaves
    (entries / ".DS_Store").write_bytes(b"noise")
    (entries / "folder.json").mkdir()
    before = {path.name: path.read_bytes() for path in entries.iterdir() if path.is_file()}
    view = ShelfStore(tmp_path).view()
    assert set(view.entries) == {TESS}
    reasons = {problem.path.rsplit("/", 1)[1]: problem.reason for problem in view.problems}
    assert reasons == {f"{record_name(WREN)}.json": "malformed", "0" * 32 + ".json": "address_mismatch",
                       "1" * 32 + ".json": "unsupported_format", "2" * 32 + ".json": "too_large",
                       "notes.txt": "unexpected_file", record_name(TESS) + ".sync-conflict-2.json": "malformed",
                       "folder.json": "unreadable"}
    assert all(problem.path.startswith("config/shelf/entries/") and problem.detail for problem in view.problems)
    assert {path.name: path.read_bytes() for path in entries.iterdir() if path.is_file()} == before  # left alone


def test_a_record_that_cannot_be_read_is_never_written_over(tmp_path):
    shelf = ShelfStore(tmp_path)
    shelf.pin_entry(WREN, True, None)
    path = tmp_path / ENTRY
    path.write_bytes(b'{"format": "grimoire.shelf.entry/2", "ref": "world:harbor/character/wren", "stars": 5}\n')
    held = path.read_bytes()
    for expected in (None, etag(held)):
        error = _refused("unreadable_record", lambda expected=expected: shelf.set_tags(WREN, ["crew"], expected))
        assert ENTRY in str(error) and "unsupported_format" in str(error)
    assert path.read_bytes() == held


def test_a_link_is_never_followed(tmp_path):
    outside = tmp_path / "outside"
    (outside / "shelf" / "entries").mkdir(parents=True)
    library = tmp_path / "library"
    library.mkdir()
    try:
        os.symlink(outside, library / "config", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this system makes no links")
    shelf = ShelfStore(library)
    assert [(problem.path, problem.reason) for problem in shelf.view().problems] == [("config", "link")]
    _refused("link", lambda: shelf.pin_entry(WREN, True, None))
    shelf.recover()
    assert list((outside / "shelf" / "entries").iterdir()) == []


def test_recover_sweeps_a_dead_writes_temp_file_and_nothing_else(tmp_path):
    shelf = ShelfStore(tmp_path)
    pinned = shelf.pin_entry(WREN, True, None)
    stray = tmp_path / "config" / "shelf" / "entries" / ".shelf-abc.tmp"
    stray.write_bytes(b"half a rec")
    assert ShelfStore(tmp_path).view().problems == ()  # a temp file is not a problem to report
    shelf.recover()
    assert _files(tmp_path) == [ENTRY] and shelf.view().entries[WREN] == pinned
    stray.write_bytes(b"again")
    shelf.pin_entry(TESS, True, None)  # the next write of that folder sweeps it too
    assert not stray.exists()


# -- crashes ---------------------------------------------------------------------------------------------
def _seed(ops, root):  # type: ignore[no-untyped-def]
    shelf = ShelfStore(root, ops=ops)
    shelf.pin_entry(TESS, True, None)
    shelf.set_tags(WREN, ["crew"], None)
    shelf.pin_chat(CHAT, True, None)
    shelf.save_filter(SavedFilter("f-crew", "Crew", tags=("crew",)))


def _empty(ops, root):  # type: ignore[no-untyped-def]
    ops.write_bytes(root / "grimoire-library.json", b"{}\n")


def first_tag(ops, root):  # type: ignore[no-untyped-def]
    ShelfStore(root, ops=ops).set_tags(WREN, ["noir", "crew"], None)


def replace_marks(ops, root):  # type: ignore[no-untyped-def]
    shelf = ShelfStore(root, ops=ops)
    shelf.pin_entry(WREN, True, shelf.view().entries[WREN].etag)


def remove_marks(ops, root):  # type: ignore[no-untyped-def]
    shelf = ShelfStore(root, ops=ops)
    shelf.pin_chat(CHAT, False, shelf.view().chats[CHAT].etag)


def rename_filter(ops, root):  # type: ignore[no-untyped-def]
    shelf = ShelfStore(root, ops=ops)
    shelf.rename_filter("f-crew", "The crew", shelf.view().filters["f-crew"].etag)


def _recover(ops, root):  # type: ignore[no-untyped-def]
    shelf = ShelfStore(root, ops=ops)
    shelf.recover()
    assert shelf.view().problems == ()  # whatever happened, every record still reads


SCENARIOS = {
    "first-tag": (first_tag, _empty, Target("replace", target="config/shelf/entries/*.json")),
    "replace": (replace_marks, _seed, Target("replace", target=ENTRY)),
    "remove": (remove_marks, _seed, Target("remove", path="config/shelf/chats/*.json")),
    "rename-filter": (rename_filter, _seed, Target("replace", target="config/shelf/filters/f-crew.json")),
}


def _scenario(name: str) -> CrashScenario:
    action, setup, commit = SCENARIOS[name]
    return CrashScenario(name=f"shelf {name}", setup=setup, action=action, recover=_recover,
                         invariants=(atomic_outcome(), idempotent_recovery(), no_residue("*.tmp"),
                                     completes_after(commit)),
                         normalize=drop_empty_dirs)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
@pytest.mark.parametrize("mode", ["errors", "recovery-crashes", FAIL_RECOVERY])
def test_shelf_writes_survive_every_crash_error_and_recovery_crash(tmp_path, name, mode):
    report = explore_crashes(_scenario(name), tmp_path, **explore_mode(mode))
    report.assert_ok()
    assert report.points_explored >= (1 if name == "remove" else 3)


def test_an_edit_made_while_a_write_is_under_way_is_refused_not_lost(tmp_path):
    """Another hand changes the record between this write's read and its rename: the write is refused and the
    other edit stays in place (the harness's ``preserves_interleave``)."""
    other = json.dumps({"format": "grimoire.shelf.entry/1", "ref": str(WREN), "tags": ["theirs"], "pinned": False},
                       sort_keys=True, indent=2).encode() + b"\n"

    def meddle(ops, root):  # type: ignore[no-untyped-def]
        ops.write_bytes(root / ENTRY, other)

    def action(ops, root):  # type: ignore[no-untyped-def]
        with pytest.raises(StaleWrite):
            replace_marks(ops, root)

    scenario = CrashScenario(name="shelf interleaved edit", setup=_seed, action=action, recover=_recover,
                             invariants=(idempotent_recovery(), no_residue("*.tmp"), preserves_interleave()))
    report = explore_crashes(scenario, tmp_path, interleaves=[
        Interleave(Target("write_bytes", path="config/shelf/entries/.shelf-*"), meddle)])
    report.assert_ok()
    assert report.runs[0].recovered[ENTRY] == other and report.points_explored >= 3
