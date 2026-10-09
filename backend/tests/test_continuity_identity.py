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
from grimoire import decisions, embeddings, inference, prompts
from grimoire.main import create_app
from grimoire.store import absorb, config, embed_space, llm_connections, vectors
from grimoire.store.absorb import materializer
from grimoire.store.campaigns import paths as campaigns_paths
from grimoire.store.continuity import identity, review, similarity
from tests.llm_fakes import FakeEmbeddings, decision_reply
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


# ------------------------------------------------------------ the vocabularies


def test_the_decision_vocabularies():
    assert identity.DECISIONS == ("existing", "new", "uncertain")
    assert identity.CHECK_DECISIONS == ("existing", "new", "uncertain", "unchecked")
    assert identity.STATUSES == ("accepted", "downgraded", "hint_only")


# ------------------------------------------------------------ the item context


def _context(row, live=None):
    """The `decide()` item context `build_items` makes of one `prompt_rows()` row."""
    [item] = identity.build_items([row], live or {})
    return item.context

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


def test_build_items_show_rows_candidates_and_signals(cid, s0, sid):
    _seed_prompt_fixture(cid, s0)
    exam = _examine(cid, sid, plot=[REOPENED_LEDGER], owed=[SERAPHINES_DEADLINE])
    items = identity.build_items(exam.prompt_rows(), exam.live)
    assert len(items) == 2
    text = "\n".join(item.context for item in items)
    assert items[0].context.startswith("Proposed plot thread: Find the ledger\n")
    assert items[1].context.startswith("Proposed commitment: Seraphine's midnight deadline\n")
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


def test_build_items_with_no_optional_fields_render():
    signals = {"title_equal": False, "slug_equal": False, "tokens": 0.3, "chars": 0.0,
               "cosine": None, "actors": [], "scenes": [], "anchors": [], "via": "lexical"}
    row = {"key": "r1", "kind": "commitment", "title": "The Saltmarch tithe",
           "beat": "Mara owes the tithe.", "status": "", "commitment_kind": "", "due": "",
           "quote": "", "speaker": "", "certainty": None, "why_new": "",
           "distinguished_from": [],
           "candidates": [{"id": "salt-owed", "title": "Salt owed", "status": "open",
                           "kind": "", "due": "", "latest_beat": "", "earlier": [],
                           "signals": signals}]}
    text = _context(row)
    assert text.startswith("Proposed commitment: The Saltmarch tithe\n")
    # A blank commitment kind is shown as the default it means.
    assert "salt-owed: Salt owed (promise, open)" in text
    assert "signals: word overlap 0.30" in text
    for label in ("Status:", "Kind:", "Due:", "Cited:", "certainty", "Why new:",
                  "Distinguished from:", "Earlier:"):
        assert label not in text, label
    assert not text.endswith("\n")


def _candidate_text_bytes(cand):
    fields = [cand["id"], cand["title"], cand["kind"], cand["status"], cand["due"],
              cand["latest_beat"], *cand["earlier"]]
    return sum(len(f.encode("utf-8")) for f in fields)


def test_a_long_stored_beat_is_clipped_in_the_item_context(cid, s0, sid):
    # Ledger routes take a beat of any length, and a closed record is in the
    # pool though not in the extraction snapshot: its title can select it while
    # its beats would blow the item's context. The candidate's stored text
    # is shown within the identity-text bound, as its embedding input was.
    pid, title, _ = LEDGER_THREAD
    first = "Winifred found the first page. " + "ш" * 20000
    latest = "Winifred burned the ledger. " + "灰" * 20000
    store.plot.set_movement(cid, pid, title, "open", first, s0)
    store.plot.set_movement(cid, pid, "", "closed", latest, s0)
    exam = _examine(cid, sid, plot=[REOPENED_LEDGER])
    [row] = exam.prompt_rows()
    [cand] = row["candidates"]
    bound = similarity.CONTINUITY_IDENTITY_BYTES
    assert _candidate_text_bytes(cand) <= bound
    assert cand["title"] == "Find the ledger"
    assert cand["latest_beat"].startswith("Winifred burned the ledger. 灰")
    assert latest.startswith(cand["latest_beat"])
    context = _context(row, exam.live)
    # The same context with one-byte beats: the labels and separators, the fixed overhead.
    short = _context({**row, "candidates": [
        {**cand, "latest_beat": "-", "earlier": ["-"] * len(cand["earlier"])}]}, exam.live)
    assert len(context.encode("utf-8")) <= len(short.encode("utf-8")) + bound
    assert "Winifred burned the ledger. 灰灰灰" in context


