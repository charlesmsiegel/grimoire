"""`store/continuity/candidates.py` -- the derived candidate cache (spec §6).

The cache is rebuildable, so its reader is tolerant all the way down: a file
that does not parse reads as empty, a record of the wrong shape is skipped
while its neighbours survive, and a nested field of the wrong type is
normalized to the shape every downstream consumer indexes without guarding.
Its writer overwrites a malformed file rather than refusing it -- the opposite
of `doc`'s rule, because nothing in this file is a decision a reader made.
"""

import ast
import importlib
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app
from grimoire.store.campaigns import paths as campaigns_paths
from grimoire.store.continuity import candidates


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


def _file(cid):
    return campaigns_paths.campaign_root(cid) / "continuity_candidates.json"


def _proposal(**over):
    base = {"decision": "duplicate", "from": "thread:maras-map",
            "to": "thread:winifreds-chart", "relation": "", "status": "",
            "reason": "Both follow Mara's map.", "evidence_scenes": ["012--the-coronation"]}
    base.update(over)
    return base


def _pair(**over):
    base = {"kind": "possible_duplicate",
            "refs": ["thread:maras-map", "thread:winifreds-chart"],
            "fingerprint": "fp1_pair", "signals": {"title_exact": False},
            "proposal": None, "created": "2026-10-05T00:00:00Z"}
    base.update(over)
    return base


def _closure(**over):
    base = {"kind": "possible_thread_closure", "refs": ["thread:maras-map"],
            "fingerprint": "fp1_life", "signals": {"reason": "stale"},
            "proposal": None, "created": "2026-10-05T00:00:00Z"}
    base.update(over)
    return base


def _store(cid, records, **top):
    data = candidates.empty()
    data.update(top)
    data["records"] = records
    _file(cid).write_text(json.dumps(data), encoding="utf-8")


def test_absent_cache_reads_empty(cid):
    assert candidates.read(cid) == candidates.empty()
    assert candidates.empty() == {
        "version": 1, "questions": 2, "generated": "", "generation": "",
        "basis": {"embedding_space": "", "embedding_model": "",
                  "identity_hashes": {}, "scored": {}, "text_hashes": {}},
        "records": {},
    }
    assert candidates.records(cid) == []
    assert candidates.malformed(cid) is False


def test_kinds_are_the_four_in_spec_order():
    assert candidates.KINDS == ("possible_duplicate", "possible_relation",
                                "possible_thread_closure", "possible_commitment_resolution")
    assert candidates.PAIR_KINDS == ("possible_duplicate", "possible_relation")
    assert candidates.LIFECYCLE_KINDS == ("possible_thread_closure",
                                          "possible_commitment_resolution")


def test_unparseable_cache_reads_empty_and_is_overwritable(cid):
    _file(cid).write_text("{ no", encoding="utf-8")
    assert candidates.read(cid) == candidates.empty()
    assert candidates.malformed(cid) is True
    data = candidates.empty()
    data["generation"] = "00000000000000000001-run"
    data["records"] = {"d": _pair()}
    candidates.write(cid, data)
    assert candidates.malformed(cid) is False
    back = candidates.read(cid)
    assert back["generation"] == "00000000000000000001-run"
    assert back["records"] == {"d": _pair()}


def test_a_non_dict_top_level_is_malformed(cid):
    _file(cid).write_text("[]", encoding="utf-8")
    assert candidates.read(cid) == candidates.empty()
    assert candidates.malformed(cid) is True


def test_undecodable_bytes_read_empty(cid):
    _file(cid).write_bytes(b"\xff\xfe{")
    assert candidates.read(cid) == candidates.empty()
    assert candidates.malformed(cid) is True


def test_malformed_records_are_skipped_others_kept(cid):
    _store(cid, {
        "a": [],
        "b": _pair(kind="bogus"),
        "c": _pair(refs=["thread:maras-map"]),
        "e": _pair(refs=["x", "y"]),
        "f": _closure(refs=["commitment:maras-oath"]),
        "g": _pair(refs=["thread:maras-map", "commitment:maras-oath"]),
        "h": _pair(kind="possible_relation",
                   refs=["thread:maras-map", "event:the-coronation"]),
        "i": _pair(fingerprint=3),
        "j": _pair(refs=["thread:maras-map", 4]),
        "k": _closure(kind="possible_commitment_resolution", refs=["thread:maras-map"]),
        "l": _pair(refs=["thread:", "thread:winifreds-chart"]),
        "d": _pair(),
    })
    assert list(candidates.read(cid)["records"]) == ["d"]


