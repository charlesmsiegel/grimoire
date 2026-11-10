"""Sprint 113's campaign page and its ``campaign.*`` adapter ops, through 108's shell contract.

The adapter serves ``grimoire.campaignapi.CampaignApi`` over the app's open library, as the owner with the
app's own trusted handle; ``campaign.chats`` lists the library's chats by campaign. The proving scenario runs
through the app as a screen would: the card chat's seated player keeps a turn, the director proposes its
effect in structured form, the player endorses exactly what was shown and the director's key accepts it once,
each in its own call, and only the director's view then shows it as world truth. The page itself runs end to
end under Node (``js/campaign.e2e.mjs``) where Node is installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from store_helpers import OWNER
from textapi_fixtures import Device, desktop_library, keep, say

from grimoire.app import AppServer
from grimoire.app.appapi import AppApi
from grimoire.app.pages import load_pages
from grimoire.campaignapi import CampaignApi
from grimoire.textapi import ApiRefused

STATIC = Path(__file__).resolve().parents[1] / "src" / "grimoire" / "app" / "static"
NODE = shutil.which("node")


@pytest.fixture
def desk(tmp_path):
    return desktop_library(tmp_path / "desk")


def refused(app: AppApi, op: str, **args) -> ApiRefused:
    with pytest.raises(ApiRefused) as caught:
        app.call(op, args)
    return caught.value


def kept_turn(device: Device, chat) -> str:
    """The owner seated as the card chat's persona (Anya), with one kept turn of theirs: the turn's id."""
    device.api.runner.seat(chat, OWNER)
    device("post", chat=str(chat), text="*I press a coin into Mara's hand*")
    device("generate", chat=str(chat))
    return keep(device, str(chat))["turns"][-1]["turn"]


def cast_of(hub: dict) -> dict[str, str]:
    return {member["name"]: member["actor"] for member in hub["cast"]}


def test_the_page_registers_on_the_campaign_route_and_needs_the_hub():
    (page,) = [page for page in load_pages(STATIC) if page.id == "campaign"]
    assert (page.route, page.slot, page.owner, page.entry) == ("campaign", "campaign", "113", "page.js")
    assert set(page.requires) == {"campaign.chats", "campaign.hub"}
    assert page.unavailable is None


def test_the_app_serves_every_hub_op_and_lists_chats_by_campaign(desk):
    folder, card_chat = desk
    with Device(folder) as device:
        app = AppApi(device.api)
        assert {"campaign.chats", "campaign.hub", "campaign.knowledge", "campaign.label", "campaign.confirm",
                "campaign.decide", "campaign.propose_effect", "campaign.endorse_effect", "campaign.approve_effect",
                "campaign.recover_effects"} <= set(app.ops)
        listed = app.call("campaign.chats", {})
        assert listed["complete"] is True
        by_campaign = {item["campaign"]: [chat["chat"] for chat in item["chats"]] for item in listed["campaigns"]}
        assert by_campaign[card_chat.campaign_id] == [str(card_chat)]
        assert len(by_campaign) == 2  # the card chat's campaign and the imported V1 campaign
        one = app.call("campaign.chats", {"campaign": card_chat.campaign_id})
        assert [item["campaign"] for item in one["campaigns"]] == [card_chat.campaign_id]
        assert app.call("campaign.chats", {"campaign": "no-such-campaign"})["campaigns"] == []
        assert refused(app, "campaign.chats", campaign="../escape").reason == "invalid_field"
        assert refused(app, "campaign.chats", campaign=7).reason == "invalid_field"


def test_an_effect_is_accepted_through_the_app_with_two_keys_in_two_calls(desk):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        app = AppApi(device.api)
        turn = kept_turn(device, chat)
        table = app.call("campaign.hub", {"chat": str(chat)})
        names = cast_of(table)
        anya, mara = names["Anya Rook"], names["Mara Vell"]
        assert [(member["role"], member["controller"]) for member in table["cast"] if member["actor"] == anya] == \
            [("player", OWNER)]
        assert "truth" not in table and "goals" not in table  # the director's alone
        op = {"kind": "begin", "predicate": "knows", "subject": mara, "object": anya, "agent": anya, "backed_by": turn}

        proposed = app.call("campaign.propose_effect", {"chat": str(chat), "audience": "director", "turn": turn,
                                                        "summary": "Anya pays Mara a coin.", "operations": [op]})
        effect = proposed["done"]["effect"]
        assert effect["state"] == "proposed" and effect["proposed_by"] == "director"
        assert effect["endorsers"] == [{"contribution": turn, "principal": OWNER, "given": False}]
        assert proposed["hub"]["audience"] == "director"  # the hub as it stands after the write, as asked

        stale = refused(app, "campaign.endorse_effect", chat=str(chat), turn=turn, number=1, fingerprint="0" * 64)
        assert stale.reason == "fingerprint_mismatch"
        endorsed = app.call("campaign.endorse_effect", {"chat": str(chat), "turn": turn, "number": 1,
                                                        "fingerprint": effect["fingerprint"]})
        assert endorsed["done"]["effect"]["state"] != "accepted"  # the player's key alone accepts nothing
        (shown,) = [item for record in endorsed["hub"]["turns"] for item in record["effects"]]
        assert shown["endorsers"][0]["given"] is True  # the table sees the effect it holds a key on

        approved = app.call("campaign.approve_effect", {"chat": str(chat), "audience": "director", "turn": turn,
                                                        "number": 1})["done"]["effect"]
        assert approved["state"] == "accepted" and approved["event"]
        assert refused(app, "campaign.approve_effect", chat=str(chat), turn=turn, number=1).reason == "already_accepted"

        director = app.call("campaign.hub", {"chat": str(chat), "audience": "director"})
        (event,) = director["truth"]["events"]
        assert (event["event"], event["summary"]) == (approved["event"], "Anya pays Mara a coin.")
        assert "truth" not in app.call("campaign.hub", {"chat": str(chat)})


def test_the_app_names_what_it_cannot_do_and_a_refusal_writes_nothing(desk):
    folder, chat = desk
    with Device(folder) as device:
        app = AppApi(device.api)
        hub = app.call("campaign.hub", {"chat": str(chat)})
        (label,) = [item for item in hub["actions"] if item["op"] == "label"]
        assert label["available"] is False  # the app passes the hub no labelling model
        assert refused(app, "campaign.label", chat=str(chat)).reason == "no_labeller"
        assert refused(app, "campaign.hub", chat=str(chat), audience="everyone").reason == "invalid_field"
        assert refused(app, "campaign.decide", chat=str(chat), result="success").reason == "not_an_action"
        features = {item["feature"] for item in hub["unavailable"]}
        assert {"revoke_effect", "ability_use", "proposed_by_narration"} <= features


def test_a_refused_proposal_writes_nothing(desk):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        app = AppApi(device.api)
        turn = kept_turn(device, chat)
        names = cast_of(app.call("campaign.hub", {"chat": str(chat)}))
        before = app.call("campaign.hub", {"chat": str(chat), "audience": "director"})
        # a player character's act with no backing post: only the player authors their character's act (021)
        unbacked = {"kind": "begin", "predicate": "knows", "subject": names["Mara Vell"], "object": names["Anya Rook"],
                    "agent": names["Anya Rook"]}
        assert refused(app, "campaign.propose_effect", chat=str(chat), turn=turn, summary="Anya pays Mara a coin.",
                       operations=[unbacked]).reason == "pc_authorship"
        assert refused(app, "campaign.propose_effect", chat=str(chat), turn=turn, summary="  ",
                       operations=[unbacked]).reason == "invalid_field"
        after = app.call("campaign.hub", {"chat": str(chat), "audience": "director"})
        assert after["turns"] == before["turns"] and after["truth"] == before["truth"]
        assert not [effect for record in after["turns"] for effect in record["effects"]]


def test_the_hub_acts_only_with_the_owners_own_trusted_handle(desk):
    folder, _ = desk
    with Device(folder) as device:
        store, authority = device.table.store, device.table.authority
        assert CampaignApi(store, authority, owner=OWNER, handle=device.api.owner_handle).handle is device.api.owner_handle
        with pytest.raises(ValueError):
            CampaignApi(store, authority, owner=OWNER, handle=authority.handle_for("human:someone-else", trusted=True))
        with pytest.raises(ValueError):
            CampaignApi(store, authority, owner=OWNER, handle=authority.handle_for(OWNER, trusted=False))


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_the_campaign_page_end_to_end(desk):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        kept_turn(device, chat)
        app = AppServer(device.api)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = app.address
            env = {**os.environ, "GRIMOIRE_APP": f"http://{host}:{port}", "GRIMOIRE_TOKEN": app.token,
                   "GRIMOIRE_CHAT": str(chat), "GRIMOIRE_CAMPAIGN": chat.campaign_id}
            result = subprocess.run([NODE, "--test", "--test-reporter=tap", str(Path(__file__).with_name("js") / "campaign.e2e.mjs")],
                                    capture_output=True, text=True, timeout=180, check=False, env=env)
        finally:
            app.close()
            thread.join(10)
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-3000:]
    assert "# pass 1" in result.stdout
