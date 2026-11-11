"""Pinned chats pin their pictures too (Sprint 046, the owner's call). Synthetic Wren and Mara only.

A pinned actor shows the art its revision showed when it was made, until a reviewed rebase adopts the
new art. A following actor's art stays live. The snapshot is data the resolver re-checks: a forged or
damaged one never shows another entity's picture.
"""

from __future__ import annotations

import inspect
from dataclasses import replace

import pytest
from image_fixtures import GIF, HARBOR, PNG, PNG_2, SAILOR, WREN_A, handle, seed
from portrayal_fixtures import BASE, CAST, VAMPIRE_MARA, seed_elderfen
from rebase_fixtures import (  # noqa: F401 (fixtures)
    R1, R2, WREN, chats, publish, put, rebase, reason, start, store, wren,
)
from store_helpers import OWNER
from sync_helpers import DESKTOP, PHONE

from grimoire.chats import Launch
from grimoire.core.identity import ImageRef, InstanceRef, PlacementRef, SourceRef
from grimoire.images import Crop, Hidden, Placement, Shown, Unavailable
from grimoire.imagestore import ART_PIN, ImageLibrary, InvalidPlacement, freeze_art
from grimoire.store import LibraryStore, RecordEnvelope
from grimoire.store.jsonfmt import thaw
from grimoire.store.layout import address_parts
from grimoire.sync import identify, sync_until_quiet

WORLD = WREN.source
_FULL = ("sources", "campaigns", "assets", "kept")
OLD, NEW, ODD = ImageRef(WORLD, "wren-old"), ImageRef(WORLD, "wren-new"), ImageRef(CAST, "mara-odd")


@pytest.fixture
def images(store):
    library = ImageLibrary(store, handle(), owner=OWNER)
    batch = library.change()
    batch.add_image(WORLD, "wren-old", PNG)
    batch.add_image(WORLD, "wren-new", PNG_2)
    batch.place(WREN, "portrait", Placement("set", "portrait", OLD), None)
    batch.commit()
    return library


def _replace(images, subject, slot, placement):
    _, etag = images.read_placement(PlacementRef(subject, slot))
    images.place(subject, slot, placement, etag).commit()


def _follow(chats, **options):
    """A chat that follows Wren's head: after pin by default, only with an explicit ``follow``."""
    if "follow" in inspect.signature(chats.start_chat).parameters:
        options["follow"] = True
    return chats.start_chat(Launch("character", WREN), **options).commit()


def _follower(chats):
    [actor] = _follow(chats).actors
    return actor


def _portrait(images, subject):
    return images.effective_placements(subject).slot("portrait")


def test_a_pinned_actor_keeps_its_picture_until_a_reviewed_rebase(store, chats, rebase, images):
    _, pinned = start(chats, R1)
    follower = _follower(chats)
    assert store.read(pinned).envelope.extra[ART_PIN]["base"] == str(R1)
    assert ART_PIN not in store.read(follower).envelope.extra
    _replace(images, WREN, "portrait", Placement("set", "portrait", NEW))  # the world gets new art
    assert _portrait(images, pinned).image == OLD  # a source update never silently changes a pinned chat
    assert _portrait(images, follower).image == NEW
    assert _portrait(images, WREN).image == NEW

    review = rebase.review(pinned)  # no new revision: the review is of the art alone
    assert review.rows == ()
    [change] = review.art
    assert change.slot == "portrait" and change.before.image == OLD and change.after.image == NEW
    assert _portrait(images, pinned).image == OLD  # reviewing changes nothing
    rebase.apply(review, {}).commit()
    assert _portrait(images, pinned).image == NEW
    _replace(images, WREN, "portrait", Placement("set", "portrait", OLD))
    assert _portrait(images, pinned).image == NEW  # pinned again, to the pictures the review showed


