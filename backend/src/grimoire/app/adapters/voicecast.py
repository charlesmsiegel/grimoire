"""Sprint 083's adapter ops: character voice performance. Called as ``voicecast.<op>``; see docs/VOICE_CASTING.md.

The casting (who speaks with which local voice) is ``grimoire.voicecaststore``'s file in the open library's own
``config`` folder. A chat's performance is planned from the turns opening the chat shows (the runner's own read,
which may note 020's pending knowledge as any open does), never from a draft, a proposal or a private message. No
op here writes a turn or a source: the accepted text stays exactly as it is, and audio is only ever produced from
it. A forked chat (two devices' turns from one number on) is refused ``turn_fork``: its branches are not one
scene.

Synthesis is local only: the engines are trusted configuration (by default ``ffmpeg``'s Flite voices when this
``ffmpeg`` has them), never chosen by a request, and nothing is sent anywhere, billed or kept. Audio is answered as
base64 WAV per cue, with its start and length, for the page to play as one scene. With no engine, or ``audio``
false, the same op answers the cues as text.

The ops: ``read`` (the casting, each engine and its voices), ``cast`` and ``uncast`` (change one target's voice),
``plan`` (a chat's cues, each with its voice and why) and ``render`` (a window of cues as timed audio).
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable, Mapping
from threading import Lock

from grimoire.app.adapter import Adapter, AdapterContext, Handler
from grimoire.core.entity_codec import CodecError, instance_from_payload
from grimoire.core.identity import ChatRef, InstanceRef, SourceRef
from grimoire.play.adjudicate import outcome_lines
from grimoire.scene import SceneRefused
from grimoire.store import RecordNotFound, StoreError
from grimoire.textapi import ApiRefused, TextApi
from grimoire.voicecast import (
    RIGHTS, TARGETS, CastEntry, Casting, CastingError, Cue, SpeechVoice, explain, plan_performance,
    render_performance,
)
from grimoire.voiceengines.flite import FliteSynthesizer
from grimoire.voicecaststore import CastingStore

#: How many cues one ``render`` covers by default, and at most: synthesis runs under the library's lock, so one
#: call stays short and the page asks for the next window while the first plays.
DEFAULT_WINDOW = 6
MAX_WINDOW = 12
#: Synthesis stops starting new cues after this many seconds of one render (at least one cue always renders):
#: an engine that hangs holds the library for at most this plus one cue's own timeout.
RENDER_BUDGET = 20.0
_ATTRIBUTE = "_voicecast_state"
_MADE = Lock()
_ABSENT = object()
_BAD = frozenset({"invalid_voice", "invalid_rights", "rights_unrecorded", "invalid_target", "invalid_field",
                  "invalid_window"})
_MISSING = frozenset({"not_cast"})


class VoicecastState:
    """One library's local speech engines."""

    def __init__(self, synthesizers: tuple | None = None) -> None:
        self.synthesizers = (FliteSynthesizer(),) if synthesizers is None else tuple(synthesizers)


def configure(api: TextApi, synthesizers: tuple) -> VoicecastState:
    """Give ``api``'s library these synthesizers instead of the installed ones (tests, other local engines).
    Called once, before the app serves ``api``."""
    with _MADE:
        if getattr(api, _ATTRIBUTE, None) is not None:
            raise ValueError("this library's voice performance is already configured")
        made = VoicecastState(synthesizers)
        setattr(api, _ATTRIBUTE, made)
        return made


def _state(api: TextApi) -> VoicecastState:
    with _MADE:
        found = getattr(api, _ATTRIBUTE, None)
        if found is None:
            found = VoicecastState()
            setattr(api, _ATTRIBUTE, found)
        return found


def _store(ctx: AdapterContext) -> CastingStore:
    return CastingStore(ctx.store.root, check_read=ctx.store.check_root, check_write=ctx.store.check_destination)


def _engines(ctx: AdapterContext) -> list[dict]:
    found = []
    for synthesizer in _state(ctx.api).synthesizers:
        try:
            voices = list(synthesizer.voices())
        except Exception:  # an engine that cannot list its voices has none to offer
            voices = []
        found.append({"engine": synthesizer.engine, "voices": voices, "locality": "local", "network": False,
                      "billing": "free", "available": bool(voices)})
    return found


# -- arguments -----------------------------------------------------------------------------------------------
def _text(args: Mapping[str, object], key: str) -> str | None:
    value = args.get(key)
    if value is not None and type(value) is not str:
        raise ApiRefused("invalid_field", f"{key} must be text")
    return value  # type: ignore[return-value]


def _expected(args: Mapping[str, object]) -> str | None:
    value = args.get("expected", _ABSENT)
    if value is _ABSENT or (value is not None and type(value) is not str):
        raise ApiRefused("invalid_field", "expected must be the etag voicecast.read gave, or null when it gave none")
    return value  # type: ignore[return-value]


