"""088 through the player-facing API, normal obstacle adjudication and recorded rolls."""

from dataclasses import replace
import shutil

import pytest

from chat_fixtures import Clock
from scene_fixtures import ANYA, MARA_VELL, Tagged, attach_tamsin, card_library
from store_helpers import OWNER
from test_app_rules import Dice, SLIP, STEALTH
from test_campaignapi import BRIBE, SNEAK, Hub, acting, hold
from test_scene_obstacles import _fork_first_turn

from grimoire.app.adapters import rules
from grimoire.app.appapi import AppApi
from grimoire.campaignapi.pacing import PACE_KEY, read_pacing, suggested_obstacle
from grimoire.chats import Launch
from grimoire.chatstore import ChatLibrary
from grimoire.core import Event, Moment, Perception
from grimoire.core.identity import ChatRef
from grimoire.obstacles import ObstacleFact
from grimoire.play.together import Contribution
from grimoire.testing.fileops import snapshot
from grimoire.textapi import ApiRefused, TextApi


def app_for(hub):
    api = TextApi(hub.at.store, hub.at.authority, owner=OWNER)
    api._campaign_hub = hub.api
    return AppApi(api)


def pace(app, chat, mode):
    current = app.call("campaign.hub", {"chat": str(chat)})["pacing"]
    return app.call("campaign.pacing", {"chat": str(chat), "mode": mode, "expected": current["etag"]})


def test_two_cadences_play_distinct_obstacles_and_record_the_same_roll(tmp_path):
    original, chat, _ = card_library(tmp_path)
    outcomes = []
    for mode, expected in (("every_action", ["a-hold", "b-hold", "c-hold"]),
                           ("every_third_action", [None, None, "a-hold"])):
        folder = tmp_path / mode
        shutil.copytree(original, folder)
        with Hub(folder, *([BRIBE] * 3)) as hub:
            app = app_for(hub)
            dice = Dice(14)
            rules.configure(app.api, dice)
            runner = hub.api.runner
            runner.seat(chat, OWNER)
            scene = runner.open(chat)
            (mara,) = [who for who in scene.cast if who != scene.persona]
            for name in reversed(["a-hold", "b-hold", "c-hold"]):
                hub.api.obstacles.record(replace(hold(mara), obstacle_id=name))
            args = {"chat": str(chat)}
            attempted = {}
            for index, wanted in enumerate(expected):
                post = runner.post(chat, hub.api.handle, f"*I offer the guard a coin at hatch {index + 1}.*")
                if index == 0:
                    default = app.call("campaign.hub", args)["pacing"]
                    assert default["mode"] == "manual" and default["suggested_obstacle"] is None
                    assert pace(app, chat, mode)["pacing"]["mode"] == mode
                before = snapshot(folder)
                shown = app.call("campaign.hub", args)
                assert shown["pacing"]["suggested_obstacle"] == wanted
                assert app.call("campaign.hub", args)["pacing"] == shown["pacing"]
                assert snapshot(folder) == before and len(hub.at.labels.prompts()) == len(attempted)
                if wanted is not None:
                    against = shown["open_action"]
                    request = {**args, "turn": against["turn"], "expected": against["expected"]}
                    labelled = app.call("campaign.label", {**request, "obstacle": wanted})
                    assert labelled["done"]["label"]["against"]["obstacle"] == wanted
                    assert labelled["hub"]["pacing"]["suggested_obstacle"] is None
                    reviewed = labelled["hub"]["open_action"]
                    result = app.call("campaign.confirm", {**request, "expected": reviewed["expected"]})
                    assert result["done"]["outcome"]["result"] == "success"
                    attempted[wanted] = post.turn_id
                runner.pass_turn(chat, hub.api.handle)
            assert {state.obstacle.obstacle_id: state.overcome_by for state in hub.api.obstacles.states(chat.campaign_id)
                    if state.overcome_by is not None} == attempted
            assert len(attempted) == (3 if mode == "every_action" else 1)

            ask = {**args, "campaign": chat.campaign_id, "actor": str(scene.persona), "family": "d20",
                   "request_id": "same-check", "action": STEALTH, "text": SLIP}
            planned = app.call("rules.roll_plan", ask)["plan"]
            rolled = app.call("rules.roll", {**ask, "turn": planned["turn"], "review": planned["review"]})["roll"]
            assert dice.asked == [20] and rolled["faces"] == [14]
            outcomes.append((planned["module"], rolled["faces"], rolled["result"], rolled["outcome"]))
            ledger = folder / "authority" / "rolls" / chat.campaign_id
            before = snapshot(ledger)
            assert before
            assert pace(app, chat, "manual")["pacing"]["mode"] == "manual"
            assert snapshot(ledger) == before
    assert outcomes[0] == outcomes[1]


