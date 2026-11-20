"""112 actual app wiring with synthetic libraries; inspection never calls a model."""
import json

import pytest
from scene_fixtures import MARA, SERAPHINE
from store_helpers import OWNER
from textapi_fixtures import Device, V1_CHAT, desktop_library

from grimoire.app.adapters.providers import configure
from grimoire.app.appapi import AppApi
from grimoire.app.config import config_json, parse_model_config, ConfigError
from grimoire.app.inference import SettingsFile
from grimoire.textapi import ApiRefused


def test_settings_roundtrip_typed_agendas_and_default_off():
    raw = {"provider_id": "local", "base_url": "http://127.0.0.1:9/v1", "model": "synthetic"}
    assert parse_model_config(raw).decision_calls == 0
    agenda = {"actor": str(MARA), "goals": [{"id": "safety", "aim": "Stay safe", "priority": 10,
        "approaches": [{"id": "wait", "action": "Wait quietly", "needs": [], "tags": []},
                       {"id": "leave", "action": "Walk outside", "needs": [], "tags": ["flight"]}]}],
        "limits": [{"id": "stay", "rule": "Do not leave company", "forbids": ["flight"],
                    "when": [{"present": str(SERAPHINE)}]}]}
    settings = {**raw, "decision": 1, "agendas": [agenda]}
    assert config_json(parse_model_config(settings))["agendas"] == [agenda]
    with pytest.raises(ConfigError):
        parse_model_config({**settings, "agendas": [agenda, agenda]})
    with pytest.raises(ConfigError):
        parse_model_config({**raw, "decision": True})


def test_current_inspection_and_tactics_use_actual_app_services(tmp_path):
    folder, _ = desktop_library(tmp_path / "fixture")
    path = tmp_path / "model.json"
    with Device(folder, runtime=False) as device:
        configure(device.api, path, environ={})
        app = AppApi(device.api)
        app.call("continue", {"chat": str(V1_CHAT)})
        before = {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
        selected = app.call("providers.context", {"chat": str(V1_CHAT), "actor": str(MARA)})
        assert selected["current"] is True
        assert selected["routes"] == []
        assert selected["context"]["actor"] == str(MARA)
        assert selected["context"]["items"]
        assert "hidden" not in selected["context"]
        assert not path.exists()
        after = {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
        assert before == after
        saved = app.call("providers.save_models", {"expected": None, "settings": {
            "provider_id": "local", "base_url": "http://127.0.0.1:9/v1", "model": "synthetic", "decision": 1}})
        saved = app.call("providers.save_agenda", {"chat": str(V1_CHAT), "actor": str(MARA),
            "expected": saved["etag"], "aim": "Keep watch", "actions": ["Wait quietly", "Check the door"]})
        assert len(saved["settings"]["agendas"]) == 1
        assert device.api.runner.decisions is not None
        with pytest.raises(ApiRefused, match="changed"):
            app.call("providers.save_agenda", {"chat": str(V1_CHAT), "actor": str(MARA),
                "expected": None, "aim": "Keep watch", "actions": ["Wait quietly"]})
    with Device(folder, runtime=False) as device:
        configure(device.api, path, environ={})
        app = AppApi(device.api)
        view = app.call("providers.context", {"chat": str(V1_CHAT), "actor": str(MARA)})
        assert view["agenda"]["aim"] == "Keep watch"
        assert view["agenda"]["actions"] == ["Wait quietly", "Check the door"]
        assert json.loads(path.read_text())["decision"] == 1


def test_tactics_edits_preserve_other_policies_and_refuse_advanced_or_player_edits(tmp_path):
    folder, _ = desktop_library(tmp_path / "fixture")
    path = tmp_path / "model.json"
    with Device(folder, runtime=False) as device:
        configure(device.api, path, environ={})
        app = AppApi(device.api)
        app.call("continue", {"chat": str(V1_CHAT)})
        saved = app.call("providers.save_models", {"expected": None, "settings": {
            "provider_id": "local", "base_url": "http://127.0.0.1:9/v1", "model": "synthetic", "agendas": [
                {"actor": str(SERAPHINE), "goals": [{"id": "watch", "aim": "Stand watch", "priority": 90,
                  "approaches": [{"id": "stay", "action": "Stay nearby"}]}]}]}})
        advanced = saved["settings"]["agendas"][0]
        saved = app.call("providers.save_agenda", {"chat": str(V1_CHAT), "actor": str(MARA),
            "expected": saved["etag"], "aim": "Keep watch", "actions": ["Wait quietly", "Check the door"]})
        assert saved["settings"]["agendas"][0] == advanced
        before = path.read_bytes()
        for actor in (str(SERAPHINE), str(device.api.runner.open(V1_CHAT).persona)):
            with pytest.raises(ApiRefused):
                app.call("providers.save_agenda", {"chat": str(V1_CHAT), "actor": actor,
                    "expected": saved["etag"], "aim": "Replace", "actions": ["Wait"]})
        assert path.read_bytes() == before
        assert app.call("providers.context", {"chat": str(V1_CHAT), "actor": str(SERAPHINE)})["agenda"]["editable"] is False
        with pytest.raises(ApiRefused):
            app.call("providers.context", {"chat": str(V1_CHAT), "actor": str(device.api.runner.open(V1_CHAT).persona)})
        with pytest.raises(ApiRefused):
            app.call("providers.context", {"chat": str(V1_CHAT), "actor": str(MARA).replace("/mara", "/foreign")})


def test_settings_refuse_v1_and_library_destinations_before_write(tmp_path, monkeypatch):
    folder, _ = desktop_library(tmp_path / "fixture")
    v1 = tmp_path / "private-v1"
    v1.mkdir()
    monkeypatch.setenv("GRIMOIRE_HOME", str(v1))
    for path in (v1 / "model.json", folder / "model.json"):
        with Device(folder, runtime=False) as device:
            configure(device.api, path, environ={})
            app = AppApi(device.api)
            assert app.call("providers.models")["generation"]["available"] is False
            with pytest.raises(ApiRefused, match="outside V1"):
                app.call("providers.save_models", {"expected": None, "settings": {
                    "provider_id": "local", "base_url": "http://127.0.0.1:9/v1", "model": "synthetic"}})
            assert not path.exists()


@pytest.mark.parametrize("nested", [False, True])
def test_settings_file_beside_v1_is_allowed_including_new_parent(tmp_path, monkeypatch, nested):
    home = tmp_path / "synthetic-home"
    home.mkdir()
    (home / ".grimoire").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GRIMOIRE_HOME", str(home / ".grimoire"))
    path = home / "settings" / "model.json" if nested else home / "model.json"
    settings = SettingsFile(path)
    assert settings.read() == (None, None)
    config = parse_model_config({"provider_id": "local", "base_url": "http://127.0.0.1:9/v1", "model": "synthetic"})
    revision = settings.write(config, None)
    data, found = settings.read()
    assert found == revision and json.loads(data)["model"] == "synthetic"
