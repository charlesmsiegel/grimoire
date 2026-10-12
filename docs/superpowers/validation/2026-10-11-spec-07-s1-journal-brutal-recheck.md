# Spec 07 S1 journal and lifetime checkpoint: independent brutal recheck

Date: 2026-10-11

Frozen commit: `09d1460bb346aa87cc461b695169f1fe01d6ed50`.
Prior checkpoint: `6a75b98ae8ef250e29cc0d75810b4dba57856b6b`.

**Verdict: blocking**, driven by the newly reproduced take-world native-alias bypass below. Both prior findings are resolved at this frozen commit. The reviewed World failure-stamping cases also pass. This is a scoped checkpoint, not final S1 certification.

Applied the brutal-review skill, approved S1 spec/plan, and repository conventions. Source and tests were extracted from the frozen commit to Temp. No product/test files were edited, no commits were made, and no private store or live application was used.

## Remaining finding

### Blocking: take-world can delete a detached independent group through its native filesystem alias

**Where:** `backend/src/grimoire/store/sync.py:237-238`, reached by `backend/src/grimoire/routes/campaigns.py:1101-1104`.

**Concrete sequence, reproduced on the native Windows filesystem:**

1. Create World Realm, dependent Saltmarch, and World group Salt Circle.
2. Save campaign Members `characters:mara` with a Members journal offer, creating the owned original.
3. Delete the World group using the World delete route. Verify the owned group survives and `detached_strict(cid)` contains `groups/salt-circle`.
4. Recreate the World group at `salt-circle` with Members `pcs:winifred`.
5. Submit `RefList(refs=[{'kind': 'groups', 'id': 'SALT-CIRCLE'}])` to `routes.campaigns.post_accept(cid, body)`.

The validated route returns `{'ok': True}`. The independent campaign original is deleted, the replacement World Members become effective, and the original Undo target is permanently retired. Before the call, the uppercase and lowercase owned paths report `samefile == True`.

```text
detached {'groups/salt-circle'}
alias_samefile True
route_response {'ok': True}
owned_still_exists False
effective_members pcs:winifred
retired True
```

**Cause and consequence:** `_advance` excludes detached records using raw slash-ref equality only. The uppercase request bypasses that check, while the filesystem resolves it to the protected lowercase file. Take-world then dematerializes the independent original. Correct journal retirement prevents Undo from accidentally reaching a replacement, but cannot recover the original that this operation should never have removed.

**Disproof attempt:** This was rerun through the typed public route handler, not only the store helper; `RefList` and the route do not canonicalize/reject the alias. The detachment was produced by actual World deletion, not invented fixture metadata. The exact spelling is excluded by the existing guard; the differing spelling is proven to name the same file. No casefold assumption is required. The raw-string guard predates this latest repair, but it is a concrete defect in the expressly reviewed take-world/lifetime caller seam.

**Specific fix:** Make detached exclusion in `_advance` respect actual queried record identity, reusing a samefile-aware identity predicate under its existing hold. Preserve distinctions on case-sensitive filesystems. Review the adjacent pinned-ref exclusion for the same identity seam, without treating the untested pinned variant as another finding. Add the real delete/recreate/alias accept sequence and assert owned bytes, detached metadata, and Undo targets remain unchanged.

## Prior finding resolutions

1. **World replacement moving a detached original: resolved.** `overlay.independent_owned_record` checks exact detached refs and native `samefile` identity. `reclassify.world_entity` excludes those campaigns before both suspension and following/repointing. The original independent reproduction now reports `group survives: True` and `journal target: groups`. The updated legacy detached-follow expectation matches the approved Task 4 requirement; this is not a new policy decision.
2. **Post-publication Members journal readback stamp: resolved.** `journalled_group_members` now wraps post-yield scope validation, owned-target sealing and readback in `revision.stamp_on_failure` within its campaign hold. The original injected PermissionError after durable Members publication now changes the revision before release, and `revision.require` rejects the old token under the next hold. Middleware's eventual unhandled-exception stamp is no longer needed to close that boundary. Initial preflight and best-effort manual append semantics remain separate.

## World and lifecycle failure boundary evidence

Reviewed partial suspension before World movement, a World move that lands then raises before following, and lifetime completion failure. The affected set is populated before potentially publishing fence writes, expands to all held dependents before the World move attempt, and is stamped inside `hold_all` before propagation. A failed lifetime completion with an operation token is no longer swallowed as display-only best effort. Source validation occurs before suspension for invalid/same-kind moves.

Reviewed take-world unlink followed by failed manifest persistence, owned and first-copy group publication failures, and post-yield journal sealing/readback. The required owner scopes retain the campaign hold while stamping errors. Campaign reclassification also covers later repoint failures after the move owner returns.

Independent Temp-only pytest instrumentation wrapped `revision.bump` and asserted `locks.campaign_lock(cid)._rlock._is_owned()` at every bump in these selected fault tests:

- Three World cases: partial suspension, landed World move, failed completion.
- Owned and first-copy group publication failures.
- Post-yield Members readback failure.
- Take-world manifest failure after unlink.

Result: **7 passed, 31 deselected**, confirming the tested stamps execute while their campaign locks remain owned. The World regression additionally checks both affected campaigns reject their old revision tokens. Strict unreadable-second-journal preflight and unchanged-tree tests passed in the larger run.

## Mechanical and focused verification

Configured frozen lint: `python scripts/ratchet.py ruff` passed with **1,146 findings, all at baseline**.

Frozen isolated tests:

```text
backend/tests/test_group_membership_lifecycle.py
backend/tests/test_group_membership_routes.py
backend/tests/test_journal_store.py
backend/tests/test_undo_store.py
backend/tests/test_reclassify_store.py
181 passed, 1 existing FastAPI/Starlette warning (36.69s)
```

The extra lock-probe run passed all seven selected cases. The two original counterexamples were independently rerun as disposable synthetic-store scripts, and the new alias failure was reproduced both at the store helper and typed route-handler boundaries. These counts are reviewer-run evidence, distinct from the author's reported broader suites.

## Not reviewed

Subsequent repairs (including the author's separately reported owned-update preflight correction), live UI work, final full-S1 behavior, Task 5 frontend behavior, S2-S8, browser/end-to-end behavior, full `make check`, and broad crash/performance behavior are not certified. The optional sibling `python-code-doctor/scripts/analyze_diff.py` pass was not run for this recheck; configured lint was run and passed. This artifact does not claim a physical case-sensitive-volume run. The samefile implementation was inspected, and the new failure was positively demonstrated on the native case-insensitive filesystem.
