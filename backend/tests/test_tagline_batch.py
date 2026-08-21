"""POST /worlds/{wid}/characters/taglines/generate — derive the taglines a bulk
import left blank, in one pass over the roster (#57).

Per-character `tagline/generate` is a *preview*: it returns a sentence and
writes nothing, so the popup's Skip leaves no trace (#59). This route is the
other half of that pair and deliberately does not behave that way — a roster of
three hundred imported cards is not reviewed one modal at a time, and the
request to derive the whole roster IS the consent. So it writes as it goes, and
the rules that keep that safe are what this file pins:

  * only a character whose tagline is BLANK is ever a target, and the blank is
    re-checked immediately before the write, so a sentence written by hand
    mid-run survives (the re-check is a fresh read, not a lock -- see the
    route's own note on how far that reaches);
  * the first provider failure stops the run rather than spending the roster's
    worth of calls on a broken key — and stopping is cheap, because a re-run
    targets whatever is still blank, which is exactly what the failed run left;
  * one unusable card is skipped, not fatal.
"""

import importlib
import json

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import routes
from grimoire.main import create_app
from tests.llm_fakes import FailingOpenRouter, FakeOpenRouterComplete


@pytest.fixture
def client(monkeypatch, tmp_path):
    """conftest's `client`, minus its default fake.

    Shadowed deliberately: every test here says what the provider answers, and
    the shared fixture's stand-in would let one that forgot quietly derive
    "Hello" for the whole roster and still pass.
    """
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    with TestClient(create_app()) as c:
        yield c


def _world(client, name="Realm"):
    wid = client.post("/api/worlds", json={"name": name}).json()["id"]
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    return wid


def _character(client, wid, name):
    return client.post(f"/api/worlds/{wid}/characters",
                       json={"name": name, "version_name": "main"}).json()["character"]


def _answers(client, replies):
    """One fake for the whole request, so a test can count its calls."""
    fake = FakeOpenRouterComplete(replies)
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    return fake


def _fails(client, **kw):
    fake = FailingOpenRouter(**kw)
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    return fake


def _derive(client, wid):
    resp = client.post(f"/api/worlds/{wid}/characters/taglines/generate")
    frames = [json.loads(ln[len("data:"):].strip())
              for ln in resp.text.splitlines() if ln.startswith("data:")]
    return resp, frames


def _tagline(client, wid, cid):
    return client.get(f"/api/worlds/{wid}/characters/{cid}/tagline").json()["tagline"]


def test_derives_only_the_blank_taglines(client):
    wid = _world(client)
    mara, seraphine, winifred = (_character(client, wid, n)
                                 for n in ("Mara", "Seraphine", "Winifred"))
    client.put(f"/api/worlds/{wid}/characters/{seraphine}/tagline",
               json={"tagline": "Already said."})
    fake = _answers(client, ["A courier with cold hands.", "A locksmith who never sleeps."])

    resp, frames = _derive(client, wid)

    assert resp.status_code == 200
    # Two calls, not three: the character who already has a sentence is not a target.
    assert fake.calls == 2
    assert _tagline(client, wid, seraphine) == "Already said."
    assert _tagline(client, wid, mara) == "A courier with cold hands."
    assert _tagline(client, wid, winifred) == "A locksmith who never sleeps."
    assert frames[0] == {"total": 2}
    assert frames[-1]["summary"] == {"total": 2, "written": 2, "skipped": 0, "stopped": False}


def test_streams_a_frame_per_character_as_each_lands(client):
    wid = _world(client)
    _character(client, wid, "Mara")
    _answers(client, ["A courier with cold hands."])

    _, frames = _derive(client, wid)

    assert frames[1] == {"done": 1, "character": "mara", "name": "Mara",
                         "tagline": "A courier with cold hands."}


def test_an_empty_backlog_calls_nobody(client):
    wid = _world(client)
    cid = _character(client, wid, "Mara")
    client.put(f"/api/worlds/{wid}/characters/{cid}/tagline", json={"tagline": "Said."})
    fake = _answers(client, ["never asked"])

    _, frames = _derive(client, wid)

    assert fake.calls == 0
    assert frames[0] == {"total": 0}
    assert frames[-1]["summary"] == {"total": 0, "written": 0, "skipped": 0, "stopped": False}


def test_a_roster_with_no_characters_is_not_an_error(client):
    wid = _world(client)
    resp, frames = _derive(client, wid)
    assert resp.status_code == 200
    assert frames[0] == {"total": 0}


