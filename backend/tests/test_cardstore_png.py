"""PNG cards and embedded lorebooks through the card importer (037) and the review API (110).

A PNG card is its own card document and its own portrait; the lorebook a card embeds is named before the
preview and may be left out. Everything still goes through the one plan, preview and commit. Every card and
picture is synthetic (``png_fixtures``).
"""

from __future__ import annotations

import base64
import hashlib
import json

import pytest

from card_fixtures import LOREBOOK, PORTRAIT, SCENARIO, V3, card, encode, importer, land, seed_world
from png_fixtures import as_v2, png, png_card, text_chunk
from review_fixtures import Desk, b64, secured
from test_reviewapi import apply_checked, files_of, refused

from grimoire.cards import CardError, Loss
from grimoire.cardstore import MAX_CARD_BYTES, PORTRAIT_SLOT, card_facts, unwrap
from grimoire.core.identity import PlacementRef, SourceRef
from grimoire.imagestore import ImageLibrary
from grimoire.store import LibraryStore
from grimoire.store.jsonfmt import thaw
from grimoire.testing.fileops import snapshot
from store_helpers import OWNER, owner_handle

MARA = SourceRef("card:mara-vell", "character", "mara-vell")
LORE = SourceRef("card:mara-vell", "lorebook", "mara-vell-lore")
IMPORT = SourceRef("card:mara-vell", "card-import", "import")


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed_world(opened)
        yield opened


@pytest.fixture
def desk(tmp_path):
    with Desk(secured(tmp_path / "lib", seed_world)) as opened:
        yield opened


def payload(store, ref) -> dict:
    return thaw(store.check_definition(ref).envelope.payload)


def record(store) -> dict:
    return thaw(store.check_definition(IMPORT).envelope.payload)


# -- the importer ---------------------------------------------------------------------------------

def test_a_png_card_imports_its_embedded_document_and_is_its_own_portrait(store):
    document = encode(V3)
    picture = png_card(document)
    before = snapshot(store.root)
    planned = importer(store).plan(picture, origin="mara.png")
    report = planned.report
    assert snapshot(store.root) == before  # planning writes nothing
    assert (report.status, report.source, report.kind, report.format) == ("new", "card:mara-vell", "character",
                                                                           "chara_card_v3")
    assert report.portrait is not None
    land(store, picture, origin="mara.png")
    kept = record(store)
    # the original kept is the card document itself, so the same card as JSON is the same card
    assert kept["original"]["sha256"] == hashlib.sha256(document).hexdigest()
    assert base64.b64decode(kept["original"]["base64"]) == document
    assert kept["container"] == {"format": "png", "keyword": "ccv3", "sha256": hashlib.sha256(picture).hexdigest(),
                                 "size": len(picture)}
    assert kept["portrait"]["sha256"] == hashlib.sha256(picture).hexdigest() and kept["portrait"]["slot"] == PORTRAIT_SLOT
    # the portrait is stored exactly as the optional portrait upload stores one: the whole file, in the avatar slot
    images = ImageLibrary(store, owner_handle(), owner=OWNER)
    image, _ = images.read_image(report.portrait)
    assert store.read_blob(image.blob.sha256, image.blob.ext) == picture
    placement, _ = images.read_placement(PlacementRef(MARA, PORTRAIT_SLOT))
    assert placement.image == report.portrait
    assert image.provenance["x-card-origin"] == "mara.png"


def test_a_png_card_and_the_same_upload_as_json_with_that_portrait_are_one_import(store):
    document = encode(V3)
    picture = png_card(document)
    land(store, picture, origin="mara.png")
    again = importer(store).plan(document, portrait=picture, origin="mara.png")
    assert again.report.status == "unchanged" and again.preview() == {}
    assert importer(store).plan(picture, origin="mara.png").report.status == "unchanged"


def test_a_given_portrait_stands_in_for_the_png_and_a_lorebook_in_a_png_has_no_portrait(store):
    land(store, png_card(encode(V3)), portrait=PORTRAIT)
    assert record(store)["portrait"]["sha256"] == hashlib.sha256(PORTRAIT).hexdigest()
    lore = importer(store).plan(png_card(encode(LOREBOOK)))
    assert lore.report.kind == "lorebook" and lore.report.portrait is None


def test_an_older_v2_card_in_a_chara_chunk_becomes_a_character_with_its_lorebook(store):
    planned = importer(store).plan(png_card(as_v2(V3), keyword="chara"), origin="old.png")
    assert (planned.report.format, planned.report.kind) == ("chara_card_v2", "character")
    land(store, png_card(as_v2(V3), keyword="chara"), origin="old.png")
    stored = payload(store, MARA)
    assert stored["card"]["format"] == "chara_card_v2" and stored["card"]["spec_version"] == "2.0"
    assert stored["card"]["lorebook"] == str(LORE) + "@r1"
    assert record(store)["container"]["keyword"] == "chara"


def test_a_v2_card_as_a_json_file_imports_too(store):
    planned = importer(store).plan(encode(as_v2(V3)))
    assert planned.report.format == "chara_card_v2" and planned.report.status == "new"


