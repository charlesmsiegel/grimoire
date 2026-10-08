# grimoire — project conventions

FastAPI backend (`backend/`, pytest) + Vite/React frontend (`frontend/`, vitest).
The app and its data live in a markdown/JSON store rooted at `~/.grimoire` by
default. The root is resolved by `store.home()`: `GRIMOIRE_HOME` env var (tests /
overrides) → the user-chosen path recorded in the bootstrap pointer
`~/.grimoire.json` → `~/.grimoire`. The path is editable from the Settings
page (Storage location); point it at a synced folder to share a library across
devices.

Three companion documents, so this file does not have to carry everything:
`CONTRIBUTING.md` (how to run `make check`, and the guard/marker table),
`docs/store-guarantees.md` (what the store promises about atomicity and the
campaign lock, and what it deliberately does not), and `AGENTS.md` (a router
for coding agents, which points back here rather than restating it). What each
of them claims about *this* tree is held to the code by
`backend/tests/test_docs_guard.py`.

## Privacy: real data — and references to it — never get committed

This repo is public. `~/.grimoire` (wherever `store.home()` points) holds
real, private worldbuilding/campaign/character content, kept out of the repo
structurally. That boundary holds in both directions:

- **Never commit anything under the data store itself.** Worlds, campaigns,
  and calendars all live outside the repo by design — that's the point of
  `store.home()`.
- **Never use a real world/campaign/character name as a "concrete example"**
  in a design doc, commit message, or test fixture, even in passing —
  invented names only. This has happened before (real slugs/character names
  leaked into `docs/superpowers/` and got adopted as recurring test
  fixtures) and required a full git-history rewrite to fix. Reuse the
  codebase's existing placeholder names (e.g. Seraphine, Mara, Winifred,
  Realm, Saltmarch) rather than inventing a new one that might coincidentally
  match something real.
- **Never describe the store's *contents*, not even in aggregate.** The
  rule above is about names; this one is about data, and it is the easier of
  the two to talk yourself past. A design doc may not report how many
  characters or cards a library holds, what proportion of them fill in a
  given field, a size distribution (median/p90/max), how many campaigns set
  some setting, what a real `config.md` contains, or whether a given file
  exists in the store — in a spec, a commit message, a code comment, a test
  fixture, or an appendix explaining how to measure any of it. Anonymised
  counts still describe somebody's private library, and once committed they
  are in history. **Qualitative is the acceptable form**: "many cards have no
  `mes_example`" is fine; "N of M cards" is not. Argue from what the code,
  the templates and the shipped defaults do — those are repo facts. Where a
  constant seems to need a measurement to justify it, justify it
  structurally and say it should be tuned against real prompts later. If
  measuring the live store genuinely helps a decision, do it in conversation
  and keep the result out of every committed file.
- **Personal/homebrew calendars follow the same split as any other private
  content**: only `gregorian` and `hebrew` (real-world, real holidays) ship
  in `store/calendars/`; anything else — a fictional calendar, a custom
  holiday set — is a plugin loaded from `<GRIMOIRE_HOME>/calendars/` (see
  `store/calendars/plugins.py`), never committed.

## Frontend: the list/detail page pattern

**Build every record-list page with this pattern.** A page that manages a list of
records (greetings, lore, locations, characters, …) is a two-pane editor with a
read-only detail view and an explicit edit step. Canonical implementations:
`components/GreetingEditor.tsx` and `components/EntityEditor.tsx`.

**The rail navigates the app. A column indexes the page.** `components/AppRail.tsx`
is the app's navigation: two tiers (the app, and the campaign that is open) in
chrome that outlives every route, because *which page of the app am I on* has
the same answer everywhere and should be asked once. A page's 274px context
column (`components/PageShell.tsx`) answers the other half — *which of this
page's records am I reading* — which only that page can ask. A page that builds
a second surface to answer the rail's question has misread the rail; a page
that puts its records in the rail has misread its column.

Rows come from the tables in `src/shell/rail.ts`, and **a row whose `to()`
returns `null` is not rendered at all** — which is what lets a row ship
complete in shape and sparse in fact (Sheets is absent where no mechanics
module is bound; Wrap-up is absent where nothing is pending). Badge counts come
from `GET /api/shell` in one read; a count nobody can answer cheaply is `null`
and draws no tail, which is the cost rule ("a price nobody reported is never
rendered as zero") one domain over.

**Neither Costs row draws a tail.** The app tier's goes to a global monthly
report, which one campaign's figure would mislabel, and a tail on the campaign
tier's would have to pick one of three columns that may never be added. The
campaign's money still rides the shell read: `GET /api/shell` answers
`campaign.money`, the ledger's all-time figure rather than a bounded window
wearing its name, and the campaign hub's card draws it as three labelled
columns (`MoneyColumns`). `store/usage.py`'s `lifetime_since` reserves the
all-history scan for the all-time view and not the play path, so the shell
reads a maintained aggregate instead — `store/usage_rollup.py`, a byte
bookmark into each month file, so the read costs what has been played since the
last navigation rather than the library's age — and an aggregate it could not
bring up to date is `partial`, which the card says could not be totalled
rather than drawing `$0.00`.

**Where a record rail goes depends on whether the page owns the screen.** An
editor that sits *inside* another page — a library section, a world tab, a
campaign panel — brings its own `.editor-list` rail, as below. A page that IS
the screen (the ledger, `routes/SheetsView.tsx`) puts its records in
`ColumnSection`s in the context column and the detail in main. Building an
`.editor` rail beside that column is still a misread of the page — the modes,
the read-only-by-default rule and the explicit edit step are the same either
way.

**Structure** — `.editor` containing:
- `.editor-list` — the rail. A `+ New …` button plus one `.row` button per record
  (its name). The rail is `position: sticky` and `overflow-y: auto`, so it scrolls
  **independently** of the page.
- `.editor-body` — shows either the read-only view or the form.

**Modes** — a `mode: "view" | "edit"` state:
- Clicking a row loads the record and opens it in **`view`** (read-only) — records
  are never editable by default.
- `+ New …` opens the **form** directly (`edit`, with no record selected).
- An **Edit** button (top of the sidebar) switches a viewed record into the form.
- Save returns to `view` (re-select the record); Cancel returns to `view`.

**View** — `.detail-view` containing:
- `.detail-main` — an `<h3>` title and `.detail-rendered`, the body rendered with
  `<Markdown remarkPlugins={[remarkGfm]}>` (react-markdown). Localized images are
  markdown, so they render here.
- `.detail-sidebar` — an `<aside>` holding the **Edit** button (in `.form-actions`,
  full-width) and the record's metadata in `.side-section` blocks (`<h4>` label +
  chips/hints). Metadata that references **other records** renders as clickable
  `chip` buttons that navigate to that record; plain attributes render as
  non-clickable `<span class="chip on">` or `.field-hint` text.

**Tests** — cover: clicking a row shows the read-only view (rendered body, no
`textarea`) with its sidebar; **Edit** reveals the form; `+ New` opens the form
directly. See `GreetingEditor.test.tsx` / `EntityEditor.test.tsx`.

## Frontend: keyboard bindings go in the registry, never on `window`

`src/shortcuts/` owns every key the app answers, and `no-restricted-syntax` in
`eslint.config.js` fails a `keydown`/`keyup`/`keypress` listener added anywhere
else (`registry.ts` is exempt — it installs the only one). Bind with
`useHotkeys(keys, { modal })`:

