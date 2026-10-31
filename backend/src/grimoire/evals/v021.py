"""Turn a 0.2.1 capture's evidence bundle into a ``grimoire.story-quality/2`` run, and verify it.

The bundle is what the opt-in capture harness (``grimoire.capture.v021``) read back from an
unmodified 0.2.1 app after a scripted synthetic scene: the app's own usage-ledger rows and the
contributions it produced. No prompt text and no credential is in it.

Conversion fails closed. Under the two-call profile every model call 0.2.1 made must be one
speaking NPC's generation, so any other row (tracker, decide, summary, embedding...) is an
unplanned call, not something to drop. 0.2.1 records only the attempt that served a call, so
its earlier attempts are booked as failed with unreported usage: missing, never free.

Pure: the evidence digest is computed by the file edge (``loader.evidence_digest``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .format import Scenario
from .format2 import FORMAT_V2, RunV2, parse_run_v2

EVIDENCE_FORMAT = "grimoire.v021-capture-evidence/1"
PINNED_V021_COMMIT = "e4c4ec4abacd0beb87fc9f1cdd06e6787d5a88ce"
HARNESS = "grimoire.capture/1"
PROFILE = "two-call"
PROVIDER_KINDS = ("openrouter", "anthropic")
MODES = ("dry_run", "live")
#: 0.2.1's narrator speaker (``routes/character_turns/_state.py``). The profile excludes it.
NARRATOR_REF = "grimoire"
GENERATION_TASKS = ("chat", "continuation")
GENERATION_PURPOSE = "speaking NPC contribution"
BUNDLE_KEYS = ("format", "scenario_id", "mode", "provider_kind", "v1_commit", "profile",
               "llm_retries", "model", "budget", "campaign", "scene", "actor_map",
               "choice_outcomes", "turns", "usage_rows")


class CaptureConversionError(ValueError):
    """The evidence cannot become a /2 run; ``code`` says why (ids only, never text)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True, slots=True)
class EvidenceCheck:
    ok: bool
    reason: str


def _malformed(message: str) -> CaptureConversionError:
    return CaptureConversionError("malformed_evidence", message)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _malformed(f"{label} must be a nonempty string")
    return value


