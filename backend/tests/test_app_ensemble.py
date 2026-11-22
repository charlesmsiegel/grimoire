"""Sprint 115's preview of several NPCs answering at once (051), through the ``ensemble.*`` adapter ops and its page.

The card chat has two NPCs on stage (Mara Vell and Tamsin Hale, both the director's) and Anya Rook, the owner's
character. The director authors one bid per NPC and previews the floor 051 gives them: who interjects first, who
answers, who adds a side remark, who stays meaningfully silent, and, while the player is still speaking, that every
NPC waits. The preview is an unaccepted proposal: nothing is written, nothing is said in the chat and no model is
asked; running or accepting an exchange is named as not available. The plan is the director's, and each
character's own projection shows only what it may see. The page runs end to end under Node
(``js/ensemble.e2e.mjs``). Synthetic library; no model is called.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from table_fixtures import DIRECTOR_VIEW, node_e2e, played

from grimoire.app.adapters.ensemble import MAX_BIDS, NOT_AVAILABLE
from grimoire.app.pages import load_pages
from grimoire.textapi import ApiRefused

NODE = shutil.which("node")
STATIC = Path(__file__).resolve().parents[1] / "src" / "grimoire" / "app" / "static"


def refused(table, op: str, **args) -> ApiRefused:
    with pytest.raises(ApiRefused) as caught:
        table(op, **args)
    return caught.value


def library_bytes(table) -> dict[str, bytes]:
    root = table.table.store.root
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*"))
            if path.is_file() and "authority" not in path.parts and not path.name.endswith((".lock", "-journal"))}


def moment(table, **more) -> dict:
    anya, mara, tamsin = (table.cast[name] for name in ("Anya Rook", "Mara Vell", "Tamsin Hale"))
    return {"chat": str(table.chat), **DIRECTOR_VIEW, "initiator": anya, "addressees": [mara], "floor": mara,
            "player_speaking": False, "bids": [
                {"actor": mara, "kind": "answer", "addressees": [anya], "audience": [mara, anya, tamsin],
                 "reason": "She owes Anya an answer about the lamp.", "grounding": "addressed"},
                {"actor": tamsin, "kind": "aside", "addressees": [mara], "audience": [tamsin, mara],
                 "reason": "Tamsin suspects the lamp was tampered with.", "grounding": "heard", "heard_index": 0},
            ], **more}


def test_the_page_registers_hidden_on_its_own_route_and_needs_its_ops():
    page = {item.id: item for item in load_pages(STATIC)}["ensemble"]
    assert (page.route, page.slot, page.owner) == ("ensemble", "hidden", "115")
    assert set(page.requires) == {"ensemble.exchange", "ensemble.preview"}


def test_the_exchange_is_the_directors_and_names_who_is_present_and_what_is_not_available(tmp_path):
    with played(tmp_path, bob=False) as table:
        chat = str(table.chat)
        assert refused(table, "ensemble.exchange", chat=chat).reason == "director_only"
        assert refused(table, "ensemble.preview", **{**moment(table), "audience": "table"}).reason == "director_only"
        assert refused(table, "ensemble.exchange", chat=chat, audience="everyone").reason == "invalid_field"
        exchange = table("ensemble.exchange", chat=chat, **DIRECTOR_VIEW)
        assert {(item["name"], item["role"]) for item in exchange["present"]} == {
            ("Anya Rook", "player"), ("Mara Vell", "npc"), ("Tamsin Hale", "npc")}
        assert exchange["not_available"] == NOT_AVAILABLE and exchange["max_bids"] == MAX_BIDS
        assert set(exchange["kinds"]) == {"answer", "interject", "aside", "silence", "nonverbal"}


def test_a_preview_proposes_the_floor_writes_nothing_and_keeps_each_view_to_what_it_may_see(tmp_path):
    with played(tmp_path, bob=False) as table:
        before = library_bytes(table)
        turns = table("open", chat=str(table.chat))["turns"]
        preview = table("ensemble.preview", **moment(table))
        assert preview["proposal"] is True and preview["accepted"] is False
        assert [(item["position"], item["name"], item["kind"]) for item in preview["order"]] == [
            (1, "Mara Vell", "answer"), (2, "Tamsin Hale", "aside")]
        assert preview["order"][1]["reason"] == "Tamsin suspects the lamp was tampered with."
        assert preview["calls"] == 2 and preview["quiet"] == [] and preview["held"] == []
        assert preview["not_available"] == NOT_AVAILABLE
        views = {view["name"]: view["sees"] for view in preview["views"]}
        # the side remark reaches only its audience: Anya, outside it, is aware of Mara's answer alone
        assert [item["name"] for item in views["Anya Rook"]] == ["Mara Vell"]
        assert views["Anya Rook"][0]["reason"] is None, "another character's reason is never shown to it"
        own = [item for item in views["Tamsin Hale"] if item["name"] == "Tamsin Hale"]
        assert own[0]["reason"] == "Tamsin suspects the lamp was tampered with."
        assert all(item["reason"] is None for item in views["Tamsin Hale"] if item["name"] != "Tamsin Hale")
        # nothing is written and nothing is said in the chat
        assert library_bytes(table) == before and table("open", chat=str(table.chat))["turns"] == turns


def test_an_interjection_goes_first_a_silence_is_quiet_and_a_speaking_player_holds_every_npc(tmp_path):
    with played(tmp_path, bob=False) as table:
        anya, mara, tamsin = (table.cast[name] for name in ("Anya Rook", "Mara Vell", "Tamsin Hale"))
        args = moment(table)
        args["bids"][1] = {"actor": tamsin, "kind": "interject", "addressees": [anya], "audience": [],
                           "reason": "She cannot let Mara answer first.", "grounding": "intention"}
        args["bids"][1]["audience"] = [tamsin, anya, mara]
        first = table("ensemble.preview", **args)
        assert [item["name"] for item in first["order"]] == ["Tamsin Hale", "Mara Vell"]
        args["bids"][1] = {"actor": tamsin, "kind": "silence", "addressees": [], "audience": [tamsin, anya, mara],
                           "reason": "She is waiting to see who lies.", "grounding": "listening"}
        quiet = table("ensemble.preview", **args)
        assert [item["name"] for item in quiet["order"]] == ["Mara Vell"] and quiet["calls"] == 1
        assert [(item["name"], item["kind"]) for item in quiet["quiet"]] == [("Tamsin Hale", "silence")]
        held = table("ensemble.preview", **{**args, "player_speaking": True, "floor": anya})
        assert held["order"] == [] and held["quiet"] == [] and held["calls"] == 0
        assert {item["name"] for item in held["held"]} == {"Mara Vell", "Tamsin Hale"}


def test_a_bad_bid_is_refused_with_051s_reason_never_a_server_error(tmp_path):
    with played(tmp_path, bob=False) as table:
        anya, mara, tamsin = (table.cast[name] for name in ("Anya Rook", "Mara Vell", "Tamsin Hale"))
        base = moment(table)

        def bid(**changes):
            return {**base, "bids": [{**base["bids"][0], **changes}]}

        # a player's character is never voiced: its player speaks for it
        assert refused(table, "ensemble.preview", **bid(actor=anya, addressees=[mara], audience=[anya, mara],
                                                        grounding="intention")).reason == "player_speaker"
        assert refused(table, "ensemble.preview", **bid(kind="shout")).reason == "bad_bid"
        assert refused(table, "ensemble.preview", **bid(grounding="addressed", addressees=[anya], actor=tamsin,
                                                        audience=[tamsin, anya])).reason == "bad_grounding"
        assert refused(table, "ensemble.preview", **bid(audience=[mara])).reason == "bad_audience"
        assert refused(table, "ensemble.preview", **bid(reason="")).reason == "bad_bid"
        assert refused(table, "ensemble.preview", **{**base, "bids": [base["bids"][0], base["bids"][0]]}
                       ).reason == "duplicate_bid"
        assert refused(table, "ensemble.preview", **bid(heard_index="first")).reason == "invalid_field"
        assert refused(table, "ensemble.preview", **bid(actor="campaign:other/character/x")).reason == "invalid_field"
        assert refused(table, "ensemble.preview", **{**base, "bids": [base["bids"][0]] * (MAX_BIDS + 1)}
                       ).reason == "invalid_field"
        assert refused(table, "ensemble.preview", **{**base, "player_speaking": "yes"}).reason == "invalid_field"
        assert refused(table, "ensemble.preview", **{**base, "player_speaking": True}).reason == "bad_floor"
        # a chat that is not there, or is not a chat reference, is refused with its reason
        missing = str(table.chat).rsplit("/", 1)[0] + "/t-none"
        assert refused(table, "ensemble.exchange", chat=missing, **DIRECTOR_VIEW).reason
        assert refused(table, "ensemble.exchange", chat="nonsense", **DIRECTOR_VIEW).reason == "invalid_field"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_director_plans_and_previews_an_exchange_on_the_page(tmp_path):
    with played(tmp_path, bob=False) as table:
        before = library_bytes(table)
        result = node_e2e(table, "ensemble.e2e.mjs", {}, NODE)
        assert library_bytes(table) == before, "a preview from the page writes nothing"
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-3000:]
    assert "# pass 1" in result.stdout
