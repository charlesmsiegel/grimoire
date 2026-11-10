"""112's inference settings, usage and diagnostics ops over the app's text API.

Synthetic libraries from ``textapi_fixtures``; the model is a loopback server from ``provider_servers`` (never
billed, never a real provider), reached through 013's real ``HttpTransport``.
"""

from __future__ import annotations

import json

import pytest
from provider_servers import base_url, sse_lines, sse_server, stream
from scene_fixtures import MARA, SERAPHINE
from store_helpers import OWNER
from textapi_fixtures import V1_CHAT, Device, desktop_library, keep

from grimoire.app import ConfigError, parse_model_config
from grimoire.chats import Launch
from grimoire.app.config import config_json
from grimoire.app.inference import InferenceApi, SettingsFile
from grimoire.textapi import ApiRefused

USAGE = {"prompt_tokens": 120, "completion_tokens": 9, "prompt_tokens_details": {"cached_tokens": 20}}
REPLY = "The tide keeps its own ledger."
CHAT = str(V1_CHAT)


@pytest.fixture
def desk(tmp_path):
    folder, _card_chat = desktop_library(tmp_path / "desk")
    return folder


@pytest.fixture
def model():
    with sse_server(stream(sse_lines(deltas=(REPLY,), usage=USAGE))) as server:
        yield server


def _settings(server, **extra) -> dict:
    draft = {"provider_id": "local", "base_url": base_url(server), "model": "model-a", "director": 1}
    draft.update(extra)
    return draft


def _table(device: Device, path, environ=None) -> InferenceApi:
    return InferenceApi(device.api, SettingsFile(path), owner=OWNER, environ=environ or {})


def _calls(usage: dict, *, stage: str | None = None) -> list[dict]:
    return [call for turn in usage["turns"] for call in turn["calls"]
            if "stage" in call and (stage is None or call["stage"] == stage)]


def _turn(device: Device, text: str, **generate) -> dict:
    device("post", chat=CHAT, text=text)
    return device("generate", chat=CHAT, **generate)


def _premise_chat(device: Device) -> str:
    """A world-less chat with a premise and no NPC: only the director can answer it."""
    chat = str(device.api.chats.start_chat(Launch("premise", premise="A night on the quay.")).commit().chat)
    device("continue", chat=chat)
    return chat


# -- the proving scenario --------------------------------------------------------------------------------

