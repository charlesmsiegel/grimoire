"""Per-(provider, model) facts: the file beside a connection, and its guards."""

from __future__ import annotations

import json
import threading

import pytest

from grimoire import wire
from grimoire.store import llm_connections
from grimoire.store.inference import facts


@pytest.fixture()
def conn(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = llm_connections.create_connection("openai_compatible", "Saltmarch Local",
                                            base_url="http://localhost:1234/v1")
    return cid, llm_connections.read_connection_raw(cid)["rev"]


def _ok(at: str = "2026-10-07T00:00:00Z") -> dict:
    return {"ok": True, "at": at}


def test_facts_path_sits_beside_the_connection(conn, tmp_path):
    cid, _ = conn
    assert llm_connections.facts_path(cid) == tmp_path / "llm_connections" / f"{cid}.facts.json"


def test_an_unknown_model_has_the_empty_shape(conn):
    cid, rev = conn
    assert facts.of(cid, "mara-7b", rev) == {
        "vision": "", "prefill": None, "post_process": "", "rates": None,
        "verified": {}, "overrides": {}, "context_window": None, "max_output": None,
        "embedding": None,
    }
    assert facts.read(cid) == {}


def test_round_trip(conn):
    cid, rev = conn
    facts.record_verified(cid, "mara-7b", rev, {"vision": _ok(), "embed": {
        "ok": False, "at": "2026-10-07T00:00:01Z", "error": "no such endpoint"}})
    facts.set_overrides(cid, "mara-7b", {"prefill": "yes", "structured_output": "no"})
    got = facts.of(cid, "mara-7b", rev)
    assert got["verified"] == {
        "vision": _ok(),
        "embed": {"ok": False, "at": "2026-10-07T00:00:01Z", "error": "no such endpoint"},
    }
    assert got["overrides"] == {"prefill": "yes", "structured_output": "no"}
    assert set(facts.read(cid)) == {"mara-7b"}


def test_recording_again_merges_results_under_one_rev(conn):
    cid, rev = conn
    facts.record_verified(cid, "m", rev, {"vision": _ok()})
    facts.record_verified(cid, "m", rev, {"embed": _ok(), "vision": {"ok": False, "at": "t"}})
    assert facts.of(cid, "m", rev)["verified"] == {
        "vision": {"ok": False, "at": "t"}, "embed": _ok()}


def test_verified_from_another_rev_is_hidden_and_replaced(conn):
    cid, rev = conn
    assert facts.record_verified(cid, "m", rev, {"vision": _ok()}) is True
    assert facts.of(cid, "m", "some-other-rev")["verified"] == {}
    # Recording under a new rev starts over rather than blending two eras.
    llm_connections.update_connection(cid, base_url="http://localhost:5678/v1")
    new = llm_connections.read_connection_raw(cid)["rev"]
    assert new != rev
    assert facts.record_verified(cid, "m", new, {"embed": _ok()}) is True
    assert facts.of(cid, "m", new)["verified"] == {"embed": _ok()}
    assert facts.of(cid, "m", rev)["verified"] == {}


def test_a_rev_that_is_not_the_connections_own_writes_nothing(conn):
    """The compare and the write are one step in the store: results probed
    under a rev the connection has moved past describe another endpoint, and
    writing them would replace what the current rev holds."""
    cid, rev = conn
    llm_connections.update_connection(cid, base_url="http://localhost:5678/v1")
    new = llm_connections.read_connection_raw(cid)["rev"]
    assert facts.record_verified(cid, "m", new, {"embed": _ok()}) is True
    before = llm_connections.facts_path(cid).read_text(encoding="utf-8")

    assert facts.record_verified(cid, "m", rev, {"vision": {"ok": False, "at": "t"}}) is False
    assert facts.record_verified(cid, "m", "never-a-rev", {"vision": _ok()}) is False
    assert llm_connections.facts_path(cid).read_text(encoding="utf-8") == before
    assert facts.of(cid, "m", new)["verified"] == {"embed": _ok()}


def test_a_connection_that_is_gone_gets_no_facts_file(conn):
    cid, rev = conn
    llm_connections.delete_connection(cid)
    assert facts.record_verified(cid, "m", rev, {"vision": _ok()}) is False
    assert not llm_connections.facts_path(cid).exists()
    assert facts.record_verified("nobody-here", "m", rev, {"vision": _ok()}) is False
    assert not llm_connections.facts_path("nobody-here").exists()


def test_a_rev_change_does_not_hide_what_the_user_stated(conn):
    cid, _ = conn
    facts.set_overrides(cid, "m", {"vision": "yes"})
    p = llm_connections.facts_path(cid)
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["m"].update({"vision": "on", "prefill": True, "post_process": "strict",
                     "rates": {"prompt_usd_per_1k": 1.0, "completion_usd_per_1k": 2.0}})
    p.write_text(json.dumps(doc), encoding="utf-8")
    got = facts.of(cid, "m", "a-different-rev")
    assert got["overrides"] == {"vision": "yes"}
    assert (got["vision"], got["prefill"], got["post_process"], got["rates"]) == (
        "on", True, "strict",
        {"prompt_usd_per_1k": 1.0, "completion_usd_per_1k": 2.0})


def test_set_overrides_replaces_and_empty_clears(conn):
    cid, rev = conn
    facts.set_overrides(cid, "m", {"vision": "yes", "prefill": "no"})
    facts.set_overrides(cid, "m", {"embed": "yes"})
    assert facts.of(cid, "m", rev)["overrides"] == {"embed": "yes"}
    facts.set_overrides(cid, "m", {})
    assert facts.of(cid, "m", rev)["overrides"] == {}


def test_set_overrides_keeps_the_rest_of_the_model_and_other_models(conn):
    cid, rev = conn
    facts.record_verified(cid, "a", rev, {"vision": _ok()})
    facts.record_verified(cid, "b", rev, {"embed": _ok()})
    facts.set_overrides(cid, "a", {"prefill": "yes"})
    assert facts.of(cid, "a", rev)["verified"] == {"vision": _ok()}
    assert facts.of(cid, "b", rev)["verified"] == {"embed": _ok()}


@pytest.mark.parametrize("bad", [
    {"telepathy": "yes"}, {"vision": "maybe"}, {"vision": True}, {"vision": ""},
])
def test_set_overrides_refuses_unknown_names_and_values(conn, bad):
    cid, _ = conn
    with pytest.raises(ValueError):
        facts.set_overrides(cid, "m", bad)
    assert facts.read(cid) == {}


def test_record_verified_refuses_unknown_capabilities(conn):
    cid, rev = conn
    with pytest.raises(ValueError):
        facts.record_verified(cid, "m", rev, {"telepathy": _ok()})
    assert facts.read(cid) == {}


@pytest.mark.parametrize("raw", [
    "", "{not json", "[]", "3", '"x"', "null",
    '{"m": 3}', '{"m": []}', '{"m": null}',
])
def test_a_mangled_file_reads_as_empty_and_never_raises(conn, raw):
    cid, rev = conn
    p = llm_connections.facts_path(cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(raw, encoding="utf-8")
    assert facts.read(cid).get("m", {}) == {}
    assert facts.of(cid, "m", rev) == {
        "vision": "", "prefill": None, "post_process": "", "rates": None,
        "verified": {}, "overrides": {}, "context_window": None, "max_output": None,
        "embedding": None,
    }


def test_mangled_fields_inside_a_model_read_as_empty(conn):
    cid, rev = conn
    p = llm_connections.facts_path(cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"m": {
        "vision": 7, "prefill": "yes", "post_process": 3, "rates": [1],
        "verified": {"rev": rev, "caps": {"vision": 5, "embed": _ok(), "telepathy": _ok()}},
        "overrides": {"vision": "maybe", "embed": "no", "telepathy": "yes"},
    }}), encoding="utf-8")
    got = facts.of(cid, "m", rev)
    assert got == {"vision": "", "prefill": None, "post_process": "", "rates": None,
                   "verified": {"embed": _ok()}, "overrides": {"embed": "no"},
                   "context_window": None, "max_output": None, "embedding": None}


