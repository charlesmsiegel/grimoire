# Agentic Workers — cross-cutting architecture

**Status:** long-term architectural capability / design direction, not an approved implementation spec.

**Implementation status update (2026-10-26):** The owner confirms **the bounded-investigation roadmap (12) has landed**, along with earlier 01g tool-calling infrastructure and 01–12 foundations. The October 15 partial-implementation snapshot below is historical, not a remaining ticket. The long-term sprint here is **broader, optional multi-step in-app authoring/world-maintenance workers**, requiring guarded proposals and reliable state writes. No requirement to run agents for routine narration. See [Execution Roadmap](execution-roadmap.md) and [World Maintenance](world-maintenance.md).
**Date:** 2026-10-15

## Product intent

Grimoire should be able to perform open-ended multi-step work **inside the application**, including integrating an imported character card, updating related lore/groups, reconciling an internal lorebook and suggesting coherent edits across multiple records.

This must remain **optional**: using Claude, Codex, other coding agents, scripts or manual Markdown/JSON editing for maintenance continues to be fully supported. There is no requirement or intention to make Grimoire the only authoring interface.

Agentic workers belong alongside cheap decision models and single-shot narrative generation, not in place of them:

| Mode | Typical use | Expected form |
|---|---|---|
| Deterministic or statistical decision | Speaker/attitude/action selection, constraint checks, sampled actions | Small bounded computation |
| Narrative generation | Character dialogue, description, completed-outcome narration | Single generation / stream |
| Agentic worker | Multi-step integration, investigation, preparation or repair with unknown intermediate information needs | Tool use, iteration, validation, bounded or resumable job |

**Default:** keep the synchronous roleplay turn short and inexpensive. Invoke agentic workers when the task warrants iteration/tool use, possibly explicitly at the user's request, or with an appropriate authorized trigger. Not every NPC becomes a long-running independent agent.

## Existing code foundation (main, inspected 2026-10-15)

- 01g Tool Calling is marked landed in `ROADMAP-CHECKLIST.md`: provider-neutral tool loops (`inference.run_tools`), cost/output/time/tool budgets, metering, and Decision-as-tool.
- **Historical October 15 snapshot only:** roadmap 12 Bounded Agentic Investigation then had partial E2/E3 toolset/budget/trace code and deferred E1. This is **superseded** by the owner's confirmation that roadmap 12 has since landed. Verify current route/feature switches when defining later agentic-workflow work.
- Existing external `.claude/skills/world-card-integration/`, `populate-world-content/`, and `grimoire-store-editing/` offer mature behavioral examples for world curation. Do not rewrite these as an unrelated second system without evaluating reuse.
- Existing `routes/investigation.py` uses `store/investigation/` for a read-oriented, traced toolset and independently validated outcome. This is a useful **pilot**, not necessarily a general-purpose authoring-worker contract.

A broader in-app worker framework should build on this orchestration substrate rather than introduce duplicate tool runners, inference routing, cost tracking and cancellation semantics.

## Representative worker classes

1. **World curator:** import/integrate characters and lorebooks; identify relevant world records; propose edits, group memberships, relationships, greetings, tags and voice changes; preserve provenance.
2. **Consistency auditor:** investigate contradictory lore, stale references, duplicate candidates, broken group links or unresolved continuity issues, with a cited change proposal.
3. **Scenario planner:** prepare obstacles, encounters, hooks and supporting NPC/world material outside the latency-sensitive turn; adapt to campaign tuning and chosen mechanics without authoritatively rolling outcomes.
4. **World simulator:** bounded offscreen developments for factions/NPCs, guided by explicit simulation state and deterministic policies; agentic interpretation only where needed, with consequences validated before committing.
5. **Exceptional investigator:** when ordinary retrieval or an explicit decision model genuinely cannot establish enough evidence, use constrained tools to find relevant records rather than hallucinate. The existing bounded-investigation work already pilots part of this.

These are **task-specific uses of shared infrastructure**, not automatically five separate agent frameworks or requirements that all ship in v1.

## Execution and authority boundary

A useful job lifecycle is:

`request / trigger -> scoped read snapshot -> iterative tool-using work -> typed proposed changes / answer -> deterministic validation -> optional review -> guarded apply -> report + cache refresh`

- Workers may plan and iterate using bounded tools; the result is *not automatically authoritative*.
- Read-only investigation and world mutation are different privileges. A work request specifies a target scope (world, campaign, or explicitly authorized shared library), capabilities, cost/time ceilings and approval policy.
- A worker that proposes a new character or group membership must use source-aware world identifiers. It must not mutate an imported bestiary or another world merely because it appears in the effective view.
- LLM-proposed changes become typed operations with target identities, expected revisions/hashes, provenance, preconditions, and validation results. Deterministic store code applies approved operations to **human-editable authoritative files**.
- Offer configurable autonomy: preview-only, review before apply, or automatic apply for a narrowly allowed class of non-destructive validated changes. Never imply every change requires manual approval or that freeform raw file edits must disappear.
- Expensive investigation or maintenance must not hold campaign/world locks across model calls; preflight and optimistic revalidation against current records precede a short guarded apply.
- For long jobs, consider persistent resumable job records, cancellation, progress and audit traces. Design for interruption, tool failure and partial work without silent divergence.
- Run budgets, model routing, cost-per-job, and security/permission boundaries are shared concerns with existing 01g/12.
- Workers can use a scratchpad or derived index internally, but the source of world/campaign truth remains Markdown and the store's existing editable JSON, not model chat history or a hidden canonical DB.

## Potential roadmap framing

Name **Agentic Workers** or **Agentic Workflows** explicitly in the cross-cutting Decision & Generation Systems area. Do not conflate it with the separate **World Maintenance** product sprint, which is one consumer. Continue using `docs/roadmapping/world-maintenance.md` for its UX and content-authoring requirements.

The key architecture question is not "How many different agents do we build?" but **"What safe, reusable job/tool/change-set contract lets any agent operate against the exact same resolved world as gameplay and an external editor?"**

## Long-term feature consumers migrated from backlog

Time-skip NPC/group updates [#102](https://github.com/charlesmsiegel/grimoire/issues/102) and long-activity resolution [#108](https://github.com/charlesmsiegel/grimoire/issues/108) now live in [deferred-systems.md](deferred-systems.md). A task-specific agentic worker can be useful for exceptional interpretation/offscreen developments, but deterministic clocks, legal effects, source-scoped change sets, and review are authoritative. These feature goals remain outstanding even after their previous issue trackers close.

## Deferred design questions

- Worker API and persistence/resume lifecycle; which existing `runs` types can be reused?
- Separate read, proposal, and write tool capabilities and their authorization model.
- Human review and auto-apply policies across world, campaign and library scope.
- Which operations can be treated as a single recoverable change set without switching from file authority to a database?
- Correctness under concurrent external edits and UI writes.
- How to benchmark improvements in authoring time, RP coherence, cost and safety against the existing Claude/Codex workflows.