@pytest.mark.parametrize("data, reason, detail", [
    (png(), "no_embedded_card", "png"),
    (png_card(encode(V3))[:-5], "invalid_png", "truncated"),
    (png_card(encode(V3))[:50] + b"\x00" + png_card(encode(V3))[51:], "invalid_png", "crc"),
    (png(text_chunk("ccv3", b"@@@")), "invalid_png", "base64"),
    (png(text_chunk("ccv3", base64.b64encode(b"{not json"))), "invalid_json", None),
    (png(text_chunk("ccv3", base64.b64encode(b"[1]"))), "invalid_card", ""),
    (png(text_chunk("chara", base64.b64encode(encode({"name": "x", "first_mes": "hello"})))), "unsupported_format",
     "chara_card_v1"),
    (png(text_chunk("ccv3", base64.b64encode(b" " * (MAX_CARD_BYTES + 1)))), "too_large", "ccv3"),
])
def test_an_unreadable_png_card_is_refused_before_anything_is_staged(store, data, reason, detail):
    before = snapshot(store.root)
    with pytest.raises(CardError) as caught:
        importer(store).plan(data)
    assert (caught.value.reason, caught.value.detail) == (reason, detail)
    assert snapshot(store.root) == before


def test_a_picture_is_no_separate_lorebook(store):
    with pytest.raises(CardError) as caught:
        importer(store).plan(encode(V3), lorebook=png_card(encode(LOREBOOK)))
    assert (caught.value.reason, caught.value.detail) == ("unsupported_format", "png")


def test_the_embedded_lorebook_can_be_left_out_and_the_stored_card_names_none(store):
    data = encode(card(character_book={**V3["data"]["character_book"], "scan_depth": "deep"}))
    with_book = importer(store).plan(data)
    assert Loss("data.character_book.scan_depth", "wrong_type") in with_book.report.losses
    planned = importer(store).plan(data, embedded_lorebook=False)
    report = planned.report
    assert [str(ref) for ref in report.records] == [str(MARA) + "@r1"]
    assert not any("character_book" in piece.field for piece in report.pieces)
    assert not any("character_book" in loss.path for loss in report.losses)  # not a loss: it was left out
    land(store, data, embedded_lorebook=False)
    assert "lorebook" not in payload(store, MARA)["card"]
    assert isinstance(store.check_definition(LORE), str)  # no lorebook record was written
    kept = record(store)
    assert kept["options"] == {"as_scenario": False, "embedded_lorebook": False}
    assert set(kept["entities"]) == {"main"}
    # the card's own bytes, lorebook included, are still the preserved original
    assert json.loads(base64.b64decode(kept["original"]["base64"]))["data"]["character_book"]["name"] == "Harbor lore"
    assert importer(store).plan(data, embedded_lorebook=False).report.status == "unchanged"


def test_leaving_the_lorebook_out_is_its_own_import_and_an_import_with_it_keeps_its_stored_shape(store):
    data = encode(V3)
    land(store, data)
    kept = record(store)
    assert kept["options"] == {"as_scenario": False} and "container" not in kept  # as every earlier import
    assert payload(store, MARA)["card"]["lorebook"] == str(LORE) + "@r1"
    assert importer(store).plan(data).report.status == "unchanged"
    # the same card without its lorebook is a different import: it does not land on the first one unasked
    apart = importer(store).plan(data, embedded_lorebook=False)
    assert apart.report.source == "card:mara-vell-2"
    # asked to update the first, the character stops naming the lorebook and the lorebook record is kept
    update = importer(store).plan(data, embedded_lorebook=False, target="card:mara-vell")
    assert update.report.status == "update" and [str(ref) for ref in update.report.kept] == [str(LORE) + "@r1"]
    land(store, data, embedded_lorebook=False, target="card:mara-vell")
    assert "lorebook" not in payload(store, SourceRef("card:mara-vell", "character", "mara-vell", "r2"))["card"]
    assert not isinstance(store.check_definition(LORE), str)


def test_a_scenario_can_leave_its_lorebook_out_and_a_lorebook_card_cannot(store):
    planned = importer(store).plan(encode(SCENARIO), embedded_lorebook=False)
    assert [ref.kind for ref in planned.report.records] == ["scenario"]
    with pytest.raises(CardError) as caught:
        importer(store).plan(encode(LOREBOOK), embedded_lorebook=False)
    assert (caught.value.reason, caught.value.detail) == ("invalid_options", "embedded_lorebook")
    with pytest.raises(CardError) as caught:
        importer(store).plan(encode(V3), embedded_lorebook="no")  # type: ignore[arg-type]
    assert caught.value.reason == "invalid_options"
    # a card with no embedded lorebook imports the same either way
    bare = encode(card(character_book=None))
    assert importer(store).plan(bare, embedded_lorebook=False).preview() == importer(store).plan(bare).preview()


# -- what a file says of itself -------------------------------------------------------------------

