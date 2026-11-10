# Text API (044)

`grimoire.textapi.TextApi` is what a client (the phone page first) calls to play a V2 library. It has JSON ops
over one open library: a JSON object goes in and a JSON object comes out. It is trusted application code acting
for the library's **owner**. It writes only through the services that own each record:

- 107's `ChatLibrary` (chats and navigation)
- 035's `SceneRunner` (turns)
- 052's `QuickStart` (seating and openings)
- 038's `ImageLibrary` (pictures)
- 028's `transfer` and `resolve_conflict` (sync)

It keeps no record of its own. The app that serves it on loopback, with the phone page, is [MOBILE](MOBILE.md).

```python
api = TextApi(store, authority, owner="human:owner", runtime=runtime, policy=policy, voices=voices,
              peers={"desktop": ("127.0.0.1", 8765)})
scene = api.call("continue", {"chat": "campaign:q-c-1#chat/t-c-2"})
media_type, data = api.image(scene["cast"][0]["art"]["slots"][0]["image"])
```

## Who plays

This slice is for one person and one library: the owner plays at the table. `player` other than the owner is
refused (`ValueError`), because a different player would read the table unfiltered and could choose forks. Their
own view comes with 053 and 102. Every handle is issued by the library's durable authority, trusted.

**Control is per device.** Who controls each actor is 003's sovereignty ledger. It lives under `authority/`, which
does not sync ([SYNC](SYNC.md)). A chat that arrived from another device shows `state.seated: false` until
`continue` seats this device's player as the persona and gives every actor nobody controls here its controller (052). Post and play refuse
`not_your_character` until then. Both devices must use the **same owner principal**. The app's `init` will use one
default for this: a different owner would be a different table player on each device.

## Ops

Every ref is passed and returned in its own string form (`ChatRef`, `InstanceRef`, `SourceRef`, `ImageRef`), so a
client hands it back unchanged.

