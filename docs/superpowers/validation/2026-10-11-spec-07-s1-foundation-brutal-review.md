# Spec 07 S1 foundation independent brutal review

Reviewed commit: `c97f78353407da961870444d6f5adc5051878e8a`.
Base: `f816921c8d2bd91628cfe12e28bda9106a254f11` (`origin/main` at review assignment).
Date: 2026-10-11. This is an early foundation checkpoint, not the final S1 implementation/spec gate.

Verdict: **non-blocking**. One should-fix finding survives reproduction. No blocking defect was found within the implemented foundation. The finding below cannot currently be reached through the two production reclassification callers with an invalid ref, which limits its severity.

## Finding 1: repoint can publish a ledger its own strict reader rejects

**Severity:** Should fix; non-blocking public-helper invariant/spec gap.

**Where:** `backend/src/grimoire/store/overlay.py:1046` and `:1059` at the reviewed commit (`repoint_record`).

**Failing case:** In a disposable campaign, call:

```python
overlay.add_detached(cid, "groups/salt-circle")
overlay.repoint_record(cid, "groups/salt-circle", "unknown/salt-circle", keep_base=False)
overlay.detached_strict(cid)
```

The repoint succeeds and publishes `["unknown/salt-circle"]`; the next strict read raises `GroupIdentityConflict`. The same unvalidated replacement is used for tombstones and, with `keep_base=True`, the manifest. Existing ledgers are validated before effects, but the refs being repointed are not. Spec section 7.7 explicitly requires both.

**Consequence:** A caller of this public store helper can turn valid identity bookkeeping into a ledger that blocks later actor creation, group deletion/reclassification and library-image deletion until repaired. A present manifest base may also have been moved/dropped before the invalid ledger is published.

**Attempt to disprove:** Traced both production calls (`reclassify.campaign_entity` and `_follow_in_campaign`). They construct refs from kinds/ids checked by entity reclassification, so there is no demonstrated HTTP path to the malformed argument. This is why the finding does not block the foundation checkpoint. The direct public-helper reproduction did succeed against the frozen source; it is not a hypothetical race or an assertion inferred from a mock.

**Specific fix:** Validate both record refs against the record/detachment grammar under the same campaign hold before touching the manifest or ledgers. Raise `GroupIdentityConflict` for invalid refs. Add isolated cases for invalid destination kind, extra separator and traversal; assert manifest and both ledger bytes remain unchanged. Keep legitimate `plotmap/plotmap` and safe non-referenceable ids valid.

## Evidence and attacks

The review used `git show`/the frozen diff and a `git archive` extraction of the reviewed commit into a Temp directory. It did not review the primary agent's later implementation as part of this verdict.

- Configured mechanical lint on frozen sources: `python scripts/ratchet.py ruff` passed, **1146 findings, all at baseline**. An initial invocation ran before archive extraction completed and could not find the script; the completed extraction was rerun successfully. This was an operational failure, not a lint result.
- Frozen-source focused pytest run: **230 passed**, one existing FastAPI/Starlette deprecation warning. Selection: `test_membership.py`, `test_group_membership_store.py`, `test_entity_schema.py`, `test_entities_store.py`, `test_create_world.py`, `test_overlay_tombstones.py`. `PYTHONPATH=backend/src`, disposable `GRIMOIRE_HOME`, and `-p no:cacheprovider` were set; individual store fixtures also isolate their homes.
- Optional sibling `python-code-doctor/scripts/analyze_diff.py` ran successfully (exit 0) against the **live worktree**, with explicit base `f816921c8d2bd91628cfe12e28bda9106a254f11`, and emitted 18 heuristic findings. This was not a clean/no-findings result or a frozen-commit gate. The candidates were checked against frozen code: optional returns are intentional; grammar segment counts encode existing formats; complexity/test-control-flow warnings supplied no concrete failing behavior. None was promoted to a defect finding.
- The invalid-repoint reproduction above ran separately on frozen sources with a `TemporaryDirectory` home and printed the invalid persisted marker followed by `GroupIdentityConflict` from the next strict read.

Attacked raw-token preservation and duplicate handling; first-valid Leader semantics and dormant Leader slots; non-string/legacy/ref grammar boundaries; inherited-only filtering; plain Pydantic fields and frontend type shape; field-table drift parsing; generic World field name resolution; strict JSON/type/decode/access failures; legitimate record/plotmap/asset/library marker shapes; manifest-before-ledger ordering; actor-birth, entity-delete/reclassify and library-image preflight ordering; nested/reentrant campaign holds and sorted `hold_all`; and the standalone World wrapper's all-dependent preflight. Traced changed ledger-mutator callers through overlay, sync, reclassification, campaign migration and image paths to distinguish implemented boundaries from later work.

Candidate concerns rejected: whitespace normalization is expressly allowed by the raw-token contract; safe filesystem ids need not satisfy the narrower actor-ref grammar; duplicate ledger markers intentionally collapse into sets without losing identities; multi-file atomicity and external-editor coordination are not promised; the World wrapper is deliberately not S5's discovery barrier. The Windows test adjustment retains public invalid-name validation and skips only creating a filename Windows cannot create. Case sensitivity is explicitly simulated by the corrected test.

## Not reviewed

This verdict covers Task 1 and the committed initial Task 2 foundation only. It does not approve the remaining Task 2 preflight integrations, route conflict translation, Task 3 prepared copies/publication guards, Task 4 API/journal/Undo/lifetime fences, Task 5 editor consent, or S2-S8. Their absence is not a finding in this checkpoint. Existing production callers outside the committed preflight subset are traced for compatibility, not certified as completing their planned mutation safeguards.

The full `make check`, mypy/eslint, frontend browser/Vitest/typecheck, Pydantic 1 suite, Android build, concurrency stress/fault-injection beyond the existing selected tests, and actual app/LLM execution were not run by this reviewer. Author-supplied broader passes are supporting context, not independently reproduced claims here. No real store was opened, no frozen campaign fixture was regenerated, and no production/test source was edited. The only repository write from this reviewer is this artifact. A final independent full-S1 code review and diff-versus-spec review remain required.
