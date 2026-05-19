# Transient State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land everything in `docs/superpowers/specs/2026-05-19-transient-state-design.md`. Foundation for `scene-hud`, `narrative-extras`, and `context-inspector`; the privacy model defined here is consumed by all three.

**Architecture:** Sequential branches; each lands independently. **A** (schema + types) is the prerequisite for everything; **B** (service core) blocks **D–H**. Tests verify each branch in isolation; final integration is exercised by **E** (context builder) and **G** (scene-end resets).

- **A** `feature/transient-state-A-schema` — migration 024, types, decay config dataclass.
- **B** `feature/transient-state-B-service` — `TransientStateService` CRUD with supersession, lazy decay, conflict surfacing.
- **C** `feature/transient-state-C-privacy` — `Character.privacy` schema, `resolve()` helper, observer kinds.
- **D** `feature/transient-state-D-extractor` — typed `TransientUpdateProposal` candidate list, routing, review-queue wiring.
- **E** `feature/transient-state-E-context` — spotlight-tier compact stanza, privacy-filtered.
- **F** `feature/transient-state-F-rest` — REST routes + WS plumbing.
- **G** `feature/transient-state-G-triggers` — scene-end + time-skip reset.
- **H** `feature/transient-state-H-promotion` — promote-to-fact contract with Continuity.

**Tech Stack:** Python 3.12, FastAPI, aiosqlite, pytest + pytest-asyncio (`asyncio_mode = "auto"`), Pydantic v2, PowerShell shell. Test runner: `pytest backend/tests/transient_state -v`.

---

## Conventions used in this plan

- **Test runner:** `pytest backend/tests/transient_state -v` (or specific file). Async by default — no `@pytest.mark.asyncio` decorator needed.
- **Lint/format:** `ruff check backend/src/grimoire/transient_state backend/tests/transient_state` and `ruff format <same paths>`. Run both before every commit.
- **Worktree convention (memory):** Worktrees under `.worktrees/` at repo root.
- **Merge convention (memory):** Rebase-merge to main; don't `merge --no-ff`.
- **Commit footer:** `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`
- **Migration:** raw SQL under `backend/src/grimoire/storage/migrations/`. Tests auto-apply via fixtures. Latest as of plan writing: **023**. This plan claims **024**.
- **Test fixture pattern** (model on `backend/tests/world/conftest.py`): a fresh `Database` + `StateStore` + `LibraryService` + ... per test under `tmp_path`.

---

## Branch setup (once, before any task)

- [ ] **Step S1: Create worktrees**

```powershell
git worktree add .worktrees/transient-state-A-schema      -b feature/transient-state-A-schema      main
git worktree add .worktrees/transient-state-B-service     -b feature/transient-state-B-service     main
git worktree add .worktrees/transient-state-C-privacy     -b feature/transient-state-C-privacy     main
git worktree add .worktrees/transient-state-D-extractor   -b feature/transient-state-D-extractor   main
git worktree add .worktrees/transient-state-E-context     -b feature/transient-state-E-context     main
git worktree add .worktrees/transient-state-F-rest        -b feature/transient-state-F-rest        main
git worktree add .worktrees/transient-state-G-triggers    -b feature/transient-state-G-triggers    main
git worktree add .worktrees/transient-state-H-promotion   -b feature/transient-state-H-promotion   main
```

B–H rebase onto A after A merges. C is independent of B; D–H depend on B.

---

# Branch A — Schema + types

**Working directory:** `.worktrees/transient-state-A-schema`
**Why it goes first:** every other branch imports types defined here.

### Task A1: Migration 024

**Files:**
- Create: `backend/src/grimoire/storage/migrations/024_transient_state.sql`

- [ ] **Step 1: Write the SQL**

```sql
-- backend/src/grimoire/storage/migrations/024_transient_state.sql
CREATE TABLE transient_character_state (
    id             INTEGER PRIMARY KEY,
    campaign_id    TEXT    NOT NULL,
    branch_id      TEXT    NOT NULL,
    entity_id      TEXT    NOT NULL,
    field          TEXT    NOT NULL,
    value          TEXT    NOT NULL,
    provenance     TEXT    NOT NULL,
    source_post_id TEXT,
    confidence     REAL    NOT NULL DEFAULT 1.0,
    created_at     TEXT    NOT NULL,
    expires_at     TEXT,
    superseded_by  INTEGER REFERENCES transient_character_state(id),
    in_game_at     TEXT
);

CREATE INDEX ix_tcs_current
    ON transient_character_state(campaign_id, branch_id, entity_id, field)
    WHERE superseded_by IS NULL;

CREATE INDEX ix_tcs_supersedes
    ON transient_character_state(superseded_by)
    WHERE superseded_by IS NOT NULL;


CREATE TABLE transient_location_state (
    id             INTEGER PRIMARY KEY,
    campaign_id    TEXT    NOT NULL,
    branch_id      TEXT    NOT NULL,
    entity_id      TEXT    NOT NULL,
    field          TEXT    NOT NULL,
    value          TEXT    NOT NULL,
    provenance     TEXT    NOT NULL,
    source_post_id TEXT,
    confidence     REAL    NOT NULL DEFAULT 1.0,
    created_at     TEXT    NOT NULL,
    expires_at     TEXT,
    superseded_by  INTEGER REFERENCES transient_location_state(id),
    in_game_at     TEXT
);
CREATE INDEX ix_tls_current ON transient_location_state(campaign_id, branch_id, entity_id, field) WHERE superseded_by IS NULL;
CREATE INDEX ix_tls_supersedes ON transient_location_state(superseded_by) WHERE superseded_by IS NOT NULL;


CREATE TABLE transient_faction_state (
    id             INTEGER PRIMARY KEY,
    campaign_id    TEXT    NOT NULL,
    branch_id      TEXT    NOT NULL,
    entity_id      TEXT    NOT NULL,
    field          TEXT    NOT NULL,
    value          TEXT    NOT NULL,
    provenance     TEXT    NOT NULL,
    source_post_id TEXT,
    confidence     REAL    NOT NULL DEFAULT 1.0,
    created_at     TEXT    NOT NULL,
    expires_at     TEXT,
    superseded_by  INTEGER REFERENCES transient_faction_state(id),
    in_game_at     TEXT
);
CREATE INDEX ix_tfs_current ON transient_faction_state(campaign_id, branch_id, entity_id, field) WHERE superseded_by IS NULL;
CREATE INDEX ix_tfs_supersedes ON transient_faction_state(superseded_by) WHERE superseded_by IS NOT NULL;


CREATE TABLE transient_scene_state (
    id             INTEGER PRIMARY KEY,
    campaign_id    TEXT    NOT NULL,
    branch_id      TEXT    NOT NULL,
    entity_id      TEXT    NOT NULL,           -- scene_id
    field          TEXT    NOT NULL,
    value          TEXT    NOT NULL,
    provenance     TEXT    NOT NULL,
    source_post_id TEXT,
    confidence     REAL    NOT NULL DEFAULT 1.0,
    created_at     TEXT    NOT NULL,
    expires_at     TEXT,
    superseded_by  INTEGER REFERENCES transient_scene_state(id),
    in_game_at     TEXT
);
CREATE INDEX ix_tss_current ON transient_scene_state(campaign_id, branch_id, entity_id, field) WHERE superseded_by IS NULL;
CREATE INDEX ix_tss_supersedes ON transient_scene_state(superseded_by) WHERE superseded_by IS NOT NULL;
```

- [ ] **Step 2: Verify migration applies cleanly**

```powershell
pytest backend/tests/test_storage.py -v -k migrations
```

Expected: PASS (existing test enumerates all migrations and applies them; new file is picked up automatically by the sequence loader).

- [ ] **Step 3: Commit**

```powershell
git add backend/src/grimoire/storage/migrations/024_transient_state.sql
git commit -m @'
feat(transient-state): add migration 024 for four per-entity-kind tables

Adds transient_(character|location|faction|scene)_state tables with
the supersession model. Indexes filter superseded_by IS NULL for
fast current-value reads.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
'@
```

### Task A2: Core types

