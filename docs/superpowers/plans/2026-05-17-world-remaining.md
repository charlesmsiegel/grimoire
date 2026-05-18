# World Remaining Work Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land everything in `docs/superpowers/specs/2026-05-17-world-remaining-design.md` §1–§11 (§12–§14 explicitly deferred/rejected and out of scope). §7 takes option **(b)** — widen spatial queries to resolve through the composition cascade.

**Architecture:** Eight parallel feature branches under `.worktrees/`. Branch **A** (config + calendar policy) lands first; branches **B–H** depend only on A and can rebase + merge independently:

- **A** `feature/world-A-config` — `WorldConfig` nested dataclass, `from_yaml` loader, plumbed into `WorldService.__init__`. Threads `lore.min_length` / `lore.max_results` into `lore_for_post`/`lore_by_keyword`. Implements `composition.multiple_calendars_policy` in `calendar_for_campaign`.
- **B** `feature/world-B-lore` — secrecy filtering on `search_lore`/`lore_by_keyword`/`lore_for_post`; FTS-backed `search_lore` via `StateStore.keyword_search(kinds=('lore',))`.
- **C** `feature/world-C-weather-extractor` — rule-based extractor pattern for "it began to rain"-style overrides; new `WorldService.apply_weather_override_delta(delta)`; orchestrator dispatcher hook so OVERRIDE_WRITE deltas targeting `location_state` route through the world service.
- **D** `feature/world-D-atmosphere` — new `atmosphere_generate` LLM task; Jinja templates; `WorldService._generate_atmosphere(...)` called from `create_world` when frontmatter has no atmosphere and `WorldConfig.atmosphere_auto_generate` is on.
- **E** `feature/world-E-spatial-composition` — option (b): `LocationConnection.to` accepts an entity ref; new migration adds a `location_connections` index (or extends frontmatter parsing) so cross-world refs resolve; `adjacent_locations`/`path_between`/`locations_within` take `entity_ref` + `campaign_id`.
- **F** `feature/world-F-greeting-handoff` — `WorldService.seed_scene_from_greeting(campaign_id, greeting_id)` builds a `SceneInit` from a `Greeting`, calls `SceneManager.start_scene`, and (when LLM gateway is configured) queues an opening-narration task. Wired into `create_campaign` REST handler.
- **G** `feature/world-G-emergent-location` — extractor rule detects unresolved location refs; new `location_generate` LLM task; `WorldService.apply_emergent_location_delta(delta)` writes via `state_store.write_emergent` and queues for review.
- **H** `feature/world-H-location-state-and-faction-delta` — full `LocationStateData` get/update on `WorldService` going through `apply_delta`; `update_faction_state` re-routed through `apply_delta` so undo/fork/retcon work.

Each branch rebases on `main` immediately before merge. Merge order: **A first, then B–H in any order** with rebase. Conflicts between B–H should be minimal (different files in most cases); when they overlap (`world/service.py`), the second branch rebases.

**Tech Stack:** Python 3.12, FastAPI, aiosqlite, pytest + pytest-asyncio (`asyncio_mode = "auto"`), Pydantic v2, Jinja2 templates, PowerShell shell. Test runner: `pytest backend/tests/world -v`.

---

## Conventions used in this plan

- **Test runner:** `pytest backend/tests/world -v` (or specific file/test). Async by default — no `@pytest.mark.asyncio` decorator needed.
- **Lint/format:** `ruff check backend/src/grimoire/world backend/tests/world` and `ruff format <same paths>`. Run both before every commit.
- **World fixture:** `world` fixture in `backend/tests/world/conftest.py` yields a `WorldService`; `library` yields a `LibraryService`; `store` yields a `StateStore`. Use `store.upsert_campaign(...)` to seed a campaign.
- **Worktree convention (memory):** Worktrees go under `.worktrees/` at repo root.
- **Merge convention (memory):** Use `git merge --ff-only` after rebase. Don't push.
- **Commit footer:** `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`
- **DB schema migrations:** `backend/src/grimoire/storage/migrations/NNN_<desc>.sql`. Raw SQL only. Apply by writing the file — tests auto-apply via the `store` fixture. Latest as of plan writing: 019.

---

## Branch setup (once, before any task)

- [ ] **Step S1: Create worktree directories**

```powershell
git worktree add .worktrees/world-A-config -b feature/world-A-config main
git worktree add .worktrees/world-B-lore -b feature/world-B-lore main
git worktree add .worktrees/world-C-weather-extractor -b feature/world-C-weather-extractor main
git worktree add .worktrees/world-D-atmosphere -b feature/world-D-atmosphere main
git worktree add .worktrees/world-E-spatial-composition -b feature/world-E-spatial-composition main
git worktree add .worktrees/world-F-greeting-handoff -b feature/world-F-greeting-handoff main
git worktree add .worktrees/world-G-emergent-location -b feature/world-G-emergent-location main
git worktree add .worktrees/world-H-location-state -b feature/world-H-location-state main
```

B–H initially branch from `main`. Rebase each onto the merged branch A before starting its tasks.

---

# Branch A — Config + calendar policy (§1 + §6)

**Working directory:** `.worktrees/world-A-config`
**Why it goes first:** Defines `WorldConfig` that branches B (lore knobs), D (atmosphere flag), G (procedural location feature flag), and the calendar policy in §6 all need.

### Task A1: `WorldConfig` nested dataclasses

**Files:**
- Create: `backend/src/grimoire/world/config.py`
- Modify: `backend/src/grimoire/world/__init__.py` (re-export)
- Test: `backend/tests/world/test_config.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/world/test_config.py
"""WorldConfig nested dataclass (§1)."""

from __future__ import annotations

from pathlib import Path

from grimoire.world.config import (
    CompositionPolicyConfig,
    LoreConfig,
    WeatherConfig,
    WorldConfig,
)


def test_defaults_match_spec() -> None:
    cfg = WorldConfig()
    assert cfg.weather.enabled is True
    assert cfg.weather.seed_per_campaign is True
    assert cfg.weather.model == "rule_based"
    assert cfg.lore.keyword_match is True
    assert cfg.lore.keyword_min_length == 4
    assert cfg.lore.max_lore_in_archive == 5
    assert cfg.atmosphere_auto_generate is True
    assert cfg.composition.multiple_calendars_policy == "pick"


def test_from_yaml_parses_block(tmp_path: Path) -> None:
    path = tmp_path / "world.yaml"
    path.write_text(
        "weather:\n"
        "  enabled: false\n"
        "  seed_per_campaign: false\n"
        "  model: stochastic\n"
        "lore:\n"
        "  keyword_match: false\n"
        "  keyword_min_length: 6\n"
        "  max_lore_in_archive: 3\n"
        "atmosphere_auto_generate: false\n"
        "composition:\n"
        "  multiple_calendars_policy: error\n",
        encoding="utf-8",
    )
    cfg = WorldConfig.from_yaml(path)
    assert cfg.weather.enabled is False
    assert cfg.weather.seed_per_campaign is False
    assert cfg.weather.model == "stochastic"
    assert cfg.lore.keyword_match is False
    assert cfg.lore.keyword_min_length == 6
    assert cfg.lore.max_lore_in_archive == 3
    assert cfg.atmosphere_auto_generate is False
    assert cfg.composition.multiple_calendars_policy == "error"


def test_from_yaml_missing_file_returns_defaults(tmp_path: Path) -> None:
    cfg = WorldConfig.from_yaml(tmp_path / "nope.yaml")
    assert cfg == WorldConfig()


def test_from_yaml_unknown_calendar_policy_rejected(tmp_path: Path) -> None:
    import pytest

    path = tmp_path / "world.yaml"
    path.write_text("composition:\n  multiple_calendars_policy: surprise\n", encoding="utf-8")
    with pytest.raises(ValueError, match="multiple_calendars_policy"):
        WorldConfig.from_yaml(path)
```

- [ ] **Step 2: Run, expect ImportError**

`pytest backend/tests/world/test_config.py -v` → FAIL (no module).

- [ ] **Step 3: Implement config module**

```python
# backend/src/grimoire/world/config.py
"""Top-level World YAML config (spec 09 §Configuration).

Mirrors the OrchestratorConfig shape: nested dataclasses with sensible
defaults, an optional ``from_yaml`` loader. Threaded into
:class:`WorldService` at construction time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


_CALENDAR_POLICIES: frozenset[str] = frozenset({"pick", "merge_warn", "error"})


@dataclass(frozen=True, slots=True)
class WeatherConfig:
    enabled: bool = True
    seed_per_campaign: bool = True
    model: str = "rule_based"


@dataclass(frozen=True, slots=True)
class LoreConfig:
    keyword_match: bool = True
    keyword_min_length: int = 4
    max_lore_in_archive: int = 5


@dataclass(frozen=True, slots=True)
class CompositionPolicyConfig:
    # 'pick' | 'merge_warn' | 'error'.
    multiple_calendars_policy: str = "pick"


@dataclass(frozen=True, slots=True)
class WorldConfig:
    weather: WeatherConfig = field(default_factory=WeatherConfig)
    lore: LoreConfig = field(default_factory=LoreConfig)
    atmosphere_auto_generate: bool = True
    composition: CompositionPolicyConfig = field(default_factory=CompositionPolicyConfig)

    @classmethod
    def from_yaml(cls, path: Path) -> WorldConfig:
        if not path.exists():
            return cls()
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            return cls()
        return cls._from_mapping(raw)

    @classmethod
    def _from_mapping(cls, raw: dict[str, Any]) -> WorldConfig:
        w = raw.get("weather") or {}
        lo = raw.get("lore") or {}
        comp = raw.get("composition") or {}
        policy = str(comp.get("multiple_calendars_policy") or "pick")
        if policy not in _CALENDAR_POLICIES:
            raise ValueError(
                f"multiple_calendars_policy must be one of {sorted(_CALENDAR_POLICIES)!r}, "
                f"got {policy!r}"
            )
        return cls(
            weather=WeatherConfig(
                enabled=bool(w.get("enabled", True)),
                seed_per_campaign=bool(w.get("seed_per_campaign", True)),
                model=str(w.get("model") or "rule_based"),
            ),
            lore=LoreConfig(
                keyword_match=bool(lo.get("keyword_match", True)),
                keyword_min_length=int(lo.get("keyword_min_length", 4)),
                max_lore_in_archive=int(lo.get("max_lore_in_archive", 5)),
            ),
            atmosphere_auto_generate=bool(raw.get("atmosphere_auto_generate", True)),
            composition=CompositionPolicyConfig(multiple_calendars_policy=policy),
        )


__all__ = [
    "CompositionPolicyConfig",
    "LoreConfig",
    "WeatherConfig",
    "WorldConfig",
]
```

Re-export from `__init__.py`:
```python
from grimoire.world.config import (
    CompositionPolicyConfig,
    LoreConfig,
    WeatherConfig,
    WorldConfig,
)
```

Add to `__all__`: `"CompositionPolicyConfig"`, `"LoreConfig"`, `"WeatherConfig"`, `"WorldConfig"`.

- [ ] **Step 4: Run tests, expect pass**

- [ ] **Step 5: Lint + commit**

```powershell
ruff check backend/src/grimoire/world backend/tests/world ; if ($?) { ruff format backend/src/grimoire/world backend/tests/world }
git add -A
git commit -m @'
Add WorldConfig YAML loader (§1)

Nested dataclasses (WeatherConfig, LoreConfig, CompositionPolicyConfig)
with sensible defaults. from_yaml() parses spec §Configuration block;
unknown calendar policies are rejected. Wiring into WorldService and
main.py follows in the next commits.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
'@
```

