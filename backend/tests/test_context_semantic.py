"""Semantic recall behind the `activate` seam (#29, #34).

Two halves, deliberately kept apart:

- `activate`'s *composition* — which entries reach a second stage at all, and
  what happens to what it returns. Tested with a stub strategy and no store,
  because that is the layer where the privacy invariant lives.
- `semantic.recall` itself — configuration, caching, scoring, and the fact
  that every failure it can meet degrades to keyword-only.
"""

import struct

import pytest

from grimoire import embeddings
from grimoire.store import (campaigns, chronicle, config, context as ctx, entities,
                            groupstate, llm_connections, scenes, vectors, worlds)
from grimoire.store.context import semantic, world_state

# --- a fake provider -------------------------------------------------------
#
# Vectors are 2-D and hand-picked so similarity is readable at the call site:
# [1, 0] is the query's direction, so a document at [1, 0] scores 1.0, one at
# [0, 1] scores 0.0, and [0.8, 0.6] scores 0.8.

QUERY = [1.0, 0.0]
NEAR = [1.0, 0.0]
MID = [0.8, 0.6]
FAR = [0.0, 1.0]


class FakeProvider:
    """Answers from a text -> vector map; anything unmapped is orthogonal."""

    def __init__(self, mapping=None, error=None):
        self.mapping = mapping or {}
        self.error = error
        self.calls: list[list[str]] = []

    def embed(self, texts, model, key, base_url, deadline=None):
        self.calls.append(list(texts))
        if self.error is not None:
            raise self.error
        return [list(self.mapping.get(t, FAR)) for t in texts]


