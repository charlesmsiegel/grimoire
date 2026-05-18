# Narrative Extras

User-defined free-form `extras.<key> = <value>` on library and campaign
entities (characters, locations, items, factions). The third tier of
structured entity data alongside frontmatter core (Grimoire-owned) and
mechanics sheets (module-owned).

Use cases: `mechanics: null` campaigns wanting richer character profiles,
cross-mechanics consistency (extras travel when mechanics changes),
long-tail color (favorite drink, scars, pet peeves, dialect notes).

## Schema

Extras live under an `extras:` key in YAML frontmatter:

```yaml
extras:
  favorite_drink: "Whisky, neat — Glenfarclas 25"
  scars:
    - "thin one above left eyebrow"
    - "burn on right palm from the fire at Camden in '67"
  smokes: "occasionally — only Sobranie Black Russians"
  dialect_notes: "drops his aitches when very angry"
```

- Value types: string, number, boolean, list of those, single-level map,
  null (override-clear). No deep nesting — if you need it you have a
  sheet, not an extra.
- Keys: snake_case, 1–40 chars. Reserved prefixes blocked: `_internal_`,
  `mechanics_`, `system_`.
- Soft caps: 50 total / entity (hard reject), 20 (warn); 200 chars per
  string (warn), 1000 (hard); 20 list items (warn); 4 KB total (warn).

## Storage and cascade

File SSOT — extras live in entity frontmatter (library or campaign-local).
Campaign override file (`overrides/.../entity.yaml`) carries an `extras`
block applied via the standard cascade:

- Net-new keys → added
- Present keys with override value → replaced
- Present keys with override null → removed
- List values replaced wholesale (no concatenation)

SQLite mirror `entity_extras (campaign_id, entity_kind, entity_id, scope,
key, value)` for fast query — e.g. "find every character whose
`dialect_notes` contains 'aitches'".

## Surfacing

- **Entity detail UI** — clean key/value table with a source badge
  (library 📚 / campaign-local 🌿 / override ✏️) per row, inline edit on
  click, "[+ Add field]" affordance.
- **Context Builder** — included in the spotlight tier when the entity is
  present. Compact stanza: `key: value` lines, empty/null omitted. Demote
  to background as breadcrumbs (keys only) on overflow.
- **HUD pinning** — any extra pinned to the HUD shows in the present-cast
  chip (`📌 scar across throat`). Max 3 per character (soft guidance).
  Pin state stored in `hud.yaml`, not the entity file.
- **Exports** — included in the character index appendix of EPUB and
  markdown exports.

## Editing

- Add field: modal with key + type + value; type drives the input widget.
- Edit field: inline editor, commit on blur / Enter.
- Delete field: confirmation modal.
- Pin / unpin to HUD: toggle icon next to each row.
- Bulk: edit the frontmatter file directly; watcher picks up the change.

Inline validation surfaces empty key, reserved prefix, hard-cap violations
as errors; soft caps as advisories.

## Extractor-proposed extras

The Extractor watches for repeated stable attributes:

- "She lit her seventh cigarette — Sobranie Black Russians, of course." →
  propose `extras.smokes = "Sobranie Black Russians; chain-smokes in the morning"`.
- "He always wore a black silk handkerchief in his pocket." → propose
  `extras.always_wears = ["black silk handkerchief in pocket"]`.

Heuristics: repeated attribute across multiple posts, "always / never /
usually" qualifiers, sensory specificity (color, brand, scent).

Proposals enter the review queue with evidence (post excerpt + count).
User picks scope; default `campaign-local`. Auto-create safeguards:

- Max 1 proposal / turn / entity.
- Confidence floor 0.7.
- Respect soft caps — stop proposing once a character hits 20 extras,
  warn the user once.

## Templates

A setting can ship `extras-templates/<kind>.yaml` with suggested starter
keys + descriptions. Surfaced as one-click stubs in the entity creator.
Mechanics modules and style guides do *not* ship templates (ownership
boundary).

## Protocol

```python
class Extras(Protocol):
    async def get(entity_kind, entity_id, campaign_id=None) -> dict[str, ExtraValue]
    async def get_raw(entity_kind, entity_id, scope) -> dict[str, ExtraValue]
    async def set(entity_kind, entity_id, scope, key, value, author, evidence=None) -> ExtraValue
    async def delete(entity_kind, entity_id, scope, key) -> None
    async def rename(entity_kind, entity_id, scope, old_key, new_key) -> None
    async def pin(campaign_id, entity_kind, entity_id, key, pinned: bool) -> None
```

## REST surface

```
GET    /library/.../{kind}/{id}/extras
PUT    /library/.../{kind}/{id}/extras/{key}
DELETE /library/.../{kind}/{id}/extras/{key}

GET    /campaigns/{id}/{kind}/{eid}/extras            # resolved
GET    /campaigns/{id}/{kind}/{eid}/extras/raw        # local + override only
PUT    /campaigns/{id}/{kind}/{eid}/extras/{key}      # writes override
DELETE /campaigns/{id}/{kind}/{eid}/extras/{key}      # writes override-null

POST   /campaigns/{id}/{kind}/{eid}/extras/{key}/pin
POST   /campaigns/{id}/{kind}/{eid}/extras/{key}/unpin
POST   /campaigns/{id}/{kind}/{eid}/extras/{key}/promote-to-fact
POST   /campaigns/{id}/{kind}/{eid}/extras/{key}/promote-to-library
```

## Promotion paths

Copy semantics — original extra remains unless the user removes it:

- Promote to fact (Continuity) when narratively significant.
- Promote to mechanics sheet when rules-relevant.
- Promote to library from a campaign override.
- Promote into the entity body when really descriptive prose.

## Performance

- Resolved single-entity extras: < 5ms.
- Bulk for present cast: < 30ms.
- Add / edit / delete: < 20ms.
