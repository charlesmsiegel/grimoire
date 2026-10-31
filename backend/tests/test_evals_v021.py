"""0.2.1 capture evidence -> grimoire.story-quality/2, and evidence verification."""

import copy

import pytest

from evals_helpers import V021_COMMIT, bundle, load_benchmark, minimal_scenario, v021_row
from grimoire.core.inference import Measurement
from grimoire.evals.format import parse_scenario
from grimoire.evals.format2 import parse_run_v2
from grimoire.evals.loader import evidence_digest
from grimoire.evals.v021 import (
    EVIDENCE_FORMAT, HARNESS, PINNED_V021_COMMIT, CaptureConversionError, convert,
    verify_evidence,
)

SCENARIO = parse_scenario(minimal_scenario())


def run_of(data: dict, scenario=SCENARIO):
    raw = convert(data, scenario, digest=evidence_digest(data), run_id="cap", label="capture")
    return raw, parse_run_v2(raw, scenario)


def refused(data: dict, code: str, scenario=SCENARIO) -> None:
    with pytest.raises(CaptureConversionError) as error:
        convert(data, scenario, digest=evidence_digest(data), run_id="cap", label="capture")
    assert error.value.code == code, error.value


def test_constants():
    assert EVIDENCE_FORMAT == "grimoire.v021-capture-evidence/1"
    assert PINNED_V021_COMMIT == V021_COMMIT
    assert HARNESS == "grimoire.capture/1"


def guild_bundle(mode="dry_run") -> tuple:
    raw_scenario, _ = load_benchmark()
    scenario = parse_scenario(raw_scenario)
    turns, rows = [], []
    for turn, choice in enumerate(raw_scenario["choices"], start=1):
        responses = []
        for speaker in ("oda", "bellamy"):
            rid = f"r-{turn}-{speaker}"
            responses.append({"response_id": rid, "speaker_ref": f"characters:{speaker}",
                              "text": f"{speaker} answers about the guest ledger, balcony and song."})
            rows.append(v021_row(rid, 1000 + turn, 40, cache_read=400))
        turns.append({"turn": turn, "player_text": choice["text"], "responses": responses})
    data = bundle(scenario_id="guild-betrayal", mode=mode, turns=turns, usage_rows=rows,
                  actor_map={"characters:oda": "npc-oda", "characters:bellamy": "npc-bellamy",
                             "pcs:wren": "pc-wren"},
                  choice_outcomes={c["choice_id"]: "unlabelled" if mode == "live" else "honored"
                                   for c in raw_scenario["choices"]})
    return data, scenario


def test_two_chat_rows_per_turn_convert_to_two_npc_calls():
    data, scenario = guild_bundle()
    raw, run = run_of(data, scenario)
    assert len(run.calls) == 6 and {c.stage.value for c in run.calls} == {"npc_generation"}
    assert [len(s.by_actor) for s in run.summaries] == [2, 2, 2]
    first = raw["turns"][0]["contributions"]
    assert [c["speaker_id"] for c in first] == ["pc-wren", "npc-oda", "npc-bellamy"]
    assert first[0]["author"] == "player" and first[0]["text"] == data["turns"][0]["player_text"]
    assert raw["conditions"] == {"model": "vendor-model", "budget": "max_tokens=400"}
    assert raw["policy"] == {"per_turn_call_caps": {}, "max_attempts_per_call": 3}
    assert raw["source"] == {"kind": "v0.2.1-capture", "mode": "dry_run",
                             "v1_commit": V021_COMMIT, "harness": HARNESS,
                             "evidence_sha256": evidence_digest(data), "profile": "two-call",
                             "llm_retries": 2, "route": "openrouter:vendor-model"}
    call = raw["calls"][0]
    assert call["latency_ms"] == 50
    assert call["attempts"][0]["usage"] == {"input_tokens": 1001, "output_tokens": 40,
                                            "cache_read_tokens": 400, "cache_write_tokens": None,
                                            "reasoning_tokens": None}
    assert call["attempts"][0]["route"] == "openrouter:vendor-model"


