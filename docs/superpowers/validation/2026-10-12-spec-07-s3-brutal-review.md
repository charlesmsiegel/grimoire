# Spec 07 S3 independent brutal review

Frozen target: `4ed7da891b19b430e38ab72e67364ad4880437bf`.
S2 comparison baseline: `7982fca79`.

**Verdict: NON-BLOCKING.** No concrete supported defect was established in the frozen S3 delta. This is the independent code-review gate only; actual native code/final gates and combined validation remain separate.

Reviewed against [spec section 5.3](../specs/2026-10-09-roadmap-07-explicit-group-membership-design.md) and the [approved S3 plan](../plans/2026-10-11-roadmap-07-s3-sync-fields.md): entity field extraction/alignment, campaign naming, native detached warnings, strict identity reads, raw/display UI semantics, optional-side behavior, and read-only scope. No later working-tree changes were included.

## Targeted independent evidence

All source was archived from the exact frozen SHA to `C:/Users/charl/AppData/Local/Temp/spec07-s3-review-4ed7da8`. Backend execution used snapshot `backend/src` on `PYTHONPATH` and isolated stores. UI execution used snapshot source with a Temp junction to existing installed dependencies. No production source or tracked tests were changed.

Four reviewer-only backend probes passed: **4 passed**, 7 deselected, exit 0, 2.05 seconds. Added only to the snapshot's `backend/tests/test_sync_fields.py`:

- Native case-insensitive actor alias `MARA` with canonical `characters/mara` detachment: World raw Members retains `characters:MARA, characters:missing`; shown text retains the unresolved raw alias plus the stranger warning and the missing ref. The campaign cell shows ordinary Mara without the warning. Every file under the isolated home remains byte-identical after `incoming`.
- Corrupt `deleted.json` and corrupt `detached.json`, separately, with a pending actor-reference change: each raises `GroupIdentityConflict`; the damaged bytes remain unchanged.
- Two campaigns with different names for the same selected actor ID: successive reads of the first, second, and first campaign return Mara, Winifred, and Mara respectively. The per-call display map does not leak across campaigns.

JUnit evidence: `s3-review-probes.xml` in the snapshot.

Only the four S3-specific `typed incoming` UI cases were executed: **4 passed**, 27 intentionally skipped by the name filter, exit 0, 1.55 seconds. They cover different raw refs with equal shown names, unchanged raw values with differing shown warning text, a mine-only typed row, and whitespace as a stored change. No broad suites were repeated, following the user's lighter-testing instruction.

The backend emitted the existing FastAPI TestClient/httpx deprecation warning. The focused UI run reported no warning.

## Contract assessment

- `_entity_blob` takes only schema-declared entity fields and keeps their exact parsed string values. `_align_entity_fields` emits the union in schema order, including empty peer rows. Greetings, plot maps, locked card/persona payloads, and unpicked actor summaries retain their prior shapes.
- Ref decoration operates on `shown`, not `value`. Missing and legacy tokens remain readable. One fresh View and one per-call name map are passed through the required rosters/listings; each referenced kind is fetched at most once. Existing samefile-based detachment logic supplies warnings without case folding. Raw aliases absent from the exact roster map remain raw, as the plan explicitly permits.
- Actor-reference judgments strictly load both complete ledgers from the captured roots before naming/warning. A clean or non-reference incoming set does not incur an unrelated actor roster sweep. The new code adds no writer or mutation of source records, manifests, pins, revisions, or journals.
- UI rows remain Name, typed fields, Body; defensive mine-only keys remain before Body. Equality uses raw values exclusively. Display uses shown text, and typed values are rendered as plain text. Body retains markdown where previously supported. A whitespace-only stored difference survives pairing and prevents a false identical-fields claim.
- The entity invisible-change hint now names only keys, owners, or secrecy. Actor/persona/greeting-specific explanations remain intact. Existing selection/resolve/async control flow is unchanged.

No failure hypothesis without a reproducing input was promoted to a finding. In particular, current backend actor payloads omit a missing `mine` key rather than explicitly storing null, so the optional-side traversal does not produce the suspected null dereference. Stored frontmatter fields are string scalars, matching the new wire type.

## Limits

Reported author runs and static ratchets were inspected as supporting context but not repeated or relabelled independent evidence. This review did not execute broad sync/architecture suites, Python 3.11/Pydantic-1, a browser walkthrough, or the final combined branch gate. It does not claim S3 slice completion or review S4+ behavior. No private store was accessed. Only this artifact was written in the feature worktree by the reviewer.

## Targeted native-name recheck at a04da7a33

