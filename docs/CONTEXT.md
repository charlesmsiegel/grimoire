# Audience-aware context packing

**Status:** first slice of [Context Retrieval](roadmap/sprints/context-retrieval.md) (Sprint 031, Wave 4). It is implemented as:
- `grimoire.context` (pure): one actor's candidates, chosen by audience rules, then packed into a token budget; and, with `director_lore`, the scene-level director reply's lore, triggered by table-visible text ([below](#director-lore));
- `grimoire.contextstore`: an adapter that joins 022's `chat_snapshot`, 020's director ledger read and the store's envelopes.

027's briefs render both ([GENERATION](GENERATION.md#from-031s-packs)): `npc_brief(..., context=pack, snapshot=snap)` and `director_brief(..., lore=lore, snapshot=snap)`, each refusing a pack built over another selection ([below](#the-selection-a-pack-is-built-over)). That is the seam: `grimoire.generation` imports these value types and `selection_basis`, and nothing here imports 027. There is no model call or UI here. Nothing here writes or reads files on its own. The fixtures are synthetic.

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

1. **Only active bindings.** Every source comes from the snapshot's selection, which for a chat is its active bindings and, right after each bound scenario, the lorebook that scenario's selected revision names ([CARDS](CARDS.md#chats)). A world is provenance only: nothing in its catalog is read unless it is bound. A detached source contributes nothing.
2. **A card is its own actor's.** A `character` or `persona` binding's composed name, description and sections reach only the actor bound to it. A cleared section never appears (022 composes it away). Another actor's card, an absent antagonist's secret section included, never reaches the pack. A card is never read for lore.
3. **Memory is the actor's own projection.** Confirmed perceptions and held propositions with the current stance, never events, rulings, standing, true senders, unconfirmed evidence or other actors' records. A held proposition with a current belief leads with its stance (`You believe:`, `You suspect:` or `You do not believe:`); one only reported leads with how it arrived (`You were told:`, `You heard (overheard):`, or `You told someone:` for one the actor only sent). A refuted rumour the actor suspects appears as `You suspect: ...`: it is evidence of a report, not of truth. Lines carry no record ids (012) and name no other actor beyond what the held text itself says.
4. **Lore is `common` only.** A `LoreEntry` defaults to `audience="director"`, so an unclassified entry is withheld. Only `constant` entries and entries whose key occurs in **what the actor perceived** are candidates.
5. **The director's graph is never consulted.** 012's `knowledge_about` and 017's `claims_about` for the actor and the present cast only **rank and annotate** items the actor already holds. A graph connection gives an actor nothing.
6. **What was withheld is not reported.** `omitted` lists candidates the budget shed; `untriggered` counts `common` entries per source that no key matched; `problems` covers only the actor's own binding and the lore bindings. No director entry's text or id, other card, other actor's binding or unheld record appears in any of them, because what an actor may not see is the director's. A lore source's problem describes the source as read, whole: a source over the entry limit, or one with an invalid or duplicate entry, is reported and contributes nothing, whichever audience the offending entry has. The chat's own problems are on `ChatContext.problems`.

### Caller obligations

These cannot be checked here:
- `present` is the cast this actor perceives in the scene. It only ranks memory, but a hidden actor listed there would lift memories about them.
- `scene_text` is text this actor perceived: its own window of the visible transcript. Never a whisper it did not hear, another player's interiority, director narration or table talk. It triggers lore, as do the texts of the actor's own held memory.
- `knowledge` and `claims` are the campaign's ledger and 017 claim shapes from a director read. They are trusted inputs and never handed on; only the returned pack goes toward the actor.

## Lore entries and readers

031 defines no lorebook schema; [Character Card Ingestion](roadmap/sprints/character-card-ingestion.md) (037) owns the imported formats. Instead a caller passes `readers`, a mapping of a stored record's `schema` (its envelope `extra["schema"]`) to a function from the record's payload to `LoreEntry` values, and `unpack`, which gives a raw definition's `(schema, payload)`. `grimoire.contextstore.stored_record` is the store's `unpack`.

The library's own lore formats have readers: 037's `grimoire.cards.LORE_READERS` read `grimoire.lorebook/1` and `grimoire.scenario/1` ([CARDS](CARDS.md#lore-in-play-031)). `actor_context` uses them when its caller passes no `readers` (`grimoire.contextstore.DEFAULT_READERS`; `{}` reads no format, and a caller with formats of its own passes `{**DEFAULT_READERS, **its_own}`). The pure `pack_actor_context` and `director_lore` default to no readers. The store-backed director path is `chat_lore(view, chat, *, table_text, budget, readers=None, ...)`, which returns a `ChatLore` with the `LorePack` and, like `ChatContext`, the chat snapshot's etags and `problems` (such as `invalid_scenario_lorebook:<ref>`, lore the chat could not select, which the pack's own `problems` cannot show). A caller that runs `director_lore` over `chat_snapshot(...)` itself must surface that snapshot's `problems` too. Card lore is **`common`**: as in SillyTavern, any NPC in the scene may know it. A triggered entry reaches each NPC whose own perceived text (its window of the scene, or its own held memories) shows one of its keys, and the director's lore when the table text shows one; a constant entry reaches every pack. It is never director-only and never one NPC's secret.

- A reader gets a fresh plain copy of the payload (dicts and lists), so it cannot change the loaded view.
- Only the **selected revision** of a `lorebook` or `scenario` binding is read, never an ancestor. A revision selected under both roles is read once.
- A reader may not claim a `grimoire.entity/` schema: entities are composed, never read raw for lore (`invalid_readers`).
- A source that cannot be read contributes nothing and is reported in `problems`, never failing the turn:

| `SourceProblem.code` | when |
|---|---|
| `no_entry_reader` | no reader for the record's schema (`detail`: the schema), or the definition has no record (with `actor_context`'s default readers, a hand-written record of an unknown schema) |
| `unreadable_entries` | the reader raised `ValueError`, `TypeError`, `LookupError` or `AttributeError` (`detail`: the exception's text `reason`, else its class; so one malformed entry blanks its whole source), returned something other than `LoreEntry` or `SkippedEntry` values (`not_lore_entry`), returned an id twice (`duplicate_entry`), the payload could not be copied (`too_deep`, `uncopyable`), or `unpack` gave something other than `(schema, mapping)` (`invalid_unpack`) |
| `too_many_entries` | more than 4096 entries, skipped ones included (`detail`: `>4096`; the reader's output is not read past that) |
| `skipped_entry` | the reader left an entry out because 031 cannot honour it, and said so with a `SkippedEntry(entry_id, reason, audience="director")` (`detail`: `<entry_id>:<reason>`, never its text); reported only to a reader of its audience, and the rest of the source still contributes |
| `missing_source` | the binding does not resolve (`detail`: 022's broken reason) |
| 022 notices | `binding_drift` (`detail`: `r1 -> r2`) and the other resolution notices of a head-following binding, passed through; the pack still builds from the head now in effect |

Any other exception from a reader is a bug and propagates.

`LoreEntry(entry_id, text, keys=(), constant=False, audience="director", order=0, secondary_keys=(), secondary_logic="and_any")`: `entry_id` follows the identity segment grammar; `text` is safe text of at most 8000 characters (no control, format, surrogate or line/paragraph separator characters except newline, tab, ZWNJ and ZWJ, so invisible bidi and zero-width marks are refused); `keys` and `secondary_keys` are each a collection of at most 64 distinct single-line keys of at most 100 characters, each with at least one word (`grimoire.context.valid_key` tells a reader which keys to drop); `audience` is `common` or `director`; `order` is an int within ±1,000,000; `secondary_logic` is one of `SECONDARY_LOGICS`.

**Secondary keys** narrow a key hit, and never trigger an entry on their own: `and_any` needs one of them to occur too (in any of the texts that trigger lore, as a key does), `and_all` all of them, `not_any` none of them and `not_all` not all of them. With no secondary keys, a key hit stands. A `constant` entry ignores them. Only the primary keys' distinct hits rank the entry. This is the selective logic of card lorebooks (037).

**Reader obligations:** a reader must classify each entry's audience; an entry left at the default stays director-only. Because one entry that fails `LoreEntry`'s checks blanks its source, a reader should skip or sanitize a bad entry rather than raise, and should hand over a `SkippedEntry` for one it leaves out, so the loss is visible. Sanitizing must never widen an entry: a reader that cannot keep every secondary key skips the entry. 037's card readers do all of this.

## Triggering

Matching is deterministic and model-free (`grimoire.context.trigger`). Text is NFKC-normalized (so fullwidth `ＦＥＲＲＹ` is `ferry`), casefolded, stripped of invisible format characters first (Unicode `Cf`: a soft hyphen or zero-width character inside a word neither splits it nor stops an accent after it from composing) and split into word runs of letters, marks, digits and connector punctuation (Unicode `L*`, `M*`, `N*`, `Pc`), so a key matches a whole word sequence: `ash` does not match `ashen`, `Black-Eyed` matches `black eyed`. Combining marks stay inside their word (a Devanagari or Arabic word with marks is one word), and `İstanbul` matches `istanbul`. Each perceived text is matched on its own, so a key never spans two texts. Keys that normalize alike count once.

Each `LoreEntry` normalizes its keys once. The perceived texts become word ids, and each lore source's keys are answered in one batch: the keys are hashed into a table per key length, and each text is rolled over once per distinct length, every hash hit confirmed against the window itself. Time is the texts' words times the distinct key lengths plus the keys (secondary keys count as keys here); memory is the texts plus the keys. A pass stops once every key of its length is found. A source at its limits (4096 entries of 64 keys) takes, per actor, about 1.5 s with ASCII keys of a few lengths and up to about 4.5 s when its keys span all 50 possible word lengths over a 100,000-character scene; 100-character non-ASCII keys take up to about 7 s, most of it validating and normalizing keys that a reader hands over afresh for every pack. The character tables behind normalization are bounded (they start over past 65,536 code points).

**Limit:** scripts written without spaces (Chinese, Japanese, Thai) form one word run per stretch, so a key there matches only a whole run; likewise a Korean noun with a particle attached is one word, which the bare noun does not match.

## Director lore

`director_lore(snapshot, *, campaign_id, table_text, budget, readers=None, unpack=None, count=estimate_tokens, measurement="estimated")` returns a `LorePack` for the scene-level director reply. `campaign_id` is the campaign whose chat `snapshot` is (`invalid_campaign` unless an id segment); a snapshot of a chat with no actors cannot say it. A scenario-only chat has no speaking NPC, so without it no pack would carry the chat's lorebook to anyone.

- The director is no actor: there is no portrayal or memory lane, and no card is read.
- Lore comes from the same bound `lorebook` and `scenario` revisions, readers and `unpack` as an actor's, but **`common` and `director` entries** are both candidates.
- Only `constant` entries and entries whose key occurs in **`table_text`** are candidates. **Caller obligation:** `table_text` is what the whole table saw (the visible transcript window), never a whisper, an NPC's private perception or a player's interiority. Lore never triggers lore.
- Ranking, the budget's `lore` lane, `omitted`, `untriggered` (both audiences counted, since the director may know of them) and `problems` (the lore bindings) are as for an actor. The same refusals apply, with `invalid_scene_text` for `table_text`.
- `LorePack.table_text` keeps the `table_text` its entries were triggered on. The table saw it, so `director_brief` counts it as exposed.
- Every item carries its entry's `audience` (`PackedItem.audience`; `LorePack.audience("common")` selects them). The renderer keeps the two apart: `director_brief` lets the narrator repeat `common` lore but never treats `director` lore as table-visible.

A `LorePack` is the director's alone. `npc_brief` refuses one (`foreign_context`), and refuses an actor pack holding any lore item that is not `common`.

## The selection a pack is built over

Two chats of one campaign can share an actor (107's `reuse_actors`) and her card revision yet bind different lore, a chat's lorebook can be detached or re-pinned between turns, and a definition file can be edited by hand in place ([STORAGE](STORAGE.md)), changing its content but not its reference. The actor, the card and the moment cannot tell such packs apart, so every pack records its **`basis`**, a `SelectionBasis`:

- `campaign_id`: the actor's campaign, or `director_lore`'s `campaign_id`;
- `actor`: the audience, `None` for director lore;
- `bindings`: sorted `(role, selected ref, resolved ref or None, contents)` of every `lorebook` and `scenario` binding and, for an actor, of its own bindings; other actors' bindings are not read, so they do not count. `contents` are the content identities of what was read through the binding: a lore source's selected revision, or every layer of the actor's composed portrayal (a hand edit of a base card changes a derived one). Each is the revision's stored etag (`EffectiveSnapshot.etag`), or, for a definition with none (one built in memory), a SHA-256 of its `repr`, which can only refuse equal content, never admit changed content;
- `instance_etag`: the captured actor instance's etag (`None` for director lore and for pure snapshots with no etags).

An actor's pack also records **`knowledge`**, `knowledge_digest(projection)`: a SHA-256 of the `repr` of the 012 `ActorKnowledge` it packed (plain frozen values, so equal projections digest alike). A ledger reload that quarantines a record shrinks the projection at the same `as_of` and the same bindings, so the basis cannot tell; `npc_brief` compares this digest with the view's projection and refuses a pack still holding the lost record (`context_mismatch`), whose memory would otherwise show and count as exposed. Director lore reads no knowledge (it triggers on `table_text` only), so it has no such field.

`selection_basis(snapshot, campaign_id, actor=None)` computes the same value for any snapshot. 027's briefs compare the two and refuse a pack whose basis differs (`context_mismatch`) or whose campaign differs (`foreign_context` for director lore); see [GENERATION](GENERATION.md#from-031s-packs). A head-following binding whose head moved resolves to another revision, and a hand-edited revision has another etag, so either basis differs too: a pack built before an edit that made a `common` entry the director's is refused rather than showing it, and counting it as exposed. `ChatContext`'s etags remain for a caller that wants to tell chat records apart without a snapshot.

## Ranking and the budget

`Budget(max_tokens, lane_caps=(), item_overhead=4)`. `lane_caps` is a mapping, or a tuple or list of pairs, of lane to cap, normalized to lane order; an uncapped lane may use the whole remainder. A `memory` or `lore` cap of 0 shuts that lane; the `portrayal` cap must hold at least the name line. `item_overhead` (at least 1) is added to each item's count.

Packing is greedy and deterministic: lanes in order, each lane in rank order, first fit. An item is included when its cost fits both its lane's remainder and the pack's; otherwise it is omitted as `lane_cap` (it fit the pack but not its lane) or `over_budget`, and a smaller item later in the lane may still fit. The total never exceeds `max_tokens`. If even the name line does not fit, `budget_too_small` is raised: a pack never leaves the actor nameless. A long card description is its own item, shed like any other.

| Lane | Rank (first wins) |
|---|---|
| portrayal | name, description, then sections in composed order |
| memory | propositions about the actor or a present actor first (perceptions are of events, never in focus), then newest, then perceptions before propositions, then id |
| lore | constant first (by `order`), then more distinct key hits, then `order`, then source and entry id |

`count` is the route's token counter; `measurement` records whether it is `estimated` (the default `estimate_tokens`, UTF-8 bytes / 3 rounded up, an estimate and not a bound for any real tokenizer) or `counted`. With the default estimate and an overhead of at least 1, `estimate_tokens(pack.render())` never exceeds `pack.tokens`. Every item is one line: the packer joins a multi-line text (a lore entry's paragraphs, a card section) into one line before counting it, so `render()` has exactly one line per item and a source's text cannot forge an identity or memory line.

`exclude_sections` names composed card sections left to another owner; by default `voice-evidence`, which 014/027 own.

## Refusals

`ContextError` (a `ValueError`) with a machine-readable `reason`: `invalid_audience`, `campaign_mismatch`, `invalid_as_of`, `invalid_budget`, `invalid_present`, `invalid_scene_text`, `invalid_readers`, `invalid_measurement`, `invalid_claims`, `invalid_sections`, `invalid_entry`, `invalid_count`, `invalid_unpack`, `invalid_lane`, `invalid_campaign` (`director_lore`'s or `selection_basis`'s `campaign_id` is not an id segment), `unresolved_audience` (the actor has no entity in this snapshot) and `budget_too_small`. A ledger or snapshot of the wrong type raises `TypeError`. `actor_context` lets 022's `RecordNotFound` and `ChatCodecError` propagate.

## Not in this slice

- **Director knowledge, recursion and other actors' appearance:** the director gets bound lore only (`director_lore`), never the ledger; lore never triggers lore; an actor sees no description of anyone else, and perception of appearance is a later milestone.
- **World records:** a world's locations and other definitions are read only when bound, and 022 selects only bound sources. Selecting world records by relevance waits for 022's world selection.
- **Recent history:** transcript windows belong to 027 and 035; this slice takes `scene_text` and `table_text` from the caller. Building every speaker's pack and the director's lore each turn is 035's.
- **Holdings, mechanics and inventory** (an actor's own counted holdings and unique items in its pack: 031's next slice, unscheduled), **superseded beliefs**, **embeddings**, **real tokenizers** and **card-section audience** (a card section is its own actor's; per-section secrecy waits for 037/027).
- **Binding time vs `as_of`:** the snapshot is the chat's bindings now; a source attached after `as_of` still contributes.
- **CJK segmentation** (see Triggering).
