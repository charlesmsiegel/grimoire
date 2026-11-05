# Unique possessions: one particular sword, its owner, holder and slot

**Status:** first slice of [Unique Equipment](roadmap/sprints/unique-equipment.md) (Sprint 034, Wave 4). It is implemented as:
- `grimoire.possessions`: pure values, a strict record codec, the deterministic mutator `plan()` and `originate()`;
- `grimoire.possessionstore`: a store-backed service;
- a registered `possession` key inside an item's own 009 instance payload ([ENTITIES](ENTITIES.md#provisional-payload-codec)). There is no new store family.

This slice is **headless**: there is no UI, no model call and no narration, so nothing is playable yet. All fixtures are synthetic. It proves milestones 1 and 2 of the sprint (create a unique item with its own provenance and place; transfer and equip it through explicit validated changes). Milestones 3 (ruleset effects) and 4 (letters and other authored artifacts) are not started; see [Boundaries and deferrals](#boundaries-and-deferrals).

## What exists

- **Two same-named swords stay two things.** A unique item is a campaign instance with its own instance id (`item/longsword-1`, `item/longsword-2`), however many share a name or a template. Each has its own owner, holder, slot and revision.
- **Equipping someone else's sword fails.** Only the item's current owner can wear it, and only while holding it (`not_owner`, `not_holder`).
- **A fork keeps the identity.** The record names every party relative to the item's own campaign (`character/mara-1`), so a copy of the campaign under another id rebinds every party with it and keeps the item's id, revision, origin and history. Forks themselves are [054](roadmap/sprints/scenes-forks-and-retcons.md)'s; the round trip is proven at the codec level.
- Every change is an explicit, logged **operation**, previewed as exact bytes and committed through the guarded store transaction. A retried operation is `already_applied` while its entry is in the item's log.
- Counted, fungible things stay in [holdings (023)](HOLDINGS.md). A unique item is never a stack.

## Concepts

| Concept | Value | Notes |
|---|---|---|
| Item | any campaign instance whose payload carries a `possession` record | the item's address is its envelope's; created as kind `item` by default. A campaign original is a physical, portable 009 entity with its own `local_name`; a sourced item is an instance of an item definition (`derived_from`, `base`, `base_etag`), so it inherits that definition's facets and description through 009 composition. |
| Holder | an existing instance of the same campaign, of any kind | who or what has it now: a person, a chest (another item), a place. |
| Owner | an existing instance of the same campaign, or none | whose it is. Ownership and custody are separate: Mara can lend her sword to Toma, who holds it but does not own it. |
| Slot | an ID segment (`main-hand`) | the one slot it is worn in, or none. `fits` lists the slots it can be worn in (at most 16, fixed at creation); an item that fits none cannot be worn. |
| Origin | `Origin(op, at, holder, owner, name, source, fits, note, cause)` | immutable provenance: the creating operation's id and time, where it started, its name or definition, and why. |
| Revision | `rev`, starting at 1 | one more per applied operation. A caller passes the revision it read (`expected_rev`); any other is `stale_revision`. |
| Operation | `Operation(op_id, steps, note=None, cause=None)` | 1-4 steps, all-or-nothing, on one item. `op_id` is a caller-chosen ID segment, unique within the item (its creation id included). `note` (500 characters, tabs and line breaks allowed) and `cause` (256, an opaque effect or action id for 021/030) refuse control, bidi and separator characters as [holdings](HOLDINGS.md#concepts) do. |
| Step | `Step.move(to)`, `Step.own(to or None)`, `Step.equip(by, slot)`, `Step.unequip()` | applied in order. A gift is one operation: `move(toma)` then `own(toma)`. |

## The record

The record is the value of the `possession` key in the item's `grimoire.entity/0` payload, in the item's own instance file (`campaigns/<c>/instances/<kind>/<id>.json`). Its schema is `grimoire.possession/1`:

```json
{"schema": "grimoire.possession/1", "rev": 3, "evicted_through": 1,
 "holder": "character/toma-1", "owner": "character/toma-1", "equipped": "main-hand",
 "fits": ["main-hand", "off-hand"],
 "origin": {"op": "c-sword", "at": "2026-11-05T10:00:00Z", "holder": "character/mara-1",
            "owner": "character/mara-1", "name": "Sword", "fits": ["main-hand", "off-hand"]},
 "log": [{"op": "g1", "rev": 2, "at": "2026-11-05T11:00:00Z",
          "steps": [{"step": "move", "to": "character/toma-1"}, {"step": "own", "to": "character/toma-1"}],
          "before": {"holder": "character/mara-1", "owner": "character/mara-1"},
          "after": {"holder": "character/toma-1", "owner": "character/toma-1"}, "note": "a gift"},
         {"op": "e1", "rev": 3, "at": "2026-11-05T11:00:00Z",
          "steps": [{"step": "equip", "by": "character/toma-1", "slot": "main-hand"}],
          "before": {"holder": "character/toma-1", "owner": "character/toma-1"},
          "after": {"holder": "character/toma-1", "owner": "character/toma-1", "equipped": "main-hand"}}]}
```

- **Parties** are `<kind>/<instance_id>` within the item's campaign. Nothing is keyed by a name.
- **Invariants:** an item never holds or owns itself; a worn item is held by its owner, in a slot it fits; the log's revisions increase within `(evicted_through, rev]`; op ids are unique (the origin's too). A value that breaks one is refused, as is anything decoded.
- **Canonical:** `fits` sorted and written only when nonempty, the log by revision, absent fields omitted. A log edited out of order is accepted and re-sorted on the next write.
- **Strict:** an unknown schema is `unsupported_schema` (upgrade the app); an address key (`instance_id`, `campaign_id`, ...) is `identity_in_payload`; an unknown field inside a known structure is `unknown_field`; a wrong shape is `invalid_field`. Unknown **top-level** fields of the record are kept verbatim, as is everything else in the instance file.
- **The log is bounded** at 200 entries. The oldest are evicted and `evicted_through` records the last evicted revision; the new entry is never dropped.

## The mutator (`grimoire.possessions.plan`)

`plan(op, current, *, expected_rev, at, exists, others)` is pure: given the record, the revision the caller read, a time, which parties exist and every other item of the campaign, it returns `Applied(value, before, after)`, `AlreadyApplied(entry)`, or raises `PossessionRefused(reason)`. The checks run in this order:

1. shapes: the time, `expected_rev`, and every party in the item's campaign (`cross_campaign`);
2. **replay:** the creation's op id is `op_id_reused`; a logged op id with the same steps, note and cause is `AlreadyApplied` whatever the revision now is, and with other content `op_id_reused`;
3. `stale_revision` when `expected_rev` is not the current revision;
4. `missing_party` when a party the operation names has no instance;
5. each step, in order, against the state the earlier steps left:

| Step | Refused as |
|---|---|
| `unequip` | `not_equipped` |
| `equip(by, slot)` | `already_equipped`, `not_owner` (`by` is not the owner), `not_holder` (`by` does not hold it), `slot_mismatch` (it does not fit `slot`), `slot_taken` (another item is worn in that slot by that holder) |
| `move(to)` | `still_equipped` (unequip first), `same_holder`, `self_custody`, `custody_cycle` (it would end up inside something it holds, at any depth) |
| `own(to)` | `still_equipped`, `same_owner`, `self_owner` |

`originate(creation, item, *, at, exists)` makes revision 1 from a `Creation(op_id, holder, owner, name, source, fits, note, cause)`: a campaign original needs a name (`needs_name`), and the holder and owner must exist.

## Service (`grimoire.possessionstore.PossessionLibrary`)

`PossessionLibrary(store, handle, *, clock, owner=None)` is **trusted application code**, never handed to a worker.

- `campaign_items(campaign_id)` reads every instance file of the campaign under the store lock and returns `CampaignItems(views, problems, instances)`, with `held_by(holder)` and `equipped_by(wearer)`. Nothing is repaired. Problems:
  - `unreadable`: the store could not read an instance file (`malformed`, `unsupported_format`, `address_mismatch`, `unreadable`). It may be an item, so `complete` is false;
  - `undecodable`: the codec's reason;
  - `missing_party`, `instance_id_conflict` (another kind uses the item's id), `slot_conflict` (two items in one slot of one holder) and `custody_cycle`, each only possible after a hand edit or a sync merge.
- `read(item)` returns an `ItemView(item, possession, etag)` with its `name`; `missing_item` without an instance file, `not_an_item` without a record.
- `create(creation, *, kind="item", parties=None)` plans a new item under a fresh id `<base>-<n>`, where the base is the definition's entity id (`longsword-1`) or the kind (`item-1`), outside [`campaign_taken_ids`](CHATS.md#taken-instance-ids). A sourced item's definition must resolve (`missing_definition`) and be an entity; an `individual` definition (one particular artifact) must be of the same kind (`kind_mismatch`) and can be a unique item only once per campaign (`duplicate_individual`). The creation is replayed by its op id first: the same creation is `already_applied`.
- `apply(item, op, *, expected_rev, parties=None)` reads the campaign's items once under the store lock and runs `plan`.
- Both refuse with `unreadable_items` when an instance file of the campaign cannot be read and the change needs every item to be checked (a creation, a `move`, an `equip`); an `own` or `unequip` still works. Both refuse a party (or the item) whose id another kind also uses with `instance_id_conflict` ([IDENTITY](IDENTITY.md)), after replay.
- Both return an `ItemChange`: `outcome`, `before`, `after`, `entry`, `party_etags`, `preview()` (the exact bytes, empty when already applied) and `commit(approvals=None)`. A change writes exactly one instance file with CAS on its etag, keeping every other field of it (the entity payload, owner, lineage, unknown fields).
- **At commit** the change is planned again under the store lock with the same inputs and time. A party whose instance file changed since the plan (perhaps a different individual under the same id) raises `PartyReplaced`, a `StaleWrite` that is never retried automatically: a human decides. Any other difference (another item took the slot, the item moved on, the creation id was used meanwhile) raises `StaleWrite`. The bytes approved are the bytes written, or nothing is.

### Retry contract

After `StaleWrite`, `WriteConflict` or `CommitIncomplete`, or after reopening a store that raised `PendingRecovery`, call `apply` (or `create`) again with the **same** operation, the same `expected_rev` and the first change's pin, `parties=change.party_etags`. The result is:
- `already_applied` while the operation's entry is in the item's log (a creation: while its item exists), whatever its parties became since;
- `party_replaced` when a pinned party's instance file differs;
- `stale_revision` when the item moved on: never applied twice;
- or a fresh plan, which has new bytes and needs fresh approvals.

### Instance ids stay taken

`campaign_taken_ids` also reserves every id a possession record names (owner, holder, wearer, in the current state, the origin and the log), scanned raw so an undecodable record protects its ids too. A hand-deleted owner's id never goes to a different individual, who would read as the item's owner. Any instance file the store cannot read makes allocation fail closed (`TakenIdsUnlisted`), since it could be an item.

## Known limits

- **Two sources of truth.** The graph's `owns`, `carries` and `located_in` relations (017) are neither read nor written: the possession record is the authority for a unique item's owner, holder and slot. A relation saying otherwise is not reported. Reconciling them (or retiring those predicates for unique items) is an open question for [059](roadmap/sprints/location-topology.md) and the next 034 slice.
- **No lending rights.** Only the owner can wear an item; there is no permission for a holder to use what it borrowed.
- **No facet checks** on parties: a holder need not be a container or a person, a wearer need not have hands, and a sourced definition need not be portable.
- **Condition** is 009's `Integrity` state on the item's entity; this slice neither reads nor changes it. Portraits are 018 placements on the item's instance, unchanged here.
- **Exactly-once is bounded by the log.** Once an entry is evicted (after 200 newer operations), its retry is `stale_revision` (the revision moved), never applied again; it is no longer recognised as `already_applied`. A file restored from a backup forgets what it lost, as for holdings; a per-file identity is 030's.
- `duplicate_individual` only sees unique items: an instance of the same individual definition with no possession record (a 107 actor) does not count.
- **Cost.** Every read, plan and allocation reads every instance file of the campaign.
- The party pin is byte identity, with the same limit as [holdings](HOLDINGS.md#service-grimoireholdingstoreholdingslibrary).
- Not yet synchronized: 019 treats an instance file as any other, so two devices that change one item merge as a conflict on that file.

## Boundaries and deferrals

| Not here | Owner |
|---|---|
| Ruleset effects of equipment (milestone 3); item-granted abilities ([ABILITIES](ABILITIES.md) obligation) | a later 034 slice, mechanics-owned |
| Letters, photographs and other authored artifacts with contents, readers and revisions (milestone 4) | a later 034 slice, with 020/032 |
| Driving operations from accepted turn effects | 021 |
| Recovery across files and exactly-once beyond the log | 030 |
| Forking a campaign | 054 |
| Facet constraints and one-location-at-a-time across the graph | 059 |
| Trade and craft | 095 |
| UI | later |

## Open questions (from the spec)

- **Which unique properties are first-class?** This slice: owner, holder, worn slot, the slots it fits, and its origin. Condition stays 009 state; anything else is the entity's.
- **How does equipment inheritance work?** A sourced item inherits its definition's facets through 009 composition; its possession record is never inherited.
- **Should equipment effects be entirely mechanics-owned?** Not decided; nothing here names a statistic.
- **Artifact contents and readers:** not started (milestone 4).