- The array is read at dispatch, so it needs no memoization and its `enabled`
  is never a render behind the screen. Mirror the control the binding stands
  for, and carry the condition that control is disabled by — a shortcut that
  reaches past a disabled button is a second copy of the guards.
- `whileTyping` is what survives the caret being in prose, and almost nothing
  should ask for it: a bare letter that did would eat the word being typed.
  `global` survives an open overlay, and only the palette and the shortcuts
  sheet may claim it.
- An overlay passes `modal`, which makes Escape reach it rather than the view
  underneath and holds that view's bindings off while it is up. "On top" is
  whichever modal registered last, and opening re-registers — so it follows
  what the reader sees, not the mount order.
- A binding with a `label` and a `group` is listed by the `?` sheet, which
  reads the registry rather than a hand-kept list. That is the whole
  discoverability story, so label anything a reader could want to find.

## Android (`android/`)

The Android app is a thin Kotlin/WebView shell that packages `backend/src` and
the built frontend **verbatim** (Chaquopy + APK assets) — never copy grimoire
code into `android/`. Rules that keep the platforms in lockstep
(docs/android-architecture.md):

- Backend code must not assume a repo checkout layout or a desktop `~`:
  filesystem access goes through `store.paths`, `prompts.templates_dir()`
  (`GRIMOIRE_TEMPLATES`) and `main.dist_dir()` (`GRIMOIRE_DIST`).
