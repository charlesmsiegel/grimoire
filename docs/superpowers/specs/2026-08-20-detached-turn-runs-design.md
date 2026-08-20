# Detached runs: every LLM call survives a locked phone

On Android, locking the phone or switching apps kills whatever the model was
doing. On a laptop this barely registers — another window costs nothing. On a
phone it is the dominant failure of the app, and it is worst on the operation
that takes longest: ending a scene.

This spec detaches **every** LLM job in the app from the HTTP request that
asked for it, lets a client re-attach to one already in progress, and tells the
player on Android when a turn or a review lands.

Scope is deliberately all of them rather than the two that hurt most. There are
25 routes and 10 helpers holding an `LLMClient`, and detaching a subset means
building the machinery anyway and then maintaining two rules about which calls
survive a locked phone. One uniform mechanism is *less* to specify, not more:
the taxonomy of exceptions in an earlier draft of this document — which calls
are runs, which are "deliberately not", and why — collapses into one table of
per-class policy.

It completes two things earlier documents specified and left unbuilt:
`docs/android-architecture.md` §4 ("foreground service during generation",
risk 6, Phase 3) and the foreground-service item in the scope of
`2026-08-18-absorb-performance-design.md`. `ServerService.kt` still carries the
TODO.

It changes no decision in #95 (turn transactionality), #234 (cross-process
locks), #235 (staged edits), #254 (scene write serialization) or #271 (commit
supersession). It moves where those decisions execute, not what they decide.

## Problem

Two failure modes that look identical to the player and are mechanically
opposite.

**A chat turn loses the work.** `routes.streaming._chat_stream` drives
`client.stream(...)` from inside the `StreamingResponse` generator. When the
client goes away, Starlette cancels the task; `_flush_on_abort`
(`streaming.py:83`) shield-writes whatever partial text arrived, and generation
stops. The tokens already spent are kept; every token that would have followed
is never requested. There is nothing still running to re-attach to.

**Ending a scene loses the result.** `routes.scenes.post_absorb` is a plain
JSON endpoint, and as its siblings' docstrings note, *a disconnect does not
cancel a plain endpoint — uvicorn runs it to completion*. So the work survives.
But `post_absorb` is `@computes_only`: by the deliberate design of #235 it
writes nothing, and the entire review — extraction, dossiers, voice drift,
audit, staged edits, `commit_token` — exists only in the response body, staged
in the client until `PUT /chronicle`. A client that is not there to receive it
discards ten minutes of fan-out and every token it cost.

`post_audit` and `post_dossiers` go one worse. Both take `request` and pass
`abandoned=request.is_disconnected` into the budget runner
(`scenes.py:1715`, `scenes.py:2477`), so a backgrounded app actively aborts
them. That predicate was right when a disconnect could only mean the reviewer
walked away; it is exactly wrong once a disconnect routinely means the screen
locked.

**And nothing keeps the process alive.** On Android the server runs inside the
app process (Chaquopy, in-process uvicorn). `ServerService` is a plain started
service and the manifest declares only `INTERNET`, so the OS is free to freeze
or kill the process the moment the activity backgrounds. Fixing the request
lifetime without fixing the process lifetime fixes nothing on the platform that
has the problem.

**There is no notification of any kind.** The only notification the app posts
is `DownloadManager`'s, for character exports.

## Scope

**In.** A run registry that owns LLM jobs independently of requests; re-attach
with replay-from-offset for streaming turns; durable results for the
absorb family; one live run per scene, enforced server-side and reflected in a
disabled composer; foreground-service promotion while any run is live; a local
notification when a run reaches a terminal state.

**Out.** Per-phase progress events for absorb. Resuming a *streaming* run's
frame buffer across a process restart. Anything cross-device: the store's
machine-local locking (`store/proclock.py`) and the synced-folder caveat in
`docs/android-architecture.md` are unchanged, and two devices playing at once
remains unsupported. No cloud component, no accounts, no FCM — §9 of the
architecture doc stands.

### What is a run: subject and class

**Every LLM call is a run.** What differs between them is two orthogonal
properties, and pulling them apart is what makes one mechanism cover all 35
call sites.

**Subject — what the run belongs to.** It cannot be `(cid, sid)`, because most
LLM calls in the tree are not scene-scoped:

| Subject | Call sites |
|---|---|
| `scene(cid, sid)` | chat, retry, regenerate, replay, roll-proposal continuation, opener, absorb, audit, dossiers, rolling summary, scene break |
| `campaign(cid)` | scene suggestions, scene intent, campaign image-description drafts, campaign voice anchors |
| `world(wid)` | character taglines and voice anchors, entity/PC image-description drafts, scenario parse and parse-url |
| `global` | LLM-connection model refresh |

The subject decides the lock a run's persistence takes — `campaign_lock(cid)`
for scene and campaign subjects, nothing for world and global, which write
through their own stores — and it is what a client asks about when it comes
back ("is anything running for this scene / this world?").

**Class — what policy the run gets.** Four, and the whole of the per-kind
behavior lives here rather than being restated per route:

| Class | Members | Exclusive | Result | Notify |
|---|---|---|---|---|
| `turn` | chat, retry, regenerate, replay, continuation | scene key | transcript (already persisted) | yes |
| `review` | absorb, audit, dossiers | scene key (shared with `turn`) | durable `pending_reviews` | yes |
| `background` | rolling summary, scene break | none | own store, fire-and-forget | never |
| `draft` | opener, scene suggestions, scene intent, voice anchors, taglines, image descriptions, scenario parse, models refresh | none | held on the run, reaped | no |

Three consequences worth stating, because each one deletes a special case an
earlier draft needed:

- **`rolling_summary` and `scene_break` no longer need an exemption.** They are
  fired after every turn and not awaited; as `background` they declare no
  exclusion key, so they cannot hold a scene's slot or strand the composer.
  That was previously a written-out warning about a bug the design could
  otherwise ship. It is now structural.
- **The `_ephemeral_stream` bucket disappears.** Its members become `draft`
  runs and stop needing a paragraph explaining why they are outside the
  mechanism.

### The new-scene flow, since it is three runs and not one

