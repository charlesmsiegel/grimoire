"""Golden-path scene-summary test backed by a checked-in LLM fixture.

Spec 17 §6: scene running summary is one of the spots where prose
realism matters, so we keep a tiny replay-mode test that verifies the
canonical prompt + fixture pair stays in sync. Re-record with::

    uv run pytest -m golden --record
"""

from __future__ import annotations

import pytest

from grimoire.testing.record_replay import RecordReplayLLM
from grimoire.types.llm import (
    CompletionRequest,
    Message,
    MessageRole,
)

pytestmark = [pytest.mark.golden, pytest.mark.integration]


_SUMMARY_PROMPT = (
    "Summarize: winifred enters the Elysium. She greets the Toreador "
    "primogen. They discuss the missing locket."
)
_SUMMARY_REQUEST = CompletionRequest(
    model="golden-summarizer-v1",
    system="You write a concise running summary of an RPG scene.",
    messages=[Message(role=MessageRole.USER, content=_SUMMARY_PROMPT)],
    max_tokens=256,
    temperature=0.3,
)


@pytest.mark.asyncio
async def test_scene_summary_golden_replay_returns_canned_summary(
    golden_llm: RecordReplayLLM,
) -> None:
    response = await golden_llm.complete(
        "scene.running_summary",
        _SUMMARY_REQUEST,
    )
    assert response.model == "golden-summarizer-v1"
    # The fixture summary mentions the three salient beats; assert the
    # core ones so a silent fixture edit is caught.
    assert "Elysium" in response.text
    assert "Toreador" in response.text
    assert "locket" in response.text
