# Read-path performance: caching what navigation re-reads

**Date:** 2026-08-28
**Status:** Draft for review

## Problem

Opening the campaigns or worlds list, or opening one campaign or world, is
slow enough to feel broken on a large library — and the cost is paid again on
every navigation, because nothing on either side of the API remembers
anything between requests.

Where the time goes today, traced through the code:

- **Every navigation refetches `GET /api/shell`** (the rail's badges), and
  that endpoint parses every `campaign.md` (`list_campaigns`), opens every
  scene file of the open campaign for its frontmatter head (`list_scenes`),
  parses the **full transcript of every unabsorbed scene** to count model
  turns (`routes/shell.py:_scene_turns`), reads one sheet per cast member
  (`sheets.coverage`) and probes every todo chore. Playing ahead of the
  absorb is the normal state of a campaign, so the transcript parses are the
  app's largest files, on its most frequent request.
- **`GET /campaigns` is O(every scene in the library):** one file open and
  frontmatter-head parse per scene, per campaign, plus a cover stat and an
  activity read each — and the shell call beside it does `list_campaigns`
  again.
- **`GET /worlds`** parses each `world.md` and sweeps several directories per
  world for its counts.
- **Opening a world** fires one count request per index row, each of which
  fetches the *full list* of that record kind just to take its length
  (`WorldView.countOf`) — including `list_characters`, which per character
  parses `character.md`, scans the versions and images directories, and
  reads the tagline, voice-anchor and focus sidecars. Every section switch
  fires all of the counts again.
- **Nothing is cached.** The backend re-derives everything from disk per
  request (`statcache` memoizes record-level derivations — card, PC and
  entity summaries, token counts, content hashes — but no *listing* path
  goes through it); there are no conditional requests, and the client only
  dedupes GETs that are literally in flight at the same moment.

The store's design — plain files, re-read on demand, no indexes — is a
feature and is not in question here. What this spec adds is memory *around*
that design: reuse a derivation while its input files' stat signatures are
unchanged, tell the client "nothing changed" cheaply, and let the client
render what it already had while it re-asks.

## Non-goals

- **No stored aggregates or index files.** A per-campaign scene index or
  per-world roster manifest (on the `usage_rollup` precedent) is the next
  step if this one proves insufficient; it adds a second store to keep
  consistent and is deliberately out of scope.
- **No store format changes, no pagination changes, no payload reshaping.**
  Every endpoint answers byte-identically to today (modulo new headers).
- **No per-campaign ETag granularity.** One library-wide epoch is enough to
  start (see "Conditional reads"); refining the campaign-scoped routes onto
  `store/revision.py` tokens is future work the design leaves room for.
- **Synced-store *hydration* cost is out of scope** — the reference setup
  syncs with syncthing (real local files), so there are no cloud
  placeholders to design around. Synced-store *correctness* is NOT out of
  scope: a sync client writing the store from another device is an ordinary
  event (`statcache`'s and `revision.py`'s docstrings both plan for it), and
  the conditional-read layer below is designed so such a write can never be
  hidden for more than one time bucket.

## Design

Four layers, independent and individually shippable, ordered by leverage.
Each is worth having without the others; together, an unchanged page costs
one epoch check on the server and zero perceived latency on the client.

### 1. Extend `statcache` to the hot per-file derivations

`store/statcache.py` already gives us process-wide memoization keyed by
`(path, mtime_ns, size)` with a racy-write guard and FIFO budgets. Apply it
to the derivations navigation actually repeats:

- **Scene frontmatter heads** (`scenes/read.list_scenes`): memoize the
  parsed head per scene file. This turns the per-navigation scene sweeps —
  three of them on a campaign-hub load (shell, scenes list, chronicle-side
  reads) — into stat calls.
- **Scene turn counts** (`routes/shell._scene_turns`): memoize the final
  integer. The count depends on the transcript file, the appearances record,
  and the players' display names (role derivation compares speaker labels
  against them), so the memo key is the stat signatures of the scene file
  and the campaign's appearances record **plus the player-name tuple
  itself**. The names are recomputed each time — a handful of small reads
  bounded by cast size — and go into the key rather than being trusted to a
  signature, so an actor rename invalidates naturally. One wrinkle the key
  must carry: an **absent** appearances record is a valid state, not a
  missing file (`appearances.record` reads it as the empty record), while
  `statcache.signature` answers `None` for any missing path and `memo`
  refuses to cache that — which would leave every scene of a campaign with
  no cast record parsing its full transcript on each recompute, the common
  state of a fresh or PC-less campaign. The key therefore uses an explicit
  "absent" sentinel for that file instead of an uncacheable `None`; the
  record's later creation replaces the sentinel with a real signature and
  invalidates. This removes the full-transcript parse from the navigation
  path.
