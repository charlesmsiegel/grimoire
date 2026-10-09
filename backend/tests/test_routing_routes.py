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
from grimoire.store import inference_keys as keys
from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter

from . import draft_runs as drafts
from . import review_runs
from .inference_fixtures import put_settings
from .llm_fakes import FakeLLM

pytestmark = pytest.mark.upgraded_birth


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


#: The model a route is pinned at, unless a test names another.
MODEL = "vendor/x"


def _connection(client, name) -> str:
    return client.post("/api/llm-connections", json={
        "kind": "openrouter", "name": name, "api_key": "sk-" + name}).json()["id"]


def _pins(model: str, routes: dict[str, str]) -> dict:
    """An inference settings body pinning each route to its provider at `model`."""
    return {"routes": {k: {"use": keys.PIN, "pin": {"provider": v, "model": model}}
                       for k, v in routes.items()}}


def _route(client, model: str = MODEL, **routes: str) -> None:
    """Global route choices: each named route pinned to its own provider (the
    `/routing` endpoints that wrote the legacy choices were retired in
    inference slice C)."""
    put_settings(client, _pins(model, routes))


def _campaign_route(client, cid: str, model: str = MODEL, **routes: str) -> None:
    """A campaign's own route pins (`_route`'s campaign half)."""
    got = client.put(f"/api/campaigns/{cid}/inference", json=_pins(model, routes))
    assert got.status_code == 200, got.text


def _seed(client):
    """A world, a character, a campaign, a scene with a post -- and a key on the
    Primary role's provider, so nothing here is refused for the missing-key
    reason."""
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
    # An empty cast and no resolved module, so the extraction is the only phase
    # that reaches a provider -- which is what lets the assertions here be "every
    # request went to this connection" rather than "one of them did". The
    # fan-out's own routing is `test_absorbs_phases_each_follow_their_own_route`.
    #
    # Driven to its answer rather than posted at: absorb is detached (#396), so
    # the POST returns before a single phase has reached the fake and "which
    # connection was used" has nothing to read yet.
    review_runs.absorb(client, cid, sid)


def _drive_dossier(client, wid, cid, sid):
    # A present NPC, because the phase is a loop over them and an empty cast
    # reaches no provider at all.
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": "mara"})
    # The route is a RETRY of one phase of an open review (#396), so the absorb
    # that opens the review is setup rather than the call under test -- and its
    # extraction runs on the absorb route, which would leave a second connection
    # in the set the caller is about to read. So: absorb, forget what it used,
    # then retry the phase this route actually names.
    review_runs.absorb(client, cid, sid)
    client.app.dependency_overrides[routes.get_llm]().requests.clear()
    review_runs.dossiers(client, cid, sid)


def _drive_summary(client, wid, cid, sid):
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary?force=true")


def _drive_suggestions(client, wid, cid, sid):
    drafts.post(client, f"/api/campaigns/{cid}/scene-suggestions")


def _drive_voice(client, wid, cid, sid):
    drafts.post(client, f"/api/campaigns/{cid}/characters/mara/voice-anchor/generate")


def _drive_image(client, wid, cid, sid):
    client.put(f"/api/campaigns/{cid}/images/coastline",
               files={"file": ("art.png", _png(), "image/png")})
    drafts.post(client, f"/api/campaigns/{cid}/images/coastline/description/draft")


def _drive_tagline(client, wid, cid, sid):
    drafts.post(client, f"/api/worlds/{wid}/characters/mara/tagline/generate")


def _drive_scenario(client, wid, cid, sid):
    card = ('{"spec": "chara_card_v3", "spec_version": "3.0", "data": '
            '{"name": "Winifred", "description": "a lamplighter", "extensions": {}}}')
    drafts.post(client, f"/api/worlds/{wid}/scenario/parse",
                data={"format": "json"},
                files={"file": ("card.json", card.encode(), "application/json")})


