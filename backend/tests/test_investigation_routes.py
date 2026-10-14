"""Roadmap 12's entry points as runs (spec 9; 14 "Runtime" and "Write
posture"): E2, the continuity investigation (12-S3, with 12-S4's decide
tool), and E3, the history question (12-S5).

The model is `llm_fakes.FakeToolTurns`, scripted by call order; a held
variant makes "while the run is live" a moment rather than a sleep. Every
store is synthetic and placeholder-named.
"""

from __future__ import annotations

import asyncio
import json
import threading

import grimoire.store as store
from grimoire import routes
from grimoire.routes import investigation, runs
from grimoire.store.continuity import candidates, canon

from . import draft_runs
from .llm_fakes import FakeToolTurns
from .review_runs import LEDGER_THREAD, RECOVER_THE_LEDGER

LEDGER = f"thread:{LEDGER_THREAD[0]}"
RECOVER = "thread:recover-the-harbour-ledger"
PAIR = canon.candidate_id("possible_duplicate", [LEDGER, RECOVER])
REASON = "Scene one shows the two hunts are one."


# ---- helpers ----
def _campaign(client) -> tuple[str, str]:
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "Saltmarch docks"}).json()["id"]
    store.scenes.append_message(cid, sid, "user",
                                "Mara says the ledger hunt and the recovery are one errand.")
    pid, title, beat = LEDGER_THREAD
    store.plot.set_movement(cid, pid, title, "open", beat, sid)
    store.plot.set_movement(cid, "recover-the-harbour-ledger", RECOVER_THE_LEDGER["title"],
                            "open", RECOVER_THE_LEDGER["beat"], sid)
    return cid, sid


def _found(client, cid: str) -> None:
    """A sweep with no connection: deterministic discovery files the pair."""
    resp = client.post(f"/api/campaigns/{cid}/continuity/reconcile")
    assert resp.status_code == 202, resp.text
    draft_runs.wait_for_run(client, f"/api/campaigns/{cid}/runs", resp.json()["run"]["id"])
    assert PAIR in candidates.read(cid)["records"]


def _on(client, *, key=True, **switches) -> None:
    if key:
        client.put("/api/llm-connections/openrouter", json={"api_key": "sk-test"})
    body = {"investigation_continuity": "on", "investigation_question": "on", **switches}
    assert client.put("/api/config", json=body).status_code == 200


def _install(client, fake):
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    return fake


def _investigate(client, cid, candidate=PAIR, attempt=None):
    headers = {"X-Grimoire-Attempt": attempt} if attempt else {}
    return client.post(f"/api/campaigns/{cid}/continuity/candidates/{candidate}/investigate",
                       headers=headers)


def _settled(client, cid, resp) -> dict:
    assert resp.status_code == 202, resp.text
    return draft_runs.wait_for_run(client, f"/api/campaigns/{cid}/runs",
                                   resp.json()["run"]["id"])


def _proposing(sid, *, read=True, decision="duplicate", cite=None):
    steps = [("", [("search_history", {"query": "harbour ledger", "max": None})])]
    if read:
        steps.append(("", [("get_scene_excerpt", {"scene": f"scene:{sid}",
                                                  "query": "ledger errand",
                                                  "max_posts": 4})]))
    steps.append(("", [("propose", {"decision": decision, "from": "B", "to": "A",
                                    "relation": "", "status": "",
                                    "evidence_scenes": cite or [f"scene:{sid}"],
                                    "reason": REASON})]))
    return FakeToolTurns(*steps)


class HeldTools(FakeToolTurns):
    """`FakeToolTurns` whose first turn waits for `release`."""

    def __init__(self, *steps):
        super().__init__(*steps)
        self.held = threading.Event()
        self.go = threading.Event()

    async def complete(self, *a, **k):
        if not self.held.is_set():
            self.held.set()
            await asyncio.to_thread(self.go.wait, 10)
        return await super().complete(*a, **k)


# ---- E2: refusals before reserving ----
def _no_runs(client, cid):
    return [r for r in client.app.state.runs.for_subject(runs.campaign_subject(cid))
            if r.kind == investigation.CONTINUITY_KIND]


