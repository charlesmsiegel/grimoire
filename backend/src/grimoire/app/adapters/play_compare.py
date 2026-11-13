"""The play adapter's compare op: keep chosen lines of several takes as the turn's one kept result.

Registered by ``play.py`` as ``play.keep_lines``. A thin layer over 035's ``SceneRunner.keep_lines``
(``grimoire.play.mixed``), which holds the rules; see docs/SCENES.md and docs/CHAT_PLAY.md.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.app.adapter import AdapterContext
from grimoire.core.identity import ChatRef
from grimoire.play.mixed import MAX_PICKED
from grimoire.textapi import ApiRefused, field, parse_ref


def keep_lines(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Keep ``lines`` (1 to 64 line ids as the scene shows them, at most one a speaker, of the proposed takes of
    ``turn``) as that turn's kept take. ``acknowledge_unscreened`` as for the core ``keep``. The scene."""
    chat = parse_ref(ChatRef.parse, args, "chat")
    turn = field(args, "turn", str)  # the turn the lines were read on: a late keep never closes a newer one
    lines = args.get("lines")
    if (not isinstance(lines, list) or not lines or len(lines) > MAX_PICKED
            or not all(type(item) is str and item for item in lines) or len(set(lines)) != len(lines)):
        raise ApiRefused("invalid_field", f"lines must be 1 to {MAX_PICKED} line ids, each once")
    acknowledge = field(args, "acknowledge_unscreened", bool, required=False, default=False)
    reviews = field(args, "acknowledge_reviews", dict, required=False)
    if reviews is not None and not all(type(key) is str and type(value) is str for key, value in reviews.items()):
        raise ApiRefused("invalid_field", "acknowledge_reviews maps take numbers to review tokens")
    ctx.runner.keep_lines(chat, ctx.handle, tuple(lines), acknowledge_unscreened=acknowledge,
                          acknowledge_reviews=reviews, turn=turn)
    scene = ctx.core("open", {"chat": str(chat)})
    scene["unfinished"] = None
    return scene


OPS = {"keep_lines": keep_lines}

__all__ = ["OPS"]
