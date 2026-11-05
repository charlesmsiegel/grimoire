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

### The character record

A 009 `individual` with `agent` and spoken `communicative` traits.
- `description` is 009's field, verbatim.
- `personality` becomes the 022 section `personality` when 022 would accept it. Otherwise it is kept verbatim under `card.unsectioned` and reported as a `not_section` loss.
- The card's `scenario` prompt and `mes_example` are kept verbatim under `card.texts` (`scenario`, `example-messages`), never as 022 sections: they are instructions and examples, not facts about the character, so 022's view never composes them.
- Everything else sits under the 037-owned `card` key, which 022 does not compose: `greetings` (`first`, `alt-N`, `group-N` with `group_only`), `instructions` (`system_prompt`, `post_history_instructions`), `nickname`, `tags`, `authorship` (creator, version, notes, source, dates), `lorebook` (the embedded lorebook's **pinned** ref), `assets` as written, `extensions` and unknown fields (`extras`) verbatim.
- `card.roles` names the role of each section and text (`personality`, `texts.scenario`, `texts.example-messages`…), so a prompt builder never mistakes a scenario instruction or an example for a character fact.

### Scenario and lorebook records

A scenario holds `title`, `premise`, `openings` (possible starts, never canon), `instructions`, other `texts` (a V3 card read as a scenario keeps its description as `texts.setting`), `cast` exactly as written, its lorebook's pinned ref, and `roles`. A lorebook holds its description and its entries' keys and content as authored lore, with known V3 settings kept and **not interpreted**. A lorebook document's own top-level unknown fields sit under `extras.card` and its book's under `extras.data`, so neither overwrites the other. Neither is an entity, so 022 returns them raw (`not_entity_definition`).

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

- The same inputs again: `status == "unchanged"`, nothing to write, `commit()` is a no-op. The handle must still be allowed to propose in the destination, or `plan` raises `ImportDenied`.
- An explicit `target` naming an earlier card source with changed inputs: `status == "update"`. Each entity whose payload changed gets a new revision `r<n>` and its head moves; unchanged entities keep their head. An entity the new card no longer has keeps its head and is listed in `kept`, on every later update too; when a later card has it again it comes back under the same id. That holds when the card's kind changes too (a character imported again `as_scenario`): the old entity is kept as `main:character` and returns when the card is a character again. One entity id is never used under two kinds in a source, and never as `import`: ids are reserved from every revision file in the source, headed or not, and an entity a person wrote there (even an undeclared only revision, which `load()` resolves as its head) is never taken over.
- **Hand edits stand.** When a person has moved an imported entity's head past the revision the last import wrote, or chose another picture for the `avatar` slot, a rerun or update leaves it as it is and reports a `hand_edited` collision naming it. `plan(..., replace_edits=True)` writes the card's version over it instead.
- An update writes only the card source. **Chats are never rewritten**: a pinned binding keeps its revision, and an unpinned one shows 022's `binding_drift` notice.
- Every import revision keeps its original bytes, so earlier inputs stay recoverable.

### Chats

The importer allocates no actor and writes nothing under `campaigns/`. Starting a world-less chat from an imported character, scenario or lorebook, attaching a lorebook, or adding a card to a chat under way goes through [107](CHATS.md): a scenario or lorebook binding creates no actor, and a late-added character is a new actor that has witnessed nothing (`not_witnessed`).

## The import record

`card-import/import` keeps the evidence of what was imported:
- `original`: the exact card bytes (base64), their sha256 and size; `lorebook_file` likewise for a separate lorebook; `portrait`: its image ref, sha256, size and slot;
- `kept_entities` and `kept`: entities this card no longer has, with the revision an import last wrote;
- `avatars`: for each imported subject, the last picture an import placed in its `avatar` slot, carried by every import so a later one can tell its own picture from a hand-chosen one;
- `format`, `origin` (the caller's label, such as a file name), `imported_at`, `imported_by`, `options`, `digest` (of all inputs and options);
- `entities` (role to `kind/id`: `main`, `lorebook`, `lorebook_file`), `records` (the pinned revisions that make up this import) and `losses`.

## Reported, not refused

- A known field of the wrong type is left out and reported as a loss (`wrong_type`); unknown fields are kept verbatim; a decimal a double cannot hold is kept as the nearest float and reported by the importer (`rounded_number`, with a `lorebook_file.` prefix for the separate lorebook); a too-deep kept value is `too_deep`; a blank greeting is `blank`; a field the imported kind has no place for (a nickname on a scenario) is `not_applicable`; a lorebook name over 256 characters is `too_long`. Dates may be integers or floats. Greeting ids, loss paths and preview field paths follow the card's own indexes, also past a dropped item (`alt-3` is `alternate_greetings[2]`; a lore entry or asset keeps its place in its array). A field reports at most 20 losses (`tags[0]`… count as one field, `tags[*]`, and a mapping's `{0}`… as `{*}`), then one `more_losses` at that field (`data.tags[*]`), and the report holds at most 200 losses in all, then one `more_losses` at `*`; an imprecise number inside a value that was dropped is not reported again. A known mapping such as `extensions` is checked child by child, so one too-deep value drops only itself. A loss names a field path, never its text: an unknown key that is not a short plain name (letters, digits, `_`, `-`, up to 64) appears as `{n}`, its place among its mapping's keys.
- Assets are recorded as written (an entry's unknown keys under its `extras`) and never fetched: an `http(s)` URI is `not_fetched`; an embedded, default or data URI is `unavailable`.
- **Collisions:** a definition elsewhere with the same name (`same_name`), another card source whose latest import had the same card bytes (`same_bytes`), a taken default destination (`destination_taken`, which includes a source that exists but is excluded or unreadable), an entity or portrait a person changed (`hand_edited`). None of them merges anything.
- **Link suggestions:** phrases of one to six words, taken as written (so "Saint-Malo", "St. Ives" and "Order of the Silver Hand" keep their punctuation and small inner words), starting with a word that is not lowercase (or from a script without case), that name existing definitions through `DefinitionIndex.find_by_name`. Text is read in NFC, combining marks belong to their word, and a possessive ("Port Vell's") is also tried without its "'s", and a phrase followed by a full stop also with it ("U.S.A."); Unicode hyphens and dashes join words like `-`, and a phrase written with one is also looked up with `-` (so "Saint‐Malo" finds a library's "Saint-Malo"); scripts written without spaces between words are not segmented. A suggestion lists candidates and the fields that mention them; several candidates make it `ambiguous`. Nothing is linked and no relation is written. Fields are read description, personality, scenario and greetings first, lore entries last; after 20,000 distinct phrases the scan stops and says so (`truncated`, with the fields not read in full in `skipped`); it also stops after 500,000 phrase occurrences, so a long repetitive text stays quick.
- **Cast:** each scenario cast entry, as written (an entry's other keys are kept under `extras`; with both a `ref` and a `name`, the ref is checked): `resolves`, a 010 reason (`missing_source`, `missing_entity`…), `invalid_ref` (also a ref no portable path can hold), or `name_only` with candidates.

## Refused before anything is staged (`CardError.reason`)

- `unsupported_format`, with `detail`: `png`, `charx` (zip), `chara_card_v2`, `chara_card_v1`, `sillytavern_world_info`, `unknown`, and `chara_card_v3_version` for a V3 `spec` whose `spec_version` is not `3.x`.
- `invalid_json` (not strict UTF-8 JSON: a byte-order mark, lone surrogates, duplicate keys, non-finite numbers, integers over 4,300 digits, nesting deeper than 100), `invalid_card` (not an object; a missing or blank name or title; a scenario with neither premise nor opening; wrong top-level shapes), `too_large` (a card or lorebook file over 2 MiB, or a record the store would refuse as too large), `invalid_options`, `invalid_target`, `not_a_card_source` (also for a target that exists but is excluded or unreadable).
- 018's `InvalidImage` for a portrait that is not a supported picture; `portrait_unstorable` (`detail` `portrait`) when the library's shared blob store holds a differently spelled copy of the portrait's file name, which no other target avoids, or when the destination's image record for the portrait cannot be read. A damaged stored copy of the portrait's exact bytes is repaired by the import (the repair is one of the staged paths).

Error messages never echo card text.

## Not in this slice

The preview reads the whole library for its collisions, suggestions and cast statuses, like other adapters today: it does not yet filter them by what the handle may `inspect`.

PNG `tEXt`/CHARX containers, Character Card V1/V2, SillyTavern world info, fetching remote assets, decoding embedded assets, interpreting lore triggers, choosing a suggestion, prompt building from greetings (035) and any UI.

**No context reader yet (031).** This slice registers no `readers` entry for its lorebook or scenario records, so 031 reports them `no_entry_reader` and packs none of their lore: giving card lore an audience is 027's, and 031 keeps an unclassified entry director-only. The records keep entries as authored, after dropping only the ones whose shape is wrong, one at a time (`wrong_type`). Some kept entries would fail 031's `LoreEntry` checks: blank, unsafe (control or invisible characters) or over 8,000-character content, more than 64 keys, or a repeated, blank or over 100-character key, or one with a tab or line break. A book of more than 4,096 entries blanks its source too, so a reader must also truncate. A reader added later must skip or sanitize such an entry by itself, never raise, because 031 blanks a whole source on one raised entry. It must also key entries by their place in the record, not the card's own `id`, which cards may repeat. That reader, with 027's audiences, is later 037 work.
