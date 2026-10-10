# 02. Decision integration: what slices F–H left, and the play-facing uses

**Status:** Draft — spec gate (substitute review) folded in; Codex gate pending.
**Date:** 2026-10-09
**Roadmap:** 02 in `ROADMAP-CHECKLIST.md`. Lane: Decision (01a, 01b → 01c,
01d → **02** → 11; 13 and 12 consume it too).
**Baseline:** `main` at `35c1fb7`.
**Reconciles:** the bundle draft `02-decision-integration.md` (2026-10-06,
written against `7f80c42`), which slices F, G and H of
`2026-10-07-inference-backend-refactor-design.md` (01) have since largely
absorbed. Extends, without superseding,
`2026-10-05-group-play-speaker-order-design.md` (the speaker pick and the
round planner) and `2026-09-11-character-turns.md` (one call per
contribution, handoffs, frozen snapshots).

> Implementation rule: re-read the code and every PR landed on this subsystem
> since the baseline before planning. The decide operation, the round engine
> in `routes/character_turns.py` and `evals/` all move often.

## Depends on

This matches the checklist's edge: 02 ← 01a-C1/C2/C3 (H for every play
gate), 01b-C1/C2 (H for C2b/C3/C4/C5), 01c-C1..C4 (H for C2 sampling),
01d-C1..C3 (H for C5a); 01g-C1..C6 (H for C4 only); 01e-C1..C3 (S). Landed 01
is the baseline.

