"""Sprint 116's attitude check (078), through the ``attitudes.*`` adapter ops and its page.

The card chat's cast stands in for 078's quay: Mara Vell is the envoy (an NPC, the director's), Anya Rook the
newcomer (the owner's character) and Tamsin Hale the dockhand (a second NPC). Anya told Mara "your guild are thieves
and you are their lackey"; Tamsin was in the upper town, in the rain, and never heard it. The director checks
Mara's resentment of Anya from the report Mara received (admitted) and the same attitude for Tamsin, who never
heard it (refused ``unavailable_grounds`` with Mara's report, ``unrelated_grounds`` with Tamsin's own rain). A
player character's feelings are refused (``pc_interiority``) and the table's view is refused (``director_only``).
078 has no store: nothing is written, and each check admits the page's held attitudes again from scratch. The page
runs end to end under Node (``js/attitudes.e2e.mjs``). Synthetic library; no model is called.
"""

from __future__ import annotations

import contextlib
import shutil
from pathlib import Path

import pytest
from table_fixtures import DIRECTOR_VIEW, OWNER, node_e2e, played

from grimoire.app.adapters.attitudes import MAX_HELD, NOT_AVAILABLE
from grimoire.app.pages import load_pages
from grimoire.core import Event, Moment, Perception, Proposition, Report, Subject
from grimoire.core.identity import InstanceRef
from grimoire.evidencestore import EvidenceLibrary
from grimoire.store import RecordEnvelope
from grimoire.textapi import ApiRefused

NODE = shutil.which("node")
STATIC = Path(__file__).resolve().parents[1] / "src" / "grimoire" / "app" / "static"
LACKEY = "Your guild are thieves and you are their lackey."
RAIN = "Cold rain on the neck."
GUILD = "The harbor guild"


@contextlib.contextmanager
def quay(tmp_path: Path):
    """The card chat with two NPCs, the harbor guild, and the evidence of the quay: Mara's received report and
    Tamsin's rain."""
    with played(tmp_path, bob=False) as table:
        c = table.chat.campaign_id
        mara, anya, tamsin = (InstanceRef.parse(table.cast[name]) for name in ("Mara Vell", "Anya Rook", "Tamsin Hale"))
        guild = InstanceRef(c, "group", "harbor-guild")
        txn = table.table.store.transaction()
        txn.put(RecordEnvelope(guild, {"local_name": GUILD}, owner=OWNER), None)
        txn.commit(table.table.owner)
        quay_at, town_at = Moment(1, "quay", "t1"), Moment(2, "town", "t1")
        EvidenceLibrary(table.table.store, table.table.owner, owner=OWNER).append(c, [
            Event(c, "ev-insult", "The newcomer insulted the envoy on the quay.", quay_at, "director", (anya, mara)),
            Proposition(c, "p-lackey", LACKEY, quay_at, "director",
                        (Subject("instance", str(guild)), Subject("instance", str(mara)))),
            Report(c, "heard", mara, "p-lackey", "told", quay_at, "director", True, sender=anya, attributed_to=anya,
                   addressed=True, sent_at=quay_at),
            Event(c, "ev-rain", "Rain fell on the upper town.", town_at, "director"),
            Perception(c, "wet", tamsin, "ev-rain", "touch", RAIN, town_at, "director", confirmed=True),
        ]).commit()
        table.refs = {"mara": str(mara), "anya": str(anya), "tamsin": str(tamsin), "guild": str(guild)}  # type: ignore[attr-defined]
        yield table


def refused(table, op: str, **args) -> ApiRefused:
    with pytest.raises(ApiRefused) as caught:
        table(op, **args)
    return caught.value


def library_bytes(table) -> dict[str, bytes]:
    root = table.table.store.root
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*"))
            if path.is_file() and "authority" not in path.parts and not path.name.endswith((".lock", "-journal"))}


