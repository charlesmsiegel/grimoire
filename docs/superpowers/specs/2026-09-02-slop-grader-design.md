# Measuring the natural-prose block: an offline slop grader

**Date:** 2026-09-02
**Status:** Draft for review (revision 3, after two adversarial gates)

## Problem

`templates/scene/sections/natural_prose.j2` ships in every scene and opener
system prompt. It bans stock names and phrases, rations beat words, forbids six
construction families, and asks for varied sentence and paragraph rhythm. It is,
in the words of `evals/README.md`, "a **hypothesis about model behaviour**" —
one of five that README's opening paragraph names. Three of the five have no
case: the prose style chain, scene-suggestion grounding, and this one. This
spec covers only this one.

**The block's content can be gutted with nothing failing.** The precise shape of
the hole matters, because two harnesses cover part of it:

- `scripts/verify_templates.py` keeps an **independent mirror** of the section
  order (`rendered_system`, listing `scene/sections/natural_prose.j2` at line
  902) and compares it against `context.build_messages`. Deleting the
  `SECTIONS` entry therefore **is** caught — the mirror still renders the
  section and the builder no longer does.
- What that harness structurally cannot catch is the template's *content* going
  away. Its docstring says so: "It never pins template text to literals, so
  editing a prompt in `templates/` cannot fail it." `_render_sections` drops
  empty sections, so an emptied `natural_prose.j2` renders to nothing on both
  sides and the comparison passes.
- `graders.grade_prompt_section` catches one specific content loss — it has an
  explicit empty-render guard — but only for sections some case names. Seven do:
  budget, reply format, available art, roll protocol, secrecy block, active
  speaker, voice policy (`evals/cases.py:200,206,213,298,476,599,616`).
  `natural_prose` does not.

So the wiring is guarded and the content is not. Stated precisely, because it
is easy to overclaim: `prompt.natural_prose` will catch the section rendering
*empty*, and `slop.list_current` will catch a literal instruction being
*removed*. Neither catches a partial reword — `grade_prompt_section` renders the
current template and tests containment, so an edit moves both sides together and
stays green, which `evals/README.md` describes as deliberate. The hole this
closes is "the block's content silently went away", not "the block still says
what it used to."

**Nothing measures the prose.** No instrument in the repo can say whether a
reply varies its sentence length, rations its beats, or keeps em dashes as
seasoning. That matters most for the failure the natural-prose work already
anticipated in writing: the pink-elephant effect, where naming a banned phrase
makes the model reach for it more often. The recorded remedy is to trim the ban
list in the live template — a change nobody can currently evaluate, because
there is no before and no after.

## Prior art, and what was rejected

The pattern catalogue comes from
[sloptrim](https://github.com/seyedehsanhadi/sloptrim) (Apache-2.0, as is this
repo). Roughly twenty of its documented tells transfer to roleplay prose; the
rest are shaped for documents, and one is inverted here outright — it penalises
the *absence* of sentence fragments, which fiction wants.

Its rewrite half is architecturally hostile (grimoire's corrective idiom is
feed-forward; a post-hoc rewriter would edit story nobody agreed to). Its
benchmark measures authorship classification, which is not the question when
every reply is machine-written by construction. Its plugin machinery watches
agent Edit/Write calls on disk, which is not how this prose arrives.

Usefully, it declares 9 of its 71 patterns human-judgment-only. This spec makes
the same split explicit rather than pretending regexes cover semantics.

## What this cannot do

Replay scores a **fixed** recording, so nothing measured on the output side
reacts to a template edit. Replay cannot answer "does the block work" or "did
trimming the ban list help" — only `evals/run.py --live` puts that question,
and as the README already says of `turn-taking`, one live run is an anecdote.

Offline, replay holds the prompt-contract check, the list drift guard, the
measurability gate, and the graders themselves.

## Scope: what is graded, and what is not

**Hypothesis row for the README table:** *a reply contains none of the stock
names or literal banned phrases the natural-prose block lists, does not repeat
a single beat word past the cap or use the enumerated not-X-but-Y forms, and
does not flatten into uniform sentence and paragraph length.*

That is deliberately narrower than the template, and names only what the checks
actually establish. The template's other instructions are **not graded**:

