# V0.2.1 read-only inventory (`grimoire.migrate_v1`)

**Status:** first slice implemented ([sprint 011](roadmap/sprints/v1-to-v2-migration-skill.md)). It reads an explicitly chosen, frozen Grimoire **0.2.1** library and reports what a later import would have to carry. **Nothing is imported**: no V2 record is written, no picture is copied, no model is called. The reviewed import is [sprint 026](roadmap/sprints/v1-to-v2-import-apply.md). The agent workflow is the [migration skill](../.agents/skills/migrate-v1-to-v2/SKILL.md).

## Command

```bash
python -m grimoire.migrate_v1 scan --source /path/to/frozen-v1-copy --archive-id my-backup --report /path/outside/inventory.json
```

| Option | Meaning |
|---|---|
| `--source` | **Required.** The V1 library, or a backup extracted to a directory. There is no default; `~/.grimoire`, `GRIMOIRE_HOME` and `~/.grimoire.json` are never consulted to find it. |
| `--archive-id` | **Required.** One lowercase identity segment naming this frozen source. It qualifies every legacy ID: `v1:<archive-id>:<path>`. |
| `--destination` | The V2 library a later import would target; default is the V2 library this process would use (`GRIMOIRE_V2_HOME` or `~/.grimoire-v2`). It is checked, recorded and **never opened, listed or created**. |
| `--report` | Write the JSON manifest here. Without it the scan is a dry run that prints only the summary. |
| `--overwrite` | Replace an existing report (a regular file only). |
| `--summary counts` | Print counts and finding codes only: no record names or paths. |

Before reading anything, the pair is checked with `check_migration_paths`: the source must be explicit, and the destination must not be, contain or sit inside the source or any V1 library. The report location is checked with `grimoire.library.check_report_destination`: the file may not be, or sit inside, the source, the destination, `~/.grimoire`, `GRIMOIRE_HOME`, the pointer's `data_dir` or anything that looks like a V1 library. A directory that merely *contains* one of those, such as the folder holding the backup, is fine. The report is written atomically: an exclusive `0600` temporary file beside it, then published after re-checking the parent directory's identity. Without `--overwrite`, publication cannot clobber a file created meanwhile: it uses a hard link on POSIX and a rename on Windows, and both fail if the name exists.

### Exit codes

| Code | Meaning |
|---:|---|
| 0 | Scanned; status `ready_for_review` (warnings allowed) |
| 2 | Unusable arguments, paths or report location; not a 0.2.1 library; the report could not be written (nothing partial is left) |
| 3 | The source/destination pair overlaps, or a V2 path would touch a V1 library |
| 4 | Scanned; status `blocked`. The report is still written. |
| 5 | The source changed during the scan. No report is written; stop V1 or scan a frozen copy. |

## Read-only, offline and fail-closed

- Files are opened read-only. On POSIX the walk and every later read go through directory descriptors opened `O_NOFOLLOW` from a root descriptor held for the whole scan. Each directory and file is checked by `(st_dev, st_ino)` against what the walk recorded. Windows walks by path, with an `lstat` before and an `fstat` identity check after each step.
- The bytes an interpreter parses must hash to the walk's digest. A second walk re-stats every entry and re-hashes every file, so a live source is detected (exit 5). This **detects**, and cannot prevent, a concurrent writer.
- The package imports no provider, HTTP, socket or subprocess module; a subprocess audit-hook test proves a scan opens nothing for writing, mutates nothing, spawns nothing and opens no socket. Remote image URLs are recorded and never fetched.

Every entry gets exactly one disposition, so a reviewer can see that nothing was skipped:

