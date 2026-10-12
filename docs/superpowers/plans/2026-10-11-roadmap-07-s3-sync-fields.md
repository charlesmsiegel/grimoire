# 07-S3 sync typed fields Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show every declared entity field in Incoming Review so a Members-only library change is reviewable.

**Architecture:** Keep the existing sync hashes and publication writers. Prepare each pending entity's two existing blobs, align declared fields across those two sides, then decorate ref values through one campaign View and a per-call name map. The UI carries raw comparison values separately from friendly display text and uses the existing two-column sections.

**Tech Stack:** Python 3.11+, current file store/FastAPI; React/TypeScript, pytest/Vitest. No dependency addition, data migration or new dataclass.

**Spec:** [07 explicit group membership](../specs/2026-10-09-roadmap-07-explicit-group-membership-design.md), section5.3, 07-S3 and17.3. S1 code gates must close before execution. User authorized all slices inline with independent brutal-review gates; do not repeat execution approval.

## Global Constraints

- Fields are declared by entity_schema.FIELDS[kind], never a Members-specific second schema.
- Each side carries one row per declared key held by either side, in declaration order: {key,label,value,shown}. value is the exact stored string; shown is display only.
- Entity fields only. Greetings, plot maps, actor cards/personas and unpicked actor summaries keep their existing shapes.
- One fresh overlay.View and at most one roster/listing per referenced kind per incoming call; pass the same View to character_roster, pc_roster and list_entities. No per-pending-record actor-body reads.
- Both sides resolve through the campaign's view. An inherited World-side actor ref detached here appends "(this campaign's own; not the library's)". Mine has no stranger suffix.
- Missing/legacy refs retain their raw spelling, including refs not resolvable in the current view. Native identity is never inferred by unconditional case folding.
- Read-only display cannot write group text, ledgers, sync bases, pins, revision or journal, or weaken S1 publication/copy guards.
- Plain Pydantic for any newly required model; no Field, validators, ConfigDict or model_dump in model definitions. Existing sync dictionaries remain the wire shape.
- UI order is Name, declared field rows, Body. Raw values determine equality; shown determines visible content. Empty on one side remains a paired empty cell.
- Once fields are delivered, the entity invisible-change hint returns to "keys, owners, or secrecy". Existing card/persona/greeting hints stay specific.
- Public placeholder fixtures only; shared LLM fakes when needed (none expected here). Frozen home and snapshot unchanged. Commit coherent increments liberally.

## Review Focus

1. A field exists only in the campaign copy or only in the World: neither removal nor addition can vanish because rowsOf starts with the World. Task1 tests aligned empty rows; Task2 tests mine-only payloads defensively.
2. Different raw refs have the same friendly display name: comparison still reports the typed change, with no identical-fields banner. Task2 pins this.
3. A World ref shares a slug/native alias with an unrelated detached campaign actor: the World cell warns about the stranger and campaign cell stays authored. Task1 pins exact/native identities and absence; case-sensitive distinct paths remain distinct.
4. Many pending entities mention the same actor: one captured View and one roster per kind, no card/persona reads, no leaked cross-campaign name map. Task1 pins repeated calls and read counts.
5. A legacy malformed field or an absent peer key: raw storage remains unchanged, unresolved display stays readable, and greeting/plotmap/card behavior stays intact. Both tasks pin these shapes.

---

## File map

- Modify backend/src/grimoire/store/sync.py: field extraction/alignment and per-call campaign display names.
- Create backend/tests/test_sync_fields.py using real isolated World/campaign records and existing placeholder cards/personas.
- Modify frontend/src/api/types.ts: optional IncomingBlob.fields and IncomingField type.
- Modify frontend/src/components/IncomingReview.tsx: raw/display row separation, union pairing, generic declared rows and hint.
- Modify frontend/src/components/IncomingReview.test.tsx; existing sync/publication suites run unchanged.
- Record gates in docs/superpowers/validation/2026-10-11-spec-07-s3-implementation.md and ROADMAP-CHECKLIST.md only after observed passes.

### Task 1: Generic typed incoming payload

**Files:** store/sync.py; tests/test_sync_fields.py.

