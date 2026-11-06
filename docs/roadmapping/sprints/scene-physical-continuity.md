# Local Scene Geometry, Affordances and Physical Continuity

**Outcome:** Maintain the immediate physical situation—presence, position, reachable objects, line of sight and local affordances—through ordinary dialogue and action.

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
