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
| 01d-C1 task policy; 01d-C2 one-hop escalation | 01d | Declaring that `history-sufficiency` may escalate, and escalating a non-answer or low margin once (section 7.3) | Soft: without it a non-answer is `unknown` and the repair hop does not run |
| 01a-C3 comparison table | 01a | Planning arms against 09's hybrid arm in one table | Soft |
| 02-C5b history relevance kit, the shared `history_check` route | 02 | The decide route `history-sufficiency` lands on, shared with 09's rerank | Soft |
| 01b-C1 decision capture at every decide site | 01b | Capturing the sufficiency decision to the prompt log | Soft: without it the decision is visible only in the history row |

## Required by

| Contract (provided here) | Consumer | Hard or soft | What the consumer uses it for |
|---|---|---|---|
| 10-C1 `history_plan` route, `Plan`, `plan.build_messages`, `plan.parse`, taking a `perspective` | 11 | Soft | Planning an actor's history questions once 11 lifts 09's NPC blanking, with the planner shown only what that actor may know |
| 10-C2 the bounded repair hop and its `PlanTrace` | 12 | Hard for RP mode | `PlanTrace.cheap_retrieval_failed` and `PlanTrace.tried`: the repair hop ran and 10-C3 still judged the evidence insufficient, and what was tried (section 8.1) |
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
`refused` and a `None` are answers. 01d-C2 is the one declared exception, a
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
if not repair_enabled:        return finish(E1)             (final check below if asked)
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

Two new routes, both campaign-scoped:

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
| What was tried (repair only) | the previous plan's questions and terms | `P1` |
| Coverage | the verdict word and the tiers tried | `E0.coverage` |

Total input is clipped to `PLAN_INPUT_BYTES` (6000) by dropping from the
bottom of this table upward, never the turn window. Signals, ranks and scores
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

```json
{"type": "object", "additionalProperties": false,
 "required": ["needs_history", "subjects", "questions", "terms", "scopes", "max_scenes"],
 "properties": {
   "needs_history": {"type": "boolean"},
   "subjects":  {"type": "array", "maxItems": 6, "items": {"type": "string", "enum": ["s1", "..."]}},
   "questions": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 200}},
   "terms":     {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 60}},
   "scopes":    {"type": "array", "items": {"type": "string",
                 "enum": ["shared_cast", "actor", "location", "thread", "relationship", "group"]}},
   "max_scenes": {"type": "integer", "minimum": 1, "maximum": 4}}}
```

`scopes` are 09's structural relations by name (09 section 5.2), not the
draft's `actor_history`/`shared_scenes`/`thread_history`: one vocabulary, so a
plan is a `Query` without translation. `max_scenes`' maximum is the campaign's
`history_recall_depth`, rendered per call.

### 5.3 The call

```python
resolved, why, kind = _soft_resolved(
    lambda: require_inference("history-query-plan", cid))       # task literal at the call site
with store.usage.meter("history-query-plan", campaign=cid, scene=sid, post=turn_index) as m:
    text = await _bounded_call(
        operations.generate("history-query-plan", messages, client=client,
                            resolved=resolved, usage=m.usage, schema=plan.SCHEMA_FOR(offered),
                            stream=False),
        ceiling=PLAN_CEILING)
# with 01f-C1's per-call cap: max_tokens=PLAN_MAX_TOKENS (only ever lowers the preset's)
```

- `_soft_resolved` (`routes/common.py:1519`): a route that cannot resolve (no
  key, `incapable`) skips planning with a reason in the trace; the turn is
  untouched. The lambda keeps the task a literal for `test_routing_guard.py`.
- `_bounded_call` with an explicit `PLAN_CEILING` (8 s), never the
  `llm_call_budget` default, whose `0` means "no ceiling at all"
  (`routes/common.py:544`-`570`): a turn-path call must not be able to hold
  the reply for as long as a provider likes.
- The meter is the call site's own (`test_usage_guard.py`), and `post=` puts
  the planner's cost on the post it served, beside the reply's
  (`CLAUDE.md`, attribution per player post).
