# Measuring the natural-prose block: an offline slop grader

**Date:** 2026-09-02
**Status:** Draft for review

## Problem

`templates/scene/sections/natural_prose.j2` ships in every scene and opener
system prompt. It bans a list of stock phrases, rations a list of beat words,
forbids four named constructions, and asks for varied sentence and paragraph
rhythm. It is, in the words of `evals/README.md`, "a **hypothesis about model
behaviour**" — and it is the only one of the five hypotheses that suite names
which has no case.

Two consequences follow, and they are different in kind.

**The block can vanish from the prompt silently.** `natural_prose` is a
`LOCK_IN` section in `store/context/assemble.py`'s `SECTIONS` catalog, so the
packer will never drop it under budget pressure. Nothing else protects it. A
deleted catalog entry, an emptied template, or a broken render is caught by no
test today: `test_evals.py` grades the assembled prompt on five cases and none
of them names this section. Every other instruction block the codebase cares
about — the budget, the reply format, the roll protocol, the active speaker,
the voice policy, the secrecy block, available art — is held in the prompt by a
`prompt.*` check. This one is not.

**Nothing measures the prose.** The block asserts that replies will vary in
sentence length and paragraph shape, ration their beats, and keep em dashes as
seasoning rather than structure. No instrument in the repo can say whether any
of that is true of a given reply. That matters most for the failure the
natural-prose work already anticipated and wrote down: the pink-elephant
effect, where naming a banned phrase in the prompt makes the model reach for it
more often. The recorded remedy is to trim the ban list in the live template —
a change nobody can currently evaluate, because there is no before and no
after, only an impression.

This spec builds the instrument and closes the prompt-contract hole. It does
not claim to answer the efficacy question offline; see *What this cannot do*.

## Prior art, and what was rejected

The pattern catalogue comes from
[sloptrim](https://github.com/seyedehsanhadi/sloptrim) (Apache-2.0, as is this
repo), which documents 71 AI-writing tells with 62 machine-checked detectors.
Roughly twenty transfer to roleplay prose; the rest are shaped for documents —
bullet injection, signposting, "In conclusion", title case in headings, chatbot
artifacts, citation and attribution preservation, tracking parameters, unfilled
template slots. One is inverted for this use case outright: sloptrim penalises
the *absence* of sentence fragments and run-ons, which fiction wants.

Three parts of that project are deliberately not adopted.

Its **rewrite half** — score, then silently rewrite toward one of four
nonfiction registers — is architecturally hostile here. grimoire's corrective
idiom is feed-forward: `length_drift` and `voice_drift` both push back on the
*next* turn rather than touch what landed, for the same reason as "Grimoire
never edits a fact; the user may." A post-hoc rewriter would edit story nobody
agreed to, in the one artifact that cannot be regenerated. Its four style
profiles would also fight `store/styles.py`, which is this repo's answer to the
same question in the right genre.

Its **benchmark** measures authorship classification (ROC-AUC across five
model arms). In grimoire every reply is machine-written by construction, so
classification is not the question; magnitude is.

Its **plugin machinery** watches agent Edit/Write calls on disk. grimoire's
prose does not arrive that way.

## What this cannot do

Stated up front, because it is easy to over-trust and `evals/README.md` is
already exact about the same limit for every other case.

Replay scores a **fixed** recording. Nothing the slop checks measure on the
output side can react to a template edit, so replay cannot answer "does the
natural-prose block work" or "did trimming the ban list help." That is a
live-behaviour question and only `evals/run.py --live` puts it.

What replay does hold, immediately and offline:

- the prompt-contract check, which renders `natural_prose.j2` and requires its
  output verbatim in the assembled prompt;
- the drift guard, which fails when the grader's hand-kept lists no longer
  match that render;
- the graders themselves, proven failable by counterexample recordings and by
  per-check unit tests.

The instrument is the deliverable. `--live --record` is what turns it into an
answer, and until that has run at least once the thresholds below are
provisional.

## Design

### Ownership: the detector is eval-owned

`evals/slop.py`, a new pure module beside `graders.py`. No store reads, no
network, standard library only.

