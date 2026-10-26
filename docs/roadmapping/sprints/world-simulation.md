# World Simulation, Time and Long Activities

**Priority:** after stable state/clock boundaries; start with credible small time skips and one activity rather than whole-world autonomous simulation.

## Goal

Campaign time and fictional life advance coherently. When a PC spends a week traveling, studying, crafting or recovering, the world doesn't freeze and the results are real campaign state, not improvised narration.

## Work to plan

- **Time Management:** respect the campaign calendar (including non-Gregorian/custom calendars), established scene chronology, exact versus approximate durations and notable events; keep time advancement deterministic.
- **World Simulation:** model clocks, queued tasks, offscreen developments, character/group agendas, weather/environmental changes and other causal consequences with explicit limits on cost, scope and activity.
- **Time-Skip Simulation:** advance only relevant NPC/group state, goals, relations, resources, commitments, open threats and plot threads. Reconcile with what the PC could later know; missing perception doesn't imply no consequences.
- **Long Activities / Long-Activity Resolution:** from "spend three weeks researching", "travel", "recover", "train", or "craft", estimate or accept duration, identify interim events, resolve progress using chosen mechanics when relevant and apply resulting effects. Offer a compressed outcome or played-out scene.
- **Campaign calendar and resources:** elapsed days may consume provisions, create costs, progress group commitments or trigger deadlines. Use Inventory and Mechanics only if active/appropriate, not as hard requirements for fiction-only activities.
- **Offscreen agency:** deterministic clocks/known rules first; optional bounded inference for ambiguous developments and exceptional narratives. Avoid compulsory agent loops over the full character roster.
- **Traceability:** keep idempotent time-skip IDs, scene and fact attribution, a reviewable summary of what changed, rollback/retcon policy, stale-state handling, and consistent recovery after interruption.

## Proving scenario

The party spends twenty days traveling and studying a lost language. The calendar advances once, a known deadline and one NPC faction agenda progress, rations are decremented if tracked, research advances under the selected rules, and the PC receives a reviewed narrative summary. Retrying the same accepted skip doesn't advance time twice; relevant NPCs are not updated a second time.

## Dependencies and boundaries

[State Transactions](state-transactions.md) governs committed changes; [Societies](societies.md) supplies NPC/faction agendas; [Mechanics](mechanics.md) supplies optional study/craft/rest actions; [Challenges](challenges.md) handles dangers and encounters; [Narrative Design](storytelling.md) handles what to show in a scene.

## Open questions

- How is a long activity represented—single typed intent, a series of scheduled actions, or a time-bound clock?
- When does an activity need a user's duration confirmation or explicit intermediate scene?
- What constitutes a relevant NPC/group that should act offscreen, and who chooses that scope?
- How do simultaneous time skips, overlapping deadlines, recovery and scene revisions avoid double-applying consequences?
- How much autonomy can be safely delegated to inference, and what does the player review?
- Which calendars, environmental rules and game-system seasonal procedures need support in the first useful slice?
