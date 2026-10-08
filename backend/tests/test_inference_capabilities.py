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
    # Spec 12: a failed test leaves the capability unverified, with the error.
    assert got["embed"] == Cap("unknown", "test", "404")


def test_a_failed_test_is_never_a_no():
    got = _resolve("custom", verified={"generate": {"ok": False, "at": "x"}})
    assert got["generate"] == Cap("unknown", "test", "")
    assert all(c.value != "no" for k, c in got.items() if k != "decide_native")
    # A non-string error is no error text.
    got = _resolve("custom", verified={"vision": {"ok": False, "error": {"x": 1}}})
    assert got["vision"] == Cap("unknown", "test", "")


def test_the_users_word_beats_a_failed_test_but_not_a_passed_one():
    failed = {"vision": {"ok": False, "at": "x", "error": "refused"}}
    assert _resolve("custom", verified=failed,
                    overrides={"vision": "yes"})["vision"] == Cap("yes", "user")
    assert _resolve("custom", verified=failed,
                    overrides={"vision": "no"})["vision"] == Cap("no", "user")
    assert _resolve("custom", verified=failed, vision="on")["vision"] == Cap("yes", "user")
    passed = {"vision": {"ok": True, "at": "x"}}
    assert _resolve("custom", verified=passed,
                    overrides={"vision": "no"})["vision"] == Cap("yes", "test")


def test_overrides_are_user():
    got = _resolve("custom", overrides={"prefill": "yes", "structured_output": "no"})
    assert got["prefill"] == Cap("yes", "user")
    assert got["structured_output"] == Cap("no", "user")


def test_facts_vision_and_prefill_are_user():
    assert _resolve("custom", vision="on")["vision"] == Cap("yes", "user")
    # Facts `vision: off` is a post-image preference, not a statement about
    # the model (spec 4.2, ruling 2): it contributes nothing.
    assert _resolve("custom", vision="off")["vision"] == Cap("unknown", "unknown")
    assert _resolve("custom", vision="")["vision"] == Cap("unknown", "unknown")
    assert _resolve("custom", prefill=True)["prefill"] == Cap("yes", "user")
    assert _resolve("custom", prefill=False)["prefill"] == Cap("no", "user")


def test_zai_may_read_images():
    # Spec 6.1 rules out embeddings on z.ai, never vision: the catalog decides.
    assert _resolve("zai")["vision"] == Cap("unknown", "unknown")
    assert _resolve("zai", row={"id": "m", "vision": True})["vision"] == Cap("yes", "catalog")


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


def test_catalog_decisions_are_decide_native():
    dec = _resolve("openrouter", row={"id": "m", "outputs": ["decisions"]})
    assert dec["decide_native"] == Cap("yes", "catalog")
    assert dec["generate"] == Cap("no", "catalog")
    # A stated list without "decisions" says nothing about it -- never `no`.
    text = _resolve("openrouter", row={"id": "m", "outputs": ["text"]})
    assert text["decide_native"] == Cap("unknown", "unknown")
    # The adapter's no still wins over the catalog's yes.
    assert _resolve("custom", row={"id": "m", "outputs": ["decisions"]})[
        "decide_native"] == Cap("no", "adapter")
    # A test call beats the catalog -- a failed one as unverified, not a no.
    assert _resolve("openrouter", row={"id": "m", "outputs": ["decisions"]},
                    verified={"decide_native": {"ok": False}})[
        "decide_native"] == Cap("unknown", "test")


def test_a_row_without_outputs_says_nothing_about_generate_or_embed():
    got = _resolve("custom", row={"id": "mara-7b", "vision": None})
    assert got["generate"] == Cap("unknown", "unknown")
    assert got["embed"] == Cap("unknown", "unknown")


def test_catalog_vision():
    assert _resolve("custom", row={"id": "m", "vision": True})["vision"] == Cap("yes", "catalog")
    assert _resolve("custom", row={"id": "m", "vision": False})["vision"] == Cap("no", "catalog")
    assert _resolve("custom", row={"id": "m", "vision": None})["vision"] == Cap("unknown", "unknown")


