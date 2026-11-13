"""No microphone: attribution is supplied by a trusted capture boundary."""
from dataclasses import replace

import pytest

from grimoire.intent import RoutingContext, Utterance
from grimoire.speech.control import Recognition, SpeechControls, route_recognition
from speech_helpers import policy, SyntheticBackend, perform
from grimoire.speech import Policy, Selection, SpeechSession

CTX = RoutingContext("pc-1", True)


def captured(s, *args, **kwargs):
    return event(*args, capture_token=s.capture_token, **kwargs)


def event(text="open inventory", confidence=0.9, **changes):
    return Recognition(Utterance(text, origin="voice", confidence=confidence),
                       **(dict(attribution="foreground", engaged=True, final=True) | changes))


@pytest.mark.parametrize("changes", [dict(attribution="background"), dict(attribution="unknown"),
                                       dict(overlap=True), dict(final=False), dict(engaged=False)])
@pytest.mark.parametrize("text", ["delete scene", "yes", "pause", "open inventory", "I wave"])
def test_untrusted_channels_never_reach_intent(text, changes):
    result = route_recognition(event(text, **changes), CTX)
    assert result.status == "blocked" and result.intent is None


def test_recognition_metadata_cannot_coerce_keyboard_or_truthy_flags():
    with pytest.raises(ValueError):
        Recognition(Utterance("delete scene"), "foreground", True, False, True)
    for field in ("engaged", "overlap", "final"):
        with pytest.raises(ValueError):
            event(**{field: 1})


def test_quiet_speaker_can_review_confirm_once_and_return_to_keyboard():
    c = SpeechControls(CTX)
    result = c.accept(event(confidence=0.3))
    assert result.intent.kind == "uncertain"
    assert c.pending.command.argument == "inventory"
    assert c.confirm(event("yes", proposal_token=c.pending.token)).command.argument == "inventory"
    assert c.confirm(event("yes")) is None
    c.accept(event(confidence=0.2))
    typed = c.keyboard(Utterance('"hello"'))
    assert typed.kind == "pc_speech" and typed.actor_id == "pc-1"
    assert c.pending is None


@pytest.mark.parametrize("answer", [event("yes", attribution="background"), event("yes", overlap=True),
                                     event("yes", confidence=0.2), event("yes?"), event("no")])
def test_confirmation_is_foreground_fresh_and_unambiguous(answer):
    c = SpeechControls(CTX)
    c.accept(event(confidence=0.3))
    assert c.confirm(replace(answer, proposal_token=c.pending.token)) is None
    assert c.confirm(Utterance("yes")) is None


@pytest.mark.parametrize("text", ["delete scene", "reroll", "I walk"])
def test_safe_confirmation_cannot_promote_authorized_or_destructive_or_pc_intents(text):
    c = SpeechControls(CTX)
    c.accept(event(text, confidence=0.3))
    assert c.pending is None
    assert c.confirm(Utterance("yes")) is None


def test_multiple_command_candidates_require_explicit_selection():
    c = SpeechControls(CTX)
    c.accept(Recognition(Utterance("open inventory", origin="voice", confidence=.9,
                                   alternatives=("open map",)), "foreground", True, False, True))
    assert c.pending is None


@pytest.mark.parametrize("change", ["off", "provider", "close", "context", "recover", "blocked"])
def test_session_invalidates_old_pending_on_policy_and_mode_recovery(change):
    s = SpeechSession((SyntheticBackend(),), policy(), CTX)
    s.accept(captured(s, confidence=.2))
    assert s.pending is not None
    token = s.pending.token
    if change == "off":
        s.configure(Policy())
    elif change == "provider":
        s.configure(policy(stt=Selection("provider", "synthetic")))
    elif change == "close":
        s.close()
    elif change == "context":
        s.configure_context(RoutingContext("pc-2", False))
    elif change == "recover":
        s.recover()
    else:
        s.accept(captured(s, attribution="background"))
    assert s.pending is None
    assert s.confirm(Utterance("yes"), token=token) is None
    assert s.keyboard(Utterance("pause")).command.name == "pause"


def test_voice_controls_unavailable_but_keyboard_available_without_recording():
    s = SpeechSession()
    assert s.accept(captured(s, )).status == "blocked"
    assert s.keyboard(Utterance("open inventory")).command.argument == "inventory"


