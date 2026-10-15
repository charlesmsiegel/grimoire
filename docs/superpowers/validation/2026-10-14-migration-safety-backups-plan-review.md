# Issue 496: implementation plan review

The user approved the written spec after commit `0f9ade879`.
Plan: `docs/superpowers/plans/2026-10-14-migration-safety-backups.md`.
Final plan SHA-256: `23d279251ff97c31ed9c6f8dc3d9940174c49077f445abc7c0476548d4daab38`.
Runtime sources remain unchanged from the spec-review baseline.

## Substantive findings and corrections

The first `/codex:adversarial-review --wait --scope working-tree` plan gate,
thread `01a13dc4-2416-7dc0-9960-eae0500555e8`, returned needs-attention:

1. A valid saved progress note did not establish ownership by the current
   archive. A new run could display the previous run's counters during
   retirement planning.
2. Some critical boundary/failure regressions were scheduled after the code
   implementing those behaviors.

The revised plan ties foreign progress to an optional per-archive nonce lock,
keeps local memory authoritative, and suppresses unverifiable remote progress
without blocking a valid archive. Every new behavioral regression is now
written and run before corresponding product code. Preservation checks are
allowed to pass on the original code. Red runs explicitly override the fast
target's first-failure stop. Pinned frontend setup is included because this
worktree has no installed Vitest executable.

One review attempt, thread `01a13dcb-cd99-7092-a95c-c1b25d9abc22`, could not
assess the corrected plan: it exceeded the runner's 24 KiB untracked-file
limit and the input omitted it. This was not an approval. Staging the plan
supplied its complete diff under the runner's normal 256 KiB inline-diff limit.

The complete staged-plan review in thread
`01a13dcd-4207-7522-b16a-4db4f915a3f4` found another concrete race: successful
status-reader probes could impersonate the old archive's lock to another
concurrent reader. The final plan serializes every such probe with a separate
root-specific nonblocking observer mutex, held until any acquired probe is
released. The archive writer never takes that mutex and never reuses a nonce.
The new handshake regression pauses one reader with a successful old-lease
probe while a second process reads the stale note during a new run's planning.

## Final plan gate

The final `/codex:adversarial-review --wait --scope working-tree` gate,
thread `01a13dd2-6fc8-7741-b423-81f24216f594`, reviewed the complete staged
plan, approved spec, and relevant local backup, migration, polling and proclock
source excerpts supplied directly. No local tools or remote source fetches
were needed; the earlier Windows sandbox launcher limitation persists.

Verdict: **approve**, no material findings. The review stated:

> Approve the staged plan gate. The observer mutex closes the demonstrated reader-impersonation race, and no concrete material blocker remains in the supplied protocol or spec coverage. Implementation remains unverified.

The command completed with exit 0. This is the plan gate only; no product
code or new regression tests have been written, and the full implementation
verification and review gates remain outstanding.

## Human handoff

Native execution in this session is recommended because the four tasks share
the archive/status contract. The writing-plans workflow requires the user to
review this plan and select an execution method before implementation. The
user's earlier approval covered the spec and planning, not this new artifact.
