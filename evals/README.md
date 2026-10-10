# evals/ — scoring what the model actually writes

grimoire's primary output is LLM prose, and a lot of this codebase exists only
to shape it: the prose style chain, the length budget knobs and drift
correction, the natural-prose block, scene-suggestion grounding, the roll-fence
protocol. Every one of those is a **hypothesis about model behaviour**. The
pytest/vitest suites verify the plumbing around them — that the right variables
reach the right template — but nothing verified the hypothesis itself, and a
template edit takes effect live, with no restart and no code change.

This suite closes that. It is not an eval framework; it is thirteen pass/fail
questions that need no human judgement and that the codebase already has a
stake in:

| case | the hypothesis |
|---|---|
| `scene-length` | a reply respects the resolved length budget |
| `roll-fence` | a roll-requiring prompt emits a closed, parseable ` ```roll ` fence naming a check and actor that exist |
| `absorb` | absorb returns JSON with every required section, and it materializes into applicable edits |
| `owned-lore` | lore owned by an absent character stays out of both the prompt and the reply; lore `known_by` one actor reaches that actor's call and the narrator, not its owner's |
| `turn-taking` | with four NPCs cast and `speaker_turn_taking` on, the reply is carried by the nominated speaker rather than by whoever has been monologuing |
| `natural-prose` | a reply contains none of the stock names or literal banned phrases the selected Natural Prose (Legacy) guide lists, does not repeat a single beat word past the cap or use the enumerated not-X-but-Y forms, and does not flatten into uniform sentence and paragraph length |
| `scene-suggestions` | with two focused drivers and a batch anchor in a custom calendar, the suggestions spread focus coverage instead of cloning one premise, cite only known drivers, and carry dates the anchor rule accepts, written in the calendar's own notation |
| `scene-suggestions-anchor-on` | with a batch anchor `on` an event in a custom calendar, every parsed date is the anchor's own date in the calendar's notation, whatever the model wrote |
| `decide-scene-break` | asked through `decide()` whether a scene whose beat has resolved is over, the reply is the schema's object, answers yes, and says why; the prompt carries the question, the transcript, the schema and the rationale instruction |
| `decide-voice-drift` | asked through `decide()` whether a character with a clipped, contraction-free anchor and a standing correction drifted when she chattered in contractions, the reply is the schema's object, answers `drift`, and gives a corrective no longer than `MAX_NOTE`; the prompt carries the question, every verdict with its description, the transcript and anchor, the correction and the schema |
| `decide-continuity-identity` | asked through `decide()` about each row the duplicate check examines, the reply is the schema's object, maps a reworded duplicate to the existing record by an offered id, and keeps a same-topic question and a concrete continuation new; the prompt carries the question, every examined row's title, the rationale instruction and the schema |
| `decide-continuity-reconcile` | asked through `decide()` about each candidate the reconciliation sweep sends, the reply is the schema's object, keeps a same-topic question distinct, reads a concrete question as a continuation, never merges a thread with a commitment, closes or resolves only on a scene its item shows, and keeps an old or overdue record open when nothing settles it; the prompt carries each vocabulary's question with a pair's directed words folded with their direction, the evidence questions, every candidate's records, the rationale instruction and the schema |
| `decide-speaker` | asked through `decide()` who opens a round in which the player has just put a question to one of two NPCs by name, the reply is the schema's object and picks that NPC (no rationale is asked for); the prompt carries the question, every eligible ref with its name and `grimoire` with its description, null allowed, the observable transcript (gathered by the store helpers the route calls, so a director note in the scene never reaches it) and the schema |
| `decide-rank` | asked through `decide()` to rank five earlier scenes for a turn about Mara's lost ledger, at least the top three, the reply is a list of candidate ids, each once, with the two ledger scenes first and the unrelated one outside the top three; the prompt carries the ranking line, every candidate, the ranking bullet (which renders only beside a rank) and the schema. A synthetic item: 01e's vocabulary converts no call site, so the case is metered under an existing decide task (`continuity-reconcile`) only so a live run resolves the Decision role. Its counterexamples are a duplicate, an unknown id, too short a ranking and a null, each unreadable; its `native` recording is OpenRouter's endpoint answering one pointwise predicate per scene (the rank's `pointwise` question), lifted to tiers of P(true) |
| `decide-select` | asked through `decide()` which of three characters present saw Seraphine take a key (one watching, one with her back turned, one asleep), at least one, the reply is a list of option ids naming the watcher alone; the prompt carries the selection line with its bound, every option, the selection bullet (which renders only beside a multi-select) and the schema. Synthetic like `decide-rank`, metered under `continuity-identity`. Its counterexamples are a duplicate, an unknown id, the empty list (below the bound: never padded) and a null, each unreadable; its `native` recordings are OpenAI's endpoint answering one "is this option selected" predicate per character -- the watcher alone above 0.5, and (`native-undecided`) one character at exactly 0.5, which this select, allowing no null, reads as unreadable |
| `decide-joint` | asked through `decide()` for one action and its target in one question, on a turn where an ally bleeds out unless tended and nothing else threatens, the reply is the key of the legal pair that binds the ally's wound; the prompt carries the joint line, every legal pair (`action=>target`, or the action alone) with its description, the joint bullet (which renders only beside a joint) and the schema. Synthetic, metered under `response-selector`. A joint is one answer, so its counterexamples are the analogues of the others': an illegal pair, the action alone where it takes a target, a two-element list and a null, each unreadable; its `native` recording is OpenAI's endpoint answering the one flattened choice, read back as a pair with its distribution |

## Running it

The venv's interpreter lives under `bin/` on macOS/Linux and `Scripts/` on
Windows; both forms are spelled out here for the same reason the rest of the
repo's docs do it, and `test_install_scripts.py` holds this file to it.

```sh
# replay: score the checked-in recordings. Offline, no API key, deterministic.
backend/.venv/bin/python evals/run.py
backend\.venv\Scripts\python.exe evals\run.py

