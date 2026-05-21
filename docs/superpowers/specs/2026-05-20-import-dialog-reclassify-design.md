## Import Dialog Reclassification — Design

> **Status:** IN PROGRESS (2026-05-20). Implements section 4 of `2026-05-19-lore-reclassification-COMPLETED.md`, the one slice intentionally deferred when that spec shipped. All other lore-reclassification machinery (the shared `apply_mapping` transform, the `suggest_kind` classifier, the `LibraryConfig.reclassification.suggestion_threshold` setting, the matching-metadata fields on `LoreEntry`) is already in place.

**Source idea:** `TODO.md` — the last open item from the card-imports / lore-reclassification line of work.
**Module:** `backend/src/grimoire/api/imports.py`, `backend/src/grimoire/characters/service.py`, `backend/src/grimoire/library/reclassify.py`, `frontend/src/routes/library/ImportDialog.tsx`, `frontend/src/api/imports.ts`.

## Purpose

Today, every entry in an imported card's `character_book` is written as a `LoreEntry`, even when the entry is plainly a Character ("Lyra, ranger of the Northern Hold"), a Location ("Brackhollow Inn"), a Faction ("The Silver Cradle"), or an Item ("Ashroot Talisman"). The user can convert lore entries one-by-one after the fact via the existing Convert modal — but for a card with 20 entries that is a lot of churn. The import dialog already shows the parsed entries before commit; this spec puts the per-row category dropdown there so the user can promote entries at import time, before any lore file gets written.

## Scope (what changes)

1. **Preview response carries suggestions.** When the importer parses a card, the backend runs `suggest_kind` over each ingested lore row and returns a parallel `lore_suggestions: list[{source_index, kind, confidence, reason}]` on the preview response. The classifier already applies the configured threshold (returning `kind="lore", confidence=0.0` when no signal is strong enough), so the frontend can use `kind` as the dropdown default directly without re-applying any cutoff. No new endpoint, no per-row HTTP round-trip.
2. **Commit payload accepts overrides.** `CommitPayload` grows `lore_overrides: list[LoreOverride]`, each `{source_index, kind, overrides}`. Per-entry semantics: `kind="lore"` (or absent) writes lore as today; `kind="skip"` discards the entry and logs a warning; `kind in {character, location, faction, item}` runs `apply_mapping` and writes the target-kind entity via `LibraryService.create_entity` instead of as lore.
3. **`_write_lore_entries` learns the override branch.** A single dispatch site picks lore vs target-kind vs skip per row; lore-as-lore stays bit-for-bit identical to today.
4. **Frontend dropdown.** Each row in `ImportDialog.tsx`'s lore preview list grows a `<select>` (Lore / Character / Location / Faction / Item / Skip), seeded from the server-supplied suggestion when `confidence >= threshold`. A "Why?" hover surfaces the heuristic reason. Required overrides (only `Location.kind` today, surfaced via `required_overrides_for(target_kind)` mirrored to the frontend) expand inline when a target needing them is picked; commit is blocked until they are filled.
5. **`_DEFAULT_VALUES` sentinel fix.** Update `reclassify.py:_DEFAULT_VALUES` so `position` and `selective_logic` match the real `LoreEntry` defaults (`LorePosition.AFTER_CAST`, `SelectiveLogic.AND_ANY`). Otherwise the dropped-matching-metadata warning fires for every reclassified entry — a spurious warning across the entire reclassification surface, not just imports.

## 1. Preview response: server-side suggestions

The preview route already returns `ingested` (a `model_dump` of `IngestedCharacterCard`). Extend the returned dict with:

```python
{
  "preview_id": "...",
  "expires_in_seconds": 900,
  "ingested": {...},                  # unchanged
  "lore_suggestions": [
    {
      "source_index": 0,
      "kind": "character",            # EntityKind.value, or "lore" when below threshold
      "confidence": 0.82,
      "reason": "title looks like a proper noun; body uses pronouns 6 times",
    },
    ...
  ],
}
```

