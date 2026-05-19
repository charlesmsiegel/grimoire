# Narrative Extras Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Land everything in `docs/superpowers/specs/2026-05-19-narrative-extras-design.md`. Adds `extras: dict[str, ExtraValue]` to library and campaign entities; SQLite mirror with FTS5 for search; cascade-resolved reads; HUD pinning; promotion paths; Extractor proposal flow.

**Architecture:** Six branches.

- **A** `feature/extras-A-schema-mirror` — migration with `entity_extras` + FTS5; `Character/Location/Item/Faction` Pydantic models gain `extras`.
- **B** `feature/extras-B-service` — `ExtrasService` (set/get/get_raw/delete/rename/search/pin) + mirror writer.
- **C** `feature/extras-C-context-stanza` — Context Builder spotlight tier item; breadcrumb on overflow.
- **D** `feature/extras-D-extractor-proposals` — `ExtrasProposal` typed candidate; heuristic; review-queue routing.
- **E** `feature/extras-E-rest` — REST routes including promote-to-fact/library.
- **F** `feature/extras-F-frontend` — Entity-detail extras table + composer integration.

**Tech Stack:** Python 3.12, FastAPI, pytest-asyncio, Pydantic v2; React/TS frontend.

---

## Conventions

Standard. **Prerequisite: `transient-state` (privacy helper)** for context-stanza filter. Soft dep on `scene-hud` for the pin chip rendering (extras spec writes `pinned_extras` to `hud.yaml`; the HUD reads it).

---

## Branch setup

- [ ] **Step S1: Worktrees**

```powershell
git worktree add .worktrees/extras-A-schema-mirror      -b feature/extras-A-schema-mirror      main
git worktree add .worktrees/extras-B-service            -b feature/extras-B-service            main
git worktree add .worktrees/extras-C-context-stanza     -b feature/extras-C-context-stanza     main
git worktree add .worktrees/extras-D-extractor          -b feature/extras-D-extractor          main
git worktree add .worktrees/extras-E-rest               -b feature/extras-E-rest               main
git worktree add .worktrees/extras-F-frontend           -b feature/extras-F-frontend           main
```

---

# Branch A — Schema + mirror migration

### Task A1: Migration

**Files:**
- Create: `backend/src/grimoire/storage/migrations/026_entity_extras.sql`.

- [ ] **Step 1: SQL**

```sql
-- backend/src/grimoire/storage/migrations/026_entity_extras.sql
CREATE TABLE entity_extras (
    campaign_id    TEXT NOT NULL,                -- "" for library scope
    entity_kind    TEXT NOT NULL,
    entity_id      TEXT NOT NULL,
    scope          TEXT NOT NULL,                -- library | campaign-local | override
    key            TEXT NOT NULL,
    value_json     TEXT NOT NULL,
    set_at         TEXT NOT NULL,
    set_by         TEXT NOT NULL,
    PRIMARY KEY (campaign_id, entity_kind, entity_id, scope, key),
    CHECK (key NOT LIKE '\_internal\_%' ESCAPE '\'
       AND key NOT LIKE 'mechanics\_%'   ESCAPE '\'
       AND key NOT LIKE 'system\_%'      ESCAPE '\')
);

CREATE VIRTUAL TABLE entity_extras_fts USING fts5(
    entity_kind UNINDEXED,
    entity_id   UNINDEXED,
    key         UNINDEXED,
    value_text,
    content='entity_extras',
    content_rowid='rowid'
);

CREATE TRIGGER entity_extras_ai AFTER INSERT ON entity_extras BEGIN
    INSERT INTO entity_extras_fts(rowid, entity_kind, entity_id, key, value_text)
    VALUES (new.rowid, new.entity_kind, new.entity_id, new.key, new.value_json);
END;

CREATE TRIGGER entity_extras_ad AFTER DELETE ON entity_extras BEGIN
    INSERT INTO entity_extras_fts(entity_extras_fts, rowid, entity_kind, entity_id, key, value_text)
    VALUES ('delete', old.rowid, old.entity_kind, old.entity_id, old.key, old.value_json);
END;

CREATE TRIGGER entity_extras_au AFTER UPDATE ON entity_extras BEGIN
    INSERT INTO entity_extras_fts(entity_extras_fts, rowid, entity_kind, entity_id, key, value_text)
    VALUES ('delete', old.rowid, old.entity_kind, old.entity_id, old.key, old.value_json);
    INSERT INTO entity_extras_fts(rowid, entity_kind, entity_id, key, value_text)
    VALUES (new.rowid, new.entity_kind, new.entity_id, new.key, new.value_json);
END;
```