def test_catalog_params_say_structured_output_and_absence_says_nothing():
    got = _resolve("custom", row={"id": "m", "params": ["temperature", "structured_outputs"]})
    assert got["structured_output"] == Cap("yes", "catalog")
    # `response_format` alone also covers JSON mode, which holds the reply to
    # no schema: it says nothing (plan Minor 5).
    for params in (["temperature", "response_format"], ["temperature"]):
        got = _resolve("custom", row={"id": "m", "params": params})
        assert got["structured_output"] == Cap("unknown", "unknown")


def test_anthropic_features_structured_output():
    yes = _resolve("anthropic", row={"id": "m", "outputs": ["text"],
                                     "features": {"structured_output": True}})
    assert yes["structured_output"] == Cap("yes", "catalog")
    no = _resolve("anthropic", row={"id": "m", "outputs": ["text"],
                                    "features": {"structured_output": False}})
    assert no["structured_output"] == Cap("no", "catalog")


def test_the_catalog_says_nothing_about_stream_or_prefill():
    row = {"id": "m", "outputs": ["text", "embeddings"],
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
                   row={"id": "test-embed-1", "outputs": ["embeddings", "decisions"]},
                   verified={"embed": {"ok": True, "at": "x"}},
                   overrides={"embed": "yes", "decide_native": "yes"})
    assert got["embed"] == Cap("no", "adapter")
    assert got["decide_native"] == Cap("no", "adapter")
    got = _resolve("claude", row={"id": "m", "vision": True},
                   verified={"vision": {"ok": True, "at": "x"}}, vision="on")
    assert got["vision"] == Cap("no", "adapter")


def test_test_beats_user_beats_catalog():
    row = {"id": "m", "vision": True, "outputs": ["text"]}
    assert _resolve("custom", row=row,
                    overrides={"vision": "no"})["vision"] == Cap("no", "user")
    # Facts `vision: off` leaves the catalog's word standing (ruling 2).
    assert _resolve("custom", row=row, vision="off")["vision"] == Cap("yes", "catalog")
    assert _resolve("custom", row=row, overrides={"vision": "no"},
                    verified={"vision": {"ok": True, "at": "x"}})["vision"] == Cap("yes", "test")
    assert _resolve("custom", row=row,
                    verified={"generate": {"ok": False, "at": "x"}})["generate"] == Cap(
        "unknown", "test")
    assert _resolve("custom", row=row,
                    overrides={"vision": "no"})["vision"] == Cap("no", "user")


def test_a_failed_test_never_lifts_a_catalogs_no():
    """A failed test is `unknown`, and an `unknown` never overrides a `no`
    from a lower source that knows: the catalog's `no` stands, for every
    capability (spec 5.3: a failed test is never what decides a refusal,
    either way). It still outranks the catalog's `yes`."""
    failed = {"ok": False, "at": "x", "error": "404"}
    row = {"id": "m", "outputs": ["text"], "vision": False}
    got = _resolve("custom", row=row, verified={"embed": failed, "vision": failed})
    assert got["embed"] == Cap("no", "catalog")
    assert got["vision"] == Cap("no", "catalog")
    # Only a `no` stands against it: the catalog's `yes` is still outranked.
    assert _resolve("custom", row=row, verified={"generate": failed})["generate"] == Cap(
        "unknown", "test", "404")
    # A guess is no knowledge: a failed test outranks the name rule's `no`.
    assert _resolve("custom", model="test-embed-1",
                    verified={"generate": failed})["generate"] == Cap("unknown", "test", "404")
    # The user's word and a passed test still outrank the catalog's `no`.
    assert _resolve("custom", row=row, verified={"embed": failed},
                    overrides={"embed": "yes"})["embed"] == Cap("yes", "user")
    assert _resolve("custom", row=row,
                    verified={"embed": {"ok": True, "at": "x"}})["embed"] == Cap("yes", "test")


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
    assert capabilities.group_for(_resolve("claude"), "vision", P["claude"]) == (
        "hidden", "Claude subscription reads no images")
    assert capabilities.group_for(_resolve("zai"), "vision", P["zai"])[0] == "unverified"
    assert capabilities.group_for(_resolve("anthropic"), "embed", P["anthropic"]) == (
        "hidden", "Anthropic API serves no embeddings")