| Contract | Provided by | What this spec uses it for | Hard or soft |
|---|---|---|---|
| 01 (landed) | 01, slices F–I | `inference.decide`, `decisions.Item/Answer/Decision`, the route registry, the Decision role, the native chain, `--gate` and `--decide-backend`. Section 1 is the record of it. | Baseline |
| 01a-C1 | 01a | Wall time, tokens and the three money columns per call, never added together. A play case summed across its tasks (`turn-plan`, then `chat`), which the play gate (section 4) reports per post. | H for every play gate |
| 01a-C2 | 01a | Live evals metered inside a throwaway home and stamped `scope: "eval"`, so a play-gate run never reaches a library's ledger. | H for every play gate |
| 01a-C3 | 01a | `--repeat` and `--compare`: one table across configurations (feature off vs on; the reasoning matrix of section 7.5). | H for every play gate |
| 01b-C1, 01b-C2 | 01b | A prompt-log capture at each new decide site (intent, plan, tool, relevance, epistemic), off the decide path. | H for C2b, C3, C4, C5 |
| 01c-C1 | 01c | The recorded distribution policy, and the `reports_distribution(resolved)` predicate that 5.6's settings text asks. | H for C2 sampling |
| 01c-C2 | 01c | The one sampler (`draws.py`), `(distribution, seed) → selected`. | H for C2 sampling |
| 01c-C3 | 01c | The replay record, persisted beside the round's speaker and the response's intent. | H for C2 sampling |
| 01c-C4 | 01c | An abstention, a refusal or a missing distribution is never sampled. Section 5 builds the speaker invariants on it. | H for C2 sampling |
| 01d-C1 | 01d | `routing.TaskPolicy` (shared with 01c's `samples`), which refuses `samples` together with `low_margin` escalation. Section 10 states the row each new task needs. | H for C5a; needed to declare every new task |
| 01d-C2a | 01d | Trigger evaluation with its optional answer filter, so epistemic access escalates only permissive answers (9.2). | H for C5a |
| 01d-C2b | 01d | The one-hop escalation, used by epistemic access only. | H for C5a |
| 01d-C3 | 01d | Per-task, per-endpoint-kind thresholds for that escalation. | H for C5a |
| 01e-C1, 01e-C2 | 01e | `Rank` and a finer `Score`, which history relevance (C5b) switches to when they land. | S |
| 01e-C3 (C3a/C3b) | 01e | `Joint` choice, which would let one question pick the speaker and the stance together (section 7.4). | S |
| 01g-C1..C5 | 01g | The `tools` capability, the loop primitive, its metering, its run budget and the decide tool. C4 is the play-side policy of 01g-C5. | H for C4 only |
| 01g-C6 | 01g | The final loop turn streams, and the loop can decline a tool call after visible text (8.2, 8.6). | H for C4 only |

Nothing in C1, C2, C3 or C5 waits on 01g. C4 is a separate, last slice.

## Required by

Rebuilt from the checklist's edges.

| Contract (provided here) | Consumer | Edge | What the consumer uses it for |
|---|---|---|---|
| 02-C2 (C2a, C2b) | 13 (13-C3) | S | The pattern for a sampled choice among legal options in play (eligibility decided by code, the answer sampled by Grimoire, the record kept with the outcome), and a Decision-chosen intent an NPC Action choice can be conditioned on. |
| 02-C3 | 13 (13-C3) | S (via the 02-C2 seam) | The plan's `extra` slot, so a legal-Action item rides the same `decide()` call rather than adding one (7.3). |
| 02-C5a | 11 (11-C1, Decision stage) | H for the Decision stage | The `epistemic` route, its task, item builder, mapping and fail-closed rule. |
| 02-C5b | 09 | S | The `history_check` route and the `history-rerank` task, item builder and mapping, with ungraded kept distinct from irrelevant. |
| 02-C5b | 10 (10-C3) | S | The `history_check` route, to which 10 adds its `history-sufficiency` task. |

02-C1 and 02-C6 have no edge of their own. 02-C1 is the baseline every
Decision-lane spec reads. 02-C6 is the rule set that 13's NPC seam and any
later play decision cite. 02-C4 has no consumer edge: 12 uses 01g-C5
directly.

**Contract IDs** match the checklist: 02-C1, C2a, C2b, C3, C4, C5a, C5b, C6.

---

## 1. Current state (reconciled against main) — 02-C1

This section is contract 02-C1. It records what the bundle draft asked for,
what 01's slices F–H landed in its place, and what is still open. Where a
guard or suite holds a landed behaviour, the row names it. Nothing checks
the `file:line` citations themselves except review, so a PR that moves a
landed decide site updates 1.2 in the same change.

### 1.1 The draft in one paragraph

The draft proposed a provider-neutral `decide()` with Predicate, Choice,
Score and Rank questions. It proposed native adapters for OpenAI and
OpenRouter, with structured generation as the fallback. Probabilities would
be kept, never fabricated, and sampling would be Grimoire's. The pilot was
absorb's continuity identity, run first in **shadow mode** beside the old
generative resolver, with reconcile as a second benchmark. Escalation would
send ambiguous items to the generative resolver. Later uses were listed as
future: retrieval relevance, epistemic classification, the next speaker, turn
intent, a pre-generation turn plan, NPC Action choice, faction and offscreen
behaviour, and Decision as a tool. It said scene-break was not worth
converting.

### 1.2 What landed

| Draft item | Landed as | Where | How it differs from the draft |
|---|---|---|---|
| Provider-neutral `decide()` | `inference.decide` over a chain of stages; `run_stages` | `inference.py:684`, `:595`, `stages` at `:147` | One meter per facade **call**: a structured chunk, each prompt-only re-send of a chunk whose attempt refused the schema (`_once`, `inference.py:259-286`; `_ask`, `:289-318`), or a native item. The chain moves on only on a **failed call**, never on an answer (`run_stages` docstring, `inference.py:602-605`). |
| Question vocabulary | `Predicate`, `Choice(allow_none)`, `Score(levels 2–10)`; several questions per `Item` | `decisions.py:181`, `:189`, `:200`, `:212`; level bounds `:94` | **No `Rank`.** That is now 01e-C1. |
| Normalised result | `Decision` → `ItemResult` → `Answer` with `reason`, `detail`, `probability`, `distribution` | `decisions.py:221`, `:258`, `:277` | No `profile_id`. What answered is `provider`, `model` and `served` (`decisions.py:284-297`). `backend` is per item. |
| No fabricated probabilities | `Answer.probability` and `.distribution` are only what a backend reported | `decisions.py:227-229`; native reading `native_answer` at `:866` | Only native reports any. A structured answer carries none. |
| Native adapters | OpenRouter `/api/alpha/decisions`, OpenAI `{base_url}/decisions` | `openrouter.py:40`, `:342`; `openai_compatible.py:426`; adapters `adapters.py:149`, `:195` | Native serves **only a model that cannot generate** (`resolve.native_only`, `store/inference/resolve.py:922`; `decision_mode` at `:931`). A model that generates stays structured until native wins on evals (01 section 16). |
| Structured fallback | `_once` sends `client.complete(..., schema=)` over `structured_messages`, with the schema always in the prompt and provider structured mode on each attempt flagged for it | `_once` at `inference.py:259-286`; `structured_messages` at `:119`; `_flag_structured` at `resolve.py:955` | Chunks of eight items (`decisions.MAX_ITEMS_PER_CALL`, `decisions.py:101`). Not `generate(schema=)`: a generate resolution's targets are never flagged structured, so that path sends no structured mode (01f). |
| Absorb continuity identity on Decision | `continuity-identity` decides one item per examined row | `routes/scenes.py:2105` (`_resolve_identity`), call at `:2136`; builder `store/continuity/identity.py:729` | **No shadow mode.** It switched by default in slice G, behind the offline gate (below), on the `continuity` route with `default_role="decision"` (`store/routing.py:101-105`). A row the reply never reached stays `unchecked`; there is no generative resolver left to fall back to. A native distribution split across `existing:<id>` options is regrouped before reading (`identity.py:769`, `decisions.regrouped` at `decisions.py:952`). |
| Reconcile on Decision | `continuity-reconcile` decides one item per candidate | `routes/continuity.py:651` (`_adjudicate`), call at `:685` | Shipped with identity in slice G, not left as a benchmark. An unreached candidate is `unanswered` and asked again next sweep. |
| Speaker pick | `response-selector` decides one `Choice` over the round's eligible refs and `grimoire`, null allowed | `routes/character_turns.py:728` (`_select`), call at `:752`; item `store/response_protocol.py:102`; mapping `:125` | On its own `speaker` route (`routing.py:88-91`), Decision role. Answer taken as given: **argmax, never sampled.** |
| Scene-break | `scene-break` decides a verdict; the title is a separate `generate` only after a YES lands | `routes/scenes.py:4150`, `:4181`, call at `:4217` | The draft said not to prioritise it. Slice F converted it anyway, because it was a hand-parsed closed question. |
| Voice drift | `voice-drift` decides per NPC; a native verdict with no note is usable | `routes/scenes.py:2516`, call at `:2673`; `store/voice_drift.py:336`, `:359` | Not in the draft. |
| Eval gate | `--gate`: legacy parse vs decide parse on recorded corpora, offline, in `make check` | `evals/gate.py` (`GATES` at `:857`); corpora `evals/gate/{scene-break,voice-drift,speaker,continuity-identity,continuity-reconcile}.json`; `backend/tests/test_decide_gate.py` | Replaces shadow mode. It compares **parsers**, so it cannot gate a call site that had no parser (section 4). |
| Live backend comparison | `--live --decide-backend chain\|native\|structured` | `evals/run.py:69-118`; `evals/runner.py:233`, `:302` | Grades correctness only. No latency, tokens or cost: that is 01a. A live run's rows land in a throwaway `GRIMOIRE_HOME`, so they are effectively unmetered. |
| Capture | The speaker pick's prompt and outcome are captured | `routes/character_turns.py:622` (`OUTCOME_SECTION_ID`), `_capture` at `:625` | **Only the speaker.** Scene-break, voice drift and the continuity pair capture nothing (01 section 9.4). Every other site is 01b. |
| Ledger | `decision_mode` stamped per call; a native row is never modelled | `store/usage.py:896` | As the draft asked. |
| Guards | A decide task must be on a decide route and be decided by a call site; a Decision-role route must decide | `backend/tests/test_operation_guard.py:988`, `:996`, `:1002`; `test_routing_guard.py:659` | **A route cannot land before its call site.** This shapes C5 (section 9). |

### 1.3 What did not land

| Draft item | State on `main` | Where it goes now |
|---|---|---|
| Shadow mode (old resolver and Decision side by side, compared) | **Superseded.** F–H switched each site after `--gate` showed the decide parse equals or beats the legacy parse on recorded shapes, and `--live --decide-backend` compares backends on one model. No product path runs two resolvers or charges a user twice for telemetry. | Not revived. Section 4's play gate is the equivalent for sites that have no legacy parser, and it runs only in evals. |
| Sampling (Grimoire draws from a distribution) | Not landed. Every caller takes the answer. Group play's `random.Random` (`character_turns.py:48`, `group_play.py:196`) is talkativeness and shuffle, not a decision. | 01c (policy, sampler, record); C2a and C2b here. |
| Escalation (ambiguous → another resolver) | Not landed. There is no margin rule anywhere. The chain's fallback is for failed calls only. | 01d; C5a here is its first play-adjacent user. Continuity's `uncertain` already escalates to the human review (section 10). |
| Decision as a tool | Not landed. There is no tool calling anywhere: `claude_agent.py:163` passes `allowed_tools=[], max_turns=1`, and `capabilities.NAMES` (`store/inference/capabilities.py:54`) has no `tools`. | 01g; C4 here. |
| Retrieval relevance | Not landed. Semantic recall is embedding similarity only (`store/context/semantic.py`), and historical retrieval does not exist yet. | 09 calls it; C5b defines it. |
| Epistemic classification | Not landed as a decision. The nearest thing is the in-call `perception_rider` (`store/config.py:493`, `templates/scene/response_actor.j2`), where the writer sorts its own evidence inside its generation. | 11 calls it; C5a defines it. The rider is untouched. |
| Turn intent | Not landed. Nothing chooses how a character answers. Only who answers is chosen. | C2b. |
| Pre-generation turn plan | Not landed. | C3. |
| NPC/monster Action choice | Not landed. Mechanics II Actions do not exist. | 13 (13-C3), using C2a's pattern and C3's slot. |
| Faction and offscreen behaviour | Not landed. An offscreen scene is a director turn (`store/context/assemble.py:1700`). | Not in 02. It follows 13's Actions (non-goal, section 15). |
| `Rank` | Not landed. | 01e-C1. |

### 1.4 What the landed code fixes, and the rest of this spec keeps

These are behaviours of `main` that section 3 turns into rules. Each
play-facing design below is checked against them.

1. **One generation per contribution.** A contribution is one streamed
   `generate` on the scene route (`_round_frames`, `character_turns.py:1014`;
   the meter at `:1053-1060`). No decision's output is ever shown or stored
   in the transcript, and no decide call is read out of a contribution's
   prose. The handoff block (rule 2) is the generation's own control output,
   parsed by the watcher; it is not a decision.
2. **The handoff decides later speakers.** In Directed mode a contribution's
   hidden `handoff` block names the successor, validated by
   `response_protocol.validate_handoff` (`response_protocol.py:31`) against
   the round's pool (`_successor`, `character_turns.py:425`). List, Natural
   and Manual follow the stored plan. No decision replaces either.
3. **The speaker-pick invariants.** These are the ones most easily lost:
   - The pick is asked only for a round with no lead and more than one
     eligible actor (`character_turns.py:747-748`). An explicit choice
     (Respond as, a reply-as chip, a replay) never reaches it.
   - Its options are the round's `eligible` refs plus `grimoire`, null
     allowed (`response_protocol.py:102-122`). Sitting-out, absent and player
     characters are never options (`group_play.available`,
     `group_play.py:125`).
   - **Nothing unreadable is guessed into a speaker.** An explicit null hands
     control back with no issue. An answer naming no option is `INELIGIBLE`.
     Anything else (no object, a refusal, an error, nothing answered) is
     `INVALID_HANDOFF` (`response_protocol.py:125-145`). A request `decide`
     refuses before sending is also `INVALID_HANDOFF`
     (`character_turns.py:756-759`). Either issue ends a Directed chain and
     **returns control to the player**.
   - An `LLMError` from the pick propagates. The meter files it and the round
     fails through `_rescue` (`character_turns.py:990-1000`).
   - The `incapable` 409 is raised **before `post_chat` writes anything**
     (`refuse_an_unanswerable_pick`, `character_turns.py:404`, called from
     `routes/scenes.py:853`), and `start` repeats it as a defence
     (`character_turns.py:162-173`).
   - The pick reads the last `SELECTOR_POSTS` posts in context
     (`response_protocol.py:68`) through the prompt-phase regex view
     (`character_turns.py:463-473`). It never reads another NPC's private
     description.
   - A resumed round (a retry, a roll resume, a recovery) is never re-picked.
     The round record already holds `actor_ref` (`_first_actor`,
     `character_turns.py:882-893`).
4. **Frozen snapshots, with one recompose.** `_prepare` composes the prompt
   under the campaign lock and stores `messages.snapshot()` on the response
   record (`character_turns.py:476-520`). A retry of an interrupted
   contribution and a reroll replay that snapshot and never recompose from
   live state (character-turns spec, "Reviewed implementation decisions").
   **A roll resume is the exception:** `_prepare`'s `pending and appended`
   branch recomposes with the roll result appended and stores the result as
   the response's `resume_snapshot` (`character_turns.py:497-509`). Keep
   writing on a reply split by a roll continues from that resume snapshot
   (`ExtendPlan`, `character_turns.py:1770-1785`).
5. **Every decision on the turn path runs off the loop.** The resolution, the
   scene read, the regex view and the item's templates run in the threadpool,
   and `decide` renders in a worker thread (`_select` docstring,
   `character_turns.py:736-741`).
6. **Attribution.** The pick passes `post` and `round_id`
   (`character_turns.py:753-754`), so its cost sits against the player post
   that caused it (`usage.meter`, `store/usage.py:629`).

---

## 2. Goal, and what is explicitly not the goal

**Goal.** Finish what 01 left: put Decision into play where a bounded choice
is made today by the model's free text, or not made at all, and give 09 and
11 the decide tasks they need. Each play-facing use must:

- ship off by default;
- stay byte-identical to `main` while off;
- never make a turn fail that would have succeeded without it;
- show its cost and latency before anyone is offered the switch (01a);
- declare its fallback and escalation policy (01d).

**Specifically:**

- **C2a:** sample the next speaker from a reported distribution (01c) instead
  of taking the argmax, where a distribution exists. Keep every speaker-pick
  invariant.
- **C2b:** a **turn intent**: one closed question about *how* the assigned
  actor answers (their stance). It is decided before the contribution is
  composed, and sampled where a distribution exists.
- **C3:** a **pre-generation turn plan**: the intent question plus related
  questions about the same contribution, batched as one decide item, one
  call. It comes with an evaluation of the hypothesis that a plan lets the
  Primary run at lower reasoning.
- **C4:** **Decision as a tool** from a contribution, via 01g-C5, with a
  hard per-contribution cap and placement rules that keep the stream and the
  watcher sound.
- **C5:** the `epistemic` and `history_check` decide kits (route,
  task, builder, mapping, templates, eval cases) that 11 and 09 wire in.
- **C6:** the shared rules all of the above keep.

**Not the goal:**

- Re-litigating F–H. The five landed sites keep their behaviour. The only
  change to a landed site is C2a's optional sampling after the speaker pick
  answers.
- Native-first for a model that also generates. That stays 01's later user
  decision (01 section 16), gated on `--decide-backend`.
- Changing who decides legality. Eligibility, presence, sitting out,
  mechanics legality and knowledge access stay deterministic code. Decision
  only chooses among what code allows.
- Letting a decision write prose, rewrite a post, or become a store of truth.
  An intent is advisory and frozen into one prompt. An epistemic
  classification is derived for one turn (section 9 of the 11 draft).

---

## 3. Shared rules for play-facing decisions — 02-C6

Every decision C2–C5 adds to the turn path keeps all of these. 13's NPC
Action choice and 12's tool use cite this section rather than restating it.

### 3.1 What must not change in the creative loop

1. **The contribution is still one streamed `generate` call**, on the scene
   route's resolution, under its own `chat` or `continuation` meter
   (`character_turns.py:1050-1063`). A decision may add a *block* to the
   prompt that call is sent. It never adds a second generation, never joins
   or edits generated text, and never makes the stream non-streamed.
   C4's loop is the one exception, and it is bounded (section 8).
2. **No decision output reaches the transcript.** No new
   `SYNTHETIC_SPEAKERS` line, no stored text. Intents, plans and tool
   decisions live on the round or response record and in the prompt log.
3. **The handoff and the plan keep deciding successors** (1.4 rule 2). C3's
   plan never names a next speaker.
4. **Every speaker-pick invariant in 1.4 rule 3 holds**, with sampling layered
   *after* the answer is read (section 5). The pick stays the only decision
   that may raise `incapable` before a write.
5. **A decision is asked once per contribution, and every later prompt of
   that contribution carries it.** A section a decision adds is part of the
   composed prompt, so it is in the snapshot, and a retry and a reroll reuse
   it. A roll resume recomposes (1.4 rule 4), so `_prepare`'s resume branch
   reads the pending response record first and passes its stored plan,
   `plan=record.get("intent")`, into `_compose`. The resume snapshot then
   carries the same block, and Keep writing inherits it from there. None
   of these asks again.
6. **Off is byte-identical.** With a feature off, nothing is resolved,
   nothing is called, no record key is written, and the composed prompt is
   identical to `main`'s. This is the same discipline as
   `config.speaker_turn_taking` (`store/config.py:471-478`).
7. **Follow-ups are untouched.** The rolling summary, scene-break and
   tracker updates are scheduled exactly as today
   (`streaming._fire_follow_up`, `character_turns.py:1008-1010`).

### 3.2 Soft resolution, and no new pre-write refusals

A new play decision resolves through `_soft_resolved`
(`routes/common.py:1519`), never by raising. A missing key, an `incapable`
Decision model or a route with no connection skips the feature **for that
contribution**. The skip is recorded on the record it would have written
(`"skipped": "<kind>"`) and the contribution proceeds as if the feature were
off. `_soft_resolved` returns `(None, why, kind)` in that case, and the call
site tests for `None` and never calls `decide` with it (`decide` would raise
on `resolved.task`). Only the speaker pick keeps its 409 before `post_chat` writes, because
only the pick is required for a round to have a speaker.

**Why:** a turn the player sent is the request. A feature that can make it
fail before generating would turn an optional improvement into a new way
for play to break. That is the reasoning behind `_soft_inference` for
absorb's secondary phases (`routes/common.py:1545-1556`).

### 3.3 Failure never fails the turn

A failed, timed-out or unreadable play decision (other than the pick) is
recorded and dropped. The meter has already filed the call's `error` row
(CLAUDE.md, "Instrument LLM failures at `usage.Meter.done`"). The
contribution then composes without the block. A decision that answered
`abstained` or `refused` is an answer of "no intent", not a failure, and is
not retried elsewhere (1.2: the chain moves on only on a failed call).

The whole optional pre-generation step is wrapped in `except Exception`:
the soft resolution, reading the actor's card, rendering the templates,
clipping the brief, `decide` itself (which raises `ValueError` for a
resolution with no stage, `inference.py:703-710`) and the mapping. It never
catches `BaseException`, so cancellation and Stop pass through as today. An
exception is recorded `skipped: "error:<ClassName>"`, logged at ERROR with
the class name only, and the contribution proceeds.

**Why so wide:** anything that escapes reaches `_frames`'
`except BaseException` (`character_turns.py:1002-1008`). That goes to
`_rescue`, which leaves the round `incomplete`. Retry then re-runs
`_round_frames`, asks again and fails again, so the scene stays unplayable
until the switch is turned off.

### 3.4 A deadline on every pre-generation decision

`_select` today runs with no `around`, so the facade's idle timeout is its
only bound. Every pre-generation decision 02 adds (intent, plan, tool
decision) runs under **one deadline for the whole decision**, not a ceiling
per call. `around` wraps each facade call (`inference._once`, and `_native`
per item), and one decision can make several calls in a row:

- the primary;
- a prompt-only re-send for each attempt that refused the schema (`_ask`,
  `inference.py:289-318`);
- a fallback stage when either attempt is native (`stages`, `:147-182`).

A per-call ceiling would therefore allow several times the stated wait.

```python
#: Seconds one pre-generation decision may hold the contribution it shapes,
#: across every call it makes. Argued structurally: the player is waiting on
#: a stream that has not started, and an optional decision must cost less
#: waiting than the reply it shapes. Well under `llm_call_budget` (default
#: 300 s, `store/config.py:159`), which bounds one whole non-streaming
#: generation. To be tuned against 01a's latency reports, never against a
#: real library.
PLAN_CEILING_S = 20.0

def plan_ceiling() -> float:
    budget = store.config.llm_call_budget()
    return PLAN_CEILING_S if budget <= 0 else min(PLAN_CEILING_S, budget)

def plan_deadline() -> inference.Around:
    """One monotonic deadline for one decision. Each call is bounded by
    what is left (`_bounded_call(call, ceiling=remaining)`,
    `routes/common.py:544`). A call that would start with nothing left is
    refused unsent with `DeadlineRefused`, an `LLMError` whose
    `NOT_A_FAILURE` is False, like absorb's `BudgetRefused`
    (`routes/scenes.py:1899`). So `inference._refused_unsent`
    (`inference.py:430`) ends the chain rather than trying a later stage."""
```

**No `on_timeout=_noting(...)`.** `_noting` files an overrun against the
connection through `client.note_outcome` (`routes/common.py:619`). This
deadline is the app's own constant, not the user's `llm_call_budget`, so a
slow but healthy reasoning model would show as failing on the Models page
because of 02. An overrun is still the call's own `error/timeout` row,
filed by its meter.

**Worst-case added wait before the first token**, per contribution:

- the speaker pick, when the round needs one: unbounded but for the facade's
  idle timeout, as today (open question 1);
- plus `PLAN_CEILING_S` for the intent or plan;
- plus the 01b capture write. That write is awaited inside `decide` before
  it returns, and `_record_prompt` takes the campaign lock (M16 in the
  review record), so the play gate counts it in "added pre-generation time".

**Scope for other specs (02-C6).** 3.4 binds the decisions 02 adds. For
every other turn-path decide (09's rerank, 11's Decision stage, 13's NPC
Action choice), 02-C6 requires a **total deadline per decision, named by the
owning spec**, never `llm_call_budget` alone. The owning spec may choose a
lower number than `PLAN_CEILING_S`, or a higher one with its own argument.
09's `RERANK_CEILING` meets this. **Edge note for the coordinator:** 11
section 5.4 runs its turn-path stage under `llm_call_budget`, which this rule
does not allow.

### 3.5 Attribution

Each new call passes `campaign`, `scene`, `post` and `round_id`. Per-response
calls (intent, plan, tool) also pass `response_id`. Today `decide()` takes
no `response_id`, and the response id is minted inside
`store.responses.prepare`, which runs *after* the decision. 02 makes two
small changes. Both are listed in the checklist's shared structures as 02's,
so whichever spec lands first adds them:

- `inference.decide`, `run_stages` and `_Call` gain `response_id: str = ""`,
  handed to every meter they open, as `post` and `round_id` already are.
- `store.responses` gains `mint_id()`, and `prepare(..., response_id=)`
  accepts the id the decision was metered under.

A decision whose contribution then takes the resume path keeps its ledger
row (the call was made) and is recorded nowhere else. Section 6.3 explains
why that is only a race.

### 3.6 Settings

There are three global keys in `config.md`, each with a campaign override in
`campaign.md`. They follow the tracker's pattern (`store/tracker/settings.py`:
the campaign's `on`/`off` wins, an absent or unknown value defers to the
global):

