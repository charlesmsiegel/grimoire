# 01b. Decision capture at every decide site

**Status:** Draft — spec gate (substitute review) folded in; Codex gate pending.
**Date:** 2026-10-09
**Roadmap:** 01b in `ROADMAP-CHECKLIST.md`. Lane: Decision (01a, 01b → 01c,
01d → 02).
**Baseline:** `main` at `35c1fb7`.
**Reconciles:** the bundle draft `01-inference-backend-refactor.md` §18
("Capture should generalize from prompt capture to inference capture"). It
also supersedes one ruling of the landed 01 spec
(`2026-10-07-inference-backend-refactor-design.md` §9.4): "Scene-break and
voice drift never had a capture and gain none, and neither do slice G's two
continuity decisions". That ruling's three reasons are answered in §3 below.
The draft's §18 also asked for embed capture: input count and bytes, space id,
hits and misses, and dimensions. That is already met by
`store/inference/embed._capture`'s Debug line (`store/inference/embed.py:199-223`),
so it is not taken here. It is not a dropped part.

> Implementation rule: re-read the code and every PR landed on this subsystem
> since the baseline before planning.

## Depends on

| Contract | Provided by | What this spec uses it for | Hard or soft |
|---|---|---|---|
| (no ID) `inference.Capture` and `_captured`, `decisions.outcome`, `llm.ATTEMPTED` | 01 (landed, slice H) | the per-call hook and the outcome record | Hard |
| (no ID) `store.prompt_log`, `routes.common._record_prompt` | #157 (landed) | the store a capture is filed to | Hard |
| `_Call.stage` / `_Call.positions` | shared structure of 01a and 01b (`ROADMAP-CHECKLIST.md`, "Shared structures"; 01a §5) | the stage and batch items each call carried | Soft (whichever lands first adds them) |

## Required by

Rebuilt from the "Dependency edges" list in `ROADMAP-CHECKLIST.md`.