def test_group_hidden_by_another_source_names_that_source():
    row = {"id": "m", "outputs": ["text"]}
    assert capabilities.group_for(_resolve("custom", row=row), "embed", P["custom"]) == (
        "hidden", "the catalog says this model does not make embeddings")
    # A failed test hides nothing: the row stays unverified, the error shown.
    caps = _resolve("custom", verified={"embed": {"ok": False, "at": "x", "error": "404"}})
    assert capabilities.group_for(caps, "embed", P["custom"]) == (
        "unverified", "a test call failed: 404")
    caps = _resolve("custom", row={"id": "m", "outputs": ["embeddings"]},
                    verified={"embed": {"ok": False, "at": "x"}})
    assert capabilities.group_for(caps, "embed", P["custom"]) == (
        "unverified", "a test call failed")
    caps = _resolve("custom", overrides={"vision": "no"})
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
    old = _conn(base_url="http://localhost:1234/v1", model="mara-7b")
    assert facts.record_verified(old["id"], "mara-7b", old["rev"], {"vision": {"ok": True}})
    llm_connections.update_connection(old["id"], base_url="http://localhost:5678/v1")
    conn = llm_connections.read_connection_raw(old["id"])
    assert conn["rev"] != old["rev"]
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
    got = capabilities.caps_for(None)
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
    # The setting is on, so a zero below is the capability's answer, not the limit's.
    assert post_images.limit() > 0
    assert post_images.capability(conn) == "unknown"
    assert post_images.images_for(conn) == 0
    llm_connections.set_cached_models(conn["id"], [{"id": "mara-7b", "outputs": ["text"]}],
                                      conn["rev"])
    assert post_images.capability(conn) == "unknown"
    assert post_images.images_for(conn) == 0
    llm_connections.set_cached_models(conn["id"], [{"id": "mara-7b", "vision": False}],
                                      conn["rev"])
    assert post_images.capability(conn) == "no"
    assert post_images.images_for(conn) == 0


def test_post_images_reads_model_facts_after_the_legacy_setting(home):
    conn = _conn(base_url="http://localhost:1234/v1", model="mara-7b")
    facts.record_verified(conn["id"], "mara-7b", conn["rev"], {"vision": {"ok": True}})
    assert post_images.capability(conn) == "yes"
    # The legacy field is the post-image setting, and it still wins.
    off = {**conn, "vision": "off"}
    assert post_images.capability(off) == "no"


def test_post_images_on_zai_answers_as_it_did(home):
    # Nothing rules vision out on z.ai: auto is the catalog's answer, as before.
    config.write_config(send_images="on")
    conn = _conn(base_url="https://api.z.ai/api/paas/v4", model="mara-7b")
    assert providers.infer(conn).id == "zai"
    assert post_images.capability(conn) == "unknown"
    assert post_images.reach(conn) == "unknown"
    assert post_images.images_for(conn) == 0
    for vision, want in ((True, "yes"), (False, "no"), (None, "unknown")):
        llm_connections.set_cached_models(conn["id"], [{"id": "mara-7b", "vision": vision}],
                                          conn["rev"])
        assert post_images.capability(conn) == want
        assert post_images.reach(conn) == want
    assert post_images.capability({**conn, "vision": "on"}) == "yes"
    assert post_images.capability({**conn, "vision": "off"}) == "no"


def test_anthropic_prefill_follows_the_model():
    """Claude 4.6 and later refuse a prefill: a hard adapter `no`. An older
    model takes one, so it stays possible -- unknown until someone says."""
    assert _resolve("anthropic", model="claude-opus-4-7")["prefill"] == Cap("no", "adapter")
    older = _resolve("anthropic", model="claude-haiku-4-5-20251001")["prefill"]
    assert older.value == "unknown"
    stated = _resolve("anthropic", model="claude-haiku-4-5-20251001", prefill=True)
    assert stated["prefill"].value == "yes"
