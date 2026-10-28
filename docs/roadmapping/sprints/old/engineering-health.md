# Engineering Health

**Priority:** continuing, independent engineering investment. **Focus:** residual debt, not repeating already-completed bulk Python package splitting.

## Goal

Make Grimoire easier to understand, extend and trust despite extensive agent-authored code. Reduce actual defect risk and conceptual complexity, not merely file sizes or metric scores.

## Work to plan

- **Theory Building and Code Simplification:** recover the world/problem model in high-change subsystems; rehearse plausible future requests; eliminate accidental layers, repetition-with-variants and parallel implementations. Favor one meaningful domain abstraction over a wrapper full of flags.
- **Backend Refactoring / Code Hygiene:** clarify store, route, context, model-routing, actor and derived-cache responsibilities. Existing module facades and state-owning leaves should be respected; no blind re-splitting by line count.
- **Type Safety:** reduce pinned mypy debt and frontend type gaps; favor precise boundary types, stable API contracts and error taxonomies over broad Any, assertions or ignores.
- **Dependency and Dead-Code Hygiene:** remove proven-unused code, stale compatibility paths and import cycles only after establishing callers, dynamic registration and Android/import behavior.
- **Test Hygiene and Quality Assurance:** simplify repetitive setup and enormous suites while preserving their unique failure modes. Add characterization and fault-injection coverage for state, lifecycle, cancellation and synchronization.
- **Migration Safety / Concurrency / Error Handling:** audit locks, independent file writers, source changes, retries, crashes, undo, external edits, fail-soft reads versus strict writes. Preserve existing recovery invariants.
- **Observability / Documentation / Security Review:** make meaningful failures, trace/cost attribution and ownership legible. Review untrusted imported content, tool permissions and path handling. Update current docs and tests with each behavior-preserving change.
- **Performance Optimization:** target measured bottlenecks in test loops, prompt assembly, search, render and concurrency; don't optimize a hypothetical hot path.
- Keep Ruff, mypy and ESLint rules enabled and their committed per-file/rule ratchets accurate. Improvements reduce baselines; never rebaseline upward to hide regressions.

## Proving scenarios

An agent can correctly explain why a store operation is authorized and how to add a new entity kind without duplicating write rules; a deterministic state mutation survives interruption, retry and external edit; a representative previously awkward feature crosses fewer independent boundaries; old campaigns and both Python/Pydantic runtime profiles still work.

## Dependencies and boundaries

Independent of gameplay features, but coordinate with source-aware World Resolution and mechanics spikes to avoid repeatedly moving the same interfaces. **Do not** treat the previously implemented production Python package modularization, frontend API/type splitting or test fixture consolidation as unfinished tasks. The separate [Skill Calibration](skill-calibration.md) and [Quality Gate](quality-gate.md) sprints follow substantive cleanup.

## Open questions

- Which current high-churn surfaces have the greatest *verified* conceptual and correctness debt, rather than cosmetic size?
- What representative modification rehearsals should test each recovered theory (world imports, sheet conversion, new entity kinds, capture transactions, tools)?
- Which residual lint classes are genuinely mechanical versus require behavioral or API changes?
- Where should compatibility shims be kept intentionally, and which are safe to retire only with migration tests?
- How much fixture consolidation improves clarity without losing independent failure detection?