`evals/README.md` states the rule this has to answer to: "The graders re-use
production parsers... an eval that parses output its own way stops testing the
app the moment the app's parser changes." There is no production slop parser to
re-use, and the precedent for that situation is `COLLAPSE_RATIO`, which is
eval-owned precisely because "the app has no opinion about a reply being too
SHORT."

The app has no opinion about slop either, and that is by design rather than by
omission: `natural_prose.j2` is prescriptive and feed-forward, and measuring
slop in production — a `store/slop_drift.py` sibling to `length_drift.py`
feeding the post-history corrective slot — is deliberately *not* in this scope.
It was gated behind this work, because the corrective slot already carries the
length and voice correctives and a third nagging block sits at exactly the
position where the pink-elephant effect would bite hardest. Eval ownership is
therefore the honest statement of where the opinion currently lives.

If that production module is ever built, its thresholds become the codebase's
definition of "this reply reads flat" and the grader borrows them, exactly as
`grade_length` borrows `length_drift.TRIM` today. To keep that move cheap,
every threshold in `evals/slop.py` is a module-level named constant with its
justification in a comment beside it, and no threshold is written inline.

### Reuse of production parsers

Two dependencies, both required rather than convenient:

- **`scenes.split_reply`** strips speaker markers. Measuring rhythm across raw
  marker lines would count `**Mara:**` as a sentence.
- **The roll fence** must be stripped before any prose measurement, or a
  mechanics scene's fence body reads as monotone narration.
  `store/length_drift.py` already builds a whole-fence pattern from
  `fence.OPENER` in a private `_ROLL_FENCE`, with a comment explaining that a
  second copy of the opener would silently diverge the day `fence.py` changes.
  **Promote `_ROLL_FENCE` to public and import it** rather than write the
  second copy that comment warns about.

Rhythm is measured over the full remaining prose — narration and dialogue
together, markers and fences removed — because that is what a reader reads.

### The checks

Thirteen named checks. Names follow the harness convention: `prompt.*` for the
assembled prompt, `slop.*` for everything else.

#### Template-side (2)

| check | fails when |
|---|---|
| `prompt.natural_prose` | the rendered section is not present verbatim in the assembled prompt |
| `slop.list_current` | a hand-kept list entry no longer appears in that render |

`prompt.natural_prose` uses the existing `graders.grade_prompt_section` with no
template variables — the section is static. It proves presence **under the
default layout only**: `layout.apply(SECTIONS)` lets a user disable or reorder
sections, and the fixture sets no layout.

`slop.list_current` is the one-way drift guard that replaces true derivation.
Drift is caught in the direction that matters — grading a phrase the app has
stopped banning fails loudly — while a phrase *added* to the template is not
graded until someone adds it here. That asymmetry is accepted deliberately: the
recorded pink-elephant remedy is trimming the ban list in the live template,
and this guard turns such a trim into a prompt to update the grader rather than
leaving a stale one behind.

True derivation was considered and rejected. Extracting the lists to data that
the template renders from would match `grade_absorb`'s contract derivation
exactly, but it edits a live, known-good prompt template, and any byte
difference in that render is a real change to every scene generation — held
byte-for-byte by both `scripts/verify_templates.py` and `grade_prompt_section`.
Parsing the phrases back out of the rendered prose was also rejected: the
parser would break on a reword, taxing exactly the free template editing
`evals/README.md` sets out to protect.

#### List-driven (3)

| check | fails when |
|---|---|
| `slop.phrases` | a banned phrase from the template's *Phrases — never use* list appears |
| `slop.stock_names` | a stock name from the template's list appears and is **not** an established name |
| `slop.beat_words` | any single beat word from the template's list repeats more than `BEAT_REPEAT_MAX` times |

`slop.stock_names` takes the fixture's cast and player names and excludes them.
The template's own precedence rule requires this: "Names that already exist in
this scene, cast, or world are fixed; reproduce them exactly, even if they
appear below." A campaign that legitimately contains a Selene must not be
graded as slop for saying so.

