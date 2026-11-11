# The inbox: what waits on the person

**Status: served, with its page.** `inbox.list` (`grimoire.app.adapters.inbox`, an adapter of the
[app shell](APP_SHELL.md)) is the one list of everything open that waits on the person, and **Needs you**
(`static/pages/inbox/`, route `inbox`) is the one place where each is decided. Home, the rail, a chat's tray,
Devices and sync and the campaign hub all read this op through one module (`pages/inbox/feed.js`); none of them
keeps a list of its own.

It stores nothing and writes nothing. Every call gathers the items again from the services that own them, so
there is nothing to sync, migrate or rebuild. Deciding an item is **the existing op, with the exact arguments
the item gives** (etags, fingerprints, the turn meant), so it is the same write, with
the same refusals, as deciding where the thing lives.

## The op

`inbox.list` takes an optional `limit` (1 to 100, default 24: how many of the chats played last are opened) and an
optional `chat` (a `ChatRef`: that chat's own items only, for its tray; no other chat is opened and no sync
conflict is listed). It answers:

| Field | |
|---|---|
| `items` | the open items, in the order to work down them, each with its `order` (0, 1, ...) |
| `count` | how many there are |
| `complete` | false when the list may be missing something: more chats than `limit`, a service that refused, or more than 200 items |
| `problems` | what could not be read, each `{source, code}` with the `chat` or `campaign` it concerns: `chat` (a chat that cannot be opened), `campaign` (its hub refused), `sync` (the conflicts or the devices cannot be read; `conflicts_unknown` names a campaign whose sync records cannot be read in full) |
| `scope` | `library`, or `chat` when `chat` was given |
| `inspected` | `chats` opened, the `limit`, and whether there were `more_chats` or `more_items` |

A service that refuses is named in `problems` and the rest is still listed. A malformed `limit` or `chat` is
refused `invalid_field`, an unknown chat `no_chat`.

## An item

| Field | |
|---|---|
| `id` | stable while the item is open: `fork:<chat>`, `conflict:<path>`, `label:<chat>:<turn>`, `effect:<chat>:<turn>:<number>`, `reply:<chat>:<turn>` |
| `kind`, `label` | `fork` (Fork), `conflict` (Conflict), `action` (Action), `approval` (Your approval), `reply` (Cut-off reply); items are listed in that order, and within a kind by the chat played last |
| `title`, `detail`, `notes` | what it is, in a line; what deciding does; and anything that cannot be done yet, with why |
| `where` | `text` (campaign, chat, turn or devices, as shown), and the `chat`, `campaign` and `devices` it concerns |
| `route` | "open where it happened": a page's `route`, `path` and `query`, and the link's `label` |
| `actions` | the ways to decide it, the first being the usual one |

