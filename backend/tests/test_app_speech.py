"""067's ``speech.*`` app ops over a synthetic library: an unconfigured app says voice is unavailable and
typing is untouched; with the test-owned offline recognizer installed, one gesture-opened window of synthetic
audio is analyzed, recognized only when attributable, and answered as what the page may do. Nothing posts,
deletes or writes the library.
"""
from __future__ import annotations

import base64

import pytest
from textapi_fixtures import Device, desktop_library

import speech_audio_corpus as corpus
from grimoire.app.adapters import speech as adapter
from grimoire.app.appapi import AppApi
from grimoire.speech import Policy, Selection
from grimoire.speech import acoustic
from grimoire.textapi import ApiRefused
from sphinx_recognizer import SphinxRecognizer


@pytest.fixture
def app(tmp_path):
    desk, chat = desktop_library(tmp_path / "desk")
    with Device(desk) as opened:
        yield AppApi(opened.api), opened.api, desk


def _wav(pcm) -> str:
    return base64.b64encode(acoustic.write_wav(pcm)).decode("ascii")


def _listen(api, status, pcm, _step=None, **extra):
    return api.call("speech.listen", {"audio": _wav(pcm), "trigger": "keyboard",
                                      "capture_token": status["capture_token"], **extra})


def _tree(folder):
    return sorted((p.relative_to(folder).as_posix(), p.read_bytes()) for p in folder.rglob("*") if p.is_file())


def test_unconfigured_voice_is_truthfully_unavailable_and_typing_is_untouched(app):
    api, _text, desk = app
    assert {"speech.status", "speech.listen", "speech.confirm", "speech.recover", "speech.speak"} <= set(api.ops)
    before = _tree(desk)
    status = api.call("speech.status", {})
    assert status["stt"]["status"] == "recording_off" and status["tts"]["status"] == "off"
    assert status["stt"]["fallback"] == "text" and status["triggers"] == ["keyboard", "pointer", "switch"]
    heard = _listen(api, status, corpus.window(corpus.clip("us1-pause")), 1)
    assert (heard["status"], heard["reason"], heard["action"], heard["caption"]) == ("refused", "recording_off", "none", None)
    spoken = api.call("speech.speak", {"text": "The tide is out."})
    assert spoken == {"status": "off", "caption": "The tide is out.", "audio": None}
    assert _tree(desk) == before


def test_one_window_answers_act_review_draft_or_show_and_never_executes(app):
    api, text, desk = app
    adapter.configure(text, (SphinxRecognizer(),),
                      Policy(stt=Selection("local_only", "pocketsphinx-eval"), recording=True),
                      adapter.RoutingContext("composer", True, min_confidence=corpus.CONTEXT.min_confidence))
    before = _tree(desk)
    status = api.call("speech.status", {})
    assert status["stt"]["status"] == "ready" and status["stt"]["network"] is False
    paused = _listen(api, status, corpus.window(corpus.clip("us3-pause")), 1)
    assert paused["action"] == "act" and paused["command"] == {"name": "pause", "argument": None, "gate": "instant"}
    assert paused["caption"] == "pause"
    deleted = _listen(api, status, corpus.window(corpus.clip("us3-delete")), 2)
    assert deleted["action"] == "show" and deleted["command"]["gate"] == "confirm"
    background = _listen(api, status, acoustic.pad(acoustic.far(corpus.clip("bg-command")), 0, 0.6), 3)
    assert (background["status"], background["reason"], background["caption"]) == ("blocked", "speech_at_open", None)
    quiet = acoustic.noise(corpus.window(acoustic.gain(corpus.clip("us2-open"), -24)), 8, seed=40)
    proposed = _listen(api, status, quiet, 4)
    assert proposed["action"] == "review" and proposed["pending"]["command"]["name"] == "navigate"
    token = proposed["pending"]["token"]
    assert api.call("speech.confirm", {"answer": "yes", "token": "0" * 32})["action"] == "none"
    proposed = _listen(api, status, quiet, 5)
    confirmed = api.call("speech.confirm", {"answer": "yes", "token": proposed["pending"]["token"]})
    assert confirmed["action"] == "act" and confirmed["command"]["argument"] == "inventory" and token
    # A second window answering by voice.
    proposed = _listen(api, status, quiet, 6)
    yes = acoustic.noise(corpus.window(acoustic.gain(corpus.clip("us2-yes"), -24)), 8, seed=41)
    answered = _listen(api, status, yes, 7, proposal_token=proposed["pending"]["token"])
    assert answered["status"] == "confirmed" and answered["action"] == "act"
    # A low-confidence miss is only a caption (the page offers it as a draft on request); nothing acts.
    missed = _listen(api, status, corpus.window(corpus.clip("us1-disfluent")))
    assert (missed["action"], missed["command"], missed["draft"]) == ("show", None, None) and missed["caption"]
    recovered = api.call("speech.recover", {})
    assert recovered["pending"] is None and recovered["capture_token"] != status["capture_token"]
    assert _listen(api, status, quiet)["reason"] == "stale_capture"  # a window opened before recovery
    assert _tree(desk) == before


@pytest.mark.parametrize("args", [
    {"audio": "", "trigger": "keyboard", "capture_token": "t"},
    {"audio": "not base64!", "trigger": "keyboard", "capture_token": "t"},
    {"audio": "UklGRg==", "trigger": "always_on", "capture_token": "t"},
    {"audio": "UklGRg==", "trigger": "keyboard"},
])
def test_malformed_windows_are_refused(app, args):
    api, _text, _desk = app
    with pytest.raises(ApiRefused) as refused:
        api.call("speech.listen", args)
    assert refused.value.status == 400
    with pytest.raises(ApiRefused):
        api.call("speech.confirm", {"answer": "maybe", "token": "t"})
    with pytest.raises(ApiRefused):
        api.call("speech.speak", {"text": ""})
