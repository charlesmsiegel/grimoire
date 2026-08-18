# Ending a scene: fewer calls, run at once, better grounded

Ending a scene takes minutes. On Android it frequently does not finish at all:
the phone locks, the radio goes with it, and ten minutes of sequential LLM work
is lost with nothing to resume from.

Three complaints, one shape behind them.

Touches the machinery of #243 (the absorb time budget), #236 (dossier failure
visibility), #112 (citation authority), #59 (voice drift) and #235 (staged
edits and the already-absorbed guard). It changes none of their decisions —
only the order the calls run in and the evidence a citation rests on.

## Problem

`routes.scenes.post_absorb` runs its whole LLM sequence **strictly
sequentially** inside one HTTP request:

1. extraction — one call
2. `_stage_dossiers` — one call per present NPC, in a `for` loop
3. `_stage_voice_drift` — one call per anchored NPC, in a `for` loop
4. `_run_audit` — one call

A scene with five present NPCs, three of them anchored, is **ten calls
back-to-back**. `DEFAULT_ABSORB_BUDGET` is `600` seconds and `_Budget` exists
precisely because nothing else bounds the total. There is no `asyncio.gather`,
no semaphore, no concurrency anywhere in `backend/src/grimoire/`.

**The sequencing is incidental, not required.** `_run_audit` re-reads the scene
and transcript itself and never touches `parsed`; `_stage_dossiers` and
`_stage_voice_drift` each take only `transcript`, captured from the snapshot
*before* the extraction call. The dependency graph is a single fan-out that has
been written as a chain.

Three further costs ride on the same shape:

**The transcript is re-sent once per call.** Ten calls, ten copies. The
per-NPC calls carry the entire scene text to ask one question about one
character.

**Extraction is one call asking for seventeen JSON sections** — including, for
each new character, a full W++ block, a one-to-three-paragraph history, example
dialogue and a Stable Diffusion prompt. It is the longest single generation in
the app, and it is where "close the commitment this scene just paid off"
competes for attention against sixteen other jobs. `plot_movements.status:
closed` and `commitment_movements.status: fulfilled/broken/expired` are already
in the contract; they are one clause among seventeen, which is why a finished
thread rarely gets proposed for closure.

**Grounding is a verbatim-substring test.** `absorb/routing.py` asks the model
for a short excerpt and `_found` checks `excerpt in texts[canon]` after folding
case, whitespace and quote shape. A paraphrase, a trimmed ellipsis or a quote
spanning two messages fails it, lands `UNATTRIBUTED`, scores `0.3 × certainty`,
bands `low`, and is collapsed out of the reviewer's sight. Models paraphrase.
That is why so few findings can be grounded.

**Android has no protection at all.** `docs/android-architecture.md` §4 specs
foreground-service promotion during generation; `ServerService.kt` marks it as
unimplemented Phase 3. Nothing promotes during absorb, so the OS is free to
suspend the process mid-sequence. And extraction is the one phase with **no
retry endpoint** — `POST .../audit` and `POST .../dossiers` both exist, so a
loss anywhere else is recoverable and a loss in extraction is not.

## Scope

In: concurrent execution of absorb's phases; batching the per-NPC phases; splitting extraction into three
focused prompts; a citation contract the store can actually verify; skipping
work that cannot produce a finding; a retryable extraction endpoint;
foreground-service promotion during absorb. One new config key, `absorb_concurrency`.

Out: prompt-caching breakpoints (`cache_control` is absent from `llm.py` and
`openrouter.py` entirely — a separate piece of work); server-side staging of
partial results across a reconnect; any change to what `PUT /chronicle`
persists or to the `commit_token` epoch scheme.

## The call budget

Five present NPCs, three anchored, two of whom actually speak:

| | today | proposed |
|---|---|---|
| LLM calls | 10, strictly sequential | 6, concurrent |
| transcript copies sent | 10 | 6 |
| wall clock | sum of 10 calls | slowest call × ⌈6 ÷ cap⌉ |

Six calls: three extraction, one dossier batch, one voice batch, one audit.
Extraction is the one count that rises, and the three replacing it are each far
shorter than the monolith they replace.

