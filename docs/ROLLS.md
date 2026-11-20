# Recorded rolls

**Status:** first slice of [Mechanical Resolution](roadmap/sprints/mechanical-resolution.md) (Sprint 075, Wave 9), milestone 1. It is a headless library with synthetic tests, built on [062's rules-family contracts](RULESETS.md):
- `grimoire.rolls` (pure): the dice an action needs, the one result a set of faces gives, the recorded roll and its codecs;
- `grimoire.rollstore` (adapter): `RollLibrary`, which draws dice once and records them in an authority ledger before anyone sees them.

There is no application control yet (Sprint 116 owns it), and nothing here applies an effect, spends a resource or chooses a partial result's cost. All scenarios are synthetic.

**First useful proof.** "Commit a recorded roll without narration re-rolling it": Mara, Ana's player character, slips past a guard. It is a 5e Dexterity (Stealth) check, DC 15, modifier +3. The die shows 4, so the total is 7 and the check fails. A narration take that says she slipped past is rejected (`contradicts_resolution`); a take that keeps the failure is accepted on the roll's authority (`resolution:c10`). Asking for the roll again, after a retry or after the library is reopened, reads the same failure back and draws no die, even when the next die would have been a 20 (`backend/tests/test_rollstore.py`).

## The guarantee is order

A re-roll is a result drawn after another result was seen. `roll` makes that impossible by fixing the order:

1. Every check runs first: the selected module, the actor, who may roll for it, the attempt not being 029's, and everything the append itself could refuse (an unusable authority, a field the store cannot hold, an entry too large for a batch, sized with the highest faces).
2. The dice are drawn and appended to the ledger.
3. Only then is the roll returned.

No result exists anywhere until it is on record. A crash or I/O error before the append leaves nothing anyone saw, so the next call draws afresh; one after it leaves the roll, and the next call reads it back (`backend/tests/test_rollstore_recovery.py` crash-explores this under the fault harness). Asking again with the same request and backing returns the recorded roll and draws nothing. Any other request under the same attempt (a lower DC, a reworded action) is `already_rolled`.

The ledger stores faces, never a result. Replay derives the result from the faces again with 062's profile, so a record cannot say that a failed roll succeeded: a hand-edited result is not even a field (`undecodable_entry`), and a second roll of one attempt is `already_rolled`.

## Dice and results

`dice_for(action)` lists the sides of each die, in the order the faces are recorded. `resolve(action, faces)` gives the one result they produce and checks it with 062's `check_result`, so the two can never disagree:

| Profile | Dice | Result | 003 result |
|---|---|---|---|
| `d20`, `5e-2024` | one d20 | the face plus the modifier, a success at or above the DC; a natural 20 is not an automatic success | `success` or `failure` |
| `wod`, `v5` | `pool` d10, the normal dice first and the `hunger` Hunger dice last | 062's successes and outcome | `success` for success, critical and messy critical; `failure` for failure and bestial failure |
| `pbta`, `dungeon-world-1e` | two d6 | the sum plus the modifier: 10+ strong, 7–9 partial, 6- miss | `success`, `partial` or `failure` |
| `ars`, `5e` | none: a season of study is arithmetic over the student's record (`no_roll`) | | |

A PbtA partial result owes a cost or a choice, and the roll never makes it: its `choice` is the fixed `PARTIAL_CHOICE` text saying one is unresolved. The director offers it and the player takes it; recording which is milestone 2.

## Binding narration

`RecordedRoll.resolution()` is a 003 `Resolution(campaign, attempt_id, result)`, in the result vocabulary 029's outcomes use, so `review_narration` rejects narration that contradicts it. `RollLibrary.resolutions(campaign_id)` lists one per roll.

The attempt narration names is the request id for a roll a player's contribution backs, and `director:<request_id>` for one nothing backs (029's convention). 003 binds a result by attempt id alone, so a director-controlled actor's roll can never bind narration about a player's attempt, whatever its request id.

## Who may roll

