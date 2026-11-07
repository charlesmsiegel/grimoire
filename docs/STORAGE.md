# Native record store

**Status:** first slice of [Native File Store And Revisions](roadmap/sprints/native-file-store-and-revisions.md) (Wave 2, #010), implemented as the filesystem adapter `grimoire.store`. It persists **facet-neutral record envelopes** for source-qualified definitions and campaign instances. It never interprets a payload, and it does not decide whether a record is a character, a PC, a creature or an item; that meaning belongs to [Entity Facets (009)](roadmap/sprints/entity-facets-and-instance-model.md), which joins through codecs in Wave 3. There is no application or UI yet. A green test suite proves this contract, not a playable app.

Not in this slice:
- effective views (022). Device sync (019) is [`grimoire.sync`](SYNC.md); the store extensions it uses are described [below](#synchronization-extensions-019). Events and evidence (020) and image blobs and placements (018) are additive families, documented below ([020](#events-and-evidence-020), [018](#images-and-blobs-018));
- **Markdown.** The sprint's outcome names "Markdown/JSON" records. By the requester's decision this slice is JSON-only (stdlib, no YAML dependency); a Markdown payload codec is a remaining 010 obligation, re-homed by the requester to its own later pull request.

The security audit, grants and approvals, and player sovereignty are persisted as [authority ledgers](#authority-ledgers-010b) (010b).

## Library layout

```text
<library>/
  grimoire-library.json                                           # {"format": "grimoire.library/1"}
  sources/<seg1>/…/<segN>/_source/manifest.json                   # one source's manifest
  sources/<segs>/_source/records/<kind>/<entity_id>/<revision>.json   # one definition revision
  campaigns/<campaign_id>/instances/<kind>/<instance_id>.json     # one campaign instance
  campaigns/<campaign_id>/scope.json                              # the play scope (107)
  campaigns/<campaign_id>/chats/<chat_id>.json                    # one chat record (107)
  campaigns/<campaign_id>/relations/<relation_id>.json            # one relation record (017)
  campaigns/<campaign_id>/events/<event_id>.json                  # one accepted event (020)
  campaigns/<campaign_id>/evidence/<family>/<id>.json             # proposition|ruling|evidence|belief|disclosure (020)
  campaigns/<campaign_id>/evidence/batch/<seq:010d>.json          # the campaign's knowledge log (020)
  sources/<segs>/_source/images/<image_id>.json                   # one image object (018)
  sources/<segs>/_source/placements/<kind>/<entity_id>/<slot>.json   # one placement (018)
  campaigns/<campaign_id>/images/<image_id>.json                  # a campaign's image object (018)
  campaigns/<campaign_id>/placements/<kind>/<instance_id>/<slot>.json  # a campaign placement (018)
  campaigns/<campaign_id>/holdings/<kind>/<instance_id>.json      # one holder's counted holdings (023)
  assets/blobs/<sha256[:2]>/<sha256>.<png|jpg|gif|webp>           # content-addressed bytes (018)
  campaigns/<campaign_id>/accepted/<event_id>.json                # one accepted fictional event (021)
  campaigns/<campaign_id>/wrapups/<wrapup_id>.json                # one scene wrap-up (021)
  campaigns/<campaign_id>/transcripts/<chat_id>/<seq:010d>.json   # one page of a chat's transcript (036)
  campaigns/<campaign_id>/turns/<turn_id>.json                    # one played chat turn (035)
  .txn/lock, .txn/journal.json, .txn/*.new                        # writer lock, journal, staging
  .txn/discard.json                                               # an unfinished discard_journal
  .conflicts/<hash16>/<name>~<digest12>[.N]                       # preserved versions awaiting a human
  .conflicts/discarded-<N>/…                                      # output of discard_journal (first unused N)
  .sync/device.json, .sync/peers/<device>/{archive,pending}.json # device-local sync bookkeeping (019)
```

- **One directory per source segment; a source's own files live under its own `_source/`.** An ID segment can never start with `_`, so `_source` never collides with a sub-source. `world:harbor:dlc` lives at `sources/world/harbor/dlc/_source/`, a *sibling* of harbor's `_source/`.
  - A grant scoped to `sources/world/harbor/_source` covers exactly harbor.
  - A grant scoped to `sources/world/harbor` deliberately covers harbor and its sub-sources.
  - Moving or deleting a source *folder* (`sources/world/harbor/`) includes its sub-sources.
- Every authored path segment must be a valid [`ResourcePath`](TOOL_AUTHORITY.md#logical-addresses) segment. The whole root-relative path is the record's **logical address** for authorization, for example `sources/world/harbor/_source/records/character/wren/r1.json`. An identifier that cannot be mapped raises `UnstorableIdentifier` and is never renamed. Examples:
  - a Windows device name such as `con`;
  - a segment ending in `.`;
  - a file name with no room left for `.json` (IDs used as file names are limited to 123 characters);
  - a source ID deeper than 26 segments.
- **Reserved names for later families**, which the loader skips silently:
  - at the root: `authority`, `config`, `assets`, `secrets`, and `kept` (never a folder: it is the authorization namespace of kept versions, so no record root may alias it);
  - inside a campaign: `scenes`, `events`, `evidence`, `holdings`.

  `scope.json` and `chats/` inside a campaign are read since 107 (see the scope record section), and `relations/` since 017 (see relation records). `accepted/` and `wrapups/` (021) are recognised but read only through `campaign_accepted`/`campaign_wrapups` (see accepted events and wrap-ups). `turns/` (035) likewise, only through `campaign_turns` (see played turns). `holdings/` (023) is skipped by `load()` and read only by `campaign_holdings` and `read(HoldingsRef)` (see counted holdings). `transcripts/` (036) is skipped by `load()` and read only by `chat_transcript`, `campaign_transcripts` and `read(TranscriptRef)` (see chat transcripts).

  `events/` and `evidence/` belong to 020, and `accepted/` and `wrapups/` to 021; all four stay skipped by `load()`, so `load()`'s cost never grows with campaign history. They are read only by `campaign_knowledge` (020, see [events and evidence](#events-and-evidence-020)) and by `campaign_accepted`/`campaign_wrapups` (021). An `accepted` or `wrapups` entry that is a file, not a folder, is still reported.

  These follow the [architecture layout](ARCHITECTURE.md#storage-root-and-provisional-organization) and are a deliberate deviation from an earlier draft that reserved root-level `events`/`evidence`. No root entry is a [V1 marker name](ARCHITECTURE.md#library-location-and-v1-isolation-implemented-grimoirelibrary).
- **Ignored silently:** OS and editor droppings (`.DS_Store`, `Thumbs.db`, `desktop.ini`, `*~`, `*.swp`, `*.swo`, `.#*`) and directories with no file below them. Empty directories are not state: a failed commit can leave some behind, and nothing can remove them through the file seam. Per-target `.conflicts/<hash16>/` folders accumulate in the same way.
- **Device-local:** `.txn/`, `.conflicts/` and `.sync/` are never transferred by sync (019); `load` ignores them.
- **Root entries the store does not own** (for example the `worlds/` or `catalogs/` of the [architecture's example tree](ARCHITECTURE.md#storage-root-and-provisional-organization), including a `worlds` link into V1) are reported by `load` and never written, because no store address maps to them.

## File formats

All files are JSON: UTF-8 without a BOM, a top-level object, at most 8 MiB.

**Reading is strict.** Each of the following is a load problem for that one file, never a crashed load:
- invalid UTF-8, a BOM, duplicate keys;
- `NaN`/`Infinity`, numbers that overflow, integers longer than 4300 digits;
- lone surrogates, nesting deeper than 100 levels;
- an unreadable file, or anything other than a regular file (a directory, FIFO or device) where a file belongs. These are never opened, so they cannot hang a reader.

**Writing is deterministic:** sorted keys, two-space indent, `ensure_ascii=False`, trailing newline. A caller value that could not be read back is refused as `InvalidEnvelope`; this includes nesting deeper than 99 levels inside an envelope and a rendered document larger than 8 MiB. A rewrite may reformat a hand-edited file; *values*, including unknown ones, are preserved. Numbers are read as integers (exact) or doubles: a decimal a double cannot hold exactly (`Decimal(text) != Decimal(repr(float(text)))`: excess digits, underflow such as `1e-400` to zero, a huge value) is served rounded and the file still loads and syncs byte for byte, but a rewrite that would write the rounded value back where the text held another is refused (`ImpreciseNumber`, reason `imprecise_number`, step 4), so no write silently changes a stored number in place. The check is by JSON location: a rewrite that moves such a number to another location (for example, an item inserted before it in a list) is not caught. A new definition revision is compared with its entity's current head (the declared head, or the one other revision when none is declared), since that is the file an edit was read from. `1.50`, `1e2` and `0.1` round-trip and rewrite fine (as `1.5`, `100.0`, `0.1`). For the envelope's own optional fields (`name`, `owner`, `derived_from`, `base`, `base_etag`, `title`), an explicit `null` means absent and is not written back.

**Etag (content revision):** `sha256:<hex>` of a file's exact bytes. It is never stored inside the file.

### Record envelope (`grimoire.record/1`)

| Field | Written by | Meaning |
|---|---|---|
| `format` | store | Exact version. Another version is `unsupported_format` and is never overwritten. |
| `address` | caller | A pinned `SourceRef` (definition revision) or an `InstanceRef` ([text forms](IDENTITY.md)). Must equal the address its path implies. |
| `payload` | caller | Opaque JSON. |
| `name` | caller, optional | Display name, used only for `find_by_name` candidates. An absent name stays absent. |
| `owner` | caller, optional | Attribution (nonempty text). |
| `derived_from` | caller, optional, instances only | `SourceRef` text, pinned or unpinned, kept exactly as written. |
| `base` | caller, optional, instances only | Pinned `SourceRef`: the concrete revision the instance's content was last based on. This is the retained common ancestor for 022/042. |
| `base_etag` | caller, optional | The etag of `base`'s file when it was adopted. `load` reports `base_drift` if that file has since been edited by hand. |
| `previous` | store | The etag of the file this write replaced (`null` on create). This is the per-record lineage for 019. |
| *other keys* | preserved | Unknown authored fields. |

The store retains **definition revisions** (they are immutable through the store; sync's one exception, `put_exact(replace_unreferenced=True)`, must keep the replaced bytes under `.conflicts` in the same commit) and the `base`/`previous` *identifiers* of instances. It does **not** retain the *content* an instance write replaced; ancestor-content retention for three-way merges belongs to 019/022.

### Source manifest (`grimoire.source/1`)

Fields:
- `source`: must equal the directory-derived ID.
- `owner`: nonempty.
- `title`: optional.
- `heads`: `{"<kind>/<entity_id>": "<revision>"}`. Keys are relative to the manifest's own source, so a manifest cannot declare a head for another source.
- `links`: `[{subject, relation, object}]`.
- `previous`.
- Unknown keys are preserved.

**`declared_by` is persisted by location, not as a field.** A link's declarer is always the manifest's own source. This is a deliberate deviation from "persist `declared_by`": it makes smuggling structurally impossible. A link that carries a `declared_by` key, or whose `subject` belongs to another source, is an `ownership` violation, and the whole manifest's source is excluded.

### Scope record (`grimoire.scope-record/1`) — play scopes and chats (107)

<a id="play-scopes-and-chats-107"></a>Added by [World-Optional Chat Scopes](roadmap/sprints/world-optional-chat-scopes.md) as an additive family; `record/1` and `source/1` are unchanged. Interpreted by [`grimoire.chats`](CHATS.md), never by the store.

| Field | Written by | Meaning |
|---|---|---|
| `format` | store | Exact version; another is `unsupported_format` and never overwritten. |
| `address` | caller | `campaign:<id>` (`ScopeRef`, at `scope.json`) or `campaign:<id>#chat/<chat_id>` (`ChatRef`, at `chats/<chat_id>.json`). Must equal what its path implies. |
| `schema` | caller | The payload's schema id (nonempty, ≤ 128 chars). The envelope owns it, as in 009. |
| `payload` | caller | Opaque JSON. |
| `owner` | caller, optional | Attribution. |
| `previous` | store | Etag of the replaced file. |
| *other keys* | preserved | Unknown authored fields. |

- `Transaction.put(ScopeRecord, expected)` runs every commit step a record put runs: guards, CAS, collisions (`Chats/`, `Scope.json`), `Permission.APPLY` on `campaigns/<id>/…`, journal, recovery and conflict preservation. One transaction may mix scope records, chats and instances.
- `classify` recognises exactly the two new paths. Recovery therefore accepts journals that name them and nothing else.
- A `chat_id` is limited to 123 characters by the file name.
- `load()` adds `LibraryView.scopes` and `LibraryView.chats`. Bad files are contained per file, as for instances.
- Anything else under `chats/` is `unrecognized`, including a sub-directory or a `chats` that is a file.
- **Entity instances written by 107** carry the payload schema as a preserved top-level `"schema": "grimoire.entity/0"` key in `record/1`. See [ENTITIES](ENTITIES.md#provisional-payload-codec).

**Rollback:** a pre-107 build reports `scope.json` and `chats/` as `unrecognized` and never writes or deletes them. It **cannot recover a journal that names them**: it raises `RecoveryFailed`, and reads then raise `PendingRecovery`. Recover with a 107+ build, or run `LibraryStore.discard_journal`, which does not interpret paths (tested).

### Relation records (017)

<a id="relation-records-017"></a>Added by [Typed Relationship Graph](roadmap/sprints/typed-relationship-graph.md). Interpreted by [`grimoire.graph`](GRAPH.md), never by the store.

- **An additive 010 envelope amendment, recorded here as 107's was.** `grimoire.scope-record/1`'s address union widens to `ScopeRef | ChatRef | RelationRef`. The relation address is `campaign:<id>#relation/<relation_id>`, at `campaigns/<id>/relations/<relation_id>.json`. `parse_scope_record` accepts only an exact chat or relation grammar (since 023 chosen by the address's exact form, then that family's strict grammar; see counted holdings); any other `#…` is `malformed`. No new file format is added, and `record/1` and `source/1` are unchanged.
- `relations` is no longer a reserved campaign entry. `classify` recognises exactly the new path, so recovery accepts journals that name it.
- A `relation_id` is limited to 123 characters.
- `load()` adds `LibraryView.relations`. Bad files are contained per file, as for chats. Anything else under `relations/` (a sub-directory, another suffix, a look-alike name) is `unrecognized` or `name_mismatch`, and a `relations` that is a file is `unrecognized`.
- `read(RelationRef)` returns a `StoredScopeRecord`. `campaign_relations(id)` returns `CampaignRelations(records, problems)` for **one** campaign, contained exactly as `load()` contains it (a parity test). Links are reported and never followed.
- Writes read every relation file of the campaign (no cap; O(n)).
- Instance allocators reserve relation endpoint ids through [`campaign_taken_ids`](#instance-ids-are-never-reused).

**Rollback:** a pre-017 build skips `relations/` (it is reserved there), and never writes or deletes it. Its allocators do not protect relation endpoint ids. It **cannot recover a journal that names a relation path**: it raises `RecoveryFailed`, and reads then raise `PendingRecovery`. Recover with a 017+ build, or run `LibraryStore.discard_journal` (tested).

### Accepted events and wrap-ups (021)

<a id="accepted-events-and-wrap-ups-021"></a>Added by [Turn Effects And Wrap Up](roadmap/sprints/turn-effects-and-wrap-up.md). Interpreted by [`grimoire.effects`](EFFECTS.md), never by the store.

- **Another additive 010 envelope amendment.** `grimoire.scope-record/1`'s address union widens to `ScopeRef | ChatRef | RelationRef | AcceptedRef | WrapUpRef`. The new addresses are:
  - `campaign:<id>#accepted/<event_id>`, at `campaigns/<id>/accepted/<event_id>.json`;
  - `campaign:<id>#wrapup/<wrapup_id>`, at `campaigns/<id>/wrapups/<wrapup_id>.json`.

  `parse_scope_record` dispatches by the exact fragment. No file format is added.
- These two names were never reserved. `classify` recognises exactly the new paths, so recovery accepts journals that name them.
- **`load()` skips both**, as it skips 020's `events/` and `evidence/`: one accepted file is written per played turn, so walking them would make `load()` (and 107's navigation, which is built from it) grow with play history. They are read only through `campaign_accepted(id)` and `campaign_wrapups(id)`, which return `CampaignRecords(records, problems)` for **one** campaign, contained per file as `campaign_relations` contains relations (links, look-alikes and damage reported, never followed). `CampaignRecords` generalizes 017's `CampaignRelations`, which remains its name for relations.
- One transaction may mix accepted-event, wrap-up, relation and holdings records. 021 uses that to commit an acceptance (or a revocation) with its relation changes and, since its next slice, the holder files of its 023 operation (023's `puts`, after `recheck()` under the store lock).
- **Payload schemas.** An accepted event is `grimoire.accepted-event/0`, or `grimoire.accepted-event/1` when it changes holdings or charges ability uses ([EFFECTS](EFFECTS.md#records)). Every other event, revoked or not, is still written as `/0`, byte-identical. A pre-slice build refuses `/1` as `unsupported_schema` and stops accepting (fail closed) instead of ignoring a change it cannot undo. The envelope is unchanged.
- **Event ids and holder files.** 021 never allocates an event id that a 021 token (`effect:accepted/<id>`, `effect:revoked/<id>`) in any of the campaign's holder files names, unless that holder file cannot be read and the effect has no holdings part (an effect with holdings is refused while one cannot be read). It scans their raw payloads through `campaign_holdings`, so the landed holder change of a lost event is never adopted by a new one.

**Rollback:** a pre-021 build reports `accepted/` and `wrapups/` as `unrecognized` load problems (no crash) and never writes them. Its allocators do not protect the instance ids accepted events name. It **cannot recover a journal that names them**: recover with a 021+ build, or run `LibraryStore.discard_journal` (tested).

### Played turns (035)

<a id="played-turns-035"></a>Added by [Minimal Text Scene Runner](roadmap/sprints/minimal-text-scene-runner.md). Interpreted by [`grimoire.scene`](SCENES.md), never by the store.

- **Another additive 010 envelope amendment:** the scope-record address union adds `TurnRef`, `campaign:<id>#turn/<turn_id>`, at `campaigns/<id>/turns/<turn_id>.json`. `parse_scope_record` dispatches by the exact fragment; `parse_ref` does not return it. The payload schema is `grimoire.scene-turn/1`.
- `classify` recognises exactly the new path, so recovery accepts journals that name it, and the replica treats it as an exact kind.
- **`load()` skips `turns/`**, as it skips `accepted/`: one file is written per played turn. It is read through `campaign_turns(id)` (`CampaignRecords`, contained per file) and `read(TurnRef)`. `campaign_turns(id, *, skip=None, known=None)` (048) leaves out any file whose name `skip` accepts before reading it, and returns a record from `known` (by address) unchanged when the file's bytes hash to that record's etag, so an unchanged file is read and hashed but not parsed again.
- 107's allocators treat every actor id a turn names as taken (`grimoire.scenestore.turn_instance_ids`, fail closed on an unreadable file).
- 019 synchronizes turn files as a mutable campaign-local kind since 028's turn merge ([SYNC](SYNC.md#played-turns-035-028s-turn-merge)); a turn's lines pin revisions, so an unlistable `turns/` folder means the store's reference check cannot prove a revision unreferenced.

**Rollback:** a pre-035 build reports `turns/` as an `unrecognized` load problem (no crash) and never writes it. Its allocators do not protect the actor ids turns name. It **cannot recover a journal that names a turn**: recover with a 035+ build, or run `LibraryStore.discard_journal`.

### Images and blobs (018)

<a id="images-and-blobs-018"></a>Added by [Image Identity And Placements](roadmap/sprints/image-identity-and-placements.md) as an additive family. Interpreted by [`grimoire.images`](IMAGES.md), never by the store; `record/1`, `source/1` and `scope-record/1` are unchanged.

- **Blobs** (`ImageBlob(data)`):
  - The extension is decided by the store's one sniffer (`grimoire.store.media`) from magic bytes: PNG, JPEG, GIF or WebP.
  - At most 32 MiB (`MAX_BLOB_BYTES`). The 8 MiB JSON limit does not apply.
  - A blob file's etag is `sha256:<its name without extension>`.
  - `put(blob, None)` creates. `put(blob, <actual etag>)` **repairs** a file whose bytes do not hash to its name: CAS proves which bytes are replaced, and the repaired bytes are not preserved. `put(blob, blob.etag)` raises `ImmutableBlob`.
  - Blobs are never JSON-parsed.
- **Asset records** (`grimoire.asset-record/1`, `AssetRecord`):
  - Fields mirror `scope-record/1`: `format`, `address` (an `ImageRef` or `PlacementRef`, which must equal what its path implies), `schema`, opaque `payload`, optional `owner`, `previous`, and unknown keys preserved.
  - Image records are **create-only** (`ImmutableImage`). Placements are compare-and-swap.
- **One transaction.** Blobs, asset records, definitions, manifests, instances and scope records can share one transaction. Every commit step applies to all of them: guards, CAS, collisions, `Permission.APPLY` over exact bytes, journal, recovery and conflict preservation. `classify` recognises the new paths, so recovery accepts journals that name them.
- **Ownership is checked at commit:**
  - a source asset needs its source admitted by its **own** files: a readable manifest on disk or in the transaction that the source's own scan does not exclude (`InvalidSourceState`). A manifest rewrite in the same transaction cannot clear an exclusion its records cause (an ownership violation): such a commit is refused too. An art-only commit takes that verdict from the cached per-source view instead of re-reading every revision. A cross-source exclusion is enforced by the image service (it validates against `source_facts`), not by a raw store transaction;
  - a campaign asset needs the campaign to exist: a canonical regular `scope.json` or instance file, or that campaign's scope or instance put in the transaction. Asset records alone never make a campaign exist.
- **Reads** take the store lock and never follow links:
  - `read(ImageRef | PlacementRef)`;
  - `campaign_exists(id)`;
  - `blob_stat(sha, ext)` returns `(absent | present | lookalike, size)` without reading the file;
  - `blob_etag(sha, ext)`: a file over 32 MiB raises `BlobCorrupt` unread;
  - `read_blob(sha, ext)` verifies the digest or raises `BlobCorrupt`;
  - `list_images(scope)` and `list_placements(subject)` return canonical refs plus `LoadProblem`s for stray names and for differently spelled sibling folders on the way down (`Wren-b` beside `wren-b`), without reading contents.
- **`load()`** recognises `images/` and `placements/` inside `_source/` and inside a campaign, and does not interpret them. Anything else there is `unrecognized` or `name_mismatch`. `assets/` remains a reserved root and is skipped.
- **Authorization:**
  - `assets/blobs/…` is library-global, so a grant on `sources/<s>` or `campaigns/<c>` never covers it: grant `assets` or approve each blob;
  - the service does not re-stage deduplicated bytes, so they need no new approval;
  - a preview therefore reveals whether identical bytes already exist.

**Rollback:** a pre-018 build:
- skips `assets/` silently (it was already reserved);
- reports the new `images/` and `placements/` folders as `unrecognized`, and never writes them;
- **cannot recover a journal that names them** (`RecoveryFailed`). Recover with an 018+ build, or use `discard_journal`.

### Counted holdings (023)

<a id="counted-holdings-023"></a>Added by [Counted Inventory](roadmap/sprints/counted-inventory.md) on the same `grimoire.scope-record/1` envelope; interpreted by [`grimoire.holdings`](HOLDINGS.md), never by the store.

- The address is a `HoldingsRef`: `campaign:<id>/<kind>/<instance_id>#holdings`, at `campaigns/<id>/holdings/<kind>/<instance_id>.json`.
- The address union widens to `ScopeRef | ChatRef | RelationRef | AcceptedRef | WrapUpRef | HoldingsRef`. `parse_scope_record` picks the family by the address's exact form (`…#holdings`, `#relation/`, `#accepted/`, `#wrapup/`, another `#`, or a bare scope) and applies that family's strict grammar; the six grammars are disjoint:
  - a non-text address is `malformed` with only its type named, never its value;
  - anything else, such as `campaign:x#other/y`, is `malformed`.
- Writes run the same guarded transaction:
  - collisions such as `Holdings/` or `Character/`;
  - `Permission.APPLY` on `campaigns/<id>/holdings/…`;
  - journal, recovery and conflict preservation.
- `classify` recognises exactly that path shape. Anything else under `holdings/` is `unrecognized`, including a file directly in `holdings/`, a deeper folder, or a `holdings` that is a file.
- **`load()` skips `holdings/`**, as it skips 020's `events/` and `evidence/`: a holder's operation log is campaign history (up to 7 MiB per holder), so whole-library loads such as opening a chat never pay for it. There is no `LibraryView.holdings`.
- `campaign_holdings(id)` returns `CampaignHoldingRecords(records, problems)` for **one** campaign, contained per file in the same way `load()` contains the other families: bad files, links (including a linked look-alike `Holdings`), look-alikes and an unreadable campaign folder are problems, never a crashed read. `read(HoldingsRef)` reads one file. `grimoire.holdingstore` uses only these and never loads the whole library (tested).
- `campaign_holding_names(id)` returns `(refs, complete)`: the campaign's holder files from **listings only** (no file is read). A look-alike that a case-insensitive filesystem or a case-only rename could turn into a holder file (`Holdings/`, `Character/`, `Mara-2.json`) counts under the name it folds to; a folder whose name can fold to no kind (`New folder`, `@eaDir`) is ignored. `complete=False` only when something that could hold holder files cannot be listed: the campaign folder exists only as a look-alike, or a holdings or kind folder is a link or cannot be listed or inspected.
- Instance allocators reserve every id with a holdings file (`holdings_holder_ids`, built on `campaign_holding_names`) and every id named inside a readable holder file (`holdings_peer_ids`, built on `campaign_holdings`) through [`campaign_taken_ids`](#instance-ids-are-never-reused).
- **Rollback:**
  - A pre-023 build reports `holdings/` as `unrecognized` and never touches it. Its allocators do not protect holder ids.
  - It **cannot recover a journal naming a holdings path** (`RecoveryFailed`). Recover with a 023+ build, or run `discard_journal` (both tested).

### Chat transcripts (036)

<a id="chat-transcripts-036"></a>Added by [Campaign Archaeology](roadmap/sprints/campaign-archaeology.md) on the same `grimoire.scope-record/1` envelope; interpreted by [`grimoire.transcripts`](TRANSCRIPTS.md), never by the store.

- The address is a `TranscriptRef`: `campaign:<id>#transcript/<chat_id>/<seq:010d>` (`seq` from 1), at `campaigns/<id>/transcripts/<chat_id>/<seq:010d>.json`. `parse_scope_record` picks the family by `#transcript/` and applies its strict grammar, disjoint from the others.
- **Create-only**, like 020's knowledge records: `put` with an `expected` etag raises `ImmutableRevision`, `put_exact` treats a page like a knowledge record, and journal recovery refuses (`RecoveryFailed`) a transcript target whose `old` is not null.
- `classify` recognises exactly that path shape; anything else under `transcripts/` (a file directly in it, a page name that is not ten digits from `0000000001`, a deeper folder) is `unrecognized`.
- **`load()` skips `transcripts/`.** `chat_transcript(chat)` reads one chat's folder and `campaign_transcripts(id)` every chat's, each returning `CampaignRecords(records, problems)` in page order and contained per file (damaged pages, strays, links, look-alike `Transcripts/` and look-alike chat folders are problems).
- Instance allocators reserve every id a page names through [`campaign_taken_ids`](#instance-ids-are-never-reused); an unreadable page fails closed.
- **Sync:** a synced, campaign-local, immutable kind; resolving never replaces an existing page.
- **Rollback:** a pre-036 build reports `transcripts/` as unrecognized and never writes there; it cannot recover a journal naming a transcript path (recover with a 036+ build, or `discard_journal`).

### Read-only helpers (107)

All of these take the store lock, require a consistent library, and read listings and files only:

- `read(ScopeRef | ChatRef)` returns a `StoredScopeRecord` (and since 017/021/023/035, `read(RelationRef | AcceptedRef | WrapUpRef | HoldingsRef | TurnRef)` too).
- `campaign_presence(id)` returns `absent`, `present`, or `mismatch` when only a look-alike folder exists.
- `campaign_authority_presence(id)` returns the same for the campaign's authority ledger folder (`authority/campaigns/<id>`), which a campaign id allocator must also find `absent`. It must also find the id outside `grimoire.ledgers.security_campaign_ids(store)`: every campaign a security-ledger grant or approval names.
- `campaign_instances(id)` returns canonically named instance files across kinds. It reads names only and refuses links. Both members of a [cross-kind pair](#instance-ids-are-never-reused) are listed.
- `campaign_chats(id)` returns one campaign's chat records as `CampaignRecords(records, problems)`, contained as `load()` contains them, reading only that campaign's `chats/`.
- `campaign_placement_subjects(id)` returns `(refs, complete)`: the instance subjects of one campaign's placements, from **listings only** (every `<id>` entry under `placements/<kind>/`). Look-alikes count under the names they fold to, as for `campaign_holding_names`, and `complete=False` when a folder that could hold a subject is a link or cannot be listed, or the campaign folder exists only as a look-alike.
- `excluded_sources()` returns every source `load()` would report as excluded, including by a cross-source `identity_conflict`. It runs `load()`'s own exclusion algorithm (`loader.admit`, shared with `load()` and given read-only scans) without reading campaigns. It is **incremental and cached**:
  - each source has a stat-only fingerprint of its `manifest.json` and everything under its `records/`: names, types, sizes, inodes, and nanosecond modification and change times. No file is read;
  - a source is scanned again only when its fingerprint changed, and the exclusion algorithm then reruns in memory over the cached scans;
  - images, placements, campaign and chat writes never change a fingerprint, so they never invalidate it;
  - a check on an unchanged library reads no file: it re-discovers the sources and `stat`s each manifest and revision entry. That is about 170 ms for 200 sources × 20 revisions on Linux, about half of it Python overhead rather than system calls, so it grows linearly with the library. Inside `read_pass()` it is verified only **once per pass**, and a commit inside the pass makes the next check verify again. A list view that resolves many subjects should wrap them in one `read_pass()`;
  - the cached scans keep identities, names, heads and links, but no record payloads;
  - the real spellings of `manifest.json` and `records` in `_source/` are part of the fingerprint, so a case-only rename (which keeps its stat on Windows and macOS) is seen;
  - links and reparse points are recorded and never entered;
  - **limit:** a hand edit that changes none of those stat fields is not seen until something else in that source changes. On POSIX that needs the same size, inode and timestamps (a coarse-timestamp filesystem). On Windows the check is weaker: `os.scandir` reports no inode there and its "change" time is the creation time, so a same-size replace that preserves the modification time (a `robocopy` or `cp -p` restore) is missed. Two reads narrow this for the checked source: its `manifest.json` is re-read and compared by etag on every check (heads, links and owner are therefore always current), and `check_definition` re-reads the one revision it resolves. What can still go stale is an edit missed by the stat keys to a *different* revision file of that source, or to another source that changes the cross-source verdict;
  - when to revisit: at roughly 20,000–40,000 revision files a pass approaches a second. The next step is a commit-maintained index or a filesystem watcher, not a staler stat key.
- `source_facts()` returns that verification as a `SourceFacts` value (`generation`, per-source fingerprints, `excluded`). `check_source(id, facts)` and `check_definition(ref, facts)` accept it, so a caller validating many references verifies once. Facts expire with `store.generation`, which every commit and recovery of this store bumps; an edit made by another program after they were taken is not seen until then. Without `facts`, each check verifies afresh (or once per `read_pass()`).
- `read_pass()` is a context manager that holds the store lock and verifies the facts once for its whole block. The image service runs `preview`, `commit` and `effective_placements` in one, and shares one `SourceFacts` across a batch's ops until a commit; the chat service runs `start_chat` in one. `ChatLibrary.attach_source`, `set_persona` and `attach_world` make one check each and verify once each. A caller reading many images (`read_image`, `read_image_bytes`) should wrap them in one `read_pass()`.
- **Per-source views:** each source's admission facts (its own verdict, its manifest, the entities it drops and a slim definition index) are derived once per fingerprint from the cached scan, so an unchanged source is never parsed again. `check_definition` then reads only the one resolved revision file for its envelope, and if that file changed since the scan, it forgets the source and decides afresh.
- **One-source scans skip art:** `scan_source(..., assets=False)`, used by these checks and by commit-time source validation, does not walk `images/` or `placements/` (admission never depends on art); only `load()` walks them to report stray names.
- **I/O errors are never cached:** a scan shaped by an `OSError` (an unreadable manifest, revision or folder) is not cached, so a transient error affects one call only. While such damage persists, every verification rescans that source **and** reruns the whole-library admission (about 70 ms more at 200 sources × 20 revisions on Linux).
- `check_source(source_id)` and `check_definition(ref)` agree with `load()`:
  - `check_definition` returns the definition `load()` would resolve, or a reason: `missing_source`, `excluded_source`, `entity_dropped`, `missing_entity` or `missing_revision`.
  - Parity with `load()` is tested across bad and implicit heads, kind reuse, ownership, unreadable revisions and missing manifests.
  - A cross-source `identity_conflict` is caught through `excluded_sources()` (tested with a simulated conflict, since today's loader drops bad links before the index sees them).
- `source_entities(source_id)` (added for 037's card import) lists every `(kind, entity_id)` with a revision file in one source, headed or not and readable or not, with its revision names: what a commit's id-reuse and head checks see. It reads only and judges nothing.

### Knowledge record (`grimoire.knowledge-record/1`) — events and evidence (020)

<a id="events-and-evidence-020"></a>Added by [Durable Event Evidence](roadmap/sprints/durable-event-evidence.md) as an additive, **create-only** family. `record/1`, `source/1` and `scope-record/1` are unchanged. The payloads are interpreted by [`grimoire.evidence`](EVIDENCE.md), never by the store.

| Field | Written by | Meaning |
|---|---|---|
| `format` | store | Exact version. |
| `address` | caller | A `KnowledgeRef`: `campaign:<id>#<family>/<id>`. An `event` lives at `events/<id>.json`; any other family at `evidence/<family>/<id>.json`; a log batch at `evidence/batch/<seq:010d>.json`. Must equal what its path implies. |
| `schema` | caller | The payload's schema id. |
| `payload` | caller | Opaque JSON. |
| `owner` | caller, optional | Attribution. |
| `previous` | store | Always `null`: this store never replaces a knowledge record. A non-null value reads as `malformed`. |
| *other keys* | preserved | Unknown authored fields. |

A distinct format, rather than an extension of `scope-record/1`, keeps a parser from ever treating one as a mutable scope record.

| Item | Behaviour |
|---|---|
| `Transaction.put(KnowledgeRecord, expected)` | `expected` must be `None`, otherwise `ImmutableRevision`. An existing file is `StaleWrite` (`exists`). Every other commit step applies as for a record put: guards, collisions, `Permission.APPLY` on `campaigns/<id>/…`, journal, recovery, conflict preservation. |
| `classify` | recognises exactly the two path shapes. `evidence/event/…`, a batch name other than ten digits from 1, and look-alike families are not canonical. |
| journal recovery | accepts knowledge targets, but refuses (`RecoveryFailed`) a knowledge target whose `old` is not null, so recovery can never replace an immutable record |
| `read(KnowledgeRef)` | `StoredKnowledgeRecord(record, etag)` |
| `campaign_knowledge(id)` | `KnowledgeScan(records, damaged, problems, entries)` for that campaign's two trees only; details below |

`campaign_knowledge(id)` details:
- `damaged` holds a code per unreadable canonical file: `malformed`, `unsupported_format`, `address_mismatch` or `link`.
- `problems` holds `link`, `unrecognized`, `name_mismatch` and `unreadable`, at the campaign, tree, family and file levels. Look-alike tree names such as `Events/` are included.
- `entries` is the snapshot a writer compares before committing: every file and link, canonical or not. OS noise and empty directories are not state.
- An absent campaign scans empty. A look-alike campaign reports `name_mismatch`, and a linked `campaigns/` raises `LinkRefused`.
- It never follows links, and never copies file content into a problem.
- **Director-level and store-internal:** graph, index and retrieval builders must not read it ([EVIDENCE](EVIDENCE.md#trust-boundaries-documented-not-checked-here)).

**Create-only means against this store's own writers.** Another program that creates the same file in the instant between the commit's absence check and its publish is overwritten, as for every family (the residual race R7 under [Writing](#writing)). The knowledge log then reports the loss as `edited` or `missing`; closing R7 with a no-replace publish is store work for 019.

**Rollback:** a pre-020 build skips both trees, which were already reserved, and never writes there. It cannot recover a journal that names knowledge paths: use a 020+ build, or `discard_journal` (tested).

**Sync:** knowledge files and the log are canonical user data that [019](SYNC.md) transfers. Merging two devices' logs belongs to [028](roadmap/sprints/app-managed-device-sync.md).

## Instance ids are never reused

An instance id is unique within a campaign across kinds and never goes to a different individual ([IDENTITY](IDENTITY.md)):

- the commit refuses a cross-kind pair (`InstanceIdConflict`, step 5) and `load()` leaves an existing pair out (both members);
- a campaign id stays taken while its sovereignty ledger (`authority/campaigns/<id>`) exists or a grant or approval in the security ledger (`authority/security`) names `campaigns/<id>` or anything under it, even after `campaigns/<id>` is deleted by hand (`campaign_authority_presence`, `security_campaign_ids`, [CHATS](CHATS.md#methods));
- every instance allocator takes its new id outside `grimoire.chatstore.campaign_taken_ids(store, campaign_id)`, at plan time and again under the store lock at commit ([CHATS](CHATS.md#taken-instance-ids)). It reserves every id an instance file, relation, chat roster, chat transcript page (036), accepted event, holdings file (its name and the move peers in its log), unique item's `possession` record (034), 020 knowledge file, campaign placement folder, campaign sovereignty ledger (`actor_id`s) or security-ledger grant or approval scoped to one individual (its instance, holder or placement address) names. Anything that could name ids but cannot be read or listed raises `TakenIdsUnlisted` (fail closed);
- **any later record family that names instance ids must extend `campaign_taken_ids`**, so an allocator never has to know the families.

## Writing

`LibraryStore` is trusted application code. It is never handed to a worker; workers reach it only through a `WorkerHandle` decision. Reads take no handle.

```python
with LibraryStore.open(path) as store:                 # writer lock + recovery
    txn = store.transaction()
    address = txn.put(envelope_or_manifest, expected)  # expected: the etag read, or None = "must not exist"
    txn.preview()                                      # {logical path: exact bytes} -- what a human approves
    txn.commit(handle, approvals={address: approval_id})
```

**Definition revisions are create-only.** To edit a definition, add a revision and move its head in the same transaction. A pinned reference therefore always means the same bytes, and replacing a revision raises `ImmutableRevision`.

`commit` runs in a fixed order. Steps 1–7 mutate nothing.

1. A pending journal (from a crash) refuses with `PendingRecovery`.
2. Envelopes are validated and mapped to paths. This happens at `put`.
3. **Isolation and preflight.** Every one of these is read-only:
   - the root is the directory that was opened, by device and inode (or by resolved path where the filesystem reports no inode);
   - [`check_write_destination`](ARCHITECTURE.md#library-location-and-v1-isolation-implemented-grimoirelibrary) passes for the root and for each target's deepest existing directory, which catches bind mounts. Below the root the V1 fingerprint is skipped for a directory that lies strictly below it under every alias (the library's own ids, such as campaigns `worlds` and `llm_connections`, never lock it out); every other check, and the fingerprint of the root, its ancestors and any directory a mount also names from outside the root, still runs;
   - no existing component of a target path, its `.conflicts` path, `.txn` or `.txn/lock` is a link, junction or reparse point (`LinkRefused`), or a non-directory where a directory must be. Every open, recovery, `discard_journal` and read applies the same check to `.txn` before touching anything inside it;
   - `.txn` and each target are on the same filesystem (`CrossDevice`);
   - paths fit the platform's limits: 259 UTF-16 units on Windows, `pathconf` elsewhere.
4. **Compare-and-swap.** Any target whose current etag differs from `expected` (including exists versus absent), or whose current file is in a newer format, raises `StaleWrite` and lists every stale address. Then a target the store re-encoded (a `put`, or sync state) whose new bytes would put a rounded double where the current text holds a number a double cannot hold exactly raises `ImpreciseNumber` (`where`: file → JSON pointers). A location the new document drops or sets to another value is a deliberate change and passes. A new definition revision is compared with its entity's current head on disk. Comparison is location by location, so a number moved to another location is not caught. Exact copies (`put_exact`, kept versions) write bytes verbatim and are never checked.
5. **Name collisions.** A new name that differs only by case or Unicode form from an existing sibling raises `NameCollision`. Existence is always decided from directory listings by exact spelling, never by opening a composed path, because macOS and Windows filesystems are case-insensitive.

   **One instance id per campaign.** A new instance file whose id another kind of the same campaign already holds, on disk (a look-alike file or kind folder counts, compared folded) or created in the same transaction, raises `InstanceIdConflict` (reason `instance_id_conflict`, `.paths` the refused files). It is a per-path refusal, checked under the store lock, so `put_exact` copies are refused the same way. Edits of an existing pair's members are allowed, so a human can repair one.
6. **Source consistency.** New or worsened problems in the entities and links the transaction touches raise `InvalidSourceState`:
   - a head naming a missing or unreadable revision;
   - several revision files and no head;
   - an entity ID reused under another kind;
   - a new link the loader would drop: a duplicate (including a reversed symmetric one) or one that closes a derivation cycle, checked against the links of every other source the loader would register. Only the committing source's own links are judged. Under the link-order policy below, a commit can cause *another* source's cross-source link to be dropped, and the next `load` reports it;
   - a revision without a manifest.

   A hand-made problem in an unrelated entity does not block a write, so repairs can be made incrementally.
7. **Authorization.** Every target is authorized with `Permission.APPLY` on its logical address over its exact bytes. Any denial raises `WriteDenied` and no target is written. **Trade-off:** every target is decided, so approvals already allowed for other targets in that transaction are consumed; `WriteDenied.consumed_approvals` lists them. Steps 1–6 never consume an approval.
8. **Staging.** Missing target directories are created and each file is staged as `.txn/*.new`. A `.conflicts/<hash16>/` directory is created only when something is actually preserved (after the commit point); its path was already checked in step 3, so only a genuine I/O error can fail there. An `OSError` here propagates and nothing is published; any consumed approvals are named in an exception note. A commit refused or failed at any point before its commit point (here, or at step 9's re-check) removes its staged files and every directory it created that is still empty; a directory something else appeared in since is kept.
9. **Commit point:** the root is re-verified (step 3's session check), then the journal is written and atomically replaced to `.txn/journal.json`. If that fails before the replace, nothing is published.
10. **Pre-publish guard.** If any target now equals neither its old nor its new etag, an external edit raced the commit. The journal is first durably rewritten as `abort`, then every staged version is preserved in `.conflicts/`, and `WriteConflict(published=())` is raised.
11. **Publish.** Each target is re-hashed immediately before its `replace`. An edit caught there is kept, the store's version is preserved, the remaining entries publish, and `WriteConflict` names both what was published and what was preserved. When the edit is to a revision record, its source's manifest in the same commit is kept aside with it rather than published, so a head never names bytes the commit did not write.
12. The journal is removed. Any failure in steps 10–12 raises `CommitIncomplete`, and the on-disk journal then blocks reads and commits until recovery. If staged files vanished before publication (removed by another program), nothing is published (see Recovery: vanished staging) and commit raises `CommitIncomplete` naming the targets that were not written; it never reports success for them.

**Residual race (R7, pinned by a test):** an external edit landing between step 11's per-entry re-hash and its `replace` is overwritten; for a `remove`, one landing between its re-hash and the unlink is removed (`backend/tests/test_store_deletion_recovery.py`). Multi-file atomicity holds only when no external edit happens concurrently.

## Synchronization extensions (019)

[`grimoire.sync`](SYNC.md) copies another device's file byte for byte and keeps both versions of a conflict. It does so only through these store operations, all of them riding the same journal, preflight, recovery and crash guarantees as `put`:

- **`Transaction.put_exact(path, data, expected, *, replace_unreferenced=False, regenerable=False, allowed_manifests=frozenset())`** stages exact bytes at a canonical definition, manifest, instance, play-scope, chat, relation, knowledge, accepted-event, wrap-up, holdings, 018 image-object or placement, or 036 transcript path (`replica.EXACT_KINDS`; knowledge records, transcript pages and image objects are create-only, like revisions: an image `put_exact` with an `expected` raises `ImmutableImage`). Sync copies all but 021's and 023's families, which are held back until their merge rules exist ([SYNC](SYNC.md)). Blobs are not envelopes and go through `put_blob_exact`.
  - The bytes are validated as the envelope they hold: strict JSON, supported format, address equal to the path, writable values. A failure is `InvalidEnvelope` with a `code`.
  - They are published **verbatim**: `previous` is not rewritten, so two libraries converge on one etag.
  - With `regenerable=True` (sync passes it: the bytes still exist on the other device), the entry's journal kind is `replica`, which an abort or an external edit of the target drops instead of preserving. Without it, the bytes are preserved like any `put`.
  - Definition revisions stay create-only (`ImmutableRevision`). The one exception is `replace_unreferenced`. The store enforces how it is used: the transaction must also `keep_version` exactly the bytes being replaced as a `hold` (`regenerable=False`: it may be the only copy; a reused slot is staged as a guarded no-op, so a hold is always staged), and hold nothing else but sync state, so no write can add a pin that step 6b, which reads the disk, would miss (`InvalidEnvelope` otherwise). In partial mode a replace whose kept copy is denied is dropped with it. Step 6b then refuses with `RevisionReferenced` unless nothing on the library can pin the revision. That covers a manifest head (declared, or implicit: the entity's only revision, which the loader resolves as its head), a link endpoint, text naming it in a manifest's or a link's unknown fields, an instance's `derived_from`, `base` or a pinned payload reference, a pinned reference in another revision's payload, and any text naming it in a scope, chat, relation or knowledge record (payload or unknown top-level fields), across every parsable record including excluded sources. A blocked path that could be or hold a record means "cannot prove"; a blocked entry that cannot hold one does not (including 018's `images/` and `placements/` under a source: they never name a revision). That is decided by what the entry is, not by its name (source ids may hold dots): a regular file that is no record path (`README.md`, `Desktop.ini`, a stray `Notes.json` in a source folder) holds and pins nothing, under `sources/` and `campaigns/` alike; a folder, a link or an entry that cannot be inspected always counts under `sources/`, and under a campaign's own folders; a canonical spelling blocked only because a look-alike shadows it (`shadowed`) is judged by that look-alike. If a staged file of a revision replace vanishes after the commit point (removed by another program), the committing process abandons the whole commit (`CommitIncomplete`, as for any vanished staging): nothing is published yet, so the replace never lands without the hold of what it replaced. Recovery does the same when the hold's staged file vanished unpublished while another entry is still staged. Manifests whose etag is in `allowed_manifests` are exempt (sync's `resolve` passes the winner's manifest when both devices hold it byte for byte).
- **`Transaction.delete_exact(path, expected)`** (028) stages the deletion of a canonical manifest, definition revision, instance, play scope, chat, relation or placement (`journal.DELETABLE_KINDS`) that still holds `expected`; it is sync's propagated deletion and `resolve` toward one ([SYNC](SYNC.md)). Create-only families (knowledge records, transcript pages, image objects), blobs and the held families are never deleted (`InvalidEnvelope`). The store enforces the pairing: the same transaction must `keep_version` exactly the bytes being removed as a `hold` (`regenerable=False`), so the removed version stays recoverable under `.conflicts/` and is never just unlinked (`InvalidEnvelope` otherwise, from `check()` and `commit`); in partial mode a deletion whose hold is denied is dropped with it. It is authorized as `APPLY` on the record's own address over a deletion document (`grimoire.deletion/1`: the path and the etag removed), which `preview()` shows: an approval of the record's bytes, which approves writing them, never approves removing them. Compare-and-swap is on `expected` (`StaleWrite`). Whether anything still needs the record is sync's rule (`delete_dependency`); the store checks only, at commit under the lock, that no record staying pins a revision being deleted (`RevisionReferenced`, as for a replace), judged over what the commit leaves: records it writes count with their new bytes, records it deletes not at all, and an entity's last revision being deleted is not its implicit head. Its journal kind is `remove` (below).
- **`Transaction.put_blob_exact(path, read, *, size)`** stages a create-only copy of a blob at its canonical `assets/blobs/…` path, loaded lazily: `read()` returns the bytes (or `None` when they are gone) and is called only when the commit needs them, at authorization and at staging, one blob at a time; `check()` and `staged()` never call it. Every load must be exactly `size` bytes, sniff to the path's extension and hash to its name, else `StaleWrite` and nothing is written. A path that is no blob name is `InvalidEnvelope` (`not_canonical`); a size outside 1 byte to 32 MiB is `InvalidBlob`; a blob already present makes the put stale. Its journal kind is `replica`.
- **`LibraryStore.verify_blob(path)`** reads a blob and returns its etag, or raises `RecordNotFound`, `NameCollision` (a look-alike) or `BlobCorrupt` (wrong hash, sniff or size). **`LibraryStore.read_blob_exact(path, size)`** returns the bytes only when the file is exactly `size` bytes (else `None`) and does not verify them: `put_blob_exact` verifies every load.
- **`Transaction.staged()`** returns the keys of the staged puts without loading any lazy blob.
- **`Transaction.keep_version(path, data, slot=None, *, regenerable=False)`** stages a kept version of a canonical record under `.conflicts/<hash16>/<name>~<digest12>[.N]`. With `regenerable=True` (another library still holds the bytes, as for sync) its journal kind is `keep`, dropped on abort; otherwise it is `hold`, preserved like a `put` because it may be the only copy.
  - A slot already holding those bytes is reused. For a regenerable `keep` nothing is staged; a reused `hold` is staged as a no-op compare-and-swap (old equals new), so the commit re-verifies under the lock that the possibly-only copy is still there and stops with `StaleWrite` if it vanished. Recovery accepts such an entry; any other `keep` or `hold` entry naming an existing file is refused.
  - A planned `slot` that now holds other bytes is `StaleWrite`. A `slot` must be an int from 0 to `MAX_CONFLICT_ATTEMPTS - 1`, else `InvalidEnvelope`.
  - If a `hold` must be preserved (an abort), it goes into the next free slot of the same record's folder, never into a nested conflict folder.
  - It is authorized as `APPLY` on its own address, `kept/<hash16>/<digest12>[-N]/<name>`, never on the record's.
  - At most one kept version per record per transaction, and only for the record kinds of `replica.EXACT_KINDS` (018 image objects and placements included): recovery accepts only `.json` kept paths, so a blob is refused up front (`InvalidEnvelope`).
  - A `hold` that must be preserved goes into the next free slot of its own folder, so only that folder's slots are path-checked, never a nested conflict name.
- **`Transaction.put_sync_state(name, data, expected)`** stages `.sync/device.json` or `.sync/peers/<device>/{archive,pending}.json`. This is trusted bookkeeping like the journal: it is not authorized, and a commit of state alone needs no issued handle. It may share a transaction with records, never with ledger batches.
- **`LibraryStore.scan()`** walks `sources/` and `campaigns/` as `load()` does: a noise-looking name (`desktop.ini`, `x.swp`) is skipped only when it is not a folder or link whose folded spelling is a valid identifier (so a case-renamed `Notes.swp` is still `world:notes.swp`'s look-alike; a link is blocked as `link`), an entry that can be listed but not inspected is `unreadable`, and nothing deeper than any canonical path can be is walked, so every record `load()` reads is scanned, copied and reference-checked A spelling the store never writes but that folds to one (`Campaigns`, `C9`, `Scope.json`, `_Source`) is blocked as a look-alike even when it is the only one (on a case-insensitive filesystem it is the folder), as `load()` reports `name_mismatch`, and then its canonical spelling, the one every reference names, is blocked too (reason `shadowed`), so it is never read as absent; beside its canonical spelling, only the look-alike is blocked. The reference check judges a blocked look-alike by its folded spelling, so pins below it still count.
- **`grimoire.store.syncstate.sync_peers(store)`** lists the names under `.sync/peers/`, read only (`identify` retires each peer's files when a device's ID changes). A folder that exists only as a look-alike (`.sync/Peers`) is `NameCollision`, never "no peers".
- **`Transaction.check()`** runs steps 1–6 (and 6b) read-only and raises exactly what `commit` would before authorizing. It consumes nothing. With no puts, it checks the library itself: root, V1 isolation, `.txn`, and that `.conflicts` and `.sync` are exactly spelled and not links. A commit checks `.sync` only when it holds sync state and `.conflicts` only when it holds a kept version, so a stray sync folder never blocks an ordinary write. A commit inside `cached_reads()` is refused: its checks would see stale listings.
- **`Transaction.commit(handle, approvals=None, *, on_denied=None)`**: the *partial mode*. State bytes `on_denied` returns are checked exactly as `put_sync_state` checks them.
  - Every put is authorized exactly once.
  - A put denied for its path (`not_granted`, `approval_required`, `reserved_resource`) is dropped before staging.
  - `on_denied(denied_keys)` may drop more puts (map them to `None`) or replace the bytes of *state* puts. Steps 4–6 then run again on what remains, without authorizing again.
  - Denials that are not about a path still raise `WriteDenied`: an unissued handle, or `internal_error`.
  - `CommitResult` lists `denied`, `dropped` and every consumed approval, including those of dropped dependents.
- **`preview()` keys** are what is approved: a record's logical address, a kept version's `kept/…` address, or a `.sync/…` path.
- **Journal entry kinds:** `publish` (canonical targets, via `classify`), `replica` (a canonical target holding another library's exact bytes, from `put_exact`), `keep` and `hold` (kept-version slots: regenerable, or possibly the only copy), `state` (a sync-state file) and `remove` (028: a deletion, `old` the bytes removed, its temp the deletion document). A `remove` is settled after every other entry of the commit (a source's revisions before its manifest): its target is unlinked only while it still holds `old`, and the unlink is made durable (its directory synced) before the temp and the journal go. An absent target is already done; other bytes are an external edit, kept as the human's file, and the entry is dropped (`WriteConflict`); an abort never removes anything. A commit's removals happen all or none: right before the first removal, every removal is read once, and if any of them cannot happen (its target holds other bytes, its staged document is damaged, or its temp is gone while the target is there again, with `old` or other bytes), if the hold of the bytes one removes is not in place, or if any other entry of the commit did not land as staged (held back, preserved or dropped, in this run or, in recovery, an earlier one: every other entry's target must hold exactly its planned bytes on disk, so a copy an earlier run dropped and a target edited while the app was down both count), or, in recovery, if a revision it deletes is now pinned by a record left on disk (the reference check the commit made under its lock, read again over the library as it is: a record written while the app was down, such as a new instance pinning that revision, may need it; only the disk decides, so a recovery that crashes and runs again decides the same), every removal is dropped (then only reads come before each unlink: the R7 window below). A source deleted as a whole, or a chat with its scope, never loses part of itself. This holds across a crash: when recovery cannot finish a commit whose removals had begun (another program edited a target while the app was down, removed or not, or wrote again one an earlier run had already removed: that record may need what the rest removes), it puts every removed target back from its hold, only where the path is still absent (a file written there since is the human's and stays), before it drops the rest; a target whose hold is gone too is reported `incomplete`. Recovery cannot tell its own unlink from another program's, so it keeps both: never a manifest or instance left naming a revision that is gone. Its hold is an ordinary `hold` entry of the same commit, preserved on abort. Recovery validates each target against its kind, and refuses a `keep` or `hold` entry that names an existing file (kept versions are only ever created). A `replica`, `keep` or `state` entry that would be preserved (an abort, or an external edit of its target) is **dropped** instead: its bytes are regenerable, and preserving them would leave copies nobody asked to keep, or nest conflict copies. A live commit whose settlement aborted or dropped anything raises `WriteConflict` (with `dropped`).
- **Reads:**
  - `LibraryStore.scan()` returns every canonical file's exact bytes. It also names blocked paths (`link`, `lookalike`, `unreadable`, `malformed`, `unsupported_format`, `address_mismatch`; a directory blocks its subtree) and skipped non-record entries. It never follows a link.
  - `read_exact(path)` reads a canonical or kept-version file, and `read_sync_state(name)` a `.sync` file. Both return `None` only when the file is absent; a read error propagates (a folder above it that is a file is `NotADirectoryError`), and a look-alike spelling of the file or of a folder above it is `NameCollision`.
  - `collides(path)` checks for a differently spelled existing entry.
  - `cached_reads()` reuses directory listings for a read-only phase such as a sync plan.

**R7 narrowed.** For an entry that creates a file, `settle` now re-checks the path and creates the missing directories *before* the per-entry guard read. Only the publish step's own path re-check (a stat, no read of the file) and the `replace` remain between that read and the publish (`backend/tests/test_store_replica.py::test_r7_window_is_only_the_replace_when_a_file_is_created`). An update never creates directories, so a parent deleted while the app was down is not re-created, and a parent that exists only under a look-alike spelling is never shadowed by a second folder: the guard reads the look-alike and the version is preserved.

**Rollback.** A pre-019 build cannot recover an unfinished journal that contains `replica`, `keep`, `hold` or `state` entries (`RecoveryFailed`, unknown kind). Finish recovery with a 019 build, or use `discard_journal`. Likewise a build from before 028's deletion propagation cannot recover one that contains a `remove` entry.

## Recovery

A writable `open` (and `store.recover()`) recovers before anything else.

**Validation first.** Nothing changes, not even cleanup of uncommitted staging when there is no journal, unless the root is still the one opened and outside V1. Recovery also requires that the journal and every staged file it names are regular files, not links or FIFOs, that the journal parses strictly, that every target is a canonical record or manifest path, and that every temp is a bare staging name in `.txn/`. All paths are re-checked as in step 3, so a link swapped in while the app was down is never written through. Any failure raises `RecoveryFailed`.

Then each entry follows one table, shared with publication:

| Temp | Temp digest | Target | Action |
|---|---|---|---|
| absent | — | any | already handled; the disk is kept (reported `incomplete` if the target is still the old version; see vanished staging below) |
| present | ≠ new | any | preserved; never published |
| present | = new | = old | published |
| present | = new | = new | temp removed |
| present | = new | other, or only a differently spelled look-alike | preserved; the human's file is kept |

A preserved version is named by the digest of its *actual* bytes. If that name already holds different bytes (for example, a human edited the conflict file), the version goes beside it as `….N`, so distinct bytes are never dropped. An `abort` journal never publishes. A commit journal whose temps are all still present, and which would preserve any entry, is first rewritten as `abort`. A crash part-way through preserving can therefore never resume as a partial publish. Recovery then removes the journal and any uncommitted staging, and records `store.last_recovery`: what was published, preserved and left incomplete.

**Vanished staging.** A staged file can vanish after the commit point (another program, or a person, removes "stray" `.new` files). An entry counts as published only when its target already holds its new bytes. If a staged file is missing (its target does not hold the new bytes) and no entry is published, the journal is rewritten as `abort` and nothing is published: every remaining staged version is preserved (replica, kept-version and sync-state entries are dropped, as in any abort), the missing entries are reported in `RecoveryReport.incomplete` (`incomplete` of the commit's `CommitIncomplete` when it happens live), and `aborted` is true. Live, right after the commit point, this always applies. If some entries are already published, the others are finished, except that a source manifest is never published while a revision record of the same source in that journal is incomplete or kept aside (its staged file vanished or was changed, or its slot already holds other bytes; the revision is then reported incomplete too): its staged bytes are preserved under `.conflicts` and it is reported incomplete too, so a head never names a missing revision. Manifests are staged, and so published, after every other entry of the commit, whatever order the caller put them in, so a crash part-way through publishing never leaves a head naming a revision not yet in place. **Limit:** other multi-file units have no such pairing. When a staged file of, say, a 020 knowledge batch (records plus the batch log), a 021 accepted event and its relations and holder files, or a 023 multi-holder operation vanishes *after* part of that journal was published, the published part stays and the vanished part is only reported in `RecoveryReport.incomplete`; the unit is left partly written. **Limit:** the report is not itself journaled. If recovery crashes right after rewriting a journal as `abort`, the next recovery finishes that abort (every staged version is still preserved under `.conflicts`, and nothing is published) but no longer knows which entries had vanished, so `last_recovery.incomplete` can omit them; `aborted` is still true.

**Recovery blocked.** A persistent I/O error makes a writable `open` raise `RecoveryBlocked` and release the lock. A read-only open still works, but its reads raise `PendingRecovery`. The explicit human way out is `LibraryStore.discard_journal(path)`:
- it takes the writer lock;
- it does **not** interpret the journal's paths;
- it moves the journal and all staging into the first unused `.conflicts/discarded-<N>/`, so an earlier discard is never overwritten.
- it first records its intent durably (`.txn/discard.json` names the folder). Until the discard finishes, recovery refuses to publish anything (`RecoveryFailed`) and reads raise `PendingRecovery`; running `discard_journal` again resumes into the same folder. An interrupted discard can never resume as a publishing commit.

Anything already published stays published, and `load` reports what is inconsistent.

## Reopening: `load()`

`load()` never writes and never follows a link: a link is reported as a `link` problem, and its target is never listed. It returns:
- a `DefinitionIndex` of `StoredDefinition`s;
- the readable sources and instances;
- `excluded` sources and `dropped` entities;
- `problems` (`LoadProblem(path, code, detail, source)`);
- `broken` references (`BrokenRef(path, field, text, reason)`, with the text exactly as written).

Damage is contained as locally as blame allows:

| Scope | Codes | Effect |
|---|---|---|
| file | `malformed`, `unsupported_format`, `address_mismatch` | that file is skipped (it still counts as a revision of its entity) |
| entity | `missing_head`, `bad_head`, `kind_reuse` | the entity is dropped; the rest of the source loads |
| link | `bad_link` (invalid, duplicate, or closes a derivation cycle) | the link is dropped; sources stay |
| source | `malformed`/`unsupported_format` manifest, `ownership`, `identity_conflict` | the source is excluded |
| instance pair | `instance_id_conflict` on each of the campaign's instance files that share an id across kinds (hand-made, or formed across devices) | **both** are left out of `instances`, never one picked |
| library | `link`, `unrecognized`, `name_mismatch`, `unreadable` (a directory that cannot be listed) | reported only |

**Link order.** In-source links register first, then cross-source links, each group ordered by declaring source and then by file order. When two sources' cross-source links together close a derivation cycle, the link from the alphabetically later declarer is the one dropped. **Accepted policy** (requester decision on PR 568), as is judging only the committing source's own links in step 6.

Broken-reference reasons are the index's `missing_source`, `missing_entity` and `missing_revision`, plus four store reasons:
- `excluded_source`: the source exists on disk but was excluded;
- `entity_dropped`: the entity was dropped;
- `invalid_reference`: the text does not parse;
- `base_drift`: `base` no longer has its `base_etag`.

**Removing an add-on** leaves every other file byte-identical, and its references are reported as `missing_source`.

## Sessions and concurrency

- **One writer per library.** A writable store holds an OS advisory lock on `.txn/lock` until it is closed: `flock` on POSIX, `msvcrt.locking` on Windows. The OS drops the lock when the process dies.
  - A second writer gets `LibraryLocked`.
  - On a filesystem that cannot lock (some network shares), a writable open fails with `LockUnavailable`; a read-only open still works.
  - The locker is injectable. Crash-exploration tests use a process-local `ProcessLocker`.
- **Threads.** Listing caches are per thread, so a `load` in one thread never changes what a commit in another sees. Commits, `recover()`, ledger writes, authority decisions and in-process reads (`read`, `read_source`, `load`, `read_ledger`) all take the store's re-entrant lock (`store.lock`). A read in one thread waits for a commit in another, never sees it half-done, and so never raises `PendingRecovery` for a live commit. Other processes' readers are not covered: see Readers below.
- **A linked `.txn`** is never entered. Writable opens, commits and `discard_journal` refuse with `LinkRefused`; a read-only open succeeds, but its reads raise `LinkRefused`. Remove the link by hand.
- **Readers** (`writable=False`) take no lock and never recover. They are consistent only while no writer is active: a `load` that overlaps another process's commit may see a mix of old and new files. Cross-process read isolation is deferred to Effective World Snapshots (022) and Durable Action Recovery (030).
- **Durability:** files are fsynced before they are renamed. On POSIX the parent directory is fsynced after each rename and after each directory the store creates; this is best-effort where a filesystem does not support it, but genuine I/O errors are raised. Power loss is not simulated by the harness.
- **Windows reparse points:** every reparse point counts as a link and is refused, not only symlinks and junctions. A library inside a cloud-synced folder that uses placeholder files (such as OneDrive Files-On-Demand) is therefore refused. Narrowing this to specific reparse tags is future work.
- A malformed **instance** can be replaced by passing its exact etag (`MalformedRecord.etag`); the caller has, in effect, seen those bytes. The old bytes are not copied first. A malformed definition revision can only be fixed by hand.

## Facet independence

`grimoire.store` imports only `grimoire.core.identity`, `grimoire.core.index`, `grimoire.library` and `grimoire.security`, and pure packages may not import it (`backend/tests/purity.py`). A subprocess test pins the exact set of `grimoire` modules that `import grimoire.store` loads. `grimoire.core/__init__` currently pulls in `model` and `authority`, so 009 must not re-export facet classes from `grimoire.core`, or that test fails.

## Authority ledgers (010b)

A **ledger** is an append-only, hash-chained run of batch files under the reserved root `authority/`. There are five ledger kinds: `security`; one `campaigns/<campaign_id>` ledger per campaign (sovereignty, 010b); one `adjudication/<campaign_id>` ledger per campaign ([ADJUDICATION](ADJUDICATION.md#persistence), 029); one `obstacles/<campaign_id>` ledger per campaign ([OBSTACLES](OBSTACLES.md#persistence), 040); and one `decisions/<campaign_id>` ledger per campaign ([DECISIONS](DECISIONS.md#persistence), 045).

```text
authority/security/<seq:010d>.json
authority/campaigns/<campaign_id>/<seq:010d>.json
authority/adjudication/<campaign_id>/<seq:010d>.json
authority/obstacles/<campaign_id>/<seq:010d>.json
authority/decisions/<campaign_id>/<seq:010d>.json
  {"format": "grimoire.ledger/1", "ledger": "<name>", "seq": N, "previous": "<etag of N-1>" | null,
   "entries": [ 1–500 JSON objects ]}
```

### Reading

`store.read_ledger(name)` verifies the whole chain:
- `seq` runs gap-free from 1, and each batch's `seq` matches its file name;
- each `previous` matches the etag of the batch before it, and `ledger` matches;
- the format is exact, the JSON is strict, and every batch is a regular file.

Any break raises `LedgerBroken(name, seq, reason)`; a ledger is never read partially. Other files beside the batches (for example a sync tool's "conflicted copy") are reported in `Ledger.ignored` and never replayed. For 019, that means another device's fork is dropped and only reported.

`store.ledger_documents(name)` is the one raw read, for reserving ids (`grimoire.ledgers.sovereignty_actor_ids` on `campaigns/<id>`, `grimoire.adjudicationstore.adjudication_actor_ids` on `adjudication/<id>`, `grimoire.obstaclestore.obstacle_actor_ids` on `obstacles/<id>`, `grimoire.decisionstore.decision_actor_ids` on `decisions/<id>`, and `security_campaign_ids` and `security_instance_ids` on `security`; see [`campaign_taken_ids`](#instance-ids-are-never-reused)). It returns `(documents, unread)`: the strict JSON of every canonically named batch of `authority/<name>/`, with no chain, format or entry check, and the paths that could hold batches but cannot be read (a look-alike, linked or unlistable folder; a linked, unreadable, oversized or non-JSON batch, or a look-alike batch name). Replay never uses it.

**Limit (pinned by a test):** with local file access, the chain cannot detect changes at the tail. Appending a valid batch (even one with consistent forged provenance), editing or truncating the tail, or restoring an older copy all keep a valid chain. Interior edits, deletions and reordering are detected.

### Writing

- Only trusted code writes, through one `LedgerWriter` per ledger per store session. Asking for a second writer of the same ledger raises `LedgerBusy`.
- Appends never pass through a worker's authorization; the `authority` root is reserved in `grimoire.security` too, so no grant or approval can name it.
- An append is one transaction of create-only batch files, split at 500 entries or 4 MiB. Each append checks the caller's expected head in O(1); if the head moved, it raises `StaleWrite`.
- Ledger batches ride the same journal, preflight, recovery and crash guarantees as records.

### Binding

`DurableAuthority.open` binds the store for the rest of its session; binding is permanent. Once bound:
- record commits need handles that authority issued; any other handle gets `WriteDenied`, and nothing is consumed;
- commit step 7 runs inside the authority's consequential scope, so every allow is on disk before step 8 stages anything;
- only the authority can obtain ledger writers.

An unbound store whose `authority/security` ledger exists refuses record commits. An unbound store without one keeps 010 behaviour: test fixtures may use that, production must not. Callers must not continue with a store after a failed `DurableAuthority.open`.

### Concurrency and limits

- **One lock:** the store and its authority share one re-entrant lock. A ledger flush from inside a commit re-enters it, and another thread waits.
- **No nested writes during staging:** a ledger commit started while a record commit is staging is refused, so a nested `settle` can never touch the outer commit's staged files.
- **One per process:** use one store and one authority per library per process.
- **Close order:** call `authority.close()` before `store.close()`; it flushes buffered decisions. `store.close()` never raises (requester decision, deviating from the 010b spec's `StoreError`). Closing the store first is not detected there; the later `authority.close()` raises `AuthorityUnavailable`, and the buffered non-consequential decisions are lost, as in a crash. Consequential allows are already on disk.

### Repair

`LibraryStore.repair_ledger(path, name, from_seq)` is an explicit human action:
- it takes the writer lock;
- it records its intent first in `.txn/repair.json`;
- it moves batches `>= from_seq`, newest first, into `.conflicts/ledger-<name>-<N>/`, where `/` in the name becomes `-` (`ledger-campaigns-c1-1`) and `N` is the first free number;
- if it is interrupted, running it again with the same arguments resumes it. Until it finishes, recovery and reads refuse.

**Warning:** history in the moved batches, including revocations and consumed approvals, is undone. `from_seq=1` resets the ledger, and the next authority open writes a fresh genesis.

### Growth

Ledgers grow without compaction, about one file per 500 decisions. Opening replays the whole security ledger.

## Remaining 010 obligations

- **A Markdown payload codec** (its own later pull request, by requester decision), so prose-heavy records can be authored as Markdown while the envelope stays JSON.
- **Later milestones** of the sprint: versioned snapshots and change journals, external-edit watching, ledger compaction, and device-level tests of real disk-full and permission errors.