- **Campaign listing rows** (`campaigns/read.list_campaigns`): memoize the
  parsed frontmatter + blurb per `campaign.md`.
- **World listing rows** (`worlds/read.list_worlds`): memoize the parsed
  frontmatter per `world.md`. The per-world count sweeps stay live reads —
  they are single `iterdir`s and correctness-critical for the counts.
- **Character listing sub-reads** (`store/characters.list_characters`): the
  tagline, voice-anchor and focus sidecar reads become stat-keyed memos,
  alongside the card summaries that already are.

Rules carried from the existing cache, made explicit:

- **Memoized values are frozen.** Callers already treat the parsed dicts as
  read-only (`meta.get(...)` everywhere); the plan must verify no caller
  mutates a returned dict, and any that does copies at the boundary.
- **Sweep-sized memo families get their own pools, and the pools get their
  own budgets.** The shared FIFO is 4096 entries; scene heads and turn
  counts across a large library could evict every card summary and sync
  hash on one listing, so those memos use the `pool=` parameter as
  `store/search.py` does. A separate pool is not enough on its own,
  though: `memo` applies the one global `MAX_ENTRIES` to caller pools too,
  and FIFO eviction under a repeated *sequential* sweep is the worst case
  — a library whose sweep exceeds the budget evicts the entries it will
  need next before reaching them, and a "cached" listing gets zero hits.
  So `memo` grows a per-pool budget, and the sweep pools are sized so a
  whole library's working set fits with headroom: a memoized head is a
  small dict, so tens of thousands of entries is a few megabytes, which is
  the right trade for a cache whose entire purpose is the large-library
  case. The budget is a constant justified structurally, tuned later if
  ever.
- **The racy window stands:** a file written within the last second is
  computed and not cached, same as today.

### 2. Intra-request duplicate reads

Independent of caching, several handlers read the same file twice in one
request. Fix the ones the trace found; the plan should sweep for others of
the same shape:

- `GET /api/shell`: `_campaign_block` re-parses `campaign.md` via
  `read_campaign` when `list_campaigns` (same request, same handler) already
  parsed every one. Hand the block the listing row it needs instead.
- `GET /campaigns/{cid}`: `ensure_campaign_slim` parses `campaign.md` to
  decide it has nothing to do, then `read_campaign` parses it again. The
  common (already-migrated) path should parse once — e.g. `ensure` returns
  the parsed pair for reuse, or the route consults a memoized
  `slim_pending`.
- `_scene_turns` calls `player_names` per open scene, and `player_names`
  re-reads the appearances record each call. Read the record once per
  request and pass the derived names down (this also produces the memo key
  for layer 1).

Kept deliberately, with the reason recorded so it isn't "fixed" later:
`WorldView.countOf` fetching the full list of a kind rather than a count
endpoint stays — the count and the section's own editor read must come from
the same source or they drift apart, and layers 3–4 make the shared read
cheap instead of making it two sources.

### 3. Conditional reads: a library epoch and `ETag`/`304`

