"""Spotlight-tier compact stanza rendering."""

from __future__ import annotations

from datetime import UTC, datetime

from grimoire.transient_state.stanza import render_transient_stanza
from grimoire.types.transient import Provenance, TransientValue


def _v(field: str, value: object) -> TransientValue:
    return TransientValue(
        id=1,
        entity_id="char_florence",
        field=field,
        value=value,
        provenance=Provenance.EXTRACTOR_AUTO,
        confidence=0.9,
        source_post_id=None,
        created_at=datetime.now(UTC),
        expires_at=None,
        in_game_at=None,
        decayed=False,
    )


def test_renders_basic_stanza():
    bundle = {
        "mood": _v("mood", "guarded"),
        "intent": _v("intent", "hide letter"),
        "current_action": _v("current_action", "fastening her cloak"),
        "internal_thought": _v("internal_thought", "silence is louder"),
    }
    text = render_transient_stanza("winifred Allard", bundle)
    assert "winifred Allard — current state:" in text
    assert "mood: guarded" in text
    assert "intent: hide letter" in text
    assert "action: fastening her cloak" in text
    assert "thinking: silence is louder" in text


def test_empty_bundle_returns_empty_string():
    assert render_transient_stanza("winifred", {}) == ""


def test_omits_unknown_fields():
    bundle = {"mood": _v("mood", "guarded")}
    text = render_transient_stanza("F", bundle)
    assert "mood: guarded" in text
    assert "intent" not in text
    assert "action" not in text


def test_filters_internal_thought_when_absent():
    """Caller is expected to have privacy-filtered the bundle; stanza renders what's there."""
    bundle = {
        "mood": _v("mood", "calm"),
        # internal_thought has been stripped upstream
    }
    text = render_transient_stanza("F", bundle)
    assert "thinking" not in text
    assert "mood: calm" in text


def test_handles_complex_values():
    bundle = {
        "focus_of_attention": _v("focus_of_attention", ["door", "winifred"]),
    }
    text = render_transient_stanza("F", bundle)
    assert "focus: door, winifred" in text


def test_value_stripping():
    bundle = {"mood": _v("mood", "  shaky   ")}
    text = render_transient_stanza("F", bundle)
    assert "mood: shaky" in text