| Disposition | Meaning |
|---|---|
| `directory` | A directory that was walked |
| `interpreted` | A recognized V0.2.1 file the scanner parses. Each one must actually be read: otherwise an `uninterpreted` finding blocks. |
| `hashed` | Recognized binary content (image blobs, legacy loose images): hashed, not parsed |
| `recognized_unsupported` | Recognized V0.2.1 state the first importer does not carry (knowledge, relationships, chronicle, reroll alternates, response variants, VN history, settings presets, `calendars/` Python, …). Listed in `losses`, hashed, never executed. |
| `excluded` | Never read or descended: `llm_connections/`, `usage/`, `logs/`, `.cache/`, `backups/`, `.world-staging/`, `.module-staging/`, in any letter case. `config.md` is read for format markers only; no other value, such as a legacy key, is copied. |
| `ignored` | OS litter (`.DS_Store`, `Thumbs.db`, `desktop.ini`) |
| `unknown` | Not recognized: listed with size and digest for review |
| `refused` | A symlink, Windows junction or reparse point, FIFO, socket, device, or an entry on another device (a mount point). Never followed or descended. **Blocking.** An excluded top-level link is only informational. |
| `unreadable` | An I/O error, recorded by errno name only. **Blocking.** |

Parsed files are capped at 16 MiB; a larger one is still hashed but blocks as `too_large`.

## Format contract

V0.2.1 records no layout version, so the scanner asserts the `grimoire-0.2.1` contract (git tag `0.2.1`) and reports deviations. A root needs `worlds/` or `campaigns/`. An `inference_format` other than `1` or `2` in `config.md` or a `campaign.md` is `newer_format` (blocking; V1 only writes `2`, so anything else fails closed). A campaign whose `world_copy` is not `overlay` is a legacy full-copy campaign: overlay rules are **not** applied to it, and its inheritance is `unresolved`. Legacy real-date scene names are reported, not renamed. V1's own rules are ported, not approximated:

- **Frontmatter:** exact port of V1's parser (string scalars, comma lists, CRLF fences, `{}` when unterminated).
- **Text and hashes:** strict UTF-8 with universal newlines. Every V1 hash (card, persona, flat record, actor directory, sync and appearance bases) is sha256 of that normalized text, so a CRLF copy hashes as V1 hashed it. Invalid UTF-8 or JSON is a finding, never a crash.
- **Scene authorship:** V1's marker grammar, its post splitting and metadata stripping, and `match_name` against the scene's player cast. That cast is read from `appearances.json` and named from the campaign's locked copies. Text before the first marker is reported as a loss.
- **Ledgers:** `deleted.json`, `detached.json`, `appearances.json` and `sync.md` are read **strictly**. V1's display read treats a corrupt `deleted.json` as "nothing deleted", which would resurrect hidden art. Here a corrupt ledger blocks (`invalid_ledger`) and leaves that campaign's semantics `unresolved`. That includes a non-empty `sync.md` that is not a closed frontmatter block, which V1 would read as empty. Image links in that campaign's scenes, cards and greetings are then `unresolved` too. An invalid entry is listed and ignored. An invalid appearance unresolves that actor and the scenes it sits in; if its scene list is unreadable, every scene of the campaign is unresolved.
- **Expressions:** V1's manifest validation and content digest. An incompletely synced or inconsistent layer is `unavailable` as a whole, never empty.

## What the manifest holds (`grimoire.v1-inventory/1`)

Deterministic JSON: sorted keys and lists, ASCII escapes, no timestamps. Absolute paths appear only in `roots`. Identical source bytes, archive id, roots and Unicode database (`unicode_version`) give byte-identical reports.

