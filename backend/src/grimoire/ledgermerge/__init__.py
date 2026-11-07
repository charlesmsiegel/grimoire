"""Merging one campaign's ledger from two devices that each played offline (028): pure, before 044.

A campaign's **ledger** is its 021 accepted events and wrap-ups, the 017 relations they change and its
023 holder files. Sync holds all of them back (``held``) because copying them file by file would deliver
half an operation or two events with one ordinal. This package computes the merge itself, with no store,
clock or randomness: ``merge_ledger`` (and its parts, ``merge_holdings`` and the event merge) returns
either the files both devices store, identical on both, or a review value naming every reason it stopped.
Wiring it into sync and the owner's review are later steps. See docs/LEDGER_MERGE.md.
"""

from .events import REWRITTEN, EventOrder, causal_order, merge_key, order_events, replay, same_event
from .holdings import EnvelopeSizer, HoldingsMerge, merge_holdings
from .ledger import POLICY, LedgerReview, merge_ledger, problems
from .model import (
    BASE, REASONS, Kept, Ledger, Merged, Output, Review, ReviewItem, Rewrite, Version, device_tag, ranks,
)

__all__ = [
    "BASE", "POLICY", "REASONS", "REWRITTEN", "EnvelopeSizer", "EventOrder", "HoldingsMerge", "Kept", "Ledger",
    "LedgerReview", "Merged", "Output", "Review", "ReviewItem", "Rewrite", "Version", "device_tag",
    "causal_order", "merge_holdings", "merge_key", "merge_ledger", "order_events", "problems", "ranks", "replay",
    "same_event",
]