# just one case
backend/.venv/bin/python evals/run.py --case roll-fence
backend\.venv\Scripts\python.exe evals\run.py --case roll-fence

# live: one real generation per case, on the model the app routes its task to
backend/.venv/bin/python evals/run.py --live
backend\.venv\Scripts\python.exe evals\run.py --live

# ...and save each reply as that case's new baseline recording
backend/.venv/bin/python evals/run.py --live --record
backend\.venv\Scripts\python.exe evals\run.py --live --record

# live on another model, and with one decide backend forced (below)
backend/.venv/bin/python evals/run.py --live --provider ID --model NAME --decide-backend native
backend\.venv\Scripts\python.exe evals\run.py --live --provider ID --model NAME --decide-backend native

# live on two decide backends, three times each, saved as a run file
backend/.venv/bin/python evals/run.py --live --decide-backend native --decide-backend structured --repeat 3 --out evals/out/run.json
backend\.venv\Scripts\python.exe evals\run.py --live --decide-backend native --decide-backend structured --repeat 3 --out evals\out\run.json

# compare saved run files. Offline: reads the files and nothing else.
backend/.venv/bin/python evals/run.py --compare evals/out/a.json evals/out/b.json
backend\.venv\Scripts\python.exe evals\run.py --compare evals\out\a.json evals\out\b.json

