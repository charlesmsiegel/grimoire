# 02. Decision integration: what slices F–H left, and the play-facing uses

**Status:** Draft — spec gate (`/codex:adversarial-review`) pending.
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

| Contract | Provided by | What this spec uses it for | Hard or soft |
|---|---|---|---|
| 01 (landed) | 01, slices F–I | `inference.decide`, `decisions.Item/Answer/Decision`, the route registry, the Decision role, the native chain, `--gate` and `--decide-backend`. Section 1 is the record of it. | Hard |
| 01a-C1 | 01a | Wall time, tokens and the three money columns per decide and generate call, which the play gate (section 4) reports per post. | Hard for every play gate |
| 01a-C2 | 01a | Live eval rows filed under an eval scope, so a play-gate run never lands in a campaign's spend. | Hard for every play gate |
| 01a-C3 | 01a | One comparison table across configurations (feature off vs on; the reasoning matrix of section 7.5). | Hard for every play gate |
| 01b-C1, 01b-C2 | 01b | A prompt-log capture at each new decide site (intent, plan, tool, relevance, epistemic), off the decide path. | Hard for C2b, C3, C4, C5 |
| 01c-C1 | 01c | Whether a generating Decision model returns a distribution at all. Decides whether sampling is ever live on a structured backend. | Hard for C2 sampling |
| 01c-C2 | 01c | The one sampler `(distribution, seed) → selected`. | Hard for C2 sampling |
| 01c-C3 | 01c | The replay record, persisted beside the round's speaker and the response's intent. | Hard for C2 sampling |
| 01c-C4 | 01c | An abstention, a refusal or a missing distribution is never sampled. Section 5 builds the speaker invariants on it. | Hard for C2 sampling |
| 01d-C1 | 01d | The per-task policy (`routing.TaskPolicy`, with 01c's `samples` field). Section 10 states the row each new task needs. | Hard for C5a; hard for declaring every new task |
| 01d-C2 | 01d | The one-hop escalation helper, used by epistemic access only. | Hard for C5a |
| 01d-C3 | 01d | Per-task, per-backend thresholds for that escalation. | Hard for C5a |
| 01e-C1, 01e-C2 | 01e | `Rank` and a finer `Score`, which history relevance (C5b) switches to when they land. | Soft |
| 01e-C3 | 01e | Joint choice, which would let one question pick the speaker and the stance together (section 7.4). | Soft |
| 01g-C1..C4 | 01g | The `tools` capability, the loop primitive, its metering and its run budget. | Hard for C4 only |
| 01g-C5 | 01g | The Decision-as-tool shim. C4 is its play-side policy. | Soft for 02 as a whole, hard for C4 |

Nothing in C1, C2, C3 or C5 waits on 01g. C4 is a separate, last slice.

## Required by

| Contract (provided here) | Consumer | What the consumer uses it for |
|---|---|---|
| 02-C1 | 01a, 01b, 01c, 01d, 09, 11, 12, 13 | The baseline of what `decide` already does in play and absorb, so no consumer re-specifies a landed call site. |
| 02-C2a | 13 (13-C3, soft seam) | The pattern and code for a sampled choice among legal options in play: eligibility decided by code, the answer sampled by Grimoire, the record kept with the outcome. |
| 02-C2b | 13 (13-C3) | A Decision-chosen intent on the contribution, which an NPC Action choice can be conditioned on. |
| 02-C3 | 13 (13-C3) | The plan item's extension slot, so a legal-Action question rides the same call rather than adding one. |
| 02-C4 | 12 | The play-side caps and placement rules for a decision asked as a tool. 12's investigation applies the same rules out of play. |
| 02-C5a | 11 (11-C1) | The `epistemic` route, its task, item builder, mapping and fail-closed rule. |
| 02-C5b | 09 (09-C1, 09-C2) | The `history_check` route, its task, item builder and mapping, with ungraded kept distinct from irrelevant. |
| 02-C6 | 13, 12, and any later play decision | The shared rules every play-facing decision keeps (section 3): soft resolution, byte-identical when off, attribution, the ceiling and the play gate. |

**Changes from the checklist:** C2 is split into **C2a** (sampled next
speaker) and **C2b** (turn intent). C5 is split into **C5a** (epistemic
access, for 11) and **C5b** (history relevance, for 09). **C6** is new: the
shared rules for play decisions, stated once so 13's NPC Action choice and
12's tool use can cite them rather than restate them.

---

## 1. Current state (reconciled against main) — 02-C1

This section is contract 02-C1. It records what the bundle draft asked for,
what 01's slices F–H landed in its place, and what is still open. Every
"landed" row is held by an existing guard or suite (named in the row), so
the record cannot drift silently.

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
| Provider-neutral `decide()` | `inference.decide` over a chain of stages; `run_stages` | `inference.py:684`, `:595`, `stages` at `:147` | One meter per structured chunk or native item (`inference.py:349`). The chain moves on only on a **failed call**, never on an answer (`run_stages` docstring, `inference.py:602-605`). |
| Question vocabulary | `Predicate`, `Choice(allow_none)`, `Score(levels 2–10)`; several questions per `Item` | `decisions.py:181`, `:189`, `:200`, `:212`; level bounds `:94` | **No `Rank`.** That is now 01e-C1. |
| Normalised result | `Decision` → `ItemResult` → `Answer` with `reason`, `detail`, `probability`, `distribution` | `decisions.py:221`, `:258`, `:277` | No `profile_id`. What answered is `provider`, `model` and `served` (`decisions.py:284-297`). `backend` is per item. |
| No fabricated probabilities | `Answer.probability` and `.distribution` are only what a backend reported | `decisions.py:227-229`; native reading `native_answer` at `:866` | Only native reports any. A structured answer carries none. |
| Native adapters | OpenRouter `/api/alpha/decisions`, OpenAI `{base_url}/decisions` | `openrouter.py:40`, `:342`; `openai_compatible.py:426`; adapters `adapters.py:149`, `:195` | Native serves **only a model that cannot generate** (`resolve.native_only`, `store/inference/resolve.py:922`; `decision_mode` at `:931`). A model that generates stays structured until native wins on evals (01 section 16). |
| Structured fallback | `generate(schema=)`, the schema always in the prompt, provider structured mode per attempt | `inference.py:119`; `_flag_structured` at `resolve.py:955` | Chunks of eight items (`decisions.MAX_ITEMS_PER_CALL`, `decisions.py:101`). |
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
   the meter at `:1053-1060`). No decision text is ever shown, stored in the
   transcript, or parsed out of prose.
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
4. **Frozen snapshots.** `_prepare` composes the prompt under the campaign
   lock and stores `messages.snapshot()` on the response record
   (`character_turns.py:476-520`). A reroll replays that snapshot and never
   recomposes from live state (character-turns spec, "Reviewed implementation
   decisions").
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
   (`character_turns.py:1050-1063`). A decision may add a *section* to the
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
5. **Frozen snapshots hold.** A section a decision adds is part of the
   composed prompt, so it is part of the snapshot. A reroll, a Keep writing,
   a retry and a roll resume reuse it and **ask nothing again**.
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
off. Only the speaker pick keeps its 409 before `post_chat` writes, because
only the pick is required for a round to have a speaker.

**Why:** a turn the player sent is the request. A feature that can make it
fail before generating would turn an optional improvement into a new way
for play to break. That is the reasoning behind `_soft_inference` for
absorb's secondary phases (`routes/common.py:1545-1556`).

### 3.3 Failure never fails the turn

A failed, timed-out or unreadable play decision (other than the pick) is
recorded and dropped. The meter has already filed the call's `error` row
(CLAUDE.md, "Instrument LLM failures at `usage.Meter.done`"). The
contribution then composes without the section. A decision that answered
`abstained` or `refused` is an answer of "no intent", not a failure, and is
not retried elsewhere (1.2: the chain moves on only on a failed call).

### 3.4 A ceiling on every pre-generation decision

`_select` today runs with no `around`, so the facade's idle timeout is its
only bound. Every new pre-generation decision runs under
`around=lambda call, holder: _bounded_call(call, ceiling=plan_ceiling(),
on_timeout=_noting(client, resolved, holder))` (`routes/common.py:544`):

```python
#: Seconds a pre-generation decision may hold the contribution it shapes.
#: Argued structurally: the player is waiting on a stream that has not
#: started, and an optional decision must cost less waiting than the reply it
#: shapes. Well under `llm_call_budget` (default 300 s, `store/config.py:159`),
#: which bounds a whole non-streaming generation. To be tuned against 01a's
#: latency reports, never against a real library.
PLAN_CEILING_S = 20.0

def plan_ceiling() -> float:
    budget = store.config.llm_call_budget()
    return PLAN_CEILING_S if budget <= 0 else min(PLAN_CEILING_S, budget)
```

On timeout the call is filed `error/timeout` by its meter and the
contribution proceeds without it. The pick keeps today's behaviour, with no
ceiling (an open question, section 16).

### 3.5 Attribution

Each new call passes `campaign`, `scene`, `post` and `round_id`. Per-response
calls (intent, plan, tool) also pass `response_id`. Today `decide()` takes
no `response_id`, and the response id is minted inside
`store.responses.prepare`, which runs *after* the decision. 02 makes two
small changes:

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
`read_config` silently drops them. They are read through one module,
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
of their own. The Models page lists every route the server reports, and
01s's "Advanced" toggle hides rarely used ones
(`2026-10-09-inference-settings-group-design.md`, section 3).

Turning a switch on starts no call. CLAUDE.md's "settings surface never
spends unasked" rule covers a call the settings page starts, and this one
starts none. The switch's text is the disclosure.

### 3.7 Privacy

A decision item is built from the same campaign text the turn already sends
to a model. It is never written to a log line. `logs.record` rows carry
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
a feature setting. Each selection is applied per run through the same seam
`--provider/--model` use (`override_inference`), so nothing is written to
settings. With `--repeat N` (default 5) every fixture runs N times per
configuration, because one live run is an anecdote (`evals/README.md`,
"`turn-taking` and issue #82").

It reports, through 01a-C3's comparison table, per configuration:

- per post: the wall time of each call (01a-C1), split into **added
  pre-generation time** (the decisions before the first token) and
  generation time;
- tokens and the three money columns per task, never added together (01a-C1;
  CLAUDE.md "Costs");
- the existing turn graders' pass rates (`turn-taking`, `roll-fence`,
  `scene-length`, `owned-lore`);
