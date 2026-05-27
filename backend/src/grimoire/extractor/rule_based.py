"""Rule-based extraction (spec 04 §1).

Regex and pattern matching for high-precision items: explicit time
markers, inventory verbs, and simple character actions. Deterministic
and fast; everything emitted starts at the rule-based base confidence.

The patterns intentionally lean conservative — we'd rather miss a
plausible event than fabricate one. Anything ambiguous is left to the
structured-LLM strategy.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import timedelta
from typing import Final

from grimoire.extractor.config import ExtractorConfig
from grimoire.types.common import CampaignId, Duration, Scope
from grimoire.types.state import DeltaKind, StateDelta

_TIME_NUMBER_WORDS: Final[dict[str, int]] = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "a few": 3,
    "several": 5,
    "many": 7,
}

# Map a unit token to (canonical_unit, seconds_per_unit).
_TIME_UNITS: Final[dict[str, tuple[str, int]]] = {
    "second": ("S", 1),
    "seconds": ("S", 1),
    "minute": ("M", 60),
    "minutes": ("M", 60),
    "hour": ("H", 3600),
    "hours": ("H", 3600),
    "day": ("D", 86400),
    "days": ("D", 86400),
    "week": ("W", 7 * 86400),
    "weeks": ("W", 7 * 86400),
}

_TIME_PHRASE = re.compile(
    r"""
    \b
    (?P<count>\d+|a\ few|several|many|a|an|one|two|three|four|five|six|seven|eight|nine|ten)
    \s+
    (?P<unit>seconds?|minutes?|hours?|days?|weeks?)
    \s+
    (?:passed?|later|go\ by|went\ by|drifted\ by|slipped\ by|elapsed)
    \b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_NEXT_MORNING = re.compile(
    r"\b(?:the\s+)?(?:next|following)\s+(morning|day|evening|night|afternoon)\b",
    re.IGNORECASE,
)

_INVENTORY_VERBS = re.compile(
    r"""
    \b
    (?P<actor>[A-Z][a-zA-Z'\-]+)\s+
    (?P<verb>picked\ up|grabbed|took|pocketed|produced|handed|gave|offered)\s+
    (?:the\ )?
    (?P<item>[a-zA-Z][a-zA-Z\ \-']{1,40}?)
    (?=[\.\,\;\!\?])
    """,
    re.VERBOSE,
)

_DROPPED_VERBS = re.compile(
    r"""
    \b
    (?P<actor>[A-Z][a-zA-Z'\-]+)\s+
    (?P<verb>dropped|discarded|left\ behind|set\ down|lost)\s+
    (?:the\ )?
    (?P<item>[a-zA-Z][a-zA-Z\ \-']{1,40}?)
    (?=[\.\,\;\!\?])
    """,
    re.VERBOSE,
)

# Loose mechanical-echo: "rolled N successes" / "took N damage" / etc.
_MECH_ROLL_ECHO = re.compile(
    r"\brolled\s+(?P<n>\d+)\s+success(?:es)?\b",
    re.IGNORECASE,
)
_MECH_DAMAGE = re.compile(
    r"""
    \b
    (?P<actor>[A-Z][a-zA-Z'\-]+)
    \s+(?:took|suffered|absorbed)\s+
    (?P<amount>\d+|a|light|heavy|grievous|mortal|aggravated|bashing|lethal)
    \s*(?P<kind>damage|wounds?|harm|bashing|lethal|aggravated)?
    \b
    """,
    re.VERBOSE,
)

# §9 Unresolved location reference — "I enter the X" / "they walk into the Y".
# Low-confidence (forces review). The structured-LLM strategy may also
# surface these, but the regex catches the common pattern cheaply.
_ENTERING_LOCATION = re.compile(
    r"\b(?:enter|enters|walks?\s+into|step\s+into|arrives?\s+at)\s+"
    r"(?:the\s+)?(?P<phrase>[a-z][a-z\s']{2,40})\b",
    re.IGNORECASE,
)


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned[:40] or "emergent-location"


# §5 Player-prose weather overrides. Each pattern maps to a WeatherKind.
# Patterns prefer phrasing that explicitly *changes* the weather rather
# than describing existing weather, to reduce false positives.
_WEATHER_OVERRIDE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:began|started)\s+to\s+rain\b|\brain\s+began\b", re.I), "rain"),
    (
        re.compile(
            r"\bsnow\s+(?:began|started)\s+falling\b|\b(?:began|started)\s+to\s+snow\b",
            re.I,
        ),
        "snow",
    ),
    (
        re.compile(r"\b(?:thunder|storm)\s+rolled\b|\b(?:began|started)\s+to\s+storm\b", re.I),
        "storm",
    ),
    (
        re.compile(r"\b(?:fog|mist)\s+(?:rolled\s+in|descended|crept)\b", re.I),
        "fog",
    ),
    (re.compile(r"\bwind\s+picked\s+up\b|\bwinds\s+rose\b", re.I), "wind"),
    (re.compile(r"\bskies?\s+cleared\b|\bsun\s+broke\s+through\b", re.I), "clear"),
)


