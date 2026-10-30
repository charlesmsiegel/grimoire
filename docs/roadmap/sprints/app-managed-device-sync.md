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

## Wave 2 handoff: revision-safe transfers

Consume 010's guarded record-envelope API and 019's device ancestry and conflict protocol; the input stream may contain images, but it must not infer image ownership from a raw blob hash. Authentication is independent of content-sourced permissions. The first peer transfer can use a testable reachable transport; synchronization completeness is reported only after the staged revision and all referenced blobs/placements are verified. Offline conflict preservation is guaranteed by 019; Wave 7 owns semantic resolution. No hidden dependency on a particular sync vendor, always-on server or V1 library.

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