Worth spelling out because the obvious guess — that "new scene" is one LLM
call — is wrong in both directions. **Creating a scene makes no LLM call at
all**: `post_scene` (`scenes.py:93`), `post_scene_idea` and
`post_start_from_greeting` are pure store writes. The generation happens on
either side of it:

1. `POST /campaigns/{cid}/scene-suggestions` — `draft`, campaign subject.
2. `POST /campaigns/{cid}/scene-intent` — `draft`, campaign subject.
3. `POST .../scenes/{sid}/create` — no run; a store write.
4. `POST .../scenes/{sid}/opener` — `draft`, scene subject.

**The opener is `draft`, not `turn`, and the distinction is about persistence
rather than importance.** `post_opener` returns `_ephemeral_stream(...)` and
writes nothing; the text reaches the transcript only when the player accepts
it through `post_first_post`, which takes `body.text` and makes no LLM call.
Classing it as a `turn` would claim its result is already in the transcript
when nothing has been written, so a locked phone would leave the run `landed`
with its output owned by no one.

As a `draft` it is re-attachable for the whole reap window, which is the
locked-phone case and a strict improvement on today, where backgrounding
loses the opener outright. Only a process restart loses it, and then the
recovery is the one that already exists: generate again on an empty scene,
which costs one call and no state.

It also takes **no exclusion key**, deliberately: an opener runs on an empty
scene where no turn can be in flight, and re-generating an opener the player
did not like is an ordinary thing to do twice.
- **`turn` and `review` share one exclusion key per scene**, so an absorb
  cannot race a chat turn on the same transcript. That matches what End Scene
  already does by locking the scene, and it is now enforced server-side rather
  than by the client behaving.

### The reusable core

What every run shares — the registry, the frame buffer and its absolute
indices, the subscriber protocol, attempt ids, the failure boundary, cancel
semantics, reaping — is written once. A call site provides three things: its
subject, its class, and a coroutine that does the work. It does not implement
detachment, re-attachment, or cancellation.

Streaming and non-streaming runs differ only in whether that coroutine yields
frames or returns a value; the buffer holds frames for the former and a result
for the latter, and every other property is shared. This is what makes "all of
them" cheaper than "two of them": the second call site costs a class annotation.

## Non-goals worth stating

Nothing here makes grimoire multi-user or multi-process. The registry is
in-process, exactly as `_turn_tokens` already is and for the same stated
reason: it distinguishes jobs racing inside one backend, which is what one
player generates. On Android there is one process by construction.

## Design

### The run

A **run** is one LLM job against one scene. It has:

- `id` — opaque, unique per process.
- `cid`, `sid` — the scene it belongs to. Exactly one campaign, always.
- `kind` — `chat`, `retry`, `regenerate`, `replay`, `continuation`, `absorb`,
  `audit`, `dossiers`.
- `state` — `running`, `landed`, `failed`, `cancelled`.
- `frames` — append-only list of SSE frames, for streaming kinds.
- `result` / `error` — the terminal payload, for computing kinds.
- `started_at`, `ended_at`.

Runs are addressed by `id`, never by `(cid, sid)`. This is the single most
important structural decision in this spec, and it is a correctness decision
rather than a stylistic one: a subscriber that resolves a frame stream by
scene can, in the window between one run ending and the next beginning, attach
a view that believes it is showing scene B to frames produced for scene A.
Addressing by run id removes the class of bug instead of guarding against it.

**But an id-only lookup is not sufficient on its own**, because every run route
also carries a caller-supplied subject in its path. A stale client that sends
scene A's run id through scene B's URL — or a world run's id through a
campaign route — would otherwise stream or cancel the wrong thing while the
interface believes it is acting on what it is showing. So **every lookup
verifies that the run's stored subject matches the path, and returns
`run_gone` when it does not.** Belt and braces, and cheap: one comparison at the top of each
handler.

### The registry

A new module, `routes/runs.py`, holding the registry and the run type. It
imports from `store` but **must not import `routes.streaming` or
`routes.scenes`** — they import it. `test_import_guard.py` requires the module
graph stay acyclic, and this is the edge that would close a cycle.

The registry holds `dict[run_id, Run]`, a `dict[subject, run_id]` index naming
each subject's **most recent** run, and a `dict[exclusion_key, run_id]` for the
classes that declare one. All mutated under one small lock,
get-or-create style, for the reason `locks.campaign_lock` documents: a plain
check-then-act hands two concurrent first callers different answers.

Note that the index is *most recent*, not *live*. Those differ, and the
difference is load-bearing: exclusivity asks whether the indexed run is still
`running`, while `GET .../run` needs to find a terminal one too (see the #95
obligation below). One index answers both; an index of live runs only would
make the second question unanswerable, which is the bug this spec would
otherwise ship.

### What actually runs the runner

The mechanism, stated rather than assumed. A run's task must outlive the
request and must still be supervised, and neither property is free:

`_lifespan` already opens an `anyio` task group (`main.py:157`) for
`_backup_ticker`. **That task group is stashed on `app.state` and runs start
inside it.** Nothing else in this design is acceptable:

- `asyncio.create_task` from a handler orphans the task — no supervisor, no
  shutdown drain, and an exception sink that is a log line at best. The
  spec's claim that `_flush_on_abort` fires on runner teardown is only true if
  something deterministically cancels runs at shutdown.
- A task group opened per request dies with the request, which is the bug.

Starting inside the lifespan group gives the property the whole feature rests
on: the group's `cancel_scope.cancel()` on the way out cancels every live run,
each one unwinds through its existing abort hook, and partials are persisted by
`_flush_on_abort` under its shield — the case that helper was written for and
the only case it will now see. Handlers reach the group through `request.app`,
never through a module global.

The reaper (below) runs on the same group, alongside `_backup_ticker`.

**Every runner is wrapped in its own failure boundary, and this is not
optional.** An `anyio` task group cancels all sibling tasks and propagates the
exception the moment any child raises — so without a boundary, one malformed
scene or one persistence bug would abort every other live run, stop the backup
ticker, and take the exception out through `_lifespan` itself. A single bad
turn would end the process. The boundary catches everything that is not a
shutdown cancellation, records the run `failed`, performs the same terminal
bookkeeping any other failure gets (state, notification, foreground demotion),
logs, and returns normally. Only cancellation is allowed to propagate, because
that is the shutdown path doing its job.

