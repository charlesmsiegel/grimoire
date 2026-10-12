# Spec 03 implementation validation — 2026-10-12

All six implementation slices and contracts 03-C1–C9 are implemented on a
branch based on main `246a0e73a87`. The branch remains unmerged. The final
fidelity review found a vector-history race at `391113b6b`; correction
`c291eafb1` has owning coverage but still needs mandatory review approval.
Review/Final columns remain open. At the user's request, remaining review and
CI work moves to the pull request; local implementation stops here.

The measured-cost gate in the originating spec (§§8/14) withheld search,
card and shelf SQL persistence after they lost their cost comparisons. Their
live behavior remains. Encoder-counted supplied text uses explicit zero-file
identity: exact body and encoder for bounded memory, SHA/encoding/BUILD for
persistence. Persistent admission requires 8,388,608 characters. These are
recorded decisions, not claims that every tentative consumer was wired.
See the [synthetic measurement report](2026-10-11-spec-03-compiled-cache.md).

## Theory

The filesystem supplies membership; the cache answers explicit live paths and complete keys.
Validated byte snapshots determine artifacts; unknown provenance requires reading bytes.
Materialized history records work, never membership or byte currency; vectors remain independent of BUILD.
Durable delete markers/intents fence staging before key work/vector I/O and before/after commit.
Owned schema/virtual definitions and protected planner rows are erased; canonical public DDL is rebuilt atomically before physical vacuum.
Completed delete descriptors stay registered for fork safety and are released under a nonblocking, bounded cleanup protocol.
Supplied-text counts use exact body/encoder identity; a new projection must first pass the cost gate.

## Current-source evidence

| Check | Observed result | Scope |
|---|---|---|
| Final native owning cache/purge/maintenance/lifecycle/SQL guards and vectors | 486 passed, 5 skipped | Exact runtime and guard snapshot `c291eafb1` |
| Final Android dependency owning suites | 486 passed, 5 skipped | Actual Python3.12/Pydantic1 on the same source |
| Earlier affected Android matrix | 1,086 passed, 12 skipped | Includes token/native-metadata and structural obligations; final narrow fixes have owning coverage |
| Ruff / mypy | 1,136 / 161, exactly baselines | No baseline increases |
| Simulated Linux mypy | 166 findings in 54 files | Exact main control has the same counts; final four new modules add no errors; Windows simulation only |
| Frontend | 5,004 tests in 231 files; typecheck passed | Updated-main gate; subsequent fixes change backend only |
| Templates / ESLint | 460 checks / 706 baseline findings | Fresh refresh passed |
| Full native checkpoint | 19,143 passed, 40 skipped, one failed; 94.01% coverage | Code `c45c140765` before later DDL/guard fixes; parser CPU-time check failed under load |
| Full fresh Android checkpoint | 19,137 passed, 46 skipped, one failed | Same runtime checkpoint; unchanged nanosecond-generation check |
| Frozen fixture originals | No diff from main | Neither home/ nor snapshot regenerated |
| APK | `391113b6b` build succeeded: 49 tasks, 10 executed | SQLite layers and exact source digest `be669ac58028c2a441de88ed8dfb38af888a3e65df8e87f27ea38fdab62554c2` checked; vector correction requires a fresh build |

No green whole `make check` or final-tip full-suite result is claimed.
The parser timing test passes in isolated replay on both exact main and the
current source. The `not-a-generation` nanosecond assertion fails on both.
Those are recorded controls, not hidden skips. The parser implementation and
that timing-test body are unchanged by this branch. The approved final plan
allows accurate partial-gate evidence with baseline comparison.

## Independent attacks and mandatory gates

Independent review at `9843b8ea6` closed the contentless-FTS blocker after
25 targeted attacks and 15 focused obligations passed. It identified ordinary
inherited/aliased constructor guard gaps; four root RED cases plus four
permitted-false controls now pass through bounded constructor ancestry and
the existing export graph. All 210 constructor/query guard cases passed in
native and Android dependency environments. Native review also found a SQL
RETURNING inventory bypass: six RED cases now reject those row readers,
while quoted/comment controls remain allowed; all 217 guard cases pass.

