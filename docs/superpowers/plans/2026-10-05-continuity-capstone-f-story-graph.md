# Continuity Capstone — Slice F: Story Graph — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a campaign Story Graph. It is one deterministic, read-only projection of the canonical continuity substrate, served by `GET /campaigns/{cid}/continuity/graph`. A page at `/campaigns/{cid}/graph` draws that projection through four client-side lenses. From a selected node the reader can focus or anchor the next scene (the sending side of the §16.5 handoff), open the node's Ledger entry, or filter the drawing to one arc. Opening the page, and every lens or toggle change after that, costs exactly one graph read and no model or embedding call.

**Architecture:** One new read-only store module, `backend/src/grimoire/store/continuity/graph.py`. It composes readers that already exist and adds no second copy of any record:

- the scene list and its frontmatter histories;
- the chronicle and the appearance roster (through `involvement`);
- `effective` records, links and the live resolver;
- `pressure.build` and `drivers.snapshot(pressure_result=…)`, which give dated items, pressure, and the exact driver and anchor sets the chooser will accept;
- events, scene ideas and relationships;
- D's `pending.findings` for review candidates;
- roster summaries for names.

It runs in three phases. Calendar plugin code runs only outside the campaign lock. Every other campaign file is read inside one `best_effort_campaign_lock` hold. Each source fails soft on its own and is reported in `omitted`.

Two existing readers change so the graph can name actors without card or image reads. `pcs`/`overlay` gain an image-free PC roster, and `birthdays.gather` stops reading a PC's whole card (with its image scans) just to get a name. Without that change, `pressure.build` (and so the graph) scans a portrait directory for every rostered PC.

The frontend adds the following:

- pure modules: `storyGraph/model.ts` (lens presets, the visible set, status words, accessible names, handoff state, Ledger targets) and `storyGraph/layout.ts` (deterministic coordinates);
- one page, `StoryGraphView`, on `PageShell`. Its drawing is absolutely positioned node buttons over one SVG edge layer, in a horizontally scrolling pane. The selected node's detail sits below that pane, outside it.

The rest of the frontend change is small:

- a rail row and an `App.tsx` route;
- a `dismissKey` on `PageShell`, so that picking a record closes the phone column.

**Tech Stack:** Python 3.11, FastAPI, pydantic (v1/v2-agnostic), pytest + `TestClient`; React + TypeScript + react-router, vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-04-continuity-capstone-design.md`. This plan implements §31 Slice F:

- §19 (all of it): §19.1–§19.7;
- §20: `NODE_KINDS`, `EDGE_KINDS`, `EDGE_SOURCES` and `LINK_RELATIONS` in Python, with their TS unions and a backend pin. `DRIVER_KINDS`, `DRIVER_ACTIONS` and `PRESSURE_STATES` already exist from B and E; they are reused and re-pinned here as one §20 set;
- §21's `GET /campaigns/{cid}/continuity/graph`;
- §25.3;
- §26's "dangling or self-collapsing reviewed link … omitted from graph edges" and "calendar unavailable" rows, for the graph;
- §28.8 (all of it), and the Story Graph half of §28.9;
- the sending side of §16.5;
- §12.1's "the Story Graph's 'Open ledger entry'";
- §3.10 and AC14 and AC16, for the graph.

Out of scope: anything that is not the graph. That includes groups and standing facts as nodes (§19.2 "optional"), CampaignHub links to the page, the docs inventory (Slice G) and every receiving side (Slice E owns ScenesView adoption, and Slice D owns Ledger addressing).

**Branching:** each slice is a branch stacked on the previous slice's tip, and the whole stack is rebased onto `main` at landing (the owner's direction). Slices do not gate on each other's reviews. Work on `claude/continuity-capstone-slice-f`, created from the **Slice E tip** (`claude/continuity-capstone-slice-e`). If A–E gain fixes after this branch is cut, rebase onto the new E tip.

**Before Task 1:**

1. Run `git status`. If the tree holds edits that are not this slice's, stop and ask the owner. Never sweep them into a Slice F commit.
2. Record the base gate. From the repo root, run `make check-py PY=$PWD/backend/.venv/bin/python` and `make check-web`. Write each pre-existing failure (test id and a one-line cause) into `.superpowers/sdd/2026-10-05-continuity-capstone-f-story-graph/progress.md`. Task 14 may excuse only failures listed there. `backend/tests/test_atomic.py::test_a_read_only_record_is_not_silently_replaced` fails when run as root, and is environmental.
3. Confirm, on the E tip, that every name this plan consumes exists with the signature its Interfaces block gives. If a name moved, adapt the affected Interfaces block **before** that task starts, and record the move in the ledger.
   - **B:** `pressure.build(cid, now=None, horizon=None, sources=ALL)` and its item keys `{ref, kind, label, native, friendly, fixed, in_days, relation, state, subject, due_text, precision, actor, age}`; `pressure.PRESSURE_STATES`; `drivers.DRIVER_KINDS`; `drivers.snapshot`'s `{now, friendly, fixed, matching, drivers, anchors}`; `effective.records`, `links`, `live_canon`, `Ledgers`, `is_live`, `RELATIONS`; `involvement.of`, `scene_actors`.
   - **D:** `candidates.write`, `candidates.empty`, `candidates.malformed`; `pending.Current.load(cid, ledgers=None)`, `pending.findings(cid, current=None)`, `pending.VISIBLE`, `pending.fingerprint`; `tests/test_continuity_writer_guard.py`'s `SCANNED` and `REQUIRED_PRESENT`. On the frontend: `frontend/src/ledgerPaths.ts` `ledgerHref`, `GROUP_OF`, `LedgerTarget`; `CandidateKind` in `api/types.ts`.
   - **E:** `drivers.DRIVER_ACTIONS`; `drivers.snapshot(cid, offscreen=False, *, pressure_result=None)`; `scene_ideas._project`'s `drivers: [{ref, action}]` and `time_anchor: {ref, relation, native} | None`. On the frontend: `components/pressureControls.ts` `ChooserSeed`, `sanitizeSeed`, `whenPhrase`; `DriverKind`, `DriverAction`, `PressureState` and `AnchorRelation` in `api/types.ts`; `tests/test_suggest_controls.py::test_ts_mirrors_the_python_tuples` and its union-parsing helper.
4. Collect the hand-offs addressed to this slice. Read the A–E slice ledgers (`.superpowers/sdd/2026-10-05-continuity-capstone-{a,b,c,d,e}-*/progress.md`; note a ledger that does not exist as absent). Copy every item that names Slice F, the Story Graph, `graph`, or the sending side of §16.5 into this ledger under `## Inherited hand-offs`, one line each, with its source (file and line) and either an owning task or a recorded reason for deferring it. Seed the section with these four, which the earlier plans already make:
   - **(h1)** D plan, Task 11 and Self-review: `graph` is in `SCANNED` and absent from `REQUIRED_PRESENT` until F lands. Owner: Task 6 (adds `graph` to `REQUIRED_PRESENT`).
   - **(h2)** D plan, Self-review: "Whether the Story Graph's 'Open ledger entry' uses `ledgerHref` is Slice F's call." Owner: Task 13 (it does; Decision 23).
   - **(h3)** E plan, scope and Task 11: the sending side of §16.5 is F's. Owner: Task 13.
   - **(h4)** E plan, Decision 21: the §20 TS unions are pinned by a backend test that parses `api/types.ts`. Owner: Task 8 (F's four new unions get the same pin).

---

## Global Constraints

- **Privacy:** fixtures, docstrings, comments and commit messages use only Seraphine, Mara, Winifred, Realm and Saltmarch, phrases built from them ("Mara's map", "Winifred's chart", "Mara's oath", "The coronation", "Saltmarch Eve", "Saltmarch harbour"), or ids already used as placeholders in the tree (`mara-s-map`, `mara-s-oath`, `the-coronation`). Never read `~/.grimoire`. No committed text describes store contents, counts or distributions. Every layout constant is justified structurally (a touch target, a label width) and marked "to be tuned against real campaigns later".
- **Spec values (verbatim):**
  - Route: `GET /campaigns/{cid}/continuity/graph`. It takes no lens parameter, returns one uncapped payload, has no `truncated` field, and uses `best_effort_campaign_lock` (§19.7).
  - Page: `/campaigns/{cid}/graph`, on `PageShell`.
  - Rail row: `{id: "graph", label: "Story graph", icon: <an unused literal glyph>, to: campaignPath(ctx, "/graph"), match: isUnder(...)}`, placed after the Ledger row in `CAMPAIGN_ROWS`. It has no tail, and `GET /api/shell` gains no field.
  - Column sections, in order: **Lens** (four rows), **Show** (actors, locations, merged records, review candidates), **Arcs** (one row per thread or commitment).
  - URL state: `?lens=`, `?arc=` and `?node=`.
  - Lenses: Story, Cast, Calendar, Continuity. They are client-side presets over one payload, and switching one issues no request.
  - Node kinds (§20): `scene | character | pc | location | thread | commitment | event | idea | birthday | holiday`.
  - Edge sources (§20): `structural | reviewed | candidate | alias`.
  - Edge shape (§20): `{id, kind, from, to, source, relation, candidate_id}`.
  - Kind→prefix (§4, stated once in `canon.KIND_OF_PREFIX`): scene→`scene:`, character→`characters:`, pc→`pcs:`, location→`locations:`, thread→`thread:`, commitment→`commitment:`, event→`event:`, idea→`idea:`, birthday→`birthday:`, holiday→`holiday:`. The frontend never assumes prefix == kind.
  - Dates (§19.2): every dated node carries `native`, `friendly`, `fixed: number | null` and `in_days: number | null`. The payload has a top-level `now: {native, friendly, fixed}`. The frontend positions dated nodes only by `fixed`/`in_days` and **never parses `native`**. A null `fixed` goes in an "Undated" bucket.
  - Handoff state (§16.5), exactly one of `{chooser: {drivers: {[ref]: "focus"}}}` and `{chooser: {anchor: {ref, relation: "on"}}}`, sent by `navigate` to `/campaigns/{cid}/scenes`.
  - Ledger addresses (§12.1): built only with D's `ledgerHref`.
- **Imports** (`test_import_guard`: module scope, acyclic, binding submodules inside `store/`). `graph` imports exactly:
  - `from .. import calendars, chronicle, events, fieldtext, locks, overlay, relationships, scene_ideas`;
  - `from ..campaigns import paths as campaigns_paths`;
  - `from ..scenes import read as scenes_read`;
  - `from . import candidates, canon, doc, drivers, effective, involvement, pending, pressure`.

  `graph` never imports `suggest`, `briefing`, `context`, `timeline`, `chores`, `scene_refs`, `undo`, `review` or `reconcile`. Nothing imports `graph` except `routes/continuity.py` and the frozen sweep. The edges beyond spec §7.0's "the readers above" are listed under "Deliberate deviations".
- **Read-only:** `graph` writes nothing and calls no mutator. D's writer guard scans it (Task 6). It is **not** added to `locks.DOMAIN_MODULES`, `OUTSIDE_DOMAIN` or `UNREVIEWED`: the phantom check fails a declared non-mutator. It takes one `best_effort_campaign_lock(cid)` and never `campaign_lock`, so it never raises `StoreBusy` (§19.7).
- **Plugin code outside the hold:** `calendars.primary_provider`, `pressure.build`, `drivers.snapshot`, `events.list_events(…, provider, …)`, `calendars.fixed_of` and `calendars.friendly` can each run user calendar plugin code. They run only outside the lock hold, as D's reconcile does (§11.1) and as the drivers route does (B Decision 14). Task 7 pins this.
- **Pure read:** no model, embedding or network call anywhere in this slice. That covers the route and every lens or toggle change (§3.10, AC16).
- **Fail soft (§3.9, §26):** each source is read through one helper, `_attempt(omitted, part: str | None, fn, fallback, *args)`. It holds the module's only `except Exception:  # noqa: BLE001 -- <reason>`. A source that raises costs its own nodes and edges, adds its `part` to `omitted`, and never fails the read. **`part=None` softens without recording:** it is used for the per-row `calendars.fixed_of` / `calendars.friendly` calls on a scene, event or idea date, because a free-text date such as `"midsummer"` is ordinary data (`events._UNREADABLE`), not a broken calendar. Only the provider load, `pressure.build`, `drivers.snapshot` and the `events.list_events` fallback record `"calendar"`. Each family builder that `_assemble` calls (`_scene_nodes`, `_actor_parts`, `_record_parts`, `_temporal_parts`, `_idea_parts`, `_review_parts`) also runs inside `_attempt`, under the part its family's source uses (`scenes`, `chronicle`, `plot`, `events`, `scene_ideas`, `candidates`), so a shape no reader foresaw costs only that family. `build` never raises for an existing campaign.
- **Complexity and broad excepts:** `lint-baselines/ruff.json` has no entry for `graph.py`, so one `C901` (max 10) or one unmarked `BLE001` fails `make check-lint`. `build` composes private builders (Interfaces, Task 2), and each stays at complexity ≤ 10. Every backend task runs `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python` from the repo root.
- **Lint ratchet:** `make check-lint`, `check-mypy` and `check-eslint` must not grow `lint-baselines/*.json`. A new frontend file gets zero allowance. The rules that bite are `react/no-array-index-key` (key on node and edge ids), `@typescript-eslint/no-floating-promises` (a `live`-guarded effect with `void`), `jsx-a11y` (nodes are `<button>`s, the Now marker is `role="separator"`), and `react-hooks/exhaustive-deps`. `check-web` runs no eslint, so every frontend task runs `make check-eslint` separately.
- **Keyboard:** bindings go only through `useHotkeys`, with `label` and `group: "STORY GRAPH"`. None uses `whileTyping`, `global` or `modal`. No `keydown` listener goes anywhere else (`eslint.config.js` `no-restricted-syntax`).
- **Copy (verbatim, pinned by tests):**
  - the page title is "Story graph";
  - the column label is "Story graph filters";
  - the section labels are "Lens", "Show" and "Arcs";
  - the lens rows are "Story", "Cast", "Calendar" and "Continuity";
  - the toggles are "Actors", "Locations", "Merged records" and "Review candidates";
  - the actions are "Focus next scene", "Anchor next scene", "Open ledger entry", "Filter to this arc" and "Show the whole graph";
  - the Now marker's accessible name is "Now";
  - the bucket headers are "Undated" (both axes: a non-idea node whose `fixed` is null), "Unscheduled" (the play axis: an idea whose `fixed` is null), "Reached", "Not in a scene" (the play axis's leading column, Task 10) and "People and places" (the calendar axis's trailing column for actors and locations);
  - the omitted note is `Some records could not be read: <PART_LABELS joined by ", ">.`, where `PART_LABELS: Record<GraphPart, string>` is `calendar` → "the calendar", `scenes` → "scenes", `chronicle` → "the chronicle", `plot` → "threads", `commitments` → "commitments", `events` → "events", `continuity` → "reviewed links and merges", `candidates` → "review findings", `relationships` → "relationships", `scene_ideas` → "saved scene ideas", `names` → "names";
  - pressure states are shown through `STATE_WORDS: Record<PressureState, string>` (for example `due_soon` → "due soon"), never raw;
  - finding kinds are shown through `FINDING_PHRASE: Record<CandidateKind, string>`, which is D's `KIND_PHRASES` from `components/continuity/labels.ts`, re-exported under this name by `model.ts` and never redeclared: `possible_duplicate` → "Possible overlap", `possible_relation` → "Possible relation", `possible_thread_closure` → "May be finished", `possible_commitment_resolution` → "Needs resolution review" (§30; confirmed against the base in "Before Task 1" step 3: D exports the table as `KIND_PHRASES`, and D's Task 16 ruling made `possible_relation` read "Possible relation" so that Todo and the Ledger share one table, which the graph now joins);
  - link relations are shown through `RELATION_PHRASE: Record<LinkRelation, { out: string; in: string }>`: `continues` → "Continuation of" / "Continued by", `subthread_of` → "Subthread of" / "Has subthread", `pays_off` → "Pays off" / "Paid off by", `before` → "Before" / "After this:", `on` → "On" / "On this day:", `after` → "After" / "Before this:", `by` → "Due by" / "Deadline for", `related_to` → "Related to" / "Related to";
  - no visible text or accessible name contains a raw ref (`characters:mara`), an underscore token or a scene filename;
  - the empty state is "Nothing to draw yet — play a scene and its threads appear here.", shown only when there are no nodes **and** `omitted` is empty. With no nodes and a non-empty `omitted`, the omitted note renders alone, so a broken scene listing never reads as an empty campaign;
  - the read failure is "The story graph could not be read.", with a "Retry" button.
- **Commands:**
  - backend, from `backend/`: `PYTHONPATH=src .venv/bin/python -m pytest <files> -q -p no:cacheprovider`;
  - frontend, from `frontend/`: `npx vitest run <files>`, and `npx tsc --noEmit -p .`;
  - ratchets and gates, from the repo root: `make <target> PY=$PWD/backend/.venv/bin/python`.

  One command per purpose. A `-k` filter is never combined with a file list it would deselect.

## Review Focus

Five input classes that are most likely to hurt a user, and that the spec's own test list does not reach. Each is pinned by a named test in the task that owns it.

1. **Refs containing `:`, `/` and spaces in the address and in Ledger links.** Model-written plot ids may contain `/` or `:` (D Review Focus 1). Holiday refs carry a free name with spaces (`holiday:<fixed>:Saltmarch Eve`). Refs are split at the **first** `:`.
   - Expected: `?node=` and `?arc=` round-trip through `useSearchParams` and select the right node. "Open ledger entry" on `thread:mara/s:map` addresses row `mara/s:map`, encoded by `ledgerHref`.
   - Pinned in Task 9 (`splitRef keeps everything after the first colon`, `ledgerTarget of an odd thread id`) and Task 12 (`a node id with a colon, slash and space round-trips through ?node=`).
2. **A calendar plugin that raises a non-`CalendarError`** (a hand-written `<home>/calendars/*.py`).
   - Expected: the route answers 200. Scenes, ideas and events are present with `fixed: null` and `in_days: null`. There are no holiday or birthday nodes, and nothing is dated by guesswork. `omitted` contains `"calendar"`. In the drawing, the Calendar lens puts them all under "Undated", and the Story lens puts the unfired events under "Undated" and the ideas under "Unscheduled".
   - Pinned in Task 2 (`test_a_raising_plugin_leaves_scenes_undated`), Task 7 (`test_graph_route_survives_a_raising_plugin`) and Task 10 (`a null fixed goes in the Undated bucket`).
3. **Dangling references**, which hand edits, deletes and slug reuse all produce:
   - a beat in a deleted scene;
   - an idea driver naming a deleted thread;
   - a reviewed link to a deleted event (written by hand);
   - an idea anchored to a birthday occurrence past the 30-day horizon;
   - a feeling whose token has no known prefix;
   - a bond whose `since_scene` was deleted;
   - a candidate naming a merged-away ref;
   - a reviewed link whose endpoint's ledger is unreadable (`effective._classify_links` keeps it, because `Ledgers.exists` answers `None`, not `False`), or whose event was created between phase A and phase B;
   - a hand-edited chronicle `cast` token with no `:` after `canon.actor_ref` (`"Mara"`, `"characters/"`), which `canon.split_ref` raises on.

   Expected: every edge's `from` and `to` name a node in the payload. There are no duplicate node ids or edge ids, and the read does not raise. The idea still carries its stored `time_anchor`.
   - Pinned in Task 3 (`test_malformed_cast_tokens_cost_only_their_edges`), Task 6 (`test_a_link_to_an_unreadable_ledger_draws_nothing`) and Task 7 (`test_every_edge_names_two_nodes`, and the invariant inside `test_a_malformed_optional_file_costs_only_its_part`).
4. **Acting on a node the chooser will not accept:** a closed thread, a fulfilled commitment, a merged-away source, a fired, passed or undated event.
   - Expected: the graph never offers an action the chooser drops with "Not current any more". Focus is disabled on a non-driver and absent on a merged record. Anchor is disabled unless the ref is in `drivers.snapshot(cid)["anchors"]`.
   - Pinned in Task 4 (`test_focusable_is_exactly_the_chooser_driver_set`), Task 5 (`test_anchorable_is_exactly_the_chooser_anchor_set`) and Task 13 (`Focus is disabled on a closed thread and absent on a merged record`, `Anchor is disabled on a fired event`, `the sent state is one sanitizeSeed accepts`).
5. **A campaign switch while the graph read is in flight.** The route is not keyed on `cid`, so the component stays mounted.
   - Expected: the first campaign's late answer is never drawn under the second campaign's address, and each `cid` is read exactly once.
   - Pinned in Task 12 (`a campaign switch drops the superseded read`).

---

## Decisions (and why)