**`store/epoch.py`** — a process-local, in-memory read epoch. The token a
client sees is a pair rendered into one opaque string: a fresh `uuid4`
replaced by `bump()`, **plus the current time bucket** (`now // EPOCH_TTL`).
The bucket is what bounds belief: this process cannot see a write it did not
make — a sync client landing a file from another device, a hand edit, a
second grimoire process — and a pure in-memory token would answer `304`
for such a change *forever*, until an unrelated local write happened to
land. Folding the bucket in caps that: when the bucket rolls over, every
held token stops matching and the next revalidation is a full recompute —
which layer 1 has made a stat sweep rather than a parse sweep, because
`statcache` signatures (unlike the epoch) *do* see external writes. So an
external change is visible within one navigation or one `EPOCH_TTL`,
whichever is later. `EPOCH_TTL` starts at 30 seconds — long enough that
idle navigation is almost always `304`s, short enough that another device's
edits don't outlive a coffee sip; it is a structural guess and should be
tuned against real use later, per this repo's rule about constants.

Not persisted, on purpose: a restart mints a new uuid, so no client can
get a false `304` from a value that survived a restart. Assignment is
atomic under the GIL; there is nothing to lock. Module scope rather than
`app.state`, despite the run registry's precedent: a value leaked across
`TestClient` app instances can only *fail to match* a token no test client
is holding — over-invalidation, the direction this value is allowed to be
wrong in — where a leaked run is live state answering for the wrong app.
The tests that assert a `304` mint their token and replay it against the
same app, so the leak is unobservable.

**What bumps it** — the same "one funnel, plus the writers that outlive
their response" argument `store/revision.py` is built on:

- `revision.bump(cid)` bumps the epoch as a side effect. That covers every
  campaign write: the activity middleware's stamp, every detached-run
  self-stamp (turn terminals, review saves, follow-up commits, prompt
  captures, steering appends), and the lazy migrations that funnel through
  it — those already converge on `bump`.
- **The usage-ledger append bumps.** A detached draft (`@computes_only`,
  202) persists nothing — except its metered ledger row, which lands
  minutes after the response and moves `/api/shell`'s money for the
  campaign it was metered against. Nothing else sees that write: not the
  middleware (the response is long gone), not `revision.bump` (drafts
  deliberately never call it). The bump goes where the row lands — the
  same "the meter is the one place that sees all of them" argument
  CLAUDE.md already makes for error instrumentation.
- A new, deliberately coarse trigger for everything the funnel does not
  see: any **mutating method** under `/api/` that answers 2xx and is not
  `@computes_only` bumps the epoch — worlds, config, modules, connections
  included. This is a **new, separate ASGI wrapper**, not a widened
  condition on `_CampaignActivityStamp`: the existing middleware
  early-returns for everything outside `/api/campaigns/`, exception path
  included, so its shape cannot carry this. The campaign-scoped subset
  double-bumps (once here, once via `revision.bump`); a second bump is
  free, and the redundancy is what makes "a route added tomorrow is
  covered" true in both directions. `@computes_only` today exists only on
  campaign-scoped routes; world- and global-scoped draft POSTs carry no
  marker, so each 202 preview over-bumps. Accepted knowingly — previews
  are rare beside navigations, and the marker can be extended later
  (remembering the greeting-opener lesson: a `computes_only` route that
  *does* write still bumps via the funnel).