@pytest.fixture
def provider(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(semantic, "_CLIENT", fake)
    return fake


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


def configure(depth="2", threshold="0.4", model="embed-1", kind="openai_compatible",
              base_url="https://vectors.example/v1", connection=True):
    """Turn recall on, returning the connection id it was pointed at."""
    cid = ""
    if connection:
        cid = llm_connections.create_connection(kind, "Vectors", base_url=base_url,
                                                api_key="sk-x", model="", post_process="none")
    config.write_config(semantic_recall_depth=depth, semantic_recall_threshold=threshold,
                        embeddings_model=model, embeddings_connection_id=cid)
    return cid


def space():
    """The cache namespace the configured connection produces.

    Asked for rather than spelled out: it carries the connection's `rev`, which
    is restamped on every write, so a literal here would be wrong the moment
    the connection is touched — and wrong in the direction that makes a test
    quietly stop testing anything.
    """
    return semantic.settings()["space"]


def warm(candidates, query, rounds=3):
    """Recall until the cache is warm.

    `_warm_window` is deliberately a proper subset, so the last uncached entry
    waits for the following turn. That costs one extra turn in total, not one
    per entry — but it does mean a test that wants to assert on *scoring* has
    to warm first.
    """
    out = []
    for _ in range(rounds):
        out = semantic.recall(candidates, query)
    return out


def entry(name, keys=(), owners=(), body=None):
    return {"name": name, "keys": list(keys), "owners": list(owners),
            "body": body if body is not None else f"{name} body"}


# --- activate's composition ------------------------------------------------


def test_only_keyword_misses_are_offered_to_the_second_stage():
    seen = {}

    def spy(candidates, recent_text):
        seen["names"] = [c["name"] for c in candidates]
        seen["text"] = recent_text
        return []

    entries = [entry("Keyless"), entry("Hit", keys=["pact"]), entry("Miss", keys=["blade"])]
    world_state.activate(entries, "the pact was sworn", recall=spy)
    assert seen == {"names": ["Miss"], "text": "the pact was sworn"}


def test_owned_lore_whose_owner_is_absent_never_reaches_the_second_stage():
    # The privacy invariant. The stub returns everything it is given, so if the
    # owner gate ran anywhere but before the candidate list, this would leak.
    entries = [entry("Secret", keys=["blade"], owners=["characters:mara"])]
    out = world_state.activate(entries, "nothing relevant here",
                               present=frozenset(), recall=lambda c, t: list(c))
    assert out == []


def test_owned_lore_whose_owner_is_present_can_be_recalled():
    entries = [entry("Secret", keys=["blade"], owners=["characters:mara"])]
    out = world_state.activate(entries, "nothing relevant here",
                               present=frozenset({"characters:mara"}),
                               recall=lambda c, t: list(c))
    assert [e["name"] for e in out] == ["Secret"]


def test_recalled_entries_are_appended_after_the_keyword_ones():
    entries = [entry("Miss", keys=["blade"]), entry("Keyless"), entry("Hit", keys=["pact"])]
    out = world_state.activate(entries, "the pact was sworn", recall=lambda c, t: list(c))
    assert [e["name"] for e in out] == ["Keyless", "Hit", "Miss"]


def test_the_second_stage_is_not_called_when_nothing_missed():
    def boom(candidates, recent_text):  # pragma: no cover - a call here is the failure
        raise AssertionError("no candidates, so no strategy to run")

    entries = [entry("Keyless"), entry("Hit", keys=["pact"])]
    assert world_state.activate(entries, "the pact was sworn", recall=boom) == entries


def test_without_a_strategy_activate_is_exactly_the_keyword_rule():
    entries = [entry("Keyless"), entry("Hit", keys=["pact"]), entry("Miss", keys=["blade"])]
    assert world_state.activate(entries, "the pact was sworn") == [entries[0], entries[1]]


# --- settings resolution ---------------------------------------------------


def test_recall_is_off_by_default(store, provider):
    assert semantic.settings() is None
    assert semantic.recall([entry("Miss", keys=["x"])], "some text") == []
    assert provider.calls == []


@pytest.mark.parametrize("override", [
    {"depth": "0"},                      # the explicit off switch
    {"depth": "-4"},                     # nonsense reads as off, never as unbounded
    {"depth": "many"},                   # hand-edited config.md
    {"model": ""},                       # no embedding model chosen
    {"connection": False},               # no connection chosen
    {"kind": "openrouter"},              # a kind with no /embeddings route
    {"kind": "claude"},
    {"base_url": ""},                    # a custom endpoint with nowhere to point
])
def test_an_incomplete_configuration_leaves_the_layer_off(store, provider, override):
    configure(**override)
    assert semantic.settings() is None
    assert semantic.recall([entry("Miss", keys=["x"])], "some text") == []
    assert provider.calls == []


def test_a_deleted_connection_leaves_the_layer_off(store, provider):
    cid = configure()
    llm_connections.delete_connection(cid)
    assert semantic.settings() is None
    assert semantic.recall([entry("Miss", keys=["x"])], "some text") == []
    assert provider.calls == []


def test_deleting_the_connection_clears_the_reference_to_it(store):
    # Not merely cosmetic: a dangling id leaves the Configuration page showing
    # an endpoint that is gone while recall is silently off, and the slug is
    # reusable, so a later connection created under the same name would
    # inherit the reference.
    cid = configure()
    llm_connections.delete_connection(cid)
    assert config.read_config()["embeddings_connection_id"] == ""


def test_deleting_the_connection_clears_a_fallback_reference_too(store):
    """`fallback_connection_id` names a connection just like the two beside it
    (#144), and a dangling one is worse than the others: the slug is reusable,
    so a later connection created under the same name silently inherits the
    role of "where generation goes when the primary fails"."""
    cid = llm_connections.create_connection("openrouter", "Backup", api_key="k")
    config.write_config(fallback_connection_id=cid)
    llm_connections.delete_connection(cid)
    assert config.read_config()["fallback_connection_id"] == ""


def test_deleting_another_connection_leaves_the_reference_alone(store):
    cid = configure()
    other = llm_connections.create_connection("openai_compatible", "Unrelated",
                                              base_url="https://other/v1")
    llm_connections.delete_connection(other)
    assert config.read_config()["embeddings_connection_id"] == cid


def test_the_cache_namespace_moves_when_the_credential_does(store):
    """A gateway where the *key* selects the tenant needs a new namespace.

    Same URL, same model name, different credential is a different embedding
    space, and matching dimensions hide the mismatch completely — so the
    connection's `rev`, which is restamped on every write, is part of the key.
    """
    cid = configure()
    before = semantic.settings()["space"]
    llm_connections.update_connection(cid, api_key="sk-different")
    assert semantic.settings()["space"] != before


def test_two_connections_to_one_endpoint_do_not_share_a_namespace(store):
    first = configure()
    second = llm_connections.create_connection(
        "openai_compatible", "Same gateway",          # identical URL and model
        base_url="https://vectors.example/v1", api_key="sk-other")
    config.write_config(embeddings_connection_id=first)
    one = semantic.settings()["space"]
    config.write_config(embeddings_connection_id=second)
    assert semantic.settings()["space"] != one


def test_settings_carries_the_connections_endpoint_and_key(store):
    configure()
    got = semantic.settings()
    assert got["base_url"] == "https://vectors.example/v1"
    assert got["key"] == "sk-x"
    assert (got["model"], got["depth"], got["threshold"]) == ("embed-1", 2, 0.4)


@pytest.mark.parametrize("raw,expected", [
    ("0.9", 0.9), ("-1", -1.0), ("1", 1.0),
    ("", 0.4), ("high", 0.4), ("nan", 0.4), ("inf", 0.4), ("2", 0.4), ("-3", 0.4),
])
def test_an_out_of_range_threshold_falls_back_to_the_default(store, raw, expected):
    configure(threshold=raw)
    assert semantic.settings()["threshold"] == expected


# --- scoring ---------------------------------------------------------------


def test_a_candidate_close_to_the_scan_window_is_recalled(store, provider):
    configure()
    near, far = entry("Near"), entry("Far")
    provider.mapping = {"scene text": QUERY, semantic.entry_text(near): NEAR,
                        semantic.entry_text(far): FAR}
    assert [e["name"] for e in warm([near, far], "scene text")] == ["Near"]


def test_candidates_below_the_threshold_are_not_recalled(store, provider):
    configure(threshold="0.9")
    mid = entry("Mid")
    provider.mapping = {"scene text": QUERY, semantic.entry_text(mid): MID}
    assert semantic.recall([mid], "scene text") == []


def test_a_candidate_exactly_at_the_threshold_is_recalled(store, provider):
    configure(threshold="0.8")
    mid = entry("Mid")
    provider.mapping = {"scene text": QUERY, semantic.entry_text(mid): MID}
    assert [e["name"] for e in semantic.recall([mid], "scene text")] == ["Mid"]


def test_hits_come_back_most_similar_first_and_capped_at_depth(store, provider):
    configure(depth="2")
    a, b, c = entry("Mid"), entry("Near"), entry("Nearer")
    provider.mapping = {"scene text": QUERY, semantic.entry_text(a): MID,
                        semantic.entry_text(b): [0.9, 0.436], semantic.entry_text(c): NEAR}
    assert [e["name"] for e in warm([a, b, c], "scene text")] == ["Nearer", "Near"]


def test_equal_scores_break_ties_by_the_order_activate_was_given(store, provider):
    configure(depth="1")
    first, second = entry("First"), entry("Second")
    provider.mapping = {"scene text": QUERY, semantic.entry_text(first): NEAR,
                        semantic.entry_text(second): NEAR}
    assert [e["name"] for e in warm([first, second], "scene text")] == ["First"]


def test_the_embedded_entry_text_carries_name_keys_and_body():
    text = semantic.entry_text(entry("Sablewrought", keys=["blade", "heirloom"],
                                     body="A sword her mother left her."))
    assert text == "Sablewrought\nblade, heirloom\nA sword her mother left her."


def test_a_pathological_body_is_truncated_before_it_is_sent(store, provider):
    configure()
    huge = entry("Huge", body="x" * (semantic.DOC_BYTES * 2))
    semantic.recall([huge], "scene text")
    assert all(len(t.encode("utf-8")) <= semantic.DOC_BYTES for t in provider.calls[0][1:])


def test_a_body_in_a_token_dense_script_is_bounded_by_bytes_not_characters(store, provider):
    # The bound that matters is the provider's token window, and CJK is about
    # one token per character. A character bound of 8000 was an 8000-token
    # input against an 8191-token window -- rejected, uncacheable, and so
    # retried on every turn, taking the whole warm run down with it forever.
    configure()
    semantic.recall([entry("Dense", body="刀" * 8000)], "scene text")
    sent = provider.calls[0][1]
    assert len(sent.encode("utf-8")) <= semantic.DOC_BYTES
    assert "刀" in sent and "\ufffd" not in sent   # cut on a character boundary


def test_the_query_keeps_the_most_recent_end_of_a_long_window(store, provider):
    configure()
    window = "old news. " * semantic.QUERY_BYTES + "THE LATEST TURN"
    semantic.recall([entry("Miss")], window)
    query = provider.calls[0][0]
    assert len(query.encode("utf-8")) <= semantic.QUERY_BYTES
    assert query.endswith("THE LATEST TURN")


def test_an_entry_with_no_text_is_never_embedded(store, provider):
    configure()
    blank = {"name": "", "keys": [], "owners": [], "body": "   "}
    assert semantic.recall([blank], "scene text") == []
    assert provider.calls == []


def test_an_empty_scan_window_makes_no_request(store, provider):
    configure()
    assert semantic.recall([entry("Miss")], "   ") == []
    assert provider.calls == []


# --- caching ---------------------------------------------------------------


def test_entry_vectors_are_cached_and_only_the_query_is_re_embedded(store, provider):
    configure()
    e = entry("Near")
    provider.mapping = {"scene text": QUERY, "later text": QUERY, semantic.entry_text(e): NEAR}
    assert [x["name"] for x in semantic.recall([e], "scene text")] == ["Near"]
    assert provider.calls == [["scene text", semantic.entry_text(e)]]
    assert [x["name"] for x in semantic.recall([e], "later text")] == ["Near"]
    assert provider.calls[1] == ["later text"]


def test_editing_an_entry_re_embeds_only_that_entry(store, provider):
    configure()
    a, b = entry("A"), entry("B")
    provider.mapping = {"scene text": QUERY}
    semantic.recall([a, b], "scene text")
    edited = entry("A", body="rewritten")
    semantic.recall([edited, b], "scene text")
    assert provider.calls[1] == ["scene text", semantic.entry_text(edited)]


def test_a_repeated_entry_text_is_embedded_once(store, provider):
    configure()
    a, b = entry("Twin"), entry("Twin")
    semantic.recall([a, b], "scene text")
    assert provider.calls[0] == ["scene text", semantic.entry_text(a)]


def test_the_query_vector_is_never_written_to_the_cache(store, provider):
    configure()
    e = entry("Near")
    semantic.recall([e], "scene text")
    cached = list((store / ".cache" / "embeddings").glob("*" + vectors.SUFFIX))
    assert len(cached) == 1                  # the entry's, not the window's
    assert vectors.load(space(), ["scene text"]) == {}


# --- failing soft ----------------------------------------------------------


@pytest.mark.parametrize("kind", ["auth", "rate_limit", "network", "bad_response", "missing_key"])
def test_any_provider_error_degrades_to_keyword_only(store, provider, kind):
    configure()
    provider.error = embeddings.EmbeddingsError(kind, "boom")
    assert semantic.recall([entry("Miss")], "scene text") == []


def _poisoned(provider, poison_text, mapping):
    """A provider that refuses any request containing `poison_text`."""
    def embed(texts, model, key, base_url, deadline=None):
        provider.calls.append(list(texts))
        if poison_text in texts:
            raise embeddings.EmbeddingsError("bad_response", "input rejected")
        return [list(mapping.get(t, FAR)) for t in texts]
    provider.embed = embed


def test_a_document_the_provider_refuses_does_not_block_the_others(store, provider):
    """The failure a fixed prefix made permanent, and rotation makes transient.

    A refused document is never cacheable, so under a fixed prefix it sat at
    the head of the work list every turn and nothing behind it ever warmed.
    """
    configure(depth=str(semantic.WARM_LIMIT + 5))
    poison = entry("Poison")
    others = [entry(f"E{n}") for n in range(semantic.WARM_LIMIT + 4)]
    mapping = {semantic.entry_text(e): NEAR for e in others}
    _poisoned(provider, semantic.entry_text(poison), mapping)
    candidates = [poison, *others]

    for turn in range(12):                    # a different scan window each turn
        mapping[f"turn {turn}"] = QUERY
        got = semantic.recall(candidates, f"turn {turn}")
    assert {e["name"] for e in got} == {e["name"] for e in others}


def test_a_zero_vector_from_a_successful_response_does_not_block_the_others(store, provider):
    # No request fails here, so nothing keying off an error could help: the
    # provider answers, the vector is unusable, and it is never cached.
    configure(depth=str(semantic.WARM_LIMIT + 5))
    dud = entry("Dud")
    others = [entry(f"E{n}") for n in range(semantic.WARM_LIMIT + 4)]
    provider.mapping = {semantic.entry_text(e): NEAR for e in others}
    provider.mapping[semantic.entry_text(dud)] = [0.0, 0.0]
    candidates = [dud, *others]

    for turn in range(12):
        provider.mapping[f"turn {turn}"] = QUERY
        got = semantic.recall(candidates, f"turn {turn}")
    assert {e["name"] for e in got} == {e["name"] for e in others}


def test_a_transient_outage_never_rejects_a_document_permanently(store, provider):
    """A 502 during warming must cost a turn, not an entry.

    The tombstone this replaces keyed off `bad_response` -- which is also what
    a 5xx maps to, so a routine transient outage permanently excluded a
    perfectly valid document from every future warm run.
    """
    configure()
    good = entry("Good")
    provider.error = embeddings.EmbeddingsError("bad_response", "502 Bad Gateway")
    assert semantic.recall([good], "scene text") == []

    provider.error = None                     # the server recovers
    provider.mapping = {"scene text": QUERY, semantic.entry_text(good): NEAR}
    assert [e["name"] for e in semantic.recall([good], "scene text")] == ["Good"]


def test_the_warm_window_moves_with_the_scan_window(store):
    texts = [f"doc {n}" for n in range(semantic.WARM_LIMIT * 3)]
    first = semantic._warm_window(texts, "one scene")
    second = semantic._warm_window(texts, "a different scene")
    assert len(first) == len(second) == semantic.WARM_LIMIT
    assert first != second                    # or a stuck entry would stay stuck
    assert semantic._warm_window(texts, "one scene") == first   # deterministic


def test_the_window_is_always_a_proper_subset(store):
    # Taking the whole list once it fits looks harmless and silently undoes the
    # rotation: the window stops varying, so a stuck document is in every
    # window again. Measured -- the tail never converged.
    for size in (2, 3, 10, semantic.WARM_LIMIT + 1):
        texts = [f"doc {n}" for n in range(size)]
        assert len(semantic._warm_window(texts, "q")) == min(semantic.WARM_LIMIT, size - 1)


def test_a_single_entry_is_still_offered(store):
    assert semantic._warm_window(["only"], "anything") == ["only"]


def test_both_embed_calls_share_one_deadline(store, provider):
    # Two calls each starting a fresh TIMEOUT would make recall worth twice
    # what the constant says.
    seen = []
    configure()

    def record(texts, model, key, base_url, deadline=None):
        provider.calls.append(list(texts))
        seen.append(deadline)
        if len(texts) > 1:
            raise embeddings.EmbeddingsError("bad_response", "nope")
        return [QUERY]

    provider.embed = record
    semantic.recall([entry("Miss")], "scene text")
    assert len(seen) == 2 and len(set(seen)) == 1 and seen[0] is not None


def test_a_failed_warm_run_still_scores_what_is_already_cached(store, provider):
    # The complement to the test below: `bad_response` is the one failure a
    # *document* in the batch could have caused, so the query -- which the
    # cached entries need in order to be scored at all -- is worth asking for
    # on its own.
    configure()
    good, poison = entry("Good"), entry("Poison")
    provider.mapping = {"scene text": QUERY, semantic.entry_text(good): NEAR}
    semantic.recall([good], "scene text")     # warm the good one
    _poisoned(provider, semantic.entry_text(poison), provider.mapping)
    assert [e["name"] for e in semantic.recall([good, poison], "scene text")] == ["Good"]
    assert provider.calls[-1] == ["scene text"]      # the retry actually happened


def test_an_unreachable_provider_is_asked_exactly_once(store, provider):
    # `network`/`auth`/`rate_limit` say the endpoint is unavailable, not that
    # the batch was bad, so the same answer is already known to be coming for
    # the query alone. Retrying it would double what every single turn costs
    # while a provider is down -- and lean on one that may be throttling.
    for kind in ("network", "auth", "rate_limit"):
        provider.calls.clear()
        configure()
        provider.error = embeddings.EmbeddingsError(kind, "no")
        assert semantic.recall([entry("Miss")], "scene text") == []
        assert len(provider.calls) == 1, kind


def test_nothing_to_warm_means_no_second_attempt(store, provider):
    configure()
    e = entry("Near")
    provider.mapping = {"scene text": QUERY, semantic.entry_text(e): NEAR}
    semantic.recall([e], "scene text")               # warm it
    provider.error = embeddings.EmbeddingsError("network", "down")
    assert semantic.recall([e], "scene text") == []
    assert len(provider.calls) == 2                  # no isolation retry to make


def test_an_unexpected_provider_exception_is_not_swallowed(store, provider):
    # Fail-soft covers the failures a provider is *expected* to produce. A
    # TypeError from this module's own code is a bug, and hiding it behind a
    # silent keyword fallback is how it would ship unnoticed.
    configure()
    provider.error = TypeError("bug in the client")
    with pytest.raises(TypeError):
        semantic.recall([entry("Miss")], "scene text")


def test_a_zero_query_vector_recalls_nothing(store, provider):
    configure()
    e = entry("Near")
    provider.mapping = {"scene text": [0.0, 0.0], semantic.entry_text(e): NEAR}
    assert semantic.recall([e], "scene text") == []


def test_a_provider_that_returns_too_few_vectors_recalls_nothing(store, provider):
    configure()

    def short(texts, model, key, base_url, deadline=None):
        provider.calls.append(list(texts))
        return [QUERY] * (len(texts) - 1)       # always one short, batch or solo

    provider.embed = short
    assert semantic.recall([entry("Miss")], "scene text") == []


def test_a_candidate_whose_cached_vector_has_another_dimensionality_recovers(store, provider):
    # The endpoint now answers this model id at a different width than the
    # cache was built at. Scoring the overlap would be arithmetic across two
    # unrelated spaces -- but skipping alone is not enough: a stale vector is
    # still a cache HIT, so it would never be re-embedded and the entry would
    # drop out of recall permanently.
    configure()
    e = entry("Near")
    vectors.save(space(), semantic.entry_text(e), [1.0, 0.0, 0.0])
    provider.mapping = {"scene text": QUERY, semantic.entry_text(e): NEAR}
    assert semantic.recall([e], "scene text") == []       # this turn it cannot be scored
    assert provider.calls == [["scene text"]]             # ...and was a cache hit
    assert [x["name"] for x in semantic.recall([e], "scene text")] == ["Near"]  # healed


@pytest.mark.parametrize("rot", [[1e20, 0.0], [0.9, 0.9], [float("nan"), 0.0]])
def test_a_corrupted_vector_cannot_outrank_a_real_hit(store, provider, rot):
    """End to end: a cache file rewritten in place must not rank.

    `[0.9, 0.9]` is the case that broke the earlier reasoning. Its norm is
    1.27, so it projects to 0.9 against the query -- above a genuine 0.8, and
    comfortably inside the [-1, 1] the score check accepts. Integrity has to be
    established from the bytes, not inferred from the score.
    """
    configure(depth="1")
    rotten, good = entry("Rotten"), entry("Good")
    provider.mapping = {"scene text": QUERY, semantic.entry_text(good): MID}
    warm([rotten, good], "scene text")                     # warm both
    path = vectors._path(space(), semantic.entry_text(rotten))
    path.write_bytes(path.read_bytes()[:4] + struct.pack("<2f", *rot))
    assert [e["name"] for e in warm([rotten, good], "scene text")] == ["Good"]


def test_a_cache_file_corrupted_in_place_cannot_outrank_a_real_hit(store, provider):
    # Fixed-width records make a truncated file detectable, but not one whose
    # bytes were rewritten in place: it unpacks cleanly to nonsense, and a
    # non-finite component would score `inf` and beat everything real.
    configure(depth="1")
    rotten, good = entry("Rotten"), entry("Good")
    provider.mapping = {"scene text": QUERY, semantic.entry_text(good): NEAR}
    semantic.recall([rotten, good], "scene text")          # warm both
    path = store / ".cache" / "embeddings"
    for f in path.glob("*" + vectors.SUFFIX):
        if struct.unpack(f"<{f.stat().st_size // 4}f", f.read_bytes()) != tuple(NEAR):
            f.write_bytes(struct.pack("<2f", float("inf"), 0.0))
    assert [e["name"] for e in semantic.recall([rotten, good], "scene text")] == ["Good"]


def test_an_unwritable_cache_still_recalls(store, provider, monkeypatch):
    configure()
    monkeypatch.setattr(vectors, "save", lambda *a, **k: None)
    e = entry("Near")
    provider.mapping = {"scene text": QUERY, semantic.entry_text(e): NEAR}
    assert [x["name"] for x in semantic.recall([e], "scene text")] == ["Near"]


# --- warming the cache is bounded ------------------------------------------


def test_one_turn_embeds_at_most_a_warm_run_and_the_query(store, provider):
    # Switching the layer on over a campaign with hundreds of keyed entries
    # must not become one enormous request with the event loop blocked behind
    # it — and must not lose every vector it computed when the last batch of
    # such a run trips a rate limit.
    configure()
    many = [entry(f"E{n}") for n in range(semantic.WARM_LIMIT + 5)]
    semantic.recall(many, "scene text")
    assert len(provider.calls[0]) == semantic.WARM_LIMIT + 1
    assert len(provider.calls[0]) <= embeddings.BATCH   # one provider round trip


def test_the_cache_warms_to_completion_over_a_few_turns(store, provider):
    configure()
    many = [entry(f"E{n}") for n in range(semantic.WARM_LIMIT + 5)]
    for turn in range(8):
        provider.mapping[f"turn {turn}"] = QUERY
        semantic.recall(many, f"turn {turn}")
    # Every entry is cached, so the last turn asks only for the scan window.
    assert provider.calls[-1] == ["turn 7"]


def test_entries_left_out_of_a_warm_run_are_simply_not_recalled_yet(store, provider):
    configure(depth=str(semantic.WARM_LIMIT + 2))
    many = [entry(f"E{n}") for n in range(semantic.WARM_LIMIT + 2)]
    provider.mapping = {"scene text": QUERY, **{semantic.entry_text(e): NEAR for e in many}}
    first = semantic.recall(many, "scene text")
    assert 0 < len(first) < len(many)                  # a window's worth, not all
    assert len(warm(many, "scene text", rounds=6)) == len(many)


# --- wired into the real context build --------------------------------------


TURN = "She drew the blade her mother left her."
BODY = "The sword her mother left her."


def _campaign():
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "S")
    return campaigns.campaign_root(cid), cid, sid


