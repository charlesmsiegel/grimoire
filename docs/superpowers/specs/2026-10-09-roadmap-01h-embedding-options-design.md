# 01h. Embedding options, async embed, embedding evals

**Status:** Draft — spec gate (substitute review) folded in; Codex gate pending.
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
| 01a-C2 | 01a | A live embedding eval's rows are filed in the case's isolate (01a's eval scope) and harvested into the report, never reaching the library's ledger; a case resolves its embed space from the real store before entering the isolate | Hard for C6 only |
| 01a-C3 | 01a | One comparison table across embedding configurations (C1/C2 on vs off) | Soft |
| 01s (planned) | 01s | The Embedding row of the Models summary, where the options readout sits (section 4.4) | Soft |
| 01g-C3 | 01g | The `run_id` field a ledger row carries, which C5's optional `run_id` is stamped into | Soft |

C1 to C5 depend on nothing unlanded, except that C5's `run_id` has no row
field to land in until 01g-C3 does. Only C6 waits for 01a (hard).

## Required by

| Contract (provided here) | Consumer | What the consumer uses it for |
|---|---|---|
| 01h-C3 | 03 (soft) | The vector key covers the options, so 03's "vectors are keyed by space and text" (2a item 7) holds; 03's `materialized` kind is `vector:<projection>:<space-digest>` |
| 01h-C1, 01h-C3, 01h-C5 | 05 (soft) | Re-embedding hot documents as documents in the right space; `cache sync --all` embeds across campaigns in shared batches and still charges each campaign its own rows |
| 01h-C3 | 08 (hard once C1 sends a type) | 08-C2's key, `embedding_space + hash(text)`, stays the vector key with options in the space |
| 01h-C1 | 08 (soft), 09 (soft) | Embed SearchDocuments as documents and the retrieval query as a query, compared only within one space |
| 01h-C4a, 01h-C4b | 09 (hard for the turn path) | Retrieval on the turn path without blocking the event loop |
| 01h-C2 | (no named consumer) | Smaller vectors for a larger corpus, at the user's choice |
| 01h-C6 | 09 (09-C4), and every change of C1/C2 defaults | Evidence that an option, a model or a retrieval change helps |

**The consumer-facing API, exactly** (S6 of the review; 05, 08 and 09 should
match it):

- **There is no `input_type` keyword.** Documents are the default: omit
  `queries` (it defaults to 0). A query is marked by putting it first and
  passing `queries=1` (or N). `embed_groups_sync` embeds documents only.
- **The document key is `embed_space.endpoint()["space"]`**, unchanged by
  C1 landing. It moves only when a user states document-side options
  (section 5.1). Landing 01h re-embeds nothing.
- **Sync callers** (any worker thread or CLI) call `embed_sync(task, texts, *,
  space, client: embeddings.EmbeddingsClient, ...)` with their own module
  client. **Async callers** call `await embed(task, texts, *, space, client:
  embeddings.AsyncEmbeddingsClient, ...)` with the app's client from
  `routes.get_embeddings`. A sync client is never awaited and an async one
  is never handed to `embed_sync`.
- **Round trips.** In `none` and `prefix` modes a query plus a warm run is
  one request per `BATCH`. In `param` mode the query and the documents are
  separate requests (section 3.4), so "one round trip per retrieval" holds
  only outside `param` mode.

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
   closes the header-drip hole (C4b). Make blocking on the loop impossible
   to ship unnoticed (C4a).
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
  - field names match `^[a-z_][a-z0-9_]{0,40}$`. A field name is a literal
    top-level key of the request body, never a nested path, which is why `.`
    is not allowed;
  - field names are not in the reserved set `model`, `input`,
    `encoding_format`, `user`, and are not each other;
  - prefixes and values are at most 200 characters. `\n` and `\t` are
    allowed, because published query instructions use a newline (section
    3.5). Every other C0 or C1 control character, NUL included, is refused;
  - `prefix` has at least one non-empty prefix;
  - `param` has a field and both values;
  - `dimensions` is a JSON integer (a `bool` or a float such as `512.0` is
    refused, so two spellings can never be two spaces), with
    `1 <= dimensions <= embeddings.MAX_DIMS`;
  - a key the block does not define is ignored, and is not kept by a write
    from this build. A newer build that adds a key sends something this build
    does not, so the two builds compute different spaces from what each
    sends. The invariant in section 3.3 holds either way.

  A failed check is a 400 before anything is written.
- **The Embedding role reads the block strictly** (review S1).
  - An **absent** block, or an absent facts file, means default options.
  - A facts file that **exists and cannot be read** (`facts.of(...,
    strict=True)` raising `FactsUnreadableError`, for example a sync client
    holding it) means `embed_attempt` names **no space**. Embedding is off
    for that call. That is the precedent `embed_space.endpoint` already sets
    for an unreadable provider file (`embed_space.py:71-74`).
  - A block that is **present and fails validation** also names no space,
    until it is fixed.

  Reading either as default would be a silent, unconfirmed move. It would
  warm documents into the option-less space, and a continuity sweep in that
  window would persist the base space in its basis (`reconcile.py:864`) and
  re-salt every identity hash (`reconcile.py:679`), so the next sweep would
  rescore every ref. Today the space depends on the connection record alone
  (`facts._load` turns any read failure into `{}`, `facts.py:63-73`). C3
  makes it depend on a facts read, so the read must fail closed.

  The Embedding card's `problem` says "Embedding options could not be read"
  or "Embedding options are invalid". `GET .../facts` flags the block as
  `embedding_invalid`. A strict read is taken only for the Embedding
  resolution: chat targets keep `_read_facts`'s fail-soft read.
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
`EmbedOptions` object the space id was computed from.** What is sent and where
it is cached always agree, and a read that cannot be trusted embeds nothing
(section 3.2).

### 3.4 The operation and the client

`embed_sync(..., queries: int = 0)` says that the first `queries` texts are
query-side and the rest are documents. Every current call site already puts
its query first (`[query_text, *missing]`). Recall, search and art pass
`queries=1`. Recall and search also pass it on their query-only retries. Art
has no retry (`art.py:569-579`). Continuity passes `0`. A `queries` greater
than `len(texts)`, or negative, raises `ValueError` before anything is sent.

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
- **A query vector is compared only within its own space** -- the space
  the document vectors it scores against were read under, which is the
  `space` the call was handed -- and it is **never cached**: no call site
  saves one (`semantic.py:322-324`), and C1 adds no path that does. 09 relies
  on both halves.
- **`param`**: a request carries one input type. So the client splits at the
  query/document boundary as well as every `BATCH`. The body is
  `{"model", "input", <param_field>: <value>}`. A recall turn on a `param`
  space therefore costs two round trips instead of one: the query, then the
  warm run. It is still **one ledger row and one capture line**, because the
  meter covers the whole call, as it already does across batches. The extra
  round trip is stated on the Embedding card.
- **`dimensions`**: `<dimensions_field>: n` is added to every request body.
  Every returned vector must be `n` wide. If one is not, this is an endpoint
  that silently ignores unknown fields (common among OpenAI-compatible
  servers). Storing its native-width vectors under a space that claims `n`
  would be honest only until it started honouring the field.
  - The client raises `EmbeddingsError("missing_key", ...,
    code="dimensions_mismatch")`, carrying the width that came back as
    `exc.returned_dims`.
  - `missing_key` is the kind for "configured, but not usably", the redirect
    precedent (`embeddings.py:509-529`). No caller retries it: recall and
    search do not retry the query alone (`semantic.py:363-370`), and C5
    stops its run. With `bad_response`, every turn would bill a second
    request and file a second error row, forever (review S2).
  - `record_failure` files the `code` beside kind and status. A code is a
    fixed token, never provider text, so the error store and the Embedding
    card can say "this endpoint ignores `dimensions`".
- **A 4xx on a request that carried an option field stays `bad_response`.**
  The client cannot tell an option the server refuses from a document it
  refuses. So recall and search may still retry the query alone, which also
  fails. That costs at most two requests per turn, both filed. It is
  accepted, because reclassifying would stop a document's fault from being
  retried. A confirmed test call surfaces the refusal before the user relies
  on the option (section 4.4).
- **A deadline between `param`'s requests** fails the whole call. Recall's
  query-only retry then finds the deadline spent (`NOT_SENT`), so the query
  vector already paid for is lost for that turn. This is accepted and
  stated, not engineered around.

`estimate_prompt` counts what was sent, prefixes included. The capture line's
`bytes` counts the bytes sent.

**NUL never reaches a key or a request.** `embed_sync`, `embed` and
`vectors._path` remove `\0` from a text before keying and sending it. A
NUL-free text, which is every real one, keys exactly as today. A text that
contained NUL keys as its NUL-free form, which providers would have refused
anyway. Without this, the NUL-joined key `sha256(space\0text)` is not
injective: a document text that begins `embopt1:<digest>\0` would key like a
document in the options space (review M2).

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
| Qwen3-Embedding | `Instruct: Given a passage of a story, retrieve the lore, records or images it concerns\nQuery: ` (Suggest fills a concrete task sentence; `<task>` is never stored literally) | (none) |
| EmbeddingGemma | `task: search result \| query: ` | `title: none \| text: ` |

Behaviour for an unknown field is covered both ways. A server that refuses it
gets a 4xx, which is `bad_response` (section 3.4). A server that ignores
`dimensions` fails the dimensions check as `missing_key`. An ignored input
type is undetectable and harmless:
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

- **A query-side-only change is not a move**, and asks nothing (review
  S5). The space digests document-side options only (section 5.1), and a
  query vector is always embedded fresh under the current options. So
  setting a BGE-style query instruction, or editing `param`'s
  `query_value`, re-embeds nothing, and nothing is asked.
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
| Invalid block on disk | No space: embedding is off, and the card and GET say why. Nothing mixes and nothing moves |
| Facts file exists but cannot be read | No space for that call, so embedding is off and callers degrade. The base space is never used in its place |
| Endpoint ignores `dimensions` | `missing_key`/`dimensions_mismatch`, not retried. Callers degrade to keyword with one error row per call, and the code names the cause |
| Endpoint refuses the field | 4xx, `bad_response`, degrade. Recall and search may retry the query alone once per turn (section 3.4) |

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
- The test call dialog's `embed` probe sends the stated options
  (section 3.4).
  - On success it returns `dims` as today (`routes/config.py:1214`).
  - On `dimensions_mismatch` the probe is a **failed** `embed` probe, which
    is `unknown` and never `no`. Its verdict carries `requested_dims` and
    `returned_dims` from the exception (S3).
  - The panel then shows "requested 512, test returned 1536: this endpoint
    ignores `dimensions`".

## 5. Space identity (C3)

### 5.1 The encoding

```python
def space_of(conn: dict, model: str, options: EmbedOptions | None = None) -> str:
    base = f"{conn['id']}\0{conn['rev']}\0{model}"
    if options is None or options.is_default():
        return base                                   # byte-identical to today
    return f"{base}\0embopt1:{sha256(options.canonical())[:32]}"
```

`canonical()` is the **document side** only (review S5). A cached vector
depends only on what was sent for documents. A query is never cached and is
always embedded fresh under the current options. So query-side fields
(`query_prefix`, `query_value`) do not enter the space. Moving them would
re-embed every document into bytes identical to the ones already cached.

Its exact shape, which two devices must compute alike:

- It is a JSON object with sorted keys, `separators=(",", ":")` and
  `ensure_ascii=True`.
- It holds at most these keys:
  - `"doc_prefix": <document_prefix>`, present only when the mode is `prefix`
    and the document prefix is non-empty;
  - `"field": <param_field>` and `"doc_value": <document_value>`, both
    present only when the mode is `param`;
  - `"dim": <dimensions>` (an integer) and `"dim_field": <dimensions_field>`,
    both present only when `dimensions` is set.
- No other key appears, and an empty or absent value is never written.

So a `prefix` mode whose document prefix is empty (the BGE case), a stray
`dimensions_field` with no `dimensions`, and any query-side-only setting all
canonicalise to `{}`, which is the default. The identity follows what is sent
for documents and nothing else. The golden test pins this exact text for
representative sets.

- **The digest, not the JSON, goes in the space.** The space string is logged
  as the capture line's `space_id` (`embed.py:223`) and persisted in the
  continuity basis (`reconcile.py:864`). A digest keeps both short. It keeps
  user-typed prefix text out of the log, and a raw `\0` can never come from
  inside the options. 128 bits make a digest collision irrelevant. The
  NUL-joined key is injective because NUL is removed from every embedded text
  (section 3.4).
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
  id string 03 already treats as opaque. 03's `materialized` record names a
  vector `vector:<projection>:<space-digest>` (a cross-spec decision in
  `ROADMAP-CHECKLIST.md`), so the NUL bytes a space id carries never reach its
  SQLite column, and an options change is a new digest there too.

### 5.3 Document vectors only

`vectors.py` caches **document-side** vectors under the space id. Under
01h-C1 a query vector is **never cached**, and is compared only with document
vectors read under the same space it was embedded in. An earlier draft fixed
a `space + "\0query"` key for later use. It is withdrawn (review M2): a
NUL-joined side tag is not injective against document text without further
care, and no caller needs it. A later spec that wants cached query vectors
defines its key by its own contract. That key must be injective (for example
length-prefixed fields), and must include the query-side options this space
id leaves out.

## 6. Async embed (C4a, C4b)

### 6.1 Nothing embeds on the event loop (C4a)

1. **Fix the opener.** In `routes/greetings.py:98-110`, `frames()` awaits
   `run_in_threadpool` around `compose_opener` and `_record_prompt`. That
   moves its recall, its art ranking and its whole store read off the loop.
   The per-speaker loop and the frames it yields are unchanged.
2. **A runtime guard in `embed_sync`, scoped to the app's loop** (review
   S7).
   - `runner.install` (`runner.py:122-136`) already runs on the lifespan loop
     and takes `threading.get_ident()` as `loop_thread`. It also registers
     that id with `store.inference.embed.mark_app_loop(ident)`, and the
     lifespan's exit unregisters it. It is a set, because tests build an app
     per test.
   - `embed_sync` refuses only when `threading.get_ident()` is a registered
     app loop thread. This uses `runner._PortalEvent`'s own
     thread-identity test (`runner.py:98-106`), not "is any loop running".
   - Not refused:
     - a worker thread;
     - a CLI, including 05's `cache sync`;
     - a thread that calls *through* a portal;
     - a private loop such as the one `evals/runner.py:348-358` runs each
       live case in. That loop serves nothing else, so blocking it blocks no
       one.
   - A sync function `portal.call`ed onto the app loop does run on it, and
     is refused.
   - The guard runs **after** the empty-input early return. An empty call
     returns `[]` wherever it is made. It runs **before** any meter opens.
   - On the app loop, `embed_sync` raises `EmbeddingsError("network",
     "embedding refused on the event loop", code=ON_LOOP)` and writes one
     ERROR log row (task and code only). `network` is the kind every caller
     already treats as "the endpoint is unavailable, do not retry"
     (`semantic.py:363-370`). So the turn degrades to keyword rather than
     freezing every other request for up to `TIMEOUT + READ_SLICE`.
   - That error row is a **named exception** to CLAUDE.md's "a call that
     sends nothing files nothing": it records a programming error, not a
     call. The CLAUDE.md edit (section 10) says so (review M1).
   - A static guard cannot catch this. The opener reaches `embed_sync` six
     frames down, through sync code.

