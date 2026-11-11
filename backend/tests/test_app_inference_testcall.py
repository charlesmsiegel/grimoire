"""112's test call: one small request to the saved model, sent the way a turn's reply is, in no chat.

The model is a loopback server from ``provider_servers`` (synthetic, never billed), reached through 013's real
``HttpTransport``; the libraries are ``textapi_fixtures``'. The keys are made up for the test.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from provider_servers import RawServer, base_url, sse_lines, sse_server, stream
from store_helpers import OWNER
from textapi_fixtures import Device, desktop_library

from grimoire.app.inference import InferenceApi, SettingsFile
from grimoire.app.testcall import TEST_OUTPUT_TOKENS, TEST_PROMPT
from grimoire.providers.errors import TransportError
from grimoire.textapi import ApiRefused

USAGE = {"prompt_tokens": 14, "completion_tokens": 2, "prompt_tokens_details": {"cached_tokens": 0}}
KEY = "sk-synthetic-test-key-0123456789"


@pytest.fixture
def desk(tmp_path):
    folder, _card_chat = desktop_library(tmp_path / "desk")
    return folder


def _settings(url: str, **extra) -> dict:
    return {"provider_id": "local", "base_url": url, "model": "model-a", **extra}


def _table(device: Device, path, **options) -> InferenceApi:
    return InferenceApi(device.api, SettingsFile(path), owner=OWNER, environ=options.pop("environ", {}), **options)


def _saved(inference: InferenceApi, settings: dict) -> str:
    return inference.call("save_models", {"settings": settings, "expected": None})["etag"]


def _files(folder: Path) -> dict[str, bytes]:
    return {str(path.relative_to(folder)): path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file()}


class _NoTransport:
    """A transport that reaches nothing, and counts who tried."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def connect(self, url, timeouts, cancel):  # type: ignore[no-untyped-def]
        self.asked.append(url)
        raise TransportError("network", "connect")


def test_a_test_call_answers_with_the_model_its_time_and_its_tokens_and_touches_no_chat(desk, tmp_path):
    path = tmp_path / "model.json"
    with sse_server(stream(sse_lines(deltas=("ready",), usage=USAGE, model="served-model"))) as server, \
            Device(desk, runtime=False) as device:
        inference = _table(device, path)
        etag = _saved(inference, _settings(base_url(server), price_prompt="3", price_reply="15", attempts=2,
                                           fallbacks=["model-b"]))
        shown = inference.call("models")
        assert shown["test_call"] == {"available": True, "reason": None, "seconds": 30.0}
        assert "connection_test" not in {item["feature"] for item in shown["unavailable"]}
        assert server.requests == []  # nothing is sent until it is asked for
        library, settings = _files(desk), path.read_bytes()
        chats = device("chats")

        result = inference.call("test_call", {"expected": etag})

        assert (result["ok"], result["status"], result["reason"], result["sent"]) == (True, "accepted", None, True)
        route = f"local@127.0.0.1:{server.server_address[1]}/model-a"
        assert (result["model"], result["route"], result["local"]) == ("served-model", route, True)
        assert 0 <= result["seconds"] < 30 and result["limit_seconds"] == 30.0
        assert result["tokens"]["sums"]["input_tokens"] == 14 and result["tokens"]["sums"]["output_tokens"] == 2
        (attempt,) = result["attempts"]  # one attempt answered: the fallback is a retry's, never tried here
        assert (attempt["outcome"], attempt["measurement"], attempt["route"]) == ("completed", "provider_reported", route)
        assert (result["cost"], result["cost_state"]) == ("0.000072", "priced")  # 14 at 3 and 2 at 15, per million
        assert "ready" not in json.dumps(result)  # what the model said is not kept or returned

        # one request, to the endpoint a reply goes to, with the fixed prompt and a small reply bound
        ((where, _headers, body),) = server.requests
        sent = json.loads(body)
        assert where == "/v1/chat/completions" and sent["model"] == "model-a" and sent["stream"] is True
        assert sent["messages"] == [{"role": "user", "content": TEST_PROMPT}]
        assert sent["max_tokens"] == TEST_OUTPUT_TOKENS

        # booked and traced like any other call, on this device's recent calls
        trace = inference.call("diagnostics")["traces"][0]
        assert (trace["operation"], trace["status"]) == ("test_call", "ok")
        (role,) = trace["calls"]
        assert (role["stage"], role["attempts"], role["usage"]["sums"]["input_tokens"]) == ("npc_generation", 1, 14)
        assert (role["cost"], role["cost_state"]) == ("0.000072", "priced")
        assert result["recorded"] is True

        # no chat, no library file and no settings were touched
        assert _files(desk) == library and path.read_bytes() == settings and device("chats") == chats