# the decide gate: today's parse against the structured one. Offline.
backend/.venv/bin/python evals/run.py --gate
backend\.venv\Scripts\python.exe evals\run.py --gate
```

### What each mode can and cannot show

- **`--gate` compares parsers on recorded shapes.** It feeds hand-recorded
  replies to today's parser and to `decide()`'s, and scores what the call site
  would store from each. It says the structured parse reads every shape
  today's did; it says nothing about what a model would write. Offline, no
  key, and it refuses `--live`, `--record`, `--case`, `--provider`,
  `--model` and `--decide-backend`.
- **Replay grades recorded output.** It holds the prompt contract (every
  `prompt.*` check runs on the freshly assembled prompt) and the graders,
  against a fixed recording (below).
- **`--live` measures whether a model follows the prompt** on the model the
  app routes each case's task to. For a decide case (one with a `schema`)
  that is the full decide chain of its decide resolution -- the Decision
  role, unless the Models page routed the task elsewhere -- answered by
  `inference.run_stages` down `inference.stages`, exactly as `decide` sends
  it: the selection's own backend (natively for a model that cannot
  generate, structured with the schema otherwise), then the role fallback
  where it is a stage of its own. It **costs money**, and is never run
  without the user's explicit approval; `--record` too, since it is a live
  run.

Replay also runs under pytest (`backend/tests/test_evals.py`), and so does the
decide gate (`backend/tests/test_decide_gate.py`): both are part of `make
check` and of CI (`.github/workflows/ci.yml`), so neither waits for someone to
remember the CLI.

### What replay can and cannot catch

Worth being exact about, because it is easy to over-trust. Replay scores a
**fixed** recording, so nothing it does to the *output* can react to a template
edit. What it does react to:

- **the prompt-contract checks** (`prompt.*`), which run against the freshly
  assembled prompt on every case. For the budget, the reply format and the roll
  protocol these render the section template and require its output verbatim in
  the prompt — so every value the section interpolates is covered, not just a
  couple of hand-named tokens. Absorb requires each key of its contract. Delete
  a section from `scene/system.j2`, empty the section template, break a
  variable feeding it, or drop a key from `absorb/system.j2`, and replay fails
  offline and immediately.
- **owned-lore containment**, which is a prompt-side property outright.
- **the graders and fixture assembly** themselves.

What it cannot catch: a template edit that keeps every instruction present but
*rewords* it into something the model follows less well. That is a real model
behaviour question and only `--live` answers it. Requiring the section's own
render, rather than pinning prose, is deliberate — a reword moves both sides
together and stays green, because `templates/` is meant to be edited freely.
What must not change silently is whether the instruction is there at all.

The `natural-prose` case explicitly selects the optional Natural Prose (Legacy)
style guide. It is the sharpest example of the limit above, and is
worth stating plainly. Its output-side `slop.*` checks score a fixed recording,
so **nothing they report says whether the selected prose guide works** — that is
a live-behaviour question, and as with `turn-taking`, one live run is an
anecdote. What they hold offline is that the graders still work, that the
instructions they grade against are still in that guide (`slop.list_current`,
a one-way guard: a phrase removed from the template fails loudly, a phrase
added is ungraded until mirrored in `evals/slop.py`), and that a collapsed
generation cannot score green (`slop.measurable`).

The graded set is also a strict subset of what the block asks for. The
template's semantic instructions — the rule of three, redundant adjective
pairs, explaining an emotion just shown, decorative metaphor, and the three
qualifier-dependent phrases — are not gradable by regex and are listed as
ungraded in the design spec. A green case means the graded subset held.

### A live decide case, and comparing its backends

A decide case's answers are written back as the structured reply its graders
read (`decisions.render`), whichever backend answered, so one grader scores
both. The report names what answered on the case's line: `(backend:
native)` or `(backend: structured)`, or -- when a fallback stage answered
the items the primary failed -- each backend with how many items it
answered, in item order: `(backend: native 3, structured 4)`. Where a stage
failed some items of a batch, each failure's cause follows (`; failed:
bad_response: ...`), so a failed answer check says why.

A native endpoint is asked for answers only, never a rationale. So on a
case whose prompt asks for one (`decide-scene-break`, `decide-voice-drift`)
an item a native endpoint answered passes `decide.rationale` as `n/a:
answered natively`, listed under the case in the report rather than
dropped; an item answered structured keeps the real check, and fails it
without a rationale.

The `prompt.*` checks of a live decide case grade the case's own prompt,
built fresh -- what a structured stage sends. A native item sends no such
prompt (its request is the decisions endpoint's own body), so on a native
run those checks say nothing about what was sent; the answer checks are
the measurement.

Two flags change what a live run sends, and both need `--live`:

- **`--provider ID --model NAME`** runs every case on that selection instead
  of the routed one, through the seam a reroll uses (`override_inference`):
  the same meanings and the same refusals. It is a per-run override and
  writes nothing to settings -- `config.md`, the connections and the model
  facts are as they were.
- **`--decide-backend chain|native|structured`** says how a decide case is
  answered. `chain` (the default) is what production sends, above. `native`
  and `structured` force that one backend on the resolution's primary,
  without its fallback. `native` is refused for a connection kind with no
  native decisions endpoint, a provider preset that never decides natively,
  or a model known (not guessed) unable to decide natively; `structured`
  for a model known unable to generate. An `unknown` refuses neither. A
  chain with no stage at all (`chain` on a model that can do neither) is
  refused too. A refusal is one sentence and exit 2, before any case is
  sent. The flag can be **repeated**: each backend is a configuration of
  its own (`c1`, `c2`, ...), every decide case runs once per backend on the
  same resolution, and a generate case, which no backend changes, runs once
  and stands for every configuration. Every backend is checked before
  anything is sent, so one refusal refuses the run.
- **`--repeat N`** (1 to 10) runs each (configuration, case) N times, to see
  the variance. It **multiplies what the run spends by N**. `--record` with
  `--repeat` over 1, or with several backends, is refused: it would be
  ambiguous which reply becomes the baseline.
- **`--out PATH`** writes the run file (`eval-run`, version 1): each case's
  checks, wall time, harvested ledger rows, calls and items, and the
  aggregate -- never a prompt, a reply or a provider's error text (errors
  are kept as kind and HTTP status, and each check's detail is cut to 200
  characters). It does name your providers and models, so nothing is
  written without `--out`; `evals/out/` is the suggested place, and git
  ignores it. A replay run can write one too (no money, no time).
- **`--compare FILE ...`** prints one table from run files alone: a row per
  case (and per item of a decide case), a column per configuration of each
  file, each cell the pass count over repeats, the median wall time, and the
  tokens and money summed across repeats. A structured chunk's money is the
  chunk's, on the case's row; an item cell says `(chunk)`. A case a
  configuration did not run is a blank cell, never a zero. It reads no
  store, no settings and no rates, and takes no other mode flag; a file it
  does not know (another format or version) is one sentence and exit 2.

To compare the two backends, compare them **on one model**: a model that
both generates and decides natively, in one run --

```sh
backend/.venv/bin/python evals/run.py --live --provider ID --model NAME --decide-backend native --decide-backend structured --case decide-scene-break --case decide-voice-drift --case decide-speaker --case decide-continuity-identity --case decide-continuity-reconcile --out evals/out/backends.json
backend\.venv\Scripts\python.exe evals\run.py --live --provider ID --model NAME --decide-backend native --decide-backend structured --case decide-scene-break --case decide-voice-drift --case decide-speaker --case decide-continuity-identity --case decide-continuity-reconcile --out evals\out\backends.json
```

-- which prints the comparison table after the report, and keeps it in the
run file for `--compare` later. A native-only model
against a structured one would compare the models as well as the backends.
That comparison is the measurement the later "native first" decision (spec
16) waits on: the chain serves a model that can generate structured until
native wins on evals. Like every live run it **costs money**, and is never
run without the user's explicit approval; point it at a throwaway
`GRIMOIRE_HOME` holding only the chosen provider's connection and key,
rather than repointing your real store's Decision role.

### What a live run reports

Beside pass or fail, each live case prints what it cost and how long it took,
in the ledger's own words:

```
  [ok  ] decide-continuity-reconcile.compliant  (backend: native 3, structured 4)
           wall 4.21s  calls 5 (stage 0: native 3/3; stage 1: structured 2/2)  tokens 8,112 / 1,040  billed $0.0091  modelled ~$0.0012  incomplete: 3 unpriced
