# PC Profile Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich PCs with campaign-scoped profiles (description, goals, player notes) stored as markdown files, merged with library character data and mechanical capabilities in card rendering.

**Architecture:** A new `PCProfile` Pydantic model and supporting service methods read/write profile markdown files under `data/campaigns/{campaign_id}/characters/{character_id}/profile.md`. The card renderer merges profile + library character + capabilities into an enriched PC card. New API routes expose CRUD + revision history. The frontend wizard gains profile form fields, and a mid-campaign edit form is added to the campaign API client.

**Tech Stack:** Python/Pydantic (backend types), FastAPI (routes), existing `frontmatter.py` (markdown I/O), React/TypeScript (frontend)

---

## File Structure

### New files

| File | Responsibility |
|------|----------------|
| `backend/src/grimoire/characters/pc_profile.py` | `PCProfile` model, `PCProfileRevision` model, read/write/list-revisions helpers |
| `backend/tests/characters/test_pc_profile.py` | Tests for profile I/O, revision snapshotting, merge rendering |
| `frontend/src/api/campaign/pcProfile.ts` | Frontend API client for profile CRUD |
| `frontend/src/routes/CampaignCreate/PCProfileFields.tsx` | Reusable profile form fields component |

### Modified files

| File | Change |
|------|--------|
| `backend/src/grimoire/state_store/paths.py` | New `pc_profile_path()` and `pc_profile_revisions_dir()` helpers |
| `backend/src/grimoire/characters/views.py` | New `render_full_pc()` function that merges profile + capabilities into the rendered card |
| `backend/src/grimoire/characters/service.py` | `get_full_card()` calls `render_full_pc()` for PCs; new `get_pc_profile()`, `save_pc_profile()`, `list_pc_profile_revisions()`, `get_pc_profile_revision()` methods |
| `backend/src/grimoire/api/campaigns/pcs.py` | New profile CRUD routes |
| `backend/src/grimoire/api/campaigns/schemas.py` | New `PCProfilePayload` schema |
| `frontend/src/routes/CampaignCreate/types.ts` | Extend `DraftPC` with profile fields |
| `frontend/src/routes/CampaignCreate/StepPCs.tsx` | Integrate `PCProfileFields` component |
| `frontend/src/routes/CampaignCreate/CampaignCreate.tsx` | Save profiles after PC creation |
| `frontend/src/api/campaign/api.ts` | Add profile methods to `campaignApi` |

---

### Task 1: Path helpers

**Files:**
- Modify: `backend/src/grimoire/state_store/paths.py:155-306`
- Test: `backend/tests/characters/test_pc_profile.py` (new)

- [ ] **Step 1: Write failing tests for path helpers**

Create `backend/tests/characters/test_pc_profile.py`:

```python
"""Tests for PC profile path helpers, I/O, and rendering."""

from __future__ import annotations

from pathlib import Path

from grimoire.state_store.paths import pc_profile_path, pc_profile_revisions_dir


def test_pc_profile_path(tmp_path: Path) -> None:
    result = pc_profile_path(tmp_path, "camp-1", "alistair")
    assert result == tmp_path / "campaigns" / "camp-1" / "characters" / "alistair" / "profile.md"


def test_pc_profile_revisions_dir(tmp_path: Path) -> None:
    result = pc_profile_revisions_dir(tmp_path, "camp-1", "alistair")
    assert result == tmp_path / "campaigns" / "camp-1" / "characters" / "alistair" / "revisions"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/characters/test_pc_profile.py -v`
Expected: FAIL — `ImportError: cannot import name 'pc_profile_path'`

- [ ] **Step 3: Implement path helpers**

Add to `backend/src/grimoire/state_store/paths.py` after the `sheet_path` function:

```python
def pc_profile_path(
    data_root: Path,
    campaign_id: str,
    character_id: str,
) -> Path:
    validate_path_component(campaign_id, name="campaign_id")
    validate_path_component(character_id, name="character_id")
    return (
        campaigns_root(data_root)
        / campaign_id
        / "characters"
        / character_id
        / "profile.md"
    )


def pc_profile_revisions_dir(
    data_root: Path,
    campaign_id: str,
    character_id: str,
) -> Path:
    validate_path_component(campaign_id, name="campaign_id")
    validate_path_component(character_id, name="character_id")
    return (
        campaigns_root(data_root)
        / campaign_id
        / "characters"
        / character_id
        / "revisions"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/characters/test_pc_profile.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/state_store/paths.py backend/tests/characters/test_pc_profile.py
git commit -m "feat(pc-profile): add path helpers for profile and revisions directory"
```

---

### Task 2: PCProfile model and I/O

**Files:**
- Create: `backend/src/grimoire/characters/pc_profile.py`
- Test: `backend/tests/characters/test_pc_profile.py` (extend)

- [ ] **Step 1: Write failing tests for PCProfile model and I/O**

Append to `backend/tests/characters/test_pc_profile.py`:

