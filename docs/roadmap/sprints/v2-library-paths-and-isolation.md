# V2 library paths and V1 isolation

**Outcome:** V2 defaults to ~/.grimoire-v2/ rather than ~/.grimoire/; no implicit V1 reads or writes.

## First-slice status

Implemented as the read-only resolver and V1-isolation guard `grimoire.library`; the contract is in [Architecture](../../ARCHITECTURE.md#library-location-and-v1-isolation-implemented-grimoirelibrary). Tests cover:
- the default and the override;
- V1 isolation, including symlinks, case and normalization, Windows name aliases and not-yet-existing V1 roots;
- no directory creation, and no V1 directory opened during a default launch;
- the write-destination and migration-pair guards.

They pass on Ubuntu, macOS and Windows CI. A vanished working directory cannot occur on Windows, so that failure is proven by injection on every OS. Windows has no filesystem FIFOs, so the FIFO-pointer case runs only where FIFOs exist. Explicit initialization, the Android private default and a portable location are later milestones; actual writers are [Native File Store](native-file-store-and-revisions.md#inherited-from-wave-1).

## Scope and first proof
Test path resolution, GRIMOIRE_V2_HOME overrides, symlink/case normalization, refusal to use a V1 source as a V2 destination, and no directory creation on an ordinary read.

## Subsequent milestones and moonshot
Add explicit library initialization, Android private default, and user-selected portable location.

## Independence and interfaces
V2's storage path/launching module; independent of entity schema.

## Proving scenario
Launching with no override never opens V1, while explicit migration requires an authorized source and distinct destination.

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