| Contract (provided here) | Consumer | Edge | What the consumer uses it for |
|---|---|---|---|
| 01b-C1 | 01c | S | a sampled decision's distribution is captured per call. The draw (`u`, `selected`, `mass`) is filed after `decide()` returns through `Scope.note`. The replay record (01c-C3) is persisted by the caller, and the capture only shows it |
| 01b-C1 | 01d | S | an escalation call is captured in the same scope's entry as the chain's calls. Its call record carries 01d's `hop: escalation` beside `stage` and `at` |
| 01b-C1 | 01e | S | `Rank`, multi-select and joint-choice outcomes captured with no new capture code (the outcome is `decisions.outcome`) |
| 01b-C1 | 01g | S | a Decision-as-tool call inside a loop is captured through the same helper: its handler passes its caller's `capture` through, which the §6 guard allows by name |
| 01b-C1, 01b-C2 | 02 | H for 02-C2b, C3, C4 and C5 | every decide site 02 adds or changes uses the helper, and the AST test in §6 holds that |
| 01b-C1 | 10 | S | the retrieval-relevance decision a query plan's repair hop may ask is captured |
| 01b-C1 | 11 | S | the epistemic classification decision (11-C1's Decision stage) is captured |
| 01b-C1 | 13 | S | a Decision-chosen NPC action's distribution is captured, and its legal-set size and digest are filed through `Scope.note`. The full legal set survives only in the messages of calls kept in full |

01f, 01h, 09 and 12 cite no 01b contract. A new decide site those specs add
is still held to the helper by the §6 AST test.

## 1. Current state (reconciled against main)

- **The hook.** `inference.Capture` is `(messages, outcome, target)`
  (`backend/src/grimoire/inference.py:104-116`). `_captured` (`:333-346`) runs
  it after each call settles, outside the meter, guarded so a raising capture
  costs only itself. A structured chunk is captured once, its schema-refusal
  re-send included (`:381-394`). A native item is captured once, its request
  body built in a worker thread and only when a capture is given (`:510-516`,
  `_native_request` `:543-550`). A cancelled call is never captured.
- **The outcome.** `decisions.outcome(mode, provider, model, results, error)`
  (`decisions.py:991-1011`) records the mode, what served the call, and per
  item the backend, every answer with its `reason`, `detail`, `probability`
  and `distribution`, and the rationale. It records **no stage index** and **no
  batch positions**: `_Call` (`inference.py:185-199`) carries neither. A
  native item's outcome cannot say which item of the batch it was.
- **Only the speaker pick is captured.** `character_turns._select` passes
  `capture=lambda msgs, outcome, conn: run_in_threadpool(_capture, ...)`
  (`routes/character_turns.py:752-756`). `_capture` (`:625-694`) builds plain
  message sections and appends the outcome as a zero-token section whose id is
  `OUTCOME_SECTION_ID = "decision"` (`:622`), then files through
  `routes.common._record_prompt` (`routes/common.py:406-534`). That function
  checks the scene still exists under a non-blocking campaign lock, writes,
  and bumps the campaign's write token inside the same hold
  (`:481-530`). **Each settled call is its own prompt-log entry.**
- **Four decide sites capture nothing:**
  - scene-break, `routes/scenes.py:4217-4219` (`_break_ask`, also run as a
    landed turn's follow-up, #397);
  - voice drift, `routes/scenes.py:2673-2678` (absorb review, one call per
    anchored NPC);
  - continuity identity, `routes/scenes.py:2136-2141` (absorb review, chunked);
  - continuity reconcile, `routes/continuity.py:685-690` (a campaign-scoped
    `background` run, chunked, **with no scene**).
- **The prompt log** (`store/prompt_log.py`) is per campaign: one
  `prompts/index.json` whose rows carry a `scene` field, and one payload per
  entry. Retention is `prompt_log_depth` entries **per campaign**
  (`depth()`, `record` at `:229-296`), and 0 turns capture off
  (`capturing()`). `list_entries(cid, sid)` filters by `scene == sid`. The
  routes are scene-scoped only (`routes/scenes.py:5432-5527`), and `against=live`
  diffs an entry against the live chat composition.
- **The 01 ruling's reasons:**
  1. per-check entries would compete in the Turn-history rail with the turns
     a reader inspects;
  2. they would also compete for the same per-campaign retention (implied by
     "compete");
  3. `taskLabel` has no label for them. It has none for the speaker's
     `response-selector` either (`frontend/src/components/turnLabels.ts:12-16`,
     `api/types.ts:1929-1934`), which falls back to the raw task name.
- **The frontend.** Turn history lists every row
  (`components/SceneInspector.tsx:1818-1834`). The compare picker offers "The
  live preview" for any entry (`:1761`), which for a decision entry diffs a
  decide prompt against a chat prompt. `ContextBreakdown` draws the outcome
  section as "outcome · not sent" (`components/ContextBreakdown.tsx:10`,
  `:61-71`). Settings' context bar reads `entries[0]` of the newest scene
  (`routes/ConfigView.tsx:307-322`), which would be a decision once scene-break
  checks capture.
- **Pinned by tests.** `test_decide_chain_golden.py:197-230` records every
  capture's outcome through a real `LLMClient`, and is "never regenerated to
  make a change pass". `test_character_turns.py:621-700` pins the selector's
  capture shape, and `:662` pins `OUTCOME_SECTION_ID == "decision"`.
- **Logs stay counts-only.** The identity log row (`_identity_outcome`,
  `routes/scenes.py:2167-2178`) and the reconcile row (`_log_pass`,
  `routes/continuity.py:721-737`) carry "counts and modes only -- never a
  title, a beat, a reason or a prompt".

## 2. Goal (and what is explicitly not the goal)

Every `decide()` call site files one prompt-log capture per decision it makes.
The capture holds the request as sent, the normalised outcome with any
distribution, the backend, and the stage and route that answered. A turn's
captures stay findable and are not evicted by decisions. Nothing in this
changes what a decision answers, how long its calls may take, or whether it
fails.

**Not the goal:**

- capturing the generations around a decision (the scene-break title draft,
  the absorb extraction), which are `generate` calls;
- a durable decision ledger (#150 remains the place for one);
- capturing eval runs (01a);
- logging any of this to `logs/`.

## 3. Design

### 3.1 One entry per decision scope, not per call

A **capture scope** is what a site wraps: one speaker pick, one scene-break
check, one voice-drift phase (every anchored NPC of one review), one identity
check, one reconcile pass. Inside a scope, the per-call hook appends `(part,
messages, outcome, target)` to an in-memory list and does no I/O. Building a
native body is the exception: `_captured` already does that in a worker
thread. When the scope exits, it files **one** prompt-log entry.

Why one entry per scope:

- A reconcile pass is one chunked `decide()` and would otherwise be one entry
  per chunk. A voice-drift phase would be one per NPC.
- The entry tells the whole decision's story, including the stage that
  failed before the fallback answered. Today that is two unrelated entries.
- I/O happens once, after the decision is in hand (01b-C2).

### 3.2 What a call record holds

`decisions.outcome` gains two keys, stamped by `inference._outcome` from
`_Call`:

- `stage`: the call's index into the chain `run_stages` was handed
  (`inference.stages`: 0 is the selection's backend, 1 the role fallback when
  it is a stage of its own);
- `at`: the batch indices the call carried (`_Call.positions` mapped
  through the chunk or item). Not `items`, which the outcome already uses for
  its per-item results (`decisions.py:1002-1011`).

`_Call` gains `stage: int = 0` and `positions: tuple[int, ...] = ()`, set
where `run_stages` builds each stage's call (`inference.py:653-654`). These
are the same two fields 01a §5 defines, added by whichever plan lands first.
The answering route needs no new key: `provider` and `model` already name
the attempt that answered (`llm.ATTEMPTED`, `inference.py:250-256`), so a
facade fallback or a prompt-only re-send already names itself.

**This moves the decide-chain golden's captures.** Every outcome there gains
`stage` and `at`, and nothing else changes. That is the change this spec
argues for. The golden is re-recorded in the same PR with this section cited,
and the PR description says so.

### 3.3 The helper

New module `backend/src/grimoire/routes/decision_capture.py`. It imports
`routes.common`, and `character_turns` imports it, so the import graph stays
acyclic. `OUTCOME_SECTION_ID` moves here, and `character_turns` keeps
`OUTCOME_SECTION_ID = decision_capture.OUTCOME_SECTION_ID` so the existing pin
holds.

```python
#: prompt_log's scene key for a campaign-level capture (the reconcile sweep).
#: New: 01b adds `prompt_log.NO_SCENE = ""` (today only `usage.NO_SCENE`
#: exists, `store/usage.py:1445`).
NO_SCENE = prompt_log.NO_SCENE          # ""

class Scope:
    def hook(self, part: str = "") -> inference.Capture | None: ...
    def note(self, part: str, key: str, value: JSONable) -> None: ...

@contextlib.asynccontextmanager
async def capturing(cid: str, sid: str, task: str, *,
                    fence: Callable[[], bool] | None = None,
                    abandoned: Callable[[], Awaitable[bool]] | None = None,
                    ) -> AsyncIterator[Scope]:
    ...
```

`Scope.note` records what a caller decides **after** `decide()` returns, which
no per-call hook can see:

- 01c files its draw (`u`, `selected`, `mass`; the replay record itself is
  still persisted by the caller, 01c-C3);
- 13 files its legal-set size and digest.

Notes go into the outcome section's `notes` (`{part: {key: value}}`) and
count against `MAX_OUTCOME_CHARS`. Like the hook, a note does no I/O.

Usage at a site:

```python
async with decision_capture.capturing(cid, sid, "scene-break") as scope:
    decision = await operations.decide(..., capture=scope.hook())
```

Rules:

- **Off when capture is off.** If `prompt_log.capturing()` is False (read
  once, off the loop, on entry), `hook()` returns `None`. `decide` then builds
  no native request bodies, and nothing is filed.
- **`part`** is an opaque id naming a sub-decision within the scope. Voice
  drift passes the anchored NPC's record id (`aid`), never a display name. It
  is `""` elsewhere.
- **Filed on exit** in a worker thread. It is filed on normal exit, and on an
  exception that is an `LLMError` or a `decisions.DecideRequestError`: a
  failed decision is one of the most worth having, and `BudgetRefused` is an
  `LLMError`.
  - A `DecideRequestError` is raised before any call, so the hook never ran.
    Its entry has no message sections, `calls: []`, and the envelope's
    `"error": "invalid_request: <the exception's sentence>"`. That sentence is
    our own text, not a provider's.
  - It is **not filed** on cancellation, on `Abandoned`, or on any other
    exception crossing the scope (the person closed the review, or the run is
    going away).
  - A site that swallows `Abandoned` inside its own loop passes the review's
    own `abandoned` check, and the scope skips filing when it answers True.
    Voice drift catches every `Exception` per NPC (`routes/scenes.py:2696-2701`),
    so its phase would otherwise exit normally after a close.
  - The exception is re-raised unchanged in every case.
- **Fenced.** The scope reads its identity on entry:
  - **scene level:** `store.scenes.scene_identity_strict(cid, sid)`, off the
    loop. If it reads `None` (a scene that predates identities, or a missing
    one) or raises, `hook()` returns `None` and the scope captures nothing.
    `scene_identity` is not used, because `None == None` would match a
    recycled scene (`store/scenes/identity.py:200-212`). `ensure_identity` is
    not used either: a debug capture must not be the thing that writes a
    scene file. At filing, the strict read is repeated inside the hold, and
    the capture is skipped if it is `None`, raises, or differs.
  - **campaign level:** nothing to read; the site passes its own `fence`.

  The filing checks it **inside** the non-blocking campaign-lock hold that
  covers the write, and drops the capture when it no longer holds. This is
  the scene-level `_record_prompt` rule (check and append in one critical
  section, `routes/common.py:464-470`), tightened from "the scene id still
  exists" to "the same scene". A recycled id (`delete_scene` frees ids) can
  therefore never inherit a decision. Reconcile passes its own `stillborn`
  (`routes/continuity.py:755-756`, `run.cancel_requested or run.forgotten`)
  as `fence=lambda: not stillborn()`, so a run forgotten by a campaign delete
  (`runs.forget_subject`) cannot file into a same-named replacement.

  **Residual.** Between the store delete and `forget_subject`, a window
  documented as partial at `routes/runs.py:923-941`,
  `_record_campaign_prompt`'s `read_campaign` check is all that covers it.
  That check fails only while no same-named campaign has been recreated. A
  recreate inside that window is the one path left to a misfiled
  campaign-level capture. It is accepted, and stated.
- **Never raises, never waits.** Every failure costs the capture and nothing
  else: a contended lock, a gone scene or campaign, an `OSError`, a
  serialization error, or a fence that fails. The lock is the existing
  non-blocking acquisition. A failure writes one `warning` line naming the
  task and the exception type, and nothing else, as `_captured` already does
  (`inference.py:344-346`). It is counts-only, and holds no capture content.
- **Provider error text is never captured.** `_outcome` builds a failed call's
  record with `error=f"{error.kind}: {error.detail}"`
  (`inference.py:321-330`). `detail` is the provider's own text
  (`openrouter._http_error`, `openrouter.py:98-105`), which can echo the
  input or a key. The hook rewrites a failed call's record to
  `{"error_kind": kind, "error_status": status}`, plus `code` where the error
  has one (`native_unrepresentable`), and drops `error`. This is done in
  `decision_capture`, so `decisions.outcome` and the decide-chain golden do
  not move for it.
- **Memory.** Once `MAX_CALLS_IN_FULL` calls are held, the hook keeps a later
  call's outcome only and drops its messages at append time (§3.4).

### 3.4 The entry's shape

The entry is a `context_breakdown`-shaped payload, so `ContextBreakdown`
renders it unchanged. Assembled by `decision_capture._breakdown(task,
calls)`:

- **Message sections**, per call in settle order, built as `_capture` builds
  them (`character_turns.py:644-669`). Ids are `message_{i}` when the scope
  made one call, which keeps the speaker's single-call section ids.
  With several calls they are `c{k}_message_{i}`, labelled
  `call {k+1} · {role}`. A call refused unsent (`messages=[]`) has no message
  sections. Its outcome says why.
- **One outcome section**, id `decision`, zero tokens, last:

  ```json
  {"task": "continuity-reconcile",
   "calls": [{"part": "", "stage": 0, "mode": "native", "provider": "...", "model": "...",
              "at": [3], "items": [{"backend": "native", "answers": {...}}]},
             {"part": "", "stage": 1, "mode": "structured",
              "error_kind": "timeout", "error_status": null}],
   "notes": {}, "elided_calls": 0, "truncated": false}
  ```

  Each call record is `decisions.outcome` with `stage` and `at` (§3.2),
  plus `part` from the scope. A failed call's `error` is replaced by kind and
  status (§3.3).

  **Every scope uses this envelope, the speaker's included.** The speaker's
  outcome section therefore changes shape. It was the bare
  `decisions.outcome` dict, which
  `test_the_selector_capture_records_the_decision` pins
  (`test_character_turns.py:665-668`). It becomes the envelope, with one call
  record equal to that dict plus `part`, `stage` and `at`. The test is
  rewritten to that (§6, test 11). Readers handle one shape, not two. A
  speaker entry captured before this keeps the bare shape on disk until it
  ages out. The frontend renders the section as text either way, so it needs
  no branch.
- **Index row:** `task`, `model`, `operation: "decide"` and the three totals.
  `model` is the model of the first call that answered, else the stage-0
  primary's. `total_tokens` sums the message sections kept in full. Elided
  calls' tokens are left out, because counting them would mean a tokenizer
  pass per call. The entry then reads `elided_calls: N` beside the total, so
  the total is visibly a floor. `budget_tokens` is `store.context.budget_tokens()`, as
  today. `sampling` is the report for the first call that went out
  (`llm_sampling.report`), and none when that call was native (as
  `_capture` already rules, `character_turns.py:676-681`).

**Size limits.** Two caps, structural and to be tuned later against real
prompts:

- `MAX_CALLS_IN_FULL = 4`. The first four calls keep their message sections.
  Later calls keep their outcome only, and `elided_calls` counts them. Every
  call of one `decide()` renders the same template, so the first calls show
  the prompt's shape. Later chunks differ only in their items, and the outcome
  keeps every item's answers and batch index.
- `MAX_OUTCOME_CHARS = 64_000` on the outcome section's JSON text. Past it,
  whole call records are dropped from the end and `truncated: true` is set,
  so a record is never cut mid-object. Native distributions (up to 255
  options per question, `decisions.NATIVE_MAX_OPTIONS`) and rationales are
  what grow it.

### 3.5 Retention: decisions get their own pool

`prompt_log.record(..., operation: str = "")` writes `operation` into the
index row and the payload when non-empty. **Eviction counts within the new
entry's pool.** There are three pools:

- generations: no `operation`;
- scene decisions: `operation == "decide"` and `scene != ""`;
- campaign decisions: `operation == "decide"` and `scene == ""`.

Each pool keeps `depth()` entries, so one setting governs all three, and 0
still turns all capture off. Campaign decisions get a pool of their own so
that per-turn scene-break checks and speaker picks, and 02's later sites,
cannot evict the reconcile entries. Those are the only content of the
"What the sweep asked" list (§3.8).

A reconcile pass or an absorb's voice-drift phase can therefore evict only
older decisions, never a turn. This answers 01's first two reasons with one
store, one counter, one `repoint_scenes`/`forget_scene` fan-out, and no second
log.

`_well_formed_row` and `_well_formed` accept an optional `operation` that
must be a `str` when present. Rows written before this have none, and stay in
the generation pool until they age out. That includes the speaker's existing
captures.

### 3.6 Where each site files

| Site | Task | Scope | `sid` | Fence |
|---|---|---|---|---|
| speaker pick (`character_turns._select`) | `response-selector` | one pick | the scene | scene identity |
| scene-break (`scenes._break_ask`) | `scene-break` | one check | the scene | scene identity |
| voice drift (`scenes._stage_voice_drift` loop) | `voice-drift` | the whole phase; `part` = `aid` | the scene | scene identity, plus the review's `abandoned` check |
| continuity identity (`scenes._resolve_identity`) | `continuity-identity` | one check | the absorbed scene | scene identity |
| continuity reconcile (`continuity._adjudicate`) | `continuity-reconcile` | one pass | `NO_SCENE` | `not stillborn()` |

The speaker moves from `capture=lambda ...: run_in_threadpool(_capture, ...)`
to the helper. Its entry keeps the same message section ids, gains
`operation: "decide"`, and its outcome section becomes the §3.4 envelope.
`_capture` itself stays as the generation path, and it is what the
decide-chain golden calls.

**Campaign-level entries** use `scene: ""` (`prompt_log.NO_SCENE`, added by
this spec).
`_record_prompt` cannot take that scene, because it proves a scene exists. A
sibling, `common._record_campaign_prompt(cid, task, breakdown, ...)`, proves
the campaign exists instead (`store.campaigns.read.read_campaign` raising
`CampaignNotFound`, `store/campaigns/read.py:32-37`). It does so inside the
same non-blocking hold, and bumps `store.revision` after the write as
`_record_prompt` does (`routes/common.py:497-530`). Two new routes:

- `GET /campaigns/{cid}/prompts`: the campaign-level entries, newest first.
- `GET /campaigns/{cid}/prompts/{eid}`: one entry, scoped to `scene == ""`.

There is no diff route at campaign level. Scene-scoped routes cannot reach a
campaign-level entry, because a path segment is never `""`.

### 3.7 The diff route

`GET .../prompts/{eid}/diff?against=live` on a decision entry answers **409
`{"kind": "not_comparable"}`**. A decision entry is one whose `operation` is
`decide`, or whose `task` is `response-selector`: the same test the
frontend's `isDecision` applies, so a legacy speaker entry is refused too. The live side is the
chat composition, and a decide prompt compared with it is noise. A comparison
against another frozen entry of the same task is allowed.

### 3.8 Frontend

- `api/types.ts`: `PromptEntry` gains `operation?: "decide"`. `task` widens to
  `... | "response-selector" | "scene-break" | "voice-drift" |
  "continuity-identity" | "continuity-reconcile"`.
- `turnLabels.ts` labels:
  - `response-selector`: "Speaker pick"
  - `scene-break`: "Scene-break check"
  - `voice-drift`: "Voice check"
  - `continuity-identity`: "Duplicate check"
  - `continuity-reconcile`: "Continuity sweep"

  `isDecision(row) = row.operation === "decide" || row.task ===
  "response-selector"` is exported. The second half covers the speaker
  entries already on disk.
- `SceneInspector`:
  - Turn history lists only `!isDecision` rows.
  - A new `SideSection` "Decisions" (collapsed by default) lists the scene's
    decision rows with the same row button, and opens the same frozen view.
  - For a decision entry, the compare picker omits "The live preview" and
    lists only entries with the same task.
- `ContextBreakdown`: unchanged. One `decision` section is still the outcome.
- `ConfigView.lastPrompt` uses the newest non-decision entry.
- The Settings field "Kept turn prompts" keeps its key. Its caption becomes
  "per campaign; decisions are kept separately, as many again".
- **Campaign-level captures:** the ledger's continuity review
  (`components/continuity/ContinuityReview.tsx`) gains a collapsed "What the
  sweep asked" list under Refresh. It reads `GET /campaigns/{cid}/prompts` and
  opens an entry read-only through `ContextBreakdown`. This follows the
  list/detail rule: a row opens a read-only view, and there is no edit step,
  because a capture is never editable.

## 4. Contract

**01b-C1. One capture helper, used at all five decide sites and every new
one.** Each outcome records `stage` and `at`. Captures go to their own
retention pool. The reconcile sweep captures at campaign level. Every capture
is fenced.

- `decision_capture.capturing(cid, sid, task, *, fence=None,
  abandoned=None)`, `Scope.hook(part="")` and `Scope.note(part, key, value)`
  are the one way a site captures a decision. The five sites in §3.6 use
  them, and so does every decide site added later (02, 10, 11, 13, and 01g's
  Decision-as-tool).
- The AST test in §6 holds this across the whole package. A decide call
  outside `routes/` (01g's tool handler, or a store-level helper 10 might
  add) passes a `capture=` that is a parameter of its own enclosing function,
  as a pass-through. Its `routes/` caller supplies `scope.hook()`.
- A filed entry holds:
  - the request as sent per call (structured messages, or the native
    normalised body);
  - per call: mode, the provider and model that answered, `stage`, the batch
    indices `at`, and a failed call's error kind and status (never its
    text). An escalation call 01d
    sends inside the scope is one more call record. It carries
    `hop: "escalation"` when 01d-C2b stamps it on the call's outcome, and the
    capture passes the field through unchanged;
  - per item: backend, normalised answers with `reason`, `detail`,
    `probability` and `distribution` exactly as the backend reported them
    (never computed here), and the rationale;
  - the caller's post-decision notes (`Scope.note`: 01c's draw, 13's legal
    set).
- One entry per scope. The outcome section is always the §3.4 envelope,
  the speaker's included. Each entry is in the decision retention pool,
  labelled, and listed apart from turns.
- Campaign-level decisions are filed with `scene: ""` and are readable
  through the two campaign routes.
- Every capture is fenced: on the strict scene identity at scene level (a
  `None` identity captures nothing), and on the site's own `fence` at
  campaign level (§3.3).
- **Inputs a new decide site must supply:** `cid`, `sid` (or `NO_SCENE` with
  a `fence`), and the task.

**01b-C2. Capture stays off the decide path.** It is filed after the call
settles, never on cancel, and keeps the speaker capture's privacy.

- The per-call hook does no I/O beyond what `_captured` already does in a
  worker thread.
- Filing happens once, after `decide()` has returned or raised, in a worker
  thread, under a non-blocking lock. It is skipped on contention, and on a
  site's `abandoned` check answering True.
- No capture failure raises, changes a `Decision`, files a ledger or error
  row, or delays the caller by a blocking wait. A cancelled scope files
  nothing.
- **Privacy matches the speaker capture's:**
  - nothing is composed or kept when `prompt_log_depth` is 0;
  - entries live only in the campaign's own `prompts/` and leave with their
    scene (`forget_scene`, `repoint_scenes`) or campaign;
  - no key, URL or provider error text is captured. The native body is
    `llm.native_body`, which has neither, and a failed call is recorded as
    kind, status and code only (§3.3). This is stricter than today's speaker
    capture, which stores `detail`;
  - no raw reply text is captured, only the normalised outcome and the
    requested rationale;
  - no capture content reaches `logs/`, `usage/` or the error store. A
    failed capture writes one `warning` line with the task and the exception
    type only. The identity and reconcile log rows stay counts-only.

## 5. Interaction with repo rules

- **Guards.**
  - `test_usage_guard.py`: no new LLM call.
  - `test_operation_guard.py`: the five `decide` calls keep their task literal
    and `resolved=`.
  - `test_atomic_guard.py`: writes stay inside `prompt_log` through `atomic`.
  - `test_paths_guard.py`: no new path building outside `prompt_log._root`.
  - `test_import_guard.py`: `decision_capture` imports at module scope, and
    nothing in `store/` imports it.
  - `test_lock_domain_guard.py`: `prompt_log` is already classified, and
    `record` keeps its non-blocking acquisition.
  - `test_lock_order_guard.py`: one campaign lock at a time.
- **Write token.** A capture is a campaign write and bumps `store.revision`
  after the write and inside the hold, as `_record_prompt` does today
  (CLAUDE.md, "A campaign carries a write token"). A scene-break follow-up or
  a reconcile pass that captures stamps one more time. That costs a caller
  holding an older token only a re-price, never a refusal it was not already
  owed.
- **Detached runs.** Captures run inside a `review` (identity, voice drift),
  a `background` run (scene-break follow-up, reconcile) or a turn (speaker).
  None holds an exclusion key for the capture. The fence (§3.3) replaces the
  identity guarantee the scene's exclusion key gives the review and the turn,
  for the two background sites that hold none.
- **Golden.** `test_decide_chain_golden.py` moves once, by §3.2, and is
  argued in its PR.
- **01 spec §9.4.** The ruling "Scene-break and voice drift never had a
  capture and gain none ..." is superseded here. CLAUDE.md's sentence "the
  scene-break, voice-drift and continuity decisions capture nothing" is
  rewritten in the implementing PR to name the helper, the pool and the
  campaign-level entries.
- **01s.** It touches neither the prompt log nor `SceneInspector`. The
  Settings caption change is in the Context section, which 01s leaves in
  place.
- **Android / pydantic v1:** no new dependency and no models.

## 6. Tests and acceptance

Backend:

1. `test_decision_capture.py` (new), with `prompt_log.capturing` patched True:
   - each of the five sites files exactly one entry per scope with
     `operation == "decide"`, the right `task` and the outcome section last;
   - a two-chunk identity check files one entry with `c0_*`/`c1_*` message
     sections and two call records whose `at` lists are batch indices.
2. A native primary failing an item and a structured fallback stage answering
   it: one entry, two call records, `stage` 0 with the error and `stage` 1 with
   the answer.
3. Capture off (`prompt_log_depth: 0`): no entry, and `decide` was handed
   `capture=None` (no native body built: `_native_request` is not called).
4. A raising filer (patched `prompt_log.record`): the decision is returned
   unchanged, no error row is filed, and no `ERROR` log line is written.
5. Cancellation mid-scope files nothing. `Abandoned` files nothing.
   - A voice-drift phase whose loop swallowed `Abandoned` files nothing,
     because its `abandoned` check answers True.
   - An `LLMError` that no item answered files the entry with every call's
     error kind and status, and the exception still reaches the caller.
   - A `DecideRequestError` files an entry with `calls: []` and the
     `invalid_request` sentence.
6. Fence:
   - a scene deleted and recreated under the same id during a scene-break
     check gets no entry;
   - an identity-less scene (no `id:` header) files nothing, and its file is
     not written (no identity minted);
   - an identity read that raises at filing files nothing;
   - a reconcile run marked `forgotten` files nothing;
   - a contended campaign lock files nothing and does not wait.
7. Retention: with `prompt_log_depth: 3`:
   - four reconcile passes leave three campaign-decision entries and every
     turn entry and scene-decision entry;
   - four scene-break checks leave three scene-decision entries and every
     reconcile entry;
   - four turns leave three turn entries and every decision entry.
8. Size: a scope with six calls keeps four calls' message sections and
   `elided_calls == 2`, and holds no fifth call's messages in memory.
   `Scope.note` values count against `MAX_OUTCOME_CHARS`. An outcome over `MAX_OUTCOME_CHARS` drops whole call
   records from the end and sets `truncated`.
9. Routes:
   - `GET /campaigns/{cid}/prompts` lists only `scene == ""` entries;
   - the scene-scoped detail route cannot read a campaign-level entry;
   - `diff?against=live` on a decision entry is 409 `not_comparable`, a
     legacy `response-selector` entry with no `operation` included.
10. Revision: a capture bumps the campaign token once, after the write. A
    skipped capture bumps nothing.
11. Existing:
    - `test_the_selector_capture_records_the_decision`
      (`test_character_turns.py:644-668`) is rewritten. The decision section
      is the envelope, `calls` holds one record equal to the old
      `decisions.outcome` dict plus `part`, `stage` and `at`, and the row
      carries `operation: "decide"`. The other selector capture tests keep
      their section-id assertions;
    - `OUTCOME_SECTION_ID == "decision"` still holds;
    - the decide-chain golden is re-recorded under §3.2, and its diff shows
      only `stage` and `at` added.
12. Privacy:
    - no API key or base URL string appears in any payload written in tests
      1-2 (asserted over the file bytes);
    - a failed call whose fake `LLMError.detail` is a distinctive string
      (echoing a masked key, `sk-...abcd`) leaves that string nowhere in the
      payload;
    - `logs/` holds no capture content. A forced filer failure writes one
      warning naming only the task and the exception type.

Frontend (vitest):

1. Turn history hides decision rows. The Decisions section lists them with
   the new labels, collapsed by default.
2. Opening a decision entry shows the read-only breakdown with "outcome · not
   sent". The compare picker has no live option and only same-task entries.
3. `ConfigView`'s context bar uses the newest non-decision entry.
4. The continuity review's "What the sweep asked" list opens an entry
   read-only.

Acceptance: a guard in `test_decision_capture.py` walks the **whole
package** for `decide` operation calls. It reuses `test_operation_guard`'s
import-binding recogniser, so a call through an alias is still found. Each
call must pass one of two capture shapes:

- in `routes/`, `capture=<scope>.hook(...)`;
- outside `routes/`, a `capture=` that names a parameter of the enclosing
  function, which is a pass-through. 01g's tool handler is one by name.

Anything else needs a `# capture-ok: <reason>` marker. The guard family's
rule applies: a marker with no reason fails, and the markers are capped
at 2. So a sixth site added by 02, 10 or 11 cannot skip capture. `make check`
is green.

## 7. Non-goals

- Capturing `generate` calls that a decide site's feature also makes (the
  scene-break title, absorb extraction, dossiers).
