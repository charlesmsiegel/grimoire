import pytest

from grimoire.event_bus import EventBus
from grimoire.inventory import events as inv_events
from grimoire.inventory.models import HolderKind, InventoryAction, InventoryOperation
from grimoire.inventory.service import InventoryService

pytestmark = pytest.mark.asyncio


async def _enable(store):
    await store.upsert_campaign(campaign_id="c1", name="C")
    await store.set_campaign_config("c1", {"inventory": {"enabled": True, "flag_threshold": 0.6}})


async def test_disabled_campaign_is_noop(store):
    await store.upsert_campaign(campaign_id="c1", name="C")
    svc = InventoryService(store=store, event_bus=EventBus())
    op = InventoryOperation(
        action=InventoryAction.ACQUIRE, item="ring", holder="joe", confidence=1.0
    )
    res = await svc.apply(campaign_id="c1", turn_id="t1", operations=[op])
    assert res is None  # disabled -> no-op
    assert await store.list_inventory_holdings("c1") == []


async def test_acquire_persists_and_emits(store):
    await _enable(store)
    await store.write_emergent(
        campaign_id="c1",
        kind="character",
        entity_id="joe",
        frontmatter={"id": "joe", "name": "Joe"},
        body="",
        source="test",
    )
    bus = EventBus()
    seen = []
    bus.subscribe(inv_events.INVENTORY_CHANGED, lambda e: seen.append(e))
    svc = InventoryService(store=store, event_bus=bus)
    op = InventoryOperation(
        action=InventoryAction.ACQUIRE,
        item="silver ring",
        holder="joe",
        quantity=1,
        confidence=0.95,
    )
    await svc.apply(campaign_id="c1", turn_id="t1", operations=[op])
    rows = await store.list_inventory_holdings("c1", holder_kind="character", holder_id="joe")
    assert rows and rows[0]["item_ref"] == "silver-ring"
    assert len(seen) == 1


async def test_low_confidence_records_flag(store):
    await _enable(store)
    await store.write_emergent(
        campaign_id="c1",
        kind="character",
        entity_id="joe",
        frontmatter={"id": "joe", "name": "Joe"},
        body="",
        source="test",
    )
    svc = InventoryService(store=store, event_bus=EventBus())
    op = InventoryOperation(
        action=InventoryAction.ACQUIRE,
        item="rusty key",
        holder="joe",
        confidence=0.2,
    )
    await svc.apply(campaign_id="c1", turn_id="t1", operations=[op])
    flags = await store.list_inventory_flags("c1", resolved=False)
    assert len(flags) == 1
    assert flags[0]["flag_reason"] == "low_confidence"


async def test_holder_failure_restores_already_written_holders(store):
    """#584: a holder-N write failure unwinds holders 1..N-1 — the batch
    commits all holders or none, and neither flags nor events survive a
    failed apply."""
    await _enable(store)
    for cid in ("joe", "mara"):
        await store.write_emergent(
            campaign_id="c1",
            kind="character",
            entity_id=cid,
            frontmatter={"id": cid, "name": cid.title()},
            body="",
            source="test",
        )
    bus = EventBus()
    changed = []
    bus.subscribe(inv_events.INVENTORY_CHANGED, lambda e: changed.append(e))
    svc = InventoryService(store=store, event_bus=bus)

    # Seed joe with coins so the rollback restores a non-empty pre-image.
    await svc.apply(
        campaign_id="c1",
        turn_id="t0",
        operations=[
            InventoryOperation(
                action=InventoryAction.ACQUIRE,
                item="coin",
                holder="joe",
                quantity=3,
                confidence=1.0,
            )
        ],
    )
    assert len(changed) == 1

    real_write = svc._persist.write_holder_inventory
    write_calls = {"n": 0}

    async def flaky_write(**kwargs):
        write_calls["n"] += 1
        if write_calls["n"] == 2:
            raise RuntimeError("disk full")
        return await real_write(**kwargs)

    svc._persist.write_holder_inventory = flaky_write  # type: ignore[method-assign]

    ops = [
        InventoryOperation(
            action=InventoryAction.ACQUIRE, item="coin", holder="joe", quantity=2, confidence=1.0
        ),
        InventoryOperation(
            action=InventoryAction.ACQUIRE, item="lamp", holder="mara", confidence=0.2
        ),
    ]
    with pytest.raises(RuntimeError, match="disk full"):
        await svc.apply(campaign_id="c1", turn_id="t1", operations=ops)

    # joe was written first (touch order) and restored after mara's failure.
    joe_rows = await store.list_inventory_holdings("c1", holder_kind="character", holder_id="joe")
    assert [(r["item_ref"], r["quantity"]) for r in joe_rows] == [("coin", 3)]
    joe_entries = await svc._persist.read_holder_inventory("c1", HolderKind.CHARACTER, "joe")
    assert [(e.item_ref, e.quantity) for e in joe_entries] == [("coin", 3)]
    assert (
        await store.list_inventory_holdings("c1", holder_kind="character", holder_id="mara") == []
    )
    # Nothing from the failed apply leaked: no second event, no flags.
    assert len(changed) == 1
    assert await store.list_inventory_flags("c1", resolved=False) == []

    # The restored base is consistent: the same batch re-applies cleanly.
    svc._persist.write_holder_inventory = real_write  # type: ignore[method-assign]
    await svc.apply(campaign_id="c1", turn_id="t2", operations=ops)
    joe_rows = await store.list_inventory_holdings("c1", holder_kind="character", holder_id="joe")
    assert [(r["item_ref"], r["quantity"]) for r in joe_rows] == [("coin", 5)]
    mara_rows = await store.list_inventory_holdings("c1", holder_kind="character", holder_id="mara")
    assert [(r["item_ref"], r["quantity"]) for r in mara_rows] == [("lamp", 1)]
    assert len(changed) == 2
    flags = await store.list_inventory_flags("c1", resolved=False)
    assert [f["flag_reason"] for f in flags] == ["low_confidence"]


