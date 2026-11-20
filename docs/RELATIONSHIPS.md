# Relationships and reputation (first slice of sprint 078)

**Status:** first slice of [Relationships And Reputation](roadmap/sprints/relationships-and-reputation.md): milestone 1 (a meaningful interaction and its consequences for each person who has evidence of it) and the opposing-perspectives half of milestone 2. The code is the pure package `grimoire.relationships`: headless and in memory, with no persistence, provider call, UI or runtime wiring. Public reputation held by a community (milestone 3) is not built.

## The rule

An attitude belongs to **one holder** and points at **one other actor or group**. There is no global attitude meter and no shared score: the envoy's resentment of the newcomer is a different record from the newcomer's view of the envoy, from the dockhand's view of the newcomer, and from anything the guild "thinks". An attitude changes only from what its own holder has evidence of, so updating one NPC never teaches another NPC anything. Group membership and graph connectivity are not inputs at all, and being a participant in an event and director-only truth never count.

## Records

`Attitude(attitude_id, holder, toward, dimension, account, moment, provenance, evidence_ids=(), belief_ids=(), degree=None, supersedes=None)`:

| Field | Meaning |
|---|---|
| `holder`, `toward` | Exact `InstanceRef`s of one campaign; never the same instance. The holder is a `character`; `toward` may be an agent or a `group`. |
| `dimension` | One of the first-class dimensions: `trust`, `affection`, `fear`, `rivalry`, `resentment`, `debt` (the holder feels it owes `toward`). Anything subtler lives in the account. |
| `account` | The holder's own narrative reading, at most 400 characters: "resents being called a lackey in front of the dockhands". This is the primary representation. |
| `degree` | An optional numeric interpretation, never required. `trust` and `affection` run from -3 to 3; the other dimensions are intensities from 0 to 3. No relationship needs a score. |
| `evidence_ids`, `belief_ids` | The holder's own 012 evidence and current beliefs the change rests on, 1 to 8 in all, no duplicates, stored sorted (so the same grounds compare equal however they were supplied): its episode provenance. |
| `moment`, `provenance` | When the change happened in campaign order (012's `Moment`), and attribution text such as `scene:quay/t1`. |
| `supersedes` | The previous head of the same (holder, toward, dimension) chain. |

`AttitudeLedger(campaign_id, records)` is an append-only value. Each (holder, toward, dimension) is one chain with exactly one current head: a record for a key that already has a head must supersede that head (`unsuperseded_conflict`, `stale_supersession`, `different_chain`) at a strictly later moment (`nonmonotonic_revision`). Superseded records stay as history. At most 256 records are retained; overflow fails (`budget_exceeded`) and history is never pruned. Shape errors raise `RelationshipError` with a stable `.reason` and never carry authored text.

## Admission

`admit(ledger, attitude, *, knowledge, roster, definitions, controls, authored_by, now, expected_revision)` admits one change from a caller-owned current snapshot and returns the new ledger:

1. `now` is the campaign's current sequence, and a change dated after it is refused (`future_moment`): the ledger is append-only, so one future-dated record would lock its chain. The holder is a `character` (`unsupported_holder_kind`, as in 012): a group or community holds no attitude in this slice, even one with agency and evidence of its own, because community reputation is milestone 3.
2. Holder and target resolve through the exact campaign roster and effective entity (`unresolved_actor`); the holder is an agent (`not_agent`); the target is an agent or a group (`not_agent_or_group`).
3. The holder is a director-controlled NPC and the change is director-authored. A player character's feelings are their player's interiority (`pc_interiority`), and narration or a player cannot author an NPC's attitude (`npc_authority_required`). Missing or ambiguous control fails closed (`unknown_control`, `ambiguous_control`).
4. Every ground is the holder's **own** confirmed evidence, or its own **current** belief, at or before the change's moment, read through 012's `actor_knowledge` (`unavailable_grounds`). Another actor's evidence, unconfirmed or later evidence, a belief the holder had already revised by the change's moment and director events never qualify. Grounds are read as of the change's own moment, so a backdated change is checked exactly as it would have been then.
5. At least one ground **overtly concerns** `toward` (`unrelated_grounds`): a report the holder takes `toward` to be the source of (`attributed_to`), or a report or belief whose proposition is overtly `about` `toward`. A perception alone never qualifies, because a perception's only subject is its event and event participants identify no one to the holder: a masked figure's shove does not tell the envoy who shoved. A belief that it was the newcomer, admitted under 012's rules, does.
6. Revision: a fresh ID needs `expected_revision == ledger.revision` (`stale_revision`). A changed record or revision under an existing ID is `retry_conflict`, checked before roster, control and grounds. An exact retry under the original expected revision returns the same ledger after rechecking every present condition above.

Control is checked as it stands now, because Wave 1 control has no history (as for 012's beliefs, [KNOWLEDGE](KNOWLEDGE.md#belief-authorship-player-sovereignty)). So the director could backdate an attitude into a stretch when a player controlled the holder; refusing that needs timed control history (009 and 035).

Grounds prove access and relevance, not that the account is a fair reading: the director authors the interpretation. `admit` returns only a ledger: it writes no knowledge, belief, event, ruling or accepted effect.

## Reading

- `attitudes_of(ledger, holder, *, as_of, controls, toward=None)`: the exact holder's current heads at `as_of`, ordered by target and dimension. An empty answer means "no recorded attitude", not neutrality.
- `dispositions(ledger, holder, *, as_of, controls)`: the current heads as `Disposition(toward, dimension, account, degree, since)`, without grounds, provenance or scene labels. `toward` is still a reference: a per-NPC brief (027) or a director panel must render it as a name the holder knows, never as reference text, because IDs never reach a prompt.
- `history_of(ledger, holder, toward, dimension, *, as_of)`: one chain, oldest first, with grounds: the director's audit view.

`attitudes_of` and `dispositions` read only a holder the director controls now (`pc_interiority`, `unknown_control`, `ambiguous_control` otherwise). Attitudes are director-authored NPC interiority, so when a player takes an NPC over they are never shown as that character's own feelings, and neither the player nor the director can revise them as the player's (012's inherited rule; a player-facing inherited view is later work). `history_of` keeps the NPC era readable to the director. These are pure director/internal projections, not authentication boundaries: an adapter must authorize its caller first.

## Proof

`backend/tests/test_relationships.py` runs the sprint's proving scenario. The newcomer (a PC) tells the guild envoy on the quay "your guild are thieves and you are their lackey"; a dockhand overhears. The envoy's resentment (3) and distrust (-2) are admitted from the report it received; the dockhand's affection for the newcomer is admitted from the same words, overheard: opposing perspectives on one episode. Two guild members were absent. Neither can acquire an attitude from the envoy's evidence or from an unrelated perception of their own. One later receives the envoy's letter: before it arrives they still cannot resent the newcomer, and once it has, they can, only because of the letter. The other still holds nothing. No attitude toward the envoy was recorded, and the PC's own feelings cannot be read or written here. Further tests cover perceptions and anonymous reports not identifying the target, grounds about someone else or only about the guild not concerning the newcomer, current-belief grounds, groups holding nothing, a player taking an NPC over, future-dated changes, supersession and history, idempotent retries that recheck the present, authority, shape and bounds.

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