- A durable decision history, or decisions in exports and backups beyond
  where `prompts/` already goes.
- Editing, deleting or re-running a decision from its capture.
- Capturing eval runs, which have no campaign (01a).
- Showing the distribution as a chart. The outcome text is the display, and
  01c can add a renderer.

## 8. Open questions

1. **One `depth()` for both pools, or a separate `decision_log_depth`?**
   *Recommendation: one setting for now.* A second number is a second knob to
   explain, and the pools are already independent. Add it if a library's
   sweeps make decisions churn faster than a reader inspects them.
2. **Voice drift: one entry per phase, or one per NPC?** *Recommendation: per
   phase (§3.6).* One absorb is one review, and per-NPC entries are what 01's
   ruling objected to. `part` keeps NPCs apart inside the entry.
3. **Should the speaker's legacy per-call entries be migrated into the
   decision pool?** *Recommendation: no.* They age out under the old pool, and
   `isDecision` already lists them under Decisions.
4. **Where should campaign-level captures be shown** if the ledger's
   continuity pane is the wrong home? *Recommendation: there.* It is the only
   surface where the reconcile sweep is visible, and it already has Refresh.
   A global "decision log" page is a later choice.
5. **Shared structure, not a missing edge.** `ROADMAP-CHECKLIST.md`
   ("Shared structures") lists `_Call.stage` / `positions` under 01a and 01b.
   Both specs add `_Call.stage` and
   `_Call.positions`. If 01a's plan lands first, this spec's plan reuses its
   fields and drops that task. Nothing else is shared.

