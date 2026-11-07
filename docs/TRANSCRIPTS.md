# Chat transcripts

**Status: implemented headless store family and model (Sprint 036's first slice).** A chat's transcript is the text that was posted in it: every post in order, who wrote it as the source says, and every variant it held. It is stored beside the chat as numbered, create-only pages. [ARCHAEOLOGY](ARCHAEOLOGY.md) describes the two importers that write pages today (SillyTavern chat exports and V0.2.1 scenes through 026). What is played in V2 afterwards is 035's turns, a separate family (below).

A transcript is **evidence that something was written, never of who heard it.** Nothing in this family creates an event, a belief, evidence or a relation, and nothing reads a page as a grant of knowledge to anyone.

## Storage

| | |
|---|---|
| Address | `TranscriptRef`: `campaign:<campaign_id>#transcript/<chat_id>/<seq:010d>` (`seq` from 1) |
| File | `campaigns/<campaign_id>/transcripts/<chat_id>/<seq:010d>.json` |
| Envelope | `grimoire.scope-record/1` (the address and schema id are the envelope's), schema `grimoire.transcript/0` |
| Writes | create-only: a page is never replaced (`put` with an `expected` etag raises `ImmutableRevision`; a second create of one page is `StaleWrite`) |
| Reads | `store.chat_transcript(chat)` (one chat's pages, in page order, with their problems) and `store.campaign_transcripts(campaign_id)` (every page of the campaign, for id reservation); `read(TranscriptRef)` reads one page |
| `load()` | skips `transcripts/`, so opening a library never pays for play history |
| Ids | every instance id a page names is reserved by `campaign_taken_ids`; an unreadable page fails allocation closed (`TakenIdsUnlisted`) |
| Sync | synced as an immutable, campaign-local kind (`SYNCED_KINDS`, `CAMPAIGN_LOCAL`, `IMMUTABLE_KINDS`); resolving a conflict never replaces a page that exists |
| Recovery | journal recovery refuses to replace an existing page, as for 020's knowledge records |

Files are contained per file as `load()` contains other families: a damaged page, a stray file, a link or a look-alike spelling (`Transcripts/`, a case-folded chat folder) is a problem in the result, never a crashed read. `grimoire.archaeologystore.read_transcript(store, chat)` decodes the pages and reports undecodable pages and a gap in the numbering.

A page holds at most 1000 posts. Writers split long transcripts by size too (posts take at most half of the store's 8 MiB document limit per page, less page 1's header and origin); `grimoire.transcripts.paginate` does that split in order (`first_budget` for page 1), and a single post too large for a page is refused (`post_too_large`).

**Rollback.** A pre-036 build reports `transcripts/` as unrecognized load problems and never writes there. It cannot recover a journal naming a transcript path: recover with a 036+ build or run `discard_journal`. Its allocators do not read pages; the importers in this slice name only actors that also have instance files, which every build reserves.

## The page payload (`grimoire.transcript/0`)

Pure values in `grimoire.transcripts` (`Post`, `Speaker`, `Variant`, `TranscriptPage`) with a strict codec (`page_from_payload`, `page_to_payload`).

```json
{
  "origin": {"format": "sillytavern-jsonl", "sha256": "…"},
  "header": {"user_name": "Sam", "chat_metadata": {"note_prompt": "…"}},
  "posts": [
    {"id": "p000001",
     "speaker": {"role": "character", "actor": "campaign:st-…/character/mara-1", "label": "Mara"},
     "variants": [{"text": "Who goes there?", "source": {"swipe": 0}},
                  {"text": "Welcome aboard.", "at": "b", "source": {"swipe": 1}}],
     "active": 1,
     "audience": "unknown",
     "source": {"line": 2, "speaker": "s-82287f967cfe"},
     "extras": {"original_avatar": "mara.png"}}
  ]
}
```

- **`speaker`**: `role` is one of `user`, `character`, `narrator`, `system` or `assistant` (a generated or narrated post whose source does not say which, as in V0.2.1). `actor` is the chat's actor only when one was explicitly mapped, otherwise absent. `label` is the source's display name, verbatim: two speakers with one label are never assumed to be one individual.
- **`variants` and `active`**: every text the post held (swipes, an edited text) and the index of the one shown. The others are history, never canon (`Post.unchosen`). `active` is `null` only when the source chose none.
- **`hidden`**: the source's own flag that the post was kept out of the conversation.
- **`audience`**: always `unknown` in this schema. A later schema may record a known audience for live play, where the runner saw who was present; an importer cannot know it.
- **`source`, `origin`, `header`, `extras`**: provenance and source data kept verbatim and never interpreted. `header` is the source's chat-level data and only the first page carries it.

The codec refuses, with a machine-readable `reason`: another schema (`unsupported_schema`), a payload that repeats the envelope's identity (`identity_in_payload`), unknown fields in a post, speaker or variant (`unknown_field`), a missing required field (`missing_field`), an actor of another campaign (`foreign_actor`), an audience other than `unknown` (`audience`), a repeated post id on one page (`duplicate_post`) and a header on a later page (`header_not_first`). Unknown top-level fields are kept as `extras`.

## Imported history and live play (035)

A chat's transcript is its **imported history**: text written before V2 (a SillyTavern export, a V0.2.1 scene), immutable and with audience unknown. What is played in V2 afterwards is 035's scene turns (`campaigns/<id>/turns/`, documented in 035's SCENES.md), a separate family: turns change in place (a take kept, struck or withdrawn), carry fixed audiences and are held back from sync until 028's merge milestone. The runner never writes transcript pages, and nothing here writes turns.

035's runner reads a chat's pages as its history before turn 1 and decides who heard them by its own rule ([SCENES](SCENES.md#who-heard-what)): the actors the chat was imported with heard every shown post, an actor attached later heard none, and pages it cannot all read are heard by nobody (`imported_history_unreadable`). Nothing is written back here, and a page still grants nobody knowledge. Accepted state (events, holdings, beliefs) stays in its own families; a page records only the text. A later writer that needs another payload (a scene segment, reviewed narration status, a known audience) adds a new schema version rather than reinterpreting `/0`.