```python
import pytest

from grimoire.characters.pc_profile import (
    PCProfile,
    PCProfileRevision,
    read_pc_profile,
    write_pc_profile,
    list_pc_profile_revisions,
    read_pc_profile_revision,
)
from grimoire.state_store.paths import pc_profile_path, pc_profile_revisions_dir


def test_pc_profile_defaults() -> None:
    profile = PCProfile(character_ref="library:worlds/wod/characters/alistair")
    assert profile.goals == []
    assert profile.player_notes == ""
    assert profile.description == ""
    assert profile.updated_at is not None


def test_write_and_read_profile(tmp_path: Path) -> None:
    profile = PCProfile(
        character_ref="library:worlds/wod/characters/alistair",
        goals=["Find the lost artifact", "Protect the chantry"],
        player_notes="Lean into the mentor archetype.",
        description="A Tremere elder, calm and clinical.",
    )
    write_pc_profile(tmp_path, "camp-1", "alistair", profile)
    target = pc_profile_path(tmp_path, "camp-1", "alistair")
    assert target.exists()

    loaded = read_pc_profile(tmp_path, "camp-1", "alistair")
    assert loaded is not None
    assert loaded.character_ref == "library:worlds/wod/characters/alistair"
    assert loaded.goals == ["Find the lost artifact", "Protect the chantry"]
    assert loaded.player_notes == "Lean into the mentor archetype."
    assert loaded.description == "A Tremere elder, calm and clinical."


def test_read_missing_profile(tmp_path: Path) -> None:
    result = read_pc_profile(tmp_path, "camp-1", "nobody")
    assert result is None


def test_write_creates_revision(tmp_path: Path) -> None:
    profile_v1 = PCProfile(
        character_ref="library:worlds/wod/characters/alistair",
        goals=["Original goal"],
        description="Version one.",
    )
    write_pc_profile(tmp_path, "camp-1", "alistair", profile_v1)

    profile_v2 = PCProfile(
        character_ref="library:worlds/wod/characters/alistair",
        goals=["Updated goal"],
        description="Version two.",
    )
    write_pc_profile(tmp_path, "camp-1", "alistair", profile_v2)

    revisions = list_pc_profile_revisions(tmp_path, "camp-1", "alistair")
    assert len(revisions) == 1
    assert revisions[0].description == "Version one."
    assert revisions[0].goals == ["Original goal"]


def test_read_specific_revision(tmp_path: Path) -> None:
    profile_v1 = PCProfile(
        character_ref="library:worlds/wod/characters/alistair",
        goals=["Original goal"],
        description="Version one.",
    )
    write_pc_profile(tmp_path, "camp-1", "alistair", profile_v1)

    profile_v2 = PCProfile(
        character_ref="library:worlds/wod/characters/alistair",
        goals=["Updated goal"],
        description="Version two.",
    )
    write_pc_profile(tmp_path, "camp-1", "alistair", profile_v2)

    revisions = list_pc_profile_revisions(tmp_path, "camp-1", "alistair")
    assert len(revisions) == 1

    loaded = read_pc_profile_revision(
        tmp_path, "camp-1", "alistair", revisions[0].timestamp
    )
    assert loaded is not None
    assert loaded.description == "Version one."


def test_first_write_no_revision(tmp_path: Path) -> None:
    profile = PCProfile(
        character_ref="library:worlds/wod/characters/alistair",
        goals=["First goal"],
        description="First version.",
    )
    write_pc_profile(tmp_path, "camp-1", "alistair", profile)
    revisions = list_pc_profile_revisions(tmp_path, "camp-1", "alistair")
    assert len(revisions) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/characters/test_pc_profile.py -v`
Expected: FAIL — `ImportError: cannot import name 'PCProfile'`

- [ ] **Step 3: Implement PCProfile model and I/O**

Create `backend/src/grimoire/characters/pc_profile.py`:

