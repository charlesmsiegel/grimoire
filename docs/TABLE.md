# Play at the table (115): a shared turn, NPCs answering together, captured companions

Three of Wave 8's first slices reach a screen here: two or more players at one screen (058), several NPCs
answering one moment (051) and captured companions (050). Each is served by its own adapter (owner 115) over the
domain service that slice already has. None keeps a store of its own, and none calls a model.

| Slice | Adapter | Where | Whose |
|---|---|---|---|
| 058 | `table` (`grimoire.app.adapters.table`) | in a chat: **Shared turn**, above the composer | the table at this device |
| 051 | `ensemble` (`grimoire.app.adapters.ensemble`) | `#/ensemble/<ChatRef>?audience=director`, from a chat's Details | the director |
| 050 | `companions` (`grimoire.app.adapters.companions`) | `#/companions/<campaign id>?chat=<ChatRef>&audience=director`, from a campaign's hub or a chat's Details | the director |

## A shared turn (058)

The text API is started for the players at this device (`TextApi(local_players=...)`, its `seats`). When a chat
has more than one character a person plays, or this device was started for more than one player, the chat shows
**Shared turn** above its composer.

- **Who is at the keyboard** is chosen first. Only that person's own characters' unsent parts are shown, each
  labelled "<character>'s part, by <player>". Each is a draft of the browser tab (`ctx.draft`), keyed by chat,
  character and player: leaving the chat, or switching to another chat and back, keeps it, and a character handed
  to another player never shows the last player's words. Who was at the keyboard is kept per chat too.
- **Hold for the turn** gives the part to 058 (the core `draft` op, with `by`). The panel says where it goes: the
  app holds the parts in memory until the turn is posted, so restarting the app loses them (058's milestone 2).
  Words typed while the part was on its way are the next draft, never cleared by the answer.
- **This turn, in order** lists the held parts, each with its character, its player and its mode. Another
  player's words are not shown to whoever is at the keyboard. A player moves only their own part.
- **Review the turn** reads `table.review`; **Post this turn** posts exactly what was reviewed (`table.submit`).
  A turn is posted only when the last one is closed: while the table's answer is still a take, the panel says to
  keep it or pass first, and offers no review. The table then answers the turn once.
- **Who plays whom** (the library owner) hands a character to someone at this device, or back to the director,
  with the core `seat` op, after saying exactly what changes.
- A combined turn takes no label, framing or effect (058): nothing offers one.

| Op | Args | Answer |
|---|---|---|
| `table.review` | `chat` | `{chat, owner, players[], drafts[], etag, head, open_turn, kept_in: "memory", limits}`; each draft `{actor, by, text, mode, name, controller, ready}` |
| `table.submit` | `chat`, `expected` (the review's etag), `reviewed[]` (`{actor, by, text, mode}` in order), `head` | `{done: {already_applied, posts}, scene, review}` |

- Nothing reviewed refuses `review_required`; reviewed drafts that are not the ones at `expected` refuse
  `invalid_field`.
- A part corrected, added, dropped or moved after the review refuses `changed`, and nothing is posted.
- A submission retried after its answer was lost finds no drafts and the turn just after `head` holding the
  reviewed characters' posts by the same players: `already_applied`, nothing posted twice.
- A draft whose character is no longer its writer's is flagged (`ready: false`); 058 refuses it when the turn is
  posted. 058's own refusals keep their reason.

## Several NPCs answering (051)

The director authors one bid for each NPC on stage: answers, interjects first, adds a side remark, stays
meaningfully silent, or a nonverbal beat; who it is to, who would hear it, why (private to the director and that
NPC) and what it rests on. `ensemble.preview` answers the floor 051's pure `schedule` gives them, as an
**unaccepted proposal**: nothing is written, nothing is said in the chat, and no model is asked.

| Op | Args | Answer |
|---|---|---|
| `ensemble.exchange` | `chat`, `audience` | `{present[], kinds, speaking, groundings, max_bids, not_available}`: the cast on stage someone controls, each `{actor, name, role}` |
| `ensemble.preview` | `chat`, `audience`, `initiator`, `addressees[]`, `floor`, `player_speaking`, `bids[]` | `{proposal: true, accepted: false, order[], quiet[], held[], calls, views[], not_available}` |

- Every op refuses `director_only` unless `audience` is `director`; the table's view of the page says the plan
  is the director's and shows nothing of it.
- `views` is each present character's own projection (051's `for_actor`): its own reason, and only the public
  coordination of bids whose audience includes it. A side remark never reaches a character outside its audience,
  and another character's reason is never shown.
- While the player is still speaking every bid waits (`held`): no NPC interrupts them.
- 051's refusals keep their reason: `player_speaker` (a player's character never bids), `bad_bid`,
  `bad_grounding`, `bad_audience`, `bad_floor`, `duplicate_bid`, `absent_actor`.
