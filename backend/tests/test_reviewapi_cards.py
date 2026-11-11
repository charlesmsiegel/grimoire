"""The review API's card ops beyond one card and one lorebook (110 over 037): several lorebook files, a card
updated by its exact identity, and a batch of cards listed and then approved as one proposal.

Every file of every card is under one digest and one store transaction; a file that cannot be imported is
listed with its reason and left out. Every card and picture is synthetic.
"""

from __future__ import annotations

import pytest

from card_fixtures import LOREBOOK, V3, card, encode, seed_world
from png_fixtures import png, png_card
from review_fixtures import Desk, b64, secured
from test_cardstore_batch import STARS, TIDES, WINDS, named
from test_reviewapi import apply_checked, files_of, refused

from grimoire.cardstore import MAX_BATCH_CARDS, MAX_LOREBOOK_FILES
from grimoire.reviewapi.cardbatch import MAX_BATCH_BYTES, MAX_HELD_BATCHES


@pytest.fixture
def desk(tmp_path):
    with Desk(secured(tmp_path / "lib", seed_world)) as opened:
        yield opened


def upload(*files: tuple[str, bytes]) -> list[dict]:
    return [{"card": b64(data), "origin": name} for name, data in files]


def land(desk, data: bytes, **options) -> dict:
    return apply_checked(desk, desk("card.preview", card=b64(data), **options))


# -- several lorebook files, and the same card again ----------------------------------------------

def test_several_lorebook_files_are_previewed_and_written_with_the_card(desk):
    proposal = desk("card.preview", card=b64(encode(V3)), lorebooks=[b64(TIDES), b64(WINDS), b64(STARS)])
    books = sorted(row["path"] for row in proposal["files"] if "/lorebook/" in row["path"])
    assert [path.split("/lorebook/")[1] for path in books] == ["mara-vell-lore/r1.json", "star-charts/r1.json",
                                                              "tide-tables/r1.json", "wind-lore/r1.json"]
    apply_checked(desk, proposal)
    # the older single argument still works beside the list, as the first file
    assert desk("card.preview", card=b64(encode(V3)), lorebook=b64(TIDES), lorebooks=[b64(WINDS), b64(STARS)])["writes"] == 0


def test_lorebook_files_are_bounded_and_checked(desk):
    limits = desk("review.capabilities")["imports"]["card"]
    assert limits["lorebook_files_max"] == MAX_LOREBOOK_FILES
    too_many = [b64(encode({"spec": "lorebook_v3", "data": {"name": f"Book {n}", "entries": []}}))
                for n in range(MAX_LOREBOOK_FILES + 1)]
    assert refused(lambda: desk("card.preview", card=b64(encode(V3)), lorebooks=too_many)).reason == "invalid_options"
    assert refused(lambda: desk("card.preview", card=b64(encode(V3)), lorebooks=[b64(TIDES), b64(TIDES)])).reason == "invalid_options"
    assert refused(lambda: desk("card.preview", card=b64(encode(V3)), lorebooks="x")).reason == "invalid_field"
    assert refused(lambda: desk("card.preview", card=b64(encode(V3)), lorebooks=[7])).reason == "invalid_field"
    assert refused(lambda: desk("card.preview", card=b64(encode(V3)), lorebooks=[b64(b"{nope")])).reason == "invalid_json"
    assert len(desk.api.proposals) == 0


def test_the_same_card_again_updates_it_when_the_page_asks_and_is_a_second_card_otherwise(desk):
    data = encode(V3)
    land(desk, data)
    apart = desk("card.preview", card=b64(png_card(data)))
    assert (apart["report"]["status"], apart["report"]["source"]) == ("new", "card:mara-vell-2")
    update = desk("card.preview", card=b64(png_card(data)), update_same_card=True)
    assert (update["report"]["status"], update["report"]["source"]) == ("update", "card:mara-vell")
    assert all(row["path"].startswith(("sources/card/mara-vell/", "assets/blobs/")) for row in update["files"])
    other = desk("card.preview", card=b64(named(V3, "Mara Vell", description="Someone else of that name.")),
                 update_same_card=True)
    assert (other["report"]["status"], other["report"]["source"]) == ("new", "card:mara-vell-2")  # a name is no identity
    assert refused(lambda: desk("card.preview", card=b64(data), update_same_card="yes")).reason == "invalid_field"