An action is `{id, label, op, args, confirm, done, kind, quick, otherwise}`: the op to call with exactly those
arguments; the question to ask first (`confirm`: `message`, `label`, `danger`), the same one the op's own screen
asks; what the item reads once decided (`done`); `kind: danger` for a choice that discards; `quick`, whether it
may be offered where the item is not shown in full (a chat's tray); and `otherwise` (`{reason, args}` or `null`):
when the op refuses with that reason, having written nothing, the same op is asked once more with those
arguments. Only a cut-off reply's **Ask again** has one: `generate` refuses `nothing_to_generate` in a scene where
no NPC can speak, and is then asked for the director alone, as the chat page asks. An item with no action still waits: its `notes` say
why nothing can be done yet.

| Kind | Read from | Decided by | Also carries |
|---|---|---|---|
| `fork` | the chat's turns, through the text API's scene runner, as `media.forks` lists them | `media.choose` with the branch's first turn, one action per branch still standing | `fork`: `media.forks`' entry for the chat |
| `conflict` | `media.conflicts` and `media.devices` | `media.resolve` with `keep`, and `mine` and `theirs` as read | `conflict`: `media.conflicts`' entry, every version included |
| `action` | `campaign.hub` as the **table**: an open action whose latest labelling is `proposed` and not yet framed | `campaign.confirm` | `action`: its turn, actor and labelling, with factors and stakes |
| `approval` | `campaign.hub` as the **table**: an effect still waiting on this player's own key | `campaign.endorse_effect` with the effect's `fingerprint` | `effect`: the effect in full, the cast's names and the player |
| `reply` | the open turn as the text API's table view reads it: a take left running, or a newest take that ended with no line | `generate` (only when a model is set up) and `pass` naming the turn | |

- **Which versions of a conflict may be kept** is decided here, once: a conflict is ended with one device at a
  time, and both must be reachable (a device this one cannot dial offers nothing, and says so). With several
  other versions standing, this device's is not offered (the text API keeps it only on the other device). A
  device's deletion waits until every other device's version is resolved. See [MEDIA_SYNC_UI](MEDIA_SYNC_UI.md).
- **A branch is named by its device**: this device, or a paired device whose tag it carries (`devicetag.tag_of`);
  any other is said by its tag. Naming only: the choice is the branch's first turn.
- **Nothing the director alone may read is in an item.** Campaign items are built only from the hub's table
  view, and a turn's text only from the text API's table view. An effect no character of the player's acts in,
  world truth, NPC goals and intentions never appear. The director's own keys and decisions are not inbox items:
  they stay on the hub's director view.
- **`quick`** is never set for a choice that must be read in full first: a conflict's versions, an effect's
  operations, a labelling with factors or stakes.

## What it costs

The op runs under the library's writer lock, like every adapter op. It opens each of the `limit` chats once, and
reads a chat's campaign hub only when that chat's turns show a labelling to confirm or an effect still waiting on
a key. Sync conflicts are listed for the whole library, as `media.conflicts` does. With `chat`, one chat is
opened and nothing else.

## The page and its readers

- **Needs you** (`#/inbox`; `?item=<id>` opens on one item, and `&chat=<ChatRef>` names the chat it waits in, so
  the page reads that chat's own items too when the item is not in the whole list, which looks only at the chats
  played last) lists the items in order under "N things need you".
  Exactly one is expanded: its kind and where, its title, what deciding does, what it holds in full (a conflict's
  versions side by side, an effect's operations and keys, a labelling's factors and stakes), its actions and
  "Open where it happened". The others are rows that expand on a click. A decided item stays on the page, struck
  through, with its result and everything the decision set aside (the versions not kept, the turns a fork choice
  set aside). After every decision the list is read again; a refusal is shown with its reason and the list as it
  now stands. With nothing waiting, a dashed card says so and links back to the chat played last.
- **The rail** shows the count beside Needs you from the start: the shell reads the op once after it connects
  and after leaving the inbox, a chat, Devices and sync or a campaign. A page that reads the whole feed (Home,
  Needs you, Devices and sync) sets the count from its own answer, so the shell does not ask again when one of
  those is the page opening.
- **Home** shows the top item only, as one card that opens the inbox on it, with "and N more"; or "Nothing needs
  you."
- **A chat** shows one row above the composer for its first item (`pages/chat/tray.js`, from `inbox.list` with
  `chat`): the kind, the title, its `quick` actions, or **Decide** linking to the item, and "+N more waiting". A
  fork, a conflict on a turn and a reply that did not arrive are left out of the row: the chat already says each,
  with its ways on, in its own bars and transcript.
- **Devices and sync** shows one card saying how many forks and conflicts wait, linking to the inbox.
- **The campaign hub's table view** links a labelling to confirm and a change to endorse to their items.

## Not listed

- A bound character pinned behind its source's head. The app learns that only by reading a chat's whole hub;
  the hub still shows it under Sources, with the link to the review screen.
- A start that stopped before it finished: only the answer to that start says so, and the chat it opened offers
  **Finish starting**.
- A reply proposed and waiting to be kept, a device that has not taken its seat, and anything waiting on the
  director.

## Tests

`backend/tests/test_app_inbox.py` proves the op over synthetic libraries (nothing waiting, each kind, the order,
the cap, a service that refuses, the conflict rules, and that no director-only text appears) and runs the page
end to end (`tests/js/inbox.e2e.mjs`); `tests/js/inbox.test.mjs` proves the shared model and the chat's tray;
`tests/js/sync_conflicts.mjs` a three-device conflict shown and decided in the inbox.