### 6.2 A native async door (C4b)

`embed()` stops being `to_thread`. It becomes:

```python
async def embed(task, texts, *, space, client: embeddings.AsyncEmbeddingsClient,
                deadline=None, budgeted=False, campaign="", scene="",
                cached=None, uncached=None, queries=0,
                run_id="", post=None) -> list[list[float]]
```

**`post=`** (optional, on both `embed_sync` and `embed`) is the transcript
index of the player post a turn is answering. It is filed on the ledger row
exactly as a turn's generation rows file it, so a turn's history-recall embed
buckets with the post it served (09 section 6.3). The default `None` files no
post, as every embed row does today.

It has the same validation, meter, `_stamp`, `record_failure`, NOT_SENT and
aborted rules, and capture line as `embed_sync`. The shared body moves into
helpers both doors call, so the two cannot drift.

- **`embeddings.AsyncEmbeddingsClient`** sits on `httpx.AsyncClient`. It
  shares `_Spend`, `_vectors`, `_status_kind`, the redirect refusal, the body
  bounds and the request builder from section 3.4 with the sync client. Only
  the transport loop differs. The **whole call** runs under one
  `asyncio.timeout(deadline - now)`. That closes the header-drip hole
  `embeddings.py:28-38` names, which the sync client cannot close.