```python
"""PC Profile — campaign-scoped character overlay stored as markdown.

Each PC in a campaign can have a profile with description, goals, and
player notes. Profiles are markdown files with YAML frontmatter at:
    data/campaigns/{campaign_id}/characters/{character_id}/profile.md

Revisions are timestamped copies under a sibling revisions/ directory.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from grimoire.files.frontmatter import ParsedDocument, read_markdown, write_markdown
from grimoire.state_store.paths import pc_profile_path, pc_profile_revisions_dir


class PCProfile(BaseModel):
    character_ref: str = ""
    goals: list[str] = Field(default_factory=list)
    player_notes: str = ""
    description: str = ""
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PCProfileRevision(BaseModel):
    timestamp: str
    character_ref: str = ""
    goals: list[str] = Field(default_factory=list)
    player_notes: str = ""
    description: str = ""


def read_pc_profile(
    data_root: Path, campaign_id: str, character_id: str
) -> PCProfile | None:
    target = pc_profile_path(data_root, campaign_id, character_id)
    if not target.exists():
        return None
    doc = read_markdown(target)
    fm = doc.frontmatter
    return PCProfile(
        character_ref=fm.get("character_ref", ""),
        goals=fm.get("goals", []),
        player_notes=fm.get("player_notes", ""),
        description=doc.body.strip(),
        updated_at=fm.get("updated_at", datetime.now(UTC)),
    )


def write_pc_profile(
    data_root: Path, campaign_id: str, character_id: str, profile: PCProfile
) -> None:
    target = pc_profile_path(data_root, campaign_id, character_id)
    if target.exists():
        _snapshot_revision(data_root, campaign_id, character_id, target)
    profile.updated_at = datetime.now(UTC)
    fm: dict = {
        "character_ref": profile.character_ref,
        "goals": profile.goals,
        "player_notes": profile.player_notes,
        "updated_at": profile.updated_at.isoformat(),
    }
    doc = ParsedDocument(frontmatter=fm, body=profile.description + "\n")
    write_markdown(target, doc)


def list_pc_profile_revisions(
    data_root: Path, campaign_id: str, character_id: str
) -> list[PCProfileRevision]:
    rev_dir = pc_profile_revisions_dir(data_root, campaign_id, character_id)
    if not rev_dir.exists():
        return []
    revisions: list[PCProfileRevision] = []
    for path in sorted(rev_dir.glob("*.md")):
        ts = path.stem
        doc = read_markdown(path)
        fm = doc.frontmatter
        revisions.append(
            PCProfileRevision(
                timestamp=ts,
                character_ref=fm.get("character_ref", ""),
                goals=fm.get("goals", []),
                player_notes=fm.get("player_notes", ""),
                description=doc.body.strip(),
            )
        )
    return revisions


def read_pc_profile_revision(
    data_root: Path, campaign_id: str, character_id: str, timestamp: str
) -> PCProfileRevision | None:
    rev_dir = pc_profile_revisions_dir(data_root, campaign_id, character_id)
    path = rev_dir / f"{timestamp}.md"
    if not path.exists():
        return None
    doc = read_markdown(path)
    fm = doc.frontmatter
    return PCProfileRevision(
        timestamp=timestamp,
        character_ref=fm.get("character_ref", ""),
        goals=fm.get("goals", []),
        player_notes=fm.get("player_notes", ""),
        description=doc.body.strip(),
    )


def _snapshot_revision(
    data_root: Path, campaign_id: str, character_id: str, current_path: Path
) -> None:
    rev_dir = pc_profile_revisions_dir(data_root, campaign_id, character_id)
    rev_dir.mkdir(parents=True, exist_ok=True)
    doc = read_markdown(current_path)
    fm = doc.frontmatter
    ts_raw = fm.get("updated_at", datetime.now(UTC).isoformat())
    ts = str(ts_raw).replace(":", "-").replace("+", "_")
    dest = rev_dir / f"{ts}.md"
    shutil.copy2(current_path, dest)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/characters/test_pc_profile.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/characters/pc_profile.py backend/tests/characters/test_pc_profile.py
git commit -m "feat(pc-profile): PCProfile model with markdown I/O and revision history"
```

---

### Task 3: Card rendering — render_full_pc

**Files:**
- Modify: `backend/src/grimoire/characters/views.py:15-34`
- Test: `backend/tests/characters/test_pc_profile.py` (extend)

- [ ] **Step 1: Write failing tests for render_full_pc**

Append to `backend/tests/characters/test_pc_profile.py`:

```python
from grimoire.characters.views import render_full_pc
from grimoire.types.characters import Character, CharacterRole, VoiceAnchor
from grimoire.types.mechanics import Capability


def _make_character(
    description: str = "A Tremere elder.",
    body: str = "",
) -> Character:
    return Character(
        id="alistair",
        name="Alistair",
        role=CharacterRole.PC,
        tags=["vampire", "tremere"],
        voice=VoiceAnchor(summary="Crisp and formal."),
        description=description,
        body=body,
    )


def test_render_full_pc_with_profile_and_capabilities() -> None:
    char = _make_character(description="A Tremere elder.")
    profile = PCProfile(
        character_ref="library:worlds/wod/characters/alistair",
        goals=["Find the lost artifact", "Protect the chantry"],
        player_notes="Lean into the mentor archetype.",
        description="Campaign-specific backstory details.",
    )
    capabilities = [
        Capability(id="wod.dominate.2", name="Dominate", kind="discipline", description="Mental domination"),
        Capability(id="wod.auspex.1", name="Auspex", kind="discipline", description="Heightened senses"),
    ]
    result = render_full_pc(char, profile=profile, capabilities=capabilities)
    assert "# Alistair" in result
    assert "A Tremere elder." in result
    assert "## Campaign Context" in result
    assert "Campaign-specific backstory details." in result
    assert "## Goals" in result
    assert "- Find the lost artifact" in result
    assert "- Protect the chantry" in result
    assert "## Capabilities" in result
    assert "Dominate" in result
    assert "Auspex" in result
    assert "## Voice" in result
    assert "## Player Notes" in result
    assert "Lean into the mentor archetype." in result


def test_render_full_pc_empty_library_desc_uses_profile_as_primary() -> None:
    char = _make_character(description="", body="")
    profile = PCProfile(
        character_ref="library:worlds/wod/characters/alistair",
        description="This is the primary description from the profile.",
    )
    result = render_full_pc(char, profile=profile, capabilities=[])
    assert "This is the primary description from the profile." in result
    assert "## Campaign Context" not in result


def test_render_full_pc_no_profile_no_capabilities() -> None:
    char = _make_character(description="A Tremere elder.")
    result = render_full_pc(char, profile=None, capabilities=[])
    assert "# Alistair" in result
    assert "A Tremere elder." in result
    assert "## Campaign Context" not in result
    assert "## Goals" not in result
    assert "## Capabilities" not in result
    assert "## Player Notes" not in result


def test_render_full_pc_profile_with_no_content() -> None:
    char = _make_character(description="A Tremere elder.")
    profile = PCProfile(
        character_ref="library:worlds/wod/characters/alistair",
    )
    result = render_full_pc(char, profile=profile, capabilities=[])
    assert "## Campaign Context" not in result
    assert "## Goals" not in result
    assert "## Player Notes" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/characters/test_pc_profile.py::test_render_full_pc_with_profile_and_capabilities -v`
Expected: FAIL — `ImportError: cannot import name 'render_full_pc'`

