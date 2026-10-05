"""The reconciliation run and its explicit refresh (capstone spec §11.1, §21).

`POST /campaigns/{cid}/continuity/reconcile` answers 202 and a `background` run
on the campaign subject. The run discovers, persists what it found, asks the
`continuity-reconcile` connection about the findings with no proposal (when one
resolves), and persists the proposals. Nothing in it writes a ledger: a proposal
lands in the candidate cache and nowhere else (§11.5).

Every blocking test holds the reconcile call with `HeldCassette`, matched on the
prompt that owns it, so "while the sweep is live" is a moment rather than a
sleep.
"""

from __future__ import annotations

import json
import logging

import pytest

import grimoire.store as store
from grimoire import routes
from grimoire.routes import continuity as continuity_routes
from grimoire.routes import runs
from grimoire.store.campaigns import paths as campaigns_paths
from grimoire.store.continuity import candidates, canon, doc

from . import draft_runs
from .llm_fakes import HeldCassette, from_entries
from .review_runs import LEDGER_THREAD, RECOVER_THE_LEDGER

SYSTEM = "You are reviewing a campaign's story ledger"
LEDGER = f"thread:{LEDGER_THREAD[0]}"
RECOVER = "thread:recover-the-harbour-ledger"
OATH = "commitment:mara-s-oath"
PAIR = canon.candidate_id("possible_duplicate", [LEDGER, RECOVER])
REASON = "Both rows are the hunt for the harbour ledger."
#: What the user prompt says of a touched re-check, and of nothing else.
TOUCHED = "moved in the latest scene"


# ------------------------------------------------------------------ helpers


def _campaign(client) -> tuple[str, str, str]:
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "Saltmarch docks"}).json()["id"]
    return wid, cid, sid


def _threads(cid: str, sid: str) -> None:
    """Two open threads close enough to be a possible duplicate."""
    pid, title, beat = LEDGER_THREAD
    store.plot.set_movement(cid, pid, title, "open", beat, sid)
    store.plot.set_movement(cid, "recover-the-harbour-ledger", RECOVER_THE_LEDGER["title"],
                            "open", RECOVER_THE_LEDGER["beat"], sid)


def _key(client) -> None:
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-test"})


def _install(client, fake):
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    return fake


def _reply(*decisions) -> str:
    return json.dumps({"decisions": list(decisions)})


def _entry(reply: str) -> dict:
    return {"when": {"system_contains": SYSTEM}, "reply": reply}


def _held(reply: str = '{"decisions": []}') -> HeldCassette:
    return HeldCassette([_entry(reply)], hold={"system_contains": SYSTEM})


def _duplicate(key: str = "c1") -> dict:
    return {"candidate": key, "decision": "duplicate", "from": "B", "to": "A",
            "reason": REASON}


def _refresh(client, cid: str, attempt: str | None = None):
    headers = {"X-Grimoire-Attempt": attempt} if attempt else {}
    return client.post(f"/api/campaigns/{cid}/continuity/reconcile", headers=headers)


def _settled(client, cid: str, resp) -> dict:
    assert resp.status_code == 202, resp.text
    return draft_runs.wait_for_run(client, f"/api/campaigns/{cid}/runs",
                                   resp.json()["run"]["id"])


def _reconcile_requests(fake) -> list[dict]:
    return [r for r in fake.requests
            if any(m.get("role") == "system" and SYSTEM in m.get("content", "")
                   for m in r["messages"])]


def _records(cid: str) -> dict:
    return candidates.read(cid)["records"]


def _live(client, cid: str):
    """The campaign's live reconcile run, from the registry."""
    found = [r for r in client.app.state.runs.for_subject(runs.campaign_subject(cid))
             if r.kind == "continuity-reconcile"]
    assert len(found) == 1, found
    return found[0]


# ------------------------------------------------------------- the refresh


def test_refresh_works_with_no_connection(client):
    """§28.4: with no key on the active connection the sweep still runs, finds
    the duplicate deterministically and says it ran with no model."""
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    fake = _install(client, from_entries([_entry(_reply())]))

    run = _settled(client, cid, _refresh(client, cid))

    assert run["state"] == "landed", run
    result = run["result"]
    assert (result["llm"], result["matching"], result["sweep"]) == ("off", "basic", "full")
    assert result["reason"], "an `off` sweep says why"
    assert result["candidates"] >= 1
    assert PAIR in _records(cid)
    assert _records(cid)[PAIR]["proposal"] is None
    assert fake.requests == []


