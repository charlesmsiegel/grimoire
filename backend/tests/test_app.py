"""044's runnable app: the loopback HTTP server, the phone page, the model configuration and the CLI.

Loopback only; synthetic libraries from ``textapi_fixtures``; every model call is a scripted fake (or no
model at all).
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from textapi_fixtures import DESKTOP, Device, desktop_library, pair_devices, phone_library, say, serving

from grimoire.app import AppServer, ConfigError, build_runtime, parse_model_config
from grimoire.app.cli import main
from grimoire.app.server import MAX_BODY, PAGE, TOKEN_HEADER, page_policy
from grimoire.peersync import PeerServer

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR synthetic"
SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture
def library(tmp_path):
    desk, card_chat = desktop_library(tmp_path / "desk")
    return desk, str(card_chat)


class Running:
    """An AppServer over one device, served in a thread."""

    def __init__(self, device: Device) -> None:
        self.app = AppServer(device.api)
        self.thread = threading.Thread(target=self.app.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.app.address

    def request(self, method: str, path: str, body: bytes | None = None, *, token: str | None = "",
                host: str | None = None, content_type: str | None = "application/json", headers=None):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=30)
        sent = {"Host": host if host is not None else f"127.0.0.1:{self.port}"}
        if token is not None:
            sent[TOKEN_HEADER] = self.app.token if token == "" else token
        if content_type is not None and body is not None:
            sent["Content-Type"] = content_type
        sent.update(headers or {})
        connection.request(method, path, body=body, headers=sent)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response, data

    def call(self, op: str, **args):
        response, data = self.request("POST", f"/api/{op}", json.dumps(args).encode("utf-8"))
        return response.status, json.loads(data)

    def close(self) -> None:
        self.app.close()
        self.thread.join(10)


@pytest.fixture
def running(library):
    desk, chat = library
    with Device(desk, [say("Over HTTP.")]) as device:
        server = Running(device)
        try:
            yield server, device, chat
        finally:
            server.close()


# -- the server ------------------------------------------------------------------------------------------

def test_the_page_is_served_with_a_policy_that_allows_only_itself(running):
    server, _device, _chat = running
    response, data = server.request("GET", "/", token=None)
    assert response.status == 200
    page = data.decode("utf-8")
    policy = response.getheader("Content-Security-Policy")
    assert policy == page_policy(page)
    assert "default-src 'none'" in policy and "connect-src 'self'" in policy and "frame-ancestors 'none'" in policy
    assert response.getheader("Cache-Control") == "no-store"
    # nothing from another origin, and nothing that turns library text into markup
    assert not re.search(r"(src|href)\s*=\s*\"(https?:)?//", page)
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert sink not in page


def test_a_request_for_another_host_is_refused_before_anything_is_read(running):
    server, device, chat = running
    before = device("open", chat=chat)
    for host in ("evil.example", "evil.example:%d" % server.port, "127.0.0.1:1", ""):
        response, data = server.request("POST", "/api/post", json.dumps({"chat": chat, "text": "x"}).encode(),
                                        host=host)
        assert response.status == 403 and json.loads(data)["error"] == "bad_host"
    assert device("open", chat=chat) == before


def test_the_api_needs_the_serves_token(running):
    server, device, chat = running
    before = device("open", chat=chat)
    for token in (None, "wrong", server.app.token[:-1]):
        response, data = server.request("POST", "/api/post", json.dumps({"chat": chat, "text": "x"}).encode(),
                                        token=token)
        assert response.status == 401 and json.loads(data)["error"] == "unauthorized"
        response, _ = server.request("GET", "/api/image?ref=x", token=token)
        assert response.status == 401
    assert device("open", chat=chat) == before


def test_ops_answer_json_with_their_refusal_status(running):
    server, _device, chat = running
    status, body = server.call("library")
    assert status == 200 and body["generation"] is True
    status, body = server.call("nope")
    assert (status, body["error"]) == (404, "unknown_op")
    status, body = server.call("open", chat="campaign:nowhere#chat/none")
    assert (status, body["error"]) == (404, "no_chat")
    status, body = server.call("post", chat=chat, text="Before I sit.")
    assert (status, body["error"]) == (409, "not_your_character")
    status, body = server.call("continue", chat=chat)
    assert status == 200 and body["state"]["seated"] is True
    server.call("post", chat=chat, text="Hello over HTTP.")
    status, body = server.call("generate", chat=chat)
    assert status == 200 and body["state"]["keepable"] == [1]
    assert "Traceback" not in json.dumps(body)


@pytest.mark.parametrize(("headers", "body", "status", "reason"), [
    ({"Content-Type": "text/plain"}, b"{}", 415, "unsupported_media"),
    ({"Content-Type": "application/json"}, b"[1]", 400, "invalid_field"),
    ({"Content-Type": "application/json"}, b"{not json", 400, "invalid_field"),
    ({"Content-Type": "application/json"}, b"\xff\xfe", 400, "invalid_field"),
])
def test_a_malformed_body_is_refused(running, headers, body, status, reason):
    server, _device, _chat = running
    response, data = server.request("POST", "/api/library", body, content_type=None, headers=headers)
    assert (response.status, json.loads(data)["error"]) == (status, reason)


def _raw(server: Running, head: bytes, body: bytes = b"") -> tuple[int, bytes]:
    with socket.create_connection((server.host, server.port), timeout=30) as raw:
        raw.sendall(head + body)
        reply = b""
        while chunk := raw.recv(65536):
            reply += chunk
    status = int(reply.split(b" ", 2)[1])
    return status, reply.split(b"\r\n\r\n", 1)[1]


def test_a_body_with_no_length_or_too_large_a_length_is_never_read(running):
    server, _device, _chat = running
    host = f"Host: 127.0.0.1:{server.port}\r\n{TOKEN_HEADER}: {server.app.token}\r\n".encode()
    status, _ = _raw(server, b"POST /api/library HTTP/1.1\r\n" + host +
                     b"Content-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n", b"2\r\n{}\r\n0\r\n\r\n")
    assert status == 411
    status, _ = _raw(server, b"POST /api/library HTTP/1.1\r\n" + host +
                     b"Content-Type: application/json\r\nContent-Length: %d\r\n\r\n" % (MAX_BODY + 1))
    assert status == 413
    status, _ = _raw(server, b"POST /api/library HTTP/1.1\r\n" + host + b"Content-Type: application/json\r\n\r\n")
    assert status == 411


def test_a_picture_added_over_http_is_read_back_byte_for_byte(running):
    server, device, chat = running
    scene = device("open", chat=chat)
    (mara,) = [member["actor"] for member in scene["cast"] if member["name"] == "Mara Vell"]
    status, scene = server.call("add_art", chat=chat, actor=mara, media=base64.b64encode(PNG).decode())
    assert status == 200
    (avatar,) = [slot for member in scene["cast"] if member["actor"] == mara for slot in member["art"]["slots"]
                 if slot["slot"] == "avatar"]
    response, data = server.request("GET", "/api/image?ref=" + avatar["image"].replace("#", "%23"))
    assert response.status == 200 and data == PNG
    assert response.getheader("Content-Type") == "image/png"
    assert "sandbox" in response.getheader("Content-Security-Policy")
    response, data = server.request("GET", "/api/image?ref=nonsense")
    assert response.status == 400


def test_the_server_serves_loopback_only(library):
    desk, _chat = library
    with Device(desk) as device:
        for host in ("0.0.0.0", "192.168.1.5", "localhost"):
            with pytest.raises(ValueError):
                AppServer(device.api, host=host)


def test_a_server_never_started_closes(library):
    desk, _chat = library
    with Device(desk) as device:
        AppServer(device.api).close()


# -- the model configuration -----------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    [], {}, {"provider_id": "p", "base_url": "https://x.example/v1"},
    {"provider_id": "p", "base_url": "http://x.example/v1", "model": "m"},
    {"provider_id": "p", "base_url": "https://u:p@x.example/v1", "model": "m"},
    {"provider_id": "p", "base_url": "https://x.example/v1", "model": "m", "key": "sk-in-the-file"},
    {"provider_id": "p", "base_url": "https://x.example/v1", "model": "m", "max_output_tokens": 0},
    {"provider_id": "p", "base_url": "https://x.example/v1", "model": "m", "max_tokens_field": "tokens"},
    {"provider_id": "p", "base_url": "https://x.example/v1", "model": "m", "key_env": ""},
])
def test_a_model_configuration_that_cannot_be_used_is_refused(raw):
    with pytest.raises(ConfigError):
        parse_model_config(raw)


def test_a_key_variable_that_is_not_set_is_refused_at_start():
    config = parse_model_config({"provider_id": "p", "base_url": "https://x.example/v1", "model": "m",
                                 "key_env": "GRIMOIRE_TEST_UNSET_KEY"})
    with pytest.raises(ConfigError):
        build_runtime(config, "human:owner", environ={})
    runtime, policy = build_runtime(config, "human:owner", environ={"GRIMOIRE_TEST_UNSET_KEY": "sk-test"})
    assert runtime is not None and policy is not None
    local = parse_model_config({"provider_id": "local", "base_url": "http://127.0.0.1:11434/v1", "model": "m"})
    assert build_runtime(local, "human:owner", environ={})[0] is not None


# -- the CLI ---------------------------------------------------------------------------------------------

def test_init_and_pair_make_two_libraries_that_sync(tmp_path, capsys):
    desk, phone = tmp_path / "desk", tmp_path / "phone"
    assert main(["init", str(desk), "--device", "desktop"]) == 0
    assert main(["init", str(phone), "--device", "phone"]) == 0
    assert main(["init", str(phone), "--device", "phone"]) == 2  # never over an existing library
    capsys.readouterr()
    assert main(["pair", str(desk), "--peer", "phone"]) == 0
    key = re.search(r"--key ([0-9a-f]+)", capsys.readouterr().out).group(1)
    assert main(["pair", str(phone), "--peer", "desktop", "--key", key]) == 0
    assert main(["pair", str(phone), "--peer", "desktop", "--key", "zz"]) == 2
    with Device(desk) as desktop, serving(desktop) as server, Device(phone, peers={DESKTOP: server.address}) as mobile:
        assert mobile("sync", peer=DESKTOP)["status"] == "synced"


def test_serve_refuses_peers_without_a_device_name_and_a_locked_library(tmp_path, capsys):
    bare = phone_library(tmp_path / "bare")  # created and secured, but never named
    assert main(["serve", str(bare), "--peer", "desktop=127.0.0.1:9"]) == 2
    assert "device name" in capsys.readouterr().err
    assert main(["serve", str(bare), "--host", "0.0.0.0"]) == 2
    with Device(bare):
        assert main(["pair", str(bare), "--peer", "desktop"]) == 2
        assert "another process" in capsys.readouterr().err


def test_serve_runs_as_a_process_and_answers_one_round_trip(tmp_path):
    folder = tmp_path / "lib"
    assert main(["init", str(folder), "--device", "phone"]) == 0
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(SRC), os.environ.get("PYTHONPATH", "")]))
    process = subprocess.Popen([sys.executable, "-m", "grimoire.app", "serve", str(folder), "--port", "0"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
    try:
        line = process.stdout.readline()
        match = re.match(r"open http://127\.0\.0\.1:(\d+)/#token=(\S+)", line)
        assert match, (line, process.stderr.read() if process.poll() is not None else "")
        port, token = int(match.group(1)), match.group(2)
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        connection.request("POST", "/api/library", body=b"{}", headers={
            "Host": f"127.0.0.1:{port}", TOKEN_HEADER: token, "Content-Type": "application/json"})
        response = connection.getresponse()
        body = json.loads(response.read())
        assert response.status == 200 and body["device"] == "phone" and body["generation"] is False
    finally:
        process.terminate()
        process.wait(30)


# -- two apps over HTTP ----------------------------------------------------------------------------------

def test_a_post_on_the_phones_app_resumes_on_the_desktops_app(tmp_path):
    desk, card_chat = desktop_library(tmp_path / "desk")
    phone = phone_library(tmp_path / "phone")
    pair_devices(desk, phone)
    chat = str(card_chat)
    with Device(desk) as desktop:
        peer_server = PeerServer(desktop.table.store, desktop.table.authority)
        threading.Thread(target=peer_server.serve_forever, daemon=True).start()
        desk_app = Running(desktop)
        try:
            with Device(phone, [say("Answered on the phone.")], peers={DESKTOP: peer_server.address}) as mobile:
                phone_app = Running(mobile)
                try:
                    assert phone_app.call("sync", peer=DESKTOP)[1]["status"] == "synced"
                    phone_app.call("continue", chat=chat)
                    phone_app.call("post", chat=chat, text="Typed on the phone.")
                    _status, offered = phone_app.call("generate", chat=chat)
                    status, scene = phone_app.call("keep", chat=chat, take=1, turn=offered["state"]["open_turn"])
                    assert status == 200
                    turns = [turn["turn"] for turn in scene["turns"]]
                    assert phone_app.call("sync", peer=DESKTOP)[1]["status"] == "synced"
                finally:
                    phone_app.close()
            status, scene = desk_app.call("open", chat=chat)
            assert status == 200 and [turn["turn"] for turn in scene["turns"]] == turns
            assert scene["turns"][-1]["kept"]["lines"][0]["text"] == "Answered on the phone."
        finally:
            desk_app.close()
            peer_server.close()


def test_the_page_file_is_packaged_beside_the_server():
    assert PAGE.is_file() and "<script>" in PAGE.read_text(encoding="utf-8")


def test_identify_names_an_existing_library_once(tmp_path, capsys):
    bare = phone_library(tmp_path / "bare")
    assert main(["identify", str(bare), "--device", "laptop"]) == 0
    with Device(bare) as device:
        assert device("library")["device"] == "laptop"
    assert main(["identify", str(bare), "--device", "other"]) == 2


# -- review: what a local app could otherwise do --------------------------------------------------------

def test_an_absolute_form_request_target_never_skips_the_token(running):
    server, device, chat = running
    before = device("open", chat=chat)
    host = f"Host: 127.0.0.1:{server.port}\r\n".encode()
    for target in (b"http://127.0.0.1:%d/api/continue" % server.port, b"//x/api/continue", b"api/continue"):
        status, data = _raw(server, b"POST " + target + b" HTTP/1.0\r\n" + host +
                            b"Content-Type: application/json\r\nContent-Length: %d\r\n\r\n" % len(json.dumps({"chat": chat})),
                            json.dumps({"chat": chat}).encode())
        # (http.server itself folds a leading // into one /, which then routes nowhere: 404)
        assert (status, json.loads(data)["error"]) in ((400, "invalid_field"), (404, "not_found")), target
    status, _ = _raw(server, b"GET http://127.0.0.1:%d/api/image?ref=x HTTP/1.0\r\n" % server.port + host + b"\r\n")
    assert status == 400
    assert device("open", chat=chat) == before


def test_a_length_that_is_not_ascii_digits_and_a_deeply_nested_body_are_answered(running):
    server, _device, _chat = running
    head = f"POST /api/library HTTP/1.0\r\nHost: 127.0.0.1:{server.port}\r\n{TOKEN_HEADER}: {server.app.token}\r\n"
    status, _ = _raw(server, head.encode() + "Content-Type: application/json\r\nContent-Length: ²\r\n\r\n"
                     .encode("latin-1"))
    assert status == 411
    nested = b"[" * 200000
    status, data = _raw(server, head.encode() + b"Content-Type: application/json\r\nContent-Length: %d\r\n\r\n"
                        % len(nested), nested)
    assert status == 400 and json.loads(data)["error"] == "invalid_field"


def test_a_request_trickled_a_byte_at_a_time_is_dropped_at_its_deadline(running, monkeypatch):
    import time

    import grimoire.app.server as server_module

    monkeypatch.setattr(server_module, "HEADER_DEADLINE", 0.5)
    server, _device, _chat = running
    slow = [socket.create_connection((server.host, server.port), timeout=10)
            for _ in range(server_module.MAX_CONNECTIONS)]
    try:
        for connection in slow:
            connection.sendall(b"G")  # each holds a slot, never finishing its request line
        time.sleep(1.5)
        for connection in slow:  # the server dropped every one at its deadline
            assert connection.recv(1) == b""
        # and serves the page again once their handlers have returned their slots: a slot is released when
        # the handler thread ends, a moment after the client sees the connection close (slower on Windows)
        give_up = time.monotonic() + 10
        while True:
            try:
                response, _ = server.request("GET", "/", token=None)
                break
            except (http.client.RemoteDisconnected, ConnectionError):
                if time.monotonic() > give_up:
                    raise
                time.sleep(0.05)
        assert response.status == 200
    finally:
        for connection in slow:
            connection.close()


def test_init_refuses_a_bad_owner_or_device_before_writing_anything(tmp_path, capsys):
    for args in (["--device", "phone", "--owner", "bob"], ["--device", ""], ["--device", "a/b"]):
        folder = tmp_path / "lib"
        assert main(["init", str(folder), *args]) == 2
        assert not folder.exists() or not any(folder.iterdir())
    assert main(["init", str(tmp_path / "lib"), "--device", "phone"]) == 0  # and it can be run again


def test_serve_on_a_port_in_use_or_out_of_range_says_so(tmp_path, capsys):
    folder = tmp_path / "lib"
    assert main(["init", str(folder), "--device", "phone"]) == 0
    capsys.readouterr()
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        assert main(["serve", str(folder), "--port", str(port)]) == 2
        assert "cannot listen" in capsys.readouterr().err
        assert main(["serve", str(folder), "--port", "0", "--sync-port", str(port)]) == 2
        assert "cannot listen" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["serve", str(folder), "--port", "99999"])
    with Device(folder):  # the refused serves released the library
        pass


def test_pairing_prints_the_command_for_the_other_device(tmp_path, capsys):
    desk = tmp_path / "desk"
    assert main(["init", str(desk), "--device", "desktop"]) == 0
    capsys.readouterr()
    assert main(["pair", str(desk), "--peer", "phone"]) == 0
    assert re.search(r"pair --peer desktop --key [0-9a-f]+", capsys.readouterr().out)


def test_a_key_pasted_as_its_variable_is_refused_without_being_shown():
    with pytest.raises(ConfigError) as caught:
        parse_model_config({"provider_id": "p", "base_url": "https://x.example/v1", "model": "m",
                            "key_env": "sk-or-v1-0123456789abcdef"})
    assert "sk-or" not in str(caught.value)
