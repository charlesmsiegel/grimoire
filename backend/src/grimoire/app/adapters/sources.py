"""Sprint 109's adapter ops: sources, entities, world and character authoring. Called as ``sources.<op>``.

Only Sprint 109 edits this module (or a ``sources`` subpackage that replaces it, keeping ``ADAPTER``). Each op is
``handler(ctx, args) -> dict`` over an existing domain service; see ``grimoire.app.adapter`` and docs/APP_SHELL.md.
"""

from __future__ import annotations

from grimoire.app.adapter import Adapter

ADAPTER = Adapter("sources", owner="109", ops={})

__all__ = ["ADAPTER"]