- `pyproject.toml` **base** deps must stay Android-installable (pure python or
  Chaquopy-wheel'd); compiled desktop-only deps go in the `desktop` extra, and
  the pip block in `android/app/build.gradle.kts` mirrors the base list.
- pydantic usage stays v1/v2-agnostic: plain `BaseModel` fields only, dump via
  `routes.common._dump` (no `model_dump()`, `Field`, validators, `ConfigDict`).
- The **manifest and resources are read by `backend/tests/test_android_manifest.py`**,
  which parses every XML under `android/app/src/main` and checks the service's
  `foregroundServiceType` against the declared permissions. `make check-apk` is
  the real build and it is outside `make check` (it needs `android-bootstrap`
  first), so without this nothing in the ordinary gate opens these files at all
  — an XML comment containing `--` is illegal, and one line of prose failed
  `processDebugMainManifest` on CI with nothing local to say so.

Build: `make android-bootstrap` (once per machine — JDK 17, Android SDK,
licenses, `android/local.properties`), then `make apk` (debug) /
`make apk-release`. On Windows make works from PowerShell, cmd, or Git Bash
(recipes are pinned to cmd.exe).

## Development workflow: Codex review gates

**Keep Git history linear.** Integrate feature branches by rebasing onto the target
branch when needed, then fast-forwarding it (`git merge --ff-only`). Never create
merge commits. If the target is already an ancestor, fast-forward directly.

The spec → plan → implementation pipeline (`superpowers:brainstorming` writes
specs to `docs/superpowers/specs/`; `superpowers:writing-plans` writes plans to
`docs/superpowers/plans/`) has mandatory Codex checkpoints — do not advance to
the next stage until the gate passes and its findings are resolved:

- **Spec → planning**: run `/codex:adversarial-review` against the spec before
  starting `superpowers:writing-plans`.
- **Plan → implementation**: run `/codex:adversarial-review` against the plan
  before starting implementation.
- **Implementation → done**: run `/codex:review` against the diff before
  considering the work complete (merge, PR, `finishing-a-development-branch`).
- **Done → actually done**: once implementation is otherwise complete, run
  `/codex:adversarial-review` a final time against the diff *and* the
  originating spec, asking specifically whether the changes implement the
  spec (not just whether the diff is clean code) — gaps, drift, and
  quietly-dropped requirements are the target, not style.

If a gate surfaces findings, address them (or explicitly note why not) before
moving on. Don't skip a gate because a change feels small — ask the user
first if you think one should be skipped.

## Costs: three money columns, and no two of them may be added

The monthly Costs trend has one narrowly scoped projection named **Estimated
total**. It sums the three columns before display rounding to show the estimated
cost of all activity, including modelled-only months. It is a graph reading,
never spend, and never enters budgets or the accounting columns. An unpriced
call makes this projection incomplete and must be labelled as such. All other
money surfaces keep the three sources separate under the rule below.

`store/usage.py` is an append-only ledger of what each LLM call reported, and
every rollup over it carries **three** separate money figures. Adding any two
of them together produces a number that is wrong in a direction nobody can
recover, so the split is the design rather than an artefact of it:

- `cost_usd` — what a provider said it charged. The only figure that is spend,
  and the only one a campaign budget is measured against.
- `estimated_usd` — a price the provider reported as a subscription equivalent
  (`cost_basis: equivalent`, the `claude_agent` path): what the call *would*
  have cost per token. Real usage; not money anybody paid. A billed price on a
  subscription-tagged provider stays `cost_usd`, since the tag is a label and
  `cost_basis` alone decides the column.
- `modelled_usd` — a call whose provider named no price at all, costed against
  the user's rates: the model's own (in its provider's model facts), then the
  per-token table in `pricing.json` (`store/pricing.py`). Arithmetic this side
  did, and the weakest of the three.

`unpriced_calls` counts what none of them covers, which is what makes an
incomplete total say so. The rule that follows from all of it, and the one the
UI exists to keep: **a price nobody reported is never rendered as zero.** Both
the ledger (an absent field, never a `0`) and the three cost surfaces
(`components/cost.tsx`, which every one of them formats through) are built
around that single sentence.

**A row says what served it, and which numbers this side supplied.** Each
row carries `provider_id` (the provider's store id; `provider` stays the
adapter kind), `requested_model` (only when the answer named a dated snapshot),
`operation`, `role`, `preset` and `billing`. Nobody passes them at a call site:
`llm._stamp` copies them off the resolved conn of the attempt that served, a
fallback included, and `usage.Meter.done` files what the holder carries.
`billing` (`metered` or `subscription`) is a label, and only `cost_basis` moves
a figure between columns — **a billed price beats the subscription tag**, stays
`cost_usd` and counts against a budget. A row with no reported price is priced
by `pricing.rate_for_call`, the one precedence function: the model's own rates
(stated on its provider's page and kept in that provider's model facts), then
`pricing.json`. That covers a subscription row with no price too, so it lands
in `modelled_usd`, counted again in `modelled_subscription_calls` rather than
in a fourth figure. A zero rate is a real `$0`, so a model stated free models
to `≈ $0.00` rather than reading unpriced — but a bucket whose only estimate is
that zero, beside calls nobody priced, still headlines "not reported", since a
zero there would pass for a complete total. Rollups price the whole history at
current rates: deleting a provider deletes its facts, so its rows fall back to
the table or read unpriced, and a provider later created under the same slug
prices them at its own rates. Only `modelled_usd` ever moves; no rate touches a
row a provider priced. A true `tokens_estimated` marks counts this side made:
the facade (`llm._resilient`) counts on a worker thread, never the event loop,
only for an attempt whose stream ended on its own, and only the half the
provider left out. An embed call that returned without a prompt count is the
other counter: `inference.embed.estimate_prompt` (`tokens.count_if_loaded`,
which never starts an encoder load) counts it on the caller's thread, which is
a threadpool worker for every caller today -- the model test's probe hands it
to one. `Meter.done` never counts, and a bucket says how many of its
calls rest on such counts in `estimated_token_calls`.

Attribution is per *player post*: a turn's ledger row carries the transcript
index it was answering, so a post and every reroll of it bucket together and
the transcript can say what getting one reply actually cost. The index is only
as stable as indices are — a cut renumbers what follows it and the ledger
cannot follow — so it is a breakdown, and the scene's own totals are the number
that is always right.

**A director note is in the transcript for exactly that reason.** It is what
the player typed to steer a turn rather than to say in it, and it used to be
ephemeral — which made a director turn the one generation in the app whose cost
had no index to sit against. It is stored as a `DIRECTOR_SPEAKER` line now
(`store/scenes/serialize.py`), which is a *synthetic* speaker: assistant-role,
because the transcript format derives the role from the label and `You` is the
only label that means user, and in `SYNTHETIC_SPEAKERS`, so nothing counts it
as a reply, rerolls it, or measures it for drift. It is an instruction rather
than story, so `context.story._project_history` drops it (the prompt is
byte-identical to what it was before notes were stored — the note still reaches
the model exactly once, as the final user message), no export and no absorb
prompt contains it, and the play view hides it behind a per-scene toggle. The
two director turns that store no note — an offscreen scene, and an empty send
meaning "next NPC round" — are still charged to nothing rather than to the last
post the player can see.

## Detached runs: a turn outlives the request that asked for it

A dropped connection used to cancel generation. It no longer does — it drops a
subscriber. **Thirty-two handlers** start detached runs, in five classes:

- `turn` — `post_chat`, `post_retry`, `post_regenerate`, `post_replay_turn`,
  `post_roll_proposal`, and the per-response pair in `character_turns.py`,
  `regenerate_response` (reroll one reply) and `extend_response` (Keep
  writing: continue the last reply as a new variant). All eight synchronous
  streaming handlers are detached now; the eighth, `post_opener`, is a `draft`
  rather than a turn (below).
- `review` — `post_absorb`, `post_audit` and `post_dossiers`. These are not
  streams: each answers **202** with a run to poll and persists its result to
  `store/pending_reviews.py`, because a review's value is a payload nobody has
  written down and losing it costs the longest generation in the app.
- `draft` — `post_opener`, plus the fifteen computing previews: scene
  suggestions and intent, the five image-description drafts (a campaign
  library, a world library, a character version, an entity and a PC), both voice
  anchors, taglines, both scenario parses, character-from-passage, the
  model-catalog refresh, and the model test (`post_connection_test`, which
  refuses without `confirm: true` before anything is sent, and sends each probe
  once through `LLMClient.single`). The
  fifteen answer **202** and hand back a result held on the run and reaped;
  `post_opener` streams and buffers its frames like a turn. **A draft declares
  no exclusion key** — it neither holds a scene nor is refused by one, which is
  what stops a tagline preview from being able to refuse a chat, and its result
  is deliberately *not* durable: a sentence nobody has agreed to yet is
  regenerable, and storing it would be a second store to keep consistent.

  All fifteen go through **one contract** rather than fifteen variants —
  `runs.run_draft` on the route side (the two scenario parses reserve through
  `runs.reserve_draft`, which it wraps), `common.draft_completion` for the call
  (the catalog refresh and the model test excepted, which list or probe rather
  than complete, and are the two whose run writes beside the connection), and
  `api.draftRun` in the client (`api.runModelTest` included, which the test
  call dialog reaches only after its preview). `post_opener` is the exception on the
  client side only, where `api.streamDraft` re-attaches by attempt id instead
  of polling.

- `background` — two handlers start a `continuity-reconcile` sweep on
  `("campaign", cid)`, and a campaign has at most one of them live: a second
  start is handed the live run instead of a new one. `put_chronicle` asks for an
  incremental sweep once a commit has completed in its request (a fresh save or
  a journalled resume, never the idempotent replay), and the save's answer does
  not wait on it or depend on it. `post_reconcile` is the explicit full sweep,
  `@computes_only`, answering **202**. Two more start the tracker's
  `tracker-update` on request: `post_tracker_retry` runs one post's update
  again, and `post_tracker_rerun_from` schedules that post's and every tracked
  post's after it. The class's other runs are outside the count: a landed
  turn's rolling summary and scene-break check (below), and the
  `tracker-update` that a turn, `post_start_from_greeting` or `post_first_post`
  schedules after its posts are written.

- `maintenance` — two handlers, `post_images_migrate` and `post_images_gc`
  (`routes/maintenance.py`), start the image store's migration and collector
  from Settings. They are the one class that holds the *store* rather than a
  scene or a campaign, so it carries its own constant key,
  `MAINTENANCE_KEY`, that no other run's key can collide with (a model refresh
  is never refused by it). Both start routes answer **202**, and a start goes
  through `runs.run_maintenance`. Four rules differ from every other class:
  - **The thread outlives a cancel.** The work is a synchronous function run
    through `anyio.to_thread.run_sync` with its await **shielded**, so the run
    keeps its exclusion until the thread returns; cancel is the cooperative flag
    `cancel_requested`, `abandon_on_cancel` is never used, and lifespan shutdown
    sets the flag on every live one before cancelling the task group. The work
    writes its report in its own `finally`.
  - **It pins the store root** at reservation (`run.root`) and builds every path
    from it; a root that changes stops the run, and nothing is deleted after.
  - **It excludes tree operations, both ways.** World and campaign fork and
    delete, bundle export, both manual backups and the scheduled-backup ticker
    hold `runs.maintenance_excluded(app)`; a live run refuses a hold with 409
    `maintenance_running`, and a hold refuses a start with 409 `busy`. A second
    process on this machine is refused by a proclock, and another device by a
    synced heartbeat marker, both as `maintenance_elsewhere`. The holds live in
    the routes and the ticker, never in a store module.
  - **It is neither notifying nor attachable**, and `PUT /config/data-dir`
    refuses while it is live, like any run.

- The run registry lives on **`app.state.runs`**, not at module scope: a
  `TestClient` builds an app per test, and module state would leak runs between
  them. `runner.install` adds the parts that need a running loop.
- Those handlers are `def`, so FastAPI runs them in a threadpool worker. Work is
  handed to the lifespan loop through an `anyio` **`BlockingPortal`** —
  `tg.start_soon` is not thread-safe from a worker. Reserving a run builds its
  handshake events through that portal, so **a route that reserves may not be
  `async def`**: called from the loop thread the portal raises. The two scenario
  parses have to be `async` (an upload, a download), so they reach their
  reservation through `run_in_threadpool`.
- A scene run's subject is **`("scene", cid, identity)`**. The `sid` moves on
  rename and is reissued after a delete, so it cannot name a run that outlives
  its request. The other three subjects — `("campaign", cid)`, `("world", wid)`
  and `("global",)` — need no identity, because nothing renames a campaign or a
  world in place. Each has the same four run routes mounted under it (list,
  poll, stream, cancel); without them a scenario parse or a model refresh would
  be detached and unreachable. **Deleting a campaign or a world forgets its
  runs** (`runs.forget_subject`), because those ids are slugs and a slug is
  reusable: a replacement of the same name created inside the retention window
  would otherwise be handed the dead record's drafts. A forgotten run keeps
  running and stays visible to the internal sweeps — it is only unreachable.
- Every terminal write is **fenced** on that identity under the campaign lock,
  and every route that changes a scene's *shape* is refused with `scene_busy`
  while a turn **or a review** holds it (both classes share one exclusion key
  per scene, so a scene holds at most one of either): rename, delete, message
  edit, cut, retcon, alternate
  promotion, replay begin/accept/cancel, a manual roll or check, every cast
  route (`appear`/`leave` append a transition line), location and datetime (the
  first `set_datetime` **renames** the scene), the review save, both greeting
  routes in `greetings.py`, and a width-crossing create. The check is taken
  **inside** the campaign-lock hold that covers the mutation — checking first
  and locking after leaves room for a send to reserve in between.
  `test_scene_freeze.py` is one case per door, because the guard is applied per
  call site, and the doors an inventory misses are the ones in another module.
- **`PUT /config/data-dir` is refused while any run is live**, anywhere
  (`runs_in_flight`). The frontend registry deliberately lets a turn survive
  navigation, so the player can now reach Settings mid-turn; the root is
  global, so a run in any campaign would be persisted into the wrong tree.
- `store.attempts` is the durable half: whether a send's post is still in the
  transcript, recorded beside the append and cleared inside the rollback. It is
  what lets recovery after the run record expired ask a question that has a
  right answer, instead of matching text.
- **A stored review carries a transcript watermark**, and it is not the commit
  epoch wearing another name. `commits.reserve` is called from exactly one place
  (`PUT /chronicle`), so playing on after a review lands — appending, cutting,
  retconning — advances nothing: the token still passes every check while the
  scene gets marked absorbed with a summary of posts nobody reviewed. The
  watermark is checked when the review is retrieved, when a scoped retry starts,
  and again at save. The epoch guards against another *review* committing; this
  guards against *play continuing*.
- **Cancel is ordered against the terminal persist.** `DELETE .../pending-review`
  flags every run preparing that review *before* deleting the record, both under
  one `campaign_lock(cid)` hold, and the persist checks the flag under that same
  hold — otherwise the delete lands, the runner publishes, and the review the
  player dismissed comes back minutes later. It is addressed by **generation**
  (minted per absorb, carried by its retries), because the stored payload names
  no producer and "the scene's newest run" is as likely to be a chat turn.
- **A landed turn schedules its own follow-ups.** The rolling summary and the
  scene-break check are the `background` class — no exclusion key, no
  notification, their own stores — and they were fired by
  `CampaignView.askAfterPost` once the streaming promise settled, which is
  exactly what a locked phone prevents: the turn lands server-side and nobody
  is left to ask, so the summary falls behind by every backgrounded turn and
  the break prompt never appears. `streaming._fire_follow_up` asks for both
  once the turn's terminal write is done, with a boundary read under the lock
  that write held. Holding no key, neither can refuse a turn with
  `run_in_flight` or freeze a scene; sitting after the outcome and swallowing
  its own failures, a summary that fails is not a failed turn. Every *other*
  transcript write — an edit, a cut, a retcon, a roll, a check, a replay —
  still asks from the client, and that is right: each is a request the player
  is waiting on, so there is a client there by construction.

## Output processing: stored text is raw

`store/regex/` runs find-and-replace rules over transcript text, and the transcript
itself stays what was written: a rule changes what is *shown* or *sent*.

- **Four levels, one order**: connection → global → world → campaign, each a JSON
  rule file. Only the world and campaign files carry `off`, a list of inherited
  rule ids; the global and connection files sit under nothing and `validate_doc`
  refuses one there. `off` is a switch, never an edit — the only way to change an
  inherited rule is to switch it off and add your own. A stale `off` id (its rule
  deleted upstream) is pruned on write, not refused.
- **LLM readers of transcript text go through `store.regex.view.view`** (phase
  `prompt`). `test_regex_prompt_guard.py` fails a reader that never asks for it.
- **Display runs on the server**, scene reads and `display` stream frames
  (`store/regex/stream.py`) alike, so one Python `re` dialect governs the screen,
  the test pane and the prompt. Markdown, HTML, text and EPUB exports use display
  text; only the JSON export stays raw.
- **A stored rewrite is opt-in** (`rewrite_stored`) and happens only as text lands,
  never over an existing transcript. The original is recorded per scene *identity*
  under `<campaign>/rewrites/`; Restore is validated against that record and
  refused as stale if the text has moved past it. A rewrite that would empty the
  text is never stored. The SillyTavern importer refuses what it cannot translate
  rather than guessing.

## Observability: one writer, three views

`store/logs.py` is the only thing in the app that writes a log line, and
`<home>/logs/YYYY-MM.jsonl` is the only file it writes to. `store/errors.py`
(#156) and `store/metrics.py` (#154) are *readers* over it and over the usage
ledger — there is no second store, and adding one would mean the same failure
written twice by two appends that can disagree.

Four rules that are easy to undo by accident:

- **Never attach a second `logging` handler, and never attach one to the root
  logger.** `logs.install()` (from `create_app`) puts one on the `grimoire`
  logger, and that boundary is a privacy rule, not a preference: `httpx` logs
  full request URLs at DEBUG, an OpenAI-compatible endpoint can carry its key
  in one, and this is a file a user may hand to someone else. What it *does*
  carry is campaign and scene ids, and occasionally a character name — that is
  what makes a failure findable, and Settings says so where sharing it is
  decided, rather than leaving the file to look emptier than it is.
- **`logs.record` may not raise and may not re-enter itself.** It runs beside a
  turn and inside exception handlers, and writing a row resolves the store root
  — which reads the bootstrap pointer through `failsoft`, which *logs*. The
  thread-local latch is what makes that terminate.
- **The level floor stops at `error`** (`logs.FLOORS`). The error store is a
  view over ERROR rows, so a floor above them would be a setting that silently
  switches #156 off — which the size backstop and Settings both promise it
  cannot.
- **Instrument LLM failures at `usage.Meter.done`, not at call sites.** Every
  LLM call in the app runs under a meter, so that is the one place that sees
  all of them fail, and the meter's `task` is already the module axis the error
  store aggregates on. Sixteen call sites each remembering to pass a `kind` is
  how half of them stop appearing in the per-kind counts.

Incoming LLM response capture also uses this writer, at Debug level. Adapters
record decoded SSE lines or SDK messages before selecting content and usage
fields. Long payloads are split into numbered parts rather than clipped; the
monthly cap still applies. Request bodies, URLs and headers are never passed
to the sink. Response bodies can include private prose and reasoning, so the
Settings page describes that sharing boundary. See
[`docs/incoming-llm-capture.md`](docs/incoming-llm-capture.md) for reconstruction
and completeness limits.

The two error counts on `/stats` come from two places **on purpose**: a latency
bucket's `errors` counts calls that failed (the usage ledger, the only source
that also knows how many succeeded, so the only one that can give a rate its
denominator), and the `errors` block counts failures recorded anywhere
(including those that were never a call). Reconciling them into one number
would answer neither question.

## Working notes

- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`.
- Nothing creates the store until the first API call that needs it, so the
  installers end by printing where it will land — `python -m grimoire.where`,
  which asks `store.paths` rather than assuming `~/.grimoire` (wrong wherever
  `GRIMOIRE_HOME` or the bootstrap pointer already names somewhere else).
  `scripts/unix/install.sh` and `scripts/windows/install.ps1` also check the
  Python and Node floors up front, and `test_install_scripts.py` holds those
  floors to `requires-python` and `engines.node`. A store this build creates
  is **born at the current model-settings format**: the first write of a
  missing `config.md` stamps the format marker (`inference_keys.born_current`),
  so a fresh install is never migrated and no safety archive is taken of an
  empty library. `GRIMOIRE_INFERENCE_AUTOMIGRATE=0`, which `tests/conftest.py`
  sets, turns off that birth stamp and the background migration together; a
  test that wants either stamps the marker or calls `migrate.ensure` itself.
  So the older play suites run at format 1, and `test_format2_play.py` is the
  turn path (a chat turn and a reroll) at the format a fresh install ships.
- **Model settings moved to a new layout, once, in the background**
  (`store/inference/migrate.py`, started by `main.start` at startup and after a
  data-dir move). Its first write is a full archive named
  `pre-inference-grimoire-<stamp>.zip` (`backups.SAFETY_PREFIX`), and it is the
  one archive retention leaves alone: `GET /backups` lists it beside the
  ordinary series, but `backups.sweep` counts and deletes only the ordinary
  series, so it stays until a person removes it. Until the switch lands the
  new Models settings answer 409 `not_migrated` (play carries on through the
  legacy translation); a store a newer build switched refuses every
  model-settings write with 409 `newer_format`. Backup, marker, resume, busy
  campaigns and older builds are all in `docs/store-guarantees.md`.
- **Run the gate with `make check`** — the same targets `.github/workflows/ci.yml`
  runs, so a CI failure reproduces locally with one command. Individually:
  `make check-py` (pytest under coverage, across four pytest-xdist workers;
  `WORKERS=0` is the serial run), `check-web` (npm ci + typecheck +
  vitest under coverage),
  `check-lint` (ruff), `check-mypy` (mypy), `check-eslint` (eslint),
  `check-templates` (`verify_templates.py`),
  `check-pydantic1` (the whole suite against the Android dependency set —
  pydantic 1.10, no `desktop` extra — in a throwaway venv). `make check-apk`
  is excluded from `check` because it needs `make android-bootstrap` first.
  - In a **worktree**, pass `PY` explicitly: the default points at
    `backend/.venv`, which only the main checkout has — e.g.
    `make check-py PY=~/github/grimoire/backend/.venv/bin/python` on macOS/Linux,
    `make check-py PY=C:/Users/<you>/github/grimoire/backend/.venv/Scripts/python.exe`
    on Windows. The venv's interpreter lives under `bin/` on macOS/Linux and
    `Scripts/` on Windows; every command in this file, in `README.md` and in the
    frozen-campaign README spells out both forms, and `test_install_scripts.py`
    fails on a new one that names only one.
  - `check-py` sets `PYTHONPATH` to this tree's `backend/src` on purpose:
    `backend/.venv` holds an editable install whose `.pth` points at whichever
    checkout created it, so a bare `pytest` inside a worktree silently tests the
    *other* tree's sources.
  - Run vitest **from** `frontend/` — `npx --prefix frontend vitest run` executes
    from the repo root, which skips `frontend/vitest.config.ts` and disables
    `globals`, failing every mock-based test. `make check-web` does this right.
  - **In a frontend test, an `await` means the page has SETTLED**, not that the
    query it named passed. `src/test-setup.ts` wraps React Testing Library's
    `asyncWrapper` so every `findBy*`/`waitFor` returns only once the rendered
    DOM has gone quiet. Without it, the statement after a test's first `await`
    runs against a page still building itself — a control still `disabled` (and
    `fireEvent.click` on one dispatches nothing and reports nothing), a gutter
    not yet rendered, a `<select>` not yet filled. That is invisible on an idle
    machine and reds CI on a shared runner, in a different test every run
    (#351). `settle.test.tsx` is the guarantee, deterministically — it fails
    both with the wrapper gone and with it settling for one unchanged tick
    instead of two, which is the rule that carries the invisible stages.
  - `check-web` runs `npm run test:coverage` (`vitest run --coverage`), which
    writes `frontend/coverage/lcov.info` — gitignored, uploaded by CI as the
    `frontend-coverage` artifact, and the file external readers discover by
    name. `npm test` still runs the suite bare when you only want pass/fail.
    Coverage config lives in `frontend/vite.config.ts` under `test.coverage`;
    the **istanbul** provider and `all: true` are load-bearing there, and the
    comments say why — do not switch to the v8 provider, which reports a file
    no test imports as 100% covered rather than 0%.
  - **Shared test scaffolding goes in `frontend/src/testkit/`**, which that
    coverage config excludes. Two suites drive the campaign play view — the
    play loop (`routes/CampaignView.test.tsx`) and the end-of-scene review
    (`components/review/SceneReview.test.tsx`) — against one set of mocks:
    `testkit/campaignMocks.tsx` holds the `vi.mock` factories and
    `testkit/campaignHarness.tsx` the fixtures, per-test defaults and render
    helpers. Both halves are needed because a `vi.mock` factory is hoisted
    above every import and can close over nothing, so a suite reaches its
    factory through a dynamic `import()` — and a factory module that itself
    imported the mocked `api` would deadlock on the module waiting for it.
    Left in `src/routes/` instead, scaffolding lands in the coverage
    denominator the paragraph above exists to keep honest, and a helper only
    one suite uses belongs in that suite, not in the shared half.
- **The three lint gates are ratcheted, and fixing a finding is a two-step.**
  `check-lint`, `check-mypy` and `check-eslint` run their tool and compare the
  result to `lint-baselines/<tool>.json`, keyed by (file, rule). Landing them
  any other way was not an option: ruff's widened selection, mypy at its
  defaults and typescript-eslint's type-checked rules report ~2400 findings
  against this tree between them, and report-only jobs get ignored until the
  number is unrecoverable. The consequence to remember is that **an
  improvement fails the gate too** — resolve a finding and the recorded count
  is stale, so `make baseline` and commit the smaller file with the fix.
  `scripts/ratchet.py` and `CONTRIBUTING.md` carry the rest.
- **The frozen campaign** (`backend/tests/fixtures/frozen_campaign/`) is a whole
  store tree checked in as a fixture — the only store in the repo that today's
  code did not write, which is the only way to catch a change that breaks
  reading what an *older* version wrote. `home/` is **never regenerated**; its
  value is being old. `snapshot.json` (the expected output of the read-only
  sweep) *is* regenerated deliberately, when a template or render moved on
  purpose and the new text was reviewed — `cd backend && PYTHONPATH=src
  .venv/bin/python -m tests.fixtures.frozen_campaign.sweep` (on Windows:
  `cd backend; $env:PYTHONPATH="src"; .venv\Scripts\python.exe -m
  tests.fixtures.frozen_campaign.sweep`), committed with the change that moved
  it. See that directory's README.
- **Every route test builds a fresh app, and FastAPI's analysis of its routes
  is memoised per test process** (`backend/tests/route_memo.py`, installed by
  `conftest.py`). Building that analysis for every app was most of what a
  route test cost; the memo reuses it, and nothing else -- the app, its
  lifespan, its run registry and its dependency overrides stay per test. It
  leans on one private FastAPI function and switches itself off when that
  function is absent or its signature moves, so a FastAPI upgrade can make the
  suite slower without making it wrong; `test_route_memo.py` says which.
  `GRIMOIRE_TEST_ROUTE_MEMO=0` runs without it.
- **Faking the LLM**: use `backend/tests/llm_fakes.py`, injected at
  `app.dependency_overrides[routes.get_llm]` — never write another inline fake.
  Scripted turns (`FakeOpenRouter`, `FakeOpenRouterComplete`, …) answer by call
  order; a *cassette* (`from_cassette("campaign_flow")`) answers by what the
  request looks like, replaying hand-authored bodies from
  `backend/tests/fixtures/llm/`. A request matching no cassette entry raises
  rather than defaulting, and `test_llm_fakes.py` renders every real prompt
  template to prove the matchers still match — reword a system prompt and it
  fails there rather than silently everywhere else. These bodies are canned, not
  recorded: they prove the code handles a reply, never that a model would send
  one (`evals/run.py --live` is the only thing that answers that).
- Several architecture rules are enforced by tests that parse the package's own
  ASTs: `test_atomic_guard.py` (every store write goes through `store.atomic`),
  `test_overlay_guard.py`, `test_pydantic_guard.py` (v1/v2-agnostic pydantic),
  `test_paths_guard.py` (filesystem access goes through the resolvers),
  `test_lock_order_guard.py` (only `locks.hold_all` holds more than one campaign
  lock) and `test_lock_domain_guard.py` (below). Clear a genuinely-safe call with
  `# atomic-ok: <reason>` / `# overlay-ok: <reason>` / `# pydantic-ok: <reason>`
  / `# paths-ok: <reason>` / `# lock-order-ok: <reason>` /
  `# lock-domain-ok: <reason>` — a marker with no
  reason fails, deliberately, and each guard caps how many exist.
- **Grimoire never edits a fact; the user may.** `store/facts.py` is an
  append-with-lifecycle ledger, and the absorb pass's whole vocabulary for a
  fact that stopped being true is `record`, `retire` and supersession — a pass
  that rewrote a row in place would turn the ledger into `state.md` with extra
  keys and lose the supersession history it exists to keep. That was written
  as "facts are not edited", which was always a rule about the WRITER: a
  mistyped fact is not a fact that stopped being true, and retiring it would
  put a correction into the history as though the fiction had changed. So
  `facts.set_text` and `facts.forget` are the user's, reached only from
  `routes/ledger.py`, and `test_absorb_writer_guard.py` fails if anything
  under `store/absorb/` calls either. That guard resolves import bindings
  rather than comparing spellings — an alias walked past three earlier
  versions of it.
- **Hand edits to the ledger go through `routes/ledger.py`**, which takes the
  campaign lock the absorb save already holds across its whole sequence and
  wraps every write in `store.undo.journalled` so it lands in the journal as
  `manual` and can be reversed from the play view's Changes panel. Two of the
  ledger's seven sections are deliberately not editable: the relationship
  history and the change log record what happened, and editing a log falsifies
  history rather than correcting state.
- **World-info activation is one engine, `store/context/activation.py`.**
  Keys, secondary-key logic, per-entry scan depth, recursion, presence and
  sticky/cooldown are all decided there. Sticky/cooldown are *derived by
  replaying the transcript* rather than stored, so a cut, retcon or reroll
  changes them the way it changes the transcript. Two rules are easy to
  undo. **Reasons never reach the prompt**: the `reason` dicts and the
  `names` map feed the inspector rows and prompt-log captures only, and
  `test_reasons_never_reach_the_prompt` holds that. **A section may carry a
  `shed` hook** (`{"units", "render"}`) that the packer uses to drop entries
  one at a time instead of the whole section. `render(kept)` re-renders from
  bodies already macro-expanded, so shedding never re-draws a `{{random}}`.
  A store that sets none of the activation fields composes byte-identical
  prompts, which `test_lore_golden.py` pins against a golden recorded from the
  pre-engine code. Never regenerate that golden to make a change pass.
- **The voice anchor is a PROMPT input now, not only a judge input.** It
  renders per present character in `voice_anchors.j2` and is also what
  `voice_drift` judges a played scene against — both through
  `voice_anchors.effective()`, so a rule past the cap is enforced against
  neither. The drift check is therefore an approximate second opinion rather
  than a proof: it compares against the character's *current* anchor, which
  may differ from what a turn received (substitution, a packer drop, a layout
  disable, an edit between playing and absorbing, a local template edit). A
  `drift` verdict means "worth looking at". `store/voice_anchors.py`'s
  docstring carries the reasoning, including what was given up to get here.
- **A campaign carries a write token, and one route opts out of it.**
  `store/revision.py` gives each campaign an opaque value that changes whenever
  the app records a write to it — the primitive `POST /advance` compares an
  expected state against and `POST /fork` keys a repeat on (#409). It is
  stamped by default in one place, the activity middleware in `main.py`, so a
  route added tomorrow is covered without anyone remembering. What stamps for
  itself on top of that always has the same reason in a different shape — the
  response line is not the moment. `clock.advance` bumps inside the lock hold
  that covers its commit (a check the token has not moved under is not binding).
  Everything a *detached* run writes bumps where it writes, since the run
  outlives the response the middleware stamps — `routes/scenes._under_review_lock`
  for a review's terminal write, `_rolling_commit`, `_break_commit` and
  `_break_title_commit` for the follow-ups a landed turn schedules (#397), and `routes/streaming._turn_settled`
  at each of a turn's terminal points, which is not the same thing as "a post
  landed": a closed roll fence writes a proposal and no post at all. The
  continuity sweep's two persists (`reconcile.persist_found` and
  `persist_proposals`) stamp the same way, since the request that started the
  sweep (a 202, or End Scene's save) never waits for them; one that would leave
  the candidate cache as it was stamps nothing. A prompt
  capture (`routes/common._record_prompt`) bumps after its write and under the
  lock that covers it, because the one route reaching it while persisting
  nothing else is the greeting opener, which is `@computes_only` — and a token
  minted before the write is one a reader can hold while that write is still
  landing. A multi-campaign write reached from a
  world or module route stamps every campaign it wrote, inside its `hold_all` —
  nothing under `/api/campaigns/` runs for one, and `module_edit`'s sheet
  migration is the one part of a module edit that writes a campaign's *own*
  file rather than what it inherits. A write answered **non-2xx** stamps for
  itself too, since the middleware only sees success: `proposals.project` is
  reached by a recovery that heals a transcript and then answers 409, and a
  continuity review apply or dismiss that an I/O error stops after one of its
  writes landed answers 500 naming the parts that did (`partial_apply`,
  `partial_dismiss` in `routes/continuity.py`), and a thread, commitment or
  event delete whose continuity clean-up fails after the delete landed answers
  500 `partial_delete` (`routes/ledger.forget_or_partial`). A route
  that mutates a *different*
  campaign than the one in its path says so with `@leaves_campaign_unchanged`
  (`POST /fork`, whose source is never written to) — otherwise a fork would
  invalidate the very expectation the caller took it to protect. What the token
  cannot see, and why a mismatch is a re-price rather than an error, is in that
  module's docstring and in `docs/store-guarantees.md`.
- **Images live in the content-addressed store, never in a record's folder.**
  Every image write goes through the `store.assets` primitives: `put_in` /
  `put_image` ingest the bytes into `<home>/assets/image-store/`, `link_in`
  places an image already there, and either way the record keeps only a
  placement, `image-refs/<name>.json`. Copying, forking or promoting
  art moves placements and writes no bytes; a legacy file not yet migrated
  is still copied as a file, and promoting one ingests it first. A route or
  script that writes a `.png` into a record directory is a regression, and
  the surface roster in `backend/tests/test_image_surfaces.py` fails on it.
  A picture's description and its subject tags live on its object, not in the
  record's folder: a legacy `descriptions.json` / `subjects.json` key is read
  first until migration, and descriptions are global to every world that places
  the picture.
  Legacy files are moved into the store by **migration**, and what nothing
  places any more is deleted by **collection**: both are maintenance runs
  started from Settings, Storage, Image store, never at startup, and neither is
  ever pointed at the frozen campaign's `home/`. Collection fails closed
  (a root it cannot parse, a link, a placement whose object has not arrived,
  anything younger than its grace period) and needs the token of a scan.
  Neither module appears in `store/locks.py`'s domain lists, because the guard
  surveys by `cid` and each takes a root; the migration's campaign writes take
  `campaign_lock` anyway.
  What the store promises is in `docs/store-guarantees.md`; the design is
  `docs/superpowers/specs/2026-10-05-content-addressed-image-store-design.md`.
- **Adding an LLM call site?** Resolve it with
  `require_inference(<task>, cid)` and name the task the call meters under;
  what it returns carries the connection dict `LLMClient` takes as `.conn`
  (read that, until the facade stops needing a connection at all).
  `store/routing.py` maps the task to a route (each one declares its
  `operation`, its `default_role` and what it `requires`), and
  `store/inference/` resolves the route to a role (Primary, Fast, Decision,
  Embedding) or a pinned model. Which settings it reads is decided once per
  resolution by `config.md`'s format marker. A store at format 2 reads the
  roles and their fallbacks, each route's choice, pin and preset, a campaign's
  own overrides of both, and the facts of the chosen model -- `vision`,
  `prefill` and `post_process`, which the lowering lays over the connection
  dict in place of the connection's legacy fields. A store the migration has
  not reached is read through `translate`, the legacy keys seen as that same
  layout, so a call site never asks which one it is on. The fallback rides on
  the resolved conn: the primary's dict carries the fallback attempt, lowered
  (wearing the route's preset when the route has one), under `FALLBACK_KEY`,
  and the facade sends that one. A fallback *known* unable to do what the route needs is reported
  (`fallback_missing`) and never attached, so the facade never sends it -- nor
  one that names the primary's own connection (a retry, which the retry budget
  covers; the decide skip below is the exception), nor one that cannot carry
  the call's images. A reroll's connection override goes through `override_inference`, and
  absorb's secondary phases hand `_soft_inference` a thunk (voice drift hands
  one to `_soft_resolved`, which keeps the whole resolution `decide` takes), so
  a phase that cannot resolve reports itself failed with a reason instead of
  losing the review.
  **A yes/no, pick-one or graded question is a `decide` call**, never prose
  parsed afterwards. Resolve it with `require_inference(<task>, cid,
  operation="decide")` on a route whose `operation` is `decide`, bind the
  operation module as `from .. import inference as operations`, and pass the
  resolution and the app's client to `operations.decide(<task>, items,
  client=…, resolved=…)`, which refuses a resolution for another task or
  operation. It opens one meter per chunk itself, so a caller with a time
  budget hands it over as `around` and the budget runs inside that meter.
  `test_operation_guard.py` finds the call by import binding (a continuity
  `Examination.decide` is not one), fails a task literal that is not on a
  decide route or a call with no `resolved=`, and holds the safety rule both
  ways: a route flips to `operation="decide"` and the Decision role only in the
  change whose call site decides. Prose never rides a decision -- the
  scene-break title is its own `generate`, `scene-break-title` on the
  `summary` route, drafted only once a YES verdict is written, so a read
  between the two writes sees a YES with no title and the inspector shows the
  proposal untitled until its next refresh. Whether an attempt is also sent
  its provider's structured mode is decided per attempt (`STRUCTURED_KEY`,
  present on decide resolutions only); the schema is in the prompt either
  way, and the mode is filed per call on a copied account block, never by
  mutating the resolution's. A provider that refuses the structured field
  with no other attempt to fall to is sent that attempt once more without the
  mode, as its own metered call (`llm.SchemaRefusalError`), and the refusal is
  never a health failure. Until slice H, a decide-only Decision model is
  skipped for a role fallback that can generate -- on its own provider too,
  since the skipped primary is never sent -- and the route row's `problem`
  says so (`resolve.skip_text`); with no such fallback the 409 `incapable`
  stands, and for the speaker pick `post_chat` raises it before it writes
  anything (`refuse_an_unanswerable_pick`). The three decide routes use the
  Decision role, which inherits Fast, so on them a campaign's own Fast
  override no longer outranks a global pin (spec 5.1): a campaign that wants
  that sets its own Decision role. A call site converts only behind `evals/run.py --gate`, whose corpus
  starts from the legacy parser's own tests (`evals/README.md`). The speaker
  pick turns a roster `decide` refuses before sending -- two refs that read as
  one once normalised, or more than 254 refs (with `grimoire`, past a choice's
  255 options) -- into the invalid-handoff issue, so control returns to the
  player rather than the round failing.
  `test_routing_guard.py` fails a call that names no task, a task no route
  claims, an `operation=` that differs from its route's, a reference to
  `require_inference` that is not a call (a task handed through
  `run_in_threadpool` is a literal the guard cannot see), a direct
  `inference.resolve` call in `routes/` without a `# routing-ok:` marker (the
  markers are capped), and a route whose tasks nothing uses. A route names a
  whole connection or model rather than a bare model string, because since
  `llm_connections/` a model name no longer says which provider serves it --
  the same call #144's fallback made. A route that needs a capability declares
  it in `requires`, and the seam refuses a primary that is *known* not to have
  it (a `no`, never an `unknown`) with 409 `incapable`; an image route whose
  adapter says `no` keeps the old `UNSUPPORTED` body instead. A failed test
  call is `unknown` with its error, never `no`, and the name rule's `no` is a
  guess that hides a model in a picker but never refuses one. The Embedding
  role has no seam to refuse at, so the same known `no` for `embed` turns
  embedding off instead: `embed_space.resolve` names no space, no request is
  sent that could only fail, and the Embedding card says why.
