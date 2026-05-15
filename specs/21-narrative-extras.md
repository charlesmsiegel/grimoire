# 21 — Narrative Extras

## Purpose

Narrative extras are **user-defined, free-form, narrative-only fields** attached to library and campaign entities — characters, locations, items, factions. They cover the long tail of "things I want to remember about winifred that don't fit anywhere else": her favorite drink, the shape of her scars, her pet peeves, her preferred pronouns in dialect, the brand of cigarettes she smokes, the way she always carries a sprig of lavender.

Mechanics sheets are strict (schema-validated, mechanics-owned vocabulary). Markdown body is prose. Frontmatter has structured fields specific to the entity kind. None of these fit the case where the user wants to **add a new key** without writing a mechanics module or hiding the value in narrative prose where it's not queryable.

This is SillyTavern RPG-companion's "user-defined skills" pattern, generalized: the user (or the Extractor) can add `extras.<key> = <value>` to any entity, and the system surfaces it in context, the HUD, and prompts.

Narrative extras are the **third tier** of structured entity data:

| Tier | Owner | Validation | Vocabulary | Used by |
|---|---|---|---|---|
| Frontmatter core | Grimoire (kind-specific) | Strict | Built-in | All modules |
| Mechanics sheet | Mechanics module | JSON Schema | Mechanics-owned | Mechanics, Context Builder, mechanics widgets |
| **Narrative extras** | **User / Extractor** | **None (soft caps)** | **User-defined** | **Context Builder, HUD, prompts, export** |

This spec defines the schema, storage, surfacing rules, edit affordances, Extractor-proposed creation, and limits.

## Why it matters

Three concrete use cases:

1. **`mechanics: null` campaigns** — purely narrative play has no mechanics sheets at all. Users still want richer character profiles than the markdown body alone provides.
2. **Cross-mechanics consistency** — when a campaign switches mechanics (e.g., from D&D to narrative-only), the mechanics sheet becomes irrelevant but the user-added details about winifred's accent, her favorite tavern, her old grudge against the gold-merchant guild are still relevant. Extras travel.
3. **Long-tail color** — every character accumulates small specifics that the model should remember but that don't belong in the prose body (which is meant for narrative voice and identity) or in mechanics (which is meant for rules-governed values).

## Responsibilities

- Store user-defined key/value pairs on any library or campaign entity
- Apply soft validation (size limits, depth limits) without imposing vocabulary
- Surface extras in entity detail UI, Context Builder spotlight tier, HUD chips, and exports
- Support Extractor-proposed extras with review-queue routing
- Cascade extras correctly across library and campaign-local overrides
- Pin individual extras to the HUD for high-visibility recall

## Non-responsibilities

