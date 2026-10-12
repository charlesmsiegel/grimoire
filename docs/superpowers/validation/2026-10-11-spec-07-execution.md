# Spec 07 execution record

User authorization (2026-10-11): implement every slice and use brutal-review on
an independent agent as a gate. Implementation is inline; each slice retains
its own plan and checks. Independent review findings must name a concrete
failing case and survive verification against callers. Record actual gates,
commits and test results here as work lands.

S1 plan: ../plans/2026-10-11-roadmap-07-s1-members-field.md.
S1 spec and plan actual Codex gates passed; human authorized execution.
S1 Task 1 started. S2-S8 remain to plan/implement/review.

Theory: The group owns one raw membership line; views filter inherited detached
actor identities and derive affiliations from it. Exact actor-slot consent
controls the transition to an owned copy. Later inverse, presence, graph and
retrieval readers share this rule rather than store another roster.
Reused: entity_schema refs/validation, overlay copy-on-write, atomic writers,
campaign locks, existing Pydantic serialization and review machinery.
Watch: first-copy identity changes, lifetime-bound reversals, consumer visibility,
and content freshness across local/external edits.


## S1 progress

- Task 1 complete: foundation `4f902ad03`, authoring references `4d7a2d02b`.
  212 targeted tests passed; lint ratchet and frontend typecheck passed.
- Task 2 in progress: strict ledger core `86b21ff71`, first associated-effect
  preflight `da4edb003`. Core 184 tests and first preflight/guard set 314 tests
  passed. The existing Starlette deprecation warning remains.
- Two existing tests assumed POSIX filesystem behavior. World-reference policy
  simulation is now explicit; illegal wildcard-file setup runs only where the
  native filesystem can represent it, retaining API rejection on every host.
- Remaining S1 work: complete all lifecycle/migration/sync preflight, prepared
  copies and context, API/journal/Undo, editor consent, full gate, isolated
  browser check and independent brutal-review plus mandatory Codex gates.

These are intermediate checks. No slice has passed its implementation or
independent review gate yet; S2-S8 remain pending.

Task 2 lifecycle check: 658 tests passed (197.34s), including overlay/migration,
sync, images and atomic/lock/order/path/import guards. Lint remains at its 1146
baseline. World deletion and demotion preflight all current dependents before
effects; demotion includes campaigns already hiding the record. The independent
foundation review at c97f78353 found one non-blocking invalid-repoint gap; both
refs are now validated before manifest/ledger effects, with four regression
cases. The review artifact records its exact scope and exclusions.

Task 2 complete: the final post-delete cleanup regression reproduced two
failures; cleanup now holds the campaign lock and strictly preflights before
removing campaign state. Migration's later tombstone selection is strict too.
The required Task 2 completion set passed: 566 tests, one existing warning,
180.18s. Lint/diff checks passed. Task 3 is next; S1 remains in progress.

Task 3 core: 56 regression cases first failed. Shared composition now prepares
final text before publication; only retained unresolved field/ref slots need
exact consent. Same-read Pydantic context and rev, dormant-field copy guards,
and Members/Leader publication validation are implemented. Library-move
compatibility exposed a greeting-kind validator mismatch, now corrected.
261 targeted tests passed; lint/diff checks passed. Remaining Task 3 checks
cover collateral sweep refusal, all-dependent frozen sources and concurrency.

Independent copy-core review at 938d2d4b5 was blocking: native aliases bypassed
literal detachment checks, and World reclassification could move before ledger
refusal. Both were reproduced by regression tests and corrected. Native
samefile proofs augment only queried raw detachment refs; raw spelling and the
pure filter remain intact. World moves preflight all held dependents before
any effect. Direct asset copies are strict/locked; collateral copy refusals
preserve the landed move, warn, and continue later sweep legs.

268 integration tests passed (87.68s), including native alias refusal, separate
copy-first/birth-first interleavings, frozen source/demotion, actual HTTP
collateral refusal, store reclassification and overlay/Pydantic/import guards.
Lint/diff passed. The independent recheck and full Task 3 completion set remain;
these fixes do not constitute the final S1 gate.

Task 3 complete at abdb7be03: required compatibility/concurrency suite passed
655 tests (191.42s), one existing warning. Independent recheck of abdb7be03
resolved both blocking copy-core findings, with 271 frozen-source tests and lint
passing; verdict is non-blocking for its stated scope. API, journal/lifetime,
UI and final S1 gates remain outstanding. Task 4 starts next.

Task 4 API checkpoint: 83 tests passed (22.96s), one existing warning; lint and
diff checks passed. Group detail serializes the Pydantic context, request
bindings are typed and validated, identity conflicts carry kind/message/slots
with the app's displayable detail string, and group revision checking and its
write/body-journal span share one lock. Concurrent saves yield one success and
one existing stale_record refusal. Members-specific journalling, Undo/Redo,
lifetime fences and failure stamps remain in progress.

Task 4 Members core: 133 tests passed (50.43s), one existing warning; lint/diff
passed. Whole-field raw/effective/unresolved JSON snapshots retain original
World refs/root and exact Members consent in their sealed owned scope. Undo
prunes originally unresolved refs, preserves legacy/Leader/body, rechecks
original World identity, and refuses an absent owned target without fallback.
Strict journal lifecycle fences and failure-path revision stamps remain.


Task 4 lifetime checkpoint `6a75b98ae`: strict whole-document journal reader,
permanent retirement and operation-specific relocation fences are wired through
owned deletion, dematerialization, take-world and migration pruning. JSON-native
Members Undo/Redo retains its original scope and consent. The initial lifecycle
suite passed 262 tests; campaign partial-effect/API/Undo checks passed 143.

Independent brutal review of that checkpoint was blocking: a replacement World
record moved an independent detached original; post-publication journal sealing
could release the campaign hold before failure stamping. Both findings were
reproduced with failing tests and corrected. Native samefile checks preserve
case aliases without folding distinct filesystem identities. World partial
suspension/move/completion failures now stamp every possibly changed held
campaign and propagate lifetime completion failures. Take-world also stamps
failures after unlink but before manifest completion. Pure strict preflight
refusals leave revisions unchanged. The World/migration/move suite passed 210
checks; latest journal/API/Undo/sync/import/Pydantic suite passed 182. Lint
remains at 1146; diff check passed. Independent recheck and Task 4 completion
suite remain pending; no final S1 gate is claimed.

