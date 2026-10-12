# 07-S4 member presence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A present actor makes its affiliated group structurally present, with an honest inspector reason.

**Architecture:** Extend the existing ordered structural rule table and fixed point. World-info capture consumes group metadata/body and the S2 effective roster from one coupled campaign snapshot; other structural fields and actor knowledge retain their current owners. The inspector renders the new presence reason through its existing vocabulary.

**Tech Stack:** Python 3.11+, file store and pytest; TypeScript/Vitest. No dependency or feature switch.

**Spec:** [07 explicit group membership](../specs/2026-10-09-roadmap-07-explicit-group-membership-design.md), section11, 07-S4 and17.6. Hard dependency: completed07-S2 inverse interfaces. User authorized all slices inline and independent brutal-review gates.

## Global Constraints

- Group structural order: leader/led_by/True, members/member_present/True, headquarters/headquarters/False.
- For groups, Leader and Members come from membership.campaign_snapshot(cid).index, not entity_schema.parse_refs. Group secrecy, keys, controls and body come from that same snapshot.records. One inverse per _world_info call; no actor-body reads or actor frontmatter rewrite.
- Invalid/non-actor group refs and detached inherited actor identities confer no presence. Owned authored refs follow the S2 roster contract.
- A present member opens owner gates only: matching keys, timing/cooldown and budget still apply. Keyless owned lore follows existing rules.
- A group does not activate its own entry or pull its group-state block merely through structural presence.
- Fixed-point cascade through an item held by the group is intentional. Recall eligibility reads that same present set; no new recall strategy or LLM fake.
- GM-only/excluded groups confer nothing under the existing activation.run filtering. Structural presence does not grant actor knowledge.
- No switch: inert without recorded members; valid, noncolliding legacy Leaders retain old behavior. Invalid/detached Leaders lose accidental presence intentionally.
- Golden lore and frozen campaign snapshot/home remain unchanged. Plain Pydantic for any new model; no new dataclass, schema duplication or model validators.
- Commit coherent increments liberally; explicit worktree PY, ratchets and complete gate evidence required.

## Review Focus

1. Leader and a different member both present: led_by wins; member beats headquarters. Task1 pins order with all three candidates.
2. Hand-written members:groups:other or leader:items:lantern: a present object/group cannot become an affiliate back door. Task1 uses actual file capture, not only pure refs.
3. An inherited Winifred ref collides with detached campaign Winifred: it grants no group presence while an authored owned group does. Task1 covers both layers/native identity where available.
4. Group-owned lore and group-held item lore: presence opens both gates but does not insert group prose/state or bypass missing keys/cooldown. Task1 checks actual assembly and unchanged actor knows tables.
5. A group hidden by secrecy/exclusion or inverse ledger corruption: no accidental presence or stale fallback; Task1 tests hidden variants and propagated strict failure. Task2 tests friendly and absent via labels.

---

## File map

- Modify backend/src/grimoire/store/context/activation.py: one ordered rule row and why docstring.
- Modify backend/src/grimoire/store/context/world_state.py: members in _STRUCTURAL_FIELDS and group roster-backed structural refs.
- Extend membership.py with plain Pydantic GroupRecord(id,meta,body) and GroupSnapshot(index,records), plus campaign_snapshot(cid). The private memo value retains parsed records beside its inverse from the identical GroupCapture. Add optional include_body to group_inventory.rows/overlay.group_rows; ordinary listing contracts stay unchanged. Add focused captured-snapshot tests.
- Extend backend/tests/test_lore_activation.py and test_lore_activation_context.py; add focused backend/tests/test_group_member_presence.py for actual stored malformed/detached refs and knowledge controls.
- Modify frontend/src/api/types.ts OwnerPresence union and frontend/src/components/loreReasons.ts describePresence; extend loreReasons.test.ts.
- Record observed gates in validation/2026-10-11-spec-07-s4-implementation.md and ROADMAP-CHECKLIST.md; Landed remains unchecked.

### Task 1: Effective member presence through the existing engine

**Files:** activation.py; world_state.py; membership.py; group_inventory.py; overlay.py; three backend test modules above and test_membership_snapshots.py.

**Interfaces:** Add plain GroupRecord(BaseModel) with id:str, meta:dict[str,str], body:str; GroupSnapshot(BaseModel) with index:Index and records:tuple[GroupRecord,...]. membership.campaign_snapshot(cid)->GroupSnapshot returns a deep copy of one memo value containing both records and Index produced from the SAME GroupCapture. Existing campaign_index/world_index results/signatures and Index fields stay unchanged. World-info uses snapshot.records for group enumeration AND body/meta; it never re-reads those groups through ordinary entity listing/read. Each record has authoritative filename id and actual metadata only: private layer/body/id provenance is not inserted into frontmatter. Index.groups["groups:<id>"] supplies effective leader/members. Preserve _world_info(...) return shape and activation signatures. Existing entry refs remain lists/tuples of strings.

- [ ] **Step1: Write RED tests.** In engine tests, add member_present_opens_group_owner_gate with Mara present and keyed group-owned ledger lore, exact owner_presence {type:"member_present",via:"characters:mara"}; absent affiliate gives no hit. Parameterize leader_then_member_then_headquarters priority. Add member_group_item_cascade: group -> held item -> keyed item-owned lore; recall candidate spy confirms group/item owner eligibility. GM-only/excluded group variants stay absent.

