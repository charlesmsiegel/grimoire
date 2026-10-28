# NPC Autonomy, Relationships and Societies

**Priority:** later living-world capability, with smaller relationship-policy improvements possible earlier.

## Goal

NPCs and groups have understandable motivations, knowledge, loyalties and relationships. They can make credible decisions and react to players without needing a full independent LLM agent for everyone.

## Work to plan

- **Relationships:** track significant feelings, trust, rivalry, debts, obligations, intimacy, fear and changing context between actual individuals, including mutual/asymmetric information and provenance.
- **Reputation:** distinguish what particular people or groups know and believe from global objective truth; represent local standing, fame, rumors, membership and public consequences.
- **Social Simulation:** NPCs have goals, dispositions, roles, constraints, agendas, alliances and rivalries. Their choices can influence relations, group commitments, resource access and future scenes.
- **NPC Autonomy:** choose feasible actions from current state using deterministic logic or bounded decision models, reserving generative reasoning for ambiguity. A narrator voices chosen actions but cannot silently change them.
- **Faction Dynamics:** groups can have leaders, membership, internal disagreements, territory, resources, relationships and competing plans. Actions by one member do not automatically imply universal group knowledge.
- **Time-Skip Simulation:** significant elapsed time can advance relevant NPC/group agendas, relationships, queues and commitments offscreen; see [World Simulation](world-simulation.md) for the actual time/transaction mechanism.
- **Character relevance and spotlight:** avoid simulating every NPC on every tick. Relevance, proximity, threats, player choices and policy determine who meaningfully acts.

## Proving scenarios

A PC publicly insults a guild representative. That individual's view of the PC worsens, but distant members do not immediately know it. A rival guild hears a distorted rumor later, depending on credible dissemination. When weeks pass, an affected group's plans may progress with recorded consequences, while an unrelated distant NPC is not needlessly simulated.

## Dependencies and boundaries

[Generation Systems](generation.md) selects and records decisions; [State Transactions](state-transactions.md) applies consequences; [World Simulation](world-simulation.md) handles offscreen time; [Campaign Tuning](campaign-tuning.md) influences drama, pacing and spotlight; [Narrative Design](storytelling.md) may emphasize a thread without inventing facts.

## Open questions

- Which aspects of beliefs, social relationships and group goals need canonical structure versus well-attributed prose?
- How do misinformation, rumors and faction knowledge propagate without omniscience?
- How are groups' collective decisions reconciled with individual members' independent agendas?
- Which NPCs receive offscreen processing on a skip, and how much inference is justified?
- How should campaign difficulty, drama and spotlight policies affect social dynamics without falsifying established outcomes?