| ungraded instruction | why |
|---|---|
| invented names must fit the setting and vary in sound and origin | semantic; requires knowing what the setting is |
| don't solve variety by rotating the same few origins | requires judging a distribution of invented names |
| the stock tavern name ("The Gilded-or-Rusty Anything") | a construction family, not a finite list |
| reflexive rule of three | requires knowing whether "the content is genuinely three things" |
| redundant adjective pairs | needs part-of-speech tagging |
| explaining an emotion just shown | semantic |
| metaphors that decorate rather than clarify | semantic |
| replace a repeated beat with something specific to this character and moment | semantic; the cap only catches the repetition, never the quality of the fix |
| let some moments pass without a dramatic beat | semantic |
| the three qualifier-dependent phrases — "a deep breath **as filler**", "said in a low voice **as a reflex tag**", "foreheads pressed together **as the default tender gesture**" | the qualifier is the whole test; each names a legitimate use and bans only the reflexive one. These are `JUDGMENT_ONLY` in `slop.py`: carried as drift sources, never matched |
| ellipses, italics, one-word fragments as structure | detectable in principle; deferred, each needs its own adjacency rule |
| no bullets or headings in narration | the reply-format section carries this instruction and `scene-length` grades that section's **presence in the prompt** — nothing grades the model's compliance on output, here or there |

**Three checks from earlier drafts are cut.** Unicode hygiene and degenerate
six-gram repetition are transcript-hygiene concerns the template says nothing
about; grading them under this hypothesis would make the case's name a lie, and
cutting the Unicode check also disposes of mixed-script confusable detection,
which `unicodedata` cannot do honestly. `slop.answered_question` is cut because
no standard-library method distinguishes a rhetorical question in narration
from an answered line of dialogue.

**`slop.opener_repetition` is also cut**, and for the reason that governs this
whole revision: the template asks for varied *sentence length and paragraph
shape*, and says nothing about repeated opening frames. That rule came from
sloptrim, not from grimoire's prompt, and grading a rule the prompt never
states is exactly the dishonesty the ungraded table above exists to avoid. It
would also have rejected legitimate anaphora.

## Design

### Ownership: the detector is eval-owned

`evals/slop.py` — pure, standard library only, no store reads, no network.

`evals/README.md` requires that "The graders re-use production parsers." There
is no production slop parser, and the precedent for that is `COLLAPSE_RATIO`,
eval-owned because "the app has no opinion about a reply being too SHORT." The
app has no opinion about slop either, by design: `natural_prose.j2` is
prescriptive and feed-forward, and a production `store/slop_drift.py` is out of
scope — the post-history corrective slot already carries the length and voice
correctives, and a third block sits where the pink-elephant effect bites
hardest.

Every threshold is a module-level named constant with its justification beside
it, so if production ever grows an opinion the constants move to `store/` and
the grader borrows them, as `grade_length` borrows `length_drift.TRIM`.

### Normalization pipeline

`store/length_drift.py` has `_prose(content)` —
`export.remove_images(_ROLL_FENCE.sub(" ", content))` — stripping roll fences
**and** expanded image markdown, with a docstring explaining that counting
either "punishes the model for complying." **Promote it to public `prose` and
import it.** Reusing only the fence regex would leave image markdown in the text
and pollute every statistic.

The pipeline, fully specified:

1. `scenes.split_reply(text, players)` → speaker blocks. **Two arguments**: the
   production signature is `split_reply(text: str, players: frozenset[str])`
   (`store/scenes/write.py:195`), and it routes player-named blocks to the
   narrator. `grade_slop` therefore takes `players` and passes it through.
2. `length_drift.prose(body)` on each block body.
3. Rejoin block bodies with a blank line. **Block boundaries are paragraph
   boundaries**, in addition to blank lines inside a block.

**Two normalized texts, not one.** `split_reply` moves a marker like
`**Elara:**` into the segment's `speaker` field and out of its `content`, so a
stock name used as a *speaker label* is invisible to any detector reading bodies
only. The pipeline therefore produces two values and each detector declares
which it takes:

- **`prose`** — the rejoined bodies above. Used by the rhythm checks, the beat
  cap, the phrase check and the construction check, none of which have any
  business reading marker lines.
