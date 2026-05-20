# Lore Entry Reclassification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land sections 1, 2, 3, 5, 6 of `docs/superpowers/specs/2026-05-19-lore-reclassification-design.md` — conversion service, field-mapping, heuristic classifier, library "Convert to…" UI, audit/undo. Section 4 (import-dialog integration) is deferred until `card-imports` Task E2 lands.

**Architecture:** Single feature branch. Reclassification is a pure transform on a single `LoreEntry`: `(source, target_kind, overrides) → (new frontmatter, new body, warnings)`. The transform lives in `library/reclassify.py` so card-imports can later import it for the in-flight ingest path. The service wraps the transform with read/write/delete + a JSONL audit log per world. Frontend adds one modal and a row action.

**Tech Stack:** Python 3.12, FastAPI, pytest-asyncio, Pydantic v2 on the backend; React/TS + Vitest on the frontend. The library's `EntityKind` enum, `LibraryService.create_entity` / `get_entity` / `delete_entity`, and `EventBus._emit` are the existing primitives this plan composes.

---

## Codebase conventions you should know before starting

- **The spec says `setting_id` and `/library/settings/{sid}/...`. The actual codebase uses `world_id` and `/library/worlds/{world_id}/...`.** This plan uses `world_id` throughout (matches `LibraryService`, the REST router, the frontend `libraryApi`, and existing tests).
- `LibraryService` lives at `backend/src/grimoire/library/service.py`. Its mutating methods take a `source: str` kwarg (default `"user"`) that flows down to `StateStore.write_library_file`. Use `source=f"{actor}:reclassify"` and `source=f"{actor}:reclassify-cleanup"` so audit trails distinguish.
- `EntityKind` (`grimoire.types.common`) is a `StrEnum` with values `character`, `item`, `location`, `lore`, `faction`, `greeting`, `world`, `style_guide`, `image_preset`. The library service also accepts the directory-plural form (`"characters"`, `"factions"`, …) via `_normalize_kind`.
- `LibraryService.create_entity(world_id, kind, entity_id, frontmatter, body, *, source)` accepts any frontmatter dict — Pydantic models like `Character` / `Faction` / `Location` / `Item` are projections at read time, not validated on write. Extra fields persist in the file's YAML frontmatter.
- The `StateStore` instance is `library.store`; the data root is `library.store.data_root` (a `Path`). Use it to compute the audit-log path.
- Event emission: `await self._emit("event.name", {...})` is best-effort (no-op when no bus is wired).
- Tests use `pytest-asyncio` with the in-tree `conftest.py` fixtures: `store` (StateStore on tmp_path) and `library` (LibraryService(store)).
- Frontend API tests use `Vitest` + React Testing Library; the existing `frontend/src/routes/library/*.test.tsx` files are the closest pattern but you may not find one yet — write `ConvertModal.test.tsx` from scratch using Vitest's `describe/it/expect` and `@testing-library/react`.

---

## File structure

**Create:**
- `backend/src/grimoire/library/classify.py` — `suggest_kind`, `Suggestion`, signal lexicons.
- `backend/src/grimoire/library/reclassify.py` — `apply_mapping`, `ReclassificationResult`, audit-log read/append helpers.
- `backend/tests/library/test_classify.py` — heuristic on fixture lore entries.
- `backend/tests/library/test_reclassify.py` — service-level round-trips, overrides, failure modes, audit shape.
- `backend/tests/api/test_reclassify_routes.py` — REST preview/commit/undo/list.
- `frontend/src/routes/library/ConvertModal.tsx` — the conversion modal.
- `frontend/src/routes/library/ConvertModal.test.tsx` — mapping preview renders, commit button gated by required overrides.

**Modify:**
- `backend/src/grimoire/library/errors.py` — add `ReclassificationError`.
- `backend/src/grimoire/library/config.py` — add `LibraryReclassificationConfig` nested under `LibraryConfig`.
- `backend/src/grimoire/library/service.py` — add `reclassify_entity`, `preview_reclassification`, `undo_reclassification`, `list_reclassifications`.
- `backend/src/grimoire/api/library.py` — add 4 routes under `/library/worlds/{world_id}/...`.
- `frontend/src/api/library.ts` — add 4 client methods + result types.
- `frontend/src/routes/library/EntityListView.tsx` — render a "Convert to…" button on lore rows that opens `ConvertModal`.

---

## Conventions

- TDD: write the failing test first, then the implementation. Run `pytest` between steps.
- Commit after each task. Use prefix `feat(reclassify):`, `test(reclassify):`, or `refactor(reclassify):`.
- Run from the repo root (the worktree at `.worktrees/2026-05-19-lore-reclassification/`); pytest and the frontend tooling are already set up.

---

## Branch setup

You are already in the worktree at `.worktrees/2026-05-19-lore-reclassification/` on branch `2026-05-19-lore-reclassification`. **Skip the worktree-creation step.**

- [ ] **Step S1: Verify clean tree.**

```bash
git status
```

Expected: working tree clean (or only this plan file uncommitted).

---

# Branch — Backend

### Task 1: Heuristic classifier

Produces a `Suggestion(kind, confidence, reason)` from a `LoreEntry`. Pure function; no I/O. Used by both the standalone Convert modal (to seed the dropdown) and later by the import dialog.

**Files:**
- Create: `backend/src/grimoire/library/classify.py`.
- Test: `backend/tests/library/test_classify.py`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/library/test_classify.py
from grimoire.library.classify import Suggestion, suggest_kind
from grimoire.types.common import EntityKind
from grimoire.types.world import LoreEntry


def _lore(title: str, body: str = "", **kw) -> LoreEntry:
    return LoreEntry(world_id="w", id="x", title=title, body=body, **kw)


def test_character_signal_proper_noun_plus_pronouns() -> None:
    entry = _lore(
        "Beatrice",
        body="She was born in 1789. Her family disowned her. She studied alchemy.",
    )
    s = suggest_kind(entry)
    assert s.kind == EntityKind.CHARACTER
    assert s.confidence >= 0.6
    assert "pronoun" in s.reason.lower() or "proper noun" in s.reason.lower()


def test_location_signal_the_plus_place_noun() -> None:
    entry = _lore(
        "The Tremere Chantry",
        body="Located within the inner District, the chantry rises three floors.",
    )
    s = suggest_kind(entry)
    assert s.kind == EntityKind.LOCATION


def test_faction_signal_clan_noun() -> None:
    entry = _lore(
        "Clan Tremere",
        body="Members of the Clan are bound by blood oath. Founded in the 12th century.",
    )
    s = suggest_kind(entry)
    assert s.kind == EntityKind.FACTION


def test_item_signal_artifact_noun() -> None:
    entry = _lore(
        "Sword of Caine",
        body="The blade grants its wielder unnatural strength. Forged in the first city.",
    )
    s = suggest_kind(entry)
    assert s.kind == EntityKind.ITEM


def test_no_strong_signal_returns_lore() -> None:
    entry = _lore("Background note", body="It rained that night and the streets were wet.")
    s = suggest_kind(entry)
    assert s.kind == EntityKind.LORE
    assert s.confidence == 0.0


def test_threshold_overrides_default() -> None:
    entry = _lore("Beatrice", body="She walked.")
    relaxed = suggest_kind(entry, threshold=0.1)
    strict = suggest_kind(entry, threshold=0.95)
    assert relaxed.kind == EntityKind.CHARACTER
    assert strict.kind == EntityKind.LORE


def test_suggestion_is_frozen_dataclass() -> None:
    s = Suggestion(kind=EntityKind.CHARACTER, confidence=0.7, reason="x")
    import dataclasses
    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        s.confidence = 0.0  # type: ignore[misc]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest backend/tests/library/test_classify.py -v
```

Expected: ImportError on `grimoire.library.classify` (module not defined).

- [ ] **Step 3: Implement the classifier**

```python
# backend/src/grimoire/library/classify.py
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

_CHARACTER_WEIGHT = 1.0
_LOCATION_WEIGHT = 1.0
_FACTION_WEIGHT = 1.0
_ITEM_WEIGHT = 1.0


@dataclass(frozen=True)
class Suggestion:
    kind: EntityKind
    confidence: float
    reason: str


