# World Change Sets: reviewed multi-record world edits

**Status:** first slice of [World Change Sets](roadmap/sprints/world-change-sets.md) (Sprint 043, Wave 5). It is implemented as:
- `grimoire.changesets`: pure values, a strict JSON codec (`grimoire.changeset/0`, provisional) and readable diffs;
- `grimoire.changesetstore`: the store-backed `ChangeSetLibrary`, which reviews, approves, applies and rejects.

There is no app or UI, and no model or worker is involved. The scenarios are synthetic. No store format changes: a change set writes ordinary definition revisions and the source manifest ([STORAGE](STORAGE.md)).

## What it means

- A **change set** is a proposal to edit one world source's definitions together: create new entities, and give existing ones new revisions. It is applied **all or nothing**, in one guarded store transaction.
- A human sees **exactly what will be written** before deciding: each file's bytes, a readable diff, the conflicts that stop it, and how the library's authority decides each file.
- **Nothing is overwritten silently.** Every record a change set touches, or pins, carries the revision and file etag it was drafted against. If the library changed since, applying is refused and nothing is written.
- **No unrelated file changes.** The only files written are the new revisions and the source manifest (whose heads move). Old revisions stay on disk, as the store keeps every definition revision.

## The representation (`grimoire.changesets`)

`ChangeSet(changeset_id, source, title, author, changes, rationale="", preconditions=())`:

| Field | Meaning |
|---|---|
| `changeset_id` | `[a-z0-9][a-z0-9._-]*`, at most 128 characters. |
| `source` | The one world source every target belongs to. There is no field that can name another source, a campaign or a path. |
| `title`, `author`, `rationale` | What a reviewer reads. `author` is attribution text, not authority; it becomes the `owner` of a created entity. |
| `changes` | 1 to 64 `RecordChange`s, at most one per entity and one per entity id. |
| `preconditions` | Up to 64 `Precondition(kind, entity_id, revision, etag)`: unchanged records the change set relies on. A changed record is never also pinned. |

`RecordChange(kind, entity_id, revision, payload, name=None, base=None, base_etag=None, evidence=())`:

- **Create** (`base` is `None`): a new entity, written at `revision` (usually `r1`). It must not exist.
- **Revise**: `base` is the head revision it was drafted against and `base_etag` that file's etag. The new `revision` is written and the head moves to it.
- `payload` (a JSON object) and `name` are the proposed content, exactly. A revision carries its base's `owner` (including none) and unknown envelope keys forward unchanged.
- `evidence` is up to 16 lines of attribution text ("session 12 notes"). It is shown, never interpreted.

`ChangeSetError(reason)` refusals: `invalid_field`, `empty_changeset`, `too_many_changes`, `too_many_preconditions`, `duplicate_target`, `duplicate_precondition`, `precondition_on_target`, `unsupported_schema`.

**Codec.** `encode` is canonical and `decode(encode(x)) == x`. Decoding is strict: an unknown key anywhere is `invalid_field`, because a proposal a human approves must not carry fields they never saw. This is the form a later authoring worker will submit (milestone 3). JSON types are kept strictly (`true`, `1` and `1.0` differ).

## The service (`grimoire.changesetstore`)

`ChangeSetLibrary(store, handle)` is trusted application code and is never handed to a worker.

| Call | Notes |
|---|---|
| `head(source, kind, entity_id)` | `(revision, envelope, etag)` of the entity's head: the declared head, or the only revision. Refusals: `missing_source`, `excluded_source`, `missing`, `no_head`, `unreadable`. |
| `create(source, kind, entity_id, payload, *, name, evidence, revision="r1")` | A create change. Whether the entity exists is judged at review. |
| `revise(source, kind, entity_id, payload, *, name, evidence)` | A revise change based on the current head. `name` defaults to the head's. The new revision is the next free `r<N>` after every revision file of the entity, headed or not. |
| `pin(source, kind, entity_id)` | A precondition on the entity's current head and bytes. |
| `review(changeset)` | A `ChangeSetReview`. Reads under one store lock and writes nothing. Refusals: `missing_source`, `excluded_source`, and `unstorable` for content the store cannot encode (over its size limit, or a number it cannot write exactly). |
| `approve(review, authority, *, approved_by, new_id)` | One digest-bound approval per file that needs one (below). |
| `apply(review, approvals=None)` | Commits exactly the reviewed bytes, or raises. |
| `reject(review, authority=None)` | Ends the review without writing, and revokes the approvals `approve` issued for it. A review whose apply failed may be rejected too. |

### The review

`ChangeSetReview` holds:
- `files`: one `FileChange` per file written, in change order and then the manifest. Each has its logical `path` (the address an approval names), `action` (`create`, `revise` or `manifest`), a one-line `summary`, the exact `new` bytes and their `digest`, and a unified `diff`. A revision is diffed against its base revision, so the reviewer sees the changed fields rather than a whole new file. A create is diffed against nothing.
- `conflicts`: why it cannot be applied now (below).
- `permissions`: for each file, how the authority decides `apply` with no approval (`granted`, `approval_required`, `not_granted`, ...). This uses the store's read-only `Transaction.check(handle)`: those decisions are audited like any other, and no approval is consumed.
- `blocked`: store refusals that no approval can clear, such as a name collision, or a hand-written number a rewrite of the manifest would round (`ImpreciseNumber`).
- `applicable`, `needs_approval`, `preview()`, `describe_approvals(approved_by)` and `state` (`open`, `applied`, `rejected`, `failed`).