### Task A2: Thread `WorldConfig` through `WorldService`

**Files:**
- Modify: `backend/src/grimoire/world/service.py` (`__init__`, `lore_for_post`, `lore_by_keyword`)
- Test: `backend/tests/world/test_config_plumbing.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/world/test_config_plumbing.py
"""WorldConfig knobs flow into lore_for_post / lore_by_keyword."""

from __future__ import annotations

import pytest

from grimoire.library import LibraryService
from grimoire.state_store import StateStore
from grimoire.world import LoreConfig, WorldConfig, WorldService


@pytest.fixture
async def world_with_lore(store: StateStore, library: LibraryService):
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    await library.create_world("w1", {"id": "w1", "name": "W1"})
    await library.create_entity(
        "w1",
        "lore",
        "fire",
        {"id": "fire", "name": "Fire Lore", "keywords": ["fire", "ember"]},
        body="Long ago, fire was discovered.",
    )
    await library.create_entity(
        "w1",
        "lore",
        "ice",
        {"id": "ice", "name": "Ice Lore", "keywords": ["ice", "frost"]},
        body="Ice is cold.",
    )
    await store.upsert_world_ref(
        campaign_id="camp-1", world_id="w1", priority=1,
        include=None, track_latest=True, bound_at_version=None,
    )
    return WorldService(library)


async def test_config_overrides_default_min_length(world_with_lore) -> None:
    svc = world_with_lore
    svc.config = WorldConfig(lore=LoreConfig(keyword_min_length=10))
    hits = await svc.lore_by_keyword("fire", campaign_id="camp-1")
    assert hits == []  # "fire" is 4 chars, below the configured 10


async def test_config_default_min_length_still_works(world_with_lore) -> None:
    svc = world_with_lore  # default min_length=4
    hits = await svc.lore_by_keyword("fire", campaign_id="camp-1")
    assert len(hits) == 1


async def test_lore_for_post_honors_config_max_results(world_with_lore) -> None:
    svc = world_with_lore
    svc.config = WorldConfig(lore=LoreConfig(max_lore_in_archive=1))
    hits = await svc.lore_for_post(
        "the fire burned bright next to the ice", campaign_id="camp-1"
    )
    assert len(hits) == 1
```

- [ ] **Step 2: Run, expect fail**

- [ ] **Step 3: Add `config` kwarg + thread through methods**

In `backend/src/grimoire/world/service.py`, modify `__init__`:

```python
    def __init__(
        self,
        library: LibraryService,
        *,
        config: WorldConfig | None = None,
    ) -> None:
        self.library = library
        self.store: StateStore = library.store
        self.config = config or WorldConfig()
```

(Add the import: `from grimoire.world.config import WorldConfig` at the top.)

Replace `lore_by_keyword` body:

```python
    async def lore_by_keyword(
        self,
        keyword: str,
        campaign_id: CampaignId,
        *,
        min_length: int | None = None,
    ) -> list[LoreEntry]:
        """Match lore whose ``keywords`` list contains ``keyword`` (case-insensitive)."""
        effective_min = self.config.lore.keyword_min_length if min_length is None else min_length
        kw = (keyword or "").strip().lower()
        if len(kw) < effective_min:
            return []
        entities = await self.library.list_for_composition(campaign_id, EntityKind.LORE)
        out: list[LoreEntry] = []
        for ent in entities:
            lore = _lore_from_entity(ent)
            if any(kw == k.strip().lower() for k in lore.keywords):
                out.append(lore)
        return out
```

Replace `lore_for_post` body:

```python
    async def lore_for_post(
        self,
        text: str,
        campaign_id: CampaignId,
        *,
        min_length: int | None = None,
        max_results: int | None = None,
    ) -> list[LoreEntry]:
        """Scan a post for lore-keyword triggers; used by the Context Builder."""
        effective_min = self.config.lore.keyword_min_length if min_length is None else min_length
        effective_max = self.config.lore.max_lore_in_archive if max_results is None else max_results
        body = (text or "").lower()
        if not body:
            return []
        entities = await self.library.list_for_composition(campaign_id, EntityKind.LORE)
        triggered: list[LoreEntry] = []
        for ent in entities:
            lore = _lore_from_entity(ent)
            for kw in lore.keywords:
                kw_lower = kw.strip().lower()
                if len(kw_lower) < effective_min:
                    continue
                if kw_lower in body:
                    triggered.append(lore)
                    break
        return triggered[:effective_max]
```

- [ ] **Step 4: Run, expect pass**

- [ ] **Step 5: Commit**

```
git commit -m "Thread WorldConfig.lore knobs into lore_for_post / lore_by_keyword"
```

### Task A3: §6 calendar policy enforcement

**Files:**
- Modify: `backend/src/grimoire/world/service.py` (`calendar_for_campaign`)
- Modify: `backend/src/grimoire/world/errors.py` (add `MultipleCalendarsError` if needed — `CompositionError` already exists, reuse)
- Test: `backend/tests/world/test_calendar_policy.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/world/test_calendar_policy.py
"""§6 multi-world calendar policy: pick | merge_warn | error."""

from __future__ import annotations

import logging

import pytest

from grimoire.world import CompositionPolicyConfig, WorldConfig
from grimoire.world.errors import CompositionError


async def _seed_two_worlds_with_different_calendars(store, library) -> None:
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    await library.create_world(
        "w1",
        {
            "id": "w1",
            "name": "W1",
            "calendar": {
                "months": [{"name": "Frostmoon", "days": 30}],
                "days_per_week": 7,
                "week_day_names": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            },
        },
    )
    await library.create_world(
        "w2",
        {
            "id": "w2",
            "name": "W2",
            "calendar": {
                "months": [{"name": "Firewane", "days": 28}],
                "days_per_week": 10,
                "week_day_names": ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"],
            },
        },
    )
    await store.upsert_world_ref(
        campaign_id="camp-1", world_id="w1", priority=1,
        include=None, track_latest=True, bound_at_version=None,
    )
    await store.upsert_world_ref(
        campaign_id="camp-1", world_id="w2", priority=2,
        include=None, track_latest=True, bound_at_version=None,
    )


async def test_pick_policy_returns_highest_priority(store, library, world) -> None:
    await _seed_two_worlds_with_different_calendars(store, library)
    cal = await world.calendar_for_campaign("camp-1")
    assert cal.world_id == "w1"  # priority 1 wins


async def test_merge_warn_logs_warning(store, library, world, caplog) -> None:
    await _seed_two_worlds_with_different_calendars(store, library)
    world.config = WorldConfig(
        composition=CompositionPolicyConfig(multiple_calendars_policy="merge_warn")
    )
    with caplog.at_level(logging.WARNING, logger="grimoire.world.service"):
        cal = await world.calendar_for_campaign("camp-1")
    assert cal.world_id == "w1"
    assert any(
        "multiple worlds declare calendars" in record.message for record in caplog.records
    )


async def test_error_policy_raises(store, library, world) -> None:
    await _seed_two_worlds_with_different_calendars(store, library)
    world.config = WorldConfig(
        composition=CompositionPolicyConfig(multiple_calendars_policy="error")
    )
    with pytest.raises(CompositionError, match="conflicting calendars"):
        await world.calendar_for_campaign("camp-1")


async def test_error_policy_silent_when_only_one_calendar(store, library, world) -> None:
    """When a campaign has multiple world refs but only one declares a calendar,
    the policy should not fire."""
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    await library.create_world(
        "w1",
        {
            "id": "w1",
            "name": "W1",
            "calendar": {"months": [{"name": "M1", "days": 30}]},
        },
    )
    await library.create_world("w2", {"id": "w2", "name": "W2"})
    await store.upsert_world_ref(
        campaign_id="camp-1", world_id="w1", priority=1,
        include=None, track_latest=True, bound_at_version=None,
    )
    await store.upsert_world_ref(
        campaign_id="camp-1", world_id="w2", priority=2,
        include=None, track_latest=True, bound_at_version=None,
    )
    world.config = WorldConfig(
        composition=CompositionPolicyConfig(multiple_calendars_policy="error")
    )
    cal = await world.calendar_for_campaign("camp-1")
    assert cal.world_id == "w1"
```

- [ ] **Step 2: Run, expect failure (current behavior is unconditional 'pick')**

- [ ] **Step 3: Update `calendar_for_campaign`**

```python
    async def calendar_for_campaign(self, campaign_id: CampaignId) -> WorldCalendar:
        comp = await self.library.get_composition(campaign_id)
        refs = sorted(comp.worlds, key=lambda r: r.priority)
        if not refs:
            raise CompositionError(f"campaign {campaign_id!r} has no world refs")

        # Resolve each ref's calendar block; we treat an empty/missing
        # ``calendar`` field as "this world does not contribute a calendar"
        # so a multi-world campaign in which only one world has a calendar
        # never trips the merge_warn / error branches.
        ref_cals: list[tuple[str, WorldCalendar]] = []
        for ref in refs:
            meta = await self.library.get_world(ref.world_id)
            raw = meta.calendar if isinstance(meta.calendar, dict) else {}
            if not raw:
                continue
            ref_cals.append((ref.world_id, parse_calendar(ref.world_id, raw)))

        if not ref_cals:
            return await self.calendar_for(refs[0].world_id)

        picked_world, picked_cal = ref_cals[0]
        if len(ref_cals) == 1:
            return picked_cal

        # Compare: if any other ref's calendar differs from the picked one,
        # apply the configured policy.
        conflicting = [
            world_id for world_id, cal in ref_cals[1:] if cal != picked_cal
        ]
        if not conflicting:
            return picked_cal

        policy = self.config.composition.multiple_calendars_policy
        if policy == "merge_warn":
            logger.warning(
                "multiple worlds declare calendars for campaign %s; picking %s, "
                "conflicting refs: %s",
                campaign_id, picked_world, conflicting,
            )
            return picked_cal
        if policy == "error":
            raise CompositionError(
                f"campaign {campaign_id!r} has conflicting calendars across worlds "
                f"({picked_world!r} vs {conflicting!r}); set "
                f"composition.multiple_calendars_policy = 'pick' or 'merge_warn'"
            )
        # 'pick' is the default — silent.
        return picked_cal
```

Add `import logging` and `logger = logging.getLogger(__name__)` near the top of `service.py` if not already there.

- [ ] **Step 4: Run, expect pass**

- [ ] **Step 5: Commit**

```
git commit -m "Enforce composition.multiple_calendars_policy in calendar_for_campaign (§6)"
```

### Task A4: Wire `WorldConfig.from_yaml` in `main.py`

**Files:**
- Modify: `backend/src/grimoire/main.py` (load config, pass to `WorldService(...)`)

- [ ] **Step 1: Read existing wiring**

Find the line constructing `WorldService(...)` in `main.py`. Existing code likely looks like:

```python
container.world = WorldService(container.library)
```

- [ ] **Step 2: Add YAML load + pass**

```python
from grimoire.world import WorldConfig

world_config_path = data_root / "config" / "world.yaml"
world_cfg = WorldConfig.from_yaml(world_config_path)
container.world = WorldService(container.library, config=world_cfg)
```

- [ ] **Step 3: Smoke-test app boot**

```powershell
python -c "from grimoire.main import create_app; create_app()"
```

- [ ] **Step 4: Commit**

```
git commit -m "Wire WorldConfig.from_yaml through main.py lifespan"
```

### Task A5: Merge branch A

- [ ] **Step 1: Rebase + run full suite**