- [ ] **Step 2: Commit.**

### Task A2: Entity model fields

**Files:**
- Modify: `backend/src/grimoire/types/characters.py:Character`, `types/world.py:Location/Item/Faction`.
- Create: `backend/src/grimoire/types/extras.py`.
- Test: `backend/tests/types/test_extras_field.py`.

- [ ] **Step 1: Failing tests**

```python
from grimoire.types.characters import Character


def test_default_extras_is_empty_dict():
    c = Character(id="x", name="X", role="npc")
    assert c.extras == {}


def test_extras_roundtrip_through_frontmatter():
    c = Character(id="x", name="X", role="npc", extras={
        "favorite_drink": ExtraValue(value="whisky", set_at=..., set_by="user", scope="library"),
    })
    yaml = c.model_dump()
    restored = Character.model_validate(yaml)
    assert restored.extras["favorite_drink"].value == "whisky"


def test_reserved_prefix_rejected_at_validation():
    with pytest.raises(ValueError, match="_internal_"):
        Character(id="x", name="X", role="npc",
                  extras={"_internal_secret": ExtraValue(...)})
```

- [ ] **Step 2: Implement**

```python
# backend/src/grimoire/types/extras.py
from datetime import datetime
from enum import StrEnum
from typing import Any
from pydantic import BaseModel, Field, field_validator


class ExtraScope(StrEnum):
    LIBRARY = "library"
    CAMPAIGN_LOCAL = "campaign-local"
    OVERRIDE = "override"


_RESERVED_PREFIXES = ("_internal_", "mechanics_", "system_")


def _validate_key(k: str) -> None:
    if not (1 <= len(k) <= 40):
        raise ValueError(f"extras key length must be 1-40, got {len(k)}: {k!r}")
    if any(k.startswith(p) for p in _RESERVED_PREFIXES):
        raise ValueError(f"reserved prefix on key: {k!r}")
    if not k.replace("_", "").isalnum():
        raise ValueError(f"extras key must be snake_case alphanumeric: {k!r}")


class ExtraValue(BaseModel):
    value: Any
    set_at: datetime
    set_by: str
    source_evidence: str | None = None
    scope: ExtraScope = ExtraScope.CAMPAIGN_LOCAL


def _ExtrasDict_validator(v: dict[str, Any]) -> dict[str, ExtraValue]:
    for k in v:
        _validate_key(k)
    return v


# In each entity model, add:
# extras: dict[str, ExtraValue] = Field(default_factory=dict)
# with a @field_validator("extras") wrapping _ExtrasDict_validator.
```

- [ ] **Step 3: Commit + merge A.**

---

# Branch B — `ExtrasService` core

### Task B1: Service + mirror

**Files:**
- Create: `backend/src/grimoire/extras/__init__.py`, `service.py`, `mirror.py`.
- Test: `backend/tests/extras/test_service.py`, `test_mirror.py`.

- [ ] **Step 1: Failing tests**