**Interfaces:** Preserve incoming(cid: str) -> list[dict] and private _entity_blob(root: Path, kind: str, eid: str) -> dict for its only current callers in incoming. Add fields to entity blobs from meta, retaining name/body. New private helpers:

- _align_entity_fields(world: dict, mine: dict, kind: str) -> None: align both field lists to the union of held declared keys, using declaration labels and empty strings for a missing peer.
- _incoming_names(cid: str, v: overlay.View, kinds: set[str]) -> dict[str,str]: one campaign roster/list per needed kind, canonical ref -> friendly name.
- _show_incoming_fields(cid: str, items: list[dict], v: overlay.View) -> None: collect referenced kinds/raw valid actors once, resolve shown values and annotate detached World actor identities through S1 actor_detachments using v.croot/v.wroot. Values remain untouched. The call's local map is discarded afterwards.

No other caller uses _entity_blob today. incoming constructs v once, uses v roots for the entity sweep, prepares both blobs, aligns fields, then decorates only pending entity rows. Lazy View avoids new ledger/roster reads for a clean/no-entity incoming read. Preserve actor/plotmap passes and final pin filtering.

- [ ] **Step1: Write real storage regressions.** test_members_only_incoming_has_declared_rows_and_campaign_names: create Realm, Seraphine/Mara/Winifred and Salt Circle, materialize it, change World Members only, call incoming, assert Name/Body unchanged and Members values differ with friendly campaign names. Parameterize test_fields_align_declared_order_on_one_sided_values for group_type/leader/members/headquarters, location climate/persistence/weather_zone, item holder/rarity, creature habitat/threat; assert only keys held on either side appear, both lists match declaration order, missing peer value/shown are empty, stored text bytes unchanged. Include raw whitespace/order and legacy "Mara" in a ref field.

Add test_world_stranger_is_annotated_in_campaign_view: detach an actor with an owned campaign replacement, change World group to that ref, assert World shown has suffix and mine ordinary campaign name; absent marker paths still preserve exact marker warning. Add native-alias variant on a filesystem proving alias identity; skip only when native alias is genuinely absent, and retain a case-sensitive distinct-ID control where supported. Missing/unresolved refs remain raw; only valid actor refs use the actor marker rule.

Add test_one_view_and_one_roster_per_kind_for_pending_batch: several pending groups/items/creatures, wrap actual overlay.view/roster/list_entities to count and assert identical v object; forbid character card/persona reads in the display pass, assert each needed kind once and unused kinds zero. A second campaign call must resolve its own names. Add greeting/plotmap/locked-card/unpicked-summary controls proving fields absent. Snapshot group/sync/ledger/revision/journal bytes around the display read to prove no writes. Existing unchanged campaign produces no new roster reads.

- [ ] **Step2: Run RED.** `make test-py-fast PY=../../backend/.venv/Scripts/python.exe TESTS=backend/tests/test_sync_fields.py`. Expected: FAIL on missing fields/display contract, not fixture failure. Commit tests when their reproduction is coherent.
- [ ] **Step3: Implement generic extraction and decoration.** Add module-level entity_schema import. Frontmatter values stay strings, and no normalization of value occurs. Ref shown splits the descriptor's comma-separated tokens, resolves exact refs from the one name map, joins with ", ", and preserves unresolved tokens. Native alias matching may use existing samefile identity ownership; never lower/casefold keys to claim equivalence. For an alias not represented by the exact roster map, raw ref remains a truthful unresolved display; the detached suffix still uses S1 native proofs. Require both strict ledgers before a detached annotation if entity ref fields require that identity judgment; failures follow the existing GroupIdentityConflict route mapping. Use the same captured roots for that judgment. Keep hash/status/pin/accept/push/promote semantics unchanged.
- [ ] **Step4: Verify and commit.** Run new fields tests plus backend/tests/test_sync_store.py, test_sync_pins.py, test_group_membership_store.py, test_group_membership_routes.py and test_group_membership_lifecycle.py; run check-lint/check-mypy and domain/import/lock guards. Expected all pass, lint ratchets unchanged or smaller baseline committed. Commit `feat: include typed fields in incoming sync payloads`.

