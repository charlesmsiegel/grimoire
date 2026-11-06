# Party, reserve and scene presence

**Status:** first slice of [Party And Presence](roadmap/sprints/party-and-presence.md) (Sprint 033, Wave 4). It is implemented as:
- `grimoire.party`: pure membership state, scene casts and presence;
- `grimoire.partystore`: the store-backed `PartyLibrary`;
- two predicates added to 017's [relation registry](GRAPH.md#predicates). There is **no new record family**.

There is no app, no scene runner and no model call. The scenarios are synthetic.

## What it means

Five things about an individual are kept apart:

| Question | Answered by | Never implied by |
|---|---|---|
| Who speaks and decides for it? | a control assignment ([PLAYER_SOVEREIGNTY](PLAYER_SOVEREIGNTY.md)) | party membership |
| Who is it allied with, or who owns or trained it? | 017 relations (`allied_with`, `owns`, …), and later 050's trainer links | being in a party or a scene |
| Is it travelling with a party? | an `in_party` relation | control, ownership |
| Is it stored in a party's reserve (boxed)? | an `in_reserve` relation | anything else |
| Is it present in this scene? | the scene's **cast** | membership, ownership or control |

A boxed monster is in the campaign, can be withdrawn later, and keeps everything it remembers, but it is never in a scene's cast, so it never speaks or acts as present. An owner or trainer relation never puts anyone on stage.

## Membership

Membership is a 017 relation from the member to its **party anchor**:

| Predicate | Status | Reads as |
|---|---|---|
| `in_party` | `party` | the member travels in the anchor's active party |
| `in_reserve` | `reserve` | the member is stored for the anchor's party, available but not travelling |

