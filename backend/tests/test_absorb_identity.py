"""The identity phase inside absorb (continuity capstone spec §10.4, §28.3).

Absorb chains one identity step onto its extraction: the proposed-new plot
threads and commitments are examined against the stored same-type records, and
when any of them has a plausible neighbour one `decide()` over the examined
rows, chunked, decides which are the same business. These drive the real
endpoint -- the fifth `phases` row, the `identity` block, what is staged and
what a save writes -- with `llm_fakes.from_entries` answering by which prompt
is asking and `llm_fakes.FakeEmbeddings` standing in for the embeddings
provider.

Stored records are seeded in a scene of their own (`s0`, created before the
absorbed scene), never the one being absorbed.
"""

from __future__ import annotations

import asyncio
import functools
import importlib
import json
import time

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import decisions, embeddings, llm_errors, routes
from grimoire.llm import LLMClient
from grimoire.main import create_app
from grimoire.store import llm_connections
from grimoire.store.absorb import materializer
from grimoire.store.continuity import identity, similarity

from . import inference_fixtures, review_runs
from .llm_fakes import FakeEmbeddings, FakeLLM, SequencedProvider, decision_reply, from_entries
from .review_runs import (
    EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
    LEDGER_THREAD,
    RECOVER_THE_LEDGER,
    SALTMARCH_TITHE,
    identity_requests,
)

WHEN_EXTRACTION = {"system_contains": "You are absorbing a completed role-play scene"}
WHEN_IDENTITY = dict(zip(("system_contains", "user_contains"), review_runs.IDENTITY_MATCH,
                         strict=True))

#: A second proposed thread rewording `LEDGER_THREAD`.
FIND_THE_HARBOUR_LEDGER = {"title": "Find the harbour ledger",
                           "beat": "Winifred went looking for the harbour ledger again.",
                           "status": "open"}

#: A stored commitment, as (id, title, kind, status, due, beat).
MIDNIGHT_DEADLINE = ("the-midnight-deadline", "The midnight deadline", "threat", "open",
                     "midnight", "Seraphine must pay by midnight.")

#: A proposed commitment rewording `MIDNIGHT_DEADLINE`.
SERAPHINES_DEADLINE = {"title": "Seraphine's midnight deadline",
                       "beat": "Seraphine must pay the midnight deadline.",
                       "kind": "threat", "status": "open", "due": "midnight"}

DEGRADED_SEMANTIC = "semantic matching unavailable — basic matching used"
BUDGET_SEMANTIC = ("the absorb time budget ran out during semantic matching — "
                   "basic matching used")


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def scene(client):
    """(cid, s0, sid): a key on the active connection, a world "Realm" with
    Mara, a campaign, an earlier scene `s0` to seed records in, and a scene
    "Saltmarch" with two posts to absorb."""
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-active"})
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara", "version_name": "main"})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    s0 = store.scenes.create_scene(cid, "Saltmarch docks")
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Saltmarch"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "Something happened at the docks.")
    store.scenes.append_message(cid, sid, "assistant", "The keeper said nothing.")
    return cid, s0, sid


def _seed_ledger(cid, scene_id, status="open"):
    pid, title, beat = LEDGER_THREAD
    store.plot.set_movement(cid, pid, title, "open", beat, scene_id)
    if status != "open":
        store.plot.set_movement(cid, pid, "", status, "Winifred burned the ledger.", scene_id)


def _seed_deadline(cid, scene_id):
    mid, title, kind, status, due, beat = MIDNIGHT_DEADLINE
    store.commitments.set_movement(cid, mid, title, kind, status, due, beat, scene_id)


def _extraction(plot=(), owed=()):
    return json.dumps({"one_line": "o", "summary": "s", "keywords": [], "timeline_events": [],
                       "plot_movements": [dict(r) for r in plot],
                       "commitment_movements": [dict(r) for r in owed]})


def _row(decision, rid=None):
    """One examined row's answers, as the decide schema shapes them: its
    `decision`, an ``existing`` folded with the id of the record it names
    (``existing:<id>``, spec 7.4)."""
    return {"decision": identity.EXISTING_PREFIX + rid if rid is not None else decision}


def _llm(client, extraction, resolver=None, *, error=None):
    entries = [{"when": WHEN_EXTRACTION, "reply": extraction}]
    if error is not None:
        entries.append({"when": WHEN_IDENTITY, "error": error})
    elif resolver is not None:
        entries.append({"when": WHEN_IDENTITY, "reply": resolver})
    fake = from_entries(entries)
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    return fake


def _absorb(client, cid, sid):
    r = review_runs.absorb(client, cid, sid)
    assert r.status_code == 200, r.json()
    return r.json()


def _phase(body, name):
    return next(p for p in body["phases"] if p["name"] == name)


def _plot_edits(body):
    return [e for e in body["edits"] if e["kind"] == "plot"]


def _edit(body, eid):
    return next(e for e in body["edits"] if e["id"] == eid)


def _save(client, cid, sid, body, edits=None):
    return client.put(f"/api/campaigns/{cid}/scenes/{sid}/chronicle", json={
        "one_line": body["one_line"], "summary": body["summary"],
        "keywords": body["keywords"], "timeline_events": body["timeline_events"],
        "edits": body["edits"] if edits is None else edits,
        "commit_token": body["commit_token"]})


def _proposed(kind, row, sid):
    """The subject `identity.examine` builds for a proposed row."""
    return similarity.subject(kind, "proposed:test",
                              {"title": row.get("title", ""),
                               "beats": [{"text": row["beat"], "scene": sid}],
                               "kind": row.get("kind", ""), "due": row.get("due", "")},
                              scenes={sid})


def _pooled(cid, ref):
    kind = ref.split(":", 1)[0]
    return {s.ref: s for s in similarity.pool(cid, kind)}[ref]


def _assert_lexical_candidate(cid, sid, kind, row, ref):
    sig = similarity.lexical(_proposed(kind, row, sid), _pooled(cid, ref))
    assert sig["tokens"] >= similarity.TOKEN_FLOOR, sig
    assert similarity.plausible(sig), sig


def _configure_embeddings(monkeypatch, double):
    conn = llm_connections.create_connection("openai_compatible", "Vectors",
                                             base_url="https://vectors.example/v1",
                                             api_key="sk-x", model="", post_process="none")
    inference_fixtures.embedding(conn, "embed-1")
    monkeypatch.setattr(similarity, "_CLIENT", double)
    return double


def _identity_errors(cid):
    return [r for r in store.errors.summary(campaign=cid)["rows"]
            if r.get("task") == "continuity-identity" or r.get("module") == "continuity-identity"]


# ------------------------------------------------------------- skipping


def test_a_new_record_with_no_close_neighbour_makes_no_identity_call(client, scene):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    sig = similarity.lexical(_proposed("thread", SALTMARCH_TITHE, sid),
                             _pooled(cid, "thread:find-the-ledger"))
    assert sig["tokens"] < similarity.WEAK_TOKEN and sig["chars"] < similarity.WEAK_CHAR
    assert sig["scenes"] == [] and sig["actors"] == []
    fake = _llm(client, _extraction(plot=[SALTMARCH_TITHE]))

    body = _absorb(client, cid, sid)

    assert identity_requests(fake) == []
    phase = _phase(body, "identity")
    assert (phase["status"], phase["attempted"]) == ("skipped", False)
    assert body["identity"]["reason"] == "no close existing records"
    assert body["identity"]["matching"] == "basic"


def test_an_absorb_proposing_no_records_skips_identity_with_matching_set(client, scene):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _llm(client, _extraction())

    body = _absorb(client, cid, sid)

    assert body["identity"]["status"] == "skipped"
    assert body["identity"]["reason"] == "no new records proposed"
    assert body["identity"]["attempted"] is False
    assert body["identity"]["counts"] == {}
    assert body["identity"]["matching"] == "basic"


