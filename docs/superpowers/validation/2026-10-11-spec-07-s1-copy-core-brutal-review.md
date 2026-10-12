# Spec 07 S1 copy-core independent brutal review

Reviewed commit: `938d2d4b57cb3b5f92084a53b50f005269d7eb01`.
Base: `f816921c8d2bd91628cfe12e28bda9106a254f11`.
Date: 2026-10-11.

Verdict: **blocking**, driven by findings 1 and 2 below. This verdict covers completed Task 2 and the implemented Task 3 core only. It is not the final full-S1 review gate.

## 1. Native filesystem aliases bypass detached-actor consent and publication checks

**Severity:** Blocking.

**Where:** `backend/src/grimoire/store/overlay.py:727`, `:880`; `backend/src/grimoire/store/sync.py:837`, at the reviewed commit. The shared pure filter in `group_refs.py` also compares the supplied slash refs exactly, so its caller must supply the native identity result rather than the unchanged raw ledger alone.

**Concrete sequence, reproduced on this Windows filesystem:**

1. Create World Realm and campaign Saltmarch.
2. `overlay.create_character(cid, "Mara")` creates a campaign-owned actor and the marker `characters/mara`.
3. Create a different World actor named Mara with `characters.create_character(wroot, "Mara")`.
4. Create a World group Salt Circle with `members: characters:MARA`.
5. Read its membership context, then perform a body-only campaign edit with no binding consent.

The actor paths `characters/MARA/character.md` and `characters/mara/character.md` are the same file on this native filesystem (`samefile` returned `True`). But the detachment check compares `characters/MARA` to `characters/mara` as case-sensitive strings. The context reports the member effective, with **no filtered or unresolved member**, and the body edit publishes a campaign copy. In that campaign, `overlay.actor_root(cid, "characters", "MARA")` resolves to the unrelated campaign-owned Mara. A subsequent `sync.push(cid, "groups", gid, force=True)` also succeeds, because publication repeats the same exact-string detachment test.

**Positive reachability evidence:** `entity_schema.invalid_values("groups", {"members": "characters:MARA"})` returned `[]`. Calling the existing campaign route helper `_campaign_entity_update(cid, "groups", gid, EntityUpdate(body="Winifred keeps the roster."))` returned `{"ok": True}` and the owned group file existed. This was not a malformed field passed around the API validator, a fabricated detachment state, or a simulated case-insensitive policy: ordinary actor creation wrote the marker, and the native filesystem established the alias. No World actor existed when the campaign actor was created, so the two actors are actually distinct.

**Consequence:** An otherwise valid stored ref silently binds a World group's membership to a different campaign actor on a supported platform. The inherited diagnostics, first-copy guard and World publication guard all fail together. The same comparison governs Leader and dormant actor fields; those paths require the same correction.

**Attempt to disprove:** Confirmed that `referenceable` does not lowercase or reject uppercase ids; actual actor resolution follows the filesystem; campaign creation's slug allocation does not help because the World actor is created later; and the ordinary field validator and route helper accept the case. Existing tests exercise lowercase refs only. On a case-sensitive volume, differently-cased existing ids can be distinct, so globally lowercasing is not a valid repair.

**Specific fix:** Introduce one native actor-slot identity matcher at the I/O boundary. Preserve authored ref spelling and exact per-field consent. Match exact ledger refs first, then compare relevant same-kind actor paths using actual filesystem identity (for example `samefile`, as World-reference handling already does), in the relevant campaign/World roots. For the one group's queried actor refs, derive the equivalent detached slash refs and pass that set to the pure effective filter; use the same identity decision for unresolved-copy and publication checks. This needs identity/stat probes for the relevant refs, not actor body reads, a whole-roster scan, or the inverse index. Keep the missing-World rule conservative when no actor exists to establish identity. Test the native alias case and a case-sensitive policy where two distinct existing ids must remain distinct. Do not use `casefold`, `normcase`, or Windows `Path` equality alone as proof of file identity.

This repairs the implementation's filesystem identity mismatch without changing the spec's authored spelling or per-slot consent policy. A broader decision to canonicalize/rewrite every stored actor ref, or make actor ids globally case-insensitive even on case-sensitive filesystems, would require a separate policy/spec decision and is not proposed here.

## 2. World reclassification discovers corrupt identity only after moving the World record

**Severity:** Blocking.

**Where:** `backend/src/grimoire/store/reclassify.py:202`, reached before the newly strict calls in `overlay.reclassify_entity` / `overlay.repoint_record`.

