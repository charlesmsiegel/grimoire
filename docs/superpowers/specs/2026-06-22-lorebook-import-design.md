# Lorebook / World-Info Import — Design

> Imports SillyTavern world-info into grimoire as first-class **keyed Lore entries**. Two sources —
> a **standalone lorebook `.json`** and a character card's embedded **`character_book`** (reached
> through the existing card parser for `.json`/PNG/`.charx`) — funnel into one destination: editable
> entities with a markdown body + comma-joined `keys`, the triggers the context builder (2a)
> already consumes. Import is **parse → review/route → commit**, so each entry can be sent to a
> different category.

**Status:** Design — not yet implemented
**Date:** 2026-06-22
**Branch:** `lorebook-import` (off `greetings-plotmaps`)
**Builds on:**
[`2026-06-22-context-builder-design.md`](2026-06-22-context-builder-design.md) (entity `keys`,
`activate()`, keyless = always-on) and
[`2026-06-21-character-cards-design.md`](2026-06-21-character-cards-design.md) (the `cards.loads`
parser for `.json`/PNG/`.charx`).
**Supersedes:** section G of
[`2026-06-21-greetings-context-builder-decisions.md`](2026-06-21-greetings-context-builder-decisions.md);
its "Open questions for 2c" are resolved below.

## Purpose

The context builder activates lore/location entries by their `keys`, but those keys had to be
authored by hand. This spec delivers the **data-population path**: bulk-import SillyTavern
world-info (the de-facto interchange format for RP lore) into grimoire entities, with per-entry
category routing. It is purely a population layer — **no activation logic ships here** (that landed
in 2a's `activate()` seam).

## Non-goals (this iteration)

- **No new activation behavior.** 2c writes `keys` + bodies; the builder already consumes them.
- **No advanced ST-fidelity fields.** Secondary/"selective" keys, insertion `position`/`order`,
  `priority`/`probability`, and `case_sensitive` are **dropped** on import (grimoire's model is
  keys + body + keyless-always-on). Recorded as a limitation; revisit if playtest needs them.
- **No update-in-place / dedup.** Re-importing the same lorebook creates **new** entities with
  uniquified ids (the existing `create_entity` behavior). No merge, no skip-existing.
- **No frontend.** The parse→review→commit split exists so the deferred import UI can route
  entries; this spec ships the two backend endpoints + the parser.
- **No new per-card lorebook mechanism.** A `character_book` becomes ordinary keyed entities —
  there is no separate per-character lore store.

## Field mapping (ST world-info entry → grimoire entry)

SillyTavern ships **two** entry schemas; the parser normalizes both:

| grimoire | standalone lorebook export | V3 `character_book` entry |
|----------|----------------------------|---------------------------|
| `keys`   | `key` (list)               | `keys` (list)             |
| `body`   | `content`                  | `content`                 |
| `name`   | `comment` → else first key | `comment`/`name` → else first key |
| enabled? | `not disable` (default on) | `enabled` (default on)    |
| constant | `constant`                 | `constant`                |

Normalization rules:
- **`enabled == False` (or `disable == True`) ⇒ the entry is skipped** (ST-inactive entries should
  not silently become active).
- **`constant == True` ⇒ emit empty `keys`** so grimoire's *keyless = always-on* rule reproduces
  ST's always-inject behavior. (Original keys are dropped — they are redundant for an always-on
  entry.)
- An entry with **blank `content`** is skipped (nothing to inject).
- The `entries` container may be a **dict keyed by index** (standalone export) or a **list**
  (`character_book`); both are accepted. A top level without an `entries` key is treated as the
  entries container itself.
- Every normalized entry carries a default **`category: "lore"`** (the client may re-route).

The normalized shape the parser returns and the commit endpoint accepts:

```json
{ "name": "Salt Pact", "keys": ["pact", "salt"], "body": "The pact binds…", "category": "lore" }
```

## Module — `store/lorebook.py`

```python
class LorebookError(Exception): ...

parse(data: bytes, fmt: str) -> list[dict]
    # fmt: "lorebook" (bare ST world-info JSON) | "json"|"png"|"charx" (a card -> its character_book)
    # returns normalized entries [{name, keys:[...], body, category:"lore"}]; raises LorebookError
    # (bad lorebook JSON) or cards.CardParseError (bad card) on failure.

commit(root: Path, entries: list[dict]) -> list[dict]
    # creates each entry as an entity in entries[i]["category"] (default "lore");
    # returns [{"kind": category, "id": eid}]. Raises LorebookError on a category
    # outside entities.ENTITY_KINDS.
```

