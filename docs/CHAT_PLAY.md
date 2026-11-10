# Chat launch and scene play in the app (111)

Sprint 111 puts Start Chat and a playable chat in the [app shell](APP_SHELL.md): the **Chats** list, the **Start
Chat** screen and one **chat**. Its pages are `static/pages/chats`, `start` and `chat`; its ops are the `play`
adapter (`grimoire.app.adapters.play`), called as `play.<op>` beside the [text API](TEXT_API.md)'s core ops.

The ops write only through 052's `QuickStart` (which composes 107's `ChatLibrary` and 035's `SceneRunner`) and
035's `SceneRunner` itself, with the owner's trusted handle, and keep no record of their own. Every op that
changes a chat answers the **scene** exactly as the core `open` op does, plus `unfinished` (052's reason a launch
or an added card's seat stopped after the chat was written; the core `continue` finishes it).

## Ops

| Op | Arguments | Result |
|---|---|---|
| `play.sources` | none | `characters`, `scenarios`, `lorebooks`: every definition the library could bind now, by head (`ref` unpinned, `name`, `revision`), sorted by name. A persona is a character. Excluded sources and dropped definitions are not listed; nothing is matched by name. `post_modes`: the composer's modes |
| `play.options` | a **selection**: `kind` (`character`, `scenario`, `bundle` or `premise`), `ref` (the character or scenario), `lorebook` (a bundle's, optional: default the card's own), `premise` (text, for `premise`) | 052's `options`: `sources` (the pinned refs it would bind), `openings` (`id`, `text`, `group_only`), `default_opening`, `lorebook` (the card's own), `related` (chats already started from it, as `chats` lists them, for **Continue**). Writes nothing |
| `play.start` | a selection, `persona` (a character ref; required, else `no_persona`), `opening` (an id `play.options` offered), `label` (at most 200 characters) | 052's `start`: a new world-less chat, every source pinned, the persona seated with the player, opened with the chosen or default opening. The scene. Each call is a new chat |
| `play.post` | `chat`, `text` (at most 20,000 characters), `mode` (`auto` default, `speech` or `action`) | the core `post` with 008's typed intent. A director instruction is not played yet, so `director` is refused `invalid_field`. A typed table request ("Grimoire, skip", "redirect: kinder") runs as that request, on the lines on show, and writes no turn. The scene |
| `play.add_card` | `chat`, `ref`, `role` (`character` default, `lorebook` or `scenario`) | 052's `add_card`, pinned. A new character witnessed nothing and hears only what is said from now on, seated with the director. The scene |
| `play.withdraw` | `chat`, `kind` (`skip`, `redirect` or `rephrase`), `lines` (1 to 200 line ids as the scene shows them), `direction` (at most 2,000 characters, for a redirect or rephrase) | 035's `request` on those lines. The scene |
| `play.strike` | `chat`, `line` (a shown line of a kept take) | 035's `strike`: no one hears that line from now on. The scene |
| `play.repairs` | `chat` | `repairs`: every bound source that does not resolve now, each `field`, `ref`, `reason`, `role`, `choices` (`detach`, or none for the launch and a missing actor) |
| `play.detach` | `chat`, `field` and `ref` as `play.repairs` gave them | 052's `detach_broken`: the chat plays on without it; its actor and turns stay. `not_broken` if it resolves now or was detached. The scene |

Refusals are the services' own reasons (`no_persona`, `unknown_opening`, `missing_source`, `invalid_selection`,
`not_kept`, `unknown_line`, `turn_changed`...), answered as the core ops answer them; a refused start writes nothing.