- the feature's own graders (5.7, 6.6, 7.5, 8.6, 9.3).

Rows are filed under 01a-C2's eval scope, never against a campaign.

**Requirement on 01a (flagged as an edge):** one play fixture is several
calls under several tasks (`turn-plan`, then `chat`). 01a-C1's per-case
reporting must aggregate across a case's tasks, and 01a-C2's eval scope must
accept rows from more than one task in one case. If 01a reports per task
only, 02 builds the per-post sum (one row per task, never a sum across the
money columns) on top of 01a's rows.

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

Sampling runs **after** `decide` returns and `selection_of` would have read
the answer. It changes only *which offered speaker* an already-answered pick
names. Order matters:

1. **Not sampled at all** when any of these holds. The pick is the answer, as
   today, and the record says why:
   - the switch is off (no record is written at all);
   - the answer was not read as a speaker (abstained, refused, unreadable,
     error, `NOT_AN_OPTION`). Those go to `selection_of` unchanged, so an
     explicit null still hands control back, and every issue string is as
     today (01c-C4);
   - the answer carries no distribution (structured today; see 5.6);
   - the round carries a player-typed note (`round_record["typed_note"]`).
     A director note may say who should act, and the selector is what reads
     it (group-play spec, "Continue and director notes"). Sampling could
     overrule the player's own direction.
