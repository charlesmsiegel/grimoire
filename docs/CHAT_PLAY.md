# Chat launch and scene play in the app (111)

Sprint 111 puts Start Chat and a playable chat in the [app shell](APP_SHELL.md): the **Chats** list, the **Start
a chat** screen and one **chat**. Its pages are `static/pages/chats`, `start` and `chat`; its ops are the `play`
adapter (`grimoire.app.adapters.play`), called as `play.<op>` beside the [text API](TEXT_API.md)'s core ops.

The ops write only through 052's `QuickStart` (which composes 107's `ChatLibrary` and 035's `SceneRunner`) and
035's `SceneRunner` itself, with the owner's trusted handle, and keep no record of their own. Every op that
changes a chat answers the **scene** exactly as the core `open` op does, plus `unfinished` (052's reason a launch
or an added card's seat stopped after the chat was written; the core `continue` finishes it).

## Pages

| Page | Address | What it does |
|---|---|---|
| **Chats** (`chats`, Play) | `#/chats`, `?filter=character\|scenario&source=<SourceRef>`, `?filter=pinned` | Every chat (as far as one list goes, 1,000), or one character's or scenario's chats, each linked by its exact ref; searched by title, campaign or bound name, sorted by what was played last or by name, and grouped by campaign, by first character or not at all (five rows a group, thirty ungrouped, then the rest on request). The search, grouping and sort are kept for the tab. **New chat** beside the search, **Sync** in the header, and the number of chats beside Chats in the rail. Where the app keeps pins ([SHELF](SHELF.md)), each row has a **Pin** toggle, pinned chats are lifted into a **Pinned** group at the top, and **Pinned** (`?filter=pinned`) lists them alone |
| **Start a chat** (`start`, the rail's **New chat**) | `#/start`, `?source=<SourceRef>` (a character or scenario to start from) | Three steps beside a summary of the new chat. **Start from** a character, a character with a lorebook, a scenario or a premise; **Choose** it from portrait tiles under a search (12 shown, the ones played most recently first), or write the premise; **You play** one of the library's characters (required). The summary names who meets whom and holds the openings (the default is chosen), an optional name, **Start chat**, and the chats the same card or scenario is already in, to continue instead. The persona and an unsent premise are drafts |
| **Chat** (`chat`, hidden) | `#/chat/<ChatRef>` | The page owns the pane: the story in one column and a 320px side panel beside it (over it, with **Close panel**, on a window narrower than 1100px, opened from the scene bar). **Focus** in the header shows only the story and the composer. Top to bottom: bars for what holds play up (a pause with **Resume**, an unfinished start, no seat yet with **Take your seat**, a fork to choose, a sync conflict, unreadable turns, a source that is gone); the scene bar (the cast, the player's own marked "you", over the chat's backdrop picture when it has one, with **Backdrop** opening the picture picker and **Last time**, which opens the last lines under the bar until **Hide**); the transcript (posts, each with the picture attached to it under its text and **Remove image**, kept lines with **Bookmark** and **Strike**, the second asked twice, "The table is answering…", a reply that did not arrive with **Try again**, **Pass the turn** and **Check the model**, and the proposed reply); one row for what waits on the player for this chat, when the [inbox](INBOX.md) has something open for it (its kind and title, its quick actions or **Decide**, and "+N more waiting" linking to **Needs you**); and the composer ("Playing" the persona, **Attach an image**, the mode, **Ask for a reply**, **Post**, a kept draft). The **proposed reply** is one card for the open turn's takes: ‹ Take N of M › steps through those with a line to read, with **Keep this take**, **Try again**, **Redirect…** (an instruction, then a new reply is asked for where one can be), **Pass the turn**, and **Skip** on each line (and **Redirect** on one line of several). With focus outside a field, ← and → step, Enter keeps, R tries again and P passes. The panel's **Cast** tab has each character with **Ask to speak** (an NPC) and **Add picture**, **Ask the director** (the director alone: no NPC is asked with it) and **Add a character** (a character, lorebook or scenario). Its **Story** tab is a timeline, in the order of the story, of the chat's bookmarks (each with its label and turn, **Rename** and **Remove**, the second asked twice) and of "Last time you played" where this device knows it; an entry brings its turn to the top of the transcript, marks it and is then the highlighted one, and **Bookmark this moment** marks the newest turn with something kept and opens its name field. A chat records no scene changes, so the timeline has no scene entries. Its **Details** tab has the campaign and world, what the chat started from, the turns kept, **Pause** or **Resume**, **Sync**, the campaign hub, what the chat plays from, what is missing of a source that is gone (with **Detach**, asked first) and other notes about the chat. While play is paused nothing new is played, kept or passed, but a line can still be skipped, redirected or struck. A start that stopped before it finished reaches the chat as `?unfinished=<reason>`, with **Finish starting** (the core `continue`). `?turn=<number>` opens the chat scrolled to that turn, marked for a moment (the command palette links a line it found this way) |

Every action is one op and every value from the library is set as text. An action shows only where the app serves
its op (`ctx.has`): with no model set up the chat says so above the composer and offers no reply, rather than an
ask that fails. A post is answered at once only when it made a new open turn; a typed table request is not
answered. **Post** is off while a reply is being written, while a turn is open (keep a take or pass it first) and
while play is held up; the composer's placeholder says which. The page's pure logic (the take on show, the
composer's state, the keys) is `pages/chat/turn.js`; the story's (where a sitting ends, the timeline's entries) is `pages/chat/story.js`.

A backdrop and a picture on a post are the person's own pictures, kept beside the transcript
([MEDIA_SYNC_UI](MEDIA_SYNC_UI.md#beyond-character-slots)): no turn is changed for one, no character sees one and
no model is sent one. An attached image waits in the composer (a thumbnail with **Remove**) until the post is
made; if it cannot be added then, the post stands and the composer says why, with **Try again**. They are
`pages/chat/backdrop.js` and `pages/chat/attach.js`.

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
| `play.story` | `chat`, `since` (optional: a turn id) | `recap`, `last_time`, `scenes`, `bookmarks`, `problems`: see [the story so far](#the-story-so-far). Reads only, and asks no model |
| `play.bookmark_add` | `chat`, `turn` (a turn id on the chat's line), `label` (optional, at most 120 characters), `since` | a new bookmark on that turn. `unknown_turn`, `too_many_bookmarks` (500 a chat). The story |
| `play.bookmark_rename` | `chat`, `bookmark` (its `id`), `label` (`null` or blank clears it), `expected` (its `etag`), `since` | the label set. `unknown_bookmark`; `changed` when the bookmark is not as it was read. The story |
| `play.bookmark_remove` | `chat`, `bookmark`, `expected`, `since` | the bookmark removed. `unknown_bookmark`, `changed`. The story |
| `play.detach` | `chat`, `field` and `ref` as `play.repairs` gave them | 052's `detach_broken`: the chat plays on without it; its actor and turns stay. `not_broken` if it resolves now or was detached; the persona is refused `invalid_field` (the chat would take no post again). The scene |

Refusals are the services' own reasons (`no_persona`, `unknown_opening`, `missing_source`, `invalid_selection`,
`not_kept`, `unknown_line`, `turn_changed`...), answered as the core ops answer them; a refused start writes nothing.

## The story so far

`play.story` answers what the chat page's **Last time** and **Story** tab show. None of it is written by a model.

- **`recap`** (or `null` when there is nothing to quote): `basis` and `lines`, the last four things said, verbatim, each with `speaker`, `text`, `you` (the player's own post), `turn` and `number`. It is read from the same player-facing view of each turn the transcript is drawn from: a post said or done in the open (a private or withheld post is left out), then the kept take's lines. A take nobody kept, a struck or skipped line and anything only the director sees are never in it. It is a quotation, and the page heads it as one ("The last lines from last time"), never as a summary.
- **Where the last sitting ended** is not in the library: a turn records no time, and a pause leaves no history. The client names it in `since`: the id of the newest closed (kept or passed) turn when this device last left the chat. The chat page keeps that per chat in the browser's `localStorage` (`grimoire.chat.left:<ChatRef>`), with a `sessionStorage` flag marking the browser session, so a reload or coming back from another page is the same sitting and a new session starts from where the last one ended. It is per device and does not sync. When `since` is a turn on the chat's line, the recap is the last lines up to and including it (`basis: "sitting"`) and `last_time` is that turn (`turn`, `number`), which the timeline shows as "Last time you played". Otherwise (a first visit, cleared site data, a turn the chat no longer has) `last_time` is `null` and the recap is the last lines before the turn of the newest post (`basis: "latest"`, headed "The latest lines so far").
- **`scenes`** is always empty: a chat records no scene change, name or boundary today, and none is invented.
- **`bookmarks`**: each `id`, `turn`, `label`, `etag`, `number` (the turn's number on the chat's line) and `missing` (the turn is no longer on the line, after a fork choice set it aside, say: it is still listed, last, and can be renamed or removed). In the order of the story.
- **`problems`**: each file of the chat's bookmarks that is not a bookmark of this version (`path`, `code`). It is left as found, never written over, and the page says how many there are.

### Bookmarks

A bookmark is a person's note on a moment of a chat: a turn and an optional label. It is user data of the library and syncs with its chat; it is never transcript, changes no turn file, is heard by no one and is read by no generation.

- **Stored** one record a bookmark at `campaigns/<campaign>/bookmarks/<bookmark_id>.json`, in the scope-record envelope ([STORAGE](STORAGE.md#bookmarks)) with payload schema `grimoire.chat-bookmark/1`: exactly `chat` (the `ChatRef`), `turn` (the turn's id, never its display number), `label` (text or `null`) and `removed` (a bool). `bookmark_id` is `<chat_id>.bm[-<device tag>]-<12 hex>`, drawn fresh on the device that adds it. The pure record and codec are `grimoire.scene.bookmarks`; the service is `grimoire.scenestore.bookmarks.BookmarkLibrary`.
- **A label** is trimmed, on one line, at most 120 characters; blank is none.
- **Written** as one store transaction each, crash-safe as every record write is (`backend/tests/test_scene_bookmarks.py` explores it with the fault harness). An add is create-only. A rename or a removal names the etag it was shown and is refused `changed` on any other. A removal marks the record `removed` (a record of this family is never deleted); no list shows it again.
- **Refused, never dropped:** a file with another schema, other keys, another chat's turn or text that is not JSON is reported in `problems` with a reason and is never read as a bookmark, written over or counted as another chat's.
- **Two devices:** each add is its own file under an id that carries the device's tag, so bookmarks added on both devices offline both survive a sync with no conflict. The same bookmark renamed or removed on both is an ordinary keep-both conflict ([SYNC](SYNC.md#keep-both)): each device keeps showing its own version and the chat's conflict bar names the file until one is kept.
