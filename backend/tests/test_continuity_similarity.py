"""`store/continuity/similarity.py` -- identity texts and deterministic signals.

The identity text is what a record is compared by (capstone spec §9.1), and the
lexical and structural signals (§9.2) decide which stored records are worth a
resolver's attention. Scores rank candidates; nothing here writes (§3.2). Every
candidate / non-candidate test first asserts the signal that is supposed to
decide it, so a later floor change fails at that assertion rather than
silently somewhere else.
"""

import importlib
import itertools
import json
import math
import time

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import embeddings
from grimoire.main import create_app
from grimoire.store import config, embed_space, llm_connections, logs, usage, vectors
from grimoire.store.campaigns import paths as campaigns_paths
from grimoire.store.continuity import effective, review, similarity
from tests.inference_fixtures import embedding
from tests.llm_fakes import FakeEmbeddings

S1, S2 = "001--saltmarch", "002--realm"
MARA = "characters:mara"


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


def _root(cid):
    return campaigns_paths.campaign_root(cid)


def _thread(ref, title, *beats, actors=(), scenes=(), anchors=()):
    record = {"title": title, "beats": [{"text": b} for b in beats]}
    return similarity.subject("thread", ref, record, actors=actors, scenes=scenes,
                              anchors=anchors)


def _commitment(ref, title, *beats, kind="", due="", actors=()):
    record = {"title": title, "beats": [{"text": b} for b in beats], "kind": kind, "due": due}
    return similarity.subject("commitment", ref, record, actors=actors)


# ----------------------------------------------------------- identity texts


def test_thread_identity_text_order_and_no_ids(cid):
    for beat in ("A", "B", "C", "C"):
        store.plot.set_movement(cid, "find-the-ledger", "Find the ledger", "open", beat, S1)
    rec = effective.records(cid, "thread")["thread:find-the-ledger"]
    text = similarity.thread_identity_text(rec)
    assert text == "plot thread\nFind the ledger\nC\nB\nA"
    assert "find-the-ledger" not in text


def test_commitment_identity_text_blank_kind_is_promise_and_due_present():
    rec = {"title": "The midnight deadline", "kind": "", "due": "midnight",
           "beats": [{"text": "Mara swore to pay."}, {"text": "Mara asked for a day."}]}
    lines = similarity.commitment_identity_text(rec).split("\n")
    assert lines[0] == "promise commitment"
    assert lines[1] == "The midnight deadline"
    assert lines[2] == "Mara asked for a day."
    assert lines[3] == "due midnight"
    assert lines[4:] == ["Mara swore to pay."]


def test_identity_text_is_clipped_to_the_byte_bound():
    rec = {"title": "Find the ledger", "beats": [{"text": "x" * 5000}]}
    text = similarity.thread_identity_text(rec)
    assert len(text.encode("utf-8")) <= similarity.CONTINUITY_IDENTITY_BYTES
    assert text.startswith("plot thread\nFind the ledger\n")


# ------------------------------------------------------------- normalizing


def test_titles_equal_after_nfkc_and_casefold():
    assert similarity.titles_equal("\uff2d\uff21\uff32\uff21'S DEBT", "mara's debt")   # fullwidth MARA
    assert not similarity.titles_equal("", "")


def test_cjk_reword_is_a_trigram_candidate_and_untitled_slugs_are_not_equal():
    assert not similarity.slug_equal("海図", "灯台")
    stored = _thread("thread:untitled", "港の帳簿を探す",
                     "ウィニフレッドは港の帳簿の存在を知った。")
    proposed = _thread("proposed:x", "港の帳簿を取り戻す",
                       "ウィニフレッドは港の帳簿を探しに行った。")
    sig = similarity.lexical(proposed, stored)
    assert not sig["slug_equal"]
    assert sig["chars"] >= similarity.CHAR_FLOOR
    assert sig["tokens"] < similarity.WEAK_TOKEN
    assert similarity.plausible(sig)


def test_stopwords_never_count_as_shared_tokens():
    got = similarity.tokens("The map of the harbour")
    assert "the" not in got and "of" not in got
    assert got == frozenset({"map", "harbour"})
    assert "s" not in similarity.tokens("Mara's boat")