def test_e2_is_off_by_default_and_refused_before_reserving(client):
    cid, _sid = _campaign(client)
    _found(client, cid)
    resp = _investigate(client, cid)
    assert resp.status_code == 400 and resp.json()["kind"] == "off"
    assert _no_runs(client, cid) == []


def test_e2_refuses_unknown_campaigns_and_findings(client):
    cid, _sid = _campaign(client)
    _on(client)
    assert _investigate(client, "nowhere").status_code == 404
    assert _investigate(client, cid, candidate="possible_duplicate-0000").status_code == 404
    assert _no_runs(client, cid) == []


def test_e2_refuses_a_finding_that_is_not_live(client):
    cid, sid = _campaign(client)
    _found(client, cid)
    _on(client)
    store.plot.set_movement(cid, LEDGER_THREAD[0], "A new title", "open", "Moved.", sid)
    resp = _investigate(client, cid)
    assert resp.status_code == 409 and resp.json()["kind"] == "not_live"
    assert _no_runs(client, cid) == []


def test_e2_refuses_a_model_known_unable_to_call_tools(client):
    cid, _sid = _campaign(client)
    _found(client, cid)
    _on(client)
    model = store.read_config()[store.inference_keys.role_key("primary", "model")]
    got = client.put("/api/llm-connections/openrouter/facts",
                     json={"model": model, "overrides": {"tools": "no"}})
    assert got.status_code == 200, got.text
    resp = _investigate(client, cid)
    assert resp.status_code == 409 and resp.json()["kind"] == "incapable"
    assert _no_runs(client, cid) == []


def test_e2_refuses_an_unpriced_model_under_a_ceiling(client, monkeypatch):
    cid, _sid = _campaign(client)
    _found(client, cid)
    _on(client, investigation_spend_ceiling="0.50")
    monkeypatch.setattr(store.llm_connections, "cached_row", lambda conn, model: None)
    resp = _investigate(client, cid)
    assert resp.status_code == 409 and resp.json()["kind"] == "unpriceable"
    assert "rates" in resp.json()["detail"]
    assert _no_runs(client, cid) == []


def test_a_bad_spend_ceiling_is_refused_at_settings(client):
    for bad in ("free", "-1", "0", "nan"):
        got = client.put("/api/config", json={"investigation_spend_ceiling": bad})
        assert got.status_code == 400, bad
    assert client.put("/api/config", json={"investigation_spend_ceiling": ""}).status_code == 200
    assert client.get("/api/config").json()["investigation_continuity"] == "off"


# ---- E2: the run and its one write ----
def test_e2_persists_one_proposal_with_its_trace(client):
    cid, sid = _campaign(client)
    _found(client, cid)
    _on(client)
    before = candidates.read(cid)
    token = store.revision.current(cid)
    fake = _install(client, _proposing(sid))
    run = _settled(client, cid, _investigate(client, cid))
    assert run["state"] == "landed"
    result = run["result"]
    assert result["persisted"] is True and result["candidate"] == PAIR
    assert result["outcome"]["state"] == "completed"
    after = candidates.read(cid)
    proposal = after["records"][PAIR]["proposal"]
    assert proposal["decision"] == "duplicate" and proposal["evidence_scenes"] == [sid]
    assert proposal["reason"] == REASON
    trace = proposal["investigation"]
    assert trace["run"] == run["id"] and trace["inspected"] == [f"scene:{sid}"]
    assert [s["tool"] for s in trace["steps"]] == ["search_history", "get_scene_excerpt",
                                                   "propose"]
    # Only the one record's proposal moved: the sweep's generation and basis did not.
    assert after["generation"] == before["generation"]
    assert after["basis"] == before["basis"]
    assert store.revision.current(cid) != token
    # Every turn ran under the investigation task with the run's id.
    assert len(fake.requests) == 3


def test_e2_downgrades_a_proposal_citing_a_scene_it_never_read(client):
    cid, sid = _campaign(client)
    _found(client, cid)
    _on(client)
    _install(client, _proposing(sid, read=False))
    run = _settled(client, cid, _investigate(client, cid))
    assert run["result"]["persisted"] is True
    proposal = candidates.read(cid)["records"][PAIR]["proposal"]
    assert proposal["decision"] == "uncertain" and proposal["evidence_scenes"] == []


