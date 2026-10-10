# 01i-S4: The Models page readout — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The settings view's role cards, route rows and Embedding card each
carry `limits` (the resolved primary's window and max output, the riding
fallback's window, and the prompt ceiling with its `reason`), and the Models
summary shows the window after the rate -- `200k window`, or `window unknown`
with a **Set** link to the model's size form -- the fallback's window on the
fallback line, and a ceiling `reason` in the problem style.

**Architecture:** `store/inference/settings.py` gains `_limits(resolved,
*, reserve=None)`, computed from the same resolution as `resolves` and
`rate`: `null` when nothing resolves or the resolution's `decision_mode` is
`native`; otherwise `{"window", "max_output"}` (the primary's,
`limits.limit_body`), `"fallback_window"` (the riding fallback's, `null`
when none rides) and `"ceiling": {"tokens", "binding", "reason"}`
(`limits.prompt_ceiling`). The Embedding card computes it from the same
`embed_space.resolution` it already reads, only when it is on, with
`reserve=0` (an embedding has no reply to hold back). Frontend: the card
types gain `limits`, and `RoleRow` / `EmbeddingRow` render a window readout
beside the rate, built from `providerPaths.modelLimitsPath` (S2).

**Tech Stack:** Python 3.11, pytest; React + TypeScript, vitest.

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01i-context-window-fact-design.md` §6.3, §7 (C3), §9 (settings view, Models summary).

## Global Constraints

- The server decides: the frontend holds no rule for when a window is shown (a `null` `limits` draws nothing), the decide-note precedent.
- Display-only: nothing on the Models page writes a limit.
- 01s has landed its summary (`RoleRow`, `RateLine`, the fallback line), so the readout goes there, after the rate, not on the old role cards.
- The window is shown compactly (`128k`, `200k`, `1M`), with the exact figure in the element's `title`.

## Review Focus

- A native Decision card and a native decide route: `limits` is `null` though the resolution has attempts.
- `fallback_window` is the RIDING fallback's: a non-riding fallback (a decide resolution's separate stage, or one known incapable) is `null`.
- `ceiling.binding` serialises as a two-item list (JSON has no tuple).
- **Set** only where the window is unknown AND a model is named; the link encodes per segment.

---

### Task 1: the settings view

**Files:** Modify `backend/src/grimoire/store/inference/settings.py`; Test `backend/tests/test_inference_settings.py`.

**Interfaces:** `settings._limits(resolved: ResolvedInference | None, *, reserve: int | None = None) -> dict | None`; each of `_role_card`, `_route_row`, `_embedding_card` returns `"limits"`.

- [ ] Failing tests: `test_each_cards_limits_follow_its_resolution` (Primary with a catalog row: window/max_output with sources, ceiling tokens = window − default reserve, binding list), `test_limits_are_null_when_nothing_resolves`, `test_a_native_decide_card_and_route_have_no_limits`, `test_the_riding_fallbacks_window_rides_the_card` (and binds the ceiling), `test_a_ceiling_reason_shows_when_the_preset_reserve_exceeds_the_window`, `test_the_embedding_cards_limits_reserve_nothing`.
- [ ] Implement; run `tests/test_inference_settings.py` → PASS.

### Task 2: the summary readout

**Files:** Modify `frontend/src/api/types.ts` (`CardLimits`; `RoleCard.limits`, `RouteRow.limits`, `EmbeddingCard.limits`), `frontend/src/components/models/RoleRow.tsx` (`windowWords`, `WindowReadout`, the fallback line), `frontend/src/components/models/rates.ts` or a new `limits.ts` beside it; Test `frontend/src/routes/ModelsView.test.tsx`, `frontend/src/components/models/limits.test.ts`.

- [ ] Failing vitest: `a known window shows after the rate`, `an unknown window offers Set, to the model's size form` (`/providers/saltmarch/models/vendor/m?edit=limits`), `the fallback line shows the riding fallback's window`, `a ceiling reason shows under the row`, `no window where limits is null` (native Decision); `windowWords` compact forms.
- [ ] Implement; `tsc -b`; vitest; eslint ratchet.

### Task 3: verify and commit

- [ ] Full backend suite and full vitest; ratchets.
- [ ] Commit `01i-S4: the model's window on the Models summary`.

## Plan gate (substitute review, 2026-10-10)

Adversarial self-review against spec 6.3 and the code (no Codex CLI, no
subagent tool).

- **Re-read: 01s has landed** on this branch's base as far as the summary
  row: `_rate` on every card and row, and `RoleRow`/`EmbeddingRow` with
  `RateLine` and the fallback line. Section 6.3 therefore applies as written,
  and the "no 01s" fallback (today's role cards) is not needed.
- **Folded: the Embedding card's ceiling.** `prompt_ceiling` with no reserve
  would hold back the default reply reserve for a call that has no reply;
  the card passes `reserve=0`, so its `tokens` is the window itself. A plain
  `prompt_ceiling(resolved)` is still what every generative card carries.
- **Folded: `fallback_window` is `null`, not absent**, when no fallback rides,
  so the frontend type is one shape.
- **Route rows** carry `limits` (the server half), but the summary draws only
  role rows today; no route-row readout is added, since no route row is on
  the summary. The type carries it for when one is.

## Review and final gate (substitute review, 2026-10-10)

An adversarial self-review of the slice diff against spec 6.3, 7 (C3) and 9,
and of the whole 01i diff against the spec's Contract, stood in for
`/codex:review` and the final spec gate (no Codex CLI, no subagent tool).
Section 9's settings-view case is `test_inference_settings_limits.py` (nine
cases: limits follow `resolves` on a card and a route, unknown, a stated
window, `null` when nothing resolves, `null` on a native decide card and
route, the riding fallback's window binding the ceiling, a ceiling `reason`
from a preset reserve above the window, the Embedding card, a campaign
view). The Models summary vitest cases cover a known window, `window
unknown` with **Set** linking `/providers/saltmarch/models/vendor/m?edit=limits`,
the fallback line's window, a ceiling `reason` in the problem style, and no
window where `limits` is `null`.

- **Folded: the Embedding card's reserve.** An embedding has no reply, so
  its ceiling is computed with `reserve=0` (the window itself) rather than
  holding back the default reply reserve; every generative card carries a
  plain `prompt_ceiling(resolved)`.
- **Folded: `fallback_window` is `null`, not absent**, when no fallback
  rides, so the type has one shape.
- **Folded: the window is said compactly** (`128k`, `200k`, `1M`), with the
  exact figure in the readout's `title`.
- **Drift, deliberate:** route rows carry `limits` on the server and in the
  type, but no route row is drawn on the summary, so no route-row readout is
  added; the edit form's task rows are 01s's to extend.
- **Contract check, whole spec:** C1 (`wire.Limits` on every resolver-built
  target, `Attempt.limits`, user → catalog → unknown, no new read, never
  raises, never 0) -- S1; C2 (`prompt_ceiling` / `reply_reserve` /
  `Ceiling`, the riding-fallback walk, per-call cap, `max_output` cap that is
  never the reserve, `None` vs 0, `reason`, tuple `binding`) -- S1; C3
  (stated facts and the facts route -- S2; `model_window` in every
  breakdown -- S3; the settings view's `limits` and the summary readout --
  S4). Nothing dropped.

Results: full backend suite 16383 passed, 5 skipped (the root-only
`test_atomic.py` case deselected); ModelsView + limits vitest 79 passed;
full vitest 210 files / 4567 tests passed; `tsc -b` clean; ruff, mypy and eslint ratchets
all at baseline.
