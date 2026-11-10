"""Sprint 114's adapter ops: art, sync, conflicts and resilient presentation. Called as ``media.<op>``.

Only Sprint 114 edits this module (or a ``media`` subpackage that replaces it, keeping ``ADAPTER``). Each op is
``handler(ctx, args) -> dict`` over an existing domain service; see ``grimoire.app.adapter`` and docs/APP_SHELL.md.
"""

from __future__ import annotations

from grimoire.app.adapter import Adapter

ADAPTER = Adapter("media", owner="114", ops={})

__all__ = ["ADAPTER"]
