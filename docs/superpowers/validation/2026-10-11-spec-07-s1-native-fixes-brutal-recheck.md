# Spec 07 S1 native-fixes brutal recheck

Frozen target: `6eb1370f0fc364357f1ee54ec899960de76f9671`.

**Verdict: NON-BLOCKING for the scoped World image lock-set correction.** F1 in the [frozen native-fixes brutal review](2026-10-11-spec-07-s1-native-fixes-brutal-review.md) is closed. That earlier blocking verdict remains an accurate assessment of `86964ac22d3df749eb680d65bd09cf283d8b573d`.

The recheck covers the correction in this commit and its owning image-delete path, under the approved S1 design and plan. It does not certify intervening Windows dependency integration or reopen the deferred S2–S8 slices.

## Attack and control evidence

The earlier independent attack creates a new dependent campaign Realm on another thread at the World image deletion seam, while the original dependent Saltmarch is already held. On the earlier freeze, the cleanup sweep reached Realm without holding its campaign lock and failed the review assertion. The corrected frozen regression `test_world_image_sweep_stays_in_captured_lock_set` now passes: only Saltmarch is visited, and every swept campaign is held.

The source retains the tuple yielded by `world_identity_change` and passes it to `_forget_in_dependents` as a required keyword argument. The helper iterates only that tuple. Repository search found one invocation of the helper, and it supplies the captured IDs. The context itself preflights those IDs inside `locks.hold_all` and keeps the hold across its yield. There is no dependent rediscovery in the corrected image sweep.

Independent verification used a public-source archive of the full frozen SHA in `C:/Users/charl/AppData/Local/Temp/spec07-image-recheck-6eb1370`, with snapshot `backend/src` on `PYTHONPATH` and isolated test stores. The lifecycle, World image-store, and campaign image-store modules completed with **118 passed**, exit 0, in 18.04 seconds. This includes the reproduced attack and ordinary image deletion/hidden-image controls. JUnit evidence is `checks.xml` in that snapshot directory. The only warning was the environment's FastAPI TestClient/httpx deprecation warning.

No supported remaining defect was found in this correction. The review verifies captured-lock-set discipline, not a stronger guarantee that newly created dependents participate in an already-running World operation; broader discovery coordination is deferred by the approved S1 contract. No private store, product file, or tracked test was changed by the reviewer. This scoped result does not replace the full repository gate or the separate native review.
