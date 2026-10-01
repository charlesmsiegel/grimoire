# Shared scene dialogue guidance

Scene replies use shared templates even when the global system prompt is empty.
The guidance applies across models and covers three separate concerns:

- `scene/sections/natural_prose.j2` distinguishes dialogue from narration and
  requires an established source for each character's knowledge. Clearly
  presented unquoted speech remains valid; quoted thoughts are not audible.
- `scene/sections/voice_policy.j2` uses the assigned character's authored voice
  evidence and treats dialogue history as event context rather than a style
  template. Deliberate callbacks remain possible without spreading verbal
  habits across the cast. Earlier stylization does not establish a new trait.
  The existing precedence for authored constraints and voice corrections holds.
- `scene/sections/response_budget.j2` presents resolved word and paragraph counts
  as prose ceilings with no minimum. `scene/length_correction.j2` reinforces
  the same interpretation when the existing drift detector finds overshoot.

These paths are relative to `templates/`. The voice policy keeps its existing
render condition; it does not invent voice evidence for a lone bare character.
No sampling settings, reasoning budgets, drift thresholds, or output parsers
change. Required control blocks remain outside the prose-length instruction.

Tests establish that shared guidance reaches chat and opener prompts without
custom global instructions, that actor prompts retain their own voice evidence,
and that resolved length settings reach the templates. They do not establish
that a live model follows the instructions. For a live comparison, check an easy
practical question, sincere reassurance, and a reply after someone else's
unusual metaphor. Look for brief complete answers, ordinary emotional expression,
and distinct voices without forced quirks. Knowledge-boundary checks should
include private thoughts, unquoted speech, and an unseen person's presence.


Social uncertainty is situational: competence does not imply emotional ease,
perfect self-understanding, or immediate trust. A reply can leave discomfort
unresolved without making every character hesitant. Cosmetic nervous gestures
do not substitute for uncertainty affecting what someone says or chooses.
Personal testimony stays within the speaker's experience; group claims require
checking each participant's history and the applicable mechanics.

The assigned-NPC instruction also favors a sentence or two for routine exchanges,
with detail justified by new information, decisions, or actions. It keeps
compliance language out of dialogue. It does not require confident characters
to become awkward or change the configured ceiling.

Most conversational posts favor spontaneous, selective speech over a crafted
opening, exhaustive explanation, and closing line. Uneven or unfinished replies
are allowed without forcing awkwardness or overriding authored formality.

The word ceiling covers dialogue and narration together. Meaningful nonverbal
action can carry a response without an accompanying explanation in dialogue.
Brief speech with action, or action alone, is valid; there is no fixed ratio
and no requirement to fill unused words with gestures or description.

## Experiment checkpoints

The initial checkpoint collects the prompt changes made before the experiment
branch was created. It is a reproducible starting point, not a single-variable
experiment. Live reruns still produced polished, dialogue-heavy contributions;
the added guidance did not establish the desired naturalness.

Each subsequent experiment gets a separate commit. Record its hypothesis,
the variable changed, and validation in the commit message. Keep model,
reasoning effort, sampling, character inputs, and the preceding transcript
constant when comparing prompt variants. Record live results before starting
the next experiment. Private prompts and transcripts stay outside this repo.
Rendering tests verify prompt construction; live comparisons assess voice.
Use the commit hash together with the private run record to identify a result.
