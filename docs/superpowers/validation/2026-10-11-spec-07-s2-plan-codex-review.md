# S2 actual plan review

Frozen branch: `41da96e4d993f6b9699aabe1f86cb00ee304f5b3`; actual installed Codex thread: `01a12df3-7f75-7792-8848-cae8d04c5b5d`.

Verdict: **needs-attention**. The native runtime used immutable intended-public
tracked source snapshots because its sandbox helper is unavailable. This is
the actual installed adversarial prompt and structured schema, not a substitute
reviewer.

Block S2 implementation until the memo preflight handles concurrent cache insertion.

## A concurrent insertion can bypass the freshness check

Task 2 Step 4 checks the pool before calling memo_stamped, but this function performs another pool lookup. A reader computing snapshot B can pause before cache publication. After a stat-preserving edit restores source A, another reader preflights A and observes an empty pool. If B is then published before that reader enters memo_stamped, unchanged stamps authorize B despite its mismatching content fingerprint. As written, the plan permits stale Members or secrecy on that read; its named mutation tests do not explicitly cover concurrent cache insertion.

Owning file: `backend/src/grimoire/store/statcache.py`, lines 178-180.

Recommendation: Amend Task 2 to verify the actual returned cache wrapper against that call's preflight roots, ledger/content digests and native-detachment signature. A mismatching cached return must trigger one fresh uncached computation. Add a two-reader test that pauses publication across a stat-preserving B-to-A edit.

No approval is inferred. Fixes, regressions and actual recheck evidence are
recorded separately; the final full gate remains required.

## Actual recheck

Frozen plan/source checkpoint: `3947aaa56b02350ff4440cc948baf885ffdb4918`.
Actual installed Codex thread: `01a12e1c-0150-71b0-8241-5e17915aed2b`.
Verdict: **approve**, no findings. The prior ephemeral thread could not resume,
so the actual runtime received the full updated originating context. Returned
wrapper validation and the explicit concurrent insertion test close the blocker.
S2 implementation remains dependent on closing S1 final code gates.

## Concurrent frozen-validation timing approval

Frozen `a4faa07710a4c7d21f6ee6865b7639f129adfa35`; persisted native thread `01a12e1c-0150-71b0-8241-5e17915aed2b`. Verdict **approve**, no findings.

Approve the timing amendment at a4faa077. Concurrent S2 development preserves immutable S1 validation and does not waive completion or shipping gates.

Interfaces and tests retain the prior approval. S1 full-gate result remains
required; any failure must be resolved and affected dependent work revalidated.
