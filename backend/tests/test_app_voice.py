"""061's voice adapter ops (docs/SPEECH.md) over a synthetic library: the proving scenario.

A person chooses voice input on this device only; a recording is recognized here with no connection out of the
process, nothing is kept unless they chose to keep it and then only in memory, and turning the microphone (or
voice) off leaves typed play exactly as it was. A stand-in local recognizer covers the flow; the last test runs
the real installed engine on synthetic Flite speech where both are present.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from textapi_fixtures import Device, desktop_library

from grimoire.app.adapters import voice
from grimoire.app.appapi import AppApi
from grimoire.app.server import AppServer
from grimoire.intent import Utterance
from grimoire.speech import BackendInfo
from grimoire.speech.measure import NetworkGuard, flite_available, synthesize
from grimoire.speech.sphinx import RATE, PocketSphinxBackend, installed, to_wav
from grimoire.textapi import ApiRefused


class LocalStandIn:
    """A trusted local recognizer that records each call; it 'hears' a fixed sentence."""

    def __init__(self, *, available: bool = True, heard: str = "is the cargo safe") -> None:
        self.info = BackendInfo("pocketsphinx", frozenset({"stt"}), "local", False, False, "free", available)
        self.calls: list[bytes] = []
        self.heard = heard
        self.max_seconds = 15
        self.unloaded = 0

    def transcribe(self, audio: bytes) -> Utterance:
        self.calls.append(audio)
        return Utterance(self.heard, origin="voice")

    def synthesize(self, text: str) -> bytes:
        raise NotImplementedError

    def unload(self) -> None:
        self.unloaded += 1


def tone(seconds: float = 1.0) -> str:
    samples = b"".join(((1000 if (i // 20) % 2 else -1000) & 0xFFFF).to_bytes(2, "little") for i in range(int(seconds * RATE)))
    return base64.b64encode(to_wav(samples)).decode("ascii")


@pytest.fixture
def app(tmp_path):
    desk, chat = desktop_library(tmp_path / "desk")
    with Device(desk) as opened:
        backend = LocalStandIn()
        voice.configure(opened.api, (backend,))
        yield AppApi(opened.api), backend, desk, str(chat)


def _refused(api: AppApi, op: str, args: dict, reason: str, status: int = 400) -> None:
    with pytest.raises(ApiRefused) as refused:
        api.call(op, args)
    assert (refused.value.reason, refused.value.status) == (reason, status), str(refused.value)


def _files(root) -> dict[str, int]:
    return {str(path.relative_to(root)): path.stat().st_mtime_ns for path in root.rglob("*") if path.is_file()}


def _ready(api: AppApi, **extra) -> dict:
    return api.call("voice.configure", {"mode": "local_only", "backend": "pocketsphinx", "recording": True, **extra})


def _type(api: AppApi, chat: str, text: str) -> dict:
    """Ordinary typed play: continue the chat, then post a line."""
    api.call("continue", {"chat": chat})
    return api.call("post", {"chat": chat, "text": text})


def _posted(scene: dict) -> list[str]:
    return [turn["post"]["text"] for turn in scene["turns"] if turn.get("post")]


def _say(api: AppApi, status: dict, audio: str | None = None) -> dict:
    return api.call("voice.transcribe", {"sequence": status["next_sequence"], "capture_token": status["capture_token"],
                                         "audio": audio or tone()})


def test_every_launch_starts_with_voice_off_the_microphone_off_and_nothing_kept(app):
    api, backend, _desk, _chat = app
    assert {"voice.status", "voice.configure", "voice.transcribe", "voice.delete", "voice.unload"} <= set(api.ops)
    status = api.call("voice.status", {})
    assert (status["mode"], status["recording"], status["retain"], status["kept"]) == ("off", False, [], [])
    assert status["stt"]["status"] == "recording_off" and status["tts"]["status"] == "off"
    assert status["capture_token"] is None and status["next_sequence"] is None
    assert [item["id"] for item in status["backends"]] == ["pocketsphinx"] and status["backends"][0]["locality"] == "local"
    assert backend.calls == []


def test_only_off_or_this_device_only_can_be_chosen(app):
    api, _backend, _desk, _chat = app
    _refused(api, "voice.configure", {"mode": "provider", "backend": "pocketsphinx"}, "invalid_field")
    _refused(api, "voice.configure", {"mode": "local_only", "backend": "cloud"}, "invalid_field")
    _refused(api, "voice.configure", {"mode": "local_only"}, "invalid_field")
    _refused(api, "voice.configure", {"recording": "yes"}, "invalid_field")
    _refused(api, "voice.configure", {"retain": ["synthesized_audio"]}, "invalid_field")
    _refused(api, "voice.configure", {"backend": "pocketsphinx", "max_seconds": 61}, "invalid_field")
    assert api.call("voice.status", {})["mode"] == "off"  # a refused choice changes nothing


def test_local_only_recognizes_with_no_connection_out_and_writes_nothing(app):
    api, backend, desk, _chat = app
    status = _ready(api)
    assert status["stt"] == {"status": "ready", "backend": "pocketsphinx", "input": "audio", "locality": "local",
                             "network": False, "retains_inputs": False, "billing": "free", "fallback": "text"}
    before = _files(desk)
    guard = NetworkGuard()
    with guard.active():
        heard = _say(api, status)
    assert heard["status"] == "ok" and heard["text"] == "is the cargo safe"
    assert guard.attempts == [] and len(backend.calls) == 1
    assert heard["kept"] == [] and api.call("voice.status", {})["kept"] == []  # nothing kept by default
    assert _files(desk) == before  # no recording, transcript or setting reaches the library


def test_what_is_kept_is_listed_held_in_memory_and_deleted_on_request(app):
    api, _backend, desk, _chat = app
    _refused(api, "voice.configure", {"retain": ["raw_audio"]}, "invalid_field")  # recordings are never kept
    status = _ready(api, retain=["provisional_transcript"])
    heard = _say(api, status)
    assert heard["kept"] == ["provisional_transcript"]
    assert not any("wav" in name or "voice" in name for name in _files(desk))
    deleted = api.call("voice.delete", {})
    assert deleted["kept"] == [] and deleted["capture_token"] != status["capture_token"]
    # a recording latched before the delete is stale: it is dropped, never recognized under the new token
    assert _say(api, {**deleted, "capture_token": status["capture_token"]})["status"] == "stale_capture"


def test_microphone_off_recognizes_nothing_and_typed_play_is_unaffected(app):
    api, backend, _desk, chat = app
    status = _ready(api)
    off = api.call("voice.configure", {"recording": False})
    assert off["stt"]["status"] == "recording_off" and off["capture_token"] is None
    answer = api.call("voice.transcribe", {"sequence": status["next_sequence"], "capture_token": status["capture_token"],
                                           "audio": tone()})
    assert answer["status"] == "recording_off" and backend.calls == []
    posted = _type(api, chat, '"Is the cargo safe?"')  # typing works with the microphone off
    assert _posted(posted)[-1] == "Is the cargo safe?"
    voiceless = api.call("voice.configure", {"mode": "off"})
    assert voiceless["stt"]["status"] == "recording_off" and backend.unloaded >= 1
    reopened = api.call("open", {"chat": chat})  # and the typed line is the chat's, whatever voice is set to
    assert _posted(reopened)[-1] == "Is the cargo safe?" and backend.calls == []


def test_a_missing_recognizer_is_said_and_no_other_is_used(tmp_path):
    desk, _chat = desktop_library(tmp_path / "desk")
    with Device(desk) as opened:
        backend = LocalStandIn(available=False)
        voice.configure(opened.api, (backend,))
        api = AppApi(opened.api)
        status = _ready(api)
        assert status["stt"]["status"] == "unavailable" and status["capture_token"] is None
        answer = api.call("voice.transcribe", {"sequence": 1, "capture_token": None, "audio": tone()})
        assert answer["status"] == "unavailable" and backend.calls == []


def test_bad_or_overlong_audio_is_refused_before_recognition(app):
    api, backend, _desk, _chat = app
    status = _ready(api, max_seconds=2)
    _refused(api, "voice.transcribe", {**{k: status[k] for k in ()}, "sequence": status["next_sequence"],
                                       "capture_token": status["capture_token"], "audio": tone(3)}, "too_long")
    _refused(api, "voice.transcribe", {"sequence": status["next_sequence"], "capture_token": status["capture_token"],
                                       "audio": base64.b64encode(b"RIFF-not-wav").decode()}, "invalid_audio")
    _refused(api, "voice.transcribe", {"sequence": status["next_sequence"], "capture_token": status["capture_token"],
                                       "audio": "***"}, "invalid_field")
    _refused(api, "voice.transcribe", {"sequence": 0, "audio": tone()}, "invalid_field")
    assert backend.calls == []
    assert _say(api, status)["status"] == "ok"  # refusals consumed no sequence


def test_a_replayed_recording_is_not_recognized_twice(app):
    api, backend, _desk, _chat = app
    status = _ready(api)
    assert _say(api, status)["status"] == "ok"
    assert _say(api, status)["status"] == "already_processed" and len(backend.calls) == 1


def test_a_failing_recognizer_keeps_nothing_and_says_so(app):
    api, backend, _desk, _chat = app

    def broken(audio: bytes) -> Utterance:
        raise RuntimeError("secret words the person said")

    backend.transcribe = broken
    status = _ready(api, retain=["provisional_transcript"])
    answer = _say(api, status)
    assert answer == {"status": "backend_failed", "next_sequence": status["next_sequence"] + 1,
                      "measurements": None, "kept": []}


def test_a_slow_recognition_never_holds_up_typed_play(app):
    """Voice ops run without the library's writer lock: core ops finish while a recognition is still running."""
    api, backend, _desk, chat = app
    started, release = threading.Event(), threading.Event()

    def slow(audio: bytes) -> Utterance:
        started.set()
        assert release.wait(30)
        return Utterance("is the cargo safe", origin="voice")

    backend.transcribe = slow
    status = _ready(api)
    answers: list[dict] = []
    worker = threading.Thread(target=lambda: answers.append(_say(api, status)))
    worker.start()
    try:
        assert started.wait(10)
        done = threading.Event()
        opened: list[dict] = []

        def core() -> None:
            opened.append(api.call("open", {"chat": chat}))
            done.set()

        threading.Thread(target=core).start()
        assert done.wait(10), "a core op waited on the recognizer"
        assert len(opened) == 1
    finally:
        release.set()
        worker.join(30)
    assert answers and answers[0]["status"] == "ok"


