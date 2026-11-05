# Built-in device-to-device sync transport

**Outcome:** Synchronization is a Grimoire capability, not a Syncthing requirement; use authenticated peer or remote transfer over the same revision protocol.

## Scope and first proof
Demonstrate paired sync of a scene, character and picture between two processes over an authenticated connection with conflict/incomplete status.

## Subsequent milestones and moonshot
Add LAN peer discovery, an optional user-chosen reachable remote endpoint or relay, background auto-sync and encrypted transport.

## Independence and interfaces
Owns transfer, pairing and sync status; depends on sync revision core and permissions, not full multiplayer.

## Proving scenario
Offline changes transfer safely once devices connect. When neither device is reachable, sync needs a relay/server or user-provided remote endpoint.

## Inherited from Wave 1

The [Wave 1 audit](../README.md#wave-1-status) left these with this sprint. *Obligations* are contracts it must not violate; *first-proof additions* enlarge its first slice and are listed in the roadmap row.

**Obligations**
- Pairing keys and device credentials live under reserved `grimoire.security` roots, and a peer device is a non-owner principal that cannot issue approvals for this library (007, [tool authority](../../TOOL_AUTHORITY.md#requirements-on-later-sprints)).

## Inherited from 019

[019](sync-revisions-and-conflict-core.md) leaves these with this sprint as **first-proof obligations** ([SYNC](../../SYNC.md)):
- **Merge rules for 021 accepted events and scene wrap-ups and 023 holdings.** They are still held back: never copied, and reported as `held` conflicts whenever the devices differ (with the 017 relations of any campaign holding an accepted event). Two devices accepting offline collide on ordinals, and both stores commit several files as one unit.
- **Merging 020 knowledge logs** when both devices appended to one campaign offline (today a keep-both `missing_ancestor` conflict on the batch; a log moves only whole).
- **Dismissing kept versions** (today the next sync delivers a deleted one again).
- **Deletion propagation**, and resolving toward a deletion.
- **Per-device actor id allocation.** 107 numbers new actors by the first free number, so two devices can create byte-identical actors that merge; the offline allocation merge now also carries the 020 knowledge records that name them.
- **Archive compaction.** One archive and one pending marker per pair must fit 010's 8 MiB file cap: about 23,000 synced paths today, after which the whole sync is refused (`ArchiveTooLarge`). 018 art adds about three paths per picture.

## Wave 2 handoff: revision-safe transfers

Consume 010's guarded record-envelope API and 019's device ancestry and conflict protocol; the input stream may contain images, but it must not infer image ownership from a raw blob hash. Authentication is independent of content-sourced permissions. The first peer transfer can use a testable reachable transport; synchronization completeness is reported only after the staged revision and all referenced blobs/placements are verified. Offline conflict preservation is guaranteed by 019; Wave 7 owns semantic resolution. No hidden dependency on a particular sync vendor, always-on server or V1 library.

## One chat, many navigation links

Replicate a world-less chat as 107's **one canonical play-scope record** plus source associations, conversation history, actor/evidence data and referenced picture objects. A desktop list by character and a phone list by scenario must converge to the **same** chat ID. Transport neither requires a world file nor treats derived reverse-index caches as independent conflicts. A missing/uninstalled source remains a broken qualified link with explicit recovery status. [Chat model](../CHAT_MODEL.md).

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