2. **The support** is the distribution with three things removed:
   - **the reserved none (`decisions.NONE_KEY`)**. Whether anyone speaks is
     the argmax's decision; sampling decides only who. Sampling "none" would
     sometimes end a chain at random, or let NPCs talk past a pending
     player decision ("Choose null when ... the player's decision is needed",
     `templates/scene/response_selector_question.j2`);
   - **any option below the floor**: half the uniform share,
     `1 / (2 · n)`, where `n` counts the offered options without the none.
     Argued structurally: an option the model rated below half of
     indifference is one it argued against. To be tuned through 01a's
     reports;
   - **anyone not addressed, when someone is.** If the newest
     non-synthetic post in the pick's window names one or more eligible
     actors (other than its own author), the support is narrowed to those.
     The rule is "Being addressed determines who starts"
     (`templates/scene/response_steer.j2`). It uses group play's own name
     matcher, made public as `group_play.named(text, entries, author)` (today
     `_named`, `group_play.py:136`).
3. **If the support is empty, or holds only the answer**, the answer stands.
   The record says `policy: "answer"`.
4. Otherwise the support is renormalised and handed to **01c-C2's sampler**
   with a seed. The selected key replaces the `next` answer's value, and the
   result goes through `selection_of` unchanged. The selected key is by
   construction an offered option: `decisions._distribution`
   (`decisions.py:843-856`) drops any distribution with a key outside the
   options and the none.

### 5.3 Seed and record

```python
# routes/character_turns.py
seed = _rng().getrandbits(63)   # the existing seam (`_rng`, line 48)
```

The seed comes from the existing `_rng` seam, so a test that patches it
pins the draw, as it already pins a round's plan. The record is 01c-C3's,
plus what 02's support rule needs to replay it. It is stored on the
**round record** through `_round_state(..., selection=...)`, beside the
`actor_ref` it explains:

```json
"selection": {
  "task": "response-selector", "question": "next",
  "policy": "sampled",                   // "sampled" | "answer"
  "reason": "",                          // why "answer": "no_distribution",
                                         // "not_read", "typed_note",
                                         // "single_support"
  "backend": "native", "provider": "openrouter", "model": "...",
  "answer": "npc:mara",                  // what decide answered
  "distribution": {"npc:mara": 0.55, "npc:winifred": 0.35, "<none>": 0.10},
  "support": {"npc:mara": 0.611, "npc:winifred": 0.389},
  "seed": 4127734991, "selected": "npc:winifred"
}
```

