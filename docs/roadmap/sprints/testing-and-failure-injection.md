# Reliable Tests and Failure Injection

**Outcome:** Prevent the failures that are expensive in local-first, long-running campaigns: partial writes, incorrect identity, stale views and unrecoverable runs.

## Scope
- Keep unique test coverage while reducing redundant setups and brittle fixtures.
- Exercise cancellation, process restart, racing external edits, bad files and provider failures.
- Build synthetic world/campaign fixtures with explicit expected outcomes and no private content.

## Milestones
1. Catalog high-risk invariants and the tests that actually prove them.
2. Introduce reusable crash/fault harnesses around one file-backed writer and one inference run.
3. Run compatibility matrices and adversarial scenarios in CI.

## Status (first slice)
- **Delivered:** the `grimoire.testing` harness (confined, recording `FileOps` seam; `Crash`/`Fail`/`Interleave` faults; exhaustive crash, torn-write, I/O-error and recovery-crash exploration with named invariants), the [invariant catalog](../../TESTING.md), a suite-wide privacy sentinel, and an advisory OS/Python CI matrix.
- **Milestone 1:** catalog started; each invariant is marked proven-by-test or names an owner sprint.
- **Milestone 2:** **half met.** The file-backed writer harness exists. The inference-run fault harness is deferred until a provider seam exists (owner: Model Routing And Budgeting, then Per-NPC Generation Runtime). The CI policy for inference tests is already written down.
- **Milestone 3:** the compatibility matrix is configured in CI (advisory); its first results arrive with the PR into `v2`. Adversarial scenarios cover the synthetic reference writers; feature sprints add their own.
- **Open questions:** answered in the catalog's [Policies](../../TESTING.md#policies-answers-to-the-sprints-open-questions) section.
- **Deferred with owners:** "incorrect identity" and "stale views" invariants (002, 010, 022); a synthetic world/campaign fixture library (009, 010); power-loss and lock fault models (010, 030). See the catalog for the full list.

## Moonshot
Continuously generate reproducible state-machine and metamorphic tests across forks, retcons, imports and transactions.

## Proving scenario
An injected crash after one file publication is recoverable; test failure identifies the invariant violated rather than merely a timeout.

## Independence and interfaces
Test harness is independent; individual feature sprints supply additional fixtures and invariants.

## Open questions
- Which fault modes are absent from CI?
- Where does fixture reuse hide independent failure detection?
- How should nondeterministic inference tests be bounded?