`slop.beat_words` is a repetition cap, not a ban, because the template rations
rather than forbids: these are "ordinary words" and the instruction is that
"not every line of dialogue needs a lean, nod, or murmur."

#### Construction (2)

| check | fails when |
|---|---|
| `slop.not_x_but_y` | any of the four disguises the template names appears |
| `slop.answered_question` | a question in **unquoted narration** is answered by a short fragment in the same paragraph |

`slop.answered_question` is restricted to unquoted narration because
`"Where is she?" "Gone."` is ordinary dialogue and would otherwise trip on
every scene. It carries the highest false-positive risk in the set. **If it
proves noisy in practice, the remedy is to delete the check, not to loosen its
threshold** — a rhetorical-question detector that has been widened until it
stops firing is a check that reports success by construction.

#### Rhythm (4)

| check | fails when |
|---|---|
| `slop.sentence_variance` | the coefficient of variation of sentence lengths is below `VARIANCE_MIN` |
| `slop.opener_repetition` | `OPENER_RUN_MAX` or more consecutive sentences share the same two-word opening frame |
| `slop.paragraph_uniformity` | paragraph lengths vary by less than `PARA_VARIANCE_MIN` |
| `slop.em_dash_spacing` | two consecutive paragraphs both contain an em dash |

`slop.em_dash_spacing` is derived directly from the template's own sentence —
"Em dashes, ellipses, italics, and one-word dramatic fragments are seasoning,
not structure — if the last paragraph used one, the next doesn't" — rather than
from a density threshold of our invention. It is the one rhythm check whose
definition the app already states.

Four patterns from the transferable set are deliberately excluded. Stacked
adjective chains and generic rule-of-three both need part-of-speech tagging;
synonym cycling needs a synonym table; mechanical sentence-length alternation
overlaps `slop.sentence_variance` too closely to earn a second check. None can
be done in the standard library without a detector that is mostly wrong.

#### Machine artifacts (2)

| check | fails when |
|---|---|
| `slop.repetition` | `REPEAT_SHARE` or more of the reply's six-word spans are repeated |
| `slop.unicode` | a zero-width, bidi, variation-selector, non-standard-space or mixed-script confusable character appears |

`slop.unicode` earns its place here more than in sloptrim's own use case:
grimoire transcripts are durable and are exported to EPUB, so a stray U+200B is
permanent and travels.

### Thresholds

Every threshold is justified structurally — from what the templates and the
resolved budget actually do — and never from a measurement taken over stored
content. Calibration then sets each constant only so far as to make the
hand-authored compliant baseline pass and the hand-authored counterexamples
trip on exactly their declared checks.

Each constant carries a comment naming that reasoning and stating that it
should be tuned against real prompts later. This follows CLAUDE.md directly:
"Where a constant seems to need a measurement to justify it, justify it
structurally and say it should be tuned against real prompts later."

The weakness this leaves is real and belongs in the code as well as here: the
thresholds are calibrated against one author's idea of slop, not against a
model's. They are provisional until `--live --record` replaces
`natural-prose.compliant.md` with genuine model output. The spec asserts no
accuracy claim for them before that has happened.

### The case

A new `natural-prose` case in `evals/cases.py`, reusing the existing
`_scene_prompt` builder.

**Hypothesis row for the README table:** *the natural-prose block reaches the
prompt, and a reply is free of the stock phrases, constructions and flattened
rhythm it forbids.*

Three properties of the fixture are load-bearing:

- **The `cinematic` length preset.** `scene-length`'s fixture resolves `terse`
  — 150 words, 3 blocks, 1 paragraph — and rhythm statistics over three blocks
  are noise. This case needs a reply long enough for sentence and paragraph
  distributions to mean anything, so it resolves the widest preset
  `store/lengths.py` ships (`cinematic`: 900 words, 7 blocks, 3 paragraphs)
  rather than inventing numbers. Every threshold below is calibrated against
  that shape, so a case that later changes preset must recalibrate.
- **No prose style guide set.** `natural_prose.j2` states that "The prose style
  guide, when one is set, overrides the rhythm guidance below." A styled scene
  is therefore out of this case's scope by the template's own precedence, and
  the case must not be extended to one without revisiting the rhythm checks.