Frozen source: `a04da7a336146d89f2baa6521bb0f9cbc5caeffa`, independently archived in Temp. Reviewed correction `c541485a7f061421883b7534209a36c12d786cd9` and the S3 native review artifact. **Scoped verdict: non-blocking; the reproduced owned-alias display-name defect is resolved.**

All eight `test_sync_fields.py` cases passed in the focused combined run, including the real Windows World-MARA/owned-mara regression and the one-View/one-needed-roster check. Raw field values remain unchanged. `_incoming_names` reuses the existing roster names, enumerates addressable owned IDs without opening actor/card bodies, and remaps only queried actor refs after native samefile proof in the campaign namespace. Both character and PC branches use their existing listed-ID helper; no casefold or second roster read was introduced.

Independent physical case-sensitive campaign controls also passed (2 cases): a distinct owned uppercase MARA reports its own Winifred name instead of the World Seraphine or lowercase Mara; with no owned uppercase candidate the name stays Seraphine. The latter retains the pre-existing shared detachment warning because World-native proof still matches that raw ref. The scratch test initially expected no warning and failed; source inspection showed that assumption contradicted the existing shared actor-detachment policy, so the corrected control tests preservation of that policy, not a new semantic change. Final controls: 2 passed in 0.66 seconds. Scratch source: `C:/Users/charl/AppData/Local/Temp/grimoire-review-a04da7a33/backend/tests/test_review_native_names.py`.

The initial combined run was 17 passed/1 scratch-expectation failure; all 16 repository S3/S7 cases passed. An intervening standalone Temp test invocation failed collection on an unrelated disappearing Temp path; relocating only the scratch test into the isolated snapshot resolved collection. Exact correction diff-check was clean. No broad tests/lint/typing repeats or production edits; no claim of a new full S3 gate. Runtime probes used characters; the analogous PC helper seam was read, not independently exercised.

## Second targeted native-name and cost recheck

Frozen source: `3509bb1c8dddf140ad9c60fa9c0dc51fd0eeb644`; reviewed `sync.py` and focused test changes against `3fcaefe18`. Independent source archive: `C:/Users/charl/AppData/Local/Temp/grimoire-review-3509bb1c8`. **Scoped verdict: non-blocking.** The supported flat-reference alias-name defect and pairwise actor identity-scan defect are resolved in the exercised cases. This is append-only evidence, not replacement of earlier frozen verdicts.

All 10 repository `test_sync_fields.py` cases passed, including raw uppercase location refs resolving to the owned campaign name and exact owned actor IDs avoiding native identity scans. The existing pending-batch test still proves one View and one needed actor-roster read. Source inspection confirms `entity_ids`, character `listed_ids` and PC `listed_ids` enumerate existing IDs without another record/card content read. Name remapping uses only the already-built name map.

Independent physical Windows controls covered case-sensitive owned locations over an insensitive World. With only lowercase owned Saltmarch, uppercase `locations:SALTMARCH` retains the World Realm name; with a separate uppercase owned record, it reports that record's Winifred name. Both preserve the raw field value verbatim. Both tests passed, proving device/inode matching does not casefold two distinct owned files together.

Independent cost instrumentation exercised both characters and flat locations with 100 owned IDs, 100 unmatched valid refs, malformed refs and one real native uppercase alias. Exact owned/invalid refs caused **0** identity probes. Adding 100 unmatched refs caused **100** probes and no owned-slot scan. Adding the real alias caused **201** probes: 101 queried paths plus 100 owned paths, each spelling probed once. Both tests prohibited `io.open` during remapping and passed. Thus this boundary performs linear native identity work rather than the previous cross-product scan, with no additional body/card reads. Scratch source: `C:/Users/charl/AppData/Local/Temp/grimoire-review-3509bb1c8/backend/tests/test_review_s3_second_native.py`.

Evidence accounting: first combined run had 12 passes (10 repository + 2 physical namespace controls) and 2 scratch assertion failures. The cost counts and name assertions already passed; the failed assertion used a `set[WindowsPath]`, whose case-insensitive equality intentionally collapses the alias and canonical spelling. Correcting only that scratch assertion to count exact string spellings yielded **2 passed, 2 deselected**, 0.82 seconds. Total supported focused cases passing across these runs: 14. Exact source/test diff-check was clean. One existing Starlette warning was emitted.

Not reviewed/repeated: broad suites, lint/mypy ratchets, full native S3 final gate, other slices, and metadata identity under concurrent external filesystem replacement. The physical cost probes covered characters and locations; the shared PC branch was inspected rather than independently exercised. No production/test repository files changed, no commits and no private stores were used.