def test_bad_obstacle_read_does_not_refuse_a_valid_pacing_save(tmp_path, monkeypatch):
    folder, chat, _ = card_library(tmp_path)
    with Hub(folder, labelling=False) as seeded:
        acting(seeded, chat)
    with Hub(folder) as hub:
        app = app_for(hub)
        args = {"chat": str(chat)}
        start = read_pacing(hub.at.store, chat.scope)
        ops = hub.at.store._access.ops
        list_dir = ops.list_dir
        blocked = folder / "authority" / "obstacles"
        denied = []

        def unreadable(path):
            if path == blocked:
                denied.append(path)
                raise PermissionError("synthetic obstacle read failure")
            return list_dir(path)

        monkeypatch.setattr(ops, "list_dir", unreadable)
        args |= {"mode": "every_action", "expected": start.etag}
        changed = app.call("campaign.pacing", args)["pacing"]
        assert changed["mode"] == "every_action" and changed["suggested_obstacle"] is None
        assert denied == [blocked] and changed["etag"] != start.etag
        with pytest.raises(ApiRefused) as stale:
            app.call("campaign.pacing", args)
        assert stale.value.reason == "changed"
        for changes in ({"chat": str(ChatRef(chat.campaign_id, "missing"))}, {"audience": "other"}, {"expected": "bad"}):
            with pytest.raises(ApiRefused):
                app.call("campaign.pacing", {**args, **changes})
        assert app.call("campaign.hub", {"chat": str(chat)})["pacing"] == changed
    with Hub(folder, labelling=False) as reopened:
        app = app_for(reopened)
        assert pace(app, chat, "every_action")["pacing"]["suggested_obstacle"] is None
        shown = app.call("campaign.hub", {"chat": str(chat)})
        assert shown["open_action"] is not None and shown["pacing"]["mode"] == "every_action"
        assert shown["pacing"]["suggested_obstacle"] is None
        assert [(item["obstacle"], item["status"]) for item in shown["obstacles"]] == [("hold", "open")]


def test_linked_obstacle_stops_being_a_fresh_offer_even_after_withdrawal(tmp_path):
    folder, chat, _ = card_library(tmp_path)
    with Hub(folder, SNEAK) as hub:
        app = app_for(hub)
        runner, owner = hub.api.runner, hub.api.handle
        chats = ChatLibrary(hub.at.store, owner, clock=Clock(), new_id=Tagged("other"), owner=OWNER)
        side = chats.start_chat(Launch("character", MARA_VELL), persona=ANYA, scope=chat.scope).commit().chat
        runner.seat(side, OWNER)
        acting(hub, chat, "*I slip past the lantern*")
        runner.post(side, owner, "*I light a lamp*")
        assert pace(app, side, "every_action")["pacing"]["suggested_obstacle"] == "hold"
        assert runner.label(chat, owner, obstacle="hold").state == "proposed"
        assert runner.confirm(chat, owner) is None
        pending = hub.api.obstacles.state(chat.campaign_id, "hold")
        assert pending.status == "open" and pending.pending is not None
        args = {"chat": str(side)}
        assert app.call("campaign.hub", args)["pacing"]["suggested_obstacle"] is None
        branch = _fork_first_turn(hub.at, chat)
        runner.choose(chat, owner, branch)
        withdrawn = hub.api.obstacles.state(chat.campaign_id, "hold")
        assert withdrawn.status == "open" and [attempt.status for attempt in withdrawn.attempts] == ["void"]
        assert app.call("campaign.hub", args)["pacing"]["suggested_obstacle"] is None


