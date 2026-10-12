# Spec 07 S2 independent brutal review

Frozen target: `843d7897e9e9c7b5f35b0f517606c7eed81b18d3`.
Approved S1 source baseline: `62270523a6315871d056c158a4df170402c8d7a1`.

**Verdict: BLOCKING.** One supported P2 finding in the new client methods. No additional concrete defect was established in the reviewed inverse/capture/memo, typed routes, or ActorGroups integration.

Scope: the complete frozen S2 delta against [spec sections 6.2 and 7](../specs/2026-10-09-roadmap-07-explicit-group-membership-design.md) and the [approved S2 plan](../plans/2026-10-11-roadmap-07-s2-inverse-membership.md). The prior [foundation review](2026-10-12-spec-07-s2-foundation-brutal-review.md) supplies supporting evidence; its Python 3.11 OS-denial injection correction is present. That earlier review did not cover the new routes/UI.

## F1 — P2: membership requests do not encode the scope path segment

Locations: `frontend/src/api/client.ts:1928–1931`, through `entityBase` at line 1014.

Both new membership methods interpolate `entityBase(scope)`, whose scope ID is inserted literally. The actor query uses `encodeURIComponent` correctly, but the World/campaign ID does not. The filesystem/resolvers accept a scope named `realm#1`; the backend serves its membership at `/api/worlds/realm%231/membership` or the corresponding campaign path.

Concrete reproduction:

1. In an isolated store, rename a valid Realm World directory, or a valid campaign directory, to `realm#1`. Keep a group recording `pcs:winifred`.
2. An HTTP request to the encoded scope path returns 200 and the recorded Salt Circle affiliation for that actor. Both World and campaign probes pass.
3. Call `api.getMembership({kind: "world", id: "realm#1"})`. It supplies `/api/worlds/realm#1/membership` to fetch. The equivalent campaign call has the same defect.
4. `getActorMembership` also leaves the scope unescaped: `/api/worlds/realm#1/membership?actor=characters%3Amara%231` for actor `characters:mara#1`.
5. URL parsing treats the text after `#` as a fragment, so the request cannot reach the intended membership route. The new section cannot obtain the intended scope's affiliations. Literal percent sequences can likewise change path interpretation.

Four independent client assertions fail, covering both methods and both scope kinds. The two real HTTP probes establish that this is a supported scope input, not a fabricated invalid identifier. The unencoded helper predates S2, but these new methods inherit the defect and the approved client contract requires encoded scope paths. The group chip's `sectionHref` does encode its scope path, so correct chip navigation does not repair the request.

Required correction: encode the scope ID as one path segment for both new membership methods, preserving separate encoding of the actor query. If the shared helper is corrected, account for its wider existing call surface; a membership-local correction is also sufficient for this finding. Pin both scope kinds and reserved-character scope IDs.

## Targeted execution evidence

Public source was archived from the exact frozen commit into `C:/Users/charl/AppData/Local/Temp/spec07-s2-review-843d789`. Backend tests used snapshot `backend/src` on `PYTHONPATH` and isolated stores. UI tests used the snapshot source with a Temp dependency junction to the existing installed node_modules. No production source or tracked tests were changed.

- Temp-only `frontend/src/api/membership.review.test.ts`: **4 failed** client-encoding probes.
- Frozen `ActorGroups.test.tsx`, in the same focused invocation: **7 passed**. Covers leader-only/combined roles, quiet empty/failure, same-scope link, synchronous old-chip hiding, and late old success/failure. Combined UI result: 4 failed, 7 passed, exit 1, 2.63 seconds.
- Temp-only HTTP `test_review_encoded_scope_membership_is_supported`: **2 passed**, 15 deselected, exit 0, 2.80 seconds. Both encoded World/campaign scopes returned their actual typed actor-membership response.
- Focused frozen cache adversaries: **8 passed**, 35 deselected, exit 0, 1.01 seconds. Selection: `concurrent_cache_insertion`, `preserved_stat_members`, `native_owned_group_shadow`, and `native_group_tombstones`. These exercise actual returned-wrapper insertion races, complete-stat-preserving Members/secrecy edits, and physical native group selection.

Only existing FastAPI TestClient/httpx and React Router future-flag warnings were observed. Broad suites and mechanical ratchets were deliberately not repeated, following the user's request for lighter targeted verification. Reported author runs remain supporting evidence, not independently rerun results here.

## Remaining contract assessment

The pure inverse retains stored refs without actor existence reads, separates leadership from membership, keeps GM-only rosters in the GM index, and includes every projected roster field/member order in the inverse digest. Prompt visibility excludes GM-only independently of secret framing. Body/state fields are not membership authority.

Capture couples source bytes/layer/file identity with its own fingerprints, freezes strict ledgers and roots, and stamps before reads. Warm reads still preflight current strict ledgers and content. Memo roots are explicit, the pool is private and bounded, snapshots are deep-copied, and the actual wrapper returned by `memo_stamped` is checked. A contrary wrapper from another reader triggers one fresh uncached derivation; a self-computed mismatch is already the required uncached answer. No retry loop or later digest certifying older rows was found. The existing strict-ledger/access failure coverage and corrected dual `listdir`/`scandir` injection were inspected rather than rerun broadly.

The typed membership router precedes generic entity routes, validates query actor refs, permits well-formed dangling refs, and returns the specified roles/secrecy shapes. ActorGroups is shared by character and PC context columns, uses a scope/actor-keyed loaded value plus effect cleanup, and links through `sectionHref`. Page-level same-scope integration assertions were inspected. No separate secrecy-label requirement exists in section 6.2; GM-visible groups are intentionally retained.

This artifact is the frozen S2 code review, not slice completion, a combined branch gate, a browser walkthrough, or certification of later S3–S8 work. No private store was accessed and no other files were written in the feature worktree by this reviewer.

## Author correction

Membership scope paths now pass the raw scope id through encodeSegment before the existing scope builder. Both full and actor clients share this seam; actor query encoding remains separate. The four independent failed URL probes were copied as regression tests and now pass. Scoped independent recheck follows on the committed correction.

## Scoped F1 recheck — frozen bbf74decc

Recheck target: `bbf74decc8929fcfefc9831fac06513eba12451f`.

**Verdict: NON-BLOCKING for the S2 scope-encoding correction. F1 above is closed at this SHA.** The earlier BLOCKING verdict remains the assessment of frozen `843d7897e9e9c7b5f35b0f517606c7eed81b18d3`.

Both membership methods now use a membership-specific helper that applies `encodeSegment` to the scope ID before forming the path. The actor query retains its independent `encodeURIComponent` encoding. The correction does not change the shared base helper's other callers.

Independent verification used public source archived from this exact commit at `C:/Users/charl/AppData/Local/Temp/spec07-s2-encoding-bbf74de`, with existing dependencies linked into that Temp snapshot. The four committed regressions derived from the independent failing probes all passed: **4 passed**, exit 0, 2.09 seconds. They cover full and actor-filtered membership requests for both World and campaign `realm#1` scopes, including the separately encoded actor ref. No broad suites were repeated.

The recheck read committed source only; concurrent uncommitted S3 work was excluded. No supported remaining defect was found in this bounded correction. This closes the sole supported S2 brutal-review finding, not the separate actual native gates, combined branch validation, browser verification, or slice completion. Only this validation artifact was written in the feature worktree by the reviewer.
