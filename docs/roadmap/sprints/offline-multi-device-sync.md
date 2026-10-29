# Concurrent Offline Editing and Multi-Device Reconciliation

**Outcome:** Once the early app-managed phone/desktop sync path exists, safely reconcile two devices that independently edited the *same* campaign offline.

## Scope

- V2's early [sync revision foundation](sync-revisions-and-conflict-core.md) and [app-managed transport](app-managed-device-sync.md) are prerequisites, not features postponed to this sprint.
- Build domain-specific merge/review for two valid offline branches, including scene posts, event history, world definition overrides, images and actor-private evidence.
- Never use timestamps, whichever device synced last, or implicit deletion to resolve different accepted events.
- Support keep-both/fork/review, remap histories with stable identities, and resume incomplete transfers without duplicate materialization.
- Treat Syncthing, remote folder providers and custom transports as optional integration layers; *the product requirement is that information actually synchronizes*.

## Milestones

1. Demonstrate detectable and inspectable conflicting scene edits produced by two otherwise compatible V2 libraries.
2. Resolve one non-conflicting concurrent set automatically and a conflicting set through explicit fork/merge.
3. Handle disconnected phone/desktop histories, media, tombstones and retcons for long periods, including optional remote relay.

## Moonshot

A long campaign can be played and edited on multiple devices, online or offline, and can converge without data loss while preserving genuine alternate branches.

## Proving scenario

One device changes a relationship after a scene; another device edits the same scene while offline. Both histories survive; no false 'sync complete' status and no silent replacement.

## Independence and interfaces

Depends on [phone play](v2-mobile-client.md), [transport](app-managed-device-sync.md) and [revision core](sync-revisions-and-conflict-core.md). Owns **semantic concurrent-edit reconciliation**; basic authenticated sync is already earlier.

## Open questions

- Which conflicting histories can be auto-reconciled without false causality?
- What happens when one device edits a deleted or retconned scene?
- How should a user preview consequences of a merge before choosing?
