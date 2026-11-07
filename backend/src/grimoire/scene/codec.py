"""Turn payloads (``grimoire.scene-turn/1``) to and from plain JSON-compatible data.

The store's scope-record envelope owns the address (``TurnRef``) and the schema id. Unknown top-level
payload keys survive a read/modify/write in ``extras``; inside a known structure an unknown or malformed
field is refused (``invalid_field``), never dropped. An unknown schema is refused (``unsupported_schema``),
so a newer turn is never rewritten by this version.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.adjudication import Factor, Stake
from grimoire.core.identity import ChatRef, InstanceRef, SourceRef, TurnRef
from grimoire.core.inference import TOKEN_FIELDS, CallAttempt, CallId, InferenceStage, TokenUsage

from .model import CallRecord, Label, Post, SceneLine, SceneRefused, SetAside, Take, TurnRecord, turn_id

TURN_SCHEMA = "grimoire.scene-turn/1"
_TURN_KEYS = frozenset({"chat", "number", "origin", "post", "takes", "paused", "paused_by", "passed_by"})
#: Written on every turn. A turn written before turn ids carried a device tag has no ``device`` (read as
#: untagged); one written before 028's turn merge has no ``follows`` (its place is inferred) or ``set_aside``.
#: ``labels`` is written only on a turn whose action was labelled (035 milestone 2); an older build keeps it as
#: an unknown field when it rewrites the turn.
_OPTIONAL_TURN_KEYS = frozenset({"device", "follows", "set_aside", "labels"})
_SET_ASIDE_KEYS = frozenset({"chosen", "by"})
_IDENTITY_KEYS = frozenset({"schema", "address", "campaign_id", "ref"})
_POST_KEYS = frozenset({"submitted_by", "actor", "kind", "text", "audience", "channel", "addressees", "withheld"})
_TAKE_KEYS = frozenset({"take", "state", "origin", "cast", "lines", "calls", "outcome", "declined", "unscreened",
                        "direction", "by"})
_LINE_KEYS = frozenset({"line", "actor", "speaker", "text", "audience", "state", "by", "actor_revision",
                        "voice_profile", "review"})
_CALL_KEYS = frozenset({"phase", "stage", "actor_id", "ordinal", "status", "reason", "attempts"})
_ATTEMPT_KEYS = frozenset({"attempt", "outcome", "measurement", "route", "usage"})
_LABEL_KEYS = frozenset({"label", "state", "sequence", "factors", "stakes", "calls", "reason", "by"})
_FACTOR_KEYS = frozenset({"factor", "bearing", "text", "established", "source"})
_STAKE_KEYS = frozenset({"stake", "text"})


def _invalid(message: str) -> SceneRefused:
    return SceneRefused("invalid_field", message)


def _ref(ref: object) -> str | None:
    return None if ref is None else str(ref)


def turn_to_payload(record: TurnRecord) -> dict[str, object]:
    payload: dict[str, object] = dict(record.extras)
    payload.update({
        "chat": record.chat.chat_id, "number": record.number, "origin": record.origin,
        "post": None if record.post is None else _post(record.post),
        "takes": [_take(take) for take in record.takes],
        "paused": record.paused, "paused_by": record.paused_by, "passed_by": record.passed_by,
        "device": record.device, "follows": record.follows,
        "set_aside": None if record.set_aside is None else {"chosen": record.set_aside.chosen,
                                                             "by": record.set_aside.by},
    })
    if record.labels:
        payload["labels"] = [_label(label) for label in record.labels]
    return payload


def _label(label: Label) -> dict[str, object]:
    return {"label": label.label, "state": label.state, "sequence": label.sequence,
            "factors": [{"factor": f.factor_id, "bearing": f.bearing, "text": f.text, "established": f.established,
                         "source": f.source} for f in label.factors],
            "stakes": [{"stake": s.stake_id, "text": s.text} for s in label.stakes],
            "calls": [_call(call) for call in label.calls], "reason": label.reason, "by": label.by}


def _post(post: Post) -> dict[str, object]:
    return {"submitted_by": post.submitted_by, "actor": str(post.actor), "kind": post.kind, "text": post.text,
            "audience": [str(a) for a in post.audience], "channel": post.channel,
            "addressees": [str(a) for a in post.addressees], "withheld": post.withheld}


def _take(take: Take) -> dict[str, object]:
    return {"take": take.take, "state": take.state, "origin": take.origin, "cast": [str(a) for a in take.cast],
            "lines": [_line(line) for line in take.lines], "calls": [_call(call) for call in take.calls],
            "outcome": take.outcome, "declined": list(take.declined), "unscreened": list(take.unscreened),
            "direction": take.direction, "by": take.by}


def _line(line: SceneLine) -> dict[str, object]:
    return {"line": line.line_id, "actor": _ref(line.actor), "speaker": line.speaker, "text": line.text,
            "audience": [str(a) for a in line.audience], "state": line.state, "by": line.by,
            "actor_revision": _ref(line.actor_revision), "voice_profile": _ref(line.voice_profile),
            "review": line.review}


def _call(call: CallRecord) -> dict[str, object]:
    return {"phase": call.phase, "stage": call.stage.value, "actor_id": call.actor_id, "ordinal": call.ordinal,
            "status": call.status, "reason": call.reason,
            "attempts": [{"attempt": a.attempt, "outcome": a.outcome, "measurement": a.measurement.value,
                          "route": a.route, "usage": {name: getattr(a.usage, name) for name in TOKEN_FIELDS}}
                         for a in call.attempts]}


# -- decoding -------------------------------------------------------------------------------------

def _mapping(value: object, label: str, keys: frozenset[str], *, open_keys: bool = False) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(type(key) is str for key in value):
        raise _invalid(f"{label} must be an object")
    missing = keys - set(value)
    if missing:
        raise _invalid(f"{label} lacks {sorted(missing)}")
    if not open_keys and set(value) - keys:
        raise _invalid(f"{label} has unknown fields {sorted(set(value) - keys)}")
    return value


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, (list, tuple)):
        raise _invalid(f"{label} must be a list")
    return list(value)


def _actor(value: object, label: str) -> InstanceRef:
    try:
        return InstanceRef.parse(value)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        raise _invalid(f"{label} must be an actor reference") from None


def _source(value: object, label: str) -> SourceRef | None:
    if value is None:
        return None
    try:
        return SourceRef.parse(value)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        raise _invalid(f"{label} must be a source reference") from None


def _int(value: object, label: str) -> int:
    if type(value) is not int:
        raise _invalid(f"{label} must be an integer")
    return value


def turn_from_payload(address: TurnRef, schema: str, payload: object) -> TurnRecord:
    """The turn at ``address``; refused (``SceneRefused``) when the payload is not one this version reads."""
    if schema != TURN_SCHEMA:
        raise SceneRefused("unsupported_schema", f"not a {TURN_SCHEMA} payload")
    data = _mapping(payload, "turn", _TURN_KEYS, open_keys=True)
    if _IDENTITY_KEYS & set(data):
        raise SceneRefused("identity_in_payload", "the envelope owns a turn's identity")
    try:
        chat = ChatRef(address.campaign_id, data["chat"])  # type: ignore[arg-type]
    except (ValueError, TypeError):
        raise _invalid("chat must be a chat id") from None
    number = _int(data["number"], "number")
    device = data.get("device")
    if device is not None and type(device) is not str:
        raise _invalid("device must be a device tag or null")
    if turn_id(chat, number, device) != address.turn_id:  # type: ignore[arg-type]
        raise SceneRefused("address_mismatch", "a turn's chat, number and device must spell its address")
    extras = {key: value for key, value in data.items() if key not in _TURN_KEYS | _OPTIONAL_TURN_KEYS}
    follows = data.get("follows")
    if follows is not None and type(follows) is not str:
        raise _invalid("follows must be a turn id or null")
    try:
        mark = None if data.get("set_aside") is None else _mapping(data["set_aside"], "set_aside", _SET_ASIDE_KEYS)
        set_aside = None if mark is None else SetAside(mark["chosen"], mark["by"])  # type: ignore[arg-type]
        return TurnRecord(chat, number, data["origin"],  # type: ignore[arg-type]
                          None if data["post"] is None else _decode_post(data["post"]),
                          tuple(_decode_take(item, address) for item in _list(data["takes"], "takes")),
                          data["paused"], data["paused_by"], data["passed_by"],  # type: ignore[arg-type]
                          device, follows, set_aside,  # type: ignore[arg-type]
                          tuple(_decode_label(item, address) for item in _list(data.get("labels", []), "labels")),
                          extras=extras)
    except SceneRefused:
        raise
    except (ValueError, TypeError) as error:
        raise _invalid(str(error)[:200]) from None


def _decode_post(value: object) -> Post:
    data = _mapping(value, "post", _POST_KEYS)
    return Post(data["submitted_by"], _actor(data["actor"], "post.actor"), data["kind"],  # type: ignore[arg-type]
                data["text"],  # type: ignore[arg-type]
                tuple(_actor(item, "post.audience") for item in _list(data["audience"], "post.audience")),
                data["channel"],  # type: ignore[arg-type]
                tuple(_actor(item, "post.addressees") for item in _list(data["addressees"], "post.addressees")),
                data["withheld"])  # type: ignore[arg-type]


def _decode_take(value: object, address: TurnRef) -> Take:
    data = _mapping(value, "take", _TAKE_KEYS)
    number = _int(data["take"], "take")
    plan_turn = f"{address.turn_id}.{number}"  # the id its calls were planned and billed under
    return Take(number, data["state"], data["origin"],  # type: ignore[arg-type]
                tuple(_actor(item, "take.cast") for item in _list(data["cast"], "take.cast")),
                tuple(_decode_line(item) for item in _list(data["lines"], "take.lines")),
                tuple(_decode_call(item, address.campaign_id, plan_turn)
                      for item in _list(data["calls"], "take.calls")),
                data["outcome"], tuple(_list(data["declined"], "take.declined")),  # type: ignore[arg-type]
                tuple(_list(data["unscreened"], "take.unscreened")),  # type: ignore[arg-type]
                data["direction"], data["by"])  # type: ignore[arg-type]


def _decode_label(value: object, address: TurnRef) -> Label:
    data = _mapping(value, "label", _LABEL_KEYS)
    number = _int(data["label"], "label")
    plan_turn = f"{address.turn_id}.l{number}"
    factors = []
    for item in _list(data["factors"], "label.factors"):
        found = _mapping(item, "factor", _FACTOR_KEYS)
        factors.append(Factor(found["factor"], found["bearing"], found["text"],  # type: ignore[arg-type]
                              found["established"], found["source"]))  # type: ignore[arg-type]
    stakes = [Stake(found["stake"], found["text"])  # type: ignore[arg-type]
              for found in (_mapping(item, "stake", _STAKE_KEYS) for item in _list(data["stakes"], "label.stakes"))]
    calls = tuple(_decode_call(item, address.campaign_id, plan_turn) for item in _list(data["calls"], "label.calls"))
    return Label(number, data["state"], data["sequence"], tuple(factors), tuple(stakes),  # type: ignore[arg-type]
                 calls, data["reason"], data["by"])  # type: ignore[arg-type]


def _decode_line(value: object) -> SceneLine:
    data = _mapping(value, "line", _LINE_KEYS)
    actor = None if data["actor"] is None else _actor(data["actor"], "line.actor")
    return SceneLine(data["line"], actor, data["speaker"], data["text"],  # type: ignore[arg-type]
                     tuple(_actor(item, "line.audience") for item in _list(data["audience"], "line.audience")),
                     data["state"], data["by"],  # type: ignore[arg-type]
                     _source(data["actor_revision"], "line.actor_revision"),
                     _source(data["voice_profile"], "line.voice_profile"), data["review"])  # type: ignore[arg-type]


def _decode_call(value: object, campaign_id: str, plan_turn: str) -> CallRecord:
    data = _mapping(value, "call", _CALL_KEYS)
    try:
        stage = InferenceStage(data["stage"])
    except ValueError:
        raise _invalid("call.stage is not an inference stage") from None
    phase, actor_id, ordinal = data["phase"], data["actor_id"], _int(data["ordinal"], "call.ordinal")
    probe = CallId(campaign_id, plan_turn, phase, stage, actor_id, ordinal)  # type: ignore[arg-type]
    attempts = []
    for item in _list(data["attempts"], "call.attempts"):
        found = _mapping(item, "attempt", _ATTEMPT_KEYS)
        usage = _mapping(found["usage"], "attempt.usage", frozenset(TOKEN_FIELDS))
        attempts.append(CallAttempt(probe, _int(found["attempt"], "attempt.attempt"),
                                    TokenUsage(**{name: usage[name] for name in TOKEN_FIELDS}),  # type: ignore[arg-type]
                                    found["measurement"], found["outcome"], found["route"]))  # type: ignore[arg-type]
    return CallRecord(phase, stage, actor_id, ordinal, data["status"], data["reason"],  # type: ignore[arg-type]
                      tuple(attempts))


__all__ = ["TURN_SCHEMA", "turn_from_payload", "turn_to_payload"]
