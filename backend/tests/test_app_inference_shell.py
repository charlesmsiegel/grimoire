"""112's Models and costs page and ops as the app serves them: through 108's shell and adapter seam, and from
``serve --model FILE`` as a process.

Synthetic libraries from ``textapi_fixtures``; the model is a loopback server from ``provider_servers``.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from provider_servers import base_url, sse_lines, sse_server, stream
from store_helpers import OWNER
from textapi_fixtures import V1_CHAT, Device, desktop_library

from grimoire.app.adapters import providers
from grimoire.app.appapi import AppApi
from grimoire.app.cli import main
from grimoire.app.pages import load_pages
from grimoire.app.server import STATIC, TOKEN_HEADER
from grimoire.textapi import ApiRefused

SRC = Path(__file__).resolve().parent.parent / "src"
USAGE = {"prompt_tokens": 120, "completion_tokens": 9}
CHAT = str(V1_CHAT)


@pytest.fixture
def desk(tmp_path):
    folder, _card_chat = desktop_library(tmp_path / "desk")
    return folder


@pytest.fixture
def model():
    with sse_server(stream(sse_lines(deltas=("The tide keeps its own ledger.",), usage=USAGE))) as server:
        yield server


def _settings(server) -> dict:
    return {"provider_id": "local", "base_url": base_url(server), "model": "model-a", "director": 1}


def test_the_page_is_in_the_settings_slot_and_every_op_it_needs_is_served(desk):
    page = next(page for page in load_pages(STATIC) if page.id == "providers")
    assert (page.route, page.slot, page.owner) == ("providers", "settings", "112")
    with Device(desk, runtime=False) as device:
        app = AppApi(device.api)
        assert page.to_json(app.ops)["missing"] == []
        assert {f"providers.{op}" for op in providers.OPS} <= set(app.call("library")["ops"])


def test_a_model_saved_through_the_shell_plays_the_next_turn_and_the_library_says_so(desk, model, tmp_path):
    with Device(desk, runtime=False) as device:
        providers.configure(device.api, tmp_path / "model.json", environ={})
        app = AppApi(device.api)
        assert app.call("library")["generation"] is False
        shown = app.call("providers.models")
        assert shown["generation"]["problem"]["code"] == "not_configured" and shown["file"]["configured"]
        with pytest.raises(ApiRefused) as refused:  # a refusal reaches the client as a core op's would
            app.call("providers.save_models", {"settings": {**_settings(model), "attempts": 9}, "expected": None})
        assert refused.value.reason == "invalid_settings"
        app.call("providers.save_models", {"settings": _settings(model), "expected": shown["etag"]})
        assert app.call("library")["generation"] is True
        device("continue", chat=CHAT)
        device("post", chat=CHAT, text="Who keeps the ledger?")
        device("generate", chat=CHAT)
        calls = [call for turn in app.call("providers.usage", {"chat": CHAT})["turns"] for call in turn["calls"]]
        assert calls and all(call["attempts"][0]["measurement"] == "provider_reported" for call in calls)


def test_a_library_nobody_configured_keeps_the_runtime_it_was_built_with(desk):
    with Device(desk) as device:  # a scripted runtime, given by whoever built the text API
        before = device.api.runtime
        shown = AppApi(device.api).call("providers.models")
        assert device.api.runtime is before and shown["generation"]["available"]
        assert shown["file"] == {"configured": False, "exists": False} and shown["settings"] is None
        with pytest.raises(ValueError, match="already configured"):
            providers.configure(device.api, None)


def test_serve_starts_without_a_model_when_its_settings_file_is_missing_and_serves_the_page(tmp_path):
    folder, settings = tmp_path / "lib", tmp_path / "settings" / "model.json"
    assert main(["init", str(folder), "--device", "phone", "--owner", OWNER]) == 0
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(SRC), os.environ.get("PYTHONPATH", "")]))
    process = subprocess.Popen([sys.executable, "-m", "grimoire.app", "serve", str(folder), "--owner", OWNER,
                                "--port", "0", "--model", str(settings)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
    try:
        line = process.stdout.readline()
        match = re.match(r"open http://127\.0\.0\.1:(\d+)/#token=(\S+)", line)
        assert match, (line, process.stderr.read() if process.poll() is not None else "")
        port, token = int(match.group(1)), match.group(2)

        def ask(method: str, path: str, body: bytes | None = None, *, with_token: bool = True):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
            headers = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"}
            if with_token:
                headers[TOKEN_HEADER] = token
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, response.read()

        status, data = ask("POST", "/api/providers.models", b"{}")
        shown = json.loads(data)
        assert status == 200 and shown["file"] == {"configured": True, "exists": False}
        assert shown["generation"]["problem"]["code"] == "not_configured"
        status, data = ask("GET", "/shell/pages", with_token=False)
        page = next(page for page in json.loads(data)["pages"] if page["id"] == "providers")
        assert page["missing"] == [] and page["unavailable"] is None
        status, _data = ask("GET", "/static/pages/providers/page.js", with_token=False)
        assert status == 200
        status, _data = ask("GET", "/static/pages/providers/page.json", with_token=False)
        assert status == 404  # a manifest is not served
        assert ask("POST", "/api/providers.models", b"{}", with_token=False)[0] == 401
        assert not settings.exists()  # nothing is written until a save
    finally:
        process.terminate()
        process.wait(30)


def test_the_first_run_flag_says_whether_the_settings_file_would_give_a_model(desk, model, tmp_path):
    path = tmp_path / "model.json"
    assert providers.usable(None, OWNER) is False and providers.usable(path, OWNER) is False  # none, missing
    path.write_text("{", encoding="utf-8")
    assert providers.usable(path, OWNER) is False  # unusable
    path.unlink()
    with Device(desk, runtime=False) as device:
        settings = providers.configure(device.api, path, environ={})
        etag = settings.call("models")["etag"]
        settings.call("save_models", {"settings": {**_settings(model), "key_env": "LOCAL_API_KEY"}, "expected": etag})
    assert providers.usable(path, OWNER, environ={}) is False  # saved, but its key variable is not set
    assert providers.usable(path, OWNER, environ={"LOCAL_API_KEY": "sk-synthetic-value"}) is True
