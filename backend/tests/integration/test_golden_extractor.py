"""Golden-path extractor test backed by a checked-in LLM fixture.

This is the spec 17 §6 minimum: prove the record/replay pipeline plus
the canned fixture round-trip through the structured-LLM extractor
surface for one realistic prompt. We skip if the extractor's public
surface isn't queryable in a way that lets a test feed it a gateway
directly — today the extractor accepts a ``gateway`` via constructor,
so the test runs.

Re-record (after a prompt template or model change) with::

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


# The canonical request the extractor fixture was recorded against.
# Keep these values in lock-step with
# ``backend/tests/fixtures/llm/by_hash/<sha256>.json`` — change one and
# you must re-record.
_EXTRACTOR_PROMPT = "winifred accepted the contract. The locket now hangs around her neck."
_EXTRACTOR_REQUEST = CompletionRequest(
    model="golden-extractor-v1",
    system="You extract structured state changes from RPG prose. Return JSON.",
    messages=[Message(role=MessageRole.USER, content=_EXTRACTOR_PROMPT)],
    max_tokens=512,
    temperature=0.0,
)


@pytest.mark.asyncio
async def test_extractor_golden_replay_returns_canned_response(
    golden_llm: RecordReplayLLM,
) -> None:
    """REPLAY-mode round-trip for the extractor's structured-LLM task.

    Verifies the request hash matches the checked-in fixture and the
    response carries the canonical extracted deltas (commitment added,
    inventory change). If this test starts failing with
    ``FixtureMissingError``, the prompt or model changed — re-record.
    """
    response = await golden_llm.complete(
        "extractor.structured_llm",
        _EXTRACTOR_REQUEST,
    )
    assert response.model == "golden-extractor-v1"
    # The fixture is JSON-shaped because the structured-LLM strategy
    # expects ``deltas`` / ``candidates`` keys. Check both delta kinds
    # the canonical fixture promises so a silent regeneration (e.g.
    # someone edited the JSON to drop a delta) trips this assertion.
    assert "commitment_added" in response.text
    assert "inventory_change" in response.text
    assert "silver locket" in response.text
