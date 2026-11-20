# Story threads

**Status:** first useful slice of [081](roadmap/sprints/story-graph-and-navigation.md). The pure package
`grimoire.story`, the app's `story` adapter ops (`backend/src/grimoire/app/adapters/story.py`) and the **Story
threads** page (`static/pages/story/`, route `story`), linked from the campaign hub. Read-only: the story writes
nothing of its own and calls no model. Like every read that opens a chat (the hub, the knowledge view, the inbox),
opening the chat first writes any accepted 021 effect's 020 knowledge still pending after an interrupted acceptance
(`SceneRunner.open`, `play/witness.py`): it completes what was accepted, and never records anything new.

The proving question is "what became of that promise?": a player finds an early promise, follows it through every
scene that records it, and sees when canon says it was kept or broken, and who still believes otherwise.

## What a thread is

A thread is one 012 **proposition** of the campaign's ledger (020), and every record the ledger links to it
**explicitly**. Nothing is inferred from text: two records are in one thread only because the ledger names one
from the other.

| Entry | What it is | Grade |
|---|---|---|
| `stated` | the proposition itself, when it was recorded | proposal |
| `report` | a character received it (as they have it: who they take the source to be) | proposal |
| `sent` | a character passed it on (`sender`) to another (`holder`): the table's own sends; for the director, also a report sent by now but received later, and a 063 rumour queued but not yet delivered | proposal |
| `observation` | what a character saw that their belief about it cites: their own percept, never the event as canon has it | proposal |
| `belief` | a character's own conclusion: believed, suspected or rejected; current or since changed | belief |
| `ruling` | canon ruled on its standing | confirmed |
| `event` | an event a ruling rests on, the proposition is overtly about, or a belief's perception cites | confirmed, or revoked |

So a commitment is a proposition ("Mara will bring the harbour key back before the thaw"), and its **outcome** is
canon's standing as of the read (012: the latest ruling; else `reported` once anyone received it; else
`unasserted`), with the ruling that decided it and the events it rests on. A dedicated commitment record is
077's (Quests and Commitments); until it lands, a promise is a proposition like any other claim.

### Grades

Every entry carries one grade, so an interpretation is never read as truth:

- **confirmed** (shown as *Canon*): an accepted event, or a ruling.
- **belief**: one character's conclusion, whatever its stance. Never the standing.
- **proposal** (shown as *Proposed*): an assertion only, or what a character saw. Receiving, repeating, seeing or
  believing it never establishes it.
- **revoked**: an event whose 021 accepted effect was revoked. Its 020 bridge is still stored
  (`revoked_but_bridged`), so it stays in the thread, struck through, and a ruling resting on it says so.

### Contradictions

Where the story disagrees with itself, named by the records that disagree:

- `reversed_ruling`: canon ruled it established, then refuted (or the other way round). Rulings of `unknown`
  reverse nothing.
- `belief_against_canon`: a character's **current** belief goes against canon's standing now (believed or
  suspected while refuted, rejected while established), as the hub's contested claim reads it.
- `revoked_ground`: the deciding ruling rests on an event whose effect was revoked.

### Scenes and sources

Each entry leads back to its source: the chat 020 recorded it from (its `origins`) and the turn its moment names.
The app numbers each turn on that chat's line, so the page links to it (`#/chat/<ChatRef>?turn=<number>`). A turn
a fork choice set aside is said ("From a turn set aside"), never linked as if it were played; a turn the app could
not number is shown without a link (`turns_unread`), and so, while a fork is unresolved, is a number two live
branches share (028), since it could open the other branch's turn. A record with no chat is grouped by the scene label its moment
carries. A thread's **scenes** are those places, in the order the thread first reaches each, with the turns and
everyone each involves; filtering by a character keeps only the scenes that involve them (milestone 1).

Every read is **as of** a campaign sequence: nothing later is shown. The app reads the story as it stands now.

## Who sees what

Spoiler exposure is controlled by **building** the table's thread from 012's `player_knowledge`, never by hiding
parts of the director's:

- **director** (`audience: director`): every entry, the outcome, the senders behind attributions and every
  contradiction. Ledger damage is said with its counts (`knowledge_damaged`).
- **table** (the default): only what the library owner's own characters received, passed on, saw (when their
  belief cites it) and concluded. A report one of their characters passed to another is both a send and a receipt. An
  NPC-era belief the present player did not author is kept and marked inherited. There is no outcome, no ruling, no
  event, no sender and no other character's evidence. A proposition none of their characters knows of has no table
  thread at all: `story.thread` refuses it `no_thread` exactly as it refuses an id the ledger lacks, and a search
  for how canon says it ended finds nothing. Damage is said without its counts. An owner who plays no character in
  the campaign is told so (`no_character`).

## Search

`story.threads` lists the threads that match, oldest first (by when each first entered the story):

- `query` (at most 200 characters, case-insensitive) is found in the proposition's text, **in the summary of an
  event of its thread** or in what a character observed of it, so a search for how a promise ended finds the
  promise. At the table only the text as the
  table has it is searched.
- `actor` (an instance of the chat's campaign) keeps only threads that involve them.
- `limit` (1 to 200, default 50): at most this many are listed, and `total` says how many matched.

## Ops

Both take `chat` (a `ChatRef`: its cast names the actors, its controls say whose characters are the table's) and
`audience` (`table` or `director`), and answer the chat, its cast, the filter's actor and `problems`.

| Op | Arguments | Answer |
|---|---|---|
| `story.threads` | `query?`, `actor?`, `limit?` | `threads` (each: id, text, first moment, last sequence, standing and when it was decided for the director, counts, actors), `total` |
| `story.thread` | `proposition`, `actor?` | `thread`: entries (kind, record, grade, moment, text, actors, source, and what each kind adds), scenes, actors, outcome, contradictions |

Refusals: `invalid_field` for a malformed argument (an audience, a limit, a query, an actor of another campaign, a
proposition id), and `no_thread` (see above).

Problems, shown on the page and never hidden: `knowledge_unreadable`, `knowledge_damaged`, `effects_unreadable`
(which effects were revoked could not be read: nothing is graded revoked), `turns_unread` (more than 32 chats, or
one whose turns could not be read), `no_character`.

## Not yet

Milestone 2 (branch differences and contradictory claims across forks with source links) and milestone 3 (an
interactive timeline of causal and relationship change), annotations, and any association inferred from text.
