## Lore Entry Reclassification — Design

> **Status:** SHIPPED 2026-05-19. Sections 1, 2, 3, 5, 6 implemented in `feat(reclassify): …` commits on branch `2026-05-19-lore-reclassification`. Plan at `docs/superpowers/plans/2026-05-19-lore-reclassification.md`.
>
> **Section 4 (import-dialog integration) intentionally deferred.** The shared transform lives at `backend/src/grimoire/library/reclassify.py:apply_mapping` and the classifier at `classify.py:suggest_kind`, both ready for `card-imports` Task E2 to import. When card-imports lands and the LoreEntry model gains `priority`/`probability`/etc., the dropped-matching-metadata warning in `apply_mapping` will start firing automatically (the field-default sentinels are already in place).

**Source idea:** discussion follow-up to card-imports — imported `character_book` entries often describe Characters, Locations, Factions, or Items, but the importer writes everything as `LoreEntry`. Users need a way to promote a lore entry into the right entity type without re-typing it.

**Module:** `backend/src/grimoire/library/`, `backend/src/grimoire/characters/`, `frontend/src/routes/library/`

## Purpose

After a SillyTavern card import (or any other source of bulk lore), the library typically ends up with a pile of `lore/*.md` files where a meaningful fraction are really NPCs, places, or organizations. Today the only fix is hand-editing: delete the lore file, create a new `Character` / `Location` / `Faction`, copy the body across, lose the keyword/priority/probability metadata. This spec adds a first-class reclassification flow — one click in the library UI, or a checkbox in the import dialog — that converts a `LoreEntry` into the target entity type with a defined field mapping and a reversible audit trail.

## Scope (what changes)

1. **Conversion service** — `LibraryService.reclassify_entity(source_id, target_kind, overrides)` that reads the source, maps fields to the target schema, writes the new entity, and deletes the source in a single atomic step.
2. **Field-mapping table** — canonical lore → {character, location, faction, item} mappings, with explicit "kept", "dropped", and "merged into notes" buckets per target.
3. **Heuristic classifier** — a cheap rule-based suggester (`suggest_kind(lore_entry) -> tuple[EntityKind, float, str]`) that surfaces a recommended target + confidence + reason, used in both the import dialog and the standalone library UI.
4. **Import dialog integration** — Task E2's lore list grows a per-row category dropdown; default = "Lore" but seeded with the heuristic suggestion when confidence is above a threshold.
5. **Library UI "Convert to…" action** — a menu entry on any existing `LoreEntry` row that opens a conversion modal showing the field mapping preview before committing.
6. **Audit trail** — every reclassification appends to `data/library/imports/reclassifications.jsonl` with source id, target id, kind, timestamp, and the overrides applied. Enables undo and surfaces in observability.

## 1. Conversion service

```python
class LibraryService:
    async def reclassify_entity(
        self,
        setting_id: str,
        source_id: str,
        *,
        target_kind: EntityKind,
        overrides: dict[str, Any] | None = None,
        actor: str = "user",
    ) -> ReclassificationResult:
        """
        Convert source_id (any entity, in practice usually a LoreEntry) into a
        new entity of target_kind.

        - Reads source.
        - Applies the field-mapping table (see §2) plus any per-field overrides.
        - Writes new entity via create_entity (collision suffix on slug).
        - Deletes source.
        - Appends an audit record.

        Atomic-ish: if create fails, source is left intact; if delete fails
        after create, returns the new id and a non-fatal warning.
        """
```

```python
@dataclass(frozen=True)
class ReclassificationResult:
    source_id: str
    target_id: str
    target_kind: EntityKind
    fields_kept: list[str]
    fields_dropped: list[str]      # with reasons in warnings
    fields_into_notes: list[str]
    warnings: list[str]
```

`EntityKind` is the existing library-kind enum (`character`, `location`, `faction`, `item`, `lore`, `greeting`, …). v1 supports `lore → {character, location, faction, item}` only; the reverse and lateral moves are out of scope.

## 2. Field-mapping table

Each row says what happens to a `LoreEntry` field when converting to the target. Anything not in the kept/merged columns goes into the new entity's `notes` field (if present) prefixed by the field name, otherwise dropped with a warning.

