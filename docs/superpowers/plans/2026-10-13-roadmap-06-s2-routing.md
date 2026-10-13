# 06-S2 Route store-writing workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the canonical store-editing procedure the closing handoff for outside-app writes without adding duplicate instructions or redundant syncs.

**Architecture:** AGENTS.md points to the canonical and adapter directory. Four existing skills add links at their completion points; script-backed workflows distinguish already-synced script writes from manual follow-up edits.

**Tech Stack:** Markdown, existing pytest documentation and skill guards, the current cache CLI and store writers.

**Spec:** [06](../specs/2026-10-09-roadmap-06-store-editing-skill-design.md), sections 5 and 9; delivers 06-C1 in full. Hard need: 06-S1.

## Global Constraints

- The canonical procedure remains `.claude/skills/grimoire-store-editing/SKILL.md`; adapters hold only discovery metadata and a pointer.
- AGENTS.md continues routing to CLAUDE.md and uses fresh wording; do not duplicate a run longer than 16 words.
- Add no sync algorithm, runtime behavior, real library data, fixture rewrite or unrelated skill cleanup.
- Existing script entry points already collect their writes; handoffs apply to additional outside-script edits. A new cold World needs no previously hot work rebuilt.
- A slice is Landed only when it is on main; completion in this worktree does not tick that box.

## Review Focus

- A script-backed World creation is mistaken for unfinished cache work: explain that the script already syncs, and cold results are expected.
- Ingest is rerun solely to refresh an already-completed record: direct extra edits to the sync procedure without repeating ingestion.
- Another agent finds only AGENTS.md: the canonical link and adapter directory both resolve.
- The closing handoff restates cache flags or privacy rules: keep it a short relative link and let S1's duplication guard enforce the boundary.
- A skill dry run touches the real bootstrap pointer or sends requests: pin scratch paths before imports, disable embeddings and use the existing fake client where one is required.

## Task 1: Routing links and application verification

**Files:** Modify `AGENTS.md`, `.claude/skills/world-card-integration/SKILL.md`, `.claude/skills/create-world/SKILL.md`, `.claude/skills/populate-world-content/SKILL.md`, `.claude/skills/ingest-campaign-log/SKILL.md`; update `ROADMAP-CHECKLIST.md`, this plan and the execution record.

**Interfaces:** Consume S1's canonical, adapter, guard and current `cache_sync.collecting(*, embed=False, deleted=...)`; produce discoverable completion links. No product interface changes.

- [x] Inspect the four completion points and existing script self-sync before editing. The existing S1 link and routing guards are the safety net; this low-impact prose handoff needs no exact-wording test.
- [x] Add one fresh AGENTS.md sentence naming and linking the canonical procedure and `.agents/skills/` directory.
- [x] Add a relative handoff to the World integration and populate workflows once records are written. Leave their existing content intact.
- [x] Add a handoff for edits outside `create_world.py` and explain that script writes already sync and a cold new World has nothing hot to warm. Add the equivalent outside-script handoff to ingest; do not repeat an already-done ingestion to run sync.
- [x] Run skill/link/routing, docs and interpreter checks; inspect all new targets for exact spelling and anchors.
- [x] Perform the spec's real dry run in a disposable `GRIMOIRE_HOME`, with bootstrap pointer, machine lock location and compiled device ID isolated as in the existing verification/test harness. Seed placeholder records via existing store functions, read the old state, then rewrite the anchor and delete the named lore entry in one `collecting(embed=False, deleted=[...])` block.
- [x] Read back the changed anchor through `voice_anchors.read`, confirm the intended lore path is absent, verify the report is complete, then run the no-embed CLI verification and confirm exit 0. Keep counts/statuses only in the execution record; no scratch contents or actual library access.
- [x] Use an independent fresh-context agent with the canonical skill to evaluate the safety scenarios named by the corrected spec. Report actual observations, including deliberately untested behavior. Do not assert that model behavior is proven by the static guard.
- [ ] Run the full `make check` gate with explicit shared `PY`. Address actual failures and ratchet updates without broadening scope or fabricating passing results.
- [ ] Run the required Codex diff review, then the adversarial diff-and-originating-spec review. Resolve findings and record both slices' evidence, theory note and verification output.
- [ ] Update only the stages actually completed for 06, and commit S2 separately. Leave 06's spec and slice Landed flags clear. Do not infer gates for other specs.

## Verification

Focused checks: the same S1 trio plus existing collecting/CLI regressions. The final evidence is `make check PY=C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe` from this worktree; on macOS/Linux select the primary checkout's `backend/.venv/bin/python`.

The user requested implementation in this worktree. Leave the completed branch reviewable there; do not merge, push, publish or open a PR unless requested.
