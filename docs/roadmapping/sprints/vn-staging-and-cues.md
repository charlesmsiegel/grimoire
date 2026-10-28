# Visual-Novel Stage Direction and Cues

**Outcome:** Make the existing VN reader more dramatically expressive without creating a different underlying story.

## Scope
- Extend existing stage, sprite placement, backgrounds and dialogue highlighting with explicit/reviewable direction cues.
- Support scene transitions, focus, cinematic pacing, emotion and portrait fallback.
- Keep historical cast/audience attribution and transcript identity correct in all modes.

## Milestones
1. Test transitions and multi-character blocking against current VN reader.
2. Provide low-friction user-authored cues and reliable fallbacks.
3. Offer adaptive cinematic composition for complex scenes, still inspectable and overrideable.

## Moonshot
Real-time virtual stage direction gives each scene a distinctive visual language and reacts to conversation without rewriting story facts.

## Proving scenario
Two outfits and one portrait-only NPC appear with appropriate focus and historical visibility; switching to transcript changes no canonical text.

## Independence and interfaces
The VN reader and outfit/emotion libraries already exist. This is strictly enhancement, not a rebuild.

## Open questions
- Who authors cues: user, deterministic rules or inference?
- What cue state belongs to a post versus a view?
- How are motion/accessibility preferences honored?
