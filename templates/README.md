# templates/ — every LLM prompt, as Jinja2

Every piece of text grimoire sends to the LLM lives here. Edit a prompt by
editing its template; nothing prompt-shaped is hard-coded anywhere else. The
backend renders these via `grimoire/prompts.py` (jinja2, auto-reload), so a
template edit changes the app's prompts immediately — no server restart, no
code change.

## Conventions

- **One folder per LLM call**, one file per message part: `system.j2` is the
  system message, `user.j2` the user message.
- **Variants live in subfolders** (`scene_suggestions/instruction/`,
  `scene/opener_instruction/`, `scene/sections/story_so_far/`,
  `snippets/plot_thread_line/`, `snippets/commitment_line/`). A selector
  variable picks the file — either
  via a dynamic `{% include %}` in the composing template, or by the caller
  choosing which file to render.
- `snippets/` holds line formats that feed prompt *content* (transcripts,
  relationship lines, plot-thread lines, commitment lines, standing-fact lines)
  and are shared across calls, plus `scene_break_head.j2`, the head that
  `scene_break/user.j2` and `scene_break_title/user.j2` both include.
- Files starting with `_` are macro libraries, not messages.

Rendering contract: `jinja2.Environment(loader=FileSystemLoader("templates"),
undefined=StrictUndefined)` — defaults otherwise (no `trim_blocks`,
`keep_trailing_newline=False`, no autoescape). Every template renders its
exact text with no trailing newline. `{{user}}`/`{{char}}` substitution is a
**data** transformation (case-insensitive literal replace, empty values
skipped — `context._substitute`) applied by code, never by templates.

## The calls

