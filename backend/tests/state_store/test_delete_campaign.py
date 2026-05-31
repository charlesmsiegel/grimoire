"""Deleting a campaign cascades to every campaign-scoped table (no orphans)."""

from __future__ import annotations


async def _add_embedding(store, campaign_id: str, ref: str) -> None:
    await store.add_embedding(
        ref=ref,
        scope="campaign",
        source_kind="scene_body",
        text="hello",
        vector=[0.1, 0.2, 0.3],
        model="test-model",
        campaign_id=campaign_id,
    )


async def _count(store, table: str, campaign_id: str) -> int:
    row = await store.db.fetchone(
        f"SELECT COUNT(*) AS n FROM {table} WHERE campaign_id = ?", (campaign_id,)
    )
    return int(row["n"])


async def test_delete_campaign_cascades_and_isolates(store) -> None:
    await store.upsert_campaign(campaign_id="doomed", name="Doomed")
    await store.upsert_campaign(campaign_id="keep", name="Keep")
    await _add_embedding(store, "doomed", "campaigns/doomed/emergent/x")
    await _add_embedding(store, "keep", "campaigns/keep/emergent/y")

    assert await _count(store, "embeddings", "doomed") == 1
    assert await _count(store, "embeddings", "keep") == 1

    await store.delete_campaign("doomed")

    # Deleted campaign: row gone AND its derived rows gone.
    assert await store.get_campaign_row("doomed") is None
    assert await _count(store, "embeddings", "doomed") == 0
    # Other campaign: completely untouched.
    assert await store.get_campaign_row("keep") is not None
    assert await _count(store, "embeddings", "keep") == 1
