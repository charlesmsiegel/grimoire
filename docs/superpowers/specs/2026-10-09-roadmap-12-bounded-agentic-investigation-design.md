# 12. Bounded agentic investigation

**Status:** Draft — spec gate (substitute review) folded in; Codex gate pending.
**Date:** 2026-10-09
**Roadmap:** 12 in `ROADMAP-CHECKLIST.md`. Lane: retrieval (… → 09 → 10 → 11
→ **12**, with 01g). It is the last item of the lane and has no consumers.
**Baseline:** `main` at `35c1fb7`.
**Reconciles:** the bundle draft `12-bounded-agentic-investigation.md`
(2026-10-06, written against `7f80c42`). It builds on the continuity
capstone's proposal and review machinery
(`2026-10-04-continuity-capstone-design.md` §6, §11, §12), which has landed.
Contract IDs follow `ROADMAP-CHECKLIST.md`, including its cross-spec
decision on unpriced models under a spend ceiling (section 7.2).

> Implementation rule: re-read the code and every PR landed on this subsystem
> since the baseline before planning. 01g, 09, 10 and 11 were specified in
> parallel with this document. Re-check the points in section 15 against
> their landed form before the plan starts.

## Depends on

The checklist edge reads `12 ← 01g-C1..C5, 01i-C1, 08-C3, 09-C1, 01a-C1/C2
(H); 01d-C2b, 10-C2/C3, 11-C1/C2 (H for RP mode); 11-C4 (S)`. **This spec
drops `01d-C2b`** (review B1, section 17): a 01d resolver must return
`ItemResult`s that replace an answer, and E1 returns evidence, so E1 is
triggered from 10's `PlanTrace` instead (section 3.2). The checklist edge
should read `10-C2/C3, 11-C1/C2 (H for RP mode)` without it, and gain
`01i-C2` (H).

| Contract | Provided by | What this spec uses it for | Hard or soft |
|---|---|---|---|
| 01g-C1 `tools` capability, probe, seam `incapable` refusal | 01g | The `investigation` route `requires=("tools",)`. A primary known not to call tools is refused at the seam, and the entry point is skipped | Hard |
| 01g-C2a tool calling over OpenRouter, OpenAI-compatible and Anthropic; the loop primitive (`inference.run_tools`): the caller executes the tools, supplies the run id, and gets the final call back | 01g | The loop itself (section 5) | Hard |
| 01g-C2b Claude Agent SDK tools (until it lands, `claude` reads as unable to call tools) | 01g | Nothing beyond C1's refusal: a `claude` primary is skipped until C2b | Part of the `01g-C1..C5` hard edge; only bounds which adapters can serve |
| 01g-C3 a ledger row per loop turn with `run_id` and `loop_turn`, plus capture | 01g | Every model turn of an investigation is attributed to the run (sections 8, 9) | Hard |
| 01g-C4 run budget (turns, tool calls, decisions, wall clock, spend ceiling, per-turn output cap); reports which limit stopped the run; refuses an unpriced model under a ceiling before sending | 01g | The budgets of section 7 | Hard |
| 01g-C5 decide tool on the `tool_decision` route, capped per run, no recursion, task name supplied by the caller | 01g | The `decide` tool in maintenance modes (section 4.4) | Hard for that tool only |
| 01i-C1 `wire.Limits(window, max_output)` with a source, on every target; 01i-C2 `prompt_ceiling(resolved, reserve=None, max_tokens=None)` | 01i | The loop's context ceiling, derived through `prompt_ceiling` (section 7.3) | Hard (with a structural fallback when the ceiling is `None`) |
| 08-C3b `expand(...)`, the caller naming the phase, posts keyed `r-<response_id>` / `p-<post_id>` plus `part` | 08 | `get_scene_excerpt` in the prompt phase (section 4.3). `search_history` embeds its query through 09 under 09's `history-recall` task, not 08's `history-index` | Hard |
| 09-C1 `history.retrieve(Query) -> Evidence` with a `perspective` seam | 09 | `search_history` is 09's retrieval, not a second one | Hard |
| 01a-C1 / 01a-C2 eval cost, latency and token reporting; metered live evals | 01a | The eval gate compares cost and latency, not only correctness (section 10) | Hard |
| 10-C2 one repair hop and its `PlanTrace` | 10 | E1's trigger is the trace's terminal state and verdict, and its seed names what 10 tried (section 3.2) | Hard for RP mode |
| 10-C3 the evidence-sufficiency predicate on `history_check` | 10 | Its last verdict, `insufficient`, read off the trace, is half of E1's trigger. 12 never re-runs it | Hard for RP mode |
| 11-C1 per-actor classes | 11 | Every tool result in RP actor mode is filtered through it (section 4.2); E1's selection is re-classified at finish (6.1) | Hard for RP mode |
| 11-C2 narrator and actor prompt separation | 11 | What an RP investigation selects is rendered through 11-C2, never as model prose (section 6.1) | Hard for RP mode |
| 11-C4 leakage eval suite | 11 | The eval gate's leakage line (section 10) | Soft |

## Required by

None. 12 is the end of the retrieval lane, and no roadmap spec consumes its
contracts.

## 1. Current state (reconciled against main)

### 1.1 Nothing calls tools today

- The Claude Agent adapter is pinned to a single turn with no tools:
  `ClaudeAgentOptions(system_prompt=…, model=model, allowed_tools=[],
  max_turns=1)` (`backend/src/grimoire/claude_agent.py:161-163`).
- The capability vocabulary has no `tools`: `NAMES = ("generate", "stream",
  "vision", "embed", "decide_native", "structured_output", "prefill")`
  (`store/inference/capabilities.py:54-55`). The 01 spec left it out on
  purpose: "`tools` is out: no call site uses it"
  (`2026-10-07-inference-backend-refactor-design.md:782`).
- Provider structured mode is set only for `decide`. No route, adapter or
  meter knows about a multi-turn exchange. `usage.meter` has no run id
  (`store/usage.py:629`).

01g is therefore a prerequisite in substance, not only in name. 12 adds no
tool plumbing of its own: it uses 01g-C2a's `run_tools`, 01g-C4's budget and
01g-C5's decide tool.

### 1.2 The read side already exists, as functions

Most of the draft's tools are existing store readers. None takes a model call,
and most are already what a UI surface reads:

| Draft tool | Reader on main |
|---|---|
| `get_record` (thread, commitment) | `continuity.effective.records(cid, kind)` (`store/continuity/effective.py:385`), `threads`/`commitments` (`:431-455`) |
| `get_record` (fact, event) | `facts.get` (`store/facts.py:107`), `events.get` (`store/events.py:231`) |
| `get_record_history` | thread and commitment beats, `relationship_history.for_pair` (`store/relationship_history.py:153`), the facts supersession chain (`facts.py:226-311`) |
| `get_related` | `continuity.effective.links` (`effective.py:248`), `involvement.of` (`store/continuity/involvement.py:144`) |
| `get_involvement` | `involvement.of`, `involvement.scene_actors` (`:101`) |
| `get_scene_summary` | `chronicle.get_record` (`store/chronicle.py:53`), plus scene metadata (`scenes/read.py:202`) |
| `get_scene_transcript` | `scenes.read.read_scene_window(cid, sid, limit, before)` (`store/scenes/read.py:169`) |
| `get_timeline` | `timeline.build` (`store/timeline.py:118`), `events.list_events` (`store/events.py:209`) |
| `get_pressure` | `continuity.pressure.build` (`store/continuity/pressure.py:393`) |
| `get_actor` | `casefile.build` (`store/casefile.py:97`) |
| `search_history`, `find_similar` | nothing campaign-history-shaped yet: 08/09 build it. `store/search.py` and `store/semsearch.py` are library-wide reader search (below) |

Two of these are deliberately **not** toolable as they stand:

