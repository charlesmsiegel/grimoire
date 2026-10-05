"""Sampler presets through the real routes: CRUD, import, attachment, and what
a turn is actually sent."""

from grimoire import llm, routes, store
from grimoire.llm_errors import LLMError
from grimoire.routes import common
from grimoire.store.sampler_presets import PRESET_CLEAR
from tests.llm_fakes import ScriptedProvider


def _preset(client, name="Warm", **params):
    r = client.post("/api/sampler-presets", json={"name": name, "params": params})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _scene(client):
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x", "model": "m"})
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Saltmarch"}).json()["id"]
    return cid, sid


def _real_facade(client, **providers):
    """A REAL facade over shared provider doubles, so the split actually runs."""
    facade = llm.LLMClient(retries=0, **providers)
    client.app.dependency_overrides[routes.get_llm] = lambda: facade
    return facade


# ---- CRUD ----

def test_crud(client):
    listing = client.get("/api/sampler-presets").json()
    assert listing["presets"] == []
    assert listing["params"][0]["name"] == "temperature"
    pid = _preset(client, temperature=0.8)
    assert client.get(f"/api/sampler-presets/{pid}").json()["params"] == {"temperature": 0.8}
    r = client.put(f"/api/sampler-presets/{pid}",
                   json={"name": "Warm", "params": {"top_k": 40}, "notes": "n"})
    assert r.json()["params"] == {"top_k": 40} and r.json()["notes"] == "n"
    assert client.delete(f"/api/sampler-presets/{pid}").status_code == 200
    assert client.get(f"/api/sampler-presets/{pid}").status_code == 404


def test_bad_params_are_a_400_naming_the_param(client):
    r = client.post("/api/sampler-presets", json={"name": "x", "params": {"temperature": 70}})
    assert r.status_code == 400 and "temperature" in r.json()["detail"]
    r = client.post("/api/sampler-presets", json={"name": "x", "params": {"top_k": 40.7}})
    assert r.status_code == 400


def test_unknown_presets_are_404(client):
    assert client.put("/api/sampler-presets/nope", json={"name": "x"}).status_code == 404
    assert client.delete("/api/sampler-presets/nope").status_code == 404


# ---- import ----

def test_import_saves_and_reports(client):
    r = client.post("/api/sampler-presets/import", json={
        "name": "Shared", "data": {"temp": 0.7, "rep_pen": 1.1, "top_k": 0, "genamt": 400,
                                   "mirostat_mode": 0}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["preset"]["params"] == {"temperature": 0.7, "repetition_penalty": 1.1}
    assert body["preset"]["source"] == "sillytavern"
    assert body["report"]["unmapped"] == ["mirostat_mode"]
    assert body["report"]["skipped"][0]["param"] == "max_tokens"
    assert [n["param"] for n in body["report"]["neutral"]] == ["top_k"]


def test_import_refuses_a_non_object(client):
    assert client.post("/api/sampler-presets/import",
                       json={"name": "x", "data": [1]}).status_code == 400


# ---- connection attachment ----

def test_a_connection_takes_a_preset_and_reports_its_split(client):
    pid = _preset(client, temperature=0.5, min_p=0.1)
    conn = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "Local", "base_url": "http://x/v1",
        "sampler_preset": pid}).json()["id"]
    got = client.get(f"/api/llm-connections/{conn}").json()
    assert got["sampler_preset"] == pid
    assert got["sampling"]["applied"] == {"temperature": 0.5}
    assert got["sampling"]["dropped"][0]["param"] == "min_p"
    got = client.put(f"/api/llm-connections/{conn}",
                     json={"sampler_support": "extended"}).json()
    assert got["sampling"]["dropped"] == []


def test_a_connection_naming_no_preset_is_a_400(client):
    r = client.post("/api/llm-connections", json={"kind": "claude", "name": "C",
                                                  "sampler_preset": "nope"})
    assert r.status_code == 400


def test_a_sampler_only_edit_keeps_the_health_verdict(client):
    pid = _preset(client)
    before = client.get("/api/llm-connections/openrouter").json()["rev"]
    client.put("/api/llm-connections/openrouter",
               json={"name": "OpenRouter", "sampler_preset": pid})
    assert client.get("/api/llm-connections/openrouter").json()["rev"] == before


# ---- routing ----

def test_the_routing_bundle_carries_presets_and_reports(client):
    pid = _preset(client, temperature=0.4)
    client.put("/api/llm-connections/claude", json={"sampler_preset": pid})
    client.put("/api/config", json={"active_connection_id": "claude"})
    bundle = client.get("/api/routing").json()
    assert bundle["preset_catalog"] == [{"id": pid, "name": "Warm"}]
    assert bundle["presets"]["scene"] == ""
    assert bundle["preset_inherited"]["scene"] == pid
    assert bundle["preset_inherited_from"]["scene"] == {"scope": "connection"}
    report = bundle["sampling"]["absorb"]
    assert report["applied"] == {} and report["dropped"][0]["param"] == "temperature"