```

- **Wall time** is measured once around the case's model work. A call's own
  `duration_ms` is summed only where it is labelled `call time` (each
  route / backend / hop row of the aggregate): native items run several at
  once, so their durations can add up to more than the case took.
- **The three money columns** -- `billed` (what a provider charged),
  `sub-equiv` (a subscription's per-token equivalent) and `modelled` (your
  rates times the counts, for a call no provider priced) -- are each under
  their own label and are **never added together**. A column with no calls in
  it is left out; a price nobody reported is never shown as `$0.00`. A case
  with nothing priced says `cost: not reported`; one with some calls unpriced
  says `incomplete: N unpriced`.
- **Tokens** print `in / out`. A side no call counted is `not reported`, one
  some calls counted says `(N of M counted)`, never a `0`.
- A case that ran several tasks (a play case's pick, turn and follow-ups) is
  summed across all of them, with a `by task` block beneath. After the cases
  comes an aggregate keyed by route, backend and hop.
- A structured call answers up to eight items at one price, so its figures
  are the call's: they are never divided among its items.

### What a live run reads

`--live` reads model settings and credentials from your **real** store while
every case still builds its campaign in a throwaway `GRIMOIRE_HOME`. Each case
names the task the app meters it under (`Case.task`), and a live run sends it
wherever the app's own seam would — the role or route chosen on the Models
page, with its fallback, the model's facts and the route's preset -- and the
per-token rates (`pricing.json` and each provider's stated model rates), read
once before the first case, so every modelled figure in the run is priced
against one table. In the recommended setup (a throwaway `GRIMOIRE_HOME`
holding only the chosen provider), the rates come from that store, so the run
reports modelled figures only if a rate was set there. No campaign, world or
character content is read or written.

Every call a live case sends is metered by the app's own meter, into the
case's throwaway home, and harvested from there into the run's report before
that home is deleted: no eval row ever reaches your library's ledger, its
Costs page, a campaign budget or a rollup. The money was still spent on your
key, so your provider's invoice shows it; the eval report is where it is
shown. A tripwire refuses any case whose home turns out to be the real one,
before its fixture is built and again before its rows are read. The one real-store write it can make is the same one-off
`llm_connections/` migration the app itself runs at startup, on a library old
enough to predate that feature. Live runs cost API credits, so they are opt-in
and the result is a report, never a gate.

## The decide gate

`decide()` (spec 7.4) replaces call sites' hand-written prompts and
parsers -- the scene-break check, the voice-drift check, the next-speaker
pick, absorb's duplicate check and the reconciliation sweep -- with one decision
contract answered by structured generation. Each
call site switches only when the structured parse **equals or beats** today's
on recorded replies, offline: `evals/gate.py`, run by `--gate` and by pytest
(`backend/tests/test_decide_gate.py`).

- **A conversion** (`gate.Conversion`, one per call site, in `gate.GATES`)
  names the decision items the production builder makes from a fixed fixture,
  today's parser, the production mapping from a parsed answer to what the
  call site stores, and its corpus.
- **A corpus** is `gate/<conversion>.json`: a list of entries, each a reply
  to today's prompt (`legacy`) and a reply of the same shape to the decide
  prompt (`decide`), with what the reply means (`intended`) and, where a
  conversion needs them, its other replies (`aux`). It starts from today's
  parser tests: every input string those tests feed the legacy parser is the
  `legacy` of some entry, and `test_every_legacy_parse_case_is_a_gate_entry`
  holds that before the tests themselves are deleted. The reconciliation
  sweep's parse tests build their replies over a seeded store, so their
  elements are copied verbatim into `gate.RECONCILE_SOURCES`, each with its
  test's own maps from runtime keys and scenes to the gate's store-free
  fixture; `gate._adapt` builds the legacy reply and `gate._twin` its decide
  twin from them, mechanically.
- **Outcomes are whole.** Each side is scored by the value the call site acts
  on -- a verdict with its stored reason, a speaker with its issue string --
  never a boolean alone. Where the call site checks a parsed value further
  before storing it (voice drift's `check_failure`), that production check is
  the conversion's `settle`, applied to both sides alike. `gate.judge`
  guards it both ways: a `settle` that binds anything from `legacy.py`, at
  any depth (through a helper, a closure or a partial), would
  carry today's parse onto the decide side, and one that merges outcomes the
  corpus tells apart would pass any gate, so each is refused before scoring
  (`test_decide_gate.py` plants both). The speaker's
  outcome is `_select`'s `(next, issue)` pair, so both of today's issue
  strings -- `missing or invalid handoff` and `ineligible or repeated
  speaker` -- are part of what the gate compares.
- **The rule**: wherever today's parse reaches `intended`, the decide parse
  must too. It may win an entry today's parse loses; it may never lose one.
  `--gate` prints one line per conversion
  (`<id>: legacy 19/24, decide 24/24 -- PASS`) and exits non-zero on a
  regression, naming it.
- **A deliberate change says so.** An entry whose `intended` is a change of
  behaviour the switch brings by ruling, rather than today's reading of the
  reply (a title cut to its first line, a case-folded speaker ref), carries a
  `ruling`: the sentence saying why. `--gate` prints each one under its
  conversion's line, with whether today's parse loses it, so a legacy loss by
  ruling is visible beside the score instead of counted as a parser win.
- **A conversion's batch is its fixture items, at most one call's worth.**
  `gate.judge` refuses a conversion whose items would take more than one
  `decisions.chunks` call (a corpus reply answers one call): the duplicate
  check and the reconciliation sweep each send one item per row or candidate
  and are chunked in production (`decisions.MAX_ITEMS_PER_CALL`, eight), but
  their fixtures are sized to a single chunk, so the gate scores what one
  call's reply is parsed into and never a chunk boundary. A chunk that failed
  beside one that answered (its items stay unchecked or unanswered) is the
  route and store suites' ground, not the corpus's. A corpus reply
  answers the whole batch, keyed by item index, as a structured call does, and
  the conversion's `decide` is handed every item's result at once. Legacy
  replies answer a whole batch too (`{"decisions": [...]}` across rows or
  candidates), so a batch fixture is the shape both sides share; the
  conversions with one item read `results[0]`.
- **Today's parsers are frozen** in `legacy.py`, verbatim, and imported from
  nowhere in production: the switch deletes the originals, and the gate has to
  keep measuring against what they did. The copies are never edited.
  `legacy.py` imports the standard library alone, and every conversion's
  `legacy` must call through it and bind no production parser module
  (`test_every_conversion_parses_today_through_the_frozen_copy`).

What it cannot show is the other half of the switch: whether a model, given
the decide prompt and schema, writes the right answer. Each conversion lands
with a permanent `decide-*` case beside its corpus, which holds that prompt's
contract offline, and `--live` (above) is the only thing that asks a model.

## How a case works

```
build()          populates the current GRIMOIRE_HOME with a fixture campaign
prompt(ctx)      assembles the messages with the SAME production builder the
                 app calls (context.build_messages / absorb.build_prompt)
