# Backend package modularization

## Objective and scope

Continue the behavior-preserving work on `refactor/modularize-large-python`
from `a7ebe3961`. The owner asks for full Python packages with smaller,
controllable files, preferably no production file exceeding 1,000 physical
lines. The two completed route packages stay intact. The current inventory
contains 35 production Python files above that target; production Python is
the scope, not large tests or TypeScript components.

Prioritize the seven largest remaining subsystems: scene routes, overlay,
compiled cache, character turns, inference operations, detached runs and
context assembly. Continue through the other oversized production modules;
report an explicit reason for any remaining exception rather than quietly
dropping it. Line count is a review aid, not permission to introduce arbitrary
fragments or change behavior.

## Theory and package boundaries

Recover each subsystem's responsibility, invariants and mutable owners before
moving definitions. A leaf should represent a named domain responsibility;
its imports should expose the direction of dependency. Keep one owner for a
router, registry, lock, thread-local, weak collection, cache, fork-rebound state,
catalog or lifecycle. Consumers of rebound state must read the owner module,
not retain a stale imported value. Preserve original comments and their issue
context. A facade exposes the old interface while leaves own implementation.

Store files remain authoritative Markdown/frontmatter and human-editable JSON;
SQLite remains disposable. Preserve copy-on-write, source provenance, strict
identity-ledger preflights, locking, atomic publication, recovery, synchronization
and mechanics-free play. Keep Python 3.11 syntax, Pydantic 1 and Android base
dependencies/resolver behavior. No new runtime dependencies are needed.

## Compatibility contracts

- Preserve every former public and intentionally exposed private binding,
  including imported dependency modules. Use explicit re-export aliases or
  accurate `__all__`; protect facades before unused-import cleanup.
- Preserve endpoint registration order, overlaps, signatures, dependencies,
  response behavior, markers and OpenAPI. Route leaves share a single router
  and construct routes eagerly. Where cohesive leaves interleave original
  declarations, finalize that router's list in place using an explicit original
  route-key sequence before package initialization completes. Preserve the
  APIRoute objects and reject missing, extra or duplicate keys; no per-leaf
  routers or deferred registration registry.
- Re-exporting does not forward assignment into a function's globals. Inventory
  production dynamic access and test patches. Keep real runtime extension
  points working; update tests to patch the defining owner when their purpose
  is internal fault injection, retaining every behavioral assertion. Do not
  introduce a generic module proxy or a hidden dependency registry.
- Preserve state identity, reset/fork behavior, per-app lifetime, cancellation,
  producer unwind, deadlines, review watermarks and immutable prompt snapshots.
- Preserve observable logger names and persisted module labels. Extracted
  `logging.getLogger(__name__)` and `logs.record(..., __name__, ...)` sites
  retain the original logical module label. Characterize an emitted row and
  keep the existing single-handler/privacy boundary.
- Keep imports at module scope, graph acyclic, and cross-package store imports
  bound to modules. Package conversion cannot justify broad guard exemptions.

## Characterization and guard relocation

Before modifying a subsystem, capture its bindings and the applicable external
contracts from the current source. Reuse existing behavioral tests; add missing
characterization before a potentially semantic change. Verify source bodies
remain equivalent for straightforward moves and independently inspect changes
required to access a shared owner through a module.

Architecture guards that name a former file must follow the actual owner and
continue inspecting every relevant leaf. Add or retain forbidden-sibling and
unsafe-call negative controls. Preserve exact lock classifications; relocating
unreviewed writers must not silently label them reviewed or widen the backlog.
Do not use package-wide import/SQL/write permissions to obtain a green gate.

## Validation and diagnostic accounting

The starting pinned baselines are Ruff 764, mypy 141, ESLint 307. Match relocated
diagnostics by rule, message and original definition/source, not total alone.
Regenerate through the official ratchet only for proven relocations or real
reductions. Do not weaken lint rules, coverage floors, guards or assertions.

Run focused tests and applicable guards for each extraction before expanding.
At integration run every constituent of `make check`: `check-lint`,
`check-mypy`, `check-templates`, `check-eslint`, `check-web`, `check-py` and
`check-pydantic1`, using the configured dependencies and this worktree's source.
Both frontend typechecking and coverage are mandatory even without frontend
source edits. Make is absent locally: execute its recipes directly
and report that distinction. The primary configured interpreter is Python 3.14;
the isolated Pydantic-1 environment uses installed Python 3.12. Python 3.11
runtime/APK checks must be distinguished from syntax/Android guards if unavailable.

## Delivery and exclusions

Work in reviewable local commits on the existing branch, preserving local work.
Complete the required independent Codex spec, plan, implementation and final
spec-versus-diff checkpoints. Independent package work may run concurrently with
exclusive file ownership; shared guards, baselines and integration remain with
the coordinating agent. No push, PR, merge, real library access, frozen-home
regeneration, feature changes, Mechanics II or future B+ gate activation.

At completion report package responsibilities and theory, API/patch/state
compatibility decisions, diagnostic before/after, tests and actual results,
remaining files above 1,000 lines and reasons for any exceptions. A new
production file above the target requires a specific cohesion/state reason;
moving a monolith unchanged into `__init__.py` does not meet the objective.