- `schema=` reaches the provider's structured mode through 01f-C1, with the
  schema also in the prompt (01f-C1's rule), and 01f-C1's per-call
  `max_tokens` caps the reply at `PLAN_MAX_TOKENS` (256: the schema's own
  bounds fit well inside it; tuned later), never raising a preset's cap.

### 5.4 Tolerant parse and validation

`plan.parse(text, offered: Offered, depth: int) -> Plan | None`, pure and
total (it never raises):

1. **Object.** `json.loads` of the reply; failing that, the first balanced
   `{...}` in it, fenced or not. No object: `None`, trace `unreadable`.
2. **Fields, independently.** A field that is missing or the wrong type takes
   its empty default rather than voiding the plan, the per-section tolerance
   `continuity/doc.py` uses for a hand-edited file:
   - `subjects`: handles mapped to refs; an unknown handle is dropped and
     counted (`dropped.subjects`).
   - `questions`: strings stripped, clipped to 200 characters at a word
     boundary, deduplicated case-insensitively, at most 3; one with no letters
     is dropped.
   - `terms`: the same, 60 characters, at most 8, and a term that is a
     stopword or a single character is dropped.
   - `scopes`: intersected with 09's `RELATIONS`; empty means all.
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
`terms = terms + subject names`, `subjects`, `scopes`, `max_scenes`,
`perspective`, `turn_index`. The turn window is not repeated: `E0` already
retrieved on it, and `merge` keeps its signals.

## 6. Merging rounds without growing the prompt

Each planned round goes through 09-C1 with `rerank=None` and is merged into
the evidence so far with `history.merge(prev, round, ceiling=...)`:

- Candidates are matched by scene identity, so a scene found by both rounds
  carries both rounds' signals, and its agreement count can rise (09's
  coverage counts it).
- The merged order is RRF over the union of every round's ranked lists, so a
  scene the turn window and a planned question both found ranks above one
  either found alone. No round's order is privileged; that would be a fused
  confidence by another name.
- The selection is still `history_recall_depth` items under the same ceiling
  (09 section 9.2). Planning can change *which* scenes the section carries,
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
`_soft_resolved`, its `around` bounding it by `CHECK_CEILING`.

### 7.2 Mapping the answer

| Decide outcome | Verdict |
|---|---|
| `True`, read | `sufficient` |
| `False`, read | `insufficient` |
| abstained, native `refused`, unreadable, failed call, unresolvable route, ceiling hit | escalate (7.3), else `unknown` |

`unknown` stops planning, exactly like `sufficient`. Spending the repair hop on
a non-answer would turn "the checker could not say" into "go and spend more",
which is the opposite of the cost rule.

### 7.3 Escalation through 01d-C2

When 01d has landed, `history-sufficiency` declares in its 01d-C1 policy that
escalation is allowed, and a non-answer (abstention, native `refused`) or a
low margin (where the backend reports one: a native distribution, never a
fabricated one) is handed to 01d-C2's helper, which asks the declared next
resolver once, metered and captured. Its answer maps through 7.2 again; a
second non-answer is `unknown`. A failed *call* is not escalated by 01d: it
already moved down the decide chain to its fallback, which is the chain's
job. Which resolver is next is 01d's to define; the recommendation is the
Primary role, the strongest model the user has already chosen to pay for
(open question 4).

Before 01d lands, a non-answer is `unknown` and the repair hop does not run.

## 8. The repair hop (10-C2)

The repair hop is a second planner call with `tried=P1`, gated on an
`insufficient` verdict, and it is the last model call the phase makes.

- **Inputs** as 5.1, with `E1`'s items as the evidence found and `P1`'s
  questions and terms as what was tried, and the instruction to ask for what
  is still missing rather than restate.
- **Novelty.** `P2.adds_nothing_to(P1)` is true when `P2`'s subjects, questions
  and terms are each a subset of `P1`'s after normalisation. Such a plan
  retrieves nothing new, so it is not run.
- **No check after it.** `E2` goes to the prompt as merged. A second
  sufficiency call would invite a third plan, and the bound is the point.
- **Deadline.** The whole planning phase, both plans, the check and its
  escalation, and the planned retrievals, runs under `PLAN_PHASE_DEADLINE`
  (20 s), started when planning starts. A step that would start after it has
  passed is not started; what is merged so far is returned. The constant is
  justified by the planner and check ceilings plus two embed round trips, and
  is tuned against the eval suite's latency column.

`PlanTrace` records every step: `trigger` (`thin`, `empty`, `forced`), each
call's task, outcome and milliseconds, the parsed plans (refs shown as names),
the validation drops, the verdict and whether it escalated, and the terminal
state:

| Terminal state | Meaning |
|---|---|
| `not_run` | mode off, or coverage sufficient |
| `skipped:<why>` | unresolvable route, deadline, call cap, budget (open question 3) |
| `declined` | the planner said no history is needed |
| `unusable` | the planner's reply was unreadable or empty |
| `planned` | one round, no repair (repair off, check sufficient or unknown) |
| `repaired` | the repair hop ran |
| `repair_redundant` | the repair plan added nothing |