def _lore_scene(similarity, provider, owners=""):
    """A campaign whose one lore entry is keyed on a word nobody says, with the
    provider primed to score it `similarity` against the turn."""
    croot, cid, sid = _campaign()
    entities.create_entity(croot, "lore", "Sablewrought", BODY, keys="sablewrought",
                           owners=owners)
    scenes.append_message(cid, sid, "user", TURN)
    configure(depth="1")
    provider.mapping = {TURN: QUERY,  # the scan window is the query
                        semantic.entry_text({"name": "Sablewrought",
                                             "keys": ["sablewrought"],
                                             "body": BODY}): similarity}
    return cid, sid


def test_a_missed_lore_entry_reaches_the_built_prompt(store, provider):
    """The end-to-end wiring: `_world_info` -> `activate` -> `semantic.recall`.

    Every other test in this file stubs one side of that chain, so every one of
    them stays green with the strategy unwired. This is the one that fails when
    `_world_info` stops passing it.
    """
    cid, sid = _lore_scene(NEAR, provider)
    # The keyword rule alone cannot reach it — nobody typed "sablewrought".
    assert BODY in ctx.build_messages(cid, sid)[0]["content"]


def test_a_distant_lore_entry_stays_out_of_the_built_prompt(store, provider):
    cid, sid = _lore_scene(FAR, provider)
    assert BODY not in ctx.build_messages(cid, sid)[0]["content"]


