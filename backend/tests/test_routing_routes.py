"""Per-task routing, end to end (#142).

The guard beside this (`test_routing_guard.py`) proves every generation names a
task and every task belongs to a route. What it cannot prove is that a call site
names the *right* task, or that the campaign id it passes is a campaign at all
-- `routes/characters.py` spells a CHARACTER id `cid`, and a route reading that
as a campaign would silently apply another record's settings.

So these drive the real endpoints with a capturing fake and assert which
connection each was handed. One case per route, for the reason
`test_every_one_shot_generation_route_carries_the_ceiling` gives about the call
budget: routing is applied per call site, so a site that gets it wrong is only
visible from that site.
"""

from __future__ import annotations

import importlib
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import grimoire.store as store
from grimoire import routes
from grimoire.main import create_app

from .llm_fakes import FakeLLM


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    app = create_app()
    with TestClient(app) as c:
        yield c


def _fake(client) -> FakeLLM:
    """A capturing fake with enough scripted turns for absorb's fan-out.

    Its replies are deliberately useless: every assertion here is about which
    CONNECTION a call went to, never about what came back, and a reply a parser
    accepts would invite a test that quietly drifts into asserting the parse.
    """
    fake = FakeLLM([["{}"]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    return fake


def _connection(client, name, model="vendor/x") -> str:
    return client.post("/api/llm-connections", json={
        "kind": "openrouter", "name": name, "model": model,
        "api_key": "sk-" + name}).json()["id"]


def _seed(client):
    """A world, a character, a campaign, a scene with a post -- and a key on the
    active connection, so nothing here is refused for the missing-key reason."""
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-active"})
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara", "version_name": "main"})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Saltmarch"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "Something happened at the docks.")
    store.scenes.append_message(cid, sid, "assistant", "The keeper said nothing.")
    return wid, cid, sid


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


def _drain(response):
    for _ in response.iter_lines():
        pass


# --- one driver per route, so a route is exercised by the URL a user hits ---
def _drive_scene(client, wid, cid, sid):
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": "hello"}) as r:
        _drain(r)


def _drive_opener(client, wid, cid, sid):
    fresh = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "New"}).json()["id"]
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{fresh}/opener",
                       json={"prompt": "open on the pier"}) as r:
        _drain(r)


def _drive_absorb(client, wid, cid, sid):
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")


def _drive_dossier(client, wid, cid, sid):
    # A present NPC, because the phase is a loop over them and an empty cast
    # reaches no provider at all.
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": "mara"})
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/dossiers")


def _drive_summary(client, wid, cid, sid):
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary?force=true")


def _drive_suggestions(client, wid, cid, sid):
    client.post(f"/api/campaigns/{cid}/scene-suggestions")


def _drive_voice(client, wid, cid, sid):
    client.post(f"/api/campaigns/{cid}/characters/mara/voice-anchor/generate")


def _drive_image(client, wid, cid, sid):
    client.put(f"/api/campaigns/{cid}/images/coastline",
               files={"file": ("art.png", _png(), "image/png")})
    client.post(f"/api/campaigns/{cid}/images/coastline/description/draft")


def _drive_tagline(client, wid, cid, sid):
    client.post(f"/api/worlds/{wid}/characters/mara/tagline/generate")


def _drive_scenario(client, wid, cid, sid):
    card = ('{"spec": "chara_card_v3", "spec_version": "3.0", "data": '
            '{"name": "Winifred", "description": "a lamplighter", "extensions": {}}}')
    client.post(f"/api/worlds/{wid}/scenario/parse",
                data={"format": "json"},
                files={"file": ("card.json", card.encode(), "application/json")})


#: route key -> what a user does to reach it. Every route in the registry is
#: here; `test_every_route_has_a_driver` fails if one is added without one,
#: which is the same "no phantoms" rule the guard applies to tasks.
DRIVERS = {
    "scene": _drive_scene,
    "opener": _drive_opener,
    "absorb": _drive_absorb,
    "dossier": _drive_dossier,
    "summary": _drive_summary,
    "suggestions": _drive_suggestions,
    "voice": _drive_voice,
    "image": _drive_image,
    "tagline": _drive_tagline,
    "scenario": _drive_scenario,
}

#: The routes a campaign may override, which is the registry's own answer.
CAMPAIGN_ROUTES = [r.key for r in store.routing.ROUTES if r.campaign_scoped]
GLOBAL_ONLY = [r.key for r in store.routing.ROUTES if not r.campaign_scoped]


def test_every_route_has_a_driver():
    assert set(DRIVERS) == {r.key for r in store.routing.ROUTES}


