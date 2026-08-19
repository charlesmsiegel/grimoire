# evals/ — scoring what the model actually writes

grimoire's primary output is LLM prose, and a lot of this codebase exists only
to shape it: the prose style chain, the length budget knobs and drift
correction, the natural-prose block, scene-suggestion grounding, the roll-fence
protocol. Every one of those is a **hypothesis about model behaviour**. The
pytest/vitest suites verify the plumbing around them — that the right variables
reach the right template — but nothing verified the hypothesis itself, and a
template edit takes effect live, with no restart and no code change.

This suite closes that. It is not an eval framework; it is five pass/fail
questions that need no human judgement and that the codebase already has a
stake in:

| case | the hypothesis |
|---|---|
| `scene-length` | a reply respects the resolved length budget |
| `roll-fence` | a roll-requiring prompt emits a closed, parseable ` ```roll ` fence naming a check and actor that exist |
| `absorb` | absorb returns JSON with every required section, and it materializes into applicable edits |
| `owned-lore` | lore owned by an absent character stays out of both the prompt and the reply |
| `turn-taking` | with four NPCs cast and `speaker_turn_taking` on, the reply is carried by the nominated speaker rather than by whoever has been monologuing |

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

# live: one real generation per case through your ACTIVE LLM connection
backend/.venv/bin/python evals/run.py --live
backend\.venv\Scripts\python.exe evals\run.py --live

# ...and save each reply as that case's new baseline recording
backend/.venv/bin/python evals/run.py --live --record
backend\.venv\Scripts\python.exe evals\run.py --live --record
```

Replay also runs under pytest (`backend/tests/test_evals.py`) — this repo has
no CI, so pytest is the gate.

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

`--live` reads credentials from your **real** store (the connection you picked
on the Configuration page) while every case still builds its campaign in a
throwaway `GRIMOIRE_HOME`. No campaign, world or character content is read or
written. The one real-store write it can make is the same one-off
`llm_connections/` migration the app itself runs at startup, on a library old
enough to predate that feature. Live runs cost API credits, so they are opt-in
and the result is a report, never a gate.

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
catch. The length grader scores against `length_drift.TRIM` for the same
reason: that constant *is* this codebase's definition of "this reply ran long",
so tuning it moves the eval with it.

**Every case carries a counterexample, and names what it violates.** A
counterexample declares the exact set of checks it must trip
(`Recording("collapsed", ("length.reply_words",))`), and replay asserts set
equality. "It must fail somehow" is not enough: `scene-length.bloated` trips
four knobs at once, so a bare fail-expectation stays green even if the word
counter stops working entirely, hidden behind its three neighbours.
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

## Recordings

`recordings/<case>.<variant>.<md|json>`

- `<case>.compliant.*` is the **baseline** — the only variant `--record`
  overwrites. Today's baselines are hand-authored; replace them with real
  model output by running `--live --record`.
- Every other variant is a permanent hand-authored counterexample
  (`bloated`, `collapsed`, `no-fence`, `unknown-check`, `unclosed`,
  `truncated`, `no-summary`, `laundered`, `leaked`, `monologue`, `out-talked`,
  `chorus`) and is never touched by a live run.

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
