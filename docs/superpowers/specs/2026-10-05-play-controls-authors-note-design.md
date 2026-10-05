# Play controls V — author's notes with depth injection

Step 5 of the SillyTavern-parity play controls (programme table in
`2026-10-05-play-controls-swipes-design.md`). Decisions a brainstorm would have
put to the owner are recorded with their reason; the owner asked for the
programme to run without stopping.

## The request

> A short standing instruction inserted at a chosen depth in the history, such
> as four posts from the end, separate from the system prompt.
> Regenerate-with-guidance is one-shot; this persists. Levels: campaign, scene
> and character. With per-character calls, a character's note goes only into
> that character's call. Optional frequency (every N turns), rendered from a
> Jinja2 template, shown as its own inspector section.

## A note

```json
{"text": "Keep the storm audible in every scene.", "depth": 4, "every": 1}
```

- `text` — up to 2000 characters, multi-line allowed. Empty text means no note.
- `depth` — posts from the end, `0..50`, default `4`. `0` puts the note after
  the last post (just before the post-history block); `4` puts it before the
  fourth-most-recent post.
- `every` — inject on every Nth turn, `1..50`, default `1` (always).

## Storage

Notes are multi-line, and frontmatter is single-line by construction, so they
live in JSON: `<campaign>/authors_notes.json`

```json
{"campaign": {...note...},
 "scenes": {"<scene identity>": {...note...}},
 "characters": {"characters:mara": {...note...}}}
```

- Scene notes are keyed by **identity**, not sid, so a rename or the first
  date stamp does not orphan them. Branching (step 3) copies the source's scene
  note to the sibling's new identity; deleting a scene drops its note.
- Character notes are per campaign (the same character may need different
  steering in two campaigns), keyed by the actor ref the response ledger uses.
- New module `store/authors_notes.py`, in `locks.DOMAIN_MODULES`, every write
  under `campaign_lock(cid)` through `atomic`. Reads are lenient: a missing or
  garbled file is no notes, never a failed turn. Forks copy it with the campaign
  directory.

## Injection

In `context.assemble._assemble`, after history has been filtered to what
reaches the prompt (step 2's `in_context`) and before `_project_history`:

1. **Which notes apply.** Campaign note; this scene's note; and the character
   note for `actor_ref` **only** — in a per-character call for that character.
   The narrator call (`actor_ref == "grimoire"`) and non-actor-scoped
   compositions get no character note.
2. **Frequency.** The turn number is the count of in-context player posts and
   director notes in the scene (the turn being generated answers the latest
   one). A note applies when `turn % every == 0`. An opener (no history) gets
   only `every == 1` notes.
3. **Render.** Each applying note renders through
   `templates/scene/authors_note.j2` (`level`, `name` for a character note,
   `text`) into one message `{"role": "system", "content": …}` tagged as an
   author's-note message.
4. **Insert.** At `len(history) - depth`, clamped to `0`. Notes at the same
   depth go campaign, scene, character, in that order.

`_project_history` passes an author's-note message through as its own system
message, never merged with a neighbour and never given a speaker label.
Because the insertion happens before `_prepare` freezes the profile variants,
the notes are in the response's frozen snapshot: a reroll or Keep writing
replays exactly the notes the original saw.

**Packing.** The notes sit inside the history tier; trimming from the front
can only remove a note whose depth is larger than the history that fit, which
is the honest outcome of a tiny budget.

**Provider caveat, stated.** Strict OpenAI-compatible endpoints fold a system
message into the next user turn, and the Claude agent SDK path lifts every
system message into its system prompt, losing the depth. The note still
reaches the model; only its position degrades.

## Inspector

The breakdown gains an **`authors_note`** row ("Author's notes"), `LOCK_IN`,
listing each applied note with its level and depth, and counting their
tokens. Its text is removed from the joined `history` row so tokens are not
counted twice. A configured note that did not apply this turn (frequency) is
listed as "skipped (every N)" with zero tokens, so the player can see why.

## Routes

- `GET /campaigns/{cid}/authors-notes` → the whole file (campaign, characters;
  scenes keyed by sid for the scenes that exist, resolved from identity).
- `PUT /campaigns/{cid}/authors-notes/campaign` body: a note (or empty text to
  clear).
- `PUT /campaigns/{cid}/authors-notes/characters/{ref}` body: a note.
- `PUT /campaigns/{cid}/scenes/{sid}/authors-note` body: a note.
- Validation: plain pydantic model `AuthorsNote {text, depth, every}`; ranges
  checked in the route (400).

## Play view

An **Author's notes** section in the scene inspector sidebar, beside Response
preset and Model routing: three tabs — *Campaign*, *This scene*, *Character*
(a picker over the scene's NPC cast) — each a textarea, a depth number and an
every-N number, with Save. The section header shows how many notes apply to
the next turn.

## Harnesses

- `scripts/verify_templates.py`: the fixture sets a campaign note, and
  `rendered_messages` mirrors the insertion at its depth.
- `evals/cases.py`: a case requiring the rendered note verbatim in the
  assembled prompt (`grade_prompt_section(..., "authors_note",
  "scene/authors_note.j2")`) and asserting a character note appears only in
  that character's call.
- `templates/README.md` "Message assembly" gains the step.

## Testing

Backend:
- a campaign note at depth 4 lands before the fourth-most-recent post; depth 0
  lands after the last post; a depth larger than the history clamps to the
  start;
- `every: 3` applies on turns 3, 6, … and not on 4;
- a character note reaches only that character's call, not another
  character's and not the narrator's;
- notes appear in the frozen snapshot, so a reroll's prompt carries them;
- excluded posts and director notes do not count toward depth;
- the inspector row lists applied and skipped notes and the history row does
  not repeat their text;
- the store survives a missing or garbled file; scene notes follow a rename;
  deleting a scene drops its note; branching copies it;
- routes validate ranges and clear on empty text.

Frontend:
- the inspector section edits each level and saves through the matching route;
  the header count reflects applied notes.