def test_a_local_model_set_up_in_the_app_bills_each_npc_call_and_an_optional_call_only_while_enabled(
        desk, model, tmp_path):
    path = tmp_path / "config" / "model.json"
    with Device(desk, runtime=False) as device:
        inference = _table(device, path)
        shown = inference.call("models")
        assert shown["settings"] is None and shown["etag"] is None
        assert shown["generation"] == {"available": False, "routes": [], "local": None,
                                       "problem": {"code": "not_configured",
                                                   "message": "no model is configured yet: generation is off"}}
        # a missing provider stops generation, never offline play: posting still works
        device("continue", chat=CHAT)
        device("post", chat=CHAT, text="Is anyone keeping the ledger?")
        with pytest.raises(ApiRefused) as refused:
            device("generate", chat=CHAT)
        assert refused.value.reason == "no_runtime"

        saved = inference.call("save_models", {"settings": _settings(model), "expected": None})
        assert saved["generation"]["available"] is True and saved["generation"]["local"] is True
        assert saved["etag"] is not None and json.loads(path.read_text()) == config_json(
            parse_model_config(_settings(model)))
        assert device("library")["generation"] is True

        device("generate", chat=CHAT, speakers=[str(MARA), str(SERAPHINE)])
        usage = inference.call("usage", {"chat": CHAT})
        npc = _calls(usage, stage="npc_generation")
        assert [(call["name"], call["kind"], call["status"]) for call in npc] == [
            ("Mara", "baseline", "accepted"), ("Seraphine", "baseline", "accepted")]
        for call in npc:  # each of the two separate calls is its own usage entry
            (attempt,) = call["attempts"]
            assert attempt["measurement"] == "provider_reported" and attempt["outcome"] == "completed"
            assert attempt["route"] == f"local@127.0.0.1:{model.server_address[1]}/model-a"
            assert attempt["tokens"] == {"input_tokens": 120, "output_tokens": 9, "cache_read_tokens": 20,
                                         "cache_write_tokens": None, "reasoning_tokens": None}
            assert (attempt["cost"], attempt["cost_state"]) == (None, "unpriced")  # unknown, never free
        assert usage["total"]["sums"]["input_tokens"] == 240 and usage["total"]["attempts"] == 2
        assert usage["total"]["unreported"]["cache_write_tokens"] == 2
        assert usage["total"]["lower_bound"]["cache_write_tokens"] is True
        assert (usage["total"]["cost"], usage["total"]["cost_state"]) == (None, "unpriced")
        assert len(model.requests) == 2
        keep(device, CHAT)

        # the optional director reply is billed as an extra while it is enabled ...
        quay = _premise_chat(device)
        device("generate", chat=quay, director=True)  # no persona here: the table asks the director to go on
        (director,) = _calls(inference.call("usage", {"chat": quay}), stage="director_generation")
        assert (director["kind"], director["actor"], director["status"]) == ("optional", None, "accepted")
        keep(device, quay)
        sent = len(model.requests)

        # ... and once the owner switches it off, the next turn plans and bills none
        current = inference.call("models")
        off = inference.call("save_models", {"settings": {**current["settings"], "director": 0},
                                             "expected": current["etag"]})
        assert {stage["stage"]: stage["enabled"] for stage in off["stages"]}["director_generation"] is False
        with pytest.raises(ApiRefused) as declined:
            device("generate", chat=quay, director=True)
        assert declined.value.reason == "director_declined"
        assert len(model.requests) == sent
        assert len(_calls(inference.call("usage", {"chat": quay}), stage="director_generation")) == 1

    # a restart plays by the saved settings
    with Device(desk, runtime=False) as device:
        inference = _table(device, path)
        assert inference.call("models")["generation"]["available"] is True
        assert inference.call("models")["settings"]["director"] == 0
        assert device("library")["generation"] is True


def test_a_fallback_model_is_tried_only_by_a_retry_and_both_attempts_are_listed(desk, tmp_path):
    answers = iter([lambda handler: handler.respond(503, b"{}"), stream(sse_lines(deltas=(REPLY,), usage=USAGE))])
    with sse_server(lambda handler: next(answers)(handler)) as server, Device(desk, runtime=False) as device:
        inference = _table(device, tmp_path / "model.json")
        inference.call("save_models", {"settings": _settings(server, fallbacks=["model-b"], attempts=2),
                                       "expected": None})
        assert inference.call("models")["generation"]["routes"][1].endswith("/model-b")
        device("continue", chat=CHAT)
        _turn(device, "Who answers?", speakers=[str(MARA)])
        (call,) = _calls(inference.call("usage", {"chat": CHAT}))
        assert [(a["outcome"], a["route"].rsplit("/", 1)[1]) for a in call["attempts"]] == [
            ("failed", "model-a"), ("completed", "model-b")]
        assert call["status"] == "accepted"


def test_counts_a_provider_does_not_report_are_unreported_never_zero(desk, tmp_path):
    with sse_server(stream(sse_lines(deltas=(REPLY,)))) as server, Device(desk, runtime=False) as device:
        inference = _table(device, tmp_path / "model.json")
        inference.call("save_models", {"settings": _settings(server), "expected": None})
        device("continue", chat=CHAT)
        _turn(device, "Anything?", speakers=[str(MARA)])
        usage = inference.call("usage", {"chat": CHAT})
        (call,) = _calls(usage)
        assert call["attempts"][0]["tokens"]["input_tokens"] is None
        assert usage["total"]["sums"]["input_tokens"] == 0 and usage["total"]["unreported"]["input_tokens"] == 1
        assert usage["total"]["lower_bound"]["input_tokens"] is True