def test_refresh_with_a_connection_adjudicates_once(client):
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    _key(client)
    fake = _install(client, from_entries([_entry(_reply(_duplicate()))]))

    run = _settled(client, cid, _refresh(client, cid))

    assert run["state"] == "landed", run
    assert (run["result"]["llm"], run["result"]["adjudicated"]) == ("ok", 1)
    assert len(fake.requests) == 1
    assert len(_reconcile_requests(fake)) == 1
    proposal = _records(cid)[PAIR]["proposal"]
    assert proposal["decision"] == "duplicate"
    assert {proposal["from"], proposal["to"]} == {LEDGER, RECOVER}
    tasks = [r.get("task") for r in store.usage.calls(campaign=cid)]
    assert tasks.count("continuity-reconcile") == 1


def test_an_llm_failure_keeps_deterministic_candidates(client):
    """§26: the first persist already landed, so a failed call costs only the
    proposals."""
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    _key(client)
    _install(client, from_entries([{"when": {"system_contains": SYSTEM},
                                     "error": {"kind": "network",
                                               "message": "connection reset"}}]))

    run = _settled(client, cid, _refresh(client, cid))

    assert run["state"] == "failed", run
    assert run["error"]["kind"] == "network"
    assert run["result"]["llm"] == "failed"
    assert PAIR in _records(cid)
    assert _records(cid)[PAIR]["proposal"] is None


def test_a_failed_run_carries_its_sweep_in_the_error(client):
    """Decision 24: a client keeps only `error` for a run that did not land, so
    the sweep it ran rides there too."""
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    _key(client)
    _install(client, from_entries([{"when": {"system_contains": SYSTEM},
                                     "error": {"kind": "network",
                                               "message": "connection reset"}}]))

    run = _settled(client, cid, _refresh(client, cid))

    assert run["state"] == "failed"
    assert run["error"]["sweep"] == "full"


def test_an_undecodable_reply_fails_the_run_and_keeps_candidates(client):
    """§24: no decodable object is a failed run, not an empty answer."""
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    _key(client)
    _install(client, from_entries([_entry("I think they are the same.")]))

    run = _settled(client, cid, _refresh(client, cid))

    assert run["state"] == "failed", run
    assert (run["error"]["kind"], run["error"]["status"]) == ("undecodable", 502)
    assert run["error"]["sweep"] == "full"
    assert run["result"]["llm"] == "failed"
    assert _records(cid)[PAIR]["proposal"] is None


def _bytes(path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def test_a_duplicate_proposal_mutates_nothing_until_apply(client):
    """§28.4, §11.5: the run writes the candidate cache and nothing else -- not
    the ledgers, not continuity.json's decisions, not the events the temporal
    pair names, not the scene ideas."""
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    store.clock.advance(cid, to="2026-05-10")
    store.commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open",
                                   "before the bells stop", "Mara swore it.", sid)
    event = "event:" + store.events.create(cid, "The coronation", "2026-05-13")
    store.scene_ideas.add(cid, "The coronation", "Mara's oath comes due.")
    temporal = canon.candidate_id("possible_relation", [OATH, event])
    root = campaigns_paths.campaign_root(cid)
    before = (store.plot.read(cid), store.commitments.read(cid), doc.read(cid)["aliases"],
              doc.read(cid)["links"], store.scene_ideas.read(cid),
              _bytes(root / "events.json"), _bytes(root / "scene_ideas.json"),
              _bytes(root / "plot.json"), _bytes(root / "commitments.json"))
    assert before[5] is not None and before[6] is not None
    _key(client)
    _install(client, from_entries([_entry(_reply(
        _duplicate("c1"),
        {"candidate": "c2", "decision": "before", "from": "A", "to": "B",
         "reason": "The oath falls before the crowning."}))]))

    run = _settled(client, cid, _refresh(client, cid))

    assert run["state"] == "landed", run
    records = _records(cid)
    assert records[PAIR]["proposal"]["decision"] == "duplicate"
    assert records[temporal]["proposal"]["decision"] == "before"
    after = (store.plot.read(cid), store.commitments.read(cid), doc.read(cid)["aliases"],
             doc.read(cid)["links"], store.scene_ideas.read(cid),
             _bytes(root / "events.json"), _bytes(root / "scene_ideas.json"),
             _bytes(root / "plot.json"), _bytes(root / "commitments.json"))
    assert after == before


