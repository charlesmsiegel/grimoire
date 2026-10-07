"""The capability resolver: one answer per capability, with where it came from,
and the three groups a role picker splits a catalog into (spec 6.2, 6.3)."""

from __future__ import annotations

import json

import pytest

from grimoire.store import config, llm_connections, post_images
from grimoire.store.inference import capabilities, facts, providers
from grimoire.store.inference.capabilities import Cap

P = providers.PRESETS


def _facts(**kw) -> dict:
    base = {"vision": "", "prefill": None, "post_process": "", "rates": None,
            "verified": {}, "overrides": {}}
    return {**base, **kw}


def _resolve(preset: str, model: str = "mara-7b", row: dict | None = None,
             **fact_kw) -> dict[str, Cap]:
    return capabilities.resolve_caps(P[preset], model, catalog_row=row,
                                     facts=_facts(**fact_kw))


# ---- the vocabulary ----
def test_every_capability_is_answered():
    got = _resolve("custom")
    assert set(got) == set(capabilities.NAMES) == set(providers.CAPABILITIES)
    assert all(isinstance(c, Cap) for c in got.values())


def test_nothing_known_is_unknown():
    # Custom rules out only a native decision endpoint; the rest is open.
    got = _resolve("custom")
    assert got["decide_native"] == Cap("no", "adapter")
    assert all(c == Cap("unknown", "unknown") for k, c in got.items() if k != "decide_native")


# ---- each source ----
def test_adapter_never_is_no():
    got = _resolve("zai")
    assert got["vision"] == Cap("no", "adapter")
    assert got["embed"] == Cap("no", "adapter")
    assert got["decide_native"] == Cap("no", "adapter")


def test_preset_always_is_yes():
    got = _resolve("openai")
    assert got["generate"] == Cap("yes", "preset")
    assert got["stream"] == Cap("yes", "preset")


def test_possible_never_yields_yes():
    got = _resolve("openrouter")
    for cap in P["openrouter"].possible:
        assert got[cap] == Cap("unknown", "unknown"), cap


def test_verified_results_are_test():
    got = _resolve("custom", verified={"vision": {"ok": True, "at": "x"},
                                       "embed": {"ok": False, "at": "x", "error": "404"}})
    assert got["vision"] == Cap("yes", "test")
    assert got["embed"] == Cap("no", "test")


def test_overrides_are_user():
    got = _resolve("custom", overrides={"prefill": "yes", "structured_output": "no"})
    assert got["prefill"] == Cap("yes", "user")
    assert got["structured_output"] == Cap("no", "user")


def test_facts_vision_and_prefill_are_user():
    assert _resolve("custom", vision="on")["vision"] == Cap("yes", "user")
    assert _resolve("custom", vision="off")["vision"] == Cap("no", "user")
    assert _resolve("custom", vision="")["vision"] == Cap("unknown", "unknown")
    assert _resolve("custom", prefill=True)["prefill"] == Cap("yes", "user")
    assert _resolve("custom", prefill=False)["prefill"] == Cap("no", "user")


def test_catalog_outputs():
    text = _resolve("custom", row={"id": "mara-7b", "outputs": ["text"]})
    assert text["generate"] == Cap("yes", "catalog")
    assert text["embed"] == Cap("no", "catalog")
    emb = _resolve("custom", row={"id": "mara-7b", "outputs": ["embeddings"]})
    assert emb["embed"] == Cap("yes", "catalog")
    assert emb["generate"] == Cap("no", "catalog")
    dec = _resolve("custom", row={"id": "mara-7b", "outputs": ["decisions"]})
    assert dec["generate"] == Cap("no", "catalog")
    both = _resolve("custom", row={"id": "mara-7b", "outputs": ["decisions", "text"]})
    assert both["generate"] == Cap("yes", "catalog")


def test_a_row_without_outputs_says_nothing_about_generate_or_embed():
    got = _resolve("custom", row={"id": "mara-7b", "vision": None})
    assert got["generate"] == Cap("unknown", "unknown")
    assert got["embed"] == Cap("unknown", "unknown")


