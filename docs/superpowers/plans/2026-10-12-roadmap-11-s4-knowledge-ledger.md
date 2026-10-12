# 11-S4: The Knowledge ledger: routes, UI and accept — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Review gates: substitute review (no Codex CLI in this environment); the Codex
gates are owed, and `@codex review` on the PR stands in for them (CLAUDE.md).**

**Goal:** the user can read, write, correct and remove knowledge overrides by
hand, from the campaign ledger. Delivers 11-C3 (full, less the soft
accept-from-capture surface, below).

**Spec:** §7.1, §7.4, §7.5, §11 ("Overrides", routes; "Frontend"), Slices
(11-S4). Builds on 11-S1 (`store/knowledge.py`, `store/knowledge_scene.py`).

## The soft need on 11-S3

11-S3 has not landed (it waits on 09), so no capture holds a classified part
or a Decision answer: there is nothing to accept from. Per the slice's own
degradation ("until then there is nothing to accept from, and the ledger's
`+ New` form is the only writer"), this slice ships the server half of accept
(`source: "accepted"`, the `unkeyed` refusal, and a keyless `unaware` stored
as a whole-scene `unaware`) and no capture-view button. 11-S3 adds the button
on the parts it captures, posting this same route. The checklist keeps the
slice's code box ticked with that note, as 07-S8 kept its documented outcome.

## Routes (`routes/ledger.py`)

All under `locks.campaign_lock(cid)`, writes wrapped in `undo.journalled(cid,
{"w": "knowledge", "id": kid}, kind="knowledge", ...)` as `manual`. Bodies are
plain `BaseModel` fields (`routes/models.py`), dumped through `_dump`.

- `GET /campaigns/{cid}/ledger/knowledge` -- every entry for display:
  the stored fields, its id, `scene` (`{sid, title}` or null when the
  identity no longer resolves -- "points at a deleted scene"), `posts` (each
  named post's current index, speaker and text, or `missing`), `stale` (a
  grant whose fingerprint no longer holds), `audience_now` (the audience's
  current name, null when it no longer resolves), and `intact` (the file read
  whole). `find_by_identity` raising `UnreadableError` is 409 `busy`.
- `POST /campaigns/{cid}/ledger/knowledge` with `KnowledgeSave`:
  `audience`, `status`, `scene` (a sid), `posts` (`[{key, part}]`, or null
  for the whole scene), `note`, `source` (`manual` default, or `accepted`).
  - 404 for a missing campaign or scene; 400 for a malformed audience,
    status or source, or an audience that does not resolve in this campaign
    (a character not in it, a group not in it), or a post key the scene does
    not hold.
  - The scene's identity comes from `ensure_identity` under the lock (it may
    write the identity line, which a legacy scene needs).
  - The route computes the variant and digest (`knowledge.fingerprint`) or
    the transcript hash; a client never supplies a fingerprint.
  - A post with an empty key: `knows`/`suspects` is 400 `unkeyed`; an
    `unaware` is stored as a whole-scene `unaware`, which only withholds
    more (§7.4).
  - `audience_name` is the audience's current display name.
  - Answers `{id}`.
- `PUT /campaigns/{cid}/ledger/knowledge/{kid}` with `KnowledgeEdit`: any of
  `audience`, `status`, `note`, and a new subject (`scene` + `posts`), plus
  `restamp`. The stored fingerprint is kept unless a new subject is given
  (fingerprinted now, as a POST is) or `restamp` is true (the stored keys
  re-fingerprinted against the current text, which the detail view showed).
  A note edit therefore never silently revalidates a stale grant. 404 for an
  unknown id; restamping a subject whose scene or post is gone is 409
  `gone`.
- `DELETE /campaigns/{cid}/ledger/knowledge/{kid}`.

`knowledge.add` gains an optional `kid`, so the route mints the id under its
hold and journals the create against it (`knowledge.mint_id`). `UnreadableError`
and `knowledge.KnowledgeError` from a garbled file map to 409 `busy` and 409
`malformed`.

## Frontend

- `api.ledgerKnowledge(cid)`, `ledgerCreateKnowledge`, `ledgerSaveKnowledge`,
  `ledgerDeleteKnowledge` and the types.
- `components/ledger/Knowledge.tsx` (after the plan gate, see below): the
  ledger owns the screen, so `KnowledgeRows` puts `+ New statement` and one
  row per entry in a "Who knows what" `ColumnSection`, and `KnowledgePane`
  is the detail in main, read-only by default with an explicit Edit step.
  - **View** (`.detail-view`): `<h3>` "Mara knows", the named posts' current
    text and the note rendered as markdown; `.detail-sidebar` with Edit, the
    audience and scene as link chips, status and source as plain chips, the
    §7.3 group copy, and for a stale grant its reason with **Confirm against
    current text** (a re-stamp carrying the fingerprints this view shows) or
    **Remove**.
  - **Form**: who, status, scene, whole scene or a checklist of its keyed
    posts (`GET .../knowledge/scenes/{sid}`, with their fingerprints), note.
    A save sends only what changed.
