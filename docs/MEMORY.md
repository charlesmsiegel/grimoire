# Sourced memory

`grimoire.memory` builds a disposable, bounded view of exact confirmed evidence held by one actor. Selections accumulate in independent named collections. Corrections link obsolete evidence to later replacement evidence, suppressing the obsolete item even if a later selection repeats it. Competing evidence is retained. A view includes history and replacement links, unavailable IDs, and an omitted count; terminal current and unavailable entries take priority over superseded history within the total limit; displayed evidence and unavailable details retain the union of selection and correction operation IDs along their incoming paths; it does not create beliefs, summarize events, or copy excerpts into the memory ledger. Evidence is resolved again on every read. `as_of` uses campaign knowledge sequence, not elapsed time.

`MemoryLibrary` stores operations in `authority/memory/<campaign>` through `DurableAuthority`. The current sovereignty controller must request each operation. A human controller uses their own trusted issued handle; director operations require an explicit director perspective on a trusted `app:` service handle. Control is checked again on each operation. Current actor views apply only the present controller's operations; `inspect` is a separate director audit of all recorded editors and provenance. The executor remains responsible for authenticating callers and protecting director access.

IDs are campaign-qualified through the actor reference. Operation IDs are unique within the campaign. An exact retry by the same principal and editor is a no-op; changed payloads conflict. Batches contain 1–500 distinct evidence IDs. View limits default to 100 and range from 1 to 1000. Evidence must be confirmed and held by the actor at the operation moment; all replacement evidence must be later than every obsolete item. Corrections cannot compete for the same old ID within one editor's history. Moment order cannot decrease within an actor/editor history. Source evidence and beliefs are never modified.

## Consolidated accounts and dossiers

A `MemorySummary` is an editor's attributable account of one remembered detail: authored text (at most 2,000 characters), a `topic` that groups accounts of the same detail, the 1–500 evidence IDs it cites, and optionally the earlier account it `supersedes` (the same editor's, collection's and topic's). Nothing generates or rewrites summary text; every account is traced to exact evidence and, through `inspect`, to its editor and principal. `MemoryLibrary.summarize` records one under the same controller, retry, moment-order and held-evidence rules as a selection. It is accepted even when it cites evidence already corrected, so a backlog recap keeps its provenance.

`consolidate_dossier` (and `MemoryLibrary.dossier`) resolves every account again on each read, as of a knowledge sequence:

| Status | When |
|---|---|
| `current` | the topic's one standing account |
| `contested` | one of several standing accounts no editorial step reconciled; all are kept |
| `superseded` | a supported account resting on evidence at least as late as every supported account on the chain it names replaced it, directly or through that chain |
| `stale` | it cites evidence a correction made obsolete; it lists the terminal replacements and is never current again, whenever it was written |
| `older_basis` | it names an account to replace but rests on older evidence than some supported account on that chain; the chain stands as it was |
| `unsupported` | some cited evidence is not in the actor's projection |

So a corrected character identity is never silently overwritten by an older recap: a recap of the scenes before the correction is `stale`, and a later account built only from older scenes is `older_basis`. Each item carries its resolved evidence (exact excerpt, kind, channel, moment and attribution), its evidence basis and the scenes it draws on, so the dossier is the inspector for each remembered detail. The dossier also carries the collection's evidence `MemoryView` and `suppressed`, every evidence ID the editor's corrections made obsolete as of the read. Statuses do not depend on the order accounts were recorded or read. Every standing account is always kept; `limit` (default 100) bounds only the history beside them, newest first, and `omitted` counts the history left out. `MemoryLibrary.dossier` takes the caller's `knowledge` projection, when it already holds one for the same actor and moment, instead of reading evidence again.

## Context packs

031's `pack_actor_context` and `pack_player_context` (and `contextstore`'s `actor_context` and `player_context`) take an optional `memory` dossier. It must be the packed actor's, as of the pack's moment, and its present controller's: the director's for an NPC, the one human's for a player character (`memory_mismatch` otherwise, also when a standing account cites evidence the pack's projection does not hold). Standing accounts join the memory lane (`You remember: ...`, or `You remember conflicting accounts; one is: ...` for contested ones) with `Origin("summary")` naming the topic, the account and its evidence; suppressed evidence, the reports sent as it and the current beliefs citing it are neither shown, ranked nor used to trigger lore. History never reaches a pack. The pack's `knowledge` digest remains that of the actor's projection, which 027's brief checks.

The scene runner reads an NPC's director dossier in `PLAY_COLLECTION` (`play`) for every NPC pack when given a `MemoryLibrary` on a trusted, issued `app:` key (`bad_memory` otherwise); `TextApi` wires one on `app:memory`. An unreadable memory ledger, or any authority refusal other than an actor the library cannot hold, refuses the turn and the inspector (`memory_unreadable`) rather than packing without it.

## Limits

The ledger is a headless local contract. There is no UI (115) or sync adapter, no automatic or model-written summarization, and no structured identity field: an identity is a topic whose accounts cite evidence. A memory ledger holding summaries fails closed (`invalid_memory_ledger`) on builds older than this one. The shared `campaign_taken_ids` reservation includes `memory_actor_ids`, including references retained after an instance file is removed. This is bounded-view behavior, not a claim of million-word performance. The current evidence projection carries exact excerpt, kind, channel, moment, proposition and attribution, but does not expose additional uncertainty fields not present in `KnownEvidence`.
