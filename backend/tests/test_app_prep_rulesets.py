"""Sprint 115's ruleset action contracts (062) as the Prep page's ``prep.rulesets`` and ``prep.ruleset_check``.

The four reference profiles are shown as 062 defines them; a proposed action and its recorded result are checked by
062's own constructors and ``propose``. Nothing is rolled, resolved or written: every answer says so, and the
library's files are unchanged. The table proposes only for its own character. Synthetic library; no model is called.
"""

from __future__ import annotations

import pytest
from prep_app_fixtures import DIRECTOR, cast_of
from store_helpers import OWNER
from textapi_fixtures import Device, desktop_library, keep, say

from grimoire.app.adapters.prep.rulesets import CONTRACTS
from grimoire.app.appapi import AppApi
from grimoire.rulesets.model import PROFILES
from grimoire.testing.fileops import snapshot as files_of
from grimoire.textapi import ApiRefused


@pytest.fixture
def desk(tmp_path):
    return desktop_library(tmp_path / "desk")


def refused(app: AppApi, op: str, **args) -> ApiRefused:
    with pytest.raises(ApiRefused) as caught:
        app.call(op, args)
    return caught.value


def seated(device, chat) -> dict[str, str]:
    device.api.runner.seat(chat, OWNER)
    device("post", chat=str(chat), text="*I press a coin into Mara's hand*")
    device("generate", chat=str(chat))
    keep(device, str(chat))
    return cast_of(device, chat)


def test_the_contracts_are_public_and_name_what_they_are_not(desk):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        cast = seated(device, chat)
        app = AppApi(device.api)
        table = app.call("prep.rulesets", {"chat": str(chat)})
        director = app.call("prep.rulesets", {"chat": str(chat), **DIRECTOR})
        assert table["profiles"] == director["profiles"]
        assert [(item["family"], item["edition"]) for item in table["profiles"]] == list(PROFILES.items())
        assert table["profiles"][0]["module"] == "reference:062/ruleset/d20@5e-2024"
        assert table["pinned"] is None and "No campaign or chat selects a ruleset" in table["not_pinned"]
        assert "no dice are rolled" in table["noncanonical"]
        # the table proposes only for the character it plays; the director for anyone with a controller
        assert [item["actor"] for item in table["actors"]] == [cast["Anya Rook"]]
        assert table["actors"][0]["yours"] is True and table["actors"][0]["role"] == "player"
        assert {item["actor"]: item["role"] for item in director["actors"]} == {
            cast["Anya Rook"]: "player", cast["Mara Vell"]: "npc"}


def test_each_documented_example_is_a_proposal_and_nothing_is_kept(desk):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        cast = seated(device, chat)
        app = AppApi(device.api)
        before = files_of(device.table.store.root)
        for family, contract in CONTRACTS.items():
            answer = app.call("prep.ruleset_check", {"chat": str(chat), "family": family, "actor": cast["Anya Rook"],
                                                     **contract["example"]})
            assert (answer["valid"], answer["canonical"], answer["edition"]) == (True, False, PROFILES[family])
            assert answer["action"] == contract["example"]["action"]
            assert {key: value for key, value in answer["result"].items() if value is not None} == contract["example"]["result"]
            assert "no dice are rolled" in answer["noncanonical"] and answer["request"] == "check"
        # the director may propose for the NPC too, under an id of its own
        npc = app.call("prep.ruleset_check", {"chat": str(chat), **DIRECTOR, "family": "d20", "request_id": "mara-1",
                                              "actor": cast["Mara Vell"], **CONTRACTS["d20"]["example"]})
        assert npc["request"] == "mara-1" and npc["actor"] == cast["Mara Vell"]
        assert files_of(device.table.store.root) == before


def test_062_refuses_what_does_not_fit_with_its_own_words(desk):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        cast = seated(device, chat)
        app = AppApi(device.api)
        base = {"chat": str(chat), "actor": cast["Anya Rook"]}
        d20 = CONTRACTS["d20"]["example"]
        # a die no d20 shows, a total that does not add up, success that is not the DC's verdict
        for result in ({**d20["result"], "die": 25}, {**d20["result"], "total": 16}, {**d20["result"], "success": False}):
            caught = refused(app, "prep.ruleset_check", **base, family="d20", action=d20["action"], result=result)
            assert caught.reason == "invalid_proposal" and str(caught)
        # a partial Dungeon World result keeps a choice open, and only a partial one does
        pbta = CONTRACTS["pbta"]["example"]
        assert refused(app, "prep.ruleset_check", **base, family="pbta", action=pbta["action"],
                       result={**pbta["result"], "choice": None}).reason == "invalid_proposal"
        # wrong types are 062's refusal too, never a server error
        assert refused(app, "prep.ruleset_check", **base, family="d20", action={**d20["action"], "dc": "fifteen"},
                       result=d20["result"]).reason == "invalid_proposal"
        assert refused(app, "prep.ruleset_check", **base, family="wod", action=CONTRACTS["wod"]["example"]["action"],
                       result={**CONTRACTS["wod"]["example"]["result"], "normal": "10, 6"}).reason == "invalid_proposal"


def test_a_check_is_refused_for_anyone_this_view_does_not_propose_for(desk):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        cast = seated(device, chat)
        app = AppApi(device.api)
        example = CONTRACTS["d20"]["example"]
        base = {"chat": str(chat), "family": "d20", **example}
        assert refused(app, "prep.ruleset_check", **base, actor=cast["Mara Vell"]).reason == "not_your_character"
        assert refused(app, "prep.ruleset_check", **base, actor=cast["Mara Vell"], audience="table").reason == "not_your_character"
        stranger = f"campaign:{chat.campaign_id}/character/stranger"
        assert refused(app, "prep.ruleset_check", **base, **DIRECTOR, actor=stranger).reason == "no_actor"
        assert refused(app, "prep.ruleset_check", **base, actor=None).reason == "not_your_character"


def test_malformed_requests_are_refused_by_field(desk):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        cast = seated(device, chat)
        app = AppApi(device.api)
        example = CONTRACTS["d20"]["example"]
        base = {"chat": str(chat), "actor": cast["Anya Rook"], "family": "d20", **example}
        for changes in ({"family": "gurps"}, {"action": {**example["action"], "luck": 1}},
                        {"action": {"ability": "dexterity"}}, {"result": [12, 15, True]}, {"request_id": "Check 1"},
                        {"audience": "everyone"}, {"chat": "nowhere"}):
            assert refused(app, "prep.ruleset_check", **{**base, **changes}).reason == "invalid_field", changes
        assert refused(app, "prep.rulesets", chat="nowhere").reason == "invalid_field"
