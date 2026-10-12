# 07-S2 inverse membership Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Derive a fresh, serializable inverse of authoritative group rosters and show it on actor pages in their current scope.

**Architecture:** Plain Pydantic models hold the inverse; the group file remains its only authority. Capture group bytes and pre-read stamps through the owning entity/overlay boundaries, then couple the parsed rows, ledger digest and content fingerprint to that capture. A private root-keyed 64-entry memo saves parsing/inverse construction after current strict ledger and content checks; read routes and one shared actor-page component consume isolated model snapshots.

**Tech Stack:** Python 3.11+, Pydantic v1/v2, FastAPI, pytest; React/TypeScript and Vitest. Existing file store, statcache, overlay, route composition and ColumnSection. No dependency addition.

**Spec:** [07 explicit group membership](../specs/2026-10-09-roadmap-07-explicit-group-membership-design.md), S2 scope: 6.2, 7.1-7.3, 7.5-7.6, 12.1 and S2 acceptance in 17.2/17.8. S1 copy/context/lifetime behavior is already implemented; S1 final code review gates must close before this plan is executed. S1's full repository gate may run concurrently in an immutable validation worktree; S1 completion stays pending until it passes. User authorized all slices inline with an independent brutal-review gate; do not repeat execution approval.

## Global Constraints

- Plain Pydantic models, not dataclasses; no Field, validators or ConfigDict; route serialization through routes.common._dump.
- max_entries = 64; one root-keyed memo entry per world/campaign, never the shared statcache pool.
- Keys: ("membership", "world", os.fspath(wroot)) and ("membership", "campaign", os.fspath(croot), os.fspath(wroot)). Resolve absolute native roots.
- Refs are reported as stored, existence unchecked. No actor-body or full actor-roster reads in the inverse or its digest.
- Group fields remain authoritative; no actor frontmatter rewrite, data migration, origin metadata or frozen-fixture regeneration.
- A body, keys or group-state edit does not move the inverse digest. Every Roster field and member order does.
- Strict whole-ledger errors raise GroupIdentityConflict even on warm hits with unchanged stats. Never stale/empty/partial fallback.
- Return copy.deepcopy of memo models on every path, including a miss; nested mutation must not affect later calls.
- Group input bytes, layer/ref, collection membership and ledger sets must be coupled to their own rows. Capture stamps BEFORE reads; absence is vouched for by the existing parent. Unvouchable inputs return stamps=None.
- On preflight/compute digest or root mismatch, return the fresh computation uncached. No retry loop and no later digest certifying earlier rows.
- Groups routes precede the generic entity router. Actor page chips use sectionHref and stay in their current scope.
- Placeholder fixtures only: Seraphine, Mara, Winifred, Realm, Saltmarch, Salt Circle and The counting-house. All store tests isolated.
- Preserve why/history comments, AST guard boundaries and lint ratchets. Commit coherent docs/test/code increments liberally.

## Review Focus

1. An old same-size source edit with its complete stat signature restored changes Members/secrecy: the next warm hit sees the new value and framing. Task 2 pins this with mocked preserved ctime as needed.
2. A warm memo and a corrupt/unreadable ledger with unchanged stats: projection raises before delivering any affiliations; repair permits a fresh answer. Task 2 pins both ledgers and native read failures.
3. Identical cids in different stores, or campaign.md switching worlds mid-read: never publish the new value under the old roots. Task 2 pins root isolation and preflight/compute races.
4. Inherited raw refs colliding with detached native aliases: bulk results agree with S1's one-group effective rule while owned refs stay authored. Task 2 pins case-sensitive and actual native alias identity.
5. A delayed actor membership response followed by scope/actor navigation: old chips never appear or navigate in the new scope. Task 4 pins stale success and failure, plus empty and leader-only sections.

---

## File map

