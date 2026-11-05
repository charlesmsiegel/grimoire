# Durable events and evidence

**Status:** first slice of [Durable Event Evidence](roadmap/sprints/durable-event-evidence.md) (Sprint 020, Wave 3). It is implemented as:
- `grimoire.evidence`: a pure payload codec and read-time assembly;
- `grimoire.evidencestore`: a store-backed service;
- an additive, create-only record family in [the native store](STORAGE.md#events-and-evidence-020).

There is **no app** yet. Nothing here calls a model. Nothing here decides which narration becomes an accepted event; that is [021](roadmap/sprints/turn-effects-and-wrap-up.md).

## What it does

It persists [012's records](KNOWLEDGE.md) exactly, and their meaning does not change:
- **events** (what happened);
- **propositions**;
- **rulings** (canon's standing);
- **perceptions and reports** (one actor's evidence);
- **beliefs** (one actor's conclusions);
- **disclosures** (what the table may read).

Behaviour:
- **Reopening reproduces every projection exactly.** Each actor knows what it knew before, and nothing more:
  - `actor_knowledge`, `knowledge_about`, `player_knowledge`, `standing` and `director_knowledge` over the stored ledger equal the in-memory ones (tested for every actor and moment; `knowledge_about` for the private event's subject).
- **Events and evidence are stored apart.** An event file has no field for a witness, percept, report or belief. The director's record therefore cannot leak a private witness's evidence. The free-text `summary` and `provenance` are the writer's responsibility (012's neutral-text guidance).
- **Evidence belongs to the play scope, never to a world.**
  - A [107](CHATS.md) world-less chat's campaign namespace takes records directly. A record may carry its chat as provenance.
  - An actor added to a chat later starts with no evidence.
  - Naming the scope or attaching a world never touches a knowledge file.
- **A rumour is never promoted to fact.**
  - Standing comes only from rulings.
  - No stored field means "true".
  - A hand-added `"established": true` is refused on read.

## Layout and records

```text
campaigns/<campaign>/events/<event_id>.json                 # family event
campaigns/<campaign>/evidence/<family>/<id>.json            # proposition | ruling | evidence | belief | disclosure
campaigns/<campaign>/evidence/batch/<seq:010d>.json         # the knowledge log
```

- Each record has a `KnowledgeRef` address: `campaign:<campaign>#<family>/<id>`. See [IDENTITY](IDENTITY.md#reference-families).
- Its `name`, `<family>:<id>`, is exactly 012's `KnowledgeError.records` form, so an error names one file.
- Perceptions and reports share family `evidence`, as they share 012's ID namespace.

The envelope is `grimoire.knowledge-record/1` ([STORAGE](STORAGE.md#events-and-evidence-020)). It is **create-only**: no edit and no delete exists.

### Payload codec (`grimoire.evidence.codec`)

| Schema | Record |
|---|---|
| `grimoire.event/1` | `Event` |
| `grimoire.proposition/1` | `Proposition` |
| `grimoire.ruling/1` | `Ruling` |
| `grimoire.perception/1`, `grimoire.report/1` | `Perception`, `Report` (family `evidence`) |
| `grimoire.belief/1` | `Belief` |
| `grimoire.disclosure/1` | `Disclosure` |
| `grimoire.knowledge-batch/1` | a log batch |

**What the payload holds:**
- Each 012 field, except `campaign_id` and the record's own ID; the address owns those.
- `moment` is `{"sequence", "scene_id"?, "turn_id"?}`.
- Refs are kept as exact text. A subject is `{"kind", "ref"}`.
- Lists keep 012's order.
- Optional fields are omitted when they hold their default. `confirmed` and `evidence_ids` are always present.
- An optional `chat` is a `ChatRef` in the same campaign. It is provenance only: the director read exposes it as `origins`, and it is never used for access or traversal.

**Decoding is strict.** Only the exact form `encode` writes is accepted. An `EvidenceCodecError(reason, record)` carries only a reason and the record's name. It is raised outside any handler, so its cause and context are empty.

| reason | meaning |
|---|---|
| `identity_in_payload` | the payload repeats `campaign_id` or the record's own ID |
| `unknown_field` / `missing_field` | an unknown key; a required key absent |
| `invalid_field` | a wrong JSON type (`true` is not `1`, `"false"` is not a bool), text over 20 000 characters, or a label over 200 |
| `non_canonical` | an explicit default, such as `"addressed": false` or `"participants": []` |
| `schema_mismatch` / `unsupported_schema` | the schema does not fit the family; or a schema this version does not know (a newer build's) |
| `campaign_mismatch` | a holder, sender or chat belongs to another campaign |
| 012 reasons | for example `invalid_record`, `ungrounded_ruling` and `invalid_sent_at`, from constructing the record |

Encoding refuses text or a label over the limit with `too_long`.

### The knowledge log

Every append writes its records and exactly **one batch**, in one store transaction:

```json
{"seq": 3, "previous": "<etag of batch 2>",
 "records": {"belief:b-7": {"etag": "sha256:…", "holder": "campaign:c1/character/ines", "proposition": "p-2"}}}
```

- Batch 1 has no `previous`.
- A batch holds 1–500 records.
- Each entry carries the etag of its file's exact bytes.
- Rulings and beliefs also carry their **chain key** in the entry, and so do disclosures (as `event`). A damaged file's chain is therefore known without reading the file.

## Reading: assembly and quarantine

`grimoire.evidence.assemble` turns what a scan found into a valid `Knowledge` and names everything it left out. It is pure and deterministic. The steps run in order:

1. **Verify the log.**
   - Batches run gap-free from 1.
   - Each `previous` is the etag of the batch before it.
   - Each batch decodes, and no record is listed twice.
   - At the first failure, at batch `k`, the problem `log_broken` is reported, and:
     - only batches `< k−1` are trusted, because a `previous` mismatch cannot tell which side was altered;
     - all rulings, beliefs and disclosures are quarantined (`*_family_damaged`);
     - batches from `k−1` on are untrusted hints. Every record they list is quarantined, as `log_broken`, or as `missing` if absent.
2. **Account for every file**, checking each record a trusted batch lists in this order:
   - absent → `missing`;
   - a damaged scan → its code (`malformed`, `unsupported_format`, `address_mismatch`, `link`);
   - an etag different from the entry's → `edited`;
   - a codec failure → its reason;
   - a decoded chain key different from the entry's → `key_mismatch`, and both chains go.

   A canonically named file that no trusted batch lists is `unlisted`. That covers a hand-added file, a store-level put that bypassed the service, and the remains of a partial publish.
3. **Chains.** Quarantining a ruling quarantines every ruling of its proposition. Quarantining a belief quarantines every belief of its (holder, proposition). Quarantining a disclosure quarantines every disclosure of its event. The reason is `chain_quarantined`. An unlisted file that cannot be decoded has no trusted key, so its whole family goes.
4. **Validate the rest.**
   - First, in linear passes, remove every record whose reference is gone (an event, a proposition, a cited evidence item, a superseded belief), with the reason 012's check order would give (`unknown_event`, `unknown_proposition`, `unknown_evidence`, `unknown_belief`) and the chain rule. One deleted file therefore costs a near-linear read, however many records cite it.
   - Build `Knowledge` from the remaining records.
   - On a `KnowledgeError`, quarantine only the **first-named record still present**. In 012 that is the record whose rule failed (for `reused_contribution`, the belief with the later ID). For a pair 012 names together (`conflicting_rulings`, forked or unsuperseded beliefs) it may be either one; the chain rule then takes both. Use 012's reason, and the chain rule applies. Repeat until the ledger is valid.
   - Evidence that a broken belief cites is kept.
   - After each such removal, the linear reference pass runs again, so the removed record's dependents also go in linear time.
5. **Report non-canonical entries** as problems: `unrecognized`, `name_mismatch`, `link`, `unreadable`.

A record's reason is the first one assigned.

### Guarantees

These are tested against the undamaged ledger U, for every actor, the player and every `as_of`. They are tested on a 300-seed in-memory property test and a 40-seed on-disk one, under any visible damage: a deleted, edited, added, truncated, linked, newer-schema or look-alike-renamed record, and an edited or deleted middle batch.

- **Monotone fail-closed.** Every bucket of every actor and player projection is a subset of U's, `inherited` included, and so are `table_events`.
- **Never promotes.** A standing is U's, or `reported`/`unasserted`. An older ruling never resurfaces. A proposition that is itself quarantined has no standing: `standing` raises 012's `unknown_proposition`, exactly as for an id the ledger never held.
- **No resurfacing.** No current belief is ever an older belief of its chain, and a disclosure's moment never moves.
- **Nothing silently dropped.**
  - Every record on disk is in the ledger or in `quarantined`.
  - With an intact log, every listed record that is absent is `missing`.
  - With a broken log, the break itself is reported. Any deleted record that only the altered or deleted batch listed may then go unreported individually. Its loss only removes knowledge, and rulings, beliefs and disclosures are quarantined wholesale anyway.

**Limits.** As for [010b ledgers](STORAGE.md#reading), the log is an integrity check, not authentication. It cannot detect:
- truncating, or consistently editing, the **tail**: removing the newest record together with its entry in the newest batch (a test pins this);
- a correctly chained forged batch;
- restoring an older copy of the whole campaign;
- altering two or more consecutive batches consistently. A broken `previous` at batch `k` is blamed on `k` or `k−1`, so the guarantees assume the visible damage altered **one** batch. Whoever re-chains two batches can as easily re-chain to the tail, which is undetectable anyway; the log protects against accidental damage, not a determined editor.

**Deviation from 012's per-actor independence.** A broken log, or an unlisted and undecodable ruling, belief or disclosure, quarantines that whole family. One actor's damaged file can then strip other actors' beliefs. That is fail-closed. Otherwise, damage to one actor's files affects only that actor's chains and the director view. An edited record is placed in its chain by its batch entry, never by its edited content, so an edit that names another holder leaves that holder's chain alone (tested).

## The service (`grimoire.evidencestore.EvidenceLibrary`)

`EvidenceLibrary(store, handle=None, *, owner=None)` is **trusted application code**. It is never handed to a worker or to narration. All reads work on a read-only store.

### Reads

| Method | Returns |
|---|---|
| `read(campaign, *, requested_by)` | `CampaignKnowledge(knowledge, quarantined, problems, origins)`; `requested_by` must be `DIRECTOR`, else `AuthorityError`. A campaign with no real state reports `campaigns/<id>:absent`, so a typo'd id is not mistaken for "nothing known" |
| `actor_knowledge(campaign, actor, *, as_of)` | 012's projection over the assembled ledger |
| `knowledge_about(…)` | 012's projection over the assembled ledger |
| `player_knowledge(…)` | 012's projection over the assembled ledger |
| `standing(…)` | 012's projection over the assembled ledger |

There is no widening flag. Every read scans the campaign's knowledge files and assembles them. That is linear in the number of files; log verification walks only the batch files present, so a stray huge sequence number costs one file. A cascade from missing references is removed in linear passes. Only records that fail other 012 rules are removed one validation at a time, which is quadratic in their number. Nothing is cached or persisted.

### `append`

`append(campaign, records, *, controls=(), contributions=None, chat=None)` returns a `KnowledgeChange`. Its checks run in order. Every refusal writes nothing and carries no record text.

1. **Read-only.** A read-only store, or no handle → `read_only`.
2. **Shape.** 1–500 012 records of this campaign (`empty`, `too_many`, `invalid_record`, `campaign_mismatch`).
3. **Existence.** The campaign (a play scope or an instance file; an empty leftover folder is not one), the chat (of this campaign) and every actor named must already exist as files (`unknown_campaign`, `unknown_chat`, `unknown_actor`). That covers holder, sender, `attributed_to`, participants and instance subjects. There is no name lookup, and a typo never creates a campaign. An actor whose id another kind of the campaign also uses is no one actor (`instance_id_conflict`; see [STORAGE](STORAGE.md#instance-ids-are-never-reused)).
4. **Integrity.** Only an intact ledger is extended. Any quarantine or problem → `ledger_damaged`, naming the quarantined records and the problem paths. A write validated against a partial ledger could change meaning once a human repairs it.
5. **Fresh IDs.** An ID already on disk → `duplicate_id`. (A damaged or look-alike file has already been refused at step 4.)
   - **Accepted (021) ids.** An event id that is an accepted event's id in this campaign is reserved for that event's bridge, `as_knowledge_event(accepted)` ([EFFECTS](EFFECTS.md#continuity-the-next-response)): any other event there → `accepted_id`; the bridge of a revoked event → `revoked` (an undone turn is never stored as objective fact). Fail closed: while any accepted-event file, link or folder of the campaign cannot be read, or the one under a new event's id cannot be decoded, an append holding an event → `accepted_unreadable`. Appends without events are unaffected. The accepted files are read through the store and decoded with 021's pure codec.
6. **No backdating.**
   - Every new `moment.sequence` must be at least the ledger's high-water mark H (`backdated`), and at most H + 1 000 000 (`sequence_jump`).
   - **Single exception:** a delayed letter. An addressed report's `sent_at` may be earlier than H; its proposition must already be stored, otherwise `before_proposition`. Only that exception changes projections at `as_of < H`: the sender's `sent` bucket and the director view.
   - Campaign-wide sequence allocation belongs to 021/035.
7. **012's ledger rules.** `KnowledgeError` propagates unchanged.
8. **Sovereignty.** Each new belief goes through `admit_belief`; `BeliefAuthorityError` propagates.
9. **Symmetry.** Each record must read back exactly, at an address every platform can hold (`too_long`, `invalid_field` for text UTF-8 cannot hold, `unstorable_id`), in a file the store accepts (`too_large`: over 8 MiB rendered).
10. **The write.** The records and their batch are staged. The assembled post-state must quarantine nothing.

`KnowledgeChange.preview()` gives the exact bytes per logical path. **Previews are director-facing:** they hold raw records. Do not show a player a batch that holds director-only records; batch a player-approved belief alone.

`commit(approvals=None)` runs under the store lock:
- It re-checks the campaign, the chat, the actors and the full snapshot of both knowledge trees (every file and link, and every problem; OS noise and empty folders are not state). Any difference → `StaleWrite`.
- For an append holding events, it also re-checks the accepted-id rule, so an acceptance that took one of its ids after the preview → `StaleWrite` (a fresh `append` then names the reason). 021's commit makes the mirror check (no 020 event under its new id), and 021's `continuity` reports what sync or a hand edit can still deliver (`bridge_mismatch`, `revoked_but_bridged`).
- It then commits one transaction. The batch's create-only CAS also stops a concurrent append.
- A refusal before the commit point (`StaleWrite`, `WriteDenied`, `UnstorableIdentifier` — a Windows `MAX_PATH` overflow is only caught here — `NameCollision`, or an `OSError` while staging, which may leave empty folders that are not state) writes nothing. After `WriteDenied`, register fresh approvals for any it consumed and commit again. After `StaleWrite`, build a fresh `append`: the change's snapshot no longer matches. A change commits at most once.
- After a `WriteConflict` partial publish, the next read reports `unlisted`/`missing` and appends refuse until the damage is repaired. The `.conflicts/` copies hold director-private evidence and stay on the device.

### Instance ids stay taken

`evidence_instance_ids(store, campaign_id)` returns every instance id of the campaign named anywhere in its events, evidence and knowledge-log files. The raw payloads are scanned without decoding (every string, key or value, that parses as an `InstanceRef` of this campaign), so a quarantined record still protects its ids. 020 persists no bare control ids. It is part of the one reservation set, [`campaign_taken_ids`](CHATS.md#taken-instance-ids), so a hand-deleted actor's id never goes to a new individual who would read the old one's private perceptions and beliefs. Because this is private knowledge, any damaged file or problem in either tree (malformed, a link, a look-alike, a stray) raises `TakenIdsUnlisted`: allocation fails closed, as an append does at step 4.

### Trust boundaries (documented, not checked here)

- **Rulings, events and disclosures** must reach the service only from director or adjudication paths (021/035).
- **`controls` and contributions come from the caller,** because Wave 1 control is not persisted. 035 must take them from the authenticated session; a caller that names the director as a persona's controller can author its beliefs. After reopen, a PC belief's authorship cannot be re-verified. `player_knowledge`'s `inherited` bucket surfaces any belief the current controller did not author.
- **`Transaction.put` of a `KnowledgeRecord` is not a supported writer.** A put without its batch entry reads as `unlisted`, is quarantined, and blocks appends (tested). A put together with a correctly chained forged batch is the undetectable case under Limits, as is editing a `KnowledgeChange`'s staged records before `commit`: trusted code only.
- **Graph, index and retrieval builders** (017, 031, 065) must not derive anything from these files or from `store.campaign_knowledge`. They consume actor projections or the explicit director read only. Connectivity never grants knowledge. (021's director-only `continuity` reads the raw event files solely to check accepted-event bridges.)

## Correction and repair

- **This slice has no retcons.** Correct by appending:
  - a later ruling (standing changes from then on);
  - a superseding belief.
  An event cannot be corrected. A hand edit, deletion or addition is damage, not a correction.
- **Never delete to correct.** To recover from damage:
  - restore the original bytes; or
  - roll the log back: remove the newest batches and exactly the records they list. That is a consistent earlier state, and it deliberately brings back earlier stances and rulings.
- If a middle batch cannot be decoded, only restoring its bytes works.
- Remove an `unlisted` file by hand.
- To an older build, a newer `knowledge-batch/2` looks like a broken log. It quarantines all rulings, beliefs and disclosures and refuses appends.

## Deferrals

| Not here | Owner |
|---|---|
| Retcons, correcting an event, invalidating witnessed evidence | 020 later milestone |
| Guided repair tooling | 020 later milestone |
| Materialized projections, caches, incremental reads | 020 later milestone / 065 |
| Which narration becomes an accepted event | 021 ([EFFECTS](EFFECTS.md); its `as_knowledge_event` bridge yields the 012 `Event` to store here) |
| Transmission semantics | 032 |
| Prompts from actor projections | 027 |
| Cross-device merge of logs | [028](roadmap/sprints/app-managed-device-sync.md) |
| Export redaction | the export owner |
| Graph traversal | 017 |
| Persisted control assignments | 009/035 |
| Persisting an `AbilityRuling` ([025](ABILITIES.md)) and its attempt; 020 stores only what 029 decides to record | 029 |
| 017's claim shapes stored with their propositions; `check_subjects` before persisting propositions and rulings; definition-subject and future-instance validity | 020, next step ([obligations from 017](roadmap/sprints/durable-event-evidence.md#obligations-from-017)) |