def test_every_stored_candidate_field_shares_the_identity_text_bound(cid, s0, sid):
    bound = similarity.CONTINUITY_IDENTITY_BYTES
    # The kind is what a ledger route stores (one of `commitments.KINDS`); the
    # id is the model's handle: both are shown whole, the rest shares what is left.
    record = {"title": "The Saltmarch tithe " + "t" * bound, "kind": "threat",
              "due": "midsummer " + "d" * bound, "status": "open",
              "beats": [{"text": "Mara swore it. " + "e" * bound, "scene": s0},
                        {"text": "Mara owes the tithe. " + "b" * bound, "scene": s0}]}
    stored = similarity.subject("commitment", "commitment:the-saltmarch-tithe", record)
    examined = identity.Examined("commitment_movements", 0, "r1", "commitment",
                                 {"title": "Saltmarch tithe", "beat": "Mara owes."},
                                 "saltmarch-tithe", [(stored, {"via": "lexical"})], [])
    [cand] = identity.Examination([examined], 1, "basic", "off", "", 0, set(),
                                  {}).prompt_rows()[0]["candidates"]
    assert _candidate_text_bytes(cand) <= bound
    assert (cand["id"], cand["kind"]) == ("the-saltmarch-tithe", "threat")
    assert cand["title"].startswith("The Saltmarch tithe t")
    assert all(not text or (text in record["kind"] or text in record["due"]
                            or any(text in b["text"] for b in record["beats"]))
               for text in (cand["kind"], cand["due"], cand["latest_beat"], *cand["earlier"]))
    assert "" not in cand["earlier"]


def test_a_record_whose_id_overruns_the_bound_is_never_a_candidate(cid, s0, sid):
    # A ledger route slugifies a title of any length into the record's id, and
    # the model has to name a candidate back by that id, so it cannot be
    # clipped: a record whose id alone overruns the identity-text bound is not
    # offered, and the prompt does not grow with it.
    bound = similarity.CONTINUITY_IDENTITY_BYTES
    _seed_prompt_fixture(cid, s0)
    long_title = "Find the ledger " + "x" * 30000
    long_id = store.paths.slugify(long_title)
    store.plot.set_movement(cid, long_id, long_title, "closed", "Mara hid it.", s0)
    exam = _examine(cid, sid, plot=[REOPENED_LEDGER])
    [row] = exam.prompt_rows()
    assert [c["id"] for c in row["candidates"]] == ["find-the-ledger"]
    assert [s.ref for s, _ in exam.rows[0].candidates] == ["thread:find-the-ledger"]
    assert len(_context(row, exam.live).encode("utf-8")) <= 2 * bound


def test_a_title_filling_the_bound_does_not_relabel_a_commitment_kind(s0):
    bound = similarity.CONTINUITY_IDENTITY_BYTES
    record = {"title": "The Saltmarch tithe " + "t" * bound, "kind": "threat",
              "status": "open", "beats": [{"text": "Mara swore it.", "scene": s0}]}
    stored = similarity.subject("commitment", "commitment:the-saltmarch-tithe", record)
    examined = identity.Examined("commitment_movements", 0, "r1", "commitment",
                                 {"title": "Saltmarch tithe", "beat": "Mara owes."},
                                 "saltmarch-tithe", [(stored, {"via": "lexical"})], [])
    [row] = identity.Examination([examined], 1, "basic", "off", "", 0, set(),
                                 {}).prompt_rows()
    [cand] = row["candidates"]
    assert cand["kind"] == "threat"
    assert _candidate_text_bytes(cand) <= bound
    context = _context(row)
    assert "(threat, open)" in context
    assert "promise" not in context


# ------------------------------------------------------------ deciding rows

#: A second reword of `LEDGER_THREAD`, so two rows can name one record.
RECOVER_THE_LEDGER_AGAIN = {"title": "Recover the ledger",
                            "beat": "Winifred searched the harbour for the ledger.",
                            "status": "open"}