def test_recalled_lore_gives_way_before_the_keyword_hits(store, provider):
    """Enabling recall must never REMOVE lore, budget or no budget.

    The packer drops sections whole and takes the largest in a tier first, so
    while recalled entries shared the World info section a recall could grow it
    until the whole thing went -- keyword hits included. They render as their
    own section in the archive tier now, which is the first tier dropped.
    """
    croot, cid, sid = _campaign()
    # The keyword section is deliberately the BIGGER of the two: within one
    # tier the packer drops the largest first, so if these shared a tier the
    # keyword lore would go first and this test would be satisfied by the very
    # arrangement it exists to reject.
    entities.create_entity(croot, "lore", "Keyed", "KEYWORD BODY " * 120, keys="pact")
    entities.create_entity(croot, "lore", "Sablewrought", "RECALLED BODY " * 10,
                           keys="sablewrought")
    scenes.append_message(cid, sid, "user", "they spoke of the pact")
    configure(depth="1")
    provider.mapping = {
        "they spoke of the pact": QUERY,
        semantic.entry_text({"name": "Sablewrought", "keys": ["sablewrought"],
                             "body": "RECALLED BODY " * 10}): NEAR,
    }
    roomy = ctx.build_messages(cid, sid)[0]["content"]
    assert "KEYWORD BODY" in roomy and "RECALLED BODY" in roomy

    # Squeeze by just enough to force ONE drop. `total_tokens` is the packer's
    # own measure of the whole request, so a budget a shade under it is
    # satisfied by dropping the smallest available section — which the tier
    # order, not the size order, decides.
    before = ctx.context_breakdown(cid, sid)
    config.write_config(context_budget=str(before["total_tokens"] - 5))
    tight = ctx.build_messages(cid, sid)[0]["content"]
    assert "KEYWORD BODY" in tight        # never lost to a recall
    assert "RECALLED BODY" not in tight   # the recall gave way first