def test_catalog_vision():
    assert _resolve("custom", row={"id": "m", "vision": True})["vision"] == Cap("yes", "catalog")
    assert _resolve("custom", row={"id": "m", "vision": False})["vision"] == Cap("no", "catalog")
    assert _resolve("custom", row={"id": "m", "vision": None})["vision"] == Cap("unknown", "unknown")


def test_catalog_params_say_structured_output_and_absence_says_nothing():
    for param in ("structured_outputs", "response_format"):
        got = _resolve("custom", row={"id": "m", "params": ["temperature", param]})
        assert got["structured_output"] == Cap("yes", "catalog")
    got = _resolve("custom", row={"id": "m", "params": ["temperature"]})
    assert got["structured_output"] == Cap("unknown", "unknown")


def test_anthropic_features_structured_output():
    yes = _resolve("anthropic", row={"id": "m", "outputs": ["text"],
                                     "features": {"structured_output": True}})
    assert yes["structured_output"] == Cap("yes", "catalog")
    no = _resolve("anthropic", row={"id": "m", "outputs": ["text"],
                                    "features": {"structured_output": False}})
    assert no["structured_output"] == Cap("no", "catalog")


def test_the_catalog_says_nothing_about_stream_decide_or_prefill():
    row = {"id": "m", "outputs": ["text", "decisions"],
           "params": ["structured_outputs"], "vision": True}
    for preset, cap in (("ollama", "stream"), ("ollama", "prefill"),
                        ("openrouter", "decide_native"), ("openrouter", "prefill")):
        assert _resolve(preset, row=row)[cap] == Cap("unknown", "unknown"), (preset, cap)


def test_a_malformed_row_contributes_nothing():
    got = _resolve("custom", row={"id": "m", "outputs": "text", "vision": "yes",
                                  "params": "structured_outputs", "features": ["x"]})
    assert all(c == Cap("unknown", "unknown") for k, c in got.items() if k != "decide_native")


# ---- the name rule ----
def test_name_rule():
    got = _resolve("custom", model="test-Embed-1")
    assert got["embed"] == Cap("yes", "name")
    assert got["generate"] == Cap("no", "name")


def test_name_rule_is_blocked_by_catalog_outputs():
    got = _resolve("custom", model="test-embed-1",
                   row={"id": "test-embed-1", "outputs": ["text"]})
    assert got["generate"] == Cap("yes", "catalog")
    assert got["embed"] == Cap("no", "catalog")


def test_name_rule_does_not_beat_preset_always():
    # OpenAI says every model behind it generates; the name rule fills gaps only.
    got = _resolve("openai", model="test-embed-1")
    assert got["generate"] == Cap("yes", "preset")
    assert got["embed"] == Cap("yes", "name")


def test_name_rule_does_not_beat_adapter_no():
    got = _resolve("zai", model="test-embed-1")
    assert got["embed"] == Cap("no", "adapter")


# ---- precedence ----
def test_adapter_no_beats_every_yes():
    got = _resolve("zai", model="test-embed-1",
                   row={"id": "test-embed-1", "outputs": ["embeddings"], "vision": True},
                   verified={"embed": {"ok": True, "at": "x"}, "vision": {"ok": True, "at": "x"}},
                   overrides={"embed": "yes", "decide_native": "yes"}, vision="on")
    assert got["embed"] == Cap("no", "adapter")
    assert got["vision"] == Cap("no", "adapter")
    assert got["decide_native"] == Cap("no", "adapter")


def test_test_beats_user_beats_catalog():
    row = {"id": "m", "vision": True, "outputs": ["text"]}
    assert _resolve("custom", row=row, vision="off")["vision"] == Cap("no", "user")
    assert _resolve("custom", row=row, vision="off",
                    verified={"vision": {"ok": True, "at": "x"}})["vision"] == Cap("yes", "test")
    assert _resolve("custom", row=row,
                    verified={"generate": {"ok": False, "at": "x"}})["generate"] == Cap("no", "test")
    assert _resolve("custom", row=row,
                    overrides={"vision": "no"})["vision"] == Cap("no", "user")


def test_user_and_catalog_beat_preset_always():
    assert _resolve("openai", overrides={"generate": "no"})["generate"] == Cap("no", "user")
    got = _resolve("openai", row={"id": "m", "outputs": ["embeddings"]})
    assert got["generate"] == Cap("no", "catalog")


