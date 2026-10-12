# Spec 07 S1 final brutal recheck

- Frozen implementation: `02131dd93be627d6f45fb3c960c48161e04a0d20`.
- Recheck base: `a9b8915f5280f81337f064eb3fd3255bc4a2dbf2`.
- Previous finding: [final brutal review, F1](2026-10-11-spec-07-s1-final-brutal-review.md).
- Contract: [approved spec](../specs/2026-10-09-roadmap-07-explicit-group-membership-design.md), **07-S1 only**, and [S1 plan](../plans/2026-10-11-roadmap-07-s1-members-field.md).

## Verdict: non-blocking for this scoped recheck

**F1 is resolved on the frozen revision.** Both original independent failing cases now pass, their missing-context control remains passing, and the four implementation regression cases pass with the editor/incoming-review suites. No supported new defect was found in the reviewed asynchronous guards or the necessary backend/type-gate changes.

This closes the finding against the earlier frozen revision. It is not a claim that a new full S1 review, the full repository gate, or a native Codex slash-command review completed here.

## F1 closure

`captureViewGuard` snapshots both the active read and scope generations. The new-group save branch now checks that guard after creation and after its listing reload, before clearing the form. The existing-group save uses the same helper without weakening its prior guard. This preserves a different group's current draft and explicit binding choice when the earlier request completes.

`discardAndReload` captures the old view, clears its own consent/context, and waits for the listing. It checks the old view before issuing another record read, so the stale action cannot mint a fresh token on the newly selected screen. Its selected read receives an explicit token; a failed read only resets the form if both that token and its scope are still current. The rail reload also checks scope generation before installing its result.

The neighboring reclassify and delete completions now refuse to reset/navigate a newer view. Reclassify checks again after the listing and ignores old-view errors. The four added repository cases cover stale reload, new-group save, reclassification and deletion after navigation.

### Independent attacks and control

The original reviewer-added cases were copied unchanged from the previous disposable test tree into a **new** disposable extraction of the frozen revision. They were not added to repository tests and no product files were modified.

| Independent case | Previous frozen revision | Rechecked revision |
|---|---|---|
| Delayed stale-banner reload completes after changing campaign | Failed: unexpected third read of the old campaign | Passed: no old read; new body retained |
| Delayed create completes after opening an existing group and selecting Members consent in the same campaign | Failed: blank new-group form replaced active edit | Passed: active binding checkbox stays checked; Save remains present |
| Missing-context direct reload followed by navigation | Passed; candidate rejected | Passed; existing request-token control preserved |

The create case uses a different record within the **same** campaign, which independently exercises `readReq` rather than relying only on scope changes. The stale reload case changes campaign while the old listing is unresolved. Together they cover both components of the shared guard.

## Narrow gate fixes

- `undo._require_members_target` explicitly requires a string id before `safe_id` and owned-path resolution. `safe_id` already rejects non-strings, so this makes the type narrowing explicit without admitting a new target shape.
- `routes.campaigns._journal_row` now narrows the plan and target dictionaries explicitly before reading membership lifetime flags. A malformed non-dictionary target remains non-reversible; valid retired, suspended or non-group targets remain disabled. The earlier `reversible` predicate still controls the response.
- `mypy.ini` pins `platform = linux`, matching the frozen CI mypy job's `ubuntu-24.04` platform. The shared baseline file did not change. An independent ratchet run returned **165 findings, all at baseline**, exit 0. This controls the static platform view; it does not alter runtime platform selection. Windows-only static branches are consequently not separately typechecked by this one shared ratchet.

## Verification evidence

Frozen public source was extracted with `git archive` into `<Temp>/spec07-recheck-02131dd`. Backend `PYTHONPATH` pointed to that extraction's `backend/src`; the main checkout's venv supplied only the interpreter/dependencies. The frontend used frozen source/config with already-installed worktree dependencies through a Temp junction. No private store, running verification helper, real model, product edit or commit was involved.

From the disposable frontend:

```powershell
& ./node_modules/.bin/vitest.cmd run src/components/EntityEditor.test.tsx src/components/IncomingReview.test.tsx --reporter=json --outputFile=<Temp>/spec07-recheck-ui-result.json
& ./node_modules/.bin/tsc.cmd -b
```

**179 tests passed, zero failed**: 176 frozen repository tests plus the three independent attack/control cases. JSON report `success: true`. Project strict typecheck `tsc -b`: **exit 0**.

The first test launch happened while the coordinator's concurrent dependency installation temporarily removed `vitest.cmd`; it did not run tests. A subsequent guessed `tsconfig.app.json` command failed because this project uses `tsconfig.json`. Neither setup error is a product failure or a pass claim; the actual project command above completed successfully.

From the disposable repository root:

```powershell
$env:PYTHONPATH='<Temp>/spec07-recheck-02131dd/backend/src'
& C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend/tests/test_group_membership_lifecycle.py backend/tests/test_group_membership_routes.py backend/tests/test_journal_store.py backend/tests/test_undo_store.py backend/tests/test_docs_guard.py -q --junitxml=<Temp>/spec07-recheck-backend.xml
& C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe scripts/ratchet.py mypy
```

**195 backend tests passed**, exit 0, with one existing Starlette/httpx deprecation warning. Mypy: **165 findings, all at baseline**, exit 0.

## Scope limits

This is a scoped recheck of F1's delayed save/reload behavior, the added reclassify/delete/rail guards, and the two backend narrowings plus mypy platform pin. The earlier backend attack pass remains the evidence for unchanged S1 copy/publication/lifecycle surfaces; it was not repeated exhaustively. Template/wizard-specific asynchronous callbacks were not newly audited in this pass. No full repository coverage gate, Pydantic 1, APK or actual browser run was performed here. S2-S8 remain intentionally outside the review. The coordinator's concurrent full gate and separate native reviewer are not represented as independently completed by this artifact.