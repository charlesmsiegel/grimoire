# Local image collections

## Intent and scope

Some remote image addresses return a different picture on each request. Saving
one response as an ordinary localized image loses that behavior. Grimoire should
store the sampled alternatives as one local collection and display a random
initial member, with Previous, Next, and Reroll controls.

The source is a finite, static pool. Downloading is a one-time migration, not a
subscription. Once sampling gives sufficient confidence and the collection is
accepted, the application needs neither the source address nor a refresh job.
Final collections contain local references only. Sampling cannot prove that a
rare alternative does not exist, so reaching a request limit must never be
reported as having retrieved every possible image.

This change introduces a generic collection store, read endpoints, a shared
Markdown image renderer, and a resumable harvesting helper used by a private
migration runner. It does not add collection-management screens, scheduled
downloads, automatic detection of arbitrary rotating hosts, or promotion of
source archives into playable greetings. Operational inputs, source addresses,
record inventories, backups, and download reports remain in the private store.

## Final storage and references

Each collection has an opaque UUID id and a manifest at
`<world>/assets/image-collections/<id>.json`. The format is:

```json
{
  "format": 1,
  "members": ["collection-image-<first-sha256>", "collection-image-<second-sha256>"]
}
```

The two entries in the example represent different full SHA-256 digests, rather
than literal filenames to write. A member is an existing world-library image
name, without an extension or world id. Images live in the existing
`<world>/assets/images/` directory and use their detected extension. Full content
hashes deduplicate identical bytes across collections in the same world. Similar
but different pictures remain separate; there is no perceptual deduplication.
Member order is the order of first discovery and is preserved on resume.

Only a nonempty, unique, validated member list can be published. The final
manifest has no source URL, source alias, refresh timestamp, or harvesting state.
A UUID is independent of the source's spelling and survives its removal. All
manifest and asset writes use existing atomic store primitives and filesystem
resolvers. Mutating a collection serializes on a per-world collection-store
lock, including a process lock, so two harvesters cannot lose one another's
members. Network requests happen outside that lock.

Member bytes are immutable while referenced by a collection. Existing world
image PUT and DELETE routes reject an overwrite with different bytes or a
deletion of a referenced member with HTTP 409 (`image_in_collection`); uploading
the same bytes is harmless. These checks share the collection-store lock with
publication. No automatic collection or shared-image deletion is introduced.
Unreferenced assets left by an interrupted import are safe to retain.

Markdown uses an ordinary image reference:

```markdown
![Scene illustration](/api/worlds/realm/image-collections/<id>/image)
```

That prefix is already local under localization's `/api/worlds/` exclusion, so
ordinary localization will not replace a finished collection with one snapshot.

## Local read API

- `GET /api/worlds/{wid}/image-collections/{id}` returns format, id, and an
  ordered list of local member image URLs. It does not expose a source address.
- `GET /api/worlds/{wid}/image-collections/{id}/image` serves the first available
  member using the existing image-serving helper. This provides an ordinary
  image fallback for Markdown readers without the collection widget. The
  interactive renderer chooses the initial random member itself.

Routes validate world, collection id, schema, and member names before touching
the filesystem. Unknown worlds or collections return 404; corrupt manifests
return a readable server error. An invalid member cannot resolve outside the
world image library or supply a remote URL. Missing members caused by manual
filesystem edits are omitted from the read result; no available members returns
404. Member image URLs keep the existing version/cache semantics. The collection
read revalidates its manifest rather than permanently caching an empty result.

Router ordering must not let an existing generic entity route capture these
paths. Manifest reads, fallback reads, and ordinary member image reads never
perform an external request.

## Harvesting and finalization

The private migration runner provides explicit source addresses, known spelling
corrections, and exact target fields. Product code does not hardcode private
hostnames, character names, or source paths. Corrections preserve path case and
meaning; a malformed URL is not globally repaired by guessing.

Transient job state lives under the private home's `.cache/`, outside the world
tree. It records the source address, collection id, discovered hashes, intended
replacements, request outcomes, and progress through finalization. This state
can be used to resume an interrupted job, but is not needed to render a final
collection and is not included in a world bundle.

For each source, use the existing guarded downloader, size bounds, redirect
validation, and byte-based image detection. On each valid response:

1. Compute SHA-256. Existing bytes are a duplicate, not another saved member.
2. Store a new member atomically before recording it as discovered.
3. Reset the consecutive-duplicate counter only when a new member is found.

Default sampling stops after 30 consecutive valid duplicate responses, or 200
total requests in that invocation. The first is a candidate for acceptance; the
second leaves the job incomplete and resumable. A subsequent invocation permits
another bounded batch, preserves all previous members, and resets the duplicate
streak so it obtains fresh evidence. A failure or invalid response breaks the
duplicate streak and is never evidence of completeness. Three consecutive
failed responses stop the invocation as failed/resumable. Requests are serial
per source, with a short delay between requests. These thresholds and the delay
are configurable inputs, not facts about any private image pool.