def test_public_action_cadence_does_not_read_another_chats_private_history(tmp_path):
    folder, chat, _ = card_library(tmp_path)
    with Hub(folder, BRIBE) as hub:
        app = app_for(hub)
        runner, owner = hub.api.runner, hub.api.handle
        chats = ChatLibrary(hub.at.store, owner, clock=Clock(), new_id=Tagged("side"), owner=OWNER)
        side = chats.start_chat(Launch("character", MARA_VELL), persona=ANYA, scope=chat.scope).commit().chat
        for thread in (chat, side):
            runner.seat(thread, OWNER)
        scene = runner.read_scene(chat)
        (mara,) = [who for who in scene.cast if who != scene.persona]
        timed = ObstacleFact("late", "constraint", "A gate closes later.", 1, "author:test", {"bribe": "favours"})
        clash = replace(timed, fact_id="f1", established=0)
        hub.api.obstacles.record(replace(hold(mara), obstacle_id="a-timed", facts=(timed,)))
        hub.api.obstacles.record(replace(hold(mara), obstacle_id="b-clash", facts=(clash,)))
        hub.api.obstacles.record(hold(mara))
        pace(app, chat, "every_third_action")
        private_action = "*I whisper the code under my breath*"
        for text, kind, private in (("*I put down a coin.*", "action", False), (private_action, "action", True),
                                    ('"I wait."', "speech", False), ("*I light a lamp*", "action", False)):
            post = runner.post(chat, owner, text).post
            assert (post.kind, post.private) == (kind, private)
            runner.pass_turn(chat, owner)
        runner.post(chat, owner, "*I move to the hatch.*")
        at = {"chat": str(chat)}
        before = app.call("campaign.hub", at)
        assert before["pacing"]["suggested_obstacle"] == "hold"
        private = runner.post(side, owner, private_action)
        assert private.post.private
        assert app.call("campaign.hub", {"chat": str(side)})["pacing"] == {**before["pacing"], "suggested_obstacle": None}
        campaign = chat.campaign_id
        event = Event(campaign, "private-event", "A blue token is hidden below deck.", Moment(10), "director")
        observed = Perception(campaign, "private-sight", mara, "private-event", "sight",
                              "There is a blue token below deck.", Moment(10), "director", True)
        hub.api.evidence.append(campaign, (event, observed)).commit()
        assert app.call("campaign.hub", at)["pacing"] == before["pacing"]
        assert {item["obstacle"] for item in before["obstacles"]} == {"a-timed", "b-clash", "hold"}

        # Changing cadence never invalidates an explicit obstacle choice from the same reviewed turn.
        pace(app, chat, "manual")
        action = before["open_action"]
        request = {**at, "turn": action["turn"], "expected": action["expected"], "obstacle": "a-timed"}
        changed = app.call("campaign.label", request)
        assert changed["done"]["label"]["against"]["obstacle"] == "a-timed"
        with pytest.raises(ApiRefused) as stale:
            app.call("campaign.label", request)
        assert stale.value.reason == "changed"
        reviewed = changed["hub"]["open_action"]
        with pytest.raises(ApiRefused) as unavailable:
            app.call("campaign.label", {**request, "expected": reviewed["expected"], "obstacle": "b-clash"})
        assert unavailable.value.reason == "obstacle_ids_clash"
        assert len(hub.at.labels.prompts()) == 1