def _target(args: Mapping[str, object]) -> tuple[str, SourceRef | InstanceRef | None, str | None]:
    target = args.get("target")
    if target not in TARGETS:
        raise ApiRefused("invalid_field", f"target is one of {TARGETS}")
    ref_text, campaign = _text(args, "ref"), _text(args, "campaign")
    if target == "narrator":
        if ref_text is not None:
            raise ApiRefused("invalid_field", "the narrator is cast by campaign (or for every campaign), not by ref")
        return target, None, campaign  # type: ignore[return-value]
    if campaign is not None:
        raise ApiRefused("invalid_field", "only the narrator is cast per campaign")
    try:
        ref = InstanceRef.parse(ref_text) if target == "actor" else SourceRef.parse(ref_text)
    except (ValueError, TypeError):
        raise ApiRefused("invalid_field", f"ref is not a {'campaign instance' if target == 'actor' else 'definition'}"
                         " reference") from None
    return target, ref, None  # type: ignore[return-value]


def _require(ctx: AdapterContext, target: str, ref: SourceRef | InstanceRef | None, campaign: str | None) -> None:
    """A new voice is given only to a character, actor or campaign this library holds now."""
    try:
        if target == "character":
            assert isinstance(ref, SourceRef)
            held = not isinstance(ctx.store.check_definition(ref), str) or (
                not ref.pinned and (ref.kind, ref.entity_id) in ctx.store.source_entities(ref.source))
        elif target == "actor":
            ctx.store.read(ref)
            held = True
        else:
            held = campaign is None or ctx.store.campaign_exists(campaign)
    except RecordNotFound:
        held = False
    except (StoreError, ValueError, OSError):
        held = target != "character"  # there, if not readable: a voice is about the target, not its content
    if not held:
        missing = "campaign" if target == "narrator" else target
        raise ApiRefused(f"no_{missing}", f"no {missing} {ref or campaign} is in this library", 404)


def _chat(args: Mapping[str, object]) -> ChatRef:
    try:
        return ChatRef.parse(args.get("chat"))
    except (ValueError, TypeError):
        raise ApiRefused("invalid_field", "chat is not a chat reference") from None


def _int(args: Mapping[str, object], key: str, default: int, low: int, high: int) -> int:
    value = args.get(key, default)
    if type(value) is not int or not low <= value <= high:
        raise ApiRefused("invalid_field", f"{key} is a whole number from {low} to {high}")
    return value


# -- views ---------------------------------------------------------------------------------------------------
def _entry_view(entry: CastEntry, engines: Mapping[str, list[str]]) -> dict:
    return {"target": entry.target, "ref": None if entry.ref is None else str(entry.ref), "campaign": entry.campaign,
            "engine": entry.voice.engine, "voice": entry.voice.voice, "rights": entry.voice.rights,
            "note": entry.voice.note, "describes": entry.describe(),
            "available": entry.voice.voice in engines.get(entry.voice.engine, [])}


def _cue_view(cue: Cue) -> dict:
    return {"index": cue.index, "turn": cue.turn, "kind": cue.kind, "item": cue.item, "speaker": cue.speaker,
            "actor": None if cue.actor is None else str(cue.actor),
            "revision": None if cue.revision is None else str(cue.revision),
            "portrayal": None if cue.portrayal is None else str(cue.portrayal),
            "text": cue.text, "digest": cue.digest, "script": cue.script, "status": cue.status, "reason": cue.reason,
            "voice": None if cue.chosen is None else {"engine": cue.chosen.voice.engine, "voice": cue.chosen.voice.voice,
                                                      "rights": cue.chosen.voice.rights, "note": cue.chosen.voice.note,
                                                      "cast_for": cue.chosen.describe()},
            "considered": [{"rule": item.rule, "names": item.names,
                            "voice": None if item.entry is None else item.entry.voice.key} for item in cue.considered],
            "why": explain(cue)}


def _played(ctx: AdapterContext, actors: set[InstanceRef]) -> dict[InstanceRef, SourceRef]:
    """The character each posting actor plays, from its campaign instance; an actor that cannot be read plays none
    (its posts are then voiced by its own entry only)."""
    found: dict[InstanceRef, SourceRef] = {}
    for actor in actors:
        try:
            envelope = ctx.store.read(actor).envelope
            schema = envelope.extra.get("schema")
            source = instance_from_payload(actor, schema if isinstance(schema, str) else "", envelope.payload).source
        except (RecordNotFound, StoreError, CodecError, ValueError, OSError, AttributeError):
            continue
        if isinstance(source, SourceRef):
            found[actor] = source
    return found


def _performance(ctx: AdapterContext, chat: ChatRef):  # type: ignore[no-untyped-def]
    view = _store(ctx).view()
    scene = ctx.runner.open(chat)
    if any(code == "turn_fork" for code, _detail in scene.problems):
        raise ApiRefused("turn_fork", "this chat's turns fork (two devices played the same turn); its branches are "
                         "not one scene to read aloud until the fork is resolved", 409)
    problems = [{"code": code, "detail": detail} for code, detail in scene.problems]
    speaking = {post.actor for record in scene.records for post in record.posts if not post.private}
    speaking |= {line.actor for record in scene.records for take in record.takes for line in take.lines
                 if line.actor is not None and line.actor_revision is None}
    try:
        heard = outcome_lines(ctx.runner, scene.records, scene.names)[0]
    except SceneRefused as error:  # the outcomes cannot be read now: the rest of the scene still performs
        heard = {}
        problems.append({"code": "outcomes_unreadable", "detail": error.reason})
    outcomes = {turn: (line.speaker, line.text) for turn, line in heard.items()}
    performance = plan_performance(scene.records, view.casting, scene.names, _played(ctx, speaking), outcomes)
    if view.problem is not None:
        problems.append({"code": f"casting_{view.problem.reason}", "detail": view.problem.path})
    return performance, problems