- **The bump lands before the response line is forwarded**, for exactly
  the reason the activity stamp does (`main.py`): stamped after send, the
  client can navigate on the response and have its refetch revalidate
  against the pre-write token — a `304` on a stale body, which is the one
  failure this layer may never produce from its own writes. The wrapper's
  exception path (a write that raised mid-way) bumps too, as the revision
  stamp does today. For a **streaming mutator** the response line is *too
  early* to be the only bump: a non-detached SSE route can persist while
  its generator runs — the localize stream saves the rewritten card, the
  tagline batch writes as it yields, a gallery import downloads mid-stream
  — all after the 200 was sent, under an epoch already minted. The wrapper
  therefore bumps a mutating stream **twice**: at the response line (the
  navigation-ordering bound) and again when the application call
  **returns or is cancelled** — a `finally`, not an observation of the
  final body frame. The distinction is exactly the disconnect case: a
  client that drops mid-stream never receives a final frame, and the
  disconnect is precisely when these generators' cleanup persists what
  landed (the localize save runs in its `finally`; a cancelled tagline
  batch keeps its completed writes). Campaign-scoped streams already
  self-bump at their terminals via the funnel; the terminal bump is what
  covers the world-scoped ones, which have no revision to call.
- `PUT /config/data-dir` is a mutating 2xx, so repointing the store
  invalidates everything — no special case needed, but the test suite
  proves it.

**How routes spend it** — an explicit, per-route opt-in, not blanket
middleware, because not every GET reads the store: run polling reads
in-memory run state that changes with **no** write, and a blanket `304`
there would freeze a poll loop. A small helper in `routes/common.py`:

```python
def read_cache(request: Request, response: Response) -> Response | None:
    """304 if the client's If-None-Match token is the current epoch;
    otherwise stamp ETag + Cache-Control: no-cache and return None."""
```

Each cacheable route takes `request`/`response` params, calls the helper
first, and returns the `304` before doing any work — **with one stated
exception**: `GET /campaigns/{cid}` runs `ensure_campaign_slim` before its
read, a durable lazy migration reached from a GET, and a helper-first
`304` would skip it for as long as the epoch stands. On that route the
check sits *after* the migration call; since the migration funnels through
`revision.bump`, a run that migrated mints a new epoch and the same
request answers `200` with fresh state. (`GET /api/shell` also writes — the
usage rollup's byte bookmark — but a `304` merely defers bookkeeping the
next `200` performs identically, so it stays helper-first.) The opt-in set
is the
hot read surface: `GET /campaigns`, `/worlds`, `/shell`, `/campaigns/{cid}`,
`/worlds/{wid}`, the scenes list, the chronicle, the campaign changes sweep,
and the world- and campaign-scoped record lists (characters, PCs, entities,
greetings, tags). ETags are weak (`W/"…"`), since GZip re-encoding makes
byte-identity claims false. `Cache-Control: no-cache` means browsers always
revalidate but serve the cached body themselves on `304` — the frontend's
`fetch` calls get the conditional-request machinery for free, with no client
code.

Granularity is a single library-wide token, so **any** write invalidates
**every** cached read. That is the accepted cost of one mechanism: writes
are orders of magnitude rarer than navigations, and mid-play navigation
still revalidates in one round trip against a recompute the layer-1 memos
have already made cheap. The helper's shape leaves room to hand a
campaign-scoped route `revision.current(cid)` as its token later — but note
before reaching for it that a campaign's reads *overlay the world*, so the
campaign token alone under-invalidates; that refinement needs
`(epoch-of-world, revision)` or similar, which is exactly why it is future
work.

### 4. Frontend: render what we had, then revalidate

**A read-through payload cache in the API layer** (`api/readCache.ts`):
successful GET payloads for opted-in paths are kept in a module-level map
keyed by request path, **bounded by a small LRU budget** — the values
include whole record lists fetched for counts, and a session that visits
many scopes must not retain every one of them for the life of the tab
(the browser's HTTP cache already holds the bodies; this map only needs
the recently-rendered ones). A small hook (`useCachedGet` beside the
existing patterns) hands a view the cached payload synchronously — marked
stale — and swaps in the fresh answer when the refetch lands.