def test_a_global_preset_put_lands_and_a_clear_is_accepted(client):
    pid = _preset(client)
    r = client.put("/api/routing", json={"presets": {"scene": pid, "absorb": PRESET_CLEAR}})
    assert r.status_code == 200, r.text
    assert r.json()["presets"]["scene"] == pid
    assert store.read_config()["preset_absorb"] == PRESET_CLEAR


def test_routing_put_refuses_unknown_presets_and_scopes(client):
    cid, _ = _scene(client)
    assert client.put("/api/routing", json={"presets": {"scene": "nope"}}).status_code == 400
    r = client.put(f"/api/campaigns/{cid}/routing", json={"presets": {"tagline": PRESET_CLEAR}})
    assert r.status_code == 400 and "tagline" in r.json()["detail"]


def test_a_campaign_preset_overrides_the_global_one(client):
    cid, sid = _scene(client)
    glob, mine = _preset(client, "Global", temperature=0.2), _preset(client, "Mine", top_p=0.9)
    client.put("/api/routing", json={"presets": {"scene": glob}})
    r = client.put(f"/api/campaigns/{cid}/routing", json={"presets": {"scene": mine}})
    assert r.json()["presets"]["scene"] == mine
    assert r.json()["preset_inherited"]["scene"] == glob
    live = client.get(f"/api/campaigns/{cid}/scenes/{sid}/context").json()
    assert live["sampling"]["preset_id"] == mine and live["sampling"]["scope"] == "campaign"
    assert live["sampling"]["applied"] == {"top_p": 0.9}


# ---- what a turn is actually sent ----

def test_a_chat_turn_sends_the_applied_params_and_snapshots_the_report(client):
    cid, sid = _scene(client)
    pid = _preset(client, temperature=0.6, min_p=0.05)
    client.put(f"/api/campaigns/{cid}/routing", json={"presets": {"scene": pid}})
    provider = ScriptedProvider(chunks=("Mara nods.",))
    _real_facade(client, openrouter=provider)
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": "Shall we go?"}).status_code == 200
    assert provider.requests[0]["kwargs"]["sampling"] == {"temperature": 0.6, "min_p": 0.05}
    eid = store.prompt_log.list_entries(cid, sid)[0]["id"]
    frozen = client.get(f"/api/campaigns/{cid}/scenes/{sid}/prompts/{eid}").json()
    assert frozen["sampling"]["applied"] == {"temperature": 0.6, "min_p": 0.05}
    assert frozen["sampling"]["scope"] == "campaign"


def test_a_cleared_route_sends_nothing(client):
    cid, sid = _scene(client)
    pid = _preset(client, temperature=0.6)
    client.put("/api/llm-connections/openrouter", json={"sampler_preset": pid})
    client.put(f"/api/campaigns/{cid}/routing", json={"presets": {"scene": PRESET_CLEAR}})
    provider = ScriptedProvider(chunks=("Mara nods.",))
    _real_facade(client, openrouter=provider)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "Go?"})
    assert "sampling" not in provider.requests[0]["kwargs"]


def test_the_catalog_decides_what_openrouter_is_sent(client):
    cid, sid = _scene(client)
    pid = _preset(client, temperature=0.6, min_p=0.05)
    client.put("/api/llm-connections/openrouter", json={"sampler_preset": pid})
    rev = store.llm_connections.read_connection_raw("openrouter")["rev"]
    store.llm_connections.set_cached_models("openrouter", [
        {"id": "m", "name": "m", "context": None, "prompt": None, "completion": None,
         "params": ["temperature", "max_tokens"]}], rev)
    live = client.get(f"/api/campaigns/{cid}/scenes/{sid}/context").json()
    assert live["sampling"]["applied"] == {"temperature": 0.6}
    assert live["sampling"]["dropped"][0]["param"] == "min_p"
    assert live["sampling"]["verified"] is True
    provider = ScriptedProvider(chunks=("Mara nods.",))
    _real_facade(client, openrouter=provider)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "Go?"})
    assert provider.requests[0]["kwargs"]["sampling"] == {"temperature": 0.6}


def test_no_preset_anywhere_reports_provider_defaults(client):
    cid, sid = _scene(client)
    live = client.get(f"/api/campaigns/{cid}/scenes/{sid}/context").json()
    assert live["sampling"]["scope"] == "none" and live["sampling"]["applied"] == {}



def test_a_connection_whose_preset_was_deleted_can_still_be_saved(client):
    pid = _preset(client)
    client.put("/api/llm-connections/openrouter", json={"sampler_preset": pid})
    client.delete(f"/api/sampler-presets/{pid}")
    r = client.put("/api/llm-connections/openrouter",
                   json={"name": "Renamed", "sampler_preset": pid})
    assert r.status_code == 200, r.text
    # ...while naming a DIFFERENT missing preset is still a decision, and refused.
    assert client.put("/api/llm-connections/openrouter",
                      json={"sampler_preset": "other"}).status_code == 400