# -- a batch: listed, then one proposal -----------------------------------------------------------

def test_a_batch_lists_every_file_with_what_importing_it_would_do(desk):
    mara = encode(V3)
    land(desk, mara)                                                     # already here, from JSON
    land(desk, named(V3, "Tobin Marsh"))                                 # already here, unchanged below
    before = files_of(desk.folder)
    found = desk("card.batch_scan", cards=upload(
        ("mara.png", png_card(mara)),                                     # the same card, now with its picture
        ("tobin.json", named(V3, "Tobin Marsh")),                         # exactly what the library holds
        ("ilse.png", png_card(named(V3, "Ilse Dorn", character_book=None), shade=20)),   # new, no lorebook
        ("namesake.json", named(V3, "Tobin Marsh", description="Another Tobin entirely.")),  # a name, not an identity
        ("gazetteer.json", encode(LOREBOOK)),                             # a lorebook on its own
        ("picture.png", png()),                                           # a picture with no card
        ("ilse-again.json", named(V3, "Ilse Dorn", character_book=None)),  # the same card as ilse.png
        ("notes.json", b"not a card"),
    ))
    assert files_of(desk.folder) == before and len(desk.api.proposals) == 0  # listing proposes and writes nothing
    rows = {row["origin"]: row for row in found["cards"]}
    assert [row["item"] for row in found["cards"]] == list(range(8))
    assert rows["mara.png"]["result"] == {"status": "update", "source": "card:mara-vell", "from": None, "to": None,
                                          "same_name": True}  # the fixture world has another Mara Vell
    assert (rows["mara.png"]["container"], rows["mara.png"]["portrait"], rows["mara.png"]["name"]) == ("png", True, "Mara Vell")
    assert rows["mara.png"]["lorebooks"] == [{"name": "Harbor lore", "entries": 2, "origin": "embedded"}]
    assert rows["tobin.json"]["result"]["status"] == "unchanged" and rows["tobin.json"]["writes"] == 0
    assert rows["ilse.png"]["result"] == {"status": "new", "source": "card:ilse-dorn", "from": None, "to": None,
                                          "same_name": False}
    assert rows["ilse.png"]["lorebooks"] == []
    assert rows["namesake.json"]["result"]["status"] == "new" and rows["namesake.json"]["result"]["same_name"]
    assert rows["namesake.json"]["result"]["source"] == "card:tobin-marsh-2"
    assert (rows["gazetteer.json"]["kind"], rows["gazetteer.json"]["entries"], rows["gazetteer.json"]["lorebooks"]) == ("lorebook", 1, [])
    assert rows["picture.png"] == {"item": 5, "origin": "picture.png", "importable": False,
                                   "refused": {"reason": "no_embedded_card", "message": rows["picture.png"]["refused"]["message"]}}
    assert rows["ilse-again.json"]["refused"]["reason"] == "duplicate_in_batch" and "ilse.png" in rows["ilse-again.json"]["refused"]["message"]
    assert rows["notes.json"]["refused"]["reason"] == "invalid_json"
    assert found["counts"] == {"files": 8, "importable": 5, "refused": 3, "new": 3, "update": 1, "unchanged": 1}


