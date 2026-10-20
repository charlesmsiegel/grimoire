# Runtime work

Source: [runs/](../../../../../backend/src/grimoire/routes/runs/), [runner.py](../../../../../backend/src/grimoire/runner.py), [test_scene_freeze.py](../../../../../backend/tests/test_scene_freeze.py).

## Detached runs: a turn outlives the request that asked for it

A dropped connection used to cancel generation. It no longer does — it drops a
subscriber. Handlers start detached runs in these classes; consult the route implementations for the current inventory:

- `turn` — `post_chat`, `post_retry`, `post_regenerate`, `post_replay_turn`,
  `post_roll_proposal`, and the per-response pair in `routes/character_turns/_responses.py`,
  `regenerate_response` (reroll one reply) and `extend_response` (Keep
  writing: continue the last reply as a new variant). All eight synchronous
  streaming handlers are detached now; the eighth, `post_opener`, is a `draft`
  rather than a turn (below).
- `review` — `post_absorb`, `post_audit` and `post_dossiers`. These are not
  streams: each answers **202** with a run to poll and persists its result to
  `store/pending_reviews.py`, because a review's value is a payload nobody has
  written down and losing it costs the longest generation in the app.
- `draft` — `post_opener`, plus the eighteen computing previews: scene
  suggestions and intent, the five image-description drafts (a campaign
  library, a world library, a character version, an entity and a PC), both voice
  anchors, taglines, the campaign capsule for an emergent character
  (`post_campaign_capsule_generate`, #62), both scenario parses,
  character-from-passage, optional character-text refinement, the
  model-catalog refresh, and the model test (`post_connection_test`, which
  refuses without `confirm: true` before anything is sent, and sends each probe
  once through `LLMClient.single`), and the history question
  (`post_history_question`, roadmap 12's E3, a tool loop whose answer is held
  on the run -- `run_draft(pass_run=True)`, since the loop runs under the run's
  id). The eighteen answer **202** and hand back a result held on the run and reaped;
  `post_opener` streams and buffers its frames like a turn. **A draft declares
  no exclusion key** — it neither holds a scene nor is refused by one, which is
  what stops a tagline preview from being able to refuse a chat, and its result
  is deliberately *not* durable: a sentence nobody has agreed to yet is
  regenerable, and storing it would be a second store to keep consistent.

  All eighteen go through **one contract** rather than eighteen variants —
  `runs.run_draft` on the route side (the two scenario parses reserve through
  `runs.reserve_draft`, which it wraps), `common.draft_completion` for the call
  (the catalog refresh and the model test excepted, which list or probe rather
  than complete, and are the two whose run writes beside the connection, and
  the history question, which runs a tool loop), and
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
  post's after it. `post_cache_sync` (`routes/cache.py`, spec 05) runs one
  compiled-cache warm-up, kind `cache-sync`, on `("global",)`, answering
  **202**: one at a time and never merged -- a request while one is live is
  refused 409 `sync_in_flight` naming it, except a repeat of its own attempt
  id -- in a worker thread it never abandons, stopped by `cancel_requested`.
  `post_candidate_investigate` (`routes/investigation.py`, roadmap 12's E2)
  runs one continuity investigation, kind `continuity-investigate`, on
  `("campaign", cid)`, answering **202** under `@computes_only`: one live per
  campaign, adopted by a start for the same finding and refused 409 `busy`
  (naming it) to a start for another, its one write
  `reconcile.persist_investigation` stamping the revision itself.
  The class's other runs are outside the count: a landed
  turn's rolling summary and scene-break check (below), and the
  `tracker-update` that a turn, `post_start_from_greeting` or `post_first_post`
  schedules after its posts are written. The write-through queue
  (`cache_warm`) is not a run at all.

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
  would otherwise be handed the dead record's drafts. A move of the store
  root forgets every run (`runs.forget_root`), the same rule one library
  over: the new tree can hold a campaign of the same slug. A forgotten run keeps
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
