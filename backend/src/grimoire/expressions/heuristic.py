"""Rule-based expression classifier.

Scans the spoken paragraphs of each present character and emits one
``ExpressionChange`` per character per post, using the *last* (terminal)
detected emotion when multiple cues appear in the same paragraph.

The classifier is cheap and deterministic. It catches the obvious cases
(``"I knew you'd come!" winifred laughed, eyes bright with joy`` → happy;
``"Get out!" winifred snapped`` → angry) at ~70% recall on natural prose;
the LLM classifier picks up the subtle cases.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from grimoire.types.expressions import CoreExpression, ExpressionChange

# Each entry maps a keyword (matched as a word boundary) to a core
# expression. Order is irrelevant — we collect all matches and let the
# terminal-wins rule decide. Punctuation cues are handled separately and
# layered on top of the keyword score to bump confidence.
_KEYWORDS: dict[str, str] = {
    # happy
    "laughed": "happy",
    "laughing": "happy",
    "smiled": "happy",
    "smiling": "happy",
    "grinned": "happy",
    "beamed": "happy",
    "delighted": "happy",
    "joy": "happy",
    "joyful": "happy",
    "bright": "happy",
    "cheered": "happy",
    # sad
    "wept": "sad",
    "cried": "sad",
    "sobbed": "sad",
    "tears": "sad",
    "tearful": "sad",
    "grieved": "sad",
    "mournful": "sad",
    "face fell": "sad",
    # angry
    "snapped": "angry",
    "shouted": "angry",
    "snarled": "angry",
    "growled": "angry",
    "barked": "angry",
    "yelled": "angry",
    "furious": "angry",
    "fuming": "angry",
    "seething": "angry",
    "glared": "angry",
    # surprised
    "gasped": "surprised",
    "startled": "surprised",
    "blinked": "surprised",
    "stunned": "surprised",
    "shocked": "surprised",
    # fearful
    "trembled": "fearful",
    "shaking": "fearful",
    "terrified": "fearful",
    "afraid": "fearful",
    "fearful": "fearful",
    "flinched": "fearful",
    # disgusted
    "grimaced": "disgusted",
    "recoiled": "disgusted",
    "sneered": "disgusted",
    "disgusted": "disgusted",
    # smug
    "smirked": "smug",
    "smirking": "smug",
    "smug": "smug",
    # thoughtful
    "frowned": "thoughtful",
    "pondered": "thoughtful",
    "considered": "thoughtful",
    "mused": "thoughtful",
    "thoughtful": "thoughtful",
    # embarrassed
    "blushed": "embarrassed",
    "flushed": "embarrassed",
    "stammered": "embarrassed",
    "embarrassed": "embarrassed",
    # determined
    "determined": "determined",
    "resolved": "determined",
    "steeled": "determined",
    "set jaw": "determined",
    # hurt
    "wounded": "hurt",
    "winced": "hurt",
    "grunted": "hurt",
    "groaned": "hurt",
    # tired
    "sighed": "tired",
    "exhausted": "tired",
    "weary": "tired",
    "yawned": "tired",
    # suspicious
    "squinted": "suspicious",
    "narrowed": "suspicious",
    "suspicious": "suspicious",
    "wary": "suspicious",
}

# Compile a single regex matching any keyword as a whole token. Multi-word
# entries (``"face fell"``, ``"set jaw"``) are joined verbatim — the regex
# uses lookarounds to ensure the surrounding chars are non-word.
_KEYWORD_PATTERN = re.compile(
    r"(?<![\w])(" + "|".join(re.escape(k) for k in _KEYWORDS) + r")(?![\w])",
    re.IGNORECASE,
)

# Punctuation cues by emotion: presence of these glyphs in a paragraph
# attributed to a character raises the confidence floor.
_PUNCT_BUMP_ANGER = re.compile(r"!+")
_PUNCT_BUMP_QUESTION = re.compile(r"\?\?+|\?!|!\?")

_BASE_CONFIDENCE = 0.6
_PUNCT_BUMP = 0.15
_TERMINAL_FLOOR = 0.55


def heuristic_classify(
    *,
    scene_post_text: str,
    present_characters: Iterable[tuple[str, str]],
    scene_id: str = "",
    post_id: str = "",
) -> list[ExpressionChange]:
    """Classify the speaker of each paragraph in ``scene_post_text``.

    Args:
        scene_post_text: The raw body of a single scene post.
        present_characters: Iterable of ``(character_id, display_name)``
            for characters known to be in scene. Used to attribute prose
            to a speaker via name match.
        scene_id, post_id: Forwarded onto each emitted change.

    Returns one ``ExpressionChange`` per character whose name appeared in
    the text with at least one matched cue. Multi-emotion paragraphs
    resolve to the *terminal* (last) detected emotion per spec.
    """
    present_list = list(present_characters)
    if not present_list:
        return []
    # Per-character running state: terminal emotion + confidence.
    state: dict[str, dict[str, object]] = {}

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", scene_post_text) if p.strip()]
    for paragraph in paragraphs:
        # Determine which characters this paragraph references; we attribute
        # cues to every named character in the paragraph (a paragraph with
        # multiple names splits attribution evenly — terminal-wins still
        # applies independently per character).
        mentioned = [
            (cid, name)
            for cid, name in present_list
            if re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", paragraph, re.IGNORECASE)
        ]
        if not mentioned:
            continue
        emotions_in_paragraph: list[tuple[str, float]] = []
        for match in _KEYWORD_PATTERN.finditer(paragraph):
            emotion = _KEYWORDS[match.group(1).lower()]
            emotions_in_paragraph.append((emotion, _BASE_CONFIDENCE))
        if not emotions_in_paragraph:
            continue

        # Terminal emotion wins for this paragraph.
        terminal_emotion, base_conf = emotions_in_paragraph[-1]
        confidence = max(_TERMINAL_FLOOR, base_conf)
        # Bump confidence when an exclamation aligns with an anger-typed
        # terminal emotion, or when keywords repeat (signals intent).
        anger_bump = terminal_emotion == "angry" and _PUNCT_BUMP_ANGER.search(paragraph)
        surprise_bump = terminal_emotion == "surprised" and _PUNCT_BUMP_QUESTION.search(paragraph)
        if anger_bump or surprise_bump:
            confidence = min(1.0, confidence + _PUNCT_BUMP)
        # Repeated cues for the same emotion in the same paragraph also
        # bump confidence.
        same_emotion_count = sum(1 for e, _ in emotions_in_paragraph if e == terminal_emotion)
        if same_emotion_count > 1:
            confidence = min(1.0, confidence + 0.05 * (same_emotion_count - 1))

        for cid, _name in mentioned:
            state[cid] = {
                "emotion": terminal_emotion,
                "confidence": confidence,
                "evidence": paragraph[:240],
            }

    return [
        ExpressionChange(
            character_id=cid,
            scene_id=scene_id,
            post_id=post_id,
            emotion=str(s["emotion"]),
            confidence=float(s["confidence"]),
            evidence=str(s["evidence"]),
        )
        for cid, s in state.items()
        if str(s["emotion"]) in {e.value for e in CoreExpression}
    ]


__all__ = ["heuristic_classify"]