- **`names`** — the `speaker` values from the same split. Used by
  `slop.stock_names`, which searches `prose` **and** `names`. A reply that
  invents an NPC called Elara and gives her a labelled block must fail.

**Paragraph** = a run of text between one or more blank lines, where a blank
line is one whose `.strip()` is empty; paragraphs with no word tokens are
dropped. **Sentence** = a span produced by splitting on `[.!?…]+` followed by
whitespace and an opening quote or uppercase letter, guarded by an explicit
abbreviation list (`Mr. Mrs. Ms. Dr. Prof. St. Lt. Capt. Sgt. Sr. Jr. vs. etc.
e.g. i.e.`); sentences with no word tokens are dropped. The splitter's known
failure modes — initials, honorifics outside the list, ellipsis used as a
fragment — are named in the module docstring. Approximation is acceptable
because every threshold is calibrated through the same splitter.

### List representation: source and pattern are separate

The gate found a real conflict: `slop.list_current` requires each detector entry
to appear verbatim in the rendered template, but detectors need forms the
template does not contain contiguously — inflections (`murmuring` where the
template has `murmured`), and alternations like "heart pounding or hammering in
a chest or against ribs."

Every list entry is therefore a pair:

- **`source`** — a literal substring that must appear in the rendered template.
  This is the only thing `slop.list_current` checks.
- **`pattern`** — the compiled matcher the detector actually applies, free to
  cover inflections and alternations the source does not spell out.

An entry whose `source` is no longer in the render fails the drift guard even
though its `pattern` still matches text, which is the direction that matters.

### The checks

Ten named checks: one `prompt.*`, nine `slop.*`.

#### Template-side (2)

**`prompt.natural_prose`** — `grade_prompt_section(messages, "natural_prose",
"scene/sections/natural_prose.j2")`, no variables; the section is static. This
is the check that closes the content hole `verify_templates.py` structurally
cannot. It proves presence **under the default layout only** —
`layout.apply(SECTIONS)` lets a user disable or reorder sections, which is
outside what any eval sees, equally so for the seven existing `prompt.*` checks.

**`slop.list_current`** — every entry's `source` must still appear in the
render.