**Concrete sequence, reproduced against frozen source:** Create Salt Circle in the World, materialize its group in Saltmarch, then replace that campaign's `detached.json` with truncated JSON (`{`). Call:

```python
reclassify.world_entity(wid, "groups", gid, "lore")
```

It raises `GroupIdentityConflict`, but the World source group is already absent and the World lore destination already exists; the campaign still holds its old group. No Members or Leader field is needed to trigger the defect.

**Consequence:** A predictable strict-ledger refusal reports failure after a durable World move, leaves the campaign at the old kind, and a normal retry addresses a source that no longer exists. The existing catch excludes the new RuntimeError-derived conflict, so it also aborts the remaining campaign sweep. This is an associated-effect preflight failure, not the separate unfinished Task 4 lifetime-fence work.

**Attempt to disprove:** `world_entity` already holds all dependent campaign locks, but makes no strict ledger preflight before `entities.reclassify`. The lock cannot prevent corrupt bytes already on disk. The route reaches this store operation and no prior route check reads every dependent ledger. The author confirmed that strict World-move preflight belongs to completed Task 2; future journal suspension/retirement is a different requirement.

**Specific fix:** Inside the existing sorted `hold_all(cids)`, strictly preflight both ledgers for every dependent before `entities.reclassify` or any World/ref/copy effect. Add a two-dependent case with the second ledger corrupt and assert both campaigns, World records/assets and manifests remain byte-identical, with no revision stamp on this no-effect refusal. Keep later Task 4 journal/lifetime fences distinct.

## Evidence and scope

All production analysis and executions for this review used a `git archive` extraction of the exact reviewed commit into a Temp directory; no result is attributed to the primary agent's later edits. Mandatory repository conventions and the brutal-review skill/rubrics were retained from the preceding foundation review. The approved plan, originating spec and execution record were consulted for this checkpoint's boundaries.

Mechanical/verification results:

- Configured `python scripts/ratchet.py ruff` on frozen sources: **passed, 1146 findings all at baseline**.
- Frozen `test_group_membership_store.py` and `test_membership.py`: **149 passed**, one existing FastAPI/Starlette deprecation warning.
- Frozen `test_library_move.py`, `test_reclassify_store.py`, `test_lock_order_guard.py` and `test_import_guard.py`: **162 passed**, the same existing warning.
- Total selected existing tests: **311 passed**. Both substantive failing cases above were independent disposable-store reproductions, outside the existing green tests.
- The optional sibling `python-code-doctor/scripts/analyze_diff.py` is installed but **not run for this checkpoint**. Its previous foundation run is not evidence about this commit. Configured repository lint was the mechanical pass here.

Every execution set `PYTHONPATH=backend/src` to the frozen source tree. Test/reproduction homes were disposable Temp paths; no private store was opened. No app server, external model, production file or test source was changed. The only repository write by this reviewer is this artifact.

Attack coverage included raw parsing and retained exact slots, first-copy publication versus source hashing, missing/detached World actors, native case aliases, owned-context exemptions, dormant Leader/Members, force publication, preflight-before-assets ordering, migration/reattachment ledger reads, all-dependent World delete/demote holds, World reclassification callers, plain Pydantic same-read context, and existing library-move compatibility. The prior invalid-repoint finding is fixed in this commit: both refs are validated before manifest/ledger effects.

Rejected candidates included treating documented multi-file failures as transactions, assigning missing future APIs/UI to this core, assuming normalized filesystem case without evidence, and calling new authored slots retained inherited ones merely because their field changed. Prepared-copy revision/hash and final text are derived from the selected source; existing tests establish that no unsafe intermediate copy is published on the tested clear path.

## Not reviewed

The remaining Task 3 collateral sweep/all-dependent source and concurrency checks, Task 4 API/Undo/journal/lifetime fences, Task 5 editor consent, and S2-S8 are explicitly unfinished and their absence is not a finding here. Findings above are defects in the implemented identity and completed strict-preflight boundaries, not requests to pull those future slices forward.

Full `make check`, frontend/Vitest/browser verification, mypy/eslint, Pydantic 1, Android packaging and broad concurrency/fault-injection campaigns were not run. Native Windows behavior was tested; a real case-sensitive volume was not available in this review. No performance benchmark or full actor-alias policy audit was conducted. The final full-S1 independent code review and diff-versus-spec gate remain required after implementation and supported findings are resolved.