def test_combining_marks_stay_inside_their_word():
    # A Devanagari vowel sign or virama is a combining mark, not alphanumeric:
    # split on it, every word breaks into bare consonants that any two texts in
    # the script share.
    assert similarity.normalize("किताब खोई") == "किताब खोई"
    stored = _thread("thread:a", "मारा की नाव", "मारा ने घाट पर अपनी नाव की मरम्मत की।")
    unrelated = _thread("thread:b", "विनिफ्रेड का दीया", "विनिफ्रेड ने खिड़की में दीया जलाया।")
    sig = similarity.lexical(stored, unrelated)
    assert sig["tokens"] < similarity.TOKEN_FLOOR and sig["chars"] < similarity.CHAR_FLOOR
    assert not sig["title_equal"] and not sig["slug_equal"]
    assert not similarity.plausible(sig)

    reworded = _thread("thread:c", "मारा की नाव खो गई", "मारा की नाव घाट से गायब हो गई।")
    sig = similarity.lexical(stored, reworded)
    assert sig["tokens"] >= similarity.TOKEN_FLOOR
    assert similarity.admitted_by(sig) == "lexical"


def test_a_slug_counts_only_when_it_spells_the_whole_title():
    # `paths.slugify` drops every non-ASCII character, so different titles that
    # share only an ASCII remnant slug alike.
    assert not similarity.slug_equal("Mara的海図", "Mara的灯台")
    assert not similarity.slug_equal("海図 1", "灯台 1")
    assert not similarity.slug_equal("Ölmühle", "Älmühle")
    assert similarity.slug_equal("Mara's map", "Mara-s Map!")
    sig = similarity.lexical(_thread("thread:a", "Mara的海図", "港で古い海図が盗まれた。"),
                             _thread("thread:b", "Mara的灯台", "灯台の明かりが消えた夜。"))
    assert not sig["title_equal"] and not sig["slug_equal"]
    assert sig["tokens"] < similarity.TOKEN_FLOOR and sig["chars"] < similarity.CHAR_FLOOR
    assert not similarity.plausible(sig)


def test_the_due_prefix_is_not_a_shared_word():
    # Every dated commitment's identity text carries the line "due <phrase>";
    # the prefix is layout, like the type label, and says nothing about which
    # obligation a record is.
    debt = _commitment("commitment:a", "Mara's debt", "Mara owes the guild.",
                       due="dawn", actors=[MARA])
    oath = _commitment("commitment:b", "Winifred's oath", "Winifred swore an oath.",
                       due="dusk", actors=[MARA])
    sig = similarity.lexical(debt, oath)
    assert sig["actors"] == [MARA]
    assert sig["tokens"] < similarity.WEAK_TOKEN and sig["chars"] < similarity.WEAK_CHAR
    assert similarity.admitted_by(sig) is None
    assert "due" not in similarity.tokens(similarity.body(debt.text))
    # The phrase itself still counts, and "due" written in a title still does.
    same = _commitment("commitment:c", "Mara's debt", due="dawn")
    assert "dawn" in similarity.tokens(similarity.body(same.text))
    titled = _commitment("commitment:d", "The debt comes due", due="dawn")
    assert "due" in similarity.tokens(similarity.body(titled.text))


# ------------------------------------------------------------ plausibility


def _boat(title="Mara's boat"):
    return _thread("thread:maras-boat", title,
                   "Mara patched the hull of her boat at the pier.", actors=[MARA])


def _lantern():
    return _thread("thread:winifreds-lantern", "Winifred's lantern",
                   "Winifred lit the lantern in the window.", actors=[MARA])


def test_unrelated_records_are_not_plausible_even_with_shared_actors():
    sig = similarity.lexical(_boat(), _lantern())
    assert sig["actors"] == [MARA]
    assert sig["tokens"] < similarity.WEAK_TOKEN
    assert sig["chars"] < similarity.WEAK_CHAR
    assert not similarity.plausible(sig)
    assert similarity.admitted_by(sig) is None


def test_structural_signal_needs_a_weak_lexical_partner():
    sig = similarity.lexical(_boat("Mara's lantern"), _lantern())
    assert sig["actors"] == [MARA]
    assert sig["tokens"] >= similarity.WEAK_TOKEN
    assert sig["tokens"] < similarity.TOKEN_FLOOR and sig["chars"] < similarity.CHAR_FLOOR
    assert not sig["title_equal"] and not sig["slug_equal"]
    assert similarity.admitted_by(sig) == "structural"
    assert similarity.plausible(sig)


