"""Sprint 113's adapter ops: campaign situation, authority and canon. Called as ``campaign.<op>``.

Only Sprint 113 edits this module (or a ``campaign`` subpackage that replaces it, keeping ``ADAPTER``). Each op is
``handler(ctx, args) -> dict`` over an existing domain service; see ``grimoire.app.adapter`` and docs/APP_SHELL.md.
"""

from __future__ import annotations

from grimoire.app.adapter import Adapter

ADAPTER = Adapter("campaign", owner="113", ops={})

__all__ = ["ADAPTER"]