def test_an_absorb_that_only_moves_stored_records_proposes_none_and_logs_no_row(client, scene):
    """§10.2: a row whose explicit id names a stored record is not
    proposed-new, so an absorb whose rows all do proposes nothing -- the
    same phase block as an empty extraction, and (§29) no log row."""
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    fake = _llm(client, _extraction(plot=[{"id": LEDGER_THREAD[0], "beat": "Mara looked again.",
                                          "status": "advanced"}]))

    body = _absorb(client, cid, sid)

    assert identity_requests(fake) == []
    assert body["identity"]["status"] == "skipped"
    assert body["identity"]["reason"] == "no new records proposed"
    assert body["identity"]["attempted"] is False
    assert body["identity"]["counts"] == {}
    assert body["identity"]["matching"] == "basic"
    assert [r for r in store.logs.scan(level="info", campaign=cid)
            if r.get("message") == "continuity identity check"] == []


def test_identity_matching_agrees_with_get_continuity(client, scene, monkeypatch):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _llm(client, _extraction(plot=[SALTMARCH_TITHE]))
    body = _absorb(client, cid, sid)
    assert body["identity"]["matching"] == \
        client.get(f"/api/campaigns/{cid}/continuity").json()["matching"] == "basic"

    _configure_embeddings(monkeypatch, FakeEmbeddings())
    other = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Second"}).json()["id"]
    store.scenes.append_message(cid, other, "user", "Mara counted the coins.")
    body = _absorb(client, cid, other)
    assert body["identity"]["matching"] == \
        client.get(f"/api/campaigns/{cid}/continuity").json()["matching"] == "semantic"


# ------------------------------------------------- what reaches the ledgers


def test_identity_fields_never_reach_the_ledgers(client, scene):
    """A regression pin: materialize spells payloads field by field and
    `set_movement` writes named fields only, so this passes once the rows
    stage at all."""
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    unexamined = {**SALTMARCH_TITHE, "why_new": "A tithe nobody owed before.",
                  "distinguished_from": ["find-the-ledger"]}
    examined = {**RECOVER_THE_LEDGER, "why_new": "The first search ended.",
                "distinguished_from": ["find-the-ledger"]}
    _assert_lexical_candidate(cid, sid, "thread", RECOVER_THE_LEDGER, "thread:find-the-ledger")
    _llm(client, _extraction(plot=[unexamined, examined]),
         decision_reply(_row("new"), rationales=("a second search",)))

    body = _absorb(client, cid, sid)

    plots = _plot_edits(body)
    assert len(plots) == 2
    assert [("identity_check" in e) for e in plots] == [False, True]
    for edit in plots:
        assert "why_new" not in edit["payload"]
        assert "distinguished_from" not in edit["payload"]
    assert _save(client, cid, sid, body).status_code == 200
    for rec in [*store.plot.read(cid).values(), *store.commitments.read(cid).values()]:
        assert "why_new" not in rec and "distinguished_from" not in rec


def test_a_failing_identity_staging_pass_fails_only_the_phase(client, scene, monkeypatch):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)

    def broken(*a, **k):
        raise RuntimeError("alternatives broke")

    monkeypatch.setattr(materializer, "_attach_alternatives", broken)
    _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
         decision_reply(_row("new"), rationales=("a second search",)))

    body = _absorb(client, cid, sid)

    assert _phase(body, "identity")["status"] == "failed"
    assert body["identity"]["status"] == "failed"
    assert body["identity"]["reason"] == (
        "the duplicate check's staging step failed; rows staged without alternatives")
    [edit] = _plot_edits(body)
    assert edit["identity_check"]["alternatives"] == []


def test_a_defect_in_the_rewrite_fails_only_the_phase(client, scene, monkeypatch):
    """Steps 9-10 (rewrite, counts, log row) sit in a try of their own: a
    defect there stages the extraction's rows as extracted, never fails the
    absorb."""
    cid, s0, sid = scene
    _seed_ledger(cid, s0)

    def broken(self, parsed):
        raise ZeroDivisionError("division by zero")

    monkeypatch.setattr(identity.Examination, "rewritten", broken)
    _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
         decision_reply(_row("existing", "find-the-ledger")))

    body = _absorb(client, cid, sid)

    assert _phase(body, "identity")["status"] == "failed"
    block = body["identity"]
    assert (block["status"], block["counts"]) == ("failed", {})
    assert block["reason"] == "duplicate check failed: division by zero"
    [edit] = _plot_edits(body)
    assert "identity_check" not in edit
    assert edit["target"]["id"] == "recover-the-harbour-ledger"
    assert len(_identity_errors(cid)) == 1


def test_an_examination_error_fails_only_the_phase(client, scene, monkeypatch):
    """A non-LLM failure in `examine` (a store read) is the phase's status,
    not the absorb's: the row stages as new and the resolver is never asked."""
    cid, s0, sid = scene
    _seed_ledger(cid, s0)

    def broken(*a, **k):
        raise OSError("disk gone")

    monkeypatch.setattr(identity, "examine", broken)
    fake = _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
                decision_reply(_row("existing", "find-the-ledger")))

    body = _absorb(client, cid, sid)

    assert identity_requests(fake) == []
    block = body["identity"]
    assert (block["status"], block["attempted"]) == ("failed", False)
    assert block["reason"] == "duplicate check failed: disk gone"
    [edit] = _plot_edits(body)
    assert "identity_check" not in edit
    assert edit["target"]["id"] == "recover-the-harbour-ledger"
    assert len(_identity_errors(cid)) == 1


# ------------------------------------------------------------- budget


def test_a_budget_cut_embed_is_the_budget_not_the_provider(client, scene, monkeypatch):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    client.put("/api/config", json={"absorb_budget": "60"})
    clock = [0.0]
    monkeypatch.setattr(routes.scenes, "_clock", lambda: clock[0])

    def cut(text):
        # The client's own words for a read the deadline cut mid-flight.
        clock[0] = 1e6
        raise embeddings.EmbeddingsError("network", "embeddings response exceeded 30.0s",
                                         code=embeddings.DEADLINE)

    _configure_embeddings(monkeypatch, FakeEmbeddings(vector_for=cut))
    fake = _llm(client, _extraction(plot=[SALTMARCH_TITHE]))

    body = _absorb(client, cid, sid)

    assert identity_requests(fake) == []
    block = body["identity"]
    assert (block["status"], block["budget_exhausted"], block["reason"]) == (
        "degraded", True, BUDGET_SEMANTIC)
    assert block["fallback"] == BUDGET_SEMANTIC
    assert _identity_errors(cid) == []
    # The meter agrees: the absorb's clock is not the provider failing, so
    # the embed row is `aborted` and nothing at all reaches the error store.
    assert store.errors.summary(campaign=cid)["rows"] == []
    embeds = [r for r in store.usage.calls(campaign=cid) if r.get("operation") == "embed"]
    assert embeds and all(r["status"] == "aborted" for r in embeds)


def test_budget_refused_identity_reports_budget_exhausted(client, scene, monkeypatch):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    client.put("/api/config", json={"absorb_budget": "60"})
    clock = [0.0]
    monkeypatch.setattr(routes.scenes, "_clock", lambda: clock[0])
    real = identity.examine
    monkeypatch.setattr(identity, "examine",
                        lambda *a, **k: (clock.__setitem__(0, 1e6), real(*a, **k))[1])
    fake = _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
                decision_reply(_row("new")))

    body = _absorb(client, cid, sid)

    assert identity_requests(fake) == []
    block = body["identity"]
    assert (block["status"], block["budget_exhausted"], block["attempted"]) == (
        "failed", True, False)
    assert block["reason"] == "the absorb time budget ran out before the duplicate check could run"
    [edit] = _plot_edits(body)
    assert edit["identity_check"]["status"] == "hint_only"


# ------------------------------------------------------------- deciding


