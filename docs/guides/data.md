# Data and backups

[Documentation](../README.md) · [Storage contracts](../reference/storage.md)

## Find the active library

Open **Configuration → Storage**. The resolver uses, in order:

1. `GRIMOIRE_HOME`, when set.
2. The `data_dir` saved in `~/.grimoire.json`.
3. `~/.grimoire`.

The bootstrap pointer is outside the library because it names the library.
Changing an environment override requires changing the launching environment;
a saved location cannot override it. In a checkout, the installed environment
can print the resolved path with `python -m grimoire.where` using that
environment's interpreter.

## Choose or move a storage location

Selecting a directory tells Grimoire where to read its library. Do not assume
that selecting an empty destination copies an existing library there. To move
files manually, stop Grimoire, copy the complete library, then choose the copied
directory and check it before removing the original.

The server refuses a storage-location change while runs are active. Finish or
cancel them first. For Android locations and USB access, see the
[Android reference](../reference/android.md#device-storage).

## Back up and restore

Use Configuration's backup controls to choose the backup folder, create a full
archive and configure scheduled backups and retention. A full archive contains
the library records and original images. Backup archives, derived `.cache/` data
and the external bootstrap pointer are excluded from the full store walk.

To restore a full archive:

1. Stop Grimoire and retain the current library as a separate recovery copy.
2. Extract the archive into a new empty directory.
3. Launch Grimoire and select that directory as the storage location.
4. Check the expected worlds and campaigns before continuing play.

A full backup is a file snapshot, not a transaction spanning all campaign files.
Taking it while writes are landing can capture related files at different
moments. Stop play before taking an important restore point.

The app can also create image-only archives. These do not replace a full backup.
Model-settings migrations create separately named safety archives. Current
settings-only safety archives protect the records that migration can rewrite;
they are partial, are not full-library restore points, and are not pruned by
ordinary backup retention.

Backups can contain story text, provider credentials, prompts and logs. Keep
them private, just like the source library.

## Native actor packs

The **Expressions** tab's **Full character / PC pack** section exports a
`.grimoire-character.zip`: all versions, ordinary art, expression manifests,
placements, hide rules and referenced image objects. This is separate from
ordinary character-card export and from a single-version expression pack.
World exports and full backups also retain expression assets. Image-only
backups include their manifests and references; they still need the corresponding
character records from a full backup.

Import a native pack into its matching character or PC type, choose a destination
actor ID, review its versions, then explicitly choose its world inheritance.
Existing local files are never overwritten: use a new ID for a copy. Campaign
packs can bind to their original, still-matching world actor and retain their
patches and hide rules. The current store has no portable actor UUID, so this
binding requires the original native file identity as well as matching card
content. A copied library, recreated actor or changed dependency requires
**Detached copy**, which materializes the exported visible expression state.
Import never creates a world or silently attaches by slug.

Native packs are limited to 512 archive members and 128 MiB, with a 20 MiB
per-member limit. Sprite decoding has the expression import limits described
in [Worldbuilding](worldbuilding.md#expression-outfits); ordinary gallery
animations remain supported. Preview errors or conflicting destinations publish
no actor. Preview tokens expire after 30 minutes and are tied to the selected
destination and library.

## Share a library between devices

Point each installation at its copy of the synced folder, use one device at a
time, and let the sync client finish before switching. Grimoire does not merge
simultaneous edits made on different devices. A conflicted copy is not a second
authoritative record to silently accept.

Same-machine process locks protect particular write domains; they do not provide
cross-device locking or promise every multi-file operation is transactional.
The [storage reference](../reference/storage.md#multiple-processes-and-sync)
states the narrower contracts.

For a USB-connected Android device, `make sync-phone` reports the proposed sync;
`make sync-phone-apply` copies. Review the report first. This is separate from a
cloud sync client.

## Export for another purpose

Use character export for a portable character card and campaign export for the
available transcript/book formats, including EPUB. A reading export is not a
complete backup: it may omit editable state, alternate responses, credentials or
other library records.

Display processing is applied to readable transcript exports; JSON transcript
export preserves raw text. Choose the format for whether you want to read the
story or retain its underlying transcript representation.

## Image maintenance

The image store keeps a shared picture once and records placements on the records
that use it. A shared description or subject update affects that picture wherever
it is reused. Removing a placement does not immediately reclaim the original.

Configuration's image-store maintenance separates two jobs:

- **Migration** moves supported legacy image placements into the shared store.
- **Collection** scans for unused images, then requires the scan token for the
  actual collection. Recent files, unresolved references and unreadable roots
  can hold collection back.

Collection uses the operating system's recycle bin and refuses an actual run
when a suitable bin is unavailable. Its report can include deferred or blocked
work; read it before assuming space was reclaimed. Do not manually delete blob
or object files to remove one record's image.

## Direct edits outside the app

Use the [store-editing procedure](../../.agents/skills/grimoire-store-editing/SKILL.md)
before scripting changes to a library. It owns the isolation, mutation and
post-edit synchronization rules. The app's API is preferable when it already
offers the operation.

After authorized direct edits, the `grimoire.cache sync` CLI can warm affected
projections. `--dry-run` plans without writing; `--no-embed` defers embedding
work. Missing files and intentionally deleted files have different meanings.
See `python -m grimoire.cache sync --help` using the installed backend interpreter
for the current arguments, rather than editing cache internals.

## Implementation references

- [Path resolution](../../backend/src/grimoire/store/paths.py) and [storage UI](../../frontend/src/components/StorageLocation.tsx)
- [Backups](../../backend/src/grimoire/store/backups.py) and [backup controls](../../frontend/src/components/BackupsPanel.tsx)
- [Image maintenance](../../backend/src/grimoire/routes/maintenance.py) and [collector](../../backend/src/grimoire/store/image_gc/)
- [Cache CLI](../../backend/src/grimoire/cache.py) and [USB synchronization](../../scripts/grimoire_sync.py)