Lifetime theory: an offer names an owned original in a host-derived campaign
scope, not matching bytes at a reusable slug. Retirement precedes disappearance;
a move suspends before publication and resumes only its own token at the actual
landed id. Generic display repoints cannot erase either fence. Revision stamps
record possible partial effects after the final failed attempt, within the same
hold; they do not promise a transaction across journal, record and revision.


Task 4 independent recheck at `09d1460bb` resolved both previous findings but
found a native case alias bypass in take-world: a detached original could be
unlinked through an uppercase submitted id. The defect was reproduced at the
store and by the reviewer through the typed route. A shared samefile-based
record marker check now protects detached and pinned records, retaining exact
marker semantics and distinct case-sensitive files. 130 lifecycle/sync/pin/move
checks passed. The broad completion run reached 714 passes before the overlay
AST guard identified the deliberately owned-only Undo lookup; its reasoned
read now goes through overlay.require_owned_entity: the resolver owns the
explicit no-fallback policy, preserving the guard without a new exemption.
An intermediate compatibility check also caught an unnecessary strict ledger
read on generic owned body edits; it was removed in `f4ec68039`, with 39 checks
passing. Strict lifecycle and Members journal owner preflights remain intact.


Task 4 complete at `5abe9c8de`: all 773 required compatibility and guard tests
passed (184.80s), one existing warning. Independent native recheck is
non-blocking within its recorded scope, with 185 frozen checks and actual typed
route reproductions. Both original journal findings and the later alias defect
are resolved; final whole-S1 certification still waits for the editor and full
gates. Task 5 begins. UI test preparation overlapped only the independent
backend test run, without product changes to its frozen source.

Browser setup note: the installed in-app browser runtime cannot start its Node
kernel because codex-windows-sandbox-setup.exe is absent. Isolated app/browser
verification remains required; no in-app-browser pass is claimed. The real
library and default ports remain untouched.


Task 5 UI checkpoint `a9b8915f5`: separate unchecked exact Members/Leader
bindings, foreign chip diagnostics, missing-context reload and safe update
completion passed 172 UI tests and strict TypeScript. Independent whole-S1
brutal review found two additional late reset paths: stale-banner reload and
new-group create could overwrite a newly selected scope. Root reproduced those
plus delete/reclassification variants (four failing cases), reused the existing
scope/read request counters for all reset/navigation completions, and guarded
late rail listings too. Latest UI suite passed 176 checks; TypeScript passed.
The independent frozen artifact retains its blocking verdict until recheck.

First full-gate attempt stopped at mypy: two new optional-value errors were
fixed explicitly. The five extra POSIX-attribute findings were in unchanged
atomic/proclock source and arose solely from Windows platform selection;
mypy.ini now pins the shared ratchet to Linux, matching CI, as its Python
version already is pinned. Baselines were not expanded. Mypy passed at 165;
120 lifetime/journal/docs checks passed. The full gate must run again.

Actual native Codex review thread `01a12da1-5a49-77e0-a3c7-4bb02bde0417`
could not read the branch because the Windows sandbox helper is absent. It
explicitly returned an unverified placeholder, not approval. The approved
public-snapshot runtime workaround will run the actual native reviewer again.

Independent whole-S1 recheck of `02131dd93` resolved the async editor reset
finding: 179 reviewer UI checks, strict TypeScript and 195 backend checks passed.
The scoped recheck is non-blocking; its artifact records its exact limits.

Native implementation review of `02131dd93` returned four concrete findings in
thread `01a12dad-498e-7f02-a005-8924d34b4dde`. All reproduced and were corrected,
with 355 targeted checks passing. The native review artifact retains its
findings and resolution evidence; actual recheck and final spec coverage gate
remain required.

The full gate reached 18,048 passing tests and 93.27% backend coverage, but
failed with 13 failures and 10 setup/teardown errors. The frontend passed all
4,613 tests and its coverage gate. Platform-specific fixture assumptions and
an existing Windows concurrent append defect are being diagnosed; no full
gate pass is claimed. The Pydantic 1 run has not yet executed.

Native fix guard suite passed 234 tests (64.43s). Independent review found one
missed owner of the captured-lock contract: World image deletion re-enumerated
a newly born dependent under older locks. Its deterministic regression failed
before the fix. World image deletion now passes its captured cids through the
cleanup sweep; all 244 lifecycle/image/route/guard checks passed (36.53s).

The existing local windows-atomic-append branch supplied inspected dependency
fixes required by the failing full gate: cross-process Windows append locking,
bounded record-open races, actual-volume sidecar limits, and portable fixtures.
Commits through 17118d9c9 were cherry-picked linearly; the unrelated final UI
settling commit was excluded. Duplicate fixtures were resolved to preserve S1
policy simulation and all test tails; an accidental conflict-script truncation
was immediately restored in 8e9f2f08e. Integrated lint passed at 1146 and the
smaller committed mypy baseline passed at 161. Targeted dependency checks and
a fresh full gate remain required; integration is not a verification claim.

Independent image recheck of 6eb1370f0 is non-blocking: 118 frozen checks
passed. Independent Windows dependency review of the same frozen source is
non-blocking: 528 checks passed, 9 skipped; native alias/contention/no-write
timeout/release and actual volume filename-limit probes passed. Artifacts
retain their exact scopes and do not certify the full branch.

Native recheck thread 01a12ddc-3971-7803-ae3c-afc0b2df0a82 confirmed the four
original findings resolved and found the new post-restoration readback fallback
regression. Both Undo/Redo cases failed before correction; 160 checks then
passed. The failure was introduced by the display correction and is now
recorded explicitly for final review. Automatic approval initially rejected
the recheck by classifying project source as private; anonymous GitHub access
confirmed origin is Public and only tracked code/synthetic fixtures were in
the payload. That evidence was accepted; no private store data was sent.

All failed Windows gate areas passed the root targeted dependency selection:
384 passed, 6 skipped (169.95s). A fresh make check started at 6eb1370f0.
Its source predates the narrow Undo readback fix; final validation must account
for this delta rather than claim that frozen run checked later code.

Isolated browser verification completed all S1 consent/conflict/Undo flows,
including the actual Incoming typed-field hint; no browser page errors.
The separate consent-controls screenshot was visually inspected at original
resolution. Helpers on 8199/5199 were stopped after verifying their exact
worktree launcher/Vite command lines; the real library/default ports were
untouched. Node sandbox was unavailable, so this used a disposable standalone
headless Chrome context and the verification skill's isolated store/mock API
with shared Cassette replies. Browser result and screenshots remain ignored.