def test_a_call_with_no_attempt_booked_is_counted_unmetered_in_its_stage_too(desk, tmp_path):
    with sse_server(lambda handler: handler.respond(400, b"{}")) as server, Device(desk, runtime=False) as device:
        inference = _table(device, tmp_path / "model.json")
        inference.call("save_models", {"settings": _settings(server), "expected": None})
        device("continue", chat=CHAT)
        device("post", chat=CHAT, text="Who answers?")
        device("generate", chat=CHAT, speakers=[str(MARA), str(SERAPHINE)])  # halted: the first call failed
        usage = inference.call("usage", {"chat": CHAT})
        calls = _calls(usage, stage="npc_generation")
        missing = sum(1 for call in calls if not call["attempts"])
        assert missing >= 1
        (stage,) = [entry for entry in usage["by_stage"] if entry["stage"] == "npc_generation"]
        assert stage["unmetered_calls"] == missing == usage["total"]["unmetered_calls"]
        assert stage["not_issued_calls"] == sum(1 for call in calls if call["status"] == "not_issued") >= 1


# -- privacy -----------------------------------------------------------------------------------------------

def test_usage_and_diagnostics_hold_no_text_a_call_sent_or_received(desk, model, tmp_path):
    with Device(desk, runtime=False) as device:
        inference = _table(device, tmp_path / "model.json")
        inference.call("save_models", {"settings": _settings(model), "expected": None})
        device("continue", chat=CHAT)
        _turn(device, "A curious question about the ledger", speakers=[str(MARA), str(SERAPHINE)])
        usage = json.dumps(inference.call("usage", {"chat": CHAT}))
        diagnostics = inference.call("diagnostics")
        dumped = json.dumps(diagnostics)
        prompts = [body.decode("utf-8") for _path, _headers, body in model.requests]
        for text in ("curious question", REPLY, "tide"):
            assert text not in usage and text not in dumped
        assert all("curious question" in prompt for prompt in prompts)  # it was sent: just never shown here
        generated = [trace for trace in diagnostics["traces"] if trace["operation"] == "generate"]
        assert generated and {role["stage"] for role in generated[0]["calls"]} == {"npc_generation"}
        assert {role["actor"] for role in generated[0]["calls"]} == {"npc-1", "npc-2"}  # never an actor id


def test_a_key_variable_that_is_not_set_saves_but_generates_nothing_and_never_shows_a_value(desk, model, tmp_path):
    path = tmp_path / "model.json"
    with Device(desk, runtime=False) as device:
        inference = _table(device, path, environ={})
        draft = _settings(model, key_env="LOCAL_API_KEY")
        checked = inference.call("check_models", {"settings": draft})
        assert checked["ok"] is True and checked["key_set"] is False and checked["sent"] is False
        saved = inference.call("save_models", {"settings": draft, "expected": None})
        assert saved["generation"]["available"] is False
        assert saved["generation"]["problem"]["code"] == "key_missing"
        assert saved["settings"]["key_env"] == "LOCAL_API_KEY" and saved["settings"]["key_set"] is False
    with Device(desk, runtime=False) as device:
        inference = _table(device, path, environ={"LOCAL_API_KEY": "sk-synthetic-value"})
        shown = json.dumps(inference.call("models"))
        assert "sk-synthetic-value" not in shown and inference.call("models")["settings"]["key_set"] is True
    assert "sk-synthetic-value" not in path.read_text()