def test_close_candidate_mapped_to_existing_rewrites_the_row(client, scene):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _assert_lexical_candidate(cid, sid, "thread", RECOVER_THE_LEDGER, "thread:find-the-ledger")
    _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
         decision_reply(_row("existing", "find-the-ledger"), rationales=("the same search",)))

    body = _absorb(client, cid, sid)

    [edit] = _plot_edits(body)
    assert edit["target"] == {"kind": "plot", "id": "find-the-ledger"}
    assert edit["payload"]["status"] == "advanced"
    ic = edit["identity_check"]
    assert (ic["decision"], ic["status"]) == ("existing", "accepted")
    [as_new] = [a for a in ic["alternatives"] if a["before"] == ""]
    assert as_new["payload"]["title"] == RECOVER_THE_LEDGER["title"]
    assert body["identity"]["status"] == "ok"


def test_one_batched_call_for_several_ambiguous_rows(client, scene):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _seed_deadline(cid, s0)
    _assert_lexical_candidate(cid, sid, "thread", RECOVER_THE_LEDGER, "thread:find-the-ledger")
    _assert_lexical_candidate(cid, sid, "commitment", SERAPHINES_DEADLINE,
                              "commitment:the-midnight-deadline")
    fake = _llm(client, _extraction(plot=[RECOVER_THE_LEDGER], owed=[SERAPHINES_DEADLINE]),
                decision_reply(_row("new"), _row("new")))

    body = _absorb(client, cid, sid)

    [request] = identity_requests(fake)
    user = "\n".join(m["content"] for m in request["messages"] if m["role"] == "user")
    assert "Item 0" in user and "Item 1" in user
    assert body["identity"]["status"] == "ok"
    assert body["identity"]["counts"]["examined"] == 2


def test_resolver_naming_an_unknown_id_marks_the_row_uncertain_and_low(client, scene):
    """An ``existing`` naming a record the row was not offered is no option
    of its folded decision (spec 7.4), so it reads as any word outside the
    options does: ``uncertain``, accepted, and the row stays new (the gate's
    `unoffered-id` ruling)."""
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
         decision_reply(_row("existing", "no-such-thread")))

    body = _absorb(client, cid, sid)

    [edit] = _plot_edits(body)
    assert edit["target"]["id"] != "find-the-ledger"
    assert (edit["identity_check"]["decision"], edit["identity_check"]["status"]) == (
        "uncertain", "accepted")
    assert edit["review"]["band"] == "low"


def test_resolver_naming_a_closed_thread_never_reopens_it(client, scene):
    cid, s0, sid = scene
    _seed_ledger(cid, s0, status="closed")
    _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
         decision_reply(_row("existing", "find-the-ledger")))

    body = _absorb(client, cid, sid)

    [edit] = _plot_edits(body)
    assert edit["target"]["id"] != "find-the-ledger"
    assert edit["identity_check"]["decision"] == "uncertain"
    assert edit["review"]["band"] == "low"
    assert _save(client, cid, sid, body).status_code == 200
    assert store.plot.get(cid, "find-the-ledger")["status"] == "closed"
    assert store.plot.get(cid, edit["target"]["id"])["title"] == RECOVER_THE_LEDGER["title"]


def test_two_rows_mapped_to_one_record_downgrade_the_second(client, scene):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _assert_lexical_candidate(cid, sid, "thread", FIND_THE_HARBOUR_LEDGER,
                              "thread:find-the-ledger")
    _llm(client, _extraction(plot=[RECOVER_THE_LEDGER, FIND_THE_HARBOUR_LEDGER]),
         decision_reply(_row("existing", "find-the-ledger"), _row("existing", "find-the-ledger")))

    body = _absorb(client, cid, sid)

    first, second = _plot_edits(body)
    assert first["target"]["id"] == "find-the-ledger"
    assert (first["identity_check"]["decision"], first["identity_check"]["status"]) == (
        "existing", "accepted")
    assert second["target"]["id"] != "find-the-ledger"
    assert (second["identity_check"]["decision"], second["identity_check"]["status"]) == (
        "uncertain", "downgraded")
    assert second["review"]["band"] == "low"


# -------------------------------------------------------- failure matrix


def test_undecodable_resolver_reply_fails_the_phase_and_keeps_rows_with_hints(client, scene):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER, "I cannot tell.")

    body = _absorb(client, cid, sid)

    assert body["identity"]["status"] == "failed"
    assert body["identity"]["reason"] == "the duplicate check returned no readable answer"
    [edit] = _plot_edits(body)
    assert edit["target"]["id"] != "find-the-ledger"
    assert edit["identity_check"]["status"] == "hint_only"
    assert edit["identity_check"]["alternatives"]


def test_resolver_error_fails_only_the_phase(client, scene):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
         error={"kind": "network", "message": "connection reset"})

    body = _absorb(client, cid, sid)

    block = body["identity"]
    assert (block["status"], block["attempted"]) == ("failed", True)
    assert "connection reset" in block["reason"]
    [edit] = _plot_edits(body)
    assert edit["identity_check"]["status"] == "hint_only"


def test_partially_answered_batch_is_degraded(client, scene):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _llm(client, _extraction(plot=[RECOVER_THE_LEDGER, FIND_THE_HARBOUR_LEDGER]),
         decision_reply(_row("new"), rationales=("a second search",)))

    body = _absorb(client, cid, sid)

    assert (body["identity"]["status"], body["identity"]["reason"]) == (
        "degraded", "the duplicate check left some rows unanswered")
    first, second = _plot_edits(body)
    assert first["identity_check"]["decision"] == "new"
    assert (second["identity_check"]["decision"], second["identity_check"]["status"]) == (
        "unchecked", "hint_only")
    assert second["review"]["band"] == "low"


def test_decodable_reply_answering_no_row_is_degraded(client, scene):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _llm(client, _extraction(plot=[RECOVER_THE_LEDGER, FIND_THE_HARBOUR_LEDGER]),
         "{}")

    body = _absorb(client, cid, sid)

    assert (body["identity"]["status"], body["identity"]["reason"]) == (
        "degraded", "the duplicate check answered none of the rows")
    for edit in _plot_edits(body):
        assert (edit["identity_check"]["decision"], edit["identity_check"]["status"]) == (
            "unchecked", "hint_only")


def test_embedding_failure_degrades_the_phase_and_keeps_lexical_candidates(
        client, scene, monkeypatch):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _configure_embeddings(monkeypatch, FakeEmbeddings(
        error=embeddings.EmbeddingsError("network", "connection refused")))
    fake = _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
                decision_reply(_row("new"), rationales=("a second search",)))

    body = _absorb(client, cid, sid)

    block = body["identity"]
    assert (block["status"], block["reason"], block["matching"]) == (
        "degraded", DEGRADED_SEMANTIC, "semantic")
    assert len(identity_requests(fake)) == 1
    [edit] = _plot_edits(body)
    assert [c["ref"] for c in edit["identity_check"]["candidates"]] == ["thread:find-the-ledger"]
    [row] = _identity_errors(cid)
    assert (row["task"], row["kind"]) == ("continuity-identity", "network")
    for value in row.values():
        assert RECOVER_THE_LEDGER["title"] not in str(value)
        assert RECOVER_THE_LEDGER["beat"] not in str(value)


def test_an_embedding_failure_survives_a_partial_resolver_answer(client, scene, monkeypatch):
    # The phase's `reason` can name one thing, and a partial answer outranks
    # the embed. The fallback is a fact about where the possible-match lists
    # came from, so it is reported on its own field, not lost to the reason.
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _configure_embeddings(monkeypatch, FakeEmbeddings(
        error=embeddings.EmbeddingsError("network", "connection refused")))
    _llm(client, _extraction(plot=[RECOVER_THE_LEDGER, FIND_THE_HARBOUR_LEDGER]),
         decision_reply(_row("new"), rationales=("a second search",)))

    block = _absorb(client, cid, sid)["identity"]

    assert (block["status"], block["reason"], block["matching"], block["fallback"]) == (
        "degraded", "the duplicate check left some rows unanswered", "semantic",
        DEGRADED_SEMANTIC)


