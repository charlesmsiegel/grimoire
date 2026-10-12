# S5 token/barrier foundation independent review

Checkpoint: dirty source over `3950f9b00942ef9a81fd732878587fa36ff3a597`, frozen independently in `C:/Users/charl/AppData/Local/Temp/grimoire-s5-token-review`. Exact captured SHA-256 values:

- group_proposals.py: `64A3152032E2196DA3DFFD313373C5B166E980E2B7BF2E48F2E05B04463545B6`
- locks.py: `FE1F0793F92B1ABBE93154E5993A0C3562CD54B0086AF045F1CC22DC26CE2A7A`
- test_group_proposals.py: `06A53C6AC2F3DFE7337792D5C9542BC055363C9B7F28B68444084EBB39111DB0`

Scope: approved S5 Task 2 token-ledger and World barrier foundation, plus read-only advice on lifecycle integration. Capture/save/lifecycle callers are deliberately pending and their absence is not a finding.

## Initial verdict: blocking

### Blocking — explicit ledger root and held campaign root can disagree

`store/group_proposals.py:90` and the analogous validate/retire/copied methods accept explicit `croot`, but take `locks.campaign_lock(cid)`, whose file-lock path follows the current global home (`locks.py:666`). Reproduction uses two real processes and two synthetic stores. Parent holds store A's Saltmarch campaign lock. A child on A cannot register and raises CampaignBusy, proving the exclusion is held. A child whose current home is B calls register with explicit A campaign root and A owned-source provenance: it prints REGISTERED and creates A's membership_review_tokens.json while the parent still holds A's campaign lock.

Consequence: token read-modify-writes can race another process's operations on the actual ledger, invalidating retirement/nonce guarantees. This is a foundation ownership mismatch, not absence of a future capture caller. Fix at the shared token hold boundary: pin the host-selected store, prove supplied croot is its selected campaign root, then acquire that same store's campaign domain before reads/writes; alternatively provide a correctly pinned campaign-lock API. A stale explicit root must refuse before writing. Do not rely on a once-only comparison before a later pointer read.

### Should fix — forbidden world-actor reverse acquisition is allowed

`store/locks.py:994` checks physical campaign holds only. With `locks.world_actor_lock(wid)` held, `world_lifecycle_lock(store, world).acquire(blocking=False)` succeeds. Section 4.5 explicitly prohibits entering the lifecycle barrier inside a world-actor hold, as well as inside a campaign hold. Opposing actor/lifecycle acquisition can therefore form the forbidden wait cycle. Track physical world-actor holds as well, preserving reentrancy when the lifecycle barrier was already acquired in the permitted outer order, and refuse reverse entry before waiting.

Both cases are in `C:/Users/charl/AppData/Local/Temp/grimoire-s5-token-lock-probe.py`. Output: same-store control refused=True; switched-store child exit0 REGISTERED; A ledger created while A exclusion held=True; reverse world-actor acquisition permitted=True. Author acknowledged these findings and is preparing corrections; that is not yet closure evidence.

## Initial validation and boundaries

The supplied focused token/barrier suite passed **10 tests**, one existing Starlette warning, 4.60 seconds. Frozen configured ruff ratchet: **1136 findings, all at baseline**. Existing tests exercise nonce preservation, permanent retirement after recreation, observed binding mismatch, trusted owned-copy continuity, malformed-account refusal, barrier pinning and cross-process/reentrant exclusion. Source review confirms complete-account shape validation, no following of stored provenance paths, and returned lease deep copies. No broad suite or typing repeat was run. Real POSIX fork behavior is not testable on this Windows host; factory PID reset and inherited-reference handling require their own explicit evidence. No speculative fork finding is asserted here.

## Smallest lifecycle reuse seam

Keep record identity decisions and effect ordering at the existing S1 owner calls: overlay delete/reclassify and discard/take-World; campaign pruning; World reclassification/follow; World identity-change and demotion preflight. Fan out token and unfinished-commit retirement beside the existing journal retire/begin/complete relocation operations. First preflight all participating ledgers across the held domain, then durably fence before any unlink/move. Complete relocation using the actual landed suffix, not the requested destination. Preserve existing samefile/source-namespace capture before removal.

Let commits.py own strict target discovery and retirement/relocation RMW, preserving `_preserve_targets`/`_preserve_progress` as the checkpoint merge authority. Iterate every unfinished commit, including deferred journalled reversals, citations/diffs and pending/unconfirmed receipts; completed historical results are not new active offers. Do not put source-sensitive World retirement inside journal's owned-only target helper or use generic display-ref repoint as lifecycle authority.

