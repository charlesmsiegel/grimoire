# Review API: import, migration and change review (110)

Sprint 110's adapters let the graphical app import a card, a SillyTavern chat history or a Grimoire 0.2.1
library, review a world change set and update a chat's character to a newer source revision, all through
one guarded pattern: **preview the exact bytes, then approve those bytes or reject them**. The adapters live
in `grimoire.reviewapi`; they call the shipped domain services (037, 036, 011 + 026, 043, 042) and add no
store of their own.

```python
api = ReviewApi(store, authority, owner="human:owner")   # the library's durable authority
api.call("card.preview", {"card": b64, "origin": "mara.json", "portrait": b64_png})
api.call("proposal.apply", {"proposal": "p-…", "digest": "sha256:…"})
```

`call(op, args)` takes a JSON object and returns one, like the [text API](TEXT_API.md). A refusal raises
`ReviewRefused` (an `ApiRefused`): `reason` is the domain service's own reason code (`refused`, with the
error's kind in `details`, when the service gives none), `status` is 404 for a missing proposal, scan,
review, chat, actor (`missing_actor`) or V1 folder (`no_source`), 400 for arguments or an input file that
cannot be read as asked (`invalid_field`, `too_large`, `unsupported_format`, `invalid_png`, `no_embedded_card`,
`invalid_portrait`, `unsupported_schema`, `not_a_v1_library`, `unsafe_paths` and the like), 409 for everything the library's
state refuses, and `details` carries the paths or fields involved (for example `{path, reason}` rows of a
stale write). Calls are serialized. Bytes travel as base64.

## Proposals

Every write is a **proposal**: a preview op plans the domain service's write and returns

| field | meaning |
| --- | --- |
| `proposal` | its id, held in this process only |
| `kind` | `card`, `history`, `v1`, `changeset` or `rebase` |
| `digest` | `sha256:` over `["grimoire.review-preview/1", kind, [[path, sha256(bytes)], …]]` in path order |
| `files` | one row per written file: `path`, `action` (`create`, `replace`, `unchanged`, `write` when the current file cannot be read here, such as a picture blob, or `unreadable:<kind>` when the store refuses to read it), exact `size` and `sha256`, `text` when it is UTF-8 up to 256 KiB, `binary`, and a readable `diff` of a replaced text file |
| `report` | the domain service's own report, as JSON, fields named as the service names them |

`proposal.apply {proposal, digest}` commits it. The digest must be the one shown, and it is recomputed from
the plan's own preview before the domain service commits, so a client cannot approve one set of bytes and
write another (`stale_approval`). The domain service's compare-and-swap still refuses a library that moved
since the preview (`stale`, with each path and why); the proposal then ends and is previewed again. A
change set over a world edited since review is `conflict`. A write the authority refuses is `denied` and
stays open. An applied proposal is applied once and then gone. `proposal.reject {proposal}` ends it and
writes nothing (a rejected change set is withdrawn through 043). `proposal.get` shows one again.

At most 8 proposals are held; past that the oldest is dropped (a change set's is withdrawn) and must be
previewed again. A restart drops them all. V1 scans and rebase reviews are held the same way, 4 each.

## Ops

| op | arguments | what it does |
| --- | --- | --- |
| `review.capabilities` | | the ops, formats and size limits, and what is unavailable and which milestone it waits on |
| `card.inspect` | `card`, `as_scenario?` | 037's `card_facts`: what the file says of itself (container, format, what it becomes, name, whether the file is its own portrait, the embedded lorebook's name and entry count). Reads no library and proposes nothing |
| `card.preview` | `card`, `portrait?`, `lorebook?`, `origin?`, `target?`, `as_scenario?`, `replace_edits?`, `embedded_lorebook?` | 037's plan of a card file: destination, losses, collisions, suggestions, missing art. `embedded_lorebook: false` leaves the card's own lorebook out |
| `history.scan` | `export` | 036's read-only scan of a SillyTavern JSONL export: speakers, counts, losses |
| `history.preview` | `export`, `speakers {id: source ref}`, `lorebooks?`, `label?`, `name?` | 036's plan. The report counts the staged files by kind (`staged`) and lists any accepted event, wrap-up, played turn or actor knowledge among them (`accepted_events`, `turns`, `memories`): imported text is transcript only, so these are empty |
| `v1.scan` | `source`, `archive_id` | 011's read-only inventory of a 0.2.1 library: worlds, campaigns, identities, art, losses, findings, and the proposed selection |
| `v1.preview` | `scan`, `selection?` | 026's plan for that scan and selection (worlds and campaigns by V2 id, `skip`) |
| `v1.receipts` | `archive_id` | the receipt of earlier imports of that archive |
| `changeset.review` | `changeset` (the 043 JSON document) | 043's review: diffs, conflicts, permissions. The owner applies it with their own grant, so it needs no per-file approval; rejecting it withdraws it through 043 |
| `rebase.candidates` | `chat` | the chat's characters whose pinned source has a newer head |
| `rebase.review` | `actor`, `target?` | 042's field-by-field review |
| `rebase.preview` | `review`, `decisions {field: take or keep}` | 042's change for those decisions (`undecided` until every needed field has one) |
| `proposal.get`, `proposal.apply`, `proposal.reject` | `proposal` (and `digest`) | as above |

