"""Shared production construction preserves startup authority across provider settings."""
import json
from dataclasses import replace

import pytest
from scene_fixtures import MARA, SERAPHINE
from store_helpers import OWNER
from textapi_fixtures import Device, V1_CHAT, desktop_library
from grimoire.app.adapters.providers import configure
from grimoire.app.agendas import simple_agenda
from grimoire.app.campaign_config import parse_campaign_config
from grimoire.app.campaign_services import configure as configure_campaign
from grimoire.app.config import config_json, parse_model_config
from grimoire.app.appapi import AppApi
from grimoire.textapi import TextApi, ApiRefused
from grimoire.scene import SceneRefused

MODEL = {"provider_id": "local", "base_url": "http://127.0.0.1:9/v1", "model": "synthetic"}


def construct(device, path):
    startup = simple_agenda(MARA, "Protect startup", ["Watch the hatch"])
    api = TextApi(device.api.store, device.api.authority, owner=OWNER,
                  voices=device.api.runner.authored, agendas=iter([startup]))
    campaign = replace(parse_campaign_config({"version": 1}), agendas=(startup,))
    hub = configure_campaign(api, campaign)
    inference = configure(api, path, environ={})
    app = AppApi(api)
    app.call("continue", {"chat": str(V1_CHAT)})
    return api, app, inference, hub, startup


def inspect(app, actor=MARA):
    return app.call("providers.context", {"chat": str(V1_CHAT), "actor": str(actor)})


def test_startup_survives_absent_invalid_conflicting_reload_and_disjoint_saves(tmp_path):
    folder, _ = desktop_library(tmp_path / "fixture")
    path = tmp_path / "model.json"
    with Device(folder, runtime=False) as device:
        api, app, inference, hub, startup = construct(device, path)
        assert api.startup_agendas == (startup,)
        with pytest.raises(AttributeError):
            api.startup_agendas = ()
        for contents in (None, '{bad', json.dumps(config_json(replace(parse_model_config(MODEL), agendas=(startup,))))):
            if contents is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(contents)
            view = inspect(app)
            assert view["agenda"]["editable"] is False
            assert view["agenda"]["source"] == "campaign_startup"
            assert "Watch the hatch" in view["context"]["system"]
            assert api.runtime is None
        saved = app.call("providers.save_models", {"expected": view["etag"], "settings": MODEL})
        for remove in (False, True):
            with pytest.raises(ApiRefused, match="startup"):
                app.call("providers.save_agenda", {"chat": str(V1_CHAT), "actor": str(MARA),
                    "expected": saved["etag"], "aim": "Overwrite", "actions": ["Leave"], "remove": remove})
        saved = app.call("providers.save_agenda", {"chat": str(V1_CHAT), "actor": str(SERAPHINE),
            "expected": saved["etag"], "aim": "Model watch", "actions": ["Guard the door"]})
        assert "Watch the hatch" in inspect(app)["context"]["system"]
        assert inspect(app, SERAPHINE)["agenda"]["aim"] == "Model watch"
        assert len(saved["settings"]["agendas"]) == 1
        before, runtime = path.read_bytes(), api.runtime
        overlapping = {**saved["settings"], "agendas": config_json(replace(parse_model_config(MODEL), agendas=(startup,)))["agendas"]}
        checked = app.call("providers.check_models", {"settings": overlapping})
        assert not checked["ok"] and "startup" in checked["problems"][0]["message"]
        with pytest.raises(ApiRefused):
            app.call("providers.save_models", {"expected": saved["etag"], "settings": overlapping})
        assert path.read_bytes() == before and api.runtime is runtime
        assert hub.agendas == {MARA: startup}
        path.write_text(json.dumps({**{k: v for k, v in saved["settings"].items() if k != "key_set"}, "key_env": "MISSING_API_KEY"}))
        assert "Watch the hatch" in inspect(app)["context"]["system"]
        assert inspect(app, SERAPHINE)["agenda"]["aim"] == "Model watch"
        assert api.runtime is None
        path.unlink()
        assert inspect(app, SERAPHINE)["agenda"]["aim"] == ""
        assert "Watch the hatch" in inspect(app)["context"]["system"]


@pytest.mark.parametrize("bad", ["", b"", {}, None, 1])
def test_constructor_rejects_non_collection_agenda_shapes(tmp_path, bad):
    folder, _ = desktop_library(tmp_path / "fixture")
    with Device(folder, runtime=False) as device, pytest.raises(SceneRefused):
        TextApi(device.api.store, device.api.authority, owner=OWNER, agendas=bad)
