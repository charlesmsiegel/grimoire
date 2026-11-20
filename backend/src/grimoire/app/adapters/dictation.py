"""Sprint 080's adapter ops: speech to draft. Called as ``dictation.<op>``; see docs/SPEECH.md.

Only Sprint 080 edits this module. Dictation records through the same private, on-device route as 061's Voice
page (``voice.*``): the same session, capture token, sequence and recognizer, chosen only in Settings (Voice), so
there is still no online speech service and nothing new to consent to. What it adds is where the words go: into
the chat composer's unsent draft, revised by ``grimoire.speech.dictation`` (append, a spoken correction, "scratch
that"). The page sends the draft and gets the new draft back; no op here posts, reads or writes the library, or
runs a command, so every op runs without the library's writer lock and never holds up typed play.

The ops: ``status`` (can dictation record now, and if not why not; the language), ``hear`` (one recording into a
revised draft) and ``revise`` (the same words against a draft the player changed while they were recognized).
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.app.adapter import Adapter, AdapterContext
from grimoire.app.adapters import voice
from grimoire.speech.dictation import MAX_DRAFT, MAX_NAME, MAX_NAMES, revise
from grimoire.speech.model import MAX_TEXT
from grimoire.textapi import ApiRefused

#: The bundled recognizer's one model. Choosing another language needs another model; until one is installed
#: the page says which language it listens for rather than offering a choice it cannot keep.
LANGUAGE = {"tag": "en-US", "name": "English (US)", "choices": ["en-US"]}
_ABSENT = object()


def _text(args: Mapping[str, object], name: str, limit: int, *, optional: bool = False) -> str | None:
    value = args.get(name, _ABSENT)
    if optional and value in (_ABSENT, None):
        return None
    if type(value) is not str or len(value) > limit:
        raise ApiRefused("invalid_field", f"{name} must be text of at most {limit} characters", 400)
    return value


def _draft(args: Mapping[str, object]) -> tuple[str, str | None, tuple[str, ...]]:
    draft = _text(args, "draft", MAX_DRAFT)
    last = _text(args, "last", MAX_DRAFT, optional=True)
    names = args.get("names", [])
    if not isinstance(names, list) or len(names) > MAX_NAMES \
            or not all(type(name) is str and 0 < len(name) <= MAX_NAME for name in names):
        raise ApiRefused("invalid_field", f"names lists at most {MAX_NAMES} names of the scene", 400)
    return draft, last, tuple(names)  # type: ignore[return-value]


def _why(status: str, mode: str) -> str:
    """061's speech-input status, except that voice input switched off is ``off`` whatever the microphone is (the
    session reports the microphone first, which would send the player to the wrong switch)."""
    return "off" if mode == "off" and status != "ready" else status


def status(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Whether dictation can record now. ``status`` is 061's speech-input status (``ready``, ``off``,
    ``recording_off``, ``unavailable``...); only when ready do ``capture_token`` and ``next_sequence`` come too."""
    told = voice.status(ctx, {})
    selected = next((backend for backend in told["backends"] if backend["id"] == told["selected"]), None)
    return {"status": _why(told["stt"]["status"], told["mode"]), "capture_token": told["capture_token"],
            "next_sequence": told["next_sequence"],
            "max_seconds": selected["max_seconds"] if selected is not None else None,
            "language": LANGUAGE, "kept": told["kept"]}


def hear(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """One recording into the draft: ``audio``, ``sequence`` and ``capture_token`` exactly as ``voice.transcribe``
    takes them (the token latched when recording started), with ``draft`` (the composer's text), ``last`` (the
    passage last dictated into it, or none) and ``names`` (the scene's, for suggestions). Answers ``status``
    (``ok``, or why nothing was heard), ``heard`` (verbatim), ``confidence`` and ``revision``. Nothing is posted:
    the page puts ``revision.text`` in the composer for the player to read, edit and post themselves."""
    draft, last, names = _draft(args)
    heard = voice.transcribe(ctx, {key: args.get(key) for key in ("audio", "sequence", "capture_token")})
    mode = voice.state(ctx.api).session.policy.stt.mode
    answer = {"status": _why(heard["status"], mode), "next_sequence": heard["next_sequence"], "kept": heard["kept"],
              "heard": None, "confidence": None, "revision": None}
    if heard["status"] == "ok":
        text = heard.get("text") or ""
        answer["heard"] = text
        answer["confidence"] = heard.get("confidence")
        answer["revision"] = revise(draft, last, text, confidence=heard.get("confidence"), names=names).as_dict()
    return answer


def revise_op(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """The words already heard (``heard``, with its ``confidence``) against the draft as it is now: for a
    composer the player typed in while their recording was being recognized."""
    draft, last, names = _draft(args)
    heard = _text(args, "heard", MAX_TEXT)
    confidence = args.get("confidence")
    if confidence is not None and (type(confidence) not in (int, float) or not 0 <= confidence <= 1):
        raise ApiRefused("invalid_field", "confidence must be a number from 0 to 1", 400)
    return {"revision": revise(draft, last, heard, confidence=confidence, names=names).as_dict()}  # type: ignore[arg-type]


OPS = {"status": status, "hear": hear, "revise": revise_op}
ADAPTER = Adapter("dictation", owner="080", ops=OPS, unlocked=frozenset(OPS))

__all__ = ["ADAPTER", "LANGUAGE"]