def test_a_rebase_onto_a_new_revision_carries_the_art_it_showed(store, chats, rebase, images):
    _, pinned = start(chats, R1)
    publish(store, wren(R2, sections={"biography": "Born in the harbor town.", "voice-evidence": "Counts tides."}))
    _replace(images, WREN, "portrait", Placement("set", "portrait", NEW))
    review = rebase.review(pinned)
    assert [change.slot for change in review.art] == ["portrait"]
    _replace(images, WREN, "portrait", Placement("set", "portrait", OLD))  # changed again after the review
    rebase.apply(review, {name: "take" for name in review.decisions_needed}).commit()
    stored = store.read(pinned).envelope
    assert stored.base == str(R2) and stored.extra[ART_PIN]["base"] == str(R2)
    assert _portrait(images, pinned).image == NEW  # exactly what the review showed, never newer art


def test_a_following_actor_has_no_pin_after_its_rebase(store, chats, rebase, images):
    follower = _follower(chats)
    _replace(images, WREN, "portrait", Placement("set", "portrait", NEW))
    assert reason(lambda: rebase.review(follower)) == "up_to_date"  # its art is live: nothing to review
    publish(store, wren(R2, sections={"biography": "Born in the harbor town.", "voice-evidence": "Counts tides."}))
    review = rebase.review(follower)
    assert review.art == ()
    rebase.apply(review, {name: "take" for name in review.decisions_needed}).commit()
    assert ART_PIN not in store.read(follower).envelope.extra
    _replace(images, WREN, "portrait", Placement("set", "portrait", NEW))
    assert _portrait(images, follower).image == NEW


def test_the_campaigns_own_art_stays_live_over_pinned_art(store, chats, images):
    _, pinned = start(chats, R1)
    _replace(images, WREN, "portrait", Placement("set", "portrait", NEW))
    images.place(pinned, "avatar", Placement("set", "avatar", NEW), None).commit()  # its source's picture
    images.place(pinned, "portrait", Placement("hide"), None).commit()
    shown = images.effective_placements(pinned)
    assert isinstance(shown.slot("portrait"), Hidden) and shown.origin("portrait").origin == "cleared"
    assert shown.slot("avatar").image == NEW and shown.slot("avatar").layer == "campaign"


def _edit_pin(store, actor, **changes):
    stored = store.read(actor).envelope
    pin = dict(thaw(stored.extra[ART_PIN]))
    pin.update(changes)
    put(store, replace(stored, extra={**thaw(stored.extra), ART_PIN: pin}))


def test_a_pin_naming_another_entity_inherits_nothing(store, chats, images):
    _, pinned = start(chats, R1)
    layers = thaw(store.read(pinned).envelope.extra[ART_PIN])["layers"]
    stranger = [{**layers[0], "entity": "world:elsewhere/character/wren"}]  # a namesake's layer
    _edit_pin(store, pinned, layers=stranger)
    shown = images.effective_placements(pinned)
    assert shown.slot("portrait") is None
    assert "art_pin_mismatch" in {problem.code for problem in shown.problems}


def test_a_pin_cannot_show_a_picture_its_entity_does_not_own(store, chats, images):
    _, pinned = start(chats, R1)
    layers = thaw(store.read(pinned).envelope.extra[ART_PIN])["layers"]
    layers[0]["placements"]["portrait"]["image"] = "world:elsewhere#image/someone"
    _edit_pin(store, pinned, layers=layers)
    portrait = _portrait(images, pinned)
    assert isinstance(portrait, Unavailable) and portrait.reason == "wrong_scope"


def test_an_unreadable_pin_fails_closed(store, chats, images):
    _, pinned = start(chats, R1)
    _edit_pin(store, pinned, layers="everything")
    shown = images.effective_placements(pinned)
    assert shown.slot("portrait") is None
    assert "art_pin_unreadable" in {problem.code for problem in shown.problems}


def test_a_pin_taken_for_another_base_is_ignored_and_art_is_live(store, chats, images):
    _, pinned = start(chats, R1)
    _edit_pin(store, pinned, base=str(R2))  # e.g. an older build rebased the actor and kept the field
    _replace(images, WREN, "portrait", Placement("set", "portrait", NEW))
    assert _portrait(images, pinned).image == NEW