### One run per exclusion key

Only `turn` and `review` declare an exclusion key, and they share it: the
scene. `background` and `draft` declare none and may run freely alongside
anything, which is what keeps a rolling summary or a voice-anchor draft from
blocking play.

Starting a `turn` or `review` for a scene that already has a live one of either
class returns **409** with
`{"detail": ..., "kind": "run_in_flight", "run_id": ...}`. The `kind` field
follows the existing `ApiError` convention in `frontend/src/api/client.ts`, so
the client can tell this apart from every other 409 and attach to the named run
rather than surface an error.

**`streamPost` must be changed to carry the run id through**, or this does not
work at all on the routes that need it most. Its non-2xx path currently builds
`new ApiError(res.status, data.detail ?? res.statusText, data.kind)` — the
decoded body is read and then dropped, so `kind` survives and `run_id` does
not. `kind === "run_in_flight"` alone cannot attach to anything. Either the
error retains the whole payload or `run_id` is lifted onto it explicitly, and
the 409-to-attach path gets a test.

This is a backstop, not the primary mechanism: the composer is disabled while
the scene has a live `turn` or `review`, so a well-behaved client never sends
the second request. Both exist because only the server-side one is a guarantee.
The composer keys on the exclusion key specifically, **not** on "any run for
this scene" — otherwise a background rolling summary would disable it after
every single turn.

**The slot is reserved before the first mutator in every run-producing route** —
not merely before `post_chat`'s append. Ordering, not detail, and the append is
only the most obvious case: a 409 raised after it would strand a post with no
reply and no run, the exact orphan `undo_user_post` exists to prevent, arrived
at by a different road.

The other routes each do destructive setup of their own before a stream is ever
constructed, and a check placed "near the append" would sail past all of it:

- `post_chat` / `post_retry` heal and supersede proposals;
- `post_regenerate` archives and removes the reply it is replacing;
- `post_replay_turn` stages posts;
- `POST .../roll-proposal` resolves a check before building its continuation.

Reserving late in any of those returns `run_in_flight` *after* the destructive
work has already landed, which is strictly worse than not checking at all — the
player is told nothing happened, and something did. **Reserve first, and release
the reservation if the synchronous setup that follows fails**, so a route that
raises on its own validation does not leave a phantom slot holding the scene.

A rejected send must leave the scene byte-identical.

Concurrency across subjects — and across non-exclusive classes on one
subject — is allowed and is the point. It is
already safe: `_turn_tokens` is keyed `(cid, sid)`; `campaign_lock(cid)` is
per-campaign and reentrant; `scenes/locking.py` states that no LLM call is ever
held across it, so critical sections are one file read and one file write; the
prompt is built by the caller before the run starts and passed by value; and
`FenceWatcher`, `StreamRedactor` and the usage meter are per-run instances.
Absorb's phase semaphore is per-absorb (`absorb_concurrency` is documented as
"how many of **one** absorb's LLM calls"), so two absorbs do not contend.

Two runs in different scenes of one campaign serialize briefly at persist time.
That is `_serialized` doing its job, and it is measured in file writes.

### Streaming kinds: tee, don't store-and-forward

The runner appends every SSE frame it produces to `run.frames` **and** the
frames are read from there by whoever is subscribed. A subscriber attached from
the first token reads the list as it grows, in-process; there is no added
latency and no behavioral change on a laptop or a foregrounded phone. Replay
only has content when a client *re*-attaches.

This is cheap because `FenceWatcher` and `StreamRedactor` are stateful
transformers whose output already *is* that frame sequence. Nothing is
re-derived on reconnect; the buffer is the frames as emitted, post-fence,
post-redaction.

`POST .../chat` keeps its current shape — it appends the player's post, then
returns a streaming response. That ordering is the #95 contract that
`client.ts` depends on ("a response arriving means the post exists"), and this
spec does not touch it. The stream's **first frame** becomes
`{"run": {"id": ...}}` so the client learns the id without a second request.

That frame lives in the buffer like any other and is counted by the index, so
a replay from `0` re-delivers it and a client attaching mid-run simply never
sees it (it already has the id — that is how it addressed the stream). The
`ChatEvent` union in `api/stream.ts` grows one variant; every consumer must
keep ignoring events it does not recognise, which `parseSSEChunk` already does
for SSE comments.

### The persist hooks move; they do not change

`_chat_stream`'s `finalize`, `on_error` and `on_abort` already run under the
campaign lock, never touch the socket, and only *return* frames. They move to
the runner unchanged. The turn-token claim, the `owned_tail` read, the
restore/undo transactionality and the roll-fence ordering all come along
as-is — which is why this is tractable at all.

### Disconnect stops meaning cancel

The one real semantic change, and it applies on the laptop too.

- **Streaming kinds.** A closed socket detaches the subscriber and nothing
  else. Cancellation becomes explicit: `POST .../runs/{run_id}/cancel` drives
  the existing `on_abort` path. `_flush_on_abort` survives unchanged and still
  wraps that hook — it now fires on the two events that actually tear a runner
  down, an explicit cancel and server shutdown, rather than on any closed
  socket. Its shield is what makes both safe, since in each case the
  cancellation is already in flight.

  **Cancel signals the task but the run stays live until the abort hook has
  finished.** The state moves to `cancelled` only after `on_abort` returns, and
  the cancel response does not resolve before that transition. Freeing the slot
  at the moment of the *request* would let a player who presses Stop and
  immediately sends again start a new turn while the old one is still
  persisting its partial or restoring a removed reply — two writers on one
  transcript, which is the corruption `_serialized` and the turn-token machinery
  exist to prevent. It would also let the foreground service demote mid-cleanup
  and expose that write to process death.
