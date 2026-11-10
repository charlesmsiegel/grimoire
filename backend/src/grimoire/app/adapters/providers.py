"""Sprint 112's adapter ops: provider settings, evidence and metering. Called as ``providers.<op>``.

Only Sprint 112 edits this module (or a ``providers`` subpackage that replaces it, keeping ``ADAPTER``). Each op is
``handler(ctx, args) -> dict`` over an existing domain service; see ``grimoire.app.adapter`` and docs/APP_SHELL.md.
"""

from __future__ import annotations

from grimoire.app.adapter import Adapter

ADAPTER = Adapter("providers", owner="112", ops={})

__all__ = ["ADAPTER"]