- **`store/search.py` walks every file** in a campaign, including every
  actor's `state.md` and `dossier.md` sidecars and raw scene transcripts
  (`store/search.py:24-31`). It returns snippets of **raw** text, not the
  regex prompt view. Handing it to a model would bypass the prompt phase
  (`test_regex_prompt_guard.py`), hidden posts (`chronicle.transcript_text`
  filters them; `search.py` is a reader's tool and does not), and every
  perspective rule.
- **`continuity.graph.build`** (`store/continuity/graph.py:830`) builds the
  whole campaign graph per call. It is the right shape for a page, and the
  wrong grain for one tool call.

Two more need a different backing than the one the table names:

- **`casefile.build(cid, sid, kind, actor_id)`** takes the full
  `campaign_lock` (raising `StoreBusy` on contention) and raises `AppearError`
  unless the actor is in scene `sid` (`store/casefile.py:97-125`). A tool with
  no scene (E2, E3), or about an actor not on stage, could never call it.
- **`timeline.build`** takes the full `campaign_lock` too
  (`store/timeline.py:142`), which is why `continuity/graph.py` re-derives
  play order instead of calling it (`graph.py:34-38`).

### 1.3 The write side already exists, as proposals

The capstone made model output a **proposal** that a reader applies:

- `continuity_candidates.json` is a derived cache of `records`, each with a
  `kind` (`possible_duplicate`, `possible_relation`,
  `possible_thread_closure`, `possible_commitment_resolution`;
  `store/continuity/candidates.py:66-70`) and a `proposal` whose text fields
  are `decision`, `from`, `to`, `relation`, `status` and `reason` (`:83`),
  plus `evidence_scenes`.
- The sweep's model answers are rebuilt field by field (`reconcile._decide`,
  `store/continuity/reconcile.py:1409-1424`). Evidence scenes not in the
  run's known scene set are dropped, and a proposal with no known evidence
  scene is downgraded to `uncertain` (capstone §11.4: "a lifecycle proposal is
  never materialized with fabricated evidence").
- `reconcile.persist_proposals` (`reconcile.py:898-922`) sets a proposal only
  on a record still cached and still `live`, under the campaign lock, through
  `_commit`'s stillborn check.
- Only `continuity.review` and the route layer apply reviewed operations, and
  `test_continuity_writer_guard.py` holds that (capstone §11.6).

That is exactly the draft's "proposal tools feed existing deterministic
validation/review/apply pipelines". 12 reuses it, and narrows it (section 6).

### 1.4 The runtime already has the classes 12 needs

- Run classes: `turn`, `review`, `background`, `draft`, `maintenance`
  (`routes/runs.py:104`). Only `turn` and `review` hold an exclusion key
  (`:108`, `:225-241`). `maintenance` holds the one store-wide key
  `MAINTENANCE_KEY` (`:116`).
- `reserve_campaign_background` gives a campaign one live `background` run of
  a kind, and adopts the live one (`runs.py:2001-2026`). The continuity
  sweep uses it: `post_reconcile` answers 202 under `@computes_only`, and its
  persists stamp the revision themselves (`routes/continuity.py:862-879`,
  `:936-950`).
- `run_draft` is the shared 202 contract for computing previews: reserve,
  adopt a duplicate, guarantee a terminal state, and hold the result on the
  run until it is reaped (`runs.py:2159-2188`; `REAP_SECONDS = 600`,
  `:66`). A draft holds no exclusion key and writes nothing durable.

### 1.5 Traces have a home

- The prompt log (`store/prompt_log.py`) is the opt-in, campaign-local,
  rolling capture of what a model was sent. The speaker pick files its outcome
  as a zero-token section, `OUTCOME_SECTION_ID` (`"decision"`), which the
  viewer draws as "outcome · not sent" (CLAUDE.md, decide section).
- Incoming responses are captured at Debug level by `store/logs.py` through
  `llm_capture.emit` (`backend/src/grimoire/llm_capture.py:49`). Request
  bodies, URLs and headers are never passed to that sink (CLAUDE.md,
  Observability).

### 1.6 Where the draft does not match the code

- The draft's mutator-free proposal tools included `propose_new_thread` and
  `propose_new_commitment`. The candidate cache has no kind for a *new*
  record (1.3), and absorb is the only creator of ledger rows. 12 drops them
  (section 13).
- The draft offered `get_scene_transcript` everywhere. In RP mode the actor
  contract forbids it (11), and in every mode it must go through the prompt
  view. It survives as a paged window in maintenance modes only.
- The draft proposed a "Grimoire-owned orchestrator" with its own provider
  abstraction and cancellation. The provider abstraction, metering and
  cancellation exist (01, the run registry). 12 owns only the toolset, the
  budgets per entry point, the trace and the validation of a final result.

## 2. Goal

Use a tool-calling model **only** where the evidence a correct answer needs
cannot be named in advance, and where the cheaper ladder has already failed:

```text
deterministic context
  -> 09 hybrid retrieval (09-C1)
  -> 10 planner + one repair hop (10-C1, 10-C2, 10-C3)
  -> 12 bounded investigation, only when 10's trace says cheap retrieval
     failed (RP), or when a person asks (maintenance)
```

It is bounded in every dimension 01g-C4 can bound, read-only over the store,
and perspective-safe. It writes nothing except proposals that a person
reviews, and it leaves a trace that answers "what did it look at before it
said this?".

**Not the goal:**

- an agent on every turn, or on every NPC line;
- a general agent runtime, a filesystem, shell or SQL tool, or raw vector
  access;
- any direct mutation of the ledger, the transcript, knowledge overrides
  (11-C3) or any record;
- replacing the reconcile sweep, absorb, the planner, or Decision.

## 3. Entry points: when investigation runs

There are three entry points. Each is a separate task, has a separate switch
(all **off** by default), has a separate budget and a separate result shape,
and must pass the eval gate separately (section 10).

| | E1: RP escalation | E2: Continuity investigation | E3: History question |
|---|---|---|---|
| Who starts it | The turn path, when 10's trace says cheap retrieval failed, or the player's explicit "deep history" option on a send | A reader, from a continuity candidate's detail ("Investigate") | A reader, from the campaign ledger ("Ask the history") |
| Perspective | The actor step's perspective (11-C1), or the narrator | Narrator (author-facing) | Narrator (author-facing) |
| Output | A **selection** of history evidence ids, rendered through 11-C2 | One **proposal** for that candidate, or `no_change` / `insufficient_evidence` | An **answer** with citations, never stored |
| Write posture | None | Proposal on the candidate cache only | None |
| Runtime | A stage inside the turn run (no new run) | `background` run on `("campaign", cid)` | `draft` run on `("campaign", cid)` |
| Task | `investigation-turn` | `investigation-continuity` | `investigation-question` |
| Switch (config key) | `investigation_rp` | `investigation_continuity` | `investigation_question` |

### 3.1 When it never runs

- When the switch is off, which is the default for all three.
- When the `investigation` route resolves to a primary that is *known* not to
  call tools: 01g-C1's seam refuses with 409 `incapable`. A `claude` primary
  reads as unable until 01g-C2b lands. E1 then skips. E2 and E3 surface the
  409 at the start route, before anything is reserved. `unknown` is allowed,
  per the seam rule.
- When a spend ceiling is set and any attempt on the chain is unpriced
  (01g-C4; the checklist's cross-spec decision). Section 7.2.
- On a reroll, Retry, Keep writing or roll continuation. These replay the
  frozen prompt snapshot (`responses.prepare`), which already holds what the
  original turn selected. The turn path also skips retrieval entirely for a
  round with a pending incomplete response (11 section 5.4).
- On an opener, and on a director turn that carries no note.
- For calendar arithmetic, check or effect resolution, graph construction,
  absorb extraction, or any context deterministic code can assemble.

### 3.2 E1's trigger, precisely

**E1 is triggered from 10's outcome, not through 01d.** A 01d `Resolver` must
return `ItemResult`s that replace the triggered item's answer (01d section
5.2), and an investigation returns evidence, not an answer to "is this
sufficient?". 01d also escalates only on a non-answer or a low margin
(01d-C2a), never on a read `insufficient`, and 10 runs its check *before* the
repair hop and none after (10 section 8). So the trigger reads 10's
`PlanTrace` after `plan_and_repair` returns (10-C2), inside the actor step's
`gather` (11 section 6.1):

1. 09 retrieves (09-C1). If 09-C2's coverage is sufficient, 10 does not run,
   the trace is `not_run`, and E1 does not run.
2. 10 plans, checks sufficiency (10-C3, task `history-sufficiency`, with
   whatever 01d policy 10 declares for its own check), and runs its one
   repair hop when the check says `insufficient`.
3. **E1 runs when the trace's terminal state is**:
   - `repaired`: the check said `insufficient` and the one hop ran. 10 runs
     no check after its hop, so the last verdict on record is `insufficient`.
     12 does not re-run the predicate to confirm (a second paid call per
     turn); open question 5 asks whether 10 should add a post-repair check;
   - or `planned` with an `insufficient` verdict and the repair not run
     (`skipped:<why>`, for example the call cap): "planned with an
     `insufficient` check that could not repair", which is 10's own consumer
     row.
   An `unknown` verdict (a non-answer after 10's own escalation) does not
   trigger E1: cheap retrieval did not say it failed.
4. E1 runs at most **once per round** (once per player post), for the first
   actor step whose trace triggers it. The round record carries
   `investigated: <actor ref>` (`responses.update_round`), set before the loop
   starts, so a resumed or recovered round does not run E1 twice. Later steps
   in the round proceed on 09/10's evidence.

**What 10 must expose in `PlanTrace`** (an interface requirement on 10-C2,
section 15): the terminal state; the last 10-C3 verdict and whether it was an
answer, an escalated answer or `unknown`; the trigger; the questions and terms
each round tried; and the scene keys and evidence ids of `E0` and of each
round, so E1's seed can say what was already tried.

The **"deep history"** option on a send (a one-shot turn field, carried like
the response-length chip in `_turn_override`) sets 10's `force=True` and
starts E1 for the round's first actor step regardless of the verdict. It is
still subject to every budget, and it is still once per round.

E1 is **fail-soft**: every terminal state other than `completed`, including
`failed`, `refused` and `budget_exhausted`, leaves the step on 09/10's
evidence. An investigation never fails a turn.

## 4. The toolset (12-C1)

### 4.1 Shape

Every tool is a 01g `tool_calls.Tool` (`name`, `description`, `parameters`,
`fn`, `effect`, `terminal`, `timeout`, `max_result_chars`), declared in
`store/investigation/tools.py` and grouped into one `tool_calls.Toolset` per
mode. `fn(args, ctx)` returns a 01g `ToolOutput(text, refs, proposal)`:
`text` is the JSON envelope below, `refs` the store refs inspected (for the
trace), and `proposal` is set only by the E2 `propose` tool
(`effect="propose"`). The investigation's own state rides beside 01g's
`ToolContext` in a run-local object:

```python
@dataclass
class RunState:
    cid: str
    sid: str                              # the scene the run serves ("" for E2/E3)
    mode: str                             # rp_actor | rp_narrator | continuity | question
    perspective: epistemic.Perspective    # 11-C1; bound by the run, never by the model
    inspected: set[str]                   # scene keys whose transcript text was returned (6.2)
    evidence: dict[str, EvidenceItem]     # evidence id -> 09-C1 item, for E1's selection
```

**The perspective is bound by the run, never passed by the model.** No tool
takes a `perspective`, `as`, `viewer` or `actor` argument that changes what
it may return.

The envelope is:

```json
{"ok": true,
 "items": [{"id": "e7", "ref": "scene:0007--the-pier", "title": "The pier at dusk",
            "text": "...", "truncated": false}]}
```

or `{"ok": false, "error": "<code>"}` with `error` one of `not_found`,
`not_in_mode`, `bad_args` or `unavailable` (a reader failed). Errors are tool
results, not exceptions: the loop continues and the step is traced. 01g's
`max_result_chars` and `max_result_chars_total` bound the text (7.1).

**`not_found` is also the answer for "exists but not visible to this
perspective"**, and a visible-but-empty search answers exactly as a search
over nothing. A distinct `withheld`, or a non-empty result whose content
depended on a hidden post, would let the model probe for what it may not
read.

### 4.2 Rules every tool obeys

1. **Read-only.** A tool calls no mutator. `test_investigation_writer_guard.py`
   (section 12) holds this by import binding.
2. **The prompt view, over the whole transcript.** Any transcript text goes
   through `regex.view.view(messages, cid=…, phase="prompt", offset=…,
   total=…)` with the window's true offset and the transcript's length, or
   over the whole transcript before slicing. Depth-ranged rules count from the
   newest post of the scene (`store/regex/view.py:49-73`), so viewing a window
   alone would apply them at the wrong depth. Then `scenes_serialize.in_context`
   removes hidden posts and director notes. The package is added to
   `test_regex_prompt_guard.py`'s scan, and the window and excerpt readers are
   pinned by name.
3. **`gm-only` bodies never.** An entity whose secrecy is `gm-only` is never
   returned with its body (`store/entities.py:26-37`). In N, C and Q its name
   and kind may be returned; in R it is `not_found` (rule 4).
4. **Perspective filtering in rp_actor mode.**
   - Every history item passes through 11-C1 for the run's perspective. An
     item with no visible slice is omitted entirely, ref and header included,
     and a header shows a title only where 11's summary rule allows it (11
     section 4.3).
   - Text search anchors only over the actor's visible posts (4.3,
     `get_scene_excerpt`), so a query that would match only a hidden post
     finds nothing, exactly as a query that matches nothing.
   - An entity the actor does not `know` (`actor.knows`,
     `store/context/actor.py:54-72`) is `not_found`, name included.
   - Overrides (11-C3) are never returned.
   - Ledger tools (threads, commitments, facts, timeline, pressure, other
     actors' state) are not offered, because the actor contract blanks those
     sections (`store/context/assemble.py:521-538`).
5. **Bounded per call.** Each result is clipped at a post or line boundary to
   the tool's `max_result_chars`, and `truncated` says so; 01g counts the run's
   total against `max_result_chars_total`.
6. **Refs, never paths.** Arguments name store refs (`scene:<sid>`,
   `thread:<id>`, `commitment:<id>`, `event:<id>`, `fact:<id>`,
   `characters:<id>`, `pcs:<id>`, `groups:<id>`, `locations:<id>`,
   `lore:<id>`, `items:<id>`), parsed by `continuity.canon`'s rule and checked
   against the campaign's live records. A path-shaped or unknown ref is
   `not_found`.
7. **No full lock, and none across a model call.** A tool takes
   `locks.best_effort_campaign_lock(cid)` for its own reads, as
   `continuity.graph` does, never `campaign_lock`, and releases it before
   returning.
8. **This campaign only.** History retrieval is pinned to `tier_limit=2`
   (09-C1) in every mode, so no tool reaches another campaign's history (09's
   tier 3). 09 section 11.1 names this tool as tier 3's only caller; with this
   pin tier 3 has none, which 09 should record.