@pytest.mark.parametrize("route", sorted(DRIVERS))
def test_a_global_route_sends_that_job_to_its_own_connection(client, route):
    wid, cid, sid = _seed(client)
    routed = _connection(client, f"for-{route}")
    client.put("/api/routing", json={"routes": {route: routed}})
    fake = _fake(client)

    DRIVERS[route](client, wid, cid, sid)

    assert fake.requests, f"{route}: nothing reached the provider"
    assert {r["conn"]["id"] for r in fake.requests} == {routed}, (
        f"{route} did not run on the connection it was routed to")


@pytest.mark.parametrize("route", sorted(DRIVERS))
def test_an_unset_route_still_runs_on_the_active_connection(client, route):
    """The whole change is invisible until someone asks for it."""
    wid, cid, sid = _seed(client)
    fake = _fake(client)

    DRIVERS[route](client, wid, cid, sid)

    assert fake.requests
    assert {r["conn"]["id"] for r in fake.requests} == {"openrouter"}


@pytest.mark.parametrize("route", sorted(CAMPAIGN_ROUTES))
def test_a_campaign_override_beats_the_global_route(client, route):
    wid, cid, sid = _seed(client)
    globally = _connection(client, f"global-{route}")
    locally = _connection(client, f"local-{route}")
    client.put("/api/routing", json={"routes": {route: globally}})
    r = client.put(f"/api/campaigns/{cid}/routing", json={"routes": {route: locally}})
    assert r.status_code == 200
    fake = _fake(client)

    DRIVERS[route](client, wid, cid, sid)

    assert {req["conn"]["id"] for req in fake.requests} == {locally}


@pytest.mark.parametrize("route", sorted(GLOBAL_ONLY))
def test_a_world_scoped_route_takes_no_campaign_override(client, route):
    """`routes/characters.py` calls a CHARACTER id `cid`. A route that read it
    as a campaign would apply another record's settings, and the spelling is
    what would hide it."""
    _wid, cid, _sid = _seed(client)
    r = client.put(f"/api/campaigns/{cid}/routing",
                   json={"routes": {route: _connection(client, "nope")}})
    assert r.status_code == 400
    assert route in r.json()["detail"]


def test_absorbs_phases_each_follow_their_own_route(client):
    """Absorb runs four jobs at once -- extraction, the per-NPC dossier loop,
    voice drift and the mechanics audit -- and they used to share one resolved
    connection, which would have made three of those four settings do nothing
    whenever an absorb was what ran them."""
    _wid, cid, sid = _seed(client)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": "mara"})
    extraction = _connection(client, "extraction")
    dossiers = _connection(client, "dossiers")
    client.put("/api/routing", json={"routes": {"absorb": extraction, "dossier": dossiers}})
    fake = _fake(client)

    client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")

    used = {r["conn"]["id"] for r in fake.requests}
    assert extraction in used, "the extraction did not use the absorb route"
    assert dossiers in used, "the dossier loop did not use the dossier route"


def test_a_scene_turn_and_its_retry_share_the_one_route(client):
    """#142 named the scene turn's retries and director turns as part of ONE
    task, so a reader who sets "Scene turns" gets all of them."""
    _wid, cid, sid = _seed(client)
    routed = _connection(client, "prose")
    client.put("/api/routing", json={"routes": {"scene": routed}})
    fake = _fake(client)

    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": "hello"}) as r:
        _drain(r)
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/retry", json={}) as r:
        _drain(r)

    assert {req["conn"]["id"] for req in fake.requests} == {routed}
    assert len(fake.requests) >= 2


# --- the failure modes ---
def test_a_route_naming_a_deleted_connection_falls_back_rather_than_failing(client):
    """A delete clears the config keys; it cannot reach into every campaign's
    frontmatter. So a stale campaign override degrades to the next scope."""
    _wid, cid, _sid = _seed(client)
    doomed = _connection(client, "doomed")
    client.put(f"/api/campaigns/{cid}/routing", json={"routes": {"suggestions": doomed}})
    assert client.delete(f"/api/llm-connections/{doomed}").status_code == 200
    fake = _fake(client)

    client.post(f"/api/campaigns/{cid}/scene-suggestions")

    assert {r["conn"]["id"] for r in fake.requests} == {"openrouter"}


def test_deleting_a_connection_clears_it_from_the_global_routes(client):
    _seed(client)
    doomed = _connection(client, "doomed")
    client.put("/api/routing", json={"routes": {"summary": doomed}})
    assert client.get("/api/routing").json()["routes"]["summary"] == doomed
    client.delete(f"/api/llm-connections/{doomed}")
    assert client.get("/api/routing").json()["routes"]["summary"] == ""