def test_an_embedding_failure_survives_a_failed_resolver(client, scene, monkeypatch):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _configure_embeddings(monkeypatch, FakeEmbeddings(
        error=embeddings.EmbeddingsError("network", "connection refused")))
    _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
         error={"kind": "network", "message": "connection reset"})

    body = _absorb(client, cid, sid)

    block = body["identity"]
    assert (block["status"], block["fallback"]) == ("failed", DEGRADED_SEMANTIC)
    assert "connection reset" in block["reason"]
    [edit] = _plot_edits(body)
    assert edit["identity_check"]["status"] == "hint_only"


def test_semantic_matching_that_stood_reports_no_fallback(client, scene, monkeypatch):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _configure_embeddings(monkeypatch, FakeEmbeddings())
    _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
         decision_reply(_row("new")))

    block = _absorb(client, cid, sid)["identity"]

    assert (block["status"], block["matching"], block["fallback"]) == ("ok", "semantic", "")


def test_identity_embedding_deadline_never_exceeds_the_absorb_budget(client, scene, monkeypatch):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    client.put("/api/config", json={"absorb_budget": "5"})
    double = _configure_embeddings(monkeypatch, FakeEmbeddings())
    fake = _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
                decision_reply(_row("new")))

    _absorb(client, cid, sid)

    assert double.deadlines
    assert all(d is not None and d <= time.monotonic() + 5 for d in double.deadlines)
    # The scene's usage rows are the LLM requests plus one embed row per
    # embeddings request, each charged to the absorb's campaign and scene.
    rows = [r for r in store.usage.calls(campaign=cid) if r.get("scene") == sid]
    llm_rows = [r for r in rows if r.get("operation") != "embed"]
    embed_rows = [r for r in rows if r.get("operation") == "embed"]
    assert len(llm_rows) == len(fake.requests) == 2
    assert all(r.get("model") != "embed-1" for r in llm_rows)
    for row in embed_rows:
        assert (row["task"], row["operation"], row["campaign"], row["scene"]) == (
            "continuity-similarity", "embed", cid, sid)
    assert len(embed_rows) == len(double.calls)


def test_misrouted_identity_reports_itself_and_leaves_absorb_standing(client, scene):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _keyless(client)
    fake = _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
                decision_reply(_row("new")))

    body = _absorb(client, cid, sid)

    assert identity_requests(fake) == []
    assert body["identity"]["status"] == "failed"
    assert "key" in (body["identity"]["reason"] or "").lower()
    [edit] = _plot_edits(body)
    assert edit["identity_check"]["status"] == "hint_only"


def test_with_no_identity_connection_nothing_is_sent_to_the_embeddings_provider(
        client, scene, monkeypatch):
    """§10.4: with no connection the step runs deterministic neighbours only.
    The resolver can never run, so embedding the proposed rows and their
    neighbours would send campaign prose to the provider, on the extraction's
    critical path, for a check nobody can make."""
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    double = _configure_embeddings(monkeypatch, FakeEmbeddings())
    _keyless(client)
    fake = _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER)

    body = _absorb(client, cid, sid)

    assert identity_requests(fake) == []
    assert double.calls == []
    block = body["identity"]
    assert (block["status"], block["fallback"]) == ("failed", "")
    assert "key" in (block["reason"] or "").lower()
    [edit] = _plot_edits(body)
    assert edit["identity_check"]["status"] == "hint_only"
    assert edit["identity_check"]["candidates"]


# ------------------------------------------------------------- saving


def test_alternatives_pass_check_conflicts_at_save(client, scene):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
         decision_reply(_row("new"), rationales=("a second search",)))
    body = _absorb(client, cid, sid)
    edits = [{k: v for k, v in e.items() if k != "identity_check"} for e in body["edits"]]
    index = next(i for i, e in enumerate(body["edits"]) if e.get("identity_check"))
    [alt] = [a for a in body["edits"][index]["identity_check"]["alternatives"]
             if a["target"]["id"] == "find-the-ledger"]
    edits[index] = alt

    r = _save(client, cid, sid, body, edits)

    assert r.status_code == 200, r.json()
    beats = [b["text"] for b in store.plot.get(cid, "find-the-ledger")["beats"]]
    assert RECOVER_THE_LEDGER["beat"] in beats


#: `SERAPHINES_DEADLINE` with the ``"due": ""`` a model filling every key sends.
SERAPHINES_BLANK_DUE = {**SERAPHINES_DEADLINE, "due": ""}


def test_an_accepted_retarget_with_a_blank_due_keeps_the_stored_deadline(client, scene):
    cid, s0, sid = scene
    _seed_deadline(cid, s0)
    _assert_lexical_candidate(cid, sid, "commitment", SERAPHINES_BLANK_DUE,
                              "commitment:the-midnight-deadline")
    _llm(client, _extraction(owed=[SERAPHINES_BLANK_DUE]),
         decision_reply(_row("existing", "the-midnight-deadline")))
    body = _absorb(client, cid, sid)

    edit = _edit(body, "commitment:the-midnight-deadline")
    assert (edit["identity_check"]["decision"], edit["identity_check"]["status"]) == (
        "existing", "accepted")
    assert edit["payload"]["due"] is None
    assert "due midnight" in edit["label"]
    assert _save(client, cid, sid, body).status_code == 200
    assert store.commitments.get(cid, "the-midnight-deadline")["due"] == "midnight"


def test_a_swapped_in_alternative_with_a_blank_due_keeps_the_stored_deadline(client, scene):
    cid, s0, sid = scene
    _seed_deadline(cid, s0)
    _llm(client, _extraction(owed=[SERAPHINES_BLANK_DUE]),
         decision_reply(_row("new"), rationales=("a second deadline",)))
    body = _absorb(client, cid, sid)
    edits = [{k: v for k, v in e.items() if k != "identity_check"} for e in body["edits"]]
    index = next(i for i, e in enumerate(body["edits"]) if e.get("identity_check"))
    [alt] = [a for a in body["edits"][index]["identity_check"]["alternatives"]
             if a["target"]["id"] == "the-midnight-deadline"]
    assert alt["payload"]["due"] is None
    assert "due midnight" in alt["label"]
    edits[index] = alt

    r = _save(client, cid, sid, body, edits)

    assert r.status_code == 200, r.json()
    assert store.commitments.get(cid, "the-midnight-deadline")["due"] == "midnight"


def test_a_save_body_still_carrying_alternatives_is_accepted(client, scene):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
         decision_reply(_row("new"), rationales=("a second search",)))
    body = _absorb(client, cid, sid)
    assert any(e.get("identity_check", {}).get("alternatives") for e in body["edits"])

    first = _save(client, cid, sid, body)
    second = _save(client, cid, sid, body)

    assert first.status_code == 200 and second.status_code == 200
    assert second.json() == first.json()


# ------------------------------------------------------- what is recorded


def test_identity_log_row_carries_counts_only(client, scene):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
         decision_reply(_row("new"), rationales=("a second search",)))

    _absorb(client, cid, sid)

    [row] = [r for r in store.logs.scan(level="info", campaign=cid)
             if r.get("message") == "continuity identity check"]
    assert row["kind"] == "continuity-identity"
    for key in ("proposed", "examined", "candidates", "deterministic", "semantic",
                "embedded", "new", "existing", "uncertain", "unchecked"):
        assert isinstance(row[key], int) and not isinstance(row[key], bool), key
    assert (row["proposed"], row["examined"], row["new"]) == (1, 1, 1)
    for value in row.values():
        for text in (RECOVER_THE_LEDGER["title"], RECOVER_THE_LEDGER["beat"],
                     "a second search"):
            assert text not in str(value)


#: The identity row's string fields (§29): modes only, each from a closed set.
IDENTITY_MODES = {"kind", "campaign", "scene", "status", "matching", "embedding",
                  "embedding_error"}
#: Its counts (§29): `Examination.counts()`, one per check decision among them.
IDENTITY_COUNTS = ({"proposed", "examined", "candidates", "deterministic", "semantic",
                    "embedded", "downgraded", "hint_only"} | set(identity.CHECK_DECISIONS))
