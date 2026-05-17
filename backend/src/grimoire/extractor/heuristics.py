"""Heuristic flagging (spec 04 §3).

Detects drift signals against the prior state snapshot and scene:
  - a proper noun that doesn't match any known character → candidate NPC
  - a wound or stat change in prose without a mechanical roll → flag missing mechanic
  - a fact that contradicts existing facts → contradiction flag (via the
    `ContradictionChecker` dependency, when wired up).

The output of this strategy is mostly flags; new candidate names are
emitted as `EntityCandidate`s but never as deltas (the structured-LLM
strategy is responsible for proposing the new-character flow).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from grimoire.types.common import CampaignId, EntityKind
from grimoire.types.extraction import EntityCandidate, ExtractionFlag, FlagLevel
from grimoire.types.scene import Scene
from grimoire.types.state import StateSnapshot

# Capture sequences of capitalized words: "winifred", "the orchard" (rejected),
# "Margaux Dubois". We post-filter via a small stoplist to drop sentence-starts
# and common nouns.
_PROPER_NOUN = re.compile(r"\b([A-Z][a-zA-Z'\-]+(?:\s+[A-Z][a-zA-Z'\-]+)*)\b")

# Words that look like names but aren't characters. Conservative — adding to
# the stoplist trades recall for precision.
_NAME_STOPLIST: frozenset[str] = frozenset(
    {
        # Sentence starters / function words
        "The",
        "A",
        "An",
        "He",
        "She",
        "It",
        "They",
        "We",
        "You",
        "I",
        "His",
        "Her",
        "Their",
        "My",
        "Your",
        "Our",
        "But",
        "And",
        "Or",
        "So",
        "Then",
        "Now",
        "Here",
        "There",
        "When",
        "Where",
        "What",
        "Why",
        "How",
        "Yes",
        "No",
        "Not",
        # Time references
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    }
)

# Phrases describing a wound or stat change.
_WOUND_PHRASE = re.compile(
    r"""
    \b
    (?:took|suffered|absorbed)\s+
    (?:\d+|a|light|heavy|grievous|mortal|aggravated|bashing|lethal)\s*
    (?:damage|wounds?|harm|bashing|lethal|aggravated)?
    \b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Phrases indicating a mechanical roll happened.
_ROLL_HINT = re.compile(
    r"\b(?:rolled|the\s+dice|made\s+a\s+roll|critical|successes?|botched)\b",
    re.IGNORECASE,
)


@dataclass
class HeuristicOutput:
    candidates: list[EntityCandidate] = field(default_factory=list)
    flags: list[ExtractionFlag] = field(default_factory=list)


def _known_names(scene: Scene | None, snapshot: StateSnapshot | None) -> set[str]:
    names: set[str] = set()
    if scene is not None:
        for ref in scene.present_character_refs:
            names.add(ref)
            # Allow a refs's last segment to count as a name match too.
            tail = ref.split(":")[-1].split("/")[-1]
            if tail:
                names.add(tail)
    if snapshot is not None:
        for cs in snapshot.character_states:
            names.add(cs.character_ref)
            tail = cs.character_ref.split(":")[-1].split("/")[-1]
            if tail:
                names.add(tail)
    # Normalize on common variants: capitalize words, replace dashes/underscores
    normalized: set[str] = set()
    for n in names:
        cleaned = re.sub(r"[\-_]", " ", n).strip()
        normalized.add(cleaned)
        normalized.add(cleaned.title())
    return names | normalized


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "unknown"


