"""Production app service construction, persisted settings and metered 045 calls."""
import json

from provider_servers import base_url, sse_lines, sse_server, stream
from scene_fixtures import MARA, SERAPHINE
from textapi_fixtures import Device, V1_CHAT, desktop_library, keep

from grimoire.app.adapters.providers import configure
from grimoire.app.appapi import AppApi

CHAT = str(V1_CHAT)


def model_reply(handler):
    request = json.loads(handler.server.requests[-1][2])
    decision = "Answer with the number of one choice" in request["messages"][-1]["content"]
    stream(sse_lines(deltas=("2" if decision else "The tide keeps its own ledger.",),
                     usage={"prompt_tokens": 90, "completion_tokens": 8}))(handler)


def decisions(server):
    return sum("Answer with the number of one choice" in json.loads(body)["messages"][-1]["content"]
               for _, _, body in server.requests)


def test_actual_app_decides_once_reopens_replays_and_disables_next_turn(tmp_path):
    folder, _ = desktop_library(tmp_path / "fixture")
    path = tmp_path / "model.json"
    with sse_server(model_reply) as server:
        with Device(folder, runtime=False) as device:
            configure(device.api, path, environ={})
            app = AppApi(device.api)
            app.call("continue", {"chat": CHAT})
            saved = app.call("providers.save_models", {"expected": None, "settings": {
                "provider_id": "local", "base_url": base_url(server), "model": "synthetic", "decision": 1}})
            app.call("providers.save_agenda", {"chat": CHAT, "actor": str(MARA), "expected": saved["etag"],
                "aim": "Keep watch", "actions": ["Wait quietly", "Check the door"]})
            app.call("post", {"chat": CHAT, "text": "Is anyone keeping watch?"})
            app.call("generate", {"chat": CHAT, "speakers": [str(MARA), str(SERAPHINE)]})
            usage = app.call("providers.usage", {"chat": CHAT})
            calls = usage["turns"][-1]["calls"]
            assert [c["stage"] for c in calls] == ["decision", "npc_generation", "npc_generation"]
            assert all(c["status"] == "accepted" for c in calls)
            assert calls[0]["attempts"][0]["measurement"] == "provider_reported"
            assert calls[0]["attempts"][0]["cost_state"] == "unpriced"
            assert decisions(server) == 1 and len(server.requests) == 3
            assert "Check the door" in app.call("providers.context", {"chat": CHAT, "actor": str(MARA)})["context"]["system"]
        with Device(folder, runtime=False) as device:
            configure(device.api, path, environ={})
            app = AppApi(device.api)
            app.call("generate", {"chat": CHAT, "speakers": [str(MARA), str(SERAPHINE)]})
            assert decisions(server) == 1 and len(server.requests) == 5
            keep(device, CHAT, take=2)
            models = app.call("providers.models")
            app.call("providers.save_models", {"expected": models["etag"],
                "settings": {**models["settings"], "decision": 0}})
            app.call("post", {"chat": CHAT, "text": '"Keep an eye on the door."'})
            app.call("generate", {"chat": CHAT, "speakers": [str(MARA), str(SERAPHINE)]})
            assert decisions(server) == 1 and len(server.requests) == 7
            current = app.call("providers.context", {"chat": CHAT, "actor": str(MARA)})
            assert "Wait quietly" in current["context"]["system"]
            record = device.api.runner.open(V1_CHAT).last
            assert device.api.decisions.get(record.ref, MARA).record.method == "policy"