def test_admitted_by_names_the_clause():
    equal = similarity.lexical(_thread("thread:a", "Mara's map", "Stolen."),
                               _thread("thread:b", "Mara's map", "Traced at the docks."))
    assert equal["title_equal"]
    assert similarity.admitted_by(equal) == "lexical"

    boat, lantern = _boat(), _lantern()
    near = {boat.text: [1.0, 0.0], lantern.text: [0.4, math.sqrt(1 - 0.16)]}
    sig = similarity.with_cosine(similarity.lexical(boat, lantern), boat, lantern, near)
    assert sig["cosine"] == 0.4
    assert sig["actors"] == [MARA]
    assert sig["tokens"] < similarity.WEAK_TOKEN and sig["chars"] < similarity.WEAK_CHAR
    assert similarity.admitted_by(sig) == "semantic"

    alone_a = _thread("thread:a", "Mara's boat", "Mara patched the hull of her boat.")
    alone_b = _thread("thread:b", "Winifred's lantern", "Winifred lit the lantern.")
    close = {alone_a.text: [1.0, 0.0], alone_b.text: [0.9, math.sqrt(1 - 0.81)]}
    sig = similarity.with_cosine(similarity.lexical(alone_a, alone_b), alone_a, alone_b, close)
    assert sig["cosine"] == 0.9
    assert sig["actors"] == [] and sig["scenes"] == [] and sig["anchors"] == []
    assert sig["tokens"] < similarity.WEAK_TOKEN and sig["chars"] < similarity.WEAK_CHAR
    assert similarity.admitted_by(sig) == "semantic"


def test_with_cosine_leaves_none_when_a_vector_is_missing_or_off_width():
    a = _thread("thread:a", "Mara's map", "Stolen.")
    b = _thread("thread:b", "Winifred's chart", "Lost.")
    sig = similarity.lexical(a, b)
    assert similarity.with_cosine(sig, a, b, {a.text: [1.0, 0.0]})["cosine"] is None
    off = {a.text: [1.0, 0.0], b.text: [1.0, 0.0, 0.0]}
    assert similarity.with_cosine(sig, a, b, off)["cosine"] is None


LEDGER = ("Find the ledger", "Winifred learned the harbour ledger exists.")


def test_top_k_keeps_the_three_best_plausible_and_never_pads():
    subject = _thread("proposed:x", *LEDGER)
    plausible = [
        _thread("thread:e", "Find the ledger", "Mara burned the ledger."),
        _thread("thread:d", "Find the harbour ledger", "Winifred learned the ledger exists."),
        _thread("thread:c", "Recover the harbour ledger",
                "Winifred went looking for the harbour ledger."),
        _thread("thread:b", "The harbour ledger", "Winifred heard of a ledger."),
        _thread("thread:a", "The missing ledger", "Winifred asked about the harbour ledger."),
    ]
    sigs = {c.ref: similarity.lexical(subject, c) for c in plausible}
    assert all(similarity.plausible(s) for s in sigs.values())
    expected = sorted(sigs, key=lambda ref: similarity.rank_key(sigs[ref], ref))[:3]
    got = similarity.nearest(subject, plausible)
    assert len(got) == 3
    assert [s.ref for s, _ in got] == expected
    assert expected == ["thread:e", "thread:d", "thread:b"]   # the title-equal one leads
    assert all(sig["via"] == "lexical" for _, sig in got)

    implausible = [
        _thread("thread:f", "The Saltmarch tithe", "The guild demanded Mara pay the Saltmarch tithe."),
        _thread("thread:g", "Mara's boat", "Mara patched the hull of her boat at the pier."),
        _thread("thread:h", "The coronation", "Seraphine was crowned in the great hall."),
        _thread("thread:i", "Seraphine's oath", "Seraphine knelt before the throne."),
    ]
    for c in implausible:
        sig = similarity.lexical(subject, c)
        assert sig["tokens"] < similarity.WEAK_TOKEN and sig["chars"] < similarity.WEAK_CHAR
    got = similarity.nearest(subject, [plausible[0], *implausible])
    assert [s.ref for s, _ in got] == ["thread:e"]