def resentment(table, **changes) -> dict:
    refs = table.refs
    return {"id": "a1", "holder": refs["mara"], "toward": refs["anya"], "dimension": "resentment",
            "account": "Resents being called a lackey in front of the dockhands.", "degree": 3,
            "evidence": ["heard"], "beliefs": [], **changes}


def check(table, attitude: dict, held=()) -> dict:
    return table("attitudes.check", chat=str(table.chat), **DIRECTOR_VIEW, attitude=attitude, held=list(held))


def test_the_page_registers_hidden_on_its_own_route_and_needs_its_ops():
    page = {item.id: item for item in load_pages(STATIC)}["attitudes"]
    assert (page.route, page.slot, page.owner) == ("attitudes", "hidden", "116")
    assert set(page.requires) == {"attitudes.cast", "attitudes.grounds", "attitudes.check"}


def test_attitudes_are_the_directors_the_table_view_is_refused(tmp_path):
    with quay(tmp_path) as table:
        chat = str(table.chat)
        before = library_bytes(table)
        for op, args in (("attitudes.cast", {}), ("attitudes.grounds", {"holder": table.refs["mara"]}),
                         ("attitudes.check", {"attitude": resentment(table)})):
            assert refused(table, op, chat=chat, **args).reason == "director_only"
            assert refused(table, op, chat=chat, audience="table", **args).reason == "director_only"
        assert refused(table, "attitudes.cast", chat=chat, audience="everyone").reason == "invalid_field"
        assert library_bytes(table) == before


def test_who_may_hold_an_attitude_and_whom_it_may_point_at(tmp_path):
    with quay(tmp_path) as table:
        setting = table("attitudes.cast", chat=str(table.chat), **DIRECTOR_VIEW)
        holds = {item["name"]: (item["may_hold"], item["why_not"]) for item in setting["cast"]}
        assert holds == {"Mara Vell": (True, None), "Tamsin Hale": (True, None),
                         "Anya Rook": (False, "a player character's feelings are its player's")}
        assert setting["groups"] == [{"actor": table.refs["guild"], "name": GUILD}]
        assert setting["dimensions"] == ["trust", "affection", "fear", "rivalry", "resentment", "debt"]
        assert setting["saved"] is False and setting["not_available"] == NOT_AVAILABLE and setting["now"] == 2
        assert setting["max_held"] == MAX_HELD


def test_grounds_are_the_holders_own_as_the_hub_shows_them(tmp_path):
    with quay(tmp_path) as table:
        chat = str(table.chat)
        mara = table("attitudes.grounds", chat=chat, holder=table.refs["mara"], **DIRECTOR_VIEW)
        assert [(item["evidence"], item["content"]) for item in mara["evidence"]] == [("heard", LACKEY)]
        assert mara["evidence"][0]["attributed_to"]["name"] == "Anya Rook"
        tamsin = table("attitudes.grounds", chat=chat, holder=table.refs["tamsin"], **DIRECTOR_VIEW)
        assert [(item["evidence"], item["content"]) for item in tamsin["evidence"]] == [("wet", RAIN)]
        hub = table("campaign.knowledge", chat=chat, actor=table.refs["tamsin"], **DIRECTOR_VIEW)
        assert [item["evidence"] for item in hub["actors"][0]["observations"]] == ["wet"], "the hub's own view"
        assert refused(table, "attitudes.grounds", chat=chat, holder=table.refs["anya"],
                       **DIRECTOR_VIEW).reason == "pc_interiority"
        other = f"campaign:{table.chat.campaign_id}/character/nobody"
        assert refused(table, "attitudes.grounds", chat=chat, holder=other, **DIRECTOR_VIEW).reason == "no_actor"


