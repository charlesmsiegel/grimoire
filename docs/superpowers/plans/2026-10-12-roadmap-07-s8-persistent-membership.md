# 07-S8 persistent membership implementation plan

> Execute inline with superpowers:executing-plans. User authorized every spec07
> slice, frequent coherent commits and independent brutal-review gates. Focused
> checks once per change and one combined full branch gate before completion.

**Goal:** Reuse roadmap03's optional persistent artifact provider for membership
without changing any answer, ordering, error or native filesystem identity rule.

**Spec:** ../specs/2026-10-09-roadmap-07-explicit-group-membership-design.md,
sections7 and17.2 plus the S8 slice. Preparation review:
../validation/2026-10-12-spec-07-s8-preparation-review.md.

**Dependency:** Hard03-S2/S3, soft03-S4. Frozen public provider from sibling
.worktrees/spec-03-compiled-cache atcccb5ac55a7e3af3805c61055ad655d03aedca45.
Current feature lacks that provider. Integrate its reviewed infrastructure closure
in this worktree; never mutate the sibling or import unrelated search/vector/token
consumers. Integration is a separate checkpoint and gate scope.

**Theory:** Existing group_inventory/overlay own captured bytes, effective source
selection, native identity and strict ledgers. Persistence saves derivation cost
only; it is never a roster authority. The serialized artifact is the complete
coupled _MemoValue (Index plus GroupRecord bodies/metadata/proofs), reconstructed
through external Pydantic compatibility and independently returned. Cache failure
is a miss; strict input failure remains a refusal.

## Boundaries and reviewed provider reuse

- Reuse store/compiled.py, store/file_stats.py, compiled_build.py and
  template_paths.py from the frozen provider. Preserve its bounded checksummed JSON,
  SQLite optionality, source/generation/deletion fences and early begin_compute.
  Provider resource/key protocols are the existing dependency's infrastructure;
  new membership serialization models remain plain Pydantic. Do not create a second
  persistence implementation or introduce dataclass membership/wire models.
- Port only required supporting interfaces: physical campaign hold accounting in
  locks, fork resets in logs/maintenance_reports, root pinning in library_paths,
  template resolver ownership, BUILD/package-source identity, shutdown close,
  root_change around routes/config Storage publication and provider delete intents
  around World/campaign deletion. Merge hooks into current S1/S5/Art lifetimes;
  never replace a whole newer route/lifecycle file with the sibling copy.
- Preserve module-edit/World barrier/campaign ordering and run bookkeeping. SQL
  flush/purge stays outside campaign holds and async event loops. Optional cleanup
  never changes a successful authoritative write's result. No expanded baselines.
- Use SDK-exposed provider batch, begin_compute, make_key, lookup and put. Captured
  group input manifests supply collections digests; do not substitute compiled
  Collection enumeration or trusted persisted-source hashes for the current strict
  membership capture. Its absent-dir/.MD/directory/native-shadow behavior differs.
- This capture-based adapter is initially lazy. Explicitly record that source-to-
  materialized reverse registration/eager rebuilding remains the soft03-S4 follow-up.
  Do not claim a measured speedup without this consumer's evidence.

## File map and contracts

Dependency: new frozen provider files and existing locks/logs/maintenance_reports,
library_paths/prompts/compiled_build packaging, main lifespan and config/delete
routes as required by actual imports and lifecycle ownership. Reuse relevant provider
regressions from its tracked tests, especially early derive/purge/root-switch/fork.

Membership: store/group_inventory.py and owning entities/overlay capture helpers
may add explicit logical library provenance to GroupCapture, supplied by owning
root resolvers before physical resolve. Never infer a store from a physically
relocated campaign junction. Arbitrary direct World roots without a proven library
binding skip persistence. Store/membership.py owns adapter and registration;
external model_schema compatibility serializes/reconstructs plain Pydantic values.
New backend/tests/test_membership_persistent.py covers complete on/off parity.

Registered kind membership_index version1, persisted-source trust disabled. Exact
key parameters declare scope and host store/campaign/World roots and relevant
native detachment proof. File/collection categories remain separate: strict ledger
sets and selected owned/inherited group-byte manifests are hashed from the SAME
capture that supplies rows. Missing collections use explicit declared absence,
not an attempted denied read. Include body/metadata bytes even when Index.digest
itself does not change. Keys remain bounded through provider.make_key.