Replay is 01c-C2 over `support` and `seed`. `support` is recomputable from
`distribution`, the offered options, the floor and the named set. It is
stored anyway, so a later change to the floor does not change how an old
pick reads. A resumed round reads `actor_ref` and never resamples (1.4 rule
3).

### 5.4 Code shape

```python
# store/response_protocol.py  (pure)
def sampled(result: decisions.ItemResult, *, offered: Sequence[str],
            named: Sequence[str], seed: int, enabled: bool,
            typed_note: bool) -> tuple[decisions.ItemResult, dict | None]:
    """`result` with its `next` answer replaced by a draw from its reported
    distribution under section 5.2's rules, and the record (None when
    `enabled` is False). Never touches an answer that is not a read speaker."""
```

`_select` calls it after `decide` and returns the record with
`(next, issue)`. `_first_actor` writes it in the same `_round_state` call
that writes `actor_ref`, so the record and the speaker never come apart.

### 5.5 What it costs

**Nothing in calls.** The sample reuses the pick's own answer. Its latency
is a few microseconds of arithmetic. Its cost shows only through which
speaker runs, so the play gate measures its *effect*, not its price.

### 5.6 When a distribution exists

A distribution exists today only on a native stage, which serves only a model
that cannot generate (1.2). 01c-C1 decides whether a generating Decision
model ever reports one (native-first, model-stated probabilities, or
neither). 02 reads a predicate rather than re-deriving the rule:

**Requirement on 01c (flagged as an edge):** a function
`reports_distribution(resolved) -> bool` (or the same fact on each stage).
02's settings text asks it, so the switch can say "with this Decision
model the pick is always the most likely speaker". If 01c does not provide
it, 02 uses `resolved.attempts[0].decision_mode == "native"`, which is
right for `main` and wrong the day 01c adds another source.

### 5.7 Play gate for C2a

- **Configurations:** a native-only Decision model with sampling `off` vs
  `on`. A structured one is included to prove sampling is inert there.
- **Feature graders:**
  - *mass on intended*, computed exactly from the recorded distribution with
    no seeds needed: the probability the sampled pick equals the fixture's
    intended speaker, set beside the argmax's 0 or 1;
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
particular stance" is a real answer, and an abstention renders no section.
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
- the round has a pending response that `_prepare` would resume (a retry or
  roll resume reuses its snapshot, 3.1 rule 5);
- `appended` is non-empty (a roll continuation);
- the round carries a player-typed note. The note *is* the player's
  direction, and a sampled stance could contradict it. An empty send's
  `director_note.j2` is app wording, not the player's, so it does not count.

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
    around=lambda call, holder: _bounded_call(call, ceiling=plan_ceiling(),
                                              on_timeout=_noting(client, resolved, holder)))
```

`LLMError` and `DecideRequestError` are caught. The intent is skipped with
the error's kind (3.3). The task goes on a new route:

```python
Route("turn_plan", "Turn planning",
      "How each character approaches their reply in play, decided before it is "
      "written. Off unless Play decisions are switched on.",
      ("turn-intent",), True, operation="decide", default_role="decision")
```

C3 adds `"turn-plan"` and C4 adds `"turn-tool-decision"` to its tasks, each
in the slice whose call site names the task (`test_routing_guard.py:659`,
`test_operation_guard.py:996`). One route, because a user who wants a cheap
model for "how does Mara answer" wants it for all three. The ledger still
tells the tasks apart.

### 6.5 Sampling, the record and the section

**Sampling.** It follows the same "whether by argmax, which by sample" rule
as 5.2:

- an abstained, refused or unread stance renders nothing;
- a read stance with a distribution is sampled over the stances above the
  floor, without the none, with a seed from `_rng`;
- with no distribution, the answer is the stance.

**The record** is stored on the response record by `store.responses.prepare`:

```json
"intent": {
  "task": "turn-intent", "skipped": "",          // or "timeout", "missing_key", ...
  "stance": "deflect",
  "replay": { /* 01c-C3's record, as in 5.3, for the stance question */ },
  "backend": "native", "provider": "...", "model": "..."
}
```

**The section** is a new catalog section, `turn_intent`, in
`assemble.SECTIONS`:

- template `templates/scene/sections/turn_intent.j2`;
- tier lock-in;
- placed immediately after `active_speaker`. The layout upgrade rule inserts
  it there in a saved layout (`store/context/layout.py`, "The upgrade
  rule").

It renders:

```
# This reply