@pytest.mark.parametrize("bad", [0, -1, True, 8192.0, "8192", 2**31, None, [1]])
def test_the_stated_limits_are_read_and_a_malformed_one_is_none(conn, bad):
    """01i: the model's size, as the user states it -- read before anything
    can write it, and only as the write keeps it (a positive int below
    `2**31`); anything a hand left there reads as not stated."""
    cid, rev = conn
    p = llm_connections.facts_path(cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"m": {"context_window": 8192, "max_output": 2000},
                             "n": {"context_window": bad, "max_output": bad}}), encoding="utf-8")
    got = facts.of(cid, "m", rev)
    assert (got["context_window"], got["max_output"]) == (8192, 2000)
    # Stated, so a rev that moved does not hide them.
    assert facts.of(cid, "m", "another-rev")["context_window"] == 8192
    bad_view = facts.of(cid, "n", rev)
    assert (bad_view["context_window"], bad_view["max_output"]) == (None, None)


@pytest.mark.parametrize("raw", [
    "", "{not json", '{"other": {"rates": {"prompt_usd_per_1k": 1,}}}', "[]", "null",
    '{"m": 3}',
])
@pytest.mark.parametrize("write", ["overrides", "state", "rates", "verified"])
def test_no_write_replaces_a_file_it_could_not_parse(conn, raw, write):
    """A hand-mangled file reads as nothing stated, but it still holds every
    other model's word -- a trailing comma away from readable. A write merged
    onto that empty read would keep only its own entry, so every write refuses
    (`FactsMangledError`) and the bytes stay as they were."""
    cid, rev = conn
    p = llm_connections.facts_path(cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(raw, encoding="utf-8")
    with pytest.raises(facts.FactsMangledError):
        if write == "overrides":
            facts.set_overrides(cid, "m", {"vision": "yes"})
        elif write == "state":
            facts.state(cid, "m", vision="off")
        elif write == "rates":
            facts.state(cid, "m", rates={"prompt_usd_per_1k": 0.0,
                                         "completion_usd_per_1k": 0.0})
        else:
            facts.record_verified(cid, "m", rev, {"vision": _ok()})
    assert p.read_text(encoding="utf-8") == raw


def test_a_mangled_file_is_refused_on_a_strict_read_and_fail_soft_otherwise(conn):
    cid, rev = conn
    p = llm_connections.facts_path(cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    assert facts.of(cid, "m", rev)["overrides"] == {}
    with pytest.raises(facts.FactsMangledError):
        facts.of(cid, "m", rev, strict=True)


@pytest.mark.parametrize("write", ["state", "verified"])
def test_no_write_replaces_a_file_it_could_not_read(conn, monkeypatch, write):
    """One that could not be READ (a sharing violation, a sync client holding
    it) is refused as well -- rewritten from an empty read, it would hold only
    this write, and every other model's verified results and overrides would
    be gone. It is the transient case, so it is not `FactsMangledError`."""
    from pathlib import Path

    cid, rev = conn
    assert facts.record_verified(cid, "other", rev, {"vision": _ok()})
    p = llm_connections.facts_path(cid)
    before = p.read_bytes()
    real = Path.read_text

    def held(self, *a, **kw):
        if self == p:
            raise PermissionError("held by a sync client")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", held)
    with pytest.raises(facts.FactsUnreadableError) as got:
        if write == "state":
            facts.state(cid, "m", vision="off")
        else:
            facts.record_verified(cid, "m", rev, {"vision": _ok()})
    monkeypatch.setattr(Path, "read_text", real)
    assert not isinstance(got.value, facts.FactsMangledError)
    assert p.read_bytes() == before


@pytest.mark.parametrize("raw", ["", "{not json", "[]", '{"m": 3}'])
def test_the_migrations_copy_refuses_a_mangled_file(conn, raw):
    cid, _ = conn
    p = llm_connections.facts_path(cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(raw, encoding="utf-8")
    with pytest.raises(facts.FactsUnreadableError):
        facts.adopt_legacy(cid, "m", {"vision": "off"})
    assert p.read_text(encoding="utf-8") == raw


def test_the_migrations_copy_states_nothing_without_writing(conn):
    cid, _ = conn
    assert facts.adopt_legacy(cid, "m", {}) == {}
    assert not llm_connections.facts_path(cid).exists()


def test_the_migrations_copy_reports_a_refused_value_and_lands_the_rest(conn):
    cid, rev = conn
    refused = facts.adopt_legacy(cid, "m", {"vision": "off", "post_process": "sideways"})
    assert set(refused) == {"post_process"}
    assert facts.of(cid, "m", rev)["vision"] == "off"


@pytest.mark.parametrize("bad", ["../escape", "a/b", "", "..", "a:b"])
def test_an_unsafe_provider_id(conn, bad):
    _, rev = conn
    assert facts.read(bad) == {}
    assert facts.of(bad, "m", rev)["verified"] == {}
    with pytest.raises(ValueError):
        facts.set_overrides(bad, "m", {"vision": "yes"})
    with pytest.raises(ValueError):
        facts.record_verified(bad, "m", rev, {"vision": _ok()})


def test_deleting_the_connection_removes_its_facts(conn):
    cid, _ = conn
    facts.set_overrides(cid, "m", {"vision": "yes"})
    p = llm_connections.facts_path(cid)
    assert p.exists()
    llm_connections.delete_connection(cid)
    assert not p.exists()


def test_the_file_is_pretty_json_with_a_trailing_newline(conn):
    cid, _ = conn
    facts.set_overrides(cid, "m", {"vision": "yes"})
    text = llm_connections.facts_path(cid).read_text(encoding="utf-8")
    assert text.endswith("}\n") and "\n  " in text


def test_concurrent_recordings_for_different_models_both_land(conn):
    cid, rev = conn
    names = [f"model-{i}" for i in range(24)]
    barrier = threading.Barrier(len(names))
    errors: list[BaseException] = []

    def go(name: str) -> None:
        try:
            barrier.wait()
            facts.record_verified(cid, name, rev, {"vision": _ok()})
        except BaseException as exc:  # noqa: BLE001 -- surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=go, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert set(facts.read(cid)) == set(names)
    assert all(facts.of(cid, n, rev)["verified"] == {"vision": _ok()} for n in names)


def test_clearing_overrides_on_an_unknown_model_leaves_no_entry(conn):
    cid, _ = conn
    facts.set_overrides(cid, "ghost", {})
    assert facts.read(cid) == {}


# ---- 01i: the model's size, stated ----
def _sizes(cid: str, rev: str, model: str = "m") -> tuple:
    got = facts.of(cid, model, rev)
    return got["context_window"], got["max_output"]


def test_a_stated_limit_is_set_removed_and_left(conn):
    cid, rev = conn
    facts.state(cid, "m", context_window=8192, max_output=2000)
    assert _sizes(cid, rev) == (8192, 2000)
    facts.state(cid, "m", vision="on")                    # None leaves both
    assert _sizes(cid, rev) == (8192, 2000)
    facts.state(cid, "m", max_output=0)                   # 0 removes one
    assert _sizes(cid, rev) == (8192, None)
    assert "max_output" not in facts.read(cid)["m"]
    facts.state(cid, "m", context_window=0)
    assert facts.read(cid) == {"m": {"vision": "on"}}


@pytest.mark.parametrize("bad", ["8192", True, False, -1, 2**31, 1.5, 8192.0, [8192]])
def test_a_limit_that_is_not_a_positive_int_is_refused_before_the_file(conn, bad):
    cid, _ = conn
    facts.state(cid, "m", context_window=4096)
    before = llm_connections.facts_path(cid).read_bytes()
    for field in ("context_window", "max_output"):
        with pytest.raises(ValueError):
            facts.state(cid, "m", **{field: bad})
    assert llm_connections.facts_path(cid).read_bytes() == before


@pytest.mark.parametrize("first, second", [
    ({"context_window": 8192}, {"max_output": 16000}),
    ({"max_output": 16000}, {"context_window": 8192}),
])
def test_a_max_output_above_the_window_is_refused_on_the_merged_entry(conn, first, second):
    """The check needs the merged entry: the second request states one value
    while the other is already on file."""
    cid, rev = conn
    facts.state(cid, "m", **first)
    before = llm_connections.facts_path(cid).read_bytes()
    with pytest.raises(ValueError, match="window"):
        facts.state(cid, "m", **second)
    assert llm_connections.facts_path(cid).read_bytes() == before
    with pytest.raises(ValueError):
        facts.state(cid, "n", context_window=4096, max_output=4097)
    assert llm_connections.facts_path(cid).read_bytes() == before
    # Equal is not above; and a value that only disagrees with no stated
    # partner is allowed.
    facts.state(cid, "n", context_window=4096, max_output=4096)
    assert _sizes(cid, rev, "n") == (4096, 4096)


def test_a_stated_limit_survives_a_rev_change(conn):
    cid, rev = conn
    facts.state(cid, "m", context_window=32768)
    llm_connections.update_connection(cid, api_key="sk-new")
    moved = llm_connections.read_connection_raw(cid)["rev"]
    assert moved != rev
    assert _sizes(cid, moved) == (32768, None)


def test_a_hand_edited_size_pair_does_not_refuse_an_unrelated_write(conn):
    """The size check judges a write that states a size; a file a hand left
    inconsistent still takes a vision change, and reads its sizes as stated."""
    cid, rev = conn
    p = llm_connections.facts_path(cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"m": {"context_window": 4096, "max_output": 8192}}),
                 encoding="utf-8")
    facts.state(cid, "m", vision="on")
    assert facts.of(cid, "m", rev)["vision"] == "on"
    with pytest.raises(ValueError):
        facts.state(cid, "m", context_window=4000)
    facts.state(cid, "m", max_output=0)
    assert _sizes(cid, rev) == (4096, None)


def test_removing_an_unstated_limit_leaves_no_entry(conn):
    cid, _ = conn
    facts.state(cid, "ghost", context_window=0, max_output=0)
    assert facts.read(cid) == {}


# ---- embedding options (01h-S2) ----

NOMIC_BLOCK = {"input": "prefix", "query_prefix": "search_query: ",
               "document_prefix": "search_document: "}
QWEN_QUERY = ("Instruct: Given a passage of a story, retrieve the lore, records or "
              "images it concerns\nQuery: ")


def test_embedding_options_are_a_stated_fact(conn):
    from grimoire import wire

    cid, rev = conn
    facts.state(cid, "embed-1", embedding={**NOMIC_BLOCK, "colour": "red"})
    assert facts.read(cid)["embed-1"]["embedding"] == NOMIC_BLOCK   # unknown key dropped
    assert facts.embed_options(facts.of(cid, "embed-1", rev)) == wire.EmbedOptions(
        input="prefix", query_prefix="search_query: ", document_prefix="search_document: ")
    # A stated fact survives a rev restamp.
    llm_connections.update_connection(cid, api_key="sk-new")
    new_rev = llm_connections.read_connection_raw(cid)["rev"]
    assert new_rev != rev
    assert facts.of(cid, "embed-1", new_rev)["embedding"] == NOMIC_BLOCK


def test_an_embedding_write_of_none_or_an_empty_block(conn):
    cid, _ = conn
    facts.state(cid, "embed-1", embedding=NOMIC_BLOCK)
    facts.state(cid, "embed-1", prefill=True)                 # leaves the block
    assert facts.read(cid)["embed-1"]["embedding"] == NOMIC_BLOCK
    facts.state(cid, "embed-1", embedding={})
    assert "embedding" not in facts.read(cid)["embed-1"]
    facts.state(cid, "embed-1", embedding=NOMIC_BLOCK)
    facts.state(cid, "embed-1", embedding={"input": "none"}, prefill=False)
    assert "embedding" not in facts.read(cid)["embed-1"]
    facts.state(cid, "embed-2", embedding=NOMIC_BLOCK)
    facts.state(cid, "embed-2", embedding={})
    assert "embed-2" not in facts.read(cid)                   # an empty entry is dropped


def test_a_qwen_style_prefix_with_a_newline_is_kept(conn):
    cid, _ = conn
    block = {"input": "prefix", "query_prefix": QWEN_QUERY, "document_prefix": "doc:\t"}
    facts.state(cid, "embed-1", embedding=block)
    assert facts.read(cid)["embed-1"]["embedding"] == block


@pytest.mark.parametrize(("block", "message"), [
    ("prefix", facts.EMBED_NOT_OBJECT),
    ([], facts.EMBED_NOT_OBJECT),
    ({"input": "both"}, facts.EMBED_BAD_INPUT),
    ({"input": "prefix", "document_prefix": 5}, facts.EMBED_NOT_TEXT),
    ({"input": "prefix"}, facts.EMBED_NO_PREFIX),
    ({"input": "prefix", "query_prefix": "", "document_prefix": ""}, facts.EMBED_NO_PREFIX),
    ({"input": "prefix", "document_prefix": "x" * 201}, facts.EMBED_TOO_LONG),
    ({"input": "prefix", "document_prefix": "a\0b"}, facts.EMBED_CONTROL),
    ({"input": "prefix", "document_prefix": "a\x1bb"}, facts.EMBED_CONTROL),
    ({"input": "prefix", "document_prefix": "a\x85b"}, facts.EMBED_CONTROL),
    ({"input": "prefix", "document_prefix": "a\ud800b"}, facts.EMBED_CONTROL),
    ({"document_prefix": "passage: "}, facts.EMBED_WRONG_MODE),
    ({"input": "none", "query_prefix": "q: "}, facts.EMBED_WRONG_MODE),
    ({"input": "prefix", "document_prefix": "p: ", "param_field": "task"},
     facts.EMBED_WRONG_MODE),
    ({"dimensions_field": "model"}, facts.EMBED_RESERVED),
    ({"dimensions_field": "a.b"}, facts.EMBED_BAD_FIELD),
    ({"dimensions_field": "Field"}, facts.EMBED_BAD_FIELD),
    ({"dimensions_field": "f" * 42}, facts.EMBED_BAD_FIELD),
    ({"input": "param", "param_field": "input_type", "query_value": "query"},
     facts.EMBED_NO_PARAM),
    ({"input": "param", "query_value": "query", "document_value": "document"},
     facts.EMBED_NO_PARAM),
    ({"input": "param", "param_field": "model", "query_value": "q",
      "document_value": "d"}, facts.EMBED_RESERVED),
    ({"input": "param", "param_field": "dimensions", "query_value": "q",
      "document_value": "d", "dimensions": 512}, facts.EMBED_SAME_FIELD),
    ({"dimensions": 512.0}, facts.EMBED_BAD_DIMENSIONS),
    ({"dimensions": True}, facts.EMBED_BAD_DIMENSIONS),
    ({"dimensions": "512"}, facts.EMBED_BAD_DIMENSIONS),
    ({"dimensions": 0}, facts.EMBED_BAD_DIMENSIONS),
    ({"dimensions": 32769}, facts.EMBED_BAD_DIMENSIONS),
])
def test_invalid_embedding_options_are_refused_before_writing(conn, block, message):
    cid, _ = conn
    facts.state(cid, "embed-1", prefill=True)
    before = llm_connections.facts_path(cid).read_bytes()
    with pytest.raises(ValueError) as got:
        facts.state(cid, "embed-1", embedding=block)
    assert str(got.value) == message
    assert llm_connections.facts_path(cid).read_bytes() == before


def test_a_lone_dimensions_field_is_checked_and_dropped(conn):
    cid, _ = conn
    facts.state(cid, "embed-1", embedding={**NOMIC_BLOCK, "dimensions_field": "output_dimension"})
    assert facts.read(cid)["embed-1"]["embedding"] == NOMIC_BLOCK


@pytest.mark.parametrize(("block", "stored", "options"), [
    ({"input": "param", "param_field": "input_type", "query_value": "query",
      "document_value": "document"},
     {"input": "param", "param_field": "input_type", "query_value": "query",
      "document_value": "document"},
     wire.EmbedOptions(input="param", param_field="input_type", query_value="query",
                       document_value="document")),
    ({"dimensions": 512},
     {"dimensions": 512}, wire.EmbedOptions(dimensions=512)),
    ({"input": "prefix", "document_prefix": "passage: ", "dimensions": 256,
      "dimensions_field": "output_dimension"},
     {"input": "prefix", "document_prefix": "passage: ", "dimensions": 256,
      "dimensions_field": "output_dimension"},
     wire.EmbedOptions(input="prefix", document_prefix="passage: ", dimensions=256,
                       dimensions_field="output_dimension")),
    ({"input": "param", "param_field": "task", "query_value": "retrieval.query",
      "document_value": "retrieval.passage", "dimensions": 32768, "dimensions_field": None},
     {"input": "param", "param_field": "task", "query_value": "retrieval.query",
      "document_value": "retrieval.passage", "dimensions": 32768},
     wire.EmbedOptions(input="param", param_field="task", query_value="retrieval.query",
                       document_value="retrieval.passage", dimensions=32768)),
])
def test_the_request_field_and_dimensions_save(conn, block, stored, options):
    cid, rev = conn
    facts.state(cid, "embed-1", embedding=block)
    assert facts.read(cid)["embed-1"]["embedding"] == stored
    assert facts.embed_options(facts.of(cid, "embed-1", rev)) == options


@pytest.mark.parametrize("block", [
    {"input": "param", "param_field": "input_type", "query_value": "query"},
    {"dimensions": 512.0},
    "prefix",
    {"input": "prefix"},
])
def test_an_invalid_block_on_disk_is_named_by_its_reader(conn, block):
    cid, rev = conn
    p = llm_connections.facts_path(cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"embed-1": {"embedding": block}}), encoding="utf-8")
    known = facts.of(cid, "embed-1", rev)
    assert known["embedding"] == block
    with pytest.raises(ValueError):
        facts.embed_options(known)


def test_no_block_reads_as_no_options(conn):
    cid, rev = conn
    assert facts.embed_options(facts.of(cid, "embed-1", rev)) is None


def test_adopted_strict_raises_on_a_held_file(conn, monkeypatch):
    from pathlib import Path

    cid, rev = conn
    facts.state(cid, "m", prefill=True)
    p = llm_connections.facts_path(cid)
    real = Path.read_text

    def held(self, *a, **kw):
        if self == p:
            raise PermissionError("held by a sync client")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", held)
    with pytest.raises(facts.FactsUnreadableError):
        facts.adopted(cid, "m", rev, adopting="m", stated={}, strict=True)
    assert facts.adopted(cid, "m", rev, adopting="m", stated={})["prefill"] is None