- Modify backend/src/grimoire/store/membership.py: models, pure derivations, visibility and private memo ownership.
- Create backend/src/grimoire/store/group_inventory.py: narrow read-only group-byte capture and its plain Pydantic provenance models. It imports paths/statcache/frontmatter utilities as needed, never overlay, entities or membership.
- Modify backend/src/grimoire/store/entities.py: World group capture/listing entry points reusing ordinary group parsing conventions.
- Modify backend/src/grimoire/store/overlay.py: fresh campaign group capture/listing, strict ledger/selection/filter ownership.
- Modify backend/src/grimoire/store/__init__.py only if its existing membership module binding needs updating; no leaf-function import.
- Create backend/src/grimoire/routes/membership.py; modify routes/models.py and routes/__init__.py for typed responses and ordering.
- Modify frontend/src/api/types.ts and client.ts for typed membership reads.
- Create frontend/src/components/ActorGroups.tsx and ActorGroups.test.tsx; modify routes/CharacterPage.tsx and PCPage.tsx plus their tests.
- Create backend/tests/test_membership.py, test_membership_cache.py and test_membership_routes.py; extend test_route_order.py.

### Task 1: Pure Pydantic inverse and visibility

**Files:** store/membership.py; tests/test_membership.py.

**Interfaces:** Preserve S1 parse_members, parse_leader and edited_line exports. Add:

```python
class Roster(BaseModel):
    ref: str
    name: str
    secrecy: str
    group_type: str
    leader: str | None
    members: tuple[str, ...]
    headquarters: str | None

class Affiliation(BaseModel):
    group: str
    roles: tuple[str, ...]

class Index(BaseModel):
    groups: dict[str, Roster]
    by_actor: dict[str, tuple[Affiliation, ...]]
    digest: str

def index_from_rows(rows: Iterable[Mapping[str, object]]) -> Index: ...
def groups_for(index: Index, actor: str) -> tuple[Affiliation, ...]: ...
def members_of(index: Index, group: str) -> tuple[str, ...]: ...
def affiliated(index: Index, group: str) -> tuple[str, ...]: ...
def co_affiliates(index: Index, actor: str) -> dict[str, tuple[str, ...]]: ...
def prompt_visible(roster: Roster, *, secret_framed: bool = False) -> bool: ...
```

- [ ] **Step 1: Write pure derivation tests.** `test_inverse_roles_order_and_dangling_refs`: Salt Circle leader characters:seraphine; Members characters:mara, pcs:winifred, characters:seraphine gives Seraphine roles (leader, member), others member; leader-first affiliated order is Seraphine, Mara, Winifred. `test_inverse_lenient_metadata`: duplicates dedup, malformed members ignored, first valid leader/headquarters retained, name falls back to id, normalize secrecy, missing type becomes empty. Groups and by_actor sort by ref; affiliation tuples sort by group. Unknown actor/group answers empty. `test_co_affiliates_excludes_self_and_sorts_groups` uses shared membership and independent leader inclusion.
- [ ] **Step 2: Run RED.** `make test-py-fast PY=../../backend/.venv/Scripts/python.exe TESTS=backend/tests/test_membership.py`; missing Index/functions must fail.
- [ ] **Step 3: Implement the exact interfaces.** Reuse group_refs parsing/effective primitives and entity_schema.referenceable for headquarters. Build roles independently (leader first). Digest SHA-256 over canonical JSON, UTF-8, ensure_ascii=False, compact separators, containing [(ref,name,secrecy,group_type,leader,members,headquarters)] in ref order. No I/O, time, actor existence or second roster authority. prompt_visible rejects GM_ONLY always and SECRET unless secret_framed, otherwise true.
- [ ] **Step 4: Add and run digest/model tests.** `test_digest_covers_every_roster_field_and_member_order`, `test_digest_ignores_body_keys_tokens_and_state_metadata`, `test_plain_pydantic_serializes_nested_inverse`, `test_prompt_visible_never_exposes_gm_only` (secret framing parameterized). Expected serialization through _dump matches spec JSON keys/lists on both Pydantic versions. Run membership tests plus test_entity_schema.py and test_pydantic_guard.py; all pass.
- [ ] **Step 5: Commit the coherent pure inverse.** Commit before proceeding to cache/I/O.

