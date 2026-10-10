"""Embedding options in the space identity (roadmap 01h-S2; spec 01h §3, §5).

What this suite holds:

* **Golden identity.** `EmbedOptions.canonical()` is the document side only,
  in one exact text, and its digest is pinned. These literals were computed
  outside the code under test and are never regenerated to make a change
  pass (the `test_lore_golden.py` rule): a new encoding takes a new tag.
* **Resolution.** No block keeps today's space id byte for byte; stated
  options move it to `\\0embopt1:<digest>`; the endpoint dict carries the very
  object its space was computed from; an invalid block, or a facts file that
  cannot be read, names no space -- never the option-less base space.
* **The Embedding card** says why options turned embedding off.

Every provider, name and key below is invented.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from grimoire import wire
from grimoire.store import config, embed_space, llm_connections, pricing
from grimoire.store import inference_keys as keys
from grimoire.store.inference import facts
from grimoire.store.inference import resolve as inference_resolve

EmbedOptions = wire.EmbedOptions

NOMIC = EmbedOptions(input="prefix", query_prefix="search_query: ",
                     document_prefix="search_document: ")
NOMIC_BLOCK = {"input": "prefix", "query_prefix": "search_query: ",
               "document_prefix": "search_document: "}
NOMIC_DIGEST = "b61a0b1188d1b0bd3a7cf05b26a81c2a"
BGE_QUERY = "Represent this sentence for searching relevant passages: "


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


# ---- golden identity -----------------------------------------------------------

@pytest.mark.parametrize("options", [
    EmbedOptions(),
    EmbedOptions(input="prefix", query_prefix=BGE_QUERY),
    EmbedOptions(input="prefix", document_prefix=""),
    EmbedOptions(dimensions_field="output_dimension"),
    EmbedOptions(input="param", param_field="input_type", query_value="query"),
    EmbedOptions(document_prefix="passage: "),
])
def test_default_options_are_canonically_empty(options):
    assert options.canonical() == "{}"
    assert options.is_default()


@pytest.mark.parametrize(("options", "canonical", "digest"), [
    (NOMIC, '{"doc_prefix":"search_document: "}', NOMIC_DIGEST),
    (EmbedOptions(input="prefix", document_prefix="passage: "),
     '{"doc_prefix":"passage: "}', "83408df40675d61e692471b86d88967a"),
    (EmbedOptions(input="prefix", document_prefix="title: none | text: "),
     '{"doc_prefix":"title: none | text: "}', "d986480efdf605a08dc9ab99cb458464"),
    (EmbedOptions(input="prefix", document_prefix="Résumé: "),
     '{"doc_prefix":"R\\u00e9sum\\u00e9: "}', "64e976ded517afb132256ac894a35643"),
    (EmbedOptions(input="param", param_field="input_type", query_value="query",
                  document_value="document"),
     '{"doc_value":"document","field":"input_type"}', "e2d2833fd99bb8fddea27a204553e640"),
    (EmbedOptions(dimensions=512),
     '{"dim":512,"dim_field":"dimensions"}', "18bd39256d9c7a2f22f990839a43d09d"),
    (EmbedOptions(input="prefix", document_prefix="passage: ", dimensions=256,
                  dimensions_field="output_dimension"),
     '{"dim":256,"dim_field":"output_dimension","doc_prefix":"passage: "}',
     "4dec4044ecf44b2072468492a38f06b9"),
    (EmbedOptions(input="param", param_field="task", query_value="retrieval.query",
                  document_value="retrieval.passage", dimensions=1024),
     '{"dim":1024,"dim_field":"dimensions","doc_value":"retrieval.passage","field":"task"}',
     "9a09c1a1eecdb603e79ed3cd1f3f947b"),
])
def test_the_canonical_text_is_pinned(options, canonical, digest):
    assert options.canonical() == canonical
    assert options.digest() == digest
    assert not options.is_default()


def test_a_query_side_change_never_moves_the_digest():
    other = dataclasses.replace(NOMIC, query_prefix="query: ")
    assert other.canonical() == NOMIC.canonical() and other.digest() == NOMIC_DIGEST
    param = EmbedOptions(input="param", param_field="input_type", query_value="query",
                         document_value="document")
    assert dataclasses.replace(param, query_value="search").digest() == param.digest()


def test_options_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        NOMIC.input = "none"  # type: ignore[misc]
    assert wire.Target("p", "openai_compatible", "m").embed_options is None


def test_space_of_is_todays_string_for_default_options():
    conn = {"id": "saltmarch-vectors", "rev": "r1"}
    base = "saltmarch-vectors\0r1\0m"
    assert inference_resolve.space_of(conn, "m") == base
    assert inference_resolve.space_of(conn, "m", EmbedOptions()) == base
    assert inference_resolve.space_of(
        conn, "m", EmbedOptions(input="prefix", query_prefix=BGE_QUERY)) == base
    assert inference_resolve.space_of(conn, "m", NOMIC) == f"{base}\0embopt1:{NOMIC_DIGEST}"


# ---- resolution ----------------------------------------------------------------

def _provider(name: str = "Saltmarch Vectors") -> str:
    return llm_connections.create_connection(
        "openai_compatible", name, base_url="https://vectors.example/v1",
        api_key="sk-fake-0001", model="", post_process="none")


def _role(conn: str, model: str = "embed-1") -> None:
    config.write_config(**{keys.FORMAT_KEY: "2",
                           keys.role_key("embedding", "provider"): conn,
                           keys.role_key("embedding", "model"): model})


def _rev(conn: str) -> str:
    return llm_connections.read_connection_raw(conn)["rev"]


def _hand_write(conn: str, doc: object) -> None:
    import json
    path = llm_connections.facts_path(conn)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _hold(monkeypatch, conn: str) -> None:
    """`conn`'s facts file held by a sync client: every read of it raises."""
    path = llm_connections.facts_path(conn)
    real = Path.read_text

    def held(self, *a, **kw):
        if self == path:
            raise OSError("held by a sync client")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", held)