**Files:**
- Create: `backend/src/grimoire/types/transient.py`
- Modify: `backend/src/grimoire/types/__init__.py` (re-export)
- Test: `backend/tests/types/test_transient.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/types/test_transient.py
"""Transient-state core types (spec §Storage)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from grimoire.types.transient import (
    EntityKind,
    ObserverKind,
    Provenance,
    TransientConflict,
    TransientValue,
)


def test_provenance_enum_values():
    assert Provenance.EXTRACTOR_AUTO.value == "extractor:auto"
    assert Provenance.EXTRACTOR_REVIEWED.value == "extractor:reviewed"
    assert Provenance.USER_HUD.value == "user:hud"
    assert Provenance.USER_EDIT.value == "user:edit"


def test_provenance_mechanics_with_module_id():
    p = Provenance.mechanics("wod")
    assert p.value == "mechanics:wod"
    assert p.module_id == "wod"


def test_entity_kind_enum_complete():
    assert {e.value for e in EntityKind} == {"character", "location", "faction", "scene"}


def test_observer_kind_enum_complete():
    assert {o.value for o in ObserverKind} == {"author", "pc_owner", "other_pc", "audience"}


def test_transient_value_roundtrip():
    now = datetime.now(timezone.utc)
    v = TransientValue(
        id=1,
        entity_id="char_florence",
        field="mood",
        value="guarded",
        provenance=Provenance.EXTRACTOR_AUTO,
        confidence=0.82,
        source_post_id="p_4710",
        created_at=now,
        expires_at=None,
        in_game_at=None,
        decayed=False,
    )
    assert v.entity_id == "char_florence"
    assert v.field == "mood"
    assert v.confidence == 0.82


def test_transient_conflict_carries_both_writes():
    now = datetime.now(timezone.utc)
    user = TransientValue(id=1, entity_id="x", field="mood", value="happy",
                         provenance=Provenance.USER_EDIT, confidence=1.0,
                         source_post_id=None, created_at=now, expires_at=None,
                         in_game_at=None, decayed=False)
    extractor = TransientValue(id=2, entity_id="x", field="mood", value="sad",
                               provenance=Provenance.EXTRACTOR_AUTO, confidence=0.8,
                               source_post_id="p_1", created_at=now, expires_at=None,
                               in_game_at=None, decayed=False)
    conflict = TransientConflict(current=user, losing=extractor)
    assert conflict.current.value == "happy"
    assert conflict.losing.value == "sad"
```

- [ ] **Step 2: Run, expect ImportError**

```powershell
pytest backend/tests/types/test_transient.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'grimoire.types.transient'`.

- [ ] **Step 3: Implement the types**

```python
# backend/src/grimoire/types/transient.py
"""Core types for the transient-state subsystem (spec 2026-05-19).

Mirrors the supersession-row model defined in the design doc. Public
types are TransientValue, TransientUpdateProposal, TransientConflict,
DecayHint, and the small enums (EntityKind, ObserverKind, Provenance).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class EntityKind(StrEnum):
    CHARACTER = "character"
    LOCATION = "location"
    FACTION = "faction"
    SCENE = "scene"


class ObserverKind(StrEnum):
    AUTHOR = "author"          # internal/server reads; no filter
    PC_OWNER = "pc_owner"      # the active PC's player
    OTHER_PC = "other_pc"
    AUDIENCE = "audience"      # read-only viewer; strictest filter


class _ProvenanceMechanics:
    """Carrier type for mechanics:<module> provenance values."""

    __slots__ = ("module_id",)

    def __init__(self, module_id: str) -> None:
        self.module_id = module_id

    @property
    def value(self) -> str:
        return f"mechanics:{self.module_id}"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _ProvenanceMechanics):
            return self.module_id == other.module_id
        if isinstance(other, str):
            return other == self.value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.value)

    def __repr__(self) -> str:
        return f"Provenance.mechanics({self.module_id!r})"


class Provenance(StrEnum):
    EXTRACTOR_AUTO = "extractor:auto"
    EXTRACTOR_REVIEWED = "extractor:reviewed"
    USER_HUD = "user:hud"
    USER_EDIT = "user:edit"

    @classmethod
    def mechanics(cls, module_id: str) -> "_ProvenanceMechanics":
        return _ProvenanceMechanics(module_id)

    @classmethod
    def parse(cls, raw: str) -> "Provenance | _ProvenanceMechanics":
        if raw.startswith("mechanics:"):
            return _ProvenanceMechanics(raw.split(":", 1)[1])
        return cls(raw)


@dataclass(frozen=True, slots=True)
class TransientValue:
    id: int
    entity_id: str
    field: str
    value: Any                          # JSON-serializable
    provenance: Provenance | _ProvenanceMechanics
    confidence: float
    source_post_id: str | None
    created_at: datetime
    expires_at: datetime | None
    in_game_at: datetime | None
    decayed: bool


@dataclass(frozen=True, slots=True)
class TransientUpdateProposal:
    entity_kind: EntityKind
    entity_id: str
    field: str
    value: Any
    confidence: float
    evidence: str                       # post excerpt
    proposed_decay_override: "DecayHint | None" = None


@dataclass(frozen=True, slots=True)
class TransientConflict:
    current: TransientValue
    losing: TransientValue


@dataclass(frozen=True, slots=True)
class DecayHint:
    posts: int | None = None
    in_game_seconds: int | None = None
    scene_scope: bool = False
    reinforce_extends: bool = False
    promote_to_fact: bool = False        # special: "this is settled, promote it"
```

- [ ] **Step 4: Add re-exports**

