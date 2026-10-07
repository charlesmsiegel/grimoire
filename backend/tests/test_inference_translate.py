"""The pure legacy -> roles translation (inference refactor, slice A)."""

from __future__ import annotations

from grimoire.store import sampler_presets
from grimoire.store.inference import keys, translate

CONNS = {
    "or": {"model": "vendor/a", "sampler_preset": "warm"},
    "claude": {"model": "", "sampler_preset": ""},
    "spare": {"model": "spare-model", "sampler_preset": "calm"},
    "local": {"model": "local-model", "sampler_preset": "cold"},
}


def lookup(cid: str):
    return CONNS.get(cid)


def test_the_active_connection_becomes_primary():
    view = translate.global_view({"active_connection_id": "or"}, lookup)
    assert view[keys.role_key("primary", "provider")] == "or"
    assert view[keys.role_key("primary", "model")] == "vendor/a"
    assert view[keys.role_key("primary", "preset")] == "warm"
    assert not view.get(keys.role_key("fast", "provider"))
    assert not view.get(keys.role_key("decision", "provider"))


def test_the_active_id_is_used_raw():
    view = translate.global_view({"active_connection_id": "  or  "}, lookup)
    assert view[keys.role_key("primary", "provider")] == "  or  "


def test_an_empty_claude_model_stays_empty():
    view = translate.global_view({"active_connection_id": "claude"}, lookup)
    assert view[keys.role_key("primary", "provider")] == "claude"
    assert view[keys.role_key("primary", "model")] == ""


def test_the_global_fallback_backs_every_generative_role():
    view = translate.global_view(
        {"active_connection_id": "or", "fallback_connection_id": "spare"}, lookup)
    for role in keys.GENERATIVE_ROLES:
        assert view[keys.fallback_key(role, "provider")] == "spare"
        assert view[keys.fallback_key(role, "model")] == "spare-model"
        assert view[keys.fallback_key(role, "preset")] == "calm"
    assert not any(k.startswith("role_embedding_fallback_") for k in view)


def test_embeddings_become_the_embedding_role():
    view = translate.global_view(
        {"embeddings_connection_id": "  local ", "embeddings_model": " m "}, lookup)
    assert view[keys.role_key("embedding", "provider")] == "local"
    assert view[keys.role_key("embedding", "model")] == "m"


def test_embedding_role_needs_no_lookup():
    cfg = {"embeddings_connection_id": "  local ", "embeddings_model": " m "}
    assert translate.embedding_role(cfg) == ("local", "m")


def test_embedding_role_reads_the_view_keys_when_current():
    cfg = {keys.FORMAT_KEY: "2", keys.role_key("embedding", "provider"): "x",
           keys.role_key("embedding", "model"): "y",
           "embeddings_connection_id": "ignored"}
    assert translate.embedding_role(cfg) == ("x", "y")
    assert translate.embedding_role({}) == ("", "")


def test_a_route_connection_becomes_a_pin():
    view = translate.global_view({"route_dossier": "  local  "}, lookup)
    assert view[keys.use_key("dossier")] == keys.PIN == "model"
    assert view[keys.pin_key("dossier", "provider")] == "local"
    assert view[keys.pin_key("dossier", "model")] == "local-model"
    assert view[keys.pin_key("dossier", "preset")] == "cold"


def test_split_routes_copy_their_parents():
    view = translate.global_view(
        {"route_summary": "claude", "preset_summary": sampler_presets.PRESET_CLEAR},
        lookup)
    for route in ("summary", "scene_break"):
        assert view[keys.use_key(route)] == "model"
        assert view[keys.pin_key(route, "provider")] == "claude"
        assert view[keys.preset_key(route)] == sampler_presets.PRESET_CLEAR


def test_a_dangling_route_id_is_kept_for_the_cascade_to_walk_past():
    view = translate.global_view({"route_voice": "gone"}, lambda cid: None)
    assert view[keys.use_key("voice")] == "model"
    assert view[keys.pin_key("voice", "provider")] == "gone"
    assert view[keys.pin_key("voice", "model")] == ""


def test_a_blank_route_says_nothing():
    view = translate.global_view({"route_voice": "   ", "preset_voice": " "}, lookup)
    assert keys.use_key("voice") not in view
    assert keys.preset_key("voice") not in view


def test_a_campaign_view_carries_routes_only():
    view = translate.campaign_view(
        {"route_scene": "local", "route_tagline": "x"}, lookup)
    assert view[keys.use_key("scene")] == "model"
    assert view[keys.pin_key("speaker", "provider")] == "local"
    assert not any(k.endswith("tagline") or "_tagline_" in k for k in view)
    assert not any(k.startswith("role_") for k in view)


def test_a_format_two_store_passes_through():
    cfg = {keys.FORMAT_KEY: "2", "route_scene": "local", "active_connection_id": "or"}
    assert translate.is_current(cfg)
    assert translate.global_view(cfg, lookup) is cfg
    assert translate.campaign_view(cfg, lookup) is cfg
    assert not translate.is_current({"inference_format": "1"})
    assert not translate.is_current({})


def test_a_connections_own_preset_is_stripped():
    conns = {"p": {"model": "m", "sampler_preset": "  warm  "}}
    cfg = {"active_connection_id": "p", "fallback_connection_id": "p",
           "route_dossier": "p"}
    view = translate.global_view(cfg, conns.get)
    assert view[keys.role_key("primary", "preset")] == "warm"
    assert view[keys.fallback_key("fast", "preset")] == "warm"
    assert view[keys.pin_key("dossier", "preset")] == "warm"


def test_a_padded_preset_value_is_stripped_at_both_scopes():
    g = translate.global_view({"preset_scene": "  warm "}, lookup)
    assert g[keys.preset_key("scene")] == "warm"
    assert g[keys.preset_key("speaker")] == "warm"
    c = translate.campaign_view({"preset_scene": "  warm "}, lookup)
    assert c[keys.preset_key("scene")] == "warm"
    assert c[keys.preset_key("speaker")] == "warm"


def test_the_clear_sentinel_passes_through_a_campaign_view():
    c = translate.campaign_view(
        {"preset_summary": sampler_presets.PRESET_CLEAR}, lookup)
    assert c[keys.preset_key("summary")] == sampler_presets.PRESET_CLEAR
    assert c[keys.preset_key("scene_break")] == sampler_presets.PRESET_CLEAR


def test_a_dangling_fallback_id_is_kept_raw():
    view = translate.global_view({"fallback_connection_id": "gone"}, lambda cid: None)
    for role in keys.GENERATIVE_ROLES:
        assert view[keys.fallback_key(role, "provider")] == "gone"
        assert view[keys.fallback_key(role, "model")] == ""


def test_a_whitespace_only_fallback_id_is_kept_raw():
    view = translate.global_view({"fallback_connection_id": "  "}, lookup)
    for role in keys.GENERATIVE_ROLES:
        assert view[keys.fallback_key(role, "provider")] == "  "
