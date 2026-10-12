# Roadmap 03 S6 Synthetic Library, Measured Consumers and Final Verification Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans, Native in this session. Human authorized all slices, frequent commits and repeated independent brutal review. No further plan handoff question.

**Goal:** Prove restart reuse with a safe synthetic generator, measured first consumers and output parity; complete whole-spec verification.

**Architecture:** Live enumeration and existing ctime-aware in-process memos remain the outer layer. Measured byte-fed extraction runs through compiled's complete keys inside misses. Caller-supplied token text has its own text hash and actual encoder identity; heuristic results are never persisted. Whole-page projections remain04's work.

**Tech Stack:** Existing Python/std-library/pytest, optional tiktoken, existing store/frontmatter readers and test LLM fakes. Requires reviewed S3/S4/S5 and this plan's mandatory Codex gate. [Original spec](../specs/2026-10-09-roadmap-03-content-addressed-compiled-cache-design.md) sections6,8,13,14 and03-S6.

## Global Constraints

- Generator writes placeholder names only, to a genuinely empty target; even a planted marker cannot authorize overwriting contents. No real-library reads, counts, timings or screenshots.
- Measure before wiring each kind. Every wired kind must win warm after restart, and same-process warm performance must not regress. Search extraction/tokenization are the likely wins; world/campaign row wiring is conditional on measured cost. If a small-row kind fails, record the result and retain its existing implementation, as section8 requires.
- Bytes supplied to compute decode exactly like read_text(encoding="utf-8"), including universal newline translation. Preserve each legacy unreadable/non-UTF8 fallback. Path stem/directory-derived names are declared params.
- statcache stays a leaf. Replace relevant outer memos with memo_stamped; its insertion window must check both mtime and ctime against computation start, so two rewrites in one coarse ctime tick cannot reuse old content.
- Preserve decision formulas/read/write semantics, live membership/counts, existing comments, privacy and frozen fixtures. SQL does not run under a campaign lock; cache opt-out under such a lock leaves live/in-process computation available.
- Token persistence keys exact stripped supplied text hash plus encoding identity. A path's stamp cannot vouch for a supplied body. Only a count actually made by ENCODING may be stored; offline load/encode failures remain uncached. The record_tokens wrapper clears ambiguous last-counter state before its own count attribution, including empty text; count_tokens itself keeps its public behavior.

## Interfaces and File Ownership

```python
# backend/scripts/synth_library.py, runnable from backend/
@dataclass(frozen=True)
class Shape:
    worlds: int = 2
    campaigns_per_world: int = 2
    scenes_per_campaign: int = 8
    records_per_world: int = 16
    body_words: int = 2000

generate(target: Path, shape: Shape) -> dict[str, object]
# CLI positional target, flags --worlds --campaigns --scenes --records --body-words.
# Marker .synth-library.json; negative sizes rejected before writing.

# backend/scripts/benchmark_compiled.py
measure(target: Path, *, repeats: int = 5) -> dict[str, object]
# CLI requires generator marker and writes JSON results only on explicit --output.

# backend/src/grimoire/template_paths.py (acyclic leaf)
DEFAULT_TEMPLATES_DIR: Path
templates_dir() -> Path

# store/locks.py
campaign_locks_held() -> bool
```

Generator uses existing atomic publication and frontmatter serialization, deterministic Realm/Saltmarch/Seraphine/Mara/Winifred names with numeric suffixes, existing world/campaign/scene shapes. Validate empty target before creating anything; mark only a successful complete build, never treat a marker as permission. Store APIs, if used, run under a temporary GRIMOIRE_HOME restored in finally; never publish a bootstrap pointer. Include character cards and CRLF fixture variants where measurements/parity need them.04 may extend Shape and flags without forking this generator.

The public filesystem_clock seam already exists. Benchmark callers advance that clock past source mtime/ctime plus five seconds, preserving devices and clock provenance; advance statcache's test clock where required, without changing perf_counter. Measure cold, warm same-process, warm after close/reopen and clearing owned in-process pools, and one-file-edited loads on world shelf/campaign shelf/Todo/search/campaign hub. Compare per-kind parse/tokenization cost to stat plus indexed lookup plus decode. Report raw repeat samples/median, call/read counts, dependency versions and shape; no fragile performance assertion in ordinary tests. Use a marked temp copy for mutation cases.

Search refactors _markdown_doc/_card_doc into a byte-text compute plus the existing path wrappers for live fallback. The persistent output is native JSON list converted back to its legacy tuple at the boundary. Params include stem and reader identity, not whole output paths. _doc retains its bounded _POOL and _LATEST behavior, now with full stamps; live corpus walk still supplies every path. Unavailable bytes fall back through the original reader without storing a value under an unvouched key.

