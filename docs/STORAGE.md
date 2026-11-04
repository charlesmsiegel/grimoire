# Native record store

**Status:** first slice of [Native File Store And Revisions](roadmap/sprints/native-file-store-and-revisions.md) (Wave 2, #010), implemented as the filesystem adapter `grimoire.store`. It persists **facet-neutral record envelopes** for source-qualified definitions and campaign instances. It never interprets a payload, and it does not decide whether a record is a character, a PC, a creature or an item; that meaning belongs to [Entity Facets (009)](roadmap/sprints/entity-facets-and-instance-model.md), which joins through codecs in Wave 3. There is no application or UI yet. A green test suite proves this contract, not a playable app.

Not in this slice:
- sync (019) and effective views (022). Events and evidence (020) and image blobs and placements (018) are additive families, documented below ([020](#events-and-evidence-020), [018](#images-and-blobs-018));
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
  .txn/lock, .txn/journal.json, .txn/*.new                        # writer lock, journal, staging
  .txn/discard.json                                               # an unfinished discard_journal
  .conflicts/<hash16>/<name>~<digest12>[.N]                       # preserved versions awaiting a human
  .conflicts/discarded-<N>/…                                      # output of discard_journal (first unused N)
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
  - at the root: `authority`, `config`, `assets`, `secrets`;
  - inside a campaign: `scenes`, `events`, `evidence`, `holdings`.

  `scope.json` and `chats/` inside a campaign are read since 107 (see the scope record section), and `relations/` since 017 (see relation records). `accepted/` and `wrapups/` (021) are recognised but read only through `campaign_accepted`/`campaign_wrapups` (see accepted events and wrap-ups). `holdings/` (023) is skipped by `load()` and read only by `campaign_holdings` and `read(HoldingsRef)` (see counted holdings).

  `events/` and `evidence/` belong to 020, and `accepted/` and `wrapups/` to 021; all four stay skipped by `load()`, so `load()`'s cost never grows with campaign history. They are read only by `campaign_knowledge` (020, see [events and evidence](#events-and-evidence-020)) and by `campaign_accepted`/`campaign_wrapups` (021). An `accepted` or `wrapups` entry that is a file, not a folder, is still reported.

  These follow the [architecture layout](ARCHITECTURE.md#storage-root-and-provisional-organization) and are a deliberate deviation from an earlier draft that reserved root-level `events`/`evidence`. No root entry is a [V1 marker name](ARCHITECTURE.md#library-location-and-v1-isolation-implemented-grimoirelibrary).
- **Ignored silently:** OS and editor droppings (`.DS_Store`, `Thumbs.db`, `desktop.ini`, `*~`, `*.swp`, `*.swo`, `.#*`) and directories with no file below them. Empty directories are not state: a failed commit can leave some behind, and nothing can remove them through the file seam. Per-target `.conflicts/<hash16>/` folders accumulate in the same way.
- **Device-local:** `.txn/` and `.conflicts/` must never be transferred by sync (019/028).
- **Root entries the store does not own** (for example the `worlds/` or `catalogs/` of the [architecture's example tree](ARCHITECTURE.md#storage-root-and-provisional-organization), including a `worlds` link into V1) are reported by `load` and never written, because no store address maps to them.

## File formats

All files are JSON: UTF-8 without a BOM, a top-level object, at most 8 MiB.

**Reading is strict.** Each of the following is a load problem for that one file, never a crashed load:
- invalid UTF-8, a BOM, duplicate keys;
- `NaN`/`Infinity`, numbers that overflow, integers longer than 4300 digits;
- lone surrogates, nesting deeper than 100 levels;
- an unreadable file, or anything other than a regular file (a directory, FIFO or device) where a file belongs. These are never opened, so they cannot hang a reader.

**Writing is deterministic:** sorted keys, two-space indent, `ensure_ascii=False`, trailing newline. A caller value that could not be read back is refused as `InvalidEnvelope`; this includes nesting deeper than 99 levels inside an envelope and a rendered document larger than 8 MiB. A rewrite may reformat a hand-edited file; *values*, including unknown ones, are preserved. For the envelope's own optional fields (`name`, `owner`, `derived_from`, `base`, `base_etag`, `title`), an explicit `null` means absent and is not written back.

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

The store retains **definition revisions** (they are immutable through the store) and the `base`/`previous` *identifiers* of instances. It does **not** retain the *content* an instance write replaced; ancestor-content retention for three-way merges belongs to 019/022.

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
- **Instance allocators must use `grimoire.graphstore.relation_endpoint_ids`** (see [GRAPH](GRAPH.md#endpoint-ids-stay-taken)), so the id of a hand-deleted relation endpoint is never reused.

**Rollback:** a pre-017 build skips `relations/` (it is reserved there), and never writes or deletes it. Its allocators do not protect relation endpoint ids. It **cannot recover a journal that names a relation path**: it raises `RecoveryFailed`, and reads then raise `PendingRecovery`. Recover with a 017+ build, or run `LibraryStore.discard_journal` (tested).

### Accepted events and wrap-ups (021)

<a id="accepted-events-and-wrap-ups-021"></a>Added by [Turn Effects And Wrap Up](roadmap/sprints/turn-effects-and-wrap-up.md). Interpreted by [`grimoire.effects`](EFFECTS.md), never by the store.

- **Another additive 010 envelope amendment.** `grimoire.scope-record/1`'s address union widens to `ScopeRef | ChatRef | RelationRef | AcceptedRef | WrapUpRef`. The new addresses are:
  - `campaign:<id>#accepted/<event_id>`, at `campaigns/<id>/accepted/<event_id>.json`;
  - `campaign:<id>#wrapup/<wrapup_id>`, at `campaigns/<id>/wrapups/<wrapup_id>.json`.

  `parse_scope_record` dispatches by the exact fragment. No file format is added.
- These two names were never reserved. `classify` recognises exactly the new paths, so recovery accepts journals that name them.
- **`load()` skips both**, as it skips 020's `events/` and `evidence/`: one accepted file is written per played turn, so walking them would make `load()` (and 107's navigation, which is built from it) grow with play history. They are read only through `campaign_accepted(id)` and `campaign_wrapups(id)`, which return `CampaignRecords(records, problems)` for **one** campaign, contained per file as `campaign_relations` contains relations (links, look-alikes and damage reported, never followed). `CampaignRecords` generalizes 017's `CampaignRelations`, which remains its name for relations.
- One transaction may mix accepted-event, wrap-up and relation records. 021 uses that to commit an acceptance and its relation changes together.

**Rollback:** a pre-021 build reports `accepted/` and `wrapups/` as `unrecognized` load problems (no crash) and never writes them. Its allocators do not protect the instance ids accepted events name. It **cannot recover a journal that names them**: recover with a 021+ build, or run `LibraryStore.discard_journal` (tested).

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
- **Instance allocators must treat ids with a holdings file as taken** (`grimoire.holdingstore.holdings_holder_ids`, built on `campaign_holding_names`, which 107's allocator uses alongside 017's `relation_endpoint_ids` and 021's `accepted_instance_ids`), so a hand-deleted holder's id never goes to a different individual.
- **Rollback:**
  - A pre-023 build reports `holdings/` as `unrecognized` and never touches it. Its allocators do not protect holder ids.
  - It **cannot recover a journal naming a holdings path** (`RecoveryFailed`). Recover with a 023+ build, or run `discard_journal` (both tested).

### Read-only helpers (107)

All of these take the store lock, require a consistent library, and read listings and files only:

- `read(ScopeRef | ChatRef)` returns a `StoredScopeRecord` (and since 017/021/023, `read(RelationRef | AcceptedRef | WrapUpRef | HoldingsRef)` too).
- `campaign_presence(id)` returns `absent`, `present`, or `mismatch` when only a look-alike folder exists.
- `campaign_instances(id)` returns canonically named instance files across kinds. It reads names only and refuses links.
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

**Sync:** knowledge files and the log are canonical user data that 019/028 will transfer. Merging two devices' logs belongs to 019.

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
   - [`check_write_destination`](ARCHITECTURE.md#library-location-and-v1-isolation-implemented-grimoirelibrary) passes for the root and for each target's deepest existing directory, which catches bind mounts;
   - no existing component of a target path, its `.conflicts` path, `.txn` or `.txn/lock` is a link, junction or reparse point (`LinkRefused`), or a non-directory where a directory must be. Every open, recovery, `discard_journal` and read applies the same check to `.txn` before touching anything inside it;
   - `.txn` and each target are on the same filesystem (`CrossDevice`);
   - paths fit the platform's limits: 259 UTF-16 units on Windows, `pathconf` elsewhere.
4. **Compare-and-swap.** Any target whose current etag differs from `expected` (including exists versus absent), or whose current file is in a newer format, raises `StaleWrite` and lists every stale address.
5. **Name collisions.** A new name that differs only by case or Unicode form from an existing sibling raises `NameCollision`. Existence is always decided from directory listings by exact spelling, never by opening a composed path, because macOS and Windows filesystems are case-insensitive.
6. **Source consistency.** New or worsened problems in the entities and links the transaction touches raise `InvalidSourceState`:
   - a head naming a missing or unreadable revision;
   - several revision files and no head;
   - an entity ID reused under another kind;
   - a new link the loader would drop: a duplicate (including a reversed symmetric one) or one that closes a derivation cycle, checked against the links of every other source the loader would register. Only the committing source's own links are judged. Under the link-order policy below, a commit can cause *another* source's cross-source link to be dropped, and the next `load` reports it;
   - a revision without a manifest.

   A hand-made problem in an unrelated entity does not block a write, so repairs can be made incrementally.
7. **Authorization.** Every target is authorized with `Permission.APPLY` on its logical address over its exact bytes. Any denial raises `WriteDenied` and no target is written. **Trade-off:** every target is decided, so approvals already allowed for other targets in that transaction are consumed; `WriteDenied.consumed_approvals` lists them. Steps 1–6 never consume an approval.
8. **Staging.** Missing target directories are created and each file is staged as `.txn/*.new`. A `.conflicts/<hash16>/` directory is created only when something is actually preserved (after the commit point); its path was already checked in step 3, so only a genuine I/O error can fail there. An `OSError` here propagates and nothing is published; any consumed approvals are named in an exception note.
9. **Commit point:** the root is re-verified (step 3's session check), then the journal is written and atomically replaced to `.txn/journal.json`. If that fails before the replace, nothing is published.
10. **Pre-publish guard.** If any target now equals neither its old nor its new etag, an external edit raced the commit. The journal is first durably rewritten as `abort`, then every staged version is preserved in `.conflicts/`, and `WriteConflict(published=())` is raised.
11. **Publish.** Each target is re-hashed immediately before its `replace`. An edit caught there is kept, the store's version is preserved, the remaining entries publish, and `WriteConflict` names both what was published and what was preserved.
12. The journal is removed. Any failure in steps 10–12 raises `CommitIncomplete`, and the on-disk journal then blocks reads and commits until recovery. If staged files vanished before publication (removed by another program), commit also raises `CommitIncomplete` naming the targets that were not written; it never reports success for them.

**Residual race (R7, pinned by a test):** an external edit landing between step 11's per-entry re-hash and its `replace` is overwritten. Multi-file atomicity holds only when no external edit happens concurrently.

## Recovery

A writable `open` (and `store.recover()`) recovers before anything else.

**Validation first.** Nothing changes, not even cleanup of uncommitted staging when there is no journal, unless the root is still the one opened and outside V1. Recovery also requires that the journal and every staged file it names are regular files, not links or FIFOs, that the journal parses strictly, that every target is a canonical record or manifest path, and that every temp is a bare staging name in `.txn/`. All paths are re-checked as in step 3, so a link swapped in while the app was down is never written through. Any failure raises `RecoveryFailed`.

Then each entry follows one table, shared with publication:

| Temp | Temp digest | Target | Action |
|---|---|---|---|
| absent | — | any | already handled; the disk is kept (reported `incomplete` if the target is still the old version) |
| present | ≠ new | any | preserved; never published |
| present | = new | = old | published |
| present | = new | = new | temp removed |
| present | = new | other, or only a differently spelled look-alike | preserved; the human's file is kept |

A preserved version is named by the digest of its *actual* bytes. If that name already holds different bytes (for example, a human edited the conflict file), the version goes beside it as `….N`, so distinct bytes are never dropped. An `abort` journal never publishes. A commit journal whose temps are all still present, and which would preserve any entry, is first rewritten as `abort`. A crash part-way through preserving can therefore never resume as a partial publish. Recovery then removes the journal and any uncommitted staging, and records `store.last_recovery`: what was published, preserved and left incomplete.

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

A **ledger** is an append-only, hash-chained run of batch files under the reserved root `authority/`. There are two ledger kinds: `security`, and one `campaigns/<campaign_id>` ledger per campaign.

```text
authority/security/<seq:010d>.json
authority/campaigns/<campaign_id>/<seq:010d>.json
  {"format": "grimoire.ledger/1", "ledger": "<name>", "seq": N, "previous": "<etag of N-1>" | null,
   "entries": [ 1–500 JSON objects ]}
```

### Reading

`store.read_ledger(name)` verifies the whole chain:
- `seq` runs gap-free from 1, and each batch's `seq` matches its file name;
- each `previous` matches the etag of the batch before it, and `ledger` matches;
- the format is exact, the JSON is strict, and every batch is a regular file.

Any break raises `LedgerBroken(name, seq, reason)`; a ledger is never read partially. Other files beside the batches (for example a sync tool's "conflicted copy") are reported in `Ledger.ignored` and never replayed. For 019, that means another device's fork is dropped and only reported.

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