IDENTITY_ROW_KEYS = IDENTITY_MODES | IDENTITY_COUNTS
#: What `logs.record` adds to every row.
ROW_ENVELOPE = {"ts", "level", "module", "message"}

#: field -> the values it may take. The three shared with the sweep's row, then
#: the identity phase's own status.
MODES = {
    "matching": {"basic", "semantic"},
    "embedding": {"off", "configured", "failure"},
    "embedding_error": {""} | set(llm_errors.KINDS) | {"unexpected"},
    "status": {"ok", "degraded", "failed", "skipped"},
}


def _row_texts(fake) -> list[str]:
    """Every stripped line of 12 or more characters from every message the
    fake received: a prompt line that leaked into a row is one of these."""
    return [line.strip()
            for request in fake.requests for message in request["messages"]
            for line in str(message.get("content", "")).splitlines()
            if len(line.strip()) >= 12]


def _dumped(row: dict) -> str:
    """The row as text, with non-ASCII kept: the default `json.dumps` writes an
    em dash as an escape, and both continuity prompts put em dashes in their
    lines, so a leaked line would never match the escaped dump."""
    return json.dumps(row, ensure_ascii=False)


def _leaked(text: str, dumped: str) -> bool:
    """Whether `text` sits in a `_dumped` row, as written or as JSON spells it:
    a prompt line holding a double quote is stored with that quote escaped, so
    the line as written never matches the dump."""
    return text in dumped or json.dumps(text, ensure_ascii=False)[1:-1] in dumped


def _keyless(client):
    """The continuity route pinned to an OpenRouter provider with no key."""
    keyless = client.post("/api/llm-connections",
                          json={"kind": "openrouter", "name": "Keyless"}).json()["id"]
    inference_fixtures.put_settings(client, {"routes": {"continuity": {
        "use": "model", "pin": {"provider": keyless, "model": "vendor/keyless"}}}})


def _embedding_failure(monkeypatch):
    _configure_embeddings(monkeypatch, FakeEmbeddings(
        error=embeddings.EmbeddingsError("network", "connection refused")))


def _examination_raised(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("Mara's oath")

    monkeypatch.setattr(identity, "examine", broken)


@pytest.mark.parametrize("case", ["ok", "no connection", "embedding failure",
                                  "examination raised"])
def test_identity_log_row_is_counts_and_closed_modes_only(client, scene, monkeypatch, case):
    """§29: the row's exact key set, every string field from a closed set, every
    count an int, and no title, beat, reason or prompt line anywhere in it --
    whichever way the phase went."""
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    setup = {"no connection": lambda: _keyless(client),
             "embedding failure": lambda: _embedding_failure(monkeypatch),
             "examination raised": lambda: _examination_raised(monkeypatch)}
    setup.get(case, lambda: None)()
    fake = _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
                decision_reply(_row("new"), rationales=("a second search",)))

    body = _absorb(client, cid, sid)

    [row] = [r for r in store.logs.scan(level="info", campaign=cid)
             if r.get("message") == "continuity identity check"]
    raised = case == "examination raised"
    assert row["status"] == {"ok": "ok", "no connection": "failed",
                             "embedding failure": "degraded",
                             "examination raised": "failed"}[case], "the case set up its phase"
    assert set(row) - ROW_ENVELOPE == (IDENTITY_MODES if raised else IDENTITY_ROW_KEYS)
    assert (row["kind"], row["campaign"], row["scene"]) == ("continuity-identity", cid, sid)
    counts = {k: row[k] for k in IDENTITY_COUNTS if k in row}
    for key, value in counts.items():
        assert isinstance(value, int) and not isinstance(value, bool), key
    if not raised:
        assert counts["deterministic"] + counts["semantic"] == counts["candidates"]
    for field, allowed in MODES.items():
        if raised and field == "embedding":
            assert row[field] == ""
        else:
            assert row[field] in allowed, (field, row[field])
    dumped = _dumped(row)
    if case == "embedding failure":
        assert row["embedding_error"] == "network"
        assert "connection refused" not in dumped
    if raised:
        # The phase says why to the reader; the row keeps only that it failed.
        assert "Mara's oath" in body["identity"]["reason"]
        assert "Mara's oath" not in dumped
    texts = [*LEDGER_THREAD[1:], RECOVER_THE_LEDGER["title"], RECOVER_THE_LEDGER["beat"],
             "a second search", *_row_texts(fake)]
    for text in texts:
        assert not _leaked(text, dumped), text


def test_identity_meter_files_under_its_own_task(client, scene):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
         decision_reply(_row("new")))

    _absorb(client, cid, sid)

    tasks = [r.get("task") for r in store.usage.calls(campaign=cid)]
    assert "continuity-identity" in tasks