### Task 2: Coupled overlay reads and bounded memo

**Files:** group_inventory.py, entities.py, overlay.py, membership.py; tests/test_membership_cache.py and test_membership.py.

**Interfaces:** Internal plain Pydantic GroupSource carries actual file id, path, layer Literal[world,campaign], and exact raw bytes. GroupCapture carries sources, pre-read stamps or None, resolved roots, the complete strict deleted/detached sets, ledger_digest and groups_digest. These are private provenance, never frontmatter or route output. The leaf captures groups/*.md in stable file order with safe-id filtering; no actor files/state/assets and no token encoding merely to construct the inverse.

```python
# entities.py: actual signatures; World readers need no campaign ledger.
def group_capture(wroot: Path) -> group_inventory.GroupCapture: ...
def group_listing(wroot: Path) -> tuple[list[dict], tuple[statcache.Stamp, ...] | None, str]: ...
# overlay.py: no caller View parameter. Every call creates a fresh View.
def group_capture(cid: str) -> group_inventory.GroupCapture: ...
def group_listing(cid: str) -> tuple[list[dict], tuple[statcache.Stamp, ...] | None,
    tuple[Path, Path], str, str]: ...
# membership.py public API; no v parameter.
def world_index(wroot: Path) -> Index: ...
def campaign_index(cid: str) -> Index: ...
```

Captured bytes parse to ordinary frontmatter summaries using the same parser and error policy as list_entities, with source-file id authoritative. Campaign capture strictly reads both ledgers before selecting effective groups; owned rows override World rows, literal/native marker semantics match existing overlay, and inherited actor refs filter through shared group_refs.effective_refs plus overlay.actor_detachments native identity proofs. Owned rows remain authored. Both Members and Leader use the same effective rule as S1.

Memo value is a private plain Pydantic wrapper of Index, coupled roots/ledger_digest/groups_digest, and raw inherited actor refs needed to recheck native marker identity without actor bodies or parsing group text on a hit. Its native-detachment signature is rechecked before serving a hit, so native alias identity changes cannot reuse a contrary effective projection. This remains one bounded scope entry, not an actor roster cache.

- [ ] **Step 1: Write layer/detachment/cache tests.** `test_campaign_inverse_follows_inherit_copy_tombstone`; `test_owned_group_refs_remain_authored`; `test_inherited_leader_and_members_share_s1_native_detachment_rule`; `test_world_inverse_never_reads_actor_records`; `test_empty_collection_uses_root_stamp`; `test_first_copy_creates_groups_directory_and_invalidates`; `test_private_memo_is_bounded_and_returns_deep_copies` exercises first miss and aged hits, including nested roles/rosters/dicts. Assert no store writes/campaign locks by these readers.
- [ ] **Step 2: Run RED.** Run both membership files and test_membership_cache.py; missing group_listing/index entry points fail.
- [ ] **Step 3: Implement capture and listing at their owners.** Pre-read campaign root/campaign.md before world resolution; strict-ledger stamps before read and absence parent; each groups directory before listing or its parent when absent; each file before bytes. group content fingerprint SHA-256 includes every exact effective source byte string, layer/ref and collection membership; ledger digest canonicalizes the exact sets used for those rows. Use raw bytes for fingerprint, universal newline normalization only before frontmatter parsing. An unvouchable input makes stamps=None; ordinary group parsing/read failure propagates and never produces a partial answer. Bind fresh View's strict frozen sets once, and never pair a reused View with new stamps. Expose group_listing's exact tuple contracts for consumers.
- [ ] **Step 4: Implement root-keyed memo through statcache.memo_stamped.** Current capture supplies strict availability/content preflight. Before a potential hit compare coupled digests and native marker proofs; invalidate stale entry, never overwrite provenance on cached rows. On miss capture fresh inputs, parse/filter once and build index; compare fresh capture roots/digests with preflight; any mismatch returns uncached. Validate the ACTUAL wrapper returned by memo_stamped against this call's preflight roots, ledger/content digests and current native-detachment signature too: memo_stamped performs its own pool lookup and another reader can insert after our earlier inspection. A mismatching returned wrapper triggers exactly one fresh uncached computation, with no later digest attached to its rows and no retry loop. Preserve pre-read stamps; max_entries64; deep-copy every result. No repeated retry and no shared pool. Hits still read/hash bytes but reuse parsed model/inverse construction. Body edits may rebuild, while the inverse digest stays equal.
- [ ] **Step 5: Write and run freshness adversaries.** `test_preserved_stat_members_and_secrecy_edits_are_fresh` (World and campaign; monkeypatch stamp's complete signature to its saved value, use real same-size byte edits); `test_warm_hit_strictly_rejects_unreadable_ledgers` for both ledgers, malformed/wrong types/invalid refs/decoding/access failures; `test_ledger_digest_coupled_to_rows_and_no_retry_on_race`; `test_capture_root_switch_returns_uncached`; `test_preflight_compute_content_mismatch_cannot_certify_old_rows`; `test_concurrent_cache_insertion_cannot_bypass_preflight` pauses reader B before publication, restores source A with every stat field preserved, lets reader A preflight an empty pool, publishes B, then releases A into memo_stamped; A must return one fresh uncached A computation, never B, and later reads agree. Repeat for Members and secrecy and assert the returned wrapper's own provenance. `test_same_cid_two_store_roots_do_not_share_results`; `test_external_group_write_changes_answer`; `test_group_state_and_body_do_not_move_digest`; `test_native_alias_filter_rechecks_without_actor_body_reads`. Repair retains unrelated markers, and valid duplicate/non-actor/asset/library ledgers work. Verify next successive reads converge after each deliberate race.
- [ ] **Step 6: Verify and commit.** Run membership suites, test_overlay_view.py, test_entities_store.py, test_statcache.py, test_overlay_guard.py, test_import_guard.py, test_paths_guard.py, test_pydantic_guard.py; lint/mypy ratchets pass without expansion. Commit the independently usable fresh inverse before routes/UI.

### Task 3: Typed read API and ordering

**Files:** routes/membership.py, routes/models.py, routes/__init__.py, tests/test_membership_routes.py and test_route_order.py; frontend api/types.ts and client.ts.

**Interfaces:**

```python
class MembershipResponse(BaseModel):
    digest: str
    groups: tuple[membership.Roster, ...]
    by_actor: dict[str, tuple[membership.Affiliation, ...]]
class ActorGroup(BaseModel):
    ref: str
    name: str
    secrecy: str
    roles: tuple[str, ...]
class ActorMembershipResponse(BaseModel):
    actor: str
    groups: tuple[ActorGroup, ...]
```

GET /api/worlds/{wid}/membership and /api/campaigns/{cid}/membership declare union response models. No actor argument returns the full sorted roster tuple + by_actor/digest; actor argument returns only matching groups' ref/name/secrecy/roles. Validate using group_refs.actor_ref; groups:x, malformed or unreferenceable actor gives400. Dangling well-formed actor returns recorded affiliations without existence I/O. Root-or404 guards preserve unknown scope404. GroupIdentityConflict uses existing409 wire response. Register the new router before entities catch-alls through _compose. TypeScript API `api.getMembership(scope: EntityScope): Promise<MembershipResponse>` and `api.getActorMembership(scope: EntityScope, actor: string): Promise<ActorMembershipResponse>` use encoded query refs and scope paths.

- [ ] **Step 1: Write HTTP tests.** `test_membership_response_models_and_role_lists` asserts exact spec JSON shapes in World/campaign. `test_membership_route_validates_actor_but_keeps_dangling_ref` covers400, empty, dangling, no actor reader calls. `test_membership_unknown_scope_is_404`; `test_membership_warm_ledger_refusal_is409`; `test_membership_routes_precede_entity_catchalls` extends existing actual route-order guard. Verify response model/schema includes plain Pydantic fields.
- [ ] **Step 2: Run RED.** membership route/order tests fail through catch-all404 or absent API.
- [ ] **Step 3: Implement models, router and API clients.** Keep store models imported as module bindings, serialize through _dump only when needed. No hand-built untyped response dict duplicating model contract. No mutation/LLM call.
- [ ] **Step 4: Verify and commit.** Run route tests, test_route_order.py and Pydantic/import/overlay guards; frontend typecheck; lint/mypy. Commit typed API before page rendering.

### Task 4: Read-only actor Groups sections and slice gates

**Files:** components/ActorGroups.tsx and tests; routes/CharacterPage.tsx, PCPage.tsx and tests; validation/execution record and roadmap S2 cells.

**Interfaces:** `ActorGroups({scope, actor}: {scope: EntityScope; actor: string})` reads api.getActorMembership and returns ColumnSection label Groups only for a successful nonempty answer. It uses Link chips labelled group name plus ` (leads)` when roles includes leader; each ref uses sectionHref(scope,{kind:record,at:groups,rid:<id>}). It offers no edit checkbox. CharacterPage passes characters:<eid>, PCPage pcs:<pid>, alongside metadata sections in the274px context column. Request cleanup plus a synchronously scope/actor-keyed loaded value suppress late old answers; failures hide the section without a banner/log.

- [ ] **Step 1: Write UI tests.** World/campaign actor pages show groups and navigate exact same-scope group URLs; leader-only and leader+member have one leads chip; empty/failure hides Groups; membership not edited here. `test_late_membership_success_cannot_draw_in_another_scope` and late failure keep a new actor's successful section intact. Exercise PC and character integration, not just mocked child rendering.
- [ ] **Step 2: Run RED.** ActorGroups/CharacterPage/PCPage tests fail missing section/API call.
- [ ] **Step 3: Implement the shared component and page integration.** Reuse ColumnSection, chip styles and sectionHref; no duplicate async effect between actor pages, new navigation rail or standalone editor. Scope/actor change synchronously hides old chips before effect cleanup.
- [ ] **Step 4: Verify coherent UI and commit.** Run the three UI files, TypeScript and lint ratchets; existing page flows remain green. Commit independently before final documentation.
- [ ] **Step 5: Complete slice verification and reviews.** Run the slice's backend/architecture tests and make check with explicit worktree PY. Use isolated verification skill for World/campaign character+PC chips, leads and same-scope navigation. Native /codex:review examines frozen diff; an independent agent runs brutal-review as gate; fix reproduced findings and commit regularly. Actual /codex:adversarial-review compares final diff with originating S2 spec requirements. Runtime public-snapshot workaround may be used only for the actual installed Codex runtime when its sandbox helper is unavailable, with exact frozen source and no private store data; a blocked/unverified review is never approval.
- [ ] **Step 6: Record evidence and advance.** Mark only genuinely passed S2 Plan/Plan gate/Code/Review/Final cells; Landed remains unchecked until integration is authorized and occurs. Record commands/counts/frozen SHAs/thread IDs/limitations in validation artifact and theory note: sole group authority, coupled source provenance, current strict preflight before every hit, deep-copy ownership. Continue S3-S8 under existing authorization.

## Plan self-review

S2 coverage: Task1 owns7.1/7.2/12.1; Task2 owns7.3/7.6 and S2 strict/freshness acceptance; Task3 owns7.5 and response models/order; Task4 owns6.2/17.8 and actual review gates. S1 owns copy/identity diagnostics and lifetime fences already; no new writer lands in S2. S8's compiled tier remains separate. Every Review Focus case has a named task test. Internal capture models are provenance for one read, not another authority or persistent artifact. Signatures and public field names match the approved spec.
