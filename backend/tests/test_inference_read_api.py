"""The two read APIs slice C's screens use: a provider's models grouped by what
a role needs (`GET /llm-connections/{id}/capabilities`) and a sampler preset
previewed on a model (`POST /inference/controls`).

Neither calls an LLM or reserves a run. The grouping is `capabilities.group_for`
and the preview is `controls.preview`, so what is held here is the wire shape,
the whole-provider case, the 404s, and that the preview is what an attempt
resolved for the same provider, model and preset carries.
"""

from __future__ import annotations

import pytest

from grimoire import llm_sampling
from grimoire.store import config, llm_connections, sampler_presets
from grimoire.store.inference import facts
from grimoire.store.inference import resolve as inf

ZAI = "https://api.z.ai/api/paas/v4"

TEXT = {"id": "vendor/text-7b", "outputs": ["text"], "vision": False}
SEER = {"id": "vendor/seer-9b", "outputs": ["text"], "vision": True}
EMBED = {"id": "vendor/embed-small", "outputs": ["embeddings"]}
BARE = {"id": "vendor/bare-1"}


def _connection(client, kind="openrouter", rows=(), **fields) -> str:
    body = {"kind": kind, "name": "Saltmarch", "api_key": "sk-fake-key", **fields}
    cid = client.post("/api/llm-connections", json=body).json()["id"]
    if rows:
        rev = llm_connections.read_connection_raw(cid)["rev"]
        llm_connections.set_cached_models(cid, list(rows), rev)
    return cid


def _get(client, cid, need, **params):
    return client.get(f"/api/llm-connections/{cid}/capabilities",
                      params={"need": need, **params})


def _ids(rows):
    return [r["id"] for r in rows]


# ---- capabilities: grouped per need ----
def test_openrouter_embed_lists_only_embedding_models_as_fitting(client):
    cid = _connection(client, rows=[TEXT, EMBED, BARE])
    got = _get(client, cid, "embed")
    assert got.status_code == 200
    body = got.json()
    assert _ids(body["groups"]["fits"]) == ["vendor/embed-small"]
    assert body["groups"]["fits"][0]["capabilities"]["embed"] == {
        "value": "yes", "source": "catalog"}
    # A row that states no outputs is not ruled out: a test call can check.
    assert _ids(body["groups"]["unverified"]) == ["vendor/bare-1"]
    assert body["groups"]["unverified"][0]["capabilities"]["embed"] == {
        "value": "unknown", "source": "unknown"}
    # The text model is hidden, and says why.
    assert body["hidden"] == [{"id": "vendor/text-7b",
                               "reason": "the catalog says this model does not make embeddings"}]
    assert body["reason"] is None
    assert body["preset"]["id"] == "openrouter"


def test_a_row_is_the_catalog_entry_plus_its_capabilities(client):
    row = {**EMBED, "name": "Embed Small", "context": 8192, "prompt": "0.1",
           "completion": None, "vision": None}
    cid = _connection(client, rows=[row])
    got = _get(client, cid, "embed").json()["groups"]["fits"][0]
    assert {k: got[k] for k in row} == row
    from grimoire.store.inference import capabilities
    assert set(got["capabilities"]) == set(capabilities.NAMES)
    assert all(set(v) == {"value", "source"} for v in got["capabilities"].values())
    assert got["reason"] == "catalog"


def test_generate_groups_text_models_and_hides_embedding_ones(client):
    cid = _connection(client, rows=[TEXT, EMBED, BARE, SEER])
    body = _get(client, cid, "generate").json()
    # OpenRouter's preset says every model behind it generates.
    assert _ids(body["groups"]["fits"]) == ["vendor/bare-1", "vendor/seer-9b",
                                            "vendor/text-7b"]
    assert body["groups"]["unverified"] == []
    assert _ids(body["hidden"]) == ["vendor/embed-small"]


def test_vision_groups_by_what_the_catalog_states(client):
    cid = _connection(client, rows=[TEXT, SEER, {"id": "vendor/murk-2", "vision": None}])
    body = _get(client, cid, "vision").json()
    assert _ids(body["groups"]["fits"]) == ["vendor/seer-9b"]
    assert _ids(body["groups"]["unverified"]) == ["vendor/murk-2"]
    assert body["groups"]["unverified"][0]["reason"] == "not known yet — a test call can check"
    assert _ids(body["hidden"]) == ["vendor/text-7b"]


