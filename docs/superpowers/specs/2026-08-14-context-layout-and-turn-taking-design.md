# Prompt layout and active-speaker turn-taking

Issue #29's two remaining implementable layers, taken together: the
**user-editable section order** and the **group active-speaker** layer. The
epic's third open item — playtest validation of block ordering, the
keyless-always-on rule and the `context_scan_depth` default — is a human
activity and is not in scope here.

## Problem

### The section list is a constant

`context/assemble.py` holds `_SECTIONS`, a hardcoded sequence of 29
`Section(label, template, tier, …)` entries. It is genuinely *the* order —
`_render_sections` renders the list and `scene/system.j2` only joins what it is
handed — which is exactly what makes it unreachable. A reader who wants World
info above Character state, or who never wants the Off-scene cast directory in
their prompt at all, has no way to say so short of editing the source.

Two toggles already exist for individual layers (`archive_depth: 0`,
`semantic_recall_depth: 0`), but they are per-feature knobs that happen to
empty a section. There is no general answer.

### With several NPCs cast, nobody is holding the conversation

Every present NPC's card is concatenated into Character descriptions and the
model picks a speaker implicitly. In a two-hander that is fine. With four NPCs
in a room it produces the failure every group scene has: one character
monologues for three turns while the other three stand silently, or all four
answer the same question in sequence.

`context/cast.py` already looks at speakers, but only to *measure* — the drift
roster canonicalizes labels so `length_drift` can count them, and `_voice_notes`
carries correctives about how a character sounds. Neither decides who talks.

## Scope

**In:** a stored, user-editable prompt layout (order + enable/disable +
inspector label) behind a config toggle; a derived active-speaker nomination
rendered as its own section behind a second config toggle; the route surface
and Configuration-page UI for both; `id` on breakdown rows.

**Out:** editing the *prompt text* of a section (already possible — see below);
per-campaign or per-scene layouts; user-editable tiers; an LLM director pass to
choose the speaker; the playtest item.

## Decisions

### Relabelling renames the inspector row, not the prompt heading

This is the one place the issue's wording and the code disagree, so it is worth
being explicit. Each section template renders its own heading —
`character_state.j2` emits `# Character state`, `transient_state.j2` emits
`# Transient state`. `Section.label` never reaches the model; it is what the
scene inspector and the token breakdown call the row.

So "user-editable prompt template" is already solved, by a different mechanism:
`prompts.py` loads `templates/` from disk with auto-reload, and its docstring
states the intent outright — *"the templates own the text, so prompts are
editable without touching code."* A reader who wants a different heading, or
different wording, edits the template.

Making the heading configurable as well would mean threading a heading variable
through 29 templates, and it would put a user-typed string inside a contract
other things depend on: `evals/run.py` requires the budget, reply-format and
roll-protocol sections verbatim in the assembled prompt, and the mechanics and
transient-tracker parsers key off blocks the prompt asks for by name. The
layout owns *presentation and presence*; the template owns *text*. Label edits
therefore change the inspector row only, and the UI says so.

### Tier is not user-editable

`pack.py` documents at length why `RECALLED` sits below `ARCHIVE`: semantic
recall promises to be purely additive, and within a tier the packer drops the
largest section first, so sharing a tier let a recalled-lore section evict the
Earlier-scenes section — swapping context the prompt already had for context it
never did. A user-editable tier is a control whose whole function is to break
that promise.

Prompt order and drop order are two axes. Reordering moves a section within the
message; the tier decides what gives way when the message does not fit. Only
the first is a preference, and only the first is exposed.

The three selectors (`pcless_only`, `opener_only`, `except_opener`) stay
code-owned for the same reason. `except_opener` on the transient-state tracker
is not a taste: the opener is streamed unpersisted into a box the user adopts
by hand, so a machine-readable block there is one they have to delete
themselves, and there is no reply after it to strip it from.

### A missing catalog id is inserted, not appended

The merge rule that matters. A layout saved today does not know about a section
a later version ships. Appending unknown-to-the-layout sections at the end
would silently move a new Response format block below everything — the exact
place it must not be.

So: walk the catalog; a catalog section absent from the saved layout is
inserted **immediately after its nearest preceding catalog neighbour that is
present in the merged output**, enabled, with its default label — at the front
when it has none. A user who saved a layout in one version and upgrades gets
the new section where its author put it, without re-saving.

The mirror rule: an id in the saved layout that is not in the catalog is
ignored, which is how a section a later version *removes* stops rendering
without a migration.

### Both layers ship off, and off is byte-identical

