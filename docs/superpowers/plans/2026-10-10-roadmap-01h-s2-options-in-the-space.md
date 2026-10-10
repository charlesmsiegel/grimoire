# 01h-S2: Options in the space identity, the `queries` split and prefix mode — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a user can state embedding options for one model on one provider
(an `embedding` block in its model facts). The document-side options enter the
Embedding role's vector space id as `\0embopt1:<digest>`, and an option-less
model keeps today's space id byte for byte. The role reads the block strictly:
an unreadable facts file or an invalid block names no space. `embed_sync`
takes `queries=N` (the first N texts are queries). The client builds `none`
and `prefix` requests from the same `EmbedOptions` object the space was
computed from. NUL never reaches a key or a request. A write of the role's
document-side options asks `confirm_embedding` first, and a query-side-only
write asks nothing. Delivers 01h-C3 in full and 01h-C1 in part (the `queries`
keyword, modes `none` and `prefix`, and the rule that a query vector is never
cached).

**Architecture:**

- **One value.** `wire.EmbedOptions`, a frozen stdlib-only dataclass with
  `canonical()` (the document side only, every key of spec §5.1 including
  `dim`, `field` and `doc_value`), `is_default()` and `digest()`.
- **Where options live.** `facts.py` stores them as a stated fact,
  `"embedding": {...}`, checked and normalised by `_check_embedding`. The
  reader is `facts.embed_options(model_facts)`: an absent block is None, and
  an invalid block raises `ValueError`.
- **The resolver reads them.** `resolve._target` reads them fail-soft into the
  new `wire.Target.embed_options`, for the probe and for display.
  `resolve.embed_attempt` reads the facts file **strictly** (one read) and
  computes `space_of(raw, model, options)` from the same object it puts on
  the attempt's target. An unreadable file or an invalid block gives no space
  and an `options_problem`. That problem reaches
  `ResolvedInference.embed_options_problem` and then the Embedding card.
- **The endpoint dict.** `embed_space.endpoint_of` adds
  `"options": target.embed_options or wire.EmbedOptions()`.
- **The operation.** `embed_sync` reads `space["options"]` and refuses one
  that does not agree with `space["space"]`. It removes NUL from each text,
  validates `queries` and hands `options` and `queries` to the client. It
  counts and captures the inputs as sent (`embeddings.prepare_inputs`).