- **Invented placeholder names only**, drawn from the codebase's existing
  fixture vocabulary, none colliding with the template's stock-name list. Per
  CLAUDE.md, no real world, campaign or character name may enter this repo.

### Recordings

| file | must trip |
|---|---|
| `natural-prose.compliant.md` | nothing (baseline; the only variant `--record` overwrites) |
| `natural-prose.slop.md` | `slop.phrases`, `slop.stock_names`, `slop.beat_words`, `slop.not_x_but_y`, `slop.answered_question` |
| `natural-prose.flat.md` | `slop.sentence_variance`, `slop.opener_repetition`, `slop.paragraph_uniformity`, `slop.em_dash_spacing` |

Two counterexamples rather than one, split along the same reasoning that makes
`scene-length.bloated` name four knobs at once: `flat.md` is rhythmically
monotone but clean of every literal, so the rhythm checks cannot pass unnoticed
behind a phrase hit, and `slop.md` keeps varied rhythm so the reverse is also
true. Set equality is what makes both bite.

`slop.repetition` and `slop.unicode` get **no recording** and are pinned in
`backend/tests/test_eval_graders.py` only. A checked-in file of hand-inserted
zero-width characters is invisible in review, trivially corrupted by an editor
or by git's line-ending handling, and would make the fixture's correctness
unverifiable by reading it. `evals/README.md` already sanctions unit-test-only
coverage for "the ones no recording exercises."

## Files touched

| file | change |
|---|---|
| `evals/slop.py` | new — lists, thresholds, measurement functions |
| `evals/graders.py` | new `grade_slop`, delegating to `slop.py` |
| `evals/cases.py` | `build_natural_prose`, `grade_natural_prose`, a `Case` in `CASES` |
| `evals/recordings/natural-prose.{compliant,slop,flat}.md` | new fixtures |
| `backend/tests/test_eval_graders.py` | one test per check failure mode |
| `backend/src/grimoire/store/length_drift.py` | `_ROLL_FENCE` → public |
| `evals/README.md` | hypothesis row, recordings list, replay-limits paragraph |
| `lint-baselines/*.json` | regenerated if the new files move any count |

## Testing

`make check` is the gate, as always. Within it:

- `backend/tests/test_evals.py` replays all six cases; the new one passes on
  its baseline and fails on each counterexample **on exactly the declared
  check set**, which is what makes "it happened to fail on something else" a
  failure too.
- `backend/tests/test_eval_graders.py` pins every one of the twelve `slop.*`
  checks on minimal inputs, one test per failure mode, including
  `slop.repetition` and `slop.unicode` which no recording exercises.
- `test_no_orphan_recordings` covers the three new files by construction.
- The three ratcheted lint gates may need `make baseline` for the new module;
  per CLAUDE.md an improvement fails the gate too, so the smaller file is
  committed with the change.

Promoting `length_drift._ROLL_FENCE` is a rename inside a module with existing
coverage; `make check-py` covers it.

## Risks

- **Thresholds are provisional.** Calibrated to hand-authored fixtures, not to
  model output. Mitigated by `--live --record`, and by every constant naming
  the limitation in place.
- **Replay proves less than it appears to.** Only the prompt contract, the
  drift guard and the graders are held offline. The README paragraph exists to
  stop that being over-read later.
- **False positives on deliberate craft.** Intentional fragments, repetition
  for effect, and incantatory rhythm are legitimate. The style-guide exclusion
  handles the systematic case; `slop.answered_question` is the residual risk
  and carries a written instruction to delete rather than loosen it.
- **Layout disable.** `prompt.natural_prose` proves presence under the default
  layout. A user who disables the section in their own layout is outside what
  any eval can see, which is true of every other `prompt.*` check too.
- **This changes no production behaviour.** The only non-eval edit is a
  private-to-public rename. Nothing about how a scene is generated moves.

## Out of scope

`store/slop_drift.py` and the production corrective; surfacing tells in the
end-of-scene review; Unicode hygiene on transcript writes. All three were
gated behind what this instrument reports.
