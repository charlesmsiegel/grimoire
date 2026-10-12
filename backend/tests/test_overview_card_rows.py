"""04-S7: the world and campaign card rows, and the `module` chip.

The row kinds are byte-fed 03 kinds over one meta file each, with the name
fallback applied outside the cache, so two identical `campaign.md` files share
an artifact and still render two names. Whether they are REGISTERED is spec
03's measurement's call (`compiled.KINDS`); these tests register them, so the
persisted layer is held to the live answer either way.
"""

from __future__ import annotations

import importlib
import os

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app
from grimoire.store import compiled, overview, readstats
from grimoire.store.overview import persist
from tests.overview_support import (
    cache_clock_forward,
    clear_memos,
    clock_forward,
    database_locked,
    register_overview_kinds,
    restart,
)


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    monkeypatch.delenv("GRIMOIRE_COMPILED_CACHE", raising=False)
    importlib.reload(store)
    compiled.close()
    clear_memos()
    yield tmp_path
    compiled.close()


@pytest.fixture
def client(home):
    with TestClient(create_app()) as c:
        yield c


def _library(client):
    wid = client.post("/api/worlds", json={"name": "Saltmarch"}).json()["id"]
    other = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    a = client.post("/api/campaigns", json={"name": "A Long Run", "world": wid}).json()["id"]
    b = client.post("/api/campaigns", json={"name": "Mara's Wake", "world": other}).json()["id"]
    store.scenes.create_scene(a, "Saltmarch Gate")
    return wid, other, a, b


def _set(path, key, value):
    meta, body = store.frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
    if value is None:
        meta.pop(key, None)
    else:
        meta[key] = value
    path.write_text(store.frontmatter.dump_frontmatter(meta, body), encoding="utf-8")


def _oracle_worlds():
    """`GET /worlds` before 04: `list_worlds` plus the cover token."""
    return [{**w, "cover": store.covers.world_cover_version(w["id"])} for w in store.worlds.list_worlds()]


def _sans(rows, key):
    return [{k: v for k, v in r.items() if k != key} for r in rows]


def _payloads(client, cid):
    return (client.get("/api/worlds").json(), client.get("/api/campaigns").json(),
            client.get("/api/shell", params={"campaign": cid}).json())


def test_worlds_payload_equals_the_oracle_but_for_module(client):
    _library(client)
    worlds = client.get("/api/worlds").json()
    assert _sans(worlds, "module") == _oracle_worlds()
    assert all(w["module"] == "" for w in worlds)


@pytest.mark.parametrize("registered", [True, False])
def test_five_states_answer_identically(client, home, monkeypatch, registered):
    if registered:
        register_overview_kinds(monkeypatch)
    cache_clock_forward(monkeypatch)
    _wid, _other, a, _b = _library(client)
    cold = _payloads(client, a)                     # cache on and empty
    assert _payloads(client, a) == cold             # cache on and warm
    restart()
    assert _payloads(client, a) == cold             # after a restart
    with database_locked(home):
        clear_memos()
        assert _payloads(client, a) == cold         # the database locked
    monkeypatch.setenv("GRIMOIRE_COMPILED_CACHE", "0")
    clear_memos()
    assert _payloads(client, a) == cold             # cache off


def test_warm_after_restart_parses_no_campaign_md(client, monkeypatch):
    register_overview_kinds(monkeypatch)
    cache_clock_forward(monkeypatch)
    _library(client)
    first = client.get("/api/campaigns").json()
    restart()
    with readstats.collect() as counters:
        assert client.get("/api/campaigns").json() == first
    assert counters.get("overview.computed.campaign_row") == 0
    assert counters.get("compiled.hit.overview.campaign_row") == len(first)
    # Scene heads are not persisted: one head-only parse per scene, today's cost.
    assert counters.get("frontmatter.parse_head") == sum(c["scenes"] for c in first)


def test_identical_bytes_in_two_directories_share_an_artifact_and_keep_their_names(
        client, home, monkeypatch):
    register_overview_kinds(monkeypatch)
    cache_clock_forward(monkeypatch)
    _wid, _other, a, b = _library(client)
    ma = store.campaigns.paths.campaign_meta_path(a)
    mb = store.campaigns.paths.campaign_meta_path(b)
    _set(ma, "name", None)
    mb.write_bytes(ma.read_bytes())
    with readstats.collect() as counters:
        cards = {c["id"]: c for c in client.get("/api/campaigns").json()}
    assert (cards[a]["name"], cards[b]["name"]) == (a, b)
    assert counters.get("overview.computed.campaign_row") == 1


def test_present_but_empty_name_stays_empty(client):
    _wid, _other, a, _b = _library(client)
    _set(store.campaigns.paths.campaign_meta_path(a), "name", "")
    assert next(c for c in client.get("/api/campaigns").json() if c["id"] == a)["name"] == ""


