"""The in-app import of an existing transcript (#92), over HTTP.

The store side is `test_scene_import_store.py`; what is asserted here is the
half only the routes own: that parsing writes nothing, that a draft the
reviewer confirmed lands as one scene or as none at all, and that an imported
scene is an ordinary scene afterwards -- absorb included, which is the "full
post-processing pipeline" half of the issue's title.
"""

from __future__ import annotations

import grimoire.store as store
from grimoire import routes
from tests.llm_fakes import FakeOpenRouterComplete

STORED = """---
title: The Long Quay
time_history: 2026-01-02
location_history: the-quay
---

**You:** I walk the quay looking for Mara.

**Mara:** "You found me. Now what?"
"""


def _campaign(client, name="Run"):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    return wid, client.post("/api/campaigns", json={"name": name, "world": wid}).json()["id"]


def _upload(client, cid: str, text: str, filename: str = "scene.md"):
    return client.post(f"/api/campaigns/{cid}/scenes/import/parse",
                       files={"file": (filename, text.encode(), "text/markdown")})


def _commit(client, cid: str, draft: dict, **over):
    body = {"title": draft["title"], "date": draft["date"], "location": draft["location"],
            "pcless": draft["pcless"], "messages": draft["messages"],
            "cast": [{"kind": c["kind"], "id": c["id"], "role": c["role"]}
                     for c in draft["cast"]]}
    body.update(over)
    return client.post(f"/api/campaigns/{cid}/scenes/import", json=body)


# ---- parse ----
def test_parse_returns_a_draft_and_creates_nothing(client):
    wid, cid = _campaign(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara", "version_name": "main"})
    client.post(f"/api/campaigns/{cid}/locations", json={"name": "The Quay"})

    r = _upload(client, cid, STORED)
    assert r.status_code == 200
    draft = r.json()
    assert draft["title"] == "The Long Quay"
    assert draft["date"] == "2026-01-02" and draft["location"] == "the-quay"
    assert [c["id"] for c in draft["cast"]] == ["mara"]
    assert client.get(f"/api/campaigns/{cid}/scenes").json() == []


def test_parse_reports_a_speaker_the_campaign_does_not_have(client):
    """The issue's own constraint: an actor an import references has to exist
    here already, and the review step is where that has to surface."""
    _wid, cid = _campaign(client)
    draft = _upload(client, cid, STORED).json()
    assert draft["unmatched"] == ["Mara"] and draft["cast"] == []


def test_parse_rejects_a_file_that_is_not_a_transcript(client):
    _wid, cid = _campaign(client)
    r = _upload(client, cid, "just some prose\n")
    assert r.status_code == 400 and "could not parse" in r.json()["detail"]


def test_parse_404s_for_an_unknown_campaign(client):
    r = _upload(client, "nope", STORED)
    assert r.status_code == 404


# ---- commit ----
def test_commit_creates_the_scene(client):
    wid, cid = _campaign(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara", "version_name": "main"})
    client.post(f"/api/campaigns/{cid}/locations", json={"name": "The Quay"})
    draft = _upload(client, cid, STORED).json()

    r = _commit(client, cid, draft)
    assert r.status_code == 200
    sid = r.json()["id"]

    scene = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()
    assert scene["meta"]["title"] == "The Long Quay"
    assert [m["content"] for m in scene["messages"]] == [
        "I walk the quay looking for Mara.", '"You found me. Now what?"']
    assert [a["id"] for a in client.get(f"/api/campaigns/{cid}/scenes/{sid}/cast").json()] == ["mara"]
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/datetime").json()["history"] == ["2026-01-02"]


def test_an_unknown_actor_leaves_no_scene_behind(client):
    """Resolved before the create, so the commonest failure an import has
    doesn't cost the reviewer a stray half-scene to clean up."""
    _wid, cid = _campaign(client)
    draft = _upload(client, cid, STORED).json()
    r = _commit(client, cid, draft, cast=[{"kind": "characters", "id": "mara", "role": "npc"}])
    assert r.status_code == 404
    assert client.get(f"/api/campaigns/{cid}/scenes").json() == []


def test_a_player_cannot_be_seated_in_an_offscreen_import(client):
    wid, cid = _campaign(client)
    client.post(f"/api/worlds/{wid}/pcs", json={"name": "Seraphine", "version_name": "main"})
    draft = _upload(client, cid, "**Seraphine:** I put my hand on the ledger.\n").json()
    r = _commit(client, cid, draft, pcless=True)
    assert r.status_code == 400
    assert client.get(f"/api/campaigns/{cid}/scenes").json() == []


def test_a_date_the_calendar_refuses_leaves_no_scene_behind(client):
    """The failures that can only be found after the create still clean up:
    half an imported transcript reads like a scene."""
    _wid, cid = _campaign(client)
    draft = _upload(client, cid, STORED).json()
    r = _commit(client, cid, draft, date="the second of never", cast=[])
    assert r.status_code == 400
    assert client.get(f"/api/campaigns/{cid}/scenes").json() == []


def test_a_location_that_vanished_between_review_and_commit_leaves_no_scene(client):
    _wid, cid = _campaign(client)
    draft = _upload(client, cid, STORED).json()
    r = _commit(client, cid, draft, location="the-quay", cast=[])
    assert r.status_code == 400
    assert client.get(f"/api/campaigns/{cid}/scenes").json() == []


def test_an_empty_import_is_refused(client):
    _wid, cid = _campaign(client)
    r = client.post(f"/api/campaigns/{cid}/scenes/import", json={"title": "Nothing", "messages": []})
    assert r.status_code == 400
    assert client.get(f"/api/campaigns/{cid}/scenes").json() == []


def test_a_role_the_transcript_cannot_hold_is_refused_at_the_boundary(client):
    """The serializer looks an unknown role up in `ROLE_TO_LABEL`, which raises
    `KeyError` -- a 500 from inside the transcript write, on a value the request
    boundary can reject."""
    _wid, cid = _campaign(client)
    r = client.post(f"/api/campaigns/{cid}/scenes/import",
                    json={"title": "T", "messages": [{"role": "banana", "content": "hi"}]})
    assert r.status_code == 422
    assert client.get(f"/api/campaigns/{cid}/scenes").json() == []


def test_commit_404s_for_an_unknown_campaign(client):
    r = client.post("/api/campaigns/nope/scenes/import",
                    json={"title": "T", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 404


# ---- what an imported scene is afterwards ----
def test_an_imported_scene_absorbs_like_a_played_one(client):
    """The other half of the issue's title. Nothing about absorb is special-cased
    for an import -- the scene it reads is an ordinary transcript."""
    wid, cid = _campaign(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara", "version_name": "main"})
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    draft = _upload(client, cid, STORED).json()
    sid = _commit(client, cid, draft, location="").json()["id"]

    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouterComplete(
        '{"one_line": "They met on the quay.", "summary": "Mara was found.",'
        ' "keywords": ["quay"], "timeline_events": []}')
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    assert r.status_code == 200 and r.json()["one_line"] == "They met on the quay."


def test_an_imported_scene_can_be_played_on(client):
    """The transcript an import writes is the transcript the play loop appends
    to: no separate shape, no import-only state to reconcile."""
    _wid, cid = _campaign(client)
    draft = _upload(client, cid, STORED).json()
    sid = _commit(client, cid, draft, location="", date="", cast=[]).json()["id"]
    store.scenes.append_message(cid, sid, "user", "I keep walking.")
    assert len(client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]) == 3
