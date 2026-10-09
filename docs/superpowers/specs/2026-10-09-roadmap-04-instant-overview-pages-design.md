# 04. Instant Worlds, Campaigns, Todo and shell

**Status:** Draft — spec gate (`/codex:adversarial-review`) pending.
**Date:** 2026-10-09
**Roadmap:** 04 in `ROADMAP-CHECKLIST.md`. Lane: cache (03 -> 04 -> 05 -> 06).
**Baseline:** `main` at `35c1fb7`.
**Reconciles:** the bundle draft `04-instant-worlds-campaigns-todo.md`
(2026-10-06); the reconciled 03 spec
(`2026-10-09-roadmap-03-content-addressed-compiled-cache-design.md`), whose
sections 2a, 5, 6, 7, 8 and 9 bound what this spec may cache; and the
not-landed parts of `2026-08-28-read-path-performance-design.md` (its layer 1
`_scene_turns` memo, layer 3 epoch/`ETag`, and layer 4 render-then-revalidate).
This spec supersedes that spec's layer 4 for the four pages named here and
records why it does not land layer 3 (section 6.6).

> Implementation rule: re-read the code and every PR landed on this subsystem
> since the baseline before planning. In particular, re-read 03 as it landed:
> this spec is written against 03's contract, not its implementation, and every
> API name below that belongs to 03 (`compiled.derive` and friends) is a
> placeholder for whatever 03's plan named it.

## Depends on