It covers **every graded instruction that has a literal form in the template**,
which is all four detector lists. An earlier draft excluded `NOT_X_BUT_Y` on the
grounds that the constructions have no verbatim form; that was wrong — the
template spells all three examples out ("it wasn't just X — it was Y", "she
didn't X; she Y'd", "no longer X; now Y") alongside the base form, and the
source/pattern split is exactly what lets those literals be drift sources while
the detector regexes stay broader. The rhythm checks carry sources too: the
sentence-and-paragraph-variation sentence for the two variance checks, and the
em-dash clause for `slop.em_dash_spacing`. **Every check whose failure the
hypothesis claims has a drift source**, so an instruction cannot be deleted from
the template while its grader keeps scoring replies against it.

The asymmetry is a stated limitation, not a feature: a phrase *removed* from the
template fails loudly; one *added* is ungraded until mirrored here. The
pink-elephant remedy on record is trimming the ban list, so the guard fires in
the direction that gets exercised. True derivation was rejected because
extracting the lists to data edits a live, known-good prompt template, and
parsing them back out of rendered prose would break on a reword — taxing the
free template editing `evals/README.md` protects.

#### The measurability gate (1)

**`slop.measurable`** — **fails** when the normalized prose yields fewer than
`MIN_SENTENCES` sentences or fewer than `MIN_PARAGRAPHS` paragraphs.

This exists because without it the entire case passes on an empty reply: no
banned phrase occurs in nothing, both variance checks have no sample, and em-dash
spacing needs two paragraphs. A green case on empty output would be the exact
"very slow way of asserting True" `evals/cases.py` warns about.

The precedent is `length.measurable`, and it is a **failure** check — `graders.py:72`
returns `Check("length.measurable", False, ...)` when a reply has no measurable
model blocks. (Earlier drafts of this spec described that precedent backwards
and proposed a *passing* check; that was the bug this gate is named for.) The
README's note that it is "not an independent gate" means it cannot fail in
isolation from its neighbours, not that unmeasurable output passes.

With this gate in place, each variance check **must** return a passing `Check`
when its own sample minimum is unmet — not "may". The distinction is load-bearing
for set equality: a single-paragraph reply has a paragraph-word-count
coefficient of variation of exactly 0, which is below `PARA_VARIANCE_MIN`, so a
check that measured anyway would fail `slop.paragraph_uniformity` too and
`terse.md` could not declare `slop.measurable` alone. The minimum-sample
condition is evaluated **before** the statistic, and short-circuits to a pass
whose detail names the sample size.

#### List-driven (3)

**`slop.phrases`** — fails when any `LITERAL_PHRASES` pattern matches,
casefolded, on word boundaries. That list holds only template entries whose ban
is unconditional. The three qualifier-dependent ones go in `JUDGMENT_ONLY`, are
never matched, and are listed in the ungraded inventory. Both constants' `source`
values are checked by `slop.list_current`, so a judgment-only phrase still
guards against the template losing it.

**`slop.stock_names`** — searches both `prose` and `names`, and fails when a
`STOCK_NAMES` pattern matches, casefolded, on word boundaries, and the matched
token is **not** exempt.

**Exemption is per token, not per name.** `established` is flattened to the set
of individual whitespace-separated tokens of every established name, casefolded.
A stock match is exempt when its matched token is in that set — so a campaign
whose cast includes `Elara Vale` exempts the token `Elara` everywhere,
including where it appears alone. The alternative, requiring the full
established name at the match site, would flag `Elara` in "Elara shrugged" as
invented while accepting it in "Elara Vale shrugged", which is not a distinction
the template draws. This rule is over-permissive by construction, and that is
the right direction: the template's instruction is that established names are
fixed, so a false negative reproduces an existing name and a false positive
would fail a reply for obeying the prompt. `test_eval_graders.py` covers both
the single-token and multiword-name cases.

The template's precedence rule is that "Names that already exist in this scene,
cast, or world are fixed; reproduce them exactly, even if they appear below."
`established` is therefore the full set of names the fixture creates — players,
cast, and every world and campaign record (locations, items, groups, lore,
absent characters) — assembled explicitly in `build_natural_prose` from the ids
and names it wrote, not scraped from record bodies. Word-boundary matching is
required so `Aria` does not match inside a longer name.

That the set is knowable here only because the fixture built it is itself an
argument for eval ownership: production could not assemble it cheaply.

**`slop.beat_words`** — fails when any single beat group matches more than
`BEAT_REPEAT_MAX` times. Groups carry their inflections in `pattern`
(`murmur|murmurs|murmured|murmuring`) and a single template token in `source`.
A cap rather than a ban, because the template rations rather than forbids.

#### Construction (1)

**`slop.not_x_but_y`** — fails on any of four enumerated forms, which are the
template's own three examples plus the base construction:

| form | matches |
|---|---|
| base | `not X, but Y` |
| example 1 | `it wasn't just X — it was Y` |
| example 2 | `she didn't X; she Y'd` |
| example 3 | `no longer X; now Y` |

Each is a bounded regex whose `X` and `Y` spans use a **negated character class
excluding sentence terminators** (`[^.!?…]{0,60}`) rather than a bare wildcard —
a length bound alone does not stop a match running across two short sentences,
which is how a construction detector starts reporting contrasts nobody wrote.
The four patterns are written out in the implementation plan. The template says "in every disguise"; that is not
implementable, and the constant's comment states plainly that the check is a
**floor** on the family rather than a decision procedure for it. The hypothesis
row is worded to claim only the enumerated forms.

#### Rhythm (3)

| check | fails when |
|---|---|
| `slop.sentence_variance` | coefficient of variation of sentence word counts is **below** `VARIANCE_MIN` |
| `slop.paragraph_uniformity` | coefficient of variation of paragraph word counts is below `PARA_VARIANCE_MIN` |
| `slop.em_dash_spacing` | two consecutive paragraphs both contain U+2014 |

Coefficient of variation is `statistics.pstdev(xs) / mean(xs)` over word counts.
Every `_MAX` constant is the largest **permitted** value; a check fails strictly
above it. `slop.em_dash_spacing` passes trivially with fewer than two
paragraphs, which `slop.measurable` has already failed on.

`slop.em_dash_spacing` is derived from the template's own sentence — "if the
last paragraph used one, the next doesn't" — rather than from a density
threshold of our invention. It enforces one clause of the rhythm rule; the
ellipsis, italics and fragment clauses are in the ungraded inventory.

### Thresholds: proposed values, justification, and what they are worth

Proposed starting values, each derived from what the templates and the resolved
budget do rather than from any measurement over stored content:

| constant | value | structural justification |
|---|---|---|
| `MIN_SENTENCES` | 12 | `cinematic` targets 900 words; at even 25 words per sentence that is ~36 sentences, so 12 is a third of the low estimate — high enough for a coefficient of variation to mean anything, low enough not to gate a legitimately compact reply |
| `MIN_PARAGRAPHS` | 4 | `cinematic` permits 7 blocks, and block boundaries are paragraph boundaries; 4 is comfortably under that ceiling |
| `BEAT_REPEAT_MAX` | 3 | the count is global, not per block, so the block ceiling cannot justify it directly. The instruction is that "not every line of dialogue needs a lean, nod, or murmur": with `cinematic` permitting at most 7 blocks and at most 5 speakers, a *single* beat word used a fourth time is reaching for the same reflex more often than there are speakers to attribute it to. That is a structural argument for the order of magnitude, not a measured optimum |
| `VARIANCE_MIN` | 0.35 | prose alternating only between short and long clauses still clears this; prose whose sentences cluster within a few words of one mean does not |
| `PARA_VARIANCE_MIN` | 0.25 | lower than the sentence figure because dialogue blocks are legitimately short and similar; this catches the uniform 3-to-5-sentence paragraph, not ordinary exchange |

**What calibration can and cannot establish.** Tuning a threshold until a
hand-authored baseline passes and a hand-authored counterexample fails is
circular — the fixtures are written around the desired answer. Two things
mitigate it and neither removes it:

1. **A negative corpus** in `test_eval_graders.py`: hand-authored passages of
   legitimate prose every check must leave alone — dialogue-heavy exchange,
   deliberate fragments, an incantatory refrain, terse action. A threshold
   tightened until it trips one of them fails that test.
2. **Provisionality on the record.** Until `--live --record` replaces
   `natural-prose.compliant.md` with genuine model output the numbers are
   provisional, and even then one run is an anecdote.

Stated precisely, because earlier drafts overclaimed here: the negative corpus
**prevents regression on those exact fixtures**. It is not an independent
distribution and yields no statistical false-positive bound. These thresholds
separate one author's written examples of flat prose from one author's written
examples of good prose. They are a regression guard and a starting instrument,
not a validated detector, and nothing here should be read as claiming otherwise.

### The case

A new `natural-prose` case in `evals/cases.py`, reusing `_scene_prompt`.

- **The `cinematic` length preset** (`store/lengths.py`: 900 target words, at
  most 7 blocks, 3 paragraphs per block, 5 speakers, 2 blocks per speaker).
  `scene-length` resolves `terse` — 150 words, 3 blocks — and rhythm statistics
  over three blocks are noise. These are a target and maxima, not a promised
  shape, which is exactly why `slop.measurable` exists rather than the preset's
  numbers being trusted as a sample size.
- **No prose style guide, and no voice anchors on the cast.**
  `natural_prose.j2` says a style guide overrides its rhythm guidance and that
  "A specific character's established voice also wins where it conflicts." A
  fixture carrying either would be graded against rules the template disclaims.
- **Invented placeholder names only**, from the codebase's existing fixture
  vocabulary, none colliding with `STOCK_NAMES`, all passed in `established`.

### Recordings

| file | must trip |
|---|---|
| `natural-prose.compliant.md` | nothing (baseline; the only variant `--record` overwrites) |
| `natural-prose.slop.md` | `slop.phrases`, `slop.stock_names`, `slop.beat_words`, `slop.not_x_but_y` |
| `natural-prose.flat.md` | `slop.sentence_variance`, `slop.paragraph_uniformity`, `slop.em_dash_spacing` |
| `natural-prose.terse.md` | `slop.measurable` |

Four recordings, each isolating a family so none can pass unnoticed behind
another: `flat.md` is rhythmically monotone but literally clean, `slop.md` keeps
varied rhythm, and `terse.md` is a collapsed generation that proves the
vacuous-pass gate actually gates. Set equality is what makes all three bite.
`slop.md` and `flat.md` must both clear `MIN_SENTENCES` and `MIN_PARAGRAPHS`,
or they would trip `slop.measurable` instead of their declared checks.

## Files touched

| file | change |
|---|---|
| `evals/slop.py` | new — source/pattern lists, thresholds, normalization, detectors |
| `evals/graders.py` | new `grade_slop(text, players, established)` |
| `evals/cases.py` | `build_natural_prose`, `grade_natural_prose`, a `Case` in `CASES` |
| `evals/recordings/natural-prose.{compliant,slop,flat,terse}.md` | new fixtures |
| `backend/tests/test_eval_graders.py` | per-entry coverage, negative corpus, prompt-check failure test |
| `backend/src/grimoire/store/length_drift.py` | `_prose` → public `prose` |
| `evals/README.md` | hypothesis row, recordings list, replay-limits paragraph |
| `lint-baselines/*.json` | regenerated if the new module moves any count |

## Testing

`make check` is the gate. Within it:

- `test_evals.py` replays all six cases; the new one passes its baseline and
  fails each counterexample **on exactly the declared check set**.
- `test_eval_graders.py` carries three things no recording can:
  - **Per-entry coverage**, parameterized over `LITERAL_PHRASES`, `STOCK_NAMES`,
    `BEAT_WORDS` and `NOT_X_BUT_Y`, so a dead entry cannot hide behind a named
    check some other entry already makes fail.
  - **The negative corpus** — legitimate prose that must trip nothing.
  - **An explicit failure test for `prompt.natural_prose`**, driving
    `grade_prompt_section` with a prompt lacking the section. No recording can
    fail that check, because every recording for a case is graded against the
    same assembled prompt.
- `test_no_orphan_recordings` covers the four new files by construction.
- The three ratcheted lint gates may need `make baseline`.

Promoting `length_drift._prose` is a rename inside a module with existing
coverage; its in-module callers are `_words` and `_paragraphs`, and `make
check-py` covers the rename.

## Deliberately deferred to the implementation plan

The adversarial gate's standing bar is that "two competent implementers would
build materially the same thing." That is the bar for a **plan**, and this
repo's pipeline puts a separate Codex gate in front of implementation for
exactly that reason. The following are operational details this spec
deliberately does not fix, and the plan must:

- the exact regex text for each `NOT_X_BUT_Y` form, including apostrophe
  variants (U+0027 and U+2019), capitalization handling, and which subject
  pronouns each form admits;
- the inflection set for every beat group and the slash/parenthetical
  alternations inside individual phrase entries;
- the word-token definition (`str.split()` on normalized prose is the intended
  answer) and the quote characters the sentence splitter treats as sentence
  openers, including the `"Go." Mara left.` case where a terminator precedes a
  closing quote;
- the full `LITERAL_PHRASES` / `JUDGMENT_ONLY` partition, entry by entry, with
  each entry's `source`.

What the spec does fix, and what the plan may not silently change: which checks
exist, what each one's failure means, which normalized text each reads, that
every graded instruction carries a drift source, that insufficient samples pass
rather than fail, and that stock names are searched in speaker labels as well as
bodies.

## Risks

- **The thresholds are a regression guard, not a validated detector.** The
  negative corpus prevents regression on its own fixtures and nothing more.
- **Replay proves less than it appears to.** Only the prompt contract, the drift
  guard, the measurability gate and the graders are held offline.
- **The graded set is a strict subset of the template.** The ungraded inventory
  is the honest list; a green case means the graded subset held, not that the
  block was obeyed. `slop.not_x_but_y` in particular is a floor on its family.
- **`slop.list_current` under-covers by design** — additions go ungraded until
  mirrored.
- **Sentence splitting is approximate.** Errors are systematic rather than
  random, since every threshold is calibrated through the same splitter.
- **This changes no production behaviour.** The only non-eval edit is a
  private-to-public rename.

## Out of scope

`store/slop_drift.py` and the production corrective; surfacing tells in the
end-of-scene review; Unicode hygiene and degenerate-repetition detection on
transcript writes; cases for the prose style chain and scene-suggestion
grounding, the other two uncovered hypotheses.
