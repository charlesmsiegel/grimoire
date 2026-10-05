"""`store/continuity/identity.py` -- proposed-new detection and neighbours.

Before absorb treats a plot thread or commitment as new, `identity.examine`
asks the same id assignment `materialize` uses which rows would open a record,
and which stored same-type records each of those is plausibly the same as
(capstone spec §10.2). Every candidate / non-candidate test first asserts the
signal that is supposed to decide it, so a later floor change fails at that
assertion rather than silently somewhere else. Stored records are seeded in a
scene of their own (`s0`), never the one being absorbed.
"""

import importlib
import json
import math
import time

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import embeddings
from grimoire.main import create_app
from grimoire.store import absorb, config, embed_space, llm_connections, vectors
from grimoire.store.absorb import materializer
from grimoire.store.campaigns import paths as campaigns_paths
from grimoire.store.continuity import identity, review, similarity
from tests.llm_fakes import FakeEmbeddings
from tests.review_runs import LEDGER_THREAD, RECOVER_THE_LEDGER, SALTMARCH_TITHE

NO_FACTS = {"cast": [], "location": "", "date": ""}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def cid(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    return client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]


@pytest.fixture
def s0(cid):
    return store.scenes.create_scene(cid, "Saltmarch docks")


@pytest.fixture
def sid(cid, s0):
    return store.scenes.create_scene(cid, "The harbour office")


@pytest.fixture
def fake(monkeypatch):
    double = FakeEmbeddings()
    monkeypatch.setattr(similarity, "_CLIENT", double)
    return double


def _seed_ledger(cid, scene, status="open"):
    pid, title, beat = LEDGER_THREAD
    store.plot.set_movement(cid, pid, title, status, beat, scene)


def _examine(cid, sid, plot=(), owed=(), deadline=None):
    parsed = {"plot_movements": [dict(r) for r in plot],
              "commitment_movements": [dict(r) for r in owed]}
    return identity.examine(cid, sid, parsed, NO_FACTS, embed_deadline=deadline)


def _proposed(kind, row, sid):
    """The subject `examine` builds for a proposed row, for asserting signals."""
    return similarity.subject(kind, "proposed:test",
                              {"title": row.get("title", ""),
                               "beats": [{"text": row["beat"], "scene": sid}],
                               "kind": row.get("kind", ""), "due": row.get("due", "")},
                              scenes={sid})


def _pooled(cid, ref):
    kind = ref.split(":", 1)[0]
    return {s.ref: s for s in similarity.pool(cid, kind)}[ref]


def _refs(examined):
    return [s.ref for s, _ in examined.candidates]


def _configure():
    conn = llm_connections.create_connection("openai_compatible", "Vectors",
                                             base_url="https://vectors.example/v1",
                                             api_key="sk-x", model="", post_process="none")
    config.write_config(embeddings_model="embed-1", embeddings_connection_id=conn)


def _soon():
    return time.monotonic() + embeddings.TIMEOUT


# ------------------------------------------------------------------ basics


def test_no_rows_no_proposals():
    assert not identity.has_proposals({"plot_movements": [], "commitment_movements": []})
    assert not identity.has_proposals({})
    assert identity.has_proposals({"plot_movements": [], "commitment_movements": [{}]})


def test_idless_row_with_no_close_record_is_proposed_but_not_examined(cid, s0, sid):
    _seed_ledger(cid, s0)
    sig = similarity.lexical(_proposed("thread", SALTMARCH_TITHE, sid),
                             _pooled(cid, "thread:find-the-ledger"))
    assert sig["tokens"] < similarity.WEAK_TOKEN and sig["chars"] < similarity.WEAK_CHAR
    assert sig["scenes"] == [] and sig["actors"] == []
    exam = _examine(cid, sid, plot=[SALTMARCH_TITHE])
    assert exam.proposed == 1
    assert exam.rows == []
    assert exam.targets == set()
    assert exam.matching == "basic" and exam.embedding == "off"


def test_reworded_duplicate_is_examined_with_the_record_as_candidate(cid, s0, sid):
    _seed_ledger(cid, s0)
    sig = similarity.lexical(_proposed("thread", RECOVER_THE_LEDGER, sid),
                             _pooled(cid, "thread:find-the-ledger"))
    assert sig["tokens"] >= similarity.TOKEN_FLOOR and sig["chars"] >= similarity.CHAR_FLOOR
    exam = _examine(cid, sid, plot=[RECOVER_THE_LEDGER])
    assert exam.proposed == 1
    [row] = exam.rows
    assert (row.section, row.index, row.key, row.kind) == ("plot_movements", 0, "r1", "thread")
    assert row.title == "Recover the harbour ledger"
    assert row.row == RECOVER_THE_LEDGER
    assert row.assigned == "recover-the-harbour-ledger"
    first, signals = row.candidates[0]
    assert first.ref == "thread:find-the-ledger"
    assert signals["via"] == "lexical"
    assert (row.decision, row.status, row.reason, row.target) == ("unchecked", "hint_only", "", None)


