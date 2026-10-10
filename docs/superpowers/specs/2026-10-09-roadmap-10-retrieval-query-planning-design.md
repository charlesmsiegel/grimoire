# 10. Retrieval query planning

**Status:** Draft — spec gate (substitute review) folded in; Codex gate pending.
**Date:** 2026-10-09
**Roadmap:** 10 in `ROADMAP-CHECKLIST.md`. Lane: retrieval.
**Baseline:** `main` at `35c1fb7`.
**Reconciles:** bundle draft `10-retrieval-query-planning.md` (2026-10-06,
planning anchor `7f80c42`). Builds on
`2026-10-09-roadmap-09-hybrid-historical-retrieval-design.md` (09), and on the
inference refactor (`2026-10-07-inference-backend-refactor-design.md`, 01) for
routes, roles, `generate` and `decide`.

> Implementation rule: re-read the code and every PR landed on this subsystem
> since the baseline before planning. 09, 01d and 01f will have landed by the
> time this is planned; re-read them as landed, because this spec consumes
> their contracts by headline and states what it needs of each (Depends on,
> and section 13).

## Depends on

Edges as `ROADMAP-CHECKLIST.md` lists them for 10.

| Contract | Provided by | What this spec uses it for | Hard or soft |
|---|---|---|---|
| 09-C1 `history.retrieve(Query) -> Evidence`, `history.merge`, the `perspective` seam | 09 | Running each planned round through the same retrieval, and merging rounds by scene identity without growing the section | Hard |
| 09-C2 the `Coverage` verdict | 09 | The free gate: the planner runs only when the first retrieval is `thin` or `empty` | Hard |
| 09-C3 the `history_recall` section and its row | 09 | Planned evidence renders through the same section; the plan's trace rides its inspector row | Hard |
| 01f-C1 `generate(schema=)` sends structured mode per attempt; per-call `max_tokens` cap | 01f | The planner's reply is schema-shaped where the provider supports it, and its output is hard-capped (section 5.3) | Hard |
| 01f-C3 the portable schema subset, `schemas.render`, `schemas.find_object` | 01f | The plan schema stays inside the subset, the prompt carries its rendering, and the parse uses the shared tolerant reader (5.2, 5.4) | Hard (part of 01f-C1's refusal) |
| 01a-C1 eval cost, latency and token reporting | 01a | The planning arms report the calls, tokens, time and three money columns they add (section 11) | Hard for live evals; offline needs nothing |
| 01f-C2 schema refusal re-sent through the helper shared with decide | 01f | A provider that refuses the field still yields a plan | Soft |
| 01d-C1 task policy; 01d-C2a trigger evaluation; 01d-C2b one escalation hop | 01d | Declaring that `history-sufficiency` may escalate, and escalating a non-answer or low margin once (section 7.3) | Soft: without it a non-answer is `unknown` and the repair hop does not run |
| 01a-C3 comparison table | 01a | Planning arms against 09's hybrid arm in one table | Soft |
| 02-C5b history relevance kit, the shared `history_check` route | 02 | The decide route `history-sufficiency` lands on, shared with 09's rerank | Soft |
| 01b-C1 decision capture at every decide site | 01b | Capturing the sufficiency decision to the prompt log | Soft: without it the decision is visible only in the history row |

## Required by

| Contract (provided here) | Consumer | Hard or soft | What the consumer uses it for |
|---|---|---|---|
| 10-C1 `history_plan` route, `Plan`, `plan.build_messages`, `plan.parse`, taking a `perspective` | 11 | Soft | Planning an actor's history questions once 11 lifts 09's NPC blanking, with the planner shown only what that actor may know |
| 10-C2 the bounded repair hop and its `PlanTrace` | 12 | Hard for RP mode | `PlanTrace`'s stable fields: terminal state, trigger, last 10-C3 verdict and its source, `cheap_retrieval_failed`, and per round (and `E0`) the questions and terms tried, scene keys and evidence ids (section 8.1) |
| 10-C3 the evidence-sufficiency predicate (`history-sufficiency` on `history_check`) | 12 | Hard for RP mode | The same question gates escalation from retrieval to investigation |

## 1. Current state (reconciled against main)

Nothing of 10 exists, and the draft's premises have moved in three ways that
change the design:

**Structured generation is half-built.** `inference.generate` already accepts
`schema=` and passes it to the facade (`backend/src/grimoire/inference.py:747`),
but its docstring is explicit that "the facade sends structured mode only on
an attempt flagged for it, which a generate resolution never is": the
structured flag is set only on a decide resolution's targets
(`resolve.py` around 955-966, the audit finding in the brief). 01f-C1 is the
contract that makes a generate attempt carry the mode. Until it lands, a
schema passed to `generate` is prompt text and nothing more, which is why the
planner keeps a tolerant parse (section 5.4) rather than trusting the shape.

**Routes are a frozen registry with legacy views.** `routing.ROUTES`
(`store/routing.py:82`) is the registry; every task a call site resolves must
be claimed by a route (`test_routing_guard.py`), a route whose tasks nothing
uses fails the same guard, and a route flips to `decide` only in the change
whose call site decides (`routing.py:44`-`50`). Three routes were split out of
legacy ones with `legacy=` (`speaker`, `scene_break`, `voice_drift`), and the
legacy surfaces are frozen by tests: `LEGACY_ROUTES` stays twelve routes with
their original task lists (`tests/test_routing.py:114`-`156`). A brand-new
route has no legacy layout to read, and neither existing option fits it
(section 4.1).

**Decide has no escalation and never re-asks on an answer.** The decide chain
moves on only on a failed call (`CLAUDE.md`, "What moves an item on to the
next stage is a failed call, never an answer"); an abstention, a native
`refused` and a `None` are answers. 01d-C2b is the one declared exception, a
single metered hop for a non-answer, and 10 uses it for exactly one question.

**And 09 changed where this runs.** 09 moves retrieval out of `compose` into a
phase before it, outside every lock, under one deadline
(09 section 10). The planner is an LLM call on the turn path, so it lives in
that phase too, in the route layer (`routes/history_recall.py`), where
`require_inference` is reachable and the store stays free of resolution.

## 2. Goal (and what is explicitly not the goal)

**Goal.** When 09's first retrieval cannot find enough, spend one cheap,
bounded model call to ask *what history this turn actually needs*, retrieve
again with the answer, and at most once more if a cheap check says the
evidence is still not enough. Never loop, never fail the turn, never grow the
section, and never run when the free gate says retrieval already succeeded.

1. The planner answers a question about history; it does not search. It emits
   a validated `Plan` (subjects, questions, terms, scopes, a scene count), and
   09 does every read.
2. It runs only on `thin` or `empty` coverage, or when explicitly forced
   (evals, a later "recall harder" control).
3. One repair hop at most, gated by a `decide` sufficiency predicate.
4. Every call is metered under its own task, attributed to the post it served,
   bounded in input, output and wall time, and fail-soft.
5. It earns a default place only through evals that price its recall gain
   against the calls it adds (01a).

**Not the goal:**

- A tool loop or an agent. That is 12, behind 01g's budgets.
- Planning on every turn. The free gate decides.
- Rewriting the reply's prompt. Only evidence reaches the scene prompt; the
  plan's questions and terms do not.
- Perspective-aware planning (11). The `perspective` field is carried and
  must be `narrator` until 11-C1.

## 3. Flow

All of it inside `routes/history_recall.gather` (09 section 10.1), after 09's
first retrieval and before the evidence is handed to compose, **under 09's
turn-phase deadline** (09 section 10.2: `HISTORY_PHASE_PLANNED_SECONDS`, 12 s,
when `history_plan` is `auto`), from which every step below draws
`min(own ceiling, remaining)`:

```text
E0 = retrieve(turn query)                                   09; no model call unless rerank
if mode == "off" and not force:                    return E0
if E0.coverage.verdict not in {"thin", "empty"} and not force:
    return E0                                       sufficient, exhausted or error: never plan
if not plan_allowed(cid, remaining):               return E0   resolution, budget, deadline
P1 = plan(turn, E0)                                         step 1: generate, history-query-plan
if P1 is None:                return E0                     failed or unusable: fail soft
if not P1.needs_history:      return E0                     planner says history is not needed
E1 = merge(E0, retrieve(query_of(P1)), ceiling, expand)     09, rerank=None
if not repair_enabled:        return finish(E1)             no check: `sufficiency` is not_asked
S1 = sufficient(turn, E1)                                   step 2: decide, history-sufficiency (+01d hop)
if S1 != "insufficient":      return finish(E1)             sufficient or unknown: stop
P2 = plan(turn, E1, tried=P1)                               step 3: generate, the repair hop
if P2 is None or P2.adds_nothing_to(P1): return finish(E1)  `unrepaired` / `repair_redundant`
E2 = merge(E1, retrieve(query_of(P2)), ceiling, expand)
if final_check: S2 = sufficient(turn, E2)                   step 4, only when asked (8.1)
return finish(E2)                                           never another plan
```

`finish` applies 09's optional rerank once to the final merge
(`history.merge(..., rerank=...)`, 09-C1), so a planned turn pays one rerank
at most. Every branch returns evidence; the worst case is `E0`, which is what
09 alone would have sent.

**The gate is `thin` or `empty`, and nothing else** (review B4). 09's
`exhausted` verdict (09 section 11.2: no eligible scene left unadmitted,
including a campaign's first scene) and its `error` verdict never plan:
there is nothing more to find, or retrieval itself failed. The trace records
`not_run:exhausted` or `not_run:error`.

**Once per player post** (review S8). On the round path 09 retrieves once per
round and reuses the evidence for every narrator contribution (09 section
10.1); planning runs inside that one retrieval, so a round plans at most once.

**The step cap and what it means in rows** (review S2). `MAX_PLAN_STEPS` is 3,
or 4 with `final_check`, counted before every step; the 01d escalation hop is
inside step 2 or 4, not beside it. A step is not one request:

| Step | Ledger rows, worst case |
|---|---|
| A plan (`generate`) | 1: 01f-C2's re-send and the facade's fallback file through the one meter (01f-C2: "`generate` files one row ... with `attempts` counting every attempt") |
| A check (`decide`, one item) | up to 4: primary stage, its prompt-only re-send, the fallback stage, its re-send (01f-C2: `decide` files a row per re-send); one request per stage on a native model |
| The 01d hop inside a check | up to 4 more, by the same count |

So the worst case is 2 + 2 x 8 = 18 rows with `final_check`, 10 without, and
a typical planned turn files 1 to 3 (one plan, one structured check, and on
a repair one more plan). Each row is attributed to the post. The phase
deadline, not the row count, is what bounds the wait.

## 4. Routes and tasks

### 4.1 A route born at format 2

One new route, campaign-scoped, and one task on 02-C5b's route:

```python
Route("history_plan", "History recall planning",
      "When recalled history looks thin, one short call that rewrites what the turn "
      "needs from the campaign's past.",
      ("history-query-plan",), True, default_role="fast", legacy=routing.NO_LEGACY),
```

and `history-sufficiency` **appended to 02-C5b's `history_check` entry**
(02 section 9.3), which is the single definition of that route: its label,
hint, `operation="decide"`, `default_role="decision"` and `NO_LEGACY` are
02's, and 10 changes only its task tuple (review S7). If 10-C3 lands before
09's rerank, 10's change adds 02-C5b's entry as written with
`("history-sufficiency",)` alone, and the rerank slice appends
`history-rerank`.

The 01d-C1 row for `history-sufficiency` (02 section 10's table, one row
added): `fallback="role"` (agreeing with `history-rerank`, as
`test_task_policy.py` requires of one route's tasks), escalation **on**
(`abstained`, `refused`, and `low_margin` where a margin is reported),
`samples` off. A sufficiency verdict is never sampled: it gates spend, and a
sampled gate would make spend random.

Neither `legacy=""` nor `legacy="summary"` is right for a route that never
existed under the format-1 layout:

- `legacy=""` makes it a legacy route: a thirteenth entry in `LEGACY_ROUTES`,
  a `route_history_plan` key in `CONFIG_KEYS` that the format-1 planner reads
  and the retirement deletes, and a red `test_legacy_surfaces_still_see_twelve_routes`.
- `legacy="summary"` folds its task into the summary route's legacy view, so
  `test_legacy_routes_keep_their_original_task_lists` goes red, and a legacy
  campaign's `route_summary` override would silently start pinning the
  planner: a setting the user made for summaries governing a call that did
  not exist when they made it.

So routes born at format 2 use `routing.NO_LEGACY`, the shared structure
`ROADMAP-CHECKLIST.md` records for 01g (`tool-decision`), 02-C5, 09 and 10
(`NO_LEGACY = None`; whichever lands first adds it):

- `Route.legacy` becomes `str | None`, which the mypy ratchet sees, and every
  test of it is an explicit `route.legacy is NO_LEGACY`. A truthiness test
  would be wrong: `_legacy_routes()`'s `if r.legacy: continue`
  (`routing.py:150`) reads `None` as falsy and would *include* such a route as
  a legacy route (review M5).
- `_legacy_routes()` skips such a route by that explicit test, so `LEGACY_ROUTES`, `CONFIG_KEYS`,
  `PRESET_CONFIG_KEYS` and the campaign allow-list (`routes_for`,
  `store/campaigns/lifecycle.py:536`) are unchanged and their frozen tests
  stay green as written.
- `legacy_key(route)` returns `""` for it (today it returns `route.legacy or
  route.key`, which would hand back the route's own key and read a
  `route_history_plan` nobody ever wrote). The two readers of `legacy_key`,
  the format-1 pins-and-presets mapping (`store/inference/legacy_plan.py:287`-`300`)
  and `legacy_route` (`legacy_plan.py:949`-`953`), skip such a route and answer
  `""` respectively. The other `routing.ROUTES` loops in `legacy_plan.py` read
  format-2 keys only and need nothing, and `store/inference/retire.py` reads
  only `CONFIG_KEYS` (`retire.py:98`), which never contains it. On a format-1
  store such a route therefore resolves to its `default_role` through the
  overlay, exactly as an unpinned format-2 route does.
- The Models page lists it from `routing.ROUTES` like every route
  (`store/inference/settings.py:305`), so 01s's settings work needs nothing
  route-specific.

`history_check` is 02-C5b's shared route: 09's rerank adds `history-rerank`
and 10-C3 adds `history-sufficiency`; the route lands with whichever call site
lands first, which is what `test_routing_guard.py` requires.

### 4.2 Why Fast for the plan and Decision for the check

The plan is short generative reformulation: natural-language questions and
search terms, which a decide question cannot express (a `Choice` cannot
invent a question). It is the `summary` and `tracker` routes' kind of work,
small and frequent, and they default to Fast (`routing.py:106`-`115`).

The check is a closed question, so `CLAUDE.md` requires a `decide` call ("A
yes/no ... question is a `decide` call, never prose parsed afterwards"), on a
decide route with the Decision role, which inherits Fast.

## 5. The plan (10-C1)

### 5.1 Inputs, bounded

`store/history/plan.py` builds the planner's messages from one
`PlanInput`, assembled in `gather` from data 09's query step already read:

| Part | Bound | Source |
|---|---|---|
| The turn window | last 2000 bytes, prompt view | 09 `query.build`'s window (already through `store.regex.view.view(..., phase="prompt")`) |
| The present cast | 12 actors, name and handle | 09's seeds |
| Mentioned records | 12, name, kind and handle | 09's seeds |
| Live threads and commitments | 10, title and handle, involving present actors first | `continuity.effective` rows 09 already read |
| Groups of the present cast | 8, name and handle | 07-C2 via 09's seeds |
| Evidence already found | 6 items: title, in-fiction date, one-line summary | `E0.items`, then `E1` for the repair |
| What was tried (repair only) | the previous plan's subjects (as handles), questions and terms, rendered as quoted data | `P1` |
| Coverage | the verdict word and the tiers tried | `E0.coverage` |

Total input is clipped to `PLAN_INPUT_BYTES` (6000) by dropping from the
bottom of this table upward, never the turn window.

**Only 09's filtered seeds** (review M6). Every record, thread, commitment and
group offered comes from 09's seeds after 09 section 5.1's gates: a ref the
reader excluded through pins, and a gm-only record, is never offered, so the
planner cannot be shown it or name it. **For an actor perspective** (10-C1,
once 11-C1 lands) the input is narrowed further to what 11-C1 classifies as
known to that actor: offered records, evidence found and the turn window as
that actor observed it. That is what keeps a plan's questions and terms from
encoding a secret: the planner was never shown one.

**What was tried is data, not instruction** (review M1). `P1`'s questions and
terms are model-written text. They are rendered inside a fenced block labelled
as the previous attempt's search, after normalisation and the same clipping
`parse` applies, and the template says to treat them as a list of what was
already searched, never as directions. They are bounded (3 x 200 and 8 x 60
characters) and come only from a reply that already passed `parse`. Signals, ranks and scores
are not shown to the planner: they are 09's reasons, and a planner that sees
"found by shared cast" learns to ask for shared cast.

**Handles, not refs.** Every record offered is shown as a short handle
(`s1`, `s2`, ...) beside its name; the mapping to refs stays server-side.
The planner can therefore only name what it was offered: an invented handle
fails validation exactly, and no store id reaches a provider it was not
already sent to. The handle scheme is `decisions.Option`'s idea applied to a
generate reply.

The prompt is `templates/history_plan/system.j2` and `user.j2`. It states the
job ("decide what earlier events the next reply needs; write questions a
search could answer"), the schema, and that an empty `subjects`, `questions`
and `terms` with `needs_history: false` is a correct answer.

### 5.2 The schema

The schema must pass 01f-C3's `schemas.check`, or 01f-C1 refuses the call
with `ValueError` before anything is sent (review B1). The portable subset
refuses numeric bounds, `minItems`/`maxItems` and any key it does not list
(`maxLength` included), and requires every property in `required` with
`additionalProperties: false` (01f section 3.1). So the schema carries
**shapes only**, and every bound lives in `plan.parse` (5.4):

```json
{"type": "object", "additionalProperties": false,
 "required": ["needs_history", "subjects", "questions", "terms", "scopes", "max_scenes"],
 "properties": {
   "needs_history": {"type": "boolean"},
   "subjects":  {"type": "array", "items": {"type": "string", "enum": ["s1", "..."]}},
   "questions": {"type": "array", "items": {"type": "string"}},
   "terms":     {"type": "array", "items": {"type": "string"}},
   "scopes":    {"type": "array", "items": {"type": "string",
                 "enum": ["shared_cast", "actor", "location", "thread", "relationship", "group"]}},
   "max_scenes": {"type": "integer"}}}
```

- **No offered records**: `subjects.items` is `{"type": "string"}` with no
  `enum`, since an empty `enum` is accepted by no strict mode; `parse` then
  drops every subject as unknown.
- **The bounds the schema cannot carry** (6 subjects, 3 questions of 200
  characters, 8 terms of 60, `max_scenes` in `[1, history_recall_depth]`) are
  stated in the prompt's prose and enforced by `parse`.
- **The prompt carries `schemas.render(schema)` byte-for-byte**, as 01f-C1
  requires; the template renders it with the shared filter rather than
  spelling the schema itself.

`scopes` are 09's structural relations by name (09 section 5.2), not the
draft's `actor_history`/`shared_scenes`/`thread_history`: one vocabulary, so a
plan is a `Query` without translation.

### 5.3 The call

```python
resolved, why, kind = await run_in_threadpool(
    _soft_resolved, lambda: require_inference("history-query-plan", cid))  # literal in the thunk
schema = plan.schema_for(offered, depth)
with store.usage.meter("history-query-plan", campaign=cid, scene=sid, post=turn_index) as m:
    text = await _bounded_call(
        operations.generate("history-query-plan", messages, client=client,
                            resolved=resolved, usage=m.usage, schema=schema,
                            max_tokens=PLAN_MAX_TOKENS, stream=False),
        ceiling=min(PLAN_CEILING, remaining()),
        on_timeout=_noting(client, resolved, m.usage))
got = plan.parse(text, offered, depth)                       # after the meter's block
```

- **Resolution in a worker** (review S5): `require_inference` reads
  `config.md`, the connections and the catalog synchronously, so it runs
  through `run_in_threadpool`, the pattern the round driver already uses
  (`character_turns.py:748`-`749`); the task stays a literal inside the thunk
  for `test_routing_guard.py`. `_soft_resolved` (`routes/common.py:1519`): a
  route that cannot resolve (no key, `incapable`) skips planning with a reason
  in the trace; the turn is untouched.
- **The `draft_completion` pattern** (review S6): `_bounded_call` with
  `on_timeout=_noting(...)`, so a planner that always overruns marks its
  connection as failing like every other bounded generation
  (`routes/common.py:249`-`252`), and `plan.parse` runs after the meter's
  block, so a parse cannot change what the row says.
- **The ceiling** is `min(PLAN_CEILING, remaining)`: `PLAN_CEILING` (6 s) for
  one short structured reply, never the `llm_call_budget` default, whose `0`
  means "no ceiling at all" (`routes/common.py:544`-`570`), and never past the
  turn-phase deadline (09 section 10.2).
- The meter is the call site's own (`test_usage_guard.py`), and `post=` puts
  the planner's cost on the post it served, beside the reply's (`CLAUDE.md`,
  attribution per player post).
- **Structured mode and the cap** (01f-C1): `schema=` reaches the provider's
  structured mode on each attempt that can take it, the schema also in the
  prompt. `max_tokens=PLAN_MAX_TOKENS` caps the reply at `min(preset's cap,
  PLAN_MAX_TOKENS, the model's known max output)`, never raising a preset's
  cap. `PLAN_MAX_TOKENS` is **1024** (review S3): a maximal valid plan (6
  handles, 3 questions of 200 characters, 8 terms of 60, the scopes and the
  JSON syntax) is a few hundred tokens, and the rest is an allowance for a
  reasoning model, whose `max_completion_tokens` counts reasoning (01f section
  3.9) and whose adaptive thinking on Anthropic is held to half the cap. The
  route's hint says a non-reasoning preset is the right choice for it, and the
  eval suite carries a maximal-plan case so truncation shows up as
  `unusable`, not silently.
- **Unpriced** (review M3): a Fast model with no stated price files unpriced
  rows, so the Costs view reads "incomplete" until rates are set; the settings
  copy says so, as 09 section 14 does for embeddings.

### 5.4 Tolerant parse and validation

`plan.parse(text, offered: Offered, depth: int) -> Plan | None`, pure and
total (it never raises):

1. **Object.** `schemas.find_object(text)` (01f-C3), the shared tolerant
   reader: the reply's top-level JSON value, tolerant of a fence or prose
   around it. No object, or not a JSON object: `None`, trace `unreadable`.
2. **Fields, independently.** A field that is missing or the wrong type takes
   its empty default rather than voiding the plan, the per-section tolerance
   `continuity/doc.py` uses for a hand-edited file:
   - `subjects`: handles mapped to refs; an unknown handle is dropped and
     counted (`dropped.subjects`); at most 6 kept.
   - `questions`: strings stripped, clipped to 200 characters at a word
     boundary, deduplicated case-insensitively, at most 3; one with no letters
     is dropped.
   - `terms`: the same, 60 characters, at most 8, and a term that is a
     stopword or a single character is dropped.
   - `scopes`: intersected with 09's `RELATIONS`; empty means all, and
     `query_of` passes 09's `RELATIONS` explicitly, since a `Query` scope
     outside it is a `ValueError` (review M2).
   - `max_scenes`: clamped to `[1, history_recall_depth]`; absent means the
     setting.
   - `needs_history`: absent reads as `true` if anything else survived.
3. **Usable.** A plan with no subjects, questions or terms after validation is
   `None` (trace `empty`), unless `needs_history` is explicitly `false`, which
   is a valid answer (trace `declined`).

```python
@dataclass(frozen=True)
class Plan:
    needs_history: bool
    subjects: tuple[str, ...]          # refs, validated
    questions: tuple[str, ...]
    terms: tuple[str, ...]
    scopes: frozenset[str]
    max_scenes: int
    perspective: str = "narrator"
    dropped: Mapping[str, int] = field(default_factory=dict)   # for the trace only
```

`plan.query_of(plan, turn_index) -> history.Query`: `texts = questions`,
`terms = terms + subject names`, `subjects`, `scopes or RELATIONS`,
`max_scenes`, `perspective`, `turn_index`. The turn window is not repeated: `E0` already
retrieved on it, and `merge` keeps its signals.

## 6. Merging rounds without growing the prompt

Each planned round goes through 09-C1 with `rerank=None` and
`deadline=remaining`, and is merged into the evidence so far with
`history.merge(prev, round, ceiling=..., expand=...)` (09-C1):

- Candidates are matched by `SceneRef.key` (09 section 7.2: the identity, or a
  sid fallback for a legacy scene), so a scene found by both rounds carries
  both rounds' signals under `(round, signal)` ranks, and its agreement count
  can rise.
- A newly selected scene that no round expanded is expanded through 09's
  `expand` callback, at most `depth` transcript reads per merge.
- The merged order is RRF over the union of every round's ranked lists, so a
  scene the turn window and a planned question both found ranks above one
  either found alone. No round's order is privileged; that would be a fused
  confidence by another name.
- The selection is at most `history_recall_depth` items under the same
  ceiling as `E0` (09 section 9.2), not at most `E0`'s item count: planning
  runs because `E0` was thin or empty, so it must be able to add (review B3). Planning can change *which* scenes the section carries,
  never how many or how large. `max_scenes` narrows a round's own selection
  and nothing else.

`PlanTrace.gain` records which selected scenes were not selected in `E0`,
which is the eval's primary signal and the inspector's headline.

## 7. The sufficiency predicate (10-C3)

### 7.1 The question

`store/history/sufficiency.py` builds one `decisions.Item`:

- **context**: the turn window (clipped as for the planner) and the merged
  evidence as it would render: each item's header, one-line summary and its
  first excerpt line, at most `depth` items.
- **question**: `Predicate("sufficient", ...)`, worded in
  `templates/history_check/question.j2`: whether the evidence contains what
  the next reply needs to stay consistent with what happened earlier, with
  "not needed" counting as sufficient.
- `explain=""`: no rationale is asked for. Nothing would stand on it (a
  status word stands on its evidence, `CLAUDE.md`), and feeding a model's
  rationale into the repair planner would make one model's prose another's
  instruction. The repair planner is told only that the check said
  insufficient, and what was tried.

The call is `operations.decide("history-sufficiency", [item], client=...,
resolved=..., campaign=cid, scene=sid, post=turn_index, around=...)`
(`inference.py:684`), resolved with
`require_inference("history-sufficiency", cid, operation="decide")` through
`_soft_resolved` **in a worker** (5.3). Its `around` bounds each facade call
by `min(CHECK_CEILING, remaining)` (`CHECK_CEILING` 4 s), and the whole check,
its fallback stage, its 01f-C2 re-sends and its 01d hop included, stops
starting new calls once the turn-phase deadline has passed (review S1).

### 7.2 Mapping the answer

| Decide outcome | Verdict |
|---|---|
| `True`, read | `sufficient` |
| `False`, read | `insufficient` |
| abstained, native `refused`, unreadable, failed call, unresolvable route, ceiling hit | escalate (7.3), else `unknown` |

`unknown` stops planning, exactly like `sufficient`. Spending the repair hop on
a non-answer would turn "the checker could not say" into "go and spend more",
which is the opposite of the cost rule.

### 7.3 Escalation through 01d-C2a and 01d-C2b

When 01d has landed, `history-sufficiency` declares in its 01d-C1 policy that
escalation is allowed, and a non-answer (abstention, native `refused`) or a
low margin (where the backend reports one: a native distribution, never a
fabricated one) is evaluated by 01d-C2a and handed to 01d-C2b's hop, which asks the declared next
resolver once, metered and captured. Its answer maps through 7.2 again; a
second non-answer is `unknown`. A failed *call* is not escalated by 01d: it
already moved down the decide chain to its fallback, which is the chain's
job. Which resolver is next is 01d's to define; the recommendation is the
Primary role, the strongest model the user has already chosen to pay for
(open question 4).

Before 01d lands, a non-answer is `unknown` and the repair hop does not run.

## 8. The repair hop (10-C2)

The repair hop is a second planner call with `tried=P1`, gated on an
`insufficient` verdict. It is the last *plan* the phase makes.

- **Inputs** as 5.1, with `E1`'s items as the evidence found and `P1` as what
  was tried (quoted data), and the instruction to ask for what is still
  missing rather than restate.
- **Novelty.** `P2.adds_nothing_to(P1)` is true when `P2`'s subjects, questions
  and terms are each a subset of `P1`'s after normalisation. Such a plan
  retrieves nothing new, so it is not run.
- **No further plan, ever.** At most one check may follow it, and only when
  the caller asks (`final_check`, 8.1); that check's verdict is recorded and
  acted on by 12, never by another plan.
- **Deadline.** Every step, the planned retrievals included, draws from 09's
  turn-phase deadline (09 section 10.2), each taking `min(own ceiling,
  remaining)`, so a step started late cannot overrun it (review S1). On
  `post_chat` the worst wait before the first frame is therefore 09's file
  stages plus `HISTORY_PHASE_PLANNED_SECONDS` (12 s), and lore recall's own
  embed in compose, which 09's shared outage memo caps during an outage.

### 8.1 The trace, and what 12 reads

`PlanTrace` records every step: `trigger` (`thin`, `empty`, `forced`), each
call's task, outcome and milliseconds, the parsed plans (refs shown as names),
the validation drops, each sufficiency verdict and whether it escalated, and
three stable fields 12 reads (review S4; the coordinator's second addendum):

```python
@dataclass(frozen=True)
class PlanTrace:
    terminal: str                    # the table below
    trigger: str                     # thin | empty | forced | "" (planning did not start)
    sufficiency: str                 # last 10-C3 verdict: sufficient | insufficient | unknown | not_asked
    sufficiency_source: str          # answer | escalated | unknown | not_asked
    cheap_retrieval_failed: bool     # the repair hop ran AND a 10-C3 check after it said insufficient
    e0: RoundRecord                  # what 09's first retrieval selected
    rounds: tuple[RoundRecord, ...]  # one per planned round, in order
    steps: tuple[Step, ...]          # task, outcome, ms, rows filed

@dataclass(frozen=True)
class RoundRecord:
    questions: tuple[str, ...]       # () for E0
    terms: tuple[str, ...]           # () for E0
    subjects: tuple[str, ...]        # refs; () for E0
    scene_keys: tuple[str, ...]      # SceneRef.key of each selected item (09 section 7.2)
    evidence_ids: tuple[str, ...]    # the selected items' `EvidenceItem.evidence_ids`, as 09-C1 defines them
```

**Stable fields** (12's fold-in). `terminal`, `trigger`, `sufficiency`,
`sufficiency_source`, `cheap_retrieval_failed`, `e0` and `rounds` are the
fields 12's RP trigger reads, and they are part of 10-C2: renaming or
re-meaning one is a contract change, never a refactor. `sufficiency_source`
says whether the last verdict was the check's own answer, an answer only
after 01d-C2b's escalation hop, or `unknown` (a non-answer that stayed one).
Evidence ids are 09-C1's (`EvidenceItem.evidence_ids`), copied unchanged, so
12 can name exactly which evidence each round added; 10 defines no spelling of
its own. `steps` is diagnostic and not stable.

`cheap_retrieval_failed` is the one field that means "planning and its one
repair could not find enough". It is true only when the repair hop ran
(terminal `repaired`) **and** the post-repair check (`final_check`) returned
`insufficient`. `gather` passes `final_check=True` exactly when 12's RP-mode
investigation is enabled for the campaign, so the extra decide call is spent
only when something will act on its answer; with it off, `sufficiency` after a
repair is `not_asked` and `cheap_retrieval_failed` is false. With
`history_plan_repair` off the pre-repair check does not run either, so 12's
RP entry, if it wants a verdict, calls 10-C3 itself on the final evidence;
10-C3 is a public function for exactly that.

| Terminal state | Meaning |
|---|---|
| `not_run:<why>` | mode off, coverage `sufficient`, `exhausted` or `error` |
| `skipped:<why>` | unresolvable route, deadline, step cap, budget (open question 3) |
| `declined` | the planner said no history is needed |
| `unusable` | the first plan was unreadable, empty, or timed out |
| `planned` | one round; repair off, or the check said sufficient or unknown |
| `unrepaired` | the check said insufficient, and the repair plan was unusable, timed out, or cut by the deadline or the cap |
| `repair_redundant` | the check said insufficient, and the repair plan added nothing |
| `repaired` | the repair hop ran; `sufficiency` says what the final check found, if asked |

12's RP trigger is `cheap_retrieval_failed`, plus, at 12's discretion,
`unrepaired` and `repair_redundant` with `sufficiency == "insufficient"`
(planning could not even try again, or had nothing new to try). The
combinations are stated here so 12 does not re-derive them.

## 9. Visibility

The 09 history row (09 section 9.5) gains a `plan` key holding the
`PlanTrace` projection. The questions and terms are model-written text and
are shown there; they never reach the scene prompt, and
`test_reasons_never_reach_the_prompt` gains a scenario in which a plan ran and
asserts that its questions, terms, handles, verdict and terminal state are
absent from `build_messages`.

The sufficiency decision is captured to the prompt log through 01b-C1 when it
lands, with the speaker capture's privacy (01b-C2). The planner's own request
is not captured in this spec; its parsed plan and trace are, inside the
turn's breakdown (open question 6).

## 10. Configuration and cost controls

| Key | Default | Meaning |
|---|---|---|
| `history_plan` | `off` | `off` or `auto`. `auto` plans on `thin` or `empty` coverage |
| `history_plan_repair` | `on` | Whether `auto` may take the repair hop |

With `history_plan` on, 09's turn-phase deadline is
`HISTORY_PHASE_PLANNED_SECONDS` (12 s) rather than 4 s; planning has no
deadline of its own.

Both join `config._CONFIG_KEYS` (`store/config.py:219`) and, under 01s, the
Context pane beside 09's keys. `history_plan` has no effect while
`history_recall_depth` is 0. `force` is a parameter of `gather`, used by the
evals and by a later explicit control, never a stored setting.

The controls, each with what it bounds:

| Control | Bounds |
|---|---|
| The free gate (09-C2 coverage) | How often planning runs at all |
| `PLAN_INPUT_BYTES`, the per-part caps in 5.1 | Planner input |
| Parse-side bounds (5.4), `PLAN_MAX_TOKENS` (01f-C1), `PLAN_CEILING` | Planner output and its wall time |
| `CHECK_CEILING`, one item, no rationale | The check |
| `MAX_PLAN_STEPS` (3, or 4 with `final_check`), the novelty rule, no plan after repair | Steps per turn; section 3 states the worst-case rows |
| 09's turn-phase deadline, shared | The whole phase's wall time |
| Once per player post (09's per-round retrieval) | Group rounds |
| 09's one embed call per round, one rerank per turn | Retrieval cost added by planning |
| 09's ceiling and depth | Prompt tokens: planning adds none |
| Ledger tasks `history-query-plan`, `history-sufficiency` | What it cost, per post, in the three money columns |

Planner output tokens are hard-capped by 01f-C1's per-call `max_tokens`
(`PLAN_MAX_TOKENS`), beside the schema's bounds and the ceiling.

## 11. Evals (through 01a)

The suite extends 09-C4's generator and harness (`evals/history/`), adding
cases and arms rather than a second corpus.

**Cases** (synthetic, placeholder names only), the draft's six plus two
controls:

1. A generic accusation ("You knew the whole time, didn't you?") whose
   evidence shares no words with it.
2. A callback to an old promise, phrased differently from the commitment.
3. "You said you'd never..." against a scene many scenes back.
4. A relationship reversal: the evidence is the scene that moved the bond.
5. Hidden object knowledge: who handled the item, and where.
6. Similarly named incidents: two scenes with near-identical wording, one
   correct.
7. **Control, needs nothing**: small talk with no history in it. Planning
   should not run, or should decline.
8. **Control, already found**: 09 alone succeeds. The free gate should stop
   planning.
9. **Control, young campaign**: a first scene, and a campaign with fewer
   earlier scenes than `depth`. Coverage is `exhausted`; planning must not run
   (review B4).
10. **Maximal plan**: a recorded reply at every parse-side bound, which must
    parse whole within `PLAN_MAX_TOKENS` (review S3).

Plus validation cases replayed from recorded planner replies: an invented
handle, a bare string where an array belongs, a fenced object, prose with no
object, an over-long question. Each must parse to the documented `Plan` or
`None`.

**Arms**: `hybrid` (09 alone), `plan` (`auto`, no repair), `plan+repair`
(`auto`), `plan-always` (forced every turn, as an upper bound on recall and on
cost).

**Metrics** per arm and aggregate, through 01a-C1, compared in 01a-C3's
table: scene recall at 1, 3 and 5 and excerpt recall (09's); `gain` (selected
scenes not in `E0`); trigger rate and, on the controls, false-trigger rate;
plan validity rate and drops; steps and ledger rows, tokens, wall time and
cost (three money columns, never added) added per turn; terminal-state counts
and `cheap_retrieval_failed` rate. **Live arms add 09's downstream-consistency
grader** (the reply names the planted fact token and does not contradict it,
09 section 12.3), which is the draft's "retrieval/response improvement"
(review M4).

**Offline** (`evals/run.py --history --plan`): recorded planner replies and
recorded sufficiency decisions (`evals/recordings/history-plan.<case>.json`),
09's recorded vectors, deterministic graders; inside `pytest backend`.
**Live**: the configured Fast and Decision models, metered under 01a-C2's
eval scope.

**The gate for a default.** `auto` stays off by default unless `plan+repair`
improves recall at 3 over `hybrid` on cases 1 to 6 while the controls'
false-trigger rate stays low and the added cost per planned turn is within a
bound agreed in conversation. Figures are compared, never committed against a
user's library.

## Slices

Landing order within this spec: S1 → S2 → S3. `history_plan` defaults to
`off` in every slice, so no turn plans until a reader turns it on.

### 10-S1: The planner and one planned round

- **Delivers:** 10-C1 (full); 10-C2 (part: the free gate on `thin`/`empty`, one planned round merged into `E0`, the shared turn-phase deadline, `MAX_PLAN_STEPS`, once per player post, and `PlanTrace`'s `terminal`, `trigger`, `e0` and `rounds`, with `sufficiency` always `not_asked`)
- **Needs (this spec):** none
- **Needs (other specs):** 09-C1 (H: `retrieve(Query)`, `history.merge(..., expand)`, `SceneRef.key` and evidence ids); 09-C2 (H: the `Coverage` verdict with `exhausted` and `error`, and the turn-phase deadline 10 draws from); 09-C3 (H: the history row the trace rides on); 01f-C1 (H: `generate(schema=)` structured mode per attempt, and the per-call `max_tokens` cap); 01f-C3 (H: `schemas.check`, `schemas.render`, `schemas.find_object`); 01f-C2 (S: a schema refusal re-sent without the mode — until it lands, a refusing provider fails the call and the trace says `unusable`)
- **Needs (slices):** 01f-S1 (H), 01f-S2 (S), 01f-S3 (H), 09-S2 (H), 09-S3 (H)
- **Scope:** The `history_plan` route lands with its one call site (the routing guard forces both into one slice), with `routing.NO_LEGACY`, its explicit `is NO_LEGACY` tests, the `NO_LEGACY_TASKS` baseline mechanism and the `test_routing.py` registry edits, unless 09-S5 already landed the sentinel and the mechanism. `store/history/plan.py` (`build_messages`, `schema_for`, `parse`, `query_of`), `templates/history_plan/`, the planner call in `routes/history_recall.py` (resolution in a worker, `_bounded_call` with `_noting`), the `history_plan` config key, the `plan` key on the history row. No repair and no check yet.
- **Acceptance:** section 14's store tests (parse, `schemas.check` with zero and many offered records, the rendered schema in the prompt, `query_of`, `build_messages` caps and quoted data); the Routes tests for off, `sufficient`/`exhausted`/`error`, `thin` with one round (an `empty` `E0` gains items), resolution off the loop, unresolvable route, planner timeout with `_noting`, the deadline, one plan per round; the `NO_LEGACY` pins of section 13; `test_reasons_never_reach_the_prompt` with a plan.
- **Size:** L

### 10-S2: The sufficiency check and the repair hop

- **Delivers:** 10-C3 (full); 10-C2 (full)
- **Needs (this spec):** 10-S1 (H)
- **Needs (other specs):** 02-C5b (S: the `history_check` route entry — until it lands, this slice lands 02's entry as written with `("history-sufficiency",)` alone, per section 4.1); 01d-C1 (S: the `TaskPolicy` row for `history-sufficiency` — until it lands, no row and the chain's default); 01d-C2a (S: the trigger evaluation — until it lands, a non-answer is `unknown`); 01d-C2b (S: one escalation hop after the unchanged chain — until it lands, `sufficiency_source` is never `escalated` and a non-answer stops planning); 01b-C1 (S: the decide capture helper — until it lands, the verdict is visible only in the history row)
- **Needs (slices):** 01b-S2 (S), 01d-S1 (S), 01d-S2 (S), 01d-S4 (S), 02-S7 (S), 10-S1 (H)
- **Scope:** `store/history/sufficiency.py`, `templates/history_check/`, the public `sufficient(...)` in `routes/history_recall.py`, its `decide-history-sufficiency` replay case, the repair hop (`tried`, novelty, quoted data), `final_check` passed by `gather` when 12's RP mode is on, and the rest of `PlanTrace` (`sufficiency`, `sufficiency_source`, `cheap_retrieval_failed`, the full terminal table). The `history-sufficiency` task lands with this call site on `history_check`. The `history_plan_repair` key lands here, default `on` but inert while `history_plan` is off.
- **Acceptance:** section 14's Routes tests for repair (`insufficient` -> second plan, `sufficient`/`unknown` stop, `repair_redundant`, `unrepaired`), `final_check` and `cheap_retrieval_failed`, the trace's stable-field snapshot, escalation once 01d lands, the step cap; `sufficiency.item` asks no rationale; the replay case.
- **Size:** M

### 10-S3: Planning evals

- **Delivers:** none in full (acceptance item 5)
- **Needs (this spec):** 10-S2 (H)
- **Needs (other specs):** 09-C4 (H: the long-history generator, harness and arms in `evals/history/`); 01a-C1 (H: per-case and per-call wall time, tokens and the three money columns, for the live arms); 01a-C2 (H: live evals metered by the production meter, for the live arms); 01a-C3 (S: the comparison table — until then, one report per arm)
- **Needs (slices):** 01a-S2 (H), 01a-S3 (H), 01a-S4 (S), 09-S6 (H), 10-S2 (H)
- **Scope:** Cases 1 to 10 of section 11 on 09's generator, the recorded planner replies and sufficiency decisions, the `plan`, `plan+repair` and `plan-always` arms beside 09's `hybrid`, `evals/run.py --history --plan` offline inside `pytest backend`, and the live arms with 09's downstream grader. The gate for making `auto` a default reads this slice's output; the default itself does not change here.
- **Acceptance:** the offline validation cases parse as documented; the controls (young campaign included) do not trigger; live arms report steps, rows and the three money columns through 01a.
- **Size:** M

## 12. Contract

### 10-C1: the `history_plan` route and a structured, validated plan

- **Route**: `history_plan`, task `history-query-plan`, `operation="generate"`,
  `default_role="fast"`, campaign-scoped, `legacy=NO_LEGACY` (section 4.1).
- **Store**: `plan.build_messages(PlanInput) -> list[dict]`,
  `plan.schema_for(offered, depth) -> dict`, `plan.parse(text, offered, depth)
  -> Plan | None` (total, never raises), `plan.query_of(Plan, turn_index) ->
  history.Query`.
- **Perspective**: `PlanInput.perspective` (`narrator` or `actor:<ref>`) is
  carried into `Plan` and `query_of`'s `Query`. For an actor perspective the
  planner's input (offered records, threads, evidence found, turn window) is
  built only from what 11-C1 classifies as known to that actor, so a plan's
  questions and terms cannot encode a secret: the planner was never shown one,
  and a handle it was not offered fails validation. Until 11-C1 lands, any
  perspective but `narrator` is a `ValueError`, matching 09-C1.
- **Guarantees**: a `Plan` names only offered records; every list is bounded;
  output is capped by 01f-C1's `max_tokens`; a provider without structured
  mode still yields a plan when the reply holds one.
- **Failure**: unresolvable route, failed call, ceiling, unreadable or empty
  reply each yield no plan and a trace state; never an exception into the
  turn.

### 10-C2: one bounded, metered repair hop

- **Entry**: `routes/history_recall.plan_and_repair(app, cid, sid, turn, E0, *,
  client, ceiling, deadline, force=False, final_check=False) ->
  tuple[Evidence, PlanTrace]`, called from 09's `gather` on `thin` or `empty`
  coverage only.
- **Guarantees**: at most `MAX_PLAN_STEPS` steps (3, or 4 with `final_check`),
  at most one repair hop, never a plan after it; every step takes `min(own
  ceiling, remaining)` of 09's turn-phase deadline; each call metered under
  its task with campaign, scene and post, with the worst-case row count stated
  in section 3; at most once per player post; the returned evidence has at
  most `history_recall_depth` items and the same ceiling as `E0`; the worst
  case returns `E0` unchanged.
- **The trace 12 reads** (8.1), as stable fields: the terminal state; the
  trigger that started planning; the last 10-C3 verdict and whether it was an
  answer, an escalated answer or unknown (`sufficiency`,
  `sufficiency_source`); `cheap_retrieval_failed` (the repair hop ran and the
  post-repair 10-C3 check said insufficient); per round, the questions,
  terms and subjects tried; and the scene keys and evidence ids of `E0` and
  of each round (`e0`, `rounds`).
- **Escalation**: only the sufficiency check escalates, through 01d-C2a/C2b, one
  hop, and only for a non-answer or a reported low margin.
- **Failure**: never raises into the turn; the trace says where it stopped.

### 10-C3: the evidence-sufficiency predicate (new; split out of 10-C2)

- **Route**: 02-C5b's `history_check` (decide, Decision role, `NO_LEGACY`),
  task `history-sufficiency` appended to its tuple; the route lands with the
  first of its call sites. 01d-C1 row: fallback `role`, escalation on,
  samples off.
- **Store**: `sufficiency.item(turn_window, evidence) -> decisions.Item`.
- **Route layer**: `sufficient(app, cid, sid, turn_window, evidence, *,
  client, deadline) -> Literal["sufficient", "insufficient", "unknown"]`,
  mapping per section 7.2, escalation per 7.3; public, so 12's RP entry can
  ask it of the final evidence when the trace says `not_asked`.
- **Guarantees**: no rationale requested or consumed; a non-answer is never
  `insufficient`.

## 13. Interaction with repo rules

- **Routing guard.** Both task literals appear at their call sites through
  `require_inference` thunks; each route lands with its call site; the
  decide route is `operation="decide"` and uses `operations.decide`
  (`test_operation_guard.py`). `NO_LEGACY` is new vocabulary in
  `store/routing.py`, which must stay a pure leaf: a sentinel constant keeps
  it one.
- **Frozen legacy tests** (`test_routing.py:114`-`156`) stay green
  unmodified (section 4.1). A test pins that a `NO_LEGACY` route never appears
  in `LEGACY_ROUTES`, `CONFIG_KEYS`, `PRESET_CONFIG_KEYS` or
  `routes_for(...)`, and that a format-1 store resolves it to its default role
  without reading any legacy key (`tests.inference_fixtures.legacy_store()`).
- **Tests this change edits** (review B2), named so nobody discovers them red:
  - `test_routing.py:159`-`172`, `test_every_route_declares_operation_and_default_role`:
    `len(routing.ROUTES)` grows by the routes landed (`history_plan`, and
    `history_check` if 10-C3 lands it first), `history_check` joins the
    `decide` set.
  - **The frozen inference baselines.** `tests/inference_baseline.py:414`-`420`
    observes every task in `sorted(routing.TASK_ROUTE)`;
    `test_inference_resolve.py:40`-`49` reads
    `BASELINE[state]["tasks"][NEW_TASKS.get(task, task)]`; both JSON fixtures
    are never regenerated, and `without_new_tasks`
    (`test_inference_equivalence.py:53`-`74`, and its `_c` twin at `:31`-`51`)
    requires a new task to resolve identically to a recorded sibling. A
    `NO_LEGACY` task has none: on a format-1 state that pins `route_summary`,
    `rolling-summary` follows the pin while `history-query-plan` resolves to
    Fast by design. The mechanism: a `NO_LEGACY_TASKS` set, derived from
    `routing.ROUTES` (every task of a `NO_LEGACY` route), that
    `without_new_tasks` drops from both cells and `_recorded` skips, plus one
    test asserting that each such task resolves, in every baseline state at
    both scopes, exactly as its route's `default_role` resolves for an
    unpinned route (`_role_cell(state, role)`, a helper beside `_task`). Derived
    rather than listed, so 01g's `tool-decision` and 02's routes inherit it.
    Whichever spec lands the first `NO_LEGACY` route lands it; it belongs to
    the checklist's shared `NO_LEGACY` structure.
- **Metering and costs.** Every call files a ledger row under its task.
  Planning is never charged to an unrelated post; its rows carry `post`. An
  unpriced Fast or Decision model files unpriced rows (5.3). A
  native decision row is never modelled (`CLAUDE.md`), which applies to the
  check if its Decision model is native-only.
- **Faking the LLM.** Tests use `backend/tests/llm_fakes.py` with cassette
  entries for the planner and the check in `backend/tests/fixtures/llm/`;
  `test_llm_fakes.py` renders `templates/history_plan/` and
  `templates/history_check/` to prove the matchers match.
- **Templates.** Both directories are documented in `templates/README.md`;
  `scripts/verify_templates.py` gains the builders.
- **Regex view.** The planner and the check read the turn window and excerpts
  09 already took through the prompt view; `test_regex_prompt_guard.py`'s
  scan of `store/history/` (09) covers `plan.py` and `sufficiency.py`, pinned
  by name, because each renders a prompt from transcript text built in another
  function.
- **Locks and revision.** No campaign lock is taken and nothing campaign-scoped
  is written, so no revision stamp. The guard of 09 section 10.1 covers
  `plan_and_repair` (never called inside a campaign-lock hold).
- **Detached runs.** Planning is part of the turn phase 09 defines; on the
  round path it runs in the run's driver and is cancelled with it.
- **Privacy.** Log lines carry ids and counts, never plan text. The evals are
  synthetic. Model-written questions appear only in the inspector row and the
  turn's capture, the same sharing boundary as the reply itself.
- **Android and pydantic.** Pure Python; no new request model (settings
  travel through `PUT /config`).
- **Imports.** `store/history/plan.py` and `sufficiency.py` bind submodules as
  module objects and import nothing from `routes/`.

## 14. Tests and acceptance

**Store:**

- `parse`: every recorded shape in section 11 maps to its documented `Plan` or
  `None`; an invented handle is dropped and counted; every parse-side bound is
  enforced; `needs_history: false` with empty lists is `declined`, not `None`;
  it never raises on arbitrary input (a property test over random strings and
  JSON values).
- `schemas.check(plan.schema_for(offered, depth))` passes with zero, one and
  many offered records, and the schema has no `enum` when none is offered.
- The built prompt contains `schemas.render(schema)` byte-for-byte.
- `query_of` produces a `Query` whose subjects are refs, whose scopes are
  `RELATIONS` when the plan named none, and whose texts do not repeat the turn
  window.
- `build_messages` respects every cap in 5.1, drops from the bottom first,
  never shows a signal, rank or score, never offers an excluded or gm-only
  ref, and renders `P1` as quoted data.
- `sufficiency.item` asks no rationale.

**Routes** (with `llm_fakes` cassettes):

- Off: no planner request, no check, evidence is `E0`.
- `sufficient`, `exhausted` and `error` coverage: no request (`not_run:<why>`).
- `thin` coverage: one plan, one round, merged; the post's ledger rows include
  `history-query-plan`; an `empty` `E0` gains items (the bound is depth, not
  `E0`'s count).
- Repair: check `insufficient` -> second plan -> merged; check `sufficient` or
  `unknown` -> stop; a redundant repair plan is not run (`repair_redundant`);
  an unusable repair plan gives `unrepaired`.
- `final_check`: after a repair, an `insufficient` check sets
  `cheap_retrieval_failed`; without `final_check`, `sufficiency` is
  `not_asked` and the flag is false.
- The trace's stable fields: `trigger` for each of `thin`, `empty`, `forced`
  and not started; `sufficiency_source` is `answer`, `escalated` (an
  abstention answered on 01d's hop) and `unknown`; `e0` and each `rounds`
  entry carry the questions and terms tried and the selected scene keys and
  evidence ids, matching the merged evidence; a snapshot test pins the field
  names and their JSON projection.
- Resolution runs in a worker (a spy on the loop thread sees no config read).
- Unresolvable planner route: skipped with reason; the turn proceeds.
- Planner timeout: `unusable`, the connection is noted as failing through
  `_noting`, turn proceeds, `E0` returned.
- Deadline: a step started with little time left is bounded by `remaining`,
  and the phase never exceeds 09's turn-phase deadline.
- Escalation (with 01d): an abstention is escalated once; a second abstention
  is `unknown`.
- Step cap: a forced extra step is refused by `MAX_PLAN_STEPS`.
- A group round with several narrator contributions plans once.
- `test_reasons_never_reach_the_prompt` with a plan in play.

**Routing:** the `NO_LEGACY` pins of section 13; the two routes' operations
and default roles.

**Evals:** offline planning arms pass their validation cases; the control
cases, the young campaign included, do not trigger under the free gate.

**Acceptance (the draft's, restated):**

1. Planner output is bounded, validated structured data naming only offered
   records.
2. It runs only on thin or empty coverage, or when forced.
3. At most one repair hop exists, no plan follows it, and a check follows it
   only when 12's RP mode asks.
4. Every failure is fail-soft and returns at worst what 09 alone found.
5. The evals report recall gain against added calls and cost, and the default
   stays off until they justify it.

## 15. Non-goals

- Tools, loops, or a planner that reads files (12).
- Planning for actor-scoped composes (11 lifts 09's blanking first).
- A planner on the opener, mechanics continuations or replay (09 open
  question 5 applies).
- A UI control to force planning on one turn. `force` exists for it; the
  control is a later, small change.
- Caching plans across turns. A plan answers one turn's window.

## 16. Open questions

1. **`NO_LEGACY` versus amending the frozen legacy tests.** The sentinel keeps
   `LEGACY_ROUTES` and its tests exactly as they are and makes the new routes
   resolve to their default role on a format-1 store. *Recommendation:* the
   sentinel; it is one constant and two reader guards, and it is reusable by
   every route the roadmap adds later. Now a recorded shared structure in
   `ROADMAP-CHECKLIST.md` (01g, 02-C5, 09, 10).
2. **One decide route for rerank and sufficiency (resolved).** 02-C5b
   provides `history_check`; 09's rerank and 10-C3 each add their task, and
   the first call site to land brings the route.
3. **Skip planning when the campaign is over its budget?** Budgets only warn
   today. *Recommendation:* yes, for planning only (it is optional spend the
   player did not ask for), reading the maintained aggregate the shell already
   reads (`store/usage_rollup.py`), never the full ledger; the trace says
   `skipped:budget`.
4. **Which resolver does 01d escalate the check to?** *Recommendation:*
   Primary, declared in 01d-C1's policy for `history-sufficiency`; 01d owns the
   mechanism, this spec only asks for one hop.
5. **A hard output cap on `generate` (resolved).** 01f-C1 adds a per-call
   `max_tokens`; the planner uses `PLAN_MAX_TOKENS`.
6. **Capture the planner's request?** 01b captures decide sites only.
   *Recommendation:* not in 10; the parsed plan in the breakdown is what a
   reader needs, and the request would duplicate the turn window in every
   capture. Revisit if evals show plan-quality problems that need the prompt.
7. **Gate the *first* plan with the decide check as well?** That would spend a
   decide call on every thin turn to save some planner calls.
   *Recommendation:* no; the free coverage gate first, the check only before
   the repair hop, and let the evals' false-trigger rate say whether the first
   gate needs help.

## 17. Review record

Substitute adversarial review of 2026-10-09, folded in. Codex gate pending.

| Item | Disposition |
|---|---|
| B1 schema outside 01f's portable subset | Fixed: shapes only, no `enum` when nothing is offered, bounds in `parse`, `schemas.render` in the prompt, `schemas.check` test (5.2, 5.4) |
| B2 frozen inference baselines and registry assertions | Fixed: `NO_LEGACY_TASKS` mechanism and the edited `test_routing.py` assertions named (13) |
| B3 "no more items than E0" | Fixed: at most `history_recall_depth`, same ceiling (6, 10-C2) |
| B4 gate fires when nothing more exists | Fixed: gate on `thin`/`empty` only; 09's `exhausted` and `error` never plan; young-campaign control (3, 11) |
| S1 phase deadline not a bound | Fixed: one turn-phase deadline shared with 09, every step `min(own, remaining)`, worst case stated (3, 8) |
| S2 call cap counts steps | Fixed: `MAX_PLAN_STEPS` with the escalation hop inside a step; worst-case rows stated (3) |
| S3 `PLAN_MAX_TOKENS` too small | Fixed: 1024 with a reasoning allowance, non-reasoning preset advised, maximal-plan eval case (5.3, 11) |
| S4 terminal states insufficient for 12 | Fixed: `sufficiency`, `cheap_retrieval_failed`, `unrepaired`, `final_check`, combinations named (8.1) |
| 12's fold-in: trace fields for the RP trigger | Fixed: `PlanTrace` gains `trigger`, `sufficiency_source`, `e0` and per-round `RoundRecord`s (questions, terms, scene keys, and 09-C1's evidence ids) as stable 10-C2 fields, with a snapshot test (8.1, 10-C2, 14) |
| S5 resolving on the loop | Fixed: `run_in_threadpool` around `_soft_resolved` (5.3, 7.1) |
| S6 missing `_noting` and parse placement | Fixed: `draft_completion` pattern (5.3) |
| S7 two `history_check` definitions; policy row | Fixed: 02-C5b's entry is the definition, 10 appends its task; 01d-C1 row given (4.1) |
| S8 planning per contribution | Fixed: once per player post via 09's per-round retrieval (3) |
| M1 P1 text into the repair prompt | Fixed: rendered as quoted data after parse's normalisation (5.1) |
| M2 empty scopes | Fixed: `query_of` passes `RELATIONS` (5.4) |
| M3 unpriced rows | Fixed: stated, settings copy says so (5.3) |
| M4 no downstream metric | Fixed: live arms add 09's consistency grader (11) |
| M5 `NO_LEGACY` truthiness | Fixed: explicit `is NO_LEGACY`, `str | None` noted for mypy (4.1) |
| M6 excluded refs offered to the planner | Fixed: 09's filtered seeds only (5.1) |

Slices added (3 slices), before the Contract section. The 01d-C2 citations now name 01d-C2a and 01d-C2b, the checklist's split.

Coordinator inputs applied in the same pass: the schema fits 01f-C3; 10 has
no 01i edge; 12's RP trigger is the stable `cheap_retrieval_failed` field with
`e0` and `rounds` (8.1); `perspective` keeps secrets out of the planner's input (5.1,
10-C1).
