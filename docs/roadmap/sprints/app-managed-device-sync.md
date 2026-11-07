# Built-in device-to-device sync transport

**Outcome:** Synchronization is a Grimoire capability, not a Syncthing requirement; use authenticated peer or remote transfer over the same revision protocol.

**Status:** first slice implemented as `grimoire.peersync` ([DEVICE_SYNC](../../DEVICE_SYNC.md)): pairing, an authenticated loopback transfer over 019's core with `synced`/`conflicts`/`incomplete` status, and per-device actor IDs. The other obligations below are still open.

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
- **Merging 020 knowledge logs** when both devices appended to one campaign offline: done. An identified library names each batch for its device and an append extends every head, so the two devices' appends are different files that sync copies create-only into one log both read whole; a union 020 would not read whole, or rulings on one proposition from both devices, stays one held unit (`unapplicable`) ([SYNC](../../SYNC.md#knowledge-logs-028), [EVIDENCE](../../EVIDENCE.md#the-knowledge-log)).
- **Dismissing kept versions:** done. `dismiss` records the path and the dismissed bytes' etag per pair, only for a version a sync wrote here from the peer, recorded in the commit that writes the keep (never one `resolve` held, which every pair's record lists as `held`); a sync never keeps those bytes for that device again on that conflict's base, a different later version still is, the other device is untouched, and the record ends with its conflict ([SYNC](../../SYNC.md#keep-both)).
- **Deletion propagation**, and resolving toward a deletion.
- **Per-device actor id allocation:** done in the first slice; an identified library numbers actors `<entity>-<tag>-<n>`, and 034's unique items the same way ([DEVICE_SYNC](../../DEVICE_SYNC.md#per-device-ids)).
- **Archive compaction:** done. An archive or marker past one file is sharded by path hash (`grimoire.sync.archive/2`, `pending/2`), up to about six million paths per pair; a `/1` file is still read and written while it fits. Blob tombstones are dropped (provably redundant); record tombstones are kept, since each is a resurrection check ([SYNC](../../SYNC.md#sharding-and-tombstones-028)).
- **Resolving a conflict over the transport:** done; either paired device ends a reported conflict with 019's `resolve` over the authenticated connection, and the receiver records its replaced version as `held` in a commit of its own just before the one that writes the hold, as a local resolve does, so it is never dismissable, even after a recovery that loses the hold commit's staged `.sync` state ([DEVICE_SYNC](../../DEVICE_SYNC.md#resolving-a-conflict-over-the-transport)).

## Milestone: merges before 044

Still owed by this sprint, and due **before Sprint 044**:
- **Merge rules for 021 accepted events and scene wrap-ups, and for 023 holdings.** Until then they stay `held`: never copied, reported whenever the devices differ.
- ~~**Merge rules for 035 played turns**~~: done. Sync carries `turns/` as a mutable family, never landing a turn without its chat, play scope, preceding turn and chosen turn; two devices' turn *n* make the chat forked on both, and the library owner chooses which branch goes on (`SceneRunner.choose`): the others are set aside from that number on, kept but heard by no one, by a `set_aside` mark on each losing branch head that travels like any turn edit. Different offline choices are a conflict both devices show, never a flip ([SCENES](../../SCENES.md#forked-chats), [SYNC](../../SYNC.md#played-turns-035-028s-turn-merge)). A UI for the choice is 065's.
- ~~**Merging 020 knowledge logs** that both devices appended to offline~~: done ([SYNC](../../SYNC.md#knowledge-logs-028)). Still owed by the allocators (021/029/032/035): device-tagged record ids and sequences taken from the merged high-water mark.
- **Deletion propagation**, and resolving toward a deletion.

## Wave 2 handoff: revision-safe transfers

Consume 010's guarded record-envelope API and 019's device ancestry and conflict protocol; the input stream may contain images, but it must not infer image ownership from a raw blob hash. Authentication is independent of content-sourced permissions. The first peer transfer can use a testable reachable transport; synchronization completeness is reported only after the staged revision and all referenced blobs/placements are verified. Offline conflict preservation is guaranteed by 019; Wave 7 owns semantic resolution. No hidden dependency on a particular sync vendor, always-on server or V1 library.

## One chat, many navigation links

Replicate a world-less chat as 107's **one canonical play-scope record** plus source associations, conversation history, actor/evidence data and referenced picture objects. A desktop list by character and a phone list by scenario must converge to the **same** chat ID. Transport neither requires a world file nor treats derived reverse-index caches as independent conflicts. A missing/uninstalled source remains a broken qualified link with explicit recovery status. [Chat model](../CHAT_MODEL.md).

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
