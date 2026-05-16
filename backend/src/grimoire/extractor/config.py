"""Extractor configuration (spec 04 §Configuration)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExtractorConfig:
    """Tunable thresholds for the extractor pipeline.

    Defaults mirror spec 04 §Configuration. The `parallel_strategies`
    list lets callers disable individual strategies (useful in tests and
    for fast-path setups).
    """

    task_name: str = "extractor"
    parallel_strategies: tuple[str, ...] = (
        "rule_based",
        "structured_llm",
        "heuristic_flags",
    )
    auto_apply_threshold: float = 0.85
    review_threshold: float = 0.60
    timeout_seconds: float = 30.0
    max_new_entities_per_turn: int = 5
    # LLM response is parsed as JSON; if the model emits a markdown fence
    # we still try to recover (see `_extract_json_payload`).
    llm_max_output_tokens: int = 2048
    llm_temperature: float = 0.0
    # Player-text rule of thumb (spec 04 §Handling player text): things players
    # declare about their own PC are taken at face value; declarations about
    # others/world are flagged. Confidences below this floor get clamped down
    # for non-PC subjects in player text.
    player_other_subject_confidence_cap: float = 0.7
    # Penalty applied when a delta's confidence is reduced for contradicting
    # an established fact (spec 04 §Confidence scoring).
    contradiction_confidence_penalty: float = 0.25
    # Penalty applied to facts speaking as a character (testimony) vs. narrator.
    testimony_confidence_penalty: float = 0.1
    # How many times to retry the structured-LLM call when the first response
    # is unparseable JSON (spec 04 §Configuration). 0 disables retries.
    retry_on_parse_failure: int = 1
    # Strategy base confidences.
    rule_based_base_confidence: float = 0.95
    # Tags applied to deltas by source attribution.
    strategy_tags: dict[str, str] = field(
        default_factory=lambda: {
            "rule_based": "extractor:rule",
            "structured_llm": "extractor:llm",
            "heuristic_flags": "extractor:heuristic",
        }
    )
