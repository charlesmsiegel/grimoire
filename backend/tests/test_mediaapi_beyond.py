"""Pictures beyond character slots: covers (an entry's, a source's, a campaign's), lore entry pictures, scene
backdrops and pictures on posts, through the app's media adapter, on one device and across two.

Every picture is a synthetic byte string; every model call is a scripted fake.
"""

from __future__ import annotations

import base64

import pytest
from card_fixtures import SCENARIO, encode, land
from scene_fixtures import ANYA, ANYA_CARD, MARA_VELL, STORM, VOICED, secure
from textapi_fixtures import DESKTOP, Device, desktop_library, keep, pair_devices, phone_library, say, serving

from grimoire.app.appapi import AppApi
from grimoire.core.identity import ChatRef, OwnerRef, PlacementRef, TurnRef
from grimoire.mediaapi.beyond import COPIED_FROM, MAX_SHOWN, entry_slot
from grimoire.store import LibraryStore
from grimoire.store.layout import address_parts, turn_parts
from grimoire.textapi import ApiRefused

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR synthetic cover"
OTHER_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR another synthetic cover"
THIRD_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR a third synthetic cover"
LORE = "card:mara-vell/lorebook/mara-vell-lore"
MARA_CARD = "card:mara-vell/character/mara-vell"
CARDS = "card:mara-vell"
WORLD = "world:saltmarch"
CAMPAIGN = "campaign:q-c-1"