def _drive_tracker(client, wid, cid, sid):
    # An adopted opener rather than a chat: a chat's own turn would reach the
    # provider on the `scene` route, and every assertion here is "EVERY request
    # went to this connection". Adoption makes no call of its own, so the only
    # requests are the tracker updates it schedules. The suite runs with the
    # tracker off (conftest), so this campaign opts in.
    client.put(f"/api/campaigns/{cid}/tracker", json={"setting": "on"})
    fresh = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Tideline"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{fresh}/first-post",
                json={"text": "The lamps along the quay gutter out."})
    # Detached `background` runs: the POST answers before they reach the
    # provider, so wait for them as `test_tracker_flow.py` does.
    identity = store.scenes.scene_identity(cid, fresh)
    updates = [r for r in client.app.state.runs.for_subject(("scene", cid, identity))
               if r.cls == "background" and r.kind == "tracker-update"]
    assert updates, "adopting an opener scheduled no tracker update"
    for run in updates:
        assert run.terminal.wait(timeout=10), "a tracker update never finished"


def _drive_continuity(client, wid, cid, sid):
    # The duplicate check beside absorb runs only when a proposed record has a
    # plausible stored neighbour, so one is seeded -- in the absorbed scene
    # itself, so the structural clause applies too, though the proposed title
    # clears the lexical floors on its own. Every call answers with the
    # extraction: the check reads it as an object holding no item (the phase
    # is `degraded`), and the assertion is only about which
    # connection the check used, so the extraction's request is forgotten.
    pid, title, beat = review_runs.LEDGER_THREAD
    store.plot.set_movement(cid, pid, title, "open", beat, sid)
    fake = client.app.dependency_overrides[routes.get_llm]()
    fake.turns = [[review_runs.EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER]]
    review_runs.absorb(client, cid, sid)
    fake.requests[:] = review_runs.identity_requests(fake)
    assert fake.requests, "the absorb made no identity call"


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
    "tracker": _drive_tracker,
    "continuity": _drive_continuity,
}

#: The routes a campaign may override, which is the registry's own answer.
CAMPAIGN_ROUTES = [r.key for r in store.routing.LEGACY_ROUTES if r.campaign_scoped]
GLOBAL_ONLY = [r.key for r in store.routing.LEGACY_ROUTES if not r.campaign_scoped]


def test_the_routing_endpoints_are_gone(client):
    """The legacy routing surface is retired (slice C, Task 8): roles and
    routes are read and written at `/api/inference/settings` and
    `/api/campaigns/{cid}/inference`."""
    _wid, cid, _sid = _seed(client)
    for path in ("/api/routing", f"/api/campaigns/{cid}/routing"):
        assert client.get(path).status_code in (404, 405), path
        assert client.put(path, json={"routes": {}}).status_code in (404, 405), path
    assert not any(getattr(r, "path", "").endswith("/routing") for r in client.app.routes)


def test_every_route_has_a_driver():
    assert set(DRIVERS) == {r.key for r in store.routing.LEGACY_ROUTES}


@pytest.mark.parametrize("route", sorted(DRIVERS))
def test_a_global_route_sends_that_job_to_its_own_connection(client, route):
    wid, cid, sid = _seed(client)
    routed = _connection(client, f"for-{route}")
    _route(client, **{route: routed})
    fake = _fake(client)

    DRIVERS[route](client, wid, cid, sid)

    assert fake.requests, f"{route}: nothing reached the provider"
    assert {r["conn"]["id"] for r in fake.requests} == {routed}, (
        f"{route} did not run on the connection it was routed to")


@pytest.mark.parametrize("route", sorted(DRIVERS))
def test_an_unset_route_still_runs_on_the_primary_role(client, route):
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
    _route(client, **{route: globally})
    _campaign_route(client, cid, **{route: locally})
    fake = _fake(client)

    DRIVERS[route](client, wid, cid, sid)

    assert {req["conn"]["id"] for req in fake.requests} == {locally}


