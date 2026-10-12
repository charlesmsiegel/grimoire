# Spec 07 S4 independent brutal review

Verdict: **NON-BLOCKING** for the frozen S4 delta. No supported blocking or non-blocking defect survived the targeted attack pass.

Frozen source: `7413517756f19f2eb73b298fcbef7cd8e1b79d35`, compared with `f150a99c273210014bfa0e83368af006e8d7a858`. Reviewed the approved S4 plan, the specification's integration overview and the actual presence requirements in sections 11 and 17.6 (section 17.4 concerns Absorb), and the owning snapshot, overlay, activation, actor-knowledge and inspector code. This is an independent review gate, not whole-branch validation or slice completion.

## Ranked findings

None. In particular, secret-group structural presence does not itself constitute a knowledge leak under the approved contract: the scene supplies presence while actor knowledge independently filters candidate prose. GM-only and excluded groups remain barred from contributing presence.

## Independent attack evidence

Frozen public source was extracted with `git archive` to `C:/Users/charl/AppData/Local/Temp/spec07-s4-review-7413517`. Reviewer-only probes live there in `backend/tests/test_s4_independent_review.py`; no product source or tracked tests were changed. All fixtures use disposable `GRIMOIRE_HOME` directories.

Command: shared repository Python, `PYTHONPATH=<snapshot>/backend/src`, `python -m pytest backend/tests/test_s4_independent_review.py -q --disable-warnings`.

Result: **6 passed, 1 warning in 0.81s**.

- Actual consumer interleaving: capture GM-only Salt Circle with Mara in Members, then rewrite the file public with empty Members immediately before returning the snapshot to `_world_info`. Neither group presence nor keyed group-owned Saltmarch lore appears. The consumer uses the captured secrecy alongside the captured refs.
- Stat-preserving edit: warm the snapshot after aging stamps, replace `characters:mara` with same-length `characters:MARA` and body `Mara` with `MARA`, restore file nanosecond timestamps. The next snapshot has the new body and new Members, while the earlier returned snapshot retains its old body. Current raw-content preflight defeats a stamp-only cache hit.
- Strict current identity, two cases: warm actual world-info collection, corrupt `deleted.json` or `detached.json`, then collect again. Both raise `GroupIdentityConflict`; neither falls back to the warm snapshot.
- Secret/excluded controls, two cases: secret Salt Circle names present Mara; an item is held by the group and public keyed lore belongs to that item. Mara's actor-scoped call receives the public lore and exact `member_present` reason, but no secret group prose. Excluding the group removes both group/item presence and that lore.

A focused run including the existing `test_group_member_presence.py` controls reported **16 passed, 1 warning in 1.76s**. Those controls cover leader/member/headquarters priority, malformed/non-actor refs, detached inherited versus owned refs, GM-only filtering, and exactly one snapshot call with ordinary group reads forbidden. The six independent probes were subsequently rerun with only repository placeholder spellings, as reported above.

## Code conclusions and limits

`GroupSnapshot` records and index derive from the same captured rows and remain in one memo wrapper. The actual-returned-wrapper provenance check is retained; public snapshot/index access deep-copies results. Group enumeration and prose consumption no longer perform a second ordinary group read. Non-group reads and Headquarters parsing retain their prior owners. Leader and Members use the effective roster, and ordered structural rules preserve legacy leader precedence. The inspector adds the exact specified member reason using its existing friendly-name/raw-ref fallback.

Mechanical lint/type results were supplied by the implementation owner, not independently rerun; the optional code-doctor detector was not run. I inspected the one-case inspector change and its tests but did not rerun frontend or browser suites. No repeated whole-context/golden/frozen campaign suite, performance benchmark, full `make check`, separate native Codex gate, or later S5 work is certified here. Existing S2 cache concurrency and native identity proofs were traced as retained dependencies rather than exhaustively rerun. No private store or frozen campaign fixture was accessed or changed.
