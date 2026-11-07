"""The ``grimoire.decision/1`` payload of one ``DecisionRecord``: strict both ways.

Every key is required and none is extra; values are rebuilt through the record's own constructor, so a
payload that reads back is a record that validates. Lists keep the policy's order; nothing here is a
set, so the payload is the same on every device and under every hash seed.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.core.identity import InstanceRef, TurnRef

from .model import Candidate, DecisionError, DecisionRecord, Evidence

FORMAT = "grimoire.decision/1"
_RECORD_KEYS = {"format", "turn", "actor", "method", "as_of", "agenda_digest", "candidates", "chosen", "default",
                "evidence", "call", "fallback"}
_CANDIDATE_KEYS = {"goal", "approach", "outcome", "unmet", "forbidden_by"}
_EVIDENCE_KEYS = {"supports", "proposition", "stance", "actor"}


def _pair(value: tuple[str, str] | None) -> list[str] | None:
    return list(value) if value is not None else None


def decision_to_payload(record: DecisionRecord) -> dict[str, object]:
    if type(record) is not DecisionRecord:
        raise DecisionError("bad_record", "decision_to_payload needs a DecisionRecord")
    return {
        "format": FORMAT, "turn": str(record.turn), "actor": str(record.actor), "method": record.method,
        "as_of": record.as_of, "agenda_digest": record.agenda_digest,
        "candidates": [{"goal": entry.goal_id, "approach": entry.approach_id, "outcome": entry.outcome,
                        "unmet": list(entry.unmet), "forbidden_by": list(entry.forbidden_by)}
                       for entry in record.candidates],
        "chosen": _pair(record.chosen), "default": _pair(record.default),
        "evidence": [{"supports": item.supports, "proposition": item.proposition_id, "stance": item.stance,
                      "actor": str(item.actor) if item.actor is not None else None} for item in record.evidence],
        "call": record.call, "fallback": record.fallback,
    }


def _bad(message: str) -> DecisionError:
    return DecisionError("bad_record", message)


def _exact(value: object, keys: set[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise _bad(f"{label} must hold exactly {sorted(keys)}")
    return value


def _text(value: object, label: str, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or not value:
        raise _bad(f"{label} must be text")
    return value


def _texts(value: object, label: str) -> tuple[str, ...]:
    if type(value) not in (list, tuple):  # a ledger reads its arrays back as tuples
        raise _bad(f"{label} must be a list")
    return tuple(_text(item, label) for item in value)  # type: ignore[misc]


def _read_pair(value: object, label: str) -> tuple[str, str] | None:
    if value is None:
        return None
    pair = _texts(value, label)
    if len(pair) != 2:
        raise _bad(f"{label} must be a (goal, approach) pair")
    return pair  # type: ignore[return-value]


def _ref(value: object, label: str) -> InstanceRef:
    try:
        return InstanceRef.parse(value)
    except ValueError:
        raise _bad(f"{label} must be an instance reference") from None


def decision_from_payload(payload: object) -> DecisionRecord:
    """The record ``payload`` holds, or ``bad_record`` (or the record's own refusal reason)."""
    data = _exact(payload, _RECORD_KEYS, "a decision")
    if data["format"] != FORMAT:
        raise _bad(f"unknown decision format {data['format']!r}")
    try:
        turn = TurnRef.parse(data["turn"])
    except ValueError:
        raise _bad("turn must be a turn reference") from None
    as_of = data["as_of"]
    if type(as_of) is not int:
        raise _bad("as_of must be an integer")
    if type(data["candidates"]) not in (list, tuple) or type(data["evidence"]) not in (list, tuple):
        raise _bad("candidates and evidence must be lists")
    candidates = []
    for raw in data["candidates"]:
        entry = _exact(raw, _CANDIDATE_KEYS, "a candidate")
        candidates.append(Candidate(_text(entry["goal"], "goal"), _text(entry["approach"], "approach"),  # type: ignore[arg-type]
                                    _text(entry["outcome"], "outcome"),  # type: ignore[arg-type]
                                    _texts(entry["unmet"], "unmet"), _texts(entry["forbidden_by"], "forbidden_by")))
    evidence = []
    for raw in data["evidence"]:
        item = _exact(raw, _EVIDENCE_KEYS, "an evidence item")
        actor = item["actor"]
        evidence.append(Evidence(_text(item["supports"], "supports"),  # type: ignore[arg-type]
                                 _text(item["proposition"], "proposition", optional=True),
                                 _text(item["stance"], "stance", optional=True),
                                 _ref(actor, "actor") if actor is not None else None))
    return DecisionRecord(turn, _ref(data["actor"], "actor"), _text(data["method"], "method"),  # type: ignore[arg-type]
                          as_of, _text(data["agenda_digest"], "agenda_digest"),  # type: ignore[arg-type]
                          tuple(candidates), _read_pair(data["chosen"], "chosen"),
                          _read_pair(data["default"], "default"), tuple(evidence),
                          _text(data["call"], "call", optional=True), _text(data["fallback"], "fallback", optional=True))
