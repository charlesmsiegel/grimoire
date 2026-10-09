# Roadmap checklist

> **Working file. Delete it once the roadmap has landed.** It tracks the
> roadmap's specs, what each one owes the others, and what is done. The specs
> themselves are the design. This file is only the ledger. Tick a box in the
> same PR that makes it true.

Specs live in `docs/superpowers/specs/2026-10-09-roadmap-<id>-<slug>-design.md`.
Each spec carries a **Depends on** table and a **Required by** table, and both
cite contract IDs from the list below (`01g-C2` means contract item 2 of spec
01g). When a spec gains, drops or renumbers a contract item, change this file
in the same PR.

## Lifecycle per spec

`spec drafted` → `spec gate` (`/codex:adversarial-review`, or a recorded
substitute) → `plan` → `plan gate` → `implemented` → `/codex:review` → `final
gate` (diff against spec) → `landed`.

## Status

| ID | Spec | Size | Depends on (hard) | Drafted | Spec gate | Plan | Landed |
|----|------|------|-------------------|:-:|:-:|:-:|:-:|
| 01 | Inference backend refactor (`2026-10-07-inference-backend-refactor-design.md`) | L | — | [x] | [x] | [x] | [x] |
| 01s | Inference settings group (`2026-10-09-inference-settings-group-design.md`) | M | 01 | [x] | [x] | [x] | [ ] |
| 01a | Eval cost, latency and token reporting | S | 01 | [ ] | [ ] | [ ] | [ ] |
| 01b | Decision capture at every decide site | S | 01 | [ ] | [ ] | [ ] | [ ] |
| 01c | Decision distributions and seeded sampling | M | 01, 01a, 01b | [ ] | [ ] | [ ] | [ ] |
| 01d | Decision escalation and per-task fallback policy | M | 01, 01a | [ ] | [ ] | [ ] | [ ] |
| 01e | Decision vocabulary: rank, fine score, multi-select | S–M | 01 | [ ] | [ ] | [ ] | [ ] |
| 01f | Structured generation (provider schema mode on `generate`) | S | 01 | [ ] | [ ] | [ ] | [ ] |
| 01g | Tool calling, Decision-as-tool, run budgets | L | 01, 01f | [ ] | [ ] | [ ] | [ ] |
| 01h | Embedding options, async embed, embedding evals | M | 01, 01a | [ ] | [ ] | [ ] | [ ] |
| 01i | Context window as a resolved model fact | S | 01 | [ ] | [ ] | [ ] | [ ] |
| 02 | Decision integration (what 01's slices F–H did not already land) | M | 01, 01a, 01b, 01c, 01d | [ ] | [ ] | [ ] | [ ] |
| 03 | Content-addressed compiled cache | L | 01, 01h-C3 | [x] | [~] substitute + PR Codex | [ ] | [ ] |
| 04 | Instant Worlds, Campaigns, Todo and shell | M | 03 | [ ] | [ ] | [ ] | [ ] |
| 05 | Direct-edit cache sync | M | 03, 04 | [ ] | [ ] | [ ] | [ ] |
| 06 | Store-editing cache skill | S | 05 | [ ] | [ ] | [ ] | [ ] |
| 07 | Explicit group membership | M | — | [ ] | [ ] | [ ] | [ ] |
| 08 | Derived history SearchDocuments | M | 03, 05, 07, 01h | [ ] | [ ] | [ ] | [ ] |
| 09 | Hybrid historical retrieval | L | 07, 08, 01e, 01h, 01i | [ ] | [ ] | [ ] | [ ] |
| 10 | Retrieval query planning | M | 09, 01a, 01d, 01f | [ ] | [ ] | [ ] | [ ] |
| 11 | Epistemic history retrieval | M | 02, 07, 09, 10 | [ ] | [ ] | [ ] | [ ] |
| 12 | Bounded agentic investigation | L | 02, 08, 09, 10, 11, 01d, 01g, 01i | [ ] | [ ] | [ ] | [ ] |
| 13 | Mechanics II: Actions and Effects (reconciles `2026-10-04-mechanics-ii-design.md`) | L | 01, 01c, 01e; 02 is a soft seam | [ ] | [ ] | [ ] | [ ] |

## Parallel lanes

- **Now:** 07, plus every 01x minor spec except 01c, 01d, 01g and 01h, which
  each wait on a minor spec of their own. 03's plan can start once its spec
  gate passes.
- **Decision lane:** 01a, 01b → 01c, 01d → 02 → 11. 01e also feeds 09, 10, 11
  and 13.
- **Cache lane:** 03 → 04 → 05 → 06, and 05 + 07 + 01h → 08.
- **Retrieval lane:** 08 + 01e + 01h + 01i → 09 → 10 (with 01f and 01d) → 11 →
  12 (with 01g).
- **Mechanics:** 13 can start after 01, 01c and 01e. Its Decision-chosen NPC
  actions wait for 02.

## Contracts

Each item is something a spec **provides**. Downstream specs cite these IDs.
The owning spec states each one precisely. The wording here is the headline
only.

### 01a: Eval cost, latency and token reporting
- [ ] **01a-C1** `evals/run.py` reports per-item and aggregate wall time,
  prompt/completion tokens, and cost (in the three money columns, never
  added together) for every live run, per backend and per route.
- [ ] **01a-C2** Live eval runs are metered: they file ledger rows under a
  marked eval scope, kept out of campaign spend and budgets.
- [ ] **01a-C3** Comparison output across backends (`--decide-backend`) and
  across configurations, in one table, readable without real store data.

### 01b: Decision capture at every decide site
- [ ] **01b-C1** Every `decide()` call site can file a prompt-log capture:
  the normalised request, the outcome, any distribution, the backend and the
  stage that answered.
- [ ] **01b-C2** Capture is off the decide path. It never fails an answered
  decision, and its privacy matches the speaker capture's.

### 01c: Decision distributions and seeded sampling
- [ ] **01c-C1** A policy, decided and recorded, for when a generating
  Decision model returns a distribution: native-first, structured
  probabilities, or neither. It includes the eval evidence 01a produces.
- [ ] **01c-C2** One sampling helper: `(distribution, seed) → selected`,
  deterministic and replayable.
- [ ] **01c-C3** A replay record `{distribution, seed, selected, backend}` that
  callers persist where their outcome is persisted.
- [ ] **01c-C4** An abstention, a refusal, or a missing distribution is never
  turned into a sampled answer.

### 01d: Decision escalation and per-task fallback policy
- [ ] **01d-C1** A task-level policy (in `routing`) for whether fallback and
  escalation are allowed.
- [ ] **01d-C2** An escalation helper: a low margin, an abstention or a native
  `refused` hands the item to a declared next resolver, bounded to one hop,
  metered and captured.
- [ ] **01d-C3** Thresholds are set per task and per backend, are justified
  structurally, and are tuned through 01a's evals.

### 01e: Decision vocabulary extensions
- [ ] **01e-C1** A `Rank` question (an order over the given candidates), on
  the native and structured paths, with abstention.
- [ ] **01e-C2** A finer `Score`, enough for reranking, with ties made explicit.
- [ ] **01e-C3** Multi-select and joint choice (action plus a target
  conditioned on it), at least on the structured path.

### 01f: Structured generation
- [ ] **01f-C1** `generate(schema=…)` can request a provider's structured mode
  per attempt, as decide does, with the schema still in the prompt.
- [ ] **01f-C2** A refusal of the schema field is retried without it, mirroring
  `SchemaRefusalError` handling, and is never a health failure.

### 01g: Tool calling, Decision-as-tool, run budgets
- [ ] **01g-C1** A `tools` capability with provenance, and a refusal at the
  seam for a route that requires it.
- [ ] **01g-C2** Provider-neutral tool schemas on `generate`, tool-call
  parsing, and a bounded multi-turn loop primitive, across the OpenRouter,
  OpenAI-compatible and Claude Agent adapters.
- [ ] **01g-C3** Metering and capture for each turn of a loop, under one run
  id.
- [ ] **01g-C4** A run budget: maximum turns, maximum tool calls, a wall-clock
  limit and a spend ceiling, enforced before a call is sent.
- [ ] **01g-C5** A Decision-as-tool shim: a generating model may call
  `decide()` as a tool, with a per-run cap.

### 01h: Embedding options, async embed, embedding evals
- [ ] **01h-C1** Query vs document input type, carried to providers that
  support it.
- [ ] **01h-C2** Requested output dimensions, where a provider supports them.
- [ ] **01h-C3** Embedding options are part of the space identity, and so of
  the `vectors.py` key. 03 relies on this item.
- [ ] **01h-C4** An async embed path that turn-path callers use, so they never
  block the event loop.
- [ ] **01h-C5** Embedding attribution across campaigns in one batch.
- [ ] **01h-C6** Embedding evals: retrieval recall at k on a synthetic corpus.

### 01i: Context window as a resolved fact
- [ ] **01i-C1** `context_window` (and the maximum output, where known) as a
  model fact with provenance, on the resolved attempt.

### 02: Decision integration
- [ ] **02-C1** A reconciled record of what 01's slices F–H already landed:
  continuity identity and reconcile on decide, speaker, scene-break, voice
  drift, and the eval gate.
- [ ] **02-C2** A sampled next speaker and a turn intent through 01c,
  shipped behind an eval gate.
- [ ] **02-C3** A pre-generation turn plan (batched decide) behind an eval
  gate.
- [ ] **02-C4** Decision-as-tool used from play behind an eval gate (via
  01g-C5).
- [ ] **02-C5** Decide routes and tasks for epistemic classification and
  retrieval relevance, for 11 and 09.

### 03: Content-addressed compiled cache
- [ ] **03-C1** Composite keys over collection digests and inputs that are not
  files.
- [ ] **03-C2** Liveness by construction: a superseded version is unreachable
  from any key.
- [ ] **03-C3** The `materialized` record (which kinds were built from a path).
- [ ] **03-C4** One validation primitive, reused by 05.
- [ ] **03-C5** Artifacts can be stored at write time; the `sources` row
  waits for the racy window.
- [ ] **03-C6** Batch lookups over a live key set, with index ranking
  restricted to that set.
- [ ] **03-C7** Vectors are keyed by text and never by `BUILD`.

### 04: Instant overview pages
- [ ] **04-C1** World card, campaign card, per-scope Todo and shell
  projections, each keyed by what it renders.
- [ ] **04-C2** Synchronous retirement of a projection on Grimoire's own
  writes, and stale-while-revalidate for first paint.
- [ ] **04-C3** Instrumentation and a synthetic-library benchmark harness.

### 05: Direct-edit cache sync
- [ ] **05-C1** An in-process sync primitive called by Grimoire's writers.
- [ ] **05-C2** `cache sync` CLI and API (paths, `--campaign`, `--world`,
  `--all`), batched, with a summary that holds no content.
- [ ] **05-C3** An eager rebuild of materialized kinds (via 03-C3), embedding
  only what was already embedded.

### 06: Store-editing skill
- [ ] **06-C1** A canonical `grimoire-store-editing` skill, with thin adapters
  and a drift test.

### 07: Explicit group membership
- [ ] **07-C1** Authoritative group members (and a leader) on group records,
  with campaign overlay semantics.
- [ ] **07-C2** Derived inverse membership (actor → groups), which can be
  cached under 03.
- [ ] **07-C3** Absorb, graph and retrieval integration points.

### 08: SearchDocuments
- [ ] **08-C1** A deterministic, bounded scene SearchDocument with metadata,
  keyed by its input digest plus `search_document_version`.
- [ ] **08-C2** A lazy build, a hot rebuild via 05, and embeddings keyed by
  space and text (03-C7, 01h-C3).
- [ ] **08-C3** A history embed task and a transcript-expansion helper.

### 09: Hybrid historical retrieval
- [ ] **09-C1** Structural, lexical and semantic candidates combined into a
  bounded evidence set, with signals.
- [ ] **09-C2** Tiered widening, and fallbacks with no embeddings and during
  an outage.
- [ ] **09-C3** A history prompt section with a strict token budget (using
  01i-C1).
- [ ] **09-C4** A long-history eval suite.

### 10: Retrieval query planning
- [ ] **10-C1** A `history-query-plan` route on Fast, with a structured plan
  (01f-C1).
- [ ] **10-C2** One repair hop, bounded and metered, with escalation through
  01d-C2.

### 11: Epistemic retrieval
- [ ] **11-C1** Actor-knowledge classifications, deterministic first, Decision
  second (02-C5).
- [ ] **11-C2** A separation between narrator and actor prompts in retrieved
  history.

### 12: Bounded agentic investigation
- [ ] **12-C1** A read-only investigation toolset over the store (01g-C2).
- [ ] **12-C2** Run budgets (01g-C4), a trace, and an eval gate before broad
  use.

### 13: Mechanics II
- [ ] **13-C1** Module-defined Actions and the Effect DSL, with transactions
  (II-A).
- [ ] **13-C2** Conditions, clocks and contests (II-B to II-D).
- [ ] **13-C3** A seam for NPC action choice: a distribution over legal
  Actions (01c-C2/C3, 01e-C3), with a Decision-chosen intent from 02.

## Dependency edges to resolve

Tick an edge when the providing contract has landed. The consuming spec's plan
should not start an edge's slice while its box is empty.

- [ ] 02 ← 01a-C1, 01b-C1, 01c-C1..C4, 01d-C1..C2
- [ ] 03 ← 01h-C3 (the vector key covers embedding options; soft, because 03
  works without it)
- [ ] 04 ← 03-C1, 03-C2, 03-C5
- [ ] 05 ← 03-C3, 03-C4, 04-C2, 01h-C5
- [ ] 06 ← 05-C2
- [ ] 08 ← 03-C1, 03-C3, 03-C6, 03-C7, 05-C3, 07-C2, 01h-C1, 01h-C3
- [ ] 09 ← 07-C2, 08-C1..C3, 01e-C1/C2, 01h-C1, 01h-C4, 01i-C1, 01a-C1
- [ ] 10 ← 09-C1, 01f-C1, 01d-C2, 01a-C1
- [ ] 11 ← 02-C5, 07-C1, 09-C1, 10-C1
- [ ] 12 ← 08-C3, 09-C1, 10-C2, 11-C1, 01g-C1..C5, 01d-C2, 01i-C1
- [ ] 13 ← 01c-C2/C3, 01e-C3; 02-C2 (soft seam)