def test_decide_is_met_by_generating(client):
    cid = _connection(client, rows=[TEXT, EMBED])
    body = _get(client, cid, "decide").json()
    assert _ids(body["groups"]["fits"]) == ["vendor/text-7b"]
    # `group_for`'s own rule: a model that does not generate is not ruled out of
    # deciding while its native decision endpoint is merely unknown.
    assert _ids(body["groups"]["unverified"]) == ["vendor/embed-small"]
    assert body["hidden"] == []


def test_the_users_word_moves_a_model_between_groups(client):
    cid = _connection(client, rows=[BARE])
    assert _ids(_get(client, cid, "vision").json()["groups"]["unverified"]) == ["vendor/bare-1"]
    facts.set_overrides(cid, "vendor/bare-1", {"vision": "yes"})
    row = _get(client, cid, "vision").json()["groups"]["fits"][0]
    assert row["id"] == "vendor/bare-1"
    assert row["capabilities"]["vision"] == {"value": "yes", "source": "user"}
    assert row["reason"] == "user"


def test_every_group_is_sorted_by_id(client):
    cid = _connection(client, rows=[{"id": "vendor/zed"}, {"id": "vendor/amy"},
                                    {"id": "vendor/mara"}])
    body = _get(client, cid, "generate").json()
    assert _ids(body["groups"]["fits"]) == ["vendor/amy", "vendor/mara", "vendor/zed"]


def test_an_empty_catalog_is_empty_groups(client):
    cid = _connection(client)
    body = _get(client, cid, "generate").json()
    assert body["groups"] == {"fits": [], "unverified": []}
    assert body["hidden"] == [] and body["reason"] is None


# ---- the whole provider ruled out ----
def test_zai_embed_is_ruled_out_for_the_provider_not_row_by_row(client):
    cid = _connection(client, "openai_compatible", base_url=ZAI,
                      rows=[{"id": "glm-test-1"}, {"id": "glm-embed-test"}])
    body = _get(client, cid, "embed").json()
    assert body["preset"]["id"] == "zai"
    assert body["groups"] == {"fits": [], "unverified": []}
    # Documented choice: the one provider-wide reason, and no per-row echo of it.
    assert body["hidden"] == []
    assert body["reason"] == "z.ai serves no embeddings"


def test_the_claude_subscription_reads_no_images_for_any_model(client):
    cid = _connection(client, "claude", api_key="")
    body = _get(client, cid, "vision").json()
    assert body["groups"] == {"fits": [], "unverified": []}
    assert body["reason"] == "Claude subscription reads no images"


def test_a_provider_only_partly_ruled_out_has_no_provider_reason(client):
    # z.ai cannot decide natively, but generating answers a decision.
    cid = _connection(client, "openai_compatible", base_url=ZAI, rows=[{"id": "glm-test-1"}])
    body = _get(client, cid, "decide").json()
    assert body["reason"] is None
    assert _ids(body["groups"]["fits"]) == ["glm-test-1"]


# ---- one model ----
def test_model_narrows_to_that_model_in_its_group(client):
    cid = _connection(client, rows=[TEXT, EMBED, BARE])
    body = _get(client, cid, "embed", model="vendor/text-7b").json()
    assert body["groups"] == {"fits": [], "unverified": []}
    assert body["hidden"] == [{"id": "vendor/text-7b",
                               "reason": "the catalog says this model does not make embeddings"}]
    body = _get(client, cid, "embed", model="vendor/embed-small").json()
    assert _ids(body["groups"]["fits"]) == ["vendor/embed-small"] and body["hidden"] == []


def test_model_not_in_the_catalog_is_judged_on_its_name_alone(client):
    cid = _connection(client, rows=[TEXT])
    body = _get(client, cid, "embed", model="vendor/typed-embed-1").json()
    assert _ids(body["groups"]["fits"]) == ["vendor/typed-embed-1"]
    assert body["groups"]["fits"][0]["capabilities"]["embed"]["source"] == "name"
    body = _get(client, cid, "embed", model="vendor/typed-chat-1").json()
    assert _ids(body["groups"]["unverified"]) == ["vendor/typed-chat-1"]