def suggest_kind(entry: LoreEntry, *, threshold: float = 0.6) -> Suggestion:
    """Score a lore entry against the four target kinds; pick the top.

    Confidence is `top_weight / sum_weights` clamped to `[0, 1]`. If the
    top weight is below `threshold`, returns LORE with confidence 0.0 —
    "no strong signal; leave it as lore."
    """
    title = (entry.title or "").strip()
    body = entry.body or ""

    weights: dict[EntityKind, float] = {}
    reasons: dict[EntityKind, list[str]] = {}

    # Character signals.
    char_weight = 0.0
    char_reasons: list[str] = []
    if _PROPER_NOUN_TITLE_RE.match(_DETERMINER_RE.sub("", title)):
        char_weight += _CHARACTER_WEIGHT * 1.0
        char_reasons.append("title looks like a proper noun")
    pronoun_hits = len(_PRONOUN_RE.findall(body))
    if pronoun_hits >= 3:
        char_weight += _CHARACTER_WEIGHT * (1.0 + min(pronoun_hits / 10.0, 1.0))
        char_reasons.append(f"body uses pronouns {pronoun_hits} times")
    if char_weight > 0:
        weights[EntityKind.CHARACTER] = char_weight
        reasons[EntityKind.CHARACTER] = char_reasons

    # Location signals.
    loc_weight = 0.0
    loc_reasons: list[str] = []
    if any(noun in title for noun in _LOCATION_NOUNS):
        loc_weight += _LOCATION_WEIGHT * 1.5
        loc_reasons.append("title contains a place noun")
    if title.startswith("The "):
        loc_weight += _LOCATION_WEIGHT * 0.5
        loc_reasons.append('title starts with "The"')
    for pat in _LOCATION_BODY_PATTERNS:
        if pat.search(body):
            loc_weight += _LOCATION_WEIGHT * 0.5
            loc_reasons.append(f"body matches {pat.pattern!r}")
            break
    if loc_weight > 0:
        weights[EntityKind.LOCATION] = loc_weight
        reasons[EntityKind.LOCATION] = loc_reasons

    # Faction signals.
    fac_weight = 0.0
    fac_reasons: list[str] = []
    if any(noun in title for noun in _FACTION_NOUNS):
        fac_weight += _FACTION_WEIGHT * 1.5
        fac_reasons.append("title contains an organization noun")
    fac_body_hits = sum(1 for pat in _FACTION_BODY_PATTERNS if pat.search(body))
    if fac_body_hits >= 2:
        fac_weight += _FACTION_WEIGHT * 1.0
        fac_reasons.append(f"body uses organizational language ({fac_body_hits} matches)")
    if fac_weight > 0:
        weights[EntityKind.FACTION] = fac_weight
        reasons[EntityKind.FACTION] = fac_reasons

    # Item signals.
    item_weight = 0.0
    item_reasons: list[str] = []
    if any(noun in title for noun in _ITEM_NOUNS):
        item_weight += _ITEM_WEIGHT * 1.5
        item_reasons.append("title contains an artifact noun")
    for pat in _ITEM_BODY_PATTERNS:
        if pat.search(body):
            item_weight += _ITEM_WEIGHT * 0.5
            item_reasons.append(f"body matches {pat.pattern!r}")
            break
    if item_weight > 0:
        weights[EntityKind.ITEM] = item_weight
        reasons[EntityKind.ITEM] = item_reasons

    if not weights:
        return Suggestion(kind=EntityKind.LORE, confidence=0.0, reason="no strong signal")

    top_kind = max(weights, key=lambda k: weights[k])
    top_weight = weights[top_kind]
    total = sum(weights.values())
    confidence = max(0.0, min(top_weight / total if total else 0.0, 1.0))

    if top_weight < threshold:
        return Suggestion(kind=EntityKind.LORE, confidence=0.0, reason="no strong signal")

    return Suggestion(
        kind=top_kind,
        confidence=confidence,
        reason="; ".join(reasons[top_kind]),
    )


__all__ = ["Suggestion", "suggest_kind"]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest backend/tests/library/test_classify.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/library/classify.py backend/tests/library/test_classify.py
git commit -m "feat(reclassify): add heuristic classifier for lore entries"
```

---

### Task 2: Field mapping + `ReclassificationResult`

Pure-function transform: takes a `LoreEntry` + target kind + overrides, returns the new entity's frontmatter dict, body string, and bookkeeping lists (kept/dropped/into_notes/warnings). Owned by `reclassify.py` so card-imports can later reuse it without round-tripping through the service.

**Files:**
- Create: `backend/src/grimoire/library/reclassify.py`.
- Test: `backend/tests/library/test_reclassify.py` (just the transform tests for this task; service tests come in Task 4).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/library/test_reclassify.py
from grimoire.library.reclassify import (
    ReclassificationResult,
    apply_mapping,
    required_overrides_for,
)
from grimoire.types.common import EntityKind
from grimoire.types.world import LoreEntry


def _lore(**kw) -> LoreEntry:
    base = dict(
        world_id="w",
        id="tremere-chantry",
        title="Tremere Chantry",
        body="The chantry rises three floors.",
        tags=["wod", "imported"],
        keywords=["chantry", "tremere"],
        related_factions=["tremere"],
        secrecy="restricted",
    )
    base.update(kw)
    return LoreEntry(**base)


def test_apply_mapping_to_character_keeps_core_fields() -> None:
    lore = _lore(title="Beatrice", body="She studied alchemy in the chantry.")
    fm, body, kept, dropped, into_notes, warnings = apply_mapping(
        lore, EntityKind.CHARACTER, overrides=None,
    )
    assert fm["name"] == "Beatrice"
    assert fm["aliases"] == ["chantry", "tremere"]
    assert fm["tags"] == ["wod", "imported"]
    assert fm["secrecy"] == "restricted"
    assert "She studied alchemy" in body
    assert "name" in kept
    assert "aliases" in kept
    assert "tags" in kept
    assert "secrecy" in kept


def test_apply_mapping_to_location_drops_secrecy_into_notes_section() -> None:
    lore = _lore(title="The Tremere Chantry")
    fm, body, kept, dropped, into_notes, warnings = apply_mapping(
        lore, EntityKind.LOCATION, overrides={"kind": "building"},
    )
    assert fm["name"] == "The Tremere Chantry"
    assert fm["kind"] == "building"
    # related_factions has no Location equivalent -> into notes.
    assert "related_factions" in into_notes
    assert "## Notes\n" in body
    assert "related_factions" in body


def test_apply_mapping_to_faction_maps_related_factions_to_allies() -> None:
    lore = _lore(title="House Tremere", related_factions=["camarilla"])
    fm, _body, kept, _dropped, _into_notes, _warnings = apply_mapping(
        lore, EntityKind.FACTION, overrides=None,
    )
    assert fm["allies"] == ["camarilla"]
    assert "allies" in kept


def test_apply_mapping_to_item_drops_secrecy() -> None:
    lore = _lore(title="Sword of Caine", secrecy="secret")
    fm, body, _kept, _dropped, into_notes, _warnings = apply_mapping(
        lore, EntityKind.ITEM, overrides=None,
    )
    assert "secrecy" not in fm
    assert "secrecy" in into_notes
    assert "secrecy" in body


def test_apply_mapping_overrides_win_over_defaults() -> None:
    lore = _lore(title="Beatrice")
    fm, _body, _kept, _dropped, _into_notes, _warnings = apply_mapping(
        lore, EntityKind.CHARACTER,
        overrides={"name": "Lady Beatrice", "role": "major_npc"},
    )
    assert fm["name"] == "Lady Beatrice"
    assert fm["role"] == "major_npc"


def test_required_overrides_for_location_includes_kind() -> None:
    required = required_overrides_for(EntityKind.LOCATION)
    assert "kind" in required


def test_required_overrides_for_character_is_empty() -> None:
    # Character.role has a sensible default in our import flow.
    required = required_overrides_for(EntityKind.CHARACTER)
    assert required == []


def test_reclassification_result_is_frozen() -> None:
    r = ReclassificationResult(
        source_id="lore/x",
        target_id="characters/x",
        target_kind=EntityKind.CHARACTER,
        fields_kept=[], fields_dropped=[], fields_into_notes=[], warnings=[],
    )
    import dataclasses
    import pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.target_id = "y"  # type: ignore[misc]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest backend/tests/library/test_reclassify.py -v
```

Expected: ImportError (`reclassify` module missing).

- [ ] **Step 3: Implement the transform**

