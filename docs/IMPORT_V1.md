# Reviewed V0.2.1 import apply

**Status:** first slice of [V1 To V2 Import Apply](roadmap/sprints/v1-to-v2-import-apply.md) (Sprint 026, Wave 4), implemented as `grimoire.import_v1`, a headless adapter over the native store, the image service and 107's chat records. It is the first writer of V2 state from a V1 library.

There is **no UI and no command yet**. Nothing is fetched and no model is called (`provider_calls` is always 0). The V1 library is only read, and the tests prove it byte-for-byte unchanged after every run. A green test suite proves this contract, not a playable app.

## Flow

```python
plan = plan_import(store, v1_root, manifest, handle=handle, owner=owner, selection=None, clock=clock)
plan.review()          # what maps where, what is written, kept, left out or unresolved (JSON-ready)
plan.preview()         # {path: exact bytes} of every write: what a human approves
plan.apply(approvals)  # one store transaction, re-checked under the store lock
```

1. **Verify.** `grimoire.migrate_v1.open_verified(source, manifest, destination=store.root)` refuses with `ManifestMismatch` unless the manifest is a `grimoire.v1-inventory/1` file, is not `blocked`, names this library as its destination, and still describes the source exactly: every entry's path, disposition, size and sha256, the `source_fingerprint`, and the manifest itself, which is re-derived from the snapshot by the same deterministic interpretation 011 ran (`roots` and `unicode_version` aside). An edited manifest is therefore refused (`manifest_changed`), as is a changed source (`source_changed`). This is a check of the reviewed file, not a second scan: nothing the plan uses comes from the re-derivation. A source that changes while it is read raises `SourceChanged`. The returned `VerifiedSource` reads only the snapshot's files and refuses reads once closed.
2. **Map** (`_map.py`, pure). Builds the desired V2 state from the manifest rows and the verified bytes.
3. **Plan** (`_plan.py`). Classifies every target against the library and the receipts, allocates nothing that is taken, and stages one `ImageBatch` holding every record, image, placement, blob and the new receipt.
4. **Apply.** Re-reads the receipts and, under the store lock, re-checks new campaigns, new actor ids and every existing record the plan builds on (each present or kept target must still have the etag it was planned against, and each campaign it extends must still exist): `StaleWrite` if anything moved. Then it commits the batch. An untrusted handle needs a covering grant and one approval per previewed path, exactly as for any 010 write.

## Selection

`Selection(worlds, campaigns={}, skip=frozenset())` maps V1 world ids to source ids and V1 campaign ids to campaign ids; `skip` lists `LegacyId` texts of portrayals to leave out. The default proposes every readable world as `world:<id>` and every campaign under its own id; a V1 id with no valid V2 spelling (`Old_Coast`) is left out and reported (`no_id_candidate`) for the reviewer to map. A selection is refused (`ImportRefused`, nothing written) when it names a world or campaign the manifest does not hold, an invalid id, the reserved `import:` source family, or two worlds onto one source.

## Mapping

