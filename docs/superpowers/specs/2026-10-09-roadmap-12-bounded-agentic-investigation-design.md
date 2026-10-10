# 12. Bounded agentic investigation

**Status:** Draft — cross-linked; spec gate pending.
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

Matches the checklist edge `12 ← 01g-C1..C5, 01i-C1, 08-C3, 09-C1,
01a-C1/C2 (H); 01d-C2b, 10-C2/C3, 11-C1/C2 (H for RP mode); 11-C4 (S)`.

| Contract | Provided by | What this spec uses it for | Hard or soft |
|---|---|---|---|
| 01g-C1 `tools` capability, probe, seam `incapable` refusal | 01g | The `investigation` route `requires=("tools",)`. A primary known not to call tools is refused at the seam, and the entry point is skipped | Hard |
| 01g-C2a tool calling over OpenRouter, OpenAI-compatible and Anthropic; the loop primitive (`inference.run_tools`): the caller executes the tools, supplies the run id, and gets the final call back | 01g | The loop itself (section 5) | Hard |
| 01g-C2b Claude Agent SDK tools (until it lands, `claude` reads as unable to call tools) | 01g | Nothing beyond C1's refusal: a `claude` primary is skipped until C2b | Part of the `01g-C1..C5` hard edge; only bounds which adapters can serve |
| 01g-C3 a ledger row per loop turn with `run_id` and `loop_turn`, plus capture | 01g | Every model turn of an investigation is attributed to the run (sections 8, 9) | Hard |
| 01g-C4 run budget (turns, tool calls, decisions, wall clock, spend ceiling, per-turn output cap); reports which limit stopped the run; refuses an unpriced model under a ceiling before sending | 01g | The budgets of section 7 | Hard |
| 01g-C5 decide tool on the `tool_decision` route, capped per run, no recursion, task name supplied by the caller | 01g | The `decide` tool in maintenance modes (section 4.4) | Hard for that tool only |
| 01i-C1 `wire.Limits(window, max_output)` with a source, on every target | 01i | The loop's context ceiling (section 7.3) | Hard (with a structural fallback when unknown) |
| 08-C3 (`08-C3a` `history-index` embed task; `08-C3b` `expand(...)`, the caller naming the phase) | 08 | `get_scene_excerpt` in the prompt phase; `search_history`'s embedding, through 09 | Hard |
| 09-C1 `history.retrieve(Query) -> Evidence` with a `perspective` seam | 09 | `search_history` is 09's retrieval, not a second one | Hard |
| 01a-C1 / 01a-C2 eval cost, latency and token reporting; metered live evals | 01a | The eval gate compares cost and latency, not only correctness (section 10) | Hard |
| 01d-C2b one escalation hop whose next resolver may be caller-supplied | 01d | E1 is the caller-supplied resolver after 10's repair hop (section 3.2) | Hard for RP mode |
| 10-C2 one repair hop, at most three calls per turn, a phase deadline | 10 | E1 runs only after 10's hop; its seed names what the hop tried | Hard for RP mode |
| 10-C3 the evidence-sufficiency predicate on `history_check` | 10 | The verdict that escalates to E1 (section 3.2) | Hard for RP mode |
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
  -> 10 planner + one repair hop (10-C1, 10-C2)
  -> 12 bounded investigation, only when 10 reports insufficient evidence
     (RP), or when a person asks (maintenance)
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
| Who starts it | The turn path, after 10 reports insufficient evidence, or the player's explicit "deep history" option on a send | A reader, from a continuity candidate's detail ("Investigate") | A reader, from the campaign ledger ("Ask the history") |
| Perspective | The turn's actor (11-C1), or the narrator | Narrator (author-facing) | Narrator (author-facing) |
| Output | A **selection** of evidence refs, rendered through 11-C2 | One **proposal** for that candidate, or `no_change` / `insufficient_evidence` | An **answer** with citations, never stored |
| Write posture | None | Proposal on the candidate cache only | None |
| Runtime | A stage inside the turn run (no new run) | `background` run on `("campaign", cid)` | `draft` run on `("campaign", cid)` |
| Task | `investigation-turn` | `investigation-continuity` | `investigation-question` |
| Switch (config key) | `investigation_rp` | `investigation_continuity` | `investigation_question` |

### 3.1 When it never runs

- When the switch is off, which is the default for all three.
- When the `investigation` route resolves to a primary that is *known* not to
  call tools: 01g-C1's seam refuses with 409 `incapable`. E1 then skips
  silently. E2 and E3 surface the 409 at the start route, before anything is
  reserved, as `require_inference` refusals already do. `unknown` is allowed,
  per the seam rule (a `no` refuses, never an `unknown`).
- On a reroll, Retry, Keep writing or roll continuation. These replay the
  frozen prompt snapshot (`responses.prepare`), which already holds what the
  original turn selected, so the reroll costs no investigation.
- On an opener, and on a director turn that carries no note. Neither has a
  question to investigate.
- For calendar arithmetic, check or effect resolution, graph construction,
  absorb extraction, or any context deterministic code can assemble. These
  are the draft's "low-value or negative" uses, and no entry point reaches
  them.

### 3.2 E1's trigger, precisely

E1 is a **caller-supplied next resolver** (01d-C2b) for 10's sufficiency
question:

1. 09 retrieves (09-C1). If 09-C2's coverage verdict is sufficient, stop.
2. 10 plans and runs its one repair hop (10-C2). Its sufficiency judgement is
   10-C3's predicate on `history_check` (task `history-sufficiency`). An
   `insufficient` verdict, or a low-margin, abstained or `refused` one under
   01d-C2a's trigger, is what escalates.
3. 01d-C2b runs one hop. For the `history-sufficiency` task's policy
   (01d-C1), the declared next resolver is `investigation-turn`, supplied by
   the turn path when `investigation_rp` is on, and nothing otherwise. Its
   ledger rows carry `hop: escalation`.
4. E1 runs at most **once per round** (once per player post), for the first
   actor whose retrieval escalates. A round is what the player waits on, and
   a round with three NPCs must not run three investigations. Later actors in
   the round proceed on 09/10's evidence.

