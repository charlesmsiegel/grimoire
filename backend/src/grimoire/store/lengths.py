"""The built-in length vocabulary: four named bundles of the five response knobs.

Constants, not files — these are the primitive presets a response preset names,
and tuning happens by overriding individual knobs at a scope rather than by
editing a preset. See docs/superpowers/specs/2026-07-26-response-presets-design.md.
"""

from __future__ import annotations

# reply_words         target TOTAL words in one reply, narration included
# blocks              max blocks in one reply, narration included
# paragraphs          max paragraphs in any single block
# speakers            max distinct speaking characters (narration excluded)
# blocks_per_speaker  max blocks any one character may take (1 == no repeats)
KNOBS = ("reply_words", "blocks", "paragraphs", "speakers", "blocks_per_speaker")

# `blocks` is deliberately > `speakers` in every preset: narration occupies a
# block but is not a speaker, so it always needs room.
PRESETS: dict[str, dict[str, int]] = {
    "terse":     {"reply_words": 150, "blocks": 3, "paragraphs": 1,
                  "speakers": 2, "blocks_per_speaker": 1},
    "brisk":     {"reply_words": 300, "blocks": 4, "paragraphs": 2,
                  "speakers": 3, "blocks_per_speaker": 1},
    "standard":  {"reply_words": 550, "blocks": 5, "paragraphs": 2,
                  "speakers": 4, "blocks_per_speaker": 2},
    "cinematic": {"reply_words": 900, "blocks": 7, "paragraphs": 3,
                  "speakers": 5, "blocks_per_speaker": 2},
}

DEFAULT = "standard"


def get(preset_id: str) -> dict[str, int] | None:
    """The knob values for `preset_id`, or None if it names nothing. Callers get
    a fresh dict — resolution mutates its working copy."""
    preset = PRESETS.get(preset_id)
    return dict(preset) if preset else None


def coerce(value) -> int | None:
    """A knob value as a positive int, or None if it isn't one. Frontmatter is
    all strings, and a malformed knob must degrade to 'unset' rather than raise
    mid-scene."""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
