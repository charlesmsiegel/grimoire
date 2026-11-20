"""The play adapter's hypothetical reply (073): ``play.imagine``, how one NPC might answer a line, saved nowhere.

Registered by ``play.py``. A thin layer over ``grimoire.play.hypothetical.imagine``, which holds the rules: the line
is read as the next post would be, the NPC is briefed and asked as ``generate`` would ask it, and nothing is
written. The answer says so itself (``canonical: false``), so no client can take it for a turn. See
docs/CHAT_PLAY.md#a-hypothetical-reply.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.app.adapter import AdapterContext
from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.play.hypothetical import imagine
from grimoire.textapi import MAX_POST_CHARS, ApiRefused, field, parse_ref
from grimoire.textapi.views import heard

#: 008's typed modes a line may be read in: the composer's own (``play.POST_MODES``).
MODES = ("auto", "speech", "action")


def _usage(take) -> dict:  # type: ignore[no-untyped-def]
    """What the one call cost, as its attempts booked it: no take holds them, so the answer does. A count the
    provider did not report makes the total ``None``, never a smaller number."""
    attempts = [attempt for call in take.calls for attempt in call.attempts]
    totals: dict[str, int | None] = {}
    for name in ("input_tokens", "output_tokens"):
        counts = [getattr(attempt.usage, name) for attempt in attempts]
        totals[name] = None if any(count is None for count in counts) else sum(counts)
    return {"attempts": len(attempts), **totals}


def imagine_reply(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """``speaker`` (an NPC of the chat) answers ``text`` (the player character's line, at most 20,000 characters,
    read in ``mode``: ``auto`` by default, ``speech`` or ``action``) as it might next, and nothing is written.
    ``replies`` are the screened lines the table would have heard, each ``speaker``, ``actor`` and ``text``; none
    when the call failed, was refused or was held back, with why in ``calls``."""
    chat = parse_ref(ChatRef.parse, args, "chat")
    speaker = parse_ref(InstanceRef.parse, args, "speaker")
    text = field(args, "text", str)
    if not text.strip():
        raise ApiRefused("no_content", "say or do something to imagine an answer to")
    if len(text) > MAX_POST_CHARS:
        raise ApiRefused("invalid_field", f"a line is at most {MAX_POST_CHARS} characters")
    mode = field(args, "mode", str, required=False, default="auto")
    if mode not in MODES:
        raise ApiRefused("invalid_field", f"mode must be one of {', '.join(MODES)}")
    found = imagine(ctx.runner, chat, ctx.handle, speaker, text, mode=mode)
    names, post = found.names, found.post
    return {
        "chat": str(chat), "canonical": False, "saved": False,
        "speaker": {"actor": str(found.speaker), "name": names.get(found.speaker, found.speaker.instance_id)},
        "line": {"kind": post.kind, "channel": post.channel, "text": post.text, "mode": mode,
                 "to": [names.get(actor, actor.instance_id) for actor in post.addressees]},
        "replies": [{"speaker": line.speaker, "actor": None if line.actor is None else str(line.actor),
                     "text": line.text} for line in heard(found.take)],
        "outcome": found.take.outcome,
        "calls": [{"status": call.status, "reason": call.reason} for call in found.take.calls
                  if call.status != "accepted"],
        "usage": _usage(found.take),
    }


OPS = {"imagine": imagine_reply}

__all__ = ["MODES", "OPS"]
