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

import asyncio
import json
import logging

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import routes
from grimoire.main import create_app
from grimoire.routes import continuity as continuity_routes
from grimoire.routes import runs
from grimoire.store.campaigns import paths as campaigns_paths
from grimoire.store.continuity import candidates, canon, doc, reconcile, similarity

from . import draft_runs, review_runs
from .llm_fakes import Cassette, FakeEmbeddings, HeldCassette, from_cassette, from_entries
from .review_runs import LEDGER_THREAD, RECOVER_THE_LEDGER
from .test_absorb_identity import MODES as SHARED_MODES
from .test_absorb_identity import ROW_ENVELOPE, _dumped, _leaked, _row_texts
from .test_continuity_reconcile import _chores as chores
from .test_continuity_reconcile import _configure as configure_embeddings

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
    proposals -- and the failed run says so everywhere a client reads it.

    One refresh, one settle, every field read off the same terminal run.
    Merged (test-suite acceleration, C1) with `test_a_failed_run_carries_its_sweep_in_the_error`
    (Decision 24: a client keeps only `error` for a run that did not land, so
    the sweep it ran rides there too) and
    `test_a_failed_model_call_says_the_findings_were_saved` (`error.saved` says
    persist 1 landed, so the Refresh note may say the basic findings are
    listed). The fields are compared as one dict, so one regression cannot
    hide another behind the first failing assert."""
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    _key(client)
    _install(client, from_entries([{"when": {"system_contains": SYSTEM},
                                     "error": {"kind": "network",
                                               "message": "connection reset"}}]))

    run = _settled(client, cid, _refresh(client, cid))

    error = run.get("error") or {}
    records = _records(cid)
    got = {
        "state": run.get("state"),
        "error.kind": error.get("kind"),
        "error.sweep": error.get("sweep"),
        # `is True`, as the original asserted: a truthy stand-in is not `saved`.
        "error.saved is True": error.get("saved") is True,
        "result.llm": (run.get("result") or {}).get("llm"),
        # Model failure must not erase what discovery found (§26)...
        "duplicate cached": PAIR in records,
        # ...and must not leave a proposal behind either.
        "duplicate proposal": (records.get(PAIR) or {}).get("proposal", "<no record>"),
    }
    want = {
        "state": "failed",
        "error.kind": "network",
        "error.sweep": "full",
        "error.saved is True": True,
        "result.llm": "failed",
        "duplicate cached": True,
        "duplicate proposal": None,
    }
    assert got == want, (
        "a failed model call reported the wrong outcome: "
        f"{ {k: {'got': got[k], 'want': want[k]} for k in want if got[k] != want[k]} }"
        f"\nrun: {run}")


def test_an_undecodable_reply_fails_the_run_and_keeps_candidates(client):
    """§24: no decodable object is a failed run, not an empty answer -- and,
    as with a failed call (§26), persist 1's findings stand and the run says
    they were saved.

    One refresh, one settle. Merged (test-suite acceleration, C2) with
    `test_an_undecodable_reply_says_the_findings_were_saved`. Kept apart from
    the network-failure test on purpose: the two failures leave `_adjudicate`
    by different branches (`LLMError` vs `parse_output` returning None)."""
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    _key(client)
    _install(client, from_entries([_entry("I think they are the same.")]))

    run = _settled(client, cid, _refresh(client, cid))

    error = run.get("error") or {}
    records = _records(cid)
    got = {
        "state": run.get("state"),
        "error.kind": error.get("kind"),
        "error.status": error.get("status"),
        "error.sweep": error.get("sweep"),
        "error.saved is True": error.get("saved") is True,
        "result.llm": (run.get("result") or {}).get("llm"),
        "duplicate cached": PAIR in records,
        "duplicate proposal": (records.get(PAIR) or {}).get("proposal", "<no record>"),
    }
    want = {
        "state": "failed",
        "error.kind": "undecodable",
        "error.status": 502,
        "error.sweep": "full",
        "error.saved is True": True,
        "result.llm": "failed",
        "duplicate cached": True,
        "duplicate proposal": None,
    }
    assert got == want, (
        "an undecodable reply reported the wrong outcome: "
        f"{ {k: {'got': got[k], 'want': want[k]} for k in want if got[k] != want[k]} }"
        f"\nrun: {run}")


# ------------------------------------------ whether a failed run saved anything
#
# A failed run's `error.saved` says whether persist 1 landed -- whether the
# deterministic findings this sweep made are the ones the section now lists.
# The Refresh note is chosen from it (§26): "basic findings are listed" is true
# only when it is.


@pytest.mark.parametrize("exc, kind", [(OSError("disk full"), "io"),
                                       (store.locks.StoreBusy("x", "campaign"), "busy")])
def test_a_persist_1_failure_says_nothing_was_saved(client, monkeypatch, exc, kind):
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    monkeypatch.setattr(continuity_routes, "_PERSIST_ATTEMPTS", 1)

    def refuse(*_args, **_kwargs):
        raise exc

    monkeypatch.setattr(continuity_routes.reconcile, "persist_found", refuse)

    run = _settled(client, cid, _refresh(client, cid))

    assert run["state"] == "failed", run
    assert (run["error"]["kind"], run["error"]["saved"]) == (kind, False)
    assert _cache_bytes(cid) is None


def test_a_persist_2_failure_says_the_findings_were_saved(client, monkeypatch):
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    _key(client)
    _install(client, from_entries([_entry(_reply(_duplicate()))]))

    def refuse(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(continuity_routes.reconcile, "persist_proposals", refuse)

    run = _settled(client, cid, _refresh(client, cid))

    assert (run["error"]["kind"], run["error"]["saved"]) == ("io", True)
    assert PAIR in _records(cid)


def test_an_unexpected_failure_after_persist_1_says_the_findings_were_saved(
        client, monkeypatch):
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)

    def boom(*_args, **_kwargs):
        raise RuntimeError("select broke")

    monkeypatch.setattr(continuity_routes.reconcile, "select", boom)

    run = _settled(client, cid, _refresh(client, cid))

    assert (run["error"]["kind"], run["error"]["saved"]) == ("run_failed", True)
    assert PAIR in _records(cid)


def test_an_unexpected_failure_before_persist_1_says_nothing_was_saved(
        client, monkeypatch):
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)

    def boom(*_args, **_kwargs):
        raise RuntimeError("discovery broke")

    monkeypatch.setattr(continuity_routes.reconcile, "discover", boom)

    run = _settled(client, cid, _refresh(client, cid))

    assert (run["error"]["kind"], run["error"]["saved"]) == ("run_failed", False)
    assert _cache_bytes(cid) is None


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



#: The sweep row's keys (§29): modes, counts, the two flags, and one count per
#: decision word.
RECONCILE_ROW_KEYS = ({"kind", "campaign", "sweep", "matching", "embedding",
                       "embedding_error", "llm", "continuity", "candidates",
                       "deterministic", "semantic", "adjudicated", "pairs_capped",
                       "superseded"} | set(continuity_routes._WORDS))
#: field -> the values it may take: the three shared with the identity row,
#: then the sweep's own.
MODES = {**{k: SHARED_MODES[k] for k in ("matching", "embedding", "embedding_error")},
         "sweep": set(reconcile.SWEEPS), "llm": {"off", "skipped", "ok", "failed"},
         "continuity": {"ok", "malformed"}}
_FLAGS = {"pairs_capped", "superseded"}
_COUNTS = RECONCILE_ROW_KEYS - set(MODES) - _FLAGS - {"kind", "campaign"}


@pytest.mark.parametrize("case", ["ok", "off", "failed"])
def test_reconcile_log_row_is_counts_and_closed_modes_only(client, case):
    """§29: the row's exact key set, every string field from a closed set, every
    count an int, and no title, beat, reason or prompt line anywhere in it --
    whether the model answered, was never asked, or answered nothing usable."""
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    if case != "off":
        _key(client)
    fake = _install(client, from_entries(
        [_entry("no json here" if case == "failed" else _reply(_duplicate()))]))

    _settled(client, cid, _refresh(client, cid))

    row = _reconcile_row(cid)
    assert row["llm"] == case, "the case set up the sweep it names"
    assert set(row) - ROW_ENVELOPE == RECONCILE_ROW_KEYS
    assert (row["kind"], row["campaign"]) == ("continuity-reconcile", cid)
    for key in _COUNTS:
        assert isinstance(row[key], int) and not isinstance(row[key], bool), key
    for key in _FLAGS:
        assert isinstance(row[key], bool), key
    for field, allowed in MODES.items():
        assert row[field] in allowed, (field, row[field])
    dumped = _dumped(row)
    texts = [*LEDGER_THREAD[1:], RECOVER_THE_LEDGER["title"], RECOVER_THE_LEDGER["beat"],
             REASON, *_row_texts(fake)]
    for text in texts:
        assert not _leaked(text, dumped), text


# ------------------------------------------- a malformed continuity.json


def _garble(cid: str) -> None:
    (campaigns_paths.campaign_root(cid) / "continuity.json").write_text("{ no",
                                                                      encoding="utf-8")


def _cache_bytes(cid: str) -> bytes | None:
    return _bytes(campaigns_paths.campaign_root(cid) / "continuity_candidates.json")


def _reconcile_row(cid: str) -> dict:
    """The newest pass's info row (`scan` is oldest first)."""
    rows = [r for r in store.logs.scan(level="info", campaign=cid)
            if r.get("message") == "continuity reconcile"]
    return rows[-1]


