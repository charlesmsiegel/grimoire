"""``RetconResult`` carries both the leave-as-is and replay outcomes
(spec 2026-05-19-retcon-design §Backend surface)."""

from __future__ import annotations

from grimoire.types.orchestrator import RetconResult


def test_leave_as_is_defaults() -> None:
    r = RetconResult(
        post_id="p_1",
        original_text="orig",
        new_text="new",
        reversed_delta_ids=["d_a"],
        new_delta_ids=["d_b"],
        downstream_flagged_turns=["t_2"],
    )
    assert r.replay_batch_id is None
    assert r.replayed_post_ids == []
    assert r.cancelled_at_post_id is None
    assert r.contradictions_detected == []


def test_replay_fields_populated() -> None:
    r = RetconResult(
        post_id="p_1",
        original_text="orig",
        new_text="new",
        replay_batch_id="rb_a1b2",
        replayed_post_ids=["p_2", "p_3"],
        cancelled_at_post_id=None,
        contradictions_detected=["cr_1"],
    )
    assert r.replay_batch_id == "rb_a1b2"
    assert r.replayed_post_ids == ["p_2", "p_3"]
    assert r.contradictions_detected == ["cr_1"]


def test_model_dump_roundtrip_includes_replay_fields() -> None:
    r = RetconResult(
        post_id="p_1",
        original_text="o",
        new_text="n",
        replay_batch_id="rb_x",
        replayed_post_ids=["p_2"],
    )
    payload = r.model_dump(mode="json")
    assert payload["replay_batch_id"] == "rb_x"
    assert payload["replayed_post_ids"] == ["p_2"]
    assert payload["cancelled_at_post_id"] is None
