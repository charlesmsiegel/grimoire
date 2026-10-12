# Unified image surfaces: validation

Implementation tasks 1-13 are committed. Task 14 remains in progress: the full
repository gate is being handed to CI at the user's request. The final
adversarial review remains required; the native review findings are fixed. Interactive
browser and Android WebView acceptance remains outstanding. A separate
Android verification package was built and installed with its own application
ID (app.grimoire.artverify.oct12v1). The user has deferred further device
interaction; that test package remains installed. No UI capture, seeding or
Android acceptance was performed, and no further device actions will run
while that restriction is in place.

Worktree: .worktrees/unified-image-surfaces, branch
feat/unified-image-surfaces-spec, based on origin/main at f816921c8.
The latest corrections are pending-context ownership 1c242f5c4,
exhausted-collection rendering and detached-root recovery 54529e1dc.
Earlier required promotion recovery is 73a5a954d. Earlier
corrections are viewer 1b70cc5f9 and recovered-slot reuse 4ca4db049.
Earlier frontend corrections are e27825984 and 9c4e6e232, following
f31c6faad and b64fcd9c5. The bounded Windows record-open correction is
34ede3ff1. Supplemental Art reads and forced-collision coverage remain
652de4816 and 06420a9ea. Earlier Windows compatibility and portability corrections are
also committed.

## Current observed results

- **202 affected frontend tests pass** after the fifteenth review, with
  TypeScript and scoped ESLint passing. Five corrected historical regressions
  reproduce abandoned upload queues and world/campaign PC save-refresh state
  loss (including real committed partial-cleanup errors). Both findings are
  corrected; final adversarial review remains pending. Initial test fixture
  selectors/API response shapes were corrected before the historical replay.
- The ninth complete local gate was intentionally stopped for the requested
  draft-PR/CI handoff. Ruff1136, mypy161 and templates443 passed before the
  stop; no completed coverage or Pydantic1 pass is claimed for that run.
  CI runs the full frontend, both backend Python versions, Pydantic1, lint,
  templates and current APK gates. The local APK15 result below precedes
  these final frontend corrections; the current build is delegated to CI.
- **Current standard APK15 passes** on 1c242f5c4/0a5192b8e: native make
  exit0, Gradle BUILD SUCCESSFUL in 2m32 (48 tasks, 10 executed). **Real
  HTTPv9 passes** from an empty isolated store: 14 media reads plus cross-kind
  reuse, shared-caption refresh, stale409, campaign append and legacy reuse.
  Its uniquely owned server tree was stopped and port8199 released.
- Fourteenth native review found three further P2 issues. Detached-record
  isolation is corrected: all 28 seven-kind/four-operation regressions fail
  against the prior source with Busy409, and all 216 owning recovery, route
  and reuse tests pass with effective-root recovery. Ruff (1136) and mypy
  (161) remain unchanged. Pending-navigation and exhausted-collection fixes
  pass all 171 affected frontend tests across seven files; TypeScript and
  scoped ESLint pass. Five controller navigation cases, three companion guard
  cases and one collection case reproduced the findings before the fixes.
  This review is not approval; the fresh review and complete gate are pending.
- **122 affected frontend tests in five files pass** after the twelfth review,
  including delayed-original and retry zoom/pan preservation. TypeScript and
  scoped ESLint pass. Short-screen CSS gives the modal its own scrolling and
  retains an 80px image-stage minimum; actual visual reachability is unverified.
- **701 owning backend tests pass**, with one existing skip on 73a5a954d:
  34 recovery-ordering cases and 667 surrounding Art/sidecar/lock cases.
  Valid pending promotion journals settle before vacancy, source preparation
  and captured byte/primary-token checks, or return retryable Busy409. Default
  reads retain best-effort recovery. Controlled lock-release probes reproduce
  append and delete data loss on the old code. Corrected historical helpers
  produce 20 RED failures and 14 positive controls; all 34 pass on the fix.
  The first GREEN run used the wrong exception-message assertion; the existing
  sanitized Busy response is now asserted, and historical RED was replayed.
  Ruff (1136) and mypy (161) remain unchanged.