Tokens use a no-file-input kind with text_hash and encoding params; derive receives no path to reopen. Before lookup select the current encoder identity, then trust only persisted encoder-counted rows; a miss calls the existing counter once and checks last_counter from that call. Heuristic/failed/empty attribution returns via S4's explicit no-persist result. An optional history row for supplied path uses built_from="" (unknown), because stripped caller text is not the file's raw bytes; never claim it proves file currency. Outer memo keys body text and actual encoder state as well as path, stamps and any retry epoch, and never lets a heuristic survive encoder recovery. Native count_tokens public behavior and thread-local attribution remain compatible.

Template resolver moves to a leaf shared by prompts and compiled, breaking compiled->prompts->schemas->tokens->compiled before tokens imports compiled. Preserve prompts.DEFAULT_TEMPLATES_DIR and prompts.templates_dir public names/env override behavior; no extra dependency. Compiled's availability checks campaign_locks_held; derive still computes without SQL when a caller is inside a campaign mutation. Track campaign-domain acquisitions/releases in the existing lock class with thread-local nesting counts, including reentrant/no-wait/hold_all paths; reset after fork. No unrelated lock domain is counted.

Measured shelf row consumers preserve native row keys and fallback names, pass dirname params and feed exact decoded bytes to existing parse_frontmatter/_first_paragraph. Public listings continue live membership/counts and fresh copies. Their in-process memo uses path/dirname plus full stamp and the same pre-compute window rule.

## Review Focus

1. Equal file bytes at two stems and CRLF/nonUTF8 inputs must retain legacy outputs (Task3).
2. A supplied body differs from an unchanged file or the encoder recovers after heuristic counting (Task4).
3. Two same-size rewrites restore mtime within one ctime tick; start-time gating prevents an outer-memo stale answer (Task1).
4. A planted synthetic marker in a nonempty directory must not authorize writes (Task2).
5. A campaign-locked caller and the new token import graph must remain SQL-free and acyclic (Tasks1/4).
6. A tiny shelf parse costs less than the lookup; the measurement must prevent unjustified wiring (Tasks2/5).

## Task 1: Ctime-Aware Memo Safety and Acyclic Support

**Files:** statcache.py; locks.py; template_paths.py; prompts.py; compiled.py; test_statcache.py; test_import_guard.py; existing environment/lock tests.

- [x] Write RED tests for fresh ctime with old/restored mtime, two coarse-tick rewrites, slow compute aging during read, and unchanged aged stamps. Existing four-field decision memos remain unchanged.
- [x] Write RED campaign nesting/no-wait/hold_all tracking and compiled SQL opt-out tests, plus template resolver env/default parity/import cycle. Inspect the complete import graph before moving code.
- [x] Implement both-timestamp insertion gate, shared template leaf and thread-local campaign hold tracking; add cache availability opt-out without blocking live reads. Preserve public aliases/default behavior and comments.
- [x] Run statcache/import/env/lock/core/lifecycle tests, Linux mypy and ruff; commit.

## Task 2: Safe Generator and Baseline Cost Experiment

**Files:** scripts/synth_library.py; scripts/benchmark_compiled.py; test_synth_library.py; a synthetic-only measurement report under docs/benchmarks/.

- [x] Write RED CLI/function tests for deterministic valid world/campaign/scene/card shapes, parameterized sizes, refused nonempty/unmarked/marked targets, no pointer writes, restored environment on failure and negative values. No inline LLM fakes.
- [x] Implement generator with explicit atomic writes and successful-build marker. Smoke-read the generated library through current public shelf/search/Todo/hub readers in an isolated test app using existing fakes.
- [x] Implement the four-case measurement driver and per-kind inequality probe against current readers; assert benchmark validates its marker and works only on its requested root. Record synthetic setup and measurement limitations.
- [x] Run generator/script/import/privacy guards and actual baseline experiments before selecting consumer kinds; commit generator and findings separately from consumer code.

## Task 3: Measured Per-File Search Extraction — Conditional Decision

**Ruling:** Original spec §§8/14 make the cost inequality a precondition.
Search/card extraction lost that comparison, so persistence was withheld.

- [x] Measure extraction against validation/cache costs before selecting kinds; record the loss in the synthetic report.
- [x] Retain the original search signature memo after the added wrapper regressed actual warm-page calls. Record its preexisting restored-mtime residual.
- [x] Preserve live extraction and output behavior; no restart-skips-byte-reads claim is made for this unselected consumer. The substrate/source/composite and output tests remain delivered.

