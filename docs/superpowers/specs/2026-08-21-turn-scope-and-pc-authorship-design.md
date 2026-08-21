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
  history to continue from.
- **`/end` is the escape hatch**: a player command that vanishes from the
  transcript and licenses the next reply to close the scene.
- **`/end` is parsed backend-side** off `ChatTurn.content`, **recorded in scene
  frontmatter** so retry and regenerate reproduce it, **prompt-only** in
  effect, and **a single special case** rather than a command framework.

### The PC line: sensation is the world's, volition is the player's

An earlier draft banned writing what the PC feels, notices, realizes,
remembers or wants. That is too wide. A sensation the environment causes is
the world acting, not the player's character being authored — the cold
reaching them, a sound behind them, a smell bringing a place back, a breath
caught. Banning those makes second-person narration nearly impossible, and
`opener_instruction/standard.j2` *mandates* second person.

The line is **volition**, and within the permitted half, **reflex and
recognition, never conclusion**:

- Allowed: what reaches them, what is plainly there to be seen, what a sense
  stirs unbidden, and how their body answers on its own.
- Banned: speech, action, choice, attempt, **want**, intent, and any
  conclusion or judgment drawn from what they perceived.

*"The voice is one you know"* is the world. *"You realize she has been lying to
you all along"* is a judgment, and judgments are the player's.

This extends to involuntary somatic responses (a flinch, a caught breath, skin
gone tight), which the same reasoning covers: they are environment-triggered
reflexes, and permitting "you feel the cold" while banning "your breath
catches" would be incoherent.

## Precedence

Stated in the blocks themselves, because `natural_prose.j2` had to learn this
the expensive way — its own spec records the hierarchy under the heading
*"(adversarial-review finding)"*, added after a review caught that an always-on
behavioral block with no stated ordering gets improvised against. Three
always-on blocks with no ordering between them would be that finding, tripled.

1. **Response format and established facts win.** Unchanged from
   `natural_prose.j2`: the `**<Name>:**` markers, resolved identities, and
   every existing name outrank everything below.
2. **The PC boundary is absolute.** `player_character` is never overridden — not
   by a prose style, not by a card's post-history instruction, not by the
   global system prompt. It is the one rule in the corpus about whose words
   these are rather than what they sound like, and a style that wants the
   narrator inside the PC's head is asking for something the player owns.
3. **`turn_scope` outranks the prose style.** This is the one place the
   hierarchy differs from `natural_prose.j2`, which yields its rhythm guidance
   to a set style. Pacing is not rhythm: a style describes how prose *sounds*,
   and letting it also decide how much story a turn consumes reintroduces the
   bug this spec exists to fix, for whoever picked that style.

   The apparent conflict is smaller than it looks. `superheroes.md` asks to
   *"cut away at the moment of impact, mid-sentence, mid-fall"* and
   `pulp-adventure.md` wants cliffhangers — both of which are **exactly**
   `turn_scope`'s "leave it open", not a contradiction of it. The only clause
   genuinely in tension is "do not land a closing image", and a style that
   cuts away mid-fall was never landing one. Nothing in either style file
   needs editing.
4. **`/end` outranks `turn_scope`,** by replacing it rather than contradicting
   it (below).

## Design

### 1. `templates/scene/sections/turn_scope/` — new lock-in section

Placed immediately after `natural_prose`.
**Registered `except_opener=True`** — an opener has no scene to continue, and
`opener_shape.j2` (sent last, explicitly to outrank the system prompt) orders
the establishing block this section forbids. `transient_tracker` already uses
that flag for the same class of reason.

**Three variants, selected through `_VARIANTS`** — the same mechanism
`opener_instruction` (standard/offscreen) and `story_so_far` (full/compact)
already use, keyed on data the assembler already carries:

```python
"scene/sections/turn_scope":
    lambda d: "wrap" if d["wrap"] else ("pcless" if d["pcless"] else "standard"),
```