| Contract | Provided by | What this spec uses it for | Hard or soft |
|---|---|---|---|
| 03-C1 | 03 | Composite keys: the card rows over one file, `scene_summary` over a collection digest, `scene_turns` over a file plus a non-file input (player names), `continuity_summary` over five absent-ok files (section 3). Also needs a collection **member filter** (section 3.4); see Open question 8. | Hard |
| 03-C2 | 03 | Liveness by construction. It is why Grimoire's own writes need no server-side retirement step (section 5.1) and why an external edit is seen on the next read (section 6.5). | Hard |
| 03-C4 | 03 | The validate-and-hash primitive behind every key, and its rule 5 registry flag: every kind here is registered "may use persisted `sources`", and 03's guard keeps them away from decision sites (section 3.7). | Hard |
| 03-C5 | 03 | Storing artifacts at write time: the turn path warms `scene_head` and `scene_turns` for the scene it just wrote (04-C2a). | Hard for 04-C2a only; the rest of 04 works without it |
| 03-C6 | 03 | Batch lookup of `scene_head` artifacts for a campaign's live scene set on a `scene_summary` miss (section 3.4). | Soft: without it the miss path does one lookup per scene |
| 03-C3 | 03 | Nothing directly. Every artifact 04 stores leaves `materialized` rows because 03 writes them; 05 reads them. | Soft (05's) |
| 03 kill switch, section 13 | 03 | `GRIMOIRE_COMPILED_CACHE=0` for the cached-versus-uncached equivalence tests (section 12). | Soft |

No 01x contract is used. Nothing here makes an LLM call.

## Required by

| Contract (provided here) | Consumer | What the consumer uses it for |
|---|---|---|
| 04-C1a, 04-C1b | 05 (05-C3) | The overview kinds are registry entries with byte-fed computes, so 05's eager rebuild can rebuild them from the `materialized` record without calling route code. |
| 04-C1c | 05 | The per-scope composition is what 05's sync summary can say it refreshed ("campaign X's scene and continuity projections"), without naming content. |
| 04-C2a | 05 (05-C1) | The warm hook 05's in-process sync primitive calls from every writer. 04 wires it at one site; 05 generalises it. |
| 04-C2b | 05 | The client's consistency bound. 05 promises "the same guarantee after `cache sync`"; this is the guarantee. |
| 04-C3a | 05, 08 | The counters 05 and 08 report their own hit/miss and rebuild costs through. |
| 04-C3b | 05, 08, and 03's own plan acceptance | The synthetic library and its harness. 03 section 14 already requires a committed generator; see Open question 7. |

## 1. Current state (reconciled against main)

### 1.1 What each page reads today

**Worlds.** `GET /worlds` (`routes/worlds.py:72-81`) lists worlds through
`store.worlds.list_worlds` (`store/worlds/read.py:100-107`) and adds a cover
token per row (`store/covers.py:224-241`).

- The row's fields come from `_world_row` (`store/worlds/read.py:24-43`), an
  in-process `statcache.memo` over `world.md`: `{name, created, updated,
  genre}`. The `id` and the name fallback are the directory name.
- The `counts` are nine live listings per world (six entity kinds, characters,
  PCs, greetings: `entities.py:486-491`, `characters.py:880-884`,
  `pcs.py:569-572`, `greetings.py:208-210`). They are deliberately not
  memoised (`store/worlds/read.py:27-28`).
- The page (`frontend/src/routes/WorldsView.tsx`) renders the cover, name,
  genre and a footer built from the counts (`WorldsView.tsx:12-20`, `:225-238`).

**Campaigns.** `GET /campaigns` (`routes/campaigns.py:156-191`) walks
`list_campaigns` (`store/campaigns/read.py:150-163`, rows memoised by
`_campaign_row`, `:110-147`) and then, per campaign:

- `list_scenes(cid)` (`store/scenes/read.py:72-83`): a listing of
  `scenes/*.md`, one `scene_head` memo per scene (`:30-69`, its own 65536-entry
  pool, `:22-27`), and `_resolve_groups` (`:86-112`), which derives `closed_by`
  across branch siblings;
- from that list: `scenes`, `absorbed`, `last_scene` (the first row's title),
  and `activity`, which is `best_stamp` over `campaign.md`'s `updated`, the
  activity file and every scene's `updated` (`campaigns/read.py:289-342`);
- a cover token (`covers.cover_version`, `store/covers.py:128-141`).

`CampaignsView` also calls `listWorlds()` (`CampaignsView.tsx:91-94`), for
world names and the filter column, and so pays every world's nine count
listings for names it could have had without them.

**Shell.** `GET /api/shell` (`routes/shell.py:281-324`) runs on every
navigation. Its campaign block (`:141-212`) reads:

- `read_campaign` for the name and world (`:155`), and `world_name` for the
  world's name (`store/worlds/read.py:145-154`);
- `list_scenes` for the scene count and the open scenes;
- **the full transcript of every open scene**, parsed through `read_scene`
  (`store/scenes/read.py:159-166`) to count model replies (`_scene_turns`,
  `shell.py:96-116`). This is not memoised. The read-path spec's layer 1 asked
  for it (`2026-08-28-...:83-100`) and it did not land;
- `_pending`, a glob of `*.review.json` plus a read of each (`:67-93`);
- `ctx.coverage()`, i.e. `sheets.coverage` (`store/sheets/tally.py:120-136`),
  which starts with `modules.binding.resolve` (`store/modules/binding.py:52-73`).
  For a campaign that sets no module, that calls `worlds_read.read_world`
  (`binding.py:63`), which computes the world's nine count listings
  (`store/worlds/read.py:141-142`) only to read one frontmatter key;
- `_ledger_open`, a parse of the commitments and continuity ledgers (`:215-224`);
- `_images_undescribed`, a walk of every version folder of every record in the
  campaign's world (`:119-138`);
- `_money`, `usage_rollup.campaign_totals` (`:227-244`). That module owns its
  own bookmark semantics and stays out of this spec (03 section 8 says the
  same).

The shell's `todo` field is now always `null` (`shell.py:321-323`), and the
rail draws no Todo count (`frontend/src/shell/rail.ts:141-145`). The draft's
premise that "shell badges should reuse the Todo projections" therefore
becomes: the shell's campaign block reuses the same scene and continuity
projections that Todo's campaign chores read.

**Todo.** `GET /todo` (`routes/todo.py:1305-1308`) calls `live(cid)`
(`:1244-1285`).

- With a campaign, it runs the ten `CAMPAIGN_BUILDERS` (`:774-789`) for that
  campaign.
- Without one, which is the global page, it runs the nine `LIBRARY_BUILDERS`
  (`:791-801`) and then **all ten campaign builders for every campaign in the
  library**.
- The ignore set (`store/chores.py:47-64`) is applied last, to the composed
  list.
- `_Ctx` (`:57-205`) shares derivations within one request and is thrown away
  after it. The module docstring (`:1-26`) and `store/chores.py:5-14` both
  promise "nothing is cached", with one read-only exception, the continuity
  candidate cache.

Section 4.4 lists each chore with its inputs.

### 1.2 What the read-path spec landed, and what it did not

Landed:

- `statcache.signature` gained `st_ino` (`store/statcache.py:30-56`).
- Per-pool budgets (`memo(..., pool=, max_entries=)`, `:59-89`).
- The `scene_head`, `campaign_row` and `world_row` memos.
- `memo_stamped`, with directory stamps vouching for listings and `ctime` in
  the stamp (`:107-187`).
- Some intra-request sharing: `_Ctx.coverage`, and `list_world_rows` for the
  chores (`store/worlds/read.py:86-97`).

Not landed:

- **Layer 3.** There is no `store/epoch.py`, and no `ETag`/`304` on JSON reads.
  The only `ETag`s are on images and static files (`routes/common.py:827-904`,
  `main.py:107`). 03 section 2 records the same.
- **The `_scene_turns` memo** (1.1 above).
- **Layer 4 as designed.** There is no `api/readCache.ts` and no
  `useCachedGet`. A narrower mechanism landed instead: **remembered reads**
  (`frontend/src/api/client.ts:238-404`). It keeps the last good answer for
  exactly three reads (`getWorld`, `listCharacters`, `listAppearances`) in
  memory only, under these rules:
  - keys are scoped by store root;
  - an LRU of `MEMO_MAX = 24`;
  - an epoch that `writing()` bumps before and after every `/api/worlds` or
    `/api/campaigns` write;
  - in-flight answers are discarded if issued before a forget (`issuedIn`).

  An intent prefetch (`api/prefetch.ts`) fills it.

### 1.3 First paint today

- **WorldsView** starts from `[]` (`WorldsView.tsx:24`, `:40-41`). Every visit
  paints an empty grid and "0 worlds" until the answer lands.
- **CampaignsView** starts both lists from `[]` (`:74-75`, `:91-94`). Before
  the first answer it renders the **"No worlds yet"** empty state with a
  "Create a world" link, so for a moment it shows wrong content, not merely no
  content.
- **TodoView** calls `setData(null)` on every load (`TodoView.tsx:129-139`). The
  page is blank until the server answers, on every visit and after every
  ignore.
- **The rail** (`shell/useShellPayload.ts`) keeps the last payload for its
  `(data_dir, cid)` key and refetches. Within a tab it already paints at once;
  after a page reload it waits.

### 1.4 Drift found while reconciling

- **The campaign card's module chip never renders.** `CampaignsView.tsx:329`
  renders `c.module`, and `CampaignMeta.module` is typed (`api/types.ts:783`
  onward). `campaign.md` carries `module` (`store/campaigns/lifecycle.py:120`).
  But `_campaign_row` never projects it (`campaigns/read.py:118-143`). Open
  question 4.
- **`characters.roster` is not memoised.** It re-parses every `character.md`
  on every call (`store/characters.py:750-782`). Todo reaches it through
  `overlay.character_sidecars` (`store/overlay.py:1478-1498`; the campaign
  `taglines`/`anchors` chores), the campaign `avatars` chore and
  `world-avatars`. The global page therefore parses every character file of
  every world, more than once, on every read, in the same process. The
  sibling `name_and_versions` (`characters.py:364-390`) already shows the
  stamped-memo pattern that fixes it.
- **`binding.resolve` pays for counts.** See 1.1 (Shell).
- **`_items_unreviewed` lists every scene even when nothing is waiting.** It
  builds the title map before the glob loop (`todo.py:871-878`), so every
  campaign whose `scenes/` directory exists pays `list_scenes`, whether or not
  any review sidecar exists.
- **Post-mutation refreshes that are not `fresh`.** `CampaignsView`'s rename
  and delete (`:169`, `:175`) and `WorldsView`'s create, rename and delete
  (`:60`, `:67`, `:78`) re-read without `fresh`, so they can join a read issued
  before their own write. The read-path spec's section 4 made `fresh` the rule
  for these. Once every visit starts a background revalidation (section 6),
  that window is open on every mutation rather than rarely.

### 1.5 What 03 allows this spec to cache

Restated because every decision below follows from it:

- A key is computed from the current bytes, so a stale row is unreachable
  (03 section 9, 03-C2).
- **Member counts stay live listings** (03 section 7). A world's nine counts
  and a campaign's scene count are listings, and caching them saves nothing.
- **Predicates over members' content** are what collection digests key (03
  section 7).
- **A Todo or shell projection is allowed only when its key covers every input
  it reads** (03 section 8). Chores read the ignore set, config and routing,
  the usage ledger and the continuity candidate cache, and "a chore whose
  inputs cannot all be named in its key stays live".
- Computes receive the bytes the key was computed from, never a path (03
  section 6).
- A persisted `sources` row may vouch only for kinds whose wrong answer is
  visible and recoverable, never at a decision site (03 section 5, rule 5).

## 2. Goal

1. **The four overview surfaces paint useful content immediately on every
   in-tab visit after the first**, including after a backend restart, and
   never paint the wrong library's content or a pre-write answer to the
   tab's own write (section 6).
2. **No overview request pays a cost that grows with a campaign's age.**
   Scenes, transcripts and ledgers are what grow with play. Each of them
   reaches these routes only through a content-keyed projection, so a warm
   read after a restart costs stats and lookups rather than parses (section 3).
3. **A change to one campaign recomputes that campaign's projections and no
   other's** (section 4.4).
4. **Todo stays derived and self-healing.** Every chore is still a live answer
   about the current files. What changes is that part of that answer is
   reused, under a key that covers all of its inputs.
5. **What "instant" costs is measured**, on a synthetic library, with counters
   that a test can assert (sections 8 and 9).

**Explicitly not the goal:**

- Caching member counts or anything else 03 forbids (section 1.5).
- Making cold-start-ever (empty cache, fresh tab) instant. That case is
  bounded, not eliminated.
- Persisting payloads in the browser across a page reload (Open question 2).
- Landing the library epoch, `ETag` or `304` (section 6.6).
- Touching `usage_rollup`, the run registry, or any write path's semantics.

## 3. The projection kinds (04-C1a, 04-C1b)

### 3.1 The rule that chooses them

A surface's answer splits into two parts:

- **What grows with play:** scenes, transcripts, ledgers. These are
  history-proportional. Each becomes a projection kind whose key names every
  file it reads.
- **What is bounded by the roster or the world's size:** listings, stats, one
  placement, one small sidecar. These stay live, already statcache'd where they
  can be.

This is `routes/todo.py`'s own cost rule (`:13-17`), *"a count proportional to
the library's age would make it a page nobody opens"*, turned into a test for
what to project. It also keeps every projection's inputs inside one campaign
root or one world's `world.md`, which is what lets each key be named
statically (03 section 8).

### 3.2 The kinds

All six are 03 registry entries (03 section 4), registered by a new
`store/overview/` package (section 11). All may use persisted `sources` (03
section 5, rule 5): a stale answer from any of them is a stale card line, count
or badge, which the next edit, the kill switch or deleting
`.cache/compiled/` repairs.

| Kind | One per | Inputs (role -> source) | `params` | Output |
|---|---|---|---|---|
| `overview.world_row` | world | `meta` -> `world.md` | none | `{name, created, updated, genre}`, with `name` set to `null` when the key is absent |
| `overview.campaign_row` | campaign | `meta` -> `campaign.md` | none | `{name, world, created, updated, parent, forked_from_scene, blurb, module}`, with `name` set to `null` when absent |
| `overview.scene_head` | scene file | `scene` -> `scenes/<sid>.md` | none | `_scene_row`'s dict, with `title` set to `null` when absent and `_identity` kept |
| `overview.scene_summary` | campaign | `scenes` -> collection `scenes/*.md`, stems passing `safe_id` | none | section 3.4 |
| `overview.scene_turns` | scene file | `scene` -> `scenes/<sid>.md` | `players`: sorted player display names | `int`, or `null` when the bytes do not decode |
| `overview.continuity_summary` | campaign | `plot`, `commitments`, `events`, `continuity`, `candidates` -> the five JSON files at the campaign root (`plot.py:19`, `commitments.py:42`, `events.py:65`, `continuity/doc.py:64`, `continuity/candidates.py:91`), each absent-ok | none | section 3.5 |

**Path-derived fields are applied outside the cache** (03 section 6). The
name fallbacks are the directory name for a world or campaign
(`worlds/read.py:35`, `campaigns/read.py:121`) and the stem for a scene title
(`scenes/read.py:37`). The compute reports `null` when the frontmatter key is
absent, and the caller substitutes. This keeps `meta.get("name", d.name)`
exactly: a present-but-empty name stays `""`. Two identical `campaign.md`
files in two directories then share one artifact and still render two names.

**`module` is new on the campaign row.** It fixes the dead chip in 1.4. Open
question 4 decides what value it carries. The store's `list_campaigns` is not
changed, so the frozen campaign's sweep entry for it does not move (section
12.6).

### 3.3 Byte-fed computes

Each compute receives bytes from 03's API, never a path (03 section 6).

- **Decoding** must match the `read_text(encoding="utf-8")` it replaces,
  universal newlines included. `_first_paragraph` splits on `"\n\n"`
  (`campaigns/read.py:102`), and the transcript parser splits on lines. The
  compute decodes UTF-8 and then translates `\r\n` and `\r` to `\n`. The
  equivalence tests use CRLF and non-UTF-8 fixtures (03 section 14).
