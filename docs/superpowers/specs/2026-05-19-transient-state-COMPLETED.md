## Transient State — Design

> **Status:** SHIPPED. Initial implementation landed on 2026-05-19; the gap-closure pass tracked by `../plans/2026-05-19-transient-state-finish.md` (audit fields on `TurnAudit`, `RoutingSummary` writes/conflicts/promoted_to_fact, reinforcement detection + continuity promotion, LLM extractor `transient_updates` schema + plumbing through `ExtractionResult`, orchestrator wiring via `route_transient_updates` + audit fragment) is also complete in code.

**Source idea:** `specs/new/transient-state.md`
**Module:** `backend/src/grimoire/transient_state/` (new)
**Migration:** `026_transient_state.sql` (the design said 024, but 024/025 were claimed by other work before this landed)

## Purpose

Capture per-entity per-turn ephemeral state — mood, intent, current action, posture, internal thought, focus, relationship tone, energy, ambient mood, alert level — in SQLite. It's too noisy for markdown files, too valuable to lose, and weaker than canonical facts (conflicts surface for review rather than auto-resolve).

Today, `backend/src/grimoire/types/state.py` defines `CharacterState` / `LocationState` / `FactionState` as coarse per-entity singletons (`emotional_state`, `physical_state`, `immediate_intent` are JSON blobs on one row per entity per branch). That layer stays — it's what canonical extractor + state-store currently consume. This spec adds a parallel **field-level** store underneath: per-field rows with provenance, confidence, expiry, and supersession history.

Two stores, one direction of promotion: ephemeral field-level rows → coarse summary on the existing tables only when a write is reinforced enough to count as "settled."

## Scope (what changes)

- **New SQLite tables** (migration 024): `transient_character_state`, `transient_location_state`, `transient_faction_state`, `transient_scene_state`. One row per `(campaign_id, entity_id, field)` "current value", with prior rows preserved via `superseded_by`.
- **New service** `grimoire.transient_state.TransientStateService` exposing the `TransientState` protocol (`get`, `set`, `clear`, `history`).
- **Extractor wiring** — `ExtractionResult` gains a typed `transient_updates: list[TransientUpdateProposal]` field (per Theme E decision); the existing `StateDelta` flow is untouched.
- **Context Builder integration** — a new spotlight-tier `_TierItem` per present character renders the compact stanza (`mood / intent / action / thinking`), gated by the per-character privacy frontmatter introduced here.
- **Privacy model lives here** (per Theme B decision) — `Character.privacy.internal_thoughts.{surface_in_hud, surface_inline, surface_in_context}` plus campaign presets are owned by the Transient State spec; `scene-hud`, `narrative-extras`, and `context-inspector` consume the resolved view via a helper exposed from this module.
- **Promotion to facts** — explicit Continuity contract: `TransientStateService.promote_to_fact(...)` writes through `ContinuityService.add_fact` and supersedes the transient row.

Out of scope: vacuum schedule (background job + retention defaults are stated, but the worker lives in `observability`/maintenance, not here); UI for conflict resolution (only the read-time conflict flag + audit trail are owned here).

## Storage

```sql
CREATE TABLE transient_character_state (
    id             INTEGER PRIMARY KEY,
    campaign_id    TEXT    NOT NULL,
    branch_id      TEXT    NOT NULL,
    entity_id      TEXT    NOT NULL,        -- character_ref
    field          TEXT    NOT NULL,
    value          TEXT    NOT NULL,        -- JSON (scalar or {} / [])
    provenance     TEXT    NOT NULL,        -- enum below
    source_post_id TEXT,                    -- nullable
    confidence     REAL    NOT NULL DEFAULT 1.0,
    created_at     TEXT    NOT NULL,        -- ISO-8601 wall clock
    expires_at     TEXT,                    -- nullable: lazy decay
    superseded_by  INTEGER REFERENCES transient_character_state(id),
    in_game_at     TEXT                     -- in-game timestamp at write
);

CREATE INDEX ix_tcs_current
    ON transient_character_state(campaign_id, branch_id, entity_id, field)
    WHERE superseded_by IS NULL;

CREATE INDEX ix_tcs_supersedes
    ON transient_character_state(superseded_by)
    WHERE superseded_by IS NOT NULL;
```

