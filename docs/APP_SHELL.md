# The app shell and its page contract (108)

`python -m grimoire.app serve` serves one V2 library and a browser app on loopback ([phone client](MOBILE.md),
[text API](TEXT_API.md)). From Wave 7 the browser app is a **shell** (Sprint 108) that other sprints plug
**pages** and **adapter ops** into. A sprint adds a folder and edits its own module; nobody edits a shared route
table, menu or op list. This page is the frozen handoff contract for Sprints 109–114.

The app needs no build step and no Node: the shell and every page are plain ES modules served by the app's own
server.

## Files

```
backend/src/grimoire/app/
  static/index.html            the shell's host page (108)
  static/shell/                the shell (108): shell.js, router.js, ui.js, api.js, drafts.js, shell.css
  static/pages/<id>/           one page: page.json and its ES module (and, optionally, its own .css, .svg,
                               .png, .webp or .woff2 files; nothing else under static/ is served)
  adapter.py                   the adapter contract: Adapter, AdapterContext
  adapters/<name>.py           one sprint's ops: sources 109, imports 110, play 111, providers 112,
                               campaign 113, media 114, shell 108; inbox, what waits on the person
                               ([INBOX](INBOX.md)); and shelf, tags, pins and saved filters ([SHELF](SHELF.md))
  pages.py                     the page manifest format and its checks
```

## A page

A page is a folder `static/pages/<id>/` with a `page.json` manifest and one ES module:

```json
{"contract": 1, "id": "sources", "route": "sources", "title": "Library", "slot": "library", "order": 10,
 "owner": "109", "entry": "page.js", "requires": ["sources.list"], "unavailable": null}
```

- **id** names its own folder; **route** is the first path segment of its address (lower case, `-` between words).
  Two pages may not share a route.
- **slot** is where the navigation lists it: `home`, `play`, `library`, `campaign`, `media`, `settings`, or
  `hidden` (reached only by a link, such as one chat). **order** sorts pages within a slot (0–1000).