def test_dry_run_rows_are_synthetic_even_if_counts_present():
    _, run = run_of(bundle())
    assert run.usage.measurements == {Measurement.SYNTHETIC}
    assert not run.usage.provider_reported_only


def live_bundle(**overrides) -> dict:
    return bundle(mode="live", choice_outcomes={"c1": "unlabelled"}, **overrides)


def test_live_rows_are_provider_reported():
    _, run = run_of(live_bundle(usage_rows=[v021_row("r-1", cost_usd=0.002, cost_basis="billed")]))
    assert run.usage.provider_reported_only
    assert run.calls[0].costs[0].usd == 0.002 and run.calls[0].costs[0].basis == "billed"


def test_tokens_estimated_row_is_estimated():
    _, run = run_of(live_bundle(usage_rows=[v021_row("r-1", tokens_estimated=True)]))
    assert run.usage.measurements == {Measurement.ESTIMATED}


def test_retry_row_books_unreported_failed_attempts():
    raw, run = run_of(live_bundle(usage_rows=[v021_row("r-1", attempts=3)]))
    assert [a["outcome"] for a in raw["calls"][0]["attempts"]] == ["failed", "failed", "completed"]
    assert {a["measurement"] for a in raw["calls"][0]["attempts"]} == {"provider_reported"}
    assert run.usage.total.unreported["input_tokens"] == 2
    assert not run.usage.provider_reported_only


def test_attempts_default_to_one():
    raw, _ = run_of(bundle())
    assert [a["attempt"] for a in raw["calls"][0]["attempts"]] == [1]


def test_tracker_row_is_unplanned_v021_call():
    refused(bundle(usage_rows=[v021_row("r-1"), v021_row("", task="tracker-update")]),
            "unplanned_v021_call")


def test_chat_row_without_response_is_unplanned():
    row = v021_row("r-1")
    del row["response_id"]
    refused(bundle(usage_rows=[row]), "unplanned_v021_call")


def test_continuation_row_is_multi_call_contribution():
    refused(bundle(usage_rows=[v021_row("r-1"), v021_row("r-1", task="continuation")]),
            "multi_call_contribution")


def test_narrator_row_refused():
    data = bundle()
    data["turns"][0]["responses"][0]["speaker_ref"] = "grimoire"
    refused(data, "narrator_not_in_profile")


def test_unknown_speaker_refused():
    data = bundle()
    data["turns"][0]["responses"][0]["speaker_ref"] = "characters:nobody"
    refused(data, "unknown_speaker")


def test_rename_rows_are_ignored_but_digested():
    rename = {"ts": "2026-10-31T00:00:00Z", "kind": "rename", "campaign": "c-1", "from": "a",
              "to": "b"}
    plain = bundle()
    with_rename = bundle(usage_rows=[v021_row("r-1"), rename])
    renamed_again = bundle(usage_rows=[v021_row("r-1"), dict(rename, to="c")])
    assert run_of(plain)[1].calls == run_of(with_rename)[1].calls
    assert evidence_digest(with_rename) != evidence_digest(renamed_again)


@pytest.mark.parametrize("change", [{"campaign": "c-2"}, {"scene": "s-2"}, {"campaign": None}])
def test_foreign_row_is_refused(change):
    row = v021_row("r-1")
    for key, value in change.items():
        if value is None:
            del row[key]
        else:
            row[key] = value
    refused(bundle(usage_rows=[row]), "unplanned_v021_call")


def test_missing_usage_row_refused():
    refused(bundle(usage_rows=[]), "missing_usage_row")


def test_unknown_response_refused():
    refused(bundle(usage_rows=[v021_row("r-1"), v021_row("r-9")]), "unknown_response")


def test_aborted_row_is_cancelled():
    raw = convert(bundle(usage_rows=[v021_row("r-1", status="aborted")]), SCENARIO,
                  digest="0" * 64, run_id="cap", label="capture")
    assert raw["calls"][0]["attempts"][-1]["outcome"] == "cancelled"
    raw = convert(bundle(usage_rows=[v021_row("r-1", status="error")]), SCENARIO,
                  digest="0" * 64, run_id="cap", label="capture")
    assert raw["calls"][0]["attempts"][-1]["outcome"] == "failed"