# ---- refusals ----
def test_an_unknown_connection_is_a_404(client):
    got = _get(client, "no-such-connection", "generate")
    assert got.status_code == 404
    assert got.json()["detail"] == "connection not found"


def test_an_unknown_need_is_refused(client):
    cid = _connection(client)
    assert _get(client, cid, "levitate").status_code == 422


def test_the_response_never_carries_the_key(client):
    cid = _connection(client, rows=[TEXT])
    assert "sk-fake-key" not in _get(client, cid, "generate").text


# ---- controls preview ----
def _preview(client, **body):
    return client.post("/api/inference/controls", json=body)


def test_the_preview_is_what_an_attempt_resolved_for_it_carries(client):
    rows = [{"id": "vendor/winifred-2", "outputs": ["text"],
             "params": ["temperature", "reasoning"]}]
    cid = _connection(client, rows=rows, model="vendor/winifred-2")
    pid = sampler_presets.create_preset("Warm", {"temperature": 0.8, "top_k": 30,
                                                 "reasoning_effort": "low"})
    llm_connections.update_connection(cid, sampler_preset=pid)
    config.write_config(active_connection_id=cid)

    attempt = inf.resolve("chat").attempts[0]
    assert (attempt.provider_id, attempt.model, attempt.preset_id) == (
        cid, "vendor/winifred-2", pid)

    got = _preview(client, preset_id=pid, provider=cid, model="vendor/winifred-2")
    assert got.status_code == 200
    body = got.json()
    assert body == attempt.controls
    assert set(body) == {"requested", "effective", "controls"}
    assert body["requested"]["temperature"] == 0.8
    assert body["controls"]["top_k"]["state"] == "unsupported"
    assert all("source" in c for c in body["controls"].values())


def test_the_preview_of_another_model_is_that_models(client):
    cid = _connection(client, model="vendor/mara-7b", rows=[
        {"id": "vendor/mara-7b", "params": ["temperature"]},
        {"id": "vendor/seraphine-1", "params": ["top_p"]}])
    pid = sampler_presets.create_preset("Cool", {"temperature": 0.2})
    own = _preview(client, preset_id=pid, provider=cid, model="").json()
    other = _preview(client, preset_id=pid, provider=cid, model="vendor/seraphine-1").json()
    assert own["controls"]["temperature"]["state"] == "supported"
    assert other["controls"]["temperature"]["state"] == "unsupported"
    assert own == _preview(client, preset_id=pid, provider=cid,
                           model="vendor/mara-7b").json()


def test_the_preview_with_no_preset_requests_nothing(client):
    cid = _connection(client, model="vendor/mara-7b")
    body = _preview(client, preset_id="", provider=cid, model="vendor/mara-7b").json()
    assert body["requested"] == {} and body["effective"] == {}
    assert set(body["controls"]) == set(llm_sampling.CONTROLS)


def test_the_preview_of_an_unknown_provider_is_a_404(client):
    got = _preview(client, preset_id="", provider="no-such-connection", model="m")
    assert got.status_code == 404
    assert got.json()["detail"] == "connection not found"


def test_the_preview_of_an_unknown_preset_is_a_404(client):
    cid = _connection(client)
    got = _preview(client, preset_id="no-such-preset", provider=cid, model="")
    assert got.status_code == 404
    assert got.json()["detail"] == "sampler preset not found"


def test_the_preview_needs_a_provider(client):
    assert _preview(client, preset_id="").status_code == 422


@pytest.mark.parametrize("kind", ["claude", "anthropic", "openai_compatible"])
def test_the_preview_reads_every_adapter(client, kind):
    cid = _connection(client, kind, base_url="http://localhost:1234/v1"
                      if kind == "openai_compatible" else "")
    pid = sampler_presets.create_preset("Warm", {"temperature": 0.8})
    got = _preview(client, preset_id=pid, provider=cid, model="")
    assert got.status_code == 200
    assert got.json()["requested"] == {"temperature": 0.8}