def test_card_facts_name_the_card_its_container_and_its_embedded_lorebook():
    document = encode(V3)
    facts = card_facts(png_card(document))
    assert (facts.container, facts.keyword, facts.format, facts.kind, facts.name) == \
        ("png", "ccv3", "chara_card_v3", "character", "Mara Vell")
    assert facts.portrait and (facts.lorebook.name, facts.lorebook.entries) == ("Harbor lore", 2)
    assert (facts.sha256, facts.size) == (hashlib.sha256(document).hexdigest(), len(document))
    plain = card_facts(document)
    assert (plain.container, plain.keyword, plain.portrait) == ("json", None, False) and plain.sha256 == facts.sha256
    unnamed = card_facts(encode(card(character_book={"entries": []})))
    assert (unnamed.lorebook.name, unnamed.lorebook.entries) == ("Mara Vell lore", 0)
    assert card_facts(encode(card(character_book=None))).lorebook is None
    assert card_facts(document, as_scenario=True).kind == "scenario"
    book = card_facts(encode(LOREBOOK))
    assert (book.kind, book.name, book.lorebook.entries, book.portrait) == ("lorebook", "Port Vell gazetteer", 1, False)
    assert card_facts(png_card(as_v2(V3), keyword="chara")).format == "chara_card_v2"
    assert unwrap(document).png is None and unwrap(png_card(document)).card == document
    with pytest.raises(CardError) as caught:
        card_facts(png())
    assert caught.value.reason == "no_embedded_card"


# -- the review API -------------------------------------------------------------------------------

def test_a_png_card_is_inspected_previewed_and_approved_as_exactly_the_files_shown(desk):
    picture = png_card(encode(V3))
    before = files_of(desk.folder)
    found = desk("card.inspect", card=b64(picture))
    assert (found["container"], found["name"], found["portrait"]) == ("png", "Mara Vell", True)
    assert found["lorebook"] == {"name": "Harbor lore", "entries": 2}
    assert files_of(desk.folder) == before and len(desk.api.proposals) == 0  # inspecting proposes nothing
    proposal = desk("card.preview", card=b64(picture), origin="mara.png")
    assert files_of(desk.folder) == before
    blobs = [row for row in proposal["files"] if row["path"].startswith("assets/blobs/")]
    assert [row["sha256"] for row in blobs] == [hashlib.sha256(picture).hexdigest()] and blobs[0]["binary"]
    assert proposal["report"]["portrait"] and proposal["report"]["status"] == "new"
    applied = apply_checked(desk, proposal)
    assert applied["state"] == "applied"
    assert set(files_of(desk.folder)) - set(before) >= {row["path"] for row in proposal["files"]}
    assert desk("card.preview", card=b64(picture), origin="mara.png")["writes"] == 0  # a rerun writes nothing


def test_the_review_api_leaves_the_embedded_lorebook_out_when_asked(desk):
    proposal = desk("card.preview", card=b64(encode(V3)), embedded_lorebook=False)
    assert not any("/lorebook/" in row["path"] for row in proposal["files"])
    assert any("/lorebook/" in row["path"] for row in desk("card.preview", card=b64(encode(V3)))["files"])
    assert refused(lambda: desk("card.preview", card=b64(encode(V3)), embedded_lorebook="no")).reason == "invalid_field"


@pytest.mark.parametrize("data, reason, detail", [
    (png(), "no_embedded_card", "png"),
    (png_card(encode(V3))[:-1], "invalid_png", "truncated"),
    (png_card(encode(V3)) + b"extra", "invalid_png", "trailing_data"),
    (png(text_chunk("ccv3", b"not base64")), "invalid_png", "base64"),
    (png(text_chunk("ccv3", base64.b64encode(b"{"))), "invalid_json", None),
    (b"PK\x03\x04rest", "unsupported_format", "charx"),
])
def test_an_unreadable_card_file_is_refused_the_same_by_inspect_and_preview(desk, data, reason, detail):
    before = files_of(desk.folder)
    for op in ("card.inspect", "card.preview"):
        error = refused(lambda: desk(op, card=b64(data)))
        assert (error.reason, error.status, error.details["detail"]) == (reason, 400, detail), op
    assert files_of(desk.folder) == before and len(desk.api.proposals) == 0


def test_upload_limits_hold_for_json_and_png_cards(desk):
    # a JSON card keeps its 2 MiB limit; only a PNG may be larger, up to one picture's cap
    assert refused(lambda: desk("card.inspect", card=b64(b" " * (MAX_CARD_BYTES + 1)))).reason == "too_large"
    assert refused(lambda: desk("card.preview", card=b64(b" " * (MAX_CARD_BYTES + 1)))).reason == "too_large"
    limits = desk("review.capabilities")["imports"]["card"]
    assert refused(lambda: desk("card.inspect", card="A" * ((limits["png_max_bytes"] + 2) // 3 * 4 + 8))).reason == "too_large"
    assert refused(lambda: desk("card.inspect", card="not base64 at all")).reason == "invalid_field"
    assert refused(lambda: desk("card.inspect")).reason == "invalid_field"