The wall-clock row is deliberately not "one call". With a semaphore cap below
six the calls run in rounds, and a provider that rate-limits a burst will
serialize them regardless of what the cap allows — so the honest claim is one
round rather than one call, and a cap of 3 is two rounds, not ten.

## Decisions

### Fan out, in-request, under a cap

`post_absorb` stays one POST. Its phases become one `asyncio.gather` over the
phase coroutines, bounded by a semaphore so a large cast cannot open twenty
sockets at once.

The cap is a new config key, `absorb_concurrency`, beside `absorb_budget` and
`llm_call_budget` — not a constant. A rate-limited provider is a per-account
fact no default can know, and `absorb_concurrency = 1` has to remain available
as an exact restoration of today's sequential behaviour: the one setting that
makes this change reversible without a revert.

The alternative — the client firing four requests and assembling the review
itself — was considered and not taken. The `commit_token` epoch is stamped at
the top of the handler precisely because the whole review is prepared from one
snapshot under one hold (#271); splitting the request would mean re-deciding
that, for no latency the fan-out does not already deliver.

### The budget needs nothing done to it — a correction

An earlier draft of this spec called the budget the sharpest edge in the
change, on the following reasoning: `routes/common.py::_bounded_call` excludes
absorb from `llm_call_budget` because `_Budget` "bounds a whole *sequence* and
knows which of its steps are droppable"; under a fan-out nothing is droppable;
therefore all phases share one deadline and an overrun would take the
extraction with it, turning today's graceful degradation into a 502.

**That reasoning is wrong, and it was caught by implementing it.** `_Budget.run`
reads `remaining()` at the moment it is called and passes it to `wait_for`. In
a fan-out every phase calls `run` at t≈0, so each independently receives the
*full* budget. Parallel phases do not consume one another's wall-clock, so a
slow dossier phase cannot expire the clock out from under the extraction. The
only way the deadline can pass while the extraction is still in flight is the
extraction alone exceeding the whole budget — which is exactly the case
`test_absorb_extraction_overrunning_the_budget_is_502` says *should* be a 502.

Two invariants would have been broken by the "fix":

- `absorb_budget = 0` means "no ceiling at all, however long the calls take" —
  the documented escape hatch for a slow local endpoint, defended by
  `test_the_one_shot_ceiling_does_not_bound_absorb`. Routing absorb through the
  per-call ceiling to compensate for the exemption narrows that hatch silently,
  which is the specific regression that test exists to catch.
- Exempting the extraction *without* the per-call ceiling leaves it bounded by
  nothing but the facade's idle timeout — strictly worse than the 502 the
  exemption was meant to prevent.

So `_Budget` is untouched. Its semantics do shift, for free and in the right
direction: a ceiling that used to bound the *sum* of the calls now bounds each
of them, and since they are concurrent that is a ceiling on the whole absorb.
`drop_tail` still works — `_stage_dossiers` checks `spent()` between NPCs, so a
dossier loop that runs long still sheds its tail and reports it.

### gather semantics, spelled out

`asyncio.gather` without `return_exceptions=True` propagates the first exception
and leaves its siblings **running, detached** — five orphaned LLM calls billed
to nobody. `Abandoned` and `BudgetRefused` both fly through this code, so this
is the expected path, not the exotic one.

The fan-out therefore uses `return_exceptions=True` and cancels the remaining
tasks explicitly, following the detach-don't-await discipline `_watched` and
`common._abandon` already established for exactly this reason: waiting on a
cancellation hands the unwinding the control you were taking back.

`_watched`'s disconnect handling moves out of the individual phases to wrap the
gather, since a disconnect abandons the whole review, not one phase of it.

### Batch the per-NPC phases — cheaper *and* better

`_stage_dossiers` and `_stage_voice_drift` each collapse to one call per chunk
of NPCs instead of one per NPC. Chunk size is a module constant, initially 5,
sized so an ordinary scene is one call and a crowded one is two — not a config
knob, since nothing a user knows would tell them how to set it.

Cheaper is the obvious half: the transcript goes once per chunk instead of once
per character.

Better is the half worth designing for. Each dossier is currently written in
isolation, with no view of the others, so two paragraphs can describe the same
event incompatibly — Mara's dossier recording a reconciliation that Winifred's
records as a rupture. Batched, the model sees the whole present cast and writes
them against each other.

The voice judge gains more. It currently **disqualifies** any anchored NPC
whose card name is `confusable` with another speaker, because the judge cannot
be told which lines are whose — a correct decision that costs the check
entirely. With every speaker in one prompt the judge can be shown the speaker
roster explicitly and asked to attribute against it, so a `Winifred Vance` and
a `Winifred Vale` in the same scene stop costing each other their voice checks.

Chunking has one constraint beyond size: **NPCs whose names are `confusable`
with each other must land in the same chunk.** Splitting them across calls
would hand each chunk the ambiguity the batching was supposed to resolve, and
silently — the judge would answer confidently about lines it cannot attribute.
Where co-locating them would overflow the chunk, they keep today's behaviour
and are reported as disqualified rather than judged.

Chunking rather than one unbounded batch: a twelve-NPC scene would otherwise
risk a truncated reply, and one bad reply would lose every dossier. Parsing is
per-NPC, so a reply good for four of five stages those four and reports the
fifth in the existing `failed` list — the same shape the inspector already
renders. `POST .../dossiers` stays and gains a per-NPC scope, so a reviewer can
re-run one character rather than the phase.

### Skip work that cannot produce a finding

`_stage_voice_drift` filters to *anchored* NPCs but not to NPCs who actually
**spoke**. Judging drift for a character with no dialogue can only return
`not_enough` — a call bought to learn nothing. `routing.speaker_index` already
computes exactly who spoke, from the same snapshot, at no extra cost. Silent
NPCs are dropped from the batch and reported as skipped with that reason, not
as a budget casualty.

The same filter does not apply to dossiers: a character can matter to a scene
without speaking in it.

**Skipping the audit when the scene logged no rolls was considered and
rejected.** It looks like the same saving and is the opposite: a scene where
the narration resolves a lock-picking and no roll was ever logged is precisely
the scene the audit exists to flag. Gating the check on the presence of the
thing it checks for would blind it to every absence.

### Split extraction into three

Three prompts, run concurrently:

- **narrative** — `one_line`, `summary`, `keywords`, `timeline_events`, `facts`
- **ledger** — `plot_movements`, `commitment_movements`, `relationship_deltas`,
  `bond_changes`, `character_state_edits`, `group_state_edits`
- **new material** — `new_characters`, `new_locations`, `new_lore`,
  `lore_edits`, `authored_edits`, `weather_edits`

The split is by *what the model has to hold in mind*, not by output size. The
ledger call is the one that matters: it receives the plot, commitment,
relationship, state and group snapshots `absorb/snapshots.py` already builds,
and it is asked to **walk every open item and say whether this scene resolved
it** — a checklist it must answer, rather than an invitation it may take. That
is the whole of the "it never proposes closing anything" fix, and it costs
nothing: those snapshots are in the prompt today.

The new-material call is the one that was making everything else wait. Nothing
about generating a W++ card and a Stable Diffusion prompt should block the
one-line summary.

`parse_output` gains a merge: three replies, one `parsed` dict with the same
keys `materialize` consumes today. A phase that fails degrades to its own
sections missing, reported like any other phase, rather than failing the
absorb — except narrative, which keeps today's 502 behaviour, since a review
with no summary is not a review.

### Cite the message, not the words

The citation contract changes from "reproduce a short verbatim excerpt" to
"name the message you read it in". Every edit carries `"src": 12`, and
`routing.authority` looks message 12 up directly: the speaker is known exactly,
with no `match_name` prefix guessing and no way for a tidied apostrophe to be
reported as a fabrication.

Two things the first draft of this got wrong, both load-bearing:

**The numbering does not go in `snippets/transcript.j2`.** That snippet is
shared — `chronicle.transcript_text` renders it for the absorb, dossier, voice,
audit and rolling-summary prompts *and* for `store/export.py`, which is the
epub and markdown a human reads. Numbering it would stamp `[12]` markers
through exported campaign documents and change every other prompt in the app
for a change that concerns one of them. Absorb gets its own numbered renderer,
built from the same two label pieces so `routing._label` still agrees with what
the model saw, and the shared snippet is untouched.

**It numbers messages, not lines.** The snippet joins bodies with `\n\n` and
interpolates `m.content` verbatim, so a multi-paragraph post is already several
lines. `src` indexes the message list — the same list `speaker_index` walks —
so the two cannot disagree about what 12 means.

**`src` is not strictly better than the quote; it is better at the thing that
is failing.** The substring check proved the cited *words* exist, which is weak
evidence about attribution and real evidence about relevance. An index proves
attribution exactly and proves nothing about relevance: a model can point at a
message that does not support its claim. The trade is deliberate — the observed
failure is honest rows collapsing as fabrications, not fabricated rows sailing
through — but it is a trade, and `certainty` remains the only signal about
whether the cited message says what the edit claims.

The numbering is computed **once**, from the snapshot, and shared by all three
extraction prompts and by `speaker_index`. Renumbering per call would let two
prompts disagree about what 12 means while both looked correct.

Nothing regresses: `quote` stays as an optional display field and the existing
substring path stays as the fallback for a row that gives a quote and no index.
An out-of-range, non-integer or missing `src` falls through to exactly today's
behaviour.

The tier vocabulary (`NARRATION` / `SELF` / `OTHER` / `UNATTRIBUTED` /
`UNCITED`), the `WEIGHTS` table, the band edges and the rule that **nothing
here is permission** are all unchanged. This changes how authority is
*established*, never what it authorizes.

### Order prompts for the cache

OpenRouter caches automatically for some providers, and every absorb prompt is
currently built to defeat it. `templates/dossier/user.j2` and
`templates/voice_drift/user.j2` both put the varying part first (`Character:`,
`Prior dossier:` / `Voice anchor:`) and the long shared transcript last, so the
only shared prefix across N calls is the system prompt.

Every prompt in the absorb family puts the transcript and the deterministic
context head **first** and the per-call instruction **last**. Batching already
removes most of the repetition this would have saved; the ordering is adopted
because it is free and because it is the precondition for adding explicit
`cache_control` breakpoints later.

One honest tension to record: firing all six calls at once means all six miss
the cache together, since none has written it yet. Serializing one call first
to warm the prefix would trade a second round-trip for a large input-token
saving. Not taken here — latency is the complaint being answered — but the
ordering leaves the option open.

### Android: promote, then retry

Two changes, in that order of importance. The first is much larger than
"promote the service" makes it sound, and the spec should say so.

`ServerService` today is a bare started service: no `foregroundServiceType`, no
`FOREGROUND_SERVICE` permission in the manifest, no notification channel, and
no way for the Python side to tell the Kotlin side that a call is in flight.
Promotion needs all of it:

- `FOREGROUND_SERVICE` and a typed `FOREGROUND_SERVICE_DATA_SYNC` permission,
  with `android:foregroundServiceType="dataSync"` on the service — Android 14
  rejects an untyped promotion.
- `POST_NOTIFICATIONS` at runtime (Android 13+) and a notification channel, or
  the required notification never appears.
- A signal path from the request to the service. The server is in-process, so
  the cheapest honest option is the WebView side promoting on fetch start and
  demoting on settle, rather than a Chaquopy callback per request.
- An accepted limit: `dataSync` foreground services are capped at roughly six
  cumulative hours per day on Android 14+. That is far above any absorb, but it
  is shared with the scene-stream promotion §4 already plans, so the two want
  one owner rather than two independent promoters.

This is the change that *prevents* the failure rather than recovering from it,
and it is the one piece here that is plausibly its own piece of work.

`POST /campaigns/{cid}/scenes/{sid}/extract` joins the existing `audit` and
`dossiers` retries, so extraction stops being the one unrecoverable phase. A
retry is a single phase, so it takes the per-call ceiling and not the overall
one — the overall ceiling exists to bound a fan-out, and there is no fan-out in
a retry of one step. With
the fan-out, a drop that still happens costs one round of calls rather than ten
minutes, and each phase has its own button.

Absorb remains non-idempotent and the `force` / `already_absorbed` guard (#235)
is untouched: a retry re-runs a *phase* of an open review, never a second
absorption.

## Staging

Six changes ride in this spec, and landing them as one diff would make a real
regression indistinguishable from a fake answering out of order. They have a
required order, and the first two are not optional preludes:

1. **Migrate the order-dependent fakes to cassettes.** Behaviour-preserving,
   large, and a precondition for anything concurrent. Lands green on its own.
2. **Fan out**, with the gather/cancellation discipline. This is the whole
   latency win and it changes no prompt. `_Budget` is untouched (see above).
3. **Batch the per-NPC phases** and add the silent-NPC filter.
4. **Split extraction** into narrative / ledger / new-material, with the
   reordered cache-friendly prompts. The only step that can move output quality.
5. **The citation contract**, then **Android**. Independent of each other and of
   1–4; either can be dropped without stranding the rest.

Steps 1–2 deliver the latency complaint on their own. Everything after is the
"without making it worse" half, and 4 is where that claim is actually at risk.

## Testing

- **Concurrency**: a fake client recording call start/end timestamps proves the
  six calls overlap, that the semaphore caps how many are open at once, and
  that a cap of 1 reproduces today's sequential behaviour exactly — which is
  the escape hatch a rate-limited provider needs.
- **The fake-client migration is the largest single piece of this work, and it
  is a precondition rather than a consequence.** `llm_fakes`' cassettes answer
  by *what the request looks like*, so they tolerate arrival order; the
  scripted turns answer by *call order*, so they cannot survive a fan-out.
  Twenty sites pass a list to `FakeOpenRouterComplete` (all in `test_routes.py`
  and `test_llm_fakes.py`), and every one of them that drives absorb has to
  move to a cassette in `backend/tests/fixtures/llm/` before the fan-out can
  land. A single-element `FakeOpenRouterComplete("…")` is order-independent and
  stays. `test_llm_fakes.py` renders every real prompt template to prove the
  matchers still match, so the split prompts need matcher entries there in the
  same change or that test fails — which is the harness working as designed.
- **Batching**: a chunk reply good for four NPCs of five stages four and
  reports the fifth as failed; chunk boundaries are covered at 1, 5 and 6 NPCs.
- **Silent-NPC skip**: an anchored NPC with no lines costs no call and is
  reported skipped with its own reason, not as budget-exhausted.
- **Citation**: `src` resolves to the right speaker and tier; an out-of-range
  `src` falls back to the quote path; a row with a quote and no `src` bands
  exactly as it does today. The existing `routing` tests are the regression
  suite for that last one and must not be edited.
- **Budget**: unchanged behaviour, so the existing budget tests are the
  regression suite and must keep passing untouched — including
  `test_the_one_shot_ceiling_does_not_bound_absorb`, which defends
  `absorb_budget = 0`, and `test_absorb_extraction_overrunning_the_budget_is_502`.
  Both were broken by the rework this spec no longer proposes, which is how the
  reasoning behind it was found to be wrong.
- **Templates**: `scripts/verify_templates.py` covers the three new extraction
  prompts and the reordered dossier/voice prompts; the eval suite's verbatim
  section checks are extended to the split prompts, since that harness is what
  proves the instructions survived the split.
- **Frozen campaign**: `snapshot.json` must come back **unchanged**. The shared
  transcript snippet is deliberately untouched, so a diff there is the signal
  that the numbering leaked into the shared renderer — the failure this design
  is arranged to prevent, caught by the fixture built to catch exactly it.

## Risks

**The split changes what the model is asked, so output quality can move in
either direction.** The eval suite proves the instructions are still present,
never that the model still follows them; `evals/run.py --live` is the only
thing that answers that and is opt-in. The split lands behind the review panel
either way — every edit is staged and a human ticks it — so the failure mode is
a worse review, not a bad write.

**Three extraction calls cost more input tokens than one.** Six transcript
copies against today's ten is a net saving, but the saving is smaller than the
call-count drop suggests.

**Batched replies are longer**, so a chunk can truncate where a per-NPC call
would not. Chunk size is the mitigation and per-NPC parsing is the backstop.

**Concurrency reaches provider rate limits** that sequential execution never
did. `absorb_concurrency` is the knob, and `1` restores today's behaviour.

**The order-dependent test fakes are load-bearing and numerous.** Twenty call
sites script replies by call order, and a fan-out makes call order
meaningless. Migrating them to cassettes is unavoidable, mechanical, and large
enough that it should be its own step ahead of the concurrency change rather
than a tail of it — landing the two together makes a genuine behavioural
regression indistinguishable from a fake that answered out of order.