def _media(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class App:
    """One device's library through the app's dispatch (the text API's ops and every adapter's)."""

    def __init__(self, device: Device) -> None:
        self.device = device
        self.api = AppApi(device.api)

    def __call__(self, op: str, **args):
        return self.api.call(op, args)

    def refused(self, op: str, **args) -> ApiRefused:
        with pytest.raises(ApiRefused) as caught:
            self(op, **args)
        return caught.value

    def image(self, ref: str) -> bytes:
        return self.device.api.image(ref)[1]

    def put(self, subject: str, slot: str, data: bytes = PNG, *, scope: str | None = None, **more) -> dict:
        """Upload ``data`` into ``slot`` of ``subject``, over whatever the subject itself placed there."""
        own = next((entry for entry in self("media.art", subject=subject)["own"] if entry["slot"] == slot), None)
        owner = scope or (subject if "/" not in subject else subject.split("/")[0])
        return self("media.upload", scope=owner, media=_media(data), subject=subject, slot=slot,
                    expected=None if own is None else own["etag"], **more)["art"]

    def cover(self, subject: str) -> dict:
        (found,) = self("media.shown", subjects=[subject])["shown"]
        return found


def _slot(art: dict, name: str) -> dict | None:
    return next((slot for slot in art["slots"] if slot["slot"] == name), None)


def _own(art: dict, name: str) -> dict | None:
    return next((entry for entry in art["own"] if entry["slot"] == name), None)


@pytest.fixture
def desk(tmp_path):
    folder, chat = desktop_library(tmp_path / "desk")
    return folder, str(chat)


@pytest.fixture
def synced(tmp_path):
    """Paired desktop and phone, the phone holding the desktop's library after one sync: (desk, phone, chat)."""
    folder, chat = desktop_library(tmp_path / "desk")
    phone = phone_library(tmp_path / "phone")
    pair_devices(folder, phone)
    with Device(folder) as desktop, serving(desktop) as server, Device(phone, peers={DESKTOP: server.address}) as mobile:
        assert mobile("sync", peer=DESKTOP)["status"] == "synced"
    return folder, phone, str(chat)


# -- covers ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("subject", [LORE, CARDS, WORLD, CAMPAIGN])
def test_a_cover_is_set_changed_and_removed_on_an_entry_a_source_and_a_campaign(desk, subject):
    folder, _chat = desk
    with Device(folder) as device:
        app = App(device)
        assert app.cover(subject) == {"subject": subject, "slot": "cover", "state": "empty", "etag": None}
        art = app.put(subject, "cover", note="Fog over the quay")
        cover = _slot(art, "cover")
        assert (cover["state"], cover["role"], cover["origin"]) == ("shown", "gallery", "local")
        assert app.image(cover["image"]) == PNG and cover["provenance"]["origin"] == "placed in the app"
        assert art["modes"] == ["set", "hide"] and art["reusable"] == [subject if "/" not in subject else CARDS]
        shown = app.cover(subject)
        assert (shown["state"], shown["image"], shown["etag"]) == ("shown", cover["image"], _own(art, "cover")["etag"])
        # a stale edit is refused and nothing is written
        stale = app.refused("media.place", subject=subject, slot="cover", mode="hide", expected=None)
        assert (stale.reason, stale.status) == ("changed", 409) and app.cover(subject) == shown
        # another picture never silently replaces this one: it is a new image object, and the first is still there
        changed = app.put(subject, "cover", OTHER_PNG)
        assert app.image(_slot(changed, "cover")["image"]) == OTHER_PNG and app.image(cover["image"]) == PNG
        removed = app("media.place", subject=subject, slot="cover", mode="hide",
                      expected=_own(changed, "cover")["etag"])
        assert _slot(removed, "cover")["state"] == "hidden" and app.cover(subject)["state"] == "hidden"
        # and it can be put back: the picture was never deleted
        back = app("media.place", subject=subject, slot="cover", mode="set", image=cover["image"],
                   crop={"x": 0, "y": 2500, "w": 10000, "h": 5000}, expected=_own(removed, "cover")["etag"])
        assert (_slot(back, "cover")["image"], _slot(back, "cover")["crop"]["h"]) == (cover["image"], 5000)
        assert device.api.store.load().problems == ()


def test_a_cover_obeys_the_scope_rules_and_names_a_subject_that_exists(desk):
    folder, _chat = desk
    with Device(folder) as device:
        app = App(device)
        card = app.put(MARA_CARD, "portrait")
        card_image = _slot(card, "portrait")["image"]
        campaign = app.put(CAMPAIGN, "cover", OTHER_PNG)
        campaign_image = _slot(campaign, "cover")["image"]
        # whose pictures a subject may use is unchanged (038 W7): a source's own, a campaign's own
        for subject, image, expected in ((WORLD, card_image, None), (CARDS, campaign_image, None),
                                         (CAMPAIGN, card_image, _own(campaign, "cover")["etag"]),
                                         (LORE, campaign_image, None)):
            error = app.refused("media.place", subject=subject, slot="cover", mode="set", image=image,
                                expected=expected)
            assert error.reason == "wrong_scope", (subject, error)
        assert app.cover(CARDS)["state"] == "empty" and app.cover(WORLD)["state"] == "empty"
        # a source or a campaign inherits nothing: there is nothing to re-crop or to reset to
        for mode, more in (("inherit", {}), ("adjust", {"image": campaign_image})):
            error = app.refused("media.place", subject=CAMPAIGN, slot="cover", mode=mode,
                                expected=_own(campaign, "cover")["etag"], **more)
            assert error.reason == "mode_not_allowed"
        for subject, scope in (("campaign:nowhere", "campaign:nowhere"), ("world:nowhere", "world:nowhere")):
            assert app.refused("media.upload", scope=scope, media=_media(PNG), subject=subject, slot="cover",
                               expected=None).reason in ("missing_subject", "missing_scope")
        assert app("media.art", subject="world:nowhere")["slots"] == []
        assert app("media.art", subject="world:nowhere")["problems"][0]["code"] == "missing_source"
        assert app("media.art", subject="campaign:nowhere")["problems"][0]["code"] == "missing_subject"
        assert app.refused("media.art", subject="World:Harbor").reason == "invalid_field"


def test_one_request_reads_a_grid_of_covers_and_one_bad_subject_never_fails_the_rest(desk):
    folder, _chat = desk
    with Device(folder) as device:
        app = App(device)
        art = app.put(LORE, "cover")
        found = app("media.shown", subjects=[LORE, MARA_CARD, "not a ref", WORLD, LORE, "world:nowhere"])
        assert found["slot"] == "cover" and [item["subject"] for item in found["shown"]] == \
            [LORE, MARA_CARD, "not a ref", WORLD, "world:nowhere"]
        by = {item["subject"]: item for item in found["shown"]}
        assert by[LORE]["image"] == _slot(art, "cover")["image"] and by[MARA_CARD]["state"] == "empty"
        assert (by["not a ref"]["state"], by["not a ref"]["reason"]) == ("unavailable", "invalid_subject")
        assert by["world:nowhere"]["state"] == "empty"
        portrait = app("media.shown", subjects=[MARA_CARD], slot="avatar")["shown"][0]
        assert portrait["slot"] == "avatar"
        for bad in ({"subjects": "x"}, {"subjects": [1]}, {"subjects": [LORE] * (MAX_SHOWN + 1)},
                    {"subjects": [LORE], "slot": "Cover"}, {"subjects": [LORE], "slot": 3}):
            assert app.refused("media.shown", **bad).reason == "invalid_field"


def test_the_gallery_lists_a_scope_itself_first_and_where_its_pictures_are_used(desk):
    folder, _chat = desk
    with Device(folder) as device:
        app = App(device)
        image = _slot(app.put(CAMPAIGN, "cover"), "cover")["image"]
        gallery = app("media.gallery", scope=CAMPAIGN)
        assert gallery["subjects"][0]["subject"] == CAMPAIGN
        (listed,) = [item for item in gallery["images"] if item["image"] == image]
        assert listed["used_by"] == [{"subject": CAMPAIGN, "slot": "cover", "mode": "set"}]
        world = app("media.gallery", scope=WORLD)
        assert world["subjects"][0] == {"subject": WORLD, "name": "Saltmarch"}


def test_a_damaged_cover_file_shows_nothing_and_is_reported_never_a_crash(desk):
    folder, _chat = desk
    with Device(folder) as device:
        app = App(device)
        app.put(CAMPAIGN, "cover")
        path = folder.joinpath(*address_parts(PlacementRef(OwnerRef(CAMPAIGN), "cover")))
        assert path.parent.name == "scope-art"
        path.write_text("{ not json")
        cover = app.cover(CAMPAIGN)
        assert (cover["state"], cover["reason"]) == ("unavailable", "invalid_placement")
        own = _own(app("media.art", subject=CAMPAIGN), "cover")
        assert own["state"] == "unreadable" and own["etag"]  # and it can be replaced, as read
        fixed = app("media.upload", scope=CAMPAIGN, media=_media(OTHER_PNG), subject=CAMPAIGN, slot="cover",
                    expected=own["etag"])["art"]
        assert _slot(fixed, "cover")["state"] == "shown"
        (path.parent / "notes.txt").write_text("stray")
        problems = device.api.store.load().problems
        assert [(problem.path.rsplit("/", 1)[-1], problem.code) for problem in problems] == [("notes.txt", "unrecognized")]


# -- lore entry pictures -----------------------------------------------------------------------------------

def test_an_entry_picture_follows_its_entry_through_a_reorder_and_is_orphaned_by_an_edit(desk):
    folder, _chat = desk
    with Device(folder) as device:
        app = App(device)
        first = app("media.entries", subject=LORE)
        assert [entry["index"] for entry in first["entries"]] == [0, 1]
        assert all(entry["picture"]["state"] == "empty" for entry in first["entries"]) and first["orphans"] == []
        lighthouse = first["entries"][0]["slot"]
        assert lighthouse == entry_slot({"keys": ["lighthouse"], "content": "The lighthouse has been dark for a year."})
        art = app.put(LORE, lighthouse)
        image = _slot(art, lighthouse)["image"]
        # a picture goes only on an entry the lorebook holds
        missing = app.refused("media.upload", scope=CARDS, media=_media(PNG), subject=LORE, slot="entry.nothing",
                              expected=None)
        assert missing.reason == "missing_entry" and _slot(app("media.art", subject=LORE), "entry.nothing") is None
        # reordered: the picture is still the lighthouse entry's, now second
        read = app("sources.read", ref=LORE)
        app("sources.save", ref=read["entity"], head=read["head"], etag=read["etag"], expected=read["manifest_etag"],
            fields={"entries": [{"index": 1}, {"index": 0}]})
        after = app("media.entries", subject=LORE)
        # the entries are numbered in the lorebook's head, which the answer names: a page showing another
        # revision's entries pairs none of them by index
        assert first["revision"] and after["revision"] != first["revision"]
        swapped = after["entries"]
        assert [(entry["slot"] == lighthouse, entry["picture"]["state"]) for entry in swapped] == \
            [(False, "empty"), (True, "shown")]
        assert swapped[1]["picture"]["image"] == image and swapped[1]["picture"]["etag"]
        # its text edited: the picture is an orphan, listed and on no entry
        read = app("sources.read", ref=LORE)
        app("sources.save", ref=read["entity"], head=read["head"], etag=read["etag"], expected=read["manifest_etag"],
            fields={"entries": [{"index": 0}, {"index": 1, "content": "The lighthouse was relit last night."}]})
        edited = app("media.entries", subject=LORE)
        assert all(entry["picture"]["state"] == "empty" for entry in edited["entries"])
        (orphan,) = edited["orphans"]
        assert (orphan["slot"], orphan["image"]) == (lighthouse, image)
        # the orphan can be taken off (nothing checks a hide), and the picture is still in the gallery
        app("media.place", subject=LORE, slot=lighthouse, mode="hide", expected=orphan["etag"])
        assert app("media.entries", subject=LORE)["orphans"] == []
        assert image in [item["image"] for item in app("media.gallery", scope=CARDS)["images"]]
        for subject, reason in ((MARA_CARD, "not_a_lorebook"), (CAMPAIGN, "invalid_field"), ("x", "invalid_field")):
            assert app.refused("media.entries", subject=subject).reason == reason


def test_entries_that_say_the_same_share_a_slot_and_a_value_that_is_no_entry_has_none():
    one = entry_slot({"keys": ["storm"], "content": "Three days.", "enabled": False})
    assert one == entry_slot({"content": "Three days.", "keys": ["storm"], "settings": {"insertion_order": 4}})
    assert one != entry_slot({"keys": ["storm", "gale"], "content": "Three days."})
    assert one != entry_slot({"keys": ["storm"], "content": "Three days"})
    assert one.startswith("entry.") and len(one) <= 123 and PlacementRef(OwnerRef("x"), one)
    assert entry_slot("text") is None and entry_slot({"keys": 3, "content": None}) == entry_slot({})


# -- scene backdrops ---------------------------------------------------------------------------------------

def test_a_chat_has_a_backdrop_and_may_take_one_from_the_scenario_it_is_played_from(tmp_path):
    folder = tmp_path / "cards"
    with LibraryStore.create(folder) as store:
        for document in (VOICED, ANYA_CARD, SCENARIO):
            land(store, encode(document))
    secure(folder)
    scenario = str(STORM)
    with Device(folder) as device:
        app = App(device)
        chat = app("play.start", kind="character", ref=str(MARA_VELL), persona=str(ANYA))["chat"]
        storm = app("play.start", kind="scenario", ref=scenario, persona=str(ANYA), opening="opening-1")["chat"]
        campaign = storm.split("#", 1)[0]
        chat_id = ChatRef.parse(storm).chat_id
        slot = "backdrop." + chat_id
        seen = app("media.chat", chat=storm)
        assert (seen["subject"], seen["backdrop"]) == (campaign, {"slot": slot, "state": "empty", "etag": None})
        assert seen["offered"] == [] and seen["posts"] == [] and seen["orphans"] == []
        # the scenario carries two backdrops; a chat played from it is offered them, another chat is not
        inn = _slot(app.put(scenario, "backdrop.inn", note="The harbor inn"), "backdrop.inn")["image"]
        app.put(scenario, "backdrop.quay", OTHER_PNG)
        offered = app("media.chat", chat=storm)["offered"]
        assert [(item["slot"], item["from"]) for item in offered] == [("backdrop.inn", scenario),
                                                                      ("backdrop.quay", scenario)]
        assert offered[0]["image"] == inn and offered[0]["provenance"]["note"] == "The harbor inn"
        assert app("media.chat", chat=chat)["offered"] == []
        # a campaign places only its own pictures: the scenario's picture is refused as it is...
        assert app.refused("media.place", subject=campaign, slot=slot, mode="set", image=inn,
                           expected=None).reason == "wrong_scope"
        assert app.refused("media.backdrop_from", chat=chat, image=inn, expected=None).reason == "wrong_scope"
        # ...and taken as the campaign's own copy over the same stored bytes, saying where it came from
        art = app("media.backdrop_from", chat=storm, image=inn, expected=None)
        backdrop = _slot(art, slot)
        assert backdrop["image"].startswith(campaign + "#image/") and app.image(backdrop["image"]) == PNG
        (copy,) = [item for item in app("media.gallery", scope=campaign)["images"] if item["image"] == backdrop["image"]]
        assert copy["provenance"]["note"] == "The harbor inn" and COPIED_FROM in copy["provenance"]["other"]
        assert copy["sha256"] == [item for item in app("media.gallery", scope=STORM.source)["images"]
                                  if item["image"] == inn][0]["sha256"]
        assert app("media.chat", chat=storm)["backdrop"]["image"] == backdrop["image"]
        # taking it again makes no second copy; a stale read is refused
        assert app.refused("media.backdrop_from", chat=storm, image=inn, expected=None).reason == "changed"
        again = app("media.backdrop_from", chat=storm, image=inn, expected=_own(art, slot)["etag"],
                    crop={"x": 0, "y": 0, "w": 10000, "h": 5000})
        assert _slot(again, slot)["image"] == backdrop["image"] and _slot(again, slot)["crop"]["h"] == 5000
        assert len(app("media.gallery", scope=campaign)["images"]) == 1
        # a file of the person's own works like any other slot, and Remove hides it
        mine = app.put(campaign, slot, THIRD_PNG)
        assert app.image(_slot(mine, slot)["image"]) == THIRD_PNG
        app("media.place", subject=campaign, slot=slot, mode="hide", expected=_own(mine, slot)["etag"])
        assert app("media.chat", chat=storm)["backdrop"]["state"] == "hidden"
        # a backdrop names a chat that exists; a chat that does not is not found
        assert app.refused("media.upload", scope=campaign, media=_media(PNG), subject=campaign,
                           slot="backdrop.no-such-chat", expected=None).reason == "missing_chat"
        assert app.refused("media.chat", chat=campaign + "#chat/no-such-chat").status == 404
        assert app.refused("media.chat", chat="nope").reason == "invalid_field"
        assert app.refused("media.backdrop_from", chat=storm, image="nope", expected=None).reason == "invalid_field"


# -- pictures on posts -------------------------------------------------------------------------------------

def test_a_picture_on_a_post_sits_beside_the_transcript_and_reaches_no_model(desk):
    folder, chat = desk
    campaign = chat.split("#", 1)[0]
    with Device(folder, [say("The harbor is quiet tonight.")] * 4) as device:
        app = App(device)
        app("continue", chat=chat)
        app("post", chat=chat, text="I light the lamp.")
        app("generate", chat=chat)
        scene = keep(device, chat)
        post = next(turn for turn in scene["turns"] if turn["post"] is not None)
        opening = next(turn for turn in scene["turns"] if turn["post"] is None)  # the greeting: nobody's post
        slot = "post." + post["turn"]
        turn_file = folder.joinpath(*turn_parts(TurnRef(ChatRef.parse(chat).campaign_id, post["turn"])))
        before, prompts = turn_file.read_bytes(), list(device.prompts())
        opened = app("open", chat=chat)
        art = app.put(campaign, slot, note="A lamp in fog")
        image = _slot(art, slot)["image"]
        (shown,) = app("media.chat", chat=chat)["posts"]
        assert (shown["turn"], shown["state"], shown["image"]) == (post["turn"], "shown", image)
        # the turn, the scene the table reads and every other canonical record of play are untouched
        assert turn_file.read_bytes() == before and app("open", chat=chat) == opened
        # and no model is called for it, nor ever shown it: the next prompt names no picture
        assert device.prompts() == prompts
        app("post", chat=chat, text="I wait.")
        app("generate", chat=chat)
        later = [prompt for prompt in device.prompts() if prompt not in prompts]
        assert later and not any(needle in prompt for prompt in later
                                 for needle in (image, "A lamp in fog", "#image/", slot, "post."))
        # only a post on the chat's line may be illustrated
        assert app.refused("media.upload", scope=campaign, media=_media(PNG), subject=campaign,
                           slot="post." + opening["turn"], expected=None).reason == "not_a_post"
        for turn in (ChatRef.parse(chat).chat_id + ".999", "elsewhere.1", "x"):
            assert app.refused("media.upload", scope=campaign, media=_media(PNG), subject=campaign,
                               slot="post." + turn, expected=None).reason == "missing_turn"
        gallery = [item["image"] for item in app("media.gallery", scope=campaign)["images"]]
        assert gallery == [image]  # a refused attachment left no picture behind
        # the turn gone (deleted by hand, or never synced): the picture is an orphan, listed and not shown
        turn_file.unlink()
        seen = app("media.chat", chat=chat)
        assert seen["posts"] == [] and [(item["turn"], item["image"], item["why"]) for item in seen["orphans"]] == \
            [(post["turn"], image, "turn_gone")]
        app("media.place", subject=campaign, slot=slot, mode="hide", expected=seen["orphans"][0]["etag"])
        assert app("media.chat", chat=chat)["orphans"] == []


def test_a_picture_on_a_post_a_fork_choice_set_aside_is_an_orphan_listed_and_not_shown(synced):
    folder, phone, chat = synced
    campaign = chat.split("#", 1)[0]

    def play(app: App, text: str, data: bytes) -> str:
        app("continue", chat=chat)
        app("post", chat=chat, text=text)
        app("generate", chat=chat)
        turn = keep(app.device, chat)["turns"][-1]["turn"]
        app.put(campaign, "post." + turn, data)
        return turn
    with Device(folder, [say("Desk answer.")]) as desktop_device:
        mine = play(App(desktop_device), "From the desk.", PNG)
        with serving(desktop_device) as server, \
                Device(phone, [say("Phone answer.")], peers={DESKTOP: server.address}) as mobile_device:
            mobile = App(mobile_device)
            theirs = play(mobile, "From the phone.", OTHER_PNG)
            assert mine != theirs  # each device's turn has its own id, so the two pictures never share a slot
            assert mobile("media.sync", peer=DESKTOP)["status"] == "synced"
            (fork,) = mobile("media.forks")["forks"]
            (chosen,) = [branch for branch in fork["fork"]["branches"] if theirs in branch["turns"]]
            mobile("media.choose", chat=chat, turn=chosen["head"])
            seen = mobile("media.chat", chat=chat)
            assert [item["turn"] for item in seen["posts"]] == [theirs]
            assert [(item["turn"], item["why"]) for item in seen["orphans"]] == [(mine, "set_aside")]
            assert mobile.image(seen["orphans"][0]["image"]) == PNG  # kept and listed, like the turn itself
            # and nothing new may be put on a turn that is off the line
            assert mobile.refused("media.upload", scope=campaign, media=_media(THIRD_PNG), subject=campaign,
                                  slot="post." + mine, expected=seen["orphans"][0]["etag"]).reason == "missing_turn"


# -- two devices -------------------------------------------------------------------------------------------

def test_covers_entry_pictures_a_backdrop_and_a_post_picture_arrive_on_the_other_device(synced):
    folder, phone, chat = synced
    campaign, chat_id = chat.split("#", 1)[0], ChatRef.parse(chat).chat_id
    with Device(folder, [say("Heard.")] * 2) as desktop_device:
        desktop = App(desktop_device)
        desktop("continue", chat=chat)
        desktop("post", chat=chat, text="I light the lamp.")
        post = desktop("open", chat=chat)["turns"][-1]["turn"]
        entry = desktop("media.entries", subject=LORE)["entries"][0]["slot"]
        placed = {(LORE, "cover"): PNG, (WORLD, "cover"): OTHER_PNG, (campaign, "cover"): THIRD_PNG,
                  (LORE, entry): OTHER_PNG, (campaign, "backdrop." + chat_id): PNG,
                  (campaign, "post." + post): OTHER_PNG}
        for (subject, slot), data in placed.items():
            desktop.put(subject, slot, data)
        with serving(desktop_device) as server, Device(phone, peers={DESKTOP: server.address}) as mobile_device:
            mobile = App(mobile_device)
            report = mobile("media.sync", peer=DESKTOP)
            assert report["status"] == "synced" and not report["conflicts"], report
            for (subject, slot), data in placed.items():
                shown = _slot(mobile("media.art", subject=subject), slot)
                assert shown["state"] == "shown" and mobile.image(shown["image"]) == data, (subject, slot)
            seen = mobile("media.chat", chat=chat)
            assert seen["backdrop"]["state"] == "shown" and [item["turn"] for item in seen["posts"]] == [post]
            assert mobile("media.entries", subject=LORE)["entries"][0]["picture"]["state"] == "shown"
            # a cover removed on one device is removed on the other: a hide is a record like any other
            for subject in (LORE, WORLD, campaign):
                own = _own(desktop("media.art", subject=subject), "cover")
                desktop("media.place", subject=subject, slot="cover", mode="hide", expected=own["etag"])
            assert mobile("media.sync", peer=DESKTOP)["status"] == "synced"
            assert [mobile.cover(subject)["state"] for subject in (LORE, WORLD, campaign)] == ["hidden"] * 3
            assert mobile_device.api.store.load().problems == ()


def test_two_devices_covers_for_one_campaign_are_a_conflict_the_owner_settles_never_an_overwrite(synced):
    folder, phone, chat = synced
    campaign = chat.split("#", 1)[0]
    path = "/".join(address_parts(PlacementRef(OwnerRef(campaign), "cover")))
    with Device(folder) as desktop_device:
        desktop = App(desktop_device)
        desktop.put(campaign, "cover", PNG)
        with serving(desktop_device) as server, Device(phone, peers={DESKTOP: server.address}) as mobile_device:
            mobile = App(mobile_device)
            mobile.put(campaign, "cover", OTHER_PNG)
            report = mobile("media.sync", peer=DESKTOP)
            assert [item["path"] for item in report["conflicts"]] == [path], report
            # each device still shows its own cover: neither was overwritten
            assert mobile.image(mobile.cover(campaign)["image"]) == OTHER_PNG
            assert desktop.image(desktop.cover(campaign)["image"]) == PNG
            (conflict,) = mobile("media.conflicts")["conflicts"]
            assert (conflict["kind"], conflict["subject"], conflict["slot"]) == ("placement", campaign, "cover")
            (theirs,) = conflict["theirs"]
            held = {mobile.image(side["preview"]["placement"]["image"]) for side in (conflict["mine"], theirs)}
            assert held == {PNG, OTHER_PNG}
            # the slot says it is in conflict, and a third version is refused until the owner chooses
            art = mobile("media.art", subject=campaign)
            assert art["conflicts"] == ["cover"]
            blocked = mobile.refused("media.upload", scope=campaign, media=_media(THIRD_PNG), subject=campaign,
                                     slot="cover", expected=_own(art, "cover")["etag"])
            assert blocked.reason == "art_conflict"
            (waiting,) = [item for item in mobile("inbox.list")["items"] if item["kind"] == "conflict"]
            assert "cover" in waiting["title"]
            done = mobile("media.resolve", peer=DESKTOP, path=path, keep=theirs["etag"],
                          mine=conflict["mine"]["etag"], theirs=[theirs["etag"]])
            assert done["status"] == "resolved", done
            assert [mobile.image(item["preview"]["placement"]["image"]) for item in done["not_kept"]] == [OTHER_PNG]
            assert mobile.image(mobile.cover(campaign)["image"]) == PNG
            assert mobile("media.art", subject=campaign)["conflicts"] == []


def test_two_devices_covers_for_one_entry_are_kept_apart_and_reported(synced):
    """A definition's and a source's placements are a source's files: their conflict is reported by Sync and
    waits there (docs/MEDIA_SYNC_UI.md), and neither device's cover is replaced."""
    folder, phone, _chat = synced
    with Device(folder) as desktop_device:
        desktop = App(desktop_device)
        desktop.put(LORE, "cover", PNG)
        desktop.put(WORLD, "cover", PNG)
        with serving(desktop_device) as server, Device(phone, peers={DESKTOP: server.address}) as mobile_device:
            mobile = App(mobile_device)
            mobile.put(LORE, "cover", OTHER_PNG)
            mobile.put(WORLD, "cover", OTHER_PNG)
            report = mobile("media.sync", peer=DESKTOP)
            paths = {"/".join(address_parts(PlacementRef(subject, "cover")))
                     for subject in (OwnerRef(WORLD), PlacementRef.parse(LORE + "#placement/cover").subject)}
            assert {item["path"] for item in report["conflicts"]} == paths, report
            for subject in (LORE, WORLD):
                assert mobile.image(mobile.cover(subject)["image"]) == OTHER_PNG
                assert desktop.image(desktop.cover(subject)["image"]) == PNG
            again = mobile("media.sync", peer=DESKTOP)  # and it stays reported, never settled by a second sync
            assert {item["path"] for item in again["conflicts"]} == paths