Independent Undo-readback recheck of ea7a93f24 is non-blocking: 120 frozen
checks passed plus two HTTP probes of Undo/Redo verified truthful unavailable
display, non-undoable reversal row, changed revision, completed original, and
duplicate requests refused without another write. Artifact keeps its scope.

S2 plan prepared and self-reviewed while S1 final checks run:
../plans/2026-10-11-roadmap-07-s2-inverse-membership.md. No S2 implementation
has started. It covers pure Pydantic inverse/visibility, coupled group-byte
capture and strict root-keyed memo, typed read routes and shared read-only
actor-page Groups sections. Actual plan gate remains unchecked until run;
S1 native recheck, final spec gate and full verification remain outstanding.

Actual final S1 originating-spec gate at41da96e4d returned four grounded
findings in thread01a12def-f74b-7af2-84fa-d45ef5af1df4: stale pre-hold copy
flags, stale/recreated demotion target scope, native detached-group push and
native tombstoned-group read/copy bypasses. Deterministic regressions reproduced
both demotion cases and four read/copy variants. The push attack setup was
corrected to assert its explicit retained marker; the actual old frozen
_push_locked function then failed both forced and normal push cases. Fixes and
current owning/guard verification remain in progress; no final approval claimed.

Actual S2 plan gate at41da96e4d in thread01a12df3-7f75-7792-8848-cae8d04c5b5d
requires validating the wrapper ACTUALLY returned by memo_stamped: a different
reader can insert between the earlier pool check and the second lookup. The
plan now specifies bounded fresh uncached recovery and a two-reader publication
pause across stat-preserving Members/secrecy edits. Actual recheck is required
before S2 implementation.

Final-S1 findings fix checkpoint: one captured dependent discovery and sorted
hold now covers fresh World-binding, native marker and copy-existence checks.
Each selected dependent goes through the locked materializer; a recreated
campaign on another World is skipped before any preflight/copy/manifest effect.
Native detached groups refuse normal/forced push through aliases; inherited
read, preflight and materialization honor native tombstone aliases. Read
diagnostics anchor marker matching to their existing View roots.

All corrected owning/route/architectural checks passed: 471 tests (175.34s),
one existing warning. Lint/mypy passed at1146/161. Initial implementation
verification caught a missed helper signature edit; it was corrected before
this passing run. The valid-marker push regression also reproduced both
failures against the exact old frozen function rather than infer a result from
a malformed setup. Independent and actual originating-spec rechecks remain.

The earlier full desktop run at6eb1370f0 finished18107 passing,34 skipped,
93.53% coverage (2312.56s). The Pydantic1 run is in progress and loads later
source changes; a final frozen-source validation must account for the delta.
No current-source full-gate pass is claimed. An ignored Python3.11 runtime
was provisioned locally, without PATH/registry changes, to match Android CI
when needed; the running compatibility job currently uses the existing314
interpreter with the actual Pydantic1 pin.

Independent final-scope review at3947aaa56 reproduced push/promote publication
into a campaign's former World after recreation before lock acquisition. Both
public entry points now resolve the World under their campaign hold. The new
parameterized regression schedules actual delete/create before entering the
real publication lock; both cases failed before correction. Current group, sync,
route and architecture selection passed379 tests (127.25s). Independent recheck
and actual originating-spec recheck remain required.

Actual S2 plan recheck at3947aaa56 approved with no findings in installed
Codex thread01a12e1c-0150-71b0-8241-5e17915aed2b. The amended actual-return
validation and two-reader insertion test close the blocker. S2 Plan gate is
recorded; no S2 code starts before S1 final code gates close.

Independent scoped recheck of882cc05d2 is non-blocking:263 frozen regression
tests and2 extra lock-ownership/publication probes passed. Former-World
publication blocker is closed; actual final spec gate remains separate.

Task5: Ruling: use a target-specific Python3.11 override only for the
check-pydantic1 venv bootstrap, matching Android CI, while desktop gates
retain the shared worktree Python3.14. The same make check prerequisites,
asserted Pydantic1 pin and complete test suites run unchanged. This avoids
claiming a different interpreter is Android parity; no product/Makefile
change or test skip. Disposable target resolved within this worktree/build.

S3 plan prepared/self-reviewed while S1 frozen gate runs. Generic typed sync
rows, one campaign View/name map, raw-vs-shown comparison and existing display
components own the change. Actual plan gate pending; no S3 code started.

S4 plan drafted/self-reviewed while S1 code stays frozen. Presence reuses the
ordered engine and consumes the approved S2 effective roster interface. Actual
gate waits for checking those interfaces against completed S2 source. NoS4 code.

Actual S3 plan gate atc8892fe9c approved with no findings in thread
01a12e29-527e-7053-bdb0-0be0a6b11f90. Existing global GIC409 handler verified
in main.py after the reviewer requested confirming unsupplied route wiring.
No S3 implementation yet. Earlier mixed-source full run finished Pydantic1
onPython314:18111 passed,40 skipped (1964.60s), enormous deprecation-warning
volume. This is earlier-run evidence, not the final frozen gate now active.

Actual final recheck at882cc05d2 accepted the prior four fixes and found
native-marker relocation/reattachment, image pre-hold roots and two stale
dependent scope boundaries. Independent agents reproduced the failures.
Held-scope root fix: image publication roots resolve under campaign hold;
shared bound_dependents removes deleted/rebound captured campaigns before
strict preflight, relocation fencing or cleanup. Three valid root regressions
failed before correction;381 lifecycle/group/lock/import checks passed after
(109.25s), lint/mypy1146/161. Extra corrupt-other-scope controls will run.
Native lifecycle correction remains in progress; no final approval claimed.

The current full gate was stopped at backend2% after these new actual findings
made its source obsolete. Only the verified owned task-done process tree was
terminated. Frontend/type/lint/template phases had finished; this interrupted
run is not a complete gate. A final frozen gate follows resolved code gates.

Independent held-scope recheck of73fb54a19 is non-blocking:7 extra probes
passed (three races, two corrupt-other-World variants, two removed-campaign
controls) and123 frozen controls passed. The source filter precedes preflight.