- The preceding **667-case owning run** passed with one existing skip: the full reuse
  module has 112 passes and ten other Art/sidecar/lock modules have 555 passes.
  Eight corrected regression cases fail against the isolated historical
  publication function and pass on the correction, covering both source
  surfaces, both reuse intents and both recovery scan points. The first test
  draft had four primary-token fixture errors; its displayed-snapshot token
  was corrected before the historical RED and final GREEN runs. Ruff (1136)
  and mypy (161) remain at baseline.
- The preceding **119-case frontend run** covered the eleventh review,
  covering fallback focus without changing the page scroller, campaign-owned
  override removal consequences and initial legacy write-target labels. TypeScript
  and scoped ESLint pass. The preceding **134-case run** covered the tenth review,
  including recovered promotion, replacement caption rebase and independently
  owned gallery/tagging failures and loading. TypeScript and scoped ESLint pass;
  all six backend continuity wording guards pass on the corrected source.
  The preceding 92-case run covered scope-aware conflict labels.
  The preceding **96-case run** covered all five eighth
  review findings: retained explicit-empty caption intent, recoverable crop
  reload, actor-version reuse labels, superseded refresh reporting and active
  sequential upload progress. The picker six-case rerun, TypeScript and scoped
  ESLint pass. Preceding 388- and 595-case runs cover surrounding navigation,
  seven-kind panels, mobile entries, libraries, Markdown and Greetings.
- **476 backend tests pass** across Art reads, writes, reuse, preconditions,
  caption guards, image locks and domain/order guards. Disappearing originals
  preserve effective and surviving shelves for all seven kinds. Four forced
  collision cases establish that nested stranded-promotion recovery is already
  nonblocking for both scopes and both guarded promotion/primary-reuse paths.
- **108 record-read and owning backend tests pass**, with four existing skips.
  Header, strict-record and config readers recover from brief Windows EACCES
  opens; persistent denials remain bounded, missing/POSIX errors are immediate,
  and errors after opening never repeat. Atomic API and import guards pass.
  Ruff (1136) and mypy (161) remain at baseline. A real isolated NTFS probe
  reproduced 226 read denials during replacement before the correction; the
  corrected probe completes 76,491 reads with no read denial. This does not
  claim to fix writer denials or other filesystem operations.
- **63 backend compatibility tests pass**, including raw LF/CRLF frontmatter,
  preserved body newlines, archive retirement, overlong Windows names and
  ordinary invalid-name errors. Another **215 portability tests pass with three
  skips**, plus five absorb cancellation/retry/concurrency cases. The latter
  reproduced a too-short preparation ceiling serially; its revised fixture
  preserves a ten-to-one slow-phase/ceiling separation and every outcome
  assertion. The Ruff ratchet passes at 1136. The preceding 351-case
  run covered Art publication races, name locks and campaign lock ordering.
- The latest complete-gate frontend phase at 08ebf1074 passes **4,914 tests
  in 228 files**, with coverage 88.85% statements, 84.14% branches, 84.22%
  functions and 91.47% lines. TypeScript, Ruff (1136), mypy (161), templates
  (443) and ESLint (706) pass. Modern backend coverage and Pydantic 1 remain
  incomplete: the verified owned gate tree was stopped at about 29% backend
  coverage before the confirmed thirteenth-review recovery-order correction.
  Interrupted workers emitted termination/failure markers; no completed
  backend result is inferred from them; this is not a
  passing complete aggregate.
- The preceding aggregate at d9608dd8e passed **4,895 frontend tests in 227
  files**. Coverage is 88.81% statements, 84.11% branches, 84.18% functions
  and 91.44% lines. Ruff (1136), mypy (161), templates (443), ESLint (706)
  and TypeScript pass. Its backend phase was stopped at about 45% for the
  ninth review corrections, after two unidentified failure markers. No complete
  backend or Pydantic 1 result is claimed for that interrupted run.