#: A proposed commitment rewording `the-midnight-deadline`, with no due phrase.
SERAPHINES_THREAT = {"title": "Seraphine's midnight deadline",
                     "beat": "Seraphine must pay the midnight deadline.",
                     "kind": "threat", "status": "open"}

#: A citation, as the extraction carries one.
CITED = {"quote": "I want that ledger back.", "speaker": "Winifred", "certainty": 0.8}


def _say(*answers):
    """Today's decision dicts, as `answers_of` hands them to
    `Examination.decide`: one per `(row, word, id)`."""
    return [{"row": row, "decision": word, "id": rid, "reason": f"because {row}"}
            for row, word, rid in answers]


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


def test_a_blank_due_on_a_retargeted_row_never_lifts_the_stored_deadline(cid, s0, sid):
    # On a row that would open a record "" lifts nothing -- there is no deadline
    # yet -- so moving it onto a stored commitment must not turn it into an
    # instruction to clear that commitment's deadline. A stated one still lands.
    _seed_deadline(cid, s0)
    blank = {**SERAPHINES_THREAT, "due": ""}
    exam = _examine(cid, sid, owed=[blank])
    exam.decide(_say(("r1", "existing", "the-midnight-deadline")))
    [new] = _rewrite(exam, owed=[blank])[1]["commitment_movements"]
    assert new["id"] == "the-midnight-deadline"
    assert "due" not in new
    assert new[identity.AS_NEW_KEY] == blank


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


def test_a_hand_edited_long_status_shares_the_identity_text_bound(cid, s0, sid):
    # A ledger route only stores a known status, but plot.json is hand-editable
    # and `effective.records` passes a status through as written: it is shown
    # inside the same budget as the rest of the candidate, never whole.
    bound = similarity.CONTINUITY_IDENTITY_BYTES
    record = {"title": "Find the ledger", "status": "closed" + "z" * (10 * bound),
              "beats": [{"text": "Mara hid it.", "scene": s0}]}
    stored = similarity.subject("thread", "thread:find-the-ledger", record)
    examined = identity.Examined("plot_movements", 0, "r1", "thread",
                                 {"title": "Find that ledger again", "beat": "Winifred looks."},
                                 "find-that-ledger-again", [(stored, {"via": "lexical"})], [])
    [row] = identity.Examination([examined], 1, "basic", "off", "", 0, set(),
                                 {}).prompt_rows()
    [cand] = row["candidates"]
    assert _candidate_text_bytes(cand) <= bound
    assert cand["status"].startswith("closed")
    assert len(_context(row).encode("utf-8")) <= 2 * bound


# ------------------------------------------------- the check as decision items
#
# Slice G prepares the duplicate check for `decide()` without moving the call
# site: one item per examined row, asking `decision` and then `id`, and a
# mapping from the parsed batch back to today's decision dicts, for the
# unchanged `Examination.decide`.

def _decision_row(key, kind, title, ids, **over):
    """A `prompt_rows()`-shaped row offering a candidate per id, in rank order."""
    signals = {"title_equal": False, "slug_equal": False, "tokens": 0.4, "chars": 0.5,
               "cosine": None, "actors": [], "scenes": [], "anchors": [], "via": "lexical"}
    return {"key": key, "kind": kind, "title": title, "beat": "Winifred went looking.",
            "status": "open", "commitment_kind": "", "due": "", "quote": "", "speaker": "",
            "certainty": None, "why_new": "", "distinguished_from": [],
            "candidates": [{"id": rid, "title": rid.replace("-", " ").capitalize(),
                            "status": "open", "kind": "", "due": "",
                            "latest_beat": f"{rid} moved.", "earlier": [],
                            "signals": dict(signals)} for rid in ids],
            **over}


def _memory_exam(*rows):
    """An in-memory `Examination` over `(kind, title, [candidate ids])` rows,
    with no store: each candidate an open record of the row's kind."""
    examined = []
    for n, (kind, title, ids) in enumerate(rows):
        cands = [(similarity.subject(kind, f"{kind}:{rid}",
                                     {"title": rid, "status": "open",
                                      "beats": [{"text": f"{rid} moved.", "scene": "s0"}]}),
                  {"via": "lexical"}) for rid in ids]
        section = "plot_movements" if kind == "thread" else "commitment_movements"
        examined.append(identity.Examined(section, n, f"r{n + 1}", kind,
                                          {"title": title, "beat": "Winifred looks."},
                                          f"proposed-{n}", cands, []))
    return identity.Examination(examined, len(examined), "basic", "off", "", 0, set(), {})


