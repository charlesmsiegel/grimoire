"""Tests for deterministic per-campaign RNG seed derivation."""

from __future__ import annotations

from grimoire.mechanics.rng import INT64_MASK, derive_roll_seed


def test_same_inputs_yield_same_seed() -> None:
    a = derive_roll_seed(campaign_seed=1234, roll_seed=42, roll_id="roll-1")
    b = derive_roll_seed(campaign_seed=1234, roll_seed=42, roll_id="roll-1")
    assert a == b


def test_changing_any_input_changes_the_seed() -> None:
    base = derive_roll_seed(campaign_seed=1234, roll_seed=42, roll_id="roll-1")
    assert base != derive_roll_seed(campaign_seed=9999, roll_seed=42, roll_id="roll-1")
    assert base != derive_roll_seed(campaign_seed=1234, roll_seed=99, roll_id="roll-1")
    assert base != derive_roll_seed(campaign_seed=1234, roll_seed=42, roll_id="roll-2")


def test_seed_is_non_negative_63_bit() -> None:
    seed = derive_roll_seed(campaign_seed=2**60, roll_seed=2**60, roll_id="x")
    assert 0 <= seed <= INT64_MASK


def test_roll_id_is_optional() -> None:
    assert derive_roll_seed(1, 2) == derive_roll_seed(1, 2)
    assert derive_roll_seed(1, 2) != derive_roll_seed(1, 2, "with-id")
