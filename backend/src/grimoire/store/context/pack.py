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

#: Tokens charged per history message on top of its own content, for the
#: framing a provider wraps around a turn. No provider sends bare content: the
#: chat APIs bill role tokens per message, and the Claude agent path is heavier
#: still — `claude_agent._flatten` serialises the WHOLE conversation into one
#: prompt string, prefixing every turn with `[role]\n` and joining with blank
#: lines, so on that provider the per-message sum is not even the right shape.
#: An exact figure would mean teaching this module each provider's wire format;
#: a small fixed allowance, erring high, is the safe direction for a ceiling —
#: the same reasoning as rounding the token heuristic up.
MESSAGE_OVERHEAD = 5

def budget_tokens() -> int:
    """The configured context budget in tokens; 0 = unbounded. A hand-edited
    config.md holding nonsense falls back to unbounded rather than raising: a
    malformed budget must not take scene generation down with it."""
    try:
        return max(int(config.read_config().get("context_budget",
                                                config.DEFAULT_CONTEXT_BUDGET)), 0)
    except (TypeError, ValueError):
        return 0


#: How system.j2 joins the sections. `pack` only uses it as the fallback for
#: `compose`; the real callers pass the template render itself, so what the
#: packer measures is the string that gets sent.
SEPARATOR = "\n\n"


def message_cost(content: str) -> int:
    """What one history message costs: its content plus `MESSAGE_OVERHEAD`.

    Shared with `context_breakdown` so the inspector reports history the way
    the packer charges it — the two disagreeing about the same messages is the
    bug this whole seam keeps producing.
    """
    return tokens.count_tokens(content) + MESSAGE_OVERHEAD


def pack(sections: list[dict], history: list[dict], reserved: int = 0,
         budget: int | None = None, compose=None) -> dict:
    """Fit `sections` + `history` into `budget` tokens.

    `sections` are the rendered sections in prompt order, each ``{"label",
    "text", "tier"}``. `reserved` is every token the caller will send that this
    packer cannot drop — the post-history block, plus any message appended
    after the system one (a director note, regenerate guidance, a roll-result
    block, the opener's prompt and shape rules). Counting them here is what
    keeps the packed prompt and the sent request the same size; anything left
    out is budget the request overspends silently. `budget` defaults to the
    configured one.

    `compose` joins the surviving section texts into the system message exactly
    as the caller will send it — the packer measures that composed string, not
    the sum of its parts. Defaults to the blank-line join system.j2 does.

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
        return {"sections": packed, "history": list(history), "history_trimmed": 0,
                "history_trimmed_tokens": 0}
    if compose is None:
        compose = SEPARATOR.join

    # Per-section costs order the drops (largest first); they do NOT decide
    # whether we are over. Token counts are not additive: the separators
    # between sections go uncharged in a sum, and on the tiktoken-less Android
    # path every section's `len // 4` throws away its own remainder. A sum can
    # therefore clear a ceiling the real message misses, which is the one thing
    # this must not do -- so the system message is composed and measured whole,
    # and re-measured after each drop.
    costs = [tokens.count_tokens(s["text"]) for s in packed]

    def system_cost() -> int:
        return tokens.count_tokens(compose([s["text"] for s in packed if not s["dropped"]]))

    hist = list(history)
    # History messages are sent as separate entries, so summing them is the
    # right shape (unlike the joined system message) -- plus the per-message
    # framing the provider adds around each one.
    hist_costs = [message_cost(m["content"]) for m in hist]
    hist_total = sum(hist_costs)
    sys_cost = system_cost()
    total = reserved + sys_cost + hist_total
    trimmed = 0
    trimmed_tokens = 0

    for tier in DROP_ORDER:
        if total <= budget:
            break
        for i in sorted((n for n, s in enumerate(packed)
                         if s["tier"] == tier and not s["dropped"]),
                        key=lambda n: (costs[n], n), reverse=True):
            if total <= budget:
                break
            packed[i]["dropped"] = True
            sys_cost = system_cost()
            total = reserved + sys_cost + hist_total
        if tier == ARCHIVE:
            while total > budget and len(hist) > HISTORY_FLOOR:
                cost = hist_costs.pop(0)
                hist_total -= cost
                trimmed_tokens += cost
                hist.pop(0)
                trimmed += 1
                total = reserved + sys_cost + hist_total

    return {"sections": packed, "history": hist, "history_trimmed": trimmed,
            "history_trimmed_tokens": trimmed_tokens}