{{ name }}'s approach in this reply: {{ stance_description }}. Write it in
their own voice. Never name or explain the approach.
```

`compose_turn` and `compose_director_turn` (`assemble.py:1637`, `:1700`)
gain `plan: dict | None = None`. `None` renders the section empty, so it
drops out in `_render_sections` and the prompt is byte-identical (3.1 rule 6).
Because the section is composed, it is in the snapshot and in the generation's
own prompt capture. A reader inspecting "What the model saw" sees the intent
the reply was written under.

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
    section's wording. A regex check, offline on recordings as well.
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
def plan_item(actor: dict, conversation: list[dict], brief: str,
              extra: tuple[decisions.Question, ...] = ()) -> decisions.Item:
    """(stance, disclosure, tension, *extra) over intent_item's context."""

DISCLOSURE_ID = "disclosure"   # Choice, allow_none: share | hint | withhold
TENSION_ID = "tension"         # Score: ("ease off", "hold steady", "raise the stakes")
```

| Question | Kind | Read as | Sampled? | Why |
|---|---|---|---|---|
| `stance` | Choice, none allowed | 6.1 | Yes, as 6.5 | Behavioural variety is the point. |
| `disclosure` | Choice, none allowed | Of what *this character already knows*, how much they let out | **Never** (argmax) | A random reveal of a secret is a continuity event nobody chose. A deterministic answer can be reviewed in the inspector; a draw cannot be argued with. |
| `tension` | Score, three levels | Whether the reply cools, holds or raises the scene | Never | It shapes pacing. A coin flip on pacing is noise. |

The section (6.5) renders one line per read answer and nothing for an
abstained or unread one. Disclosure's line says "of what they already know".
That keeps knowledge in the actor prompt's existing rules: perception and
"Use a fact ... only when their own established knowledge ... supplies it"
(`response_actor.j2`). A plan can make a character more or less forthcoming.
It can never grant knowledge.

The task is `turn-plan`, on the `turn_plan` route. The record is
`"intent"` with `disclosure` and `tension` added, and the stance's replay as
in C2b.

### 7.3 The extension slot (for 13-C3)

`extra` lets a later spec add questions to the same item, so they ride the
same call instead of adding one:

- each extra question is built and read back by its owner (13's legal-Action
  `Choice`, from `available_actions`);
- the plan code renders none of them, and stores their answers under the
  owner's key in the record, untouched;
- the owner keeps every 02-C6 rule.

This is the seam 13-C3 cites. 02 adds no Action question itself.

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

**Only before any visible text.** The first turn of the loop may be a tool
call. Once the stream has produced a visible delta, a later tool call is
**not executed**. The contribution ends as written and the call is recorded
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
- the contribution is composed fresh (C2b's `_plans` conditions, minus the
  typed-note rule: a model may still meet an unanticipated choice under a
  note).

A reroll offers the tool again. The loop's turns come after the snapshot,
so a reroll is a new response and may decide differently. The snapshot itself
never holds a tool result.

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

**Record.** The tool decision is stored on the response record under
`"tool"`: the question, the options, the selection, 01c-C3's replay where
sampled, and the backend. 01b-C1 captures it. The transcript holds only the
final visible text (3.1 rule 2). Tool turns are never shown.

**Invariants.** 3.1 holds, with rule 1's exception bounded by 8.2–8.3. The
handoff and the roll fence are parsed from the visible text only. A stream
cancelled during the tool turn aborts its meter like any cancel.

**Requirement on 01g (flagged as an edge):**

- 01g-C2's loop must stream its final turn through `inference.generate`'s
  streaming path, so `_stream_contribution`'s display and watcher stay as
  they are;
- it must expose a per-loop hook that can decline a tool call after visible
  text (8.2).

A loop that only joins replies cannot carry a scene turn.

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
task, and the consumer's first slice lands the route entry together with its
call site, exactly as specified here:

- the route entry;
- the item builder and the mapping (pure modules with tests);
- the templates;
- the `decide-*` eval case and recordings;
- the 01d-C1 policy row.

The kit modules can land in 02 because a pure builder names no task in a
`decide()` call.

### 9.2 C5a: epistemic access (for 11-C1)