- The preceding aggregate's backend run **failed**: 18,129 passed, 33 skipped and 15
  failed in 22m 6s. Combined coverage was **94.16%**, above the unchanged 93%
  floor. Pydantic 1 was not reached. Failures included Windows filename and
  line-ending assumptions, raw archive parsing, mixed timeout output types,
  four config PermissionErrors and an absorb timing assertion. The four
  permission cases passed serially; their aggregate cause remains unproven.
- The latest real isolated HTTP run starts from an empty scratch store and
  passes **14 seven-kind/two-scope media reads**, cross-kind reuse retaining
  the same image ID, shared-caption refresh, stale-caption 409 refusal,
  actual campaign append and inherited legacy reuse. Its verified owned server
  was proven to own port 8199 before testing and its four-process tree was
  stopped afterwards. This latest run uses current code at 73a5a954d with a
  newly created isolated-store-v8; it also includes the Art preparation correction.
- **Current-product make check-apk passes** at 73a5a954d: Gradle BUILD SUCCESSFUL
  in 1m 9s, 48 tasks (8 executed, 40 up-to-date), producing
  build/grimoire-debug.apk. Explicit native-exit capture confirms make exit 0.
  The bounded Windows record-open correction, viewer fixes and required
  promotion-recovery barrier are packaged.
  No interactive WebView pass is claimed.

Separate focused counts overlap and are not summed. The frozen campaign's
home/ fixture was not regenerated. Baselines were only reduced; no increased
lint count or weaker coverage floor was accepted.

## Complete gate and mandatory reviews

The required aggregate command is:

    make check PY=C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe WORKERS=4

The first completed backend run used the documented WORKERS=8 option. The
rerun uses the repository default of four workers to reduce contention after
its permission and wall-clock failures. This does not establish their cause.
Test selection, xdist isolation and the 93% backend coverage floor are unchanged.
The first complete Pydantic 1 suite passed 18,154 tests with 41 skips in
39m 29s at the preceding backend checkpoint. The full modern diagnostic stopped
on a Windows scene-record read denial after 17,914 passes and 29 skips in
40m 3s. All 15 follow-up cases pass in a parallel focused rerun. The reproduced
NTFS read class now has a bounded correction, but the complete modern coverage
gate and Pydantic 1 suite must run on the latest committed source. Earlier Windows setup failures were reproduced as an
oversized PYTEST_CURRENT_TEST environment value from 3,000-line diff parameter
IDs; short local IDs preserve all inputs and assertions, and all 34 cases pass.

The standard /codex:review invocation could not launch the missing Windows
sandbox setup helper and returned an unverified placeholder. The same plugin's
built-in native reviewer receives the **complete actual diff**, original spec
and repository rules inline in a read-only session. Its eleven completed rounds
found concrete defects; their findings were corrected with failing-then-passing
regressions. The ninth review found two P2 defects, corrected with RED/GREEN regressions,
and a P1 promotion-lock premise ruled out by the existing nonblocking repair
implementation plus four forced-collision cases. The tenth review received
those unchanged helper bodies and did not repeat the lock premise. Its three
frontend recovery findings are corrected with nine new observed RED/GREEN
cases, including related pending-companion and obsolete-gallery cases.
The eleventh review found fallback focus scrolling, campaign-owned override
removal disclosure and the initial legacy write-target label. Four observed
RED cases plus shared/inherited positive controls cover their corrections.
The twelfth review confirmed a P1 reuse overwrite after stranded-image recovery
and found original-load zoom reset and short-viewport control overflow. Eight
reuse RED cases cover both source surfaces, both intents and both recovery
scan points. Three viewer RED cases cover zoom/pan and original retry. The final corrected-source owning checks pass: frontend122 and backend667
with one existing skip, TypeScript and scoped lint. A fresh review is running;
no approval is inferred.
The thirteenth review found two further P1 recovery-ordering defects, confirmed
by isolated controlled-release probes. Append vacancy and mutation tokens now
require settled journals while the complete avatar/name holds remain acquired.
Source preparation and publication apply the same ordering. All 701 owning
backend cases pass, with one existing skip, and static ratchets are unchanged.
Current packaging and isolated HTTP validation pass. The fourteenth native
review and a complete aggregate remain required after the backend-only change.
Both final source-review verdicts and the complete aggregate on the latest
committed source remain required. Historical or diagnostic passes are not
substitutes for that gate.
Runtime exit 0 is not an approval verdict. The final /codex:adversarial-review
against diff and original spec remains pending until implementation and
automated gates are otherwise complete.

