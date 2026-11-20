"""Sprint 061's adapter ops: private, local voice input. Called as ``voice.<op>``; see docs/SPEECH.md.

Only Sprint 061 edits this module. One ``SpeechSession`` per open library, in memory only: every launch starts
with voice off, the microphone off and nothing kept. The choices are speech input off or *on this device only*;
no online speech service is offered, and none is switched to when the local recognizer is missing, slow or
fails. Audio reaches this op from the app's own page over the loopback server (it never leaves the device),
is recognized in this process by the trusted local adapter, and is kept only if the person chose to keep it,
for this launch. The transcript is returned to the page for the person to read and use; no op here posts it,
writes the library or runs a command.

Every op here touches no library state, so all run without the library's writer lock (``Adapter.unlocked``):
a recognition, however long, never stalls typed play. ``VoiceState.lock`` serializes them among themselves.

The ops: ``status`` (every disclosure, the choices, what is kept now, the recognizer's measured cost),
``configure`` (choose), ``transcribe`` (one recording), ``delete`` (forget what is kept) and ``unload`` (free
the recognizer's memory).
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from threading import Lock

from grimoire.app.adapter import Adapter, AdapterContext
from grimoire.speech import Policy, Selection, SpeechSession
from grimoire.speech.model import MAX_AUDIO, MAX_SEQUENCE
from grimoire.speech.sphinx import MAX_SECONDS, AudioRefused, PocketSphinxBackend, pcm
from grimoire.textapi import ApiRefused, TextApi

#: Where a text API holds its voice state: on the text API itself, living exactly as long as it does.
_ATTRIBUTE = "_voice_state"
#: What the page may keep, for this launch only: the last transcript. Recordings are never kept (no op could
#: give one back, so keeping one would only be a copy of private speech), nor synthesized audio (there is none).
KEEPABLE = ("provisional_transcript",)
MODES = ("off", "local_only")
_ABSENT = object()
#: Guards making a library's voice state. Not the library's lock: voice ops never take that one.
_MADE = Lock()


class VoiceState:
    """One library's speech session and its local recognizers."""

    def __init__(self, backends: tuple | None = None) -> None:
        self.backends = (PocketSphinxBackend(),) if backends is None else backends
        self.session = SpeechSession(self.backends)
        self.lock = Lock()

    def backend(self, backend_id: str | None):  # type: ignore[no-untyped-def]
        return next((backend for backend in self.backends if backend.info.id == backend_id), None)


def configure(api: TextApi, backends: tuple) -> VoiceState:
    """Give ``api``'s library these local recognizers instead of the installed ones (tests, other engines).
    Called once, before the app serves ``api``."""
    with _MADE:
        if getattr(api, _ATTRIBUTE, None) is not None:
            raise ValueError("this library's voice is already configured")
        made = VoiceState(backends)
        setattr(api, _ATTRIBUTE, made)
        return made


def state(api: TextApi) -> VoiceState:
    with _MADE:
        found = getattr(api, _ATTRIBUTE, None)
        if found is None:
            found = VoiceState()
            setattr(api, _ATTRIBUTE, found)
        return found


def _measured(backend) -> dict | None:  # type: ignore[no-untyped-def]
    measured = getattr(backend, "measurements", None)
    if measured is None:
        return None
    return {"loaded": measured.loaded, "load_seconds": measured.load_seconds, "load_memory": measured.load_memory,
            "recognitions": measured.recognitions, "last_audio_seconds": measured.last_audio_seconds,
            "last_seconds": measured.last_seconds, "last_real_time_factor": measured.last_real_time_factor}


def _status(voice: VoiceState) -> dict:
    session = voice.session
    policy = session.policy
    disclosures = {}
    for feature in ("stt", "tts"):
        told = session.describe(feature)
        disclosures[feature] = {"status": told.status, "backend": told.backend, "input": told.input_kind,
                                "locality": told.locality, "network": told.network,
                                "retains_inputs": told.retains_inputs, "billing": told.billing,
                                "fallback": told.fallback}
    selected = voice.backend(policy.stt.backend_id)
    ready = disclosures["stt"]["status"] == "ready"
    return {
        "stt": disclosures["stt"],
        "tts": disclosures["tts"],
        "mode": policy.stt.mode,
        "recording": policy.recording,
        "retain": sorted(policy.retain & set(KEEPABLE)),
        "kept": sorted(session.export().keys()),
        "backends": [{"id": backend.info.id, "features": sorted(backend.info.features), "locality": backend.info.locality,
                      "network": backend.info.network, "retains_inputs": backend.info.retains_inputs,
                      "billing": backend.info.billing, "available": backend.info.available,
                      "max_seconds": getattr(backend, "max_seconds", None), "measurements": _measured(backend)}
                     for backend in voice.backends],
        "selected": selected.info.id if selected is not None else None,
        # Only a ready route gets a capture token and a sequence: the page has nothing to record into otherwise.
        "capture_token": session.capture_token if ready else None,
        "next_sequence": session.sequence + 1 if ready else None,
        "limits": {"max_seconds": MAX_SECONDS, "max_audio_bytes": MAX_AUDIO},
        "keepable": list(KEEPABLE),
    }


def _bool(args: Mapping[str, object], key: str, default: bool) -> bool:
    value = args.get(key, _ABSENT)
    if value is _ABSENT:
        return default
    if type(value) is not bool:
        raise ApiRefused("invalid_field", f"{key} must be true or false")
    return value