def _options(choice):
    return [(o.id, o.description, o.aliases) for o in choice.options]


def _candidate_lines(context):
    return [line for line in context.split("Candidates:", 1)[1].splitlines()
            if line.startswith("- ")]


def _existing(rid):
    return identity.EXISTING_PREFIX + rid


def _offered_ids(item):
    return [o.id for o in item.questions[0].options]


def test_build_items_one_per_row_asking_one_folded_decision():
    rows = [_decision_row("r1", "thread", "Recover the harbour ledger",
                          ["find-the-ledger", "maras-map"], quote="I want it back.",
                          speaker="Winifred", why_new="A second search."),
            _decision_row("r2", "commitment", "Seraphine's midnight deadline",
                          ["the-midnight-deadline"], commitment_kind="threat",
                          due="midnight")]
    items = identity.build_items(rows, {})
    decisions.validate(items)
    assert len(items) == len(rows)
    for item, row, shown in zip(items, rows, identity.template_rows(rows), strict=True):
        # One question: no `id` asked apart from the decision it depends on.
        [decision] = item.questions
        assert decision.id == identity.DECISION_ID
        assert isinstance(decision, decisions.Choice) and not decision.allow_none
        assert decision.instructions == prompts.render("continuity_identity/question.j2")
        assert [o.id for o in decision.options] == [
            *(_existing(c["id"]) for c in row["candidates"]), "new", "uncertain"]
        assert [o.description for o in decision.options] == [
            *(prompts.render("continuity_identity/option.j2", decision="existing",
                             title=c["title"]) for c in row["candidates"]),
            *(prompts.render("continuity_identity/option.j2", decision=w)
              for w in ("new", "uncertain"))]
        assert item.context == prompts.render("continuity_identity/item.j2", r=shown)
        for cand in shown["candidates"]:
            assert cand["line"] in item.context
    assert items[0].context.startswith(
        "Proposed plot thread: Recover the harbour ledger\nBeat: Winifred went looking.")
    assert items[1].context.startswith("Proposed commitment: Seraphine's midnight deadline\n")
    assert "Row r1" not in items[0].context
    # A candidate is described under its clipped title, and spelled by its ref form.
    # The title is quoted and followed by a comma, so the option line keeps one
    # colon between the id and its description (`decide/user.j2` quotes the
    # id); each spelling is also offered with a space after "existing:".
    assert _options(items[0].questions[0])[0] == (
        "existing:find-the-ledger",
        ('"Find the ledger", only when this listed candidate is the same narrative question '
         "or obligation, so the row's beat simply moves that record forward."),
        ("existing:thread:find-the-ledger", "existing: find-the-ledger",
         "existing: thread:find-the-ledger"))
    user = inference.structured_messages(items[:1], explain="")[1]["content"]
    assert ('  - "existing:find-the-ledger": "Find the ledger", only when this listed '
            "candidate") in user
    assert identity.explain() == prompts.render("continuity_identity/explain.j2")
    assert identity.EXISTING_PREFIX == "existing:"
    assert identity.UNREADABLE == "the duplicate check returned no readable answer"


def test_a_single_candidate_row_offers_one_existing():
    rows = [_decision_row("r1", "commitment", "Seraphine's midnight deadline",
                          ["the-midnight-deadline"])]
    items = identity.build_items(rows, {})
    decisions.validate(items)
    assert _offered_ids(items[0]) == ["existing:the-midnight-deadline", "new", "uncertain"]
    named = decision_reply({"decision": "existing:the-midnight-deadline"})
    [answered] = decisions.parse(named, items, explain=True)
    assert answered.answers[identity.DECISION_ID].answer == "existing:the-midnight-deadline"
    assert identity.answers_of(rows, (answered,))[0]["id"] == "the-midnight-deadline"