| Op | Arguments | Result |
|---|---|---|
| `library` | none | `device`, `owner`, `player`, `peers` (each `peer`, `address`, `paired`, `last` sync report), `generation` (a model is configured), `ops`, `post_modes` |
| `chats` | `filter` `recent` (default), `character` or `scenario`; `source` (a source ref, required with a filter; a pinned ref is unpinned); `limit` 1 to 1000 (default 50) | `chats`, newest activity first, each `chat`, `label`, `last_activity`, `scope` (`campaign`, `title`, `world`: `null` for a world-less chat), `launch`, `sources` (active bindings: `role`, unpinned `ref`, `name`), `problem`; and `sources`, every character and scenario some chat binds, for the filter picker |
| `open` | `chat` | the **scene** (below) |
| `continue` | `chat`, `opening` (optional greeting id) | 052's `continue_chat`: seats this device's player and opens a chat with no turn yet. The scene, plus `unfinished` |
| `post` | `chat`, `text` (at most 20,000 characters), `mode` (`auto`, `speech` or `action`; below) | the scene |
| `generate` | `chat`, `speakers` (optional actor refs), `director` (bool) | the scene, with the new take proposed |
| `keep` | `chat`, `turn` (the open turn the take was read on), `take`, `acknowledge_unscreened` (bool) | the scene; another turn is refused `turn_changed` |
| `pass` | `chat`, `turn` (the open turn meant) | the scene; another turn is refused `turn_changed` |
| `pause`, `resume` | `chat` | the scene |
| `choose` | `chat`, `turn` (a fork branch's first turn) | the scene: the owner's fork choice (028) |
| `add_art` | `chat`, `actor` (one of the cast), `slot` (default `avatar`; `portrait` or a gallery slot; expression slots are not in this slice), `media` (base64, at most 32 MiB decoded), `expected` (the slot's `etag` as `open` gave it, to replace a picture the campaign already placed) | the scene |
| `sync` | `peer` (a configured peer) | 028's transfer report: `peer`, `status` (`synced`, `incomplete`, `refused`), `reason`, `code` (the reason's first word, such as `unreachable`), `passes`, `conflicts` (`path`, `code`), `copied`, `deleted` |
| `resolve` | `peer`, `path` (a campaign file `open` reports in conflict), `mine` and `theirs` (the conflict's etags exactly as `open` gave them: a version changed since is refused `changed`), `keep`: `mine`, `theirs` (the one other version kept here, or the peer's deletion), or one of `theirs` by etag | 028's resolve report: `peer`, `path`, `status` (`resolved`, `refused`, `incomplete`), `reason`, `code`, `receiver` |

### Chat launch and play (111)

These ops are `grimoire.textapi.chatplay`, registered on the same `TextApi`. They write only through 052's
`QuickStart` and 035's `SceneRunner`. Every op that changes a chat returns the scene, with `unfinished` (052's
reason a launch or an added card's seat stopped after the chat was written; `continue` finishes it).

| Op | Arguments | Result |
|---|---|---|
| `start_sources` | none | `characters`, `scenarios`, `lorebooks`: every definition the library could bind now, by head (`ref` unpinned, `name`, `revision`), sorted by name. A persona is a character. Excluded sources and dropped definitions are not listed; nothing is matched by name |
| `start_options` | a **selection**: `kind` (`character`, `scenario`, `bundle` or `premise`), `ref` (the character or scenario), `lorebook` (a bundle's, optional: default the card's own), `premise` (text, for `premise`) | 052's `options`: `sources` (the pinned refs it would bind), `openings` (`id`, `text`, `group_only`), `default_opening`, `lorebook` (the card's own), `related` (chats already started from it, as `chats` lists them, for **Continue**). Writes nothing |
| `start` | a selection, `persona` (a character ref; required, else `no_persona`), `opening` (an id `start_options` offered), `label` (at most 200 characters) | 052's `start`: a new world-less chat, every source pinned, the persona seated with the player, opened with the chosen or default opening. The scene. Each call is a new chat |
| `add_card` | `chat`, `ref`, `role` (`character` default, `lorebook` or `scenario`) | 052's `add_card`, pinned. A new character witnessed nothing and hears only what is said from now on, seated with the director. The scene |
| `withdraw` | `chat`, `kind` (`skip`, `redirect` or `rephrase`), `lines` (line ids as the scene shows them), `direction` (for a redirect or rephrase) | 035's `request` on those lines. The scene |
| `strike` | `chat`, `line` (a shown line of a kept take) | 035's `strike`: no one hears that line from now on. The scene |
| `repairs` | `chat` | `repairs`: every bound source that does not resolve now, each `field`, `ref`, `reason`, `role`, `choices` (`detach`, or none for the launch and a missing actor) |
| `detach` | `chat`, `field` and `ref` as `repairs` gave them | 052's `detach_broken`: the chat plays on without it; its actor and turns stay. `not_broken` if it resolves now or was detached. The scene |

`post` also takes `mode`: `auto` (default), `speech` or `action` (008's typed intent; `library.post_modes`
lists them). A director instruction is not played yet, so `director` is refused `invalid_field`. A typed table
request ("Grimoire, skip", "redirect: kinder") is run as the same request, on the lines on show.

`TextApi.image(ref)` returns a picture's media type and exact bytes. 038 checks them against the blob's name.

### The scene

`open` and every play op return the same object:

- `chat`, `label`, `scope`, `launch` (`kind`, `ref`, `premise`)
- `persona`: `actor`, `name`
- `cast`: each `actor`, `name`, `role` (`player`, `npc` or `unseated`), `persona`, and `art`. `art` holds `slots`,
  each with `state`:
  - `shown`: with `image`, `media_type`, `size`, `layer`, `crop` and `focus`
  - `hidden`
  - `unavailable`: with `reason` and `note`, such as a card picture not given with the card. It is never
    substituted.

  Every slot carries its campaign-layer placement `etag` (`null`: none yet). `art` also carries `problems`.