The report distinguishes total requests, valid image responses, newly saved
images, duplicates, and failures. It records whether the stop was due to the
duplicate threshold, request limit, or failures. Confidence comes from a long
duplicate streak against a static pool; it is not a mathematical guarantee of
exhaustion. An operator can explicitly accept a nonempty sampled collection
under the already authorized migration, or run another batch before accepting.
No additional approval dialog is added to the application.

Finalization is ordered and idempotent:

1. Verify every proposed member exists and its bytes match its recorded hash.
2. Atomically publish the source-free manifest, then verify its local API reads.
3. Replace only the job's exact external image references with its local
   collection reference, preserving other text and JSON fields. Back up each
   affected private record first. Refuse to overwrite a concurrent edit; report
   the conflict and retain progress for retry.
4. Verify persisted replacements and local serving. Only then remove resolved
   entries from the private failure todo CSV, preserving unrelated rows.
5. Remove the transient source mapping when all intended replacements are
   verified. Record a local completion receipt with collection id and counts,
   without retaining a source URL in the final runtime data.

A crash between these steps leaves whole records and a resumable journal.
Once its final manifest is published, a resumed job finishes replacement and
verification rather than sampling or changing that collection's member list.
Manifest publication alone does not authorize discarding the source mapping:
an unreplaced target still needs recovery. Historical backups and audit files
are not deleted by finalization. A source used only in an archive is rewritten
there without inventing an active greeting. References shared by different
versions point to the same collection. A source that produces no valid images
keeps its original references and todo entry.

## Display behavior

One shared `MarkdownImage` component recognizes same-origin collection image
paths and fetches the local manifest. All other images retain their existing
behavior. Use it in greeting rendering and the memoized transcript/streaming
renderer, and in the existing record/Markdown previews that can display those
references. Preserve greeting image extras and pass their callback the selected
member's actual image URL, so subject metadata addresses a real image.

Choose the initial member uniformly once per displayed collection occurrence.
Previous and Next wrap around the stable member order. Reroll selects uniformly
among other available members when there is more than one, so clicking it
visibly changes the image. Display `Image n of total` with accessible button
labels. A single-member collection displays its image without navigation.
Controls work with touch and normal keyboard focus; no global shortcuts are
introduced. Only the selected image needs to load.

Selection is local UI state: browsing does not mutate greetings, transcripts,
or manifests. A stable occurrence retains its selection when its parent
rerenders, when the player types, or when streaming appends unrelated text.
Navigating away and reopening can choose another initial image. Two occurrences
of the same collection have independent selections. Changing the collection
reference resets selection. Ignore stale fetch responses after a source change
or unmount. Use span wrappers compatible with images nested inside paragraphs.

A manifest fetch failure uses the local fallback image and offers a concise
unavailable state if that also fails. A member that fails to load is skipped for
that occurrence; controls and counts reflect usable members. No failure path
falls back to the external service. Ordinary images, alt text, quote formatting,
streaming block boundaries, and existing image extras must remain intact.

## Portability and exports

World fork and bundle export/import already copy the world tree and repoint
`/api/worlds/{wid}/` references in Markdown and JSON. Relative image names in
manifests need no rewrite; collection image references do. Verify these paths
with the new collection format, including import under a different world id.

Extend the campaign export image resolver to recognize collection references
and pack the first available local member deterministically through its existing
image registry. Markdown ZIP, HTML, and EPUB exports display that representative
image without requiring an app server or remote source. Plain-text behavior
continues to use alt text. World bundles retain every member. Interactive
collection browsing in standalone campaign exports is outside this change.

## Required verification

Use placeholder worlds and existing fixture conventions, never private URLs or
record inventories. Fake downloader responses cover discovery, duplicates,
byte-identical images shared between collections, limits, invalid bytes, failures,
resume, and finalization crashes. Check that failure responses cannot satisfy
the completion criterion, incomplete jobs retain recovery information, and
successful finalization requires no source address.

Store/API tests cover atomic publication, concurrent writers, manifest validation,
path traversal, missing members, referenced-member write/delete guards, router
shadowing, and fallback serving with external access disabled. Migration tests
cover shared references, exact replacement, edited-record conflicts, failure CSV
preservation, and retry after partial completion.

Frontend tests cover greeting and transcript integration, random initialization,
wraparound, reroll, independent occurrences, selection stability during rerenders
and streaming, stale fetches, single-member behavior, missing images, accessibility,
and unchanged ordinary-image behavior. Export tests cover deterministic packed
representatives and world bundle/fork behavior under a new world id. Complete
the repository's required review and check gates before implementation is done.