1. **Module, lock and phases.**
   - `graph.build(cid)` lives in `store/continuity/graph.py`, as §7.0 names it.
   - It takes `best_effort_campaign_lock` itself, as `context/assemble.py` does, rather than leaving that to the route. The reason is that it must interleave reads inside the lock with plugin code outside it, and only the store function knows which is which.
   - The three phases:
     - **(A) temporal, outside the hold:** provider, `pressure.build`, `drivers.snapshot(cid, pressure_result=p)`, and events;
     - **(B) campaign files, inside one hold:** scenes and their histories, chronicle, `Ledgers`, records, links, the continuity doc, candidates, relationships and ideas;
     - **(C) names and fixed days, outside:** rosters, plus `calendars.fixed_of` and `calendars.friendly` on scene and idea dates (each per row with `part=None`). Neither may be computed inside `_files`, beside the scene list it is derived from: both run plugin code.
   - The pressure read (A) and the files (B) can be a moment apart under contention. That is the stated trade of every best-effort reader (`docs/store-guarantees.md`). Refusing would turn a read into a new way to fail.
2. **Scene order is the sorted scene ids**, which is `timeline.build`'s rule (`scene_ids`: lexicographic filename order equals play order). It is re-derived here rather than called, because `timeline.build` takes the full `campaign_lock` (raising `StoreBusy`) and would add a second lock acquisition. `list_scenes` is used only as the set of scenes, never for its order (it sorts by `updated`).
3. **Scene dates and places.**
   - `date` is `list_scenes`' `date` (the opening moment, `time_history[0]`), falling back to the chronicle record's `date`. These are timeline's fallbacks.
   - `fixed` is `calendars.fixed_of(provider, date)`, computed outside the hold and softened per scene with `part=None` (an unparseable date is data, not a broken calendar; Decision 16).
   - `occurred_at` edges come from `scenes_read.get_location_history(cid, sid)`. That is one frontmatter-head parse per scene, which never opens a transcript.
   - Adding the history to `_scene_row` was rejected: it would change every `list_scenes` payload (`GET /scenes`, the frozen snapshot) for one reader.
   - `place` is the chronicle record's flat location name. The scene detail shows it when the scene has no location history. It is data, not rendered prose.
4. **"Once per request" (§19.7) is read as a constant number of whole-file reads, independent of the node count.** It does not mean literally one read of each file. Literal "once" would mean threading one `Ledgers` through `pressure`, `drivers`, `involvement` and `effective.records` (whose signatures take none). That is a cross-slice refactor which belongs to Slice G's performance audit (§31 G).
   - What this slice guarantees is no N+1: no per-node card read, no image scan, and no per-thread ledger read.
   - Task 7 pins this structurally. It counts `plot.read` and `chronicle.read_chronicle` calls for a campaign with one thread and for one with five, and requires the two counts to be equal. It also counts card and image reads, which must be zero.
5. **Names come from roster summaries** (§19.7, §25.3):
   - characters: `overlay.character_roster`;
   - PCs: the new `overlay.pc_roster`, because there is no image-free PC listing today (`overlay.list_pcs` scans images per PC);
   - locations: `overlay.list_entities(cid, "locations")`. This is **not** a stat-memoized summary: `entities.list_entities` reads and parses every location file in both layers on every call, and only its token count is memoized (on a cold process that count loads the tokenizer). It is a whole-kind read, constant per request with no N+1, which is what Decision 4 guarantees; it is the same sweep the turn loop pays (`context.world_state._world_info`). Naming only the locations that occur in a history, by parsing frontmatter heads of `overlay.entity_path`-resolved files, would avoid it, but adds a second entity reader for one caller. Kept, recorded under "Deliberate deviations", and handed to G's performance audit.

   The three rosters are read through **three separate** `_attempt(omitted, "names", …)` calls. `entities.list_entities` raises on a record it cannot parse (an invalid-UTF-8 file, say) rather than skipping it, so one bad location file must cost only location labels, not every actor name. A ref missing from its roster is labelled with its bare id.

   **`birthdays.gather` is fixed in this slice.** It read a PC's name through `pcs.read_pc`, which runs `assets.list_images`, `read_focus` and `image_descriptions.read_all` for every version. `pressure.build` reaches it through `_birthday_items`, so the graph would scan every rostered PC's image directories on every read, against §19.7's "no image-directory scans". `pcs.name_of` returns the same `meta.get("name", pid)` and raises where `read_pc` raises, so the birthday line is byte-identical. Task 1 pins this.
6. **Which nodes exist.**
   - **Scenes:** every listed scene.
   - **Actors:** only actors some edge names, meaning appearances, involvement, relationships and birthdays. The whole world roster is not included ("do not dump", §19.2).
   - **Locations:** only locations named in some scene's location history.
   - **Threads and commitments:** every effective record, closed and resolved included, plus every alias source (Decision 7).
   - **Events:** every stored event, with fired, passed and undated ones included. The Calendar lens and node detail show them (§19.4).
   - **Holidays and birthdays:** exactly pressure's occurrence items (Decision 11).
   - **Ideas:** stored ideas whose status is `active`. A used idea became a scene, which is already on the spine, and a dismissed idea is the reader's own "no". Neither is a future-facing obligation (§19.4 places "active saved ideas"). Composed greeting ideas are never stored, so they never appear (§19.2, "stored ideas only").
   - **Groups and standing facts:** not built (§19.2 "optional"). They are recorded as deferred.
7. **Thread and commitment nodes come from `effective.records(cid, kind)`**, keyed by canonical ref. Each carries `aliases: [{ref, title, status}]`.
   - Each alias becomes its own node, carrying `merged_into: <canonical ref>`, plus a `merged_into` edge with `source: "alias"`.
   - The **frontend** hides merged nodes and `merged_into` edges unless "Merged records" is on (§19.3 "shown only when 'Show merged' is enabled"; §28.8 "hidden by default"). The backend does not filter them.
   - A merged node carries the physical record's title and status only. Its pressure is `null`, `focusable` is false, and it has no movement or involvement edges, because the canonical answers for the group.
   - A dangling alias (`live_canon` stopped early) is no alias at all, so its source is an ordinary node (§7.1).
8. **Movement edges** come from each effective record's merged beats (§19.3: "Beats carry no status, so `opened_in`, `advanced_in` and `closed_in` are derived exactly as described"). Edges are unique by `(kind, from, to)`.
   - `opened_in` targets the **first** merged beat's scene, if that scene is listed. A first beat in a deleted scene gives no `opened_in`, and the edge never moves to the next beat: that would claim an opening that did not happen there.
   - `advanced_in` (threads) or `touched_in` (commitments) targets each distinct listed beat scene **other than** the `opened_in` scene. This is the one reading of "each beat's scene" that does not draw two edges to one scene for one beat.
   - `closed_in` (a thread) or `resolved_in` (a commitment) applies when `not effective.is_live(kind, status)`, the predicate's one stated definition, which lowercases. Keying on the literal `"closed"` or on `commitments.RESOLVED` directly would miss a hand-edited `"Closed"` or `"Fulfilled"` (`plot.open_threads` keeps the stored case). It targets the effective `last_scene` when it is listed. It can coexist with an `advanced_in` edge to the same scene, because "moved here" and "finished here" are different statements.