- **Adding an embedding call site?** There is one door,
  `store.inference.embed.embed_sync(<task>, texts, space=embed_space.endpoint(),
  client=…, campaign=…)`, and the task must be in `routing.EMBED_TASKS` (a
  route would carry no choice: every embed task resolves through the global
  Embedding role, and the frozen observer enumerates `TASK_ROUTE`). The caller
  resolves the space **once**, from that role (`resolve.embedding`, which
  `resolve.resolve` refuses to stand in for), reads its vector cache under it
  and hands it in, because `embed_sync` never re-resolves: embedding with one
  model and saving under another space's key is how vectors from two models
  end up in one cache. No embed resolution reads a campaign; there is no
  fallback on any axis. The model-test probe is the only other door to the
  embeddings client, and it stays on the provider under test.
  - **Every call that sent a request files one ledger row**, `operation:
    "embed"`, under its embed task, covering all of its batches. It carries the
    caller's campaign where there is one (lore recall, art and continuity do)
    because spend is measured per campaign, and the scene where there is one
    (a turn's lore recall and art, and absorb's identity check), because a
    scene's own totals are the number that is always right and a turn's
    embeds are part of what playing it cost; library-wide semantic search has
    none and is unattributed. A
    call that sends nothing files nothing -- no row, no error, no capture line
    -- whether its input was empty or its deadline lapsed before the first
    request. When the caller's own budget cuts a request that did go out
    (`budgeted=True`: absorb's identity check and the continuity sweep), the
    row is `aborted` and nothing reaches the error store: that is the
    caller's clock, not the provider failing. A deadline that only bounds the
    provider (recall's, search's) cutting it is an error like any other.
  - **A failure is recorded at `Meter.done` like any LLM call, and only its
    kind and HTTP status are recorded** -- never the provider's text, a URL or
    a key, since an error body can echo the input and a redirect's `Location`
    can carry a key. The caller still sees the provider's own words and still
    degrades silently, writing no line of its own. So a failed continuity
    sweep legitimately files **two** error entries: a `continuity-similarity`
    row for the failed request and reconcile's own `continuity-reconcile` row
    for the degraded sweep; a failed absorb identity check likewise files a
    `continuity-similarity` row and a `continuity-identity` record. They answer
    different questions, and a test that counts one filters on `task`. (A
    request the run's own budget cut is not a failed request: its row is
    `aborted`, and only the caller says what running out meant.)
  - **A known `no` turns embedding off rather than refusing**: the adapter, a
    preset's hard `no`, the user's facts or the catalog (never the name rule's
    guess, and never a failed test, which is `unknown`) mean no request is
    sent and every caller degrades as it does when the role is unset. The
    Embedding card's `problem` says why. An edit of a provider known not to
    embed by its adapter, its preset or the user's override asks for no
    `confirm_embedding`; a `no` that only the catalog or the old rev's probe
    verdicts gave does not survive a `rev` restamp, so `moved_by` judges the
    edited provider as it will read after the save, and an edit that leaves
    embedding on is a move that asks.
  - **Cost:** an endpoint that reports no price (a local or
    OpenAI-compatible server) files an **unpriced** row on every recall or art
    turn, so the Costs totals read "incomplete" until the user sets rates for
    that model. An embedding generates nothing, so its row carries a prompt
    count and no completion count, and `usage._completion_count` reads that
    absence as zero output: a rate models the row into `modelled_usd`, and the
    Costs card offers rates rather than saying nobody counted. An embed row
    with no prompt count is still unmetered. An OpenRouter embed reports its
    cost, which is real spend and counts against the campaign's budget.
  - `test_operation_guard.py` (the embed half) holds the task, the door, and
    where each call's `space=` comes from -- traced back through the package
    to `embed_space.endpoint`, so a space built by hand fails it -- and
    `test_usage_guard.py` holds that every embeddings request passes a meter's
    holder.
- **A settings surface never spends unasked, and the server is what holds
  that.** Play is not gated -- sending a turn *is* the request -- but a call a
  settings page starts that may cost money says what it will send and waits
  for a yes, and its route refuses the request without that yes before
  anything is built or sent. Three do today: the model test
  (`post_connection_test`, 400 without `confirm: true`; its `/test/preview`
  sends nothing and is what the dialog shows), a health check that generates
  (`GENERATING_CHECK_KINDS`, the Claude subscription, 400 without `confirm:
  true`; the free checks take none), and a change that moves the Embedding
  role's vector space -- the role changed to a selection that embeds (`PUT
  /inference/settings`), a new key or address on the provider it embeds
  through, which restamps that provider's `rev` (`PUT /llm-connections/{id}`,
  judged by `embed_space.moved_by`), or a model-facts write that turns the
  role on, such as the user's `embed: yes` over a known `no` (`PUT
  /llm-connections/{id}/facts`, judged by `embed_space.facts_moved`), or, on a
  store not yet at format 2, a legacy `embeddings_connection_id` /
  `embeddings_model` change through `PUT /config` (judged by
  `embed_space.config_moved`) -- each
  400 `confirm_embedding` without `confirm_embedding: true`, compared inside
  the hold that writes, because re-embedding a library may cost money. Two
  things can still lift a known `no` without that question, because neither
  is a settings write the user makes to the Embedding role: a test call that
  PASSES (itself confirmed before it is sent; a failed test is `unknown` and
  lifts nothing) and a catalog refresh that starts listing the model's
  embeddings. A confirmation
  that lives only in a client dialog is not the rule: a new settings action
  that can spend adds its refusal at the route.
- **Adding a module that mutates campaign-scoped state?** Classify it in
  `store/locks.py`, or `test_lock_domain_guard.py` fails naming your module. The
  campaign lock domain used to be a docstring list, which is how two mutators
  shipped outside it; it is now `DOMAIN_MODULES` (its public `cid`-taking
  mutators all take `locks.campaign_lock(cid)`), `OUTSIDE_DOMAIN` (deliberately
  not, with the reason) and `UNREVIEWED` (a frozen backlog that may only shrink
  — not open for new entries). A new mutator inside a `DOMAIN_MODULES` module
  must take the lock or carry `# lock-domain-ok: <reason>`. Scene transcripts
  are the artifact this protects: they cannot be regenerated, and `store/scenes`
  serializes its whole mutator surface through `@_serialized` to keep two
  concurrent read-modify-writes from losing one.
- **Needing more than one campaign lock at a time?** There is exactly one way:
  `locks.hold_all(cids)`, which sorts, and `test_lock_order_guard.py` fails any
  other function whose shape can hold two (an acquisition on an `ExitStack`, one
  carried around a loop, two open at once for different campaigns). Two holders once
  acquired every campaign lock in *different* orders — one by recency, one by id
  — and two concurrent requests wedged permanently (#267).
- **Imports in `backend/src/grimoire/` are all at module scope and the module
  graph is acyclic**, enforced by `backend/tests/test_import_guard.py`. Inside
  `store/`, a cross-package import binds a *submodule* and keeps it as a module
  object — `from ..campaigns import read` then `read.world_refs()`, never
  `from ..campaigns import world_refs`, and never `from ..campaigns.read import
  world_refs` either. Two distinct reasons: binding a name off a package that is
  still initializing raises at import time, and binding a function by value off
  a sibling package's leaf module makes the caller cache it, so a test patching
  `campaigns.read.world_refs` silently stops intercepting and goes green while
  injecting nothing. Both leave the file graph acyclic, so the cycle check alone
  would catch neither. The marker is `# import-ok:
  <reason>`, same convention as the guards above — and, given how often this
  refactor caught a stated reason that wasn't actually true, the reason must
  hold up, not merely be present.
- **After editing anything in `templates/`**, `make check` covers both harnesses
  that guard prompts: `scripts/verify_templates.py` (builders and templates agree
  byte-for-byte) and `evals/run.py` (see `evals/README.md`). Offline, the eval
  suite proves the *instructions* are still in the assembled prompt — it
  renders the budget, reply-format, roll-protocol, active-speaker and
  available-art sections and requires each one verbatim in the prompt (so all
  five length knobs, the whole check roster, the nomination the speaker layer
  derived and the art handles it offered are covered),
  plus every key of the absorb contract and owned-lore containment — and that
  the graders still score recorded output correctly. It runs inside
  `pytest backend`.
  Whether the model still *follows* a reworded instruction is a question only
  `evals/run.py --live` answers; that makes real LLM calls and is opt-in.