| Native round | Findings addressed | Final focused evidence |
|---|---|---|
| 1 | Publication deletion races; controller/editor ownership; ordered refresh; viewer identity, centering and recovery; fresh picker/default PC version; drawer navigation | Frontend 358; backend destination-deletion 18; TypeScript |
| 2 | Gallery-only reuse allocation; accepted draft retirement; A-B-A ownership; ordered entity reads; campaign Greeting scope; live related viewer; failed library refresh; focus/help/uncertain-write wording | Frontend 449 and client 160; backend 119; TypeScript; baseline 1136/161/706 |
| 3 | Reuse restoration destination checks; explicit-empty caption retention; departed picker results; failed campaign-library refresh; Markdown focus; phone crop; named viewer position | Frontend 268; backend 137; TypeScript; scoped lint |
| 4 | Restoration source visibility/token checks; departed entity callbacks; same-record Back viewer retirement; multi-file retry retention; partial-promotion recovery | Frontend 242; backend 158; TypeScript; scoped lint |
| 5 | Same-record Manage retirement; related viewer route ownership; visible phone heading focus; campaign preparation outside long exclusion; unavailable connected focus candidates | Frontend 308; backend 351; TypeScript; scoped lint |
| 6 | Initial shared caption conflict disclosure; same-context Markdown Back; global library caption guards and picker dismissal; retained zoom after viewport growth | Frontend 595; TypeScript; no new scoped lint findings |
| 7 | Stale caption reload/reconciliation snapshots; reuse picker route retirement; specified mobile entity entry; visible focus fallback after width change | Frontend 388; TypeScript; exact scoped baseline match |
| 8 | Explicit-empty caption intent; failed crop reload recovery; actor-version reuse labels; superseded refresh reporting; active upload progress | Frontend 96 and picker 6; TypeScript; scoped ESLint |
| 9 | Disappearing world-original reads; placement-local caption labels; promotion-lock premise ruled out by nonblocking helpers and forced collisions | Frontend 92; backend 476; TypeScript; Ruff ratchet and scoped ESLint |
| 10 | Recovered primary reuse; retired reload on replacement rebase; independent gallery/tagging errors and pending queue | Frontend 134; wording guards 6; TypeScript; scoped ESLint |
| 11 | Scroll-preserving fallback focus; campaign override removal warning; initial legacy write-target heading | Frontend 119; TypeScript; scoped ESLint |
| 12 | Reuse occupancy after recovery; explicit zoom/pan after original load; short-screen control reachability | Frontend 122; backend 667 with 1 existing skip; TypeScript; scoped lint and ratchets; visual reachability unverified |
| 13 | Settle or refuse valid pending journals before vacancy, source preparation and mutation tokens | Recovery 34; surrounding backend 667 with 1 existing skip; historical RED 20 and positive controls 14; Ruff/mypy unchanged |

The earlier fresh-context whole-branch reviewer also completed its review;
its findings and follow-up cases were corrected before the native rounds.
Source review does not establish browser layout or device interaction.

## Isolated media verification