| V0.2.1 | V2 |
|---|---|
| World | A source manifest with the world's name, marked `v1_import: {archive_id, legacy_id}`. The play scope carries the same marker |
| Character or PC version (portrayal) | Its **own** entity `<record>.<version>` (`mara.default`, `mara.older`), revision `v1-<sha256[:16]>`: an individual with the `agent` facet, the card's or persona's description, and the V1 file verbatim in `extras.v1.text` with its legacy id, hash, format and label. PCs and NPCs are alike: control is not a definition property |
| Several versions of one record | Separate entities joined by authored `alternate_portrayal` links in the same source, never revisions of one entity |
| Same-named characters in two worlds | Two entities in two sources; nothing is coalesced by name |
| Campaign | A 107 play scope (`grimoire.play-scope/0`) on the imported world, observed at that source manifest's etag |
| Campaign appearance | An instance `InstanceRef(campaign, "character", <record>)` derived from its pinned base portrayal. Its V1 control, role, sync state and provenance go in `extras.v1`. When the campaign plays its own copy (an override, or a lock at a card the world has changed since, `sync: world_changed`), that copy is kept verbatim in `extras.v1.campaign_copy` and flagged, not applied |
| Scene that 011 attributed (`authorship: resolved`) | A 107 chat `v1-<sha256(campaign legacy id#scene identity)[:16]>`, so a renamed scene keeps its chat; labelled with the scene title. The cast is bound (an NPC launch, or the premise when only a PC appears; the PC as persona). Post ids, order, speakers and content hashes are kept in `extras.v1_import.posts`. **No transcript is imported** (036 owns history); no belief is inferred from presence |
| Image object with an intact blob | One image object per owning source, id `v1-<sha256(px1 id)[:24]>`, with `provenance.legacy_id` and `v1_object`; the bytes are one shared blob |
| Avatar, gallery and live expression placements | Placements as in [IMAGES](IMAGES.md#v021-mapping-for-026), on the portrayal entity |
| Campaign layering `hidden` / `focus_override` / `overridden` | Campaign `hide` / `adjust` / `set` |

A scene 011 could not attribute (unreadable, an unknown role, a malformed card) is unresolved (`scene_unresolved`), never a chat with guessed or missing posts. A chat's cast is fixed when it starts, so a scene waits while any of its cast could not be imported (`cast_not_imported`); only a portrayal the selection declines lets it start without that actor. Two scenes with one identity in a campaign are both left out (`scene_identity_duplicate`), as is a scene with no V1 identity (`scene_no_identity`). A scene with two player characters starts with the first as its persona (`second_persona`), and a second one that could not be imported never holds it back. Flat records (lore, locations, groups, greetings) are listed under `not_imported` with their family. Everything the manifest left unresolved, and every losses row, is carried through the review unchanged.

## Re-runs, edits and the receipt

Every target is classified against the receipts of earlier runs:

| Outcome | When |
|---|---|
| `write` | The path is absent and no earlier run wrote it |
| `present` | Our earlier bytes are unchanged, or the bytes are identical |
| `kept` | A V2 edit since we wrote it: it is never overwritten |
| `removed` | Deleted in V2 since we wrote it: it is never recreated |
| unresolved | It exists but this archive did not write it (`source_exists`, `campaign_exists`, …); everything that depends on it is unresolved too |

Dependents follow what they rest on:
- a campaign whose play scope was deleted gets nothing more (`campaign_scope_removed`), and an apply refuses with `StaleWrite` if the campaign or its scope disappears between plan and apply;
- a campaign `adjust` or `hide` builds only on world art that is still exactly as imported, so a V2 art edit leaves the campaign's re-crop out (`placement_needs_not_imported`) instead of refusing the run;
- every image and placement is validated against the library while planning, so one that could not be committed is unresolved (`image_unavailable`, for example over a damaged blob already in the library, or `invalid_placement`) and the rest still imports;
- a V1 world or campaign is imported once, under one id: a later run that maps it to another id leaves it out (`already_imported_as`, with the id it has), and a campaign scope this archive wrote for one V1 campaign is never extended from another (`campaign_exists`).

A `present` target whose V1 content has changed since it was written (a rescanned scene with a new post, an actor whose base changed) is reported as `v1_changed`, and nothing is rewritten. The receipt stores, for every path, a digest of the content it was written for, computed without run times.

So a second run with the same manifest writes nothing, even with a later clock, and a run that adds a world only adds. The receipt is a definition revision `import:<archive>/receipt/runs@r<n>` (`grimoire.v1-import-receipt/0`): the run's review, the cumulative `{path: etag}` of everything any run wrote with the matching content digests, and the mapping of every run so far (`run_mapping` holds this run's alone). `read_receipts(store, archive)` reads it and refuses a source that is not this archive's receipt store; `legacy_map(payload)` turns it into 002's reviewed `LegacyIdMap`. A plan whose receipts moved before apply raises `StaleWrite`. If a source or play scope carries this archive's marker but the receipts are gone, planning refuses (`receipt_missing`): without them the import could not tell what was deleted since, and would restore it.

