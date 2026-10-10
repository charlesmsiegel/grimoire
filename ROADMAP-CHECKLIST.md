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
update this file in the same PR.

## Lifecycle of a spec

1. `drafted`
2. `spec gate`: `/codex:adversarial-review`, or a recorded substitute
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
| 01a | Eval cost, latency and token reporting | now | [x] | [ ] | [ ] | [ ] |
| 01b | Decision capture at every decide site | now | [x] | [ ] | [ ] | [ ] |
| 01c | Decision distributions and seeded sampling | now (switching a task on waits for 01a) | [x] | [ ] | [ ] | [ ] |
| 01d | Decision escalation and per-task policy | now (enabling escalation waits for 01a) | [x] | [ ] | [ ] | [ ] |
| 01e | Decision vocabulary: Rank, finer Score, MultiSelect, Joint | now | [x] | [ ] | [ ] | [ ] |
| 01f | Structured generation | now | [x] | [ ] | [ ] | [ ] |
| 01g | Tool calling, Decision as a tool, run budgets | 01f | [x] | [ ] | [ ] | [ ] |
| 01h | Embedding options, async embed, embedding evals | now (C6 waits for 01a) | [x] | [ ] | [ ] | [ ] |
| 01i | Context window as a resolved model fact | now | [x] | [ ] | [ ] | [ ] |
| 02 | Decision integration (what 01's slices F–H did not land) | 01a, 01b, plus 01c/01d per feature | [x] | [ ] | [ ] | [ ] |
| 03 | Content-addressed compiled cache | now | [x] | [~] substitute + PR Codex review | [ ] | [ ] |
| 04 | Instant Worlds, Campaigns, Todo and shell | 03 | [x] | [ ] | [ ] | [ ] |
| 05 | Direct-edit cache sync | 03, 04 | [x] | [ ] | [ ] | [ ] |
| 06 | Store-editing skill | 05-C2 | [x] | [ ] | [ ] | [ ] |
| 07 | Explicit group membership | now | [x] | [ ] | [ ] | [ ] |
| 08 | Derived history SearchDocuments | 03 (C2c waits for 05) | [x] | [ ] | [ ] | [ ] |
| 09 | Hybrid historical retrieval | 08 (the turn path also waits for 01h-C4) | [x] | [ ] | [ ] | [ ] |
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
- [ ] **03-C3** `materialized`, with kind form `vector:<projection>:<space-digest>`
  and an optional `instance` column.
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
  deadline.
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
  C2b/C3/C4/C5), 01c-C1..C4 (H for C2 sampling), 01d-C1..C3 (H for C5a);
  01g-C1..C6 (H for C4 only); 01e-C1..C3 (S)
- [ ] 03 ← 01h-C3 (S)
- [ ] 04 ← 03-C1, 03-C2, 03-C4 (H); 03-C5 (H for C2a); 03-C6, 03-C9 (S)
- [ ] 05 ← 03-C2, 03-C3, 03-C4, 03-C5 (H); 04-C2b (H, a property relied
  on); 03-C1, 03-C8, 04-C2a, 08-C2c, 01h-C1/C3/C5 (S)
- [ ] 06 ← 05-C2 (H); 05-C1/C4, 04-C2b (S)
- [ ] 07 ← 03-C1/C2/C3 (S)
- [ ] 08 ← 03-C1, 03-C2, 03-C6, 03-C7 (H); 03-C3, 05-C3 (H for C2c);
  01h-C3 (H once C1 sends a type); 07-C2, 07-C3c, 01h-C1 (S)
- [ ] 09 ← 08-C1/C2/C3, 03-C6, 03-C7 (H); 01h-C4 (H for the turn path);
  01a-C1 (H for live evals); 07-C2, 07-C3c, 01e-C1/C2, 01h-C1, 01i-C1/C2,
  02-C5b (S)
- [ ] 10 ← 09-C1/C2/C3, 01f-C1 (H); 01a-C1 (H for live evals); 01f-C2,
  01d-C1/C2, 01a-C3, 02-C5b, 01b-C1 (S)
- [ ] 11 ← 09-C1, 09-C3 (H); 02-C5a (H for the Decision stage); 07-C1 (H for
  group overrides); 07-C3c, 10-C1, 08-C3b, 01b-C1, 01d-C1/C2, 01c-C4 (S)
- [ ] 12 ← 01g-C1..C5, 01i-C1/C2, 08-C3, 09-C1, 01a-C1/C2 (H); 01d-C2b,
  10-C2/C3, 11-C1/C2 (H for RP mode); 11-C4 (S)
- [ ] 13 ← (nothing for II-A to II-D); 01c-C2/C3/C4 (H for C3); 01e-C3a (H for
  multi-target actions: action first, then a MultiSelect over targets);
  01e-C3b (S, single-target joint pairs only); 01c-C1, 01b-C1, 01a-C1, 02-C2
  (S). Legal sets past 254 options need nothing from 01e: they take two steps,
  the action and then the target.

## Shared structures: whichever spec lands first adds them

- `routing.TaskPolicy`: 01c and 01d.
- `inference._Call.stage` / `positions`: 01a and 01b.
- `decisions.CallRecord`, `Decision.calls`: 01a, used by 01d.
- `ItemResult.served`: 01d, read by 01c.
- `routing.NO_LEGACY` sentinel for routes new at format 2: 01g
  (`tool-decision`), 09 and 10 (`history_check`, `history_plan`), 02-C5.
  Any of these may add it.
- `decide(response_id=)` and `responses.mint_id()`: 02.

## Cross-spec decisions

Recorded here so that every spec says the same thing. A spec that disagrees
should change this section rather than drift from it.

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
- [ ] **Delete `ROADMAP-CHECKLIST.md`**, in that same PR.

The specs stay. Only this ledger goes. Its job ends when there is nothing left
to coordinate.
