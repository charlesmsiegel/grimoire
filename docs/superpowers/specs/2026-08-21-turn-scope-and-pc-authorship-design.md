# Turn scope and PC authorship — teaching the prompt what one reply is

## Problem

Two complaints, one root cause.

**Every reply tries to be a complete scene.** Posts open with an establishing
narration block, run a beat to its resolution, and land on a closing image.
Nothing breathes; the story is rushed through at one resolved beat per turn,
and a scene that should take fifteen exchanges takes four.

**The model writes the player's character.** The existing guard is one
trailing clause on a formatting instruction and names only the two violations
models do not actually commit. The gap is currently patched in a user's
personal global system prompt, which is the clearest possible signal that the
shipped default is wrong.

These are the same bug. The scene prompt specifies **shape** — words, blocks,
paragraphs, speakers, blocks-per-speaker, five knobs and an adaptive drift
corrective — and specifies **scope** nowhere. Nothing tells the model what one
reply is relative to the scene it sits in.

### Why the prompt produces this

1. **`scene/sections/response_format.j2`** says: *"Use `**Grimoire:**` for
   narration, scene description, and any voice that isn't a named character."*
   No placement rule. The model bookends — establishing narration on top,
   summarizing narration at the bottom — and that bookend **is the shape of a
   scene**: establishing shot, action, closing image. The reply format itself
   teaches "one post = one complete vignette".

2. **`scene/sections/response_budget.j2`** hands over a word target with no
   scope statement. A container plus a number reads as *fill this with a
   satisfying unit of story*, and "satisfying unit" resolves to "a resolved
   beat".

3. **`scene/opener_shape.j2`** prescribes exactly that bookended shape for the
   opener: *"Open with exactly one `**Grimoire:**` paragraph that sets the
   scene…"*. The opener is the first assistant message in every transcript, so
   every subsequent turn has an in-context exemplar of the bad shape. Few-shot
   beats instruction, and nothing in the prompt ever contradicts it.

4. **Nothing says "continue".** The only such text is
   `scene/director_note.j2` ("Continue the scene."), a user-turn fallback for
   empty input — not a standing rule.

5. **The closing narration block is where PC authorship lands.** A narration
   block that has to "land" the post is pulled toward summarizing the moment's
   effect on the POV character, who is the PC. Removing the bookend addresses
   both complaints with one rule.

The sharpest evidence that the concept is merely missing rather than hard:
**`scene_break/system.j2` already contains it**, but is only ever shown to the
judge that decides whether a scene ended, never to the writer —

> "A scene ends when the beat it was about has resolved… A scene has NOT ended
> merely because the characters walked into another room… Movement in the
> middle of an unresolved beat is pacing, not a boundary."

`natural_prose.j2`'s *"let some moments pass without a dramatic beat"* is the
only breathing-room language in the entire prompt corpus, and it governs
sentence rhythm, not turn scope.

## Decisions (from brainstorming)

- **Always-on sections, no knob.** Two new lock-in sections, siblings to
  `natural_prose.j2`. No config keys, no cascade, no Configuration UI. Both
  problems are "the default is wrong", not "I need per-campaign control".
- **Ban the closing narration block**, rather than rationing narration or
  capping it per reply. Structural, one sentence, mechanically checkable, and
  it removes the block where PC authorship concentrates.
- **PC guard gets its own section**, enumerating the real failure modes the way
  `natural_prose.j2` enumerates banned phrases. Enumeration is what makes that
  block work.
- **Openers keep their shape.** An opener legitimately establishes; it has no
  history to continue from. The new section draws the contrast explicitly so
  the model stops reading the opener as the template for every post.
- **`/end` is the escape hatch**: a player command that vanishes from the
  transcript and licenses the next reply to close the scene.
- **`/end` is parsed backend-side** off `ChatTurn.content`, **recorded in scene
  frontmatter** so retry and regenerate reproduce it, **prompt-only** in
  effect, and **a single special case** rather than a command framework.

### The PC line: sensation is the world's, volition is the player's

The first draft banned writing what the PC feels, notices, realizes, remembers
or wants. That is too wide. A sensation the environment causes is the world
acting, not the player's character being authored — the cold reaching them, a
sound behind them, a smell bringing a place back, a breath caught. Banning
those makes second-person narration nearly impossible, and
`opener_instruction/standard.j2` *mandates* second person.