- **Not available, and named so:** running the exchange (one separately voiced call per speaking NPC) and
  accepting its lines or quiet beats into the story. The chat's play has no interjection or side-remark modes and
  no per-line audience yet, and no review owner takes an exchange into the story. The preview says how many calls
  a run would make; neither step is offered as a control.
- The bids being written are a draft of the tab, kept across leaving the page; a change takes the old preview
  away, and an answer to a preview asked before the change is never drawn.

## Captured companions (050)

The page shows who trains whom: each trainer's companions, by their own instance (a second creature of the same
species is another companion), with its species, in the active party or the reserve; and the creatures nobody
has captured. A capture is reviewed first and made exactly as reviewed, once.

| Op | Args | Answer |
|---|---|---|
| `companions.roster` | `chat`, `audience` | `{trainers[], creatures[], people[], holdings, turns[], players[], problems[], outcomes, placements, max_capacity}` |
| `companions.capture_plan` | `chat`, `audience`, `attempt_id`, `trainer`, `creature`, `outcome` (success, failed, declined), `device`, `capacity`, `placement` (auto, party, reserve), `turn` | `{plan: {outcome, placement, trainer, creature, device, spends, writes, already_applied, files, sequence, keys[], etag}}`; nothing is written |
| `companions.capture_commit` | the plan's args, `sequence` and `expected` (the plan's etag), `keys[]` | `{done, roster}` |

- Every op refuses `director_only` unless `audience` is `director`: capture is an adjudicated decision, and the
  roster reads 017's relations, which the hub shows the director alone.
- The commit builds 050's request again with the same attempt id at the reviewed campaign sequence. A party, a
  creature or a campaign that moved on since the review refuses `changed`, and nothing is written.
- An exact retry finds 050's own receipt: `already_applied`, and the one device is spent once however often the
  commit is retried (or the review is asked again with the same attempt id). The same attempt id for another
  capture refuses `attempt_conflict`. The page uses a new attempt id for each review and keeps it with that review.
- A character a person plays takes that person's own key: the capture rests on that character's post in the
  chosen turn (`no_backing` without one), and the person ticks their own box at this device (`player_key_required`
  otherwise; `not_at_this_table` for a player not at this device).
- 021 accepts one change a turn (`turn_already_accepted`): the roster marks such turns, and the form offers only
  the others.
- 050's, 021's and 033's refusals keep their reason (`party_full`, `trainer_in_reserve`, `invalid_request`, ...).
  Malformed arguments refuse `invalid_field`, an unknown turn `unknown_turn`.
- **Nothing else follows from a capture, and the page says so:** no NPC speaks for the companion, nobody's
  control of any character changes, and no character learns of it ([COMPANIONS](COMPANIONS.md)).

## Tests

`test_app_table.py`, `test_app_ensemble.py` and `test_app_companions.py` run each op through the app's adapter
contract over a synthetic card chat: behavior, refusals, the director's privacy, stale reviews and retried
commits. Each page runs end to end in fakedom against a real app server (`js/table.e2e.mjs`,
`js/ensemble.e2e.mjs`, `js/companions.e2e.mjs`), including the sprint's proving scenario: two players draft one
turn, switch chats without losing a draft, and resume play. The pages' logic has node unit tests
(`js/table.test.mjs`, `js/ensemble.test.mjs`, `js/companions.test.mjs`).

## Not proved here

The pages run in fakedom against a real app server. They have not been used in a real browser, with a screen
reader or on a phone. Two people at one physical keyboard is simulated by choosing who is at the keyboard.
