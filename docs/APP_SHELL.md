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
                               campaign 113, media 114, shell 108
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

**Home** (`home`) opens on the chat played last, with **Continue chat**, then the three chats played last and
the library's characters, ending in the way to import a card. A library with no chat yet points to a new chat
and to Import instead, and Home says so when no model is set up. A screen this app has not registered is named
as not in this app yet, never linked. Home reads the `chats` op and `sources.catalog`, and shows a placeholder
where neither names a picture.

### Addresses

An address is `#/<route>/<segment>/...?<query>`. These routes are reserved for their owners, so any page can link
to them now; opening one whose page is not in the app yet shows it as not available, naming its sprint:

| Route | Owner | Path / query |
|---|---|---|
| `home`, `setup` | 108 | |
| `chats` | 111 | `?filter=character\|scenario&source=<SourceRef>` |
| `chat` | 111 | `/<ChatRef>` |
| `start` | 111 | `?source=<SourceRef>` |
| `sources` | 109 | `?role=character\|persona\|scenario\|lore\|world` |
| `source` | 109 | `/<SourceRef>` |
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
on it is usable, and setup lists where to go next (Start Chat, Import, a provider), each shown as not available
until its sprint adds it. `GET /shell/pages` lists the ops the app serves now (`ops`), so a page is usable or
not before the library op exists; the core `library` op answers only once a library is open.

The header's **Search** button, or Ctrl+K, Cmd+K or `/` outside a text box, opens the **command palette**: every
page that can open, every chat by its name (from the `chats` op), the themes and the shortcuts. Before anything is
typed it lists every page and command; a typed search shows the best 12 matches. `?` lists the
shortcuts. The theme (system, light or dark) is kept per browser and switches the `--g-` tokens.

## Tests

`backend/tests/test_app_shell.py` proves the registry, the static files and the adapter seam;
`backend/tests/js/*.test.mjs` the shell's pure modules (run with Node when it is installed). A page's own
behaviour is its sprint's to test, through its adapter ops (`AppApi(device.api).call("sources.show", {...})`) and
its module.