- **The anchor** is any existing instance of the campaign. For one player character's party it is that character (or trainer). For a party spanning several player characters it is a group instance, and the player characters are members too.
- **Why relations.** A relation already has what membership needs: exact endpoints, a `since` and an `ending` in campaign sequence, editorial retraction, preview and approval, [taken instance ids](CHATS.md#taken-instance-ids), [sync](SYNC.md) and [accepted effects](EFFECTS.md) (021).
- **Accepted effects (021)** can begin and end these predicates like any other. 021 does not apply this slice's move rules (one membership at a time, no cycles, appended history). What it records is read by the same rules as a hand edit: two memberships at once, or a cycle, are reported and fail closed, never guessed. A play-time move that enforces the move rules is a later slice.
- **History is the relation files.** Nothing is deleted. Leaving or releasing ends a relation; `PartyLibrary.history(member)` lists every membership relation of a member, oldest first.
- **Older builds.** A build without these predicates decodes such a file as an `UnsupportedRelation`: listed, never walked and never rewritten (017's rule).

### State (`grimoire.party.PartyState`)

`PartyState.build(campaign_id, relations, as_of=…, unreadable=…)` is a pure function of the campaign's decoded relations at one sequence. `unreadable` is required: the caller says whether every relation file was read.

- `membership(member)` returns a `Membership(member, party, status, relation, since)` or `None`.
- `party(anchor)` returns the anchor's active members and its reserve, each sorted by reference text.
- `chain(x)` walks upward: `x`'s membership, then its anchor's, and so on (a group travelling in a larger party). `anchors_above(x)` lists those anchors.
- **Reserve is transitive.** `reserved(x)` is true when `x` is stored, or travels in a party whose anchor (or an anchor above it) is stored: a boxed group's members are boxed with it.
- `uncertain(x)` is true when `x`'s membership, or an anchor's above it, is ambiguous, or when its chain loops back on itself.
- A retracted membership never counts. An ended one counts before its `until`.
- **Ambiguity is reported, never guessed.** A member holding two membership relations at once (a hand edit, two devices, or two accepted effects) is in `conflicted`, appears in no party, and is reported as `conflicting_membership`. `membership()` refuses it, and so does every `PartyLibrary` move of that member. **Repair** is editorial, through 017's `RelationLibrary`: end or retract the relation that is wrong (the problem lists both relation ids), and the member is moveable again.
- **A recorded cycle is reported, never walked.** `PartyLibrary` refuses a move that would make one, but a hand edit or accepted effects can record one. It is reported once as a `party_cycle` problem listing the relation ids, and every member of the loop, and everyone travelling with them, is `uncertain`: none can be staged. Repair is the same as for a conflict.
- **Unreadable files fail closed.** When a relation file or the folder cannot be read, `complete` is false and the state reports `relations_unreadable`: a reserve membership might be hiding in the unreadable file.

## Scene casts and presence

A **scene cast** (`SceneCast(campaign_id, scene_id, as_of, present)`) is exactly who is present in one scene.
- **Only `stage` builds a cast.** Constructing one directly, or copying one with `dataclasses.replace`, is refused (`invalid_field`), so a cast is never made with a member `stage` would refuse.
- **A cast is judged against current membership.** Membership can change at the cast's own sequence after staging, for example a member boxed during the scene. The cast keeps each member's membership chain as staged (`basis`): every relation in it with its endpoints, status and `since`, so a hand edit that keeps a relation's id but changes its anchor also makes the cast stale. `cast.may_speak(x, state)` is true only for a cast member whose chain in `state` is still the staged one; otherwise it refuses with `stale_cast`, and the scene is staged again.

`stage(state, scene_id, present, instances=…)` builds one. It refuses, with `PartyRefused.reason` and the offending `members`:

| Reason | When |
|---|---|
| `relations_unreadable` | the state is incomplete |
| `in_reserve` | a selected member is reserved, directly or through a stored anchor. Withdraw it first, which is a recorded move |
| `conflicting_membership` | a selected member's membership, or an anchor's above it, is ambiguous |
| `missing_actor` | a selected individual has no instance file |
| `instance_id_conflict` | its id is also used by another kind, so it is no one individual ([IDENTITY](IDENTITY.md)) |
| `invalid_field`, `cross_campaign`, `duplicate_actor`, `too_large` | the selection's shape (at most 256), including an invalid `scene_id` |

**Any instance kind may be cast**, a creature included, under its own reference: an individualized goblin (`campaign:c/creature/goblin-1`, [024](ENTITIES.md#individualizing-a-template-instance)) is staged as itself, with no re-key and no character copy.

`presence(state, cast, x)` answers one of:
- `present`: in the cast;
- `reserved`: stored, directly or through a stored anchor. It cannot appear;
- `uncertain`: its membership, or an anchor's above it, is ambiguous;
- `accompanying`: in the active party of an anchor who is present, directly or through a group. It is nearby and can join the scene, but it is not on stage and never a speaker;
- `absent`: anything else, including every companion of an anchor who is not in the scene.

`presence` refuses with `relations_unreadable` while the state is incomplete (a hidden membership could change the answer), and with `stale_cast` for a cast of another campaign or sequence, or for a cast member whose membership chain changed since staging.

`cast_report(state, cast, instances, controlled=…)` gives one `PresenceRow(instance, presence, membership, controlled)` per individual. `controlled` is the caller's set of player-controlled instances; this slice does not read the sovereignty ledger.

Casts are values. **No cast is persisted in this slice.**

## Service (`grimoire.partystore.PartyLibrary`)

`PartyLibrary(store, handle, *, clock, new_id, owner=None)` is trusted application code. It is never handed to a worker. It writes through 017's `RelationLibrary`.

### Reading

- `view(campaign_id, as_of=…)` returns `PartyView(state, instances, problems)`. It is rebuilt from canonical files under one store lock, and nothing is cached.
- `history(member)`. Refused with `relations_unreadable` when a relation file cannot be read, since the history could be partial.
- `stage(campaign_id, scene_id, present, as_of=…)` is `stage` over the campaign as it is now. It writes nothing.

### Moves

Every move returns a `PartyChange`:
- `before` and `after` are `(party, status)` or `None`;
- `preview()` gives the exact bytes per relation file;
- `commit(approvals=None)` performs **one** store transaction that ends the current membership relation and adds the new one, so a member is never in two places and never lost between them.

| Method | From | To |
|---|---|---|
| `join(party, member)` | no membership | `party`, active |
| `store_in_reserve(member)` | active | the same party's reserve |
| `withdraw(member)` | reserve | the same party, active |
| `leave(member)` | active | no membership |
| `release(member)` | active or reserve | no membership |
| `move(member, party, status)` | anywhere | `party` with `status` (another party included) |

Each takes `at` (a 012 `Moment`) and `provenance` (attribution text, not authority). Refusals (`PartyRefused.reason`), made before anything is written:

- `invalid_field`, `cross_campaign`, `self_membership`, `missing_campaign`, `missing_member`, `missing_party`, `instance_id_conflict`;
- `relations_unreadable`: any relation file of the campaign cannot be read;
- `already_member`, `not_a_member`, `not_in_party`, `not_in_reserve`, `already_there`: the named move does not fit where the member stands;
- `conflicting_membership`: the member, or the target anchor or one above it, holds two memberships;
- `out_of_order`: **any** membership of the campaign is already recorded after `at`. Membership history is appended in campaign order, never rewritten. This is campaign-wide on purpose: the cycle and stored-anchor checks are made at `at`, and they stay true afterwards only if nothing later is already recorded;
- `same_moment`: the current membership began at `at`. Record the move at a later sequence;
- `party_cycle`: the target anchor is itself, directly or through other parties, in the member's party;
- `party_in_reserve`: joining the active party of an anchor that is itself stored. Its reserve can still take members.

**At commit**, under the store lock, the move is refused with `StaleWrite` (nothing written) when:
- any membership relation file of the campaign was added, changed or removed since the plan;
- a relation file has become unreadable;
- the member, or an anchor the move ends or adds, now shares its id with another kind (checked for every move, since ending a relation rewrites no endpoint);
- or a folded 017 change's own check fails: a replaced or vanished endpoint file, an id pair, or a duplicate.

`stale()` reports the same reasons without committing, plus `exists` when the relation id the add allocated has since been taken (the store's own check at commit). Pinning every membership file of the campaign is deliberately coarse: a concurrent move of another member also makes the change stale, and the caller plans again.

### Releasing keeps memories

A release only ends a relation. The member's instance file, its other relations, and every event, evidence and knowledge file are untouched. The proving test checks this byte for byte.

## Boundaries and deferrals

| Not here | Owner |
|---|---|
| A creature (or another non-character instance) in a **chat's** roster. 107 admits only character and persona actors; this must land before milestone 2 | 033, next slice |
| Persisted scene casts, cast defaults when a party moves, and milestone 2 (stage selected members without losing reserve identities in a running scene) | 033, later slices; scenes run in 035 |
| Who may command an autonomous companion, and reading control from the sovereignty ledger | 003/035 (this slice takes `controlled` from the caller) |
| Capture, trainer links and storage capacity | [050](roadmap/sprints/capture-and-trainer-links.md) |
| Which present individuals actually speak, and what a newly present one is told | 027 (each speaker is its own generation call) and 032 |
| Physical positions within a scene, and who can still reach whom | 041 ([STAGING](STAGING.md); its first slice checks a stage against a `SceneCast`) |
| An **audience**: individuals who perceive a scene without being on stage | 032 (who receives what) and 041 (who can see and hear, [STAGING](STAGING.md)); the cast here is only who is present |
| Cross-device merge of memberships. Relation files sync one at a time as 017's do, so a move's end could land on a device without its add (for example if the new anchor's instance file is refused there), leaving the member in no party on that device until the add follows. Not reproduced in tests | 019/028 |