def test_cross_type_never_enters_nearest():
    subject = _thread("proposed:x", *LEDGER)
    same_title = _commitment("commitment:find-the-ledger", "Find the ledger",
                             "Winifred learned the harbour ledger exists.")
    thread = _thread("thread:find-the-ledger", *LEDGER)
    got = similarity.nearest(subject, [same_title, thread])
    assert [s.ref for s, _ in got] == ["thread:find-the-ledger"]


def test_nearest_never_returns_the_subject_itself():
    subject = _thread("thread:find-the-ledger", *LEDGER)
    assert similarity.nearest(subject, [subject]) == []


# ------------------------------------------------------------------- pools


def _chronicle(cid, casts: dict):
    (_root(cid) / "chronicle.json").write_text(json.dumps(
        {sid: {"id": sid, "one_line": "", "cast": cast} for sid, cast in casts.items()}),
        encoding="utf-8")


def test_pool_includes_closed_and_canonicalizes_aliases(cid):
    _chronicle(cid, {S1: ["characters/mara"]})
    store.plot.set_movement(cid, "maras-map", "Mara's map", "open", "Stolen.", S1)
    store.plot.set_movement(cid, "the-debt", "The debt", "closed", "Paid.", S2)
    store.plot.set_movement(cid, "a", "Winifred's chart", "open", "Lost.", S2)
    store.plot.set_movement(cid, "b", "Winifred's chart found", "open", "Found.", S2)
    review.create_alias(cid, "thread:a", "thread:b")
    pool = similarity.pool(cid, "thread")
    refs = [s.ref for s in pool]
    assert refs == sorted(refs)
    assert "thread:b" in refs and "thread:a" not in refs
    by_ref = {s.ref: s for s in pool}
    assert by_ref["thread:the-debt"].live is False
    assert by_ref["thread:maras-map"].live is True
    assert by_ref["thread:maras-map"].actors == frozenset({MARA})
    assert by_ref["thread:maras-map"].scenes == frozenset({S1})
    assert by_ref["thread:maras-map"].kind == "thread"
    assert by_ref["thread:maras-map"].text.startswith("plot thread\nMara's map\n")
    # the merged group's beats are the canonical's identity
    assert "Lost." in by_ref["thread:b"].text and "Found." in by_ref["thread:b"].text


def test_pool_of_commitments_uses_the_commitment_text(cid):
    store.commitments.set_movement(cid, "the-midnight-deadline", "The midnight deadline",
                                   "threat", "open", "midnight", "Mara owes the guild.", S1)
    [only] = similarity.pool(cid, "commitment")
    assert only.ref == "commitment:the-midnight-deadline"
    assert only.text == "threat commitment\nThe midnight deadline\nMara owes the guild.\ndue midnight"


def test_pool_of_an_unreadable_ledger_is_empty(cid):
    store.plot.set_movement(cid, "maras-map", "Mara's map", "open", "Stolen.", S1)
    (_root(cid) / "plot.json").write_text("{ no", encoding="utf-8")
    assert similarity.pool(cid, "thread") == []


def test_anchors_come_from_reviewed_event_links(cid):
    store.plot.set_movement(cid, "x", "Mara's map", "open", "Stolen.", S1)
    store.plot.set_movement(cid, "y", "Winifred's chart", "open", "Lost.", S1)
    eid = store.events.create(cid, "The coronation", "2026-05-13")
    review.create_link(cid, "thread:x", f"event:{eid}", "on")
    by_ref = {s.ref: s for s in similarity.pool(cid, "thread")}
    assert by_ref["thread:x"].anchors == frozenset({f"event:{eid}"})
    assert by_ref["thread:y"].anchors == frozenset()


# ------------------------------------------------------- embedding mechanics
#
# One shared double, `llm_fakes.FakeEmbeddings`, installed over the module's
# `_CLIENT`. The cache namespace is always asked of `embed_space.resolve()`:
# it carries the connection's `rev`, so a literal would quietly stop testing.


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def fake(monkeypatch):
    double = FakeEmbeddings()
    monkeypatch.setattr(similarity, "_CLIENT", double)
    return double


