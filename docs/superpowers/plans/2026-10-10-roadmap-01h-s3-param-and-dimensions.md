# 01h-S3: Request-field input type and requested dimensions — Plan

**Goal:** a model's stated embedding options may use the `param` input mode
(the type sent as a request field) and a requested `dimensions`. The client
sends both, splits a `param` call at the query/document boundary under the
call's one meter, checks every returned width against a requested one, and
names a width-ignoring endpoint `missing_key`/`dimensions_mismatch` (with
`returned_dims`). The error row files that code beside the kind and status,
and the model test reports the mismatch as a failed `embed` probe carrying
both widths. Delivers 01h-C1 in full (adds `param`) and 01h-C2 in full.

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01h-embedding-options-design.md`
§3.2 (validation), §3.4 (`param`, `dimensions`, 4xx stays `bad_response`),
§4.3, §4.4 (the probe's widths), §9 (C1, C2), §11 (Request bodies, Facts,
Probe), Slices 01h-S3.

**Review gates:** not run (speed mode); the Codex gates are owed.

**Open questions (§13):** Q6 (generic field versus per-provider presets):
the spec's recommendation, a generic `param_field` / `query_value` /
`document_value`; no provider preset is shipped. The others are not this
slice's.

## Design

- **No space id moves.** S2's `canonical()` already encodes `field`,
  `doc_value`, `dim` and `dim_field`; the golden ids in
  `test_embed_options.py` stay pinned and untouched.
- **Validation (`facts._check_embedding`).** The S2 interim refusals
  (`PARAM_NOT_YET`, `DIMENSIONS_NOT_YET`) are deleted.
  - `param` needs `param_field` and both values (`EMBED_NO_PARAM`).
  - `dimensions` is a JSON integer -- a `bool` or a float (`512.0`) is
    refused -- with `1 <= dimensions <= embeddings.MAX_DIMS`
    (`EMBED_BAD_DIMENSIONS`), in any mode.
  - The stored block keeps the mode's fields, plus `dimensions` and a stated
    `dimensions_field` when `dimensions` is set. A lone `dimensions_field`
    is still checked and dropped. Mode `none` with dimensions stores
    `{"dimensions": n}`.
  - `facts.embed_options` builds every field.
- **The client (`embeddings`).**
  - `check_sendable` no longer refuses `param`/`dimensions`; it still
    refuses (`ValueError`, before sending) a hand-built object whose `param`
    lacks a field or a value, or whose `dimensions` is not a positive int.
  - `request_body(model, chunk, options, query=False)`: `none`/`prefix` send
    exactly `{"model", "input"}` (byte-identical); `param` adds
    `<param_field>: <query_value | document_value>`; `dimensions` adds
    `<dimensions_field>: n`.
  - `embed` splits the inputs into segments: one for `none`/`prefix`; for
    `param`, the query texts then the document texts (an empty side sends
    nothing). Each segment is batched by `BATCH`. Vectors come back in input
    order. `first` (the `NOT_SENT` code) is true only for the call's first
    request.
  - `_post` checks every returned vector's width after `_vectors` when
    `dimensions` is set, and raises `DimensionsMismatchError`, an
    `EmbeddingsError("missing_key", code=DIMENSIONS_MISMATCH)` carrying
    `requested_dims` and `returned_dims`. The body was read and billed, so
    its usage is folded first (as for any read batch).
- **The operation (`inference.embed.record_failure`).** The error row's
  detail is the kind, the HTTP status and the code (`code <token>`), the code
  only when it is a fixed token (`[a-z_]{1,40}`), never provider text.
  Recall and search do not retry `missing_key`, so a width-ignoring endpoint
  costs one request per turn.
- **The probe (`routes/config._probe`).** A `DimensionsMismatchError` is a failed
  `embed` probe (`unknown`, never `no`) whose result and filed verdict carry
  `requested_dims` and `returned_dims`. It is filed (it is a verdict on the
  model's stated options at this endpoint) and halts nothing (the other
  probes ask other questions).
- `CLAUDE.md`'s options paragraph names the `param` mode and `dimensions`.

## Files

- `backend/src/grimoire/store/inference/facts.py`
- `backend/src/grimoire/embeddings.py`, `backend/src/grimoire/wire.py`
  (docstrings only)
- `backend/src/grimoire/store/inference/embed.py`
- `backend/src/grimoire/routes/config.py`
- `CLAUDE.md`
- Tests: `test_inference_facts.py`, `test_provider_api.py`,
  `test_embeddings.py`, `test_embed_options.py`, `test_inference_embed.py`,
  `test_model_test_call.py`, `test_context_semantic.py`.

## Tests

- Facts: `param` and `dimensions` save; `512.0`, `true`, `0`, `"512"` and
  `MAX_DIMS + 1` refused; `param` without a field or a value refused; the
  same-field rule; an on-disk `param` block now names a space.
- Request bodies: `param` sends the query request then the document request
  (two requests, one row, input order, one capture line); a mixed `param`
  batch over `BATCH` splits at both boundaries; `dimensions` adds the field
  under `none`, `prefix` and `param`.
- A wrong width is `missing_key`/`dimensions_mismatch` with
  `returned_dims`; its error row reads `missing_key, code
  dimensions_mismatch` and nothing else.
- Recall against a width-ignoring endpoint sends one request.
- The probe: options sent; a mismatch is `ok: false` with both widths, filed,
  and the cap reads `unknown`.
