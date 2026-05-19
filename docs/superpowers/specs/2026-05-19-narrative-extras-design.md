## Narrative Extras — Design

> **Status:** Design ready for implementation plan. Soft dep on `transient-state-design.md` (privacy model is consumed for `extras` rendered in HUD/context). Soft dep on `scene-hud-design.md` (pin chips on present-cast widget). Independent of `swipes-alternates`/`retcon`/`fork`.

**Source idea:** `specs/new/narrative-extras.md`
**Module:** `backend/src/grimoire/extras/` (new), schema extensions in `backend/src/grimoire/types/`

## Purpose

A third structured tier on library and campaign entities (characters, locations, items, factions): `extras.<key> = <value>`. Sits alongside frontmatter core (Grimoire-owned) and mechanics sheets (module-owned). The use cases: `mechanics: null` campaigns wanting richer character profiles, cross-mechanics consistency (extras travel when mechanics changes), long-tail color (favorite drink, scars, pet peeves, dialect notes).

Extras travel with the entity — they're part of frontmatter, cascade through campaign overrides like other entity fields, and pin to the HUD per-campaign.

## Scope (what changes)

- **Schema:** `extras: dict[str, ExtraValue]` added to `Character`, `Location`, `Item`, `Faction` Pydantic models with backwards-compatible default `{}`.
- **Persistence:** values live in entity frontmatter (file SSOT — same as existing entity data). New SQLite mirror `entity_extras` for query (substring search, listing pinned, observability).
- **Service:** new `grimoire.extras.ExtrasService` exposing the `Extras` protocol; writes route through `LibraryService` / `StateStore` for library vs campaign-override files.
- **Cascade:** uses the existing `_deep_merge_frontmatter` path (`library/service.py:83`) — net-new keys added, present keys with override value replaced, present keys with `null` removed, lists replaced wholesale.
- **HUD pinning:** stored in `hud.yaml` per `scene-hud-design.md` (no separate table — the HUD config file owns pin metadata).
- **Context Builder:** new compact stanza in spotlight tier (after the main character card), demote to background breadcrumb on overflow.
- **REST:** library + campaign read/write/pin/promote routes.
- **Extractor:** typed `extras_proposals: list[ExtrasProposal]` on `ExtractionResult` (per Theme E typed-candidate-lists decision); enters the review queue with evidence.
- **Templates:** setting-shipped starter keys at `data/library/settings/<sid>/extras-templates/<kind>.yaml` (per the open question — campaign/world directory; mechanics modules do *not* ship templates, ownership-boundary).

## Schema

```yaml
extras:
  favorite_drink: "Whisky, neat — Glenfarclas 25"
  scars:
    - "thin one above left eyebrow"
    - "burn on right palm from the fire at Camden in '67"
  smokes: "occasionally — only Sobranie Black Russians"
  dialect_notes: "drops his aitches when very angry"
```

Value types:
- Scalar: `str`, `int`, `float`, `bool`, `None` (null-as-override-clear).
- `list[scalar]`.
- `dict[str, scalar]` — single level only.
- No deep nesting (if you need it, you have a sheet, not an extra).

Keys: snake_case, 1–40 chars. Reserved prefixes: `_internal_`, `mechanics_`, `system_`. Validation enforces these on every write (service-side; the SQL layer also rejects via `CHECK` constraint as belt-and-suspenders).

```python
class ExtraValue(BaseModel):
    value: ScalarOrListOrDict
    set_at: datetime
    set_by: str                          # actor: "user", "extractor:reviewed", "import:sillytavern"
    source_evidence: Optional[str]       # post excerpt for extractor-proposed
    scope: ExtraScope                    # library | campaign-local | override
```

Soft caps (warning, not rejection):
- 20 extras per entity, 200 chars per string, 20 list items.
- 4 KB total serialized.

Hard caps (rejection):
- 50 extras per entity.
- 1000 chars per string.

Limits computed at write time after cascade resolution (an override that brings the total above 50 fails the PATCH; the user sees a 422 with the specific key that pushed over).

## Storage cascade

File SSOT — extras live in frontmatter. Library files own their portion; campaign overrides own theirs.

```
library/worlds/by-night/characters/winifred-allard.md  (library extras: voice_register, scars)
campaigns/by-night-london/overrides/by-night/characters/winifred-allard.yaml  (override extras: smokes: null, dialect_notes: "...")

Resolved (in this campaign):
  voice_register: ...           ← library (unchanged)
  scars: [...]                  ← library (unchanged)
  dialect_notes: "..."          ← override (net-new)
  # smokes removed because override set null
```

