# Start Chat: one action from a card or a premise to play

**Status:** first slice of [Instant Campaign Creation](roadmap/sprints/instant-campaign-creation.md) (Sprint 052, Wave 6), milestone 1. Headless like the rest of V2: there is no UI yet, so "one click" is one call. It is one package, `grimoire.quickstart`:
- `grimoire.quickstart.model` (pure): a `Selection`, the `LaunchOptions` it offers, what `start` returns, and a broken source's `Repair`;
- `grimoire.quickstart.launcher` (adapter): `QuickStart`, which composes [107](CHATS.md)'s `ChatLibrary` and [035](SCENES.md)'s `SceneRunner`. It adds **no record family**: everything it writes is a 107 chat or a 035 turn.

All cards are synthetic and every model call is scripted (`backend/tests/test_quickstart.py`).

## What it does

**Proving scenario.** A player gives a one-line premise and a persona, and plays at once: Start Chat writes a world-less scope and chat, seats the persona with the player, and keeps the premise as the narrated opening. The player posts, the director replies, the app restarts, and play goes on. Adding a card later changes neither the chat's launch nor its premise, the new character hears nothing said before it arrived, and nothing becomes canon (no event, belief, relation or effect is written).

- **No world, no wizard.** The scope is unnamed (`title: None`) and has no world, mechanics or map. Naming it a campaign is an optional later step (`name_campaign`), and changes no chat id, actor id, launch or history.
- **No model runs** until the player plays: the opening is the card's greeting, the scenario's opening or the premise itself, never a generation.
- **One canonical chat.** A chat started from a bundle is found under the character, its lorebook and the persona as the same `ChatRef`, never a copy per view.

## A selection

`Selection` is exactly one launch:

