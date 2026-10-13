"""04-S5: the continuity summary, computed from the five ledgers' bytes.

The five readers each ask `store.ledger_texts` for their file's text and parse
it with their own `*_from_text` twin, so the summary's byte-fed compute is
today's code fed differently. The tests here hold that it opens nothing, reads
no clock, notices every change to its five inputs, and answers what the
pre-04 code answered -- except a `commitments.json` holding `[]`, which the
spec widens from a 500 to "unknown".
"""

from __future__ import annotations

import importlib
import json
import os

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app
from grimoire.routes import todo
from grimoire.store import ledger_texts, overview, readstats
from grimoire.store.continuity import effective
from grimoire.store.overview import kinds
from tests.overview_support import audited, clear_memos, clock_forward


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    clear_memos()
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def cid(client):
    wid = client.post("/api/worlds", json={"name": "Saltmarch"}).json()["id"]
    return client.post("/api/campaigns", json={"name": "A Long Run", "world": wid}).json()["id"]


def _ledgers(cid: str) -> str:
    sid = store.scenes.create_scene(cid, "The Pier at Dusk")
    store.commitments.set_movement(cid, "maras-oath", "Mara's oath", "promise", "open",
                                   "2026-06-01", "Mara swore it on the quay.", sid)
    store.commitments.set_movement(cid, "seraphines-favour", "Seraphine's favour", "promise",
                                   "open", "", "Seraphine owes one.", sid)
    return sid


def _oracle(cid: str) -> dict:
    """The pre-04 answers, each with its old failure mapping."""
    try:
        ledger_open = len(effective.commitments(cid))
    except Exception:  # noqa: BLE001
        ledger_open = None
    owed = len(todo._owed(cid))
    live = [r for _k, r, v in store.continuity.pending.findings(cid) if v == "live"]
    return {"ledger_open": ledger_open, "owed": owed,
            "overlaps": sum(1 for r in live if store.continuity.pending.CHORE_OF.get(r["kind"])
                            == "continuity-overlaps"),
            "closures": sum(1 for r in live if store.continuity.pending.CHORE_OF.get(r["kind"])
                            == "continuity-closures"),
            "closures_threads": any(r["kind"] == "possible_thread_closure" for r in live)}


def _files(cid: str) -> dict:
    return {name: (p.read_bytes() if p.exists() else None)
            for name, p in kinds.continuity_paths(cid).items()}


def test_summary_equals_the_oracle(cid):
    _ledgers(cid)
    assert overview.continuity_summary(cid) == _oracle(cid)
    assert overview.continuity_summary(cid)["owed"] == 1
    assert overview.continuity_summary(cid)["ledger_open"] == 2


def test_a_campaign_with_no_ledgers_gets_a_real_projection(cid):
    summary = overview.continuity_summary(cid)
    assert summary == {"ledger_open": 0, "owed": 0, "overlaps": 0, "closures": 0,
                       "closures_threads": False}


def test_commitments_holding_a_list_reads_unknown_where_the_oracle_500s(client, cid):
    _ledgers(cid)
    store.commitments._path(cid).write_text("[]", encoding="utf-8")
    with pytest.raises(AttributeError):
        todo._owed(cid)
    r = client.get("/api/todo", params={"campaign": cid})
    assert r.status_code == 200
    assert "owed" not in {c["id"] for c in r.json()["chores"]}
    assert client.get("/api/todo").status_code == 200
    block = client.get("/api/shell", params={"campaign": cid}).json()["campaign"]
    assert block["ledger_open"] is None


def test_the_byte_fed_compute_opens_nothing_and_reads_no_clock(cid, monkeypatch):
    _ledgers(cid)
    files = _files(cid)
    expected = kinds.continuity_live(cid)

    def trap(*a, **k):
        raise AssertionError("the compute read a clock")
    monkeypatch.setattr(store.paths, "now_iso", trap)
    import time as time_module
    monkeypatch.setattr(time_module, "time", trap)
    with audited() as audit:
        value = kinds.continuity(cid, files)
    assert value == expected
    assert audit.opened == [] and audit.listed == []