async def test_flag_insert_failure_removes_recorded_flags_and_restores(store):
    """#584: a failure while recording flags deletes the flags already
    inserted and restores the holder writes — no review row survives for an
    apply that rolled back."""
    await _enable(store)
    await store.write_emergent(
        campaign_id="c1",
        kind="character",
        entity_id="joe",
        frontmatter={"id": "joe", "name": "Joe"},
        body="",
        source="test",
    )
    svc = InventoryService(store=store, event_bus=EventBus())

    real_record = store.record_inventory_flag
    record_calls = {"n": 0}

    async def flaky_record(**kwargs):
        record_calls["n"] += 1
        if record_calls["n"] == 2:
            raise RuntimeError("flag insert boom")
        return await real_record(**kwargs)

    store.record_inventory_flag = flaky_record  # type: ignore[method-assign]
    try:
        ops = [
            InventoryOperation(
                action=InventoryAction.ACQUIRE, item="key", holder="joe", confidence=0.2
            ),
            InventoryOperation(
                action=InventoryAction.ACQUIRE, item="map", holder="joe", confidence=0.2
            ),
        ]
        with pytest.raises(RuntimeError, match="flag insert boom"):
            await svc.apply(campaign_id="c1", turn_id="t1", operations=ops)
    finally:
        store.record_inventory_flag = real_record  # type: ignore[method-assign]

    assert await store.list_inventory_flags("c1", resolved=False) == []
    assert await store.list_inventory_holdings("c1", holder_kind="character", holder_id="joe") == []


async def test_restore_holders_rewrites_pre_images(store):
    """#584: the rollback payload a successful apply returns restores the
    holder to its pre-apply state when the orchestrator unwinds the turn."""
    await _enable(store)
    await store.write_emergent(
        campaign_id="c1",
        kind="character",
        entity_id="joe",
        frontmatter={"id": "joe", "name": "Joe"},
        body="",
        source="test",
    )
    bus = EventBus()
    changed = []
    bus.subscribe(inv_events.INVENTORY_CHANGED, lambda e: changed.append(e))
    svc = InventoryService(store=store, event_bus=bus)
    await svc.apply(
        campaign_id="c1",
        turn_id="t0",
        operations=[
            InventoryOperation(
                action=InventoryAction.ACQUIRE,
                item="coin",
                holder="joe",
                quantity=3,
                confidence=1.0,
            )
        ],
    )

    result = await svc.apply(
        campaign_id="c1",
        turn_id="t1",
        operations=[
            InventoryOperation(
                action=InventoryAction.ACQUIRE,
                item="coin",
                holder="joe",
                quantity=2,
                confidence=1.0,
            )
        ],
    )
    assert result is not None
    rows = await store.list_inventory_holdings("c1", holder_kind="character", holder_id="joe")
    assert [(r["item_ref"], r["quantity"]) for r in rows] == [("coin", 5)]

    await svc.restore_holders(campaign_id="c1", turn_id="t1", rollback=result["rollback"])
    rows = await store.list_inventory_holdings("c1", holder_kind="character", holder_id="joe")
    assert [(r["item_ref"], r["quantity"]) for r in rows] == [("coin", 3)]
    entries = await svc._persist.read_holder_inventory("c1", HolderKind.CHARACTER, "joe")
    assert [(e.item_ref, e.quantity) for e in entries] == [("coin", 3)]
    # apply (t0), apply (t1), restore — each surfaced a change event.
    assert len(changed) == 3


# --- #622: turn-scoped cross-round dedup --------------------------------------
#
# In per_character_multi_call mode every speaker round re-extracts and applies
# under the same turn_id. Two NPC responses that restate one event must apply
# the additive op once; genuinely repeated ops within a single response (one
# apply() call) must both apply. The policy: within-round repeats are real,
# cross-round repeats are restatements.


async def _enable_with_holder(store, holder="joe"):
    await _enable(store)
    await store.write_emergent(
        campaign_id="c1",
        kind="character",
        entity_id=holder,
        frontmatter={"id": holder, "name": holder.title()},
        body="",
        source="test",
    )


