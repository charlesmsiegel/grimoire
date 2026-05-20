"""Heuristic classifier for `LoreEntry` reclassification (spec §3).

Pure-Python rule-based suggester: looks at title shape, body pronouns, and
known nouns to decide whether a lore entry is "really" a character,
location, faction, or item. No LLM, no I/O. Both the standalone Convert
modal and the (future) import-dialog category dropdown call this through
`suggest_kind` to seed their default selection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from grimoire.types.common import EntityKind
from grimoire.types.world import LoreEntry

_LOCATION_NOUNS: frozenset[str] = frozenset(
    {
        "Keep", "District", "Forest", "Cathedral", "Quarter", "Sept",
        "Chantry", "Court", "Tower", "Hall", "Manor", "Crypt", "Chapel",
        "Castle", "Bridge", "Square", "Market", "Harbor", "Garden",
    }
)
_LOCATION_BODY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\blocated\b", re.IGNORECASE),
    re.compile(r"\b(north|south|east|west) of\b", re.IGNORECASE),
    re.compile(r"\bwithin the\b", re.IGNORECASE),
    re.compile(r"\bhalls of\b", re.IGNORECASE),
)

_FACTION_NOUNS: frozenset[str] = frozenset(
    {"Sect", "Clan", "House", "Order", "Guild", "Court", "Coterie", "Circle"}
)
_FACTION_BODY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bmembers\b", re.IGNORECASE),
    re.compile(r"\bruled by\b", re.IGNORECASE),
    re.compile(r"\bfounded\b", re.IGNORECASE),
    re.compile(r"\ballies\b", re.IGNORECASE),
)

_ITEM_NOUNS: frozenset[str] = frozenset(
    {"Sword", "Tome", "Amulet", "Grimoire", "Blade", "Ring", "Crown",
     "Staff", "Wand", "Cup", "Chalice", "Mirror", "Key"}
)
_ITEM_BODY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bgrants\b", re.IGNORECASE),
    re.compile(r"\bforged\b", re.IGNORECASE),
    re.compile(r"\bimbued\b", re.IGNORECASE),
    re.compile(r"\benchant", re.IGNORECASE),
)

_PRONOUN_RE = re.compile(
    r"\b(she|he|they|her|his|hers|him|them|their|theirs)\b",
    re.IGNORECASE,
)
_DETERMINER_RE = re.compile(r"^(The|A|An)\s+", re.IGNORECASE)
_PROPER_NOUN_TITLE_RE = re.compile(r"^([A-Z][a-z]+)(\s+[A-Z][a-z]+){0,2}$")
# Spec §3 character signal: first sentence matches "is a <profession/role>".
_ROLE_INTRO_RE = re.compile(r"\bis\s+(?:a|an|the)\s+[a-z]", re.IGNORECASE)

# Maximum total weight reachable for each kind. Confidence is the ratio of
# observed weight to this ceiling, so a kind with one weak signal scores
# low even if no other category fires, and the threshold filter can reject
# weak single-signal hits.
_MAX_WEIGHTS: dict[EntityKind, float] = {
    EntityKind.CHARACTER: 3.5,   # proper-noun(1) + pronoun bonus up to 2 + role-intro(0.5)
    EntityKind.LOCATION: 2.5,    # place noun(1.5) + "The"(0.5) + body match(0.5)
    EntityKind.FACTION: 2.5,     # org noun(1.5) + body matches(1.0)
    EntityKind.ITEM: 2.0,        # artifact noun(1.5) + body match(0.5)
}


@dataclass(frozen=True)
class Suggestion:
    kind: EntityKind
    confidence: float
    reason: str


def suggest_kind(entry: LoreEntry, *, threshold: float = 0.6) -> Suggestion:
    """Score a lore entry against the four target kinds; pick the top.

    Confidence is the top kind's accumulated weight divided by the maximum
    weight reachable for that kind (see ``_MAX_WEIGHTS``), clamped to
    ``[0, 1]``. If confidence is below ``threshold``, returns ``LORE`` with
    confidence ``0.0`` — "no strong signal; leave it as lore."
    """
    title = (entry.title or "").strip()
    body = entry.body or ""

    weights: dict[EntityKind, float] = {}
    reasons: dict[EntityKind, list[str]] = {}

    # Character signals.
    char_weight = 0.0
    char_reasons: list[str] = []
    if _PROPER_NOUN_TITLE_RE.match(_DETERMINER_RE.sub("", title)):
        char_weight += 1.0
        char_reasons.append("title looks like a proper noun")
    pronoun_hits = len(_PRONOUN_RE.findall(body))
    if pronoun_hits >= 3:
        char_weight += 1.0 + min(pronoun_hits / 10.0, 1.0)
        char_reasons.append(f"body uses pronouns {pronoun_hits} times")
    first_sentence = body.split(".", 1)[0] if body else ""
    if _ROLE_INTRO_RE.search(first_sentence):
        char_weight += 0.5
        char_reasons.append('first sentence matches "is a <role>"')
    if char_weight > 0:
        weights[EntityKind.CHARACTER] = char_weight
        reasons[EntityKind.CHARACTER] = char_reasons

    # Location signals.
    loc_weight = 0.0
    loc_reasons: list[str] = []
    if any(noun in title for noun in _LOCATION_NOUNS):
        loc_weight += 1.5
        loc_reasons.append("title contains a place noun")
    if title.startswith("The "):
        loc_weight += 0.5
        loc_reasons.append('title starts with "The"')
    for pat in _LOCATION_BODY_PATTERNS:
        if pat.search(body):
            loc_weight += 0.5
            loc_reasons.append(f"body matches {pat.pattern!r}")
            break
    if loc_weight > 0:
        weights[EntityKind.LOCATION] = loc_weight
        reasons[EntityKind.LOCATION] = loc_reasons

    # Faction signals.
    fac_weight = 0.0
    fac_reasons: list[str] = []
    if any(noun in title for noun in _FACTION_NOUNS):
        fac_weight += 1.5
        fac_reasons.append("title contains an organization noun")
    fac_body_hits = sum(1 for pat in _FACTION_BODY_PATTERNS if pat.search(body))
    if fac_body_hits >= 2:
        fac_weight += 1.0
        fac_reasons.append(f"body uses organizational language ({fac_body_hits} matches)")
    if fac_weight > 0:
        weights[EntityKind.FACTION] = fac_weight
        reasons[EntityKind.FACTION] = fac_reasons

    # Item signals.
    item_weight = 0.0
    item_reasons: list[str] = []
    if any(noun in title for noun in _ITEM_NOUNS):
        item_weight += 1.5
        item_reasons.append("title contains an artifact noun")
    for pat in _ITEM_BODY_PATTERNS:
        if pat.search(body):
            item_weight += 0.5
            item_reasons.append(f"body matches {pat.pattern!r}")
            break
    if item_weight > 0:
        weights[EntityKind.ITEM] = item_weight
        reasons[EntityKind.ITEM] = item_reasons

    if not weights:
        return Suggestion(kind=EntityKind.LORE, confidence=0.0, reason="no strong signal")

    top_kind = max(weights, key=lambda k: weights[k])
    top_weight = weights[top_kind]
    confidence = max(0.0, min(top_weight / _MAX_WEIGHTS[top_kind], 1.0))

    if confidence < threshold:
        return Suggestion(kind=EntityKind.LORE, confidence=0.0, reason="no strong signal")

    return Suggestion(
        kind=top_kind,
        confidence=confidence,
        reason="; ".join(reasons[top_kind]),
    )


__all__ = ["Suggestion", "suggest_kind"]