The World barrier belongs outside dependent discovery and hold_all. Discovery must union current dependents with host-scanned live token and unfinished-commit source claims, then recheck bound/effective source identity under the ordered holds. Retire inherited outstanding work without stealing a verified independent owned original. Originally owned tokens and trusted copy receipts have different survival rules; retain that distinction rather than infer continuity from matching bytes.

Not reviewed: unfinished lifecycle/capture/save integration, extraction/model behavior, UI, full S5 gates, external editors/devices and real fork execution. No production/test repository edits or commits; only synthetic Temp stores/probes and this authorized artifact were written.

## First correction recheck: ordinary closure, native root bypass remains

Captured corrected dirty files separately in `Temp/grimoire-s5-token-dirty-recheck`: group_proposals SHA-256 `B063C49A6DD95C53D849D08E0772792277EFB81A156A9E81DC37B8DD4764A63D`; locks `44801E5A2467F0F3AB94F69EBD1EA0D76212EB86255E6FAFD650D4C1D202ABD5`; paths `D962DAEDEB23970E0DCE9CD943D7DF1561FB51923D4B87B1803F4497B434C2D9`; tests `F225A3EA57E4B77CF0A0A680A1A03086BD7A412B9177C5A7C8203C816CD81322`.

The new shared `_hold` pins the host root and refuses ordinary stale-root calls, and physical domain tracking now refuses entering the lifecycle barrier under a world-actor hold. The focused suite passes **12 tests**, 5.48 seconds. Old World barrier references now reset descriptor/depth/RLock on PID change as well as registry reset; this is source-inspected, not a claim of actual fork execution on Windows.

**Verdict remains blocking on the narrowed F1 case.** `_hold` compares resolved `WindowsPath` objects using equality. On native Windows this comparison folds case even inside case-sensitive directories. Independent probe `Temp/grimoire-s5-native-root-probe.py` enables real directory case sensitivity, creates distinct existing stores `realm` and `REALM`, each holding Saltmarch, and proves `not crootA.samefile(crootB)` while `crootA.resolve() == crootB.resolve()`. With A's campaign exclusion held in the parent, the same-store child refuses, but the B-home child still writes A's explicit token account successfully. Exact output: distinct existing campaign roots=True; Path equality=True; same-store control refuses=True; switched-store child exit0 REGISTERED; A ledger created while A exclusion held=True.

Use native samefile proof for the selected existing campaign root versus the explicit root, treating absence or unavailable identity as refusal, rather than lexical Path equality. Keep the pin around validation, acquisition and RMW. This is the same demonstrated wrong-domain write defect, not a new integration requirement. The world-actor reverse-order finding is closed by its focused regression; full lifecycle/capture remains excluded.

## Final narrow F1 closure: non-blocking foundation checkpoint

Captured final dirty `group_proposals.py` separately as `Temp/grimoire-s5-token-dirty-recheck/group_proposals-final.py`, SHA-256 `6BF4962BA5CE2D13E05B492E5F4EFD95E868CE5B78A65F42FC5B2303B85C07F0`; other runtime files remain the first-correction snapshot identified above. This is a dirty-source checkpoint, not a claim about a later commit.

The shared `_hold` now pins the selected library and proves `campaign_root(cid).samefile(croot)` before taking the campaign lock or accessing the token ledger. Failed native identity lookup becomes GroupIdentityConflict. This closes the demonstrated mismatch without treating WindowsPath equality as physical identity.

Independent native subprocess probe `C:/Users/charl/AppData/Local/Temp/grimoire-s5-native-root-closure.py` passed once (exit 0, 7.05 seconds) against the captured final file. The real case-sensitive parent contains distinct existing `realm` and `REALM` stores: native campaign roots differ although resolved Path equality is true. While process A holds A's campaign exclusion, its same-store child still refuses with CampaignBusy; the B-selected child using explicit A root now refuses with GroupIdentityConflict; A's token ledger remains absent. All three outcomes are asserted, including refusal before ledger creation.

**Scoped verdict: non-blocking.** Both supported foundation findings are closed by the recorded evidence. No further suite, lint, typing, fork or integration run was performed for this final one-file closure; earlier mechanical and focused results retain their stated scopes. Lifecycle/capture/save integrations and final S5 certification remain expressly not reviewed. No production files or repository tests were changed by this review.