def test_unknown_campaign_is_404_and_starts_nothing(client):
    r = _refresh(client, "nobody")
    assert r.status_code == 404, r.text
    assert client.app.state.runs.for_subject(runs.campaign_subject("nobody")) == []


# ---------------------------------------------------------- a live sweep


def _temporal(client, cid: str) -> str:
    """A commitment whose due the calendar cannot place, three days before "The
    coronation": a model-only temporal nomination, which only persist 2 writes.
    Seeded the same way twice, it has the same id and fingerprint."""
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "Saltmarch docks"}).json()["id"]
    store.clock.advance(cid, to="2026-05-10")
    store.commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open",
                                   "before the bells stop", "Mara swore it.", sid)
    event = "event:" + store.events.create(cid, "The coronation", "2026-05-13")
    return canon.candidate_id("possible_relation", [OATH, event])


def test_a_deleted_campaign_run_writes_nothing(client):
    """A slug is reusable: the run of a deleted campaign must not write its
    proposals into the replacement -- even one holding the very records the
    proposal names, where every other fence would let it through."""
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    temporal = _temporal(client, cid)
    _key(client)
    held = _install(client, _held(_reply(
        {"candidate": "c1", "decision": "before", "reason": "The oath falls first."})))
    resp = _refresh(client, cid, attempt="a-delete")
    assert resp.status_code == 202, resp.text
    run = client.app.state.runs.for_attempt(runs.campaign_subject(cid), "a-delete")
    assert run is not None
    held.await_held()

    assert client.delete(f"/api/campaigns/{cid}").status_code == 200
    again = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    assert again == cid
    assert _temporal(client, cid) == temporal
    path = campaigns_paths.campaign_root(cid) / "continuity_candidates.json"
    assert not path.exists()
    held.release()

    assert run.terminal.wait(10)
    assert run.state == "cancelled"
    assert not path.exists()


def test_a_live_sweep_refuses_a_storage_move(client, tmp_path):
    """§11.1: accepted, because the run is bounded."""
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    _key(client)
    held = _install(client, _held())
    resp = _refresh(client, cid)
    assert resp.status_code == 202, resp.text
    held.await_held()
    dest = tmp_path / "moved"

    try:
        r = client.put("/api/config/data-dir", json={"data_dir": str(dest)})
        assert r.status_code == 409, r.text
        assert r.json()["kind"] == "runs_in_flight"
    finally:
        held.release()
    assert _settled(client, cid, resp)["state"] == "landed"


def test_a_second_refresh_during_a_live_sweep_returns_it(client):
    """One live per campaign: the second POST adopts the first run."""
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    _key(client)
    held = _install(client, _held())
    first = _refresh(client, cid, attempt="a1")
    held.await_held()
    try:
        second = _refresh(client, cid, attempt="a2")
        assert second.status_code == 202, second.text
        assert second.json()["run"]["id"] == first.json()["run"]["id"]
    finally:
        held.release()
    assert _settled(client, cid, first)["state"] == "landed"


def test_reconcile_log_row_carries_counts_only(client):
    """§29: counts and modes, never a title, a beat or the model's reason."""
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    _key(client)
    _install(client, from_entries([_entry(_reply(_duplicate()))]))

    _settled(client, cid, _refresh(client, cid))

    [row] = [r for r in store.logs.scan(level="info", campaign=cid)
             if r.get("message") == "continuity reconcile"]
    assert row["kind"] == "continuity-reconcile"
    assert (row["sweep"], row["llm"], row["matching"]) == ("full", "ok", "basic")
    for key in ("candidates", "deterministic", "semantic", "adjudicated", "duplicate"):
        assert isinstance(row[key], int) and not isinstance(row[key], bool), key
    assert row["adjudicated"] == 1 and row["duplicate"] == 1
    assert isinstance(row["pairs_capped"], bool) and isinstance(row["superseded"], bool)
    texts = (LEDGER_THREAD[1], LEDGER_THREAD[2], RECOVER_THE_LEDGER["title"],
             RECOVER_THE_LEDGER["beat"], REASON)
    for value in row.values():
        for text in texts:
            assert text not in str(value)