```python
Route("epistemic", "Character knowledge",
      "Whether a character could know a recalled piece of history, where the "
      "record alone cannot say.",
      ("epistemic-access",), True, operation="decide", default_role="decision")
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
def access_of(questions, results: Sequence[decisions.ItemResult]) -> dict[tuple[str, str], str]
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
  The hop's answer replaces the first one. If the hop fails, is skipped
  (`same_model`, the clock) or is unread, the answer is `UNKNOWN`, never the
  first answer and never `known`.
  - **Only permissive answers need the hop.** A low-margin `narrator_only` is
    already safe, so escalating it only spends. 01d's triggers do not read
    which option was chosen. **Requirement on 01d (flagged as an edge):** an
    optional per-task filter, for example `escalate_answers=("known",
    "suspected", "experienced")`. Without it, 02-C5a escalates every
    low-margin answer, which costs more but is no less safe.
- **Never sampled.** What a character knows is not behaviour.
- **Derived, never persisted as truth.** The result is per turn (section 9 of the 11 draft). The
  kit writes nothing.
- **Budget.** Chunked at eight items per structured call. 11 should hand at
  most one chunk per turn on the turn path, under 3.4's ceiling. 11 owns the
  number.

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
      ("history-rerank",), True, operation="decide", default_role="decision")
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

- builder and mapping tests, including fail-closed and ungraded;
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
| `response-selector` | `role` (today) | Off. 01d's candidate (`refused`, `low_margin`) stays off; `refused` alone is compatible with sampling, and may be switched on after C2a's gate | Yes, per campaign (C2a) | A flat distribution is the behaviour to sample, not doubt to resolve, and the pick is on the turn path. |
| `turn-intent`, `turn-plan`, `turn-tool-decision` | `role` (one route; they must agree) | Off | Yes (stance; the tool's selection) | As above. The feature is soft, so a failure costs only the intent. |
| `scene-break` | `role` (today) | Off, as 01d recommends | No | A YES is a proposal the player confirms. |
| `voice-drift` | `role` (today) | Off, as 01d recommends | No | A verdict feeds the review. |
| `continuity-identity` | `role` (today) | Not switched by 02. 01d names it the first candidate; its switching change is its own, under 01d's bar (01d's draft, section 6.3) | No | `uncertain` already flags the row for the human review, so the hop must be shown to cut wrong merges and missed duplicates, not merely the count of `uncertain`s. The bar should say so. |
| `continuity-reconcile` | `role` (today) | Not switched by 02 | No | Proposals are reviewed. Unanswered candidates are asked again next sweep. |
| `epistemic-access` | `role` | **On** (9.2) | No | Leakage is the costly error. Fail closed. |
| `history-rerank` | `role` | Off | No | Turn-path latency. Ungraded falls back to signals. |

**One rule across the table: a task never both samples and escalates on
`low_margin`.** Escalation treats a flat distribution as uncertainty to
remove. Sampling treats it as the behaviour to reproduce. A task that did both
would send exactly the items sampling exists for to a second resolver.
`refused` escalation is compatible with sampling, because a refusal is never
sampled (01c-C4).

**Requirements on 01c and 01d (flagged as edges):**

- `test_task_policy.py` fails a row with `samples=True` and `"low_margin" in
  escalate_on`;
- a failed or skipped hop leaves the item for the call site to map. For
  epistemic access, 02-C5a maps it to `UNKNOWN` itself (9.2), so this needs
  nothing new from 01d beyond a per-item "escalation did not answer" signal,
  which 01d's per-item provenance already provides (01d's draft, section
  5.6).

---

## 11. Contract

**02-C1 — Reconciled record.** Section 1, as of `35c1fb7`.

- **Guarantees:** every "landed" row cites the code and the guard or suite
  holding it. Every "not landed" row names the contract or spec that now owns
  it.
- **Failure behaviour:** a later PR that changes a landed decide site updates
  section 1.2 in the same PR. The guards cited there fail first if it does
  not.

**02-C2a — Sampled next speaker.**

- **Inputs:** the speaker pick's `ItemResult`, the offered refs, the named
  set, a seed, the switch and the typed-note flag.
- **Outputs:** `(next, issue)` exactly as `selection_of` returns it, plus a
  `selection` record on the round.
- **Guarantees:**
  - every 1.4 rule 3 invariant holds;
  - a sampled value is an offered option;
  - none, abstained, refused, unread and missing-distribution answers are never
    sampled (01c-C4);
  - an addressed actor is never sampled away;
  - a resumed round never resamples;
  - off writes nothing and calls nothing.
- **Failure behaviour:** a sampler error (a defect) leaves the answer
  standing, records `policy: "answer"` with the error's class name and logs at
  ERROR. It never fails the round.

**02-C2b — Turn intent.**

- **Inputs:** the assigned actor, the observable window and the actor's own
  brief.
- **Output:** a `stance` (or none), the `intent` record on the response, and
  the `turn_intent` section in that contribution's composed prompt.
- **Guarantees:**
  - asked only for a freshly composed NPC contribution with no typed note;
  - frozen in the snapshot;
  - never asked again by a retry, a resume, a reroll or Keep writing;
  - soft and bounded (3.2–3.4);
  - attributed with `response_id`.
- **Failure behaviour:** skipped, recorded with the reason, and the
  contribution proceeds unchanged.

**02-C3 — Turn plan.** C2b's contract with `disclosure` (argmax) and
`tension` (argmax) added in the same item and the same call, plus an `extra`
slot whose questions are owned, read and stored by their provider.

- **Guarantees:** one pre-generation call per contribution, never two (the
  plan replaces the intent call); the plan never changes a preset; disclosure
  never grants knowledge.

**02-C4 — Decision as a tool from play.**

- **Guarantees:**
  - at most one honoured tool call per contribution, before any visible text;
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

- **Guarantees:** deterministic first; fail closed to `UNKNOWN`; escalation
  only on permissive answers; never sampled; nothing persisted.

**02-C5b — History relevance kit.** The route entry, the task
`history-rerank`, `build_items` and `grades_of`, the templates, the
`decide-history-rerank` case and the 01d-C1 row.

- **Guarantees:** ungraded is `None`, never a level; no escalation, no
  sampling; a `Rank` variant once 01e-C1 lands.

**02-C6 — Shared play-decision rules.** Section 3, together with the play
gate of section 4.

- **Guarantees:** what must not change (3.1); soft resolution and no new
  pre-write refusal (3.2); failure never fails a turn (3.3); the ceiling
  (3.4); attribution with `response_id` (3.5); settings off by default and
  exposed only after ratification (3.6, 4.3); privacy (3.7).

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
- **Prompt sections and goldens.** `turn_intent` is a new catalog section that
  renders empty when off. `test_lore_golden.py` and every prompt golden stay
  untouched. A new test pins `compose_turn(plan=None)` to the pre-change
  bytes. `scripts/verify_templates.py` covers the new templates.
  `evals/run.py`'s offline cases cover the new decide prompts.
- **Import guard.** The new store modules (`turn_plan.py`,
  `play_decisions.py`, `epistemic_access.py`, `history_rerank.py`) import
  `grimoire.decisions` and `prompts` like `response_protocol.py` does
  (`response_protocol.py:10`), all at module scope. Cross-package store
  imports bind submodules.
- **Pydantic and Android.** No new dependency. No pydantic models beyond
  plain fields. The settings body uses existing `PUT /config` and campaign
  update shapes, dumped through `_dump`.
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

- Off: the pick's result, the round record and the ledger are unchanged.
- A native fake with a distribution and a patched `_rng`: the draw is the
  seeded one. The `selection` record replays to the same key through 01c-C2.
- Each "not sampled" case of 5.2 rule 1 leaves `selection_of`'s output
  identical to today's: abstained → `(None, None)`; off-option →
  `INELIGIBLE`; unread, refused or error → `INVALID_HANDOFF`; typed note →
  the answer.
- `NONE_KEY` mass is never selected. A distribution whose argmax is none is
  abstained, not sampled.
- Floor: an option under `1/(2n)` is never selected over 1,000 seeds.
- Addressed: a post naming Mara narrows the support to Mara. Naming both Mara
  and Winifred narrows it to the two.
- A retry and a roll resume of a sampled round do not resample (the
  `_rng`-patched proof the group-play suite already uses for plans).
- `DecideRequestError` still becomes `INVALID_HANDOFF`. `LLMError` still
  propagates and rescues.

**Turn intent and plan (`test_turn_plan.py`, route tests with
`llm_fakes.py` only):**

- Off: no `turn-intent` or `turn-plan` row, no `intent` key, and
  `compose_turn` bytes equal to `main`'s.
- On: one decide call before `response_start`. The section is in the
  generation's prompt and snapshot. The record carries `response_id`, and the
  ledger row carries the same id.
- Skips: `grimoire`; resume of a pending response; roll continuation; typed
  note; missing key (soft, `skipped: "missing_key"`); `incapable`; timeout
  (patched ceiling); `LLMError`; abstained (no section).
- Reroll and Keep writing reuse the snapshot and make no decide call.
- Plan: one call carrying three questions. Disclosure and tension are argmax
  even when a distribution is present. `extra` questions are stored untouched
  and rendered by nobody.
- `turn_intent` section: renders empty for `None` and for all-abstained.
  Placed after `active_speaker` in a saved layout (layout upgrade rule).

**Tool (`test_play_tool.py`, after 01g):** offered only under 8.4; one
honoured call; a call after visible text declined and recorded; cap answers
`cap`; the selection is returned without the distribution; tool turns absent
from the transcript and from the watcher's input; one meter per loop turn
under one run id.

**Kits (`test_epistemic_access.py`, `test_history_rerank.py`):**
fail-closed mapping for every reason and detail; escalation only on
permissive answers (with 01d's helper faked); ungraded is `None`; builders
refuse nothing a caller could legally pass; `decide-*` replay cases green.

**Guards:** the routing and operation guards pass at each slice. A planted
`require_inference("turn-intent", ...)` without its route fails, which proves
the kit-not-route rule.

**Evals:** the new `decide-*` cases replay in `make check`. `decide-speaker`
has a native recording with a distribution. `evals/run.py --live --play
<feature>` refuses without `--live`, writes no settings, and files rows only
under 01a-C2's scope.

**Acceptance for the spec as a whole:**

1. Section 1 is accurate at the slice's baseline.
2. Each feature lands off and byte-identical.
3. Each feature's switch appears only with a ratified live report.
4. 09 and 11 can land their routes from the kits without re-specifying them.
5. 13 can cite 02-C2a, 02-C3's slot and 02-C6 for its NPC Action choice.

---

## 14. Slices

| Slice | Lands | Waits on |
|---|---|---|
| **02-A — Record and plumbing** | Section 1 confirmed. `decide(response_id=)`. `responses.mint_id()`. `store/play_decisions.py` (settings readers, keys in `_CONFIG_KEYS`, no UI). The `turn_intent` section, rendering empty. The byte-identical golden. | 01 only |
| **02-B — Turn intent, dark** | `turn_plan.py` (intent), the `turn_plan` route with `turn-intent`, the call site, the record, the `decide-turn-intent` case. Argmax only. | 02-A, 01b-C1 |
| **02-C — Plan, dark** | `turn-plan` task, the plan item with the `extra` slot, the `decide-turn-plan` case. | 02-B |
| **02-D — Sampling** | C2a in `_select`. Stance sampling in B and C. The `decide-speaker` native recording with a distribution. | 01c-C1..C4 |
| **02-E — Play gate** | `evals/play.py`, `--live --play`, fixtures, feature graders. | 01a-C1..C3 |
| **02-F — Exposure** | The "Play decisions" ConfigView section and campaign overrides, one switch per ratified feature, and the inspector line. | 02-E reports, the user's yes |
| **02-G — Kits** | `epistemic_access.py`, `history_rerank.py`, templates, `decide-*` cases. No routes (9.1). | 01d-C1..C3 for the policy rows |
| **02-H — Tool** | C4. | 01g-C1..C5, 02-E |

B and C land dark: the backend honours the key and there is no UI. 02-E can
run B and C before 02-D lands; argmax is a valid configuration.

---

## 15. Non-goals

- **Shadow mode in the product.** Superseded (1.3). Two resolvers per call are
  only ever run in evals.
- **Native-first for a generating model.** It stays 01 section 16's decision.
- **Re-tuning or re-gating F–H's sites,** beyond C2a's optional sampling.
- **Switching on escalation for continuity identity or reconcile.** 01d's
  candidate table names identity first. Its switch is one policy row in a
  change of its own, under 01d's bar (section 10).
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

1. **Should the speaker pick get 3.4's ceiling too?** Today a stalled Decision
   model holds the turn until the facade's idle timeout. *Recommendation:* yes,
   in 02-A, with timeout mapped to `INVALID_HANDOFF`. That returns control to
   the player, which is the existing failure mode, rather than failing the
   round. It is a behaviour change to a landed site, so it needs the user's
   yes.
2. **Should sampling exclude the none?** (5.2 rule 2.) *Recommendation:* yes.
   Hand-back is a player-agency signal, and randomising it lets NPCs talk past
   a pending player decision. The alternative, sampling none like any option,
   is one constant to flip if the play gate shows chains that end too rarely.
3. **Should a reroll re-ask the intent?** *Recommendation:* no for 02 (frozen
   snapshot, 3.1 rule 5). A "reroll with a fresh approach" control would store
   the section outside the snapshot as an appended block and needs its own
   design.
4. **A global switch plus a campaign override, or campaign only?**
   *Recommendation:* both, mirroring the tracker (3.6). A cost knob a user sets
   per story should not need a global change.
5. **Should there be a `deciding` SSE frame** before `response_start`, so the
   client can show that the plan is running? *Recommendation:* not in 02. The
   responding indicator already covers the wait. Revisit if the play gate's
   added latency is noticeable.
6. **The stance vocabulary.** *Recommendation:* ship 6.1's seven and let the
   adherence grader and stance diversity revise it before exposure (02-F). The
   options live in templates, so revising them is a template edit plus a
   recording update.
7. **Joint speaker and stance once 01e-C3 lands.** *Recommendation:* a
   follow-up slice behind its own play gate, measured against the two-call
   path on added latency.
8. **Missing edges, for the owners to accept or refuse:**
   - 01a: per-case aggregation across tasks (4.2);
   - 01c: `reports_distribution(resolved)` (5.6);
   - 01c/01d: `test_task_policy.py` refuses `samples=True` together with
     `low_margin` escalation (10);
   - 01d: an optional per-task answer filter on escalation
     (`escalate_answers`), so epistemic access escalates only permissive
     answers (9.2);
   - 09 and 10: the `history_check` route and the `history-rerank` task follow
     09's naming. 09 lists 02-C5 as a soft edge the checklist lacks (09 <-
     02-C5), and that is right: 09 needs only the kit;
   - 01g: a streamed final loop turn and a decline-after-text hook (8.6).

   *Recommendation:* each owner states the item in its own contract. 02's
   fallbacks are written beside each requirement so 02 is not blocked.