- **`scene_head` parses the head from the full bytes**, where
  `parse_frontmatter_head(p)` reads only the head from a path. The compiled
  layer has already read the whole file to hash it whenever the stamp moved.
  The plan adds `frontmatter.parse_head_text(text)` beside the path form, so
  the two parsers cannot drift, and an equivalence test holds them together.
- **`scene_turns`** runs `parse_frontmatter`, then
  `serialize._parse_messages(body, frozenset(players))`, then
  `turns._model_blocks` (`store/scenes/turns.py:72-78`). This is exactly what
  `read_scene` + `_scene_turns` do today (`scenes/read.py:159-166`,
  `shell.py:112-116`), minus the path.
- **`continuity_summary` needs a text seam** in the continuity readers.
  - Today `effective.Ledgers.load(cid)` (`continuity/effective.py:92-114`),
    `doc.read(cid)`, `doc.malformed(cid)` and `candidates.read(cid)` each read
    their own file.
  - The plan adds `*_from_text` twins (or one `Texts` bundle threaded through
    `pending.Current.load` and `effective.records` / `live_canon` / `links`).
    The existing path functions become "read, then call the twin", so there is
    one parser per file.
  - The test for "fed, not reading": run the compute under an audit hook
    (`sys.addaudithook`, recording `open`, `os.listdir` and `os.scandir`
    events) and assert it opens nothing.
  - If the call graph turns out to reach a file outside the five, that file
    joins the key, or the kind is not wired. No other option is acceptable.

### 3.4 `scene_summary`: the one collection kind

The key is 03 section 7's collection digest over the campaign's `scenes/`
directory.

**Member filter.** Members are `*.md` whose stem passes `safe_id`. This is
exactly `list_scenes`' enumeration (`scenes/read.py:77-79`). The same
directory also holds `<sid>.review.json` sidecars and other per-scene files. A
digest over the whole directory would move whenever a review was written,
and would miss for a reason the summary does not depend on. 03 section 7 says
"list the directory" and names no filter. **This spec needs 03-C1's
collection to be `(directory, member filter)`, with the filter part of the
kind's declaration and covered by its `version`** (Open question 8).

Output:

```
{
  "count":    int,                       # members that parsed
  "absorbed": int,                       # rows with done
  "rows":     [ {sid, title|null, updated, done, closed_by|null} ... ],
                                         # list order (below), one per scene
  "open":     [ {sid, title|null} ... ], # not done and no closed_by, list order
  "stamps":   [str ...],                 # distinct scene `updated` values that pass the
                                         # format round-trip, newest first
}
```

- **Order** is `updated` descending, then `sid` ascending.
  - Today's `list_scenes` sorts stably over `glob` order (`:81`), and glob
    order is the filesystem's directory order. Two scenes with the same
    `updated` (one-second stamps make that ordinary) can therefore order
    differently on two devices sharing one synced store, and `last_scene` can
    name either.
  - The projection fixes the tie-break. This is a deliberate change to
    `GET /campaigns`' `last_scene` and the shell's open order in tie cases
    only (Open question 6).
  - `list_scenes` itself is left alone (3.7).
- **`closed_by`** is `_resolve_groups`' rule (`:86-112`), computed over the
  members. It depends only on member content.
- **`stamps` keeps the clock out of the artifact.** `best_stamp` rejects a
  stamp that is implausibly far in the future against *now*
  (`campaigns/read.py:254-286`), so its answer depends on the clock. The
  projection keeps only the clock-free half: the format round-trip, plus
  de-duplication. `GET /campaigns` then applies
  `best_stamp(updated, read_activity(cid), *stamps)` live. The answer is
  identical, because an invalid or duplicate candidate can never be
  `best_stamp`'s answer. The list grows with the number of scenes, which is the
  same size as the head rows the in-process memo already holds.
- `rows` carries what Todo's open-scene chore and its items need. The items
  expansion (`_items_open_scenes`, `todo.py:902-911`) stays on live
  `list_scenes`: it also renders `date` and `place`, and it is on demand.

**Miss path.** The compute needs every member's head.

- Members whose `scene_head` artifact exists are batch-looked-up (03-C6) over
  the live member keys.
- The rest are parsed from their bytes, and their `scene_head` artifacts are
  stored in the same batch.
- So after one scene changes, the miss costs one head parse plus N lookups,
  not N parses.

**In-process layer** (03 section 13: statcache above, compiled below).

- `statcache.memo_stamped` keyed on `("overview.scene_summary", scenes dir)`.
- Its compute stamps the directory before listing it and each member before
  reading it (the contract at `statcache.py:152-163`), then calls the compiled
  layer.
- A warm same-process read therefore costs one stat of the directory and one
  per member, and **no listing**: the directory's stamp vouches for its listing
  in-process, exactly as `characters._build_identity` and the store guarantees
  (`docs/store-guarantees.md`, "Reads notice external writes") already rely
  on.
- The persisted layer never treats a directory stamp as a voucher (03 section
  7). It lists.

### 3.5 `continuity_summary`

Output: `{ledger_open, owed, overlaps, closures, closures_threads}`. Each
field keeps the failure semantics of the code it replaces:

| Field | Today | Failure today -> here |
|---|---|---|
| `ledger_open` | `len(effective.commitments(cid))` (`shell.py:221-224`) | any exception -> `null` |
| `owed` | `len(_owed(cid))`, commitments with a `due` (`todo.py:368-393`) | `OSError`/`ValueError` -> `null`, and the chore is absent |
| `overlaps`, `closures` | `continuity_pending.findings`, `live` verdicts, by `CHORE_OF` (`todo.py:113-127`, `:396-432`) | `OSError`/`ValueError`/`CampaignNotFound` -> `0` |
| `closures_threads` | whether any live closure is a `possible_thread_closure` (`todo.py:424-425`) | as above -> `false` |

**Content-determined failures are cached; I/O failures never are.**

- The compute receives bytes, so a `ValueError` from a garbled ledger is a
  property of those bytes. Caching the `null` under their hash is exactly as
  true as caching a number.
- An `OSError` can only happen in 03's read step, before any compute runs. 03
  section 12 turns it into "compute live, store nothing".
- This is the read-path spec's "only successful derivations are memoised"
  rule (`2026-08-28-...:120-127`), made precise for byte-keyed artifacts.

**Clock check.** `pending.verdict` (`continuity/pending.py:255-280`) and
`effective` read no clock. `_owed` "does no calendar work" by design
(`todo.py:372-374`). The plan's audit-hook test also patches
`paths.now_iso` and `time.time` to raise, so a future clock read inside the
compute fails loudly instead of freezing an answer in the artifact.

### 3.6 Failure and absence, everywhere

- **An absent input is an input.** For absent-ok roles it carries 03's
  absent sentinel, so creating the file moves the key. A campaign with no
  `commitments.json` gets a real projection, not a perpetual miss. The
  read-path spec found the same trap for appearances (`2026-08-28-...:90-99`).
- A projection that cannot be computed because the campaign or world is not
  there raises the same exception the live code raises. Routes keep their
  current mapping: 404 for `GET /todo?campaign=`, `campaign: null` for the
  shell, and the row skipped for the shelves.
- The cache is never the reason a read fails (03 section 12). With the cache
  off, busy or corrupt, every kind computes live and returns the same value.

### 3.7 Who may reach an overview kind

**Only the overview read routes:**

- `GET /worlds`;
- `GET /campaigns`;
- `GET /api/shell`;
- `GET /todo`.

Not `GET /todo/{id}/items` (on demand, live), and not `PUT /todo/{id}/ignored`.

**The store functions they replace stay as they are**, in-process only:

- `list_worlds`, `list_world_rows`;
- `list_campaigns`, `read_campaign`;
- `list_scenes`, `closed_by`, `read_scene`;
- `effective.*`, `pending.*`.

This is load-bearing:

- `scenes.read.closed_by` (`:115-132`) is asked on **every turn's
  reservation** and refuses a closed branch. That is a decision. If it read a
  persisted `scene_head`, 03 section 5's permanent residuals (Windows ctime,
  FAT) could refuse or admit a turn wrongly for the life of a row.
- `list_campaigns` feeds `put_todo_ignored`'s legacy-ignore migration
  (`todo.py:1349-1351`), which is a write.

The overview package therefore exposes **new entry points**
(`overview.world_rows()`, `overview.campaign_cards()`,
`overview.scene_summary(cid)`, `overview.scene_turns(cid, sid)`,
`overview.continuity_summary(cid)`) and does not re-implement the old ones on
top of the cache. 03's guard (03 section 5, rule 5) is given these kinds and
this route allowlist.

## 4. Pages composed from the kinds

### 4.1 `GET /worlds`

`overview.world_rows()`:

- the live listing (`_listed`, `worlds/read.py:46-60`);
- `overview.world_row` per world, with the name fallback applied;
- the nine **live** counts (03 section 7);
- the live cover token.

The payload is byte-identical to today's.