In real store tests, use Realm/Saltmarch/Seraphine/Mara/Winifred/Salt Circle and the existing scene patterns. stored_members_reach_context reads actual S2 roster into assembly and inspects the member reason, group lore prompt, absent unrelated group prose/state and no body writes. Parameterize malformed/non-actor Members/Leader including groups:other (use an existing placeholder group fixture name), legacy prose and multiple Leader tokens; a group key activation cannot turn a non-actor raw member into presence. Detached inherited Winifred vs owned authored Winifred controls use actual S1 lifecycle APIs and native alias variants where supported. Wrap actual campaign_snapshot to prove one inverse snapshot call per _world_info; poison/corrupt strict ledger after a warm S2 hit and assert GroupIdentityConflict propagates. Test member no keys, keyed miss and cooldown cases; compare actor_scope.knows/known_entries for public/secret/owned combinations to unchanged expectations. No new inline LLM fake.

Add the native plan finding regression: capture a gm-only Salt Circle with Mara listed, then atomically change it to public with no Members before the consumer would ordinarily re-read the group. The returned snapshot must keep body, secrecy, keys, controls and effective refs from one captured version; neither mixed version can grant group presence. Pin no ordinary group read/list after snapshot, warm stat-preserving freshness, ledger refusal and deep-copy ownership of nested record metadata. Store the raw parsed group records alongside Index in the private memo, preserving coupled roots/digests/native proofs and the actual-returned-wrapper check; do not create a second pool. Body edits can rebuild this companion while Index.digest stays equal.

- [ ] **Step2: Run RED.** `make test-py-fast PY=../../backend/.venv/Scripts/python.exe TESTS=backend/tests/test_group_member_presence.py`. Run new engine/context nodes too. Expected: missing member presence or raw malformed Leader behavior failures, not setup errors. Commit coherent reproductions.
- [ ] **Step3: Implement.** Add module-level membership import to world_state; include members in structural-field names. Build one campaign_snapshot when collecting groups. Enumerate groups from its captured GroupRecords and construct their e[meta]/body directly from those records, with no second group read. Other kinds keep their current owner reads. For each groups entry overwrite parsed leader/members refs with the snapshot.index effective Roster values; a missing roster means empty effective actor refs rather than raw fallback. Headquarters stays parsed as today. Insert members row between Leader and headquarters. Keep activation.run's GM-only/exclusion filters, fixed point and candidate knowledge unchanged; preserve extensive why comments.
- [ ] **Step4: Verify and commit.** Run group_member_presence, lore_activation, lore_activation_context, actor_context, lore_golden and frozen_campaign suites plus import/domain guards and check-lint/check-mypy. Expected all pass, golden/frozen files unchanged, ratchets no increase. Commit `feat: derive group presence from effective members`.

### Task 2: Inspector reason and complete slice gate

**Files:** api/types.ts; loreReasons.ts; loreReasons.test.ts; validation/checklist.

**Interfaces:** Extend OwnerPresence.type with "member_present". describePresence uses `via ? "member here: <friendly via>" : "member here"`; via resolution remains the captured names map with raw-ref fallback. No new UI component or state.

- [ ] **Step1: Write RED test.** Add a table row for member_present via characters:mara -> owner present text containing "member here: Mara". Add null-via -> "member here" and missing-name -> raw characters:winifred, keeping other presence rows unchanged.
- [ ] **Step2: Run RED.** `npm --prefix frontend test -- src/components/loreReasons.test.ts`. Expected missing member reason text.
- [ ] **Step3: Implement union/case.** Add exact vocabulary and preserve old reason fallbacks. Update why comments to mention effective affiliated actor presence; no inferred knows statement.
- [ ] **Step4: Verify and commit.** Run loreReasons tests/typecheck and check-eslint after lockfile dependencies are settled. Expected passing with unchanged or reduced baseline. Commit `feat: explain member presence in lore inspector`.
- [ ] **Step5: Gates.** Complete make check with explicit worktree PY and recorded Python3.11 Pydantic1 bootstrap override; golden/frozen files unchanged. Isolated browser verification checks actual inspector member reason while group prose/state remains absent unless keyed. Run actual /codex:review on S4 diff, actual final /codex:adversarial-review on diff+originating spec for S4 coverage, and independent-agent $brutal-review on frozen source. Fix supported blockers with RED/GREEN evidence, record exact commits/threads/results and a short theory note. Mark Code/Review/Final only after passes, leave Landed unchecked, commit docs separately.

## Self-review

11.1/ordered row and index-backed Leader/Members -> Task1;11.2 gates/cascade/recall/knows/state/exclusion -> Task1;11.3 union/reason -> Task2.17.6 golden/frozen invariants run unchanged. Every Review Focus has owning tests. No new dataclass or behavior in unrelated actor knowledge. The one inverse-per-call boundary is defined, and missing rosters do not resurrect raw invalid refs.

**Plan status:** Native review at bbf74decc (thread01a12f2f-f4b3-7b83-b5e1-ee2bf5b2668e) blocked independent index/body reads on a concrete secrecy/Members interleaving. This amendment couples records and inverse in one memo value; actual recheck required before S4 implementation. User explicitly authorized lighter focused checks and one complete combined branch gate; native/independent reviews remain required.

Actual amended plan gate APPROVED at7982fca79, persisted thread01a12f2f-f4b3-7b83-b5e1-ee2bf5b2668e. Coupled snapshot resolves the earlier visibility/refs race.