def _number_for(token: str) -> int | None:
    tok = token.strip().lower()
    if tok.isdigit():
        return int(tok)
    return _TIME_NUMBER_WORDS.get(tok)


def _iso8601_duration(seconds: int) -> str:
    """Render a positive number of seconds as a coarse ISO-8601 duration.

    We only emit a single most-significant unit (e.g. PT2H, not PT2H30M).
    The model and the user both think in coarse increments; sub-minute
    precision rarely matters.
    """
    if seconds <= 0:
        return "PT0S"
    if seconds % (7 * 86400) == 0:
        return f"P{seconds // (7 * 86400)}W"
    if seconds % 86400 == 0:
        return f"P{seconds // 86400}D"
    if seconds % 3600 == 0:
        return f"PT{seconds // 3600}H"
    if seconds % 60 == 0:
        return f"PT{seconds // 60}M"
    return f"PT{seconds}S"


def _make_time_delta(
    *,
    seconds: int,
    evidence: str,
    confidence: float,
    campaign_id: CampaignId,
    target_id: str,
    source: str,
) -> StateDelta:
    iso = _iso8601_duration(seconds)
    duration = Duration(iso8601=iso, delta=timedelta(seconds=seconds))
    return StateDelta(
        kind=DeltaKind.TIME_ADVANCE,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id=target_id,
        target_table="calendar",
        after={
            "duration": duration.model_dump(mode="json"),
            "campaign_id": campaign_id,
        },
        confidence=confidence,
        source=source,
        evidence=evidence,
        extra={"strategy": "rule_based"},
    )


def _make_inventory_delta(
    *,
    actor: str,
    item: str,
    direction: str,  # 'gain' | 'loss'
    evidence: str,
    confidence: float,
    campaign_id: CampaignId,
    source: str,
) -> StateDelta:
    delta_value = "+1" if direction == "gain" else "-1"
    return StateDelta(
        kind=DeltaKind.INVENTORY_CHANGE,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id=f"{actor.lower()}:{item.strip().lower()}",
        target_table="character_state",
        after={
            "character_id": actor,
            "item": item.strip(),
            "delta": delta_value,
            "campaign_id": campaign_id,
        },
        confidence=confidence,
        source=source,
        evidence=evidence,
        extra={"strategy": "rule_based", "direction": direction},
    )


def _make_mechanical_event_delta(
    *,
    kind: str,
    actor: str | None,
    description: str,
    evidence: str,
    confidence: float,
    campaign_id: CampaignId,
    source: str,
) -> StateDelta:
    return StateDelta(
        kind=DeltaKind.MECHANICAL_EVENT,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id=f"{kind}:{(actor or 'unknown').lower()}",
        target_table="deltas",
        after={
            "event_kind": kind,
            "actor_ref": actor,
            "description": description,
            "campaign_id": campaign_id,
        },
        confidence=confidence,
        source=source,
        evidence=evidence,
        extra={"strategy": "rule_based"},
    )


def _make_emergent_location_delta(
    *,
    phrase: str,
    evidence: str,
    confidence: float,
    campaign_id: CampaignId,
    source: str,
) -> StateDelta:
    slug = _slug(phrase)
    return StateDelta(
        kind=DeltaKind.EMERGENT_CREATE,
        target_scope=Scope.CAMPAIGN_FILE,
        target_table=None,
        target_path=f"emergent/location/{slug}",
        target_id=f"emergent:location:{slug}",
        after={
            "campaign_id": campaign_id,
            "kind": "location",
            "name": phrase.strip(),
            "evidence": evidence,
        },
        confidence=confidence,
        source=source,
        evidence=evidence,
        extra={"strategy": "rule_based", "target": "emergent_location"},
    )


def _make_weather_override_delta(
    *,
    weather_kind: str,
    evidence: str,
    confidence: float,
    campaign_id: CampaignId,
    location_ref: str,
    source: str,
) -> StateDelta:
    return StateDelta(
        kind=DeltaKind.OVERRIDE_WRITE,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_table="location_state",
        target_id=location_ref,
        after={
            "campaign_id": campaign_id,
            "weather": {"kind": weather_kind, "source": "override"},
        },
        confidence=confidence,
        source=source,
        evidence=evidence,
        extra={"strategy": "rule_based", "target": "weather_override"},
    )