def test_the_identity_examination_runs_off_the_event_loop(client, scene, monkeypatch):
    """§25.2: the examination reads the ledgers and scores pairs, so it runs on
    a worker thread rather than on the event loop the turn loop shares."""
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _assert_lexical_candidate(cid, sid, "thread", RECOVER_THE_LEDGER, "thread:find-the-ledger")
    _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
         decision_reply(_row("new")))
    seen: list[bool] = []
    real = identity.examine

    def recorded(*args, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            seen.append(True)
        else:
            seen.append(False)
        return real(*args, **kwargs)

    monkeypatch.setattr(identity, "examine", recorded)

    body = _absorb(client, cid, sid)

    assert body["identity"]["counts"]["examined"] == 1
    assert seen == [True]


# ------------------------------------------- through decide() (slice G)
#
# The check is one `inference.decide()` item per examined row, chunked at
# `decisions.MAX_ITEMS_PER_CALL` with one metered call per chunk, on the
# continuity route's Decision role.

ROW_0 = "Proposed plot thread: " + RECOVER_THE_LEDGER["title"]
ROW_1 = "Proposed plot thread: " + FIND_THE_HARBOUR_LEDGER["title"]


def _two_rows():
    """Two examined plot rows, each rewording the stored ledger thread."""
    return _extraction(plot=[RECOVER_THE_LEDGER, FIND_THE_HARBOUR_LEDGER])


def _one_per_chunk(monkeypatch):
    """`decide()` chunks one item per call, so two rows are two calls."""
    monkeypatch.setattr(decisions, "chunks", functools.partial(decisions.chunks, size=1))


def _identity_rows(cid):
    return [r for r in store.usage.calls(campaign=cid) if r.get("task") == "continuity-identity"]


def _install(client, fake):
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    return fake


def _checks(body):
    return [(e["identity_check"]["decision"], e["identity_check"]["status"])
            for e in _plot_edits(body)]


def test_identity_runs_on_decide_one_item_per_row(client, scene):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _seed_deadline(cid, s0)
    fake = _llm(client, _extraction(plot=[RECOVER_THE_LEDGER], owed=[SERAPHINES_DEADLINE]),
                decision_reply(_row("existing", "find-the-ledger"), _row("new"),
                               rationales=("the same search", "a second deadline")))

    body = _absorb(client, cid, sid)

    [request] = identity_requests(fake)
    system, user = request["messages"]
    assert system["content"].startswith("You answer closed questions about material")
    assert "Item 0\n\nProposed plot thread: " + RECOVER_THE_LEDGER["title"] in user["content"]
    assert ("Item 1\n\nProposed commitment: " + SERAPHINES_DEADLINE["title"]
            in user["content"])
    assert identity.explain() in user["content"]
    schema = fake.schemas[fake.requests.index(request)]
    assert set(schema["properties"]) == {"0", "1"}
    assert schema["properties"]["0"]["properties"]["answers"]["required"] == [
        identity.DECISION_ID]
    [plot] = _plot_edits(body)
    assert plot["target"] == {"kind": "plot", "id": "find-the-ledger"}
    assert plot["identity_check"]["reason"] == "the same search"
    [owed] = [e for e in body["edits"] if e["kind"] == "commitment"]
    assert (owed["identity_check"]["decision"], owed["identity_check"]["reason"]) == (
        "new", "a second deadline")
    assert body["identity"]["status"] == "ok"


def test_identity_meters_one_row_per_chunk(client, scene, monkeypatch):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _one_per_chunk(monkeypatch)
    fake = _llm(client, _two_rows(), decision_reply(_row("new")))

    body = _absorb(client, cid, sid)

    assert len(identity_requests(fake)) == 2
    assert [r["status"] for r in _identity_rows(cid)] == ["ok", "ok"]
    assert _checks(body) == [("new", "accepted"), ("new", "accepted")]
    assert body["identity"]["status"] == "ok"


def _identity_capture(cid, sid):
    """The duplicate check's one prompt-log entry (roadmap 01b)."""
    (row,) = [e for e in store.prompt_log.list_entries(cid, sid)
              if e["task"] == "continuity-identity"]
    assert row["operation"] == "decide"
    entry = store.prompt_log.read_entry(cid, row["id"], scene=sid)
    assert entry is not None and entry["sections"][-1]["id"] == "decision"
    return entry, json.loads(entry["sections"][-1]["text"])


def test_a_two_chunk_check_is_one_capture(client, scene, monkeypatch):
    """Roadmap 01b, test 1: one scope around the chunked check files one
    entry, each chunk's messages numbered by call and each call's `at` the
    batch positions it carried."""
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _one_per_chunk(monkeypatch)
    fake = _llm(client, _two_rows(), decision_reply(_row("new")))
    _absorb(client, cid, sid)
    entry, envelope = _identity_capture(cid, sid)
    assert envelope["task"] == "continuity-identity"
    assert [(c["stage"], c["at"], c["part"]) for c in envelope["calls"]] == [
        (0, [0], ""), (0, [1], "")]
    ids = [s["id"] for s in entry["sections"]]
    assert ids == ["c0_message_0", "c0_message_1", "c1_message_0", "c1_message_1",
                   "decision"]
    sent = [m["content"] for r in identity_requests(fake) for m in r["messages"]]
    assert sorted(s["text"] for s in entry["sections"][:-1]) == sorted(sent)


def test_a_failed_check_is_captured_with_its_kind_and_not_its_text(client, scene):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _llm(client, _extraction(plot=[RECOVER_THE_LEDGER]),
         error={"kind": "network", "message": "connection reset at sk-...abcd"})
    body = _absorb(client, cid, sid)
    assert body["identity"]["status"] == "failed"
    entry, envelope = _identity_capture(cid, sid)
    (call,) = envelope["calls"]
    assert call["error_kind"] == "network" and "error" not in call
    assert "sk-...abcd" not in json.dumps(entry)


def test_a_failed_second_chunk_leaves_the_first_chunks_rows_decided(
        client, scene, monkeypatch):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _one_per_chunk(monkeypatch)
    _install(client, from_entries([
        {"when": WHEN_EXTRACTION, "reply": _two_rows()},
        {"when": {**WHEN_IDENTITY, "user_contains": ROW_1},
         "error": {"kind": "network", "message": "connection reset"}},
        {"when": WHEN_IDENTITY, "reply": decision_reply(_row("new"))}]))

    body = _absorb(client, cid, sid)

    assert _checks(body) == [("new", "accepted"), ("unchecked", "hint_only")]
    block = body["identity"]
    assert (block["status"], block["reason"], block["budget_exhausted"]) == (
        "degraded", "the duplicate check left some rows unanswered", False)
    assert sorted(r["status"] for r in _identity_rows(cid)) == ["error", "ok"]


def test_the_budget_running_out_between_chunks_leaves_the_rest_unchecked(
        client, scene, monkeypatch):
    """The budget is checked per chunk: a chunk it refuses was never sent, so
    its rows are unchecked and the phase says the clock is why."""
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _one_per_chunk(monkeypatch)
    client.put("/api/config", json={"absorb_budget": "60"})
    clock = [0.0]
    monkeypatch.setattr(routes.scenes, "_clock", lambda: clock[0])
    parse = decisions.parse

    def parse_then_spend(*args, **kwargs):
        clock[0] = 1e6                      # the first chunk answered; the clock ran out
        return parse(*args, **kwargs)

    monkeypatch.setattr(decisions, "parse", parse_then_spend)
    fake = _llm(client, _two_rows(), decision_reply(_row("new")))

    body = _absorb(client, cid, sid)

    assert len(identity_requests(fake)) == 1
    assert _checks(body) == [("new", "accepted"), ("unchecked", "hint_only")]
    block = body["identity"]
    assert (block["status"], block["reason"], block["budget_exhausted"], block["attempted"]) \
        == ("degraded", "the duplicate check left some rows unanswered", True, True)
    assert [r["status"] for r in _identity_rows(cid)] == ["ok"]


def test_a_reply_in_todays_format_answers_no_row(client, scene):
    """The legacy one-call reply holds an object but no item: every row is
    unchecked (never `uncertain`), and the absorb lands."""
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _llm(client, _two_rows(), json.dumps({"decisions": [
        {"row": "r1", "decision": "existing", "id": "find-the-ledger", "reason": "same"},
        {"row": "r2", "decision": "new", "id": "", "reason": "another"}]}))

    body = _absorb(client, cid, sid)

    assert (body["identity"]["status"], body["identity"]["reason"]) == (
        "degraded", "the duplicate check answered none of the rows")
    assert _checks(body) == [("unchecked", "hint_only")] * 2
    assert body["one_line"] == "o"


#: A native endpoint's answer for the one examined row: new business, with
#: no rationale (a native backend never has one).
NATIVE_NEW = decisions.ItemResult({"decision": decisions.Answer("new")})

#: What a native endpoint answers for a model it has no decisions for.
NO_ENDPOINT = llm_errors.LLMError("bad_response", "no decisions endpoint", status=404)


def _native(fake, *answers):
    """`fake` with its native endpoint scripted by `answers`."""
    fake.decisions = list(answers)
    return fake


@pytest.mark.parametrize("on", [inference_fixtures.SPARE, inference_fixtures.SAME_PROVIDER],
                         ids=["spare", "same-provider"])
def test_identity_on_a_decide_only_model_answers_natively(client, scene, on):
    """Slice H: a Decision model that cannot generate is answered by its
    provider's native decisions endpoint. The check is asked there, the role
    fallback is not asked, and no identity prompt is completed on any model."""
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    inference_fixtures.decide_only(client, fallback=True, on=on)
    fake = _native(_llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
                        decision_reply(_row("new"))), NATIVE_NEW)

    body = _absorb(client, cid, sid)

    assert body["identity"]["status"] == "ok"
    assert _checks(body) == [("new", "accepted")]
    [(_item, conn, _retries)] = fake.native_requests
    assert (conn.provider_id, conn.model) == ("openrouter", "vendor/decider")
    assert identity_requests(fake) == []
    assert [(r["operation"], r["decision_mode"]) for r in _identity_rows(cid)] == [
        ("decide", "native")]


@pytest.mark.parametrize("on", [inference_fixtures.SPARE, inference_fixtures.SAME_PROVIDER],
                         ids=["spare", "same-provider"])
def test_identity_on_a_decide_only_model_answers_on_the_fallback(client, scene, on):
    """When the native endpoint fails, the role fallback that generates
    answers the check, on another provider or the decide-only model's own (a
    stage of its own rather than a retry)."""
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    inference_fixtures.decide_only(client, fallback=True, on=on)
    fake = _native(_llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
                        decision_reply(_row("new"))), NO_ENDPOINT)

    body = _absorb(client, cid, sid)

    assert body["identity"]["status"] == "ok"
    assert len(fake.native_requests) == 1
    [sent] = identity_requests(fake)
    assert (sent["target"].provider_id, sent["target"].model) == on
    assert all(r["target"].model != "vendor/decider" for r in fake.requests)


