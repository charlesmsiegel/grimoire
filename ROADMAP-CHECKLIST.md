# Roadmap checklist

> **Working file. Delete it once the roadmap has landed.** See
> [Final step](#final-step-delete-this-file) at the end of this file. It is a
> ledger of the roadmap's specs: what each one owes the others, and what is
> done. The design itself lives in the specs. Tick a box in the same PR that
> makes it true.

Specs live in `docs/superpowers/specs/2026-10-09-roadmap-<id>-<slug>-design.md`.
Each spec has two tables: **Depends on** and **Required by**. Both cite
contract IDs from this file. For example, `01g-C2a` is contract item 2a of
spec 01g. When a spec gains, drops, splits or renumbers a contract item,
update this file in the same PR. Each spec also breaks into slices
(`01g-S4`), and the [slice graph](#slice-graph) says which slices each one
needs, across specs.

## Lifecycle of a spec

1. `drafted`
2. `spec gate`: `/codex:adversarial-review`, or a recorded substitute. `[~]`
   means a substitute review is folded in and the Codex gate is still owed.
3. `plan`
4. `plan gate`
5. `implemented`
6. `/codex:review`
7. `final gate`: the diff checked against the spec
8. `landed`

## Status

"Can start when" means the earliest point at which the plan's **first** slice
can start. Later slices may wait on more, as listed under "Dependency edges"
below.

| ID | Spec | Can start when | Drafted | Spec gate | Plan | Landed |
|----|------|----------------|:-:|:-:|:-:|:-:|
| 01 | Inference backend refactor (`2026-10-07-inference-backend-refactor-design.md`) | — | [x] | [x] | [x] | [x] |
| 01s | Inference settings group (`2026-10-09-inference-settings-group-design.md`) | now | [x] | [x] | [x] | [ ] |
| 01a | Eval cost, latency and token reporting | now | [x] | [~] substitute | [ ] | [ ] |
| 01b | Decision capture at every decide site | now | [x] | [~] substitute | [ ] | [ ] |
| 01c | Decision distributions and seeded sampling | now (switching a task on waits for 01a) | [x] | [~] substitute | [ ] | [ ] |
| 01d | Decision escalation and per-task policy | now (enabling escalation waits for 01a) | [x] | [~] substitute | [ ] | [ ] |
| 01e | Decision vocabulary: Rank, finer Score, MultiSelect, Joint | now | [x] | [~] substitute | [ ] | [ ] |
| 01f | Structured generation | now | [x] | [~] substitute | [ ] | [ ] |
| 01g | Tool calling, Decision as a tool, run budgets | 01f | [x] | [~] substitute | [ ] | [ ] |
| 01h | Embedding options, async embed, embedding evals | now (C6 waits for 01a) | [x] | [~] substitute | [ ] | [ ] |
| 01i | Context window as a resolved model fact | now | [x] | [~] substitute | [ ] | [ ] |
| 02 | Decision integration (what 01's slices F–H did not land) | 01a, 01b, plus 01c/01d per feature | [x] | [~] substitute | [ ] | [ ] |
| 03 | Content-addressed compiled cache | now | [x] | [~] substitute ×2 + PR Codex | [ ] | [ ] |
| 04 | Instant Worlds, Campaigns, Todo and shell | 03 | [x] | [~] substitute | [ ] | [ ] |
| 05 | Direct-edit cache sync | 03, 04 | [x] | [~] substitute | [ ] | [ ] |
| 06 | Store-editing skill | 05-C2 | [x] | [~] substitute | [ ] | [ ] |
| 07 | Explicit group membership | now | [x] | [~] substitute | [ ] | [ ] |
| 08 | Derived history SearchDocuments | 03 (C2c waits for 05) | [x] | [~] substitute | [ ] | [ ] |
| 09 | Hybrid historical retrieval | 08 (the turn path also waits for 01h-C4) | [x] | [~] substitute | [ ] | [ ] |
| 10 | Retrieval query planning | 09, 01f | [x] | [ ] | [ ] | [ ] |
| 11 | Epistemic history retrieval | 09 (the Decision stage waits for 02-C5a) | [x] | [ ] | [ ] | [ ] |
| 12 | Bounded agentic investigation | 01g, 01i, 08, 09 (RP mode also waits for 10, 11) | [x] | [ ] | [ ] | [ ] |
| 13 | Mechanics II (supersedes parts of `2026-10-04-mechanics-ii-design.md`) | now for II-A..II-D; C3 waits for 01c | [x] | [ ] | [ ] | [ ] |

## Parallel lanes

- **Now:** 01a, 01b, 01e, 01f, 01i, 03, 07, 13 (II-A to II-D). 01c, 01d and
  01h land their mechanisms now, and switch a task on only with 01a's
  evidence.
- **Decision lane:** 01a + 01b → 01c + 01d → 02 → 11 (Decision stage). 01e
  feeds 02, 09 and 13.
- **Generate lane:** 01f → 01g → 12.
- **Cache lane:** 03 → 04 → 05 → 06. 03 + 05 + 01h → 08.
- **Retrieval lane:** 08 + 01h-C4 + 01i → 09 → 10 (with 01f) → 11 → 12.

## Contracts

These are headlines. The owning spec's Contract section is authoritative.

### 01a: Eval cost, latency and token reporting
- [ ] **01a-C1** Per-case and per-call wall time, tokens and the three money
  columns, never added together. Aggregated per route, backend and `hop`. A
  play case can be summed across its tasks. An absent price is never shown
  as zero.
- [ ] **01a-C2** Live evals are metered by the production meter inside a
  throwaway home. Rows are copied to the run file stamped `scope: "eval"` and
  never reach the library's ledger. A tripwire refuses to run against the
  real home.
- [ ] **01a-C3** `--decide-backend` can be repeated. Adds `--repeat`, `--out`
  (eval-run v1) and `--compare FILE...`, which works offline.

### 01b: Decision capture
- [ ] **01b-C1** One capture helper used at all five decide sites, plus every
  new one. Each outcome records `stage` and `at`. Captures go to their own
  retention pool. The reconcile sweep captures at campaign level. Captures
  are fenced.
- [ ] **01b-C2** Capture stays off the decide path: it is filed after the
  call settles, never on cancel, with the speaker capture's privacy.

### 01c: Distributions and seeded sampling
- [ ] **01c-C1** Recorded policy:
  - verbalised probabilities are rejected;
  - native-first is opt-in per task (`TaskPolicy.native_first`) and needs
    01a evidence;
  - every task is off at landing;
  - there is a `reports_distribution(resolved)` predicate.
- [ ] **01c-C2** `draws.py`: SHA-256 inverse-CDF draw, stable across Python
  versions, Android and a browser.
- [ ] **01c-C3** A replay record, stored with the outcome under the same lock
  and never re-drawn.
- [ ] **01c-C4** Abstain, refuse, unreadable, error or no usable
  distribution: never sampled. Rank or MultiSelect marginals: never sampled.

### 01d: Escalation and per-task policy
- [ ] **01d-C1** `routing.TaskPolicy` (shared with 01c). `fallback="none"`
  must agree across a route.
- [ ] **01d-C2a** A pure trigger evaluation (low margin, abstention, native
  `refused`), with an optional answer filter.
- [ ] **01d-C2b** One escalation hop after the unchanged chain. The next
  resolver is declared and may be a caller-supplied resolver. Rows carry
  `hop: escalation`.
- [ ] **01d-C3** Thresholds per task and native endpoint kind, capped at 0.5,
  starting at 0.2. All existing tasks are off.

### 01e: Decision vocabulary
- [ ] **01e-C1** `Rank` returns a `Ranking`: tied groups, plus unranked.
  Natively it uses a per-candidate `pointwise` predicate, or is refused
  unsent.
- [ ] **01e-C2** `Answer.expected`, `tiers()`. `MAX_LEVELS` stays 10.
- [ ] **01e-C3a** `MultiSelect`, where an empty selection is a real answer.
- [ ] **01e-C3b** `Joint`: a flattened choice over the legal (action, target)
  pairs.
- [ ] **01e-C4** `Answer.marginals`, kept separate from `distribution`.

### 01f: Structured generation
- [ ] **01f-C1** `generate(schema=)` sends structured mode on each attempt
  that can take it. The schema must also be in the prompt. Adds a per-call
  `max_tokens` cap.
- [ ] **01f-C2** A schema refusal is re-sent through a helper shared with
  decide. It is never a health failure.
- [ ] **01f-C3** `grimoire/schemas.py`: the portable schema subset, the
  prompt spelling, and a tolerant reader.

### 01g: Tool calling, Decision as a tool, run budgets
- [ ] **01g-C1** A `tools` capability, a probe, and the seam's `incapable`
  refusal.
- [ ] **01g-C2a** Tool calling over OpenRouter, OpenAI-compatible and
  Anthropic. The loop primitive: the caller executes the tools, supplies the
  run id, and gets the final call back.
- [ ] **01g-C2b** Claude Agent SDK. Until it lands, `claude` reads as unable
  to call tools.
- [ ] **01g-C3** Each turn is a ledger row with `run_id` and `loop_turn`,
  plus capture. The trace carries no argument or result text.
- [ ] **01g-C4** Run budget: turns, tool calls, decisions, wall clock, spend
  ceiling, per-turn output cap. It reports which limit stopped the run. An
  unpriced model under a spend ceiling is refused before sending.
- [ ] **01g-C5** A decide tool on the `tool-decision` route, capped per run,
  with no recursion. The task name is supplied by the caller.
- [ ] **01g-C6** The final loop turn streams, and the loop can decline a tool
  call that comes after visible text (needed by 02-C4).

### 01h: Embedding options, async embed, embedding evals
- [ ] **01h-C1** Input type, with a `queries: int` split and modes
  `none | prefix | param`. A query vector is compared within its space and
  is never cached.
- [ ] **01h-C2** Requested `dimensions`. A mismatch raises
  `dimensions_mismatch`, and callers degrade.
- [ ] **01h-C3** Stated options go into the space id as `\0embopt1:<digest>`.
  Default options produce today's exact space string.
- [ ] **01h-C4a** No embedding on the event loop: a guard degrades instead.
- [ ] **01h-C4b** A native async `embed()` with a total deadline.
- [ ] **01h-C5** `attribute(claims)` + `embed_groups_sync`: one row per
  campaign group, plus an optional `run_id`.
- [ ] **01h-C6** `evals/run.py --embed`: recall@k and MRR against a lexical
  baseline.

### 01i: Context window
- [ ] **01i-C1** `wire.Limits(window, max_output)` with a source, on every
  target.
- [ ] **01i-C2** `prompt_ceiling(resolved)`: the smallest window minus a
  reserve. Unknown means `None`, never 0.
- [ ] **01i-C3** User-stated facts, the Models readout, and `model_window` in
  context breakdowns.

### 02: Decision integration
- [ ] **02-C1** A record of what 01's slices F–H landed.
- [ ] **02-C2a** A sampled next speaker. **02-C2b** A turn intent.
- [ ] **02-C3** A per-contribution turn plan, with an `extra` slot for 13-C3.
- [ ] **02-C4** Decision as a tool in play, at most one per contribution.
- [ ] **02-C5a** Epistemic access, as a kit for 11.
- [ ] **02-C5b** History relevance (`history_check` route), as a kit for 09
  and 10.
- [ ] **02-C6** Play-decision rules and the play gate. Everything is off by
  default.

### 03: Compiled cache
- [ ] **03-C1** Composite keys over collection digests and inputs that are not
  files. A collection can take a member filter.
- [ ] **03-C2** Liveness by construction.
- [ ] **03-C3** `materialized`, keyed by `(path, kind, instance)`, with kind form
  `vector:<projection>:<space-digest>` (one spelling: `compiled.space_digest`).
- [ ] **03-C4** One validate-and-hash primitive.
- [ ] **03-C5** Artifacts can be stored at write time. The `sources` row waits
  out the racy window.
- [ ] **03-C6** Batch lookup over a live key set. An index ranks only within
  that set. Lookups report hit and miss counts.
- [ ] **03-C7** Vectors are keyed by text and never by `BUILD`.
- [ ] **03-C8** A callable purge for a world or campaign delete, through a
  purge marker.
- [ ] **03-C9** A synthetic-library generator, owned by 03's plan and
  extended by 04-C3b.

### 04: Instant overview pages
- [ ] **04-C1a** Card projections. **04-C1b** Scene and continuity projections.
  **04-C1c** A Todo scope assembled from projections plus three live chores.
- [ ] **04-C2a** A post-turn warm hook. **04-C2b** First paint from client
  memory, then revalidate; a consistency bound.
- [ ] **04-C3a** Counters and a debug line. **04-C3b** Benchmark harness that
  runs on synthetic libraries only.

### 05: Direct-edit cache sync
- [ ] **05-C1** `cache_sync.sync_paths` through a write-through queue.
- [ ] **05-C2** `python -m grimoire.cache sync` and `POST /api/cache/sync`.
- [ ] **05-C3** `WarmHook` order: files → overview → searchdocs → vectors.
  Re-embeds only text not already cached. No confirmation step.
- [ ] **05-C4** `store/writeset.py`: a context-variable collector that
  `store.atomic` notes into, with a guard.

### 06: Store-editing skill
- [ ] **06-C1** A canonical skill in `.claude/skills/grimoire-store-editing/`,
  an `.agents/` adapter, and `test_skills_guard.py`.

### 07: Explicit group membership
- [ ] **07-C1** `members` on the group record, with overlay and sync
  semantics.
- [ ] **07-C2** Inverse membership, with a digest and routes.
- [ ] **07-C3a** Absorb join/leave proposals. **07-C3b** Graph group nodes.
  **07-C3c** Retrieval projections (`scene_groups`, `co_affiliates`,
  `prompt_visible`). **07-C3d** A present member makes their group present
  for lore.

### 08: History SearchDocuments
- [ ] **08-C1** A bounded `SceneDocument` with no transcript text, keyed by
  per-scene slices.
- [ ] **08-C2a** Lazy build plus batch lookup. **08-C2b** Document vectors.
  **08-C2c** A hot-rebuild hook for 05.
- [ ] **08-C3a** A `history-index` embed task. **08-C3b** `expand(...)`, where
  the caller names the phase.

### 09: Hybrid historical retrieval
- [ ] **09-C1** `history.retrieve(Query) -> Evidence`, RRF merge, a
  `perspective` seam. Evidence carries the scene identity and post indices,
  keys and texts.
- [ ] **09-C2** Two tiers per turn, a `Coverage` verdict, fallbacks.
- [ ] **09-C3** A `history_recall` section. NPC prompts get none of it until
  11. Byte-identical when off.
- [ ] **09-C4** A long-history eval suite.

### 10: Retrieval query planning
- [ ] **10-C1** `history_plan` route on Fast, taking a perspective.
- [ ] **10-C2** One repair hop, at most three calls per turn, a phase
  deadline, and a `PlanTrace` that 12's trigger reads.
- [ ] **10-C3** An evidence-sufficiency predicate on `history_check`, which
  12 reuses.

### 11: Epistemic history retrieval
- [ ] **11-C1** Per-actor classes, deterministic first, with a capped
  Decision pass that is off by default.
- [ ] **11-C2** Narrator and actor prompt separation, refused on a mismatched
  perspective.
- [ ] **11-C3** `knowledge.json` overrides through `routes/ledger.py`.
- [ ] **11-C4** A leakage eval suite.

### 12: Bounded agentic investigation
- [ ] **12-C1** A read-only toolset, perspective-filtered in RP.
- [ ] **12-C2a** Budgets. **12-C2b** Trace. **12-C2c** An eval gate.
- [ ] **12-C3** Write posture: selection in RP, one proposal for continuity,
  nothing for questions.
- [ ] **12-C4** Three entry points and their run classes.

### 13: Mechanics II
- [ ] **13-C1a** Actions and the legal set. **13-C1b** The Effect DSL and
  transaction ledger. **13-C1c** Action proposals, narration and audit.
- [ ] **13-C2a** Conditions. **13-C2b** Clocks. **13-C2c** Contests.
- [ ] **13-C3** The NPC action seam, sampled through 01c.

## Dependency edges

Tick an edge when its provider has landed. **H** is hard: the consuming slice
cannot start without it. **S** is soft: it degrades, or waits only for one
sub-feature. Edges come from each spec's Depends-on table.

- [ ] 01c ← 01a-C1/C3 (H to switch a task on), 01b-C1 (S), 01e-C4 (S: marginals are never sampled)
- [ ] 01d ← 01a-C1/C3 (H to enable), 01b-C1 (S)
- [ ] 01e ← 01a-C1 (S), 01b-C1 (S)
- [ ] 01f ← 01a-C1 (S), 01i-C1 (S: the `max_tokens` cap respects the model's max output)
- [ ] 01g ← 01f-C1/C2/C3 (H); 01d-C1, 01i-C1, 01c-C2/C3, 01b-C1 (S)
- [ ] 01h ← 01a-C1/C2 (H for C6), 01a-C3 (S), 01s (S); 01g-C3 run id (S)
- [ ] 01i ← 01s (S)
- [ ] 02 ← 01a-C1/C2/C3 (H for every play gate), 01b-C1/C2 (H for
  C2b/C3/C4; S for the C5 kits, which make no call themselves), 01c-C1..C4 (H
  for C2 sampling), 01d-C1..C3 (H for C5a);
  01g-C1..C6 (H for C4 only); 01e-C1..C3 (S)
- [ ] 03 ← 01h-C3 (S)
- [ ] 04 ← 03-C1, 03-C2, 03-C4 (H); 03-C5 (H for C2a); 03-C6, 03-C9 (S)
- [ ] 05 ← 03-C2, 03-C3, 03-C4, 03-C5 (H); 04-C2b (H, a property relied
  on); 03-C1, 03-C8, 04-C2a, 08-C2c, 01h-C1/C3/C5 (S)
- [ ] 06 ← 05-C2 (H); 05-C1/C4, 04-C2b (S)
- [ ] 07 ← 03-C1/C2/C3 (S)
- [ ] 08 ← 03-C1, 03-C2, 03-C6, 03-C7 (H); 03-C3, 05-C3 (H for C2c);
  07-C2, 07-C3c, 01h-C1, 01h-C3, 03-C9 (S)
- [ ] 09 ← 08-C1/C2/C3, 03-C6, 03-C7 (H); 01h-C4 (H for the turn path);
  01a-C1 (H for live evals); 07-C2, 07-C3c, 01e-C1/C2, 01h-C1, 01i-C1/C2,
  02-C5b (S)
- [ ] 10 ← 09-C1/C2/C3, 01f-C1 (H); 01a-C1 (H for live evals); 01f-C2,
  01d-C1/C2, 01a-C3, 02-C5b, 01b-C1 (S)
- [ ] 11 ← 09-C1, 09-C3 (H); 02-C5a (H for the Decision stage); 07-C1 (H for
  group overrides); 07-C3c, 10-C1, 08-C3b, 01b-C1, 01d-C1/C2, 01c-C4 (S)
- [ ] 12 ← 01g-C1..C5, 01i-C1/C2, 08-C3, 09-C1, 01a-C1/C2 (H); 10-C2 (its
  `PlanTrace`) and 10-C3, 11-C1/C2 (H for RP mode); 11-C4 (S). The RP trigger
  reads 10's `PlanTrace`, not 01d escalation.
- [ ] 13 ← (nothing for II-A to II-D); 01c-C2/C3/C4 (H for C3); 01e-C3a (H for
  multi-target actions: action first, then a MultiSelect over targets);
  02-C3 (S: the NPC Action question rides the turn plan's `extra` slot when
  the plan runs);
  01e-C3b (S, single-target joint pairs only); 01c-C1, 01b-C1, 01a-C1, 02-C2
  (S). Legal sets past 254 options need nothing from 01e: they take two steps,
  the action and then the target.

## Slice graph

Each spec's **Slices** section breaks it into landable units, each roughly
one PR. A slice names the contract items it delivers and, on its
**Needs (slices)** line, the exact slices it needs, marked **H** or **S** as
for edges. A slice can land as soon as its **H** needs have landed, whatever
spec they sit in, so a path can cut across specs without landing any of them
whole. A slice lands with its **S** needs missing by doing what its own
**Needs (other specs)** line says (degrading, staying off, or taking a stub
path).

`scripts/roadmap_slices.py` reads those lines and nothing else:

- `python3 scripts/roadmap_slices.py check`: every contract item is
  delivered in full by exactly one slice. Every contract-level need is
  resolved by a listed slice that delivers it, with a hard need kept hard.
  The graph has no cycle.
- `python3 scripts/roadmap_slices.py trace 07-S5`: the slices `07-S5` needs,
  directly or not, in an order they can land. `--soft` adds the soft needs.
- `python3 scripts/roadmap_slices.py ready`: the slices whose hard needs are
  all ticked below and which are not ticked themselves.

Tick a slice in the PR that lands it. If a slice changes its needs, change
its **Needs (slices)** line in that PR, and run `check`. The arrow lists the
slice's direct needs; `trace` gives the rest.

### 01a

- [ ] **01a-S1** [S] Decide call records ← none
- [ ] **01a-S2** [M] Metered live runs in an eval scope ← none
- [ ] **01a-S3** [M] Cost, latency and token reporting ← 01a-S1, 01a-S2, 01d-S3 (S)
- [ ] **01a-S4** [M] Run file and comparison ← 01a-S3

### 01b

- [ ] **01b-S1** [S] Stage and batch positions on the decide outcome ← none
- [ ] **01b-S2** [L] The capture helper, the decision pool and the four scene-level sites ← 01b-S1
- [ ] **01b-S3** [M] Campaign-level capture for the reconcile sweep, and the guard ← 01b-S2

### 01c

- [ ] **01c-S1** [M] The sampler, the record and replay ← 01d-S2 (S), 01e-S1 (S)
- [ ] **01c-S2** [M] Native-first in the decide chain (mechanism, all tasks off) ← 01d-S1 (S)
- [ ] **01c-S3** [S] The recorded evidence and the distribution grader ← 01a-S3, 01a-S4, 01c-S2

### 01d

- [ ] **01d-S1** [S] The task policy and `fallback="none"` ← none
- [ ] **01d-S2** [S] Per-item provenance and trigger evaluation ← none
- [ ] **01d-S3** [L] The escalation hop in `decide` ← 01a-S1 (S), 01b-S3 (S), 01d-S1, 01d-S2
- [ ] **01d-S4** [M] The escalation seam in `routes/` ← 01d-S3
- [ ] **01d-S5** [M] Threshold tooling in evals ← 01a-S2, 01a-S3, 01a-S4, 01d-S4

### 01e

- [ ] **01e-S1** [S] Answer fields, `expected` and `tiers` ← none
- [ ] **01e-S2** [M] `Rank` on the structured path, and the template switch to `KIND` ← 01e-S1
- [ ] **01e-S3** [S] `MultiSelect` on the structured path ← 01e-S2
- [ ] **01e-S4** [M] `Joint` on both paths, and the native lowering framework ← 01e-S2
- [ ] **01e-S5** [M] Native `Rank` and `MultiSelect` through pointwise predicates ← 01a-S3 (S), 01e-S3, 01e-S4

### 01f

- [ ] **01f-S1** [S] The portable-schema leaf ← none
- [ ] **01f-S2** [M] Structured mode on `generate`, and the shared refusal re-send ← 01f-S1
- [ ] **01f-S3** [S] The per-call output cap ← 01f-S2, 01i-S1 (S)
- [ ] **01f-S4** [S] The `intent` pilot ← 01f-S2, 01f-S3

### 01g

- [ ] **01g-S1** [M] The `tools` capability and its seam refusal ← none
- [ ] **01g-S2** [L] Neutral tool shapes, wire lowering and stream parsing on the HTTP adapters ← 01f-S1
- [ ] **01g-S3** [S] Run attribution on the ledger and the capture ← none
- [ ] **01g-S4** [L] The loop primitive (joined) ← 01d-S1 (S), 01f-S1, 01f-S2, 01f-S3, 01g-S2, 01g-S3, 01i-S1 (S)
- [ ] **01g-S5** [M] The spend ceiling ← 01f-S3, 01g-S4
- [ ] **01g-S6** [M] Streaming the loop ← 01g-S4
- [ ] **01g-S7** [M] The decide tool ← 01b-S2 (S), 01c-S1 (S), 01g-S4, 01g-S5
- [ ] **01g-S8** [M] The Claude Agent SDK adapter ← 01g-S1, 01g-S4

### 01h

- [ ] **01h-S1** [S] No embedding on the event loop ← none
- [ ] **01h-S2** [L] Options in the space identity, the `queries` split and prefix mode ← none
- [ ] **01h-S3** [M] Request-field input type and requested dimensions ← 01h-S2
- [ ] **01h-S4** [M] Options in the UI ← 01h-S3
- [ ] **01h-S5** [M] Native async embed ← 01g-S3 (S), 01h-S3
- [ ] **01h-S6** [M] Cross-campaign attribution ← 01g-S3 (S), 01h-S3 (S)
- [ ] **01h-S7** [M] Embedding evals ← 01a-S2, 01a-S3, 01a-S4 (S), 01h-S3

### 01i

- [ ] **01i-S1** [M] The resolved fact and the ceiling ← none
- [ ] **01i-S2** [M] Stating the limits ← 01i-S1
- [ ] **01i-S3** [S] `model_window` in context breakdowns ← 01i-S1
- [ ] **01i-S4** [S] The Models page readout ← 01i-S1, 01i-S2 (S)

### 02

- [ ] **02-S1** [M] Record and plumbing ← none
- [ ] **02-S2** [L] Turn intent, dark (answers only) ← 01b-S2, 02-S1
- [ ] **02-S3** [M] Sampling (speaker and stance) ← 01c-S1, 01c-S2, 01d-S1, 02-S2
- [ ] **02-S4** [M] Turn plan, dark ← 01e-S4 (S), 02-S3
- [ ] **02-S5** [L] Play gate harness ← 01a-S2, 01a-S3, 01a-S4, 02-S2
- [ ] **02-S6** [M] Exposure of ratified features ← 02-S5
- [ ] **02-S7** [M] Decide kits for 11 and 09 ← 01d-S3, 01e-S5 (S), 02-S1 (S)
- [ ] **02-S8** [L] Decision as a tool from play, dark ← 01c-S1, 01g-S1, 01g-S4, 01g-S5, 01g-S6, 01g-S7, 01g-S8 (S), 02-S2, 02-S5

### 03

- [ ] **03-S1** [L] The cache file, its guards, and failing safe ← none
- [ ] **03-S2** [L] The trust point and the validate-and-hash primitive ← 03-S1
- [ ] **03-S3** [M] Collections and live-set lookups ← 03-S2
- [ ] **03-S4** [M] Write-time artifacts and the `materialized` record ← 03-S2
- [ ] **03-S5** [M] Bounding, retention and the purge ← 03-S4
- [ ] **03-S6** [L] Synthetic library and the first consumers ← 03-S3, 03-S4

### 04

- [ ] **04-S1** [S] Request counters and the debug line ← 03-S3 (S)
- [ ] **04-S2** [M] Client first paint, then revalidate ← none
- [ ] **04-S3** [S] Live-path fixes ← none
- [ ] **04-S4** [M] `scene_summary`, in-process, and its consumers ← none
- [ ] **04-S5** [M] `continuity_summary`, in-process, with the text seam ← none
- [ ] **04-S6** [S] `scene_turns`, in-process, on the shell ← none
- [ ] **04-S7** [M] Persisted card rows, and the `module` chip ← 03-S2, 03-S3 (S)
- [ ] **04-S8** [M] Persisted `scene_turns` and `continuity_summary` ← 03-S2, 03-S3 (S)
- [ ] **04-S9** [S] Post-turn warm and `warm_paths` ← 03-S4
- [ ] **04-S10** [M] Overview benchmark on synthetic libraries ← 03-S2, 03-S6 (S)

### 05

- [ ] **05-S1** [M] The write set ← none
- [ ] **05-S2** [L] The sync primitive and the `files` hook ← 03-S2, 03-S4
- [ ] **05-S3** [M] The CLI, scoped modes and scripts ← 03-S2, 03-S4, 03-S5 (S), 05-S2
- [ ] **05-S4** [M] The write-through queue ← 05-S1, 05-S2
- [ ] **05-S5** [S] The `overview` hook adapter ← 04-S9, 05-S2
- [ ] **05-S6** [L] The `vectors` hook and the three producers ← 01h-S2 (S), 01h-S6 (S), 03-S4, 05-S2, 05-S5 (S)
- [ ] **05-S7** [M] The API ← 04-S2 (S), 05-S3, 05-S6 (S)

### 06

- [ ] **06-S1** [M] The skill, its adapter and the drift test ← 04-S2 (S), 05-S3, 05-S7 (S)
- [ ] **06-S2** [S] Routing to the skill ← 06-S1

### 07

- [ ] **07-S1** [M] The members field, its reader and writer, and the editor ← none
- [ ] **07-S2** [L] The inverse index, its routes and the actor pages ← 07-S1
- [ ] **07-S3** [M] Sync shows fields; push and promote refuse strangers ← 07-S1
- [ ] **07-S4** [S] Structural presence through members ← 07-S2
- [ ] **07-S5** [L] Absorb membership proposals ← 07-S1, 07-S2
- [ ] **07-S6** [M] Story Graph group nodes and edges ← 07-S2
- [ ] **07-S7** [S] Retrieval projections ← 07-S2
- [ ] **07-S8** [S] Persistent tier of the inverse index ← 03-S2, 03-S3, 03-S4 (S), 07-S2

### 08

- [ ] **08-S1** [S] Extract the rolling-summary intactness test ← none
- [ ] **08-S2** [L] The scene SearchDocument, in process ← 03-S2, 03-S6 (S), 08-S1
- [ ] **08-S3** [M] The live set, the compiled cache, and recording embedded vectors ← 03-S1, 03-S2, 03-S3, 03-S4, 03-S5 (S), 08-S2
- [ ] **08-S4** [M] Transcript expansion and its guard rule ← none
- [ ] **08-S5** [M] The `searchdocs` hook, `vectors_for`, and the `history-index` task ← 01h-S2 (S), 03-S4, 05-S6, 08-S3
- [ ] **08-S6** [S] Group metadata from 07 ← 07-S2, 07-S7, 08-S2

### 09

- [ ] **09-S1** [L] Retrieval core, structural and lexical, no network ← 03-S1, 03-S3, 07-S2 (S), 07-S7 (S), 08-S2, 08-S3, 08-S4
- [ ] **09-S2** [M] The history section and its packer tier ← 01i-S1 (S), 09-S1
- [ ] **09-S3** [M] The turn phase, structural and lexical ← 01i-S1 (S), 09-S2
- [ ] **09-S4** [M] The semantic signal and tier 2 ← 01h-S1, 01h-S2 (S), 01h-S5, 08-S3, 09-S3
- [ ] **09-S5** [M] The rerank ← 01d-S1 (S), 01e-S1 (S), 01e-S5 (S), 02-S7, 09-S3
- [ ] **09-S6** [M] The long-history eval suite ← 01a-S2, 01a-S3, 01a-S4 (S), 09-S4, 09-S5 (S)

### 10

- [ ] **10-S1** [L] The planner and one planned round ← 01f-S1, 01f-S2 (S), 01f-S3, 09-S2, 09-S3
- [ ] **10-S2** [M] The sufficiency check and the repair hop ← 01b-S2 (S), 01d-S1 (S), 01d-S2 (S), 01d-S4 (S), 02-S7 (S), 10-S1
- [ ] **10-S3** [M] Planning evals ← 01a-S2, 01a-S3, 01a-S4 (S), 09-S6, 10-S2

### 11

- [ ] **11-S1** [M] The knowledge store ← 07-S2 (S)
- [ ] **11-S2** [L] The deterministic classifier ← 08-S4 (S), 09-S1, 11-S1
- [ ] **11-S3** [L] Perspective-aware retrieval and prompt separation ← 09-S1, 09-S2, 10-S1 (S), 10-S2 (S), 11-S2
- [ ] **11-S4** [M] The Knowledge ledger: routes, UI and accept ← 11-S1, 11-S3 (S)
- [ ] **11-S5** [S] Live leakage evals ← 01a-S2 (S), 01a-S3 (S), 11-S3
- [ ] **11-S6** [L] The Decision stage ← 01b-S2 (S), 01c-S1 (S), 01d-S4, 02-S5, 02-S7, 11-S3, 11-S4 (S), 11-S5 (S)

### 12

- [ ] **12-S1** [L] The narrator-side toolset ← 01g-S2, 08-S4, 09-S4
- [ ] **12-S2** [M] The loop driver, budgets and trace recorder ← 01g-S4, 01g-S5, 01i-S1, 12-S1
- [ ] **12-S3** [L] E2, continuity investigation (the pilot) ← 01g-S1, 12-S2
- [ ] **12-S4** [S] Decision as a tool for maintenance modes ← 01g-S7, 12-S3
- [ ] **12-S5** [M] E3, history question ← 01g-S1, 12-S2, 12-S4 (S)
- [ ] **12-S6** [L] E1, RP escalation ← 02-S1, 08-S4 (S), 10-S2, 11-S2, 11-S3, 12-S2
- [ ] **12-S7** [M] The adoption gate ← 01a-S2, 01a-S3, 11-S5 (S), 12-S3, 12-S5 (S), 12-S6 (S)

### 13

- [ ] **13-S1** [M] The `actions.json` format and pack validation ← none
- [ ] **13-S2** [M] The Effect engine (pure) ← 13-S1
- [ ] **13-S3** [M] Availability, pools and the legal set ← 13-S1, 13-S2
- [ ] **13-S4** [L] The transaction ledger, the unit writer, undo and settle ← 13-S2
- [ ] **13-S5** [L] Resolving an Action through the proposal path ← 13-S3, 13-S4
- [ ] **13-S6** [M] Model-proposed Actions: the fence and the prompt section ← 13-S5
- [ ] **13-S7** [M] Recovery doors and module authoring (backend) ← 13-S5
- [ ] **13-S8** [S] Audit integration ← 13-S5
- [ ] **13-S9** [L] II-A frontend ← 13-S4, 13-S5, 13-S7 (S)
- [ ] **13-S10** [S] List operations (II-A2) ← 13-S4
- [ ] **13-S11** [M] Conditions: definitions, store and ops ← 13-S5
- [ ] **13-S12** [M] Conditions: modifiers, gating and display ← 13-S11
- [ ] **13-S13** [M] Clocks (II-C) ← 13-S11
- [ ] **13-S14** [M] Contests (II-D) ← 13-S12
- [ ] **13-S15** [L] The NPC action seam, single-target ← 01a-S3 (S), 01b-S2 (S), 01c-S1, 01c-S2 (S), 01e-S4 (S), 02-S2 (S), 02-S4 (S), 13-S5, 13-S6 (S)
- [ ] **13-S16** [S] The NPC action seam, multi-target ← 01e-S3, 13-S15

## Shared structures: whichever spec lands first adds them

- `routing.TaskPolicy`: 01c and 01d.
- `inference._Call.stage` / `positions`: 01a and 01b.
- `decisions.CallRecord`, `Decision.calls`: 01a, used by 01d.
- `ItemResult.served`: 01d, read by 01c.
- `routing.NO_LEGACY` sentinel for routes new at format 2: 01g
  (`tool-decision`), 09 and 10 (`history_check`, `history_plan`), 02-C5.
  Any of these may add it, together with its test mechanism: a
  `NO_LEGACY_TASKS` set that the frozen inference baselines exclude.
- `decide(response_id=)` and `responses.mint_id()`: 02.

## Cross-spec decisions

Recorded here so that every spec says the same thing. A spec that disagrees
should change this section rather than drift from it.

- [x] **A warm hook reads its own rows by path, never by enumeration.** 05-C3
  allows a hook to read its own kinds' `materialized` rows for the paths it
  was handed, and for paths it derives from them by reading the store. 08's
  `searchdocs` hook, handed a campaign ledger path, lists that campaign's live
  scenes through the scene store and reads its own rows for those scene
  paths. It never scans the table.
- [x] **01c-S2 and 01d-S1 land in either order.** Both touch
  `routing.TaskPolicy`. The slice graph puts 01d-S1 first, as a soft need of
  01c-S2. Whichever lands first creates the structure, and the rule refusing
  `samples` with `low_margin` lands with whichever is second.

- [x] **Unpriced model under a spend ceiling.** 01g-C4 refuses it before
  sending. A ceiling cannot hold against a price nobody reported, and
  counting such a call as free would break the cost rule. 12 follows this: an
  entry point whose model is unpriced under a ceiling is skipped, and the
  reason is recorded.
- [x] **Vectors on a world or campaign delete.** 03-C8 purges the compiled
  cache. `vectors.py` files are keyed by space and text, never by campaign, so
  they cannot be purged selectively. They are a residual that 03 and 08 both
  state, and the Settings page says so. A full vector purge is a manual
  action, and none is added here.
- [x] **The `materialized` vector kind.** It is
  `vector:<projection>:<space-digest>`, with an optional `instance`. A raw
  space id contains NUL bytes, and a bare `vector:<space>` would let two
  projections of one text collide. 03, 05 and 08 all use this form.
- [x] **NPC answer with no distribution (13 open question 4).** Per 01c-C4,
  it is never presented as a sample. 13 may act on a plain Choice answer,
  recorded `sampled: false`. 01c states that this is allowed: it is not a
  draw, and nothing is replayed from it.
- [x] **History in NPC prompts.** None until 11-C2 lands (09-C3).

## Existing decisions these specs supersede (for review)

- The landed 01 spec, section 9.4 (only the speaker decision is captured) is
  superseded by 01b.
- The landed 01 spec says "`rank` is not provided"; 01e adds it.
- Continuity capstone section 33 parks new graph node families. 07 lifts that
  for groups only.
- Read-path spec section 3 (library epoch, `ETag`/`304`) is never built. 04
  recommends marking it superseded. **Needs your call.**
- `test_decide_chain_golden.py` gains two outcome keys (01b), argued in 01b.
- CLAUDE.md's detached-run handler count rises by one (05's
  `POST /api/cache/sync`).
- `2026-10-04-mechanics-ii-design.md` is partly superseded by 13, as section
  3 of 13 records.

## Found on main, outside the roadmap

These are not specs, and each needs a decision on whether to fix it.

- [ ] `store/dice.py` replays with `random.randint`, which is not stable across
  Python versions (found by 01c).
- [ ] `claude_agent.py:163` passes `allowed_tools=[]` rather than `tools=[]`,
  so the built-in tool definitions likely ride on every Claude subscription
  call. Verify against the installed SDK (found by 01g).
- [ ] The shell parses every open scene's full transcript on each navigation,
  because the read-path memo never landed (04).
- [ ] The campaign card's module chip never renders, because the row lacks
  `module` (04).
- [ ] `TodoView` blanks on every visit, and `CampaignsView` flashes "No worlds
  yet" (04).
- [ ] `characters.roster` re-parses every card without caching; several
  post-write refreshes do not pass `fresh` (04).
- [ ] The opener's compose step embeds on the event loop
  (`routes/greetings.py:98-106`). 01h-C4a fixes it.
- [ ] `migrations._backfill_campaign` measures its racy window at record time
  rather than at stamp time (03).
- [ ] Stale docstrings: `entities.py:420` (ref fields do reach prompts),
  `embed_space.facts_moved`, and `context/pack.py:69-73`.

## Final step: delete this file

When every spec in the status table is `Landed`, and every box above is
ticked or explicitly dropped with a reason, the PR that lands the last spec
also does three things:

- [ ] Re-check every edge and cross-spec decision above against the landed
  code. Anything still open becomes an issue or a section in a spec, never a
  line kept here.
- [ ] Remove the specs' references to this file. Each "Roadmap:" header line
  points here. Change it to name the spec IDs it depends on, or drop it.
- [ ] **Delete `ROADMAP-CHECKLIST.md` and `scripts/roadmap_slices.py`**, in
  that same PR. The script reads this file, so neither outlives the other.

The specs stay. Only this ledger goes. Its job ends when there is nothing left
to coordinate.