The server saves one small parse per world after a restart. 03 section 8
already says this is not where a performance claim is made. **Worlds is
instant because of section 6, not this.** The kind is here because it is the
cheapest end-to-end proof of the wiring, and because the shell and Todo need
world names without counts.

### 4.2 `GET /campaigns`

`overview.campaign_cards()`, per campaign:

- the `overview.campaign_row`;
- the `overview.scene_summary` (`count`, `absorbed`, `rows[0].title` ->
  `last_scene`, `stamps`);
- live: `read_activity` (one small file, `campaigns/read.py:321-342`),
  `best_stamp` over it, and the cover token.

The payload is byte-identical except for `module` (3.2) and the tie-break
(3.4).

Cost after a restart: one listing of `campaigns/`, then per campaign one
listing of `scenes/`, one stat per scene plus a batched `sources` lookup, two
artifact lookups and three small live reads. **No `campaign.md` parse and no
scene head parse** for anything unchanged. Today a restart re-parses every
scene head in the library.

### 4.3 `GET /api/shell`

| Field | Today | After 04 |
|---|---|---|
| `campaigns` | `len(list_campaigns())` | live listing count, rows from `campaign_row` |
| `_most_recent` | rows + activity read | same, rows from `campaign_row` |
| `campaign.name`, `world` | `read_campaign` | `campaign_row` |
| `world_name` | `world_name(wid)` (parses `world.md`) | `world_row` |
| `scenes`, `open[].sid/title` | `list_scenes` | `scene_summary` |
| `open[].turns` | `read_scene` per open scene | `scene_turns` per open scene, `players` from `cast.player_names` (live, small reads; read the appearances record once per request, read-path layer 2) |
| `unreviewed`, `pending` | glob + read sidecars | unchanged (live; normally zero or one small file) |
| `sheets` | `ctx.coverage()` | unchanged (live, bounded by cast), and `binding.resolve` stops paying world counts (4.5) |
| `ledger_open` | parse ledgers | `continuity_summary.ledger_open` |
| `images_undescribed` | world walk | unchanged (live; Open question 1) |
| `money` | `usage_rollup` | unchanged |

**What a navigation costs while playing** (the common case). The shell refetch
after a turn lands still reads and hashes the played scene's transcript, since
its stamp moved. It no longer parses it: the turn path stored `scene_turns` for
exactly those bytes when it wrote them (04-C2a, section 5.2). Hashing costs
less than parsing, and the parse was this route's largest cost (read-path spec,
`:16-23`).

### 4.4 `GET /todo`: per-scope composition (04-C1c)

**A Todo scope is a composition, not an artifact.** The draft asked for
`todo_campaign_scope_vN(...)` as one materialised artifact per scope. That
would need a key over every input of every chore in the scope, and three
campaign chores (`sheets`, `avatars`, the sidecar pair) read inputs that cannot
be named before the compute runs:

- module packs, through `binding.resolve` and `modules_pack.load_pack`;
- image-store objects, named only inside placements (`overlay.avatar_v`,
  `store/image_store.py:119-122`);
- the overlay's tombstones and manifest across two roots.

03 section 8 forbids the projection in exactly that case. So a scope is:

```
campaign_scope(cid, name) = [ builder(ctx) for builder in CAMPAIGN_BUILDERS ]
library_scope()           = [ builder(ctx) for builder in LIBRARY_BUILDERS ]
live(cid)                 = apply_ignores( campaign_scope(cid) )            if cid
                          = apply_ignores( library_scope()
                                           + concat(campaign_scope(c) for c in campaigns) )
```

The builders stay as they are. **The chores whose cost grows with play now
read projections through `_Ctx`.** The rest stay live, and every chore is
classified:

| Chore | Scope | Inputs today | After 04 | Why |
|---|---|---|---|---|
| `open-scenes` | campaign | `list_scenes` (`todo.py:269-281`) | `scene_summary.open` | grows with play |
| `owed` | campaign | commitments + continuity ledgers | `continuity_summary.owed` | grows with play |
| `continuity-overlaps`, `-closures` | campaign | 5 ledger/cache files via `pending.findings` | `continuity_summary` | grows with play |
| `unreviewed` | campaign | `*.review.json` glob + reads; title map from `list_scenes` | live; title map built **only when a sidecar exists** (1.4), from `scene_summary.rows` | a listing plus usually zero files |
| `sheets` | campaign | module binding, pack, overlay cast, sheet files | live | inputs not nameable (pack, overlay); bounded by cast |
| `anchors`, `taglines` | campaign | `overlay.character_sidecars` (stats + roster) | live, roster memoised (4.5) | bounded by roster; stat-based by design (`todo.py:225-232`) |
| `avatars` | campaign | overlay roster + `avatar_v` (placements, objects) | live, roster memoised | late-bound object inputs |
| `cover` | campaign | one placement | live | one file |
| `world-describe` | library | per-world walk of version folders (`todo.py:586-616`) | live | walk-dominated; late-bound objects (Open question 1) |
| `world-taglines`, `-anchors` | library | stats (`todo.py:553-583`) | live | listing/stat cost already |
| `world-avatars` | library | `characters.roster` per world + version facts | live, roster memoised | as `avatars` |
| `world-covers` | library | one placement per world | live | as `cover` |
| `world-subjects` | library | greeting catalogs, sidecars, objects (`image_subjects.untagged`) | live | late-bound objects (Open question 1) |
| `unpriced` | library | two newest ledger months, memoised per file (`usage.py:1756-1806`); pricing; provider rates | live | rates are applied after the memo on purpose; the current month grows on every call |
| `unpriced-models` | library | settings, `inference:campaign_meta` memo | live | config-derived, already memoised |
| `embeddings` | library | `embed_space.resolve`, campaign listing | live | config-derived |

**What this makes true:**

- **No projection's key needs the ignore set, config, routing or the usage
  ledger.** The ignore set is applied after composition. Config, routing and
  the ledger are read only by live library chores. This is how 04 meets 03
  section 8's condition: projections read nothing those chores read.
- **A change to one campaign recomputes that campaign's projections and no
  other's** (Goal 3). Other campaigns' `scene_summary` and
  `continuity_summary` keys hit. Their live chores still run, but those are
  bounded by cast, not history. Being honest about that is part of the
  contract (04-C1c).
- **`has_campaign`** (`todo.py:95-107`) stops being "`list_scenes` did not
  raise". It becomes a campaign-existence check: `campaign.md` exists, and an
  `OSError` reads as no campaign. The gate is unchanged: an unknown or
  unreadable id gives no campaign chores. It no longer needs a scene listing to
  say so.
- **The docstrings that promise "nothing is cached"** (`routes/todo.py:1-26`,
  `:65-69`, `store/chores.py:5-14`, `TodoView.tsx:11-17`) are amended to the
  precise statement. Every chore is computed for this request. Some of its
  inputs are reused under a key covering every file they read, so a reused
  answer can never describe bytes that have moved. 03 section 8 promised this
  amendment.

### 4.5 Live-path fixes that ride with this spec

Each is small, keeps its output byte-identical, and removes a per-navigation
cost the reconciliation found. None of them persists anything.

1. **`binding.resolve` reads the world's `module` key without counting.** It
   replaces `worlds_read.read_world(...)["meta"]` (`binding.py:63`) with a
   frontmatter-only read that keeps `WorldNotFound` on a missing `world.md`.
   This removes nine listings from every shell read and every `sheets` chore
   of a campaign that inherits its module.
2. **`characters.roster` rows are memoised in-process.** Each `(id, name,
   default_version)` row is memoised by `statcache.memo_stamped`, stamped on
   the character directory (the version glob is the row filter) and on
   `character.md`, following `_build_identity` (`characters.py:384-`). Same
   walk, same filter, same order; the existing roster-versus-listing test holds
   it.
3. **`_items_unreviewed` and `_chore_unreviewed` take titles from
   `scene_summary.rows`**, and only after the glob has found a sidecar.
4. **Post-mutation refreshes pass `fresh`** in `WorldsView` and
   `CampaignsView` (1.4). This is required by section 6, not optional.

## 5. Grimoire's own writes, server side (04-C2a)

### 5.1 No retirement step

**The server needs no retirement step for correctness.** Every projection is
reached through a key computed from the current bytes (03-C2). After any write,
the next read computes a new key, misses, recomputes and stores. The old
artifact becomes unreachable at the moment the write lands, not at some later
sweep. The in-process layer above it is stat-keyed: an atomic write moves the
stamp, and `RACY_WINDOW_NS` refuses to trust a stamp younger than one tick
(`statcache.py:23-26`).

