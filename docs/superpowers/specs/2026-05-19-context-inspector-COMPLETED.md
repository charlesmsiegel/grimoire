## Context Inspector — Design

> **Status:** SHIPPED. `backend/src/grimoire/context/inspector.py` defines `ContextInspector` with `preview` / `explain` / `pin` / `exclude` / `diff`; `types/inclusion_reasons.py` carries the closed 18-reason enum; migration `027_context_pins.sql` adds the pin store; REST surface lives in `api/context.py` with `/context/preview`, `/preview/{handle}`, `/preview/{handle}/explain`, `/pins`, `/pins/{id}`, `/diff`.

**Source idea:** `specs/new/context-inspector.md`
**Module:** `backend/src/grimoire/context/inspector.py` (new), additions to `backend/src/grimoire/context/builder.py` and `backend/src/grimoire/observability/`

## Purpose

A pre-flight, live counterpart to the post-hoc "what did the model see?" turn-trace view (`GET /turns/{turn_id}/prompt` in `api/observability.py`). The inspector lets the user see what the next turn's prompt will look like given current state and the draft input, while typing. Crucially: it surfaces **why** each chunk was included, supports user **pins** and **excludes** with TTL, and **diffs** previews against a prior turn.

This is the upgrade that `observability-COMPLETED.md §7` ("Why this character?") was deferred for. Inspector lives in the Context Builder module since it's tightly coupled to assembly internals.

## Scope (what changes)

- **Inclusion reasons** on every chunk: extend `ContextSource` with a `inclusion_reasons: list[InclusionReason]` field. Reasons compose (a character can be both `present_in_scene` and `commitment_open_to_pc`).
- **`ContextInspector` service**: `preview`, `explain`, `pin`, `exclude`, `clear_pin`, `clear_exclude`, `diff`.
- **`ContextPreview` payload**: same shape as `AssembledPrompt` plus the inclusion-reason annotations and per-tier budget warnings. **Not a separate type — preview reuses `AssembledPrompt`** with the new field. (See decision below.)
- **User overrides**: `context_pins` SQLite table with rows that affect *whether* an entity drops out under budget pressure (no tier reordering, no inline editing).
- **`PreviewHandle`**: session-scoped UUID identifying an in-memory preview; lifecycle and eviction defined below.
- **REST + WebSocket**: preview endpoints, pin/exclude endpoints, diff endpoint, debounce config.
- **Frontend panel**: token bars per tier, click-through to inclusion reasons, source attribution, debounced live update on draft input, pin/exclude with TTL selector, diff toggle.

## Design choices made (open questions resolved)

**Inclusion-reason placement (Q1):** Extend `ContextSource.inclusion_reasons: list[InclusionReason]` (composable list). Drop the parallel `InclusionAnnotation` type — one less indirection, the source already knows its reasons by the time it's built. The list is allowed to be empty (legacy / unannotated sources).

**ContextPreview vs new type (Q5):** Reuse `AssembledPrompt`. The preview *is* an `AssembledPrompt` produced in dry-run mode (deltas not applied to state, no commitments materialized) plus the inclusion-reason annotations. A `PreviewHandle` is a UUID keyed lookup into an in-memory `OrderedDict[handle_id, AssembledPrompt]` with LRU eviction. This avoids a parallel type and ensures the preview is byte-identical to what the real turn would assemble.

**PreviewHandle persistence (Q2):** In-memory LRU cache, max 50 entries per process, 15 min idle TTL. Handles are session-scoped (frontend includes its session id; cache key is `(session_id, handle_id)`). No persistence across server restart; the frontend re-creates a preview on reconnect.

**Pin/exclude TTL semantics (Q3):** Relative turn count — "pin for 3 turns" means apply to the next 3 canonical turns, then auto-clear. Stored as `expires_at_turn_id` (the turn id after which the pin is no longer applied; computed at write time as `current_turn_id + ttl_turns`). On every `build()`, the Context Builder filters pins where `expires_at_turn_id IS NULL OR expires_at_turn_id > current_turn_id`. Idempotent re-creates of a pin extend the TTL (the user can keep refreshing).

**Diff computation (Q4):** Both shapes supported:
- `diff(handle_a, handle_b)` — two live previews.
- `diff(turn_id, turn_id)` — two prior canonical turns from `turn_audits`.
- `diff(turn_id, handle)` — mixed: prior turn vs current preview (the most useful case for "how did my draft input change things").

