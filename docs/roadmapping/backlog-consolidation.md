# Backlog consolidation before code cleanup

**Status: both consolidation waves executed and verified 2026-10-15. Not a code implementation specification.**
**Date: 2026-10-15.**
**Repository:** `charlesmsiegel/grimoire`.
**Do not merge the `roadmapping` branch.** Implemented work goes through its own PRs from current `main`.

## Final status as of 2026-10-26

This document is an **audit trail of decisions from October 15**, not a queue of issues to execute today. GitHub currently shows **zero open issues and zero open PRs**. The four formerly remaining independently actionable issues (#62, #128, #208, #437) were closed as completed on October 16. The ten then-in-review PRs were no longer open. The 19 retired trackers' unimplemented or optional goals remain traceable through [Inventory](inventory.md), [Deferred Systems](deferred-systems.md), [Discretionary Backlog](backlog-discretionary.md), [Archived Issue Text](archived-issue-text.md) and the [Sprint Register](sprint-register.md).

The team moved into technical-debt cleanup and has already landed significant modularization and lint/test work. **Do not restart the pre-cleanup backlog phase** when executing the [current roadmap](execution-roadmap.md).

## Decision

**Original plan:** First land as many practical, relatively independent existing backlog issues as possible, explicitly excluding inventory- and mechanics-related work. **Updated 2026-10-15:** A *simple, mechanics-free Inventory MVP* is now a near-term feature candidate because quantity tracking is immediately useful in monster-catching RP; full inventory/mechanics integration remains deferred. Close issues once their implementations are actually complete and verified. Record the completed capabilities and original product goals in the thematic roadmapping documents without accidentally turning completed work back into new to-dos.

**Then** freeze a baseline for the substantial Engineering Health / Naur Theory Building / Code Simplification wave. Clean up and stabilize the code after major selected backlog modifications have landed, so that refactors aren't invalidated by other in-flight feature work. **Then** improve/calibrate the code-overview and theory-building skills, and only after cleanup turn on the planned hard B+ (>=87) PR quality gate.

This ordering is **a preference for avoiding churn**, not a commitment to finish every unrelated issue before *any* urgent bug/security fix, nor a reason to defer a critical correctness repair. Some cleanup can proceed concurrently when non-overlapping.

## Roadmap transfers completed 2026-10-15

The following **active product goals** were analyzed and documented. **Twelve trackers were retired in the first wave**; the initial exception for #93 and #151 was later reconsidered by the user, who opted to transfer both into the second wave of seven additional trackers. The resulting tracked/closed total is nineteen. None should be counted as shipped functionality:

- **Inventory:** #167, #168, #169, #170, #171 → [inventory.md](inventory.md). The first mechanics-independent counted-holdings slice is now **near-term**; the full functionality is later.
- **World/composition/identity:** #50 (cross-world variants), #42 and #43 (spatial queries/maps), #55 (library activity and impact) → [deferred-systems.md](deferred-systems.md).
- **Retired campaign/time/presentation:** #102 (offscreen NPC/group ticks), #108 (long activities), #140 (export filters/history) → [deferred-systems.md](deferred-systems.md).
- **Second wave moved out of the active backlog:** #93 (cross-campaign scene import), #151 (optional historical prompt replay), #81 (multi-PC), #132 (live preview), #188 (residual auxiliary task kinds), #190 (optional auxiliary accept/discard), #196 (HUD customization) → [backlog-discretionary.md](backlog-discretionary.md). Their original bodies are archived; #93 and #151 are also described in [deferred-systems.md](deferred-systems.md).

[archived-issue-text.md](archived-issue-text.md) preserves the **complete original bodies**, including dated code assumptions and all alternatives. New thematic docs restate actionable requirements against the upcoming architectural work and explicitly avoid prematurely ratifying old preferred solutions.

**Ten open PRs were in review at the time of triage**, including #519–#528. Issues linked to them were deliberately **protected from closure**: #18, #26, #68, #73, #131, #136, #477, #478, #479, #480 and #496. In particular #480 was referenced by PR #523 and should not be retired as “finished” until its residual cleanup is explicitly resolved.

**Disposition convention:** closing as `not_planned` retires a stand-alone tracker because the requirements are instead planned in the long-term roadmap. It does **not** mean we have rejected or implemented the feature. Close only with a comment and a link to the transferred requirements.

---

**Verified first-wave outcome:** 12 issues retired with direct roadmap links (#167–171, #50, #42, #43, #55, #102, #108, #140). #93 and #151 were briefly reopened on a readiness reconsideration, then were closed in the second wave at the user's direction. **Second wave completed:** seven discretionary trackers (#81, #93, #132, #188, #151, #190, #196) → [backlog-discretionary.md](backlog-discretionary.md). The ten open PRs #519–#528 and all their linked issues remain protected. The archive now holds original bodies for **19 unique issues**. **Post-closure verification: 15 open issues remain, of which 11 are referenced by the 10 open PRs, and four (#62, #128, #208, #437) are independently actionable.**

---

## Historical immediate implementation backlog — October 15 snapshot

**Keep OPEN until implementation and review:** #62 (sparse emergent NPC capsule/tags), #128 (prompt inclusion reasons), #208 (macOS launcher icon), #437 (Images library placement and entity browse UX). The active ten PRs and issues they reference are also to stay open until landing and verification. The goal is eventually **zero open issues before the tech-debt sprint**, not to call undone features completed.

---

## Scope triage

Search of open GitHub issues on 2026-10-15 returned 34 issue results. This is a **point-in-time discovery**, not a reviewed issue inventory or guarantee that all titles still describe unimplemented functionality. Some issue descriptions explicitly predate substantial rebuilds; validate on current main before implementing or closing.

**Category A — candidate to land during backlog phase:** non-inventory, non-mechanics and sufficiently independent of upcoming World Resolution/Content Identity/Mechanics contracts. Examples to *evaluate* (not automatically declare ready):
- Image-store operational, maintenance and redundant-sweep improvements: #477, #478, #479.
- Library Images and editor browse UX: #437.
- Prompt inspector explainability: #128, #131, #132.
- Export/character-import/task UX cases, if current code confirms manageable scope: #140, #136, #188, #190.
- Small presentation/launcher fixes where appropriate, e.g. #208.

**Category B — explicitly postpone:** inventory and mechanics work, including the open inventory sequence #167–#171, as well as tasks that substantially touch mechanics even if the issue is not labeled as such. They remain real product requirements for future inventory/mechanics sprints; don't implement them as incidental dependencies of other issues.

**Category C — reconsider/defer despite not being inventory or mechanics:** items whose implementation would bake in assumptions about soon-to-change identity, inheritance, world composition, and world simulation. Representative examples:
- #50: Character variants across worlds, selected per campaign. Its body explicitly depends on reuse/overlays across different worlds: defer to World Resolution/Content Identity studies before implementing a throwaway design.
- #102: NPC and group state advancement on a time skip. Potentially depends on future world simulation and state transition contracts.
- #108: Long activity resolution. Potentially depends on action/resolution mechanics and time/state machinery.
- #42/#43: Spatial model and map work should be checked for compatibility with planned location identity and world composition; they may be independent in part, but don't assume so from titles.

**Category D — deliberate cleanup issues already in backlog:** #480 explicitly tracks image-store code cleanup deferred from previous stages. This belongs naturally to Engineering Health. Decide whether to land it in the backlog phase to reduce future noise or group it into cleanup; do not lose the issue or schedule the same cleanup twice.

The examples above are **starting triage candidates only**, not proof of the individual issue's completeness, priority, or readiness. Inspect the current branch/PRs and the actual affected modules for each.

## Definition of an issue being complete

1. Identify the **current** requirement rather than trust an old issue body or pre-rebuild suggested implementation.
2. Verify the landed PR or current code satisfies the issue's acceptance criteria, with tests and relevant frontend/backend behavior.
3. Close with a reference to the landed PR or concrete evidence for why the work is complete. If only a slice landed, keep the remaining issue open or split it; don't call partial work complete.
4. If the idea is intentionally no longer appropriate, a superseded/won't-do closure needs a clear rationale and a pointer to its successor in the roadmap; this is **not** equivalent to "implemented."
5. Do not close an issue just because its concept now appears in the high-level roadmap.

## How to integrate goals into `roadmapping`

Map each reviewed issue to a **thematic sprint and concrete outcome**, recording a concise disposition:

| Field | Meaning |
|---|---|
| Issue | Link/number and short product goal |
| Disposition | **Landed**, **Partially landed**, **Deferred**, or **Superseded/declined** |
| Evidence | PR/commit and the behavior/acceptance criterion that was actually delivered |
| Roadmap home | Existing sprint/theme, or one new candidate only if genuinely necessary |
| Residual | The unmet product goal and its dependencies, or **none** if complete |

**Do not transplant a closed issue's implementation checklist as a new feature backlog.** Instead credit the delivered capability as an established foundation; preserve still-unmet ambitions separately. If an issue already corresponds to a listed sprint, update that sprint rather than invent another category.

**Examples (illustrative classifications, not verified dispositions):** a shipped image browser counts as present Presentation/Content Ecosystem capability; an unresolved optional card-add-on remains part of Content Composition; a completed prompt-source inspector counts under Context/Reliability rather than a brand-new future sprint.

## Handoff to cleanup

When the chosen issue wave stabilizes:

1. Record a specific **post-backlog main SHA**, the issues landed, postponed or superseded, and which capabilities those changes delivered.
2. Reconcile issue goals with the sprint inventory and product release themes; remove redundant future work and preserve unfulfilled aspirations.
3. Re-run the current `coding-skills/code-overview` at that exact SHA; capture health, theory and measurement evidence and tool/model versions. Do not compare raw scores from different grader versions/judges as though interchangeable.
4. Triage confirmed code debt, then execute behavior-preserving cleanup/refactors on small PRs with characterization tests and the existing gates.
5. Improve the skills to address real false positives, establish stable Naur-theory judging, and finally activate the **B+ (87/100) minimum quality gate** as an independent later phase.

**No issue closures or implementation changes are authorized by this planning note.** It tracks intended sequencing only.