`transient_location_state`, `transient_faction_state`, `transient_scene_state` mirror this shape (substitute the foreign-key column for `scene_id`/`location_ref`/`faction_ref` as `entity_id`).

**Provenance enum** (string): `extractor:auto`, `extractor:reviewed`, `user:hud`, `user:edit`, `mechanics:<module-id>`.

**Decay** is computed lazily on read. Reads filter `WHERE superseded_by IS NULL AND (expires_at IS NULL OR expires_at > :now)`. Wall-clock time is the default; per-field overrides (mood: 1 in-game hour) compare against `in_game_at` resolved through `TimeEngineService.now(campaign_id)`. The decay table lives in code (`grimoire.transient_state.decay.DEFAULT_DECAY`) and is overridable via `TransientStateConfig` (campaign YAML).

## Built-in fields and decay defaults

| Entity | Field | Lifetime |
|---|---|---|
| character | `mood` | 10 posts OR 1 in-game hour, whichever first |
| character | `intent` | 5 posts OR next scene boundary |
| character | `current_action` | 1 post |
| character | `posture` | 3 posts |
| character | `internal_thought` | 1 post |
| character | `focus_of_attention` | 2 posts |
| character | `relationship_tone_toward_pc` | scene-scoped; reinforced extends |
| character | `energy_level` | until next sleep/rest delta from Time Engine |
| location | `ambient_mood`, `noteworthy_detail`, `occupancy_summary` | scene-scoped |
| faction | `alert_level`, `internal_mood` | persists until changed |
| scene | `emotional_temperature`, `dominant_mood`, `pacing` | per-scene only |

`transient_extra.<key>` is the escape hatch — mechanics modules can write under `transient_extra.blood_pool`, `transient_extra.morality`, etc., without schema migration. Per Theme D (per-spec manifest extension), formal declaration in `manifest.yaml` is deferred to a later transient-state-vocab follow-up; until then, modules write at their own risk and reads return raw JSON values.

**Per-campaign / per-mechanics override** of lifetimes lives in `data/campaigns/<id>/transient.yaml` (created on first override; absent = defaults). Schema:

```yaml
decay:
  character:
    mood: { posts: 20, in_game_hours: 2 }
    relationship_tone_toward_pc: { scene_scope: true, reinforce_extends: true }
```

Decay overrides are read by `TransientStateService` at construction and re-read on file watcher events.

## Write priority and conflicts

Per spec: **user > mechanics > extractor**. Losing writes are preserved via `superseded_by` (not discarded). A `set()` call:

1. SELECT current row for `(entity_id, field)` where `superseded_by IS NULL`.
2. If current `provenance` outranks incoming, **insert the new row with `superseded_by` already pointing at the prior current** (the prior stays current, the new row is preserved as history). Surface a conflict via `TransientStateService.list_conflicts(campaign_id)` for the user to resolve.
3. Otherwise, insert new row + `UPDATE prior SET superseded_by = new.id`.

`list_conflicts` returns rows where there's a `extractor:auto` insert that lost to a `user:*` write within the last N (config) posts. Powers a HUD-side conflict surface (the actual UX is owned by `scene-hud`).

**Concurrency** — SQLite transaction wraps the SELECT + INSERT + UPDATE. With a single-writer process (the FastAPI app), this is sufficient; the in-flight race in the open question is addressed by the transaction boundary, not application-level CAS.

## Extractor integration

`ExtractionResult` gains:

```python
@dataclass
class TransientUpdateProposal:
    entity_kind: Literal["character", "location", "faction", "scene"]
    entity_id: str
    field: str
    value: JSON
    confidence: float
    evidence: str            # post excerpt
    proposed_decay_override: Optional[DecayHint] = None

@dataclass
class ExtractionResult:
    ...
    transient_updates: list[TransientUpdateProposal] = field(default_factory=list)
```

Routing (in `extractor/service.py` after merge):
- `confidence >= extractor.auto_apply_threshold` → `TransientStateService.set(provenance="extractor:auto", confidence=..., source_post_id=...)`.
- `confidence >= extractor.review_threshold` → enqueue in the existing `review_queue` table with `kind="transient_update"`; user approves → `set(provenance="extractor:reviewed")`.
- Below review threshold → discarded.

