# Agentic Workers

**Priority:** incremental read-only/optional tooling; guarded multi-record editing after Change Sets.

## Goal

Enable bounded, inspectable **multi-step work inside Grimoire** when the result really benefits from tools, iteration and cross-file reasoning. Do not turn every normal roleplay turn into an open-ended agent loop.

## Work to plan

- Build a reusable job model for a worker's target scope, tool capabilities, input context, state, budgets, model routing, trace, cancellation, retry, resumability and user-visible progress.
- Reuse provider-neutral tool calling, decision-as-tool, cost/time/token/tool-call caps and bounded investigation foundations. Do not recreate already implemented per-task inference and tracing.
- Distinguish **read tools**, **proposal tools** and **write/apply tools**, each with explicit authority. Workers return structured evidence and a proposed Change Set; deterministic writers apply authorized results with fresh precondition checks.
- Candidate workers: imported card/lore/group reconciliation, consistency auditing, scoped worldbook search, source repair, scenario preparation, optional exceptional historical/context investigation, and limited offscreen-world assessment.
- Perform model/tool exploration outside file locks. A short guarded write phase revalidates current state to avoid stale proposals or racing with external edits.
- Make interruption, timeouts, malformed tool output, missing sources, cancellation and duplicate retries understandable and recoverable.
- Collect accuracy/coherence, authoring time saved, tokens/cost per job, number of edits reviewed, user corrections, and unjustified mutation rate.
- Keep external coding agents, scripts and plain file edits first-class alternatives. An in-app worker must not become an exclusive authoring path.

## Proving scenario

A user requests a world consistency review. The worker checks three related sources, identifies one genuine missing group link and one *possible* duplicate, proposes an evidenced change set and stops for approval. An external file edit before acceptance triggers a stale-base warning, not silent overwrite. Cancellation during investigation leaves zero canonical changes.

## Dependencies and boundaries

[World Maintenance](world-maintenance.md) is a *consumer* with its own editor UX; this file is the general worker contract. [World Resolution](world-resolution.md) supplies consistent source-qualified reads; [State Transactions](state-transactions.md) and Change Sets control authorized mutation. Cheap decision models and normal narration remain covered by [Generation Systems](generation.md).

## Open questions

- What job state and persistence/resume lifecycle can reuse existing run/trace infrastructure?
- What specific tool permissions and delegation rules separate viewing, proposing, editing and applying?
- Where should the reviewer grant blanket low-risk auto-approval, and how is it scoped/revoked?
- How is interrupted work resumed safely after changed files or dependency revisions?
- What progress, uncertainty, reasoning trace and cost reporting are appropriate to expose to users?
- Which benchmark against external coding-agent/manual workflows establishes that in-app workers are useful rather than a duplicate UI?