def test_the_envoys_resentment_is_admitted_and_the_dockhand_who_never_heard_it_is_refused(tmp_path):
    with quay(tmp_path) as table:
        before = library_bytes(table)
        admitted = check(table, resentment(table))
        assert admitted["saved"] is False and admitted["unchanged"] is True
        assert admitted["verdict"]["admitted"] is True and admitted["verdict"]["reason"] is None
        assert admitted["held"] == ["a1"]
        (head,) = admitted["current"]
        assert (head["toward_name"], head["dimension"], head["degree"], head["evidence"]) == (
            "Anya Rook", "resentment", 3, ["heard"])
        # the dockhand never heard it: Mara's report is not Tamsin's, and Tamsin's own rain is about no one
        dockhand = resentment(table, id="a2", holder=table.refs["tamsin"])
        assert check(table, dockhand)["verdict"] == {"admitted": False, "reason": "unavailable_grounds", "attitude": None}
        assert check(table, {**dockhand, "evidence": ["wet"]})["verdict"]["reason"] == "unrelated_grounds"
        # a player character's feelings are its player's, whatever it heard
        pc = resentment(table, id="a3", holder=table.refs["anya"], toward=table.refs["mara"])
        assert check(table, pc)["verdict"]["reason"] == "pc_interiority"
        # 078's other refusals keep their reasons
        assert check(table, resentment(table, at=99))["verdict"]["reason"] == "future_moment"
        assert check(table, resentment(table, account=" "))["verdict"]["reason"] == "invalid_text"
        assert check(table, resentment(table, degree=-1))["verdict"]["reason"] == "invalid_degree"
        assert check(table, resentment(table, toward=table.refs["mara"]))["verdict"]["reason"] == "self_attitude"
        assert check(table, resentment(table, evidence=[]))["verdict"]["reason"] == "ungrounded_attitude"
        assert check(table, resentment(table, toward=f"campaign:{table.chat.campaign_id}/character/nobody")
                     )["verdict"]["reason"] == "unresolved_actor"
        # the guild: what Mara heard is overtly about it
        toward_guild = check(table, resentment(table, id="a4", toward=table.refs["guild"], dimension="rivalry",
                                               account="Will not let the guild be called thieves."))
        assert toward_guild["verdict"]["admitted"] is True
        assert toward_guild["verdict"]["attitude"]["toward_name"] == GUILD
        # malformed requests are refused before 078 is asked
        chat = str(table.chat)
        assert refused(table, "attitudes.check", chat=chat, **DIRECTOR_VIEW, attitude="x").reason == "invalid_field"
        assert refused(table, "attitudes.check", chat=chat, **DIRECTOR_VIEW,
                       attitude=resentment(table, degree="3")).reason == "invalid_field"
        assert refused(table, "attitudes.check", chat=chat, **DIRECTOR_VIEW, attitude=resentment(table),
                       held=[resentment(table)] * (MAX_HELD + 1)).reason == "invalid_field"
        assert library_bytes(table) == before, "nothing is ever written"


def test_the_held_attitudes_are_admitted_again_from_scratch_on_every_check(tmp_path):
    with quay(tmp_path) as table:
        held = [resentment(table)]
        trust = resentment(table, id="a2", dimension="trust", degree=-2, account="Will not take her at her word.")
        both = check(table, trust, held)
        assert both["verdict"]["admitted"] is True and both["held"] == ["a1", "a2"]
        assert [item["dimension"] for item in both["current"]] == ["trust", "resentment"]
        # the same chain again, unsuperseded, is 078's conflict, and is said as its verdict
        again = check(table, resentment(table, id="a3"), held)
        assert again["verdict"]["reason"] == "unsuperseded_conflict" and again["held"] == ["a1"]
        # a held attitude the client says was admitted is checked again: the dockhand's is not, with 078's reason
        forged = resentment(table, id="a9", holder=table.refs["tamsin"])
        assert refused(table, "attitudes.check", chat=str(table.chat), **DIRECTOR_VIEW, attitude=trust,
                       held=[forged]).reason == "unavailable_grounds"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_director_checks_attitudes_on_the_page(tmp_path):
    with quay(tmp_path) as table:
        before = library_bytes(table)
        result = node_e2e(table, "attitudes.e2e.mjs", {}, NODE)
        assert library_bytes(table) == before, "a check from the page writes nothing"
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-3000:]
    assert "# pass 1" in result.stdout