- [ ] **Step 3: Implement render_full_pc**

Add to `backend/src/grimoire/characters/views.py`:

```python
from grimoire.characters.pc_profile import PCProfile
from grimoire.types.mechanics import Capability


def render_full_pc(
    character: Character,
    *,
    profile: PCProfile | None = None,
    capabilities: list[Capability] | None = None,
    seed: int | None = None,
) -> str:
    parts: list[str] = [f"# {character.name}"]
    if character.aliases:
        parts.append(f"_aliases:_ {', '.join(character.aliases)}")
    if character.age:
        parts.append(f"_age:_ {character.age}")
    if character.tags:
        parts.append(f"_tags:_ {', '.join(character.tags)}")

    lib_has_desc = bool(character.description and character.description.strip())

    if lib_has_desc:
        parts.append("")
        parts.append(character.description)
    elif profile and profile.description.strip():
        parts.append("")
        parts.append(profile.description.strip())

    if character.body:
        parts.append("")
        parts.append(character.body)

    if profile and profile.description.strip() and lib_has_desc:
        parts.append("")
        parts.append("## Campaign Context")
        parts.append(profile.description.strip())

    if profile and profile.goals:
        parts.append("")
        parts.append("## Goals")
        for goal in profile.goals:
            parts.append(f"- {goal}")

    if capabilities:
        parts.append("")
        parts.append("## Capabilities")
        for cap in capabilities:
            line = f"- **{cap.name}** ({cap.kind})"
            if cap.description:
                line += f": {cap.description}"
            parts.append(line)

    voice = _render_voice(character.voice, seed=seed)
    if voice:
        parts.append("")
        parts.append("## Voice")
        parts.append(voice)

    if profile and profile.player_notes.strip():
        parts.append("")
        parts.append("## Player Notes")
        parts.append(profile.player_notes.strip())

    return "\n".join(parts).strip()
```

Note: this adds imports at the top of `views.py`. The import of `PCProfile` uses `from grimoire.characters.pc_profile import PCProfile` and `Capability` uses `from grimoire.types.mechanics import Capability`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/characters/test_pc_profile.py -v -k "render_full_pc"`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/characters/views.py backend/tests/characters/test_pc_profile.py
git commit -m "feat(pc-profile): render_full_pc merges profile and capabilities into card"
```

---

### Task 4: Wire profile + capabilities into CharactersService.get_full_card

**Files:**
- Modify: `backend/src/grimoire/characters/service.py:302-311`
- Test: `backend/tests/characters/test_pc_profile.py` (extend)

- [ ] **Step 1: Write failing test for enriched get_full_card**

Append to `backend/tests/characters/test_pc_profile.py`:

```python
from grimoire.characters import CharactersService
from grimoire.library import LibraryService
from grimoire.mechanics import MechanicsConfig, MechanicsService
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations
from grimoire.characters.pc_profile import write_pc_profile


@pytest.fixture
async def store_for_service(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    db = Database(tmp_path / "campaigns.sqlite", pool_size=2)
    await db.connect()
    await apply_migrations(db)
    s = StateStore(db, data_root)
    try:
        yield s
    finally:
        await db.close()


@pytest.fixture
async def characters_svc(store_for_service: StateStore, tmp_path: Path) -> CharactersService:
    library = LibraryService(store_for_service)
    mech_root = tmp_path / "mechanics"
    mech_root.mkdir()
    mechanics = MechanicsService(config=MechanicsConfig(root=mech_root), state_store=store_for_service)
    return CharactersService(library, mechanics)


async def _setup_character_with_profile(
    store: StateStore, characters: CharactersService
) -> str:
    await store.write_library_file(
        library_id="worlds/wod/world/wod",
        frontmatter={"id": "wod", "name": "WoD", "version": 1},
        body="",
        source="test",
    )
    await store.upsert_campaign(campaign_id="camp-1", name="Test Campaign")
    await store.upsert_world_ref(
        campaign_id="camp-1", world_id="wod", priority=1, include=None, track_latest=True
    )
    data = CharacterData(
        id="alistair",
        name="Alistair",
        role=CharacterRole.PC,
        description="A Tremere elder.",
        voice=VoiceAnchor(summary="Formal."),
    )
    await characters.create("wod", data)
    ref = "library:worlds/wod/characters/alistair"
    await characters.add_pc("camp-1", ref, "Alistair", "local")

    profile = PCProfile(
        character_ref=ref,
        goals=["Find the artifact"],
        player_notes="Keep it dark.",
        description="Campaign-specific context.",
    )
    write_pc_profile(store.data_root, "camp-1", "alistair", profile)
    return ref


async def test_get_full_card_includes_profile(
    characters_svc: CharactersService, store_for_service: StateStore
) -> None:
    ref = await _setup_character_with_profile(store_for_service, characters_svc)
    card = await characters_svc.get_full_card(ref, "camp-1")
    assert "## Campaign Context" in card
    assert "Campaign-specific context." in card
    assert "## Goals" in card
    assert "- Find the artifact" in card
    assert "## Player Notes" in card
    assert "Keep it dark." in card
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/characters/test_pc_profile.py::test_get_full_card_includes_profile -v`
Expected: FAIL — the card should not yet contain profile sections

- [ ] **Step 3: Update get_full_card to merge profile and capabilities**

In `backend/src/grimoire/characters/service.py`, add the import near the top:

