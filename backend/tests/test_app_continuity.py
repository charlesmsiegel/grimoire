"""047 production settings and API wiring; synthetic libraries and fake provider only."""

import json

import pytest

from grimoire.app.config import config_json, parse_model_config, policy_for
from grimoire.app import ConfigError
from grimoire.core.inference import InferenceStage

BASE = {"provider_id": "local", "base_url": "http://127.0.0.1:11434/v1", "model": "synthetic"}


def test_critique_is_explicit_bounded_and_preserved_in_config_roundtrip():
    default = parse_model_config(BASE)
    assert not policy_for(default).enabled(InferenceStage.CRITIQUE)
    enabled = parse_model_config({**BASE, "critique": 1})
    assert policy_for(enabled).cap_for(InferenceStage.CRITIQUE) == 1
    assert parse_model_config(json.loads(json.dumps(config_json(enabled)))) == enabled
    disabled = parse_model_config({**config_json(enabled), "critique": 0})
    assert not policy_for(disabled).enabled(InferenceStage.CRITIQUE)


@pytest.mark.parametrize("value", [True, False, -1, 2, 1.0, "1", None])
def test_critique_cannot_be_an_unbounded_or_coerced_call_count(value):
    with pytest.raises(ConfigError):
        parse_model_config({**BASE, "critique": value})


def test_configured_generation_has_a_separate_untrusted_extractor_only_when_enabled():
    from grimoire.app.config import build_access, runtime_for
    from grimoire.core.inference import OptionalRequest, TurnUsageLedger, plan_turn
    from grimoire.providers.meter import TurnMeter
    from grimoire.testing.providers import Reply, ScriptedTransport

    transport = ScriptedTransport([Reply(deltas=('{"lines": []}',))])
    config = parse_model_config({**BASE, "critique": 1, "max_output_tokens": 128})
    access = build_access(config, "human:owner", transport=transport)
    narrator = runtime_for(access, config)
    extractor = narrator.continuity_extractor
    assert extractor is not None and extractor is not narrator
    plan = plan_turn("synthetic", "t1", present_actor_ids=(), optional_requests=[
        OptionalRequest(InferenceStage.CRITIQUE, None, "extract narration claims")], policy=policy_for(config))
    meter = TurnMeter(TurnUsageLedger(plan))
    reply = extractor.extract(meter, plan.calls[0], (("user", "Synthetic prose"),))
    assert reply.status == "decoded"
    assert len(meter.ledger.attempts) == 1
    assert len(transport.sends) == 1
    assert json.loads(transport.sends[0].body)["max_tokens"] == 128
    off = parse_model_config(BASE)
    assert runtime_for(build_access(off, "human:owner"), off).continuity_extractor is None


def test_configured_extractor_proposal_handle_is_untrusted_and_revocation_survives_restart(tmp_path):
    from grimoire.app.config import build_runtime
    from grimoire.textapi import TextApi
    from grimoire.textapi.continuity import PROPOSAL_GRANT
    from grimoire.security import Permission
    from grimoire.ledgers import DurableAuthority
    from grimoire.store import LibraryStore

    folder = tmp_path / "v2"
    with LibraryStore.create(folder) as store:
        authority = DurableAuthority.open(store, ["human:owner"])
        runtime, policy = build_runtime(parse_model_config({**BASE, "critique": 1}), "human:owner")
        api = TextApi(store, authority, owner="human:owner", runtime=runtime, policy=policy)
        handle = api.runner.extract_handle
        assert authority.issued(handle) and handle.trusted is False
        assert handle.authorize(Permission.PROPOSE, "campaigns/synthetic/turns/t1", b"{}").allowed
        assert not handle.authorize(Permission.APPLY, "campaigns/synthetic/turns/t1", b"{}").allowed
        authority.revoke_grant(PROPOSAL_GRANT)
        authority.close()
    with LibraryStore.open(folder) as store:
        authority = DurableAuthority.open(store, ["human:owner"])
        api = TextApi(store, authority, owner="human:owner", runtime=runtime, policy=policy)
        assert not api.runner.extract_handle.authorize(
            Permission.PROPOSE, "campaigns/synthetic/turns/t1", b"{}").allowed
        authority.close()