`RollLibrary(store, authority, handle, draw=system_draw)`. `handle` is the **roll key**: a trusted handle the authority issued. `draw` is the source of faces, the operating system's randomness by default; only whoever constructs the library chooses it, never a caller of `roll`.

`roll(selected, request, contribution=None, author=None)`:
- `selected` is the campaign's one selected rules module. With none, a roll is `mechanics_disabled`; a request for another module is `module_mismatch`, as 062's `propose` refuses it.
- The actor must be an instance of an existing campaign (`missing_campaign`, `unknown_actor`).
- Control is read from the [sovereignty ledger](PLAYER_SOVEREIGNTY.md#persistence-010b), as for a 029 frame. A player-controlled actor's roll is backed by the controller's own `action` contribution whose id is the request id (`pc_authorship` without one, `invalid_expression` for another's or another id), given through `author`, the controller's own trusted handle (`not_controller` otherwise). The roll key draws dice; it never acts for a player. An actor no human controls takes neither (`unexpected_backing`); one a non-human principal controls is `non_human_controller`.
- An attempt 029 already frames is adjudicated there, not rolled (`attempt_adjudicated`).
- The backing contribution is held to 029's bounds: its ids storable and at most 512 characters (`invalid_field`), its text at most 4000 (`contribution_too_long`).
- A draw that is no face of its die is `invalid_draw`, and nothing is written.

`state`, `rolled(campaign_id, attempt_id)` and `resolutions` read; rolls are keyed by attempt, so a director's `director:c10` and a player's `c10` are two rolls. Every refusal is a `RollError` with a `reason`, and writes nothing.

## Persistence

Each campaign has one authority ledger, `authority/rolls/<campaign_id>` ([STORAGE](STORAGE.md#authority-ledgers-010b)), with one entry kind, `roll`: `{"schema": 1, "kind": "roll", "recorded_at", "recorded_by", "value"}`, where the value is `{"request", "contribution", "faces"}`. The request is the full 062 `ActionRequest` (its module with its pinned ref, bindings and components, the binding, and the action's fields); the contribution is the 003 contribution or `null`. Decoding is strict and re-runs every constructor.

- **Replay** re-derives each result from its faces and refuses an attempt rolled twice. Anything else is `LedgerInvalid`, and the library fails closed. Control at the time of the roll and the actor's existence are checked when recorded, not on replay.
- **Reserved ids.** `roll_actor_ids` scans the batches raw and is part of [`campaign_taken_ids`](CHATS.md#taken-instance-ids), so a hand-deleted actor's id never goes to a new individual who would inherit its rolls. `campaign_authority_presence` counts this ledger too.
- **Not synced,** like the other authority ledgers.

## Known limits

- **The action is the caller's.** The DC, the modifier, the pool and the Hunger count are taken as given: nothing reads them off the actor's sheet or checks that the action is legal for the fiction (milestone 2 and 086's authored sheets).
- **One module is the caller's selection.** There is no stored campaign setting for the selected rules module yet; the caller passes it, and each roll records its module.
- **029 in one direction only.** `roll` refuses an attempt 029 frames, but 029 does not yet refuse to frame an attempt that was rolled. Nothing yet passes both libraries' resolutions to one review; a caller that did, with the two disagreeing, would have every outcome claim about that attempt rejected (`conflicting_resolutions`). Joining the two in one scene flow is milestone 3.
- **A director roll's attempt is its request id.** A retry under the same request id reads the roll back, but a fresh request id is a fresh roll. "No re-roll" for director rolls depends on the caller deriving the id from the turn, which milestone 3's scene runner does; until then it is the caller's discipline.
- **No effects.** A roll records a result; it applies no damage, condition or resource change, and journals nothing beyond itself (milestone 3).
- **No opposed rolls or chosen costs.** A WoD contest between two pools and a PbtA partial's recorded choice are milestone 2.
- **Not in the scene runner yet.** 035's runner reviews narration against 029's outcomes; feeding it these resolutions is milestone 3, and the controls are 116's.
- **Tail forgery.** As for every authority ledger, a valid batch appended at the tail with local file access is not detected by the chain; replay still refuses impossible faces and a second roll of one attempt.