The existing `StateDelta` flow (CHARACTER_STATE_UPDATE etc.) is **unchanged** and still writes to the coarse `character_state` table. The new typed candidate list is additive.

Promotion-to-fact escalation: when the same `(entity_id, field, value)` is reinforced across N (default 5) consecutive posts, the extractor adds a `TransientUpdateProposal` with `proposed_decay_override=PROMOTE_TO_FACT`. Routing dispatches to `ContinuityService.add_fact` via the standard contradiction-check path; on success, `TransientStateService.supersede_with_fact(transient_id, fact_id)` records the supersession.

## Read interface

```python
class TransientState(Protocol):
    async def get(
        self,
        campaign_id: str,
        entity_kind: EntityKind,
        entity_id: str,
        field: str | None = None,
        *,
        branch_id: str | None = None,
        for_observer: ObserverKind | None = None,    # filters privacy
    ) -> dict[str, TransientValue] | TransientValue | None: ...

    async def get_bulk(
        self,
        campaign_id: str,
        entity_kind: EntityKind,
        entity_ids: list[str],
        fields: list[str] | None = None,
        *,
        for_observer: ObserverKind | None = None,
    ) -> dict[str, dict[str, TransientValue]]: ...

    async def set(
        self,
        campaign_id: str,
        entity_kind: EntityKind,
        entity_id: str,
        field: str,
        value: JSON,
        *,
        provenance: Provenance,
        confidence: float = 1.0,
        source_post_id: str | None = None,
        branch_id: str | None = None,
    ) -> TransientValue: ...

    async def clear(
        self,
        campaign_id: str,
        entity_kind: EntityKind,
        entity_id: str,
        field: str | None = None,
        *,
        reason: str = "user:reset",
        branch_id: str | None = None,
    ) -> None: ...

    async def history(
        self,
        campaign_id: str,
        entity_kind: EntityKind,
        entity_id: str,
        field: str,
        limit: int = 20,
        *,
        branch_id: str | None = None,
    ) -> list[TransientValue]: ...

    async def promote_to_fact(
        self,
        campaign_id: str,
        entity_kind: EntityKind,
        entity_id: str,
        field: str,
        *,
        evidence: str,
        turn_id: str,
    ) -> tuple[FactId, TransientValueId]: ...

    async def list_conflicts(
        self,
        campaign_id: str,
        *,
        within_posts: int = 10,
    ) -> list[TransientConflict]: ...
```

`TransientValue` is a small dataclass wrapping the row plus a `decayed: bool` flag (False unless caller passed an explicit "include decayed" knob). The bulk helper is the HUD's primary entry point (target: 5 chars × 8 fields under 50 ms p95).

## Privacy model (owned here)

Per Theme B: this spec owns the privacy schema; HUD / context / extras / inspector are consumers.

Per-character frontmatter (Character schema gains, with backwards-compatible defaults):

```yaml
privacy:
  internal_thoughts:
    surface_in_hud: true       # show in HUD chip bubble
    surface_inline: true       # show in scene-pane bubble next to post
    surface_in_context: true   # allow Context Builder to include
```