def test_settings_install_clear_and_reload_independent_extractor(tmp_path):
    from grimoire.app.inference import InferenceApi, SettingsFile
    from textapi_fixtures import Device, desktop_library

    folder, _ = desktop_library(tmp_path / "fixture")
    path = tmp_path / "model.json"
    with Device(folder, runtime=False) as device:
        inference = InferenceApi(device.api, SettingsFile(path), owner=device.api.owner, environ={})
        before = inference.call("models")
        assert not device.api.runner.extractor
        saved = inference.call("save_models", {"settings": {**BASE, "critique": 1}, "expected": before["etag"]})
        assert device.api.runner.extractor is not None
        stage = next(s for s in saved["stages"] if s["stage"] == "critique")
        assert stage["available"] and stage["enabled"] and stage["cap"] == 1
        off = inference.call("save_models", {"settings": {**BASE, "critique": 0}, "expected": saved["etag"]})
        assert device.api.runner.extractor is None and device.api.runner.extract_handle is None
        inference.call("save_models", {"settings": {**BASE, "critique": 1}, "expected": off["etag"]})
    with Device(folder, runtime=False) as device:
        inference = InferenceApi(device.api, SettingsFile(path), owner=device.api.owner, environ={})
        assert device.api.runner.extractor is not None
        path.write_text('{"invalid": true}')
        assert not inference.call("models")["generation"]["available"]
        assert device.api.runner.extractor is None and device.api.runner.extract_handle is None


def test_real_app_generates_then_extracts_with_usage_and_retained_review_on_reopen(tmp_path):
    from grimoire.app.inference import InferenceApi, SettingsFile
    from provider_servers import base_url, sse_lines, sse_server, stream
    from scene_fixtures import MARA
    from textapi_fixtures import Device, desktop_library, V1_CHAT

    folder, _ = desktop_library(tmp_path / "fixture")
    path = tmp_path / "model.json"
    prose = "The rain settles over the harbor."
    usage = {"prompt_tokens": 80, "completion_tokens": 12}

    def respond(handler):
        request = json.loads(handler.server.requests[-1][2])
        if len(handler.server.requests) == 1:
            answer = prose
        else:
            source = json.loads(request["messages"][-1]["content"])
            answer = json.dumps({"lines": [
                {"line": line["line"], "sha256": line["sha256"], "complete": True, "continuity": [],
                 "authorship": [{"id": "weather", "domain": "world_fact", "subject": None,
                                 "voiced_by": None, "span": [0, len(line["text"])], "quote": line["text"]}]}
                for line in source["lines"]]})
        stream(sse_lines(deltas=(answer,), usage=usage))(handler)

    chat = str(V1_CHAT)
    with sse_server(respond) as server, Device(folder, runtime=False) as device:
        inference = InferenceApi(device.api, SettingsFile(path), owner=device.api.owner, environ={})
        inference.call("save_models", {"settings": {**BASE, "base_url": base_url(server), "critique": 1},
                                       "expected": None})
        device("continue", chat=chat)
        device("post", chat=chat, text="How is the weather?")
        scene = device("generate", chat=chat, speakers=[str(MARA)])
        turn = scene["state"]["open_turn"]
        pending = device.api.runner.open(V1_CHAT).pending
        take = pending.takes[-1]
        assert len(server.requests) == 2
        assert take.lines[0].text == prose and take.lines[0].review != "unlabelled"
        assert take.review_token
        report = device("continuity_review", chat=chat, turn=turn, take=take.take)
        assert report
        billed = inference.call("usage", {"chat": chat})
        calls = [call for entry in billed["turns"] for call in entry["calls"] if "stage" in call]
        assert [call["stage"] for call in calls] == ["npc_generation", "critique"]
        assert billed["total"]["attempts"] == 2
        assert billed["total"]["sums"]["input_tokens"] == 160
    with Device(folder, runtime=False) as device:
        reopened = device.api.runner.open(V1_CHAT).pending.takes[-1]
        assert reopened == take
        assert device("continuity_review", chat=chat, turn=turn, take=take.take) == report


