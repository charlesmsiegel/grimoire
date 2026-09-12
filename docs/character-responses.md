# Character responses

Individual character responses are the default. Each generation receives the
current speaker's voice, examples and knowledge. Other cast members contribute
their names and conversation that the speaker witnessed. Grimoire retains the
campaign context needed for narration and mechanics.

A player post can receive a short sequence of responses. Each NPC can respond
once automatically; Grimoire also has one narration slot. The response proposes
the next speaker, and the application checks eligibility before starting another
call. Missing or invalid routing metadata ends the sequence.

## During play

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
historical variant uses the frozen unprofiled context.

The automated checks establish routing, persistence, privacy boundaries and UI
behavior. Voice naturalness still needs comparison during play; no automated
result here establishes that one model or mode writes better dialogue.