def test_a_candidate_titled_blank_is_described_by_its_id():
    row = _decision_row("r1", "thread", "Recover the ledger", ["find-the-ledger"])
    row["candidates"][0]["title"] = ""
    [item] = identity.build_items([row], {})
    assert item.questions[0].options[0].description.startswith('"find-the-ledger", ')


def test_the_folded_existing_reads_alias_sources_and_ref_forms():
    live = {"thread:a": "thread:b", "thread:c": "thread:other", "thread:b": "thread:b"}
    rows = [_decision_row("r1", "thread", "Mara's map", ["b"])]
    items = identity.build_items(rows, live)
    decisions.validate(items)
    assert _options(items[0].questions[0])[0][::2] == (
        "existing:b", ("existing:thread:b", "existing:thread:a", "existing:a", "existing: b",
                       "existing: thread:b", "existing: thread:a", "existing: a"))
    for named in ("a", "thread:b", "thread:a", "b", "B ", " b", " thread:a", "  a "):
        [result] = decisions.parse(
            decision_reply({"decision": _existing(named)}), items, explain=True)
        assert result.answers[identity.DECISION_ID].answer == "existing:b", named
        assert identity.answers_of(rows, (result,))[0] == {
            "row": "r1", "decision": "existing", "id": "b", "reason": ""}


def test_build_items_drops_a_candidate_whose_id_collides_once_normalised():
    rows = [_decision_row("r1", "thread", "The map", ["the-map", "the map"])]
    items = identity.build_items(rows, {})
    decisions.validate(items)
    assert _offered_ids(items[0]) == ["existing:the-map", "new", "uncertain"]
    assert len(_candidate_lines(items[0].context)) == 1
    assert "the-map: The map" in items[0].context


def test_build_items_drops_a_candidate_whose_id_is_the_reserved_none():
    """`<none>` is the native none's distribution key (slice H, N5), which
    `decisions.offerable` refuses: a hand-edited ledger holding it as an id
    loses that candidate rather than folding it into an option that names it."""
    rows = [_decision_row("r1", "thread", "The ledger",
                          ["<none>", "find-the-ledger", "<NONE>"])]
    items = identity.build_items(rows, {})
    decisions.validate(items)
    assert _offered_ids(items[0]) == ["existing:find-the-ledger", "new", "uncertain"]
    assert len(_candidate_lines(items[0].context)) == 1


def test_build_items_never_builds_a_request_validate_refuses():
    # A hand-edited ledger: a `thread:` key with no id, two ids that read as
    # one once normalised, and an earlier candidate whose alias source spells
    # a later candidate's id. Every id is taken before any alias.
    live = {"thread:maras-map": "thread:find-the-ledger"}
    rows = [_decision_row("r1", "thread", "The ledger",
                          ["", "find-the-ledger", "Find The Ledger", "maras-map"])]
    items = identity.build_items(rows, live)
    decisions.validate(items)
    # The later candidate's spaced id is taken before the earlier one's spaced
    # alias source, which would otherwise spell it.
    assert [(o.id, o.aliases) for o in items[0].questions[0].options] == [
        ("existing:find-the-ledger",
         ("existing:thread:find-the-ledger", "existing:thread:maras-map",
          "existing: find-the-ledger", "existing: thread:find-the-ledger",
          "existing: thread:maras-map")),
        ("existing:maras-map", ("existing: maras-map",)), ("new", ()), ("uncertain", ())]
    assert len(_candidate_lines(items[0].context)) == 2
    for named in ("existing:maras-map", "existing: maras-map", "EXISTING: Maras-Map "):
        [result] = decisions.parse(decision_reply({"decision": named}), items, explain=True)
        assert result.answers[identity.DECISION_ID].answer == "existing:maras-map", named
    # A row left with no offerable candidate is offered no `existing` at all
    # -- an empty id would fold into an option naming nothing -- and one it
    # answers anyway is a word outside the options.
    bare = [_decision_row("r1", "thread", "The ledger", [""])]
    [item] = identity.build_items(bare, {})
    decisions.validate((item,))
    assert _offered_ids(item) == ["new", "uncertain"]
    assert _candidate_lines(item.context) == []
    [result] = decisions.parse(decision_reply({"decision": "existing:"}), (item,),
                               explain=True)
    assert result.answers[identity.DECISION_ID].detail == decisions.NOT_AN_OPTION
    assert identity.answers_of(bare, (result,)) == [
        {"row": "r1", "decision": "", "id": "", "reason": ""}]


