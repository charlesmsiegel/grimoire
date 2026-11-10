"""111: the app's chat launch and scene-play ops, over 052's Start Chat and 035's runner.

Synthetic cards imported through 037's real importer (``card_fixtures``); every model call is a scripted
fake (``scene_fixtures.say``). Each test starts from a fresh copy of one built library.
"""

from __future__ import annotations

import json
import shutil

import pytest
from card_fixtures import SCENARIO, encode, land
from scene_fixtures import ANYA, ANYA_CARD, MARA_LORE, MARA_VELL, STORM, TAMSIN, TAMSIN_CARD, VOICED, say, secure
from textapi_fixtures import Device

from grimoire.store import LibraryStore
from grimoire.textapi import ApiRefused

PREMISE = "A lighthouse keeper finds a letter addressed to {{user}}, dated tomorrow."


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    folder = tmp_path_factory.mktemp("built") / "cards"
    with LibraryStore.create(folder) as store:
        for document in (VOICED, ANYA_CARD, TAMSIN_CARD, SCENARIO):
            land(store, encode(document))
    secure(folder)
    return folder


@pytest.fixture
def folder(built, tmp_path):
    copy = tmp_path / "cards"
    shutil.copytree(built, copy)
    return copy


def refused(device: Device, op: str, **args) -> ApiRefused:
    with pytest.raises(ApiRefused) as caught:
        device(op, **args)
    return caught.value


def actor_named(scene: dict, name: str) -> str:
    (actor,) = [member["actor"] for member in scene["cast"] if member["name"] == name]
    return actor


def kept_lines(scene: dict) -> list[tuple[str, str]]:
    return [(line["speaker"], line["text"]) for turn in scene["turns"] if turn["kept"]
            for line in turn["kept"]["lines"]]


def keep_newest(device: Device, scene: dict) -> dict:
    turn = scene["state"]["open_turn"]
    take = max(scene["state"]["keepable"])
    return device("keep", chat=scene["chat"], turn=turn, take=take)


# -- the picker and the options ----------------------------------------------------------------------------

def test_the_picker_lists_bindable_characters_scenarios_and_lorebooks_by_head(folder):
    with Device(folder) as device:
        found = device("start_sources")
    assert [item["name"] for item in found["characters"]] == ["Anya Rook", "Mara Vell", "Tamsin Hale"]
    assert {item["ref"] for item in found["characters"]} == {str(ANYA), str(MARA_VELL), str(TAMSIN)}
    assert [item["ref"] for item in found["scenarios"]] == [str(STORM)]
    lorebooks = [item["ref"] for item in found["lorebooks"]]
    assert str(MARA_LORE) in lorebooks and len(lorebooks) == 2  # the card's and the scenario's own
    assert all(item["revision"] == "r1" for kind in found.values() for item in kind)


def test_options_offer_the_greetings_and_the_chats_to_continue(folder):
    with Device(folder) as device:
        options = device("start_options", kind="character", ref=str(MARA_VELL))
        assert [(o["id"], o["group_only"]) for o in options["openings"]] == [
            ("first", False), ("alt-1", False), ("group-1", True)]
        assert options["default_opening"] == "first" and options["related"] == []
        assert options["sources"] == [str(MARA_VELL.at("r1"))] and options["lorebook"] == str(MARA_LORE.at("r1"))
        started = device("start", kind="character", ref=str(MARA_VELL), persona=str(ANYA), opening="alt-1")
        related = device("start_options", kind="character", ref=str(MARA_VELL))["related"]
        assert [entry["chat"] for entry in related] == [started["chat"]]
        assert related[0]["launch"]["name"] == "Mara Vell"
        # a persona's chat is not this character's: Anya plays, so Anya's own options list none
        assert device("start_options", kind="character", ref=str(ANYA))["related"] == []


# -- the proving scenario ------------------------------------------------------------------------------------

