"""Rule-based strategy tests."""

from __future__ import annotations

from grimoire.extractor.config import ExtractorConfig
from grimoire.extractor.rule_based import extract_rule_based
from grimoire.types.state import DeltaKind


def _kinds(deltas) -> list[DeltaKind]:
    return [d.kind for d in deltas]


def test_explicit_time_phrase_emits_time_advance():
    config = ExtractorConfig()
    deltas = list(extract_rule_based("Two hours passed.", campaign_id="c", config=config))
    assert any(d.kind == DeltaKind.TIME_ADVANCE for d in deltas)
    t = next(d for d in deltas if d.kind == DeltaKind.TIME_ADVANCE)
    assert t.after["duration"]["iso8601"] == "PT2H"
    assert t.confidence >= 0.85


def test_next_morning_phrase_emits_time_advance():
    config = ExtractorConfig()
    deltas = list(
        extract_rule_based("The next morning, julian rose.", campaign_id="c", config=config)
    )
    times = [d for d in deltas if d.kind == DeltaKind.TIME_ADVANCE]
    assert times, "expected a time_advance for 'the next morning'"
    assert times[0].after["duration"]["iso8601"] == "P1D"


def test_inventory_pick_up_emits_inventory_change():
    config = ExtractorConfig()
    deltas = list(
        extract_rule_based(
            "winifred picked up the silver ring.",
            campaign_id="c",
            config=config,
        )
    )
    inv = [d for d in deltas if d.kind == DeltaKind.INVENTORY_CHANGE]
    assert len(inv) == 1
    assert inv[0].after["item"].strip() == "silver ring"
    assert inv[0].after["action"] == "acquire"
    assert inv[0].after["holder"] == "winifred"
    # Inventory is intentionally below auto-apply threshold.
    assert inv[0].confidence == 0.8


def test_handed_emits_loss_direction():
    config = ExtractorConfig()
    deltas = list(
        extract_rule_based(
            "vivienne handed the silver ring to julian.",
            campaign_id="c",
            config=config,
        )
    )
    inv = [d for d in deltas if d.kind == DeltaKind.INVENTORY_CHANGE]
    assert inv and inv[0].extra["direction"] == "loss"
    assert inv[0].after["action"] == "drop"


def test_wound_phrase_emits_mechanical_event():
    config = ExtractorConfig()
    deltas = list(
        extract_rule_based(
            "julian took heavy damage to his shoulder.",
            campaign_id="c",
            config=config,
        )
    )
    mech = [d for d in deltas if d.kind == DeltaKind.MECHANICAL_EVENT]
    assert mech, "wound phrase should produce a MECHANICAL_EVENT delta"
    assert mech[0].after["event_kind"] == "wound"


def test_no_extraction_for_inert_text():
    config = ExtractorConfig()
    deltas = list(
        extract_rule_based(
            "The sky was a tired grey, with no particular activity at all.",
            campaign_id="c",
            config=config,
        )
    )
    assert _kinds(deltas) == []
