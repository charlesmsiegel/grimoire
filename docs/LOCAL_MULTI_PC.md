# Local multi-PC turns (058)

`grimoire.play.together`, `grimoire.play.drafts` and the text API's local table (`grimoire.textapi.table`)
implement the first useful slice of [Local Multi PC](roadmap/sprints/local-multi-pc.md): two or more player
characters, each under its own controller, post in one turn, and the cast answers that turn once. The proving
scenario is executable in `backend/tests/test_local_multi_pc.py`: Anya Rook (played by `human:ana`) and Tamsin
Hale (handed to `human:bob` by the owner) speak, Mara Vell answers once, each post keeps its author, and Mara is
still the director's NPC.

## Who controls a character

Control is 003's sovereignty ledger, as for one player ([PLAYER_SOVEREIGNTY](PLAYER_SOVEREIGNTY.md)). `seat`
gives the chat's persona to its player and every other bound character to the director. A character becomes a
player character in only one way: the library owner hands it over with
`SceneRunner.seat_player(chat, by, actor, controller)`.

- `by` must be the owner's trusted handle (`not_owner` otherwise), and `controller` a human principal or
  `"director"` (`invalid_field`).
- The actor must be in the scene's cast (`not_present`), already seated (`unassigned_actor`), and not the
  persona, which is seated with the chat (`persona`).
- A character another player controls is handed back to the director first (`seated_elsewhere`): one player
  never takes a character straight from another.
- A character is not handed to a player who gave a delegation naming it that is not revoked
  (`delegations_on_record`). 003 tells a delegation's validity by its grantor controlling the actor now, so
  handing the character back to that player would revive it. This is the Wave 1 obligation 058 inherited.
- A character whose post is in the open turn keeps its controller until that turn is kept or passed
  (`turn_open`): handed to the director, the model would answer the post as the character it was written for.
- Every change is a `ControlChange` recorded with the owner's handle, so 003's control history says who handed
  which character to whom. Handing a character to the controller it already has writes nothing.

One player can hold several player characters ("one player, multiple PCs"); several players can each hold one.
Control is per device: `authority/` does not sync ([SYNC](SYNC.md)), so each device seats its own table.

## A combined turn

`SceneRunner.post_together(chat, contributions)` takes 1 to 8 `Contribution(by, actor, text, mode)` values, in
posting order, and writes one turn holding every post. `by` is the trusted handle of the character's controller.

- **Each post is its own controller's.** Every post is read exactly as a single `post` is: 008 routes it, 003's
  `submit_contribution` accepts it only from the actor's current controller (`not_your_character`), and 032 says
  who hears it. The post records that principal as `submitted_by`.
- **Nothing becomes a player character by being posted for.** A contribution for a character the director plays
  is refused `not_a_player_character`; one for a character nobody controls `unassigned_actor`; one outside the
  cast `not_present`; one off the stage `not_on_stage`. A combined turn holds one post per character
  (`invalid_field`).
- **All or nothing.** Every handle is checked, then every contribution read, before anything is written, and the
  turn is written in one compare-and-swap. A refused contribution writes no post of any character, so nothing is
  half-posted and the cast is never asked to answer part of a turn. The same submission retried onto its own
  turn, before anything was generated, returns that turn; anything else while the last turn is open is refused
  `turn_open`, a single `post` included.
- **Who heard what is each post's own.** A whisper in a combined turn is heard by its addressees and its speaker;
  the other posts by everyone present. `heard_for` and `table_lines` give each listener the posts they heard, in
  order, then the turn's kept lines.
- **One answer.** `generate` runs once on the turn: an NPC hears every post in its audience, the director's
  narration answers them all, and a player character is never voiced (narration review rejects a line for a
  human-controlled actor).

`TurnRecord.post` is the first post and `TurnRecord.also` the others; `TurnRecord.posts` is all of them in order.
Each post's 003 contribution id is `TurnRecord.post_id(index)`: the turn id for the first, `<turn_id>.p<n>` for
the *n*-th.

### The turn file

A combined turn is written under its own payload schema, `grimoire.scene-turn/2`, with the further posts under
`"also"`. A turn of one post is still written under `grimoire.scene-turn/1`, byte for byte as before. The decoder
requires `"also"` under `/2` and refuses it under `/1` (`invalid_field`). A build from before 058 does not know
`/2`, so it refuses the turn file and its chat stops (`turns_unreadable`) instead of showing, answering or
rewriting the turn with the other players' posts missing.

### Not in this slice

A combined turn is not labelled, framed or given an effect (`combined_turn`): several characters' posts are not
one action to adjudicate, and whose key one effect would need is not settled. The cast's answer is kept like any
other take.

## Drafts

`TurnDraft(chat, drafts)` is one chat's pending combined turn, a pure value. Each `Draft(actor, by, text, mode)`
is one character's post as its drafter wrote it; `mode` is `auto`, `speech` or `action`.

- `put` adds a draft (last, or at `at`) or corrects the character's draft in its place; `move` places a draft;
  `drop` removes it. A player changes only a draft they wrote (`not_your_draft`); a character with no draft is
  `no_draft`.
- At most 8 drafts, one per character, each at most 20,000 characters (`too_many_drafts`, `invalid_field`).
- Nothing about a draft is checked against the table: control is read again on submission, so a draft whose
  character changed hands is refused then and nothing is posted.

## The text API's local table

`TextApi(..., local_players=[...])` names the other people playing at this device when the app starts
(`python -m grimoire.app serve LIBRARY --local-player human:bob`). Each is a human principal other than the owner; the app vouches for
whichever of them a call names, as it does for the owner. Nobody else is ever named (`not_at_this_table`).

| Op | Arguments | Result |
|---|---|---|
| `seat` | `chat`, `actor`, `player` (the owner, a local player, or `"director"`) | the scene: 058's `seat_player` |
| `draft` | `chat`, `actor`, `by` (default the owner), `text`, `mode`, `at` (0 is first; alone, a move) | the scene |
| `drop_draft` | `chat`, `actor`, `by` | the scene |
| `submit` | `chat` | the scene: every draft posted, in order, as one turn. Drafts are cleared once it is written, and kept if it is refused (`no_drafts` with none) |

The scene adds `drafts` (each `actor`, `name`, `by`, `text`, `mode`) and each player character's `controller`
in `cast`. Every turn carries `posts`, each with `speaker`, `actor`, `by`, `kind`, `channel`, `text`, `private`,
`withheld` and `to`; `post` is still the first of them.

## Limits

- **Drafts are in memory.** The open app holds them, at most 64 chats' worth; a restart loses them. Persisting
  drafts and recovering an interrupted submission are milestone 2.
- **One screen.** Everyone at a local table reads the owner's view, so a whisper one player's character posts is
  on the screen the others read (the turn still records who heard it). A per-player view is 053 and 102.
- **No drafting UI.** The chat page renders a combined turn's posts, but composing one is the text API's for now
  (Wave 8 UI exit, 115).
- **No effects on a combined turn** (above). Troupe-style play and several simultaneous protagonists are
  milestone 3.
