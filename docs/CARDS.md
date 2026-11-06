# Card import

**Status:** first slice of [Character Card Ingestion](roadmap/sprints/character-card-ingestion.md) (Sprint 037, Wave 4). It is implemented as:
- `grimoire.cards`: pure parsing of an already-decoded card document, mapping to record payloads, role classification and name-mention link suggestions;
- `grimoire.cardstore`: a store-backed importer (`CardImporter`) that stages one import in a single guarded batch over [the native store](STORAGE.md) and [018's image batch](IMAGES.md).

There is **no UI, no PNG or CHARX reader, no V2 card support and no network**. Nothing is fetched and no model is called. A green test suite proves this contract with synthetic cards written for the tests; it is not a playable app. 026's world importer is not needed and not used.

## What a card becomes

A card is interchange data, not native storage. An import creates (or updates) one **card source**, `card:<slug>`, and writes its texts as source records that keep their authored roles. Nothing a card says becomes an event, evidence, a belief or canon, and nothing is linked by name.

| Input | Records in the card source (`grimoire.record/1`, top-level `schema`) |
|---|---|
| Character Card V3 (`spec: chara_card_v3`, `spec_version: 3.x`) | `character/<slug>` (`grimoire.entity/0`), plus `lorebook/<slug>-lore` (`grimoire.lorebook/1`) when it embeds a `character_book` |
| The same card with `as_scenario=True` | `scenario/<slug>` (`grimoire.scenario/1`): never a fake `Agent` character |
| Grimoire scenario document (`format: grimoire.scenario-card/1`) | `scenario/<slug>`, plus its lorebook |
| V3 standalone lorebook (`spec: lorebook_v3`) | `lorebook/<slug>` |
| A separate `lorebook_v3` file passed with a character or scenario card | an extra `lorebook/<slug>` record |
| Every import | `card-import/import` (`grimoire.card-import/1`), see [The import record](#the-import-record) |

The manifest declares every head. A portrait (`portrait=` bytes) becomes one 018 image object in the card source and a `set` placement in slot **`avatar`** of the imported character or scenario. Its provenance names the card's sha256, format and origin label.

### Card art (038)

A V3 card's `data.assets` become 018 placements on the imported character or scenario too ([038](roadmap/sprints/expression-and-art-lineage.md); `grimoire.cards.card_art` decides the slots from the card alone, `grimoire.cardstore.art` what an import writes there):

| asset | slot |
|---|---|
| the caller's `portrait` | `avatar` |
| the main `icon` (named `main`, else the first icon) | `avatar`; with a `portrait`, a `ccdefault:` main icon *is* that portrait and any other main icon goes to `icon-main` |
| any other `icon` | `icon-<name>` |
| `emotion` | `expr.default.<name>`: a sprite of the card's one outfit, `default` |
| the main `background` (named `main`, else the first) | `background` |
| any other `background` | `background-<name>` |
| `user_icon` | none: it pictures the user (`not_applicable` loss) |
| any other type | `asset-<name>` |

`<name>` is the asset's name as an identity segment, or its place in the array (`emotion-3`) when the name gives none. The first asset to claim a slot wins; a later one is a `duplicate_slot` loss, and assets past the first 64 slots are `too_many`.

- **Bytes come only from the card.** A `data:` URI with base64 content is decoded locally; `ccdefault:` is the given `portrait`. Nothing is fetched and no file is read.
- **A missing picture keeps its slot.** An `http(s)` address (`not_fetched`), a CHARX `embeded://` path, `ccdefault:` with no portrait, a `data:` URI that cannot be decoded or is not a PNG, JPEG, GIF or WebP picture, and any other address are written as an explicit `unresolved` placement whose `provenance.note` says why, so the slot never shows another picture found by name. Each is also a loss (`not_fetched` for a remote picture, `unavailable` otherwise).
- **One object per picture.** Identical bytes are one image object per card source (`portrait-<sha256[:16]>` when they are the portrait, `art-<sha256[:16]>` otherwise) and one blob per library, whichever assets carry them. A placement's provenance names its asset (`x-card-asset`: index, type, name), never the card's text.
- **Individuals inherit it.** Every campaign instance derived from the imported character sees the card's art through 018's inheritance; its own `set`, `adjust` or `hide` changes only that individual, and an updated card moves only the art the individual inherits.
- `ImportReport.art` lists every slot the import fills: `placed`, `unchanged` or `hand_edited`, with the card's picture or why it is missing, and the asset's index.

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
planned = importer.plan(card_bytes, origin="mara.json", portrait=png_bytes, lorebook=None,
                        target=None, as_scenario=False, replace_edits=False)
planned.report      # status, destination, roles, losses, collisions, suggestions, cast, paths, grant scopes
planned.preview()   # {logical path: exact bytes}: what a human approves
planned.commit(approvals)  # {path: approval id}
```

- `plan` reads the library and writes nothing. It asks the handle to **`propose`** every staged path with its exact bytes; any refusal raises `ImportDenied` (`denied` lists `(path, reason)`), and nothing is staged.
- `commit` goes through 010's guarded writer. An untrusted handle needs a covering `apply` grant **and** an approval bound to the payload digest of every path. Approvals for other bytes, or for only some paths, write nothing. A commit happens once.
- The grants an import needs are reported as `grant_scopes`: `sources/card/<slug>/_source`, and `assets/blobs` when a new picture blob is written.
- `plan` runs the commit's own checks read-only on every write of the batch (records, the portrait's image, blob and placement), so a plan a human approves is not refused at commit for the state of its source (`invalid_target` at plan instead: a stray `Images/` folder or `placements` file, a manifest holding a number a double cannot hold, one entity id under two kinds).
- `StaleWrite` if the library changed between plan and commit (for example, another import took the destination). Nothing is written.

### The destination

Chosen by trusted code, never by card text:
1. an explicit `target` source id, which must be in the `card` namespace (`invalid_target` otherwise). A source that exists but was not made by a card import is refused `not_a_card_source`, as is any source outside the `card` namespace, whatever records it holds; an id the library cannot hold is `invalid_target`;
2. otherwise the `card:` source an identical earlier import went to (only `card:` sources are read as earlier imports) (same bytes, format, options, lorebook file and portrait);
3. otherwise `card:<slug of the name or title>`, with `-2`, `-3`… when that exists (the report then carries a `destination_taken` collision). A slug Windows reserves (`con`, `nul`, `com1`…) gets `-card` added, here and for entity ids.

### Reruns and updates

- The same inputs again: `status == "unchanged"`, nothing to write, `commit()` is a no-op (unless the stored copy of a picture's bytes is damaged: then the rerun is an `update` that repairs it). The handle must still be allowed to propose in the destination, or `plan` raises `ImportDenied`.
- An explicit `target` naming an earlier card source with changed inputs: `status == "update"`. Each entity whose payload changed gets a new revision `r<n>` and its head moves (payloads are compared as JSON, so `true`, `1` and `1.0` are different values); unchanged entities keep their head. An entity the new card no longer has keeps its head and is listed in `kept`, on every later update too; when a later card has it again it comes back under the same id. That holds when the card's kind changes too (a character imported again `as_scenario`): the old entity is kept as `main:character` and returns when the card is a character again. One entity id is never used under two kinds in a source, and never as `import`: ids are reserved from every revision file in the source, headed or not, and an entity a person wrote there (even an undeclared only revision, which `load()` resolves as its head) is never taken over.
- **Hand edits stand.** When a person has moved an imported entity's head past the revision the last import wrote, or chose another picture, a crop or a focus for any slot the card fills, a rerun or update leaves it as it is and reports a `hand_edited` collision naming it. `plan(..., replace_edits=True)` writes the card's version over it instead.
- An update writes only the card source. **Chats are never rewritten**: a pinned binding keeps its revision, and an unpinned one shows 022's `binding_drift` notice.
- Every import revision keeps its original bytes, so earlier inputs stay recoverable.

### Chats

The importer allocates no actor and writes nothing under `campaigns/`. Starting a world-less chat from an imported character, scenario or lorebook, attaching a lorebook, or adding a card to a chat under way goes through [107](CHATS.md): a scenario or lorebook binding creates no actor, and a late-added character is a new actor that has witnessed nothing (`not_witnessed`).

**A scenario brings its own lorebook.** A chat that binds a `grimoire.scenario/1` record, by launch or by `attach_source`, also selects the lorebook that the scenario's **selected revision** names, right after it, every time a snapshot is taken (`grimoire.effective.chat_snapshot`); it is not a chat binding. So the lore follows the scenario: a head-following chat whose scenario is updated to drop or rename its lorebook loses or changes that lore with it, a chat pinned to a revision keeps the book that revision names (pinned, as named), and detaching the scenario takes its lore away. A lorebook the chat binds itself, under any revision, keeps its own binding. The player can opt out: `detach_source(chat, lorebook, "lorebook")` on an implied lorebook records it as bound and detached at once, and that tombstone keeps it out of this chat through scenario updates, as does detaching an explicit binding of it; attaching it again undoes the opt-out. A lorebook that no longer resolves never blocks play: the chat starts and packs report it `missing_source`. A scenario whose `lorebook` is not a pinned lorebook ref (a hand edit) selects none, and the chat snapshot reports `invalid_scenario_lorebook:<scenario>`. A character's embedded lorebook is not selected with it: attach it explicitly.

### Lore in play (031)

`grimoire.cards.LORE_READERS` are 031's readers for the imported records, and `grimoire.contextstore.actor_context` uses them by default (`DEFAULT_READERS`), as does its director counterpart `chat_lore`. Card lore is **`common`**: as in SillyTavern, any NPC in the scene may know it. A triggered entry reaches each NPC whose own perceived text (its window of the scene, or its own held memories) shows one of its keys, and the director's lore when the table text shows one; a constant entry reaches every pack. It is never director-only and never one NPC's secret. Ranking, the budget and every audience rule are [031's](CONTEXT.md).

- `grimoire.lorebook/1` (a card's embedded lorebook, a separate lorebook file, a standalone V3 lorebook): each entry's `keys`, `constant`, and, when `selective` is true, its `secondary_keys` with SillyTavern's `extensions.selectiveLogic` (0 AND ANY, 1 NOT ALL, 2 NOT ANY, 3 AND ALL; anything else is AND ANY) are read; `insertion_order` is 031's `order` (lowest first), rounded and clamped to ±1,000,000.
- `grimoire.scenario/1` yields no entries: its embedded lorebook is its own record, selected with the scenario (above). Its premise, openings and instructions are prompts for play (035), never lore. A bound scenario reports no `no_entry_reader`.
- **Ignored**, because 031 has no place for them: `priority` (031's budget sheds by its own rank), `position` (one lore lane), `case_sensitive` (031 always casefolds), `name` and `comment` (labels), the card's own entry `id` (cards may repeat it: an entry is `entry-<n>`, its place in the record), every other `extensions` setting (probability, inclusion groups, depth, sticky, cooldown, delay, `match_whole_words`: 031 always matches whole words), and the book's `scan_depth`, `token_budget` and `recursive_scanning` (031's budget governs, and lore never triggers lore).
- **Skipped**, one entry at a time, so one entry never blanks its source, and named in the pack's `problems` as `skipped_entry` (`detail`: `entry-<n>:<reason>`, never its text): a `use_regex` entry that is not constant (`use_regex`: 031 matches words, not patterns); content that is blank, unsafe (control or invisible characters) or over 8,000 characters after `\r\n` and `\r` become `\n` (`content`); a non-constant entry left with no usable key (`keys`); and a `selective` entry with any secondary key 031 cannot use, or more than 64 of them, or one the importer could not read (`secondary_keys`). The importer drops a secondary key that is not text, or a `secondary_keys` value that is not a list, and reports the loss; it then marks the stored entry `secondary_keys_lost`, so the narrower list it kept is never read as the author's. Secondary keys are all or nothing: dropping one would make an AND ALL or NOT ANY entry fire where its author's logic would not. A disabled entry is left out silently. A bad primary key (blank, over 100 characters, with a tab or line break, or with no word) is dropped and the entry kept, since that can only make it fire less; a repeated key counts once, and only the first 64 keys are kept. Entries past the first 4,096, skips included, are left out.

## The import record

`card-import/import` keeps the evidence of what was imported:
- `original`: the exact card bytes (base64), their sha256 and size; `lorebook_file` likewise for a separate lorebook; `portrait`: its image ref, sha256, size and slot;
- `kept_entities` and `kept`: entities this card no longer has, with the revision an import last wrote;
- `avatars`: for each imported subject, the last picture an import placed in its `avatar` slot (an image ref, or `unresolved`), carried by every import so a later one can tell its own picture from a hand-chosen one;
- `art` (038): the same for every other slot, per subject (`slot` to an image ref or `unresolved`). A slot the card no longer fills keeps its placement and its entry, so it is still the card's own if the asset comes back;
- `format`, `origin` (the caller's label, such as a file name), `imported_at`, `imported_by`, `options`, `digest` (of all inputs and options);
- `entities` (role to `kind/id`: `main`, `lorebook`, `lorebook_file`), `records` (the pinned revisions that make up this import) and `losses`.

## Reported, not refused

- A known field of the wrong type is left out and reported as a loss (`wrong_type`); unknown fields are kept verbatim; a decimal a double cannot hold is kept as the nearest float and reported by the importer (`rounded_number`, with a `lorebook_file.` prefix for the separate lorebook); a too-deep kept value is `too_deep`; a blank greeting is `blank`; a field the imported kind has no place for (a nickname on a scenario) is `not_applicable`; a lorebook name over 256 characters is `too_long`. Dates may be integers or floats. Greeting ids, loss paths and preview field paths follow the card's own indexes, also past a dropped item (`alt-3` is `alternate_greetings[2]`; a lore entry or asset keeps its place in its array). A field reports at most 20 losses (`tags[0]`… count as one field, `tags[*]`, and a mapping's `{0}`… as `{*}`), then one `more_losses` at that field (`data.tags[*]`), and the report holds at most 200 losses in all, then one `more_losses` at `*`; an imprecise number inside a value that was dropped is not reported again. A known mapping such as `extensions` is checked child by child, so one too-deep value drops only itself. A loss names a field path, never its text: an unknown key that is not a short plain name (letters, digits, `_`, `-`, up to 64) appears as `{n}`, its place among its mapping's keys.
- Assets are recorded as written (an entry's unknown keys under its `extras`) and never fetched. Their pictures are placed as [above](#card-art-038); a picture that is missing is `not_fetched` (an `http(s)` URI) or `unavailable` (anything else), and an asset that fills no slot is `not_applicable`, `duplicate_slot` or `too_many`.
- **Collisions:** a definition elsewhere with the same name (`same_name`), another card source whose latest import had the same card bytes (`same_bytes`), a taken default destination (`destination_taken`, which includes a source that exists but is excluded or unreadable), an entity or a slot's picture a person changed (`hand_edited`). None of them merges anything.
- **Link suggestions:** phrases of one to six words, taken as written (so "Saint-Malo", "St. Ives" and "Order of the Silver Hand" keep their punctuation and small inner words), starting with a word that is not lowercase (or from a script without case), that name existing definitions through `DefinitionIndex.find_by_name`. Text is read in NFC, combining marks belong to their word, and a possessive ("Port Vell's") is also tried without its "'s", and a phrase followed by a full stop also with it ("U.S.A."); Unicode hyphens and dashes join words like `-`, and a phrase written with one is also looked up with `-` (so "Saint‐Malo" finds a library's "Saint-Malo"); scripts written without spaces between words are not segmented. A suggestion lists candidates and the fields that mention them; several candidates make it `ambiguous`. Nothing is linked and no relation is written. Fields are read description, personality, scenario and greetings first, lore entries last; after 20,000 distinct phrases the scan stops and says so (`truncated`, with the fields not read in full in `skipped`); it also stops after 500,000 phrase occurrences, so a long repetitive text stays quick.
- **Cast:** each scenario cast entry, as written (an entry's other keys are kept under `extras`; with both a `ref` and a `name`, the ref is checked): `resolves`, a 010 reason (`missing_source`, `missing_entity`…), `invalid_ref` (also a ref no portable path can hold), or `name_only` with candidates.

## Refused before anything is staged (`CardError.reason`)

- `unsupported_format`, with `detail`: `png`, `charx` (zip), `chara_card_v2`, `chara_card_v1`, `sillytavern_world_info`, `unknown`, and `chara_card_v3_version` for a V3 `spec` whose `spec_version` is not `3.x`.
- `invalid_json` (not strict UTF-8 JSON: a byte-order mark, lone surrogates, duplicate keys, non-finite numbers, integers over 4,300 digits, nesting deeper than 100), `invalid_card` (not an object; a missing or blank name or title; a scenario with neither premise nor opening; wrong top-level shapes), `too_large` (a card or lorebook file over 2 MiB, or a record the store would refuse as too large), `invalid_options`, `invalid_target`, `not_a_card_source` (also for a target that exists but is excluded or unreadable).
- 018's `InvalidImage` for a portrait that is not a supported picture; `portrait_unstorable` (`detail` `portrait`, or `assets[<n>]` for an asset's picture) when the library's shared blob store holds a differently spelled copy of the picture's file name, which no other target avoids, or when the destination's image record for it cannot be read. An asset whose bytes are not a supported picture is not refused: its slot is `unresolved`. A damaged stored copy of a picture's exact bytes is repaired by any import that carries that picture, a rerun with the same inputs included (the repair is one of the staged paths). A slot holding a placement this version cannot replace (`unsupported_format`) is `invalid_target`.

Error messages never echo card text.

## Not in this slice

The preview reads the whole library for its collisions, suggestions and cast statuses, like other adapters today: it does not yet filter them by what the handle may `inspect`.

PNG `tEXt`/CHARX containers, Character Card V1/V2, SillyTavern world info, fetching remote assets, reading pictures packaged in a PNG or CHARX file, outfits other than `default`, the lore settings 031 does not read ([below](#lore-in-play-031)), choosing a suggestion, prompt building from greetings (035) and any UI.