- `history`: an imported V1 scene's posts as the table heard them, or `null`
- `turns`: each `turn`, `number`, `origin`, `device`, `post`, `kept` (the kept take's shown lines), `offered` (the
  takes still on offer while the turn is open), `open`, `passed`, `paused`, `set_aside`
- `set_aside`: turns a fork choice set aside, by `turn`, `number` and `device` only. Nobody heard them (035).
- `fork`: the open fork, by `number`, `after`, `state` and `branches`, or `null`
- `state`:
  - `waiting`: why play waits now, any of `paused`, `forked`, `conflicted`, `turns_unreadable`, `interrupted`
  - `seated`
  - `open_turn`
  - `keepable`: the open turn's proposed takes that can be kept
  - `unscreened`: those that need `acknowledge_unscreened`
  - `conflicted_turns`
  - `conflicts`: each `path`, `mine` (this device's version's etag; `null`: deleted here) and `theirs` (the etags of the other versions kept here; empty for the peer's deletion, or when no kept version can be read)
- `problems`: each `code` and `detail`. These are 035's scene problems, broken bindings, the chat's own problems,
  and `conflicts_unknown` when this device's sync records cannot be read.

The scene shows only what the table's player may read. It never shows intentions, briefs, unscreened lore, lines
that are not shown, or set-aside lines. On a 041 stage a take's line is shown only when a player character of
that take (its recorded `players`) was in the line's audience, as 035's table view reads it; a staged take that
recorded no players shows none.

## Refusals

A refusal raises `ApiRefused`. It carries `reason`, a message and `status`, and `to_json()` gives
`{"error", "message"}`. A refusal of 035, 052, 038, 107 or 028 keeps that service's own reason. Nothing is written,
except what the reason's own service documents: a `generate` refused after its running take was written
(SCENES, "Refusals"), for instance.

| Status | Reasons |
|---|---|
| 404 | `no_chat`, `unknown_op`, `missing_image`, `missing_blob`, `no_actor`, `unknown_peer` |
| 400 | `invalid_field`, `invalid_selection`, `too_large`, `unsupported_media`, `empty`, `no_content`, `unknown_opening` |
| 409 | every refusal of state, such as `not_your_character`, `turn_fork`, `turn_conflict`, `paused`, `no_runtime`, `changed` (a compare-and-swap lost, so nothing was written), `not_paired`, `no_conflict`, `art_conflict` (the slot is in conflict: `resolve` first), `blob_corrupt`, `device_identity`, and any other refusal of the store by its class name |

## Sync, forks and conflicts

`sync` runs one 028 transfer with a configured peer at its loopback address. A peer that cannot be reached
returns `incomplete` with code `unreachable`, and the phone plays on offline.

- **Forks.** When both devices played one chat offline, the chat forks on both (`waiting: forked`). Play is
  refused `turn_fork` until the owner `choose`s a branch on either device. The next sync carries the choice.
- **Conflicts.** A turn both devices kept or passed differently, or a picture both devices placed in one slot,
  is a keep-both conflict. Every sync reports it until `resolve` keeps one version, and both devices must be
  reachable for `resolve`.
  - A turn in conflict blocks play in that chat (`turn_conflict`).
  - A picture in conflict blocks only its slot: `add_art` there is refused `art_conflict`, because a third
    version would leave neither device a version to resolve against. Each device shows its own picture.
- **Only campaign files.** `resolve` takes the conflicts `open` reports, which are the chat campaign's files.
  A conflict that `sync` reports elsewhere, such as a source's definition revision, is not resolved here.
- **One peer at a time.** With more than one paired device, `theirs` lists every version kept here. Name the
  one to keep by etag.

## Limits of this slice

- **Single user.** Owner only. Seating is per device (above).
- **Pictures.** Pictures are added as campaign placements on the chat's own actors, never on a source.
  Expression slots, crops and focus are not set here.
- **Loopback only.** 028's transport is loopback and unencrypted. The phone reaches the desktop through a
  forwarded port.
- **Generation needs a model.** Without one configured, `generate` is refused `no_runtime`. Everything else
  works offline.