```python
from .pc_profile import PCProfile, read_pc_profile
from .views import render_full_pc
```

Then update `get_full_card` (lines 302-311) to:

```python
    async def get_full_card(
        self, ref: str, campaign_id: CampaignId, *, seed: int | None = None
    ) -> str:
        cached = self._cache.view_get(ref, campaign_id, "full", seed)
        if cached is not None:
            return cached
        resolved = await self.resolve(ref, campaign_id)
        is_pc = resolved.character.role == CharacterRole.PC
        if is_pc:
            asset_id = _asset_id_for_ref(ref)
            profile = read_pc_profile(self.store.data_root, campaign_id, asset_id)
            capabilities = await self.capabilities_of(ref, campaign_id)
            rendered = render_full_pc(
                resolved.character,
                profile=profile,
                capabilities=capabilities or None,
                seed=seed,
            )
        else:
            rendered = render_full(resolved.character, seed=seed)
        self._cache.view_set(ref, campaign_id, "full", seed, rendered)
        return rendered
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/characters/test_pc_profile.py::test_get_full_card_includes_profile -v`
Expected: PASS

- [ ] **Step 5: Run full character test suite to check for regressions**

Run: `cd backend && python -m pytest tests/characters/ -v`
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/characters/service.py backend/tests/characters/test_pc_profile.py
git commit -m "feat(pc-profile): get_full_card merges profile and capabilities for PCs"
```

---

### Task 5: Service methods for profile CRUD

**Files:**
- Modify: `backend/src/grimoire/characters/service.py`
- Test: `backend/tests/characters/test_pc_profile.py` (extend)

- [ ] **Step 1: Write failing tests for service CRUD methods**

Append to `backend/tests/characters/test_pc_profile.py`:

```python
async def test_service_get_pc_profile(
    characters_svc: CharactersService, store_for_service: StateStore
) -> None:
    ref = await _setup_character_with_profile(store_for_service, characters_svc)
    profile = await characters_svc.get_pc_profile("camp-1", ref)
    assert profile is not None
    assert profile.goals == ["Find the artifact"]
    assert profile.player_notes == "Keep it dark."


async def test_service_save_pc_profile(
    characters_svc: CharactersService, store_for_service: StateStore
) -> None:
    ref = await _setup_character_with_profile(store_for_service, characters_svc)
    new_profile = PCProfile(
        character_ref=ref,
        goals=["New goal"],
        player_notes="Updated notes.",
        description="Updated description.",
    )
    await characters_svc.save_pc_profile("camp-1", ref, new_profile)
    loaded = await characters_svc.get_pc_profile("camp-1", ref)
    assert loaded is not None
    assert loaded.goals == ["New goal"]


async def test_service_list_revisions(
    characters_svc: CharactersService, store_for_service: StateStore
) -> None:
    ref = await _setup_character_with_profile(store_for_service, characters_svc)
    new_profile = PCProfile(
        character_ref=ref,
        goals=["Updated goal"],
        description="V2.",
    )
    await characters_svc.save_pc_profile("camp-1", ref, new_profile)
    revisions = await characters_svc.list_pc_profile_revisions("camp-1", ref)
    assert len(revisions) >= 1


async def test_service_get_profile_missing_returns_none(
    characters_svc: CharactersService, store_for_service: StateStore
) -> None:
    await store_for_service.upsert_campaign(campaign_id="camp-empty", name="Empty")
    profile = await characters_svc.get_pc_profile("camp-empty", "library:worlds/wod/characters/nobody")
    assert profile is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/characters/test_pc_profile.py -v -k "service_"`
Expected: FAIL — `AttributeError: 'CharactersService' object has no attribute 'get_pc_profile'`

- [ ] **Step 3: Add service methods**

In `backend/src/grimoire/characters/service.py`, add these imports at the top:

```python
from .pc_profile import (
    PCProfile,
    PCProfileRevision,
    read_pc_profile,
    write_pc_profile,
    list_pc_profile_revisions as _list_revisions,
    read_pc_profile_revision as _read_revision,
)
```

Add the following methods to `CharactersService`, after the `capabilities_of` method (around line 661):

```python
    # ------------------------------------------------------------------ #
    # PC profiles (campaign-scoped overlay)
    # ------------------------------------------------------------------ #

    async def get_pc_profile(
        self, campaign_id: CampaignId, ref: CharacterRef
    ) -> PCProfile | None:
        asset_id = _asset_id_for_ref(ref)
        return read_pc_profile(self.store.data_root, campaign_id, asset_id)

    async def save_pc_profile(
        self, campaign_id: CampaignId, ref: CharacterRef, profile: PCProfile
    ) -> None:
        asset_id = _asset_id_for_ref(ref)
        write_pc_profile(self.store.data_root, campaign_id, asset_id, profile)
        self._cache.invalidate(ref, campaign_id)

    async def list_pc_profile_revisions(
        self, campaign_id: CampaignId, ref: CharacterRef
    ) -> list[PCProfileRevision]:
        asset_id = _asset_id_for_ref(ref)
        return _list_revisions(self.store.data_root, campaign_id, asset_id)

    async def get_pc_profile_revision(
        self, campaign_id: CampaignId, ref: CharacterRef, timestamp: str
    ) -> PCProfileRevision | None:
        asset_id = _asset_id_for_ref(ref)
        return _read_revision(self.store.data_root, campaign_id, asset_id, timestamp)
