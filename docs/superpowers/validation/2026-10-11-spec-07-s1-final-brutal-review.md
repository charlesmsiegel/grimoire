# Spec 07 S1 final brutal review

- Frozen implementation: `a9b8915f5280f81337f064eb3fd3255bc4a2dbf2`.
- Compared with `origin/main` at `f816921c8d2bd91628cfe12e28bda9106a254f11`.
- Contract: [approved spec](../specs/2026-10-09-roadmap-07-explicit-group-membership-design.md), **07-S1 only**, and [S1 plan](../plans/2026-10-11-roadmap-07-s1-members-field.md).
- Independent review followed the local brutal-review skill and its attack/checkpoint rubric. No product or repository test files were edited. No commits, private stores, live model calls, or verification helpers were used.

## Verdict: blocking

**F1 blocks this frozen revision.** The backend identity/lifecycle attack pass found no additional supported defect, but the editor's required asynchronous navigation isolation is incomplete. This verdict does not certify later fixes, the full repository gate, or S2-S8.

## F1 — Blocking: old asynchronous editor actions overwrite the newly selected group's draft/context

**Where:** `frontend/src/components/EntityEditor.tsx:873` (create success branch) and `:898` (`discardAndReload`). Line numbers refer to the frozen commit.

**Contract:** S1 plan Task 5 requires scope/record/reload/success reset coverage and says late replies must not restore old context/consent. The edit-save branch already captures `readReq` and `scopeGen` and tests them before using its response. These two paths do not.

**Concrete sequence A, stale-record reload:**

1. Open Salt Circle in campaign Saltmarch, edit it, and receive `stale_record` on Save with a current revision.
2. Click **Discard mine and reload**. Hold that action's `listEntities` response pending.
3. Navigate to Salt Circle in another campaign, Saltmarch-2; let its read complete and show its new body.
4. Resolve the old campaign's pending listing.
5. `discardAndReload` invokes its old `select(editing)` closure, which increments the *current* `readReq`. It therefore treats the old campaign read as the newest read and installs its fields/revision/membership state over the newly selected screen.

**Observed:** the disposable test expected two `readEntity` calls and received three. The third read is the old action's read. The context key prevents treating that old context as the new campaign's authority, so the symptom includes a forced reload/disabled Save rather than a demonstrated silent consent bypass. It still replaces the current user's screen/draft with the wrong campaign's record.

**Concrete sequence B, create completion:**

1. Start creating a new group and hold `createEntity` pending.
2. Navigate to the existing Salt Circle, enter Edit and select its explicit Members binding checkbox (or type a new unsaved body).
3. Resolve the earlier create successfully.
4. The create branch runs `reload()` and `resetForm()` without testing the captured request/scope. The existing group's draft, membership context, and opt-in disappear; the screen becomes a blank **New group** form while the selected URL still names the existing group.

**Observed:** the disposable test can no longer find `Bind Members reference characters:mara` after the old create resolves; the rendered form is the new-group form. This discards current unsaved work during an ordinary slow-request/navigation sequence.

**Fix:** reuse the existing request/scope generation fence for create success and stale-record reload, checking after every awaited operation and before reload-driven state writes, `select`, or `resetForm`. A stale action must not mint a fresh read token for the current screen. Keep the active screen's draft and explicit choices intact. Audit the neighboring reclassify/template/remove completion paths for the same pattern; only the two sequences above are independently reproduced findings here.

## Exact reproduction evidence

Public frozen source was extracted with `git archive` into a disposable Temp tree. Backend `PYTHONPATH` named that tree's `backend/src`; the shared venv was used only as the interpreter/dependency installation. Frontend used the frozen source/config and the worktree's installed dependencies through a Temp junction. Additional test cases existed only in the disposable copy of `EntityEditor.test.tsx`, appended inside its existing `spec 07 group reference consent` describe, using its `record`, `context`, `scope`, `show`, `edit`, `deferred`, and `editorWith` helpers.