- **Absorb family.** `abandoned=request.is_disconnected` becomes a predicate
  reading the run's cancellation instead of the socket's. It keeps the shape
  the budget runner already expects — `request.is_disconnected` is an
  awaitable, and the replacement must be one too, rather than a bare bool that
  quietly makes `Abandoned` unreachable. Without this change, backgrounding the
  app still aborts the absorb, which is the bug.

The player-facing consequence: closing a tab or navigating away no longer stops
a turn. Cancel does. The affordance already exists.

### Computing kinds: the result has to be durable

For `absorb`, `audit` and `dossiers`, the run's value is a payload nobody has
written down. Losing it costs the whole ten minutes, so it is persisted; a chat
turn's reply is already in the transcript by the time the run ends, so its
frame buffer is a convenience whose loss costs the live tail and not the reply.
Asymmetric value, asymmetric treatment.

A new store module, **`store/pending_reviews.py`**, holds at most one pending
review per `(cid, sid)`: the absorb payload verbatim, including its
`commit_token`. Written through `store.atomic` and classified in
`locks.DOMAIN_MODULES` with its mutators taking `campaign_lock(cid)`.

**`_absorb_snapshot` stays in the handler, synchronously, and its result is
handed to the run.** This falls out of the pre-flight rule below rather than
competing with it: `_already_absorbed(scene)` is judged from that same
snapshot, and it has to raise its 409 before a token is spent. Taking the
snapshot in the handler and passing it in satisfies both — validation stays
synchronous, and the epoch is read before any of the scene state the review is
built from, which is #271's actual requirement.

What must *not* happen is minting the token from an epoch read when the result
is collected. Today handler-entry and result are the same instant so the
distinction is invisible; detached they are minutes apart, and an epoch read at
collection would date the response instead of the snapshot — letting a review
built from pre-save state pass its own supersession check, which is precisely
what #271's comment warns against.

Staleness is otherwise already solved and must not be re-solved. The
`commit_token` is minted with the scene's commit epoch captured at run start, and
`PUT /chronicle` already rejects a token whose epoch has been overtaken —
that is #271, and it is exactly the check a review that sat on disk needs. The
End Scene view checks the epoch on *read* so it can say "the scene changed,
re-run" instead of letting the reviewer fill in a form that will be refused;
the existing token check at save remains the guarantee.

On disk it is a per-scene file beside the transcript under the campaign's
`scenes/` tree, reached through a `store/paths.py` resolver like everything
else — `test_paths_guard.py` requires it and a hand-built path would fail
there.

**The runner rechecks that the scene still exists, under the same
`campaign_lock(cid)` hold as the pending-review write.** The guards cited under
scene deletion protect *transcript* persistence; a pending review is a sidecar,
so nothing stops its terminal write from recreating a file under a scene id the
delete cascade has already swept. The run is marked `failed` and publishes
nothing when the scene has gone.

**Retry results merge into the stored review; they never replace it.** A
single record persisted verbatim would be wrong for two of the three computing
kinds: `post_audit` returns `{mechanics, edits}` and `post_dossiers` returns
`{dossiers, edits}` — partial payloads that `useSceneReview` folds into an
existing absorb. Writing either one whole would destroy the absorb's prose, its
staged edits and its `commit_token` — the token being the part nothing else can
reconstruct. So a retry run's terminal persist is a **read-modify-write of the
existing pending review under `campaign_lock(cid)`**, and it fails rather than
inventing a review if none is stored.

**The merge is defined at edit-row granularity, because neither retry owns the
whole `edits` key.** "Fold in the keys this retry owns" is too coarse and would
silently discard unrelated staged work. The stored merge must reproduce what
the client already does on screen, or reopening a pending review would differ
from the review that was in front of the reviewer:

- **audit** replaces `mechanics`, updates the `audit` row of `phases` (a
  projection of `mechanics`, so it moves with it), and replaces **only the
  `sheet` edit rows** — `rows.filter(r => r.kind !== "sheet")` plus the new
  ones (`useSceneReview.ts:452`).
- **dossiers** replaces `dossiers`, updates the `dossiers` row of `phases`,
  and replaces **only the dossier rows whose target appears in
  `res.dossiers.proposed`** — `rows.filter(r => r.kind !== "dossier" ||
  !reproposed.has(r.target.id))` plus the new ones (`:533`). Prior proposals
  for NPCs this retry did not re-propose are deliberately preserved, which is
  what makes a partially-failed dossier phase recoverable at all.

Getting either rule wrong fails quietly: the review still opens, it is just
missing edits the reviewer had already seen, or reporting a phase status the
retry has since superseded.

### Cancellation and the terminal persist must not race

The reviewer's Cancel (`DELETE .../pending-review`) and a run's terminal write
are two operations on one record, and left unordered they lose to each other:
the reviewer cancels while an absorb is finishing, the DELETE lands, and then
the runner publishes and **recreates the review the player just dismissed**. A
cancelled review that reappears minutes later is worse than one that was never
saved.

One ordering, stated so the implementation cannot pick another: **cancellation
is recorded on the run before the record is deleted, and the terminal persist
checks that flag and suppresses itself — both under the same
`campaign_lock(cid)` hold**, so the check and the write cannot be split. A run
already past its persist when Cancel arrives is fine: the DELETE then removes a
record that exists, which is the outcome the player asked for.

**Which run gets flagged has to be unambiguous, and needs its own identifier.**
The obvious readings both fail: the pending payload is the absorb result
verbatim and names no producer, and the DELETE route carries no `run_id` — so
"flag the scene's most recent run" would cancel an unrelated live *chat* run
that happens to be newer, and "flag the run named by the stored record" finds
nothing at all before the absorb has published one. Both are worse than not
flagging.

So a **review-generation id** is minted when an absorb run starts, carried on
the run, and stored on the pending review; `DELETE .../pending-review` takes it
(or the run id) and flags **only a matching absorb-family run**. Deletion stays
idempotent — a DELETE naming a generation that has already gone removes nothing
and reports success, because the reviewer's intent is satisfied either way.