def test_recalled_lore_gives_way_before_the_earlier_scenes_it_never_replaced(store, provider):
    """Sharing the archive tier was not enough: within a tier the largest goes
    first, so a bigger Earlier scenes section was dropped to keep newly
    recalled lore — swapping context the prompt already had for context it
    never did. Recalled lore has a tier of its own, ahead of every other."""
    croot, cid, sid = _campaign()
    entities.create_entity(croot, "lore", "Sablewrought", "RECALLED BODY " * 10,
                           keys="sablewrought")
    scenes.append_message(cid, sid, "user", "they spoke of the pact")
    # A big archive record, so a tier that sorts by size would drop it first.
    # The id must order before the scene being played (`001--s`), and the recap
    # window has to be out of the way — the archive section deliberately never
    # repeats what the recap already shows.
    chronicle.absorb(cid, {"id": "000--earlier", "one_line": "an earlier scene",
                           "summary": "EARLIER SCENE " * 120, "keywords": ["pact"]})
    config.write_config(recap_depth="0")
    configure(depth="1")
    provider.mapping = {
        "they spoke of the pact": QUERY,
        semantic.entry_text({"name": "Sablewrought", "keys": ["sablewrought"],
                             "body": "RECALLED BODY " * 10}): NEAR,
    }
    roomy = ctx.build_messages(cid, sid)[0]["content"]
    assert "EARLIER SCENE" in roomy and "RECALLED BODY" in roomy

    before = ctx.context_breakdown(cid, sid)
    config.write_config(context_budget=str(before["total_tokens"] - 5))
    tight = ctx.build_messages(cid, sid)[0]["content"]
    assert "EARLIER SCENE" in tight        # was already being sent; still is
    assert "RECALLED BODY" not in tight    # the newcomer gives way first


