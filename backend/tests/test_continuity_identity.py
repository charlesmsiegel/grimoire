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
    """The subject `examine` builds for a proposed row, for asserting signals --
    titled, like the record materialize would open, by its title or else its id."""
    return similarity.subject(kind, "proposed:test",
                              {"title": row.get("title", "") or row.get("id", ""),
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


def _untitled(kind, row, sid):
    """`_proposed` without the id fallback: the subject the id-less title line
    would give, to show the id is what decides the test."""
    return _proposed(kind, {**row, "id": ""}, sid)


def test_unknown_id_without_a_title_is_matched_under_its_id(cid, s0, sid):
    # §10.2: "its title, or else its id, becomes the title text". Only the id
    # carries the overlap here; the beat alone is below the lexical floors.
    _seed_ledger(cid, s0)
    row = {"id": "the-harbour-ledger", "title": "", "beat": "Winifred kept searching.",
           "status": "open"}
    stored = _pooled(cid, "thread:find-the-ledger")
    assert similarity.admitted_by(similarity.lexical(_untitled("thread", row, sid), stored)) is None
    sig = similarity.lexical(_proposed("thread", row, sid), stored)
    assert sig["tokens"] >= similarity.TOKEN_FLOOR and sig["chars"] >= similarity.CHAR_FLOOR
    exam = _examine(cid, sid, plot=[row])
    assert exam.proposed == 1
    [examined] = exam.rows
    assert examined.assigned == "the-harbour-ledger"
    assert examined.title == "the-harbour-ledger"
    assert _refs(examined) == ["thread:find-the-ledger"]


def test_an_id_spelling_a_stored_title_is_examined(cid, s0, sid):
    # The record materialize would open is titled "the-lost-map", which is the
    # stored title slugged -- an exact signal, though the beats share nothing.
    store.plot.set_movement(cid, "old-map", "The lost map", "open",
                            "Mara lost the map.", s0)
    row = {"id": "the-lost-map", "title": "", "beat": "Seraphine sailed for Saltmarch.",
           "status": "open"}
    stored = _pooled(cid, "thread:old-map")
    assert not similarity.lexical(_untitled("thread", row, sid), stored)["slug_equal"]
    sig = similarity.lexical(_proposed("thread", row, sid), stored)
    assert sig["slug_equal"]
    exam = _examine(cid, sid, plot=[row])
    [examined] = exam.rows
    assert examined.title == "the-lost-map"
    assert _refs(examined) == ["thread:old-map"]


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


def test_examine_treats_source_title_with_closed_canonical_as_proposed(cid, s0, sid):
    """A slug collision on an open alias source whose canonical is closed is not
    honoured (that would reopen the canonical unexamined): the row is proposed
    new, and the closed canonical is a candidate the resolver may see."""
    store.plot.set_movement(cid, "mara-s-map", "Mara's map", "open", "Mara lost the map.", s0)
    store.plot.set_movement(cid, "winifreds-chart", "Winifred's chart", "closed",
                            "Winifred copied the map.", s0)
    review.create_alias(cid, "thread:mara-s-map", "thread:winifreds-chart",
                        accept_status_change=True)
    row = {"title": "Mara's map", "beat": "Mara hunted for the lost map.", "status": "open"}
    sig = similarity.lexical(_proposed("thread", row, sid), _pooled(cid, "thread:winifreds-chart"))
    assert sig["tokens"] >= similarity.TOKEN_FLOOR
    exam = _examine(cid, sid, plot=[row])
    assert exam.proposed == 1
    [examined] = exam.rows
    assert examined.assigned == "mara-s-map-2"
    by_ref = {s.ref: s for s, _ in examined.candidates}
    assert "thread:winifreds-chart" in by_ref
    assert by_ref["thread:winifreds-chart"].live is False
    assert "thread:mara-s-map" not in by_ref


def test_slug_on_an_explicit_source_is_not_examined_with_closed_canonical(cid, s0, sid):
    """An explicit source row reserves the source's slug, so a later id-less row
    titled like the source is a second move of the canonical -- dropped, never a
    proposed-new record -- even when the canonical is closed."""
    store.plot.set_movement(cid, "mara-s-map", "Mara's map", "open", "Mara lost the map.", s0)
    store.plot.set_movement(cid, "winifreds-chart", "Winifred's chart", "closed",
                            "Winifred copied the map.", s0)
    review.create_alias(cid, "thread:mara-s-map", "thread:winifreds-chart",
                        accept_status_change=True)
    exam = _examine(cid, sid, plot=[
        {"id": "mara-s-map", "beat": "Mara found a clue.", "status": "advanced"},
        {"title": "Mara's map", "beat": "Mara hunted for the lost map.", "status": "open"}])
    assert exam.proposed == 0 and exam.rows == []


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


def test_no_same_type_record_means_no_provider_call(cid, s0, sid, fake):
    # An empty plot ledger: nothing a proposed thread could be compared with,
    # so embedding its text would send campaign prose for nothing.
    _configure()
    exam = _examine(cid, sid, plot=[RECOVER_THE_LEDGER], deadline=_soon())
    assert exam.proposed == 1
    assert exam.rows == []
    assert exam.embedding == "configured" and exam.embedded == 0
    assert fake.calls == []


def test_only_a_proposal_with_a_same_type_pool_is_embedded(cid, s0, sid, fake):
    # A stored commitment, no stored thread: only the commitment row is embedded.
    _configure()
    _seed_deadline(cid, s0)
    exam = _examine(cid, sid, plot=[RECOVER_THE_LEDGER], owed=[SERAPHINES_THREAT],
                    deadline=_soon())
    assert exam.proposed == 2
    owed = _proposed("commitment", SERAPHINES_THREAT, sid)
    record = _pooled(cid, "commitment:the-midnight-deadline")
    assert [call[0] for call in fake.calls] == [owed.text]
    assert _proposed("thread", RECOVER_THE_LEDGER, sid).text not in {
        t for call in fake.calls for t in call}
    assert {t for call in fake.calls for t in call} <= {owed.text, record.text}


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


# ------------------------------------------------------------ resolver parse


def test_parse_undecodable_is_none():
    assert identity.parse_output("I think so.") is None


def test_parse_decodable_empty_is_empty_list():
    assert identity.parse_output("{}") == []
    assert identity.parse_output('{"decisions": 3}') == []


def test_parse_accepts_row_labels_as_printed():
    for label in ("Row r1", " R1 ", "r1", "ROW R1"):
        [decision] = identity.parse_output(json.dumps(
            {"decisions": [{"row": label, "decision": "new"}]}))
        assert decision["row"] == "r1", label


def test_parse_normalizes_and_never_raises():
    reply = "Here you go:\n```json\n" + json.dumps({"decisions": [
        "not a row",
        {"decision": "new", "id": "", "reason": "no row key"},
        {"row": "   ", "decision": "new"},
        {"row": 3, "decision": "new"},
        {"row": "Row r1", "decision": "EXISTING", "id": " find-the-ledger ",
         "reason": " Same ledger. "},
        {"row": "r2", "decision": "maybe", "id": 7, "reason": "x" * 400},
        {"row": "r1", "decision": "new", "id": "", "reason": "a second answer for r1"},
        {"row": "r3", "decision": None, "reason": ["not", "text"]},
    ]}) + "\n```"
    assert identity.parse_output(reply) == [
        {"row": "r1", "decision": "existing", "id": "find-the-ledger", "reason": "Same ledger."},
        {"row": "r2", "decision": "uncertain", "id": "", "reason": "x" * identity.REASON_CHARS},
        {"row": "r3", "decision": "uncertain", "id": "", "reason": ""},
    ]
    assert len(identity.parse_output(reply)[1]["reason"]) == identity.REASON_CHARS
    assert identity.DECISIONS == ("existing", "new", "uncertain")
    assert identity.CHECK_DECISIONS == ("existing", "new", "uncertain", "unchecked")
    assert identity.STATUSES == ("accepted", "downgraded", "hint_only")


# ------------------------------------------------------------ resolver prompt

#: A proposed thread re-opening `LEDGER_THREAD` under its exact title, after the
#: stored one was closed -- so it is proposed, and titled the same.
REOPENED_LEDGER = {"title": "Find the ledger", "beat": "Winifred went looking again.",
                   "status": "open", "quote": "I want that ledger back.",
                   "speaker": "Winifred", "certainty": 0.8,
                   "why_new": "The old search ended; this is a new one.",
                   "distinguished_from": ["find-the-ledger"]}

#: A proposed commitment rewording a stored `the-midnight-deadline`.
SERAPHINES_DEADLINE = {"title": "Seraphine's midnight deadline",
                       "beat": "Seraphine must pay the midnight deadline.",
                       "kind": "threat", "status": "open", "due": "midnight"}


def _seed_prompt_fixture(cid, s0):
    pid, title, beat = LEDGER_THREAD
    store.plot.set_movement(cid, pid, title, "open", beat, s0)
    store.plot.set_movement(cid, pid, "", "advanced", "Winifred found the first page.", s0)
    store.plot.set_movement(cid, pid, "", "closed", "Winifred burned the ledger.", s0)
    store.commitments.set_movement(cid, "the-midnight-deadline", "The midnight deadline",
                                   "threat", "open", "midnight",
                                   "Seraphine must pay by midnight.", s0)


def test_prompt_rows_carry_the_row_and_its_neighbours(cid, s0, sid):
    _seed_prompt_fixture(cid, s0)
    sig = similarity.lexical(_proposed("commitment", SERAPHINES_DEADLINE, sid),
                             _pooled(cid, "commitment:the-midnight-deadline"))
    assert sig["tokens"] >= similarity.TOKEN_FLOOR
    exam = _examine(cid, sid, plot=[REOPENED_LEDGER], owed=[SERAPHINES_DEADLINE])
    thread, owed = exam.prompt_rows()
    assert {k: thread[k] for k in ("key", "kind", "title", "beat", "status", "commitment_kind",
                                   "due", "quote", "speaker", "certainty", "why_new",
                                   "distinguished_from")} == {
        "key": "r1", "kind": "thread", "title": "Find the ledger",
        "beat": "Winifred went looking again.", "status": "open", "commitment_kind": "",
        "due": "", "quote": "I want that ledger back.", "speaker": "Winifred",
        "certainty": 0.8, "why_new": "The old search ended; this is a new one.",
        "distinguished_from": ["find-the-ledger"]}
    [cand] = thread["candidates"]
    assert {k: cand[k] for k in ("id", "title", "status", "kind", "due", "latest_beat",
                                 "earlier")} == {
        "id": "find-the-ledger", "title": "Find the ledger", "status": "closed", "kind": "",
        "due": "", "latest_beat": "Winifred burned the ledger.",
        "earlier": ["Winifred found the first page.",
                    "Winifred learned the harbour ledger exists."]}
    assert cand["signals"]["title_equal"] is True
    assert (owed["key"], owed["kind"], owed["commitment_kind"], owed["due"],
            owed["certainty"], owed["why_new"], owed["distinguished_from"]) == (
        "r2", "commitment", "threat", "midnight", None, "", [])
    [ocand] = owed["candidates"]
    assert (ocand["id"], ocand["kind"], ocand["due"], ocand["earlier"]) == (
        "the-midnight-deadline", "threat", "midnight", [])


def test_build_prompt_shows_rows_candidates_and_signals(cid, s0, sid):
    _seed_prompt_fixture(cid, s0)
    exam = _examine(cid, sid, plot=[REOPENED_LEDGER], owed=[SERAPHINES_DEADLINE])
    system, user = identity.build_prompt(exam.prompt_rows())
    assert (system["role"], user["role"]) == ("system", "user")
    assert system["content"].startswith("You are checking whether newly proposed story records")
    for word in ('"existing"', '"new"', '"uncertain"'):
        assert word in system["content"]
    text = user["content"]
    assert "Row r1 — proposed plot thread: Find the ledger" in text
    assert "Row r2 — proposed commitment: Seraphine's midnight deadline" in text
    assert "find-the-ledger: Find the ledger (closed) — Winifred burned the ledger." in text
    assert ("the-midnight-deadline: The midnight deadline (threat, open), due midnight"
            " — Seraphine must pay by midnight.") in text
    assert "Winifred found the first page." in text
    assert "signals: same title; same slug; word overlap" in text
    assert '"I want that ledger back." — Winifred, certainty 0.8' in text
    assert "The old search ended; this is a new one." in text
    assert "Distinguished from: find-the-ledger" in text


def test_template_rows_signal_text_order():
    signals = {"title_equal": True, "slug_equal": True, "tokens": 0.5, "chars": 0.4567,
               "cosine": 0.91, "actors": ["characters:mara", "characters:winifred"],
               "scenes": ["s1", "s2"], "anchors": ["event:e1"], "via": "lexical"}
    row = {"key": "r1", "kind": "thread", "title": "t", "beat": "b", "status": "open",
           "commitment_kind": "", "due": "", "quote": "", "speaker": "", "certainty": None,
           "why_new": "", "distinguished_from": [],
           "candidates": [{"id": "x", "title": "X", "status": "open", "kind": "", "due": "",
                           "latest_beat": "", "earlier": [], "signals": signals}]}
    [out] = identity.template_rows([row])
    [cand] = out["candidates"]
    assert cand["signal_text"] == ("same title; same slug; word overlap 0.50; text overlap 0.46;"
                                   " meaning 0.91; shared characters: characters:mara,"
                                   " characters:winifred; shared scenes: 2;"
                                   " shared dates: event:e1")
    assert cand["line"] == "x: X (open)"
    assert "line" not in row["candidates"][0]   # the input is not mutated


def test_build_prompt_with_no_optional_fields_renders():
    signals = {"title_equal": False, "slug_equal": False, "tokens": 0.3, "chars": 0.0,
               "cosine": None, "actors": [], "scenes": [], "anchors": [], "via": "lexical"}
    row = {"key": "r1", "kind": "commitment", "title": "The Saltmarch tithe",
           "beat": "Mara owes the tithe.", "status": "", "commitment_kind": "", "due": "",
           "quote": "", "speaker": "", "certainty": None, "why_new": "",
           "distinguished_from": [],
           "candidates": [{"id": "salt-owed", "title": "Salt owed", "status": "open",
                           "kind": "", "due": "", "latest_beat": "", "earlier": [],
                           "signals": signals}]}
    _, user = identity.build_prompt([row])
    text = user["content"]
    assert "Row r1 — proposed commitment: The Saltmarch tithe" in text
    # A blank commitment kind is shown as the default it means.
    assert "salt-owed: Salt owed (promise, open)" in text
    assert "signals: word overlap 0.30" in text
    for label in ("Status:", "Kind:", "Due:", "Cited:", "certainty", "Why new:",
                  "Distinguished from:", "Earlier:"):
        assert label not in text, label
    assert not text.endswith("\n")


# ------------------------------------------------------------ deciding rows

#: A second reword of `LEDGER_THREAD`, so two rows can name one record.
RECOVER_THE_LEDGER_AGAIN = {"title": "Recover the ledger",
                            "beat": "Winifred searched the harbour for the ledger.",
                            "status": "open"}

#: A proposed commitment rewording `the-midnight-deadline`, with no due phrase.
SERAPHINES_THREAT = {"title": "Seraphine's midnight deadline",
                     "beat": "Seraphine must pay the midnight deadline.",
                     "kind": "threat", "status": "open"}

#: A citation, as `parse_output` carries one.
CITED = {"quote": "I want that ledger back.", "speaker": "Winifred", "certainty": 0.8}


def _say(*answers):
    """A resolver reply, through `parse_output` as the route reads one."""
    return identity.parse_output(json.dumps({"decisions": [
        {"row": row, "decision": word, "id": rid, "reason": f"because {row}"}
        for row, word, rid in answers]}))


def _assert_ledger_candidate(cid, sid, row):
    sig = similarity.lexical(_proposed("thread", row, sid), _pooled(cid, "thread:find-the-ledger"))
    assert sig["tokens"] >= similarity.TOKEN_FLOOR


def _seed_deadline(cid, scene, status="open"):
    store.commitments.set_movement(cid, "the-midnight-deadline", "The midnight deadline",
                                   "threat", status, "midnight",
                                   "Seraphine must pay by midnight.", scene)


def _rewrite(exam, plot=(), owed=()):
    parsed = {"plot_movements": [dict(r) for r in plot],
              "commitment_movements": [dict(r) for r in owed], "one_line": "o"}
    return parsed, exam.rewritten(parsed)


def test_accepted_existing_rewrites_plot_row_status_never_open(cid, s0, sid):
    _seed_ledger(cid, s0)
    _assert_ledger_candidate(cid, sid, RECOVER_THE_LEDGER)
    for given, staged in (("open", "advanced"), ("advanced", "advanced"), ("closed", "closed")):
        row = {**RECOVER_THE_LEDGER, "status": given}
        exam = _examine(cid, sid, plot=[row])
        exam.decide(_say(("r1", "existing", "find-the-ledger")))
        [examined] = exam.rows
        assert (examined.decision, examined.status, examined.target) == (
            "existing", "accepted", "find-the-ledger")
        parsed, out = _rewrite(exam, plot=[row])
        [new] = out["plot_movements"]
        assert new["id"] == "find-the-ledger"
        assert new["title"] == ""
        assert new["status"] == staged
        assert new["beat"] == row["beat"]
        assert new[identity.AS_NEW_KEY] == row
        assert parsed["plot_movements"] == [row]          # the input is not mutated
        assert out["plot_movements"] is not parsed["plot_movements"]
        assert out["one_line"] == "o"


def test_accepted_existing_rewrites_commitment_fields(cid, s0, sid):
    _seed_deadline(cid, s0)
    sig = similarity.lexical(_proposed("commitment", SERAPHINES_THREAT, sid),
                             _pooled(cid, "commitment:the-midnight-deadline"))
    assert sig["tokens"] >= similarity.TOKEN_FLOOR
    exam = _examine(cid, sid, owed=[SERAPHINES_THREAT])
    exam.decide(_say(("r1", "existing", "the-midnight-deadline")))
    [new] = _rewrite(exam, owed=[SERAPHINES_THREAT])[1]["commitment_movements"]
    assert (new["id"], new["title"], new["kind"], new["status"]) == (
        "the-midnight-deadline", "", "", "")
    assert "due" not in new
    assert new[identity.AS_NEW_KEY] == SERAPHINES_THREAT

    kept = {**SERAPHINES_THREAT, "status": "fulfilled", "due": "midnight"}
    exam = _examine(cid, sid, owed=[kept])
    exam.decide(_say(("r1", "existing", "the-midnight-deadline")))
    [new] = _rewrite(exam, owed=[kept])[1]["commitment_movements"]
    assert (new["id"], new["kind"], new["status"], new["due"]) == (
        "the-midnight-deadline", "", "fulfilled", "midnight")


def test_existing_naming_an_unoffered_id_is_uncertain(cid, s0, sid):
    _seed_ledger(cid, s0)
    _seed_deadline(cid, s0)
    exam = _examine(cid, sid, plot=[RECOVER_THE_LEDGER])
    # A stored record of another type, and an id that names nothing.
    for rid in ("the-midnight-deadline", "nope", ""):
        exam.decide(_say(("r1", "existing", rid)))
        [examined] = exam.rows
        assert (examined.decision, examined.status, examined.reason, examined.target) == (
            "uncertain", "downgraded", "named a record that was not offered", None)
    [new] = _rewrite(exam, plot=[RECOVER_THE_LEDGER])[1]["plot_movements"]
    assert "id" not in new and new["title"] == RECOVER_THE_LEDGER["title"]
    assert identity.AS_NEW_KEY not in new


def test_existing_naming_a_closed_candidate_is_downgraded(cid, s0, sid):
    _seed_ledger(cid, s0, status="closed")
    row = {**RECOVER_THE_LEDGER, "id": "recover-the-harbour-ledger"}
    exam = _examine(cid, sid, plot=[row])
    [examined] = exam.rows
    [(cand, _)] = examined.candidates
    assert cand.ref == "thread:find-the-ledger" and cand.live is False
    exam.decide(_say(("r1", "existing", "find-the-ledger")))
    assert (examined.decision, examined.status, examined.reason, examined.target) == (
        "uncertain", "downgraded", "that record is already closed or resolved", None)
    [new] = _rewrite(exam, plot=[row])[1]["plot_movements"]
    assert new["id"] == "recover-the-harbour-ledger"
    assert new["title"] == row["title"] and new["status"] == "open"
    assert new["identity_check"]["decision"] == "uncertain"
    assert identity.AS_NEW_KEY not in new


def test_existing_naming_a_resolved_commitment_is_downgraded(cid, s0, sid):
    _seed_deadline(cid, s0, status="broken")
    exam = _examine(cid, sid, owed=[SERAPHINES_THREAT])
    exam.decide(_say(("r1", "existing", "commitment:the-midnight-deadline")))
    [examined] = exam.rows
    assert (examined.decision, examined.status) == ("uncertain", "downgraded")
    assert examined.reason == "that record is already closed or resolved"


def test_second_row_mapping_to_the_same_record_is_downgraded(cid, s0, sid):
    _seed_ledger(cid, s0)
    _assert_ledger_candidate(cid, sid, RECOVER_THE_LEDGER)
    _assert_ledger_candidate(cid, sid, RECOVER_THE_LEDGER_AGAIN)
    exam = _examine(cid, sid, plot=[RECOVER_THE_LEDGER, RECOVER_THE_LEDGER_AGAIN])
    first, second = exam.rows
    assert (first.key, second.key) == ("r1", "r2")
    exam.decide(_say(("r1", "existing", "find-the-ledger"), ("r2", "existing", "find-the-ledger")))
    assert (first.decision, first.status, first.target) == ("existing", "accepted",
                                                            "find-the-ledger")
    assert (second.decision, second.status, second.reason, second.target) == (
        "uncertain", "downgraded", "another row in this scene already moves that record", None)


def test_existing_naming_a_record_an_explicit_row_already_moves_is_downgraded(cid, s0, sid):
    _seed_ledger(cid, s0)
    explicit = {"id": "find-the-ledger", "title": "", "beat": "Winifred found a page.",
                "status": "advanced"}
    exam = _examine(cid, sid, plot=[explicit, RECOVER_THE_LEDGER])
    assert exam.targets == {("thread", "find-the-ledger")}
    [examined] = exam.rows
    assert (examined.key, examined.index) == ("r1", 1)
    exam.decide(_say(("r1", "existing", "find-the-ledger")))
    assert (examined.decision, examined.status, examined.reason) == (
        "uncertain", "downgraded", "another row in this scene already moves that record")
    # decide never adds to the examination's own targets.
    assert exam.targets == {("thread", "find-the-ledger")}


def test_existing_named_by_alias_source_or_ref_form_is_accepted(cid, s0, sid):
    _seed_alias(cid, s0)
    row = {"title": "Mara's map", "beat": "Mara hunted for her map.", "status": "open"}
    exam = _examine(cid, sid, plot=[row])
    [examined] = exam.rows
    assert "thread:b" in _refs(examined)
    for named in ("a", "thread:b", "thread:a", "b"):
        exam.decide(_say(("r1", "existing", named)))
        assert (examined.decision, examined.status, examined.target) == (
            "existing", "accepted", "b"), named
    [new] = _rewrite(exam, plot=[row])[1]["plot_movements"]
    assert new["id"] == "b"


def test_a_ref_prefix_of_the_other_kind_is_not_stripped(cid, s0, sid):
    _seed_ledger(cid, s0)
    exam = _examine(cid, sid, plot=[RECOVER_THE_LEDGER])
    exam.decide(_say(("r1", "existing", "commitment:find-the-ledger")))
    [examined] = exam.rows
    assert (examined.decision, examined.status) == ("uncertain", "downgraded")
    assert examined.reason == "named a record that was not offered"


def test_missing_decision_is_unchecked_hint_only(cid, s0, sid):
    _seed_ledger(cid, s0)
    exam = _examine(cid, sid, plot=[RECOVER_THE_LEDGER, RECOVER_THE_LEDGER_AGAIN])
    first, second = exam.rows
    exam.decide(_say(("r1", "new", "")))
    assert (first.decision, first.status, first.reason) == ("new", "accepted", "because r1")
    assert (second.decision, second.status, second.reason, second.target) == (
        "unchecked", "hint_only", "the check gave no answer for this row", None)
    assert not exam.all_unchecked()
    exam.decide([])
    assert exam.all_unchecked()


def test_new_and_uncertain_arrive_as_given_with_the_models_reason(cid, s0, sid):
    _seed_ledger(cid, s0)
    exam = _examine(cid, sid, plot=[RECOVER_THE_LEDGER, RECOVER_THE_LEDGER_AGAIN])
    first, second = exam.rows
    exam.decide(_say(("r1", "uncertain", "find-the-ledger"), ("r2", "new", "find-the-ledger")))
    assert (first.decision, first.status, first.reason, first.target) == (
        "uncertain", "accepted", "because r1", None)
    assert (second.decision, second.status, second.reason, second.target) == (
        "new", "accepted", "because r2", None)


def test_unknown_decision_word_arrives_as_uncertain_accepted(cid, s0, sid):
    _seed_ledger(cid, s0)
    exam = _examine(cid, sid, plot=[RECOVER_THE_LEDGER])
    exam.decide(_say(("r1", "maybe", "find-the-ledger")))
    [examined] = exam.rows
    assert (examined.decision, examined.status, examined.target) == (
        "uncertain", "accepted", None)


def test_hint_only_discards_decide(cid, s0, sid):
    _seed_ledger(cid, s0)
    exam = _examine(cid, sid, plot=[RECOVER_THE_LEDGER])
    exam.decide(_say(("r1", "existing", "find-the-ledger")))
    for _ in range(2):   # idempotent
        exam.hint_only("no connection")
        [examined] = exam.rows
        assert (examined.decision, examined.status, examined.reason, examined.target) == (
            "unchecked", "hint_only", "no connection", None)
    assert exam.all_unchecked()
    [new] = _rewrite(exam, plot=[RECOVER_THE_LEDGER])[1]["plot_movements"]
    assert "id" not in new and new["title"] == RECOVER_THE_LEDGER["title"]
    assert identity.AS_NEW_KEY not in new
    assert new["identity_check"]["decision"] == "unchecked"


def test_citations_survive_rewrite(cid, s0, sid):
    _seed_ledger(cid, s0)
    cited = {**RECOVER_THE_LEDGER, **CITED}
    again = {**RECOVER_THE_LEDGER_AGAIN, **CITED}
    exam = _examine(cid, sid, plot=[cited, again])
    exam.decide(_say(("r1", "existing", "find-the-ledger"), ("r2", "uncertain", "")))
    accepted, uncertain = _rewrite(exam, plot=[cited, again])[1]["plot_movements"]
    for new in (accepted, uncertain):
        assert {k: new[k] for k in CITED} == CITED


def test_identity_check_shape(cid, s0, sid):
    _seed_ledger(cid, s0)
    row = {**RECOVER_THE_LEDGER, "why_new": "A second search.",
           "distinguished_from": ["find-the-ledger", "nope"]}
    exam = _examine(cid, sid, plot=[row, SALTMARCH_TITHE])
    exam.decide(_say(("r1", "existing", "find-the-ledger")))
    [new, plain] = _rewrite(exam, plot=[row, SALTMARCH_TITHE])[1]["plot_movements"]
    assert plain == SALTMARCH_TITHE   # no plausible candidate: untouched
    ic = new["identity_check"]
    assert set(ic) == {"decision", "status", "reason", "proposed", "candidates"}
    assert (ic["decision"], ic["status"], ic["reason"]) == ("existing", "accepted",
                                                            "because r1")
    assert ic["proposed"] == {"title": "Recover the harbour ledger",
                              "why_new": "A second search.",
                              "distinguished_from": ["find-the-ledger"]}
    [cand] = ic["candidates"]
    assert set(cand) == {"ref", "title", "status", "latest_beat", "signals"}
    assert (cand["ref"], cand["title"], cand["status"], cand["latest_beat"]) == (
        "thread:find-the-ledger", "Find the ledger", "open",
        "Winifred learned the harbour ledger exists.")
    assert cand["signals"]["via"] == "lexical"
    # The private original carries no identity_check of its own.
    assert "identity_check" not in new[identity.AS_NEW_KEY]
    json.dumps(new)   # stored with the review, so plain JSON


def test_proposed_title_falls_back_to_the_assigned_id(cid, s0, sid):
    _seed_ledger(cid, s0)
    row = {"id": "recover-the-harbour-ledger", "title": "", "beat": RECOVER_THE_LEDGER["beat"],
           "status": "open"}
    exam = _examine(cid, sid, plot=[row])
    [new] = _rewrite(exam, plot=[row])[1]["plot_movements"]
    assert new["identity_check"]["proposed"]["title"] == "recover-the-harbour-ledger"
    assert new["identity_check"]["proposed"]["why_new"] == ""


COUNT_KEYS = {"proposed", "examined", "candidates", "deterministic", "semantic", "embedded",
              "existing", "new", "uncertain", "unchecked", "downgraded", "hint_only"}


def test_counts_are_flat_ints(cid, s0, sid, monkeypatch):
    _configure()
    proposed = _seed_paraphrase_fixture(cid, s0, sid, cached=True)
    near = [0.9, math.sqrt(1 - 0.81)]
    monkeypatch.setattr(similarity, "_CLIENT", FakeEmbeddings(
        vector_for=lambda t: near if t == proposed.text else [0.0, 1.0]))
    exam = _examine(cid, sid, plot=[RECOVER_THE_LEDGER, PARAPHRASE, SALTMARCH_TITHE],
                    deadline=_soon())
    before = exam.counts()
    assert set(before) == COUNT_KEYS
    assert all(type(v) is int for v in before.values())
    assert (before["proposed"], before["examined"], before["candidates"]) == (3, 2, 2)
    assert (before["deterministic"], before["semantic"]) == (1, 1)
    assert before["embedded"] == exam.embedded
    assert (before["unchecked"], before["hint_only"], before["existing"]) == (2, 2, 0)

    exam.decide(_say(("r1", "existing", "find-the-ledger"), ("r2", "existing", "find-the-ledger")))
    counts = exam.counts()
    assert set(counts) == COUNT_KEYS
    assert all(type(v) is int for v in counts.values())
    assert counts["deterministic"] + counts["semantic"] == counts["candidates"]
    assert {k: counts[k] for k in ("existing", "new", "uncertain", "unchecked", "downgraded",
                                   "hint_only")} == {
        "existing": 1, "new": 0, "uncertain": 1, "unchecked": 0, "downgraded": 1,
        "hint_only": 0}


def test_all_unchecked_is_false_with_no_examined_rows(cid, s0, sid):
    _seed_ledger(cid, s0)
    exam = _examine(cid, sid, plot=[SALTMARCH_TITHE])
    assert exam.rows == []
    exam.decide([])
    assert not exam.all_unchecked()
    assert exam.rewritten({"plot_movements": [SALTMARCH_TITHE]}) == {
        "plot_movements": [SALTMARCH_TITHE]}


# ------------------------------------------------- siblings of a retarget
#
# `assign_ids` drops a later row that lands on an id an earlier row in its
# section already holds. When that earlier row is a proposal the resolver then
# retargets onto a stored record, the id it held is free again on
# materialize's own `assign_ids` run -- so the rewrite has to keep the
# dropped sibling dropped, or its beat opens the very duplicate the resolver
# ruled out, with no identity_check to say so.

#: A second id-less row under `RECOVER_THE_LEDGER`'s title, with its own beat.
RECOVER_THE_LEDGER_TWIN = {**RECOVER_THE_LEDGER,
                           "beat": "Winifred asked the harbourmaster about the ledger."}

#: The same business named by the id the first row is assigned.
RECOVER_THE_LEDGER_BY_ID = {"id": "recover-the-harbour-ledger", "title": "",
                            "beat": "Winifred asked the harbourmaster about the ledger.",
                            "status": "open"}

#: A second id-less row under `SERAPHINES_THREAT`'s title, with its own beat.
SERAPHINES_THREAT_TWIN = {**SERAPHINES_THREAT,
                          "beat": "Seraphine swore again that she would pay by midnight."}


def _targets(cid, sid, parsed, kind):
    return [(e["target"]["id"], e["after"]) for e in absorb.materialize(cid, sid, parsed)
            if e["kind"] == kind]


def _assert_second_move(cid, section, rows):
    """The sibling is dropped before any identity step: a second move of the
    record the first row is assigned."""
    parsed = {"plot_movements": [], "commitment_movements": [], section: rows}
    assigned = materializer.assign_ids(store.plot.read(cid), store.commitments.read(cid), parsed)
    assert assigned[(section, 0)] is not None
    assert assigned[(section, 1)] is None


@pytest.mark.parametrize("sibling", [RECOVER_THE_LEDGER_TWIN, RECOVER_THE_LEDGER_BY_ID],
                         ids=["idless", "explicit-id"])
def test_a_sibling_deduped_onto_a_retargeted_row_stays_dropped(cid, s0, sid, sibling):
    _seed_ledger(cid, s0)
    rows = [RECOVER_THE_LEDGER, sibling]
    _assert_second_move(cid, "plot_movements", rows)
    exam = _examine(cid, sid, plot=rows)
    [examined] = exam.rows
    assert examined.index == 0
    exam.decide(_say(("r1", "existing", "find-the-ledger")))
    out = _rewrite(exam, plot=rows)[1]
    assert _targets(cid, sid, out, "plot") == [("find-the-ledger", RECOVER_THE_LEDGER["beat"])]
    # The sibling is retargeted too, and carries no check of its own.
    kept = out["plot_movements"][1]
    assert kept["id"] == "find-the-ledger" and kept["beat"] == sibling["beat"]
    assert "identity_check" not in kept and identity.AS_NEW_KEY not in kept


def test_a_commitment_sibling_deduped_onto_a_retargeted_row_stays_dropped(cid, s0, sid):
    _seed_deadline(cid, s0)
    rows = [SERAPHINES_THREAT, SERAPHINES_THREAT_TWIN]
    _assert_second_move(cid, "commitment_movements", rows)
    exam = _examine(cid, sid, owed=rows)
    [examined] = exam.rows
    assert examined.index == 0
    exam.decide(_say(("r1", "existing", "the-midnight-deadline")))
    out = _rewrite(exam, owed=rows)[1]
    assert _targets(cid, sid, out, "commitment") == [
        ("the-midnight-deadline", SERAPHINES_THREAT["beat"])]


@pytest.mark.parametrize("word", ["new", "uncertain"])
def test_a_sibling_of_a_row_not_retargeted_is_untouched(cid, s0, sid, word):
    _seed_ledger(cid, s0)
    rows = [RECOVER_THE_LEDGER, RECOVER_THE_LEDGER_TWIN]
    exam = _examine(cid, sid, plot=rows)
    exam.decide(_say(("r1", word, "")))
    out = _rewrite(exam, plot=rows)[1]
    assert out["plot_movements"][1] == RECOVER_THE_LEDGER_TWIN
    assert _targets(cid, sid, out, "plot") == [
        ("recover-the-harbour-ledger", RECOVER_THE_LEDGER["beat"])]
