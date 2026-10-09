# 01h. Embedding options, async embed, embedding evals

**Status:** Draft — spec gate (`/codex:adversarial-review`) pending.
**Date:** 2026-10-09
**Roadmap:** 01h in `ROADMAP-CHECKLIST.md`. Lane: retrieval (feeds 03, 05, 08, 09).
**Baseline:** `main` at `35c1fb7`.
**Reconciles:** the bundle draft `01-inference-backend-refactor.md` section 8.2
("embedding-relevant profile options" in `space_id`; "the public inference
abstraction should be async"), and extends the landed 01
(`2026-10-07-inference-backend-refactor-design.md` section 7.3, which promised that
`embed` runs in a worker thread "until a native async client replaces it").
It must hold 03's section 2a item 7
(`2026-10-09-roadmap-03-content-addressed-compiled-cache-design.md`).

> Implementation rule: re-read the code and every PR landed on this subsystem
> since the baseline before planning.

## Depends on

| Contract | Provided by | What this spec uses it for | Hard or soft |
|---|---|---|---|
| (landed) `embed_sync`, `embed_space.endpoint`, `resolve.embedding`, model facts, the `confirm_embedding` gates | 01 | Everything here extends them in place | Hard |
| 01a-C1 | 01a | Per-run wall time, tokens and the three money columns in the embedding eval's report (C6) | Hard for C6 only |
| 01a-C2 | 01a | A live embedding eval files its ledger rows under the marked eval scope, kept out of campaign spend | Hard for C6 only |
| 01a-C3 | 01a | One comparison table across embedding configurations (C1/C2 on vs off) | Soft |
| 01s (planned) | 01s | The Embedding row of the Models summary, where the options readout sits (section 4.4) | Soft |

C1 to C5 depend on nothing unlanded. Only C6 waits for 01a.

## Required by

| Contract (provided here) | Consumer | What the consumer uses it for |
|---|---|---|
| 01h-C1 | 08, 09 | Embed SearchDocuments as documents and the retrieval query as a query |
| 01h-C2 | 08, 09 (indirectly) | Smaller vectors for a larger corpus, at the user's choice |
| 01h-C3 | 03, 08 | The vector key covers the options, so 03's "vectors are keyed by space and text" (2a item 7) and 08-C2's key stay true |
| 01h-C4 | 09 | Retrieval on the turn path without blocking the event loop |
| 01h-C5 | 05 | `cache sync --all` embeds across campaigns in shared batches and still charges each campaign its own rows |
| 01h-C6 | 09 (09-C4), and every change of C1/C2 defaults | Evidence that an option, a model or a retrieval change helps |

## 1. Current state (reconciled against main)

**One metered door.** `store/inference/embed.py:143` `embed_sync(task, texts,
*, space, client, deadline, budgeted, campaign, scene, cached, uncached)` files
one ledger row per call (`usage.meter`, `embed.py:180`), however many
`embeddings.BATCH` (64, `embeddings.py:59`) requests the client splits it
into. It writes one Debug capture line with counts and `space_id`, never text
(`embed.py:199-223`). A failure records its kind and HTTP status only
(`record_failure`, `embed.py:107`). `NOT_SENT` files nothing, and a `budgeted`
deadline cut is `aborted` (`embed.py:26-40`). The task must be in
`routing.EMBED_TASKS` (`routing.py:174-175`: `semantic-recall`,
`semantic-search`, `art-catalog`, `continuity-similarity`).

**The async form is a thread, and nobody calls it.** `embed()`
(`embed.py:226-236`) is `asyncio.to_thread(embed_sync, ...)`. The guard counts
six operation calls and says "the async `embed` has no caller yet"
(`backend/tests/test_operation_guard.py:180-184`). The client is synchronous
by design (`embeddings.py:8-15`). It also names a hole it cannot close: response
headers drip-fed a byte at a time are bounded per read, not in total, and "the
fix is the async rewrite ... where the whole thing is one `asyncio.wait_for`"
(`embeddings.py:28-38`).

**The request carries no options.** The body is exactly `{"model", "input"}`
(`embeddings.py:480`). There is no `dimensions`, no input type and no prefix.
The returned width is bounded (`MAX_DIMS`, `MAX_TOTAL_DIMS`,
`embeddings.py:104,124`) but never compared with anything requested.

**The space.** `resolve.space_of(conn, model)` is `f"{id}\0{rev}\0{model}"`
(`store/inference/resolve.py:1000-1031`). `embed_attempt` sets it only when the
role embeds (`resolve.py:1043-1069`). `resolve.embedding` is the role's one
reader (`resolve.py:1085-1139`). `embed_space.endpoint_of` turns it into the
dict every caller hands in (`store/embed_space.py:45-59`: `model, base_url,
key, space, provider, provider_name, provider_kind, target`).

**The vector key.** `vectors._path` is `sha256(f"{space}\0{text}")`
(`store/vectors.py:115-121`), little-endian float32 with a CRC32, normalized on
the way in. The space string is also persisted outside `vectors.py`: the
continuity sweep stores it in its candidate basis (`embedding_space`,
`store/continuity/reconcile.py:864`) and hashes it into each subject's identity
(`reconcile.py:679`). A space id that changed for a store with no options would
cost a full re-embed **and** a full continuity re-sweep.

**The confirm gates.** A role change to a selection that embeds
(`store/inference/settings.py:695-711`), a provider edit that moves the space
(`embed_space.moved_by`, `embed_space.py:158-193`, guarded at
`routes/config.py:803-812`), a facts write that turns the role on
(`embed_space.facts_moved`, `embed_space.py:196-222`, guarded at
`routes/config.py:814-826`) and a legacy `PUT /config` change
(`config_moved`, `embed_space.py:225-237`). Each answers 400
`confirm_embedding` without `confirm_embedding: true`. `facts_moved`'s
docstring says "facts never move the space itself", which C3 makes false.

**The call sites** (all `embed_sync`):

| Site | Task | Shape | Thread today |
|---|---|---|---|
| `store/context/semantic.py:352,373` | `semantic-recall` | `[query, *warm docs]`, then a query-only retry | Worker, except the opener (below) |
| `store/semsearch.py:254,266` | `semantic-search` | `[query, *warm passages]`, then query-only | Worker (`routes/search.py:30` is `def`) |
| `store/context/art.py:570` | `art-catalog` | `[query, *warm descriptions]` | Worker, except the opener |
| `store/continuity/similarity.py:552` | `continuity-similarity` | Record texts only (symmetric) | Worker (`run_in_threadpool`, `routes/scenes.py:2211`; `routes/continuity.py:750`) |

Queries are never cached (`semantic.py:322-324`). Every cached vector is a
document-side vector.

**One caller embeds on the event loop.** `routes/greetings.py:98-106`: the
opener's `async def frames()` calls `store.context.compose_opener`
synchronously. `compose_opener` runs `_assemble` (`store/context/assemble.py:101`),
which runs world-info activation with semantic recall
(`assemble.py:432`, `world_state.py:268`) and `art.catalogue`
(`assemble.py:739`). Both can make a blocking embeddings request.
`runs.start_detached` drives that generator in `pump()` on the lifespan loop
(`routes/runs.py:1565`). Every other compose is in a `def` handler or behind
`run_in_threadpool`. An AST walk of `routes/` for async functions that call a
compose or embed path finds only this one. (The other hit,
`routes/tracker.py:405`, is the tracker's own `build_messages`, which embeds
nothing.)

**Attribution.** `embed_sync` takes one `campaign` and one `scene`.

**Evals.** `evals/run.py` grades generation and decide cases. Nothing measures
retrieval quality, and live runs are unmetered (01a closes that).

## 2. Goal (and what is explicitly not the goal)

1. Send a **query or document input type** to the providers and models that
   use one, either as a request field or as a text prefix, at the user's word
   (C1).
2. Request a **smaller output dimension** where the endpoint supports it, and
   refuse a reply that ignored the request (C2).
3. Make every option part of the **space identity**, with option-less spaces
   **byte-identical** to today. No `.vec` file, continuity basis or 03 key is
   invalidated by upgrading (C3).
4. Give async callers a **native async embed** that never blocks the loop and
   closes the header-drip hole. Make blocking on the loop impossible to ship
   unnoticed (C4).
5. Let one batched job embed **across campaigns** and still file each
   campaign's spend under that campaign, exactly (C5).
6. Measure **retrieval recall@k** on a synthetic corpus, comparable across
   models and options, reported through 01a (C6).

Not the goal: native non-OpenAI-shaped APIs (Cohere `/v2/embed`, Gemini
`embedContent`), local Matryoshka truncation, a vector index, re-ranking, query
caching on any current path, or changing which tasks embed.

## 3. Embedding options (C1, C2)

### 3.1 The option set

One frozen dataclass in `wire.py`. `wire.py` is a leaf module that the client,
the resolver and the operation can all import without a cycle.

```python
@dataclass(frozen=True)
class EmbedOptions:
    input: Literal["none", "prefix", "param"] = "none"
    query_prefix: str = ""       # input == "prefix"
    document_prefix: str = ""    # input == "prefix"
    param_field: str = ""        # input == "param", e.g. "input_type" or "task"
    query_value: str = ""        # input == "param", e.g. "query"
    document_value: str = ""     # input == "param", e.g. "document"
    dimensions: int | None = None
    dimensions_field: str = "dimensions"   # or "output_dimension"
    def canonical(self) -> str: ...        # section 5.1
    def is_default(self) -> bool: ...      # canonical() == "{}"
```

Grimoire has **two input types**, `query` and `document`. A symmetric
comparison (continuity's record against record) embeds both sides as
`document`. Then the document vectors that recall, search, art and continuity
share by text (`vectors.py`) stay shared.

### 3.2 Where the options live: stated model facts

The options are a fact about **one model on one provider**. That is exactly
the scope of a space (`id`, `rev`, `model`). So they are stored as a stated
fact in `<home>/llm_connections/<id>.facts.json`, under the model:
`"embedding": {...}` beside `vision`, `prefill` and `post_process`
(`store/inference/facts.py:1-24`).

- A stated fact survives a `rev` change. The rev still moves the space, as
  today.
- They are written through `facts.state(..., embedding=...)`: `None` leaves
  them, `{}` removes them, and anything else replaces them after
  `_check_embedding`. The validation:
  - field names match `^[a-z_][a-z0-9_.]{0,40}$`;
  - field names are not in the reserved set `model`, `input`,
    `encoding_format`, `user`, and are not each other;
  - prefixes and values are at most 200 characters, with no control
    characters;
  - `prefix` has at least one non-empty prefix;
  - `param` has a field and both values;
  - `1 <= dimensions <= embeddings.MAX_DIMS`.

  A failed check is a 400 before anything is written.
- `facts.of` reads the block through the same check. **A block that fails
  validation reads as no options, as a whole.** It never reads as a mix of
  valid fields. `GET .../facts` flags it as `embedding_invalid`.
- Below format 2 the facts route already answers 409 `not_migrated`
  (`routes/config.py:1677-1700`). So a legacy store never has options, and its
  space ids cannot move.

**Options are never derived.** No catalog row, preset, name rule or probe
verdict sets them. A derived option would change when a later build changed
its table, and a space id that changes on upgrade forces the re-embed C3
exists to prevent. The UI may *suggest* values (section 4.4). Only a user's
saved, confirmed write applies them.

### 3.3 The resolver carries them

`resolve._target` (`resolve.py:422-458`) reads `model_facts["embedding"]` into
a new `wire.Target.embed_options: EmbedOptions | None`. It reads `prefill`
and `post_process` the same way (`_stated`). `embed_attempt` passes those
options to `space_of` (section 5). `embed_space.endpoint_of` adds `"options":
target.embed_options or EmbedOptions()` to its dict.

**The one invariant C1 to C3 rest on: the request is built from the same
`EmbedOptions` object the space id was computed from.** Whatever a read
yields, including a mangled block read as default, what is sent and where it
is cached agree.

### 3.4 The operation and the client

`embed_sync(..., queries: int = 0)` says that the first `queries` texts are
query-side and the rest are documents. Every current call site already puts
its query first (`[query_text, *missing]`). Recall, search and art pass
`queries=1`, including on their query-only retries. Continuity passes `0`.

The client's `embed` gains `options: EmbedOptions | None = None` and
`queries: int = 0`. The model-test probe (`routes/config.py:1203`) passes the
options stated for the model under test, so a confirmed test call verifies
them.

- **`none`**: the texts are sent unchanged, one request per `BATCH` as today.
  The body is byte-identical to today's `{"model", "input"}`.
- **`prefix`**: each text is sent as `prefix + text`. The query and document
  prefixes are per text, so a mixed batch is still one request. Byte clipping
  (`DOC_BYTES`, `QUERY_BYTES`, `PASSAGE_BYTES`) happens before the prefix is
  added. A prefix is at most 200 characters, which those bounds already leave
  room for under an 8k-token window (`semantic.py:94-102`).
- **`param`**: a request carries one input type. So the client splits at the
  query/document boundary as well as every `BATCH`. The body is
  `{"model", "input", <param_field>: <value>}`. A recall turn on a `param`
  space therefore costs two round trips instead of one: the query, then the
  warm run. It is still **one ledger row and one capture line**, because the
  meter covers the whole call, as it already does across batches. The extra
  round trip is stated on the Embedding card.
- **`dimensions`**: `<dimensions_field>: n` is added to every request body.
  Every returned vector must be `n` wide. If one is not, the client raises
  `EmbeddingsError("bad_response", ..., code="dimensions_mismatch")`. This is
  an endpoint that silently ignores unknown fields (common among
  OpenAI-compatible servers). Storing its native-width vectors under a space
  that claims `n` would be honest only until it started honouring the field.
  The error is filed like any other: kind and status only.

`estimate_prompt` counts what was sent, prefixes included. The capture line's
`bytes` counts the bytes sent.

### 3.5 What providers accept (to verify at plan time)

The design is a generic field plus a generic prefix, because the request field
and its values differ by provider, and many self-hosted servers have neither.
The table is guidance for the UI's suggestions and for this spec's tests. It
is not code.

| Endpoint (OpenAI-shaped `/embeddings`) | Input type | Output dimension | Confidence |
|---|---|---|---|
| OpenAI | none (text-embedding-3 is symmetric) | `dimensions`, text-embedding-3 and later only; unknown fields refused with 400 | Documented |
| Voyage AI | `input_type`: `query` / `document` (or null) | `output_dimension` on the voyage-3.5 / 3-large generation (256, 512, 1024, 2048); `usage.total_tokens` rather than `prompt_tokens` | Documented; verify field list |
| Jina | `task`: `retrieval.query` / `retrieval.passage` (jina-embeddings-v3) | `dimensions` | Documented for v3; v4 unverified |
| Mistral | none | `output_dimension`, codestral-embed only | Verify |
| OpenRouter `/embeddings` | **Unknown** whether `input_type` is forwarded upstream | **Unknown** whether `dimensions` is forwarded | Unknown: the probe decides |
| Cohere compatibility API | **Unknown** | **Unknown** | Unknown; the native `/v2/embed` (`input_type` required, `output_dimension`) is not OpenAI-shaped and is out of scope |
| Gemini OpenAI compatibility | **Unknown** for task type | **Unknown** for `dimensions` | Unknown |
| Ollama `/v1/embeddings` | none: prefix by model | **Unknown** for the OpenAI route | Verify |
| llama.cpp `llama-server` | none: prefix by model | not supported, as far as known | Verify |
| vLLM | none: prefix by model | `dimensions` for models declaring Matryoshka support, an error otherwise | Verify |
| HF TEI, LM Studio | none on the OpenAI route, as far as known | **Unknown** | Verify |

Prefix conventions published by model authors. These are suggestion-table
entries; check each against its model card when the plan is written:

| Model family | Query | Document |
|---|---|---|
| nomic-embed-text v1 / v1.5 | `search_query: ` | `search_document: ` |
| E5 (e5-*-v2, multilingual-e5) | `query: ` | `passage: ` |
| BGE en v1.5, mxbai-embed-large-v1 | `Represent this sentence for searching relevant passages: ` | (none) |
| Qwen3-Embedding | `Instruct: <task>\nQuery: ` | (none) |
| EmbeddingGemma | `task: search result \| query: ` | `title: none \| text: ` |

Behaviour for an unknown field is covered both ways. A server that refuses it
gets a 4xx, which is `bad_response`. A server that ignores it gets the
dimensions check, while an ignored input type is undetectable and harmless:
the vectors are what the model made of that text.

## 4. Confirm before spend, and the UI

### 4.1 An options write moves the space

`facts_moved` already compares the space before and after a facts write
(`embed_space.py:209-219`). With options in the space id (section 5), it
reports a change of options for the role's own provider and model as a move
with no new logic. Its docstring and `EMBEDDING_FACTS_CONFIRM` wording change
from "turns the Embedding role on" to "moves the Embedding role to a new
vector space". The rule stays the one CLAUDE.md states: the 400 is decided
inside the hold that writes (`facts.state`'s `guard`).

- Moving **back** to no options is a move too. The option-less vectors may
  still be cached, because nothing prunes them (`vectors.py:61-66`). Knowing
  whether they are would mean scanning the cache, which the guard has no
  business doing.
- An options write for a model the role does not use moves nothing, and asks
  nothing. Choosing that model later is a role change, and the role change is
  what is confirmed.
- `moved_by` (a provider edit) reads the facts through `embed_attempt`, so it
  judges the post-edit record with its options included, with no change.

### 4.2 Writes that do not ask

These follow 01's existing exceptions. A passed test call does not write
options, and neither does a catalog refresh. Options are never derived, so
neither can move a space. That is stricter than the `embed: yes` rule, which a
passed test may lift.

### 4.3 Failure behaviour

| Situation | Result |
|---|---|
| Options write for the role's model without `confirm_embedding: true` | 400 `confirm_embedding`, nothing written |
| Invalid options body | 400, nothing written |
| Mangled block on disk | Reads as no options, and GET flags `embedding_invalid`. Requests and space agree, so nothing mixes |
| Endpoint ignores `dimensions` | Every call fails `dimensions_mismatch`, callers degrade to keyword, and the error store shows the kind |
| Endpoint refuses the field | 4xx, `bad_response`, same degradation |

### 4.4 Where the user sees and edits them

- **Edited** in `ModelFactsPanel` (`frontend/src/routes/ProvidersView.tsx:869`),
  as an "Embedding options" section shown when the model's `embed` capability
  is not a known `no`. It has the input mode (None / Prefix / Request field),
  its fields, and Dimensions with its field name. A **Suggest** control fills
  in a row of section 3.5's prefix table when the model id matches it. It
  fills the form only. Save does the rest, and a 400 `confirm_embedding`
  brings up the existing inline confirmation and its "Re-embed and save".
- **Read** on the Models summary's Embedding row (01s section 3.1). The row
  gains one line, "Options: query/document prefixes, 512 dimensions", with
  "two requests per recall" when the mode is `param`. The line comes from a
  new `EmbeddingCard.options` field (the canonical dict, or null). Nothing is
  editable on `/models/edit`, which fits 01s's form unchanged.
- The test call dialog's `embed` probe result already returns `dims`
  (`routes/config.py:1214`). The panel shows "requested 512, test returned
  1536: this endpoint ignores `dimensions`" when they differ.

## 5. Space identity (C3)

### 5.1 The encoding

```python
def space_of(conn: dict, model: str, options: EmbedOptions | None = None) -> str:
    base = f"{conn['id']}\0{conn['rev']}\0{model}"
    if options is None or options.is_default():
        return base                                   # byte-identical to today
    return f"{base}\0embopt1:{sha256(options.canonical())[:32]}"
```

`canonical()` is JSON with sorted keys, `separators=(",", ":")` and
`ensure_ascii=True`, holding only the fields that **change what is sent**:

- `input` and its two prefixes or field and values, when the mode is not
  `none` and it sends something;
- `dimensions` and `dimensions_field`, when `dimensions` is set.

So `prefix` with two empty prefixes, or a stray `dimensions_field` with no
`dimensions`, canonicalises to `{}`, which is the default. The identity
follows what is sent and nothing else.

- **The digest, not the JSON, goes in the space.** The space string is logged
  as the capture line's `space_id` (`embed.py:223`) and persisted in the
  continuity basis (`reconcile.py:864`). A digest keeps both short. It keeps
  user-typed prefix text out of the log, and a raw `\0` can never come from
  inside the options. 128 bits make a collision irrelevant.
- **`embopt1` versions the encoding.** A future change to `canonical()` must
  take a new tag deliberately. A golden test pins representative space ids,
  and it is never regenerated to make a change pass (the
  `test_lore_golden.py` rule).

### 5.2 Why no stored vector is invalidated

- A store with no `embedding` facts block (every store today, and every
  format-1 store forever) computes `base`, which is today's string, so
  `vectors._path` is today's digest. Every `.vec` file is still read. The
  continuity basis and identity hashes are unchanged, so no re-sweep runs.
  03's and 08's keys (`embedding_space + hash(text)`) are unchanged.
- An option set moves to a new space, and the old vectors become unreachable
  strays. That is the same bounded leak an edited entry causes today
  (`vectors.py:61-66`), and it is confirmed first (section 4.1).
- **03, section 2a item 7 holds.** Vectors stay keyed by space and exact text,
  never by `BUILD`. The options are inputs to the space, carried by the space
  id string 03 already treats as opaque.

### 5.3 Document vectors only, and a rule for query vectors

`vectors.py` caches **document-side** vectors under the space id. No path
caches a query vector today, and none is added here. For a future caller (09
may want cached expansions), the rule is fixed now so it cannot collide:

```python
def cache_space(space: str, options: EmbedOptions, side: Literal["query", "document"]) -> str
```

It returns `space` for a document, or for any side when `options.input ==
"none"`. In that mode a query vector **is** a document vector of the same
text, so sharing the key is correct. Otherwise it returns `space + "\0query"`
for a query.

## 6. Async embed (C4)

### 6.1 Nothing embeds on the event loop

1. **Fix the opener.** In `routes/greetings.py:98-110`, `frames()` awaits
   `run_in_threadpool` around `compose_opener` and `_record_prompt`. That
   moves its recall, its art ranking and its whole store read off the loop.
   The per-speaker loop and the frames it yields are unchanged.
2. **A runtime guard in `embed_sync`.** If `asyncio.get_running_loop()`
   succeeds in the calling thread, the call is on a loop thread.
   `asyncio.get_running_loop()` raises in a worker, an anyio portal thread and
   a CLI. On a loop thread, `embed_sync` raises
   `EmbeddingsError("network", "embedding refused on the event loop",
   code=ON_LOOP)` before any meter opens, and writes one ERROR log row (task
   only). `network` is the kind every caller already treats as "the endpoint
   is unavailable, do not retry" (`semantic.py:363-370`), so the turn
   degrades to keyword rather than freezing every other request for up to
   `TIMEOUT + READ_SLICE`. A static guard cannot catch this: the opener
   reaches `embed_sync` six frames down, through sync code.

### 6.2 A native async door

`embed()` stops being `to_thread`. It becomes:

```python
async def embed(task, texts, *, space, client: embeddings.AsyncEmbeddingsClient,
                deadline=None, budgeted=False, campaign="", scene="",
                cached=None, uncached=None, queries=0) -> list[list[float]]
```

It has the same validation, meter, `_stamp`, `record_failure`, NOT_SENT and
aborted rules, and capture line as `embed_sync`. The shared body moves into
helpers both doors call, so the two cannot drift.

- **`embeddings.AsyncEmbeddingsClient`** sits on `httpx.AsyncClient`. It
  shares `_Spend`, `_vectors`, `_status_kind`, the redirect refusal, the body
  bounds and the request builder from section 3.4 with the sync client. Only
  the transport loop differs. The **whole call** runs under one
  `asyncio.timeout(deadline - now)`. That closes the header-drip hole
  `embeddings.py:28-38` names, which the sync client cannot close.
- **Cancellation.** A cancel mid-request runs `_Spend.lose()` if a batch was
  in flight, files the meter `aborted` and re-raises. The capture line says
  `error: "aborted"`. A cancel is never an error row.
- **One client per app.** It is built in the lifespan and closed at shutdown,
  as the LLM clients are (#215). Callers reach it through a
  `routes.get_embeddings` dependency, whose override is the test seam. It is
  touched only from the loop, so it needs no lock (compare
  `embeddings.py:349-359`).
- `estimate_prompt` runs in `asyncio.to_thread`, as the probe already does
  (`routes/config.py:1212`): a loaded encoder counts synchronously.
- **Who uses it.** Code already in an async function: 09's retrieval and 10's
  planning hop, when they are written. Code in a worker keeps `embed_sync`. The
  context builder is synchronous to its roots (`semantic.py:72-82`), and
  making it async is not this spec. A worker thread does not block the loop,
  which is the property C4 promises. `vectors.load`/`save` stay synchronous
  disk I/O, so an async caller reaches them through `run_in_threadpool`.

## 7. Cross-campaign attribution (C5)

05's sync embeds what was hot for many paths, in many campaigns, in shared
batches. One row per campaign keeps per-campaign spend exact without
apportioning a number the provider reported for a whole request. Apportioning
by local token counts would charge each campaign a figure nobody reported.

```python
@dataclass(frozen=True)
class EmbedGroup:
    campaign: str          # "" = unattributed
    scene: str = ""
    texts: tuple[str, ...] = ()

def attribute(claims: Iterable[tuple[str, str]]) -> list[EmbedGroup]
def embed_groups_sync(task, groups: Sequence[EmbedGroup], *, space, client,
                      deadline=None, budgeted=False) -> list[GroupResult]
# GroupResult: vectors (in the group's text order) or None, and error kind or ""
```

- **`attribute`** takes `(campaign, text)` claims. A text claimed by exactly
  one campaign goes to that campaign's group. A text claimed by several, or by
  none (world-scoped), goes to the unattributed `""` group, embedded once.
  Charging it to whichever campaign sorted first would put one campaign's
  spend in another's total. Groups are ordered by campaign id with `""` last,
  and texts by first claim. The result is deterministic.
- **`embed_groups_sync`** makes one `embed_sync` call per group, under one
  shared deadline. That gives one ledger row and one capture line per group,
  and no request ever mixes campaigns. The cost is at most one extra request
  per group boundary, compared with perfect packing.
- **Failure.** `bad_response` (the input's fault) fails its group and the next
  group still runs. Any other kind (`auth`, `missing_key`, `rate_limit`,
  `network`, a deadline) stops the run. Every later group comes back with
  `error="not_sent"` and files nothing (01's "nothing sent, nothing filed").
  This is the same split `semantic._embed` makes (`semantic.py:363-370`). The
  caller saves what landed, as `similarity.embed_missing` does per chunk
  (`similarity.py:546-566`).
- `cached`/`uncached` go to the first group's call only (the `Counts` rule,
  `similarity.py:510-525`).
- `test_operation_guard.py` learns the new door: a task literal in
  `EMBED_TASKS`, and `space=` traced to `embed_space.endpoint`.
  `MIN_EMBED_CALLS` is unchanged until 05 adds a caller.

## 8. Embedding evals (C6)

`evals/embed/`, run by `evals/run.py --embed`:

- **Corpus** (`corpus.json`): invented documents using only the codebase's
  placeholder names (Seraphine, Mara, Winifred, Realm, Saltmarch). There are
  three shapes, one per kind of production caller:
  - lore entries for **recall** (the query is a scene's last few posts);
  - library records for **search** (the query is a short reader question);
  - ledger records for **identity** (the query is a reworded record, compared
    symmetrically as a document).

  Each query lists its relevant ids.
  - **Paraphrase positives** share no content word with their query. That is
    the miss recall exists for (`semantic.py:4-10`).
  - **Lexical decoys** share the query's names and words and are not
    relevant, so a lexical scorer cannot pass the case.
  - The size is set structurally: at least ten documents per unit of the
    largest k, so recall@k is not trivial. It is to be tuned later.
- **Metrics**: recall@k for k in {1, 3, 5, 10}, and MRR, per shape and
  overall. Alongside them, a **lexical baseline** over the same corpus using
  `store/search.py`'s term scorer. 09's hybrid design needs to know whether
  embeddings beat lexical on each shape, not just a raw number.
- **Replay (offline, in `make check`)** re-grades **recorded rankings** (query
  id to ranked doc ids) from `evals/recordings/embed/<config>.json`. Ids only:
  no vectors are checked in, and nothing private exists to record. A second
  offline case runs the whole path, `embed_sync` with options, through an
  `httpx.MockTransport` with a deterministic hashed bag-of-words embedder. It
  proves the harness, the prefixes, the `param` split and the dimensions
  check end to end, and makes no claim about quality.
- **Live (`--live --embed`)** costs money and is opt-in like every live run.
  - It resolves the Embedding role from the real store, as `run_live` does
    (`evals/run.py:72-112`), then embeds inside `temp_home()`. The synthetic
    corpus's vectors therefore never land in the user's cache.
  - Before sending, it prints the corpus size in bytes and the estimated
    tokens.
  - `--embed-options <json>` (repeatable) runs the same corpus under each
    option set in memory, without writing facts. That is the C1/C2 A/B.
  - Rows go under 01a-C2's eval scope with the real embed task of each shape,
    so 01a-C1's per-task report reads naturally.
  - The output is 01a-C1's per-run wall time, prompt tokens and three money
    columns, and 01a-C3's comparison table across option sets.
  - `--record` saves rankings.
- **What it cannot show:** whether a real library retrieves well. Quality on a
  synthetic corpus is evidence for a choice, and the default thresholds stay
  "tune against the inspector" (`semantic.py:66-68`).

## 9. Contract

- **01h-C1, input type.**
  - `embed_sync`/`embed`/`embed_groups_sync(..., queries: int)` mark the
    first `queries` texts as queries.
  - With the space's stated `input` mode, the request carries the type as a
    prefix (any batch) or as `param_field` (one type per request, under one
    meter).
  - `none` sends today's exact body.
  - No guarantee is made about vector quality.
- **01h-C2, dimensions.**
  - A stated `dimensions` is sent as `dimensions_field`.
  - Every returned vector must be that wide, or the call fails
    `bad_response`/`dimensions_mismatch` and the caller degrades.
  - There is no local truncation.
- **01h-C3, identity.**
  - `space_of(conn, model, options)` is today's string when the options are
    default, and `base + "\0embopt1:" + digest(canonical)` otherwise.
  - Options come only from stated facts.
  - The request is built from the object the space was computed from.
  - Vectors are keyed `sha256(space\0text)`, independent of `BUILD`.
  - Query vectors, if ever cached, use `cache_space` (5.3).
  - A change of the role's options is a confirmed move.
- **01h-C4, async.**
  - Nothing calls `embed_sync` on a loop thread. The runtime guard refuses it
    as `network`/`on_loop` and logs one error row.
  - `await embed(...)` is native async: one total deadline, cancellable (a
    cancel files `aborted`), metered and captured as `embed_sync` is.
  - Async callers get the client from `routes.get_embeddings`.
- **01h-C5, attribution.**
  - `attribute(claims)` plus `embed_groups_sync(groups)` give one row per
    group, and no request spans groups.
  - A text shared by several campaigns is unattributed.
  - An endpoint-wide failure stops the run, and the rest is `not_sent` and
    files nothing.
  - Synchronous, so 05's CLI can call it outside a loop.
- **01h-C6, evals.**
  - `evals/run.py --embed`: recall@k (1/3/5/10) and MRR per shape, against a
    lexical baseline.
  - Replay of recorded rankings runs offline in `make check`.
  - Live runs cost money, are opt-in, run in a throwaway store, are metered
    under 01a-C2, are reported through 01a-C1, and compare option sets
    through 01a-C3.

## 10. Interaction with repo rules

- **Metering and the cost rule.**
  - Every request still sits under a meter. A `param` split or a group is
    still covered by its call's meter, and only C5 multiplies rows, one per
    group.
  - No row shape changes, so `usage_rollup.VERSION` does not move.
  - Voyage-style `usage.total_tokens` is not read as `prompt_tokens`. A row
    that reported neither is estimated (`tokens_estimated`), and is never
    written as 0.
- **Privacy.**
  - Capture lines and error rows still carry counts, kinds and statuses only.
  - The space digest keeps prefix text out of logs.
  - The eval corpus is invented, uses placeholder names, and is recorded as
    ids.
- **Confirm before spend.** Section 4.1. The server refuses, inside the hold
  that writes. Nothing in the client is relied on.
- **Guards.**
  - `test_operation_guard.py` (embed half): task literals and `space=`
    tracing apply to `embed` and `embed_groups_sync`.
  - The client rule allows only `store/inference/embed.py` and the probe to
    call either client. `test_usage_guard.py` requires `usage=` on the async
    client's `.embed` too.
  - The import guard needs every import at module scope and no cycle: `wire`
    stays a leaf, and `embeddings` imports it.
  - Facts writes already go through `atomic`.
- **Android and pydantic v1.**
  - `FactsUpdate` gains `embedding: Any = None` (a plain field, checked by the
    store).
  - `httpx.AsyncClient` is already a base dependency of the chat clients.
  - No new dependency is added. In particular there is no numpy: the eval
    computes cosines in pure Python.
- **Detached runs.** The opener stays a `draft`-class run, and only where its
  compose runs changes. The async client is per app. A cancel of a detached
  run that is mid-embed files `aborted`.
- **CLAUDE.md.** The embedding paragraph ("There is one door, `embed_sync`")
  is updated in the same PR. It should list three doors (`embed_sync`,
  `embed`, `embed_groups_sync`) in one module, the loop guard, and options as
  stated facts in the space. `facts_moved`'s docstring is corrected.

## 11. Tests and acceptance

- **Golden identity.**
  - `space_of(conn, m)` and `space_of(conn, m, EmbedOptions())` equal
    `"id\0rev\0m"`.
  - `vectors._path` for a fixed text is pinned to today's digest.
  - Canonically empty options (empty prefixes, a lone `dimensions_field`)
    are default.
  - Representative option sets pin their `embopt1` ids.
- **No re-embed on upgrade.** A store whose vectors were written by today's
  code, with no facts block, reads every vector after the change (load hits,
  no request). The continuity basis for a fixed campaign is byte-identical.
- **Request bodies** (MockTransport, both clients):
  - `none` gives exactly `{"model","input"}`.
  - `prefix` gives prefixed inputs, one request.
  - `param` gives the query request then the document request, one meter
    row, and vectors in input order.
  - `dimensions` adds the field. A wrong width is `dimensions_mismatch`, with
    an error row carrying kind and status only.
- **Facts.**
  - Validation refusals are 400 and write nothing.
  - A mangled block reads as default and is flagged.
  - An options write on the role's model without confirm is 400
    `confirm_embedding`, and with confirm it writes.
  - An options write on another model asks nothing.
  - Moving back to none still asks.
  - A format-1 store answers 409 `not_migrated`.
- **Loop guard.**
  - `embed_sync` called inside `asyncio.run` raises `on_loop`, files no
    usage row, and writes one error log row.
  - From `run_in_threadpool` it is fine.
  - An opener with recall and art configured completes with recall applied
    and no `on_loop` row (the fix is live).
- **Async door.**
  - Parity suite: the same cases run through `embed_sync` and `embed`, with
    the same rows and lines.
  - A cancel files `aborted`.
  - A drip-fed header is cut at the deadline (the sync client's documented
    hole, closed).
  - The client is closed at lifespan exit.
- **Attribution.**
  - `attribute` grouping: a shared text goes unattributed, and order is
    deterministic.
  - One row per group, with the right `campaign`.
  - `bad_response` in group 2 still runs group 3.
  - `rate_limit` in group 2 leaves group 3 `not_sent` with no row.
  - No request body mixes groups.
- **Evals.** Replay grades recorded rankings. The MockTransport case passes
  under `pytest backend`. The graders flag a planted ranking that misses.
- **Probe.** The model test of a model with options sends them and returns
  `dims`.
- **Frontend.**
  - `ModelFactsPanel` shows the options section only for an embedding-capable
    model.
  - Suggest fills and does not save.
  - Save, a 400, the confirm and the resend.
  - The Models Embedding row shows the options line.

**Acceptance:** `make check` passes, and an upgraded store issues no
embedding request it would not have issued before.

## 12. Non-goals

- Cohere `/v2/embed`, Gemini `embedContent` and any other non-OpenAI-shaped
  API.
- Local Matryoshka truncation.
- More input types than query and document (Cohere's `classification` or
  `clustering`).
- Per-campaign or per-task embedding spaces: the role stays global (01 section 7.3).
- Making the context builder async.
- Pruning stray vectors.
- Caching query vectors on any current path.
- A CI gate on live eval quality.

## 13. Open questions

1. **Where options live.** As stated model facts (recommended: the same scope
   as a space, and they survive `rev`), or as Embedding-role keys in
   `config.md` (simpler UI, but `dimensions` would then outlive a model
   change it no longer fits). *Recommend facts.*
2. **A text claimed by several campaigns (C5).** Unattributed (recommended),
   charged to the first campaign, or apportioned. Apportioning invents
   per-campaign figures nobody reported. *Recommend unattributed.*
3. **Loop-guard behaviour.** Degrade with an error row (recommended), or raise
   a hard error that fails the turn. *Recommend degrade.* A hard error turns
   a latency bug into a lost turn.
4. **Native async client now, or keep `to_thread`.** The native client closes
   a documented hole and makes cancel real. It has no caller until 09.
   *Recommend native now*, because 09's plan should not also carry a
   transport rewrite.
5. **Suggest by model id.** Suggest only, never auto-apply (recommended). A
   shipped table that applied itself would move spaces on upgrade.
6. **Generic field versus per-provider presets for `param`.** *Recommend
   generic*, with suggestions. Provider field names are unverified for half
   the table (3.5), and a preset that guessed wrong would need a release to
   fix.
7. **03 note, not a change to 03.** Space ids already contain NUL bytes, and
   03's `materialized.kind` is `vector:<space>`. 03 may prefer
   `vector:<sha256(space)>` so its SQLite column holds no NUL. Recommend 03
   decide this in its plan. Nothing here depends on it.