```python
async def test_set_library_writes_frontmatter_and_mirror(service, library, store):
    await service.set(
        entity_kind=EntityKind.CHARACTER, entity_id="winifred",
        key="favorite_drink", value="Glenfarclas 25",
        scope=ExtraScope.LIBRARY, campaign_id=None, actor="user",
    )
    # Frontmatter has the extra
    entity = await library.get_entity(world_id="by-night", kind="character", asset_id="winifred")
    assert entity.frontmatter["extras"]["favorite_drink"] == "Glenfarclas 25"
    # Mirror row exists
    async with store.db.connect_read() as conn:
        row = await conn.execute_fetchone(
            "SELECT * FROM entity_extras WHERE entity_id='winifred' AND key='favorite_drink'",
        )
    assert row is not None


async def test_get_cascade_merges_library_and_override(service, library, store, seeded_campaign):
    await service.set(EntityKind.CHARACTER, "winifred", "drink", "wine",
                      scope=ExtraScope.LIBRARY, campaign_id=None, actor="user")
    await service.set(EntityKind.CHARACTER, "winifred", "drink", "whisky",
                      scope=ExtraScope.OVERRIDE, campaign_id=seeded_campaign, actor="user")
    resolved = await service.get(EntityKind.CHARACTER, "winifred", campaign_id=seeded_campaign)
    assert resolved["drink"].value == "whisky"


async def test_override_null_removes_key(service, ...):
    ...


async def test_hard_cap_50_extras_rejected(service, ...):
    ...


async def test_soft_cap_warning_in_response(service, ...):
    ...


async def test_search_fts_substring(service, ...):
    # set dialect_notes: "drops aitches when angry"
    # search "aitches" returns the hit
    ...
```

- [ ] **Step 2: Implement ExtrasService**

```python
# backend/src/grimoire/extras/service.py
class ExtrasService:
    def __init__(self, *, library, store):
        self.library = library
        self.store = store

    async def set(
        self, *,
        entity_kind: EntityKind, entity_id: str, key: str, value,
        scope: ExtraScope, campaign_id: str | None,
        actor: str, evidence: str | None = None,
    ) -> ExtraValue:
        _validate_key(key)
        await self._check_caps(entity_kind, entity_id, campaign_id, key, value)
        extra = ExtraValue(value=value, set_at=_now(), set_by=actor,
                           source_evidence=evidence, scope=scope)
        if scope == ExtraScope.LIBRARY:
            await self.library.update_entity_extras(
                entity_kind=entity_kind, entity_id=entity_id,
                patch={key: extra.model_dump()},
            )
        else:
            # campaign-local emergent OR override
            await self.store.write_extra_to_campaign(
                campaign_id=campaign_id, scope=scope,
                entity_kind=entity_kind, entity_id=entity_id,
                key=key, extra=extra,
            )
        # Update mirror
        await self.store.upsert_entity_extras_mirror(
            campaign_id=campaign_id or "",
            entity_kind=entity_kind.value, entity_id=entity_id,
            scope=scope.value, key=key, value=value, set_at=extra.set_at,
            set_by=actor,
        )
        return extra

    async def get(
        self, *,
        entity_kind, entity_id, campaign_id, for_observer=None,
    ) -> dict[str, ExtraValue]:
        # Cascade resolution: library + campaign override (and emergent for campaign-only entities)
        resolved = await self.library.resolve_entity(entity_kind, entity_id, campaign_id)
        return {
            k: ExtraValue(**v) for k, v in (resolved.frontmatter.get("extras") or {}).items()
        }

    async def delete(self, *, entity_kind, entity_id, key, scope, campaign_id):
        if scope == ExtraScope.OVERRIDE:
            # Write override-null to clear the key
            await self.store.write_extra_to_campaign(
                campaign_id=campaign_id, scope=ExtraScope.OVERRIDE,
                entity_kind=entity_kind, entity_id=entity_id,
                key=key, extra=None,    # None = override null
            )
        elif scope == ExtraScope.LIBRARY:
            await self.library.update_entity_extras(entity_kind, entity_id, delete=[key])
        else:
            await self.store.delete_extra_campaign_local(campaign_id, entity_kind, entity_id, key)
        await self.store.delete_entity_extras_mirror(campaign_id or "", entity_kind, entity_id, key, scope)

    async def search(self, query, *, campaign_id=None, entity_kind=None, key=None, limit=50):
        async with self.store.db.connect_read() as conn:
            rows = await conn.execute_fetchall(
                "SELECT * FROM entity_extras_fts WHERE entity_extras_fts MATCH ? LIMIT ?",
                (query, limit),
            )
        return [self._row_to_hit(r) for r in rows]

    async def promote_to_fact(self, *, campaign_id, entity_kind, entity_id, key, turn_id):
        extras = await self.get(entity_kind=entity_kind, entity_id=entity_id,
                                campaign_id=campaign_id)
        extra = extras.get(key)
        if extra is None:
            raise ExtrasNotFoundError(key)
        fact_text = self._render_fact_text(entity_kind, entity_id, key, extra.value)
        fact_id = await self.continuity.add_fact(
            campaign_id=campaign_id, text=fact_text,
            established_in_post=None, established_in_turn=turn_id,
            tags=[entity_kind.value, "promoted-from-extras"],
        )
        return fact_id

    async def promote_to_library(self, *, campaign_id, entity_kind, entity_id, key):
        extras = await self.get(entity_kind=entity_kind, entity_id=entity_id,
                                campaign_id=campaign_id)
        extra = extras.get(key)
        if extra is None or extra.scope == ExtraScope.LIBRARY:
            raise ExtrasPromotionError("nothing to promote")
        await self.set(entity_kind=entity_kind, entity_id=entity_id, key=key,
                       value=extra.value, scope=ExtraScope.LIBRARY,
                       campaign_id=None, actor="promotion")
        await self.delete(entity_kind=entity_kind, entity_id=entity_id, key=key,
                          scope=ExtraScope.OVERRIDE, campaign_id=campaign_id)

    async def pin(self, *, campaign_id, entity_kind, entity_id, key, pinned: bool):
        # Delegate to HudConfigService — pin state lives in hud.yaml
        await self.hud_config.set_pinned_extra(campaign_id, entity_kind, entity_id, key, pinned=pinned)
```