def test_a_native_identity_answer_maps_with_an_empty_reason(client, scene):
    """G's review I4: a native `existing` on an offered id is accepted on its
    own, with `reason: ""` -- the rationale is display text, and a native
    backend has none to give."""
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    inference_fixtures.decide_only(client, fallback=False)
    _native(_llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
                 decision_reply(_row("new"))),
            decisions.ItemResult({"decision": decisions.Answer("existing:find-the-ledger")}))

    body = _absorb(client, cid, sid)

    [edit] = _plot_edits(body)
    assert edit["target"] == {"kind": "plot", "id": "find-the-ledger"}
    ic = edit["identity_check"]
    assert (ic["decision"], ic["status"], ic["reason"]) == ("existing", "accepted", "")
    assert body["identity"]["status"] == "ok"


@pytest.mark.parametrize("answer", [
    decisions.Answer(None, "refused"), decisions.Answer(None, "abstained")],
    ids=["refused", "abstained"])
def test_a_native_answer_that_is_no_reading_leaves_the_row_unchecked(client, scene, answer):
    """The plan's coordination obligation (brutal review H, 2-P3): a native
    `refused` (or a tie, `abstained`) is an ANSWER, so it never moves to the
    fallback -- but it is no reading of the row (`decisions.was_read`), so
    the row is `unchecked` with its hint, never `uncertain`, and the phase
    says the check answered none of the rows."""
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    inference_fixtures.decide_only(client, fallback=True)
    fake = _native(_llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
                        decision_reply(_row("new"))),
                   decisions.ItemResult({"decision": answer}))

    body = _absorb(client, cid, sid)

    assert len(fake.native_requests) == 1 and identity_requests(fake) == []
    assert _checks(body) == [("unchecked", "hint_only")]
    assert body["identity"]["status"] == "degraded"


@pytest.mark.parametrize("native", ["refused", "cut"])
def test_a_budget_stopped_native_stage_is_the_budget_through_its_fallback(
        client, scene, monkeypatch, native):
    """Task 4's parked review item, end to end, on a native-only Decision
    model: the absorb clock stops the native stage -- refusing it unsent
    (`BudgetRefused`), or sending it and cutting it off in flight -- and the
    fallback stage is never sent. Refused, the chain ends there and the
    refusal is raised as itself (brutal review H 🟡2); cut off, the spent
    clock refuses the fallback stage unsent, the two compose into one error
    whose sentence is not the clock's, and `_budget_overrun` still reads the
    clock in its `words`. Either way the phase reports the budget, never a
    provider failure, worded by whether a call went out."""
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    inference_fixtures.decide_only(client, fallback=True)
    assert client.put("/api/config", json={"absorb_budget": "60"}).status_code == 200
    clock = [0.0]
    monkeypatch.setattr(routes.scenes, "_clock", lambda: clock[0])
    real_explain = identity.explain

    def explain_then_tick():
        # Just before decide: the budget gone, or a sliver of it left.
        clock[0] = 1e6 if native == "refused" else 59.95
        return real_explain()

    monkeypatch.setattr(identity, "explain", explain_then_tick)
    fake = _native(_llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
                        decision_reply(_row("new"))), NATIVE_NEW)
    asked: list[str] = []
    real_native = fake.decide_native

    def stalls(item, conn, *args, **kwargs):
        asked.append(conn.model)          # the call is built; the clock decides if it goes

        async def sent():
            result = await real_native(item, conn, *args, **kwargs)
            clock[0] = 1e6                   # out, and the budget runs out while it waits
            await asyncio.sleep(30)
            return result

        return sent()

    fake.decide_native = stalls

    body = _absorb(client, cid, sid)

    assert asked == ["vendor/decider"]
    assert len(fake.native_requests) == (1 if native == "cut" else 0)
    assert identity_requests(fake) == []      # the fallback was refused unsent
    block = body["identity"]
    assert (block["status"], block["budget_exhausted"], block["attempted"]) == (
        "failed", True, native == "cut")
    assert block["reason"] == (routes.scenes._IDENTITY_CUT_SHORT if native == "cut"
                               else routes.scenes._IDENTITY_REFUSED)
    assert _checks(body) == [("unchecked", "hint_only")]
    # A refused call files no row; a cut-off one is its meter's `error/timeout`.
    assert [(r["decision_mode"], r["status"], r.get("error"))
            for r in _identity_rows(cid)] == (
        [("native", "error", "timeout")] if native == "cut" else [])


def test_identity_on_a_decide_only_model_without_a_fallback_answers_natively(client, scene):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    inference_fixtures.decide_only(client, fallback=False)
    fake = _native(_llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
                        decision_reply(_row("new"))), NATIVE_NEW)

    body = _absorb(client, cid, sid)

    assert body["identity"]["status"] == "ok"
    assert len(fake.native_requests) == 1 and identity_requests(fake) == []


def test_identity_on_a_model_that_neither_generates_nor_decides_fails_the_phase(
        client, scene):
    """On a model that can neither generate nor decide natively the seam
    refuses (`incapable`), and the phase reports that refusal as its own
    failure: the review still lands, the rows stage with their hints, and
    nothing is sent for the check."""
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    inference_fixtures.neither(client)
    fake = _native(_llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
                        decision_reply(_row("new"))), NATIVE_NEW)

    r = review_runs.absorb(client, cid, sid)

    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["one_line"] == "o"
    block = body["identity"]
    assert (block["status"], block["attempted"]) == ("failed", False)
    assert block["reason"].startswith("The Continuity checks route runs on the Decision "
                                      "role (vendor/neither on OpenRouter), which cannot "
                                      "generate text or make native decisions"), block["reason"]
    assert identity_requests(fake) == [] and _identity_rows(cid) == []
    assert fake.native_requests == []
    assert all(q["target"].model != "vendor/neither" for q in fake.requests)
    [edit] = _plot_edits(body)
    assert edit["identity_check"]["status"] == "hint_only"


def test_the_decision_role_now_serves_the_identity_check(client, scene):
    """The continuity route's default flipped from Fast to Decision: a Decision
    role set on its own is what the check runs on, and the extraction stays
    where it was."""
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    inference_fixtures.format2(client)
    inference_fixtures.put_settings(client, {"roles": {"decision": {
        "selection": {"provider": "spare", "model": "vendor/spare"}}}})
    fake = _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
                decision_reply(_row("new")))

    assert _absorb(client, cid, sid)["identity"]["status"] == "ok"

    [sent] = identity_requests(fake)
    assert (sent["target"].provider_id, sent["target"].model) == ("spare", "vendor/spare")
    assert {r["target"].provider_id for r in fake.requests if r is not sent} == {"openrouter"}


def test_identity_rows_file_decide_and_structured(client, scene, monkeypatch):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _one_per_chunk(monkeypatch)
    _llm(client, _two_rows(), decision_reply(_row("new")))

    _absorb(client, cid, sid)

    rows = _identity_rows(cid)
    assert len(rows) == 2
    assert {(r["operation"], r["decision_mode"]) for r in rows} == {("decide", "structured")}


def test_a_refused_schema_still_decides_the_rows(client, scene):
    """A provider refusing the structured field, with no attempt to fall to, is
    sent the same attempt once more without the mode (slice F): the schema is
    in the prompt, so the row is still decided."""
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    inference_fixtures.format2(client)
    inference_fixtures.put_settings(client, {"roles": {"decision": {
        "selection": {"provider": "openrouter", "model": "vendor/active"},
        "fallback": {"provider": ""}}}})
    rev = store.llm_connections.read_connection_raw("openrouter")["rev"]
    store.llm_connections.set_cached_models(
        "openrouter", [{"id": "vendor/active",
                        "params": ["temperature", "structured_outputs"]}], rev)
    refused = llm_errors.LLMError(
        "bad_response", "response_format: json_schema strict mode is not supported", status=400)
    provider = SequencedProvider([[EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER], refused,
                                  [decision_reply(_row("new"), rationales=("a second search",))]])
    _install(client, LLMClient(openrouter=provider, timeout=0, retries=0))

    body = _absorb(client, cid, sid)

    assert body["identity"]["status"] == "ok"
    assert _checks(body) == [("new", "accepted")]
    _extraction_call, first, second = provider.requests
    assert "schema" in first["kwargs"] and "schema" not in second["kwargs"]
    assert first["messages"] == second["messages"]
    assert [r["status"] for r in _identity_rows(cid)] == ["error", "ok"]