def test_exact_title_open_record_is_a_target_not_a_proposal(cid, s0, sid):
    _seed_ledger(cid, s0)
    row = {"title": "Find the ledger", "beat": "Winifred found a page of it.", "status": "open"}
    exam = _examine(cid, sid, plot=[row])
    assert exam.proposed == 0
    assert exam.rows == []
    assert ("thread", "find-the-ledger") in exam.targets


def test_exact_title_closed_record_is_a_proposal_with_the_closed_neighbour(cid, s0, sid):
    _seed_ledger(cid, s0, status="closed")
    row = {"title": "Find the ledger", "beat": "Winifred went looking again.", "status": "open"}
    sig = similarity.lexical(_proposed("thread", row, sid), _pooled(cid, "thread:find-the-ledger"))
    assert sig["title_equal"]
    exam = _examine(cid, sid, plot=[row])
    assert exam.proposed == 1
    assert exam.targets == set()
    [examined] = exam.rows
    assert examined.assigned == "find-the-ledger-2"
    [(cand, _)] = examined.candidates
    assert cand.ref == "thread:find-the-ledger"
    assert cand.live is False


def test_exact_title_on_a_suffixed_record_is_a_target(cid, s0, sid):
    store.plot.set_movement(cid, "untitled", "海図", "open", "Mara drew it.", s0)
    store.plot.set_movement(cid, "untitled-2", "灯台", "open", "Winifred lit it.", s0)
    exam = _examine(cid, sid, plot=[{"title": "灯台", "beat": "It went dark.", "status": "open"}])
    assert exam.proposed == 0
    assert ("thread", "untitled-2") in exam.targets


def test_batch_reservation_matches_materialize(cid, s0, sid):
    # `the-map-2` is an explicit id naming nothing stored, so it is reserved
    # under its own title; the id-less "The map" slugs to `the-map`, which is
    # closed, then to `the-map-2`, which this batch holds under another title.
    store.plot.set_movement(cid, "the-map", "The map", "closed", "Mara burned it.", s0)
    explicit = {"id": "the-map-2", "title": "Mara's chart", "beat": "Mara drew a chart.",
                "status": "open"}
    idless = {"title": "The map", "beat": "Seraphine found another map.", "status": "open"}
    sig = similarity.lexical(_proposed("thread", idless, sid), _pooled(cid, "thread:the-map"))
    assert sig["title_equal"]
    exam = _examine(cid, sid, plot=[explicit, idless])
    assert exam.proposed == 2
    [second] = [r for r in exam.rows if r.index == 1]
    staged = absorb.materialize(cid, sid, {"plot_movements": [explicit, idless]})
    assert [e["target"]["id"] for e in staged] == ["the-map-2", second.assigned]
    assert second.assigned == "the-map-3"


def test_a_row_materialize_drops_is_ignored(cid, s0, sid):
    # An explicit id on a stored record reserves its STORED title, so a second
    # id-less row with that title is a second move of the same thread, which
    # materialize drops -- it is neither proposed nor a second target.
    store.plot.set_movement(cid, "the-map", "The map", "open", "Mara drew it.", s0)
    explicit = {"id": "the-map", "title": "Renamed map", "beat": "Mara inked it.",
                "status": "open"}
    idless = {"title": "The map", "beat": "Seraphine found it.", "status": "open"}
    assigned = materializer.assign_ids(store.plot.read(cid), {},
                                       {"plot_movements": [explicit, idless]})
    assert assigned[("plot_movements", 1)] is None
    exam = _examine(cid, sid, plot=[explicit, idless])
    assert exam.proposed == 0
    assert exam.targets == {("thread", "the-map")}


def test_row_with_unknown_id_is_proposed_and_titled_by_title_or_id(cid, s0, sid):
    store.commitments.set_movement(cid, "the-debt", "Mara's debt", "debt", "open", None,
                                   "Mara owes the guild.", s0)
    row = {"id": "maras-debt", "title": "", "beat": "Mara owes the guild again.",
           "kind": "debt", "status": "open"}
    sig = similarity.lexical(_proposed("commitment", row, sid), _pooled(cid, "commitment:the-debt"))
    assert sig["tokens"] >= similarity.TOKEN_FLOOR
    exam = _examine(cid, sid, owed=[row])
    assert exam.proposed == 1
    [examined] = exam.rows
    assert examined.title == "maras-debt"
    assert examined.kind == "commitment"
    assert examined.section == "commitment_movements"


