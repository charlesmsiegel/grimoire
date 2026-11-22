# Cross-campaign scene transfer

**Status: implemented headless service (Sprint 084's first slice: transcript only).** A chat's history can be copied into a new chat in another campaign. Every actor's identity is reviewed: a name, a shared world or an alternate portrayal never makes two characters one individual. The origin is never written. Requirements: [Safe Cross-Campaign Scene Transfer](roadmap/sprints/cross-campaign-scene-transfer.md).

Code: `grimoire.scenetransfer` (`SceneTransfer`, with the pure history reader in `scenetransfer.history`). Tests: `backend/tests/test_scene_transfer.py`.

## Scan, plan, commit

| Step | What it does |
|---|---|
| `SceneTransfer(store, handle, clock=, new_id=, owner=)` | Over one open, writable library, as 107's `ChatLibrary` is |
| `scan(origin: ChatRef, target: ScopeRef)` | Reads the origin chat and the target campaign. Writes nothing and calls no provider (`provider_calls == 0`) |
| `plan(origin, target, cast=..., sources=None, label=None)` | One 107 `ChatChange`: a new chat in the target, its bindings and new actors, and the copied history as 036 transcript pages, in one transaction |
| `plan.preview()` / `plan.commit(approvals)` | The exact bytes; the write. `commit` first checks, under the store lock, that the origin chat, its pages and its turns are byte for byte what was copied, and raises `StaleWrite` otherwise (plan again) |

`scan` reports:

- **`cast`**: every actor of the origin's roster, active or not. Each gives its role, the definition the origin bound it to (pinned as the origin pinned it), its name, and two lists:
  - `candidates`: the target campaign's instances of that same definition entity. These are the only actors it may be reused as. A 033 creature bound by instance gets none: one species is many individuals, so the same definition is no evidence of the same one, and this slice can only leave a creature out.
  - `namesakes`: target actors of another entity with the same name (`name_key`). They are shown so a reviewer sees them, and are never offered.
- **`sources`**: the origin's scenario and lorebook bindings, each with `broken` when it does not resolve now.
- **`history`**: counts (`pages`, `page_posts`, `turns`).
- **`history_problems`**: why the history cannot be copied whole now: a damaged or missing page (`imported_history_unreadable`, by the runner's own rule), unreadable turns, an unresolved fork (028), a sync conflict, or a 016 pause (`paused`: a copy must not play on past a player's safety stop; resume first). `plan` refuses while any stands, judged on the very reads it copies from.
- **`notes`**: `world_differs` when the target plays another world (or none), and the origin snapshot's own problems.

## Every actor is decided

`cast` maps **every** origin roster actor to one of:

- **a target actor**: reused. It must be in that member's `candidates`. A reused actor binds its definition unpinned, so 107 pins it to the revision that actor already plays in the target (042's rule). The plan notes `revision_differs` when that is not the revision the origin played.
- **`NEW`**: created in the target from the very definition revision the origin played, as a new `not_witnessed` actor with a fresh id. The plan notes `new_beside_existing` when the target already holds instances of that definition.
- **`LEAVE`**: not carried over. Its posts are still copied when they qualify, with their label and no actor.

Nothing is matched by name. Refusals (`TransferRefused.reason`, with `.actor` for a cast refusal) are:

| Reason | When |
|---|---|
| `unreviewed_actor` | an origin actor has no decision |
| `unknown_actor` | a decision names an actor the origin's roster does not hold |
| `different_individual` | a reuse names a namesake (an alternate portrayal, or any other entity with the name) |
| `actor_mismatch` | a reuse names a target actor of another definition |
| `foreign_actor` | a reuse names an actor outside the target campaign |
| `merged_actors` | two origin actors map to one target actor |
| `cannot_create` | `NEW` for an actor the origin cannot show, or for a 033 creature bound by instance (leave it out) |
| `second_persona` | two origin personas are carried over (a chat has one) |
| `same_definition_twice` | two origin actors would be one binding of one definition |
| `same_campaign` | the target is the origin's own campaign. That is a fork, not a transfer |
| `missing_scope`, `missing_chat`, `origin_unreadable` | the target campaign or the origin chat cannot be read |
| `history_incomplete` | a `history_problems` entry stands |
| `unknown_source` | `sources` names a scenario or lorebook the origin does not bind now |
| 107's `InvalidBinding` reasons | passed through unchanged (a broken source, a reused actor's id paired with another kind, ...) |