## 9. Review record

**Substitute adversarial review, 2026-10-10**
(`scratchpad/roadmap/reviews/01b.md`): 0 blocking, 5 should-fix, 9 minor.
Each finding was verified against the code.

- **S1 (folded).** The claim that the speaker entry is byte-compatible was
  false: `test_character_turns.py:665-668` pins the bare outcome. Every scope
  now uses the envelope, and the test is rewritten (§3.4, §3.6, test 11).
- **S2 (folded).** `scene_identity` returns `None` both for a missing scene
  and for one that predates identities (`identity.py:200-212`), so
  `None == None` would match. The fence uses `scene_identity_strict`, takes no
  capture on `None`, and re-checks strictly at filing. It mints no identity
  (§3.3, test 6).
- **S3 (folded).** `_outcome` stores `LLMError.detail` (`inference.py:326-328`).
  The capture now records kind, status and code only. This is done in
  `decision_capture`, so the golden does not move for it (§3.3, C2, test 12).
- **S4 (folded).** The guard covers the whole package. A pass-through
  parameter is allowed outside `routes/`, with a capped `# capture-ok:`
  marker for anything else (C1, §6).
- **S5 (folded).** `Scope.note` carries post-decision facts. The 01c and 13
  rows are restated to match (§3.3, Required by).
- **Minors folded:**
  - M1: `prompt_log.NO_SCENE` is new;
  - M2: `DecideRequestError` entry shape;
  - M3: the warning line, stated;
  - M4: the diff route uses `isDecision`'s test;
  - M5: elided calls' tokens are left out and messages are dropped at append;
  - M6: an `abandoned` check for voice drift;
  - M7: campaign decisions get their own pool;
  - M8: reconcile reuses `stillborn`, and the forget-window residual is
    stated;
  - M9: embed capture is noted as already met.
- **Rejected:** none.

**Cross-spec items, for the coordinator (no other spec was edited):**

- 01c: file the draw through `Scope.note(part, "draw", {u, selected, mass})`
  rather than expecting it in `decisions.outcome`.
- 13: file the legal-set size and digest through `Scope.note`.
- 01g: the Decision-as-tool handler passes `capture=` through as a parameter
  (the guard allows it by name). Its `routes/` entry point must open a scope.
- 01d: `hop: "escalation"` on a call's outcome is passed through. A capture
  call record is per chunk with its re-send folded in, while a 01a
  `CallRecord` is per metered request.
- 11: the "same section id convention as the speaker capture" now means the
  §3.4 envelope.

