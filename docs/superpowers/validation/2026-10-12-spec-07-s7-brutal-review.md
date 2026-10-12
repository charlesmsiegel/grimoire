# S7 scene membership projection independent brutal review

Frozen HEAD: `7f21b9a245bf4524a2e868f5cbc8cbf47db8f998`. Reviewed diff: `741351775..7f21b9a24`, limited to `SceneGroup`, `SCENE_GROUP_LIMIT`, `scene_groups` and `test_membership_projections.py`. Source/test execution used the independent git archive at `C:/Users/charl/AppData/Local/Temp/grimoire-review-s7-7f21b9a24`.

**Verdict: non-blocking. No concrete supported defect found.** The pure projection satisfies the approved S7 plan and spec sections 12 and 17.7 in the reviewed scope.

## Attacks and results

- Inverse candidate enumeration: the implementation unions `by_actor` affiliations and only looks up those groups. The guarded mapping test forbids whole-library iteration and passes. Computing each candidate's present affiliates reads that candidate roster; no library-wide enumeration is introduced.
- Cast handling: slash/colon spellings normalize to the same valid actor ref and deduplicate; malformed and nonactor strings do not contribute, case remains meaningful, and dangling well-formed refs remain stored facts. Leader-only affiliations count independently of explicit Members, without double-counting a leader who is also a member.
- Determinism: present refs sort lexically; rows sort by descending distinct-present count, name, then group ref. Independent script `C:/Users/charl/AppData/Local/Temp/grimoire-s7-independent-probe.py` exercises six source permutations, generator input, duplicate slash/colon tokens, malformed strings, equal-name ref ties and a forbidden roster sorting before visible rows. It passes, including visibility-before-limit behavior.
- Metadata/rendering boundary: default metadata retains all groups and secrecy markers, with no implicit eight-row cap. Explicit prompt visibility rejects GM-only and requires secret framing; the cap applies after filtering and sorting. Zero/negative limits return empty.
- Ownership and effects: each result contains new plain Pydantic models. Returned-model mutation does not alter the Index or a later result. Frozen source search finds no production caller of this newly added projection. It reads no files/actors, writes no state, takes no locks, and introduces no prompt, embedding, history or knowledge consumer. The function's documentation preserves those consumer responsibilities.

## Verification

Focused frozen run: `python -m pytest backend/tests/test_membership_projections.py -q -p no:cacheprovider`: **7 passed**, one existing Starlette deprecation warning, 2.43 seconds on the shared Python 3.14 environment. Independent pure ordering/normalization script: **passed**. `git diff --check 741351775 7f21b9a24`: clean. Configured frozen ruff ratchet: **1146 findings, all at baseline**.

## Not reviewed

No broad suite or full repository gate was repeated, as requested. Mypy and Pydantic/import guards were not independently rerun for this narrow review; the author's supplied results are not counted above as independent evidence. The optional Python diff analyzer was not run; configured lint supplied the mechanical pass. Future renderer/retrieval integrations, audience expansion, historical membership, actor knowledge, and earlier slices are outside this gate. A caller-supplied visibility predicate is responsible for its own side effects; this review does not claim isolation from a deliberately mutating callback. No production/test edits or commits were made; only Temp probes and this authorized artifact were written.

## Targeted inverse-cost recheck at a04da7a33

Frozen source: `a04da7a336146d89f2baa6521bb0f9cbc5caeffa`, independently archived in Temp. Reviewed correction `1a2d4ab49751d73269a483a9be00d5cdd703a883` and the S7 native review artifact. **Scoped verdict: non-blocking; the off-scene member traversal finding is resolved.** This closes a cost-contract gap missed by the initial independent review, which checked candidate enumeration but accepted scanning each candidate's full roster.

All eight `test_membership_projections.py` cases passed in the focused combined run. The new `NoTraversal` tuple raises if the candidate roster's Members are iterated; projection succeeds. Present sets now accumulate directly from the cast actors' inverse entries. Only candidate metadata (name, secrecy, leader) is read from `Index.groups`; neither the library nor candidate member tuples are scanned. Distinct slash/colon cast spellings still deduplicate, unlisted leaders remain represented by the inverse, and deterministic count/name/ref ordering plus explicit visibility/capping are preserved.

Exact correction diff-check was clean. No new production callers or history/knowledge effects were introduced. No broad suite, lint, typing or full-slice gate was repeated; the focused S3/S7 run passed all 16 repository cases, with a separate corrected S3 scratch control documented in that artifact. No production/test repository files were changed. The projection consumes a coherent Index produced by the existing inverse builder; intentionally inconsistent caller-mutated forward/inverse maps are not a supported alternative authority.
