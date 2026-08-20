"""Measure reply-length drift over the recent transcript. Pure: no I/O.

The counterweight to length drift needs to know what actually happened, not
what was asked for. This module turns the stored transcript into per-turn
metrics and the boolean signals the corrective template renders from.
"""

from __future__ import annotations

import re

from . import export, fence
from .scenes import serialize as scenes_serialize

WINDOW = 3          # turns measured; a constant, deliberately not a setting
# The two overshoot thresholds, public because they ARE the codebase's
# definition of "this reply ran long": below TRIM the corrective renders
# nothing, at CUT it escalates. evals/graders.py scores recorded output
# against TRIM rather than inventing a second tolerance that would drift out
# of step with the corrective the moment either number is tuned.
TRIM = 1.25
CUT = 1.75

# The fence grammar is owned by store/fence.py; a second copy of the opener here
# would silently diverge the day that one changes. Only the closing half is
# ours — word counting has to span the whole fence, not just its opener.
_ROLL_FENCE = re.compile(fence.OPENER.pattern + r".*?(?:```|\Z)",
                         re.IGNORECASE | re.DOTALL)


def _is_model_block(m: dict) -> bool:
    return (m.get("role") == "assistant"
            and m.get("speaker") not in scenes_serialize.SYNTHETIC_SPEAKERS)


def segment(messages: list[dict], turn_sizes: list[int]) -> list[list[dict]]:
    """Partition the TRACKED SUFFIX of model blocks into turns.

    turn_sizes describes the last sum(turn_sizes) model blocks, not the whole
    transcript. Anything before that is pre-tracking history — a scene played
    before turn recording existed — and is simply ignored, which is what lets an
    upgraded scene start being measured after a few new generations. Comparing
    against ALL model blocks instead would disable drift control on such a scene
    forever.

    Returns [] when the recorded sizes don't fit the transcript at all (a
    hand-edited file): measuring what we can't explain produces confident wrong
    numbers, and silence is the better failure.
    """
    blocks = [m for m in messages if _is_model_block(m)]
    tracked = sum(turn_sizes)
    if not turn_sizes or tracked > len(blocks):
        return []
    blocks = blocks[len(blocks) - tracked:]
    turns, at = [], 0
    for size in turn_sizes:
        turns.append(blocks[at:at + size])
        at += size
    return turns


def _prose(content: str) -> str:
    """`content` with everything that is not prose the model WROTE taken out.

    Two exclusions, one reason. A roll fence is machine-readable output the
    protocol asked for; an image is a picture the reply included, and on the
    narrator's side of #376 it was requested with a ten-character handle that
    `context.art.resolve_handles` expanded into markdown afterwards.

    Counting either as prose punishes the model for complying, and the image is
    the sharper case: one added ~7 phantom words and a whole phantom paragraph
    to a fifteen-word reply, which under a `terse` budget is enough on its own
    to trip the drift correction and tell the model to write LESS -- for having
    done exactly what the available-art section asked of it.
    """
    return export.remove_images(_ROLL_FENCE.sub(" ", content))


def _words(content: str) -> int:
    return len(_prose(content).split())


def _paragraphs(content: str) -> int:
    return max(len([p for p in _prose(content).split("\n\n") if p.strip()]), 1)


def _identity(speaker: str, cast_names) -> str:
    """Canonicalize a label to a cast member. split_reply preserves whatever the
    model wrote, so one character can appear as 'Winifred' and 'Winifred Vance';
    counting raw strings inflates the speaker count into a false violation while
    letting that same character slip under blocks_per_speaker."""
    return scenes_serialize.match_name(speaker, cast_names) or speaker


def measure(messages: list[dict], turn_sizes: list[int], cast_names,
            budget: dict, window: int = WINDOW) -> dict | None:
    """Per-turn metrics plus the render signals, or None if nothing to measure.

    `cast_names` may be a sequence or a zero-argument callable returning one.
    The callable form exists because building the roster opens one card file per
    campaign actor, and the common case — a scene with no recorded turns — bails
    out before a single name is needed.

    EVERY signal is "any turn in the window violated it" — including the word
    signal, which uses the window MAXIMUM. A mean oscillates: at a 100-word
    budget, 130/130/130 corrects at 1.30x, one compliant turn clears it at
    1.20x, and the next 150-word turn re-triggers at 1.27x. The maximum makes
    the rule monotone in the window's contents, so it cannot flicker, and makes
    "clears only after 3 compliant turns" true rather than merely claimed.
    """
    turns = segment(messages, turn_sizes)[-window:]
    if not turns:
        return None
    cast_names = cast_names() if callable(cast_names) else cast_names

    totals, ratios = [], []
    over_blocks = over_paras = over_speakers = over_repeats = False
    for turn in turns:
        total = sum(_words(m["content"]) for m in turn)
        totals.append(total)
        ratios.append(total / budget["reply_words"])
        over_blocks = over_blocks or len(turn) > budget["blocks"]
        over_paras = over_paras or any(_paragraphs(m["content"]) > budget["paragraphs"]
                                       for m in turn)
        counts: dict[str, int] = {}
        for m in turn:
            if m.get("speaker") is None:
                continue  # narration occupies a block but is not a character
            who = _identity(m["speaker"], cast_names)
            counts[who] = counts.get(who, 0) + 1
        over_speakers = over_speakers or len(counts) > budget["speakers"]
        over_repeats = over_repeats or any(n > budget["blocks_per_speaker"]
                                           for n in counts.values())

    peak = max(ratios)
    return {"totals": totals, "max_ratio": peak,
            "tier": "cut" if peak >= CUT else ("trim" if peak >= TRIM else ""),
            "blocks": over_blocks, "paragraphs": over_paras,
            "speakers": over_speakers, "blocks_per_speaker": over_repeats}