The verify skill's launcher sets GRIMOIRE_HOME to a scratch directory **before
imports**, imports the worktree backend and uses the existing campaign-flow
OpenRouter cassette. Initial worlds [] confirms isolation. Synthetic assets
exercise a tall portrait (120 by 1200), wide map (1600 by 120), narrow item
(40 by 800), tiny image (16 by 16), animated GIF and animated WebP using the
existing placeholder records only.

HTTP checks compare original bytes, dimensions and animation frame counts,
verify download attachment headers and effective provenance, and check the
existing thumbnail contract. A static 160 request uses the upward 256-width
bucket; animated thumbnails retain the original animation. These checks do
not establish CSS fit, overflow, touch behavior or crop presentation.

## Acceptance evidence

These rows map the sixteen cases in the approved spec to automated evidence.
Browser and Android interaction remain distinct, outstanding verification.

| Spec case | Evidence | Remaining interactive evidence |
|---|---|---|
| 1. Seven kinds in both scopes | RecordArtPanel management matrix; record-art read/write matrices; isolated 14 HTTP reads | All seven pages in browser |
| 2. Actor version selection and bookmarks | CharacterPage, PCPage, typed paths, adapter/controller and gallery navigation tests | Browser version transitions |
| 3. Phone Art entry on empty/gallery-only records | EntityEditor, actor entry and panel regressions | Phone layout and overflow |
| 4. Image shape, motion, preview-only crop | Geometry/focus tests; synthetic HTTP originals, dimensions, frames and thumbnails | Visual fit and crop at both widths |
| 5. Fit/zoom/pan/steps, focus and layered close | ImageViewer pointer/keyboard tests, RecordDrawer tests, nested-dialog Back regression | Real pointer/touch/scroll behavior |
| 6. Partial batch and concurrent append | useRecordArt batch tests; record-art write and Windows ledger concurrency tests | Browser partial-upload presentation |
| 7. Scoped cross-kind/library reuse | Backend reuse matrix/source guards/no-extra-blob tests; picker tests; isolated cross-kind HTTP reuse | Browser picker workflow |
| 8. Primary swap and partial promotion retry | Backend swap/reuse tests; controller promotion-only retry regressions | Browser recovery workflow |
| 9. Placement removal and inherited visibility | Backend removal/tombstone guards; panel and controller tests | Browser inherited removal |
| 10. Shared/empty/local caption scope and cancellation | Captured scope disclosure, controller/editor and shared invalidation tests; isolated HTTP refresh | Browser editor flow |
| 11. Late reads/writes and replacement preconditions | Controller owner/generation tests; backend byte/version/caption guards | Browser changing context while pending |
| 12. Gallery search/filter/selection/Back scroll | Real memory data-router gallery return and gallery-state tests | Browser shell scroll at both widths |
| 13. Independent collection occurrences | Canonical member/failure tests; structural stream and independent comparison-document regressions | Streaming viewer in browser |
| 14. Honest read/original/refresh failures | Controller and viewer failures; failed-first-read reopen retry | Browser failure presentation |
| 15. Separate Greetings and copy bridge | GreetingMarkdown, gallery tagging/detail, character bridge and backend copy tests | Browser dedicated greeting workflow |
| 16. Cross-placement CAS and partial cleanup | Backend description-conflict/cleanup guards; real partial-response client/controller tests; isolated stale HTTP save | Browser reconciliation/cleanup retry |

## Outstanding interactive evidence

The Browser skill's supported Node runtime cannot start because
codex-windows-sandbox-setup.exe is missing. No screenshots, desktop/phone layout
checks, real touch/pinch/drag, actual shell scroll/focus restoration, or
interactive streaming run are claimed. The successful isolated API exercises
and DOM tests remain separate evidence.

The browser connection was rechecked after the latest successful APK build and
failed on the same missing helper.

Android bootstrap succeeds with the installed JDK 17, SDK and Python 3.12.
adb devices -l lists no device or emulator. APK packaging and interactive
WebView acceptance are separate: the latter remains outstanding even when
packaging succeeds. The seven-source system preserves the existing entity
rails; Greetings retain their dedicated rendering and art workflow.