9. **Pressure and the handoff flags come from the chooser's own read.**
   - `drivers.snapshot(cid, pressure_result=p)` (E's keyword) runs once over the same `pressure.build` result the dated nodes use.
   - A thread or commitment node's `pressure` is its driver's `{state, in_days, friendly}`, or `null` when it is not a driver (closed, resolved or merged).
   - `focusable` is `ref ∈ {d.ref for d in drivers if d.kind in ("thread", "commitment")}`.
   - `anchorable` on event, holiday and birthday nodes is `ref ∈ {a.ref for a in anchors}`.
   - Computing these from the same function E's `applySeed` validates against (E Decision 19 reads `GET /continuity/drivers`, which is `drivers.snapshot`) is what makes "Focus" and "Anchor" never send a ref the chooser drops. Mirroring B's `_anchors` rule in TS was rejected as a second copy of the rule.
   - A commitment's dated fields come from its own deadline item, meaning the pressure item whose `subject` is its ref and whose `relation` is not `after` (B Decision 8 makes this at most one). With none, the fields are `""`/`null`.
10. **Event nodes come from `events.list_events(cid, provider, now_fixed)`**, which reuses its `passed` rule, read outside the hold.
    - A raising call falls back to `events.list_events(cid)` (no provider, so every event is undated) and adds `"calendar"` to `omitted`.
    - `fixed` is `calendars.fixed_of(provider, row["date"])`, softened per event with `part=None` (a free-text date such as `"midsummer"` raises `CalendarError`, and `events` treats that as ordinary data), and `in_days` is `fixed - now_fixed`.
    - `status` is the first match of: `fired` (a fire stamp), `passed`, `undated` (`fixed is None`), then `scheduled`.
    - `pressure` is the event's driver reading when one exists (unfired events, and events fired today), else `null`.
11. **Holiday and birthday nodes come only from pressure items.** Their ids are therefore byte-identical to the anchor refs (`notices.holiday_key`, B's birthday ref forms).
    - An occurrence outside the 30-day horizon, or in the past, has no node, and **no edge points at it**. A node is never synthesized by parsing a ref back apart: that would be a second definition of B's ref grammar.
    - An idea anchored to such an occurrence keeps its `time_anchor: {ref, relation, native}` on its own node, so the detail can still say what it was anchored to.
    - **Consequence for §19.6's actor detail.** "An upcoming birthday, if one is recorded" is narrowed to one inside `calendars.UPCOMING_WINDOW_DAYS`. An actor whose recorded birthday falls beyond that window, or is already past this year, has no birthday node, so its detail renders **no** Birthday section at all. It does not say "no birthday", which would be false. A "None in the next N days" line was rejected: N would be a TS copy of `UPCOMING_WINDOW_DAYS`, a second definition of B's horizon. This is recorded against §19.6 at Task 14 Step 7.
    - The invariant "every edge names two nodes" is pinned (Review Focus 3).
12. **Idea edges.**
    - `serves` goes from the idea to each stored driver ref, canonicalized through `live_canon` and kept only when the target is a node. Its `relation` is the stored `DriverAction`. The action is what "serves" means here, and `relation` is the edge's one typed qualifier; that is a recorded deviation from §20's example, where `relation` is null.
    - `anchored_to` goes from the idea to `time_anchor.ref`, with `relation` set to the stored anchor relation, and is kept only when the target is a node.
    - Edges are deduped by target, and the first stored entry wins.
    - **No `stale_reason`.** E's `suggest.validate_ideas` is the one definition of staleness, and B and E state that `continuity.*` never imports `suggest`. Re-deriving it here would be a second definition that can drift. The idea's detail shows each served driver's current status from its node, which carries the same evidence.
13. **Reviewed links and candidates.**
    - `effective.links(cid, ledgers)` edges get `kind: "link"`, `source: "reviewed"`, `relation` set to the link relation, and the stored link id as `id`. Self-collapsing and duplicate links, and links whose endpoint is known to be gone, are already excluded (§26). **A link edge is still kept only when both `a` and `b` are in `node_ids`**, the rule candidate, `serves` and `anchored_to` edges use. `_classify_links` drops a link only when `ledgers.exists(...)` is `False`; with that endpoint's ledger unreadable it answers `None` (existence unknown) and the link is kept, while the graph builds no node for that ledger. Event nodes also come from phase A and links from phase B, so an event created and linked in between would otherwise give a dangling edge.
    - Candidates come from `pending.findings(cid, pending.Current.load(cid, ledgers))`, keeping verdicts in `pending.VISIBLE`. Anything else would draw a dashed edge whose Ledger address says "This finding is no longer pending" (D).
    - Pair kinds (`possible_duplicate`, `possible_relation`) become edges with `kind` set to the candidate kind, `source: "candidate"`, and `id = candidate_id = <candidate id>`. Their endpoints are canonicalized through `live_canon`, dropped when absent, and dropped when the two collapse into one.
    - Every visible finding, of either kind, is also listed on the nodes it names as `findings: [{id, kind, other}]`, where `other` is the canonical ref at the other end for a pair kind (when it is a node; else `null`) and `null` for a lifecycle kind. Without `other`, which record a candidate pairs with would live only in a dashed, `aria-hidden` path (Decision 26). Lifecycle kinds have one ref, so they can only be node findings. This is §19.6's "candidate findings".
    - D's `possible_relation` can pair a commitment with an **event** (the temporal relation). So event nodes carry `findings: []` too, in Python and in the TS `event` member, and the commitment→event candidate edge draws in the Continuity lens, which shows events.
14. **Relationships.**
    - `relationships.read` is wrapped by `_attempt`: it raises on bad JSON, and on a list top level it raises `AttributeError` from `setdefault`.
    - Feelings and bonds are parsed as `routes/campaigns._ledger_relationships` parses them: split on `->` and `|`, skip non-str keys and non-dict records, and clamp meters to 0–5 on `("trust", "affection", "tension")`. Its names are **not** copied: it reads a card per token.
    - A `feeling` edge carries typed `trust`, `affection`, `tension` and `note`. A `bond` edge carries `bond_type` and `since_scene`, which is kept even when that scene is deleted (it is provenance).
    - A token whose prefix is not `characters` or `pcs` gives no edge.
    - Every actor token in the module (`appeared_in`, `involves`, `feeling`, `bond`, `birthday_of`) is classified by one non-raising helper, `_actor_kind(token) -> "character" | "pc" | None`, built on `token.partition(":")` and requiring a non-empty id. `canon.split_ref` is not used for this: `involvement.scene_actors` passes every non-empty chronicle `cast` string through `canon.actor_ref`, which maps `"Mara"` to `"Mara"` and `"characters/"` to `"characters:"`, and `split_ref` raises `ValueError` on both.
    - The typed extras are a recorded additive deviation from §20's edge shape. Without them, the Cast lens and actor detail could not say what a relationship *is*.
15. **Ids and order are deterministic.**
    - A structural or alias edge id is `"e" + sha256(f"{kind}\0{from}\0{to}").hexdigest()[:20]`, the `link_id` recipe. Link edges use the link id, and candidate edges the candidate id. The three prefixes are disjoint.
    - Nodes are ordered by `NODE_KINDS` index, then scenes by `order` and everything else by id.
    - Edges are ordered by `EDGE_KINDS` index, then `from`, `to` and `id`.
    - So two reads of an unchanged campaign are equal, and the frozen snapshot is stable.
16. **`omitted: [part…]`** is a top-level, additive field. `part ∈ PARTS = ("calendar", "scenes", "chronicle", "plot", "commitments", "events", "continuity", "candidates", "relationships", "scene_ideas", "names")`, sorted in that order.
    - `plot`, `commitments` and `events` come from `Ledgers.unreadable`.
    - `continuity` is set when `doc.malformed(cid)` is non-empty.
    - `candidates` is set when `candidates.malformed(cid)` is true.
    - Every other part is added by `_attempt` when its read raises.
    - Per-row date softening (`part=None`) never records a part, so a healthy campaign with a free-text event date or an undated idea reads `omitted == []`.

    §3.9 says reads omit the affected enhancement. Saying *which* one was omitted is what lets a reader tell an empty campaign from a broken file. The page renders it as one note.
17. **§20 vocabulary.**
    - `graph.NODE_KINDS`, `EDGE_KINDS`, `EDGE_SOURCES`, `EVENT_STATUSES` and `PARTS` are new. `effective.LINK_RELATIONS = tuple(RELATIONS)` sits beside the table it names, so there is one source. `doc._RELATIONS` stays private, because `doc` is a leaf and may not import `effective`.
    - The TS unions `NodeKind`, `EdgeKind`, `EdgeSource`, `LinkRelation`, `EventStatus` and `GraphPart` are hand-written in `api/types.ts`. E's `DriverKind`, `DriverAction`, `PressureState`, `AnchorRelation` and D's `CandidateKind` are reused, never redeclared.
    - A backend test pins the Python literals, and parses `types.ts` with E's union-parsing helper.
18. **The campaign name comes from `useCampaignShell(cid, { demand: false }).payload?.campaign?.name`**, not from `api.getCampaign`. §28.9 requires the graph read to be the only API call the page makes. Plain `useCampaignShell(cid)` (the ScenesView/CampaignHub call) does not meet that: it calls `retry()` once per arrival, which raises the rail's `demand` and issues a fresh `GET /api/shell`. So `useCampaignShell` gains an optional second argument, `{ demand?: boolean }` (default `true`, so every existing caller is unchanged); with `demand: false` it skips the arrival `retry()` and only reads what the context already holds for this `cid`. The rail's own reads stay the rail's. Putting the name in the graph payload was the alternative, and was rejected because it is a field nobody else needs. Until the context holds this campaign, the eyebrow is absent and the back link reads "Campaign".
19. **URL and local state.**
    - `?lens=` takes one of the four, and anything else reads as `story`.
    - `?arc=` names a canonical thread or commitment node, and anything else reads as no filter.
    - `?node=` names any node, and anything else reads as no selection.
    - Lens and arc changes **push** a history entry: they are views the reader may go Back to. Node selection **replaces** the entry: a tap is browsing, and twenty taps should not take twenty Backs.
    - All three are written through a `setQuery(next, replace)` helper in SearchView's style (`URLSearchParams`, never concatenation).
    - The Show toggles are local state. Each lens has defaults (Decision 20), and switching the lens restores them. §19.1 puts only lens, arc and node in the URL.
    - The read effect depends on `[cid]` only, so no search change refetches.
20. **Lens presets** (`model.ts`, client-side, one payload):

    | Lens | Node kinds | Edge kinds | Show defaults |
    |---|---|---|---|
    | Story | scene, thread, commitment, event, holiday, birthday, idea | opened_in, advanced_in, touched_in, closed_in, resolved_in, serves, anchored_to, link | all off |
    | Cast | scene, thread, commitment | (none of its own) | actors on |
    | Calendar | scene, event, holiday, birthday, commitment (only with a deadline) | link, birthday_of | all off |
    | Continuity | thread, commitment, event | link, merged_into, possible_duplicate, possible_relation | merged and candidates on |

    The Show toggles work on top of the lens:
    - **Actors** adds `character`/`pc` nodes, and the `appeared_in`, `involves`, `feeling`, `bond` and `birthday_of` edges.
    - **Locations** adds `location` nodes and `occurred_at` edges.
    - **Merged records** adds nodes with `merged_into` set, and `merged_into` edges. Without it, those nodes and edges are never visible.
    - **Review candidates** adds candidate edges. Without it, they are never visible.

    The Cast lens's actors and their edges come **only** from the Actors toggle, which that lens turns on by default. Listing them in the preset as well would make unchecking Actors in Cast do nothing.

    Further rules:
    - The Story lens also drops events whose `status` is `fired` or `passed` (§19.4).
    - The Calendar lens keeps a commitment only when it carries a deadline (`native !== ""`), because §19.5 lists "deadlines", not every commitment. A deadline whose due cannot be parsed (`native` set, `fixed` null) still goes under "Undated", as §19.2 allows. Ideas are not in the Calendar lens (§19.5 does not list them), so it has no `anchored_to` edges.
    - **Arc filter:** with `?arc=` set, the visible nodes are the arc, its merged nodes, its reviewed-link neighbours, the scenes its movement edges reach, and the actors its `involves` edges reach (§19.6). That set replaces the lens's node kinds and toggles. **Precedence:** the arc filter shows the arc's own merged nodes and `merged_into` edges whatever the Merged toggle says, because §19.6 asks the filter to show "its aliases" and is the more specific rule. §19.3's "shown only when 'Show merged' is enabled" still governs every view without an arc filter. This is recorded against §19.3. The lens still chooses the layout, and candidate edges still need their toggle.
    - In every case an edge is visible only when both of its ends are.
    - Every edge kind a preset lists has both of its end kinds reachable in that lens (through its node kinds or a toggle). A model test pins this, so a preset cannot list an edge that never draws.
21. **Layout is a pure function** (`layout.ts`), so jsdom tests can assert coordinates.
    - There are two axes. The **play axis** serves Story, Cast and Continuity, and the **calendar axis** serves Calendar.
    - Columns are ordinal, never a linear day scale. A day-scaled axis would put a year's gap between two scenes a flashback apart.
    - The geometry is `COL = 184`, `ROW = 64`, `NODE_W = 160`, `NODE_H = 44`, `PAD = 16`, `HEAD = 28`. `NODE_H` is the 44px touch target, and a node button is exactly that tall (one-line label and status, ellipsized), so it can never grow into the next row; `NODE_W` fits a short title at the column font; `HEAD` is one line of column-head text, reserved above row 0 so a head never overlaps a node. All are to be tuned against real campaigns later.
    - The Now column holds **only** the marker. A node with no scene column on the play axis (an arc in a lens with no visible scene, an actor with no appearance) goes in a leading "Not in a scene" column, before Reached and Now, so the marker never runs through a button.
    - The algorithm is given in Task 10.
22. **The phone column closes when a record is picked.** PageShell's rule is that a column row that hands you a record leaves the sheet, while one that filters keeps it up. Today it can only apply that rule on a pathname change, and an Arcs row changes only `?node=`. So `PageShell` gains an optional `dismissKey`: when its value changes, `showColumn` resets. The key must change on every **pick**, not only on a new value: re-tapping the Arcs row that is already selected writes the same `?node=`, and the sheet would stay over the detail. So the page keeps a `pick` counter that every Arcs-row click and node click increments, and passes `dismissKey={`${node}#${pick}`}`. Lens and Show rows (filters) do not increment it, so they keep the sheet up and an Arcs row closes it. Making Arcs rows change the pathname was rejected: §19.1 puts the node in `?node=`.
23. **Node detail and actions** (§19.6), rendered as a `.detail-view` below the drawing pane:
    - **Thread and commitment:**
      - **Focus next scene:** shown unless the node is merged, and enabled iff `focusable`. Disabled, its hint reads "Only an open thread or an unresolved commitment can steer the next scene."
      - **Open ledger entry:** a `<Link>` to `ledgerHref(cid, {section: kind === "thread" ? "threads" : "commitments", row: <id after the first ":">})`. D resolves a merged source to its canonical row and shows retired rows.
      - **Filter to this arc:** sets `?arc=` to the canonical ref, meaning `merged_into ?? id` (a merged node filters to its canonical, since Decision 19 reads a non-canonical `?arc=` as no filter), and reads "Show the whole graph" when that canonical ref is already the filter.
    - **Event, birthday and holiday:**
      - **Anchor next scene:** enabled iff `anchorable`. Disabled, its hint reads "Only an upcoming dated moment can anchor the next scene."
    - Each finding links to `ledgerHref(cid, {section: "continuity", group: GROUP_OF[kind], candidate: id})`.
    - Metadata that names another record is a `chip` button that selects that node (the CLAUDE.md list/detail rule). A scene also gets an "Open scene" link to `/campaigns/{cid}/scenes/{encodeSegment(sid)}`.
24. **Hotkeys:**
    - `1`–`4` choose the lens in column order ("Story lens", "Cast lens", "Calendar lens", "Continuity lens");
    - `escape` reads "Clear the selection", `enabled: !!node`.

    All are in group `"STORY GRAPH"`. Bare digits are unbound everywhere else in the app (the play view's `n`/`r`/`t` live on another route). A node is activated by its own button (§19.5).
25. **The rail glyph is `⋈`** (bowtie, read as two joined nodes). A grep of `frontend/src` finds it nowhere, and it is in the BMP math block that the rail's font stack renders. Task 12's AppRail test pins the row. The glyph is `aria-hidden` there, as every rail icon is.
26. **Edges carry no interaction and no unique information.** The SVG layer is `aria-hidden`, and every edge is restated in the detail of the node at either end. For a candidate edge that restatement is the finding's `other` (Decision 13), rendered as "<FINDING_PHRASE> with <chip to other>". That satisfies "no hover-only information" (§19.4) without a second focus order through hundreds of paths. Candidate edges are dashed (`.sg-edge.candidate`, after PlotMap's `excludes` precedent), and alias edges are dotted.
27. **The writer guard covers `graph`.** D's `SCANNED` already lists it, and Task 6 adds it to `REQUIRED_PRESENT`.
28. **The frozen sweep gains `continuity.graph.build[{cid}]`** (Task 7). The fixture has no continuity.json, candidate cache or ideas, so this proves those absent-file reads on a store today's code did not write. It **does** have a pre-capstone `relationships.json` (one bond, one feeling, between a world character and a campaign PC), a `region` on its calendar, and two scenes, one of them a legacy id. So the sweep also proves that old relationship tokens become `feeling` and `bond` edges whose ends are nodes, and that the legacy id orders by `timeline.build`'s rule.

### Deliberate deviations (each recorded in the spec at Task 14)

- `graph` imports `calendars`, `chronicle`, `events`, `fieldtext`, `locks`, `overlay`, `relationships`, `scene_ideas`, `campaigns.paths` and `scenes.read` beyond §7.0's "the readers above" (Decision 1). It takes the best-effort lock inside the store function.
- §19.7's "once per request" is read as a constant number of reads per request, with no N+1 (Decision 4). Literal "once" is deferred to G's performance audit.
- Edges are additive beyond §20's shape: `feeling` carries `trust`, `affection`, `tension` and `note`, and `bond` carries `bond_type` and `since_scene` (Decision 14). `serves.relation` is the driver action (Decision 12).
- §19.3's reviewed links are `kind: "link"`, and candidate edges take their candidate kind as `kind` (Decision 13).
- Nodes add typed fields §20 does not list (`order`, `done`, `pcless`, `place`, `live`, `merged_into`, `focusable`, `anchorable`, `findings` with `other`, on events too, `status` on events). The payload adds a top-level `omitted` (Decisions 9, 13, 16).
- Only active ideas are nodes. Groups and facts are not built (Decision 6).
- Holiday and birthday nodes exist only inside pressure's horizon. An idea anchored outside it has no `anchored_to` edge, and an actor whose birthday falls outside it shows no Birthday section in its detail (§19.6; Decision 11).
- The calendar axis adds a trailing "Not dated" column, before "People and places", for record nodes that carry no date by nature: threads, commitments without a deadline, and merged nodes. An arc filter (which replaces the lens's node kinds) or the Merged toggle can make them visible on the Calendar lens (Task 10).
- Fired and passed events sit in a "Reached" play-axis column in the Cast and Continuity lenses, and the Story lens drops them (Decision 21). The play axis adds two columns §19.4 does not name: a leading "Not in a scene" for arcs and actors with no visible scene column, and an "Undated" column (after the temporal slots, before the idea slots) for unfired events and birthdays whose `fixed` is null. "Unscheduled" is reserved for ideas with no date, as §19.4 says; a dated idea in the past still takes an idea slot.
- The arc filter shows the arc's aliases whatever the Merged toggle says (§19.6 over §19.3; Decision 20). The Calendar lens shows only commitments with a deadline and no ideas (§19.5; Decision 20).
- Location names come from a whole-kind `overlay.list_entities` read (constant per request, not N+1; Decision 5). Narrowing it is handed to G.
- `useCampaignShell` gains `{ demand: false }`, so the page reads the campaign name without issuing a shell read (Decision 18).
- `PageShell` gains `dismissKey` (Decision 22).
- `birthdays.gather` reads a PC's name from meta (Decision 5). It is a B module, changed here because the graph's §19.7 cost rule reaches through it.

---

## File structure

| File | Responsibility |
|---|---|
| `backend/src/grimoire/store/pcs.py` | `name_of`, `roster` (image-free) |
| `backend/src/grimoire/store/overlay.py` | `pc_roster` (the campaign∪world union, as `character_roster`) |
| `backend/src/grimoire/store/birthdays.py` | `gather` names a PC through `pcs.name_of` |
| `backend/src/grimoire/store/continuity/effective.py` | `LINK_RELATIONS` |
| `backend/src/grimoire/store/continuity/graph.py` (new) | the tuples, `edge_id`, `build` and its private builders |
| `backend/src/grimoire/store/continuity/__init__.py` | docstring bullet for `graph` (no imports) |
| `backend/src/grimoire/routes/continuity.py`, `routes/__init__.py` | `GET /continuity/graph`; docstring rows |
| `backend/tests/test_continuity_graph.py` (new) | store tests: §28.8, the pins, the cost and invariants |
| `backend/tests/test_continuity_routes.py` | route tests |
| `backend/tests/test_roster_shell.py`, `test_birthday_occurrences.py` | the PC roster and the birthday name read |
| `backend/tests/test_continuity_writer_guard.py` | `REQUIRED_PRESENT` gains `graph` |
| `backend/tests/fixtures/frozen_campaign/sweep.py`, `snapshot.json` | one new key; regenerated deliberately |
| `frontend/src/api/types.ts`, `api/client.ts` | the graph types; `continuityGraph` |
| `frontend/src/components/storyGraph/model.ts` (new) | lenses, presets, `visible`, `splitRef`, `statusOf`, `accessibleName`, `seedFor`, `ledgerTarget` |
| `frontend/src/components/storyGraph/layout.ts` (new) | `layout`, `edgePath`, the geometry constants |
| `frontend/src/components/storyGraph/StoryGraphDrawing.tsx` (new) | the positioned buttons, the SVG layer, the Now marker and the column heads |
| `frontend/src/components/storyGraph/NodeDetail.tsx` (new) | the per-kind detail and its actions |
| `frontend/src/routes/StoryGraphView.tsx` (new) | the page: the read, URL state, the column, hotkeys |
| `frontend/src/components/PageShell.tsx` | `dismissKey` |
| `frontend/src/shell/ShellPayloadContext.tsx` | `useCampaignShell(cid, { demand: false })` (Decision 18) |
| `frontend/src/testkit/storyGraph.ts` (new) | the shared hand-built `StoryGraph` fixture (Task 9), imported by `model.test.ts`, `layout.test.ts` and `StoryGraphView.test.tsx` |
| `frontend/src/testkit/viewport.ts` (new) | `setInnerWidth(px): () => void`, lifted from `PageShell.test.tsx`'s local `atWidth` (Task 11) |
| `frontend/src/shell/rail.ts`, `App.tsx` | the rail row; the route |
| `frontend/src/index.css` | `.sg-*` rules |
| tests beside each frontend file, plus `shell/rail.test.ts`, `components/AppRail.test.tsx`, `components/PageShell.test.tsx` | |
| `docs/superpowers/specs/2026-10-04-continuity-capstone-design.md` | amended to the recorded deviations (Task 14) |

Backend paths below are relative to `backend/src/grimoire/` and tests to `backend/tests/`, unless they start with `frontend/`.

---

### Task 1: Image-free PC names, and the birthday name read

**Files:**
- Modify: `store/pcs.py`, `store/overlay.py`, `store/birthdays.py`
- Test: `tests/test_roster_shell.py`, `tests/test_birthday_occurrences.py` (append). These must pass **unmodified**: `tests/test_suggest_store.py`, `tests/test_birthdate.py`, `tests/test_calendar_plugins.py` and `tests/test_continuity_pressure.py`.

**Interfaces:**
- Produces:
  - `pcs.name_of(root: Path, pid: str) -> str`. It runs `_require_pc` and raises `PCNotFound` when `_version_ids` is empty, exactly where `read_pc` raises, then returns `_read_meta(root, pid).get("name", pid)`. It reads no version file, no image and no sidecar.
  - `pcs.roster(root: Path) -> list[dict]`: `{id, name, default_version}` per PC that `list_pcs` lists, with the same walk (`_listed_dirs`), filter (an addressable version), order and default rule. It makes no `assets` call. The docstring follows `characters.roster`'s.
  - `overlay.pc_roster(cid: str, *, v: View | None = None) -> list[dict]`: `_roster_union(v, "pcs", pcs.roster(v.croot), pcs.roster(v.wroot), key=lambda r: r["id"])`. It sits beside `character_roster` and inherits that block comment.
  - `birthdays.gather`: the PC branch's name becomes `pcs.name_of(aroot, a["id"])`, and nothing else changes.
- [ ] **Step 1: Write the failing tests.**
  - `test_roster_shell.py::test_the_pc_roster_is_list_pcs_cut_to_three_fields`:
    - setup: reuse `_library`'s PCs as they are. It already creates the world PCs `seraphine` and `mara` and the campaign PC `winifred`, and tombstones `mara` in the campaign. Creating them again would yield `seraphine-2` and `winifred-2`;
    - assert `overlay.pc_roster(cid) == [{"id": p["id"], "name": p["name"], "default_version": p["default_version"]} for p in overlay.list_pcs(cid)]`, that the list is non-empty, and that the tombstoned `mara` is absent from it.
  - `test_roster_shell.py::test_the_pc_roster_scans_no_image`: wrap `grimoire.store.assets.list_images` and `read_focus` in call **recorders** (not raisers). `overlay.pc_roster(cid)` records 0 calls.
  - `test_roster_shell.py::test_pc_name_of_matches_read_pc`:
    - it equals `pcs.read_pc(root, pid)["meta"]["name"]`;
    - a missing id raises `PCNotFound`;
    - a PC whose every `<vid>.md` was unlinked (versions are files beside `pc.md`, and `_version_ids` globs `*.md` minus `pc.md`) raises `PCNotFound`, as `read_pc` does.
  - `test_birthday_occurrences.py::test_gather_names_a_pc_without_scanning_images`:
    - setup: that file's own `_campaign(monkeypatch, tmp_path, now="2026-05-10")` and `_now_fixed(cid)` (it has no `F`). Seat Seraphine as a PC with birthdate `"--05-11"` on her persona. The PC needs a scene to be seated in, so create one (`S1`), as B's `test_offscreen_drops_player_tokens` does (`overlay.create_pc`, persona `birthdate`, `appearances.transitions.appear(cid, S1, "pcs", pid, vid, "player")`). Wrap `grimoire.store.assets.list_images` in a recorder;
    - `birthdays.occurrences(cid, _now_fixed(cid))` has one row with `name == "Seraphine"`;
    - the recorder counts 0.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_roster_shell.py tests/test_birthday_occurrences.py -q -p no:cacheprovider`. Expected: `AttributeError` for `pc_roster` and `name_of`, and a non-zero recorder count.
- [ ] **Step 3: Implement** the three functions and the one-line `gather` change.
- [ ] **Step 4: Run them, and the unchanged suites.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_roster_shell.py tests/test_birthday_occurrences.py tests/test_suggest_store.py tests/test_birthdate.py tests/test_calendar_plugins.py tests/test_continuity_pressure.py tests/test_frozen_campaign.py tests/test_import_guard.py tests/test_store_api_baseline.py tests/test_lock_domain_guard.py tests/test_paths_guard.py tests/test_atomic_guard.py -q -p no:cacheprovider`. The last three see the change: `overlay` is a lock-domain module, and `pcs.roster` walks directories. Expected: PASS, with no existing test edited and `snapshot.json` untouched (the birthday line is byte-identical).
- [ ] **Step 5: Lint and types.** From the repo root, `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS.
- [ ] **Step 6: Commit:** `feat(store): image-free PC roster; birthdays name a PC from its meta`

---

### Task 2: `continuity.graph` — vocabulary, phases, and the scene spine

**Files:**
- Create: `store/continuity/graph.py`
- Modify: `store/continuity/effective.py` (`LINK_RELATIONS`), `store/continuity/__init__.py` (docstring bullet: "``graph`` -- the Story Graph projection (§19): nodes and edges over every reader above; read-only, best-effort locked, plugin code outside the hold.")
- Test: `tests/test_continuity_graph.py` (new)

**Interfaces:**
- Produces:
  - `effective.LINK_RELATIONS = tuple(RELATIONS)`.
  - In `graph`:
    - `NODE_KINDS = ("scene", "character", "pc", "location", "thread", "commitment", "event", "idea", "birthday", "holiday")`;
    - `EDGE_KINDS = ("appeared_in", "occurred_at", "opened_in", "advanced_in", "touched_in", "closed_in", "resolved_in", "involves", "serves", "anchored_to", "feeling", "bond", "birthday_of", "link", "merged_into", "possible_duplicate", "possible_relation")`;
    - `EDGE_SOURCES = ("structural", "reviewed", "candidate", "alias")`;
    - `EVENT_STATUSES = ("scheduled", "fired", "passed", "undated")`;
    - `PARTS = ("calendar", "scenes", "chronicle", "plot", "commitments", "events", "continuity", "candidates", "relationships", "scene_ideas", "names")`.
  - `edge_id(kind: str, a: str, b: str) -> str`: `"e" + sha256(f"{kind}\0{a}\0{b}".encode()).hexdigest()[:20]`.
  - `build(cid: str) -> dict`, returning `{"now": {"native", "friendly", "fixed"}, "nodes": [...], "edges": [...], "omitted": [...]}` (Decisions 15, 16). It never raises for an existing campaign. The phases (Decision 1):
    - `_attempt(omitted: set[str], part: str | None, fn, fallback, *args)`: the only broad `except`. On failure it adds `part` (unless `None`) and returns `fallback`.
    - `_temporal(cid, omitted) -> dict`, outside the hold. It returns `{provider, pressure, snapshot, events}`. In this task it carries only `provider` (via `calendars.primary_provider(campaigns_paths.campaign_root(cid))`, with part `"calendar"`) and `pressure` (`pressure.build(cid)`, which never raises). Task 5 fills the rest.
    - `_files(cid, omitted) -> dict`, inside `with locks.best_effort_campaign_lock(cid):`. In this task it reads `scenes` (`scenes_read.list_scenes`, part `"scenes"`), `chronicle` (`chronicle.read_chronicle`, part `"chronicle"`; a non-dict reads `{}`) and `histories` (`{sid: scenes_read.get_location_history(cid, sid)}`, each read guarded inside the `"scenes"` part). Later tasks add their reads here.
    - `_assemble(cid, temporal, files, names, omitted) -> dict`, outside. It calls one private builder per family (`_scene_nodes` here, and later `_actor_parts`, `_record_parts`, `_temporal_parts`, `_idea_parts`, `_review_parts`), each through `_attempt` with its family's part (Global Constraints), then dedupes nodes by id and edges by id, and sorts both (Decision 15).
  - **Scene node:**
    - `{id: "scene:<sid>", kind: "scene", label: <title or sid>, order: <index in sorted ids>, done, pcless, place: <chronicle location text>, native: <date>, friendly, fixed, in_days}`;
    - `native` is `list_scenes`' `date`, else the chronicle `date`, else `""`;
    - `friendly` is `calendars.friendly(provider, native)`, softened to `""` with `part=None`;
    - `fixed` is `calendars.fixed_of(provider, native)`, softened to `None` with `part=None`;
    - `in_days` is `fixed - now_fixed` when both are ints, else `None`.

    Every text field goes through `fieldtext.text` (the hand-edited-JSON guard `timeline._text` and `plot._field` apply).
- [ ] **Step 1: Write the failing tests.** Use B's `_campaign(monkeypatch, tmp_path, ...)`, `_plugin`, `_primary`, `_BROKEN_PROVIDER_SRC` and `F`, imported from `tests.test_continuity_pressure` as `test_continuity_routes.py` does (`region=""`, clock 2026-05-10). B's helper is `F(cid, native)`: every `F(...)` in Tasks 2–7 is written `F(cid, "<native>")` (a test may define a local `def D(native): return F(cid, native)`), never with one argument.
  - `test_graph_tuples_are_pinned`:
    - the five literals above;
    - `effective.LINK_RELATIONS == ("continues", "subthread_of", "pays_off", "before", "on", "after", "by", "related_to") == tuple(effective.RELATIONS)`;
    - `set(graph.NODE_KINDS) == set(canon.KIND_OF_PREFIX.values()) - {"group", "fact"}` (the kind→prefix table is stated once);
    - and, as §20's one set: `drivers.DRIVER_KINDS`, `drivers.DRIVER_ACTIONS` and `pressure.PRESSURE_STATES` equal their B/E literals.
  - `test_edge_id_is_stable_and_kind_scoped`:
    - `edge_id("appeared_in", "characters:mara", "scene:001")` is 21 characters and starts with `"e"`;
    - it equals itself on a second call;
    - it differs from the `occurred_at` id for the same ends.
  - `test_an_empty_campaign_is_an_empty_graph`: the result equals `{"now": {"native": "2026-05-10", "friendly": <provider friendly>, "fixed": F(cid, "2026-05-10")}, "nodes": [], "edges": [], "omitted": []}`.
  - `test_scenes_follow_play_order_not_recency_or_date` (§28.8):
    - create "Saltmarch harbour", "Mara's flashback" and "Winifred's chart" in that order;
    - `store.scenes.set_datetime` them to `2026-05-20`, `2026-04-01` and `2026-05-12`, re-reading each returned `id`, since the first stamp renames;
    - then write each scene's `updated` frontmatter explicitly, in an order that is not play order: S1 `2026-01-01T00:00:03Z`, S2 `…:01Z`, S3 `…:02Z` (through `parse_frontmatter` / `dump_frontmatter` on the scene file). `now_iso()` has one-second resolution, so stamps from create, `set_datetime` and `mark_absorbed` usually tie in one second, and `list_scenes`' stable sort then leaves directory order: relying on them makes the control filesystem-dependent;
    - `[n["id"] for n in scene_nodes] == [f"scene:{s}" for s in sorted(ids)]`, and `order == [0, 1, 2]`;
    - positive control, exact: `[r["id"] for r in store.scenes.list_scenes(cid)] == [S1, S3, S2]` (newest `updated` first), and the dates order S2 < S3 < S1, so both recency and date disagree with play order.
  - `test_scene_dates_carry_fixed_and_in_days`:
    - a scene stamped `2026-05-12T20:00` has `native == "2026-05-12T20:00"`, `fixed == F(cid, "2026-05-12")` and `in_days == 2`;
    - an unstamped scene whose chronicle record has date `2026-05-08` has `in_days == -2`;
    - a scene with neither has `native ""`, `fixed None` and `in_days None`.
  - `test_a_raising_plugin_leaves_scenes_undated` (Review Focus 2): stamp a scene, then switch the primary to `_BROKEN_PROVIDER_SRC`. `build` returns, the scene node has `fixed None`, and `"calendar" in g["omitted"]`.
  - `test_build_is_deterministic`: two builds of one campaign are equal.
  - `test_a_free_text_scene_date_is_undated_not_a_broken_calendar`: a scene whose chronicle date is `"midsummer"` has `fixed None`, and `g["omitted"] == []` (per-row softening records nothing).
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_graph.py -q -p no:cacheprovider`. Expected: `ModuleNotFoundError` / `ImportError`.
- [ ] **Step 3: Implement** `graph.py`'s tuples, `edge_id`, `_attempt`, the three phases and `_scene_nodes`, plus `effective.LINK_RELATIONS`. The module docstring states:
  - the phases and why (Decision 1);
  - the play-order rule and why not `list_scenes` or `timeline.build` (Decision 2);
  - the "constant reads" reading of §19.7 (Decision 4);
  - that every edge names two nodes (Decision 11).
- [ ] **Step 4: Run them and confirm they pass.** Use the same command. Expected: PASS.
- [ ] **Step 5: Run the module guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_import_guard.py tests/test_lock_domain_guard.py tests/test_lock_order_guard.py tests/test_paths_guard.py tests/test_atomic_guard.py tests/test_store_api_baseline.py tests/test_continuity_effective.py -q -p no:cacheprovider`. Expected: PASS (`graph` is unclassified, as a reader should be).
- [ ] **Step 6: Lint and types.** `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS.
- [ ] **Step 7: Commit:** `feat(continuity): story graph projection with the scene spine in play order`

---

### Task 3: Actors and locations — `appeared_in`, `occurred_at`, `feeling`, `bond`, names

**Files:**
- Modify: `store/continuity/graph.py`
- Test: `tests/test_continuity_graph.py`

**Interfaces:**
- Consumes: `involvement.scene_actors(cid)` (inside the hold), `relationships.read(cid)` (inside, part `"relationships"`), `overlay.character_roster` / `overlay.pc_roster` / `overlay.list_entities(cid, "locations")` (outside, each in its own `_attempt` with part `"names"`, Decision 5), and `canon.KIND_OF_PREFIX`. Actor tokens are classified by `_actor_kind`, never by `canon.split_ref` (Decision 14).
- Produces:
  - `_names(cid, omitted) -> dict[str, str]`: `"characters:<id>" → name`, `"pcs:<id>" → name` and `"locations:<id>" → name`, from three independent `_attempt` calls.
  - `_actor_kind(token) -> "character" | "pc" | None`: `token.partition(":")`; `"characters"` → `"character"`, `"pcs"` → `"pc"`, provided the id after the colon is non-empty; anything else `None`. It never raises.
  - `_actor_parts(files, names) -> (nodes, edges)`:
    - **`appeared_in`:** `actor → scene:<sid>` for each `(sid, actors)` in `scene_actors` whose `sid` is listed and whose `_actor_kind` is not `None`.
    - **`occurred_at`:** `scene:<sid> → locations:<id>` for each distinct entry of the scene's history, in order.
    - **`feeling`:** `a → b` per Decision 14, with `trust`, `affection`, `tension` (each clamped to 0–5, a non-int reading 0) and `note`.
    - **`bond`:** `lo → hi`, with `bond_type` (the record's `type` text) and `since_scene`.
    - **Actor nodes:** `{id, kind: "character" | "pc", label}`, for every actor any edge names; this task covers these four kinds, and Tasks 4 and 5 add `involves` and `birthday_of`.
    - **Location nodes:** `{id, kind: "location", label}`.
    - **Labels:** `names.get(ref)`, else the id after the first `:`.
- [ ] **Step 1: Write the failing tests.**
  - `test_appeared_in_unions_roster_and_chronicle_cast` (§28.8):
    - Mara is seated in S1 through the appearance roster;
    - Winifred appears only in S2's chronicle `cast` (`"characters/winifred"`);
    - Seraphine is a PC seated in S2;
    - edges `{("characters:<mara>", "scene:S1"), ("characters:<winifred>", "scene:S2"), ("pcs:<seraphine>", "scene:S2")}`, and the node kinds are `character`, `character` and `pc` with their roster names.
  - `test_occurred_at_follows_location_history` (§28.8):
    - create the location "Saltmarch harbour";
    - `set_location` S1 there, then to a second location;
    - two `occurred_at` edges from S1, and both locations are nodes labelled by name;
    - a scene with no history has none.
  - `test_relationships_become_feeling_and_bond_edges`:
    - `set_feeling(cid, "characters:mara", "characters:winifred", 9, 2, -1, "wary")` gives a `feeling` edge whose meters are `trust 5, affection 2, tension 0` and whose note is `"wary"`;
    - `set_bond(..., "kin", since_scene="009--gone")` gives a `bond` edge with `bond_type "kin"` and `since_scene "009--gone"`;
    - a hand-written feeling `"groups:realm->characters:mara"` gives no edge.
  - `test_actor_labels_fall_back_to_the_id`: a chronicle cast token for a deleted character is a node labelled with its id.
  - `test_relationships_raising_costs_only_their_edges`: relationships.json holds `"[]"`. There are no feeling or bond edges, `"relationships" in omitted`, and `appeared_in` is still present.
  - `test_malformed_cast_tokens_cost_only_their_edges` (Review Focus 3): S2's chronicle `cast` is `["Mara", "characters/", "groups/realm", "characters/winifred"]`. `build` returns, and S2 has exactly one `appeared_in` edge, from `characters:winifred`.
  - `test_an_unreadable_location_costs_only_location_labels`: write a location `.md` with invalid UTF-8 bytes. Character labels still come from the roster, the location in a history is labelled with its id, and `"names" in omitted`.
- [ ] **Step 2: Run them and confirm they fail.** Same command as Task 2. Expected: FAIL (no actor edges).
- [ ] **Step 3: Implement** `_names` and `_actor_parts`, and add the two reads to `_files`.
- [ ] **Step 4: Run them and confirm they pass.** Expected: PASS, with Task 2's tests still green.
- [ ] **Step 5: Lint and types.** `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS.
- [ ] **Step 6: Commit:** `feat(continuity): story graph actors, locations and relationships`

---

### Task 4: Threads and commitments — records, merged sources, movements, involvement, pressure

**Files:**
- Modify: `store/continuity/graph.py`
- Test: `tests/test_continuity_graph.py`

**Interfaces:**
- Consumes:
  - inside the hold: `effective.Ledgers.load(cid)` (stored in `files["ledgers"]`; `unreadable` maps `plot` → `"plot"`, `commitments` → `"commitments"` and `events` → `"events"` into `omitted`), `effective.live_canon(cid, ledgers)`, `effective.records(cid, "thread")` (part `"plot"`), `effective.records(cid, "commitment")` (part `"commitments"`), and `involvement.of(cid, refs)` over every canonical record ref, closed ones included;
  - outside: `drivers.snapshot` from `_temporal`. This task adds `snapshot = _attempt(..., "calendar", lambda: drivers.snapshot(cid, pressure_result=p), _NO_SNAPSHOT)`, where `_NO_SNAPSHOT = {"now": "", "friendly": "", "fixed": None, "matching": "basic", "drivers": [], "anchors": []}`.
- Produces `_record_parts(files, temporal) -> (nodes, edges)`:
  - **Canonical node:** `{id, kind: "thread" | "commitment", label: title, status, live: effective.is_live(kind, status), merged_into: None, aliases, latest_beat, pressure: <driver reading> | None, focusable, findings: []}`. Commitments add `commitment_kind` (the record's `kind`), `due` (stored prose) and the four dated fields from their deadline item (Decision 9; `""`/`None` without one).
  - **Merged node:** each `aliases[]` entry gives `{id: ref, kind, label: title, status, live, merged_into: <canonical>, aliases: [], latest_beat: "", pressure: None, focusable: False, findings: []}`, and commitments add `commitment_kind: ""`, `due: ""` and empty dates. It also gives a `merged_into` edge `ref → canonical` with `source: "alias"`.
  - **Movement edges** (Decision 8): `opened_in`, `advanced_in`/`touched_in` and `closed_in`/`resolved_in`, from canonical records only, to listed scenes only.
  - **`involves`:** `canonical → actor` for each actor in `involvement.of(...)[ref]["actors"]` whose `_actor_kind` is not `None` (these tokens come from the same chronicle cast, so `split_ref` would raise on them). Actor nodes are ensured, labelled as in Task 3.
- [ ] **Step 1: Write the failing tests.**
  - `test_movement_edges_opened_advanced_closed` (§28.8). Thread "Mara's map" has beats at S1 (twice), S2 and S3, then `set_movement` closes it at S3.
    - Edges: `opened_in → S1`; `advanced_in → S2` and `→ S3`, with **no** `advanced_in → S1`; `closed_in → S3`.
    - A second, open thread has no `closed_in`.
    - Parametrized over the closing status `"closed"` and a hand-edited `"Closed"`: both give `closed_in → S3` (closure is `not effective.is_live`, Decision 8).
  - `test_commitment_movements_are_touched_and_resolved`: the same shape on "Mara's oath", fulfilled at S2, gives `opened_in`, `touched_in` and `resolved_in`.
  - `test_a_first_beat_in_a_deleted_scene_opens_nothing`: delete S1 after the beats land. There is no `opened_in`, `advanced_in` reaches S2, and no edge names `scene:<S1>`.
  - `test_an_alias_source_is_a_merged_node_with_a_merged_into_edge` (§28.8): `review.create_alias(cid, "thread:winifred-s-chart", "thread:mara-s-map")`.
    - The node `thread:winifred-s-chart` has `merged_into == "thread:mara-s-map"`, `focusable False` and `pressure None`.
    - The edge `merged_into` has `source "alias"`.
    - The canonical's `aliases == [{"ref": "thread:winifred-s-chart", "title": "Winifred's chart", "status": ...}]`.
    - The source's beats reach the canonical's movement edges, and the source has no movement edges of its own.
  - `test_involves_edges_come_from_involvement_of` (§28.8): Mara stood in S2, where "Mara's map" advanced, and Winifred stood only in S4. There is `involves` `thread:mara-s-map → characters:<mara>`, and none to Winifred. The edge set equals `involvement.of(cid, refs)`'s actors.
  - `test_focusable_is_exactly_the_chooser_driver_set` (Review Focus 4): open, closed and merged threads, plus open and fulfilled commitments. `{n.id for record nodes if n.focusable} == {d.ref for d in drivers.snapshot(cid)["drivers"] if d.kind in ("thread", "commitment")}`, and that set is non-empty.
  - `test_record_pressure_is_the_driver_reading`: a commitment due in 4 days with `warn 7` has `pressure == {"state": "due_soon", "in_days": 4, "friendly": <friendly>}` and dated fields `fixed == F(cid, "2026-05-14")`, `in_days == 4`. A closed thread has `pressure None`.
  - `test_a_garbled_plot_costs_only_threads`: plot.json `"{ no"`. There are no thread nodes, commitment nodes survive, `"plot" in omitted`, and scenes are present.
- [ ] **Step 2: Run them and confirm they fail.** Expected: FAIL.
- [ ] **Step 3: Implement** `_record_parts`, the `Ledgers`/records/involvement reads in `_files`, and the snapshot read in `_temporal`.
- [ ] **Step 4: Run them and confirm they pass.** Expected: PASS.
- [ ] **Step 5: Lint and types.** `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS (`_record_parts` split into `_record_node`, `_merged_nodes` and `_movements`, so each stays ≤ 10).
- [ ] **Step 6: Commit:** `feat(continuity): story graph threads, commitments, merges and movements`

---

### Task 5: Dated nodes and ideas — events, holidays, birthdays, `birthday_of`, `serves`, `anchored_to`

**Files:**
- Modify: `store/continuity/graph.py`
- Test: `tests/test_continuity_graph.py`

**Interfaces:**
- Consumes:
  - outside: `events.list_events(cid, provider, now_fixed)` (fallback `events.list_events(cid)`, part `"calendar"`), `calendars.fixed_of` and `calendars.friendly` per row (`part=None`, Decision 16), and the pressure items and snapshot anchors from `_temporal`;
  - inside: `scene_ideas.records(cid)` (part `"scene_ideas"`), with E's `drivers` and `time_anchor`.
- Produces `_temporal_parts(temporal, names)` and `_idea_parts(files, temporal, live, node_ids)`:
  - **Event node:** `{id: "event:<eid>", kind: "event", label: name, native: date, friendly, fixed, in_days, status, pressure, anchorable, findings: []}` (Decisions 10, 13; Task 6 fills `findings` from a temporal `possible_relation`).
  - **Holiday node:** `{id: item.ref, kind: "holiday", label, native, friendly, fixed, in_days, pressure: {state, in_days, friendly}, anchorable}`, from every pressure item of kind `holiday`.
  - **Birthday node:** the same fields plus `actor`, `precision` and `age`, from items of kind `birthday`.
  - **`birthday_of`:** `birthday → item.actor`, ensuring the actor node.
  - **Idea node:** `{id: "idea:<id>", kind: "idea", label: title, premise, source, pcless, time_anchor, native: date, friendly, fixed, in_days}`, for `status == "active"` only (Decision 6). `friendly` and `fixed` come through the provider outside the hold.
  - **`serves` and `anchored_to`:** per Decision 12.
- [ ] **Step 1: Write the failing tests.**
  - `test_events_are_all_listed_with_their_status`. The fixture starts the clock at 2026-05-01 (`_campaign(..., now="2026-05-01")`), creates an event on 05-05, and advances to 05-10, which fires it (a blank clock's first advance fires nothing, B plan #15). Then it creates the coronation for 05-13, writes an unfired event dated 05-02 by hand (passed), and writes an undated one (`"date": "midsummer"`). The statuses by id are `fired`, `scheduled`, `passed` and `undated`. The fired event is a node even though it is not a pressure item.
  - `test_dated_nodes_carry_fixed_and_in_days_undated_null` (§28.8):
    - the coronation has `fixed == F(cid, "2026-05-13")` and `in_days == 3`;
    - a dated idea has `in_days` set;
    - an undated idea and the undated event (`"date": "midsummer"`) have `fixed None` and `in_days None`;
    - `g["omitted"] == []`: an undated row is data, and per-row softening records no part (Decision 16).
  - `test_holidays_and_birthdays_come_from_pressure_items`. The fixture has a holiday rule in 2 days and Mara's yearless birthday in 1 day. The node ids, **as a set**, equal `{i["ref"] for i in pressure.build(cid)["items"] if i["kind"] in ("holiday", "birthday")}` (graph order is `NODE_KINDS` then id, pressure's is urgency, so a list comparison would pass only by fixture coincidence), that set is non-empty, and `birthday_of` reaches `characters:<mara>`.
  - `test_anchorable_is_exactly_the_chooser_anchor_set` (Review Focus 4): `{n.id for n in nodes if n.get("anchorable")} == {a["ref"] for a in drivers.snapshot(cid)["anchors"]}`, non-empty, with the fired and undated events absent from it.
  - `test_only_active_ideas_are_nodes`: an active idea, a used idea and a dismissed idea give one idea node.
  - `test_ideas_serve_canonical_drivers_and_anchor_to_nodes`. Write an idea through `scene_ideas.add(..., drivers=[{"ref": "thread:winifred-s-chart", "action": "advance"}, {"ref": "thread:gone", "action": "advance"}], time_anchor={"ref": "event:the-coronation", "relation": "on", "native": "2026-05-13"})`, after aliasing `winifred-s-chart` into `mara-s-map`.
    - One `serves` edge goes to `thread:mara-s-map`, with relation `"advance"`.
    - There is no edge to the gone thread.
    - `anchored_to` goes to `event:the-coronation`, with relation `"on"`.
  - `test_an_anchor_beyond_the_horizon_keeps_its_record_but_draws_no_edge`. The idea is anchored to `f"birthday:characters:<mara>:{F(cid, '2026-07-01')}"`. It has no `anchored_to` edge, and its `time_anchor` is as stored.
  - `test_a_garbled_ideas_file_costs_only_ideas`: scene_ideas.json `"[1]"` gives no idea nodes and `"scene_ideas" in omitted`.
- [ ] **Step 2: Run them and confirm they fail.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them and confirm they pass.** Expected: PASS.
- [ ] **Step 5: Lint and types.** `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS.
- [ ] **Step 6: Commit:** `feat(continuity): story graph events, occurrences and saved ideas`

---

### Task 6: Reviewed links and review candidates; the writer guard covers `graph`

**Files:**
- Modify: `store/continuity/graph.py`, `tests/test_continuity_writer_guard.py` (`REQUIRED_PRESENT` gains `"graph"`; the docstring line "`graph` is Slice F's and may be absent" is removed)
- Test: `tests/test_continuity_graph.py`

**Interfaces:**
- Consumes, inside the hold:
  - `effective.links(cid, ledgers)`;
  - `doc.malformed(cid)`, where non-empty gives `"continuity"`;
  - `candidates.malformed(cid)`, where true gives `"candidates"`;
  - `pending.findings(cid, pending.Current.load(cid, ledgers))` (part `"candidates"`).
- Produces `_review_parts(files, live, node_ids) -> (edges, findings_by_ref)`:
  - **`link`:** `{id: <link id>, kind: "link", from: a, to: b, source: "reviewed", relation, candidate_id: None}`, kept only when both `a` and `b` are in `node_ids` (Decision 13: existence unknown is not existence false).
  - **Candidate edges:** for each `(id, record, verdict)` with `verdict in pending.VISIBLE` and `record["kind"] in candidates.PAIR_KINDS`, `{id, kind: record["kind"], from, to, source: "candidate", relation: None, candidate_id: id}`. The endpoints are `live_canon` of `record["refs"]`. The edge is dropped when an endpoint is not a node or the two collapse into one.
  - **Findings:** every visible finding of any kind is appended as `{id, kind, other}` to `findings` on each canonical node it names (thread, commitment **and event** nodes), sorted by id. `other` is the pair's other canonical end when that is a node, else `null`; lifecycle kinds give `null`.
- [ ] **Step 1: Write the failing tests.**
  - `test_reviewed_links_are_link_edges` (§28.8): `review.create_link(cid, "thread:mara-s-map", "commitment:mara-s-oath", "pays_off")` gives a `link` edge with `source "reviewed"`, `relation "pays_off"` and `id` equal to the stored link id.
  - `test_a_dangling_link_draws_nothing`: a hand-written link to `event:gone` gives no edge, and the link appears in `effective.diagnostics(cid)["broken_links"]`.
  - `test_a_link_to_an_unreadable_ledger_draws_nothing` (Review Focus 3), parametrized: (a) a `thread:mara-s-map pays_off commitment:mara-s-oath` link, then plot.json `"{ no"`; (b) a `commitment:mara-s-oath before event:the-coronation` link, then events.json `"{ no"`. In each, `effective.links` still keeps the link (the positive control: `Ledgers.exists` answers `None`), the graph has no `link` edge, and every edge names two nodes.
  - `test_visible_pair_findings_are_candidate_edges` (§28.8). Write the cache with `candidates.write`, using `pending.fingerprint(pending.Current.load(cid), kind, refs)` so the verdict is `live`.
    - A `possible_duplicate` over Mara's map and Winifred's chart gives an edge with `kind "possible_duplicate"`, `source "candidate"` and `candidate_id` equal to its id.
    - A suppressed duplicate, written by `doc.put_suppression` with that fingerprint, gives none.
    - Mara's map's `findings` holds `{"id": <dup id>, "kind": "possible_duplicate", "other": "thread:winifred-s-chart"}`.
    - A `possible_thread_closure` gives no edge, and `{"id": ..., "kind": "possible_thread_closure", "other": None}` in Mara's map's `findings`.
    - A live temporal `possible_relation` over `commitment:mara-s-oath` + `event:the-coronation` gives a candidate edge between them, `build` does not raise, and the finding is in both the commitment's and the event's `findings`, each naming the other end.
  - `test_a_pair_finding_on_a_merged_source_is_dropped`. Write a pair finding over Winifred's chart and a third thread, then alias Winifred's chart into Mara's map. D's `test_verdicts` fixes the verdict for an alias source as `gone`, so assert that: `pending.findings(cid)` gives that id `gone`, there is no candidate edge, and neither `thread:mara-s-map` nor `thread:winifred-s-chart` lists the finding.
  - `test_a_lifecycle_finding_on_the_canonical_lands_on_it`: a live `possible_thread_closure` over `thread:mara-s-map` while Winifred's chart is merged into it is in Mara's map's `findings` only, and no edge names the merged node.
  - `test_a_malformed_cache_costs_only_candidates`: continuity_candidates.json `"{ no"` gives no candidate edges and `"candidates" in omitted`, with links still present.
  - `test_continuity_writer_guard.py::test_the_scanned_modules_exist`, now with `graph` required. It is RED until `graph.py` exists, and GREEN here.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_graph.py tests/test_continuity_writer_guard.py -q -p no:cacheprovider`. Expected: the graph tests FAIL; the guard PASSES, which proves `graph` calls no forbidden mutator.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them and confirm they pass.** Use the same command. Expected: PASS.
- [ ] **Step 5: Lint and types.** `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS.
- [ ] **Step 6: Commit:** `feat(continuity): story graph reviewed links and pending findings`

---

### Task 7: `GET /continuity/graph`; cost, isolation and invariants; the frozen sweep

**Files:**
- Modify:
  - `routes/continuity.py`: the import gains `graph`; a module-docstring sentence ("The Story Graph read (`GET .../continuity/graph`, spec §19.7) is `continuity.graph.build`: one uncapped payload, best-effort locked inside the store function, calendar plugin code outside the hold.");
  - `routes/__init__.py`: the `continuity` docstring row gains `/campaigns/{cid}/continuity/graph`;
  - `tests/fixtures/frozen_campaign/sweep.py`: `continuity.graph.build[{cid}]` after B's `continuity.*` keys;
  - `tests/fixtures/frozen_campaign/snapshot.json`: regenerated deliberately (`home/` is never touched).
- Test: `tests/test_continuity_routes.py`, `tests/test_continuity_graph.py`

**Interfaces:**
- Produces `@router.get("/campaigns/{cid}/continuity/graph") def get_graph(cid: str)`. It calls `_campaign_or_404(cid)`, then `return graph.build(cid)`. It is a plain `def`, so it runs in the threadpool, and it takes no parameters beyond `cid`.
- [ ] **Step 1: Write the failing tests.**
  - In `test_continuity_routes.py`:
    - add `("get", "/continuity/graph", None)` to `test_unknown_campaign_404_on_every_route`;
    - `test_graph_route_is_not_captured_by_entities`: the body has keys `{"now", "nodes", "edges", "omitted"}`;
    - `test_graph_route_matches_the_store`: the body equals `graph.build(cid)`, and `?lens=cast` is ignored (the same body);
    - `test_graph_route_makes_no_model_call` (§28.8): the `test_drivers_route_makes_no_model_call` setup (`embed_space.resolve` returning a dict, `routes.get_llm` overridden), over a campaign with a scene, a thread, an event and an idea, but with **recorders, not raisers**. A raising `_NoEmbeddings` would be swallowed by `_attempt` and by B's `_soft` (both `except Exception`, which catches `AssertionError`), and the route would still answer 200: the E plan records the same trap. So `grimoire.embeddings.EmbeddingsClient` is replaced by a class whose `__init__` appends to a list, and the LLM fake records every call it receives. Assert 200, both counts `0`, and `body["omitted"] == []`, so a failure that was swallowed still shows;
    - `test_graph_route_survives_a_raising_plugin` (Review Focus 2): the `test_drivers_route_survives_a_raising_plugin` setup gives 200, `"calendar" in omitted`, no holiday nodes, and thread nodes present.
  - In `test_continuity_graph.py`:
    - `test_graph_makes_no_model_call`: the store-level twin over `graph.build(cid)`, with the same recorders. Both counts are `0` and `omitted == []`.
    - `test_plugin_code_runs_outside_the_hold`:
      - replace `locks.best_effort_campaign_lock` in `graph`'s namespace with a context manager that writes to a **mutable cell**, `state = {"inside": False}`: it sets `state["inside"] = True` on enter and resets it in a `finally`, and yields `True`. Never a module-level name rebound through `global`: ruff's `PLW0603` is selected, and a new test file has no baseline allowance;
      - wrap `calendars.primary_provider`, `pressure.build`, `drivers.snapshot`, `events.list_events`, `calendars.fixed_of` and `calendars.friendly` (as seen from `graph`; `fixed_of` and `friendly` patched on the `calendars` package) in recorders that append `state["inside"]` at each call;
      - the fixture has a stamped scene and a dated idea, so `fixed_of` and `friendly` are each called at least once (the check is not vacuous);
      - every recorded value is `False`, and the hold was entered exactly once.
    - `test_graph_reads_no_card_or_image`:
      - the fixture: a PC with a birthday, a character in a scene, a location in a history, and a feeling;
      - recorders on `grimoire.store.assets.list_images`, `characters.read_character`, `pcs.read_pc` and `relationships.actor_name` all count 0.
    - `test_reads_do_not_grow_with_the_node_count` (Decision 4): count `plot.read` and `chronicle.read_chronicle` calls for a campaign with one thread and one scene, then for five threads and five scenes. The counts are equal.
    - `test_graph_calls_no_card_image_or_blocking_lock` (§19.7, replacing a grep): parse `graph.py` with `ast`, walk the `ast.Call` nodes, and assert that no call's attribute or name is in `{read_pc, read_character, list_images, actor_name, list_pcs, campaign_lock}`, that no call is `timeline.build`, and that no import binds `timeline`. Comments and docstrings, which Task 2 Step 3 asks to explain why not `timeline.build`, are ignored by construction, and `best_effort_campaign_lock` is a different name, not a substring hit.
    - `test_a_malformed_optional_file_costs_only_its_part` (§28.8): parametrized over `plot.json`, `commitments.json`, `events.json`, `continuity.json`, `continuity_candidates.json`, `relationships.json`, `scene_ideas.json`, `chronicle.json` and `appearances.json`, each written as `"{ no"`. The fixture is seeded with a `thread:mara-s-map pays_off commitment:mara-s-oath` link and a `commitment:mara-s-oath by event:the-coronation` link, so the plot.json and events.json cases exercise a kept link to an unreadable ledger.
      - `build` returns, and scene nodes are present.
      - For every parameter, `{e["from"], e["to"]} <= node_ids` for every edge.
      - The expected part is in `omitted`: `plot`, `commitments`, `events`, `continuity`, `candidates`, `relationships`, `scene_ideas`, `chronicle`, and none for `appearances`, which `scene_actors` absorbs while the chronicle's cast still answers.
    - `test_every_edge_names_two_nodes` (Review Focus 3): one campaign holding every dangling case in Review Focus 3.
      - `{e["from"], e["to"]} <= node_ids` for every edge;
      - `len(node_ids) == len(nodes)` and `len({e["id"] for e in edges}) == len(edges)`;
      - every `kind` is in `NODE_KINDS` / `EDGE_KINDS`, and every `source` is in `EDGE_SOURCES`.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_routes.py tests/test_continuity_graph.py -q -p no:cacheprovider`. Expected: the body-shape route tests FAIL on the entities catch-all (`GET /campaigns/{cid}/{kind}/{eid}` answers `{"detail": "unknown kind"}`), as B's ledger records for the drivers route. The `test_unknown_campaign_404_on_every_route` graph case already PASSES (the catch-all 404s too): it is a regression guard, not a RED test. Any store invariant that fails is fixed in `graph.py`, not in the test.
- [ ] **Step 3: Implement** the route and the docstrings.
- [ ] **Step 4: Run them and confirm they pass.** Use the same command. Expected: PASS.
- [ ] **Step 5: Run the route and module guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_route_order.py tests/test_routing_guard.py tests/test_pydantic_guard.py tests/test_import_guard.py tests/test_lock_domain_guard.py tests/test_continuity_writer_guard.py tests/test_docs_guard.py tests/test_path_guard_store.py -q -p no:cacheprovider`. `test_path_guard_store` enumerates every `{cid}` route from OpenAPI and fuzzes it with a hostile id, so the new route enters that sweep here rather than first at Task 14. Expected: PASS. `continuity` is composed before `entities`, so `CROSSING_PAIRS` needs no entry. If `test_route_order` fails, stop and report rather than editing the pinned table.
- [ ] **Step 6: Add the sweep key and regenerate.** From `backend/`, run `PYTHONPATH=src .venv/bin/python -m tests.fixtures.frozen_campaign.sweep`. Then run B Task 10 Step 4's script with its last assertion changed to `added == ["continuity.graph.build[<the fixture cid>]"]`. Expected: one key added, and no existing key moved. Read the value (Decision 28):
  - the two scenes ordered `001--…` before `2026-01-02-…` (`timeline.build`'s rule), with `order` 0 and 1, and no fabricated date;
  - exactly one `feeling` edge `pcs:winifred → characters:seraphine` with `trust 1`, `affection 0`, `tension 2`, and exactly one `bond` edge with `bond_type "creditor"`, with both actors present as nodes, `pcs:winifred` labelled from the roster (or its id);
  - every edge names two nodes, and `omitted == []`.
- [ ] **Step 7: Run the frozen-campaign tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_frozen_campaign.py -q -p no:cacheprovider`. Expected: PASS, including `test_the_read_only_sweep_writes_nothing`. If that test fails, stop and report: a write means the graph is not read-only.
- [ ] **Step 8: Lint and types.** `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS.
- [ ] **Step 9: Commit:** `feat(continuity): GET /continuity/graph; frozen sweep gains the graph`

---

### Task 8: Frontend types, the client call, and the TS mirror pin

**Files:**
- Modify: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/api/client.test.ts`
- Test (backend): `tests/test_continuity_graph.py`

**Interfaces:**
- `types.ts` (reuse `DriverKind`, `DriverAction`, `PressureState`, `AnchorRelation`, `CandidateKind` and B's `LedgerAlias`; redeclare none of them):
  - `export type NodeKind = "scene" | "character" | "pc" | "location" | "thread" | "commitment" | "event" | "idea" | "birthday" | "holiday";`
  - `export type EdgeKind = "appeared_in" | "occurred_at" | "opened_in" | "advanced_in" | "touched_in" | "closed_in" | "resolved_in" | "involves" | "serves" | "anchored_to" | "feeling" | "bond" | "birthday_of" | "link" | "merged_into" | "possible_duplicate" | "possible_relation";`
  - `export type EdgeSource = "structural" | "reviewed" | "candidate" | "alias";`
  - `export type LinkRelation = "continues" | "subthread_of" | "pays_off" | "before" | "on" | "after" | "by" | "related_to";`
  - `export type EventStatus = "scheduled" | "fired" | "passed" | "undated";`
  - `export type GraphPart = "calendar" | "scenes" | "chronicle" | "plot" | "commitments" | "events" | "continuity" | "candidates" | "relationships" | "scene_ideas" | "names";`
  - `export type GraphDate = { native: string; friendly: string; fixed: number | null; in_days: number | null };`
  - `export type NodePressure = { state: PressureState; in_days: number | null; friendly: string };`
  - `export type ArcFields = { status: string; live: boolean; merged_into: string | null; aliases: LedgerAlias[]; latest_beat: string; pressure: NodePressure | null; focusable: boolean; findings: GraphFinding[] };` with `export type GraphFinding = { id: string; kind: CandidateKind; other: string | null };`
  - `export type GraphNode`, a discriminated union on `kind`, with exactly the Python fields:
    - `({ kind: "scene"; order: number; done: boolean; pcless: boolean; place: string } & GraphDate)`;
    - `{ kind: "character" | "pc" | "location" }`;
    - `({ kind: "thread" } & ArcFields)`;
    - `({ kind: "commitment"; commitment_kind: string; due: string } & ArcFields & GraphDate)`;
    - `({ kind: "event"; status: EventStatus; pressure: NodePressure | null; anchorable: boolean; findings: GraphFinding[] } & GraphDate)`;
    - `({ kind: "holiday"; pressure: NodePressure; anchorable: boolean } & GraphDate)`;
    - `({ kind: "birthday"; actor: string; precision: "exact" | "yearless" | "month"; age: number | null; pressure: NodePressure; anchorable: boolean } & GraphDate)`;
    - `({ kind: "idea"; premise: string; source: string; pcless: boolean; time_anchor: { ref: string; relation: AnchorRelation; native: string } | null } & GraphDate)`;

    each intersected with `{ id: string; label: string }`.
  - `GraphEdge` is discriminated on `kind` for `relation` too (§20: typed unions, not open string bags): `export type GraphEdge = { id: string; from: string; to: string; source: EdgeSource; candidate_id: string | null } & ({ kind: "link"; relation: LinkRelation } | { kind: "serves"; relation: DriverAction } | { kind: "anchored_to"; relation: AnchorRelation } | { kind: "feeling"; relation: null; trust: number; affection: number; tension: number; note: string } | { kind: "bond"; relation: null; bond_type: string; since_scene: string } | { kind: Exclude<EdgeKind, "link" | "serves" | "anchored_to" | "feeling" | "bond">; relation: null });`
  - `export type StoryGraph = { now: { native: string; friendly: string; fixed: number | null }; nodes: GraphNode[]; edges: GraphEdge[]; omitted: GraphPart[] };`
  - **E's `DriverLink.relation` is narrowed from `string` to `LinkRelation`** (§20 "typed unions, not open string bags"; F is the slice that introduces the union, so it owns the narrowing). The backend already sends only `effective.LINK_RELATIONS` values there: `drivers.snapshot`'s links come from `effective.links`. E's plan reads `DriverLink.relation` in no frontend consumer (`pressureControls`, `NewSceneChooser` use `links` only as a type), so `tsc` is the whole check. If an E consumer or test fixture does assign an arbitrary string, fix a fixture literal in place; anything that genuinely needs an open string is recorded as a hand-off in this slice's ledger rather than widened back.
- `client.ts`: `continuityGraph: (cid: string) => request<StoryGraph>("GET", \`/api/campaigns/${encodeSegment(cid)}/continuity/graph\`, undefined, { fresh: true })`. A comment says it is fresh because a lens change never re-reads, so the one read a mount makes must not be a deduped stale one.
- [ ] **Step 1: Write the failing tests.**
  - `client.test.ts`: `continuityGraph reads fresh and encodes the campaign`, in the `getAlternates` style. `request` shares only **in-flight** GETs, so two awaited calls fetch twice with or without `fresh`; the test must overlap them. Make the first fetch a deferred promise (`mockImplementationOnce`), call `api.continuityGraph("a b")` twice without awaiting, and assert `fetchMock` was called twice with GET `/api/campaigns/a%20b/continuity/graph`. Then resolve both.
  - Backend, `test_continuity_graph.py::test_ts_mirrors_the_graph_tuples`. Reuse E's union parser from `tests/test_suggest_controls.py` (import it; if it is private, lift it into `tests/ts_unions.py` and make both tests import it). Assert, as tuples in declaration order:
    - `NodeKind == graph.NODE_KINDS`;
    - `EdgeKind == graph.EDGE_KINDS`;
    - `EdgeSource == graph.EDGE_SOURCES`;
    - `LinkRelation == effective.LINK_RELATIONS`;
    - `EventStatus == graph.EVENT_STATUSES`;
    - `GraphPart == graph.PARTS`.

    A missing union fails by name.
  - `model.test.ts` is not written yet; the `relation` discrimination is pinned by a type-level case in `client.test.ts`: narrowing an edge on `kind === "link"` lets `const r: LinkRelation = e.relation` typecheck (checked by `tsc` in Step 5). A second type-level line pins the driver narrowing: `const d: LinkRelation = ({} as DriverLink).relation` typechecks, and `// @ts-expect-error` precedes `const bad: DriverLink["relation"] = "not_a_relation"`.
- [ ] **Step 2: Run them and confirm they fail.** From `frontend/`: `npx vitest run src/api/client.test.ts`. From `backend/`: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_graph.py tests/test_suggest_controls.py -q -p no:cacheprovider`. Expected: FAIL (no client call, no unions).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them and confirm they pass.** Use the same two commands. Expected: PASS, with E's mirror test still green.
- [ ] **Step 5: Typecheck and eslint.** From `frontend/`, `npx tsc --noEmit -p .` (this is also what checks E's consumers of `DriverLink` against the narrowed `relation`). From the repo root, `make check-eslint PY=$PWD/backend/.venv/bin/python`. Expected: PASS.
- [ ] **Step 6: Commit:** `feat(api): story graph types and read; TS unions pinned to the Python tuples`

---

### Task 9: `storyGraph/model.ts` — lenses, the visible set, names, handoff and ledger targets

**Files:**
- Create: `frontend/src/components/storyGraph/model.ts`, `frontend/src/components/storyGraph/model.test.ts`, `frontend/src/testkit/storyGraph.ts` (the shared fixture: CLAUDE.md puts scaffolding two suites use in `testkit/`, which coverage excludes; a fixture exported from `model.test.ts` would re-register that file's tests in every importer)

**Interfaces** (pure; no React; imports types, `whenPhrase` and `ChooserSeed` from `../pressureControls`, `LedgerTarget` from `../../ledgerPaths`, and `KIND_PHRASES` from `../continuity/labels`, a types-only leaf):
- `export const LENSES = ["story", "cast", "calendar", "continuity"] as const; export type Lens = typeof LENSES[number];`
- `export const LENS_LABELS: Record<Lens, string>` ("Story", "Cast", "Calendar", "Continuity").
- `export type Show = { actors: boolean; locations: boolean; merged: boolean; candidates: boolean };` and `export const SHOW_LABELS: Record<keyof Show, string>` ("Actors", "Locations", "Merged records", "Review candidates").
- `export const PRESETS: Record<Lens, { nodes: NodeKind[]; edges: EdgeKind[]; show: Show; axis: "play" | "calendar" }>`: Decision 20's table, with Calendar on `"calendar"` and the rest on `"play"`.
- `export function isLens(v: string | null): v is Lens`.
- `export function splitRef(ref: string): [string, string]`: the prefix and everything after the **first** `:` (`["", ref]` when there is none).
- `export type GraphIndex = { byId: Map<string, GraphNode>; out: Map<string, GraphEdge[]>; into: Map<string, GraphEdge[]> }` and `export function indexGraph(g: StoryGraph): GraphIndex`.
- `export function visible(g: StoryGraph, ix: GraphIndex, lens: Lens, show: Show, arc: string | null): { nodes: GraphNode[]; edges: GraphEdge[] }`: Decision 20, in payload order.
- `export function arcNeighbourhood(ix: GraphIndex, arc: string): Set<string>`.
- `export const KIND_NOUN: Record<NodeKind, string>` ("scene", "character", "player character", "location", "thread", "commitment", "event", "scene idea", "birthday", "holiday").
- `export function statusOf(n: GraphNode, ix: GraphIndex): string`:
  - scene: `done ? "absorbed" : "in play"`;
  - character, pc and location: `"<n> scene(s)"`, from `appeared_in` / `occurred_at`;
  - thread and commitment: `status`, then `", " + STATE_WORDS[pressure.state]` when the pressure is not `ok`, then `", " + FINDING_PHRASE[f.kind]` for each **lifecycle** finding in `n.findings` (`possible_thread_closure`, `possible_commitment_resolution`; in stored order, each phrase once), or `"merged into <canonical label>"` when merged. Lifecycle findings name one ref, so they are never edges; without this, a thread with a pending "May be finished" would draw identically to one without, and half of §19.5's Continuity "pending candidates" would be invisible in the drawing. **The rule applies on every lens, whatever the Review candidates toggle says.** That toggle governs candidate *edges*, which are drawing clutter; a phrase on the node's own status line adds none. Applying it always also keeps `statusOf` a function of `(n, ix)`, so the Arcs row, the node's status line and its accessible name cannot disagree across lenses. Pair findings stay out of `statusOf`: they are edges, and the detail restates them (Decision 26);
  - event: `status === "scheduled" ? whenPhrase(in_days) : status`;
  - holiday and birthday: `whenPhrase(in_days, precision)`;
  - idea: `fixed === null ? "unscheduled" : whenPhrase(in_days)`.
- `export const STATE_WORDS`, `PART_LABELS`, `FINDING_PHRASE` and `RELATION_PHRASE` (Global Constraints copy).
- `export function accessibleName(n: GraphNode, ix: GraphIndex): string`: `` `${n.label}, ${KIND_NOUN[n.kind]}, ${statusOf(n, ix)}` ``.
- `export function seedFor(n: GraphNode, action: "focus" | "anchor"): { chooser: ChooserSeed }`: `{chooser: {drivers: {[n.id]: "focus"}}}`, or `{chooser: {anchor: {ref: n.id, relation: "on"}}}`.
- `export function ledgerTarget(n: GraphNode): LedgerTarget | null`: threads give `{section: "threads", row: splitRef(id)[1]}`, commitments give `{section: "commitments", row}`, and anything else gives `null`.
- `export function arcRows(g: StoryGraph): GraphNode[]`: canonical threads, then commitments. Within each, live before closed, then label case-insensitively, then id.
- [ ] **Step 1: Write the fixture, then the failing tests.** `testkit/storyGraph.ts` exports `graphFixture(): StoryGraph`, hand-built, with these ids pinned (Tasks 10, 12 and 13 import it rather than re-describing it):
  - scenes `scene:001--saltmarch-harbour`, `scene:002--mara-s-flashback`, `scene:003--winifred-s-chart` (`order` 0–2; scene 1 with a later `fixed` than scene 2);
  - actors `characters:mara`, `characters:winifred`, `pcs:seraphine`; the location `locations:saltmarch-harbour`;
  - threads `thread:mara-s-map` (open, with `appeared_in`, `involves` and movement edges), `thread:winifred-s-chart` (merged into it), `thread:mara/s:map` (closed), and the commitment `commitment:mara-s-oath` (due in 4 days) plus one commitment with no deadline;
  - events `event:the-coronation` (scheduled, in 3 days), one fired event, and one undated event;
  - `holiday:739000:Saltmarch Eve` (in 2 days), a month-precision birthday (`precision: "month"`, `fixed: null`, `in_days: null`, `native: ""`), a dated idea (in 1 day) and an undated idea;
  - one `possible_relation` candidate edge `thread:mara-s-map` → `commitment:mara-s-oath` (both ends visible in Story with Merged off), whose finding sits on both nodes with `other` set;
  - one `possible_thread_closure` finding `{id, kind: "possible_thread_closure", other: null}` on `thread:mara-s-map` only (a lifecycle finding: no edge), so its status reads "open, May be finished" (with any pressure word between).

  Then the tests (`model.test.ts`):
  - `splitRef keeps everything after the first colon` (`"thread:mara/s:map"` → `["thread", "mara/s:map"]`; `"holiday:739000:Saltmarch Eve"` → `["holiday", "739000:Saltmarch Eve"]`).
  - `each lens preset shows its kinds` (Story has no actor nodes and Cast has them; Calendar has no thread nodes; Continuity has the merged node and the candidate edge).
  - `toggles add actors, locations, merged records and candidates`.
  - `unchecking Actors in Cast removes the actors` (the Cast preset lists none of its own).
  - `the Calendar lens shows only commitments with a deadline`: the no-deadline commitment is absent; a commitment with `native` set and `fixed: null` is present (it goes under Undated, Task 10).
  - `every edge kind a preset lists can draw`: for each lens, each listed edge kind has both end kinds among the lens's node kinds or a toggle's.
  - `merged records and candidate edges are hidden until shown`.
  - `the Story lens drops fired and passed events`.
  - `an edge shows only when both ends do`.
  - `the arc filter shows the arc, its merges, link neighbours, touched scenes and involved actors`, with the Merged toggle **off** (the arc filter's precedence, Decision 20); without an arc filter the merged node stays hidden.
  - `statusOf and accessibleName include the label and the status` (one case per kind).
  - `a lifecycle finding is named on its node's status`: `accessibleName(<thread:mara-s-map>, ix)` contains "May be finished"; a thread with no finding does not; a pair finding (`possible_relation`) adds no phrase to either end's status; the result is the same whatever lens or toggle a caller is on (`statusOf` takes neither).
  - `no shown string carries an internal token`: for every fixture node, `statusOf` and `accessibleName` contain no `_` and no `<prefix>:` ref; every `PART_LABELS`, `STATE_WORDS`, `FINDING_PHRASE` and `RELATION_PHRASE` value contains no `_`.
  - `seedFor builds exactly the two section 16.5 shapes`. Each equals its literal, and `sanitizeSeed(seedFor(n, a))` deep-equals `seedFor(n, a).chooser` (E's validator accepts what F sends; it takes the whole history state `{chooser}` and reads `.chooser` itself, per E's Task 11 ruling, so passing `.chooser` would always give `null`).
  - `ledgerTarget of an odd thread id` (Review Focus 1): `ledgerHref("run", ledgerTarget(node)!)` ends `/ledger/threads/mara%2Fs%3Amap`, and events give `null`.
  - `arcRows order`.
- [ ] **Step 2: Run them and confirm they fail.** From `frontend/`: `npx vitest run src/components/storyGraph/model.test.ts`. Expected: FAIL (no module).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them and confirm they pass.** Use the same command. Expected: PASS.
- [ ] **Step 5: Typecheck and eslint.** `npx tsc --noEmit -p .`, then `make check-eslint PY=$PWD/backend/.venv/bin/python` from the repo root. Expected: PASS.
- [ ] **Step 6: Commit:** `feat(graph): lens presets, visibility, names and handoff state`

---

### Task 10: `storyGraph/layout.ts` — deterministic coordinates on two axes

**Files:**
- Create: `frontend/src/components/storyGraph/layout.ts`, `frontend/src/components/storyGraph/layout.test.ts`

**Interfaces:**
- `export const COL = 184, ROW = 64, NODE_W = 160, NODE_H = 44, PAD = 16, HEAD = 28;` (Decision 21).
- `export type Placed = { id: string; x: number; y: number };`
- `export type Column = { index: number; head: string; kind: "unplaced" | "scene" | "reached" | "now" | "future" | "undated" | "idea" | "unscheduled" | "day" | "not-dated" | "people" };`
- `export type Layout = { nodes: Placed[]; columns: Column[]; nowX: number | null; width: number; height: number };`
- `export function layout(g: StoryGraph, ix: GraphIndex, view: { nodes: GraphNode[]; edges: GraphEdge[] }, axis: "play" | "calendar"): Layout`.
- `export function edgePath(a: Placed, b: Placed): string`: a cubic from `a`'s centre to `b`'s centre, with control points at the horizontal midpoint (`M x1 y1 C mx y1, mx y2, x2 y2`), centres being `x + NODE_W / 2` and `y + NODE_H / 2`.
- The algorithm, since the signature and tests do not fully determine it:

  ```text
  x(col) = PAD + col * COL ; y(row) = HEAD + PAD + row * ROW
  columnOf is computed for EVERY visible node first; the column list is then
  derived from those results, so a bucket column exists iff some node maps to it
  (no node can be assigned a column that was never allocated)
  PLAY AXIS
    cols, left to right:
          "Not in a scene" iff any node maps to it (arcs and actors with no visible
                           scene column; the Continuity lens shows no scene)
          one per visible scene, in `order`
          "Reached"      iff a visible event has status fired|passed
          "Now"          always; it holds ONLY the marker, never a node
          temporal slots: distinct in_days >= 0 of visible unfired, unpassed
               event/holiday/birthday nodes and of commitments with a deadline
               in_days >= 0, ascending; head = whenPhrase(in_days)
          "Undated"      iff any node maps to it
          idea slots: distinct in_days of dated ideas (fixed !== null, any sign),
               ascending; head = whenPhrase(in_days)
          "Unscheduled"  iff any node maps to it (ideas with fixed === null only)
    column of a node:
      scene                -> its own
      event                -> Reached if fired|passed; temporal slot if
                              in_days !== null && in_days >= 0; else Undated
      holiday, birthday    -> temporal slot if in_days !== null && in_days >= 0;
                              else Undated (a month-precision birthday has
                              fixed/in_days null: B gives it no day)
      idea                 -> idea slot if fixed !== null (a past date included);
                              else Unscheduled
      merged node          -> its canonical's column when the canonical is visible
      (merged_into set)       (so it sits directly below it, see lanes); else the
                              thread/commitment rule below (a merged node has no
                              movement edges, Decision 7)
      thread, commitment   -> the commitment's temporal slot if its deadline has
                              in_days >= 0; else the max scene column its movement
                              edges reach; else the last visible scene column;
                              else "Not in a scene"
      character, pc        -> the min scene column its appeared_in edges reach,
                              else "Not in a scene"
      location             -> the min scene column its occurred_at edges reach,
                              else "Not in a scene"
    rows (bands, top to bottom):
      band 0: scenes, and every Reached/temporal/Undated/idea/Unscheduled node,
              stacked per column in NODE_KINDS order then id
      lanes : one row per visible thread/commitment NODE, merged nodes included
              (arcRows lists canonicals only, so it cannot be the whole rule):
                for each canonical in arcRows order that is visible: its lane,
                then directly below it one lane per visible merged node whose
                merged_into is that canonical, ordered by id
                then one lane per visible merged node whose canonical is NOT
                visible, ordered by id (defensive: no play-axis lens hides a
                canonical while showing its merged node, but no node may go
                unplaced)
              (each lane is owned by one node whatever its column, so
               merged y === canonical y + ROW * (1 + its index among its siblings))
      then actors, then locations: stacked per column, in id order, below the last lane
  CALENDAR AXIS (scene, event, holiday, birthday, deadline commitment; no idea)
    cols: distinct `fixed` of every visible dated node with fixed < now.fixed,
          ascending; then "Now" iff now.fixed !== null; then distinct
          fixed >= now.fixed, ascending;
          then "Undated" iff any visible dated-kind node has fixed === null;
          then "Not dated" iff any visible record node carries no date by
               nature: a thread, a commitment with native === "", or any
               merged node (Decision 7: title and status only). The Calendar
               preset shows none of these, but an arc filter replaces the
               lens's node kinds while the lens still chooses the layout
               (Decision 20), and the Merged toggle adds merged nodes, so
               they can be visible here. "Undated" means a date that could
               not be placed; "Not dated" means there was never a date, so
               the two are kept apart rather than merged into one bucket
          then "People and places" iff any actor or location is visible
          (they are not dates, so never filed as "Undated" or "Not dated")
    column of a node (calendar axis):
      thread, merged node, commitment with native === ""  -> Not dated
      commitment with a deadline, scene, event, holiday,
      birthday                -> the day column of its fixed; else Undated
      character, pc, location -> People and places
      (an idea is never visible on the Calendar lens: no preset, toggle or arc
       neighbourhood adds one)
    head of a day column: the `friendly` of its first node, in NODE_KINDS order then id
    rows: stacked per column in NODE_KINDS order, then id
  nowX = x(Now column) + NODE_W / 2, or null when there is no Now column
  width = PAD + (number of columns) * COL ; height = HEAD + PAD + (rows used) * ROW + PAD
  ```
- [ ] **Step 1: Write the failing tests** (`layout.test.ts`, on `testkit/storyGraph.ts`'s fixture):
  - `scenes run left to right in payload order, not by date`: give scene 1 a later `fixed` than scene 2; their x still follows `order`.
  - `the Now boundary is drawn immediately after the last scene on the Story lens`: `nowX` is greater than the last scene's x, and there is no column between them.
  - `future nodes sit right of Now by ascending in_days, then ideas`: a holiday in 2 days, the coronation in 3, and a dated idea in 1 day lie at x = holiday < coronation < idea, all greater than `nowX` (ideas come after every temporal slot, §19.4).
  - `undated ideas go in a trailing Unscheduled column`.
  - `a month-precision birthday goes in Undated and every placed x is finite` (Story lens: the birthday's x equals the Undated column's x, which lies after the temporal slots and before the idea slots; Calendar lens: it is under Undated). Also assert `Number.isFinite` for every placed x and y on every lens.
  - `an undated event goes in Undated, not Unscheduled`.
  - `a past-dated idea takes an idea slot, not Unscheduled`.
  - `the Continuity lens puts arcs left of Reached and Now`: every arc's `x + NODE_W < nowX`, and no arc shares a column index with an event.
  - `no node shares the Now column on any lens`: for each of the four lenses, no placed node's x equals the Now column's x.
  - `row 0 starts below the column heads`: the smallest placed y is at least `HEAD + PAD`.
  - `actors on the Calendar lens go in People and places, not Undated`.
  - `a commitment with a future deadline sits in its temporal slot; a thread sits at its last scene`.
  - `each arc owns its own lane`: distinct `y` per thread and commitment.
  - `a merged node gets its own lane directly below its canonical` (Continuity lens, whose preset turns Merged on, with the fixture's `thread:winifred-s-chart`): every node in `visible(...)` appears in `layout.nodes` with a finite x and y; the merged node's y differs from every other thread and commitment's y; it equals `thread:mara-s-map`'s y + `ROW`; and its x equals the canonical's x.
  - `an arc filter places every node on every lens`: with `arc = "thread:mara-s-map"`, for each of the four lenses take `view = visible(g, ix, lens, PRESETS[lens].show, arc)` and `layout(g, ix, view, PRESETS[lens].axis)`. The set of placed ids equals the set of `view.nodes` ids, every x and y is finite, and no placed x equals the Now column's x. On the Calendar lens, the arc and its merged node sit in the "Not dated" column, which lies after "Undated" (when present) and before "People and places"; a no-deadline commitment made visible the same way (the fixture's, set as the arc) also sits in "Not dated".
  - `a null fixed goes in the Undated bucket` (Calendar; Review Focus 2): with `now.fixed === null` and every `fixed` null, there is one column, "Undated", and `nowX === null`.
  - `the Calendar lens orders by fixed and puts Now at now.fixed`: a scene before now lies left of `nowX`, and a scene after now lies right of it, whatever its `order`.
  - `the layout is a function of its inputs`: two calls are deep-equal.
  - `edgePath joins two centres`.
- [ ] **Step 2: Run them and confirm they fail.** From `frontend/`: `npx vitest run src/components/storyGraph/layout.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement.** For readability, split `layout` into one helper per axis plus `columnOf` and `stack`. (No eslint complexity rule is enabled in `frontend/eslint.config.js`; the per-axis tests above are what hold the behaviour.)
- [ ] **Step 4: Run them and confirm they pass.** Expected: PASS.
- [ ] **Step 5: Typecheck and eslint.** As in Task 9. Expected: PASS.
- [ ] **Step 6: Commit:** `feat(graph): deterministic play-axis and calendar-axis layout`

---

### Task 11: `PageShell` closes the phone column when a page's pick changes

**Files:**
- Create: `frontend/src/testkit/viewport.ts`: `export function setInnerWidth(px: number): () => void` (it overrides `window.innerWidth` and returns the restore), lifted unchanged from `PageShell.test.tsx`'s file-local `atWidth`. It is named so it cannot be confused with `testkit/stylesheet.ts`'s `stylesheet().atWidth`, which returns the CSS text of `@media (max-width: Npx)` blocks, not a width override.
- Modify: `frontend/src/components/PageShell.tsx`
- Test: `frontend/src/components/PageShell.test.tsx` (its local `atWidth` is deleted, and every call site imports `setInnerWidth` instead)

**Interfaces:**
- `PageShell` gains `dismissKey?: string | null`. An effect resets `showColumn` to `false` when `dismissKey` changes, exactly as it already does on a pathname change.
- The docstring's push paragraph gains one sentence: a page whose pick lives in the query rather than the path passes the pick as `dismissKey`, so picking a record still closes the column while a filter keeps it up.
- [ ] **Step 1: Write the failing test.** `a dismissKey change closes the phone column; a re-render with the same key does not`.
  - Use `setInnerWidth(375)`, and a harness whose button changes the key.
  - Open the column, so `.shell` has `show-column`.
  - Re-render with the same key: the column is still open.
  - Change the key: `show-column` is gone.
- [ ] **Step 2: Run it and confirm it fails.** From `frontend/`: `npx vitest run src/components/PageShell.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run it and confirm it passes.** Use the same command. Expected: PASS, with every existing PageShell test unchanged.
- [ ] **Step 5: Typecheck and eslint.** Expected: PASS.
- [ ] **Step 6: Commit:** `feat(shell): PageShell dismissKey closes the phone column on a query pick`

---

### Task 12: The Story Graph page — read, URL state, column, drawing, rail row, route, hotkeys

**Files:**
- Create: `frontend/src/routes/StoryGraphView.tsx`, `frontend/src/components/storyGraph/StoryGraphDrawing.tsx`, `frontend/src/routes/StoryGraphView.test.tsx`
- Modify:
  - `frontend/src/App.tsx`: `import StoryGraphView from "./routes/StoryGraphView"`, and `<Route path="/campaigns/:cid/graph" element={<StoryGraphView />} />` after the sheets route. Its JSX comment says why it is a room: a projection read across the whole campaign, not a drawer over one scene;
  - `frontend/src/shell/rail.ts`: the row after `ledger`, `{ id: "graph", label: "Story graph", icon: "⋈", to: (ctx) => campaignPath(ctx, "/graph"), match: (p, ctx) => !!ctx.cid && isUnder(p, \`/campaigns/${ctx.cid}/graph\`) }`. It has no `tail` and no `tailLabel`, and a comment cites §19.1;
  - `frontend/src/shell/rail.test.ts`, `frontend/src/components/AppRail.test.tsx`;
  - `frontend/src/shell/ShellPayloadContext.tsx`: `useCampaignShell(cid, opts?: { demand?: boolean })`; with `demand: false` the arrival effect does not call `retry()` (Decision 18). Default `true`; no existing caller changes;
  - `frontend/src/index.css`: the `.sg-*` rules.

**Interfaces:**
- `StoryGraphView`:
  - It takes `const { cid = "" } = useParams()` and `const [params, setParams] = useSearchParams()`.
  - The read is held with its campaign: `const [loaded, setLoaded] = useState<{ cid: string; graph: StoryGraph } | { cid: string; failed: true } | null>(null)`. An effect on `[cid]` with a `live` flag runs `void api.continuityGraph(cid).then(...)`. A `retry` counter in the deps serves only the Retry button.
  - It draws only when `loaded?.cid === cid`.
  - Derived values: `lens = isLens(params.get("lens")) ? … : "story"`; `node` is the param when `ix.byId` has it, else `null`; `arc` is the param when it names a canonical thread or commitment node, else `null`.
  - Show state is `useState<{ lens: Lens; show: Show } | null>`. The effective show is that state when its lens matches, else `PRESETS[lens].show` (Decision 19).
  - `setQuery(next: Record<string, string | null>, replace: boolean)` copies `URLSearchParams(prev)` and sets or deletes each key.
  - It calls `usePublishShellContext(name ? { campaign: name, scene: "" } : null)`, with `name` from `useCampaignShell(cid, { demand: false })`.
  - It keeps `const [pick, setPick] = useState(0)`; every Arcs-row click and node click calls `setPick((p) => p + 1)` beside `setQuery`. It renders `PageShell` with `columnLabel="Story graph filters"` and `` dismissKey={`${node ?? ""}#${pick}`} `` (Decision 22).
  - **The column:**
    - `<Link className="column-back" to={\`/campaigns/${cid}\`}>‹ {name ?? "Campaign"}</Link>`;
    - `ColumnSection` "Lens": four `button.column-row` with `aria-pressed`, each `setQuery({lens}, false)`;
    - `ColumnSection` "Show": four `<label className="column-row sg-toggle"><input type="checkbox"/>…</label>`;
    - `ColumnSection` "Arcs", with `count={arcRows.length}`: `button.column-row` per arc with `aria-pressed={node === id}`, each `setQuery({node: id}, true)`. The row's label is the arc label, and its count is `statusOf`.
  - **Main:** `.page-wide` holds:
    - an eyebrow (the campaign name) and `<h1 className="screen-title">Story graph</h1>`;
    - the omitted note (`.field-hint`, through `PART_LABELS`) whenever `omitted` is non-empty, **including** when there are no nodes;
    - with `arc` set, a line "Filtered to <arc label>" with a "Show the whole graph" button (`setQuery({arc: null}, false)`);
    - `<div className="sg-scroll" data-testid="story-graph-drawing">` holding `<StoryGraphDrawing …/>`;
    - after that pane and outside it, `<NodeDetail …/>` (Task 13; this task renders a placeholder `<section className="sg-detail" aria-label="Selected: <label>"><h3>{label}</h3></section>`).
    - Loading shows "Reading the story graph…". The empty state (no nodes **and** empty `omitted`) and the failure use the Global Constraints copy.
  - **Hotkeys:** `useHotkeys([...LENSES.map((l, i) => ({ keys: String(i + 1), label: \`${LENS_LABELS[l]} lens\`, group: "STORY GRAPH", run: () => setQuery({ lens: l }, false) })), { keys: "escape", label: "Clear the selection", group: "STORY GRAPH", enabled: !!node, run: () => setQuery({ node: null }, true) }])`.
  - **On a `node` change:** `detailRef.current?.scrollIntoView?.({ block: "nearest" })`.
- `StoryGraphDrawing({ graph, ix, view, layout, selected, onSelect })`:
  - `<div className="sg-canvas" style={{ width, height }}>`;
  - `<svg className="sg-edges" aria-hidden="true" width height>` with one `<path className={\`sg-edge ${e.source} ${e.kind}\`} d={edgePath(…)} key={e.id}/>` per visible edge;
  - column heads as `<div className="sg-col-head" style={{ left }}>`;
  - the Now marker `<div role="separator" aria-label="Now" className="sg-now" style={{ left: nowX, height }}>Now</div>`;
  - one `<button type="button" className={\`sg-node ${kind}${selected ? " selected" : ""}\`} aria-pressed={selected} aria-label={accessibleName(n, ix)} style={{ left: x, top: y, width: NODE_W }} onClick={() => onSelect(n.id)} key={n.id}>` per node, showing the label and a small `statusOf` line.
  - It uses no `title` attribute.
- CSS:
  - `.sg-scroll { overflow-x: auto; max-width: 100%; min-width: 0; }`;
  - `.sg-canvas { position: relative; }`;
  - `.sg-edges { position: absolute; top: 0; left: 0; overflow: visible; pointer-events: none; }`;
  - `.sg-node { position: absolute; height: 44px; overflow: hidden; }`, with the label and status lines each `white-space: nowrap; overflow: hidden; text-overflow: ellipsis`. A wrapped label would grow the button past `ROW` and over the next lane's button, so a tap would land on the wrong record; the full label is still in the accessible name and the detail;
  - `.sg-edge { fill: none; stroke: var(--muted); }`, `.sg-edge.candidate { stroke-dasharray: 5 4; }`, `.sg-edge.alias { stroke-dasharray: 1 3; }`;
  - `.sg-now { position: absolute; top: 0; border-left: 2px solid var(--accent); pointer-events: none; }`;
  - `.sg-col-head { position: absolute; top: 0; height: 28px; pointer-events: none; }` (the `HEAD` band), `.sg-detail`, `.sg-toggle`. The marker and the heads sit over the canvas, so without `pointer-events: none` their boxes would catch taps meant for node buttons on a touch screen. Colours are tokens only, so dark mode follows.
- [ ] **Step 1: Write the failing tests.**
  - `rail.test.ts`:
    - add `"/campaigns/c1/graph"` to `PATHS`;
    - in "a campaign child lights its own row and not Overview", `expect(activeIn(CAMPAIGN_ROWS, "/campaigns/c1/graph")).toEqual(["graph"])`, with `/campaigns/c1/ledger` still `["ledger"]`;
    - the dead-row list stays `["wrap", "images"]`.
  - `AppRail.test.tsx`: `the Story graph row links to the graph with no tail`:
    - the "Open campaign" nav has a link named exactly `/^story graph$/i`, with `href="/campaigns/c1/graph"`;
    - at that path it has `aria-current="page"`, and the Ledger row does not.
  - `StoryGraphView.test.tsx`:
    - **Harness:** `vi.mock("../api/client", () => ({ api: { continuityGraph: vi.fn() } }))`, so any other API call throws. Render inside a `ShellPayloadProvider` (or the context's provider value) whose `retry` is a `vi.fn()` spy, then `<MemoryRouter initialEntries={[entry]}><Jump/><Routes><Route path="/campaigns/:cid/graph" element={<StoryGraphView/>}/><Route path="/campaigns/:cid/scenes" element={<Probe/>}/>…`, where `Probe` renders `JSON.stringify(useLocation().state)`, `LocationProbe` renders `useLocation().search`, and `Jump` (outside the `Routes`, as `PageShell.test.tsx`'s `Jump` is) is a button calling `useNavigate()("/campaigns/c2/graph")`. The fixture is `graphFixture()` from `testkit/storyGraph.ts`; widths go through `testkit/viewport.ts`'s `setInnerWidth`.
    - `renders the drawing from one graph read`: the scene, thread and event buttons are present.
    - `filter presets: lens rows switch the preset and write ?lens=`: clicking "Cast" makes actor buttons appear, and `?lens=cast` is in the search. Clicking "Story" removes them.
    - `show toggles add actors and locations; merged records and candidates stay hidden until shown`: the merged node's button and `.sg-edge.candidate` are absent, then present after toggling.
    - `tapping a node shows its detail below the drawing`: clicking "Mara's map" gives `?node=thread%3Amara-s-map`, and the `sg-detail` section is named "Selected: Mara's map".
    - `an Arcs row selects its node`.
    - `the Now boundary sits after the last scene and before future nodes`: `getByRole("separator", {name: "Now"})`, and its `style.left` lies between the last scene button's left and the coronation button's left.
    - `at 375px an Arcs row or a node shows its detail in main, outside the drawing`:
      - use `setInnerWidth(375)` before render, with `try/finally` restore;
      - open the column with `‹ Story graph filters`, then click an Arcs row;
      - `.shell` loses `show-column` (Task 11);
      - open the column again and tap the **same, already selected** Arcs row: `show-column` is gone again (the `pick` counter, Decision 22);
      - the detail is inside `getByRole("main")`, and `getByTestId("story-graph-drawing").contains(detail)` is `false`;
      - repeat by clicking a node button;
      - then set the width to 1200 and fire `resize`: `.shell` loses `phone`.
    - `every node is a button named with its label and status`: for every `.sg-node`, the element is a `BUTTON`, its accessible name is `accessibleName(...)`, the name contains both the label and `statusOf(...)`, and it has no `title` attribute.
    - `only one graph read across mount, lenses, toggles, arcs and nodes` (§28.9): click every lens, every toggle, an arc filter, a node and Escape, then `expect(api.continuityGraph).toHaveBeenCalledTimes(1)` **and** the provider's `retry` spy was never called (no shell read either; Decision 18).
    - `?lens= ?arc= ?node= are read from the address; unknown values fall back`: `?lens=calendar&node=event%3Athe-coronation` opens that lens and that detail. `?lens=bogus&node=thread%3Agone&arc=event%3Athe-coronation` reads as Story with no selection and no filter.
    - `a node id with a colon, slash and space round-trips through ?node=` (Review Focus 1): select `holiday:739000:Saltmarch Eve` and `thread:mara/s:map`. Each selects its node, and `new URLSearchParams(search).get("node")` equals the id.
    - `the lens keys and Escape are registered and listed`: `fireEvent.keyDown(window, {key: "2"})` gives `?lens=cast`. With a node selected, `{key: "Escape"}` clears `?node=`. `activeHotkeys().filter(r => r.key.group === "STORY GRAPH").map(r => r.key.label)` equals the five labels.
    - `a campaign switch drops the superseded read` (Review Focus 5):
      - defer the first read: `vi.mocked(api.continuityGraph).mockImplementationOnce(() => new Promise((r) => { release = r; }))`, and resolve the second with a c2 graph whose labels differ from c1's;
      - click `Jump` (same `MemoryRouter`, so `StoryGraphView` stays mounted: the route is not keyed on `cid`; assert the same drawing-pane DOM node is still in the document after the jump);
      - then `release(c1Graph)` and await settle;
      - no c1-only label is drawn, c2's nodes are, and `continuityGraph` was called `toHaveBeenNthCalledWith(1, "c1")` and `toHaveBeenNthCalledWith(2, "c2")`, twice in all.
    - `a malformed part is named, not hidden`: `omitted: ["relationships"]` renders "Some records could not be read: relationships."
    - `an empty graph with omitted parts names them and does not claim an empty campaign`: `{nodes: [], edges: [], omitted: ["scenes", "scene_ideas"]}` renders "Some records could not be read: scenes, saved scene ideas." and no "Nothing to draw yet" text.
    - `the drawing pane scrolls sideways and the main pane does not` (stylesheet): `stylesheet()`'s rule for `.sg-scroll` declares `overflow-x: auto` and `min-width: 0`.
    - `node boxes are fixed-height and overlays let taps through` (stylesheet): `.sg-node` declares `height: 44px` and `overflow: hidden`; `.sg-now` and `.sg-col-head` each declare `pointer-events: none`.
- [ ] **Step 2: Run them and confirm they fail.** From `frontend/`: `npx vitest run src/routes/StoryGraphView.test.tsx src/shell/rail.test.ts src/components/AppRail.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them and confirm they pass.** Use the same command. Expected: PASS.
- [ ] **Step 5: Typecheck and eslint.** As before. Expected: PASS, with zero findings in the new files.
- [ ] **Step 6: Commit:** `feat(graph): Story Graph page, rail row and lens hotkeys`

---

### Task 13: Node detail and its actions — Focus, Anchor, Open ledger entry, Filter to this arc

**Files:**
- Create: `frontend/src/components/storyGraph/NodeDetail.tsx`
- Modify: `frontend/src/routes/StoryGraphView.tsx` (the placeholder is replaced), `frontend/src/routes/StoryGraphView.test.tsx`

**Interfaces:**
- `NodeDetail({ cid, graph, ix, node, arc, onSelect, onArc })` renders `<section className="sg-detail" aria-label={\`Selected: ${label}\`} ref>`, holding:
  - a `.detail-view`, whose `.detail-main` has the `<h3>` label and the facts;
  - an `<aside className="detail-sidebar" aria-label="Node actions">`, whose `.form-actions` hold the actions and whose `.side-section` blocks hold the chips.
- Per kind (§19.6, Decision 23). Every chip that names a record is a `<button className="chip">` that calls `onSelect(ref)`. Plain attributes are `<span className="chip on">` or `.field-hint`.
  - **Thread and commitment:**
    - facts: canonical title, status, latest beat, pressure, commitment kind and due. The pressure line is `STATE_WORDS[state]`, then `whenPhrase(in_days)` **only when `in_days !== null`**, then `friendly` **only when it is non-empty**. A stale or ok reading, and every driver reading without a date, carries `in_days: null` (B Decision 10: "aging `stale` gives `stale` … with `in_days: None` and `friendly: ""`"), and E's `whenPhrase(null)` returns "undated", so an unconditional call would make a stale open thread read "stale … undated", as though a date were missing;
    - side sections: "Merged" (the aliases), "Scenes" (movement edges in scene order, each chip prefixed with its verb: opened, advanced, touched, closed or resolved), "Involves", "Links" (`RELATION_PHRASE[relation].out` for an outgoing link and `.in` for an incoming one, then a chip to the other end), and "Findings": each is `FINDING_PHRASE[kind]`, then, for a pair kind, "with" and a chip to `other` (when `other` is a node), then `<Link to={ledgerHref(cid, {section: "continuity", group: GROUP_OF[kind], candidate: id})}>`;
    - a merged node shows "Merged into <canonical>" as a chip;
    - actions as in Decision 23.
  - **Event, holiday and birthday:**
    - facts: date (`friendly`, else `native`, else "Undated"), `whenPhrase(in_days, precision)`, the event status, and the birthday actor;
    - side section "Linked": the far ends of `link` (with `RELATION_PHRASE`), `anchored_to`, `serves` and `birthday_of`; an event also shows "Findings" as above (a temporal `possible_relation`);
    - the action "Anchor next scene".
  - **Character and pc:** "Scenes" (from `appeared_in`), "Active drivers" (`involves` sources whose node is `focusable`), "Relationships" (feelings with their three meters and note; bonds with type and since when: a chip to `scene:<since_scene>` when that node exists, else "a deleted scene", never the filename), and "Birthday" (a birthday node whose `actor` is this ref; rendered only when one exists, so an actor whose birthday falls outside pressure's horizon shows no Birthday section, Decision 11).
  - **Location:** "Scenes".
  - **Scene:** "Cast" (incoming `appeared_in`), "Where" (`occurred_at`, else `place`), "When" (`friendly` / "Undated"), "Moved here" (incoming movement edges with their verbs), and an "Open scene" link.
  - **Idea:** premise, date, "Serves" (with each target's current `statusOf`), "Anchored to" (the chip; or, when the anchor has no node, its noun from `ANCHOR_NOUN[splitRef(ref)[0]]` (a three-entry table in `model.ts`: `event` → "an event", `birthday` → "a birthday", `holiday` → "a holiday"; anything else → "a date") plus the stored `native`, or "no longer upcoming" when `native` is empty, never the ref itself).
- **Actions:**
  - Focus: `navigate(\`/campaigns/${encodeSegment(cid)}/scenes\`, { state: seedFor(node, "focus") })`;
  - Anchor: the same, with `"anchor"`;
  - Filter: with `const canon = node.merged_into ?? node.id`, `onArc(canon)`, or `onArc(null)` when `arc === canon` (the button then reads "Show the whole graph"). Calling `onArc(node.id)` on a merged node would write a non-canonical `?arc=`, which Decision 19 reads as no filter, so the button would do nothing.

  Disabled buttons carry their hint in a `.field-hint` beneath them, never only in a `title`.
- [ ] **Step 1: Write the failing tests** (in `StoryGraphView.test.tsx`):
  - `a thread's detail shows title, merges, status, latest beat, scenes, actors, pressure, links and findings`.
  - `a pair finding names the record it pairs with`: Mara's map's detail shows `${FINDING_PHRASE.possible_relation} with` ("Possible relation with") and a "Mara's oath" chip; clicking the chip selects `commitment:mara-s-oath`. The detail text contains no `_` and no raw ref.
  - `Focus next scene sends the drivers handoff` (§16.5): Probe shows exactly `{"chooser":{"drivers":{"thread:mara-s-map":"focus"}}}`.
  - `Anchor next scene sends the anchor handoff`: exactly `{"chooser":{"anchor":{"ref":"event:the-coronation","relation":"on"}}}`.
  - `Focus is disabled on a closed thread and absent on a merged record` (Review Focus 4): the closed thread's button is `disabled` with its hint visible. The merged node has no Focus button and has a "Merged into Mara's map" chip.
  - `Anchor is disabled on a fired event`.
  - `the sent state is one sanitizeSeed accepts`: `sanitizeSeed(JSON.parse(probe))` is non-null for both actions (it takes the whole state, `{chooser}`).
  - `Open ledger entry links to the record's ledger row`: the href equals `ledgerHref("run", {section: "threads", row: "mara-s-map"})`, and for the merged node it is its own id's row.
  - `a finding links to its review address`.
  - `Filter to this arc sets ?arc= and narrows the drawing; Show the whole graph clears it`: an unrelated thread's button disappears and comes back.
  - `Filter to this arc on a merged record filters to its canonical`: open `?node=thread%3Awinifred-s-chart` and click "Filter to this arc". The search's `arc` is `thread:mara-s-map` (`?arc=thread%3Amara-s-map`), the same unrelated thread's button disappears, and the same Filter button in the merged node's detail now reads "Show the whole graph"; clicking it clears `arc`.
  - `a stale reading shows its state and no false "undated"`: with `graphFixture()` altered so `thread:mara-s-map`'s `pressure` is `{state: "stale", in_days: null, friendly: ""}`, its detail's pressure line contains `STATE_WORDS.stale` and the detail text does not contain "undated".
  - `a lifecycle finding shows on the node in the Continuity lens`: at `?lens=continuity`, the `thread:mara-s-map` button's accessible name contains "May be finished", and so does its visible status line (Task 9's `statusOf` rule).
  - `an actor with no birthday node shows no Birthday section`: the detail of an actor no fixture birthday node names (pick it from `graphFixture()` by that property, not by id) has no "Birthday" heading; the actor the fixture's birthday node names does.
  - `a chip selects the record it names`: clicking the "Mara" chip in a scene's Cast gives `?node=characters%3A…`.
  - `actor, scene, event, idea and location details show their sections` (one assertion per §19.6 bullet).
- [ ] **Step 2: Run them and confirm they fail.** From `frontend/`: `npx vitest run src/routes/StoryGraphView.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them and confirm they pass.** Use the same command. Expected: PASS, with Task 12's tests still green.
- [ ] **Step 5: Typecheck and eslint.** Expected: PASS.
- [ ] **Step 6: Commit:** `feat(graph): node detail, chooser handoff, ledger links and arc filter`

---

### Task 14: Slice gate

- [ ] **Step 1: Lint, types, eslint and templates.** From the repo root, `make check-lint check-mypy check-eslint check-templates PY=$PWD/backend/.venv/bin/python`. Expected: PASS. This slice touches no template, so `check-templates` must pass unchanged. If a change resolved a recorded finding, run `make baseline` and commit the smaller file.
- [ ] **Step 2: The full backend suite, under its coverage floor.** `make check-py PY=$PWD/backend/.venv/bin/python`. Expected: PASS, including `test_frozen_campaign`, `test_llm_fakes` and `test_evals`. Only failures listed in the ledger by "Before Task 1" step 2 may be excused.
- [ ] **Step 3: The Android dependency set.** `make check-pydantic1 PY=$PWD/backend/.venv/bin/python`. Expected: PASS (`graph` and the route use no pydantic).
- [ ] **Step 4: The frontend gate.** `make check-web`. Expected: PASS (typecheck plus vitest under coverage; the new `storyGraph/` modules are in the denominator, and no scaffolding went into `src/routes/`). Steps 1–4 are every target of `make check`.
- [ ] **Step 5: Evals replay.** `backend/.venv/bin/python evals/run.py` (on Windows, `backend\.venv\Scripts\python.exe evals\run.py`). This is the offline replay of every case. Expected: every recording scores as declared. The slice changes no prompt, so a failure here is a regression to investigate, not a re-record.
- [ ] **Step 6: Re-check the cost rule.** From `backend/`, `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_graph.py -q -p no:cacheprovider -k "no_card_image_or_blocking_lock or reads_no_card_or_image or do_not_grow"`. Expected: PASS. The cost rule is held by Task 7's AST test, not a grep: a grep for `campaign_lock(` matches `best_effort_campaign_lock(`, and one for `timeline\.build` matches the docstring Task 2 asks for, so it could never come back empty.
- [ ] **Step 7: Amend the spec to match the recorded deviations.** Edit `docs/superpowers/specs/2026-10-04-continuity-capstone-design.md`, citing this plan's decision number in each edit:
  - §7.0's `graph` row names its imports, and says it takes `best_effort_campaign_lock` itself with plugin code outside the hold (Decision 1);
  - §19.2: only active ideas; groups and facts deferred; holiday and birthday nodes only inside pressure's horizon (Decisions 6, 11);
  - §19.3: reviewed links are `kind: "link"`; candidate edges take their candidate kind; `serves.relation` is the driver action; feeling and bond typed fields; movement edges unique per `(kind, from, to)`, with `advanced_in` excluding the opening scene (Decisions 8, 12, 13, 14);
  - §19.4: the Reached, "Not in a scene" and play-axis "Undated" columns, "Unscheduled" kept for undated ideas, the calendar-axis "Not dated" column for threads, deadline-less commitments and merged nodes, merged nodes laned directly below their canonical, and ordinal columns (Decision 21; Task 10);
  - §19.5/§19.6: the Calendar lens shows only commitments with a deadline and no ideas; the arc filter shows the arc's aliases whatever the Merged toggle says, recorded against §19.3 (Decision 20); a pending lifecycle finding is named on its node's status line and accessible name on every lens (Task 9);
  - §19.6: the actor detail's "upcoming birthday" is one inside `calendars.UPCOMING_WINDOW_DAYS`, and no Birthday section renders otherwise (Decision 11); "Filter to this arc" on a merged record filters to its canonical (Decision 23);
  - §19.7: "once" read as a constant number of reads with no N+1, and the per-request read list (Decision 4);
  - §20: the node fields added (`order`, `done`, `pcless`, `place`, `live`, `merged_into`, `focusable`, `anchorable`, `findings` as `{id, kind, other}` on threads, commitments and events, event `status`), the top-level `omitted`, `relation` typed per edge kind, and where each tuple lives (Decisions 9, 13, 16, 17);
  - §19.7: location names come from a whole-kind overlay read, handed to G (Decision 5);
  - §19.1: `PageShell.dismissKey` (Decision 22).

  Commit: `docs(spec): continuity capstone matches slice F's recorded deviations`.
- [ ] **Step 8: Implementation → done.** Run `/codex:review` against the slice diff. Resolve the findings, and commit. If Codex is unavailable, record in the ledger the independent reviewer that stood in.
- [ ] **Step 9: Done → actually done.** Run `/codex:adversarial-review` against the diff **and** the spec. Ask specifically whether the diff implements Slice F: §19.1–§19.7, §20's tuples and unions, §21's graph route, §25.3, §28.8 (every bullet), §28.9's Story Graph bullets, §16.5's sending side, §12.1's "Open ledger entry", and AC14/AC16. Look for gaps, drift and quietly dropped requirements. Resolve the findings, and commit. Record a substitute reviewer if Codex is unavailable.
  - Then confirm every `## Inherited hand-offs` item in this slice's ledger is closed (its owning task and commit named) or explicitly deferred (the reason, and where it is now recorded).
  - List this slice's hand-offs to Slice G:
    - the literal-"once" ledger read (Decision 4);
    - narrowing the location-name read to the locations in a history (Decision 5);
    - groups and facts as optional nodes;
    - the CLAUDE.md, CONTRIBUTING and store-guarantees inventory lines, if G's docs task wants the graph named.

---

## Self-review notes

**Spec coverage:**

| Spec section | Task |
|---|---|
| §19.1 placement: route, rail row after Ledger, unused glyph, no tail, no shell field, rail.test PATHS | 12 |
| §19.1 column: Lens, Show, Arcs; Arcs row selects the node | 12 |
| §19.1 main: horizontally scrolling drawing; detail below and outside it | 12, 13 |
| §19.1 URL: `?lens=`, `?arc=`, `?node=` | 12 (Decision 19) |
| §19.2 node kinds (required, plus birthday and holiday markers); dates `native`/`friendly`/`fixed`/`in_days`; top-level `now`; never parse `native`; Undated bucket | 2, 3, 4, 5, 10 |
| §19.2 stored ideas only; no lore dump; relationships as edges | 5, 3 (Decision 6) |
| §19.3 structural edges: every listed kind, with the opened, advanced and closed derivation | 3, 4, 5 |
| §19.3 reviewed, alias and candidate sources; `candidate_id`; no embedding score | 4, 6 |
| §19.4 scene-id order; Now after the last scene; right of Now by `in_days`, then ideas, then Unscheduled; lanes; fired events off the Story spine; Calendar by `fixed` | 2, 9, 10 |
| §19.4 hand-built layout, no dependency; buttons with label and status names; no title-only information; local scroll; detail reachable at phone width | 10, 11, 12 |
| §19.5 four lenses as client presets with no request; Show toggles; hotkeys with label and group | 9, 12 |
| §19.6 thread and commitment detail and its three actions; event, birthday and holiday detail and Anchor; actor and scene detail | 13 |
| §19.7 route; no lens param; uncapped; best-effort lock; names from rosters; no image scans; no model call | 1, 2, 7 |
| §20 the seven tuples in Python; TS unions; backend pin; kind→prefix stated once; no `meta` bag | 2, 8 |
| §16.5 sending side, both shapes | 9, 13 |
| §12.1 "Open ledger entry" through `ledgerHref` | 9, 13 |
| §25.3 graph read cost | 1, 7 (Decision 4) |
| §26 dangling or self-collapsing link omitted from graph edges; calendar unavailable; malformed files | 6, 7 |
| §28.8: scene order with disagreeing `updated` and dates | 2 |
| §28.8: actor and location edges | 3 |
| §28.8: beat edges; involvement edges | 4 |
| §28.8: future and undated `fixed`/`in_days` | 5 |
| §28.8: alias hidden by default with a `merged_into` alias-source edge | 4 (backend field), 9 and 12 (hidden by default) |
| §28.8: reviewed and candidate sources | 6 |
| §28.8: malformed optional file; no model or embedding client | 7 |
| §28.9 Story Graph: presets; tap → detail; Now and future nodes; 375 width plus resize; accessible names with status; one read across interactions | 9, 10, 11, 12 |
| §3.10, AC14, AC16 for the graph | 7, 12 |
| §11.6 writer guard covers `graph` | 6 |
| §27 frozen sweep gains the projection | 7 |

**Deliberate deviations:** listed under "Decisions", and amended into the spec at Task 14 Step 7.

**Open questions (none block execution):**

- Mixed-provider campaigns: `fixed` is always on the **primary** provider (§3.8). A scene stamped in a secondary calendar's notation that the primary cannot parse is shown as Undated. That is honest, but the question of whether such stamps exist in practice is for G's performance and polish pass. It is not answered by measuring a real store.
- A very large campaign draws one wide canvas. The spec forbids a cap (§19.7). If a real campaign proves the DOM too heavy, the fix is to virtualize columns in `StoryGraphDrawing`, never to truncate the payload.

**Type consistency:**

- `NODE_KINDS`, `EDGE_KINDS`, `EDGE_SOURCES`, `EVENT_STATUSES` and `PARTS` (Task 2) and `effective.LINK_RELATIONS` (Task 2) are mirrored by `NodeKind`, `EdgeKind`, `EdgeSource`, `EventStatus`, `GraphPart` and `LinkRelation` (Task 8), and pinned by `test_ts_mirrors_the_graph_tuples`.
- `LinkRelation` types both the graph's `link` edge `relation` and E's `DriverLink.relation`, which Task 8 narrows from `string`; `tsc` checks E's consumers, and a type-level line in `client.test.ts` pins it. So no driver API type carries an open relation string.
- The node field sets are stated once, in Tasks 2–5 for Python and in Task 8 for TS. They are the same names, and every dated kind carries `GraphDate`.
- The edge shape is §20's seven keys plus the `feeling` and `bond` extras (Tasks 3 and 8).
- `pcs.name_of`, `pcs.roster` and `overlay.pc_roster` are defined in Task 1. `pc_roster` is used in Task 3, and `name_of` by `birthdays.gather`.
- `Lens`, `Show`, `PRESETS`, `visible`, `indexGraph`, `statusOf`, `accessibleName`, `seedFor`, `ledgerTarget`, `splitRef` and `arcRows` are defined in Task 9 and used in Tasks 10, 12 and 13.
- `layout`, `edgePath` and the geometry are defined in Task 10 and used in Task 12.
- `PageShell.dismissKey` is defined in Task 11 and used in Task 12.
- E's `ChooserSeed`, `sanitizeSeed`, `whenPhrase`, `DriverKind`, `DriverAction`, `PressureState` and `AnchorRelation`, and D's `ledgerHref`, `GROUP_OF`, `LedgerTarget` and `CandidateKind`, are consumed unchanged and confirmed in "Before Task 1" step 3.

---

## Plan-gate review resolution

The 52 findings are numbered in the order they were received. Each was checked against the code and the spec before it was applied. Several reviewers reported the same defect; duplicates share one row. None was rejected.

| # | Finding (lens) | Verified against | Resolution |
|---|---|---|---|
| 1 | Cost-rule grep can never be empty (guards) | `best_effort_campaign_lock(` contains `campaign_lock(`; Task 2 Step 3's docstring names `timeline.build` | Task 7 gains `test_graph_calls_no_card_image_or_blocking_lock`, an AST walk of `ast.Call` nodes and imports. Task 14 Step 6 runs it instead of the grep. |
| 2 | Module-level `inside` flag needs `global` → PLW0603 (guards) | `ruff.toml` selects `PLW`; no `PLW0603` baseline | Task 7's lock fake writes to a mutable cell, `state = {"inside": False}`, with a `finally` reset. `global` is ruled out by name. |
| 3 | Outside-the-hold pin omits `calendars.friendly` (guards) | Global Constraints lists it; Tasks 2 and 5 call it | `friendly` is added to the recorders, with a fixture that guarantees at least one call. Decision 1 phase (C) names it and forbids computing it in `_files`. |
| 4 | Per-row softening would falsely record `"calendar"` (guards) | `events._UNREADABLE`; `fixed_of("midsummer")` raises `CalendarError` | `_attempt` takes `part: str \| None`, and `None` softens silently (Global Constraints, Decisions 3, 10, 16, Tasks 2 and 5). Task 5's dated-nodes test and a new Task 2 test assert `omitted == []`. |
| 5, 26, 51 | Frozen fixture does have `relationships.json` (guards/behaviour/tests) | `frozen_campaign/.../relationships.json`, `calendar.json` `region`, a legacy scene id | Decision 28 corrected. Task 7 Step 6's read-through checks the scene order, the `feeling` meters (1/0/2), the `creditor` bond, both actor nodes, the edge invariant and `omitted == []`. |
| 6, 31, 43 | `atWidth` is file-local; the shared fixture lives in a test file (guards/ui/tests) | `PageShell.test.tsx:74`; `testkit/stylesheet.ts`'s `atWidth` returns CSS text | New `testkit/viewport.ts` (`setInnerWidth`, Task 11) and `testkit/storyGraph.ts` (`graphFixture`, Task 9, with every id later tasks select pinned, including a month-precision birthday and a both-ends-visible candidate). Tasks 10, 12 and 13 import both. |
| 7, 25, 44 | `F` takes `(cid, native)`; `test_birthday_occurrences` has no `F` (guards/behaviour/tests) | `test_continuity_pressure.py:168`; `test_birthday_occurrences.py:15,28` | Every call is `F(cid, …)`, and Task 2 says so once. Task 1 uses that file's `_campaign(now=…)` and `_now_fixed`, and creates the scene the PC is seated in. |
| 8 | Holiday/birthday ids compared as lists in two orders (guards) | Decision 15 orders by kind then id; `pressure.build` by urgency | Compared as sets, with a non-empty check. |
| 9 | Guard runs skip the guards that see the change (guards) | `test_path_guard_store._id_routes` enumerates `{cid}` routes; `overlay` is a lock-domain module | Task 7 Step 5 adds `test_path_guard_store.py`. Task 1 Step 4 adds `test_lock_domain_guard`, `test_paths_guard` and `test_atomic_guard`. |
| 10, 16 | Reviewed-link edges not filtered against nodes (spec/behaviour) | `effective._classify_links` drops only on `exists(...) is False`; `Ledgers.exists` answers `None` when unreadable | Decision 13 and Task 6 keep a link edge only when both ends are nodes, and say why. New `test_a_link_to_an_unreadable_ledger_draws_nothing` (plot and events cases, with a positive control). Task 7's malformed-file test seeds both links and asserts the invariant per parameter. Review Focus 3 lists the case. |
| 11, 37 | `useCampaignShell` issues a shell read the harness cannot see (spec/ui) | `ShellPayloadContext.tsx`: arrival `retry()` | Decision 18: `useCampaignShell(cid, { demand: false })`, default unchanged. Task 12 renders under a provider with a `retry` spy and asserts it is never called. Recorded as a deviation. |
| 12 | `GraphEdge.relation` is an open string (spec) | §20 "typed unions, not open string bags" | `GraphEdge` is discriminated on `kind`: `link` → `LinkRelation`, `serves` → `DriverAction`, `anchored_to` → `AnchorRelation`, the rest → `null`. Task 8 adds a type-level narrowing check. |
| 13 | Calendar lens shows every commitment (spec) | §19.5 "deadlines" | Calendar keeps a commitment only with `native !== ""`. Model test added; recorded as a deviation. |
| 14 | Play axis files undated events, month birthdays and past ideas under "Unscheduled" (spec) | §19.2 "Undated"; §19.4 "Unscheduled" for ideas | The play axis gains an "Undated" column after the temporal slots. Idea slots cover any dated idea, and "Unscheduled" is only for undated ideas. Layout tests added; recorded as a deviation. |
| 15 | Arc filter vs Merged toggle; Actors toggle dead in Cast (spec) | §19.6 "its aliases" vs §19.3 | Stated precedence: the arc filter shows the arc's aliases (§19.6 over §19.3, recorded). The Cast preset lists no actors or actor edges of its own, so the toggle governs them. Tests for both. |
| 17 | Builders run outside `_attempt`; `split_ref` raises on cast tokens (behaviour) | `canon.split_ref`, `canon.actor_ref`, `involvement.scene_actors` | Decision 14 adds a non-raising `_actor_kind`, used for every actor edge. Every builder runs inside `_attempt` (Global Constraints, Task 2). New `test_malformed_cast_tokens_cost_only_their_edges`. |
| 18, 27, 42 | Month-precision birthday has no column (behaviour/ui/tests) | `birthdays.occurrences` docstring: `month` gives `fixed`/`in_days` None | `columnOf` runs first, and bucket columns are derived from its results. "By construction" removed. Test: a month birthday sits in Undated with a finite x, and every x and y is finite on every lens. |
| 19, 28 | Arcs land in the Reached or Now column; marker and heads catch taps (behaviour/ui) | Task 10's old "last scene column (0 with no scene)"; CSS without `pointer-events: none` | Leading "Not in a scene" column; Now holds only the marker. Tests: arcs left of Reached and Now; no node shares the Now column on any lens. `pointer-events: none` on `.sg-now` and `.sg-col-head`, and a `HEAD` band above row 0, each pinned. |
| 20, 40 | `updated` ties make the scene-order control flaky (behaviour/tests) | `paths.now_iso` one-second resolution; `list_scenes` stable sort over glob order | `updated` is written explicitly in non-play order, and the control asserts the exact `list_scenes` order `[S1, S3, S2]`. Sorted ids remain play order: `_stamp_start_date` keeps the number section. |
| 21, 39 | Location names are a whole-kind read, not a stat memo; one bad file costs every name (behaviour/ui) | `entities.list_entities` reads and parses every file and raises on an unparseable one | Decision 5 corrected and recorded as a deviation, with narrowing handed to G. Three separate `_attempt` calls, plus `test_an_unreadable_location_costs_only_location_labels`. |
| 22 | `closed_in` keys on the literal `"closed"` (behaviour) | `plot.open_threads` keeps case; `effective.is_live` lowercases | Closure is `not effective.is_live(kind, status)`. The test is parametrized with `"Closed"`. |
| 23 | Broken scene listing renders as "play a scene" (behaviour) | Decision 16's purpose | The omitted note renders whenever `omitted` is non-empty, and the empty copy only when it is empty. Test added. |
| 24, 35, 50 | Re-tapping the selected Arcs row leaves the phone sheet up (behaviour/ui/tests) | `PageShell` resets only on a key change | Decision 22: a `pick` counter, and `` dismissKey={`${node}#${pick}`} ``. The 375px test re-taps the selected row. |
| 29 | Findings on event nodes have no field (ui) | D plan: `possible_relation` pairs commitment+event | Option (b): event nodes carry `findings: []` in Python and TS. A Task 6 case covers a temporal `possible_relation`. |
| 30 | A candidate's partner is shown only by a dashed line (ui) | Decision 26's guarantee; `aria-hidden` SVG | Findings carry `other`. NodeDetail renders "<phrase> with <chip>". Task 13 test added. |
| 32 | No §30 wording for findings and link relations (ui) | D plan §30 wording and `labels.ts` | `FINDING_PHRASE` and `RELATION_PHRASE` are in Global Constraints and `model.ts`, reused from D if it exports them, and pinned by a no-underscore test. |
| 33 | Raw state words, part names, refs and filenames reach the page (ui) | `statusOf` appends `pressure.state`; idea anchor and `since_scene` rendered raw | `STATE_WORDS` and `PART_LABELS`. An anchor with no node is shown as a noun plus `native`. `since_scene` is a chip or "a deleted scene". Model tests assert no `_` and no refs. |
| 34 | Calendar lists `anchored_to` that never draws, and files actors as Undated (ui) | Calendar preset has no `idea`; §19.5 lists no ideas | `anchored_to` is dropped from Calendar, and `idea` from the calendar axis. Actors and locations go in a trailing "People and places" column. Model test: every listed edge kind can draw. |
| 36 | Long labels grow node buttons into the next lane (ui) | `.sg-node { min-height }` only | Fixed `height: 44px; overflow: hidden`, with one-line ellipsized label and status, pinned by a stylesheet assertion. |
| 38, 49 | The campaign-switch test names no navigation (ui/tests) | `MemoryRouter` ignores new `initialEntries`; `PageShell.test.tsx`'s `Jump` | `Jump` inside the same router, a `mockImplementationOnce` deferred read, a stable-DOM-node check, and `toHaveBeenNthCalledWith` for c1 and c2. |
| 41 | No-model test cannot fail: raisers are swallowed (tests) | `_attempt`, `drivers._soft` and `pressure._soft` catch `Exception`; the E plan records the trap | Recorders, not raisers, with counts of 0 and `omitted == []`. A store-level twin is added. |
| 45 | `fresh` not pinned by sequential calls (tests) | `client.ts` shares only in-flight GETs | The test overlaps two calls with a deferred first fetch, in the `getAlternates` style. |
| 46 | Merged-source finding test accepts two outcomes (tests) | D `test_verdicts`: alias source → `gone` | Renamed `test_a_pair_finding_on_a_merged_source_is_dropped`, asserting `gone`, no edge and no listing. The "lands on its canonical" claim moves to a lifecycle-finding test. |
| 47 | Expected RED is wrong for the route tests (tests) | B ledger, Task 7: the entities catch-all answers `unknown kind`; the 404 param is green | Task 7 Step 2 expects the catch-all body, and calls the 404 case a regression guard. |
| 48 | PC fixture wording does not match the store (tests) | `pcs._version_ids` globs `<vid>.md`; `_library` already creates `seraphine`, `mara` (tombstoned) and `winifred` | Task 1 reuses `_library`'s PCs and asserts `mara` is absent. The missing-version case unlinks every `<vid>.md`. |
| 52 | The eslint complexity rule does not exist (tests) | `frontend/eslint.config.js` enables no `complexity` | The clause is dropped. The helper split is kept for readability, and behaviour is pinned per axis in tests. |

### Spec-coverage gap pass

Seven gaps from a later spec-coverage pass, numbered after the 52 above. Each was checked against this plan, the B and E plans, and the code before it was applied. None was rejected.

| # | Gap (spec) | Verified against | Resolution |
|---|---|---|---|
| 53 | Merged nodes have no play-axis lane (§19.4, §19.5, §28.9) | Task 9 `arcRows` lists canonical threads and commitments only; Task 10's lane rule iterated `arcRows`; Decision 20 shows merged nodes in Continuity by default, under the Merged toggle, and under every arc filter | Task 10's lane rule now ranges over every visible thread or commitment **node**: each visible canonical in `arcRows` order, then its visible merged nodes directly below it by id, then any merged node whose canonical is not visible (defensive). A merged node takes its canonical's column. New layout test on the Continuity lens: every visible node placed with finite x and y, the merged y distinct from every other arc's and equal to the canonical's y + `ROW`. |
| 54 | Calendar axis gives threads and deadline-less commitments no column (§19.4, §19.6) | Decision 20: an arc filter replaces the lens's node kinds while "the lens still chooses the layout"; Task 10's calendar columns covered dated kinds, Undated and People and places only; its finite-x test ran without an arc | The calendar axis gains a trailing "Not dated" column (`Column.kind` `"not-dated"`), before "People and places", for threads, commitments with `native === ""` and merged nodes. It is kept apart from "Undated" (a date that could not be placed). The column-of rule is stated per kind. New layout test with `arc = "thread:mara-s-map"` on all four lenses: placed ids equal visible ids, every x and y finite, none on the Now column, and the arc in "Not dated" on Calendar. Recorded as a deviation and in Task 14 Step 7's §19.4 amendment. |
| 55 | Filter to this arc on a merged node writes a dead `?arc=` (§19.6) | Decision 23 says "the canonical ref"; Task 13 called `onArc(node.id)`; Decision 19 reads a non-canonical `?arc=` as no filter | Task 13 uses `canon = node.merged_into ?? node.id` for both the write and the "Show the whole graph" comparison; Decision 23 states it. New test: from `thread:winifred-s-chart`'s detail, Filter writes `?arc=thread%3Amara-s-map` and the drawing narrows. |
| 56 | Lifecycle findings are invisible in the drawing (§19.5 "pending candidates", §19.4 no hover-only information) | Decision 13: lifecycle kinds have one ref, so they are never edges; Task 9's `statusOf` named no finding | `statusOf` appends `FINDING_PHRASE` for each lifecycle finding on a thread or commitment, on **every** lens whatever the Review candidates toggle says (the toggle governs edges; `statusOf` stays a function of `(n, ix)`, so the Arcs row, status line and accessible name agree). Pair findings stay out (they are edges). The fixture gains a `possible_thread_closure` on `thread:mara-s-map`. Pinned in `model.test` and a Continuity-lens `StoryGraphView` test. |
| 57 | E's `DriverLink.relation` stays an open string (§20) | E plan Task 8: `DriverLink = { id; relation: string; other; direction }`; E's frontend reads `relation` nowhere; `drivers.snapshot` links come from `effective.links` | Task 8 narrows `DriverLink.relation` to `LinkRelation`; `tsc` checks E's consumers, and `client.test.ts` gains a type-level line plus a `@ts-expect-error` case. Any E consumer that genuinely needs a string is recorded as a ledger hand-off rather than widened back. Type consistency notes updated. |
| 58 | Actor detail's birthday narrowed silently (§19.6) | Decision 11: birthday nodes exist only inside pressure's horizon; `birthdays.occurrences` defaults to `calendars.UPCOMING_WINDOW_DAYS` | Decision 11 records the consequence: the Birthday section renders only when a birthday node names the actor, and is otherwise absent (a "None in the next N days" line was rejected as a TS copy of B's horizon). Task 13 says so and adds a test. Added to the deviations list and to Task 14 Step 7's §19.6 amendment. |
| 59 | A stale record's pressure line reads "undated" (§19.6) | B Decision 10: a thread's `stale` reading has `in_days: None`, `friendly: ""`; E's `whenPhrase(null)` returns "undated" | Task 13's pressure line is `STATE_WORDS[state]`, then `whenPhrase(in_days)` only when `in_days !== null`, then `friendly` only when non-empty. New test with `{state: "stale", in_days: null, friendly: ""}`: "stale" (through `STATE_WORDS`) shown, no "undated". |