| Key | Contents |
|---|---|
| `schema`, `archive_id`, `source_format`, `unicode_version`, `roots`, `inference` | Header. `inference.provider_calls` is always `0`. |
| `source_fingerprint` | sha256 of canonical JSON of every non-excluded entry's `[path, disposition, size, sha256]` |
| `entries` | Every entry: verbatim relative path, `legacy_id` (or null), kind, disposition, layout family, size, sha256, reason |
| `worlds`, `records`, `actors`, `campaigns`, `scenes` | V1 content by V1 coordinates. Image links in a campaign's own cards, greetings and scenes resolve through that campaign's overlay. Records carry resolved/dangling reference fields, as seen from their scope; a campaign does not see world records it tombstoned or detached. Actors carry portrayals (versions) with label, card spec, greeting count and `v1_hash` (V1's `card_hash` or `version_hash`). Campaigns carry ledgers, appearances and per-record inheritance (below). Scenes carry ordered posts with speaker, role, post id and content digest. |
| `images` | `objects` (every sidecar, opaque `px1-` ids, pixels never re-verified; `addressable: false` for one outside V1's `objects/<id[4:6]>/` path, which V1 never reads), `blobs`, `placements` (owner, slot, focus, status), `legacy_images`, `expression_layers`, `collections`, `campaign_layering` (inherited / overridden / focus override / hidden), Markdown `references`, `referenced_objects` |
| `identity_candidates` | Proposed V2 identities, below |
| `findings` | `{code, severity, paths, detail}` |
| `losses` | Recognized but unsupported content, with counts where cheap |
| `unresolved` | Everything an importer must not apply without an explicit user decision |
| `counts`, `status` | `ready_for_review` or `blocked` |

### Campaign states

- **Appearances** have two axes, as V1's `actor_source` keeps them apart:
  - `provenance`: `library` when the campaign copy still hashes to the lock's `base`; `override` when it does not, or the copy is missing; `emergent` when the actor is detached or has no world original.
  - `sync`: the world's current version against `base`. Values are `current`, `world_changed`, `world_missing` or `not_applicable`.
  - `control` is `player` or `director`, from V1's `role`. An invalid record is `unresolved` on both axes.
- **Inheritance**, per record of an inherited kind:
  - `state` is `inherited`, `materialized`, `campaign_origin`, `detached`, `detached_absent` or `tombstoned`.
  - A materialized record has a `sync` of:
    - `in_sync`: the world is unchanged since the copy;
    - `diverged`: the world changed since the copy;
    - `world_missing`: copied from a world record that is gone;
    - `no_base`: no base recorded;
    - `locked_by_appearance`: an appearance lock governs it instead;
    - `unresolved`: the base is invalid.
  - `local_edit` says whether the campaign copy differs from its base.

### Identity candidates

Candidates are **proposals**, never a mapping, and they are unpinned: the importer mints revisions. Each is keyed by the `LegacyId` of the exact V1 file. Nothing is lowercased, slugified or matched by name.

- A world's records propose `world:<world>/<kind>/<id>` (`location`, `lore`, `item`, `group`, `creature`, `greeting`). The `world:<world>` source is local to this archive; reconciling two archives belongs to the importer.
- Each character **or PC** version is a separate portrayal entity, `world:<world>/character/<actor>.<version>`, with proposed `alternate_portrayal` links among one actor's portrayals. PC and NPC are not kinds.
- Two records that would claim one identity (for example `characters/x` and `pcs/x` in one world) **both** lose their candidate (`id_candidate_collision`). A V1 id outside the V2 grammar gets `no_id_candidate`.
- Campaign actors propose `campaign:<campaign>/character/<actor>` instances, with `control` (`player` or `director`) from the V1 role and `derives_from` naming the portrayal they were locked to. If that portrayal has no proposed candidate (for example it was withheld as a collision), `derives_from` stays null with `derives_from_unresolved` set and an `unresolved` item. Instance collisions are withheld the same way.
- Same-named records in different worlds are listed as review groups with `relation: none`.

### Status vocabularies

| Field | Values |
|---|---|
| placement `status` | `resolved` (object and intact blob present), `focus_only` (a crop override, not a picture), `dangling_object`, `unarrived_blob`, `invalid`, `not_addressable` (a name V1 cannot address, such as a conflict copy) |
| definition `status` | `proposed`, `collision` (withheld; `withheld` names the claim), `no_id_candidate`, `unresolved` (the file could not be parsed), `campaign_copy` (a campaign copy of a world record), `campaign_origin` |
| instance `status` | `proposed`, `collision`, `no_id_candidate`, `unresolved` |
| `portrayal_groups` | One per world actor: the actor file's legacy id, its `default_version`, and the legacy ids of its portrayals |
| reference `status` | `resolved`, `unresolved`, `missing` (collection member), `listed` (a route to an image collection: recorded, membership not checked), `not_fetched` (external URL) |
| expression layer `status` | `available`, `unavailable` (with `reason`) |
| object `blob` | `present`, `unarrived`, `hash_mismatch` |

### Status and the importer contract

`blocked` means the importer must refuse until the source is fixed and rescanned. Any item listed in `unresolved` must not be applied without an explicit user decision. Before applying, the importer re-verifies each entry's sha256 and the `source_fingerprint` against the source; it never performs a second, hidden scan.

## Findings

**Blocking:**
- `refused_entry`, `mount_point`, `unreadable`, `too_large`, `uninterpreted`: the walk could not read part of the source faithfully.
- `case_variant_name`: an unrecognized entry whose path differs only in letter case from a name V1 uses, such as `Deleted.json` or `Image-Refs`. On a case-insensitive filesystem V1 would honour it.
- `newer_format`: a settings format other than 0.2.1's.
- `invalid_ledger`: a campaign ledger cannot be trusted.
- `blob_hash_mismatch`: a blob's bytes do not match its name.

**Warnings:**
- Paths: `legacy_id_refused`, `normalization_collision`, `case_collision`, `invalid_v1_id`.
- Formats: `invalid_utf8`, `malformed_json`, `malformed_card`, `missing_default_version`, `no_versions`.
- Scenes: `legacy_scene_name`, `invalid_scene_name`, `missing_scene_identity`, `duplicate_scene_identity`.
- Campaigns: `legacy_full_copy_campaign`, `invalid_ledger_entry`, `world_missing`, `world_case_variant`, `missing_locked_version`.
- References: `dangling_ref`.
- Pictures: `invalid_object`, `object_shard_mismatch`, `blob_size_mismatch`, `unarrived_blob`, `invalid_blob_name`, `invalid_placement`, `dangling_object`, `legacy_image_ambiguous`, `expression_layer_unavailable`, `invalid_collection`, `unresolved_image_ref`.
- Identity: `id_candidate_collision`, `instance_candidate_collision`.

**Info:** `excluded_link`, `hard_link`, `unknown_entry`, `card_spec`, `unknown_fields` (names only, never values), `orphan_blob`, `stale_expression_revision`, `external_reference`, `no_id_candidate`.

## Privacy

A report names the user's records and paths: keep it private, and never commit it or attach it to CI. An agent that reads a report or full summary sends those names to its own model provider. `--summary counts` prints neither names nor paths. All tests use the invented synthetic fixture in `backend/tests/v1_fixture.py`, which was checked against V0.2.1's own readers.

## Known limits

- **Pixel ids not re-verified:** `px1-` object ids come from V1's pixel canonicalizer and are taken as declared keys; only blob bytes are hash-checked.
- **Mount points:** only devices are compared. A same-device bind mount inside the source is not detected.
- **Quiescence on Windows:** without descriptor-relative opens, path-based identity checks (before and after each listing) and the second walk are the only protection against a concurrently swapped entry. Every reparse point is refused, including cloud-sync placeholders (OneDrive "files on demand"): make the backup fully local first.
- **A seated player's malformed card:** V1 falls back to the actor id as the display name when a card's `data` is not an object. The scanner instead marks that scene's authorship `unresolved`, because a fallback name could misattribute posts.
- **Not interpreted:** full-copy campaigns are inventoried raw. Knowledge, relationships, chronicle, tracker, reroll and response-variant contents, VN presentation history, sheets and mechanics modules are listed only, as losses.
- **Backup archives:** zip input is not read; extract the backup to a directory first.
- **Collection routes in Markdown:** a Markdown image link to an image-collection route is recorded as `listed`, without checking the collection or member it names. The collections themselves are resolved.
