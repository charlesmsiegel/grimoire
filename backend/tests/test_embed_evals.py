"""The embedding evals (roadmap 01h-C6, slice 01h-S7), offline, as part of
the ordinary test run: the corpus keeps its rules, recorded rankings replay
and grade, a planted miss is flagged, the whole embedded path runs end to end
against a deterministic offline endpoint (options, the `param` split and the
dimensions check included), and a live run files its rows in its isolate and
never in the real ledger. Live runs cost money and are never a test.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evals import run as eval_run  # noqa: E402
from evals.embed import corpus as embed_corpus  # noqa: E402
from evals.embed import harness, metrics  # noqa: E402
from grimoire import embeddings, wire  # noqa: E402
from grimoire.store import logs, usage  # noqa: E402

CORPUS = embed_corpus.load()


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    logs.forget_file_sizes()
    yield tmp_path
    logs.forget_file_sizes()


# ---- the corpus ------------------------------------------------------------------

def test_the_corpus_keeps_its_rules():
    assert embed_corpus.problems(CORPUS) == []
    # At least ten documents per unit of the largest k, every query ranking
    # the whole pool, and every shape asked.
    assert len(CORPUS.documents) >= 10 * max(metrics.KS)
    assert {q.shape for q in CORPUS.queries} == set(embed_corpus.SHAPES)


def test_the_rules_catch_a_positive_that_shares_a_word_and_a_decoy_that_shares_none():
    doc = embed_corpus.Document
    bad = embed_corpus.Corpus(
        (doc("p", "recall", "Mara lights lanterns"), doc("x", "recall", "Bread is baked")),
        (embed_corpus.Query("q", "recall", "Mara walks", ("p",), ("x",)),))
    found = embed_corpus.problems(bad)
    assert any("positive p shares ['mara']" in f for f in found)
    assert any("decoy x" in f for f in found)


def test_the_lexical_baseline_cannot_pass():
    """What the decoys are for: a term scorer ranks no paraphrase first."""
    got = metrics.grade(CORPUS, harness.lexical_rankings(CORPUS))
    assert got.whole and got.scores["all"]["R@1"] == 0.0


# ---- grading and replay ----------------------------------------------------------

def test_metrics_on_a_known_ranking():
    q = CORPUS.queries[0]
    [relevant] = q.relevant
    others = [d.id for d in CORPUS.documents if d.id != relevant]
    assert metrics.recall_at([*others[:2], relevant], q.relevant, 1) == 0.0
    assert metrics.recall_at([*others[:2], relevant], q.relevant, 3) == 1.0
    assert metrics.reciprocal_rank([*others[:2], relevant], q.relevant) == pytest.approx(1 / 3)
    assert metrics.reciprocal_rank(others, q.relevant) == 0.0


def test_a_perfect_ranking_scores_one_and_misses_nothing():
    perfect = {q.id: list(q.relevant) for q in CORPUS.queries}
    got = metrics.grade(CORPUS, perfect)
    assert got.misses == [] and got.whole
    assert all(v == 1.0 for scores in got.scores.values() for v in scores.values())


def test_a_planted_miss_is_flagged():
    perfect = {q.id: list(q.relevant) for q in CORPUS.queries}
    planted = CORPUS.queries[4]
    perfect[planted.id] = [*planted.decoys, "filler-01"]
    got = metrics.grade(CORPUS, perfect)
    assert got.misses == [planted.id]
    assert got.scores[planted.shape]["R@10"] < 1.0
    assert planted.id in metrics.table([("planted", got)])


def test_an_unanswered_query_and_an_unknown_id_are_not_whole():
    partial = {q.id: list(q.relevant) for q in CORPUS.queries[1:]}
    partial[CORPUS.queries[1].id] = ["no-such-doc"]
    got = metrics.grade(CORPUS, partial)
    assert not got.whole
    assert got.unanswered == [CORPUS.queries[0].id]
    assert got.unknown == [f"{CORPUS.queries[1].id}:no-such-doc"]


def test_every_recording_replays_whole():
    recorded = harness.read_recordings()
    assert recorded, "no recorded rankings to replay"
    for name, rankings in recorded:
        got = metrics.grade(CORPUS, rankings)
        assert got.whole, name


def test_replay_through_the_cli(capsys):
    assert eval_run.main(["--embed"]) == 0
    out = capsys.readouterr().out
    assert "lexical baseline" in out and "bow-1-none" in out


@pytest.mark.parametrize("argv", [["--embed", "--embed-options", "{}"],
                                  ["--embed", "--out", "x.json"],
                                  ["--embed", "--record"],
                                  ["--embed-options", "{}"]])
def test_the_cli_refuses_what_embed_does_not_take(argv):
    with pytest.raises(SystemExit) as got:
        eval_run.main(argv)
    assert got.value.code == 2


# ---- the whole path, offline -----------------------------------------------------

def test_the_offline_path_reproduces_its_recording():
    """The bag-of-words endpoint is deterministic, so its recording is what
    the embedded path ranks today: a change to the corpus or the harness
    re-records it on purpose."""
    seen: list[dict] = []
    got = harness.embedded_rankings(CORPUS, space=harness.bow_space(),
                                    client=harness.bow_client(seen=seen), run_id="eval-run")
    [(_name, recorded)] = [r for r in harness.read_recordings() if r[0] == "bow-1-none"]
    assert got == recorded
    # One call per shape, under its real task, each a row of its own (the
    # search call, which carries the fillers, is split at `BATCH`).
    assert len(seen) == sum(-(-n // embeddings.BATCH) for n in (30, 70, 20))
    rows = list(usage.calls(days=1))
    assert sorted(r["task"] for r in rows) == sorted(t for t, _q in embed_corpus.SHAPES.values())
    assert all(r["run_id"] == "eval-run" for r in rows)


def test_prefixes_reach_every_request():
    seen: list[dict] = []
    options = wire.EmbedOptions(input="prefix", query_prefix="query: ",
                                document_prefix="passage: ")
    harness.embedded_rankings(CORPUS, space=harness.bow_space(options),
                              client=harness.bow_client(seen=seen))
    recall = next(b for b in seen if any(t.startswith("query: ") for t in b["input"]))
    n = sum(1 for q in CORPUS.queries if q.shape == "recall")
    assert all(t.startswith("query: ") for t in recall["input"][:n])
    assert all(t.startswith("passage: ") for t in recall["input"][n:])
    # The symmetric shape sends its reworded records as documents.
    identity = [b for b in seen if all(t.startswith("passage: ") for t in b["input"])]
    assert identity


def test_the_param_split_and_dimensions_end_to_end():
    seen: list[dict] = []
    options = wire.EmbedOptions(input="param", param_field="input_type", query_value="query",
                                document_value="document", dimensions=16)
    got = harness.embedded_rankings(CORPUS, space=harness.bow_space(options),
                                    client=harness.bow_client(seen=seen))
    assert metrics.grade(CORPUS, got).whole
    # Recall and search: a query request then a document request each; identity
    # sends documents only.
    assert [b["input_type"] for b in seen] == ["query", "document", "query", "document",
                                               "document"]
    assert all(b["dimensions"] == 16 for b in seen)
    rows = list(usage.calls(days=1))
    assert len(rows) == 3


def test_a_width_ignoring_endpoint_fails_the_check():
    options = wire.EmbedOptions(dimensions=16)
    with pytest.raises(embeddings.DimensionsMismatchError) as got:
        harness.embedded_rankings(CORPUS, space=harness.bow_space(options),
                                  client=harness.bow_client(honour_dimensions=False))
    assert (got.value.kind, got.value.returned_dims) == ("missing_key", harness.BOW_DIMS)


def test_option_sets_never_share_a_space():
    base = harness.bow_space()
    a = harness.with_options(base, harness.options_of({"input": "prefix",
                                                       "document_prefix": "passage: "}))
    b = harness.with_options(base, harness.options_of({"dimensions": 256}))
    default = harness.with_options(a, wire.EmbedOptions())
    assert len({base["space"], a["space"], b["space"]}) == 3
    assert default["space"] == base["space"]
    assert a["space"] == harness.bow_space(a["options"])["space"]
    with pytest.raises(ValueError):
        harness.options_of({"dimensions": 512.0})


def test_recordings_are_named_by_model_and_option_digest(tmp_path):
    options = wire.EmbedOptions(dimensions=256)
    name = harness.recording_name("Vendor/Embed-Large", options)
    assert name == f"vendor-embed-large-{options.digest()}"
    assert harness.recording_name("m", wire.EmbedOptions()) == "m-none"
    path = harness.write_recording(name, "m", options, {"q": ["a", "b"]}, root=tmp_path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["options"] == {"dim": 256, "dim_field": "dimensions"}
    assert harness.read_recordings(tmp_path) == [(name, {"q": ["a", "b"]})]


# ---- live, against the offline endpoint ------------------------------------------

def test_a_live_run_files_its_rows_in_the_isolate_only(monkeypatch, capsys, _home):
    """The live flow end to end with the endpoint swapped for the offline one:
    the space is resolved once before any isolate, each option set runs in
    its own throwaway home, the report carries its cost, and the real ledger
    stays empty (01a's tripwire)."""
    monkeypatch.setattr(harness, "resolve_live", harness.bow_space)
    monkeypatch.setattr(harness, "live_client", harness.bow_client)
    code = eval_run.main(["--live", "--embed", "--embed-options", "{}",
                          "--embed-options", '{"dimensions": 16}'])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "bytes, about" in out
    assert "bow-1-none" in out and f"bow-1-{wire.EmbedOptions(dimensions=16).digest()}" in out
    assert out.count("calls 3") == 2
    assert list(usage.calls(days=1)) == []           # nothing in the real ledger


def test_an_option_set_is_ranked_beside_the_configured_space(monkeypatch, capsys, _home):
    """`--embed-options` adds a set: the Embedding role's own space still runs
    as the control, and a set naming that same space runs once."""
    monkeypatch.setattr(harness, "resolve_live", harness.bow_space)
    monkeypatch.setattr(harness, "live_client", harness.bow_client)
    code = eval_run.main(["--live", "--embed", "--embed-options", '{"dimensions": 16}'])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "(x2)" in out and out.count("calls 3") == 2
    assert "bow-1-none" in out and f"bow-1-{wire.EmbedOptions(dimensions=16).digest()}" in out
    code = eval_run.main(["--live", "--embed", "--embed-options", "{}"])
    out = capsys.readouterr().out
    assert code == 0 and "(x1)" in out and out.count("calls 3") == 1, out


def test_a_live_run_whose_isolate_is_the_real_home_is_refused(monkeypatch, capsys):
    monkeypatch.setattr(harness, "resolve_live", harness.bow_space)
    sent: list[dict] = []
    monkeypatch.setattr(harness, "live_client", lambda: harness.bow_client(seen=sent))
    monkeypatch.setattr(eval_run, "temp_home", contextlib.nullcontext)
    assert eval_run.main(["--live", "--embed"]) == 1
    assert sent == [] and list(usage.calls(days=1)) == []


def test_temp_home_is_not_the_real_home():
    """The isolate the live run uses really moves the home (a guard on the
    guard above)."""
    before = os.environ["GRIMOIRE_HOME"]
    with eval_run.temp_home() as path:
        assert os.environ["GRIMOIRE_HOME"] == str(path) != before
        assert str(path).startswith(tempfile.gettempdir())
    assert os.environ["GRIMOIRE_HOME"] == before