```python
# backend/src/grimoire/library/reclassify.py
"""Pure-function mapping `LoreEntry` -> target-kind frontmatter+body (spec §2).

This module is the shared transform: the standalone `LibraryService.reclassify_entity`
flow calls `apply_mapping` after reading the source, and the (future)
card-imports E2 path will call it before writing the lore entry to disk.
Keeping the mapping table here (not on the service) is what makes the
import-time path possible without round-tripping a file.

Also owns the audit log helpers (`append_audit`, `iter_audit`) so the
audit log shape is co-located with the conversion logic that produces it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from grimoire.types.common import EntityKind
from grimoire.types.world import LoreEntry

# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReclassificationResult:
    source_id: str
    target_id: str
    target_kind: EntityKind
    fields_kept: list[str]
    fields_dropped: list[str]
    fields_into_notes: list[str]
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Mapping table (spec §2)
# --------------------------------------------------------------------------- #

# Per-target-kind direct field map: lore_field -> target_frontmatter_key.
_DIRECT_MAP: dict[EntityKind, dict[str, str]] = {
    EntityKind.CHARACTER: {
        "title": "name",
        "keywords": "aliases",
        "tags": "tags",
        "related_factions": "factions",
        "secrecy": "secrecy",
    },
    EntityKind.LOCATION: {
        "title": "name",
        "keywords": "aliases",
        "tags": "tags",
        "secrecy": "secrecy",
    },
    EntityKind.FACTION: {
        "title": "name",
        "keywords": "aliases",
        "tags": "tags",
        "related_factions": "allies",
        "secrecy": "secrecy",
    },
    EntityKind.ITEM: {
        "title": "name",
        "keywords": "aliases",
        "tags": "tags",
    },
}

# Fields that go into the body's "## Notes" section instead of frontmatter
# when converting to a given target kind. Order matters: rendered in this order.
_INTO_NOTES: dict[EntityKind, tuple[str, ...]] = {
    EntityKind.CHARACTER: ("secondary_keys", "comment"),
    EntityKind.LOCATION: ("secondary_keys", "comment", "related_factions"),
    EntityKind.FACTION: ("secondary_keys", "comment"),
    EntityKind.ITEM: ("secondary_keys", "comment", "related_factions", "secrecy"),
}

# Fields that are silently dropped (matching/scoring metadata only meaningful
# for LoreEntry's keyword-match algorithm). Always rolled up into a single
# warning per spec §2.
#
# NOTE: As of 2026-05-19 the LoreEntry model in types/world.py does not yet
# carry these fields — they are added by card-imports §3. The dropped-field
# logic is forward-compatible (`getattr(source, field, None)` returns None
# today) and will start firing automatically once card-imports lands and
# extends the model. Do not delete this list; it is the v2 surface area.
_DROPPED_MATCHING_FIELDS: frozenset[str] = frozenset(
    {
        "priority", "probability", "position", "at_depth", "scan_depth",
        "constant", "enabled", "case_sensitive", "match_whole_words",
        "selective_logic",
    }
)

# Per-field default sentinels: a value equal to its default should not be
# flagged as "dropped" because the user never set it. Defaults come from
# the card-imports-extended LoreEntry model (spec §3).
_DEFAULT_VALUES: dict[str, Any] = {
    "priority": 100,
    "probability": 100,
    "position": None,
    "at_depth": None,
    "scan_depth": None,
    "constant": False,
    "enabled": True,
    "case_sensitive": False,
    "match_whole_words": False,
    "selective_logic": None,
}

# Required overrides per target kind (target schema fields lore can't supply).
_REQUIRED_OVERRIDES: dict[EntityKind, tuple[str, ...]] = {
    EntityKind.CHARACTER: (),
    EntityKind.LOCATION: ("kind",),  # LocationKind has no safe default for imported lore.
    EntityKind.FACTION: (),
    EntityKind.ITEM: (),
}

_VALID_TARGET_KINDS: frozenset[EntityKind] = frozenset(
    {EntityKind.CHARACTER, EntityKind.LOCATION, EntityKind.FACTION, EntityKind.ITEM}
)


def required_overrides_for(target_kind: EntityKind) -> list[str]:
    """Return the override keys the UI must collect before allowing commit."""
    return list(_REQUIRED_OVERRIDES.get(target_kind, ()))


def apply_mapping(
    source: LoreEntry,
    target_kind: EntityKind,
    overrides: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str, list[str], list[str], list[str], list[str]]:
    """Map `source` to `(frontmatter, body, kept, dropped, into_notes, warnings)`.

    `overrides` patches the resulting frontmatter after the mapping runs, so a
    UI-collected `name` edit or required `Location.kind` value replaces the
    derived/default value. `frontmatter['id']` is **not** set here; callers
    derive the target id (collision-resolved) and set it themselves.
    """
    if target_kind not in _VALID_TARGET_KINDS:
        raise ValueError(
            f"reclassify target_kind must be one of "
            f"{sorted(k.value for k in _VALID_TARGET_KINDS)!r}, got {target_kind!r}"
        )

    fm: dict[str, Any] = {}
    kept: list[str] = []
    dropped: list[str] = []
    into_notes_keys: list[str] = []
    warnings: list[str] = []
    notes_payload: list[tuple[str, Any]] = []

    direct = _DIRECT_MAP[target_kind]
    into_notes_fields = _INTO_NOTES[target_kind]

    # Direct field map (kept).
    for src_field, target_key in direct.items():
        value = getattr(source, src_field, None)
        if value in (None, "", [], {}):
            continue
        fm[target_key] = value
        kept.append(target_key)

    # Into-notes bucket.
    for src_field in into_notes_fields:
        value = getattr(source, src_field, None)
        if value in (None, "", [], {}):
            continue
        into_notes_keys.append(src_field)
        notes_payload.append((src_field, value))

    # Preserve import_source verbatim if present.
    import_source = getattr(source, "import_source", None)
    if import_source is not None:
        fm["import_source"] = (
            import_source.model_dump() if hasattr(import_source, "model_dump") else import_source
        )
        kept.append("import_source")

    # Dropped matching metadata (single rollup warning). Only flags fields
    # the user actually customized away from their LoreEntry default.
    dropped_any = False
    for src_field in _DROPPED_MATCHING_FIELDS:
        value = getattr(source, src_field, None)
        default = _DEFAULT_VALUES.get(src_field)
        if value is None or value == default:
            continue
        dropped.append(src_field)
        dropped_any = True
    if dropped_any:
        warnings.append("matching metadata discarded (lore-only fields)")

    # Render body with optional notes appendix.
    body = source.body or ""
    if notes_payload:
        lines = ["", "## Notes", ""]
        for name, value in notes_payload:
            rendered = ", ".join(str(v) for v in value) if isinstance(value, list) else str(value)
            lines.append(f"- **{name}**: {rendered}")
        body = (body.rstrip() + "\n".join(lines) + "\n") if body else "\n".join(lines[1:]) + "\n"

    # Apply caller overrides last (UI edits, required-field values).
    if overrides:
        for key, value in overrides.items():
            fm[key] = value
            if key not in kept:
                kept.append(key)

    return fm, body, kept, dropped, into_notes_keys, warnings


# --------------------------------------------------------------------------- #
# Audit log (spec §6)
# --------------------------------------------------------------------------- #


def audit_log_path(data_root: Path) -> Path:
    """Return the shared reclassifications audit log path under `data_root`."""
    return data_root / "library" / "imports" / "reclassifications.jsonl"


def append_audit(
    data_root: Path,
    *,
    world_id: str,
    source_id: str,
    source_snapshot: dict[str, Any],
    target_id: str,
    target_kind: EntityKind,
    overrides: dict[str, Any],
    actor: str,
    ts: datetime | None = None,
) -> dict[str, Any]:
    """Append one JSONL record to the audit log; return the record."""
    record = {
        "ts": (ts or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z"),
        "world_id": world_id,
        "source_id": source_id,
        "source_snapshot": source_snapshot,
        "target_id": target_id,
        "target_kind": target_kind.value,
        "overrides": overrides or {},
        "actor": actor,
    }
    path = audit_log_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


def iter_audit(data_root: Path, *, world_id: str | None = None) -> Iterator[dict[str, Any]]:
    """Yield audit records in append order, optionally filtered by world_id."""
    path = audit_log_path(data_root)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if world_id is not None and record.get("world_id") != world_id:
                continue
            yield record


__all__ = [
    "ReclassificationResult",
    "apply_mapping",
    "append_audit",
    "audit_log_path",
    "iter_audit",
    "required_overrides_for",
]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest backend/tests/library/test_reclassify.py -v
```

Expected: all transform tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/library/reclassify.py backend/tests/library/test_reclassify.py
git commit -m "feat(reclassify): add lore->entity field mapping transform + audit helpers"
```

---

### Task 3: Errors + config

Adds the new error class and a config block so `LibraryConfig` can override the audit log path, suggestion threshold, and undo window.

**Files:**
- Modify: `backend/src/grimoire/library/errors.py`.
- Modify: `backend/src/grimoire/library/config.py`.
- Test: `backend/tests/library/test_config.py`.

- [ ] **Step 1: Write the failing config test**

Append to `backend/tests/library/test_config.py`:

```python
def test_reclassification_config_defaults() -> None:
    from grimoire.library.config import LibraryConfig

    cfg = LibraryConfig()
    assert cfg.reclassification.suggestion_threshold == 0.6
    assert cfg.reclassification.undo_window_days == 30
    # audit_log path may be None (use data_root default) or a Path
    assert cfg.reclassification.audit_log is None


def test_reclassification_config_from_yaml(tmp_path) -> None:
    from grimoire.library.config import LibraryConfig

    path = tmp_path / "lib.yaml"
    path.write_text(
        "library:\n"
        "  reclassification:\n"
        "    audit_log: /tmp/reclass.jsonl\n"
        "    suggestion_threshold: 0.8\n"
        "    undo_window_days: 7\n",
        encoding="utf-8",
    )
    cfg = LibraryConfig.from_yaml(path)
    assert cfg.reclassification.suggestion_threshold == 0.8
    assert cfg.reclassification.undo_window_days == 7
    assert str(cfg.reclassification.audit_log).endswith("reclass.jsonl")
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest backend/tests/library/test_config.py -v -k reclassification
```

Expected: AttributeError on `cfg.reclassification`.

- [ ] **Step 3: Add the error class.**

Append to `backend/src/grimoire/library/errors.py`:

```python
class ReclassificationError(LibraryError):
    """Raised when reclassify_entity / undo_reclassification cannot proceed."""