| Key | Values | Default | What it switches |
|---|---|---|---|
| `decide_speaker_sampling` | `off` \| `on` | `off` | C2a |
| `decide_turn_plan` | `off` \| `intent` \| `plan` | `off` | C2b (`intent`) or C3 (`plan`) |
| `decide_play_tool` | `off` \| `on` | `off` | C4 |

All three keys join `config._CONFIG_KEYS` (`store/config.py:219-250`), or
`read_config` silently drops them. They also join `ConfigUpdate`
(`routes/models.py:26-66`), which lists its fields explicitly. The campaign
override gets its own body and route, modelled on the tracker's:
`CampaignPlayDecisions` (`{speaker_sampling, turn_plan, play_tool}`, each `""`
to follow the global) at `PUT /campaigns/{cid}/play-decisions`, beside
`CampaignTracker` (`routes/models.py:289`). They are read through one module,
`store/play_decisions.py` (pure apart from the config read), with
`speaker_sampling(cid)`, `turn_plan_mode(cid)` and `play_tool(cid)`.

**A key is shown in the UI only after its play gate is ratified (section
4.3).** Until then it exists for evals and tests only, and the backend
honours a hand edit. That is acceptable because the default is off and the
backend honours what it stores: no UI writes a setting the backend ignores.
The UI home is a new ConfigView section, `{ id: "decisions", group: "What the
model sees", label: "Play decisions" }` (`frontend/src/routes/ConfigView.tsx:122`).
Each switch's text states the extra call per contribution it costs, on which
route, and, for sampling, whether the current Decision model reports
distributions (5.6).

The new routes (`turn_plan`, `epistemic`, `history_check`) need no page
of their own. The Models page lists every route the server reports, in
01s's Advanced section (`2026-10-09-inference-settings-group-design.md`,
section 3.3). 01s has no toggle that hides a route, so from 02-S2 on the
`turn_plan` row is listed while no UI can switch the feature on (the **dark
period**, until 02-S6). The route's hint says so: "Off unless Play decisions
are switched on".

Turning a switch on starts no call. CLAUDE.md's "settings surface never
spends unasked" rule covers a call the settings page starts, and this one
starts none. The switch's text is the disclosure.

### 3.7 Privacy

A decision item is built from campaign text the turn already sends to a
model. One thing is new: the actor's card brief (6.2) now also goes to the
**Decision-role provider**, which may be a different provider from the scene
route's. The switch's text says so. A decision item is never written to a
log line. `logs.record` rows carry
counts, kinds and ids only, as the continuity identity line does
(`routes/scenes.py:2167-2180`). Captures go through 01b-C1 under the prompt
log's existing Settings disclosure. Eval fixtures (section 4) are synthetic
stores with placeholder names (Seraphine, Mara, Winifred, Saltmarch, Realm),
and no gate report may quote or measure a real store.

---

## 4. The play gate

### 4.1 Why `--gate` cannot gate these

`evals/gate.py` holds a conversion to one rule: wherever the frozen legacy
parse reached `intended`, the decide parse must too (`gate.py:1-40`). Every
site in C2–C5 except C2a is **new**: there is no legacy parse to compare
against. C2a keeps the parse and changes what is done with its answer. So
these features need a different gate: one that measures what the feature does
to *play*, at what cost.

### 4.2 What the play gate is

There are two halves.

**Offline, in `make check`:**

- A `decide-*` eval case per new builder: `decide-turn-intent`,
  `decide-turn-plan`, `decide-play-tool`, `decide-epistemic-access`,
  `decide-history-rerank`. Each holds the prompt contract offline, with
  recordings for compliant, undecodable, off-option, abstained and native
  shapes, on the model of `decide-continuity-identity`
  (`evals/cases.py:1870-1905`).
- **`decide-speaker` gains a `native` recording that carries a
  distribution.** Today it has none (`cases.py:1906-1925`), and C2a's path
  only runs on one.
- Mapping and invariant tests (section 13), including the byte-identical-off
  goldens.

**Live, opt-in, never automatic:** `evals/run.py --live --play <feature>`
runs fixed synthetic play fixtures (`evals/play.py`). Each fixture is a
seeded throwaway store holding a scene, its cast and a player post. The run
produces the full contribution for every **configuration** in the feature's
matrix. A configuration is a scene-route selection, a Decision selection and
a feature setting.

**How a configuration reaches the turn path.** `override_inference` returns
one resolution for one task. The turn path resolves internally: `_select`
calls `require_inference("response-selector", ...)`
(`character_turns.py:748-749`), and the intent call resolves through
`_soft_resolved`. A live eval resolves in the real store and runs in a
throwaway home (`evals/run.py:35-45`). A fixture that only overrode one
task would find no connection in its throwaway home: the pick would raise
409 and the intent would be skipped as `missing_key`, and the gate would
measure "off" under an "on" label. So the play harness **seeds the throwaway
store**:

- it copies into the isolate only the providers each configuration selects,
  with their keys (deleted with the temp directory);
- it writes the format-2 role selections (Primary, Decision) and the
  feature's switch into the isolate's `config.md`;
- it writes nothing to the real store.

The configuration's identity is declared as 01a-C3's open `axes`
(01a section 8): `{"feature": "off|intent|plan|on", "scene_selection":
"<provider>/<model>/<preset>", "decision_selection": "<provider>/<model>"}`,
plus `"sampling": "on|off"` for C2a and `"cutoff": "0|1/(2n)"` where open
question 9 is measured.

**Repeats and drain.** With `--repeat N` (this mode's own default is 5)
every fixture runs N times per configuration, because one live run is an
anecdote (`evals/README.md`, "`turn-taking` and issue #82"). A play case runs
through the app, and a landed turn schedules detached follow-ups. So the
harness follows 01a's drain contract (01a section 6): the last request
returns, `runs.runs_in_flight(app)` empties (under `DRAIN_CEILING_S`), the
lifespan exits, the tripwire is re-checked, and only then are the rows
harvested and the environment restored.

It reports, through 01a-C3's comparison table, per configuration:

- per post: the wall time of each call (01a-C1), split into **added
  pre-generation time** (the decisions before the first token) and
  generation time;
- tokens and the three money columns per task, never added together (01a-C1;
  CLAUDE.md "Costs");
- the existing turn graders' pass rates (`turn-taking`, `roll-fence`,
  `scene-length`, `owned-lore`);
- the feature's own graders (5.7, 6.6, 7.5, 8.6, 9.3).

Rows are filed under 01a-C2's eval scope, never against a **real**
campaign. They carry the fixture campaign's id, because the turn path
attributes every call to its campaign and post (3.5).

One play fixture is several calls under several tasks (`turn-plan`, then
`chat`). 01a-C1 sums a play case across its tasks, per money column and never
across columns, and 01a-C2's eval scope carries every task's rows from one
case.

### 4.3 What passing means

**Hard criteria, which fail the gate:**

1. **Legality:** every selected value, in every live and recorded run, is
   an option the item offered (a property, also tested offline).
2. **No sampled abstention** (01c-C4): no item whose answer was abstained,
   refused or unread produced a sampled value.
3. **No consistent turn-grader regression:** a turn-grader check that passes
   on every repeat of a fixture with the feature off and fails on every
   repeat with it on fails the gate. A mixed result is reported, not failed.
   That threshold is deliberately loose, it is argued from the repeat count
   alone, and it is to be tuned once 01a's numbers exist.
4. **Off is free:** with the feature off, a configuration files no row
   under the feature's task.
5. **On is really on:** a configuration labelled with the feature on fails
   if any repeat records `skipped` for that feature, or files no row under
   its task. This catches a harness that failed to seed a selection.

**Reported, for the user to judge:** added latency per contribution (median
and maximum over the fixture set), extra tokens and money per post by
column, feature-grader rates, and stance diversity (6.6).

**Ratification:** the slice that exposes a feature's switch (3.6) lands only
with its live report attached to the PR and the user's explicit yes. That is
the same "ask the user" rule CLAUDE.md applies to skipping a gate. The report
names models and fixture ids, never store content. Defaults stay `off` in
every slice of this spec. Making any of them default `on` is a later, separate
decision.

---

## 5. C2a: a sampled next speaker

### 5.1 Where it applies

C2a applies wherever `_select` runs: the opening speaker of a Directed round
with no lead, the selector rounds of an empty send (Directed, Manual), and a
Directed follow-on round with no lead (`_first_actor`,
`character_turns.py:882-893`; `_frames` at `:951` and `:982`). It changes
nothing about *whether* a pick is asked.

### 5.2 Rules (on top of 1.4 rule 3)

**01c owns the sampler** (01c-C2, C3, C4; 01c section 5). 02 carries no
sampler, cutoff or renormalisation of its own. Its whole policy is one
`draws.Eligibility`, which 01c applies and records. With the switch on, after
`decide` returns:

1. **The issue mapping comes first and is unchanged.** `selection_of` reads
   the `Answer` exactly as today. Abstained hands control back; an answer
   naming no option is `INELIGIBLE`; anything else unread is
   `INVALID_HANDOFF` (01c section 7: "the caller's issue mapping stays the
   caller's"). The draw never turns a non-answer into a speaker (01c-C4).
2. **The draw.** For every pick that was asked, with a fresh seed:

   ```python
   offered = [*eligible_refs, response_protocol.GRIMOIRE_REF]   # n = len(offered)
   eligibility = draws.Eligibility(
       exclude=(decisions.NONE_KEY,),
       only=tuple(addressed) or None,
       cutoff=1 / (2 * len(offered)))
   draw = draws.draw(question, result, seed=draws.new_seed(), purpose="next",
                     eligibility=eligibility)
   ```

   - **`exclude=(NONE_KEY,)`.** Whether anyone speaks is the model's answer.
     The draw decides only who. Drawing the none would sometimes end a chain
     at random, or let NPCs talk past a pending player decision ("Choose null
     when ... the player's decision is needed",
     `templates/scene/response_selector_question.j2`). An *answered* none
     never reaches a draw: it is `abstained`, and rule 1 hands control back.
   - **`only=addressed`.** This is the actors the round's trigger addresses.
     When the round carries a player-typed note, the trigger is that note;
     otherwise it is the newest non-synthetic post in the pick's window.
     `addressed` is the eligible actors the trigger names, minus the trigger's
     author. The rule is the writer's handoff guidance: "Being addressed
     determines who starts" (`templates/scene/response_actor.j2:74-75`).
     It uses group play's name matcher, made public as
     `group_play.named(text, entries, author_ref=None)`. Today's `_named(text,
     entries)` (`group_play.py:136`) takes no author, and its callers filter
     the author themselves (`:182`, `:199`). The window carries speaker
     *names*, so the post's author is mapped to its ref through the response
     record (`responses.actor_refs`, as `_last_contribution` does). A note
     that names Mara confines the draw to Mara, which is how a director note
     keeps its authority without a special case. **This narrowing is a
     deterministic policy that can override the model's answer.** If the
     report puts Winifred first and Seraphine addressed Mara, Mara is drawn
     with certainty. That is recorded, not hidden: `eligibility.only` holds
     the narrowing, and `answer != selected` shows the override. The play
     gate grades it on its own line (5.7).
   - **`cutoff=1/(2n)`.** This is 01c's `Eligibility.cutoff`. It removes
     every key whose *reported* probability is below it. 01c applies it after
     `only` and `exclude` and before any floor; 02 sets no floor. The
     argument is structural: an actor the model rated below half of
     indifference is one it argued against, and a draw should not revive
     them. It is to be tuned through 01a's reports (the gate's `cutoff`
     axis, 5.7). A cutoff that empties the set gives no draw (`basis:
     none`, `why: cutoff`), which rule 3 handles.
3. **Mapping the draw to `(next, issue)`**, when rule 1 read a speaker:
   - `basis: "sampled"` gives `next = draw.value`. It is an offered key,
     never the none, because the none is excluded.
   - `basis: "answer"` (no usable report, a partial or inconsistent report,
     01c section 5.4) gives the answer, as today.
   - `basis: "none"` with `why: "ineligible"` or `why: "cutoff"` comes from
     one of two cases. Either the plain answer lies outside `addressed`
     with no usable report, or the cutoff removed every addressed actor.
     The pick then draws once more with the same seed and purpose,
     `Eligibility(exclude=(NONE_KEY,))` and no `only` or `cutoff`, and
     stores that record. A narrowing must not turn an answered speaker into
     "nobody" and so hand control back.

`selection_of` is unchanged. `decisions._distribution`
(`decisions.py:843-856`) drops any distribution with a key outside the
options and the none, and 01c's step 2 raises on an `only` key that is not
offered. So a drawn key is an offered one by construction.

### 5.3 Seed and record

Each draw's seed comes from `draws.new_seed()`, which is 53 bits. Its test
seam is 01c's `draws._seed_source`, not `character_turns._rng`. The seed is
minted once per asked pick, and nothing derives one from an id (01c section
6.1, rule 3).

The record is **01c-C3's record, unchanged** (01c section 6). It holds
`offered`, `distribution` as ordered pairs, `eligibility`, `seed`, `purpose`,
`selected`, `answer`, `basis` and `why`. It is stored under the key **`pick`**
on the round record, in the same `_round_state` call that writes the speaker,
under the lock that write holds (01c section 6.1, rule 1):

```python
_round_state(cid, sid, round_record, actor_ref=next_ref,
             status="pending" if next_ref else "complete", issue=issue,
             pick=draw.record)
