"""Sprint 038 proof: a card's art and its expression variants become source placements that keep
their role, provenance and override state, and a missing picture stays an explicit unresolved slot.

Every card and picture is synthetic (``card_fixtures``): the "pictures" are short byte strings with
a valid magic header, and nothing is fetched from anywhere.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace

import pytest

from grimoire.cards import Loss, parse_card
from grimoire.cards.art import card_art
from grimoire.cardstore import PORTRAIT_SLOT
from grimoire.cardstore.art import stage_art
from grimoire.core.identity import ImageRef, InstanceRef, PlacementRef, SourceRef
from grimoire.images import Crop, Expression, Hidden, Placement, Shown, Unavailable
from grimoire.imagestore import ImageLibrary
from grimoire.store import LibraryStore, RecordEnvelope
from grimoire.testing.fileops import snapshot

from card_fixtures import PORTRAIT, PORTRAIT_2, V3, card, encode, importer, land, seed_world
from store_helpers import owner_handle

MARA = SourceRef("card:mara-vell", "character", "mara-vell")
SMILE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR synthetic smile"
FROWN_GIF = b"GIF89a" + b"synthetic frown"
DOCKS_JPEG = b"\xff\xd8\xff\xe0" + b"synthetic docks"
SMILE, GRIN, FROWN = "expr.default.smile", "expr.default.grin", "expr.default.frown"
ANGRY, SHY = "expr.default.angry", "expr.default.shy"
FIRST, SECOND = InstanceRef("c1", "character", "mara-first"), InstanceRef("c1", "character", "mara-second")


def data_uri(data: bytes, media: str = "image/png") -> str:
    return f"data:{media};base64,{base64.b64encode(data).decode('ascii')}"


ASSETS = [
    {"type": "icon", "uri": "ccdefault:", "name": "main", "ext": "png"},
    {"type": "emotion", "uri": data_uri(SMILE_PNG), "name": "Smile", "ext": "png"},
    {"type": "emotion", "uri": data_uri(SMILE_PNG), "name": "grin", "ext": "png"},  # the same bytes again
    {"type": "emotion", "uri": data_uri(FROWN_GIF, "image/gif"), "name": "frown", "ext": "gif"},
    {"type": "emotion", "uri": "embeded://assets/emotion/angry.png", "name": "angry", "ext": "png"},
    {"type": "emotion", "uri": "https://example.invalid/shy.png", "name": "shy", "ext": "png"},
    {"type": "background", "uri": data_uri(DOCKS_JPEG, "image/jpeg"), "name": "docks", "ext": "jpg"},
    {"type": "user_icon", "uri": data_uri(SMILE_PNG), "name": "me", "ext": "png"},
]


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed_world(opened)
        yield opened


def _images(store: LibraryStore) -> ImageLibrary:
    return ImageLibrary(store, owner_handle("sources", "campaigns", "assets"))


def _individuals(store: LibraryStore) -> None:
    """Two campaign individuals of the imported character (each derived from it, unpinned)."""
    txn = store.transaction()
    for ref in (FIRST, SECOND):
        txn.put(RecordEnvelope(ref, {}, derived_from=str(MARA), base=str(MARA.at("r1"))), None)
    txn.commit(owner_handle())


def _blob_count(store: LibraryStore) -> int:
    folder = store.root / "assets" / "blobs"
    return sum(1 for path in folder.rglob("*") if path.is_file()) if folder.exists() else 0


# -- the proof ---------------------------------------------------------------------------------------

def test_card_art_becomes_qualified_placements_and_missing_pictures_stay_unresolved(store):
    planned = land(store, encode(card(assets=ASSETS)), portrait=PORTRAIT)
    report = planned.report
    statuses = {entry.slot: (entry.status, entry.unresolved, entry.asset) for entry in report.art}
    assert statuses == {
        PORTRAIT_SLOT: ("placed", None, None), SMILE: ("placed", None, 1), GRIN: ("placed", None, 2),
        FROWN: ("placed", None, 3), ANGRY: ("placed", "not_embedded", 4), SHY: ("placed", "not_fetched", 5),
        "background": ("placed", None, 6),
    }
    # only what is missing, or has no place, is a loss; the main icon is the portrait itself
    assert {loss for loss in report.losses if loss.path.startswith("data.assets")} == {
        Loss("data.assets[4]", "unavailable"), Loss("data.assets[5]", "not_fetched"),
        Loss("data.assets[7]", "not_applicable")}

    images = _images(store)
    shown = images.effective_placements(MARA)
    avatar, smile, grin = shown.slot(PORTRAIT_SLOT), shown.slot(SMILE), shown.slot(GRIN)
    assert images.read_image_bytes(avatar.image) == PORTRAIT
    assert (smile.role, smile.layer) == ("expression", "source") and images.read_image_bytes(smile.image) == SMILE_PNG
    assert smile.provenance == {"origin": "character card asset",
                                "x-card-asset": {"index": 1, "type": "emotion", "name": "Smile",
                                                 "sha256": hashlib.sha256(SMILE_PNG).hexdigest()}}
    assert grin.image == smile.image  # identical bytes: one image object, one blob
    assert shown.slot("background").role == "gallery"
    assert images.read_image_bytes(shown.slot(FROWN).image) == FROWN_GIF
    assert _blob_count(store) == 4  # portrait, smile, frown, docks: the user's own icon is never placed
    assert shown.slot(ANGRY) == Unavailable(ANGRY, "unresolved",
                                            "a picture packaged with the card file, which this import does not read")
    assert shown.slot(SHY) == Unavailable(SHY, "unresolved", "a remote picture; nothing is fetched")
    placement, _ = images.read_placement(PlacementRef(MARA, SHY))
    assert (placement.mode, placement.expression) == ("unresolved", Expression("default", "shy"))
    assert not any(slot.slot.startswith("icon") or slot.slot.endswith("me") for slot in shown.slots)


def test_an_individuals_own_sprite_alters_neither_the_card_nor_another_individual(store):
    land(store, encode(card(assets=ASSETS)), portrait=PORTRAIT)
    _individuals(store)
    images = _images(store)
    smile = images.effective_placements(MARA).slot(SMILE).image
    batch = images.change()
    own = batch.add_image("campaign:c1", "first-smile", PORTRAIT_2)
    batch.place(FIRST, SMILE, Placement("set", "expression", own, expression=Expression("default", "smile")), None)
    batch.place(FIRST, "background", Placement("hide"), None)
    batch.place(SECOND, FROWN, Placement("adjust", image=images.effective_placements(MARA).slot(FROWN).image,
                                         crop=Crop(0, 0, 5000, 5000)), None)
    batch.commit()

    first, second = images.effective_placements(FIRST), images.effective_placements(SECOND)
    assert (first.slot(SMILE).image, first.slot(SMILE).layer) == (own, "campaign")
    assert first.slot("background") == Hidden("background")
    assert (second.slot(SMILE).image, second.slot(SMILE).layer) == (smile, "source")
    assert second.slot(FROWN).layer == "campaign_adjusted" and first.slot(FROWN).crop is None
    assert images.effective_placements(MARA).slot(SMILE).image == smile  # the card itself is untouched


def test_an_updated_card_moves_inherited_art_and_never_an_individuals_own(store):
    land(store, encode(card(assets=ASSETS)), portrait=PORTRAIT)
    _individuals(store)
    images = _images(store)
    batch = images.change()
    own = batch.add_image("campaign:c1", "first-smile", PORTRAIT_2)
    batch.place(FIRST, SMILE, Placement("set", "expression", own, expression=Expression("default", "smile")), None)
    batch.commit()

    changed = [dict(asset) for asset in ASSETS]
    changed[1]["uri"] = data_uri(FROWN_GIF, "image/gif")  # the smile is redrawn
    changed[5]["uri"] = data_uri(DOCKS_JPEG, "image/jpeg")  # the missing sprite arrives inline
    update = land(store, encode(card(assets=changed)), portrait=PORTRAIT, target="card:mara-vell")
    assert {entry.slot for entry in update.report.art if entry.status == "placed"} == {SMILE, SHY}
    assert "hand_edited" not in {collision.kind for collision in update.report.collisions}

    new_smile = images.effective_placements(MARA).slot(SMILE).image
    assert images.read_image_bytes(new_smile) == FROWN_GIF
    assert images.effective_placements(SECOND).slot(SMILE).image == new_smile  # inherited: follows the card
    assert images.effective_placements(FIRST).slot(SMILE).image == own  # its own picture stands
    assert isinstance(images.effective_placements(SECOND).slot(SHY), Shown)


def test_a_hand_chosen_sprite_stands_until_edits_are_replaced(store):
    land(store, encode(card(assets=ASSETS)), portrait=PORTRAIT)
    images = _images(store)
    _, etag = images.read_placement(PlacementRef(MARA, SMILE))
    batch = images.change()
    chosen = batch.add_image("card:mara-vell", "chosen-smile", PORTRAIT_2)
    batch.place(MARA, SMILE, Placement("set", "expression", chosen, expression=Expression("default", "smile")), etag)
    batch.commit()

    again = importer(store).plan(encode(card(assets=ASSETS)), portrait=PORTRAIT)
    assert again.report.status == "unchanged" and again.preview() == {}
    assert ("hand_edited", (str(PlacementRef(MARA, SMILE)),)) in [(c.kind, c.refs) for c in again.report.collisions]
    assert {entry.slot: entry.status for entry in again.report.art}[SMILE] == "hand_edited"
    land(store, encode(card(assets=ASSETS, personality="Later.")), portrait=PORTRAIT, target="card:mara-vell")
    assert images.effective_placements(MARA).slot(SMILE).image == chosen
    land(store, encode(card(assets=ASSETS, personality="Later.")), portrait=PORTRAIT, target="card:mara-vell",
         replace_edits=True)
    assert images.read_image_bytes(images.effective_placements(MARA).slot(SMILE).image) == SMILE_PNG


def test_the_cards_own_picture_is_unresolved_until_it_is_given(store):
    first = land(store, encode(V3))  # the main icon is ``ccdefault:``, and no portrait came with the card
    images = _images(store)
    assert images.effective_placements(MARA).slot(PORTRAIT_SLOT) == Unavailable(
        PORTRAIT_SLOT, "unresolved", "the card's own picture, which was not given with the card")
    assert Loss("data.assets[0]", "unavailable") in first.report.losses and first.report.portrait is None
    later = land(store, encode(V3), portrait=PORTRAIT, target="card:mara-vell")
    assert "hand_edited" not in {collision.kind for collision in later.report.collisions}
    assert images.read_image_bytes(images.effective_placements(MARA).slot(PORTRAIT_SLOT).image) == PORTRAIT


def test_a_main_icon_inline_is_the_avatar_without_a_portrait_and_a_gallery_picture_with_one(store):
    icons = [{"type": "icon", "uri": data_uri(SMILE_PNG), "name": "main"},
             {"type": "icon", "uri": data_uri(FROWN_GIF, "image/gif"), "name": "Side View"}]
    land(store, encode(card(assets=icons)))
    images = _images(store)
    shown = images.effective_placements(MARA)
    assert images.read_image_bytes(shown.slot(PORTRAIT_SLOT).image) == SMILE_PNG
    assert images.read_image_bytes(shown.slot("icon-side-view").image) == FROWN_GIF
    land(store, encode(card(assets=icons)), portrait=PORTRAIT, target="card:mara-vell")
    shown = images.effective_placements(MARA)
    assert images.read_image_bytes(shown.slot(PORTRAIT_SLOT).image) == PORTRAIT  # the given picture wins avatar
    assert images.read_image_bytes(shown.slot("icon-main").image) == SMILE_PNG


@pytest.mark.parametrize("uri, reason", [
    ("data:image/png;base64,***", "invalid_data"),
    (data_uri(b"plain text, not a picture", "image/png"), "unsupported_media"),
    ("data:image/png,%89PNG", "invalid_data"),  # only base64 content is read
    ("file:///home/someone/smile.png", "unsupported_uri"),  # never read
])
def test_an_unreadable_inline_picture_is_an_unresolved_slot(store, uri, reason):
    planned = land(store, encode(card(assets=[{"type": "emotion", "uri": uri, "name": "smile"}])))
    assert [(entry.slot, entry.unresolved) for entry in planned.report.art] == [(SMILE, reason)]
    assert Loss("data.assets[0]", "unavailable") in planned.report.losses
    assert _images(store).effective_placements(MARA).slot(SMILE).reason == "unresolved"
    assert _blob_count(store) == 0


def test_slots_are_claimed_in_card_order_and_bounded(store):
    many = [{"type": "emotion", "uri": data_uri(SMILE_PNG), "name": "Smile"},
            {"type": "emotion", "uri": data_uri(FROWN_GIF, "image/gif"), "name": "smile!"},  # the same slot
            {"type": "emotion", "uri": data_uri(SMILE_PNG), "name": "   "}]  # no usable name: its place
    many += [{"type": "x-prop", "uri": data_uri(SMILE_PNG), "name": f"prop {n}"} for n in range(70)]
    planned = importer(store).plan(encode(card(assets=many)))
    slots = [entry.slot for entry in planned.report.art]
    assert slots[:3] == [SMILE, "expr.default.emotion-2", "asset-prop-0"] and len(slots) == 64
    assert Loss("data.assets[1]", "duplicate_slot") in planned.report.losses
    assert Loss("data.assets[72]", "too_many") in planned.report.losses


def test_a_rerun_writes_nothing_and_planning_never_writes(store):
    before = snapshot(store.root)
    importer(store).plan(encode(card(assets=ASSETS)), portrait=PORTRAIT)
    assert snapshot(store.root) == before
    land(store, encode(card(assets=ASSETS)), portrait=PORTRAIT)
    again = importer(store).plan(encode(card(assets=ASSETS)), portrait=PORTRAIT)
    assert again.report.status == "unchanged" and again.preview() == {}
    assert {entry.status for entry in again.report.art} == {"unchanged"}


def test_a_scenario_card_places_its_art_on_the_scenario(store):
    planned = land(store, encode(card(assets=ASSETS[1:2])), as_scenario=True)
    scenario = planned.report.main
    assert scenario.kind == "scenario"
    assert isinstance(_images(store).effective_placements(scenario).slot(SMILE), Shown)


def test_the_portrait_record_key_keeps_its_meaning_for_an_older_reader(store):
    land(store, encode(card(assets=ASSETS)), portrait=PORTRAIT)
    record = store.check_definition(SourceRef("card:mara-vell", "card-import", "import")).envelope.payload
    portrait = _images(store).effective_placements(MARA).slot(PORTRAIT_SLOT).image
    assert record["avatars"] == {str(MARA): str(portrait)}  # Sprint 037's key, unchanged
    assert record["art"][str(MARA)][SHY] == "unresolved" and record["art"][str(MARA)][SMILE].startswith(
        "card:mara-vell#image/art-")
    assert record["portrait"]["image"] == str(portrait)
    assert ImageRef.parse(record["art"][str(MARA)][GRIN]) == _images(store).effective_placements(MARA).slot(GRIN).image


# -- a later import never loses what an earlier one placed (038 review) --------------------------------

def test_a_card_read_again_without_its_pictures_keeps_the_ones_it_placed(store):
    land(store, encode(card(assets=ASSETS)), portrait=PORTRAIT)  # say, read from the card's PNG
    remote = [dict(asset) for asset in ASSETS]
    remote[1]["uri"] = "https://example.invalid/smile.png"  # the smile is remote in this copy
    later = land(store, encode(card(assets=remote)), target="card:mara-vell")  # its JSON alone
    statuses = {entry.slot: entry.status for entry in later.report.art}
    assert statuses[PORTRAIT_SLOT] == statuses[SMILE] == "kept"
    assert "hand_edited" not in {collision.kind for collision in later.report.collisions}
    images = _images(store)
    shown = images.effective_placements(MARA)
    assert images.read_image_bytes(shown.slot(PORTRAIT_SLOT).image) == PORTRAIT
    assert images.read_image_bytes(shown.slot(SMILE).image) == SMILE_PNG
    again = importer(store).plan(encode(card(assets=remote)), target="card:mara-vell")
    assert again.report.status == "unchanged" and again.preview() == {}


def test_an_update_says_where_each_picture_now_comes_from(store):
    land(store, encode(card(assets=[ASSETS[1], ASSETS[5]])))  # smile inline, shy remote
    update = land(store, encode(card(assets=[ASSETS[4] | {"name": "shy"}, ASSETS[1]])), target="card:mara-vell")
    assert {entry.slot: entry.status for entry in update.report.art} == {SHY: "placed", SMILE: "placed"}
    images = _images(store)
    shy, _ = images.read_placement(PlacementRef(MARA, SHY))
    smile, _ = images.read_placement(PlacementRef(MARA, SMILE))
    assert shy.provenance["note"] == "a picture packaged with the card file, which this import does not read"
    assert (shy.provenance["x-card-asset"]["index"], smile.provenance["x-card-asset"]["index"]) == (0, 1)
    assert images.effective_placements(MARA).slot(SHY).text == shy.provenance["note"]


def test_the_same_bytes_stay_one_picture_with_or_without_the_portrait(store):
    same = [{"type": "emotion", "uri": data_uri(PORTRAIT), "name": "smile"}]
    land(store, encode(card(assets=same)), portrait=PORTRAIT)
    before = _blob_count(store), _images(store).effective_placements(MARA).slot(SMILE).image
    for portrait in (None, PORTRAIT):
        planned = land(store, encode(card(assets=same)), portrait=portrait, target="card:mara-vell")
        assert {entry.slot: entry.status for entry in planned.report.art}[SMILE] == "unchanged"
    assert (_blob_count(store), _images(store).effective_placements(MARA).slot(SMILE).image) == before
    assert before[1].image_id.startswith("portrait-")


def test_an_icon_named_like_the_main_one_never_takes_its_slot(store):
    icons = [{"type": "icon", "uri": data_uri(FROWN_GIF, "image/gif"), "name": "Main!"},
             {"type": "icon", "uri": data_uri(SMILE_PNG), "name": "main"}]
    planned = land(store, encode(card(assets=icons)), portrait=PORTRAIT)
    assert {entry.slot: entry.asset for entry in planned.report.art} == {PORTRAIT_SLOT: None, "icon-0": 0,
                                                                         "icon-main": 1}


def test_art_an_older_build_left_unrecorded_is_still_the_cards_and_a_crop_is_still_a_persons(store):
    land(store, encode(card(assets=ASSETS)), portrait=PORTRAIT)
    images = _images(store)
    _, etag = images.read_placement(PlacementRef(MARA, SMILE))
    chosen, _ = images.read_placement(PlacementRef(MARA, FROWN))
    batch = images.change()
    batch.place(MARA, FROWN, replace(chosen, crop=Crop(0, 0, 5000, 5000)), images.read_placement(
        PlacementRef(MARA, FROWN))[1])
    batch.commit()
    redrawn = [dict(asset) for asset in ASSETS]
    redrawn[1]["uri"] = data_uri(DOCKS_JPEG, "image/jpeg")
    redrawn[3]["uri"] = data_uri(SMILE_PNG)
    pieces, _ = card_art(parse_card(json.loads(encode(card(assets=redrawn)))), PORTRAIT)
    # an import record an older build rewrote has no ``art``: the placements still say whose they are
    stage = stage_art(images, "card:mara-vell", MARA, pieces, {}, False, {}, str)
    statuses = {entry.slot: entry.status for entry in stage.slots}
    assert statuses[SMILE] == "placed" and statuses[FROWN] == "hand_edited"
    assert etag in {expected for slot, _, expected in stage.places if slot == SMILE}


def test_bytes_that_later_become_the_portrait_keep_their_picture_and_an_individuals_crop(store):
    same = [{"type": "emotion", "uri": data_uri(PORTRAIT), "name": "smile"}]
    land(store, encode(card(assets=same)))  # no portrait yet: the sprite's bytes get an object
    _individuals(store)
    images = _images(store)
    sprite = images.effective_placements(MARA).slot(SMILE).image
    images.place(FIRST, SMILE, Placement("adjust", image=sprite, crop=Crop(0, 0, 5000, 5000)), None).commit()
    planned = land(store, encode(card(assets=same)), portrait=PORTRAIT, target="card:mara-vell")
    assert {entry.slot: entry.status for entry in planned.report.art}[SMILE] == "unchanged"
    assert planned.report.portrait == sprite  # one object for the bytes, in either order
    assert images.effective_placements(MARA).slot(PORTRAIT_SLOT).image == sprite
    assert images.effective_placements(FIRST).slot(SMILE).layer == "campaign_adjusted"


def test_without_a_record_another_imported_picture_in_a_slot_is_a_persons_choice(store):
    land(store, encode(card(assets=ASSETS)), portrait=PORTRAIT)
    images = _images(store)
    smile, etag = images.read_placement(PlacementRef(MARA, SMILE))
    frown = images.effective_placements(MARA).slot(FROWN).image
    images.place(MARA, SMILE, replace(smile, image=frown), etag).commit()  # its provenance kept as it was
    pieces, _ = card_art(parse_card(json.loads(encode(card(assets=ASSETS)))), PORTRAIT)
    stage = stage_art(images, "card:mara-vell", MARA, pieces, {}, False, {}, str)
    assert {entry.slot: entry.status for entry in stage.slots}[SMILE] == "hand_edited"


# -- names with no ASCII letter or digit (Wave 5 audit, 038 B3) ------------------------------------

SMILE_JA, ANGER_JA = "笑顔", "怒り"  # "smile" and "anger"
#: the first 8 hex digits of the sha256 of each (NFC) name: the slot follows the name, not its place
SMILE_JA_SLOT, ANGER_JA_SLOT = "expr.default.emotion-b80c40ec", "expr.default.emotion-9370c62b"
JA_SMILE = {"type": "emotion", "uri": data_uri(SMILE_PNG), "name": SMILE_JA, "ext": "png"}
JA_ANGER = {"type": "emotion", "uri": data_uri(FROWN_GIF, "image/gif"), "name": ANGER_JA, "ext": "gif"}


def test_a_sprite_named_without_ascii_keeps_its_slot_when_the_card_reorders_its_assets(store):
    land(store, encode(card(assets=[JA_SMILE, JA_ANGER])), portrait=PORTRAIT)
    _individuals(store)
    images = _images(store)
    own = images.change()
    picture = own.add_image("campaign:c1", "first-smile", PORTRAIT_2)
    own.place(FIRST, SMILE_JA_SLOT, Placement("set", "expression", picture,
                                              expression=Expression("default", "emotion-b80c40ec")), None)
    own.commit()

    update = land(store, encode(card(assets=[JA_ANGER, JA_SMILE])), portrait=PORTRAIT, target="card:mara-vell")
    assert {entry.slot: entry.asset for entry in update.report.art if entry.slot.startswith("expr.")} == {
        ANGER_JA_SLOT: 0, SMILE_JA_SLOT: 1}  # each sprite's place changed; its slot did not
    shown = images.effective_placements(MARA)
    assert images.read_image_bytes(shown.slot(SMILE_JA_SLOT).image) == SMILE_PNG
    assert images.read_image_bytes(shown.slot(ANGER_JA_SLOT).image) == FROWN_GIF
    assert shown.slot(SMILE_JA_SLOT).provenance["x-card-asset"]["name"] == SMILE_JA
    assert images.effective_placements(FIRST).slot(SMILE_JA_SLOT).image == picture  # still over the smile
    assert images.read_image_bytes(images.effective_placements(SECOND).slot(ANGER_JA_SLOT).image) == FROWN_GIF


def test_the_slot_of_a_name_without_ascii_ignores_its_unicode_spelling_and_an_unnamed_asset_keeps_its_place():
    import unicodedata
    composed = "웃음"  # Hangul, which NFD spells as separate letters
    assets = [{"type": "emotion", "uri": data_uri(SMILE_PNG), "name": composed},
              {"type": "icon", "uri": data_uri(SMILE_PNG), "name": "main"},
              {"type": "icon", "uri": data_uri(SMILE_PNG), "name": SMILE_JA},
              {"type": "background", "uri": data_uri(SMILE_PNG), "name": "main"},
              {"type": "background", "uri": data_uri(SMILE_PNG), "name": ANGER_JA},
              {"type": "x-prop", "uri": data_uri(SMILE_PNG), "name": f" {ANGER_JA} "},
              {"type": "emotion", "uri": data_uri(SMILE_PNG)}]
    decomposed = [dict(asset) for asset in assets]
    decomposed[0]["name"] = unicodedata.normalize("NFD", composed)
    assert decomposed[0]["name"] != composed

    def slots(listed):
        return [piece.slot for piece in card_art(parse_card(json.loads(encode(card(assets=listed)))), None)[0]]

    expected = ["expr.default.emotion-4f5bbfbe", "avatar", "icon-b80c40ec", "background", "background-9370c62b",
                "asset-9370c62b", "expr.default.emotion-6"]
    assert slots(assets) == slots(decomposed) == expected


def test_a_rerun_over_slots_an_earlier_build_named_by_place_neither_fails_nor_copies_a_picture(store, monkeypatch):
    import grimoire.cards.art as art

    def by_place(asset, prefix):  # the slot names a build before this fix gave such sprites
        return f"{prefix}{asset.index}" if prefix else str(asset.index)

    monkeypatch.setattr(art, "_name", by_place)
    land(store, encode(card(assets=[JA_SMILE, JA_ANGER])), portrait=PORTRAIT)
    monkeypatch.undo()
    images = _images(store)
    before = images.effective_placements(MARA)
    old_smile = before.slot("expr.default.emotion-0").image
    blobs = _blob_count(store)

    rerun = land(store, encode(card(assets=[JA_SMILE, JA_ANGER])), portrait=PORTRAIT, target="card:mara-vell")
    assert {entry.slot: entry.status for entry in rerun.report.art if entry.slot.startswith("expr.")} == {
        SMILE_JA_SLOT: "placed", ANGER_JA_SLOT: "placed"}
    after = images.effective_placements(MARA)
    assert after.slot(SMILE_JA_SLOT).image == old_smile  # the same image object, and no new blob
    assert _blob_count(store) == blobs
    # a slot the card no longer fills keeps its last picture, as for a renamed asset (a known limit)
    assert after.slot("expr.default.emotion-0").image == old_smile
    again = importer(store).plan(encode(card(assets=[JA_SMILE, JA_ANGER])), portrait=PORTRAIT,
                                 target="card:mara-vell")
    assert again.report.status == "unchanged" and again.preview() == {}