```

- [ ] **Step 4: Add the config block**

Edit `backend/src/grimoire/library/config.py`. Add a new dataclass before `LibraryConfig` and a new field on `LibraryConfig`:

```python
# Insert after LibraryPromotionConfig:
@dataclass(frozen=True, slots=True)
class LibraryReclassificationConfig:
    # Override for the audit-log path. `None` means
    # `<data_root>/library/imports/reclassifications.jsonl`.
    audit_log: Path | None = None
    # Heuristic suggestions below this confidence default the dropdown to "lore".
    suggestion_threshold: float = 0.6
    # Records older than this can be pruned from the log (cron not implemented in v1;
    # surfaced for downstream maintenance jobs and for the UI to grey out old undo links).
    undo_window_days: int = 30
```

Add to `LibraryConfig` (after the `promotion` field):

```python
    reclassification: LibraryReclassificationConfig = field(
        default_factory=LibraryReclassificationConfig
    )
```

And in `_from_mapping`, after parsing `prom`, add:

```python
        reclass = raw.get("reclassification") or {}
        audit_log_raw = reclass.get("audit_log")
        audit_log = Path(audit_log_raw).expanduser() if audit_log_raw else None
        reclassification_cfg = LibraryReclassificationConfig(
            audit_log=audit_log,
            suggestion_threshold=float(reclass.get("suggestion_threshold", 0.6)),
            undo_window_days=int(reclass.get("undo_window_days", 30)),
        )
```

Then add `reclassification=reclassification_cfg,` to the `cls(...)` call at the bottom of `_from_mapping`.

Add `"LibraryReclassificationConfig"` to `__all__`.

- [ ] **Step 5: Run all library config tests**

```bash
pytest backend/tests/library/test_config.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/library/errors.py backend/src/grimoire/library/config.py backend/tests/library/test_config.py
git commit -m "feat(reclassify): add ReclassificationError + reclassification config block"
```

---

### Task 4: `LibraryService.reclassify_entity` + `preview_reclassification`

Wraps the transform with reads/writes and emits an event. Source is always a `LoreEntry`; target is one of {character, location, faction, item}.

**Files:**
- Modify: `backend/src/grimoire/library/service.py`.
- Modify: `backend/src/grimoire/library/__init__.py` (export `ReclassificationError`, `ReclassificationResult`, `Suggestion`).
- Test: `backend/tests/library/test_reclassify.py` (append service-level tests).

- [ ] **Step 1: Append the failing service tests** to `backend/tests/library/test_reclassify.py`:

```python
# (append to existing file)
import pytest

from grimoire.library import LibraryNotFoundError, LibraryService
from grimoire.library.errors import ReclassificationError
from grimoire.library.reclassify import iter_audit
from grimoire.state_store import StateStore


async def _seed_world(store: StateStore, world_id: str) -> None:
    await store.write_library_file(
        library_id=f"worlds/{world_id}/world",
        frontmatter={"id": world_id, "name": world_id, "version": 1},
        body="",
        source="user",
    )


async def _seed_lore(
    store: StateStore,
    world_id: str,
    entity_id: str,
    *,
    title: str,
    body: str = "",
    **extras,
) -> None:
    fm: dict = {"id": entity_id, "title": title}
    fm.update(extras)
    await store.write_library_file(
        library_id=f"worlds/{world_id}/lore/{entity_id}",
        frontmatter=fm,
        body=body,
        source="user",
    )


async def test_reclassify_to_character_writes_target_and_deletes_source(
    library: LibraryService, store: StateStore,
) -> None:
    await _seed_world(store, "w")
    await _seed_lore(
        store, "w", "beatrice",
        title="Beatrice", body="She studied alchemy.",
        keywords=["tremere", "beatrice"], tags=["wod"],
    )
    result = await library.reclassify_entity(
        "w", "beatrice",
        target_kind=EntityKind.CHARACTER, overrides=None, actor="tester",
    )
    assert result.target_kind == EntityKind.CHARACTER
    assert result.target_id  # set by the service
    # Source is gone.
    with pytest.raises(LibraryNotFoundError):
        await library.get_entity("w", "lore", "beatrice")
    # Target exists.
    target = await library.get_entity("w", "character", result.target_id)
    assert target.name == "Beatrice"
    assert target.frontmatter.get("aliases") == ["tremere", "beatrice"]


async def test_reclassify_to_location_requires_kind_override(
    library: LibraryService, store: StateStore,
) -> None:
    await _seed_world(store, "w")
    await _seed_lore(store, "w", "chantry", title="The Chantry")
    with pytest.raises(ReclassificationError, match="kind"):
        await library.reclassify_entity(
            "w", "chantry",
            target_kind=EntityKind.LOCATION, overrides=None,
        )


async def test_reclassify_resolves_target_id_collisions_with_suffix(
    library: LibraryService, store: StateStore,
) -> None:
    await _seed_world(store, "w")
    # Existing character with the slug that would collide.
    await store.write_library_file(
        library_id="worlds/w/characters/beatrice",
        frontmatter={"id": "beatrice", "name": "Other Beatrice"},
        body="",
        source="user",
    )
    await _seed_lore(store, "w", "beatrice-lore", title="Beatrice", body="She lived.")
    result = await library.reclassify_entity(
        "w", "beatrice-lore",
        target_kind=EntityKind.CHARACTER, overrides=None,
    )
    assert result.target_id == "beatrice-2"


async def test_reclassify_appends_audit_record(
    library: LibraryService, store: StateStore,
) -> None:
    await _seed_world(store, "w")
    await _seed_lore(store, "w", "beatrice", title="Beatrice", body="She lived.")
    await library.reclassify_entity(
        "w", "beatrice", target_kind=EntityKind.CHARACTER, overrides=None, actor="tester",
    )
    records = list(iter_audit(store.data_root, world_id="w"))
    assert len(records) == 1
    assert records[0]["source_id"] == "beatrice"
    assert records[0]["target_kind"] == "character"
    assert records[0]["actor"] == "tester"
    assert records[0]["source_snapshot"]["frontmatter"]["title"] == "Beatrice"


async def test_reclassify_missing_source_raises_not_found(
    library: LibraryService,
) -> None:
    with pytest.raises(LibraryNotFoundError):
        await library.reclassify_entity(
            "w", "missing",
            target_kind=EntityKind.CHARACTER, overrides=None,
        )


async def test_preview_reclassification_returns_mapping_without_writing(
    library: LibraryService, store: StateStore,
) -> None:
    await _seed_world(store, "w")
    await _seed_lore(
        store, "w", "beatrice",
        title="Beatrice", body="She studied alchemy.", keywords=["b"], tags=["wod"],
    )
    preview = await library.preview_reclassification(
        "w", "beatrice", target_kind=EntityKind.CHARACTER,
    )
    assert preview["target_kind"] == "character"
    assert preview["frontmatter"]["name"] == "Beatrice"
    assert preview["required_overrides"] == []
    assert "kept" in preview and "dropped" in preview and "into_notes" in preview
    assert preview["suggestion"]["kind"] in {"character", "lore"}
    # Source still exists (preview did not delete).
    assert (await library.get_entity("w", "lore", "beatrice")).asset_id == "beatrice"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest backend/tests/library/test_reclassify.py -v -k "reclassify or preview"