def test_a_worldless_chat_plays_takes_a_second_card_and_reopens_from_every_entrypoint(folder):
    steps = [say("Safe as the tide."), say("Papers, please.")]
    with Device(folder, steps) as device:
        scene = device("start", kind="character", ref=str(MARA_VELL), persona=str(ANYA))
        chat = scene["chat"]
        assert scene["unfinished"] is None and scene["scope"]["world"] is None  # no world, no wizard
        assert kept_lines(scene) == [("Mara Vell", "You came back. I didn't think you would.")]
        assert device.prompts() == []  # the greeting is the card's text, not a generation
        assert scene["persona"]["name"] == "Anya Rook" and scene["state"]["seated"]
        device("post", chat=chat, text='"Is the cargo safe?"', mode="speech")
        scene = device("generate", chat=chat, speakers=[actor_named(scene, "Mara Vell")])
        scene = keep_newest(device, scene)
        assert kept_lines(scene)[-1] == ("Mara Vell", "Safe as the tide.")

        scene = device("add_card", chat=chat, ref=str(TAMSIN))
        tamsin = actor_named(scene, "Tamsin Hale")
        assert {member["role"] for member in scene["cast"] if member["actor"] == tamsin} == {"npc"}
        device("post", chat=chat, text='"Who keeps the customs house?"')
        scene = device("generate", chat=chat, speakers=[tamsin])  # one separate call for the newcomer
        prompt = device.prompts()[-1]
        assert "Who keeps the customs house?" in prompt
        assert "Is the cargo safe?" not in prompt and "Safe as the tide." not in prompt  # never heard before
        keep_newest(device, scene)

    with Device(folder) as device:  # a restart: the same canonical chat from three library entrypoints
        recent = [entry["chat"] for entry in device("chats")["chats"]]
        by_mara = [entry["chat"] for entry in device("chats", filter="character", source=str(MARA_VELL))["chats"]]
        by_tamsin = [entry["chat"] for entry in device("chats", filter="character", source=str(TAMSIN))["chats"]]
        assert recent == by_mara == by_tamsin == [chat]
        related = device("start_options", kind="character", ref=str(MARA_VELL))["related"]
        assert [entry["chat"] for entry in related] == [chat]
        scene = device("continue", chat=chat)
        assert scene["unfinished"] is None
        assert kept_lines(scene)[-1] == ("Tamsin Hale", "Papers, please.")
        device("post", chat=chat, text='"Thank you."')


def test_a_premise_or_scenario_chat_is_answered_by_the_director_not_a_made_up_npc(folder):
    with Device(folder, [say("Thunder answers.")]) as device:
        scene = device("start", kind="premise", premise=PREMISE, persona=str(ANYA), label="The letter")
        assert scene["label"] == "The letter" and scene["launch"]["premise"] == PREMISE
        assert [member["name"] for member in scene["cast"]] == ["Anya Rook"]
        device("post", chat=scene["chat"], text='"Who sent it?"')
        assert refused(device, "generate", chat=scene["chat"]).reason == "nothing_to_generate"
        scene = device("generate", chat=scene["chat"], director=True)
        assert scene["turns"][-1]["offered"][0]["lines"][0]["text"] == "Thunder answers."
        storm = device("start", kind="scenario", ref=str(STORM), persona=str(ANYA), opening="opening-2")
        assert kept_lines(storm) == [("Narrator", "The lamps gutter; someone screams upstairs.")]


def test_a_bundle_binds_the_cards_own_lorebook(folder):
    with Device(folder) as device:
        scene = device("start", kind="bundle", ref=str(MARA_VELL), persona=str(ANYA))
        by_lore = device("chats", filter="character", source=str(MARA_VELL))["chats"]
        (entry,) = by_lore
        assert {(s["role"], s["ref"]) for s in entry["sources"]} >= {("lorebook", str(MARA_LORE))}
        assert entry["chat"] == scene["chat"]


# -- line requests and corrections ---------------------------------------------------------------------------

def test_skip_redirect_and_strike_name_the_lines_on_screen(folder):
    steps = [say("Safe as the tide."), say("Gone with the tide.")]
    with Device(folder, steps) as device:
        scene = device("start", kind="character", ref=str(MARA_VELL), persona=str(ANYA))
        chat = scene["chat"]
        device("post", chat=chat, text='"Is the cargo safe?"')
        scene = device("generate", chat=chat)
        line = scene["turns"][-1]["offered"][0]["lines"][0]["line"]
        scene = device("withdraw", chat=chat, kind="redirect", lines=[line], direction="kinder")
        assert scene["state"]["keepable"] == []  # its only line was withdrawn: nothing to keep
        assert all(not take["lines"] for take in scene["turns"][-1]["offered"])  # nobody hears it now
        scene = device("generate", chat=chat)
        scene = keep_newest(device, scene)
        assert kept_lines(scene)[-1] == ("Mara Vell", "Gone with the tide.")
        kept = scene["turns"][-1]["kept"]["lines"][0]["line"]
        scene = device("strike", chat=chat, line=kept)
        assert ("Mara Vell", "Gone with the tide.") not in kept_lines(scene)
        assert refused(device, "strike", chat=chat, line=kept).reason == "not_kept"


def test_a_typed_skip_in_the_composer_is_the_same_request(folder):
    with Device(folder, [say("Safe as the tide.")]) as device:
        scene = device("start", kind="character", ref=str(MARA_VELL), persona=str(ANYA))
        chat = scene["chat"]
        device("post", chat=chat, text='"Is the cargo safe?"')
        device("generate", chat=chat)
        scene = device("post", chat=chat, text="Grimoire, skip")
        assert scene["state"]["keepable"] == []


# -- a source gone --------------------------------------------------------------------------------------------

