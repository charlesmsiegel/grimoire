# Copy a chat into another campaign (116)

The **Copy into another campaign** page (`#/transfer/<ChatRef>?audience=director`) is Wave 9's UI exit for 084's
first slice ([SCENE_TRANSFER](SCENE_TRANSFER.md)): a chat's history copied, transcript only, into a new chat in
another campaign, with every actor's identity reviewed. A chat's **Details**, under "The director's", links to it;
the link names no audience, so on a screen the table reads it lands on the table's view.

The page is served by the `transfer` adapter (`grimoire.app.adapters.transfer`, owner 116). It runs 084's
`SceneTransfer` over the app's open library, as its owner with the app's trusted handle, and keeps no record of its
own: its one write is 084's own commit.

## Whose it is

A copy is the **director's**. 084's scan shows every actor of the origin's roster (active or not), and, for each,
the target campaign's actors that share its definition or only its name, which includes characters the table has
never met in that campaign; a plan shows what of the history the table's characters never heard, and the accepted
state that stays behind. So every op refuses `director_only` unless `audience` is `director`, and the table's view
of the page says whose it is and shows nothing. The owner acts; nothing is written as or for the table.

## Ops

| Op | Args | Answer |
|---|---|---|
| `transfer.targets` | `chat`, `audience` | `{chat, origin, targets[{campaign, title, world, chats}], not_available}`: every campaign the library lists chats of, but the origin's own |
| `transfer.scan` | `chat`, `target` (a campaign id), `audience` | `{cast[], sources[], history, history_problems[], notes[], provider_calls: 0, not_available}`; nothing is written |
| `transfer.plan` | `chat`, `target`, `audience`, `operation_id`, `cast`, `sources` (optional), `label` (optional) | `{plan: {chat, origin, mapping[], pages, posts[], posts_total, omitted[], left[], notes[], label, sources: {carried[], left_out[]}, bindings[], files[], provider_calls: 0, review}, saved: false}`; nothing is written |
| `transfer.commit` | the plan's args and its `review` | `{done: {chat, already_applied, pages, posts, files}}` |

- **`cast`** in the scan is 084's `TransferScan.cast`: each origin actor's `role`, `name`, `active`, `definition`,
  `problem`, `can_create`, its `candidates` (the target's instances of the same definition entity, the only actors
  it may be reused as) and its `namesakes` (target actors of *another* entity with the same name), each named.
  **`sources`** are the scenario and lorebook bindings, each with `broken`; `history` is 084's counts
  (`pages`, `page_posts`, `turns`); `history_problems` and `notes` (`world_differs`, a snapshot problem) are 084's.
- **`cast`** in a plan maps **every** origin actor's reference to `new`, `leave`, or a target actor's reference.
  Nothing is matched by name: the page offers only the candidates, `new` (where 084 can create the actor) and
  `leave`. A namesake is shown under the actor as "shares the name, another individual (never offered)".
  `sources` is the list of origin bindings to carry (all by default); `label` names the new chat (200 characters).
- **A plan's answer** is what the copy would write: each actor's `decision` (`reuse`, `new`, `leave`) and its
  target; the posts copied in order (speaker, text, where in the origin each came from; the first 200 listed, all
  counted); `omitted` with 084's reason (`private` for a whisper, telepathy or a withheld action; `unheard`, with
  who missed it) and the omitted words; `left` (accepted effects with their event ids and rulings, which stay in the
  origin); `notes` (`revision_differs`, `new_beside_existing`, `world_differs`); the new chat's label, the origin's
  scenario and lorebook bindings it carries and leaves out, and its bindings; and the files it would write, all
  under the target campaign.

## Reviewed, then made as reviewed

- `transfer.plan` writes nothing and answers a `review` fingerprint of everything it shows (the mapping, the
  pages' exact posts, what is omitted and left, the notes, the label, the sources and bindings, and the files) and
  of the new chat's own record as 084 plans it (label, bindings and roster, without the clock's times).
- `transfer.commit` builds the plan again from the same arguments and refuses `changed` unless its fingerprint is
  the one reviewed (the origin was played on, a binding moved, the target changed under a decision, the label or
  the sources differ from those reviewed...). 084's own commit then re-checks, under the store lock, that the origin
  chat, its pages and its turns are byte for byte what was copied: a change there is a stale write, also answered
  `changed`. A commit without a review is `review_required`; a review that is not a fingerprint `plan` could have
  answered (64 lower-case hex digits) is `invalid_field`. Any other refusal keeps its own reason: a scene paused
  after the review, for one, is 084's `history_incomplete`.