### Task 2: Raw comparisons and visible field rows

**Files:** api/types.ts; components/IncomingReview.tsx and its test.

**Interfaces:** Add `IncomingField = {key:string;label:string;value:string;shown:string}` and optional `IncomingBlob.fields?: IncomingField[]` for older fixtures/other blob variants. Local Row becomes `{key:string;label:string;value:string;shown:string}`; ordinary Name/Body/card/persona rows use identical value/shown. pairs returns aligned rows with raw world/mine and displayed worldShown/mineShown. Detail compares raw values for the identical banner, and Value renders the displayed strings. Typed ref/scalar cells are plain text; Body retains existing markdown, card/persona/plotmap rules.

- [ ] **Step1: Write failing UI tests.** members_only_change_renders_members_between_name_and_body: API sends equal name/body and differing raw Members/shown names, assert ordered headings, both names and no identical-fields hint. raw_ref_change_with_equal_shown_still_counts_as_change: raw characters:seraphine vs characters:mara but shown "Mara" on both, assert no identical banner. unchanged_raw_different_shown_is_not_a_stored_change: equal raw but World stranger suffix, assert identical-fields banner remains truthful about stored values, displays warning. mine_only_field_is_kept: only mine carries a populated typed field in a defensive old/mixed payload, assert row and empty World cell. Test one-sided removal/addition, zero/raw numeric strings, legacy unresolved values, no mine column, and location climate/creature habitat to forbid Members-specific branching. Ordinary cards/personas/greetings/plotmap controls remain green. Adjust existing temporary hint expectation to keys/owners/secrecy and assert typed fields no longer claimed invisible.
- [ ] **Step2: Run RED.** `npm --prefix frontend test -- src/components/IncomingReview.test.tsx`. Expected: missing field rows/raw comparison failures. No npm install while another Vite/Vitest worker owns node_modules.
- [ ] **Step3: Implement.** Add declared incoming rows after Name, before Body. Pair the union by stable keys, preserve World/declaration order and append any mine-only keys before Body; ordinary fields continue existing order. Filter an empty pair using raw OR shown on either side so a warning/display survives; compare raw values only. Render field values as plain text and retain markdown only for Body. Update why comments about hash coverage and typed delivery and remove temporary hint wording. Preserve selection/resolve/async guards.
- [ ] **Step4: Verify and commit.** Run IncomingReview tests and frontend typecheck, then check-eslint with existing lockfile install after concurrent helpers stop. Expected all pass at727 or smaller committed baseline. Commit `feat: show typed fields in Incoming Review`.
- [ ] **Step5: Slice gates and real browser.** Apply verification/browser skills with isolated store and existing cassette, drive an actual Members-only incoming change and detached stranger row, verify no invisible typed-field hint and raw-equal/raw-different behavior. Stop only owned helpers. Run complete make check with explicit worktree PY and recorded Pydantic1 Python3.11 bootstrap override. Expected exit0, frozen fixtures unchanged. Run actual installed /codex:review on S3 diff and actual final /codex:adversarial-review on diff plus originating spec for S3 coverage. Independent agent must apply $brutal-review to frozen S3 code as the user-requested gate. Resolve supported blockers with RED/GREEN regressions. Record exact commits/threads/results, short theory note and Code/Review/Final cells only after passes; Landed stays unchecked. Commit evidence separately.

## Self-review

5.3 extraction/union/order -> Task1; one View/campaign naming/stranger annotation -> Task1; Name-fields-Body, raw equality and hint -> Task2. 17.3 Members-only incoming acceptance is pinned end to end; S1 publication controls stay in Task1's suite without new writers. Every Review Focus has owning tests. Interfaces preserve dictionary wire compatibility and optional TS fields; no new dataclass/model schema duplicate. Tasks decide data contracts, not function bodies. S2 inverse, presence, proposals, graph and persistent cache remain their own plans.

**Plan status:** Actual Codex plan gate approved at c8892fe9c, thread01a12e29-527e-7053-bdb0-0be0a6b11f90. S3 implemented under the user-authorized lighter check cadence; code/final native and independent review gates remain pending.
