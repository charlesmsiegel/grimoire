# Local Scene Geometry, Affordances and Physical Continuity

**Outcome:** Maintain the immediate physical situation—presence, position, reachable objects, line of sight and local affordances—through ordinary dialogue and action.

**Status:** **first slice implemented**, headless and pure: `grimoire.staging` (no store, no runner). The contract is in [STAGING.md](../../STAGING.md). Milestone 1 and the proving scenario are met on synthetic fixtures; milestone 2 (a stage kept across narrated turns, with 035) is next, and how a turn's several staged steps map to 021's one sequence per turn is an [open question](../../STAGING.md#open-design-question-one-sequence-per-change) for it.

## Scope and ownership
- Track salient local positions, posture, held objects, barriers, exits, visibility, audibility, distance bands and object/door states only where gameplay or narration needs them.
- Separate locally ephemeral staging from durable changes; do not require global map coordinates or tactical grids.
- Let players inspect or correct facts and distinguish established positions from plausible but unstated staging assumptions.
- Use these facts to constrain reach, stealth, movement and narration without replacing a selected ruleset's movement mechanics.

## Milestones
1. Record one small room scene with two characters, a locked door, a held key and a changing line of sight.
2. Maintain those constraints across several narrated turns and corrected actor movements.
3. Offer optional spatial/sensory affordance queries to other systems, maps, audio and combat.

## Moonshot
Grimoire tracks a believable, continuously changing local scene with the grounding of a virtual stage director—even in pure text, without a renderer or coordinates.

## Proving scenario
An NPC leaves the room; the next post does not have them casually take an object from the table. A shut door blocks an ordinary whispered exchange unless the scene establishes another channel.

## Interfaces and independence
Owns *local physical facts*, not geography ([Location Topology](location-topology.md)), player intentions, combat turns or character knowledge. Early staging works with scene-local records and fixtures; commit timing is delegated to [Turn Effects](turn-effects-and-wrap-up.md).

## Open questions
- How much geometry is useful before it becomes simulation clutter?
- When should the director ask rather than invent a position?
- What is the distinction between visible staging and canonical movement?

## First slice (implemented)

The headless first slice is [`grimoire.staging`](../../STAGING.md). It reaches milestone 1 and the proving scenario on synthetic fixtures: an actor who left the room cannot take the letter from the table, sight between two rooms follows the door, a whisper through a shut door is refused while telepathy reaches, and only those who can see an event get a perception of it.

Answers to the open questions, for this slice:
- **How much geometry:** areas and the ways between them, nothing finer. No coordinates, spots or distances.
- **Ask or invent:** every fact (a way, a placement, an absence) is `established` or `assumed`; each answer lists the assumed facts it rests on, and anything that would become evidence (a presence, a perception) refuses an assumed fact unless the caller allows it.
- **Staging or canon:** the stage is scene-local director truth, never canon and never actor knowledge. Durable consequences become 021 operations in the next slice.

Later work: a stage persisted and maintained across turns with 035 (milestone 2); 021 operations and seeding from 017 `located_in`/`carries` and 034 holders; where off stage an absent actor is (off stage is one place); positions within an area, distance bands, posture, cover, stealth; containers and lockable containers; audibility levels; affordance queries for maps, audio and combat (milestone 3). See [STAGING](../../STAGING.md#boundaries-and-later-work).
