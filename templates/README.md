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
  `snippets/plot_thread_line/`). A selector variable picks the file — either
  via a dynamic `{% include %}` in the composing template, or by the caller
  choosing which file to render.
- `snippets/` holds line formats that feed prompt *content* (transcripts,
  relationship lines, plot-thread lines) and are shared across calls.
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

### `dossier/` — the per-NPC dossier refresh inside POST …/absorb
Mirrors `store/dossiers.py:build_prompt` (one call per present NPC).
`user.j2` vars: `name`, `prior` (existing dossier, may be ""),
`transcript` (render `snippets/transcript.j2` over the scene's messages).

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

### `absorb/` — POST /campaigns/{cid}/scenes/{sid}/absorb
Mirrors `store/absorb.py:build_prompt`. Messages: system, user.
`user.j2` vars: `facts` (`chronicle.scene_facts()`), `state_snapshot`
(`absorb.state_snapshot()` — values are `snippets/state_snapshot_line.j2`
lines), `rel_snapshot` (`absorb.relationships_snapshot()` — lines per
`snippets/feeling_line.j2` / `snippets/bond_line.j2`), `plot_snapshot`
(`absorb.plot_snapshot()` — lines per `snippets/plot_thread_line/absorb.j2`),
`transcript` (`snippets/transcript.j2`).

### `audit/` — the post-absorb mechanics audit inside POST …/absorb
Mirrors `store/audit.py:build_prompt`. Messages: system, user.
`user.j2` vars: `sheet_blocks` (`audit.sheet_blocks()[0]` — one rendered block
per present, sheeted cast member and the scene's current location; each
mutable line marks "start X -> now Y" against `audit.baseline_field()`, each
static line is marked `[static]`), `roll_lines` (`audit.roll_lines()` — the
scene's roll-log entries), `transcript` (`snippets/transcript.j2`).

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
   `npc_cards` and `length_correction` — the latter rendered from
   `scene/length_correction.j2` (vars: `drift`, `budget`) when
   `length_drift.measure()` finds the last 3 turns over budget, else `""`.
   This is the closest slot to generation, which is why the drift
   counterweight rides here rather than in the system prompt.
6. Opener only: `scene/opener_shape.j2` as the final system message (always
   sent — last, right before generation, so it outranks the system prompt).

`system.j2` selectors: `opener` (prepend
`opener_instruction/{standard|offscreen}.j2`), `pcless` (offscreen sections +
opener variant), `story_full` (`sections/story_so_far/{full|compact}.j2`;
the opener uses `full` with the last 5 scenes, chat uses `compact` with the
configured `recap_depth`).

`system.j2` data vars, in section order — all already `{{user}}`/`{{char}}`
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
  `playstate.read_state` for in-scene NPCs with any state
- `relationship_lines` — `relationships.render_present()` lines
- `players` — seated players: `{kind: "pcs", name, pronouns, summary,
  description}` (persona) or `{kind: "characters", name, description,
  personality}` (card played as player)
- `ref_names`, `refs` — pcless only: the campaign's player actors (same
  shapes as above), the offscreen reference cast
- `story_entries` — chronicle recap strings, oldest first (compact:
  `one_line or summary`; full: `summary or one_line`)
- `plot_lines` — `plot.render_open(cid, with_id=False)` lines
- `today` — `calendars.today_facts()` fields + `cast`
  (`context.cast_datetime_facts()`), or None when the scene has no date
- `current_setting` — the current location's body ("" if none)
- `world_info_bodies` — bodies selected by `context.activate()` (the
  current location is excluded here and shown as `current_setting` instead)
- `group_states` — `[{name, goals, resources, focus, public_perception,
  secrets}]` from `groupstate.read_state()` for activated `groups` entries
  that have a state.md
- `offscene_active` / `offscene_known` — the cast-directory tiers
  (dossiers / taglines + version ids)
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
`package.opf` takes `identifier`/`title`/`modified`/`items`/`spine`;
`nav.xhtml` takes `chapters`/`appendix`; `titlepage.xhtml` takes
`title`/`world`/`date_range`; `chapter.xhtml` takes
`title`/`date`/`location`/`cast`/`epigraph`/`body`; `divider.xhtml` takes
`title`; `appendix.xhtml` takes `name`/`role`/`portrait`/`sections`.
