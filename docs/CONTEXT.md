# Audience-aware context packing

**Status:** first slice of [Context Retrieval](roadmap/sprints/context-retrieval.md) (Sprint 031, Wave 4). It is implemented as:
- `grimoire.context` (pure): one actor's candidates, chosen by audience rules, then packed into a token budget;
- `grimoire.contextstore`: an adapter that joins 022's `chat_snapshot`, 020's director ledger read and the store's envelopes.

There is no prompt renderer, model call or UI. Nothing here writes or reads files on its own. The fixtures are synthetic.

## What it does

`pack_actor_context(snapshot, knowledge, actor, *, as_of, budget, ...)` returns a `ContextPack` for **one actor** of one frozen view ([EFFECTIVE_VIEWS](EFFECTIVE_VIEWS.md)) at one moment of the [ledger](KNOWLEDGE.md). It works the same in a world-less chat and a world-based one ([CHATS](CHATS.md)).

This slice packs for an **NPC**: memory is 012's `actor_knowledge`, so every current belief reads as the actor's own. A player-controlled actor's NPC-era beliefs are `inherited` under `player_knowledge`; packing for a PC's controller is 027's and 035's.

A pack has three **lanes**, packed in this order:

| Lane | What | From |
|---|---|---|
| `portrayal` | the actor's name line (`You are Name.`), its description, and its composed card sections | 022's `entity(actor)` and `effective(...)` of the actor's **own** binding |
| `memory` | what the actor perceived, was told, said and currently believes | 012's `actor_knowledge` as of `as_of` |
| `lore` | `common` entries of bound `lorebook` and `scenario` sources | a reader for the stored record's schema |

`actor_context(view, chat, ledger, actor, *, as_of, budget, ...)` in `grimoire.contextstore` is the same over one `LibraryView` and one `EvidenceLibrary.read(campaign, requested_by=DIRECTOR)`. It returns a `ChatContext` with the pack, the chat's and scope's etags (so a caller can tell a pack built before a binding changed from one built after), and the chat snapshot's own `problems`. When the ledger read quarantined records, the pack carries the note `knowledge_quarantined`: the actor's knowledge can only have shrunk ([EVIDENCE](EVIDENCE.md)).

## Who may see what

Audience is decided before any ranking or budget, and only from inputs the actor is entitled to.

1. **Only active bindings.** Every source comes from the snapshot's selection, which for a chat is its active bindings. A world is provenance only: nothing in its catalog is read unless it is bound. A detached source contributes nothing.
2. **A card is its own actor's.** A `character` or `persona` binding's composed name, description and sections reach only the actor bound to it. A cleared section never appears (022 composes it away). Another actor's card, an absent antagonist's secret section included, never reaches the pack. A card is never read for lore.
3. **Memory is the actor's own projection.** Confirmed perceptions and held propositions with the current stance, never events, rulings, standing, true senders, unconfirmed evidence or other actors' records. A refuted rumour the actor suspects appears as `You suspect: ...`: it is evidence of a report, not of truth. Lines carry no record ids (012) and name no other actor beyond what the held text itself says.
4. **Lore is `common` only.** A `LoreEntry` defaults to `audience="director"`, so an unclassified entry is withheld. Only `constant` entries and entries whose key occurs in **what the actor perceived** are candidates.
5. **The director's graph is never consulted.** 012's `knowledge_about` and 017's `claims_about` for the actor and the present cast only **rank and annotate** items the actor already holds. A graph connection gives an actor nothing.
6. **What was withheld is not reported.** `omitted` lists candidates the budget shed; `untriggered` counts `common` entries per source that no key matched; `problems` covers only the actor's own binding and the lore bindings. Director entries, other cards, other actors' bindings and unheld records appear in none of them, because what an actor may not see is the director's. The chat's own problems are on `ChatContext.problems`.

### Caller obligations

These cannot be checked here:
- `present` is the cast this actor perceives in the scene. It only ranks memory, but a hidden actor listed there would lift memories about them.
- `scene_text` is text this actor perceived: its own window of the visible transcript. Never a whisper it did not hear, another player's interiority, director narration or table talk. It triggers lore, as do the texts of the actor's own held memory.
- `knowledge` and `claims` are the campaign's ledger and 017 claim shapes from a director read. They are trusted inputs and never handed on; only the returned pack goes toward the actor.

## Lore entries and readers

031 defines no lorebook schema; [Character Card Ingestion](roadmap/sprints/character-card-ingestion.md) (037) owns the imported formats. Instead a caller passes `readers`, a mapping of a stored record's `schema` (its envelope `extra["schema"]`) to a function from the record's payload to `LoreEntry` values, and `unpack`, which gives a raw definition's `(schema, payload)`. `grimoire.contextstore.stored_record` is the store's `unpack`.

- A reader gets a fresh plain copy of the payload (dicts and lists), so it cannot change the loaded view.
- Only the **selected revision** of a `lorebook` or `scenario` binding is read, never an ancestor. A revision selected under both roles is read once.
- A reader may not claim a `grimoire.entity/` schema: entities are composed, never read raw for lore (`invalid_readers`).
- A source that cannot be read contributes nothing and is reported in `problems`, never failing the turn:

| `SourceProblem.code` | when |
|---|---|
| `no_entry_reader` | no reader for the record's schema (`detail`: the schema), or the definition has no record |
| `unreadable_entries` | the reader raised `ValueError`, `TypeError`, `LookupError` or `AttributeError` (`detail`: the exception's text `reason`, else its class), returned something other than `LoreEntry`s (`not_lore_entry`), returned an id twice (`duplicate_entry`), the payload could not be copied (`too_deep`, `uncopyable`), or `unpack` gave something other than `(schema, mapping)` (`invalid_unpack`) |
| `too_many_entries` | more than 4096 entries (`detail`: `>4096`; the reader's output is not read past that) |
| `missing_source` | the binding does not resolve (`detail`: 022's broken reason) |
| 022 notices | `binding_drift` (`detail`: `r1 -> r2`) and the other resolution notices of a head-following binding, passed through; the pack still builds from the head now in effect |

Any other exception from a reader is a bug and propagates.

`LoreEntry(entry_id, text, keys=(), constant=False, audience="director", order=0)`: `entry_id` follows the identity segment grammar; `text` is safe text of at most 8000 characters; `keys` is a collection of at most 64 distinct single-line keys of at most 100 characters, each with at least one word; `audience` is `common` or `director`; `order` is an int within ±1,000,000.

**037 handoff:** an importer that registers a reader must classify each entry's audience. An entry left at the default stays director-only.

## Triggering

Matching is deterministic and model-free (`grimoire.context.trigger`). Text is NFC-normalized, casefolded and split into word runs of letters, marks, digits and connector punctuation (Unicode `L*`, `M*`, `N*`, `Pc`), so a key matches a whole word sequence: `ash` does not match `ashen`, `Black-Eyed` matches `black eyed`. Combining marks stay inside their word (a Devanagari or Arabic word with marks is one word), and `İstanbul` matches `istanbul`. Each perceived text is matched on its own, so a key never spans two texts. Keys that normalize alike count once.

A lookup first checks that every word of the key occurs in a text, then scans only the occurrences of its rarest word; results and key normalization are remembered, so a lorebook at its limits (4096 entries of 64 keys) costs time linear in its keys.

**Limit:** scripts written without spaces (Chinese, Japanese, Thai) form one word run per stretch, so a key there matches only a whole run.

## Ranking and the budget

`Budget(max_tokens, lane_caps=(), item_overhead=4)`. `lane_caps` is a mapping, or a tuple or list of pairs, of lane to cap, normalized to lane order; an uncapped lane may use the whole remainder. A `memory` or `lore` cap of 0 shuts that lane; the `portrayal` cap must hold at least the name line. `item_overhead` (at least 1) is added to each item's count.

Packing is greedy and deterministic: lanes in order, each lane in rank order, first fit. An item is included when its cost fits both its lane's remainder and the pack's; otherwise it is omitted as `lane_cap` (it fit the pack but not its lane) or `over_budget`, and a smaller item later in the lane may still fit. The total never exceeds `max_tokens`. If even the name line does not fit, `budget_too_small` is raised: a pack never leaves the actor nameless. A long card description is its own item, shed like any other.

| Lane | Rank (first wins) |
|---|---|
| portrayal | name, description, then sections in composed order |
| memory | about the actor or a present actor first, then newest, then perceptions before propositions, then id |
| lore | constant first (by `order`), then more distinct key hits, then `order`, then source and entry id |

`count` is the route's token counter; `measurement` records whether it is `estimated` (the default `estimate_tokens`, UTF-8 bytes / 3 rounded up, an estimate and not a bound for any real tokenizer) or `counted`. With the default estimate and an overhead of at least 1, `estimate_tokens(pack.render())` never exceeds `pack.tokens`.

`exclude_sections` names composed card sections left to another owner; by default `voice-evidence`, which 014/027 own.

## Refusals

`ContextError` (a `ValueError`) with a machine-readable `reason`: `invalid_audience`, `campaign_mismatch`, `invalid_as_of`, `invalid_budget`, `invalid_present`, `invalid_scene_text`, `invalid_readers`, `invalid_measurement`, `invalid_claims`, `invalid_sections`, `invalid_entry`, `invalid_count`, `invalid_lane`, `unresolved_audience` (the actor has no entity in this snapshot) and `budget_too_small`. A ledger or snapshot of the wrong type raises `TypeError`. `actor_context` lets 022's `RecordNotFound` and `ChatCodecError` propagate.

## Not in this slice

- **Director packs, recursion and other actors' appearance:** an actor sees no description of anyone else; perception of appearance is a later milestone.
- **World records:** a world's locations and other definitions are read only when bound, and 022 selects only bound sources. Selecting world records by relevance waits for 022's world selection.
- **Recent history:** transcript windows belong to 027 and 035; this slice takes `scene_text` from the caller.
- **Holdings, mechanics and inventory**, **superseded beliefs**, **embeddings**, **real tokenizers** and **card-section audience** (a card section is its own actor's; per-section secrecy waits for 037/027).
- **Binding time vs `as_of`:** the snapshot is the chat's bindings now; a source attached after `as_of` still contributes.
- **CJK segmentation** (see Triggering).