def test_old_affirmative_cannot_confirm_a_new_proposal():
    c = SpeechControls(CTX)
    c.accept(event(confidence=.2))
    old_yes = event("yes", proposal_token=c.pending.token)
    assert c.confirm(old_yes).command.argument == "inventory"
    c.accept(event("open map", confidence=.2))
    assert c.confirm(old_yes) is None


def test_keyboard_confirmation_requires_reviewed_proposal_token():
    c = SpeechControls(CTX)
    c.accept(event(confidence=.2))
    token = c.pending.token
    assert c.confirm(Utterance("yes"), token=token).command.argument == "inventory"
    c.accept(event("open map", confidence=.2))
    assert c.confirm(Utterance("yes"), token=token) is None


def test_replacement_stt_failure_and_draft_correction_invalidate_pending():
    class Broken(SyntheticBackend):
        def transcribe(self, audio):
            raise RuntimeError("synthetic failure")
    s = SpeechSession((Broken(),), policy(), CTX)
    s.accept(captured(s, confidence=.2))
    token = s.pending.token
    assert perform(s, 1, "stt", b"replacement").status == "backend_failed"
    assert s.confirm(Utterance("yes"), token=token) is None
    s.accept(captured(s, confidence=.2))
    token = s.pending.token
    s.correct_draft("open map")
    assert s.confirm(Utterance("yes"), token=token) is None


def test_transcription_to_review_to_bound_confirmation_is_usable():
    class Replies(SyntheticBackend):
        def transcribe(self, audio):
            self.calls.append(audio)
            return Utterance("yes" if audio == b"confirmation" else "open inventory",
                             origin="voice", confidence=.9 if audio == b"confirmation" else .2)
    b = Replies()
    s = SpeechSession((b,), policy(), CTX)
    first = perform(s, 1, "stt", b"ordinary")
    s.accept(Recognition(first.transcript, "foreground", True, False, True, capture_token=first.capture_token))
    token = s.pending.token
    second = perform(s, 2, "stt", b"confirmation", confirmation_token=token)
    assert second.confirmation_token == token and s.pending is not None
    answer = Recognition(second.transcript, "foreground", True, False, True,
                         proposal_token=second.confirmation_token, capture_token=second.capture_token)
    assert s.confirm(answer).command.argument == "inventory"
    s.accept(captured(s, "open map", confidence=.2))
    assert s.confirm(answer) is None
    stale = perform(s, 3, "stt", b"confirmation", confirmation_token=token)
    assert stale.status == "stale_confirmation" and len(b.calls) == 2


def test_failed_bound_confirmation_invalidates_proposal():
    class Broken(SyntheticBackend):
        def transcribe(self, audio):
            raise RuntimeError("synthetic failure")
    s = SpeechSession((Broken(),), policy(), CTX)
    s.accept(captured(s, confidence=.2))
    token = s.pending.token
    assert perform(s, 1, "stt", b"yes", confirmation_token=token).status == "backend_failed"
    assert s.pending is None


def test_old_transcript_cannot_become_a_new_actors_action():
    class Action(SyntheticBackend):
        def transcribe(self, audio):
            return Utterance("I open the chest", origin="voice", confidence=.9)
    s = SpeechSession((Action(),), policy(), CTX)
    captured_token = s.capture_token
    result = s.perform(1, "stt", b"input", capture_token=captured_token)
    old = Recognition(result.transcript, "foreground", True, False, True, capture_token=result.capture_token)
    s.configure_context(RoutingContext("pc-2", True))
    assert s.accept(old).status == "blocked"
    assert s.accept(old).reason == "stale_capture"


@pytest.mark.parametrize("change", ["policy", "context", "delete", "recover", "close"])
def test_generation_change_rejects_old_audio_before_backend(change):
    b = SyntheticBackend()
    s = SpeechSession((b,), policy(), CTX)
    old = s.capture_token
    if change == "policy":
        s.configure(policy())
    elif change == "context":
        s.configure_context(RoutingContext("pc-2", True))
    else:
        getattr(s, change)()
    assert s.perform(1, "stt", b"old capture", capture_token=old).status == "stale_capture"
    assert b.calls == []
    assert s.accept(event(capture_token=old)).status == "blocked"


def test_missing_capture_binding_cannot_upload_route_or_confirm():
    b = SyntheticBackend()
    s = SpeechSession((b,), policy(), CTX)
    assert s.perform(1, "stt", b"unbound").status == "stale_capture"
    assert b.calls == []
    assert s.accept(event()).status == "blocked"
    s.accept(event(confidence=.2, capture_token=s.capture_token))
    assert s.confirm(event("yes", proposal_token=s.pending.token)) is None
