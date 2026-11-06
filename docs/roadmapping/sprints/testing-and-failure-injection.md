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
