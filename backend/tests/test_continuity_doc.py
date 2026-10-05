"""`store/continuity/doc.py` -- the continuity.json file (capstone spec §5).

What is held here is the reader/writer split the spec takes from `events`: a
reader that degrades a malformed file (or one malformed section) to empty so a
prompt or a panel loses an enhancement rather than its turn, and mutators that
refuse to publish over content they could not read.
"""

import importlib
import json

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app
from grimoire.store.campaigns import paths as campaigns_paths
from grimoire.store.continuity import doc


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
    return campaigns_paths.campaign_root(cid) / "continuity.json"


def _alias(to="thread:winifreds-chart"):
    return {"to": to, "created": "2026-10-05T00:00:00Z", "source": "manual", "note": ""}


def _link():
    return {"a": "thread:maras-map", "b": "commitment:mara-promise",
            "relation": "pays_off", "created": "2026-10-05T00:00:00Z",
            "scene": "", "note": ""}


def test_absent_file_reads_empty(cid):
    assert doc.read(cid) == {"version": 1, "aliases": {}, "links": {}, "suppressions": {}}
    assert doc.malformed(cid) == []


def test_unparseable_file_reads_empty_and_refuses_writes(cid):
    _file(cid).write_text("{ no", encoding="utf-8")
    assert doc.read(cid) == {"version": 1, "aliases": {}, "links": {}, "suppressions": {}}
    assert doc.malformed(cid) == ["file"]
    with pytest.raises(doc.ContinuityError):
        doc.put_alias(cid, "thread:maras-map", _alias())
    assert _file(cid).read_text(encoding="utf-8") == "{ no"


# Two hand-edited shapes `json.loads` refuses with something other than a
# JSONDecodeError: an integer literal past Python's 4300-digit conversion limit
# (a plain ValueError) and nesting deep enough to exhaust the C scanner's
# recursion guard (RecursionError).
_HOSTILE = [
    pytest.param('{"aliases": {}, "n": ' + "9" * 5000 + "}", id="overlong-int"),
    pytest.param("[" * 200000, id="deep-nesting"),
]


@pytest.mark.parametrize("text", _HOSTILE)
def test_a_file_json_refuses_without_a_decode_error_still_reads_empty(cid, text):
    """`read` never raises over the file, whatever `json.loads` raises over it:
    every prompt section, the briefing and the advance digest read through it."""
    _file(cid).write_text(text, encoding="utf-8")
    assert doc.read(cid) == {"version": 1, "aliases": {}, "links": {}, "suppressions": {}}
    assert doc.malformed(cid) == ["file"]
    with pytest.raises(doc.ContinuityError):
        doc.put_alias(cid, "thread:maras-map", _alias())
    assert _file(cid).read_text(encoding="utf-8") == text


def test_non_dict_top_level_is_the_whole_file_malformed(cid):
    _file(cid).write_text("[]", encoding="utf-8")
    assert doc.malformed(cid) == ["file"]
    with pytest.raises(doc.ContinuityError):
        doc.put_link(cid, "l1", _link())


def test_one_malformed_section_reads_empty_others_survive(cid):
    _file(cid).write_text(json.dumps(
        {"aliases": [], "links": {"l1": _link()}, "suppressions": {}}), encoding="utf-8")
    data = doc.read(cid)
    assert data["aliases"] == {}
    assert data["links"] == {"l1": _link()}
    assert doc.malformed(cid) == ["aliases"]
    doc.put_link(cid, "l2", _link())
    assert set(doc.read(cid)["links"]) == {"l1", "l2"}
    with pytest.raises(doc.ContinuityError):
        doc.put_alias(cid, "thread:maras-map", _alias())


def test_unknown_keys_and_version_preserved(cid):
    record = dict(_alias(), x=2)
    _file(cid).write_text(json.dumps(
        {"version": 7, "extra": 1, "aliases": {"thread:maras-map": record}}),
        encoding="utf-8")
    doc.put_link(cid, "l1", _link())
    on_disk = json.loads(_file(cid).read_text(encoding="utf-8"))
    assert on_disk["version"] == 7
    assert on_disk["extra"] == 1
    assert on_disk["aliases"]["thread:maras-map"]["x"] == 2
    assert doc.read(cid)["version"] == 7
    assert doc.read(cid)["extra"] == 1


@pytest.mark.parametrize("put,get,drop,key,record", [
    (doc.put_alias, doc.get_alias, doc.drop_alias, "thread:maras-map", _alias()),
    (doc.put_link, doc.get_link, doc.drop_link, "l1", _link()),
    (doc.put_suppression, None, doc.drop_suppression, "fp1_abc",
     {"kind": "possible_duplicate", "refs": ["thread:a", "thread:b"],
      "decision": "dismiss", "created": "2026-10-05T00:00:00Z"}),
])
def test_put_and_drop_round_trip(cid, put, get, drop, key, record):
    put(cid, key, record)
    if get is not None:
        assert get(cid, key) == record
    assert drop(cid, key) == record
    assert drop(cid, key) is None


def test_written_json_is_sorted_and_newline_terminated(cid):
    doc.put_alias(cid, "thread:maras-map", _alias())
    text = _file(cid).read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"


# -------------------------------------------------------------- scene renames


def test_repoint_rewrites_link_scene(cid):
    doc.put_link(cid, "l1", dict(_link(), scene="003--old"))
    doc.repoint_scenes(cid, {"003--old": "003--new"})
    assert doc.get_link(cid, "l1")["scene"] == "003--new"


def test_repoint_skips_unparseable_file(cid):
    _file(cid).write_text("{ no", encoding="utf-8")
    doc.repoint_scenes(cid, {"003--old": "003--new"})
    assert _file(cid).read_text(encoding="utf-8") == "{ no"


def test_repoint_tolerates_hand_edited_shapes(cid):
    _file(cid).write_text(json.dumps({"links": {"l1": 3, "l2": {"scene": ["x"]},
                                                "l3": dict(_link(), scene="003--old")}}),
                          encoding="utf-8")
    doc.repoint_scenes(cid, {"003--old": "003--new"})
    on_disk = json.loads(_file(cid).read_text(encoding="utf-8"))
    assert on_disk["links"]["l1"] == 3
    assert on_disk["links"]["l2"] == {"scene": ["x"]}
    assert on_disk["links"]["l3"]["scene"] == "003--new"


def test_repoint_never_creates_absent_file(cid):
    doc.repoint_scenes(cid, {"003--old": "003--new"})
    assert not _file(cid).exists()


def test_scene_refs_fans_out_to_continuity(cid):
    doc.put_link(cid, "l1", dict(_link(), scene="003--old"))
    store.scene_refs.repoint(cid, {"003--old": "003--new"})
    assert doc.get_link(cid, "l1")["scene"] == "003--new"