- **The client.** It builds bodies with `embeddings.request_body`. It refuses
  `param` and `dimensions` (S3's) with `ValueError` before anything is sent.
- **The key.** `vectors._path` removes NUL before hashing.
- **Confirmation.** The existing `facts_moved` guard now sees an options
  change as a space change, with no new logic.

**Tech Stack:** Python 3.11+ (`dataclasses`, `hashlib`, `json`, `re`),
httpx `MockTransport`, pytest, the shared fakes (`FakeEmbeddings`,
`FakeOpenRouter`).

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01h-embedding-options-design.md`:
- "Required by" (the consumer-facing API) and §1 (current state);
- §3.1 to §3.4 (`none` and `prefix` only), §3.5 (the prefix table, used in
  tests only);
- §4.1 to §4.3, §5.1 to §5.3;
- §9 (01h-C1 in part, 01h-C3), §10 (privacy, guards, Android/pydantic,
  CLAUDE.md, `docs/store-guarantees.md`);
- §11 (Golden identity, No re-embed, Request bodies for `none`/`prefix`,
  Facts, the Probe's stated-options half);
- Slices (01h-S2, including its interim rule).

**Open questions (spec §13), none marked decided, so each takes the spec's
recommendation:**
- **Q1, where options live:** as stated model facts.
- **Q5, suggest by model id:** suggestions are UI-only and never applied
  (S4). This slice derives nothing: no catalog row, preset, name rule or
  probe verdict sets options, so no shipped table can move a space on
  upgrade.
- **Q6, generic field versus presets:** a generic `param_field` /
  `query_value` / `document_value`. The dataclass and `canonical()` carry
  them now, so S3 moves no id. The validator still refuses `param` in this
  slice (the interim rule).
- Q2, Q3, Q4 and Q7 are not this slice's.

## Current state (read against `wt/01h-s2` at `b3204cc`, the integration tip with the final 01h-S1)

- `wire.py`: a stdlib-only leaf (`test_wire.test_wire_imports_nothing_from_the_package`).
  `Target` has no embedding field.
- `store/inference/facts.py`:
  - `_view(entry, rev)` is the one shape (`vision`, `prefill`,
    `post_process`, `rates`, `verified`, `overrides`, `context_window`,
    `max_output`). `test_inference_facts.py` pins it exactly in three tests
    (`test_an_unknown_model_has_the_empty_shape`, the mangled-file case at
    :155-163 and `test_mangled_fields_inside_a_model_read_as_empty`).
  - `of(..., strict=True)` reads through `_load_for_write`, raising
    `FactsUnreadableError` / `FactsMangledError`.
  - `adopted(...)` (the format-1 planner's read) uses the fail-soft `_load`.
  - `state(...)` returns early when nothing is stated, and its nested
    `change(entry)` applies every field.
- `store/inference/resolve.py`:
  - `space_of(conn, model)` is `f"{id}\0{rev}\0{model}"`.
  - `embed_attempt(provider_id, model, raw, *, catalog, model_facts, stated)`
    builds the attempt through `_attempt`, which reads facts fail-soft
    through `_read_facts` → `_model_facts` / `facts.adopted`, unless
    `model_facts` is handed in (`facts_moved` hands `before`/`after`).
  - `space_id` is set when there is a model, an endpoint and nothing
    `missing`.
  - `embedding()` is the role's one reader. `_target` builds every target
    from `model_facts`.
- `ResolvedInference.space_id` (`resolved.py`) has no sibling saying why no
  space was named.
- `store/embed_space.py`:
  - `endpoint_of` returns `{model, base_url, key, space, provider,
    provider_name, provider_kind, target}`.
  - `resolution()` swallows `OSError` (so a `FactsUnreadableError` escaping
    `embedding()` would read as plain "off").
  - `facts_moved` compares `embed_attempt(...).space_id` before and after;
    its docstring says "Facts never move the space itself".
- `store/inference/settings._embedding_problem`:
  `embed_space.problem(cfg, embeds=False)` first, then `got.missing`'s
  "cannot make embeddings" sentence.
- `store/inference/embed.py`: `embed_sync(task, texts, *, space, client,
  deadline, budgeted, campaign, scene, cached, uncached)`. The order is: the
  task check, the empty return, `_refuse_on_loop`, the spent deadline, then
  the meter. It calls `client.embed(texts, model, key, base_url,
  deadline=..., usage=m.usage)`. `estimate_prompt` and `_capture` count
  `texts`. `embed()` is `to_thread(embed_sync, ...)`.
- `embeddings.py`:
  - `EmbeddingsClient.embed(texts, model, key, base_url, deadline=None,
    usage=None)` batches by `BATCH`.
  - `_fetch` posts `json={"model": model, "input": chunk}`.
  - It imports `llm_usage` and `llm_errors` only.
- `store/vectors.py`: `_path(space, text)` is
  `sha256(f"{space}\0{text}")`. For `space = "saltmarch-vectors\0r1\0embed-1"`
  and `text = "Mara crossed the Saltmarch at dusk"`, today's file is
  `1375582ed7a59d40cac77ce4142344db6fa3acd98385ef8961e06e9dcb82b77d.vec`
  (computed against this tree).
- Call sites:
  - `semantic._embed` sends `[query_text, *missing]`, then `[query_text]` on
    retry;
  - `semsearch` does the same;
  - `art._semantic_scores` sends `[query_text, *missing]`, with no retry;
  - `similarity.embed_missing` sends chunks of documents.
  - None passes a type.
- Continuity: `reconcile.discover` sets `sweep.space = space["space"]` and
  hashes `_identity_hash(sweep.space, text) = sha256(space + "\0" + text)`.
  With no space, `sweep.space` stays `""`, and persists as such
  (`_found_basis`).
- Routes:
  - `routes/models.FactsUpdate` has no `embedding`.
  - `put_connection_facts` passes each field to `facts.state` with the
    `_refuse_unconfirmed_facts` guard.
  - `_facts_body` spreads `facts.of(...)` and flags `unreadable`.
  - `_embed_probe` calls `_EMBEDDINGS.embed([probes.EMBED_TEXT], model, key,
    url, usage=m.usage)` and estimates `[probes.EMBED_TEXT]`.
  - `EMBEDDING_FACTS_CONFIRM` says "This turns embedding on through
    {provider}…" (no backend test pins it; the frontend test mocks its own
    text).
- Inline embeddings fakes with the old signature:
  - `tests/llm_fakes.FakeEmbeddings.embed`;
  - `test_context_semantic.py` (`FakeProvider.embed`, and the nested
    `embed` in `_poisoned`, `record` (~:526), `short` (~:595) and both
    `once_bad` (~:967, ~:987));
  - `test_semsearch_store.py:58`;
  - `test_search_route.py:169`;
  - `test_continuity_similarity.py:548` (`Short`);
  - `test_continuity_reconcile.py:969` (`_Refusing`).
- Pins that move: `test_inference_embedding.py:164-165` pins
  `set(embed_space.endpoint())` to eight keys (it gains `options`); the three
  exact facts shapes in `test_inference_facts.py`.

## Global Constraints

- **Byte-identical without a block.** For a store with no `embedding` block
  (every store today), each of these is unchanged: `space_of`, every
  `space_id`, the `endpoint()["space"]`, every `vectors._path` digest of a
  NUL-free text, every request body (exactly `{"model", "input"}`, the same
  bytes), every ledger row and capture line, and the continuity basis.
  `test_embed_space_role.py`, `test_inference_embed.py`,
  `test_context_semantic.py`, `test_semsearch_store.py`,
  `test_context_art.py`, `test_continuity_similarity.py`,
  `test_continuity_reconcile.py` and `test_inference_equivalence.py` pass.
  The existing tests are touched only to widen the inline fakes' signatures
  and to update the two pins listed above (the endpoint's key set and the
  facts shape), each of which gains one key.
- **One object.** The space id is computed from the same `EmbedOptions` that
  the request is built from:
  - `embed_attempt` sets `target.embed_options` and computes
    `space_of(raw, model, options)` from one `options` value;
  - `endpoint_of` hands that target's options on as `space["options"]`;
  - `embed_sync` refuses (`ValueError`, before the loop guard and any meter)
    a space whose options do not agree with its id.
- **Options are never derived.** Nothing but `facts.state(...,
  embedding=...)` (the user's confirmed write) puts a block on disk. No
  catalog, preset, name rule or probe touches it.
- **Strict for the role, fail-soft elsewhere.**
  - Only `embed_attempt` (and so `resolve.embedding`, `moved_by`'s `_space`)
    reads the facts strictly.
  - Chat targets, `target_for` and the probe keep the fail-soft read; an
    invalid block reads as no options there.
  - The GET facts body flags an invalid block (`embedding_invalid`).
- **Interim rule (spec Slices, S2).** In this slice:
  - `_check_embedding` refuses `input: "param"` and any non-null
    `dimensions`, with a 400 naming "not supported by this build";
  - a block on disk that uses either is invalid and names no space;
  - the client raises `ValueError` before sending for either (a hand-built
    `EmbedOptions`).
  `canonical()` already encodes both, so S3 moves no id.
- **Privacy.** The space id carries a digest, never prefix text. Capture
  lines and error rows are unchanged in shape: counts, kinds and statuses
  only.
- **Imports at module scope, no cycle.**
  - `wire` imports only `dataclasses`, `hashlib`, `json` and `typing` (plus
    what it has).
  - `embeddings` gains `from . import wire`, and `store/inference/facts.py`
    gains `from ... import wire`.
  - `store/inference/embed.py` already imports `wire` and `embeddings`.
- **Pydantic v1/v2.** `FactsUpdate.embedding: Any = None`, a plain field the
  store checks.
- **Ratchets.** No new C901 (max 10). `_check_embedding` is split into small
  helpers, the facts write applies the block through `_apply_embedding`, and
  `embed_sync`'s new checks are one helper (`_sendable`). No baseline is
  raised. A baseline that drops is reported to the coordinator, never
  `make baseline`d in the worktree.
- **Names.** Placeholder names only (Seraphine, Mara, Winifred, Realm,
  Saltmarch). The model ids are invented (`embed-1`, `vendor/embed-small`).
  The prefix strings are the published conventions of spec §3.5, which are
  not private data.

## Review Focus

- `canonical()` is the **document side only**. These keys appear only when
  their mode or field applies and the value is non-empty: `doc_prefix`,
  `field`, `doc_value`, `dim` and `dim_field`. The JSON uses sorted keys, `(",", ":")` and
  `ensure_ascii=True`, and the digest is `sha256(ascii)[:32]`. The golden
  values in Task 1 were computed independently with `hashlib`/`json` and are
  never regenerated.
- The strict read in `embed_attempt` is ONE read whose facts feed the
  capabilities, the target and the space. A `FactsUnreadableError` (held
  or mangled) is caught there and is never left to `embed_space.resolution`
  (whose `OSError` catch would hide the reason).
- `facts_moved` and `moved_by` need no new logic:
  - a document-side change is a move (it asks);
  - moving back to none is a move;
  - a query-side-only change and another model's write ask nothing.
- The `queries` check happens before the empty return, the loop guard and
  any meter, so a misuse files nothing.
- NUL removal in `embed_sync` (what is sent) and in `vectors._path` (the key)
  agree. Callers still save under their own text, and `_path` strips it the
  same way.
- The Embedding card's order: `embed_space.problem`'s reasons, then
  `missing`, then the options problem.

---

### Task 1: `wire.EmbedOptions` and the golden identity

**Files:**
- Modify: `backend/src/grimoire/wire.py`
  - `EMBED_INPUT_MODES`, `EMBED_OPTIONS_TAG = "embopt1"`, `EmbedOptions`;
  - `Target.embed_options: EmbedOptions | None = None`;
  - the module docstring gains a sentence on the embedding options.
- Create: `backend/tests/test_embed_options.py`

**Interfaces:**
- Produces:
  - `wire.EMBED_INPUT_MODES = ("none", "prefix", "param")`.
  - `@dataclass(frozen=True) class EmbedOptions` with these fields:
    - `input: str = "none"`;
    - `query_prefix: str = ""`, `document_prefix: str = ""`;
    - `param_field: str = ""`, `query_value: str = ""`,
      `document_value: str = ""`;
    - `dimensions: int | None = None`;
    - `dimensions_field: str = "dimensions"`.

    `input` is a plain `str` checked by the facts validator, not a `Literal`,
    so a parsed dict constructs it without casts; this is noted as a choice
    for the plan gate.
  - Methods:
    - `canonical() -> str`. It builds a dict:
      - `doc_prefix` when `input == "prefix"` and `document_prefix` is
        non-empty;
      - `field` and `doc_value`, both together, only when
        `input == "param"` AND `document_value` is non-empty (and
        `param_field` is non-empty), so a query-side-only `param` setting
        is `{}` (§5.1; gate finding 2);
      - `dim` and `dim_field` when `dimensions is not None` (`dim_field`
        only when non-empty).

      It returns `json.dumps(doc, sort_keys=True, separators=(",", ":"),
      ensure_ascii=True)`.
    - `is_default() -> bool`: `canonical() == "{}"`.
    - `digest() -> str`:
      `hashlib.sha256(self.canonical().encode("ascii")).hexdigest()[:32]`.

- [ ] **Step 1: Failing tests** (`test_embed_options.py`, "Golden identity";
  every literal below was computed outside the code under test, and is
  never regenerated):
  - `test_default_options_are_canonically_empty`: these are all
    `is_default()`, with `canonical() == "{}"`:
    - `EmbedOptions()`;
    - `EmbedOptions(input="prefix", query_prefix="Represent this sentence
      for searching relevant passages: ")` (the BGE case);
    - `EmbedOptions(input="prefix", document_prefix="")`;
    - `EmbedOptions(dimensions_field="output_dimension")` (a lone field);
    - `EmbedOptions(input="param", param_field="input_type",
      query_value="query")` (no `document_value`);
    - `EmbedOptions(document_prefix="passage: ")` (mode `none`).
  - `test_the_canonical_text_is_pinned`: each set gives exactly this text
    and digest.

    | Set | `canonical()` | `digest()` |
    |---|---|---|
    | prefix, doc `"search_document: "` (query `"search_query: "`) | `{"doc_prefix":"search_document: "}` | `b61a0b1188d1b0bd3a7cf05b26a81c2a` |
    | prefix, doc `"passage: "` | `{"doc_prefix":"passage: "}` | `83408df40675d61e692471b86d88967a` |
    | prefix, doc `"title: none \| text: "` | `{"doc_prefix":"title: none \| text: "}` | `d986480efdf605a08dc9ab99cb458464` |
    | prefix, doc `"Résumé: "` | `{"doc_prefix":"Résumé: "}` (escaped) | `64e976ded517afb132256ac894a35643` |
    | param, `input_type` / `document` | `{"doc_value":"document","field":"input_type"}` | `e2d2833fd99bb8fddea27a204553e640` |
    | dims 512 | `{"dim":512,"dim_field":"dimensions"}` | `18bd39256d9c7a2f22f990839a43d09d` |
    | prefix `"passage: "` + dims 256 `output_dimension` | `{"dim":256,"dim_field":"output_dimension","doc_prefix":"passage: "}` | `4dec4044ecf44b2072468492a38f06b9` |
    | param `task` / `retrieval.passage` + dims 1024 | `{"dim":1024,"dim_field":"dimensions","doc_value":"retrieval.passage","field":"task"}` | `9a09c1a1eecdb603e79ed3cd1f3f947b` |

    (The `|` inside the third row is a literal pipe; the test spells the
    string in Python, not from this table.)
  - `test_a_query_side_change_never_moves_the_digest`: changing
    `query_prefix` or `query_value` on a non-default set leaves `canonical()`
    and `digest()` equal.
  - `test_options_are_frozen`: assigning a field raises
    `FrozenInstanceError`, and a target built by hand has
    `embed_options is None`.
- [ ] **Step 2: Run** → fail:
  `cd /home/user/wt/01h-s2/backend && PYTHONPATH=src /home/user/grimoire/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_embed_options.py`
- [ ] **Step 3: Implement** in `wire.py` as above.
- [ ] **Step 4: Run** → pass, plus `tests/test_wire.py` (stdlib-only holds:
  `hashlib` and `json` are stdlib) and `tests/test_adapter_registry.py`.

### Task 2: the `embedding` facts block

**Files:**
- Modify: `backend/src/grimoire/store/inference/facts.py`
  - module docstring: the shape gains `"embedding": {...}` and the stated
    list gains it;
  - `EMBED_TEXT_MAX = 200`, `EMBED_FIELD_RE`, `EMBED_RESERVED_FIELDS`,
    `PARAM_NOT_YET`, `DIMENSIONS_NOT_YET`;
  - `_check_embedding` and its helpers `_embed_text`, `_embed_field`;
  - `embed_options`, `_apply_embedding`;
  - `_view` gains `embedding`;
  - `state(..., embedding=None)`;
  - `adopted(..., strict=False)`.
- Modify: `backend/tests/test_inference_facts.py` (the three exact-shape
  assertions gain `"embedding": None`; new tests)

**Interfaces:**
- Produces:
  - `facts._check_embedding(raw: object) -> dict`: the block as stored, or
    `ValueError` naming the problem. Rules (spec §3.2, plus the interim
    rule):
    - `raw` must be a dict. A key outside the eight `EmbedOptions` field
      names is ignored and not kept. A JSON `null` value reads as absent.
    - `input` is one of `wire.EMBED_INPUT_MODES` (absent is `"none"`).
    - The five text fields (`query_prefix`, `document_prefix`,
      `param_field`, `query_value`, `document_value`) and `dimensions_field`
      must be `str`.
    - `_embed_text` holds each prefix and value to at most `EMBED_TEXT_MAX`
      characters. It refuses any C0 control except `\n` and `\t`
      (`ord < 0x20`), and any C1 control (`0x80 <= ord < 0xa0`). NUL is
      therefore refused.
    - `_embed_field` checks `param_field` (when non-empty) and
      `dimensions_field`:
      - each must match `EMBED_FIELD_RE = re.compile(r"[a-z_][a-z0-9_]{0,40}")`
        (`fullmatch`);
      - neither may be in `EMBED_RESERVED_FIELDS = frozenset({"model",
        "input", "encoding_format", "user"})`;
      - in `param` mode with `dimensions` set, the two may not be equal.
    - **Interim:** `input == "param"` raises `ValueError(PARAM_NOT_YET)`, and
      a non-None `dimensions` raises `ValueError(DIMENSIONS_NOT_YET)`. The
      texts are "the request-field input type is not supported by this build"
      and "requested dimensions are not supported by this build". Both are
      checked after the type and text checks, so a malformed body is still
      named first.
    - `prefix` needs at least one non-empty prefix.
    - Normalised: mode `none` → `{}`; mode `prefix` →
      `{"input": "prefix", **the non-empty prefixes}`.
  - `facts.embed_options(model_facts: Mapping) -> wire.EmbedOptions | None`:
    `model_facts.get("embedding")`. None → None. Otherwise
    `_check_embedding(block)`, built into `EmbedOptions(...)` field by field.
    Raises `ValueError` for an invalid block.
  - `_view(entry, rev)` gains `"embedding"`: the raw block (a shallow
    `dict(...)` copy when a dict, the value as stored otherwise, None when
    absent), judged by readers rather than here.
  - `facts.state(..., embedding: object = None, guard=...)`:
    - `None` leaves the block;
    - anything else is `_check_embedding`'d before the file is touched;
    - a normalised `{}` removes it (so `{}` and `{"input": "none"}` both
      remove), and anything else replaces it.

    Applied by `_apply_embedding(entry, block)`. The early "nothing stated"
    return also requires `embedding is None`.
  - `facts.adopted(..., strict: bool = False)`: `strict` reads through
    `_load_for_write`, so a held or mangled file raises as `of(strict=True)`
    does.

- [ ] **Step 1: Failing tests** (`test_inference_facts.py`, the `conn`
  fixture):
  - The three exact-shape assertions gain `"embedding": None`.
  - `test_embedding_options_are_a_stated_fact`:
    - `facts.state(cid, "embed-1", embedding={"input": "prefix",
      "query_prefix": "search_query: ", "document_prefix": "search_document: ",
      "colour": "red"})`;
    - `facts.read(cid)["embed-1"]["embedding"] == {"input": "prefix",
      "query_prefix": "search_query: ", "document_prefix": "search_document: "}`
      (the unknown key is dropped);
    - `facts.embed_options(facts.of(cid, "embed-1", rev))` equals the
      matching `EmbedOptions`;
    - after `llm_connections.update_connection(cid, api_key="sk-new")` (a
      rev restamp), `of` at the new rev still carries the block.
  - `test_an_embedding_write_of_none_or_an_empty_block`:
    - `embedding=None` alongside `prefill=True` keeps an existing block;
    - `embedding={}` removes it;
    - `embedding={"input": "none"}` removes it too;
    - an entry left with nothing is dropped (`facts.read(cid) == {}`).
  - `test_a_qwen_style_prefix_with_a_newline_is_kept`:
    `query_prefix = "Instruct: Given a passage of a story, retrieve the lore,
    records or images it concerns\nQuery: "` (and a `\t` in the document
    prefix) is stored as given.
  - `test_invalid_embedding_options_are_refused_before_writing`
    (parametrized). Each raises `ValueError` and the file is byte-unchanged:
    - not an object (`"prefix"`, `[]`);
    - `input: "both"`;
    - a prefix that is not a string (`5`);
    - `prefix` with both prefixes empty;
    - a 201-character prefix;
    - a prefix containing `\0`, `\x1b` or `\x85`;
    - `dimensions_field` set to `"model"`, `"a.b"`, `"Field"` or 42 chars;
    - `{"input": "param", "param_field": "input_type", "query_value":
      "query", "document_value": "document"}` (message names "not
      supported by this build");
    - `{"dimensions": 512}` (same);
    - `{"dimensions": 512.0}` and `{"dimensions": True}` (refused, by the
      interim rule today; S3 keeps them refused by the integer rule).
  - `test_an_invalid_block_on_disk_is_named_by_its_reader`: a hand-written
    `{"embed-1": {"embedding": {"input": "param", ...}}}`, and another with
    `"embedding": "prefix"`. `facts.of` returns the raw block (no raise);
    `facts.embed_options(...)` raises `ValueError`.
  - `test_adopted_strict_raises_on_a_held_file`: the
    `test_no_write_replaces_a_file_it_could_not_read` monkeypatch shape;
    `facts.adopted(..., strict=True)` raises `FactsUnreadableError`, and
    `strict=False` returns the empty shape.
- [ ] **Step 2: Run** → fail:
  `cd /home/user/wt/01h-s2/backend && PYTHONPATH=src /home/user/grimoire/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_inference_facts.py`
- [ ] **Step 3: Implement** as above. `state`'s docstring gains the
  `embedding` paragraph (stated, survives `rev`, moves the Embedding role's
  space for the role's model, so the facts route confirms it).
- [ ] **Step 4: Run** → pass, plus `tests/test_inference_capabilities.py
  tests/test_model_rates.py tests/test_inference_migrate.py
  tests/test_format_hold.py tests/test_import_guard.py tests/test_atomic_guard.py`.

### Task 3: the resolver, the endpoint dict and the Embedding card

**Files:**
- Modify: `backend/src/grimoire/store/inference/resolve.py`:
  - `OPTIONS_UNREADABLE`, `OPTIONS_INVALID`;
  - `space_of(conn, model, options=None)`;
  - `_read_facts(..., strict=False)`;
  - `_target` sets `embed_options`;
  - `EmbedAttempt.options_problem`;
  - `embed_attempt`'s strict read;
  - `embedding()` passes `embed_options_problem`;
  - docstrings of `space_of`, `embed_attempt` and `embedding`.
- Modify: `backend/src/grimoire/store/inference/resolved.py`:
  - `ResolvedInference.embed_options_problem: str = ""`;
  - the `space_id` comment names the `\0embopt1:` suffix.
- Modify: `backend/src/grimoire/store/embed_space.py`: `endpoint_of` adds
  `options`, and the docstrings of `endpoint` and `facts_moved` change.
- Modify: `backend/src/grimoire/store/inference/settings.py`
  (`_embedding_problem` names the options problem)
- Test: `backend/tests/test_embed_options.py`,
  `backend/tests/test_inference_settings.py`

**Interfaces:**
- Produces:
  - `resolve.space_of(conn, model, options: wire.EmbedOptions | None = None)
    -> str`. Today's `f"{id}\0{rev}\0{model}"` when `options is None or
    options.is_default()`. Otherwise
    `f"{base}\0{wire.EMBED_OPTIONS_TAG}:{options.digest()}"`.
  - `resolve.OPTIONS_HELD = "held"`, `resolve.OPTIONS_MANGLED = "mangled"`
    and `resolve.OPTIONS_INVALID = "invalid"` (gate finding 5: held and
    mangled are told apart, as `_facts_body` does).
  - `resolve.strict_embed_options(provider_id, model, rev, stated=None) ->
    tuple[dict, EmbedOptions | None, str]`: the one strict read (facts,
    options, problem), shared by `embed_attempt` and the probe (gate
    finding 6).
  - `EmbedAttempt(attempt, missing, space_id, options_problem: str = "")`.
  - `embed_attempt`, when `model_facts is None`:
    - it reads `_read_facts(provider_id, _sent_model(raw, model), _rev(raw),
      stated, strict=True)`;
    - `facts.FactsMangledError` gives `model_facts = {}` and
      `OPTIONS_MANGLED`; any other `facts.FactsUnreadableError` gives
      `OPTIONS_HELD`;
    - then `options = facts.embed_options(model_facts)`, where a
      `ValueError` gives `options = None` and `OPTIONS_INVALID` (unless
      already unreadable);
    - the facts go to `_attempt(..., model_facts=model_facts)`, so the facts
      file is read once;
    - the attempt's target becomes
      `dataclasses.replace(target, embed_options=options)`, so the target
      and the space share one object;
    - `space_id = space_of(raw, model, options)` only when there is a model,
      an endpoint, nothing `missing` and no `options_problem`, and None
      otherwise.

    A handed-in `model_facts` (`facts_moved`) is parsed the same way, with
    no read.
  - `_read_facts(..., strict=True)` reads `facts.of(..., strict=True)`, or
    `facts.adopted(..., strict=True)` below format 2. It lets
    `facts.FactsUnreadableError` through, and every other error still reads
    as `{}`.
  - `_target(...)` sets `embed_options=_stated_options(model_facts)`:
    `facts.embed_options(model_facts)`, and None on `ValueError`. It is
    fail-soft, for chat targets and `target_for`'s display. The probe does
    NOT send these: it reads `strict_embed_options` itself (Task 7).
  - `embed_space.endpoint_of`'s dict gains
    `"options": target.embed_options or wire.EmbedOptions()` (it imports
    `wire`).
  - `settings._embedding_problem`: after `embed_space.problem`'s reasons and
    BEFORE `missing` (gate finding 5: with the file unreadable, `missing` was
    computed from no facts and would blame the model):
    - `OPTIONS_HELD` → `f"The model facts file of {name}
      (llm_connections/{id}.facts.json) is held by another program, so
      embedding is off until it can be read — try again shortly."`;
    - `OPTIONS_MANGLED` → `f"The model facts file of {name}
      (llm_connections/{id}.facts.json) is not valid JSON, so embedding is
      off — fix or remove that file."` (no "try again");
    - `OPTIONS_INVALID` → `f"The embedding options of {model} on {name} are
      invalid, so embedding is off — fix them in the model's facts
      (llm_connections/{id}.facts.json)."`.
  - `embed_space.options_problem(cfg=None) -> str`: the resolution's
    `embed_options_problem` ("" when it embeds or reading raised), for the
    continuity sweep (Task 6).
  - `embed_space.moved_by` and `config_moved` ask when either side has an
    options problem (gate finding 1; see the Plan gate section for the
    exact rule).

- [ ] **Step 1: Failing tests** (`test_embed_options.py`, sections
  "Resolution" and "Card"; `_provider()` and `_role()` shaped as in
  `test_inference_embed.py`: an `openai_compatible` "Saltmarch Vectors", the
  role on `embed-1` at format 2):
  - `test_space_of_is_todays_string_for_default_options`:
    - `space_of(conn, "m") == space_of(conn, "m", EmbedOptions())`, and both
      equal `space_of(conn, "m", <BGE query-only set>) == "id\0rev\0m"`;
    - a nomic set gives `"id\0rev\0m\0embopt1:b61a0b1188d1b0bd3a7cf05b26a81c2a"`.
  - `test_no_block_keeps_todays_space`:
    - with no facts file, `embed_space.endpoint()["space"] ==
      f"{pid}\0{rev}\0embed-1"` and `["options"] == EmbedOptions()`;
    - the same holds with a facts file stating `prefill`, `rates` and
      `context_window` for `embed-1`, and an `embedding` block on another
      model.
  - `test_stated_options_move_the_space_and_ride_the_endpoint`: state the
    nomic set (`facts.state(pid, "embed-1", embedding=...)`). Then the
    endpoint's space ends `"\0embopt1:b61a0b1188d1b0bd3a7cf05b26a81c2a"`,
    and `got["options"] is got["target"].embed_options`.
  - `test_a_query_side_only_block_keeps_the_base_space`: the BGE set. The
    space is the base string, and `options.query_prefix` is the BGE
    instruction (so a query is still sent prefixed).
  - `test_an_invalid_block_names_no_space`: write the param block by hand
    (the S2-invalid case), and separately `"embedding": 7`. Then
    `embed_space.endpoint() is None`, and `embed_space.resolution()` has
    `embed_options_problem == "invalid"` and `space_id is None`.
  - `test_an_unreadable_facts_file_names_no_space`: the nomic set stated,
    then the facts file held (`Path.read_text` raising `OSError` for that
    path, as `test_provider_api._facts_held` does). Then:
    - `endpoint() is None` and the problem is `"unreadable"`;
    - the same for a mangled file (`"{"` written by hand);
    - `space_of`'s base string is never returned in its place.
  - `test_chat_targets_read_options_fail_soft`:
    `resolve.target_for(raw, "embed-1", NO_SAMPLING,
    model_facts=resolve.facts_for(raw, "embed-1"))` carries the stated
    options. With the invalid block on disk it carries `None` and does not
    raise.
  - `test_the_card_says_why_options_turned_embedding_off`
    (`test_inference_settings.py`, the `_embedding_problem` helper and
    `client`):
    - an invalid block on the role's model gives `card["on"] is False` and
      the `OPTIONS_INVALID` sentence;
    - the held file gives the `OPTIONS_HELD` sentence, naming the file, even
      when the catalog says the model cannot embed (options before
      `missing`);
    - a mangled file gives the `OPTIONS_MANGLED` sentence, with no "try
      again".
- [ ] **Step 2: Run** → fail (`tests/test_embed_options.py
  tests/test_inference_settings.py`).
- [ ] **Step 3: Implement** as above. `facts_moved`'s docstring becomes:
  facts move the space when they change the role's document-side options,
  or turn the role on; a query-side-only change and turning it off move
  nothing. `EMBEDDING_FACTS_CONFIRM` is changed in Task 7.
- [ ] **Step 4: Run** → pass, plus `tests/test_embed_space_role.py
  tests/test_inference_embedding.py tests/test_inference_resolve.py
  tests/test_inference_equivalence.py tests/test_inference_retire.py
  tests/test_inference_settings.py tests/test_inference_settings_limits.py
  tests/test_inference_settings_rates.py tests/test_wire.py
  tests/test_lowering_retired_guard.py`.

### Task 4: the client builds `none` and `prefix` requests

**Files:**
- Modify: `backend/src/grimoire/embeddings.py`:
  - `from . import wire`;
  - `prepare_inputs`, `request_body`, `check_sendable`, `check_queries`;
  - `EmbeddingsClient.embed(..., *, options=None, queries=0)`;
  - `_post` and `_fetch` take `options`;
  - the module docstring states what is sent.
- Test: `backend/tests/test_embeddings.py`

**Interfaces:**
- Produces:
  - `embeddings.check_sendable(options: wire.EmbedOptions | None) -> None`:
    `ValueError` when `options` is neither None nor an `EmbedOptions`, when
    `options.input == "param"`, or when `options.dimensions is not None`
    ("not supported by this build", S3 lifts both).
  - `embeddings.check_queries(queries: int, count: int) -> None`:
    `ValueError` unless `queries` is an int (not a bool) with
    `0 <= queries <= count`.
  - `embeddings.prepare_inputs(texts, options, queries) -> list[str]`. In
    `prefix` mode each text is `query_prefix + t` for the first `queries`
    texts and `document_prefix + t` for the rest. Otherwise it is a copy of
    `texts`. This is the request builder's input half, which S5's async
    client shares.
  - `embeddings.request_body(model, chunk, options) -> dict`:
    `{"model": model, "input": chunk}` (S3 adds fields).
  - `EmbeddingsClient.embed(texts, model, key, base_url, deadline=None,
    usage=None, *, options=None, queries=0)`. In order:
    1. `check_sendable(options)` and `check_queries(queries, len(texts))`;
    2. the empty return;
    3. the base URL and model checks;
    4. `sent = prepare_inputs(texts, options or wire.EmbedOptions(),
       queries)`, batched by `BATCH`.

- [ ] **Step 1: Failing tests** (`test_embeddings.py`, `make_client`):
  - `test_no_options_sends_todays_exact_bytes`: `embed(["a", "b"], "embed-1",
    ...)` with no options, and again with `options=EmbedOptions()` and
    `queries=1`. Each request's `content` equals
    `httpx.Request("POST", BASE, json={"model": "embed-1", "input": ["a",
    "b"]}).content`.
  - `test_prefix_mode_prefixes_each_input_in_one_request`: nomic options,
    `queries=1`, texts `["Where is Mara?", "Mara crossed the Saltmarch.",
    "Seraphine waits."]`. This makes one request with input
    `["search_query: Where is Mara?", "search_document: Mara crossed the
    Saltmarch.", "search_document: Seraphine waits."]`, keys exactly
    `{"model", "input"}`, and returns vectors in input order.
  - `test_a_query_only_prefix_leaves_documents_bare`: the BGE set, with
    `queries=1`.
  - `test_prefixes_survive_batching`: `BATCH + 2` texts, `queries=1`. The
    first request's first input carries the query prefix, and every other
    input of both requests carries the document prefix.
  - `test_queries_out_of_range_is_a_value_error_before_sending`: `queries=3`
    for two texts, `-1`, and `True`. Each raises `ValueError`, and the
    handler is never called.
  - `test_param_and_dimensions_are_refused_before_sending`: hand-built
    `EmbedOptions(input="param", ...)` and `EmbedOptions(dimensions=512)`.
    Each raises `ValueError` and nothing is sent.
- [ ] **Step 2: Run** → fail (`tests/test_embeddings.py`).
- [ ] **Step 3: Implement.** `_fetch` sends `json=request_body(model, chunk,
  options)`, so `_fetch` gains no branch.
- [ ] **Step 4: Run** → pass, plus `tests/test_operation_guard.py
  tests/test_usage_guard.py tests/test_import_guard.py`.

### Task 5: the operation (`queries`, options, NUL) and the vector key

**Files:**
- Modify: `backend/src/grimoire/store/inference/embed.py`:
  - `_sendable(space, texts, queries) -> tuple[wire.EmbedOptions,
    list[str]]`;
  - `embed_sync(..., queries: int = 0)` and `embed(..., queries: int = 0)`;
  - the module docstring gains an "Options and queries" paragraph.
- Modify: `backend/src/grimoire/store/vectors.py`: `_path` removes `\0`, and
  the module docstring's key sentence says so.
- Modify the fakes' signatures (keyword-only `options=None, queries=0`,
  unused unless stated):
  - `backend/tests/llm_fakes.py` (`FakeEmbeddings` records `options:
    list` and `queries: list[int]`);
  - `backend/tests/test_context_semantic.py`;
  - `backend/tests/test_semsearch_store.py`;
  - `backend/tests/test_search_route.py`;
  - `backend/tests/test_continuity_similarity.py`;
  - `backend/tests/test_continuity_reconcile.py`.
- Test: `backend/tests/test_inference_embed.py`, `backend/tests/test_vectors.py`

**Interfaces:**
- Produces `embed._sendable(space, texts, queries)`, in this order:
  1. `embeddings.check_queries(queries, len(texts))`;
  2. `options = space.get("options")`, where None gives `wire.EmbedOptions()`;
  3. `embeddings.check_sendable(options)`;
  4. **agreement**: the id `space["space"]` must carry
     `"\0" + wire.EMBED_OPTIONS_TAG + ":"` exactly when
     `not options.is_default()`, and then end with that tag plus
     `options.digest()`; otherwise `ValueError("the space and its options
     disagree")`;
  5. it returns `(options, [t.replace("\0", "") for t in texts])`.
- `embed_sync` order: the task check, then
  `options, clean = _sendable(...)`, the empty return, `_refuse_on_loop`, the
  spent deadline, the meter, and then:
  - `client.embed(clean, ..., options=options, queries=queries)`;
  - `sent = embeddings.prepare_inputs(clean, options, queries)`;
  - `estimate_prompt(m.usage, sent)`;
  - `_capture(task, sent, ...)`.
- `embed()` forwards `queries`.
- `vectors._path(space, text)` hashes `f"{space}\0{text.replace(chr(0), '')}"`.

- [ ] **Step 1: Failing tests:**
  - `test_inference_embed.py`, the `space` fixture and `_client`:
    - `test_queries_out_of_range_files_nothing`: `queries=2` for one text, and
      `queries=1` for `[]`. Each raises `ValueError`, with no request, no
      row, and no debug line.
    - `test_a_space_without_options_sends_the_bare_body`: the fixture's
      endpoint dict with `"options"` deleted, and a hand-built
      `{"space": "s", "model": "m", "key": "k", "base_url": "u"}`. The body is
      `{"model", "input"}`.
    - `test_stated_options_reach_the_request`: nomic stated before the
      fixture resolves, `queries=1`, `["q", "d"]`. The request input is the
      two prefixed strings. The capture line's `bytes` equals the summed
      UTF-8 length of the prefixed inputs. A test endpoint with no
      `prompt_tokens` gives a row whose estimated `prompt_tokens` is the
      characters/4 heuristic over the prefixed inputs (`tokens._loaded`
      patched to None, as
      `test_an_unreported_prompt_is_counted_locally_and_says_so` does).
    - `test_a_space_that_disagrees_with_its_options_is_refused`:
      `{**space, "options": nomic}` on a base-space dict, and `{**space_with_
      options, "options": EmbedOptions()}`. Each raises `ValueError` before
      anything is sent, with no row.
    - `test_nul_never_reaches_a_request`: `["Mara\0 crossed"]` is sent as
      `["Mara crossed"]`.
    - `test_the_async_door_forwards_queries`:
      `asyncio.run(embed.embed(..., queries=1))` over the nomic space sends
      the query prefix first.
  - `test_vectors.py`:
    - `test_the_key_is_todays_digest`:
      `vectors._path("saltmarch-vectors\0r1\0embed-1", "Mara crossed the
      Saltmarch at dusk").name ==
      "1375582ed7a59d40cac77ce4142344db6fa3acd98385ef8961e06e9dcb82b77d.vec"`.
    - `test_a_nul_text_keys_as_its_nul_free_form`:
      `_path(s, "Mara crossed the Salt\0march at dusk") == _path(s, "Mara
      crossed the Saltmarch at dusk")`, and a `save` under the first is
      `load`ed under the second.
- [ ] **Step 2: Run** → fail (`tests/test_inference_embed.py
  tests/test_vectors.py`).
- [ ] **Step 3: Implement**, and widen every fake's signature listed above.
- [ ] **Step 4: Run** → pass, plus `tests/test_embed_off_loop.py
  tests/test_context_semantic.py tests/test_semsearch_store.py
  tests/test_search_route.py tests/test_context_art.py
  tests/test_continuity_similarity.py tests/test_continuity_reconcile.py
  tests/test_operation_guard.py tests/test_usage_guard.py`.

### Task 6: callers mark their query; nothing re-embeds on upgrade

**Files:**
- Modify: `backend/src/grimoire/store/context/semantic.py` (`_embed`: both
  calls pass `queries=1`; the docstring says the query is sent as a query
  and is never cached)
- Modify: `backend/src/grimoire/store/semsearch.py` (both calls `queries=1`)
- Modify: `backend/src/grimoire/store/context/art.py` (`_semantic_scores`
  passes `queries=1`)
- (`store/continuity/similarity.py` is unchanged: it embeds documents only,
  `queries` defaults to 0.)
- Test:
  - `backend/tests/test_context_semantic.py`;
  - `backend/tests/test_semsearch_store.py`;
  - `backend/tests/test_context_art.py`;
  - `backend/tests/test_continuity_similarity.py`;
  - `backend/tests/test_continuity_reconcile.py`.

- [ ] **Step 1: Failing tests:**
  - `test_context_semantic.py`:
    - `FakeProvider.embed` records `queries`.
    - `test_recall_sends_its_query_as_a_query`: the first call passes
      `queries=1`. With the provider failing `bad_response` once, the
      query-only retry passes `queries=1` too.
    - `test_no_block_reads_todays_vectors` (no re-embed on upgrade): with
      `configure()` and no facts block, one recall over an entry writes
      exactly one `.vec`. Its name is
      `hashlib.sha256(f"{pid}\0{rev}\0embed-1\0{entry_text}".encode()).hexdigest()
      + ".vec"`, today's formula restated in the test. A second recall with
      another window sends only `[window]`.
    - `test_prefix_options_end_to_end`:
      - setup: nomic stated on `embed-1`; `semantic._CLIENT` is a real
        `EmbeddingsClient` over a recording `MockTransport` answering
        `[1.0, 0.0]` per input; threshold `"0.0"`;
      - one recall sends one request whose input is
        `["search_query: " + window, "search_document: " +
        semantic.entry_text(e)]`;
      - the entry's vector loads under `space()`, which ends
        `"\0embopt1:b61a0b1188d1b0bd3a7cf05b26a81c2a"`, keyed by the
        unprefixed entry text, and not under the base space;
      - the cache holds exactly one `.vec` (the query vector is never
        cached).
  - `test_semsearch_store.py`: `FakeProvider` records `queries`.
    `test_search_sends_its_query_as_a_query` checks both the first call and
    the retry.
  - `test_context_art.py`: `test_art_sends_its_query_as_a_query`
    (`FakeEmbeddings.queries == [1]`, on the
    `test_semantic_ranking_files_one_art_catalog_row` shape).
  - `test_continuity_similarity.py`: `test_continuity_embeds_documents_only`
    (`fake.queries` all 0).
  - `test_continuity_reconcile.py`:
    - `test_no_block_keeps_the_sweeps_basis_byte_identical`: with
      `_configure()` and no facts block, `_sweep(cid).space ==
      f"{conn}\0{rev}\0embed-1"`, and each `sweep.hashes[ref] ==
      sha256(f"{space}\0{text}")`. These are today's strings, restated.
    - `test_stated_options_salt_the_sweep_with_the_options_space`: the nomic
      set gives `sweep.space.endswith("\0embopt1:" + digest)`.
    - `test_an_options_problem_carries_the_old_basis_forward` (gate finding
      3): the nomic set stated, a first embedded sweep persisted
      (`reconcile.persist_found`), so the stored basis holds the options
      space. Then the facts file is held (and, in a second case, the block
      made invalid by hand). A sweep is persisted again, and
      `candidates.read(cid)["basis"]` is unchanged: the same
      `embedding_space`, `embedding_model` and `identity_hashes`. The sweep
      reports `embedding == "failure"` with `embedding_error ==
      "options_held"` / `"options_invalid"`, and no `FakeEmbeddings` call is
      made.
- [ ] **Step 2: Run** → fail (the `queries` assertions; the rest pass once
  Tasks 1-5 are in, and are the acceptance evidence).
- [ ] **Step 3: Implement** the three keyword additions, and in
  `reconcile.discover`: when `similarity.available()` is None and
  `similarity.options_problem()` names one, set `sweep.space` and
  `sweep.model` from the stored basis (`basis["embedding_space"]`,
  `basis["embedding_model"]`) before the hashes are computed, and set
  `sweep.embedding = "failure"`, `sweep.embedding_error =
  f"options_{problem}"`. With no vectors, `_rescored` then reports nothing
  rescored, so `_found_basis` keeps every old entry. No error row: the
  Embedding card already says why.
- [ ] **Step 4: Run** the five files → pass, plus
  `tests/test_operation_guard.py` (`MIN_EMBED_CALLS` unchanged at six).

### Task 7: the facts route, the confirm question and the probe

**Files:**
- Modify: `backend/src/grimoire/routes/models.py` (`FactsUpdate.embedding:
  Any = None`, with a docstring line)
- Modify: `backend/src/grimoire/routes/config.py`:
  - `put_connection_facts` passes `embedding=fields.get("embedding")`;
  - its docstring says an options write on the role's model is a move,
    confirmed, and a query-side-only one asks nothing;
  - `_facts_body` adds `embedding_invalid` (and `embedding_invalid_reason`);
  - `_refuse_unconfirmed_facts`'s docstring changes;
  - `_probe` reads `resolve.strict_embed_options` for the model under test
    before the embed probe; with a problem it reports `options_unreadable` /
    `options_invalid` and sends nothing. Otherwise `_embed_probe` sends
    those options and estimates `prepare_inputs([EMBED_TEXT], ...)`.
- Modify: `backend/src/grimoire/store/inference/settings.py`
  (`EMBEDDING_FACTS_CONFIRM` reads "This changes what the Embedding role
  embeds your library with through {provider}: it will be embedded under the
  new setting, which may cost money — confirm to save it." It is true both
  of `embed: yes` turning the role on and of an options change; gate
  finding 11.)
- Test: `backend/tests/test_provider_api.py`,
  `backend/tests/test_model_test_call.py`

**Interfaces:**
- Produces:
  - `GET .../facts` adds `"embedding"` (the raw block or null, through
    `**known`) and `"embedding_invalid": bool`. When true, it also adds
    `"embedding_invalid_reason": str` (the `ValueError` text, a fixed
    sentence from the validator, never file contents).
  - `PUT .../facts` adds `embedding`. Its answers:
    - an invalid block is 400 with nothing written;
    - an unconfirmed move is 400 `confirm_embedding`;
    - with confirm it writes;
    - below format 2 it is 409 `not_migrated` (unchanged,
      `refuse_unmigrated`).

- [ ] **Step 1: Failing tests** (`test_provider_api.py`, `_embedding_on`
  and `_facts_held`; `NOMIC = {"input": "prefix", "query_prefix":
  "search_query: ", "document_prefix": "search_document: "}`):
  - `test_an_options_write_on_the_roles_model_needs_confirm`:
    - each of `{}`, `{"confirm_embedding": False}` and
      `{"confirm_embedding": "true"}` gives 400 `confirm_embedding`, with
      `facts.read(pid) == {}` and the space unchanged;
    - with `True` it is 200, and the space ends
      `"\0embopt1:b61a0b1188d1b0bd3a7cf05b26a81c2a"`;
    - the GET carries `embedding == NOMIC` and `embedding_invalid is False`.
  - `test_moving_back_to_no_options_still_asks`: after the above,
    `embedding: {}` gives 400, then 200 with confirm, and the space is the
    base string again.
  - `test_a_query_side_only_options_write_asks_nothing`:
    - the BGE set gives 200 with no confirm, and the space is unchanged;
    - then, on a confirmed nomic set, changing only `query_prefix` gives
      200 with no confirm and the same space.
  - `test_an_options_write_on_another_model_asks_nothing`:
    `vendor/other` + NOMIC gives 200 with no confirm.
  - `test_invalid_options_are_400_and_write_nothing`: a few of Task 2's
    bodies through the route (`param`, `dimensions`, a NUL prefix, the
    reserved field). Each is 400 with the validator's text and
    `facts.read(pid) == {}`.
  - `test_a_qwen_style_prefix_with_a_newline_saves`: the query-only Qwen
    prefix gives 200 and is stored with its `\n`.
  - `test_an_invalid_block_on_disk_is_flagged`: a hand-written param block
    on the role's model gives a GET with `embedding_invalid is True` and a
    reason containing "not supported by this build". Then
    `store.embed_space.resolve() is None`.
  - `test_an_options_write_before_the_switch_is_409_not_migrated` (the
    `test_a_limit_write_before_the_switch_is_409_not_migrated` shape).
  - `test_model_test_call.py`, `test_the_embed_probe_sends_the_stated_options`
    (the `_embedder` and `_connection` helpers, an `openai_compatible`
    connection at format 2, nomic stated for `MODEL`):
    - the probe request's input is `["search_document: " +
      probes.EMBED_TEXT]` (the probe text is a document);
    - the verdict is `{"ok": True, "dims": 3}`;
    - the row's estimated prompt counts the prefixed text.
- [ ] **Step 2: Run** → fail (`tests/test_provider_api.py
  tests/test_model_test_call.py`).
- [ ] **Step 3: Implement** as above.
- [ ] **Step 4: Run** → pass, plus `tests/test_pydantic_guard.py
  tests/test_routing_guard.py tests/test_operation_guard.py
  tests/test_usage_guard.py tests/test_embed_space_role.py
  tests/test_inference_settings.py`.

### Task 8: what the docs say

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/store-guarantees.md` ("Model settings across processes")
- Modify: `backend/src/grimoire/embeddings.py` (docstring, if Task 4 left
  it unsaid)

- [ ] **Step 1: CLAUDE.md**, keeping its wording distinct from
  store-guarantees' (`test_no_document_restates_another`):
  - In "Adding an embedding call site?", one new sub-bullet, **Options**:
    - a model's embedding options are a stated fact (`embedding` in its
      facts), never derived;
    - the role reads them strictly (an unreadable facts file or an invalid
      block names no space and the card says why);
    - the document-side options enter the space id as
      `\0embopt1:<digest>`, and default options keep today's id;
    - the request is built from the `options` the endpoint dict carries,
      which `embed_sync` checks against the id;
    - a caller puts its queries first and passes `queries=N`, and a query
      vector is never cached;
    - NUL is removed from what is sent and from the vector key.
  - In "A settings surface never spends unasked", the facts-write clause
    becomes "a model-facts write that turns the role on, … or changes the
    document-side embedding options of the model it embeds with (a
    query-side-only change asks nothing)".
- [ ] **Step 2: store-guarantees**: after the facts paragraph, a short
  paragraph:
  - the Embedding role is the one strict reader (a held or mangled facts
    file turns embedding off for that read rather than reading as "no
    options");
  - spec §10 M8: an older build ignores the `embedding` block and computes
    the option-less space, so two devices on different builds that take
    turns sweeping continuity each re-salt the basis and every ref
    rescores. Each build's requests still match its own space, so this
    costs work and loses nothing.
- [ ] **Step 3: Run** `tests/test_docs_guard.py` → pass.

### Task 9: gates and commit

- [ ] **Step 1:** the targeted runs of Tasks 1-8 together, then the guards:
  `tests/test_import_guard.py tests/test_operation_guard.py
  tests/test_usage_guard.py tests/test_atomic_guard.py tests/test_paths_guard.py
  tests/test_lock_domain_guard.py tests/test_pydantic_guard.py
  tests/test_docs_guard.py tests/test_wire.py tests/test_format_hold.py`.
- [ ] **Step 2:** from the worktree root, run the ratchets and templates:
  `make check-lint check-mypy check-templates PY=/home/user/grimoire/backend/.venv/bin/python`.
  They should be at baseline with no baseline file changed. A drop is
  reported to the coordinator. Do not run `make check-eslint` (it runs
  `npm ci`); no frontend file changes in this slice.
- [ ] **Step 3:** the whole backend once:
  `make check-py PY=/home/user/grimoire/backend/.venv/bin/python`. The only
  failure allowed is the known root-only
  `test_atomic.py::test_a_read_only_record_is_not_silently_replaced`.
- [ ] **Step 4: Commit** `01h-S2: options in the space identity, the queries
  split and prefix mode`. The body names the facts block, the strict read,
  the `embopt1` space suffix, `queries=`, prefix mode, NUL removal and the
  reworded confirm. It ends with the two attribution lines from the slice
  brief.

## Acceptance traced (spec §11; Slices, 01h-S2)

| Acceptance item | Test |
|---|---|
| `space_of(conn, m)` = `space_of(conn, m, EmbedOptions())` = `"id\0rev\0m"` | `test_space_of_is_todays_string_for_default_options` |
| `vectors._path` pinned to today's digest | `test_the_key_is_todays_digest` |
| Canonically empty sets are default | `test_default_options_are_canonically_empty` |
| Exact canonical JSON pinned; `embopt1` ids pinned | `test_the_canonical_text_is_pinned`, `test_space_of_is_todays_string_for_default_options`, `test_stated_options_move_the_space_and_ride_the_endpoint` |
| `512.0` / `true` refused | `test_invalid_embedding_options_are_refused_before_writing` (interim rule; S3 keeps them refused by the integer rule) |
| A NUL text keys as its NUL-free form | `test_a_nul_text_keys_as_its_nul_free_form`, `test_nul_never_reaches_a_request` |
| No re-embed on upgrade (load hits, no request) | `test_no_block_reads_todays_vectors`, `test_no_block_keeps_todays_space` |
| Continuity basis byte-identical | `test_no_block_keeps_the_sweeps_basis_byte_identical` |
| Request body `none` is exactly `{"model","input"}` | `test_no_options_sends_todays_exact_bytes`, `test_a_space_without_options_sends_the_bare_body` |
| `prefix` gives prefixed inputs, one request | `test_prefix_mode_prefixes_each_input_in_one_request`, `test_prefix_options_end_to_end` |
| Facts validation refusals are 400 and write nothing | `test_invalid_embedding_options_are_refused_before_writing`, `test_invalid_options_are_400_and_write_nothing` |
| An invalid block names no space, the card says why, no request sent | `test_an_invalid_block_names_no_space`, `test_the_card_says_why_options_turned_embedding_off`, `test_an_invalid_block_on_disk_is_flagged` |
| An unreadable facts file sends no request; the basis never takes the base space | `test_an_unreadable_facts_file_names_no_space`, `test_an_unreadable_facts_file_never_salts_with_the_base_space` |
| A Qwen-style prefix with `\n` saves | `test_a_qwen_style_prefix_with_a_newline_is_kept`, `test_a_qwen_style_prefix_with_a_newline_saves` |
| Confirm on the role's model; no question for a query-side-only change or another model; moving back asks | the four `test_provider_api.py` confirm tests |
| A format-1 store answers 409 `not_migrated` | `test_an_options_write_before_the_switch_is_409_not_migrated` |
| `queries` keyword + `ValueError`; recall/search/art pass `queries=1` | `test_queries_out_of_range_files_nothing`, `test_queries_out_of_range_is_a_value_error_before_sending`, the four "as a query" tests |
| A query vector is never cached | `test_prefix_options_end_to_end` (and the existing `test_the_query_vector_is_never_written_to_the_cache`) |
| The probe sends the stated options | `test_the_embed_probe_sends_the_stated_options` |
| Interim: `param` and `dimensions` refused, and invalid on disk | `test_invalid_embedding_options_are_refused_before_writing`, `test_an_invalid_block_names_no_space`, `test_param_and_dimensions_are_refused_before_sending` |

## Choices beyond the spec's letter (for the plan gate)

- **`EmbedOptions.input` is `str`, not a `Literal`.** The validator holds it
  to `EMBED_INPUT_MODES`, and a parsed dict builds it without casts under
  mypy.
- **`EmbedOptions.digest()`** is a method, so S7's recording names and 03's
  `vector:<projection>:<space-digest>` can reuse the one spelling.
- **The write is normalised.** Only the keys the mode uses are stored, and
  mode `none` with no dimensions stores no block, so `{"input": "none"}`
  removes the block as `{}` does. This keeps the file and the identity
  saying the same thing.
- **`embed_sync` checks that the space and its options agree.** A space id
  carries the `embopt1` tag exactly when its options are non-default, and
  that tag's digest is theirs; a mismatch is a `ValueError` before any
  meter. This enforces the "one object" invariant at the door for any
  hand-built endpoint dict. The spec only states the invariant.
- **`check_sendable` in the client and the operation.** The client refuses
  `param` and `dimensions` in this slice, so a hand-built `EmbedOptions`
  can never send a body that disagrees with its space. The spec's interim
  rule names only the validator.
- **The probe reads options strictly** (gate finding 6). With the facts
  file held, mangled or the block invalid, the `embed` probe sends nothing
  and reports `{"ok": False, "kind": "options_unreadable" | "options_invalid",
  "error": <fixed sentence>}`, which is neither a verdict nor a halt.
- **A sweep in an options-problem window carries the OLD basis forward**
  (gate finding 3; see the Plan gate section).
- **The card's sentences** are new wording around the spec's phrases.
- **`moved_by` asks in an options-problem window only for an edit that moves
  `rev`** (gate finding 1; see the Plan gate section).
- **No frontend change.** `ModelFacts` gains no `embedding` /
  `embedding_invalid` type until S4, which is the slice that reads them.

## Plan gate (substitute review, 2026-10-10)

Review gates after this one: not run (speed mode); the Codex gates are owed.
The whole backend suite was not run either (speed mode): targeted tests and
the brief's guard list only, with CI on the pushed branch as the full gate.

There is no Codex CLI in this environment. An independent reviewer agent ran
the adversarial plan review against the spec and the code, and the
coordinator ruled. The verdict was "not ready": one blocking finding, four
should-fixes and six nits. It also confirmed every golden value. The
worktree was reset onto `b3204cc` (the integration tip, with the final
01h-S1's loop-object registry) before implementation. Where this section
and a task above disagree, this section rules. Each item, and how it was
folded:

- **1 [BLOCKING] A held or mangled facts file let a provider edit move the
  space unasked.** `moved_by` returned False whenever the new `_space` was
  None, which the strict read makes true while the file is unreadable. A key
  edit then landed unconfirmed and re-embedded later.
  - `config_moved` had the same hole.
  - Folded: `embed_space._space` returns the whole `EmbedAttempt`.
    `moved_by` asks (400 `confirm_embedding`) when either side's attempt
    has an `options_problem` and the edit is not rev-neutral
    (`before["rev"] != after["rev"]`).
  - A rev-neutral edit (a name, billing, the sampler fields) cannot move a
    space: id, rev, model and the facts file are all the same on both
    sides. So it still asks nothing, rather than raising a false "re-embeds
    your library" over a rename. This is the one narrowing of the
    reviewer's "whenever", and it is stated here for the final gate.
  - `config_moved` asks when either side's resolution has an
    `embed_options_problem`. It is only consulted when a legacy embedding
    key changed.
  - Route tests: a key edit of the role's provider with the facts file held
    (`_facts_held`), and with a mangled file, each answer 400
    `confirm_embedding`, and a rename in the same state answers 200.
- **2 [SHOULD] `canonical()` against its own default-set test.** Folded in
  Task 1: `field` and `doc_value` are emitted together, only when
  `input == "param"` and both `param_field` and `document_value` are
  non-empty. A query-side-only `param` setting is `{}`. The pinned goldens
  are unchanged, because the param goldens carry both.
- **3 [SHOULD] The continuity basis in an options-problem window.** Folded
  in Task 6, following the ruling: the OLD basis is carried forward.
  - `similarity.options_problem()` (over `embed_space.options_problem`)
    surfaces the reason.
  - `reconcile.discover` takes `space`/`model` from the stored basis and
    marks the sweep `failure`/`options_<problem>`, so nothing is reported
    rescored and every basis entry is kept.
  - The test asserts on the persisted `candidates.read(cid)["basis"]`.
  - `""` is never persisted in that case.
- **4 [SHOULD] Inventory.** Folded into "Current state" and Task 5's file
  list:
  - the nested `record`, `short` and both `once_bad` fakes in
    `test_context_semantic.py`;
  - the eight-key pin in `test_inference_embedding.py`.

  The "touched only to widen fakes" claim now names the two pins that gain a
  key.
- **5 [SHOULD] The Embedding card.** Folded in Task 3:
  - the options problem is checked before `missing`;
  - held and mangled are told apart (`OPTIONS_HELD`, `OPTIONS_MANGLED`,
    `OPTIONS_INVALID`);
  - the sentence names `llm_connections/<id>.facts.json`;
  - a mangled file is never told "try again".
- **6 [NIT] The probe read options fail-soft.** Folded:
  `resolve.strict_embed_options` is the one strict read, shared with
  `embed_attempt`. With a problem the probe sends nothing and reports
  `options_unreadable` / `options_invalid`, which is not a verdict.
- **7 [NIT] The validator's messages are fixed strings.** None interpolates
  the value it refuses.
- **8 [NIT] Lone surrogates.** A prefix or value containing a code point in
  `0xD800-0xDFFF` is refused (it would raise `UnicodeEncodeError` later,
  which is not an `EmbeddingsError`).
- **9 [NIT] No silent normalisation of a mode's fields.** A non-empty
  prefix outside `input: "prefix"`, or a non-empty `param_field` /
  `query_value` / `document_value` outside `input: "param"`, is refused.
  So `{"document_prefix": "x"}` with no `input` is a 400, not `{}`. A lone
  `dimensions_field` is still accepted, checked and dropped (spec §5.1
  names it canonically empty).
- **10 [NIT] pydantic1.** The coordinator runs it at integration.
  `FactsUpdate.embedding` is a plain `Any` field.
- **11 [NIT] The confirm wording.** Folded in Task 7: one sentence true of
  both a first `embed: yes` and an options change.