Implementation: in `imports.py:preview_sillytavern_import`, after the ingest succeeds, build the suggestion list by calling `suggest_kind(LoreEntry(...), threshold=cfg.suggestion_threshold)` per `IngestedLoreEntry`. `IngestedLoreEntry` already carries `name` (which becomes `title`) and `body`; the suggestion only uses those, so a small adapter (`_lore_entry_from_ingested`, lives in `grimoire.library.reclassify` so it can be reused by `_write_lore_entries` in step 3) constructs a minimal `LoreEntry` for the classifier call. The threshold is read from `LibraryConfig.reclassification.suggestion_threshold`; the preview route grows a `LibraryDep` to reach it.

The `lore_suggestions` array is parallel to `ingested.lore_entries`, keyed by `source_index`. When the classifier returns LORE (no signal above threshold), the UI's default selection stays on Lore.

Cost: one classifier call per imported lore row, all in-memory; cards average <50 entries, classifier is rule-based, so this is well under a millisecond per card.

## 2. Commit payload: `lore_overrides`

Extend `CommitPayload`:

```python
class LoreOverride(BaseModel):
    source_index: int
    kind: str = "lore"                              # "lore" | "character" | "location" | "faction" | "item" | "skip"
    overrides: dict[str, Any] = Field(default_factory=dict)


class CommitPayload(BaseModel):
    preview_id: str
    options: dict[str, Any] = Field(default_factory=dict)
    lore_overrides: list[LoreOverride] = Field(default_factory=list)
```

Validation:
- `kind` is checked against the literal set above; unknown values get a 400.
- Duplicate `source_index` values across `lore_overrides` get a 400 ("override declared twice").
- `source_index` not present in the preview's lore entries gets a 400.
- For each `kind != "lore"`, the server checks `required_overrides_for(EntityKind(kind))` and rejects the commit (400) if any required key is missing from the row's `overrides`. The frontend should never let this happen, but the backend defends the invariant.

`commit_sillytavern_import` passes the `lore_overrides` through to `_finalize_import` as a keyword argument; `_finalize_import` forwards them to `_write_lore_entries`.

## 3. `_write_lore_entries` dispatch

The current loop becomes a switch keyed by override kind. Pseudocode:

```python
overrides_by_index = {o.source_index: o for o in lore_overrides}
for entry in ingested.lore_entries:
    override = overrides_by_index.get(entry.source_index)
    target_kind = (override.kind if override else "lore")
    if target_kind == "skip":
        result.warnings.append(f"lore entry {entry.source_index} skipped by user")
        continue
    if target_kind == "lore":
        # existing branch — write as lore
        ...
        continue
    # promote: build target-kind frontmatter via apply_mapping
    lore_proxy = _lore_entry_from_ingested(entry)              # same adapter as preview
    fm, body, kept, dropped, into_notes, warnings = apply_mapping(
        lore_proxy, EntityKind(target_kind), override.overrides,
    )
    base_id = f"{char_slug}--{_slug_for_lore_entry(entry, char_slug)}"
    entity_id = await self._unique_id(target_world_id, target_kind, base_id, result)
    fm["id"] = entity_id
    try:
        await self.library.create_entity(
            target_world_id, target_kind, entity_id, fm, body=body, source="characters:import",
        )
        result.created.append(f"{target_kind}:{entity_id}")
        for w in warnings:
            result.warnings.append(f"{target_kind} {entity_id}: {w}")
    except Exception as exc:
        result.errors.append(f"{target_kind} {entity_id!r}: {exc}")
```

Notes:
- Reclassifications during import do **not** go through `LibraryService.reclassify_entity` and do **not** append to `reclassifications.jsonl`. The spec §4 already established this: nothing was ever written as lore, so there is nothing to convert from; the import report alone is the audit trail.
- `_slug_for_lore_entry` is reused so the asset id is stable and predictable regardless of kind.
- `_unique_id` is reused unchanged: it already takes the kind as a parameter.
- `apply_mapping` propagates `import_source` from the source `LoreEntry` if present, but `IngestedLoreEntry` doesn't carry one (lore-as-lore writes set it on the frontmatter directly today, after construction). For the override path we keep symmetry: `_write_lore_entries` injects the same `{kind: "sillytavern_character_book", card_asset_id, source_index}` stanza into the returned `fm` before calling `create_entity`, so the target-kind file points back at the card.

