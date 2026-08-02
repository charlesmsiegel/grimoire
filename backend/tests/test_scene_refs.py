import json

from grimoire.store import (appearances, campaigns, changes, chronicle, commitments,
                            plot, scene_refs, worlds)


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    return campaigns.create_campaign("Run", wid)


def _seed_appearance(cid, sid):
    p = campaigns.campaign_root(cid) / "appearances.json"
    p.write_text(json.dumps({
        "characters/a": {"version": "default", "base": "", "scenes": [sid], "role": "npc"},
        "characters/b": {"version": "default", "base": "", "scenes": ["other"], "role": "npc"},
    }), encoding="utf-8")


def test_repoint_updates_every_store_that_holds_scene_ids(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    old = "001--s"
    _seed_appearance(cid, old)
    chronicle.absorb(cid, {"id": old, "one_line": "x", "summary": "", "keywords": []})
    changes.record(cid, old, {"characters/a": [{"op": "equal", "text": "hi"}]})
    plot.set_movement(cid, "heist", "The Heist", "open", "cased the vault", old)
    commitments.set_movement(cid, "the-debt", "The debt", "promise", "open", "",
                             "sworn at the vault door", old)

    scene_refs.repoint(cid, {old: "001--2026-07-04--s"})

    assert appearances.record(cid)["characters/a"]["scenes"] == ["001--2026-07-04--s"]
    assert appearances.record(cid)["characters/b"]["scenes"] == ["other"]  # untouched
    rec = chronicle.read_chronicle(cid)["001--2026-07-04--s"]
    assert rec["id"] == "001--2026-07-04--s"
    assert "001--s" not in chronicle.read_chronicle(cid)
    assert changes.read(cid)["characters/a"]["scene"] == "001--2026-07-04--s"
    thread = plot.read(cid)["heist"]
    assert thread["last_scene"] == "001--2026-07-04--s"
    assert thread["beats"][0]["scene"] == "001--2026-07-04--s"
    owed = commitments.read(cid)["the-debt"]
    assert owed["last_scene"] == "001--2026-07-04--s"
    assert owed["beats"][0]["scene"] == "001--2026-07-04--s"


def test_repoint_identity_and_empty_are_noops(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    scene_refs.repoint(cid, {})
    scene_refs.repoint(cid, {"a": "a"})  # must not create empty store files
    assert not (campaigns.campaign_root(cid) / "chronicle.json").exists()


def test_repoint_tolerates_missing_stores(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    scene_refs.repoint(cid, {"a": "b"})  # no store files exist — must not raise
