# Ending a scene: fewer calls, run at once, better grounded

Ending a scene takes minutes. On Android it frequently does not finish at all:
the phone locks, the radio goes with it, and ten minutes of sequential LLM work
is lost with nothing to resume from.

Three complaints, one shape behind them.

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

In: concurrent execution of absorb's phases; batching the per-NPC phases;
splitting extraction into three focused prompts; a citation contract the store
can actually verify; skipping work that cannot produce a finding; a retryable
extraction endpoint; foreground-service promotion during absorb.

Out: prompt-caching breakpoints (`cache_control` is absent from `llm.py` and
`openrouter.py` entirely — a separate piece of work); server-side staging of
partial results across a reconnect; any change to what `PUT /chronicle`
persists or to the `commit_token` epoch scheme.

## The call budget

Five present NPCs, three anchored, two of whom actually speak:

| | today | proposed |
|---|---|---|
| LLM calls | 10, strictly sequential | 6, all concurrent |
| transcript copies sent | 10 | 6 |
| wall clock | sum of 10 calls | ≈ slowest single call |

Six calls: three extraction, one dossier batch, one voice batch, one audit.
Extraction is the one count that rises, and the three replacing it are each far
shorter than the monolith they replace.

## Decisions

### Fan out, in-request, under a cap

`post_absorb` stays one POST. Its four phases become one `asyncio.gather` over
the phase coroutines, bounded by a semaphore so a large cast cannot open twenty
sockets at once.

The alternative — the client firing four requests and assembling the review
itself — was considered and not taken. The shared `_Budget` and the
`commit_token` epoch stamped at the top of the handler both exist because the
whole review is prepared from one snapshot under one hold; splitting the
request would require re-deciding both, for no latency the fan-out does not
already deliver.

`_Budget` becomes a ceiling on the *slowest* phase rather than on their sum,
which is what it was always trying to express. Its `BudgetRefused` / overrun
reporting and the `phases` projection are unchanged — every phase still reports
`attempted` and `budget_exhausted`, and `_phase_report` still projects one row
per step from the block that owns it.

`_watched`'s disconnect handling survives the change: each phase coroutine is
already the unit it wraps.

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

### Cite the line, not the words

The citation contract changes from "reproduce a short verbatim excerpt" to
"name the line you read it in".

`snippets/transcript.j2` numbers its lines (`[12] Mara: …`). Every edit carries
`"src": 12`. `routing.authority` looks message 12 up directly: the speaker is
known exactly, with no `match_name` prefix guessing, no normalization, and no
way for a tidied apostrophe to be reported as a fabrication.

This is cheaper as well as better — an integer replaces a quoted excerpt on
every one of dozens of rows — but correctness is the reason. Pointing at a line
is a thing models do reliably; reproducing text verbatim is not, and the
existing design charges the difference to the reviewer as a collapsed row.

Nothing regresses: `quote` stays as an optional display field, and the existing
substring path stays as the fallback for a row that gives a quote and no index.
An out-of-range or missing `src` falls through to exactly today's behaviour.

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

Two changes, in that order of importance.

`ServerService` promotes to foreground for the duration of an absorb, exactly
as `docs/android-architecture.md` §4 already specs for a scene stream, and
demotes when the review is returned. This is what stops the OS killing the
radio mid-call, and it is the only one of the two that prevents the failure
rather than recovering from it.

`POST /campaigns/{cid}/scenes/{sid}/extract` joins the existing `audit` and
`dossiers` retries, so extraction stops being the one unrecoverable phase. With
the fan-out, a drop that still happens costs one round of calls rather than ten
minutes, and each phase has its own button.

Absorb remains non-idempotent and the `force` / `already_absorbed` guard is
untouched: a retry re-runs a *phase* of an open review, never a second
absorption.

## Testing

- **Concurrency**: a fake client recording call start/end timestamps proves the
  four phases overlap, and that the semaphore caps them.
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
- **Budget**: `_clock` still drives the arithmetic off a fake clock; the
  ceiling now applies to the slowest phase, and a phase refused before its
  first call still reports `attempted: false`.
- **Templates**: `scripts/verify_templates.py` covers the three new extraction
  prompts and the reordered dossier/voice prompts; the eval suite's verbatim
  section checks are extended to the split prompts, since that harness is what
  proves the instructions survived the split.
- **Frozen campaign**: `snapshot.json` is regenerated only if the split
  deliberately moves rendered text, reviewed and committed with the change.

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
did. The semaphore cap is the knob; it wants to be config, not a constant.

**The order-dependent test fakes are load-bearing and numerous.** Twenty call
sites script replies by call order, and a fan-out makes call order
meaningless. Migrating them to cassettes is unavoidable, mechanical, and large
enough that it should be its own step ahead of the concurrency change rather
than a tail of it — landing the two together makes a genuine behavioural
regression indistinguishable from a fake that answered out of order.