```

**"Decided" means the record is present** (01c section 6.1, rule 2). With
the switch on, `_first_actor` skips `_select` when `round_record.get("pick")
is not None`, whatever `actor_ref` says. A retry, a recovery or a roll
resume therefore never re-draws. With the switch off, no `pick` is written,
and `_first_actor` behaves as today. Every reader uses `.get("pick")`. Rounds
written before this spec, and the frozen campaign's, have none (01c section
6.1, rule 4).

Replay is `draws.replay(record)`, so a browser inspector replays the same
draw (01c section 6.2).

### 5.4 Code shape

```python
# store/response_protocol.py  (pure)
def addressed(conversation: list[dict], note: str, eligible: Sequence[dict],
              author_ref: str | None) -> tuple[str, ...]:
    """The eligible refs the round's trigger names (5.2 rule 2)."""

def pick_eligibility(offered: Sequence[str],
                     addressed: Sequence[str]) -> draws.Eligibility:
    """exclude=(NONE_KEY,), only=addressed or None, cutoff=1/(2n)."""
```

`_select` calls `decide`, reads `selection_of` and, with the switch on, calls
`draws.draw` (and the `only=None` re-draw of 5.2 rule 3). It returns
`(next, issue, record)`. `_first_actor` writes the record with the speaker.
The task's 01d-C1 row is `TaskPolicy(samples=True, question="next",
escalate_to="primary", escalate_on=("refused",), reads_declines=True)`.
Escalation is on `refused` only, never `low_margin`, because a sampling task
may not escalate on margin (01c section 4.1). The row ships with escalation
off (01d section 6.4) until C2a's gate measures it.

### 5.5 What it costs

**Nothing in calls.** The sample reuses the pick's own answer. Its latency
is a few microseconds of arithmetic. Its cost shows only through which
speaker runs, so the play gate measures its *effect*, not its price.

### 5.6 When a distribution exists

A distribution exists today only on a native stage, which serves only a model
that cannot generate (1.2). 01c-C1 decides whether a generating Decision
model ever reports one (native-first, model-stated probabilities, or
neither; 01c-C1 records that verbalised probabilities are rejected). 02 reads
01c-C1's predicate `reports_distribution(resolved)` rather than re-deriving
the rule. The settings text asks it, so the switch can say "with this
Decision model the pick is always the most likely speaker".

### 5.7 Play gate for C2a

- **Configurations:** a native-only Decision model with sampling `off` vs
  `on`. A structured one is included to prove sampling is inert there.
- **Feature graders:**
  - *mass on intended*: the probability the draw selects the fixture's
    intended speaker, computed exactly over the weights **actually drawn
    from** (the record's distribution after its own `eligibility`, as
    `draws.replay` computes them) and set beside the answer's 0 or 1;
  - *addressed override*: how often `eligibility.only` was set and the
    answer lay outside it. These are the picks the narrowing decided, not
    the model;
  - *departure*: how often `selected != answer`;
  - *spread*, the distinct speakers across repeats on a fixture with no
    addressee;
  - *hand-back preserved*, on fixtures whose intended answer is null:
    sampling must never turn one into a speaker (5.2 rule 1 makes this
    structural; the grader proves it).
- **Fixtures** include an addressed NPC (Seraphine asks Mara), an open
  remark to two NPCs, a pending player decision, and a director note naming
  Winifred.

---

## 6. C2b: turn intent

### 6.1 The question

One `Choice` about the assigned actor's next contribution:

```python
# store/turn_plan.py  (pure; renders templates, so callers run it in the threadpool)
STANCE_ID = "stance"
STANCES: tuple[str, ...] = (
    "engage",    # answer or go along openly
    "press",     # push, challenge or accuse
    "deflect",   # evade, change the subject, answer another question
    "refuse",    # decline openly
    "deceive",   # say what the character believes untrue
    "withdraw",  # disengage, go quiet, leave the exchange
    "act",       # let an action carry the reply rather than words
)
```

Each option's description is a template, `templates/turn_plan/stance.j2`,
so the wording is editable like every other prompt. `allow_none=True`: "no
particular stance" is a real answer, and an abstention renders no block.
The set is closed and small on purpose. A stance is a way of answering,
not a script. It never says *what* the character says. The vocabulary is a
starting point, to be revised against the play gate's adherence grader
rather than argued in the abstract.

### 6.2 The item

```python
def intent_item(actor: dict, conversation: list[dict], brief: str) -> decisions.Item:
    """Context: `turn_plan/item.j2` over the observable conversation (the
    speaker pick's window and regex view, `response_protocol.
    observable_conversation`), the actor's name, and `brief`: the actor's OWN
    card summary clipped to INTENT_BRIEF_TOKENS. Questions: (stance,)."""

#: Tokens of the actor's own card a turn decision reads. Argued structurally:
#: a closed question over a short context answers as well as over a long one,
#: and the item is re-sent per contribution, so its context is paid every
#: time. Tuned against the play gate's adherence grader later.
INTENT_BRIEF_TOKENS = 600
```

**Epistemics.** The brief is the actor's own material: their card
description and personality, through the same overlay the writer reads, and
nothing else. It holds no other actor's private description, which the
character-turns spec forbids even to the selector ("Raw
descriptions/personality/dossiers are not proven public"). The conversation
is the observable window the pick reads.

### 6.3 When it is asked

In `_round_frames` (`character_turns.py:1014`), after the
`_recover_completed` check and **before** `_prepare`:

```python
plan = None
if await run_in_threadpool(_plans, cid, sid, turn.round_record, actor, turn.appended):
    plan = await _turn_plan(cid, sid, client, turn.round_record, actor, response_id)
turn.record, messages, ... = await run_in_threadpool(
    _prepare, cid, sid, run, token, turn.round_record, actor, resolved,
    turn.appended, plan=plan, response_id=response_id)
```

`_plans` is False, so nothing is asked, when any of these holds:

- the setting is `off`;
- the actor is `grimoire` (a narrator has no stance, `response_actor.j2`);
- the round has a pending response that `_prepare` would resume. A retry
  reuses the snapshot. A roll resume passes the stored plan back in
  (3.1 rule 5);
- `appended` is non-empty (a roll continuation, which carries the stored
  plan, 3.1 rule 5);
- the round carries a player-typed note. The note *is* the player's
  direction, and a sampled stance could contradict it. An empty send's
  `director_note.j2` is app wording, not the player's, so it does not count.

A replayed turn (`post_replay_turn`) composes fresh
(`routes/scenes.py:6031-6033`), so it asks a new intent for each turn it
replays, and each of those is one more decide call. `replay_fork_threshold`
counts model turns, not these calls. The switch text says so.

A reroll (`regenerate_response`, `character_turns.py:1522`) and Keep writing
(`extend_response`, `:1591`) replay snapshots and never reach this code.
`_plans` reads the same state `_prepare` does, outside the lock. The run
owns the round (turn token and fence), so they disagree only when the fence
is about to fail anyway. A plan computed for a contribution `_prepare` then
resumes is discarded (3.5).

### 6.4 The call

```python
resolved, why, kind = await run_in_threadpool(
    _soft_resolved, lambda: require_inference("turn-intent", cid, operation="decide"))
decision = await operations.decide(
    "turn-intent", [item], client=client, resolved=resolved,
    campaign=cid, scene=sid, post=round_record["post"], round_id=round_record["id"],
    response_id=response_id, capture=<01b-C1 capture>,
    around=plan_deadline())
```

This runs only when `resolved` is not `None`. Otherwise the intent is
recorded `skipped: kind` (3.2). The whole step is inside 3.3's
`except Exception`, and a failure is recorded with its kind or class name.
The task goes on a new route:

```python
Route("turn_plan", "Turn planning",
      "How each character approaches their reply in play, decided before it is "
      "written. Off unless Play decisions are switched on.",
      ("turn-intent",), True, operation="decide", default_role="decision",
      legacy=routing.NO_LEGACY)
```

C3 adds `"turn-plan"` and C4 adds `"turn-tool-decision"` to its tasks, each
in the slice whose call site names the task (`test_routing_guard.py:659`,
`test_operation_guard.py:996`). One route, because a user who wants a cheap
model for "how does Mara answer" wants it for all three. The ledger still
tells the tasks apart.

`legacy=routing.NO_LEGACY` marks a route born at format 2 that has no
legacy key. `Route.legacy` is `""` today, which means "this route *is* a
legacy route", and that would add `route_turn_plan` to `LEGACY_ROUTES` and
`CONFIG_KEYS` (`store/routing.py:55-58`, `:142-158`, `:183`). The sentinel is
a checklist shared structure: 01g, 09, 10 and 02-C5 need it, and whichever
lands first adds it. The `epistemic` and `history_check` routes (9.2, 9.3)
carry it too.

### 6.5 Sampling, the record and the block

**Sampling.** It follows the same "whether by argmax, which by sample" rule
as 5.2:

- an abstained, refused or unread stance renders nothing, and is recorded;
- otherwise `draws.draw(stance_q, result, seed=draws.new_seed(),
  purpose="stance", eligibility=Eligibility(exclude=(NONE_KEY,),
  cutoff=1/(2n)))`, where `n` is the number of stances without the none. A
  cutoff that empties the set leaves the plain answer as the stance. A
  draw with `basis: answer` is the plain answer, which is the only case on a
  structured backend. The record is 01c's, unchanged.

**The record** is stored on the response record by `store.responses.prepare`:

```json
"intent": {
  "task": "turn-intent", "skipped": "",          // or "timeout", "missing_key",
                                                 // "error:<Class>", ...
  "stance": "deflect",
  "draw": { /* 01c-C3's record, unchanged, for the stance question */ }
}
```

Readers use `.get("intent")`. Responses written before this spec, and the
frozen campaign's, have none.

**Where it goes: after the history, not in the system prompt.** It is placed
exactly as an author's note at depth 0 is placed: its own `{"role":
"system"}` message after the last post, through the same `inject`
(`store/context/authors_note.py`, "Where" and "How it reaches the model").
It inherits that module's stated provider caveats. It is **not** a catalog
section, for two reasons:

- **Prefix caching.** The block changes on every contribution. Anywhere in
  the system prompt, it would invalidate the provider's cached prefix for
  everything after it, including the whole history. The ledger records
  that loss as `cache_read_tokens` (`openai_compatible.py:297`), and it
  would skew 7.5's cost comparison for a reason that has nothing to do with
  the hypothesis.
- **Layout.** Every catalog section can be switched off by id in the prompt
  layout (`layout.apply`, `assemble.py:1338`). A user who switched it off
  would still pay for every decide call.

Template `templates/scene/turn_intent.j2`. It renders:

```
{{ name }}'s approach in this reply: {{ stance_description }}. Write it in
their own voice. Never name or explain the approach.
```

`compose_turn` and `compose_director_turn` (`assemble.py:1637`, `:1700`)
gain `plan: dict | None = None`. `None` adds no message, so the prompt is
byte-identical (3.1 rule 6). The block is in the snapshot, in the resume
snapshot (3.1 rule 5), and in the generation's own prompt capture, as its
own inspector row like an author's note. A reader inspecting "What the model
saw" sees the intent the reply was written under. The play gate reports
cache-read tokens per configuration.

**Inspector.** One line in the response's details, "Approach: deflect
(sampled, 38%)" or "(most likely)", read from `intent`. It is a plain
attribute, a non-clickable `<span class="chip on">` per the list/detail
rules. No new page.

### 6.6 Play gate for C2b

- **Configurations:** the same Primary and Decision models with `off` vs
  `intent`, on a structured and on a native Decision model.
- **Feature graders:**
  - *adherence*: a decide call on the Decision role asks of the generated
    reply "which of these stances does it take?", with the same options.
    That is a model judging a model, an approximate second opinion like voice
    drift, and it is reported as such;
  - *stance diversity*: distinct stances across repeats and fixtures. A
    deterministic argmax that always says `engage` is visible here;
  - *leak check*: the generated text never contains a stance id or the
    block's wording. A regex check, offline on recordings as well.
- **Added latency** is the intent call's wall time, all of it before the
  first token.

---

## 7. C3: the pre-generation turn plan

### 7.1 What "batched" means here, and what it does not

The plan is **one decide item with several questions about one contribution**,
sent as one call. That is the batching `Item` already supports
(`decisions.py:212-217`) and the draft asked for ("Batch several independent
questions over shared state").

It is **not** one call at round open planning every speaker of the round.
Rejected, for two reasons:

- A later speaker's plan would be decided against a transcript missing the
  contributions it answers.
- It would pre-script reactions, which the character-turns spec forbids
  ("Handoff predicts who might respond; it must not script that person's
  reaction"; "The pre-roll choice cannot pre-author outcomes for subsequent
  speakers").

So the plan is per contribution, at C2b's place in the loop, and when it is
on it **replaces** the intent call. The setting is `intent` *or* `plan`,
never both, so a contribution never pays for two pre-generation calls.

### 7.2 The questions

```python
def plan_item(actor: dict, conversation: list[dict], brief: str) -> decisions.Item:
    """(stance, disclosure, tension) over intent_item's context."""