The **"deep history"** option on a send (a one-shot turn field, carried like
the response-length chip in `_turn_override`) skips step 2's verdict and
starts E1 for the round's first actor. It is still subject to every budget,
and it is still once per round.

E1 is **fail-soft**: every terminal state other than `completed`, including
`failed` and `budget_exhausted`, leaves the turn on 09/10's evidence. An
investigation never fails a turn.

## 4. The toolset (12-C1)

### 4.1 Shape

`store/investigation/tools.py` declares a registry of `Tool` records. The
model sees only names, descriptions and JSON-schema parameters in 01g-C2a's
provider-neutral form:

```python
@dataclass(frozen=True)
class Tool:
    name: str
    description: str                      # rendered from templates/investigation/tools/<name>.j2
    parameters: dict                      # JSON schema: plain dict, no pydantic
    modes: frozenset[str]                 # subset of {"rp_actor", "rp_narrator", "continuity", "question"}
    run: Callable[[Ctx, dict], dict]      # sync; executed in the threadpool
    terminal: bool = False                # a finish/propose/answer tool

@dataclass
class Ctx:
    cid: str
    sid: str                              # the scene the run serves ("" for E2/E3)
    mode: str
    perspective: epistemic.Perspective    # 11-C1; bound by the run, never by the model
    inspected: set[str]                   # scene refs whose text a tool returned this run
    returned: dict[str, dict]             # evidence id -> unit, for E1's selection
    bytes_left: int                       # the run's remaining returned-text allowance
```

**The perspective is bound by the run, never passed by the model.** No tool
takes a `perspective`, `as`, `viewer` or `actor` argument that changes what it
may return. A model that wants to see narrator-only material in an actor run
has no parameter to ask with.

Every tool returns one envelope:

```json
{"ok": true,
 "items": [{"id": "e7", "ref": "scene:0007--the-pier", "title": "The pier at dusk",
            "text": "...", "truncated": false}],
 "bytes": 1840}
```