def test_the_app_names_only_a_provider_key_variable_unless_the_file_already_named_another(desk, model, tmp_path):
    path = tmp_path / "model.json"
    with Device(desk, runtime=False) as device:
        inference = _table(device, path)
        for name in ("AWS_SECRET_ACCESS_KEY", "HOME", "sk-pasted-key-by-mistake", "lower_api_key"):
            checked = inference.call("check_models", {"settings": _settings(model, key_env=name)})
            assert checked["ok"] is False and checked["problems"][0]["field"] == "key_env"
            assert name not in json.dumps(checked)
            with pytest.raises(ApiRefused) as refused:
                inference.call("save_models", {"settings": _settings(model, key_env=name), "expected": None})
            assert refused.value.reason == "invalid_settings" and name not in str(refused.value)
        assert not path.exists()
    # a file the owner wrote by hand keeps its own name, saved again from the app
    path.write_text(json.dumps(_settings(model, key_env="MY_LOCAL_TOKEN")))
    with Device(desk, runtime=False) as device:
        inference = _table(device, path, environ={"MY_LOCAL_TOKEN": "t"})
        current = inference.call("models")
        saved = inference.call("save_models", {"settings": {**current["settings"], "max_output_tokens": 300},
                                               "expected": current["etag"]})
        assert saved["settings"]["key_env"] == "MY_LOCAL_TOKEN" and saved["generation"]["available"] is True
        # ...for the endpoint it was written for only: never sent anywhere the owner did not name by hand
        moved = {**saved["settings"], "base_url": "https://elsewhere.example/v1"}
        with pytest.raises(ApiRefused) as refused:
            inference.call("save_models", {"settings": moved, "expected": saved["etag"]})
        assert refused.value.reason == "invalid_settings" and "MY_LOCAL_TOKEN" not in str(refused.value)


# -- refusals ------------------------------------------------------------------------------------------------

def test_a_save_at_a_revision_that_moved_on_writes_nothing(desk, model, tmp_path):
    path = tmp_path / "model.json"
    with Device(desk, runtime=False) as device:
        inference = _table(device, path)
        first = inference.call("save_models", {"settings": _settings(model), "expected": None})
        with pytest.raises(ApiRefused) as refused:
            inference.call("save_models", {"settings": _settings(model, director=0), "expected": None})
        assert refused.value.reason == "changed"
        path.write_text(json.dumps(_settings(model, model="model-z")))  # edited by hand meanwhile
        with pytest.raises(ApiRefused) as refused:
            inference.call("save_models", {"settings": _settings(model, director=0), "expected": first["etag"]})
        assert refused.value.reason == "changed" and json.loads(path.read_text())["model"] == "model-z"
        # the page reads the file as it now is, and plays by it: a save from that read keeps the hand edit's revision
        current = inference.call("models")
        assert current["settings"]["model"] == "model-z" and current["generation"]["routes"][0].endswith("/model-z")
        assert device.api.runner.runtime is not None
        path.unlink()  # removed by hand: no model, and a save must create the file again
        gone = inference.call("models")
        assert gone["settings"] is None and gone["etag"] is None and gone["generation"]["problem"]["code"] == "not_configured"
        assert device.api.runner.runtime is None
        for expected in ("not-an-etag", 3):
            with pytest.raises(ApiRefused) as refused:
                inference.call("save_models", {"settings": _settings(model), "expected": expected})
            assert refused.value.reason == "invalid_field"
        with pytest.raises(ApiRefused):
            inference.call("save_models", {"settings": _settings(model)})


def test_without_a_settings_file_settings_are_read_only_and_play_goes_on(desk, model):
    with Device(desk, runtime=False) as device:
        inference = InferenceApi(device.api, SettingsFile(None), owner=OWNER, environ={})
        shown = inference.call("models")
        assert shown["file"] == {"configured": False, "exists": False}
        with pytest.raises(ApiRefused) as refused:
            inference.call("save_models", {"settings": _settings(model), "expected": None})
        assert refused.value.reason == "read_only"
        device("continue", chat=CHAT)
        device("post", chat=CHAT, text="Still here.")


@pytest.mark.parametrize("content", [b"not json", b"[]", json.dumps({"sk-pasted-as-a-key": 1}).encode(),
                                     json.dumps({"provider_id": "p", "base_url": "http://x.example/v1",
                                                 "model": "m"}).encode()])