DISCLOSURE_ID = "disclosure"   # Choice, allow_none: share | hint | withhold
TENSION_ID = "tension"         # Score: ("ease off", "hold steady", "raise the stakes")
```

| Question | Kind | Read as | Sampled? | Why |
|---|---|---|---|---|
| `stance` | Choice, none allowed | 6.1 | Yes, as 6.5 | Behavioural variety is the point. |
| `disclosure` | Choice, none allowed | Of what *this character already knows*, how much they let out | **Never** (argmax) | A random reveal of a secret is a continuity event nobody chose. A deterministic answer can be reviewed in the inspector; a draw cannot be argued with. |
| `tension` | Score, three levels | Whether the reply cools, holds or raises the scene | Never | It shapes pacing. A coin flip on pacing is noise. |

The block (6.5) renders one line per read answer and nothing for an
abstained or unread one. Disclosure's line says "of what they already know".
That keeps knowledge in the actor prompt's existing rules: perception and
"Use a fact ... only when their own established knowledge ... supplies it"
(`response_actor.j2`). A plan can make a character more or less forthcoming.
It can never grant knowledge.

The task is `turn-plan`, on the `turn_plan` route. The record is
`"intent"` with `disclosure` and `tension` added, and the stance's `draw` as
in C2b.

### 7.3 The extension slot (for 13-C3)

The slot carries extra **items**, not extra questions on the plan's item.
13's NPC Action decision needs a context of its own: a sheet summary, the
actor's conditions, and descriptions of the legal set (13 section 24.3). The
plan's item context is the conversation and the actor's brief.

```python
def plan_items(plan: decisions.Item,
               extra: tuple[decisions.Item, ...] = ()) -> tuple[decisions.Item, ...]:
    """(plan, *extra): one `decide("turn-plan", ...)` call."""
```

**What "one call" means with extra items:**

- On a **structured** stage, the plan and up to seven extra items are one
  chunk, so one call (`decisions.MAX_ITEMS_PER_CALL`, `decisions.py:101`).
- On a **native** stage, every item is its own request (`_native`, at most
  `NATIVE_CONCURRENCY` in flight, `inference.py:86`). It is still one
  `decide()` under one deadline (3.4), but it is not one request.

**What the slot cannot do:** an extra item answered in the same call cannot
be conditioned on that call's stance. Both are answered together, and
neither sees the other. A 13 Action that must follow the stance needs a
second, ordered call. That is 13's choice to make and to gate.

**Rules for the owner:**

- each extra item is built, read back and drawn from by its owner (13's
  legal-Action `Choice` or `Joint`, from `available_actions`);
- the plan code stores the owner's `ItemResult` and any 01c record under the
  owner's key in `intent`, untouched, and renders none of it;
- the owner keeps every 02-C6 rule.

02 adds no Action item itself.

### 7.4 What the plan does not batch: the speaker

The speaker pick and the plan cannot share a question until 01e-C3 (joint
choice) lands. The plan is about the chosen actor, and a stance conditioned
on "whoever you pick" is not a question `Choice` can ask. So a Directed round
that needs a pick pays two serial calls before its first token: the pick,
then the plan. When 01e-C3 lands, a joint `(speaker, stance)` question can
replace both in the opening contribution. That is a follow-up, gated like
everything here (open question, section 16).

### 7.5 The hypothesis, and how it is tested

The draft's claim: a plan may let the Primary run at lower reasoning, so that
plan cost + cheaper generation < high-reasoning generation. **02 never acts
on it.** The plan never changes the scene route's preset. Whether a user
lowers reasoning is their own preset choice, made with the report in hand.

The play gate for C3 runs 01a-C3's matrix on each fixture:

| Configuration | Scene route | Plan |
|---|---|---|
| A | Primary, high-reasoning preset | off |
| B | Primary, medium | `plan` |
| C | Primary, low | `plan` |
| D | A cheaper Primary model | `plan` |

The table reports, per configuration and per post: added pre-generation time,
generation time, tokens and money per task in three columns (never summed),
and every turn grader and C2b grader. The hard criteria are 4.3's, comparing
B, C and D against A. The cost comparison is a report, not a threshold.
Whether "C costs less than A at equal grades" holds is a statement about two
models on synthetic fixtures, and the user reads it as that.

---

## 8. C4: Decision as a tool, from play

C4 is the play-side policy for 01g-C5. It lands last, only after 01g-C1..C5
have landed, and only behind its own play gate.

### 8.1 What the model may ask

A contribution's generation may be offered one tool, `decide_choice`, with
provider-neutral parameters (01g-C2):

```json
{"question": "string, at most 300 characters",
 "options": [{"id": "string", "description": "string"}],   // 2..6 options
 "allow_none": "boolean"}
```

- The shim (01g-C5) builds one `decisions.Item`:
  - context: `turn_plan/tool_item.j2` over the asking actor's name, the
    question, and the same observable conversation window the pick reads.
    Never the writer's whole prompt;
  - one `Choice` over the model's options.
- It answers it with `decide("turn-tool-decision", ...)` on the `turn_plan`
  route.
- Options that are not `offerable` (`decisions.py:334`), or that collide once
  normalised, are refused unsent. The tool result is then `{"selected": null,
  "reason": "invalid_request"}`.
- **The result is the selection, never the distribution:**
  `{"selected": "<id>"}` or `{"selected": null, "reason": "abstained" |
  "unanswered"}`. Handing the model the probabilities invites it to argue with
  the draw. Sampling follows 6.5's rule.

### 8.2 Where a tool call is honoured

**Only before any visible text.** "Visible" means text the watcher would
show: `ResponseWatcher`'s visible output, not any non-empty delta. With
`perception_rider` on, the hidden perception fence is a non-empty delta that
is never shown, so testing raw deltas would decline every later tool call.
The test handed to 01g-C6's decline hook is the watcher's. The first turn of
the loop may be a tool call. Once the watcher has produced visible text, a
later tool call is **not executed**. The contribution ends as written and the call is recorded
as `declined: "after_text"`.

**Why:** display frames already sent cannot be withdrawn. `ResponseWatcher`
reads one continuous stream for the handoff and the roll fence
(`response_protocol.py:226`). Splicing a second generation after a
mid-prose tool turn would give the watcher two streams joined by an invisible
seam, and `_rescue` a partial whole it cannot attribute. A tool call before
any text costs nothing of that kind: the visible stream starts on the second
turn.

### 8.3 Caps

- **At most one tool call per contribution** (`PLAY_TOOL_CALLS = 1`), and at
  most two loop turns. Argued structurally: each loop turn re-sends the whole
  contribution prompt (8.5), so the cap bounds the worst case at twice a
  contribution's prompt. To be revisited only with 01a's report.
- 01g-C4's run budget is applied per contribution, with the wall clock of
  3.4 for the decision itself.
- A cap reached returns `{"selected": null, "reason": "cap"}` to the model,
  rather than an error that would fail the stream.

### 8.4 Where it is offered

The tool is offered only when **all** of these hold:

- `decide_play_tool` is on;
- the actor is an NPC (not `grimoire`);
- the scene route's primary is not known to lack `tools` (01g-C1: a `no`
  refuses; an `unknown` offers, as with every capability, 01 section 5.3);
- the generation is a fresh contribution **or a reroll of one**. C2b's
  `_plans` conditions apply, minus the typed-note rule (a model may still
  meet an unanticipated choice under a note). A reroll is the one replay
  that offers the tool.

A reroll is a new **variant** of the same response, not a new response:
`regenerate_response` → `_reroll_frames` → `_land_variant` → `save_variant`
and `activate` (`character_turns.py:1521-1588`, `:1738-1760`). Its loop turns
come after the snapshot, so it may decide differently, and the snapshot
never holds a tool result. Retry, roll resume and Keep writing do not offer
the tool: they continue a reply whose loop is already over.

### 8.5 Cost, stated plainly

- The tool definition is sent on every offered call, so prompt tokens rise
  on every contribution, called or not.
- A call that is used re-sends the whole contribution prompt plus the tool
  result for the second turn. That roughly doubles that contribution's input
  tokens, unless the provider caches prompts.
- Each loop turn is its own meter under one run id (01g-C3), attributed with
  3.5's ids. The decision is its own `turn-tool-decision` row.

The play gate reports all three separately.

### 8.6 Records, invariants and gate

**Record.** The tool decision is stored **per variant**, beside the
variant's `made_by`, under `"tool"`: the question, the options, the
selection, 01c-C3's record, and the backend. Swiping back to variant 1 then
shows variant 1's decision, and a reroll's decision never overwrites another
variant's. 01b-C1 captures it. The transcript holds only the
final visible text (3.1 rule 2). Tool turns are never shown.

**Invariants.** 3.1 holds, with rule 1's exception bounded by 8.2–8.3. The
handoff and the roll fence are parsed from the visible text only. A stream
cancelled during the tool turn aborts its meter like any cancel.

**What 01g-C6 provides for this:**

- the loop's final turn streams through `inference.generate`'s streaming
  path, so `_stream_contribution`'s display and watcher stay as they are;
- the loop can decline a tool call that arrives after visible text (8.2).

A loop that only joins replies could not carry a scene turn, which is why C4
is hard on 01g-C6.

**Gate.** The configurations are `off` vs `on` on a tools-capable Primary.
The feature graders are:

- tool-call rate;
- declined-after-text rate;
- legality of the model's options;
- *adherence*: does the reply follow the selection? Judged as in 6.6.

The cost report separates the two-turn input from the one-turn input.

---

## 9. C5: decide kits for 11 and 09

### 9.1 Why kits, not live routes

A route cannot land before a call site decides its task
(`test_operation_guard.py:996`; `test_routing_guard.py:659`; 01 section 14's safety
rule). The call sites are 09's and 11's. So 02-C5 delivers a **kit** per
task. Exactly what lands where:

**In 02 (slice 02-S7):**

- the item builder and the mapping (pure modules with tests). A pure
  builder names no task in a `decide()` call;
- the templates, with their `verify_templates.py` entries;
- the recordings, and a `Case` definition held **outside `CASES`**
  (`evals/cases.py`, as an unregistered constant).
  `backend/tests/test_evals.py:67-74` fails any registered case whose task
  no route claims, so the case cannot be registered yet. The recordings are
  replayed by the kit's own unit test against that unregistered case, so
  they are not orphaned.

**In the consumer's first slice, together with its call site:**

- the route entry;
- the `Case` registration in `CASES`;
- the 01d-C1 policy row (01d's `test_task_policy.py` requires every
  `TASK_POLICY` key to be in `TASK_ROUTE`, 01d section 4.3);
- the 01b-C1 capture, which belongs to the call site. The checklist lists
  01b as hard for C5, but that applies to the consumer's call, not to the
  kit (an edge note for the coordinator).

The case is an `evals/cases.py` replay case, gated live by 4.3. It is not
a `gate.py` `Conversion`, because a new site has no legacy parse (4.1).

### 9.2 C5a: epistemic access (for 11-C1)

```python
Route("epistemic", "Character knowledge",
      "Whether a character could know a recalled piece of history, where the "
      "record alone cannot say.",
      ("epistemic-access",), True, operation="decide", default_role="decision",
      legacy=routing.NO_LEGACY)
```

```python
# store/epistemic_access.py  (pure; renders templates)
ACCESS_ID = "access"
CLASSES = ("known", "suspected", "experienced", "narrator_only")   # allow_none -> UNKNOWN
UNKNOWN = "unknown"

@dataclass(frozen=True)
class AccessQuestion:
    actor_ref: str
    actor_name: str
    evidence_ref: str      # 09's evidence id
    excerpt: str           # bounded, already through the prompt-phase regex view
    basis: tuple[str, ...] # 11's deterministic signals, e.g. ("present", "not_addressed")

def build_items(questions: Sequence[AccessQuestion]) -> tuple[decisions.Item, ...]
def access_of(questions: Sequence[AccessQuestion],
              decision: decisions.Decision) -> dict[tuple[str, str], str]
    """(actor_ref, evidence_ref) -> class. Reads `decision.items` and
    `decision.escalations`: any index with an `Escalation` whose `outcome`
    is not "answered" maps to UNKNOWN, whatever `items[i]` holds."""
```

**Rules:**

- **Deterministic first.** 11-C1 asks only what its structural rules could
  not settle (sections 3 and 4 of the 11 draft). The kit never decides a pair the caller did not
  hand it.
- **Fail closed.** `access_of` maps the following to `UNKNOWN`:
  abstained, refused, unreadable, `NOT_AN_OPTION`, an error, and an item the
  reply never reached. Never `known`. The costly error is a character knowing
  what they should not, so every failure lands on the side that withholds.
- **Escalation (01d-C2) applies here.** The policy row is
  `TaskPolicy(escalate_to="primary", escalate_on=("refused", "low_margin"),
  question="access", margins=<01d-C3's>)` (01d's draft, section 4.1). Not
  `abstained`: an abstention is `UNKNOWN`, which is already the safe side.
  The hop's answer replaces the first one.
  - **A hop that did not answer.** When the hop fails or is skipped
    (`same_model`, `cap`, the clock), 01d-C2b leaves the **base** answer in
    `decision.items[i]`, and reports the hop's fate only in
    `Decision.escalations[i].outcome` (`"failed" | "skipped"`, 01d section
    5.5). That is why `access_of` takes the whole `Decision`. Any index whose
    escalation `outcome != "answered"` maps to `UNKNOWN`, never to the base
    answer. Concretely: a low-margin `known` whose hop times out is
    `UNKNOWN`. Reading `items` alone would have returned `known`, and handed
    the actor history they should not have.
  - **Only permissive answers need the hop.** A low-margin `narrator_only` is
    already safe, so escalating it only spends. 01d's triggers do not read
    which option was chosen without a filter, so 02-C5a uses 01d-C2a's
    optional answer filter, set to `("known", "suspected", "experienced")`.
- **Never sampled.** What a character knows is not behaviour.
- **Derived, never persisted as truth.** The result is per turn (section 9 of the 11 draft). The
  kit writes nothing.
- **Budget.** Chunked at eight items per structured call. 11 should hand at
  most one chunk per turn on the turn path, under a total deadline it
  names (02-C6, 3.4). 11 owns the number.

### 9.3 C5b: history relevance (for 09-C1/C2)

The names follow 09's draft (its open question 3): task `history-rerank` on a
route `history_check`. 10 later adds its own `history-sufficiency` task to
the same route. Both are decide tasks on the Decision role, so they agree on
`fallback` as 01d's `test_task_policy.py` requires of a route's tasks. 09
names its own ceiling (`RERANK_CEILING`) rather than 3.4's, and passes
`post`. Nothing here contradicts either.