Emergent entities live entirely in the campaign; their extras live in the emergent file directly.

`StateStore.write_override` already handles arbitrary frontmatter patch dicts — no schema change needed for the override file format. The `Extras` service writes through `LibraryService.create_entity` / `update_entity` for library, and `StateStore.write_override` for campaign overrides.

**SQLite mirror** (migration 025 — picked at plan time):

```sql
CREATE TABLE entity_extras (
    campaign_id    TEXT NOT NULL,           -- "" for library scope
    entity_kind    TEXT NOT NULL,           -- character | location | item | faction
    entity_id      TEXT NOT NULL,
    scope          TEXT NOT NULL,           -- library | campaign-local | override
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
    entity_id UNINDEXED,
    key UNINDEXED,
    value_text,                              -- flattened scalar/list values for search
    content='entity_extras',
    content_rowid='rowid'
);
```

Per the open question on FTS vs LIKE: use FTS5, matching the existing pattern in `facts` and `library_index`. The `value_text` column is populated by triggers (or write-time materialization in `ExtrasService.set`); list values are space-joined, dict values are `key:value` joined.

Reads do not touch the mirror — they use the cascade-resolved frontmatter dict already on the `ResolvedEntity`. The mirror is for **query only**:
- "find every character with `dialect_notes` containing 'aitches'"
- "list all pinned extras for present cast"
- "audit: which campaign-override extras are net-new vs override of library"

Mirror staleness: re-materialized on every `Extras.set` / `delete` call; periodic reconciliation job (lives with the existing library re-index path).

## Service

```python
class Extras(Protocol):
    async def get(
        self,
        entity_kind: EntityKind,
        entity_id: str,
        *,
        campaign_id: str | None = None,
        for_observer: ObserverKind | None = None,
    ) -> dict[str, ExtraValue]:
        """Cascade-resolved extras; None campaign_id returns library-scope only."""

    async def get_raw(
        self,
        entity_kind: EntityKind,
        entity_id: str,
        scope: ExtraScope,
        *,
        campaign_id: str | None = None,
    ) -> dict[str, ExtraValue]:
        """Single-scope read (no cascade) — for the entity-detail UI source badges."""

    async def set(
        self,
        entity_kind: EntityKind,
        entity_id: str,
        key: str,
        value: ScalarOrListOrDict,
        *,
        scope: ExtraScope,
        campaign_id: str | None,
        actor: str,                              # "user" | "extractor:reviewed" | "import:<source>"
        evidence: str | None = None,
    ) -> ExtraValue: ...

    async def delete(
        self,
        entity_kind: EntityKind,
        entity_id: str,
        key: str,
        *,
        scope: ExtraScope,
        campaign_id: str | None,
    ) -> None: ...

    async def rename(
        self,
        entity_kind: EntityKind,
        entity_id: str,
        old_key: str,
        new_key: str,
        *,
        scope: ExtraScope,
        campaign_id: str | None,
    ) -> None: ...

    async def pin(
        self,
        campaign_id: str,
        entity_kind: EntityKind,
        entity_id: str,
        key: str,
        *,
        pinned: bool,
    ) -> None: ...

    async def search(
        self,
        query: str,
        *,
        campaign_id: str | None = None,
        entity_kind: EntityKind | None = None,
        key: str | None = None,
        limit: int = 50,
    ) -> list[ExtrasSearchHit]: ...

    async def promote_to_fact(
        self,
        campaign_id: str,
        entity_kind: EntityKind,
        entity_id: str,
        key: str,
        *,
        turn_id: str,
    ) -> FactId: ...

    async def promote_to_library(
        self,
        campaign_id: str,
        entity_kind: EntityKind,
        entity_id: str,
        key: str,
    ) -> None: ...
```

Promotion is **copy semantics**: the original extra remains unless the user explicitly removes it. Promote-to-fact writes a `facts` row via `ContinuityService.add_fact`; promote-to-library moves the extra from override scope to library scope (delete override + write library).

## Pin state

Per `scene-hud-design.md`, pin state lives in `hud.yaml`:

```yaml
ordered_widgets:
  - id: core.present-cast
    visible: true
    options:
      pinned_extras:
        winifred-allard: [scars, dialect_notes]
        julian-bain: [smokes]
```

Max 3 per character (soft guidance — warning in UI, not enforced). The HUD aggregator reads `pinned_extras` and includes them in the present-cast payload alongside transient state.

Pin/unpin endpoints write through `HudConfigService.update_pinned_extras`; the Extras service exposes a convenience `pin()` that delegates. No new SQLite table for pins (per the open question — config file is sufficient because the data is small and per-campaign).