So "synchronous retirement of a projection on Grimoire's own writes" (the
checklist's 04-C2) is, on the server, a property 04 inherits rather than a
mechanism it adds. Section 6.3 is the mechanism, and it lives in the client,
the one place a pre-write answer can still be shown.

### 5.2 Warming at write time (03-C5)

What a write *can* do is make the next read a hit, by storing the new artifacts
for the bytes it just wrote (03 section 2a, item 5). 04 wires this at the one
site where it pays on every turn:

```python
# store/overview/warm.py
def warm_scene(cid: str, sid: str) -> None:
    """Store `scene_head` and `scene_turns` for the scene file as it now is.

    Best effort, and never raises. Reads the file back, so it is keyed on the bytes
    actually on disk (a newline-translating write, or a second writer landing
    in between, cannot make it vouch for bytes it did not read). Records no
    `sources` row (03 section 5 rule 4). Holds no lock."""
```

- **Where:** `routes/streaming._fire_follow_up` (`streaming.py:574-`). It runs
  after the turn's outcome is decided and outside every write path, it
  swallows its own failures, and it is gated on the turn having written
  something. Those are exactly the conditions this needs.
  - It is handed to a worker thread (`anyio.to_thread.run_sync`), never run
    on the event loop's thread (03 section 12).
  - It holds no campaign lock and no run exclusion key.
  - The plan confirms that `sid` is in scope there. `_turn_settled` receives
    only `cid`.
- **What:** `scene_head` from the bytes, and `scene_turns` with `players` from
  `cast.player_names(cid, sid)`, the same names the shell will ask with.
  - If the cast changes before the next shell read, that read misses on
    `players` and computes. It is correct, only not warm.
- **What not:** `scene_summary` is not warmed here. Its digest needs every
  member's hash, and the next read builds it from the in-process heads in one
  fold. The overview package (section 11) exposes `warm_paths(paths)` for 05.
  This spec calls only `warm_scene`.

**Every other writer warms nothing in 04.** A rename of `campaign.md`, an
absorb's ledger writes, a world edit: each next read rebuilds lazily, which
costs one small parse or one campaign's ledgers. Generalising the warm to all
writers, and choosing which kinds to rebuild eagerly from the `materialized`
record, is 05-C1 and 05-C3.

## 6. First paint: render, then revalidate (04-C2b)

### 6.1 Mechanism: extend remembered reads, do not add a second cache

The landed mechanism (`client.ts:238-404`) already has the rules the read-path
spec's `readCache.ts` was going to have: scoping by root, LRU, invalidation
epochs, the in-flight generation rule, and memory only. 04 extends it to three
more reads rather than building a parallel cache that would need the same
rules restated:

| Read | Key | Recall |
|---|---|---|
| `api.listWorlds(fresh?)` | `memoKey("worlds")` | `api.rememberedWorlds()` |
| `api.listCampaigns(fresh?)` | `memoKey("campaigns")` | `api.rememberedCampaigns()` |
| `api.getTodo(cid)` | `memoKey("todo", cid ?? "")` | `api.rememberedTodo(cid)` |

`getShell` is not added: `useShellPayload` already holds the last payload per
`(data_dir, cid)` for the life of the app (`useShellPayload.ts:26-34`), and it
is mounted once.

`MEMO_MAX` stays 24. These add at most one entry per page plus one per
Todo scope visited. The constant is still justified structurally
(`client.ts:283-289`) and tuned later.

### 6.2 What the pages do

A small hook, `frontend/src/api/useRevalidated.ts`:

```ts
function useRevalidated<T>(recall: () => T | undefined,
                           read: () => Promise<T>,
                           deps: unknown[]): {
  data: T | undefined;   // remembered on the first render, then the fresh answer
  stale: boolean;        // true while `data` came from memory and no answer has landed
  failed: boolean;       // the newest read failed
  reload: () => void;
}
```

- **Every mount issues the read.** Painting from memory never replaces asking.
  This is the remembered-reads safety argument (`client.ts:248-253`): a
  remembered value is on screen for at most one revalidation.
- **While `stale`,** the page's main region carries `aria-busy="true"`. There
  is no spinner and no greyed-out list. The draft's "treats it as stale
  internally" is that attribute plus the rules below.
- **On failure,** the remembered payload is **dropped** and the page shows its
  existing failure state. The rail is the exception: it keeps the last good
  payload because navigation must survive (`useShellPayload.ts:6-11`).
  Pages follow `remembering`'s rule instead (`client.ts:350-354`): a read that
  could not confirm the rows is no reason to keep painting them.
- **Empty states render only after a read has settled.** "No worlds yet",
  "No campaigns yet", "Nothing outstanding" and "0 worlds" are statements, and
  a statement made before any read is how `CampaignsView` tells a reader with
  ten worlds to create one (1.3).
- **Actions from a stale frame are allowed.** Rename, fork, delete, export,
  Ignore and Restore are all addressed by id, validated by the server, and
  followed by a `fresh` re-read. A stale frame cannot address another
  library's ids, because recall is root-scoped and returns nothing until a
  config read names the root (`client.ts:255-271`). The draft's warning, *"do
  not use stale-while-revalidate to hide authoritative write conflicts"*,
  applies to editors, and none of these pages is one.

Per page:

- **WorldsView:** `worlds` comes from the hook. Create, rename and delete
  refreshes pass `fresh` (4.5.4).
- **CampaignsView:** two hook instances, one for campaigns and one for worlds.
  Rename and delete refreshes pass `fresh`.
- **TodoView:** `data` comes from the hook, keyed on `cid`, and `load` no
  longer calls `setData(null)`. `ignoreAsync` keeps its explicit re-read,
  which is a write's refresh. Item expansions (`getChoreItems`) stay `fresh`
  and unremembered: they are on demand, and the reader is waiting on them.

**What does not opt in:** play-path reads, editor reads, record bodies,
`GET /todo/{id}/items`, and the campaign hub's chronicle. That last one keeps
the read-path spec's reason (`2026-08-28-...:348-356`): a recap painted from
before an absorb is a stale record body, not a stale count.

### 6.3 Retirement: what forgets, and when

1. **Every write this client sends forgets every remembered read, before it is
   sent and again when it settles.** `writing()` (`client.ts:394-405`) drops
   its `/api/worlds` / `/api/campaigns` condition.
   - Todo and the shelves depend on writes under `/api/config`,
     `/api/llm-connections`, `/api/inference`, `/api/todo`, `/api/modules` and
     image routes. Enumerating those is the per-endpoint list the existing
     comment declines to keep (`client.ts:388-393`).
   - Over-forgetting is the safe direction: the next visit paints after its
     read, as every visit did before 04.
   - The second forget, on settle, drops an answer that was issued while the
     write was on the wire (`client.ts:394-397`).
   - Every write path already goes through `writing()`: `requestRaw`,
     `streamPost`, `postZip`, and the draft starters
     (`client.ts:173, 995, 1007, 1067`).
2. **A run this client observes reaching a terminal state forgets.** A
   detached run writes after the request that started it has settled: a turn
   whose stream the client re-attached to, an absorb, or a review persisted to
   `pending_reviews`.
   - `draftRun`'s completion, the stream helpers' terminal frame, and
     `runs/RunRegistryProvider.tsx` when it sees a run end each call one
     exported `noteRunSettled()`, which forgets.
3. **A store-root change forgets.** This is unchanged: `noteRoot`,
   `putDataDir` and the cross-tab `ROOT_MOVED_KEY` (`client.ts:303-348`).
4. **An answer issued before a forget is never stored.** This is unchanged:
   `issuedIn` and `memoEpoch` (`client.ts:272-281`, `:355-369`).

### 6.4 Why `fresh` on post-mutation refreshes is now required

Every mount starts a background revalidation, so a pre-write GET is now
usually in flight when a shelf action completes. A non-`fresh` refresh would
join it (`request`'s in-flight share, `client.ts:418-429`) and render the
pre-write list as the post-write answer. `writing()`'s epoch keeps that answer
out of memory, but it does not keep it off the screen. The rule the fork and
import paths already follow (`WorldsView.tsx:124, 157`) therefore becomes
general for these pages. Inventory: section 4.5, item 4. A grep for
`list(Worlds|Campaigns)\(\)` after a write finds new ones.

### 6.5 The consistency bound (what 05 inherits)

For these four surfaces:

- **A write this tab made** (any non-GET through `api`) is never followed by a
  painted answer from before it.
- **A detached run this tab observed end** is never followed by a painted
  answer from before its end.
- **Anything else** may be shown for exactly one frame of the next visit, with
  `aria-busy`, before that visit's own read replaces it wholesale. This covers
  another tab, another process, another device through a sync client, a hand
  edit, an agent's direct edit (with or without 05's `cache sync`), and a
  detached run nobody watched.
- **Every server answer is current** with respect to the file bytes at the
  moment of the read (03-C2), up to 03 section 5's stated residuals for kinds
  that use persisted `sources`.
- **After a page reload** nothing is shown from memory at all.

`docs/store-guarantees.md` gains this as a short subsection under "Reads
notice external writes".

### 6.6 The library epoch and `ETag`: not landed here, not depended on

The read-path spec's layer 3 is still unbuilt. 04 neither lands it nor needs
it, for four reasons:

1. **First paint no longer waits on the server.** A `304` would shorten a
   revalidation that the reader is not waiting for.
2. **The epoch is one token for the whole library**
   (`2026-08-28-...:301-310`). During play, which is when people navigate, every
   turn bumps it, so the revalidations that matter would mostly be `200`s
   anyway.
3. **Its bump inventory is a separate correctness surface**: a status-blind
   wrapper on every mutating method, a bump on every ledger append, and two
   bumps per streaming mutator. Its failure mode, a `304` on a stale body that
   is then believed, is worse than 04's, which is one stale frame that is
   always revalidated.
4. **After 04, a `200` on an unchanged library costs listings, stats and
   lookups.** Layer 3 existed partly to stop the per-navigation parse sweeps,
   and the projections stop those anyway.

The door stays open. A later spec can derive a per-response validator from the
projection keys a response was composed from plus its live fields. That
validator would be exact rather than time-bucketed, but it still costs the
listings, so it saves only encode and transfer. Open question 5 asks whether
to mark the read-path spec's layer 3 superseded.

## 7. Startup

There is no startup sweep (draft section 8; 03 section 3, "rebuilt lazily, by
reads, and never by a startup sweep"). The first overview read after a restart:

1. lists the top-level directories, which are live;
2. stats members and looks their stamps up in `sources` in a batch, so only
   files whose stamp moved, or that are inside `PERSIST_WINDOW`, are read and
   hashed;
3. looks up artifacts for the keys, in a batch;
4. computes only the misses.

`BUILD` (03 section 6) makes every artifact cold once after an upgrade, or
after each pull for someone running from a checkout. The first visit after an
upgrade then costs what a cold visit costs today, plus one hash per file read.
The benchmark measures it (section 9, scenario `cold`).

## 8. Instrumentation (04-C3a)

**A leaf module, `store/readstats.py`,** holds a request-scoped counter set in
a `contextvars.ContextVar`:

```python
@contextmanager
def collect() -> Iterator[Counters]: ...        # opens a set for this context
def bump(name: str, n: int = 1) -> None: ...    # no-op when none is open
def snapshot() -> dict[str, int]: ...
```

It imports nothing from `store`, so `statcache` and `frontmatter` can import
it and stay leaves (03 section 13; `test_import_guard.py`). `bump` with no open
set costs a `ContextVar.get` and a `None` test.

**Counters**, with names stable because tests assert on them:

| Counter | Bumped by |
|---|---|
| `statcache.hit.<kind>`, `statcache.miss.<kind>` | `memo`, `memo_stamped` |
| `frontmatter.parse`, `frontmatter.parse_head` | `parse_frontmatter`, `parse_frontmatter_head`, and the new text form |
| `transcript.parse` | `serialize._parse_messages` |
| `compiled.hit.<kind>`, `.miss.<kind>`, `.store.<kind>`, `.bypass` | 03's `derive` (Open question 9) |
| `compiled.sources_hit`, `.sources_miss`, `.bytes_hashed`, `.collection_digest` | 03's validate-and-hash and collection paths |
| `overview.computed.<kind>` | the overview computes |
| `todo.scope.library`, `todo.scope.campaign` | `live()` |
| `todo.chore.<id>` | each builder run |

**Where they surface:**

- **In-process, to tests and the harness.** This is the primary consumer.
- **One Debug-level log row per overview request**, through `logs.record`
  (the only writer; `CLAUDE.md`, Observability), with `kind="overview_read"`,
  the endpoint, the scope kind (`library` or `campaign`), wall milliseconds and
  the counter snapshot.
  - No ids, names, paths or payload, even though ids are permitted in logs.
  - It is Debug because it fires on every navigation. The level is the user's
    choice and the monthly cap still applies.
  - It never raises, which is `record`'s own contract.
- **Nothing in `/stats`.** `store/metrics.py` takes percentiles over LLM calls
  from the usage ledger. Route latency would be a different question with a
  different source, and this spec does not add a second store for it.

**Harness-only counts** (section 9) that need no production code:

- files opened, and their sizes at open;
- directories listed.

Both come from a `sys.addaudithook` that the harness installs, recording
`open`, `os.listdir` and `os.scandir` events while a request runs. `stat` has
no audit event, so stats are not counted. They are cheap anyway, and the
`sources` counters stand in for them.

## 9. Synthetic library and benchmark (04-C3b)

### 9.1 Generator

`backend/scripts/synth_library.py`:

```
python -m scripts.synth_library --out DIR --profile {small,medium,large} --seed N
```

- **It writes only through the store's own public writers**, with
  `GRIMOIRE_HOME=DIR`: `worlds.create_world`, the character and entity
  writers, `campaigns.create_campaign`, scene creation and `append_message`,
  the commitments and plot writers, and review sidecars.
  - Every file is therefore in the current format, by construction.
  - The generator writes nothing that was not produced through a store writer.
    It is never a template of hand-written files.
- **Names** combine the placeholder set with an index: worlds `Realm 3`, places
  `Saltmarch 12`, characters `Seraphine 7`, `Mara 2`, `Winifred 11`. They are
  never drawn from anywhere else.
- **Prose** comes from a fixed nonsense vocabulary in the script, shuffled by a
  seeded RNG. It is never read from any file, any store or the network.
- **Images** are generated solid-colour PNGs, ingested through
  `assets.put_in`, so placements and objects are real.
- **The library is dated deterministically.** `paths.now_iso` is patched to a
  seeded clock during generation, and file mtimes are backdated past
  `PERSIST_WINDOW` (03 section 5), so a freshly generated library caches like
  an old one.
- **It writes `DIR/.synthetic-library`**, holding the profile, seed and
  generator version, and it refuses a non-empty `DIR`.

**Profiles** are tables of structural knobs: worlds; characters, entities,
greetings and images per world; campaigns; scenes per campaign; posts per
scene; review sidecars; ledger rows; continuity candidates. **The plan sets the
numbers. Each one is justified by the boundary it exercises, and none is drawn
from a real library** (`CLAUDE.md`, Privacy). For `large`:

- total scene files above `statcache.MAX_ENTRIES` (4096), so a run proves that
  the scene pool keeps the shared FIFO intact;
- enough posts per scene that a transcript parse visibly dominates a stat and
  a hash;
- at least one campaign far larger than the rest, for the "one campaign
  changed in a large library" scenario.

**Coordination with 03.** 03 section 14 already requires "a generated
synthetic library ... The generator is committed", for 03's own acceptance.
Whichever plan lands first builds the generator to this section, and the other
extends it (Open question 7).

### 9.2 Harness

`backend/scripts/bench_overview.py --library DIR [--repeat N] [--json OUT]`:

- **Privacy guard.**
  - It refuses unless `DIR/.synthetic-library` exists.
  - It refuses if `DIR` resolves to the default store location (`~/.grimoire`)
    or to the path named by the bootstrap pointer. It reads only the pointer's
    path value, never the store.
  - It sets `GRIMOIRE_HOME=DIR` itself.
  - Its output holds endpoint names, scenario names, counters and timings, and
    nothing else, even though the library is synthetic. One output shape for
    every library is how a later edit cannot start printing names.
- **Endpoints:** `GET /worlds`, `GET /campaigns`, `GET /todo`,
  `GET /todo?campaign=<largest>`, and `GET /api/shell?campaign=<largest>`, all
  through `TestClient` on one app per scenario.
- **Scenarios:**

| Scenario | Setup |
|---|---|
| `cold` | delete `.cache/compiled/`, clear statcache pools, new app |
| `warm_same_process` | `cold` once, then measure the second request |
| `warm_after_restart` | `cold` once, then new app and statcache cleared, with `.cache/compiled/` kept |
| `relevant_change` | after warm: append a post to one scene of one campaign (store writer); separately, edit that campaign's `commitments.json` |
| `unrelated_change` | after warm: edit one lore entry in a world, through the entity writer |
| `one_campaign_in_large` | `large` profile; after warm: change one campaign; compare per-kind miss counters to the campaign count |
| `cache_off` | `GRIMOIRE_COMPILED_CACHE=0`, as the baseline |

- **Output:** per scenario and endpoint, the median wall time over `--repeat`
  plus the counter snapshot, and the harness-only open/list counts.
- **What is committed:** the two scripts, and the counter assertions in
  section 12. **No timing figure is committed in 04.** A CI warning budget in
  the style of `backend/tests/perf_budget.json` is a later decision (Open
  question 10).

## 10. Contract

**04-C1a. Card projections.**

- `overview.world_row` and `overview.campaign_row` are 03 registry kinds:
  byte-fed, persisted-`sources`-allowed, one input each (`world.md`,
  `campaign.md`). They return the row fields with `name: null` for an absent
  key. `campaign_row` adds `module` (Open question 4).
- `overview.world_rows()` and `overview.campaign_cards()` compose the rows with
  the live counts, cover tokens and activity.
- `GET /worlds` is byte-identical to today's. `GET /campaigns` is identical
  except for `module` and the `last_scene` tie-break.
- **Guarantee:** cache on (cold or warm), cache off and after a restart all
  give the same payload.
- **Failure:** the cache degrades to the live compute (03 section 12). A row
  that cannot be read raises exactly as it does today.

**04-C1b. Scene and continuity projections.**

- `overview.scene_head`, `overview.scene_summary` (collection digest over
  `scenes/*.md` with `safe_id` stems), `overview.scene_turns` (scene bytes plus
  sorted `players`) and `overview.continuity_summary` (five absent-ok files),
  with the outputs in sections 3.2 to 3.5.
- **Guarantees:**
  - each compute reads nothing it was not handed, which an audit-hook test
    proves;
  - each compute reads no clock;
  - content-determined failures are part of the value, and I/O failures are
    never stored;
  - only the four overview read routes reach these kinds (3.7).

**04-C1c. Per-scope Todo and shell composition.**

- `live(cid)`'s payload shape is unchanged.
- A campaign scope is `CAMPAIGN_BUILDERS` over a `_Ctx` whose
  history-proportional reads go through 04-C1b. The library scope is
  `LIBRARY_BUILDERS`, unchanged. The global page concatenates the library
  scope with every campaign scope. The ignore set is applied last.
- The shell's campaign block takes its rows, scenes, open turns and
  `ledger_open` from 04-C1a/b (4.3).
- **Guarantees:**
  - every chore keeps its current answer for the same files;
  - a change confined to campaign X misses only X's projection keys;
  - no projection key includes the ignore set, config, routing or the usage
    ledger, and no projection reads them;
  - the chore classification table (4.4) is the registry of what is projected
    and what is live, and a new chore must take a row in it.

**04-C2a. Server-side write handling.**

- No server-side retirement step exists or is needed (03-C2).
- `overview.warm_scene(cid, sid)` stores `scene_head` and `scene_turns` for a
  scene's current on-disk bytes. It is called from the turn's follow-up
  (`_fire_follow_up`) on a worker thread, holding no lock or exclusion key,
  storing artifacts only and never a `sources` row. It never raises.
- `overview.warm_paths(paths)` is exposed for 05-C1, which owns every other
  call site.
- **Guarantee:** a warmed artifact is keyed on bytes read back from disk.
- **Failure:** a failed warm is a later miss, and nothing else.

**04-C2b. Client first paint and retirement.**

- `listWorlds`, `listCampaigns` and `getTodo` answers are remembered, root-
  and key-scoped, in memory only.
- `WorldsView`, `CampaignsView` and `TodoView` paint a remembered answer on
  their first render, with `aria-busy`, and always revalidate.
- Every client write forgets every remembered read, both before it is sent and
  when it settles. An observed run ending forgets. A root change forgets.
- Post-mutation refreshes pass `fresh`.
- Empty states wait for a settled read. A failed revalidation drops the
  remembered answer.
- **Guarantee:** the bound in section 6.5.

**04-C3a. Instrumentation.**

- `store/readstats.py` is a leaf, contextvar-scoped counter set with the
  counter names in section 8.
- Each overview request writes one Debug `overview_read` log row holding
  counts and milliseconds only.
- **Failure:** instrumentation never raises and never changes an answer.

**04-C3b. Synthetic library and harness.**

- `synth_library.py` uses store writers only, placeholder names, generated
  prose and images, a seeded clock, and the `.synthetic-library` marker.
- `bench_overview.py` refuses anything but a marked synthetic library away from
  the default store, runs the scenarios in 9.2, and prints counters and timings
  only.
- Counter assertions on the `small` profile run inside `make check-py`.

**Changes from the checklist:**

- **04-C1** is split into C1a, C1b and C1c. The draft's single per-scope Todo
  artifact becomes a composition, for the reason in 4.4.
- **04-C2** is split into C2a and C2b. On the server, "synchronous
  retirement" is inherited from 03-C2. The mechanism lives in the client.
- **04-C3** is split into C3a and C3b.

## 11. Interaction with repo rules

- **Module placement and the import guard.**
  - `store/overview/` is a package: `kinds.py` (registry entries and byte-fed
    computes), `read.py` (the entry points in 3.7) and `warm.py`.
  - It imports `scenes`, `campaigns`, `worlds`, `continuity`, `frontmatter`,
    `statcache` and 03's module by submodule binding (`CLAUDE.md`, imports).
    Routes import `store.overview`.
  - Nothing in `store/` imports `overview`, so the graph stays acyclic.
  - `readstats` is a leaf.
- **Lock domain.** `overview` has no `cid`-taking mutator; its only writes are
  03's artifact rows. So it gets **no** `store/locks.py` entry (03 section 13;
  `test_lock_domain_guard.py` rejects a phantom `OUTSIDE_DOMAIN` entry).
  `warm_scene` takes no lock.
- **Atomic guard and paths guard.** No record is written. Paths come from the
  existing resolvers (`campaigns_paths.campaign_root`,
  `scenes.paths._scenes_dir`, `worlds.paths.world_root`). The generator writes
  only through store writers. The harness writes only its own JSON output,
  outside the store.
- **03's query-safety guard** (03 section 9). Every overview read starts from a
  live listing or a live path, so nothing enumerates the cache.
- **03's decision-site guard** (03 section 5, rule 5) is given the overview
  kinds and the route allowlist in 3.7. `closed_by`, `list_campaigns` and
  `read_scene` stay off it.
- **Observability.** One writer: `logs.record`. No second handler, no new log
  file, nothing at a level that could reach the error store.
- **Detached runs.** `warm_scene` rides `_fire_follow_up`. It is not a run,
  holds no exclusion key, and cannot refuse a turn or freeze a scene.
  `PUT /config/data-dir` is unaffected: 03 closes its handles on a root change,
  and the client forgets on root change.
- **Revision tokens** (`store/revision.py`) are unaffected. No overview read
  writes a campaign. The shell's rollup write is `usage_rollup`'s and is
  unchanged.
- **Costs rule.** Every `null` the routes report today stays `null`: a count
  nobody could take is never rendered as `0` (`shell.py:9-14`). The money
  fields are untouched.
- **Android.**
  - The read paths must work with the cache off. 03's first plan task confirms
    `sqlite3` and the `BUILD` stamp in the Chaquopy build. Until then 04's kinds
    compute live, and the server gains only 4.5.
  - The client memo is memory-only, which suits a WebView.
  - No base dependency is added.
- **pydantic.** No models are added. Routes return dicts, as today.
- **Privacy.**
  - Derived text (titles, blurbs, names) lives in 03's per-device file inside
    the store root, under 03's purge-on-delete rule.
  - The client memo lives in the tab's heap and is never written to
    `localStorage`.
  - The generator and harness are synthetic-only by construction (section 9).
  - No count, size or proportion from any real library appears in this spec,
    its tests or its reference files.
- **Frontend rules.**
  - No keyboard binding is added.
  - The `?` sheet is unaffected.
  - The page-shell and list/detail patterns are unaffected, because these
    pages only change where their first state comes from.

## 12. Tests and acceptance

### 12.1 Equivalence (backend, pytest, `tmp_path` stores with placeholder names)

- For each of `GET /worlds`, `GET /campaigns`, `GET /todo` (global and
  `?campaign=`) and `GET /api/shell?campaign=`, the payload is byte-identical
  in four states: cache on and empty, cache on and warm, cache off
  (`GRIMOIRE_COMPILED_CACHE=0`), and after a simulated restart (app rebuilt,
  statcache pools cleared, compiled file reopened). Fixtures include:
  - CRLF and non-UTF-8 scene files;
  - two campaigns with byte-identical `campaign.md` in different directories;
  - a scene with no `title`;
  - a campaign with no ledgers at all;
  - a garbled `commitments.json`.
- Against the pre-04 implementation, kept as a test-only oracle for the
  landing PR: identical, except `module` and the tie-break, each covered by its
  own test.
- **Text parser.** `parse_head_text(bytes->text)` equals `parse_frontmatter_head(path)`
  on the frontmatter corpus `test_frontmatter*.py` already uses.

### 12.2 Change detection (each change is visible on the next read, in-process and after a restart)

- Per projected input:
  - a scene appended, renamed, deleted or added;
  - a review sidecar added, which moves `unreviewed` but not
    `scene_summary`'s key, because the member filter excludes it;
  - each of the five continuity files edited, created or deleted;
  - `campaign.md` and `world.md` renamed in place;
  - a rename-replace;
  - a same-size rewrite with the mtime restored (caught by ctime on POSIX, as
    in 03 section 14).
- A hand edit through `os` rather than a store writer is seen on the next read.
- The player cast changes, so `scene_turns` misses on `players` and the shell's
  turn count follows.

### 12.3 Counters (deterministic assertions on the `small` synthetic profile)

- **`warm_after_restart`:**
  - `GET /campaigns` records `frontmatter.parse_head == 0`;
  - `GET /campaigns` records `frontmatter.parse == 0` for `campaign.md`;
  - `GET /api/shell` records `transcript.parse == 0` for unchanged open
    scenes;
  - `GET /todo` records no ledger parse.
- **`warm_same_process`:** `GET /campaigns` lists no `scenes/` directory. This
  is asserted through the audit hook: no `os.scandir`/`os.listdir` on any
  `scenes/`.
- **`relevant_change`** (one post appended in campaign X):
  - `compiled.miss.overview.scene_summary == 1` and
    `compiled.miss.overview.scene_head == 1` on `GET /campaigns`;
  - `GET /api/shell` after the turn's `warm_scene` records
    `compiled.hit.overview.scene_turns` for that scene and
    `transcript.parse == 0`.
- **`unrelated_change`:** no overview kind misses on `GET /campaigns` or the
  campaign scopes of `GET /todo`.
- **Shell reuse:** the shell's `ledger_open` and Todo's `owed` in the same
  process share one `continuity_summary` computation (`overview.computed`
  counts 1).
- **Live-path fixes:**
  - a shell read for a campaign inheriting its module performs no
    `os.scandir` of the world's record kind directories;
  - two consecutive global Todo reads parse each `character.md` at most once
    across both.

### 12.4 Guards and failure

- The audit-hook test for every byte-fed compute: no `open`, no listing.
- A clock trap for the `continuity_summary` compute.
- 03's decision-site guard fails a fixture that calls `overview.scene_summary`
  from `closed_by`.
- Degradation: with the compiled database locked, corrupt or absent, every
  endpoint answers identically and logs once (03 section 12).
- `warm_scene` swallows a raising compute, a missing scene and a cache that is
  off, and the turn's outcome is unchanged (`test_streaming*` gains a case).

### 12.5 Frontend (vitest, from `frontend/`)

- `api/remembered.test.ts` gains:
  - `listWorlds`, `listCampaigns` and `getTodo` are remembered per root and
    key;
  - a write to `/api/config` forgets them, which the old scoped rule did not;
  - a GET issued before a write and settling after it is not stored;
  - `noteRunSettled()` forgets;
  - a failed read drops the entry.
- `WorldsView.test.tsx`, `CampaignsView.test.tsx` and `TodoView.test.tsx` gain:
  - a remembered payload renders on the first render, with `aria-busy`,
    before the mocked read resolves;
  - the fresh answer replaces it and clears `aria-busy`;
  - no empty-state text is rendered before the first settle when nothing is
    remembered;
  - a failed revalidation removes the stale rows and shows the existing failure
    state;
  - post-mutation refreshes are called with `fresh`;
  - TodoView never renders blank between visits of the same `cid`.
- **Settling** follows `src/test-setup.ts`'s rule that an `await` means the
  page has settled. A test asserting the stale frame reads it synchronously
  after `render`, before any `await`.

### 12.6 Frozen campaign

The sweep (`backend/tests/fixtures/frozen_campaign/sweep.py:129-154`) keeps
calling `list_worlds` and `list_campaigns`. 04 does not change them, so those
entries do not move. The sweep **gains** entries for `overview.world_rows()`,
`overview.campaign_cards()`, `overview.scene_summary` and
`overview.continuity_summary` over the frozen home. `snapshot.json` is
regenerated deliberately in the landing PR, with the new entries reviewed. The
new entries hold the projections to files an older version wrote. The
fixture's `home/` is never regenerated, and the sweep runs on a copy, so the
compiled cache never writes into it (03 section 14).

### 12.7 Acceptance (the draft's section 10, mapped)

| Draft criterion | Held by |
|---|---|
| Warm Worlds/Campaigns/Todo have no perceptible blank wait | 12.5 (first render from memory; no premature empty state) |
| Warm-after-restart close to warm same-process | 12.3 `warm_after_restart` counters; harness timings reported |
| Editing one campaign does not reparse every campaign | 12.3 `relevant_change`, `one_campaign_in_large` |
| Deleting `.cache` gives identical user-visible state | 12.1 (cache cold, off, after restart) |
| Shell reuses shared projections | 12.3 shell reuse |
| Todo remains derived and self-healing | 12.2; 4.4 classification; amended docstrings |
| External edits visible per a documented bound | 12.2 hand edits; 6.5; `docs/store-guarantees.md` |

## 13. Non-goals

- Caching member counts, listings or existence. 03 section 7 forbids it, and
  this spec keeps them live.
- A materialised Todo artifact per scope (4.4).
- Persisting `world-describe`, `world-subjects`, `avatars`, `world-avatars` or
  `sheets` (Open question 1).
- Persisting anything in the browser (Open question 2).
- The epoch, `ETag` or `304` (6.6).
- WorldView's per-section count requests and the campaign hub's other reads.
  They are already remembered or held by the shell payload, and stay as they
  are.
- An eager rebuild after writes other than a turn's scene write (05).
- Any change to `usage_rollup`, the costs surfaces or the money rule.
- A Settings control for any of this. The kill switch is 03's env var and is
  not a setting.

## 14. Open questions

1. **The image-backlog chores and the shell's `images_undescribed`.** These
   are now the largest live cost on the shell and the global Todo page. They
   walk every version folder of a world, and their inputs include image-store
   objects named only inside placements, which a key cannot name in advance.
   *Recommendation:* no persistence in 04. As a follow-up slice, gated on
   section 9's numbers, add an **in-process** `memo_stamped` over each world's
   backlog walk, with the walk reporting the stamps it read, on the
   `characters._build_row` pattern. Persisting it would need 03 to support
   dependent inputs (a key phase that reads placements to name objects). That
   is a missing edge to raise only if the in-process memo proves insufficient.
2. **Keep the remembered payloads across a page reload** (`localStorage` or
   IndexedDB)? *Recommendation:* no. It would put derived private content
   (names, blurbs, chore labels) outside the store root the user chose, which
   is 03 section 3's argument, and the WebView keeps such storage
   indefinitely. With 04's server projections, a reloaded page's first read
   is cheap.
3. **Tell other tabs about writes** (a `storage` event on each write settle,
   like `ROOT_MOVED_KEY`)? *Recommendation:* not in v1. The bound without it
   is one revalidated frame (6.5). Add it if multi-tab use turns out to be
   common.
4. **What `module` carries on the campaign card.** The options:
   - (a) the campaign's raw setting;
   - (b) the raw setting resolved over the world row's raw default, with no
     pack validation;
   - (c) `binding.resolve`'s effective module, which needs the pack;
   - (d) drop the chip.

   *Recommendation:* (b). It is computed from the two persisted rows, needs
   no pack load per card, and shows what the user configured. A module whose
   pack fails to load is reported on the campaign's own pages. `world_row`
   gains a `module` field for this (raw), and `GET /worlds` gains the field as
   well. `WorldMeta.module` is already typed for it (`api/types.ts:746-760`).
5. **Mark the read-path spec's layer 3 as superseded?** *Recommendation:* yes,
   for these four routes, with a pointer to 6.6. Leave the idea of a
   projection-key validator for a later spec, if 04's measurements show that
   revalidation transfer matters.
6. **The `last_scene` tie-break** (3.4). *Recommendation:* `updated`
   descending, then `sid` ascending. That is deterministic across devices.
   Leave `list_scenes` untouched, since the scenes list and decision sites
   use it.
7. **Who builds the synthetic generator: 03's plan or 04's?** 03 section 14
   needs one for its own acceptance and names no contract for it.
   *Recommendation:* 03's plan builds the generator core to section 9.1 here,
   because it lands first. 04 adds the overview scenarios and the harness. A
   checklist note (or a new 03 contract item) records the hand-off. **Missing
   edge.**
8. **Collection member filters in 03-C1.** `scene_summary` needs a collection
   defined as `(directory, filter)`, so that review sidecars in `scenes/` do
   not move its key. *Recommendation:* 03's plan adds it to the collection API,
   with the filter declared on the kind and covered by its `version`. Without
   it, 04 keys over the whole directory and accepts a miss per review write.
   That is correct but colder. **Missing edge.**
9. **Hit and miss reporting from 03's `derive`.** 04-C3a needs 03's module to
   report hit, miss, store and bypass per kind, plus hashed bytes and digests,
   through `readstats.bump`. *Recommendation:* 03's plan adds the `bump` calls,
   since `readstats` is a leaf with no dependencies. **Missing edge.**
10. **A CI timing budget for the overview harness**, like `perf_budget.json`?
    *Recommendation:* not in 04. The counter assertions are deterministic and
    are the gate. Revisit once the harness has run on CI enough times to give
    a stable median.
11. **Persist `scene_head` at all?** 03 section 8 lists it as "wire last". It
    is needed here as the per-member artifact behind `scene_summary`'s miss
    path. *Recommendation:* wire it as part of `scene_summary`, and keep it
    only if `one_campaign_in_large` shows the batched member lookup beating a
    re-parse of every head. That is 03's cost test, applied to this kind.
