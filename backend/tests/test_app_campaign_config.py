"""113 production configuration uses synthetic records and scripted providers only."""
from __future__ import annotations

import json

import pytest

from generation_fixtures import say
from provider_servers import base_url, sse_lines, sse_server, stream
from scene_fixtures import attach_tamsin, card_library
from store_helpers import OWNER
from textapi_fixtures import Device

from grimoire.app.campaign_config import CampaignConfigError, load_campaign_config, parse_campaign_config
from grimoire.app.campaign_services import LABEL_PRINCIPAL, PROPOSAL_GRANT, configure
from grimoire.app.config import parse_model_config
from grimoire.app.cli import main
from grimoire.app.pricing import cost_view, price_table
from grimoire.testing.providers import ScriptedTransport
from grimoire.security import Permission
from grimoire.textapi import TextApi


MODEL = {"provider_id": "labels", "base_url": "https://labels.example/v1", "model": "label-model"}


def test_strict_bounded_configuration_never_echoes_authored_content(tmp_path):
    path = tmp_path / "campaign.json"
    for text in ('{"version":1,"version":1}', '{"version":1,"secret":"private-canary"}',
                 '{"version":true}', '{"version":1,"agendas":"private-canary"}',
                 '{"version":1,"labelling":{"key_env":"private-canary"}}', ' ' * (1024 * 1024 + 1)):
        path.write_text(text)
        with pytest.raises(CampaignConfigError) as caught:
            load_campaign_config(path)
        assert "private-canary" not in str(caught.value)
    assert parse_campaign_config({"version": 1}).agendas == ()


def test_unknown_nested_fields_and_duplicate_stage_are_refused():
    stage = {"chat": "campaign:realm#chat/scene", "as_of": 0, "areas": [{"area_id": "room"}]}
    with pytest.raises(CampaignConfigError):
        parse_campaign_config({"version": 1, "stages": [stage, stage]})
    stage["areas"][0]["silently_ignored"] = True
    with pytest.raises(CampaignConfigError):
        parse_campaign_config({"version": 1, "stages": [stage]})


def test_configured_labeller_is_untrusted_metered_and_cannot_apply(tmp_path):
    folder, chat, _ = card_library(tmp_path)
    response = stream(sse_lines(deltas=('{"factors":[],"stakes":[]}',),
                                usage={"prompt_tokens": 20, "completion_tokens": 3}, model="label-served"))
    with sse_server(response) as server:
        config = parse_campaign_config({"version": 1, "labelling": {**MODEL, "base_url": base_url(server)}})
        with Device(folder, runtime=False) as device:
            hub = configure(device.api, config)
            hub.runner.seat(chat, OWNER)
            hub.runner.post(chat, hub.handle, "*I open the hatch*")
            label = hub.call("label", {"chat": str(chat)})["done"]["label"]
            assert label["state"] == "proposed"
            pending = hub.runner.open(chat).pending
            attempt = pending.labels[-1].calls[0].attempts[0]
            assert attempt.route.startswith("labels@") and attempt.route.endswith("/label-model")
            assert attempt.measurement == "provider_reported"
            assert attempt.usage.input_tokens == 20 and attempt.usage.output_tokens == 3
            price = price_table(parse_model_config({**MODEL, "model": "narration-model", "price_prompt": "3", "price_reply": "15"}))
            assert cost_view([attempt], price)["cost"] is None
            assert hub.runner.label_handle.trusted is False
            assert not hub.runner.label_handle.authorize(Permission.APPLY, "campaigns/realm", b"{}").allowed
            grant = next(g for g in device.api.authority.active_grants() if g.grant_id == PROPOSAL_GRANT)
            assert grant.principal == LABEL_PRINCIPAL and grant.permissions == frozenset({Permission.PROPOSE})
            assert hub.labelling_config["attempts"] == 1
    with Device(folder, runtime=False) as device:
        hub = configure(device.api, config, transport=ScriptedTransport([]))
        assert hub.runner.open(chat).pending.labels[-1].state == "proposed"
        device.api.authority.revoke_grant(PROPOSAL_GRANT)
    with Device(folder, runtime=False) as device:
        with pytest.raises(CampaignConfigError):
            configure(device.api, config, transport=ScriptedTransport([]))