def test_no_block_keeps_todays_space():
    conn = _provider()
    _role(conn)
    got = embed_space.endpoint()
    assert got is not None
    assert got["space"] == f"{conn}\0{_rev(conn)}\0embed-1"
    assert got["options"] == EmbedOptions()
    # Other facts on the model, and a block on ANOTHER model, move nothing.
    facts.state(conn, "embed-1", prefill=True, context_window=8192,
                rates={pricing.PROMPT: 0.1, pricing.COMPLETION: 0.0})
    facts.state(conn, "embed-2", embedding=NOMIC_BLOCK)
    assert embed_space.endpoint()["space"] == f"{conn}\0{_rev(conn)}\0embed-1"


def test_stated_options_move_the_space_and_ride_the_endpoint():
    conn = _provider()
    _role(conn)
    facts.state(conn, "embed-1", embedding=NOMIC_BLOCK)
    got = embed_space.endpoint()
    assert got is not None
    assert got["space"] == f"{conn}\0{_rev(conn)}\0embed-1\0embopt1:{NOMIC_DIGEST}"
    assert got["options"] == NOMIC
    assert got["options"] is got["target"].embed_options


def test_a_query_side_only_block_keeps_the_base_space():
    conn = _provider()
    _role(conn)
    facts.state(conn, "embed-1", embedding={"input": "prefix", "query_prefix": BGE_QUERY})
    got = embed_space.endpoint()
    assert got["space"] == f"{conn}\0{_rev(conn)}\0embed-1"
    assert got["options"].query_prefix == BGE_QUERY


@pytest.mark.parametrize("block", [
    {"input": "param", "param_field": "input_type", "query_value": "query",
     "document_value": "document"},
    7,
    {"input": "prefix"},
])
def test_an_invalid_block_names_no_space(block):
    conn = _provider()
    _role(conn)
    _hand_write(conn, {"embed-1": {"embedding": block}})
    assert embed_space.endpoint() is None
    got = embed_space.resolution()
    assert got is not None and got.space_id is None
    assert got.embed_options_problem == inference_resolve.OPTIONS_INVALID
    assert embed_space.options_problem() == "invalid"


def test_an_unreadable_facts_file_names_no_space(monkeypatch):
    conn = _provider()
    _role(conn)
    facts.state(conn, "embed-1", embedding=NOMIC_BLOCK)
    _hold(monkeypatch, conn)
    assert embed_space.endpoint() is None
    assert embed_space.resolution().embed_options_problem == inference_resolve.OPTIONS_HELD


def test_a_mangled_facts_file_names_no_space():
    conn = _provider()
    _role(conn)
    path = llm_connections.facts_path(conn)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{", encoding="utf-8")
    assert embed_space.endpoint() is None
    assert embed_space.resolution().embed_options_problem == inference_resolve.OPTIONS_MANGLED


def test_chat_targets_read_options_fail_soft():
    conn = _provider()
    _role(conn)
    facts.state(conn, "embed-1", embedding=NOMIC_BLOCK)
    raw = llm_connections.read_connection_raw(conn)
    target = inference_resolve.target_for(
        raw, "embed-1", dict(inference_resolve.NO_SAMPLING),
        model_facts=inference_resolve.facts_for(raw, "embed-1"))
    assert target.embed_options == NOMIC
    _hand_write(conn, {"embed-1": {"embedding": {"input": "both"}}})
    target = inference_resolve.target_for(
        raw, "embed-1", dict(inference_resolve.NO_SAMPLING),
        model_facts=inference_resolve.facts_for(raw, "embed-1"))
    assert target.embed_options is None


def test_strict_options_for_the_probe(monkeypatch):
    conn = _provider()
    facts.state(conn, "embed-1", embedding=NOMIC_BLOCK)
    raw = llm_connections.read_connection_raw(conn)
    assert inference_resolve.strict_embed_options(raw, "embed-1")[1:] == (NOMIC, "")
    _hold(monkeypatch, conn)
    assert inference_resolve.strict_embed_options(raw, "embed-1")[1:] == (
        None, inference_resolve.OPTIONS_HELD)