**User override behavior (Q6):** Pinning does not reorder tiers — it *prevents* drop. Specifically: pinned entities are exempt from budget-driven eviction (they survive the truncation step in `_pack_tier`). Excluding *removes* an entity from the candidate set before assembly. Neither touches tier assignment; the spec explicitly disallows reordering.

**Debounce / live preview config (Q7):** Configuration lives in `ContextBuilderConfig` (the existing `context/config.py`). Frontend has its own UI debounce as well; the server's `debounce_ms` is a guard against drive-by spam and applies to the `/preview` endpoint specifically.

**Audit trail (Q8):** `context_pin_applied` / `context_pin_cleared` audit entries are recorded in the existing `turn_audits.applied_deltas` blob as JSON entries (no separate audit table). Reuses the established audit pipeline; lightweight.

## Inclusion-reason vocabulary

```python
class InclusionReason(StrEnum):
    PRESENT_IN_SCENE            = "present_in_scene"
    MENTIONED_IN_RECENT_POSTS   = "mentioned_in_recent_posts"
    COMMITMENT_OPEN_TO_PC       = "commitment_open_to_pc"
    KEYWORD_TRIGGERED           = "keyword_triggered"
    RELATIONSHIP_TO_PRESENT     = "relationship_to_present"
    PINNED_BY_USER              = "pinned_by_user"
    SCENE_ANCHOR                = "scene_anchor"
    MECHANICS_RELEVANT          = "mechanics_relevant"
    STYLE_GUIDE_ACTIVE          = "style_guide_active"
    PC_CARD                     = "pc_card"
    COMPOSITION_DEFAULT         = "composition_default"
    EXTRAS_PINNED_TO_HUD        = "extras_pinned_to_hud"        # cross-spec hook to narrative-extras
    EXTRAS_DEFAULT_VISIBLE      = "extras_default_visible"
    LORE_BEFORE_CAST            = "lore_before_cast"            # cross-spec hook to card-imports
    LORE_AFTER_CAST             = "lore_after_cast"
    LORE_AT_DEPTH               = "lore_at_depth"
    LORE_ARCHIVE                = "lore_archive"
    TRANSIENT_STATE_ACTIVE      = "transient_state_active"      # cross-spec hook to transient-state
```

Vocabulary is a closed enum at the codebase level. Adding a new reason is a deliberate change (touches the enum + the source-emitting site). Reasons are emitted by the relevant assembly steps in `builder.py` (e.g., `_resolve_cast` emits `PRESENT_IN_SCENE` and possibly `MENTIONED_IN_RECENT_POSTS`; the commitments loader emits `COMMITMENT_OPEN_TO_PC`).

## Service interface

```python
class ContextInspector:
    async def preview(
        self,
        campaign_id: str,
        player_input: str,
        *,
        branch_id: str,
        pc_ref: str,
        session_id: str,
        for_observer: ObserverKind | None = None,
    ) -> tuple[PreviewHandle, ContextPreviewSummary]: ...
    """
    Dry-run build. Returns a handle + a summary suitable for the frontend
    (per-tier token totals, source counts, warnings). Full per-chunk detail
    fetched via `explain(handle)`.
    """

    async def get(self, session_id: str, handle: PreviewHandle) -> AssembledPrompt: ...

    async def explain(
        self,
        session_id: str,
        handle: PreviewHandle,
    ) -> list[ContextSourceExplanation]: ...
    """
    Per-source detail: tier, kind, scope, owner, version, inclusion_reasons,
    token estimate, summary text. Drives the click-through UI.
    """

    async def pin(
        self,
        campaign_id: str,
        *,
        target: PinTarget,         # by source_id or by (entity_kind, entity_id)
        ttl_turns: int | None = None,    # None = until manually cleared
        actor: str = "user",
    ) -> PinId: ...

    async def exclude(
        self,
        campaign_id: str,
        *,
        target: PinTarget,
        ttl_turns: int | None = None,
        actor: str = "user",
    ) -> PinId: ...

    async def clear_pin(self, campaign_id: str, pin_id: PinId) -> None: ...
    async def clear_exclude(self, campaign_id: str, pin_id: PinId) -> None: ...

    async def list_active(
        self,
        campaign_id: str,
    ) -> list[ContextPin]: ...

    async def diff(
        self,
        *,
        a: PreviewHandle | TurnId,
        b: PreviewHandle | TurnId,
        session_id: str | None = None,
    ) -> ContextDiff: ...
```