```

Expected: AttributeError on `library.reclassify_entity` and `library.preview_reclassification`.

- [ ] **Step 3: Add helpers + methods to `LibraryService`.**

At the top of `backend/src/grimoire/library/service.py`, add to imports:

```python
from grimoire.library.classify import Suggestion, suggest_kind
from grimoire.library.errors import (
    LibraryConflictError,
    LibraryError,
    LibraryNotFoundError,
    PromotionError,
    ReclassificationError,
)
from grimoire.library.reclassify import (
    ReclassificationResult,
    append_audit,
    apply_mapping,
    audit_log_path,
    iter_audit,
    required_overrides_for,
)
from grimoire.types.world import LoreEntry
```

Then add a section comment + methods (insert before the "Promotion" comment block in service.py):

```python
    # ------------------------------------------------------------------ #
    # Reclassification (spec §§1, 2, 6)
    # ------------------------------------------------------------------ #

    async def preview_reclassification(
        self,
        world_id: str,
        source_id: str,
        *,
        target_kind: EntityKind | str,
    ) -> dict[str, Any]:
        """Return the mapping result without writing — used by the Convert modal."""
        target = self._coerce_target_kind(target_kind)
        source_entity = await self.get_entity(world_id, "lore", source_id)
        lore = _lore_from_entity(source_entity)
        fm, body, kept, dropped, into_notes, warnings = apply_mapping(lore, target, None)
        suggestion = suggest_kind(lore, threshold=self.config.reclassification.suggestion_threshold)
        return {
            "source_id": source_id,
            "target_kind": target.value,
            "frontmatter": fm,
            "body": body,
            "kept": kept,
            "dropped": dropped,
            "into_notes": into_notes,
            "warnings": warnings,
            "required_overrides": required_overrides_for(target),
            "suggestion": {
                "kind": suggestion.kind.value,
                "confidence": suggestion.confidence,
                "reason": suggestion.reason,
            },
        }

    async def reclassify_entity(
        self,
        world_id: str,
        source_id: str,
        *,
        target_kind: EntityKind | str,
        overrides: dict[str, Any] | None = None,
        actor: str = "user",
    ) -> ReclassificationResult:
        """Convert a lore entry into a new entity of `target_kind`.

        Order of operations: validate required overrides → read source →
        resolve target id (collision suffix) → create target → delete source
        → append audit → emit event. A failure at create surfaces as
        ReclassificationError with the source intact; a failure at delete is
        a non-fatal warning on the result.
        """
        target = self._coerce_target_kind(target_kind)
        overrides = dict(overrides or {})

        missing = [
            key for key in required_overrides_for(target)
            if key not in overrides or overrides[key] in (None, "")
        ]
        if missing:
            raise ReclassificationError(
                f"missing required override(s) for {target.value}: {missing!r}"
            )

        source_entity = await self.get_entity(world_id, "lore", source_id)
        lore = _lore_from_entity(source_entity)
        fm, body, kept, dropped, into_notes, warnings = apply_mapping(lore, target, overrides)

        derived = _slugify(fm.get("name") or source_id)
        target_id = await self._collision_suffix(world_id, target, derived)
        fm["id"] = target_id

        try:
            await self.create_entity(
                world_id, target, target_id, fm, body,
                source=f"{actor}:reclassify",
            )
        except Exception as exc:
            raise ReclassificationError(
                f"failed to create {target.value}/{target_id}: {exc}"
            ) from exc

        try:
            await self.delete_entity(world_id, "lore", source_id, source=f"{actor}:reclassify")
        except Exception as exc:
            warnings.append(f"source not deleted: {exc}")

        # v1: always writes to <data_root>/library/imports/reclassifications.jsonl.
        # cfg.reclassification.audit_log override exists in config but is honored
        # only when a future caller passes an explicit data_root; not wired today.
        try:
            append_audit(
                self.store.data_root,
                world_id=world_id,
                source_id=source_id,
                source_snapshot={
                    "frontmatter": dict(source_entity.frontmatter or {}),
                    "body": source_entity.body or "",
                },
                target_id=target_id,
                target_kind=target,
                overrides=overrides,
                actor=actor,
            )
        except Exception as exc:
            warnings.append(f"audit log write failed: {exc}")

        await self._emit(
            "library.reclassify",
            {
                "world_id": world_id,
                "source_id": source_id,
                "target_id": target_id,
                "target_kind": target.value,
                "actor": actor,
                "warnings": warnings,
            },
        )

        return ReclassificationResult(
            source_id=source_id,
            target_id=target_id,
            target_kind=target,
            fields_kept=kept,
            fields_dropped=dropped,
            fields_into_notes=into_notes,
            warnings=warnings,
        )

    async def _collision_suffix(
        self,
        world_id: str,
        kind: EntityKind,
        base_id: str,
    ) -> str:
        """Return base_id unless it collides; otherwise base_id-2, -3, ..., -99."""
        candidate = base_id
        for n in range(1, 100):
            library_id = make_library_id(world_id, kind.value, candidate)
            existing = await self.store.get_library_entity(library_id)
            if existing is None:
                return candidate
            candidate = f"{base_id}-{n + 1}"
        raise ReclassificationError(
            f"too many id collisions for {kind.value}/{base_id} (>99); refusing to write"
        )

    def _coerce_target_kind(self, target_kind: EntityKind | str) -> EntityKind:
        if isinstance(target_kind, EntityKind):
            value = target_kind
        else:
            try:
                value = EntityKind(_normalize_kind(target_kind))
            except ValueError as exc:
                raise ReclassificationError(f"unknown target_kind {target_kind!r}") from exc
        if value not in {EntityKind.CHARACTER, EntityKind.LOCATION, EntityKind.FACTION, EntityKind.ITEM}:
            raise ReclassificationError(
                f"reclassify target must be character/location/faction/item, got {value.value!r}"
            )
        return value
```

At the bottom of the file (with the other helpers), add:

```python
import re as _re


def _slugify(value: str) -> str:
    """Crude ASCII slugifier: lowercase, non-alphanum -> hyphens, collapse + trim."""
    value = value.strip().lower()
    value = _re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "entity"


def _lore_from_entity(entity: LibraryEntity) -> LoreEntry:
    """Construct a `LoreEntry` from a freshly-read library entity row."""
    fm = dict(entity.frontmatter or {})
    fm.setdefault("world_id", entity.world_id or "")
    fm.setdefault("id", entity.asset_id)
    fm.setdefault("title", fm.get("name") or entity.asset_id)
    if entity.body and "body" not in fm:
        fm["body"] = entity.body
    return LoreEntry.model_validate(fm)
```

(Add `LibraryEntity` to the existing `grimoire.types.composition` import if not already present — it is.)

- [ ] **Step 4: Export the new types from `library/__init__.py`.**

Open `backend/src/grimoire/library/__init__.py` and add to the `__all__` / exports list:

```python
from grimoire.library.classify import Suggestion, suggest_kind  # noqa: F401
from grimoire.library.errors import ReclassificationError  # noqa: F401
from grimoire.library.reclassify import (  # noqa: F401
    ReclassificationResult,
    apply_mapping,
    required_overrides_for,
)
```

(If the file uses an explicit `__all__` list, extend it with the new names.)

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest backend/tests/library/test_reclassify.py -v
```

Expected: all tests (transform + service) PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/library/service.py backend/src/grimoire/library/__init__.py backend/tests/library/test_reclassify.py
git commit -m "feat(reclassify): add LibraryService.reclassify_entity + preview"
```

---

### Task 5: Undo + list reclassifications

Reads the audit log, recreates the source from `source_snapshot`, deletes the target, appends an inverse audit record. Best-effort: if downstream entities reference the target the undo proceeds anyway and `LibraryService.dependents` warnings surface in the result.

**Files:**
- Modify: `backend/src/grimoire/library/service.py`.
- Test: `backend/tests/library/test_reclassify.py` (append undo tests).

- [ ] **Step 1: Append failing undo tests** to `backend/tests/library/test_reclassify.py`:

```python
async def test_undo_reclassification_recreates_source_and_deletes_target(
    library: LibraryService, store: StateStore,
) -> None:
    await _seed_world(store, "w")
    await _seed_lore(store, "w", "beatrice", title="Beatrice", body="She lived.")
    result = await library.reclassify_entity(
        "w", "beatrice", target_kind=EntityKind.CHARACTER, overrides=None, actor="tester",
    )
    records = list(iter_audit(store.data_root, world_id="w"))
    ts = records[0]["ts"]

    undo_result = await library.undo_reclassification("w", ts, actor="tester")
    assert undo_result["restored_source_id"] == "beatrice"
    assert undo_result["deleted_target_id"] == result.target_id
    # Source is back.
    restored = await library.get_entity("w", "lore", "beatrice")
    assert restored.frontmatter.get("title") == "Beatrice"
    # Target is gone.
    with pytest.raises(LibraryNotFoundError):
        await library.get_entity("w", "character", result.target_id)
    # Inverse audit appended.
    records_after = list(iter_audit(store.data_root, world_id="w"))
    assert len(records_after) == 2
    assert records_after[1]["overrides"].get("_undo_of") == ts


async def test_undo_with_collision_suffixes_restored_source(
    library: LibraryService, store: StateStore,
) -> None:
    await _seed_world(store, "w")
    await _seed_lore(store, "w", "beatrice", title="Beatrice", body="She lived.")
    result = await library.reclassify_entity(
        "w", "beatrice", target_kind=EntityKind.CHARACTER, overrides=None,
    )
    # Re-create a lore with the same id between reclassify and undo.
    await _seed_lore(store, "w", "beatrice", title="Different Beatrice", body="x")

    records = list(iter_audit(store.data_root, world_id="w"))
    ts = records[0]["ts"]

    undo_result = await library.undo_reclassification("w", ts)
    # Collision -> restored under suffixed id.
    assert undo_result["restored_source_id"] == "beatrice-2"


async def test_undo_missing_timestamp_raises(library: LibraryService, store: StateStore) -> None:
    await _seed_world(store, "w")
    with pytest.raises(ReclassificationError, match="no audit"):
        await library.undo_reclassification("w", "2026-05-19T00:00:00Z")


async def test_list_reclassifications_returns_records_in_order(
    library: LibraryService, store: StateStore,
) -> None:
    await _seed_world(store, "w")
    await _seed_lore(store, "w", "a", title="Beatrice", body="She lived.")
    await _seed_lore(store, "w", "b", title="Caine", body="He walked.")
    await library.reclassify_entity("w", "a", target_kind=EntityKind.CHARACTER)
    await library.reclassify_entity("w", "b", target_kind=EntityKind.CHARACTER)
    records = await library.list_reclassifications("w")
    assert len(records) == 2
    assert records[0]["source_id"] == "a"
    assert records[1]["source_id"] == "b"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest backend/tests/library/test_reclassify.py -v -k "undo or list_reclassifications"