def _assert_malformed(run: dict, *, saved: bool = False) -> None:
    assert run["state"] == "failed", run
    error = run["error"]
    assert (error["kind"], error["status"], error["sweep"]) == ("malformed", 409, "full")
    assert "continuity.json" in error["detail"]
    assert run["result"]["continuity"] == "malformed"
    # Refused before persist 1, nothing this sweep found was saved; refused at
    # persist 2, the findings stand and only the proposals were lost.
    assert error["saved"] is saved
    assert ("nothing this sweep found was saved" in error["detail"]) is not saved


def test_proposals_refused_by_a_malformed_continuity_file_fail_the_run(client):
    """Decision 2 keeps the cache as it is while continuity.json is malformed,
    so persist 2 writes nothing -- and a run that paid for the call and saved
    none of it says so rather than landing with `llm: "ok"`."""
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    _key(client)
    held = _install(client, _held(_reply(_duplicate())))
    resp = _refresh(client, cid)
    assert resp.status_code == 202, resp.text
    held.await_held()
    _garble(cid)
    before = _cache_bytes(cid)
    assert before is not None, "persist 1 landed before the call"
    held.release()

    run = _settled(client, cid, resp)

    _assert_malformed(run, saved=True)
    assert _cache_bytes(cid) == before
    assert _reconcile_row(cid)["continuity"] == "malformed"