A card file (`card`) is a Character Card V3 or V2 JSON, a scenario card or a V3 lorebook of at most 2 MiB, or a
PNG card of at most 32 MiB carrying one of those in a `ccv3` or `chara` text chunk ([CARDS](CARDS.md#png-cards)).
The PNG is read strictly and is the card's portrait unless a `portrait` is sent with it; a damaged one is
`invalid_png`, a picture with no card `no_embedded_card`, each with what was wrong in `details.detail`.
`review.capabilities` gives both limits (`max_bytes`, `png_max_bytes`).

The V1 library is only ever read: 011 and 026 refuse a source inside the V2 library or its home folder
(`unsafe_paths`), a folder that is no 0.2.1 library (`not_a_v1_library`), and a library that changed after
its scan (`source_changed`). Rerunning an import of the same card, export or archive writes nothing new.

## Authority

The adapters write with the library's trusted owner handle, as the text API does. 037 asks its handle to
`propose` each staged path; here the owner proposes to themselves and approves on the same screen, so a
path the owner may `apply` may be proposed (`_OwnerProposes` asks `apply` first, and `propose` only when
that is refused). Commits still ask `apply` through the real handle, so nothing is staged that the owner
could not write directly, and an owner with no grant over the destination is refused `denied` with nothing
staged. A preview writes no library file, but, like every planner's ask, the authority records its one
decision per staged path in the security audit, whether or not the proposal is applied.

## The page

The app serves these ops through 108's adapter seam as `imports.<op>`, one per review op (`imports.card_preview`
is `card.preview`, `imports.proposal_apply` is `proposal.apply`; `imports.capabilities` lists the `imports.*`
names). The adapter (`grimoire/app/adapters/imports.py`) keeps one `ReviewApi` per open library over the app's
trusted owner handle, so a proposal previewed in one request is approved in the next; proposals stay in memory
and are gone when the app restarts. The shell's client keeps only a refusal's reason and message, so the adapter
folds a refusal's details into its message (cut at 600 characters).

The Import page (`static/pages/import/`, route `import`, Library menu) lists the kinds the app serves across its
top, the chosen one kept in the address as `?tab=`: Character card (`card`), Chat history (`history`), V1 library
(`v1`), Change set (`changeset`) and Source update (`rebase`: a chat's characters following a newer revision of
their source). A kind shows only when the app serves every op its form calls and `imports.capabilities` lists it.
A card or a chat export is dropped on a drop zone or chosen as a file; the card zone takes PNG cards and JSON
documents. The page sends the card to `imports.card_inspect` and shows what was found: the name, the format, what
it becomes, and for a PNG card the picture itself (an `img` showing a `blob:` URL made from the dropped file,
let go when the form starts over or the page is left) with "Portrait: the PNG itself"; a JSON card offers "Add a
portrait" instead. A **Lorebooks** list beside it names the lorebook the card embeds, with its entry count, as
"embedded in the card"; Remove leaves it out of the import (`embedded_lorebook: false`) and it can be put back,
and one separate lorebook file can be added and removed. A character card offers "Import as a scenario". A file
the app refuses is shown as what it is, with the app's reason, and offers no preview. Each
kind previews into the shared review: a summary, every file on the left with its action and size, the selected
file's sha256 and its diff or text on the right, and a footer with the digest. "Approve and write N files" asks
first, naming how many files, and sends the digest back; Reject discards the proposal. Once decided, the footer
says the outcome and offers "Import another" and "Open the library". A stale or conflicting approval says nothing
was written and to preview again; a proposal the server no longer holds (an approval whose answer was lost, or an
app restart) says to check the library first, since it may have been written. The "What this app cannot import
yet" list comes from `imports.capabilities`.
Files are sent as base64 in one request, which the app server caps at 48 MiB, so one history file is limited to
about 34 MiB; a card file has the limits above. First run's "First card" step takes the same card files through
the same `imports.card_preview`.

## Not available yet

CHARX cards, Character Card V1 and SillyTavern world info (037 later milestones); remote card art;
choosing link suggestions; stored change-set proposals (043 milestone 3: a change set is reviewed from its
document); rebasing a pinned scenario or lorebook (042 later milestone); V1 campaign sprites and voice
anchors (026 later milestones).
