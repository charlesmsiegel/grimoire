"""Sprint 110's adapter ops: card import, V1 migration and guarded review. Called as ``imports.<op>``.

Only Sprint 110 edits this module (or a ``imports`` subpackage that replaces it, keeping ``ADAPTER``). Each op is
``handler(ctx, args) -> dict`` over an existing domain service; see ``grimoire.app.adapter`` and docs/APP_SHELL.md.
"""

from __future__ import annotations

from grimoire.app.adapter import Adapter

ADAPTER = Adapter("imports", owner="110", ops={})

__all__ = ["ADAPTER"]
