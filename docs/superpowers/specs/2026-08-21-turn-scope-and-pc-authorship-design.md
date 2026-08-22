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
- **Input typed-only, output visible.** No composer control — `/end` is a
  documented power-user gesture. But once set the flag is sticky and survives
  re-rolls, so it gets a pending indicator and a one-click cancel. The
  asymmetry is deliberate: discovering a command is a one-time cost, while
  invisible state that changes generation is a recurring one.

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
2. **The PC boundary is absolute — and is enforced, not merely asserted.** It
   is the one rule in the corpus about whose words these are rather than what
   they sound like, and a style that wants the narrator inside the PC's head
   is asking for something the player owns.

   Asserting this from `player_character` alone would not survive contact with
   the architecture. `assemble.py:658-667` builds the list as system prompt →
   **the whole transcript** → `post_history` → generation, so a character
   card's `post_history_instructions` sits in a system message *after* every
   section this spec adds and after the entire scene history. That slot exists
   precisely to outrank — `post_history.j2` calls itself *"the last message
   before generation, and so the closest available push-back"*. A card saying
   "describe how {{user}} reacts" would win on position against a rule a
   thousand tokens upstream.

   **And `post_history` is not the last word either.** `compose_turn` appends
   its `appended` messages *after* it (`assemble.py:667`) — the roll-result
   block, regenerate guidance, and the user-authored on-roll and check rule
   documents those blocks carry. On the roll-continuation and regenerate
   paths, all of that sits closer to generation than a boundary placed in
   `post_history`, so the positional argument this spec made for that slot
   does not hold there. An earlier draft asserted otherwise from reading only
   the first half of that function.

   So the requirement is stated by position, not by slot: **the PC boundary is
   the final system instruction on every composition path**, emitted after
   `appended` rather than before it. `post_history` remains where it renders
   for the ordinary chat turn, which has no appended messages; the paths that
   do get it last.

   **The opener is the third such path, and it was missed twice.**
   `compose_opener` appends `opener_shape.j2` after `post_history`
   (`assemble.py:76`) precisely so the shape rules "outrank everything above" —
   and that template carries no PC guard whatsoever. The opener path's only
   boundary text is `opener_instruction/standard.j2`'s *"Do not speak or act
   for the player"*, which sits near the top of the system prompt and is the
   weak one-liner this spec exists to replace. So an opener currently ends on
   an instruction that says nothing about wants, conclusions, judgments or
   movement. The boundary is emitted after the shape rules there too.

   One mechanical consequence: `compose_opener` renders both trailing messages
   before packing *"so their tokens can be reserved: neither is droppable, so
   neither may go uncounted"*. A third non-droppable trailing message joins
   that reserve, or the budget silently under-counts the opener.

   It is also made non-removable in the layout editor — see §6.
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
- No prose style relaxes this. A style sets how the writing sounds, not how
  much story one reply covers.