def test_a_garbled_chunk_beside_an_answered_one_leaves_its_rows_unchecked(
        client, scene, monkeypatch):
    """I1: a chunk whose reply held no object never reached its rows, so they
    are unchecked with their hints -- never `uncertain` -- and the phase is
    degraded, never `ok`."""
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _one_per_chunk(monkeypatch)
    _install(client, from_entries([
        {"when": WHEN_EXTRACTION, "reply": _two_rows()},
        {"when": {**WHEN_IDENTITY, "user_contains": ROW_0}, "reply": "no json"},
        {"when": WHEN_IDENTITY, "reply": decision_reply(_row("new"))}]))

    body = _absorb(client, cid, sid)

    first, second = _plot_edits(body)
    assert (first["identity_check"]["decision"], first["identity_check"]["status"]) == (
        "unchecked", "hint_only")
    assert first["identity_check"]["candidates"]
    assert (second["identity_check"]["decision"], second["identity_check"]["status"]) == (
        "new", "accepted")
    assert (body["identity"]["status"], body["identity"]["reason"]) == (
        "degraded", "the duplicate check left some rows unanswered")


@pytest.mark.parametrize("order", ["garbled-then-error", "error-then-garbled"])
def test_an_errored_chunk_beside_a_garbled_one_reports_the_error(
        client, scene, monkeypatch, order):
    """M12: with nothing read, a chunk's provider error is what the phase
    reports -- by its kind, which is all a chunk's failure leaves (the ledger
    row's `error`) -- never `UNREADABLE`, whichever chunk failed first."""
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _one_per_chunk(monkeypatch)
    if order == "garbled-then-error":
        # The extraction, then the first chunk garbled, then the second raises.
        _install(client, FakeLLM([[_two_rows()], ["no json"]],
                                 error=llm_errors.LLMError("network", "connection reset"),
                                 fail_after=2))
    else:
        _install(client, from_entries([
            {"when": WHEN_EXTRACTION, "reply": _two_rows()},
            {"when": {"user_contains": ROW_0},
             "error": {"kind": "network", "message": "connection reset"}},
            {"when": {}, "reply": "no json"}]))

    body = _absorb(client, cid, sid)

    block = body["identity"]
    assert (block["status"], block["reason"]) == ("failed", "duplicate check failed: network")
    assert _checks(body) == [("unchecked", "hint_only")] * 2
    assert sorted(r["status"] for r in _identity_rows(cid)) == ["error", "ok"]


def test_a_chunk_error_is_reported_over_a_retried_schema_refusal(client, scene, monkeypatch):
    """M1: the error a failed chunk reports is that chunk's own. A later chunk
    whose provider refused the structured field, and whose retry without it
    answered (garbled), files an `error` row too -- but it answered, so its
    refusal is not the failure the phase names."""
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _one_per_chunk(monkeypatch)
    inference_fixtures.format2(client)
    inference_fixtures.put_settings(client, {"roles": {"decision": {
        "selection": {"provider": "openrouter", "model": "vendor/active"},
        "fallback": {"provider": ""}}}})
    rev = store.llm_connections.read_connection_raw("openrouter")["rev"]
    store.llm_connections.set_cached_models(
        "openrouter", [{"id": "vendor/active",
                        "params": ["temperature", "structured_outputs"]}], rev)
    refused = llm_errors.LLMError(
        "bad_response", "response_format: json_schema strict mode is not supported", status=400)
    provider = SequencedProvider([[_two_rows()],
                                  llm_errors.LLMError("network", "connection reset"),
                                  refused, ["no json"]])
    _install(client, LLMClient(openrouter=provider, timeout=0, retries=0))

    body = _absorb(client, cid, sid)

    block = body["identity"]
    assert (block["status"], block["reason"]) == ("failed", "duplicate check failed: network")
    assert _checks(body) == [("unchecked", "hint_only")] * 2
    assert [(r["status"], r.get("error")) for r in _identity_rows(cid)] == [
        ("error", "network"), ("error", "bad_response"), ("ok", None)]


class _SpendsOnTheSecondRefusal(SequencedProvider):
    """Runs the absorb clock out as the fallback's structured call is refused,
    so the moded chain went out and each prompt-only re-send is refused unsent."""

    def __init__(self, script, clock):
        super().__init__(script)
        self.clock = clock

    async def stream(self, messages, model="", *args, **kwargs):
        if len(self.requests) == 2:     # the extraction, the primary's refusal
            self.clock[0] = 1e6
        async for chunk in super().stream(messages, model, *args, **kwargs):
            yield chunk


def test_both_routes_refusing_and_the_clock_stopping_the_re_sends_is_the_clock(
        client, scene, monkeypatch):
    """Review 2 #1: both Decision routes refuse the structured field and the
    absorb clock stops the prompt-only re-sends. Their failures compose into a
    sentence that is no longer the clock's sentinel; the phase still reports
    the clock, worded as a check that ran partly -- not a provider failure
    whose reason names the budget twice."""
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    inference_fixtures.format2(client)
    inference_fixtures.put_settings(client, {"roles": {"decision": {
        "selection": {"provider": "openrouter", "model": "vendor/active"},
        "fallback": {"provider": "spare", "model": "vendor/spare"}}}})
    for conn_id, model in (("openrouter", "vendor/active"), ("spare", "vendor/spare")):
        rev = store.llm_connections.read_connection_raw(conn_id)["rev"]
        store.llm_connections.set_cached_models(
            conn_id, [{"id": model, "params": ["temperature", "structured_outputs"]}], rev)
    client.put("/api/config", json={"absorb_budget": "60"})
    clock = [0.0]
    monkeypatch.setattr(routes.scenes, "_clock", lambda: clock[0])
    refused = llm_errors.LLMError(
        "bad_response", "response_format: json_schema strict mode is not supported", status=400)
    provider = _SpendsOnTheSecondRefusal(
        [[EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER], refused, refused,
         [decision_reply(_row("new"))]], clock)
    _install(client, LLMClient(openrouter=provider, timeout=0, retries=0))

    body = _absorb(client, cid, sid)

    _extraction_call, *identity_calls = provider.requests
    assert [(r["model"], "schema" in r["kwargs"]) for r in identity_calls] == [
        ("vendor/active", True), ("vendor/spare", True)]
    block = body["identity"]
    assert (block["status"], block["budget_exhausted"], block["attempted"]) == (
        "failed", True, True)
    assert block["reason"] == routes.scenes._IDENTITY_CUT_SHORT
    assert _checks(body) == [("unchecked", "hint_only")]


def test_a_budget_spent_after_a_garbled_chunk_says_the_check_ran_partly(
        client, scene, monkeypatch):
    """M3: a chunk was sent and came back garbled, then the clock refused the
    next one. Nothing was read, so the phase fails on the clock -- but the
    check DID run, so its reason must not say it never could."""
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _one_per_chunk(monkeypatch)
    client.put("/api/config", json={"absorb_budget": "60"})
    clock = [0.0]
    monkeypatch.setattr(routes.scenes, "_clock", lambda: clock[0])
    parse = decisions.parse

    def parse_then_spend(*args, **kwargs):
        clock[0] = 1e6                      # the first chunk came back; the clock ran out
        return parse(*args, **kwargs)

    monkeypatch.setattr(decisions, "parse", parse_then_spend)
    fake = _llm(client, _two_rows(), "no json")

    body = _absorb(client, cid, sid)

    assert len(identity_requests(fake)) == 1
    assert _checks(body) == [("unchecked", "hint_only")] * 2
    block = body["identity"]
    assert (block["status"], block["budget_exhausted"], block["attempted"]) == (
        "failed", True, True)
    assert block["reason"] == routes.scenes._IDENTITY_CUT_SHORT
    assert "could run" not in block["reason"]