# ---- fits ----
def test_fits():
    caps = _resolve("custom", row={"id": "m", "outputs": ["text"], "vision": False})
    assert capabilities.fits(caps, "generate") == "yes"
    assert capabilities.fits(caps, "vision") == "no"
    assert capabilities.fits(caps, "embed") == "no"
    assert capabilities.fits(_resolve("custom"), "generate") == "unknown"


def test_fits_decide():
    # Structured generation can answer any decision (spec 7.4).
    assert capabilities.fits(_resolve("zai"), "decide") == "yes"
    assert capabilities.fits(_resolve("ollama", overrides={"decide_native": "no"}),
                             "decide") == "unknown"
    assert capabilities.fits(_resolve("openrouter", overrides={"decide_native": "yes"},
                                      verified={"generate": {"ok": False}}), "decide") == "yes"
    assert capabilities.fits(_resolve("custom"), "decide") == "unknown"
    # decide_native is never on custom, so it all rests on generate.
    assert capabilities.fits(_resolve("custom", model="test-embed-1"), "decide") == "no"


def test_fits_refuses_an_unknown_need():
    with pytest.raises(ValueError):
        capabilities.fits(_resolve("custom"), "tools")


# ---- group_for ----
def test_group_fits():
    group, _ = capabilities.group_for(_resolve("openai"), "generate", P["openai"])
    assert group == "fits"


def test_group_unverified():
    group, reason = capabilities.group_for(_resolve("openai"), "embed", P["openai"])
    assert group == "unverified"
    assert reason == "not known yet — a test call can check"


def test_group_hidden_by_the_adapter_names_the_preset():
    assert capabilities.group_for(_resolve("zai"), "embed", P["zai"]) == (
        "hidden", "z.ai serves no embeddings")
    assert capabilities.group_for(_resolve("zai"), "vision", P["zai"]) == (
        "hidden", "z.ai reads no images")
    assert capabilities.group_for(_resolve("anthropic"), "embed", P["anthropic"]) == (
        "hidden", "Anthropic API serves no embeddings")


def test_group_hidden_by_another_source_names_that_source():
    row = {"id": "m", "outputs": ["text"]}
    assert capabilities.group_for(_resolve("custom", row=row), "embed", P["custom"]) == (
        "hidden", "the catalog says this model does not produce embeddings")
    caps = _resolve("custom", verified={"embed": {"ok": False, "at": "x"}})
    assert capabilities.group_for(caps, "embed", P["custom"]) == (
        "hidden", "a test call found no embeddings")
    caps = _resolve("custom", vision="off")
    assert capabilities.group_for(caps, "vision", P["custom"]) == (
        "hidden", "you marked this model as not reading images")
    caps = _resolve("custom", model="test-embed-1")
    group, reason = capabilities.group_for(caps, "generate", P["custom"])
    assert group == "hidden" and "name" in reason


def test_group_decide():
    assert capabilities.group_for(_resolve("zai"), "decide", P["zai"])[0] == "fits"
    assert capabilities.group_for(_resolve("custom"), "decide", P["custom"])[0] == "unverified"
    caps = _resolve("custom", row={"id": "m", "outputs": ["embeddings"]})
    group, reason = capabilities.group_for(caps, "decide", P["custom"])
    assert group == "hidden" and reason


# ---- caps_for: reading the store ----
@pytest.fixture()
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


def _conn(**fields) -> dict:
    kind = fields.pop("kind", "openai_compatible")
    cid = llm_connections.create_connection(kind, fields.pop("name", "Saltmarch"),
                                            api_key="k", **fields)
    return llm_connections.read_connection_raw(cid)


def _write_facts(conn_id: str, doc: dict) -> None:
    p = llm_connections.facts_path(conn_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc), encoding="utf-8")


def test_caps_for_reads_preset_catalog_and_facts(home):
    conn = _conn(base_url="https://api.openai.com/v1", model="mara-7b")
    llm_connections.set_cached_models(
        conn["id"], [{"id": "mara-7b", "vision": True, "outputs": ["text"]}], conn["rev"])
    facts.set_overrides(conn["id"], "mara-7b", {"prefill": "no"})
    got = capabilities.caps_for(conn)
    assert got["generate"] == Cap("yes", "catalog")
    assert got["stream"] == Cap("yes", "preset")
    assert got["vision"] == Cap("yes", "catalog")
    assert got["prefill"] == Cap("no", "user")


