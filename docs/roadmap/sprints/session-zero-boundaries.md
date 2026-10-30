# Session Zero, Narrative Boundaries and Consent Controls

**Status:** milestone 1's headless policy is implemented; see [the session-zero contract](../../SESSION_ZERO.md). Executing pause/skip/redirect in play belongs to 035.

**Outcome:** Give players and groups explicit control over narrative themes, sensitive content, character relationships and permitted storytelling intensity.

## Scope and ownership
- Provide world/campaign-level authoring of themes, hard limits, fade-to-black preferences and personal/PC boundaries without requiring disclosure of private reasons.
- Distinguish content preferences from PC action sovereignty and mechanical legality; support private per-player controls in collaboration.
- Offer reliable mid-session pause, redirect, skip and revise controls that do not punish the player or silently override recorded facts.
- Handle negotiated boundaries in prompts, scenarios, voice and exports with privacy-preserving defaults and transparent conflicts.

## Milestones
1. Define and test an **immutable, typed, optional session-zero configuration and a safe pause/skip/redirect intent contract** against synthetic proposed narration. Do not claim an executable text-play action until the Wave 5 runner consumes it.
2. Test enforcement with difficult genre situations and conflicting group boundaries, preserving player control.
3. Support revisable, per-participant confidential preferences and automatic safe staging decisions in multimodal play.

## Moonshot
Grimoire handles the social contract of long-form collaborative storytelling with the care of an excellent human group: expressive, flexible and respectful without reducing difficult fiction to bland content.

## Proving scenario
A player excludes graphic violence, keeps high-stakes conflict and requests a fade-to-black transition; the game preserves the outcome but avoids graphic depiction and offers easy correction.

## Interfaces and independence
Owns **content and participation boundaries**; [Player Sovereignty](player-sovereignty.md) owns PC actions and internal experiences. Can start as isolated campaign settings and prompt configuration.

## Wave 2 configuration and later execution boundary

- Define optional setting/world/campaign/player-scoped content preferences: avoid, fade-to-black, skip, pause/redirect, themes and private notes as appropriate. Missing configuration is explicitly *unset*, not a presumption about content or player motives; privacy is enforced by access policy.
- Keep content/participation boundaries independent of **PC sovereignty** (who may author choices and thoughts) and **adjudicated truth** (what happened). Changing the rendered depiction of graphic violence must not retroactively change the accepted attack's result or secretly create an action.
- First proof is **in-memory typed policy and pure intent decisions** using fake scene/narration records, including conflicting preferences, private note exclusion, explicit redirect requests, and rules distinguishing presentation changes from proposed canonical edits. No file writer, UI, model call or real player interaction is part of 016's first slice.
- The generic file store (010) can hold an opaque preference record without importing 016. The V0.2.1 importer (026) preserves only actual existing preferences; unknown/missing fields stay unknown, never auto-generated.
- **035 Minimal Text Scene Runner owns the first working text pause/skip/redirect execution.** It consumes 016's policy, the Wave 1 intent router and player authority, preserves already accepted outcomes, and provides a visible correction. Audio, cooperative private settings and full interactive UI come later.

See [Wave 2 handoffs](../WAVE2_HANDOFFS.md#session-zero-boundary-016--026--035).

## Open questions
- What is private versus visible to other players/GM?
- How are rules for character harm and consent expressed without changing mechanical results?
- What happens when a boundary changes after events are recorded?
