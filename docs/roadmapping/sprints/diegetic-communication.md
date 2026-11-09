# Diegetic Communication, Channels and Recipients

**Outcome:** Correctly distinguish who speaks, who hears, who understands, and through which fictional channel a message was communicated.

## Scope and ownership
- Represent addressed audience, channel and privacy for speech, whisper, thought, telepathy, radio, letter, sign, text message and other setting-specific means.
- Distinguish **real spoken input to the app** from speech inside the fiction; real-world speech capture never automatically declares an in-world broadcast.
- Account for languages, range, barriers, eavesdropping, interpretation, blocked channels and imperfect receipt when supported by fiction or mechanics.
- Emit evidence of sent versus received messages to the knowledge system without assuming receipt establishes truth.

## Milestones
1. Route a whispered or telepathic message to its intended recipient in a multi-NPC scene without leaking it to bystanders.
2. Add explicit delivery/receipt status and setting-specific channels with constraints.
3. Support multimodal, historical and partially intercepted communications with inspectable knowledge effects.

## Moonshot
Arbitrarily complex worlds communicate faithfully: coded letters, encrypted radio, magical speech, telepathy and misunderstood languages all have correct recipients and consequences.

## Proving scenario
The player *speaks into a microphone* 'I tell Emma telepathically the guards are listening'; Emma receives a fictional private message, guards do not, and a separate UI command is not mistaken for dialogue.

## Interfaces and independence
Uses the [Knowledge and Perspective](knowledge-and-perspective.md) evidence contract when available; first slice can use the existing audience/cast fields. Owns **transmission**, not long-term belief, real microphone recognition or player control.

## Open questions
- What default audience does ordinary unmarked dialogue assume?
- When is receipt certain versus inferred?
- How do public transcript views avoid spoiling private channels?