def test_a_pair_naming_one_record_twice_is_skipped(cid):
    """A pair is two records: one ref twice (a hand edit, a garbled sync) is
    not a finding, and kept would reach an alias plan with no source."""
    _store(cid, {
        "dup-t": _pair(refs=["thread:maras-map", "thread:maras-map"]),
        "dup-c": _pair(refs=["commitment:maras-oath", "commitment:maras-oath"]),
        "rel-c": _pair(kind="possible_relation",
                       refs=["commitment:maras-oath", "commitment:maras-oath"]),
        "d": _pair(),
    })
    assert list(candidates.read(cid)["records"]) == ["d"]


def test_valid_record_shapes_of_every_kind_are_kept(cid):
    _store(cid, {
        "dup-c": _pair(refs=["commitment:maras-oath", "commitment:winifreds-vow"]),
        "rel-tc": _pair(kind="possible_relation",
                        refs=["thread:maras-map", "commitment:maras-oath"]),
        "rel-ce": _pair(kind="possible_relation",
                        refs=["commitment:maras-oath", "event:the-coronation"]),
        "close": _closure(),
        "resolve": _closure(kind="possible_commitment_resolution",
                            refs=["commitment:maras-oath"]),
    })
    assert sorted(candidates.read(cid)["records"]) == [
        "close", "dup-c", "rel-ce", "rel-tc", "resolve"]


def test_bad_nested_fields_are_normalized(cid):
    _store(cid, {
        "a": _pair(signals=[]),
        "b": _pair(proposal={"decision": 3, "evidence_scenes": "012"}),
        "c": _pair(proposal={"decision": "distinct"}),
        "d": _pair(proposal=[]),
        "e": _pair(created=7),
        "f": _pair(proposal=_proposal(evidence_scenes=["012", 5])),
    })
    records = candidates.read(cid)["records"]
    assert records["a"]["signals"] == {}
    assert records["b"]["proposal"] is None
    assert records["c"]["proposal"] == {
        "decision": "distinct", "from": "", "to": "", "relation": "",
        "status": "", "reason": "", "evidence_scenes": []}
    assert records["d"]["proposal"] is None
    assert records["e"]["created"] == ""
    assert records["f"]["proposal"] is None


def test_non_finite_signals_are_dropped_so_the_record_stays_json_safe(cid):
    # `json.loads` accepts the standard literal 1e999 (as inf) and the
    # non-standard NaN / Infinity tokens; a JSON response renders with
    # allow_nan=False, so one such value must not reach a consumer.
    _store(cid, {"a": _pair(signals={"cosine": "__INF__", "lexical": 0.5,
                                     "shared_scenes": ["012--the-coronation"],
                                     "spread": ["__NAN__"],
                                     "nested": {"x": "__NEG__"}}),
                 "b": _closure()})
    path = _file(cid)
    text = (path.read_text(encoding="utf-8")
            .replace('"__INF__"', "1e999").replace('"__NAN__"', "NaN")
            .replace('"__NEG__"', "-Infinity"))
    path.write_text(text, encoding="utf-8")
    data = candidates.read(cid)
    assert data["records"]["a"]["signals"] == {
        "lexical": 0.5, "shared_scenes": ["012--the-coronation"]}
    json.dumps(data, allow_nan=False)
    assert candidates.drop(cid, "b") is not None
    json.loads(path.read_text(encoding="utf-8"), parse_constant=_refuse)


def _refuse(token):
    raise AssertionError(f"non-standard JSON token {token!r} written")


def test_bad_top_level_fields_are_normalized(cid):
    _file(cid).write_text(json.dumps({
        "version": 1, "generated": 5, "generation": None,
        "basis": {"embedding_space": 3, "embedding_model": "m",
                  "identity_hashes": {"thread:maras-map": "h", "thread:x": 1},
                  "scored": [], "text_hashes": {"thread:maras-map": "t", "thread:x": None}},
        "records": [],
    }), encoding="utf-8")
    data = candidates.read(cid)
    assert data["generated"] == "" and data["generation"] == ""
    # A file with no question shape predates it: the shape before the fold.
    assert data["questions"] == 1
    assert data["basis"] == {"embedding_space": "", "embedding_model": "m",
                             "identity_hashes": {"thread:maras-map": "h"}, "scored": {},
                             "text_hashes": {"thread:maras-map": "t"}}
    assert data["records"] == {}
    assert candidates.malformed(cid) is False