Native lifecycle checkpoint: capture_record_refs freezes native-proven marker
and base keys before movement/removal; canonical source spelling comes from
the real resolver. Repoint carries captured whole-record/assets/base aliases
to the landed record; reattachment clears them and rollback restores the exact
original detachment set. Actor/greeting delete now captures before disappearance;
entity delete publishes the canonical tombstone and drops captured bases.
Four original native attack variants failed before correction; adjacent actor
delete also failed before correction. Current targeted suite7 passed,1 skipped
(actual case-sensitive independent-file control unavailable on this volume).
Broad owning run at the initial capture correction passed750,1 skipped in
176.62s; it predates the final actor/greeting deletion/helper simplification.
Latest lint/mypy1146/161 passed. The current full owning run and independent
plus actual final recheck remain required, not inferred from the earlier run.

Final owning source0a4c59535 passed751 tests,1 case-sensitive control skipped
(191.56s). Independent physical mixed-policy review nevertheless reproduced
cross-owner marker capture: distinct campaign files alias only in the World.
Root added a real fsutil fixture policy before files exist; the valid mixed
attack failed, and the corrected independent-file control passed. Earlier
post-file policy requests skipped rather than pretending to test the policy.

Actual persisted final recheck accepted previous fixes and found unsafe-ID
canonicalization plus aliased actor bookkeeping. Root additionally reproduced
an inherited World alias reselecting a different owned file (delete/reclassify).
Seven source/unsafe/promotion cases failed before correction. Owned namespace
now owns native proofs; inherited operations retain their submitted target ID;
delete validates kind/id before probes; promotion resolves the owned canonical
actor under the hold. All32 native/source cases passed, including physical
mixed policies; lint/mypy1146/161. One alias-promotion case initially fails at
ActorNameError before bookkeeping, so that result is not claimed as a direct
unpicked-dematerialization reproduction. Existing alias appearance-key variants
will be checked before this finding is closed. Independent/actual recheck and
complete final gate remain outstanding.

Native namespace correction: plain Pydantic RecordIdentityRefs freezes owned
keys for detachment/sync bases separately from World keys for tombstones/assets.
Both mixed case-policy directions now preserve distinct originals. Current native
selection passed34 tests (142 deselected,117.09s); lint/mypy1146/161 passed.
Independent review reproduced the inverse failure against11d0ca049 before this fix.

Task5: Ruling: a saved actor appearance lock under a native alias other than the
owned canonical ID blocks promotion before publication. Automatically migrating
that key could change historical role references or select a distinct World actor;
ordinary canonical locks retain promotion behavior. Cost: an existing alias lock
must be resolved before that actor can be promoted. The concrete two-Incoming-row
failure is pinned; refusal preserves both stores byte-for-byte.

Current owning run:783 passed,1 failed (existing World-only actor promotion
conflict became CharacterNotFound). Canonicalization now preserves the existing
conflict-first order when no owned path exists. The existing regression supplied
RED; native alias and publication controls are rerun with the correction.
Inverse Gone test also reproduced RED against frozen11d source using explicit
pytest pythonpath override (the first environment-only attempt loaded current
source through pytest configuration and is not RED evidence).

Promotion conflict correction GREEN:242 group/library tests, lint1146/mypy161.
Independent namespace recheck at11e96e0c8 is NON-BLOCKING:308 owner tests,
no skips/exclusions, both mixed policies, exact rollback and both saved-lock
branches independently checked. Data-loss findings are closed.
Final: minor (deferred): an image deletion authored with an uppercase native
alias leaves its asset tombstone under that alias after World deletion and an
owned move; a successor World group can display its map canonically but hide it
through the old alias. Reviewer classified SHOULD FIX/non-blocking; preserve the
separate namespace proofs and address at the asset writer or pre-deletion capture.

S7 plan drafted and self-reviewed against12.1-12.5/17.7 and approved S2 pure
interfaces. All projection/visibility requirements map to Task1, exact DTO/types
agree with S2, candidate-cost and model ownership tests cover hidden seams.
No S7 code; native plan gate remains pending. Explicit nonpositive limit yields
empty, consistent with an explicit cap; defaults remain uncapped metadata.

S6 plan drafted/self-reviewed against10/17.5 and current graph phaseC, assembly,
typed unions, model toggles, layout and detail. Existing-actor closure, fail-soft
omission, one read, marked GM groups and both layout modes map to explicit tests.
Approved S2 interfaces consumed exactly; no S6 code and native plan gate pending.

S7 actual installed plan gate APPROVED at1246e34b7 in thread
01a12e84-330a-7343-8f6f-b8a20b371477; no findings. No S7 code.
S6 native plan gate found strict-ledger softening and actor-family ordering gaps;
plan amended: GIC propagates before delivery, ordinary source errors omit groups;
groups run after events before ideas; tests cover plot/birthday-only affiliates
and cold/warm strict errors. Self-review confirms exact producer APIs unchanged;
actual recheck remains pending.

Latest actual S1 final gate found cross-namespace owned selection and actor
publication failures. Both independently reproduced at1246e34b7. Root regressions
RED9, GREEN9 across both physical policy directions, Members/Leader, normal/force
push and World move with a live Members offer. Shared proof compares canonical
IDs within each root, never copy inode versus ancestor inode; both directions
must preserve existing distinct originals. Static checks1146/161 passed.
Task5: Ruling: an ambiguous following owned original blocks a World move before
any suspension or movement. This preserves both records and live journal offers;
blindly excluding it could strand World-marker/display refs. Cost: a conflicting
native alias must be resolved (for example by renaming the owned original) before
that World operation can proceed. No ambiguous record is silently seized.

Cross-namespace owning verification GREEN793 tests,1 existing warning in233.77s;
includes group/lifecycle/overlay/reclass/library/sync/pins/routes and architectural
checks. Lint1146/mypy161 pass. Shared follower preflight extracted to stay within
existing complexity ratchet; behavior unchanged. Root source correction checkpoint
follows; actual final recheck and independent gate still required, no full pass.
Theory note records record authority, owning namespaces, pre-effect lifetime
fences, native transfer proof and explicit refusal/coordination limits.

