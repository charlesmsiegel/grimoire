# S2 native implementation review

Actual installed runAppServerReview at843d7897e, thread `01a12f25-6170-7af0-aecc-d8386d43cf8e`. Verdict: two supported capture findings requiring correction.

The capture can mix stores during a data-directory switch, and native shadowing can replace an owned roster with world membership. This review used only the supplied frozen snapshots; no tools or tests were run, and the complete branch gate remains outstanding.

Full review comments:

- [P2] Resolve the world binding from the captured campaign root — C:\Users\charl\github\grimoire\.worktrees\spec-07-group-membership\backend\src\grimoire\store\overlay.py:938-940
  If the data directory switches during the compute capture after `v._croot` is frozen, `v.wroot` calls `wroot_of(cid)` against the new store, while ledgers and owned groups still come from the old store. With matching campaign IDs, the returned index can combine old-store owned groups with new-store inherited groups—an overlay belonging to neither campaign. The preflight mismatch merely returns this mixed computation uncached. Resolve the world binding from the captured campaign root instead of resolving the campaign ID again.

- [P2] Preserve captured owned IDs before checking native aliases — C:\Users\charl\github\grimoire\.worktrees\spec-07-group-membership\backend\src\grimoire\store\overlay.py:946-949
  On Windows with a case-sensitive campaign `groups/` directory, an owned `salt-circle.MD` is accepted by `collect()` because `WindowsPath.match("*.md")` is case-insensitive, but this lowercase `.md` existence probe fails. The world `salt-circle.md` is consequently included too, and `index_from_rows()` overwrites the captured owned roster with the later world row. The inverse then contradicts `overlay.list_entities()`, which suppresses inheritance using the captured owned IDs. Check those IDs before applying the additional native-alias probe.

Both findings reproduced (2 RED). Captured logical store root plus root-owned world binding prevents mid-capture pointer switches from mixing stores. Captured owned IDs suppress same-ID inheritance before additional native alias lookup, including accepted `.MD` sources. Targeted GREEN3 includes the existing World-rebinding control. Corrected native recheck and final originating-spec gate remain pending.