- `LedgerView`: the `knowledge` section (`/ledger/knowledge[/<kid>|/new]`,
  in `ledgerPaths`) is excluded from the table path like `continuity`.

## Tests

- `backend/tests/test_ledger_knowledge_routes.py`: create (posts, whole
  scene), a legacy scene given an identity, 409 `busy` on `UnreadableError`,
  400 for an unknown post key / bad audience / unresolved audience, 400
  `unkeyed` and the keyless `unaware` stored whole-scene, 404 for an unknown
  scene and kid, `accepted` source, PUT keeps the fingerprint on a note edit
  and re-stamps on `restamp`, stale and deleted-scene flags in the GET,
  journal rows as `manual` and Undo restores (create, edit, delete).
- `frontend/src/components/ledger/KnowledgeEditor.test.tsx`: a row opens the
  read-only view (rendered text, no textarea) with its sidebar; Edit shows the
  form; `+ New` opens the form; Save posts the right subject; a stale entry
  offers the re-stamp; `ledgerPaths` round-trips the new section.

## Plan gate (substitute review) and dispositions

An independent reviewer agent read this plan, the spec and the in-progress
routes (no Codex CLI here). What changed:

| Finding | Disposition |
|---|---|
| B1 an `.editor` rail beside the ledger's column misreads a page that owns the screen (CLAUDE.md) | Fixed, spec §7.4 amended: the entries are a "Knowledge" `ColumnSection` in the ledger's context column, the detail view and form in main, with the same view/edit modes |
| B2 a confirm or a create could grant text changed after it was shown; a full-form save could re-fingerprint | Fixed: `GET .../knowledge/scenes/{sid}` returns each keyed post's fingerprint and the scene digest; a grant (POST, or a restamp) carries them as `expect`/`expect_digest` and is 409 `moved` on a mismatch; the form sends only changed fields; a note edit keeps the stored fingerprint (tested) |
| S1 omitting `posts` granted the whole scene | Fixed: `whole_scene` is explicit; neither is 400 `bad_subject` |
| S2 a status flip bypassed `unkeyed` | Fixed: `unaware` → grant needs a newly named subject |
| S3 an audience renamed or unresolvable still looked live | Fixed: `stale_reason: "audience"`; re-selecting the audience records its name |
| S4 one unreadable scene file hid the list | Fixed: that entry is `unverifiable`; the identity lookups are cached per identity |
| S5 the capture-view button had no owner | Fixed in the spec: moved to 11-S3 (classified parts) and 11-S6 (Decision answers); this slice ships the route side |
| S6 `knowledge` would fall into the table path | Fixed: excluded from `tableSection` like `continuity` |
| M1 grants on hidden or ambiguous posts | Refused (400 `hidden`, `unknown_post`); the list says `hidden` |
| M2 identity written by a refused request | Fixed: `ensure_identity` runs after every check |
| M3 empty journal diffs | Fixed: `undo._display` renders a statement as "who: status — note" |
| M4 re-pointed statements keep their age | Fixed: a new audience or subject dates it now (`update(renew=)`) |

## Code review and final gate (substitute, covering 11-S1 and 11-S4)

An independent reviewer agent reviewed the diff and checked it against the
spec as amended (no Codex CLI here; `@codex review` on the PR stands in for
the Codex gates). Final gate: both slices deliver what the spec says, the
accept button's move to 11-S3/11-S6 is explicit in all three slices, and
nothing was dropped. Findings:

| Finding | Disposition |
|---|---|
| A reclassified group's grants pass to a new group of the same name (the id is reissued) | Fixed: a campaign group reclassify (`overlay.reclassify_entity`, preflighted) and a World one (`reclassify.world_entity`, for every following campaign) prune the audience like a delete; both tested, and the writer guard allows `store/reclassify.py` |
| The UI could not re-confirm a renamed audience, nor turn an `unaware` into a grant | Fixed: "Confirm it is still {name}" sends the audience; a flip to a grant always sends the subject with `expect` |
| A garbled `knowledge.json` 500s every branch and replay | Fixed: `branch_scene` preflights it before writing (409), and `KnowledgeError` has a 409 `malformed` handler |
| A restamp could flip an `unaware` into a whole-scene grant | Fixed: any `unaware` → grant without a new subject is 400 `bad_subject`, restamp included |
| `UnreadableError` from `scene_identity` was a 500 | Fixed: 409 `busy` on the scene read and every write, tested |
| An unreadable scene read as deleted (and offered Remove) | Fixed: `stale_reason: "unverifiable"`, no Remove |
| A note could not be edited on a statement about a deleted scene | Fixed: an edit that keeps the subject needs no scene read |