def _ids(n, width=0):
    """`n` distinct candidate ids, each padded to `width` characters."""
    return [f"record-{k:04d}".ljust(width, "x") for k in range(n)]


def test_a_full_batch_of_the_longest_ids_fits_one_call_on_both_backends():
    """The folded choice grows with the row's candidates. `examine` offers at
    most `similarity.IDENTITY_TOP_K`, each with an id that fits
    `CONTINUITY_IDENTITY_BYTES`, so a whole chunk of such rows is one call:
    validate accepts it, it is one chunk, and a decisions endpoint takes every
    item."""
    width = similarity.CONTINUITY_IDENTITY_BYTES
    rows = [_decision_row(f"r{n}", "thread", f"Row {n}", _ids(similarity.IDENTITY_TOP_K, width))
            for n in range(decisions.MAX_ITEMS_PER_CALL)]
    items = identity.build_items(rows, {})
    decisions.validate(items)
    assert len(decisions.chunks(items)) == 1
    for item in items:
        assert len(item.questions[0].options) == similarity.IDENTITY_TOP_K + 2
        assert decisions.native_gap(item) == ""


def test_the_folded_choice_stops_at_the_options_a_decision_may_offer():
    """Past what `examine` builds, at the boundary: beside `new` and
    `uncertain`, a row offers at most 253 `existing` options -- 255 in all,
    which `decisions.validate` accepts and a decisions endpoint carries (the
    decision allows no none, so no reserved none is added) -- and the rest, by
    rank, are dropped from the options and the context alike."""
    room = decisions.NATIVE_MAX_OPTIONS - 2
    for count, offered in ((room, room), (room + 1, room)):
        row = _decision_row("r1", "thread", "The ledger", _ids(count))
        [item] = identity.build_items([row], {})
        decisions.validate((item,))
        assert decisions.native_gap(item) == ""
        ids = _offered_ids(item)
        assert len(ids) == offered + 2 == min(count + 2, decisions.NATIVE_MAX_OPTIONS)
        assert ids[:offered] == [_existing(rid) for rid in _ids(offered)]
        assert len(_candidate_lines(item.context)) == offered


def test_the_folded_choice_keeps_inside_the_enum_string_budget():
    """Past `decisions.ENUM_STRING_CHARS_ABOVE` options, strict mode caps one
    enum's strings at `decisions.MAX_ENUM_STRING_CHARS`: long ids stop being
    offered where the next would pass it, so the request stays one validate
    accepts."""
    width = 51   # `existing:` and the id: 60 characters an option
    row = _decision_row("r1", "thread", "The ledger", _ids(decisions.NATIVE_MAX_OPTIONS, width))
    [item] = identity.build_items([row], {})
    decisions.validate((item,))
    ids = _offered_ids(item)
    kept = len(ids) - 2
    assert len(ids) > decisions.ENUM_STRING_CHARS_ABOVE
    assert sum(map(len, ids)) <= decisions.MAX_ENUM_STRING_CHARS
    following = _existing(_ids(kept + 1, width)[kept])
    assert sum(map(len, ids)) + len(following) > decisions.MAX_ENUM_STRING_CHARS
    assert len(_candidate_lines(item.context)) == kept


def _result(decision, rationale=""):
    answer = (decision if isinstance(decision, decisions.Answer)
              else decisions.Answer(decision))
    return decisions.ItemResult({identity.DECISION_ID: answer}, rationale)


_ROWS3 = [{"key": "r1"}, {"key": "r2"}, {"key": "r3"}]


def test_answers_of_maps_each_answer_to_todays_decision_shape():
    results = (_result("existing:find-the-ledger", " Same ledger. "),
               _result("new", "x" * 400),
               _result(decisions.Answer(None, "unreadable", detail=decisions.NOT_AN_OPTION)))
    assert identity.answers_of(_ROWS3, results) == [
        {"row": "r1", "decision": "existing", "id": "find-the-ledger",
         "reason": "Same ledger."},
        {"row": "r2", "decision": "new", "id": "", "reason": "x" * identity.REASON_CHARS},
        {"row": "r3", "decision": "", "id": "", "reason": ""}]
    assert identity.unfolded("existing:thread:b") == ("existing", "thread:b")
    assert identity.unfolded("uncertain") == ("uncertain", "")


