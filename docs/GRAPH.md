# Typed relationship graph: relations, traversal and actor claims

**Status:** first slice of [Typed Relationship Graph](roadmap/sprints/typed-relationship-graph.md) (Sprint 017, Wave 3). It is implemented as:
- `grimoire.graph`: pure relation values, payload codec, the derived index and actor claims;
- `grimoire.graphstore`: the store-backed `RelationLibrary`;
- an additive record family in [the native store](STORAGE.md#relation-records-017).

There is no app or UI, and nothing here calls a model. Graph lookup is deterministic. The scenarios are synthetic.

## What it means

- A **relation** is director truth: a typed, dated fact about two individuals of one campaign ("Wren is a member of the Ashen Guild from sequence 3").
- **Being connected is never knowledge.** No actor learns anything from the graph. What an actor thinks is connected comes only from its own [012 projection](KNOWLEDGE.md), one node at a time.
- A **false rumour stays a claim**: a proposition the actor holds, with its apparent source and the actor's stance. It is never materialized as a relation.
- **Absence is open-world.** "Not recorded" never means "known false".
- The index is **derived and rebuildable**. The canonical data is the relation files.

## Identity and files

`RelationRef(campaign_id, relation_id)`, text `campaign:<campaign_id>#relation/<relation_id>` (see [IDENTITY](IDENTITY.md#reference-families)). `relation_id` is the segment a knowledge `Subject("relation", <id>)` names. One file per relation:

```text
campaigns/<campaign_id>/relations/<relation_id>.json      # grimoire.scope-record/1, schema grimoire.relation/0
```

One file per relation was chosen over one file per campaign. An edit touches one small file under a narrow compare-and-swap. Different relations never conflict under sync (019). A damaged file loses one relation, not the graph.

## Predicates

A closed registry, `grimoire.graph.PREDICATES`. Every predicate's domain is `world_fact`.

| predicate | direction | reads as |
|---|---|---|
| `member_of` | directed | subject is a member of object (an organization, group, crew) |
| `located_in` | directed | subject is in object (a place, a container) |
| `owns` | directed | subject owns object |
| `carries` | directed | subject is carrying object |
| `knows` | directed | subject is acquainted with object. It **grants no knowledge of the object's facts** |
| `has_met` | symmetric | the two have met |
| `kin_of` | symmetric | the two are family |
| `allied_with` | symmetric | the two are allied |

**Player sovereignty.** Relations are editorial world facts, approved through the store's preview and approval like 107's actor records. There is no player-consent gate in this slice, even when a player character is an endpoint (requester decision). Play-derived changes, such as a PC joining a guild in a scene, go through [accepted turn effects](EFFECTS.md) (021), which add and end relations with the provenance `effect:accepted/<event_id>` under its two-key rule. Emotional and attitudinal relations (`loves`, `trusts`) are interiority, a protected PC domain, and are not predicates here; owner [Relationships and reputation](roadmap/sprints/relationships-and-reputation.md) (078).

## The record

`Relation(ref, predicate, subject, object, since, provenance, recorded_at, ending=None, retraction=None, extras={})`:

| Field | Meaning |
|---|---|
| `subject`, `object` | Exact `InstanceRef`s of `ref`'s campaign, and different. For a symmetric predicate the stored order means nothing. |
| `since` | A 012 `Moment`: the relation holds from `since.sequence`, inclusive. |
| `provenance`, `recorded_at` | Who asserted it (attribution text, not authority) and the clock text of the add. **Immutable**, like `since` and the endpoints. |
| `ending` | `Ending(until, provenance, at)`: it stopped holding at `until.sequence` (exclusive). `until > since`. |
| `retraction` | `Retraction(provenance, at)`: the record was wrong. A tombstone, kept on disk and never walked at any time. |
| `extras` | Unknown authored payload keys, preserved verbatim. |

`holds_at(seq)` is true when the relation is not retracted, `since <= seq`, and either there is no ending or `seq < until`.

**Nothing is ever deleted.**
- *Ending* is fictional ("left the guild at 12").
- *Retracting* is editorial ("never true").
- `end` on a retracted relation is `already_retracted`, and ending twice is `already_ended`.
- Retracting an ended relation is allowed. Retracting twice is `already_retracted`.

### Payload codec (`grimoire.relation/0`, provisional)

Keys: `predicate`, `subject`, `object`, `since` (`{sequence, scene_id?, turn_id?}`), `provenance`, `recorded_at`, then optionally `ending` (`{until, provenance, at}`) and `retraction` (`{provenance, at}`).

- The envelope owns the address: a payload key `ref`, `address`, `campaign_id`, `relation_id` or `schema` is `address_in_payload`.
- Unknown top-level keys go to `extras`. Unknown keys inside a moment, ending or retraction are `invalid_field`.
- Other refusals: `unsupported_schema`, `campaign_mismatch`, `self_relation`, `invalid_until`.
- Re-encoding is canonical, and decode(encode(x)) == x.
- A predicate this version does not know decodes to an `UnsupportedRelation`. It is kept verbatim with whichever endpoints parse, reported as `unknown_predicate`, never walked, and never rewritten (`end` and `retract` refuse with `unsupported_relation`).

### Reopening an ending (021)

<a id="reopening-an-ending-021"></a>An additive amendment made by [021](EFFECTS.md) for revoking an accepted event: **reopening** cancels a relation's ending in place.

- `Reopening(ending, provenance, at)` keeps the cancelled ending verbatim.
- `Relation.reopenings` is a tuple, oldest first, and is the model's last field, so 017's positional calls still fit.
- `Relation.reopened(reopening)` requires a current ending equal to `reopening.ending` and no retraction. Otherwise it refuses with `not_ended` or `already_retracted`.
- The result has `ending=None`, and the same id, `since`, provenance and extras. It holds again from `since`, so a later `end` works as usual.

Reopening is the third non-destructive operation:
- *ending* is fictional;
- *retraction* is editorial;
- *reopening* says "that ending was revoked".

Nothing is deleted, so 012/020 subjects that cite the relation stay valid. 021 reopens only endings it made; a relation that was added already bounded (`add(until=…)`) can be reopened only editorially.

- **Codec.** The optional payload key is `reopenings: [{ending: {until, provenance, at}, provenance, at}]`. It is written only when nonempty, so a never-reopened file is byte-identical to 017's. An absent, `null` or empty `reopenings` all mean never reopened (as an absent or `null` ending means not ended), and re-encoding omits the key. Decoding refuses unknown keys and `until <= since`. List order is the order the reopenings were made; their `at` clock text is never compared, since clocks can step back.
- **A file's own `reopenings` key.** 017 preserved any unknown top-level key, so a file may already carry its own `reopenings`. Only a list is 021's form, and it is decoded strictly. Any other non-`null` value (text, a number, an object) is that older key: it stays in `extras`, is written back verbatim, and the relation decodes as never reopened. A reopen of such a relation refuses with `reserved_key` rather than overwrite it; rename the key by hand to reopen. A corrupted 021 list that becomes text this way is still reported by 021's receipt checks.
- **Older builds.** A 017-only build keeps `reopenings` as a preserved unknown key and sees no ending, so it answers `holds_at` correctly. This is tested against the verbatim 017 codec.
- **The index** flags `RelationEntry.reopened`. `history`, `relation` and `to_json` expose the reopenings.
- **The service.** `RelationLibrary.reopen(ref, *, provenance, expected)` mirrors `end`, works on a relation with a missing endpoint, and refuses with `duplicate_relation` when the reopened interval would overlap another unretracted record of the fact. Its overlap is re-checked at commit.
- **`RelationChange.stale()`** is additive and public. It reports why a previewed change can no longer be committed:
  - for an add, exactly `commit`'s own check;
  - for a rewrite, the store's CAS words `absent` and `changed`;
  - for an end, also `endpoint changed` (its pinned endpoint files);
  - for a reopen, also an overlap.

  021 calls it under the store lock before folding relation changes into its own transaction. `commit` makes the same checks.
- **Checking many changes against one read.** `add`, `reopen` and `RelationChange.stale` take an optional `relations` list (the campaign's decoded relations, read under the same lock), so 021 checks up to 32 operations against one read of the folder instead of one read each. Omitted, they read the folder as before.

## The index (director-only)

`GraphIndex.build(campaign_id, relations, instances, *, unsupported=(), undecodable=())` is a pure function of one campaign's relation files and the instances that exist.

- **It is a director-only type**, like `DirectorKnowledge`: every query takes `requested_by` and refuses anything but `DIRECTOR` with `AuthorityError`. That keeps call sites explicit and auditable; it is not authorization. Its `problems` and `instances` attributes are plain data: holding a `GraphIndex` (or a `CampaignGraph`) is itself the director's, and neither may be handed to an actor-facing caller.
- `to_json(requested_by=…)` is its deterministic form. Any later cache must equal it.
- **No derived file is written.**

Problems (`GraphProblem(code, relation_ids, cited_by, subject)`) are reported, never repaired:
- `missing_endpoint`: an endpoint has no instance file. The relation is never walked and never rebound by name, but stays listed by `history`.
- `duplicate`: the same predicate and endpoints (unordered for a symmetric predicate) with overlapping validity. **Every file is kept**, and each overlapping pair is reported.
- `unknown_predicate`.
- `undecodable`: the codec reason, or the store's code for a file it could not read.

A relation is **walkable** at `as_of` when it is decodable, has a known predicate, is not retracted, has both endpoints, and holds at `as_of`.

| Query | Returns |
|---|---|
| `relations(node, *, as_of, predicates=None, direction="any")` | walkable relations incident to an existing node, ordered by `(predicate, id)`. A symmetric relation counts as both `out` and `in`. |
| `history(node)` | every decodable or unsupported relation file naming `node` at any time, flagged (ended, retracted, missing endpoint, unsupported). `node` need not exist any more. An undecodable file has no known endpoints, so it is listed only in `problems` and by `relation(id)`. |
| `relation(id)` | any relation entry, undecodable ones included (`unknown_relation` otherwise) |
| `traverse(start, *, as_of, predicates=None, direction="any", max_depth=2, max_steps=200)` | a bounded walk; see below |
| `recorded(predicate, subject, object, *, as_of)` | whether a walkable relation records it. **False means not recorded.** |

Validation:
- `as_of` must be a non-negative `int` (else `invalid_as_of`).
- `direction` must be `out`, `in` or `any` (else `invalid_direction`).
- `predicates` must be a nonempty iterable of registry names, never a bare string (else `invalid_predicates` or `unknown_predicate`).
- A node that is not an `InstanceRef` is `invalid_field`; a node of another campaign is `campaign_mismatch`.
- A `relations` or `traverse` node with no instance file is `unknown_instance`.

### Traversal

`traverse` is a breadth-first walk with a FIFO queue in first-reached order:
- `reached` maps each node to its depth and includes `start` at 0.
- A node is expanded once, if its depth is below `max_depth`. It considers its walkable, filter-matching relations in `(predicate, id)` order.
- Each relation becomes at most one `Step(relation_id, predicate, source, target, depth, forward)`, including an edge into an already-reached node, so cycles and duplicates stay visible.
- `forward` is False only for a directed relation walked object→subject.
- Bounds: `1 <= max_depth <= 8` and `1 <= max_steps <= 2000` (else `invalid_bound`).
- `truncated` is True when the step limit stopped the walk, or when a node at `max_depth` still has an unwalked walkable relation. Truncation is never silent.
- Equal inputs give equal results.

## Claims: what an actor thinks is connected

012's `Proposition` is text plus `about`. A `ClaimShape(campaign_id, proposition_id, predicate, subject, object)` gives one proposition a typed, **positive, present-tense** reading.

`ClaimSet(knowledge, shapes)` validates the shapes against the ledger and keeps only the campaign id and the shapes. Its refusals are `GraphError` reasons:
- `unknown_proposition`, `campaign_mismatch`, `unknown_predicate`, `self_relation`;
- `duplicate_claim`: at most one shape per proposition;
- `not_overt`: **each endpoint must be in the proposition's `about`**. This is 012's overt-reference rule. A shape can never identify "the masked man" as the duke; that identification is a separate proposition.

**Persisted by 020.** A shape is stored in its proposition's file (`grimoire.proposition/2`), written in the same append as the proposition, and read back into `CampaignKnowledge.claims` (a `ClaimSet`). `EvidenceLibrary.claims_about` and `player_claims_about` give an actor's or player's answer over the stored ledger. See [EVIDENCE](EVIDENCE.md#claim-shapes-017).

Writers must also follow these rules, which the text is not checked for, as in 012:
- shapes come with the proposition from the same writer (director, adjudication, 032);
- never shape a negative ("is not a member"), past ("was a member") or conditional proposition.

`claims_about(view, claims, node)` → `NodeClaims(actor, as_of, node, items)`:
- `view` must be exactly an `ActorKnowledge`, from `actor_knowledge` or from one entry of `player_knowledge` (else `not_actor_knowledge`). No ledger, director view or graph index is accepted.
- Its items are the propositions the view holds (heard reports, sent reports, current and inherited beliefs) whose shape names `node`.
- Each `NodeClaim(proposition_id, predicate, position, text, heard, sent, stance, inherited_stance)` gives:
  - `position`: `subject`, `object`, or `either` for a symmetric predicate. **The other endpoint is never included.**
  - `heard`: the actor's own confirmed reports, with apparent author `attributed_to`. Never the true sender.
  - `stance` and `inherited_stance`: the current belief stance; superseded beliefs are not items.
- Standing, rulings, matching relations, provenance and unconfirmed evidence never appear.
- **There is no multi-hop expansion** (requester decision). 012's contract holds unchanged: subjects never appear in an actor projection, and an actor view never expands from a subject.
- An unknown node and an existing-but-unheld node give the same empty answer.
- By test, the result equals the shaped propositions held in `knowledge_about(…, Subject("instance", node))` whose shape names the node.
- Like `ActorKnowledge`, the IDs in it are never rendered into prompts (027).

**Director-side checks:**
- `claim_matches(index, claims, proposition_id, *, as_of, requested_by)` returns the walkable relations that match a claim; it is empty for a false claim, and a proposition with no shape is `unknown_claim`. It is informational only and creates no ruling.
- `check_subjects(knowledge, index, *, requested_by)` validates what 012 handed to the graph, in propositions' `about` and rulings' `grounds`:
  - a `Subject("relation", …)` naming no relation is `unknown_relation`; others are `undecodable_relation`, `unsupported_relation` or `retracted_relation`;
  - one valid only after the citing record is `about_future_relation` or `ruling_cites_future_relation`;
  - a `Subject("instance", …)` with no instance file is `unknown_instance`;
  - definition subjects are not checked here.

  It is a check, not a ledger rule: 012's `Knowledge` stays graph-free.

## The service (`grimoire.graphstore`)

`RelationLibrary(store, handle, *, clock, new_id, owner=None)` is trusted application code and is never handed to a worker. Each mutation returns a `RelationChange`: call `preview()` to get the exact bytes, then `commit(approvals)`. Each is one guarded store transaction.

| Call | Notes |
|---|---|
| `add(campaign_id, predicate, subject, object, *, since, provenance, until=None)` | `until` writes a past or bounded stint, already ended, with the add's provenance and time. Refusals: `missing_campaign` (also for a look-alike folder), `unknown_predicate`, `campaign_mismatch`, `self_relation`, `missing_endpoint` (an exact instance file name; never a namesake or another kind), `instance_id_conflict` (an endpoint whose id another kind of the campaign also uses: `load()` leaves both out, so it is no one individual; see [IDENTITY](IDENTITY.md)), `invalid_until`, `duplicate_relation` (overlap on `[since, until)` among decodable files). Undecodable files do not block a write. |
| `end(ref, *, until, provenance, expected)`, `retract(ref, *, provenance, expected)` | Guarded by compare-and-swap on `expected`. Refusals: `undecodable_relation` and `unsupported_relation`, plus the ordering rules above. **Both work on a relation with a missing endpoint** (the repair path). A rewrite keeps the envelope's owner and unknown fields. |
| `read(ref)` | the relation and its etag. Store errors propagate. |
| `graph(campaign_id, *, requested_by)` | `CampaignGraph(index, etags, problems)`, read from one campaign's relation and instance listings under one store lock. It never loads the whole library. |

- An `add` is re-checked under the store lock at commit: each endpoint's instance file must still hold the bytes it was checked against (a file replaced since the preview may be a different individual under the same id), no endpoint's id may have been taken by another kind since (`instance_id_conflict`), and no duplicate may have appeared. Otherwise it raises `StaleWrite`, never reallocating an id the approval pinned.
- An `end` pins both endpoint files the same way: a missing or unreadable one is pinned as such, so the repair path still commits, but any change to either file between the preview and the commit is `StaleWrite` (`endpoint changed`), and `RelationChange.stale()` reports it. `retract` and `reopen` pin only the relation file.
- **Scale.** There is no cap on relations per campaign. A write reads every relation file of its campaign (O(n)); `graph()` reads the campaign's relation and instance files once, and duplicate detection compares only relations recording the same fact. Only `traverse` is bounded. `relations`, `history` and `claims_about` return everything incident to one node.
- Relation ids come from `new_id("r")` and are taken by exact presence; any file or link already there counts as taken. A look-alike file fails the commit with `NameCollision`.
- No model call is made, and nothing writes events, evidence or beliefs.

### Endpoint ids stay taken

`relation_endpoint_ids(store, campaign_id)` returns every instance id named by the campaign's decodable and unsupported relation files. The one reservation set, [`campaign_taken_ids`](CHATS.md#taken-instance-ids), also scans every relation file raw, so an undecodable one still protects its ids, and it fails closed (`TakenIdsUnlisted`) when a relation file or folder cannot be read. A hand-deleted endpoint's id is therefore never given to a different individual, who would otherwise silently inherit the relations.

**Obligation:** every instance allocator (024, 026, 037) uses `campaign_taken_ids`, never this function alone.

**Limits (pinned by tests):**
- `relation_endpoint_ids` alone does not protect an id named only by an undecodable file (`campaign_taken_ids` does);
- a pre-017 build does not protect these ids.

## Boundaries and deferrals

| Not here | Owner |
|---|---|
| Relations authored inside sources or worlds | 022/026 |
| Events as nodes; running `check_subjects` on write; definition-subject and future-instance validity (claim shapes are persisted: [EVIDENCE](EVIDENCE.md#claim-shapes-017)) | 020 |
| Play-derived relation changes and their undo | [021](EFFECTS.md) |
| Endpoint facet constraints (`located_in` a `spatial`, `carries` a `portable`), functional predicates (one location at a time). Known gap: an item may be recorded in two places at once, and nothing reports it | 059/034 (023 shipped counted stacks only, not individual items) |
| Species membership | 024 |
| Emotions, trust, reputation | 078 |
| A multi-hop actor-side mental graph | not planned; it would need an amendment to 012 |
| A persisted graph cache, cross-campaign relations, user-defined predicates | later 017 milestones |
| Cross-device merge of relation files | 019 |