S6 exact corrected plan gate APPROVED at2355a68dd in persisted native thread
01a12e84-3085-7e53-83e7-f51f9aefe708. Rebuilt packet contains exact current plan
and its actual corrected diff. No findings, no S6 code/full-gate claim.
World deletion/demotion namespace regression RED2 (move control passed), then
583 owning tests GREEN231.71s; lint1146/mypy161. Additional flat record sync
transfer regressions RED4 (forced push, accept, reject and aliased promotion);
shared transfer guard/canonical promotion now implemented, owning run pending.
These corrections apply the same record-original theory at actual boundaries,
not a new source identity ledger. Current-code final gates remain pending.

Completed transfer/removal owning run GREEN575 tests,1 existing warning in241.27s;
lint1146/mypy161 passed. Actual first-effect guards now cover World route deletions,
demotion, World reclassification, whole-batch take/keep-World and forced push.
Flat promotion adopts the canonical owned source ID, matching actor promotion.
The removal namespace refusal is the same ledgered conservative ruling as World
moves; its cost is resolving an ambiguous native alias before the operation.
No current full gate is claimed. Frozen review checkpoint follows.

Independent final transfer correction gates at52c5ab3e1 are NON-BLOCKING:
foundation295 owner tests plus real move/delete refusal probes; publication296
owner tests plus14 native publication/promote/whole-batch probes. Actual final
Codex recheck separately found first-copy/Undo native actor mismatch adoption.
Independent agent reproduced10 cases plus4 canonical controls. Root extended
exact-slot/copy-down controls:RED18,4 passing controls; GREEN22 in6.62s.
Shared unresolved calculator now uses captured croot/wroot and the SAME identity
proof as publication; GET, first-copy and Undo require the same exact slot review.
Owning run GREEN831 tests,1 existing warning in313.85s; lint1146/mypy161 passed.

Frozen validation worktree `.worktrees/spec-07-s1-validation` was created at52c5
and full make check started with explicit desktop/Pydantic1 interpreters. It was
stopped after the new actual finding made52c5 obsolete, before a complete backend
gate. Only the verified task-done68396 process tree was stopped; all remaining
workers ended. No full pass or Task5 completion was recorded. This tree will run
the eventual final reviewed snapshot; feature work stays in the original branch.

S1 final actual native code gate APPROVED at62270523a in persisted thread
01a12e23-437b-7493-8f2f-b871d433a23c; no supported substantive S1 gap remains.
Independent adoption recheck NON-BLOCKING:22 probes and338 frozen owner tests,
both physical policies, wrong/exact consent and multi-dependent refusal checked.
Together with earlier closed independent scopes, current S1 code gates are closed.
Full make check and Task5 completion remain pending; no slice completion claim.

Task5: Ruling: freeze the full S1 repository gate in the detached validation tree
at the approved code SHA, while later authorized slices may proceed after their
own plan/code gates. S1 remains incomplete until that full gate passes. This
preserves immutable evidence and avoids later source edits contaminating the run;
cost if a broad S1 regression surfaces is revisiting dependent work. Relevant
S1 owners831 and independent gates already pass. S2 plan's execution timing is
amended explicitly; actual plan recheck required before S2 implementation.

Actual S2 timing recheck APPROVED ata4faa0771 in persisted thread
01a12e1c-0150-71b0-8241-5e17915aed2b. Concurrent S2 development preserves the
immutable S1 run and does not waive completion/shipping gates. Full622705 S1
validation active in session64312; Task5 still pending. S2 inline implementation
may now start with the unchanged approved interfaces/tests.

