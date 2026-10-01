# Scene generation experiments

Each experiment is a separate commit on a scene-generation experiment branch. Keep the
commit hash with the private run record so a prompt variant can be replayed.
Private transcripts, character cards, settings, and measurements stay outside
this repository. Tests verify prompt construction and isolation; live reruns
assess writing quality. A passing rendering test is not a successful voice eval.

## Baseline: accumulated guidance

Commit `a3fb9fe89` collects the knowledge-boundary, voice, prose-ceiling, and
conversational-action guidance added before this branch was created. Several
variables changed together, so it is a checkpoint rather than an isolated
experiment. It is the comparison point for the experiments below.

## ST-01: lean roleplay framing

Hypothesis: general style prohibitions and repeated instructions to explain less
can crowd out character evidence. A short roleplay brief and explicit Markdown
narration may encourage character-led action and speech without requiring every
contribution to be a complete conversational explanation.

Changes, relative to the baseline:

- `scene/sections/natural_prose.j2` replaces the generic phrase blacklist,
  construction bans, and detailed rhythm rules with a short scene-writing brief.
  The earlier expression policy remains available as the optional built-in
  Natural Prose (Legacy) style; its replay eval explicitly selects that style.
  Continuity, player control, individual knowledge, and personal testimony
  boundaries remain explicit. Configured prose style and character expression
  retain precedence.
- `scene/sections/voice_policy.j2` keeps attribution and authored-constraint
  precedence while reducing general voice instruction. Actor-scoped cards,
  anchors, examples, and outstanding corrections retain their existing wiring.
- `scene/response_actor.j2` asks for the assigned character's next fictional
  roleplay reply, using actions, private reactions, and dialogue. It replaces
  the accumulated anti-polish advice. Narrator ownership, eligible speakers,
  handoff rules, player decision stops, and roll/state ordering are unchanged.
- `scene/sections/response_format.j2` requests italicized action and narration
  in Markdown. Dialogue remains quoted so the existing transcript format and
  speech distinction continue to work. Private thoughts remain narrator context.
- `scene/model_guidance/glm-5.3.j2` retains a short instruction to keep reasoning
  out of the contribution rather than adding another prose-style policy.

This is an approximation of SillyTavern's roleplay framing, not a full transport
clone. The configured word and paragraph ceilings, no-minimum instruction,
message roles, sampling, reasoning parameters, context packing, and turn parser
are unchanged. NoAss-style history folding, initial-system-role preservation,
sampling changes, and additional thinking fields are separate experiments.
Character-specific narration instructions and greetings are not copied or
invented by this experiment; existing authored evidence remains the source.

For a live comparison, rerun the same player cue against the same preceding
transcript and character inputs at the baseline and experiment commits. Retain
the same model, reasoning effort, sampling, and length settings. Check whether
speech is selective, actions matter, private reactions remain private, and
voices stay distinct. Also check required control blocks and player ownership.
The output may differ even with identical inputs; compare multiple reruns before
attributing a change to the experiment. Live outcome: pending.


## ST-03: actor perception at the response boundary

Based directly on ST-01, independently of ST-02's transport change. Hypothesis:
a knowledge reminder beside the assigned actor instructions helps distinguish
speech, observable action, and private narrative context in mixed transcript
contributions. The actor prompt explicitly permits unquoted speech and visible
narrated actions while withholding private explanations and unsupported
background knowledge. Missing sources must not be repaired with invented
briefings. This instruction applies to NPC responses, not the scene narrator.

All other ST-01 inputs and settings are held constant. Use the same opening,
player cue, character inputs, model, and generation settings in a separate
replay. Rendering checks establish instruction delivery only; live evaluation
must check both private-information restraint and ordinary dialogue/action
recognition. Length and prose polish remain separate evaluation dimensions.