- `review: a late stale-record reload cannot reopen the previous scope`: **FAIL**, `expected "spy" to be called 2 times, but got 3 times`.
- `review: a late create must preserve another group's in-progress consent`: **FAIL**, binding checkbox absent after the delayed create resolves.
- `review: a late missing-context reload cannot reopen the previous scope`: **PASS**. This is a deliberately rejected candidate: the new missing-context button calls `select` immediately, whose response guard discards the stale read. It is not the stale-record banner's `discardAndReload` path.

The successful-create test's initial selector was corrected from `Create` to the real `Create group` control before recording the product failure. The initial selector error is not evidence of a product defect.

Commands from the disposable frontend directory:

```powershell
& ./node_modules/.bin/vitest.cmd run src/components/EntityEditor.test.tsx -t 'review: a late (stale|create)'
& ./node_modules/.bin/vitest.cmd run src/components/EntityEditor.test.tsx -t 'review: a late create' --reporter=json --outputFile=<Temp>/spec07-final-create-result.json
```

A subsequent combined editor/incoming-review run executed **175 tests: 173 passed, 2 failed**. That is all **172 original tests passing**, plus the passing rejected-candidate control and the two failing attack cases. The attempted negative-name filter did not exclude the added cases in that invocation; counts here describe the actual JSON results rather than the intended filter.

Backend command from the disposable root:

```powershell
$env:PYTHONPATH='<Temp>/spec07-final-a9b8915/backend/src'
& C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend/tests/test_group_membership_store.py backend/tests/test_group_membership_routes.py backend/tests/test_group_membership_lifecycle.py backend/tests/test_membership.py -q --junitxml=<Temp>/spec07-final-backend.xml
```

**231 passed**, exit 0, with one existing Starlette/httpx deprecation warning. A preceding run's final status was not captured and is not counted as verification evidence.

## Attack coverage and rejected candidates

The frozen implementation and its callers/tests were traced for:

- Plain Pydantic context/bindings, field mirror, raw-token preservation, independent Members/Leader slots, and current-versus-inherited context.
- Strict complete Off/Gone reads and read-modify-write, actor births, reattachment/repoint, image deletion, migration and World dependent preflight before effects.
- Native `samefile` alias checks, selected-source context/hash coupling, final prepared first-copy publication, original World base, exact retained-slot consent, and dormant fields through reclassification.
- Missing/detached World actors on push/promote, forced publication, and all-dependent demotion copy/asset preflight using captured source text.
- Campaign group GET/PUT, revision-check/write/journal hold, JSON-native Members snapshots, owned-only restoration, scope/original World/consent retention, conservative unresolved-ref removal and Undo/Redo CAS.
- Permanent retirement before delete/take-world/pruning; operation-scoped suspension before moves; suffixed destinations; independent detached originals; preflight and post-effect failure revision stamps under the existing hold.
- Editor foreign chips overriding matching actor options, separate initially unchecked opt-ins, changed fields, removal/re-add, missing-context reload, direct read and edit-save generation fences, identity-conflict 409 without retry, and the temporary incoming-change hint.

The focused backend suite exercises these boundaries with malformed ledgers, access failures, injected atomic failures, native aliases and event-controlled interleavings. No new backend finding survived this pass. The passing UI tests cover ordinary consent/reset flows but omit the two delayed-action sequences in F1.

## Limits / not reviewed

S2 inverse/index/routes/actor pages, S3 typed sync rendering, S4 presence, S5 absorb proposals/progress/tokens/barrier, S6 graph, S7 projections and S8 cache are deliberately pending and are not missing-S1 findings. No APK, Pydantic 1 run, full-suite coverage run or real-browser interaction was independently performed here. No optional python-code-doctor detector ran; the coordinator owns the repository mechanical/full-gate run and reported mypy failures on the frozen revision while this review was running. This review makes no full-gate success claim. Later working-tree fixes and prior scoped rechecks are supporting context, not certification of this frozen commit. This is an independent brutal-review artifact, not a claim that a separate native Codex slash-command gate ran.