# -- the ops -------------------------------------------------------------------------------------------------
def _read(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    view = _store(ctx).view()
    engines = _engines(ctx)
    voices = {engine["engine"]: engine["voices"] for engine in engines}
    return {"casting": [_entry_view(entry, voices) for entry in view.casting.entries], "etag": view.etag,
            "problem": None if view.problem is None else {"path": view.problem.path, "reason": view.problem.reason,
                                                          "detail": view.problem.detail},
            "engines": engines, "targets": list(TARGETS), "rights": list(RIGHTS)}


def _cast(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    target, ref, campaign = _target(args)
    voice = SpeechVoice(args.get("engine"), args.get("voice"), args.get("rights", "engine"),  # type: ignore[arg-type]
                        _text(args, "note"))
    entry = CastEntry(target, voice, ref, campaign)
    _require(ctx, target, ref, campaign)
    _store(ctx).write(lambda casting: casting.with_entry(entry), _expected(args))
    return _read(ctx, args)


def _uncast(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    target, ref, campaign = _target(args)

    def change(casting: Casting) -> Casting:
        return casting.without(target, ref, campaign)

    _store(ctx).write(change, _expected(args))
    return _read(ctx, args)


def _plan(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    chat = _chat(args)
    performance, problems = _performance(ctx, chat)
    return {"chat": str(chat), "fingerprint": performance.fingerprint,
            "cues": [_cue_view(cue) for cue in performance.cues],
            "shared": [{"voice": shared.voice, "speakers": list(shared.speakers)} for shared in performance.shared],
            "engines": _engines(ctx), "problems": problems}


def _render(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    chat = _chat(args)
    count = _int(args, "count", DEFAULT_WINDOW, 1, MAX_WINDOW)
    audio = args.get("audio", True)
    if type(audio) is not bool:
        raise ApiRefused("invalid_field", "audio must be true or false")
    expected = args.get("fingerprint")
    if expected is not None and (type(expected) is not str or not expected.startswith("sha256:")):
        raise ApiRefused("invalid_field", "fingerprint must be the one voicecast.plan or render gave, or null")
    performance, problems = _performance(ctx, chat)
    if expected is not None and expected != performance.fingerprint:
        raise ApiRefused("changed", "the scene or its casting changed since that fingerprint; plan it again", 409)
    start = _int(args, "start", 0, 0, len(performance.cues))
    rendering = render_performance(performance, _state(ctx.api).synthesizers, start=start, count=count, audio=audio,
                                   clock=time.monotonic, budget=RENDER_BUDGET)
    segments = []
    for segment in rendering.segments:
        cue = performance.cues[segment.cue]
        segments.append({"cue": segment.cue, "turn": cue.turn, "item": cue.item, "speaker": cue.speaker, "text": cue.text, "digest": segment.digest,
                         "voice": segment.voice, "status": segment.status, "reason": segment.reason,
                         "start": round(segment.start, 3), "duration": round(segment.duration, 3),
                         "audio": None if segment.audio is None else base64.b64encode(segment.audio).decode("ascii")})
    return {"chat": str(chat), "fingerprint": performance.fingerprint, "total": len(performance.cues),
            "start": start, "next": rendering.next,
            "segments": segments, "problems": problems}


def _refuse(error: CastingError) -> ApiRefused:
    status = 400 if error.reason in _BAD else 404 if error.reason in _MISSING else 409
    return ApiRefused(error.reason, str(error), status)


def _op(work: Callable[[AdapterContext, Mapping[str, object]], dict], *, writes: bool) -> Handler:
    def handler(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
        if writes and not ctx.store.writable:
            raise ApiRefused("read_only", "this library is open read-only")
        try:
            return work(ctx, args)
        except CastingError as error:
            raise _refuse(error) from None
        except OSError as error:
            raise ApiRefused("casting_unwritable" if writes else "casting_unreadable",
                             f"the casting cannot be {'written' if writes else 'read'} "
                             f"({error.strerror or type(error).__name__})") from None

    handler.__name__ = "voicecast" + work.__name__
    return handler


ADAPTER = Adapter("voicecast", owner="083", ops={
    "read": _op(_read, writes=False), "plan": _op(_plan, writes=False), "render": _op(_render, writes=False),
    "cast": _op(_cast, writes=True), "uncast": _op(_uncast, writes=True)})

__all__ = ["ADAPTER", "DEFAULT_WINDOW", "MAX_WINDOW", "RENDER_BUDGET", "VoicecastState", "configure"]
