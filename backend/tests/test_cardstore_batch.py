"""Several lorebook files on one card, updating a card by its exact identity, and a batch of cards as one write.

All through 037's importer: a batch is one image batch and so one store transaction, every card of it written
or none. Identity is the card's own bytes, never its name. Every card and picture is synthetic.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import tempfile
from pathlib import Path

import pytest

from card_fixtures import LOREBOOK, PORTRAIT, SCENARIO, V3, Clock, Gate, card, encode, importer, land, seed_world
from png_fixtures import png_card
from store_helpers import OWNER, drop_empty_dirs, explore_mode

from grimoire.cards import CardError, Loss
from grimoire.cardstore import (
    MAX_BATCH_CARDS, MAX_LOREBOOK_FILES, BatchCard, BatchCardRefused, CardImporter, outcome, plan_batch, plan_card,
)
from grimoire.core.identity import SourceRef
from grimoire.store import LibraryStore, ProcessLocker, StaleWrite, StoreError
from grimoire.store.errors import WriteDenied
from grimoire.store.jsonfmt import thaw
from grimoire.testing import CrashScenario, Target, atomic_outcome, completes_after, explore_crashes, idempotent_recovery, no_residue
from grimoire.testing.fileops import snapshot

MARA = SourceRef("card:mara-vell", "character", "mara-vell")
IMPORT = SourceRef("card:mara-vell", "card-import", "import")


def book(name: str, *entries: str) -> bytes:
    return encode({"spec": "lorebook_v3", "data": {"name": name, "extensions": {}, "entries": [
        {"keys": [text.split()[0]], "content": text, "enabled": True, "insertion_order": 0, "use_regex": False,
         "extensions": {}} for text in entries]}})


TIDES, WINDS, STARS = book("Tide tables", "tides turn at noon"), book("Wind lore", "winds come from the west"), \
    book("Star charts", "stars set late in summer")


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed_world(opened)
        yield opened


def record(store, ref=IMPORT) -> dict:
    return thaw(store.check_definition(ref).envelope.payload)


def named(document: dict, name: str, **changes) -> bytes:
    return encode(card(document, name=name, **changes))


# -- several lorebook files -----------------------------------------------------------------------

def test_several_lorebook_files_become_their_own_records_in_order(store):
    data = encode(V3)
    planned = importer(store).plan(data, lorebook=TIDES, lorebooks=[WINDS, STARS])
    kinds = sorted(str(ref) for ref in planned.report.records)
    assert kinds == ["card:mara-vell/character/mara-vell@r1", "card:mara-vell/lorebook/mara-vell-lore@r1",
                     "card:mara-vell/lorebook/star-charts@r1", "card:mara-vell/lorebook/tide-tables@r1",
                     "card:mara-vell/lorebook/wind-lore@r1"]
    fields = {piece.field for piece in planned.report.pieces}
    assert {"lorebook_file.data.entries[0]", "lorebook_file-2.data.entries[0]", "lorebook_file-3.data.entries[0]"} <= fields
    land(store, data, lorebook=TIDES, lorebooks=[WINDS, STARS])
    kept = record(store)
    assert kept["entities"] == {"main": "character/mara-vell", "lorebook": "lorebook/mara-vell-lore",
                                "lorebook_file": "lorebook/tide-tables", "lorebook_file-2": "lorebook/wind-lore",
                                "lorebook_file-3": "lorebook/star-charts"}
    assert base64.b64decode(kept["lorebook_file"]["base64"]) == TIDES
    assert [base64.b64decode(row["base64"]) for row in kept["lorebook_files"]] == [WINDS, STARS]
    assert [row["sha256"] for row in kept["lorebook_files"]] == [hashlib.sha256(raw).hexdigest() for raw in (WINDS, STARS)]
    # the character still names only its own embedded lorebook: a file is a record beside it, as one file always was
    assert thaw(store.check_definition(MARA).envelope.payload)["card"]["lorebook"] == "card:mara-vell/lorebook/mara-vell-lore@r1"
    assert thaw(store.check_definition(SourceRef("card:mara-vell", "lorebook", "wind-lore")).envelope.payload)["name"] == "Wind lore"
    # the same files again write nothing; another order, or one fewer, is another import
    assert importer(store).plan(data, lorebook=TIDES, lorebooks=[WINDS, STARS]).report.status == "unchanged"
    assert importer(store).plan(data, lorebooks=[TIDES, WINDS, STARS]).report.status == "unchanged"
    assert importer(store).plan(data, lorebook=TIDES, lorebooks=[STARS, WINDS]).report.source == "card:mara-vell-2"
    assert importer(store).plan(data, lorebook=TIDES, lorebooks=[WINDS]).report.source == "card:mara-vell-2"


def test_one_lorebook_file_is_stored_exactly_as_before(store):
    land(store, encode(V3), lorebook=TIDES)
    kept = record(store)
    assert "lorebook_files" not in kept and set(kept["entities"]) == {"main", "lorebook", "lorebook_file"}
    assert importer(store).plan(encode(V3), lorebooks=[TIDES]).report.status == "unchanged"  # the same one file


def test_dropping_a_lorebook_file_on_an_update_keeps_the_record_it_wrote(store):
    land(store, encode(V3), lorebooks=[TIDES, WINDS])
    update = importer(store).plan(encode(V3), lorebooks=[TIDES], target="card:mara-vell")
    assert update.report.status == "update"
    assert [str(ref) for ref in update.report.kept] == ["card:mara-vell/lorebook/wind-lore@r1"]
    land(store, encode(V3), lorebooks=[TIDES], target="card:mara-vell")
    assert record(store)["kept_entities"] == {"lorebook_file-2": "lorebook/wind-lore"}
    back = importer(store).plan(encode(V3), lorebooks=[TIDES, WINDS], target="card:mara-vell")
    assert back.report.kept == () and "card:mara-vell/lorebook/wind-lore@r1" in [str(ref) for ref in back.report.records]


def test_two_lorebook_files_of_one_name_get_distinct_ids_and_a_scenario_binds_the_first(store):
    twin = book("Tide tables", "another table entirely")
    planned = importer(store).plan(encode(V3), lorebooks=[TIDES, twin])
    assert {"card:mara-vell/lorebook/tide-tables@r1", "card:mara-vell/lorebook/tide-tables-2@r1"} <= \
        {str(ref) for ref in planned.report.records}
    bare = {key: value for key, value in SCENARIO.items() if key != "lorebook"}
    land(store, encode(bare), lorebooks=[WINDS, STARS])
    scenario = thaw(store.check_definition(SourceRef("card:storm-over-port-vell", "scenario", "storm-over-port-vell")).envelope.payload)
    assert scenario["lorebook"] == "card:storm-over-port-vell/lorebook/wind-lore@r1"


@pytest.mark.parametrize("options, detail", [
    ({"lorebooks": [TIDES, TIDES]}, "lorebooks"),
    ({"lorebook": TIDES, "lorebooks": [WINDS, TIDES]}, "lorebooks"),
    ({"lorebooks": [book(f"Book {n}", "x y") for n in range(MAX_LOREBOOK_FILES + 1)]}, "lorebooks"),
    ({"lorebooks": TIDES}, "lorebooks"),
])
def test_lorebook_files_given_twice_or_too_many_are_refused(store, options, detail):
    before = snapshot(store.root)
    with pytest.raises(CardError) as caught:
        importer(store).plan(encode(V3), **options)
    assert (caught.value.reason, caught.value.detail) == ("invalid_options", detail)
    assert snapshot(store.root) == before


def test_a_bad_file_among_the_lorebooks_refuses_the_import_and_names_its_place(store):
    with pytest.raises(CardError) as caught:
        importer(store).plan(encode(V3), lorebooks=[TIDES, b"{not json"])
    assert caught.value.reason == "invalid_json" and "lorebook 2" in str(caught.value)
    with pytest.raises(CardError) as caught:
        importer(store).plan(encode(V3), lorebooks=[TIDES, encode(V3)])
    assert caught.value.reason == "unsupported_format"
    with pytest.raises(CardError) as caught:
        importer(store).plan(encode(LOREBOOK), lorebooks=[TIDES])
    assert (caught.value.reason, caught.value.detail) == ("invalid_options", "lorebook")
    messy = encode({"spec": "lorebook_v3", "data": {"name": "Messy", "scan_depth": "deep", "entries": []}})
    planned = importer(store).plan(encode(V3), lorebooks=[TIDES, messy])
    assert Loss("lorebook_file-2.data.scan_depth", "wrong_type") in planned.report.losses


# -- the same card, by its bytes and never by its name ---------------------------------------------

def test_the_same_card_again_updates_the_card_it_was_imported_as_only_when_asked(store):
    data = encode(V3)
    land(store, data)
    # as before: with nothing said, the same card with a portrait is a second source, and reported as the same bytes
    apart = importer(store).plan(data, portrait=PORTRAIT)
    assert apart.report.source == "card:mara-vell-2"
    assert ("same_bytes", ("card:mara-vell",)) in [(c.kind, c.refs) for c in apart.report.collisions]
    update = importer(store).plan(data, portrait=PORTRAIT, update_same_card=True)
    assert (update.report.status, update.report.source) == ("update", "card:mara-vell")
    assert not any(c.kind in ("same_bytes", "destination_taken") for c in update.report.collisions)
    found = outcome(update.report)
    assert (found.status, found.from_revision, found.to_revision) == ("update", None, None)  # the text did not move
    # a PNG card of the same document is the same card
    assert importer(store).plan(png_card(data), update_same_card=True).report.source == "card:mara-vell"


def test_an_update_that_moves_the_character_says_from_which_revision_to_which(store):
    data = encode(V3)
    land(store, data, embedded_lorebook=False)
    update = importer(store).plan(data, update_same_card=True)  # now with its lorebook: the character names it
    found = outcome(update.report)
    assert (found.status, found.source, found.from_revision, found.to_revision) == ("update", "card:mara-vell", "r1", "r2")
    assert [str(ref) for ref in update.report.replaces] == ["card:mara-vell/character/mara-vell@r1",
                                                            "card:mara-vell/card-import/import@r1"]


def test_a_different_card_of_the_same_name_is_never_taken_for_the_same_card(store):
    land(store, encode(V3))
    other = named(V3, "Mara Vell", description="Another person entirely who shares a name.")
    planned = importer(store).plan(other, update_same_card=True)
    assert (planned.report.status, planned.report.source) == ("new", "card:mara-vell-2")
    found = outcome(planned.report)
    assert found.same_name and (found.from_revision, found.to_revision) == (None, None)
    # a card with a name nothing else has is plainly new
    fresh = outcome(importer(store).plan(named(V3, "Ilse Dorn"), update_same_card=True).report)
    assert (fresh.status, fresh.same_name) == ("new", False)


def test_a_card_imported_another_way_or_in_two_places_is_not_matched(store):
    data = encode(V3)
    land(store, data)
    as_scenario = importer(store).plan(data, as_scenario=True, update_same_card=True)
    assert as_scenario.report.source == "card:mara-vell-2"  # imported as a character: a scenario is not that card
    land(store, data, target="card:second-copy")
    twice = importer(store).plan(data, portrait=PORTRAIT, update_same_card=True)
    assert twice.report.source == "card:mara-vell-2"  # two candidates: none is chosen for the person
    assert sorted(ref for c in twice.report.collisions if c.kind == "same_bytes" for ref in c.refs) == \
        ["card:mara-vell", "card:second-copy"]
    with pytest.raises(CardError) as caught:
        importer(store).plan(data, update_same_card="yes")  # type: ignore[arg-type]
    assert caught.value.reason == "invalid_options"


# -- a batch: one transaction ---------------------------------------------------------------------

def batch_of(*documents) -> list[BatchCard]:
    return [BatchCard(data, f"card-{index}.json") for index, data in enumerate(documents)]


def test_a_batch_stages_every_card_and_commits_them_as_one_write(store):
    cards = [BatchCard(png_card(named(V3, "Ilse Dorn"), shade=10), "ilse.png"),
             BatchCard(named(V3, "Tobin Marsh"), "tobin.json"), BatchCard(encode(LOREBOOK), "gazetteer.json")]
    before = snapshot(store.root)
    gate = Gate("sources", "assets")
    planned = plan_batch(CardImporter(store, gate.handle, owner=OWNER, clock=Clock()), cards)
    assert snapshot(store.root) == before  # planning writes nothing
    assert [card.report.source for card in planned.cards] == ["card:ilse-dorn", "card:tobin-marsh", "card:port-vell-gazetteer"]
    assert [card.report.status for card in planned.cards] == ["new", "new", "new"]
    preview = planned.preview()
    paths = [set(card.report.paths) for card in planned.cards]
    assert set().union(*paths) == set(preview) and sum(len(found) for found in paths) == len(preview)  # each file is one card's
    assert any(path.startswith("assets/blobs/") for path in paths[0]) and not any(path.startswith("assets/") for path in paths[1])
    for card in planned.cards:  # a card of a batch is committed only by the batch
        with pytest.raises(StoreError):
            card.commit()
    assert snapshot(store.root) == before
    planned.commit(gate.approve(preview))
    after = snapshot(store.root)
    assert sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path)) == sorted(preview)
    for path, data in preview.items():
        assert after[path] == data
    with pytest.raises(StoreError):
        planned.commit()  # once
    again = plan_batch(importer(store), cards)
    assert [card.report.status for card in again.cards] == ["unchanged"] * 3 and again.preview() == {}
    assert again.commit().etags == {}


def test_a_batch_missing_one_approval_writes_no_card_at_all(store):
    gate = Gate("sources", "assets")
    planned = plan_batch(CardImporter(store, gate.handle, owner=OWNER, clock=Clock()),
                         batch_of(named(V3, "Ilse Dorn"), named(V3, "Tobin Marsh")))
    preview = planned.preview()
    approvals = gate.approve(preview)
    approvals.pop(sorted(planned.cards[1].report.paths)[0])  # one file of the second card is not approved
    before = snapshot(store.root)
    with pytest.raises(WriteDenied):
        planned.commit(approvals)
    assert snapshot(store.root) == before  # not even the first card, whose every file was approved


def test_a_library_that_moved_under_one_card_refuses_the_whole_batch(store):
    gate = Gate("sources", "assets")
    worker = CardImporter(store, gate.handle, owner=OWNER, clock=Clock())
    planned = plan_batch(worker, batch_of(named(V3, "Ilse Dorn"), named(V3, "Tobin Marsh"), named(V3, "Wren Halloway")))
    approvals = gate.approve(planned.preview())
    land(store, named(V3, "Tobin Marsh", description="Imported beside the batch, first."))  # takes card:tobin-marsh
    before = snapshot(store.root)
    with pytest.raises(StaleWrite):
        planned.commit(approvals)
    assert snapshot(store.root) == before
    assert isinstance(store.check_source("card:ilse-dorn"), str) and isinstance(store.check_source("card:wren-halloway"), str)


def test_cards_of_one_name_in_a_batch_get_their_own_sources_and_the_same_card_twice_is_refused(store):
    first, second = named(V3, "Ilse Dorn"), named(V3, "Ilse Dorn", description="A second, different Ilse.")
    planned = plan_batch(importer(store), batch_of(first, second))
    assert [card.report.source for card in planned.cards] == ["card:ilse-dorn", "card:ilse-dorn-2"]
    assert outcome(planned.cards[1].report).same_name and not outcome(planned.cards[0].report).same_name
    with pytest.raises(BatchCardRefused) as caught:
        plan_batch(importer(store), batch_of(first, named(V3, "Tobin Marsh"), first))
    assert (caught.value.index, caught.value.reason) == (2, "duplicate_in_batch")
    assert "card 1 of the batch" in str(caught.value)
    with pytest.raises(BatchCardRefused) as caught:  # the same document as a PNG card and as JSON is one card too
        plan_batch(importer(store), [BatchCard(first, "ilse.json"), BatchCard(png_card(first), "ilse.png")])
    assert (caught.value.index, caught.value.reason) == (1, "duplicate_in_batch")
    # alone, as a list plans it: the second card is told which sources the first ones take
    assert plan_card(importer(store), BatchCard(second), {"card:ilse-dorn"}).report.source == "card:ilse-dorn-2"
    land(store, first)
    with pytest.raises(CardError) as caught:
        plan_card(importer(store), BatchCard(first), {"card:ilse-dorn"})  # an existing card another row already writes
    assert caught.value.reason == "duplicate_destination"


def test_one_bad_card_refuses_the_batch_by_its_place_and_nothing_is_staged(store):
    before = snapshot(store.root)
    with pytest.raises(BatchCardRefused) as caught:
        plan_batch(importer(store), batch_of(named(V3, "Ilse Dorn"), b"{not json", named(V3, "Tobin Marsh")))
    assert (caught.value.index, caught.value.reason) == (1, "invalid_json") and "card 2 of the batch" in str(caught.value)
    assert snapshot(store.root) == before
    for cards, reason in (([], "invalid_options"), (batch_of(*[named(V3, f"Card {n}") for n in range(MAX_BATCH_CARDS + 1)]),
                                                     "too_many_cards")):
        with pytest.raises(CardError) as caught:
            plan_batch(importer(store), cards)
        assert caught.value.reason == reason


def test_a_batch_updates_a_card_already_here_and_adds_the_new_ones_together(store):
    data = encode(V3)
    land(store, data)
    gate = Gate("sources", "assets")
    planned = plan_batch(CardImporter(store, gate.handle, owner=OWNER, clock=Clock()),
                         [BatchCard(png_card(data), "mara.png"), BatchCard(named(V3, "Ilse Dorn"), "ilse.json")])
    assert [(card.report.status, card.report.source) for card in planned.cards] == \
        [("update", "card:mara-vell"), ("new", "card:ilse-dorn")]
    planned.commit(gate.approve(planned.preview()))
    assert record(store)["container"]["format"] == "png" and record(store)["portrait"]["slot"] == "avatar"
    assert not isinstance(store.check_source("card:ilse-dorn"), str)


# -- a crash part way through a batch -------------------------------------------------------------

LIB = "lib"
BATCH = [BatchCard(png_card(named(V3, "Ilse Dorn"), shade=10), "ilse.png"),
         BatchCard(png_card(named(V3, "Tobin Marsh"), shade=200), "tobin.png"), BatchCard(encode(LOREBOOK), "gazetteer.json")]


@functools.cache
def _template() -> tuple[tuple[str, bytes | None], ...]:
    with tempfile.TemporaryDirectory() as folder:
        LibraryStore.create(Path(folder) / LIB, locker=ProcessLocker()).close()
        return tuple(snapshot(folder).items())


def _setup(ops, root):
    for key, data in _template():
        if data is None:
            ops.make_dirs(root / key)
        else:
            ops.write_bytes(root / key, data)


def _import(ops, root, locker):
    with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
        gate = Gate("sources", "assets")  # an untrusted worker, approved path by path
        planned = plan_batch(CardImporter(store, gate.handle, owner=OWNER, clock=Clock()), BATCH)
        planned.commit(gate.approve(planned.preview()))


def test_a_crash_anywhere_in_a_batch_leaves_every_card_or_none(tmp_path):
    """The whole batch is one journaled store transaction: explored at every crash point, the library is the
    one before the batch or the one with all three cards, never one with some of them."""
    locker = ProcessLocker()

    def recover(ops, root):
        LibraryStore.open(root / LIB, ops=ops, locker=locker).close()

    scenario = CrashScenario(
        name="a batch of three cards", setup=_setup, action=lambda ops, root: _import(ops, root, locker), recover=recover,
        invariants=(atomic_outcome(), idempotent_recovery(), no_residue("lib/.txn/*.new", "lib/.txn/journal.json"),
                    completes_after(Target("replace", target="lib/.txn/journal.json"))),
        normalize=drop_empty_dirs)
    report = explore_crashes(scenario, tmp_path / "work", **explore_mode("crashes"))
    report.assert_ok()
    assert report.points_explored > 30