| Build it with | Starts | Binds (pinned) |
|---|---|---|
| `Selection.character(ref)` | a chat with one standalone character | the character |
| `Selection.bundle(ref, lorebook=None)` | a character with a lorebook | the character, and `lorebook` or, when `None`, the card's own embedded lorebook (037's pinned `card.lorebook`); a card with none is refused `no_lorebook` |
| `Selection.scenario(ref)` | a scenario chat: no NPC is made up for it | the scenario (its own lorebook follows it, [CARDS](CARDS.md#chats)) |
| `Selection.from_premise(text)` | a bare premise (at most 4,000 characters) | nothing |

Anything else is refused `invalid_selection`: a lorebook alone (107 can bind one, but there is nobody to talk to and nothing to open with), a ref of the wrong kind, premise text on a card selection, a lorebook on anything but a bundle.

## Pinned by default

Every source Start Chat binds (the launch, a bundle's lorebook, the persona, a card added later) is bound **pinned** to the revision it resolves to at that moment, whether the caller's ref was pinned or not (the owner's Nov 6 call, *pin by default*). Importing a newer copy of the card later changes nothing in a chat already started: it keeps its revision's greeting, voice and lore, and moves only through [042's reviewed rebase](REBASE.md) (take or keep, field by field). A chat started after the import binds the new revision.

## The service

`QuickStart(chats, runner)`: `chats` is a `ChatLibrary` and `runner` a `SceneRunner`, both over the same open store, and `chats` writes with a trusted handle the runner's authority issued; anything else is refused `invalid_field`. The player acts with their own handle (`by`), which must be trusted, issued by the library's authority and human (`not_authenticated`).

**Who may act on a chat.** `continue_chat`, `add_card`, `name_campaign` and `detach_broken` are for the library owner or the player who controls the chat's persona; anyone else is refused `not_at_table` before anything is written (035's own table rule, checked first, so a refused player never seats anyone). A persona nobody controls yet (a chat made outside Start Chat, or a launch whose seat was lost) is the owner's to seat: another player never claims it by continuing.

| Method | What it does |
|---|---|
| `options(selection)` | What a selection offers, reading only: the pinned `sources` it would bind; its `openings` (a character's greetings, or a scenario's openings, by their imported ids, `group_only` marked); the `default_opening`, which is the first that is not group-only; the character card's own embedded `lorebook`, offered for a bundle (a bundle whose card embeds none is previewed with its character alone); and `related`, the chats that bind this source in the same role (a character as a character, never as a persona), newest first, for **Continue**; imported chats of that card are among them. |
| `start(selection, by, *, persona=None, opening=None, label=None)` | A new chat. One 107 transaction writes the scope, the chat and its actors; then 035 seats the persona with `by`'s player and every other character with the director, and opens with `opening` (an id from `options`; by default `default_opening`, else the premise), which is kept. A selection with nothing to open with starts empty and the player speaks first. Each call is a new chat, in its own scope, with new actors. |
| `continue_chat(chat, by, *, opening=None)` | An existing chat, to play on: never a second chat. A launch that stopped after its chat was written is finished here: control is seated for actors with none, a chat with no turn is opened as `start` would, and an opening still open is kept (its newest proposed take, or a take for the chosen `opening`; proposed again once if the cast changed). A chat under way is left as it is, and choosing an `opening` for it is refused `already_begun`. A chat with an imported history (026's V1 scene pages, 036's SillyTavern pages) and no 035 turn is seated but given no greeting: the imported conversation is its beginning. An `opening` the chat does not have is `unknown_opening`, before anything is written. |
| `add_card(chat, ref, by, *, role="character")` | Adds a character, lorebook or scenario to a chat under way, pinned. A new character is a new actor that witnessed nothing (107's `not_witnessed`) and hears only what is said from now on (035), seated with the director. |
| `name_campaign(chat, title, by)` | Names the chat's scope as a campaign (or clears the name): presentation only (107's `name_scope`). |
| `repairs(chat)` | Every source the chat binds that does not resolve now (107's `open_chat` broken list), with the player's `choices`. Reads only. |
| `detach_broken(chat, repair, by)` | Takes the `detach` choice: a 107 tombstone for a binding (107's `detach_source`), or the world detached from the scope (107's `detach_world`), so the chat plays on without it. |

**Refused before anything is written** (`QuickStartRefused.reason`): `invalid_selection`, `invalid_field`, `not_authenticated`, `not_at_table` (no persona, and the player is not the library owner: 035 lets only the owner act at a table with no player character), `unknown_opening` (an id `options` does not offer), `no_lorebook`, 010's reasons for a source that cannot be bound (`missing_source`, `missing_entity`, `missing_revision`, `excluded_source`, `entity_dropped`), 107's binding reasons (`role_kind`, `binding_limit`, `device_identity`, ...), `changed` (the library changed during the transaction; nothing was written, try again), `taken_ids_unlisted` (107 cannot list the ids a new actor must avoid), `denied` (the store refused the write), `store_error`, `no_chat` (a chat that cannot be read or opened) and `already_begun`.

**After the chat is written.** Seating and opening are 035 calls of their own, after 107's commit. If one is refused (for example `turns_unreadable`, or a race lost with `turn_changed`), `start` still returns the chat, with `Started.unfinished` set to 035's reason: the chat exists, and `continue_chat` finishes the launch. Start Chat never starts it twice. A refusal of `add_card`'s seat is reported the same way.

## Choosing the opening (035)

`SceneRunner.begin(chat, by, *, opening=None)` takes the opening's imported id: a greeting of the launch card (`first`, `alt-2`, `group-1`) or an opening of the bound scenario (`opening-1`, `opening-alt-2`). An id the chat does not have is refused `unknown_opening`, never replaced by the first one. A group-only greeting is never chosen by default, but may be chosen by its id. With no id, `begin` opens as before: the scenario's first opening, else the card's first greeting, else the premise.

## A source gone

A bound card that was deleted (or excluded, or has lost the pinned revision) is a `Repair`: its `field` (`launch`, `bindings.<role>`, `roster.actor`, `world`), its exact `ref`, 107's `reason` and the `choices`. The chat and its turns are kept, and nothing similarly named is bound in its place.
- A binding offers `detach`. The launch is immutable provenance and offers nothing; its binding (`bindings.character`, `bindings.scenario`) is the one to detach. A missing actor is reported, never rebound. A world offers `detach` too: `detach_broken` detaches the scope's world.
- A missing pinned revision is never swapped for the head: that is an update nobody reviewed. Importing the card again and 042's rebase is the way back.
- `detach_broken` on a source that resolves again, or was already detached, is refused `not_broken`.

## Known limits

- **A persona is chosen, not made.** With no persona the chat starts, but nobody can post in it: 035 needs a player character for a post (`no_persona`), so only the owner can act there (open it, ask for a director reply). A default persona for quick starts is an open question to the owner.
- **No session-zero boundaries at launch.** 016's configuration has no codec yet and 035 refuses to generate in a scope that has one (`session_zero_unreadable`), so Start Chat offers none: a chat with boundaries could not be played. Offering them at launch waits for 035 M2, which executes them.
- **Seat, open and keep are separate commits** after 107's transaction (above): a crash between them leaves a chat that `continue_chat` finishes, keeping an opening proposed but not yet kept. When the cast changed since that take and it is proposed again, it is proposed for the `opening` passed to `continue_chat` (by default the first greeting or opening), not necessarily the one first chosen.
- **An unseated persona needs the owner.** If a non-owner's launch lost its seat, only the owner can continue it, which seats the persona with the owner; reassigning it is 003's.
- **No duplicate guard.** Two `start` calls are two chats, as 107 defines a start. Offering **Continue** on `options(...).related` is the UI's part.
- **Lazy world growth is milestone 2.** Reviewing and persisting a named NPC or place only when it becomes relevant (reusing world/campaign record types), and growing a multi-region setting, are 052's later milestones; a world made later from a chat is 042's reviewed canon proposal.

## Later milestones (named owners)

| Item | Owner |
|---|---|
| A default persona for quick starts, if the owner wants one | 052 |
| Session-zero boundaries at launch, once a scope with them can be played | 052, 035 M2, 016 |
| Review and persist a named NPC/place when it becomes relevant; lazy world growth with provenance | 052 M2, M3 |
| The UI: Start Chat from Chats, Character detail, Scenario detail and a source collection; Continue; Add Character/Card; Name as Campaign / Attach World | 052 with 065 |
| Previewing and importing a card at launch | 037 |