def test_requires_a_connection_before_streaming_anything(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    _character(client, wid, "Mara")
    _answers(client, ["never asked"])

    resp = client.post(f"/api/worlds/{wid}/characters/taglines/generate")

    # 409 with a JSON body, not a 200 whose first frame carries the bad news:
    # nothing has been attempted yet, so this is an ordinary refusal.
    assert resp.status_code == 409
    assert "data:" not in resp.text


def test_unknown_world_is_404(client):
    resp = client.post("/api/worlds/nope/characters/taglines/generate")
    assert resp.status_code == 404


def test_stops_at_the_first_provider_failure(client):
    wid = _world(client)
    mara = _character(client, wid, "Mara")
    winifred = _character(client, wid, "Winifred")
    fake = _fails(client, kind="rate_limit", message="slow down")

    _, frames = _derive(client, wid)

    # One call, not one per character: a key that is out of quota is out of
    # quota for the whole roster, and finding that out three hundred times is
    # the expensive way to learn it.
    assert fake.calls == 1
    assert _tagline(client, wid, mara) == ""
    assert _tagline(client, wid, winifred) == ""
    assert frames[-2]["error"] == {"detail": "slow down", "kind": "rate_limit"}
    assert frames[-2]["character"] == "mara"
    assert frames[-1]["summary"] == {"total": 2, "written": 0, "skipped": 0, "stopped": True}


def test_keeps_what_landed_before_the_failure(client):
    wid = _world(client)
    _character(client, wid, "Mara")
    _character(client, wid, "Winifred")
    _fails(client, deltas=["A courier with cold hands."], fail_after=1)

    _, frames = _derive(client, wid)

    assert _tagline(client, wid, "mara") == "A courier with cold hands."
    assert _tagline(client, wid, "winifred") == ""
    assert frames[-1]["summary"] == {"total": 2, "written": 1, "skipped": 0, "stopped": True}


def test_a_blank_reply_is_skipped_not_written(client):
    wid = _world(client)
    cid = _character(client, wid, "Mara")
    _answers(client, ["   \n  "])

    _, frames = _derive(client, wid)

    assert _tagline(client, wid, cid) == ""
    assert frames[1] == {"done": 1, "character": "mara", "name": "Mara", "skipped": "blank"}
    assert frames[-1]["summary"] == {"total": 1, "written": 0, "skipped": 1, "stopped": False}


def test_a_tagline_written_during_the_run_is_not_overwritten(client, monkeypatch):
    wid = _world(client)
    cid = _character(client, wid, "Mara")
    _answers(client, ["A courier with cold hands."])
    root = store.worlds.world_root(wid)
    real_read_card = store.characters.read_card

    def racing_read_card(r, c, v):
        # Stands in for another request landing between the roster scan and the
        # write — the window the second blank-check exists to close.
        store.taglines.write(root, c, "Hand-written, mid-run.")
        return real_read_card(r, c, v)

    monkeypatch.setattr(store.characters, "read_card", racing_read_card)

    _, frames = _derive(client, wid)

    assert _tagline(client, wid, cid) == "Hand-written, mid-run."
    assert frames[1]["skipped"] == "already set"
    assert frames[-1]["summary"] == {"total": 1, "written": 0, "skipped": 1, "stopped": False}


def test_a_card_with_no_data_is_prompted_from_nothing_rather_than_500ing(client):
    wid = _world(client)
    cid = _character(client, wid, "Mara")
    vid = client.get(f"/api/worlds/{wid}/characters/{cid}").json()["meta"]["default_version"]
    # Version PUT accepts ANY dict as a card and writes it unchanged, so this is
    # supported state, not a corrupt store (see the voice-anchor route's note).
    client.put(f"/api/worlds/{wid}/characters/{cid}/versions/{vid}", json={"card": {}})
    fake = _answers(client, ["A courier with cold hands."])

    resp, _ = _derive(client, wid)

    assert resp.status_code == 200
    assert fake.calls == 1
    assert _tagline(client, wid, cid) == "A courier with cold hands."


def test_an_unreadable_card_is_skipped_and_the_run_continues(client, monkeypatch):
    wid = _world(client)
    _character(client, wid, "Mara")
    _character(client, wid, "Winifred")
    fake = _answers(client, ["A locksmith who never sleeps."])
    real_read_card = store.characters.read_card

    def one_bad_card(r, c, v):
        if c == "mara":
            raise store.characters.VersionNotFound(v)
        return real_read_card(r, c, v)

    monkeypatch.setattr(store.characters, "read_card", one_bad_card)

    _, frames = _derive(client, wid)

    assert fake.calls == 1
    assert _tagline(client, wid, "winifred") == "A locksmith who never sleeps."
    assert frames[1] == {"done": 1, "character": "mara", "name": "Mara",
                         "skipped": "unreadable card"}
    assert frames[-1]["summary"] == {"total": 2, "written": 1, "skipped": 1, "stopped": False}


def test_each_call_is_metered_as_a_tagline(client):
    wid = _world(client)
    _character(client, wid, "Mara")
    _character(client, wid, "Winifred")
    _answers(client, ["A courier with cold hands.", "A locksmith who never sleeps."])

    _derive(client, wid)

    by_task = {b["key"]: b["calls"] for b in store.usage.summary(days=1)["by_task"]}
    assert by_task == {"tagline": 2}


def test_a_failed_write_becomes_an_error_frame_rather_than_a_dead_stream(client, monkeypatch):
    """The response is a 200 the moment the first frame goes out, so a failure
    after that has nowhere to go but a frame — and the summary still follows,
    because "what did this run manage" has an answer even after a crash."""
    wid = _world(client)
    _character(client, wid, "Mara")
    _character(client, wid, "Winifred")
    _answers(client, ["A courier with cold hands."])

    def no_disk(*a, **k):
        raise OSError("read-only file system")

    monkeypatch.setattr(store.taglines, "write", no_disk)

    resp, frames = _derive(client, wid)

    assert resp.status_code == 200
    assert frames[-2]["error"] == {"detail": "read-only file system", "kind": "tagline"}
    assert frames[-1]["summary"] == {"total": 2, "written": 0, "skipped": 0, "stopped": True}