def test_a_test_call_sends_the_key_of_the_key_variable_and_never_returns_it(desk, tmp_path, monkeypatch):
    monkeypatch.setenv("TABLE_TEST_API_KEY", KEY)

    def refuse(handler):  # a provider that echoes the key it was sent in its own error
        text = "no such key: " + handler.headers.get("Authorization", "")
        handler.respond(401, json.dumps({"error": {"message": text, "type": "invalid_api_key"}}).encode())

    with sse_server(refuse) as server, Device(desk, runtime=False) as device:
        inference = _table(device, tmp_path / "model.json", environ=os.environ)
        etag = _saved(inference, _settings(base_url(server), key_env="TABLE_TEST_API_KEY"))
        result = inference.call("test_call", {"expected": etag})
        assert (result["ok"], result["status"], result["reason"], result["http_status"]) == (False, "failed", "auth", 401)
        assert result["code"] == "invalid_api_key" and result["detail"].startswith("no such key: ")
        assert result["sent"] is True and result["attempts"][0]["outcome"] == "failed"
        assert server.requests[0][1]["Authorization"] == "Bearer " + KEY  # the same header a reply's call carries
        everything = json.dumps([result, inference.call("diagnostics"), inference.call("models")])
        assert KEY not in everything
        trace = inference.call("diagnostics")["traces"][0]
        assert (trace["operation"], trace["status"]) == ("test_call", "failed")  # a failed call is booked too
        assert trace["calls"][0]["failed"] == 1


def test_a_test_call_retries_as_the_settings_allow_and_books_every_attempt(desk, tmp_path):
    answers = iter([lambda handler: handler.respond(503, b"{}"), stream(sse_lines(deltas=("ready",), usage=USAGE))])
    with sse_server(lambda handler: next(answers)(handler)) as server, Device(desk, runtime=False) as device:
        inference = _table(device, tmp_path / "model.json")
        etag = _saved(inference, _settings(base_url(server), fallbacks=["model-b"], attempts=2, price_prompt="3",
                                           price_reply="15"))
        result = inference.call("test_call", {"expected": etag})
        assert result["ok"] is True and result["route"].endswith("/model-b")
        assert [(a["outcome"], a["route"].rsplit("/", 1)[1], a["reason"]) for a in result["attempts"]] == [
            ("failed", "model-a", "server_error"), ("completed", "model-b", None)]
        assert [json.loads(body)["model"] for _path, _headers, body in server.requests] == ["model-a", "model-b"]
        # the answer came from the fallback, which has no price; the priced model's failed attempt reported nothing
        assert (result["cost_state"], result["cost_lower_bound"], result["unpriced_attempts"]) == ("partial", True, 1)
        assert inference.call("diagnostics")["traces"][0]["calls"][0]["attempts"] == 2


def test_a_reply_cut_short_by_the_tests_own_small_bound_still_shows_the_model_answers(desk, tmp_path):
    with sse_server(stream(sse_lines(deltas=("rea",), finish="length", usage=USAGE))) as server, \
            Device(desk, runtime=False) as device:
        inference = _table(device, tmp_path / "model.json")
        result = inference.call("test_call", {"expected": _saved(inference, _settings(base_url(server)))})
        assert (result["ok"], result["status"], result["reason"]) == (True, "incomplete", "length")
        assert (result["cost"], result["cost_state"]) == (None, "unpriced")  # no prices: unknown, never free


def test_a_model_that_never_answers_is_given_up_on_and_said_to_have_timed_out(desk, tmp_path):
    def silent(conn, server):
        server.read_request(conn)
        server.release.wait(20)

    with RawServer(silent) as server, Device(desk, runtime=False) as device:
        inference = _table(device, tmp_path / "model.json", test_seconds=0.5)
        etag = _saved(inference, _settings(server.url(), attempts=3))
        assert inference.call("models")["test_call"]["seconds"] == 0.5
        result = inference.call("test_call", {"expected": etag})
        assert (result["ok"], result["status"], result["reason"]) == (False, "timeout", "timeout")
        assert result["seconds"] < 10 and result["limit_seconds"] == 0.5
        assert len(result["attempts"]) == 1 and result["sent"] is True  # cancelled as a whole: no retry follows
        assert result["tokens"]["unreported"]["input_tokens"] == 1  # sent, and what it used is unknown
        assert inference.call("diagnostics")["traces"][0]["status"] == "timeout"
        assert inference.call("models")["generation"]["available"] is True  # the app goes on as before


