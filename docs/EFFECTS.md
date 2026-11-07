# Accepted turn effects: acceptance, one-time publication, revocation and scene wrap-up

**Status:** first slice and next slice of [Turn Effects And Wrap Up](roadmap/sprints/turn-effects-and-wrap-up.md) (Sprint 021, Wave 3). The next slice lets an accepted effect also change [023 counted holdings](HOLDINGS.md) and charge [025 ability costs](ABILITIES.md), each attempt exactly once ([Holdings and ability uses](#holdings-and-ability-uses)). It is implemented as:
- `grimoire.effects`: pure values, payload codecs, two-key authorization, planning against the 017 graph, and ledger and holdings-receipt checks (`parts.py` holds the holdings and ability parts);
- `grimoire.effectstore`: the store-backed `TurnEffectsLibrary` (`parts.py` holds its holdings flow);
- two additive record families in [the native store](STORAGE.md#accepted-events-and-wrap-ups-021), and a second accepted-event schema, `grimoire.accepted-event/1`;
- an additive amendment to [017](GRAPH.md#reopening-an-ending-021): reopening a relation's ending in place;
- an additive 023 read, `HoldingsLibrary.view(holder)` ([HOLDINGS](HOLDINGS.md#service-grimoireholdingstoreholdingslibrary)).

There is **no app and no model call**, and 035's text runner accepts only structured effect proposals, with every key ([SCENES](SCENES.md#accepting-an-effect)): its kept lines are transcript, and no model proposes an effect yet. The scenarios are synthetic.

## What it means

A fictional result becomes **playable truth** only through an explicit **acceptance** that holds the right keys. It is written **once**, in the same store transaction as the state change it implies. After that:
- a reroll or an alternate response cannot apply it again;
- nor can a retry of the same acceptance, a scene wrap-up or a later re-mention.

A revised turn is undone only by an explicit **revocation** that holds the same keys. Revocation restores the state in place, strictly latest first. Any disagreement between the event ledger and the relation or holder files is **reported, never hidden**.

The state is 017 relations and 023 holdings. Every holdings change and ability use belongs to an **attempt**, which is applied and charged once while an event holds it: rerolls, retries, crashes and recovery never spend or charge twice, and a late retry after a revocation is refused.

## The five stages

| Stage | Type | Persisted here | Can change canonical state |
|---|---|---|---|
| draft narration (rerolls, swipes, alternate responses) | none; 035/036 own transcripts | no | never |
| proposed effect | `EffectProposal` (a pure value) | no | never by itself |
| accepted fictional event | `AcceptedEvent` | yes (`accepted/`) | only through its acceptance transaction |
| committed operation | a `Receipt` (and an `AcceptedHoldings` receipt) inside the accepted event | yes, with the event | it *is* the state change: the 017 relation files and the 023 holder files |
| retrospective memory summary | `SceneWrapUp` | yes (`wrapups/`) | never: it has no operation field |

```text
narration/labeller ──proposes──▶ EffectProposal            (no authority; proposed_by is attribution only)
player input (003) ──PlayerContribution, kind "action"──┐
player confirms the operations ──Endorsement─────────────┤   authorship key (only for a PC's voluntary acts)
director/adjudication ──the library's trusted handle─────┤   outcome key (always)
                                                         ▼
  accept(proposal, controls, contributions, endorsements)
    ├─ authorize: 003 control; a PC's voluntary op needs the contribution AND the endorsement   (pure)
    ├─ fail closed on a damaged or inconsistent ledger; duplicate check with receipt verification
    ├─ sequence floor; each attempt held by at most one event; plan against one 017 graph       (pure)
    ├─ plan the holdings operation with 023's HoldingsLibrary.apply; record each ability use's charges
    └─ EffectChange.preview() → a human approves → commit(approvals)
          one store transaction: accepted/<id>.json + relation ends/adds (provenance effect:accepted/<id>)
                                 + holder files (023 entry, cause effect:accepted/<id>)
                                                         ▼
  continuity(as_of, requested_by=DIRECTOR)  → the director context for the next response (027/035 render it)
  wrap_up(scene)                            → cites the scene's accepted events; no operations; never actor memory
  revoke(event)                             → same keys; latest first; retract what it began, reopen what it ended,
                                              apply the inverse holdings operation; its charges no longer count
```

## Operations

State changes are changes to [017 relations](GRAPH.md), described by operations, and to [023 holdings](HOLDINGS.md), described by an effect's holdings part ([below](#holdings-and-ability-uses)). An effect may also record the [025 ability costs](ABILITIES.md) its uses charge.

| `Op.kind` | Meaning | Written as |
|---|---|---|
| `begin` | the fact starts holding at the event's sequence | a 017 add with `since = moment`, provenance `effect:accepted/<event_id>` |
| `end` | the one walkable relation recording the fact stops holding there | a 017 `end` with `until = moment`, ending provenance `effect:accepted/<event_id>` |

`Op(kind, predicate, subject, object, agent=None, backed_by=None)`:
- `predicate`: one of 017's.
- `subject` and `object`: exact `InstanceRef`s of the campaign, and different.
- `agent`: the individual whose **voluntary action** causes the change, or `None` for an external effect or a world fact.
- `backed_by`: the 003 contribution behind a player-controlled agent's action.

Examples: a transfer ("Wren gives key-1 to Tam") is `end(carries, wren, key-1)` plus `begin(carries, tam, key-1)`. Joining a guild is one `begin(member_of, …)`.

`EffectProposal(campaign_id, chat, moment, summary, operations, proposed_by, attempt_id=None, holdings=None, abilities=())` (all text must be storable UTF-8; a lone surrogate is `invalid_field` here, not a store error later):
- `moment.scene_id` and `moment.turn_id` are required ID segments.
- It holds up to 32 operations. No two may touch the same fact (`conflicting_operations`). It needs at least one operation, holdings part or ability use (`no_operations`).
- The summary is at most 4000 characters.
- `proposed_by` is one of `narration | player | director | adjudication`. It is attribution only.

**Fingerprint.** The fingerprint identifies the effect, so a reroll that words it differently is still a duplicate:
- **It covers:** the chat, the moment's sequence, scene and turn, and each operation's kind, fact and agent. Operations are hashed in sorted order, so the same operations in another order are the same effect.
- **With [holdings or ability uses](#holdings-and-ability-uses),** it appends, after the sorted operations: the sha256 of `attempt_id`; one line per holdings line, **in order** (order matters to 023), with its kind, holder, item, amount, seen, to, target and agent; then, sorted, one line per ability use with its attempt id's sha256, actor, ability, effect, needs and conditions met.
- **It does not cover:** the summary, the proposer, the backing, nor the holdings note, the uses' provenance, classification, rulings or charges.

It is a sha256 over a hand-rolled canonical text, pinned by a golden-vector test, because stored endorsements carry it. An effect with no holdings and no ability use hashes exactly as before (the legacy vector is unchanged); a second golden vector pins the extension.

### Holdings and ability uses

An effect may also carry, with or without operations:
- **`holdings`**, an `EffectHoldings(lines, note=None)`: the lines of **one** 023 operation, applied all-or-nothing with the event record. `lines` holds 1–32 `ActedLine(line, agent=None, backed_by=None)`; a bare 023 `Line` is taken as one with no agent. `agent` and `backed_by` mean what they mean on an `Op`, so a trade between two players' characters is two lines with two agents, each backed and endorsed. The lines must form a valid 023 `Operation`: one campaign (`campaign_mismatch`), at most 8 holders (`too_large`), otherwise `invalid_field`. `note` follows 023's note rules.
- **`abilities`**: a tuple of up to 8 of the caller's 025 `AbilityAssessment`s, one per ability use (more is `too_many_operations`). Each actor is in the campaign (`campaign_mismatch`).

Examples: "Wren pays Tam 2 coins while Tam hands Wren a knife" is two `move` lines. "Orin hops with the thruster pack" is an ability use charging `cooldown` and `fuel_burn`, plus a holdings part `consume(orin, fuel, 1)` under the same attempt. Neither needs a relation operation.

**Attempts.** Every holdings part and ability use belongs to an attempt, and one attempt is applied and charged at most once while an event holds it (Accepting, step 10). An effect's attempts are its `attempt_id` and each use's `attempt.attempt_id`:
- `attempt_id` names the holdings part. It is required exactly when `holdings` is set (`missing_attempt`; one without holdings is `invalid_field`). It is nonblank text of at most 512 characters, storable UTF-8: the cap bounds a stored record.
- Each ability use is its own attempt, under the same text rule. Their ids are pairwise distinct (`duplicate_attempt`). For a player-controlled actor the attempt id is the action contribution's id (025), which is also its backing.
- The holdings attempt may equal one use's attempt: Orin's hop consumes the fuel it burns, as one attempt.

`permitted` is **not** checked when the proposal is built. Accepting checks it after the duplicate check (step 5), so a retry of a charged use whose re-assessment has changed since is still recognised as a duplicate.

**The holdings operation's identity** is derived, never chosen:
- `op_id` is `h-` plus 32 hex digits of sha256(campaign_id NUL fingerprint). A retry of the same effect derives the same id, and a revision of the attempt another.
- `cause` is `effect:accepted/<event_id>`.
- Its undo is `op_id` plus `-r`, with `cause` `effect:revoked/<event_id>` and no note. Every line is inverted (add and consume swap, a move goes back, an adjust goes from `to` back to `seen`), in reverse order.

**Charges.** Each permitted use is recorded as an `AbilityCharge(attempt, classification, ruling_id, charges, domain)`. `charges` are the assessment's `charges` as sorted, unique `(ability_id, cost_id)` pairs: cost text is prose, so the record keeps ids. `ruling_id` is the approving ruling of an extrapolation, new ability or combination, and `None` for a declared use. A charge is a record of the price paid; a counted resource is consumed by the same effect's holdings part.

**Known limits** of these parts:
- The ability assessment is the caller's, like `controls`. 029 computes and authenticates it: a caller gets it from `AdjudicationLibrary.ability_use`, and `check_effect` verifies a proposal's ([ADJUDICATION](ADJUDICATION.md#the-store-service)). 021 itself does not call 029.
- Still one accepted effect per turn: several actors' acts are lines and uses of that one effect.
- Orphan detection and duplicate recognition are bounded by the holders' logs, and so is step 10's check that an applied undo landed on every holder: an undo whose event file landed but not one holder's file, followed by more than `MAX_LOG` (1000) operations on that holder, reads as confirmed and frees the attempt. Beyond them is 030's; completing a partial publication is 030's `EffectRecovery` ([RECOVERY](RECOVERY.md)); a restored holder file forgets its operations ([HOLDINGS](HOLDINGS.md#known-limits)).
- A skipped holdings undo is reported until 030 can record a human repair.
- A paired holder blocks a holdings undo ([Revoking](#revoking-the-undo-of-a-revised-turn)).
- A holder file that cannot be read at all blocks an acceptance with holdings, not one without, except one that reuses an attempt of a revoked event whose holder files must be read to tell whether its undo landed.
- Charges record cost ids, not cost text.

## Two keys

- **Outcome key (director/adjudication), always.** `TurnEffectsLibrary(store, handle, *, issued, clock, new_id, owner=None)`:
  - It refuses a handle that is not trusted, or that `issued` does not recognize (`AuthorityError`, as in 010b).
  - It only *checks* handles, through the `issued` predicate. **By convention it never mints one**, and a static test pins that `handle_for` does not appear in it.
  - Every commit is a store transaction whose approval policy can require a human to approve the exact preview. The record stores `committed_by`, the handle's principal.
  - *When* director acceptance needs human review is 035's policy (open question 1).
- **Authorship key (player), for every act whose agent is player-controlled:** an op, a holdings line (its `agent`), or an ability use (its actor). This is decided by `controller_of` over `controls`; ambiguous control raises `AuthorityError`. Two things are needed:
  - **The player's own action contribution.** `backed_by` must name a 003 `PlayerContribution` of kind `action`, for the same campaign, scene, turn and actor, submitted by the current controller.
    An ability use has no `backed_by`: its backing is the contribution whose id is its attempt id.
    - None → `pc_authorship`.
    - One that mismatches → `invalid_expression`.
  - **An `Endorsement(contribution_id, handle, fingerprint)`.** The handle must be a trusted `human:` handle that `issued` recognizes, whose principal is the submitter. The fingerprint must be the one the player was shown.
    - The controller must itself be a `human:` principal; otherwise `non_human_controller` (only an authenticated human authors a PC's act, as 010b mints trusted handles only for humans). **Obligation on 035/010b:** record player controllers as `human:` ids.
    - None → `not_endorsed`.
    - Anything wrong → `invalid_endorsement`.

    The record stores `{contribution_id, principal, fingerprint}`, so the endorsement is bound to exactly these operations. Two endorsements for one contribution are refused as ambiguous (`invalid_endorsement`). A contribution whose text is over 20 000 characters, or whose submitter is over 512, is refused with `contribution_too_long`, since 003 caps neither. (A `backed_by` over 512 is already refused when the proposal is built.)
- **Director-domain acts** (an NPC or unassigned agent, or `agent=None`) need only the outcome key. An op or holdings line refuses `backed_by` there (`unexpected_backing`). Sovereignty never guarantees success: an NPC taking the PC's key is the director's to accept.
- **Domains are recorded per act:** per operation, per holdings line (`AcceptedHoldings.domains`) and per ability use (`AbilityCharge.domain`). One endorsement per contribution covers the whole fingerprint, so a two-PC trade needs both players' endorsements, and an endorsement made for another holdings part is `invalid_endorsement`.

**The summary is director narration.** The endorsement covers the operations (the fingerprint), not the `summary` text, so that a reroll worded differently is still the same effect. The summary is stored with the event and carried into the 012 bridge, so it must stay neutral, director-authored world description: it never authors a PC's speech, interiority or commitments (003). **Obligation on 035:** show it to the player with the operations, and keep it to what the operations say.

Narration holds neither key. A player alone can never make their character's action succeed (a theft, joining a guild that refuses): the outcome needs the director key.

**Strength, stated plainly.** An `Endorsement` is a statement, not a one-time token. Code that kept one could present it again to re-accept the *same* operations after they were revoked (an effect with an attempt is then refused `attempt_revoked`). It can never be used to revoke: revocation needs a separate endorsement, over a fingerprint bound to that event (see Revoking). As with 010b, the guarantee is only as strong as 035's handle minting: any trusted code that holds the authorizer can mint `human:ana`, and the library itself cannot. 035's runner mints one only to present again an endorsement that the authority's own audit records as given with that player's own handle ([TOOL_AUTHORITY](TOOL_AUTHORITY.md)). In **solo play** one human holds both keys. The rule then guarantees two explicit, separately recorded decisions (endorse the mapping, approve the outcome), not two people.

**Recorded for audit:** the backing contributions, the endorsements, and the control in force for every agent (`controls`). `controls` comes from the caller. **Obligation on 035:** load it from 010b's `SovereigntyLedger`. 035's runner does, and for 030's `adopt` after a restart it replays the ledger to the position the acceptance recorded, so the retry is checked under the control it was first authorized under ([SCENES](SCENES.md#accepting-an-effect)).

003 delegations (`auto_complete` …) are **not** honoured here: a PC's voluntary act always needs its player's own contribution.

**Limit:** mislabelling, a PC's voluntary act proposed as `agent=None`, is a labeller error, as in 003. The remedy is the narration referee's mislabel review plus revocation.

## Accepting

`accept(proposal, *, controls, contributions=(), endorsements=()) -> EffectChange` runs these steps in order:

1. **Inputs.** The campaign must be present (`missing_campaign`, also for a look-alike folder), and the chat record must exist (`missing_chat`).
2. **Snapshot.** Under one store lock, read every accepted-event file of the campaign and its 017 graph.
3. **Fail closed on the ledger.** Each of these blocks acceptance:
   - an accepted-event file that cannot be decoded (`undecodable_event`), including a newer schema;
   - any store problem under `campaigns/<c>/accepted`, at the campaign folder, or a look-alike `Accepted/`;
   - a duplicate ordinal, or active events whose ordinal order contradicts their sequence order (`inconsistent_ledger`).
4. **Duplicate.** Look for an active event with the same fingerprint:
   - **its receipts verify:** a no-op. `duplicate_of` is set, `preview()` is `{}`, and `commit()` writes nothing and returns that event. This is the path for a reroll, an alternate response or a retry after a crash. It runs **before** authorization: a no-op writes nothing and needs no keys, so a retry after control changed hands, or with a stale endorsement, is told that the effect is in force rather than refused for authority. Its receipts are the relation receipts and, with a holdings part, the holdings receipt, checked leniently ([below](#holdings-receipts));
   - **they do not:** refused `receipt_mismatch`. The way out is `revoke(..., partial=True)`. If a relation file is damaged at the same time (step 6), the refusal is `undecodable_relation` instead, and a touched holder file that cannot be read is `undecodable_holdings`, so damage is named, never reported as a mismatch.

   Receipts name exact relation and holder files, so a damaged *unrelated* file does not refuse a duplicate. A receipt mismatch on any *other* event does not block an unrelated acceptance. It stays reported by `continuity` until it is repaired: revoke back to that event with `partial=True`, or hand-repair the file. One editorial edit therefore never freezes a campaign's play.
5. **Permitted.** Every ability use must be `permitted` (`ability_not_permitted`, naming its classification). This comes after the duplicate check, so a retry of a charged use whose re-assessment has changed since is still a no-op.
6. **Fail closed on the relations.** A relation file of the campaign that cannot be decoded, or any store problem under its `relations/` (a link, a look-alike `Relations/`), refuses a new effect with `undecodable_relation`, because it could hide the fact a precondition must see. Unlike 017's own writes, which an undecodable file never blocks, an acceptance fails closed. The commit re-checks it too.

   `revoke` fails closed the same way (`undecodable_relation`, also with `partial=True`), so a damaged receipt or a hidden overlap is never skipped silently. **Limit:** 017's own `reopen`, called directly, does not block on undecodable files.

   The repair for any of these is to fix the file or move it out of the library by hand.

   **No paired actor.** Then, before authority, no operation's endpoint or agent, and no holder, holdings line agent or ability user, may share its id with an instance of another kind in the campaign (`instance_id_conflict`, naming the operations and actors): `load()` leaves both members of such a [pair](IDENTITY.md) out, so the id is no one actor. A duplicate (step 4) writes nothing and is still reported as in force. Revocation does not check it for relation operations, so a pair formed after an acceptance never blocks undoing them; it does block a holdings undo ([Revoking](#revoking-the-undo-of-a-revised-turn)).
7. **Authorize** (two keys, above).
8. **One accepted effect per turn.** An active event for the same chat, scene and turn with a *different* fingerprint refuses the acceptance (`turn_already_accepted`). Another swipe's effect needs the accepted one revoked first, and several things that happen in one turn are one proposal (up to 32 operations, one holdings part and up to 8 ability uses, so several actors' acts fit in one effect). This also stops a turn retried at a newly allocated sequence after a later event restored the state, which the preconditions alone would let through.
9. **Sequence floor.** The sequence must be at least the highest sequence of any active event (`stale_sequence`); equal sequences are allowed (different turns or chats at one moment). Sequences are campaign-wide. **Obligation on 035:** allocate one non-decreasing sequence across all chats of a scope.
10. **Attempts.** For each attempt of the proposal, over every event file, active or revoked:
    - an active event holds it → `attempt_reused`;
    - a revoked event holds it with the same fingerprint → `attempt_revoked`: a late retry of a revoked effect is refused, never accepted again;
    - a revoked event whose holdings undo was skipped while its change may still be in force (`Revocation.holdings_left`) holds **every** attempt it records, holdings and ability uses alike → `attempt_revoked`. A revision reusing the ability attempt with a fresh holdings attempt would otherwise spend again for the same use;
    - a revoked event whose applied undo some holder file does not confirm (the revocation landed its event file but not every holder file) holds every attempt it records too → `attempt_revoked`, since part of its change is still in force. An unreadable holder file there is `undecodable_holdings`. A revoked event whose undo was skipped as `never applied` holds every attempt again once a touched holder logs its forward change after all (a holder file restored from after the effect); continuity then reports it as `receipt_mismatch` (`applied since`). The holder files this check read are pinned to the commit, so one changed before it is a `StaleWrite` ("holder file changed").

    A revision (another fingerprint) of a cleanly revoked attempt is allowed, and so is one whose skipped undo was `never applied` (see Revoking). These checks come after steps 8 and 9.
11. **Ordinal.** The new event gets `ordinal = 1 + max` over every event file, active or revoked. This is the order of the undo stack.
12. **Plan** against the graph at `as_of = sequence`:
    - **`end`:** needs exactly one walkable relation recording the fact, with no ending and `since < as_of`.
      - None, a bounded stint or a same-sequence start → `precondition_failed`.
      - Two → `ambiguous_relation`.
    - **`begin`:** both endpoints must have instance files (`missing_endpoint`), and no unretracted relation of the fact may overlap `[as_of, ∞)` (`precondition_failed`).
    - **any operation:** an `agent` must have an instance file (`missing_agent`), and so must every holdings line's agent and every ability user.

    **Nothing is ever matched by name**: key-2 is never "the key".
13. **Holdings,** once the event id is allocated (step 14), since the operation's cause names it:
    - every touched holder file is read; one that cannot be read is `undecodable_holdings`;
    - **orphans:** an entry in a touched holder's log whose cause is an exact 021 token naming an event that does not record it refuses with `orphan_effect` (naming the holders). It is the landed half of a partial publication, found even when a retry regenerated its attempt id. Its repair is a human's ([Orphans](#orphans));
    - the operation is planned with `HoldingsLibrary.apply(op)`, with no `basis` and no pin: like its relation operations, an acceptance is a fresh plan, and the event's seqs are the basis later operations pass. `already_applied` (this effect's derived op id is logged, yet no event records it) and `op_id_reused` (an altered copy of it) are `orphan_effect`. Every other 023 refusal passes through with its own reason (`insufficient`, `stale_count`, `missing_holder`, …), and an undecodable holder is `undecodable_holdings`;
    - the receipt is `AcceptedHoldings(holdings, domains, op_id, seqs)`: each line's domain, the 023 op id, and each holder's logged seq. Each ability use is recorded as its `AbilityCharge`.
14. **Commit.** One transaction writes:
    - the new `accepted/<event_id>.json`, create-only;
    - each 017 end rewrite, under CAS on the etag read;
    - each 017 add, create-only;
    - each touched holder file, under CAS on the etag read (023's `puts`).

    Event and relation ids are allocated by exact presence. On a library with a device identity ([DEVICE_SYNC](DEVICE_SYNC.md#per-device-ids)) a new event id, and the id of every relation an acceptance begins, also ends in that device's tag (`<id>-<tag>`, `grimoire.devicetag.device_tag`), so two paired devices that each accept an effect offline never write one id with two contents; the tagged id is the one every check below sees. A library never given an identity allocates as before, and events and relations made before `identify` keep their ids. 030's `adopt` begins the relations still missing under the same tagged ids, and is stale at commit too if the identity changed. A damaged identity refuses a new acceptance (`device_identity`; a duplicate still writes nothing), and an acceptance planned under another identity (or none) is `StaleWrite` at commit ("device identity changed"). A generated id too long to take its tag (over 119 characters) is refused, never cut. A new event id is also never a 020 event's id, nor one that a 021 token (`effect:accepted/<id>`, `effect:revoked/<id>`) in any of the campaign's relation files or holder files names (holder files are scanned raw), so the relation and holdings changes of a hand-deleted event stay reported as orphans and are never adopted by a new one. A holder file of the campaign that cannot be read at all refuses an acceptance with holdings (`undecodable_holdings`), since it could name an id; one without holdings goes ahead (unless step 10 must read that file). A generator that repeats a value within one change is retried. A linked or unreadable `events/` or `accepted/` folder cannot say whether an id is free, so it raises `TakenIdsUnlisted`. `commit(approvals)` re-checks under the store lock:
    - first, 023's `recheck()`: each holder still has the instance file the holdings change was planned against, so a replaced holder is `HolderReplaced` (never retried automatically) even when it is also a pinned agent;
    - 017's own `RelationChange.stale()` for each relation change (an `end` also pins both endpoint files, see [GRAPH](GRAPH.md#the-service-grimoiregraphstore));
    - that the set and etags of the accepted-event files are unchanged;
    - that the chat still exists;
    - that each agent's instance file still has the bytes it was checked against (a replaced file may be a different individual); this covers holdings line agents and ability users;
    - that no endpoint, agent, holder or ability user has since become one of an instance-id pair (`instance_id_conflict`; an endpoint of a `begin` is reported once, though 017's check finds it too);
    - that no 020 event has taken the new event's id meanwhile (`events/<event_id>.json` is still absent).

    If anything changed, it raises `StaleWrite`, writes nothing and reallocates nothing; a holder file changed since the plan is `StaleWrite` too. Committing a **duplicate** re-checks the same way: the accepted-event files must be unchanged, and the event must still be active with relation and holdings receipts that verify against a fresh read. A retry is therefore never told that a since-revoked or hand-undone event is in force.

**Backstops.** Preconditions are the real guard against double application: once a fact has ended it cannot be ended again, whatever the ledger says. If someone moves an event file away, a re-acceptance still fails `precondition_failed`. For holdings, the derived op id plays that part while it is in the holders' logs: a re-acceptance of a hidden event's effect is `orphan_effect`.

Store step 11 can publish part of a transaction when an external edit races it (`WriteConflict`). Neither direction is hidden:
- **The event is published without its relation or holder changes:** the receipts fail, so `receipt_mismatch` is reported and a retry is refused.
- **The relation or holder changes are published without the event:** they are reported as `orphan_effect`, and a holdings orphan also refuses an acceptance touching that holder.

Exactly-once recovery across files belongs to 030: a partly published acceptance is finished by `EffectRecovery.complete` (event without some changes) or `adopt` (changes without their event), see [RECOVERY](RECOVERY.md).

**A late retry after a revocation.** An effect with an attempt (a holdings part or an ability use) is refused `attempt_revoked` (step 10). **Limit:** a relation-only effect has no attempt, and the duplicate check looks only at active events. A retry of a director-only one (no endorsement needed) that arrives after its event was revoked is accepted again as a new event, if its preconditions hold. A player-backed one needs the endorsement again, which a caller that kept it could replay (see Two keys).

## Continuity: the next response

`continuity(campaign_id, *, as_of, requested_by)` is **director-only** (`requested_by` must be `DIRECTOR`). A campaign that does not exist is `missing_campaign`, never an empty, problem-free answer. It returns `Continuity`:

| Field | Contents |
|---|---|
| `events` | active events with `sequence <= as_of`, ordered by `(sequence, ordinal)` |
| `revoked` | revoked events with `sequence <= as_of`, ordered by ordinal |
| `wrapups` | the campaign's wrap-ups with `through <= as_of`, so nothing from after `as_of` is narrated |
| `graph` | the 017 `CampaignGraph`, to be queried at `as_of` |
| `problems` | the codes below |
| `store_problems` | problems the store reported |

Problem codes (reported, never repaired):
- `undecodable_event` and `undecodable_wrapup`;
- `undecodable_holdings`: a holder file an event's holdings receipt names that cannot be read (`detail` is `holdings: <holder>: <why>`), and every holder file or folder of the campaign that cannot be read (undecodable, or a link or other store problem that could hide a holder file), named or not, since it may hide an orphan (`event_id` none, `detail` `holdings file <path>: <reason>`);
- `duplicate_ordinal` and `inconsistent_ledger`;
- `receipt_mismatch`, for an operation's receipt or, with `op=None` and `detail` `holdings: <holder>: <why>`, for a holdings receipt (checked leniently, [below](#holdings-receipts));
- `orphan_effect`, for a relation or, with `detail` `holdings <holder>`, for a holder-log entry ([Orphans](#orphans));
- `partial_revocation`: a revoked event's skipped operation whose change is **still in force**: a relation it began that is still unretracted, or an ending it made that is still there. The event is then only partly undone (half a transfer may remain, an item held twice). It is reported until a human undoes that change by hand (retracts the relation, reopens the ending); a skipped operation that holds nothing is not reported. A revoked event whose holdings undo was skipped while its change may be in force is reported too (`op=None`, `detail` `holdings: <why>`), but never one skipped as `never applied`. 021 cannot tell a human repair of it (a fresh 023 operation) from none, so it stays reported until 030 can record that repair;
- `edited_since`: a relation an active event began was ended by something other than a later active accepted event (a direct 017 edit). The receipt itself still matches, but undoing that event will be refused (`has_dependents`) until the edit is resolved;
- `stale_wrapup`: it cites a revoked or vanished event;
- `incomplete_wrapup`: an active event of that chat and scene, within its range, that it does not cite;
- `bridge_mismatch`: a 020 event file under an active event's id that is not that event's bridge (`as_knowledge_event`): a different event, or one that cannot be read (`detail` says which);
- `revoked_but_bridged`: a revoked event whose bridge is still stored in 020 (see `as_knowledge_event`, below).

The holdings codes read the campaign's holder files through `HoldingsLibrary.campaign_holdings`, under the same store lock as the snapshot. The last two check 020's raw `events/` files, read through the store and decoded with 020's pure codec (`grimoire.evidence`); 021 never loads 020's service. 020's `append` refuses both states, so they arise only from sync or a hand edit; an `events/` folder the store cannot list is reported by 020's own reads, not here.

Headlessly, "the next reply knows the key is gone" means this: the director context for the next turn reads the graph at the new sequence, together with the accepted event.

**No actor learns anything.** Accepting writes no 012/020 knowledge, evidence or belief. Who witnessed the gift is for 020 and 032 to record. `read(ref, *, requested_by)` and `read_wrapup(ref, *, requested_by)` are director-only too.

**Receipt verification** checks exact relation ids. Revocation is strictly latest first and restores in place, so an earlier event's relations are never rewritten by a later event's undo.

| Event | Operation | Its receipt must show |
|---|---|---|
| active | `begin` | the fact, `since == moment`, provenance `effect:accepted/<id>`, and no retraction. A later ending is a later event's business. |
| active | `end` | the fact, an ending at `moment` with provenance `effect:accepted/<id>`, and no retraction |
| revoked | `begin` | a retraction with provenance `effect:revoked/<id>` |
| revoked | `end` | a reopening by `effect:revoked/<id>` of exactly that ending |

A revoked event is checked by these permanent marks only. Revoking an earlier event afterwards may retract a relation that this event reopened.

<a id="holdings-receipts"></a>**Holdings receipts.** An event's holdings receipt is a seq per holder (`AcceptedHoldings.seqs`, or `Revocation.holdings_seqs` for an applied undo). Each holder's log must show, at that seq:

| Event | Its holdings receipt must show |
|---|---|
| active | the forward operation (op id, lines, note and cause `effect:accepted/<id>`), and no entry of its inverse op id anywhere in the log ("undone": the revocation landed on the holders but not on the event file, so a retry is never told a since-revoked effect is in force) |
| revoked, undo applied | the inverse operation, cause `effect:revoked/<id>`, at `holdings_seqs` |
| revoked, undo skipped | nothing: there is no receipt to check |

Per holder, the failure is one of these:
- the file cannot be read: `undecodable_holdings`, never a mismatch;
- an entry at that seq that is not the expected operation: "different operation";
- no entry at that seq, and the holder's own seq is below it: "holder restored";
- no entry, and the log was evicted through that seq: no failure when lenient (duplicates, `continuity`), since an evicted entry cannot contradict the receipt; "evicted" when strict (revoke), since the entry evicted there may be another operation's;
- otherwise: "not logged".

A holder an event names but whose holdings file is gone reads as 023's empty holdings everywhere, so `continuity`, a duplicate and revoke all name it "holder restored".

<a id="orphans"></a>**Orphans.** A relation token that names an event which does not record that relation is an orphan (`orphan_effect`). So is a holder-log entry whose cause is a 021 token naming an event that does not record it: The event must touch that holder: `effect:accepted/<id>` needs that event's holdings op id, and `effect:revoked/<id>` needs a revoked event and its inverse op id. Only the exact token forms `effect:accepted/<id>` and `effect:revoked/<id>` are interpreted; free-text provenance and causes never are.

An `effect:revoked/<id>` orphan on a still-active event is the landed half of that event's own revocation: retrying the revoke adopts it when it landed on every touched holder. When it landed on some holders only, the revoke is refused (`receipt_mismatch`, `partial_publication`), or with `partial=True` the undo is skipped and reported; completing it is 030's.

**Repairing a holdings orphan** is a human's decision, made with ordinary 023 tools:
1. undo the landed change with an ordinary 023 operation, or decide to keep it;
2. rewrite the orphan entry's `cause` to free text, so it no longer names the event.

The orphan is then no longer reported. The same effect is still refused (`orphan_effect`), because its derived op id is still logged with another cause (023's `op_id_reused`); once the landed change has been undone, the effect is accepted again under a fresh attempt (tested).

`as_knowledge_event(event)` is pure. It returns the 012 `Event` for 020 to persist:
- the same id, summary and moment;
- provenance `effect:accepted/<id>`;
- participants: the operations' endpoints and agents, every holder and holdings line agent, and every ability user. Participants are description only. A relation-only event's participants are unchanged.

It is the only 020 `Event` that may be stored under an accepted event's id. 020's `append` refuses anything else there (`accepted_id`), the bridge of a revoked event (`revoked`), and any event while the accepted events cannot be read (`accepted_unreadable`); it re-checks at commit (see [EVIDENCE](EVIDENCE.md#append)). `revoke` is never refused because a bridge exists, so latest-first undo keeps working: **the bridge outlives the revocation** as objective 020 fact, reported as `revoked_but_bridged`, until corrections are owned by 030/054 (020 has no retcons).

## Revoking: the undo of a revised turn

`revoke(ref, *, reason, expected, endorsements=(), partial=False) -> EffectChange`:

- **Inputs.** A repeat is `already_revoked`, checked before the CAS on `expected`, so a retry of a revoke that landed is told so. The same fail-closed checks as accepting apply (`undecodable_event`, `inconsistent_ledger`).
- **Latest first.** While another active event has a higher ordinal, revoke refuses with `not_latest`. Undo is a stack, so history behind a later accepted event never changes under it. To revise turn 7 after events at 8 and 9: revoke 9, then 8, then 7; accept the revision; then re-accept whatever still applies.
- **Keys.** The outcome key, plus an endorsement from **the original endorser**, the principal recorded for each contribution. Its fingerprint is `revocation_fingerprint(event_ref, event_fingerprint)`, a sha256 over `revoke`, the event's reference and its fingerprint. An acceptance endorsement is therefore never consent to undo, and the record shows which decision each endorsement was.
  - Current control does not matter. Through this API a new controller cannot undo a previous player's action, and the director cannot revoke it alone. That protects the record of the event; 017 editorial edits (`retract`, `reopen`) can still change the relation state by hand, and are then reported (`receipt_mismatch`, `edited_since`), never hidden.
  - **Limit:** an action whose endorser is gone cannot be revoked. Because undo is latest first, that also freezes the stack below it. A recorded director override is deferred to 102/035.
- **Receipts must verify** (`receipt_mismatch`), and nothing may depend on the operation (`has_dependents`). A dependent is one of these:
  - a relation the event began that has since been ended;
  - a reopening that would overlap a later relation of the same fact (017's `duplicate_relation`).
- **Repair.** With `partial=True`, operations that fail those checks are left as a human left them and recorded in `revocation.skipped`, with the reason.
- **Holdings.** The holdings receipt is verified **strictly** ([Holdings receipts](#holdings-receipts)): an evicted forward entry is "evicted", since the entry evicted at that seq may be another operation's. An unreadable touched holder is `undecodable_holdings`, also with `partial=True`. A mismatch is `receipt_mismatch`, or with `partial=True` the undo is skipped. When every touched holder was restored from before the effect ("holder restored" on every holder), the skip reason starts `never applied`: nothing is in force, so it holds no attempt and is not reported. "Not logged" is a hole in the log (an entry removed by hand, 023's `beyond_history`), no evidence that nothing is in force, so it is an ordinary skip. Before any of this, an inverse this revocation already logged on every holder (an earlier partial publication) is adopted with its seqs, even when the forward entry has been evicted since. Logged on some holders only, it is `receipt_mismatch` with `partial_publication` in its detail (with `partial=True`, a skip), never adopted and never applied again: completing it is 030's. Otherwise the inverse is planned with `HoldingsLibrary.apply(inverse, basis=seqs)`, the receipt's seqs being the basis 023 asks for:
  - applied: the revocation records the inverse's seqs (`holdings_seqs`);
  - `already_applied`: the inverse was logged by this revocation's own earlier, partial publication (the holder files landed, the event file did not). It is adopted with its seqs, never applied twice;
  - every 023 refusal (`insufficient` because the coins were spent since, `stale_count`, `missing_holder`, `beyond_history`, `partial_op`, `instance_id_conflict`, and the rarer `count_limit`, `stack_limit`, `too_large`, `op_id_reused`) is `has_dependents` with that reason in its detail, or with `partial=True` the undo is skipped. The undo is not pinned to the instance files the accept was planned against, only from plan to commit (`recheck`): a holder replaced in between is refused at commit as `HolderReplaced`, like any 023 change. A revocation that writes no holder file (an adopted undo, or a skip) pins every touched holder file as it read it, so a holder file changed before the commit is a `StaleWrite` ("holder file changed"). A damaged touched holder file refuses the undo as `undecodable_holdings` before any partial publication is classified.

  A skipped undo is recorded in `revocation.holdings_skipped` with its reason. `Revocation.holdings_left` is true when it may have left the change in force (a skip that is not `never applied`): `continuity` reports it as `partial_revocation`, and the event keeps holding every attempt it records (Accepting, step 10). An event with holdings records exactly one of `holdings_seqs` and `holdings_skipped`; one without records neither.

  **A paired holder blocks the undo.** A pair formed after acceptance never blocks undoing relation operations (Accepting, step 6), but 023 names no single holder for a paired id and refuses it (`instance_id_conflict`), so the holdings undo is `has_dependents`. 023's rule wins over the relation rule here.
- **Ability charges** need no write: a revoked event's charges no longer count. Refunding a consumed cost is intended: revoking Orin's hop gives the fuel back and voids `cooldown` and `fuel_burn`.
- **Effect, in one transaction.** Relation ids are preserved and nothing is deleted:
  - the event gains `revocation {reason, at, committed_by, endorsements, skipped}`, plus `holdings_seqs` or `holdings_skipped` when it has holdings;
  - every relation it began is **retracted** with provenance `effect:revoked/<id>`;
  - every relation it ended is **reopened** (see [GRAPH](GRAPH.md#reopening-an-ending-021));
  - the inverse holdings operation is written to the holder files, cause `effect:revoked/<id>`.

  The graph then answers exactly as before acceptance at every sequence, and a revised `end` at the same sequence succeeds. 012/020 subjects that cite the relation stay valid. The commit runs 023's `recheck()` under the lock first, as accepting does, so a replaced holder raises `HolderReplaced`.

**Limit:** causal dependents that share no relation are not detected. A later operation on a holder blocks the holdings undo only when 023 refuses the inverse: Tam spending the 2 coins Wren paid blocks it only if Tam cannot pay them back.

## Scene wrap-up

`wrap_up(chat, scene_id, *, through, summary, cites, written_by, requested_by, expected=None)` is director-only.

- **One per chat scene.** The id is `w-` plus 24 hex digits of sha256(chat_id NUL scene_id). A new wrap-up is created with `expected=None`; a revised one rewrites it under CAS, so a retried create is `StaleWrite` and must re-read the wrap-up. Only `through.sequence` counts; a `through` whose scene label names another scene is refused (`invalid_moment`).
- **It remembers every accepted event.** `cites` must contain exactly the active events of that chat and scene with `sequence <= through`:
  - one of them missing → `uncited_event`;
  - any other id → `unknown_event`;
  - a revoked one → `revoked_event`.

  A wrap-up is refused while an accepted-event file cannot be read (`undecodable_event`), because its citations could not be checked.
- **It carries no operations.** The model has no such field, and the codec is strict at every level: any other key, `operations` or `receipts` included, is `unknown_field`. Writing a wrap-up touches exactly one file, and every other file is byte-identical afterwards (tested). That is why a wrap-up can never remove another key.
- **It is director truth, never actor memory.** A wrap-up may mention things no actor witnessed. A later memory consumer must derive actor memory from 020 evidence, never from wrap-ups.
- The summary is at most 20 000 characters, and `written_by` is attribution text.

## Records

```text
campaigns/<c>/accepted/<event_id>.json   grimoire.scope-record/1, schema grimoire.accepted-event/0 or /1
campaigns/<c>/wrapups/<wrapup_id>.json   grimoire.scope-record/1, schema grimoire.scene-wrapup/0
```

`AcceptedRef` is `campaign:<c>#accepted/<event_id>`, and `WrapUpRef` is `campaign:<c>#wrapup/<wrapup_id>` (see [IDENTITY](IDENTITY.md#reference-families)). They are deliberately not `events/`: ARCHITECTURE's `events/` belongs to 020's 012 `Event`s. An accepted event is an authority record, with operations and receipts.

**`grimoire.accepted-event/0`** has these keys:
- `chat`, `moment`, `summary`, `proposed_by`;
- `operations`: `[{kind, predicate, subject, object, agent?, domain, backed_by?}]`, where `domain` is `voluntary_action` or `director`;
- `contributions`: snapshots of the 003 contributions;
- `endorsements`: `[{contribution_id, principal, fingerprint}]`;
- `controls`: `[{actor_id, controller_id}]`;
- `committed_by`, `accepted_at`, `ordinal`;
- `receipts`: `[{op, relation}]`, one per operation, in order;
- optionally `revocation`: `{reason, at, committed_by, endorsements, skipped: [{op, why}]}`.

**`grimoire.accepted-event/1`** is the schema of an event with a holdings part or an ability use (`accepted_schema(event)`). It has every `/0` key, and, where the event has them:
- `attempt_id`: the holdings part's attempt;
- `holdings`: `{lines: [{line, holder, item, amount | seen + to, target?, agent?, backed_by?, domain}], note?, op_id, seqs: {<holder ref>: seq}}`, where `line` is the 023 line kind and `seqs` keys are sorted;
- `abilities`: `[{attempt_id, actor, ability_id, needs, provenance, effect_id?, conditions_met, classification, ruling_id?, charges: [[ability_id, cost_id]], domain}]`, sorted by attempt id;
- in `revocation`: `holdings_skipped?` and `holdings_seqs?`.

References are full `InstanceRef` text, so `accepted_instance_ids` reserves holders, line agents and ability users like any other actor. An event with **no** holdings part and **no** ability use is always written as `/0`, byte-identical to the first slice, active or revoked (pinned by golden digests computed with the pre-slice codec). There is one canonical form: a `/0` payload holding `attempt_id`, `holdings` or `abilities` (even as an unknown top-level key) is `invalid_field`, and so is a `/1` payload with no part.

**`grimoire.accepted-event/2`** (028) is the schema of an event that records `after`: the sorted ids of the events it was accepted after, which is the frontier of every accepted event (revoked ones included) that its library held. It has every `/1` key the event has, plus `after` (possibly empty). An identified library ([DEVICE_SYNC](DEVICE_SYNC.md#per-device-ids)) records it, in `accept` and in 030's `adopt`. So does a library that holds any event recording `after`, such as one whose identity was lost for a while; otherwise a merge could number what it accepted meanwhile below the events it followed. A library that never took part (no identity, and no event with `after`) writes `/0` and `/1` exactly as before. `after` is outside the fingerprint and is never rewritten, so 028's ledger merge can keep every event above the ones it was accepted after ([LEDGER_MERGE](LEDGER_MERGE.md#events)). An event without `after` comes after the event without one just before it by ordinal (`predecessors`). `after` in a `/0` or `/1` payload is `invalid_field`, and a `/2` payload without it is `missing_field`. A build before 028 refuses `/2` as `unsupported_schema`, as with other 028 formats, rather than read `after` as an unknown field and lose the event's place.

How it is read and written:
- The envelope owns the address and the schema, so identity keys in the payload are refused (`identity_in_payload`).
- Unknown *top-level* keys are preserved in `extras`. Inside a known structure, anything unknown or malformed is refused (`unknown_field`, `missing_field`, `invalid_field`).
- An unknown schema is `unsupported_schema`; such a file blocks acceptance and is never rewritten.
- Decoding re-checks the structure: one receipt per operation; a domain that agrees with `backed_by`; contributions that match their operations, holdings lines and ability uses and the recorded controls; endorsement fingerprints equal to the recomputed fingerprint; the attempt rules; one seq per holder; sorted, unique charges, and a ruling id exactly for an approvable classification.
- Re-encoding is canonical.

**`grimoire.scene-wrapup/0`** has these keys: `chat`, `scene_id`, `through`, `summary`, `cites` (sorted), `written_by` and `written_at`. Nothing else is allowed.

## Other turn flows

| Flow | Contract |
|---|---|
| Reroll, alternate response (swipe) | Draft narration only. The same operations are a duplicate no-op. A swipe that implies a *different* effect is refused (`turn_already_accepted`) until the accepted one is revoked. |
| Retrying the same acceptance | A duplicate no-op if the receipts verify; `receipt_mismatch` otherwise, repaired with `revoke(partial=True)`. With an attempt, a retry after its event was revoked is `attempt_revoked`, and reusing a charged attempt in another turn is `attempt_reused`. |
| Editing a past turn | Revoke back to it, latest first, then accept the revision. |
| A 003 player `Correction` | Strikes a narration claim and never revokes an accepted event automatically. A player who objects to an effect on their character asks for revocation, which needs the director key. A mislabel goes to the referee. |
| Deleting a scene's transcript | Never deletes accepted events. To undo state, revoke its events; 035 owns the UX. |
| Forks and branches | Not here. Accepted events belong to one campaign scope and are never copied implicitly. Owner: [Scenes, Forks And Retcons](roadmap/sprints/scenes-forks-and-retcons.md) (054). |
| A direct 017 `end`, `retract` or `reopen` of an effect-managed relation | Allowed, because 017 edits are editorial. Undoing one of the event's own changes (retracting a relation it began, reopening an ending it made) is `receipt_mismatch`: re-accepting that effect is refused, and revoke refuses unless `partial=True`. Ending a relation the event began is `edited_since`: the effect still counts as applied, so a re-acceptance is a duplicate, but revoke refuses `has_dependents` unless `partial=True`. Unrelated acceptances proceed either way. |
| A direct 023 operation on a holder an effect changed | Allowed, because 023 operations are reviewed edits. Spending what the effect gave can make its undo `has_dependents` (`partial=True` records `holdings_skipped`). A hand edit of the logged entry, or a restored or deleted holder file, is `receipt_mismatch`. |

## Migration, privacy and rollback

- **Additive.** Libraries without `accepted/` or `wrapups/` load identically.
- **A pre-021 build** reports those two campaign entries as `unrecognized` load problems, and its allocators do not protect the instance ids accepted events name. It cannot recover a journal that names them; use a 021+ build or `discard_journal` (tested).
- **A pre-021 build reading a reopened relation** keeps `reopenings` as a preserved unknown key and sees no ending, so it answers `holds_at` correctly. This is tested with the verbatim 017 codec.
- **After trying a newer build** that wrote a newer accepted-event schema, an older build stops accepting until that file is removed or the newer build is used again.
- **The next slice is additive.** Relation-only proposals behave as before, and their events are still `/0`, byte-identical. An event with holdings or ability uses is `/1`, which a pre-slice build refuses (`unsupported_schema`, so `undecodable_event`): it stops accepting rather than ignore a change it cannot undo. Holder files are ordinary 023 files. Sync still holds back both families.
- All fixtures are synthetic. Neither package imports a provider or network module (tested), and `grimoire.effects` is on the purity allowlist (it may import the pure `grimoire.holdings`, since its holdings part is a 023 operation's lines). The store's import set is unchanged, except that `grimoire.effectstore` reads 028's device identity through 019's parser (`grimoire.sync`, which loads no network or provider module either). `grimoire.effectstore` reads 020 event files only through the store and 020's pure codec, and never loads `grimoire.evidencestore` (tested); 020's service in turn uses only 021's pure `grimoire.effects`.

## Boundaries and obligations

| Not here | Owner |
|---|---|
| **Computing and authenticating the ability assessment.** 021 checks `permitted` and records `charges`, nothing more. [029](ADJUDICATION.md) computes it (`ability_use`) and verifies a proposal (`check_effect`); requiring that check on every accept belongs to the runner | 029 (done) / 035 |
| Which counted resource a cost consumes (today the caller puts it in the same effect's holdings part). Assessing attempts and persisting rulings are done by [029](ADJUDICATION.md) | 034 |
| Completing a partial publication automatically (delivered for acceptances: [RECOVERY](RECOVERY.md); a partly published revocation is left); recording a human repair of a skipped holdings undo (`partial_revocation` until then); orphan detection and duplicate recognition beyond the holder log | 030 |
| Persisting the 012 `Event`, witnesses, evidence and beliefs of an accepted event | 020 / 032 |
| Extracting and labelling narration into proposals; mislabel review | 035 / Narration Continuity Referee |
| When director acceptance needs human review | 035 |
| Loading controls from 010b; campaign-wide sequence allocation; showing the player the operations before endorsing | 035 |
| Exactly-once recovery across files beyond one transaction (one acceptance's partial publication: [RECOVERY](RECOVERY.md)) | 030 |
| Delegated PC actions in acceptance; a recorded director override for a departed endorser | 021 later / 035 / 102 |
| Transcripts of draft narration and swipes | 035 / 036 |
| Forks and retcon previews without duplicated effects | [054](roadmap/sprints/scenes-forks-and-retcons.md) |
| Cascading undo and the continuity inspector (the moonshot) | a later 021 milestone |
| Summary-only accepted events (with no operation, holdings part or ability use) | 020 (012 `Event`) |
| Actor memory; long-form consolidation over wrap-ups | [Memory Consolidation](roadmap/sprints/memory-consolidation.md) (053), deriving actor memory from 020 evidence |
| Mechanics legality | 029 / mechanics modules |
| **Instance ids named by accepted events stay taken.** `accepted_instance_ids(store, campaign_id)` is part of the one reservation set, [`campaign_taken_ids`](CHATS.md#taken-instance-ids), which every allocator (024, 026, 037) must use. It scans the raw payloads (every instance reference of the campaign and every `actor_id`) without decoding them, so an event that fails validation still protects its ids. A file or folder the store cannot read at all raises `TakenIdsUnlisted`: allocation fails closed. It reads every accepted-event file, so an allocation's cost grows with play history | 024 / 026 / 037 |
| Deciding when to persist the 012 bridge for an accepted event. 021's allocator skips every 020 `events/` id, and 020's `append` stores only the bridge under an accepted id (above), so the two id spaces stay consistent on one device | 035 |
| Correcting or retiring a bridge that outlived its revocation (`revoked_but_bridged`) | 030 / 054 |
| **One linear writer per campaign.** Ordinals are `1 + max` on one replica. Two devices that each accept an event offline and are then merged produce a duplicate ordinal: acceptance and revocation then refuse (`inconsistent_ledger`) until it is resolved. Sync now merges a campaign's accepted events with its relations and holdings as one ledger unit, renumbering the merged events, and reports what it cannot settle as a `merge_review` ([SYNC](SYNC.md#ledgers-028)); a library merged by other means keeps this limit | [028](roadmap/sprints/app-managed-device-sync.md) |
| Performance at scale: each accept, and each commit, reads the campaign's accepted events and relation files once (not once per operation), and each accept also scans every holder file of the campaign raw for 021 tokens. Cost still grows linearly with campaign history, and nothing is cached | later 021 / 022 |