`PinTarget` is either `PinTarget.source(source_id: str)` or `PinTarget.entity(entity_kind: EntityKind, entity_id: str)`. The entity form is more durable — `source_id` may change between assemblies; entity-based pins survive across turns.

`ContextDiff` shape:
```python
@dataclass
class ContextDiff:
    entities_added: list[ContextSourceExplanation]
    entities_removed: list[ContextSourceExplanation]
    entities_changed_tier: list[ContextSourceExplanation]   # not yet possible (no reorder) but reserved
    budget_shifts: dict[ContextTier, int]                    # token delta per tier
    source_version_changes: list[SourceVersionChange]        # library entity bumped, override added/removed
    rolls_deltas: list[RollChange]                           # mechanics rolls (when modes diverge)
```

## Storage

Migration adds `context_pins`:

```sql
CREATE TABLE context_pins (
    id                  TEXT PRIMARY KEY,        -- ctx_pin_<uuid>
    campaign_id         TEXT NOT NULL,
    branch_id           TEXT NOT NULL,
    kind                TEXT NOT NULL,           -- pin | exclude
    target_kind         TEXT NOT NULL,           -- source | entity
    target_source_id    TEXT,                    -- non-null when target_kind=source
    target_entity_kind  TEXT,                    -- non-null when target_kind=entity
    target_entity_id    TEXT,                    -- non-null when target_kind=entity
    created_at          TEXT NOT NULL,
    created_by          TEXT NOT NULL,
    created_at_turn_id  TEXT,
    expires_at_turn_id  TEXT,                    -- null = never expires
    cleared_at          TEXT,
    cleared_by          TEXT
);

CREATE INDEX ix_ctx_pins_active
    ON context_pins(campaign_id, branch_id)
    WHERE cleared_at IS NULL;
```

`cleared_at IS NULL` defines "active." A pin with `expires_at_turn_id <= current_turn_id` is logically expired but the row remains for audit; the active-pin query filters on both fields.

## REST surface

```
POST   /campaigns/{id}/context/preview          # body: {player_input, session_id, branch_id, pc_ref}
                                                 # returns: {handle, summary}
GET    /campaigns/{id}/context/preview/{handle} # returns: AssembledPrompt
GET    /campaigns/{id}/context/preview/{handle}/explain    # returns: list of source explanations
POST   /campaigns/{id}/context/pins             # body: {target, kind, ttl_turns?}
DELETE /campaigns/{id}/context/pins/{pin_id}
GET    /campaigns/{id}/context/pins             # active pins for the campaign
POST   /campaigns/{id}/context/diff             # body: {a, b}
                                                 # a, b are either {handle:...} or {turn_id:...}
```

WebSocket: no new events (pins emit audit-only entries; the live preview is request/response).

## Frontend

Inspector panel in the play view (collapsible, off by default):

```
┌─ Context preview ───────────────────────────────────────┐
│ Tier        Tokens     Sources    Budget                 │
│ ────────────────────────────────────────────────────────│
│ LOCK_IN     6,234 ▓▓▓▓▓▓▓▓▓░░░░  8,000                   │
│ SPOTLIGHT  31,508 ▓▓▓▓▓▓▓▓▓▓▓▓░░ 40,000                  │
│ BACKGROUND 18,902 ▓▓▓▓▓▓░░░░░░░  30,000                  │
│ ARCHIVE     4,512 ▓▓░░░░░░░░░░░  20,000                  │
│                                                           │
│ ⚠ 2 spotlight characters near budget edge                │
│                                                           │
│ Diff vs last turn:  +winifred Allard  -julian Bain        │
│                                                           │
│ Sources (click for inclusion reasons):                   │
│   ▸ winifred Allard            spotlight   1,820 tok 📌   │
│   ▸ Henry Davies               spotlight   1,420 tok      │
│   ▸ Lore: "The Tremere"        background    340 tok      │
│   ...                                                     │
└──────────────────────────────────────────────────────────┘
```

Per source row:
- Click → expands to show inclusion reasons + tier + version + summary text.
- Pin/Unpin button: opens TTL picker (presets: "this turn", "3 turns", "indefinite") + writes the pin.
- Exclude button: same shape.