```powershell
git fetch origin main:main
git rebase main
/c/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest tests/world -v
```

- [ ] **Step 2: ff-merge to main**

From repo root:
```powershell
cd ..\.. ; git checkout main ; git merge --ff-only feature/world-A-config
```

---

# Branch B — Lore upgrades (§2 secrecy + §4 FTS)

**Working directory:** `.worktrees/world-B-lore`
**Depends on:** Branch A merged (uses `WorldConfig`).

### Task B1: Secrecy filtering on lore methods

**Files:**
- Modify: `backend/src/grimoire/world/service.py` (`search_lore`, `lore_by_keyword`, `lore_for_post`)
- Test: `backend/tests/world/test_lore_secrecy.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/world/test_lore_secrecy.py
"""§2 Player-facing lore views filter restricted + secret entries."""

from __future__ import annotations

import pytest


async def _seed_lore_with_secrecy(store, library) -> None:
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    await library.create_world("w1", {"id": "w1", "name": "W1"})
    for asset_id, secrecy in [
        ("public-fact", "public"),
        ("common-knowledge", "common-knowledge"),
        ("restricted-fact", "restricted"),
        ("secret-fact", "secret"),
    ]:
        await library.create_entity(
            "w1",
            "lore",
            asset_id,
            {
                "id": asset_id,
                "name": f"{asset_id.title()}",
                "keywords": ["thing"],
                "secrecy": secrecy,
            },
            body=f"Lore body for {asset_id}.",
        )
    await store.upsert_world_ref(
        campaign_id="camp-1", world_id="w1", priority=1,
        include=None, track_latest=True, bound_at_version=None,
    )


async def test_default_audience_returns_all(store, library, world) -> None:
    await _seed_lore_with_secrecy(store, library)
    out = await world.lore_by_keyword("thing", campaign_id="camp-1")
    ids = {e.id for e in out}
    assert ids == {"public-fact", "common-knowledge", "restricted-fact", "secret-fact"}


async def test_player_audience_drops_restricted_and_secret(store, library, world) -> None:
    await _seed_lore_with_secrecy(store, library)
    out = await world.lore_by_keyword("thing", campaign_id="camp-1", audience="player")
    ids = {e.id for e in out}
    assert ids == {"public-fact", "common-knowledge"}


async def test_model_audience_returns_all_explicit(store, library, world) -> None:
    await _seed_lore_with_secrecy(store, library)
    out = await world.lore_by_keyword("thing", campaign_id="camp-1", audience="model")
    ids = {e.id for e in out}
    assert ids == {"public-fact", "common-knowledge", "restricted-fact", "secret-fact"}


async def test_lore_for_post_player_filter(store, library, world) -> None:
    await _seed_lore_with_secrecy(store, library)
    out = await world.lore_for_post(
        "tell me about this thing", campaign_id="camp-1", audience="player"
    )
    assert {e.id for e in out} == {"public-fact", "common-knowledge"}


async def test_search_lore_player_filter(store, library, world) -> None:
    await _seed_lore_with_secrecy(store, library)
    out = await world.search_lore("Lore body", campaign_id="camp-1", audience="player")
    assert {e.id for e in out} == {"public-fact", "common-knowledge"}


async def test_unknown_audience_raises(store, library, world) -> None:
    await _seed_lore_with_secrecy(store, library)
    with pytest.raises(ValueError, match="audience"):
        await world.lore_by_keyword("thing", campaign_id="camp-1", audience="alien")
```

- [ ] **Step 2: Run, expect fail (no audience param)**

- [ ] **Step 3: Implement audience filter**

In `world/service.py`, add a module-level helper:

```python
_PLAYER_HIDDEN_SECRECIES: frozenset[str] = frozenset({"restricted", "secret"})
_VALID_AUDIENCES: frozenset[str] = frozenset({"model", "player"})


def _filter_by_audience(entries: list[LoreEntry], audience: str) -> list[LoreEntry]:
    if audience == "model":
        return entries
    return [
        e
        for e in entries
        if (e.secrecy or "public").lower() not in _PLAYER_HIDDEN_SECRECIES
    ]
```

Update each lore method to accept `audience: str = "model"`. Validate, then apply the filter:

```python
    async def lore_by_keyword(
        self,
        keyword: str,
        campaign_id: CampaignId,
        *,
        min_length: int | None = None,
        audience: str = "model",
    ) -> list[LoreEntry]:
        if audience not in _VALID_AUDIENCES:
            raise ValueError(f"audience must be one of {sorted(_VALID_AUDIENCES)!r}")
        effective_min = self.config.lore.keyword_min_length if min_length is None else min_length
        kw = (keyword or "").strip().lower()
        if len(kw) < effective_min:
            return []
        entities = await self.library.list_for_composition(campaign_id, EntityKind.LORE)
        out: list[LoreEntry] = []
        for ent in entities:
            lore = _lore_from_entity(ent)
            if any(kw == k.strip().lower() for k in lore.keywords):
                out.append(lore)
        return _filter_by_audience(out, audience)
```

Apply the same `audience` parameter + filter to `lore_for_post` and `search_lore`. For `lore_for_post`, apply `_filter_by_audience` BEFORE the `max_results` truncation so the cap counts visible entries only.

- [ ] **Step 4: Run, expect pass**

- [ ] **Step 5: Commit**

```
git commit -m "Filter lore by audience (player drops restricted+secret) (§2)"
```

### Task B2: FTS-backed `search_lore`

**Files:**
- Modify: `backend/src/grimoire/world/service.py` (`search_lore`)
- Test: extend `backend/tests/world/test_lore_secrecy.py` or new `backend/tests/world/test_lore_fts.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/world/test_lore_fts.py
"""§4 search_lore uses StateStore.keyword_search (FTS)."""

from __future__ import annotations


async def _seed_lore_in_two_worlds(store, library):
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    await library.create_world("w1", {"id": "w1", "name": "W1"})
    await library.create_world("w2", {"id": "w2", "name": "W2"})
    await library.create_entity(
        "w1", "lore", "fire",
        {"id": "fire", "name": "Fire Lore", "keywords": ["fire"]},
        body="The ancient dragons breathed fire upon the keep.",
    )
    await library.create_entity(
        "w2", "lore", "frost",
        {"id": "frost", "name": "Frost Lore", "keywords": ["frost"]},
        body="The winter spirits froze the river.",
    )


async def test_search_returns_hits_via_fts(store, library, world) -> None:
    await _seed_lore_in_two_worlds(store, library)
    await store.upsert_world_ref(
        campaign_id="camp-1", world_id="w1", priority=1,
        include=None, track_latest=True, bound_at_version=None,
    )
    out = await world.search_lore("dragons", campaign_id="camp-1")
    assert {e.id for e in out} == {"fire"}


async def test_search_filters_excluded_worlds(store, library, world) -> None:
    await _seed_lore_in_two_worlds(store, library)
    # Only w1 is in the composition; w2 lore must not leak.
    await store.upsert_world_ref(
        campaign_id="camp-1", world_id="w1", priority=1,
        include=None, track_latest=True, bound_at_version=None,
    )
    out = await world.search_lore("river", campaign_id="camp-1")
    assert out == []
```

