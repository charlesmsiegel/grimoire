# Dialogue prompts with GLM-5.3

Checked against Z.ai's official documentation on 2026-09-11. These notes
separate the provider's documented behavior from our roleplay hypotheses.

## Documented guidance

- GLM-5.3 always reasons. Disabling `thinking` is unsupported; the available
  `reasoning_effort` values are `low`, `high`, and `max`, with `max` the
  documented default. Z.ai recommends `max` for complex work such as coding;
  this is not evidence that it is optimal for dialogue.
- Sampling defaults are `temperature=1.0` and `top_p=0.95`. Z.ai recommends
  tuning one at a time and checking output quality, latency, and cost.
- Streaming separates `reasoning_content` from final `content`.
- Z.ai's coding-agent guidance favors explicit goals, relevant context,
  constraints, and success criteria, with reusable rules maintained centrally.
  Applying that structure to dialogue is our inference, not a documented
  GLM-specific roleplay recipe.

Sources: [GLM-5.3](https://docs.z.ai/guides/llm/glm-5.3),
[migration guidance](https://docs.z.ai/guides/overview/migrate-to-glm-new),
[coding-agent best practices](https://docs.z.ai/devpack/resources/best-practice).

## How the prompts use that guidance

`scene/sections/natural_prose.j2` owns the general continuity rules, including
scenes without a voice anchor. It defines what a coherent reply must retain:
current terms, completed actions, unresolved proposals, and knowledge limits.
It asks for the scene in the required format, rather than an explanation of
the writer's checks. It does not ask the model to disable reasoning.

`scene/sections/voice_policy.j2` owns expression. Distinctness is judged across
an exchange, leaving room for ordinary answers. Anchors describe evidenced
individual tendencies; example dialogue is neither a quota nor an event to
replay. Voice corrections cannot undo current decisions or grant knowledge.
The anchor generator and drift judge use the same distinction.

`rolling_summary/system.j2` and `absorb/system.j2` preserve decision status so
compression does not restore a superseded proposal or invent agreement.
Ambiguous contradictions remain attributed and unresolved. A unilateral
promise can still be a commitment; a demand does not bind its listener.

The OpenAI-compatible client currently sends no sampling or reasoning
overrides, leaving those defaults to the endpoint. It streams final content
and treats reasoning-only chunks as liveness, without inserting reasoning
into the transcript. There is no model-specific parameter change here.

## Automatic model profiles

`templates/scene/model_guidance/glm-5.3.j2` supplies the optional scene profile
for exact `glm-5.3` and `z-ai/glm-5.3` requests. It distinguishes internal
reasoning effort from the length or polish of spoken dialogue and remains
subordinate to shared scene rules. This is a roleplay hypothesis informed by
the documentation above, not a vendor-validated tuning recipe.

Profiles are editable and discovered by exact filename stem. Add a future
model's safe ID as a `.j2` file in that folder; add explicit provider-prefixed
aliases in `backend/src/grimoire/model_guidance.py` when needed. Unknown IDs
add no guidance. An empty profile disables it for that model; the context
layout can disable the whole model-guidance section. The template root follows
`GRIMOIRE_TEMPLATES`, including packaged installations.

Every scene generation uses the requested model from the resolved connection,
including a reroll override. The generation prepares each profile's packed
variant from the same frozen scene context, preserving macro results and
appended instructions. Fallback dispatch selects the matching variant. The inspector includes the selected section and its token cost;
capture records distinct fallback attempts as well as the primary. Provider
strict-message folding still occurs afterward. Utility and JSON tasks do not
receive scene guidance.

Variant preparation performs one packing pass per distinct installed profile
plus the empty profile before dispatch. This keeps editable Jinja includes
stable across attempts. Primary capture follows the turn claim; fallback
variants are captured only when dispatched. With capture off and an unbounded
context budget, composition still skips token counting.

## What still needs measurement

The proposed benefit is more natural dialogue with fewer conversational
resets. Offline template and parser checks verify wiring, not that benefit.
Use an opt-in live comparison with synthetic scenarios and the same model,
endpoint, cast, history, and output budget; vary one instruction or sampling
setting at a time and repeat each case.

Include a revised plan, a refusal that remains a refusal, a conditional offer
not yet accepted, a completed task, a justified reversal, conflicting claims,
private knowledge, and a terse ordinary reply. Check both the next reply and
its subsequent summary. Record adherence, voice naturalness, output length,
latency, and token usage separately. Do not call a prompt tuned or improved
solely because canned-output tests pass, or use real campaign content in
public fixtures or reports.
