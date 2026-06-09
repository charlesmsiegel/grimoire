"""ArchiveRetriever failure paths log at WARNING instead of failing silently (#587).

Archive retrieval may degrade (return no items) when a dependency breaks, but
the degradation must be observable: every swallowed exception logs a WARNING
with campaign context so "no archive hits" is distinguishable from "retrieval
broke".
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from grimoire.context.archive import ArchiveRetriever
from grimoire.context.config import ContextBuilderConfig

CAMPAIGN = "camp-1"


def _retriever(**kwargs: Any) -> ArchiveRetriever:
    return ArchiveRetriever(config=ContextBuilderConfig(), **kwargs)


class _RaisingWorld:
    async def lore_for_post(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise RuntimeError("lore lookup broke")


class _LegacyRaisingWorld:
    """Rejects the turn_id kwarg (legacy signature), then breaks on retry."""

    async def lore_for_post(self, player_input: str, campaign_id: str) -> list[Any]:
        raise RuntimeError("legacy lore lookup broke")


class _RaisingGateway:
    async def embed(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise RuntimeError("embedding provider down")


class _RaisingStore:
    async def vector_search(self, **kwargs: Any) -> list[Any]:
        raise RuntimeError("vector table missing")

    async def keyword_search(self, **kwargs: Any) -> list[Any]:
        raise RuntimeError("fts table missing")


class _RaisingScenes:
    async def get_scene(self, scene_id: str) -> Any:
        raise RuntimeError("scene store broke")


async def test_lore_trigger_failure_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    retriever = _retriever(world=_RaisingWorld())
    with caplog.at_level(logging.WARNING, logger="grimoire.context.archive"):
        result = await retriever.lore_triggers("the ancient pact", CAMPAIGN)
    assert result == ([], [], [])
    assert any(
        "lore_for_post failed" in r.message and CAMPAIGN in r.message for r in caplog.records
    )


async def test_lore_trigger_legacy_retry_failure_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    retriever = _retriever(world=_LegacyRaisingWorld())
    with caplog.at_level(logging.WARNING, logger="grimoire.context.archive"):
        result = await retriever.lore_triggers("the ancient pact", CAMPAIGN, turn_id="t-1")
    assert result == ([], [], [])
    assert any("lore_for_post failed" in r.message for r in caplog.records)


async def test_embed_failure_logs_warning_and_skips_vector_search(
    caplog: pytest.LogCaptureFixture,
) -> None:
    retriever = _retriever(gateway=_RaisingGateway(), state_store=_RaisingStore())
    with caplog.at_level(logging.WARNING, logger="grimoire.context.archive"):
        hits = await retriever._vector_search("query", CAMPAIGN)
    assert hits == []
    assert any(
        "query embedding failed" in r.message and CAMPAIGN in r.message for r in caplog.records
    )


async def test_keyword_search_failure_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    retriever = _retriever(state_store=_RaisingStore())
    with caplog.at_level(logging.WARNING, logger="grimoire.context.archive"):
        hits = await retriever._keyword_search("query", CAMPAIGN)
    assert hits == []
    assert any(
        "keyword_search failed" in r.message and CAMPAIGN in r.message for r in caplog.records
    )


async def test_scene_ref_lookup_failure_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    retriever = _retriever(scenes=_RaisingScenes())
    with caplog.at_level(logging.WARNING, logger="grimoire.context.archive"):
        items = await retriever.scene_refs_from_input("recall scene:intro-1", CAMPAIGN)
    # Degrades to the "not found" rendering, but says so in the log.
    assert len(items) == 1
    assert "(not found)" in items[0].text
    assert any("get_scene(intro-1) failed" in r.message for r in caplog.records)