```python
Route("history_check", "History checks",
      "Whether a recalled scene bears on the current turn, after retrieval has "
      "found it.",
      ("history-rerank",), True, operation="decide", default_role="decision",
      legacy=routing.NO_LEGACY)
```

```python
# store/history_rerank.py  (pure; renders templates)
RELEVANCE_ID = "relevance"
LEVELS = ("unrelated", "background only", "related", "bears directly on this turn")

@dataclass(frozen=True)
class Candidate:
    ref: str        # 09's evidence id
    excerpt: str    # bounded, already through the prompt-phase regex view
    when: str = ""  # an in-fiction date label, or ""

@dataclass(frozen=True)
class Grade:
    level: int | None              # None = ungraded, never 0
    distribution: dict[str, float] | None   # only as reported (native)

def build_items(query: str, candidates: Sequence[Candidate]) -> tuple[decisions.Item, ...]
def grades_of(candidates, results) -> dict[str, Grade]
```

**Rules:**

- **Ungraded is not irrelevant.** An item that was not read is
  `Grade(None, None)`, and 09 ranks it by its own signals (09-C2's fallback).
  This is the cost rule ("a price nobody reported is never rendered as zero")
  one domain over: a grade nobody gave is not a low grade.
- **No escalation and no sampling.** Relevance runs on the turn path, where a
  second hop is latency the player waits for. A low margin is a middling
  grade, not a failure.
- **01e upgrade path.** When 01e-C1 (`Rank`) lands, `build_items_ranked` asks
  one item ranking up to eight candidates, so the model compares them rather
  than scoring each alone. When 01e-C2 lands, the scale may get finer. 09
  chooses which to call. The kit keeps the Score form for 09's first slice.
- **Budget.** 09 bounds the candidates it hands over to one chunk (eight) on
  the turn path, under its own `RERANK_CEILING`. On a native stage that is eight
  requests at `NATIVE_CONCURRENCY` (four, `inference.py:86`) at a time.

### 9.4 Kit acceptance

Each kit has:

- builder and mapping tests, including fail-closed and ungraded. For C5a
  this includes: a low-margin `known` whose `Escalation.outcome` is
  `"failed"` (and `"skipped"`) maps to `UNKNOWN`;
- a `decide-*` replay case with compliant, abstained, off-option,
  undecodable, native and native-refused recordings;
- a `verify_templates.py` entry for its templates.

09 and 11 add the live gate for their own call sites. Their calls are on
the turn path too, so 4.3 applies to them.

---

## 10. Fallback, escalation and sampling per task (the 01d-C1 rows)

01d-C1 owns the policy's spelling (`routing.TaskPolicy`, `routing.policy`,
`TASK_POLICY`, which is empty at landing; 01d's draft, section 4.1). 01c adds
`samples` to the same structure. This is the row each new task of 02 needs.
For the landed tasks it is 02's position on 01d's candidate table (01d's
draft, section 6.4). That table ships every row off, and switches each task
on in a change of its own.

| Task | `fallback` | Escalation | `samples` | Why |
|---|---|---|---|---|
| `response-selector` | `role` (today) | `escalate_on=("refused",)`, `reads_declines=True`, shipped off until C2a's gate measures it (01d section 6.4). Never `low_margin` | Yes, per campaign (C2a) | A flat distribution is the behaviour to sample, not doubt to resolve, and the pick is on the turn path. `reads_declines=True` because the pick maps a decline to its own outcome (an abstention hands control back). |
| `turn-intent`, `turn-plan`, `turn-tool-decision` | `role` (one route; they must agree) | Off | Yes (stance; the tool's selection) | As above. The feature is soft, so a failure costs only the intent. |
| `scene-break` | `role` (today) | Off, as 01d recommends | No | A YES is a proposal the player confirms. |
| `voice-drift` | `role` (today) | Off, as 01d recommends | No | A verdict feeds the review. |
| `continuity-identity` | `role` (today) | Not switched by 02. 01d names it the first candidate; its switching change is its own, under 01d's bar (01d's draft, section 6.3) | No | `uncertain` already flags the row for the human review, so the hop must be shown to cut wrong merges and missed duplicates, not merely the count of `uncertain`s. The bar should say so. |
| `continuity-reconcile` | `role` (today) | Not switched by 02 | No | Proposals are reviewed. Unanswered candidates are asked again next sweep. |
| `epistemic-access` | `role` | **On** (9.2) | No | Leakage is the costly error. Fail closed. |
| `history-rerank` | `role` | Off | No | Turn-path latency. Ungraded falls back to signals. |
| `history-sufficiency` (10, same `history_check` route) | `role` (agrees with `history-rerank`, as a route's tasks must) | On (10's row; 10-C2's repair hop escalates through 01d-C2b) | No | 10's own argument. Listed here because it shares C5b's route. |

**One rule across the table: a task never both samples and escalates on
`low_margin`.** Escalation treats a flat distribution as uncertainty to
remove. Sampling treats it as the behaviour to reproduce. A task that did both
would send exactly the items sampling exists for to a second resolver.
`refused` escalation is compatible with sampling, because a refusal is never
sampled (01c-C4).

**What 01c and 01d provide for this:**

- 01d-C1's `TaskPolicy` refuses a row with `samples=True` and `"low_margin"
  in escalate_on`;
- a failed or skipped hop leaves the base answer in `items` and says so
  in `Decision.escalations[*].outcome` (01d section 5.5). 02-C5a reads that
  signal and maps the item to `UNKNOWN` (9.2). Per-item provenance (01d
  section 5.6) names who served the item, not whether the hop answered.

---

## 11. Contract

**02-C1 — Reconciled record.** Section 1, as of `35c1fb7`.

- **Guarantees:** every "landed" row cites the code, and names the guard or
  suite holding it where one exists. Every "not landed" row names the
  contract or spec that now owns it.
- **Failure behaviour:** a later PR that changes a landed decide site updates
  section 1.2 in the same PR. Nothing checks the citations automatically.
  Where a row names a guard, that guard fails on a behaviour change; the
  citation itself is held only by review.

**02-C2a — Sampled next speaker.**

- **Inputs:** the speaker pick's `Decision`, the offered refs, the addressed
  set (from the round's typed note or its newest post) and the switch.
- **Outputs:** `(next, issue)`. The issue comes from `selection_of`, exactly
  as today; the speaker comes from 01c's `draws.draw` with
  `Eligibility(exclude=(NONE_KEY,), only=addressed, cutoff=1/(2n))`, a seed
  from `draws.new_seed()`, and `purpose="next"`. 01c's record is stored
  unchanged as `pick` on the round, in the write that stores the speaker.
- **Guarantees:**
  - every 1.4 rule 3 invariant holds;
  - a drawn value is an offered option and never the none;
  - abstained, refused, unread and no-usable-report answers are never drawn
    (01c-C4); the issue mapping is decided before any draw;
  - an addressed actor is never drawn away, and the narrowing is recorded
    (`eligibility.only`), not hidden;
  - "decided" means `pick` is present, so a retry, a recovery or a roll
    resume never re-draws;
  - off writes nothing and calls nothing.
- **Failure behaviour:** a `ValueError` from `draw` (a defect: a bad
  eligibility or seed) leaves the answer standing, stores no `pick`, and
  logs at ERROR with the class name. It never fails the round.

**02-C2b — Turn intent.**

- **Inputs:** the assigned actor, the observable window and the actor's own
  brief.
- **Output:** a `stance` (or none), the `intent` record on the response, and
  the intent block, after the history, in that contribution's composed prompt.
- **Guarantees:**
  - asked only for a freshly composed NPC contribution with no typed note
    (a replayed turn composes fresh, and is asked);
  - frozen in the snapshot, and carried into a roll resume's recompose and
    so into Keep writing (3.1 rule 5);
  - never asked again by a retry, a resume, a reroll or Keep writing;
  - soft and bounded by one deadline per decision (3.2–3.4), with every
    exception caught (3.3);
  - placed after the history, so it can neither break prefix caching of
    the rest nor be switched off by a layout (6.5);
  - attributed with `response_id`.
- **Failure behaviour:** skipped, recorded with the reason, and the
  contribution proceeds unchanged.

**02-C3 — Turn plan.** C2b's contract with `disclosure` (argmax) and
`tension` (argmax) added in the same item and the same `decide()` call, plus
an `extra` slot of whole **items** that are owned, read and stored by their
provider (7.3).

- **Guarantees:** one pre-generation `decide()` per contribution, never two
  (the plan replaces the intent call). That is one request on a structured
  stage, and one request per item on a native one. The plan never changes a
  preset. Disclosure never grants knowledge. An extra item is never
  conditioned on the same call's stance.

**02-C4 — Decision as a tool from play.**

- **Guarantees:**
  - at most one honoured tool call per contribution, before any
    watcher-visible text;
  - offered on a fresh contribution and on a reroll, never on a retry, a
    roll resume or Keep writing; recorded per variant;
  - the model gets the selection, never the distribution;
  - tool turns never reach the transcript or the watcher;
  - every loop turn is metered under one run id;
  - offered only where 8.4 allows.
- **Failure behaviour:** a tool decision that fails returns
  `{"selected": null, "reason": "unanswered"}` to the model, and the
  contribution continues. A loop failure is the contribution's failure, as
  any stream failure is today.

**02-C5a — Epistemic access kit.** The route entry, the task
`epistemic-access`, `build_items` and `access_of`, the templates, the
`decide-epistemic-access` case and the 01d-C1 row.

- **Guarantees:** deterministic first; fail closed to `UNKNOWN`, including
  any item whose escalation did not answer (`access_of` reads
  `Decision.escalations`); escalation only on permissive answers (01d-C2a's
  answer filter); never sampled; nothing persisted.
- **What lands where:** the builder, the mapping, the templates and the
  recordings land in 02. The route, the `Case` registration, the policy row
  and the capture land with 11's call site (9.1).

**02-C5b — History relevance kit.** The route entry, the task
`history-rerank`, `build_items` and `grades_of`, the templates, the
`decide-history-rerank` case and the 01d-C1 row.

- **Guarantees:** ungraded is `None`, never a level; no escalation, no
  sampling; a `Rank` variant once 01e-C1 lands. What lands where follows
  9.1, with 09's call site.

**02-C6 — Shared play-decision rules.** Section 3, together with the play
gate of section 4.

- **Guarantees:** what must not change (3.1); soft resolution and no new
  pre-write refusal (3.2); failure, including any exception, never fails a
  turn (3.3); one total deadline per turn-path decision, `PLAN_CEILING_S` for
  02's own and a named one for every other spec's (3.4); attribution with
  `response_id` (3.5); settings off by default and exposed only after
  ratification (3.6, 4.3); privacy (3.7); the play gate with its seeded
  isolate, 01a's drain and declared `axes` (4.2, 4.3).

---

## 12. Interaction with repo rules

- **Routing and operation guards.** Each new task lands in the slice whose
  call site decides it (`test_operation_guard.py:996`,
  `test_routing_guard.py:659`). Each call names its task as a literal and
  passes `resolved=` (`test_operation_guard.py:988`). The thunk form of
  `_soft_resolved` keeps the literal visible
  (`routes/common.py:1530-1534`). `MIN_DECIDE_CALLS` rises with each slice.
- **Usage guard and metering.** `decide` opens its own meters.
  `test_usage_guard.py` already scans `inference.py`. 3.5's `response_id`
  passes through to `store.usage.meter` (`store/usage.py:629`). A native row
  is never modelled (`usage.py:896`), so the Costs card shows a native
  play-decision row as `cost_usd` or unpriced, never as a modelled figure.
  Per-player-post attribution holds because every call passes `post`.
- **Regex view.** Every item's transcript text goes through
  `store.regex.view.view(..., phase="prompt")`
  (`test_regex_prompt_guard.py`), reusing
  `response_protocol.observable_conversation`.
- **Locks and detached runs.** Decisions run inside the turn's detached run,
  outside the campaign lock, as `_select` does. The only writes are the
  round and response records, through `_round_state` and
  `responses.prepare`, which already take `campaign_lock`
  (`store/responses.py:311`). No new module mutates campaign state, so
  `store/locks.py`'s domain lists are unchanged, except
  `store/play_decisions.py`, which only reads. Each turn's terminal write
  still stamps the revision token through `streaming._turn_settled`.
- **Event loop.** Builders render templates, so they run in the threadpool,
  as `_selector_item` does (`character_turns.py:750`).
- **Prompt sections and goldens.** The intent block is a history-placed
  system message, not a catalog section (6.5), and adds nothing when off.
  `test_lore_golden.py` and every prompt golden stay untouched. A new test pins `compose_turn(plan=None)` to the pre-change
  bytes. `scripts/verify_templates.py` covers the new templates.
- **Frozen inference equivalence.** `tests/inference_baseline.py:420`,
  `inference_baseline_c.py:192` and `test_inference_equivalence.py:378`
  enumerate every task in `routing.TASK_ROUTE` against JSON fixtures that are
  never regenerated. `NEW_TASKS` (`test_inference_equivalence.py:53-75`)
  only admits a task equal to a sibling. A task on a `NO_LEGACY` route
  equals no sibling on a format-1 store: `response-selector` reads the
  legacy `route_scene` keys (`store/routing.py:88-91`). The checklist's
  shared structure for `routing.NO_LEGACY` carries its own test mechanism:
  a `NO_LEGACY_TASKS` set (every task on a `NO_LEGACY` route) that the
  frozen inference baselines exclude. Whichever spec lands the first such
  route (02-S2 here, or 01g, 09 or 10) adds both. `turn-intent`,
  `turn-plan`, `turn-tool-decision`, `epistemic-access` and `history-rerank`
  join the set with their routes.
  `evals/run.py`'s offline cases cover the new decide prompts.
- **Import guard.** The new store modules (`turn_plan.py`,
  `play_decisions.py`, `epistemic_access.py`, `history_rerank.py`) import
  `grimoire.decisions` and `prompts` like `response_protocol.py` does
  (`response_protocol.py:10`), all at module scope. Cross-package store
  imports bind submodules.
- **Pydantic and Android.** No new dependency. No pydantic models beyond
  plain fields: three new `ConfigUpdate` fields, and a new
  `CampaignPlayDecisions` body for `PUT /campaigns/{cid}/play-decisions`
  (3.6), dumped through `_dump`.
- **Privacy (3.7).** No log line carries item text. Eval fixtures are
  synthetic, with placeholder names.
- **Frontend.** One ConfigView section after ratification. One inspector line
  per response. No new keyboard binding. No list/detail page.
- **Costs rule.** Every report keeps the three money columns apart. The play
  gate's per-post figures are per column. A plan's cost is never folded into
  the generation's.

---

## 13. Tests and acceptance

**Speaker sampling (backend, `test_character_turns.py`,
`test_group_play_turns.py`, new `test_speaker_sampling.py`):**

- Off: the pick's result, the round record and the ledger are unchanged, and
  no `pick` is written.
- A native fake with a distribution and a patched `draws._seed_source`: the
  selection is the seeded one. The `pick` record is 01c's record, unchanged,
  and `draws.replay(pick) == pick["selected"]`.
- The issue mapping is unchanged by a draw: abstained → `(None, None)`;
  off-option → `INELIGIBLE`; unread, refused or error → `INVALID_HANDOFF`.
  Each still writes a `pick` (basis `none`).
- `NONE_KEY` is never selected (excluded). An answered none is abstained and
  hands back.
- Cutoff: the record's `eligibility.cutoff` is `1/(2n)` and it sets no
  floor. An actor reported below the cutoff is never selected over many
  seeds. A cutoff that removes every addressed actor re-draws with no
  `only` or `cutoff` and keeps a speaker (5.2 rule 3).
- Addressed: a post naming Mara gives `only=("…:mara",)`. Naming both Mara
  and Winifred gives both. A typed note naming Winifred narrows to Winifred.
  An answer outside `addressed` with no usable report re-draws with
  `only=None` and keeps the answer.
- A retry, a recovery and a roll resume of a round with `pick` present do not
  call `_select` again, including when the pick was none.
- `DecideRequestError` still becomes `INVALID_HANDOFF`. `LLMError` still
  propagates and rescues.

**Turn intent and plan (`test_turn_plan.py`, route tests with
`llm_fakes.py` only):**

- Off: no `turn-intent` or `turn-plan` row, no `intent` key, and
  `compose_turn` bytes equal to `main`'s.
- On: one decide call before `response_start`. The block is in the
  generation's prompt and snapshot, after the history. The record carries
  `response_id`, and the ledger row carries the same id.
- Skips: `grimoire`; resume of a pending response; roll continuation; typed
  note; missing key (soft, `skipped: "missing_key"`, and `decide` not
  called); `incapable`; deadline (patched clock, including a second call
  refused unsent); `LLMError`; abstained (no block).
- **Any exception:** a builder that raises `RuntimeError` still produces a
  reply, records `skipped: "error:RuntimeError"`, and a retry does not loop.
- **Roll resume:** a reply that pauses for a roll and resumes carries the
  same intent block in its resume snapshot, with no second decide call. Keep
  writing on that split reply carries it too.
- Reroll and retry reuse the snapshot and make no decide call. A replayed
  turn asks one.
- No `on_timeout` reaches `client.note_outcome` on a deadline overrun.
- Plan: one call carrying three questions, plus extra items when given.
  Disclosure and tension are argmax even when a distribution is present.
  Extra items are stored under their owner's key and rendered by nobody.
- A prompt layout that disables every catalog section still carries the
  intent block (it is not a section).

**Tool (`test_play_tool.py`, after 01g):** offered only under 8.4,
including on a reroll and not on a retry; one honoured call; a call after
watcher-visible text declined and recorded, while a call after a hidden
perception fence is honoured; the record stored per variant and unchanged by
a later reroll; cap answers
`cap`; the selection is returned without the distribution; tool turns absent
from the transcript and from the watcher's input; one meter per loop turn
under one run id.

**Kits (`test_epistemic_access.py`, `test_history_rerank.py`):**
fail-closed mapping for every reason and detail; a low-margin `known` whose
escalation `outcome` is `"failed"` or `"skipped"` maps to `UNKNOWN`;
escalation only on permissive answers (with 01d's helper faked); the
unregistered case's recordings replay; ungraded is `None`; builders
refuse nothing a caller could legally pass; `decide-*` replay cases green.

**Guards:** the routing and operation guards pass at each slice. A planted
`require_inference("turn-intent", ...)` without its route fails, which proves
the kit-not-route rule.

**Evals:** the new `decide-*` cases replay in `make check`. `decide-speaker`
has a native recording with a distribution. `evals/run.py --live --play
<feature>` refuses without `--live`, writes nothing to the real store, seeds
each isolate with exactly the configuration's selections, drains before it
harvests (01a section 6), and files rows only under 01a-C2's scope, carrying
the fixture campaign's id.

**Acceptance for the spec as a whole:**

1. Section 1 is accurate at the slice's baseline.
2. Each feature lands off and byte-identical.
3. Each feature's switch appears only with a ratified live report.
4. 09 and 11 can land their routes from the kits without re-specifying them.
5. 13 can cite 02-C2a, 02-C3's slot and 02-C6 for its NPC Action choice.

---

## Slices

Landing order within this spec: S1 → S2 → S3 → S4 → S5 → S6 → S7 → S8.
S7 (kits) needs nothing after S1 and can go in parallel with S2–S6. S5 (play
gate) needs only S2 and can go in parallel with S3 and S4.

Every switch lands **dark**. The backend honours the key, no UI shows it,
and it defaults to off, until its play-gate report is ratified (4.3). S6
exposes the ratified ones. A route lands only together with its call site,
so each new task's route entry is in the slice that first decides it.

### 02-S1: Record and plumbing

- **Delivers:** 02-C1 (full); 02-C6 (part: 3.1's off-is-byte-identical
  rule, 3.2's soft resolution, 3.3's catch-all, 3.4's `plan_deadline()` and
  `DeadlineRefused`, 3.5's attribution, and 3.6's settings readers with no
  UI).
- **Needs (this spec):** none
- **Needs (other specs):** none (01 is landed)
- **Scope:**
  - Section 1 confirmed at the slice's baseline.
  - `inference.decide`, `run_stages` and `_Call` gain `response_id=`.
    `store.responses` gains `mint_id()` and `prepare(..., response_id=)`.
    Both are checklist shared structures, so if another spec landed them
    first, this slice reuses them.
  - `store/play_decisions.py` with the three keys in `_CONFIG_KEYS` and
    `ConfigUpdate`. No UI, no campaign route yet.
  - `plan_deadline()` and `DeadlineRefused` in `routes/common.py`.
  - `compose_turn` and `compose_director_turn` gain `plan=`, which adds
    nothing for `None`.
  - Nothing asks a new decision yet.
- **Acceptance:**
  - `compose_turn(plan=None)` equals the pre-change bytes, and every prompt
    golden is untouched;
  - a `decide(response_id=...)` call files the id on each ledger row;
  - the settings readers follow the global/campaign rule of 3.6;
  - `plan_deadline` refuses a call unsent once the deadline has passed, and
    never reaches `client.note_outcome`.
- **Size:** M

### 02-S2: Turn intent, dark (answers only)

- **Delivers:** 02-C2b (part: the stance question, the call site, the
  `intent` record and the block after the history, the roll-resume carry,
  every skip, with the answer used as given and no draw).
- **Needs (this spec):** 02-S1 (H)
- **Needs (other specs):**
  - 01b-C1 (H: the one capture helper, used at a new decide site);
  - 01b-C2 (H: capture filed after the call settles, off the decide path);
  - `routing.NO_LEGACY` with `NO_LEGACY_TASKS` (S: a checklist shared
    structure). If no spec has landed it, this slice adds it.
- **Scope:**
  - `store/turn_plan.py` (intent), its templates, and the `turn_plan` route
    with `turn-intent`, landed together with its call site in
    `_round_frames`. The routing guards force the route and the call site
    into one slice.
  - The `turn_intent.j2` block, placed after the history as an author's
    note is.
  - `_prepare`'s resume branch carries `record.get("intent")` into the roll
    resume's recompose.
  - The `decide-turn-intent` case and recordings.
  - The switch value `intent` works with no UI.
- **Acceptance:** section 13's "Turn intent and plan" items for the intent:
  - off is byte-identical and files no row;
  - one call before `response_start`, with `response_id` on the row;
  - every skip, including `missing_key` without calling `decide`, the
    deadline, and any exception (`error:RuntimeError`, and a retry that does
    not loop);
  - a roll resume and Keep writing carry the block with no second call;
  - reroll and retry make no call, and a replay makes one;
  - a layout that disables every catalog section still carries the block;
  - the routing and operation guards and the frozen-equivalence exclusion
    pass.
- **Size:** L

### 02-S3: Sampling (speaker and stance)

- **Delivers:** 02-C2a (full); 02-C2b (full: the stance drawn through 01c).
- **Needs (this spec):** 02-S2 (H)
- **Needs (other specs):**
  - 01c-C1 (H: `reports_distribution(resolved)`);
  - 01c-C2 (H: `draws.draw` with `Eligibility(only, exclude, cutoff)`,
    `new_seed()` and its `_seed_source` seam);
  - 01c-C3 (H: the v1 replay record, and `draws.replay`);
  - 01c-C4 (H: no draw on abstained, refused, unreadable or error, or with
    no usable report);
  - 01d-C1 (H: `TaskPolicy` with `samples`, `question` and
    `reads_declines`, refusing `samples` together with `low_margin`).
- **Scope:**
  - The `_select` draw with `Eligibility(exclude=(NONE_KEY,),
    only=addressed, cutoff=1/(2n))` and the re-draw of 5.2 rule 3.
  - `pick` written with the speaker; `_first_actor` treats a present
    `pick` as decided.
  - `group_play.named(..., author_ref=)`, and `response_protocol.addressed`
    and `pick_eligibility`.
  - The stance draw in the intent path.
  - The `response-selector` and `turn-intent` `TaskPolicy` rows, with
    escalation off.
  - The `decide-speaker` native recording with a distribution.
  - `decide_speaker_sampling` works with no UI.
- **Acceptance:** section 13's "Speaker sampling" items:
  - off is unchanged;
  - a seeded draw, with `draws.replay(pick) == pick["selected"]`;
  - the issue mapping is unchanged;
  - the none is never selected;
  - the cutoff is honoured;
  - the addressed narrowing, a typed note, and the re-draw;
  - no re-draw on retry, recovery or resume.

  For the stance: its `draw` record, and the answer used when there is no
  report.
- **Size:** M

### 02-S4: Turn plan, dark

- **Delivers:** 02-C3 (full).
- **Needs (this spec):** 02-S3 (H)
- **Needs (other specs):** none. 01e-C3b (`Joint`) is not used here; a
  joint speaker-and-stance question is open question 7.
- **Scope:**
  - The `turn-plan` task on the `turn_plan` route, landed with its call
    site.
  - `plan_item` (stance, disclosure, tension) and `plan_items` with the
    `extra` item slot of 7.3.
  - The record's `disclosure` and `tension`, which are never drawn.
  - The `decide-turn-plan` case.
  - The switch value `plan` replaces the intent call, and works with no UI.
- **Acceptance:**
  - one `decide()` per contribution;
  - three questions, plus extra items when given;
  - disclosure and tension take the answer even when a distribution is
    present;
  - extra items are stored under their owner's key and rendered by nobody;
  - no contribution pays for both an intent and a plan.
- **Size:** M

### 02-S5: Play gate harness

- **Delivers:** 02-C6 (part: the play gate of section 4).
- **Needs (this spec):** 02-S2 (H). S3 and S4 add configurations as they
  land.
- **Needs (other specs):**
  - 01a-C1 (H: a play case summed across its tasks, per money column and
    never across columns);
  - 01a-C2 (H: metering inside a throwaway home with the tripwire, rows
    stamped `scope: "eval"`, and the drain);
  - 01a-C3 (H: `--repeat`, `--out`, `--compare` and the open `axes`).
- **Scope:**
  - `evals/play.py` with synthetic fixtures (placeholder names only).
  - `evals/run.py --live --play <feature>`. It seeds each isolate with
    exactly the configuration's selections and switch, drains before it
    harvests, and declares `feature`, `scene_selection`,
    `decision_selection`, `sampling` and `cutoff` as `axes`.
  - The feature graders of 5.7, 6.6 and 7.5: mass on intended over the
    drawn weights, addressed override, departure, spread, hand-back,
    adherence, stance diversity and the leak check.
  - The 4.3 hard criteria, including "on is really on".
  - Nothing here runs in `make check` beyond the harness's offline tests.
- **Acceptance:**
  - `--play` refuses without `--live`;
  - it writes nothing to the real store;
  - an isolate holds exactly the selected providers;
  - a configuration that records `skipped` fails the gate;
  - rows carry the fixture campaign's id under the eval scope;
  - the leak check passes offline on recordings.
- **Size:** L

### 02-S6: Exposure of ratified features

- **Delivers:** 02-C6 (full: the "Play decisions" settings, the campaign
  override, and the rule that a switch is shown only after ratification).
- **Needs (this spec):** 02-S5 (H), plus a ratified live report for each
  switch shown. A feature without one stays dark in this slice.
- **Needs (other specs):** none
- **Scope:**
  - The ConfigView "Play decisions" section, showing only ratified
    switches, with the cost and provider disclosure text of 3.6 and 3.7.
  - `CampaignPlayDecisions` at `PUT /campaigns/{cid}/play-decisions`.
  - The inspector's "Approach" line on a response.
  - Defaults stay `off`.
- **Acceptance:**
  - a ratified switch round-trips through `PUT /config` and the campaign
    route;
  - an unratified switch has no control;
  - frontend tests for the section and the inspector line;
  - the ratified report is attached to the PR.
- **Size:** M

### 02-S7: Decide kits for 11 and 09

- **Delivers:** 02-C5a (full); 02-C5b (full).
- **Needs (this spec):** 02-S1 (S: only for shared helpers; it can land
  right after S1).
- **Needs (other specs):**
  - 01d-C2b (H: `Decision.escalations[*].outcome`, read by `access_of`);
  - 01e-C1 (S: `Rank`. Until it lands, `history_rerank` offers the Score
    form only and `build_items_ranked` is absent).
- **Scope:**
  - `store/epistemic_access.py` and `store/history_rerank.py`, with their
    templates, `verify_templates.py` entries, recordings, and `Case`
    definitions held outside `CASES`.
  - No routes, no registered cases, no `TaskPolicy` rows and no capture:
    those land with 11's and 09's call sites (9.1). The routing guards and
    `test_evals.py:67-74` force that split.
- **Acceptance:** the kit items of section 13:
  - fail-closed mapping for every reason;
  - a low-margin `known` whose escalation `outcome` is `failed` or
    `skipped` maps to `UNKNOWN`;
  - ungraded is `None`;
  - the unregistered cases' recordings replay;
  - the routing guards stay green with no new route.
- **Size:** M

### 02-S8: Decision as a tool from play, dark

- **Delivers:** 02-C4 (full).
- **Needs (this spec):** 02-S2 (H: the `turn_plan` route); 02-S5 (H: its
  play gate).
- **Needs (other specs):**
  - 01g-C1 (H: the `tools` capability, and its known-`no` refusal);
  - 01g-C2a (H: the loop primitive over OpenRouter, OpenAI-compatible and
    Anthropic, where the caller executes the tool);
  - 01g-C2b (S: Claude Agent SDK. Until it lands, `claude` reads as unable
    to call tools and is never offered the tool);
  - 01g-C3 (H: a ledger row per loop turn with `run_id` and `loop_turn`);
  - 01g-C4 (H: the run budget);
  - 01g-C5 (H: the decide tool, metered under the caller-named
    `turn-tool-decision`);
  - 01g-C6 (H: a streamed final turn, and declining a tool call after
    visible text);
  - 01c-C2 (H: `draws.draw` for the selection).
- **Scope:**
  - The `turn-tool-decision` task on the `turn_plan` route, landed with its
    call site.
  - The offer rules of 8.4, the watcher-visible decline of 8.2, and the
    caps of 8.3.
  - The per-variant `tool` record.
  - `decide_play_tool` works with no UI. Its switch is added to the
    Play decisions section only after its report is ratified, in a separate
    PR under 02-C6's rule (no new design).
- **Acceptance:** section 13's tool items:
  - offered under 8.4, including on a reroll and not on a retry;
  - one honoured call;
  - a decline after visible text, but not after a hidden perception fence;
  - the per-variant record;
  - `cap`;
  - selection only, never the distribution;
  - no tool turn in the transcript or the watcher;
  - one meter per loop turn.
- **Size:** L

---

## 15. Non-goals

- **Shadow mode in the product.** Superseded (1.3). Two resolvers per call are
  only ever run in evals.
- **Native-first for a generating model.** It stays 01 section 16's decision.
- **Re-tuning or re-gating F–H's sites,** beyond C2a's optional sampling.
- **Switching on escalation for continuity identity or reconcile.** 01d's
  candidate table names identity first. Its switch is one policy row in a
  change of its own, under 01d's bar (section 10).
- **Mechanics-driven speaker eligibility** (the draft's "incapacitated where
  mechanics says so → ineligible"). It needs 13-C2a's conditions. 13's open
  question 9 defers it to a later 02 revision, and 02 agrees: until then
  eligibility is presence and sitting out, as today.
- **Faction and offscreen behaviour.** It follows 13's Actions and a later
  spec. An offscreen scene keeps today's director-turn path.
- **NPC Action choice itself.** That is 13-C3. 02 provides the pattern
  (02-C2a), the slot (02-C3) and the rules (02-C6).
- **A plan at round open** (rejected, 7.1). **A speaker inside the plan**
  (waits on 01e-C3, 7.4).
- **Automatically lowering reasoning when a plan is on** (7.5).
- **Exposing distributions to the generating model** (8.1).
- **Default-on for anything here.**
- **Changing the perception rider,** the `speaker_turn_taking` hint, the
  greeting opener's fan-out or group play's talkativeness rolls.

---

## 16. Open questions

1. **Should the speaker pick get 3.4's deadline too?** Today a stalled
   Decision model holds the turn until the facade's idle timeout.
   *Recommendation:* yes, but as **its own slice**, not in 02-S1, because it
   changes a landed site while every feature is off. A timeout would map to
   `INVALID_HANDOFF`, which returns control to the player (the existing
   failure mode) rather than failing the round. It needs the user's yes.
2. **Should the draw exclude the none?** (5.2 rule 2.) *Decided:* yes,
   through `Eligibility(exclude=(NONE_KEY,))`, which 01c allows and records.
   Hand-back is a player-agency signal, and randomising it lets NPCs talk past
   a pending player decision.
3. **Should a reroll re-ask the intent?** *Recommendation:* no for 02 (frozen
   snapshot, 3.1 rule 5). A "reroll with a fresh approach" control would store
   the block outside the snapshot as an appended block and needs its own
   design.
4. **A global switch plus a campaign override, or campaign only?**
   *Recommendation:* both, mirroring the tracker (3.6). A cost knob a user sets
   per story should not need a global change.
5. **Should there be a `deciding` SSE frame** before `response_start`, so the
   client can show that the plan is running? *Recommendation:* not in 02. The
   responding indicator already covers the wait. Revisit if the play gate's
   added latency is noticeable.
6. **The stance vocabulary.** *Recommendation:* ship 6.1's seven and let the
   adherence grader and stance diversity revise it before exposure (02-S6). The
   options live in templates, so revising them is a template edit plus a
   recording update.
7. **Joint speaker and stance once 01e-C3 lands.** *Recommendation:* a
   follow-up slice behind its own play gate, measured against the two-call
   path on added latency.
8. **Missing edges.** None remain. Each upstream item 02 asked for now has a
   contract in the checklist, cited where it is used:
   - 01a-C1 (a play case summed across its tasks, 4.2);
   - 01c-C1 (`reports_distribution(resolved)`, 5.6);
   - 01d-C1 (`TaskPolicy` refuses sampling with `low_margin`, 10);
   - 01d-C2a (the answer filter, 9.2);
   - 01g-C6 (a streamed final turn and declining a tool call after text,
     8.6).

   The edges 09 ← 02-C5b and 10 ← 02-C5b are both in the checklist.
   **Edge notes for the coordinator:**
   - 13 uses 02-C3's `extra` slot (7.3), but the checklist has no `13 ←
     02-C3 (S)` edge;
   - the checklist lists 01b as hard for C5, but the kits make no call; the
     capture belongs to 09's and 11's call sites (9.1);
   - 11 section 5.4 runs its turn-path stage under `llm_call_budget`, which
     02-C6's deadline rule (3.4) does not allow.
9. **Floor or cutoff for the speaker?** *Closed.* 01c now has a separate
   `Eligibility.cutoff`. It excludes keys whose reported probability is
   below it, after `only` and `exclude` and before any floor, and a cutoff
   that empties the set gives `basis: none`, `why: cutoff` (01c section
   5.3). That matches 02's original intent: cut the long tail the model
   argued against, rather than lift it. 02 uses
   `cutoff=1/(2n)` with no floor, for the speaker and the stance, and C2a's
   gate runs `cutoff` as an axis (`0` vs `1/(2n)`).

---

## 17. Review record

**Substitute adversarial review, 2026-10-10** (`reviews/02.md`: 3 blocking,
11 should-fix, 17 minor). Each item was checked against the code at the
baseline, and against the revised 01a, 01c and 01d specs.

| Item | Finding | Disposition |
|---|---|---|
| B1 | 02's own support, floor, record shape and 63-bit seed contradict 01c | **Fixed, by the coordinator's ruling** (01c owns the sampler). `draws.draw` with `Eligibility(exclude=(NONE_KEY,), only=addressed, cutoff=1/(2n))` (01c's cutoff, no floor; open question 9 closed), `new_seed()`, 01c's record unchanged under `pick`, "decided" means the record is present (5.2-5.4, 6.5, 11). The same applies to the stance. |
| B2 | A failed or skipped hop leaves `known` in `items`, and `access_of` cannot see it | **Fixed.** `access_of(questions, decision)` reads `Decision.escalations[*].outcome`; not answered maps to `UNKNOWN` (9.2, 9.4, 10, 13). |
| B3 | A roll resume recomposes, so the intent drops out | **Fixed.** 1.4 rule 4 corrected. The resume branch passes `record.get("intent")` into `_compose`; tests added (3.1 rule 5, 13). |
| S1 | A per-call ceiling is several times the stated wait, and `_noting` blames healthy connections | **Fixed.** One deadline per decision, `DeadlineRefused` unsent past it, no `_noting`, worst case stated (3.4). |
| S2 | Only `LLMError` and `DecideRequestError` are caught | **Fixed.** `except Exception` around the whole step (3.3). |
| S3 | Kit cases and policy rows fail `make check` without a route | **Fixed.** What lands in 02-S7 versus the consumer's slice is spelled out (9.1). |
| S4 | A `NO_LEGACY` route breaks the frozen equivalence baseline | **Fixed.** The shared structure's `NO_LEGACY_TASKS` set, excluded by the frozen baselines, landed with whichever spec adds the first such route (12). |
| S5 | The play gate cannot apply a Decision selection to the turn path | **Fixed.** The isolate is seeded with the configuration's selections, and "on is really on" is a hard criterion (4.2, 4.3). |
| S6 | C4's reroll story conflicts with variants | **Fixed.** Recorded per variant; reroll offers the tool, retry and resume do not (8.4, 8.6). |
| S7 | The addressed narrowing is a hidden override, with a wrong citation | **Fixed.** Stated and recorded as an override, graded on its own line, mass on intended computed over the drawn weights, citation `response_actor.j2:74-75` (5.2, 5.7). |
| S8 | The `extra` slot disagrees with 13's use | **Fixed.** Extra whole items, one `decide()`; the native request count and the no-conditioning limit are stated (7.3, 11). |
| S9 | A layout can disable the section while the call is still paid | **Fixed by S10's move.** The block is no longer a catalog section, so a layout cannot drop it (6.5). |
| S10 | Placement breaks prefix caching and skews 7.5 | **Fixed.** Placed after the history like an author's note at depth 0; cache-read tokens reported (6.5). |
| S11 | The ceiling rule is ambiguous for 09 and 11 | **Fixed.** 02-C6 requires a named total deadline per turn-path decision; the 11 discrepancy is an edge note (3.4, 16). |
| M1 | The structured path is `client.complete(schema=)`, not `generate(schema=)` | **Fixed** (1.2). |
| M2 | One meter per call, not per chunk | **Fixed** (1.2). |
| M3 | The "held by a guard" claim is too strong | **Fixed** (1, 11). |
| M4 | `_named` takes no author | **Fixed.** New signature, author mapped to its ref (5.2). |
| M5 | Settings fields and routes not named | **Fixed** (3.6, 12). |
| M6 | 01s has no hiding toggle, so the route shows during the dark period | **Fixed** (3.6). |
| M7 | Mechanics eligibility dropped silently | **Fixed.** Listed as a non-goal (15). |
| M8 | `_soft_resolved`'s `None` not handled in the snippet | **Fixed** (3.2, 6.4). |
| M9 | Open question 1 contradicted "not re-litigating F–H" | **Fixed.** Its own slice (16). |
| M10 | 1.4.1 contradicted 1.4.2 on parsing prose | **Fixed** (1.4). |
| M11 | The card brief goes to another provider | **Fixed.** Disclosed in the switch text (3.7). |
| M12 | A replay asks a new intent per turn | **Fixed.** Stated in 6.3 and 11. |
| M13 | "Visible text" versus the hidden perception fence | **Fixed.** The watcher's visible text (8.2). |
| M14 | Sampling `deceive` makes lies absorb records without a signal | **Rejected.** A character lying is ordinary fiction on `main` today, with or without an intent, and how absorb treats an in-fiction lie is not 02's to change. The gate's stance diversity and adherence report how often `deceive` is drawn. |
| M15 | Checklist edges: `13 ← 02-C3` missing; 01b hard for C5 | **Noted** as edge notes for the coordinator (16, question 8). |
| M16 | The capture is on the pre-first-token path | **Fixed.** Counted in the worst case and in "added pre-generation time" (3.4). |
| M17 | Readers must use `.get` | **Fixed** (5.3, 6.5). |

**Slices added (8 slices):** section 14's table was converted to the
slices format. The old slice letters map as A→S1, B→S2, D→S3, C→S4, E→S5,
F→S6, G→S7 and H→S8. Sampling now lands before the plan, because 02-C3
includes C2b's drawn stance. 02-S7 no longer waits on the policy rows:
those land with the consumer's call site (9.1).

**Also folded, from 01a and 01b:**

- the play harness honours 01a's drain;
- rows carry the fixture campaign's id, never a real campaign's;
- each play gate declares its configuration as 01a's open `axes` (4.2).