def test_a_batch_is_one_proposal_under_one_digest_grouped_by_card_and_approved_at_once(desk):
    mara = encode(V3)
    land(desk, mara)
    found = desk("card.batch_scan", cards=upload(("mara.png", png_card(mara)), ("ilse.json", named(V3, "Ilse Dorn")),
                                                 ("picture.png", png()), ("wren.json", named(V3, "Wren Halloway"))))
    before = files_of(desk.folder)
    proposal = desk("card.batch_preview", batch=found["batch"], include=[0, 1])
    assert files_of(desk.folder) == before
    assert proposal["kind"] == "cards" and proposal["digest"].startswith("sha256:")
    report = proposal["report"]
    assert report["count"] == 2 and [card["origin"] for card in report["cards"]] == ["mara.png", "ilse.json"]
    assert [(card["result"]["status"], card["report"]["source"]) for card in report["cards"]] == \
        [("update", "card:mara-vell"), ("new", "card:ilse-dorn")]
    # every file belongs to exactly one card, and together they are every file of the proposal
    listed = [path for card in report["cards"] for path in card["paths"]]
    assert sorted(listed) == [row["path"] for row in proposal["files"]] and len(set(listed)) == len(listed)
    assert all(path.startswith("sources/card/ilse-dorn/") for path in report["cards"][1]["paths"])
    # what is left out is said, with its reason: the picture that is no card, and the card that was not ticked
    assert report["left_out"] == [
        {"item": 2, "origin": "picture.png", "why": "refused", "reason": "no_embedded_card",
         "message": found["cards"][2]["refused"]["message"]},
        {"item": 3, "origin": "wren.json", "name": "Wren Halloway", "why": "not_included"}]
    applied = apply_checked(desk, proposal)
    assert applied["state"] == "applied"
    assert [(row["origin"], row["status"], row["source"]) for row in applied["result"]["cards"]] == \
        [("mara.png", "update", "card:mara-vell"), ("ilse.json", "new", "card:ilse-dorn")]
    assert sorted(applied["result"]["written"]) == sorted(row["path"] for row in proposal["files"])
    after = files_of(desk.folder)
    assert not any(path.startswith("sources/card/wren-halloway") for path in after)
    # the batch is still held: the card left out can be reviewed next, and the rest now writes nothing
    again = desk("card.batch_preview", batch=found["batch"], include=[0, 1, 3])
    assert [card["result"]["status"] for card in again["report"]["cards"]] == ["unchanged", "unchanged", "new"]
    assert all(row["path"].startswith("sources/card/wren-halloway/") for row in again["files"])


def test_a_stale_digest_or_a_moved_library_writes_no_card_of_the_batch(desk):
    found = desk("card.batch_scan", cards=upload(("ilse.json", named(V3, "Ilse Dorn")), ("tobin.json", named(V3, "Tobin Marsh")),
                                                 ("wren.png", png_card(named(V3, "Wren Halloway")))))
    proposal = desk("card.batch_preview", batch=found["batch"], include=[0, 1, 2])
    before = files_of(desk.folder)
    error = refused(lambda: desk("proposal.apply", proposal=proposal["proposal"], digest="sha256:" + "0" * 64))
    assert error.reason == "stale_approval" and files_of(desk.folder) == before
    assert desk("proposal.get", proposal=proposal["proposal"])["state"] == "open"  # still there to approve as shown
    # another import takes one card's destination: the whole batch is refused, not the two untouched cards written
    land(desk, named(V3, "Tobin Marsh", description="Imported beside the batch."))
    moved = files_of(desk.folder)
    error = refused(lambda: desk("proposal.apply", proposal=proposal["proposal"], digest=proposal["digest"]))
    assert error.reason == "stale" and error.status == 409
    assert any("tobin-marsh" in row["path"] for row in error.details)
    assert files_of(desk.folder) == moved
    assert not any(path.startswith(("sources/card/ilse-dorn", "sources/card/wren-halloway")) for path in moved)
    assert refused(lambda: desk("proposal.get", proposal=proposal["proposal"])).reason == "no_proposal"
    # previewed again from the same held batch, the namesake gets its own source and everything is written
    fresh = desk("card.batch_preview", batch=found["batch"], include=[0, 1, 2])
    assert [card["report"]["source"] for card in fresh["report"]["cards"]] == ["card:ilse-dorn", "card:tobin-marsh-2",
                                                                               "card:wren-halloway"]
    apply_checked(desk, fresh)


def test_a_rejected_batch_writes_nothing(desk):
    found = desk("card.batch_scan", cards=upload(("ilse.json", named(V3, "Ilse Dorn")), ("tobin.json", named(V3, "Tobin Marsh"))))
    proposal = desk("card.batch_preview", batch=found["batch"], include=[1, 0])
    before = files_of(desk.folder)
    assert desk("proposal.reject", proposal=proposal["proposal"])["state"] == "rejected"
    assert files_of(desk.folder) == before
    assert refused(lambda: desk("proposal.apply", proposal=proposal["proposal"], digest=proposal["digest"])).reason == "no_proposal"


