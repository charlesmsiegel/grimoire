# S1 native Codex implementation review

Frozen branch: `02131dd93be627d6f45fb3c960c48161e04a0d20`. Native review thread: `01a12dad-498e-7f02-a005-8924d34b4dde`.

The installed Codex plugin ran its native review/start API against an immutable
public source snapshot after the sandbox-helper failure documented in the
execution record. This is an implementation review; the originating-spec final
adversarial gate remains outstanding.

The frozen S1 diff contains an inherited-write validation bypass, a post-mutation ledger refusal, a lock-order violation, and inaccurate membership reversal displays. These findings do not require implementing any pending slice.

Full review comments:

- [P1] Validate the entity ID before publishing an inherited edit — C:/Users/charl/github/grimoire/.worktrees/spec-07-group-membership/backend/src/grimoire/store/overlay.py:1027-1030
  On Windows, `PUT /api/campaigns/saltmarch/groups/..%5Clore%5Csalt-circle` with a body and no `rev` reaches this branch when the World holds `lore/salt-circle.md` and the campaign does not. The prepared-copy path no longer calls `entities.update_entity`, bypassing its `safe_id(eid)` check and publishing an edited lore record through a groups URL. Additional backslash traversal can publish outside the requested campaign under the wrong lock. Validate kind and ID before path probes or publication.

- [P2] Preflight image promotion before the strict tombstone read — C:/Users/charl/github/grimoire/.worktrees/spec-07-group-membership/backend/src/grimoire/store/overlay.py:301-304
  With corrupt `deleted.json`, promoting an inherited gallery image when neither layer has an avatar mutates assets before this new refusal. `overlay.promote_image` still selects through fail-soft ledger reads, copies the image and swaps it into the avatar slot, then calls `add_deleted` for the vacated gallery name. This strict read consequently returns a 409 after the promotion has already landed. Preflight both ledgers at the promotion entry point before copying or swapping, consistent with the [S1 associated-effect requirements](docs/superpowers/plans/2026-10-11-roadmap-07-s1-members-field.md#L119).

- [P2] Keep deletion sweeps within the captured campaign lock set — C:/Users/charl/github/grimoire/.worktrees/spec-07-group-membership/backend/src/grimoire/store/overlay.py:2725-2730
  If a dependent campaign is created after this enumeration, `forget_world_record` re-enumerates it while this context still holds the original campaign locks, and `_forget_in_campaign` acquires its lock individually. For example, an original `saltmarch` hold can acquire newly created `realm` after another World operation has acquired `realm` and is waiting for `saltmarch`. This creates a reverse-order wait until timeout and can skip post-delete cleanup. Reuse the captured dependent set throughout the sweep; this requires no S5 discovery barrier and preserves the existing [sorted-lock rule](CLAUDE.md#L1261-L1266).

- [P2] Render membership reversal diffs from the restored value — C:/Users/charl/github/grimoire/.worktrees/spec-07-group-membership/backend/src/grimoire/store/undo.py:664-666
  For an inherited missing-world `pcs:winifred` reference, explicitly binding it and then pressing Undo removes the token under the conservative restoration policy. However, swapping the original display strings makes the Undo row claim `Recorded members: pcs:winifred` and `Effective members: pcs:winifred`, although the owned group now has no members. The new writer therefore produces a Changes diff describing a state it did not write. Format `group_members` reversal displays from the actual before/after values, accounting for the [conservative restore contract](docs/superpowers/plans/2026-10-11-roadmap-07-s1-members-field.md#L234).

## Resolution evidence

All four findings were reproduced before fixes: seven regression cases failed.
Prepared publication now validates kind and safe id before path probes; image
promotion strictly reads both identity ledgers before effects; World deletion
owners pass the campaign set captured by hold_all to the post-delete sweep;
membership reversal diffs format the actual restored value.

355 targeted tests passed (98.08s). The concurrent campaign-creation regression
also passed independently after switching its harness to propagate worker
exceptions through Future.result. Lint remains at 1146 and mypy at 165.
The architectural guard check and subsequent native recheck are recorded in the
execution ledger when completed. No approval is inferred from unresolved reviews.

## Native recheck at 6eb1370f0fc364357f1ee54ec899960de76f9671

Thread `01a12ddc-3971-7803-ae3c-afc0b2df0a82`; installed native review/start API.

The supplied fixes address all four prior findings, but introduce a post-write failure path that loses membership reversal bookkeeping. This was a static snapshot review; Windows gate execution was not independently verified.

Review comment:

- [P2] Journal successful membership reversals when readback fails — C:/Users/charl/github/grimoire/.worktrees/spec-07-group-membership/backend/src/grimoire/store/undo.py:668-670
  If restoration succeeds but the subsequent readback encounters an error, `seal` returns `None`. This branch then raises after `write_value` has persisted the restored membership but before `journal.append` and `journal.mark_undone`: Undo reports a conflict, creates no reversal row, and leaves the original entry unmarked. When the restored value differs from the original expectation, retrying also conflicts. Preserve the existing non-undoable reversal fallback, with an unavailable display if necessary, rather than abandoning bookkeeping after the write.

The new readback finding reproduced on both Undo and Redo. The successful
restoration now records the existing non-undoable reversal fallback, marks the
original completed, and displays its unreadable value without inventing member
tokens. 160 group/lifecycle/Undo/journal/store/route checks passed (63.75s).
Lint and typing remained at 1146/161. A subsequent actual native recheck is
required; this finding is not treated as a review approval.