def test_e2_no_change_writes_nothing_and_the_sweep_still_asks(client):
    cid, _sid = _campaign(client)
    _found(client, cid)
    _on(client)
    before = candidates.read(cid)
    _install(client, FakeToolTurns(("", [("finish_no_change", {"reason": "distinct hunts"})])))
    run = _settled(client, cid, _investigate(client, cid))
    assert run["result"]["outcome"]["state"] == "no_change"
    assert run["result"]["persisted"] is False
    assert candidates.read(cid) == before
    sweep = store.continuity.reconcile.Sweep(store.continuity.reconcile.generation("x"), True)
    assert PAIR in {c["id"] for c in store.continuity.reconcile.select(cid, sweep)}


def test_e2_a_same_finding_restart_adopts_and_another_is_busy(client):
    cid, sid = _campaign(client)
    store.plot.set_movement(cid, "find-the-harbour-ledger", "Find the harbour ledger",
                            "open", "Mara searches the harbour for the ledger.", sid)
    _found(client, cid)
    _on(client)
    others = [k for k in candidates.read(cid)["records"] if k != PAIR]
    assert others
    held = _install(client, HeldTools(("", [("finish_no_change", {"reason": "same"})])))
    first = _investigate(client, cid, attempt="a1")
    assert first.status_code == 202
    assert held.held.wait(10)
    try:
        again = _investigate(client, cid, attempt="a2")
        assert again.status_code == 202
        assert again.json()["run"]["id"] == first.json()["run"]["id"]
        busy = _investigate(client, cid, candidate=others[0])
        assert busy.status_code == 409 and busy.json()["kind"] == "busy"
        assert busy.json()["run"]["id"] == first.json()["run"]["id"]
        moved = client.put("/api/config/data-dir", json={"data_dir": "/tmp/nowhere-12"})
        assert moved.status_code == 409 and moved.json()["kind"] == "runs_in_flight"
    finally:
        held.go.set()
    assert _settled(client, cid, first)["state"] == "landed"


def test_e2_a_deleted_campaigns_run_writes_nothing(client):
    cid, sid = _campaign(client)
    _found(client, cid)
    _on(client)
    held = _install(client, HeldTools(*_proposing(sid).script))
    resp = _investigate(client, cid, attempt="a-delete")
    assert resp.status_code == 202
    run = client.app.state.runs.for_attempt(runs.campaign_subject(cid), "a-delete")
    assert held.held.wait(10)
    assert client.delete(f"/api/campaigns/{cid}").status_code == 200
    held.go.set()
    assert run.terminal.wait(10)
    assert run.result is None or run.result.get("persisted") is not True


# ---- 12-S4: the decide tool ----
def test_e2_offers_the_decide_tool_on_its_own_route(client):
    cid, _sid = _campaign(client)
    _found(client, cid)
    _on(client)
    fake = _install(client, FakeToolTurns(("", [("finish_no_change", {"reason": "same"})])))
    _settled(client, cid, _investigate(client, cid))
    offered = {t["name"] for t in fake.requests[0]["tools"]}
    assert "decide" in offered and "propose" in offered


def test_e2_runs_without_the_decide_tool_when_it_cannot_resolve(client, monkeypatch):
    cid, _sid = _campaign(client)
    _found(client, cid)
    _on(client)
    monkeypatch.setattr(investigation, "_soft_resolved",
                        lambda resolve: (None, "no decision model", "missing_key"))
    fake = _install(client, FakeToolTurns(("", [("finish_no_change", {"reason": "same"})])))
    run = _settled(client, cid, _investigate(client, cid))
    assert run["result"]["outcome"]["state"] == "no_change"
    assert "decide" not in {t["name"] for t in fake.requests[0]["tools"]}


# ---- E3: the history question ----
def _ask(client, cid, question="Who keeps the harbour ledger?", attempt=None):
    headers = {"X-Grimoire-Attempt": attempt} if attempt else {}
    return client.post(f"/api/campaigns/{cid}/history/question", json={"question": question},
                       headers=headers)


