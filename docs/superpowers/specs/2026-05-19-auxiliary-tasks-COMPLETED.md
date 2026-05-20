# Auxiliary Tasks — Completion Notes

Tracks how the auxiliary-tasks implementation landed and any deltas from
the design (`2026-05-19-auxiliary-tasks-design.md`) and plan
(`../plans/2026-05-19-auxiliary-tasks.md`). Read alongside those, not in
place of them.

## Branches landed

| Branch | Plan section | Status |
|--------|--------------|--------|
| A — `AuxiliaryTask` / `AuxiliaryResult` / `CommitAction` types + 7 Jinja prompts | A1, A2 | shipped |
| B — Context-builder branch on `auxiliary_task`; per-task budget plan | B1 | shipped |
| C — `orchestrator/auxiliary_runner.py` + `_inflight_aux` slot | C1 | shipped |
| D — Accept dispatch per task kind (submit/replace/append/copy/draft) | D1 | shipped |
| E — 7 start endpoints + accept/discard + `/in-flight` + WS aux_* events | E1 | shipped |
| F — 7 task-kind frontend surfaces + SideHud in-flight badge | F1 | shipped |

## Deltas vs the design

### SideHud in-flight indicator landed as a header badge

The design said "dedicated icon + color in the HUD status area … click
expands to a panel listing in-flight tasks with cancel controls." The
implementation is a small header pill that hides itself at zero, shows the
in-flight count when non-zero, and toggles a list of tasks (with a
**Discard** action per row) when clicked. No dedicated SideHud widget
config slot — it lives in the existing `<header className="side-hud-header">`
alongside the Refresh button.

Files:
- `frontend/src/routes/campaign/SideHud/AuxInflightBadge.tsx`
- `frontend/src/routes/campaign/SideHud/useAuxInflight.ts`
- `frontend/src/routes/campaign/SideHud/SideHud.tsx` (mount point)
- `frontend/src/routes/campaign/SideHud/__tests__/AuxInflightBadge.test.tsx`
- `frontend/src/index.css` (`.aux-inflight-badge*` rules)

### WS events fire directly, not through `_FORWARDED_EVENTS`

The plan suggested adding `aux_token` / `aux_complete` / `aux_error` to
`backend/src/grimoire/api/stream.py:_FORWARDED_EVENTS`. The runner instead
pushes these directly via `orchestrator._push_to_ws(...)`, so they reach
campaign subscribers without going through the event bus. Frontend
subscribes with `useCampaignEvent(["aux_complete", "aux_error"], ...)`
verbatim.

### `/auxiliary/in-flight` is a "completed but not committed" view

`_inflight_aux` is populated only after streaming finishes (see
`orchestrator/auxiliary_runner.py:143`), so the badge reflects tasks that
are awaiting accept/discard rather than tasks mid-stream. The streaming
UX itself is handled by the per-entry `AuxPanel` components.

## Not shipped

- A real-app manual smoke pass across all 7 task kinds (Integration check
  steps end1–end3) — owned by the human operator, not blocking the code.