def _configure(depth="2"):
    """Embeddings on, as `test_context_semantic.configure` sets them."""
    conn = llm_connections.create_connection("openai_compatible", "Vectors",
                                             base_url="https://vectors.example/v1",
                                             api_key="sk-x", model="", post_process="none")
    config.write_config(semantic_recall_depth=depth, semantic_recall_threshold="0.4")
    embedding(conn, "embed-1")


def _space():
    return embed_space.resolve()["space"]


def _soon():
    return time.monotonic() + embeddings.TIMEOUT


def test_unconfigured_is_off_and_never_calls(home, fake):
    config.write_config(semantic_recall_depth="0")
    embedding("", "embed-1")
    got = similarity.semantic(["x"], [], deadline=_soon())
    assert got.mode == "off"
    assert got.vectors == {}
    assert fake.calls == []


def test_availability_ignores_recall_depth(home, fake):
    _configure(depth="0")
    assert similarity.available() is not None
    got = similarity.semantic(["x"], [], deadline=_soon())
    assert got.mode == "configured"
    assert fake.calls == [["x"]]
    assert set(got.vectors) == {"x"}


def test_matching_label_and_its_exception_policy(home, monkeypatch):
    assert similarity.matching() == "basic"
    _configure()
    assert similarity.matching() == "semantic"

    def boom(*args, **kwargs):
        raise RuntimeError("garbled config")

    monkeypatch.setattr(embed_space, "resolve", boom)
    assert similarity.matching() == "basic"


def test_required_texts_embed_and_cache(home, fake):
    _configure()
    first = similarity.semantic(["Mara's map", "Saltmarch"], [], deadline=_soon())
    assert fake.calls == [["Mara's map", "Saltmarch"]]
    assert first.embedded == 2
    second = similarity.semantic(["Mara's map", "Saltmarch"], [], deadline=_soon())
    assert fake.calls == [["Mara's map", "Saltmarch"]]          # no second call
    assert second.embedded == 0
    assert second.mode == "configured"
    assert second.vectors == vectors.load(_space(), ["Mara's map", "Saltmarch"])
    assert set(second.vectors) == {"Mara's map", "Saltmarch"}


def test_warm_limit_is_honoured(home, fake):
    _configure()
    warm = [f"neighbour {i}" for i in range(40)]
    similarity.semantic(["Mara's map"], warm, deadline=_soon())
    sent = [t for call in fake.calls for t in call]
    assert sent == ["Mara's map", *warm[:similarity.IDENTITY_WARM_LIMIT]]


def test_semantic_warm_limit_defaults_to_identity(home, monkeypatch):
    _configure()
    first = FakeEmbeddings()
    monkeypatch.setattr(similarity, "_CLIENT", first)
    warm = [f"neighbour {i}" for i in range(40)]
    similarity.semantic([], warm, deadline=_soon())
    assert sum(len(call) for call in first.calls) == similarity.IDENTITY_WARM_LIMIT

    second = FakeEmbeddings()
    monkeypatch.setattr(similarity, "_CLIENT", second)
    wider = [f"Saltmarch errand {i}" for i in range(40)]
    similarity.semantic([], wider, deadline=_soon(), warm_limit=40)
    assert len(second.calls[0]) == 40


def test_semantic_uses_a_given_loaded_map(home, fake, monkeypatch):
    _configure()
    calls = []
    real = vectors.load

    def spy(space, texts):
        calls.append(list(texts))
        return real(space, texts)

    monkeypatch.setattr(vectors, "load", spy)
    got = similarity.semantic(["Mara's map"], [], deadline=_soon(), loaded={})
    assert calls == []
    assert fake.calls == [["Mara's map"]]
    assert set(got.vectors) == {"Mara's map"}

    held = similarity.semantic(["Winifred's chart"], [], deadline=_soon(),
                               loaded={"Winifred's chart": [0.0, 1.0]})
    assert calls == []
    assert fake.calls == [["Mara's map"]]                 # a given vector is not re-embedded
    assert held.vectors["Winifred's chart"] == [0.0, 1.0]


def test_width_mismatch_forgets_and_re_embeds_off_width_vectors(home, fake):
    _configure()
    n = "Winifred's chart"
    vectors.save(_space(), n, [1.0, 0.0, 0.0])
    got = similarity.semantic(["Mara's map"], [n], deadline=_soon())
    assert fake.calls[0] == ["Mara's map"]
    assert fake.calls[1] == [n]
    assert len(got.vectors[n]) == 2
    assert len(vectors.load(_space(), [n])[n]) == 2
    assert got.mode == "configured"