def test_a_malformed_continuity_file_ends_the_pass_before_the_model(client):
    """A Refresh over a malformed continuity.json pays for no call, leaves the
    cache byte-identical, and fails visibly rather than landing `skipped`."""
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    assert _settled(client, cid, _refresh(client, cid))["state"] == "landed"
    before = _cache_bytes(cid)
    assert before is not None and PAIR in _records(cid)
    _key(client)
    fake = _install(client, from_entries([_entry(_reply(_duplicate()))]))
    _garble(cid)

    run = _settled(client, cid, _refresh(client, cid))

    _assert_malformed(run)
    assert _reconcile_requests(fake) == []
    assert _cache_bytes(cid) == before
    assert _reconcile_row(cid)["continuity"] == "malformed"


def test_a_continuity_file_garbled_before_persist_1_ends_the_pass(client, monkeypatch):
    """Garbled between discovery and persist 1: the first persist refuses, and
    the pass ends there -- no model call, no write, a failed run."""
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    _key(client)
    fake = _install(client, from_entries([_entry(_reply(_duplicate()))]))
    discover = continuity_routes.reconcile.discover

    def discover_then_garble(*args, **kwargs):
        sweep = discover(*args, **kwargs)
        _garble(cid)
        return sweep

    monkeypatch.setattr(continuity_routes.reconcile, "discover", discover_then_garble)

    run = _settled(client, cid, _refresh(client, cid))

    _assert_malformed(run)
    assert _reconcile_requests(fake) == []
    assert _cache_bytes(cid) is None


def test_a_sweep_that_discovered_under_a_malformed_file_persists_nothing(client, monkeypatch):
    """Discovery read continuity.json malformed, so it found nothing; repaired
    before persist 1, nothing it did is worth writing, and the run fails
    rather than landing a sweep that never looked."""
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    _key(client)
    fake = _install(client, from_entries([_entry(_reply(_duplicate()))]))
    path = campaigns_paths.campaign_root(cid) / "continuity.json"
    original = _bytes(path)
    discover = continuity_routes.reconcile.discover

    def discover_garbled(*args, **kwargs):
        _garble(cid)
        try:
            return discover(*args, **kwargs)
        finally:
            if original is None:
                path.unlink()
            else:
                path.write_bytes(original)

    monkeypatch.setattr(continuity_routes.reconcile, "discover", discover_garbled)

    run = _settled(client, cid, _refresh(client, cid))

    _assert_malformed(run)
    assert _reconcile_requests(fake) == []
    assert _cache_bytes(cid) is None


def test_a_well_formed_pass_says_continuity_ok(client):
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)

    run = _settled(client, cid, _refresh(client, cid))

    assert run["state"] == "landed", run
    assert run["result"]["continuity"] == "ok"
    assert _reconcile_row(cid)["continuity"] == "ok"

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