Modify `backend/src/grimoire/types/__init__.py` — append (don't replace):

```python
from .transient import (
    DecayHint,
    EntityKind as TransientEntityKind,
    ObserverKind,
    Provenance,
    TransientConflict,
    TransientUpdateProposal,
    TransientValue,
)
```

- [ ] **Step 5: Run tests, expect PASS**

```powershell
pytest backend/tests/types/test_transient.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Commit**

```powershell
git add backend/src/grimoire/types/transient.py backend/src/grimoire/types/__init__.py backend/tests/types/test_transient.py
git commit -m @'
feat(transient-state): core types (Provenance, ObserverKind, TransientValue)

Adds the type surface that branches B-H import: EntityKind, ObserverKind,
Provenance (including parametric mechanics:<id>), TransientValue dataclass,
TransientUpdateProposal (typed candidate per Theme E), TransientConflict,
DecayHint.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
'@
```

### Task A3: Decay defaults table + config dataclass

**Files:**
- Create: `backend/src/grimoire/transient_state/__init__.py` (empty package marker)
- Create: `backend/src/grimoire/transient_state/decay.py`
- Create: `backend/src/grimoire/transient_state/config.py`
- Test: `backend/tests/transient_state/__init__.py` (empty), `backend/tests/transient_state/test_decay.py`, `backend/tests/transient_state/test_config.py`

- [ ] **Step 1: Write failing tests for decay defaults**

```python
# backend/tests/transient_state/test_decay.py
"""Default per-field decay table + override merging (spec §Built-in fields)."""

from __future__ import annotations

import pytest

from grimoire.transient_state.decay import (
    DEFAULT_DECAY,
    DecaySpec,
    decay_for,
    merge_overrides,
)
from grimoire.types.transient import EntityKind


def test_character_mood_default():
    spec = decay_for(EntityKind.CHARACTER, "mood")
    assert spec.posts == 10
    assert spec.in_game_seconds == 3600


def test_character_internal_thought_default():
    spec = decay_for(EntityKind.CHARACTER, "internal_thought")
    assert spec.posts == 1


def test_unknown_field_returns_default():
    spec = decay_for(EntityKind.CHARACTER, "unknown_field")
    assert spec == DecaySpec()                    # zero-valued (no decay)


def test_scene_scoped_field():
    spec = decay_for(EntityKind.LOCATION, "ambient_mood")
    assert spec.scene_scope is True


def test_merge_overrides_replaces_field_spec():
    overrides = {
        "character": {
            "mood": {"posts": 20, "in_game_seconds": 7200},
        },
    }
    merged = merge_overrides(overrides)
    spec = merged.get(EntityKind.CHARACTER, {}).get("mood")
    assert spec is not None
    assert spec.posts == 20
    assert spec.in_game_seconds == 7200


def test_merge_overrides_preserves_unmentioned_fields():
    overrides = {"character": {"mood": {"posts": 99}}}
    merged = merge_overrides(overrides)
    assert merged[EntityKind.CHARACTER]["intent"] == DEFAULT_DECAY[EntityKind.CHARACTER]["intent"]
```

- [ ] **Step 2: Run, expect ImportError**

```powershell
pytest backend/tests/transient_state/test_decay.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement decay table**

```python
# backend/src/grimoire/transient_state/decay.py
"""Per-field decay defaults + override merging.

Decay spec values:
  posts            - decay after N posts in the field's entity's scene
  in_game_seconds  - decay after N in-game seconds since last set
  scene_scope      - decay at scene end (resets unless reinforced)
  reinforce_extends - new write extends the previous deadline
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping

from grimoire.types.transient import EntityKind


@dataclass(frozen=True, slots=True)
class DecaySpec:
    posts: int | None = None
    in_game_seconds: int | None = None
    scene_scope: bool = False
    reinforce_extends: bool = False


_ONE_HOUR = 3600
_ONE_DAY = 86_400


DEFAULT_DECAY: dict[EntityKind, dict[str, DecaySpec]] = {
    EntityKind.CHARACTER: {
        "mood":                          DecaySpec(posts=10, in_game_seconds=_ONE_HOUR),
        "intent":                        DecaySpec(posts=5, scene_scope=True),
        "current_action":                DecaySpec(posts=1),
        "posture":                       DecaySpec(posts=3),
        "internal_thought":              DecaySpec(posts=1),
        "focus_of_attention":            DecaySpec(posts=2),
        "relationship_tone_toward_pc":   DecaySpec(scene_scope=True, reinforce_extends=True),
        "energy_level":                  DecaySpec(in_game_seconds=_ONE_DAY),
    },
    EntityKind.LOCATION: {
        "ambient_mood":          DecaySpec(scene_scope=True),
        "noteworthy_detail":     DecaySpec(scene_scope=True),
        "occupancy_summary":     DecaySpec(scene_scope=True),
    },
    EntityKind.FACTION: {
        "alert_level":  DecaySpec(),                    # persists until changed
        "internal_mood": DecaySpec(),
    },
    EntityKind.SCENE: {
        "emotional_temperature": DecaySpec(scene_scope=True),
        "dominant_mood":         DecaySpec(scene_scope=True),
        "pacing":                DecaySpec(scene_scope=True),
    },
}


def decay_for(kind: EntityKind, field_name: str) -> DecaySpec:
    return DEFAULT_DECAY.get(kind, {}).get(field_name, DecaySpec())


def merge_overrides(
    overrides: Mapping[str, Mapping[str, Mapping[str, object]]],
) -> dict[EntityKind, dict[str, DecaySpec]]:
    """Merge a campaign YAML override map onto DEFAULT_DECAY.

    Shape::

        decay:
          character:
            mood: { posts: 20, in_game_hours: 2 }

    Unknown keys in the override dict raise nothing; they're just ignored
    on the DecaySpec build.
    """
    result: dict[EntityKind, dict[str, DecaySpec]] = {
        kind: dict(defaults) for kind, defaults in DEFAULT_DECAY.items()
    }
    for kind_str, fields in (overrides or {}).items():
        try:
            kind = EntityKind(kind_str)
        except ValueError:
            continue
        bucket = result.setdefault(kind, {})
        for field_name, spec_dict in (fields or {}).items():
            bucket[field_name] = _spec_from_dict(spec_dict)
    return result


def _spec_from_dict(d: Mapping[str, object]) -> DecaySpec:
    posts = d.get("posts")
    igs = d.get("in_game_seconds")
    if igs is None and (hrs := d.get("in_game_hours")) is not None:
        igs = int(hrs) * _ONE_HOUR
    return DecaySpec(
        posts=int(posts) if posts is not None else None,
        in_game_seconds=int(igs) if igs is not None else None,
        scene_scope=bool(d.get("scene_scope", False)),
        reinforce_extends=bool(d.get("reinforce_extends", False)),
    )
```

- [ ] **Step 4: Run tests, expect PASS**

```powershell
pytest backend/tests/transient_state/test_decay.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Write failing tests for config**

```python
# backend/tests/transient_state/test_config.py
"""TransientStateConfig - the per-campaign YAML knobs."""

from __future__ import annotations

from pathlib import Path

from grimoire.transient_state.config import TransientStateConfig


def test_defaults_match_spec():
    cfg = TransientStateConfig()
    assert cfg.auto_apply_threshold == 0.85
    assert cfg.review_threshold == 0.60
    assert cfg.promote_to_fact.reinforcement_count == 5
    assert cfg.conflict_window_posts == 10
    assert cfg.vacuum.enabled is True
    assert cfg.vacuum.retain_superseded_days == 30


def test_from_yaml_parses_block(tmp_path: Path):
    path = tmp_path / "transient.yaml"
    path.write_text(
        "auto_apply_threshold: 0.75\n"
        "promote_to_fact:\n"
        "  reinforcement_count: 7\n"
        "conflict_window_posts: 20\n"
        "decay:\n"
        "  character:\n"
        "    mood: { posts: 15 }\n",
        encoding="utf-8",
    )
    cfg = TransientStateConfig.from_yaml(path)
    assert cfg.auto_apply_threshold == 0.75
    assert cfg.promote_to_fact.reinforcement_count == 7
    assert cfg.conflict_window_posts == 20
    # decay overrides applied
    from grimoire.types.transient import EntityKind
    assert cfg.decay_table[EntityKind.CHARACTER]["mood"].posts == 15


def test_from_yaml_missing_file_returns_defaults(tmp_path: Path):
    cfg = TransientStateConfig.from_yaml(tmp_path / "nope.yaml")
    assert cfg == TransientStateConfig()
```

- [ ] **Step 6: Implement config**

```python
# backend/src/grimoire/transient_state/config.py
"""TransientStateConfig — per-campaign overrides (spec §Configuration)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from grimoire.types.transient import EntityKind
from grimoire.transient_state.decay import (
    DEFAULT_DECAY,
    DecaySpec,
    merge_overrides,
)


@dataclass(frozen=True, slots=True)
class PromoteToFactConfig:
    reinforcement_count: int = 5
    require_evidence_diversity: bool = True


@dataclass(frozen=True, slots=True)
class VacuumConfig:
    enabled: bool = True
    retain_superseded_days: int = 30


@dataclass(frozen=True, slots=True)
class TransientStateConfig:
    auto_apply_threshold: float = 0.85
    review_threshold: float = 0.60
    conflict_window_posts: int = 10
    promote_to_fact: PromoteToFactConfig = field(default_factory=PromoteToFactConfig)
    vacuum: VacuumConfig = field(default_factory=VacuumConfig)
    decay_table: dict[EntityKind, dict[str, DecaySpec]] = field(default_factory=lambda: {
        kind: dict(defaults) for kind, defaults in DEFAULT_DECAY.items()
    })

    @classmethod
    def from_yaml(cls, path: Path) -> "TransientStateConfig":
        if not path.exists():
            return cls()
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            return cls()
        promote_raw = raw.get("promote_to_fact") or {}
        vacuum_raw = raw.get("vacuum") or {}
        decay_raw = raw.get("decay") or {}
        return cls(
            auto_apply_threshold=float(raw.get("auto_apply_threshold", 0.85)),
            review_threshold=float(raw.get("review_threshold", 0.60)),
            conflict_window_posts=int(raw.get("conflict_window_posts", 10)),
            promote_to_fact=PromoteToFactConfig(
                reinforcement_count=int(promote_raw.get("reinforcement_count", 5)),
                require_evidence_diversity=bool(promote_raw.get("require_evidence_diversity", True)),
            ),
            vacuum=VacuumConfig(
                enabled=bool(vacuum_raw.get("enabled", True)),
                retain_superseded_days=int(vacuum_raw.get("retain_superseded_days", 30)),
            ),
            decay_table=merge_overrides(decay_raw),
        )
```

- [ ] **Step 7: Run config tests**

```powershell
pytest backend/tests/transient_state/test_config.py -v
```

Expected: 3 passed.

- [ ] **Step 8: Commit branch A**

```powershell
ruff format backend/src/grimoire/transient_state backend/src/grimoire/types/transient.py backend/tests/transient_state backend/tests/types/test_transient.py
ruff check backend/src/grimoire/transient_state backend/src/grimoire/types/transient.py backend/tests/transient_state backend/tests/types/test_transient.py
git add backend/src/grimoire/transient_state backend/tests/transient_state
git commit -m @'
feat(transient-state): decay table + per-campaign config

DEFAULT_DECAY enumerates the per-(EntityKind, field) lifetimes from
the design spec; merge_overrides supports per-campaign YAML overrides.
TransientStateConfig wraps the user-facing knobs (thresholds, vacuum
retention, promote-to-fact reinforcement count).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
'@
```

- [ ] **Step 9: Merge A**

```powershell
git checkout main
git rebase feature/transient-state-A-schema    # fast-forward
```

---

# Branch B — TransientStateService core

**Working directory:** `.worktrees/transient-state-B-service` (rebase onto main after A merges)
**Depends on:** A merged.
**Why it goes second:** the service is the API every other branch uses.

### Task B1: Service skeleton + database access

**Files:**
- Create: `backend/src/grimoire/transient_state/service.py`
- Test: `backend/tests/transient_state/conftest.py` (fixture wiring)

- [ ] **Step 1: Conftest fixture**

```python
# backend/tests/transient_state/conftest.py
"""Fixtures: fresh Database + StateStore + TransientStateService per test."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations
from grimoire.transient_state import TransientStateService
from grimoire.transient_state.config import TransientStateConfig


@pytest.fixture
async def store(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    db = Database(tmp_path / "campaigns.sqlite", pool_size=2)
    await db.connect()
    await apply_migrations(db)
    yield StateStore(db, data_root)
    await db.close()


@pytest.fixture
async def service(store: StateStore) -> TransientStateService:
    return TransientStateService(store, config=TransientStateConfig())


@pytest.fixture
async def seeded_campaign(store: StateStore) -> str:
    """Insert a minimal campaign + branch row so foreign-key-light writes work."""
    await store.upsert_campaign(
        campaign_id="c_test",
        name="Test campaign",
        setting_id="s_test",
    )
    return "c_test"
```

- [ ] **Step 2: Failing service tests**

```python
# backend/tests/transient_state/test_service.py
"""TransientStateService CRUD with supersession + lazy decay."""

from __future__ import annotations

import pytest

from grimoire.transient_state import TransientStateService
from grimoire.types.transient import EntityKind, Provenance


async def test_set_then_get_returns_value(service: TransientStateService, seeded_campaign: str):
    await service.set(
        campaign_id=seeded_campaign,
        entity_kind=EntityKind.CHARACTER,
        entity_id="char_florence",
        field="mood",
        value="guarded",
        provenance=Provenance.USER_EDIT,
    )
    v = await service.get(
        campaign_id=seeded_campaign,
        entity_kind=EntityKind.CHARACTER,
        entity_id="char_florence",
        field="mood",
    )
    assert v is not None
    assert v.value == "guarded"
    assert v.provenance == Provenance.USER_EDIT


async def test_get_with_no_field_returns_bundle(service: TransientStateService, seeded_campaign: str):
    await service.set(seeded_campaign, EntityKind.CHARACTER, "char_florence",
                      "mood", "guarded", provenance=Provenance.USER_EDIT)
    await service.set(seeded_campaign, EntityKind.CHARACTER, "char_florence",
                      "intent", "hide letter", provenance=Provenance.USER_EDIT)
    bundle = await service.get(seeded_campaign, EntityKind.CHARACTER, "char_florence")
    assert isinstance(bundle, dict)
    assert bundle["mood"].value == "guarded"
    assert bundle["intent"].value == "hide letter"


async def test_set_supersedes_when_priority_outranks(service, seeded_campaign):
    # extractor:auto loses to user:edit
    await service.set(seeded_campaign, EntityKind.CHARACTER, "x", "mood", "sad",
                      provenance=Provenance.EXTRACTOR_AUTO, confidence=0.7,
                      source_post_id="p_1")
    await service.set(seeded_campaign, EntityKind.CHARACTER, "x", "mood", "happy",
                      provenance=Provenance.USER_EDIT)
    current = await service.get(seeded_campaign, EntityKind.CHARACTER, "x", "mood")
    assert current.value == "happy"
    assert current.provenance == Provenance.USER_EDIT


async def test_set_losing_write_preserved_as_history(service, seeded_campaign):
    # user:edit then extractor:auto — extractor loses
    await service.set(seeded_campaign, EntityKind.CHARACTER, "x", "mood", "happy",
                      provenance=Provenance.USER_EDIT)
    await service.set(seeded_campaign, EntityKind.CHARACTER, "x", "mood", "sad",
                      provenance=Provenance.EXTRACTOR_AUTO, confidence=0.9,
                      source_post_id="p_1")
    current = await service.get(seeded_campaign, EntityKind.CHARACTER, "x", "mood")
    assert current.value == "happy"
    history = await service.history(seeded_campaign, EntityKind.CHARACTER, "x", "mood")
    assert {h.value for h in history} == {"happy", "sad"}


async def test_clear_supersedes_to_null(service, seeded_campaign):
    await service.set(seeded_campaign, EntityKind.CHARACTER, "x", "mood", "happy",
                      provenance=Provenance.USER_EDIT)
    await service.clear(seeded_campaign, EntityKind.CHARACTER, "x", field="mood")
    assert await service.get(seeded_campaign, EntityKind.CHARACTER, "x", "mood") is None


async def test_clear_all_fields(service, seeded_campaign):
    await service.set(seeded_campaign, EntityKind.CHARACTER, "x", "mood", "a",
                      provenance=Provenance.USER_EDIT)
    await service.set(seeded_campaign, EntityKind.CHARACTER, "x", "intent", "b",
                      provenance=Provenance.USER_EDIT)
    await service.clear(seeded_campaign, EntityKind.CHARACTER, "x")
    bundle = await service.get(seeded_campaign, EntityKind.CHARACTER, "x")
    assert bundle == {}


async def test_get_bulk_returns_keyed_dict(service, seeded_campaign):
    await service.set(seeded_campaign, EntityKind.CHARACTER, "a", "mood", "happy",
                      provenance=Provenance.USER_EDIT)
    await service.set(seeded_campaign, EntityKind.CHARACTER, "b", "mood", "sad",
                      provenance=Provenance.USER_EDIT)
    bulk = await service.get_bulk(
        seeded_campaign, EntityKind.CHARACTER, ["a", "b", "missing"], fields=["mood"]
    )
    assert bulk["a"]["mood"].value == "happy"
    assert bulk["b"]["mood"].value == "sad"
    assert bulk["missing"] == {}
```

- [ ] **Step 3: Implement the service**

```python
# backend/src/grimoire/transient_state/__init__.py
from grimoire.transient_state.service import TransientStateService

__all__ = ["TransientStateService"]
```

```python
# backend/src/grimoire/transient_state/service.py
"""TransientStateService — per-field ephemeral state with supersession.

Spec: docs/superpowers/specs/2026-05-19-transient-state-design.md

Routing rules:
 - Write priority: user > mechanics > extractor.
 - Losing write is preserved via superseded_by (insert + leave prior current).
 - Reads filter on superseded_by IS NULL and (expires_at IS NULL OR expires_at > now()).
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from typing import Sequence

from grimoire.state_store import StateStore
from grimoire.transient_state.config import TransientStateConfig
from grimoire.types.transient import (
    EntityKind,
    ObserverKind,
    Provenance,
    TransientConflict,
    TransientValue,
    _ProvenanceMechanics,
)


_TABLE = {
    EntityKind.CHARACTER: "transient_character_state",
    EntityKind.LOCATION:  "transient_location_state",
    EntityKind.FACTION:   "transient_faction_state",
    EntityKind.SCENE:     "transient_scene_state",
}


_PRIORITY = {
    "user:edit":           3,
    "user:hud":            3,
    "extractor:reviewed":  2,
    "extractor:auto":      1,
}


def _priority_for(provenance: Provenance | _ProvenanceMechanics) -> int:
    raw = provenance.value if hasattr(provenance, "value") else str(provenance)
    if raw.startswith("user:"):
        return 3
    if raw.startswith("mechanics:"):
        return 2
    if raw == "extractor:reviewed":
        return 2
    if raw == "extractor:auto":
        return 1
    return 0


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TransientStateService:
    def __init__(self, store: StateStore, *, config: TransientStateConfig | None = None) -> None:
        self.store = store
        self.config = config or TransientStateConfig()

    async def get(
        self,
        campaign_id: str,
        entity_kind: EntityKind,
        entity_id: str,
        field: str | None = None,
        *,
        branch_id: str | None = None,
        for_observer: ObserverKind | None = None,
    ):
        branch = branch_id or f"{campaign_id}:main"
        table = _TABLE[entity_kind]
        async with self.store.db.connect_read() as conn:
            if field is None:
                rows = await conn.execute_fetchall(
                    f"SELECT * FROM {table} "
                    f"WHERE campaign_id=? AND branch_id=? AND entity_id=? "
                    f"AND superseded_by IS NULL "
                    f"AND (expires_at IS NULL OR expires_at > ?) ",
                    (campaign_id, branch, entity_id, _now().isoformat()),
                )
                return {r["field"]: self._row_to_value(r) for r in rows}
            row = await conn.execute_fetchone(
                f"SELECT * FROM {table} "
                f"WHERE campaign_id=? AND branch_id=? AND entity_id=? AND field=? "
                f"AND superseded_by IS NULL "
                f"AND (expires_at IS NULL OR expires_at > ?) ",
                (campaign_id, branch, entity_id, field, _now().isoformat()),
            )
            return self._row_to_value(row) if row else None

    async def get_bulk(
        self,
        campaign_id: str,
        entity_kind: EntityKind,
        entity_ids: Sequence[str],
        fields: Sequence[str] | None = None,
        *,
        branch_id: str | None = None,
        for_observer: ObserverKind | None = None,
    ) -> dict[str, dict[str, TransientValue]]:
        if not entity_ids:
            return {}
        branch = branch_id or f"{campaign_id}:main"
        table = _TABLE[entity_kind]
        placeholders = ",".join("?" * len(entity_ids))
        sql = (
            f"SELECT * FROM {table} "
            f"WHERE campaign_id=? AND branch_id=? AND entity_id IN ({placeholders}) "
            f"AND superseded_by IS NULL "
            f"AND (expires_at IS NULL OR expires_at > ?) "
        )
        params: list[object] = [campaign_id, branch, *entity_ids, _now().isoformat()]
        if fields:
            field_placeholders = ",".join("?" * len(fields))
            sql += f"AND field IN ({field_placeholders}) "
            params.extend(fields)
        async with self.store.db.connect_read() as conn:
            rows = await conn.execute_fetchall(sql, tuple(params))
        result: dict[str, dict[str, TransientValue]] = {eid: {} for eid in entity_ids}
        for r in rows:
            result.setdefault(r["entity_id"], {})[r["field"]] = self._row_to_value(r)
        return result

    async def set(
        self,
        campaign_id: str,
        entity_kind: EntityKind,
        entity_id: str,
        field: str,
        value,
        *,
        provenance: Provenance | _ProvenanceMechanics,
        confidence: float = 1.0,
        source_post_id: str | None = None,
        branch_id: str | None = None,
        in_game_at: datetime | None = None,
    ) -> TransientValue:
        branch = branch_id or f"{campaign_id}:main"
        table = _TABLE[entity_kind]
        provenance_str = provenance.value if hasattr(provenance, "value") else str(provenance)
        async with self.store.db.connect_write() as conn:
            async with conn.transaction():
                current = await conn.execute_fetchone(
                    f"SELECT * FROM {table} "
                    f"WHERE campaign_id=? AND branch_id=? AND entity_id=? AND field=? "
                    f"AND superseded_by IS NULL",
                    (campaign_id, branch, entity_id, field),
                )
                new_id_row = await conn.execute_fetchone(
                    f"INSERT INTO {table} "
                    f"(campaign_id, branch_id, entity_id, field, value, provenance, "
                    f" source_post_id, confidence, created_at, expires_at, "
                    f" superseded_by, in_game_at) "
                    f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?) "
                    f"RETURNING id",
                    (
                        campaign_id, branch, entity_id, field,
                        json.dumps(value),
                        provenance_str,
                        source_post_id,
                        confidence,
                        _now().isoformat(),
                        # superseded_by inserted later based on priority
                        None,
                        (in_game_at or _now()).isoformat(),
                    ),
                )
                new_id = new_id_row["id"]
                if current is None:
                    return await self._fetch_row(conn, table, new_id)
                if _priority_for(provenance) >= _priority_for(Provenance(current["provenance"])
                                                              if not current["provenance"].startswith("mechanics:")
                                                              else _ProvenanceMechanics(current["provenance"].split(":",1)[1])):
                    # Incoming wins: mark prior current as superseded by new
                    await conn.execute(
                        f"UPDATE {table} SET superseded_by=? WHERE id=?",
                        (new_id, current["id"]),
                    )
                else:
                    # Incoming loses: insert the new row as already superseded by current
                    await conn.execute(
                        f"UPDATE {table} SET superseded_by=? WHERE id=?",
                        (current["id"], new_id),
                    )
                return await self._fetch_row(conn, table, new_id)

    async def clear(
        self,
        campaign_id: str,
        entity_kind: EntityKind,
        entity_id: str,
        field: str | None = None,
        *,
        branch_id: str | None = None,
        reason: str = "user:reset",
    ) -> None:
        branch = branch_id or f"{campaign_id}:main"
        table = _TABLE[entity_kind]
        async with self.store.db.connect_write() as conn:
            if field is None:
                await conn.execute(
                    f"UPDATE {table} SET expires_at=? "
                    f"WHERE campaign_id=? AND branch_id=? AND entity_id=? "
                    f"AND superseded_by IS NULL",
                    (_now().isoformat(), campaign_id, branch, entity_id),
                )
            else:
                await conn.execute(
                    f"UPDATE {table} SET expires_at=? "
                    f"WHERE campaign_id=? AND branch_id=? AND entity_id=? AND field=? "
                    f"AND superseded_by IS NULL",
                    (_now().isoformat(), campaign_id, branch, entity_id, field),
                )

    async def history(
        self,
        campaign_id: str,
        entity_kind: EntityKind,
        entity_id: str,
        field: str,
        limit: int = 20,
        *,
        branch_id: str | None = None,
    ) -> list[TransientValue]:
        branch = branch_id or f"{campaign_id}:main"
        table = _TABLE[entity_kind]
        async with self.store.db.connect_read() as conn:
            rows = await conn.execute_fetchall(
                f"SELECT * FROM {table} "
                f"WHERE campaign_id=? AND branch_id=? AND entity_id=? AND field=? "
                f"ORDER BY id DESC LIMIT ?",
                (campaign_id, branch, entity_id, field, limit),
            )
        return [self._row_to_value(r) for r in rows]

    async def list_conflicts(
        self,
        campaign_id: str,
        *,
        branch_id: str | None = None,
        within_posts: int | None = None,
    ) -> list[TransientConflict]:
        branch = branch_id or f"{campaign_id}:main"
        results: list[TransientConflict] = []
        # Pull losing extractor rows whose superseded_by points at a user:* current
        for kind, table in _TABLE.items():
            async with self.store.db.connect_read() as conn:
                rows = await conn.execute_fetchall(
                    f"SELECT loser.*, "
                    f"  winner.id AS w_id, winner.provenance AS w_prov, winner.value AS w_value, "
                    f"  winner.created_at AS w_created_at "
                    f"FROM {table} loser "
                    f"JOIN {table} winner ON loser.superseded_by = winner.id "
                    f"WHERE loser.campaign_id=? AND loser.branch_id=? "
                    f"AND loser.provenance LIKE 'extractor:%' "
                    f"AND winner.provenance LIKE 'user:%' ",
                    (campaign_id, branch),
                )
            for r in rows:
                results.append(
                    TransientConflict(
                        current=self._row_to_value({
                            "id": r["w_id"], "entity_id": r["entity_id"], "field": r["field"],
                            "value": r["w_value"], "provenance": r["w_prov"],
                            "source_post_id": None, "confidence": 1.0,
                            "created_at": r["w_created_at"], "expires_at": None,
                            "in_game_at": None,
                        }),
                        losing=self._row_to_value(r),
                    ),
                )
        return results

    @staticmethod
    async def _fetch_row(conn, table: str, row_id: int) -> TransientValue:
        r = await conn.execute_fetchone(f"SELECT * FROM {table} WHERE id=?", (row_id,))
        return TransientStateService._row_to_value(r)

    @staticmethod
    def _row_to_value(r) -> TransientValue:
        prov_raw = r["provenance"]
        if prov_raw.startswith("mechanics:"):
            prov: object = _ProvenanceMechanics(prov_raw.split(":", 1)[1])
        else:
            prov = Provenance(prov_raw)
        return TransientValue(
            id=r["id"],
            entity_id=r["entity_id"],
            field=r["field"],
            value=json.loads(r["value"]),
            provenance=prov,
            confidence=float(r["confidence"]),
            source_post_id=r["source_post_id"],
            created_at=datetime.fromisoformat(r["created_at"]),
            expires_at=datetime.fromisoformat(r["expires_at"]) if r["expires_at"] else None,
            in_game_at=datetime.fromisoformat(r["in_game_at"]) if r["in_game_at"] else None,
            decayed=False,
        )
```

- [ ] **Step 4: Run service tests**

```powershell
pytest backend/tests/transient_state/test_service.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit B1**

```powershell
ruff format backend/src/grimoire/transient_state backend/tests/transient_state
ruff check  backend/src/grimoire/transient_state backend/tests/transient_state
git add backend/src/grimoire/transient_state backend/tests/transient_state
git commit -m @'
feat(transient-state): TransientStateService CRUD + supersession

set/get/clear/history with supersession-based history preservation;
write priority (user > mechanics > extractor:reviewed > extractor:auto);
get_bulk for HUD aggregation; list_conflicts for the user-vs-extractor
surface.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
'@
```

### Task B2: Lazy decay on read

**Files:**
- Modify: `backend/src/grimoire/transient_state/service.py`
- Test: `backend/tests/transient_state/test_decay_on_read.py`

- [ ] **Step 1: Write failing test for posts-based decay**

```python
# backend/tests/transient_state/test_decay_on_read.py
"""Lazy decay: get() returns None when posts elapsed since write."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from grimoire.transient_state import TransientStateService
from grimoire.transient_state.config import TransientStateConfig
from grimoire.transient_state.decay import DecaySpec
from grimoire.types.transient import EntityKind, Provenance


async def test_current_action_expires_after_1_post(service: TransientStateService, seeded_campaign, store):
    # current_action default decay: posts=1
    await service.set(seeded_campaign, EntityKind.CHARACTER, "x", "current_action",
                      "pouring tea", provenance=Provenance.EXTRACTOR_AUTO,
                      source_post_id="p_1")
    # before next post, value visible
    assert (await service.get(seeded_campaign, EntityKind.CHARACTER, "x", "current_action")).value == "pouring tea"
    # advance 2 posts via scene_manager helper (use store directly for test)
    await store.advance_post_counter(seeded_campaign, by=2)
    assert await service.get(seeded_campaign, EntityKind.CHARACTER, "x", "current_action") is None


async def test_in_game_seconds_decay(service, seeded_campaign):
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    await service.set(seeded_campaign, EntityKind.CHARACTER, "x", "mood", "guarded",
                      provenance=Provenance.EXTRACTOR_AUTO, in_game_at=past,
                      source_post_id="p_1")
    # mood decay: in_game_seconds=3600 (1h) — 2h elapsed → decayed
    assert await service.get(seeded_campaign, EntityKind.CHARACTER, "x", "mood") is None


async def test_no_decay_for_field_without_spec(service, seeded_campaign):
    await service.set(seeded_campaign, EntityKind.FACTION, "f_camarilla", "alert_level",
                      "raised", provenance=Provenance.USER_EDIT)
    # alert_level has DecaySpec() (no decay)
    v = await service.get(seeded_campaign, EntityKind.FACTION, "f_camarilla", "alert_level")
    assert v is not None
```

- [ ] **Step 2: Run test, expect FAIL** (service doesn't apply decay yet)

```powershell
pytest backend/tests/transient_state/test_decay_on_read.py -v
```

Expected: FAIL — values returned even after posts elapsed.

- [ ] **Step 3: Implement decay filter in `get`**

Add helpers to `service.py`:

```python
# backend/src/grimoire/transient_state/service.py — add after existing imports
from grimoire.transient_state.decay import DecaySpec, decay_for


# add a private helper on the service class
def _is_decayed(self, row, *, post_counter: int, in_game_now: datetime) -> bool:
    spec = self.config.decay_table.get(
        EntityKind(_KIND_FROM_TABLE[row.get("__table__", "")]),
        {},
    ).get(row["field"], DecaySpec())
    if spec.posts is not None and row["source_post_id"]:
        # Find how many posts have elapsed since source_post_id; service uses
        # store helper to query post counter.
        delta_posts = post_counter - int(row.get("source_post_counter", 0))
        if delta_posts >= spec.posts:
            return True
    if spec.in_game_seconds is not None and row["in_game_at"]:
        in_game_at = datetime.fromisoformat(row["in_game_at"])
        if (in_game_now - in_game_at).total_seconds() >= spec.in_game_seconds:
            return True
    return False
```

Refactor the `get` and `get_bulk` reads to first fetch raw rows (no decay filter), then apply `_is_decayed` in Python. (Alternative would be SQL-side filtering, but post-counter math is awkward in SQL; Python is simpler and the row counts per query are small.)

Update `_row_to_value` to optionally mark `decayed=True` rather than just filtering.

- [ ] **Step 4: Wire post-counter lookup through StateStore**

The store needs a helper `current_post_counter(campaign_id, branch_id) -> int`. Likely already exists or one is trivial — add to `state_store/store.py` if missing:

```python
async def current_post_counter(self, campaign_id: str, branch_id: str) -> int:
    async with self.db.connect_read() as conn:
        row = await conn.execute_fetchone(
            "SELECT MAX(turn_no) AS n FROM posts WHERE campaign_id=? AND branch_id=?",
            (campaign_id, branch_id),
        )
    return int(row["n"] or 0)
```

Add `source_post_counter` capture at `set` time so reads don't have to query posts for every row:

```sql
ALTER TABLE transient_character_state ADD COLUMN source_post_counter INTEGER;
-- repeat for other 3 tables
```

This is a follow-up migration **025_transient_post_counter.sql**.

- [ ] **Step 5: Migration 025**

```sql
-- backend/src/grimoire/storage/migrations/025_transient_post_counter.sql
ALTER TABLE transient_character_state ADD COLUMN source_post_counter INTEGER;
ALTER TABLE transient_location_state  ADD COLUMN source_post_counter INTEGER;
ALTER TABLE transient_faction_state   ADD COLUMN source_post_counter INTEGER;
ALTER TABLE transient_scene_state     ADD COLUMN source_post_counter INTEGER;
```

Update `service.set` to capture the counter via the new store helper at write time.

- [ ] **Step 6: Run decay tests, expect PASS**

```powershell
pytest backend/tests/transient_state/test_decay_on_read.py -v
```

Expected: 3 passed.

- [ ] **Step 7: Commit B2**

```powershell
git add backend/src/grimoire/transient_state/service.py \
        backend/src/grimoire/state_store/store.py \
        backend/src/grimoire/storage/migrations/025_transient_post_counter.sql \
        backend/tests/transient_state/test_decay_on_read.py
git commit -m @'
feat(transient-state): lazy decay on read

Captures source_post_counter at write time (migration 025); applies
posts-based and in_game_seconds-based decay filters in get/get_bulk.
Reads honor the per-(EntityKind, field) DecaySpec from config.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
'@
```

### Task B3: Privacy filter integration point

For now, `for_observer` is accepted but no-op. The actual filter lives in branch C. Just thread the parameter through:

- [ ] **Step 1: Test that observer param doesn't crash**

```python
# extend test_service.py
async def test_get_accepts_observer_kind(service, seeded_campaign):
    await service.set(seeded_campaign, EntityKind.CHARACTER, "x", "mood", "happy",
                      provenance=Provenance.USER_EDIT)
    v = await service.get(seeded_campaign, EntityKind.CHARACTER, "x", "mood",
                          for_observer=ObserverKind.AUDIENCE)
    # Without privacy frontmatter, all fields surface for any observer
    assert v.value == "happy"
```

- [ ] **Step 2: Run, expect PASS** (param is currently accepted no-op).

- [ ] **Step 3: Commit B3** — single test addition.

### Task B4: Merge B to main

- [ ] **Step 1: Rebase + ff-merge**

```powershell
git checkout main
git rebase feature/transient-state-B-service
```

---

# Branch C — Privacy schema + helper

**Working directory:** `.worktrees/transient-state-C-privacy`
**Why standalone:** transient-state owns the privacy model; HUD, extras, context-inspector consume it.

### Task C1: Schema additions

**Files:**
- Modify: `backend/src/grimoire/types/characters.py:Character` — add `privacy` field.
- Create: `backend/src/grimoire/transient_state/privacy.py`
- Test: `backend/tests/transient_state/test_privacy.py`

- [ ] **Step 1: Write failing privacy tests**

```python
# backend/tests/transient_state/test_privacy.py
"""Privacy resolution: per-character frontmatter + campaign presets + POV mode."""

from __future__ import annotations

from grimoire.transient_state.privacy import (
    PrivacyView,
    resolve_privacy,
)
from grimoire.types.characters import (
    Character,
    CharacterPrivacy,
    InternalThoughtsPrivacy,
)
from grimoire.types.transient import ObserverKind


def _make_character(**overrides):
    return Character(
        id="winifred",
        name="winifred",
        role="npc",
        privacy=CharacterPrivacy(internal_thoughts=InternalThoughtsPrivacy(**overrides)),
    )


def test_defaults_all_true_for_author():
    c = _make_character()
    view = resolve_privacy(c, observer=ObserverKind.AUTHOR)
    assert view == PrivacyView(hud=True, inline=True, context=True)


def test_audience_observer_with_default_privacy_sees_all():
    # Default frontmatter is all-true; AUDIENCE observer sees what the character flags
    c = _make_character()
    view = resolve_privacy(c, observer=ObserverKind.AUDIENCE)
    assert view == PrivacyView(hud=True, inline=True, context=True)


def test_audience_blocked_when_character_marks_private():
    c = _make_character(surface_in_hud=False, surface_inline=False, surface_in_context=False)
    view = resolve_privacy(c, observer=ObserverKind.AUDIENCE)
    assert view == PrivacyView(hud=False, inline=False, context=False)


def test_pc_owner_always_sees_own_thoughts():
    c = _make_character(surface_in_hud=False, surface_inline=False, surface_in_context=False)
    view = resolve_privacy(c, observer=ObserverKind.PC_OWNER, is_self=True)
    assert view == PrivacyView(hud=True, inline=True, context=True)


def test_pov_mode_strips_npc_thoughts():
    c = _make_character()
    view = resolve_privacy(c, observer=ObserverKind.AUDIENCE, pov_mode=True, is_pc=False)
    assert view == PrivacyView(hud=False, inline=False, context=False)


def test_pov_mode_does_not_strip_pc_thoughts():
    c = _make_character()
    view = resolve_privacy(c, observer=ObserverKind.PC_OWNER, pov_mode=True, is_pc=True, is_self=True)
    assert view == PrivacyView(hud=True, inline=True, context=True)
```

- [ ] **Step 2: Run, expect ImportError**

```powershell
pytest backend/tests/transient_state/test_privacy.py -v
```

- [ ] **Step 3: Implement Pydantic schema**

Modify `backend/src/grimoire/types/characters.py` — add (don't touch existing fields):

```python
class InternalThoughtsPrivacy(BaseModel):
    surface_in_hud: bool = True
    surface_inline: bool = True
    surface_in_context: bool = True


class CharacterPrivacy(BaseModel):
    internal_thoughts: InternalThoughtsPrivacy = Field(default_factory=InternalThoughtsPrivacy)


class Character(BaseModel):
    # ... existing fields ...
    privacy: CharacterPrivacy = Field(default_factory=CharacterPrivacy)
```

- [ ] **Step 4: Implement resolve()**

```python
# backend/src/grimoire/transient_state/privacy.py
"""Privacy resolution helper.

Decision: HUD / extras / context-inspector all read through this function
so the privacy boundary is enforced at the data layer.
"""

from __future__ import annotations

from dataclasses import dataclass

from grimoire.types.characters import Character
from grimoire.types.transient import ObserverKind


@dataclass(frozen=True, slots=True)
class PrivacyView:
    hud: bool
    inline: bool
    context: bool


def resolve_privacy(
    character: Character,
    *,
    observer: ObserverKind,
    is_self: bool = False,
    is_pc: bool = False,
    pov_mode: bool = False,
) -> PrivacyView:
    if observer == ObserverKind.AUTHOR:
        return PrivacyView(hud=True, inline=True, context=True)
    if observer == ObserverKind.PC_OWNER and is_self:
        return PrivacyView(hud=True, inline=True, context=True)
    if pov_mode and not is_pc:
        return PrivacyView(hud=False, inline=False, context=False)
    p = character.privacy.internal_thoughts
    return PrivacyView(
        hud=p.surface_in_hud,
        inline=p.surface_inline,
        context=p.surface_in_context,
    )


def resolve_extras_visibility(
    character: Character,
    extras_key: str,
    *,
    observer: ObserverKind,
    is_self: bool = False,
) -> bool:
    """Default: all extras visible. Future: per-key privacy in CharacterPrivacy."""
    if observer in (ObserverKind.AUTHOR, ObserverKind.PC_OWNER) and is_self:
        return True
    return True
```

- [ ] **Step 5: Run privacy tests, expect PASS**

```powershell
pytest backend/tests/transient_state/test_privacy.py -v
```

Expected: 6 passed.

- [ ] **Step 6: Commit + merge C**

```powershell
ruff format backend/src/grimoire/types/characters.py backend/src/grimoire/transient_state/privacy.py backend/tests/transient_state/test_privacy.py
git add -A
git commit -m @'
feat(transient-state): per-character privacy schema + resolve helper

Adds Character.privacy.internal_thoughts.{surface_in_hud,
surface_inline, surface_in_context} to the frontmatter schema (defaults
all-true). resolve_privacy(character, observer, ...) is the helper that
HUD/extras/context-inspector use to get the effective visibility.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
'@

git checkout main
git rebase feature/transient-state-C-privacy
```

### Task C2: Wire the helper into TransientStateService.get filter

- [ ] **Step 1: Add integration test in the service**

```python
# extend backend/tests/transient_state/test_service.py
async def test_get_filters_internal_thought_for_audience(
    service, seeded_campaign, library_with_private_character,
):
    # library_with_private_character: a fixture that creates a Character
    # with internal_thoughts.surface_in_context=False
    char_id = "char_private"
    await service.set(seeded_campaign, EntityKind.CHARACTER, char_id,
                      "internal_thought", "secret plan",
                      provenance=Provenance.EXTRACTOR_AUTO)
    v = await service.get(seeded_campaign, EntityKind.CHARACTER, char_id,
                          "internal_thought", for_observer=ObserverKind.AUDIENCE)
    assert v is None    # filtered

    # but mood survives
    await service.set(seeded_campaign, EntityKind.CHARACTER, char_id,
                      "mood", "guarded",
                      provenance=Provenance.EXTRACTOR_AUTO)
    v = await service.get(seeded_campaign, EntityKind.CHARACTER, char_id,
                          "mood", for_observer=ObserverKind.AUDIENCE)
    assert v is not None
```

- [ ] **Step 2: Implement library-aware filter** — modify `TransientStateService` to accept a `library: LibraryService` arg in its constructor for character lookup; apply `resolve_privacy` to filter `internal_thought` (and other gated fields) from the response when `for_observer` is set and the resolved view's `context` flag is False (for context-builder reads; HUD reads use `view.hud`).

This requires a small expansion of the `_PRIVATE_FIELDS_BY_KIND` map:

```python
_PRIVATE_FIELDS_BY_KIND = {
    EntityKind.CHARACTER: ("internal_thought",),
}
```

In `get`/`get_bulk`, after loading rows, drop those whose field is in `_PRIVATE_FIELDS_BY_KIND[kind]` if `resolve_privacy(...).context` is False.

- [ ] **Step 3: Test PASS + commit**

---

# Branch D — Extractor integration (TransientUpdateProposal)

**Working directory:** `.worktrees/transient-state-D-extractor`
**Depends on:** A, B merged.

### Task D1: ExtractionResult adds typed candidate list

**Files:**
- Modify: `backend/src/grimoire/types/extraction.py:ExtractionResult` — add `transient_updates: list[TransientUpdateProposal]`.
- Test: `backend/tests/extractor/test_transient_routing.py` (new).

- [ ] **Step 1: Failing test**

```python
async def test_high_confidence_proposal_auto_applied(
    extractor_service, transient_service, seeded_campaign,
):
    proposal = TransientUpdateProposal(
        entity_kind=EntityKind.CHARACTER, entity_id="char_x",
        field="mood", value="guarded", confidence=0.92,
        evidence="She tensed at the question.",
    )
    result = ExtractionResult(transient_updates=[proposal])
    await extractor_service.route_transient_updates(seeded_campaign, result, turn_id="t_1")
    v = await transient_service.get(seeded_campaign, EntityKind.CHARACTER, "char_x", "mood")
    assert v.value == "guarded"
    assert v.provenance == Provenance.EXTRACTOR_AUTO


async def test_medium_confidence_enqueued_for_review(...):
    # similar but confidence=0.65 — assert review_queue row exists


async def test_low_confidence_discarded(...):
    # confidence=0.4 — assert neither set nor review_queue write
```

- [ ] **Step 2-N**: extend `ExtractionResult` (Pydantic); implement `ExtractorService.route_transient_updates`; wire into the existing post-extraction routing path in `extractor/service.py` after merge.

- [ ] **Step N+1: Tests PASS, commit, merge D.**

---

# Branch E — Context Builder spotlight stanza

**Working directory:** `.worktrees/transient-state-E-context`
**Depends on:** A, B, C merged.

### Task E1: TransientStanza tier item

**Files:**
- Modify: `backend/src/grimoire/context/builder.py:_resolve_cast` — emit a new `_TierItem` per present character.
- Test: `backend/tests/context/test_transient_stanza.py` (new).

- [ ] **Step 1: Failing test**

```python
async def test_spotlight_includes_transient_stanza(builder, seeded_state, store, transient_service):
    await transient_service.set(seeded_state.campaign_id, EntityKind.CHARACTER,
                                "char_florence", "mood", "guarded",
                                provenance=Provenance.EXTRACTOR_AUTO)
    await transient_service.set(seeded_state.campaign_id, EntityKind.CHARACTER,
                                "char_florence", "current_action", "fastening her cloak",
                                provenance=Provenance.EXTRACTOR_AUTO,
                                source_post_id="p_last")
    prompt = await builder.build(player_input="...", campaign_id=seeded_state.campaign_id, ...)
    blob = "\n".join(m.content for m in prompt.messages)
    assert "winifred Allard — current state:" in blob
    assert "mood: guarded" in blob
    assert "action: fastening her cloak" in blob


async def test_internal_thought_filtered_for_audience(...):
    # Same setup, char privacy.internal_thoughts.surface_in_context=False
    # Build with audience observer; internal_thought line absent.
```

- [ ] **Step 2: Implement stanza renderer** — wire `TransientStateService` into the Context Builder's constructor (optional `transient_state: TransientStateService | None = None` kwarg); emit a `_TierItem(tier=SPOTLIGHT, section="transient", text=..., priority=8.5)` per present character right after the voice anchor.

Render template (Jinja2 or simple f-string):

```
{name} — current state:
  mood: {mood}
  intent: {intent}
  action: {current_action}
  thinking: {internal_thought}
```

Empty / None values are omitted.

- [ ] **Step 3-N**: tests PASS, commit, merge E.

---

# Branch F — REST surface

**Working directory:** `.worktrees/transient-state-F-rest`
**Depends on:** A, B, C merged.

### Task F1: 8 REST routes

**Files:**
- Create: `backend/src/grimoire/api/transient_state.py`
- Modify: `backend/src/grimoire/main.py` to mount the new subrouter.
- Test: `backend/tests/api/test_transient_state_routes.py`

Routes (per spec §REST surface):
```
GET    /campaigns/{id}/entities/{kind}/{eid}/transient
GET    /campaigns/{id}/entities/{kind}/{eid}/transient/{field}
PATCH  /campaigns/{id}/entities/{kind}/{eid}/transient/{field}
DELETE /campaigns/{id}/entities/{kind}/{eid}/transient[/{field}]
GET    /campaigns/{id}/entities/{kind}/{eid}/transient/{field}/history
POST   /campaigns/{id}/entities/{kind}/{eid}/transient/{field}/promote-to-fact
GET    /campaigns/{id}/transient/conflicts
```

Each handler is a thin pydantic-typed wrapper around `TransientStateService`.

- [ ] **Step 1: Failing test for GET single field**

```python
async def test_get_field_returns_404_when_absent(client, seeded_campaign):
    r = await client.get(f"/campaigns/{seeded_campaign}/entities/character/x/transient/mood")
    assert r.status_code == 404


async def test_patch_then_get_roundtrips(client, seeded_campaign):
    r = await client.patch(
        f"/campaigns/{seeded_campaign}/entities/character/x/transient/mood",
        json={"value": "guarded"},
    )
    assert r.status_code == 200
    assert (await client.get(
        f"/campaigns/{seeded_campaign}/entities/character/x/transient/mood"
    )).json()["value"] == "guarded"


# ... + DELETE, /history, /promote-to-fact, /conflicts routes
```

- [ ] **Step 2: Implement routes** with pydantic request/response models.

- [ ] **Step 3-N: Tests PASS + commit + merge.**

---

# Branch G — Reset triggers (scene-end + time-skip)

**Working directory:** `.worktrees/transient-state-G-triggers`
**Depends on:** B merged.

### Task G1: Scene-end handler

The TransientStateService subscribes to the event bus on construction. On `scene_ended(campaign_id, scene_id, location_ref, present_character_refs)`, walks scene-scoped fields and expires them.

- [ ] **Step 1: Failing test**

```python
async def test_ambient_mood_expires_on_scene_end(service, store, seeded_campaign, event_bus):
    await service.set(seeded_campaign, EntityKind.LOCATION, "loc_pub", "ambient_mood",
                      "tense", provenance=Provenance.EXTRACTOR_AUTO)
    await event_bus.emit(Event(
        type="scene_ended",
        campaign_id=seeded_campaign,
        scene_id="s_1",
        location_ref="loc_pub",
        present_character_refs=[],
    ))
    # ambient_mood has scene_scope=True; should now be expired
    assert await service.get(seeded_campaign, EntityKind.LOCATION, "loc_pub", "ambient_mood") is None


async def test_persistent_field_not_expired_on_scene_end(...):
    # faction alert_level (no decay) survives scene_end


async def test_time_skip_24h_resets_default_fields(...):
    # mood, intent, posture default-reset on >24h time advance
```

- [ ] **Step 2: Subscribe handlers** at service construction:

```python
self.store.event_bus.subscribe("scene_ended", self._on_scene_ended)
self.store.event_bus.subscribe("time_advanced", self._on_time_advanced)
```

Handlers walk the decay table for scene-scope/time-skip-reset fields and apply `clear(...)` for matching rows.

- [ ] **Step 3-N: Tests PASS, commit, merge.**

---

# Branch H — Promote-to-fact contract

**Working directory:** `.worktrees/transient-state-H-promotion`
**Depends on:** B, plus the existing Continuity service.

### Task H1: promote_to_fact

**Files:**
- Modify: `backend/src/grimoire/transient_state/service.py` — add `promote_to_fact`.
- Test: `backend/tests/transient_state/test_promotion.py`.

- [ ] **Step 1: Failing test**

```python
async def test_promote_creates_fact_and_supersedes_transient(
    service, continuity_service, seeded_campaign,
):
    await service.set(seeded_campaign, EntityKind.CHARACTER, "x", "mood", "haunted",
                      provenance=Provenance.EXTRACTOR_AUTO,
                      source_post_id="p_1")
    fact_id, transient_id = await service.promote_to_fact(
        campaign_id=seeded_campaign,
        entity_kind=EntityKind.CHARACTER,
        entity_id="x",
        field="mood",
        evidence="She kept watching the door.",
        turn_id="t_1",
    )
    # Continuity has the fact
    fact = await continuity_service.get_fact(seeded_campaign, fact_id)
    assert fact.text == "x has mood: haunted"
    # Transient is superseded
    assert await service.get(seeded_campaign, EntityKind.CHARACTER, "x", "mood") is None


async def test_promote_aborts_if_continuity_finds_contradiction(...):
    # Continuity check_contradictions returns CONFLICT
    # promote_to_fact raises; transient row unchanged
```

- [ ] **Step 2: Implement method** — call `ContinuityService.add_fact(text=..., established_in_post=...)` (goes through existing contradiction check), then mark the transient row as superseded by a sentinel "promoted to fact" expiration.

- [ ] **Step 3-N: Tests PASS, commit, merge.**

---

# Final integration check

After all 8 branches merge to main:

- [ ] **Step F1: Full suite passes**

```powershell
pytest backend/tests -v --ignore=backend/tests/perf
```

Expected: all tests pass; transient-state coverage > 85%.

- [ ] **Step F2: HUD aggregate latency check** — perf test in `backend/tests/perf/test_transient_bulk_read.py`:

```python
async def test_bulk_read_5_chars_8_fields_under_50ms(service, seeded_campaign):
    # Seed 5 characters × 8 fields
    for i in range(5):
        for field in ("mood", "intent", "current_action", "posture",
                      "internal_thought", "focus_of_attention",
                      "relationship_tone_toward_pc", "energy_level"):
            await service.set(seeded_campaign, EntityKind.CHARACTER, f"c_{i}",
                              field, "value", provenance=Provenance.EXTRACTOR_AUTO)
    import time
    t0 = time.perf_counter()
    bulk = await service.get_bulk(seeded_campaign, EntityKind.CHARACTER,
                                  [f"c_{i}" for i in range(5)])
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert elapsed_ms < 50
```

- [ ] **Step F3: Update memory** — `docs/superpowers/specs/2026-05-19-transient-state-COMPLETED.md` records the implementation deviations vs the spec (if any) and any deferred follow-ups.

- [ ] **Step F4: Delete `docs/superpowers/specs/2026-05-19-transient-state-design.md`** — replaced by the COMPLETED doc per the project pattern. Keep the plan.

```powershell
git mv docs/superpowers/specs/2026-05-19-transient-state-design.md docs/superpowers/specs/2026-05-19-transient-state-COMPLETED.md
# Edit the file to reflect "shipped" framing and any deltas
git commit -m "docs: transient-state COMPLETED" ...
```