## Task 4: Encoder-Only Supplied-Text Token Measures

**Files:** tokens.py; test_compiled_consumers.py; existing token/counting tests.

- [x] Write RED same-path different supplied bodies and same-body different paths; encoder warm/restart reuse; offline load, encode exception and later recovery; ambiguous empty count attribution; no persisted heuristic rows. Preserve existing count values and last_counter contracts.
- [x] Register text-hash/encoding kind and use explicit no-persist derive results. Do not reopen the supplied path for token keying. Outer memo covers supplied text and actual counter state; path-history built_from stays unknown unless raw bytes are actually known.
- [x] Test all token calls under a held campaign lock perform no SQLite operation, plus old body paired with new/restored file stamps and cross-thread attribution. Existing token fixtures/fakes are reused.
- [x] Run tokens/context/counting/source/derive/import tests in both dependency sets and ruff; commit.

## Task 5: Conditionally Measured Shelf Rows and Four-Case Report

**Files:** worlds/read.py; campaigns/read.py only if cost probe passes; test_compiled_consumers.py; benchmark driver/report.

- [x] Record a ruling per kind: wire only a demonstrated warm-after-restart win with same-process parity; retain the existing small-file implementation if it fails the inequality. No whole-page04 projections are introduced.
- [x] No shelf kind passed the cost gate, so conditional SQL wiring was not selected. Existing parse behavior, complete fallback-name memo params and output parity remain verified; no whole-page projection is introduced.
- [x] Run the four cases on every named page, report synthetic-only samples/counts/medians and each wired kind's cost inequality. Same-process regressions or missing restart wins require a fix or removing that wiring before completion.
- [x] Run shelf/search/Todo/hub/frozen-campaign copy sweeps with cache cold/warm/off/restart. Require snapshot bytes equal and fixture originals untouched. Commit measured behavior and report.

## Task 6: Whole-Spec Gates and Review Loop

**Files:** all changed files; ROADMAP-CHECKLIST.md; slice plans and theory note.

- [ ] Run fresh-tip make check with explicit worktree Python, full native/Py1 backend suites, frontend tests/typecheck/ratcheted ESLint, template suite, Linux-platform mypy counters and configured ruff. Compare Windows failures to independently replayed baseline, retaining accurate partial-gate evidence.
- [ ] Build APK at final source tip and verify packaged BUILD matches package/templates and sqlite3/_sqlite3/nativeSQLite components. No phone install or real-data browser exercise is required by user authorization; state real-POSIX/on-device limits.
- [ ] Run independent brutal-review/fix/re-review until non-blocking against all03 slices. Resolve mandatory native Codex whole-diff review and final original-spec fidelity gate; no skipped requirements disguised as completed checkboxes.
- [ ] Mark only delivered contracts/slices complete (landed stays open), commit final evidence/theory notes, ensure clean worktree and report branch/commits/tests/remaining platform limits. Do not push, merge or create a PR without an instruction.

## Self-review

All six review focuses have task-owned regressions or measurement gates. Synthetic-only observations satisfy the privacy rule. Conditional small-row wiring follows the spec's explicit cost test. Actual outputs and at-rest delete guarantees have separate evidence; a green focused suite is not a claimed green full Windows gate.


Task1 support checkpoint: eight observed RED cases now pass: both-timestamp insertion against computation start, nested/no-wait/hold_all campaign ownership with thread isolation, SQL-free live derive under campaign holds, and a shared template resolver leaf retaining prompts public aliases/environment behavior. Support/statcache/import/environment/lock/cache/lifecycle183 passed5 platform skips. Focused Linux mypy has only the two preexisting locks.RLock._is_owned attr-defined findings; no new source errors. New import formatting corrected; whole Ruff1146 unchanged. Ruling: this additive support task may execute while mandatory S5 corrections remain open; no slice completion or consumer wiring is claimed before its gates and measurements.

Task2 generator checkpoint:12 passing tests prove deterministic bytes, explicit shapes, empty-target refusal including planted markers, negative-size refusal, no pointer consultation, unsuccessful-build marker absence, CLI, and public API shelf/Todo/hub/shell plus two parsed scene posts. Existing client LLM fake reused. Generator alone delivered; benchmark/cost experiment and consumer selection remain pending.


