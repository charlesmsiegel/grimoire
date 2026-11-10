"""Sprint 108's adapter ops: the app shell, onboarding and navigation. Called as ``shell.<op>``.

Only Sprint 108 edits this module (or a ``shell`` subpackage that replaces it, keeping ``ADAPTER``). Each op is
``handler(ctx, args) -> dict`` over an existing domain service; see ``grimoire.app.adapter`` and docs/APP_SHELL.md.
"""

from __future__ import annotations

from grimoire.app.adapter import Adapter

ADAPTER = Adapter("shell", owner="108", ops={})

__all__ = ["ADAPTER"]
