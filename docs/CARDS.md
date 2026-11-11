# Card import

**Status:** first slice of [Character Card Ingestion](roadmap/sprints/character-card-ingestion.md) (Sprint 037, Wave 4). It is implemented as:
- `grimoire.cards`: pure parsing of an already-decoded card document, mapping to record payloads, role classification and name-mention link suggestions;
- `grimoire.cardstore`: a store-backed importer (`CardImporter`) that stages one import in a single guarded batch over [the native store](STORAGE.md) and [018's image batch](IMAGES.md), with the reader of PNG cards (`cardstore.png`) and `card_facts`, which says what a card file is before anything is planned.

There is **no CHARX reader, no V1 card support and no network** (the app's Import page is [REVIEW_UI](REVIEW_UI.md)). Nothing is fetched and no model is called. A green test suite proves this contract with synthetic cards written for the tests; it is not a playable app. 026's world importer is not needed and not used.

## What a card becomes

A card is interchange data, not native storage. An import creates (or updates) one **card source**, `card:<slug>`, and writes its texts as source records that keep their authored roles. Nothing a card says becomes an event, evidence, a belief or canon, and nothing is linked by name.

| Input | Records in the card source (`grimoire.record/1`, top-level `schema`) |
|---|---|
| Character Card V3 (`spec: chara_card_v3`, `spec_version: 3.x`) | `character/<slug>` (`grimoire.entity/0`), plus `lorebook/<slug>-lore` (`grimoire.lorebook/1`) when it embeds a `character_book` |
| Character Card V2 (`spec: chara_card_v2`, `spec_version: 2.x`) | the same as a V3 card: V2's `data` is read with V3's field set, and the record's `card.format` says `chara_card_v2` |
| A PNG card: any of the above as base64 JSON in a `tEXt` chunk keyed `ccv3` or `chara` ([below](#png-cards)) | what its embedded document becomes, with the PNG itself as the portrait |
| The same card with `as_scenario=True` | `scenario/<slug>` (`grimoire.scenario/1`): never a fake `Agent` character |
| Grimoire scenario document (`format: grimoire.scenario-card/1`) | `scenario/<slug>`, plus its lorebook |
| V3 standalone lorebook (`spec: lorebook_v3`) | `lorebook/<slug>` |
| Each separate `lorebook_v3` file passed with a character or scenario card (at most 8) | an extra `lorebook/<slug>` record |
| Every import | `card-import/import` (`grimoire.card-import/1`), see [The import record](#the-import-record) |

The manifest declares every head. A portrait (`portrait=` bytes) becomes one 018 image object in the card source and a `set` placement in slot **`avatar`** of the imported character or scenario. Its provenance names the card's sha256, format and origin label.

### PNG cards

A PNG card is a picture that carries its card. `cardstore.png.read_png_card` finds the card document in a `tEXt` chunk whose keyword is `ccv3` (Card V3) or `chara` (the older key, usually a V2 card), compared without case; `ccv3` is preferred when both are there. The chunk's text is base64 of the card's JSON. Pixels are never decoded.

- **The whole file is checked before a card is read out of it:** the signature; a first `IHDR` chunk of 13 bytes with a width and a height, and no second one; every chunk's four-letter type, its length against what is left of the file, and its CRC; picture data (`IDAT`); an empty `IEND`; and nothing after it. A file over 32 MiB (038's cap on one picture) or of more than 65,536 chunks is refused before it is walked.
- **Refusals** (`CardError`, nothing staged): `invalid_png` with `detail` `signature`, `truncated`, `header`, `chunk_type`, `crc`, `no_image_data`, `no_end`, `trailing_data`, `too_many_chunks`, `text` (a text chunk with no keyword), `duplicate_card` (two different cards under one keyword) or `base64`; `no_embedded_card` for a sound picture with no `ccv3` or `chara` `tEXt` chunk (a card in a `zTXt` or `iTXt` chunk is not read); `too_large` for the file or for an embedded card over the 2 MiB a card may be. The embedded document is then read as any card file is, so `invalid_json`, `invalid_card` and `unsupported_format` mean what they mean for a JSON file.
- **The card is its embedded document.** The import record's `original` is the decoded JSON, byte for byte, so a PNG card and the same document as a JSON file are the same card (`same_bytes`), and importing the JSON with that PNG as its `portrait` is the same import. The record also keeps `container`: `{format: "png", keyword, sha256, size}` of the PNG file.
- **The PNG is the portrait**, placed exactly as a `portrait=` upload is (the whole file as one blob, text chunk included, in slot `avatar`, with the same provenance), unless the caller gives a `portrait` of its own. A PNG whose document is a lorebook has no portrait.

### Card art (038)

A V3 card's `data.assets` become 018 placements on the imported character or scenario too ([038](roadmap/sprints/expression-and-art-lineage.md); `grimoire.cards.card_art` decides the slots from the card alone, `grimoire.cardstore.art` what an import writes there):

| asset | slot |
|---|---|
| the caller's `portrait` | `avatar` |
| the main `icon` (named `main`, else the first icon) | `avatar`; with a `portrait`, a `ccdefault:` main icon *is* that portrait and any other main icon goes to `icon-main` |
| any other `icon` | `icon-<name>` (`icon-<index>` when its name is `main` too, so it never takes the main icon's slot) |
| `emotion` | `expr.default.<name>`: a sprite of the card's one outfit, `default` |
| the main `background` (named `main`, else the first) | `background` |
| any other `background` | `background-<name>` |
| `user_icon` | none: it pictures the user (`not_applicable` loss) |
| any other type | `asset-<name>` |

`<name>` is the asset's name as an identity segment. A name with no ASCII letter or digit (`笑顔`, `怒り`) gives no segment, so it is the first 8 hex digits of the sha256 of the name, trimmed and in Unicode NFC (`expr.default.emotion-b80c40ec`, `icon-<hash>`, `background-<hash>`, `asset-<hash>`): the slot follows the name, so reordering a card's assets in an update never moves a picture, or an individual's own picture over it, to another sprite. A name that loses some letters or digits to the fold to ASCII (`笑顔 2`) keeps its slug with that hash after it (`expr.default.2-<hash>`), so it never shares a slot with another such name (`怒り 2`); an accent alone loses nothing (`Café` is `cafe`). Only an asset with no name, or a blank one, takes its place in the array (`emotion-3`), which a reorder does move. The first asset to claim a slot wins; a later one is a `duplicate_slot` loss, and assets past the first 64 slots are `too_many`.

A card imported before this rule (Wave 5 audit) has such a sprite in its old place-numbered slot (`expr.default.emotion-0`). Importing it again places the sprite in its name's slot, reusing the same image object and blob, and the old slot keeps its last picture, as any slot a card no longer fills does (an earlier build was never released; an individual's own picture over the old slot stays there).

- **Bytes come only from the card.** A `data:` URI with base64 content is decoded locally; `ccdefault:` is the given `portrait`. Nothing is fetched and no file is read.
- **A missing picture keeps its slot.** An `http(s)` address (`not_fetched`), a CHARX `embeded://` path, `ccdefault:` with no portrait, a `data:` URI that cannot be decoded or is not a PNG, JPEG, GIF or WebP picture, and any other address are written as an explicit `unresolved` placement whose `provenance.note` says why, so the slot never shows another picture found by name. Each is also a loss (`not_fetched` for a remote picture, `unavailable` otherwise).
- **A later copy without the picture keeps it.** When a slot still holds the picture an earlier import of the card placed and this import has no bytes for it (the card read again without its portrait, or with that asset now remote), the picture stands: the slot is reported `kept`, never blanked to `unresolved`.
- **One object per picture.** Identical bytes are one image object per card source and one blob per library, whichever assets carry them and in whichever order imports make them the portrait or an asset: an import reuses the object already stored for them, else names a new one `portrait-<sha256[:16]>` for the portrait's bytes and `art-<sha256[:16]>` for any other. A placement's provenance names its asset (`x-card-asset`: index, type, name, and the sha256 of the picture placed).
- **Individuals inherit it.** Every campaign instance derived from the imported character sees the card's art through 018's inheritance; its own `set`, `adjust` or `hide` changes only that individual, and an updated card moves only the art the individual inherits.
- **Whose slot it is.** A slot is the card's to rewrite when it holds what the import record says the last import placed there; when the record has no entry for it (an older build rewrote the record), when the placement itself is one an import writes: its provenance names a card asset, it is `unresolved` or shows the card source's picture for the sha256 that provenance names, and it has no crop or focus. Anything else is a person's choice. An update also rewrites a slot whose picture is unchanged but whose asset moved or whose reason for missing changed, so the provenance stays true.
- `ImportReport.art` lists every slot the import fills: `placed`, `unchanged`, `kept` or `hand_edited`, with the card's picture or why it is missing, and the asset's index.

### The character record

A 009 `individual` with `agent` and spoken `communicative` traits.
- `description` is 009's field, verbatim.
- `personality` becomes the 022 section `personality` when 022 would accept it. Otherwise it is kept verbatim under `card.unsectioned` and reported as a `not_section` loss.
- The card's `scenario` prompt and `mes_example` are kept verbatim under `card.texts` (`scenario`, `example-messages`), never as 022 sections: they are instructions and examples, not facts about the character, so 022's view never composes them.
- Everything else sits under the 037-owned `card` key, which 022 does not compose: `greetings` (`first`, `alt-N`, `group-N` with `group_only`), `instructions` (`system_prompt`, `post_history_instructions`), `nickname`, `tags`, `authorship` (creator, version, notes, source, dates), `lorebook` (the embedded lorebook's **pinned** ref), `assets` as written, `extensions` and unknown fields (`extras`) verbatim.
- `card.roles` names the role of each section and text (`personality`, `texts.scenario`, `texts.example-messages`…), so a prompt builder never mistakes a scenario instruction or an example for a character fact.
- **Voice.** No voice profile is written. 027's `SnapshotVoices` derives one from the character revision's own `texts.example-messages` (its `{{char}}` turns), `personality` and description when a play catalog is built, and fills 014's `ImportedVoiceSource` with the exact `mes_example` and the card's extensions ([GENERATION](GENERATION.md#imported-voices)). Instructions, the scenario, greetings and creator notes are never read for it. Each revision derives its own: an update's `r2` speaks with `r2`'s text, a chat pinned to `r1` keeps `r1`'s voice. A card with no usable text at all stays `MissingVoice("no_voice_text")`. Only the character's own example turns are kept, and any line that might be another speaker's (a macro, an early colon or dash) ends a turn (fail closed).

### Scenario and lorebook records

A scenario holds `title`, `premise`, `openings` (possible starts, never canon), `instructions`, other `texts` (a V3 card read as a scenario keeps its description as `texts.setting`), `cast` exactly as written, its lorebook's pinned ref, and `roles`. A lorebook holds its description and its entries' keys and content as authored lore, with known V3 settings kept and **not interpreted** by the importer (031's reader reads some of them in play: [below](#lore-in-play-031)). A lorebook document's own top-level unknown fields sit under `extras.card` and its book's under `extras.data`, so neither overwrites the other. Neither is an entity, so 022 returns them raw (`not_entity_definition`).

## Roles

Every imported text has one role, shown apart in the preview (`ImportReport.pieces`, which carries the card's own field paths and lengths, never the text; a V3 card read as a scenario names `data.scenario`, `data.system_prompt`… as the fields its premise and instructions came from). A lorebook's description is lore, and an entry's `name` and `comment` share its entry's role:

`authored_description`, `authored_style` (personality), `authored_voice` (example messages), `scenario_instruction` (a card's `scenario`), `private_instruction` (system prompt, post-history instructions), `opening_greeting`, `lorebook_content`, `scenario_premise`, `scenario_opening`, `creator_note`.

There is no `accepted_event` role, and `ImportReport.accepted_events` is always `()`.

## Planning, approval and commit

```python
importer = CardImporter(store, worker_handle, owner="human:owner")
planned = importer.plan(card_bytes, origin="mara.json", portrait=png_bytes, lorebook=None, lorebooks=(),
                        target=None, as_scenario=False, replace_edits=False, embedded_lorebook=True,
                        update_same_card=False)
planned.report      # status, destination, roles, losses, collisions, suggestions, cast, paths, grant scopes
planned.preview()   # {logical path: exact bytes}: what a human approves
planned.commit(approvals)  # {path: approval id}
```

- `card_facts(card_bytes, as_scenario=False)` reads no library: it decodes the file as `plan` would and names its container (`png` with its chunk keyword, or `json`), format, what it becomes, its name, whether the file is also the card's picture, how many fields would not be carried over, and the embedded lorebook (the name its record would have and its entry count). It refuses what `plan` refuses before reading the library.
- **The embedded lorebook can be left out.** `embedded_lorebook=False` imports a character or scenario without the lorebook its card embeds: no lorebook record is written, the character's `card.lorebook` (or the scenario's `lorebook`) is absent, and nothing inside the left-out book is reported as a loss. The choice is part of the import's identity (`options.embedded_lorebook: false` in the import record, and in its digest), so the same card with and without its lorebook are different imports; one that keeps the lorebook is stored exactly as before this option existed. Updating an earlier import of the card this way keeps the lorebook record it wrote (`kept`), no longer named by the character. A lorebook card refuses the option (`invalid_options`).
- **Several lorebook files.** `lorebooks` are further separate lorebook files after `lorebook` (the two are one ordered list; at most 8 in all, and the same file twice is `invalid_options`). Each becomes its own lorebook record in the card source: the first under the role `lorebook_file`, as one file always did, the next under `lorebook_file-2`, `lorebook_file-3`… Their order is part of the import's identity. A file left out of a later update keeps the record it wrote (`kept`). A character's record still names only its own embedded lorebook (`card.lorebook`); a scenario with none embedded names the first file. **A lorebook already in the library is not bound to a card at import**: a card record names exactly one lorebook, its own, which is what a `bundle` launch offers, and any other lorebook is chosen when a chat is started or attached to it later ([CHATS](CHATS.md)).
- `plan` reads the library and writes nothing. It asks the handle to **`propose`** every staged path with its exact bytes; any refusal raises `ImportDenied` (`denied` lists `(path, reason)`), and nothing is staged.
- `commit` goes through 010's guarded writer. An untrusted handle needs a covering `apply` grant **and** an approval bound to the payload digest of every path. Approvals for other bytes, or for only some paths, write nothing. A commit happens once.
- The grants an import needs are reported as `grant_scopes`: `sources/card/<slug>/_source`, and `assets/blobs` when a new picture blob is written.
- `plan` runs the commit's own checks read-only on every write of the batch (records, the portrait's image, blob and placement), so a plan a human approves is not refused at commit for the state of its source (`invalid_target` at plan instead: a stray `Images/` folder or `placements` file, a manifest holding a number a double cannot hold, one entity id under two kinds).
- `StaleWrite` if the library changed between plan and commit (for example, another import took the destination). Nothing is written.

### The destination

Chosen by trusted code, never by card text:
1. an explicit `target` source id, which must be in the `card` namespace (`invalid_target` otherwise). A source that exists but was not made by a card import is refused `not_a_card_source`, as is any source outside the `card` namespace, whatever records it holds; an id the library cannot hold is `invalid_target`;
2. otherwise the `card:` source an identical earlier import went to (only `card:` sources are read as earlier imports) (same bytes, format, options, lorebook file and portrait);
3. with `update_same_card=True`, otherwise the one `card:` source whose latest import had **exactly these card bytes** and the same `as_scenario`: the import is then an `update` of that source (a PNG card of a card first imported as JSON adds its picture there). Two such sources, or none, choose nothing. A name never identifies a card: a different card of the same name goes on to rule 4 and is reported `same_name`;
4. otherwise `card:<slug of the name or title>`, with `-2`, `-3`… when that exists (the report then carries a `destination_taken` collision). A slug Windows reserves (`con`, `nul`, `com1`…) gets `-card` added, here and for entity ids.

### Reruns and updates

- The same inputs again: `status == "unchanged"`, nothing to write, `commit()` is a no-op (unless the stored copy of a picture's bytes is damaged: then the rerun is an `update` that repairs it). The handle must still be allowed to propose in the destination, or `plan` raises `ImportDenied`.
- An explicit `target` naming an earlier card source with changed inputs: `status == "update"`. Each entity whose payload changed gets a new revision `r<n>` and its head moves (payloads are compared as JSON, so `true`, `1` and `1.0` are different values); unchanged entities keep their head. An entity the new card no longer has keeps its head and is listed in `kept`, on every later update too; when a later card has it again it comes back under the same id. That holds when the card's kind changes too (a character imported again `as_scenario`): the old entity is kept as `main:character` and returns when the card is a character again. One entity id is never used under two kinds in a source, and never as `import`: ids are reserved from every revision file in the source, headed or not, and an entity a person wrote there (even an undeclared only revision, which `load()` resolves as its head) is never taken over.
- **Hand edits stand.** When a person has moved an imported entity's head past the revision the last import wrote, or chose another picture, a crop or a focus for any slot the card fills, a rerun or update leaves it as it is and reports a `hand_edited` collision naming it. `plan(..., replace_edits=True)` writes the card's version over it instead.
- An update writes only the card source. **Chats are never rewritten**: a pinned binding keeps its revision, and an unpinned one shows 022's `binding_drift` notice. A chat started from the card pins it by default, so the update reaches that chat only through 042's reviewed rebase ([REBASE](REBASE.md#pin-by-default)); only a chat that asked to `follow` the head, or one an earlier build stored unpinned, shows the drift.
- Every import revision keeps its original bytes, so earlier inputs stay recoverable.

### Chats

The importer allocates no actor and writes nothing under `campaigns/`. Starting a world-less chat from an imported character, scenario or lorebook, attaching a lorebook, or adding a card to a chat under way goes through [107](CHATS.md): a scenario or lorebook binding creates no actor, and a late-added character is a new actor that has witnessed nothing (`not_witnessed`). Each source is pinned to the revision it resolves to when it is bound, a late-added card included ([pin by default](REBASE.md#pin-by-default)).

**A scenario brings its own lorebook.** A chat that binds a `grimoire.scenario/1` record, by launch or by `attach_source`, also selects the lorebook that the scenario's **selected revision** names, right after it, every time a snapshot is taken (`grimoire.effective.chat_snapshot`); it is not a chat binding. So the lore follows the scenario: a head-following chat (`follow=True`, or one an earlier build stored unpinned; a chat pins its scenario by default) whose scenario is updated to drop or rename its lorebook loses or changes that lore with it, a chat pinned to a revision keeps the book that revision names (pinned, as named), and detaching the scenario takes its lore away. A lorebook the chat binds itself, under any revision, keeps its own binding. The player can opt out: `detach_source(chat, lorebook, "lorebook")` on an implied lorebook records it as bound and detached at once, and that tombstone keeps it out of this chat through scenario updates, as does detaching an explicit binding of it; attaching it again undoes the opt-out. A lorebook that no longer resolves never blocks play: the chat starts and packs report it `missing_source`. A scenario whose `lorebook` is not a pinned lorebook ref (a hand edit) selects none, and the chat snapshot reports `invalid_scenario_lorebook:<scenario>`. A character's embedded lorebook is not selected with it: attach it explicitly.

### Lore in play (031)

`grimoire.cards.LORE_READERS` are 031's readers for the imported records, and `grimoire.contextstore.actor_context` uses them by default (`DEFAULT_READERS`), as does its director counterpart `chat_lore`. Card lore is **`common`**: as in SillyTavern, any NPC in the scene may know it. A triggered entry reaches each NPC whose own perceived text (its window of the scene, or its own held memories) shows one of its keys, and the director's lore when the table text shows one; a constant entry reaches every pack. It is never director-only and never one NPC's secret. Ranking, the budget and every audience rule are [031's](CONTEXT.md).

- `grimoire.lorebook/1` (a card's embedded lorebook, a separate lorebook file, a standalone V3 lorebook): each entry's `keys`, `constant`, and, when `selective` is true, its `secondary_keys` with SillyTavern's `extensions.selectiveLogic` (0 AND ANY, 1 NOT ALL, 2 NOT ANY, 3 AND ALL; anything else is AND ANY) are read; `insertion_order` is 031's `order` (lowest first), rounded and clamped to ±1,000,000.
- `grimoire.scenario/1` yields no entries: its embedded lorebook is its own record, selected with the scenario (above). Its premise, openings and instructions are prompts for play (035), never lore. A bound scenario reports no `no_entry_reader`.
- **Ignored**, because 031 has no place for them: `priority` (031's budget sheds by its own rank), `position` (one lore lane), `case_sensitive` (031 always casefolds), `name` and `comment` (labels), the card's own entry `id` (cards may repeat it: an entry is `entry-<n>`, its place in the record), every other `extensions` setting (probability, inclusion groups, depth, sticky, cooldown, delay, `match_whole_words`: 031 always matches whole words), and the book's `scan_depth`, `token_budget` and `recursive_scanning` (031's budget governs, and lore never triggers lore).
- **Skipped**, one entry at a time, so one entry never blanks its source, and named in the pack's `problems` as `skipped_entry` (`detail`: `entry-<n>:<reason>`, never its text): a `use_regex` entry that is not constant (`use_regex`: 031 matches words, not patterns); content that is blank, unsafe (control or invisible characters) or over 8,000 characters after `\r\n` and `\r` become `\n` (`content`); a non-constant entry left with no usable key (`keys`); and a `selective` entry with any secondary key 031 cannot use, or more than 64 of them, or one the importer could not read (`secondary_keys`). The importer drops a secondary key that is not text, or a `secondary_keys` value that is not a list, and reports the loss; it then marks the stored entry `secondary_keys_lost`, so the narrower list it kept is never read as the author's. Secondary keys are all or nothing: dropping one would make an AND ALL or NOT ANY entry fire where its author's logic would not. A disabled entry is left out silently. A bad primary key (blank, over 100 characters, with a tab or line break, or with no word) is dropped and the entry kept, since that can only make it fire less; a repeated key counts once, and only the first 64 keys are kept. Entries past the first 4,096, skips included, are left out.

## A batch of cards

`cardstore.batch.plan_batch(importer, [BatchCard(data, origin), …])` stages up to 50 cards into **one** image batch, so a batch is one store transaction with one preview:

- Each card is planned as `plan` plans it (with `update_same_card` on by default), in order. A card never takes the new destination an earlier card of the batch was given (two different cards of one name become `card:<slug>` and `card:<slug>-2`); the same card document twice, as a PNG and as JSON say, is refused `duplicate_in_batch`, and two cards that would write one source `duplicate_destination`.
- `CardBatchImport.cards` are the cards' own `CardImport`s (report and files; a picture two cards share is listed with the first). `preview()` is every file of every card; `commit(approvals)` writes them all through 010's guarded writer, once. A card of a batch cannot be committed on its own.
- **All or nothing.** A card that cannot be staged refuses the batch (`BatchCardRefused`, with the card's place and its own reason) with nothing staged. At commit, a library that moved under any card (`StaleWrite`), or a missing grant or approval for any file (`WriteDenied`), writes no file of any card. A crash part way is the store's journal's to finish: before the commit point nothing is published, and after it recovery at the next open publishes every file, so the library is never left with some cards of a batch ([STORAGE](STORAGE.md); explored at every crash point in `tests/test_cardstore_batch.py`).
- `plan_card(importer, card, reserved)` plans one card alone, as it would be inside a batch whose other cards write the `reserved` sources, and `outcome(report)` says what a planned card does: its status, its source, the imported entity's head before and after when the import moves it (`ImportReport.replaces` lists the head each written revision moves on from), and whether another definition of the same kind has its name.

## The import record

`card-import/import` keeps the evidence of what was imported:
- `original`: the exact card bytes (base64), their sha256 and size; `lorebook_file` likewise for a separate lorebook, and `lorebook_files` (a list, in order, present only when there are more) for the further ones; `portrait`: its image ref, sha256, size and slot;
- `kept_entities` and `kept`: entities this card no longer has, with the revision an import last wrote;
- `avatars`: for each imported subject, the last picture an import placed in its `avatar` slot (an image ref, or `unresolved`), carried by every import so a later one can tell its own picture from a hand-chosen one;
- `art` (038): the same for every other slot, per subject (`slot` to an image ref or `unresolved`). A slot the card no longer fills keeps its placement and its entry, so it is still the card's own if the asset comes back;
- `format`, `origin` (the caller's label, such as a file name), `imported_at`, `imported_by`, `options` (`as_scenario`, and `embedded_lorebook: false` only when the card's lorebook was left out), `digest` (of all inputs and options);
- `container`, only for a PNG card: the PNG file's sha256 and size and the text chunk keyword the card was under;
- `entities` (role to `kind/id`: `main`, `lorebook`, `lorebook_file`, `lorebook_file-2`…), `records` (the pinned revisions that make up this import) and `losses`.

## Reported, not refused

- A known field of the wrong type is left out and reported as a loss (`wrong_type`); unknown fields are kept verbatim; a decimal a double cannot hold is kept as the nearest float and reported by the importer (`rounded_number`, with a `lorebook_file.` prefix for the separate lorebook and `lorebook_file-<n>.` for a further one); a too-deep kept value is `too_deep`; a blank greeting is `blank`; a field the imported kind has no place for (a nickname on a scenario) is `not_applicable`; a lorebook name over 256 characters is `too_long`. Dates may be integers or floats. Greeting ids, loss paths and preview field paths follow the card's own indexes, also past a dropped item (`alt-3` is `alternate_greetings[2]`; a lore entry or asset keeps its place in its array). A field reports at most 20 losses (`tags[0]`… count as one field, `tags[*]`, and a mapping's `{0}`… as `{*}`), then one `more_losses` at that field (`data.tags[*]`), and the report holds at most 200 losses in all, then one `more_losses` at `*`; an imprecise number inside a value that was dropped is not reported again. A known mapping such as `extensions` is checked child by child, so one too-deep value drops only itself. A loss names a field path, never its text: an unknown key that is not a short plain name (letters, digits, `_`, `-`, up to 64) appears as `{n}`, its place among its mapping's keys.
- Assets are recorded as written (an entry's unknown keys under its `extras`) and never fetched. Their pictures are placed as [above](#card-art-038); a picture that is missing is `not_fetched` (an `http(s)` URI) or `unavailable` (anything else), and an asset that fills no slot is `not_applicable`, `duplicate_slot` or `too_many`.
- **Collisions:** a definition elsewhere with the same name (`same_name`), another card source whose latest import had the same card bytes (`same_bytes`), a taken default destination (`destination_taken`, which includes a source that exists but is excluded or unreadable), an entity or a slot's picture a person changed (`hand_edited`). None of them merges anything.
- **Link suggestions:** phrases of one to six words, taken as written (so "Saint-Malo", "St. Ives" and "Order of the Silver Hand" keep their punctuation and small inner words), starting with a word that is not lowercase (or from a script without case), that name existing definitions through `DefinitionIndex.find_by_name`. Text is read in NFC, combining marks belong to their word, and a possessive ("Port Vell's") is also tried without its "'s", and a phrase followed by a full stop also with it ("U.S.A."); Unicode hyphens and dashes join words like `-`, and a phrase written with one is also looked up with `-` (so "Saint‐Malo" finds a library's "Saint-Malo"); scripts written without spaces between words are not segmented. A suggestion lists candidates and the fields that mention them; several candidates make it `ambiguous`. Nothing is linked and no relation is written. Fields are read description, personality, scenario and greetings first, lore entries last; after 20,000 distinct phrases the scan stops and says so (`truncated`, with the fields not read in full in `skipped`); it also stops after 500,000 phrase occurrences, so a long repetitive text stays quick.
- **Cast:** each scenario cast entry, as written (an entry's other keys are kept under `extras`; with both a `ref` and a `name`, the ref is checked): `resolves`, a 010 reason (`missing_source`, `missing_entity`…), `invalid_ref` (also a ref no portable path can hold), or `name_only` with candidates.

## Refused before anything is staged (`CardError.reason`)

- `unsupported_format`, with `detail`: `charx` (zip), `chara_card_v1`, `sillytavern_world_info`, `unknown`, `chara_card_v3_version` for a V3 `spec` whose `spec_version` is not `3.x`, `chara_card_v2_version` for a V2 `spec` whose `spec_version` is not `2.x`, and `png` for a picture given as the separate lorebook file.
- `invalid_png` and `no_embedded_card` for a PNG card ([above](#png-cards)).
- `invalid_json` (not strict UTF-8 JSON: a byte-order mark, lone surrogates, duplicate keys, non-finite numbers, integers over 4,300 digits, nesting deeper than 100), `invalid_card` (not an object; a missing or blank name or title; a scenario with neither premise nor opening; wrong top-level shapes), `too_large` (a card or lorebook file over 2 MiB, or a record the store would refuse as too large), `invalid_options`, `invalid_target`, `not_a_card_source` (also for a target that exists but is excluded or unreadable).
- 018's `InvalidImage` for a portrait that is not a supported picture; `portrait_unstorable` (`detail` `portrait`, or `assets[<n>]` for an asset's picture) when the library's shared blob store holds a differently spelled copy of the picture's file name, which no other target avoids, or when the destination's image record for it cannot be read. An asset whose bytes are not a supported picture is not refused: its slot is `unresolved`. A damaged stored copy of a picture's exact bytes is repaired by any import that carries that picture, a rerun with the same inputs included (the repair is one of the staged paths). A slot holding a placement this version cannot replace (`unsupported_format`) is `invalid_target`.

Error messages never echo card text.

## Not in this slice

The preview reads the whole library for its collisions, suggestions and cast statuses, like other adapters today: it does not yet filter them by what the handle may `inspect`.

CHARX containers, cards in a PNG's `zTXt` or `iTXt` chunk, Character Card V1, SillyTavern world info, fetching remote assets, reading pictures packaged in a CHARX file, outfits other than `default`, the lore settings 031 does not read ([below](#lore-in-play-031)), choosing a suggestion and any UI. 035 opens a chat from a card's first greeting or a scenario's first opening ([SCENES](SCENES.md)).