```

Note: `save_pc_profile` invalidates the view cache so `get_full_card` re-renders with updated profile data.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/characters/test_pc_profile.py -v -k "service_"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/characters/service.py backend/tests/characters/test_pc_profile.py
git commit -m "feat(pc-profile): add get/save/list/revision service methods to CharactersService"
```

---

### Task 6: API routes for PC profile CRUD

**Files:**
- Modify: `backend/src/grimoire/api/campaigns/schemas.py`
- Modify: `backend/src/grimoire/api/campaigns/pcs.py`
- Test: `backend/tests/characters/test_pc_profile.py` (extend)

- [ ] **Step 1: Add PCProfilePayload to schemas**

In `backend/src/grimoire/api/campaigns/schemas.py`, add after `AddPCPayload`:

```python
class PCProfilePayload(BaseModel):
    description: str = ""
    goals: list[str] = Field(default_factory=list)
    player_notes: str = ""
```

Add the `Field` import to the existing `from pydantic import BaseModel` line at the top:

```python
from pydantic import BaseModel, Field
```

- [ ] **Step 2: Add profile routes to pcs.py**

Append to `backend/src/grimoire/api/campaigns/pcs.py`:

```python
from .schemas import PCProfilePayload


@router.get("/{campaign_id}/pcs/{character_ref:path}/profile")
async def get_pc_profile(
    campaign_id: str,
    character_ref: str,
    characters: CharactersDep,
) -> Any:
    try:
        profile = await characters.get_pc_profile(campaign_id, character_ref)
        if profile is None:
            return {"description": "", "goals": [], "player_notes": "", "character_ref": character_ref}
        return to_payload(profile)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.put("/{campaign_id}/pcs/{character_ref:path}/profile")
async def save_pc_profile(
    campaign_id: str,
    character_ref: str,
    payload: PCProfilePayload,
    characters: CharactersDep,
) -> Any:
    from grimoire.characters.pc_profile import PCProfile

    try:
        profile = PCProfile(
            character_ref=character_ref,
            goals=payload.goals,
            player_notes=payload.player_notes,
            description=payload.description,
        )
        await characters.save_pc_profile(campaign_id, character_ref, profile)
        return to_payload(profile)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/{campaign_id}/pcs/{character_ref:path}/profile/revisions")
async def list_pc_profile_revisions(
    campaign_id: str,
    character_ref: str,
    characters: CharactersDep,
) -> Any:
    try:
        revisions = await characters.list_pc_profile_revisions(campaign_id, character_ref)
        return to_payload(revisions)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/{campaign_id}/pcs/{character_ref:path}/profile/revisions/{timestamp}")
async def get_pc_profile_revision(
    campaign_id: str,
    character_ref: str,
    timestamp: str,
    characters: CharactersDep,
) -> Any:
    from fastapi import HTTPException

    try:
        revision = await characters.get_pc_profile_revision(
            campaign_id, character_ref, timestamp
        )
        if revision is None:
            raise HTTPException(status_code=404, detail="Revision not found")
        return to_payload(revision)
    except HTTPException:
        raise
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
```

- [ ] **Step 3: Run existing tests to verify no regressions**

Run: `cd backend && python -m pytest tests/ -v -k "campaign" --timeout=30`
Expected: All existing tests PASS

- [ ] **Step 4: Commit**

```bash
git add backend/src/grimoire/api/campaigns/schemas.py backend/src/grimoire/api/campaigns/pcs.py
git commit -m "feat(pc-profile): add GET/PUT profile and revision history API routes"
```

---

### Task 7: Frontend — API client for profiles

**Files:**
- Create: `frontend/src/api/campaign/pcProfile.ts`
- Modify: `frontend/src/api/campaign/api.ts`

- [ ] **Step 1: Create pcProfile API module**

Create `frontend/src/api/campaign/pcProfile.ts`:

```typescript
import { api } from "../client";

export interface PCProfilePayload {
  description: string;
  goals: string[];
  player_notes: string;
  character_ref?: string;
}

export interface PCProfileRevision {
  timestamp: string;
  description: string;
  goals: string[];
  player_notes: string;
  character_ref?: string;
}

const enc = encodeURIComponent;

export const pcProfileApi = {
  get: (campaignId: string, characterRef: string) =>
    api.get<PCProfilePayload>(
      `/api/campaigns/${enc(campaignId)}/pcs/${enc(characterRef)}/profile`,
    ),

  save: (campaignId: string, characterRef: string, profile: PCProfilePayload) =>
    api.put<PCProfilePayload>(
      `/api/campaigns/${enc(campaignId)}/pcs/${enc(characterRef)}/profile`,
      profile,
    ),

  listRevisions: (campaignId: string, characterRef: string) =>
    api.get<PCProfileRevision[]>(
      `/api/campaigns/${enc(campaignId)}/pcs/${enc(characterRef)}/profile/revisions`,
    ),

  getRevision: (campaignId: string, characterRef: string, timestamp: string) =>
    api.get<PCProfileRevision>(
      `/api/campaigns/${enc(campaignId)}/pcs/${enc(characterRef)}/profile/revisions/${enc(timestamp)}`,
    ),
};
```

- [ ] **Step 2: Re-export from campaign api index**

Check what `frontend/src/api/campaign/index.ts` exports and add the re-export:

