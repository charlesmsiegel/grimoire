"""`store.semsearch` — the semantic half of the search surface (#34).

The vector stack (provider, cache, cosine) was built for context retrieval and
is tested in `test_context_semantic.py`. What this file pins down is the part
that is new: the same corpus the keyword sweep walks, cut into passages,
scored against an embedded query, and — when the endpoint is not there — a
refusal the caller can degrade on rather than an error the reader sees.
"""

from __future__ import annotations

import pytest

from grimoire.embeddings import EmbeddingsError
from grimoire.store import (campaigns, characters, config, entities, llm_connections,
                            scenes, search, semsearch, worlds)

# Vectors are 2-D and hand-picked so a similarity is readable at the call site:
# the query points at [1, 0], so a document at [1, 0] scores 1.0 and one at
# [0, 1] scores 0.0.
NEAR = [1.0, 0.0]
MID = [0.8, 0.6]
FAR = [0.0, 1.0]


class FakeProvider:
    """Answers by the first marker word a text contains.

    Keyed on markers rather than on whole texts because a document is embedded
    as passages with its name prefixed, so the exact string the provider sees
    is this module's business and not the test's.
    """

    def __init__(self, rules=(), error=None):
        self.rules = list(rules)
        self.error = error
        self.calls: list[list[str]] = []

    def vector(self, text: str) -> list[float]:
        low = text.casefold()
        for marker, vector in self.rules:
            if marker in low:
                return list(vector)
        return list(FAR)

    def embed(self, texts, model, key, base_url, deadline=None):
        self.calls.append(list(texts))
        if self.error is not None:
            raise self.error
        return [self.vector(t) for t in texts]


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def provider(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(semsearch, "_CLIENT", fake)
    return fake


def configure(kind="openai_compatible", model="embed-1",
              base_url="https://vectors.example/v1", connection=True):
    """Point the store at an embeddings endpoint. Deliberately does NOT set
    `semantic_recall_depth`: recall is a separate feature with its own switch,
    and search must not need it turned on."""
    cid = ""
    if connection:
        cid = llm_connections.create_connection(kind, "Vectors", base_url=base_url,
                                                api_key="sk-x", model="", post_process="none")
    config.write_config(embeddings_model=model, embeddings_connection_id=cid)
    return cid


@pytest.fixture
def world(home):
    wid = worlds.create_world("Realm")
    return wid, worlds.world_root(wid)


def run(q, rounds=1, **kw):
    """Search `rounds` times, returning the last result.

    The warm window is a proper subset of what is uncached, so a corpus is not
    fully indexed by its first query — the same arrangement `semantic.recall`
    has, and for the same reason. A test about *scoring* warms first.
    """
    out = None
    for _ in range(rounds):
        out = semsearch.search_semantic(q, **kw)
    return out


# ---- passages -------------------------------------------------------------

def test_a_transcript_is_cut_at_its_post_markers():
    """Every passage of a transcript starts a post — a speaker's turn is never
    split across two passages by the byte bound alone, and never begins
    mid-sentence."""
    text = " ".join(f"**Speaker{n}:** " + ("salt " * 60) for n in range(12))
    out = semsearch.passages(text)
    assert len(out) > 1
    assert all(p.startswith("**Speaker") for p in out)


def test_short_posts_are_merged_rather_than_embedded_one_line_at_a_time():
    text = "**Seraphine:** The tide is late. **Mara:** It always is."
    assert semsearch.passages(text) == [text]


def test_no_passage_exceeds_the_byte_bound_however_long_one_post_is():
    text = "**Seraphine:** " + ("salt " * 2000)
    out = semsearch.passages(text)
    assert len(out) > 1
    assert all(len(p.encode("utf-8")) <= semsearch.PASSAGE_BYTES for p in out)


def test_prose_with_no_posts_is_split_on_word_boundaries():
    text = " ".join(f"word{n}" for n in range(2000))
    out = semsearch.passages(text)
    assert len(out) > 1
    assert all(len(p.encode("utf-8")) <= semsearch.PASSAGE_BYTES for p in out)
    # Nothing is lost and nothing is cut mid-word.
    assert " ".join(out) == text


def test_empty_text_has_no_passages():
    assert semsearch.passages("   ") == []


# ---- availability ---------------------------------------------------------

def test_an_unconfigured_store_refuses_rather_than_erroring(home, provider):
    with pytest.raises(semsearch.Unavailable):
        semsearch.search_semantic("brine")


def test_a_connection_that_serves_no_embeddings_route_is_unavailable(home, provider):
    configure(kind="openrouter")
    with pytest.raises(semsearch.Unavailable):
        semsearch.search_semantic("brine")


def test_search_does_not_need_recall_turned_on(world, provider):
    """`semantic_recall_depth` gates the context builder's second stage, not
    this. A store that has never enabled recall can still search."""
    _, root = world
    configure()
    entities.create_entity(root, "lore", "The Salt Pact", body="Debts written in brine.")
    assert config.read_config().get("semantic_recall_depth", "0") in ("", "0")
    assert semsearch.search_semantic("brine")["corpus"] > 0


def test_a_dead_endpoint_is_unavailable_not_an_exception(world, provider):
    _, root = world
    configure()
    entities.create_entity(root, "lore", "The Salt Pact", body="Debts written in brine.")
    provider.error = EmbeddingsError("network", "down")
    with pytest.raises(semsearch.Unavailable):
        semsearch.search_semantic("brine")


# ---- ranking --------------------------------------------------------------

def test_records_are_ranked_by_similarity_to_the_query(world, provider):
    _, root = world
    configure()
    entities.create_entity(root, "lore", "The Salt Pact", body="Debts written in brine.")
    entities.create_entity(root, "lore", "The Middle Road", body="A road of gravel.")
    entities.create_entity(root, "locations", "Far Hall", body="Nothing to do with it.")
    provider.rules = [("brine", NEAR), ("gravel", MID)]
    out = run("brine", rounds=3)
    assert [h["id"] for h in out["hits"]] == ["the-salt-pact", "the-middle-road"]
    assert out["hits"][0]["score"] > out["hits"][1]["score"]


def test_a_document_below_the_floor_is_not_a_hit(world, provider):
    _, root = world
    configure()
    entities.create_entity(root, "lore", "Far Hall", body="Nothing to do with it.")
    provider.rules = [("brine", NEAR)]
    out = run("brine", rounds=3)
    assert out["hits"] == [] and out["total"] == 0


def test_a_hit_carries_the_same_shape_a_keyword_hit_does(world, provider):
    wid, root = world
    configure()
    entities.create_entity(root, "lore", "The Salt Pact", body="Debts written in brine.")
    provider.rules = [("brine", NEAR)]
    hit = run("brine", rounds=3)["hits"][0]
    assert set(hit) == {"scope", "root", "root_name", "kind", "id", "sub",
                        "name", "score", "snippet"}
    assert (hit["scope"], hit["root"], hit["root_name"], hit["kind"]) == \
        ("world", wid, "Realm", "lore")
    assert "brine" in hit["snippet"]


def test_a_long_record_is_scored_by_its_closest_passage(world, provider):
    """The whole point of cutting a transcript up: the passage that answers the
    query is what the record is ranked on, not the average of everything else
    it happens to say."""
    _, root = world
    configure()
    wid, _ = world
    cid = campaigns.create_campaign("The Long Run", wid)
    sid = scenes.create_scene(cid, "A long night")
    scenes.append_message(cid, sid, "user", "gravel " * 400)
    scenes.append_message(cid, sid, "assistant", "brine " * 10)
    provider.rules = [("brine", NEAR), ("gravel", MID)]
    out = run("brine", rounds=4, scope="campaign")
    scene_hits = [h for h in out["hits"] if h["kind"] == "scenes"]
    assert scene_hits and scene_hits[0]["score"] == pytest.approx(1.0, abs=1e-6)


# ---- coverage and warming -------------------------------------------------

def test_coverage_is_reported_and_grows_with_repeat_queries(world, provider,
                                                            monkeypatch):
    _, root = world
    configure()
    monkeypatch.setattr(semsearch, "WARM_LIMIT", 1)
    for n in range(4):
        entities.create_entity(root, "lore", f"Entry {n}", body=f"brine number {n}")
    provider.rules = [("brine", NEAR)]
    first = semsearch.search_semantic("brine")
    second = semsearch.search_semantic("brine")
    assert first["corpus"] >= 4
    assert first["indexed"] < first["corpus"]
    assert second["indexed"] > first["indexed"]


def test_a_warmed_corpus_stops_asking_the_provider_for_documents(world, provider):
    _, root = world
    configure()
    entities.create_entity(root, "lore", "The Salt Pact", body="Debts written in brine.")
    provider.rules = [("brine", NEAR)]
    run("brine", rounds=4)
    provider.calls.clear()
    out = semsearch.search_semantic("brine")
    assert out["indexed"] == out["corpus"]
    # One request, carrying the query alone: nothing left to warm.
    assert len(provider.calls) == 1 and len(provider.calls[0]) == 1


# ---- filters --------------------------------------------------------------

def test_the_kind_filter_and_its_facets_behave_like_keyword_search(world, provider):
    _, root = world
    configure()
    entities.create_entity(root, "lore", "The Salt Pact", body="Debts written in brine.")
    entities.create_entity(root, "locations", "The Brine Steps", body="Steps in brine.")
    provider.rules = [("brine", NEAR)]
    out = run("brine", rounds=3, kinds=("lore",))
    assert [h["kind"] for h in out["hits"]] == ["lore"]
    # Facets count what dropping the filter would find, exactly as #33's do.
    assert out["facets"]["locations"] == 1 and out["facets"]["lore"] == 1
    assert out["total"] == 1


def test_an_unknown_scope_or_kind_is_refused_the_way_keyword_search_refuses_it(
        world, provider):
    configure()
    with pytest.raises(search.BadScope):
        semsearch.search_semantic("brine", scope="nowhere")
    with pytest.raises(search.BadKind):
        semsearch.search_semantic("brine", kinds=("nonsense",))


def test_a_blank_query_is_an_empty_result_not_a_provider_call(world, provider):
    configure()
    out = semsearch.search_semantic("   ")
    assert out["hits"] == [] and out["total"] == 0
    assert provider.calls == []


# ---- the corpus this surface covers ---------------------------------------

def test_character_cards_and_taglines_are_in_the_semantic_corpus(world, provider):
    """#34's second item: user search wants more than the entries the context
    builder embeds. It gets the whole keyword corpus, characters included."""
    _, root = world
    cid, _ = characters.create_character(root, "Seraphine")
    configure()
    characters.create_version(root, cid, "veiled",
                              {"data": {"name": "Seraphine", "description": "Counts in brine."}})
    provider.rules = [("brine", NEAR)]
    out = run("brine", rounds=3)
    assert any(h["kind"] == "characters" and h["id"] == cid for h in out["hits"])


def test_a_vector_that_could_not_be_saved_is_not_counted_as_indexed(world, provider,
                                                                    monkeypatch):
    """Scoring reads the cache back off disk rather than keeping what it just
    embedded — a 1536-float vector costs forty times the passage it stands for,
    so a query must not hold the whole corpus's worth. The visible consequence:
    a store that cannot be written to reports no coverage rather than one
    query's worth of it."""
    _, root = world
    configure()
    entities.create_entity(root, "lore", "The Salt Pact", body="Debts written in brine.")
    provider.rules = [("brine", NEAR)]
    monkeypatch.setattr(semsearch.vectors, "save", lambda *a, **kw: None)
    out = semsearch.search_semantic("brine")
    assert out["corpus"] > 0 and out["indexed"] == 0 and out["hits"] == []


def test_the_tail_of_a_long_transcript_is_searchable_not_silently_dropped(world, provider,
                                                                          monkeypatch):
    """A per-record passage cap was a coverage lie: `corpus` counted what
    survived it, so a page could say "indexed 40 of 40" while half of every
    long scene had never been in the corpus at all. And it saved nothing —
    `passages` cuts the whole text either way, so the cap only ever discarded
    work already done."""
    _, root = world
    wid, _ = world
    configure()
    cid = campaigns.create_campaign("The Long Run", wid)
    sid = scenes.create_scene(cid, "A very long night")
    for _ in range(40):
        scenes.append_message(cid, sid, "user", "gravel " * 250)
    scenes.append_message(cid, sid, "assistant", "brine at the very end")
    provider.rules = [("brine", NEAR)]
    monkeypatch.setattr(semsearch, "WARM_LIMIT", 10_000)
    out = run("brine", rounds=2, scope="campaign")
    assert [h["kind"] for h in out["hits"] if h["kind"] == "scenes"] == ["scenes"]
    assert out["indexed"] == out["corpus"]


def test_a_snippet_from_inside_a_record_says_it_is_from_inside(world, provider):
    """A passage from post 40 rendered exactly like a passage from post 1, so
    every semantic hit read as the opening of the record it came from."""
    wid, _ = world
    configure()
    cid = campaigns.create_campaign("The Long Run", wid)
    sid = scenes.create_scene(cid, "A long night")
    scenes.append_message(cid, sid, "user", "gravel " * 400)
    scenes.append_message(cid, sid, "assistant", "brine at the end")
    provider.rules = [("brine", NEAR), ("gravel", MID)]
    out = run("brine", rounds=3, scope="campaign")
    hit = next(h for h in out["hits"] if h["kind"] == "scenes")
    assert hit["snippet"].startswith("…")