_NOT_READ = (decisions.Answer(None, "unreadable", detail=decisions.NO_OBJECT),
             decisions.Answer(None, "unreadable", detail=decisions.NO_ITEM),
             decisions.Answer(None, "error"), decisions.Answer(None, "refused"),
             decisions.Answer(None, "abstained"))


def test_answers_of_leaves_out_every_item_the_reply_never_reached():
    exam = _memory_exam(("thread", "Recover the ledger", ["find-the-ledger"]),
                        ("thread", "Recover the ledger again", ["find-the-ledger"]),
                        ("thread", "Mara's map", ["maras-map"]))
    rows = exam.prompt_rows()
    for missing in _NOT_READ:
        results = (_result("new", "Its own business."), _result(missing), _result("new"))
        answers = identity.answers_of(rows, results)
        assert [a["row"] for a in answers] == ["r1", "r3"], missing
        exam.decide(answers)
        assert [(e.decision, e.status) for e in exam.rows] == [
            ("new", "accepted"), ("unchecked", "hint_only"), ("new", "accepted")], missing
        assert exam.rows[1].reason == identity.NO_ANSWER
    # An item the reply reached and answered badly is read: `uncertain`.
    for garbled in (decisions.Answer(None, "unreadable"),
                    decisions.Answer(None, "unreadable", detail=decisions.NOT_AN_OPTION)):
        answers = identity.answers_of(rows, (_result("new"), _result(garbled), _result("new")))
        assert [a["row"] for a in answers] == ["r1", "r2", "r3"]
        assert answers[1] == {"row": "r2", "decision": "", "id": "", "reason": ""}
        exam.decide(answers)
        assert (exam.rows[1].decision, exam.rows[1].status) == ("uncertain", "accepted")


def test_answers_of_is_none_only_when_no_item_held_an_object():
    no_object, no_item = _NOT_READ[0], _NOT_READ[1]
    assert identity.answers_of(_ROWS3, tuple(_result(no_object) for _ in _ROWS3)) is None
    assert identity.answers_of(_ROWS3, tuple(_result(no_item) for _ in _ROWS3)) == []
    assert identity.answers_of(_ROWS3, (_result(no_object), _result(no_item),
                                        _result(no_object))) == []
    # The reply's own shapes, through the parser: no object, and an object
    # with no item (`{}`, or today's format).
    items = identity.build_items(_memory_exam(
        ("thread", "Recover the ledger", ["find-the-ledger"]),
        ("thread", "Mara's map", ["maras-map"])).prompt_rows(), {})
    rows = [{"key": "r1"}, {"key": "r2"}]
    for text, want in (("I think so.", None), ("{}", []),
                       ('{"decisions": [{"row": "r1", "decision": "new"}]}', [])):
        assert identity.answers_of(rows, decisions.parse(text, items, explain=True)) == want


def test_take_hint_onlys_an_unreadable_reply():
    exam = _memory_exam(("thread", "Recover the ledger", ["find-the-ledger"]),
                        ("thread", "Mara's map", ["maras-map"]))
    assert identity.take(exam, [{"row": "r1", "decision": "new", "id": "",
                                 "reason": "Its own."}]) is True
    assert [(e.decision, e.status) for e in exam.rows] == [
        ("new", "accepted"), ("unchecked", "hint_only")]
    assert identity.take(exam, None) is False
    assert [(e.decision, e.status, e.reason) for e in exam.rows] == [
        ("unchecked", "hint_only", identity.UNREADABLE)] * 2
    assert identity.take(exam, []) is True
    assert [(e.decision, e.status, e.reason) for e in exam.rows] == [
        ("unchecked", "hint_only", identity.NO_ANSWER)] * 2


def test_unfolded_strips_the_space_around_the_id():
    assert identity.unfolded("existing: find-the-ledger ") == ("existing", "find-the-ledger")
    assert identity.unfolded("existing:find-the-ledger") == ("existing", "find-the-ledger")
    assert identity.unfolded("new") == ("new", "")
