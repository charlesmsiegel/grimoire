# Relationships and reputation (first slice of sprint 078)

**Status:** first slice of [Relationships And Reputation](roadmap/sprints/relationships-and-reputation.md): milestone 1 (a meaningful interaction and its consequences for each person who has evidence of it) and the opposing-perspectives half of milestone 2. The code is the pure package `grimoire.relationships`: headless and in memory, with no persistence, provider call, UI or runtime wiring. Public reputation held by a community (milestone 3) is not built.

## The rule

An attitude belongs to **one holder** and points at **one other actor or group**. There is no global attitude meter and no shared score: the envoy's resentment of the newcomer is a different record from the newcomer's view of the envoy, from the dockhand's view of the newcomer, and from anything the guild "thinks". An attitude changes only from what its own holder has evidence of, so updating one NPC never teaches another NPC anything. Group membership, graph connectivity, being a participant in an event and director-only truth never count.

## Records

`Attitude(attitude_id, holder, toward, dimension, account, moment, provenance, evidence_ids=(), belief_ids=(), degree=None, supersedes=None)`:

| Field | Meaning |
|---|---|
| `holder`, `toward` | Exact `InstanceRef`s of one campaign; never the same instance. `toward` may be an agent or a `group`. |
| `dimension` | One of the first-class dimensions: `trust`, `affection`, `fear`, `rivalry`, `resentment`, `debt` (the holder feels it owes `toward`). Anything subtler lives in the account. |
| `account` | The holder's own narrative reading, at most 400 characters: "resents being called a lackey in front of the dockhands". This is the primary representation. |
| `degree` | An optional numeric interpretation, never required. `trust` and `affection` run from -3 to 3; the other dimensions are intensities from 0 to 3. No relationship needs a score. |
| `evidence_ids`, `belief_ids` | The holder's own 012 evidence and beliefs the change rests on, 1 to 8 in all, no duplicates: its episode provenance. |
| `moment`, `provenance` | When the change happened in campaign order (012's `Moment`), and attribution text such as `scene:quay/t1`. |
| `supersedes` | The previous head of the same (holder, toward, dimension) chain. |

`AttitudeLedger(campaign_id, records)` is an append-only value. Each (holder, toward, dimension) is one chain with exactly one current head: a record for a key that already has a head must supersede that head (`unsuperseded_conflict`, `stale_supersession`, `different_chain`) at a strictly later moment (`nonmonotonic_revision`). Superseded records stay as history. At most 256 records are retained; overflow fails (`budget_exceeded`) and history is never pruned. Shape errors raise `RelationshipError` with a stable `.reason` and never carry authored text.

## Admission

`admit(ledger, attitude, *, knowledge, roster, definitions, controls, authored_by, expected_revision)` admits one change from a caller-owned current snapshot and returns the new ledger:

1. Holder and target resolve through the exact campaign roster and effective entity (`unresolved_actor`); the holder is an agent (`not_agent`); the target is an agent or a group (`not_agent_or_group`).
2. The holder is a director-controlled NPC and the change is director-authored. A player character's feelings are their player's interiority (`pc_interiority`), and narration or a player cannot author an NPC's attitude (`npc_authority_required`). Missing or ambiguous control fails closed (`unknown_control`, `ambiguous_control`).
3. Every ground is the holder's **own** confirmed evidence, or its own (current or superseded) belief, at or before the change's moment, read through 012's `actor_knowledge` (`unavailable_grounds`). Another actor's evidence, unconfirmed or later evidence and director events never qualify.
4. At least one ground **overtly concerns** `toward` (`unrelated_grounds`): a report the holder takes `toward` to be the source of (`attributed_to`), or a report or belief whose proposition is overtly `about` `toward`. A perception alone never qualifies, because a perception's only subject is its event and event participants identify no one to the holder: a masked figure's shove does not tell the envoy who shoved. A belief that it was the newcomer, admitted under 012's rules, does.
5. Revision: a fresh ID needs `expected_revision == ledger.revision` (`stale_revision`). An exact retry under the original expected revision returns the same ledger after rechecking every present condition above; a changed record or revision under the same ID is `retry_conflict`.

Grounds prove access and relevance, not that the account is a fair reading: the director authors the interpretation. `admit` writes no knowledge, belief, event, ruling or accepted effect.

## Reading

- `attitudes_of(ledger, holder, *, as_of, toward=None)`: the exact holder's current heads at `as_of`, ordered by target and dimension. An empty answer means "no recorded attitude", not neutrality.
- `history_of(ledger, holder, toward, dimension, *, as_of)`: one chain, oldest first, with grounds.
- `dispositions(ledger, holder, *, as_of)`: the current heads as `Disposition(toward, dimension, account, degree, moment)`, without IDs, grounds, provenance or authorship. This is the shape a per-NPC brief (027) or a director panel renders; IDs never reach a prompt.

These are pure director/internal projections, not authentication boundaries. A controller-facing adapter must authorize the requested holder first; an NPC's attitudes are not a player's to read.

## Proof

`backend/tests/test_relationships.py` runs the sprint's proving scenario. The newcomer (a PC) tells the guild envoy on the quay "your guild are thieves and you are their lackey"; a dockhand overhears. The envoy's resentment (3) and distrust (-2) are admitted from the report it received; the dockhand's affection for the newcomer is admitted from the same words, overheard: opposing perspectives on one episode. Two guild members were absent. Neither can acquire an attitude from the envoy's evidence or from an unrelated perception of their own. One later receives the envoy's letter: before it arrives they still cannot resent the newcomer, and once it has, they can, less strongly and only because of the letter. The other still holds nothing. The PC has no attitude written for them, nobody's attitude toward the envoy changed, and the director and every actor's knowledge projection are unchanged. Further tests cover perceptions and anonymous reports not identifying the target, belief grounds, supersession and history, idempotent retries that recheck the present, authority, shape and bounds.

## Decisions on the sprint's open questions

- **First-class traits:** the six dimensions above; anything else is narrative in `account`.
- **Strength:** narrative first, with an optional small integer interpretation; never a forced score.
- **Narrator proposals:** not in this slice. Narration cannot author an attitude; a reviewed proposal path (like 021's) is later work.

## Not in this slice

| Item | Owner |
|---|---|
| Public reputation held by a community, with uncertainty (milestone 3) | 078 later |
| Change of allegiance (the rest of milestone 2) | 078 later |
| Persisting attitudes, sync, and a storage adapter binding roster, control and knowledge revisions | 078 later |
| Rendering dispositions in the per-NPC brief, and goals reading them | 027 / 039 consumers |
| Debts and obligations as commitments with deadlines | [Quests and commitments](roadmap/sprints/quests-and-commitments.md) (077) |
| Rumor transport between members | 063 ([RUMORS](RUMORS.md)) |
| A relationship panel | 116 |