def test_e3_is_off_by_default_and_checks_its_question(client):
    cid, _sid = _campaign(client)
    assert _ask(client, cid).json()["kind"] == "off"
    _on(client)
    assert _ask(client, cid, question="   ").status_code == 400
    assert _ask(client, cid, question="x" * 501).status_code == 400
    assert _ask(client, "nowhere").status_code == 404


def test_e3_answers_with_read_citations_and_stores_nothing(client, monkeypatch):
    cid, sid = _campaign(client)
    _on(client)
    # The prompt log is a write of its own (the next test): off here, so the
    # token says whether the answer itself was stored anywhere.
    monkeypatch.setattr(store.prompt_log, "depth", lambda: 0)
    token = store.revision.current(cid)
    _install(client, FakeToolTurns(
        ("", [("get_scene_excerpt", {"scene": f"scene:{sid}", "query": "ledger",
                                     "max_posts": 4})]),
        ("", [("answer", {"text": "Mara treats both hunts as one errand.",
                          "citations": [f"scene:{sid}", "scene:999--elsewhere"]})])))
    run = _settled(client, cid, _ask(client, cid))
    outcome = run["result"]["outcome"]
    assert outcome["state"] == "completed"
    assert outcome["result"]["citations"] == [f"scene:{sid}"]
    assert outcome["trace"]["inspected"] == [f"scene:{sid}"]
    assert store.revision.current(cid) == token


def test_e3_files_its_turns_as_one_tool_loop_prompt_entry(client):
    cid, sid = _campaign(client)
    _on(client)
    _install(client, FakeToolTurns(
        ("", [("get_scene_excerpt", {"scene": f"scene:{sid}", "query": "ledger",
                                     "max_posts": 4})]),
        ("", [("answer", {"text": "Mara keeps it.", "citations": [f"scene:{sid}"]})])))
    _settled(client, cid, _ask(client, cid))
    rows = [e for e in store.prompt_log.list_entries(cid, store.prompt_log.NO_SCENE)
            if e["task"] == "investigation-question"]
    assert [e.get("operation") for e in rows] == ["tools"]
    entry = store.prompt_log.read_entry(cid, rows[0]["id"], store.prompt_log.NO_SCENE)
    assert entry is not None
    outcome = json.loads(next(s["text"] for s in entry["sections"] if s["id"] == "decision"))
    assert [c["calls"][0]["name"] for c in outcome["calls"]] == ["get_scene_excerpt", "answer"]
    # The last turn's messages are kept whole: they carry the tool result.
    assert outcome["elided_calls"] == 1
    assert any(s["label"].endswith("tool") for s in entry["sections"])


def test_e3_with_no_read_citation_is_insufficient_evidence(client):
    cid, sid = _campaign(client)
    _on(client)
    _install(client, FakeToolTurns(
        ("", [("answer", {"text": "Mara.", "citations": [f"scene:{sid}"]})])))
    run = _settled(client, cid, _ask(client, cid))
    assert run["result"]["outcome"]["state"] == "insufficient_evidence"


def test_e3_a_repeated_attempt_is_not_run_again(client):
    cid, _sid = _campaign(client)
    _on(client)
    fake = _install(client, FakeToolTurns(("", [("finish_insufficient_evidence",
                                                 {"reason": "nothing"})])))
    first = _ask(client, cid, attempt="q1")
    _settled(client, cid, first)
    sent = len(fake.requests)
    again = _ask(client, cid, attempt="q1")
    assert again.json()["run"]["id"] == first.json()["run"]["id"]
    assert len(fake.requests) == sent