def test_written_json_is_sorted_and_newline_terminated(cid):
    data = candidates.empty()
    data["records"] = {"z": _pair(), "a": _closure()}
    candidates.write(cid, data)
    text = _file(cid).read_text(encoding="utf-8")
    assert text == json.dumps(data, indent=2, sort_keys=True) + "\n"
    assert text.index('"a"') < text.index('"z"')


def test_drop_returns_the_record_and_absent_writes_nothing(cid):
    data = candidates.empty()
    data["records"] = {"d": _pair(), "e": _closure()}
    candidates.write(cid, data)
    assert candidates.drop(cid, "d") == _pair()
    assert list(candidates.read(cid)["records"]) == ["e"]

    path = _file(cid)
    os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    before = path.stat().st_mtime_ns
    assert candidates.drop(cid, "missing") is None
    assert path.stat().st_mtime_ns == before


def test_drop_on_a_malformed_cache_writes_nothing(cid):
    _file(cid).write_text("{ no", encoding="utf-8")
    assert candidates.drop(cid, "d") is None
    assert _file(cid).read_text(encoding="utf-8") == "{ no"


def test_drop_on_an_absent_cache_creates_nothing(cid):
    assert candidates.drop(cid, "d") is None
    assert not _file(cid).exists()


def test_mark_dismissed_records_the_fingerprint_and_reads_it_back(cid):
    """§12.7: a model-only finding set aside carries the fingerprint it was
    dismissed under; a record never dismissed carries no such key, and one
    that is not a string reads as never dismissed."""
    _store(cid, {"d": _closure(), "e": _pair(dismissed=7)})
    assert candidates.mark_dismissed(cid, "d", "fp2_life") is True
    records = candidates.read(cid)["records"]
    assert records["d"] == {**_closure(), "dismissed": "fp2_life"}
    assert records["e"] == _pair()
    assert candidates.mark_dismissed(cid, "missing", "fp") is False


def test_mark_dismissed_on_a_malformed_or_absent_cache_writes_nothing(cid):
    assert candidates.mark_dismissed(cid, "d", "fp") is False
    assert not _file(cid).exists()
    _file(cid).write_text("{ no", encoding="utf-8")
    assert candidates.mark_dismissed(cid, "d", "fp") is False
    assert _file(cid).read_text(encoding="utf-8") == "{ no"


def test_records_projection_carries_ids_sorted(cid):
    _store(cid, {"zeta": _pair(), "alpha": _closure()})
    rows = candidates.records(cid)
    assert [r["id"] for r in rows] == ["alpha", "zeta"]
    assert rows[0] == {"id": "alpha", **_closure()}


def test_candidates_is_a_leaf():
    src = Path(candidates.__file__)
    tree = ast.parse(src.read_text(encoding="utf-8"))
    reached = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            base = node.module or ""
            for alias in node.names:
                reached.add(f"{'.' * node.level}{base}:{alias.name}")
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("grimoire"):
            reached.add(node.module)
        elif isinstance(node, ast.Import):
            reached.update(a.name for a in node.names if a.name.startswith("grimoire"))
    assert reached == {"..:atomic", "..:locks", "..campaigns:paths"}


@pytest.mark.parametrize(("stored", "read"), [
    (2, 2), (1, 1), (3, 3), ("2", 1), (True, 1), (0, 1), (-1, 1), (2.0, 1), (None, 1)])
def test_the_question_shape_reads_as_stored_else_as_before_the_fold(cid, stored, read):
    _file(cid).write_text(json.dumps({"version": 1, "questions": stored, "records": {}}),
                          encoding="utf-8")
    assert candidates.read(cid)["questions"] == read
    assert candidates.QUESTIONS == 2


def test_a_drop_keeps_the_question_shape_it_read(cid):
    """Only a sweep's persist asks again what an older shape answered, so a
    drop between sweeps must not stamp today's shape over a cache it did not
    heal."""
    data = candidates.empty()
    data["questions"] = 1
    data["records"] = {"a": _closure(), "b": _closure()}
    candidates.write(cid, data)
    assert candidates.drop(cid, "a") is not None
    assert json.loads(_file(cid).read_text(encoding="utf-8"))["questions"] == 1

