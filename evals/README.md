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
| `continuity-identity` | the identity resolver maps a reworded duplicate to the existing record and keeps a same-topic question and a concrete continuation new; a row is only ever offered records of its own type |
| `continuity-reconcile` | the reconciliation sweep keeps a same-topic question apart (distinct or related), reads a concrete question as a continuation or subthread of the broad one, never merges a thread with a commitment, closes a thread or resolves a commitment only on a shown beat, and keeps an old or overdue record open when nothing shown settles it |
| `scene-suggestions` | with two focused drivers and a batch anchor in a custom calendar, the suggestions spread focus coverage instead of cloning one premise, cite only known drivers, and carry dates the anchor rule accepts, written in the calendar's own notation |
| `scene-suggestions-anchor-on` | with a batch anchor `on` an event in a custom calendar, every parsed date is the anchor's own date in the calendar's notation, whatever the model wrote |
| `decide-scene-break` | asked through `decide()` whether a scene whose beat has resolved is over, the reply is the schema's object, answers yes, and says why; the prompt carries the question, the transcript, the schema and the rationale instruction |
| `decide-voice-drift` | asked through `decide()` whether a character with a clipped, contraction-free anchor and a standing correction drifted when she chattered in contractions, the reply is the schema's object, answers `drift`, and gives a corrective no longer than `MAX_NOTE`; the prompt carries the question, every verdict with its description, the transcript and anchor, the correction and the schema |
| `decide-speaker` | asked through `decide()` who opens a round in which the player has just put a question to one of two NPCs by name, the reply is the schema's object and picks that NPC (no rationale is asked for); the prompt carries the question, every eligible ref with its name and `grimoire` with its description, null allowed, the observable transcript (gathered by the store helpers the route calls, so a director note in the scene never reaches it) and the schema |

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

# the decide gate: today's parse against the structured one. Offline.
backend/.venv/bin/python evals/run.py --gate
backend\.venv\Scripts\python.exe evals\run.py --gate
```

### What each mode can and cannot show

- **`--gate` compares parsers on recorded shapes.** It feeds hand-recorded
  replies to today's parser and to `decide()`'s, and scores what the call site
  would store from each. It says the structured parse reads every shape
  today's did; it says nothing about what a model would write. Offline, no
  key, and it refuses `--live`, `--record` and `--case`.
- **Replay grades recorded output.** It holds the prompt contract (every
  `prompt.*` check runs on the freshly assembled prompt) and the graders,
  against a fixed recording (below).
- **`--live` measures whether a model follows the prompt** on the model the
  app routes each case's task to. For a decide case (one with a `schema`)
  that is the decide resolution -- the Decision role, unless the Models page
  routed the task elsewhere -- sent with the schema, in the provider's
  structured mode wherever that model is known to support it: exactly what
  production sends. It **costs money**, and is never run without the user's
  explicit approval; `--record` too, since it is a live run.

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

`--live` reads model settings and credentials from your **real** store while
every case still builds its campaign in a throwaway `GRIMOIRE_HOME`. Each case
names the task the app meters it under (`Case.task`), and a live run sends it
wherever the app's own seam would — the role or route chosen on the Models
page, with its fallback, the model's facts and the route's preset. No campaign, world or character content is read or
written. The one real-store write it can make is the same one-off
`llm_connections/` migration the app itself runs at startup, on a library old
enough to predate that feature. Live runs cost API credits, so they are opt-in
and the result is a report, never a gate.

## The decide gate

`decide()` (spec 7.4) replaces three call sites' hand-written prompts and
parsers -- the scene-break check, the voice-drift check and the next-speaker
pick -- with one decision contract answered by structured generation. Each
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
  holds that before the tests themselves are deleted.
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
  `decisions.chunks` call (a corpus reply answers one call). A corpus reply
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
`continuity-identity` scores the resolver's reply the same way: the identity
parser reads an unknown decision word as `uncertain`, so only the raw object
can fail `identity.enum`. Its row keys are the one thing read the app's way
(a `Row r1` key is one the app accepts). Its `build` pins which records
`identity.examine` offers each row, and through which clause, so a change to
the similarity floors fails there rather than leaving the case asking about
nothing.
`continuity-reconcile` follows the same rule for the same reason:
`reconcile.parse_output` reads a word outside a candidate's vocabulary, a
direction the relation does not allow and a closure with no known evidence
scene all as `uncertain`, so `reconcile.enum`, `reconcile.shape` and
`reconcile.evidence` score the raw object, and only candidate keys are read the
app's way (a `Candidate c1` key is one the app accepts). Its candidates are
specified by hand, one per §28.10 case 2–8, and sent through the production
`reconcile.build_payload` / `build_prompt`, which key them `c1`… in the order
they are sent (so case 2 is `c1`). Its `prompt` asserts that each case is sent
under the vocabulary it needs and that every scene is known evidence, so a
change there fails at build rather than leaving a recording citing a scene the
parser would refuse. A verdict check reads the decision word alone, and
`reconcile.evidence` judges the citation, so "the wrong call" and "the right
call, unfounded" stay separable.
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
  and is never touched by a live run.

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