## What is copied

The copy holds the origin's history as its table saw it, in order: its imported pages (036/026), then its played turns (035) on the chat's line.

- **Page posts** travel whole: every variant (an unchosen swipe stays unchosen), `hidden`, the speaker's role and label, and `extras`. The speaker's actor is mapped, or dropped for `LEAVE`. A post whose actor stays behind, or that names its speaker by no actor (a V0.2.1 `**You:**` post, named by the origin's persona), is labelled with the name the origin's runner heard it from, so the target hears it from the same speaker and never takes an unlabelled reply for narration. `source` records where the post came from (`copied_from`: chat, page, post), with the source's own provenance kept beneath it.
- **A turn's posts** become `user` posts labelled with their character's name, with an action in asterisks, as the runner shows it.
- **A kept take's shown lines** become `character` posts with the line's speaker label, or `narrator` posts for narration. On a 041 stage, only a line a player character of its take heard is copied (`table_lines`' rule).

The new chat opens as the origin did: its premise, or its launch source when that is carried (otherwise the first carried character or source). Every lorebook the origin ever bound that the copy does not carry (one the origin detached, or one the caller left out of `sources`) is carried as an opt-out (bound and detached), so a carried scenario's own lorebook cannot bring it back.

Each copied post records its origin turn and post or line under `copied_from`. The new pages get new post ids (`p000001` onward). Page 1's `origin` is `{"format": "grimoire-scene-transfer", "from": <origin chat>, "cast": {<origin actor>: <target actor or null>}, "posts": n, "pages": k}`. No source header is carried.

### Who heard it

The target's runner hears a chat's transcript pages by one rule: the cast the chat starts with heard every shown post ([SCENES](SCENES.md#who-heard-what)). A copy is therefore never allowed to give an actor something its origin counterpart did not hear:

- A post is copied only when **every origin actor the copy carries over** heard it in the origin.
  - Page posts were heard by the origin's imported cast (its sequence-0 roster), by the runner's rule.
  - A turn's post or line was heard by its own `audience`.
- Narration, a `system` notice and a hidden post were heard by no actor, and are heard by none in the target either, so they are copied as the table's.
- Anything else is reported in `plan.omitted` as `unheard`, with the carried-over actors that missed it. Leaving such an actor out (`LEAVE`) lets the post through.
- A **private** post (a whisper, telepathy, a withheld action) is never copied (`private`): the target's pages cannot say who heard it.
- A proposal, a withdrawn or struck line, and a take nobody kept were heard by no one, and are not history.

### What stays in the origin

A copy is text, never state. It never writes the origin's accepted state to the target. `plan.left` reports each turn's accepted (or accepting) 021 effect with its event id, and each framed ruling (029) with its decided outcome line. Events, beliefs, relations, holdings, memories, evidence and knowledge stay where they are. The target's runner hears the copied text and nothing that was accepted about it.

## Not covered yet

The sprint's later milestones are not covered yet:

- reviewed transfer of chosen state and attached assets, with conflict recovery;
- a chronology mapping beyond "the copy's history comes before its first turn";
- presenting missing art.

The director reviews and makes a copy on the **Copy into another campaign** page (116, [TRANSFER_PAGE](TRANSFER_PAGE.md)), linked from a chat's Details; it shows a plan, writes nothing, and commits only what was reviewed. A copy into the origin's own campaign is refused; a fork is 028's.