def test_live_with_labels_is_mode_label_conflict():
    refused(bundle(mode="live"), "mode_label_conflict")


def test_wrong_commit_refused():
    refused(bundle(v1_commit="1" * 40), "pinned_commit_mismatch")


def test_malformed_evidence_refused():
    refused(bundle(extra="x"), "malformed_evidence")
    refused(bundle(mode="replay"), "malformed_evidence")
    refused(bundle(provider_kind="claude"), "malformed_evidence")
    refused(bundle(format="other"), "malformed_evidence")


def test_matching_evidence_verifies():
    data = live_bundle()
    _, run = run_of(data)
    check = verify_evidence(run, data, evidence_digest(data), SCENARIO)
    assert check.ok, check.reason


def test_tampered_evidence_fails_verification():
    data = live_bundle()
    _, run = run_of(data)
    tampered = copy.deepcopy(data)
    tampered["usage_rows"][0]["prompt_tokens"] += 1
    check = verify_evidence(run, tampered, evidence_digest(tampered), SCENARIO)
    assert not check.ok and "digest" in check.reason


def test_reconversion_mismatch_fails_verification():
    data = live_bundle()
    _, run = run_of(data)
    edited = copy.deepcopy(data)
    edited["usage_rows"][0]["prompt_tokens"] += 1
    digest = evidence_digest(edited)
    forged = parse_run_v2({**convert(data, SCENARIO, digest=evidence_digest(data), run_id="cap",
                                     label="capture"),
                           "source": {**run_of(edited)[0]["source"]}}, SCENARIO)
    assert forged.source.evidence_sha256 == digest
    check = verify_evidence(forged, edited, digest, SCENARIO)
    assert not check.ok and "re-conversion" in check.reason


def test_unconvertible_evidence_fails_verification():
    data = live_bundle()
    _, run = run_of(data)
    broken = live_bundle(usage_rows=[])
    check = verify_evidence(run, broken, run.source.evidence_sha256, SCENARIO)
    assert not check.ok


# ---- final-review fixes ------------------------------------------------------------------

@pytest.mark.parametrize("change", [
    lambda b: b["turns"][0].update(responses=None),
    lambda b: b["turns"][0]["responses"][0].update(response_id=["unhashable"]),
    lambda b: b.update(actor_map={"characters:ana": ["npc-a"]}),
    lambda b: b.update(turns=[None]),
    lambda b: b["usage_rows"][0].update(prompt_tokens="many"),
])
def test_malformed_bundle_shapes_are_malformed_evidence(change):
    data = bundle()
    change(data)
    refused(data, "malformed_evidence")


def test_conditions_name_the_model_that_served():
    data = bundle(model="vendor/model", usage_rows=[v021_row(
        "r-1", model="vendor-model-2026-01", requested_model="vendor/model")])
    raw, _ = run_of(data)
    assert raw["conditions"]["model"] == "vendor-model-2026-01"


def test_a_row_served_by_another_model_is_refused():
    refused(bundle(model="vendor/model", usage_rows=[v021_row("r-1", model="fallback-model")]),
            "model_mismatch")


def test_rows_must_agree_on_the_served_model():
    data, scenario = guild_bundle()
    data["model"] = "vendor/model"
    for index, row in enumerate(data["usage_rows"]):
        row["requested_model"] = "vendor/model"
        row["model"] = "snapshot-a" if index else "snapshot-b"
    refused(data, "model_mismatch", scenario)


def test_cost_without_basis_is_unreported():
    raw, _ = run_of(live_bundle(usage_rows=[v021_row("r-1", cost_usd=0.002, cost_basis="")]))
    assert "cost" not in raw["calls"][0]["attempts"][-1]


def test_verification_covers_judge_scores():
    data = live_bundle()
    raw, run = run_of(data)
    scored = parse_run_v2({**raw, "judge_scores": {"fluency": 0.9}}, SCENARIO)
    check = verify_evidence(scored, data, evidence_digest(data), SCENARIO)
    assert not check.ok and "judge_scores" in check.reason