- [ ] **Step 2: Run, expect fail (current substring scan doesn't hit "dragons" in body — actually it WOULD, but the FTS version should match `dragons` as a tokenised search rather than substring)**

Actually the substring version DOES match "dragons". So this test alone won't fail. Make a stronger assertion that FTS is actually used:

Append:
```python
async def test_search_lore_uses_keyword_search(store, library, world, monkeypatch) -> None:
    """Verify search_lore drives StateStore.keyword_search."""
    await _seed_lore_in_two_worlds(store, library)
    await store.upsert_world_ref(
        campaign_id="camp-1", world_id="w1", priority=1,
        include=None, track_latest=True, bound_at_version=None,
    )

    calls: list[dict] = []
    real_search = store.keyword_search

    async def spy(**kwargs):
        calls.append(kwargs)
        return await real_search(**kwargs)

    monkeypatch.setattr(store, "keyword_search", spy)
    await world.search_lore("dragons", campaign_id="camp-1")
    assert calls, "search_lore did not call StateStore.keyword_search"
    assert calls[0].get("kinds") == ("lore",)
```

- [ ] **Step 3: Run, expect fail**

- [ ] **Step 4: Implement FTS-backed search**

In `world/service.py`, replace `search_lore`:

```python
    async def search_lore(
        self,
        query: str,
        campaign_id: CampaignId,
        top_k: int = 5,
        *,
        audience: str = "model",
    ) -> list[LoreEntry]:
        """FTS-backed lore search filtered by composition + secrecy.

        Drives :meth:`StateStore.keyword_search` against the lore FTS index,
        then post-filters by the campaign's composition (so excluded worlds
        never leak) and by audience.
        """
        if audience not in _VALID_AUDIENCES:
            raise ValueError(f"audience must be one of {sorted(_VALID_AUDIENCES)!r}")
        q = (query or "").strip()
        if not q:
            return []

        hits = await self.store.keyword_search(
            query=q, kinds=("lore",), top_k=top_k * 4,
        )
        if not hits:
            return []

        # Restrict to lore reachable through the campaign's composition.
        in_composition = {
            ent.asset_id: ent
            for ent in await self.library.list_for_composition(
                campaign_id, EntityKind.LORE
            )
        }
        out: list[LoreEntry] = []
        for hit in hits:
            asset_id = getattr(hit, "asset_id", None) or hit.id.split(":")[-1]
            ent = in_composition.get(asset_id)
            if ent is None:
                continue
            out.append(_lore_from_entity(ent))
            if len(out) >= top_k:
                break
        return _filter_by_audience(out, audience)
```

(Note: `SearchHit`'s exact shape may need adjustment — look at `state_store/search.py` to confirm whether `hit.id` or `hit.library_id` etc.)

- [ ] **Step 5: Run, expect pass**

- [ ] **Step 6: Commit**

```
git commit -m "Switch search_lore to FTS via StateStore.keyword_search (§4)"
```

### Task B3: Merge branch B

Same as A5.

---

# Branch C — Weather override via Extractor (§5)

**Working directory:** `.worktrees/world-C-weather-extractor`
**Depends on:** Branch A merged.

### Task C1: Rule-based extractor pattern for weather

**Files:**
- Modify: `backend/src/grimoire/extractor/rule_based.py` (add weather phrase detector)
- Test: `backend/tests/extractor/test_weather_rule.py` (new)

- [ ] **Step 1: Inspect existing rule_based.py shape**

Read `backend/src/grimoire/extractor/rule_based.py` to understand the pattern. Existing rules typically use a regex + emit a `StateDelta`. The plan assumes:

```python
def _detect_X(text: str, scene: Scene) -> Iterable[StateDelta]: ...
```

If the existing pattern uses a different shape, adapt accordingly.

- [ ] **Step 2: Write failing test**

```python
# backend/tests/extractor/test_weather_rule.py
"""§5 Rule-based weather override detection."""

from __future__ import annotations

from grimoire.extractor.rule_based import detect_weather_override
from grimoire.types.state import DeltaKind
from grimoire.types.world import WeatherKind


def test_detects_began_to_rain() -> None:
    deltas = list(
        detect_weather_override(
            "and it began to rain heavily",
            campaign_id="camp-1",
            scene_location_ref="library:worlds/w1/locations/town",
            branch_id="camp-1:main",
        )
    )
    assert len(deltas) == 1
    d = deltas[0]
    assert d.kind == DeltaKind.OVERRIDE_WRITE
    assert d.target_table == "location_state"
    assert d.target_id == "library:worlds/w1/locations/town"
    assert d.after["weather"]["kind"] == WeatherKind.RAIN.value
    assert d.after["weather"]["source"] == "override"


def test_detects_snow_began_falling() -> None:
    deltas = list(
        detect_weather_override(
            "Suddenly, snow began falling across the rooftops.",
            campaign_id="camp-1",
            scene_location_ref="library:worlds/w1/locations/town",
            branch_id="camp-1:main",
        )
    )
    assert len(deltas) == 1
    assert deltas[0].after["weather"]["kind"] == WeatherKind.SNOW.value


def test_no_match_returns_empty() -> None:
    deltas = list(
        detect_weather_override(
            "they had a pleasant conversation",
            campaign_id="camp-1",
            scene_location_ref="library:worlds/w1/locations/town",
            branch_id="camp-1:main",
        )
    )
    assert deltas == []


def test_skip_when_no_scene_location() -> None:
    """Without a known location ref we can't write an override row."""
    deltas = list(
        detect_weather_override(
            "it began to storm",
            campaign_id="camp-1",
            scene_location_ref=None,
            branch_id="camp-1:main",
        )
    )
    assert deltas == []
```

- [ ] **Step 3: Run, expect ImportError**

- [ ] **Step 4: Implement detection rule**

Add to `backend/src/grimoire/extractor/rule_based.py`:

```python
import re
from collections.abc import Iterable

from grimoire.types.state import DeltaKind, StateDelta
from grimoire.types.world import Weather, WeatherKind


_WEATHER_PHRASES: list[tuple[re.Pattern[str], WeatherKind]] = [
    (re.compile(r"\b(?:began|started)\s+to\s+rain\b|\brain\s+began\b", re.I), WeatherKind.RAIN),
    (re.compile(r"\bsnow\s+(?:began|started)\s+falling\b|\b(?:began|started)\s+to\s+snow\b", re.I), WeatherKind.SNOW),
    (re.compile(r"\b(?:thunder|storm)\s+rolled\b|\b(?:began|started)\s+to\s+storm\b", re.I), WeatherKind.STORM),
    (re.compile(r"\b(?:fog|mist)\s+(?:rolled\s+in|descended|crept)\b", re.I), WeatherKind.FOG),
    (re.compile(r"\bwind\s+picked\s+up\b|\bwinds\s+rose\b", re.I), WeatherKind.WIND),
    (re.compile(r"\bskies?\s+cleared\b|\bsun\s+broke\s+through\b", re.I), WeatherKind.CLEAR),
]


def detect_weather_override(
    text: str,
    *,
    campaign_id: str,
    scene_location_ref: str | None,
    branch_id: str,
    confidence: float = 0.85,
) -> Iterable[StateDelta]:
    """Detect player-prose weather overrides; emit OVERRIDE_WRITE deltas.

    Returns an empty iterable when no scene location ref is supplied — without
    one we have nowhere to attach the override.
    """
    if not text or scene_location_ref is None:
        return
    for pattern, kind in _WEATHER_PHRASES:
        if pattern.search(text):
            weather = Weather(kind=kind, source="override")
            yield StateDelta(
                kind=DeltaKind.OVERRIDE_WRITE,
                target_scope="campaign-sqlite",
                target_table="location_state",
                target_id=scene_location_ref,
                after={
                    "campaign_id": campaign_id,
                    "branch_id": branch_id,
                    "weather": weather.model_dump(mode="json"),
                },
                confidence=confidence,
                source="extractor:rule_based:weather",
                evidence=text[:200],
            )
            return  # Only emit the first match per call
```

(Adjust `StateDelta` field names to match the actual dataclass in `types/state.py` — confirm `target_scope`, `target_table`, `target_id`, `after`, `confidence`, `source`, `evidence`.)

- [ ] **Step 5: Run, expect pass**

- [ ] **Step 6: Commit**

```
git commit -m "Add rule-based weather override detector (§5)"
```

### Task C2: Wire detector into extractor strategy

**Files:**
- Modify: `backend/src/grimoire/extractor/rule_based.py` (call `detect_weather_override` from the strategy entry point)
- Test: `backend/tests/extractor/test_weather_rule_integration.py` (new)

- [ ] **Step 1: Find the rule-based strategy's entry function**

Look for the function that aggregates all rule-based deltas. The plan assumes a `run_rule_based_extraction(...)` or similar; locate it and add `detect_weather_override(text, ...)` to its output.

- [ ] **Step 2: Skip a strategy-level test — defer to C3's end-to-end coverage**

A unit test for `detect_weather_override` already lives in `test_weather_rule.py` (Task C1). The strategy-level integration (does the dispatch list actually include the new detector?) is naturally covered by C3's end-to-end test in `test_weather_override_delta.py`, which exercises the full path: an extractor pass → delta is emitted → orchestrator dispatches → `WorldService.override_weather` is called → `weather_for` returns the override.

If you want an explicit strategy-level test, read `backend/tests/extractor/test_rule_based.py` first to find the existing helper that drives the rule-based pass, then mirror it. Otherwise, skip directly to step 3.

- [ ] **Step 3: Integrate the detector**

Find where `rule_based.py` lists/runs its detectors and add `detect_weather_override(...)` to the dispatch list. Pass through `scene_location_ref` and `branch_id` (these must already flow into the rule-based strategy from the extractor's `extract(...)` call — verify).

- [ ] **Step 4: Run, expect pass**

- [ ] **Step 5: Commit**

```
git commit -m "Wire weather-override detector into rule-based extractor strategy"
```

### Task C3: `WorldService.apply_weather_override_delta` + orchestrator hook

**Files:**
- Modify: `backend/src/grimoire/world/service.py` (add `apply_weather_override_delta`)
- Modify: `backend/src/grimoire/orchestrator/service.py` (`_apply_routing`: dispatch OVERRIDE_WRITE+location_state to WorldService when available)
- Test: `backend/tests/world/test_weather_override_delta.py` (new)

- [ ] **Step 1: Write failing test**

```python
# backend/tests/world/test_weather_override_delta.py
"""§5 WorldService applies weather-override deltas."""

from __future__ import annotations

from grimoire.types.state import DeltaKind, StateDelta
from grimoire.types.world import Weather, WeatherKind


async def _seed_world_with_location(store, library):
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    await library.create_world("w1", {"id": "w1", "name": "W1"})
    await library.create_entity(
        "w1", "location", "town",
        {"id": "town", "name": "Town", "kind": "city"}, body="",
    )
    await store.upsert_world_ref(
        campaign_id="camp-1", world_id="w1", priority=1,
        include=None, track_latest=True, bound_at_version=None,
    )


async def test_apply_writes_override(store, library, world) -> None:
    await _seed_world_with_location(store, library)
    delta = StateDelta(
        kind=DeltaKind.OVERRIDE_WRITE,
        target_scope="campaign-sqlite",
        target_table="location_state",
        target_id="library:worlds/w1/locations/town",
        after={
            "campaign_id": "camp-1",
            "branch_id": "camp-1:main",
            "weather": Weather(kind=WeatherKind.RAIN, source="override").model_dump(mode="json"),
        },
        confidence=0.9,
        source="extractor",
    )
    await world.apply_weather_override_delta(delta)
    from grimoire.types.common import InGameTime
    from datetime import datetime, UTC
    w = await world.weather_for(
        "w1", "town",
        when=InGameTime(moment=datetime(2025, 1, 1, 12, 0, tzinfo=UTC)),
        campaign_id="camp-1",
    )
    assert w.kind == WeatherKind.RAIN
    assert w.source == "override"


async def test_apply_rejects_non_override_delta(store, library, world) -> None:
    import pytest
    delta = StateDelta(
        kind=DeltaKind.FACT_ADD,
        target_scope="campaign-sqlite",
        target_table="facts",
        target_id="x",
        after={},
        confidence=0.5,
        source="extractor",
    )
    with pytest.raises(ValueError, match="OVERRIDE_WRITE"):
        await world.apply_weather_override_delta(delta)
```

- [ ] **Step 2: Run, expect fail**

- [ ] **Step 3: Implement method**

```python
    async def apply_weather_override_delta(self, delta: Any) -> None:
        """Apply an extractor-emitted weather override delta.

        Validates the delta kind/target_table, parses the payload, and
        routes through :meth:`override_weather`. Used by the orchestrator's
        delta-dispatch hook (§5 of world remaining-design).
        """
        from grimoire.types.state import DeltaKind

        if getattr(delta, "kind", None) != DeltaKind.OVERRIDE_WRITE:
            raise ValueError(
                f"apply_weather_override_delta requires kind=OVERRIDE_WRITE, "
                f"got {getattr(delta, 'kind', None)!r}"
            )
        if getattr(delta, "target_table", None) != "location_state":
            raise ValueError(
                f"apply_weather_override_delta requires target_table='location_state', "
                f"got {getattr(delta, 'target_table', None)!r}"
            )
        after = getattr(delta, "after", None) or {}
        location_ref = getattr(delta, "target_id", None) or ""
        campaign_id = after.get("campaign_id") or ""
        branch_id = after.get("branch_id") or f"{campaign_id}:main"
        weather_payload = after.get("weather") or {}
        weather = Weather.model_validate(weather_payload)
        # location_ref is "library:worlds/<world_id>/locations/<asset_id>"
        parts = location_ref.split("/")
        try:
            world_id = parts[parts.index("worlds") + 1]
            location_id = parts[parts.index("locations") + 1]
        except (ValueError, IndexError) as exc:
            raise ValueError(f"unparseable location_ref {location_ref!r}") from exc
        await self.override_weather(
            world_id, location_id, weather, campaign_id, branch_id=branch_id,
            source=delta.source or "extractor",
        )
```

- [ ] **Step 4: Add orchestrator dispatch hook**

In `orchestrator/service.py`, locate the `_apply_routing` method. Before calling `state_store.apply_delta` for each auto-applied delta, check if a world-side handler should intercept:

```python
            if decision is Decision.AUTO_APPLY:
                # World module owns specific OVERRIDE_WRITE targets.
                if (
                    self._world is not None
                    and delta.kind == DeltaKind.OVERRIDE_WRITE
                    and delta.target_table == "location_state"
                ):
                    try:
                        await self._world.apply_weather_override_delta(delta)
                        continue
                    except Exception:
                        logger.exception("world weather-override apply failed; falling through")
                await self._store.apply_delta(...)
```

If `OrchestratorService` doesn't currently hold a `_world` reference, add `world: Any = None` to its constructor and pass `container.world` in `main.py`. (Use `Any` to avoid a hard module import dependency in the orchestrator layer.)

- [ ] **Step 5: Run, expect pass**

- [ ] **Step 6: Commit**

```
git commit -m "Apply weather-override deltas through WorldService.override_weather (§5)"
```

### Task C4: Merge branch C

Same as A5.

---

# Branch D — Atmosphere generation (§3)

**Working directory:** `.worktrees/world-D-atmosphere`
**Depends on:** Branch A merged.

### Task D1: Atmosphere generator + templates

**Files:**
- Create: `backend/src/grimoire/world/atmosphere.py`
- Create: `backend/src/grimoire/templates/world_atmosphere_system/default.j2`
- Create: `backend/src/grimoire/templates/world_atmosphere_user/default.j2`
- Test: `backend/tests/world/test_atmosphere.py` (new)

- [ ] **Step 1: Write failing test**

```python
# backend/tests/world/test_atmosphere.py
"""§3 LLM-driven atmosphere auto-generation."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from grimoire.world.atmosphere import generate_atmosphere


class _FakeGateway:
    def __init__(self, response_json: str) -> None:
        self.response_json = response_json
        self.calls: list[dict] = []

    async def complete(self, task, request, campaign_id=None, turn_id=None):
        self.calls.append({"task": task, "request": request})
        # CompletionResponse-shaped object — copy from a sibling test
        # for the real shape. Placeholder below.
        return type(
            "Resp", (), {"text": self.response_json, "usage": None}
        )()


async def test_generate_atmosphere_returns_parsed_dict() -> None:
    response = json.dumps({
        "default_register": "low-fantasy formal",
        "default_palette": "warm umber",
        "mood_tags": ["weary", "hopeful"],
    })
    gateway = _FakeGateway(response)
    out = await generate_atmosphere(
        gateway=gateway,
        world_id="w1",
        name="Karthos",
        tags=["fantasy", "medieval"],
        description="A weary kingdom on the edge of collapse.",
    )
    assert out["default_register"] == "low-fantasy formal"
    assert out["default_palette"] == "warm umber"
    assert out["mood_tags"] == ["weary", "hopeful"]
    assert gateway.calls[0]["task"] == "world_atmosphere"


async def test_generate_atmosphere_invalid_json_returns_empty() -> None:
    gateway = _FakeGateway("not json")
    out = await generate_atmosphere(
        gateway=gateway, world_id="w1", name="X", tags=[], description="",
    )
    assert out == {}


async def test_generate_atmosphere_passes_inputs_through_template() -> None:
    response = "{}"
    gateway = _FakeGateway(response)
    await generate_atmosphere(
        gateway=gateway, world_id="kar", name="Karthos",
        tags=["fantasy"], description="weary kingdom",
    )
    # CompletionRequest.messages should mention the inputs somewhere.
    req = gateway.calls[0]["request"]
    rendered = "\n".join(m.content for m in req.messages)
    assert "Karthos" in rendered
    assert "weary kingdom" in rendered
```

- [ ] **Step 2: Run, expect ImportError**

- [ ] **Step 3: Implement the generator**

```python
# backend/src/grimoire/world/atmosphere.py
"""LLM-driven atmosphere generation (§3 of world remaining-design)."""

from __future__ import annotations

import json
import logging
from typing import Any

from grimoire.templates import registry as template_registry
from grimoire.types.llm import CompletionRequest, Message

logger = logging.getLogger(__name__)


async def generate_atmosphere(
    *,
    gateway: Any,
    world_id: str,
    name: str,
    tags: list[str] | None = None,
    description: str = "",
    campaign_id: str | None = None,
) -> dict[str, Any]:
    """Ask the LLM gateway for a world atmosphere block.

    Returns the parsed JSON dict, or ``{}`` on any failure (malformed JSON,
    gateway error). Callers should treat ``{}`` as "leave atmosphere empty".
    """
    system_text = template_registry.render(
        "world_atmosphere_system", variant="default"
    )
    user_text = template_registry.render(
        "world_atmosphere_user",
        variant="default",
        world_id=world_id,
        name=name,
        tags=list(tags or []),
        description=description or "",
    )
    request = CompletionRequest(
        messages=[
            Message(role="system", content=system_text),
            Message(role="user", content=user_text),
        ],
    )
    try:
        response = await gateway.complete(
            "world_atmosphere", request, campaign_id=campaign_id, turn_id=None
        )
    except Exception:
        logger.warning("atmosphere generation failed: gateway error", exc_info=True)
        return {}

    try:
        parsed = json.loads(response.text or "")
    except (ValueError, AttributeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    # Coerce known fields to sane defaults.
    return {
        "default_register": str(parsed.get("default_register") or ""),
        "default_palette": str(parsed.get("default_palette") or ""),
        "mood_tags": [str(t) for t in (parsed.get("mood_tags") or [])],
    }
```

Templates:

```jinja
{# backend/src/grimoire/templates/world_atmosphere_system/default.j2 #}
You generate an "atmosphere" block for a fictional setting. Respond with
JSON only — no commentary. Schema:

  {
    "default_register": "<short prose register, e.g. 'low-fantasy formal'>",
    "default_palette": "<colour / mood palette, e.g. 'warm umber'>",
    "mood_tags": ["short", "evocative", "tags"]
  }
```

```jinja
{# backend/src/grimoire/templates/world_atmosphere_user/default.j2 #}
Setting: {{ name }}{% if tags %} ({{ tags | join(", ") }}){% endif %}
{% if description %}
Description:
{{ description }}
{% endif %}

Generate an atmosphere block for this setting. JSON only.
```

(Verify the template directory structure by checking an existing template like `extractor_system/default.j2`. The plan assumes the directory-per-template + variant-file convention.)

- [ ] **Step 4: Run tests, expect pass**

- [ ] **Step 5: Commit**

```
git commit -m "Add atmosphere LLM generator + templates (§3)"
```

### Task D2: Hook into `create_world`

**Files:**
- Modify: `backend/src/grimoire/world/service.py` (`__init__` accepts a `gateway`; `create_world` calls generator when frontmatter is empty and config flag is on)
- Test: `backend/tests/world/test_atmosphere_hook.py` (new)

- [ ] **Step 1: Add gateway kwarg to constructor**

```python
    def __init__(
        self,
        library: LibraryService,
        *,
        config: WorldConfig | None = None,
        gateway: Any = None,
    ) -> None:
        self.library = library
        self.store: StateStore = library.store
        self.config = config or WorldConfig()
        self.gateway = gateway
```

- [ ] **Step 2: Modify `create_world`**

```python
    async def create_world(self, world_id: str, meta: dict | None = None) -> WorldMeta:
        meta = dict(meta or {})
        # §3 atmosphere auto-generation
        if (
            self.config.atmosphere_auto_generate
            and self.gateway is not None
            and not (meta.get("atmosphere") or {})
        ):
            atmosphere = await generate_atmosphere(
                gateway=self.gateway,
                world_id=world_id,
                name=str(meta.get("name") or world_id),
                tags=list(meta.get("tags") or []),
                description=str(meta.get("description") or ""),
            )
            if atmosphere:
                meta["atmosphere"] = atmosphere
        return await self.library.create_world(world_id, meta)
```

(Add `from grimoire.world.atmosphere import generate_atmosphere` at the top.)

- [ ] **Step 3: Write test**

```python
# backend/tests/world/test_atmosphere_hook.py
"""§3 create_world auto-fills empty atmosphere when config flag is on."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

from grimoire.world import WorldConfig, WorldService


class _FakeGateway:
    async def complete(self, task, request, campaign_id=None, turn_id=None):
        return type(
            "Resp", (), {
                "text": json.dumps({
                    "default_register": "test register",
                    "default_palette": "test palette",
                    "mood_tags": ["a", "b"],
                }),
            }
        )()


async def test_create_world_fills_atmosphere_when_empty_and_flag_on(library) -> None:
    svc = WorldService(library, gateway=_FakeGateway())
    meta = await svc.create_world("w1", {"id": "w1", "name": "W1"})
    fm = (meta.model_dump() if hasattr(meta, "model_dump") else dict(meta)).get(
        "atmosphere"
    ) or {}
    assert fm.get("default_register") == "test register"


async def test_create_world_skips_when_flag_off(library) -> None:
    from grimoire.world.config import WorldConfig
    svc = WorldService(
        library, gateway=_FakeGateway(),
        config=WorldConfig(atmosphere_auto_generate=False),
    )
    meta = await svc.create_world("w1", {"id": "w1", "name": "W1"})
    atmosphere = (
        meta.model_dump() if hasattr(meta, "model_dump") else dict(meta)
    ).get("atmosphere") or {}
    assert atmosphere == {}


async def test_create_world_skips_when_atmosphere_already_set(library) -> None:
    svc = WorldService(library, gateway=_FakeGateway())
    meta = await svc.create_world(
        "w1", {"id": "w1", "name": "W1", "atmosphere": {"default_register": "preset"}}
    )
    atmosphere = (
        meta.model_dump() if hasattr(meta, "model_dump") else dict(meta)
    ).get("atmosphere") or {}
    assert atmosphere["default_register"] == "preset"


async def test_create_world_skips_when_no_gateway(library) -> None:
    svc = WorldService(library, gateway=None)
    meta = await svc.create_world("w1", {"id": "w1", "name": "W1"})
    atmosphere = (
        meta.model_dump() if hasattr(meta, "model_dump") else dict(meta)
    ).get("atmosphere") or {}
    assert atmosphere == {}
```

- [ ] **Step 4: Update existing world fixture**

The `world` fixture in `backend/tests/world/conftest.py` currently calls `WorldService(library)`. After this branch, that still works (gateway defaults to None, atmosphere generation skipped). No fixture change needed.

- [ ] **Step 5: Run all world tests**

- [ ] **Step 6: Wire gateway in `main.py`**

In `main.py`, find the `WorldService(...)` construction. After the LLM gateway is set up (it's already wired for orchestrator/extractor), pass it to WorldService:

```python
container.world = WorldService(
    container.library, config=world_cfg, gateway=llm_gateway,
)
```

- [ ] **Step 7: Smoke-test app boot**

```powershell
python -c "from grimoire.main import create_app; create_app()"
```

- [ ] **Step 8: Commit**

```
git commit -m "Hook atmosphere generation into create_world (§3)"
```

### Task D3: Merge branch D

Same as A5.

---

# Branch E — Composition-aware spatial queries (§7, option b)

**Working directory:** `.worktrees/world-E-spatial-composition`
**Depends on:** Branch A merged.
**Decision:** Option (b) — `LocationConnection.to` accepts entity refs; queries take refs + campaign_id and resolve through composition.

### Task E1: Type changes — accept entity refs

**Files:**
- Modify: `backend/src/grimoire/types/world.py` (`LocationConnection.to` may be a ref OR asset_id)
- Test: `backend/tests/world/test_location_ref_resolution.py` (new)

- [ ] **Step 1: Write failing test**

```python
# backend/tests/world/test_location_ref_resolution.py
"""§7 LocationConnection.to accepts entity refs across worlds."""

from __future__ import annotations

import pytest


async def _seed_two_worlds(store, library):
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    await library.create_world("w1", {"id": "w1", "name": "W1"})
    await library.create_world("w2", {"id": "w2", "name": "W2"})
    await library.create_entity(
        "w1", "location", "town",
        {
            "id": "town", "name": "Town", "kind": "city",
            "connections": [
                # Same-world asset-id connection (legacy form).
                {"to": "tavern", "via": "street", "duration_min": 5},
                # Cross-world entity-ref connection (new form).
                {"to": "library:worlds/w2/locations/portal", "via": "portal", "duration_min": 1},
            ],
        },
        body="",
    )
    await library.create_entity(
        "w1", "location", "tavern",
        {"id": "tavern", "name": "Tavern", "kind": "building"}, body="",
    )
    await library.create_entity(
        "w2", "location", "portal",
        {"id": "portal", "name": "Portal", "kind": "other"}, body="",
    )
    for wid in ("w1", "w2"):
        await store.upsert_world_ref(
            campaign_id="camp-1", world_id=wid, priority=1,
            include=None, track_latest=True, bound_at_version=None,
        )


async def test_adjacent_locations_resolves_same_world_asset_id(
    store, library, world,
) -> None:
    await _seed_two_worlds(store, library)
    out = await world.adjacent_locations(
        "library:worlds/w1/locations/town", campaign_id="camp-1"
    )
    ids = {l.id for l in out}
    assert "tavern" in ids


async def test_adjacent_locations_resolves_cross_world_ref(
    store, library, world,
) -> None:
    await _seed_two_worlds(store, library)
    out = await world.adjacent_locations(
        "library:worlds/w1/locations/town", campaign_id="camp-1"
    )
    ids = {l.id for l in out}
    assert "portal" in ids  # cross-world ref resolves


async def test_path_between_works_with_refs(store, library, world) -> None:
    await _seed_two_worlds(store, library)
    path = await world.path_between(
        "library:worlds/w1/locations/town",
        "library:worlds/w1/locations/tavern",
        campaign_id="camp-1",
    )
    assert len(path) == 1
    assert path[0].to in ("tavern", "library:worlds/w1/locations/tavern")
```

- [ ] **Step 2: Run, expect fail (signature mismatch / no resolution)**

- [ ] **Step 3: Implement ref-aware spatial queries**

Add a module-level helper in `world/service.py`:

```python
def _is_entity_ref(value: str) -> bool:
    return isinstance(value, str) and value.startswith("library:worlds/")


def _parse_location_ref(ref: str) -> tuple[str, str] | None:
    """Parse 'library:worlds/<world_id>/locations/<asset_id>' → (world_id, asset_id)."""
    parts = ref.split("/")
    try:
        world_idx = parts.index("worlds")
        loc_idx = parts.index("locations")
    except ValueError:
        return None
    try:
        return parts[world_idx + 1], parts[loc_idx + 1]
    except IndexError:
        return None
```

Replace the spatial methods to take refs:

```python
    async def adjacent_locations(
        self,
        location_ref: str,
        campaign_id: CampaignId,
    ) -> list[Location]:
        """Locations connected to ``location_ref`` within the campaign's composition.

        Accepts a full entity ref ('library:worlds/<world>/locations/<asset>'.
        Connections whose ``to`` is a bare asset_id are resolved against the
        same world; connections whose ``to`` is an entity ref are resolved
        through the campaign's composition cascade.
        """
        parsed = _parse_location_ref(location_ref)
        if parsed is None:
            return []
        world_id, asset_id = parsed
        try:
            center = await self.get_location(world_id, asset_id)
        except WorldNotFoundError:
            return []

        out: list[Location] = []
        seen: set[str] = set()

        if center.parent_id and center.parent_id not in seen:
            try:
                parent = await self.get_location(world_id, center.parent_id)
                out.append(parent)
                seen.add(parent.id)
            except WorldNotFoundError:
                pass
        for conn in center.connections:
            if conn.to in seen:
                continue
            resolved = await self._resolve_connection_target(
                conn.to, source_world=world_id, campaign_id=campaign_id,
            )
            if resolved is None:
                continue
            if resolved.id not in seen:
                out.append(resolved)
                seen.add(resolved.id)
        return out

    async def _resolve_connection_target(
        self,
        target: str,
        *,
        source_world: str,
        campaign_id: CampaignId,
    ) -> Location | None:
        """Return the :class:`Location` ``target`` points to, or ``None``.

        ``target`` may be a bare asset_id (resolves against ``source_world``)
        or an entity ref ``library:worlds/<wid>/locations/<aid>`` (resolves
        through the campaign's composition).
        """
        if _is_entity_ref(target):
            parsed = _parse_location_ref(target)
            if parsed is None:
                return None
            wid, aid = parsed
            # Confirm the target world is part of the campaign's composition.
            comp = await self.library.get_composition(campaign_id)
            if not any(ref.world_id == wid for ref in comp.worlds):
                return None
            try:
                return await self.get_location(wid, aid)
            except WorldNotFoundError:
                return None
        try:
            return await self.get_location(source_world, target)
        except WorldNotFoundError:
            return None
```

For `path_between` and `locations_within`, similar reshape. The plan: have them take entity refs plus `campaign_id`, accept the same composition-bounded behaviour.

For `path_between`:

```python
    async def path_between(
        self,
        src_ref: str,
        dst_ref: str,
        campaign_id: CampaignId,
    ) -> list[LocationConnection]:
        if src_ref == dst_ref:
            return []
        # Build the cross-world graph: for each world in the composition,
        # load its locations; the connection set spans worlds via ref-form
        # 'to' values.
        comp = await self.library.get_composition(campaign_id)
        all_locs: dict[str, tuple[str, Location]] = {}
        for ref in comp.worlds:
            for loc in await self.list_locations(ref.world_id):
                full_ref = _location_ref(ref.world_id, loc.id)
                all_locs[full_ref] = (ref.world_id, loc)

        if src_ref not in all_locs or dst_ref not in all_locs:
            return []

        prev: dict[str, tuple[str, LocationConnection]] = {}
        from collections import deque
        frontier: deque[str] = deque([src_ref])
        visited: set[str] = {src_ref}
        while frontier:
            cur_ref = frontier.popleft()
            if cur_ref == dst_ref:
                break
            cur_world, cur_loc = all_locs[cur_ref]
            for conn in cur_loc.connections:
                neighbor_ref = (
                    conn.to if _is_entity_ref(conn.to)
                    else _location_ref(cur_world, conn.to)
                )
                if neighbor_ref in visited or neighbor_ref not in all_locs:
                    continue
                visited.add(neighbor_ref)
                prev[neighbor_ref] = (cur_ref, conn)
                frontier.append(neighbor_ref)

        if dst_ref not in prev:
            return []
        path: list[LocationConnection] = []
        cursor = dst_ref
        while cursor in prev:
            parent, conn = prev[cursor]
            path.append(conn)
            cursor = parent
        path.reverse()
        return path
```

For `locations_within`:

```python
    async def locations_within(
        self,
        parent_ref: str,
        campaign_id: CampaignId,
        depth: int = 1,
    ) -> list[Location]:
        parsed = _parse_location_ref(parent_ref)
        if parsed is None:
            return []
        parent_world, parent_asset = parsed

        # Walk locations from every world in the composition; children
        # are matched by parent_id within the same world (no cross-world
        # parenting today).
        out: list[Location] = []
        comp = await self.library.get_composition(campaign_id)
        all_locs_by_world: dict[str, list[Location]] = {}
        for ref in comp.worlds:
            all_locs_by_world[ref.world_id] = await self.list_locations(ref.world_id)

        by_parent: dict[tuple[str, str | None], list[Location]] = {}
        for wid, locs in all_locs_by_world.items():
            for loc in locs:
                by_parent.setdefault((wid, loc.parent_id), []).append(loc)

        frontier: list[tuple[str, Location, int]] = [
            (parent_world, child, 1)
            for child in by_parent.get((parent_world, parent_asset), [])
        ]
        while frontier:
            wid, loc, level = frontier.pop(0)
            out.append(loc)
            if level < depth:
                frontier.extend(
                    (wid, c, level + 1) for c in by_parent.get((wid, loc.id), [])
                )
        return out
```

- [ ] **Step 4: Run all spatial tests, expect pass**

- [ ] **Step 5: Update callers**

Search for callers of `adjacent_locations` / `path_between` / `locations_within` and update them to pass entity refs + campaign_id. Use Grep:

```
grep -rn "adjacent_locations\|path_between\|locations_within" backend/src backend/tests
```

Update each call site.

- [ ] **Step 6: Update existing world tests that use the old signature**

The existing `test_service.py` likely has tests using `(world_id, location_id)` arg style. Update them to use the new `(location_ref, campaign_id)` form.

- [ ] **Step 7: Commit**

```
git commit -m "Make spatial queries composition-aware (§7 option b)"
```

### Task E2: Merge branch E

Same as A5.

---

# Branch F — Greeting handoff (§8)

**Working directory:** `.worktrees/world-F-greeting-handoff`
**Depends on:** Branch A merged.

### Task F1: `seed_scene_from_greeting`

**Files:**
- Modify: `backend/src/grimoire/world/service.py` (add `seed_scene_from_greeting`)
- Test: `backend/tests/world/test_greeting_handoff.py` (new)

- [ ] **Step 1: Inspect existing Greeting type**

Read `backend/src/grimoire/types/composition.py` to see Greeting's fields. The plan assumes a Greeting has at minimum: `id`, `world_id`, `name`/`title`, `location_ref` (or `location_id`), `in_game_start` (or similar), `present_character_refs`, `narration` (opening prose). Confirm from the file.

- [ ] **Step 2: Write failing test**

```python
# backend/tests/world/test_greeting_handoff.py
"""§8 Greeting hands off to SceneManager for scene-1 seeding."""

from __future__ import annotations

from unittest.mock import AsyncMock


class _FakeSceneManager:
    def __init__(self) -> None:
        self.calls: list = []
        self.scene = type("Scene", (), {"id": "scene-1"})()

    async def start_scene(self, init):
        self.calls.append(init)
        return self.scene


async def _seed_world_with_greeting(library):
    await library.create_world("w1", {"id": "w1", "name": "W1"})
    await library.create_entity(
        "w1", "location", "town",
        {"id": "town", "name": "Town"}, body="",
    )
    await library.create_entity(
        "w1", "greeting", "intro",
        {
            "id": "intro",
            "name": "Intro",
            "location_ref": "library:worlds/w1/locations/town",
            "present_character_refs": ["library:characters/alice"],
            "narration": "You wake at dawn.",
        },
        body="You wake at dawn in the town square.",
    )


async def test_seed_scene_from_greeting_calls_start_scene(store, library, world) -> None:
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    await _seed_world_with_greeting(library)
    sm = _FakeSceneManager()
    scene = await world.seed_scene_from_greeting(
        campaign_id="camp-1", greeting_id="intro", world_id="w1",
        scene_manager=sm,
    )
    assert scene.id == "scene-1"
    assert len(sm.calls) == 1
    init = sm.calls[0]
    # Whatever SceneInit fields exist, they should carry greeting info:
    assert getattr(init, "greeting_id", None) == "intro"


async def test_seed_scene_unknown_greeting_raises(store, library, world) -> None:
    import pytest
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    await _seed_world_with_greeting(library)
    with pytest.raises(Exception):  # LibraryNotFoundError or similar
        await world.seed_scene_from_greeting(
            campaign_id="camp-1", greeting_id="missing", world_id="w1",
            scene_manager=_FakeSceneManager(),
        )
```

- [ ] **Step 3: Implement method**

```python
    async def seed_scene_from_greeting(
        self,
        *,
        campaign_id: CampaignId,
        greeting_id: str,
        world_id: str,
        scene_manager: Any,
        branch_id: str | None = None,
    ) -> Any:
        """§8 Build a SceneInit from a Greeting and create scene 1.

        Returns the resulting :class:`Scene`. The caller (typically the
        campaign-creation REST handler) is responsible for any follow-up
        (opening-narration LLM call, first-post append).
        """
        from grimoire.scenes.types import SceneInit  # late import — Scenes is a sibling module

        greeting = await self.library.get_greeting(world_id, greeting_id)
        fm = greeting.model_dump() if hasattr(greeting, "model_dump") else dict(greeting)
        branch = branch_id or f"{campaign_id}:main"
        init = SceneInit(
            campaign_id=campaign_id,
            branch_id=branch,
            greeting_id=greeting_id,
            title=str(fm.get("name") or fm.get("title") or "Scene 1"),
            location_ref=fm.get("location_ref"),
            in_game_start=fm.get("in_game_start"),
            pov_character_ref=fm.get("pov_character_ref"),
            present_character_refs=list(fm.get("present_character_refs") or []),
            present_pc_refs=list(fm.get("present_pc_refs") or []),
            mood=str(fm.get("mood") or ""),
            tags=list(fm.get("tags") or []),
        )
        return await scene_manager.start_scene(init)
```

(Use only the SceneInit fields that exist in `scenes/types.py`. If a field doesn't exist, drop it from the kwargs.)

- [ ] **Step 4: Run tests, expect pass**

- [ ] **Step 5: Commit**

```
git commit -m "Add WorldService.seed_scene_from_greeting (§8)"
```

### Task F2: Wire into campaign-creation REST handler

**Files:**
- Modify: `backend/src/grimoire/api/campaigns.py` (`create_campaign` route)
- Test: `backend/tests/api/test_campaign_greeting.py` (new — only if test infra exists; otherwise verify by hand)

- [ ] **Step 1: Update `create_campaign` to call the handoff**

In `api/campaigns.py`, after `state_store.upsert_campaign(...)` succeeds, if `payload.greeting_id` is set, call:

```python
    if payload.greeting_id and container.world is not None and container.scenes is not None:
        try:
            # Resolve the world that owns this greeting. If multiple worlds are
            # in the composition, use the highest-priority one — same convention
            # as calendar_for_campaign.
            composition = (
                payload.composition or CompositionPayload()
            )
            world_refs = sorted(composition.worlds, key=lambda r: r.priority)
            if world_refs:
                await container.world.seed_scene_from_greeting(
                    campaign_id=payload.id,
                    greeting_id=payload.greeting_id,
                    world_id=world_refs[0].world_id,
                    scene_manager=container.scenes,
                )
        except Exception:
            logger.warning("greeting handoff failed", exc_info=True)
```

(Adjust to the actual structure of the existing handler. The container reference may need to come via `request.app.state.container` or a dependency.)

- [ ] **Step 2: Test by booting the app and exercising the POST**

A full integration test is heavyweight; instead, lint + smoke-boot:

```powershell
python -c "from grimoire.main import create_app; create_app()"
```

- [ ] **Step 3: Commit**

```
git commit -m "Wire greeting handoff into create_campaign route (§8)"
```

### Task F3: Merge branch F

Same as A5.

---

# Branch G — Emergent location generation (§9)

**Working directory:** `.worktrees/world-G-emergent-location`
**Depends on:** Branch A merged. Coordinates with Branch C's dispatcher pattern but doesn't strictly require it merged first (would just rebase if a conflict arises in `_apply_routing`).

### Task G1: Detection rule + LLM task

**Files:**
- Modify: `backend/src/grimoire/extractor/rule_based.py` (add `detect_unresolved_location`)
- Create: `backend/src/grimoire/templates/world_location_generate_system/default.j2`
- Create: `backend/src/grimoire/templates/world_location_generate_user/default.j2`
- Create: `backend/src/grimoire/world/location_generator.py`
- Test: `backend/tests/world/test_emergent_location.py` (new)

- [ ] **Step 1: Detection rule**

Add to `rule_based.py` — detect patterns like "I enter the X" or "they walk into the Y" where X/Y is a noun phrase. Emit a low-confidence delta (force review):

```python
_ENTERING_LOCATION = re.compile(
    r"\b(?:enter|enters|enter\s+the|walks?\s+into|step\s+into|"
    r"arrives?\s+at)\s+(?:the\s+)?([a-z][a-z\s']{2,40})\b",
    re.I,
)


def detect_unresolved_location(
    text: str,
    *,
    campaign_id: str,
    branch_id: str,
    known_location_names: set[str] | None = None,
) -> Iterable[StateDelta]:
    if not text:
        return
    seen: set[str] = set()
    for match in _ENTERING_LOCATION.finditer(text):
        phrase = match.group(1).strip().lower()
        if phrase in seen:
            continue
        if known_location_names and phrase in known_location_names:
            continue
        seen.add(phrase)
        yield StateDelta(
            kind=DeltaKind.EMERGENT_CREATE,
            target_scope="campaign-file",
            target_table="emergent",
            target_id=f"emergent/location/{_slug(phrase)}",
            after={
                "campaign_id": campaign_id,
                "branch_id": branch_id,
                "kind": "location",
                "name": phrase,
                "evidence": match.group(0),
            },
            confidence=0.4,  # below auto-apply threshold — forces review
            source="extractor:rule_based:emergent_location",
            evidence=text[:200],
        )


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]
```

- [ ] **Step 2: LLM-driven location generation**

```python
# backend/src/grimoire/world/location_generator.py
"""LLM-driven Location frontmatter generation (§9)."""

from __future__ import annotations

import json
import logging
from typing import Any

from grimoire.templates import registry as template_registry
from grimoire.types.llm import CompletionRequest, Message

logger = logging.getLogger(__name__)


async def generate_location_frontmatter(
    *,
    gateway: Any,
    name: str,
    context: str = "",
    campaign_id: str | None = None,
) -> dict[str, Any]:
    """Ask the LLM gateway for a Location frontmatter dict.

    Returns ``{}`` on any failure.
    """
    sys_text = template_registry.render(
        "world_location_generate_system", variant="default"
    )
    user_text = template_registry.render(
        "world_location_generate_user", variant="default",
        name=name, context=context,
    )
    request = CompletionRequest(
        messages=[
            Message(role="system", content=sys_text),
            Message(role="user", content=user_text),
        ],
    )
    try:
        response = await gateway.complete(
            "world_location_generate", request,
            campaign_id=campaign_id, turn_id=None,
        )
    except Exception:
        logger.warning("emergent location generation failed: gateway error", exc_info=True)
        return {}
    try:
        parsed = json.loads(response.text or "")
    except (ValueError, AttributeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed
```

Templates: `world_location_generate_system/default.j2` and `world_location_generate_user/default.j2` — prompt the model to return a Location-schema JSON.

```jinja
{# world_location_generate_system/default.j2 #}
You generate a Location entry for a campaign. Respond with JSON only.
Schema (all fields optional except name and kind):
  {
    "id": "<slug>",
    "name": "<display name>",
    "kind": "city|building|room|region|outdoor|other",
    "description": "<one paragraph>",
    "tags": ["..."],
    "aliases": ["..."],
    "indoor": <bool>,
    "climate_zone": "<optional climate hint>",
    "typical_occupants": ["..."]
  }
```

```jinja
{# world_location_generate_user/default.j2 #}
Generate a Location entry for "{{ name }}".
{% if context %}
Context from the narrative:
{{ context }}
{% endif %}
Return JSON only.
```

- [ ] **Step 3: Test the generator**

```python
# backend/tests/world/test_emergent_location.py
"""§9 LLM-driven location generator + emergent write."""

from __future__ import annotations

import json

from grimoire.world.location_generator import generate_location_frontmatter


class _FakeGateway:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.calls: list = []

    async def complete(self, task, request, campaign_id=None, turn_id=None):
        self.calls.append({"task": task, "request": request})
        return type("Resp", (), {"text": self.response_text})()


async def test_generator_returns_parsed_dict() -> None:
    gw = _FakeGateway(json.dumps({"id": "tavern", "name": "Old Tavern", "kind": "building"}))
    out = await generate_location_frontmatter(gateway=gw, name="tavern")
    assert out["name"] == "Old Tavern"
    assert gw.calls[0]["task"] == "world_location_generate"


async def test_generator_returns_empty_on_bad_json() -> None:
    gw = _FakeGateway("not json")
    out = await generate_location_frontmatter(gateway=gw, name="x")
    assert out == {}
```

- [ ] **Step 4: Apply-delta path: `WorldService.apply_emergent_location_delta`**

In `world/service.py`:

```python
    async def apply_emergent_location_delta(
        self,
        delta: Any,
        *,
        turn_id: str | None = None,
    ) -> Path:
        """§9 Materialize an emergent-location delta to disk + review queue."""
        from grimoire.types.state import DeltaKind

        if getattr(delta, "kind", None) != DeltaKind.EMERGENT_CREATE:
            raise ValueError("expected EMERGENT_CREATE delta")
        after = getattr(delta, "after", None) or {}
        if (after.get("kind") or "") != "location":
            raise ValueError("expected after.kind='location'")
        campaign_id = after.get("campaign_id") or ""
        name = after.get("name") or ""
        entity_id = name.replace(" ", "-").lower()[:40] or "emergent-location"

        frontmatter: dict[str, Any] = {}
        if self.gateway is not None:
            frontmatter = await generate_location_frontmatter(
                gateway=self.gateway,
                name=name,
                context=after.get("evidence") or "",
                campaign_id=campaign_id,
            )
        # Ensure minimum shape so the file is parseable.
        frontmatter.setdefault("id", entity_id)
        frontmatter.setdefault("name", name)
        frontmatter.setdefault("kind", "other")

        path = await self.store.write_emergent(
            campaign_id=campaign_id,
            kind="location",
            entity_id=entity_id,
            frontmatter=frontmatter,
            body=str(frontmatter.get("description") or ""),
            source=delta.source or "extractor",
            turn_id=turn_id,
        )
        return path
```

Add `from grimoire.world.location_generator import generate_location_frontmatter` at top.

- [ ] **Step 5: Test apply_emergent_location_delta**

Append to `test_emergent_location.py`:

```python
async def test_apply_emergent_location_writes_to_disk(store, library, world) -> None:
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    world.gateway = _FakeGateway(json.dumps({"name": "Old Tavern", "kind": "building"}))
    from grimoire.types.state import DeltaKind, StateDelta
    delta = StateDelta(
        kind=DeltaKind.EMERGENT_CREATE,
        target_scope="campaign-file",
        target_table="emergent",
        target_id="emergent/location/tavern",
        after={
            "campaign_id": "camp-1",
            "branch_id": "camp-1:main",
            "kind": "location",
            "name": "tavern",
            "evidence": "they entered the tavern",
        },
        confidence=0.4,
        source="extractor",
    )
    path = await world.apply_emergent_location_delta(delta)
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "Old Tavern" in text
```

- [ ] **Step 6: Optional: extend orchestrator dispatcher**

Same pattern as branch C — recognize EMERGENT_CREATE deltas with `after.kind=="location"` and dispatch to `world.apply_emergent_location_delta`. Because of the low confidence (0.4), most of these will route to review rather than auto-apply; only after user approval should we materialize. For v1, route through the review queue and document that approving the review item triggers the materialization.

The simpler v1: don't auto-apply at all; let the review-approval handler call `apply_emergent_location_delta`. Document this as a TODO in the commit.

- [ ] **Step 7: Run, commit, merge**

```
git commit -m "Add procedural emergent-location pipeline (§9)"
```

### Task G2: Merge branch G

Same as A5.

---

# Branch H — LocationState API + faction-state delta logging (§10 + §11)

**Working directory:** `.worktrees/world-H-location-state`
**Depends on:** Branch A merged.

### Task H1: `get_location_state` / `update_location_state` via `apply_delta`

**Files:**
- Modify: `backend/src/grimoire/world/service.py`
- Test: `backend/tests/world/test_location_state.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/world/test_location_state.py
"""§10 Full LocationState get/update via apply_delta."""

from __future__ import annotations


async def _seed(store, library):
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    await library.create_world("w1", {"id": "w1", "name": "W1"})
    await library.create_entity(
        "w1", "location", "town",
        {"id": "town", "name": "Town", "kind": "city"}, body="",
    )
    await store.upsert_world_ref(
        campaign_id="camp-1", world_id="w1", priority=1,
        include=None, track_latest=True, bound_at_version=None,
    )


async def test_get_location_state_empty_returns_default(store, library, world) -> None:
    await _seed(store, library)
    state = await world.get_location_state(
        "library:worlds/w1/locations/town", campaign_id="camp-1",
    )
    assert state.condition == ""
    assert state.occupants == []


async def test_update_location_state_round_trips(store, library, world) -> None:
    await _seed(store, library)
    updated = await world.update_location_state(
        "library:worlds/w1/locations/town",
        campaign_id="camp-1",
        patch={"condition": "ransacked", "transient_features": ["broken table"]},
        source="user",
        turn_id="t1",
    )
    assert updated.condition == "ransacked"
    assert updated.transient_features == ["broken table"]
    re_read = await world.get_location_state(
        "library:worlds/w1/locations/town", campaign_id="camp-1",
    )
    assert re_read.condition == "ransacked"


async def test_update_location_state_records_delta(store, library, world) -> None:
    await _seed(store, library)
    await world.update_location_state(
        "library:worlds/w1/locations/town",
        campaign_id="camp-1",
        patch={"condition": "burning"},
        source="user", turn_id="t1",
    )
    rows = await store.db.fetchall(
        "SELECT * FROM deltas WHERE kind = 'location_state_update' "
        "AND campaign_id = ?",
        ("camp-1",),
    )
    assert len(rows) >= 1
```

- [ ] **Step 2: Run, expect fail**

- [ ] **Step 3: Implement methods**

```python
    async def get_location_state(
        self,
        location_ref: str,
        campaign_id: CampaignId,
        *,
        branch_id: str | None = None,
    ) -> LocationStateData:
        from grimoire.types.world import Weather, LocationStateData
        branch = branch_id or f"{campaign_id}:main"
        row = await self.store.db.fetchone(
            "SELECT * FROM location_state WHERE location_ref = ? AND branch_id = ?",
            (location_ref, branch),
        )
        if row is None:
            return LocationStateData(
                location_ref=location_ref,
                campaign_id=campaign_id,
                branch_id=branch,
            )
        weather: Weather | None = None
        if row["weather"]:
            try:
                weather = Weather.model_validate(json.loads(row["weather"]))
            except Exception:
                weather = None
        return LocationStateData(
            location_ref=location_ref,
            campaign_id=campaign_id,
            branch_id=branch,
            weather=weather,
            time_of_day=row["time_of_day"] or "",
            occupants=[
                o for o in (json.loads(row["occupants"]) if row["occupants"] else [])
                if isinstance(o, str)
            ],
            condition=row["condition"] or "",
            transient_features=[
                t for t in (json.loads(row["transient_features"]) if row["transient_features"] else [])
                if isinstance(t, str)
            ],
            updated_at_turn=row["updated_at_turn"],
        )

    async def update_location_state(
        self,
        location_ref: str,
        campaign_id: CampaignId,
        patch: dict,
        *,
        branch_id: str | None = None,
        source: str = "user",
        turn_id: str | None = None,
    ) -> LocationStateData:
        from grimoire.types.state import DeltaKind, StateDelta
        branch = branch_id or f"{campaign_id}:main"
        current = await self.get_location_state(location_ref, campaign_id, branch_id=branch)
        merged = current.model_dump()
        for k, v in (patch or {}).items():
            merged[k] = v

        # Build a row-shaped after payload for apply_delta.
        weather_json = (
            json.dumps(merged.get("weather"), default=str)
            if merged.get("weather") else None
        )
        after = {
            "location_ref": location_ref,
            "campaign_id": campaign_id,
            "branch_id": branch,
            "weather": weather_json,
            "time_of_day": merged.get("time_of_day") or "",
            "occupants": json.dumps(merged.get("occupants") or []),
            "condition": merged.get("condition") or "",
            "transient_features": json.dumps(merged.get("transient_features") or []),
            "updated_at_turn": turn_id or merged.get("updated_at_turn"),
        }
        delta = StateDelta(
            kind=DeltaKind.LOCATION_STATE_UPDATE,
            target_scope="campaign-sqlite",
            target_table="location_state",
            target_id=location_ref,
            after=after,
            confidence=1.0,
            source=source,
        )
        await self.store.apply_delta(
            delta=delta, source=source, turn_id=turn_id,
            branch_id=branch, campaign_id=campaign_id,
        )
        return await self.get_location_state(
            location_ref, campaign_id, branch_id=branch
        )
```

- [ ] **Step 4: Run, expect pass**

- [ ] **Step 5: Commit**

```
git commit -m "Add LocationState get/update via apply_delta (§10)"
```

### Task H2: Route `update_faction_state` through `apply_delta`

**Files:**
- Modify: `backend/src/grimoire/world/service.py` (`update_faction_state`)
- Test: extend `backend/tests/world/test_faction_state.py` or add new

- [ ] **Step 1: Write test**

```python
# backend/tests/world/test_faction_state_delta.py
"""§11 update_faction_state records a delta."""

from __future__ import annotations


async def test_update_faction_state_records_delta(store, library, world) -> None:
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    await library.create_world("w1", {"id": "w1", "name": "W1"})
    await library.create_entity(
        "w1", "faction", "guild",
        {"id": "guild", "name": "Guild"}, body="",
    )
    await world.update_faction_state(
        faction_ref="library:worlds/w1/factions/guild",
        campaign_id="camp-1",
        patch={"current_focus": "recruiting"},
        source="user", turn_id="t1",
    )
    rows = await store.db.fetchall(
        "SELECT * FROM deltas WHERE kind = 'faction_state_update' AND campaign_id = ?",
        ("camp-1",),
    )
    assert len(rows) >= 1


async def test_update_faction_state_round_trips(store, library, world) -> None:
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    await library.create_world("w1", {"id": "w1", "name": "W1"})
    await library.create_entity(
        "w1", "faction", "guild",
        {"id": "guild", "name": "Guild"}, body="",
    )
    await world.update_faction_state(
        faction_ref="library:worlds/w1/factions/guild",
        campaign_id="camp-1",
        patch={"current_focus": "recruiting"},
        source="user", turn_id="t1",
    )
    state = await world.faction_state(
        faction_ref="library:worlds/w1/factions/guild", campaign_id="camp-1",
    )
    assert state.current_focus == "recruiting"
```

- [ ] **Step 2: Reroute through `apply_delta`**

Replace `update_faction_state`:

```python
    async def update_faction_state(
        self,
        faction_ref: str,
        campaign_id: CampaignId,
        patch: dict,
        *,
        branch_id: str | None = None,
        source: str = "user",
        turn_id: str | None = None,
    ) -> FactionStateData:
        from grimoire.types.state import DeltaKind, StateDelta
        branch = branch_id or f"{campaign_id}:main"
        existing = await self.faction_state(faction_ref, campaign_id, branch_id=branch)
        merged = existing.model_dump()
        for k, v in (patch or {}).items():
            if k == "goals" and isinstance(v, list):
                merged["goals"] = [g if isinstance(g, dict) else g.model_dump() for g in v]
            else:
                merged[k] = v
        payload = {
            "goals": merged.get("goals") or [],
            "resources": merged.get("resources") or {},
            "current_focus": merged.get("current_focus") or "",
            "public_perception": merged.get("public_perception") or "",
            "secrets": merged.get("secrets") or [],
        }
        after = {
            "faction_ref": faction_ref,
            "campaign_id": campaign_id,
            "branch_id": branch,
            "state": json.dumps(payload, sort_keys=True, default=str),
            "updated_at_turn": turn_id or _now_iso(),
        }
        delta = StateDelta(
            kind=DeltaKind.FACTION_STATE_UPDATE,
            target_scope="campaign-sqlite",
            target_table="faction_state",
            target_id=faction_ref,
            after=after,
            confidence=1.0,
            source=source,
        )
        await self.store.apply_delta(
            delta=delta, source=source, turn_id=turn_id,
            branch_id=branch, campaign_id=campaign_id,
        )
        return await self.faction_state(faction_ref, campaign_id, branch_id=branch)
```

- [ ] **Step 3: Run, expect pass**

- [ ] **Step 4: Commit**

```
git commit -m "Route update_faction_state through apply_delta (§11)"
```

### Task H3: Merge branch H

Same as A5.

---

# Final integration

### Task FINAL1: Verify all merges land cleanly

- [ ] **Step 1: From repo root, full test pass**

```powershell
git checkout main
cd backend
/c/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest tests/world tests/extractor tests/api tests/scenes -q
```

- [ ] **Step 2: Smoke-test app boot**

```powershell
python -c "from grimoire.main import create_app; create_app(); print('ok')"
```

- [ ] **Step 3: Remove worktrees**

```powershell
git worktree remove .worktrees/world-A-config
git worktree remove .worktrees/world-B-lore
git worktree remove .worktrees/world-C-weather-extractor
git worktree remove .worktrees/world-D-atmosphere
git worktree remove .worktrees/world-E-spatial-composition
git worktree remove .worktrees/world-F-greeting-handoff
git worktree remove .worktrees/world-G-emergent-location
git worktree remove .worktrees/world-H-location-state
```

### Task FINAL2: Mark spec complete

```powershell
git mv docs/superpowers/specs/2026-05-17-world-remaining-design.md docs/superpowers/specs/2026-05-17-world-COMPLETED.md
git commit -m "Mark world remaining-design spec complete"
```

---

# Self-review checklist

**Spec coverage (§1–§11 of `2026-05-17-world-remaining-design.md`):**

- §1 WorldConfig → Branch A Tasks A1–A4
- §2 Lore secrecy filtering → Branch B Task B1
- §3 Atmosphere generation → Branch D Tasks D1–D2
- §4 FTS-backed search_lore → Branch B Task B2
- §5 Weather override via Extractor → Branch C Tasks C1–C3
- §6 Multi-world calendar policy → Branch A Task A3
- §7 Composition-aware spatial queries (option b) → Branch E Task E1
- §8 Greeting handoff → Branch F Tasks F1–F2
- §9 Procedural location generation → Branch G Task G1
- §10 Full LocationState API → Branch H Task H1
- §11 Faction-state delta logging → Branch H Task H2

**Known assumptions / risks:**

- **StateDelta dataclass shape**: I assume fields `kind`, `target_scope`, `target_table`, `target_id`, `after`, `confidence`, `source`, `evidence`. If the actual dataclass uses different names (e.g., `entity_id` vs `target_id`), the code in branches C, G, H must adapt. Confirm by reading `backend/src/grimoire/types/state.py` before starting branch C.
- **SceneInit fields** (branch F): Use only fields that exist in `backend/src/grimoire/scenes/types.py`. Drop the ones that don't.
- **Greeting frontmatter fields** (branch F): The plan assumes `location_ref`, `in_game_start`, `present_character_refs`, `narration`. Confirm from `types/composition.py` and adapt.
- **CompletionRequest / Message shape** (branches D, G): Assumed shape: `CompletionRequest(messages=[Message(role=..., content=...)])`. Confirm from `types/llm.py`.
- **Orchestrator constructor** (branch C): The plan adds a `world` parameter. If `OrchestratorService.__init__` is a large kwarg-only function, threading is straightforward; if not, may need a small refactor.
- **Branch G v1 limitation**: Emergent location generation currently flows through the review queue, not auto-apply. The reviewer-approval handler must call `world.apply_emergent_location_delta(delta)` — this hook is documented in the commit but the actual UI/REST surface for review approval is out of scope.

**Branch dependency graph:**

```
A (config + calendar policy) ─┬─► B (lore upgrades)
                              ├─► C (weather extractor)
                              ├─► D (atmosphere)
                              ├─► E (spatial composition)
                              ├─► F (greeting handoff)
                              ├─► G (emergent location)
                              └─► H (LocationState + faction delta)
```

B–H can be developed concurrently after A merges. Merge order: A first; B–H in any order with rebase before each merge.