**Invalidation must retire what is in flight, not just what has landed.**
A GET that began before a mutation and settles after it would otherwise
repopulate the map with the pre-mutation answer — a stale page with no
revalidation scheduled behind it. `api/client.ts` already solves exactly
this ordering for its in-flight dedupe (`retireInflight` / the `fresh`
flag); the cache carries the same idea as a per-path generation: an
invalidation bumps the generation, and a settling response stamped with an
older generation is discarded instead of stored. The generation protects
the *cache*; it does not protect a **direct post-mutation refetch** from
joining a pre-mutation promise still sitting in the client's in-flight
map — and SWR widens that window, since every render now starts a
background revalidation for such a promise to be. So the rule the fork and
import paths already follow becomes general: a refetch issued *because a
mutation just succeeded* passes `fresh` (the world views' rename and
delete refreshes are the two that don't today), and the fresh answer
replaces the cache entry as any settling fresh response does. `useShellPayload`
already implements exactly this posture ("the rail's first job is
navigation, and navigation must survive a server that stopped answering");
this generalizes it to the pages.

Consumers: `CampaignsView` (campaign + world lists), `WorldsView`,
`WorldView` (the index counts), `CampaignHub` (its parallel block of
reads, **minus the chronicle**: the hub renders the newest chronicle entry
as the campaign's current recap, and a pre-absorb recap painted as current
is a stale record body in exactly the sense the next sentence excludes —
the chronicle read still benefits from `304`s, it just never renders from
the stale cache). Play-path and editor reads do **not** opt in — a
transcript or a record body rendered stale is wrong in a way a count or a
card shelf is not.

**Invalidation reuses what exists — and says plainly where nothing exists:**

- The `appEvents` bus broadcasts `campaignsChanged` / `configChanged` /
  `shellChanged` / `noticesChanged` after the mutations that fire them; the
  cache subscribes and drops the affected entries.
- **There is no worlds channel, and most world-scoped mutations emit
  nothing** (`deleteWorld` fires no event; record CRUD under a world —
  characters, entities, greetings, tags — fires none). The world surfaces
  therefore rely on the other two mechanisms alone: every render
  revalidates (stale is only ever the first frame), and the views already
  re-fetch explicitly after their own mutations, which replaces the cache
  entry. Adding a worlds channel is not part of this design; if a stale
  first frame after a world mutation proves confusing in practice, that is
  the follow-up, done as an emission audit rather than piecemeal.
- A store-root change (`configChanged` with a new `data_dir`) clears the
  whole cache — the same identity rule `useShellPayload.keyOf` enforces:
  never render the previous library's numbers, not even as a stale frame.
- Entries are also replaced whenever a fresh answer lands, so the cache can
  never serve older data than the last thing the user saw.

**WorldView stops re-asking everything.** On a section switch, only the
count of the section being *left* is refreshed (that is the one the user
could have changed), keeping the full re-ask for `populated` (a scenario
import creates records in half a dozen sections at once). One switch is
*not* an ordinary leave: a reclassify moves a record between entity kinds
and then navigates to the destination (`onReclassified={openEntity}`), so
refreshing only the source would undercount the very section the user
just landed in — a cross-kind move refreshes **both** affected counts.
Combined with the cache, switching sections renders the previous counts
instantly and corrects at most the ones that could have moved.

## What correctness rests on

- **A stat signature is a real boundary — widened by one field to keep it
  one.** Every store write goes through `store.atomic` (guard-enforced),
  which replaces via rename — mtime and dir entries move on every write,
  and the racy window covers coarse filesystem clocks. The remaining hole
  is a *metadata-preserving replacement*: a sync client or a restore that
  lands a same-size file while carrying the origin's old mtime produces an
  identical `(path, mtime_ns, size)` tuple, and — unlike the epoch, whose
  TTL forces a recompute that would only re-fetch the same stale memo —
  nothing ever invalidates. Replacement via rename cannot preserve the
  *inode*, so `statcache.signature` gains `st_ino`: the shared primitive
  is widened (every existing memo family inherits the fix, not just the
  new ones), the cost is zero (the field is already in the `stat` result),
  and the residual shrinks to an in-place same-size rewrite with a
  restored mtime — the racy-clean residual git also accepts.
- **The epoch is wrong in the safe direction for this process's writes,
  and boundedly wrong for everyone else's.** Missed-bump bugs are the
  dangerous class; for writes this process makes, the design makes the
  bump redundant (funnel + middleware + ledger append) and accepts
  over-bumping everywhere else. For writes it *cannot* see — a sync
  client, a hand edit, a second process — no bump exists to miss, and the
  time bucket is the guarantee instead: a `304` is never believed past
  `EPOCH_TTL`, and the recompute it forces reads through `statcache`
  signatures, which external writes do move.
- **Stale frames on the client are always *the user's own last view*,**
  never another library's, never rendered without a revalidation in
  flight.
- **A price nobody reported is never rendered as zero** — unchanged: the
  cache stores whole payloads, so the cost surfaces' absent-vs-zero
  distinction rides along untouched.

## Testing

- **Memo behaviour** (pytest): a counting fake around the parser proves a
  second `list_scenes`/`list_campaigns`/`_scene_turns` call re-reads
  nothing; a write through `store.atomic` invalidates; the turns memo
  invalidates on an actor rename (name-in-key); a scene in a campaign with
  **no appearances record** caches (the absent-sentinel), and the record's
  creation invalidates; a rename-replace that preserves mtime and size
  still invalidates (the `st_ino` field); pool isolation proves a
  library-wide sweep leaves the shared cache's entries alone, and a
  repeated sweep *larger than the shared budget* still hits in its own
  pool (the per-pool budget). **Fixture
  mtimes must be backdated with `os.utime`** past the racy window, or the
  cache refuses to engage — the suite's established pattern
  (`test_statcache.py`, `test_characters_store.py`, `test_search_store.py`);
  the Testing plan inherits it rather than rediscovering it red.
- **Epoch + ETag** (TestClient): a cacheable GET carries `ETag` and
  `Cache-Control: no-cache`; a repeat with `If-None-Match` answers `304`
  with no store reads (counting fake again); any write — campaign-scoped,
  world-scoped, config — flips the answer back to `200` with a new tag; a
  usage-ledger append (a detached draft's metered row) flips it too; a
  clock stepped past `EPOCH_TTL` flips it with **no** write, which is the
  external-writer guarantee stated as a test; a mutating SSE route whose
  generator writes after the response line answers `200` with a new tag to
  the next conditional read — including when the client disconnected
  mid-stream and the write landed in cleanup (the terminal bump is a
  `finally`); `GET /campaigns/{cid}` still
  performs its slim migration when answering `304`-eligible requests (the
  ordering exception); a run-poll GET never carries an `ETag`;
  campaign-scoped `@computes_only` routes don't bump.
- **Intra-request dedup**: read-counting tests per fixed handler (shell
  parses each `campaign.md` at most once per request, etc.).
- **Frontend** (vitest): the hook renders a cached payload synchronously
  and swaps on settle; `campaignsChanged` drops the entry; a response that
  settles after its path was invalidated is discarded, not stored (the
  generation check); the LRU budget evicts; a `data_dir`
  change clears everything; WorldView's section switch issues exactly one
  count request, and a reclassify refreshes both affected kinds; existing
  suites keep passing unchanged, which is the payload-shape guarantee.
- **Frozen campaign**: the read-only sweep must not change — the memos are
  pure reuse, and `snapshot.json` not moving is the proof.

## Success criteria

Navigating between unchanged pages does no frontmatter or transcript
parsing on the server (asserted by the counting tests, observable as `304`s
in the log), and revisiting a list page paints the previous payload with no
network wait. Cold-start listing cost is unchanged by design; if cold cost
is still felt after this ships, the stored-aggregate follow-up (explicitly
out of scope here) is the next spec.
