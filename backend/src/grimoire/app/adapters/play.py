"""Sprint 111's adapter ops: Start Chat, cast, play and transcript. Called as ``play.<op>``.

Only Sprint 111 edits this module (or a ``play`` subpackage that replaces it, keeping ``ADAPTER``). Each op is
``handler(ctx, args) -> dict`` over an existing domain service; see ``grimoire.app.adapter`` and docs/APP_SHELL.md.
"""

from __future__ import annotations

from grimoire.app.adapter import Adapter

ADAPTER = Adapter("play", owner="111", ops={})

__all__ = ["ADAPTER"]