```typescript
export { pcProfileApi } from "./pcProfile";
export type { PCProfilePayload, PCProfileRevision } from "./pcProfile";
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/campaign/pcProfile.ts frontend/src/api/campaign/index.ts
git commit -m "feat(pc-profile): frontend API client for profile CRUD and revisions"
```

---

### Task 8: Frontend — PCProfileFields component

**Files:**
- Create: `frontend/src/routes/CampaignCreate/PCProfileFields.tsx`

- [ ] **Step 1: Create the reusable profile form component**

Create `frontend/src/routes/CampaignCreate/PCProfileFields.tsx`:

```tsx
import { useState } from "react";

export interface ProfileFieldValues {
  description: string;
  goals: string[];
  playerNotes: string;
}

interface Props {
  values: ProfileFieldValues;
  onChange: (values: ProfileFieldValues) => void;
  defaultExpanded?: boolean;
}

export function PCProfileFields({ values, onChange, defaultExpanded = false }: Props) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  const updateGoal = (index: number, text: string) => {
    const next = [...values.goals];
    next[index] = text;
    onChange({ ...values, goals: next });
  };

  const addGoal = () => {
    onChange({ ...values, goals: [...values.goals, ""] });
  };

  const removeGoal = (index: number) => {
    onChange({ ...values, goals: values.goals.filter((_, i) => i !== index) });
  };

  return (
    <details className="pc-profile-fields" open={expanded} onToggle={(e) => setExpanded((e.target as HTMLDetailsElement).open)}>
      <summary>PC Profile</summary>
      <div className="pc-profile-fields-body">
        <label htmlFor="pc-profile-desc">Description</label>
        <textarea
          id="pc-profile-desc"
          value={values.description}
          onChange={(e) => onChange({ ...values, description: e.target.value })}
          placeholder="Appearance, personality, backstory..."
          rows={4}
        />

        <label>Goals</label>
        {values.goals.map((goal, i) => (
          <div key={i} className="pc-profile-goal-row">
            <input
              type="text"
              value={goal}
              onChange={(e) => updateGoal(i, e.target.value)}
              placeholder="What does this character want?"
            />
            <button type="button" onClick={() => removeGoal(i)}>
              Remove
            </button>
          </div>
        ))}
        <button type="button" onClick={addGoal}>
          Add goal
        </button>

        <label htmlFor="pc-profile-notes">Player Notes</label>
        <textarea
          id="pc-profile-notes"
          value={values.playerNotes}
          onChange={(e) => onChange({ ...values, playerNotes: e.target.value })}
          placeholder="Guidance for the narrator — tone, themes, things to avoid..."
          rows={3}
        />
      </div>
    </details>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/routes/CampaignCreate/PCProfileFields.tsx
git commit -m "feat(pc-profile): PCProfileFields reusable form component"
```

---

### Task 9: Frontend — Integrate profile fields into wizard

**Files:**
- Modify: `frontend/src/routes/CampaignCreate/types.ts:13-18`
- Modify: `frontend/src/routes/CampaignCreate/StepPCs.tsx`
- Modify: `frontend/src/routes/CampaignCreate/CampaignCreate.tsx:257-269`

- [ ] **Step 1: Extend DraftPC with profile fields**

In `frontend/src/routes/CampaignCreate/types.ts`, update `DraftPC`:

```typescript
export interface DraftPC {
  character_ref: string;
  name: string;
  owner: string;
  origin: "library" | "new";
  profileDescription: string;
  profileGoals: string[];
  profilePlayerNotes: string;
}
```

Update `addNewPC` and `addLibraryPC` default values in `StepPCs.tsx` and the empty default in `emptyDraft()` — both need the three new fields defaulting to `""`, `[]`, and `""`.

- [ ] **Step 2: Integrate PCProfileFields into StepPCs**

Update `frontend/src/routes/CampaignCreate/StepPCs.tsx` to show profile fields per PC. In the PC list, under each PC entry, add the `PCProfileFields` component:

```tsx
import { PCProfileFields, type ProfileFieldValues } from "./PCProfileFields";

// Inside the pcs.map() render, after the remove button:
<PCProfileFields
  values={{
    description: pc.profileDescription,
    goals: pc.profileGoals,
    playerNotes: pc.profilePlayerNotes,
  }}
  onChange={(vals: ProfileFieldValues) => {
    const updatedPCs = draft.pcs.map((p) =>
      p.character_ref === pc.character_ref
        ? {
            ...p,
            profileDescription: vals.description,
            profileGoals: vals.goals,
            profilePlayerNotes: vals.playerNotes,
          }
        : p,
    );
    update({ pcs: updatedPCs });
  }}
  defaultExpanded={pc.origin === "new"}
/>
```

The `addLibraryPC` and `addNewPC` functions must include the new default fields:

```typescript
// In addLibraryPC:
const pc: DraftPC = {
  character_ref: ref,
  name: character.name ?? character.id,
  owner: "local",
  origin: "library",
  profileDescription: "",
  profileGoals: [],
  profilePlayerNotes: "",
};

// In addNewPC:
update({
  pcs: [
    ...draft.pcs,
    {
      character_ref: ref,
      name: trimmed,
      owner: "local",
      origin: "new",
      profileDescription: "",
      profileGoals: [],
      profilePlayerNotes: "",
    },
  ],
});
```

- [ ] **Step 3: Save profiles after campaign creation**

In `frontend/src/routes/CampaignCreate/CampaignCreate.tsx`, in the `submit` function, after the PC add loop (around line 269), add profile saving:

```typescript
import { pcProfileApi } from "../../api/campaign";

// After the PC add loop:
for (const pc of draft.pcs) {
  const hasProfile =
    pc.profileDescription.trim() ||
    pc.profileGoals.some((g) => g.trim()) ||
    pc.profilePlayerNotes.trim();
  if (hasProfile) {
    try {
      await pcProfileApi.save(draft.id, pc.character_ref, {
        description: pc.profileDescription,
        goals: pc.profileGoals.filter((g) => g.trim()),
        player_notes: pc.profilePlayerNotes,
      });
    } catch (err) {
      console.warn(`Failed to save profile for ${pc.character_ref}: ${errorMessage(err)}`);
    }
  }
}
```

- [ ] **Step 4: Verify the frontend builds**

Run: `cd frontend && npm run build`
Expected: Build succeeds with no type errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/CampaignCreate/types.ts frontend/src/routes/CampaignCreate/StepPCs.tsx frontend/src/routes/CampaignCreate/CampaignCreate.tsx
git commit -m "feat(pc-profile): integrate profile fields into campaign creation wizard"
```

---

### Task 10: Frontend — Mid-campaign profile edit dialog

**Files:**
- Modify: `frontend/src/routes/campaign/PCSwitcher.tsx`

- [ ] **Step 1: Add an "Edit Profile" button to each PC row in the switcher**

In `PCSwitcher.tsx`, add an "Edit Profile" button to each PC row. Clicking it opens a dialog (using a simple state-driven modal or `<dialog>` element) containing the `PCProfileFields` component. The dialog loads the current profile via `pcProfileApi.get()` on open and saves via `pcProfileApi.save()` on submit.

Add to `PCSwitcher.tsx`:

```tsx
import { useCallback, useMemo, useState } from "react";
import { pcProfileApi, type PCProfilePayload } from "../../api/campaign";
import { PCProfileFields, type ProfileFieldValues } from "../CampaignCreate/PCProfileFields";

// Inside the component, add state for the edit dialog:
const [editingRef, setEditingRef] = useState<string | null>(null);
const [editProfile, setEditProfile] = useState<ProfileFieldValues>({
  description: "",
  goals: [],
  playerNotes: "",
});
const [editLoading, setEditLoading] = useState(false);
const [editError, setEditError] = useState<string | null>(null);
```

Add handlers:

```tsx
const openEdit = useCallback(
  async (campaignId: string, ref: string) => {
    setEditingRef(ref);
    setEditLoading(true);
    setEditError(null);
    try {
      const profile = await pcProfileApi.get(campaignId, ref);
      setEditProfile({
        description: profile.description ?? "",
        goals: profile.goals ?? [],
        playerNotes: profile.player_notes ?? "",
      });
    } catch {
      setEditProfile({ description: "", goals: [], playerNotes: "" });
    } finally {
      setEditLoading(false);
    }
  },
  [],
);

const saveEdit = useCallback(
  async (campaignId: string, ref: string) => {
    setEditLoading(true);
    setEditError(null);
    try {
      await pcProfileApi.save(campaignId, ref, {
        description: editProfile.description,
        goals: editProfile.goals.filter((g) => g.trim()),
        player_notes: editProfile.playerNotes,
      });
      setEditingRef(null);
    } catch (err) {
      setEditError(err instanceof Error ? err.message : String(err));
    } finally {
      setEditLoading(false);
    }
  },
  [editProfile],
);
```

Add a button to each PC row after the existing content:

```tsx
<button
  type="button"
  className="pc-switcher-edit"
  onClick={(e) => {
    e.stopPropagation();
    openEdit(campaignId, pc.character_ref);
  }}
>
  Edit Profile
</button>
```

Render the dialog when `editingRef` is set:

```tsx
{editingRef && (
  <dialog open className="pc-profile-edit-dialog">
    <h3>Edit PC Profile</h3>
    {editLoading ? (
      <p>Loading…</p>
    ) : (
      <>
        <PCProfileFields
          values={editProfile}
          onChange={setEditProfile}
          defaultExpanded={true}
        />
        {editError && <p className="error">{editError}</p>}
        <footer>
          <button type="button" onClick={() => setEditingRef(null)}>
            Cancel
          </button>
          <button
            type="button"
            className="primary"
            onClick={() => saveEdit(campaignId, editingRef)}
          >
            Save
          </button>
        </footer>
      </>
    )}
  </dialog>
)}
```

Note: `PCSwitcher` will need `campaignId` as a new prop. Update the `Props` interface to include `campaignId: string` and thread it through from the parent `PlayView`.

- [ ] **Step 2: Verify the frontend builds**

Run: `cd frontend && npm run build`
Expected: Build succeeds

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/campaign/PCSwitcher.tsx
git commit -m "feat(pc-profile): add mid-campaign profile edit dialog to PC switcher"
```

---

### Task 11: End-to-end verification

**Files:** None (test-only)

- [ ] **Step 1: Run full backend test suite**

Run: `cd backend && python -m pytest tests/ -v --timeout=60`
Expected: All tests PASS

- [ ] **Step 2: Run frontend build**

Run: `cd frontend && npm run build`
Expected: Build succeeds

- [ ] **Step 3: Run frontend linter/typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: No type errors

- [ ] **Step 4: Final commit (if any cleanup needed)**

If any lint or type fixes are needed, commit them:

```bash
git commit -m "chore: fix lint/type issues from pc-profile integration"
```