S2 Task1: pure Pydantic Roster/Affiliation/Index and derivations implemented.
RED19 missing-function failures with21 S1 parser controls passing. GREEN105
membership/schema/Pydantic/import tests in20.54s; lint1146/mypy161. Existing
Pydantic1.10.26 onPython3.14.5 ran40 parser/inverse tests in257.15s (1069 dependency
warnings); this is focused serialization evidence, not Android interpreter parity.
S1's full isolated622705 gate continues separately. Task1 extends the existing
S1 test file and preserves its comments/checks (plan's Create verb was stale).
Theory: group files alone author the roster; leader/member roles independent;
canonical digest covers every projected roster field but ignores prose/state.
No actor existence, I/O, clock or secondary membership authority in this step.

S1 frozen622705 desktop gate:18179 passed,32 skipped,94.11% coverage in2401.60s.
The same full make check continues with actual Python3.11.14/Pydantic1.10
compatibility; S1 Task5 and completion cells remain pending.

S2 Task2: coupled Pydantic group provenance, strict captured ledgers, pre-read
stamps, actual native shadow/tombstone selection and private64-entry memo.
Every warm hit checks current bytes/ledgers/native actor-directory proofs; no
actor-body read or token encode. The actual memo-returned wrapper is validated,
including another reader publishing between preflight and cache lookup.
Ruling: a self-computed mismatching wrapper is already the one fresh uncached
answer. Only another reader's contrary wrapper needs one fresh fallback; no
retry loop. Two real threaded Members/secrecy insertion adversaries pin this.
Initial RED7 missing APIs; directory access RED2 demonstrated glob's silent
empty fallback. Strict directory iteration propagates denial. Layer-marker
RED1 pins actual source layer overriding authored metadata. Owner regression
run232pass; static1146/161, no baseline changes. Final task-level verification
and slice-level API/UI/reviews remain pending.

S2 Task2 final core after actual layer-marker fix:83 passed in13.86s; native physical-policy tests exercised without skips. Current lint1146/mypy161.

S2 Task2 task-done232pass137.62s at79f82a7c0. Actual Py3.11/Pyd1 run81pass/2fail exposed version-specific test fault injection: Path.iterdir uses os.listdir on3.11 versus os.scandir on3.14. Correct both OS-denial boundaries; production collection behavior unchanged. Revalidation pending.

Corrected S2 core83passed8.50s on actual Python3.11.14/Pydantic1.10. Directory-denial checks also pass desktop after fault-injection portability correction. No dependency/store/source behavior changed by this test fix.

S2 foundation independent brutal review NON-BLOCKING at79f82a7c0. Frozen232
regressions; independent captured-byte, denied-read and opposite mixed-native
policy probes pass. One test-injection portability should-fix was reproduced
independently and resolved at94c2e9ad6; corrected actual3.11 core83pass.

S2 Task3: typed full/actor membership responses, earlier literal router and
encoded scope clients implemented. RED14 missing routes/models/order,16 existing
controls; GREEN87 HTTP/order/Pydantic/import/overlay checks in81.77s. TypeScript
passes; current ratchets1146/161/727. Full union response schemas, independent
roles, stored dangling refs without actor reads, strict warm409 and both scope
404/order contracts are covered. Task3 compatibility run and commit closure
follow; this is not the full S2 gate.

User steering2026-10-12: accelerate implementation and reduce excessive testing.
Cadence ruling: focused checks once for each coherent behavioral change; avoid
repeated broad task suites and per-slice full-gate repetition while implementing
approved independent slices. Keep native implementation/final and independent
brutal-review gates, resolve concrete defects, and run the complete branch gate
before declaring all work done. Frozen S1 full gate continues independently.
Task3 actual3.11 response/schema/order30pass13.15s; desktop87pass. TypeScript0,
ratchets1146/161/727; shell wrapper reported1 after npm stderr despite successful
native targets, so future commands explicitly propagate native exit codes.

S2 Task4 coherent UI119pass30.36s across ActorGroups/CharacterPage/PCPage; TypeScript0 and ESLint727 native exit0. Shared keyed loaded value and cleanup guard protect scope/actor changes; World/campaign character+PC links and independent leadership pinned. Full S2 native/independent gates queued on the frozen committed result.

Independent S2 full review blocked843d7897e on unencoded valid scope ids such as realm#1. Four independently failing client probes reproduced supported backend200 scopes. Fix membership-only paths through existing encodeSegment; four URL probes now GREEN alongside30 Incoming Review cases. S3 backend46pass11.54s, UI30pass and TypeScript0; remaining static/gates pending.

S2 actual native implementation review843d thread01a12f25-6170-7af0-aecc-d8386d43cf8e found mixed-store compute capture and `.MD` owned override bugs. Reproduced2RED; fixed through captured logical store/root-owned binding and owned-ID selection; targeted3GREEN. Independent URI recheck non-blockingbbf74decc; corrected native and final spec gates still pending.

S3 complete coherent implementation:46 sync/field regressions,42 field/import/overlay checks,30 Incoming Review cases plus focused raw-whitespace RED/GREEN. TypeScript0; current1146/161/727 ratchets pass. Declarations own extraction/order, local View/rosters own friendly labels, raw values own comparison. Code and final native/independent gates queued; full combined branch gate later under explicit user cadence.

S4 coupled snapshot/member presence implemented after actual amended plan approval.99 core,95 context/actor/golden/frozen compatibility and21 inspector cases pass; TypeScript0; lint1146/mypy161. Existing ESLint baseline727 applies; final combined gate pending. Strict group preflight, snapshot metadata/body/refs, deep isolation and leader/member/headquarters priority are pinned. Native/independent code and final spec gates follow this frozen commit.
S2 corrected actual native implementation review7982fca79 non-blocking, thread01a12f46-372f-79d3-b8df-75ee876a5576; independent store-switch/junction/native-extension recheckc9b742273 non-blocking. S2 final native spec gate is running. S3 independent frozen4ed7da891 brutal review non-blocking; native implementation gate running.

S7 pure SceneGroup projection implemented.7 expected RED,71 focused GREEN; lint1146/mypy161. Inverse-only candidates, independent leader affiliation, cast normalization/deduplication, stable ordering, explicit model visibility and uncapped metadata/capped renderer tested. Native/independent gates queued; combined full gate pending.

User-requested history squash:108 feature commits replaced by97eaeeb5f with identical committed tree71714857ae4fe56e92239b4b49b99e4ad8eabc18. Original commits preserved atbackup/spec-07-before-squash-20261012; dirty implementation/review artifacts retained. Native frozen SHA evidence remains resolvable.
S3 native review found owned native alias naming mismatch. Concrete Windows RED reproduced; directory-only existing identity proof remaps queried aliases to campaign roster names.8 focused field tests GREEN; lint1146/mypy161. Native recheck/final pending.

User requested deletion of the pre-squash backup branch; exact backup/spec-07-before-squash-20261012 deleted.
S7 native review found off-scene roster traversal violating the promised cast-inverse bound. Tuple-iteration guard RED; present actors now accumulated from inverse entries.8 focused GREEN, lint1146/mypy161. Corrected native/final and independent rechecks queued.

S6 implemented: one outside-hold inverse read; groups after all actor families; endpoint closure/independent roles; ordinary family omission and strict identity refusal.109 backend/route controls plus corrected authored-layer birthday-only case pass;32 model/detail +27 layout +47 view/keyboard cases pass; TypeScript0, lint1146/mypy161/eslint727. Coherent code checkpoint; native/independent/final and combined browser/full gate pending.
S2 actual final native spec gate approved7982fca79 thread01a12f52-1498-7282-ac6b-a12e58507a17. S4 native code static non-blocking741351775 thread01a12f5b-4e64-7291-8c36-a770a9076a34 (completed status0 with transient503 warning); clean final spec gate approved same frozenSHA thread01a12f62-7395-7970-9e50-57172265d915. Independent S4 non-blocking six probes. Full branch completion still requires combined full gate.

Rebased the four post-squash commits ontoorigin/main be59c1dc2. Merged Art and membership contracts without replacing source layers. Stale automatic rerere dropped existing tests in two files; restored full upstream corpus plus Members test (582/558 lines).133 restored corpus tests and350 merged UI cases pass; TypeScript0; current lint1136/mypy161/eslint706. Independent S3/S7 targeted rechecks and S6 frozena04da7a33 gate are non-blocking. Frozen review SHAs retain the exact reviewed source; subsequent combined gate owns rebased integration.
S5 concrete plan written from spec4.5/9/17.4 and existing owner audit; native plan gate pending.

S3 native recheck found flat-record native alias naming and exact-owned quadratic probing.2RED supported repros; generalized native identity indexing, exact/invalid fast paths;10 focused GREEN plus malformed/exact guard1GREEN, lint1136/mypy161. Corrected native/independent gates queued.
Frozen S1 full gate was stopped via its owned session64312 after over90 minutes without progress at96 percent of actual Pyd1 tests. Desktop18179 evidence retained, Pyd1/full completion not claimed. This superseded pre-rebase per-slice run is incomplete; the final combined full gate remains required.

S5 actual native plan gate3fcaefe18 needs attention, persisted thread01a12f7a-c85a-7540-ba87-766a1cc716c8: supported tokenless saves bypass reserve/checkpoint. Plan/preparation audit amended to require a nonempty commit token before effects for membership saves while preserving tokenless non-membership saves. Same-thread recheck queued before code. S8 concrete provider integration/capture adapter plan written against frozen03 providercccb5ac55; actual plan gate pending.
S6 native code3fcaefe18 non-blocking thread01a12f7a-bc94-79d0-9dce-ca457015b504. Corrected S7 native code3fcaefe18 non-blocking thread01a12f7a-b4af-77b1-8ecb-1ab0687a6972. Final originating-spec gates queued; combined full/browser gate pending.

S5 Task1 strict account foundation:16 expected RED;47 account/legacy controls GREEN,17 targeted strict/fence cases after nested validation, owning import/lock guards pass.1136/161 static ratchets. Absence alone is empty; complete legacy marker-named entries survive; undated spent entries do not expire by lexical accident. Initial progress interface already existed and is reused. Stale checkpoints preserve durable provenance/relocation/retirement; finalization retains target history while dropping bulky progress. This is foundation only; token/barrier/capture/staging/writer/UI work remains.


### 2026-10-12 S5 token/barrier checkpoint

The strict host-only membership review ledger now registers stable nonces, refuses
unknown/retired identities, records trusted owned-copy continuity and durably
retires observed source changes. The pinned World process barrier excludes other
servers, requires acquisition before campaign/world-actor holds, and resets old
references after fork. Reused the approved spec03 library pin/publication helpers
early because explicit old campaign roots must never write while holding a new
store's campaign OS lock. No stored provenance path is opened.

Focused token/model/lock-order checks:75 passed before the root-pin correction;
focused corrected token/data-dir/pointer checks:34 passed. Ruff1136 and mypy161
remain at baseline. Lifecycle discovery/invalidation, pre-generation capture,
staging/apply/Undo and the S5 slice gates remain pending. This checkpoint does not
claim S5 completion or final combined validation.

S5 foundation independent brutal review: non-blocking after two concrete fixes.
The stale-root campaign comparison now uses native samefile identity and refuses
missing/denied selected roots; lexical WindowsPath equality cannot merge real
case-sensitive realm/REALM stores. An independent held-lock subprocess repro
confirmed both ordinary and native stale-root calls refuse without writing.
World-actor reverse barrier acquisition also refuses. Corrected foundation tests:
13 passed (within the16-case token/schema focused run). Full S5 remains pending.


### 2026-10-12 S5 model-derived extraction checkpoint

Added plain Pydantic extraction and membership-change models. A generic v1/v2
adapter expands references, closes objects, marks every key required, restores
optional null alternatives, and refuses unsupported schema constraints. The
existing portable schemas.check/render owners validate and render the same
contract for the prompt and operations.generate schema argument. Raw parsing
still decides null/omission versus explicit empty-string clears before defaults.
The state materializer preserves omitted current state and stages last-field
character/group clears.

Initial existing Absorb/weather run:271 passed,9 contract assertions failed as the
new membership section and generated template input arrived. Updated those
specific contract fixtures:16 focused checks passed, including all prior failures
and real state writes. Actual Python3.11/Pydantic1 model checks:5 passed. Ruff1136
and mypy161 remain at baseline. Membership lifecycle/capture/staging/apply/Undo,
UI/evals and S5 native/final gates remain pending; no full branch claim.


### 2026-10-12 S5 lifecycle checkpoint and revised execution cadence

World deletion, demotion and reclassification now take the pinned source barrier
before discovering current dependents and strict historical token/unfinished
commit claims. The sorted held union preflights complete accounts. Token leases
are revoked before identity removal/movement; already reserved membership targets
retire or relocate separately to actual landed suffixes. Trusted final copy
publication preserves nonces and records per-position copy receipts. Owned
removal/reclassification uses the same strict account fan-out. Membership change
normalization now lowercases join/leave, closing the independent schema review's
P3 tolerance finding.

Before the user's latest cadence change, focused account/lifecycle checks24 passed,
existing reclassification/sync checks86 passed, and native group lifecycle checks22
passed. Ruff1136/mypy161 were at baseline before the final helper extraction.
These are scoped results, not a S5 or full-branch completion claim.

The user explicitly instructed: "Focus on implementation, testing and review we
can do on the PR." From this point further tests and review gates are deferred to
PR work. Continue implementation and coherent commits without repeated validation
or approval questions; leave deferred QA and native gates visibly pending. The
remaining S5 capture/reservation/staging/writer/Undo/UI work and S8 implementation
are still required before handoff.


### 2026-10-12 S5 host capture/reservation checkpoint

Added a Pydantic host capture combining one selected-byte group snapshot, existing
actor IDs and pre-generation leases. Preparation takes the outer World barrier,
checks the captured binding, and releases holds before generation. Group context
uses one inverse, distinct Members/Leader roles and explicit secret narrator
framing; identity failures propagate and GM-only content is excluded. Membership
rows stage only against captured groups/actors, retain first pair occurrence, drop
no-ops, and route on the actor's citation.

Chronicle saves reject tokenless membership before target reads, validate row
shape, freeze original-position targets before the first write and resume from
durable targets instead of the request's vacated group slug. Binary actor conflict
keys remain separate and membership is not mergeable. Further checks/review are
deferred by user instruction; writer/recovery/Undo/UI and S8 remain in progress.


### 2026-10-12 S5 actor writer and Undo checkpoint

The actor writer and original-position apply ordering are implemented. Neutral
first-copy construction removes only approved initially absent filtered actors,
preserves pending present leaves and Leader, and consumes only the current row's
Members consent. Settled rows do not copy or publish diffs. Returned copy receipts
advance local reserved targets, and post-attempt I/O ambiguity remains pending.
Deferred actor citations/diffs/Undo are suppressed or retargeted from durable
lifetimes before publication. Actor bool Undo restores only its slot under scoped
owned-target and original World-reference guards. Capture durably retires observed
scope mismatches even if the binding later returns. A patch interruption split
writer and Undo into separate coherent commits; Undo is included in this follow-up.

Further testing/review remains deferred to PR work by explicit user instruction.
The branch is still being completed for PR readiness; no new pass claims.


### 2026-10-12 S8 reviewed provider integration checkpoint

Imported frozen spec03 infrastructure atcccb5ac55a7e3af3805c61055ad655d03aedca45:
compiled provider, file-stat support, BUILD fingerprint/stamp and shared template
resolver. Reused its resource dataclasses unchanged as the approved dependency
protocol; new membership wire/artifact values remain plain Pydantic. Ported narrow
fork/reset, shutdown close, Storage transition, deletion intent/purge and Android
stamp hooks. Current S1/S5 and Art routes remain intact. World-tree deletion also
fences historical inherited membership claims before removal. Optional purge is
outside run/root/campaign holds and response stamping retains the captured root.

Provider/adapter validation and review are deferred to the PR by user instruction.
The membership artifact adapter and final UI/eval/identity bookkeeping remain in
progress; no CI or full-branch pass claim is made.


### 2026-10-12 S8 adapter and S5 review UI checkpoint

Registered optional membership_index version1 with persisted-source trust disabled.
Keys use selected captured bytes, strict ledger digests, proven logical library
roots and current native detachment proof. Provider generation fencing begins
before capture/hash work; every warm process/persistent path still performs strict
fresh input preflight. Complete coupled Pydantic index/record/proof artifacts are
serialized externally and rejected on malformed shape, root/content/proof mismatch
or index/record disagreement. Public values remain independently copied, and one
fresh uncached fallback retains S2 behavior. Arbitrary direct World roots without
host library provenance skip persistence. Registration is lazy; eager reverse
source registration/rebuilding remains the explicit soft03-S4 follow-up, with no
measured speedup claim.

Membership rows now render read-only actor status in Relationships using the
existing approval/citation/conflict drawer, and membership deltas are browsable.
Remaining implementation housekeeping includes durable deferred-target fan-out,
root transition identity retirement and eval contract additions. Further testing,
independent/native reviews, browser acceptance and CI remain deferred to PR work.


### 2026-10-12 final recovery and root-transition implementation checkpoint

Unfinished target RMW and checkpoint merge now share actor-specific deferred
fan-out: actual relocation IDs, retirement and suspension reach journalled Undo,
citation keys and field diffs without touching another actor or Leader. Resume
observations durably retire exactly the reserved position and matching nonce;
returning the binding to its old value does not revive it. Lazy campaign pruning
fences owned review accounts before manifest reservation, and sync take-World
continues through the same dematerialization owner.

Storage changes prepare the effective destination first, then hold host-discovered
World barriers before sorted campaign holds, preflight all token/commit accounts,
and retire inherited claims before pointer publication. Owned/trusted retained
copies survive. Optional persistent serialization failures retain the live answer;
strict native source proof is outside optional artifact decoding.

Only source parsing was checked for this checkpoint. Tests, independent/native
review and CI remain deferred to the PR, as the user explicitly requested. The
user has now also authorized pushing and opening that PR once implementation is
handled; no further integration-choice prompt is required.


### 2026-10-12 extraction eval implementation checkpoint

The synthetic absorb fixture now includes Mara explicitly joining the existing
Salt Circle, captures host leases under the production scope and shares that
capture across prompt/materialization. The compliant recording must yield the
actor-specific host proposal. Existing negative recordings include the new empty
section so they retain their intended isolated failures; a new [null] membership
recording fails the raw contract grader. No frozen campaign fixture was changed.

Eval execution, like the remaining behavioral checks, remains deferred to PR CI.


### 2026-10-12 PR handoff

S1-S8 implementation is committed for PR handoff: explicit Members and copy
consent, coupled inverse/API/UI, typed sync, structural presence, host-scoped
absorb proposals/save recovery/Undo, Story Graph, retrieval projections and the
optional persistent inverse. Model/wire contracts use plain Pydantic with external
serialization and one model-derived portable structured output schema.

The latest checkpoint received source parsing only. No full make check, combined
final native gate, final independent brutal review, browser acceptance or APK
build is claimed. Earlier scoped results retain their dated checkpoints above.
Those checks and review follow on the PR by user instruction. Persistent reverse
source registration/eager rebuilding stays the approved soft03-S4 follow-up.
The feature worktree is retained for PR fixes; no merge or cleanup is requested.


### 2026-10-12 PR takeover: 07-S8 deferred to 03

The persistent tier and the vendored 03 provider (`store/compiled.py`,
`store/file_stats.py`, `compiled_build.py`, `template_paths.py`, the Android
build stamp and the delete/root-change purge hooks) are removed from this PR.
07-S8 hard-needs 03-S2 and 03-S3, neither of which has landed or been
reviewed; the copy used neither collections nor the materialized record, had
no tests, did strictly more work on a hit than a fresh derive, and wrote
`<home>/.cache/compiled` on ordinary reads (Story Graph, turn composition),
which failed `test_the_read_only_sweep_writes_nothing`. S2's in-process memo
is unchanged and serves every consumer. 07-S8 keeps its plan and returns once
03-S2/03-S3 land, consuming 03's own reader as section 7.4 describes.


### 2026-10-12 PR takeover: audit and fixes

Four per-slice audits (S1/S3, S2/S8, S4/S6/S7, S5) compared the branch with
this spec, and an adversarial review covered the fixes. The Codex CLI was not
available in the takeover environment, so these are recorded substitutes for
`/codex:review` and the final gate; the checklist's Review and Final gate cells
stay unticked until the Codex gates run.

Fixed:
- Gates: ruff/mypy ratchets, the template harness and the LLM-fake cassette
  check (both now render the one cached extraction schema), WorldView's mock.
- S1/S3: an unrelated push no longer requires readable identity ledgers; the
  incoming view is fail-soft again; the refusal names its remedy; every
  unresolved Leader token is reported for consent.
- S4: a campaign that sees no group composes past a damaged ledger (11.1).
- S6: Groups alone no longer draws orphaned group nodes (10.2).
- S5: the snapshot (present affiliates only, framed secret block), the prompt
  paragraph after group_state_edits, Present-line actor spelling (previously
  dropped, so nothing was ever proposed), the spec's id/label/payload, Present
  cast only, per-row skip/fail instead of a whole-save 409 or a 500 on a
  deleted group, validated neutral-copy authority, membership out of the body
  changes log, leave-undo consent limited to dangling cleanups, the Group
  membership drawer.
- Acceptance tests added for 17.1, 17.2 (detached), 17.4 (staging, snapshot,
  per-row outcomes, citations, undo, and the absorb -> review -> save -> undo
  route path), 17.5, 17.6, 17.8, and the v1/v2 schema parity fixture.

Open decisions recorded rather than changed: publication validates Leader
tokens as well as members (Q4, adopted by the S1 plan); 9.4.1's general
"choose any row that can copy safely" fallback is implemented only for the
dangling-leave case it names.