def test_mixed_width_fresh_chunks_are_filtered_and_forgotten(home, monkeypatch):
    _configure()
    monkeypatch.setattr(embeddings, "BATCH", 2)
    wide = {"Saltmarch tithe"}
    double = FakeEmbeddings(vector_for=lambda t: [0.0, 0.0, 1.0] if t in wide else [1.0, 0.0])
    monkeypatch.setattr(similarity, "_CLIENT", double)
    texts = ["Mara's map", "Winifred's chart", "Saltmarch tithe"]
    got = similarity.semantic(texts, [], deadline=_soon())
    assert double.calls == [texts[:2], texts[2:]]
    cache = vectors.load(_space(), texts)
    assert set(got.vectors) == {"Mara's map", "Winifred's chart"}
    assert set(cache) == {"Mara's map", "Winifred's chart"}
    assert len(double.calls) == 2                    # a fresh off-width text is not re-asked


def test_off_width_cached_only_text_is_forgotten_not_embedded(home, fake):
    _configure()
    n = "Winifred's chart"
    vectors.save(_space(), n, [1.0, 0.0, 0.0])
    got = similarity.semantic(["Mara's map"], [], deadline=_soon(), cached=[n])
    assert n not in got.vectors
    assert vectors.load(_space(), [n]) == {}
    assert all(n not in call for call in fake.calls)


def test_cached_vectors_are_read_for_texts_never_embedded(home, fake):
    _configure()
    n = "The coronation"
    vectors.save(_space(), n, [0.0, 1.0])
    got = similarity.semantic(["Mara's map"], [], deadline=_soon(), cached=[n])
    assert got.vectors[n] == pytest.approx([0.0, 1.0])
    assert fake.calls == [["Mara's map"]]


def test_fully_cached_run_uses_the_most_common_loaded_width(home, fake):
    _configure()
    space = _space()
    vectors.save(space, "a", [1.0, 0.0])
    vectors.save(space, "b", [0.0, 1.0])
    vectors.save(space, "c", [1.0, 0.0, 0.0])
    got = similarity.semantic(["a"], ["b"], deadline=None, cached=["c"])
    assert set(got.vectors) == {"a", "b"}
    assert set(vectors.load(space, ["a", "b", "c"])) == {"a", "b"}
    assert fake.calls == []


def test_reference_width_ties_and_sources():
    assert similarity.reference_width([], {}) is None
    assert similarity.reference_width([[1.0, 0.0, 0.0]], {"a": [1.0, 0.0], "b": [0.0, 1.0]}) == 3
    assert similarity.reference_width([], {"a": [1.0], "b": [1.0, 0.0]}) == 1
    assert similarity.reference_width([[1.0, 0.0], [1.0], [0.0, 1.0]], {}) == 2


def test_partial_batch_failure_keeps_earlier_chunks(home, monkeypatch):
    _configure()
    monkeypatch.setattr(embeddings, "BATCH", 2)
    double = FakeEmbeddings(error=embeddings.EmbeddingsError("network", "down"), fail_after=1)
    monkeypatch.setattr(similarity, "_CLIENT", double)
    texts = ["Mara's map", "Winifred's chart", "Saltmarch tithe", "The coronation", "Realm"]
    got = similarity.semantic(texts, [], deadline=_soon())
    assert got.mode == "failure"
    assert got.error == "network"
    assert set(got.vectors) == set(texts[:2])
    assert set(vectors.load(_space(), texts)) == set(texts[:2])


def test_a_wrong_length_reply_is_a_bad_response(home, monkeypatch):
    _configure()

    class Short(FakeEmbeddings):
        def embed(self, texts, model, key, base_url, deadline=None, usage=None, *, options=None,
            queries=0):
            return super().embed(texts, model, key, base_url, deadline, usage,
                                 options=options, queries=queries)[:-1]

    monkeypatch.setattr(similarity, "_CLIENT", Short())
    got = similarity.semantic(["Mara's map", "Realm"], [], deadline=_soon())
    assert got.mode == "failure"
    assert got.error == "bad_response"
    assert got.vectors == {}