# ------------------------------------------------- the automatic trigger


def _plot_edit(pid: str) -> dict:
    return {"id": f"plot:{pid}", "kind": "plot", "target": {"kind": "plot", "id": pid}}


def _applied(*edits) -> dict:
    return {"edits": {str(i): {"state": "applied", "id": e["id"]}
                      for i, e in enumerate(edits)}}


def test_schedule_reconcile_does_nothing_under_the_suite_switch(client):
    """Decision 20: unmarked, the automatic sweep is off."""
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    assert continuity_routes.AUTO_RECONCILE is False
    edit = _plot_edit(LEDGER_THREAD[0])

    continuity_routes.schedule_reconcile(client.app, cid, from_entries([_entry(_reply())]),
                                         edits=[edit], progress=_applied(edit))

    assert client.app.state.runs.for_subject(runs.campaign_subject(cid)) == []


@pytest.mark.reconcile
def test_schedule_reconcile_starts_an_incremental_sweep(client):
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    edit = _plot_edit(LEDGER_THREAD[0])

    continuity_routes.schedule_reconcile(client.app, cid, from_entries([_entry(_reply())]),
                                         edits=[edit], progress=_applied(edit))

    run = _live(client, cid)
    assert run.terminal.wait(10)
    assert run.state == "landed", run.error
    assert run.result["sweep"] == "incremental"
    assert run.cls == "background"


@pytest.mark.reconcile
def test_schedule_reconcile_hands_its_refs_to_an_adopted_run(client):
    """Decision 6: a trigger that adopts a live sweep pends its touched refs,
    and the live run takes them for one more pass after its own."""
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    _key(client)
    held = _install(client, _held())
    first = _refresh(client, cid)
    held.await_held()
    edit = _plot_edit(LEDGER_THREAD[0])
    try:
        continuity_routes.schedule_reconcile(client.app, cid, held, edits=[edit],
                                             progress=_applied(edit))
        assert len(client.app.state.runs.for_subject(runs.campaign_subject(cid))) == 1
    finally:
        held.release()

    run = _settled(client, cid, first)
    assert run["state"] == "landed", run
    assert run["result"]["sweep"] == "full"
    assert run["result"]["follow_on"] is True
    requests = _reconcile_requests(held)
    assert len(requests) == 2
    assert TOUCHED not in requests[0]["messages"][1]["content"]
    assert TOUCHED in requests[1]["messages"][1]["content"]
    assert client.app.state.runs.take_touched(runs.campaign_subject(cid)) == set()


def test_a_full_refresh_keeps_refs_left_pending_for_its_follow_on(client):
    """A full sweep re-checks nothing as touched, so refs an earlier adopter
    left behind wait for the pass after it rather than being taken and lost."""
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    _key(client)
    fake = _install(client, from_entries([_entry(_reply())]))
    client.app.state.runs.pend_touched(runs.campaign_subject(cid), {LEDGER})

    run = _settled(client, cid, _refresh(client, cid))

    assert run["state"] == "landed", run
    assert (run["result"]["sweep"], run["result"]["follow_on"]) == ("full", True)
    requests = _reconcile_requests(fake)
    assert len(requests) == 2
    assert TOUCHED not in requests[0]["messages"][1]["content"]
    assert TOUCHED in requests[1]["messages"][1]["content"]


@pytest.mark.reconcile
def test_schedule_reconcile_never_raises(client, monkeypatch, caplog):
    """§11.1: the save response never depends on the sweep."""
    _wid, cid, _sid = _campaign(client)

    def boom(*_a, **_k):
        raise RuntimeError("no reservation today")

    monkeypatch.setattr(runs, "reserve_campaign_background", boom)
    with caplog.at_level(logging.WARNING, logger="grimoire"):
        continuity_routes.schedule_reconcile(client.app, cid, from_entries([_entry(_reply())]),
                                             edits=[{"kind": "plot", "target": "x", "id": 3}],
                                             progress={"edits": {"0": "nonsense"}})
    assert any("continuity sweep" in r.getMessage() for r in caplog.records)
    assert client.app.state.runs.for_subject(runs.campaign_subject(cid)) == []