The line is **volition**, and within the permitted half, **reflex and
recognition, never conclusion**:

- Allowed: what reaches them, what is plainly there to be seen, what a sense
  stirs unbidden, and how their body answers on its own.
- Banned: speech, action, choice, attempt, **want**, intent, and any
  conclusion or judgment drawn from what they perceived.

*"The voice is one you know"* is the world. *"You realize she has been lying to
you all along"* is a judgment, and judgments are the player's.

This softening extends to involuntary somatic responses (a flinch, a caught
breath, skin gone tight), which the same reasoning covers: they are
environment-triggered reflexes, and permitting "you feel the cold" while
banning "your breath catches" would be incoherent.

## Design

### 1. `templates/scene/sections/turn_scope.j2` — new lock-in section

Placed immediately after `natural_prose`, which holds the only adjacent
existing guidance. Vars: `wrap` (bool).

Default branch (`not wrap`):

```
# Turn scope

This reply is one move in a continuing scene, not a scene in miniature. The
scene has room; this reply does not have to use it all.

- **Advance one beat.** A single exchange, or a single action and its
  immediate answer. Not a sequence, not a montage, not "and then, later".
- **Leave it open.** End while the moment is still unresolved — mid-gesture,
  on a question, on someone about to answer. The player's next post is the
  other half of this one.
- **Do not close.** Do not summarize what just happened, state what it meant,
  or land a closing image. Bringing a beat to rest is what ending a scene is
  for, and this reply is not ending one.
- The scene's opener established. Every reply after it continues.
```

`wrap` branch — replaces the section wholesale rather than appending an
exception, because a contradicted rule reads worse than a replaced one:

```
# Turn scope

The player has asked to end the scene. This reply closes it: bring the current
beat to rest, let it land, and stop. Do not open anything new, and do not
leave a thread mid-gesture for a reply that is not coming.
```

### 2. `templates/scene/sections/player_character.j2` — new lock-in section

Vars: `player_names` (`[str]`). Renders empty when the list is empty — a pcless
scene has no player character to protect, and the section must not appear
there.

```
# The player's character

<Names> is the player's. You write the world; the player writes what
<Names> makes of it.

Never write:
- What they say, or that they said anything at all.
- What they do, choose, decide, or attempt.
- What they want, intend, or have resolved on.
- Consequence that presumes their answer — "the blow lands before you can
  raise your guard" decides how they moved.
- Movement through time or space — "by the time you reach the door".

You may write what the world does to them: what reaches them (the cold, the
smell of tar, a footstep behind), what is plainly there to be seen, what a
sense stirs unbidden (a smell that brings back a place, a face already
familiar), and how their body answers on its own — a breath caught, a flinch,
skin gone tight.

Keep it to the reflex and the recognition, never the conclusion. "The voice is
one you know" is the world acting. "You realize she has been lying to you all
along" is a judgment, and judgments are theirs.

This holds in every block, **Grimoire:** included. The line is volition:
sensation is the world's, what they make of it is theirs. End the reply where
their answer begins.
```

The closing sentence deliberately restates the turn-scope hand-off rule, so the
two sections reinforce rather than compete.

### 3. `templates/scene/sections/response_format.j2` — two edits

- **Remove** the trailing `Never write dialogue or actions for: …` clause.
  Superseded by §2; two statements of one rule drift apart, and this one is in
  the weaker position.
- **Add**, gated on `not wrap`: *"The reply does not end on a `**Grimoire:**`
  block. The last block belongs to a character."*

The section gains `wrap` as a var. `player_names` stays plumbed to it only if
some other clause needs it; otherwise it moves to §2.

### 4. `/end`

**Parsing** — a new pure module (no store access, no locks, so no
`lock_domain` classification needed). One function, roughly:

```python
def take_end(content: str) -> tuple[str, bool]:
    """`content` with a trailing/leading `/end` line removed, and whether one
    was there."""
```

Recognition rules, deliberately narrow:

- Recognized only as a **line that is exactly `/end`** (after stripping
  surrounding whitespace), and only as the **first or last line** of the post.
  A `/end` mid-paragraph is prose.
- `//end` on its own line escapes to a literal `/end` line, mirroring the
  common convention.
