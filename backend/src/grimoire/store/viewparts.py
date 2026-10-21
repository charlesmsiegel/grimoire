"""Shared plumbing for the read-only campaign views that assemble many parts.

`briefing.build`, `casefile.build` and `timeline.build` each gather a page from
a dozen separate store reads, and each promises never to raise on a garbled
store file: every section degrades to empty on its own, the contract
``plot.render_open`` set and ``get_ledger`` kept. They each carried the same
nested helper for that; this is it, once.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def tolerant(read: Callable[[], Any], empty: Any) -> Any:
    """`read()`, or `empty` when it raises anything at all.

    Callers run their reads inside their own `campaign_lock` hold; this only
    decides what a failed part of the view becomes.
    """
    try:
        return read()
    except Exception:  # noqa: BLE001 — a garbled file empties its section, not the view
        return empty