A pending review is otherwise cleared when its scene is absorbed or when a
fresh absorb replaces it — and the clearing belongs to **both** paths through
`PUT /chronicle`, not only the fresh-success tail. The commit is idempotent by
design (#235): a replay of a save whose first response was lost returns the
recorded result through the `prior["done"]` early return. If a process exits
after recording the commit but before deleting the review, cleanup that lives
only on the first-execution path never runs, and an obsolete review stays
retrievable forever for a scene that is demonstrably absorbed. The delete goes
on the completed-save path too, under the same campaign lock.

### Renaming a scene must carry its pending review

A pending review is keyed by `sid` and stored beside the transcript, and
renaming a scene changes that id. Once a run has landed the scene is no longer
locked, so renaming before saving the review is ordinary use, not an exotic
race — and without an explicit integration `GET .../{new_sid}/pending-review`
returns 404 while the durable review sits orphaned under the old id.

So `pending_reviews` joins the **`scene_refs.repoint` fan-out** alongside the
other scene-id-keyed stores, and is named in `store/cascade.py`. That module is
not a courtesy list: it says outright that *"a store that persists a scene id
and is not named here is one nobody has decided about."* A new sid-keyed
sidecar that skips it is precisely the omission that sentence was written to
catch, so this is an obligation rather than a nicety — and it belongs in the
guard table below for the same reason.

### A scene deleted under a live run

`delete_scene` (`scenes.py:329`) can land while a run for that scene is in
flight. The run must not resurrect it. The persist hooks already refuse to
write to a scene that moved out from under them — `on_abort` checks both the
turn token and the transcript tail, and `_persist_reply` reads the scene first
— so the existing failure is a raise, not a resurrection. The registry marks
such a run `failed` rather than leaving it live and holding the slot for a
scene that no longer exists, and its pending review, if any, is removed with
the scene.

### Routes

New, under the existing scene prefix:

- `GET  /api/campaigns/{cid}/scenes/{sid}/run[?attempt=<id>]` — with
  `attempt`, the run carrying that attempt id, or 404: the only form that
  answers "did my send land?". Without it, the scene's **most recent run
  within the reap window**, live or terminal, which is what a fresh view uses
  to discover a run in progress. Returns
  `{id, kind, state, next_index}`, where `next_index` is meaningful for
  streaming kinds only and is absent for the absorb family, which has no frame
  buffer to offset into. It reports the live tail and is **not** what a
  reconnecting client resumes from — see the frontend section. Terminal runs
  are included deliberately; see below.
- `GET  /api/campaigns/{cid}/scenes/{sid}/runs/{run_id}/stream?from=N` — SSE;
  replays frames from `N`, then tails.
- `GET  /api/campaigns/{cid}/scenes/{sid}/runs/{run_id}` — terminal payload for
  computing kinds: `{state, result?, error?}`.
- `POST /api/campaigns/{cid}/scenes/{sid}/runs/{run_id}/cancel`.
- `GET  /api/campaigns/{cid}/scenes/{sid}/pending-review` — the stored review
  for this scene, or 404. **Not reachable through the run**, deliberately: a
  review outlives its run by design (disk versus a ten-minute registry), so
  after a reap the run is `run_gone` and the review is still there. Without
  this route the durability the whole absorb half of this spec buys would be
  unreachable an hour later, which is when the player actually comes back.
  Paired with a `DELETE` for the reviewer's Cancel.

Changed:

- `POST .../absorb`, `.../audit`, `.../dossiers` return **202** with
  `{"run_id": ...}` once the run is accepted, instead of blocking for the
  duration. The client polls `GET .../runs/{run_id}` every few seconds while
  the view is visible. Polling rather than SSE because absorb has no token
  stream to watch and its four phases finish out of order; a progress feed is a
  separate feature, deliberately out of scope. A backgrounded WebView does not
  poll at all, which is fine and is the point — the run does not depend on
  being watched, and the client resumes polling (or finds the finished result
  in one request) when it comes back.
- `POST .../chat`, `.../retry`, `.../regenerate`, `.../replay/turn` and
  `POST .../roll-proposal` (`mechanics.py:110`, the route that reaches
  `_continuation_stream`) keep their shape and gain the leading `run` frame.
  Each also accepts a **client-generated attempt id**, recorded on the run so
  recovery can ask "did *my* send land?" rather than "has anything run here
  lately" — see the #95 obligation.

### Pre-flight errors stay synchronous. This is not negotiable.

**Every check each computing endpoint performs today before spending a token
runs before its 202**, with status and `kind` intact. The rule is per-endpoint,
not just absorb's — all three detach, and each guards something different:

- `post_absorb`: 409 `already_absorbed` (the #235 guard), 400 "nothing to
  absorb", 404 unknown scene or campaign, and `_require_connection()`.
  **Not** a missing-module check: `post_absorb` deliberately has none, because
  a module-less campaign is still absorbable — `_run_audit` returns a skipped
  mechanics block with reason `"no module"` (`scenes.py:1138`) while
  extraction, dossiers and voice all complete normally. Adding one here would
  reject End Scene outright for those campaigns.
- `post_audit`: `_require_scene`, `_require_connection`, and 400 when
  `store.modules.resolve(cid)` is None.
- `post_dossiers`: `_require_scene`, `_require_connection`, and 400 "nothing to
  build dossiers from" on an empty transcript — a guard the audit deliberately
  does not have, because an audit with nothing to audit finds nothing while a
  dossier phase would stage a proposal overwriting a real dossier with
  invention.
- **Both retries additionally require a stored pending review to exist.** Their
  terminal step is a merge into one, so without it the run is guaranteed to
  fail at the end — and a 202 that accepts work whose result the design has
  already promised to refuse is exactly what the pre-flight rule exists to
  prevent. A stale tab retrying after the reviewer cancelled would otherwise
  spend a full budget on an unusable answer. A review deleted *after*
  acceptance is a different case and keeps the cancellation ordering below.

`already_absorbed` is the one with teeth: `useSceneReview.ts:212` branches on
it (`err?.kind !== "already_absorbed"`) and `SceneReview.test.tsx` asserts it,
so a 202 that swallowed it into an async run payload would break a tested
behavior silently — the failure mode this whole document exists to avoid. But
the general rule matters as much: a caller must never receive an accepted run
for work already known to be invalid.

Only failures that happen *after* the run is accepted become run state. Those
must round-trip enough to rebuild the same client-side error: `error` carries
`{status, detail, kind}` so `GET .../runs/{run_id}` can be turned back into the
`ApiError(status, detail, kind)` the client already knows how to branch on.
`_llm_http_error` is the shape to preserve — a fatal extraction failure over a
poll boundary must be indistinguishable, to `useSceneReview`, from today's
synchronous one.

### Edges the implementer will otherwise have to guess

- **`?from=N` beyond the buffer** — return no frames and tail; do not error. A
  client that raced ahead is asking a legitimate question.
- **`?from=N` against a terminal run** — replay the remainder and close. A run
  that finished while the client was away is the common case, not an error.
- **Any request naming a reaped or unknown run id** — 404 with
  `kind: "run_gone"`, which the client turns into "refetch the scene", the
  correct recovery in every case: the reply is in the transcript, or the
  pending review is on disk, or neither ever existed.
- **Overlapping subscribers** — allowed and benign. A reconnect routinely
  overlaps the socket it replaces, and a laptop may have two tabs. Subscribers
  are strictly read-only against an append-only list, so N of them see the same
  frames; nothing coordinates them and nothing needs to.

### Reaping

A terminal run is kept for **ten minutes** so a late re-attach still catches
its tail, then dropped by a sweep running on the lifespan task group beside
`_backup_ticker`. After that the transcript is the record for a streaming run,
and `store/pending_reviews.py` is the record for an absorb.

This is unlike `_turn_tokens`, which is documented as never reaped because its
entries are one small integer per scene. Run frames are not that, and a
never-reaped registry in a long-lived desktop process is a leak.

All of these must be registered in an order `test_route_order.py` accepts —
`runs/{run_id}` sits under a `{sid}` path that already captures freely, so the
literal-segment rules there apply.

**A terminal pending-review write stamps campaign activity explicitly.** The
`_CampaignActivityStamp` middleware (`main.py:186`, installed at `:292`) moves
a campaign up the recents rail per *request*, and skips routes marked
`grimoire_computes_only` (`:244`). Detaching breaks that: the absorb's 202 is
`@computes_only` and returns long before the review exists, so the write that
does mutate the campaign happens outside any request the middleware can see. A
completed background review would silently fail to move its campaign. The
terminal persist therefore stamps activity itself, once, after the write —
`@computes_only` stays correct for the 202, which really does compute and
return nothing.

**Cancel does not get `@computes_only`,** and the temptation to add it is a
trap. Cancelling a streaming run drives `on_abort`, whose entire job is to
persist the partial and possibly `restore_removed()` — that is a write, and
`computes_only`'s docstring is explicit that the marker means a route persists
nothing. Tagging it would make the recents rail lie in precisely the case where
the campaign *was* just written to.

**A run listing across campaigns is deliberately not specified.** The obvious
convenience endpoint is the exact shape `test_lock_order_guard.py` fails — one
handler holding more than one campaign lock. Each run touches exactly one
campaign and takes exactly one lock; nothing in this design needs `hold_all`.

### Answering "did my post land?" — the #95 obligation

`client.ts` tags a pre-response failure `beforeResponse` for one reason: the
line between "the server never got this" and "the server got it and then
something went wrong" is the response, because `post_chat` appends the player's
post *before* returning the stream. A chat that cannot tell them apart has to
guess whether the prompt still exists, and guessing either way loses or
duplicates it.

Detaching widens that window rather than narrowing it, so this needs saying
plainly: **if the response is lost after the post was appended, and the run
then finishes before the client comes back, a live-runs-only lookup 404s and
the client is in exactly the ambiguous state #95 closed.** The scene would
contain the post and a reply, and the client would believe neither had
happened.

That is why `GET .../run` answers for terminal runs inside the reap window, not
only live ones. But *"a run exists"* is *not* sufficient proof on its own, and
saying otherwise would replace one ambiguity with a worse one: a run A that
finished eight minutes ago is still inside the window, so a later POST for B
that never reached the server at all would find A and be reported as accepted.
That does not merely fail to fix #95 — it converts an "I don't know" into a
confident wrong answer, and loses or duplicates B's prompt depending on which
way the client then guesses.

**So the POST carries a client-generated attempt id, and the run records it.**
Recovery asks about *that id*, not about recency:

- a run exists carrying my attempt id → my post landed; attach, or read its
  terminal state;
- no such run → my post never landed, whatever else the scene has been doing;
  the send can be retried as a new attempt.

The attempt id is what makes the question answerable, and it is also what makes
the reap window a comfort rather than a correctness knob: a client whose id has
aged out falls back to the move it already makes today — refetch the scene and
compare — which is safe because it is the honest "I don't know" rather than a
guess dressed as proof.

The stream currently lives in the scene view's component state, so navigating
away unmounts it and aborts. That has to move up.

- A **run-registry provider above the router**, holding in-flight runs by id
  with a `(cid, sid)` index. Leaving a scene no longer tears its run down;
  returning re-attaches by id.
- On scene-view mount and on `visibilitychange` → `GET .../run`; attach if
  present.
- **The client resumes from its own last-consumed frame index, not from the
  server's `next_index`.** This is a correctness rule, not an optimization.
  `next_index` is the buffer's length *at the moment of the lookup*, so a
  client that disconnected at frame 40 and looks up at frame 900 would resume
  at 900 and silently lose 860 frames — a reply rendered with a hole in the
  middle, in exactly the locked-phone flow this document exists to fix. The
  registry provider persists each run's consumed index as it reads, and
  `?from=` carries that.
- **The server stamps an absolute frame index on every frame, and the client
  resumes from the last one it saw.** Counting `ChatEvent`s client-side does
  not work and the difference is silent: heartbeats are SSE comments with no
  `data:` line, and `parseSSEChunk` drops them without invoking the callback.
  A cursor advanced per event therefore lags the server's raw frame position
  by however many heartbeats went by — and a generation that waited on the
  model emits a great many. Reconnecting from that short index replays frames
  already rendered and **duplicates text in the middle of the reply**, the
  mirror of the bug above and just as invisible. An index carried on the frame
  itself removes the need for the two sides to agree on what counts.
- `next_index` is only for a client that means to attach at the live tail and
  does not want the backlog.
- The **composer is disabled** whenever the scene has a live run, extending the
  existing `sceneLocked` signal (already used by rename, End scene and the roll
  controls) rather than inventing a second one.
- `SceneReview` changes from await-the-response to start → poll → render, and
  learns to pick up a pending review on open.
- `run_gone` from any run endpoint means refetch the scene, never surface an
  error: by then the reply is in the transcript or the review is on disk.

Per `CLAUDE.md`, shared scaffolding for this goes in `frontend/src/testkit/`
(`campaignMocks.tsx` / `campaignHarness.tsx`), which the coverage config
excludes — not in `src/routes/`.

### Android

**Foreground service.** `ServerService` promotes when the registry gains its
first live run and demotes when it has none. Manifest additions:
`FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_DATA_SYNC`, `POST_NOTIFICATIONS`
(runtime-requested on Android 13+), and `android:foregroundServiceType="dataSync"`
on the service. `dataSync` is the honest type; `shortService` caps at three
minutes, which an absorb exceeds routinely.

**Notification channels are registered before the first promotion or
notification.** The app has none today, and on Android 8.0+ — which is every
supported device, given `minSdk 26` — posting to an unregistered channel
suppresses a completion notification outright and makes a foreground-service
notification invalid. That last part is not cosmetic: an invalid foreground
notification undermines the process-lifetime guarantee this whole feature rests
on. Two channels with explicit ids, one for the ongoing service notification
(low importance, no sound) and one for completions, created at service start
before either builder runs.

**Notification.** Python fires a terminal-state hook; Kotlin receives it over a
Chaquopy callback — the same shape as `ServerRuntime`'s existing port callback —
and posts a `NotificationCompat` on the completions channel. No JS↔Kotlin
bridge is required, because the trigger is server-side.

Text, per the player's request:

- turn landed → `New Post in <Campaign>: <Scene>`
- turn failed → `Error on <Campaign>: <Scene>`
- review ready → `Scene review ready — <Campaign>: <Scene>`, with the same
  error form. The `audit` and `dossiers` retries land under this wording too;
  they are the same review arriving, and a player who asked for one is not
  helped by a third phrasing.

Display names, not slugs — and **captured when the run starts, not when it
ends.** The spec allows a scene to be deleted under a live run and marks that
run `failed`; resolving the title from the scene record at terminal time would
then find nothing, and the error notification — the one case where the player
most needs telling — is exactly the one that cannot be built. Campaign name
from `store.campaigns` and scene title from the scene record, both read at run
start and carried on the run.

**The terminal notification hook is isolated and its failures are non-fatal.**
It runs after the run's own bookkeeping, and anything it raises is swallowed and
logged. A notification is the least important thing a terminal run does;
letting it interrupt the persist, the state transition or the foreground-service
demotion would trade the whole feature for a toast.

The notification id derives from the run id, so two runs completing cannot
collapse into one notification. The tap intent carries `cid` and `sid`;
`MainActivity` turns them into an SPA route.

**No notification while the app is in the foreground.** A player watching the
tokens land does not need to be told they landed, and a notification for
something already on screen is the fastest way to make the feature feel broken.
Kotlin suppresses the completion notification while the activity is resumed;
the foreground-service notification is separate and unaffected.

**If `POST_NOTIFICATIONS` is denied**, foreground promotion still works — the
service runs, its notification is simply not shown, and runs survive
backgrounding exactly as designed. Only the completion notification is lost.
That is a degraded mode, not a broken one, and the app must not treat a denied
permission as a reason to skip promotion.

**On desktop there is no notification and none is planned.** The window is
already there; the run lands in the open tab. Stated so its absence reads as a
decision rather than an oversight.

## Constraints this must satisfy

The tree enforces most of its architecture with AST guards. A change this
broad touches nearly all of them, so they are requirements, not afterthoughts:

| Guard | Obligation |
|---|---|
| `test_atomic_guard.py` | every `pending_reviews` write goes through `store.atomic` |
| `test_lock_domain_guard.py` | `store/pending_reviews.py` classified in `DOMAIN_MODULES`; its `cid`-taking mutators take `campaign_lock(cid)` |
| `store/cascade.py` | `pending_reviews` named there and wired into the `scene_refs.repoint` fan-out — it persists a scene id, and that module's rule is that an unnamed such store is one nobody has decided about |
| `test_lock_order_guard.py` | one campaign lock per run, ever; no `ExitStack`, no lock carried around a loop |
| `test_import_guard.py` | module-scope imports, acyclic: `routes/runs.py` must not import `streaming`/`scenes` |
| `test_pydantic_guard.py` | plain `BaseModel` fields; dump via `routes.common._dump` |
| `test_paths_guard.py` | filesystem through the resolvers |
| `test_usage_guard.py` | **the guard must be taught to follow the runner, or the widened scope blinds it** — see below |
| `test_route_order.py` | new routes registered where they are reachable |
| `check-pydantic1` | the whole suite passes under the Android dependency set |
| ratchet baselines | `make baseline` and commit the smaller files with the change |

### The usage guard is the sharp edge of this refactor

`test_usage_guard.py` exists because the ledger is only worth reading if it is
complete, and completeness decays silently. It is honest about how it works:
it scans `routes/` and recognizes an LLM call **by the receiver name `client`**,
counting a call metered when it is passed `<something>.usage`. That is the
codebase's own convention, not a proof.

Moving all 35 call sites behind a uniform runner is exactly the change that
convention does not survive by accident. If the call migrates into a runner
factory — or out of `routes/` entirely — the guard stops seeing it, reports
nothing, and goes green over a ledger that has quietly stopped recording.
A guard that fails open is worse than no guard, because the number it produces
still looks like an answer.

So this is a requirement of the work, not a consequence to notice afterwards:
either the meter stays at each call site with the receiver still named
`client`, or the guard is rewritten to follow runs and its docstring updated to
say what it now recognizes. Whichever is chosen, the guard must be shown
failing on a deliberately unmetered run before the work is called done — the
same standard the module already holds itself to.

## Testing

Backend, with `backend/tests/llm_fakes.py` injected at
`app.dependency_overrides[routes.get_llm]` — never a new inline fake:

- run lifecycle per kind; terminal states.
- second start on a busy scene → 409 with `kind: "run_in_flight"`.
- **a dropped subscriber does not cancel the run** — the inverse of today's
  behavior, and the single most important test here.
- reconnect replays from `N` and produces byte-identical output to an
  uninterrupted stream.
- explicit cancel drives `on_abort`; the existing partial-persist and
  restore/undo assertions still hold.
- two runs in different scenes of one campaign, and in two campaigns,
  interleave without cross-contamination.
- a roll-proposal continuation (`_continuation_stream`) detaches and re-attaches
  like a chat turn, and a supersede landing mid-run still makes
  `commit_narration` return False.
- absorb's **pre-flight** errors stay synchronous with their `kind` intact —
  `already_absorbed` above all — while a fatal extraction failure surfaces
  through the run and rebuilds the same `ApiError`.
- an absorb result survives its requester leaving; a pending review whose scene
  moved on is refused.
- `GET .../run?attempt=` answers for a run that finished while nobody was
  attached — the #95 case, and the reason terminal runs stay in the registry.
- **a send that never reached the server reports as not-landed even though an
  unrelated run finished on that scene moments earlier** — the case that makes
  recency alone an unsafe proxy for "my post landed".
- `rolling_summary` and `scene_break` fire after a turn **without** taking the
  scene's run slot, so the composer re-enables.
- shutdown cancels live runs and their partials are persisted through
  `_flush_on_abort`.
- a reaped run id returns 404 `kind: "run_gone"`; `?from=` past the buffer tails
  rather than erroring.
- a send rejected by exclusivity leaves the transcript byte-identical — no
  orphaned player post.
- a pending review is still retrievable after its run has been reaped, which is
  the case the persistence exists for.
- a reconnect resuming from the client's consumed index reproduces the whole
  reply; resuming from a stale `next_index` is what the test must *fail* on, so
  it asserts frame contiguity rather than merely "some frames arrived".
- an `audit` / `dossiers` retry landing in the background merges into the stored
  review and leaves the absorb prose, staged edits and `commit_token` intact.
- Cancel racing a terminal persist leaves the review deleted, never recreated,
  in both interleavings.
- renaming a scene carries its pending review to the new id.
- a run id from another scene, sent through this scene's URL, returns
  `run_gone` and neither streams nor cancels.
- a run-producing route rejected by exclusivity mutates nothing — proposals not
  superseded, no reply archived, no check resolved.
- a terminal pending-review write moves its campaign up the recents rail.
- a run whose scene was deleted still produces its error notification, from
  labels captured at run start.

Widened scope:

- every one of the 25 LLM routes starts a run and is re-attachable; a table
  test over the route inventory, so a route added later without a class is a
  failure rather than an omission.
- a `background` run (rolling summary, scene break) does **not** take the
  scene's exclusion key, and the composer stays enabled while one is live —
  the regression that would make the app lock itself after every turn.
- a `draft` run for a world subject and a `turn` for a scene run concurrently
  without either blocking the other.
- the global models-refresh run detaches with no campaign lock taken at all.
- a run id from one subject, sent through another subject's route, returns
  `run_gone`.

Frontend (vitest, from `frontend/`):

- composer disabled while a run is live, and re-enabled once it lands —
  including after a `rolling_summary` / `scene_break` fires, which must not
  hold it down.
- navigate away and back re-attaches to the right run.
- two scenes with live runs do not cross-render.
- `run_gone` refetches the scene instead of surfacing an error.
- End Scene: start → poll → render, and pick-up of a pending review on open.

The `await`-means-settled rule in `CLAUDE.md` applies throughout; these tests
are exactly the shape (a control that is briefly `disabled`) that goes green on
an idle machine and red on a shared runner.

Android has no automated coverage beyond `make check-apk`; foreground promotion
and notification delivery are verified by hand on device, and the spec says so
rather than implying otherwise.

## Documentation this change invalidates

`docs/android-architecture.md` states that foreground promotion during
generation is unimplemented Phase 3, and lists risk 6 (process killed
mid-stream → reply lost) as unmitigated; `ServerService.kt`'s docstring says
the same. Both become false and are updated with the implementation. Neither
file is in `test_docs_guard.py`'s `DOCS` tuple, so nothing fails if they are
forgotten — which is exactly why they are named here.

No template changes, so `evals/run.py` and the frozen campaign's
`snapshot.json` should not move. If the snapshot does move, something rendered
differently and that is a finding, not a regeneration.

## Risks and accepted limitations

- **Process death still loses in-flight work, and the persistence here does
  not change that.** For a streaming run the reply itself survives —
  `_persist_reply` wrote it — and only the live tail is lost. For an absorb the
  loss is total: the review is written when the run *completes*, so a process
  killed at minute eight of a ten-minute fan-out has nothing to show for it.
  What `store/pending_reviews.py` buys is durability for a **finished** review
  whose client went away, which is the common case; surviving a death
  mid-absorb would need phase-level checkpointing and is not in this design.
  The foreground service is what makes the death rare, and it is the only
  defense this spec offers against it.
- **`dataSync` foreground services are capped at roughly six hours per day on
  Android 15+.** Runs are minutes; a player would have to be generating
  continuously for hours to reach it. Noted so it is not discovered.
- **Memory.** Bounded by one exclusive run per scene plus whatever
  non-exclusive `background`/`draft` runs a user has triggered, with frames
  measured in kilobytes. Widening scope widens this: `draft` runs are the ones
  with no natural cap, so they are reaped on the same TTL and hold results
  rather than frame buffers wherever they do not stream. Terminal runs
  are reaped on a TTL so a late re-attach can still catch the tail, after which
  the transcript is the record.
- **Disconnect no longer cancels.** A deliberate behavior change on every
  platform, called out here because it changes a habit that works today.
- **Nothing crosses devices.** Unchanged, and unchanged deliberately.