@pytest.mark.parametrize("persisted", [True, False])
def test_meta_changes_are_seen_in_process_and_after_restart(client, monkeypatch, persisted):
    if persisted:
        register_overview_kinds(monkeypatch)
    cache_clock_forward(monkeypatch)
    clock_forward(monkeypatch)
    wid, _other, a, _b = _library(client)
    client.get("/api/campaigns")
    client.get("/api/worlds")
    _set(store.campaigns.paths.campaign_meta_path(a), "name", "Winifred's Return")
    _set(store.worlds.paths.world_meta_path(wid), "name", "Saltmarch Deep")
    assert next(c for c in client.get("/api/campaigns").json() if c["id"] == a)["name"] == \
        "Winifred's Return"
    assert next(w for w in client.get("/api/worlds").json() if w["id"] == wid)["name"] == "Saltmarch Deep"
    restart()
    _set(store.campaigns.paths.campaign_meta_path(a), "name", "Mara Again")
    assert next(c for c in client.get("/api/campaigns").json() if c["id"] == a)["name"] == "Mara Again"


def test_same_size_campaign_md_rewrite_with_mtime_restored_is_seen(client, monkeypatch):
    register_overview_kinds(monkeypatch)
    cache_clock_forward(monkeypatch)
    clock_forward(monkeypatch)
    _wid, _other, a, _b = _library(client)
    mp = store.campaigns.paths.campaign_meta_path(a)
    assert next(c for c in client.get("/api/campaigns").json() if c["id"] == a)["name"] == "A Long Run"
    st = mp.stat()
    with open(mp, "r+b") as f:
        data = f.read().replace(b"name: A Long Run", b"name: A Lone Run")
        f.seek(0)
        f.write(data)
    os.utime(mp, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert next(c for c in client.get("/api/campaigns").json() if c["id"] == a)["name"] == "A Lone Run"
    restart()
    assert next(c for c in client.get("/api/campaigns").json() if c["id"] == a)["name"] == "A Lone Run"


@pytest.mark.parametrize("campaign_setting,world_default,chip", [
    ("", "", ""), ("", "dnd5e", "dnd5e"), ("fate", "dnd5e", "fate"),
    ("none", "dnd5e", ""), ("", "none", ""),
])
def test_the_module_chip(client, campaign_setting, world_default, chip):
    wid, _other, a, _b = _library(client)
    _set(store.campaigns.paths.campaign_meta_path(a), "module", campaign_setting or None)
    _set(store.worlds.paths.world_meta_path(wid), "module", world_default or None)
    card = next(c for c in client.get("/api/campaigns").json() if c["id"] == a)
    assert card["module"] == chip
    world = next(w for w in client.get("/api/worlds").json() if w["id"] == wid)
    assert world["module"] == world_default


def test_list_campaigns_is_unchanged(client):
    _library(client)
    assert all("module" not in c for c in store.campaigns.list_campaigns())


def test_an_unreadable_campaign_md_raises_as_it_does_today(client, monkeypatch):
    register_overview_kinds(monkeypatch)
    _wid, _other, a, _b = _library(client)
    mp = store.campaigns.paths.campaign_meta_path(a)
    mp.write_bytes(mp.read_bytes() + b"\xff")
    clear_memos()
    with pytest.raises(UnicodeDecodeError):
        store.campaigns.list_campaigns()
    with pytest.raises(UnicodeDecodeError):
        overview.campaign_cards()


def test_a_failed_input_read_takes_the_live_path_and_counts_it(client, monkeypatch):
    register_overview_kinds(monkeypatch)
    _wid, _other, a, _b = _library(client)

    def unavailable(*args, **kwargs):
        raise compiled.InputUnavailable("busy")
    monkeypatch.setattr(compiled.Batch, "derive", unavailable)
    with readstats.collect() as counters:
        cards = overview.campaign_cards()
    assert {c["id"] for c in cards} >= {a}
    assert counters.get("overview.live_path.overview.campaign_row") >= 1


def test_an_unregistered_kind_never_touches_the_cache(client, monkeypatch):
    _library(client)
    monkeypatch.setattr(compiled, "KINDS", {k: v for k, v in compiled.KINDS.items()
                                            if k not in compiled.OVERVIEW_CANDIDATES})
    monkeypatch.setattr(compiled, "batch", lambda *a, **k: pytest.fail("an unregistered kind opened a batch"))
    clear_memos()
    assert overview.campaign_cards()
    assert not persist.registered(compiled.OVERVIEW_CAMPAIGN_ROW)


def test_the_shell_reads_its_rows_and_world_name_from_the_card_rows(client):
    wid, _other, a, _b = _library(client)
    block = client.get("/api/shell", params={"campaign": a}).json()["campaign"]
    assert (block["name"], block["world"], block["world_name"]) == ("A Long Run", wid, "Saltmarch")
    _set(store.worlds.paths.world_meta_path(wid), "name", "")
    assert client.get("/api/shell", params={"campaign": a}).json()["campaign"]["world_name"] == wid