Instance ids follow [the one reservation set](CHATS.md#taken-instance-ids): a new actor's id must be outside `campaign_taken_ids` at plan time and again under the lock (an approval the commit itself spends does not count against it). A taken id is unresolved (`actor_id_taken`); `TakenIdsUnlisted` is unresolved (`taken_ids_unlisted`), never a guess. A campaign whose folder, sovereignty ledger or security grants already exist is never merged into (`campaign_exists`, `campaign_id_taken`); map it to another id.

## Unresolved reasons

Each row names the legacy id, a machine-readable `reason` and, where useful, a `detail`. Besides the ones above: `scene_unresolved`, `no_id_candidate`, `expression_layer_unavailable` (011 could not verify the outfit layer), `actor_unreadable`, `unreadable_world`, `source_unavailable`, `source_not_imported`, `entity_unavailable`, `no_candidate`, `malformed_portrayal`, `unhashed_portrayal`, `not_utf8`, `too_large` (a V1 file, or a record built from it, past the store's document limit), `entity_has_revision` (a changed V1 card on re-import), `entity_removed` (a changed V1 card for an entity deleted in V2), `unstorable_id` (an id no library can hold on every platform, such as a character or a picture called `con`: map or skip it; a selected world or campaign id like that refuses the run), `campaign_unreadable`, `scene_identity_duplicate`, `cast_not_bound` (an actor imported after its chat started), `link_not_added` (a new link to an existing manifest), `campaign_world` / `campaign_world_not_imported`, `campaign_semantics`, `campaign_copy_unreadable`, `appearance_unmapped`, `appearance_base_not_imported`, `actor_base_not_imported`, `cast_not_imported`, `second_persona`, `scene_unmapped`, `image_bytes_unavailable`, `dangling_object`, `image_scope_not_imported`, `invalid_placement`, `role_slot_mismatch`, `slot_unsupported`, `stale_expression`, `layering_no_actor`, `layering_base_not_imported`, `placement_subject_not_imported` and `placement_needs_not_imported`.

## Privacy

Reviews, receipts and errors carry ids, paths, hashes and reason codes. The V1 text is copied only into the definition it becomes; no summary of library contents is produced.

## Known limits

- **Not carried:** flat records, transcripts and response variants (036), knowledge and relationships, control assignment (003/035), outfits' and expressions' hidden and default settings, and campaign-copy differences beyond flagging them.
- **A changed V1 card on re-import** is unresolved (`entity_has_revision`): definitions are create-only, and the importer does not decide whether a new V1 version is a revision or a new portrayal. If the user deleted the entity in V2, a new card never brings it back (`entity_removed`).
- **Links** are only written with a new source manifest; links to add to an existing one are reported (`link_not_added`).
- **Recovery:** the apply is one store transaction, so a crash recovers to the library before the run or after it (`tests/test_import_v1_recovery.py`). There is no resumable batching for very large libraries yet.
- **Approvals reserve ids.** Durable approvals registered for a plan's preview name its campaign, so a fresh plan after a lost plan object or a `StaleWrite` sees that campaign id as taken (`campaign_id_taken`): revoke the approvals or map the campaign to a new id.
- **Portrayal hashes** follow V1: the text is newline-normalized before it is compared with 011's hash (a card saved with CRLF imports), while the revision id and `extras.v1.text` come from the exact bytes.
- **A chat keeps the cast it started with:** an actor whose portrayal was declined and is imported later is reported (`cast_not_bound`), not added to the chat.
- **Scenes without a V1 identity** are not imported: the chat id comes from the identity, so a rename keeps its chat.
- **One receipt document:** the receipt holds every run's writes and mapping, so a library whose receipt would pass the store's 8 MiB document limit is refused (`receipt_too_large`) before anything is written. Sharding it is follow-up work.
- **Names and titles** are shown with control characters (a pasted escape, say) replaced by spaces.
- **No remapping:** an imported world or campaign keeps its V2 id. Moving it to another id is a V2 operation, not a re-import.
- **No command or UI:** the importer is a library call.
