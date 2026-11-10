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
`ReviewRefused` (an `ApiRefused`): `reason` is the domain service's own reason code, `status` is 404 for a
missing proposal, scan, review, chat or actor, 400 for a malformed request, 409 otherwise, and `details`
carries the paths or fields involved (for example `[path, "changed"]` rows of a stale write). Calls are
serialized. Bytes travel as base64.

## Proposals

Every write is a **proposal**: a preview op plans the domain service's write and returns

| field | meaning |
| --- | --- |
| `proposal` | its id, held in this process only |
| `kind` | `card`, `history`, `v1`, `changeset` or `rebase` |
| `digest` | `sha256:` over `["grimoire.review-preview/1", kind, [[path, sha256(bytes)], …]]` in path order |
| `files` | one row per written file: `path`, `action` (`create`, `replace`, `unchanged`, `write` when the current file cannot be read, such as a picture blob), exact `size` and `sha256`, `text` when it is UTF-8 up to 256 KiB, `binary`, and a readable `diff` of a replaced text file |
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
| `card.preview` | `card`, `portrait?`, `lorebook?`, `origin?`, `target?`, `as_scenario?`, `replace_edits?` | 037's plan of a Character Card V3 JSON, scenario card or V3 lorebook: destination, losses, collisions, suggestions, missing art |
| `history.scan` | `export` | 036's read-only scan of a SillyTavern JSONL export: speakers, counts, losses |
| `history.preview` | `export`, `speakers {id: source ref}`, `lorebooks?`, `label?`, `name?` | 036's plan. The report shows `accepted_events: []` and `memories: []`: imported text is transcript only |
| `v1.scan` | `source`, `archive_id` | 011's read-only inventory of a 0.2.1 library: worlds, campaigns, identities, art, losses, findings, and the proposed selection |
| `v1.preview` | `scan`, `selection?` | 026's plan for that scan and selection (worlds and campaigns by V2 id, `skip`) |
| `v1.receipts` | `archive_id` | the receipt of earlier imports of that archive |
| `changeset.review` | `changeset` (the 043 JSON document) | 043's review: diffs, conflicts, permissions; applying it registers any per-file approval with the authority on the owner's own apply (007) |
| `rebase.candidates` | `chat` | the chat's characters whose pinned source has a newer head |
| `rebase.review` | `actor`, `target?` | 042's field-by-field review |
| `rebase.preview` | `review`, `decisions {field: take or keep}` | 042's change for those decisions (`undecided` until every needed field has one) |
| `proposal.get`, `proposal.apply`, `proposal.reject` | `proposal` (and `digest`) | as above |

The V1 library is only ever read: 011 and 026 refuse a source inside the V2 library or its home folder
(`unsafe_paths`), a folder that is no 0.2.1 library (`not_a_v1_library`), and a library that changed after
its scan (`source_changed`). Rerunning an import of the same card, export or archive writes nothing new.

## Authority

The adapters write with the library's trusted owner handle, as the text API does. 037 asks its handle to
`propose` each staged path; here the owner proposes to themselves and approves on the same screen, so a
path the owner may `apply` may be proposed (`_OwnerProposes`). An owner with no grant over the destination
is still refused `denied` and nothing is staged.

## The page

`app/static/pages/review.js` (landed with the shell's route registration) draws one tab per kind, the
shared proposal panel (every file with its size, hash, text or diff; Approve and Reject bound to the
digest) and the "Not available yet" list from `review.capabilities`. It sets every library value as text.

## Not available yet

PNG and CHARX cards, Character Card V1/V2 and SillyTavern world info (037 later milestones); remote card art;
choosing link suggestions; stored change-set proposals (043 milestone 3: a change set is reviewed from its
document); rebasing a pinned scenario or lorebook (042 later milestone); V1 campaign sprites and voice
anchors (026 later milestones).
