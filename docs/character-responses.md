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

A player post can receive a short sequence of responses. Each NPC can respond
once automatically; Grimoire also has one narration slot. The response proposes
the next speaker, and the application checks eligibility before starting another
call. Missing or invalid routing metadata ends the sequence.

Addressing one NPC determines who starts, not who alone may speak. Their handoff
can nominate another NPC with a relevant response, without writing that person's
contribution. The prompt lists only unused slots, including the narrator when
available. An explicit Continue or Respond as request lists no successor slots.
An unanswered question need not prevent another NPC from reacting, provided their
reaction does not decide or act for the player.

## During play

Individual responses use prose with quoted speech; the application supplies the
speaker name. Combined mode retains labelled script blocks. Continue stays visible
but disabled while the application selects speakers and generates their replies.
As each speaker starts, their contribution shows **Name is responding…**, even
before the first words arrive. Stop remains available alongside Continue.

- **Continue** selects one additional response, then gives control back.
- **Respond as** requests one response from the selected present NPC. Both
  actions permit a character who already responded to speak again.
- **Response actions** offers individual reroll, delete and saved variants.
  A reroll uses the original saved context and does not start another speaker.
  Later replies remain in place and show **Earlier context changed**.
- **Replay from here** explicitly regenerates onward when later replies need
  revisiting. Review the existing replay controls before accepting its result.
- **Stop** prevents further responses. Incomplete output cannot authorize the
  next speaker.

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

In Configuration, **Scene responses** can be changed to **Combined scene
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

For a Custom (OpenAI-compatible) connection using `glm-5.3` or
`glm-5.3-flash`, **LLM Connections → Edit → Reasoning effort** offers Provider
default, Low, High, and Max. Existing connections send no setting. The chosen
value is sent as `reasoning_effort` on calls through that connection; changing
the connection to another model stops sending the GLM-specific setting.

[Z.ai's Chat Completion reference](https://docs.z.ai/api-reference/llm/chat-completion)
documents Max as the default, Low/High/Max as GLM 5.3's supported values, and
thinking as always enabled for this model. Effort is a qualitative control,
not a target token count. This feature does not impose an output-token cap.