# ------------------------------------------------------------------ aliases


def _seed_alias(cid, scene):
    store.plot.set_movement(cid, "a", "Mara's map", "open", "Mara lost the map.", scene)
    store.plot.set_movement(cid, "b", "Mara's stolen map", "open", "The map was stolen.", scene)
    review.create_alias(cid, "thread:a", "thread:b")


def test_explicit_alias_source_id_targets_the_canonical(cid, s0, sid):
    _seed_alias(cid, s0)
    exam = _examine(cid, sid, plot=[{"id": "a", "title": "", "beat": "Mara found a clue.",
                                     "status": "open"}])
    assert exam.proposed == 0
    assert exam.targets == {("thread", "b")}
    assert exam.live == {"thread:a": "thread:b"}


def test_candidates_never_include_a_hidden_alias_source(cid, s0, sid):
    _seed_alias(cid, s0)
    row = {"title": "Mara's map", "beat": "Mara hunted for her map.", "status": "open"}
    # The source would be the best candidate of all, were it shown.
    assert similarity.titles_equal(row["title"], "Mara's map")
    exam = _examine(cid, sid, plot=[row])
    [examined] = exam.rows
    assert "thread:a" not in _refs(examined)
    assert "thread:b" in _refs(examined)


def test_distinguished_from_drops_unknown_and_canonicalizes(cid, s0, sid):
    _seed_alias(cid, s0)
    row = {"title": "Mara's map", "beat": "Mara hunted for her map.", "status": "open",
           "distinguished_from": ["nope", "a", "b"]}
    exam = _examine(cid, sid, plot=[row])
    [examined] = exam.rows
    assert examined.distinguished_from == ["b"]


def test_distinguished_from_keeps_only_the_rows_own_type(cid, s0, sid):
    _seed_ledger(cid, s0)
    store.commitments.set_movement(cid, "the-debt", "The debt", "debt", "open", None,
                                   "Mara owes the guild.", s0)
    row = {**RECOVER_THE_LEDGER, "distinguished_from": ["the-debt", "thread:find-the-ledger",
                                                        "find-the-ledger"]}
    [examined] = _examine(cid, sid, plot=[row]).rows
    assert examined.distinguished_from == ["find-the-ledger"]


# ------------------------------------------------------------------ type isolation


def test_cross_type_never_a_candidate(cid, s0, sid):
    _seed_ledger(cid, s0)
    store.commitments.set_movement(cid, "the-midnight-deadline", "The midnight deadline",
                                   "deadline", "open", "midnight",
                                   "Seraphine must pay by midnight.", s0)
    row = {"title": "Find the ledger", "beat": "Winifred swore to find it.",
           "kind": "promise", "status": "open"}
    thread_sig = similarity.lexical(similarity.subject("thread", "proposed:test", row),
                                    _pooled(cid, "thread:find-the-ledger"))
    assert thread_sig["title_equal"]   # a thread row would be a candidate
    exam = _examine(cid, sid, owed=[row])
    assert exam.proposed == 1
    assert exam.rows == []
    assert exam.targets == set()


def test_commitments_unreadable_examines_no_commitment_rows(cid, s0, sid):
    store.commitments.set_movement(cid, "the-debt", "The debt", "debt", "open", None,
                                   "Mara owes the guild.", s0)
    (campaigns_paths.campaign_root(cid) / "commitments.json").write_text("{ no", encoding="utf-8")
    row = {"title": "The debt", "beat": "Mara owes the guild more.", "kind": "debt",
           "status": "open"}
    exam = _examine(cid, sid, owed=[row])
    assert exam.proposed == 0
    assert exam.rows == []
    assert exam.targets == set()


# ------------------------------------------------------------------ embeddings

#: Shares no word with `LEDGER_THREAD` -- found only by meaning.
PARAPHRASE = {"title": "Track down the account book",
              "beat": "Someone wants the dockside books.", "status": "open"}


def _seed_paraphrase_fixture(cid, s0, sid, *, cached):
    _seed_ledger(cid, s0)
    record = _pooled(cid, "thread:find-the-ledger")
    proposed = _proposed("thread", PARAPHRASE, sid)
    sig = similarity.lexical(proposed, record)
    assert sig["tokens"] == 0 and sig["chars"] < similarity.WEAK_CHAR
    assert sig["scenes"] == [] and sig["actors"] == []
    if cached:
        vectors.save(embed_space.resolve()["space"], record.text, [1.0, 0.0])
    return proposed