def test_caps_for_names_another_model(home):
    conn = _conn(base_url="https://api.openai.com/v1", model="mara-7b")
    llm_connections.set_cached_models(
        conn["id"], [{"id": "mara-7b", "vision": True},
                     {"id": "winifred-2", "vision": False}], conn["rev"])
    assert capabilities.caps_for(conn, "winifred-2")["vision"] == Cap("no", "catalog")


def test_caps_for_ignores_a_stale_rev(home):
    conn = _conn(base_url="http://localhost:1234/v1", model="mara-7b")
    facts.record_verified(conn["id"], "mara-7b", "an-old-rev", {"vision": {"ok": True}})
    assert capabilities.caps_for(conn)["vision"] == Cap("unknown", "unknown")
    facts.record_verified(conn["id"], "mara-7b", conn["rev"], {"vision": {"ok": True}})
    assert capabilities.caps_for(conn)["vision"] == Cap("yes", "test")


def test_caps_for_does_not_read_the_legacy_vision_field(home):
    conn = _conn(base_url="http://localhost:1234/v1", model="mara-7b", vision="on")
    assert capabilities.caps_for(conn)["vision"] == Cap("unknown", "unknown")


def test_caps_for_never_raises(home):
    conn = _conn(base_url="http://localhost:1234/v1", model="mara-7b")
    _write_facts(conn["id"], ["not", "a", "map"])
    llm_connections.set_cached_models(conn["id"], ["not a row", {"id": "mara-7b",
                                      "outputs": None, "vision": "maybe"}], conn["rev"])
    assert capabilities.caps_for(conn)["vision"] == Cap("unknown", "unknown")
    assert capabilities.caps_for({"kind": "openai_compatible"})["generate"].value == "unknown"
    assert capabilities.caps_for({"id": "../nope", "model": 3})["vision"].value == "unknown"


def test_caps_for_none_is_all_unknown():
    got = capabilities.caps_for(None)  # type: ignore[arg-type]
    assert all(c == Cap("unknown", "unknown") for c in got.values())


def test_caps_for_survives_a_failing_catalog(home, monkeypatch):
    conn = _conn(base_url="http://localhost:1234/v1", model="mara-7b", vision="")
    facts.set_overrides(conn["id"], "mara-7b", {"vision": "yes"})

    def boom(_id):
        raise OSError("disk")

    monkeypatch.setattr(llm_connections, "cached_models", boom)
    assert capabilities.caps_for(conn)["vision"] == Cap("yes", "user")


# ---- post_images keeps its answers (Review Focus 1) ----
def test_openai_preset_without_catalog_vision_sends_no_images(home):
    config.write_config(send_images="on")
    conn = _conn(base_url="https://api.openai.com/v1", model="mara-7b")
    assert providers.infer(conn).id == "openai"
    assert post_images.capability(conn) == "unknown"
    assert post_images.images_for(conn) == 0
    llm_connections.set_cached_models(conn["id"], [{"id": "mara-7b", "outputs": ["text"]}],
                                      conn["rev"])
    assert post_images.capability(conn) == "unknown"
    assert post_images.images_for(conn) == 0


def test_post_images_reads_model_facts_after_the_legacy_setting(home):
    conn = _conn(base_url="http://localhost:1234/v1", model="mara-7b")
    facts.record_verified(conn["id"], "mara-7b", conn["rev"], {"vision": {"ok": True}})
    assert post_images.capability(conn) == "yes"
    # The legacy field is the post-image setting, and it still wins.
    off = {**conn, "vision": "off"}
    assert post_images.capability(off) == "no"


def test_post_images_on_a_preset_that_reads_no_images(home):
    # z.ai's wire protocol takes no image part (the preset table's `never`).
    conn = _conn(base_url="https://api.z.ai/api/paas/v4", model="mara-7b")
    assert post_images.capability(conn) == "no"
    assert post_images.capability({**conn, "vision": "on"}) == "yes"