grade(ctx, out)  scores the output, delegating to evals/graders.py
recordings       the checked-in outputs replay mode scores
```

Two rules keep this honest, and both are enforced by tests:

**The graders re-use production parsers.** `scenes.split_reply`,
`length_drift.measure`, `fence.FenceWatcher`, `absorb.parse_output`. A grader
that parsed output its own way would stop testing the app the moment the app's
parser changed, and would sail straight through the regression it exists to
catch. The actor-length case strips preparation and control blocks through
`response_protocol.ResponseWatcher` and `state_fence.split_block`, then scores
visible prose against exact word and paragraph ceilings, with no minimum
beyond a nonempty reply. The legacy ensemble `grade_length` helper retains
the production drift band as a diagnostic; it is not actor ceiling compliance.

**Every case carries a counterexample, and names what it violates.** A
counterexample declares the exact set of checks it must trip
(`Recording("empty", ("length.words",))`), and replay asserts set
equality. "It must fail somehow" is not enough: `scene-length.bloated` trips
both word and paragraph ceilings, so a bare fail-expectation stays green
even if the word counter stops working, hidden behind its neighbour.
`backend/tests/test_eval_graders.py` goes further and pins the graders on
minimal inputs — one test per failure mode, including the ones no recording
exercises. Two checks there are deliberately not independent gates and say so:
`fence.parses` can only fail alongside `fence.check_known` and is kept for the
diagnostic it gives, and `length.measurable` fires only for a reply made
entirely of forged synthetic-speaker blocks.

**The graders score raw model output, not normalised output.** `absorb`'s
grader reads the extracted JSON object rather than `parse_output`'s result,
because that parser is deliberately tolerant — it substitutes `[]` for a
missing or wrong-typed section and turns a JSON `null` into the string
`"None"`. Grading its output would make every "is this a list?" question answer
yes regardless of what the model sent. The contract itself *is* derived from
`parse_output` (its key set, with defaults telling text from list), so a
section added to absorb is graded from the day it lands.
`decide-continuity-identity` scores the duplicate check's reply the same way:
`identity.answers_of` turns a word outside the decisions into `uncertain`, so
`identity.enum` is scored on the parsed answers' `NOT_AN_OPTION` detail rather
than on the mapping. An `existing` is folded with the id it names
(`existing:<id>`, spec 7.4), so one naming an unoffered record is no option
either; `identity.known_ids` tells it from an unknown word by its raw
spelling. Its `build` pins which records `identity.examine` offers
each row, and through which clause, so a change to the similarity floors fails
there rather than leaving the case asking about nothing.
`decide-continuity-reconcile` follows the same rule for the same reason:
`reconcile.proposals_of` reads a word outside a candidate's vocabulary, a
direction the relation does not allow and a closure with no shown evidence
scene all as `uncertain`, so `reconcile.enum` and `reconcile.evidence` score
the parsed answers. Its candidates are specified by hand, one per §28.10 case
2–8, and sent through the production `reconcile.build_payload` /
`build_items`, which key them `c1`… in the order they are sent (so case 2 is
`c1`). Its `prompt` asserts that each case is sent under the vocabulary it
needs and that every scene is known evidence, so a change there fails at build
rather than leaving a recording citing a scene the item would not offer. A
verdict check reads the decision answer alone -- a pair's directed word folded
with its direction (`continuation_b_of_a`), which `reconcile.unfolded` splits
back -- and `reconcile.evidence` judges the citation, so "the wrong call" and
"the right call, unfounded" stay separable.

**Both continuity cases carry a native recording** (`native`), the compliant
verdicts as a decisions endpoint returns them: a JSON list of response
bodies, one per item, in the adapter's wire shape (`Recording.native` names
it), which replay reads through that adapter's `decision_result` and writes
back with `decisions.render`, as a live native run does. A native endpoint
answers each question of an item on its own, which is why no continuity
question depends on another's answer, nor refers to one: G's `from` / `to`
and `id` did, came back none natively, and lost every directed pair verdict
and every native `existing` -- what these recordings, answering only the
folded choice, would have shown. The evidence questions were the last: asked
"for" the status words, they came back none natively and every closure fell
to `uncertain`; each now asks which shown scene, if any, shows a record
settled, and `_decide` keeps the rule that a status word needs one.
`decisions.render` writes every unread native answer as null, so the native
results are kept beside the text (`ctx["native_results"]`, by replay and by a
live run alike) and the two continuity graders read a native item from them:
a refusal or an abstention fails `covers` as the app leaves that item
unanswered, and a value naming no option keeps what it named
(`Answer.stated`), so an unoffered `existing:<id>` still fails
`identity.known_ids`. The counterexamples `native-unknown-id`,
`native-refused` (identity) and `native-unfounded` (reconcile: a closure
answered with every evidence slot none) show each.
`scene-suggestions` decodes the reply with the app's `suggest.raw_suggestions`,
keeps only the entries `suggest.is_card` keeps (a title and a premise, the
cards the player is shown), and judges claims and dates with `suggest.claim`
and `suggest.check_date`, but
scores two things raw: `suggest.known_refs` and `suggest.anchor_known` read the
model's own `drivers` and `time_anchor`, because `claim` drops an unknown ref
and the batch anchor overrides the model's, so the claimed result could never
show either miss. `suggest.date_consistent` also requires the raw date string
to round-trip the calendar's own `parse` and `format` (outside an `on` batch,
whose date is derived rather than the model's), because the tolerant
normalizer reads the friendly form ("9 Thaw 5") too. Both cases run on an
eval-owned plugin calendar written into the throwaway store, so the date rules
are exercised in a notation no built-in provider knows; the prompt side
requires the quoted action words, keys, high-pressure states and control refs,
and renders the drivers and controls addenda whole.

## Recordings

`recordings/<case>.<variant>.<md|json>`

- `<case>.compliant.*` is the **baseline** — the only variant `--record`
  overwrites. Today's baselines are hand-authored; replace them with real
  model output by running `--live --record`.
- Every other variant is a permanent hand-authored counterexample
  (`bloated`, `collapsed`, `no-fence`, `unknown-check`, `unclosed`,
  `truncated`, `no-summary`, `laundered`, `leaked`, `monologue`, `out-talked`,
  `chorus`, `slop`, `flat`, `terse`, `undecodable`, `merged`, `unknown-id`,
  `eager`, `unfounded`, `timid`, `cloned`, `bad-date`, `unknown-ref`,
  `wrong`, `no-reason`, `no-note`, `long-note`, `off-roster`, `abstained`)
  and is never touched by a live run. So is a `native` recording, which is
  hand-authored and passes: response bodies of a native decisions endpoint
  (above).

A file in `recordings/` that no case claims fails `test_no_orphan_recordings` —
renaming a case without deleting its old files would otherwise leave dead
fixtures scoring nothing.

## Adding a case

1. Write `build_*`, wire it to a grader, append a `Case` to `CASES` in
   `cases.py`.
2. Hand-author `recordings/<id>.compliant.*` plus at least one counterexample,
   declaring on each the exact checks it must trip.
3. `python evals/run.py --case <id>` until the baseline passes and each
   counterexample fails **on the checks you wrote it to violate** — the set
   equality makes "it happened to fail on something else" a failure too.

## `turn-taking` and issue #82

This case exists to answer one open question, and it is worth saying which.
`store/context/speaker.py` nominates a lead speaker for every group turn, so
that four cast NPCs do not leave one monologuing while three stand silent.
#82 asks whether that is enough, or whether the fallback — one model call per
NPC, sequentially, each persisted before the next — has to be built after all.

That is a question about whether the model *obeys* a nomination, so replay
cannot answer it and neither can pytest. What they hold is the prompt side:
`prompt.active_speaker` renders the whole section from the nomination the
fixture computed and requires it verbatim in the assembled prompt, so the
layer going away, or the flag plumbing breaking, is caught offline.
The same case hosts the author's-note checks (play controls V):
`prompt.authors_note` requires the rendered campaign note in the narrator's
prompt, and `prompt.authors_note_scoped` requires Seraphine's character note in
her own call and in neither Tobin's nor the narrator's.

The answer itself comes from:

```sh
backend/.venv/bin/python evals/run.py --live --case turn-taking
backend\.venv\Scripts\python.exe evals\run.py --live --case turn-taking
```

The fixture is a four-hander mid-monologue: every NPC has spoken, at strictly
different distances back, and the last three blocks all belong to one of them.
A green `turns.*` says the nomination was followed on a turn shaped exactly
like the failure, which is the per-turn form of the multi-turn failure #82
describes — with a lead named every turn, the monologue can only re-form if
single turns ignore it. A red one says the loop is back on the table.

"Followed" is judged in words, not blocks: a reply that answers the nomination
with one obliging line and then hands the floor back to whoever has been
talking all scene has not taken turns, and counting blocks scores it 1-1.

One run is an anecdote. Repeat it across a few connections and models before
either closing #82 or paying for N calls a turn on the strength of it.

The case outlives the decision either way. If #82 closes because the layer
works, this is what keeps it working: "the model still follows the Active
speaker section" is a live-behaviour claim with exactly the standing of the
length budget's, and it is one reworded section away from quietly ceasing to
be true.

Fixture content uses invented placeholder names only (Realm, Saltmarch,
Seraphine Vale, Mara, Winifred, Rowan, Tobin). See CLAUDE.md: real world, campaign and
character names must never enter this repo, not even as examples.