def test_e3_an_answer_is_not_found_again_from_another_library(client, tmp_path,
                                                              monkeypatch):
    # The env var pins the root; name it through the pointer instead, so the
    # move below really moves it.
    store.set_data_dir(store.home())
    monkeypatch.delenv("GRIMOIRE_HOME")
    cid, _sid = _campaign(client)
    _on(client)
    _install(client, FakeToolTurns(("", [("finish_insufficient_evidence",
                                         {"reason": "nothing"})])))
    first = _ask(client, cid, attempt="q-moved")
    _settled(client, cid, first)
    runs_at = f"/api/campaigns/{cid}/runs"
    assert client.get(runs_at, params={"attempt": "q-moved"}).json()["runs"]
    moved = client.put("/api/config/data-dir", json={"data_dir": str(tmp_path / "other")})
    assert moved.status_code == 200, moved.text
    # a campaign of the same slug in the new library finds nothing to re-attach
    assert client.get(runs_at, params={"attempt": "q-moved"}).json()["runs"] == []
    assert client.get(f"{runs_at}/{first.json()['run']['id']}").status_code == 404


def test_e3_refuses_an_unpriced_model_under_a_ceiling(client, monkeypatch):
    cid, _sid = _campaign(client)
    _on(client, investigation_spend_ceiling="1")
    monkeypatch.setattr(store.llm_connections, "cached_row", lambda conn, model: None)
    resp = _ask(client, cid)
    assert resp.status_code == 409 and resp.json()["kind"] == "unpriceable"


def test_e2_a_finding_gone_before_the_run_reads_it_fails_cleanly(client, monkeypatch):
    cid, _sid = _campaign(client)
    _found(client, cid)
    _on(client)
    fake = _install(client, FakeToolTurns(("", [("finish_no_change", {"reason": "x"})])))

    def gone(cid, candidate_id):
        raise KeyError(candidate_id)
    monkeypatch.setattr(investigation, "continuity_seed", gone)
    run = _settled(client, cid, _investigate(client, cid))
    assert run["state"] == "landed"
    assert run["result"]["outcome"]["state"] == "failed"
    assert run["result"]["outcome"]["error"] == "gone"
    assert run["result"]["persisted"] is False
    assert fake.requests == []


def test_e2_a_finding_no_longer_live_when_the_run_begins_sends_nothing(client, monkeypatch):
    """Live at the start's check, moved by the time the background run
    begins (an edit, a sweep): the run re-reads it and spends nothing."""
    cid, _sid = _campaign(client)
    _found(client, cid)
    _on(client)
    fake = _install(client, FakeToolTurns(("", [("finish_no_change", {"reason": "x"})])))
    verdicts = iter([True, False])
    monkeypatch.setattr(investigation, "_live", lambda cid, record: next(verdicts))
    run = _settled(client, cid, _investigate(client, cid))
    outcome = run["result"]["outcome"]
    assert (outcome["state"], outcome["error"]) == ("failed", "gone")
    assert run["result"]["persisted"] is False
    assert fake.requests == []


def test_e2_a_finding_whose_fingerprint_moved_is_not_still_live(client):
    cid, _sid = _campaign(client)
    _found(client, cid)
    record = candidates.read(cid)["records"][PAIR]
    assert investigation._still_live(cid, PAIR, record)
    assert not investigation._still_live(cid, PAIR, {**record, "fingerprint": "fp1_old"})
    assert not investigation._still_live(cid, "no-such-finding", record)
    # a sweep that adjudicated it between the start and the run moved its proposal
    assert not investigation._still_live(
        cid, PAIR, {**record, "proposal": {"status": "contradiction", "rationale": "x"}})


def test_e2_a_stopping_investigation_still_holds_the_campaign(client):
    cid, sid = _campaign(client)
    store.plot.set_movement(cid, "find-the-harbour-ledger", "Find the harbour ledger",
                            "open", "Mara searches the harbour for the ledger.", sid)
    _found(client, cid)
    _on(client)
    others = [k for k in candidates.read(cid)["records"] if k != PAIR]
    held = _install(client, HeldTools(("", [("finish_no_change", {"reason": "same"})])))
    first = _investigate(client, cid, attempt="s1")
    assert held.held.wait(10)
    try:
        run = client.app.state.runs.for_attempt(runs.campaign_subject(cid), "s1")
        run.cancel_requested = True
        for candidate in (PAIR, others[0]):
            refused = _investigate(client, cid, candidate=candidate)
            assert refused.status_code == 409 and refused.json()["kind"] == "busy"
    finally:
        held.go.set()
    assert run.terminal.wait(10)
    assert first.status_code == 202
