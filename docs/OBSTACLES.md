# Open obstacles

**Status:** first slice of [Obstacles And Agency](roadmap/sprints/obstacles-and-agency.md) (Sprint 040, Wave 5), milestones 1 and 2. It is a headless library with synthetic tests, built on [fiction-first adjudication (029)](ADJUDICATION.md):
- `grimoire.obstacles` (pure): obstacles, approaches, the frame rules, codecs and `derive`;
- `grimoire.obstaclestore` (adapter): `ObstacleLibrary`, which records obstacles in an authority ledger, frames attempts against them through 029's `AdjudicationLibrary` and derives each obstacle's state from 029's outcomes.

There is no dice or combat engine. 035's runner uses obstacles (Wave 6, 035 milestone 2: [Attempting an obstacle](SCENES.md#attempting-an-obstacle-040)): a labelling press names one, the labeller picks a suggested or novel approach and frames it through 029, kept apart from the decider, and what comes due is put on the turn's 021 effect as [structured changes](#structured-changes). All scenarios are synthetic.

**First useful proof.** "Support an unconventional solution without denying player choice": Mara, a player character, wants into a guarded archive. The archive suggests bribing the guard, fighting past him, slipping past the lantern and climbing in by a window (it has none). She bribes the guard instead of fighting. The guard's debts favour it, his loyalty to the archivist and his watch hinder it, and a report to the archivist or a pocketed purse are at stake. Decided a success, the archive is overcome and the bribe's price comes due: twelve crowns spent, and a guard who now expects a cut (resources and reputation). Fighting and sneaking stay real paths, and so does an approach no one suggested: forging a requisition under the archivist's seal, a clue that bears on none of the suggestions.

## The guarantee is player choice

An obstacle states a problem, never its solution. It records no result and no required approach, and every outcome is a 029 decision. Three rules keep the choice with the player:

1. **An approach is never refused for being unlisted.** The suggestions are offers, not a menu. A novel approach is framed like any other, and so is a suggestion the facts make hopeless: trying it is allowed, and 029 assesses it as impossible.
2. **The obstacle cannot shut out a novel approach.** Its facts may favour or hinder an unlisted approach, never prevent it (`novel_prevented`). No fact or escalation may prevent every approach (`blanket_prevention`), and an obstacle needs at least two suggestions that nothing it holds or can come to hold prevents, every stake escalated (`railroaded`): one credible answer is a puzzle with a key, before a failure or after one.
3. **The author's facts bind the labeller.** A frame for a suggestion carries exactly the facts the author said bear on it, with the author's bearing, and exactly the suggestion's risks. Difficulty is the author's, set before the player chose, not the labeller's in the moment: an attempt comes after every authored fact is established (`Obstacle.opens`), so the moment a labeller picks cannot leave one out. Once an attempt is linked, the obstacle can no longer be revised (`obstacle_engaged`).

Failure matters. A stake that comes true may **escalate**: it becomes a fact that bears on later attempts (the guard who reported a bribe will not take another, and watches the door harder). The obstacle stays open, and the next attempt meets what the last one changed.

## Obstacles

`Obstacle(obstacle_id, campaign_id, goal, actors, facts, stakes, approaches, provenance)`:

| Field | Meaning |
|---|---|
| `goal` | Prose for people, never read: what is on the far side. |
| `actors` | Up to 16 `InstanceRef`s of the campaign who stand in the way or hold a key (the guard). They must be instances of the campaign when recorded (`unknown_actor`). |
| `facts` | Up to 16 `ObstacleFact(fact_id, kind, text, established, source, bearings)`, sorted by id. `kind` is `constraint`, `clue`, `resource` or `motive`, for people. `established` and `source` are as for a 029 `Factor`. `bearings` maps a suggestion's id, or `"*"` (every approach the fact does not name, a novel one included), to `favours`, `hinders` or `prevents`; an approach it does not reach is untouched, so a clue may bear on no suggestion at all. |
| `stakes` | Up to 8 `ObstacleStake(stake_id, text, escalates, changes)`. `escalates` has the form of `bearings`: if the stake comes true on a failed attempt, it becomes an `escalation` fact with those bearings, established at that attempt's moment, once. A stake and a fact never share an id (`duplicate_id`). `changes` are its [structured changes](#structured-changes). |
| `approaches` | Up to 8 suggested `Approach(approach_id, text, risks, costs)`: `risks` are the stake ids an attempt this way puts at stake (`unknown_stake` otherwise); up to 4 `Cost(cost_id, text, changes)` come true when the approach overcomes the obstacle (the purse, the debt owed). |
| `provenance` | Who authored it: attribution, never authority. |

The bounds leave a 029 frame (32 factors) room for the labeller's own factors beside every fact and escalation that can bear on one attempt.

### Structured changes

A cost or a stake is prose for people. Its optional `changes` (at most `MAX_CHANGES` = 2) say what it does to the world, in terms 021 can accept. They are written by the author before any player chose an approach, like the facts, so the price of a success and the weight of a failure are never a labeller's or a model's:

| Change | Meaning |
|---|---|
| `RelationChange(kind, predicate, subject, object, agent=None)` | A 017 relation that `begin`s or `end`s, with a 017 predicate (`unknown_predicate`), between two different instances (`self_relation`). |
| `HoldingsChange(kind, holder, item, amount, target=None, agent=None)` | One 023 line: `add` or `consume` `amount` of `item` at `holder`, or `move` it to `target` (`same_holder`). 023's own rules on the item and amount apply. No `adjust`: it needs the count seen when it is applied, which an author cannot know. |

The acting character is not known when an obstacle is authored, so a change names it as `ACTOR` (`"actor"`). Every other reference must be one of the obstacle's own `actors` (`unlisted_actor`), and so must a novel approach's costs' (`resolve_approach`), so every reference is reserved with the obstacle's actors. `agent` is whose voluntary act a change is (the actor who hands over the purse), or none for something that befalls them; 021 decides who endorses it. Anything else is `invalid_field`.

A `Consequence` that `due()` lists carries its cost's or stake's changes. 035's runner puts them on the turn's 021 effect with the acting character for `ACTOR`, and an acceptance checks they are still there ([SCENES](SCENES.md#attempting-an-obstacle-040)). The labeller's own stakes, for a novel approach, have none.

## Attempts

An attempt is a 029 `ActionFrame` and an approach: a suggestion by id, or a novel `Approach` that carries its own risks and costs, chosen with the player's action. A novel approach may not reuse a suggestion's id (`not_novel`). `check_attempt(obstacle, facts, attempt, frame, after)` refuses a frame that does not agree with the obstacle as it stands (`facts` are its own and the escalations so far):

| Rule | Refusal |
|---|---|
| A frame factor whose id is an obstacle fact is that fact: same text, establishment and source. | `fact_changed` |
| For a suggestion, it has the author's bearing on that suggestion; a fact the author said does not bear on it is left out. | `fact_changed`, `fact_misapplied` |
| For a novel approach, the labeller chooses `favours` or `hinders` for any fact, but a `"*"` fact keeps its bearing and none prevents. | `fact_changed`, `novel_prevented` |
| Every fact established before the attempt that bears on the approach is in the frame (for a novel approach, the `"*"` facts). | `missing_fact` |
| The frame's stakes are exactly the approach's risks; an obstacle stake keeps its declared text. A suggestion risks only obstacle stakes; a novel approach may also risk something the author never foresaw. | `stakes_mismatch`, `stake_changed` |
| An attempt comes after every authored fact (`opens`), so its moment cannot choose which facts bear. | `premature` |
| An attempt comes after the one before it, so it cannot dodge an escalation. | `out_of_order` |

Labels beyond the obstacle's are the labeller's, as in 029: the actor's training, a relation, a power.

`required_factors(facts, approach_id, sequence)` lists what a frame must carry; `ObstacleLibrary.required` gives it for an obstacle as it stands.

## Derived state

`derive(obstacle, revision, attempts, frames, outcomes)` replays an obstacle's attempts, oldest first, against their latest 029 frames and recorded outcomes. Each attempt is checked against the facts as they stood when it was made, using the frame its outcome decided:

- **pending:** not decided; the latest attempt is pending with no frame when its link was written and its 029 frame was not (a crash), until it is framed or superseded;
- **void:** an attempt the next attempt's link `supersedes`: when that link was written, its frame had never been written, or 029 could not decide it because its actor had changed hands; or an undecided attempt `withdraw` withdrew, on the same grounds (035 withdraws one whose turn passes undecidable). It stays void for good: control may come back and 029 may decide it after all, but that decision brings no consequences, `due()` leaves it out and the obstacle is not diverged by it. (A link written before links named what they superseded has no `supersedes`; an undecided attempt with a later one after it is then void too, but decided afterwards it counts in link order, and what follows must agree with it.)
- **failed:** the decided consequences are due, and those that escalate become facts;
- **overcame:** a success or a partial success; the decided consequences and the approach's costs are due, and the obstacle is `overcome`;
- **diverged:** the attempt no longer agrees with the obstacle, or the records are out of the order the service keeps (`attempt_out_of_turn`). The obstacle is `diverged` and takes no new attempt; its `problem` says why. Revising a diverged pending attempt back into agreement restores it.

`ObstacleState.due()` lists every `Consequence(kind, consequence_id, text, changes)` the decided attempts brought (`stake` or `cost`), for a caller to apply.

## The store service

`ObstacleLibrary(store, authority, handle)` takes the same **adjudication key** as 029 (a trusted handle the authority issued) and frames through an `AdjudicationLibrary` over the same authority (`adjudication`). Decisions are made there.

| Call | Does |
|---|---|
| `record(obstacle)` | Records a new obstacle, or a revision of one no attempt has engaged. The campaign must exist (`missing_campaign`). Recording the latest revision again writes nothing. |
| `frame(obstacle_id, frame, approach=..., contribution=None, author=None)` | Checks the frame (`check_attempt`) and runs 029's own checks without writing (`AdjudicationLibrary.check`: a player character's action is backed by the player's own contribution), links it, then records it through 029's `frame`. A frame for the pending attempt is a 029 revision, keeps its approach (`approach_changed`) and is checked again; it may bring a diverged pending attempt back into agreement. |
| `withdraw(campaign_id, obstacle_id, attempt_id)` | Withdraws the obstacle's latest attempt for good (a `withdrawal` entry): it is `void` from then on, whatever control does. Only while it is pending, or diverged by its own frame (any problem but `attempt_out_of_turn`), and undecided (`not_pending`, `already_withdrawn`), and while 029 cannot decide it (`attempt_decidable`: no outcome, and no frame or an `undecidable` one); `not_linked`, `withdrawal_mismatch` (not the latest). A frame for it afterwards is `attempt_withdrawn`, and 035 decides an attempt at an obstacle only under the same lock, after reading that the ledger does not hold it void (`attempt_withdrawn` otherwise), so a decision and a withdrawal never cross. |
| `check(obstacle_id, frame, approach=..., contribution=None, author=None)` | Every check `frame` makes, writing nothing: raises the refusal `frame` would raise now. 035 checks before it marks a turn framed, and frames after. |
| `linked(campaign_id)` | Every attempt this ledger links, with its obstacle: what a caller checks its own record of an attempt against (035 checks a turn's `against`). |
| `state`, `states`, `entries`, `required` | Read. |

- **One attempt at a time.** A new attempt waits until the pending one is decided (`attempt_pending`): it meets what that one changes. The exception is a pending attempt that cannot be decided now, because its frame was never written or its actor changed hands (029's `control_changed`, through `AdjudicationLibrary.undecidable`): the new controller's action is a new attempt, and it supersedes the old one. The new link names the attempt it supersedes, so the old one stays `void` even if control returns and 029 decides it then. Without a new attempt, `withdraw` voids it the same way, before control can return. Refused too: `unknown_obstacle`, `unknown_approach`, `obstacle_diverged`, `already_overcome`, `attempt_linked` (another obstacle's attempt) and `already_decided` (an attempt decided before it was linked, so a known success cannot be pinned to an obstacle afterwards). A refusal writes nothing.
- **Order of writes.** Under the authority's lock, every check runs first (the obstacle's and 029's), then the link is appended, then 029 records the frame. A 029 frame made this way never exists without its link, so a decided attempt cannot escape the escalations its obstacle would bring, and the obstacle is fixed (`obstacle_engaged`) before the player's frame is on record. A crash between the two writes leaves a pending attempt with no frame: framing it again records it, and a new attempt supersedes it (`void`). A superseding link that reached the disk already names the attempt it superseded, so a crash before its frame cannot bring that attempt back. `backend/tests/test_obstaclestore_recovery.py` crash-explores `frame` (a first attempt and a superseding one) and `record` under the fault harness: after recovery the libraries read nothing, the link alone or both, never a frame without its link, and a torn link batch reads as nothing.
- **029 refusals stay 029's.** They raise `AdjudicationError`; the obstacle's own raise `ObstacleError`. Both carry `reason`.

## Persistence

Each campaign has one authority ledger, `authority/obstacles/<campaign_id>` ([STORAGE](STORAGE.md#authority-ledgers-010b)), with three entry kinds: `obstacle` (a revision), `withdrawal` (`{obstacle_id, attempt_id}`) and `attempt` (`{obstacle_id, attempt_id, approach_id, novel}`, the novel approach in full or `null`, and `supersedes`, the id of the pending attempt it superseded, only on a link that superseded one, so links written before it read back unchanged; it is never `null`). Each entry is `{"schema": 1, "kind", "recorded_at", "recorded_by", "value"}`; values are the exact JSON of `grimoire.obstacles`' codecs, and decoding re-runs every authoring check. A cost or stake records `changes` (each `{"change": "relation" | "holdings", ...}`, with `"actor"` for `ACTOR`) only when it has some, so entries written before them read back and encode unchanged; an empty list, an unknown key or a wrong type is `undecodable_entry`.

- **Replay** re-checks what this ledger alone holds: each obstacle (a railroad read back is still refused), no revision after an attempt, each link's obstacle and approach, no attempt linked twice, and a link supersedes only the attempt linked just before it to the same obstacle (`supersedes_mismatch`); a withdrawal names its obstacle's latest attempt, once (`not_linked`, `withdrawal_mismatch`, `already_withdrawn`). Anything else is `LedgerInvalid`, and the library fails closed.
- **Derived, not recorded.** An obstacle's progress is never written: it is derived from this ledger and 029's on every read. What needs both ledgers (an attempt linked while another was pending, a frame revised around the obstacle through 029 directly) is reported as a diverged obstacle, not as ledger damage.
- **Reserved ids.** `obstacle_actor_ids` scans the batches raw for `actors` and is part of [`campaign_taken_ids`](CHATS.md#taken-instance-ids), so a hand-deleted guard's id never goes to a new individual who would inherit its post. `campaign_authority_presence` counts this ledger too.
- **Not synced.** Like the other authority ledgers, it is not part of 019's sync ([028](roadmap/sprints/app-managed-device-sync.md)).

## Known limits

- **The labeller is trusted beyond the obstacle's facts.** As in 029, a labeller can add its own hindrance or preventing factor to any attempt, and can call a suggestion novel to escape its facts and stakes (the intent is never read). In 035 it chooses the approach; the obstacle's part of the frame is added by rule, so it never cites or rewrites an obstacle fact. The obstacle guarantees only that its own facts bind suggestions and never shut out a novel approach.
- **Consequences apply only as authored changes.** A cost or stake without `changes` is prose: nothing applies it. Changes are bounded to 017 relations and 023 lines (no `adjust`, no knowledge, no ability use), and a novel approach's labeller stakes make nothing due. Applying them is 035's, through a proposed 021 effect that needs every key; nothing proposes it without a press.
- **A due change can fail when applied.** The world may not take it: a relation that already holds (021's `precondition_failed`), a purse the actor cannot pay (023's `insufficient`), two consequences making the same change, or an actor's own act after the character changed hands. The director then proposes the turn's effect again with that change waived, by its id and with a reason ([SCENES](SCENES.md#attempting-an-obstacle-040)). A waiver is the effect's: `due()` still lists the change, and the obstacle's state is the same either way.
- **One attempt at a time, one actor each.** Several characters working one obstacle together (one distracts, one slips past), and contests between actors, are later work (029 milestone 3).
- **Escalation is coarse.** A stake escalates once, with fixed bearings, from a failed attempt only; a partial success's consequences do not escalate, since the obstacle is overcome. Graded escalation and clocks are later.
- **No retreat record.** Declining or leaving an obstacle needs no record: it stays open and nothing forces an attempt. Retiring an obstacle the story has moved past is later work.
- **Actors are checked when recorded.** Replay does not re-check that an obstacle's actors still exist; their ids stay reserved.
- **A diverged obstacle stays diverged once decided.** Until the diverged attempt is decided, revising it back into agreement (here or through 029) restores the obstacle. 029 decisions are final, so once a diverged attempt is decided the obstacle takes no further attempts; the author records a new obstacle.
- **Only attempts framed here are attempts.** A frame recorded through 029 directly, never linked, is not an attempt against any obstacle, and once decided it cannot be linked (`already_decided`).
- **Forgery across ledgers.** Replay checks each ledger on its own, so a hand-forged link to an attempt 029 decided before the link is not caught (the service refuses to write one), and neither is a hand-forged `supersedes` or `withdrawal` that voids an attempt 029 decided or could decide (the service voids only one it cannot).
- **029 still holds a void attempt's decision.** Deciding a superseded attempt after control returns is 029's to allow; its outcome and `resolutions` keep it. The obstacle ignores it, so a caller applying consequences goes by `due()`, not by 029's decisions.
- **A link whose frame was never written still engages the obstacle.** It cannot be revised afterwards, even though no frame is on record.
- **Factor sources are attributed, not verified,** as in 029.
- **Railroad detection is structural.** It counts suggestions nothing prevents; it cannot tell whether a hindered suggestion is credible, or whether outcomes over many attempts favour one approach. Evaluating railroading in play is later work.
- **Procedural obstacles** (milestone 3: optional encounter design grounded in actor motives and the setting) are not built.