def _count(value: object, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _malformed(f"{label} must be a count")
    return value


def _check_bundle(bundle: Mapping, scenario: Scenario) -> None:
    if not isinstance(bundle, Mapping):
        raise _malformed("the bundle must be an object")
    keys = set(bundle)
    if keys != set(BUNDLE_KEYS):
        extra, missing = sorted(keys - set(BUNDLE_KEYS)), sorted(set(BUNDLE_KEYS) - keys)
        raise _malformed(f"unknown keys {extra}" if extra else f"missing keys {missing}")
    if bundle["format"] != EVIDENCE_FORMAT:
        raise _malformed(f"format must be {EVIDENCE_FORMAT!r}")
    if bundle["scenario_id"] != scenario.scenario_id:
        raise _malformed("scenario_id does not match the scenario")
    if bundle["mode"] not in MODES:
        raise _malformed(f"mode must be one of {', '.join(MODES)}")
    if bundle["provider_kind"] not in PROVIDER_KINDS:
        raise _malformed(f"provider_kind must be one of {', '.join(PROVIDER_KINDS)}")
    if bundle["profile"] != PROFILE:
        raise _malformed(f"profile must be {PROFILE!r}")
    if _count(bundle["llm_retries"], "llm_retries") is None:
        raise _malformed("llm_retries must be a count")
    for label in ("model", "budget", "campaign", "scene", "v1_commit"):
        _text(bundle[label], label)
    if bundle["v1_commit"] != PINNED_V021_COMMIT:
        raise CaptureConversionError("pinned_commit_mismatch",
                                     "the capture did not run the pinned 0.2.1 commit")
    actor_map = bundle["actor_map"]
    actors = {actor.actor_id for actor in scenario.actors}
    if not isinstance(actor_map, Mapping) or not all(
            isinstance(ref, str) and value in actors for ref, value in actor_map.items()):
        raise _malformed("actor_map must map 0.2.1 refs to scenario actors")
    for name in ("choice_outcomes",):
        if not isinstance(bundle[name], Mapping):
            raise _malformed(f"{name} must be an object")
    if bundle["mode"] == "live" and any(
            label != "unlabelled" for label in bundle["choice_outcomes"].values()):
        raise CaptureConversionError("mode_label_conflict",
                                     "a live capture's choices are never labelled by the harness")
    if not isinstance(bundle["turns"], list) or not isinstance(bundle["usage_rows"], list):
        raise _malformed("turns and usage_rows must be lists")


def _attempts(row: Mapping, mode: str, route: str) -> list[dict]:
    """0.2.1 books one row per served call; earlier attempts left no counts."""
    count = row.get("attempts", 1)
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise _malformed("a usage row's attempts must be a count >= 1")
    if mode == "dry_run":
        measurement = "synthetic"
    elif row.get("tokens_estimated") is True:
        measurement = "estimated"
    else:
        measurement = "provider_reported"
    unreported = {"input_tokens": None, "output_tokens": None, "cache_read_tokens": None,
                  "cache_write_tokens": None, "reasoning_tokens": None}
    attempts = [{"attempt": n, "outcome": "failed", "measurement": measurement, "route": route,
                 "usage": dict(unreported)} for n in range(1, count)]
    status = row.get("status", "ok")
    served = {
        "attempt": count,
        "outcome": {"ok": "completed", "aborted": "cancelled"}.get(status, "failed"),
        "measurement": measurement,
        "route": route,
        "usage": {
            "input_tokens": _count(row.get("prompt_tokens"), "prompt_tokens"),
            "output_tokens": _count(row.get("completion_tokens"), "completion_tokens"),
            "cache_read_tokens": _count(row.get("cache_read_tokens"), "cache_read_tokens"),
            "cache_write_tokens": _count(row.get("cache_write_tokens"), "cache_write_tokens"),
            "reasoning_tokens": None,  # 0.2.1 has no reasoning-token field
        },
    }
    if row.get("cost_usd") is not None and row.get("cost_basis"):  # no basis: unreported
        served["cost"] = {"usd": row["cost_usd"], "basis": row["cost_basis"]}
    return [*attempts, served]


def convert(bundle: Mapping, scenario: Scenario, *, digest: str, run_id: str,
            label: str) -> dict:
    """The /2 run dict for ``bundle``. Validate the result with ``parse_run_v2``."""
    try:
        return _convert(bundle, scenario, digest=digest, run_id=run_id, label=label)
    except (TypeError, AttributeError, KeyError) as error:  # a shape no check above expected
        raise _malformed(f"unexpected structure ({type(error).__name__})") from None


def _served_model(rows: list[Mapping], requested: str) -> str:
    """The one model that answered every call; a dated snapshot of the requested one is fine."""
    served = set()
    for index, row in enumerate(rows):
        model = row.get("model")
        if not isinstance(model, str) or not model or (
                model != requested and row.get("requested_model") != requested):
            raise CaptureConversionError("model_mismatch",
                                         f"usage row {index} was not served by the requested model")
        served.add(model)
    if len(served) > 1:
        raise CaptureConversionError("model_mismatch", "calls were served by different models")
    return served.pop() if served else requested


def _convert(bundle: Mapping, scenario: Scenario, *, digest: str, run_id: str,
             label: str) -> dict:
    _check_bundle(bundle, scenario)
    mode = bundle["mode"]
    actor_map = bundle["actor_map"]
    route = f"{bundle['provider_kind']}:{bundle['model']}"

    responses: dict[str, tuple[int, str]] = {}
    turns: list[dict] = []
    player = next((actor_map[ref] for ref in actor_map if scenario.is_player(actor_map[ref])),
                  None)
    for raw in bundle["turns"]:
        if not isinstance(raw, Mapping) or set(raw) != {"turn", "player_text", "responses"}:
            raise _malformed("a turn needs exactly turn, player_text and responses")
        turn = _count(raw["turn"], "turn")
        if not turn:
            raise _malformed("turn numbers start at 1")
        contributions = [{"speaker_id": player, "author": "player",
                          "text": _text(raw["player_text"], "player_text")}]
        for response in raw["responses"]:
            if not isinstance(response, Mapping) or set(response) != {
                    "response_id", "speaker_ref", "text"}:
                raise _malformed("a response needs exactly response_id, speaker_ref and text")
            response_id = _text(response["response_id"], "response_id")
            ref = _text(response["speaker_ref"], "speaker_ref")
            if ref == NARRATOR_REF:
                raise CaptureConversionError("narrator_not_in_profile",
                                             f"turn {turn}: the narrator spoke")
            actor_id = actor_map.get(ref)
            if actor_id is None or scenario.is_player(actor_id):
                raise CaptureConversionError("unknown_speaker",
                                             f"turn {turn}: response {response_id} has no NPC")
            if response_id in responses:
                raise _malformed(f"duplicate response {response_id}")
            if not isinstance(response["text"], str):
                raise _malformed("response text must be a string")
            responses[response_id] = (turn, actor_id)
            contributions.append({"speaker_id": actor_id, "author": "model",
                                  "text": response["text"],
                                  "call_id": f"t{turn}-{actor_id}"})
        turns.append({"turn": turn, "contributions": contributions})

    rows_by_response: dict[str, Mapping] = {}
    for index, row in enumerate(bundle["usage_rows"]):
        if not isinstance(row, Mapping):
            raise _malformed(f"usage row {index} is not an object")
        if row.get("kind") == "rename":
            continue
        response_id = row.get("response_id")
        if (row.get("kind") != "llm" or row.get("task") not in GENERATION_TASKS
                or not response_id or row.get("campaign") != bundle["campaign"]
                or row.get("scene") != bundle["scene"]):
            raise CaptureConversionError(
                "unplanned_v021_call",
                f"usage row {index} (task {row.get('task')!r}) is not a speaking NPC's "
                "generation in the captured scene")
        if response_id not in responses:
            raise CaptureConversionError("unknown_response",
                                         f"usage row {index} names no captured response")
        if response_id in rows_by_response:
            raise CaptureConversionError("multi_call_contribution",
                                         f"response {response_id} took more than one call")
        rows_by_response[response_id] = row

    calls = []
    for response_id, (turn, actor_id) in responses.items():
        row = rows_by_response.get(response_id)
        if row is None:
            raise CaptureConversionError("missing_usage_row",
                                         f"response {response_id} has no usage row")
        latency = row.get("duration_ms")
        calls.append({
            "call_id": f"t{turn}-{actor_id}", "turn": turn, "stage": "npc_generation",
            "actor_id": actor_id, "purpose": GENERATION_PURPOSE,
            "latency_ms": _count(latency, "duration_ms"),
            "attempts": _attempts(row, mode, route),
        })

    return {
        "format": FORMAT_V2,
        "run_id": run_id,
        "scenario_id": scenario.scenario_id,
        "label": label,
        "source": {"kind": "v0.2.1-capture", "mode": mode, "v1_commit": bundle["v1_commit"],
                   "harness": HARNESS, "evidence_sha256": digest, "profile": PROFILE,
                   "llm_retries": bundle["llm_retries"], "route": route},
        "conditions": {"model": _served_model(list(rows_by_response.values()), bundle["model"]),
                       "budget": bundle["budget"]},
        "policy": {"per_turn_call_caps": {}, "max_attempts_per_call": 1 + bundle["llm_retries"]},
        "turns": turns,
        "calls": calls,
        "choice_outcomes": dict(bundle["choice_outcomes"]),
    }


def verify_evidence(run: RunV2, bundle: Mapping, digest: str, scenario: Scenario) -> EvidenceCheck:
    """Whether ``bundle`` (whose digest the file edge computed) reproduces ``run`` exactly."""
    if run.source.evidence_sha256 != digest:
        return EvidenceCheck(False, "evidence digest does not match the run's source")
    try:
        again = parse_run_v2(convert(bundle, scenario, digest=digest, run_id=run.run_id,
                                     label=run.label), scenario)
    except ValueError as error:
        code = getattr(error, "code", "invalid")
        return EvidenceCheck(False, f"re-conversion failed ({code})")
    for name in ("source", "conditions", "policy", "turns", "calls", "choice_outcomes",
                 "judge_scores"):
        if getattr(again, name) != getattr(run, name):
            return EvidenceCheck(False, f"re-conversion does not reproduce the run's {name}")
    return EvidenceCheck(True, "digest and re-conversion match")