def test_a_recalled_group_does_not_pull_its_state_into_the_spotlight(store, provider):
    """Group state is a `spotlight` section, so feeding it from recall grows a
    section the packer drops whole — and dropping it would take the states of
    KEYWORD-activated groups with it. Same defect as sharing World info, one
    section over."""
    croot, cid, sid = _campaign()
    gid = entities.create_entity(croot, "groups", "Saltmarch Circle", "A quiet order.",
                                 keys="sablewrought")
    groupstate.write_state(croot, gid, "## Goals\nRECALLED GROUP STATE")
    scenes.append_message(cid, sid, "user", "they spoke of the pact")
    configure(depth="1")
    provider.mapping = {
        "they spoke of the pact": QUERY,
        semantic.entry_text({"name": "Saltmarch Circle", "keys": ["sablewrought"],
                             "body": "A quiet order."}): NEAR,
    }
    prompt = ctx.build_messages(cid, sid)[0]["content"]
    assert "A quiet order." in prompt              # the lore body is recalled
    assert "RECALLED GROUP STATE" not in prompt    # its state is not


def test_an_entry_with_no_body_never_takes_a_recall_slot(store, provider):
    # `entry_text` includes the name, so a bodiless entry is perfectly
    # scorable — and renders nothing, since the template emits the body alone.
    # At depth 1 it would consume the entire recall.
    configure(depth="1")
    empty = entry("Sablewrought", keys=["blade"], body="")
    real = entry("Real", body="ACTUAL CONTENT")
    provider.mapping = {"scene text": QUERY,
                        semantic.entry_text(empty): NEAR,      # scores higher
                        semantic.entry_text(real): MID}
    assert [e["name"] for e in warm([empty, real], "scene text")] == ["Real"]
    assert all(semantic.entry_text(empty) not in call for call in provider.calls)


def test_the_inspector_reports_recalled_lore_as_its_own_section(store, provider):
    cid, sid = _lore_scene(NEAR, provider)
    labels = [s["label"] for s in ctx.context_sections(cid, sid)]
    assert "Recalled lore" in labels


def test_owned_lore_stays_out_of_the_built_prompt_however_similar(store, provider):
    """The privacy invariant, end to end rather than against a stub."""
    cid, sid = _lore_scene(NEAR, provider, owners="characters:mara")
    assert BODY not in ctx.build_messages(cid, sid)[0]["content"]
    # ...and it was never sent to the embeddings provider either.
    assert all(BODY not in text for call in provider.calls for text in call)