## 4. Frontend dropdown

`ImportDialog.tsx`'s existing lore preview block (lines 169–181 today, just a `<ul>` summary) becomes a structured table-per-row. Each row carries:

- The current label (name + keys), unchanged.
- A `<select>` with options Lore / Character / Location / Faction / Item / Skip. Default: the server-supplied suggestion when `confidence >= threshold`, otherwise Lore.
- A short "Why?" hint shown when the suggestion is non-Lore: tooltip carries the reason string.
- An inline overrides region that appears only when the selected target needs one or more required fields. v1: only Location requires `kind`, so the overrides region renders a single `<input>` labeled "Location kind" when target=Location. The required-keys list comes from a small `requiredOverridesFor(kind)` helper mirroring the backend's `_REQUIRED_OVERRIDES` — kept in `frontend/src/api/imports.ts` so the backend stays source-of-truth (any future required key gets one matching addition in both places, but typescript will tell you when you miss).

State shape inside the component:

```ts
type LoreRowState = {
  source_index: number;
  kind: "lore" | "character" | "location" | "faction" | "item" | "skip";
  overrides: Record<string, string>;       // only string inputs in v1
};

const [loreRows, setLoreRows] = useState<LoreRowState[]>([]);
```

Initialized from preview data: for each lore row, kind = suggestion when above threshold, else "lore"; overrides = {}.

Commit button stays enabled but the click handler validates: any row with `kind in {character, location, faction, item}` whose required overrides are missing → set an inline error and bail (no commit). On success, build `lore_overrides`: include only rows whose `kind !== "lore"` (omit no-ops so the payload stays small).

`commitSillyTavernImport` (in `frontend/src/api/imports.ts`) takes a third arg `loreOverrides`; the new server validation will surface any missed required field as an error message in the existing error UI.

## 5. `_DEFAULT_VALUES` sentinel fix

`backend/src/grimoire/library/reclassify.py:141` currently has:

```python
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
```

`LoreEntry.position` defaults to `LorePosition.AFTER_CAST`, `LoreEntry.selective_logic` defaults to `SelectiveLogic.AND_ANY`. With the current sentinels, any lore entry parsed from a real card (which always carries non-None values for these fields) will trip the "matching metadata discarded" warning — even when the user touched nothing. Fix: replace those two sentinels with the enum defaults. Import the enums at module top.

Existing reclassify tests around dropped-fields rollup keep their behavior — they set fields away from defaults explicitly — but a new test belongs alongside, exercising the just-defaults case to lock the regression.

## 6. Test wiring

Backend:
- `tests/api/test_imports.py` (new or extended): preview returns `lore_suggestions` + `suggestion_threshold`; commit with `lore_overrides` containing one of each non-lore kind writes the correct file under each kind; commit with `kind="skip"` skips; commit with missing required override returns 400.
- `tests/library/test_reclassify.py`: regression — a `LoreEntry` left at all defaults produces no "matching metadata discarded" warning.
- `tests/characters/test_service_import.py` (or wherever `_write_lore_entries` is currently covered): override branches cover lore / character / location / faction / item / skip, and unique-id resolution still works when a name collides with an existing character.

Frontend:
- `ImportDialog.test.tsx` (new): renders preview with lore_suggestions, the default selection follows threshold, picking Location reveals the kind input, commit fires with the correct `lore_overrides`, skipped rows are excluded from the payload, commit with missing override shows inline error.

## Out of scope (v1)

- Bulk "set all rows to Character" UI shortcut. The per-row dropdown is enough; if users complain we revisit.
- Live reclassification preview (showing the target frontmatter before commit). The standalone Convert modal already does this; replicating it inline blows the preview's compact footprint.
- Character-scoped lore. The existing import dialog already calls this out as "coming in a future release"; the override flow just respects whatever scope the lore write would have used.
- Server-side persistence of the user's per-row choices if the preview expires before commit (current 15-minute TTL is long enough; if it expires the user re-uploads).