def status(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    voice = state(ctx.api)
    with voice.lock:
        return _status(voice)


def configure_op(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Choose: ``mode`` ("off" or "local_only"), ``backend`` (a local recognizer's id), ``recording`` (the
    microphone), ``retain`` (what to keep for this launch) and ``max_seconds`` (the longest recording). Any
    change forgets whatever was kept and invalidates a recording in progress."""
    voice = state(ctx.api)
    with voice.lock:
        policy = voice.session.policy
        mode = args.get("mode", policy.stt.mode)
        if mode not in MODES:
            raise ApiRefused("invalid_field", "mode must be off or local_only: no online speech service is offered", 400)
        backend_id = args.get("backend", policy.stt.backend_id)
        if backend_id is not None and (type(backend_id) is not str or voice.backend(backend_id) is None):
            raise ApiRefused("invalid_field", "backend must name one of this device's recognizers", 400)
        if mode == "local_only" and backend_id is None:
            raise ApiRefused("invalid_field", "choose which recognizer runs on this device", 400)
        recording = _bool(args, "recording", policy.recording)
        retain = args.get("retain", _ABSENT)
        if retain is _ABSENT:
            kept = policy.retain
        elif not isinstance(retain, list) or not all(type(item) is str and item in KEEPABLE for item in retain):
            raise ApiRefused("invalid_field", "retain lists what to keep: " + ", ".join(KEEPABLE), 400)
        else:
            kept = frozenset(retain)
        limit = args.get("max_seconds", _ABSENT)
        backend = voice.backend(backend_id)
        if limit is not _ABSENT:
            if type(limit) is not int or not 1 <= limit <= MAX_SECONDS:
                raise ApiRefused("invalid_field", f"max_seconds must be a whole number from 1 to {MAX_SECONDS}", 400)
            if backend is None or not hasattr(backend, "max_seconds"):
                raise ApiRefused("invalid_field", "choose a recognizer before setting its recording limit", 400)
        selection = Selection("local_only", backend_id) if mode == "local_only" else Selection("off", backend_id)
        voice.session.configure(Policy(stt=selection, recording=recording, retain=kept))
        if limit is not _ABSENT:
            backend.max_seconds = limit
        if mode == "off":
            # Off frees the recognizer's memory too: nothing about voice stays loaded on a device not using it.
            for each in voice.backends:
                if hasattr(each, "unload"):
                    each.unload()
        return _status(voice)


def transcribe(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Recognize one recording: ``audio`` (base64 WAV, 16-bit mono 16 kHz), ``sequence`` (status's
    ``next_sequence``, increasing) and ``capture_token`` (the one status gave when recording started). Answers
    ``status`` ("ok" with ``text``, or why not: recording_off, off, unavailable, stale_capture,
    already_processed, backend_failed...) and the recognizer's measured cost. Nothing is posted."""
    voice = state(ctx.api)
    sequence = args.get("sequence")
    if type(sequence) is not int or not 1 <= sequence <= MAX_SEQUENCE:
        raise ApiRefused("invalid_field", "sequence must be status's next_sequence", 400)
    capture = args.get("capture_token")
    if capture is not None and type(capture) is not str:
        raise ApiRefused("invalid_field", "capture_token must be text", 400)
    encoded = args.get("audio")
    if type(encoded) is not str or len(encoded) > (MAX_AUDIO * 4) // 3 + 4:
        raise ApiRefused("invalid_field", "audio must be a base64 WAV of at most 4 MiB", 400)
    try:
        audio = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ApiRefused("invalid_field", "audio must be base64", 400) from None
    if not 0 < len(audio) <= MAX_AUDIO:
        raise ApiRefused("invalid_field", "audio must be a WAV of at most 4 MiB", 400)
    with voice.lock:
        session = voice.session
        selected = voice.backend(session.policy.stt.backend_id)
        told = session.describe("stt").status
        if told != "ready" or selected is None:
            # Not ready (off, microphone off, not installed...): the recognizer is not touched and nothing is kept.
            return {"status": told, "next_sequence": None, "measurements": None, "kept": sorted(session.export().keys())}
        try:
            pcm(audio, getattr(selected, "max_seconds", MAX_SECONDS))
        except AudioRefused as refused:
            raise ApiRefused(refused.reason, str(refused), 400) from None
        result = session.perform(sequence, "stt", audio, capture_token=capture)
        answer = {"status": result.status, "next_sequence": session.sequence + 1, "measurements": _measured(selected)}
        if result.status == "ok" and result.transcript is not None:
            answer["text"] = result.transcript.text
            answer["confidence"] = result.transcript.confidence
        answer["kept"] = sorted(session.export().keys())
        return answer


def delete(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Forget every recording and transcript kept for this launch, and invalidate a recording in progress."""
    voice = state(ctx.api)
    with voice.lock:
        voice.session.delete()
        return _status(voice)


def unload(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Free the local recognizer's memory; the next recording loads it again (and is measured doing so)."""
    voice = state(ctx.api)
    with voice.lock:
        for backend in voice.backends:
            if hasattr(backend, "unload"):
                backend.unload()
        return _status(voice)


OPS = {"status": status, "configure": configure_op, "transcribe": transcribe, "delete": delete, "unload": unload}
ADAPTER = Adapter("voice", owner="061", ops=OPS, unlocked=frozenset(OPS))

__all__ = ["ADAPTER", "KEEPABLE", "MODES", "VoiceState", "configure", "state"]