@pytest.fixture
def elderfen(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed_elderfen(opened, handle())
        library = ImageLibrary(opened, handle(), owner=OWNER)
        batch = library.change()
        batch.add_image(CAST, "mara-portrait", PNG)
        batch.add_image(CAST, "mara-odd", GIF)
        batch.place(BASE.unpinned(), "portrait", Placement("set", "portrait", ImageRef(CAST, "mara-portrait")), None)
        batch.commit()
        yield opened, library


def test_a_pinned_portrayal_keeps_its_base_chains_art(elderfen):
    store, images = elderfen
    vampire = VAMPIRE_MARA.unpinned()
    images.place(vampire, "avatar", Placement("hide"), None).commit()
    pinned, live = InstanceRef("c1", "character", "mara"), InstanceRef("c1", "character", "mara-2")
    pin = freeze_art(store, VAMPIRE_MARA)
    assert [layer["entity"] for layer in pin["layers"]] == [str(BASE.unpinned()), str(vampire)]
    txn = store.transaction()
    for ref, extra in ((pinned, {ART_PIN: pin}), (live, {})):
        txn.put(RecordEnvelope(ref, {}, derived_from=str(vampire), base=str(VAMPIRE_MARA), extra=extra), None)
    txn.commit(handle())
    _, etag = images.read_placement(PlacementRef(BASE.unpinned(), "portrait"))
    images.place(BASE.unpinned(), "portrait", Placement("set", "portrait", ODD), etag).commit()  # the species changes
    _, etag = images.read_placement(PlacementRef(vampire, "avatar"))
    images.place(vampire, "avatar", Placement("inherit"), etag).commit()  # and the portrayal un-hides its avatar
    kept = images.effective_placements(pinned)
    assert isinstance(kept.slot("portrait"), Shown) and kept.slot("portrait").image == ImageRef(CAST, "mara-portrait")
    assert kept.origin("portrait").placed_by == BASE.unpinned()
    assert isinstance(kept.slot("avatar"), Hidden)
    followed = images.effective_placements(live)
    assert followed.slot("portrait").image == ODD and followed.slot("avatar") is None


# -- an actor shared by pinned and following chats ---------------------------------------------

def test_a_pinned_chat_pins_the_follower_it_reuses(store, chats, images):
    started = _follow(chats)
    [actor] = started.actors
    assert ART_PIN not in store.read(actor).envelope.extra
    chats.start_chat(Launch("character", R1), scope=started.scope,
                     reuse_actors={("character", WREN.entity_key): actor}).commit()
    stored = store.read(actor).envelope
    assert stored.extra[ART_PIN]["base"] == stored.base  # frozen at the revision the actor already shows
    _replace(images, WREN, "portrait", Placement("set", "portrait", NEW))
    assert _portrait(images, actor).image == OLD  # the pinned chat's pictures hold still


def test_a_following_chat_never_unpins_a_shared_actor(store, chats, rebase, images):
    started, pinned = start(chats, R1)
    _follow(chats, scope=started.scope, reuse_actors={("character", WREN.entity_key): pinned})
    _replace(images, WREN, "portrait", Placement("set", "portrait", NEW))
    assert _portrait(images, pinned).image == OLD
    review = rebase.review(pinned)
    assert [change.slot for change in review.art] == ["portrait"]
    rebase.apply(review, {name: "take" for name in review.decisions_needed}).commit()
    assert store.read(pinned).envelope.extra[ART_PIN]["base"] == str(R1)  # still pinned, to the reviewed art
    _replace(images, WREN, "portrait", Placement("set", "portrait", OLD))
    assert _portrait(images, pinned).image == NEW


def test_an_unpinned_actor_is_pinned_by_a_review_of_no_change(store, chats, rebase, images):
    """An actor made before 046 (or whose pin was lost) shows live art; a review pins what it shows."""
    _, pinned = start(chats, R1)
    stored = store.read(pinned).envelope
    put(store, replace(stored, extra={k: v for k, v in thaw(stored.extra).items() if k != ART_PIN}))
    review = rebase.review(pinned)
    assert review.rows == () and review.art == ()
    rebase.apply(review, {}).commit()
    assert store.read(pinned).envelope.extra[ART_PIN]["base"] == str(R1)
    _replace(images, WREN, "portrait", Placement("set", "portrait", NEW))
    assert _portrait(images, pinned).image == OLD


# -- the campaign's own placements over pinned art ----------------------------------------------

def test_a_campaign_adjust_names_the_pinned_picture(store, chats, images):
    _, pinned = start(chats, R1)
    _replace(images, WREN, "portrait", Placement("set", "portrait", NEW))
    with pytest.raises(InvalidPlacement) as refused:  # the actor shows OLD, not the entity's live NEW
        images.place(pinned, "portrait", Placement("adjust", "portrait", NEW, Crop(0, 0, 5000, 5000)), None).commit()
    assert refused.value.reason == "stale_adjust"
    images.place(pinned, "portrait", Placement("adjust", "portrait", OLD, Crop(0, 0, 5000, 5000)), None).commit()
    portrait = _portrait(images, pinned)
    assert portrait.image == OLD and portrait.crop == Crop(0, 0, 5000, 5000)


def test_a_pin_keeps_only_what_a_slot_shows(store, chats, images):
    """Open provenance may nest as deep as a placement file allows; the instance file would refuse it."""
    deep: dict = {"note": "kept nowhere"}
    for _ in range(90):
        deep = {"next": deep}
    _replace(images, WREN, "portrait", Placement("set", "portrait", OLD, provenance={"origin": "x", "deep": deep}))
    _, pinned = start(chats, R1)
    [layer] = thaw(store.read(pinned).envelope.extra[ART_PIN])["layers"]
    assert layer["placements"]["portrait"] == {"mode": "set", "role": "portrait", "image": str(OLD)}
    assert _portrait(images, pinned).image == OLD


def test_a_forged_derived_flag_grants_no_reset(store, chats, images):
    _, pinned = start(chats, R1)
    layers = thaw(store.read(pinned).envelope.extra[ART_PIN])["layers"]
    layers[0]["derived"] = True  # Wren is standalone: it inherits nothing, so its slots have nothing to reset to
    layers[0]["placements"]["portrait"] = {"mode": "inherit"}
    _edit_pin(store, pinned, layers=layers)
    portrait = _portrait(images, pinned)
    assert isinstance(portrait, Unavailable) and portrait.reason == "invalid_placement"


# -- sync ---------------------------------------------------------------------------------------

def test_sync_holds_an_adjust_of_pinned_art(tmp_path):
    """Sync compares an adjust with the entity's live placement; over pinned art that is not the
    picture the actor shows, so the adjust is held until sync compares pinned art."""
    desktop, phone = LibraryStore.create(tmp_path / DESKTOP), LibraryStore.create(tmp_path / PHONE)
    seed(desktop)
    identify(desktop, DESKTOP)
    identify(phone, PHONE)
    images = ImageLibrary(desktop, handle(), owner=OWNER)
    batch = images.change()
    first = batch.add_image(HARBOR, "first", PNG)
    batch.place(SAILOR, "avatar", Placement("set", "avatar", first), None)
    batch.commit()
    stored = desktop.read(WREN_A)
    pinned = replace(stored.envelope, extra={**thaw(stored.envelope.extra),
                                             ART_PIN: freeze_art(desktop, SourceRef.parse(stored.envelope.base))})
    txn = desktop.transaction()
    txn.put(pinned, stored.etag)
    txn.commit(handle())
    images.place(WREN_A, "avatar", Placement("adjust", "avatar", first, Crop(0, 0, 5000, 5000)), None).commit()
    hands = {DESKTOP: handle(*_FULL), PHONE: handle(*_FULL)}
    report = sync_until_quiet(desktop, phone, hands)
    adjust = "/".join(address_parts(PlacementRef(WREN_A, "avatar")))
    assert {conflict.path: conflict.code for conflict in report.conflicts} == {adjust: "head_dependency"}
    assert "pinned art" in report.conflict(adjust).detail
    assert not (phone.root / adjust).exists()
    desktop.close()
    phone.close()
