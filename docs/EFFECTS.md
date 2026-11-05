# Accepted turn effects: acceptance, one-time publication, revocation and scene wrap-up

**Status:** first slice of [Turn Effects And Wrap Up](roadmap/sprints/turn-effects-and-wrap-up.md) (Sprint 021, Wave 3). It is implemented as:
- `grimoire.effects`: pure values, payload codecs, two-key authorization, planning against the 017 graph, and ledger checks;
- `grimoire.effectstore`: the store-backed `TurnEffectsLibrary`;
- two additive record families in [the native store](STORAGE.md#accepted-events-and-wrap-ups-021);
- an additive amendment to [017](GRAPH.md#reopening-an-ending-021): reopening a relation's ending in place.

There is **no app, no runner, no transcript store and no model call**. The scenarios are synthetic. The first playable chat is 035 (Wave 5).

## What it means

A fictional result becomes **playable truth** only through an explicit **acceptance** that holds the right keys. It is written **once**, in the same store transaction as the state change it implies. After that:
- a reroll or an alternate response cannot apply it again;
- nor can a retry of the same acceptance, a scene wrap-up or a later re-mention.

A revised turn is undone only by an explicit **revocation** that holds the same keys. Revocation restores the state in place, strictly latest first. Any disagreement between the event ledger and the relation files is **reported, never hidden**.

## The five stages

| Stage | Type | Persisted here | Can change canonical state |
|---|---|---|---|
| draft narration (rerolls, swipes, alternate responses) | none; 035/036 own transcripts | no | never |
| proposed effect | `EffectProposal` (a pure value) | no | never by itself |
| accepted fictional event | `AcceptedEvent` | yes (`accepted/`) | only through its acceptance transaction |
| committed operation | a `Receipt` inside the accepted event | yes, with the event | it *is* the state change: the 017 relation files |
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
    ├─ sequence floor; plan against one 017 graph at the event's sequence                      (pure)
    └─ EffectChange.preview() → a human approves → commit(approvals)
          one store transaction: accepted/<id>.json + relation ends/adds (provenance effect:accepted/<id>)
                                                         ▼
  continuity(as_of, requested_by=DIRECTOR)  → the director context for the next response (027/035 render it)
  wrap_up(scene)                            → cites the scene's accepted events; no operations; never actor memory
  revoke(event)                             → same keys; latest first; retract what it began, reopen what it ended
```

## Operations

In this slice, state changes are changes to [017 relations](GRAPH.md). Holdings ([Counted Inventory, 023](roadmap/sprints/counted-inventory.md)) merged after this slice and are not handled yet; see the obligations below.

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

`EffectProposal(campaign_id, chat, moment, summary, operations, proposed_by)` (all text must be storable UTF-8; a lone surrogate is `invalid_field` here, not a store error later):
- `moment.scene_id` and `moment.turn_id` are required ID segments.
- It holds 1–32 operations. No two may touch the same fact (`conflicting_operations`).
- The summary is at most 4000 characters.
- `proposed_by` is one of `narration | player | director | adjudication`. It is attribution only.

**Fingerprint.** The fingerprint identifies the effect, so a reroll that words it differently is still a duplicate:
- **It covers:** the chat, the moment's sequence, scene and turn, and each operation's kind, fact and agent. Operations are hashed in sorted order, so the same operations in another order are the same effect.
- **It does not cover:** the summary, the proposer or the backing.

It is a sha256 over a hand-rolled canonical text, pinned by a golden-vector test, because stored endorsements carry it.

## Two keys

- **Outcome key (director/adjudication), always.** `TurnEffectsLibrary(store, handle, *, issued, clock, new_id, owner=None)`:
  - It refuses a handle that is not trusted, or that `issued` does not recognize (`AuthorityError`, as in 010b).
  - It only *checks* handles, through the `issued` predicate. **By convention it never mints one**, and a static test pins that `handle_for` does not appear in it.
  - Every commit is a store transaction whose approval policy can require a human to approve the exact preview. The record stores `committed_by`, the handle's principal.
  - *When* director acceptance needs human review is 035's policy (open question 1).
- **Authorship key (player), for every op whose agent is player-controlled.** This is decided by `controller_of` over `controls`; ambiguous control raises `AuthorityError`. Two things are needed:
  - **The player's own action contribution.** `backed_by` must name a 003 `PlayerContribution` of kind `action`, for the same campaign, scene, turn and actor, submitted by the current controller.
    - None → `pc_authorship`.
    - One that mismatches → `invalid_expression`.
  - **An `Endorsement(contribution_id, handle, fingerprint)`.** The handle must be a trusted `human:` handle that `issued` recognizes, whose principal is the submitter. The fingerprint must be the one the player was shown.
    - The controller must itself be a `human:` principal; otherwise `non_human_controller` (only an authenticated human authors a PC's act, as 010b mints trusted handles only for humans). **Obligation on 035/010b:** record player controllers as `human:` ids.
    - None → `not_endorsed`.
    - Anything wrong → `invalid_endorsement`.

    The record stores `{contribution_id, principal, fingerprint}`, so the endorsement is bound to exactly these operations. Two endorsements for one contribution are refused as ambiguous (`invalid_endorsement`). A contribution whose text is over 20 000 characters, or whose submitter is over 512, is refused with `contribution_too_long`, since 003 caps neither. (A `backed_by` over 512 is already refused when the proposal is built.)
- **Director-domain ops** (an NPC or unassigned agent, or `agent=None`) need only the outcome key, and refuse `backed_by` (`unexpected_backing`). Sovereignty never guarantees success: an NPC taking the PC's key is the director's to accept.

**The summary is director narration.** The endorsement covers the operations (the fingerprint), not the `summary` text, so that a reroll worded differently is still the same effect. The summary is stored with the event and carried into the 012 bridge, so it must stay neutral, director-authored world description: it never authors a PC's speech, interiority or commitments (003). **Obligation on 035:** show it to the player with the operations, and keep it to what the operations say.

Narration holds neither key. A player alone can never make their character's action succeed (a theft, joining a guild that refuses): the outcome needs the director key.

**Strength, stated plainly.** An `Endorsement` is a statement, not a one-time token. Code that kept one could present it again to re-accept the *same* operations after they were revoked. It can never be used to revoke: revocation needs a separate endorsement, over a fingerprint bound to that event (see Revoking). As with 010b, the guarantee is only as strong as 035's handle minting: any trusted code that holds the authorizer can mint `human:ana`, and the library itself cannot. In **solo play** one human holds both keys. The rule then guarantees two explicit, separately recorded decisions (endorse the mapping, approve the outcome), not two people.

**Recorded for audit:** the backing contributions, the endorsements, and the control in force for every agent (`controls`). `controls` comes from the caller. **Obligation on 035:** load it from 010b's `SovereigntyLedger`.

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
   - **its receipts verify:** a no-op. `duplicate_of` is set, `preview()` is `{}`, and `commit()` writes nothing and returns that event. This is the path for a reroll, an alternate response or a retry after a crash. It runs **before** authorization: a no-op writes nothing and needs no keys, so a retry after control changed hands, or with a stale endorsement, is told that the effect is in force rather than refused for authority;
   - **they do not:** refused `receipt_mismatch`. The way out is `revoke(..., partial=True)`. If a relation file is damaged at the same time (step 5), the refusal is `undecodable_relation` instead, so damage is named, never reported as a mismatch.

   Receipts name exact relation files, so a damaged *unrelated* relation file does not refuse a duplicate. A receipt mismatch on any *other* event does not block an unrelated acceptance. It stays reported by `continuity` until it is repaired: revoke back to that event with `partial=True`, or hand-repair the relation file. One editorial edit therefore never freezes a campaign's play.
5. **Fail closed on the relations.** A relation file of the campaign that cannot be decoded, or any store problem under its `relations/` (a link, a look-alike `Relations/`), refuses a new effect with `undecodable_relation`, because it could hide the fact a precondition must see. Unlike 017's own writes, which an undecodable file never blocks, an acceptance fails closed. The commit re-checks it too.

   `revoke` fails closed the same way (`undecodable_relation`, also with `partial=True`), so a damaged receipt or a hidden overlap is never skipped silently. **Limit:** 017's own `reopen`, called directly, does not block on undecodable files.

   The repair for any of these is to fix the file or move it out of the library by hand.
6. **Authorize** (two keys, above).
7. **One accepted effect per turn.** An active event for the same chat, scene and turn with a *different* fingerprint refuses the acceptance (`turn_already_accepted`). Another swipe's effect needs the accepted one revoked first, and several things that happen in one turn are one proposal (up to 32 operations). This also stops a turn retried at a newly allocated sequence after a later event restored the state, which the preconditions alone would let through.
8. **Sequence floor.** The sequence must be at least the highest sequence of any active event (`stale_sequence`); equal sequences are allowed (different turns or chats at one moment). Sequences are campaign-wide. **Obligation on 035:** allocate one non-decreasing sequence across all chats of a scope.
9. **Ordinal.** The new event gets `ordinal = 1 + max` over every event file, active or revoked. This is the order of the undo stack.
10. **Plan** against the graph at `as_of = sequence`:
   - **`end`:** needs exactly one walkable relation recording the fact, with no ending and `since < as_of`.
     - None, a bounded stint or a same-sequence start → `precondition_failed`.
     - Two → `ambiguous_relation`.
   - **`begin`:** both endpoints must have instance files (`missing_endpoint`), and no unretracted relation of the fact may overlap `[as_of, ∞)` (`precondition_failed`).
   - **any operation:** an `agent` must have an instance file (`missing_agent`).

   **Nothing is ever matched by name**: key-2 is never "the key".
11. **Commit.** One transaction writes:
   - the new `accepted/<event_id>.json`, create-only;
   - each 017 end rewrite, under CAS on the etag read;
   - each 017 add, create-only.

   Event and relation ids are allocated by exact presence. A new event id is also never a 020 event's id, nor one that a 021 token (`effect:accepted/<id>`, `effect:revoked/<id>`) in any of the campaign's relation files names, so the relation changes of a hand-deleted event stay reported as orphans and are never adopted by a new one. A generator that repeats a value within one change is retried. `commit(approvals)` re-checks under the store lock:
   - 017's own `RelationChange.stale()` for each relation change (an `end` also pins both endpoint files, see [GRAPH](GRAPH.md#the-service-grimoiregraphstore));
   - that the set and etags of the accepted-event files are unchanged;
   - that the chat still exists;
   - that each agent's instance file still has the bytes it was checked against (a replaced file may be a different individual);
   - that no 020 event has taken the new event's id meanwhile (`events/<event_id>.json` is still absent).

   If anything changed, it raises `StaleWrite`, writes nothing and reallocates nothing. Committing a **duplicate** re-checks the same way: the accepted-event files must be unchanged, and the event must still be active with receipts that verify against a fresh graph. A retry is therefore never told that a since-revoked or hand-undone event is in force.

**Backstops.** Preconditions are the real guard against double application: once a fact has ended it cannot be ended again, whatever the ledger says. If someone moves an event file away, a re-acceptance still fails `precondition_failed`.

Store step 11 can publish part of a transaction when an external edit races it (`WriteConflict`). Neither direction is hidden:
- **The event is published without its relation changes:** the receipts fail, so `receipt_mismatch` is reported and a retry is refused.
- **The relation changes are published without the event:** they are reported as `orphan_effect`.

Exactly-once recovery across files belongs to 030.

**Limit: a late retry after a revocation is a new acceptance.** The duplicate check looks only at active events. A retry of a director-only effect (no endorsement needed) that arrives after its event was revoked is accepted again as a new event, if its preconditions hold. A player-backed one needs the endorsement again, which a caller that kept it could replay (see Two keys). Telling a late retry apart from a deliberate re-acceptance needs per-attempt idempotency keys: owner 030.

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
- `duplicate_ordinal` and `inconsistent_ledger`;
- `receipt_mismatch`;
- `orphan_effect`;
- `partial_revocation`: a revoked event's skipped operation whose change is **still in force**: a relation it began that is still unretracted, or an ending it made that is still there. The event is then only partly undone (half a transfer may remain, an item held twice). It is reported until a human undoes that change by hand (retracts the relation, reopens the ending); a skipped operation that holds nothing is not reported;
- `edited_since`: a relation an active event began was ended by something other than a later active accepted event (a direct 017 edit). The receipt itself still matches, but undoing that event will be refused (`has_dependents`) until the edit is resolved;
- `stale_wrapup`: it cites a revoked or vanished event;
- `incomplete_wrapup`: an active event of that chat and scene, within its range, that it does not cite;
- `bridge_mismatch`: a 020 event file under an active event's id that is not that event's bridge (`as_knowledge_event`): a different event, or one that cannot be read (`detail` says which);
- `revoked_but_bridged`: a revoked event whose bridge is still stored in 020 (see `as_knowledge_event`, below).

The last two check 020's raw `events/` files, read through the store and decoded with 020's pure codec (`grimoire.evidence`); 021 never loads 020's service. 020's `append` refuses both states, so they arise only from sync or a hand edit; an `events/` folder the store cannot list is reported by 020's own reads, not here.

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

**Orphans.** A relation token that names an event which does not record that relation is an orphan (`orphan_effect`). Only the exact token forms `effect:accepted/<id>` and `effect:revoked/<id>` are interpreted; free-text provenance never is.

`as_knowledge_event(event)` is pure. It returns the 012 `Event` for 020 to persist:
- the same id, summary and moment;
- provenance `effect:accepted/<id>`;
- participants: the operations' endpoints and agents. Participants are description only.

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
- **Effect, in one transaction.** Relation ids are preserved and nothing is deleted:
  - the event gains `revocation {reason, at, committed_by, endorsements, skipped}`;
  - every relation it began is **retracted** with provenance `effect:revoked/<id>`;
  - every relation it ended is **reopened** (see [GRAPH](GRAPH.md#reopening-an-ending-021)).

  The graph then answers exactly as before acceptance at every sequence, and a revised `end` at the same sequence succeeds. 012/020 subjects that cite the relation stay valid.

**Limit:** causal dependents that share no relation are not detected.

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
campaigns/<c>/accepted/<event_id>.json   grimoire.scope-record/1, schema grimoire.accepted-event/0
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

How it is read and written:
- The envelope owns the address and the schema, so identity keys in the payload are refused (`identity_in_payload`).
- Unknown *top-level* keys are preserved in `extras`. Inside a known structure, anything unknown or malformed is refused (`unknown_field`, `missing_field`, `invalid_field`).
- An unknown schema is `unsupported_schema`; such a file blocks acceptance and is never rewritten.
- Decoding re-checks the structure: one receipt per operation; a domain that agrees with `backed_by`; contributions that match their operations and the recorded controls; endorsement fingerprints equal to the recomputed fingerprint.
- Re-encoding is canonical.

**`grimoire.scene-wrapup/0`** has these keys: `chat`, `scene_id`, `through`, `summary`, `cites` (sorted), `written_by` and `written_at`. Nothing else is allowed.

## Other turn flows

| Flow | Contract |
|---|---|
| Reroll, alternate response (swipe) | Draft narration only. The same operations are a duplicate no-op. A swipe that implies a *different* effect is refused (`turn_already_accepted`) until the accepted one is revoked. |
| Retrying the same acceptance | A duplicate no-op if the receipts verify; `receipt_mismatch` otherwise, repaired with `revoke(partial=True)`. |
| Editing a past turn | Revoke back to it, latest first, then accept the revision. |
| A 003 player `Correction` | Strikes a narration claim and never revokes an accepted event automatically. A player who objects to an effect on their character asks for revocation, which needs the director key. A mislabel goes to the referee. |
| Deleting a scene's transcript | Never deletes accepted events. To undo state, revoke its events; 035 owns the UX. |
| Forks and branches | Not here. Accepted events belong to one campaign scope and are never copied implicitly. Owner: [Scenes, Forks And Retcons](roadmap/sprints/scenes-forks-and-retcons.md) (054). |
| A direct 017 `end`, `retract` or `reopen` of an effect-managed relation | Allowed, because 017 edits are editorial. Undoing one of the event's own changes (retracting a relation it began, reopening an ending it made) is `receipt_mismatch`: re-accepting that effect is refused, and revoke refuses unless `partial=True`. Ending a relation the event began is `edited_since`: the effect still counts as applied, so a re-acceptance is a duplicate, but revoke refuses `has_dependents` unless `partial=True`. Unrelated acceptances proceed either way. |

## Migration, privacy and rollback

- **Additive.** Libraries without `accepted/` or `wrapups/` load identically.
- **A pre-021 build** reports those two campaign entries as `unrecognized` load problems, and its allocators do not protect the instance ids accepted events name. It cannot recover a journal that names them; use a 021+ build or `discard_journal` (tested).
- **A pre-021 build reading a reopened relation** keeps `reopenings` as a preserved unknown key and sees no ending, so it answers `holds_at` correctly. This is tested with the verbatim 017 codec.
- **After trying a newer build** that wrote a newer accepted-event schema, an older build stops accepting until that file is removed or the newer build is used again.
- All fixtures are synthetic. Neither package imports a provider or network module (tested), and `grimoire.effects` is on the purity allowlist. The store's import set is unchanged. `grimoire.effectstore` reads 020 event files only through the store and 020's pure codec, and never loads `grimoire.evidencestore` (tested); 020's service in turn uses only 021's pure `grimoire.effects`.

## Boundaries and obligations

| Not here | Owner |
|---|---|
| Holdings operations from accepted effects (the [023 obligation](roadmap/sprints/turn-effects-and-wrap-up.md#obligation-holdings-operations-023)) | 021's next slice (023 has merged) |
| Persisting the 012 `Event`, witnesses, evidence and beliefs of an accepted event | 020 / 032 |
| Extracting and labelling narration into proposals; mislabel review | 035 / Narration Continuity Referee |
| When director acceptance needs human review | 035 |
| Loading controls from 010b; campaign-wide sequence allocation; showing the player the operations before endorsing | 035 |
| Exactly-once recovery across files beyond one transaction | 030 |
| Delegated PC actions in acceptance; a recorded director override for a departed endorser | 021 later / 035 / 102 |
| Transcripts of draft narration and swipes | 035 / 036 |
| Forks and retcon previews without duplicated effects | [054](roadmap/sprints/scenes-forks-and-retcons.md) |
| Cascading undo and the continuity inspector (the moonshot) | a later 021 milestone |
| Summary-only accepted events (with no operations) | 020 (012 `Event`) |
| Actor memory; long-form consolidation over wrap-ups | [Memory Consolidation](roadmap/sprints/memory-consolidation.md) (053), deriving actor memory from 020 evidence |
| Mechanics legality | 029 / mechanics modules |
| **Instance ids named by accepted events stay taken.** `accepted_instance_ids(store, campaign_id)` is part of 107's allocator; every future allocator (024, 026, 037) must use it too, as with 017's `relation_endpoint_ids`. It scans the raw payloads (every instance reference of the campaign and every `actor_id`) without decoding them, so an event that fails validation still protects its ids; only a file the store cannot read at all does not. It reads every accepted-event file, so an allocation's cost grows with play history (as 017's does with relations) | 024 / 026 / 037 |
| Deciding when to persist the 012 bridge for an accepted event. 021's allocator skips every 020 `events/` id, and 020's `append` stores only the bridge under an accepted id (above), so the two id spaces stay consistent on one device | 035 |
| Correcting or retiring a bridge that outlived its revocation (`revoked_but_bridged`) | 030 / 054 |
| **One linear writer per campaign.** Ordinals are `1 + max` on one replica. Two devices that each accept an event offline and are then merged produce a duplicate ordinal: acceptance and revocation then refuse (`inconsistent_ledger`) until it is resolved. A merge rule for accepted events (rebasing one device's events on the other's, with review) is needed before sync carries `accepted/` | 019 / 028 |
| Performance at scale: each accept, and each commit, reads the campaign's accepted events and relation files once (not once per operation). Cost still grows linearly with campaign history, and nothing is cached | later 021 / 022 |