async def test_cross_round_restatement_acquire_applies_once(store):
    """#622: the same acquire restated in a second round of the same turn is
    skipped — a repeated fungible acquire does not double the quantity."""
    await _enable_with_holder(store)
    svc = InventoryService(store=store, event_bus=EventBus())
    op = InventoryOperation(
        action=InventoryAction.ACQUIRE, item="gold", holder="joe", quantity=10, confidence=1.0
    )

    first = await svc.apply(campaign_id="c1", turn_id="t1", operations=[op])
    second = await svc.apply(campaign_id="c1", turn_id="t1", operations=[op])

    assert first is not None and first["deduped"] == 0
    assert second is not None and second["deduped"] == 1
    rows = await store.list_inventory_holdings("c1", holder_kind="character", holder_id="joe")
    assert [(r["item_ref"], r["quantity"]) for r in rows] == [("resource:gold", 10)]


async def test_cross_round_restatement_transfer_applies_once(store):
    """#622 (sharpest case): a transfer restated across rounds does not
    reconcile a now-missing source item and mint a second copy at the dest."""
    await _enable_with_holder(store, "alice")
    await store.write_emergent(
        campaign_id="c1",
        kind="character",
        entity_id="bob",
        frontmatter={"id": "bob", "name": "Bob"},
        body="",
        source="test",
    )
    svc = InventoryService(store=store, event_bus=EventBus())
    await svc.apply(
        campaign_id="c1",
        turn_id="t0",
        operations=[
            InventoryOperation(
                action=InventoryAction.ACQUIRE, item="ring", holder="alice", confidence=1.0
            )
        ],
    )

    transfer = InventoryOperation(
        action=InventoryAction.TRANSFER, item="ring", holder="alice", to="bob", confidence=1.0
    )
    # Round 1: "Alice hands Bob the ring". Round 2 restates it.
    await svc.apply(campaign_id="c1", turn_id="t1", operations=[transfer])
    second = await svc.apply(campaign_id="c1", turn_id="t1", operations=[transfer])

    assert second is not None and second["deduped"] == 1
    bob_rows = await store.list_inventory_holdings("c1", holder_kind="character", holder_id="bob")
    assert [(r["item_ref"], r["quantity"]) for r in bob_rows] == [("ring", 1)]
    assert (
        await store.list_inventory_holdings("c1", holder_kind="character", holder_id="alice") == []
    )
    # No spurious reconciliation flag from a re-applied transfer.
    assert await store.list_inventory_flags("c1", resolved=False) == []


async def test_within_round_repeats_both_apply(store):
    """#622 acceptance: two separate 10-gold payments narrated in *one* response
    (one apply() call) both apply — only cross-round restatements are deduped."""
    await _enable_with_holder(store)
    svc = InventoryService(store=store, event_bus=EventBus())
    pay = InventoryOperation(
        action=InventoryAction.ACQUIRE, item="gold", holder="joe", quantity=10, confidence=1.0
    )

    result = await svc.apply(campaign_id="c1", turn_id="t1", operations=[pay, pay])

    assert result is not None and result["deduped"] == 0
    rows = await store.list_inventory_holdings("c1", holder_kind="character", holder_id="joe")
    assert [(r["item_ref"], r["quantity"]) for r in rows] == [("resource:gold", 20)]


async def test_same_op_different_turns_both_apply(store):
    """Dedup is turn-scoped: an identical op in a later, distinct turn applies —
    two separate payments across two turns are not collapsed."""
    await _enable_with_holder(store)
    svc = InventoryService(store=store, event_bus=EventBus())
    pay = InventoryOperation(
        action=InventoryAction.ACQUIRE, item="gold", holder="joe", quantity=10, confidence=1.0
    )

    await svc.apply(campaign_id="c1", turn_id="t1", operations=[pay])
    second = await svc.apply(campaign_id="c1", turn_id="t2", operations=[pay])

    assert second is not None and second["deduped"] == 0
    rows = await store.list_inventory_holdings("c1", holder_kind="character", holder_id="joe")
    assert [(r["item_ref"], r["quantity"]) for r in rows] == [("resource:gold", 20)]


async def test_manual_ops_never_deduped(store):
    """turn_id=None (manual API ops) opts out of dedup: two identical manual
    acquires both apply regardless of how the service is reused."""
    await _enable_with_holder(store)
    svc = InventoryService(store=store, event_bus=EventBus())
    op = InventoryOperation(
        action=InventoryAction.ACQUIRE, item="gold", holder="joe", quantity=5, confidence=1.0
    )

    await svc.apply(campaign_id="c1", turn_id=None, operations=[op])
    second = await svc.apply(campaign_id="c1", turn_id=None, operations=[op])

    assert second is not None and second["deduped"] == 0
    rows = await store.list_inventory_holdings("c1", holder_kind="character", holder_id="joe")
    assert [(r["item_ref"], r["quantity"]) for r in rows] == [("resource:gold", 10)]