@pytest.mark.parametrize("route", sorted(GLOBAL_ONLY))
def test_a_world_scoped_route_takes_no_campaign_override(client, route):
    """`routes/characters.py` calls a CHARACTER id `cid`. A route that read it
    as a campaign would apply another record's settings, and the spelling is
    what would hide it."""
    wid, cid, sid = _seed(client)
    # Written by hand: no writer stores a world-scoped key in a campaign.
    path = store.campaigns.campaign_meta_path(cid)
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    meta[keys.use_key(route)] = keys.PIN
    meta[keys.pin_key(route, "provider")] = _connection(client, "nope")
    meta[keys.pin_key(route, "model")] = MODEL
    path.write_text(dump_frontmatter(meta, body), encoding="utf-8")
    fake = _fake(client)

    DRIVERS[route](client, wid, cid, sid)

    assert {req["conn"]["id"] for req in fake.requests} == {"openrouter"}


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
    _route(client, absorb=extraction, dossier=dossiers)
    fake = _fake(client)

    review_runs.absorb(client, cid, sid)

    used = {r["conn"]["id"] for r in fake.requests}
    assert extraction in used, "the extraction did not use the absorb route"
    assert dossiers in used, "the dossier loop did not use the dossier route"


def test_the_reconcile_sweep_runs_on_the_continuity_route(client):
    """The reconciliation sweep is the continuity route's second task (§29), so
    pointing the route at a connection moves the sweep's one call there too."""
    _wid, cid, sid = _seed(client)
    routed = _connection(client, "for-continuity")
    _route(client, continuity=routed)
    fake = _fake(client)
    pid, title, beat = review_runs.LEDGER_THREAD
    store.plot.set_movement(cid, pid, title, "open", beat, sid)
    store.plot.set_movement(cid, "recover-the-harbour-ledger",
                            review_runs.RECOVER_THE_LEDGER["title"], "open",
                            review_runs.RECOVER_THE_LEDGER["beat"], sid)

    r = drafts.post(client, f"/api/campaigns/{cid}/continuity/reconcile")

    assert r.status_code == 200, r.json()
    assert r.json()["llm"] == "ok"
    sweeps = [req for req in fake.requests
              if "You answer closed questions about material you are given."
              in req["messages"][0]["content"]
              and "Candidate — " in req["messages"][1]["content"]]
    assert sweeps, "the sweep made no reconcile call"
    assert {req["conn"]["id"] for req in sweeps} == {routed}


def test_a_misrouted_secondary_phase_reports_itself_and_leaves_absorb_standing(client):
    """The three phases beside the extraction promise never to fail an absorb.

    Resolving their connections up front made a routing mistake for one of them
    a 409 that discarded the extraction's result too -- a setting the extraction
    does not use taking down the phase whose output the reviewer came for.
    """
    _wid, cid, sid = _seed(client)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": "mara"})
    keyless = client.post("/api/llm-connections",
                          json={"kind": "openrouter", "name": "Keyless"}).json()["id"]
    _route(client, dossier=keyless)
    _fake(client)

    r = review_runs.absorb(client, cid, sid)

    assert r.status_code == 200, r.json()
    dossiers = r.json()["dossiers"]
    assert dossiers["status"] == "failed"
    assert "key" in (dossiers["reason"] or "").lower()
    # And the absorb itself landed: a stored review carrying an extraction is
    # exactly what "the dossier route did not take this down with it" looks
    # like.
    assert r.json()["one_line"] is not None


def test_a_misrouted_extraction_still_refuses_the_whole_absorb(client):
    """The other half of the same rule. The extraction's failure is fatal to an
    absorb anyway, so its route gets the 409 the secondary phases do not."""
    _wid, cid, sid = _seed(client)
    keyless = client.post("/api/llm-connections",
                          json={"kind": "openrouter", "name": "Keyless"}).json()["id"]
    _route(client, absorb=keyless)
    _fake(client)

    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")

    assert r.status_code == 409 and r.json()["kind"] == "missing_key"