- `parse`:
  - `fmt == "lorebook"` → `json.loads(data)`; the book is `obj` (its `entries` key, or `obj` itself).
  - card formats → `cards.loads(data, fmt)` then `card["data"].get("character_book") or {}`.
  - then `_normalize(book)` applies the mapping above. Pure; writes nothing.
- `commit` routes each entry via `entities.create_entity(root, category, name, body,
  keys=",".join(keys))` (ids uniquified there). Keys land in the entity frontmatter exactly as the
  builder expects.
- `_normalize` is a small pure helper (dict-or-list container, key/keys, comment/name, the
  enabled/constant/blank rules) — unit-tested directly.

`lorebook.py` depends only on `cards` (parser) + `entities` (writer) — one responsibility
(translate ST world-info ↔ grimoire entities).

## Route wiring (`routes.py`)

World-level, declared **before** the generic `/worlds/{wid}/{kind}` routes (the literal-before-
generic convention; `/lorebook/parse` and `/lorebook/import` are 2-segment paths the generic
entity routes would otherwise not match, but kept ordered for consistency).

```
POST /worlds/{wid}/lorebook/parse   (multipart: file, format=lorebook|json|png|charx)
        → {entries: [{name, keys:[...], body, category}]}        # stateless; nothing written
POST /worlds/{wid}/lorebook/import  (JSON: {entries: [{name, keys:[...], body, category}]})
        → {created: [{kind, id}]}                                # writes entities
```

- `parse`: read the upload, call `lorebook.parse(data, format)`. `cards.CardParseError` /
  `LorebookError` → `400` with a readable reason; an unknown `format` → `400`.
- `import`: `lorebook.commit(root, entries)`; a `category` outside `{lore, locations}` → `400`.
- Pydantic models: `LoreEntry {name, keys:[]=…, body:""=…, category:"lore"}` and
  `LorebookCommit {entries: list[LoreEntry]}`.

The two-call flow keeps **routing a real choice**: the client parses, lets the user re-categorize
each entry, then commits. A caller wanting a blind import simply posts the parse result straight to
`import` unchanged.

## Error handling / edges

- Bad lorebook JSON / unparseable card → `400`.
- A card with no `character_book`, or a lorebook with no entries → `{entries: []}` (no error).
- All entries disabled/blank → `{entries: []}`.
- Unknown `format` on parse, or an entry `category` outside the entity allowlist on import → `400`.
- Re-import creates new uniquified entities (no dedup), consistent with `create_entity`.
- Entry ids/path-safety inherit `entities`' existing `slugify`/`uniquify` + `_safe_id` guards.

## Testing (backend, pytest, temp `GRIMOIRE_HOME`)

**`lorebook.py`:**
- `_normalize`/`parse` of a **standalone export** (entries **dict** keyed by index, `key`/`comment`/
  `content`/`disable`/`constant`): primary keys → `keys`; `comment` → name; a `constant` entry →
  **empty keys** (always-on); a `disable: true` entry **skipped**; a blank-`content` entry skipped.
- `parse` of a **card** (`fmt="json"`) whose `character_book.entries` is a **list** using
  `keys`/`enabled`: extracted + normalized; `enabled: false` skipped.
- card with **no `character_book`** → `[]`; bad JSON → `LorebookError`; bad card bytes →
  `cards.CardParseError`.
- `commit` writes each entry to its `category` (default `lore`, routed `locations`), uniquifies
  colliding names, and the written entity's `keys` frontmatter round-trips (read back as the
  builder reads it). Unknown category → `LorebookError`.

**Routes (TestClient):**
- `parse` multipart with a standalone lorebook returns entries and **writes nothing** (world entity
  lists unchanged); `import` then creates them; a routed entry lands in `locations`.
- `parse` of a PNG/charx card extracting `character_book` (reuse a card fixture).
- bad upload → `400`; unknown category on `import` → `400`.
- an imported keyed entry **activates** through the existing builder (an end-to-end sanity check:
  create campaign, copy the entry in, key present in recent text ⇒ injected).

Suite is green at 145 before this work; every task keeps it green.

## Phasing (for the implementation plan)

1. **`lorebook.py` parser** — `_normalize` + `parse` (both ST schemas, both sources); unit tests.
2. **`commit`** — route entries to categories via `entities.create_entity`; unit tests.
3. **Routes** — `parse` (multipart) + `import` (JSON), error mapping; route tests + the
   builder-activation sanity check.

## What's next

- **Frontend** — the import UI (drop a lorebook/card, review the parsed entries, re-route each to a
  category, commit) alongside the rest of the deferred frontend; plus editing `keys` on entities.