Diff toggle: switches the view to a two-column comparison (left = prior turn, right = current preview), with adds/removes highlighted.

Debounced live update: every 500 ms while the draft input changes (server `debounce_ms` enforces a floor of 250 ms).

## Determinism guarantee

Because pins/excludes are applied **pre-assembly** (in the candidate-set filter) and never reorder tiers, the existing deterministic assembly invariants hold. A preview built at turn N with the same input + same active pins as a canonical turn would assemble the same prompt — the inspector is byte-equivalent to the canonical path. Spec invariant: an internal test asserts `inspector.get(handle) == canonical.build(same inputs)` for the no-pin case.

## Cross-spec hooks

- **`transient-state`**: privacy helper used when building `ContextSourceExplanation` to filter internal-thought details for non-author observers.
- **`narrative-extras`**: the `EXTRAS_*` inclusion reasons are emitted by the extras-stanza tier item builder.
- **`card-imports`**: the `LORE_*` inclusion reasons match lore positions; emitted by `_route_lore_to_tier`.
- **`scene-hud`**: the HUD's `/diagnostics` route could surface "live context budget" by polling the inspector. Not wired in v1; reserved.

## Performance

- `POST /preview` (full assembly): ~150–300 ms p95 (same cost as canonical build minus mechanics).
- `GET /preview/{handle}` cache hit: < 10 ms.
- `GET /preview/{handle}/explain`: < 20 ms (materialized from the cached prompt).
- `POST /diff`: < 50 ms for two cache hits; 300 ms when one operand is a turn_id requiring `turn_audits` fetch.
- `POST /pins`: < 20 ms (single insert).

## Failure handling

| Failure | Behavior |
|---|---|
| Handle expired / unknown | 404; UI re-creates a fresh preview |
| Preview build fails | 500 with stage-of-failure; surface in UI; no pin state corrupted |
| Pin target unresolvable at build time | Pin remains in DB; explain payload notes "pin target not found this turn"; budget not affected |
| Diff against non-existent turn_id | 404 |
| LRU evicts active handle | Subsequent fetch → 404; UI re-creates |
| Concurrent pin/clear on same id | Row-level last-write-wins; audit records both |

## Test wiring

`backend/tests/context/test_inspector.py` (new):
- Preview → handle returned; full prompt retrievable.
- Determinism: preview equals canonical build for the no-pin case.
- Pin extends entity survival across truncation; verified by building a budget-tight fixture.
- Exclude removes entity from preview.
- TTL expiry: pin created at turn N with ttl=3 stops applying at turn N+4.
- Diff: two previews differ only in `player_input` and inspector surfaces the delta.
- Diff: preview vs prior turn detects added/removed entities.

`backend/tests/context/test_inclusion_reasons.py`:
- Each assembly step emits the documented reason on the right `ContextSource`.
- Reasons compose (multi-reason sources).
- Reason filter unaffected by per-call observer.

`backend/tests/api/test_context_inspector_routes.py`:
- All 7 routes round-trip.
- Session isolation: handle from session A is not accessible to session B.

## Wiring touchpoints

- `backend/src/grimoire/types/context.py:ContextSource`: add `inclusion_reasons: list[InclusionReason]`.
- `backend/src/grimoire/context/inspector.py` (new): inspector service.
- `backend/src/grimoire/context/builder.py`: emit reasons on every `_TierItem.source`; honor `context_pins` in candidate filter and truncation steps.
- `backend/src/grimoire/context/config.py:ContextBuilderConfig`: add `inspector_debounce_ms`, `inspector_handle_ttl_minutes`.
- `backend/src/grimoire/api/context.py` (new): routes.
- Migration adds `context_pins` table.
- `frontend/src/routes/campaign/Inspector/` (new): panel, source list, pin/exclude controls, diff view, debounce-driven preview hook.
- `frontend/src/api/inspector.ts` (new): client.

## Out of scope (v1)

- Inline edit of prompt fragments.
- Tier reordering.
- Template overrides (custom system-prompt rewriting per-campaign).
- Persisted previews (handles are session-scoped only).
- Cross-campaign comparisons.
- AI-assisted "why is this character missing?" explanations (the explain endpoint is data-driven; LLM-narrated explanations are a future polish).