- **requires** names every op the page cannot work without (`open`, or an adapter's `sources.list`). A page whose
  op the app does not serve is listed as unavailable, naming the missing ops, and never opened.
- **unavailable** lets a page say itself that it cannot be used yet, and why ("waits on 046 milestone 2").
- **owner** is the sprint that owns the folder.

The app refuses to start on a bad manifest, a missing entry module, a folder with no manifest or two pages on one
route, and says which.

The module exports `mount`:

```js
export async function mount(root, ctx) {
  ctx.setTitle("Library");
  const answer = await ctx.api("sources.list", { role: "character" });
  root.append(ctx.ui.list(answer.sources.map((source) => ctx.ui.sourceLink(source.ref, source.name))));
  return () => { /* optional: undo anything outside root when the page is left */ };
}
```

`root` is the page's own region; the shell detaches it when the page is left, and from then on `ctx.api` and
`ctx.picture` reject with reason `left` and `setTitle`, `setActions`, `notify` and `navigate` do nothing, so a
late answer never writes into the next page. Build everything from text: never assign library text (card text,
model output) to `innerHTML` or any other markup sink. `ui.el` refuses a `style` attribute.

A page's own style sheet names only classes that start with its id (`chat-post` for the `chat` page): every
sheet stays linked once loaded, so an unprefixed class would restyle other pages.

### What `ctx` gives a page

| | |
|---|---|
| `ctx.route`, `ctx.path`, `ctx.query` | the address: `#/chat/<ref>` is route `chat`, path `[ref]`; `query` is a `URLSearchParams` |
| `ctx.api(op, args)` | call an op; resolves to its JSON answer or rejects with `ctx.ApiError` (`reason`, `message`, `status`; `network`, `bad_response` and `left` are the shell's own reasons) |
| `ctx.act(work)` | run one action: the page's buttons are disabled meanwhile, a second tap does nothing, and a refusal is shown with its reason |
| `ctx.picture(ref)` | an `ImageRef`'s picture as a `blob:` URL (the only image source the page's policy allows) |
| `ctx.library`, `ctx.refreshLibrary()` | the `library` op's answer (device, peers, whether a model is set up, `ops`) |
| `ctx.has(op)` | whether the app serves an op: show an action only when it does |
| `ctx.registered(route)` | whether some page owns a route: link to another sprint's screen only when it does |
| `ctx.href(route, segments, query)` | the address of a route; each segment is one exact string (a ref), URI-encoded |
| `ctx.navigate(hash)` | go to an address |
| `ctx.restart(hash)` | load the app again (its pages, ops and library) and open `hash`: after first run opens a library |
| `ctx.draft(key)` | an unsent draft (`get`, `set`, `clear`), kept per tab until cleared, across navigation and reloads |
| `ctx.setTitle(text)`, `ctx.setActions(...nodes)` | the header's title and its action buttons |
| `ctx.setCrumb(...parts)` | the line above the title saying where the page sits ("Chats / Harbor"): text, or links and text |
| `ctx.layout(name)` | how wide the page is: `reading`, `page`, `wide`, or `full` (the page lays out the whole pane itself, as a chat does); unset, a page gets the default column |
| `ctx.focusMode(on, onchange)` | hide the rail and the header (a chat's story only); the shell shows "Leave focus · Esc" and tells `onchange(on)` when it changes |
| `ctx.scrollTo(y \| "end")` | scroll the page's pane (the pane scrolls, not the window) |
| `ctx.setCount(route, {value, label, bad})` | a count beside a page in the rail, or `null` to clear it |
| `ctx.notify(text, {bad})` | a notice above the page (`bad` stays until dismissed and is announced) |
| `ctx.styles(file)` | link a style sheet from the page's own folder (inline styles are refused by the page's policy) |
| `ctx.signal` | aborted when the page is left: pass it on, and do nothing after it fires |
| `ctx.ui` | the design primitives below |

The shell moves focus to the page heading after each navigation and restores the pane's scroll position when the
person comes back to an address. Coming back also puts focus in the control they were last in on that page, with the
caret where it was, when that control has a `data-focus-key`: every `field` with a draft has one, and a page may
give one to any other control, as a key that stays the same from one mount to the next (ids do not). A control
the person picks while the page is still loading keeps focus.

### Design primitives (`ctx.ui`, `static/shell/ui.js`)

`el(tag, attrs, ...children)`, `button(label, onclick, {kind, size})` (`primary`, `danger`, `dashed` for an "add"
action, `linklike`; `small`, `large`), `buttonLink(label, route, segments, query, {kind, size})` (going somewhere,
where a button does something), `link(label, route, segments, query)`, `sourceLink(ref, name)` (shows the exact ref
when the name is missing; a source is never replaced by another of the same name), `field(label, {draft, multiline,
hint})` (labelled, with `setError` tied to the input for screen readers), `choice(label, options, {value,
onchange})`, `search(label, {oninput})`, `section(title, ...)`, `list(items, {empty})`, `rowText(title, detail)`,
`notice(text, {kind})` (`soft`, `bad`), `errorBox(error)`, `errorText(error)`, `unavailable(what, blocker)`,
`empty(text)`, `actions(...)` and `confirm(message, {confirmLabel, danger})`, which names exactly what is about to
happen before anything that cannot be undone (and answers no by itself if the person leaves the page while it is
open).

The shared pieces of the design: `eyebrow(text, {accent})`, `pill(text, {kind})` (`accent`, `bad`, `good`),
`card({kind}, ...)` (`hero`, `accent`, `dashed`, `flush`), `segments(label, options, {value, onchange})` (a
segmented control; `set(value)` moves the choice without calling back), `selectable(content, {selected, chip})`
(one of a set of cards or chips), `avatar(name, {size, key, picture})`, `avatars(people)`, `portrait(name, {key,
picture})`, `tile(name, href, {key, picture, detail})`, `cover({key, picture, tall}, ...)` and `modal(title,
{onclose})` (a fixed head and foot around a body that scrolls; it closes with its page). A picture is a promise of
an image URL (`ctx.picture(ref)`); until it resolves, and when there is none, the piece shows the name's initial on
a colour that `hue(key)` keeps the same for the same key everywhere.

Style with the `g-` classes and the `--g-` tokens of `shell.css`: colours `--g-fg`, `--g-bg`, `--g-raised` (the
rail and strips), `--g-card`, `--g-muted`, `--g-line`, `--g-accent`, `--g-on-accent`, `--g-accent-soft`, `--g-bad`,
`--g-bad-soft`, `--g-good`, `--g-focus`; type `--g-sans` (the interface), `--g-serif` (story text and titles) and
`--g-mono` (paths, refs and diffs). The shell switches the colours for dark mode. The fonts are named, not shipped:
IBM Plex and Spectral show where a device has them, and its own sans and serif otherwise. Layout classes pages
share: `g-stack`, `g-row`, `g-columns`, `g-tiles`, `g-rows`, `g-table`, `g-hero-title`, `g-heading`, `g-quote`,
`g-prose`, `g-muted`, `g-mono`.

### The rail and the header

The rail lists the registry's pages in four groups made of the slots: **Play** (`home`, `play`), **Library**
(`library`), **Table** (`campaign`) and **System** (`media`, `settings`). The `start` page is the rail's **New
chat** button, not an item. Under the groups, **Jump back in** lists the three chats played last (the `chats` op),
and the foot says whether a model will answer and names this device. A `hidden` page is in no group; the command
palette still lists it when its route takes no path segment (Library setup).

The header shows the page's crumb and title, the page's own actions, **Search** and a button that switches between
the light and dark themes.

The count beside **Needs you** is how many things wait on the person ([INBOX](INBOX.md)): the shell reads
`inbox.list` once after it connects and after the person leaves the inbox, a chat, Devices and sync or a campaign
(unless the page then opening reads the feed itself, as Home does), so the count is there before that page was
ever opened.

**Home** (`home`) opens on the chat played last, with **Continue chat**, then the three chats played last beside
the top item of the inbox (one card that opens **Needs you** on it, or "Nothing needs you."), and
the library's characters, ending in the way to import a card. A library with no chat yet points to a new chat
and to Import instead, and Home says so when no model is set up. A screen this app has not registered is named
as not in this app yet, never linked. Home reads the `chats` op, `sources.catalog` and `inbox.list`, and shows a
placeholder where none names a picture.

### Addresses

An address is `#/<route>/<segment>/...?<query>`. These routes are reserved for their owners, so any page can link
to them now; opening one whose page is not in the app yet shows it as not available, naming its sprint:

| Route | Owner | Path / query |
|---|---|---|
| `home`, `setup` | 108 | |
| `inbox` | 108 | `?item=<item id>` (the item to open on, as `inbox.list` names it) |
| `chats` | 111 | `?filter=character\|scenario&source=<SourceRef>`, `?filter=pinned` |
| `chat` | 111 | `/<ChatRef>?turn=<number>` (open at a turn) |
| `start` | 111 | `?source=<SourceRef>` |
| `sources` | 109 | `?role=character\|persona\|scenario\|lore\|world&source=<source>&shelf=cards\|pinned&tags=<tag>,<tag>&q=<text>` |
| `source` | 109 | `/<SourceRef>?tab=art\|edit\|revisions\|raw` |
| `import` | 110 | |
| `providers` | 112 | |
| `campaign` | 113 | `/<campaign id>` |
| `art` | 114 | `?scope=<scope>&subject=<subject>&limit=<n>` |
| `sync` | 114 | |

A sprint may register other routes of its own (prefix them with its area, such as `source-history`).

## An adapter op

A screen that needs more than the [text API](TEXT_API.md) gets it from an **adapter** over an existing domain
service, never from a second store. Each sprint owns one module of `grimoire.app.adapters` and adds ops there:

```python
from grimoire.app.adapter import Adapter, AdapterContext
from grimoire.core.identity import SourceRef
from grimoire.textapi import field, parse_ref


def _show(ctx: AdapterContext, args) -> dict:
    ref = parse_ref(SourceRef.parse, args, "source")
    ...  # read or write through the owning service, as ctx.handle
    return {"source": str(ref)}


ADAPTER = Adapter("sources", owner="109", ops={"show": _show}, refusals={CardError: "card_refused"})
```

- The op is called as `sources.show`: `POST /api/sources.show` with the serve's token, like a core op, and `library`
  lists it in `ops`.
- It runs through `TextApi.serialized`: under the library's one writer lock, with each service's refusal answered
  as `ApiRefused` exactly as a core op's (`StaleWrite` is `changed`, 409). Raise `ApiRefused(reason, message)` for
  a refusal of the adapter's own. **`refusals`** maps another service's refusal class that the text API does not
  know (a card error, an import refusal, a provider's `CallRefused`) to its reason; a subclass matches its base.
  The text API's own mapping comes first, so a `StaleWrite` is `changed` whatever an adapter declares.
  Any other exception is a bug: the server logs it and answers 500 `internal`.
- An op answers one JSON object (no `NaN`, no Python objects); anything else is a bug, answered 500.
- `ctx` holds the open library (`store`, `authority`), the owner and **`ctx.handle`, the owner's trusted handle**:
  every write goes through a domain service with that handle. Its services are the text API's own (`chats`,
  `runner`, `quick`, `images`, `runtime`); `ctx.core(op, args)` runs a core op in the same call. `ctx.store` and
  `ctx.authority` are there to build the owning domain service over the same open library; an adapter never asks
  the authority for a handle of its own or writes the store directly.
- References travel as their canonical strings and are parsed by their owning backend (`parse_ref(SourceRef.parse,
  ...)`), never looked up by name. A write that replaces a record takes the revision it was shown (an etag) and is
  refused `changed` if the record moved on.
- An adapter may grow into a package (`adapters/sources/`), keeping `ADAPTER` at its top.
- An adapter that only gathers what other adapters' services already answer (the inbox) reaches them as those
  adapters do (`hub_of`, `media_of`), and adds no way to write.
- An adapter imports `grimoire.app.adapter` and domain packages only, never `grimoire.app` itself, its server or
  `appapi`: the registry loads every adapter while `grimoire.app` is still loading, so that would close a cycle.

## First run, search, shortcuts and themes

`serve` with no LIBRARY, where the V2 library location has no library yet (a new folder, or an empty one), serves
the app in **first-run** mode; so does `serve LIBRARY --setup`. A LIBRARY that is named but holds nothing is
otherwise refused, since a mistyped path or an unmounted drive must not become a second library. In first-run
mode only the `setup.*` ops (`status`, `check`, `create`, `open`) are served, and the shell opens on Library setup
(`#/setup`), since every other page needs the library's ops. There the person checks a folder, names this device
and makes a library, or opens one. A place inside, around or beside a V1 library, a relative path or an occupied
folder is refused before anything is written; a folder left by a create that stopped early counts as empty, and
the next create finishes it. One library per serve. A create that fails after the store exists answers
`create_incomplete` (a library is there, without the owner's grants or device name: move it aside). `setup.open`
names an unnamed library's device once and refuses a different name for a named one (`device_differs`). The setup
ops are served alongside an open library too, so Library setup always shows which library is open and the
`serve` command that opens it next time. Once one is open, every page that waited
on it is usable, and setup lists where to go next (Start a chat, Import, a provider), each shown as not available
until its sprint adds it.

Library setup is a `hidden` page: the rail does not list it, the shell opens it by default in first-run mode
(where every item of the rail is shown blocked), and the command palette lists it at any time. It reads as four
steps, **Library folder · First card · Who you play · Begin** (`#/setup?step=card|persona|begin`; the last three
only once a library is open). A new library goes on to the second step. Each of those steps uses the library's own
ops, and is a link to the page that does the same where the app does not serve them: **First card** previews a
PNG card or a card JSON file with `imports.card_preview`, lists every file it would write and writes them only on
`imports.proposal_apply` with the digest shown (a portrait, a separate lorebook and a scenario card stay on Import),
or takes a one-line premise, or a card the library already has; **Who you play** makes a character with
`sources.create` in the source `local:mine` (made the first time with `sources.create_source`), or takes a
character the library already has; **Begin** shows the openings `play.options` answers and starts the chat with
`play.start`, saying so when no model is set up. The picks are drafts until the chat begins. `GET /shell/pages` lists the ops the app serves now (`ops`), so a page is usable or
not before the library op exists; the core `library` op answers only once a library is open.

The header's **Search** button, or Ctrl+K, Cmd+K or `/` outside a text box, opens the **command palette**: every
page that can open, every chat by its name (from the `chats` op), every character by its name (from
`sources.catalog`, with the source it is in; it opens the character's sheet), the themes and the shortcuts, and the
**lines that were said** (below). Before anything is typed it lists every page and command; a typed search shows
the best 12 matches. Equal matches are listed pages, chats, characters, commands; lines come after them, in serif
italic, each with "speaker · chat · turn N", and keep four of the twelve places when there is more of both. The
chat and character lists are read once per palette. A source that fails is named under the box ("Characters could
not be searched: ...") and the others' results stay. `?` lists the shortcuts. The theme (system, light or dark) is
kept per browser and switches the `--g-` tokens.

### Search over what was said (`shell.search_lines`)

`shell.search_lines {query, limit}` finds lines of the library's chats by their words. It is plain text matching:
no model is called and nothing is embedded. Case and accents do not count (NFKD, combining marks U+0300 to U+036F
dropped, lower case: the same folding the palette ranks names with), and every word of the query must be in the
one line, anywhere in it.

- **What can be found** is what opening the chat shows of its played turns, read through the same code: 035's
  `SceneLibrary.turns` and the text API's `turn_view`. That is each turn's post and the shown lines of the take
  that was kept. A take on offer or not kept, a withdrawn or struck line, the direction given with a redirect, a
  turn a fork choice set aside and a staged line the table did not hear are in no view and in no result. A chat's
  imported history (its pages from before turn 1) is not searched.
- **`query`** is text of 2 to 200 characters (counted folded, without spaces) in at most 8 words. **`limit`** is 1
  to 50 lines (20 when left out). Anything else is refused `invalid_field`.
- **The answer** is `{query, lines, complete, stopped, more, chats, searched, unread}`. Each of `lines` is
  `{chat, title, turn, turn_id, kind, speaker, text}`: the chat's ref and its title (its label, else the name of
  what it was started from, else its ref), the turn's number and id, `post` or `line`, who said it (the name of
  the character that posted, or the line's speaker; `null` is the narrator) and the text, cut to about 160
  characters around the first match with `…` at a cut end. Chats come in the `chats` op's order (the most recent first) and, in a
  chat, from the latest turn first, at most 3 from one chat. `more` is true when there were more matches than
  were answered.
- **Caps.** One search looks at the 2000 most recent chats, and reads chats it does not hold yet for 1.5 seconds
  (at least 8 of them, so asking again always gets further). `complete` is false when a cap stopped it, and
  `stopped` says which: `time` (`unread` chats were not reached; the next search goes on from there), `chats`
  (the library has more than 2000) or `unreadable` (a chat's turns could not be read). The chats it did not reach
  were not searched.
- **The index** is a derivative, kept in memory only, per open library: each chat's lines with a stamp of its
  turn files (each file's name, size, modification time and inode). It is never written to disk, never synced
  and never canonical; it is gone when the app stops and is rebuilt as searches ask for it. A chat whose stamp
  differs is read again before it is searched. Before a line is answered, its turn file's bytes are checked
  against the content etag the line was read at, and the chat is read again when they differ, so a change the
  stamp missed cannot put a stale line in an answer. It holds at most 32 million characters; past that a chat is
  read, searched and not kept.
- **Cost**, measured on a synthetic library of 301 chats of 31 turns each (9,331 turn files, 7 MB of lines): a
  search that holds every chat takes 0.5 to 0.8 s, nearly all of it the one read of the library's records that
  the `chats` op also makes (1.0 to 1.4 s there); reading the chats the first time takes seven searches of about
  1.5 s each. A chat that changed costs its own turns again (about 25 ms for 31 turns).

The palette asks from 2 characters up, 250 ms after the typing rests, and shows only the answer to what is typed
now. While the answer is `stopped: time` it asks again (up to 12 times) and says how far the search got. Choosing
a line opens `#/chat/<ChatRef>?turn=<number>`: the chat scrolls to that turn and marks it for a moment.

## Tests

`backend/tests/test_app_shell.py` proves the registry, the static files and the adapter seam;
`backend/tests/js/*.test.mjs` the shell's pure modules and the pages' shared ones (run with Node when it is installed). A page's own
behaviour is its sprint's to test, through its adapter ops (`AppApi(device.api).call("sources.show", {...})`) and
its module.