### `tagline/` — POST /worlds/{wid}/characters/{cid}/tagline/generate
Mirrors `store/taglines.py:build_prompt`. Messages: system, user.
`user.j2` vars: `card` (the resolved card's `data` dict).

### `scenario/` — POST /worlds/{wid}/scenario/parse (and /parse-url)
Mirrors `store/scenario.py:build_prompt`. Messages: system, user. One call per
card: it reads a *scenario* card (a whole setting in one card) and proposes the
cast to split out of it, plus a category for each of the card's world-info
entries.
`user.j2` vars:
- `card` — the card's `data` dict
- `fields` — `(label, key)` pairs, `scenario.PROMPT_FIELDS`; a key the card
  does not carry renders no heading at all
- `entries` — the card's own world-info, `scenario.prompt_entries()`
  (`{"name","keys","body"}` rows). They are listed so the model can *re-file*
  one by name; a body it sends for a listed entry is discarded
- `greetings` — its scene openers, `scenario.prompt_greetings()`
  (`{"name","body"}`), with image references already removed by
  `scenario.strip_images` — an embedded `data:` image is megabytes of base64
  that says nothing about the cast

Both lists carry **clipped** bodies (`ENTRY_PROMPT_CHARS` /
`GREETING_PROMPT_CHARS`), since the cards this exists for are large and neither
body is needed whole. The clip is the prompt's alone — `proposal`/`apply` carry
every body entire — and it ends in `…`, which `system.j2` explains so the model
does not read an abridged entry as an incomplete one.
The reply is one JSON object, `{"characters": [...], "entries": [...]}`, parsed
by `scenario.parse_output` through `absorb.extract_object`. Nothing is written
from it: it becomes a *proposal* the user reviews and edits, and only
`POST …/scenario/import` writes.

### `dossier/` — the per-NPC dossier refresh inside POST …/absorb
Mirrors `store/dossiers.py:build_prompt` (one call per present NPC).
`user.j2` vars: `name`, `prior` (existing dossier, may be ""),
`transcript` (render `snippets/transcript.j2` over the scene's messages).

### `voice_anchor/` — POST /worlds/{wid}/characters/{cid}/voice-anchor/generate
Mirrors `store/voice_anchors.py:build_prompt`. Messages: system, user.
`user.j2` vars: `card` (the resolved card's `data` dict — reads `name`,
`personality`, `mes_example`, `system_prompt`, and `description` as raw
material to mine for speech evidence, already clipped to
`voice_anchors.VOICE_SOURCE_CAP` by the builder). `scenario` is deliberately
not read: it describes the situation every character in it shares, so it can
only push anchors toward each other. Preview only; the caller persists via PUT.

### `voice_drift/` — the per-NPC voice check inside POST …/absorb
One `decide()` item (spec 7.4) per present NPC **that has a voice anchor** — an
anchorless character is never judged, which is what keeps the extra calls
opt-in — on the `voice_drift` route, which runs on the Decision role. No
messages of its own: `store/voice_drift.py:build_item` makes the item and
`decide/` renders the prompt around it.
`user.j2` is the item's context. Its vars: `name`, `anchor` (never ""),
`transcript` (render `snippets/transcript.j2` over the scene's messages), and
`correction` (optional, `""` when there is none). The correction is the
character's outstanding drift note, and the CALLER owns deciding it is still in
force -- `store/voice_drift.py:judge_item`, which `_stage_voice_drift` builds
through, passes it only while `voice_drift.live_correction` finds its
fingerprint naming the current anchor, because a note judged against a
REPLACED anchor is suppressed for the writer and must not be shown to the judge
as current. `question.j2` treats it as superseding the anchor wherever the two
conflict, which is what the scene prompt tells the writer, so a judge that
could not see it would flag the model for obeying its instructions.
`judge_item` sends the effective anchor (`voice_anchors.effective(...)`), and
`voice_drift.locked_name` names the character as the transcript labels its
lines (the locked card's raw name).

The item's one choice (id `verdict`) takes `question.j2` as its instructions —
the standard and criteria of the legacy one-call prompt, moved there whole —
and is **not** a boolean, because clearing a standing flag is a write: only an
explicit `in_voice` justifies one. Its three options, each described by
`option.j2` (var `verdict`, the option's id) with that verdict's legacy bullet,
word for word after its label:
- `drift` — spoke, sounded wrong; the rationale carries the corrective.
- `in_voice` — spoke enough to judge, sounded right → stages a clear.
- `not_enough` — silent or too few lines to tell. A real answer, not a
  fallback: silence is not evidence of sounding right, so a standing flag
  survives it. It keeps the legacy synonyms `insufficient` and `unclear` as
  aliases (`voice_drift.ALIASES`).

There is no none-of-these: `voice_drift.finding_of` maps an answer of `None`,
for any reason, to `voice_drift.UNKNOWN`, which the absorb route reports as a
failed check. Collapsing it into `in_voice` would let a garbled reply retire a
real corrective on a default-approved review. `store/voice_drift.py:explain`
renders `explain.j2`, the rationale instruction: the corrective
`scene/voice_correction.j2` renders on the next turn, empty unless the verdict
is `drift`. Neither `question.j2` nor `explain.j2` takes vars, and none of the
three restates the reply format: that is `decide/system.j2`'s.
`voice_drift.check_failure` is the route's three per-finding refusals —
unreadable, a drift with no corrective, a corrective over `MAX_NOTE` — with its
words. `scripts/verify_templates.py` holds that each criterion of the legacy
prompt is in a decide-era template verbatim (`VOICE_DRIFT_CARRIED`), and that
`option.j2` renders each verdict's own description and no other
(`VOICE_DRIFT_OPTIONS`, keyed by verdict).

### `scene_suggestions/` — POST /campaigns/{cid}/scene-suggestions
Mirrors `store/suggest.py:build_prompt`. Messages: system, user.
Vars (both files take the same set):
- `s` — `suggest.build_snapshot()` dict (the `drivers=True` shape: no
  `upcoming`; adds `commitments`, `timeline`, `driver_index`, `anchors`,
  `links`, `fixed`, `near_days` and `sooner_ref`, and a `ref` on each thread)
- `offscreen` — bool selector: `instruction/{standard|offscreen}.j2`
- `greeting_candidates` — `suggest.greeting_candidates()` list or None
- `direction` — the game master's steering text, "" when absent
- `drivers` — bool; `build_prompt` passes True. Under it, `user.j2` prefixes
  each thread line with its `thread:<id> =` ref, drops the Upcoming line in
  favour of the timeline, and adds the "Open commitments", "Timeline", "Story
  drivers" and "Reviewed links between drivers" blocks (each only when
  non-empty). False is `scene_intent`'s legacy render, below.
- `view` — `suggest.driver_view(s, controls)`: the rendered index (capped at
  `DRIVER_PROMPT_CAP`, pinned refs always in, the rest "and N more"), the
  capped timeline (same rule, plus the `sooner_ref` pick; rows keep the
  snapshot's calendar order), the links among rendered drivers, the action
  vocabulary, the high-pressure states and the controls with their labels.
  Read only under `drivers`.

`system.j2` appends `instruction/date_addendum.j2` when `s.now` is set,
`instruction/rank_addendum.j2` when there are greeting candidates and
`instruction/direction_addendum.j2` when there is a direction — together, the
**instruction section**, which no driver or control ever changes (capstone
spec §15.1, pinned by `tests/test_suggest_golden.py`). After it, only under
`drivers`: `instruction/drivers_addendum.j2` when `view.index` is non-empty
(the `"drivers"`/`"time_anchor"` reply keys and the diversity guidance), then
`instruction/controls_addendum.j2` when `view.active` (focus, avoid, must and
the time mode). So a campaign with no drivers and no controls gets the system
message it always had. Every addendum begins with a **leading space** — they
continue the instruction sentence-style; keep it when editing. The two driver
addenda render every enum (action words, relations, high-pressure states) from
`view`, never as literals, so the vocabulary has one source in Python.

Reply shape: ONLY `{"suggestions": [{"title", "premise", "cast": ["<kind>:<id>"],
"location": "<id>" | "", "date"?, "drivers"?: [{"ref", "action"}],
"time_anchor"?: {"ref", "relation"}}], "next_date"?, "greeting_picks"?:
["<greeting id>"]}`, where each optional key is asked for only by the addendum
that adds it. The suggestion list is read by `suggest.parse_output` through
`suggest.raw_suggestions` (a bare top-level array is accepted as the list);
`parse_greeting_picks` and `parse_next_date` read their keys from the same
decoded object. An entry with no title or premise is dropped
(`suggest.is_card`); cast and location ids are checked against the campaign
(`suggest.valid_ids`), greeting picks against the greetings the prompt offered,
and drivers, anchors and dates against the snapshot the prompt was rendered
from. An undecodable reply is no
suggestions, no greeting picks and no next date, not a failed run. The replies
are captured at Debug level like every other LLM response, under the existing
Settings disclosure.

### `scene_intent/` — POST /campaigns/{cid}/scene-intent
Mirrors `store/suggest.py:build_intent_prompt`. Messages: system, user.
`user.j2` includes `scene_suggestions/user.j2` verbatim, so it takes that
file's vars plus `typed`. It renders with `drivers=False` and `view=None`, over
`build_snapshot(drivers=False)` — the legacy snapshot, `upcoming` included —
so the intent prompt is byte-identical whatever drivers exist (spec §15).

`instruction/date_notation.j2` is a **shared** partial, and the one file here
included from outside its own family: `date_addendum.j2` and
`scene_intent/system.j2` both end with it. It spells out how the campaign's
calendar writes a date (`s.notation.example`, plus `s.notation.months` when the
set is small enough to list), which every prompt asking for a `date` needs and
none can get from `s.friendly` — that is the human form, not one
`suggest.date_normalizer` reads back. Edit it for both callers or neither: two
prompts teaching different notations for one campaign is the bug it exists to
prevent. It renders nothing when the calendar could not be resolved.

### `absorb/` — POST /campaigns/{cid}/scenes/{sid}/absorb
Mirrors `store/absorb.py:build_prompt`. Messages: system, user.
`user.j2` vars: `facts` (`chronicle.scene_facts()`), `state_snapshot`
(`absorb.state_snapshot()` — values are `snippets/state_snapshot_line.j2`
lines), `rel_snapshot` (`absorb.relationships_snapshot()` — lines per
`snippets/feeling_line.j2` / `snippets/bond_line.j2`), `plot_snapshot`
(`absorb.plot_snapshot()` — lines per `snippets/plot_thread_line/absorb.j2`),
`commitment_snapshot` (`absorb.commitment_snapshot()` — lines per
`snippets/commitment_line/absorb.j2`), `fact_snapshot`
(`absorb.fact_snapshot()` — lines per `snippets/fact_line.j2`),
`steering_snapshot` (`absorb.steering_snapshot()` — the scene's reroll
steering log as `- <text>` lines; `system.j2`'s "Player steering notes"
paragraph makes them signals to sharpen or extend lore, never citable
evidence), `transcript` (`snippets/transcript.j2`).
`system.j2` also tells the model to move, close or open plot threads and
commitments against the listed ones, and lets a row that opens a new record carry
the identity fields `absorb.parse.IDENTITY_FIELDS` (`why_new`,
`distinguished_from`), which `parse_output` keeps only when well typed.

### `audit/` — the post-absorb mechanics audit inside POST …/absorb
Mirrors `store/audit.py:build_prompt`. Messages: system, user.
`user.j2` vars: `sheet_blocks` (`audit.sheet_blocks()[0]` — one rendered block
per present, sheeted cast member and the scene's current location; each
mutable line marks "start X -> now Y" against `audit.baseline_field()`, each
static line is marked `[static]`), `roll_lines` (`audit.roll_lines()` — the
scene's roll-log entries), `transcript` (`snippets/transcript.j2`).

### `continuity_identity/` — the duplicate check inside POST …/absorb
Mirrors `store/continuity/identity.py:build_items`. One `decide()` item per
`Examination.prompt_rows()` row, in order, with `decide/` rendering the prompt
around them, one `decide()` over every examined row and chunked when the batch
is long (the per-item wording repeats once per row, which is why each call is
bounded): one proposed-new plot thread or commitment that has a plausible
same-type neighbour, each row `{key, kind ("thread" | "commitment"), title,
beat, status, commitment_kind, due, quote, speaker, certainty, why_new,
distinguished_from, candidates}`; each candidate is `{id (bare canonical id),
title, status, kind, due, latest_beat, earlier (up to two earlier beats, newest
first), signals, line, signal_text}`, where `line` is
`snippets/plot_thread_line/absorb.j2` or `snippets/commitment_line/absorb.j2`
(a blank commitment kind passed as `promise`) rendered by Python, and
`signal_text` the similarity signals as fixed-order phrases
(`identity.template_rows` adds the last two). Every key is always passed; a
blank optional one renders nothing.
- `item.j2` is the item's context. Var: `r`, one `identity.template_rows` row
  holding only the candidates the item offers. A heading (`Proposed plot
  thread: <title>` or `Proposed commitment: <title>`), then the row's block,
  from `Beat:` to the candidates' signals.
- `question.j2` is the item's one question's instructions (a choice under
  the id `decision`, no null): the criteria of the retired one-call prompt,
  two sentences reworded for one row shown above, and its id instructions
  reworded for the fold below. No vars.
- `option.j2` describes each option. Vars: `decision` (`existing`, `new` or
  `uncertain`) and, for `existing`, `title`. `new` and `uncertain` are each
  that decision's bullet in the retired prompt, word for word after its
  quoted word. `existing` is folded with the candidate it names: one option
  `existing:<id>` (`identity.EXISTING_PREFIX`) per offered candidate, in rank
  order and before `new` and `uncertain`, described under that candidate's
  clipped title (or its id when the title is blank): "the listed candidate
  "<title>" itself, only when …; a narrower question, a continuation or a
  related subplot of it is "new"", the title quoted with no colon after it, so
  the rendered line (`- existing:<id>: …`) chains no further colon after the
  id's own. There is no `id`
  question: a native decisions endpoint answers each question on its own,
  so an id asked "unless the decision is existing" was answered without the
  decision (spec 7.4). An option's aliases, each folded the same way, are
  the spellings the acceptance guard canonicalises: the ref form
  `<kind>:<id>`, and each live alias source of it with its bare id, and
  every one of them, the id included, again with a space after the colon
  (`existing: <id>`), which `decisions.normalise` would otherwise read as no
  option; the spaced id is taken before any other alias. A
  candidate whose id is not offerable, whose folded option collides once
  normalised with a higher-ranked one's, or past the room one choice has
  (`decisions.MAX_OPTIONS`, and strict mode's enum string budget past 250
  options) is dropped from the options and the context, ids before aliases
  (`identity._offered`), so a hand-edited ledger never builds a request
  `decide` refuses; a row left with no offerable candidate is offered no
  `existing` at all.
- `explain.j2` is the rationale instruction (`identity.explain`): the row's
  display-only reason. No vars.

`identity.answers_of` maps the parsed batch to the decision dicts
`Examination.decide` reads, leaving out an item the reply never reached
(`decisions.was_read`), so its row stays `unchecked` (one the reply reached and
answered badly was read, and is `uncertain`); `identity.take` decides
or, for no readable answer at all, makes every row a hint only. An undecodable
reply means the phase is `failed` (every examined row keeps its hints); a
decodable one with nothing usable is no decisions. The replies, including each
row's reason, are captured at Debug level like every other LLM response, under
the existing Settings disclosure, while the info-level log row carries counts
and modes only. `scripts/verify_templates.py` holds each criterion of the
retired prompt to a decide-era template (`IDENTITY_CARRIED`, `IDENTITY_OPTIONS`
keyed by decision, `IDENTITY_REWORDED` with why); the reply format is
`decide/system.j2`'s.

### `continuity_reconcile/` — the reconciliation sweep after End Scene or a refresh
Mirrors `store/continuity/reconcile.py:build_items`. One `decide()` per sweep,
chunked, over at most `RECONCILE_MAX_CANDIDATES` findings chosen by
`reconcile.select`, as one item per payload candidate, in order, with `decide/`
rendering the prompt around them. The question and evidence wording repeats
once per item, so the overhead grows linearly with the findings, and the
chunking bounds each call. `reconcile.build_payload()` supplies `now` (the
campaign date, or blank), `chronicle` (`[{id, one_line}]` — the chronicle lines
of every beat scene shown and of the last `RECONCILE_RECENT_SCENES` scenes),
`recent` (that recent window itself, in play order) and `candidates`, each
`{key ("c1", …), vocabulary, records, signal_text}`. Each record is `{letter
("A" | "B"), ref, type, line, beats ([{scene, text}], its last
`RECONCILE_BEATS`), pressure, links, actors}`; `type` (`plot thread` /
`commitment`, blank for an event) is printed beside the letter, because a cross
pair stores the commitment as A; `line` is `snippets/plot_thread_line/absorb.j2`
or `snippets/commitment_line/absorb.j2` with no latest beat (the beats follow
it), or `event: <name> (<date>)`, rendered by Python. Only scenes that exist
are shown: a beat in a deleted scene loses its scene marker and a deleted
scene's chronicle line is dropped, so the evidence the sweep accepts is
evidence the apply can check. No transcript is ever sent. Every key is always
passed; a blank optional one renders nothing.
`reconcile.item_scenes` is the scenes one item shows: its records' beat scenes
and the chronicle lines of those and of the recent window, in `known_scenes`
order, an id `decisions.offerable` refuses or one colliding once normalised
with an earlier one dropped from the item's options and lines.
- `item.j2` is the item's context. Vars: `now`, `chronicle` (only the lines
  of the scenes the item shows) and `c` (`{label, records, signal_text}`):
  the campaign date and those lines, a heading `Candidate — <label>`, then the
  candidate's record and signal block.
- `question.j2` is the first question's instructions (a choice under the id
  `decision`, no null, each option labelled by its own word with `_` as a
  space). Var: `vocabulary`. The retired prompt's criteria, one sentence
  reworded for one candidate shown above, and the vocabulary's whole bullet
  (two commitments carry the plot threads' bullet too, which theirs calls
  "the same rules"); under `reconcile.PAIR_VOCABULARIES` it includes
  `direction.j2`.
- `direction.j2` says that a pair's directed words are offered folded with
  their direction. No vars. There is no `from` or `to` question: a native
  decisions endpoint answers each question on its own, so a direction asked
  "null when the decision has no direction" was answered without the
  decision (spec 7.4). Instead each of `duplicate`, `continuation`,
  `subthread` and `pays_off` is offered once per way the link rules let it
  run between the item's records (`reconcile._runs`), as
  `reconcile.folded`'s `<word>_<from>_<join>_<to>` -- `duplicate_a_into_b`,
  `continuation_b_of_a`, `subthread_a_of_b`, `pays_off_b_to_a` -- and
  `reconcile.unfolded` splits the answer back into today's decision, `from`
  and `to`. `pays_off`, which runs from the plot thread to the commitment
  only, is offered once, with its bare word as an alias.
- `directed_option.j2` describes one folded option. Vars: `word`, `frm` and
  `to` (the letters): the retired prompt's direction rules, one clause per
  word, naming the records by letter.
- `evidence.j2` and `evidence_more.j2` are the evidence questions: one
  nullable choice per scene the item shows, up to `reconcile.EVIDENCE_SCENES`
  (`evidence_scene`, then `evidence_scene_2` and `evidence_scene_3`), each
  over every scene it shows, described by `scene_option.j2` (var: `sid`; "the
  scene listed above as <id>"). No vars otherwise. Each stands alone: it
  asks which shown scene, if any, shows a record settled (a question
  answered, a commitment fulfilled, broken or expired), null when none does,
  and never "for" a decision word, since a native decisions endpoint answers
  each question without the others (spec 7.4); `evidence_more.j2` is the same
  question with one added pointer. That a status word needs a cited scene is
  `_decide`'s rule over the answers, below, not a question's.
- `explain.j2` is the rationale instruction (`reconcile.explain`): the
  proposal's display-only reason. No vars.

`reconcile.proposals_of` maps the parsed batch to cache proposals through
`_decide`, leaving out a candidate the reply never reached
(`decisions.was_read`), so it gets no proposal and is asked again (a
model-only nomination is not stored, and is nominated afresh); one the reply
reached and answered badly was read, and is `uncertain`. A word
outside the candidate's vocabulary, a direction the link rules refuse, or a
closure or resolution without an evidence scene its item showed is
`uncertain`; a status word stands on a shown scene without a rationale, its
`reason` then `""`. An undecodable reply fails the run, and the deterministic
findings already cached are kept; a decodable one with nothing usable proposes
nothing. The sweep's replies, including each reason, are captured at Debug
level like every other LLM response, under the existing Settings disclosure,
while its info-level log row carries counts and modes only.
`scripts/verify_templates.py` holds each criterion of the retired prompt to a
decide-era template (`RECONCILE_CARRIED`, `RECONCILE_BULLETS` keyed by
vocabulary, `RECONCILE_REWORDED` with why) and prints the words it adds
(`RECONCILE_ADDED`); the reply format is `decide/system.j2`'s.

### `rolling_summary/` — POST /campaigns/{cid}/scenes/{sid}/rolling-summary
The live running summary of a scene **still being played** (#85). Mirrors
`store/rolling_summary.py:build_prompt`. Messages: system, user.
`user.j2` vars: `facts` (`chronicle.scene_facts()`), `prior` (the stored summary
this refresh folds forward, `""` when there is none to fold), `transcript`
(`snippets/transcript.j2`).

`facts` renders the same Location/Date/Present head `absorb/user.j2` builds, and
carries more weight here: a scene's first location is set **silently**
(`scenes/moment.py`) and cast seated before the first message is seated
**silently** (`appearances/transitions.py`), so on an ordinary scene neither
fact is anywhere in the transcript — and a fold cannot recover a fact it was
never given, because the text it would have come from is already behind it.

The two are coupled: `transcript` is the posts appended **since** `prior` when
there is a prior, and the **whole scene** when there is not — the template
labels the two cases differently so the model is never asked to fold new posts
onto nothing. A prior is dropped, and the scene refolded whole, whenever the
prefix it covered stopped matching `rolling_summary.covered_digest` (a reroll,
an edit, a trim).

Present tense, where `absorb/system.j2` is past: this describes a scene in
progress rather than one that ended. The reply is one line of prose —
`parse_output` collapses whitespace, because scene frontmatter is one line per
key and a multi-line value corrupts the file. Display-only: this summary is
deliberately absent from `scene/sections/`.

### `scene_break/` — POST /campaigns/{cid}/scenes/{sid}/scene-break
The confirmation half of heuristic scene-break detection (#84), asked as a
`decide()` item (spec 7.4) on the `scene_break` route, which runs on the
Decision role. No messages of its own: `store/scene_break.py:build_item` makes
the item and `decide/` renders the prompt around it. `user.j2` is the item's
context. Its vars: `title` (the scene's own), `facts`
(`chronicle.scene_facts()`; these two render as the head,
`snippets/scene_break_head.j2`), `signals` (`scene_break.evaluate`'s
`[{kind, weight, detail}]` — only `detail` is rendered), `transcript`
(`snippets/transcript.j2`).

`facts` carries the same weight here as in `rolling_summary/` and for the same
reason: the first location and the first date are set silently, so on the
scenes that never move the transcript states neither.

`transcript` is the posts since the scene was last **considered**, not the whole
scene — the question is whether the story has arrived somewhere since it was
last asked, and re-sending three hundred posts to ask it would make the free
half of the feature pointless. `signals` is empty on a forced question that
crossed no threshold, and the template renders no reason list at all there
rather than an empty one.

The item's one predicate (id `over`) takes `question.j2` as its instructions:
the criteria of the legacy one-call prompt, moved there word for word, which
state outright that the signals are the reason for the question and never
evidence for a yes — they are counts, and a count cannot see whether anything
was settled. `store/scene_break.py:explain` renders `explain.j2`, the rationale
instruction, whose answer is the stored reason. Neither template takes vars,
and neither restates the reply format: that is `decide/system.j2`'s.
`scene_break.verdict_of` maps the item's answer to the stored verdict, where an
unreadable answer is no break, because this runs automatically off the play
loop. Nothing here ends or splits a scene — the answer is a suggestion in the
inspector. `scripts/verify_templates.py` holds that each criterion of the
legacy prompt is still in a decide-era template verbatim (`SCENE_BREAK_CARRIED`).

### `scene_break_title/` — the title a proposed scene break suggests
Mirrors `store/scene_break.py:build_title_prompt`. Messages: system, user.
Sent only once a YES verdict has landed and been stored, because a closed
question cannot carry prose, and metered as `scene-break-title` on the
`summary` route — so the title is drafted by that route's model, not the
Decision role's. A YES therefore costs two calls and two copies of the
transcript. A title that fails leaves the stored verdict with an empty title,
and one whose verdict moved on while it was drafted is dropped. Since the
verdict is written first, a read between the two writes sees a YES with no
title, and the inspector may show the proposal untitled until its next refresh.
`system.j2` takes no vars: the title criteria the legacy one-call prompt asked
for beside the verdict, verbatim, and "Reply with the title alone."
`user.j2` vars: `title`, `facts` and `transcript`, rendered as the head and
transcript of `scene_break/user.j2` (both include `snippets/scene_break_head.j2`,
so the two heads cannot drift), and `reason` (the stored verdict's, rendered
as "Why it ended: …"; `""` renders nothing). The reply is cleaned by
`scene_break.parse_title`: its first non-empty line only (a model that explains
its title on the next line has not made that part of it), whitespace
collapsed, a quotation mark that opens and closes the whole title stripped as
a pair (an apostrophe alone, as in "'Tis the Season", is the title's own) and
trailing punctuation stripped, then cut at `scene_break.TITLE_MAX`, since a
title is one frontmatter line shown in a chip.

### `tracker/` — the scene state tracker's update call, after every post
Mirrors `store/tracker/prompt.py:build_messages`. Messages: system, user. One
small call per post: it is shown the tracked fields, each present character's
current values and the new post, and replies with only what the post changes
(`{"changes", "awareness"}`, parsed by `tracker.merge.parse_reply`).
`update_system.j2` takes no vars; the cassette matches its first sentence.
`update_user.j2` vars:
- `fields` -- the **active** fields only (`fields.active`; a switched-off field
  is never shown), as `{key, type, aware, options, hint}`. `options` is the
  enum's list, empty otherwise; `aware == "self"` renders `[private by default]`
- `characters` -- one block per **present** character, `{name, new, entries}`;
  `entries` is `{key, text, user_set, private, known_to}` per non-empty value.
  `user_set` renders `(user-set)`; `private` (a list-aware value) renders
  `(private)` or `(private, known to: <names>)`; `new` marks a newcomer
- `newcomers` -- `prompt.newcomer(cid, ref)` rows, `{ref, name, description,
  state}`: the card (or persona) description and the standing state of a
  character the tracker has not recorded yet. A read that fails is `""`
- `context_posts` and `post` -- `{speaker, content}`; the new post is last in
  the message, so the reply is anchored on it

### `decide/` — every structured decision (`inference.decide`, slice F)
Mirrors `grimoire/inference.py:structured_messages`. Messages: system, user.
One call per chunk of items (at most `decisions.MAX_ITEMS_PER_CALL`, and at most
`decisions.MAX_ENUM_VALUES` enum values across its schema); each item is a
context and its ordered questions, and the reply is the JSON object
`decisions.schema` describes, read back by `decisions.parse`.
`system.j2` vars:
- `schema` -- the batch's JSON Schema, rendered with `tojson`. Always in the
  prompt, whether or not the attempt is also sent its provider's structured
  mode, so a fallback without the mode answers from the same prompt
- `explain` -- bool: whether each entry carries a `rationale`
`user.j2` vars:
- `items` -- `decisions.Item`s, numbered from 0; each question renders its id,
  its type (yes/no, choice, scale) and its instructions, a choice its options
  (`id: description`, and whether null is allowed), a score its levels
  (`index: description`). Option aliases are the parser's and never shown
- `explain` -- the rationale instruction, rendered last; "" renders nothing

### `scene/` — the context builder (`store/context/`)
Serves POST …/chat, …/retry, …/regenerate (via `build_messages` /
`build_director_messages`) and …/opener (via `build_opener_messages`).

Model-specific scene guidance is optional. Add or edit a direct-child
`scene/model_guidance/<model-id>.j2` template to enable an exact model ID.
IDs must begin with a letter or digit and contain only letters, digits,
periods, underscores or hyphens. The shipped `glm-5.3.j2` also serves the
explicit `z-ai/glm-5.3` alias; additional provider-prefixed aliases belong in
`grimoire/model_guidance.py`. IDs are matched exactly and case-sensitively;
unknown IDs and empty or missing templates add no instructions. There is no
automatic family or provider-prefix matching.

Templates use the configured template root (`GRIMOIRE_TEMPLATES` when set),
with the scene data below plus `opener`. Keep profiles short and subordinate
to shared continuity, knowledge, player-control and formatting rules. The
`sections/model_guidance.j2` wrapper receives `model_guidance`, the selected
profile text. It appears after natural-prose guidance by default as a lock-in
section, and can be reordered or disabled in the existing context layout.

The actual requested model selects the profile on every scene attempt,
including reroll overrides and provider fallbacks. Each generation freezes
its context, macros, templates and budget, then prepares packed variants
with each profile. A fallback selects its own prepared variant. The live inspector resolves the standing
scene route; captured attempts retain their own model and profile row.
Same-model retries reuse the composition. Utility/JSON prompts receive no
scene profile. No sampling or reasoning API settings are changed.
Variant preparation performs one packing pass per distinct installed profile
plus the empty profile before dispatch. This keeps editable Jinja includes
stable across attempts. Primary capture follows the turn claim; fallback
variants are captured only when dispatched. With capture off and an unbounded
context budget, composition still skips token counting.

See [GLM dialogue notes](../docs/glm-dialogue-prompts.md) for the evidence and
the live evaluation still needed.

Message assembly (code-side, mirrored from `context/assemble.py`):
1. `system.j2` — one system message; omitted if it renders empty.
2. The projected history: each stored message through
   `scene/history_line.j2`, consecutive same-role lines merged with a blank
   line between them (`context/story.py:_project_history`).
   2a. Author's notes (`context/authors_note.py`): `scene/authors_note.j2`
   (vars `level`, `name`, `text`) rendered once per note that applies to this
   turn -- campaign, then scene, then the character note in that character's
   own call only -- each as its own system message, never merged and never
   labelled. It is inserted into the projected history before the `depth`-th
   most recent in-context post (0 = after the last), snapped back to the start
   of a player post. A scene with no player posts (an offscreen scene) has no
   such start, so there a note with depth > 0 lands at the very start of the
   history, the first thing trimmed. Cadence is `every`, counted over the
   scene's player posts plus director notes. Openers have no history, so theirs (every-turn notes
   only) follow the opener prompt.
3. Director turn only: the note as a user message — the player's text, or
   `scene/director_note.j2` when blank. Opener only: the (substituted)
   opener prompt as the user message (openers include no history).
4. `scene/post_history.j2` as a system message, if non-empty. Vars:
   `npc_cards`, `voice_correction` and `length_correction`. The last is
   rendered from `scene/length_correction.j2` (vars: `drift`, `budget`) when
   `length_drift.measure()` finds the last 3 turns over budget, else `""`;
   `voice_correction` is rendered from `scene/voice_correction.j2` (var:
   `voice_notes`, a list of `{name, note}`) for the present NPCs carrying an
   unresolved `store/voice_drift.py` flag, else `""`. Voice rides ahead of
   length: length is about trimming what was written, voice is about who is
   writing it. This is the closest slot to generation, which is why both
   counterweights ride here rather than in the system prompt.
5. Regenerate guidance or mechanics continuation blocks, when present,
   as extra system messages after the post-history.
6. Opener only: an actor-specific final system message after the player's
   premise and completed contributions. The application writes speaker labels.

`system.j2` takes one var, `sections` — the already-rendered section texts, in
order, which it joins with blank lines. It used to `include` every section
itself; that made it a second render path over the same data, disagreeing with
the inspector's breakdown as soon as anything could be dropped. The order and
the selectors now live in `context.assemble.SECTIONS`, and
`_render_sections` renders it: `opener` (prepend
`opener_instruction/{standard|offscreen}.j2`), `opener_adapt` (the
`adapt_{standard|offscreen}.j2` pair instead, when the opener's prompt is a
greeting being rewritten for the campaign's current state, #91), `pcless`
(offscreen sections + opener variant), `story_full` (`sections/story_so_far/{full|compact}.j2`;
the opener uses `full` with the last 5 scenes, chat uses `compact` with the
configured `recap_depth`).

Each `SECTIONS` entry also carries a packer tier — `lock-in`, `spotlight`,
`background` or `archive`. Over the configured `context_budget`, whole sections
are dropped lowest tier first and the history is trimmed; lock-in never is. See
`context/pack.py`.

The section data vars, in section order — all already `{{user}}`/`{{char}}`
substituted by code:
- `global_system_prompt` — config `system_prompt`
- `prose_style_name`, `prose_style_body` — the resolved style guide; both
  `""` when none resolves. Resolved by `response_presets.resolve()`, whose
  per-field cascade (turn → scene → campaign → global) subsumes the older
  `styles.resolve_style()` chain and walks the same `style_id` /
  `default_style_id` keys when no response preset is set
- `budget` — `{words, paragraphs}` from `response_targets.resolve()`;
  Opening applies to the opener narrator, Continuation to every NPC and later
  response. It feeds `sections/response_budget.j2` as a prose ceiling with no minimum.
  This is prompt guidance, not an API token limit or enforced truncation.
- (no vars) `sections/natural_prose.j2` ? the short roleplay brief and
  continuity/knowledge boundaries; sits right after the prose style. Authored
  style and character expression may override expression defaults, never
  continuity, player control, knowledge, or reply format. The earlier phrase
  and rhythm policy is now optional `styles/natural-prose-legacy.md`.
  Experiment scope: docs/scene-dialogue-guidance.md.
- `world_overview` — the campaign world's profile (#38), `worlds.read.profile_of`
  over `campaigns.read.world_root_of`: `{genre, tone, themes: [str],
  description}`, every value empty when the world has none, in which case
  `sections/world_overview.j2` renders nothing
- `npc_cards` — locked card `data` dicts of in-scene NPCs (also feeds the
  card-level system prompts, descriptions, message examples, post-history)
- `states` — `[{name, current_state, knows, suspects}]` from
  `playstate.read_state` for in-scene NPCs with any state. In a scene with a
  player, `suspects` is POV-filtered (#116): a suspicion naming another
  present actor is withheld, since it is a private and possibly false belief
  the model cannot tell from a fact. A `pcless` scene is the director's own
  view and gets the stored value unfiltered — `context/world_state.py:
  _visible_suspects`
- `tracker_lines` — `[{name, own, values: [{label, text, private}]}]` from
  `tracker.view.lines_for`, for `sections/tracker_state.j2` (Scene state): the
  scene tracker's latest `ok` record at the transcript's tail, over the whole
  scene cast less any excluded actor, filtered for this prompt's reader. An
  assigned NPC gets its own line first (`own`, every value) and the others'
  `present`-aware values plus the private ones whose awareness list names it;
  the narrator — `grimoire`, or no assigned actor — gets every value, none
  `own`. A character with an empty `values` renders no line. `[]` — and so no
  section — when the tracker is off for the campaign, nothing has been
  recorded, or the tracker could not be read (fail-soft). Never in the opener.
  A reroll replays its frozen prompt, so it keeps the state it was first
  composed with. Read `c["values"]`, not `c.values`: on a dict that names the
  method. — `context/assemble.py: _tracker_read / _tracker_lines`
- `tracker_narrator` — `True` when the reader is the narrator: no
  "(what you can perceive)" on other characters' names, and private values
  labelled "(private: never state or imply in narration)" instead of
  "(known to you)"
- `speaker` — `None`, or `{lead, quiet, reason, spoken, silent_for}`: who
  carries this turn in a group scene (#29), for `sections/active_speaker.j2`.
  `reason` is `"named"` (the turn's input named exactly one present NPC),
  `"rotation"` (longest silence, then fewest blocks, then cast order), or
  `"opening"` (no model block yet, so the pick is arbitrary and the section
  states no reason rather than claiming a silence every candidate shares).
  `spoken`/`silent_for` are why. `None` — and so no section — when
  `speaker_turn_taking` is off, which is the shipped default, and whenever
  fewer than two NPCs are present, since naming a lead in a two-hander only
  repeats the cast list. Derived from the transcript on every pass and never
  stored, so a regenerate reproduces it rather than advancing a rotation —
  `store/context/speaker.py`
- `relationship_lines` — `relationships.render_present()` lines
- `players` — seated players: `{kind: "pcs", name, pronouns, summary,
  description, birthdate, goals, player_notes}` (persona; `pc_block` adds a
  `Goals:` and a `Narrator guidance:` line for the two profile fields, #65,
  only when set) or `{kind: "characters", name, description,
  personality}` (card played as player)
- `ref_names`, `refs` — pcless only: the campaign's player actors (same
  shapes as above), the offscreen reference cast
- `story_entries` — chronicle recap strings, oldest first (compact:
  `one_line or summary`; full: `summary or one_line`)
- `archive_entries` — `[{id, date, text}]`, newest first: absorbed scenes
  OUTSIDE the recap window whose `keywords` the scan window mentions, capped
  at `archive_depth` (`context.archive`). `sections/archive.j2` labels them as
  already concluded, or the model plays an old scene as the current one
- `plot_lines` — `plot.render_open(cid, with_id=False)` lines
- `commitment_lines` — `commitments.render_open(cid, with_id=False)` lines:
  the unresolved promises, threats and foreshadowing (#115), which resolve
  (fulfilled/broken/expired) rather than merely advancing like a plot thread
- `today` — `calendars.today_facts()` fields + `cast`
  (`context.cast_datetime_facts()`), or None when the scene has no date
- `current_setting` — the current location's body ("" if none, and "" for a
  `secrecy: gm-only` location: the setting block is the one world-info body
  that does NOT pass through `context.activate`, so the gate is applied in
  `_assemble` instead)
- `current_setting_secret` — bool; the current location is `secrecy: secret`,
  so its body renders under `scene/_secrecy.j2`'s heading
- `world_info_bodies` — `secrecy: public` bodies selected by
  `context.activate()` (the current location is excluded here and shown as
  `current_setting` instead)
- `secret_world_info_bodies` — the same selection's `secrecy: secret` bodies,
  rendered in the same section under `scene/_secrecy.j2`'s heading.
  `secrecy: gm-only` entries are absent from both lists: `activate` drops
  them before any selection rule runs, so their BODY never reaches a template.
  Their name still can — `mechanics_sheets` labels a sheeted location by name
  whatever its level, because a sheet is functional data, not lore
- `recalled_lore_bodies` / `secret_recalled_lore_bodies` — the same split for
  what `context.semantic.recall` added on top of the keyword rule
- `available_art` — `[{handle, description}]` from `context.art.catalogue`:
  described images this turn could use, ranked against the same scan window
  world info activates on. The pool is the TURN (on-stage cast at their locked
  versions, the current setting, activated and recalled entities, the
  campaign's own library), never the whole store. Empty on a store where
  nobody has described an image, and `sections/available_art.j2` renders
  nothing for an empty list — which is what keeps the prompt byte-identical
  for an install that does not use the feature. The `handle` is what the model
  writes back; `context.art.resolve_handles` turns it into markdown, or into
  nothing, before the reply is split into posts
- `group_states` / `secret_group_states` — `[{name, goals, resources,
  public_perception, focus, secrets}]` from `groupstate.read_state()` for
  activated `groups` entries that have a state.md, split by the group's
  secrecy. The secret list renders in the same section under
  `scene/_secrecy.j2`'s heading rather than relying on the (separately
  dropped) World info section to carry it
- `offscene_active` / `offscene_known` — the cast-directory tiers, rendered as
  two adjacent sections so the token breakdown can price them apart:
  `[{name, dossier}]` for the campaign-active tier and
  `[{id, name, tagline, versions}]` for the known-to-exist one, the latter
  already cut to `offscene_known_limit` by `context.cast._scope_known`. The
  directory's shared heading lives in `scene/off_scene_cast_heading.j2` and is in
  neither section: it is declared as their `Section.heading` and emitted by
  `context.assemble._render_sections`, which opens each contiguous run of the
  two with it — the only place that knows how the reader's prompt layout
  ordered them and whether anything was put between them
- `player_names` — seated player names (the response-format guard)
- `mechanics_rules` — `list[str]`, activated rules-doc bodies (frontmatter
  `always` docs, then docs gated on a present cast member's sheet type, then
  recent-text keyword matches capped at 6) — `context._mechanics()`
- `mechanics_sheets` — `list[{ref, label, type_label, lines}]`, compact
  summaries for sheeted cast + the current location
- `mechanics_checks` — `list[{ref, label, sheet_type, checks}]` (`checks` is
  `[[id, label]]`), the available-checks table also served by GET
  …/scenes/{sid}/checks

All three are `[]` when the campaign has no mechanics module bound
(`store/modules.py:resolve`).

### Roll continuation — `scene/roll_result.j2` / `scene/roll_declined.j2`
Ephemeral system messages `routes.mechanics._continuation_messages` /
`routes.mechanics._declined_continuation_messages` append to `build_messages`'s output
for the POST …/roll-proposal accept/decline call; never persisted.
- `roll_result.j2` (accept) vars: `resolution` (the resolved-check dict),
  `on_roll_docs` (`list[str]`, bodies of every `on_roll` rules doc),
  `check_docs` (`list[str]`, the check's linked rules docs).
- `roll_declined.j2` (decline): no vars.

### Reroll steer and Keep writing — `scene/response_steer.j2` / `scene/extend_instruction.j2`
Ephemeral messages `routes.character_turns` appends to a response's frozen
snapshot for POST …/responses/{rid}/regenerate and …/responses/{rid}/extend;
never persisted (the steer's text is recorded in the steering log).
- `response_steer.j2` (system): vars `guidance`, and optional `continuation`
  (true for a Keep writing prefill, whose reply is continued, not replaced).
- `extend_instruction.j2` (user, after the partial reply in instruction mode):
  vars `words` (the response's word target, or none) and `guidance` (`""`
  when no steer). A prefill-mode extend sends no instruction at all.

### Speaker selection — the `speaker` route's decide item
When two or more NPCs could open a Directed round, `routes.character_turns._select`
asks who speaks first: one `decide()` item (spec 7.4) on the `speaker` route,
which runs on the Decision role. No messages of its own:
`store/response_protocol.py:selector_item` makes the item and `decide/` renders the
prompt around it. Its inputs are gathered by
`store/response_protocol.py:observable_conversation` (the last twelve posts in
context, each through the prompt-phase regex view the caller hands in, as
`{speaker, content}`, synthetic lines dropped) and the round's eligible roster
(`store/response_protocol.py:npc_roster`'s `{ref, name}` shape); the
`decide-speaker` eval case gathers through the same two helpers.
- `response_selector_context.j2` is the item's context. Vars: `conversation`, and
  `note` (the round's user direction, or `""`); the user direction line renders
  only when `note` is set. The roster is not listed: the choice's options are the
  roster.
- `response_selector_question.j2` is the one choice's instructions (id `next`,
  `response_protocol.SELECTOR_QUESTION`, null allowed). No vars. The retired
  one-call prompt's criteria, moved there whole but for two rewordings that follow
  the decide prompt's layout: the transcript is "above" the question, and a
  character is missing "from the options" rather than "the available list".
- Its options are each eligible ref, described by its name, and `grimoire`
  (`response_protocol.GRIMOIRE_REF`), described by
  `response_selector_grimoire.j2` (no vars): the retired prompt's words for what
  grimoire is chosen for.

No rationale is asked for. `store/response_protocol.py:selection_of` maps the answer
back to the `(next, issue)` pair `_round_state` stores, with the two issue strings
the retired parse raised: an explicit null hands control back with no issue, an
answer naming nobody offered is `response_protocol.INELIGIBLE`, and anything else
unreadable is `response_protocol.INVALID_HANDOFF` -- as is a roster `decide()`
refuses before sending: two refs that read as one once normalised, or more than
254 refs, which with `grimoire` is past the 255 options a choice may offer
(`decisions.MAX_OPTIONS`).
`scripts/verify_templates.py` holds that each criterion of the retired prompt is in
a decide-era template verbatim (`SELECTOR_CARRIED`) and that
`response_selector_grimoire.j2` renders the `grimoire` option's own description
(`SELECTOR_OPTIONS`). The capture the prompt log keeps for the pick is this
decide prompt.

## Keeping templates honest

The verification harness checks the WIRING (each builder passes the documented
variables to the right template) and the DATA CONTRACT above, against a
throwaway store exercising every section. It never pins template text, so
editing a prompt here cannot fail it:

    backend/.venv/Scripts/python.exe scripts/verify_templates.py

Run it after renaming/moving a template, changing a template's variables, or
touching the prompt-building code in `backend/src/`. The regular test suite
(`backend/.venv/Scripts/python.exe -m pytest backend -q`) covers the rest.

### `epub/` — not prompts: campaign EPUB export pages

The one non-LLM family. `store/epub.py` renders these (its own jinja2
environment, `autoescape=True`, unlike the prompt contract above) into the
book's XHTML/OPF/CSS. `container.xml` and `stylesheet.css` are static;
`package.opf` takes `identifier`/`title`/`modified`/`cover_id`/`items`/`spine`
(and manifests `nav.xhtml` and `toc.ncx` itself, rather than through `items`);
`nav.xhtml` takes `chapters`/`appendix`/`cover`; `toc.ncx` takes
`identifier`/`title`/`points`; `titlepage.xhtml` takes
`title`/`world`/`date_range`; `chapter.xhtml` takes
`title`/`date`/`location`/`cast`/`epigraph`/`body`; `divider.xhtml` takes
`title`; `appendix.xhtml` takes `name`/`role`/`portrait`/`sections`.
`divider.xhtml` is named for its shape but is specifically the **back-matter**
divider — it hardcodes `epub:type="backmatter"`, which is right for its one
caller (the Appendix) and would be a lie for a front-matter one. A second
caller needs the type passed in, not the template reused as-is.

Navigation is deliberately threefold, because reading systems disagree about
where they look for it: `nav.xhtml` carries both the `toc` nav (the table of
contents, one numbered entry per scene) and a hidden `landmarks` nav, and is in
the spine so it doubles as a Contents page; `toc.ncx` is EPUB 2's table of
contents, for readers that never learned the nav document; and each page
declares an `epub:type` (`cover`, `titlepage`, `bodymatter chapter`,
`backmatter`) so a reading system can label a chapter boundary. Change one and
change the others — `store/epub.py` builds all three from the same chapter and
appendix lists.


## Character from passage

`character_from_passage/system.j2` drafts a sparse description only.
`character_from_passage/user.j2` receives `name`, `passage`, and bounded earlier
observable `context`. The optional detached draft does not create a character.
Dialogue examples are extracted from explicitly attributed source quotes by the
server, then reviewed with the description. Saving retains the selected passage
and full response snapshot in the campaign-local character card's provenance.
Ambiguous attribution leaves examples empty; no additional closeout call is made.

The passage dialog reads deterministic quote evidence through `character-evidence`
before save, even when no model description is requested. Synthetic director,
roll and transition messages are excluded from neighboring observable dialogue.
Colon attribution requires a line-start speaker label; an embedded addressee is
not treated as a speaker. Exact source snapshots are rechecked on save.


### Leading perception preparation

Assigned NPCs emit a short `perception` JSON fence before prose in the same
generation call. `store/response_protocol.py` removes this leading block before
roll detection, streaming and narration persistence. Partial leading blocks
remain hidden. Normal responses without preparation retain their existing
protocol; only leading fences are treated as preparation. The preparation is
retained in the response's existing reasoning artifact for inspection, not in
scene history. JSON is advisory model output, not validated knowledge or a
hard boundary. Existing ST-03 voice and perception guidance remain intact.


Perception preparation retains `known`, `heard_or_seen`, and `unknown`.
`known` entries name an existing source; each `heard_or_seen` entry includes
an exact transcript excerpt, its `kind` (speech or action), and `access`
(the established circumstance allowing this actor to hear or see it).
Missing perception evidence leaves a fact unknown unless an independent
established source already supplies it. These are advisory model claims,
not server-validated citations; the leading-fence parser is unchanged.

The whole preparation paragraph in `scene/response_actor.j2` sits behind the
`perception_rider` template variable, which `context.assemble` fills from the
global `perception_rider` setting (on/off, default on) and `verify_templates.py`
passes as `True`. The paragraph covers events only: what the actor heard or saw
happen. For the visible state of the others (appearance, mood, injuries) it
points at the Scene state section (`scene/sections/tracker_state.j2`) instead,
so the two do not describe the same thing twice. With the rider off, the
paragraph is absent. The setting gates only the prompt instruction, never the
stripping: `routes/character_turns.py` always builds its `ResponseWatcher` with
`perception=True` for an NPC, because a reroll replays a frozen prompt that may
have been composed with the rider on, and a switch flipped between composing a
prompt and reading the reply would otherwise leave a fence in the stored text.
Stripping a leading fence nothing asked for is harmless. The surrounding "What
this actor can perceive" and continuity-notes guidance stays either way.
