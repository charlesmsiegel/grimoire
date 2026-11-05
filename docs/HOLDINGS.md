# Counted holdings: stacks, operations and replay safety

**Status:** first slice of [Counted Inventory](roadmap/sprints/counted-inventory.md) (Sprint 023, Wave 3). It is implemented as:
- `grimoire.holdings`: pure values, a payload codec, the deterministic mutator `plan()` and `context_lines()`;
- `grimoire.holdingstore`: a store-backed service;
- an additive record family in [the native store](STORAGE.md#counted-holdings-023).

This slice is **headless**: there is no UI, no model call and no narration, so nothing is playable yet. All fixtures are synthetic.

## What exists

- A campaign records how many **ordinary, fungible things** each holder has ("8 ordinary balls, 2 strong balls"). No mechanics module is involved.
- Counts survive reopening the library.
- Counts change only through explicit **operations** that are previewed and approved before commit.
- Counts never go negative.
- A **retried operation does not double-spend** while its entry is in every touched holder's log, or when the caller passes the `basis` it recorded at creation (see [Known limits](#known-limits) for eviction and restored files).

## Concepts

| Concept | Value | Notes |
|---|---|---|
| Holder | an existing campaign instance (`InstanceRef`) of any kind | a person, a chest or a ship. The holdings file is keyed by kind **and** id, so a holder id reused under another kind neither shares nor inherits holdings. |
| Item key | a campaign-local ID segment (`ordinary-ball`) | the stable identity of a fungible kind of thing. It is not a definition reference and not a name. Display labels and links to item definitions belong to 034. |
| Stack | `(item, count)`, `1 <= count <= 10**12` | at most one stack per item per holder. A stack whose count reaches 0 is removed, and the log keeps its history. An absent stack reads as 0. At most 1024 stacks per holder. |
| Operation | `Operation(op_id, lines, note=None, cause=None)` | `op_id` is a caller-chosen ID segment. An operation has 1–32 lines and touches at most 8 holders, all in one campaign. `note` is at most 500 characters, and `cause` at most 256. Both refuse control characters (C0 and C1), bidi embeddings, overrides and isolates, the byte-order mark, line and paragraph separators and lone surrogates. Notes may contain tabs and line breaks, and ordinary format characters (the zero-width joiner in emoji, the ZWNJ Persian needs, soft hyphens) are text. `cause` allows no control characters at all. `cause` is an opaque effect or action ID for 021/030 audit. |
| Line | `Line.add(holder, item, n)`, `Line.consume(holder, item, n)`, `Line.adjust(holder, item, seen, to)`, `Line.move(source, target, item, n)` | `n >= 1`. Lines apply in order. One operation is **all-or-nothing** across every holder it touches, so a craft (consume 3, add 1), a trade (two opposite moves) or a capture that spends a ball are each one operation. |

"Stable IDs" in the roadmap's first proof means three things: holders are campaign instance IDs, item kinds are stable campaign-local keys, and operations have stable IDs. A stack has no ID of its own; individual objects with their own IDs are [Unique Equipment (034)](roadmap/sprints/unique-equipment.md).

Holdings are neither a relation nor an entity. They grant no knowledge and no control, and an operation is not an accepted fictional event (020/021). It is a reviewed state edit, which 021/030 will later drive from accepted effects.

## Identity and file

- `HoldingsRef(campaign_id, kind, instance_id)` has the text form `campaign:<campaign_id>/<kind>/<instance_id>#holdings` ([IDENTITY](IDENTITY.md)).
- The file is `campaigns/<campaign_id>/holdings/<kind>/<instance_id>.json`.
- The envelope is `grimoire.scope-record/1` (see [STORAGE](STORAGE.md#counted-holdings-023)).
- The payload schema is `grimoire.holdings/1`:

```json
{"seq": 5, "evicted_through": 0,
 "stacks": [{"item": "ordinary-ball", "count": 9}, {"item": "strong-ball", "count": 2}],
 "ops": [{"op": "m1", "seq": 5, "at": "2026-11-01T10:00:00Z",
          "lines": [{"line": "move", "holder": "character/mara-1", "item": "ordinary-ball", "amount": 2,
                     "target": "character/toma-1"}],
          "peers": {"character/toma-1": 3}, "after": {"ordinary-ball": 9}, "note": "paid the ferryman"}]}
```

- `seq` is the file's local change counter; it is not comparable across devices. `evicted_through` is the highest seq dropped from the log.
- Each op entry logs the **whole** operation's lines. Holders are written as `<kind>/<instance_id>`.
- `peers` holds the seq the same operation got in every other holder's file.
- `after` holds this holder's counts for the items it touched.
- `at` is UTC: `YYYY-MM-DDTHH:MM:SSZ`, or with exactly six fraction digits.
- **The codec is strict inside known structures.** These are refused with a `HoldingsCodecError.reason`:
  - unknown fields, duplicates, a negative or out-of-range count, a bool or a float;
  - a duplicate or out-of-range seq, or a timestamp of the wrong shape or one that is not a real UTC date and time (ops written out of order by hand are accepted and re-sorted on the next write);
  - an entry whose `peers` name the file's own holder;
  - a payload repeating identity (`schema`, `address`, `campaign_id`, `holder`, `kind`, `instance_id`, `ref`).
- **Unknown top-level payload fields are kept** across writes. An unknown schema is `unsupported_schema`: the file is listed and never rewritten.
- **Hand edits.** The file is human-editable authority: a hand-edited count is the new truth, and a count edited to `0` means "has none" (no stack; it is dropped on the next write). A hand edit that breaks the codec makes the file undecodable. Reads report it, and operations touching that holder are refused until it is repaired. The bytes are never "fixed" automatically.
- **Undecodable holders raise errors other than `HoldingsRefused`.** A bad payload makes `read` and `apply` raise `HoldingsCodecError` (with a `reason`). A bad envelope makes them raise the store's own error (`MalformedRecord`, `UnsupportedFormat`, …). `store.load()` never reads holdings at all (like 020's events and evidence, they are history); `campaign_holdings` reports both kinds.

## The mutator (`grimoire.holdings.plan`)

`plan(op, current, at, *, exists, size, basis=None, max_file=MAX_FILE)` is pure. Time, holder existence and file sizing are passed in. It returns `Applied(values, summary)` or `AlreadyApplied(entries)`, or raises `HoldingsRefused(reason, detail)`. Checks run in this order:

1. **Shape.** `invalid_field`, `too_large`, `cross_campaign`, `same_holder`. These checks run when the `Operation` is built.
2. **Reuse.** A holder logs `op_id` with different lines, note or cause → `op_id_reused`.
3. **Replay.**
   - If every touched holder logs it → `already_applied`, with the logged entries.
   - If only some do: for each holder missing it, if its `evicted_through` is at least the peer seq the logged entries recorded for it (or no seq was recorded), the log can no longer tell whether the operation landed there → `beyond_history`.
   - Otherwise → `partial_op`, naming the holders missing it.
   - Neither case is ever reported as applied.
4. **Holders.** Each holder must have an instance file of exactly its kind (`missing_holder`). This comes after the replay check, so a retry of an operation that landed stays `already_applied` even if a holder was deleted later.
5. **History boundary.** A caller that recorded `basis = {holder: seq}` when it first created the operation (030 will persist this) gets `beyond_history` if a holder's log has since been evicted past that seq, if the holder's seq went back below it (the file was restored from an older copy, or deleted), or if the log is missing an entry after it (removed by hand). A valid log is contiguous, since each operation adds one entry and eviction drops only the oldest.
6. **Apply** the lines in order. The refusals are `insufficient`, `stale_count` (an adjust whose `seen` is not the current count), `count_limit` and `stack_limit`.

**Effects.** Each touched holder's seq goes up by one, and it appends one entry whose `peers` name the others' new seqs. Zero stacks are removed. The summary has one row per `(holder, item)` with `before`, `after` and `new_stack`, which flags a possibly mistyped new item key.

**Bounded log.** A holder keeps at most 1000 entries, and its rendered file is at most 7 MiB (the store's reader accepts up to 8 MiB). The two limits are independent: a busy holder whose operations have many or long lines reaches 7 MiB first and keeps fewer than 1000 entries.
- Eviction drops the oldest entries and **never the new one**. Two renders, of the whole log and of only the new entry, calibrate the adapter's per-entry size estimates. A third confirms, evicting more while the file is still over. If uneven estimates made it evict too much, a bounded search keeps every entry that still fits. With the adapter's near-exact estimates a file is rendered about four times per operation, never once per entry.
- If the stacks and preserved extras alone exceed the bound, the plan is refused with `too_large` before anything reaches the store.

`context_lines(holdings, limit=50)` gives one holder's nonzero stacks as `"<item>: <count>"`, sorted, plus `"… and N more"` past the limit. It contains no log, notes or other holders. [Context Retrieval (031)](roadmap/sprints/context-retrieval.md) decides when to include it.

## Service (`grimoire.holdingstore.HoldingsLibrary`)

`HoldingsLibrary(store, handle, *, clock, owner=None)` is **trusted application code**, never handed to a worker.

- `read(holder)` returns a `HoldingsView(holder, holdings, etag)`, with `counts()`, `lookup(op_id)` and `evicted_through`.
  - A holder without an instance file is refused with `missing_holder`.
  - A holder without a holdings file is empty, with `etag None`.
- `apply(op, *, basis=None, holders=None)` reads every touched holder under the store lock, so no commit in the same process can land between two holders' reads, and then runs `plan`. Every `basis` key must be a touched holder with a nonnegative seq, and every `holders` key a touched holder pinned to an instance etag (`invalid_field`); these are shape checks, made before the replay check. A holder whose instance file cannot be read at all (a link, an I/O error) has no bytes to pin and counts as missing (`missing_holder`). It returns a `HoldingsChange`:
  - `outcome` (`applied` or `already_applied`), `summary`, `entries` and `holder_etags` (each holder's instance-file etag, the pin for a retry);
  - `preview()`: the exact bytes per logical path, empty when already applied. The summary and the bytes come from the same plan, so what a human reads is what they approve.
  - `commit(approvals=None)`: one store transaction with CAS on every holder file. A no-op commits nothing and returns `{}`. Committing twice raises.
  - `puts` and `recheck()` let a larger writer (021's accepted effects, 030) fold the files into its own transaction. It must hold `store.lock` from `recheck()` through its commit, and must merge two changes for one holder into one operation (the store refuses a path written twice). Approvals stay pinned to these bytes.
- **At commit,** each holder is re-checked under the store lock against the instance file's exact bytes recorded when the change was planned (as 017 pins relation endpoints). A holder whose instance file vanished (`holder gone`) raises `StaleWrite`. One whose file was replaced, perhaps by a different individual given the same id (`holder changed`), raises `HolderReplaced`, a `StaleWrite` that is never retried automatically: a human decides. A malformed instance file is pinned by its raw bytes; one that can no longer be read at all (now a link, an I/O error) is `holder gone`.
  - **Limits:** the pin is byte identity, not individual identity. A hand-deleted instance that 107 later regenerates for the same source produces identical bytes (107's instance files carry no creation stamp) and passes, so a change planned before the deletion and committed after the regeneration credits the new individual; any hand edit of the instance file in between counts as replaced. A creation stamp in 107's instance envelope would close this.
- **Edits preserve** the stored envelope's unknown fields and its original `owner`, even an absent one. Only new files get the library owner.
- `campaign_holdings(campaign_id)` reads only that campaign's `holdings/` folder (the store's `campaign_holdings`, contained per file as `load()` contains the other families) and returns every holder's view, plus problems:
  - `undecodable`: the store could not read the envelope, or the codec refused the payload (including an unknown schema);
  - `missing_holder`: the instance is gone. The file is kept.
  - `unrecognized`: something under `holdings/` that is not a holder file, such as a hand-made `Mara-1.json` or a stray `notes.txt`.

  Each problem's `reason` says why: the codec's reason (`unsupported_schema` means "upgrade the app") or the store's load-problem code.
- **There is no delete.** No API removes a holdings file, or reduces a count except through a logged line. Turning tracking off in a future UI can only hide holdings, never erase them.

### Retry contract

After `StaleWrite`, `WriteConflict` or `CommitIncomplete`, or after reopening a store that raised `PendingRecovery`, call `apply` again with **the same operation** and the first change's pin, `holders=change.holder_etags` (a holder whose instance file changed since is refused as `holder_replaced`, ahead of any other refusal). Never retry after `HolderReplaced`. Replay still comes first: an operation that landed is `already_applied` whatever its holders' instance files became since. The result is one of:
- `already_applied` (it landed);
- `partial_op`: commit step 11 published some files and kept a concurrent human edit on another, **or** a holder's file was reset, restored or resolved by sync after the operation landed. The log cannot tell these apart;
- `beyond_history`;
- a fresh plan.

A re-created change has new bytes, so it needs fresh approvals.

**Repairing a partial operation** is a human decision. First check whether the missing holder's file was deliberately reset or restored: completing the operation would then re-credit what the human removed. Otherwise apply an `adjust` (or a corrective operation) under a **fresh** op id. The partial op id stays refused, which keeps the repair visible. Automatic completion belongs to [Durable Action Recovery (030)](roadmap/sprints/durable-action-recovery.md).

### Known limits

- Op-id uniqueness is per holder. An id reused on holders disjoint from the original operation's goes undetected, so callers should derive ids from a uuid or from the causing effect.
- Once an entry is evicted, a retry without a `basis` is a fresh operation. This is the bounded-log decision; exactly-once beyond the log belongs to 030.
- **A restored or recreated file forgets its operations.** `peers` and `basis` seqs are local to one file, and a file brought back from a backup, recreated, or resolved by sync no longer logs what it lost. With a `basis`, a file whose seq went back below the recorded seq (an older backup, a deleted file) or whose log has a hole after it (an entry removed by hand) is refused as `beyond_history`. Otherwise a retry is:
  - **applied again** for a single-holder operation, or a multi-holder one whose every touched file was restored, when there is no `basis` or the file was restored to a seq at or above it (it is applied once relative to the restored state);
  - `partial_op` when only some touched files were restored, or `beyond_history` once a reused seq has been evicted.

  Telling those remaining cases apart would need a per-file identity (an epoch recorded in `peers` and `basis`); that is left to 019/030.
- **Exactly-once is bounded by the log.** A retry is recognised only while the operation's entry is still in every touched holder's log, or when the caller passes the `basis` it recorded when it first created the operation (and the files were not restored, above). Without a `basis`, a retry after eviction is applied again. The same holds for a refused partial op: once its logged half is evicted, a retry is no longer refused. Callers that may retry late (021, 030) must persist and pass `basis`.
- **Holder ids stay taken.** The one reservation set, [`campaign_taken_ids`](CHATS.md#taken-instance-ids), which every instance allocator uses, treats every id with a holdings file (readable or not) as taken, via `holdings_holder_ids`, which reads file names only, so a hand-deleted holder's id never goes to a different individual. A look-alike holder file (`Holdings/`, `Character/`, `Mara-2.json`), which a case-insensitive filesystem or the next case-only rename would hand to the id's next owner, keeps the id it folds to taken. If the campaign folder exists only as a look-alike, or the `holdings` folder or a kind folder in it is a link or cannot be listed or inspected, allocating a **new** actor id fails closed (`HoldingsUnlisted`, a `TakenIdsUnlisted`) rather than guess; a folder whose name can fold to no kind (`New folder`, `@eaDir`) cannot hide holder files and never blocks it; binding a scenario or lorebook, or reusing an existing actor, never lists holdings and is unaffected. A human who deliberately recreates an instance file with the same kind and id by hand gets the old holdings back.
- A vanished holder's stock cannot be moved out until its instance is restored, or the file is edited by hand.
- **Move peers stay taken.** `holdings_peer_ids` scans every readable holder file raw (each `<kind>/<id>` text: a line's `holder` and `target`, the `peers` keys), so an id named only in another holder's log (a `move` peer whose instance and holdings files were both deleted by hand) never goes to a new individual. A holder file the store cannot read (malformed, a link, a newer format) fails allocation closed (`TakenIdsUnlisted`); a look-alike is not read, so only its own id is taken. This reads every holder file at each allocation.

## Boundaries and deferrals

| Not here | Owner |
|---|---|
| Proposing operations from narrative mentions of items | [Turn Effects And Wrap Up (021)](roadmap/sprints/turn-effects-and-wrap-up.md) |
| Including holdings in prompts | 031 (`context_lines` is the input). Holdings are not part of 022's `ChatSnapshot` (`load()` never reads them), so 031 must read them once per turn and pin each `HoldingsView.etag` to keep a turn consistent |
| The UI editor and the tracking toggle | UI sprints (`adjust` is the headless editor) |
| Item display labels, item definitions, unique objects | 034 |
| Cross-device merge of counts (sync reports a divergence as a `held` conflict until then) | [028](roadmap/sprints/app-managed-device-sync.md) |
| Exactly-once beyond the bounded log; automatic partial completion | 030 |
| Who may mutate whose holdings beyond the store's per-path `APPLY` | 003/104 |
| Migrating V1 inventories | 026 |
