"""Roadmap 12's write posture at the store (spec 6; 14 "Write posture"):
`reconcile.persist_investigation`, the trace a proposal carries, and the
checking of a final call.
"""

from __future__ import annotations

import pytest

import grimoire.store as store
from grimoire import tool_calls
from grimoire.store.continuity import candidates, reconcile, review
from grimoire.store.history import model as history_model
from grimoire.store.investigation import final, state, trace

from .test_investigation_routes import PAIR, _campaign, _found

PROPOSAL = {"decision": "duplicate", "from": "thread:recover-the-harbour-ledger",
            "to": "thread:ledger", "relation": "", "status": "", "reason": "One errand.",
            "evidence_scenes": []}


def _trace(**changes) -> dict:
    base = trace.build(run="r1", mode="continuity", task="investigation-continuity",
                       model="openrouter/vendor/reader", started="2026-10-14T00:00:00Z",
                       outcome="completed", limit="", steps=[], inspected=set(),
                       final={"decision": "duplicate"},
                       usage_block={"model_turns": 1, "tool_calls": 0, "decisions": 0,
                                    "seconds": 1.0, "cost": {}, "unpriced_calls": 1})
    return {**base, **changes}


@pytest.fixture
def found(client):
    cid, sid = _campaign(client)
    _found(client, cid)
    record = candidates.read(cid)["records"][PAIR]
    return cid, sid, record


def _persist(cid, record, proposal=PROPOSAL):
    return reconcile.persist_investigation(cid, PAIR, proposal,
                                           started_fingerprint=record["fingerprint"],
                                           started_proposal=record.get("proposal"))


def test_a_write_replaces_one_proposal_and_bumps_once(found, monkeypatch):
    cid, _sid, record = found
    before = candidates.read(cid)
    bumps = []
    real = store.revision.bump
    monkeypatch.setattr(store.revision, "bump", lambda c: bumps.append(c) or real(c))
    assert _persist(cid, record, {**PROPOSAL, "investigation": _trace()}) is True
    after = candidates.read(cid)
    assert bumps == [cid]
    assert after["records"][PAIR]["proposal"]["investigation"]["run"] == "r1"
    assert after["generation"] == before["generation"]
    assert after["basis"] == before["basis"]
    assert {k: v for k, v in after["records"].items() if k != PAIR} == \
        {k: v for k, v in before["records"].items() if k != PAIR}


def test_a_moved_fingerprint_drops_the_write(found):
    cid, _sid, _record = found
    assert reconcile.persist_investigation(cid, PAIR, PROPOSAL, started_fingerprint="fp1_old",
                                           started_proposal=None) is False
    assert candidates.read(cid)["records"][PAIR]["proposal"] is None


def test_a_changed_prior_proposal_drops_the_write(found):
    cid, _sid, record = found
    assert _persist(cid, record) is True
    # The run that started before that write held no proposal; it lands now.
    assert _persist(cid, record, {**PROPOSAL, "decision": "distinct"}) is False
    assert candidates.read(cid)["records"][PAIR]["proposal"]["decision"] == "duplicate"


def test_a_dismissed_finding_drops_the_write(found):
    cid, _sid, record = found
    review.dismiss(cid, PAIR, "dismiss", record["fingerprint"])
    assert _persist(cid, record) is False
    assert PAIR not in candidates.read(cid)["records"]


def test_a_sweep_that_started_earlier_is_not_superseded(found):
    cid, _sid, record = found
    earlier = reconcile.Sweep(reconcile.generation("sweep-earlier"), True)
    assert _persist(cid, record) is True
    landed = reconcile.persist_proposals(cid, earlier, {})
    assert landed["superseded"] is False


def test_the_trace_round_trips_and_a_misshapen_one_is_dropped_alone(found):
    cid, _sid, record = found
    assert trace.valid(_trace())
    assert _persist(cid, record, {**PROPOSAL, "investigation": _trace()})
    kept = candidates.read(cid)["records"][PAIR]["proposal"]
    assert kept["investigation"] == _trace()
    data = candidates.read(cid)
    data["records"][PAIR]["proposal"]["investigation"] = {"version": 99}
    candidates.write(cid, data)
    kept = candidates.read(cid)["records"][PAIR]["proposal"]
    assert "investigation" not in kept and kept["decision"] == "duplicate"


# ---- the final call ----
def _call(name, **args):
    return tool_calls.ToolCall(id="c1", name=name, arguments=args, raw_arguments="")


def _cand():
    return {"vocabulary": "same_thread",
            "records": [{"letter": "A", "ref": "thread:a"}, {"letter": "B", "ref": "thread:b"}]}


def test_a_proposal_citing_a_scene_read_stands_and_one_only_listed_is_uncertain():
    run = state.RunState(cid="c", mode="continuity")
    run.inspect("001--docks")
    read = final.settle(run, _call("propose", decision="duplicate", **{"from": "B", "to": "A"},
                                   relation="", status="", reason="r",
                                   evidence_scenes=["scene:001--docks"]), candidate=_cand())
    assert read.result["decision"] == "duplicate"
    assert read.result["evidence_scenes"] == ["001--docks"]
    listed = final.settle(run, _call("propose", decision="duplicate", **{"from": "B", "to": "A"},
                                     relation="", status="", reason="r",
                                     evidence_scenes=["scene:002--quay"]), candidate=_cand())
    assert listed.result["decision"] == "uncertain" and listed.result["evidence_scenes"] == []


def test_a_selection_keeps_only_history_evidence_ids():
    run = state.RunState(cid="c", mode="rp_narrator")
    held = run.hold(history_model.EvidenceItem(history_model.SceneRef("001--docks", None), "Docks"))
    got = final.settle(run, _call("finish_evidence", ids=["thread:ledger", held, "e99"],
                                  note="the docks"))
    assert got.result == {"ids": [held]}
    assert final.settle(run, _call("finish_evidence", ids=["fact:f1"], note="")).state \
        == "insufficient_evidence"


def test_no_terminal_call_is_insufficient_evidence():
    run = state.RunState(cid="c", mode="question")
    assert final.settle(run, None).state == "insufficient_evidence"
    # A terminal tool of another mode is not this mode's finish.
    assert final.settle(run, _call("propose", decision="duplicate")).state \
        == "insufficient_evidence"


def test_a_stop_pressed_while_the_persist_waited_writes_nothing(found):
    cid, _sid, record = found
    assert reconcile.persist_investigation(
        cid, PAIR, PROPOSAL, started_fingerprint=record["fingerprint"],
        started_proposal=None, stillborn=lambda: True) is False
    assert candidates.read(cid)["records"][PAIR]["proposal"] is None