### 4.3 The tools

`R` is rp_actor, `N` rp_narrator, `C` continuity and `Q` question.

| Tool | Args | Backed by | Modes | Notes |
|---|---|---|---|---|
| `search_history` | `query: str (<=200 chars)`, `max: int (<=6)` | 09-C1 `retrieve` with `Query(texts=(query,), perspective=<run's>, tier_limit=2)`, then 11-C1 `classify` in R | R N C Q | Returns evidence ids, scene refs, headers and visible texts. The query embeds through 09 under 09's `history-recall` task. With no embeddings it runs structural plus lexical, as 09-C2 does |
| `get_scene_summary` | `scene` | `chronicle.get_record` + `scenes.read.read_scene_meta` (date, location, cast names) | N C Q; R only where 11's summary rule makes it visible (a whole-scene `knows`/`suspects` override) | A summary is narrator-authored (11 section 4.3) |
| `get_scene_excerpt` | `scene`, `query: str`, `max_posts: int (<=8)` | 08-C3b `expand(cid, sid, phase="prompt", terms=…, radius=…, max_excerpts=1, max_bytes=…)` | N C Q; R once 08-C3b takes an admission predicate (below) | `terms` are `search.query_terms(query)` (`store/search.py:174`) capped at 08's `MAX_TERMS`; `radius = (max_posts - 1) // 2`; `max_bytes` the tool's result cap |
| `read_scene_window` | `scene`, `before: int or null`, `limit: int (<=12)` | `scenes.read.read_scene_window` (`store/scenes/read.py:169`), viewed per rule 2 | C Q | The draft's "expensive, explicit" transcript read, paged and capped. Never in RP |
| `get_record` | `ref` | `effective.records`, `facts.get`, `events.get`, `overlay.read_entity` | N C Q; R for `lore`, `locations`, `items` the actor `knows` only | Thread and commitment titles, status and latest beat |
| `get_record_history` | `ref` | beats; facts supersession; `relationship_history.for_pair` for `pair:<a>+<b>` | N C Q | The latest `HISTORY_ROWS` rows |
| `get_related` | `ref` | `effective.links`, `involvement.of` | N C Q | Reviewed links and touched scenes and actors; the draft's `get_involvement`, folded in |
| `get_timeline` | `from`, `to` (str or null) | a best-effort reader: `chronicle.read_chronicle`, scene ids in sorted order (graph's play-order rule) and `events.list_events`, under `best_effort_campaign_lock` | N C Q | Not `timeline.build`, which takes the full lock (1.2). Dates through the calendar provider's rendering, never model arithmetic (capstone §3.8) |
| `get_pressure` | (none) | `pressure.build` | C | Deadlines and staleness, as the sweep sees them |
| `get_actor` | `ref` | a scene-free best-effort reader: `playstate.read_state`, `dossiers.read`, the actor's `relationships` rows, and 11-C3 overrides naming the actor (status and subject only, never the note) | N C Q; R own ref only (own `state.md` and `actor.own_relationships`, no overrides) | Not `casefile.build`, which needs the scene and the full lock (1.2) |
| `get_group` | `ref` | entity + 07-C1 members and leader + `groupstate` | N C Q | Secret groups' state only in C and Q; gm-only bodies never |
| `decide` | per 01g-C5 | 01g-C5 decide tool | C Q | Section 4.4 |
| `finish_evidence` | `ids: [str] (<=6)`, `note: str (<=200)` | (terminal) | R N | Section 6.1 |
| `propose` | `decision`, `from`, `to` (candidate letters `A`/`B`), `relation`, `status`, `evidence_scenes: [ref] (<=3)`, `reason (<=280)` | (terminal, `effect="propose"`) | C | Section 6.2 |
| `answer` | `text (<=1200)`, `citations: [ref] (<=6)` | (terminal) | Q | Section 6.3 |
| `finish_no_change` | `reason` | (terminal) | C | Writes nothing (6.2) |
| `finish_insufficient_evidence` | `reason` | (terminal) | R N C Q | A valid, successful outcome |

**`get_scene_excerpt` in R needs one thing from 08-C3b**: an admission
predicate (`admit: Callable[[int, dict], bool]`, a post index and post) that
`expand` applies *before* matching terms and placing windows, so a window can
only anchor on, and only contain, posts the actor may see. Filtering after
`expand` is not equivalent: an anchor on a hidden post still pulls in its
visible neighbours, and the non-empty result answers "does this word appear in
scene 7?" against hidden text. Until 08-C3b takes the predicate, R does not
offer the tool; `search_history`'s classified units are R's only excerpt
source.

Constants: `TOOL_RESULT_CHARS = 6000` per tool (01g's `max_result_chars`),
`HISTORY_ROWS = 12`. Both are argued from the per-run allowances in section 7
and are to be tuned against real prompts later.

Not offered, in any mode: arbitrary file reads, `store/search.py` and
`store/semsearch.py` (1.2), raw vectors, `continuity.graph.build`, shell or
SQL, and any tool that names a path. Draft tools dropped:

- `find_similar`: `search_history` is the history similarity search, and
  record similarity is the continuity sweep's candidate generation, which E2
  already starts from;
- `get_recent_story(n)`: the current scene and recap are already in the
  turn's prompt (and blanked for an actor by contract), so a tool would
  duplicate them;
- `get_involvement`: folded into `get_related`.

### 4.4 Decision as a tool (01g-C5)

In C and Q only, the model may call `decide` for a closed sub-question it did
not know to ask in advance, for example "does this excerpt show the debt was
paid?".

- **The route and task are 12's own**: `Route("investigation_decide",
  "Investigation checks", …, ("investigation-decide",), True,
  operation="decide", default_role="decision", legacy=routing.NO_LEGACY)`.
  01g-C5 takes the task name from the caller and accepts a consumer's own
  decide route (01g section 3.12), so investigation sub-questions meter, gate
  and fail apart from play's `tool_decision` ones.
- The tool's shape is 01g-C5's: one `Choice` over the options the model names,
  `allow_none` as it asks, and a context capped by 01g-C5 (the bound part
  first, then the model's). 12 binds the mode and the candidate (E2) or the
  question (E3) as the bound context. If 01g-C5's soft resolution refuses,
  the tool is not offered and the run proceeds without it.
- **The per-run cap is `RunBudget.max_decisions`**: 2 for C, 1 for Q, 0 for R
  and N. RP mode's Decision-as-tool is 02-C4's business, behind its own gate.
- With a spend ceiling set, 01g offers the tool only when its resolution
  answers on structured stages alone (01g section 3.9).
- A `decide` call **cannot start an investigation**: 01g-C5 has no recursion,
  the decide backend is offered no tools, and `run_tools` refuses to run
  inside itself.
- The answer comes back as a tool result: the answer, any probability the
  backend reported (never a fabricated one), and `abstained` when it is
  `None`.

## 5. The loop

`routes/investigation.py` owns the three entry points and the driver. The
store package owns tools, prompts, validation and the trace, which is the
house split: LLM calls in the route layer, prompt text and parsing in the
store.

```python
async def investigate(app, *, state: RunState, task: str, seed: Seed,
                      client: LLMClient, resolved: ResolvedInference,
                      budget: tool_calls.RunBudget, run_id: str,
                      post: int | None = None) -> Outcome
```

1. **Seed.** Build the opening messages from `templates/investigation/<mode>/`:
   - E1: the actor-visible recent exchange (`observed_history`, prompt view),
     the evidence 09 and 10 already tried **as `view.visible()` shows it**
     (ids and visible headers only), and 10's planned questions only as
     terms already filtered through the actor's planner input (10-C1 takes
     the perspective);
   - E2: the candidate's vocabulary and records as the sweep shows them
     (`reconcile.build_payload`, `reconcile.py:1309`), with records named by
     letter;
   - E3: the reader's question and the campaign date.

   The seed contains no tool results and no hidden posts.
2. **Run** 01g-C2a's loop primitive:

   ```python
   result = await operations.run_tools(
       task, messages, toolset=toolsets[state.mode], execute=<12's executor>,
       client=client, resolved=resolved, budget=budget,
       campaign=state.cid, scene=state.sid, post=post, run_id=run_id,
       capture=<01g-C3 turn capture>)
   ```

   The executor runs each tool's `fn` through `anyio.to_thread.run_sync`
   (tools read files), never on the event loop, and records the trace step.
3. **Stop** at the first terminal tool call, which `run_tools` returns to the
   caller unexecuted for validation (section 6); at a budget limit (01g-C4
   refuses the next send and reports which limit); on cancel (the run's
   cancel flag; `run_tools` abandons an in-flight call as
   `common._bounded_call` does, `routes/common.py:584-600`); or on a failed
   call.
4. **Return** an `Outcome`:

```python
@dataclass(frozen=True)
class Outcome:
    state: str            # completed | no_change | insufficient_evidence | budget_exhausted
                          # | refused | failed | cancelled
    limit: str = ""       # budget_exhausted: 01g-C4's turns | tool_calls | decisions | wall
                          #   | spend | result_chars, or 12's own `context` (7.3)
    reason: str = ""      # refused: unpriceable | incapable
    error: str = ""       # failed: the LLMError kind, never provider text
    result: dict | None = None   # the validated terminal payload
    trace: dict = field(default_factory=dict)   # section 8
```

A final turn that ends with prose and **no tool call** is an implicit
`finish_insufficient_evidence` with no reason. The prose is dropped and never
shown.

## 6. Write posture (12-C3)

### 6.1 E1: a selection, never prose

`finish_evidence(ids)` names evidence ids. Validation:

- **only history evidence ids count**: ids `search_history`,
  `get_scene_summary` or `get_scene_excerpt` returned in this run, each held
  in `RunState.evidence` as a 09-C1 `EvidenceItem` (scene identity, header,
  optional summary, `EvidencePost`s with 08's keys). Ids of threads, facts,
  timeline rows or casefiles are dropped: 09-C3 and 11-C2 render scene
  evidence only;
- the kept items are re-classified through 11-C1 for the step's perspective
  at finish time, since the store may have moved during the run, and dropped
  unless they have a visible slice;
- at most `RP_SELECT_MAX = 6` are kept.

The kept items are merged into the step's evidence (`history.merge`, 09-C1)
ahead of 10's, and render through 11-C2 inside 09-C3's ceiling. Nothing the
investigating model *wrote* reaches the turn's prompt. The note is trace-only.
The worst an investigation can do to a turn is choose evidence the actor was
already entitled to; how it chose is what the eval's selection-level leakage
line watches (10.3).

### 6.2 E2: one proposal for one candidate

An E2 run is about exactly one candidate, named at start. `propose` takes the
candidate vocabulary's fields, with `from` and `to` as the candidate's letters
(`A`, `B`), as the sweep's own items do (`reconcile._pair`). It is validated by
reconcile's rebuild:

- the `decision` must be a word of `reconcile.DECISIONS[vocabulary]`
  (`reconcile.py:968`), read through the same `_lifecycle_word` /
  `_temporal_word` / `_pair` path (`reconcile.py:1409-1424`), extracted as a
  public `reconcile.rebuild(item, cand, known)`;
- `known`, the positive-evidence floor of capstone §11.4, is **the scenes this
  run read transcript text from**: `RunState.inspected`, which only
  `get_scene_excerpt`, `get_scene_summary` and `read_scene_window` add to. A
  scene that merely appeared in a `search_history` list was not read, and
  does not count. A proposal citing a scene outside that set is downgraded to
  `uncertain`, mechanically;
- `reason` is clipped to `RECONCILE_REASON_CHARS` (`reconcile.py:217`).

**The persist lives in `reconcile.py`**, not in the investigation package:

```python
def persist_investigation(cid: str, key: str, proposal: dict, *,
                          started_fingerprint: str, started_proposal: dict | None) -> bool
```

Under `campaign_lock`, it writes only if the candidate record is still cached,
its verdict is still `live` (`pending.verdict`), its fingerprint equals the
one at start, and its stored `proposal` equals the one at start. It replaces
that one record's `proposal` and **leaves `generation` and `basis` as they
are**. It is neither fenced by a sweep's generation nor fences one:

- `reconcile._commit` checks `_superseded(sweep.stamp, stored["generation"])`
  and then writes the sweep's stamp (`reconcile.py:761-790`). Routing E2
  through `_commit` with a stamp of its own would supersede a sweep that
  started earlier and lands later, dropping every proposal that sweep paid
  for. Reusing the stored generation instead would be invisible to the sweep,
  which is the same as not touching it.
- A sweep that commits after an investigation keeps or replaces the
  investigated proposal by its own rules. Either way the newer write is
  authoritative, which is the rule for every proposal in the cache.

It bumps the campaign revision once when it writes (`revision.bump`), as the
sweep's persists do, because the 202 that started the run never waits for it.
It returns whether it wrote, and the run's result carries `persisted`.

The stored proposal gains one field, `investigation`: the trace summary of
section 8, so the review's candidate detail can show what was read.
`candidates.read`'s tolerant normalisation keeps the field when it is a dict
of the trace shape and drops it otherwise; it does not join `_PROPOSAL_TEXT`
(`candidates.py:83`) because it is not text.

**`finish_no_change` and `finish_insufficient_evidence` write nothing.** The
sweep's `select` asks only candidates whose `proposal is None`
(`reconcile.py:1054-1083`), so storing an `uncertain` for a no-change answer
would stop the sweep from ever adjudicating that candidate again.

Application is unchanged: the reader applies or dismisses through
`POST …/candidates/{id}/apply` and `…/dismiss` (`routes/continuity.py:525`,
`:549`), which `continuity.review` validates and journals. No investigation
code calls a ledger, alias or link mutator.

### 6.3 E3: an answer held on the run

`answer(text, citations)` is validated: citations must be refs of this
campaign whose text a tool returned in this run (`RunState.inspected`), and
others are dropped. If every citation is dropped, the outcome is
`insufficient_evidence`. The result is held on the draft run and reaped
(`REAP_SECONDS`), never written to the store.

## 7. Budgets (12-C2a)

### 7.1 Per entry point

Each entry point hands `run_tools` a 01g `RunBudget`:

| `RunBudget` field | E1 RP | E2 Continuity | E3 Question |
|---|---|---|---|
| `max_turns` | 3 | 6 | 5 |
| `max_tool_calls` | 4 | 12 | 10 |
| `max_decisions` | 0 | 2 | 1 |
| `wall_seconds` | `E1_WALL_S = 20.0` for the whole loop, capped by `llm_call_budget` when positive (02-C6) | 3 × `llm_call_budget()` | 2 × `llm_call_budget()` |
| `spend_ceiling_usd` | `investigation_spend_ceiling` | same | same |
| `max_output_tokens` | 512 | 2048 | 2048 |
| `max_result_chars_total` | 12_000 | 48_000 | 36_000 |

- **E1's three turns** are search, read, then select. Its output cap is small
  because it writes a selection, not prose.
- **E1's wall clock is one named total deadline** for the whole loop, because
  E1 is a pre-generation step a player waits on, and 02-C6 (02 section 3.4)
  requires such a step to name its own total deadline rather than lean on
  `llm_call_budget` alone. `E1_WALL_S` is 02's `PLAN_CEILING_S` (20 s),
  argued the same way: an optional step must cost less waiting than the reply
  it shapes. It is capped by `llm_call_budget` when that is positive. 01g's
  rule is that `elapsed + MIN_TURN_SECONDS > wall_seconds` refuses the next
  turn, and each turn is bounded by what remains (01g section 3.9), so three
  short turns fit and a slow first turn leaves no room for a third. No
  `_noting` is attached to an overrun, per 02-C6. E1 also keeps 02-C6's other
  play-decision rules: its resolution is soft (`_soft_resolved`), the whole
  stage is wrapped in `except Exception` and recorded as `skipped`, and with
  `investigation_rp` off nothing is resolved and the prompt is byte-identical
  (02 sections 3.1-3.3). To be tuned against 01a's latency reports.
- **E1's result characters** are a small multiple of what it may select,
  which 09-C3's ceiling bounds.
- **E2 and E3** are runs a reader started and waits on behind a 202, so they
  get 01g's default turn and tool-call shape.
- **`investigation_spend_ceiling`** is in US dollars as 01g's estimator
  models a call (prompt tokens plus `max_output_tokens` at the model's rate,
  `pricing.rate_for_call`). It is **unset by default**, which means no spend
  axis: the run is bounded by turns, tool calls, wall clock and context. A
  campaign that is `over` its budget is **not** refused (01g open question 4,
  answered here): campaign budgets only warn, and measure a different column.
  E2's and E3's start dialogs show the campaign's budget level instead.

Every number is structural and is to be tuned through the eval gate (section
10). They live in `store/investigation/budget.py` and nowhere else.

### 7.2 Enforcement, and unpriced models

01g-C4 enforces every limit **before a call is sent**, and reports which limit
stopped the run. A limit reached ends the run as `budget_exhausted` with that
`limit`. It is not an error, and it files no error row.

**An unpriced model under a spend ceiling is refused before sending** (01g-C4;
the checklist's cross-spec decision). A ceiling cannot hold against a price
nobody reported, and counting such a call as free would break the cost rule.
12 skips the entry point and records why:

- **The check happens before anything is reserved.** `run_tools` raises
  `RunRefused(kind="unpriceable")` only once called, inside the run, so 12
  asks first: `tool_calls.check_priceable(resolved, budget)` (a requirement on
  01g, section 15), which applies 01g section 3.9's rule (with a ceiling set,
  every attempt on the chain must have a rate). Until 01g exposes it, 12's
  `investigation.priceable(resolved, budget)` applies the same rule through
  `pricing.rate_for_call` for each attempt of `resolved.chain`.
- **E1** skips for the round. The step proceeds on 09/10's evidence, and the
  reason `unpriceable` is filed in the turn's prompt-log capture (8.2) and in
  the status frame (9.1).
- **E2 and E3** refuse at the start route with 409 `unpriceable`, before
  reserving, as they refuse an `incapable` seam. The setting's hint says that
  setting rates for the model, or clearing the ceiling, lifts this.
- If rates are removed between the check and the first send, `run_tools`
  still raises; the run ends `refused` with reason `unpriceable` and spends
  nothing.

### 7.3 Context (01i-C1, 01i-C2)

Before each model turn the loop estimates the request's tokens (the `tokens`
module's counter where an encoder is loaded, else the packer's byte
heuristic) and refuses the call, as `budget_exhausted` with `limit:
"context"`, when the estimate exceeds

```text
prompt_ceiling(resolved, max_tokens=budget.max_output_tokens).tokens
```

01i-C2's ceiling is the smallest window on the chain minus a reserve sized for
the turn's own output cap. It is **never** `window - max_output`: a model's
stated maximum output is not what this turn asks for, and subtracting it
would leave almost nothing on a model whose stated maximum is near its
window. When `prompt_ceiling` returns `None` (no window known on some
attempt; 01i says unknown is `None`, never 0), the loop uses
`FALLBACK_PROMPT_TOKENS = 8192`: deliberately small, so an unknown model is
bounded by the turn and result caps long before it could overflow, and to be
raised once evals show it binds. Tool results are already clipped (4.2 rule
5), so the context check is a backstop.

## 8. Trace (12-C2b)

### 8.1 What it records

```json
{"version": 1, "run": "<run id>", "mode": "continuity", "task": "investigation-continuity",
 "model": "<provider_id>/<model>", "started": "<iso>", "outcome": "completed", "limit": "",
 "steps": [
   {"turn": 1, "tool": "search_history", "args": {"query": "the debt at the pier", "max": 4},
    "result": {"ok": true, "refs": ["scene:0007--the-pier", "scene:0012--saltmarch-quay"],
               "chars": 1840, "truncated": false}},
   {"turn": 2, "tool": "get_scene_excerpt", "args": {"scene": "scene:0012--saltmarch-quay",
    "query": "paid", "max_posts": 6},
    "result": {"ok": true, "refs": ["scene:0012--saltmarch-quay"], "chars": 2210, "truncated": true}},
   {"turn": 3, "tool": "propose", "args": {"decision": "resolved", "evidence_scenes":
    ["scene:0012--saltmarch-quay"]}, "result": {"ok": true}}],
 "inspected": ["scene:0012--saltmarch-quay"],
 "final": {"decision": "resolved", "reason": "...", "evidence_scenes": ["scene:0012--saltmarch-quay"]},
 "usage": {"model_turns": 3, "tool_calls": 3, "decisions": 0, "seconds": 11.4,
           "cost": {"cost_usd": null, "estimated_usd": null, "modelled_usd": 0.0041},
           "unpriced_calls": 0}}
```

- **No tool result text**, only refs, sizes and truncation. The text is in the
  store already; the trace says where it came from.
- **No reasoning, and no assistant prose** other than the validated final
  payload's own fields (`reason`, `note`, `text`). Reasoning feeds
  `llm_reasoning` as it does today and is never copied into a trace.
- **Query strings are kept**, clipped to 200 characters, as the only record of
  why a tool was called. They are model-written and may contain private prose
  derived from the store, so this trace is stored only where private campaign
  content already lives (8.2). It is 12's own trace, not 01g-C3's: 01g-C3's
  ledger rows and loop trace carry no argument or result text, and nothing
  here adds any to them.
- **Money is the three columns, never added** (CLAUDE.md, Costs), and
  `unpriced_calls` makes an incomplete figure say so.

### 8.2 Where it is stored

| Entry point | Where | Why there |
|---|---|---|
| E1 | The turn's prompt-log capture (`store/prompt_log.py`), as a zero-token section `investigation` shown as "investigation · not sent", beside the decide captures' outcome envelope (01b). Only when capturing is on | The prompt log is the existing opt-in, rolling debug store of what a turn's model saw. No new store |
| E2 | The candidate proposal's `investigation` field (6.2), in `continuity_candidates.json` | The review shows it beside the proposal it justifies. The file is a derived cache, so a later sweep that replaces the proposal drops the trace with it |
| E3 | The draft run's result, reaped with it | Nothing about an unstored answer is durable |

Every entry point also files 01g-C3's per-turn ledger rows under the run id,
the durable cost record, and 01g-C3's per-turn captures when capturing is on.

### 8.3 Privacy

- Ledger rows carry task, model, counts and money, never text (CLAUDE.md,
  Costs).
- `store/logs.py` receives decoded incoming SSE lines at Debug level only, as
  every adapter does, including the model's tool-call arguments. **Tool
  results are request content** and are never passed to the sink, by the
  existing rule that request bodies are never logged. The Settings disclosure
  about incoming capture must name tool-call arguments when 01g lands.
- Failures are recorded at `Meter.done` with kind and status only.

## 9. Runtime ownership (12-C4)

### 9.1 E1: a stage of the turn run

E1 runs **inside the turn's existing run**, in the actor step's `gather` after
`plan_and_repair` returns and before `_prepare`, on the event loop, outside any
campaign lock. It adds no run, no class and no exclusion key. The scene is
already held by the turn's key (CLAUDE.md: shape-changing routes are refused
`scene_busy`). Other scenes can change; 6.1's re-classification at finish
covers that.

- **Cancel**: the turn's cancel flag stops the loop between steps, and
  `run_tools` abandons an in-flight call.
- **Frames**: the turn stream may emit `{"type": "status", "phase":
  "investigating"}` while E1 runs and `{"type": "status", "phase":
  "investigated", "selected": n, "reason": <terminal state or
  "unpriceable">}` when it ends. This spec has not verified that the turn
  stream reader ignores an unknown frame type (`api.streamDraft` is the
  opener's client, not the turn stream's). The plan must find the turn stream
  reader, add the frame type with a test, and only then emit it.
- **Metering**: each loop turn meters under `investigation-turn` with the
  turn's campaign, scene and post, so the cost is charged to the player post
  as a reroll's is (CLAUDE.md, per-post attribution).

### 9.2 E2: a `background` run on the campaign

`POST /campaigns/{cid}/continuity/candidates/{candidate_id}/investigate`,
`status_code=202`, `@computes_only`, `def` (a route that reserves may not be
`async def`, CLAUDE.md):

- refused before reserving: 404 for an unknown candidate; 409 `not_live` for
  a candidate not `live`; 409 `incapable` from the seam; 409 `unpriceable`
  (7.2); 400 when `investigation_continuity` is off;
- reserves a `background` run, kind `continuity-investigate`, on
  `runs.campaign_subject(cid)`;
- **one live investigation per campaign.** A start for the *same* candidate
  adopts the live run (attempt-id semantics as `post_reconcile`); a start for
  a *different* candidate answers 409 `busy` with the live run's payload.
  `reserve_campaign_background` adopts any live run of a kind, so it gains a
  `match` predicate (the candidate key on the run's labels);
- runs `investigate`, then `reconcile.persist_investigation`. The run's result
  is `{"outcome", "persisted", "candidate"}`, polled through the campaign run
  routes (`routes/runs.py:1415-1435`);
- **why not the reconcile run**: folding E2 into the sweep would hold the
  campaign's sweep run for a whole investigation, delaying the next End
  Scene's incremental sweep behind an optional question;
- **why not `maintenance`**: that class holds the *store* against tree
  operations and the image collector (`MAINTENANCE_KEY`, `routes/runs.py:116`).
  Borrowing it would refuse a backup or a fork for the length of a model loop.

### 9.3 E3: a `draft` run on the campaign

`POST /campaigns/{cid}/history/question`, through `runs.run_draft`
(`runs.py:2159`), answering 202, with the same pre-reservation refusals as
E2. The client uses `api.draftRun`. A draft declares no exclusion key and
nothing it produces is durable (CLAUDE.md), which is E3's contract.

### 9.4 What this changes in CLAUDE.md

E2 and E3 add **two handlers** that start detached runs, one `background`
(`post_candidate_investigate`) and one computing draft
(`post_history_question`). The "Detached runs" section must name both, and its
counts rise by two handlers and one draft. Other roadmap specs change the same
counts (05 adds `POST /api/cache/sync`), so the PR that lands last writes the
totals. `test_docs_guard.py` checks the run classes named
(`test_claude_md_names_every_run_class`). The "Costs" and "Adding an LLM call
site?" paragraphs gain the `investigation` and `investigation_decide` routes.

The registry rules apply unchanged: `PUT /config/data-dir` is refused while
any of these runs is live, deleting a campaign forgets its runs, and a live
run keeps the Android foreground service promoted.

## 10. Eval gate before broad adoption (12-C2c)

### 10.1 Arms

`evals/run.py --investigate <mode>` compares, on one corpus:

- **A**: 09 hybrid retrieval only;
- **B**: 09 + 10 planner and one repair hop (the incumbent);
- **C**: B + 12 investigation on 10's failure trigger.

For E2 the arms are the reconcile sweep's answer (B) against the
investigation's (C) on the same candidates. For E3 they are a single
retrieval-plus-generate answer (B) against investigation (C).

### 10.2 Corpus

`evals/cases/investigation/` holds synthetic long-history campaigns, built
through the real store APIs with placeholder names only, where the needed
evidence is **adaptive**, not nameable from the query:

- a promise made in an early scene, referred to later only by a nickname
  introduced in between;
- "why did this thread change?", where the answer sits in a beat and a
  transcript two scenes apart;
- a duplicate, a continuation and a merely related pair whose distinguishing
  evidence is only in transcripts;
- a commitment whose resolution is narrated without its title;
- for E1, a callback ("you said you'd never…") whose source the planner's
  query misses;
- for E1, every 11-C4 leakage case replayed with investigation on;
- for E1, a **hidden-route** case: the only lexical route to a relevant
  visible scene runs through a word that appears only in a post the actor
  missed. The gold is that the search over that word returns exactly what a
  search over a nonexistent word returns.

### 10.3 Metrics

- Evidence recall at the selected set (E1); proposal correctness against gold
  (E2), with `uncertain` scored as an abstention; answer correctness and
  citation validity (E3).
- **Leakage**, on its own line, in two parts:
  - **text leakage**: 11-C4's needle graders over every message of the actor
    prompt;
  - **selection leakage**: from the run's trace, a selected item is
    *steered* when every tool call that returned it carried a query or
    argument containing a forbidden needle term, or when a tool result
    differed from the nonexistent-term baseline on a hidden-only term (the
    hidden-route case). The steered-selection rate is reported per case.
  Any leakage in arm C that B does not have fails the gate whatever the
  recall gain.
- Cost in the three money columns, latency, tokens, tool calls,
  `budget_exhausted` by limit, and `refused` by reason, through 01a-C1. Live
  runs are metered under 01a-C2's eval scope.

### 10.4 Adoption rule

Each entry point ships with its switch **off**. A switch's default may become
`on` only in a PR that records, in `evals/README.md`: the gate run's table;
that C beats B on that entry point's correctness metric by a margin the plan
states in advance; that neither leakage line is worse; and the cost and
latency it pays. Until then the setting's hint says it is experimental. **E2
is the pilot**: off the turn path, reviewable, with the reconcile corpus to
start from. E1 is last, because it is the only entry point a player waits on.

## Slices

Landing order within this spec: S1 → S2 → S3 → S4 → (S5 and S6 in parallel) →
S7. Every entry point lands with its switch `off`. E2 (S3) is the pilot, and
E1 (S6) the last entry point a player can reach.

### 12-S1: The narrator-side toolset

- **Delivers:** 12-C1 (part: the 4.1 envelope and the 4.2 rules for the
  `rp_narrator`, `continuity` and `question` modes; every 4.3 tool except
  `decide`, and except the rp_actor filtering)
- **Needs (this spec):** none
- **Needs (other specs):**
  - 01g-C2a (H: the `tool_calls.Tool`, `Toolset`, `ToolOutput` and
    `ToolContext` types);
  - 09-C1 (H: `retrieve` over a `Query` with `perspective="narrator"` and
    `tier_limit=2`);
  - 08-C3b (H: `expand(..., phase="prompt")` over a scene, with posts keyed
    `r-<response_id>` / `p-<post_id>` plus `part`).
- **Scope:** Adds `store/investigation/tools.py` and the scene-free,
  best-effort readers behind `get_actor` and `get_timeline`. The package is
  read-only and declared in no lock list. Extends
  `test_regex_prompt_guard.py` to scan the package and pins the excerpt,
  window and summary readers. Adds `test_investigation_writer_guard.py` and
  generalises `test_continuity_writer_guard.py` to `(package, modules)`
  pairs. No route, no model call, no switch.
- **Acceptance:** section 14's "Toolset" tests for N, C and Q: modes, refs,
  hidden posts, regex depth in a window, gm-only names, `tier_limit=2`, no
  `campaign_lock`, truncation. The writer guards fail on a planted violation.
- **Size:** L

### 12-S2: The loop driver, budgets and trace recorder

- **Delivers:** 12-C2a (full); 12-C2b (part: the 8.1 trace shape and its
  recorder, with no storage yet)
- **Needs (this spec):** 12-S1 (H)
- **Needs (other specs):**
  - 01g-C2a (H: `run_tools` with `execute=`, the caller's run id, and the
    terminal call returned);
  - 01g-C3 (H: per-turn ledger rows with `run_id` and `loop_turn`);
  - 01g-C4 (H: `RunBudget`, the reported stopping limit, and
    `RunRefused("unpriceable")`; S for `tool_calls.check_priceable` — until
    it lands, `investigation.priceable` applies the same rule);
  - 01i-C2 (H: `prompt_ceiling(resolved, max_tokens=…)` returning `None` when
    unknown).
- **Scope:** Adds `routes/investigation.investigate`, `Outcome`, the executor
  (threadpool), the per-entry-point `RunBudget`s in
  `store/investigation/budget.py` (including `E1_WALL_S`), the pre-reservation
  priceable check, the `context` stop with its fallback, and
  `store/investigation/trace.py`. It is exercised only by tests through a new
  scripted tool-call fake in `backend/tests/llm_fakes.py`. No route is added
  yet, because a route lands with its call site.
- **Acceptance:** section 14's "Loop and budgets" tests: each limit with
  01g's names, the context stop and fallback, the prose-only final turn,
  cancel, a failed call by kind, and unpriced-under-ceiling refusal including
  a rate removed after the check.
- **Size:** M

### 12-S3: E2, continuity investigation (the pilot)

- **Delivers:** 12-C3 (part: E2's proposal write); 12-C4 (part: E2's entry
  point and its `background` run); 12-C2b (part: the candidate proposal's
  `investigation` field); 12-C2c (part: the E2 corpus and offline graders)
- **Needs (this spec):** 12-S2 (H)
- **Needs (other specs):** 01g-C1 (H: the `tools` capability and the seam's
  `incapable` refusal for a route that `requires=("tools",)`)
- **Scope:**
  - Adds the `investigation` route with `legacy=routing.NO_LEGACY` and only
    its `investigation-continuity` task. A route lands with its call site,
    and each later task joins with its own.
  - Adds `reconcile.persist_investigation` and the public `reconcile.rebuild`,
    the `candidates.read` handling of `investigation`, and the
    `reserve_campaign_background` `match` predicate.
  - Adds `post_candidate_investigate` (202, `@computes_only`, refusals before
    reserving), the `investigation_continuity` switch (default `off`), and
    the review's Investigate action showing the trace.
  - Updates CLAUDE.md's Detached runs section (+1 handler) and the docs guard.
- **Acceptance:** section 14's E2 "Write posture" tests (the inspected-set
  floor, fencing, generation and basis untouched, no-change writes nothing,
  one revision bump, trace round-trip) and E2's "Runtime" tests (refusals
  before reserving, adopt or `busy`, polling, data-dir refusal, campaign
  delete). The offline `--investigate continuity` graders run in
  `pytest backend`.
- **Size:** L

### 12-S4: Decision as a tool for maintenance modes

- **Delivers:** 12-C1 (part: the `decide` tool, 4.4)
- **Needs (this spec):** 12-S3 (H)
- **Needs (other specs):** 01g-C5 (H: the decide tool with the task name
  supplied by the caller, a consumer's own decide route accepted, the
  per-run cap, and no recursion)
- **Scope:** Adds the `investigation_decide` route
  (`legacy=routing.NO_LEGACY`, task `investigation-decide`) with its call
  site, and offers the tool in the continuity toolset with
  `max_decisions = 2`. The question toolset gains it in 12-S5. The tool is
  not offered under a spend ceiling when its resolution would answer
  natively (01g section 3.9).
- **Acceptance:** E2 runs with and without the tool (a soft resolution
  failure means the tool is not offered). `max_decisions` is enforced. The
  routing and operation guards accept the route with its literal task.
- **Size:** S

### 12-S5: E3, history question

- **Delivers:** 12-C3 (part: E3 writes nothing); 12-C4 (part: E3's entry
  point and its `draft` run); 12-C2b (part: the trace on the run result)
- **Needs (this spec):** 12-S2 (H); 12-S4 (S: the `decide` tool, with
  `max_decisions = 1` — until it lands, E3 runs without it)
- **Needs (other specs):** 01g-C1 (H: the seam's `incapable` refusal)
- **Scope:** Adds the `investigation-question` task to the route,
  `post_history_question` through `runs.run_draft`, the `answer` validation
  against the inspected set, the `investigation_question` switch (default
  `off`), and the ledger's "Ask the history" action via `api.draftRun`.
  Updates CLAUDE.md's Detached runs section (+1 handler, +1 draft).
- **Acceptance:** section 14's E3 tests: uncited answers become
  `insufficient_evidence`, `run_draft` dedupes a repeated attempt, and the
  refusals are answered before reserving.
- **Size:** M

### 12-S6: E1, RP escalation

- **Delivers:** 12-C1 (full: rp_actor filtering); 12-C3 (full: E1's
  selection); 12-C4 (full: E1's trigger and its stage in the turn run);
  12-C2b (full: E1's prompt-log capture section)
- **Needs (this spec):** 12-S2 (H)
- **Needs (other specs):**
  - 11-C1 (H: `classify` and `EpistemicView.visible()` for an actor
    perspective);
  - 11-C2 (H: per-step `gather` with a perspective, and `history_view`
    rendering);
  - 10-C2 (H: `PlanTrace` exposing the terminal state, the trigger, the
    questions and terms each round tried, and the evidence ids of `E0` and
    of each round);
  - 10-C3 (H: the last sufficiency verdict, and whether it was an answer, an
    escalated answer or `unknown`, on the trace);
  - 08-C3b (S: an admission predicate on `expand` — until it lands,
    rp_actor does not offer `get_scene_excerpt`);
  - 02-C6 (H: the turn-path play-decision rules E1 keeps — soft resolution,
    `except Exception` recorded as `skipped`, and off byte-identical).
- **Scope:**
  - Adds the `investigation-turn` task, the trigger read in the step's
    `gather`, and `investigated` on the round record.
  - Adds the rp_actor toolset (filtering, omission, title rule, unknown
    entities `not_found`, no overrides, no ledger tools), `finish_evidence`
    validation and re-classification, and the merge ahead of 10's evidence.
  - Adds the deep-history send option, the `investigation_rp` switch
    (default `off`), the prompt-log `investigation` section, and the two
    status frames. The frames are added only after the turn stream reader
    handles them, in this slice.
- **Acceptance:**
  - section 14's rp_actor "Toolset" tests (a hidden-only search is
    indistinguishable from a nonexistent term);
  - E1's "Write posture" tests;
  - E1's "Runtime" tests: the trigger on each `PlanTrace` state, once per
    round across a resume, never on a reroll, replay or opener, and
    fail-soft;
  - 11-C4's offline graders pass with E1 on.
- **Size:** L

### 12-S7: The adoption gate

- **Delivers:** 12-C2c (full)
- **Needs (this spec):** 12-S3 (H); 12-S5 (S: the E3 arm — absent until
  then); 12-S6 (S: the E1 arm and selection leakage — absent until then)
- **Needs (other specs):** 01a-C1 (H: per-case wall time, tokens and the three
  money columns, aggregated per route and backend); 01a-C2 (H: live evals
  metered in a throwaway home under the eval scope); 11-C4 (S: the leakage
  graders and corpus — until they land, the E1 arm reports no leakage line
  and cannot pass)
- **Scope:** Adds `evals/run.py --investigate <mode>` with arms A, B and C, the
  10.2 corpus including the hidden-route case, the selection-leakage metric
  replayed from traces, and the `evals/README.md` recording format for 10.4's
  adoption rule. It changes no default. Turning a switch on is a later PR that
  attaches a passing table.
- **Acceptance:** the offline arms and the selection-leakage grader run in
  `pytest backend` on the synthetic corpus. The live arms run opt-in and are
  metered under 01a-C2's scope.
- **Size:** M

## 11. Contract

**12-C1: A read-only toolset, perspective-filtered in RP** (over 01g-C2a).

- *Inputs*: a mode, a campaign, and a perspective bound by the run.
- *Outputs*: the tools of 4.3 as 01g `Tool`s in per-mode `Toolset`s, each
  returning the 4.1 envelope in a `ToolOutput`.
- *Guarantees*:
  - no tool mutates anything, and none takes the full campaign lock;
  - transcript text is the regex prompt view computed over the whole
    transcript, with hidden posts and director notes removed;
  - `gm-only` bodies are never returned;
  - in rp_actor mode: every history item is filtered by 11-C1, items with no
    visible slice are omitted ref and all, text search anchors only on
    visible posts, an entity the actor does not know is `not_found` with its
    name, overrides are never returned, ledger tools are not offered, and
    "not visible" reads as `not_found`;
  - history retrieval is pinned to this campaign (`tier_limit=2`);
  - arguments are store refs, never paths.
- *Failure*: a reader failure is a `{"ok": false, "error": "unavailable"}`
  result; the run continues.

**12-C2a: Budgets.**

- The `RunBudget` of 7.1 per entry point, enforced by 01g-C4 before each send,
  with 01g's limit names, plus 12's `context` stop derived from 01i-C2's
  `prompt_ceiling` (7.3).
- Reaching a limit is `budget_exhausted` with `limit`, never an error.
- An unpriced model under a spend ceiling is refused before sending: E1 skips
  with the reason recorded; E2 and E3 answer 409 `unpriceable` before
  reserving.
- `investigation_spend_ceiling` is USD, unset by default; a campaign `over`
  its budget is not refused.

**12-C2b: Trace.**

- The 8.1 shape: tools, argument refs and clipped queries, result refs, sizes
  and truncation, the inspected set, the final validated payload, and usage
  in three money columns.
- No tool result text and no reasoning.
- Stored where 8.2 says, and nowhere else.

**12-C2c: An eval gate.** Arms A, B and C on the 10.2 corpus with the 10.3
metrics, including selection leakage. Each entry point stays default-off until
a recorded run meets 10.4.

**12-C3: Write posture: selection in RP, one proposal for continuity, nothing for questions.**

- E1 selects history evidence items that a tool returned, re-classified at
  finish, and rendered through 11-C2. No model prose reaches a turn.
- E2 writes one proposal on one live candidate through
  `reconcile.persist_investigation`: reconcile's validation, the
  positive-evidence floor taken from the scenes the run read, fenced on
  fingerprint and prior proposal, never touching the sweep's generation, one
  revision bump. No-change and insufficient-evidence write nothing.
- E3 writes nothing.
- No investigation code calls a ledger, alias, link, knowledge (11-C3),
  transcript or record mutator, and the guards of section 12 hold that.

**12-C4: Three entry points and their run classes.**

- E1 is triggered from 10's `PlanTrace` (`repaired`, or `planned` with an
  `insufficient` verdict that could not repair), or by the explicit
  deep-history option; once per round, recorded on the round record;
  fail-soft; inside the turn run.
- E2 is a `background` run on the campaign, one live per campaign, 202.
- E3 is a `draft` run, 202.
- Each has its own task under the `investigation` route
  (`requires=("tools",)`, `legacy=routing.NO_LEGACY`) and its own
  default-off switch; the decide tool uses `investigation_decide`.

## 12. Interaction with repo rules

- **Routing guard** (`test_routing_guard.py`): add
  `Route("investigation", "History investigation", …, ("investigation-turn",
  "investigation-continuity", "investigation-question"), True,
  default_role="primary", requires=("tools",), legacy=routing.NO_LEGACY)` and
  4.4's `investigation_decide`. `Route.legacy == ""` means "this route IS a
  legacy route" (`store/routing.py:55-58`), so both new routes carry the
  shared `NO_LEGACY` sentinel (checklist, "Shared structures"); whichever of
  01g, 02, 09, 10 or 12 lands first adds it. Each task joins the route in
  the slice of its own call site (12-S3, S5, S6), because the guard fails a
  route task nothing uses. Every task is resolved through
  `require_inference`, and no `inference.resolve` call appears in `routes/`.
- **Operation guard** (`test_operation_guard.py`): the loop goes through
  01g-C2a's `run_tools`, which 01g teaches the guard to recognise. The decide
  tool goes through `operations.decide` with a literal task and `resolved=`
  (01g section 3.12).
- **Usage guard**: every model turn and every embed under `search_history`
  passes a meter's holder (01g-C3; 09's `history-recall` task).
- **Regex prompt guard**: its scan is extended to `store/investigation/`, and
  the excerpt, window and summary readers are pinned by name (4.2 rule 2).
- **Writer guards**:
  - `test_continuity_writer_guard.py` resolves relative imports against one
    package (`grimoire.store.continuity`, its `PACKAGE`, `:45-50`). It is
    generalised to a list of `(package, modules)` pairs, each resolving
    against its own package, and gains `grimoire.store.investigation` with
    all its modules;
  - a new `test_investigation_writer_guard.py` fails if anything under
    `store/investigation/` or `routes/investigation.py` calls a mutator of
    `plot`, `commitments`, `facts`, `events`, `relationships`,
    `continuity.doc`, `continuity.review`, `continuity.candidates`,
    `knowledge` (11-C3), `scenes.write`, `entities`, `playstate`, `dossiers` or
    `chronicle`, resolving import bindings as `test_absorb_writer_guard.py`
    does. The one allowed write, `reconcile.persist_investigation`, is called
    from `routes/investigation.py` and is named as its only allowed mutator.
- **Lock domain** (`test_lock_domain_guard.py`): the investigation package is
  read-only and is declared in **no** list; a read-only module in
  `OUTSIDE_DOMAIN` (or a non-writing one in `DOMAIN_MODULES`) fails
  `test_the_declaration_has_no_phantom_modules` and
  `test_modules_declared_outside_are_really_outside`
  (`backend/tests/test_lock_domain_guard.py:2332-2372`). The persist lives in
  `reconcile.py`, whose existing classification covers it, and it takes
  `campaign_lock`.
- **Lock order**: one campaign per run, so no `hold_all`.
- **Atomic writes**: the only write goes through `candidates`, already atomic.
- **Import guard**: module-scope imports and submodule bindings
  (`from ..continuity import reconcile`, then `reconcile.rebuild(...)`).
- **Revision token**: E2's persist stamps for itself, once. E1 and E3 write
  nothing.
- **Pydantic v1 / Android**: tool schemas are plain dicts inside 01f-C3's
  portable subset; route bodies are plain `BaseModel` fields via `_dump`. Tool
  calling on Android depends on 01g-C2a's adapters (OpenRouter and
  OpenAI-compatible are pure Python). The Claude Agent adapter is a desktop
  extra and reads as unable to call tools everywhere until 01g-C2b.
- **Privacy**: eval fixtures and the corpus use placeholder names only, and
  the gate's recorded table reports metrics over the synthetic corpus only.
- **Settings never spend unasked**: E2 and E3 are explicit reader actions on
  play surfaces, not settings. Their switches live on Settings and spend
  nothing when flipped.
- **Docs guard**: CONTRIBUTING.md's guard table names
  `test_investigation_writer_guard.py`; CLAUDE.md's Detached runs section
  changes as in 9.4.

## 13. Non-goals

- Tools that create records (`propose_new_thread`, `propose_new_commitment`,
  a free-standing `propose_alias`). The candidate cache has no kind for a new
  record; aliases arrive through a `possible_duplicate` merge, as today.
- Investigation on a turn whose 10 trace did not fail, except by the explicit
  deep-history option.
- Decision-as-tool in RP mode (02-C4's), and any recursion.
- Another campaign's history (09's tier 3) in any mode.
- Persisting E3 answers, or a campaign "investigation history" page.
- The draft's "optional next-scene exploration in mature campaigns": scene
  suggestions have their own call and their own review, and an agent there
  would be a second suggestion engine. A later spec may revisit it with this
  gate's evidence.
- Raw `search.py`/`semsearch.py`, filesystem, SQL, shell or vector tools.
- Provider-specific agent SDK features: Claude Agent's own tools stay off.
- Showing chain-of-thought, in the trace or anywhere else.

## 14. Tests and acceptance

**Toolset** (`backend/tests/test_investigation_tools.py`):

- every tool, every mode: offered only in its modes; unknown and path-shaped
  refs return `not_found`;
- a hidden post never appears in returned text; a depth-ranged regex rule
  applies at the transcript's depth in a window;
- `gm-only`: name only in N/C/Q, `not_found` in R;
- rp_actor: a scene with no visible slice returns `not_found`, identical to a
  missing one; a search for a hidden-only word returns exactly what a search
  for a nonexistent word returns; an entity the actor does not know is
  `not_found`; `get_actor` on another actor is `not_found`; no override
  appears; ledger tools are absent; `get_scene_excerpt` is absent until 08's
  admission predicate exists;
- `search_history` passes `tier_limit=2` in every mode;
- `get_actor` and `get_timeline` work with no scene and never take
  `campaign_lock` (a patched lock asserts it);
- truncation at a post boundary sets `truncated`.

**Loop and budgets** (`test_investigation_loop.py`, with a scripted tool-call
fake class added to `backend/tests/llm_fakes.py`, never an inline fake):

- each `RunBudget` limit ends the run `budget_exhausted` with 01g's limit name,
  and sends nothing further;
- the context stop uses `prompt_ceiling` and falls back when it is `None`;
- a prose-only final turn is `insufficient_evidence`;
- cancel stops the run and abandons the in-flight call;
- a failed call is `failed` with the kind only;
- an unpriced model under a ceiling: E1 skips with `unpriceable` in the
  capture; E2 and E3 answer 409 before reserving; a rate removed after the
  check ends the run `refused` having sent nothing.

**Write posture** (`test_investigation_persist.py`):

- E2: a proposal citing a scene only seen in a search list is downgraded to
  `uncertain`; one citing a scene read through `get_scene_excerpt` stands;
- a fingerprint change, a changed prior proposal and a dismissal each drop
  the write (`persisted: false`);
- `persist_investigation` leaves `generation` and `basis` untouched, and a
  sweep that started before the investigation and commits after it is not
  superseded by it;
- `finish_no_change` writes nothing, and the next sweep still selects the
  candidate;
- the revision is bumped exactly once on a write;
- the stored `investigation` trace round-trips through `candidates.read`;
- E1: non-history ids are dropped; an id re-classified as not visible is
  dropped; the rendered prompt contains only 11-C2 sections and none of the
  model's note;
- E3: citations not read in the run are dropped, and none left is
  `insufficient_evidence`.

**Runtime** (`test_investigation_routes.py`):

- E1 runs on a `repaired` trace and on `planned` with `insufficient`, not on
  `not_run`, `declined`, `unusable`, `planned` with `sufficient`/`unknown`,
  or `repair_redundant`; at most once per round, also across a resumed round;
  never on a reroll, a replay or an opener; a failed E1 leaves the step
  completing on 09/10's evidence;
- E2's refusals are answered before reserving; a same-candidate restart
  adopts; a different candidate gets 409 `busy`; the campaign run routes poll
  it; `PUT /config/data-dir` is refused while it is live; a campaign delete
  forgets it;
- E3 goes through `run_draft`, and a duplicate attempt is not re-run.

**Guards**: `test_investigation_writer_guard.py`, the generalised continuity
writer guard, and the regex guard's new scan path and pins, each proven by a
planted violation; the routing guard accepts both new routes with
`NO_LEGACY`.

**Eval**: the `--investigate` offline graders, including selection leakage
on replayed traces, run in `pytest backend`. The live arms are opt-in.

**Acceptance**:

1. Every entry point is off by default and reachable only as section 3 says.
2. No investigation can mutate anything but one candidate's proposal, and
   that write never touches the sweep's generation.
3. Every run ends in one of the seven terminal states within its budget.
4. Every E2 proposal's evidence scenes are a subset of the scenes its run read
   transcript text from.
5. An actor-perspective investigation passes 11-C4's text graders and the
   selection-leakage line.
6. The gate's table exists for any entry point whose default is turned on.

## 15. What to re-check against the parallel specs

The checklist contracts cited above cover 01g-C2a (caller-executed tools,
caller run id, final call returned), 01g-C4 (limits, which limit stopped,
unpriced refusal), 01g-C5 (caller task, own decide route accepted), 01i-C2,
10-C3, 09-C1 and 11-C1/C2. These points remain, each an interface requirement
on another spec:

1. **10-C2's `PlanTrace`** exposes the terminal state, the last 10-C3 verdict
   (answer, escalated answer or `unknown`), the trigger, the questions and
   terms each round tried, and the scene keys and evidence ids of `E0` and of
   each round (3.2).
2. **08-C3b's `expand`** accepts an admission predicate applied before term
   matching and window placement (4.3). Until it does, R mode does not offer
   `get_scene_excerpt`.
3. **01g** exposes `tool_calls.check_priceable(resolved, budget)`, the 3.9
   rule as a function a start route can call before reserving (7.2). Until it
   does, 12 applies the rule itself.
4. **09** records that its tier 3 has no caller once 12 pins `tier_limit=2`
   (4.2 rule 8).
5. **The checklist edge** for 12 drops `01d-C2b` and gains `01i-C2` (Depends
   on).

## 16. Open questions

1. **Ship E3 at all?** It is the most open-ended entry point and has no review
   path. *Recommendation:* specify it now, build it after E2's gate passes,
   and drop it if adaptive search rarely beats the sweep.
2. **E3 answer retention.** Hold on the run only (as specified), or offer
   "save as note"? *Recommendation:* run only for v1. A saved answer is a
   model claim, and if it is ever stored it should go through a review path.
3. **Once per round for E1.** Is the *first* triggering actor step the right
   one? *Recommendation:* yes for v1, since it is the one the round is waiting
   on first. Revisit with eval data.
4. **Investigate from a `live` candidate with a non-`uncertain` proposal?**
   *Recommendation:* allow it, labelled "second opinion". 6.2's fencing keeps
   a newer sweep's answer from being clobbered.
5. **Should 10 add a sufficiency check after its repair hop?** Today a
   `repaired` trace triggers E1 even if the repair fixed the gap.
   *Recommendation:* ask 10 to add it behind its own call cap only if the eval
   gate shows E1 firing on repaired-and-sufficient turns often enough to cost
   more than the check. 12 would then read that verdict instead.

## 17. Review record

The substitute spec-gate review (`reviews/12.md`: 1 blocking, 11 should-fix,
6 minor) was checked against the code and the parallel specs, and folded in:

| Finding | Disposition |
|---|---|
| B1 E1 as 01d-C2b's resolver cannot be built | Fixed per the coordinator's decision: E1 is triggered from 10's `PlanTrace` (3.2); `01d-C2b` dropped from Depends on. Verified against 01d section 5.2 (`Resolver` returns `ItemResult`s) and 10 section 8 (no check after the hop) |
| S1 rp_actor side channels | Fixed: anchor-before-filter requirement on 08 (tool withheld until then), omit items with no visible slice, title per 11's summary rule, seed filtered through `visible()`, unknown entities `not_found` with names, no overrides in R (4.2, 4.3, 5) |
| S2 `_commit` generation fence, double bump, no-change suppression | Fixed: `reconcile.persist_investigation` outside the generation fence, one bump, no-change writes nothing (6.2). Verified `_commit` (`reconcile.py:761-790`) and `select` (`:1054-1083`) |
| S3 tier 3 | Fixed: `tier_limit=2` pinned in every mode (4.2 rule 8) |
| S4 locked or scene-bound backings | Fixed: scene-free best-effort readers for `get_actor` and `get_timeline`. Verified `casefile.build` and `timeline.build` take `campaign_lock` |
| S5 01g API alignment | Fixed: 01g limit names, `RunBudget` fields, `execute=`, the wall rule, a pre-reservation priceable check, the ceiling's unit and default, the campaign-`over` answer, 01i-C2 (5, 7) |
| S6 `NO_LEGACY` | Fixed (12) |
| S7 lock lists and writer guard | Fixed: no lock-list entries, persist in `reconcile.py`, guard generalised per package (12). Verified the phantom tests |
| S8 regex depth over a window | Fixed: whole-transcript depth (4.2 rule 2). Verified `view(offset, total)` |
| S9 non-history ids in `finish_evidence` | Fixed (6.1) |
| S10 "inspected" | Fixed: only transcript-reading tools count (6.2) |
| S11 selection leakage | Fixed: selection-level leakage metric and the hidden-route case (10.2, 10.3) |
| M1 dropped draft tools | Fixed: reasons in 4.3 and 13 |
| M2 hard-coded handler counts | Fixed: "+2 handlers, +1 draft", totals by the last PR (9.4) |
| M3 once per round in memory | Fixed: `investigated` on the round record (3.2) |
| M4 query mapping onto `expand` | Fixed (4.3) |
| M5 letters, not refs | Fixed (4.3, 6.2) |
| M6 `api.streamDraft` | Fixed (9.1) |
| 02 addendum (02-C6) | E1 takes a named total deadline (`E1_WALL_S`), soft resolution, `except Exception` and off-is-identical from 02-C6 (7.1) |
| Slicing | Slices added (7 slices). Section 12 now says each route task joins in the slice of its call site; no contract item re-worded |
| Coordinator and cross-spec inputs | 01i: `prompt_ceiling`, never `max_output` as the reserve (7.3); unpriced under a ceiling refused (7.2); 08's revision: `search_history` embeds under 09's `history-recall`, posts carry `r-`/`p-` keys and `part` (Depends on, 4.3, 6.1) |