- Does not validate vocabulary (users pick the keys)
- Does not enforce typed schemas (mechanics sheets do that)
- Does not interpret values mechanically (they're descriptive, not rules-bearing)
- Does not promote itself to facts (user action through Continuity does)

## Schema

Extras live in the YAML frontmatter under an `extras` key:

```yaml
---
id: alistair-hyde-smythe
name: Alistair Hyde-Smythe
role: pc
# ... other frontmatter ...
extras:
  favorite_drink: "Whisky, neat — Glenfarclas 25"
  scars:
    - "thin one above left eyebrow"
    - "burn on right palm from the fire at Camden in '67"
  pet_peeves:
    - "being addressed as 'sir' by strangers"
    - "men who don't remove their hats indoors"
  smokes: "occasionally — only outside, only Sobranie Black Russians"
  carries_always:
    - "a folding knife with mother-of-pearl handle"
    - "a silver locket (closed; contents unknown)"
  dialect_notes: "drops his aitches when very angry"
---

# Alistair Hyde-Smythe

Aristocratic vampire of the Camden line... (prose body)
```

### Value types

- **String**: short or long text
- **Number**: integer or float
- **Boolean**: true / false
- **List of any of the above**: ordered, may include strings/numbers
- **Map (one level only)**: nested key/value for grouped data
- **Null**: explicit absence (useful for override-clearing)

No deep nesting beyond one level. If you need deeper structure, you have a sheet, not an extra.

### Keys

- snake_case, freeform
- 1–40 characters
- No reserved prefixes (`_internal_`, `mechanics_`, `system_` are blocked)
- Stable: renaming a key is allowed but logged as a delta; references in pins, prompts, and exports update accordingly

### Soft caps

| Cap | Limit | Behavior |
|---|---|---|
| Total keys per entity | 50 | Hard reject |
| Keys per entity (warn) | 20 | Warn in UI, allow |
| Length per string value | 200 chars | Warn; truncate in compact contexts |
| Length per string value (hard) | 1000 chars | Hard reject |
| List length | 20 items | Warn |
| Total extras bytes (per entity) | 4 KB | Warn; affects prompt budget |

Limits are enforced at write time with helpful errors.

## Storage and cascade

### File SSOT

Extras live in the entity file's YAML frontmatter. Whether that file is a library asset (`data/library/settings/wod-london/characters/alistair-hyde-smythe.md`) or a campaign-local emergent character (`data/campaigns/by-night-london/emergent/characters/the-bartender.md`), the schema is the same.

### Campaign-local overrides

A campaign can override or extend a library entity's extras. The campaign override file (`data/campaigns/<id>/overrides/settings/wod-london/characters/alistair-hyde-smythe.yaml`) declares which extras to override or add:

```yaml
extras:
  scars:
    - "thin one above left eyebrow"
    - "burn on right palm from the fire at Camden in '67"
    - "new: scar across the throat from the Whitechapel incident"   # added in this campaign
  smokes: null                                                       # cleared in this campaign (he quit)
  current_obsession: "tracking down the Whitechapel killer"          # net-new key, campaign-scoped
```

### Resolution

The library cascade (`00-overview.md`'s read cascade) applies:

```
resolved_extras = merge(library_extras, override_extras)
  - net-new override keys → added
  - library keys with override value → replaced
  - library keys with override null → removed
  - list values: replaced wholesale (not concatenated; explicit and predictable)
```

Resolution is deterministic and surfaced in the UI ("scars: from override; favorite_drink: from library").

### SQLite mirror

The library_index and campaign_local index mirror extras into a `entity_extras` table for fast query:

```sql
CREATE TABLE entity_extras (
  campaign_id TEXT,                  -- null for library-scope rows
  entity_kind TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  scope TEXT NOT NULL,               -- 'library' | 'campaign-local' | 'override'
  key TEXT NOT NULL,
  value TEXT NOT NULL,               -- JSON-encoded
  PRIMARY KEY (campaign_id, entity_kind, entity_id, scope, key)
);
```

Enables queries like "find every character in this campaign with extras.dialect_notes containing 'aitches'" for the cross-cast search.

## Surfacing

### Entity detail UI

The entity detail view has an "Extras" section rendered as a clean key/value table:

```
Extras
─────────────────────────────────────────────────
  favorite_drink     Whisky, neat — Glenfarclas 25      📚 library
  scars              • thin one above left eyebrow      ✏️ override
                     • burn on right palm from '67
                     • scar across throat (Whitechapel)
  smokes             —  (cleared in this campaign)      ✏️ override
  carries_always     • folding knife (mother-of-pearl)  📚 library
                     • silver locket                    📚 library
  current_obsession  Tracking the Whitechapel killer    🌿 campaign-local
  [+ Add field]
```

Each row shows source badge. Click → inline edit. Cleared keys render as `—` and indicate the override.

### Context Builder

Extras are included in the **spotlight tier** when the entity is present in the current scene. Format:

```
Alistair Hyde-Smythe — extras:
  favorite_drink: Whisky, neat — Glenfarclas 25
  scars: thin one above left eyebrow; burn on right palm from '67; scar across throat
  smokes: (none)
  carries_always: folding knife (mother-of-pearl); silver locket
  current_obsession: tracking down the Whitechapel killer
  dialect_notes: drops his aitches when very angry
```

Compact form. Empty / null keys are omitted. If the spotlight tier overflows, extras are demoted to background where they're compressed further (just keys, no values, as breadcrumbs the LLM knows to ask for if needed). Configurable per-tier budget allocation.

For **background tier** characters, only "pinned" extras (see below) are included — the rest stay in archive.

### HUD pinning

Any extra can be **pinned to the HUD**, which surfaces it in the Present Cast widget chip for that character:

```
┌──────────────────────────────────────────┐
│  Alistair  😐  watchful                   │
│  → leaning against the bar               │
│  💭 "She's late."                        │
│  📌 scar across throat                   │
└──────────────────────────────────────────┘
```

Pinning rules:
- Max 3 pinned extras per character (UI guidance, not a hard cap)
- Pin state is per-campaign user preference (stored in `hud.yaml`, not the entity file)
- Pin a list-valued extra → renders first item only (rest collapsed); click to expand
- Pinned extras get priority placement in spotlight tier too

### Exports

EPUB and markdown exports include extras in the character index appendix as a structured list per character (see `13-export.md`). Useful for re-reading a finished campaign — the character profiles include all the small details.

## Editing affordances

### Manual edit

From entity detail:
- **Add field**: modal with `key`, `type`, and `value` inputs; type drives the input widget (string → textarea, number → number input, list → tag input, map → key-value sub-editor)
- **Edit field**: inline editor; commits on blur / Enter
- **Delete field**: confirmation modal
- **Pin / unpin to HUD**: toggle icon next to each row

### Bulk edit

The frontmatter is a YAML file. Open in an external editor, save, watcher picks up the change. Useful for power users adding 20 extras at once.

### Validation surface

Inline:
- Empty key on add → "Key required"
- Reserved key prefix → "Keys starting with `_internal_`, `mechanics_`, `system_` are reserved"
- Value over hard cap → "Value too long (1000 char max)"
- Cap warnings render as advisories, not errors

## Extractor-proposed extras

The Extractor watches for narrative attributes that don't fit facts, sheets, or transient state — they're stable enough to be character traits but not "facts" in the storyline sense. Examples:

- "She lit her seventh cigarette of the morning — Sobranie Black Russians, of course." → propose `extras.smokes = "Sobranie Black Russians; chain-smokes in the morning"`
- "He always wore a black silk handkerchief tucked into his pocket." → propose `extras.always_wears = ["black silk handkerchief in pocket"]`
- "Her voice had a faint Welsh lilt." → propose `extras.dialect_notes = "faint Welsh lilt"`

Heuristics:
- Repeated character attribute across multiple posts (signals stable trait)
- "Always", "never", "usually", "as always" qualifiers
- Sensory specificity (color, brand, scent — color is "trait" rather than "event")

Proposed extras go to the **review queue** with a context snippet showing the evidence:

> Propose extra for winifred:
> `extras.always_wears = "thin silver chain bracelet (her mother's)"`
> Evidence: 3 posts mention the bracelet, last in scene 47 ("she twisted the chain bracelet at her wrist, the silver catching the lamplight").
> [Add to character (campaign-local)] [Add to library] [Add to override] [Reject]

The user picks the scope. Default is "campaign-local" (per Grimoire's principle that Extractor never writes to library without explicit user action).

### Auto-creation safeguards

- Max 1 proposed extra per turn per entity (avoid floods)
- Confidence threshold for auto-propose: 0.7+ (lower confidence: discard silently)
- Soft-cap respect: if a character already has 20 extras, the Extractor stops proposing new ones for that character (warn the user in review queue once)

## Templates

Settings can ship an **extras template** suggesting starter keys for characters (or other entity kinds) in that setting:

```yaml
# data/library/settings/wod-london/extras-templates/character.yaml
suggested_extras:
  - key: favorite_drink
    description: "Their go-to in a pub or salon. WoD London is a city of taverns; characters have opinions."
  - key: smokes
    description: "Brand and frequency. Diegetic for the era."
  - key: dialect_notes
    description: "Class, region, accent details."
  - key: pet_peeves
    description: "What makes them tut, sneer, or storm out."
  - key: carries_always
    description: "Their pockets are character. Knives, talismans, photographs."
```

When the user creates a new character in `wod-london`, the create dialog offers these as one-click stubs. Optional; templates do not constrain the user's actual extras.

Mechanics modules and style guides do not ship extras templates (that would be vocabulary creep into territory that's not theirs); only settings do.

## Promotion paths

Extras are intentionally lightweight. When an extra becomes mechanically or narratively significant, the user has clear paths:

- **Promote to fact** — `extras.carries_always: silver locket` becomes a fact in Continuity if the locket's contents matter. The original extra remains; the fact carries the canonical record.
- **Promote to mechanics sheet** — if `extras.smokes: chain-smoker` becomes mechanically relevant under a new mechanics module (e.g., a respiratory penalty), the user adds it to the mechanics sheet. The extra remains as narrative flavor.
- **Promote to library** — a campaign-local override extra can be saved back to the library file (the same promote-to-library flow used elsewhere).
- **Promote to entity body** — for extras that are really character description, the user can copy the value into the markdown body and remove the extra. This is purely user judgment.

Promotion is a copy operation by default — the extra stays unless the user explicitly removes it.

## Interface

```python
class Extras(Protocol):
    async def get(
        self,
        entity_kind: EntityKind,
        entity_id: str,
        campaign_id: Optional[str] = None,
    ) -> dict[str, ExtraValue]: ...
    # Returns resolved extras (library + overrides applied) when campaign_id is given.

    async def get_raw(
        self,
        entity_kind: EntityKind,
        entity_id: str,
        scope: Scope,
    ) -> dict[str, ExtraValue]: ...
    # Returns extras at a specific scope, unmerged.

    async def set(
        self,
        entity_kind: EntityKind,
        entity_id: str,
        scope: Scope,
        key: str,
        value: Any,
        author: str,
        evidence: Optional[str] = None,
    ) -> ExtraValue: ...

    async def delete(
        self,
        entity_kind: EntityKind,
        entity_id: str,
        scope: Scope,
        key: str,
    ) -> None: ...

    async def rename(
        self,
        entity_kind: EntityKind,
        entity_id: str,
        scope: Scope,
        old_key: str,
        new_key: str,
    ) -> None: ...

    async def pin(
        self,
        campaign_id: str,
        entity_kind: EntityKind,
        entity_id: str,
        key: str,
        pinned: bool,
    ) -> None: ...
```

```python
@dataclass
class ExtraValue:
    key: str
    value: Any
    scope: Scope                # library | campaign-local | override
    author: str                 # who wrote this
    created_at: datetime
    pinned: bool                # per-campaign pin state
```

## Backend contract

```
GET    /library/.../{kind}/{entity-id}/extras          # library scope only
PUT    /library/.../{kind}/{entity-id}/extras/{key}
DELETE /library/.../{kind}/{entity-id}/extras/{key}

GET    /campaigns/{id}/{kind}/{entity-id}/extras       # resolved (library + override)
GET    /campaigns/{id}/{kind}/{entity-id}/extras/raw   # campaign-local + override only
PUT    /campaigns/{id}/{kind}/{entity-id}/extras/{key} # writes to override
DELETE /campaigns/{id}/{kind}/{entity-id}/extras/{key} # writes override-null

POST   /campaigns/{id}/{kind}/{entity-id}/extras/{key}/pin
POST   /campaigns/{id}/{kind}/{entity-id}/extras/{key}/unpin
POST   /campaigns/{id}/{kind}/{entity-id}/extras/{key}/promote-to-fact
POST   /campaigns/{id}/{kind}/{entity-id}/extras/{key}/promote-to-library
```

## Conflict and safety

- **Renaming a key with pins / context refs**: the rename is atomic; pin and prompt cache are updated; audited
- **Library edit during active campaign**: pinned campaigns continue reading their pinned version's extras; non-pinned campaigns see the change immediately
- **Schema migration**: extras have no schema, so no migration; safe
- **Export consistency**: extras included in export always reflect resolved (campaign-context) values, with provenance footnoted

## Performance

- Resolved extras for one entity: < 5ms (mirror table, indexed)
- Bulk resolved extras for all present cast: < 30ms
- Add / edit / delete: < 20ms (writes the file + reindex)

## Open questions

- **Extras versus mechanics-defined optional fields** — should mechanics modules also be able to declare *recommended-but-optional* fields that look like extras? Probably yes, but as schema-validated entries on the sheet rather than as extras, to keep ownership clean.
- **Cross-entity queries from extras** — "find all characters who carry silver" requires extracting the search term from list/string values. The SQLite mirror enables LIKE queries; a richer FTS index over extras is worth adding once the feature ships.
- **AI-suggested key inference** — when the user types an extra value, suggest the key (or detect that a similar key exists). Probably a v2 polish.
- **Extras on relationships** — could relationships (winifred → julian: trust) carry extras too? Maybe ("trust_basis: she nursed him through the fever last winter"). Out of v1 scope; the relationship model in Characters covers it more rigorously.
- **Per-extra visibility** — like transient state's privacy flags, should specific extras be hideable from the player? Probably a v2 feature; v1 treats extras as user-authored therefore user-visible by default.
- **Mechanics-module-curated extras** — a mechanics module suggesting "consider tracking favorite_drink for diegetic flavor in WoD." That blurs ownership; better to let settings ship templates, mechanics ship sheets, and not cross the streams.
