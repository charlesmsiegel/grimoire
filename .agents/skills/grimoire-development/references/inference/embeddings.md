# Embedding calls and vector spaces

Source: [embed.py](../../../../../backend/src/grimoire/store/inference/embed.py), [embed_space.py](../../../../../backend/src/grimoire/store/embed_space.py).

- **Adding an embedding call site?** The doors are in one module,
  `store/inference/embed.py`: `embed_sync(<task>, texts,
  space=embed_space.endpoint(), client=…, campaign=…)` from a worker thread
  or a CLI, with a module's own synchronous `embeddings.EmbeddingsClient`,
  and `await embed(...)`, the same call natively async, from a coroutine,
  with the app's `embeddings.AsyncEmbeddingsClient` (`routes.get_embeddings`,
  one per app, closed by its lifespan; one deadline covers its whole call,
  and a cancel files the row `aborted`). Each refuses the other's client.
  Both take `post=` and `run_id=`, filed on the row. The third,
  `embed_groups_sync`, embeds several campaigns' documents in one run:
  `attribute(claims)` charges a text to the one campaign that claims it and
  leaves a shared or world-scoped one unattributed (counted toward no
  campaign's budget), and each group is its own `embed_sync` call -- one row
  per group, no request mixing groups, one deadline for the run, a
  `bad_response` group skipped and any other failure stopping the run with
  the rest `not_sent` and filed nowhere. The task must be in
  `routing.EMBED_TASKS` (a
  route would carry no choice: every embed task resolves through the global
  Embedding role, and the frozen observer enumerates `TASK_ROUTE`). The caller
  resolves the space **once**, from that role (`resolve.embedding`, which
  `resolve.resolve` refuses to stand in for), reads its vector cache under it
  and hands it in, because `embed_sync` never re-resolves: embedding with one
  model and saving under another space's key is how vectors from two models
  end up in one cache. No embed resolution reads a campaign; there is no
  fallback on any axis. The model-test probe is the only other door to the
  embeddings client, and it stays on the provider under test.
  - **Every call that sent a request files one ledger row**, `operation:
    "embed"`, under its embed task, covering all of its batches. It carries the
    caller's campaign where there is one (lore recall, art, continuity,
    `history-index` and `history-recall` do) because spend is measured per
    campaign, and the scene where there is one (a turn's lore/history recall
    and art, and absorb's identity
    check), because a scene's own totals are the number that is always right
    and a turn's embeds are part of what playing it cost; library-wide
    semantic search has none and is unattributed. `history-index` is scene
    documents re-embedded out of a turn: its one caller is
    `searchdocs.embedded.vectors_for`, reached only from the `searchdocs` hook
    of 05's explicit sync, so it is campaign-attributed with no scene.
    `history-recall` is the native async turn caller (`store/history/semantic.py`):
    one query-plus-warm call with the app client, post/run attribution, and
    a query-only retry only for a malformed warm response. History and lore
    share the 60-second outage memo in `embed_space`, keyed by space, which
    only a failure the provider or transport reported trips -- never the
    turn's own phase deadline cutting a slow endpoint. A
    call that sends nothing files nothing -- no row, no error, no capture line
    -- whether its input was empty or its deadline lapsed before the first
    request. One named exception: **never on the app's event loop**.
    `runner.install` marks the lifespan's loop and the lifespan's exit
    unmarks it, and `embed_sync` called while that is the running loop is
    refused before any meter as `network`/`on_loop`, so the caller degrades
    as it does for an unreachable endpoint -- and it writes ONE error row (the
    task, the kind, the code, the campaign and scene it was handed, and the
    caller's innermost frames as `file:line in function`, never its text),
    because it records a programming error rather than a call and the frames
    are what find the caller. A worker, a CLI, a thread calling through a portal, the `embed`
    door and a private `asyncio.run` loop are never refused; an `async def`
    caller reaches a compose or an embed through `run_in_threadpool`, as the
    greeting opener does. When the caller's own budget cuts a request that did go out
    (`budgeted=True`: absorb's identity check, the continuity sweep and
    history recall's turn-phase deadline), the
    row is `aborted` and nothing reaches the error store: that is the
    caller's clock, not the provider failing. A deadline that only bounds the
    provider (recall's, search's) cutting it is an error like any other.
  - **A failure is recorded at `Meter.done` like any LLM call, and only its
    kind and HTTP status are recorded** -- never the provider's text, a URL or
    a key, since an error body can echo the input and a redirect's `Location`
    can carry a key. The caller still sees the provider's own words and still
    degrades silently, writing no line of its own. So a failed continuity
    sweep legitimately files **two** error entries: a `continuity-similarity`
    row for the failed request and reconcile's own `continuity-reconcile` row
    for the degraded sweep; a failed absorb identity check likewise files a
    `continuity-similarity` row and a `continuity-identity` record. They answer
    different questions, and a test that counts one filters on `task`. (A
    request the run's own budget cut is not a failed request: its row is
    `aborted`, and only the caller says what running out meant.)
  - **A known `no` turns embedding off rather than refusing**: the adapter, a
    preset's hard `no`, the user's facts or the catalog (never the name rule's
    guess, and never a failed test, which is `unknown`) mean no request is
    sent and every caller degrades as it does when the role is unset. The
    Embedding card's `problem` says why. An edit of a provider known not to
    embed by its adapter, its preset or the user's override asks for no
    `confirm_embedding`; a `no` that only the catalog or the old rev's probe
    verdicts gave does not survive a `rev` restamp, so `moved_by` judges the
    edited provider as it will read after the save, and an edit that leaves
    embedding on is a move that asks.
  - **Cost:** an endpoint that reports no price (a local or
    OpenAI-compatible server) files an **unpriced** row on every recall or art
    turn, so the Costs totals read "incomplete" until the user sets rates for
    that model. An embedding generates nothing, so its row carries a prompt
    count and no completion count, and `usage._completion_count` reads that
    absence as zero output: a rate models the row into `modelled_usd`, and the
    Costs card offers rates rather than saying nobody counted. An embed row
    with no prompt count is still unmetered. An OpenRouter embed reports its
    cost, which is real spend and counts against the campaign's budget.
  - **Options are a stated fact, and part of the space** (01h). A model's
    embedding options live in its provider's facts (`embedding`: `input`
    `none`, `prefix` with the query and document prefixes, or `param` with
    a request field and its two values; and a requested `dimensions`) and are only ever
    the user's confirmed write -- no catalog, preset or probe derives one.
    The Embedding role reads that file strictly: one it cannot read, or a
    block the validator refuses, names no space and the card says why; it is
    never read as "no options". The document side enters the space id as a
    NUL, `embopt1:` and a digest after today's string, so a model with none
    keeps its id and its cached vectors. The endpoint dict carries the
    `options` its id came from, the request is built from them, and
    `embed_sync` refuses a dict whose two disagree. A caller puts its query
    first and passes `queries=1` (recall, search and art; continuity sends
    documents only), and no caller caches a query vector. NUL is stripped
    from what is sent and from the vector key alike. A `param` call sends
    its queries and its documents as separate requests under the one meter,
    and a reply narrower or wider than a requested `dimensions` is
    `missing_key`/`dimensions_mismatch` (never retried; its error row files
    the code), never truncated here.
  - `test_operation_guard.py` (the embed half) holds the task, the door, and
    where each call's `space=` comes from -- traced back through the package
    to `embed_space.endpoint`, so a space built by hand fails it -- and
    `test_usage_guard.py` holds that every embeddings request passes a meter's
    holder.
