# Ensemble Dialogue and Multi-Character Conversation Dynamics

**Outcome:** Multi-NPC scenes feel like a real conversation instead of isolated turns by sequential character-response generators.

## Scope and ownership
- Represent addressees, conversational floor, attention, side remarks, bids to interrupt, silence, nonverbal beats and scene-specific roles.
- Coordinate who speaks, who reacts, when several people answer at once and when a character chooses not to speak.
- Preserve distinct character voices, private information and player-interruption authority across text and optional voice.
- Separate dialogue organization from TTS scheduling and from actor state mutation.

## Milestones
1. Generate a grounded three-NPC exchange with interruption and meaningful silence without duplicated dialogue.
2. Respect directed speech, private channels and absent speakers in an explicit conversation-plan trace.
3. Support overlapping, low-latency real-time ensembles with expressive nonverbal performance.

## Moonshot
A crowded dinner, council meeting or ensemble romance scene has the organic turn-taking, interruption, subtext and group dynamics of live theater.

## Proving scenario
The PC asks one NPC a direct question; another interjects for a reason, a third stays silent, and nobody reveals the whispered message they never heard.

## Interfaces and independence
Owns **conversational coordination**, not physical voice generation. Can begin with current cast and recorded dialogue; advanced channels ([Diegetic Communication](diegetic-communication.md)) and NPC goals enrich later milestones.

## Open questions
- What minimum floor model improves prose?
- How should simultaneous speech appear in a linear transcript?
- Who can interrupt the player's current speech?
