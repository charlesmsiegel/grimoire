"""Scene boundary detection heuristics.

The Orchestrator (task #22) calls ``detect_scene_break`` before invoking the
LLM. This is a heuristic-only implementation; a future LLM-assisted refinement
can layer on top. All detectors look at signals available locally — no LLM
call — and return a confidence between 0 and 1.

Confidence policy from the spec:
* ``>= confidence_threshold_auto`` (default 0.8): start a new scene
  automatically with a rollback option.
* ``>= confidence_threshold_prompt`` (default 0.5): surface to the user.
* below: ignore.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from grimoire.scenes.types import Scene, SceneBreakDecision, SceneInit

EXPLICIT_USER_SIGNALS = (
    "/end scene",
    "/end-scene",
    "/new scene",
    "/new-scene",
    "/scene end",
    "/scene break",
)

EXPLICIT_USER_ADVANCE = ("advance to ", "skip to ", "fast forward to ")

# Prose markers that strongly suggest a time jump.
TIME_JUMP_PATTERNS = [
    re.compile(r"\b(hours?|days?|weeks?|months?|years?)\s+(later|pass(?:ed)?)\b", re.IGNORECASE),
    re.compile(r"\b(the\s+next\s+(?:morning|day|evening|night|week|month))\b", re.IGNORECASE),
    re.compile(r"\b(later\s+that\s+(?:night|evening|day|week))\b", re.IGNORECASE),
    re.compile(r"\b(meanwhile,?\s+elsewhere)\b", re.IGNORECASE),
    re.compile(r"\b(some\s+time\s+later)\b", re.IGNORECASE),
]

# "We adjourned to the library", "We moved to the courtyard"
LOCATION_TRANSITION_PATTERNS = [
    re.compile(
        r"\b(we|i)\s+(adjourn(?:ed)?|move(?:d)?|head(?:ed)?|walk(?:ed)?|travel(?:ed|led)?)\s+to\s+",
        re.IGNORECASE,
    ),
    re.compile(r"\b(arrived?\s+at)\s+", re.IGNORECASE),
]

# Strong tonal keywords grouped by register. A submission that mixes registers,
# or pivots hard into one of these, is a candidate tonal shift.
TONAL_KEYWORDS = {
    "rage": (
        "scream",
        "screams",
        "screaming",
        "screamed",
        "rage",
        "raging",
        "fury",
        "furious",
        "snarl",
        "snarling",
        "snarls",
        "snarled",
        "roar",
        "roars",
        "roared",
        "hate",
        "hates",
        "kill",
        "kills",
        "killing",
        "destroy",
        "destroys",
        "slaughter",
    ),
    "grief": (
        "weep",
        "weeps",
        "weeping",
        "wept",
        "sob",
        "sobs",
        "sobbing",
        "sobbed",
        "anguish",
        "despair",
        "grief",
        "grieve",
        "mourn",
        "mourning",
        "mourned",
        "shatter",
        "shattered",
        "broken",
        "tears",
        "crying",
    ),
    "terror": (
        "terror",
        "terrified",
        "horror",
        "horrified",
        "panic",
        "panicked",
        "dread",
        "flee",
        "flees",
        "fled",
        "tremble",
        "trembles",
        "trembled",
        "trembling",
    ),
    "awe": (
        "rapture",
        "ecstasy",
        "ecstatic",
        "awe",
        "awed",
        "marvel",
        "marvels",
        "marveling",
        "marvelous",
        "wondrous",
        "radiant",
    ),
}

_TONAL_KEYWORD_TO_REGISTER: dict[str, str] = {
    word: register for register, words in TONAL_KEYWORDS.items() for word in words
}

_TONAL_WORD_RE = re.compile(r"[A-Za-z']+")


@dataclass
class BoundaryConfig:
    confidence_threshold_auto: float = 0.8
    confidence_threshold_prompt: float = 0.5
    time_gap_hours: float = 6.0
    cast_change_ratio: float = 0.5


def _explicit_signal(text: str) -> str | None:
    lowered = text.strip().lower()
    for token in EXPLICIT_USER_SIGNALS:
        if lowered.startswith(token):
            return token
    for prefix in EXPLICIT_USER_ADVANCE:
        if lowered.startswith(prefix):
            return prefix.strip()
    return None


def _detect_time_jump(text: str) -> bool:
    return any(p.search(text) for p in TIME_JUMP_PATTERNS)


def _detect_location_transition(text: str) -> bool:
    return any(p.search(text) for p in LOCATION_TRANSITION_PATTERNS)


def _tonal_signal(text: str) -> tuple[str, float] | None:
    """Heuristic tonal-shift detector working on a single submission.

    Fires when the prose carries strong tonal markers — multiple exclamations,
    SHOUTING in caps, or a cluster of register-defining keywords (rage, grief,
    terror, awe). Confidence is moderate (0.55) per the spec: enough to surface
    to the user but not to auto-break.
    """
    if not text or not text.strip():
        return None

    score = 0
    registers: set[str] = set()

    exclamations = text.count("!")
    if exclamations >= 2:
        score += 2
    elif exclamations == 1:
        score += 1

    keyword_hits = 0
    caps_words = 0
    for match in _TONAL_WORD_RE.finditer(text):
        word = match.group(0)
        if len(word) >= 3 and word.isupper():
            caps_words += 1
        register = _TONAL_KEYWORD_TO_REGISTER.get(word.lower())
        if register is not None:
            registers.add(register)
            keyword_hits += 1

    if caps_words >= 1:
        score += 2
    score += keyword_hits

    # A pivot into two distinct emotional registers in a single submission is a
    # stronger tonal signal than a single keyword in isolation.
    if len(registers) >= 2:
        score += 1

    if score < 2:
        return None
    return ("tonal_shift", 0.55)


def detect_scene_break(
    scene: Scene | None,
    player_input: str,
    *,
    now_in_game: datetime | None = None,
    proposed_present_cast: list[str] | None = None,
    proposed_location_ref: str | None = None,
    config: BoundaryConfig | None = None,
) -> SceneBreakDecision:
    """Inspect ``player_input`` against the active scene's state."""
    cfg = config or BoundaryConfig()

    explicit = _explicit_signal(player_input)
    if explicit:
        return SceneBreakDecision(
            is_break=True,
            confidence=1.0,
            reason="user_signal",
            proposed_new_scene=SceneInit(
                campaign_id=scene.campaign_id if scene else "",
                branch_id=scene.branch_id if scene else "main",
                location_ref=proposed_location_ref or (scene.location_ref if scene else None),
                in_game_start=now_in_game,
            ),
        )

    if scene is None:
        return SceneBreakDecision(is_break=False, confidence=0.0, reason="none")

    signals: list[tuple[str, float]] = []

    if _detect_time_jump(player_input):
        signals.append(("explicit", 0.85))

    if proposed_location_ref and scene.location_ref and proposed_location_ref != scene.location_ref:
        signals.append(("location_change", 0.9))
    elif _detect_location_transition(player_input):
        signals.append(("location_change", 0.65))

    if (
        now_in_game
        and scene.in_game_end
        and now_in_game - scene.in_game_end >= timedelta(hours=cfg.time_gap_hours)
    ):
        signals.append(("time_gap", 0.85))
    elif (
        now_in_game
        and scene.in_game_start
        and now_in_game - scene.in_game_start >= timedelta(hours=cfg.time_gap_hours * 4)
    ):
        signals.append(("time_gap", 0.7))

    if proposed_present_cast is not None and scene.present_character_refs:
        before = set(scene.present_character_refs)
        after = set(proposed_present_cast)
        if before:
            overlap = len(before & after) / len(before)
            if overlap <= (1 - cfg.cast_change_ratio):
                signals.append(("cast_change", 0.75))

    tonal = _tonal_signal(player_input)
    if tonal is not None:
        signals.append(tonal)

    if not signals:
        return SceneBreakDecision(is_break=False, confidence=0.0, reason="none")

    reason, confidence = max(signals, key=lambda s: s[1])
    return SceneBreakDecision(
        is_break=confidence >= cfg.confidence_threshold_prompt,
        confidence=confidence,
        reason=reason,
        proposed_new_scene=SceneInit(
            campaign_id=scene.campaign_id,
            branch_id=scene.branch_id,
            location_ref=proposed_location_ref or scene.location_ref,
            in_game_start=now_in_game,
            present_character_refs=list(proposed_present_cast or scene.present_character_refs),
            present_pc_refs=list(scene.present_pc_refs),
        ),
    )
