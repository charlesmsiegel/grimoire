"""067's adapter ops: voice as an optional assistive control. Called as ``speech.<op>``.

One ``SpeechSession`` serves each open library while it is open (docs/SPEECH.md). By default it holds only
the ``unsupported`` backend with recording off, so every op answers truthfully that voice is unavailable and
typing is unaffected. An embedding application (061's private-voice settings, or a test) installs a trusted
local backend and policy with ``configure``. No op opens a microphone: the page captures one bounded window
after an explicit gesture and sends its audio here; nothing executes a command, posts or deletes. Ops answer
what the page may do (``act``, ``draft``, ``review``, ``show``), and the catalog's gates stay with the buttons.
"""
from __future__ import annotations

import base64
import binascii
import threading
import weakref
from collections.abc import Mapping

from grimoire.app.adapter import Adapter, AdapterContext
from grimoire.intent import RoutingContext, Utterance
from grimoire.speech import Policy, SpeechSession
from grimoire.speech.capture import MAX_WINDOW, TRIGGERS, Capture, hear, voice_action
from grimoire.speech.model import MAX_AUDIO, MAX_SEQUENCE, MAX_TEXT
from grimoire.textapi import ApiRefused, TextApi

_served: weakref.WeakKeyDictionary[TextApi, SpeechSession] = weakref.WeakKeyDictionary()
_counters: weakref.WeakKeyDictionary[SpeechSession, list[int]] = weakref.WeakKeyDictionary()
_guard = threading.Lock()
# What is not a command is the person's own words for the composer, which decides who speaks when they post:
# the router's actor here only names "the composer", never a campaign actor.
CONTEXT = RoutingContext("composer", True)


def configure(api: TextApi, backends: tuple = (), policy: Policy = Policy(),
              context: RoutingContext = CONTEXT) -> SpeechSession:
    """Install the speech session for an open library: trusted configuration, never a request argument."""
    with _guard:
        old = _served.get(api)
        if old is not None:
            old.close()
        session = _served[api] = SpeechSession(tuple(backends), policy, context)
        return session


def session_of(api: TextApi) -> SpeechSession:
    with _guard:
        found = _served.get(api)
        if found is None:
            found = _served[api] = SpeechSession(context=CONTEXT)
        return found


def _refuse(reason: str, detail: str) -> ApiRefused:
    return ApiRefused(reason, detail)


def _sequence(session: SpeechSession) -> int:
    """The session's next invocation id. The app numbers its own calls: nothing on the server acts on a
    result, so a client retry may simply be recognized again, and a reloaded page never meets an old mark."""
    with _guard:
        counter = _counters.setdefault(session, [0])
        if counter[0] >= MAX_SEQUENCE:
            raise _refuse("too_large", "this speech session has run out of call numbers; reopen the library")
        counter[0] += 1
        return counter[0]


def _token(args: Mapping[str, object], key: str, required: bool = True) -> str | None:
    value = args.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value or len(value) > 128:
        raise _refuse("invalid_field", f"{key} is required")
    return value


def _command(intent) -> dict | None:
    if intent is None or intent.command is None:
        return None
    return {"name": intent.command.name, "argument": intent.command.argument, "gate": intent.command.gate}


def _pending(session: SpeechSession) -> dict | None:
    pending = session.pending
    if pending is None:
        return None
    return {"token": pending.token, "command": _command(pending.intent), "text": pending.intent.text}


def _disclosure(session: SpeechSession, feature: str) -> dict:
    d = session.describe(feature)
    return {"status": d.status, "backend": d.backend, "input": d.input_kind, "locality": d.locality,
            "network": d.network, "retains_inputs": d.retains_inputs, "billing": d.billing, "fallback": d.fallback}


def status(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    session = session_of(ctx.api)
    return {"stt": _disclosure(session, "stt"), "tts": _disclosure(session, "tts"),
            "capture_token": session.capture_token, "pending": _pending(session),
            "triggers": sorted(TRIGGERS), "max_window": MAX_WINDOW}


def listen(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """One gesture-opened window's audio (base64 WAV): analyzed, recognized only when attributable, routed."""
    session = session_of(ctx.api)
    encoded = args.get("audio")
    if not isinstance(encoded, str) or not encoded or len(encoded) > (MAX_AUDIO * 4) // 3 + 4:
        raise _refuse("invalid_field", "audio must be base64 WAV within the size limit")
    try:
        audio = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise _refuse("invalid_field", "audio is not base64") from error
    trigger = args.get("trigger")
    if trigger not in TRIGGERS:
        raise _refuse("invalid_field", "a listening window needs an explicit gesture")
    try:
        capture = Capture(audio, trigger, _token(args, "capture_token"), _token(args, "proposal_token", False))
        heard = hear(session, _sequence(session), capture)
    except ValueError as error:
        raise _refuse("invalid_field", "invalid audio window") from error
    if heard.status in ("confirmed", "not_confirmed"):
        action = "act" if heard.intent is not None else "none"
    elif heard.status == "routed":
        action = voice_action(heard.intent)
        if action == "show" and session.pending is not None:
            action = "review"
    else:
        action = "none"
    return {"status": heard.status, "reason": heard.reason, "caption": heard.transcript, "action": action,
            "command": _command(heard.intent) if action in ("act", "show") else None,
            "draft": heard.transcript if action == "draft" else None,
            "feedback": heard.intent.feedback if heard.intent is not None else None,
            "pending": _pending(session), "capture_token": session.capture_token}


def confirm(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """The reviewed proposal's Yes or No control (keyboard or pointer): never a transcript."""
    session = session_of(ctx.api)
    answer = args.get("answer")
    if answer not in ("yes", "no"):
        raise _refuse("invalid_field", "answer is yes or no")
    intent = session.confirm(Utterance(answer), token=_token(args, "token"))
    return {"action": "act" if intent is not None else "none", "command": _command(intent),
            "pending": _pending(session), "capture_token": session.capture_token}


def recover(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Back to typing: drop any proposal and session audio/transcripts; typing never needed voice."""
    session = session_of(ctx.api)
    session.recover()
    return status(ctx, args)


def speak(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Read a line aloud through the configured TTS route, or say why not; the text stays the caption."""
    session = session_of(ctx.api)
    text = args.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise _refuse("invalid_field", "text to read is required")
    result = session.perform(_sequence(session), "tts", text)
    return {"status": result.status, "caption": text,
            "audio": base64.b64encode(result.audio).decode("ascii") if result.audio is not None else None}


ADAPTER = Adapter("speech", owner="067", ops={"status": status, "listen": listen, "confirm": confirm,
                                              "recover": recover, "speak": speak})

__all__ = ["ADAPTER", "CONTEXT", "configure", "session_of"]