def test_an_adapter_may_only_unlock_its_own_ops():
    from grimoire.app.adapter import Adapter
    with pytest.raises(ValueError):
        Adapter("demo", owner="000", ops={"a": lambda ctx, args: {}}, unlocked=frozenset({"b"}))
    with pytest.raises(ValueError):
        Adapter("demo", owner="000", ops={"a": lambda ctx, args: {}}, unlocked={"a"})
    assert voice.ADAPTER.unlocked == frozenset(voice.OPS)


@pytest.mark.skipif(not (installed() and flite_available()), reason="pocketsphinx and ffmpeg with Flite are needed")
def test_the_installed_engine_hears_synthetic_speech_through_the_app(tmp_path):
    desk, chat = desktop_library(tmp_path / "desk")
    with Device(desk) as opened:
        engine = PocketSphinxBackend(phrases=("open the door", "pause the scene", "yes", "no"))
        voice.configure(opened.api, (engine,))
        api = AppApi(opened.api)
        status = _ready(api)
        guard = NetworkGuard()
        with guard.active():
            heard = _say(api, status, base64.b64encode(synthesize("open the door", "slt")).decode("ascii"))
        assert heard["status"] == "ok" and heard["text"] == "open the door" and guard.attempts == []
        measured = heard["measurements"]
        assert measured["loaded"] and measured["load_seconds"] > 0 and measured["recognitions"] == 1
        assert measured["last_real_time_factor"] < 1
        assert api.call("voice.unload", {})["backends"][0]["measurements"]["loaded"] is False
        assert _posted(_type(api, str(chat), "I open the door."))[-1] == "I open the door."
    assert os.path.isdir(desk)


NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
@pytest.mark.parametrize("installed_engine", [True, False])
def test_the_voice_page_end_to_end(tmp_path, installed_engine):
    desk, _chat = desktop_library(tmp_path / "desk")
    with Device(desk) as opened:
        backend = LocalStandIn(available=installed_engine)
        voice.configure(opened.api, (backend,))
        app = AppServer(opened.api)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = app.address
            env = {**os.environ, "GRIMOIRE_APP": f"http://{host}:{port}", "GRIMOIRE_TOKEN": app.token,
                   "GRIMOIRE_VOICE_MISSING": "0" if installed_engine else "1"}
            result = subprocess.run([NODE, "--test", "--test-reporter=tap", str(Path(__file__).with_name("js") / "voice.e2e.mjs")],
                                    capture_output=True, text=True, timeout=240, check=False, env=env)
        finally:
            app.close()
            thread.join(10)
        assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-3000:]
        assert "# pass 1" in result.stdout
        if installed_engine:
            assert len(backend.calls) == 1 and backend.calls[0][:4] == b"RIFF"  # one WAV, from the page's Stop
        else:
            assert backend.calls == []