12 reads `planned` with an `insufficient` check that could not repair, and
`repaired`, as "cheap retrieval failed" (10-C2's consumer row).

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

Both join `config._CONFIG_KEYS` (`store/config.py:219`) and, under 01s, the
Context pane beside 09's keys. `history_plan` has no effect while
`history_recall_depth` is 0. `force` is a parameter of `gather`, used by the
evals and by a later explicit control, never a stored setting.

The controls, each with what it bounds:

| Control | Bounds |
|---|---|
| The free gate (09-C2 coverage) | How often planning runs at all |
| `PLAN_INPUT_BYTES`, the per-part caps in 5.1 | Planner input |
| Schema `maxItems`/`maxLength`, validation clipping, `PLAN_MAX_TOKENS` (01f-C1), `PLAN_CEILING` | Planner output and its wall time |
| `CHECK_CEILING`, one item, no rationale | The check |
| `MAX_PLAN_CALLS`, the novelty rule, no check after repair | Calls per turn |
| `PLAN_PHASE_DEADLINE` | The whole phase's wall time |
| 09's one embed request per round, one rerank per turn | Retrieval cost added by planning |
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
plan validity rate and drops; model calls, tokens, wall time and cost (three
money columns, never added) added per turn; terminal-state counts.

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
  client, ceiling, force=False) -> tuple[Evidence, PlanTrace]`, called from
  09's `gather`.
- **Guarantees**: at most `MAX_PLAN_CALLS` model calls, at most one repair
  hop, no check after it, `PLAN_PHASE_DEADLINE` on the whole; each call
  metered under its task with campaign, scene and post; the returned evidence
  has no more items and no larger ceiling than `E0`'s; the worst case returns
  `E0` unchanged.
- **Escalation**: only the sufficiency check escalates, through 01d-C2, one
  hop, and only for a non-answer or a reported low margin.
- **Failure**: never raises into the turn; the trace says where it stopped.

### 10-C3: the evidence-sufficiency predicate (new; split out of 10-C2)

- **Route**: `history_check` (decide, Decision role, `NO_LEGACY`), task
  `history-sufficiency`, landing with its call site.
- **Store**: `sufficiency.item(turn_window, evidence) -> decisions.Item`.
- **Route layer**: `sufficient(...) -> Literal["sufficient", "insufficient",
  "unknown"]`, mapping per section 7.2, escalation per 7.3.
- **Guarantees**: no rationale requested or consumed; a non-answer is never
  `insufficient`.

## 13. Interaction with repo rules

- **Routing guard.** Both task literals appear at their call sites through
  `require_inference` thunks; each route lands with its call site; the
  decide route is `operation="decide"` and uses `operations.decide`
  (`test_operation_guard.py`). `NO_LEGACY` is new vocabulary in
  `store/routing.py`, which must stay a pure leaf: a sentinel constant keeps
  it one.
- **Frozen legacy tests** stay green unmodified (section 4.1). A test pins
  that a `NO_LEGACY` route never appears in `LEGACY_ROUTES`, `CONFIG_KEYS`,
  `PRESET_CONFIG_KEYS` or `routes_for(...)`, and that a format-1 store
  resolves it to its default role without reading any legacy key
  (`tests.inference_fixtures.legacy_store()`).
- **Metering and costs.** Every call files a ledger row under its task.
  Planning is never charged to an unrelated post; its rows carry `post`. A
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
  `None`; an invented handle is dropped and counted; `needs_history: false`
  with empty lists is `declined`, not `None`; it never raises on arbitrary
  input (a property test over random strings and JSON values).
- `schema_for` renders the offered handles and the depth bound.
- `query_of` produces a `Query` whose subjects are refs and whose texts do not
  repeat the turn window.
- `build_messages` respects every cap in 5.1, drops from the bottom first, and
  never shows a signal, rank or score.
- `sufficiency.item` asks no rationale.

**Routes** (with `llm_fakes` cassettes):

- Off: no planner request, no check, evidence is `E0`.
- `sufficient` coverage: no request.
- `thin` coverage: one plan, one round, merged; the post's ledger rows include
  `history-query-plan`.
- Repair: check `insufficient` -> second plan -> merged; check `sufficient` or
  `unknown` -> stop; a redundant repair plan is not run.
- Unresolvable planner route: skipped with reason; the turn proceeds.
- Planner timeout at `PLAN_CEILING`: `unusable`, turn proceeds, `E0` returned.
- Escalation (with 01d): an abstention is escalated once; a second abstention
  is `unknown`.
- Call cap: a forced extra step is refused by `MAX_PLAN_CALLS`.
- The merged evidence never exceeds `E0`'s depth or ceiling.
- `test_reasons_never_reach_the_prompt` with a plan in play.

**Routing:** the `NO_LEGACY` pins of section 13; the two routes' operations
and default roles.

**Evals:** offline planning arms pass their validation cases; the control
cases do not trigger under the free gate.

**Acceptance (the draft's, restated):**

1. Planner output is bounded, validated structured data naming only offered
   records.
2. It runs only on thin or empty coverage, or when forced.
3. At most one repair hop exists, and nothing checks after it.
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
