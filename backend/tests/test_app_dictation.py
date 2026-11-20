"""080's dictation ops (docs/SPEECH.md, "Speech to draft") over a synthetic library, and its proving scenario on the
real chat page.

Dictation records through 061's private route, so it is off until voice input is on this device with the
microphone on, and refuses or drops exactly what ``voice.transcribe`` does. What it adds is a revised composer
draft: an invented name dictated, then "no, I meant the west gate", then "post it". No op posts, writes the library
or touches a scene; the page's Post does that, once, with the player's edited text. A stand-in local recognizer
covers the flow; the last test runs the installed engine on synthetic Flite speech where both are present.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from grimoire.app import AppServer
from grimoire.app.adapters import dictation, voice
from grimoire.app.appapi import AppApi
from grimoire.intent import Utterance
from grimoire.speech import BackendInfo
from grimoire.speech.measure import flite_available, synthesize
from grimoire.speech.sphinx import RATE, PocketSphinxBackend, installed, to_wav
from grimoire.textapi import ApiRefused
from textapi_fixtures import Device, desktop_library

TESTS = Path(__file__).with_name("js")
NODE = shutil.which("node")
NAMES = ["Mara Vell", "Anya Rook"]


class Hears:
    """A trusted local recognizer that hears each of ``said`` in turn and records every call."""

    def __init__(self, *said: str, confidence: float | None = None) -> None:
        self.info = BackendInfo("pocketsphinx", frozenset({"stt"}), "local", False, False, "free", True)
        self.said = list(said)
        self.calls: list[bytes] = []
        self.max_seconds = 15
        self.confidence = confidence

    def transcribe(self, audio: bytes) -> Utterance:
        self.calls.append(audio)
        return Utterance(self.said.pop(0), origin="voice", confidence=self.confidence)

    def synthesize(self, text: str) -> bytes:
        raise NotImplementedError

    def unload(self) -> None:
        pass


def tone(seconds: float = 1.0) -> str:
    samples = b"".join(((1000 if (i // 20) % 2 else -1000) & 0xFFFF).to_bytes(2, "little") for i in range(int(seconds * RATE)))
    return base64.b64encode(to_wav(samples)).decode("ascii")


def _files(folder: Path) -> dict[str, bytes]:
    return {path.relative_to(folder).as_posix(): path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file()}


@pytest.fixture
def library(tmp_path):
    desk, chat = desktop_library(tmp_path / "desk")
    yield desk, str(chat)


def _app(device: Device, *said: str, **options) -> tuple[AppApi, Hears]:
    backend = Hears(*said, **options)
    voice.configure(device.api, (backend,))
    return AppApi(device.api), backend


def _on(api: AppApi) -> dict:
    api.call("voice.configure", {"mode": "local_only", "backend": "pocketsphinx", "recording": True})
    return api.call("dictation.status", {})


def _hear(api: AppApi, status: dict, draft: str = "", last: str | None = None, **extra) -> dict:
    return api.call("dictation.hear", {"audio": tone(), "sequence": status["next_sequence"],
                                       "capture_token": status["capture_token"], "draft": draft, "last": last,
                                       "names": NAMES, **extra})


def _refused(api: AppApi, op: str, args: dict, reason: str = "invalid_field", status: int = 400) -> None:
    with pytest.raises(ApiRefused) as refused:
        api.call(op, args)
    assert (refused.value.reason, refused.value.status) == (reason, status), str(refused.value)


def test_dictation_is_off_until_voice_is_on_this_device_and_records_nothing_meanwhile(library):
    desk, _chat = library
    with Device(desk, runtime=False) as device:
        api, backend = _app(device, "unused")
        told = api.call("dictation.status", {})
        assert (told["status"], told["capture_token"], told["next_sequence"]) == ("off", None, None)
        assert told["language"]["tag"] == "en-US" and told["language"]["choices"] == ["en-US"]
        api.call("voice.configure", {"mode": "local_only", "backend": "pocketsphinx"})
        assert api.call("dictation.status", {})["status"] == "recording_off"
        answer = api.call("dictation.hear", {"audio": tone(), "sequence": 1, "capture_token": "x", "draft": "Typed"})
        assert (answer["status"], answer["revision"], answer["heard"]) == ("recording_off", None, None)
        assert backend.calls == [], "the recognizer is not touched while the microphone is off"


def test_the_proving_scenario_through_the_ops_writes_nothing(library):
    desk, chat = library
    with Device(desk, runtime=False) as device:
        api, backend = _app(device, "we meet quill on at the east gate", "no i meant the west gate", "post it")
        api.call("continue", {"chat": chat})
        before = _files(desk)
        status = _on(api)
        first = _hear(api, status)
        assert (first["status"], first["heard"]) == ("ok", "we meet quill on at the east gate")
        draft = first["revision"]
        assert (draft["kind"], draft["text"]) == ("added", "We meet quill on at the east gate")
        status = api.call("dictation.status", {})
        fixed = _hear(api, status, draft["text"], draft["passage"])["revision"]
        assert (fixed["kind"], fixed["text"], fixed["before"], fixed["after"]) == \
            ("corrected", "We meet quill on at the west gate", "the east gate", "the west gate")
        assert fixed["alternative"]["text"] == "We meet quill on at the east gate no I meant the west gate"
        asked = _hear(api, api.call("dictation.status", {}), fixed["text"], fixed["passage"])["revision"]
        assert (asked["kind"], asked["text"]) == ("not_posted", fixed["text"])
        assert len(backend.calls) == 3
        assert _files(desk) == before, "dictation wrote nothing: no turn, draft or record"
        assert api.call("open", {"chat": chat})["turns"] == AppApi(device.api).call("open", {"chat": chat})["turns"]


def test_a_recording_made_under_old_settings_is_dropped_unheard(library):
    desk, _chat = library
    with Device(desk, runtime=False) as device:
        api, backend = _app(device, "unused")
        status = _on(api)
        api.call("voice.configure", {"retain": ["provisional_transcript"]})  # any change rotates the capture token
        answer = _hear(api, status, "Typed")
        assert (answer["status"], answer["revision"]) == ("stale_capture", None)
        assert backend.calls == []


def test_a_bad_draft_or_name_list_is_refused_before_anything_is_recognized(library):
    desk, _chat = library
    with Device(desk, runtime=False) as device:
        api, backend = _app(device, "unused")
        status = _on(api)
        base_args = {"audio": tone(), "sequence": status["next_sequence"], "capture_token": status["capture_token"]}
        _refused(api, "dictation.hear", {**base_args})  # no draft
        _refused(api, "dictation.hear", {**base_args, "draft": "x" * 20001})
        _refused(api, "dictation.hear", {**base_args, "draft": "", "last": 7})
        _refused(api, "dictation.hear", {**base_args, "draft": "", "names": ["x"] * 65})
        _refused(api, "dictation.hear", {**base_args, "draft": "", "names": [""]})
        _refused(api, "dictation.hear", {**base_args, "draft": "", "audio": "not base64!"})
        assert backend.calls == []
        _refused(api, "dictation.revise", {"draft": "", "heard": "we run", "confidence": 2})
        _refused(api, "dictation.revise", {"draft": "", "heard": "we run", "confidence": True})
        _refused(api, "dictation.revise", {"draft": ""})


def test_revise_answers_the_same_words_against_a_draft_typed_in_meanwhile(library):
    desk, _chat = library
    with Device(desk, runtime=False) as device:
        api, _backend = _app(device)
        revision = api.call("dictation.revise", {"draft": "We hide. ", "last": None, "heard": "tell mara bell",
                                                 "confidence": 0.3, "names": NAMES})["revision"]
        assert (revision["text"], revision["uncertain"]) == ("We hide. Tell mara bell", True)
        assert revision["suggestions"] == [{"heard": "mara bell", "name": "Mara Vell", "offset": 5, "at": 14}]


def test_dictation_ops_never_take_the_library_lock():
    assert dictation.ADAPTER.unlocked == frozenset(dictation.ADAPTER.ops)


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_the_proving_scenario_on_the_chat_page(tmp_path):
    desk, chat = desktop_library(tmp_path / "desk")
    with Device(desk, runtime=False) as device:  # no model: nothing could be sent one
        _api, backend = _app(device, "we meet quill on at the east gate", "no i meant the west gate", "post it")
        AppApi(device.api).call("continue", {"chat": str(chat)})  # this device takes its seat
        before = _files(desk)
        app = AppServer(device.api)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = app.address
            result = subprocess.run([NODE, "--test", "--test-reporter=tap", str(TESTS / "dictate.e2e.mjs")],
                                    capture_output=True, text=True, timeout=240, check=False,
                                    env={**os.environ, "GRIMOIRE_APP": f"http://{host}:{port}", "GRIMOIRE_TOKEN": app.token,
                                         "GRIMOIRE_CHAT": str(chat)})
        finally:
            app.close()
            thread.join(10)
        assert result.returncode == 0, result.stdout[-8000:] + result.stderr[-3000:]
        assert "# pass 1" in result.stdout
        assert len(backend.calls) == 3 and all(audio[:4] == b"RIFF" for audio in backend.calls)
        after = _files(desk)
    assert all(before[name] == after.get(name) for name in before), "nothing already there was rewritten"
    added = [name for name in sorted(set(after) - set(before)) if not name.startswith("authority/security/")]
    assert [name.split("/")[2] for name in added] == ["turns"], added  # the one post, made by Post


@pytest.mark.skipif(not (installed() and flite_available()), reason="pocketsphinx and ffmpeg with Flite are not both installed")
def test_the_installed_engine_dictates_synthetic_speech_into_a_draft_and_posts_nothing(library):
    desk, _chat = library
    with Device(desk, runtime=False) as device:
        engine = PocketSphinxBackend()
        voice.configure(device.api, (engine,))
        api = AppApi(device.api)
        before = _files(desk)
        status = _on(api)
        answer = api.call("dictation.hear", {"audio": base64.b64encode(synthesize("we go to the west gate", "kal")).decode(),
                                             "sequence": status["next_sequence"], "capture_token": status["capture_token"],
                                             "draft": "", "names": NAMES})
        # open dictation of synthetic speech is rough (docs/SPEECH.md): what matters is that the words land in the
        # draft as heard, and nothing else happens
        assert answer["status"] == "ok" and answer["heard"]
        assert answer["revision"]["kind"] == "added"
        assert answer["revision"]["text"].lower() == answer["heard"].lower()
        assert _files(desk) == before
        engine.unload()