| LoreEntry field    | → Character             | → Location              | → Faction               | → Item                  |
|--------------------|-------------------------|-------------------------|-------------------------|-------------------------|
| `id`               | new id (slug from title)| new id                  | new id                  | new id                  |
| `title`            | `name`                  | `name`                  | `name`                  | `name`                  |
| `body`             | `description`           | `description`           | `description`           | `description`           |
| `keywords`         | `aliases`               | `aliases`               | `aliases`               | `aliases`               |
| `secondary_keys`   | → notes                 | → notes                 | → notes                 | → notes                 |
| `tags`             | `tags`                  | `tags`                  | `tags`                  | `tags`                  |
| `related_factions` | `factions`              | → notes                 | `allies` (best-effort)  | → notes                 |
| `secrecy`          | `secrecy`               | `secrecy`               | `secrecy`               | dropped                 |
| `comment`          | → notes                 | → notes                 | → notes                 | → notes                 |
| `import_source`    | preserved               | preserved               | preserved               | preserved               |
| `priority`, `probability`, `position`, `at_depth`, `scan_depth`, `constant`, `enabled`, `case_sensitive`, `match_whole_words`, `selective_logic` | dropped (with single rollup warning: "matching metadata discarded") | same | same | same |

The dropped block is the deliberate trade-off: matching/scoring is a `LoreEntry`-only concern. If the user wants the entity to still surface in context, they keep it as `lore`. Reclassifying says "this isn't world background prose — it's an entity in its own right."

`overrides` lets the UI patch any of the above before the write (e.g. user edits `name` away from `title`, fills in `Location.kind`, supplies `Character.stats`). Overrides are the only path for fields the target requires that lore can't supply (e.g. `Location.kind`, `Character.species`); the UI must collect them before the commit button enables.

## 3. Heuristic classifier

Pure-Python, no LLM call. Lives at `backend/src/grimoire/library/classify.py`.

```python
def suggest_kind(entry: LoreEntry) -> Suggestion:
    """Returns top suggestion + confidence in [0, 1] + human-readable reason."""
```

Signals (all cheap):

- **Character** — `title` is one or two capitalized words with no determiners ("Beatrice", "Lady Beatrice"); body contains pronouns (`she/he/they/her/his/their`) above a density threshold; first sentence matches `is a <profession/role>` pattern.
- **Location** — `title` starts with `The ` and contains a place noun ("Keep", "District", "Forest", "Cathedral", "Quarter", "Sept", "Chantry", "Court", "Tower"); body contains directional/spatial language ("located", "north of", "within the", "halls of").
- **Faction** — `title` is a plural ("Tremere", "Camarilla", "Sabbat") or contains "Sect", "Clan", "House", "Order", "Guild", "Court"; body contains organizational language ("members", "ruled by", "founded", "allies").
- **Item** — `title` contains a known artifact noun ("Sword", "Tome", "Amulet", "Grimoire"); body describes properties ("grants", "forged", "imbued").

Each signal contributes a weight; highest-weight kind wins; confidence is `top_weight / sum_weights` clamped to `[0, 1]`. If no signal fires above 0.5, suggestion is `lore` (i.e. "leave it"). Reasons accumulate ("title looks like a proper noun; body uses she/her 6 times").

Lists ("place nouns", "faction nouns", "item nouns") live in `classify.py` as module-level frozensets so they're greppable and easy to extend; future i18n is out of scope.

## 4. Import dialog integration

Extends Task E2 (`ImportDialog.tsx`). Each lore row in the preview gets:

- A category dropdown: `Lore (default) | Character | Location | Faction | Item | Skip`.
- The dropdown defaults to `suggest_kind(entry)` when confidence ≥ 0.6; otherwise `Lore`.
- A "Why?" tooltip surfacing the heuristic reason text when the suggestion is non-default.
- When a non-lore kind is selected, an inline form expands to collect required target-kind overrides (e.g. `Location.kind`, `Character.species` if non-defaulted in the schema).
- `Skip` discards the entry entirely (logged in the import report).

Commit payload extension:

```ts
type CommitOptions = {
  preview_id: string;
  options: IngestOptions;
  lore_overrides?: Array<{
    source_index: number;          // index into IngestedCharacterCard.character_book.entries
    kind: EntityKind | "skip";
    overrides?: Record<string, unknown>;
  }>;
};
```

Backend in `_finalize_import` consults `lore_overrides` before writing each lore entry; if `kind != "lore"`, it builds the target-kind payload from the mapping + overrides and writes via `LibraryService.create_entity(kind=target_kind, …)` instead of as lore. Reclassifications during import do **not** go through `reclassify_entity` (nothing to delete — the source was never written); they share only the mapping + override logic.

## 5. Standalone library UI

New action on any `LoreEntry` row in the library browser: **Convert to…** opens a modal with:

- Target-kind selector.
- Live preview of the mapping table: two columns showing source fields and where each lands, with edit controls on the target side.
- Required-field validation (commit button stays disabled until all target-required fields have values).
- A "what gets discarded" callout listing dropped fields with their reasons.
- Commit calls `POST /library/settings/{sid}/entities/{id}/reclassify`.

Routes:

```
POST /library/settings/{sid}/entities/{id}/reclassify
  body: { target_kind: EntityKind, overrides: Record<string, unknown> }
  returns: ReclassificationResult
GET  /library/settings/{sid}/entities/{id}/reclassify/preview
  query: target_kind=...
  returns: { mapping: [...], required_overrides: [...], suggestion: Suggestion }
```

## 6. Audit trail + undo

Every successful reclassification appends one JSONL line to
`data/library/imports/reclassifications.jsonl`:

```json
{"ts": "2026-05-19T14:23:00Z", "setting_id": "by-night", "source_id": "lore/tremere", "source_snapshot": {...}, "target_id": "factions/tremere", "target_kind": "faction", "overrides": {}, "actor": "user"}
```

`source_snapshot` is the full pre-conversion entity body — enough to recreate it exactly. Undo is a separate route:

```
POST /library/settings/{sid}/reclassifications/{ts}/undo
```

Reads the snapshot, recreates the source via `create_entity` (collision suffix if the slug now clashes), deletes the target, appends an inverse audit record. Undo is best-effort: if downstream entities now reference the target (rare in v1 since lore isn't referenced by id from anywhere else), undo still proceeds and the broken references show up as warnings.

## Cross-spec hooks

- **Card imports (`2026-05-19-card-imports-design.md`)** — Task E2 grows the per-row category dropdown; `_finalize_import` learns to honor `lore_overrides`. Both changes are additive.
- **Library (`2026-05-12-library-design.md`)** — adds `reclassify_entity` + audit log; no schema migration.
- **Observability** — reclassification events emit a structured log entry (`event=library.reclassify`) so dashboards can count "lore → character" conversions over time.

## Configuration

```yaml
library:
  reclassification:
    audit_log: data/library/imports/reclassifications.jsonl
    suggestion_threshold: 0.6     # below this, default to "lore"
    undo_window_days: 30          # entries older than this are pruned from the log
```

## Failure handling

- **Create succeeds, delete fails** — target is live, source still exists. Result returns `warnings=["source not deleted: <reason>"]`; user can retry delete from the UI. No silent data loss.
- **Validation fails on target** — no write, original untouched, surface validation errors to the UI.
- **Audit-log write fails** — non-fatal warning; the conversion itself still succeeds.

## Test wiring

- `backend/tests/library/test_reclassify.py` — round-trip per target kind, override application, atomic-ish failure modes, audit log shape.
- `backend/tests/library/test_classify.py` — heuristic on a fixture set of representative lore entries (Beatrice/Tremere/Elysium/Sword of Caine), assert top suggestion + reason.
- `backend/tests/api/test_reclassify_routes.py` — preview + commit + undo.
- `frontend/src/routes/library/ConvertModal.test.tsx` — mapping preview renders, required overrides gate the commit button.

## Out of scope (v1)

- Reclassifying *into* `lore` from other kinds (the inverse).
- Lateral moves (character ↔ faction, etc.).
- Bulk-select reclassification in the library browser (single-entity at a time only).
- LLM-assisted classification — heuristic only.
- Cross-setting moves.
- Reclassification of `Greeting` entries (they're tied to a character anyway).