`prompt_layout_enabled` and `speaker_turn_taking` both default to off. With
`prompt_layout_enabled` off, `layout.apply()` returns the catalog untouched;
with `speaker_turn_taking` off, no nomination is computed and the section
renders empty and drops out. An install that never visits the new UI sends the
same bytes it sent before.

This is the discipline `context_budget: 0` and `semantic_recall_depth: 0`
already set, and here it is also the answer to cost: both layers change what
the prompt costs — a disabled section removes tokens, the speaker section adds
them — so neither may arrive by upgrade.

Turning the layout toggle off **keeps** the saved layout. It is a bypass, not a
delete, so a reader can A/B their own ordering against the default without
rebuilding it.

### The speaker is derived, never stored

Nomination is computed from the transcript and the cast on each assemble pass.
No ledger, no rotation counter.

A stored counter would be a second source of truth about who has spoken, free
to disagree with the transcript the moment a post is undone, a turn is
regenerated, or the scene file is hand-edited — and `store/scenes` serializes
its whole mutator surface specifically to keep the transcript authoritative.
Deriving costs one pass over history already in memory, cannot drift, and makes
regenerate reproduce the same nomination rather than advancing a rotation the
user never saw.

### Nomination, in order

1. **Direct address wins.** If the last player post names exactly one present
   NPC, that NPC leads. Matching goes through `scenes_serialize.match_name`
   over the present cast's names, and a name `confusable` with another present
   actor's nominates nobody — the same guard `_voice_notes` uses, for the same
   reason: an instruction pointed at the wrong character is worse than none.
2. **Otherwise, least-recently-spoken.** Among present NPCs, whoever has gone
   longest without a block leads. Ties break toward fewest blocks this scene,
   then cast order — so a given store always nominates the same way.
3. **Fewer than two present NPCs renders nothing at all.** Turn-taking is a
   group problem. In a two-hander "Seraphine leads this turn" is tokens spent
   telling the model something the cast list already said.

The section names the lead, names who has been quiet longest, and asks the
others to react rather than take the scene over. It is `SPOTLIGHT` — live
situation, like the state sections it sits beside — and it is placed
immediately after Transient state.

### An id on every breakdown row

`ContextBreakdown.tsx` keys its rows on `s.label`. Labels are unique today by
construction; the moment a user can rename them, two rows can collide and React
renders one. Breakdown rows therefore carry `id`, and the component keys on it.
This is a defect the layout layer would otherwise introduce, so it is part of
it.

## Architecture

```
store/context/layout.py     prompt_layout.json I/O + the catalog merge.
                            Takes the catalog as an argument; never imports
                            assemble, so the module graph stays acyclic.
store/context/speaker.py    nominate(cast, history) -> {lead, quiet, silent_for}
                            or None. Pure over its inputs.
store/context/assemble.py   _SECTIONS gains `id`; _render_sections calls
                            layout.apply(); _assemble calls speaker.nominate().
templates/scene/sections/active_speaker.j2
routes/config.py            GET/PUT /api/prompt-layout
frontend ConfigView         a Prompt layout panel: reorder, enable, relabel,
                            reset.
```

`prompt_layout.json` is global (`<home>/`), beside `config.md`. Structured data,
so it does not belong in config frontmatter; the two toggles do, and are
ordinary `_CONFIG_KEYS` entries.

## Error handling

Every read of `prompt_layout.json` is defensive, on the pattern `turnstate.py`
sets: a truncated, hand-edited or wrong-typed file falls back to the catalog
rather than taking scene generation down. A malformed entry is skipped
individually; a malformed file is ignored whole. Labels are capped in length
and non-strings are dropped.

`speaker.nominate()` returns `None` on anything it cannot answer — no cast, no
history, an unreadable name — and `None` renders no section.

## Testing

- `test_context_layout.py` — each merge rule stated above, including the
  insert-at-neighbour upgrade case and the ignored-unknown-id case; the
  defensive reads; and that the toggle off produces the catalog unchanged.
- `test_context_speaker.py` — direct address; least-recently-spoken; both
  tie-breaks; the confusable-name suppression; fewer than two NPCs renders
  nothing; toggle off changes no bytes.
- `test_context.py` — a byte-identity assertion for both toggles off, which is
  the claim the whole "ships disabled" argument rests on.
- Frontend — the Prompt layout panel's reorder/toggle/relabel/reset, and
  `ContextBreakdown` keying on `id`.
- `scripts/verify_templates.py` — its `gather()` mirror spells the section
  order out by hand and must learn `active_speaker.j2`. It exercises the
  default path, which the toggles leave unchanged.

## Review gates

CLAUDE.md's Codex checkpoints cannot run in this environment — there is no
`codex` binary and no codex plugin installed. `/brutal-review`, looped to
convergence, stands in for them, and the PR says so rather than implying the
gates passed.
