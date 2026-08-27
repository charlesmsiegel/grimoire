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
  and are shared across calls.
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
Mirrors `store/voice_drift.py:build_prompt` (one call per present NPC **that
has a voice anchor** — an anchorless character is never judged, which is what
keeps the extra calls opt-in).
`user.j2` vars: `name`, `anchor` (never ""), `transcript` (render
`snippets/transcript.j2` over the scene's messages), and `correction`
(optional, `""` when there is none). The correction is the character's
outstanding drift note, and the CALLER owns deciding it is still in force --
`_stage_voice_drift` checks its fingerprint against the current anchor first,
because a note judged against a REPLACED anchor is suppressed for the writer
and must not be shown to the judge as current. `system.j2` treats it as
superseding the anchor wherever the two conflict, which is what the scene
prompt tells the writer, so a judge that could not see it would flag the model
for obeying its instructions.
The reply is one JSON object, `{"verdict": str, "note": str}`, parsed by
`voice_drift.parse_output` through `absorb.extract_object`. `note` becomes the
corrective `scene/voice_correction.j2` renders on the next turn.

`verdict` is an enum, **not** a boolean, and the reason is that clearing a
standing flag is a write: only an explicit `in_voice` justifies one.
- `drift` — spoke, sounded wrong; `note` carries the corrective.
- `in_voice` — spoke enough to judge, sounded right → stages a clear.
- `not_enough` — silent or too few lines to tell. A real answer, not a
  fallback: silence is not evidence of sounding right, so a standing flag
  survives it.
- anything unparseable maps to `voice_drift.UNKNOWN`, which the absorb route
  reports as a failed check. Collapsing it into `in_voice` would let a garbled
  reply retire a real corrective on a default-approved review.

### `scene_suggestions/` — POST /campaigns/{cid}/scene-suggestions
Mirrors `store/suggest.py:build_prompt`. Messages: system, user.
Vars (both files take the same set):
- `s` — `suggest.build_snapshot()` dict
- `offscreen` — bool selector: `instruction/{standard|offscreen}.j2`
- `greeting_candidates` — `suggest.greeting_candidates()` list or None
`system.j2` appends `instruction/date_addendum.j2` when `s.now` is set and
`instruction/rank_addendum.j2` when there are greeting candidates. Both
addenda begin with a **leading space** — they continue the instruction
sentence-style; keep it when editing.

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
(`absorb.fact_snapshot()` — lines per `snippets/fact_line.j2`), `transcript`
(`snippets/transcript.j2`).

### `audit/` — the post-absorb mechanics audit inside POST …/absorb
Mirrors `store/audit.py:build_prompt`. Messages: system, user.
`user.j2` vars: `sheet_blocks` (`audit.sheet_blocks()[0]` — one rendered block
per present, sheeted cast member and the scene's current location; each
mutable line marks "start X -> now Y" against `audit.baseline_field()`, each
static line is marked `[static]`), `roll_lines` (`audit.roll_lines()` — the
scene's roll-log entries), `transcript` (`snippets/transcript.j2`).

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
The confirmation half of heuristic scene-break detection (#84). Mirrors
`store/scene_break.py:build_prompt`. Messages: system, user.
`user.j2` vars: `title` (the scene's own, so a proposed NEXT title is not a
restatement of it), `facts` (`chronicle.scene_facts()`), `signals`
(`scene_break.evaluate`'s `[{kind, weight, detail}]` — only `detail` is
rendered), `transcript` (`snippets/transcript.j2`).

`facts` carries the same weight here as in `rolling_summary/` and for the same
reason: the first location and the first date are set silently, so on the
scenes that never move the transcript states neither.

`transcript` is the posts since the scene was last **considered**, not the whole
scene — the question is whether the story has arrived somewhere since it was
last asked, and re-sending three hundred posts to ask it would make the free
half of the feature pointless. `signals` is empty on a forced question that
crossed no threshold, and the template renders no reason list at all there
rather than an empty one.

The system prompt states outright that the signals are the reason for the
question and never evidence for a yes: they are counts, and a count cannot see
whether anything was settled. The reply is a JSON object
(`{"break", "reason", "title"}`); an unreadable one parses as `break: false`
with empty prose, because this runs automatically off the play loop. Nothing
here ends or splits a scene — the answer is a suggestion in the inspector.

### `scene/` — the context builder (`store/context/`)
Serves POST …/chat, …/retry, …/regenerate (via `build_messages` /
`build_director_messages`) and …/opener (via `build_opener_messages`).

Message assembly (code-side, mirrored from `context/assemble.py`):
1. `system.j2` — one system message; omitted if it renders empty.
2. The projected history: each stored message through
   `scene/history_line.j2`, consecutive same-role lines merged with a blank
   line between them (`context/story.py:_project_history`).
3. Director turn only: the note as a user message — the player's text, or
   `scene/director_note.j2` when blank. Opener only: the (substituted)
   opener prompt as the user message (openers include no history).
4. Regenerate with guidance only: `scene/regenerate_guidance.j2` as an extra
   system message before the post-history.
5. `scene/post_history.j2` as a system message, if non-empty. Vars:
   `npc_cards`, `voice_correction` and `length_correction`. The last is
   rendered from `scene/length_correction.j2` (vars: `drift`, `budget`) when
   `length_drift.measure()` finds the last 3 turns over budget, else `""`;
   `voice_correction` is rendered from `scene/voice_correction.j2` (var:
   `voice_notes`, a list of `{name, note}`) for the present NPCs carrying an
   unresolved `store/voice_drift.py` flag, else `""`. Voice rides ahead of
   length: length is about trimming what was written, voice is about who is
   writing it. This is the closest slot to generation, which is why both
   counterweights ride here rather than in the system prompt.
6. Opener only: `scene/opener_shape.j2` as the final system message (always
   sent — last, right before generation, so it outranks the system prompt).

`system.j2` takes one var, `sections` — the already-rendered section texts, in
order, which it joins with blank lines. It used to `include` every section
itself; that made it a second render path over the same data, disagreeing with
the inspector's breakdown as soon as anything could be dropped. The order and
the selectors now live in `context.assemble._SECTIONS`, and
`_render_sections` renders it: `opener` (prepend
`opener_instruction/{standard|offscreen}.j2`), `pcless` (offscreen sections +
opener variant), `story_full` (`sections/story_so_far/{full|compact}.j2`;
the opener uses `full` with the last 5 scenes, chat uses `compact` with the
configured `recap_depth`).

Each `_SECTIONS` entry also carries a packer tier — `lock-in`, `spotlight`,
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
- `budget` — `{reply_words, blocks, paragraphs, speakers,
  blocks_per_speaker}`, the resolved length budget from
  `response_presets.resolve()`; feeds `sections/response_budget.j2`. Always
  complete (StrictUndefined), falling back to the `standard` length preset.
  Spec: docs/superpowers/specs/2026-07-26-response-presets-design.md
- (no vars) `sections/natural_prose.j2` — the always-on anti-AI-ism
  defaults (names at invention, banned stock phrases, beat-word rationing,
  banned constructions, rhythm); sits right after the prose style, which
  may override only its rhythm guidance. Spec:
  docs/superpowers/specs/2026-07-14-natural-prose-block-design.md.
- `npc_cards` — locked card `data` dicts of in-scene NPCs (also feeds the
  card-level system prompts, descriptions, message examples, post-history)
- `states` — `[{name, current_state, knows, suspects}]` from
  `playstate.read_state` for in-scene NPCs with any state. In a scene with a
  player, `suspects` is POV-filtered (#116): a suspicion naming another
  present actor is withheld, since it is a private and possibly false belief
  the model cannot tell from a fact. A `pcless` scene is the director's own
  view and gets the stored value unfiltered — `context/world_state.py:
  _visible_suspects`
- `transient_states` — `[{name, fields: [{label, value}]}]`, the per-turn
  mood/intent/posture ledger (#120) decayed to the last `turnstate_depth`
  posts, newest value per field, labelled with the CAST name (what the model
  keys its tracker block by). `[]` when `turnstate_depth` is `0`, which is the
  shipped default — `store/turnstate.py`
- `transient_tracker`, `transient_fields` — `bool` (is the ledger switched on)
  and `["mood", "intent", "posture"]`, for `sections/transient_tracker.j2`: the
  instruction asking the model to end each reply with a fenced `state` block.
  `routes.streaming._persist_reply` strips that block before the reply is split
  into posts, so it is never part of a transcript. The section carries
  `except_opener=True` — the opener is streamed unpersisted into a box the user
  adopts by hand, and there is no reply after it to strip the block from
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
  description}` (persona) or `{kind: "characters", name, description,
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
  directory's shared heading lives in `scene/_off_scene_cast.j2` and is in
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
