# Character Knowledge, Secrets and Point of View

> **V2 scope correction:** The typed graph and evidence store are not globally visible to any NPC. Actor views may traverse only evidence-authorized relationships. Deterministic observation filtering is default; model uncertainty resolution needs an opt-in cost flag. See the [V2 ordered roadmap](../README.md) for prerequisites, waves and current first-slice proof. Older references to shipped V1 APIs describe behavior to *port*, not V2 capabilities.


> **Status:** the first slice (milestone 1) is implemented as a pure in-memory contract; see [Knowledge and perspective contract](../../KNOWLEDGE.md). Milestones 2–3 and persistence remain open.

**Outcome:** Distinguish established truth from who witnessed it, believes it, inferred it or is allowed to know it.

## Scope
- Define one canonical evidence/provenance contract for audience, actor knowledge, uncertainty and time.
- Separate director knowledge, player visibility, NPC beliefs, misinformation and withheld reveals.
- Filter prompt and retrieval inputs without leaking a secret through another cast member.

## Milestones
1. Define and test a typed in-memory evidence contract for a private event, two actual observers, a bystander and a distorted report; produce actor- and director-specific read projections.
2. Respect perspective in prompt selection and dialogue.
3. Represent conflicting evidence, unreliable narration and later revelations.

## Moonshot
Epistemically faithful characters maintain and revise their own imperfect theories of the world throughout long campaigns.

## Proving scenario
An unseen NPC cannot mention a private betrayal; after being told a distorted version they may believe it without making the distortion canon.

## Independence and interfaces
The evidence schema is owned here; Context Retrieval and Relationships consume it. An initial source-aware event audience is enough.

## Wave 2 evidence contract and boundaries

Produce an immutable evidence/claim vocabulary (the exact dataclass/wire spelling is this sprint's choice) that can express: stable event/claim/evidence IDs; originating source; actual observer or communication recipient; delivery/witness confirmation; channel (e.g. witnessed, overheard, reported, written, telepathic); time and scene/turn ordering; audience/privacy; assertion status; evidence provenance; and an actor's uncertain or false **belief** distinct from canonical event truth.

- Test at least: a private betrayal witnessed by two actors, an absent actor who cannot reference it, a third actor **told a false version** who may believe the claim without making it true, and subsequent corrected/rejected beliefs. Merely being in an organization or connected by graph edges must not give secret knowledge.
- An **access rule** is not a **truth rule**. Sharing an artifact of evidence need not disclose all underlying facts. One narrator/GM query may read established truth, while an actor query sees only authorized perceptions and supported beliefs. Use explicitly authorized perspective parameters, not ad-hoc filtering after a global read.
- The first proof is **deterministic, in-memory and provider-free**. No new per-actor LLM call is needed to decide simple witnessed/recipient visibility, and there is no persistence in 012.
- Provide a frozen consumer contract for 017 graph visibility, 020 evidence persistence, 027 per-NPC context, 031 bounded retrieval and 032 private messages. These consumers must not invent a rival definition of "knows" or infer it from connectivity.
- A scene transcript has text and speakers but does **not** automatically prove who heard an off-stage, private or telepathic line.

See [Wave 2 handoffs](../WAVE2_HANDOFFS.md#observationevidence-boundary-012--017--020).

## Open questions
- How explicit must belief and uncertainty be?
- What happens after scene edits invalidate witnessed evidence?
- How are GM-only secrets protected in exports?
