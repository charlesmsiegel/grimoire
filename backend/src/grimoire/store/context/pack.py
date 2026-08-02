"""Tiered budget packing: what gives way when the context does not fit.

Every section used to be all-or-nothing — included whenever its data was
non-empty — so a long campaign simply overran the model's window and the
provider truncated it, silently, from whichever end it liked. This module makes
that decision ours and makes it visible.

Four tiers, most protected first:

``lock-in``
    The instructions that define the reply itself: the system prompts, who the
    characters are, the response format and length budget. **Never dropped.**
    Losing them does not cost the model information, it costs the model its
    brief — a full answer in the wrong shape is worse than a smaller one.
``spotlight``
    The live situation: who is present and what they know, where and when it
    is, the activated world info, the rules in play.
``background``
    The standing frame: the recap, the message examples, the off-scene cast.
``archive``
    Older scenes recalled by keyword (`archive.py`). Retrieved *because* the
    conversation touched on them, so worth having — and the first thing to go
    when it does not fit.

Under pressure sections are dropped whole, lowest tier first, and the trailing
history window is trimmed between ``archive`` and ``background``. Placing the
trim there is the one ordering judgement here: history usually dominates a long
scene, so the issue calls it the first pressure valve — but the last few turns
are the only thing in the prompt the model cannot reconstruct from anything
else, so it gives way after the recalled archive rather than before it, and
never below ``HISTORY_FLOOR`` messages.

Within a tier the largest section goes first: that reaches the ceiling in the
fewest drops. Ties break toward the later section so a given store always packs
the same way.

Drops are never silent. `pack` marks a dropped section rather than deleting it,
and `context_sections` reports the marked list, so the inspector shows what was
cut instead of quietly disagreeing with what was sent.

Budget: `context_budget` in config.md, in tokens. 0 (the default, and every
pre-existing install) means unbounded — nothing is counted and nothing is
dropped, so the packed prompt is byte-identical to the unpacked one. The
backend cannot infer the number: only the frontend sees the model list that
carries each model's window size.
"""

from __future__ import annotations

from .. import config
from . import tokens

LOCK_IN = "lock-in"
SPOTLIGHT = "spotlight"
BACKGROUND = "background"
ARCHIVE = "archive"
#: Not a droppable tier — the label the conversation history reports itself
#: under, so the inspector can tell it apart from the sections around it.
HISTORY = "history"

#: The order sections give way in. `LOCK_IN` is absent by construction: this
#: tuple is what the packer iterates, so lock-in is unreachable rather than
#: merely last.
DROP_ORDER = (ARCHIVE, BACKGROUND, SPOTLIGHT)

#: Projected history messages the trim never goes below. Two keeps the latest
#: exchange — below that the model is answering a turn it cannot see.
HISTORY_FLOOR = 2

def budget_tokens() -> int:
    """The configured context budget in tokens; 0 = unbounded. A hand-edited
    config.md holding nonsense falls back to unbounded rather than raising: a
    malformed budget must not take scene generation down with it."""
    try:
        return max(int(config.read_config().get("context_budget",
                                                config.DEFAULT_CONTEXT_BUDGET)), 0)
    except (TypeError, ValueError):
        return 0


def pack(sections: list[dict], history: list[dict], reserved: int = 0,
         budget: int | None = None) -> dict:
    """Fit `sections` + `history` into `budget` tokens.

    `sections` are the rendered sections in prompt order, each ``{"label",
    "text", "tier"}``. `reserved` is every token the caller will send that this
    packer cannot drop — the post-history block, plus any message appended
    after the system one (a director note, regenerate guidance, a roll-result
    block, the opener's prompt and shape rules). Counting them here is what
    keeps the packed prompt and the sent request the same size; anything left
    out is budget the request overspends silently. `budget` defaults to the
    configured one.

    Returns ``{"sections", "history", "history_trimmed"}``: the same sections
    in the same order with a ``dropped`` flag added (dropped ones stay in the
    list, for the inspector), the surviving history, and how many messages the
    trim removed from the front.

    When even lock-in plus the history floor overruns the budget, that is what
    comes back: the packer will not drop a section it promised never to drop,
    so the caller ships an over-budget prompt rather than a malformed one.
    """
    packed = [{**s, "dropped": False} for s in sections]
    if budget is None:
        budget = budget_tokens()
    if budget <= 0:  # unbounded: skip counting entirely, it is not free
        return {"sections": packed, "history": list(history), "history_trimmed": 0}

    costs = [tokens.count_tokens(s["text"]) for s in packed]
    hist = list(history)
    hist_costs = [tokens.count_tokens(m["content"]) for m in hist]
    total = reserved + sum(costs) + sum(hist_costs)
    trimmed = 0

    for tier in DROP_ORDER:
        if total <= budget:
            break
        for i in sorted((n for n, s in enumerate(packed)
                         if s["tier"] == tier and not s["dropped"]),
                        key=lambda n: (costs[n], n), reverse=True):
            if total <= budget:
                break
            packed[i]["dropped"] = True
            total -= costs[i]
        if tier == ARCHIVE:
            while total > budget and len(hist) > HISTORY_FLOOR:
                total -= hist_costs.pop(0)
                hist.pop(0)
                trimmed += 1

    return {"sections": packed, "history": hist, "history_trimmed": trimmed}