```

Expected: AttributeError on `undo_reclassification`, `list_reclassifications`.

- [ ] **Step 3: Implement `undo_reclassification` + `list_reclassifications`**

Insert into the "Reclassification" section of `service.py` (after `reclassify_entity`):

```python
    async def list_reclassifications(self, world_id: str) -> list[dict[str, Any]]:
        """Return audit records for `world_id` in append order."""
        return list(iter_audit(self.store.data_root, world_id=world_id))

    async def undo_reclassification(
        self,
        world_id: str,
        timestamp: str,
        *,
        actor: str = "user",
    ) -> dict[str, Any]:
        """Reverse a reclassification by timestamp.

        Reads `source_snapshot`, recreates the source under its original id
        (collision-suffixed if needed), deletes the target, and appends an
        inverse audit record stamped with `_undo_of: <original_ts>` in
        `overrides`. Returns a summary dict with the restored and deleted
        ids plus warnings.
        """
        target_record: dict[str, Any] | None = None
        for record in iter_audit(self.store.data_root, world_id=world_id):
            if record.get("ts") == timestamp:
                target_record = record
                break
        if target_record is None:
            raise ReclassificationError(
                f"no audit record for world {world_id!r} at ts {timestamp!r}"
            )

        original_source_id = target_record["source_id"]
        snapshot = target_record["source_snapshot"]
        target_id = target_record["target_id"]
        target_kind = EntityKind(target_record["target_kind"])

        warnings: list[str] = []

        restored_id = await self._collision_suffix(world_id, EntityKind.LORE, original_source_id)
        snapshot_fm = dict(snapshot.get("frontmatter") or {})
        snapshot_fm["id"] = restored_id
        snapshot_body = snapshot.get("body") or ""
        await self.create_entity(
            world_id, "lore", restored_id, snapshot_fm, snapshot_body,
            source=f"{actor}:reclassify-undo",
        )

        try:
            await self.delete_entity(
                world_id, target_kind.value, target_id,
                source=f"{actor}:reclassify-undo",
            )
        except LibraryNotFoundError:
            warnings.append(f"target {target_kind.value}/{target_id} already deleted")
        except Exception as exc:
            warnings.append(f"target not deleted: {exc}")

        # Surface dangling references so callers can render a warning.
        try:
            deps = await self.dependents(world_id, target_kind.value, target_id)
            if deps:
                warnings.append(
                    f"target was referenced by {len(deps)} campaign(s); "
                    "those refs are now dangling"
                )
        except Exception:
            pass  # best-effort.

        try:
            append_audit(
                self.store.data_root,
                world_id=world_id,
                source_id=target_id,                     # the original target is the "from" now
                source_snapshot={},                       # target was already validated; no snapshot needed
                target_id=restored_id,
                target_kind=EntityKind.LORE,
                overrides={"_undo_of": timestamp},
                actor=actor,
            )
        except Exception as exc:
            warnings.append(f"audit log write failed: {exc}")

        await self._emit(
            "library.reclassify_undo",
            {
                "world_id": world_id,
                "restored_source_id": restored_id,
                "deleted_target_id": target_id,
                "undo_of": timestamp,
                "warnings": warnings,
            },
        )

        return {
            "restored_source_id": restored_id,
            "deleted_target_id": target_id,
            "undo_of": timestamp,
            "warnings": warnings,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest backend/tests/library/test_reclassify.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/library/service.py backend/tests/library/test_reclassify.py
git commit -m "feat(reclassify): add undo_reclassification + list_reclassifications"
```

---

### Task 6: REST routes

Four endpoints (preview, commit, undo, list) under `/library/worlds/{world_id}/...`.

**Files:**
- Modify: `backend/src/grimoire/api/library.py`.
- Test: `backend/tests/api/test_reclassify_routes.py` (new).

- [ ] **Step 1: Write the failing route tests**

```python
# backend/tests/api/test_reclassify_routes.py
"""REST contract tests for the reclassification routes."""

from __future__ import annotations

from typing import Any


class FakeReclassifyLibrary:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def preview_reclassification(
        self, world_id: str, source_id: str, *, target_kind: str,
    ) -> dict[str, Any]:
        self.calls.append(("preview", world_id, source_id, target_kind))
        return {
            "source_id": source_id,
            "target_kind": target_kind,
            "frontmatter": {"name": "Beatrice"},
            "body": "She lived.",
            "kept": ["name"],
            "dropped": [],
            "into_notes": [],
            "warnings": [],
            "required_overrides": [],
            "suggestion": {"kind": "character", "confidence": 0.9, "reason": "pronouns"},
        }

    async def reclassify_entity(
        self, world_id: str, source_id: str, *, target_kind: str,
        overrides: dict | None = None, actor: str = "user",
    ) -> Any:
        from grimoire.library.reclassify import ReclassificationResult
        from grimoire.types.common import EntityKind
        self.calls.append(("commit", world_id, source_id, target_kind, overrides, actor))
        return ReclassificationResult(
            source_id=source_id,
            target_id="beatrice",
            target_kind=EntityKind(target_kind),
            fields_kept=["name"],
            fields_dropped=[],
            fields_into_notes=[],
            warnings=[],
        )

    async def undo_reclassification(
        self, world_id: str, timestamp: str, *, actor: str = "user",
    ) -> dict[str, Any]:
        self.calls.append(("undo", world_id, timestamp, actor))
        return {
            "restored_source_id": "beatrice",
            "deleted_target_id": "beatrice",
            "undo_of": timestamp,
            "warnings": [],
        }

    async def list_reclassifications(self, world_id: str) -> list[dict[str, Any]]:
        self.calls.append(("list", world_id))
        return [{"ts": "2026-05-19T00:00:00Z", "source_id": "x", "target_kind": "character"}]


def test_preview_reclassify_returns_mapping(client, container) -> None:
    container.library = FakeReclassifyLibrary()
    response = client.get(
        "/api/library/worlds/w/lore/beatrice/reclassify/preview?target_kind=character"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["frontmatter"]["name"] == "Beatrice"
    assert body["suggestion"]["kind"] == "character"


def test_commit_reclassify_writes_target(client, container) -> None:
    fake = FakeReclassifyLibrary()
    container.library = fake
    response = client.post(
        "/api/library/worlds/w/lore/beatrice/reclassify",
        json={"target_kind": "character", "overrides": {"role": "major_npc"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["target_id"] == "beatrice"
    assert body["target_kind"] == "character"
    assert ("commit", "w", "beatrice", "character", {"role": "major_npc"}, "user") in fake.calls


def test_undo_reclassify_calls_service(client, container) -> None:
    fake = FakeReclassifyLibrary()
    container.library = fake
    ts = "2026-05-19T12:00:00Z"
    response = client.post(f"/api/library/worlds/w/reclassifications/{ts}/undo")
    assert response.status_code == 200
    body = response.json()
    assert body["restored_source_id"] == "beatrice"
    assert ("undo", "w", ts, "user") in fake.calls


def test_list_reclassifications_returns_records(client, container) -> None:
    container.library = FakeReclassifyLibrary()
    response = client.get("/api/library/worlds/w/reclassifications")
    assert response.status_code == 200
    body = response.json()
    assert body == [{"ts": "2026-05-19T00:00:00Z", "source_id": "x", "target_kind": "character"}]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest backend/tests/api/test_reclassify_routes.py -v
```

Expected: 404 from FastAPI (routes not defined).

- [ ] **Step 3: Add the routes to `backend/src/grimoire/api/library.py`.**

After the existing `entity_dependents` route (around line 285), add:

```python
class ReclassifyCommitPayload(BaseModel):
    target_kind: str
    overrides: dict[str, Any] | None = None
    actor: str = "user"


@router.get("/library/worlds/{world_id}/lore/{entity_id}/reclassify/preview")
async def preview_reclassify(
    world_id: str,
    entity_id: str,
    library: LibraryDep,
    target_kind: str = Query(...),
) -> Any:
    """Render the mapping a reclassification would produce, without writing."""
    try:
        return await library.preview_reclassification(
            world_id, entity_id, target_kind=target_kind,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.post("/library/worlds/{world_id}/lore/{entity_id}/reclassify")
async def commit_reclassify(
    world_id: str,
    entity_id: str,
    payload: ReclassifyCommitPayload,
    library: LibraryDep,
) -> Any:
    try:
        result = await library.reclassify_entity(
            world_id, entity_id,
            target_kind=payload.target_kind,
            overrides=payload.overrides,
            actor=payload.actor,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


@router.get("/library/worlds/{world_id}/reclassifications")
async def list_reclassifications(world_id: str, library: LibraryDep) -> Any:
    try:
        return await library.list_reclassifications(world_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.post("/library/worlds/{world_id}/reclassifications/{timestamp}/undo")
async def undo_reclassify(
    world_id: str,
    timestamp: str,
    library: LibraryDep,
    actor: str = "user",
) -> Any:
    try:
        return await library.undo_reclassification(world_id, timestamp, actor=actor)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
```

Note: `ReclassificationResult` is a `@dataclass`, not a Pydantic model, so `to_payload` (which already handles `__dataclass_fields__`) will serialize it. `EntityKind` values inside it become their string values automatically via `to_payload`.

If the `to_payload` helper does NOT yet handle `StrEnum` values, you may need to coerce `target_kind` to its `.value` before returning:

```python
@router.post(...)
async def commit_reclassify(...) -> Any:
    ...
    payload = to_payload(result)
    if hasattr(payload, "get") and hasattr(payload.get("target_kind"), "value"):
        payload["target_kind"] = payload["target_kind"].value
    return payload
```

Check `to_payload` first:

```bash
grep -n "StrEnum\|Enum\b" backend/src/grimoire/api/util.py
```

If `to_payload` doesn't strip enums, add a one-line coercion in the result dict instead of fighting `to_payload`. The dataclass field is `target_kind: EntityKind`; the test expects `body["target_kind"] == "character"`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest backend/tests/api/test_reclassify_routes.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/api/library.py backend/tests/api/test_reclassify_routes.py
git commit -m "feat(reclassify): add reclassify preview/commit/undo/list REST routes"
```

---

# Branch — Frontend

### Task 7: API client + types

**Files:**
- Modify: `frontend/src/api/library.ts`.

- [ ] **Step 1: Add types + client methods.**

After the existing `EntityKind` type / `LibraryEntity` interface, add:

```ts
export interface ReclassificationSuggestion {
  kind: EntityKind | "lore";
  confidence: number;
  reason: string;
}

export interface ReclassificationPreview {
  source_id: string;
  target_kind: EntityKind;
  frontmatter: Record<string, unknown>;
  body: string;
  kept: string[];
  dropped: string[];
  into_notes: string[];
  warnings: string[];
  required_overrides: string[];
  suggestion: ReclassificationSuggestion;
}

export interface ReclassificationResult {
  source_id: string;
  target_id: string;
  target_kind: EntityKind;
  fields_kept: string[];
  fields_dropped: string[];
  fields_into_notes: string[];
  warnings: string[];
}

export interface ReclassificationAuditRecord {
  ts: string;
  world_id: string;
  source_id: string;
  target_id: string;
  target_kind: EntityKind;
  actor: string;
  overrides: Record<string, unknown>;
}

export interface ReclassificationUndoResult {
  restored_source_id: string;
  deleted_target_id: string;
  undo_of: string;
  warnings: string[];
}
```

Inside the `libraryApi` object, add (after `dependents`):

```ts
  previewReclassify: (worldId: string, sourceId: string, targetKind: EntityKind) =>
    request<ReclassificationPreview>(
      "GET",
      `/library/worlds/${encodeURIComponent(worldId)}/lore/${encodeURIComponent(sourceId)}/reclassify/preview?target_kind=${encodeURIComponent(targetKind)}`,
    ),
  commitReclassify: (
    worldId: string,
    sourceId: string,
    body: { target_kind: EntityKind; overrides?: Record<string, unknown> },
  ) =>
    request<ReclassificationResult>(
      "POST",
      `/library/worlds/${encodeURIComponent(worldId)}/lore/${encodeURIComponent(sourceId)}/reclassify`,
      body,
    ),
  listReclassifications: (worldId: string) =>
    request<ReclassificationAuditRecord[]>(
      "GET",
      `/library/worlds/${encodeURIComponent(worldId)}/reclassifications`,
    ),
  undoReclassify: (worldId: string, ts: string) =>
    request<ReclassificationUndoResult>(
      "POST",
      `/library/worlds/${encodeURIComponent(worldId)}/reclassifications/${encodeURIComponent(ts)}/undo`,
    ),
```

- [ ] **Step 2: Type-check.**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/library.ts
git commit -m "feat(reclassify): add frontend client methods + result types"
```

---

### Task 8: `ConvertModal` component

A modal that previews the mapping, lets the user edit overrides for required fields (e.g. `Location.kind`), and commits.

**Files:**
- Create: `frontend/src/routes/library/ConvertModal.tsx`.
- Create: `frontend/src/routes/library/ConvertModal.test.tsx`.

- [ ] **Step 1: Write the failing tests.**

```tsx
// frontend/src/routes/library/ConvertModal.test.tsx
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ConvertModal } from "./ConvertModal";
import * as libraryModule from "../../api/library";

vi.mock("../../api/library", async () => {
  const actual = await vi.importActual<typeof libraryModule>("../../api/library");
  return {
    ...actual,
    libraryApi: {
      ...actual.libraryApi,
      previewReclassify: vi.fn(),
      commitReclassify: vi.fn(),
    },
  };
});

describe("ConvertModal", () => {
  beforeEach(() => {
    vi.mocked(libraryModule.libraryApi.previewReclassify).mockResolvedValue({
      source_id: "beatrice",
      target_kind: "character",
      frontmatter: { name: "Beatrice", aliases: ["b"] },
      body: "She lived.",
      kept: ["name", "aliases"],
      dropped: ["priority"],
      into_notes: [],
      warnings: ["matching metadata discarded (lore-only fields)"],
      required_overrides: [],
      suggestion: { kind: "character", confidence: 0.85, reason: "pronouns" },
    });
    vi.mocked(libraryModule.libraryApi.commitReclassify).mockResolvedValue({
      source_id: "beatrice",
      target_id: "beatrice",
      target_kind: "character",
      fields_kept: ["name", "aliases"],
      fields_dropped: ["priority"],
      fields_into_notes: [],
      warnings: [],
    });
  });

  it("renders the mapping preview after loading", async () => {
    render(
      <ConvertModal worldId="w" sourceId="beatrice" onClose={() => {}} onConverted={() => {}} />,
    );
    await waitFor(() => screen.getByText(/Beatrice/));
    expect(screen.getByText(/name/)).toBeInTheDocument();
    expect(screen.getByText(/priority/)).toBeInTheDocument(); // surfaced in "Dropped" list
    expect(screen.getByText(/matching metadata/)).toBeInTheDocument();
  });

  it("disables commit until required overrides are filled in", async () => {
    vi.mocked(libraryModule.libraryApi.previewReclassify).mockResolvedValue({
      source_id: "chantry",
      target_kind: "location",
      frontmatter: { name: "The Chantry" },
      body: "",
      kept: ["name"],
      dropped: [],
      into_notes: [],
      warnings: [],
      required_overrides: ["kind"],
      suggestion: { kind: "location", confidence: 0.7, reason: "place noun" },
    });
    render(
      <ConvertModal worldId="w" sourceId="chantry" onClose={() => {}} onConverted={() => {}} />,
    );
    // Switch to Location target.
    await waitFor(() => screen.getByLabelText(/Target kind/i));
    fireEvent.change(screen.getByLabelText(/Target kind/i), { target: { value: "location" } });
    const commit = await screen.findByRole("button", { name: /Convert$/i });
    expect(commit).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/^kind$/i), { target: { value: "building" } });
    await waitFor(() => expect(commit).not.toBeDisabled());
  });

  it("calls commitReclassify with overrides on submit", async () => {
    const onConverted = vi.fn();
    render(
      <ConvertModal worldId="w" sourceId="beatrice" onClose={() => {}} onConverted={onConverted} />,
    );
    await waitFor(() => screen.getByText(/Beatrice/));
    fireEvent.click(screen.getByRole("button", { name: /Convert$/i }));
    await waitFor(() => expect(onConverted).toHaveBeenCalled());
    expect(libraryModule.libraryApi.commitReclassify).toHaveBeenCalledWith(
      "w", "beatrice",
      expect.objectContaining({ target_kind: "character" }),
    );
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd frontend && npx vitest run src/routes/library/ConvertModal.test.tsx
```

Expected: import error on `ConvertModal`.

- [ ] **Step 3: Implement the modal**

```tsx
// frontend/src/routes/library/ConvertModal.tsx
import { useEffect, useMemo, useState } from "react";

import {
  type EntityKind,
  type ReclassificationPreview,
  libraryApi,
  ApiError,
} from "../../api/library";

interface Props {
  worldId: string;
  sourceId: string;
  /** Optional initial target — defaults to the heuristic suggestion. */
  initialTargetKind?: EntityKind;
  onClose: () => void;
  onConverted: (targetKind: EntityKind, targetId: string) => void;
}

const TARGETS: EntityKind[] = ["character", "location", "faction", "item"];

export function ConvertModal({
  worldId,
  sourceId,
  initialTargetKind,
  onClose,
  onConverted,
}: Props) {
  const [targetKind, setTargetKind] = useState<EntityKind>(initialTargetKind ?? "character");
  const [preview, setPreview] = useState<ReclassificationPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    libraryApi
      .previewReclassify(worldId, sourceId, targetKind)
      .then((p) => {
        if (cancelled) return;
        setPreview(p);
        // Default the dropdown to the heuristic suggestion the first time only.
        if (!initialTargetKind && p.suggestion.kind !== "lore" && p.suggestion.kind !== targetKind) {
          setTargetKind(p.suggestion.kind as EntityKind);
        }
        setOverrides({});
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [worldId, sourceId, targetKind]);

  const requiredFilled = useMemo(() => {
    if (!preview) return false;
    return preview.required_overrides.every((k) => (overrides[k] ?? "").trim() !== "");
  }, [preview, overrides]);

  async function submit() {
    if (!preview) return;
    setBusy(true);
    setError(null);
    try {
      const cleaned: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(overrides)) {
        if (v.trim() !== "") cleaned[k] = v.trim();
      }
      const result = await libraryApi.commitReclassify(worldId, sourceId, {
        target_kind: targetKind,
        overrides: cleaned,
      });
      onConverted(targetKind, result.target_id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div role="dialog" aria-label={`Convert ${sourceId}`} className="library-convert-modal">
      <header>
        <h3>Convert {sourceId}</h3>
        <button type="button" onClick={onClose} aria-label="Close">×</button>
      </header>

      <label>
        <span>Target kind</span>
        <select
          value={targetKind}
          onChange={(e) => setTargetKind(e.target.value as EntityKind)}
        >
          {TARGETS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
      </label>

      {loading && <p>Loading preview…</p>}
      {error && <p role="alert">{error}</p>}

      {preview && !loading && (
        <>
          {preview.suggestion.kind !== "lore" && (
            <p className="convert-suggestion">
              Heuristic suggests <strong>{preview.suggestion.kind}</strong>:{" "}
              {preview.suggestion.reason} ({(preview.suggestion.confidence * 100).toFixed(0)}%)
            </p>
          )}

          <section aria-label="Mapping preview">
            <h4>Will write</h4>
            <pre>{JSON.stringify(preview.frontmatter, null, 2)}</pre>
            <h4>Body</h4>
            <pre>{preview.body}</pre>
          </section>

          {preview.required_overrides.length > 0 && (
            <section aria-label="Required fields">
              <h4>Required</h4>
              {preview.required_overrides.map((key) => (
                <label key={key}>
                  <span>{key}</span>
                  <input
                    value={overrides[key] ?? ""}
                    onChange={(e) =>
                      setOverrides((prev) => ({ ...prev, [key]: e.target.value }))
                    }
                    required
                  />
                </label>
              ))}
            </section>
          )}

          {preview.dropped.length > 0 && (
            <section aria-label="Discarded fields">
              <h4>Dropped</h4>
              <ul>
                {preview.dropped.map((k) => (
                  <li key={k}>{k}</li>
                ))}
              </ul>
              {preview.warnings.map((w, i) => (
                <p key={i} className="convert-warning">{w}</p>
              ))}
            </section>
          )}

          <footer>
            <button type="button" onClick={onClose} disabled={busy}>
              Cancel
            </button>
            <button
              type="button"
              onClick={submit}
              disabled={busy || !requiredFilled}
            >
              Convert
            </button>
          </footer>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd frontend && npx vitest run src/routes/library/ConvertModal.test.tsx
```

Expected: all 3 tests PASS.

If `@testing-library/jest-dom` matchers like `toBeDisabled` are not auto-extended, add `import "@testing-library/jest-dom";` at the top of the test file (check whether `frontend/src/setupTests.ts` or `vitest.setup.ts` already does this).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/ConvertModal.tsx frontend/src/routes/library/ConvertModal.test.tsx
git commit -m "feat(reclassify): add ConvertModal with mapping preview + required-field gating"
```

---

### Task 9: `EntityListView` "Convert to…" action

Render a "Convert" button next to each row when the user is browsing the `lore` kind; clicking it opens `ConvertModal`. On success, navigate to the new entity.

**Files:**
- Modify: `frontend/src/routes/library/EntityListView.tsx`.

- [ ] **Step 1: Add the action.**

In the same file, find where each entity row is rendered (look for a `.map(...)` over entities). Wrap or extend with:

```tsx
import { ConvertModal } from "./ConvertModal";
import { ENTITY_KIND_PLURAL } from "../../api/library";
// ... existing imports

// inside EntityListView component, after the existing useState calls:
const [convertingId, setConvertingId] = useState<string | null>(null);

// In the rendered row JSX where `data.map((entity) => ...)` is, add a button on lore rows:
{kindPlural === "lore" && (
  <button
    type="button"
    onClick={(e) => {
      e.preventDefault();
      e.stopPropagation();
      setConvertingId(entity.asset_id);
    }}
  >
    Convert
  </button>
)}

// After the list rendering, add the modal:
{convertingId && (
  <ConvertModal
    worldId={worldId}
    sourceId={convertingId}
    onClose={() => setConvertingId(null)}
    onConverted={(kind, targetId) => {
      setConvertingId(null);
      reload();
      navigate(
        `/library/worlds/${encodeURIComponent(worldId)}/${ENTITY_KIND_PLURAL[kind]}/${encodeURIComponent(targetId)}`,
      );
    }}
  />
)}
```

(The exact JSX-edit location depends on the current `EntityListView.tsx` layout — read the file first, then place the button inside the existing row template and the modal at the component's return-tree root.)

- [ ] **Step 2: Type-check + run frontend tests**

```bash
cd frontend && npx tsc --noEmit && npx vitest run src/routes/library/
```

Expected: no type errors; all existing tests still pass; new modal tests still pass.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/library/EntityListView.tsx
git commit -m "feat(reclassify): wire Convert action into the lore list view"
```

---

# Integration

### Task 10: Full suite + smoke

- [ ] **Step 1: Run the full backend suite.**

```bash
pytest backend/tests -q
```

Expected: all tests PASS. If any unrelated test was relying on `LibraryConfig`'s field count (e.g. serialization snapshot tests), update the snapshot now.

- [ ] **Step 2: Run the full frontend suite.**

```bash
cd frontend && npx vitest run
```

Expected: all tests PASS.

- [ ] **Step 3: Manual smoke** (optional but recommended).

1. Seed a world with a fixture lore file containing a clear character-shaped entry (e.g. `title: Beatrice; body: She walked the moors. Her family disowned her.`).
2. Boot the dev server (`./scripts/dev` or whatever the repo uses; see `run` skill if needed).
3. Navigate to `Library → Worlds → <world> → Lore`, click "Convert" on Beatrice.
4. Confirm: the modal opens, the dropdown defaults to `character`, the mapping preview shows `name: Beatrice` and `aliases: […]`, the "matching metadata discarded" warning appears, and clicking **Convert** removes the lore row and navigates to `/library/worlds/<w>/characters/beatrice`.
5. Open the audit log file at `data/library/imports/reclassifications.jsonl` — verify one JSONL record with `source_snapshot.frontmatter.title == "Beatrice"`.

- [ ] **Step 4: Mark the spec COMPLETED.**

Rename:
```bash
git mv docs/superpowers/specs/2026-05-19-lore-reclassification-design.md docs/superpowers/specs/2026-05-19-lore-reclassification-COMPLETED.md
```

At the top of the renamed file, replace the "Implementation status" block with:

```markdown
> **Status:** SHIPPED 2026-05-19. Sections 1, 2, 3, 5, 6 implemented in `feat(reclassify): …` commits on branch `2026-05-19-lore-reclassification`.
> Section 4 (import-dialog integration) intentionally deferred — the shared transform lives at `backend/src/grimoire/library/reclassify.py:apply_mapping`, ready for `card-imports` Task E2 to import.
```

- [ ] **Step 5: Final commit + PR.**

```bash
git add docs/superpowers/specs/
git commit -m "docs: mark lore-reclassification COMPLETED"
```

PR title: `feat: lore-entry reclassification (Convert to character/location/faction/item)`.

PR body should call out:
- Sections of the spec shipped vs deferred.
- The new audit file `data/library/imports/reclassifications.jsonl`.
- The shared `apply_mapping` transform that card-imports E2 will reuse.

---

## Notes for the engineer executing this plan

- The classifier and transform modules are intentionally **pure** so card-imports E2 can call them without needing a `LibraryService`. Resist the urge to fold them into the service.
- The audit log is **shared across worlds** — one JSONL at `data/library/imports/reclassifications.jsonl`. The `world_id` field in each record is what makes per-world listing/undo work. Do not split per-world; that would break the spec's "outside the campaign directory" intent.
- "Notes" in the spec is implemented as a `## Notes` markdown section appended to the body. This is the simplest path that survives round-tripping (frontmatter dicts can't hold dropped lore metadata since Character/Location/etc. Pydantic models would silently drop unknown keys at read time).
- The `_DROPPED_MATCHING_FIELDS` heuristic in `apply_mapping` treats `priority=100` / `probability=100` as "default" and does not warn on them. This avoids a noisy "matching metadata discarded" on every conversion of a lore entry that never customized priority. If you discover real-world `LoreEntry` defaults differ from 100, adjust the sentinel set in `reclassify.py`.