def test_a_missing_card_is_reported_and_detached_and_the_chat_plays_on(folder):
    with Device(folder) as device:
        chat = device("start", kind="bundle", ref=str(MARA_VELL), persona=str(ANYA))["chat"]
        assert device("repairs", chat=chat)["repairs"] == []
    shutil.rmtree(folder / "sources" / "card" / "mara-vell")
    with Device(folder) as device:
        repairs = device("repairs", chat=chat)["repairs"]
        assert {(r["field"], r["choices"] == ["detach"]) for r in repairs} == {
            ("launch", False), ("bindings.character", True), ("bindings.lorebook", True)}
        assert str(MARA_VELL) not in {item["ref"] for item in device("start_sources")["characters"]}
        lore = next(r for r in repairs if r["role"] == "lorebook")
        scene = device("detach", chat=chat, field=lore["field"], ref=lore["ref"])
        assert {r["field"] for r in device("repairs", chat=chat)["repairs"]} == {"launch", "bindings.character"}
        assert refused(device, "detach", chat=chat, field=lore["field"], ref=lore["ref"]).reason == "not_broken"
        launch = next(r for r in repairs if r["field"] == "launch")
        assert refused(device, "detach", chat=chat, field="launch", ref=launch["ref"]).reason == "invalid_field"
        device("post", chat=scene["chat"], text='"Mara?"')


# -- refusals write nothing -------------------------------------------------------------------------------------

def _tree(folder) -> dict[str, bytes]:
    return {str(path.relative_to(folder)): path.read_bytes() for path in sorted(folder.rglob("*"))
            if path.is_file() and "authority" not in path.parts and path.suffix != ".lock"}


@pytest.mark.parametrize("args,reason", [
    ({"kind": "character", "ref": str(MARA_VELL)}, "no_persona"),
    ({"kind": "lorebook", "ref": str(MARA_LORE), "persona": str(ANYA)}, "invalid_selection"),
    ({"kind": "character", "ref": str(STORM), "persona": str(ANYA)}, "invalid_field"),
    ({"kind": "character", "ref": "not a ref", "persona": str(ANYA)}, "invalid_field"),
    ({"kind": "premise", "premise": "   ", "persona": str(ANYA)}, "invalid_selection"),
    ({"kind": "premise", "premise": PREMISE, "ref": str(MARA_VELL), "persona": str(ANYA)}, "invalid_selection"),
    ({"kind": "scenario", "ref": str(STORM), "lorebook": str(MARA_LORE), "persona": str(ANYA)}, "invalid_selection"),
    ({"kind": "character", "ref": str(MARA_VELL), "persona": str(STORM)}, "invalid_field"),
    ({"kind": "character", "ref": str(MARA_VELL), "persona": str(ANYA), "opening": "nope"}, "unknown_opening"),
    ({"kind": "character", "ref": "card:nobody/character/nobody", "persona": str(ANYA)}, "missing_source"),
    ({"kind": "character", "ref": str(MARA_VELL), "persona": str(ANYA), "label": "x" * 201}, "invalid_field"),
])
def test_a_refused_start_writes_nothing(folder, args, reason):
    before = _tree(folder)
    with Device(folder) as device:
        error = refused(device, "start", **args)
        assert error.reason == reason, error
        assert device("chats")["chats"] == []
    assert _tree(folder) == before


def test_malformed_play_requests_are_refused_before_anything_is_written(folder):
    with Device(folder, [say("Safe as the tide.")]) as device:
        scene = device("start", kind="character", ref=str(MARA_VELL), persona=str(ANYA))
        chat = scene["chat"]
        device("post", chat=chat, text='"Is the cargo safe?"')
        scene = device("generate", chat=chat)
        line = scene["turns"][-1]["offered"][0]["lines"][0]["line"]
        before = json.dumps(device("open", chat=chat), sort_keys=True)
        cases = [
            ("withdraw", {"chat": chat, "kind": "pause", "lines": [line]}, "invalid_field"),
            ("withdraw", {"chat": chat, "kind": "skip", "lines": []}, "invalid_field"),
            ("withdraw", {"chat": chat, "kind": "skip", "lines": [line], "direction": "x"}, "invalid_field"),
            ("withdraw", {"chat": chat, "kind": "skip", "lines": [7]}, "invalid_field"),
            ("withdraw", {"chat": chat, "kind": "skip", "lines": ["nope"]}, "unknown_line"),
            ("strike", {"chat": chat, "line": line}, "not_kept"),
            ("strike", {"chat": chat}, "invalid_field"),
            ("add_card", {"chat": chat, "ref": str(TAMSIN), "role": "persona"}, "invalid_field"),
            ("add_card", {"chat": chat, "ref": str(STORM)}, "invalid_field"),
            ("post", {"chat": chat, "text": "hi", "mode": "director"}, "invalid_field"),
            ("repairs", {"chat": "campaign:none#chat/none"}, "no_chat"),
        ]
        for op, args, reason in cases:
            assert refused(device, op, **args).reason == reason, (op, args)
        assert json.dumps(device("open", chat=chat), sort_keys=True) == before


def test_the_library_says_which_composer_modes_and_ops_exist(folder):
    with Device(folder, runtime=False) as device:
        library = device("library")
        assert library["post_modes"] == ["auto", "speech", "action"] and library["generation"] is False
        assert {"start", "start_options", "start_sources", "add_card", "withdraw", "strike", "repairs",
                "detach"} <= set(library["ops"])
