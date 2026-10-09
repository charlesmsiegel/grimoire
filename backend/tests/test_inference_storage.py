"""The store can hold the new inference settings (slice C, Task 1).

Roles, route choices and the format marker in `config.md`; a provider's
`preset` and `billing`; per-model stated facts; a campaign's own role and
route overrides with its marker. Invented names and fake keys only.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from fastapi import HTTPException

from grimoire import store
from grimoire.routes import common
from grimoire.store import (
    campaigns,
    config,
    inference_keys,
    llm_connections,
    locks,
    revision,
    routing,
    worlds,
)
from grimoire.store.frontmatter import parse_frontmatter
from grimoire.store.inference import capabilities, facts, providers
from tests import inference_fixtures


@pytest.fixture()
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


def _campaign(name: str = "Saltmarch") -> str:
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign(name, wid)


def _campaign_meta(cid: str) -> dict:
    meta, _ = parse_frontmatter(
        campaigns.paths.campaign_meta_path(cid).read_text(encoding="utf-8"))
    return meta


def _make_current() -> None:
    config.write_config(**{inference_keys.FORMAT_KEY: inference_keys.CURRENT_FORMAT})


# ---- capabilities (ruling 2) ----

def test_vision_off_is_not_a_capability_no():
    custom = providers.PRESETS["custom"]
    off = capabilities.resolve_caps(custom, "mara-7b", catalog_row=None,
                                    facts={"vision": "off"})
    assert off["vision"].value == "unknown"
    # The catalog's word survives a post-image "off".
    row = {"id": "mara-7b", "vision": True}
    got = capabilities.resolve_caps(custom, "mara-7b", catalog_row=row,
                                    facts={"vision": "off"})
    assert got["vision"] == capabilities.Cap("yes", "catalog")
    # "on" is still the user's yes, and the override is still the user's no.
    on = capabilities.resolve_caps(custom, "mara-7b", catalog_row=None, facts={"vision": "on"})
    assert on["vision"] == capabilities.Cap("yes", "user")
    no = capabilities.resolve_caps(custom, "mara-7b", catalog_row=row,
                                   facts={"overrides": {"vision": "no"}})
    assert no["vision"] == capabilities.Cap("no", "user")


# ---- the fresh store (ruling 13) ----

def test_a_fresh_store_is_born_at_format_2(home, monkeypatch):
    monkeypatch.delenv(inference_keys.AUTOMIGRATE_ENV, raising=False)
    assert not (home / "config.md").exists()
    cfg = config.read_config()
    assert cfg[inference_keys.FORMAT_KEY] == "2"
    raw, _ = parse_frontmatter((home / "config.md").read_text(encoding="utf-8"))
    assert raw[inference_keys.FORMAT_KEY] == "2"
    assert inference_keys.is_current(config.read_config())


def test_a_fresh_store_first_written_by_write_config_is_born_at_format_2(home, monkeypatch):
    monkeypatch.delenv(inference_keys.AUTOMIGRATE_ENV, raising=False)
    config.write_config(theme="dark")
    raw, _ = parse_frontmatter((home / "config.md").read_text(encoding="utf-8"))
    assert raw[inference_keys.FORMAT_KEY] == "2"


def test_an_existing_config_without_the_marker_stays_legacy(home, monkeypatch):
    monkeypatch.delenv(inference_keys.AUTOMIGRATE_ENV, raising=False)
    (home / "config.md").write_text("---\ntheme: dark\n---\n", encoding="utf-8")
    assert config.read_config()[inference_keys.FORMAT_KEY] == ""
    config.write_config(theme="system")
    raw, _ = parse_frontmatter((home / "config.md").read_text(encoding="utf-8"))
    assert inference_keys.FORMAT_KEY not in raw
    assert not inference_keys.is_current(config.read_config())


def test_the_background_switch_does_not_unbirth_a_fresh_store(home, monkeypatch):
    # `AUTOMIGRATE_ENV=0` (conftest sets it for the whole suite) means "no
    # background thread" and nothing more: a store this build creates is born
    # current whether or not the switch is on.
    monkeypatch.setenv(inference_keys.AUTOMIGRATE_ENV, "0")
    assert inference_keys.born_current()
    assert config.read_config()[inference_keys.FORMAT_KEY] == "2"


def test_a_test_store_is_born_an_upgraded_default_library(home):
    cfg = config.read_config()
    assert inference_keys.is_current(cfg)
    assert (cfg["role_primary_provider"], cfg["role_primary_model"]) == (
        "openrouter", config.DEFAULT_MODEL)
    assert config.birth_fields() == inference_fixtures.UPGRADED_DEFAULT


def test_the_suites_birth_is_set_where_an_undo_cannot_reach_it(monkeypatch):
    # `conftest.py` sets it at import, outside every test's `monkeypatch`, so
    # a test that lifts its own patches mid-test still births upgraded stores
    # (and a subprocess it spawns inherits the same birth).
    monkeypatch.undo()
    assert os.environ.get(config.TEST_BIRTH_ENV) == "upgraded-default"


@pytest.mark.product_birth
def test_a_product_store_is_born_with_the_marker_alone(home):
    cfg = config.read_config()
    assert inference_keys.is_current(cfg) and not cfg.get("role_primary_provider")
    assert config.birth_fields() == {inference_keys.FORMAT_KEY: inference_keys.CURRENT_FORMAT}


def test_legacy_store_is_format_1(home):
    inference_fixtures.legacy_store()
    assert (home / "config.md").read_text(encoding="utf-8") == "---\n---\n"
    assert not inference_keys.is_current(config.read_config())


def test_legacy_store_names_the_root_it_is_given(tmp_path):
    other = tmp_path / "elsewhere"
    inference_fixtures.legacy_store(other)
    assert (other / "config.md").read_text(encoding="utf-8") == "---\n---\n"


# ---- config.md carries every key ----

def test_read_config_round_trips_every_inference_key(home):
    inference_fixtures.legacy_store()  # a born store already holds the marker
    cfg = config.read_config()
    for key in inference_keys.GLOBAL_KEYS:
        assert cfg[key] == "", key
    values = {key: f"value-{i}" for i, key in enumerate(inference_keys.GLOBAL_KEYS)}
    config.write_config(**values)
    cfg = config.read_config()
    for key, value in values.items():
        assert cfg[key] == value, key
    # The legacy keys are still carried, untouched.
    for key in inference_keys.LEGACY_GLOBAL_KEYS:
        assert key in cfg


def test_is_current_sees_the_marker_through_read_config(home):
    inference_fixtures.legacy_store()
    assert not inference_keys.is_current(config.read_config())
    _make_current()
    assert inference_keys.is_current(config.read_config())


def test_is_newer():
    newer = inference_keys.is_newer
    assert newer({inference_keys.FORMAT_KEY: "3"})
    assert newer({inference_keys.FORMAT_KEY: 10})
    assert newer({inference_keys.FORMAT_KEY: " 4 "})
    for value in ("2", "1", "", "two", "2.5", "-3", None):
        assert not newer({inference_keys.FORMAT_KEY: value}), value
    assert not newer({})


def test_campaign_keys_exclude_the_embedding_role_global_routes_and_the_marker():
    g, c = inference_keys.GLOBAL_KEYS, inference_keys.CAMPAIGN_KEYS
    assert len(g) == len(set(g))
    assert len(c) == len(set(c))
    assert set(c) < set(g)
    assert inference_keys.FORMAT_KEY in g
    assert inference_keys.FORMAT_KEY not in c
    for part in ("provider", "model"):
        assert f"role_embedding_{part}" in g
        assert f"role_embedding_{part}" not in c
    assert "role_embedding_preset" not in g
    assert not any(k.startswith("role_embedding_fallback") for k in g)
    for route in routing.ROUTES:
        keys = {f"use_{route.key}", f"preset_{route.key}",
                *(f"use_{route.key}_{p}" for p in inference_keys.PARTS)}
        assert keys <= set(g), route.key
        if route.campaign_scoped:
            assert keys <= set(c), route.key
        else:
            assert not keys & set(c), route.key
    for role in inference_keys.GENERATIVE_ROLES:
        for part in inference_keys.PARTS:
            assert inference_keys.role_key(role, part) in c
            assert inference_keys.fallback_key(role, part) in c
    # 18 generative role slots, 2 embedding, 5 per route, the marker.
    assert len(g) == 18 + 2 + 5 * len(routing.ROUTES) + 1
    legacy = set(inference_keys.LEGACY_GLOBAL_KEYS)
    assert legacy == {"active_connection_id", "fallback_connection_id",
                      "embeddings_connection_id", "embeddings_model",
                      *routing.CONFIG_KEYS}
    assert not legacy & set(g)


def test_store_config_imports_cleanly():
    out = subprocess.run([sys.executable, "-c", "import grimoire.store.config"],
                         capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert not hasattr(store.inference, "keys")


# ---- providers ----

def test_connection_preset_and_billing_round_trip_and_keep_rev(home):
    cid = llm_connections.create_connection(
        "openai_compatible", "Saltmarch Local", base_url="http://localhost:1234/v1",
        preset="lmstudio", billing="metered")
    conn = llm_connections.read_connection_raw(cid)
    assert (conn["preset"], conn["billing"]) == ("lmstudio", "metered")
    llm_connections.set_cached_models(cid, [{"id": "mara-7b"}], conn["rev"])
    llm_connections.update_connection(cid, preset="custom", billing="subscription")
    after = llm_connections.read_connection_raw(cid)
    assert (after["preset"], after["billing"]) == ("custom", "subscription")
    assert after["rev"] == conn["rev"]
    assert llm_connections.cached_models(cid)["models"] == [{"id": "mara-7b"}]
    assert {"preset", "billing", "name"} <= llm_connections.REV_NEUTRAL_FIELDS
    assert llm_connections.MODEL_FIELDS == (
        "model", "vision", "prefill", "post_process", "reasoning_effort", "sampler_preset")


def test_renaming_a_connection_keeps_its_rev_and_catalog(home):
    cid = llm_connections.create_connection(
        "openai_compatible", "Saltmarch Local", base_url="http://localhost:1234/v1")
    rev = llm_connections.read_connection_raw(cid)["rev"]
    llm_connections.set_cached_models(cid, [{"id": "mara-7b"}], rev)
    llm_connections.update_connection(cid, name="Winifred's Box")
    after = llm_connections.read_connection_raw(cid)
    assert after["name"] == "Winifred's Box"
    assert after["rev"] == rev
    assert llm_connections.cached_models(cid)["models"] == [{"id": "mara-7b"}]
    # The key still restamps it.
    llm_connections.update_connection(cid, api_key="sk-fake-key")
    assert llm_connections.read_connection_raw(cid)["rev"] != rev


# ---- model facts ----

def test_set_stated_merges_and_validates(home):
    cid = llm_connections.create_connection(
        "openai_compatible", "Saltmarch Local", base_url="http://localhost:1234/v1")
    rev = llm_connections.read_connection_raw(cid)["rev"]
    facts.record_verified(cid, "mara-7b", rev, {"generate": {"ok": True}})
    facts.set_overrides(cid, "mara-7b", {"embed": "no"})
    facts.set_stated(cid, "mara-7b", vision="on")
    facts.set_stated(cid, "mara-7b", prefill=True)
    facts.set_stated(cid, "mara-7b", post_process="strict")
    got = facts.of(cid, "mara-7b", rev)
    assert (got["vision"], got["prefill"], got["post_process"]) == ("on", True, "strict")
    # What other writers put there survives.
    assert got["verified"]["generate"]["ok"] is True
    assert got["overrides"] == {"embed": "no"}
    # None leaves a field as it was.
    facts.set_stated(cid, "mara-7b", vision="off")
    got = facts.of(cid, "mara-7b", rev)
    assert (got["vision"], got["prefill"], got["post_process"]) == ("off", True, "strict")
    facts.set_stated(cid, "mara-7b", vision="", prefill=False, post_process="none")
    got = facts.of(cid, "mara-7b", rev)
    assert (got["vision"], got["prefill"], got["post_process"]) == ("", False, "none")
    before = llm_connections.facts_path(cid).read_text(encoding="utf-8")
    for bad in ({"vision": "maybe"}, {"vision": "yes"}, {"prefill": "true"},
                {"prefill": 1}, {"post_process": "loose"}, {"post_process": ""}):
        with pytest.raises(ValueError):
            facts.set_stated(cid, "mara-7b", **bad)
    with pytest.raises(ValueError):
        facts.set_stated("../escape", "mara-7b", vision="on")
    assert llm_connections.facts_path(cid).read_text(encoding="utf-8") == before


# ---- campaigns ----

def test_set_campaign_inference_is_locked_bumps_revision_and_refuses_other_keys(
        home, monkeypatch):
    cid = _campaign()
    token = revision.current(cid)
    held: list[bool] = []
    real_bump = revision.bump

    def spy(c: str) -> str:
        held.append(locks.campaign_lock(c)._is_owned())
        return real_bump(c)

    monkeypatch.setattr(revision, "bump", spy)
    assert campaigns.set_campaign_inference(cid, {"use_scene": "fast",
                                                  "role_fast_model": "mara-7b"}) is True
    assert held == [True]
    assert revision.current(cid) != token
    meta = _campaign_meta(cid)
    assert (meta["use_scene"], meta["role_fast_model"]) == ("fast", "mara-7b")
    # A blank value removes the key.
    assert campaigns.set_campaign_inference(cid, {"use_scene": "  "}) is True
    assert "use_scene" not in _campaign_meta(cid)
    before = campaigns.paths.campaign_meta_path(cid).read_text(encoding="utf-8")
    for bad in ({"route_scene": "openrouter"}, {inference_keys.FORMAT_KEY: "2"},
                {"use_tagline": "fast"}, {"role_embedding_provider": "openrouter"},
                {"name": "Mara"}, {"use_scene": "fast", "theme": "dark"}):
        with pytest.raises(ValueError):
            campaigns.set_campaign_inference(cid, bad)
    assert campaigns.paths.campaign_meta_path(cid).read_text(encoding="utf-8") == before
    with pytest.raises(campaigns.paths.CampaignNotFound):
        campaigns.set_campaign_inference("no-such-campaign", {"use_scene": "fast"})


def test_set_campaign_inference_marks_the_campaign_on_a_current_store(home):
    inference_fixtures.legacy_store()
    cid = _campaign()
    # A legacy store: the key is written, the campaign is not marked.
    campaigns.set_campaign_inference(cid, {"use_scene": "fast"})
    assert inference_keys.FORMAT_KEY not in _campaign_meta(cid)
    _make_current()
    campaigns.set_campaign_inference(cid, {"use_opener": "fast"})
    meta = _campaign_meta(cid)
    assert meta[inference_keys.FORMAT_KEY] == inference_keys.CURRENT_FORMAT
    assert inference_keys.is_current(meta)


def test_set_campaign_inference_that_changes_nothing_writes_nothing(home):
    cid = _campaign()
    assert campaigns.set_campaign_inference(cid, {"use_scene": "fast"}) is True
    path = campaigns.paths.campaign_meta_path(cid)
    text, stamp, token = path.read_text(encoding="utf-8"), path.stat().st_mtime_ns, \
        revision.current(cid)
    assert campaigns.set_campaign_inference(cid, {"use_scene": "fast"}) is False
    assert campaigns.set_campaign_inference(cid, {"use_opener": ""}) is False
    assert campaigns.set_campaign_inference(cid, {}) is False
    assert path.read_text(encoding="utf-8") == text
    assert path.stat().st_mtime_ns == stamp
    assert revision.current(cid) == token


def test_a_new_campaign_is_marked_on_a_current_store(home):
    inference_fixtures.legacy_store()
    legacy = _campaign("Saltmarch")
    assert inference_keys.FORMAT_KEY not in _campaign_meta(legacy)
    _make_current()
    cid = campaigns.create_campaign("Winifred", worlds.list_worlds()[0]["id"])
    assert _campaign_meta(cid)[inference_keys.FORMAT_KEY] == inference_keys.CURRENT_FORMAT


# ---- the shared refusals (Tasks 4 and 5 call them) ----

def test_refuse_newer_and_refuse_unmigrated(home):
    inference_fixtures.legacy_store()
    def kind(fn) -> str:
        try:
            fn()
        except HTTPException as exc:
            assert exc.status_code == 409
            return exc.detail["kind"]
        return ""

    # A legacy store: not newer, but not migrated either.
    assert kind(common.refuse_newer) == ""
    assert kind(common.refuse_unmigrated) == "not_migrated"
    try:
        common.refuse_unmigrated()
    except HTTPException as exc:
        assert exc.detail["status"]["state"] == "pending"
    _make_current()
    assert kind(common.refuse_newer) == ""
    assert kind(common.refuse_unmigrated) == ""
    config.write_config(**{inference_keys.FORMAT_KEY: "3"})
    assert kind(common.refuse_newer) == "newer_format"
    assert kind(common.refuse_unmigrated) == "newer_format"
