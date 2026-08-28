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
  request (`statcache` exists but only memoizes card summaries and sync
  hashes); there are no conditional requests, and the client only dedupes
  GETs that are literally in flight at the same moment.

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
- **The synced-store failure mode is out of scope** — the reference setup
  syncs with syncthing (real local files), so there is no hydration cost to
  design around.

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
  signature, so an actor rename invalidates naturally. This removes the
  full-transcript parse from the navigation path.
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
- **Sweep-sized memo families get their own pools.** The shared FIFO is
  4096 entries; scene heads and turn counts across a large library could
  evict every card summary and sync hash on one listing. Scene-head and
  scene-turns memos use the `pool=` parameter with their own budgets, as
  `store/search.py` does.
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

**`store/epoch.py`** — a process-local, in-memory read epoch: one opaque
token (a fresh `uuid4`), replaced by `bump()`. Not persisted, on purpose: a
restart mints a new token, so no client can ever get a false `304` from a
value that survived a restart. Assignment is atomic under the GIL; there is
nothing to lock.

**What bumps it** — the same "one funnel, plus the writers that outlive
their response" argument `store/revision.py` is built on:

- `revision.bump(cid)` bumps the epoch as a side effect. That covers every
  campaign write: the activity middleware's stamp, and every detached-run
  self-stamp (turn terminals, review saves, follow-up commits, prompt
  captures, steering appends) — those already converge on `bump`.
- A new, deliberately coarse trigger in the middleware layer for everything
  `revision` does not see: any **mutating method** under `/api/` that
  answers 2xx and is not `@computes_only` bumps the epoch — worlds, config,
  modules, connections included. The campaign-scoped subset double-bumps
  (once here, once via `revision.bump`); a second bump is free and the
  redundancy is what makes "a route added tomorrow is covered" true in both
  directions.
- The middleware's exception path (a write that raised mid-way) bumps too,
  exactly as it stamps the revision today: over-invalidation is the
  direction this value is allowed to be wrong in.
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
first, and returns the `304` before doing any work. The opt-in set is the
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
keyed by request path. A small hook (`useCachedGet` beside the existing
patterns) hands a view the cached payload synchronously — marked stale —
and swaps in the fresh answer when the refetch lands. `useShellPayload`
already implements exactly this posture ("the rail's first job is
navigation, and navigation must survive a server that stopped answering");
this generalizes it to the pages.

Consumers: `CampaignsView` (campaign + world lists), `WorldsView`,
`WorldView` (the index counts), `CampaignHub` (its parallel block of
reads). Play-path and editor reads do **not** opt in — a transcript or a
record body rendered stale is wrong in a way a count or a card shelf is
not.

**Invalidation reuses what exists:**

- The `appEvents` bus already broadcasts `campaignsChanged` /
  `configChanged` / `shellChanged` after mutations; the cache subscribes
  and drops the affected entries.
- A store-root change (`configChanged` with a new `data_dir`) clears the
  whole cache — the same identity rule `useShellPayload.keyOf` enforces:
  never render the previous library's numbers, not even as a stale frame.
- Entries are also replaced whenever a fresh answer lands, so the cache can
  never serve older data than the last thing the user saw.

**WorldView stops re-asking everything.** On a section switch, only the
count of the section being *left* is refreshed (that is the one the user
could have changed), keeping the full re-ask for `populated` (a scenario
import creates records in half a dozen sections at once). Combined with the
cache, switching sections renders the previous counts instantly and
corrects at most one of them.

## What correctness rests on

- **A stat signature is a real boundary.** Every store write goes through
  `store.atomic` (guard-enforced), which replaces via rename — mtime and
  dir entries move on every write, including one from another process
  syncing the folder. The racy window covers coarse filesystem clocks.
- **The epoch can only be wrong in the safe direction.** Missed-bump bugs
  are the dangerous class; the design makes the bump redundant (funnel +
  middleware) and accepts over-bumping everywhere else.
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
  invalidates on an actor rename (name-in-key); pool isolation proves a
  library-wide sweep leaves the shared cache's entries alone.
- **Epoch + ETag** (TestClient): a cacheable GET carries `ETag` and
  `Cache-Control: no-cache`; a repeat with `If-None-Match` answers `304`
  with no store reads (counting fake again); any write — campaign-scoped,
  world-scoped, config — flips the answer back to `200` with a new tag; a
  run-poll GET never carries an `ETag`; `@computes_only` routes don't bump.
- **Intra-request dedup**: read-counting tests per fixed handler (shell
  parses each `campaign.md` at most once per request, etc.).
- **Frontend** (vitest): the hook renders a cached payload synchronously
  and swaps on settle; `campaignsChanged` drops the entry; a `data_dir`
  change clears everything; WorldView's section switch issues exactly one
  count request; existing suites keep passing unchanged, which is the
  payload-shape guarantee.
- **Frozen campaign**: the read-only sweep must not change — the memos are
  pure reuse, and `snapshot.json` not moving is the proof.

## Success criteria

Navigating between unchanged pages does no frontmatter or transcript
parsing on the server (asserted by the counting tests, observable as `304`s
in the log), and revisiting a list page paints the previous payload with no
network wait. Cold-start listing cost is unchanged by design; if cold cost
is still felt after this ships, the stored-aggregate follow-up (explicitly
out of scope here) is the next spec.
