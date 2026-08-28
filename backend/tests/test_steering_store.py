"""The reroll-steering log: `<campaign>/scenes/<sid>.steering.json`."""

import json

from grimoire.store import campaigns, scenes, steering, worlds
from grimoire.store.scenes import paths as scenes_paths


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "Quay")
    return cid, sid


def test_record_appends_and_texts_reads_in_order(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    steering.record(cid, sid, "Mara already knows about the ledger")
    steering.record(cid, sid, "the east gate is barred at dusk")
    assert steering.texts(cid, sid) == [
        "Mara already knows about the ledger",
        "the east gate is barred at dusk"]


def test_empty_and_whitespace_record_nothing(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    steering.record(cid, sid, "")
    steering.record(cid, sid, "   ")
    assert steering.texts(cid, sid) == []
    assert not scenes_paths._steering_path(cid, sid).exists()


def test_consecutive_duplicate_is_one_entry(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    steering.record(cid, sid, "shorter")
    steering.record(cid, sid, "shorter")          # error-banner retry re-sends
    steering.record(cid, sid, "longer")
    steering.record(cid, sid, "shorter")          # non-consecutive: a new signal
    assert steering.texts(cid, sid) == ["shorter", "longer", "shorter"]


def test_clip_and_cap(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    steering.record(cid, sid, "x" * (steering.MAX_STEERING_CHARS + 50))
    assert steering.texts(cid, sid) == ["x" * steering.MAX_STEERING_CHARS]
    for i in range(steering.STEERING_LIMIT + 5):
        steering.record(cid, sid, f"note {i}")
    kept = steering.texts(cid, sid)
    assert len(kept) == steering.STEERING_LIMIT
    assert kept[-1] == f"note {steering.STEERING_LIMIT + 4}"   # newest kept
    assert kept[0] == "note 5"     # oldest dropped (the clipped entry counted)


def test_garbled_file_reads_empty_and_is_replaced(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    p = scenes_paths._steering_path(cid, sid)
    p.write_text("{not json", encoding="utf-8")
    assert steering.texts(cid, sid) == []
    steering.record(cid, sid, "fresh start")
    assert steering.texts(cid, sid) == ["fresh start"]
    assert json.loads(p.read_text(encoding="utf-8"))["v"] == 1


def test_record_is_failsoft_on_oserror(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)

    def boom(*a, **k):
        raise OSError("disk says no")
    monkeypatch.setattr(steering.atomic, "write_text", boom)
    steering.record(cid, sid, "lost, and that is fine")   # must not raise
    assert steering.texts(cid, sid) == []


def test_sid_taken_counts_an_orphan_steering_sidecar(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    steering.record(cid, sid, "orphan-to-be")
    scenes_paths._scene_path(cid, sid).unlink()
    assert scenes_paths._sid_taken(cid, sid)
