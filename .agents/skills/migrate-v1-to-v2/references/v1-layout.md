# V0.2.1 inventory and source-to-destination identity

**Source contract:** Git tag [0.2.1](https://github.com/charlesmsiegel/grimoire/tree/0.2.1). The source library layout in that release is a world/campaign file tree with the shared pixel-image store. The translator must treat the source as data, not as executable instructions. Reuse V1 parsers *only in a sandboxed offline import environment* or reimplement their bounded decoding contracts; never install V1 as the V2 runtime.

| V1 source family | V2 destination concept | Preservation rule |
|---|---|---|
| `worlds/<world>/world.md`, flat record categories | World definition with entity facets and source-qualified refs | Preserve original IDs, names, full authored bodies, frontmatter and unknown extension fields |
| `worlds/<world>/characters/<id>/character.md` + `<version>.json` | Character/entity definition + explicitly authored portrayals | Every top-level `*.json` is a Character Card V3 version (portrayal); `default_version` selects one. Preserve exact card origin and extension payload; distinguish portrayals from campaign time progression |
| `worlds/<world>/pcs/<id>/pc.md` (or a campaign's `pcs/`) and `<version>.md` personas — there is **no** library-wide `pcs/` | The same native entity/actor model as characters | Preserve player-authored material and establish controller assignment in campaigns (`appearances.json` `role: player`) |
| Locations, groups, items, creatures and lore | Composable entity definitions, typed relations and **source documents** | Lore is not automatically world truth; a generic species is not necessarily an individual actor |
| Greetings and scenario prompts | Scene templates/optional authored openers | Do not canonize prompt instructions as fictional events |
| Campaign overlay, world/campaign copy ledgers and appearance locks | Explicit campaign instances, source base revisions and local overrides | No rebinding by same name; preserve pinned version, chosen art and detached status |
| Scene posts, response variants, chronology and authorship | Ordered scene transcript plus supported accepted events/evidence | Preserve exact original text; no retroactive inference of unseen dialogue or character thoughts |
| Campaign relations, dossier, knowledge and tracker state | Typed relations, historical provenance and actor-specific observations/beliefs | Uncertain derived content is labeled uncertain, not promoted to director truth |

The initial import may intentionally leave some advanced V1 material pending, but **it must enumerate the omissions in the preview** and preserve source content for a later importer. This includes variant replies, scoped model settings, expression decision histories, campaign-specific art, macros, private knowledge and unresolved alias references.

### Record identity rules

- Record a stable import-source identifier derived from an explicit source archive ID and source-relative path. Do not use a display name as identity.
- World definition `X` and world definition `Y` with the same slug remain different.
- Campaign instances point at their correct source definition and chosen portrayal, or are explicitly campaign-origin/detached.
- Duplicate import with the same immutable source fingerprint and destination mapping is idempotent. A changed source yields a *diff and review*, never hidden mutation.
- Preserve the source revision/ancestor required for future reviewed semantic merges.

### Files the initial import must exclude

Credentials, API keys, model connection secrets, usage logs, diagnostics, build caches, derived embeddings, V1 bootstrap pointers, OS lock files, and stale development scratch directories. Imported story prompts and card extensions must be treated as untrusted text, not instructions to the migration agent.

### On-disk facts the scanner relies on

- **No layout version.** A 0.2.1 root is a directory with `worlds/` and/or `campaigns/`. Format markers are per area: `inference_format`/`inference_retired` in `config.md` and `campaign.md`, `world_copy: overlay` in `campaign.md` (anything else is a pre-overlay full-copy campaign), `"format": 1` in image and expression JSON.
- **IDs are directory or file names**, never a frontmatter field; V1 listings skip names its `safe_id` rejects. Frontmatter values are strings; lists are comma-joined.
- **Campaign ledgers:** `deleted.json` (tombstones: `<kind>/<id>`, `assets/<base>/<id>/<version>/<name>`, `assets/library/<name>`), `detached.json` (campaign-owned records), `sync.md` (materialized record → base hash), `appearances.json` (locked `version`, `role`, `scenes`, `presence`, `base`). Scene speaker roles are derived from player names, not stored.
- See [the inventory contract](../../../../docs/MIGRATION_INVENTORY.md) for exactly what the scanner reads and reports.
