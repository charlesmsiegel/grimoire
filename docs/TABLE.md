# Play at the table (115): a shared turn, NPCs answering together, captured companions

Three of Wave 8's first slices reach a screen here: two or more players at one screen (058), several NPCs
answering one moment (051) and captured companions (050). Each is served by its own adapter (owner 115) over the
domain service that slice already has. None keeps a store of its own, and none calls a model.

| Slice | Adapter | Where | Whose |
|---|---|---|---|
| 058 | `table` (`grimoire.app.adapters.table`) | in a chat: **Shared turn**, above the composer | the table at this device |
| 051 | `ensemble` (`grimoire.app.adapters.ensemble`) | `#/ensemble/<ChatRef>` (`?audience=director` for the director's view), from a chat's Details | the director |
| 050 | `companions` (`grimoire.app.adapters.companions`) | `#/companions/<campaign id>?chat=<ChatRef>` (`&audience=director` for the director's view), from a campaign's hub or a chat's Details | the director |

A chat's Details links to both pages without an audience: they open in the Table view, as everything the table's
screen links to does, and the director switches to the director's view there.

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
  player's words are not shown to whoever is at the keyboard (see [What hides a player's words](#what-hides-a-players-words)).
  A player moves only their own part. A part whose character was handed to someone else while it was held is
  flagged "No longer theirs" and offers **Take it out of the turn** to whoever is at the keyboard (the core
  `drop_draft` with the part's own `by`): its writer may no longer be seated here, and 058 refuses to post it.
- **Review the turn** reads `table.review`; **Post this turn** posts exactly what was reviewed (`table.submit`).
  A turn is posted only when the last one is closed: while the table's answer is still a take, the panel says to
  keep it or pass first, and offers neither a review nor **Post this turn**. A review on show is dropped as soon
  as the chat moves past it: other parts held, another turn posted, a take open or play paused. The table then
  answers the turn once.
- A redraw keeps the person's place: the boxes and the keyboard choice stay the same controls while the same
  parts are shown; **Who plays whom** stays open or shut as it was left and keeps a choice not yet handed over,
  though its pickers are drawn again (one open while an action finishes closes, its choice kept); and a control
  that had to be rebuilt takes the focus back by its `data-focus-key`. A
  move that takes a part to the top or the bottom leaves it no button for that way: the focus goes to its other
  move button, or to the part itself.
- **Who plays whom** (the library owner) hands a character to someone at this device, or back to the director,
  with the core `seat` op, after saying exactly what changes.
- A combined turn takes no label, framing or effect (058): nothing offers one.

| Op | Args | Answer |
|---|---|---|
| `table.review` | `chat` | `{chat, owner, players[], drafts[], etag, head, open_turn, kept_in: "memory", limits}`; each draft `{actor, by, text, mode, name, controller, ready}` |
| `table.submit` | `chat`, `expected` (the review's etag), `reviewed[]` (`{actor, by, text, mode}` in order), `head` | `{done: {already_applied, posts, turn, answered}, scene, review}`: `turn` is the posted turn's id, `answered` whether it has a take yet |

- Nothing reviewed refuses `review_required`; reviewed drafts that are not the ones at `expected` refuse
  `invalid_field`.
- The drafts are posted only while `head` is still the chat's last turn and they are exactly the ones reviewed.
  Another turn posted since the review, or a part corrected, added, dropped or moved, refuses `changed`, and the
  request posts nothing now.
- The moment `table.submit` posts, it keeps a receipt: the chat, the review's etag and its head, and the turn
  posted. A submission retried after its answer was lost (or the same review sent from a second tab) finds that
  receipt and answers `already_applied` with that turn's id, whatever was drafted or played since, even the very
  same words held again for the next turn; nothing is posted twice. Only the receipt says a review was posted: a
  turn that merely holds the same words (the same line posted through the composer, say, while the part is still
  held) is not this review's, and the review refuses `changed`. The page asks the table to answer the turn only
  when `answered` is false, so a retried submission never asks for a second answer.
- **The receipt is kept in memory**, beside the drafts (which 058 keeps in memory too), a bounded number per
  running app. An app restart between a submission and its retry loses it: the retry then finds another turn
  after its head and refuses `changed` ("it was not posted now"), although the first submission did post the
  turn. The chat, read again, shows that turn; nothing is posted twice.
- A draft whose character is not its writer's is flagged (`ready: false`): one handed to someone else while it was
  held, or one written by a player who never played it (058 checks who plays a character when the turn is
  posted, not when a part is held). 058 refuses it when the turn is posted (`not_your_character`, or
  `not_at_table` for a character handed away), and nothing is posted. 058's own refusals keep their reason.

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
  commit is retried (or the review is asked again with the same attempt id). The retry is compared with the
  saved request field by field (chat, turn, sequence, trainer, creature, outcome, device, capacity, placement)
  and answered from the receipt without working out again whose post backs it, so a character handed to someone
  else since the capture was made does not turn the retry into a conflict. The same attempt id for anything else
  refuses `attempt_conflict`. The page uses a new attempt id for each review and keeps it with that review.
- Two captures reviewed at the same campaign sequence: the first made takes it, and the other's commit refuses
  `changed` (its sequence is taken), writing nothing; reviewed again, it is made at the next sequence.
- A character a person plays takes that person's own key: the capture rests on that character's post in the
  chosen turn (`no_backing` without one), and the person ticks their own box at this device (`player_key_required`
  otherwise; `not_at_this_table` for a player not at this device).
- 021 accepts one change a turn (`turn_already_accepted`): the roster marks such turns, and the form offers only
  the others.
- 050's, 021's and 033's refusals keep their reason (`party_full`, `trainer_in_reserve`, `invalid_request`, ...).
  Malformed arguments refuse `invalid_field`, an unknown turn `unknown_turn`.
- **Nothing else follows from a capture, and the page says so:** no NPC speaks for the companion, nobody's
  control of any character changes, and no character learns of it ([COMPANIONS](COMPANIONS.md)).

## What hides a player's words

Per-seat privacy here is a property of the screen, not of the API. The panel shows the person at the keyboard
only their own parts' words, but nothing on the server enforces that:

- `by` on the core `draft` and `drop_draft` ops is asserted by the client. The text API checks only that `by` is
  one of the players this device was started for, and that a part held is corrected, moved or taken out by its
  own writer. It does not check that the person at the keyboard is that player, nor, when a part is held, that
  `by` plays the character: a part for someone else's character is held, flagged in the review, and refused
  when the turn is posted. Anyone at the device can draft, move or take out a part as any player the device was
  started for.
- The core `open` op, and every scene the ops answer, returns every held draft with its text, whoever asks.
  `table.review` does the same. The page hides other players' words; a person who reads the answers (the
  browser's tools, or the app's text API directly) sees them all.

That is enough for people sharing one screen who agree not to look, which is the situation 058 is for. A real
per-seat rule would need from 058: an authenticated seat for each request (a per-player token or session, so the
server knows who is asking rather than being told), drafts answered per seat (a player's own text, and only the
character, writer and mode of anyone else's), and `by` taken from that seat instead of from the request. None of
that is changed here: the domain is 058's.

## Tests

`test_app_table.py`, `test_app_ensemble.py` and `test_app_companions.py` run each op through the app's adapter
contract over a synthetic card chat: behavior, refusals, the director's privacy, stale reviews and retried
commits. Each page runs end to end in fakedom against a real app server (`js/table.e2e.mjs`,
`js/ensemble.e2e.mjs`, `js/companions.e2e.mjs`), including the sprint's proving scenario: two players draft one
turn, switch chats without losing a draft, and resume play; a part whose character was handed away is taken
out; and the focus survives a redraw (fakedom, like a browser, moves the focus to the body when the focused
control is taken out of the page, even if it is put straight back, or stops showing; and refuses to focus a
control that is out of the page, disabled, or under a hidden element or a closed `<details>`). The pages' logic has node unit tests
(`js/table.test.mjs`, `js/ensemble.test.mjs`, `js/companions.test.mjs`).

## Not proved here

The pages run in fakedom against a real app server. They have not been used in a real browser, with a screen
reader or on a phone. Two people at one physical keyboard is simulated by choosing who is at the keyboard.