def test_app_failed_extraction_requires_exact_ack_and_disabling_costs_no_extra_call(tmp_path):
    from grimoire.app.inference import InferenceApi, SettingsFile
    from grimoire.textapi import ApiRefused
    from provider_servers import base_url, sse_lines, sse_server, stream
    from scene_fixtures import MARA
    from textapi_fixtures import Device, desktop_library, V1_CHAT

    folder, _ = desktop_library(tmp_path / "fixture")
    answers = iter(["The sky is gray.", "not json", "The rain ends."])
    def respond(handler):
        stream(sse_lines(deltas=(next(answers),), usage={"prompt_tokens": 10, "completion_tokens": 4}))(handler)

    chat = str(V1_CHAT)
    with sse_server(respond) as server, Device(folder, runtime=False) as device:
        inference = InferenceApi(device.api, SettingsFile(tmp_path / "model.json"), owner=device.api.owner, environ={})
        saved = inference.call("save_models", {"settings": {**BASE, "base_url": base_url(server), "critique": 1},
                                               "expected": None})
        device("continue", chat=chat)
        device("post", chat=chat, text="Weather?")
        device("generate", chat=chat, speakers=[str(MARA)])
        pending = device.api.runner.open(V1_CHAT).pending
        take = pending.takes[-1]
        assert take.lines[0].review == "failed"
        with pytest.raises(ApiRefused) as held:
            device("keep", chat=chat, turn=pending.turn_id, take=take.take)
        assert held.value.reason == "review_required"
        with pytest.raises(ApiRefused):
            device("keep", chat=chat, turn=pending.turn_id, take=take.take, acknowledge_review="0" * 64)
        device("keep", chat=chat, turn=pending.turn_id, take=take.take, acknowledge_review=take.review_token)
        assert device.api.runner.open(V1_CHAT).last.kept.lines[0].review == "exception"
        inference.call("save_models", {"settings": {**BASE, "base_url": base_url(server), "critique": 0},
                                       "expected": saved["etag"]})
        device("post", chat=chat, text="And now?")
        device("generate", chat=chat, speakers=[str(MARA)])
        assert len(server.requests) == 3
        pending = device.api.runner.open(V1_CHAT).pending
        assert pending.takes[-1].lines[0].review == "unlabelled"
        assert len(pending.takes[-1].calls) == 1


def test_campaign_stage_and_agenda_survive_optional_extraction_settings(tmp_path):
    from grimoire.app.campaign_config import parse_campaign_config
    from grimoire.app.campaign_services import configure
    from grimoire.app.config import build_runtime
    from grimoire.app.inference import InferenceApi, SettingsFile
    from grimoire.testing.providers import Reply, ScriptedTransport
    from grimoire.textapi import TextApi
    from scene_fixtures import card_library
    from textapi_fixtures import Device

    folder, chat, _ = card_library(tmp_path)
    owner = "human:owner"
    with Device(folder, runtime=False) as device:
        owner = device.api.owner
        device.api.runner.seat(chat, owner)
        scene = device.api.runner.open(chat)
        npc = next(actor for actor in scene.cast if actor != scene.persona)
        campaign = parse_campaign_config({"version": 1,
            "stages": [{"chat": str(chat), "as_of": 0, "areas": [{"area_id": "room", "label": "Room"}],
                        "actors": [str(actor) for actor in scene.cast],
                        "placements": [{"who": str(actor), "area": "room"} for actor in scene.cast]}],
            "agendas": [{"actor": str(npc), "goals": [{"goal_id": "wait", "aim": "Wait",
                         "priority": 1, "approaches": [{"approach_id": "wait", "action": "Wait"}]}]}]})
        transport = ScriptedTransport([Reply(deltas=("The rain falls.",)), Reply(deltas=('{"lines":[]}',))])
        runtime, policy = build_runtime(parse_model_config({**BASE, "critique": 1}), owner, transport=transport)
        api = TextApi(device.api.store, device.api.authority, owner=owner, runtime=runtime, policy=policy,
                      stages=campaign.stages, agendas=campaign.agendas)
        hub = configure(api, campaign)
        assert hub.call("hub", {"chat": str(chat), "audience": "director"})["stage"]["areas"][0]["label"] == "Room"
        api.runner.post(chat, api.owner_handle, "Hello.")
        take = api.runner.generate(chat, api.owner_handle)
        pending = api.runner.open(chat).pending
        assert pending.intentions[0].approach_id == "wait"
        assert take.lines[0].review == "unchecked"
        assert [call.stage.value for call in take.calls] == ["npc_generation", "critique"]
        assert len(transport.sends) == 2
        inference = InferenceApi(api, SettingsFile(tmp_path / "models.json"), owner=owner, environ={})
        inference.call("save_models", {"settings": BASE, "expected": None})
        assert api.runner.extractor is None
        assert api.runner.open(chat).stage is not None
        assert hub.runner.stages is api.runner.stages
        assert api.runner._agendas
