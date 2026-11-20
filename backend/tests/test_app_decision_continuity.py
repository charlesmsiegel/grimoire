"""112 decisions and047 extraction share real app wiring without stale or substituted facts."""
import json

import pytest
from provider_servers import base_url, sse_lines, sse_server, stream
from scene_fixtures import MARA
from textapi_fixtures import Device, V1_CHAT, desktop_library
from grimoire.app.adapters.providers import configure
from grimoire.app.appapi import AppApi
from grimoire.app.config import config_json, parse_model_config, policy_for
from grimoire.core.inference import InferenceStage
from grimoire.play import continuity
from grimoire.play.stages import StageBook
from grimoire.staging import Area, Placement, Stage


@pytest.mark.parametrize("change_facts", [False, True])
def test_actual_app_reviews_selected_choice_and_retains_predecision_freshness(tmp_path, monkeypatch, change_facts):
    folder, _ = desktop_library(tmp_path / "fixture")
    chat = str(V1_CHAT)
    captured = []
    real_referee = continuity.referee
    def observe(*args, **kwargs):
        captured.append(kwargs["facts"])
        return real_referee(*args, **kwargs)
    monkeypatch.setattr(continuity, "referee", observe)
    with Device(folder, runtime=False) as device:
        runner = device.api.runner
        app = AppApi(device.api)
        app.call("continue", {"chat": chat})
        scene = runner.open(V1_CHAT)
        stage = Stage(V1_CHAT.campaign_id, V1_CHAT.chat_id, 0, (Area("room"),), (), scene.cast,
                      tuple(Placement(actor, area="room") for actor in scene.cast))
        runner.stages = StageBook()
        runner.stages.put(V1_CHAT, stage)
        decision_count = 0
        def respond(handler):
            nonlocal decision_count
            request = json.loads(handler.server.requests[-1][2])
            text = request["messages"][-1]["content"]
            if "Answer with the number of one choice" in text:
                decision_count += 1
                answer = "2"
                if change_facts:
                    runner.stages.put(V1_CHAT, stage.at(1))
            else:
                try:
                    source = json.loads(text)
                except ValueError:
                    source = {}
                if "lines" in source:
                    answer = json.dumps({"lines": [
                        {"line": line["line"], "sha256": line["sha256"], "complete": True, "continuity": [],
                         "authorship": [{"id": "weather", "domain": "world_fact", "subject": None,
                            "voiced_by": None, "span": [0, len(line["text"])], "quote": line["text"]}]}
                        for line in source["lines"]]})
                else:
                    assert "Check the door" in json.dumps(request)
                    answer = "The rain settles over the harbor."
            stream(sse_lines(deltas=(answer,), usage={"prompt_tokens": 80, "completion_tokens": 12}))(handler)
        with sse_server(respond) as server:
            configure(device.api, tmp_path / "model.json", environ={})
            saved = app.call("providers.save_models", {"expected": None, "settings": {
                "provider_id": "local", "base_url": base_url(server), "model": "synthetic", "decision": 1, "critique": 1}})
            saved = app.call("providers.save_agenda", {"chat": chat, "actor": str(MARA), "expected": saved["etag"],
                "aim": "Keep watch", "actions": ["Wait quietly", "Check the door"]})
            assert saved["settings"]["critique"] == saved["settings"]["decision"] == 1
            config = parse_model_config({k: v for k, v in saved["settings"].items() if k != "key_set"})
            assert parse_model_config(config_json(config)) == config
            assert policy_for(config).cap_for(InferenceStage.CRITIQUE) == 1
            assert policy_for(config).cap_for(InferenceStage.DECISION) == 1
            app.call("post", {"chat": chat, "text": "What now?"})
            before = continuity.current_fingerprint(runner, V1_CHAT)
            app.call("generate", {"chat": chat, "speakers": [str(MARA)]})
            take = runner.open(V1_CHAT).pending.takes[-1]
            assert captured and captured[0].intentions[0].approach.approach_id == "action-2"
            assert captured[0].stage == stage
            assert take.snapshot_fingerprint == before
            assert [call.stage.value for call in take.calls] == ["decision", "npc_generation", "critique"]
            assert all(len(call.attempts) == 1 for call in take.calls)
            assert take.lines[0].review == ("stale" if change_facts else "reviewed")
            usage = app.call("providers.usage", {"chat": chat})
            assert usage["total"]["attempts"] == 3
            traces = app.call("providers.diagnostics")["traces"]
            trace = next(t for t in reversed(traces) if t["operation"] == "generate")
            assert {call["stage"] for call in trace["calls"]} == {"decision", "npc_generation", "critique"}
            if not change_facts:
                app.call("generate", {"chat": chat, "speakers": [str(MARA)]})
                assert decision_count == 1 and len(server.requests) == 5
                assert captured[-1].intentions[0].approach.approach_id == "action-2"
                assert runner.open(V1_CHAT).pending.takes[-1].lines[0].review == "reviewed"
