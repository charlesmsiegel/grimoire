# Chat launch and scene play in the app (111)

Sprint 111 puts Start Chat and a playable chat in the [app shell](APP_SHELL.md): the **Chats** list, the **Start
Chat** screen and one **chat**. Its pages are `static/pages/chats`, `start` and `chat`; its ops are the `play`
adapter (`grimoire.app.adapters.play`), called as `play.<op>` beside the [text API](TEXT_API.md)'s core ops.

The ops write only through 052's `QuickStart` (which composes 107's `ChatLibrary` and 035's `SceneRunner`) and
035's `SceneRunner` itself, with the owner's trusted handle, and keep no record of their own. Every op that
changes a chat answers the **scene** exactly as the core `open` op does, plus `unfinished` (052's reason a launch
or an added card's seat stopped after the chat was written; the core `continue` finishes it).

## Pages

| Page | Address | What it does |
|---|---|---|
| **Chats** (`chats`, Play) | `#/chats`, `?filter=character\|scenario&source=<SourceRef>` | Every chat (as far as one list goes, 1,000), or one character's or scenario's chats, each linked by its exact ref; searched by title, campaign or bound name, sorted by what was played last or by name, and grouped by campaign, by first character or not at all (five rows a group, thirty ungrouped, then the rest on request). The search, grouping and sort are kept for the tab. **New chat** beside the search, **Sync** in the header, and the number of chats beside Chats in the rail |
| **Start Chat** (`start`, Play) | `#/start`, `?source=<SourceRef>` (a character or scenario to start from) | Pick a character, a character with a lorebook, a scenario or a premise; who you play (required); the opening (the default is chosen); an optional name. **Continue** lists the chats the same card or scenario is already in. The persona and an unsent premise are drafts |
| **Chat** (`chat`, hidden) | `#/chat/<ChatRef>` | The page owns the pane: the story in one column and a 320px side panel beside it (over it, with **Close panel**, on a window narrower than 1100px, opened from the scene bar). **Focus** in the header shows only the story and the composer. Top to bottom: bars for what holds play up (a pause with **Resume**, an unfinished start, no seat yet with **Take your seat**, a fork to choose, a sync conflict, unreadable turns, a source that is gone); the scene bar (the cast, the player's own marked "you"); the transcript (posts, kept lines with **Strike**, asked twice, "The table is answering…", a reply that did not arrive with **Try again**, **Pass the turn** and **Check the model**, and the proposed reply); and the composer ("Playing" the persona, the mode, **Ask for a reply**, **Post**, a kept draft). The **proposed reply** is one card for the open turn's takes: ‹ Take N of M › steps through those with a line to read, with **Keep this take**, **Try again**, **Redirect…** (an instruction, then a new reply is asked for where one can be), **Pass the turn**, and **Skip** on each line (and **Redirect** on one line of several). With focus outside a field, ← and → step, Enter keeps, R tries again and P passes. The panel's **Cast** tab has each character with **Ask to speak** (an NPC) and **Add picture**, **Ask the director** (the director alone: no NPC is asked with it) and **Add a character** (a character, lorebook or scenario). Its **Details** tab has the campaign and world, what the chat started from, the turns kept, **Pause** or **Resume**, **Sync**, the campaign hub, what the chat plays from, what is missing of a source that is gone (with **Detach**, asked first) and other notes about the chat. While play is paused nothing new is played, kept or passed, but a line can still be skipped, redirected or struck. A start that stopped before it finished reaches the chat as `?unfinished=<reason>`, with **Finish starting** (the core `continue`) |

Every action is one op and every value from the library is set as text. An action shows only where the app serves
its op (`ctx.has`): with no model set up the chat says so above the composer and offers no reply, rather than an
ask that fails. A post is answered at once only when it made a new open turn; a typed table request is not
answered. **Post** is off while a reply is being written, while a turn is open (keep a take or pass it first) and
while play is held up; the composer's placeholder says which. The page's pure logic (the take on show, the
composer's state, the keys) is `pages/chat/turn.js`.

Not in this slice: a director instruction typed as a post (035 refuses it), conflict resolution beyond keeping one
device's version (Sync, 114), costs (112), and campaigns with a world (113).

## Ops

| Op | Arguments | Result |
|---|---|---|
| `play.sources` | none | `characters`, `scenarios`, `lorebooks`: every definition the library could bind now, by head (`ref` unpinned, `name`, `revision`), sorted by name. A persona is a character. Excluded sources and dropped definitions are not listed; nothing is matched by name. `post_modes`: the composer's modes |
| `play.options` | a **selection**: `kind` (`character`, `scenario`, `bundle` or `premise`), `ref` (the character or scenario), `lorebook` (a bundle's, optional: default the card's own), `premise` (text, for `premise`) | 052's `options`: `sources` (the pinned refs it would bind), `openings` (`id`, `text`, `group_only`), `default_opening`, `lorebook` (the card's own), `related` (052's chats this pick's card or scenario is already in, as `chats` lists them, for **Continue**). Writes nothing |
| `play.start` | a selection, `persona` (a character ref; required, else `no_persona`), `opening` (an id `play.options` offered), `label` (at most 200 characters) | 052's `start`: a new world-less chat, every source pinned, the persona seated with the player, opened with the chosen or default opening. The scene. Each call is a new chat |
| `play.post` | `chat`, `text` (at most 20,000 characters), `mode` (`auto`, the default, also for `null`; `speech` or `action`) | the core `post` with 008's typed intent. A director instruction is not played yet, so `director` is refused `invalid_field`. A typed table request ("Grimoire, skip", "redirect: kinder") runs as that request, on the lines on show, and writes no turn. A line wholly in double quotes is its words said aloud in `speech` as in `auto`; a line 032 cannot read is refused with its reason and a hint on how to post it so it reads (quote the words, or name who a whisper is for). The scene |
| `play.add_card` | `chat`, `ref`, `role` (`character` default, `lorebook` or `scenario`) | 052's `add_card`, pinned. A new character witnessed nothing and hears only what is said from now on, seated with the director. The scene |
| `play.withdraw` | `chat`, `kind` (`skip`, `redirect` or `rephrase`), `lines` (1 to 200 line ids as the scene shows them), `direction` (at most 2,000 characters, for a redirect or rephrase) | 035's `request` on those lines. The scene |
| `play.strike` | `chat`, `line` (a shown line of a kept take) | 035's `strike`: no one hears that line from now on. The scene |
| `play.repairs` | `chat` | `repairs`: every bound source that does not resolve now, each `field`, `ref`, `reason`, `role`, `choices` (`detach`, or none for the launch, the persona and a missing actor: those are mended by importing the card again) |
| `play.detach` | `chat`, `field` and `ref` as `play.repairs` gave them | 052's `detach_broken`: the chat plays on without it; its actor and turns stay. `not_broken` if it resolves now or was detached; the persona is refused `invalid_field` (the chat would take no post again). The scene |

Refusals are the services' own reasons (`no_persona`, `unknown_opening`, `missing_source`, `invalid_selection`,
`not_kept`, `unknown_line`, `turn_changed`...), answered as the core ops answer them; a refused start writes nothing.