Capture algorithm: determine eligibility through host-owned logical provenance;
open a provider batch whose generation identity is captured before capture work;
call begin_compute before inventory/hash/derivation. Strict fresh preflight runs
on EVERY artifact attempt, including warm process memo/persistent hits. Reject
persistence if batch library binding differs from captured store provenance.
Validate the actual returned artifact's roots/content/ledgers/native signature
using _matches and captured raw inherited actor refs, not a wrapper inspected
before a concurrent lookup. Native changes between calls cannot be hidden by
unchanged group bytes. A mismatching or malformed artifact is a miss; derive one
fresh uncached complete result, with existing no-retry behavior. Never swallow a
GroupIdentityConflict/input read exception as optional cache degradation.

Artifact shape: complete JSON-compatible _MemoValue including coupled records and
Index. External compatible serialization handles Paths/tuples; external model
validation handles v1/v2 reconstruction. Validate expected shape/proofs before hit
publication and deep-copy all public results. A bad, oversize or unavailable optional
payload computes normally. Reuse one inverse/snapshot memo pool; no API-only tier.

### Task 1: Reviewed provider dependency integration

- [ ] Import frozen infrastructure files and only their actual dependency closure.
  Port narrow root/process/physical-hold/build/deletion hooks and keep current Art,
  S1 and S5 ownership intact. No unrelated consumer adoption.
- [ ] Targeted provider availability/disabled/event-loop/held-lock, root transition,
  purge-before-compute-to-flush, corrupt artifact replacement and fork-reset checks.
  Guard shutdown/delete result parity and current lifecycle/refusal controls.
- [ ] Static/import/paths/lock/model checks and reduced-baseline bookkeeping where
  necessary. Commit provider integration independently; exact upstream provenance
  and any local adapters documented for reviewers.

### Task 2: Snapshot-coupled artifact adapter

- [ ] RED disabled/cold/warm/restarted persistence parity, actual hit saves deriving,
  coupled body-only edit and native actor-dir-only change, malformed payload miss,
  deep-copy isolation and capture switch/race behavior.
- [ ] Register membership_index; thread proven logical library provenance through
  capture owners. Start provider generation fencing before capture/hashing; make
  keys from captured inputs/proofs with source trust disabled. Reuse exact current
  effective group filtering rather than a second collection/list/read path.
- [ ] Serialize/reconstruct the complete private coupled wrapper externally, validate
  actual returned values, retain strict current preflight and one fresh uncached
  mismatch fallback. Cache opt-out/error changes only cost; preserve S2 memo behavior.
- [ ] Focused adapter/model checks and static ratchets. Commit coherent adapter.

### Task 3: Consumer equivalence, lifecycle acceptance and gates

- [ ] Compare complete World/campaign full and actor API responses, groups_for,
  members_of/co_affiliates/scene_groups, campaign_snapshot, context member presence,
  graph and Absorb membership consumers across disabled/cold/warm/restart states.
- [ ] Existing strict corrupt/denied/UTF8 ledgers and source reads after warm hits;
  absent collections, nested/state/art files ignored identically; native .MD, forged
  frontmatter IDs/layers, native owned shadow/mixed policies and detached actor refs.
- [ ] Same-size/stat-restored Members/Leader/name/secrecy/body changes; junction/logical
  store switches; concurrent contrary returned wrapper; generation purge/root change
  before capture/after capture/before flush; disabled SQLite/BUILD, oversize artifact,
  held-lock/event-loop opt-out. No stale or partial identity publication.
- [ ] Preserve frozen home/campaign_flow cassette. Run focused tests once per change,
  then independent brutal-review on frozen integration+adapter, native /codex:review
  and final actual /codex:adversarial-review against originating spec7/17.2. Reproduce
  supported blockers only; frequent coherent repair checkpoints.
- [ ] Combined branch make check with explicit worktree Python and actual3.11/Pyd1;
  isolated mocked-store browser acceptance for final combined behavior. Record exact
  evidence and lazy-registration limitation; only passed roadmap cells, Landed blank.
