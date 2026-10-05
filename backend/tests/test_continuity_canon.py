"""`store/continuity/canon.py` -- refs, alias-graph resolution, ids, fingerprints.

`canon` answers what the stored alias graph says, independent of whether the
records it names exist; current-state questions go through
`effective.live_canon`. The ids and fingerprints pinned here are what later
slices store and compare, so their inputs are part of the contract (spec §5.4,
§5.5).
"""

import importlib
import inspect

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app
from grimoire.store.campaigns import paths as campaigns_paths
from grimoire.store.continuity import canon, doc


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


def test_split_ref_uses_first_colon():
    assert canon.split_ref("birthday:characters:mara:740000") == (
        "birthday", "characters:mara:740000")
    assert canon.split_ref("thread:a/b") == ("thread", "a/b")
    for bad in ("nocolon", ":x", "thread:", 3, None):
        with pytest.raises(ValueError):
            canon.split_ref(bad)


def test_ref_kind_and_prefix_tables_are_inverse():
    assert canon.ref_kind("characters:mara") == "character"
    assert canon.ref_kind("locations:saltmarch") == "location"
    assert canon.ref_kind("thread:missing-map") == "thread"
    with pytest.raises(ValueError):
        canon.ref_kind("place:saltmarch")
    assert {v: k for k, v in canon.KIND_OF_PREFIX.items()} == canon.PREFIX_OF_KIND


def test_actor_ref_converts_slash_form():
    assert canon.actor_ref("characters/mara") == "characters:mara"
    assert canon.actor_ref("pcs/seraphine") == "pcs:seraphine"
    assert canon.actor_ref("characters:mara") == "characters:mara"


def test_resolve_transitive():
    aliases = {"thread:a": {"to": "thread:b"}, "thread:b": {"to": "thread:c"}}
    assert canon.resolve(aliases, "thread:a") == "thread:c"
    assert canon.resolve(aliases, "thread:c") == "thread:c"


def test_resolve_stops_at_non_dict_record_and_non_str_to():
    assert canon.resolve({"thread:a": "thread:b"}, "thread:a") == "thread:a"
    assert canon.resolve({"thread:a": {"to": 3}}, "thread:a") == "thread:a"
    assert canon.resolve({"thread:a": {"to": "thread:b"}, "thread:b": {"to": ""}},
                         "thread:a") == "thread:b"


def test_resolve_cycle_lenient_returns_input_and_strict_raises():
    aliases = {"thread:a": {"to": "thread:b"}, "thread:b": {"to": "thread:a"}}
    assert canon.resolve(aliases, "thread:a") == "thread:a"
    assert canon.resolve(aliases, "thread:b") == "thread:b"
    with pytest.raises(doc.ContinuityError):
        canon.resolve(aliases, "thread:a", strict=True)


def test_canonical_ref_reads_the_file(cid):
    doc.put_alias(cid, "thread:a", {"to": "thread:b"})
    assert canon.canonical_ref(cid, "thread:a") == "thread:b"
    assert canon.canonical_refs(cid, ["thread:a", "thread:z"]) == {
        "thread:a": "thread:b", "thread:z": "thread:z"}


def test_canonical_ref_unreadable_file_returns_input_and_strict_raises(cid):
    (campaigns_paths.campaign_root(cid) / "continuity.json").write_text("{ no", encoding="utf-8")
    assert canon.canonical_ref(cid, "thread:a") == "thread:a"
    with pytest.raises(doc.ContinuityError):
        canon.canonical_ref(cid, "thread:a", strict=True)
    with pytest.raises(doc.ContinuityError):
        canon.canonical_refs(cid, ["thread:a"], strict=True)


def test_link_id_symmetric_only_for_related_to():
    assert canon.link_id("related_to", "thread:a", "event:x") == canon.link_id(
        "related_to", "event:x", "thread:a")
    assert canon.link_id("pays_off", "thread:a", "commitment:x") != canon.link_id(
        "pays_off", "commitment:x", "thread:a")
    lid = canon.link_id("continues", "thread:a", "thread:b")
    assert lid.startswith("l") and len(lid) == 21


def test_candidate_id_order_independent():
    a = canon.candidate_id("possible_duplicate", ["thread:b", "thread:a"])
    assert a == canon.candidate_id("possible_duplicate", ["thread:a", "thread:b"])
    assert a.startswith("possible_duplicate-") and len(a.split("-")[-1]) == 16


def test_pair_fingerprint_ignores_side_order_and_beats():
    one = {"ref": "thread:a", "title": "The missing map", "status": "open",
           "kind": "", "due": "", "latest_beat": "Mara found a page."}
    two = {"ref": "thread:b", "title": "The lost chart", "status": "advanced"}
    fp = canon.pair_fingerprint("possible_duplicate", [one, two])
    assert fp.startswith("fp1_")
    assert fp == canon.pair_fingerprint("possible_duplicate", [two, one])
    assert fp == canon.pair_fingerprint(
        "possible_duplicate", [dict(one, latest_beat="Something else."), two])
    assert fp != canon.pair_fingerprint(
        "possible_duplicate", [dict(one, title="The torn map"), two])


def test_lifecycle_fingerprint_has_no_last_scene():
    base = dict(kind="possible_thread_closure", ref="thread:a", status="advanced",
                beat_count=2, latest_beat="Mara found a page.", due="",
                temporal_link_ids=["l2", "l1"])
    fp = canon.lifecycle_fingerprint(**base)
    assert fp == canon.lifecycle_fingerprint(**dict(base, temporal_link_ids=["l1", "l2"]))
    assert fp != canon.lifecycle_fingerprint(**dict(base, beat_count=3))
    assert fp != canon.lifecycle_fingerprint(**dict(base, latest_beat="Gone."))
    assert "last_scene" not in inspect.signature(canon.lifecycle_fingerprint).parameters