- [ ] **Step 3: Tests PASS, commit, merge B.**

---

# Branch C — Context Builder spotlight stanza

### Task C1: Extras tier item

**Files:**
- Modify: `backend/src/grimoire/context/builder.py:_resolve_cast` — emit extras `_TierItem` per present character.
- Test: `backend/tests/context/test_extras_stanza.py`.

- [ ] **Step 1: Failing tests**

```python
async def test_extras_stanza_in_spotlight_tier(builder, seeded_state, library):
    await library.update_entity_extras(EntityKind.CHARACTER, "winifred",
        patch={"smokes": "Sobranie Black Russians", "scars": ["scar above brow"]})
    prompt = await builder.build(... extras consumer set ...)
    blob = "\n".join(m.content for m in prompt.messages)
    assert "winifred — extras:" in blob
    assert "smokes: Sobranie Black Russians" in blob


async def test_breadcrumb_on_overflow(builder, ...):
    # set 20 extras worth >> threshold; assert breadcrumb (keys only) appears in background tier
    ...


async def test_private_extras_dropped_for_audience(...):
    # Character.privacy.extras.smokes.surface_in_context = false
    # observer=AUDIENCE → smokes line absent
    ...
```

- [ ] **Step 2: Implement** — after `_resolve_cast` emits voice anchor, emit `_render_extras_for_character(character, observer)` `_TierItem(tier=SPOTLIGHT, section="extras", priority=8)`. If the rendered text exceeds `config.extras.demote_to_breadcrumb_threshold_tokens`, replace with a breadcrumb (`extras: smokes, scars, ...`) in BACKGROUND tier.

- [ ] **Step 3-N: Tests PASS, commit, merge C.**

---

# Branch D — Extractor `ExtrasProposal`