def find_proper_noun_candidates(
    text: str,
    *,
    known_names: set[str],
    max_candidates: int,
) -> list[EntityCandidate]:
    """Surface capitalized phrases that aren't in the known-names set.

    Each unique phrase appears once. Sentence-initial single capitals are
    filtered to avoid surfacing every "He" / "The" / "Then".
    """
    # Per spec extractor-remaining §8: the heuristic stays character-only.
    # Classifying a name into location / faction / item / lore from prose
    # alone is brittle; that decision is left to the LLM strategy, which
    # has the full schema's new_locations / new_factions / new_items arrays.
    found: dict[str, str] = {}
    known_lower = {n.lower() for n in known_names}
    for match in _PROPER_NOUN.finditer(text):
        phrase = match.group(1).strip()
        if not phrase or phrase in _NAME_STOPLIST:
            continue
        if phrase in known_names or phrase.lower() in known_lower:
            continue
        start = match.start()
        # Capture surrounding sentence as evidence.
        evidence_start = text.rfind(".", 0, start) + 1
        evidence_end = text.find(".", match.end())
        if evidence_end == -1:
            evidence_end = min(len(text), match.end() + 80)
        sentence = text[evidence_start:evidence_end].strip()
        found.setdefault(phrase, sentence)
        if len(found) >= max_candidates:
            break
    return [
        EntityCandidate(
            kind=EntityKind.CHARACTER,
            proposed_id=_slugify(phrase),
            proposed_name=phrase,
            role_hint="unknown",
            evidence=evidence,
            confidence=0.55,
            suggested_card={"name": phrase, "scope": "campaign-local"},
        )
        for phrase, evidence in found.items()
    ]


def detect_missing_mechanics(
    text: str,
    *,
    pre_roll_resolved: bool,
) -> list[ExtractionFlag]:
    """Flag wound/damage prose that has no accompanying mechanical roll.

    `pre_roll_resolved` is true if the orchestrator already resolved a roll
    for this turn. If the model narrates damage but no roll happened and
    none is hinted at, we surface `MISSING_MECHANIC`.
    """
    flags: list[ExtractionFlag] = []
    if pre_roll_resolved:
        return flags
    wound_matches = list(_WOUND_PHRASE.finditer(text))
    if not wound_matches:
        return flags
    roll_seen = bool(_ROLL_HINT.search(text))
    if roll_seen:
        return flags
    for m in wound_matches:
        flags.append(
            ExtractionFlag(
                level=FlagLevel.MISSING_MECHANIC,
                code="wound_without_roll",
                message="wound narrated without a mechanical roll",
                evidence=m.group(0),
            )
        )
    return flags


def detect_missing_context_names(
    text: str,
    *,
    scene: Scene | None,
    snapshot: StateSnapshot | None,
) -> list[ExtractionFlag]:
    """Flag names mentioned in prose that aren't in scene context.

    This is distinct from the candidate flow: candidates surface *new*
    names; this flag surfaces names that look established (e.g., they
    appear repeatedly or with possessive markers) but weren't in the
    turn's context — a possible inconsistency.
    """
    if scene is None and snapshot is None:
        return []
    known = _known_names(scene, snapshot)
    seen_counts: dict[str, int] = {}
    for match in _PROPER_NOUN.finditer(text):
        phrase = match.group(1)
        if phrase in _NAME_STOPLIST:
            continue
        seen_counts[phrase] = seen_counts.get(phrase, 0) + 1
    flags: list[ExtractionFlag] = []
    for phrase, count in seen_counts.items():
        if count < 2:
            continue
        if phrase in known:
            continue
        flags.append(
            ExtractionFlag(
                level=FlagLevel.MISSING_CONTEXT,
                code="referenced_without_context",
                message=(f"{phrase!r} appears repeatedly in prose but isn't in scene context"),
                evidence=phrase,
                related=[phrase],
            )
        )
    return flags


def run_heuristics(
    text: str,
    *,
    scene: Scene | None,
    snapshot: StateSnapshot | None,
    pre_roll_resolved: bool,
    max_candidates: int,
    campaign_id: CampaignId,
) -> HeuristicOutput:
    """Run all heuristic checks and return the combined output."""
    del campaign_id  # reserved for future scope-aware flags
    known = _known_names(scene, snapshot)
    candidates = find_proper_noun_candidates(text, known_names=known, max_candidates=max_candidates)
    flags: list[ExtractionFlag] = []
    flags.extend(detect_missing_mechanics(text, pre_roll_resolved=pre_roll_resolved))
    flags.extend(detect_missing_context_names(text, scene=scene, snapshot=snapshot))
    return HeuristicOutput(candidates=candidates, flags=flags)


__all__ = [
    "HeuristicOutput",
    "detect_missing_context_names",
    "detect_missing_mechanics",
    "find_proper_noun_candidates",
    "run_heuristics",
]