## Context Builder integration

After `_resolve_cast()` emits the character card + voice anchor (priorities 10 / 9), a new `_TierItem` (priority 8) emits the extras stanza:

```
winifred Allard — extras:
  voice_register: refined, occasionally drops "h" when angry
  scars: thin scar above left eyebrow; burn on right palm
  dialect_notes: drops aitches when very angry
  smokes: occasionally — only Sobranie Black Russians
```

Empty / null values are omitted. The stanza is gated by per-character privacy at the field level — keys flagged as private (via `Character.privacy.extras.<key>.surface_in_context: false`) are filtered out for `observer != author`. The privacy model is owned by transient-state per Theme B; extras consume the same helper (`grimoire.transient_state.privacy.resolve_extras_visibility`).

On context-budget overflow, the extras tier item demotes from spotlight to background as a **breadcrumb** (keys only, no values): "winifred — extras: scars, dialect_notes, smokes". The breadcrumb signals presence without spending tokens on values; the user can pin specific extras to the HUD to keep them in spotlight.

## Extractor-proposed extras

`ExtractionResult` gains:

```python
@dataclass
class ExtrasProposal:
    entity_kind: EntityKind
    entity_id: str
    key: str
    value: ScalarOrListOrDict
    confidence: float
    evidence: str
    scope_hint: ExtraScope = ExtraScope.CAMPAIGN_LOCAL
```

Routing in `extractor/service.py`:
- `confidence >= 0.7` and no soft-cap hit on the target → enqueue in `review_queue` with `kind="extras_proposal"`.
- User approves → `ExtrasService.set(..., actor="extractor:reviewed")`.
- Auto-create safeguards: max 1 proposal per turn per entity, confidence floor 0.7, respect soft caps (when an entity has 20 extras, stop proposing and emit a one-time campaign warning).

Heuristics for proposal generation live in `extractor/heuristics.py` (existing module) — repeated attribute across multiple posts, "always / never / usually" qualifiers, sensory specificity. The patterns are codified there per spec; the heuristic delivers `ExtrasProposal` objects merged into the result.

## REST surface

```
GET    /library/{world_or_setting}/{kind}/{id}/extras
PUT    /library/{world_or_setting}/{kind}/{id}/extras/{key}
DELETE /library/{world_or_setting}/{kind}/{id}/extras/{key}

GET    /campaigns/{id}/{kind}/{eid}/extras                  # cascade-resolved
GET    /campaigns/{id}/{kind}/{eid}/extras/raw              # local + override only
PUT    /campaigns/{id}/{kind}/{eid}/extras/{key}            # writes campaign override
DELETE /campaigns/{id}/{kind}/{eid}/extras/{key}            # writes override-null

POST   /campaigns/{id}/{kind}/{eid}/extras/{key}/pin
POST   /campaigns/{id}/{kind}/{eid}/extras/{key}/unpin
POST   /campaigns/{id}/{kind}/{eid}/extras/{key}/promote-to-fact
POST   /campaigns/{id}/{kind}/{eid}/extras/{key}/promote-to-library

GET    /search/extras?q=...&kind=...&key=...               # FTS via mirror
```

PUT bodies:
```json
{ "value": "...", "evidence": null }
```

## Entity-detail UI

A clean key/value table on the entity detail page with:
- Source badge per row (📚 library / 🌿 campaign-local / ✏️ override).
- Inline edit on click; commit on blur or Enter.
- `[+ Add field]` modal: key, value type picker, value input. Type drives the input widget (string, number, bool toggle, list builder).
- Delete with confirmation.
- Pin/unpin toggle icon next to each row (HUD pin state).
- Reserved-prefix / hard-cap inline validation.

Templates (when present): "Suggested starter keys for character: favorite_drink, scars, dialect_notes…" — one-click stub creation, no values.

## Templates

`data/library/settings/<sid>/extras-templates/<kind>.yaml`:

```yaml
# character.yaml
suggested:
  - key: favorite_drink
    description: "What they order without thinking"
    type: string
  - key: scars
    description: "Visible marks; appearance + provenance"
    type: list[string]
  - key: smokes
    description: "Smoking habits (or lack thereof)"
    type: string
```

Templates are read-only suggestions. Mechanics modules **do not ship templates** (ownership boundary — mechanics owns sheets, narrative-extras owns extras, no overlap). The entity-creator UI loads applicable templates lazily.

## Cross-spec hooks