def test_a_settings_file_that_cannot_be_used_leaves_the_app_without_a_model_and_is_not_echoed(desk, tmp_path,
                                                                                               content):
    path = tmp_path / "model.json"
    path.write_bytes(content)
    with Device(desk, runtime=False) as device:
        inference = _table(device, path)
        shown = inference.call("models")
        assert shown["generation"]["available"] is False
        assert shown["generation"]["problem"]["code"] == "settings_invalid"
        assert "sk-pasted" not in json.dumps(shown) and shown["etag"] is not None
        device("continue", chat=CHAT)  # offline play is untouched


def test_checking_a_draft_sends_nothing_and_says_what_a_save_would_play_by(desk, model, tmp_path):
    with Device(desk, runtime=False) as device:
        inference = _table(device, tmp_path / "model.json")
        checked = inference.call("check_models", {"settings": _settings(model, fallbacks=["model-b"])})
        assert checked["ok"] is True and checked["local"] is True and len(checked["routes"]) == 2
        hosted = inference.call("check_models", {"settings": {"provider_id": "hosted", "base_url":
                                                              "https://models.example/v1", "model": "m"}})
        assert hosted["ok"] is True and hosted["local"] is False
        bad = inference.call("check_models", {"settings": _settings(model, attempts=9)})
        assert bad["ok"] is False and bad["problems"][0]["field"] == "attempts"
        assert inference.call("check_models", {"settings": "nope"})["ok"] is False
        assert model.requests == [] and not (tmp_path / "model.json").exists()


def test_usage_refuses_what_is_not_a_chat(desk, tmp_path):
    with Device(desk, runtime=False) as device:
        inference = _table(device, tmp_path / "model.json")
        for args, reason in (({}, "invalid_field"), ({"chat": "nope"}, "invalid_field"),
                             ({"chat": CHAT, "limit": 0}, "invalid_field")):
            with pytest.raises(ApiRefused) as refused:
                inference.call("usage", args)
            assert refused.value.reason == reason
        with pytest.raises(ApiRefused) as refused:
            inference.call("nope")
        assert refused.value.reason == "unknown_op"


def test_stages_say_which_optional_calls_the_app_can_play_and_why_the_rest_are_unavailable(desk, model, tmp_path):
    with Device(desk, runtime=False) as device:
        inference = _table(device, tmp_path / "model.json")
        stages = {stage["stage"]: stage for stage in inference.call("models")["stages"]}
        assert stages["npc_generation"]["kind"] == "baseline"
        assert stages["director_generation"]["available"] is True
        unavailable = [name for name, stage in stages.items() if not stage["available"]]
        assert {"decision", "labelling", "planning", "critique"} <= set(unavailable)
        assert all(stages[name]["note"] and stages[name]["enabled"] is False for name in unavailable)
        features = {item["feature"] for item in inference.call("models")["unavailable"]}
        assert {"connection_test", "prices", "session_budget", "prompt_preview"} <= features


# -- the configuration -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("extra", [{"attempts": 0}, {"attempts": 4}, {"fallbacks": "model-b"},
                                   {"fallbacks": ["m"]}, {"fallbacks": ["b", "b"]}, {"fallbacks": ["has space"]},
                                   {"fallbacks": ["a", "b", "c", "d", "e"]}, {"max_output_tokens": 10**6}])
def test_fallbacks_and_attempts_are_bounded(extra):
    with pytest.raises(ConfigError):
        parse_model_config({"provider_id": "p", "base_url": "https://x.example/v1", "model": "m", **extra})


def test_a_configuration_written_out_reads_back_the_same():
    raw = {"provider_id": "p", "base_url": "https://x.example/v1", "model": "m", "key_env": "P_API_KEY",
           "fallbacks": ["n"], "attempts": 2, "director": 0, "max_output_tokens": 500}
    config = parse_model_config(raw)
    assert parse_model_config(config_json(config)) == config
    assert config.models == ("m", "n")
