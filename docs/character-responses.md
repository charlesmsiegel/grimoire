# Character responses

Individual character responses are the default. Each generation receives the
current speaker's voice, examples and knowledge. Other cast members contribute
their names and conversation that the speaker witnessed. Grimoire retains the
campaign context needed for narration and mechanics.

An established NPC owns their dialogue, actions, decisions and physical reactions,
including silent or involuntary ones. Grimoire handles general scene information,
independent environmental events, and the initial actions and speech of genuinely
new characters. It can acknowledge facts already written and describe their effects
on the environment; it should not finish or expand an established NPC's turn.
Speaker selection and handoffs follow the same boundary. An unused narration slot
does not call for a recap or atmospheric closing paragraph, and cannot give an NPC
a second automatic turn through the narrator. This is prompt guidance; the engine
checks speaker references and turn eligibility, not the semantics of arbitrary prose.

A player post can receive a short sequence of responses. Within one round each
NPC can respond once automatically; Grimoire also has one narration slot. By
default the response proposes the next speaker, and the application checks
eligibility before starting another call. Missing or invalid routing metadata
ends the sequence. A scene can instead fix the order itself, and can opt in to
further rounds after the first; see [Speaker order](#speaker-order).

Addressing one NPC determines who starts, not who alone may speak. Their handoff
can nominate another NPC with a relevant response, without writing that person's
contribution. The prompt lists only unused slots, including the narrator when
available. An explicit Continue or reply-as request with an empty composer lists no
successor slots.
An unanswered question need not prevent another NPC from reacting, provided their
reaction does not decide or act for the player.

## During play

Individual responses use prose with quoted speech; the application supplies the
speaker name. Combined mode retains labelled script blocks. Continue stays visible
but disabled while the application selects speakers and generates their replies.
As each speaker starts, their contribution shows **Name is responding…**, even
before the first words arrive. Stop remains available alongside Continue. During an
automatic chain the indicator also reads **Round n/total**.

- **Continue** selects one additional response, then gives control back.
- **Reply-as chips** sit in a row above the composer: **Grimoire** and one chip
  per present NPC. One tap asks that character to respond. With an empty
  composer that is exactly one additional contribution; with text, the text is
  posted and that character leads the round, which then continues by the scene's
  order with everyone else, except in Manual, where that character's reply is
  the only one. Both Continue and an empty-composer chip permit a character who
  already responded to speak again.
- **Response actions** offers individual reroll, delete and saved variants.
  A reroll uses the original saved context and does not start another speaker.
  Later replies remain in place and show **Earlier context changed**.
- **Replay from here** explicitly regenerates onward when later replies need
  revisiting. Review the existing replay controls before accepting its result.
- **Stop** prevents further responses. Incomplete output cannot authorize the
  next speaker. Retry after a Stop finishes only the interrupted reply and
  follows no handoff, in every mode. That includes a default Directed scene, where
  Retry used to carry on to the speaker the finished reply handed to. A Stop
  before any reply started leaves nothing to retry; use Continue instead.

Older responses have no historical prompt snapshot. They remain deletable, but
use explicit replay rather than claiming a historical reroll from current
character knowledge.

For older cast records without presence history, the first new round establishes
a conservative observation boundary. Characters retain what they witness from
that point across later player rounds, including when they remain silent.

Dice pauses keep the current character's contribution. Resolving or declining the
proposal resumes that character, then checks any new handoff. Deleting or rerolling
prose at or before an applied roll is refused: changing words cannot undo the
recorded dice or mechanical effects.

## Speaker order

Each scene has a **Speaker order** setting, opened from the **Order: …** chip
beside the response-length chip in the composer bar. It can be changed while a
response is being generated; the change applies at the next speaker choice or
round, never in the middle of a contribution. A scene that has never stored
these settings behaves as it did before they existed.

### Modes

- **Directed** (default) keeps the model-directed flow described above: the
  selector picks the first speaker and each contribution's handoff picks the next.
  Continue asks the selector.
- **Manual** generates no reply to a player post. Choose who answers with a
  reply-as chip. Manual never continues automatically. Continue asks the
  selector, as in Directed.
- **List** has every available character speak once per round, in the order set
  in the panel (up and down buttons). A present character the list omits speaks
  after the listed ones, in cast order, so a newcomer is never excluded. Grimoire
  speaks at its list position if it has one; the panel lists it last, with its
  own up and down buttons, and places it in the order once it is moved. Until
  then it speaks only if no character is available. Continue picks the next
  available character after the most recent speaker, wrapping around.
- **Natural** has characters the post names speak first, in the order they are
  named; every other available character then joins at random, in shuffled order,
  according to their talkativeness. If nobody is named and nobody joins, the
  character who has been silent longest speaks. Continue applies the same rule to
  the most recent contribution's text and never picks that contribution's own
  speaker; when nobody else is available it falls back to the selector. A
  character naming themselves does not count as being named, and a follow-on
  round never opens with the character who spoke last.

In List and Natural the plan alone decides who speaks: the prompt offers no
handoff candidates and a handoff block in a contribution is ignored. A director
note always goes through the selector in every mode, because the note may itself
say who should act. If no character is available in any mode but Manual,
Grimoire narrates.

### Talkativeness

Each character can have a talkativeness from 0 to 100, set with a slider shown in
Directed and Natural. It is the chance, as a percentage, that the character is
asked to speak in a round when the post does not name them.

- In **Natural** an unset talkativeness counts as 50.
- In **Directed** an unset talkativeness counts as 100, so only a character the
  player has given a value is ever filtered out. Saving some other setting, such
  as a sit-out, never starts dropping the rest of the cast. Directed applies the
  roll to the selector's candidate list, so the selector and the handoffs choose
  only among characters who passed. A character the post names always passes. If
  the roll removes everyone, the available character with the highest
  talkativeness is kept.

The roll is made once per round and stored with it, so Retry and a dice resume
continue the same plan rather than rolling again. List and Manual ignore
talkativeness.

### Sit out

A character can be set to **sit out**. They are skipped by every automatic choice
in every mode. Sitting out does not remove them from the scene, and they keep
witnessing it. A sitting-out character's reply-as chip is dimmed but still works:
tapping a name is an explicit request and overrides the setting. Grimoire cannot
sit out. If a planned speaker starts sitting out mid-round they are skipped and the
next planned speaker goes instead; in Directed, a handoff to them is treated as
ineligible.

### Automatic rounds

**Automatic rounds** (0 to 5, default 0) lets a player post run on past the first
round without further input. After a round finishes and rounds remain, a
follow-on round starts, planned by the scene's order with the latest contribution
as its trigger. Every NPC is eligible again, so a character may speak once per
round. The whole chain runs on the server inside the same run as the post, so a
locked phone does not interrupt it, and the rolling summary and scene-break
checks run once, after the chain ends. A character's own name in the
contribution that starts a follow-on round does not count as naming themselves.
Only a player post (or a reply-as chip with text) starts a chain; Continue and an
empty-composer chip produce exactly one contribution. The setting is read again
before each follow-on round: lowering it mid-chain stops the chain once it has run
that many rounds (setting it to 0 starts no further round), while raising it never
extends a chain past the rounds its post started with.

In Directed, a handoff to a character who already spoke this round ends the round
and starts the next one with that character leading. With no rounds remaining
that handoff is rejected, as it is without automatic rounds.

A chain ends early when:

- **Stop** is pressed. Stop ends the whole chain, discards the rest of the plan
  and marks the round stopped. Retry then finishes only the interrupted
  contribution and generates nothing after it, in any mode.
- A contribution pauses for a dice roll. The resumed round continues the chain
  with the rounds it had left.
- A contribution is empty, incomplete or fails with an error, or the round
  generated nothing.
- A contribution speaks for another actor (writes another character's or the
  player's lines). That ends the chain in every mode.
- In Directed, a contribution's handoff is missing, invalid or names an
  ineligible character. A handoff to the current speaker is never offered and
  never accepted, even with rounds remaining. List and Natural ignore the handoff,
  so a missing one does not end their chain; the issue is still recorded on the
  reply.
- The scene is in Manual mode.
- In Directed, a contribution or the selector explicitly hands control back
  (`next: null`).
- A speaker leaves the scene mid-round.

### Where the settings live

Settings are stored per scene, as one `group_play` field (compact JSON) in the
scene file's frontmatter, so renaming a scene carries them with it. Characters
are keyed by actor reference (`kind:id`), and a character who leaves and returns
keeps their settings. They are read and written with
`GET` and `PUT /api/campaigns/{cid}/scenes/{sid}/group`. `PUT` replaces the whole
object and refuses out-of-range values with `400`. It is not refused while a
response is running, since it changes no transcript. Reading is lenient: a missing
or malformed field, or a bad value for one key, falls back to that key's default.
Defaults are `directed`, an empty order list, nobody sitting out, no talkativeness
set and `0` automatic rounds. Settings are per scene only; there is no
card-level or campaign-level default.

## Developing a voice

Voice anchors describe tendencies that respond to the situation. Dialogue examples
supply evidence of rhythm and expression; explicit authored constraints and
established facts remain authoritative. Review both the character's **Voice
anchor** and the card's **Example dialogue** when they disagree. The context
inspector shows which evidence was included or dropped.

A completed Grimoire response offers **Create character from this passage**.
Choose the person and relevant passage, review the description and attributed
dialogue, and create a campaign character or attach the evidence to an existing
one. A person who has not spoken starts without dialogue examples. This is an
optional action during play and adds no mandatory closeout generation.

## Comparing the modes

In Settings, **Scene responses** can be changed to **Combined scene
response**. This keeps the shared scene writer available for comparison while
retaining the updated continuity and voice prompts.

Model guidance remains automatic: the actual dispatched model selects an editable
profile in `templates/scene/model_guidance/`. Unknown models receive no extra
profile. Historical rerolls use frozen prompt variants; a model without a saved
historical variant uses the frozen unprofiled context. Later prompt edits apply
to new responses. To change the formatting of a historical reroll, supply a
response steer such as "Use prose with double quotation marks around speech."

The automated checks establish routing, persistence, privacy boundaries and UI
behavior. Voice naturalness still needs comparison during play; no automated
result here establishes that one model or mode writes better dialogue.


Build and verification details are recorded in
[the branch validation report](superpowers/validation/2026-09-11-character-turns.md).
Restart Grimoire to load changed backend code. To back out of the code changes,
switch to `main` and restart; campaign history is stored separately from Git.


## Thinking display and GLM effort

New per-character responses show a collapsed **Thinking** section when the
provider returns reasoning. Expanding it shows plain text inside
`<thinking>...</thinking>` tags; HTML, roll fences, and handoff text inside it
are never executed. Thinking updates while the response streams. Retry/fallback
attempts clear the previous attempt's thinking. Stop and roll boundaries close
the underlying stream.

Reasoning is stored with each response variant in the response ledger. Only
an opaque variant pointer enters transcript metadata; the reasoning itself
is never added to the scene text or subsequent model prompts. Saved thinking
loads when expanded and follows the selected variant. Roll continuations
retain earlier thinking and replace the resumed part when it is regenerated.
This display is independent of Debug logging. Old responses without stored
reasoning have no panel; raw diagnostic captures are not backfilled into them.
The display applies to the character-response engine, including narrator
responses and individual rerolls. Background selectors/drafts and the legacy
combined response engine still use their existing display paths.

For a Custom (OpenAI-compatible) provider serving `glm-5.3` or
`glm-5.3-flash`, the reasoning effort is a sampler preset's **Reasoning effort**
(Settings → Inference → Presets, `/presets`), attached to the role or route that runs the model on the
**Models** page. GLM takes Low and High from a preset; Off and Medium are not
levels it has, and the preset's controls readout says so rather than sending
them. A preset that sets no effort sends none, unless the provider kept one
from before the model-settings upgrade: the old per-connection **Reasoning
effort** (Provider default, Low, High or Max) is no longer editable, but a
value it stored is still sent until a preset's effort replaces it. The value
goes out as `reasoning_effort`; a model other than GLM is never sent the
GLM-specific setting.

[Z.ai's Chat Completion reference](https://docs.z.ai/api-reference/llm/chat-completion)
documents Max as the default, Low/High/Max as GLM 5.3's supported values, and
thinking as always enabled for this model. Effort is a qualitative control,
not a target token count. This feature does not impose an output-token cap.