- **`transient-state`** — privacy helper consumed for `surface_in_context` and `surface_in_hud` per-key flags. Theme B locks this dependency: transient-state owns the privacy schema.
- **`scene-hud`** — pin chips on present-cast widget. The HUD reads `hud.yaml`'s `pinned_extras` map.
- **`context-inspector`** — extras become a new inclusion-reason on `ContextSource`: `inclusion_reason: "extras_pinned_to_hud"` and `"extras_default_visible"`.
- **`fork`** — extras travel naturally because they're frontmatter. The fork's bulk-copy of frontmatter files carries them.

## Configuration

```yaml
extras:
  hard_caps:
    per_entity: 50
    chars_per_string: 1000
  soft_caps:
    per_entity: 20
    chars_per_string: 200
    list_items: 20
    total_bytes: 4096
  context_budget:
    spotlight_priority: 8
    demote_to_breadcrumb_threshold_tokens: 200    # extras stanza exceeding this becomes a breadcrumb
  extractor:
    auto_apply_threshold: 0.85     # auto-apply (still requires review for v1; reserved for future)
    review_threshold: 0.70
    max_proposals_per_turn_per_entity: 1
  templates:
    enabled: true
```

For v1, the extractor never auto-applies — every proposal goes to review. The `auto_apply_threshold` knob is reserved for future opt-in.

## Performance

- Single-entity cascade-resolved read: < 5 ms (frontmatter dict already in memory after entity resolve).
- Bulk read for present cast (5 chars): < 30 ms.
- Add / edit / delete: < 20 ms (file write + mirror update).
- FTS search across a campaign: < 50 ms p95 for typical campaign sizes (< 10,000 extras rows).

## Audit and observability

Every `set`/`delete`/`rename`/`pin`/`promote-to-*` emits a `turn_audits.applied_deltas` JSON entry tagged `extras_*`. Promotion paths additionally emit canonical Continuity / Library audit lines via the existing services. Mirror reconciliation runs emit a summary log line.

## Failure handling

| Failure | Behavior |
|---|---|
| Reserved-prefix key | Reject at service + DB constraint; 422 |
| Hard cap exceeded | Reject; 422 with the breaching key surfaced |
| Soft cap exceeded | Accept; response carries a `warnings: [...]` field |
| Override with bad type (e.g., dict where library is list) | Accept (overrides replace wholesale); but render a UI warning |
| Promote-to-fact contradicts existing fact | Continuity contradiction path handles; promote returns the contradiction report id |
| Extractor proposal violates soft caps | Skip; emit one-time campaign warning |
| Mirror drift detected | Background reconcile rebuilds from frontmatter SSOT; log entry |

## Test wiring

`backend/tests/extras/test_service.py` (new):
- CRUD on library + campaign-override scopes.
- Cascade resolution (library + override merge, null-clear).
- Soft / hard cap enforcement.
- Reserved-prefix rejection.
- Rename with collision.

`backend/tests/extras/test_mirror.py`:
- Mirror reflects frontmatter after write.
- FTS substring search across `value_text`.
- Reconciliation rebuilds correctly after manual frontmatter edit.

`backend/tests/extras/test_promotion.py`:
- Promote-to-fact creates Continuity row, leaves extra in place.
- Promote-to-library moves from override to library scope.

`backend/tests/extras/test_extractor_integration.py`:
- Typed `extras_proposals` routing into review queue.
- Soft-cap-respecting proposal limiter.

`backend/tests/context/test_extras_stanza.py`:
- Spotlight tier renders the stanza for present characters.
- Privacy filter drops `surface_in_context: false` keys.
- Overflow demotes to breadcrumb.

## Wiring touchpoints

- `backend/src/grimoire/extras/service.py` (new): ExtrasService.
- `backend/src/grimoire/extras/mirror.py` (new): SQLite mirror writer + reconciler.
- `backend/src/grimoire/types/characters.py`, `types/world.py`: add `extras: dict[str, ExtraValue]` with default `{}`.
- `backend/src/grimoire/types/extraction.py`: add `extras_proposals: list[ExtrasProposal]`.
- `backend/src/grimoire/extractor/heuristics.py`: extras-proposal heuristic.
- `backend/src/grimoire/extractor/service.py`: routing for extras_proposals into review queue.
- `backend/src/grimoire/context/builder.py:_resolve_cast`: emit extras `_TierItem`; budget-driven demotion.
- `backend/src/grimoire/api/extras.py` (new): REST routes.
- Migration adds `entity_extras` table + `entity_extras_fts`.
- `frontend/src/routes/library/EntityDetail/ExtrasTable.tsx` (new).
- `frontend/src/routes/campaign/SideHud/PresentCastChip.tsx`: render pinned-extras chips.
- `frontend/src/api/extras.ts` (new): client.
