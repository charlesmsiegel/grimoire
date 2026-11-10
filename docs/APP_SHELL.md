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
| `ctx.notify(text, {bad})` | a notice above the page (`bad` stays until dismissed and is announced) |
| `ctx.styles(file)` | link a style sheet from the page's own folder (inline styles are refused by the page's policy) |
| `ctx.signal` | aborted when the page is left: pass it on, and do nothing after it fires |
| `ctx.ui` | the design primitives below |

The shell moves focus to the page heading after each navigation and restores the scroll position when the person
comes back to an address. Coming back also puts focus in the control they were last in on that page, with the
caret where it was, when that control has a `data-focus-key`: every `field` with a draft has one, and a page may
give one to any other control, as a key that stays the same from one mount to the next (ids do not).

### Design primitives (`ctx.ui`, `static/shell/ui.js`)

`el(tag, attrs, ...children)`, `button(label, onclick, {kind})` (`primary`, `danger`), `link(label, route,
segments, query)`, `sourceLink(ref, name)` (shows the exact ref when the name is missing; a source is never
replaced by another of the same name), `field(label, {draft, multiline, hint})` (labelled, with `setError` tied
to the input for screen readers), `choice(label, options, {value, onchange})`, `section(title, ...)`,
`list(items, {empty})`, `rowText(title, detail)`, `notice(text, {kind})`, `errorBox(error)`, `errorText(error)`,
`unavailable(what, blocker)`, `empty(text)`, `actions(...)` and `confirm(message, {confirmLabel, danger})`, which
names exactly what is about to happen before anything that cannot be undone (and answers no by itself if the
person leaves the page while it is open).

Style with the `g-` classes and the `--g-` tokens of `shell.css` (`--g-fg`, `--g-bg`, `--g-raised`, `--g-muted`,
`--g-line`, `--g-accent`, `--g-bad`, `--g-good`, `--g-focus`, `--g-radius`); the shell switches them for dark mode.

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
page that can open, every chat by its name (from the `chats` op), the themes and the shortcuts. `?` lists the
shortcuts. The theme (system, light or dark) is kept per browser and switches the `--g-` tokens.

## Tests

`backend/tests/test_app_shell.py` proves the registry, the static files and the adapter seam;
`backend/tests/js/*.test.mjs` the shell's pure modules (run with Node when it is installed). A page's own
behaviour is its sprint's to test, through its adapter ops (`AppApi(device.api).call("sources.show", {...})`) and
its module.
