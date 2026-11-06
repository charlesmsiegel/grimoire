# Campaign archaeology: importing prior transcripts

**Status: implemented headless first slice of [Sprint 036](roadmap/sprints/campaign-archaeology.md).** Two importers carry earlier play into V2 as [transcripts](TRANSCRIPTS.md) without inventing anything: a SillyTavern chat export becomes a world-less [107 chat](CHATS.md) with its transcript, and each V0.2.1 scene that [026](IMPORT_V1.md) imports keeps its posts. Neither calls a model, writes events, evidence, beliefs or relations, or decides who heard a line. The rule throughout: **preserve, don't reinterpret.**

## SillyTavern chat exports

`grimoire.archaeology.read_st_chat` (pure) reads the decoded lines of an export. `grimoire.archaeologystore.StImporter` (trusted application code) decodes the bytes, plans and writes.

```python
importer = StImporter(store, handle, clock=clock, owner="human:owner")
scan = importer.scan(data)                     # speakers, counts, losses; writes nothing
plan = importer.plan(data, speakers={mara_id: MARA, other_mara_id: OTHER_MARA, sam_id: SAM},
                     lorebooks=[NOTES], name="group.jsonl")
plan.preview()                                 # the exact bytes, by path
plan.commit(approvals)                         # one transaction through 107's writer
```

### What is read

- **Format:** SillyTavern's JSONL chat export, one JSON object per line, single-character and group chats alike. The first line is the chat header when it holds `user_name`, `character_name`, `create_date` or `chat_metadata` and none of a message's fields (`mes`, `name`, `is_user`, `is_system`, `swipes`, `swipe_id`, `send_date`); otherwise it is read, and checked, as a message. LF or CRLF line ends and a leading byte-order mark are accepted (the mark is reported). Every line must be one strict JSON object: duplicate keys, `NaN` or invalid UTF-8 refuse the file with its line number (`invalid_json`). A number that a double cannot hold exactly is kept as the nearest double and reported (`imprecise_number`). Exports are limited to 64 MiB and 200,000 messages; a single line larger than the store's 8 MiB document limit is refused (`invalid_json`), and so is a header and speaker table over 2 MiB (`header_too_large`).
- **Posts:** every message becomes one post, in file order, with ids `p000001`, `p000002`, … and its line number as provenance. A message without `mes` text, or with a non-boolean `is_user` or `is_system`, refuses the whole file (`invalid_message`): an import that dropped a line could not claim every message is recoverable.
- **Swipes and edits:** `swipes` become the post's variants and `swipe_id` the active one, each with its `swipe_info` verbatim and its `send_date` as the variant's time. When the shown text (`mes`) differs from the active swipe, it is kept as one more variant and made active (`edited_text_differs`); neither text is lost, and the source's `swipe_id` (the swipe the edit was made on) is kept in the post's `extras`. An invalid `swipe_id` is reported and the shown text decides (`invalid_swipe_id`). Malformed `swipes` keep the shown text and the raw field verbatim (`invalid_swipes`). Any `swipes`, `swipe_id` or `swipe_info` the variants cannot reproduce exactly (`null`, a shorter `swipe_info`, a `null` entry) is kept verbatim in the post's `extras`.
- **Roles:** `is_user` is `user`; `extra.type: "narrator"` is `narrator`; `is_system` with another `extra.type` is a `system` notice; anything else is `character`. `is_system` on a message is SillyTavern's hidden flag, kept as `hidden`.
- **Speakers:** a speaker is its role, the source's own avatar file (`original_avatar` for a group member, else `force_avatar`) and its display name, all verbatim. Its id (`s-` and 12 hex digits of a digest of the three) is the same on every run. Two speakers with one display name but different avatars stay two speakers and are both reported (`same_label_speakers`). A name the label cannot hold (not text, or over 1000 characters) leaves the label empty, but the id still uses the source's own value, so two such writers stay two speakers.
- **Kept verbatim, not interpreted:** the header (including `chat_metadata`: the author's note, the chat lorebook's name, the integrity id), reported key by key as `kept_not_interpreted`; and every message field the reader does not read (`extra`, `send_date`, `gen_started`, avatars, `title`, …) in the post's `extras`.

### Mapping speakers, never by name

