"""Retcon replay events are present on the ``EventType`` enum."""

from __future__ import annotations

from grimoire.types.orchestrator import EventType


def test_retcon_event_types_present() -> None:
    assert EventType.RETCON_STARTED.value == "retcon_started"
    assert EventType.RETCON_POST_REPLAYED.value == "retcon_post_replayed"
    assert EventType.RETCON_POST_ACCEPTED.value == "retcon_post_accepted"
    assert EventType.RETCON_CANCELLED.value == "retcon_cancelled"
    assert EventType.RETCON_COMPLETE.value == "retcon_complete"