def test_embedding_failure_mode_on_oserror(home, monkeypatch):
    _configure()
    monkeypatch.setattr(similarity, "_CLIENT", FakeEmbeddings(error=OSError("reset")))
    got = similarity.semantic(["Mara's map"], [], deadline=_soon())
    assert got.mode == "failure"
    assert got.error == "network"
    assert got.vectors == {}


def test_deadline_is_shared_and_bounded(home, monkeypatch):
    _configure()
    monkeypatch.setattr(embeddings, "BATCH", 1)
    double = FakeEmbeddings()
    monkeypatch.setattr(similarity, "_CLIENT", double)
    n = "Winifred's chart"
    vectors.save(_space(), n, [1.0, 0.0, 0.0])        # forces the step-7 second call
    similarity.semantic(["Mara's map", "Realm"], [n], deadline=similarity.deadline(None))
    assert len(double.deadlines) == 3
    assert len(set(double.deadlines)) == 1
    assert double.deadlines[0] <= time.monotonic() + embeddings.TIMEOUT


def test_each_chunk_files_one_continuity_similarity_row(home, fake, monkeypatch):
    """Every chunk is one `embed_sync` call, so one row, charged to the campaign
    and scene it was embedded for (slice D)."""
    _configure()
    monkeypatch.setattr(embeddings, "BATCH", 2)
    space = embed_space.endpoint()
    similarity.embed_missing(space, ["Mara's map", "Winifred's chart", "Realm"],
                             deadline=_soon(), campaign="saltmarch", scene="s1")
    assert len(fake.calls) == 2
    rows = list(usage.calls(days=1))
    assert len(rows) == 2
    for row in rows:
        assert (row["task"], row["campaign"], row["scene"]) == (
            "continuity-similarity", "saltmarch", "s1")


@pytest.fixture
def debug_log():
    logs.forget_file_sizes()
    logs.apply_level("debug")
    yield
    logs.forget_file_sizes()
    logs.apply_level("info")


def test_the_capture_counts_hits_and_misses(home, fake, debug_log):
    _configure()
    vectors.save(_space(), "Mara's map", [1.0, 0.0])
    similarity.semantic(["Mara's map", "Realm"], [], deadline=_soon())
    [line] = [r for r in logs.read(level="debug")["rows"] if r.get("module") == "embed"]
    assert (line["cached"], line["uncached"], line["inputs"]) == (1, 1, 1)


def test_a_continuity_deadline_cut_is_its_runs_budget(home, monkeypatch):
    """Every continuity deadline is its run's budget (absorb's, the sweep's
    `EmbedBudget`), so a request it cuts is `aborted` with nothing in the
    error store -- the caller decides, and says, whether that was a failure."""
    _configure()
    monkeypatch.setattr(similarity, "_CLIENT", FakeEmbeddings(error=embeddings.EmbeddingsError(
        "network", "embeddings response exceeded 30.0s", code=embeddings.DEADLINE)))
    got = similarity.semantic(["Realm"], [], deadline=_soon(), campaign="realm")
    assert got.mode == "failure"
    [row] = usage.calls(days=1)
    assert row["status"] == "aborted"
    assert logs.read(level="error")["rows"] == []


def test_a_runs_counts_are_on_its_first_line_only(home, fake, debug_log, monkeypatch):
    """Every chunk of a run is its own call and line, but the run's hits and
    misses are one count: only the first line carries them."""
    _configure()
    monkeypatch.setattr(embeddings, "BATCH", 1)
    vectors.save(_space(), "Mara's map", [1.0, 0.0])
    similarity.semantic(["Mara's map", "Realm", "Winifred's chart"], [], deadline=_soon())
    lines = [r for r in reversed(logs.read(level="debug")["rows"])
             if r.get("module") == "embed"]
    assert len(lines) == 2
    assert (lines[0]["cached"], lines[0]["uncached"]) == (1, 2)
    assert "cached" not in lines[1] and "uncached" not in lines[1]


def test_a_fully_cached_continuity_run_files_no_row(home, fake, debug_log):
    _configure()
    for text in ("Mara's map", "Realm"):
        vectors.save(embed_space.resolve()["space"], text, [1.0, 0.0])
    similarity.semantic(["Mara's map", "Realm"], [], deadline=_soon())
    assert fake.calls == []
    assert list(usage.calls(days=1)) == []
    assert [r for r in logs.read(level="debug")["rows"] if r.get("module") == "embed"] == []


