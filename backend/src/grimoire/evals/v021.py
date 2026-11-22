"""Turn a 0.2.1 capture's evidence bundle into a ``grimoire.story-quality/2`` run, and verify it.

The bundle is what the opt-in capture harness (``grimoire.capture.v021``) read back from an
unmodified 0.2.1 app after a scripted synthetic scene: the app's own usage-ledger rows and the
contributions it produced. No prompt text and no credential is in it.

Conversion fails closed. Under the two-call profile every model call 0.2.1 made must be one
speaking NPC's generation, so any other row (tracker, decide, summary, embedding...) is an
unplanned call, not something to drop. 0.2.1 records only the attempt that served a call, so
its earlier attempts are booked as failed with unreported usage: missing, never free.

A ``/2`` bundle also lists ``retries``: each time 0.2.1 rejected a reply as empty
(``empty_response``) and the harness pressed 0.2.1's own Retry, which asks the same prompt again
under the same response id. Such a response has one usage row per reply 0.2.1 received, in
ledger order: exactly one more than its listed Retries. The last is the reply 0.2.1 kept and
the run's call; the rejected ones are reported (``rejected_replies``) but kept out of the run,
so the run is the cost of the play 0.2.1 produced, a lower bound on what its player paid.

Pure: the evidence digest is computed by the file edge (``loader.evidence_digest``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .format import Scenario
from .format2 import FORMAT_V2, RunV2, parse_run_v2

EVIDENCE_FORMAT = "grimoire.v021-capture-evidence/1"
#: ``/1`` plus ``retries``: the 0.2.1 Retries the harness pressed on rejected (empty) replies.
EVIDENCE_FORMAT_RETRIES = "grimoire.v021-capture-evidence/2"
EVIDENCE_FORMATS = (EVIDENCE_FORMAT, EVIDENCE_FORMAT_RETRIES)
#: The one rejection a Retry may follow: a reply 0.2.1 kept no prose of.
RETRIED_KIND = "empty_response"
PINNED_V021_COMMIT = "e4c4ec4abacd0beb87fc9f1cdd06e6787d5a88ce"
HARNESS = "grimoire.capture/1"
PROFILE = "two-call"
PROVIDER_KINDS = ("openrouter", "anthropic")
MODES = ("dry_run", "live")
#: 0.2.1's narrator speaker (``routes/character_turns/_state.py``). The profile excludes it.
NARRATOR_REF = "grimoire"
#: The task of an NPC's one generation. A ``continuation`` (a roll's second call) is never part of
#: the two-call profile, with or without its first call.
GENERATION_TASK = "chat"
CONTINUATION_TASK = "continuation"
PARTIAL_FIELDS = ("prompt_tokens", "completion_tokens", "cache_read_tokens",
                  "cache_write_tokens", "cost_usd")
GENERATION_PURPOSE = "speaking NPC contribution"
BUNDLE_KEYS = ("format", "scenario_id", "mode", "provider_kind", "v1_commit", "profile",
               "llm_retries", "model", "budget", "campaign", "scene", "actor_map",
               "choice_outcomes", "turns", "usage_rows")
BUNDLE_KEYS_RETRIES = (*BUNDLE_KEYS, "retries")


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
    if bundle.get("format") not in EVIDENCE_FORMATS:
        raise _malformed(f"format must be one of {', '.join(map(repr, EVIDENCE_FORMATS))}")
    expected = set(BUNDLE_KEYS if bundle["format"] == EVIDENCE_FORMAT else BUNDLE_KEYS_RETRIES)
    keys = set(bundle)
    if keys != expected:
        extra, missing = sorted(keys - expected), sorted(expected - keys)
        raise _malformed(f"unknown keys {extra}" if extra else f"missing keys {missing}")
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
    if not isinstance(bundle.get("retries", []), list):
        raise _malformed("retries must be a list")


def _retries(bundle: Mapping, responses: Mapping[str, tuple[int, str]]) -> dict[str, int]:
    """How many times each response was rejected as empty and retried (``/2``; none in ``/1``)."""
    counts: dict[str, int] = {}
    for entry in bundle.get("retries", []):
        if not isinstance(entry, Mapping) or set(entry) != {"turn", "response_id", "kind"}:
            raise _malformed("a retry needs exactly turn, response_id and kind")
        if entry["kind"] != RETRIED_KIND:
            raise CaptureConversionError("unplanned_retry",
                                         f"a retry follows {RETRIED_KIND} only")
        response_id = _text(entry["response_id"], "response_id")
        if response_id not in responses or responses[response_id][0] != _count(
                entry["turn"], "turn"):
            raise CaptureConversionError("unknown_response",
                                         f"retry of {response_id} names no captured response "
                                         "of that turn")
        counts[response_id] = counts.get(response_id, 0) + 1
    return counts


def rejected_replies(bundle: Mapping) -> dict[str, int]:
    """What the replies 0.2.1 rejected and retried cost, summed: counts only, never text.

    Valid only for a bundle ``convert`` accepts. Keys: ``replies``, ``requests`` (their
    transport attempts) and the token fields, each summed over the reported counts, with
    ``*_unreported`` counting the replies that left one out. 0.2.1 counts only the attempt that
    served a reply, so ``attempts_unreported`` counts the earlier transport attempts, whose usage
    is unknown: missing, never free.
    """
    retried: dict[str, int] = {}
    for entry in bundle.get("retries", []):
        retried[entry["response_id"]] = retried.get(entry["response_id"], 0) + 1
    totals = {"replies": 0, "requests": 0, "attempts_unreported": 0}
    fields = ("prompt_tokens", "completion_tokens", "cache_read_tokens")
    for name in fields:
        totals[name] = totals[f"{name}_unreported"] = 0
    seen: dict[str, int] = {}
    for row in bundle["usage_rows"]:
        response_id = row.get("response_id")
        if row.get("kind") != "llm" or response_id not in retried:
            continue
        seen[response_id] = seen.get(response_id, 0) + 1
        if seen[response_id] > retried[response_id]:
            continue  # the last row is the reply 0.2.1 kept
        totals["replies"] += 1
        totals["requests"] += row.get("attempts", 1)
        totals["attempts_unreported"] += row.get("attempts", 1) - 1
        for name in fields:
            if row.get(name) is None:
                totals[f"{name}_unreported"] += 1
            else:
                totals[name] += row[name]
    return totals


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


def describe_rejected(spent: Mapping[str, int]) -> str:
    """``rejected_replies`` totals as one clause of counts: requests, then tokens."""
    def plural(count: int, word: str) -> str:
        return f"{count} {word}{'' if count == 1 else 's'}"
    counts = ", ".join(
        f"{label} {spent[name]}" + (f" ({spent[name + '_unreported']} unreported)"
                                    if spent[name + "_unreported"] else "")
        for name, label in (("prompt_tokens", "input tokens"),
                            ("completion_tokens", "output tokens"),
                            ("cache_read_tokens", "cache reads")))
    earlier = spent["attempts_unreported"]
    return (f"{plural(spent['requests'], 'request')}, {counts}"
            + (f"; {plural(earlier, 'earlier transport attempt')} whose usage 0.2.1 did not record (unreported)"
               if earlier else ""))


def convert(bundle: Mapping, scenario: Scenario, *, digest: str, run_id: str,
            label: str) -> dict:
    """The /2 run dict for ``bundle``. Validate the result with ``parse_run_v2``."""
    try:
        return _convert(bundle, scenario, digest=digest, run_id=run_id, label=label)
    except (TypeError, AttributeError, KeyError) as error:  # a shape no check above expected
        raise _malformed(f"unexpected structure ({type(error).__name__})") from None


def _served_model(rows: list[tuple[int, Mapping]], requested: str) -> str:
    """The one model that answered every call, recorded as served.

    A row is accepted when it names the requested model or 0.2.1 recorded the request as the
    requested model (a dated snapshot, or a route the provider resolved); every call must have
    been served by the same model.
    """
    served = set()
    for index, row in rows:  # index: the row's place in the ledger
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

    retried = _retries(bundle, responses)
    rows_by_response: dict[str, Mapping] = {}
    rows_of: dict[str, list[Mapping]] = {}
    served_rows: list[tuple[int, Mapping]] = []
    seen: dict[str, int] = {}
    for index, row in enumerate(bundle["usage_rows"]):
        if not isinstance(row, Mapping):
            raise _malformed(f"usage row {index} is not an object")
        if row.get("kind") == "rename":
            continue
        response_id = row.get("response_id")
        if row.get("kind") == "llm" and row.get("task") == CONTINUATION_TASK:
            raise CaptureConversionError("multi_call_contribution",
                                         f"usage row {index} is a continuation: a contribution "
                                         "took more than one call")
        if (row.get("kind") != "llm" or row.get("task") != GENERATION_TASK
                or not response_id or row.get("campaign") != bundle["campaign"]
                or row.get("scene") != bundle["scene"]):
            raise CaptureConversionError(
                "unplanned_v021_call",
                f"usage row {index} (task {row.get('task')!r}) is not a speaking NPC's "
                "generation in the captured scene")
        if response_id not in responses:
            raise CaptureConversionError("unknown_response",
                                         f"usage row {index} names no captured response")
        seen[response_id] = seen.get(response_id, 0) + 1
        if seen[response_id] > 1 + retried.get(response_id, 0):
            raise CaptureConversionError("multi_call_contribution",
                                         f"response {response_id} took more than one call")
        _attempts(row, mode, route)  # every row's counts are checked, kept or rejected
        rows_of.setdefault(response_id, []).append(row)
        served_rows.append((index, row))
        rows_by_response[response_id] = row  # in ledger order: the last is the reply kept
    for response_id, count in retried.items():
        if seen.get(response_id, 0) != 1 + count:
            raise CaptureConversionError("retry_unaccounted",
                                         f"response {response_id} was retried {count} times but "
                                         f"has {seen.get(response_id, 0)} usage rows")
        if any(row.get("status", "ok") != "ok" for row in rows_of[response_id][:-1]):
            # 0.2.1 books a reply it rejected as empty as served (ok); anything else is a
            # failed call, which a Retry does not account for.
            raise CaptureConversionError("retry_unaccounted",
                                         f"a rejected reply of {response_id} is not booked ok")

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
        "conditions": {"model": _served_model(served_rows, bundle["model"]),
                       "budget": bundle["budget"]},
        "policy": {"per_turn_call_caps": {}, "max_attempts_per_call": 1 + bundle["llm_retries"]},
        "turns": turns,
        "calls": calls,
        "choice_outcomes": dict(bundle["choice_outcomes"]),
    }


def partial_ledger(rows: object) -> list[dict]:
    """What a capture's 0.2.1 rows say was spent, as ids and counts only (never text)."""
    if not isinstance(rows, list):
        return []
    return [{"task": row.get("task"), "response_id": row.get("response_id"),
             "attempts": row.get("attempts", 1), "status": row.get("status"),
             **{name: row.get(name) for name in PARTIAL_FIELDS}}
            for row in rows if isinstance(row, Mapping) and row.get("kind") == "llm"]


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
