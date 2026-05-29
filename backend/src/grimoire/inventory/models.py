"""Inventory domain models: entries, operations, results, flags."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class InventoryAction(StrEnum):
    ACQUIRE = "acquire"
    DROP = "drop"
    TRANSFER = "transfer"
    CONSUME = "consume"
    ADJUST = "adjust"
    EQUIP = "equip"
    UNEQUIP = "unequip"


class HolderKind(StrEnum):
    CHARACTER = "character"
    LOCATION = "location"


class FlagReason(StrEnum):
    LOW_CONFIDENCE = "low_confidence"
    RECONCILED_MISSING_ITEM = "reconciled_missing_item"
    RECONCILED_QUANTITY = "reconciled_quantity"
    RECONCILED_HOLDER = "reconciled_holder"
    UNRESOLVED_ITEM = "unresolved_item"
    UNRESOLVED_HOLDER = "unresolved_holder"


class InventoryEntry(BaseModel):
    """One held item in a holder's inventory section."""

    item_ref: str
    item_name: str
    quantity: int = 1
    fungible: bool = False
    equipped: bool = False
    provenance: str | None = None
    notes: str | None = None
    acquired_in_post: str | None = None


class InventoryOperation(BaseModel):
    """A typed, deterministic operation proposed by the extractor or the user."""

    action: InventoryAction
    item: str  # natural-language item; resolved to item_ref later
    holder: str  # acting/source holder ref
    to: str | None = None  # destination holder for transfer
    quantity: int | None = None  # default 1; signed delta for ADJUST
    equipped: bool | None = None
    provenance: str | None = None
    confidence: float = 1.0
    source: str = "extractor"  # 'extractor' | 'user' | 'mechanics:...'
    evidence: str = ""


class FlaggedOp(BaseModel):
    """An operation surfaced for review (low confidence or reconciled)."""

    op: InventoryOperation
    reason: FlagReason


class HolderChange(BaseModel):
    """Resolved net change to a single holder's entries after applying ops."""

    holder_kind: HolderKind
    holder_id: str
    entries: list[InventoryEntry] = Field(default_factory=list)


class OperationResult(BaseModel):
    """Outcome of running the state machine over a turn's operations."""

    changed_holders: list[HolderChange] = Field(default_factory=list)
    flags: list[FlaggedOp] = Field(default_factory=list)
