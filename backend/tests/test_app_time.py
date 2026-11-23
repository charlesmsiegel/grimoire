"""Sprint 116's Time page and its ``time.*`` adapter ops, through 108's shell contract.

The adapter serves 074's ``DowntimeLibrary``, 076's ``OffscreenLibrary`` and 077's ``CommitmentLibrary`` over the
app's open library (all three over ``campaigns/<id>/scope.json``, the director's). The proving scenario runs as the
page does: a commitment is offered with a new deadline and refused by its NPC obligor; a plan is adopted for the
harbour guild under its leader's decision; a 20-day study is begun for the owner's character and pursued across
the deadline, which is flagged and nothing more; every change is reviewed first (a plan that writes nothing) and made
exactly as reviewed, once, however often the commit is retried; the refused commitment still reads its passed
deadline and is not overdue; and the pursuit's pending advance, applied to the plans, develops the guild's steps and
delivers no knowledge. The table reads none of it. The page itself runs end to end under Node
(``js/time.e2e.mjs``) where Node is installed. Synthetic library; no model is called.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from table_fixtures import BOB, node_e2e
from time_fixtures import DIRECTOR_VIEW, PROMISE, Played, guild, scope_bytes, timed

from grimoire.app.adapters import ADAPTERS
from grimoire.app.adapters.time import FEATURES
from grimoire.app.pages import load_pages
from grimoire.core import DIRECTOR
from grimoire.evidencestore import EvidenceLibrary
from grimoire.textapi import ApiRefused

STATIC = Path(__file__).resolve().parents[1] / "src" / "grimoire" / "app" / "static"
NODE = shutil.which("node")
DAY = 1440
SPOKEN = PROMISE.strip('"')  # the promise as the turn heard it: a speech post's words
WRITES = ("downtime_plan", "downtime_commit", "offscreen_plan", "offscreen_commit", "commitment_plan",
          "commitment_commit")


def refused(table: Played, op: str, **args) -> ApiRefused:
    with pytest.raises(ApiRefused) as caught:
        table(op, **args)
    return caught.value


class Desk:
    """The Time page's calls for one campaign and chat, as the director's view makes them."""

    def __init__(self, table: Played) -> None:
        self.table, self.campaign, self.chat = table, table.chat.campaign_id, str(table.chat)

    def overview(self) -> dict:
        return self.table("time.overview", campaign=self.campaign, chat=self.chat, **DIRECTOR_VIEW)

    def request(self, operation: str, operation_id: str | None, **args) -> dict:
        full = {"campaign": self.campaign, "chat": self.chat, **DIRECTOR_VIEW, "operation": operation,
                "expected": self.overview()["etag"], **args}
        if operation_id is not None:
            full["operation_id"] = operation_id
        return full

    def plan(self, domain: str, request: dict) -> dict:
        return self.table(f"time.{domain}_plan", **request)["plan"]

    def commit(self, domain: str, request: dict, **extra) -> dict:
        return self.table(f"time.{domain}_commit", **request, **extra)["done"]

    def made(self, domain: str, request: dict, **extra) -> tuple[dict, dict]:
        """Reviewed (writing nothing), then made as reviewed: ``(plan, done)``."""
        before = scope_bytes(self.table)
        planned = self.plan(domain, request)
        assert scope_bytes(self.table) == before, "a review writes nothing"
        if "reviewed_controls" in planned:  # a decision is made under the controls it was reviewed under
            extra = {"reviewed_controls": planned["reviewed_controls"], **extra}
        return planned, self.commit(domain, request, **extra)


def offer_map(desk: Desk, operation_id: str = "offer-map") -> dict:
    t = desk.table
    return desk.request("offer", operation_id, commitment="map", kind="mission", summary="Bring Anya the harbour map",
                        owed_by=[t.cast["Mara Vell"]], owed_to=[t.cast["Anya Rook"]], cites=["ev-asks"],
                        deadline={"new": {"deadline": "map-due", "label": "Map due", "date": "1024-03-10", "minute": 0}})


def adopt_docks(desk: Desk) -> dict:
    t = desk.table
    return desk.request("adopt", "adopt-docks", group=str(guild(t)), leader=t.cast["Mara Vell"],
                        goals=["Control the harbour"], policy="The leader decides.", choice="Seize the docks",
                        plan="docks", goal="Control the harbour", priority=80, deadline="map-due",
                        steps=[{"label": "Gather the bribe money", "minutes": 5 * DAY},
                               {"label": "Take the docks", "minutes": 10 * DAY}])


def begin_study(desk: Desk) -> dict:
    return desk.request("begin", "begin-study", activity="study", actor=desk.table.cast["Anya Rook"], kind="study",
                        intent="Learn the tide cant", required=20 * DAY)


def commitment(desk: Desk, commitment_id: str) -> dict:
    return next(item for item in desk.overview()["commitments"]["commitments"] if item["commitment"] == commitment_id)


# -- the page and its coverage ----------------------------------------------------------------------------------
def test_the_page_registers_hidden_on_its_own_route_and_needs_its_ops():
    page = {item.id: item for item in load_pages(STATIC)}["time"]
    assert (page.route, page.slot, page.owner, page.title) == ("time", "hidden", "116", "Time and commitments")
    assert set(page.requires) == {"campaign.chats", "time.overview", "time.coverage"}
    adapter = next(item for item in ADAPTERS if item.name == "time")
    assert adapter.owner == "116"
    assert {"overview", "coverage", *WRITES} == set(adapter.ops)


def test_every_wave_9_slice_is_on_a_registered_screen_or_named_with_why():
    routes = {page.route for page in load_pages(STATIC)}
    assert [item["slice"] for item in FEATURES] == [f"{n:03d}" for n in range(73, 88)]
    for item in FEATURES:
        if item["status"] == "screen":
            assert item["route"] in routes, f"{item['slice']}'s screen {item['route']} is a registered page"
            assert item["where"] and item["reason"] is None
        elif item["status"] == "here":
            assert item["route"] == "time" and item["slice"] in ("074", "076", "077")
        else:
            assert item["status"] == "unavailable" and item["route"] is None and item["reason"], item
    assert {item["slice"] for item in FEATURES if item["status"] == "here"} == {"074", "076", "077"}


def test_coverage_is_answered_to_anyone_and_matches_the_table(tmp_path):
    with timed(tmp_path) as (table, _turn):
        assert table("time.coverage")["features"] == [dict(item) for item in FEATURES]


# -- the proving scenario ---------------------------------------------------------------------------------------
def test_the_proving_scenario_each_change_reviewed_then_made_once(tmp_path):
    with timed(tmp_path) as (table, _turn):
        desk = Desk(table)
        first = desk.overview()
        assert first["clock"]["now"] == {"date": "1024-03-01", "time": "10:00", "fixed_day": first["clock"]["now"]["fixed_day"],
                                         "minute": 600}
        assert {item["name"]: item["role"] for item in first["cast"]} == {"Mara Vell": "npc", "Anya Rook": "player",
                                                                         "Tamsin Hale": "player"}
        assert first["offscreen"]["adoption"]["available"] is True

        # a commitment offered with a new deadline, and refused by its NPC obligor
        planned, done = desk.made("commitment", offer_map(desk))
        assert planned["deadline"] == {"deadline": "map-due", "schedules": True, "label": "Map due",
                                       "due": planned["deadline"]["due"]}
        assert planned["deadline"]["due"]["date"] == "1024-03-10" and planned["files"] == [
            f"campaigns/{desk.campaign}/scope.json"]
        assert done["already_applied"] is False
        refuse = desk.request("record", "refuse-map", commitment="map", status="refused", cites=["ev-refuses"],
                              note="She will not carry it")
        planned, _ = desk.made("commitment", refuse)
        assert (planned["from"], planned["status"], [item["name"] for item in planned["decided_by"]]) == (
            "offered", "refused", ["Mara Vell"])
        assert planned["keys"] == [], "an NPC's decision is the director's alone"

        # a plan for the guild under its leader's decision, racing the deadline
        planned, _ = desk.made("offscreen", adopt_docks(desk))
        assert planned["decision"]["leader"]["name"] == "Mara Vell" and planned["decision"]["choice"] == "Seize the docks"
        assert [step["due"]["date"] for step in planned["steps"]] == ["1024-03-06", "1024-03-16"]
        assert planned["deadline"]["race"] == "missed"

        # a 20-day study for Anya, begun, then pursued across the deadline: a flag, and nothing more
        desk.made("downtime", begin_study(desk))
        pursue = desk.request("pursue", "pursue-study", activity="study", minutes=20 * DAY, note="Anya follows the cant")
        planned, done = desk.made("downtime", pursue)
        assert (planned["before"]["date"], planned["after"]["date"]) == ("1024-03-01", "1024-03-21")
        assert planned["flags"] == [{"deadline": "map-due", "label": "Map due"}]
        assert planned["credited"] == 20 * DAY and planned["completes"] is True
        assert done["already_applied"] is False
        after = scope_bytes(table)
        again = desk.commit("downtime", pursue)
        assert again["already_applied"] is True and again["credited"] == 20 * DAY
        assert scope_bytes(table) == after, "a retried commit writes nothing"

        now = desk.overview()
        assert now["clock"]["now"]["date"] == "1024-03-21", "time passed once"
        study = now["downtime"]["activities"][0]
        assert (study["status"], study["progress"], study["outcome"]) == ("complete", 20 * DAY, "Anya follows the cant")
        assert [item["flagged_by"] for item in now["clock"]["deadlines"]] == ["pursue-study"]

        # the refused commitment still has its deadline, now passed, and is not overdue
        refused_map = commitment(desk, "map")
        assert refused_map["status"] == "refused"
        assert refused_map["deadline"]["found"] and refused_map["deadline"]["passed"]
        assert refused_map["deadline"]["label"] == "Map due" and refused_map["deadline"]["due"]["date"] == "1024-03-10"
        assert refused_map["overdue"] is False and refused_map["late"] is None, "a refusal is neither overdue nor late"
        assert refused_map["next"] == [], "refused is final"

        # the pursuit's advance is pending for the plans; applied, the guild's steps develop and the race is lost
        assert [item["advance"] for item in now["offscreen"]["pending"]] == ["pursue-study"]
        assert now["offscreen"]["adoption"] == {**now["offscreen"]["adoption"], "available": False, "reason": "pending_skips"}
        events_before = EvidenceLibrary(table.table.store).read(desk.campaign, requested_by=DIRECTOR).knowledge.events
        skip = desk.request("skip", None, advance="pursue-study", budget=4)
        planned, _ = desk.made("offscreen", skip)
        assert planned["files"] == [f"campaigns/{desk.campaign}/scope.json"]
        assert [item["label"] for item in planned["developments"]] == ["Gather the bribe money", "Take the docks"]
        assert planned["deadlines"] == [{"plan": "docks", "deadline": "map-due", "outcome": "missed", "label": "Map due"}]
        plans = desk.overview()["offscreen"]
        assert plans["pending"] == [] and [step["flagged"] for step in plans["plans"][0]["steps"]] == [True, True]
        assert plans["plans"][0]["complete"] is True
        events_after = EvidenceLibrary(table.table.store).read(desk.campaign, requested_by=DIRECTOR).knowledge.events
        assert events_after == events_before, "a development delivers no knowledge, event or report"
        assert desk.commit("offscreen", skip)["already_applied"] is True


def test_the_owner_decides_for_their_own_character_with_their_own_key(tmp_path):
    with timed(tmp_path) as (table, _turn):
        desk = Desk(table)
        t = table.cast
        desk.made("commitment", desk.request("offer", "offer-promise", commitment="promise", kind="promise",
                                             summary="Bring Mara the map", owed_by=[t["Anya Rook"]],
                                             owed_to=[t["Mara Vell"]], cites=["ev-asks"]))
        item = commitment(desk, "promise")
        assert item["deciders"] == {"available": True, "keys": [{"actor": t["Anya Rook"], "name": "Anya Rook",
                                                                  "kind": "character"}]}
        accept = desk.request("record", "accept-promise", commitment="promise", status="accepted", cites=["ev-promise"])
        planned = desk.plan("commitment", accept)
        assert [key["name"] for key in planned["keys"]] == ["Anya Rook"]
        assert [(item["name"], item["text"]) for item in planned["backing"]] == [("Anya Rook", SPOKEN)]
        assert planned["backing"][0]["turn_number"] is not None
        controls = planned["reviewed_controls"]
        assert [who for _actor, who in controls] == ["human:owner"]
        before = scope_bytes(table)
        assert refused(table, "time.commitment_commit", **accept, reviewed_controls=controls).reason == "player_key_required"
        assert refused(table, "time.commitment_commit", **accept, endorse=True).reason == "review_required"
        assert scope_bytes(table) == before
        done = desk.commit("commitment", accept, endorse=True, reviewed_controls=controls)
        assert done["status"] == "accepted" and done["already_applied"] is False
        assert done["backing"][0]["text"] == SPOKEN
        step = commitment(desk, "promise")["steps"][0]
        assert [(item["name"], item["player"]) for item in step["backed_by"]] == [("Anya Rook", "human:owner")]
        # a retry needs no key again: the domain finds its receipt
        assert desk.commit("commitment", accept)["already_applied"] is True
        # a decision citing an event outside the chat's turns is refused before anything is asked of 077
        other = {**accept, "operation_id": "accept-2", "cites": ["ev-asks"], "expected": desk.overview()["etag"]}
        assert refused(table, "time.commitment_plan", **other).reason == "other_chat"
        # withdrawing the owner's own backed step gives the owner's key too
        void = desk.request("void", "void-accept", target="accept-promise", reason="Recorded in error")
        assert [(key["name"], key["player"]) for key in desk.plan("commitment", void)["keys"]] == [("Anya Rook", "human:owner")]
        assert refused(table, "time.commitment_commit", **void).reason == "player_key_required"
        assert desk.commit("commitment", void, endorse=True)["withdraws"] == "step"
        assert commitment(desk, "promise")["status"] == "offered"


# -- refusals ---------------------------------------------------------------------------------------------------
def test_the_table_reads_and_writes_none_of_it(tmp_path):
    with timed(tmp_path) as (table, _turn):
        desk = Desk(table)
        before = scope_bytes(table)
        for op in ("overview", *WRITES):
            for audience in ({}, {"audience": "table"}):
                assert refused(table, f"time.{op}", campaign=desk.campaign, chat=desk.chat,
                               **audience).reason == "director_only", op
        assert scope_bytes(table) == before


def test_a_change_needs_a_review_and_a_stale_one_is_refused(tmp_path):
    with timed(tmp_path) as (table, _turn):
        desk = Desk(table)
        before = scope_bytes(table)
        begin = begin_study(desk)
        no_review = {key: value for key, value in begin.items() if key != "expected"}
        assert refused(table, "time.downtime_plan", **no_review).reason == "review_required"
        desk.plan("downtime", begin)
        # something else changes the scope after the review: the reviewed change is refused, nothing is written
        desk.made("commitment", offer_map(desk))
        changed = scope_bytes(table)
        assert refused(table, "time.downtime_commit", **begin).reason == "changed"
        assert scope_bytes(table) == changed
        assert before != changed


def test_an_operation_id_names_one_request(tmp_path):
    with timed(tmp_path) as (table, _turn):
        desk = Desk(table)
        desk.made("downtime", begin_study(desk))
        other = {**begin_study(desk), "intent": "Learn to sail"}
        before = scope_bytes(table)
        assert refused(table, "time.downtime_plan", **other).reason == "operation_mismatch"
        offer = offer_map(desk)
        desk.made("commitment", offer)
        assert refused(table, "time.commitment_plan", **{**offer_map(desk), "summary": "Something else"}
                       ).reason == "operation_mismatch"
        assert desk.commit("commitment", offer)["already_applied"] is True, "a retry is checked before the etag"
        assert scope_bytes(table) != before


def test_domain_refusals_keep_their_reasons_and_write_nothing(tmp_path):
    with timed(tmp_path) as (table, _turn):
        desk = Desk(table)
        t = table.cast
        before = scope_bytes(table)
        cases = {
            ("downtime", "no_actor"): begin_study(desk) | {"actor": str(guild(table))},
            ("downtime", "unknown_activity"): desk.request("pursue", "p", activity="nope", minutes=60),
            ("downtime", "invalid_field"): begin_study(desk) | {"kind": "nap"},
            ("offscreen", "unknown_group"): adopt_docks(desk) | {"group": t["Anya Rook"]},
            ("commitment", "self_owed"): offer_map(desk) | {"owed_to": [t["Mara Vell"]]},
            ("commitment", "citation_missing"): offer_map(desk) | {"cites": ["no-such-event"]},
            ("commitment", "unknown_commitment"): desk.request("record", "r", commitment="nope", status="accepted",
                                                               cites=["ev-asks"]),
        }
        for (domain, reason), request in cases.items():
            assert refused(table, f"time.{domain}_plan", **request).reason == reason, (domain, reason)
            assert refused(table, f"time.{domain}_commit", **request).reason == reason, (domain, reason)
        assert refused(table, "time.downtime_plan", **begin_study(desk) | {"chat": "campaign:other#chat/t"}
                       ).reason == "invalid_field"
        assert scope_bytes(table) == before, "no refusal writes anything"
        # a decision is read against a chat's control records: with no chat named, there are none
        desk.made("commitment", offer_map(desk))
        unchatted = {key: value for key, value in desk.request("record", "r", commitment="map", status="accepted",
                                                               cites=["ev-asks"]).items() if key != "chat"}
        after = scope_bytes(table)
        assert refused(table, "time.commitment_plan", **unchatted).reason == "no_controls"
        assert scope_bytes(table) == after


def test_a_player_s_character_is_theirs_to_decide_for(tmp_path):
    with timed(tmp_path) as (table, _turn):
        desk = Desk(table)
        t = table.cast
        desk.made("commitment", desk.request("offer", "offer-bob", commitment="bob", kind="promise",
                                             summary="Tamsin brings the rope", owed_by=[t["Tamsin Hale"]],
                                             owed_to=[t["Mara Vell"]], cites=["ev-asks"]))
        item = commitment(desk, "bob")
        assert item["deciders"]["available"] is False and item["deciders"]["reason"] == "not_your_character"
        before = scope_bytes(table)
        accept = desk.request("record", "bob-accepts", commitment="bob", status="accepted", cites=["ev-promise"])
        assert refused(table, "time.commitment_plan", **accept).reason == "not_your_character"
        assert refused(table, "time.commitment_commit", **accept, endorse=True).reason == "not_your_character"
        # the director's adjudication is still the director's
        lapse = desk.request("record", "bob-lapses", commitment="bob", status="lapsed", cites=["ev-guild"])
        assert desk.made("commitment", lapse)[1]["status"] == "lapsed"
        assert scope_bytes(table) != before
        # an offer the director made is voidable by the director
        void = desk.request("void", "void-bob", target="offer-bob", reason="Recorded in error")
        planned, done = desk.made("commitment", void)
        assert planned["withdraws"] == "commitment" and done["already_applied"] is False


def test_an_uninitialized_clock_spends_no_time_and_estimates_none(tmp_path):
    with timed(tmp_path, clock=False) as (table, _turn):
        desk = Desk(table)
        first = desk.overview()
        assert first["clock"] == {"started": False, "calendar": None, "now": None, "deadlines": []}
        assert first["downtime"]["started"] is False
        assert first["offscreen"]["adoption"]["reason"] == "uninitialized"
        desk.made("downtime", begin_study(desk))  # beginning spends no time
        before = scope_bytes(table)
        pursue = desk.request("pursue", "pursue-study", activity="study", minutes=DAY)
        assert refused(table, "time.downtime_plan", **pursue).reason == "uninitialized"
        assert refused(table, "time.offscreen_plan", **desk.request("skip", None, advance="nope", budget=1)
                       ).reason == "uninitialized"
        assert refused(table, "time.commitment_plan", **offer_map(desk)).reason == "uninitialized"
        assert scope_bytes(table) == before


def test_adoption_needs_a_group_with_a_leader_the_director_controls(tmp_path):
    (tmp_path / "none").mkdir()
    (tmp_path / "led").mkdir()
    with timed(tmp_path / "none", group=False) as (table, _turn):
        adoption = Desk(table).overview()["offscreen"]["adoption"]
        assert (adoption["available"], adoption["reason"]) == (False, "no_group")
    with timed(tmp_path / "led") as (table, _turn):
        desk = Desk(table)
        before = scope_bytes(table)
        # Anya is the owner's character, not an NPC the director controls: 057 refuses her as the leader
        led = adopt_docks(desk) | {"leader": table.cast["Anya Rook"]}
        assert refused(table, "time.offscreen_plan", **led).reason == "npc_authority"
        assert scope_bytes(table) == before


def test_only_supported_events_are_offered_to_cite_newest_first_with_their_turn(tmp_path):
    with timed(tmp_path) as (table, turn):
        citable = Desk(table).overview()["commitments"]["citable"]
        assert [item["event"] for item in citable] == ["ev-promise", "ev-guild", "ev-refuses", "ev-asks"]
        promise = citable[0]
        assert promise["turn"] == turn and promise["turn_number"] is not None
        assert all(item["turn_number"] is None for item in citable[1:]), "the others are of no turn of this chat"


def test_a_decision_reviewed_under_one_control_is_not_made_under_another(tmp_path):
    with timed(tmp_path) as (table, _turn):
        desk = Desk(table)
        t = table.cast
        desk.made("commitment", desk.request("offer", "offer-promise", commitment="promise", kind="promise",
                                             summary="Bring Mara the map", owed_by=[t["Anya Rook"]],
                                             owed_to=[t["Mara Vell"]], cites=["ev-asks"]))
        desk.made("commitment", offer_map(desk))
        before = scope_bytes(table)
        # the owner's decision, reviewed with Anya as the owner's: a commit under other controls is not it
        accept = desk.request("record", "accept-promise", commitment="promise", status="accepted", cites=["ev-promise"])
        planned = desk.plan("commitment", accept)
        anya = t["Anya Rook"].rsplit("/", 1)[1]
        assert planned["reviewed_controls"] == [[anya, "human:owner"]]
        assert refused(table, "time.commitment_commit", **accept, endorse=True,
                       reviewed_controls=[[anya, "director"]]).reason == "changed"
        # the director's decision for Mara, reviewed with no key: a key given for it is not what was reviewed
        refuse = desk.request("record", "map-refused", commitment="map", status="refused", cites=["ev-refuses"])
        reviewed = desk.plan("commitment", refuse)
        assert reviewed["keys"] == []
        assert refused(table, "time.commitment_commit", **refuse, endorse=True,
                       reviewed_controls=reviewed["reviewed_controls"]).reason == "changed"
        # Mara handed to the owner after the review, which the scope's etag cannot see: rebuilt now it is the
        # owner's decision, resting on a turn of this chat, which the director's citation is not
        table("seat", chat=desk.chat, actor=t["Mara Vell"], player="human:owner")
        assert refused(table, "time.commitment_commit", **refuse,
                       reviewed_controls=reviewed["reviewed_controls"]).reason == "other_chat"
        assert scope_bytes(table) == before
        assert commitment(desk, "map")["status"] == "offered"


def test_another_player_s_character_s_downtime_is_theirs(tmp_path):
    with timed(tmp_path) as (table, _turn):
        desk = Desk(table)
        t = table.cast
        cast = {item["name"]: item["downtime"] for item in desk.overview()["cast"]}
        assert cast["Anya Rook"]["available"] and cast["Mara Vell"]["available"]
        assert (cast["Tamsin Hale"]["available"], cast["Tamsin Hale"]["reason"]) == (False, "not_your_character")
        before = scope_bytes(table)
        tamsin = begin_study(desk) | {"operation_id": "begin-tamsin", "activity": "rope", "actor": t["Tamsin Hale"]}
        assert refused(table, "time.downtime_plan", **tamsin).reason == "not_your_character"
        assert refused(table, "time.downtime_commit", **tamsin).reason == "not_your_character"
        assert scope_bytes(table) == before
        # Mara's errand, begun while she is the director's, is Bob's to run once she is handed to him
        errand = begin_study(desk) | {"operation_id": "begin-errand", "activity": "errand", "actor": t["Mara Vell"]}
        desk.made("downtime", errand)
        table("seat", chat=desk.chat, actor=t["Mara Vell"], player=BOB)
        found = {item["activity"]: item for item in desk.overview()["downtime"]["activities"]}
        assert (found["errand"]["whose"]["available"], found["errand"]["whose"]["reason"]) == (False, "not_your_character")
        after = scope_bytes(table)
        pursue = desk.request("pursue", "pursue-errand", activity="errand", minutes=DAY)
        assert refused(table, "time.downtime_plan", **pursue).reason == "not_your_character"
        abandon = desk.request("abandon", "drop-errand", activity="errand", note="Bored")
        assert refused(table, "time.downtime_commit", **abandon).reason == "not_your_character"
        assert scope_bytes(table) == after
        # a retry of what was already made is still the domain's to answer
        assert desk.commit("downtime", errand)["already_applied"] is True


def test_a_group_with_no_member_the_director_controls_has_no_leader_here(tmp_path):
    with timed(tmp_path, member="Anya Rook") as (table, _turn):
        desk = Desk(table)
        adoption = desk.overview()["offscreen"]["adoption"]
        assert (adoption["available"], adoption["reason"]) == (False, "no_leader")
        [found] = adoption["groups"]
        assert [(item["name"], item["may_decide"]) for item in found["members"]] == [("Anya Rook", False)]
        before = scope_bytes(table)
        led = adopt_docks(desk) | {"leader": table.cast["Anya Rook"]}
        assert refused(table, "time.offscreen_plan", **led).reason == "npc_authority"
        assert scope_bytes(table) == before


# -- the page end to end ----------------------------------------------------------------------------------------
@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_director_runs_time_on_the_page(tmp_path):
    with timed(tmp_path) as (table, _turn):
        result = node_e2e(table, "time.e2e.mjs", {}, NODE)
        desk = Desk(table)
        now = desk.overview()
        if result.returncode == 0:
            assert now["clock"]["now"]["date"] == "1024-03-21", "time passed once, however often the page sent it"
            assert now["downtime"]["activities"][0]["progress"] == 20 * DAY
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-3000:]
    assert "# pass 1" in result.stdout
