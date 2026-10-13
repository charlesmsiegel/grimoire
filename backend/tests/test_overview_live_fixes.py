"""04-S3: the live-path fixes (spec 04 section 4.5, items 1 to 3).

Each keeps its output byte-identical and removes a per-navigation cost: the
module binding stops counting a world's records to read one key, the roster
stops re-parsing every card, and the unreviewed chore stops listing scenes
when nothing is waiting.
"""

from __future__ import annotations

import importlib
import json
from collections import Counter

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app
from grimoire.routes import todo
from grimoire.store.modules import binding
from tests.overview_support import audited, clear_memos, clock_forward


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    clear_memos()
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def campaign(client):
    wid = client.post("/api/worlds", json={"name": "Saltmarch"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "A Long Run", "world": wid}).json()["id"]
    root = store.worlds.world_root(wid)
    for name in ("Seraphine", "Mara", "Winifred"):
        store.characters.create_character(root, name, "default")
    return cid, wid


def test_binding_reads_the_worlds_module_without_listing_its_records(campaign):
    cid, wid = campaign
    root = store.worlds.world_root(wid)
    with audited() as audit:
        assert binding.resolve(cid) is None
        assert store.sheets.coverage(cid) == {}
    assert audit.listed_under(root) == []


def test_binding_still_resolves_the_worlds_module(campaign, monkeypatch):
    cid, wid = campaign
    mp = store.worlds.paths.world_meta_path(wid)
    mp.write_text(mp.read_text(encoding="utf-8").replace("---\n", "---\nmodule: dnd5e\n", 1),
                  encoding="utf-8")
    seen = []
    monkeypatch.setattr(binding.pack_mod, "load_pack",
                        lambda mid: seen.append(mid) or {"errors": []})
    assert binding.resolve(cid) == "dnd5e" and seen == ["dnd5e"]


def test_binding_keeps_world_not_found_for_a_missing_world_md(campaign):
    cid, wid = campaign
    store.worlds.paths.world_meta_path(wid).unlink()
    assert binding.resolve(cid) is None
    with pytest.raises(store.worlds.paths.WorldNotFound):
        binding._world_module(wid)
    with pytest.raises(store.worlds.paths.WorldNotFound):
        binding._world_module("")


def test_two_global_todo_reads_parse_each_character_md_at_most_once(client, campaign, monkeypatch):
    _cid, wid = campaign
    clock_forward(monkeypatch)
    root = store.worlds.world_root(wid)
    with audited() as audit:
        first = client.get("/api/todo").json()
        second = client.get("/api/todo").json()
    assert first == second
    opened = Counter(p for p in audit.opened_under(root) if p.endswith("character.md"))
    assert opened and max(opened.values()) == 1


def test_roster_matches_the_listing_and_notices_a_rename(campaign, monkeypatch):
    _cid, wid = campaign
    clock_forward(monkeypatch)
    root = store.worlds.world_root(wid)
    first = store.characters.roster(root)
    assert [r["id"] for r in first] == [c["id"] for c in store.characters.list_characters(root)]
    assert store.characters.roster(root) == first
    cid = first[0]["id"]
    meta = root / "characters" / cid / "character.md"
    meta.write_text(meta.read_text(encoding="utf-8").replace(f"name: {first[0]['name']}",
                                                             "name: Renamed Mara"), encoding="utf-8")
    assert store.characters.roster(root)[0]["name"] == "Renamed Mara"
    # Handing the caller a copy: mutating it never reaches the memo.
    store.characters.roster(root)[0]["name"] = "scribbled"
    assert store.characters.roster(root)[0]["name"] == "Renamed Mara"


def test_unreviewed_chore_lists_no_scenes_when_nothing_is_waiting(campaign, monkeypatch):
    cid, _wid = campaign
    monkeypatch.setattr(store.scenes.read, "list_scenes",
                        lambda c: pytest.fail("the chore needs no titles"))
    assert todo._chore_unreviewed(todo._Ctx(cid)) is None
    assert todo._items_unreviewed(cid) == []


def test_unreviewed_chore_and_items_agree_with_a_waiting_sidecar(client, campaign):
    cid, _wid = campaign
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Saltmarch Gate"}).json()["id"]
    d = store.scenes.paths._scenes_dir(cid)
    (d / f"{sid}.review.json").write_text(json.dumps(
        {"review": {"edits": [{"kind": "fact"}, {"kind": "fact"}, {"kind": "lore"}]}}), encoding="utf-8")
    chore = todo._chore_unreviewed(todo._Ctx(cid))
    assert chore["n"] == 3 and chore["fix"].endswith(f"/scenes/{sid}")
    [item] = todo._items_unreviewed(cid)
    assert (item["label"], item["detail"], item["proposals"]) == ("Saltmarch Gate", "2 fact, 1 lore", 3)