def extract_rule_based(
    text: str,
    *,
    campaign_id: CampaignId,
    config: ExtractorConfig,
    source: str = "extractor",
    scene_location_ref: str | None = None,
) -> Iterable[StateDelta]:
    """Yield deltas pulled out by the rule-based strategy.

    ``scene_location_ref``: when provided, enables §5 weather-override
    detection. Without a known location ref we have nowhere to attach
    the override, so the rule simply skips.
    """
    base = config.rule_based_base_confidence

    for match in _TIME_PHRASE.finditer(text):
        n = _number_for(match.group("count"))
        unit = match.group("unit").lower()
        if n is None or unit not in _TIME_UNITS:
            continue
        _canon_unit, secs_per = _TIME_UNITS[unit]
        seconds = n * secs_per
        iso = _iso8601_duration(seconds)
        yield _make_time_delta(
            seconds=seconds,
            evidence=match.group(0),
            confidence=base,
            campaign_id=campaign_id,
            target_id=f"time:{iso}",
            source=source,
        )

    for match in _NEXT_MORNING.finditer(text):
        phase = match.group(1).lower()
        # Treat 'next morning/day' as P1D; 'next evening/afternoon/night' as
        # the next named slot — modeled here uniformly as 12 hours forward,
        # which is good enough until the Time Engine resolves it precisely.
        seconds = 86400 if phase in {"morning", "day"} else 12 * 3600
        iso = _iso8601_duration(seconds)
        yield _make_time_delta(
            seconds=seconds,
            evidence=match.group(0),
            confidence=base * 0.95,  # slightly less confident than explicit counts
            campaign_id=campaign_id,
            target_id=f"time:{iso}",
            source=source,
        )

    for match in _INVENTORY_VERBS.finditer(text):
        verb = match.group("verb").lower()
        actor = match.group("actor")
        item = match.group("item")
        # "gave" + recipient is more complex; we capture as a gain for actor.
        # The structured-LLM strategy can refine direction across both ends.
        direction = "loss" if verb in {"handed", "gave", "offered"} else "gain"
        yield _make_inventory_delta(
            actor=actor,
            item=item,
            direction=direction,
            evidence=match.group(0),
            # Inventory phrasing is noisier; clamp below auto-apply.
            confidence=min(base, 0.8),
            campaign_id=campaign_id,
            source=source,
        )

    for match in _DROPPED_VERBS.finditer(text):
        yield _make_inventory_delta(
            actor=match.group("actor"),
            item=match.group("item"),
            direction="loss",
            evidence=match.group(0),
            confidence=min(base, 0.8),
            campaign_id=campaign_id,
            source=source,
        )

    for match in _MECH_ROLL_ECHO.finditer(text):
        yield _make_mechanical_event_delta(
            kind="roll_echo",
            actor=None,
            description=f"narrated {match.group('n')} successes",
            evidence=match.group(0),
            confidence=base,
            campaign_id=campaign_id,
            source=source,
        )

    for match in _MECH_DAMAGE.finditer(text):
        actor = match.group("actor")
        amount = (match.group("amount") or "").strip()
        damage_kind = (match.group("kind") or "").strip().lower() or "damage"
        yield _make_mechanical_event_delta(
            kind="wound",
            actor=actor,
            description=f"{amount} {damage_kind}".strip(),
            evidence=match.group(0),
            confidence=min(base, 0.85),
            campaign_id=campaign_id,
            source=source,
        )

    # §5 weather-override detection — only when we know which location to write to.
    if scene_location_ref:
        for pattern, weather_kind in _WEATHER_OVERRIDE_PATTERNS:
            match = pattern.search(text)
            if match is None:
                continue
            yield _make_weather_override_delta(
                weather_kind=weather_kind,
                evidence=match.group(0),
                confidence=min(base, 0.85),
                campaign_id=campaign_id,
                location_ref=scene_location_ref,
                source=source,
            )
            break  # only one weather override per extraction pass

    # §9 unresolved location reference — emergent-location candidates.
    # Confidence is deliberately low (0.4) so these route to the review
    # queue rather than auto-apply. The reviewer-approval path then
    # materializes via WorldService.apply_emergent_location_delta.
    seen_phrases: set[str] = set()
    for match in _ENTERING_LOCATION.finditer(text):
        phrase = match.group("phrase").strip().lower()
        if phrase in seen_phrases:
            continue
        seen_phrases.add(phrase)
        yield _make_emergent_location_delta(
            phrase=phrase,
            evidence=match.group(0),
            confidence=min(base, 0.4),
            campaign_id=campaign_id,
            source=source,
        )


__all__ = ["extract_rule_based"]