- Case-insensitive.
- A post consisting only of `/end` scrubs to `""`, which is already a valid
  input — it is exactly the empty "next NPC round" send that
  `director_note.j2` exists to serve.

**Recording** — `meta["wrap_at"]`, a comma-joined list of message indices,
matching the existing `turn_sizes` / `dismissed` frontmatter idiom. The route
scrubs the command and records the index of the player post it rode in on,
inside the same campaign-lock hold that appends the post.

**Reading** — `context.assemble._assemble` sets `wrap=True` when the last
message in the transcript is a player post whose index is in `wrap_at`. Retry
and regenerate rebuild the prompt from the same transcript and the same
frontmatter, so they reproduce the flag for free. This is the entire reason
durability went to frontmatter rather than the run record.

**Two cleanup paths that would otherwise rot:**

- `scenes/write.delete_from` already rewinds `turn_sizes`, `location_history`
  and `time_history`. `wrap_at` joins them, dropping every index at or past
  the cut. Left alone, a stale index points at whatever post later lands at
  that position, and a subsequent unrelated turn silently wraps the scene.
- `routes.scenes._take_the_post_back` removes the player's post when a turn
  errors. It must drop that index in the same campaign-lock hold, for the same
  reason and with the same ordering argument the existing function documents.

**Effect is prompt-only.** The scene stays open. `scene_break`, its watermark,
and the review flow are untouched — the player may still get the ordinary
end-of-scene question on its usual schedule.

### 5. Section registration

Two entries added to `context.assemble.SECTIONS`, both `pack.LOCK_IN`:

- `turn_scope` — after `natural_prose`.
- `player_character` — after `turn_scope`.

Both lock-in because a section the packer may drop under budget pressure is a
rule that silently stops applying in exactly the long, mature scenes where
pacing and PC-authorship discipline matter most.

## Testing

- **Command parsing** — unit tests over `take_end`: bare `/end`, leading line,
  trailing line, mid-paragraph (not a command), `//end` escape, case, a post
  that is only the command, CRLF, trailing whitespace.
- **Durability** — a `/end` turn, then a regenerate, produces a prompt still
  carrying the wrap section. This is the test that justifies the frontmatter.
- **Rewind** — `delete_from` at and below a recorded index drops it; a later
  post landing at that index does not inherit a wrap.
- **Error path** — a turn that fails after the post is appended leaves no
  `wrap_at` entry behind.
- **Rendering** — each section renders as expected; `player_character` renders
  empty in a pcless scene; `response_format` omits the closing-narration rule
  under `wrap`.
- **Section registration** — both appear in the assembled prompt and in the
  inspector's token breakdown, and neither is droppable.

## Consequences for the existing harnesses

- **`evals/cases.py`** requires prompt sections verbatim in the assembled
  prompt (see the `response_budget.j2` case). All three new variants —
  `turn_scope` default, `turn_scope` wrap, `player_character` — get the same
  treatment, so a reword fails there rather than silently everywhere else.
- **`backend/tests/fixtures/frozen_campaign/snapshot.json`** captures
  `store.context.build_messages` (`sweep.py:197`), so it changes and is
  regenerated deliberately with this work, per CLAUDE.md.
- **`scripts/verify_templates.py`** — builders and templates must agree
  byte-for-byte.
- **`templates/README.md`** — the section list and the section-var list both
  gain entries; `test_docs_guard.py` holds those claims to the code.
- **Lint baselines** — new files will move ruff/mypy counts; `make baseline`
  and commit the smaller files with the change.

## Out of scope

- **`/end` does not change the response budget.** A wrap-up under `terse`
  (150 words, 3 blocks) may read clipped. Deliberately not pre-empted: the
  budget is a target rather than a cap, and coupling a pacing flag to the
  length cascade would undo the "no knob" decision made above. Revisit if it
  actually reads short in play.
- **No command framework.** One recognized token, one flag, one branch. A
  parser built for imagined siblings gets escaping and precedence wrong for
  free; generalize when a second command exists.
- **`opener_shape.j2` is unchanged.** Openers establish by design.
- **No adaptive PC-authorship corrective** in `post_history.j2` alongside the
  voice and length ones. Detecting "wrote the PC" in prose is materially
  harder than counting words, and the section should be given a chance to work
  first.
