"""04-S6: the shell's open-scene turn counts, remembered in-process.

`overview.scene_turns` keeps the count's algorithm -- `read_scene` and
`_model_blocks` -- and remembers it on the scene file's stamp and the seated
players' names, so a second shell read parses no unchanged open transcript, and
a cast change misses rather than answering for the old cast.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app
from grimoire.store import overview, readstats
from grimoire.store.overview import kinds
from grimoire.store.overview import read as overview_read
from tests.overview_support import clear_memos, clock_forward


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    clear_memos()
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def scene(client):
    wid = client.post("/api/worlds", json={"name": "Saltmarch"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "A Long Run", "world": wid}).json()["id"]
    sid = store.scenes.create_scene(cid, "The Lower Step")
    store.scenes.append_message(cid, sid, "user", "I open the door.", speaker="You")
    store.scenes.append_message(cid, sid, "assistant", "The door creaks.", speaker="Mara")
    store.scenes.append_message(cid, sid, "assistant", "A shadow moves.", speaker="Winifred")
    return cid, sid


def _turns(client, cid):
    return client.get("/api/shell", params={"campaign": cid}).json()["campaign"]["open"][0]["turns"]


def test_a_second_shell_read_parses_no_unchanged_open_transcript(client, scene, monkeypatch):
    clock_forward(monkeypatch)
    cid, _sid = scene
    assert _turns(client, cid) == 2
    with readstats.collect() as counters:
        assert _turns(client, cid) == 2
    assert counters.get("transcript.parse") == 0
    assert counters.get("overview.computed.scene_turns") == 0


def test_an_append_is_counted_on_the_next_read(client, scene, monkeypatch):
    clock_forward(monkeypatch)
    cid, sid = scene
    assert _turns(client, cid) == 2
    store.scenes.append_message(cid, sid, "assistant", "The bells stop.", speaker="Mara")
    assert _turns(client, cid) == 3


def test_a_player_cast_change_misses_on_players(client, scene, monkeypatch):
    clock_forward(monkeypatch)
    cid, _sid = scene
    assert _turns(client, cid) == 2
    # Mara is seated as a player: her lines are user-side now. The file's
    # bytes did not move, so only the players in the key can notice.
    monkeypatch.setattr(overview_read.cast, "player_names", lambda c, s: ["Mara"])
    monkeypatch.setattr(store.scenes.read.cast, "player_names", lambda c, s: ["Mara"])
    with readstats.collect() as counters:
        assert _turns(client, cid) == 1
    assert counters.get("overview.computed.scene_turns") == 1


def test_a_failed_player_read_is_null_and_remembers_nothing(client, scene, monkeypatch):
    cid, _sid = scene

    def boom(c, s):
        raise OSError("appearances unreadable")
    monkeypatch.setattr(overview_read.cast, "player_names", boom)
    assert _turns(client, cid) is None


def test_missing_scene_raises_scene_not_found(client, scene):
    cid, _sid = scene
    with pytest.raises(store.SceneNotFound):
        overview.scene_turns(cid, "no-such-scene")


def test_the_byte_fed_compute_agrees_with_the_live_path(scene):
    cid, sid = scene
    p = store.scenes.paths._scene_path(cid, sid)
    players = tuple(sorted(store.appearances.cast.player_names(cid, sid)))
    assert kinds.scene_turns(p.read_bytes(), players) == kinds.scene_turns_live(cid, sid) == 2
    p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
    assert kinds.scene_turns(p.read_bytes(), players) == kinds.scene_turns_live(cid, sid)
    p.write_bytes(p.read_bytes() + b"\xff")
    assert kinds.scene_turns(p.read_bytes(), players) is None
    with pytest.raises(UnicodeDecodeError):
        kinds.scene_turns_live(cid, sid)
    assert overview.scene_turns(cid, sid) is None