**Default = all true** (solo / co-author mode, the common case). A campaign-level preset in `data/campaigns/<id>/privacy.yaml` overrides per-character defaults; the helper `grimoire.transient_state.privacy.resolve(character, campaign_id, observer)` returns the effective `{hud, inline, context}` triple. Observer kinds: `author` (always all-true), `pc_owner` (the PC's player sees their own thoughts unconditionally), `other_pc`, `audience` (read-only viewer).

POV mode auto-hides NPC `internal_thought` from `surface_in_*` regardless of frontmatter — implemented by special-casing the observer (`audience` + POV → strip).

Privacy enforcement is applied in `TransientState.get*` when `for_observer` is supplied. Callers that pass `None` (server-internal reads) bypass the filter; the HUD aggregator, context builder, and inline post renderer always pass the observer kind. This keeps the privacy boundary at the data layer rather than relying on each surface to filter independently — drift-proof.

## Reset triggers

- Scene end: any field configured with `scene_scope: true` decays immediately.
- Time skip ≥ 24h (detected via Time Engine event): mood, intent, posture default-reset; configurable.
- Manual "fresh start" — `TransientStateService.clear(field=None)` per entity.

Scene end is detected via the existing `scene_ended` event on the event bus (`backend/src/grimoire/api/stream.py`). The TransientStateService subscribes during construction; the handler walks scene-scoped fields for the closing scene's location + present characters and inserts `expires_at = now()` updates.

## Wiring

- App startup in `backend/src/grimoire/main.py` constructs `TransientStateService(database, time_engine, library, continuity, event_bus)` after the existing state-store wiring.
- The Context Builder gains a `transient_state: TransientStateService | None` constructor arg; when present, `_resolve_cast()` (`backend/src/grimoire/context/builder.py:384`) emits an extra `_TierItem` per present character (priority 8.5 — between voice anchor and recent dialogue).
- The Extractor service adds `transient_state` similarly; `service.py` after the merge step routes the new typed list.
- HUD aggregator (specified in `scene-hud-design.md`) calls `get_bulk(for_observer=...)` for the present-cast widget.
- Narrative-extras pinning surfaces `transient_extra.*` rows as HUD chips by querying through the same path.

## REST surface

```
GET    /campaigns/{id}/entities/{kind}/{eid}/transient
GET    /campaigns/{id}/entities/{kind}/{eid}/transient/{field}
PATCH  /campaigns/{id}/entities/{kind}/{eid}/transient/{field}
DELETE /campaigns/{id}/entities/{kind}/{eid}/transient[/{field}]
GET    /campaigns/{id}/entities/{kind}/{eid}/transient/{field}/history
POST   /campaigns/{id}/entities/{kind}/{eid}/transient/{field}/promote-to-fact
GET    /campaigns/{id}/transient/conflicts
```

Writes via PATCH route through `provenance="user:edit"` with `confidence=1.0`. The HUD widget edits go through the canonical owner endpoint per `scene-hud-design.md` (the canonical owner for `mood` / `intent` / `current_action` is the Characters service which delegates here — owning service is Characters, storage is Transient State).

## Configuration

```yaml
transient_state:
  auto_apply_threshold: 0.85       # extractor → set(extractor:auto)
  review_threshold: 0.60           # extractor → review queue
  promote_to_fact:
    reinforcement_count: 5         # consecutive posts with new evidence
    require_evidence_diversity: true
  conflict_window_posts: 10
  vacuum:
    enabled: true
    retain_superseded_days: 30     # background worker; not owned here
  decay:
    overrides_path: data/campaigns/{id}/transient.yaml
```

## Performance targets

- Single-entity bundle read with privacy filter: < 5ms.
- Bulk HUD aggregation (5 entities × 8 fields × privacy filter): < 50ms p95.
- Write (insert + supersede update): < 10ms.

## Audit and observability

- Every `set()` records via the existing observability turn-audit path: a `transient_state_write` JSON entry on `turn_audits.applied_deltas` carrying `(entity_kind, entity_id, field, old_value_id, new_value_id, provenance, confidence)`.
- Conflicts surfaced via `list_conflicts` also live as `transient_state_conflict` audit entries.
- Vacuum runs emit `transient_state_vacuum` summaries: rows reclaimed, by table.

## Test wiring

`backend/tests/transient_state/` (new):
- `test_storage.py` — supersession, conflict-preservation, privacy filter integration.
- `test_decay.py` — wall-clock, in-game time, scene-scope, override file.
- `test_promotion.py` — reinforcement detection + Continuity round-trip.
- `test_extractor_integration.py` — typed candidate routing, auto/review/discard.
- `test_privacy.py` — `resolve()` helper across observer kinds + POV mode.
- `test_bulk_read.py` — perf target check (asserts under threshold on a 5×8 fixture).

Conftest: a fresh `Database` + `StateStore` + `LibraryService` + `ContinuityService` + `TransientStateService` per test under `tmp_path`.

## Open items deferred (not blockers for v1)

- Vacuum worker schedule (the spec mandates it exists; the daemon lives in `observability`).
- Mechanics manifest formal declaration of `transient_extra.<key>` field ownership (per Theme D, deferred).
- Cross-branch reads (`branch_id` argument is wired through but tests focus on a single branch).
- HUD-side conflict resolution UX (owned by `scene-hud-design.md`).