### Conflicts

`Conflict(target, reason, detail)`. Each record is judged by itself:

| Reason | For |
|---|---|
| `exists` | a create whose entity now has a revision file or a declared head |
| `entity_id_taken` | a create whose id another kind of the source uses |
| `missing`, `no_head`, `unreadable` | a revised or pinned entity that is gone, has several revisions and no head, or whose head file cannot be read |
| `head_moved` | a revised or pinned entity whose head is no longer the expected revision |
| `base_edited` | a revised or pinned entity whose head file was edited since (a hand edit) |
| `revision_taken` | a revise whose new revision name now exists |
| `manifest_changed` | apply only: the manifest is not the one reviewed |
| `source_unavailable` | apply only: the source is missing or excluded |

The store's own compare-and-swap words (`changed`, `absent`, with a file path as `target`) appear only when no record conflict explains them.

**The manifest is shared.** Every record of a world has its head in one manifest, so a change to another entity's head, or to the title, changes the manifest bytes a human approved. That is not a conflict of any record, but the review is out of date: `apply` refuses with only `manifest_changed`, and `ChangeSetConflict.needs_redraft` is False. Review the same change set again and apply the fresh review. Any other conflict needs a new draft. Nothing is merged silently. A review that reported a conflict or a block is never applied, even if the conflict has gone away by then: its files were built from what the conflict changed, so review again.

### Applying

`apply(review, approvals)`, under the store lock:
1. checks every changed and pinned record again, and that the manifest is the one reviewed (`ChangeSetConflict`, nothing written, the review is `failed`);
2. stages the reviewed envelopes, and verifies the staged bytes equal the reviewed ones;
3. commits them in one store transaction with the library's handle and `approvals`. The store's guarantees apply: compare-and-swap, journal, crash recovery and authorization of every file over its exact bytes ([STORAGE](STORAGE.md#writing)).

It returns `AppliedChangeSet(changeset_id, revisions, etags)`. A review is applied at most once, and its state is checked and moved under the store lock, so a `reject` and an `apply` never both win. `WriteDenied` writes nothing and leaves the review open, so it can be retried with approvals; any other failure ends it and withdraws its pending approvals, so none outlives it (if the authority is unavailable at that moment, `reject` withdraws them later). The store's pinned residual race (R7: an external edit landing during publication) is still reported as `WriteConflict`. A pinned record, and a revised record's base revision, are not written by the transaction, so the store does not re-hash them before publishing: a hand edit to one in the moment between the check in step 1 and the commit is not caught (the library's writer lock keeps other Grimoire processes out of that moment).

### Approvals: the first approval UI (007)

A trusted `human:` handle with a covering grant applies directly. An untrusted handle needs a covering grant **and** one approval per file (see [TOOL_AUTHORITY](TOOL_AUTHORITY.md#permissions)).

`approve` registers, for each file in `needs_approval`, `Approval(new_id(), <the library handle's principal>, apply, path, digest of the reviewed bytes, approved_by)`, and returns the `{path: approval_id}` mapping `apply` takes. A handle that needs no approval gets none, and a file the handle may not write at all (`not_granted`) makes the review `not_applicable`.
- **Call it only from a human's direct action on this review**, after showing its files' bytes and `describe_approvals` (the `Approval.describe()` sentences, including the non-ASCII flag). This is 007's obligation on this sprint. It is trusted code; a worker handle cannot reach the authority.
- `approved_by` must be a human with an active grant covering every file. Registration is all or nothing. Approving the same review again withdraws the earlier set first, so a review never has two usable sets. An approval the library cannot confirm as withdrawn (the authority failed or cannot be trusted) stays tracked, and `reject` keeps failing until it can be withdrawn: after reopening the authority, `reject(review, authority=<the reopened one>)` finishes it. `reject` revokes the ones still pending before it ends the review, so a failing authority leaves the review open.
- A review with conflicts or blocks is not approved (`not_applicable`).
- The approvals cover those exact bytes: they do not authorize another review of the same paths with other content.

## Boundaries and deferrals

| Not here | Owner |
|---|---|
| Milestone 3: a bounded authoring worker submits the same representation; persisted pending proposals, and who may approve auto-apply exceptions | 043 later milestones, [World Authoring Assistant](roadmap/sprints/world-authoring-assistant.md) (087) |
| Undo. The replaced revisions are kept, so moving a head back is possible by hand, but what undo means after further outside edits is open | 043 open question |
| Links in the manifest. A change set copies the manifest's links unchanged | 043 later milestone |
| Campaign records (instances, relations). Play-derived changes go through [accepted turn effects](EFFECTS.md) | 021 |
| Reconciling a conflicting edit field by field | [Source Rebase](roadmap/sprints/source-rebase-and-semantic-merge.md) (042) |
| Several sources in one change set; Markdown payloads | later; the store's Markdown codec is a remaining 010 obligation |
| Which campaigns a change affects | [Provenance And Impact](roadmap/sprints/provenance-and-impact.md) (094) |