Final fidelity review (`01a13022-8063`) and independent replay proved that
views, DEFAULT expressions and even cache_meta's DDL retained text after
generation completion. Root 18 RED cases and independent 18 cases reproduced
this across current/obsolete files and enabled/cache0/no-BUILD modes. The
canonical in-place reset removes owned triggers, views, virtual tables and
shadows, ordinary tables and their index/default definitions in bounded
phases. SQLite's protected planner/sequence rows are erased too: an orphaned
sqlite_stat1 placeholder was independently covered by a root RED case.
The final transaction rebuilds public DDL through the same helper as initial
ownership, including cache_meta, preserving only the proved schema version
and old generation. Ownership remains present at every committed boundary.
Generation advances only after physical VACUUM succeeds. Unsupported old
layouts remain disposable; healthy files retain identity and held connections.
Current enabled caches can store and read a fresh artifact after the purge.

An independent six-case metadata identifier alias replay found SQLite accepts
`Cache_meta` while the protected-name comparison did not. SQL and Python now
use the same ASCII case-insensitive metadata identity. Root 24 DDL cases,
including metadata literals and case aliases, pass inside the final 439-test
owning suite. Independent review at `391113b6b` passed 49 attacks, 59 focused
obligations and four added controls with a non-blocking verdict.

Mandatory fidelity review `01a13048-a5d8` then found vector-history admission
opened a fresh batch after vector I/O, allowing a completed campaign purge to
be followed by history containing the deleted source path. Both deterministic
save/load interleavings failed before correction; independent public campaign
deletion replay confirmed both failures. At `c291eafb1`, the existing batch
pins the root, lifecycle and durable boundary before vector key work/I/O,
and the same request publishes history. Optional context/hash failures disable
partial history while preserving valid vectors. The owning 486-test native
and actual Android-dependency suites pass. Mandatory review of this correction
remains owed; Review/Final columns stay open for the PR.

The latest APK attempt failed before compilation because it specified an
unavailable build Python path. Use the earlier successful build interpreter
`C:/Python312/python.exe`; no final-vector APK success is claimed.

The spec expressly reconciles canonical schema transactions/full purge VACUUM
with original single-transaction/incremental-vacuum wording. Ordinary eviction
remains incremental. Healthy files erase in place; independently proved
corrupt/asides keep their explicit NO_WAIT unlink exception. Foreign files,
unproved journals and links are preserved. Schema/name transfer bounds are
not a fixed row-deletion or vacuum time budget. Permanently active/foreign
leading intent pages can retain tails while payloads are erased and storage
stays fenced. Failed marker/control I/O and unproved ownership remain limits.

## Earlier full-gate checkpoint and limits

The earlier rebased full native run passed 19,113 tests, 40 skipped, with
93.44% coverage at the `234eb7f22`/`f824a21b2` checkpoint, before later optional
memo/paging fixes. Its predecessor exposed a real fresh benchmark-worker
import failure under Make's package-only PYTHONPATH; the fix forwards the
parent's actual import search path to the fresh worker without warming its
memory or changing the parent environment.

The earlier freshly resolved Android suite passed 19,093 tests, 46 skipped,
with four failures: `test_a_future_dated_generation_does_not_fence_forever`
(`not-a-generation`) and three route budget tests
(`test_absorb_budget_exhaustion_cancels_the_slow_phase_and_keeps_the_rest`,
`test_dossier_retry_gets_a_fresh_budget`, and
`test_a_voice_drift_overrun_on_the_fallback_is_filed_and_noted_against_it`).
Exact-main replay in the same environment failed two/passed two; current
replay failed one/passed three. The other two whole-run failures passed both
replay trees. This is clock/load evidence, not a green Py1 gate or proof that
all four independently reproduce on main.

The synthetic performance observations are source-pinned, not real-library
measurements. At `61a5c7c069`, seven actual fresh workers per path measured
799.168ms cached versus 1,104.696ms direct for 8.4M supplied characters,
with zero versus seven encoder count calls. Shared Windows host and OS
page-cache limits apply. Earlier CPU-clock, Windows portability and old
frontend counts were pre-rebase checkpoints and are not current gate claims.

No private library or attached phone was accessed. Real POSIX execution,
physical remount/FAT/ReFS hardware, on-device performance and power-loss
durability remain unverified. Failed durable-control I/O, unreadable ownership,
unproved hot journals and dormant devices retain their documented residuals.
Linux-platform mypy is simulated on Windows, not a real POSIX execution.