def test_a_batch_selection_must_name_importable_files_of_a_held_batch(desk):
    found = desk("card.batch_scan", cards=upload(("ilse.json", named(V3, "Ilse Dorn")), ("picture.png", png())))
    batch = found["batch"]
    for include in ([], [1], [0, 0], [2], [-1]):
        assert refused(lambda: desk("card.batch_preview", batch=batch, include=include)).reason == "invalid_selection", include
    assert refused(lambda: desk("card.batch_preview", batch=batch, include=["0"])).reason == "invalid_field"
    assert refused(lambda: desk("card.batch_preview", batch=batch)).reason == "invalid_field"
    error = refused(lambda: desk("card.batch_preview", batch="batch-nowhere", include=[0]))
    assert (error.reason, error.status) == ("no_batch", 404)
    assert len(desk.api.proposals) == 0
    # only a bounded few batches are held: the oldest must be chosen again
    for _ in range(MAX_HELD_BATCHES):
        desk("card.batch_scan", cards=upload(("ilse.json", named(V3, "Ilse Dorn"))))
    assert refused(lambda: desk("card.batch_preview", batch=batch, include=[0])).reason == "no_batch"


def test_a_batch_is_bounded_in_cards_and_in_bytes_with_a_clear_refusal(desk):
    limits = desk("review.capabilities")["imports"]["card"]["batch"]
    assert limits == {"max_cards": MAX_BATCH_CARDS, "max_bytes": MAX_BATCH_BYTES}
    small = b64(named(V3, "Ilse Dorn"))
    error = refused(lambda: desk("card.batch_scan", cards=[{"card": small, "origin": f"{n}.json"} for n in range(MAX_BATCH_CARDS + 1)]))
    assert (error.reason, error.status) == ("too_many_cards", 400) and str(MAX_BATCH_CARDS) in str(error)
    # the bound on bytes is checked from the text's length, before anything is decoded
    big = "A" * (MAX_BATCH_BYTES // 3 * 4 // 2 + 8)
    error = refused(lambda: desk("card.batch_scan", cards=[{"card": big}, {"card": big}]))
    assert (error.reason, error.status) == ("too_large", 400) and "smaller batches" in str(error)
    for cards in ([], "x", [7], [{"origin": "x"}], [{"card": small, "origin": 7}]):
        assert refused(lambda: desk("card.batch_scan", cards=cards)).reason == "invalid_field", cards
    assert refused(lambda: desk("card.batch_scan")).reason == "invalid_field"
    assert len(desk.api.batches) == 0


def test_one_unreadable_upload_among_good_ones_is_a_row_and_the_rest_still_import(desk):
    found = desk("card.batch_scan", cards=[
        {"card": b64(named(V3, "Ilse Dorn")), "origin": "ilse.json"},
        {"card": "not base64 at all", "origin": "garbled.json"},
        {"card": b64(b" " * (2 * 1024 * 1024 + 1)), "origin": "huge.json"},
        {"card": b64(png_card(named(V3, "Tobin Marsh"))[:-9]), "origin": "cut.png"},
        {"card": b64(named(V3, "Wren Halloway")), "origin": "bad\nname.json"},
        {"card": b64(named(V3, "Tobin Marsh"))},
    ])
    reasons = [row["refused"]["reason"] if not row["importable"] else row["result"]["status"] for row in found["cards"]]
    assert reasons == ["new", "invalid_field", "too_large", "invalid_png", "invalid_options", "new"]
    proposal = desk("card.batch_preview", batch=found["batch"], include=[0, 5])
    assert [row["why"] for row in proposal["report"]["left_out"]] == ["refused"] * 4
    apply_checked(desk, proposal)
    assert {"sources/card/ilse-dorn/_source/manifest.json", "sources/card/tobin-marsh/_source/manifest.json"} <= set(files_of(desk.folder))


def test_cards_of_one_name_in_a_batch_are_listed_and_written_apart(desk):
    found = desk("card.batch_scan", cards=upload(("a.json", named(V3, "Ilse Dorn")),
                                                 ("b.json", named(V3, "Ilse Dorn", description="A different Ilse."))))
    assert [(row["result"]["source"], row["result"]["same_name"]) for row in found["cards"]] == \
        [("card:ilse-dorn", False), ("card:ilse-dorn-2", True)]
    only_second = desk("card.batch_preview", batch=found["batch"], include=[1])
    assert only_second["report"]["cards"][0]["report"]["source"] == "card:ilse-dorn"  # alone, it takes the plain name
    both = desk("card.batch_preview", batch=found["batch"], include=[0, 1])
    assert [card["report"]["source"] for card in both["report"]["cards"]] == ["card:ilse-dorn", "card:ilse-dorn-2"]
    apply_checked(desk, both)