def test_feeding_answers_only_its_own_campaign_and_requires_every_file(cid):
    with pytest.raises(ValueError), ledger_texts.feeding(cid, {"plot": None}):
        pass
    fed: dict[str, bytes | None] = dict.fromkeys(ledger_texts.NAMES)
    fed["plot"] = b'{"x": {}}'
    with ledger_texts.feeding(cid, fed):
        assert store.plot.read(cid) == {"x": {}}
        assert store.plot.read(cid + "-other") == {}


def test_crlf_and_bad_bytes_decode_as_read_text_would(cid):
    _ledgers(cid)
    p = store.commitments._path(cid)
    p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
    assert kinds.continuity(cid, _files(cid)) == kinds.continuity_live(cid)
    p.write_bytes(b"\xff" + p.read_bytes())
    files = _files(cid)
    assert kinds.continuity(cid, files) == kinds.continuity_live(cid)
    assert kinds.continuity(cid, files)["ledger_open"] is None


@pytest.mark.parametrize("name", ledger_texts.NAMES)
def test_each_file_edited_created_and_deleted_is_seen(cid, monkeypatch, name):
    clock_forward(monkeypatch)
    _ledgers(cid)
    path = kinds.continuity_paths(cid)[name]
    before = overview.continuity_summary(cid)
    with readstats.collect() as counters:
        assert overview.continuity_summary(cid) == before
    assert counters.get("overview.computed.continuity_summary") == 0
    existed = path.exists()
    original = path.read_bytes() if existed else None
    path.write_text("[]", encoding="utf-8")           # created or edited
    with readstats.collect() as counters:
        overview.continuity_summary(cid)
    assert counters.get("overview.computed.continuity_summary") == 1
    path.unlink()                                     # deleted
    with readstats.collect() as counters:
        overview.continuity_summary(cid)
    assert counters.get("overview.computed.continuity_summary") == 1
    if existed:
        path.write_bytes(original)
    assert overview.continuity_summary(cid) == before


def test_a_same_size_rewrite_with_mtime_restored_is_seen(cid, monkeypatch):
    clock_forward(monkeypatch)
    _ledgers(cid)
    p = store.commitments._path(cid)
    assert overview.continuity_summary(cid)["owed"] == 1
    st = p.stat()
    data = json.loads(p.read_text(encoding="utf-8"))
    data["maras-oath"]["due"] = ""                    # shorter ...
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    text = text + " " * (st.st_size - len(text.encode("utf-8")))   # ... padded to the same size
    with open(p, "r+b") as f:
        f.write(text.encode("utf-8"))
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert (p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ino) == \
        (st.st_size, st.st_mtime_ns, st.st_ino)
    assert overview.continuity_summary(cid)["owed"] == 0


def test_the_shell_and_todo_share_one_computation(client, cid, monkeypatch):
    clock_forward(monkeypatch)
    _ledgers(cid)
    with readstats.collect() as counters:
        block = client.get("/api/shell", params={"campaign": cid}).json()["campaign"]
        owed = next(c for c in client.get("/api/todo", params={"campaign": cid}).json()["chores"]
                    if c["id"] == "owed")
    assert (block["ledger_open"], owed["n"]) == (2, 1)
    assert counters.get("overview.computed.continuity_summary") == 1


def test_a_failed_read_takes_the_live_path_and_remembers_nothing(cid, monkeypatch):
    clock_forward(monkeypatch)
    _ledgers(cid)
    p = store.commitments._path(cid)
    real = type(p).read_bytes

    def refuse(self):
        if self == p:
            raise PermissionError(13, "denied")
        return real(self)
    monkeypatch.setattr(type(p), "read_bytes", refuse)
    assert overview.continuity_summary(cid) == kinds.continuity_live(cid)
    monkeypatch.setattr(type(p), "read_bytes", real)
    with readstats.collect() as counters:
        overview.continuity_summary(cid)
    assert counters.get("overview.computed.continuity_summary") == 1


def test_a_missing_campaign_raises_rather_than_reading_as_empty(client):
    with pytest.raises(store.CampaignNotFound):
        overview.continuity_summary("no-such-campaign")