Historical Task2/3/4/5 checkpoint (superseded by fresh-process admission and search rollback below): synthetic generator and four-case benchmark delivered with original-byte/environment refusal tests and existing-fake API smoke. The cost experiment was run before kind selection. Ruling: original section8's required inequality takes precedence over03-S6's tentative consumer list; search/card/shelf persistence is not wired because it is slower than extraction. Those readers retain live behavior with full-stamp outer memo safety and complete fallback-name keys. Only encoder-counted supplied text of at least262,144 characters enters persistence; shorter counts retain the safe outer memo. Heuristic/failed/unattributed/empty counts never persist; no supplied path bytes are reopened for keying. Native focused consumer/API/frozen163 passed; Py1 counterpart157 passed6 skips. Frozen-copy cold/warm/off/restart equality and untouched original fixture checked separately. Task6 whole-tip gates and independent/mandatory final review remain open.


Final review/cost correction (2026-10-12): the original restart driver only
closed pools/connections and left BUILD/dependencies/device state warm. Cold
and restart now use fresh Python processes; cold copies have no cache DB,
process wall times and worker IDs are recorded, and initialization is inside
the measured public call. Medium bodies fail the full restart inequality,
so persistent token admission rises to8,388,608 characters. A separate
525,000-word synthetic shape produces8,400,025 supplied characters: seven
alternating fresh-process pairs report1,764.881ms direct versus1,195.560ms
record_tokens and zero encoding calls in all seven restart-hit workers.
Search/card/shelf candidates still fail and remain unwired. A2-entry exact
string digest memo, at most16MiB per string, avoids rehashing repeated large
supplied text. Token memory counts use complete text/encoder identity; this
zero-input measure does not claim file currency or stat the supplied path.
File-derived outer memos retain full metadata/racy-window validation. The
originating spec states that distinction explicitly; final gates remain open.

Windows correction: source trust rechecks local NTFS/ReFS support every time;
serial numbers and mount paths are insufficient cached proof after cloning
or reformatting. Lifetime-limited memory memos use coherent native metadata
without that persistence allowlist. Both native APIs stamp the later of
ChangeTime/CreationTime, because CopyFile preserves older ChangeTime while
assigning a new creation time. Normal, copied, missing-API, restored-mtime,
unsupported-volume and remount cases have independent regression evidence.
The104-byte native result is decoded in one buffer operation for warm cost.

Final selection ruling: original ??8/14 mandatory inequality governs all tentative candidates. Search/card/shelf persistence is withheld. Actual search-page warm comparisons rejected the added ctime wrapper, so `_doc`/`_LATEST` retain their original signature memo and documented restored-mtime residual. Full-stamp source validation/shelf memos remain. The measured cutoff is8,388,608 supplied characters; only exact encoder counts persist. Zero-input supplied-text identity replaces path currency in both token memos. Earlier262,144 and pool-clear restart claims above are historical and superseded; the committed benchmark report records actual fresh-process samples and zero cached encodings. Mandatory final original-spec fidelity review must explicitly judge these rulings.

Final warm-caller correction: actual entities.list_entities reparses new strings and three-record sweeps thrash the two-entry digest cache. 23228d8dc memory slots compare exact supplied body+retained encoder before hashing, bounded by4096 entries/32MiB text; Path selects a slot without currency proof. Fifteen alternating actual-list pairs now have equal outputs and paired ratios0.98382/0.98961 for320k/4.2M records. The prior single-object benchmark is retained as a historical checkpoint, not the final whole-consumer proof. 727323252 resets the inherited memo mutex/pool in fork children and clears partial memory admission after allocation failure.47 counting/consumer/import tests pass; Ruff1146 exact baseline.

Mandatory final correction: native01a12f4a-601a-7f41-83f8-1e8c7d4a95e6 and original-fidelity01a12f4a-e4a9-7010-bef6-0b43d8e65a3a found pre-SHA vector fencing, unproved aside/main erasure, zero-input key fencing and synchronization-exception session reuse. Six local cases observedRED thenGREEN. Batch.begin_compute records the durable boundary without SQLite; vector source work and all derive calls use it before hashing/key construction. Session setup exceptions yieldNone, and ordinary recovery/aside cleanup require current per-file ownership. Native338 passed1skip plus333 passed1skip; actualowned corrupt-table lookup still recovers. c3ac retention fixes the independently reproduced retired-device aside. User requested squashing122commits withoutbackup:01344f952 preserves exacttree79c973b7ee0d21dbe35d7ef5ccb2a1a64bc0eb67. Remaining mandatory followups and latest APK rebuild precede completion.

Final implementation record at c45/34083: Tasks1–5 are implemented or explicitly resolved by the original measured-cost condition above. Native per-task and independent RED/GREEN checkpoints remain in the historical entries; actual fresh generator task-done passes15 tests. Final affected Android matrix passes1086 tests/12 skips. Task6 is still open for the exact current full gates and mandatory final verdicts. No whole-Py1 success or review approval is inferred.
