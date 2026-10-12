# Spec 07 S1 Undo readback brutal recheck

Frozen target: `ea7a93f24d02cc774cab1f182ee1556732611b60`.
Baseline: `6eb1370f0fc364357f1ee54ec899960de76f9671`.

**Verdict: NON-BLOCKING for the scoped Undo/Redo readback correction.** No supported remaining finding in the reviewed delta and its owning calls.

The reviewed defect was a completed Members restoration followed by `seal` returning `None` because its readback failed. The prior Members display branch raised after the write, skipping reversal history and completion bookkeeping. The fix retains the established non-undoable reversal fallback: actual before-value, explicitly unreadable after-value, no inverse offer, and normal append/mark-completed execution.

## Independent attack and control evidence

Public source was extracted from the frozen SHA into `C:/Users/charl/AppData/Local/Temp/spec07-undo-recheck-ea7a93f`. Tests used snapshot `backend/src` on `PYTHONPATH`, the shared Python environment, and isolated test stores.

- Frozen group-membership lifecycle, Undo-store, and journal-store modules: **120 passed**, exit 0, 22.76 seconds. This includes both committed Undo/Redo regressions that make `read_value` fail only after the real restoration write succeeds. JUnit evidence: `checks.xml` in the snapshot.
- Reviewer-only route probes, parameterized for Undo and Redo: **2 passed**, 50 deselected, exit 0, 2.46 seconds. These independently perform the real restoration and then inject an `OSError` into postwrite readback. Each HTTP request returns 200 with `undoable=false`; the persisted reversal contains the actual prior snapshot display and `UNREADABLE` after-value; the campaign revision changes. Repeating the original request returns 409, requesting reversal of the non-undoable row returns 400, and exactly one restoration write occurs. Tests were appended only to the disposable snapshot as `test_review_readback_failure_route_finishes_and_refuses_duplicates`.

The runs emitted only the environment's FastAPI TestClient/httpx deprecation warning. The additional route probes were collected separately from the 120-test run.

## Caller and failure-boundary inspection

`seal` intentionally catches readback failures and returns `None`; it never supplies an empty valid inverse dictionary. The corrected truthiness branch therefore preserves actual-value display when a readback exists and an explicit unavailable display otherwise. `journal.append` and `journal.mark_undone` remain inside the existing campaign hold and `revision.stamp_on_failure` boundary. The earlier prewrite read/CAS refusals are unchanged.

The journal Undo route accepts the completed reversal normally, and its response renderer derives undoability from the presence of a valid inverse target. It does not attempt another `undo.read_value` call to fabricate the unavailable value. The independent route probes confirm the disabled offer and original-entry completion behavior on both directions of reversal.

This review is limited to the new Undo/lifecycle delta and these owning calls under the approved S1 contract. It does not certify the full repository gate, Windows dependency integration, unrelated S1 surfaces, or deferred S2–S8. No product or tracked test edits were made by the reviewer; no private store was accessed.