- The new chat's id is derived from the operation id, the origin chat and the target campaign (`t-copy-<digest>`),
  so the same plan built again is the same chat, and a commit retried after a lost answer finds the copy it made and
  writes nothing (`already_applied`). The page makes a fresh operation id for every plan.
- **A retry is checked against what the copy wrote.** 084 keeps no record of a copy's decisions apart from the copy
  itself, so a commit that finds a chat at its id checks the retry's own arguments against it: each actor's target
  in the first page's cast table (or, for a copy with no post, for which 084 writes no page, in the new chat's
  roster by role and definition, and only if this chat's copy would have no page either); that a reused actor kept
  its campaign continuity on the roster and an actor made new saw none of it; the label; and the sources carried.
  Any difference, or a chat at that id that is not a copy of this origin, is `operation_conflict`. **The limits:** a
  retry's review is checked only for its shape, not against the review of the commit that made the copy (no record
  of it is kept); for a copy with no post, an actor with no definition is not checked; and a retry is checked
  against the origin as it is now, so one made after the origin was paused answers the pause's refusal.
- **The origin is never written.** Every file a plan names is under the target campaign; the tests compare the
  origin campaign's files byte for byte before and after a plan, every refusal and the commit.

## Refusals

Every 084 refusal keeps its reason ([SCENE_TRANSFER](SCENE_TRANSFER.md#every-actor-is-decided)): `same_campaign`
(the target is the origin's own campaign: never offered, and refused if asked), `missing_scope`, `missing_chat`,
`origin_unreadable`, `unreviewed_actor`, `unknown_actor`, `different_individual` (a namesake), `actor_mismatch`,
`foreign_actor`, `merged_actors`, `cannot_create`, `second_persona`, `same_definition_twice`,
`history_incomplete` (a damaged page, a fork, a sync conflict, or a player's pause: the page shows the problems and
offers no plan, and **Scan again**), `unknown_source`, and 107's `InvalidBinding` reasons. The adapter's own are `director_only`,
`invalid_field`, `review_required`, `changed` and `operation_conflict`.

## The page

1. **Copy into**: the target campaign (never this one).
2. **What would be copied**: the history's size, its problems and notes; one labelled group per actor with its
   decision (nothing is pre-chosen) and its namesakes; the sources to carry and the label. Changing anything takes
   away a review of other decisions, and an answer to a plan asked before the change is never drawn.
3. **Review the copy**: who becomes whom, the new chat's label and the sources it carries and leaves out, the posts
   it would copy, what is not copied and why, what stays in this campaign, what to know, and the files (in a closed
   `<details>`). Focus moves to the review. **Copy as reviewed** commits it; **Change the decisions** goes back.
   A commit the app refuses, for any reason, takes the review away: the page scans again, says why (focus moves to
   it) and nothing was copied. A commit that got no answer (the app unreachable, the page left) keeps the review,
   since copying it again is safe.
4. **Copied**: a link to the new chat, which takes the focus. The new chat opens with the copied history as its
   imported history; the whisper is not in it.

The target, the decisions, the sources and the label being made are a draft of the tab (`ctx.draft`), cleared once
the copy is made.

## Not on a screen, and why

084's later milestones are not built, so the page names each and offers no control (`ui.unavailable`):

- transferring chosen state and attached assets (events, beliefs, relations, holdings, memories stay in the
  origin campaign);
- a chronology mapping beyond "the copy's history comes before its first turn";
- presenting missing art.

A copy within one campaign is a fork (028), not this page's.

## Tests

`backend/tests/test_app_transfer.py` drives every op through the app's adapter contract over the synthetic card
library: the proving scenario (the alternate Mara is a namesake and never offered; Mara is created new and the
target's Anya reused; the whisper is omitted as private; the commit makes a new chat that opens with the copied
history; the origin's files are unchanged byte for byte; a retried commit writes nothing; a retry with other
decisions, label or sources is `operation_conflict`; another label than the one reviewed is `changed`), a copy with
no post retried as applied, the table's view refused, a change after the review refused `changed` with nothing
written, a pause after the review refused with 084's reason, and 084's refusals with their reasons.
`js/transfer.e2e.mjs` runs the page end to end in fakedom against a real app server (from the chat's Details, the
table's view, the scan, a missing decision, a late plan answer never drawn, the review with its label and sources,
a commit that got no answer keeping the review, a refused commit dropping it and saying why, the commit and the new
chat); `js/transfer.test.mjs` unit-tests its logic.

## Not proved here

The page runs end to end in fakedom against a real app server. It has not been used in a real browser, with a
screen reader or on a phone.