def test_a_model_override_does_not_inherit_the_standing_models_param_list(client):
    """#77: a reroll at another model must be checked against THAT model, and
    one the catalog does not list is unverified, never judged by the old list."""
    cid, _ = _scene(client)
    pid = _preset(client, temperature=0.6, min_p=0.05)
    client.put("/api/llm-connections/openrouter", json={"sampler_preset": pid})
    rev = store.llm_connections.read_connection_raw("openrouter")["rev"]
    store.llm_connections.set_cached_models("openrouter", [
        {"id": "m", "name": "m", "context": None, "prompt": None, "completion": None,
         "params": ["temperature"]}], rev)

    class Body:
        connection_id = None
        model = "other/model"

    conn, routed = common._override_connection(Body(), "chat", cid)
    assert routed and "model_params" not in conn
    from grimoire import llm_sampling
    report = llm_sampling.report(conn)
    assert report["applied"] == {"temperature": 0.6, "min_p": 0.05}
    assert report["verified"] is False


def test_a_fallback_snapshot_reports_the_fallbacks_own_split(client):
    """The distinct-model fallback capture describes the request the fallback
    was sent: the route's preset, split for the fallback's kind."""
    cid, sid = _scene(client)
    client.put("/api/llm-connections/openrouter", json={"model": "glm-5.3"})
    pid = _preset(client, temperature=0.6, min_p=0.05)
    client.put("/api/routing", json={"presets": {"scene": pid}})
    backup = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "Backup", "base_url": "https://example.test/v1",
        "model": "vendor/unknown"}).json()["id"]
    client.put("/api/config", json={"fallback_connection_id": backup})
    primary = ScriptedProvider(chunks=(), error=LLMError("auth", "refused"))
    fallback = ScriptedProvider(chunks=("Mara nods.",))
    facade = llm.LLMClient(openrouter=primary, openai_compatible=fallback, retries=0,
                           fallback=common._fallback_connection)
    client.app.dependency_overrides[routes.get_llm] = lambda: facade
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": "Shall we go?"}).status_code == 200
    assert fallback.requests[0]["kwargs"]["sampling"] == {"temperature": 0.6}
    entries = store.prompt_log.list_entries(cid, sid)
    by_model = {e["model"]: store.prompt_log.read_entry(cid, e["id"], scene=sid)
                for e in entries}
    assert by_model["vendor/unknown"]["sampling"]["kind"] == "openai_compatible"
    assert by_model["vendor/unknown"]["sampling"]["applied"] == {"temperature": 0.6}
    assert by_model["vendor/unknown"]["sampling"]["dropped"][0]["param"] == "min_p"
    assert by_model["glm-5.3"]["sampling"]["applied"] == {"temperature": 0.6, "min_p": 0.05}


def test_a_huge_integer_is_a_400(client):
    r = client.post("/api/sampler-presets", json={"name": "x", "params": {"top_k": 10 ** 400}})
    assert r.status_code == 400 and "top_k" in r.json()["detail"]


def test_a_malformed_catalog_sidecar_degrades_to_unverified(client):
    cid, sid = _scene(client)
    pid = _preset(client, temperature=0.6)
    client.put("/api/llm-connections/openrouter", json={"sampler_preset": pid})
    rev = store.llm_connections.read_connection_raw("openrouter")["rev"]
    sidecar = store.paths.home() / "llm_connections" / "openrouter.models.json"
    import json as _json
    for models in (None, [{"id": "m", "params": [{"x": 1}, "temperature"]}]):
        sidecar.write_text(_json.dumps({"models": models, "fetched_at": "", "rev": rev}))
        live = client.get(f"/api/campaigns/{cid}/scenes/{sid}/context")
        assert live.status_code == 200, live.text
        assert live.json()["sampling"]["applied"] == {"temperature": 0.6}


def test_deleting_an_overlong_id_is_a_404(client):
    _preset(client)
    assert client.delete("/api/sampler-presets/" + "a" * 1000).status_code == 404


def test_a_non_object_catalog_sidecar_does_not_fail_a_turn(client):
    cid, sid = _scene(client)
    sidecar = store.paths.home() / "llm_connections" / "openrouter.models.json"
    for body in ("[1, 2]", "3"):
        sidecar.write_text(body, encoding="utf-8")
        live = client.get(f"/api/campaigns/{cid}/scenes/{sid}/context")
        assert live.status_code == 200, live.text
        provider = ScriptedProvider(chunks=("Mara nods.",))
        _real_facade(client, openrouter=provider)
        assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                           json={"content": "Go?"}).status_code == 200