def test_semantic_matching_finds_a_wordless_paraphrase(cid, s0, sid, monkeypatch):
    _configure()
    proposed = _seed_paraphrase_fixture(cid, s0, sid, cached=True)
    near = [0.9, math.sqrt(1 - 0.81)]
    double = FakeEmbeddings(vector_for=lambda t: near if t == proposed.text else [0.0, 1.0])
    monkeypatch.setattr(similarity, "_CLIENT", double)
    exam = _examine(cid, sid, plot=[PARAPHRASE], deadline=_soon())
    assert exam.matching == "semantic"
    assert exam.embedding == "configured" and exam.embedding_error == ""
    assert exam.embedded == 1
    [examined] = exam.rows
    [(cand, signals)] = examined.candidates
    assert cand.ref == "thread:find-the-ledger"
    assert signals["via"] == "semantic"
    assert signals["cosine"] == pytest.approx(0.9, abs=1e-3)


def test_wordless_paraphrase_is_not_a_candidate_without_embeddings(cid, s0, sid, fake):
    _seed_paraphrase_fixture(cid, s0, sid, cached=False)
    exam = _examine(cid, sid, plot=[PARAPHRASE], deadline=_soon())
    assert exam.proposed == 1
    assert exam.rows == []
    assert exam.embedding == "off" and exam.matching == "basic"
    assert fake.calls == []


def test_an_uncached_lexically_unrelated_neighbour_is_not_embedded(cid, s0, sid, fake):
    _configure()
    proposed = _seed_paraphrase_fixture(cid, s0, sid, cached=False)
    exam = _examine(cid, sid, plot=[PARAPHRASE], deadline=_soon())
    assert fake.calls == [[proposed.text]]
    assert exam.rows == []
    assert exam.embedding == "configured"


def test_a_lexically_near_uncached_neighbour_is_warmed(cid, s0, sid, fake):
    _configure()
    _seed_ledger(cid, s0)
    record = _pooled(cid, "thread:find-the-ledger")
    proposed = _proposed("thread", RECOVER_THE_LEDGER, sid)
    assert similarity.weak(similarity.lexical(proposed, record))
    _examine(cid, sid, plot=[RECOVER_THE_LEDGER], deadline=_soon())
    assert fake.calls == [[proposed.text, record.text]]


def test_embedding_failure_keeps_lexical_candidates(cid, s0, sid, monkeypatch):
    _configure()
    _seed_ledger(cid, s0)
    monkeypatch.setattr(similarity, "_CLIENT",
                        FakeEmbeddings(error=embeddings.EmbeddingsError("network", "down")))
    exam = _examine(cid, sid, plot=[RECOVER_THE_LEDGER], deadline=_soon())
    assert exam.embedding == "failure"
    assert exam.embedding_error == "network"
    assert exam.embedded == 0
    [examined] = exam.rows
    first, signals = examined.candidates[0]
    assert first.ref == "thread:find-the-ledger"
    assert signals["via"] == "lexical"


def test_proposed_subject_carries_the_scene_cast(cid, s0, sid):
    # A lexically weak pair is admitted through the structural clause only when
    # the record and the proposal share an actor; `facts["cast"]` is where the
    # proposal's actors come from.
    (campaigns_paths.campaign_root(cid) / "chronicle.json").write_text(json.dumps(
        {s0: {"id": s0, "one_line": "", "cast": ["characters/mara"]}}), encoding="utf-8")
    store.plot.set_movement(cid, "maras-map", "Mara's map", "open", "Mara drew a map.", s0)
    row = {"title": "Mara's errand", "beat": "Mara ran an errand.", "status": "open"}
    sig = similarity.lexical(_proposed("thread", row, sid), _pooled(cid, "thread:maras-map"))
    assert similarity.weak(sig) and similarity.admitted_by(sig) is None
    assert _examine(cid, sid, plot=[row]).rows == []
    parsed = {"plot_movements": [dict(row)], "commitment_movements": []}
    facts = {"cast": ["characters/mara"], "location": "", "date": ""}
    [examined] = identity.examine(cid, sid, parsed, facts, embed_deadline=None).rows
    [(cand, signals)] = examined.candidates
    assert cand.ref == "thread:maras-map"
    assert signals["actors"] == ["characters:mara"]
    assert signals["via"] == "structural"
