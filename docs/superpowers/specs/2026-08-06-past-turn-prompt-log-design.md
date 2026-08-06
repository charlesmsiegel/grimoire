# "What did the model see" for a past turn

Issue #157, first half. The second half ("what changed") is untouched here —
`changes.py`'s latest-only upsert is a separate gap and a separate schema
change.

## Problem

`GET /campaigns/{cid}/scenes/{sid}/context` composes the prompt *now*:
`context_breakdown` runs `_assemble` + `_packed` against the store as it
currently stands, and `SceneInspector` renders the result. That answers "what
would the model see if I sent a turn this second", which is not the question
anyone debugging a bad reply is asking. By the time the reply looks wrong, the
chronicle has another entry, a character's state has moved, plot threads have
advanced, and the world-info activation has changed with them.

The issue proposes waiting for #150's audit trail and reading its frozen
messages. #150 is not built (`store/audit/` is the *mechanics* narrated-event
validator, unrelated), so this builds the one slice #157 needs and leaves the
cost/usage/latency ledger to #150.

### Recomputation cannot substitute for capture

Worth stating plainly, because it is what rules out the cheap version of this
feature. Even against a *frozen* store, re-running the composition does not
reproduce a past prompt: `macros.expand_macros` expands `{{random:a,b}}` and
`{{roll:1d20}}` at render time (`macros._expand_random`, `_expand_rolls`), so
two passes over identical data produce different text. A prompt that was sent
is only knowable by having recorded it.

## The key observation

`context.assemble` already holds the invariant this feature needs, one step
short of holding it across time. Its docstring:

> One list, one render — so `context_sections` (the inspector's breakdown) and
> `build_messages` (the prompt) cannot disagree about what was sent.

That is true *within* a request and false *between* requests, because
`build_messages` and `context_breakdown` are two separate entry points that
each run their own `_assemble` + `_packed` pass. Capturing a snapshot from a
second pass would reintroduce exactly the disagreement `_SECTIONS` was
restructured to remove.

So the snapshot must come from **the same pass that produced the messages that
were sent**. That is the whole design; everything below follows from it.

## Shape

### One composer, two outputs

Extract the row-building half of `context_breakdown` into `_breakdown(a, p)`,
which takes an already-computed assemble/pack pair. Then:

- `compose_turn(cid, sid, turn, appended) -> (messages, breakdown)`
- `compose_director_turn(cid, sid, note, turn, appended) -> (messages, breakdown)`
- `compose_opener(cid, sid, prompt) -> (messages, breakdown)`
- `context_breakdown(cid, sid)` keeps its signature — the live inspector view.
- `build_messages` / `build_director_messages` / `build_opener_messages` stay,
  as one-line wrappers returning `[0]`. Callers that do not record a snapshot
  (nothing today, but the seam should not force the choice) keep working.

### `appended` folds the reserve/append dance into one place

Three call sites today render a mandatory trailing block, pass it to
`build_messages` as `reserve=` so the packer charges for it, and then append it
to the returned list themselves:

- `routes/scenes.py:post_regenerate` — the regenerate-guidance block
- `routes/mechanics.py:_continuation_messages` — the roll-result block
- `routes/mechanics.py:_declined_continuation_messages` — the declined block

Reserving and appending are two statements that must agree; the packer's own
docstring explains what goes wrong when they don't ("budget the request
overspends silently"). They must *also* agree with the snapshot, or a
regenerate's frozen breakdown omits the guidance the model actually read —
which would make this feature lie on precisely the turns people re-run because
something looked wrong.

So `compose_turn` takes `appended: tuple[tuple[str, str, str], ...]` of
`(label, role, content)` and owns all three consequences: it reserves the
content against the budget, appends the message, and emits a breakdown row.
One decision, three effects, no way to drift.

### Storage: an index plus one payload per entry

    <campaign>/prompts/index.json      {"next": <int>, "entries": [...]}
    <campaign>/prompts/<id>.json       the frozen breakdown

`index.json` carries the list view — `{id, scene, ts, task, model,
total_tokens, dropped_tokens, budget_tokens}` — small enough to read whole and
rewrite per turn. The payload carries the same fields **except `scene`**, plus
`sections` (every section's full text, dropped ones included, exactly as
`context_breakdown` returns them).

Keying entries by a field (`scene`) rather than by directory name is
deliberate: it puts this store in the nine that `scene_refs.repoint` fans out
to, rather than in `alternates`' awkward position as "the one store keyed by
filename", which needs a directory move on every rename.

**The index alone owns `scene`.** The index is what `repoint_scenes` rewrites,
so a second copy in the payload would go stale on the first scene rename — and
a reader that trusted it would then refuse the very entry it had just listed.
(Implementation found this the hard way: the detail route checked the payload's
copy, and the rename test failed on it.) Repointing the payloads instead was
rejected — it rewrites up to `depth` multi-hundred-KB files per rename to keep
a field nothing reads.

`next` is a monotonic counter, never recycled, so a pruned id is never reissued
to a different turn. Ids are therefore **numeric**, and `_read_index` drops any
row whose id is not — `next` is derived from them, and an `int()` raising there
would escape `record`'s guard and fail the turn that captured it, which is
exactly what this module promises not to do.

### Retention is a per-campaign rolling window

`prompt_log_depth` in config.md, default `50`, `0` = record nothing.

Per **campaign**, not per scene. A payload is one whole prompt: ~30 KB for an
8k-token turn, ~400 KB for a 100k-token one, so 50 of them is roughly 1.5–20 MB
per campaign. Per-scene retention reads better but multiplies that by the scene
count — a campaign with 100 scenes at 20 entries each is 2000 payloads, in a
store whose whole premise is that a human can read it. A campaign-wide window
is bounded and predictable, and the threat model this feature serves — drift
*within* a scene you are actively playing — is covered by it. The cost, stated
rather than hidden: playing scene B long enough evicts scene A's snapshots.
This is a rolling debug window, not an archive; #150 is where a durable ledger
belongs.

### Frozen numbers, not recomputed ones

The payload stores the token counts computed at capture time, not just the
text. Recomputing at read time would re-derive them through whatever
`count_tokens` does *then* — a different tiktoken version, or the
characters/4 heuristic on Android, where the same stored text yields different
numbers than the desktop that captured it. A snapshot whose figures move is not
a snapshot.

## Endpoints

The issue sketched `GET /campaigns/{cid}/audit/{entry_id}`. Shipped instead:

- `GET /campaigns/{cid}/scenes/{sid}/prompts` — the rows, newest first.
- `GET /campaigns/{cid}/scenes/{sid}/prompts/{eid}` — one frozen breakdown, in
  the exact shape `GET .../context` returns.

Scene-scoped rather than campaign-scoped because the consumer is per-scene and
because the nesting is load-bearing: ids are campaign-wide, so the detail route
checks the entry against the scene in the path (via `read_entry(scene=…)`)
rather than trusting it. Without that, any scene's URL would serve any entry
and the inspector would render one scene's prompt under another's heading.

## Boundaries

- **Never fails a turn.** `record` swallows `OSError` and `StoreBusy`, and
  `_read_index` drops rows it cannot parse rather than raising through them. A
  debug view that can take down generation is a worse bug than the one it
  diagnoses.
- Recording happens at build time, in the route, before the stream. The stream
  finalizers (`streaming.py`) have delicate ownership/abort semantics and are
  not touched. The consequence, and it is the right one: a turn that failed
  upstream still leaves a snapshot, because "what did the model see when it
  failed" is a question this feature should answer.
- `compose_*` stay pure builders. Writing is the route's call.

## Lock domain

`store.prompt_log` mutates campaign-scoped state (`index.json` is
read-modify-written by every capture and by pruning), so it joins
`DOMAIN_MODULES` in `store/locks.py` and takes `campaign_lock(cid)`. It is a
new module, so it starts inside the exclusion rather than on the `UNREVIEWED`
backlog — the same call `store.commitments` made.

## Out of scope

- Cost, usage and latency capture (#150), and the `openrouter.py` changes they
  need.
- Replay (#151).
- `changes.py`'s latest-only upsert (#157's second half).