or `{"ok": false, "error": "<code>"}` with `error` one of `not_found`,
`not_in_mode`, `bad_args`, `budget` (the run's byte allowance is spent) or
`unavailable` (a reader failed). Errors are tool results, not exceptions: the
loop continues, and the step is traced.

**`not_found` is also the answer for "exists but not visible to this
perspective."** An actor run asking for a scene its actor has no access to
gets the same envelope as a scene that does not exist. A distinct `withheld`
would let the model probe for the existence of what it may not read.

### 4.2 Rules every tool obeys

1. **Read-only.** A tool calls no mutator. `test_investigation_writer_guard.py`
   (section 11) holds this by import binding.
2. **The prompt view for transcript text.** Any text drawn from a
   transcript goes through `regex.view.view(messages, cid=…,
   phase="prompt")`, then `scenes_serialize.in_context` (no hidden posts, no
   director notes), then the transcript renderer. The package is added to
   `test_regex_prompt_guard.py`'s scan, which today covers `routes/` and
   `store/context/` only.
3. **`gm-only` bodies never.** An entity whose secrecy is `gm-only` is
   returned by name and kind only, in every mode, including the author-facing
   ones. Its body "never reaches a prompt" (`store/entities.py:26-37`), and a
   tool result is a prompt.
4. **Perspective filtering (RP actor mode).** Every history item passes
   through 11-C1 for the run's perspective, and only `VISIBLE` slices are
   returned. Ledger tools (threads, commitments, facts, timeline, pressure,
   other actors' state) are **not offered** in actor mode, because the actor
   contract blanks those sections (`store/context/assemble.py:521-538`), and
   a tool is not a way around a contract.
5. **Bounded per call.** Each result is clipped to `TOOL_RESULT_BYTES` at a
   post or line boundary, and `truncated` says so. The run's cumulative
   returned text is capped by `Ctx.bytes_left` (section 7.1). A call that
   would exceed it returns `budget`.
6. **Refs, never paths.** Arguments name store refs (`scene:<sid>`,
   `thread:<id>`, `commitment:<id>`, `event:<id>`, `fact:<id>`,
   `characters:<id>`, `pcs:<id>`, `groups:<id>`, `locations:<id>`,
   `lore:<id>`, `items:<id>`). A ref is parsed by the same rule as
   `continuity.canon` and checked against the campaign's live records. A
   path-shaped or unknown ref is `not_found`.
7. **No lock held across a model call.** A tool takes
   `locks.best_effort_campaign_lock(cid)` for its own reads, as
   `continuity.graph` does, and releases it before returning.

### 4.3 The tools

`R` is rp_actor, `N` rp_narrator, `C` continuity and `Q` question.

| Tool | Args | Backed by | Modes | Notes |
|---|---|---|---|---|
| `search_history` | `query: str (<=200 chars)`, `max: int (<=6)` | 09-C1 retrieval for this campaign, with the run's perspective; units filtered through 11-C1 in R | R N C Q | Returns unit ids, scene refs, titles and short texts. Embeds through 08-C3a's `history-index` task under 09's rules. With no embeddings it runs structural plus lexical, as 09-C2 does |
| `get_scene_summary` | `scene` | `chronicle.get_record` + `scenes.read.read_scene_meta` (date, location, cast names) | R N C Q | In R only when 11-C1 classes the scene unit `witnessed` (whole-scene attendance, 11 rule R6) or `known` |
| `get_scene_excerpt` | `scene`, `query: str`, `max_posts: int (<=8)` | 08-C3b `expand(...)` with the phase named `prompt` | R N C Q | In R, only the actor's visible slices of the window are returned |
| `read_scene_window` | `scene`, `before: int or null`, `limit: int (<=12)` | `scenes.read.read_scene_window` (`store/scenes/read.py:169`), then rule 2 | C Q | The draft's "expensive, explicit" transcript read, paged and capped. Never in RP |
| `get_record` | `ref` | `effective.records`, `facts.get`, `events.get`, `overlay.read_entity` | N C Q (R: `lore`, `locations`, `items` only, through `actor.knows`) | Thread and commitment titles, status and latest beat. Lore bodies honour secrecy and `known_by` in R |
| `get_record_history` | `ref` | beats; facts supersession; `relationship_history.for_pair` for `pair:<a>+<b>` | N C Q | Bounded to the latest `HISTORY_ROWS` rows |
| `get_related` | `ref` | `effective.links`, `involvement.of` | N C Q | Reviewed links and touched scenes/actors; candidates are not included |
| `get_timeline` | `from`, `to` (str or null) | `timeline.build`, `events.list_events` | N C Q | Dates through the calendar provider's own rendering, never model arithmetic (capstone §3.8) |
| `get_pressure` | (none) | `pressure.build` | C | Deadlines and staleness, as the sweep sees them |
| `get_actor` | `ref` | `casefile.build`, plus 11-C3 overrides naming the actor | N C Q (R: own ref only, returning own `state.md` and `actor.own_relationships`) | Another actor's interiority is never visible in R |
| `get_group` | `ref` | entity + 07-C1 effective members + `groupstate` | N C Q | Secret groups' state only in C and Q, as an author's view; gm-only bodies never (rule 3) |
| `decide` | per 01g-C5 | 01g-C5 shim | C Q | Section 4.4 |
| `finish_evidence` | `ids: [str] (<=6)`, `note: str (<=200)` | (terminal) | R N | Section 6.1 |
| `propose` | `decision`, `from`, `to`, `relation`, `status`, `evidence_scenes: [ref] (<=3)`, `reason (<=280)` | (terminal) | C | Section 6.2 |
| `answer` | `text (<=1200)`, `citations: [ref] (<=6)` | (terminal) | Q | Section 6.3 |
| `finish_no_change` | `reason` | (terminal) | C | |
| `finish_insufficient_evidence` | `reason` | (terminal) | R N C Q | A valid, successful outcome (the draft says so, and it is kept) |

Constants: `TOOL_RESULT_BYTES = 6144`, `HISTORY_ROWS = 12`. Both are argued
from the per-run allowances in section 7 (a run of a few calls has to fit in
a fraction of a small context window) and are to be tuned against real
prompts later.

Not offered, in any mode: arbitrary file reads, `store/search.py` and
`store/semsearch.py` (1.2), raw vectors, `continuity.graph.build`, shell or
SQL, and any tool that names a path.

### 4.4 Decision as a tool (01g-C5)

In C and Q only, the model may call `decide` for a closed sub-question it did
not know to ask in advance, for example "does this excerpt show the debt was
paid?". The rules:

- **The shim's per-run cap is set by the entry point**: 2 for C, 1 for Q, 0
  for R and N. RP mode's Decision-as-tool is 02-C4's business, behind its own
  gate, and 12 does not open a second path to it.
- The tool is 01g-C5's, offered on the `tool_decision` route under the task
  name 12 supplies, `investigation-decide`. Its shape is 01g-C5's: one
  `Choice` over the options the model names, `allow_none` as it asks, and a
  context capped by 01g-C5 (the caller's bound part first, then the model's).
  12 binds the run's mode and candidate (E2) or question (E3) as the bound
  context. If 01g-C5's soft resolution refuses, the tool is simply not
  offered and the run proceeds without it.
- A `decide` call **cannot start an investigation**. It is a Decision, and
  01g-C5 has no recursion: the decide backend is offered no tools, and
  `run_tools` refuses to run inside itself. The draft's rule
  that "a Decision call should not recursively create another free-running
  agent" holds by construction.
- The answer comes back as a tool result: the answer, any probability the
  backend reported (never a fabricated one), and `abstained` when it is
  `None`. A native answer carries no rationale, and none is invented.

## 5. The loop

`routes/investigation.py` owns the three entry points and the driver. The
store package owns tools, prompts, validation and the trace. The split is
the house one: LLM calls in the route layer, prompt text and parsing in the
store.

```python
async def investigate(app, *, cid: str, sid: str, mode: str, task: str,
                      perspective: epistemic.Perspective, seed: Seed,
                      client: LLMClient, resolved: ResolvedInference,
                      budget: RunBudget, run_id: str,
                      cancelled: Callable[[], bool]) -> Outcome
```

1. **Seed.** Build the opening messages from `templates/investigation/<mode>/`
   (system and user):
   - for E1: the actor-visible recent exchange (`observed_history`, prompt
     view), the 09/10 evidence already tried (ids and titles), and 10's
     planned questions;
   - for E2: the candidate's `vocabulary` and records exactly as the sweep
     shows them (`reconcile.build_payload`, `reconcile.py:1309`);
   - for E3: the reader's question and the campaign date.

   The seed contains no tool results and no hidden posts.
2. **Run** 01g-C2a's loop primitive, `inference.run_tools(task, messages,
   toolset=…, client=…, resolved=…, budget=…, campaign=…, scene=…, post=…,
   run_id=…)`, with the mode's tools, the mode's `RunBudget` (01g-C4) and the
   run id (01g-C3). The caller executes each tool call and gets the final
   call back for validation. Tools execute through
   `anyio.to_thread.run_sync` (they read files), never on the event loop.
3. **Stop** at the first terminal tool call (validated, section 6), at a
   budget limit (01g-C4 refuses the next call before sending it), on cancel
   (`cancelled()` is checked between steps, and an in-flight call is abandoned
   the way `common._bounded_call` abandons one, `routes/common.py:584-600`), or
   on a failed call.
4. **Return** an `Outcome`:

```python
@dataclass(frozen=True)
class Outcome:
    state: str            # completed | no_change | insufficient_evidence | budget_exhausted | failed | cancelled
    limit: str = ""       # for budget_exhausted: turns | tool_calls | wall_clock | spend | context | bytes
    error: str = ""       # for failed: the LLMError kind, never provider text
    result: dict | None = None   # the validated terminal payload
    trace: dict = field(default_factory=dict)   # section 8
```

A model turn that ends with prose and **no tool call** is treated as an
implicit `finish_insufficient_evidence` with no reason. The prose is dropped
and never shown: it is not a validated result.

## 6. Write posture (12-C3)

### 6.1 E1: a selection, never prose

`finish_evidence(ids)` names evidence ids that **a tool returned in this run**
(`Ctx.returned`). Validation:

- unknown ids are dropped;
- ids are re-classified through 11-C1 for the run's perspective at finish
  time (the store may have moved during the run) and dropped unless
  `VISIBLE`;
- at most `RP_SELECT_MAX = 6` are kept.

The kept units join 09/10's evidence set **ahead of** 10's units and render
through 11-C2 inside 09-C3's token budget. Nothing the investigating model
*wrote* reaches the turn's prompt. The note is trace-only. This is what makes
RP mode safe to escalate into: the worst an investigation can do to a turn is
choose evidence the actor was already entitled to.

### 6.2 E2: one proposal for one candidate

An E2 run is about exactly one candidate, named at start. `propose` takes the
candidate vocabulary's fields and is validated by reconcile's own rebuild:

- the `decision` must be a word of `reconcile.DECISIONS[vocabulary]`, read
  through the same `_lifecycle_word` / `_temporal_word` / `_pair` path
  (`reconcile.py:1409-1424`, extracted as a public `reconcile.rebuild(item,
  cand, known)` so both callers share it);
- `known` (the positive-evidence floor of capstone §11.4) is **the scenes
  this run actually inspected**, `Ctx.inspected`, intersected with the
  campaign's live scenes. A proposal citing a scene whose text the run never
  saw is downgraded to `uncertain`, mechanically. The trace thereby enforces
  the draft's question "what evidence did the agent actually inspect?";
- `reason` is clipped to `RECONCILE_REASON_CHARS` (`reconcile.py:217`).

The proposal is persisted by `investigation.persist_proposal(cid, key,
proposal, trace, started_fingerprint, started_proposal)`, a sibling of
`reconcile.persist_proposals` that goes through the same `_commit`. It writes
only if, under `campaign_lock`:

- the candidate record is still cached and its verdict is still `live`
  (`pending.verdict`);
- its fingerprint equals the one at start;
- its stored `proposal` equals the one at start. If a sweep replaced it
  meanwhile, the newer sweep wins and this run's proposal is dropped, with
  outcome `completed` and `persisted: false` in the run result.

It then bumps the campaign revision itself (as `reconcile.persist_found` and
`persist_proposals` do; CLAUDE.md, "A campaign carries a write token"),
because the 202 that started it never waits for it.

The stored proposal gains one field, `investigation`: the trace summary of
section 8, so the review's candidate detail can show what was inspected.
`candidates.read`'s normalisation keeps the field when it is a dict of the
trace shape, and drops it otherwise (`store/continuity/candidates.py`'s
tolerant reader, whose `_PROPOSAL_TEXT` list it does not join because it is
not text).

Application is unchanged. The reader applies or dismisses through
`POST …/candidates/{id}/apply` and `…/dismiss` (`routes/continuity.py:525`,
`:549`), which `continuity.review` validates and journals. **No investigation
code calls a ledger, alias or link mutator.** The continuity writer guard's
module list gains the investigation package.

`finish_no_change` stores a `proposal` of `{"decision": "uncertain", "reason":
…, "investigation": …}` only when the candidate had no proposal. Otherwise it
writes nothing, so a no-change investigation never overwrites the sweep's
answer. `finish_insufficient_evidence` writes nothing.

### 6.3 E3: an answer held on the run

`answer(text, citations)` is validated: citations must be refs whose text a
tool returned in this run, and unknown ones are dropped. If every citation is
dropped, the outcome is `insufficient_evidence`. The result is held on the
draft run and reaped (`REAP_SECONDS`), never written to the store. A reader
who wants to keep it copies it. A stored Q&A history would be a second store
of model claims with no review path (section 16, question 2).

## 7. Budgets (12-C2a)

### 7.1 Per entry point

| Limit | E1 RP | E2 Continuity | E3 Question |
|---|---|---|---|
| Model turns | 3 | 6 | 5 |
| Tool calls (all) | 4 | 12 | 10 |
| `decide` tool calls (01g-C5 cap) | 0 | 2 | 1 |
| Wall clock | `config.llm_call_budget()` for the whole loop | 3 × `llm_call_budget()` | 2 × `llm_call_budget()` |
| Returned text (`bytes_left`) | 12 KiB | 64 KiB | 48 KiB |
| Spend ceiling (01g-C4) | `investigation_spend_ceiling` per run, the same key for all three | same | same |

Why these shapes:

- **E1's three turns** are search, read, then select. Anything longer is no
  longer a turn the player is waiting on.
- **E1's wall clock is one turn-path ceiling** for the whole loop, not per
  call. The turn already spends one ceiling on its own generation, and an
  investigation that could double it twice over has replaced the turn rather
  than served it. `llm_call_budget <= 0` (no ceiling) is honoured as no
  ceiling, as everywhere else, and the turn count still bounds the run.
- **E1's returned text** is a small multiple of what it may *select*. It has
  to read more than it shows, and 09-C3's section budget bounds what it
  shows.
- **E2 and E3** are runs a reader started and waits on behind a 202, so they
  get more turns. Their caps keep the worst case within one reconcile sweep's
  order of magnitude (CLAUDE.md works the sweep's own worst case out at
  several `llm_call_budget` ceilings).

Every number is structural and is to be tuned against real prompts through
the eval gate (section 10). The plan must not hard-code them anywhere but
`store/investigation/budget.py`.

### 7.2 Enforcement

01g-C4 enforces every limit **before a call is sent**: the turn, tool-call
and decision counters, the wall clock (time left must cover the next call's
ceiling), the per-turn output cap, and the spend ceiling (an estimate of the
next call, prompt tokens plus max output at the model's rate, through
`pricing.rate_for_call`). A limit reached ends the run as `budget_exhausted`,
with `limit` set from the limit 01g-C4 reports. It is not an error, and it
files no error row.

**An unpriced model under a spend ceiling is refused before sending**
(01g-C4; the checklist's cross-spec decision). A ceiling cannot hold against
a price nobody reported, and counting such a call as free would break the
cost rule ("a price nobody reported is never rendered as zero"). `run_tools`
raises `RunRefused(kind="unpriceable")` before any send, and 12 treats it as
a skipped entry point with its reason recorded:

- **E1** skips silently for the turn, which proceeds on 09/10's evidence. The
  reason `unpriceable` is filed in the turn's prompt-log capture (8.2) and in
  the status frame (9.1).
- **E2 and E3** refuse at the start route **before reserving**, with 409
  `unpriceable`, as they refuse an `incapable` seam. The setting's hint says
  that a model with no rate cannot investigate under a spend ceiling, and
  that setting rates for it (or clearing the ceiling) lifts this.

### 7.3 Context window (01i-C1)

Before each model turn, the loop estimates the request's tokens (the
`tokens` module's counter where an encoder is loaded, otherwise the byte
heuristic the packer uses) and refuses the call, as `budget_exhausted` with
`limit: "context"`, when

```text
estimate + max_output > context_window - CONTEXT_MARGIN
```

Here `context_window` and `max_output` are 01i-C1's resolved facts for the
attempt the call will go to, and `CONTEXT_MARGIN = 512` tokens absorbs
estimator error. When 01i-C1 reports `context_window` unknown, the loop uses
`FALLBACK_CONTEXT_TOKENS = 8192`: deliberately small, so an unknown model is
bounded by the turn and byte caps long before it could overflow, and to be
raised once evals show it binds. Tool results are already clipped (4.2 rule
5), so the context check is a backstop rather than the main control.

## 8. Trace (12-C2b)

### 8.1 What it records

```json
{"version": 1, "run": "<run id>", "mode": "continuity", "task": "investigation-continuity",
 "model": "<provider_id>/<model>", "started": "<iso>", "outcome": "completed", "limit": "",
 "steps": [
   {"turn": 1, "tool": "search_history", "args": {"query": "the debt at the pier", "max": 4},
    "result": {"ok": true, "refs": ["scene:0007--the-pier", "scene:0012--saltmarch-quay"],
               "bytes": 1840, "truncated": false}},
   {"turn": 2, "tool": "get_scene_excerpt", "args": {"scene": "scene:0012--saltmarch-quay",
    "query": "paid", "max_posts": 6},
    "result": {"ok": true, "refs": ["scene:0012--saltmarch-quay"], "bytes": 2210, "truncated": true}},
   {"turn": 3, "tool": "propose", "args": {"decision": "resolved", "evidence_scenes":
    ["scene:0012--saltmarch-quay"]}, "result": {"ok": true}}],
 "inspected": ["scene:0012--saltmarch-quay"],
 "final": {"decision": "resolved", "reason": "...", "evidence_scenes": ["scene:0012--saltmarch-quay"]},
 "usage": {"model_turns": 3, "tool_calls": 3, "decide_calls": 0, "seconds": 11.4,
           "cost": {"cost_usd": null, "estimated_usd": null, "modelled_usd": 0.0041},
           "unpriced_calls": 0}}
```

- **No tool result text**, only refs, sizes and truncation. The text is in
  the store already. The trace says where it came from, which is the
  question it exists to answer.
- **No reasoning, and no assistant prose** other than the validated final
  payload's own fields (`reason`, `note`, `text`). Reasoning that a provider
  returned feeds `llm_reasoning` as it does today and is never copied into a
  trace.
- **Query strings are kept**, clipped to 200 characters. They are
  model-written, and they are the only record of *why* a tool was called.
  They may contain private prose derived from the store, so the trace is
  stored only in places that already hold private campaign content.
  This is 12's own trace, not 01g-C3's: 01g-C3's per-turn ledger rows and
  loop trace carry no argument or result text, and nothing here adds any to
  them.
- **Money is the three columns, never added** (CLAUDE.md, Costs), and
  `unpriced_calls` makes an incomplete figure say so.

### 8.2 Where it is stored

| Entry point | Where | Why there |
|---|---|---|
| E1 | The turn's prompt-log capture (`store/prompt_log.py`), as a zero-token section `investigation` drawn as "investigation · not sent", beside the speaker capture's `decision` outcome. Only when capturing is on (`prompt_log.capturing()`) | The prompt log is the existing opt-in, rolling debug store of what a turn's model saw. No new store |
| E2 | The candidate proposal's `investigation` field (6.2), in `continuity_candidates.json` | The review shows it beside the proposal it justifies. The file is a derived cache, so a later sweep that replaces the proposal drops the trace with it, which is right: the trace explained that proposal |
| E3 | The draft run's result, reaped with it | Nothing about an unstored answer is durable |

Every entry point also files 01g-C3's per-turn ledger rows under the run id,
which is the durable cost record, and 01g-C3's per-turn prompt-log captures
when capturing is on.

### 8.3 Privacy

- Ledger rows carry task, model, counts and money, never text (CLAUDE.md,
  Costs).
- `store/logs.py` receives decoded incoming SSE lines at Debug level only, as
  every adapter does. Those include the model's tool-call arguments.
  **Tool results are request content** (they are sent back to the model) and
  are never passed to the sink, by the existing rule that request bodies are
  never logged. The Settings disclosure about incoming capture already covers
  tool-call arguments, and its wording must name them when 01g lands.
- Failures are recorded at `Meter.done` with kind and status only.

## 9. Runtime ownership (12-C4)

### 9.1 E1: a stage of the turn run

E1 runs **inside the turn's existing run**, between 10's escalation and
`_prepare`, on the event loop, outside any campaign lock. It adds no run, no
class, and no exclusion key. The scene is already held by the turn's key, so
no shape change can land on the current scene while it runs (CLAUDE.md:
every shape-changing route is refused `scene_busy` while a turn holds it).
Other scenes can change. 6.1's re-classification at finish time is what
covers that.

- **Cancel**: the turn's cancel flag is the loop's `cancelled()`. A cancelled
  investigation ends the turn as the turn's cancel already does.
- **Frames**: the turn stream emits `{"type": "status", "phase":
  "investigating"}` while E1 runs, and `{"type": "status", "phase":
  "investigated", "selected": n, "reason": <terminal state or "unpriceable">}`
  when it ends, so the composer can say
  "Searching history…". This spec has not verified that the client ignores
  an unknown frame type. The plan must check `api.streamDraft` and the turn
  stream reader, and add the frame type to both, with a test, before the
  server emits it.
- **Metering**: each loop turn meters under `investigation-turn` with the
  turn's `campaign`, `scene` and `post` (the player post being answered), so
  the investigation's cost is charged to the post as a reroll's is
  (CLAUDE.md, per-post attribution).

### 9.2 E2: a `background` run on the campaign

`POST /campaigns/{cid}/continuity/candidates/{candidate_id}/investigate`,
`status_code=202`, `@computes_only`, `def` (not `async def`: a route that
reserves may not be, CLAUDE.md):

- 404 for an unknown candidate; 409 `not_live` for a candidate not `live`;
  409 from the seam for an incapable primary; 409 `unpriceable` for an
  unpriced model under a spend ceiling (7.2); 400 when
  `investigation_continuity` is off. All of these are refused before
  reserving;
- reserves a `background` run, kind `continuity-investigate`, on
  `runs.campaign_subject(cid)`;
- **one live investigation per campaign.** A start for the *same* candidate
  adopts the live run (attempt-id semantics as `post_reconcile`). A start for
  a *different* candidate answers 409 `busy` with the live run's payload.
  `reserve_campaign_background` adopts any live run of the kind, so it gains a
  `match` predicate (the candidate key on the run's labels) to tell the two
  apart;
- runs `investigate` and then `persist_proposal`. The run's result is
  `{"outcome", "persisted", "candidate"}`, polled through the campaign run
  routes that already exist (`routes/runs.py:1415-1435`);
- **why not the reconcile run**: folding E2 into the sweep would hold the
  campaign's sweep run for a whole investigation, delaying the next End
  Scene's incremental sweep behind a reader's optional question;
- **why not `maintenance`**: that class holds the *store* against tree
  operations and the image collector (`MAINTENANCE_KEY`, `routes/runs.py:116`).
  An investigation holds nothing and writes one cache record. Borrowing the
  class would refuse a backup or a fork for the length of a model loop.

### 9.3 E3: a `draft` run on the campaign

`POST /campaigns/{cid}/history/question`, through `runs.run_draft`
(`runs.py:2159`), answering 202. The client uses `api.draftRun` like every
other computing draft. A draft declares no exclusion key and nothing it
produces is durable (CLAUDE.md), which is exactly E3's contract.

### 9.4 What this changes in CLAUDE.md

E2 and E3 add two handlers that start detached runs. The "Detached runs"
section must name `post_candidate_investigate` (a `background` handler) and
`post_history_question` (a sixteenth computing draft), and its counts move
from thirty-two to thirty-four and from fifteen to sixteen.
`test_docs_guard.py` checks the section against the run classes
(`test_claude_md_names_every_run_class`), and the counts are prose the plan
must update in the same PR. The "Costs" and "Adding an LLM call site?"
paragraphs gain the `investigation` route.

The registry rules apply unchanged:

- `PUT /config/data-dir` is refused while any of these runs is live
  (`runs_in_flight`);
- deleting a campaign forgets its runs (`runs.forget_subject`);
- a live run keeps the Android foreground service promoted.

## 10. Eval gate before broad adoption (12-C2c)

### 10.1 Arms

`evals/run.py --investigate <mode>` compares, on one corpus:

- **A**: 09 hybrid retrieval only;
- **B**: 09 + 10 planner and one repair hop (the incumbent);
- **C**: B + 12 investigation on escalation.

For E2 the arms are the reconcile sweep's own answer (B) against the
investigation's (C) on the same candidates. For E3 they are a single
retrieval-plus-generate answer (B) against investigation (C).

### 10.2 Corpus

`evals/cases/investigation/` holds synthetic long-history campaigns, with
invented names only, built where the needed evidence is **adaptive**, that
is, not nameable from the query:

- a promise made in an early scene, referred to later only by a nickname
  introduced in between;
- "why did this thread change?", where the answer sits in a beat and a
  transcript two scenes apart;
- a duplicate, a continuation and a merely related pair whose distinguishing
  evidence is only in transcripts, not in titles;
- a commitment whose resolution is narrated without its title;
- for E1, a callback ("you said you'd never…") whose source the planner's
  query misses;
- for E1, every 11-C4 leakage case replayed with investigation on, with the
  same forbidden needles.

### 10.3 Metrics

- Evidence recall at the selected set (E1); proposal correctness against gold
  (E2), with `uncertain` scored as an abstention and not as wrong; answer
  correctness and citation validity (E3).
- **Leakage**, through 11-C4's graders, reported on its own line. Any
  leakage in arm C that B does not have fails the gate whatever the recall
  gain.
- Cost in the three money columns, latency, tokens, tool calls and
  `budget_exhausted` rate, through 01a-C1. Live runs are metered under 01a-C2's
  eval scope.

### 10.4 Adoption rule

Each entry point ships with its switch **off**. A switch's default may become
`on` only in a PR that records, in `evals/README.md`:

- the gate run's table;
- that C beats B on that entry point's correctness metric, by a margin the
  implementation plan states in advance;
- that leakage is not worse;
- the cost and latency it pays for that.

Until then the setting's hint says it is experimental. **E2 is the pilot.**
It is off the turn path, reviewable, and it has the reconcile corpus to start
from. E1 is last, because it is the only entry point a player waits on.

## 11. Contract

**12-C1: A read-only toolset, perspective-filtered in RP** (over 01g-C2a).

- *Inputs*: a mode (`rp_actor`, `rp_narrator`, `continuity`, `question`), a
  campaign, and a perspective bound by the run.
- *Outputs*: the tools of 4.3 in 01g-C2a's provider-neutral schema form, each
  returning the 4.1 envelope.
- *Guarantees*:
  - no tool mutates anything;
  - transcript text is the regex prompt view with hidden posts and director
    notes removed;
  - `gm-only` bodies are never returned;
  - in rp_actor mode every history item is filtered by 11-C1, ledger tools
    are not offered, and "not visible" reads as `not_found`;
  - every result is clipped, and the run's returned bytes are capped;
  - arguments are store refs, never paths;
  - no lock is held across a model call.
- *Failure*: a reader failure is a `{"ok": false, "error": "unavailable"}`
  tool result; the run continues.

**12-C2a: Budgets** (01g-C4, 01i-C1).

- The limits of 7.1 per entry point, enforced before each call is sent.
- Context is bounded by 7.3, with a structural fallback for an unknown
  window.
- Reaching a limit is `budget_exhausted` with `limit` (from 01g-C4's report),
  never an error.
- An unpriced model under a spend ceiling is refused before sending
  (01g-C4). E1 skips with the reason recorded; E2 and E3 answer 409
  `unpriceable` before reserving.

**12-C2b: Trace.**

- The 8.1 shape: tools, argument refs and queries, result refs, sizes and
  truncation, the inspected set, the final validated payload, and usage in
  three money columns.
- No tool result text and no reasoning.
- Stored where 8.2 says, and nowhere else.

**12-C2c: An eval gate.** Arms A, B and C on the 10.2 corpus with the 10.3
metrics. Each entry point stays default-off until a recorded run meets 10.4.

**12-C3: Write posture: selection in RP, one proposal for continuity,
nothing for questions.**

- E1 selects evidence that a tool returned, re-classified at finish, and
  rendered through 11-C2. No model prose reaches a turn.
- E2 writes one proposal on one live candidate through reconcile's
  validation, with the positive-evidence floor taken from the inspected set,
  fenced on fingerprint and prior proposal, and revision-stamped.
- E3 writes nothing.
- No investigation code calls a ledger, alias, link, knowledge (11-C3),
  transcript or record mutator, and the guards of section 12 hold that.

**12-C4: Three entry points and their run classes.**

- E1 is reached only as 01d-C2b's caller-supplied next resolver after
  10-C2's hop and 10-C3's sufficiency verdict (or by
  the explicit deep-history option), once per round, fail-soft, inside the
  turn run.
- E2 is a `background` run on the campaign, one live per campaign, 202.
- E3 is a `draft` run, 202.
- Each has its own task under the `investigation` route
  (`requires=("tools",)`) and its own default-off switch.

## 12. Interaction with repo rules

- **Routing guard** (`test_routing_guard.py`): add
  `Route("investigation", "History investigation", …, ("investigation-turn",
  "investigation-continuity", "investigation-question"), True,
  default_role="primary", requires=("tools",))` to `routing.ROUTES`. Every
  task is resolved through `require_inference`, and no `inference.resolve`
  call appears in `routes/`. The `decide` tool runs on 01g-C5's
  `tool_decision` route; 12 supplies the task name `investigation-decide`
  (01g-C5 takes it from the caller), registered on that route's tasks, so
  investigation sub-questions meter apart from play's.
- **Operation guard** (`test_operation_guard.py`): the loop's generations go
  through 01g-C2a's primitive, which the guard must recognise as the generate
  operation's tool form (01g's job). The `decide` tool goes through
  `operations.decide` with `resolved=`.
- **Usage guard**: every model turn and every embed under `search_history`
  passes a meter's holder (01g-C3, 08-C3a).
- **Regex prompt guard**: its scan is extended to `store/investigation/`, and
  `investigation.tools` readers that render transcript text are pinned by
  name.
- **Writer guards**:
  - `test_continuity_writer_guard.py`'s module list gains
    `store/investigation/*`;
  - a new `test_investigation_writer_guard.py` fails if anything under
    `store/investigation/` or `routes/investigation.py` calls a mutator of
    `plot`, `commitments`, `facts`, `events`, `relationships`,
    `continuity.doc`, `continuity.review`, `knowledge` (11-C3),
    `scenes.write`, `entities`, `playstate`, `dossiers` or `chronicle`,
    resolving import bindings as `test_absorb_writer_guard.py` does. The one
    allowed write is `investigation.persist_proposal`, which calls
    `continuity.candidates` through reconcile's `_commit`.
- **Lock domain** (`test_lock_domain_guard.py`): `store/investigation/persist.py`
  goes in `DOMAIN_MODULES` (its `cid`-taking mutator takes
  `campaign_lock`). `tools.py`, `prompt.py` and `trace.py` go in
  `OUTSIDE_DOMAIN` as read-only.
- **Lock order**: only one campaign is touched per run, so there is no
  `hold_all`.
- **Atomic writes**: the only write is through `candidates`, already atomic.
- **Import guard**: module-scope imports, submodule bindings
  (`from ..continuity import reconcile`, then `reconcile.rebuild(...)`).
- **Revision token**: E2's persist stamps for itself (a detached run's
  write). E1 writes nothing, and E3 writes nothing.
- **Pydantic v1 / Android**: tool schemas are plain dicts; route bodies are
  plain `BaseModel` fields via `_dump`. Tool calling on Android depends on
  01g-C2a's adapters (OpenRouter and OpenAI-compatible are pure Python). The
  Claude Agent adapter is a desktop extra, unavailable there as today, and
  reads as unable to call tools everywhere until 01g-C2b lands.
- **Privacy**: eval fixtures and the corpus use invented names only. No
  committed file describes a real store, and the gate's recorded table
  reports only metrics over the synthetic corpus.
- **Settings never spend unasked**: E2 and E3 are explicit reader actions on
  play surfaces, not settings, so the confirmation rule does not apply. Their
  switches live on Settings and spend nothing when flipped.
- **Docs guard**: CONTRIBUTING.md's guard table names
  `test_investigation_writer_guard.py`. CLAUDE.md's Detached runs section
  changes as in 9.4.

## 13. Non-goals

- Tools that create records (`propose_new_thread`, `propose_new_commitment`,
  `propose_alias` as a free-standing operation). The candidate cache has no
  kind for a new record. Aliases arrive through a `possible_duplicate`
  candidate's merge, as today.
- Investigation on any turn that 10 judged sufficient, except by the
  explicit deep-history option.
- Decision-as-tool in RP mode (02-C4's), and any recursion: a `decide` tool
  never offers tools.
- Persisting E3 answers, or a campaign "investigation history" page.
- Raw `search.py`/`semsearch.py`, filesystem, SQL, shell or vector tools.
- Provider-specific agent SDK features: Claude Agent's own tools stay off,
  and 12 uses 01g-C2a's neutral loop on every adapter.
- Showing chain-of-thought, in the trace or anywhere else.

## 14. Tests and acceptance

**Toolset** (`backend/tests/test_investigation_tools.py`):

- every tool, every mode: offered only in its modes; unknown and path-shaped
  refs return `not_found`;
- a hidden post never appears in a returned text;
- a regex prompt-phase rule applies to returned text;
- a `gm-only` entity returns name only;
- rp_actor: a narrator-only scene returns `not_found` (identical to a missing
  one); `get_actor` on another actor returns `not_found`; ledger tools are
  absent from the schema list;
- truncation at a post boundary sets `truncated`; the byte allowance returns
  `budget`;
- no tool holds `campaign_lock` (patched lock asserts not taken).

**Loop and budgets** (`test_investigation_loop.py`, with `llm_fakes` scripted
tool-call turns; a new scripted fake class for tool calls, added to
`backend/tests/llm_fakes.py`, never an inline fake):

- each limit of 7.1 ends the run `budget_exhausted` with the right `limit`
  and sends no further call;
- the context check refuses before sending, with both a known and an
  unknown window;
- a prose-only turn is `insufficient_evidence`;
- cancel between steps stops the run and abandons the in-flight call;
- a failed call is `failed` with the kind only;
- an unpriced model under a spend ceiling is refused before any send:
  E1 skips with `unpriceable` in the capture, E2 and E3 answer 409
  `unpriceable` before reserving.

**Write posture** (`test_investigation_persist.py`):

- E2: a proposal citing an uninspected scene is downgraded to `uncertain`;
- a fingerprint change, a sweep that replaced the proposal, and a dismissal
  each drop the write (`persisted: false`);
- `finish_no_change` does not overwrite an existing proposal;
- the revision is bumped on a write;
- the stored `investigation` trace round-trips through `candidates.read`;
- E1: unknown ids are dropped; an id re-classified as no longer visible is
  dropped; the rendered prompt contains only 11-C2 sections and none of the
  model's note;
- E3: uncited answers become `insufficient_evidence`.

**Runtime** (`test_investigation_routes.py`):

- E2's start refusals (404, 409 `not_live`, 409 `incapable`, 400 off) are
  answered before reserving;
- a same-candidate restart adopts; a different candidate gets 409 `busy`;
- the campaign run routes poll it; `PUT /config/data-dir` is refused while it
  is live; a campaign delete forgets it;
- E3 goes through `run_draft`, and a duplicate attempt is not re-run;
- E1 runs at most once per round, never on a reroll or an opener, and a
  failed E1 leaves the turn completing on 09/10's evidence.

**Guards**:

- `test_investigation_writer_guard.py`, the extended continuity writer
  guard, and the regex guard's new scan path, each proven by a planted
  violation;
- the routing guard accepts the new route and tasks.

**Eval**: the `--investigate` offline graders run in `pytest backend`, the
same as the existing offline suite. The live arms are opt-in.

**Acceptance**:

1. Every entry point is off by default and reachable only as section 3
   says.
2. No investigation can mutate anything but one candidate's proposal.
3. Every run ends in one of the six terminal states within its budget.
4. Every E2 proposal's evidence scenes are a subset of the scenes its trace
   inspected.
5. An actor-perspective investigation passes 11-C4's leakage graders.
6. The gate's table exists for any entry point whose default is turned on.

## 15. What to re-check against the parallel specs

The earlier assumptions are now contracts in `ROADMAP-CHECKLIST.md` and are
cited by ID above: 01g-C2a (caller-executed tools, caller-supplied run id,
final call returned), 01g-C4 (reports the stopping limit; refuses an unpriced
model under a ceiling), 01g-C5 (task name from the caller), 01d-C2b (a
caller-supplied next resolver), 10-C3 (the sufficiency predicate), 09-C1
(scene identity, post indices, keys and texts; a `perspective` seam), 10-C1
(takes a perspective), and the edges to 11-C2 and 01a-C1/C2. Three points
remain for the plan to confirm against the landed specs:

1. **10-C2's outcome carries what it tried**: the queries and the evidence
   ids it returned, so E1's seed can say what was already tried.
2. **09-C1 is callable as a tool**: given a query, a campaign and a
   perspective, it returns bounded units with ids and needs no turn context.
3. **01i-C1's `max_output`** where the source does not state one: 7.3 then
   uses the route preset's `max_tokens`, or a structural 1024.

## 16. Open questions

1. **Ship E3 at all?** It is the most open-ended entry point and has no
   review path. *Recommendation:* specify it now, build it after E2's gate
   passes, and drop it if E2's results show that adaptive search rarely
   beats the sweep.
2. **E3 answer retention.** Hold on the run only (as specified), or offer
   "save as note" into an existing user-authored surface? *Recommendation:*
   run only for v1. A saved answer is a model claim, and if it is ever stored
   it should go through a review path, not a button.
3. **Once per round for E1.** Is the *first* escalating actor the right one,
   or should the narrator turn get priority when it is in the round?
   *Recommendation:* first escalating actor, since narrator turns are rarer
   in a round and are the ones least likely to need old history. Revisit with
   eval data.
4. **Investigate from a `live` candidate with a non-`uncertain` proposal?**
   *Recommendation:* allow it (a reader may doubt a confident sweep), but
   label it "second opinion". The fencing in 6.2 already keeps the newer
   answer from being clobbered.