```

The last bullet is not decoration. The Precedence section says these
relationships are *stated in the blocks themselves*, and without it nothing in
the prompt tells the model that turn scope outranks a style asking for a
resolved vignette. Section order cannot carry it either: `turn_scope` sits
after `prose_style` only by default, and `layout.py` lets a user reorder
sections freely, so proximity is not a mechanism. The same line appears in the
`pcless` variant.

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
- **Add**, gated on `not wrap`, `not opener`, **and at least one NPC being in
  the scene**: *"The last speaker block is a character's, never
  `**Grimoire:**`."*

  **The NPC gate is not defensive, it is a correctness fix.** A non-opener
  scene may legitimately have zero NPCs — `backend/tests/test_context.py:466`
  is exactly that shape, and its comment says so ("no NPCs in scene"), while
  `SceneConfirmForm.test.tsx:215` exercises the empty-cast form. In such a
  scene the only character present is the player's, whom §2 forbids the model
  to write. Ungated, the two rules together leave narration as the only
  possible output *and* forbid it: an unsatisfiable prompt, whose likeliest
  escapes are inventing an NPC or writing the PC — the second being the exact
  failure this spec exists to stop. The grader takes the same context and
  skips the check for a no-NPC turn.

  **Scoped to speaker blocks, not to "the reply", and that wording is
  load-bearing** — three instructions in this corpus describe how a reply
  ends, and a rule phrased as "the reply does not end on…" collides with two
  of them:

  - `mechanics_response_format.j2` says a roll fence ends the turn — *"then
    STOP writing immediately after the closing fence"*. Live on every check.
  - `transient_tracker.j2` asks for a fenced `state` block and says *"Write
    nothing after it."* Off by default (`turnstate_depth: 0`), live when on.

  The tracker case is genuinely reconciled by talking about speaker blocks:
  `_persist_reply` strips it before the reply is split into posts, so it is
  not one.

  **The roll case is not, and needs an outright exception.**
  `mechanics_response_format.j2` asks for the fence *"mid-narration, at the
  moment of the attempt, then STOP writing immediately after the closing
  fence"* — so in a compliant roll request the enclosing speaker block **is**
  `**Grimoire:**`, and the reply ends there by design. Scoping to speaker
  blocks does not help, because narration is the last speaker block. Worse,
  when the character attempting the check is the PC, §2 leaves no permissible
  character block at all.

  So the rule carries an explicit exemption — it does not apply to a reply
  that ends in a roll fence — and **the grader takes the same exemption**,
  skipping any reply containing one. Without it the grader would reject
  correct roll requests, which the existing roll fixture demonstrates is the
  normal shape. Fence detection reuses `store/fence.py`'s `OPENER`, as
  `length_drift` already does, rather than a second copy of the grammar.

  **`transient_tracker.j2` still needs one word changed.** It currently opens
  *"After the last line of narration, and only there"* — an instruction that
  presumes the reply ends in narration, which is the thing this rule now
  forbids. It becomes **"after the final speaker block"** — not "after the last
  character block", which would have no valid placement at all in the three
  cases this spec creates where the last block is legitimately narration: a
  wrap reply, a roll request, and a zero-NPC scene. Naming the speaker block
  keeps one wording valid under every gate.

  The opener gate matters despite `response_format` having no
  `except_opener`. `opener_shape.j2` normally puts character blocks last, so
  the clause is usually redundant there — but it falls back to a generic
  marker instruction when `npc_names` is empty, and an opener with no NPCs
  present is entirely `**Grimoire:**`. Ungated, the prompt would contain a
  rule its own final message orders the model to break.

  `wrap` and `opener` therefore both become vars of this template. **Both are
  already available** — an earlier draft of this spec claimed `opener` was
  only a function parameter and needed injecting; that was wrong.
  `_render_sections` builds its render dict as
  `data = {**a["data"], "opener": opener}` (`assemble.py:561`), and
  `verify_templates.py` mirrors it with `{**gather(...), "opener": True}`
  (`:917`). Both sides already carry it; no plumbing is needed, only use.

  Both must be passed: `verify_templates.py` renders with `StrictUndefined`,
  so a missing var is a hard failure rather than a silent blank.

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
| a turn errors | **stays `pending`** — see below |
| `delete_from` (rewind) | cleared |

Leaving it set through the reply is what makes regenerate work — the durability
requirement that sent this to frontmatter in the first place. `post_regenerate`
parks the trailing reply and streams a fresh one without appending a user post,
and `post_retry` likewise; the still-set flag applies, so both re-roll a
wrap-up rather than silently reverting to a mid-scene reply. It clears on the
next ordinary send, which is the player saying they are still playing.

**The clear must also run on the ephemeral path — twice over, for two
different reasons.**

*On the send.* An empty "next NPC round" send carries no `/end` and so must
clear the flag, but it takes the `ephemeral` branch, which today performs no
scene write of its own. Clearing only where a post is appended would leave a
wrapped scene wrapping every subsequent NPC round. The clear hangs off the
send, not off the append, inside the campaign-lock hold at `:492`.

*On failure the state is KEPT, not cleared.* An earlier draft cleared it, which
contradicted this spec's own promise that `post_retry` reproduces the wrap:
retry has no other link to the failed turn, so clearing on failure means
pressing Retry after an LLM error silently composes an ordinary continuation.
The state stays `pending` across a failed generation and is cleared by the
next ordinary send, exactly as it would have been anyway. What failure cleanup
still owes is the *post* rollback, and that hook has a placement problem:

*Where the cleanup runs.* It cannot be assigned to `_take_the_post_back`
alone. That undo is wired only into the persisted-post branch
(`undo_user_post=` at `:571-574`); the ephemeral branch (`:526-548`) builds
its stream with no undo hook at all. So a bare `/end` — which scrubs to `""`
and is therefore *always* ephemeral — and every pcless generation would fail
with `wrap_next` still set, leaving the indicator claiming a wrap that
produced nothing. The failure clear is therefore its own small hook — and it is invoked at
**route level, around the whole post-mutation setup span**, not from inside
the two streams. A hook reachable only once a stream exists still misses every
way setup can fail after `wrap_next` is set and before there is a stream to
carry an `on_error`: `compose_turn` / `compose_director_turn` raising, a
template failing to render, and `_chat_stream`'s synchronous claim, which the
route's own comment notes "claims the turn under the campaign lock before it
returns, so a contended campaign raises `StoreBusy` at this line and the route
answers 409 having sent nothing". But a route-level `try` is not sufficient
either, and for the opposite reason: an upstream `LLMError` is caught *inside*
the async iterators (`streaming.py:466`, `:918`) and emitted as an error frame
after the route has already returned. So the same cleanup is invoked from
both places — the outer route span for composition and claim failures, and
both streams' error paths for the asynchronous ones. The tests cover a
composition failure, a claim failure and a streaming failure.

One accepted loss, stated rather than solved: a rewind clears the state
unconditionally. That fails toward not-wrapping, which is the recoverable
direction — the player retypes three characters. A turn that errors does
**not** clear it; see the failure rule above, which the retry promise depends
on.

**Reading** — through a **public** accessor on `scenes/read.py`, following
`get_rolling_summary` / `scene_break_fields`:

```python
def wrap_state(meta: dict) -> str: ...            # "" | "pending" | "consumed"
def get_wrap_state(cid: str, sid: str) -> str:    # read fresh off disk
```

**State-valued, not boolean**, with the boolean derived at the one place that
wants one: composition wraps whenever the state is non-empty
(`wrap = bool(state)`), since a `consumed` wrap must still wrap so that retry
and regenerate reproduce the closing reply. A `bool` accessor could not carry
the `pending`/`consumed` distinction the indicator needs (§5), and a
string-valued one would clash with the `wrap: bool | None` composition
override — so the accessor is the state and the override stays the boolean.

Public rather than a frontmatter poke inside `_assemble`, for three consumers
that all need it: the assembler, the `DELETE .../wrap` route, and the scene
payload feeding the indicator. It is also **required** — `verify_templates.py`
rebuilds the template data *"from public store reads"* in its `gather()`
mirror, so a value only `_assemble` can compute cannot be verified at all.
Coercion follows the module's existing posture: anything that isn't the stored
truthy value reads as `False`, because a hand-edited frontmatter must cost a
non-wrapping turn rather than a 500 on the play path.

**One invariant, easy to break by reading the spec correctly:** the clear
hangs off `delete_from` *specifically*, never off transcript removal in
general. `post_regenerate` removes the outgoing reply with
`remove_trailing_assistant_run` (`scenes.py:783`), which is the only reason
"rewind clears" and "regenerate stays set" can both be true. Hook the clear
into a shared removal helper and durability dies silently — every test still
passes except the regenerate-still-wraps case, which is therefore the named
guard for this invariant.

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

### 5. Surfacing and cancelling a pending wrap

**Input stays typed-only.** No composer control, no chip, no typed field on
`ChatTurn` — `/end` is a power-user gesture, documented in `README.md` and the
release note. This preserves the "backend parses off `ChatTurn.content`"
decision intact.

**Output is visible, because the flag is sticky and invisible state that
changes generation is how "why did it do that" happens.** `wrap_next` survives
regenerate and retry by design, so a player can re-roll three times and get
three closing posts with nothing on screen explaining why.

- **`wrap_next` is two-valued, not a bool**: `pending` (a `/end` is waiting to
  be answered) and `consumed` (its wrap reply has landed, and the flag is
  deliberately still set so retry and regenerate reproduce it). One boolean
  cannot describe both, and the difference is user-visible: after a wrap reply
  succeeds, *"the next reply will close this scene"* is simply false — the
  next ordinary send clears the flag and resumes play. Only a re-roll still
  closes. The state is stored rather than derived, because after a bare
  `/end` — which appends no post — the transcript's last message cannot tell
  the two apart.
- **`pending` → `consumed` happens when the scene is actually closed, which is
  not always the first persistence.** A wrap turn may stop at a roll fence: its
  pre-fence narration persists, but nothing has been closed, and consuming
  there would have the indicator announce a closed scene mid-check. So a reply
  ending in a roll fence leaves the state `pending`, and the accepted or
  declined continuation performs the transition instead.
- **The transition is a conditional write under the finalizer's campaign
  lock** — `pending` → `consumed` only, never a blind set. The cancel route is
  deliberately unguarded and can clear the state while a turn is live; an
  unconditional write in the finalizer would resurrect a `consumed` the player
  had just cancelled. Both cases are state-machine tests: the roll-continuation
  path, and cancel-racing-finalize.
- The scene payload carries that state, read off the same frontmatter the
  assembler reads.
- `CampaignView` renders an indicator near the composer whose copy follows the
  state: *"the next reply will close this scene"* while pending, and while
  consumed, that the scene is closed and only a retry or regenerate stays in
  closing mode. Both offer cancel.
- **`DELETE /campaigns/{cid}/scenes/{sid}/wrap`** clears the flag. Without it
  the only way to clear a wrap the player no longer wants is to send an
  ordinary post, i.e. to generate a reply they don't want purely to undo a
  flag.

**The flag is snapshotted under the setup lock, and the turn composes from
the snapshot.** An earlier draft justified leaving cancel unguarded by
claiming a live turn has already assembled its prompt. That is false for the
current route ordering: `_chat_run` reserves the run (`:420-426`), *closes*
the setup lock at `:525`, and only composes afterwards (`:528` ephemeral,
`:549` ordinary); regenerate has the same shape. A cancel landing in that
window would clear `wrap_next` before `_assemble` ever read it and silently
un-wrap the reply already starting.

The fix is to read the flag inside the hold that already covers setup and
hand it to composition explicitly.

**Not through `_turn_override`,** which cannot carry it: that helper returns a
dict built from the typed `ResponseSettings` wire model
(`routes/common.py:254`), and `_assemble` feeds it to
`response_presets.resolve`, which walks only `("style_id",) + lengths.KNOBS`
(`response_presets.py:251`). A `wrap` key put there is silently dropped, and
composition would go on reading live frontmatter — the race intact and now
harder to see. That path is for response settings; this is not one.

Instead `compose_turn`, `compose_director_turn` and `_assemble` take a
separate keyword — `wrap: bool | None`, `None` meaning "read the file" — and
each route passes the value it snapshotted under its setup lock. Two
independent overrides rather than one overloaded dict, which also keeps the
response-preset cascade free of a flag that has nothing to do with presets.

**The inventory is CLAUDE.md's five, not a list grown one review at a time.**
This spec added `post_roll_proposal`, then `post_retry`, then
`post_replay_turn`, each after a reviewer named it — while CLAUDE.md has
listed all five detached-turn handlers throughout: `post_chat`, `post_retry`,
`post_regenerate`, `post_replay_turn`, `post_roll_proposal`. Every one takes
the snapshot and every one is in the race tests. `post_replay_turn` is not
hypothetical: a replay session coexists with the composer and `post_chat` has
no replay refusal, so a player can send `/end` between replay steps.

**`post_retry` specifically.** `_retry_run` closes its setup lock at `:619`
and composes at `:621-622`, the same shape as chat and regenerate, so a cancel
in that window turns an already-starting retry of a wrap reply into an
ordinary continuation. It is in the inventory and in the race tests.

**The same snapshot applies to `post_roll_proposal`.** CLAUDE.md inventories
it as one of the five scene-turn handlers that start detached runs, and a wrap
reply can legitimately stop at a roll fence with `wrap_next` still set.
Accepting or declining that proposal then builds the continuation through
`routes/mechanics.py:_continuation_messages` / `_declined_continuation_messages`,
both of which call `compose_turn` today with **no `turn=` override at all**
(`mechanics.py:98`, `:105`). Since the cancel route stays usable during the
detached run, a cancel between proposal processing and composition would
un-wrap a continuation already starting — the identical race, one route over.
Both builders take the snapshot through the same override, and the race test
is written for this path too, not only for chat and regenerate.

With that, cancelling mid-turn cleanly means "not the next one" rather than
"not this one", which is also the honest thing to show: the indicator names
the next reply explicitly — *"the next reply will close this scene"* — so a
click during a live turn is not mistaken for stopping the reply now landing.

**The cancel route is deliberately NOT `scene_busy`-guarded**, and this needs
recording in `test_scene_freeze.py` as an explicit non-door rather than an
omission — CLAUDE.md notes that file is one case per door precisely because
the guard is applied per call site. The reasoning: the freeze exists to refuse
routes that change a scene's *shape* while a turn holds it, and this changes
neither the transcript nor the cast nor the scene's identity. A live turn has
already assembled its prompt, so clearing mid-run cannot affect it — and the
post-run state (flag cleared) is exactly what a player clicking cancel is
asking for. It still takes the campaign lock like every other frontmatter
write.

### 6. Making the PC boundary enforceable

Two changes, both following doctrine the codebase already states.

**Its own trailing message — not a line inside `post_history.j2`.** An
earlier draft put the sentence in that template, which cannot satisfy the
final-position requirement above. `a["post_history"]` is one rendered string
appended as a single system message, so a composer can only move the *whole*
block: relocating it after `appended` would drag the card instructions, the
voice corrective and the length corrective past the roll-result and
regeneration guidance too — changing where three unrelated counterweights
land — while leaving it in place fails the requirement outright. One template
cannot be both.

So the boundary is a **standalone trailing system message**, rendered from its
own small template and appended last on every composition path: after
`post_history`, after `appended`, and after `opener_shape.j2` on the opener.
It carries its own token reservation — `compose_opener` renders trailing
messages before packing *"so their tokens can be reserved: neither is
droppable, so neither may go uncounted"*, and this is a third such message —
and its own entry in the inspector breakdown, so the prompt log shows it
rather than hiding it inside another block.

`post_history.j2` is left exactly as it is: card instructions, then voice,
then length, ordered as its own docstring explains.

One sentence, static, no detection required:

```
<Name> is the player's. Never write what they say, do, choose, attempt, want,
intend, realize or conclude, and never presume their answer. You may write
what the world does to them, and how their body answers on its own. Stop
where their response begins.
```

**Singular and plural, mirroring `player_character.j2`** — *"`<A>` and `<B>`
are the players'…"*, with a multi-PC `post_history` test. Naming one PC here
would be worse than naming none: this is the only boundary text positioned to
override a hostile card instruction, so a second seated character left
unnamed in it is unprotected in precisely the slot that matters. An
implementation that substitutes the first name, or joins the list into the
singular sentence, produces exactly that gap.

The wording carries the **whole** distinction, not a shortened gesture at it,
and both halves matter because this is the only boundary text positioned
after the card blocks. Dropping conclusions, wants and presumed answers would
leave a card saying "describe what the player realizes" unrebutted in the one
slot that can rebut it. And an unqualified ban on "action" would contradict
§2's explicit permission for involuntary reflexes — a flinch, a caught breath
— so the ban is on volition (say, do, choose, attempt) with the sensation
permission restated alongside it.

This is **not** the adaptive corrective ruled out under *Out of scope* — that
one was rejected because detecting "wrote the PC" in prose is hard. This needs
no detection at all; it is a constant.

`post_history.j2` gains `player_names` as a var — it currently takes only
`npc_cards`, `voice_correction` and `length_correction` — and `_assemble`
already has the list to hand.

Consequence to state: the template is *"omitted entirely when all are empty"*,
so a permanent line makes it always present in any scene with a seated player,
and a scene whose cards carry no post-history instructions now sends a system
message where it previously sent none. It stays omissible in a pcless scene,
where the line renders nothing.

**Non-removable in the layout editor.** `pack.LOCK_IN` stops the *packer* from
dropping a section; it does not stop a user, because `layout.py` makes
*"order, presence, and the inspector's label"* overridable. A rule the spec
calls absolute must not be switch-off-able from a UI.

`Section` gains `removable: bool = True`; `player_character` sets it `False`
and `layout.apply` refuses to drop it.

**`apply` alone is not enough — the editor would lie.** `layout.describe`
returns only `{id, label, default_label, tier, enabled}` (`layout.py:165-167`)
and `PromptLayoutEditor.tsx:61-63` renders the include checkbox
unconditionally, with no `disabled`. Ignoring a stored omission at render time
while still offering the checkbox means a user unticks `player_character`,
saves, sees it reported as off, and gets it anyway — the UI stating the
opposite of what generation does, which is worse than either behavior alone.
So `removable` propagates through `describe`, through the API type, and into
the editor, which disables the checkbox for a non-removable row and says why.
A frontend test covers it: the row renders, its checkbox is disabled, and the
label stays editable. This is the same distinction that
module already draws for the packer tier and `except_opener` — *"the same kind
of thing rather than a taste"* — and the reasoning transfers exactly: a
control whose only function is to let the model write someone else's character
is not a preference.

**`turn_scope` stays removable — and the opt-out has to be complete to be
honest.** Pacing is a taste; someone who wants the old shape may have it.
Whose words these are is not.

But switching the section off does not by itself restore the old shape,
because the structural half of the fix lives in `response_format`, a
separately registered section. A user would have to disable the reply-format
contract as well — losing the `**<Name>:**` markers that every downstream
split depends on — just to opt out of pacing. So the closing-narration rule
renders only when `turn_scope` is enabled in the resolved layout, making the
toggle mean what it says. The PC boundary is unaffected: it is non-removable
and does not ride on this gate.

**How the gate reaches the template, because the obvious two ways both
fail.** `_render_sections` fixes its render dict at `assemble.py:561` and only
then walks `layout.apply(SECTIONS)` at `:564`, so a template cannot observe
whether a sibling survived — the data is already frozen. And reaching for the
existing `_section_on` helper would be a *second* layout read: its own
docstring concedes it "costs one `read_config` and at most one small file
read", so under a concurrent layout save the two reads can disagree and the
rule can render for a `turn_scope` that did not.

The fix is to resolve once: `_render_sections` binds
`sections = layout.apply(SECTIONS)` to a local, derives
`turn_scope_enabled` from **that exact list**, folds it into `data` beside
`opener`, and iterates the same local. One read, one truth, and
`verify_templates.py`'s mirror derives the value the same way so the
comparison stays honest.

No migration is needed for either: `layout.py`'s upgrade rule already inserts
a catalog section a stored layout never mentioned *"after its nearest
preceding catalog neighbour that survived the merge"*, so existing custom
layouts receive both new sections in the right place.

### 7. Section registration

Two `pack.LOCK_IN` entries in `context.assemble.SECTIONS`, after
`natural_prose`: `turn_scope` (`except_opener=True`), then `player_character`.

Lock-in because a section the packer may drop under budget pressure is a rule
that silently stops applying in exactly the long, mature scenes where pacing
and PC-authorship discipline matter most.

**The cost, stated rather than assumed:** `natural_prose.j2` is 468 words.
These two add roughly 410 more, so the standing always-on instruction overhead
close to doubles — ~550 tokens on every chat, retry, regenerate and director
turn, undroppable. An **opener** pays only for `player_character` (~230), since
`turn_scope` carries `except_opener`. Add one short line to `post_history.j2`
(§6) on every turn with a seated player. That is the price of the feature, and
it is worth naming here rather than discovering it in a token breakdown.

## Shipping this in two parts

At ~680 lines this is more than one implementation plan should carry, and it
splits on a clean seam — the fix for the two complaints does not need `/end`
at all.

**Phase 1 — the prompt.** `turn_scope` (standard + pcless), `player_character`,
the `response_format` edits, the `transient_tracker` rewording, the
`post_history` line, `Section.removable` and the layout change, the precedence
hierarchy, the behavioral grader, and the eval/cassette/snapshot fallout. No
new routes and no new stored state — but it **must still pass `wrap=False`**
to `response_format.j2`. Phase 1 edits that template to branch on `wrap`
while Phase 2 supplies the accessor and the override, and the Jinja env runs
with `StrictUndefined`, so a Phase 1 that renders without the variable fails
outright. A fixed `False` during Phase 1, replaced by the real snapshot in
Phase 2. It **does** carry frontend work — §6's
`removable` has to reach `layout.describe`, the API type and
`PromptLayoutEditor`, with a test, or Phase 1 ships the misleading checkbox it
exists to remove. This alone answers both problems
this spec opened with, and it is what should go to `writing-plans` first.

**Phase 2 — `/end`.** `store/commands.py`, the `wrap_next` lifecycle and its
public reader, `turn_scope`'s `wrap` variant, `wrap_note.j2`, the wrap gates in
`response_format`, `DELETE .../wrap`, and the indicator.

Phase 2 depends on Phase 1 — the `wrap` variant is an override of a section
that has to exist first — so the order is forced rather than chosen. Splitting
also means Phase 1's prompt changes reach play (the only place "does it
actually pace better" gets answered) before the command surface is built on
top of an unvalidated assumption.

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
- **Clearing** — an ordinary send clears it and a rewind clears it. An
  errored turn does **not**: the state stays `pending` so `post_retry` can
  reproduce the wrap, and a test asserts exactly that.
- **Rendering** — each section renders; `player_character` renders empty
  pcless and renders plural with two seated players; `turn_scope` is absent
  from an opener; `response_format` omits the closing-narration rule both
  under `wrap` and in an opener, and carries it otherwise.
- **Variant selection** — `standard` / `pcless` / `wrap`, with `wrap` winning
  over `pcless` when both hold (a director scene that was asked to close).
- **The note agrees with the section** — a bare `/end` produces
  `wrap_note.j2`, not "Continue the scene."; a `/end` sent alongside prose
  keeps the prose.
- **Registration** — both appear in the assembled prompt and the inspector
  breakdown; neither is droppable.
- **Cancel** — `DELETE .../wrap` clears the flag; the next reply does not
  wrap; the route is reachable while a run is live (the non-door case, pinned
  in `test_scene_freeze.py` so a later inventory does not "fix" it).
- **Frontend** — the indicator appears when `wrap_next` is set and after a
  regenerate, and disappears on cancel and on an ordinary send.
- **The durability invariant** — regenerate still wraps *after* the clear is
  wired into `delete_from`. This is the named guard for §4's invariant: it is
  the only test that fails if the clear is moved to a shared removal helper.
- **Non-removability** — a stored layout that omits `player_character` still
  renders it; one that omits `turn_scope` does not. Plus the upgrade case: a
  layout saved before this change receives both sections.
- **`post_history`** — the PC line renders after the card blocks and before
  the voice corrective; renders nothing in a pcless scene, leaving
  `post_history` omissible there.
- **The frozen campaign reads as unwrapped.** Its scenes were written before
  `wrap_next` existed, so `get_wrap` must answer `False` for frontmatter that
  has never heard of the key. This is precisely what that fixture is for — the
  only store in the repo today's code did not write — so it is worth an
  explicit case rather than leaving it to the snapshot diff.
- **Atomicity** — the set/clear goes through `store.atomic` like every other
  scene write; `test_atomic_guard.py` fails it otherwise.
- **Frontend fixtures** — the scene payload gains a field, so the shared
  `testkit/campaignHarness.tsx` fixtures grow it rather than each suite
  patching its own copy (CLAUDE.md: shared scaffolding lives in `testkit/`,
  which coverage excludes).

### The behavioral grader

Verbatim-presence evals prove the instruction is *present*, not *obeyed* —
they pass against a section the model ignores completely. The headline rule is
the one thing here that is mechanically checkable, so `evals/graders.py` gets
a grader over recorded output: **the last speaker block of a reply is a
character's, not `**Grimoire:**`**. It ships unconditional in Phase 1; the
`wrap` inversion arrives with the flag in Phase 2, since there is nothing to
invert on before then.

This is the `length_drift` precedent. That module exists because the codebase
does not trust the prompt to hold a word budget — it measures the transcript
and pushes back. Turn scope gets the same posture for the half of it that can
be measured. Whether "advance one beat" is obeyed remains a question only
`evals/run.py --live` and play can answer.

## Consequences for the existing harnesses

- **`evals/cases.py` — an existing case breaks, not just new ones.**
  `grade_scene_length` already renders `response_format.j2` verbatim and calls
  it as `grade_prompt_section(..., player_names=sorted(ctx["players"]))`
  (`cases.py:203`). §3 removes `player_names` from that template and adds
  `wrap` and `opener`, so under `StrictUndefined` the existing call raises on
  the two vars it does not pass. That call has to be updated in the same
  change, and the comment above it — *"the marker convention the whole length
  measurement rests on"* — is still true and still worth keeping. The three
  new variants then get the same verbatim treatment alongside it.
- **Cassette matchers (`backend/tests/llm_fakes.py`).** A cassette answers by
  what the request *looks like*, matching `system_contains` over the system
  messages (`llm_fakes.py:84`, `:110`), and *"a request matching no cassette
  entry raises rather than defaulting."* This change edits `response_format`'s
  text, adds two system sections, and — via §6 — makes `post_history` a
  system message present on every turn with a seated player, in scenes that
  previously sent none. Any matcher keyed on text this change removes (most
  likely the old `Never write dialogue or actions for:` clause) stops
  matching. `test_llm_fakes.py` renders every real prompt template precisely
  so this surfaces here rather than silently everywhere else; expect to audit
  the fixtures under `backend/tests/fixtures/llm/`.
- **`frozen_campaign/snapshot.json`** captures `build_messages`
  (`sweep.py:197`), so it changes and is regenerated deliberately.
- **`scripts/verify_templates.py`** — builders and templates agree
  byte-for-byte.
- **`templates/README.md`** — section list and section-var list; the
  `player_names` move out of `response_format`.
- **`verify_templates.py`** — its `gather()` mirror must reproduce `wrap` from
  public store reads, which is what `scenes.read.get_wrap` exists for;
  `opener` it already supplies (`:917`).

  **And the comparison must not go vacuous.** That file refuses this
  explicitly twice — *"a byte-for-byte check over an empty section proves
  nothing"* before asserting the fixture exercises the archive, and again for
  the voice corrective, *"without an anchor the corrective renders "", and
  comparing "" to "" passes while proving nothing"*. Both new sections have
  branches with exactly that failure mode: `player_character` renders empty in
  the pcless pass, and `turn_scope`'s `wrap` variant never renders at all
  unless the fixture sets the flag. Each needs a positive assertion in the
  same idiom, or the mirror will pass while covering nothing.
- **`store/locks.py`** — `store/commands.py` is deliberately **not**
  classified. An earlier draft required an `OUTSIDE_DOMAIN` entry; that would
  have broken the build. `test_modules_declared_outside_are_really_outside`
  flags any declared module whose public mutators all serialize, and a pure
  parser has no mutators at all, so it reports as stale. The registry is for
  campaign-scoped mutators, not for every new module under `store/`. The
  set/clear mutator lands in
  `scenes/write.py`, a `DOMAIN_MODULES` module, so it takes
  `locks.campaign_lock(cid)` — reentrant, therefore free under the route's
  existing hold — rather than a marker.
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

## Open at implementation time

Real, verified, and deliberately not resolved in this document — each is a
mechanism detail that the implementation will settle against actual code
faster than another round of prose. Listed so none is lost:

- **`describe` should report the *effective* enabled state** for a
  non-removable section, not the stored one. `_ordered` reads `enabled`
  straight from the stored entry, so a layout written by an older client or a
  direct API caller with `player_character.enabled=false` renders in
  generation but comes back as an unchecked, disabled row.
- **`consumed` must return to `pending` when a regeneration stops at a roll
  fence**, or the indicator claims a closed scene mid-check.
- **`wrap_note.j2` must be selected from the captured snapshot**, not a fresh
  read — otherwise a cancel between snapshot and note selection restores the
  "Continue the scene." contradiction the note exists to remove.
- **`/end` sent with prose is lost across a failed-turn retry.** When the
  turn fails, `_take_the_post_back` removes the persisted post; `/retry` then
  composes with the wrap state but without the prose that accompanied it, so
  it closes the scene without the player's instruction. Either the scrubbed
  prose needs a durable retry carrier, or that failure should require
  resending from the restored composer rather than offering Retry.
- **Phase 2's documentation scope is unstated.** `/end` has no composer
  control by design, so discovery rests entirely on docs, and the phase list
  names neither the README change nor a concrete release-note artifact — the
  repo appears to have no changelog file for one to live in.
- **A wrap must stay `pending` across *chained* roll proposals.**
  `_continuation_stream` supports a continuation that itself ends in another
  fence (`streaming.py:879-886`), so consuming after the first continuation
  announces a closed scene while a second check is still pending. Every
  continuation needs the same fence test.
- **`/end` alongside prose is inert when `turn_scope` is disabled**: the wrap
  variant is gone, the closing rule is gated off with it, and the prose
  suppresses `wrap_note.j2`. The wrap instruction probably needs to be
  independent of the pacing opt-out.

## Out of scope

- **`/end` does not change the response budget.** A wrap-up under `terse`
  (150 words, 3 blocks) may read clipped. Deliberately not pre-empted: the
  budget is a target rather than a cap, and coupling a pacing flag to the
  length cascade would undo the "no knob" decision.
- **No command framework.** One token, one flag, one branch.
- **`opener_shape.j2` is unchanged.**
- **No *adaptive* PC-authorship corrective.** §6 puts a **static** line in
  `post_history.j2`; what stays out of scope is the measured, conditional kind
  that `voice_correction` and `length_correction` are — one that detects
  violations in recent turns and escalates. Detecting "wrote the PC" in prose
  is materially harder than counting words, and the constant line costs
  nothing to be right. If the boundary still leaks in play, that detector is
  the next thing to build, not more wording.
