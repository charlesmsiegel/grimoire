"""Exact JSON forms of what the adjudication ledger records (Sprint 029): frames, rulings, decisions.

Encoding is deterministic: sets are sorted lists and every key is always present (``None`` where a value
is absent), so decoding a stored entry and encoding it again reproduces it. Decoding is strict: exact
keys, exact JSON types, then the value's own constructor. Anything else is an ``AdjudicationError``
with reason ``undecodable_entry``; the store-backed ledger turns that into ``LedgerInvalid`` and fails
closed. The campaign is the ledger's: a value of another campaign is refused.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.core.ability_use import AbilityAttempt, AbilityRuling
from grimoire.core.authority import PlayerContribution
from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.core.knowledge import Moment

from .model import AbilityFinding, ActionFrame, AdjudicationError, Decision, Factor, Stake

FRAME_KEYS = frozenset({"attempt_id", "actor", "chat", "moment", "intent", "provenance", "factors", "stakes",
                        "ability", "contribution"})
RULING_KEYS = frozenset({"ruling_id", "attempt", "decides", "decision", "provenance", "note", "terms",
                         "supersedes"})
DECISION_KEYS = frozenset({"attempt_id", "actor", "revision", "classification", "result", "basis", "cites",
                           "consequences", "consented_by", "ability"})
_ATTEMPT_KEYS = frozenset({"attempt_id", "actor", "ability_id", "needs", "provenance", "effect_id",
                           "conditions_met"})
_FACTOR_KEYS = frozenset({"factor_id", "bearing", "text", "established", "source"})
_STAKE_KEYS = frozenset({"stake_id", "text"})
_MOMENT_KEYS = frozenset({"sequence", "scene_id", "turn_id"})
_CONTRIBUTION_KEYS = frozenset({"contribution_id", "scene_id", "turn_id", "actor_id", "submitted_by", "kind",
                                "text"})
_FINDING_KEYS = frozenset({"ability_id", "classification", "approval", "ruling_id", "uncertain", "charges"})


def _bad(message: str) -> AdjudicationError:
    return AdjudicationError(message, "undecodable_entry")


def _object(value: object, keys: frozenset[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise _bad(f"{label} must be an object with exactly {sorted(keys)}")
    return value


def _str(value: object, label: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str:
        raise _bad(f"{label} must be text")
    return value


def _int(value: object, label: str) -> int:
    if type(value) is not int:  # ``true`` is not 1
        raise _bad(f"{label} must be an integer")
    return value


def _strings(value: object, label: str) -> frozenset[str]:
    if not isinstance(value, (list, tuple)) or not all(type(item) is str for item in value):
        raise _bad(f"{label} must be a list of text")
    if list(value) != sorted(set(value)):
        raise _bad(f"{label} must be sorted and unique")
    return frozenset(value)


def _ref(value: object, parse, label: str):
    text = _str(value, label)
    try:
        return parse(text)
    except ValueError:
        raise _bad(f"{label} is not a reference: {text!r}") from None


def _thaw(value: object) -> object:
    """Plain JSON shapes: stored entries come back frozen (mapping proxies, tuples)."""
    if isinstance(value, Mapping):
        return {key: _thaw(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(child) for child in value]
    return value


def _build(factory, *args, **kwargs):
    try:
        return factory(*args, **kwargs)
    except (ValueError, TypeError) as error:
        raise _bad(f"{getattr(factory, '__name__', factory)}: {error}") from None


# --- attempts --------------------------------------------------------------------------------

def attempt_to_payload(attempt: AbilityAttempt) -> dict[str, object]:
    return {"attempt_id": attempt.attempt_id, "actor": str(attempt.actor), "ability_id": attempt.ability_id,
            "needs": sorted(attempt.needs), "provenance": attempt.provenance, "effect_id": attempt.effect_id,
            "conditions_met": sorted(attempt.conditions_met)}


def attempt_from_payload(value: object) -> AbilityAttempt:
    data = _object(value, _ATTEMPT_KEYS, "attempt")
    return _build(AbilityAttempt, _str(data["attempt_id"], "attempt_id"),
                  _ref(data["actor"], InstanceRef.parse, "actor"), _str(data["ability_id"], "ability_id"),
                  _strings(data["needs"], "needs"), _str(data["provenance"], "provenance"),
                  _str(data["effect_id"], "effect_id", optional=True),
                  _strings(data["conditions_met"], "conditions_met"))


# --- frames ----------------------------------------------------------------------------------

def frame_to_payload(frame: ActionFrame, contribution: PlayerContribution | None) -> dict[str, object]:
    """A frame and the player contribution behind it (``None`` for a director-controlled actor)."""
    if type(frame) is not ActionFrame:
        raise TypeError("expected an ActionFrame")
    moment = frame.moment
    return {
        "attempt_id": frame.attempt_id, "actor": str(frame.actor), "chat": str(frame.chat),
        "moment": {"sequence": moment.sequence, "scene_id": moment.scene_id, "turn_id": moment.turn_id},
        "intent": frame.intent, "provenance": frame.provenance,
        "factors": [{"factor_id": f.factor_id, "bearing": f.bearing, "text": f.text, "established": f.established,
                     "source": f.source} for f in frame.factors],
        "stakes": [{"stake_id": s.stake_id, "text": s.text} for s in frame.stakes],
        "ability": None if frame.ability is None else attempt_to_payload(frame.ability),
        "contribution": None if contribution is None else {
            "contribution_id": contribution.contribution_id, "scene_id": contribution.scene_id,
            "turn_id": contribution.turn_id, "actor_id": contribution.actor_id,
            "submitted_by": contribution.submitted_by, "kind": contribution.kind, "text": contribution.text},
    }


def frame_from_payload(campaign_id: str, value: object) -> tuple[ActionFrame, PlayerContribution | None]:
    data = _object(value, FRAME_KEYS, "frame")
    moment = _object(data["moment"], _MOMENT_KEYS, "moment")
    factors = data["factors"]
    stakes = data["stakes"]
    if not isinstance(factors, (list, tuple)) or not isinstance(stakes, (list, tuple)):
        raise _bad("factors and stakes must be lists")
    frame = _build(
        ActionFrame, _str(data["attempt_id"], "attempt_id"), _ref(data["actor"], InstanceRef.parse, "actor"),
        _ref(data["chat"], ChatRef.parse, "chat"),
        _build(Moment, _int(moment["sequence"], "sequence"), _str(moment["scene_id"], "scene_id"),
               _str(moment["turn_id"], "turn_id")),
        _str(data["intent"], "intent"),
        tuple(_factor(item) for item in factors), tuple(_stake(item) for item in stakes),
        _str(data["provenance"], "provenance"),
        None if data["ability"] is None else attempt_from_payload(data["ability"]))
    if frame.campaign_id != campaign_id:
        raise _bad("the frame belongs to another campaign")
    contribution = None
    if data["contribution"] is not None:
        given = _object(data["contribution"], _CONTRIBUTION_KEYS, "contribution")
        contribution = _build(PlayerContribution, campaign_id,
                              *(_str(given[key], key) for key in ("contribution_id", "scene_id", "turn_id",
                                                                  "actor_id", "submitted_by", "kind", "text")))
    if frame_to_payload(frame, contribution) != _thaw(data):  # sorted, canonical: nothing a reader would re-order
        raise _bad("the frame is not in its canonical form")
    return frame, contribution


def _factor(value: object) -> Factor:
    data = _object(value, _FACTOR_KEYS, "factor")
    return _build(Factor, _str(data["factor_id"], "factor_id"), _str(data["bearing"], "bearing"),
                  _str(data["text"], "text"), _int(data["established"], "established"), _str(data["source"], "source"))


def _stake(value: object) -> Stake:
    data = _object(value, _STAKE_KEYS, "stake")
    return _build(Stake, _str(data["stake_id"], "stake_id"), _str(data["text"], "text"))


# --- rulings ---------------------------------------------------------------------------------

def ruling_to_payload(ruling: AbilityRuling, supersedes: frozenset[str]) -> dict[str, object]:
    if type(ruling) is not AbilityRuling:
        raise TypeError("expected an AbilityRuling")
    return {"ruling_id": ruling.ruling_id, "attempt": attempt_to_payload(ruling.attempt), "decides": ruling.decides,
            "decision": ruling.decision, "provenance": ruling.provenance, "note": ruling.note,
            "terms": sorted(ruling.terms), "supersedes": sorted(supersedes)}


def ruling_from_payload(campaign_id: str, value: object) -> tuple[AbilityRuling, frozenset[str]]:
    data = _object(value, RULING_KEYS, "ruling")
    ruling = _build(AbilityRuling, _str(data["ruling_id"], "ruling_id"), attempt_from_payload(data["attempt"]),
                    _str(data["decides"], "decides"), _str(data["decision"], "decision"),
                    _str(data["provenance"], "provenance"), _str(data["note"], "note"),
                    _strings(data["terms"], "terms"))
    if ruling.attempt.actor.campaign_id != campaign_id:
        raise _bad("the ruling belongs to another campaign")
    return ruling, _strings(data["supersedes"], "supersedes")


# --- decisions -------------------------------------------------------------------------------

def decision_to_payload(frame: ActionFrame, revision: int, classification: str, decision: Decision,
                        consented_by: str | None, ability: AbilityFinding | None) -> dict[str, object]:
    return {"attempt_id": frame.attempt_id, "actor": str(frame.actor), "revision": revision,
            "classification": classification, "result": decision.result, "basis": decision.basis,
            "cites": sorted(decision.cites), "consequences": sorted(decision.consequences),
            "consented_by": consented_by,
            "ability": None if ability is None else {
                "ability_id": ability.ability_id, "classification": ability.classification,
                "approval": ability.approval, "ruling_id": ability.ruling_id, "uncertain": ability.uncertain,
                "charges": [list(pair) for pair in ability.charges]}}


def decision_from_payload(campaign_id: str, value: object
                          ) -> tuple[str, InstanceRef, int, str, Decision, str | None, AbilityFinding | None]:
    """``(attempt_id, actor, revision, classification, decision, consented_by, ability finding)``."""
    data = _object(value, DECISION_KEYS, "decision")
    actor = _ref(data["actor"], InstanceRef.parse, "actor")
    if actor.campaign_id != campaign_id:
        raise _bad("the decision belongs to another campaign")
    decision = _build(Decision, _str(data["result"], "result"), _str(data["basis"], "basis"),
                      _strings(data["cites"], "cites"), _strings(data["consequences"], "consequences"))
    finding: AbilityFinding | None = None
    if data["ability"] is not None:
        given = _object(data["ability"], _FINDING_KEYS, "ability")
        charges = given["charges"]
        if not isinstance(charges, (list, tuple)) or not all(
                isinstance(pair, (list, tuple)) and len(pair) == 2 and all(type(part) is str for part in pair)
                for pair in charges):
            raise _bad("charges must be [ability, cost] pairs")
        if type(given["uncertain"]) is not bool:
            raise _bad("uncertain must be true or false")
        finding = _build(AbilityFinding, _str(given["ability_id"], "ability_id"),
                         _str(given["classification"], "classification"), _str(given["approval"], "approval"),
                         _str(given["ruling_id"], "ruling_id", optional=True), given["uncertain"],
                         tuple(tuple(pair) for pair in charges))
        if [list(pair) for pair in finding.charges] != _thaw(charges):
            raise _bad("charges must be sorted")
    return (_str(data["attempt_id"], "attempt_id"), actor, _int(data["revision"], "revision"),
            _str(data["classification"], "classification"), decision,
            _str(data["consented_by"], "consented_by", optional=True), finding)