# A follow-on pass that fails at its own persist 1 still leaves the first
# pass's findings listed: `saved` stays true, `follow_on` says which pass
# failed, and its detail does not claim the whole sweep saved nothing.


def _follow_on_refresh(client, cid: str) -> None:
    client.app.state.runs.pend_touched(runs.campaign_subject(cid), {LEDGER})


def test_a_follow_on_pass_refused_by_a_malformed_file_keeps_the_first_pass(
        client, monkeypatch):
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    _key(client)
    _install(client, from_entries([_entry(_reply())]))
    _follow_on_refresh(client, cid)
    real = continuity_routes.reconcile.persist_proposals

    def then_garble(*args, **kwargs):
        written = real(*args, **kwargs)
        _garble(cid)  # between the two passes
        return written

    monkeypatch.setattr(continuity_routes.reconcile, "persist_proposals", then_garble)

    run = _settled(client, cid, _refresh(client, cid))

    assert run["state"] == "failed", run
    error = run["error"]
    assert (error["kind"], error["saved"], error["follow_on"]) == ("malformed", True, True)
    assert "nothing this sweep found was saved" not in error["detail"]
    assert "follow-on pass" in error["detail"]
    assert PAIR in _records(cid)


def test_a_follow_on_pass_refused_at_its_persist_1_keeps_the_first_pass(
        client, monkeypatch):
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    _key(client)
    _install(client, from_entries([_entry(_reply())]))
    _follow_on_refresh(client, cid)
    monkeypatch.setattr(continuity_routes, "_PERSIST_ATTEMPTS", 1)
    real = continuity_routes.reconcile.persist_found
    calls = []

    def second_refused(*args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            raise store.locks.StoreBusy("x", "campaign")
        return real(*args, **kwargs)

    monkeypatch.setattr(continuity_routes.reconcile, "persist_found", second_refused)

    run = _settled(client, cid, _refresh(client, cid))

    assert run["state"] == "failed", run
    error = run["error"]
    assert (error["kind"], error["saved"], error["follow_on"]) == ("busy", True, True)
    assert error["sweep"] == "full"


def test_a_first_pass_failure_is_not_a_follow_on(client, monkeypatch):
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    _key(client)
    _install(client, from_entries([_entry("I think they are the same.")]))
    _follow_on_refresh(client, cid)

    run = _settled(client, cid, _refresh(client, cid))

    assert (run["error"]["kind"], run["error"]["follow_on"]) == ("undecodable", False)


def test_a_follow_on_pass_embeds_within_what_the_run_has_left(client, monkeypatch):
    """§9.4: `RECONCILE_WARM_LIMIT` and the embedding window are the run's, not
    each pass's -- a follow-on embeds only what the first pass left of them
    (one `reconcile.EmbedBudget`), so a run with two passes costs and refuses a
    storage move no longer than one would."""
    _wid, cid, sid = _campaign(client)
    _key(client)
    configure_embeddings()
    texts = chores(cid, sid)
    double = FakeEmbeddings()
    monkeypatch.setattr(similarity, "_CLIENT", double)
    monkeypatch.setattr(reconcile, "RECONCILE_WARM_LIMIT", 4)
    budgets: list[object] = []
    real = reconcile.discover

    def spy(*args, **kwargs):
        budgets.append(kwargs.get("budget"))
        return real(*args, **kwargs)

    monkeypatch.setattr(reconcile, "discover", spy)
    fake = _install(client, from_entries([_entry(_reply())]))
    client.app.state.runs.pend_touched(runs.campaign_subject(cid),
                                       {"thread:saltmarch-errand-0"})

    run = _settled(client, cid, _refresh(client, cid))

    assert run["state"] == "landed", run
    assert run["result"]["follow_on"] is True
    assert len(_reconcile_requests(fake)) == 2           # two passes ran
    sent = [text for call in double.calls for text in call]
    assert set(sent) <= set(texts.values())
    assert len(sent) <= 4, sent
    assert len(budgets) == 2 and budgets[0] is not None and budgets[0] is budgets[1]


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


@pytest.mark.reconcile
def test_a_sweep_refused_during_a_storage_move_is_logged(client, monkeypatch, caplog):
    """§11.1, §26: a reservation the store move refuses is a skipped sweep, and
    it is logged like any other start failure -- not dropped in silence because
    the refusal comes back as ``None`` rather than raising."""
    _wid, cid, _sid = _campaign(client)

    def moving(*_a, **_k):
        raise runs.StoreMovingError

    monkeypatch.setattr(client.app.state.runs, "start_or_existing", moving)
    with caplog.at_level(logging.WARNING, logger="grimoire"):
        continuity_routes.schedule_reconcile(client.app, cid, from_entries([_entry(_reply())]))

    assert [r for r in caplog.records
            if r.levelno == logging.WARNING and r.name.startswith("grimoire")
            and "continuity sweep" in r.getMessage() and cid in r.getMessage()]
    assert client.app.state.runs.for_subject(runs.campaign_subject(cid)) == []


# ------------------------------------------------------ End Scene's sweep
#
# `PUT /chronicle` hands every commit that completed in the request to
# `schedule_reconcile` (Decision 6), after its lock block and before its
# return. These end real scenes: an absorb over the `campaign_flow` cassette
# moves `the-debt` and `salt-owed`, and the save commits the review it staged.

EXTRACTION = "You are absorbing a completed role-play scene"
DEBT = "thread:the-debt"
#: A second scene's thread, seeded before that scene is absorbed and moved by it.
CHART = ("winifred-s-chart", "Winifred's chart",
         "Winifred began a chart of the Saltmarch shoals.")
CHART_REF = f"thread:{CHART[0]}"
CHART_EXTRACTION = json.dumps(
    {"one_line": "Winifred redrew the chart.", "summary": "The shoals had moved.",
     "keywords": [], "timeline_events": [],
     "plot_movements": [{"id": CHART[0], "status": "open",
                         "beat": "Winifred redrew the chart past the Saltmarch shoals."}]})
_SAVED = ("one_line", "summary", "keywords", "timeline_events", "edits", "commit_token")


def _flow_entries() -> list[dict]:
    return list(Cassette.load("campaign_flow").entries)


def _played(client, cid: str, title: str) -> str:
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": title}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "I have brought the salt.")
    return sid


def _review(client, cid: str, sid: str, fake) -> dict:
    """End the scene over `fake` and hand back the save body its review stages."""
    _install(client, fake)
    review = review_runs.absorb(client, cid, sid)
    assert review.status_code == 200, review.json()
    return {k: review.json()[k] for k in _SAVED}


def _ended(client, name: str = "Run") -> tuple[str, str, dict]:
    """A campaign with one played scene ended over `campaign_flow`: (cid, sid,
    the body that saves its review)."""
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": name, "world": wid}).json()["id"]
    sid = _played(client, cid, "Saltmarch docks")
    _key(client)
    return cid, sid, _review(client, cid, sid, from_cassette("campaign_flow"))


def _chart_scene(client, cid: str) -> tuple[str, dict]:
    """A second scene that moves "Winifred's chart", ended (not yet saved)."""
    sid = _played(client, cid, "Saltmarch shoals")
    store.plot.set_movement(cid, CHART[0], CHART[1], "open", CHART[2], sid)
    fake = from_entries([{"when": {"system_contains": EXTRACTION}, "reply": CHART_EXTRACTION},
                         *_flow_entries()[1:]])
    body = _review(client, cid, sid, fake)
    assert [e["id"] for e in body["edits"]] == [f"plot:{CHART[0]}"]
    return sid, body


def _save(client, cid: str, sid: str, body: dict):
    return client.put(f"/api/campaigns/{cid}/scenes/{sid}/chronicle", json=body)


def _sweeps(client, cid: str) -> list:
    return [r for r in client.app.state.runs.for_subject(runs.campaign_subject(cid))
            if r.kind == "continuity-reconcile"]


def _touched_in(request: dict) -> str:
    """The user prompt of one reconcile request."""
    return request["messages"][1]["content"]


@pytest.mark.reconcile
def test_a_fresh_commit_starts_an_incremental_sweep(client):
    """§28.4: End Scene starts the incremental sweep once its commit lands."""
    cid, sid, body = _ended(client)
    _install(client, from_cassette("campaign_flow"))

    r = _save(client, cid, sid, body)

    assert r.status_code == 200, r.json()
    run = _live(client, cid)
    assert (run.cls, run.kind) == ("background", "continuity-reconcile")
    assert run.terminal.wait(timeout=10)
    assert run.state == "landed", run.error
    assert run.result["sweep"] == "incremental"


@pytest.mark.reconcile
def test_a_replayed_save_starts_no_run(client):
    """§28.4: the idempotent replay returns the recorded result inside the
    lock and never reaches the trigger."""
    cid, sid, body = _ended(client)
    _install(client, from_cassette("campaign_flow"))
    assert _save(client, cid, sid, body).status_code == 200
    [first] = _sweeps(client, cid)
    assert first.terminal.wait(timeout=10)

    again = _save(client, cid, sid, body)

    assert again.status_code == 200, again.json()
    assert _sweeps(client, cid) == [first]


@pytest.mark.reconcile
def test_a_resumed_commit_starts_a_sweep(client):
    """Decision 6: a resume finishes a commit, so it is a commit that completed
    in this request (the `test_commits_store` resume pattern)."""
    cid, sid, body = _ended(client)
    fp = store.commits.fingerprint({k: body[k] for k in _SAVED if k != "commit_token"})
    store.commits.reserve(cid, body["commit_token"], fp, sid, {"timeline": "done"})
    prior = store.commits.lookup(cid, body["commit_token"])
    assert prior["journalled"] and not prior["done"]
    _install(client, from_cassette("campaign_flow"))

    r = _save(client, cid, sid, body)

    assert r.status_code == 200, r.json()
    assert store.commits.lookup(cid, body["commit_token"])["done"]
    run = _live(client, cid)
    assert run.terminal.wait(timeout=10)
    assert run.result["sweep"] == "incremental"


@pytest.mark.reconcile
def test_a_reservation_failure_does_not_affect_the_save(client, monkeypatch, caplog):
    """§28.4, §26: the save response never depends on the sweep."""
    plain_cid, plain_sid, plain_body = _ended(client, "Plain")
    _install(client, from_cassette("campaign_flow"))
    plain = _save(client, plain_cid, plain_sid, plain_body)
    assert plain.status_code == 200, plain.json()
    assert _live(client, plain_cid).terminal.wait(timeout=10)
    cid, sid, body = _ended(client)
    _install(client, from_cassette("campaign_flow"))

    def boom(*_a, **_k):
        raise RuntimeError("no reservation today")

    monkeypatch.setattr(runs, "reserve_campaign_background", boom)
    with caplog.at_level(logging.WARNING, logger="grimoire"):
        r = _save(client, cid, sid, body)

    assert r.status_code == 200, r.json()
    saved = r.json()
    assert saved["applied"] == [e["id"] for e in body["edits"]]
    assert saved["failures"] == []
    assert set(saved) == set(plain.json())
    assert not [run for run in _sweeps(client, cid) if run.state == "running"]
    assert any("continuity sweep" in rec.getMessage() and rec.levelno == logging.WARNING
               for rec in caplog.records)


@pytest.mark.reconcile
def test_a_malformed_edit_still_saves(client, monkeypatch):
    """A client-supplied edit of the wrong shape is skipped by the trigger,
    never a 500 after the commit landed."""
    cid, sid, body = _ended(client)
    plot = [e for e in body["edits"] if e["kind"] == "plot"]
    body = {**body, "edits": [{"kind": "plot", "target": "x", "id": 3}, *plot]}
    seen: list[tuple[str, ...]] = []
    start = continuity_routes.start_reconcile

    def spy(app, cid_, client_, **kwargs):
        seen.append(kwargs["touched"])
        return start(app, cid_, client_, **kwargs)

    monkeypatch.setattr(continuity_routes, "start_reconcile", spy)
    _install(client, from_cassette("campaign_flow"))

    r = _save(client, cid, sid, body)

    assert r.status_code == 200, r.json()
    assert seen == [(DEBT,)]
    assert _live(client, cid).terminal.wait(timeout=10)


@pytest.mark.reconcile
def test_touched_refs_come_from_applied_plot_and_commitment_edits():
    """By POSITION through the journal's slots: ids are client-supplied and
    need not be unique, so the second of two `plot:x` edits failing must not
    hide the first, nor the first landing vouch for the second. A slot's own
    `target` (a reallocated write) wins over the staged one."""
    edits = [
        {"id": "plot:x", "kind": "plot", "target": {"kind": "plot", "id": "mara-s-map"}},
        {"id": "commitment:y", "kind": "commitment",
         "target": {"kind": "commitments", "id": "mara-s-oath"}},
        {"id": "plot:x", "kind": "plot", "target": {"kind": "plot", "id": "the-coronation"}},
        {"id": "lore:z", "kind": "lore", "target": {"kind": "lore", "id": "saltmarch"}},
        {"kind": "plot", "target": "x", "id": 3},
        {"id": "plot:w", "kind": "plot", "target": {"kind": "plot", "id": "staged"}},
    ]
    progress = {"timeline": "done", "edits": {
        "0": {"state": "applied", "id": "plot:x"},
        "1": {"state": "applied", "id": "commitment:y"},
        "2": {"state": "failed", "id": "plot:x", "kind": "conflict", "reason": "moved"},
        "3": {"state": "applied", "id": "lore:z"},
        "4": {"state": "applied", "id": 3},
        "5": {"state": "applied", "id": "plot:w",
              "target": {"kind": "plot", "id": CHART[0]}},
    }}

    touched = continuity_routes._touched_refs(edits, progress)

    assert touched == {"thread:mara-s-map", "commitment:mara-s-oath", CHART_REF}


@pytest.mark.reconcile
def test_a_refresh_during_the_automatic_sweep_returns_it(client):
    cid, sid, body = _ended(client)
    held = _install(client, _held())
    assert _save(client, cid, sid, body).status_code == 200
    held.await_held()
    run = _live(client, cid)
    try:
        resp = _refresh(client, cid, attempt="a-refresh")
        assert resp.status_code == 202, resp.text
        assert resp.json()["run"]["id"] == run.id
    finally:
        held.release()
    assert run.terminal.wait(timeout=10)
    assert run.state == "landed", run.error


def _two_scenes_held(client, name: str = "Run") -> tuple[str, HeldCassette, object]:
    """Scene one's sweep held at its model call while scene two -- which moves
    "Winifred's chart" -- is saved, so scene two's trigger adopts the live run
    and pends its ref. (cid, the held fake, the run)."""
    cid, sid, body = _ended(client, name)
    chart_sid, chart_body = _chart_scene(client, cid)
    held = _install(client, _held())
    assert _save(client, cid, sid, body).status_code == 200
    held.await_held()
    run = _live(client, cid)
    assert _save(client, cid, chart_sid, chart_body).status_code == 200
    assert _sweeps(client, cid) == [run]
    return cid, held, run


@pytest.mark.reconcile
def test_two_end_scenes_back_to_back_both_get_their_touched_refs(client):
    """Decision 6: the second End Scene adopts the live sweep and leaves its
    ref; the run re-checks it in one more pass after its own."""
    cid, held, run = _two_scenes_held(client)
    held.release()

    assert run.terminal.wait(timeout=10)
    assert run.state == "landed", run.error
    assert run.result["follow_on"] is True
    first, follow = (_touched_in(r) for r in _reconcile_requests(held))
    assert TOUCHED in first and "the-debt" in first
    assert TOUCHED in follow and CHART[1] in follow
    assert CHART[1] not in first
    assert client.app.state.runs.take_touched(runs.campaign_subject(cid)) == set()


def _cancel(client, cid: str, held: HeldCassette, run) -> None:
    """Stop the held run, then let it reach its end: a stopped run never takes
    the refs an adopter left."""
    r = client.post(f"/api/campaigns/{cid}/runs/{run.id}/cancel")
    assert r.status_code == 200, r.text
    held.release()
    assert run.terminal.wait(timeout=10)
    assert run.state == "cancelled", run.state


@pytest.mark.reconcile
def test_pending_touched_is_per_app_and_forgotten_with_the_campaign(client):
    """Decision 6: the pending refs live on the app's registry, keyed by
    subject -- a new app starts with none, and a deleted campaign's refs are
    not handed to a replacement of the same slug."""
    subject_of = runs.campaign_subject
    # (a) Per app: the refs left in one app do not reach another's sweep.
    with TestClient(create_app()) as first:
        cid, held, run = _two_scenes_held(first)
        _cancel(first, cid, held, run)
        assert first.app.state.runs.take_touched(subject_of(cid)) == {CHART_REF}
        first.app.state.runs.pend_touched(subject_of(cid), {CHART_REF})
    with TestClient(create_app()) as second:
        fake = _install(second, from_entries([_entry(_reply())]))
        _threads(cid, _played(second, cid, "Saltmarch quay"))
        _key(second)
        landed = _settled(second, cid, _refresh(second, cid))
        assert landed["state"] == "landed", landed
        assert landed["result"]["follow_on"] is False
        requests = _reconcile_requests(fake)
        assert requests, "the refresh asked about the duplicate"
        assert not any(TOUCHED in _touched_in(r) for r in requests)
        assert second.app.state.runs.take_touched(subject_of(cid)) == set()

    # (b) Forgotten with the campaign: a recreated slug inherits nothing.
    cid, held, run = _two_scenes_held(client, "Saltmarch")
    _cancel(client, cid, held, run)
    assert client.app.state.runs.take_touched(subject_of(cid)) == {CHART_REF}
    client.app.state.runs.pend_touched(subject_of(cid), {CHART_REF})
    wid = store.campaigns.read_campaign(cid)["meta"]["world"]
    assert client.delete(f"/api/campaigns/{cid}").status_code == 200
    # Not taken here: the sweep below is what would inherit them.
    again = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    assert again == cid
    sid = _played(client, cid, "Saltmarch shoals")
    store.plot.set_movement(cid, CHART[0], CHART[1], "open", CHART[2], sid)
    _threads(cid, sid)
    fake = _install(client, from_entries([_entry(_reply())]))

    landed = _settled(client, cid, _refresh(client, cid))

    assert landed["state"] == "landed", landed
    assert landed["result"]["follow_on"] is False
    requests = _reconcile_requests(fake)
    assert requests, "the refresh asked about the duplicate"
    assert not any(TOUCHED in _touched_in(r) for r in requests)
    assert client.app.state.runs.take_touched(subject_of(cid)) == set()


def test_the_suite_switch_keeps_saves_quiet(client):
    """Unmarked, End Scene starts no sweep (Decision 20)."""
    cid, sid, body = _ended(client)
    _install(client, from_cassette("campaign_flow"))

    assert _save(client, cid, sid, body).status_code == 200

    assert _sweeps(client, cid) == []


# ------------------------------------------------------- the per-run call bound
#
# §25.1 (Slice G Decision 4): a run makes at most one model call per pass and
# has at most two passes -- its own, and one follow-on carrying every ref the
# adopters pended before it took them. §25.2: the sweep's work runs on a worker
# thread, never on the event loop the turn loop shares.

MAP_REF = "thread:mara-s-map"


def _map(cid: str, sid: str) -> None:
    store.plot.set_movement(cid, "mara-s-map", "Mara's map", "open",
                            "The map turned up in Saltmarch.", sid)


@pytest.mark.reconcile
def test_a_burst_of_adopters_coalesces_into_one_follow_on(client):
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    _map(cid, sid)
    _key(client)
    held = _install(client, _held())
    first = _refresh(client, cid)
    held.await_held()
    try:
        for pid in (LEDGER_THREAD[0], "recover-the-harbour-ledger", "mara-s-map"):
            edit = _plot_edit(pid)
            continuity_routes.schedule_reconcile(client.app, cid, held, edits=[edit],
                                                 progress=_applied(edit))
        assert len(client.app.state.runs.for_subject(runs.campaign_subject(cid))) == 1
    finally:
        held.release()

    run = _settled(client, cid, first)
    assert run["state"] == "landed", run
    assert run["result"]["follow_on"] is True
    requests = _reconcile_requests(held)
    assert len(requests) == 2
    # The full first pass re-checks nothing as touched, so every touched block
    # in the follow-on came from an adopter -- and each pended ref has one of
    # its own. Titles alone would not do: two of the three also sit in the
    # similarity pair, which is asked about whether or not either was touched.
    assert _touched_ids(requests[0]) == []
    assert sorted(_touched_ids(requests[1])) == [
        LEDGER_THREAD[0], "mara-s-map", "recover-the-harbour-ledger"]
    assert client.app.state.runs.take_touched(runs.campaign_subject(cid)) == set()


def _touched_ids(request: dict) -> list[str]:
    """The record id of every touched re-check block in one reconcile prompt."""
    blocks = _touched_in(request).split("\n\nCandidate ")
    return [b.split("(plot thread): ", 1)[1].split(":", 1)[0]
            for b in blocks if b.rstrip().endswith(TOUCHED)]


def test_a_capped_sweep_says_so_in_its_run_and_log_row(client, monkeypatch):
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    _map(cid, sid)
    monkeypatch.setattr(reconcile, "RECONCILE_MAX_PAIRS", 1)
    _install(client, from_entries([_entry(_reply())]))

    run = _settled(client, cid, _refresh(client, cid))

    assert run["state"] == "landed", run
    assert run["result"]["pairs_capped"] is True
    assert _reconcile_row(cid)["pairs_capped"] is True


def _off_loop() -> bool:
    """True when the calling thread runs no event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return True
    return False


def _recorder(record: list, real):
    def recorded(*args, **kwargs):
        record.append(_off_loop())
        return real(*args, **kwargs)
    return recorded


def test_sweep_work_runs_off_the_event_loop(client, monkeypatch):
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    _key(client)
    fake = _install(client, from_entries([_entry(_reply(_duplicate()))]))
    seen: dict[str, list[bool]] = {}
    for name in ("discover", "persist_found", "select", "build_payload",
                 "persist_proposals"):
        seen[name] = []
        monkeypatch.setattr(reconcile, name, _recorder(seen[name], getattr(reconcile, name)))

    run = _settled(client, cid, _refresh(client, cid))

    assert run["state"] == "landed", run
    assert len(_reconcile_requests(fake)) == 1
    for name, record in seen.items():
        assert record, f"{name} never ran"
        assert all(record), f"{name} ran on the event loop"