def test_a_scene_turn_and_its_retry_share_the_one_route(client):
    """#142 named the scene turn's retries and director turns as part of ONE
    task, so a reader who sets "Scene turns" gets all of them."""
    # A completed contribution is rerolled through its response identity;
    # /retry is reserved for an unfinished one.
    _wid, cid, sid = _seed(client)
    routed = _connection(client, "prose")
    _route(client, scene=routed)
    fake = _fake(client)

    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": "hello"}) as r:
        _drain(r)
    messages = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    rid = next(m["response_id"] for m in reversed(messages) if m.get("response_id"))
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/responses/{rid}/regenerate",
                       json={}) as r:
        _drain(r)

    assert {req["conn"]["id"] for req in fake.requests} == {routed}
    assert len(fake.requests) >= 2


# --- the failure modes ---
def test_a_route_naming_a_deleted_connection_falls_back_rather_than_failing(client):
    """A delete clears the config keys; it cannot reach into every campaign's
    frontmatter. So a stale campaign override degrades to the next scope."""
    _wid, cid, _sid = _seed(client)
    doomed = _connection(client, "doomed")
    _campaign_route(client, cid, suggestions=doomed)
    assert client.delete(f"/api/llm-connections/{doomed}").status_code == 200
    fake = _fake(client)

    drafts.post(client, f"/api/campaigns/{cid}/scene-suggestions")

    assert {r["conn"]["id"] for r in fake.requests} == {"openrouter"}


def test_deleting_a_connection_clears_it_from_the_global_routes(client):
    _seed(client)
    doomed = _connection(client, "doomed")
    _route(client, summary=doomed)
    pinned = keys.pin_key("summary", "provider")
    assert store.read_config()[pinned] == doomed
    client.delete(f"/api/llm-connections/{doomed}")
    assert store.read_config()[pinned] == ""


def test_a_routed_connection_that_cannot_send_is_reported_not_silently_replaced(client):
    """The opposite case, and the opposite answer. A keyless connection is a
    setup mistake the user made on purpose; generating on the active connection
    instead would play a scene on a model they did not choose."""
    _wid, cid, _sid = _seed(client)
    keyless = client.post("/api/llm-connections",
                          json={"kind": "openrouter", "name": "Keyless"}).json()["id"]
    _route(client, suggestions=keyless)
    _fake(client)

    r = drafts.post(client, f"/api/campaigns/{cid}/scene-suggestions")

    assert r.status_code == 409
    # The app's LLM-error handler flattens a dict detail onto the body, which is
    # what every other missing_key refusal already looks like to the client.
    body = r.json()
    assert body["kind"] == "missing_key"
    assert "Keyless" in body["detail"] and "scene suggestions" in body["detail"].lower()


def test_the_ledger_records_the_connection_a_routed_call_actually_used(client):
    """Cost tracking has to follow routing, or the reason for routing -- running
    the cheap jobs somewhere cheap -- is invisible in the one view that would
    show it working (#152/#153).

    Nothing in this change touches the ledger; `llm._stamp` files whatever
    connection it was handed. That is exactly why it is worth a test: the
    property is inherited rather than implemented, and inherited properties are
    the ones a later refactor breaks quietly.
    """
    _wid, cid, _sid = _seed(client)
    routed = _connection(client, "thrifty")
    _route(client, model="vendor/haiku", suggestions=routed)
    _fake(client)

    drafts.post(client, f"/api/campaigns/{cid}/scene-suggestions")

    # The campaign rollup groups by model, and the model it names is the routed
    # connection's -- not the active connection's `vendor/x`.
    by_model = client.get(f"/api/campaigns/{cid}/usage").json()["by_model"]
    assert [m["key"] for m in by_model] == ["vendor/haiku"]