### Task D1: Typed candidate + heuristic

**Files:**
- Modify: `backend/src/grimoire/types/extraction.py:ExtractionResult` — add `extras_proposals`.
- Modify: `backend/src/grimoire/extractor/heuristics.py` — add proposal logic.
- Modify: `backend/src/grimoire/extractor/service.py` — route proposals into review queue.
- Test: `backend/tests/extractor/test_extras_proposals.py`.

- [ ] **Step 1: Failing tests**

```python
async def test_repeated_attribute_proposes_extra(extractor, scene_with_repeated_smokes):
    # Scene where winifred is described smoking Sobranies in 3 posts
    result = await extractor.extract(...)
    assert any(p.key == "smokes" for p in result.extras_proposals)
    assert result.extras_proposals[0].confidence >= 0.7


async def test_proposal_below_confidence_floor_discarded(...):
    # confidence 0.5 < 0.7 floor → not in result.extras_proposals
    ...


async def test_proposal_routed_to_review_queue(extractor, ...):
    # confidence 0.7 → review_queue row with kind="extras_proposal"
    ...


async def test_at_soft_cap_no_new_proposals(...):
    # entity has 20 extras; proposals skipped; single warning emitted
    ...
```

- [ ] **Step 2: Implement heuristic** in `extractor/heuristics.py` — scan post bodies for repeated `<name> <verb> <stable attribute>` patterns with sensory specifics. Emit `ExtrasProposal` records.

- [ ] **Step 3: Routing in `extractor/service.py`** — after extraction, walk `result.extras_proposals` and either enqueue review or discard based on threshold.

- [ ] **Step 4-N: Tests PASS, commit, merge D.**

---

# Branch E — REST routes

### Task E1: All routes

**Files:**
- Create: `backend/src/grimoire/api/extras.py`.
- Test: `backend/tests/api/test_extras_routes.py`.

Routes (per spec):
```
GET    /library/{world}/{kind}/{id}/extras
PUT    /library/{world}/{kind}/{id}/extras/{key}
DELETE /library/{world}/{kind}/{id}/extras/{key}

GET    /campaigns/{id}/{kind}/{eid}/extras
GET    /campaigns/{id}/{kind}/{eid}/extras/raw
PUT    /campaigns/{id}/{kind}/{eid}/extras/{key}
DELETE /campaigns/{id}/{kind}/{eid}/extras/{key}

POST   /campaigns/{id}/{kind}/{eid}/extras/{key}/pin
POST   /campaigns/{id}/{kind}/{eid}/extras/{key}/unpin
POST   /campaigns/{id}/{kind}/{eid}/extras/{key}/promote-to-fact
POST   /campaigns/{id}/{kind}/{eid}/extras/{key}/promote-to-library

GET    /search/extras?q=...
```

- [ ] **Step 1: Failing test per route** — happy path + 422 (reserved prefix, cap exceeded) + 409 (promotion conflict).

- [ ] **Step 2-N: Implement + commit + merge E.**

---

# Branch F — Frontend

### Task F1: Entity-detail ExtrasTable

**Files:**
- Create: `frontend/src/routes/library/EntityDetail/ExtrasTable.tsx`.
- Modify: `frontend/src/api/extras.ts` (new).
- Modify: `frontend/src/routes/campaign/SideHud/PresentCastChip.tsx` — render pinned-extra chips.

- [ ] **Step 1: Failing tests** for ExtrasTable component.

- [ ] **Step 2-N: Implement** — inline edit, add-field modal, pin toggle, promote menu, source badges (📚/🌿/✏️).

- [ ] **Step end: Tests PASS, commit, merge F.**

---

# Integration check

- [ ] **Step end1: Full suite + frontend tests.**
- [ ] **Step end2: Manual smoke** — add extras to a character, pin one, see it on the HUD chip, promote one to fact.
- [ ] **Step end3: COMPLETED doc + delete design.**
