"""Production adapter review identities over synthetic domain records; no provider or private data."""
from dataclasses import replace

import pytest
from scene_fixtures import card_library
from store_helpers import OWNER
from test_campaignapi import Hub, SNEAK, acting

from grimoire.app.appapi import AppApi
from grimoire.textapi import TextApi, ApiRefused


def reviewed(app, chat):
    action = app.call("campaign.hub", {"chat": str(chat)})["open_action"]
    return {"chat": str(chat), "turn": action["turn"], "expected": action["expected"]}


def test_relabel_and_replaced_turn_cannot_be_confirmed_by_stale_screen(tmp_path):
    folder, chat, _ = card_library(tmp_path)
    with Hub(folder, SNEAK, SNEAK) as hub:
        acting(hub, chat)
        api = TextApi(hub.at.store, hub.at.authority, owner=OWNER)
        api._campaign_hub = hub.api
        app = AppApi(api)
        app.call("campaign.label", {**reviewed(app, chat), "obstacle": "hold"})
        old = reviewed(app, chat)
        app.call("campaign.label", {**old, "obstacle": "hold"})
        with pytest.raises(ApiRefused, match="changed"):
            app.call("campaign.confirm", old)
        assert hub.api.runner.open(chat).pending.framed is None
        latest = reviewed(app, chat)
        hub.api.runner.pass_turn(chat, hub.api.handle, turn=latest["turn"])
        hub.api.runner.post(chat, hub.api.handle, "*I wait by the hatch*")
        for op in ("label", "confirm", "decide"):
            with pytest.raises(ApiRefused) as caught:
                app.call("campaign." + op, old)
            assert caught.value.reason == "changed"
        assert len(hub.at.labels.prompts()) == 2  # stale label did not spend another call
        assert not hub.api.runner.open(chat).pending.labels


def test_director_decision_binds_frame_even_when_turn_revision_did_not_change(tmp_path):
    folder, chat, _ = card_library(tmp_path)
    with Hub(folder, SNEAK) as hub:
        turn, _, _ = acting(hub, chat)
        hub.call("label", chat=str(chat), obstacle="hold")
        hub.call("confirm", chat=str(chat))
        api = TextApi(hub.at.store, hub.at.authority, owner=OWNER)
        api._campaign_hub = hub.api
        app = AppApi(api)
        review = reviewed(app, chat)
        fingerprint = app.call("campaign.hub", {"chat": str(chat)})["open_action"]["framed"]["fingerprint"]
        with pytest.raises(ApiRefused) as caught:
            app.call("campaign.decide", {**review, "result": "failure", "basis": "adjudicated"})
        assert caught.value.reason == "review_required"
        entry = hub.api.adjudication.state(chat.campaign_id).frame(turn)
        hub.api.adjudication.frame(replace(entry.frame, provenance="author:revised"),
                                  contribution=entry.contribution, author=hub.api.handle)
        assert reviewed(app, chat) == review  # 029 changed; the turn record did not
        with pytest.raises(ApiRefused) as caught:
            app.call("campaign.decide", {**review, "result": "failure", "basis": "adjudicated",
                                         "fingerprint": fingerprint})
        assert caught.value.reason == "fingerprint_mismatch"
        assert hub.api.adjudication.outcome(chat.campaign_id, turn) is None