- **Cancellation (C4b callers only).** A cancel mid-request runs
  `_Spend.lose()` if a batch was in flight, files the meter `aborted` and
  re-raises. The capture line says `error: "aborted"`. A cancel is never an
  error row. An `embed_sync` in a worker cannot be cancelled
  (`run_in_threadpool`, `routes/streaming.py:139`). That covers every current
  caller, and the opener once C4a moves it. Its request finishes after a run
  is cancelled, and files an ordinary row.
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
  which is the property C4a promises. `vectors.load`/`save` stay synchronous
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
                      deadline=None, budgeted=False, run_id: str = "",
                      cached: int | None = None,
                      uncached: int | None = None,
                      on_group: Callable[[EmbedGroup, GroupResult], None] | None = None
                      ) -> list[GroupResult]
# GroupResult: vectors (in the group's text order) or None, and error kind or ""
```

- **`attribute`** takes `(campaign, text)` claims. A text claimed by exactly
  one campaign goes to that campaign's group. A text claimed by several, or by
  none (world-scoped), goes to the unattributed `""` group, embedded once.
  Charging it to whichever campaign sorted first would put one campaign's
  spend in another's total. Groups are ordered with the unattributed `""`
  group **first**, then by campaign id, and texts by first claim. The result
  is deterministic. Shared texts go first because they serve several
  campaigns: a run that is cut short should not always starve them (review
  M5).
- **Groups embed documents only.** There is no `queries` keyword: a query
  belongs to a turn, which is one campaign's and uses `embed_sync`.
- **Unattributed spend counts toward no campaign's `usage.budget`.** Like
  library-wide search's rows, it appears in the global Costs totals only.
  Where 05 shows a sync's cost, it says so (review M6).
- **`embed_groups_sync`** makes one `embed_sync` call per group, under one
  shared deadline. With `deadline=None` that deadline is
  `time.monotonic() + embeddings.TIMEOUT`, taken once at entry and handed to
  every group's call. It is never a fresh `TIMEOUT` per group, which would
  make N groups N times 30s. That gives one ledger row and one capture line per group,
  and no request ever mixes campaigns. The cost is at most one extra request
  per group boundary, compared with perfect packing.
- **Failure.** `bad_response` (the input's fault) fails its group and the next
  group still runs. Any other kind (`auth`, `missing_key` including
  `dimensions_mismatch`, `rate_limit`, `network`, a deadline) stops the run. Every later group comes back with
  `error="not_sent"` and files nothing (01's "nothing sent, nothing filed").
  This is the same split `semantic._embed` makes (`semantic.py:363-370`). The
  caller saves what landed, as `similarity.embed_missing` does per chunk
  (`similarity.py:546-566`).
- **`on_group`** (optional) is called once per group, in order, as soon as that
  group's result lands and before the next group is sent. 05 saves each
  group's vectors there, so a run cut short keeps what landed without one call
  per chunk (05 section 6.6). A callback that raises stops the run: later
  groups come back `not_sent` and file nothing. The result list is returned as
  before.
- **`run_id`** (optional, default `""`). 01g asked for it: embeds made
  inside a tool loop are attributed to that loop's run. `embed_sync` and
  `embed` take the same keyword. When it is set, each row the call files
  carries it in 01g-C3's `run_id` ledger field, beside the campaign and scene;
  when it is empty the row is today's. It attributes and never resolves
  anything: the space and the client are still handed in. Until 01g-C3 adds
  the field to `usage.meter`, the keyword is accepted and not filed.
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
  - Each embed case **resolves its space before entering the isolate**
    (01a section 4): the Embedding role is read from the real store, as
    `run_live` does for a chat model (`evals/run.py:72-112`), and the
    resulting endpoint dict -- options included -- is handed into the
    case. Inside `temp_home()` nothing re-resolves; the throwaway store has
    no providers to resolve from. The synthetic corpus's vectors therefore
    never land in the user's cache.
  - Before sending, it prints the corpus size in bytes and the estimated
    tokens.
  - `--embed-options <json>` (repeatable) runs the same corpus under each
    option set in memory, without writing facts. That is the C1/C2 A/B.
    - Each set is validated as a facts write would be.
    - Its endpoint dict's `space` is recomputed with `space_of(conn, model,
      opts)`, so capture lines and rows of different sets never share a
      `space_id` (review M7).
    - Recording files are named by model and option digest
      (`<model-slug>-<digest or "none">.json`), never by connection id.
  - Live cases call `embed_sync` outside any coroutine, or `await embed(...)`.
    The C4a guard refuses only the app's loop thread, so the eval's private
    `asyncio.run` loop is not refused. The rule still keeps the harness from
    looking like a provider outage if the guard is ever widened.
  - Rows are filed in the case's isolate, which is 01a-C2's eval scope, and
    harvested into the report before it is deleted; they never reach the
    library's ledger. Each carries the real embed task of its shape, so
    01a-C1's per-task report reads naturally.
  - The output is 01a-C1's per-run wall time, prompt tokens and three money
    columns, and 01a-C3's comparison table across option sets.
  - `--record` saves rankings.
- **What it cannot show:** whether a real library retrieves well. Quality on a
  synthetic corpus is evidence for a choice, and the default thresholds stay
  "tune against the inspector" (`semantic.py:66-68`).

## Slices

Landing order within this spec: S1 → S2 → S3 → S4 → S5 → S6 → S7. S1 and S6
can go in parallel with anything; S4, S5 and S7 can go in parallel once S3 has
landed.

Every slice leaves today's behaviour byte-identical for a store with no
`embedding` facts block: options default to none, so no space id, request
body or vector key moves until a user states options.

### 01h-S1: No embedding on the event loop

- **Delivers:** 01h-C4a (full)
- **Needs (this spec):** none
- **Needs (other specs):** none
- **Scope:** The opener's `frames()` (`routes/greetings.py:98-110`) awaits
  `run_in_threadpool` around `compose_opener` and `_record_prompt`.
  `runner.install` registers the lifespan loop thread with
  `store.inference.embed.mark_app_loop`, and the lifespan exit unregisters
  it. `embed_sync` refuses on a registered loop thread as `network`/`on_loop`,
  after the empty-input return and before any meter, and writes one ERROR log
  row. CLAUDE.md's embedding paragraph names the error row as an exception
  to "a call that sends nothing files nothing". The fix and the guard land
  together, because the guard alone would degrade every opener's recall.
- **Acceptance:** section 11, Loop guard:
  - `on_loop` on a registered thread, with no usage row and one error row;
  - fine from a worker, a CLI and a private `asyncio.run` loop;
  - `[]` for empty input on the loop;
  - an opener with recall and art on completes with recall applied and no
    `on_loop` row.
- **Size:** S

### 01h-S2: Options in the space identity, the `queries` split and prefix mode

- **Delivers:** 01h-C3 (full); 01h-C1 (part: the `queries` keyword on
  `embed_sync`, modes `none` and `prefix`, and the rule that a query vector
  is never cached)
- **Needs (this spec):** none
- **Needs (other specs):** none
- **Scope:**
  - `wire.EmbedOptions` with the whole document-side `canonical()` of
    section 5.1, every key including `dim`, `field` and `doc_value`, so no
    later slice moves an id.
  - The `embedding` facts block: `facts.state(..., embedding=)`,
    `_check_embedding`, and `FactsUpdate.embedding`.
  - The strict read in `embed_attempt`. An unreadable file or an invalid
    block names no space, and the Embedding card's `problem` says why.
  - `wire.Target.embed_options`, `space_of(conn, model, options)` and
    `endpoint_of`'s `options`.
  - NUL removed from embedded text in `embed_sync` and `vectors._path`.
  - The `queries` keyword and its `ValueError`, with recall, search and art
    passing `queries=1`.
  - The client's `none` and `prefix` request building, and the probe sending
    the stated options.
  - `facts_moved` and `EMBEDDING_FACTS_CONFIRM` reworded for a move, so an
    options write on the role's model is confirmed.
  - **Interim rule (a slicing gap, noted in the review record):** in this
    slice `_check_embedding` refuses `input: "param"` and `dimensions` with a
    400 ("not supported by this build"), and a block on disk that uses them
    is invalid, so it names no space. S3 widens the check.
- **Acceptance:** section 11:
  - Golden identity: today's string for default options, today's
    `vectors._path` digest, pinned canonical JSON and `embopt1` ids, and
    canonically empty sets.
  - No re-embed on upgrade, and a byte-identical continuity basis.
  - Request bodies for `none` and `prefix`.
  - Facts: validation refusals, an invalid block and an unreadable file
    naming no space, `\n` in a prefix, confirm on the role's model, and no
    question for another model or a query-side-only change.
  - A NUL text keys as its NUL-free form.
- **Size:** L

### 01h-S3: Request-field input type and requested dimensions

- **Delivers:** 01h-C1 (full: adds `param`); 01h-C2 (full)
- **Needs (this spec):** 01h-S2 (H)
- **Needs (other specs):** none
- **Scope:**
  - `_check_embedding` accepts `param` and `dimensions` (the integer-only
    rule).
  - The client splits requests at the query/document boundary for `param`,
    under one meter.
  - The client adds `dimensions_field`, checks every returned width, and
    raises `missing_key`/`dimensions_mismatch` with `returned_dims`.
  - `record_failure` files the error `code`.
  - The probe reports a mismatch as a failed `embed` probe with both widths.
  - No space id moves: S2 already canonicalises these keys.
- **Acceptance:** section 11:
  - Request bodies for `param` (two requests, one row, input order) and
    `dimensions`.
  - A wrong width is `dimensions_mismatch`, and the row carries only kind,
    status and code.
  - Recall against a width-ignoring endpoint sends one request per turn.
  - `512.0` and `true` are refused.
  - The probe carries both widths.
- **Size:** M

### 01h-S4: Options in the UI

- **Delivers:** none in full. It is the UI half of 01h-C1 and 01h-C2, both
  already delivered server-side, and section 4.4 in full.
- **Needs (this spec):** 01h-S3 (H)
- **Needs (other specs):** 01s (S: the Models summary's Embedding row,
  3.1). Until 01s lands, the options line is added to today's
  `EmbeddingSummary` in `ModelsView.tsx`.
- **Scope:**
  - `ModelFactsPanel` gains the "Embedding options" section (input mode,
    fields, dimensions) for a model whose `embed` capability is not a known
    `no`.
  - **Suggest** fills from section 3.5's prefix table, with a concrete task
    sentence for Qwen-style prefixes. It fills the form only.
  - Save, the 400 `confirm_embedding`, and "Re-embed and save".
  - `EmbeddingCard.options` on the server, and the Models Embedding row's
    options line, with "two requests per recall" for `param`.
  - The panel's "requested N, test returned M" line.
- **Acceptance:** section 11, Frontend:
  - the options section only for an embedding-capable model;
  - Suggest fills and does not save;
  - save, the 400, the confirm and the resend;
  - the Models row's options line.
- **Size:** M

### 01h-S5: Native async embed

- **Delivers:** 01h-C4b (full)
- **Needs (this spec):** 01h-S3 (H: the shared request builder with every
  mode)
- **Needs (other specs):** 01g-C3 (S: the `run_id` field on a ledger row.
  Until it lands, `run_id` is accepted and not filed)
- **Scope:**
  - `embeddings.AsyncEmbeddingsClient` on `httpx.AsyncClient`, sharing
    `_Spend`, `_vectors`, `_status_kind`, the redirect refusal, the bounds
    and the request builder.
  - One `asyncio.timeout` covers the whole call.
  - `embed()` is rewritten natively, with `queries`, `run_id` and `post=`,
    on shared helpers with `embed_sync`. `embed_sync` gains `post=` too.
  - A cancel files `aborted`.
  - One client per app, built in the lifespan and closed at shutdown, and
    reached through `routes.get_embeddings`.
  - `test_usage_guard.py` requires `usage=` on the async client.
  - The test-operation guard covers `embed`.
  - There are no production callers yet (09 and 10 are the consumers).
- **Acceptance:** section 11, Async door:
  - the parity suite (same rows and lines from both doors);
  - a cancel files `aborted`;
  - a drip-fed header is cut at the deadline;
  - the client is closed at lifespan exit;
  - `post=` is filed on the row.
- **Size:** M

### 01h-S6: Cross-campaign attribution

- **Delivers:** 01h-C5 (full)
- **Needs (this spec):** 01h-S3 (S: `dimensions_mismatch` as `missing_key`
  among the kinds that stop a run. Before S3 no options can be sent, so the
  kind never arises)
- **Needs (other specs):** 01g-C3 (S: the `run_id` field on a ledger row.
  Until it lands, `run_id` is accepted and not filed)
- **Scope:**
  - `EmbedGroup`, `attribute(claims)` (shared texts go unattributed, the
    `""` group first) and `embed_groups_sync`.
  - One `embed_sync` per group under one deadline taken at entry.
  - A `bad_response` group continues, and any other kind stops the run with
    the rest `not_sent`.
  - `on_group` is called per group, and a raise stops the run.
  - `cached`/`uncached` go to the first call only.
  - The `run_id` keyword on `embed_sync` (and on `embed`, if S5 has not
    landed it already).
  - The test-operation guard learns the door.
  - There are no production callers yet (05 is the consumer), so
    `MIN_EMBED_CALLS` is unchanged.
- **Acceptance:** section 11, Attribution:
  - grouping and order;
  - one row per group with the right campaign;
  - `bad_response` continues and `rate_limit` stops with no row after;
  - one deadline for all groups;
  - the `""` group first;
  - no mixed request body;
  - `on_group` order, and a raise in it stopping the run.
- **Size:** M

### 01h-S7: Embedding evals

- **Delivers:** 01h-C6 (full)
- **Needs (this spec):** 01h-S3 (H: option sets for `--embed-options`)
- **Needs (other specs):**
  - 01a-C1 (H: per-case and per-call wall time, tokens and the three money
    columns);
  - 01a-C2 (H: live evals metered by the production meter inside a throwaway
    home, rows copied to the run file stamped `scope: "eval"`, with the
    space resolved before the isolate);
  - 01a-C3 (S: `--out` and offline `--compare` across runs. Until it lands,
    each option set prints its own table).
- **Scope:**
  - `evals/embed/` corpus with the three shapes, placeholder names,
    paraphrase positives and lexical decoys.
  - Recall@k (1, 3, 5, 10) and MRR, against the lexical baseline.
  - Offline replay of recorded rankings, and the offline MockTransport case,
    both under `pytest backend`.
  - `--live --embed`: the space resolved before the isolate, `embed_sync`
    called outside any coroutine, and a byte and token estimate printed
    before sending.
  - `--embed-options`, which recomputes the space.
  - `--record`, with files named by model and option digest.
- **Acceptance:** section 11, Evals:
  - replay grades recorded rankings;
  - the MockTransport case passes under `pytest backend`;
  - a planted miss is flagged.
  - Under a live run, no row reaches the real ledger (01a's tripwire).
- **Size:** M

## 9. Contract

- **01h-C1, input type.**
  - `embed_sync`/`embed(..., queries: int = 0)` mark the first `queries`
    texts as queries. `queries` outside `0..len(texts)` is a `ValueError`.
    `embed_groups_sync` embeds documents only.
  - Documents need no keyword, and there is no `input_type` keyword. The
    document key is `embed_space.endpoint()["space"]`.
  - With the space's stated `input` mode, the request carries the type as a
    prefix (any batch) or as `param_field` (one type per request, under one
    meter).
  - `none` sends today's exact body.
  - A query vector is compared only within the same space as the document
    vectors it is scored against, and is never cached.
  - No guarantee is made about vector quality.
- **01h-C2, dimensions.**
  - A stated `dimensions` is sent as `dimensions_field`.
  - Every returned vector must be that wide, or the call fails
    `missing_key`/`dimensions_mismatch`. That is never retried, the caller
    degrades, and the error carries `returned_dims`.
  - There is no local truncation.
- **01h-C3, identity.**
  - `space_of(conn, model, options)` is today's string when the
    document-side canonical form (section 5.1) is `{}`, and `base +
    "\0embopt1:" + digest(canonical)` otherwise.
  - Options come only from stated facts. The role reads them strictly: an
    unreadable facts file or an invalid block names no space.
  - NUL is removed from embedded text.
  - The request is built from the object the space was computed from.
  - Vectors are keyed `sha256(space\0text)`, independent of `BUILD`.
  - A change of the role's document-side options is a confirmed move. A
    query-side-only change moves nothing and asks nothing.
- **01h-C4a, no embedding on the event loop.**
  - The opener's compose runs in `run_in_threadpool`.
  - Nothing calls `embed_sync` on the app's lifespan loop thread. The
    runtime guard refuses it there (registered by `runner.install`) as
    `network`/`on_loop`, so the caller degrades, and logs one error row.
    Private loops and every other thread are not refused.
- **01h-C4b, native async embed.**
  - `await embed(...)` is native async: one total deadline, cancellable (a
    cancel files `aborted`), metered and captured as `embed_sync` is, with
    the same keywords including `queries` and `run_id`.
  - Async callers get the client from `routes.get_embeddings`.
- **01h-C5, attribution.**
  - `attribute(claims)` plus `embed_groups_sync(groups)` give one row per
    group, and no request spans groups. One deadline covers the run, and the
    unattributed group goes first.
  - A text shared by several campaigns is unattributed.
  - An endpoint-wide failure stops the run, and the rest is `not_sent` and
    files nothing.
  - An optional `run_id` (also on `embed_sync`/`embed`) is filed on every
    row in 01g-C3's field, so embeds inside a tool loop belong to its run.
  - Synchronous, so 05's CLI can call it outside a loop.
- **01h-C6, evals.**
  - `evals/run.py --embed`: recall@k (1/3/5/10) and MRR per shape, against a
    lexical baseline.
  - Replay of recorded rankings runs offline in `make check`.
  - A live case resolves its embed space from the real store before entering
    the isolate, and its rows stay in the isolate (01a-C2).
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
  compose runs changes. The async client is per app. Only a C4b caller's
  embed is cancelled mid-request (and files `aborted`). The opener's compose,
  once in a worker, completes its embed after a cancel and files an ordinary
  row.
- **CLAUDE.md.** The embedding paragraph ("There is one door, `embed_sync`")
  is updated in the same PR. It should list three doors (`embed_sync`,
  `embed`, `embed_groups_sync`) in one module, the loop guard and its named
  exception to "a call that sends nothing files nothing", and options as
  stated facts in the space. `facts_moved`'s docstring is corrected.
- **`docs/store-guarantees.md`** gains a note (review M8). An older build
  sharing the store ignores the `embedding` block and computes the base
  space. Devices on both builds that take turns running continuity sweeps
  therefore flip the basis each time, and every ref rescores. This is
  harmless to correctness, because each build's requests match its own
  space, but it costs work until every device is upgraded.

## 11. Tests and acceptance

- **Golden identity.**
  - `space_of(conn, m)` and `space_of(conn, m, EmbedOptions())` equal
    `"id\0rev\0m"`.
  - `vectors._path` for a fixed text is pinned to today's digest.
  - Canonically empty options (empty prefixes, a lone `dimensions_field`,
    a query-only prefix) are default.
  - The exact canonical JSON text is pinned for representative sets.
  - `dimensions` as `512.0` or `true` is refused.
  - A text containing NUL keys as its NUL-free form.
  - Representative option sets pin their `embopt1` ids.
- **No re-embed on upgrade.** A store whose vectors were written by today's
  code, with no facts block, reads every vector after the change (load hits,
  no request). The continuity basis for a fixed campaign is byte-identical.
- **Request bodies** (MockTransport, both clients):
  - `none` gives exactly `{"model","input"}`.
  - `prefix` gives prefixed inputs, one request.
  - `param` gives the query request then the document request, one meter
    row, and vectors in input order.
  - `dimensions` adds the field. A wrong width is
    `missing_key`/`dimensions_mismatch` with `returned_dims`, and the error
    row carries kind, status and code only.
  - Recall with a width-ignoring endpoint sends **one** request per turn,
    with no query-only retry.
- **Facts.**
  - Validation refusals are 400 and write nothing.
  - An invalid block names no space. The card says why, and no request is
    sent.
  - An unreadable facts file (a held file) with options stated sends no
    request, and leaves the continuity basis space unchanged.
  - A Qwen-style prefix with `\n` saves.
  - A query-side-only change asks nothing.
  - An options write on the role's model without confirm is 400
    `confirm_embedding`, and with confirm it writes.
  - An options write on another model asks nothing.
  - Moving back to none still asks.
  - A format-1 store answers 409 `not_migrated`.
- **Loop guard.**
  - `embed_sync` called on a registered app loop thread raises `on_loop`,
    files no usage row, and writes one error log row.
  - From `run_in_threadpool`, a CLI and a private `asyncio.run` loop, it is
    fine.
  - An empty input returns `[]` even on the app loop.
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
  - `deadline=None` gives one deadline for all groups.
  - The `""` group is sent first.
  - No request body mixes groups.
- **Evals.** Replay grades recorded rankings. The MockTransport case passes
  under `pytest backend`. The graders flag a planted ranking that misses.
- **Probe.** The model test of a model with options sends them and returns
  `dims`. A mismatch is a failed probe (`unknown`) carrying both widths.
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
   a latency bug into a lost turn. It is scoped to the app's loop thread
   either way.
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
7. **03's vector kind (resolved).** The cross-spec decision in
   `ROADMAP-CHECKLIST.md` makes 03's `materialized` vector kind
   `vector:<projection>:<space-digest>`, so the NUL bytes in a space id never
   reach 03's column. Nothing is left to decide here.

## 14. Review record

**Substitute spec gate (adversarial review, 0 blocking, 8 should-fix, 10
minor).** Every finding was checked against the code.

Changed:

- **S1.** The Embedding role reads its options strictly. An unreadable facts
  file or an invalid block names no space, instead of silently reading as
  default (3.2, 4.3).
- **S2.** `dimensions_mismatch` is `missing_key` (the redirect precedent).
  It is not retried, C5 stops on it, and its code is recorded. A 4xx on an
  option field stays `bad_response`, with the cost stated (3.4).
- **S3.** The mismatch carries `returned_dims`. The probe reports a failed
  `embed` probe (`unknown`) with both widths (4.4).
- **S4.** `\n` and `\t` are allowed in prefixes. The Qwen suggestion fills
  a concrete task sentence (3.2, 3.5).
- **S5.** The space digests document-side options only, so a query-side
  change neither moves the space nor asks (4.1, 5.1).
- **S6.** The consumer-facing API is stated exactly under Required by. The
  mismatches in 05, 08 and 09 are left to the coordinator.
- **S7.** The guard is scoped to the app's loop thread
  (`runner.install`), the portal wording is corrected, and the eval
  harness's rule is stated (6.1, 8).
- **S8.** Cancellation is scoped to C4b callers (6.2, 10).
- **M1.** The guard runs after the empty-input return. Its error row is a
  named exception that the CLAUDE.md edit records.
- **M2.** NUL is removed from embedded text, and `cache_space` is
  withdrawn.
- **M3.** Validation now pins an integer `dimensions`, literal field names
  with no `.`, unknown keys ignored, and the exact canonical JSON.
- **M4.** The signatures now carry `run_id`, `queries`, `cached` and
  `uncached` where the contract says. Groups embed documents only, and a
  `queries` outside range is a `ValueError`.
- **M5.** One deadline covers a C5 run, and the unattributed group goes
  first.
- **M6.** Unattributed spend is stated to count toward no campaign's budget.
- **M7.** `--embed-options` recomputes the space, and recordings are named
  by model and option digest.
- **M8.** A note for `docs/store-guarantees.md` about mixed builds.
- **M9.** The `param` mid-call deadline loss is stated.
- **M10.** Art has no query-only retry.

Rejected: none.

Slices added (7 slices). Slicing revealed one gap, now fixed in 01h-S2: until
01h-S3 lands, the facts validator refuses `param` and `dimensions`, and a
block on disk that uses them is invalid and names no space. The canonical
encoding is complete from S2, so no space id moves when S3 lands.
