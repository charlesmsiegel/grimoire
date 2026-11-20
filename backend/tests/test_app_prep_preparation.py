"""Sprint 115's scene preparation (064) as the Prep page's ``prep.preparation``, ``prep.prepare_plan``,
``prep.prepare`` and ``prep.prepare_review`` ops, through 108's shell contract.

The evidence is assembled from what a synthetic library holds for one chat (``prep_app_fixtures.stocked``); the one
metered ``planning`` call goes through 013's adapter to the endpoint the app's model settings name, over
``ScriptedTransport`` only: never a real provider. Each test counts what was sent.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from prep_app_fixtures import (
    AIM, BRIEF, DIRECTOR, REFUTED, SECRET, TOLD, configured, draft, ids_of, reply, settings, stocked,
)
from store_helpers import OWNER
from textapi_fixtures import Device, desktop_library, say

from grimoire.app.appapi import AppApi
from grimoire.core import Moment, Proposition
from grimoire.evidencestore import EvidenceLibrary
from grimoire.testing.fileops import snapshot as files_of
from grimoire.testing.providers import Reply
from grimoire.textapi import ApiRefused

OPS = ("prep.preparation", "prep.prepare_plan", "prep.prepare", "prep.prepare_review")


@pytest.fixture
def desk(tmp_path):
    return desktop_library(tmp_path / "desk")


def refused(app: AppApi, op: str, **args) -> ApiRefused:
    with pytest.raises(ApiRefused) as caught:
        app.call(op, args)
    return caught.value


class Table:
    """A stocked library with model settings over a scripted transport whose replies the test adds."""

    def __init__(self, device, chat, tmp_path, **extra) -> None:
        self.device, self.chat = device, chat
        self.cast = stocked(device, chat)
        self.transport = configured(device, settings(tmp_path / "model.json", self.cast["Mara Vell"], **extra), [])
        self.app = AppApi(device.api)
        self.args = {"chat": str(chat), **DIRECTOR}

    def view(self) -> dict:
        return self.app.call("prep.preparation", self.args)

    def answer(self, *replies) -> None:
        self.transport._steps.extend(replies)

    def good(self, **extra) -> Reply:
        ids = ids_of(self.view()["evidence"]["evidence"])
        return reply(draft(self.cast["Mara Vell"], self.cast["harbor"], ids), **extra)

    def plan(self, request: str, brief: str = BRIEF) -> dict:
        return self.app.call("prep.prepare_plan", {**self.args, "request_id": request, "brief": brief})["plan"]

    def prepare(self, request: str, expected, brief: str = BRIEF) -> dict:
        return self.app.call("prep.prepare", {**self.args, "request_id": request, "brief": brief, "expected": expected})

    def sent(self) -> list[dict]:
        return [json.loads(send.body) for send in self.transport.sends]


def test_every_op_is_the_directors(desk, tmp_path):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        table = Table(device, chat, tmp_path)
        args = {"chat": str(chat), "request_id": "r1", "brief": BRIEF, "decision": "reject"}
        for op in OPS:
            for audience in (None, "table"):
                shown = {**args, **({"audience": audience} if audience else {})}
                assert refused(table.app, op, **shown).reason == "director_only", (op, audience)
        assert refused(table.app, "prep.preparation", chat=str(chat), audience="owner").reason == "invalid_field"
        assert not table.transport.sends


def test_the_evidence_is_what_the_library_holds_and_nothing_is_written(desk, tmp_path):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        table = Table(device, chat, tmp_path)
        before = files_of(device.table.store.root)
        view = table.view()
        shown = view["evidence"]
        texts = {item["kind"]: [] for item in shown["evidence"]}
        for item in shown["evidence"]:
            texts[item["kind"]].append(item["text"])
        # a secret is the director's truth no player character holds: Anya was told of the debt, and the spy
        # rumour is refuted, so only the oil is a secret
        assert texts["secret"] == [SECRET]
        assert TOLD not in json.dumps(view) and REFUTED not in json.dumps(view)
        assert texts["goal"] == [f"Mara Vell wants: {AIM}"]
        assert texts["calendar"] == ["The campaign's date is 1024-03-01 21:00.",
                                     "The smugglers' boat leaves at low tide is due 1024-03-02 00:00."]
        assert texts["choice"] == ["Anya Rook (turn 1): I press a coin into Mara's hand"]
        assert [item["evidence"] for item in shown["evidence"]] == [
            "secret-1", "goal-1", "calendar-1", "calendar-2", "choice-1"]
        secret = shown["evidence"][0]
        assert secret["source"] == f"campaign:{chat.campaign_id}#proposition/oil-sold"
        assert shown["missing"] == [] and shown["omitted"] == {}
        assert [item["name"] for item in shown["locations"]] == ["The Quay"]
        assert {item["name"] for item in shown["cast"]} == {"Anya Rook", "Mara Vell"}
        assert view["model"] is None and view["proposals"] == [] and view["saved"] is False
        assert "Nothing here is saved" in view["note"]
        table.plan("r1")
        assert files_of(device.table.store.root) == before and not table.transport.sends


def test_one_call_is_made_only_as_estimated_and_approved(desk, tmp_path):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        table = Table(device, chat, tmp_path)
        table.answer(table.good())
        before = files_of(device.table.store.root)
        plan = table.plan("r1")
        assert not table.transport.sends, "an estimate sends nothing"
        assert plan["replay"] is False and plan["blocked"] is None
        call = plan["call"]
        assert (call["stage"], call["calls"], call["measurement"]) == ("planning", 1, "estimated")
        assert call["reply_tokens_at_most"] == 800 and call["attempts_at_most"] == 1 and call["local"] is False
        assert call["route"].startswith("prep@") and call["route"].endswith("/prep-model")
        # at most: the estimated prompt and the whole reply cap, at 3 and 15 per million
        assert call["cost_state"] == "estimated"
        assert float(call["cost_at_most"]) == pytest.approx((call["prompt_tokens"] * 3 + 800 * 15) / 1e6, abs=1e-6)

        assert refused(table.app, "prep.prepare", **table.args, request_id="r1", brief=BRIEF).reason == "review_required"
        with pytest.raises(ApiRefused) as caught:
            table.prepare("r1", "not-the-token")
        assert caught.value.reason == "changed" and not table.transport.sends

        done = table.prepare("r1", plan["expected"])
        assert done["done"] == {"request": "r1", "replay": False}
        assert len(table.transport.sends) == 1
        body = table.sent()[0]
        assert body["model"] == "prep-model"
        prompt = "\n".join(message["content"] for message in body["messages"])
        assert SECRET in prompt and AIM in prompt and BRIEF in prompt
        assert TOLD not in prompt and REFUTED not in prompt
        assert files_of(device.table.store.root) == before, "nothing is saved"

        shown = done["preparation"]["proposals"]
        assert len(shown) == 1 and shown[0]["current"] is True and shown[0]["outcome"] is None
        scene = shown[0]["draft"]
        assert scene["location"]["name"] == "The Quay" and [who["name"] for who in scene["cast"]] == ["Mara Vell"]
        assert [ground["evidence"] for ground in scene["premise"]["evidence"]] == ["choice-1", "goal-1"]
        assert scene["clues"][0]["evidence"] == [{"evidence": "secret-1", "kind": "secret",
                                                  "source": f"campaign:{chat.campaign_id}#proposition/oil-sold"}]
        usage = shown[0]["usage"]
        assert usage["sent"] == 1 and usage["tokens"] == {"input_tokens": 410, "output_tokens": 120}
        assert usage["cost"] is not None
        assert done["preparation"]["spent"][0]["kept"] is True


def test_a_retry_with_the_same_request_sends_nothing_and_a_reused_id_is_refused(desk, tmp_path):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        table = Table(device, chat, tmp_path)
        table.answer(table.good())
        plan = table.plan("r1")
        first = table.prepare("r1", plan["expected"])
        # the answer was lost: the same request sent again finds the scene and sends nothing
        again = table.prepare("r1", plan["expected"])
        assert again["done"] == {"request": "r1", "replay": True} and len(table.transport.sends) == 1
        assert again["preparation"]["proposals"] == first["preparation"]["proposals"]
        assert table.plan("r1")["replay"] is True
        # the same id for another brief is another request
        assert refused(table.app, "prep.prepare_plan", **table.args, request_id="r1",
                       brief="Somewhere else entirely").reason == "request_conflict"
        assert refused(table.app, "prep.prepare", **table.args, request_id="r1", brief="Somewhere else entirely",
                       expected=plan["expected"]).reason == "request_conflict"
        assert len(table.transport.sends) == 1


def test_evidence_that_changed_after_the_estimate_refuses_and_sends_nothing(desk, tmp_path):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        table = Table(device, chat, tmp_path)
        table.answer(table.good())
        plan = table.plan("r1")
        EvidenceLibrary(device.table.store, device.table.owner, owner=OWNER).append(chat.campaign_id, [
            Proposition(chat.campaign_id, "new-truth", "The lighthouse keeper is awake.", Moment(4, "quay", "t1"),
                        "director")]).commit()
        with pytest.raises(ApiRefused) as caught:
            table.prepare("r1", plan["expected"])
        assert caught.value.reason == "changed" and not table.transport.sends
        # the new estimate shows the new evidence, and its token sends
        fresh = table.plan("r1")
        assert fresh["expected"] != plan["expected"]
        assert any(item["text"] == "The lighthouse keeper is awake." for item in table.view()["evidence"]["evidence"])
        table.answer(table.good())
        table.transport._steps.pop(0)  # the first scripted reply was for the old evidence's ids, which still hold
        table.prepare("r1", fresh["expected"])
        assert len(table.transport.sends) == 1


def test_a_refused_reply_keeps_nothing_and_its_approval_is_spent(desk, tmp_path):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        table = Table(device, chat, tmp_path)
        table.answer(Reply(deltas=("Here is a scene: not JSON",), usage={"prompt_tokens": 400, "completion_tokens": 9}))
        plan = table.plan("r1")
        with pytest.raises(ApiRefused) as caught:
            table.prepare("r1", plan["expected"])
        assert caught.value.reason == "unparseable" and len(table.transport.sends) == 1
        view = table.view()
        assert view["proposals"] == [] and view["spent"][0]["kept"] is False and view["spent"][0]["sent"] == 1
        # the approval was for one call, and that call was made: sending again needs the estimate again
        with pytest.raises(ApiRefused) as caught:
            table.prepare("r1", plan["expected"])
        assert caught.value.reason == "changed" and len(table.transport.sends) == 1
        again = table.plan("r1")
        assert again["expected"] != plan["expected"]
        table.answer(table.good())
        assert table.prepare("r1", again["expected"])["done"]["replay"] is False
        assert len(table.transport.sends) == 2


def test_a_truncated_reply_is_never_read(desk, tmp_path):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        table = Table(device, chat, tmp_path)
        good = table.good()
        table.answer(replace(good, deltas=(good.deltas[0][:60],), finish_reason="length"))
        plan = table.plan("r1")
        with pytest.raises(ApiRefused) as caught:
            table.prepare("r1", plan["expected"])
        assert caught.value.reason == "incomplete_length" and table.view()["proposals"] == []


def test_the_review_is_the_directors_and_kept_in_memory_only(desk, tmp_path):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        table = Table(device, chat, tmp_path)
        table.answer(table.good(), table.good())
        before = files_of(device.table.store.root)
        for request in ("r1", "r2"):
            table.prepare(request, table.plan(request)["expected"])
        review = {**table.args, "request_id": "r1"}
        assert refused(table.app, "prep.prepare_review", **review, decision="accept").reason == "invalid_field"
        assert refused(table.app, "prep.prepare_review", **review, decision="keep").reason == "invalid_field"
        assert refused(table.app, "prep.prepare_review", **{**review, "request_id": "r9"},
                       decision="reject").reason == "unknown_proposal"
        accepted = table.app.call("prep.prepare_review", {**review, "decision": "accept", "credible_stakes": True,
                                                          "open_agency": True, "grounded": True})
        outcomes = {item["request"]: item["outcome"] for item in accepted["preparation"]["proposals"]}
        assert outcomes == {"r1": "accepted", "r2": None}
        rejected = table.app.call("prep.prepare_review", {**table.args, "request_id": "r2", "decision": "reject"})
        assert {item["request"]: item["outcome"] for item in rejected["preparation"]["proposals"]} == {
            "r1": "accepted", "r2": "rejected"}
        assert refused(table.app, "prep.prepare_plan", **table.args, request_id="r2", brief=BRIEF).reason == "rejected"
        assert files_of(device.table.store.root) == before and len(table.transport.sends) == 2


def test_a_review_of_a_scene_whose_evidence_changed_is_refused(desk, tmp_path):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        table = Table(device, chat, tmp_path)
        table.answer(table.good())
        table.prepare("r1", table.plan("r1")["expected"])
        EvidenceLibrary(device.table.store, device.table.owner, owner=OWNER).append(chat.campaign_id, [
            Proposition(chat.campaign_id, "late", "A storm is coming.", Moment(5, "quay", "t1"), "director")]).commit()
        assert table.view()["proposals"][0]["current"] is False
        assert refused(table.app, "prep.prepare_review", **table.args, request_id="r1", decision="accept",
                       credible_stakes=True, open_agency=True, grounded=True).reason == "changed"


def test_with_no_model_nothing_can_be_sent(desk, tmp_path):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        stocked(device, chat)
        app = AppApi(device.api)  # no model settings: preparation has no model to go to
        args = {"chat": str(chat), **DIRECTOR}
        assert app.call("prep.preparation", args)["model"]["reason"] == "no_model"
        plan = app.call("prep.prepare_plan", {**args, "request_id": "r1", "brief": BRIEF})["plan"]
        assert plan["blocked"]["reason"] == "no_model" and plan["call"]["cost_state"] == "unpriced"
        assert refused(app, "prep.prepare", **args, request_id="r1", brief=BRIEF,
                       expected=plan["expected"]).reason == "no_model"


def test_a_key_variable_that_is_not_set_sends_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("PREP_TEST_KEY_UNSET", raising=False)
    folder, chat = desktop_library(tmp_path / "desk")
    with Device(folder, [say("Mara pockets it.")]) as device:
        table = Table(device, chat, tmp_path, key_env="PREP_TEST_KEY_UNSET")
        assert table.view()["model"]["reason"] == "key_missing"
        plan = table.plan("r1")
        assert refused(table.app, "prep.prepare", **table.args, request_id="r1", brief=BRIEF,
                       expected=plan["expected"]).reason == "key_missing"
        assert not table.transport.sends


def test_bad_input_is_refused_before_anything_is_estimated(desk, tmp_path):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        table = Table(device, chat, tmp_path)
        for args, reason in (({"request_id": "R 1", "brief": BRIEF}, "invalid_field"),
                             ({"request_id": "r1", "brief": "   "}, "invalid_field"),
                             ({"request_id": "r1", "brief": "x" * 2001}, "invalid_field"),
                             ({"request_id": "r1", "brief": 7}, "invalid_field")):
            assert refused(table.app, "prep.prepare_plan", **table.args, **args).reason == reason
        assert refused(table.app, "prep.preparation", chat="not a chat", **DIRECTOR).reason == "invalid_field"
        other = f"campaign:{chat.campaign_id}#chat/no-such-chat"
        assert refused(table.app, "prep.preparation", chat=other, **DIRECTOR).reason not in ("", None)
        assert not table.transport.sends
