"""The identity phase inside absorb (continuity capstone spec §10.4, §28.3).

Absorb chains one identity step onto its extraction: the proposed-new plot
threads and commitments are examined against the stored same-type records, and
when any of them has a plausible neighbour ONE batched resolver call decides
which are the same business. These drive the real endpoint -- the fifth
`phases` row, the `identity` block, what is staged and what a save writes --
with `llm_fakes.from_entries` answering by which prompt is asking and
`llm_fakes.FakeEmbeddings` standing in for the embeddings provider.

Stored records are seeded in a scene of their own (`s0`, created before the
absorbed scene), never the one being absorbed.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import time

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import embeddings, llm_errors, routes
from grimoire.main import create_app
from grimoire.store import config, llm_connections
from grimoire.store.absorb import materializer
from grimoire.store.continuity import identity, similarity

from . import review_runs
from .llm_fakes import FakeEmbeddings, from_entries
from .review_runs import (
    EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
    LEDGER_THREAD,
    RECOVER_THE_LEDGER,
    SALTMARCH_TITHE,
    identity_requests,
)

WHEN_EXTRACTION = {"system_contains": "You are absorbing a completed role-play scene"}
WHEN_IDENTITY = {"system_contains": "You are checking whether newly proposed story records"}

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


def _decisions(*items):
    return json.dumps({"decisions": [dict(i) for i in items]})


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
    config.write_config(embeddings_model="embed-1", embeddings_connection_id=conn)
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
         _decisions({"row": "r1", "decision": "new", "reason": "a second search"}))

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
         _decisions({"row": "r1", "decision": "new", "reason": "a second search"}))

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
         _decisions({"row": "r1", "decision": "existing", "id": "find-the-ledger"}))

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
                _decisions({"row": "r1", "decision": "existing", "id": "find-the-ledger"}))

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
        clock[0] = 1e6
        raise embeddings.EmbeddingsError("network",
                                         "embeddings deadline passed before the request")

    _configure_embeddings(monkeypatch, FakeEmbeddings(vector_for=cut))
    fake = _llm(client, _extraction(plot=[SALTMARCH_TITHE]))

    body = _absorb(client, cid, sid)

    assert identity_requests(fake) == []
    block = body["identity"]
    assert (block["status"], block["budget_exhausted"], block["reason"]) == (
        "degraded", True, BUDGET_SEMANTIC)
    assert block["fallback"] == BUDGET_SEMANTIC
    assert _identity_errors(cid) == []


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
                _decisions({"row": "r1", "decision": "new"}))

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
         _decisions({"row": "r1", "decision": "existing", "id": "find-the-ledger",
                     "reason": "the same search"}))

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
                _decisions({"row": "r1", "decision": "new"}, {"row": "r2", "decision": "new"}))

    body = _absorb(client, cid, sid)

    [request] = identity_requests(fake)
    user = "\n".join(m["content"] for m in request["messages"] if m["role"] == "user")
    assert "Row r1" in user and "Row r2" in user
    assert body["identity"]["status"] == "ok"
    assert body["identity"]["counts"]["examined"] == 2


def test_resolver_naming_an_unknown_id_marks_the_row_uncertain_and_low(client, scene):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
         _decisions({"row": "r1", "decision": "existing", "id": "no-such-thread"}))

    body = _absorb(client, cid, sid)

    [edit] = _plot_edits(body)
    assert edit["target"]["id"] != "find-the-ledger"
    assert (edit["identity_check"]["decision"], edit["identity_check"]["status"]) == (
        "uncertain", "downgraded")
    assert edit["review"]["band"] == "low"


def test_resolver_naming_a_closed_thread_never_reopens_it(client, scene):
    cid, s0, sid = scene
    _seed_ledger(cid, s0, status="closed")
    _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
         _decisions({"row": "r1", "decision": "existing", "id": "find-the-ledger"}))

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
         _decisions({"row": "r1", "decision": "existing", "id": "find-the-ledger"},
                    {"row": "r2", "decision": "existing", "id": "find-the-ledger"}))

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
         _decisions({"row": "r1", "decision": "new", "reason": "a second search"}))

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
         '{"decisions": []}')

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
                _decisions({"row": "r1", "decision": "new", "reason": "a second search"}))

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
         _decisions({"row": "r1", "decision": "new", "reason": "a second search"}))

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
         _decisions({"row": "r1", "decision": "new"}))

    block = _absorb(client, cid, sid)["identity"]

    assert (block["status"], block["matching"], block["fallback"]) == ("ok", "semantic", "")


def test_identity_embedding_deadline_never_exceeds_the_absorb_budget(client, scene, monkeypatch):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    client.put("/api/config", json={"absorb_budget": "5"})
    double = _configure_embeddings(monkeypatch, FakeEmbeddings())
    fake = _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
                _decisions({"row": "r1", "decision": "new"}))

    _absorb(client, cid, sid)

    assert double.deadlines
    assert all(d is not None and d <= time.monotonic() + 5 for d in double.deadlines)
    # Embedding calls stay unmetered (§9.4): the scene's usage rows are exactly
    # the LLM requests, and none of them is the embeddings call.
    rows = [r for r in store.usage.calls(campaign=cid) if r.get("scene") == sid]
    assert len(rows) == len(fake.requests) == 2
    for row in rows:
        assert "embed" not in str(row.get("task"))
        assert row.get("model") != "embed-1"


def test_misrouted_identity_reports_itself_and_leaves_absorb_standing(client, scene):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    keyless = client.post("/api/llm-connections",
                          json={"kind": "openrouter", "name": "Keyless"}).json()["id"]
    client.put("/api/routing", json={"routes": {"continuity": keyless}})
    fake = _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
                _decisions({"row": "r1", "decision": "new"}))

    body = _absorb(client, cid, sid)

    assert identity_requests(fake) == []
    assert body["identity"]["status"] == "failed"
    assert "key" in (body["identity"]["reason"] or "").lower()
    [edit] = _plot_edits(body)
    assert edit["identity_check"]["status"] == "hint_only"


# ------------------------------------------------------------- saving


def test_alternatives_pass_check_conflicts_at_save(client, scene):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
         _decisions({"row": "r1", "decision": "new", "reason": "a second search"}))
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


def test_a_save_body_still_carrying_alternatives_is_accepted(client, scene):
    cid, s0, sid = scene
    _seed_ledger(cid, s0)
    _llm(client, EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER,
         _decisions({"row": "r1", "decision": "new", "reason": "a second search"}))
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
         _decisions({"row": "r1", "decision": "new", "reason": "a second search"}))

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
    keyless = client.post("/api/llm-connections",
                          json={"kind": "openrouter", "name": "Keyless"}).json()["id"]
    client.put("/api/routing", json={"routes": {"continuity": keyless}})


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
                _decisions({"row": "r1", "decision": "new", "reason": "a second search"}))

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
         _decisions({"row": "r1", "decision": "new"}))

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
         _decisions({"row": "r1", "decision": "new"}))
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
