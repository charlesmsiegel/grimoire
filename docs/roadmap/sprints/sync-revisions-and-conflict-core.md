# Synchronization revisions and conflict detection

**Outcome:** A transport-independent version history makes V2 libraries safe across independently edited devices.

## Scope and first proof
Demonstrate deterministic merge of nonconflicting file revisions and explicit keep-both conflict with two local copies; preserve tombstones and integrity checks.

## Subsequent milestones and moonshot
Idempotent interrupted transfers, retained ancestry, causal branches and user-visible reconciliation.

## Independence and interfaces
Storage/synchronization protocol; depends on native revision contract; not world/campaign inheritance.

## Proving scenario
A desktop edit and phone edit cannot silently overwrite one another based on timestamps.

## Inherited from Wave 1

The [Wave 1 audit](../README.md#wave-1-status) left these with this sprint. *Obligations* are contracts it must not violate; *first-proof additions* enlarge its first slice and are listed in the roadmap row.

**First-proof additions**
- Prove keep-both conflicts under `explore_crashes` with `Interleave`/`meddle` on replica roots, and add the `preserves_interleave` built-in invariant. Wave 1's "repeatable faults for native record and sync writes" delivered only a synthetic replica copy (006).

## Wave 2 handoff: stable revision and source metadata

Use 010's generic revisioned envelopes and compare-and-swap, **not** 009's entity-specific serializer and not 042's semantic source-rebase implementation. Device synchronization must distinguish *device edit ancestry* from *world/campaign source inheritance*. Keep deleted records/tombstones and missing ancestors as named conflicts; clocks/timestamps cannot decide a winner. Exercise the first transport-free divergent-copy scenario and keep-both behavior with 006's fault harness and 010's actual writer. Do not claim two-device network transport is complete here (028 owns it).

## Chat and campaign scope synchronization

The new 107 world-less scope/chat records are canonical, revisioned **campaign-local** data, even if the user sees them in Character or Scenario navigation views. Preserve their source bindings and one canonical history through divergent-device revision analysis. Cached reverse indexes are **derived**, never synchronized as an authoritative alternate owner.

A conflict while adding cards or attaching a world cannot silently drop a participant, overwrite the accepted chat or create a duplicate chat under the other navigation parent. No requirement for a world file or server-side database. [Chat model](../CHAT_MODEL.md).

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