def test_a_routed_connection_that_cannot_send_is_reported_not_silently_replaced(client):
    """The opposite case, and the opposite answer. A keyless connection is a
    setup mistake the user made on purpose; generating on the active connection
    instead would play a scene on a model they did not choose."""
    _wid, cid, _sid = _seed(client)
    keyless = client.post("/api/llm-connections",
                          json={"kind": "openrouter", "name": "Keyless"}).json()["id"]
    client.put("/api/routing", json={"routes": {"suggestions": keyless}})
    _fake(client)

    r = client.post(f"/api/campaigns/{cid}/scene-suggestions")

    assert r.status_code == 409
    # The app's LLM-error handler flattens a dict detail onto the body, which is
    # what every other missing_key refusal already looks like to the client.
    body = r.json()
    assert body["kind"] == "missing_key"
    assert "Keyless" in body["detail"] and "scene suggestions" in body["detail"].lower()


def test_a_route_naming_no_connection_is_refused_at_the_door(client):
    """Tolerated on read, refused on write -- see `_routing_fields`. A typo
    stored as a setting would sit on the page routing nothing."""
    wid, cid, sid = _seed(client)
    r = client.put("/api/routing", json={"routes": {"scene": "no-such-connection"}})
    assert r.status_code == 400 and "no-such-connection" in r.json()["detail"]
    assert client.get("/api/routing").json()["routes"]["scene"] == ""


def test_clearing_a_global_route_is_not_mistaken_for_a_bad_connection(client):
    _seed(client)
    routed = _connection(client, "temporary")
    client.put("/api/routing", json={"routes": {"summary": routed}})
    assert client.put("/api/routing", json={"routes": {"summary": ""}}).status_code == 200
    assert client.get("/api/routing").json()["routes"]["summary"] == ""


def test_an_unroutable_scope_is_refused_before_anything_is_written(client):
    _wid, cid, _sid = _seed(client)
    before = client.get(f"/api/campaigns/{cid}/routing").json()["routes"]
    r = client.put(f"/api/campaigns/{cid}/routing",
                   json={"routes": {"scene": _connection(client, "a"),
                                    "tagline": _connection(client, "b")}})
    assert r.status_code == 400
    assert "tagline" in r.json()["detail"]
    assert client.get(f"/api/campaigns/{cid}/routing").json()["routes"] == before


def test_the_bundle_says_where_each_effective_value_came_from(client):
    _wid, cid, _sid = _seed(client)
    globally = _connection(client, "wide")
    locally = _connection(client, "narrow")
    client.put("/api/routing", json={"routes": {"scene": globally, "absorb": globally}})
    client.put(f"/api/campaigns/{cid}/routing", json={"routes": {"scene": locally}})

    body = client.get(f"/api/campaigns/{cid}/routing").json()
    assert body["routes"]["scene"] == locally            # what this scope says
    assert body["routes"]["absorb"] == ""                # the global key is not its own
    assert body["effective"]["scene"] == locally
    assert body["effective"]["absorb"] == globally
    assert body["effective"]["summary"] == ""            # inherits the active connection
    assert body["provenance"] == {**body["provenance"],
                                  "scene": {"scope": "campaign"},
                                  "absorb": {"scope": "global"},
                                  "summary": {"scope": "active"}}
    assert body["active_connection_id"] == "openrouter"
    assert {c["id"] for c in body["connections"]} >= {globally, locally}


def test_clearing_a_campaign_route_removes_the_key_rather_than_emptying_it(client):
    """Ten empty keys in every campaign that ever opened the picker is noise in
    a file people read by hand -- and the resolver cannot tell them from absent."""
    _wid, cid, _sid = _seed(client)
    routed = _connection(client, "temporary")
    client.put(f"/api/campaigns/{cid}/routing", json={"routes": {"scene": routed}})
    client.put(f"/api/campaigns/{cid}/routing", json={"routes": {"scene": ""}})
    meta = store.campaigns.read_campaign(cid)["meta"]
    assert "route_scene" not in meta


def test_a_campaign_routing_write_does_not_disturb_the_rest_of_the_frontmatter(client):
    _wid, cid, _sid = _seed(client)
    before = store.campaigns.read_campaign(cid)["meta"]
    client.put(f"/api/campaigns/{cid}/routing",
               json={"routes": {"scene": _connection(client, "x")}})
    after = store.campaigns.read_campaign(cid)["meta"]
    assert after["name"] == before["name"] and after["world"] == before["world"]
    assert after["created"] == before["created"]
    assert after["updated"] >= before["updated"]


def test_routing_a_campaign_that_does_not_exist_is_a_404(client):
    assert client.get("/api/campaigns/nope/routing").status_code == 404
    assert client.put("/api/campaigns/nope/routing", json={"routes": {}}).status_code == 404


def test_the_global_bundle_offers_every_route_and_the_campaign_one_does_not(client):
    _seed(client)
    assert {c["key"] for c in client.get("/api/routing").json()["catalog"]} == set(DRIVERS)
    _wid, cid, _sid = _seed(client)
    keys = {c["key"] for c in client.get(f"/api/campaigns/{cid}/routing").json()["catalog"]}
    assert keys == set(CAMPAIGN_ROUTES)