def test_stage_and_agenda_are_consumed_by_ordinary_chat_and_hub(tmp_path):
    folder, chat, _ = card_library(tmp_path)
    with Device(folder, [say("Mara waits by the hatch.")]) as device:
        absent = attach_tamsin(device.api.store, device.api.owner_handle, chat)
        original = device.api.runner
        original.seat(chat, OWNER)
        scene = original.open(chat)
        mara = next(actor for actor in scene.cast if actor not in (scene.persona, absent))
        config = parse_campaign_config({"version": 1,
            "stages": [{"chat": str(chat), "as_of": 0, "areas": [{"area_id": "room", "label": "Cargo room"}],
                        "actors": [str(actor) for actor in scene.cast],
                        "placements": [{"who": str(actor), "area": "room"} for actor in scene.cast if actor != absent],
                        "absences": [{"actor": str(absent)}]}],
            "agendas": [{"actor": str(mara), "goals": [{"goal_id": "safe", "aim": "Stay safe",
                         "priority": 1, "approaches": [{"approach_id": "wait", "action": "Wait by the hatch"}]}]}]})
        api = TextApi(device.api.store, device.api.authority, owner=OWNER, runtime=device.api.runtime,
                      policy=original.policy, voices=original.authored, stages=config.stages, agendas=config.agendas)
        hub = configure(api, config)
        shown = hub.call("hub", {"chat": str(chat), "audience": "director"})
        assert shown["goals"][0]["goals"][0]["aim"] == "Stay safe"
        assert shown["stage"]["areas"][0]["label"] == "Cargo room"
        assert "goals" not in hub.call("hub", {"chat": str(chat)})
        api.call("post", {"chat": str(chat), "text": "Hello."})
        api.call("generate", {"chat": str(chat)})
        record = api.runner.open(chat).pending
        assert [choice.actor for choice in record.intentions] == [mara]
        assert record.intentions[0].approach_id == "wait"
        assert len(device.prompts()) == 1


def test_missing_label_credential_refuses_before_issuing_library_grant(tmp_path):
    folder, _chat, _ = card_library(tmp_path)
    config = parse_campaign_config({"version": 1, "labelling": {**MODEL, "key_env": "SYNTHETIC_LABEL_KEY"}})
    with Device(folder, runtime=False) as device:
        before = device.api.authority.active_grants()
        with pytest.raises(CampaignConfigError):
            configure(device.api, config, environ={})
        assert device.api.authority.active_grants() == before


def test_cli_refuses_missing_label_key_before_first_run_server(tmp_path, capsys):
    path = tmp_path / "campaign.json"
    path.write_text(json.dumps({"version": 1, "labelling": {**MODEL, "key_env": "ABSENT_SYNTHETIC_LABEL_KEY"}}))
    home = tmp_path / "new-library"
    assert main(["serve", str(home), "--setup", "--campaign-config", str(path)]) == 2
    assert not home.exists()
    assert "credential" in capsys.readouterr().err


def test_label_retry_has_its_own_approved_attempt_and_recorded_route(tmp_path):
    folder, chat, _ = card_library(tmp_path)
    responses = iter([lambda handler: handler.respond(503, b"{}"),
                      stream(sse_lines(deltas=('{"factors":[],"stakes":[]}',),
                                       usage={"prompt_tokens": 15, "completion_tokens": 4}))])
    with sse_server(lambda handler: next(responses)(handler)) as server, Device(folder, runtime=False) as device:
        config = parse_campaign_config({"version": 1, "labelling": {
            **MODEL, "base_url": base_url(server), "attempts": 2, "fallbacks": ["label-fallback"]}})
        hub = configure(device.api, config)
        hub.runner.seat(chat, OWNER)
        hub.runner.post(chat, hub.handle, "*I open the hatch*")
        assert hub.call("label", {"chat": str(chat)})["done"]["label"]["state"] == "proposed"
        attempts = hub.runner.open(chat).pending.labels[-1].calls[0].attempts
        assert [(a.outcome, a.route.rsplit("/", 1)[1]) for a in attempts] == [
            ("failed", "label-model"), ("completed", "label-fallback")]
        assert attempts[1].usage.input_tokens == 15
        assert len(server.requests) == 2