def test_set_aside_and_combined_public_actions_do_not_advance_the_active_cadence(tmp_path):
    folder, chat, _ = card_library(tmp_path)
    with Hub(folder) as hub:
        app = app_for(hub)
        runner, owner = hub.api.runner, hub.api.handle
        tamsin = attach_tamsin(hub.at.store, owner, chat)
        runner.seat(chat, OWNER)
        scene = runner.read_scene(chat)
        (mara,) = [who for who in scene.cast if who not in (scene.persona, tamsin)]
        runner.seat_player(chat, owner, tamsin, OWNER)
        hub.api.obstacles.record(hold(mara))
        pace(app, chat, "every_third_action")
        args = {"chat": str(chat)}

        first = runner.post(chat, owner, "*I light a lamp*")
        aside = _fork_first_turn(hub.at, chat)
        assert app.call("campaign.hub", args)["pacing"]["suggested_obstacle"] is None
        selected = runner.choose(chat, owner, first.turn_id)
        assert any(item.record.turn_id == aside for item in selected.turns.set_aside)
        runner.pass_turn(chat, owner)
        combined = runner.post_together(chat, (Contribution(owner, scene.persona, "*I move a chair*"),
                                               Contribution(owner, tamsin, "*I put down a cup*")))
        assert len(combined.posts) == 2 and all(post.kind == "action" and not post.private for post in combined.posts)
        runner.pass_turn(chat, owner)
        runner.post(chat, owner, "*I put down a coin*")
        assert app.call("campaign.hub", args)["pacing"]["suggested_obstacle"] is None
        runner.pass_turn(chat, owner)
        runner.post(chat, owner, "*I move to the hatch*")
        assert app.call("campaign.hub", args)["pacing"]["suggested_obstacle"] == "hold"


@pytest.mark.parametrize("depth", [0, 69])
def test_malformed_pacing_keeps_the_hub_available_with_an_explicit_repair(tmp_path, depth):
    folder, chat, _ = card_library(tmp_path)
    with Hub(folder, labelling=False) as hub:
        app = app_for(hub)
        record = hub.at.store.read(chat.scope)
        malformed = "private fixture"
        for _ in range(depth):
            malformed = {"nested": malformed}
        transaction = hub.at.store.transaction()
        transaction.put(replace(record.record, payload={**record.record.payload, PACE_KEY: malformed}), record.etag)
        transaction.commit(hub.api.handle)
        view = app.call("campaign.hub", {"chat": str(chat)})
        assert view["pacing"]["mode"] is None and view["pacing"]["suggested_obstacle"] is None
        assert view["pacing"]["etag"] == hub.at.store.read(chat.scope).etag
        assert not any(problem["detail"] == "undecodable_scope" for problem in view["problems"])
        assert "private fixture" not in repr(view)
        chats = ChatLibrary(hub.at.store, hub.api.handle, clock=Clock(), new_id=Tagged("rename"), owner=OWNER)
        before = hub.at.store.read(chat.scope)
        chats.name_scope(chat.scope, "Renamed campaign", expected=before.etag).commit()
        renamed = hub.at.store.read(chat.scope).record.payload
        assert renamed[PACE_KEY] == before.record.payload[PACE_KEY] and renamed["title"] == "Renamed campaign"
        assert pace(app, chat, "manual")["pacing"]["mode"] == "manual"


def test_paused_conflicted_and_combined_actions_do_not_offer_an_obstacle(tmp_path):
    folder, chat, _ = card_library(tmp_path)
    with Hub(folder) as hub:
        runner = hub.api.runner
        runner.seat(chat, OWNER)
        runner.post(chat, hub.api.handle, "*I look around the hatch.*")
        scene = runner.read_scene(chat)
        current = scene.turns.last
        (mara,) = [who for who in scene.cast if who != scene.persona]
        hub.api.obstacles.record(hold(mara))
        states = hub.api.obstacles.states(chat.campaign_id)
        assert suggested_obstacle("every_action", scene, OWNER, states) == "hold"
        assert suggested_obstacle("every_action", scene, "human:another", states) is None
        combined = replace(current.record, also=(replace(current.record.post, actor=mara, audience=(mara,)),))
        paused = replace(current.record, paused=True, paused_by=OWNER)
        for turns in (replace(scene.turns, conflicted=(current.record.turn_id,)),
                      replace(scene.turns, problems=((str(chat), "turn_fork"),)),
                      replace(scene.turns, turns=(replace(current, record=combined),)),
                      replace(scene.turns, turns=(replace(current, record=paused),))):
            assert suggested_obstacle("every_action", replace(scene, turns=turns), OWNER, states) is None