def test_a_test_call_never_sends_a_well_known_key_anywhere_but_its_own_provider_or_this_machine(
        desk, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    path = tmp_path / "model.json"
    # a file written by hand keeps its endpoint for turns (112); a test call is still not sent there
    path.write_text(json.dumps({"provider_id": "elsewhere", "base_url": "https://models.example.invalid/v1",
                                "model": "m", "key_env": "OPENAI_API_KEY"}))
    transport = _NoTransport()
    with Device(desk, runtime=False) as device:
        inference = _table(device, path, environ=os.environ, transport=transport)
        shown = inference.call("models")
        assert shown["generation"]["available"] is True
        assert shown["test_call"]["available"] is False and "api.openai.com" in shown["test_call"]["reason"]
        with pytest.raises(ApiRefused) as refused:
            inference.call("test_call", {"expected": shown["etag"]})
        assert refused.value.reason == "key_destination" and KEY not in str(refused.value)
        assert transport.asked == [] and inference.call("diagnostics")["traces"] == []

    # the same key to its own provider's host is allowed to go (here nothing answers: the attempt is booked)
    path.write_text(json.dumps({"provider_id": "openai", "base_url": "https://api.openai.com/v1", "model": "m",
                                "key_env": "OPENAI_API_KEY"}))
    with Device(desk, runtime=False) as device:
        inference = _table(device, path, environ=os.environ, transport=transport)
        shown = inference.call("models")
        assert shown["test_call"]["available"] is True
        result = inference.call("test_call", {"expected": shown["etag"]})
        assert transport.asked == ["https://api.openai.com/v1/chat/completions"]
        assert (result["ok"], result["status"], result["reason"], result["sent"]) == (False, "failed", "network", False)
        assert result["attempts"][0]["outcome"] == "not_sent" and KEY not in json.dumps(result)


def test_a_test_call_is_refused_when_there_is_no_model_or_the_settings_moved_on(desk, tmp_path, monkeypatch):
    monkeypatch.delenv("TABLE_UNSET_API_KEY", raising=False)
    path = tmp_path / "model.json"
    with sse_server(stream(sse_lines(usage=USAGE))) as server, Device(desk, runtime=False) as device:
        inference = _table(device, path)
        assert inference.call("models")["test_call"]["available"] is False
        for args, reason in (({}, "invalid_field"), ({"expected": None}, "no_model")):
            with pytest.raises(ApiRefused) as refused:
                inference.call("test_call", args)
            assert refused.value.reason == reason

        # a key variable that is not set: saved, generation is off, and there is nothing to test
        etag = _saved(inference, _settings(base_url(server), key_env="TABLE_UNSET_API_KEY"))
        shown = inference.call("models")
        assert shown["test_call"]["available"] is False and "TABLE_UNSET_API_KEY" in shown["test_call"]["reason"]
        with pytest.raises(ApiRefused) as refused:
            inference.call("test_call", {"expected": etag})
        assert refused.value.reason == "no_model"

        # the file changed by hand since the screen read it: what would be tested is not what was shown
        current = inference.call("save_models", {"settings": _settings(base_url(server)), "expected": etag})
        path.write_text(json.dumps(_settings(base_url(server), model="model-z")))
        with pytest.raises(ApiRefused) as refused:
            inference.call("test_call", {"expected": current["etag"]})
        assert refused.value.reason == "changed"
        assert server.requests == []
        again = inference.call("models")
        assert inference.call("test_call", {"expected": again["etag"]})["ok"] is True
        assert json.loads(server.requests[0][2])["model"] == "model-z"


def test_a_model_the_app_was_started_with_and_does_not_configure_is_not_tested(desk):
    with Device(desk) as device:  # the text API's own runtime, and no settings file
        inference = InferenceApi(device.api, SettingsFile(None), owner=OWNER, environ={})
        shown = inference.call("models")
        assert shown["generation"]["available"] is True and shown["test_call"]["available"] is False
        with pytest.raises(ApiRefused) as refused:
            inference.call("test_call", {"expected": None})
        assert refused.value.reason == "no_model"