The importer binds only what the caller maps. `speakers` maps a speaker id from the scan to a character definition (037's imported cards, or any other): a `user` speaker becomes the chat's persona, a `character` speaker one of its characters. Narrator and system speakers stay labels in this slice. A speaker the caller does not map stays a label on its posts (`unmapped_speaker`); nothing is ever matched to a card by a shared display name. Two speakers may be mapped to one card on purpose; they then share one actor.

The chat launches from the first mapped character to speak, attaches the other characters and the given `lorebooks`, and binds the persona. With no mapped character it launches from the first lorebook, or from the premise `Imported SillyTavern chat`. Every actor is new to this campaign and `not_witnessed`. Refusals (`ImportRefused.reason`): `unknown_speaker`, `role_kind` (a narrator mapped, or a non-character definition), `second_persona` (two user speakers mapped to different personas), `persona_is_character`, `conflicting_revisions` (one card at two revisions) and every 107 binding refusal (`missing_entity`, `missing_revision`, …).

### Writing, and repeats

The plan is one 107 `ChatChange`: the play scope, the chat with its bindings, the new actors and every transcript page, previewed and committed in one transaction with 107's re-checks. The campaign id and the chat id are both `st-`, 16 hex digits of the export's SHA-256 and this library's [device tag](DEVICE_SYNC.md#per-device-ids) (none in a library never given a device identity), so:

- importing the same export again on this device finds the earlier import: `status == "already_imported"`, nothing to write, the same chat and actors. Page 1 records how many pages and posts the import wrote, and the earlier import counts only when exactly those are there (a later writer may append pages). An import made before the library was given a device identity is found under its untagged id;
- the same export with a different speaker mapping, lorebooks or label is refused (`imported_differently`), as is a campaign folder of that id that this importer did not write completely, or a ledger that still names the campaign after its folder was deleted (`campaign_exists`);
- two paired devices that each import the export offline make two chats, one per device, and sync carries both; neither is a version of the other;
- two plans of one export committed one after the other: the second is `StaleWrite`.

The export itself is only read. Its digest, size and file name are the first page's `origin`, with the speaker table and the mapping used.

## V0.2.1 scene transcripts (through 026)

026 already imports each attributed V0.2.1 scene as a chat. It now also writes that scene's transcript pages in the same reviewed apply:

- the scene file is read again, its bytes checked against the reviewed walk, and its posts parsed exactly as 011 parsed them; every post's index, speaker, role, synthetic flag, id and content hash must match the reviewed rows, or the chat imports without a transcript (`transcript_unverified`);
- each post keeps V1's text as V1 shows it, a speaker that is a **label only** (`user` for a player's post, `system` for V1's synthetic speakers, `assistant` otherwise; never matched to an actor by name), its V1 post id and content hash as provenance, and an unknown audience. The page's `origin` names the format `grimoire-v0.2.1-scene`, the scene's legacy id and the file's digest;
- pages are ordinary 026 targets, so receipts, `present` outcomes on a rerun, `v1_changed` (a scene that gained posts keeps the pages it was imported with, and gets none added) and conflict reporting all apply. A chat imported before this slice gets its pages on the next run, only while its scene is still as imported (a `v1_changed` chat gets none, so a page never records a version of the scene the chat does not);
- a chat's pages are decided together, all or none: if any page is neither absent nor exactly as this run would write it (another writer's page, a page deleted since, a different split), no page is written and the scene is reported once (`transcript_exists`).
- a post no page can record exactly (a speaker label past 1000 characters, say) leaves the chat without pages (`transcript_unrepresentable`), and one past the store's document limit does the same (`too_large`); the chat itself still imports.

## Not in this slice (planned)

- **Response variants of V0.2.1** (`scene_alts`, reroll runs, `responses.json`) and segmenting a SillyTavern chat into scenes.
- **Reviewed V0.2.1 speaker mapping:** binding V1 posts to the scene's actors after review instead of keeping labels.
- **Binding SillyTavern lorebooks and author's notes** named in `chat_metadata` to library sources; mapping narrator speakers to a director voice.
- **Introducing a group member at its first post** instead of binding the whole cast when the chat starts.
- **Branch files** (SillyTavern's checkpoints and branches as separate files) linked as one history; other export formats (the plain-text export, other front ends).
- **Proposed historical events, relations and memories** from a transcript, with evidence and review before anything becomes canon (milestone 2), and multi-file, multi-era reconciliation with rollback (milestone 3).