The variant exists because the hand-off sentence names the player, and in a
pcless scene there isn't one — the director is driving. `pcless.j2` is
`standard.j2` with that sentence rewritten ("what comes next is the other half
of this one") and nothing else changed. Rendering the standard text there
would state something false about who posts next, in a section whose whole job
is telling the model where its turn stops.

`standard.j2`:

```
# Turn scope

This reply is one move in a continuing scene, not a scene in miniature. The
scene has room; this reply does not have to use it all.

- **Advance one beat.** A single exchange, or a single action and its
  immediate answer. Not a sequence, not a montage, not "and then, later".
- **Leave it open.** End while the moment is still unresolved — mid-gesture,
  on a question, on someone about to answer. The player's next post is the
  other half of this one.
- **Hand the moment over, don't set it down.** Stop at the point the story is
  still moving; ending a beat at rest is what ending a scene is for, and this
  reply is not ending one.
```

The third bullet is phrased as a positive direction with the prohibition
trailing it, deliberately. The natural-prose spec's stated mitigation for
prompt-level ban lists is *"pair every 'avoid' with a positive direction"*, and
an earlier draft of this bullet was three stacked negations.

`wrap.j2` — replaces the section wholesale rather than appending an exception,
because a contradicted rule reads worse than a replaced one. Worded so one
file serves both the player and the director, which is why there is no fourth
`wrap_pcless` variant:

```
# Turn scope

You have been asked to close this scene. This reply ends it: bring the current
beat to rest, let it land, and stop. Do not open anything new, and do not
leave a thread mid-gesture for a reply that is not coming.
```

### 2. `templates/scene/sections/player_character.j2` — new lock-in section

Vars: `player_names` (`[str]`). Renders empty when the list is empty — a pcless
scene has no player character to protect. **Not** `except_opener`: an opener
must not write the PC either, and today's only guard there is one sentence in
`opener_instruction/standard.j2`.

Singular and plural are separate renderings; `assemble.py:140-153` builds
`player_names` by looping over seated actors and nothing caps it at one.
Singular:

```
# The player's character

<Name> is the player's. You write the world; the player writes what <Name>
makes of it.
```

Plural: *"`<A>` and `<B>` are the players'. You write the world; the players
write what their characters make of it."* The body is shared:

```
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

This holds in every block, **Grimoire:** included, and no prose style or
character instruction relaxes it. The line is volition: sensation is the
world's, what they make of it is theirs. End the reply where their answer
begins.
```

The closing sentence restates the turn-scope hand-off rule, so the two sections
reinforce rather than compete.

### 3. `templates/scene/sections/response_format.j2` — two edits

- **Remove** the trailing `Never write dialogue or actions for: …` clause.
  Superseded by §2; two statements of one rule drift apart, and this one is in
  the weaker position. `player_names` leaves this template — update
  `templates/README.md:342`, which `test_docs_guard.py` holds to the code.
- **Add**, gated on `not wrap`: *"The reply does not end on a `**Grimoire:**`
  block. The last block belongs to a character."*

### 4. `/end`

**The flag is a scene-level boolean, not a message index.** An earlier draft
keyed it to the index of the player post that carried the command. Review
killed that: `routes/scenes.py:474` computes

```python
ephemeral = store.scenes.is_pcless(cid, sid) or not turn.content.strip()
```

and `:500` guards the whole append behind `if not ephemeral:`. So an
index-keyed flag has nothing to attach to in **the two commonest ways to invoke
the feature** — typing `/end` alone (scrubs to `""` → ephemeral → no post) and
any pcless scene (ephemeral by definition). A boolean also survives a 🎲 line
landing after the post, needs no rewind arithmetic in `delete_from`, and
removes the two-writes-must-agree problem entirely.

**Parsing** — a new pure module (no store access, no locks, so no
`lock_domain` classification needed):

```python
def take_end(content: str) -> tuple[str, bool]:
    """`content` with a leading/trailing `/end` line removed, and whether one
    was there."""
```

- Recognized only as a **line that is exactly `/end`** after stripping
  surrounding whitespace, and only as the **first or last line** of the post.
  A `/end` mid-paragraph is prose.
- `//end` on its own line escapes to a literal `/end` line.
- Case-insensitive. CRLF-tolerant.

**Ordering in the route**, both parts load-bearing:

- The scrub runs **before `ephemeral` is computed**, so `/end` alone correctly
  becomes the empty "next NPC round" send — which is exactly the right
  behavior: *wrap it up, NPCs*.
- The scrub runs **before `expand_macros`** (`:508`), so the command is taken
  from literal player input and no macro expansion can synthesize one.

**Lifecycle** — `meta["wrap_next"]`, written under the campaign lock already
held at `:492`:

| Event | Effect |
|---|---|
| A send carrying `/end` | set |
| The wrap reply persists | **left set** |
| A later send *without* `/end` | cleared |
| `_take_the_post_back` (turn errored) | cleared |
| `delete_from` (rewind) | cleared |

Leaving it set through the reply is what makes regenerate work — the durability
requirement that sent this to frontmatter in the first place. `post_regenerate`
parks the trailing reply and streams a fresh one without appending a user post,
and `post_retry` likewise; the still-set flag applies, so both re-roll a
wrap-up rather than silently reverting to a mid-scene reply. It clears on the
next ordinary send, which is the player saying they are still playing.

**The clear must also run on the ephemeral path.** An empty "next NPC round"
send carries no `/end` and so must clear the flag — but it takes the
`ephemeral` branch, which today performs no scene write of its own. Clearing
only where a post is appended would leave a wrap-up scene stuck wrapping every
subsequent NPC round. The clear therefore hangs off the send, not off the
append, inside the campaign-lock hold at `:492`.

Two accepted losses, both stated rather than solved: a turn that errors clears
a flag that a *previous* `/end` may have set, and a rewind clears it
unconditionally. Both fail toward not-wrapping, which is the recoverable
direction — the player retypes three characters.

**Reading** — `context.assemble._assemble` reads `wrap_next` and passes `wrap`
to `turn_scope` and `response_format`.

**The user turn has to agree with the system section.** `routes/scenes.py:526`
builds the ephemeral note as

```python
note = turn.content.strip() or prompts.render("scene/director_note.j2")
```

and `director_note.j2` is the single line **"Continue the scene."** A bare
`/end` scrubs to `""`, so without a change the model receives *"Continue the
scene."* as its user turn directly beneath a system section saying the scene
has been asked to close — the two halves of the prompt contradicting each
other at the exact moment the feature is supposed to be unambiguous.

New sibling template `scene/wrap_note.j2` — **"Bring the scene to a close."** —
selected in place of `director_note.j2` when `wrap_next` is set and the note
would otherwise be the default. A send that carries `/end` *and* prose keeps
the prose as its note, unchanged; only the defaulted case swaps.

**Effect is prompt-only.** `scene_break`, its watermark, and the review flow
are untouched. A player typing `/end` has authoritatively answered the question
the confirming call spends an LLM call to ask, and pre-answering it would save
that call — but it would couple the prompt flag to the break watermark, which
is the coupling "prompt-only" was chosen to avoid. Left on the table
deliberately; see *Known interactions*.

### 5. Section registration

Two `pack.LOCK_IN` entries in `context.assemble.SECTIONS`, after
`natural_prose`: `turn_scope` (`except_opener=True`), then `player_character`.

Lock-in because a section the packer may drop under budget pressure is a rule
that silently stops applying in exactly the long, mature scenes where pacing
and PC-authorship discipline matter most.

**The cost, stated rather than assumed:** `natural_prose.j2` is 468 words.
These two add roughly 410 more, so the standing always-on instruction overhead
close to doubles — ~550 tokens on every chat, retry, regenerate, director and
opener turn, undroppable. That is the price of the feature and it is worth
naming in the spec rather than discovering in a token breakdown.

## Testing

- **Command parsing** — `take_end`: bare `/end`, leading line, trailing line,
  mid-paragraph (not a command), `//end` escape, case, CRLF, trailing
  whitespace, a post that is only the command.
- **The two ephemeral paths, explicitly** — `/end` alone in a normal scene,
  and `/end` in a pcless scene, both set `wrap_next` and both produce a prompt
  carrying the wrap section. These are the regression tests for the review
  finding that killed the index design; without them the next refactor
  reintroduces it.
- **Durability** — `/end`, then regenerate, still wraps.
- **Clearing** — an ordinary send clears it; an errored turn clears it; a
  rewind clears it.
- **Rendering** — each section renders; `player_character` renders empty
  pcless and renders plural with two seated players; `turn_scope` is absent
  from an opener; `response_format` omits the closing-narration rule under
  `wrap`.
- **Variant selection** — `standard` / `pcless` / `wrap`, with `wrap` winning
  over `pcless` when both hold (a director scene that was asked to close).
- **The note agrees with the section** — a bare `/end` produces
  `wrap_note.j2`, not "Continue the scene."; a `/end` sent alongside prose
  keeps the prose.
- **Registration** — both appear in the assembled prompt and the inspector
  breakdown; neither is droppable.

### The behavioral grader

Verbatim-presence evals prove the instruction is *present*, not *obeyed* —
they pass against a section the model ignores completely. The headline rule is
the one thing here that is mechanically checkable, so `evals/graders.py` gets
a grader over recorded output: **the last block of a reply is not
`**Grimoire:**`** (inverted under `wrap`).

This is the `length_drift` precedent. That module exists because the codebase
does not trust the prompt to hold a word budget — it measures the transcript
and pushes back. Turn scope gets the same posture for the half of it that can
be measured. Whether "advance one beat" is obeyed remains a question only
`evals/run.py --live` and play can answer.

## Consequences for the existing harnesses

- **`evals/cases.py`** requires prompt sections verbatim (see the
  `response_budget.j2` case). All three new variants get the same treatment.
- **`frozen_campaign/snapshot.json`** captures `build_messages`
  (`sweep.py:197`), so it changes and is regenerated deliberately.
- **`scripts/verify_templates.py`** — builders and templates agree
  byte-for-byte.
- **`templates/README.md`** — section list and section-var list; the
  `player_names` move out of `response_format`.
- **Lint baselines** — `make baseline`, committed with the change.

## Known interactions, accepted

- **`scene_break` will refuse more often.** Its scoring is count-based, so
  breaks are still *proposed* on schedule, but the confirming call asks "has
  the beat resolved?" of a transcript now instructed never to resolve one.
  Expect more "still mid-beat" verdicts and some wasted calls, with `/end`
  becoming the ordinary way a scene ends. That is the intended shape of the
  feature, not a regression — but it is a real change in how the break flow
  behaves and should be watched in play. **The first thing to reach for if it
  does cost calls** is letting `/end` pre-answer the judge rather than
  loosening `turn_scope`; the flag already carries the player's verdict.
- **Replay.** `post_replay_turn` and `store/alternates.py` reconstruct turns.
  With a scene-level boolean there are no indices to keep in step, so replay
  simply sees whatever the flag currently says.

## Out of scope

- **`/end` does not change the response budget.** A wrap-up under `terse`
  (150 words, 3 blocks) may read clipped. Deliberately not pre-empted: the
  budget is a target rather than a cap, and coupling a pacing flag to the
  length cascade would undo the "no knob" decision.
- **No command framework.** One token, one flag, one branch.
- **`opener_shape.j2` is unchanged.**
- **No adaptive PC-authorship corrective** in `post_history.j2`. Detecting
  "wrote the PC" in prose is materially harder than counting words; the
  section gets a chance to work first.
