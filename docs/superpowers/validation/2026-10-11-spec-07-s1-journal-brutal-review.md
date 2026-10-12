# Spec 07 S1 API, Members Undo and lifetime checkpoint: independent brutal review

Date: 2026-10-11

Reviewed frozen commit: `6a75b98ae8ef250e29cc0d75810b4dba57856b6b`.
Review base: `abdb7be03cd1fb026704abf4db2b28faa8ae9a7d`.

**Verdict: blocking.** Finding 1 moves an independent campaign original and its Undo identity when an unrelated replacement World record moves. Finding 2 violates the approved same-hold failure revision contract after a landed Members edit. This is a checkpoint review, not final S1 certification. Repairs made after the frozen commit require a separate recheck.

Applied the brutal-review skill and its attack/severity references. Read repository conventions, the approved spec and S1 plan. Reviewed frozen source extracted to Temp, avoiding concurrent implementation edits. No production/test files were changed, no private store was read, and no application or real store was launched.

## Findings

### 1. Blocking: a recreated World group moves an independent detached campaign original

**Where:** `backend/src/grimoire/store/reclassify.py:214` and `:284`.

**Concrete sequence:**

1. Create World Realm, dependent campaign Saltmarch, and World group Salt Circle.
2. Save Members `characters:mara` through `undo.journalled_group_members` and `overlay.update_entity`, producing an owned original and a Members Undo offer.
3. Delete the World group through `routes.entities.delete_world_entity`. The owned original remains and its `groups/salt-circle` detachment is verified present.
4. Create a new World group at the same slug, then call `reclassify.world_entity(wid, 'groups', eid, 'lore')` on that replacement.

Observed with the frozen code: the campaign's original `groups/salt-circle.md` disappears, it moves to lore, and its persisted Members Undo target changes to `kind: lore`. The replacement World identity has moved an independent campaign record and disabled its group Undo offer.

The operation selection suspends every owned copy, and `_follow_in_campaign` follows every owned copy, without excluding detached originals. The approved Task 4 explicitly requires preserving the independent detached own copy during a World move. The pre-existing test expecting a detached copy to follow does not override that requirement.

**Disproof attempt:** A merely campaign-created same-slug record is not enough to establish this case. The reproduction uses the real World-delete lifecycle and verifies the durable detachment before recreating the World record. Thus this is not a guessed interpretation of a local file or an external edit.

**Specific fix:** Exclude detached owned originals from both relocation suspension and the entire follow/repoint operation. Keep their files, identity ledgers and Undo targets untouched. Match relevant aliases using real filesystem identity when the queried owned record exists; do not assume case folding on case-sensitive volumes. Add the deletion/recreation/move sequence, including an Undo offer, as a regression test.

### 2. Should fix: journal readback failure releases the campaign hold before failure revision stamping

**Where:** `backend/src/grimoire/store/undo.py:394-402`; caller `backend/src/grimoire/routes/entities.py:232`.

**Concrete fault:** Create an owned Salt Circle group. Record `before = revision.current(cid)`. Inject `PermissionError` from `entities.read_entity` only when the already-published owned record has Members `characters:mara`. Submit those Members through `_campaign_entity_update`.

The update publishes the new bytes successfully. The specialized journal context then tries to read back the owned target and raises `UndoConflict`. After the route helper has released its hold, a new `locks.campaign_lock(cid)` can read the durable Members, observe `revision.current(cid) == before`, and successfully run `revision.require(cid, before)`.

This is the failure boundary the approved Task 4 contract excludes: an effect has landed, but a competing revision-guarded operation can enter under the old token before failure handling invalidates it. `overlay.update_entity` protects its own publication span, but that span has returned before the new journal scope/seal/readback operations fail.

**Important qualification / disproof attempt:** The application middleware catches unhandled exceptions and eventually bumps the campaign revision (`main.py:507-536`). Therefore this reproduction does **not** establish that a completed HTTP 500 leaves the revision permanently unchanged. It establishes that the store owner releases the campaign hold without the required stamp; middleware stamps later, outside that hold. A deterministic isolated reproduction reacquires the next campaign hold and accepts the stale precondition at exactly that boundary.

**Specific fix:** Wrap the fallible post-yield scope validation, target sealing and Members readback in `revision.stamp_on_failure(cid)` inside the journal context's existing campaign hold. Preserve the no-stamp behavior of initial strict preflight refusals and the existing best-effort manual journal append contract. Pin a readback failure after durable publication, asserting the revision changes before the owner releases its hold.

## Verification and scope

Frozen configured mechanical pass: `python scripts/ratchet.py ruff` passed, 1,146 findings at the existing baseline; no new lint delta.

Frozen focused tests:

```text
backend/tests/test_group_membership_routes.py
backend/tests/test_group_membership_lifecycle.py
backend/tests/test_journal_store.py
backend/tests/test_undo_store.py
119 passed, 1 existing FastAPI/Starlette warning (28.40s)
```

Tests used the existing main-checkout Python environment against frozen Temp sources, isolated synthetic home, and disabled pytest cache. Both independent counterexamples were rerun in disposable Temp stores. Their output was:

```text
R1 group survives: False
R1 journal target: lore
R2 durable members: characters:mara
R2 revision unchanged at released-lock boundary: True
R2 stale precondition accepted under next hold: True
```

Reviewed API validation and group-only binding acceptance, same-source GET context, locked PUT revision checking, inherited before/owned after journaling, exact original Members consent, owned-only CAS restoration, inverse metadata preservation, strict journal preflight, retirement on delete/take-world/pruning, relocation suspension/completion, campaign and World movement callers, and response/middleware exception behavior. Existing tests cover native alias ownership, original-actor revalidation, stale CAS, missing owned records, malformed/suspended journal targets, and fence failures. No additional defect is asserted for those paths.

## Known unfinished work and Not reviewed

World workflow failure stamping was explicitly unfinished at this frozen checkpoint, including failures during suspension before the ordinary per-campaign follow loop. It remains required work and is not treated as a newly discovered checkpoint defect or as passing. Finding 2 concerns the separately implemented campaign Members journal owner.

Not reviewed: subsequent repairs or live uncommitted changes; final full-S1 behavior; Task 5 frontend work; S2-S8; browser behavior; a full `make check` run; load/performance and crash-recovery experiments beyond the isolated fault cases above. The optional sibling `python-code-doctor/scripts/analyze_diff.py` pass was not run for this checkpoint; the configured lint pass did run and passed. No new policy/spec amendment is needed for either finding: the approved Task 4 already states both requirements.