def test_deadline_helper():
    assert similarity.deadline(None) <= time.monotonic() + embeddings.TIMEOUT
    assert similarity.deadline(None) > time.monotonic()
    assert similarity.deadline(0) is None
    assert similarity.deadline(-1) is None
    before = time.monotonic()
    got = similarity.deadline(5)
    assert before + 5 - 0.5 <= got <= time.monotonic() + 5
    assert similarity.deadline(embeddings.TIMEOUT * 10) <= time.monotonic() + embeddings.TIMEOUT


def test_no_deadline_means_cache_only(home, fake):
    _configure()
    vectors.save(_space(), "Mara's map", [1.0, 0.0])
    got = similarity.semantic(["Mara's map", "Realm"], [], deadline=None)
    assert fake.calls == []
    assert got.mode == "configured"
    assert set(got.vectors) == {"Mara's map"}


def test_nearest_uses_cosine_when_both_vectors_present(home, monkeypatch):
    _configure()
    a = _thread("proposed:x", "Mara's map", "Stolen at dawn.")
    b = _thread("thread:b", "Winifred's chart", "Lost overboard.")
    sig = similarity.lexical(a, b)
    assert sig["tokens"] == 0 and sig["chars"] < similarity.WEAK_CHAR
    near = {a.text: [1.0, 0.0], b.text: [0.9, math.sqrt(1 - 0.81)]}
    monkeypatch.setattr(similarity, "_CLIENT", FakeEmbeddings(vector_for=lambda t: near[t]))
    got = similarity.semantic([a.text], [b.text], deadline=_soon())
    [(other, signals)] = similarity.nearest(a, [b], got.vectors)
    assert other.ref == "thread:b"
    assert signals["cosine"] == pytest.approx(0.9, abs=1e-3)
    assert signals["via"] == "semantic"


# ------------------------------------------------- one normalization per record


def test_lexical_normalizes_each_text_once(monkeypatch):
    """§25.2, Decision 6: a subject's token and trigram sets are built once, on
    its first comparison, so scoring every pair of a pool costs one
    normalization per record rather than two per pair -- and the signals are
    the ones the primitives give, pair for pair."""
    subjects = [_thread(f"thread:mara-s-errand-{i}", f"Mara's errand {i}",
                        f"Mara crossed Saltmarch market on day {i}.",
                        "The Realm road was flooded.")
                for i in range(6)]
    pairs = list(itertools.combinations(subjects, 2))
    assert len(pairs) == 15

    def expected(a, b):
        body_a, body_b = similarity.body(a.text), similarity.body(b.text)
        return {
            "title_equal": similarity.titles_equal(a.title, b.title),
            "slug_equal": similarity.slug_equal(a.title, b.title),
            "tokens": round(similarity.jaccard(similarity.tokens(body_a),
                                               similarity.tokens(body_b)), 4),
            "chars": round(similarity.jaccard(similarity.trigrams(body_a),
                                              similarity.trigrams(body_b)), 4),
            "cosine": None,
            "actors": sorted(a.actors & b.actors),
            "scenes": sorted(a.scenes & b.scenes),
            "anchors": sorted(a.anchors & b.anchors),
        }

    reference = [expected(a, b) for a, b in pairs]
    calls = {"tokens": 0, "trigrams": 0}
    for name in calls:
        real = getattr(similarity, name)

        def counted(text, _real=real, _name=name):
            calls[_name] += 1
            return _real(text)

        monkeypatch.setattr(similarity, name, counted)

    first = [similarity.lexical(a, b) for a, b in pairs]
    assert calls == {"tokens": 6, "trigrams": 6}
    again = [similarity.lexical(a, b) for a, b in pairs]
    assert calls == {"tokens": 6, "trigrams": 6}
    assert first == again == reference


def test_continuity_embeds_documents_only(home, fake):
    """Record against record is symmetric (01h §3.1): both sides documents, so
    the vectors recall, search, art and continuity share by text stay shared."""
    _configure()
    similarity.semantic(["Mara's map", "Saltmarch"], [], deadline=_soon())
    assert fake.queries and all(q == 0 for q in fake.queries)
