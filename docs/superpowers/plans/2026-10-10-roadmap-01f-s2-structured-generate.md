# 01f-S2: Structured mode on `generate`, and the shared refusal re-send — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `inference.generate(schema=...)` asks each attempt for its
provider's structured mode exactly when that attempt's model is known (`yes`)
to take it, refuses before any client call a schema outside the portable
subset, a prompt that does not carry the schema, a prompt ending in an
assistant turn and a prompt with per-attempt tails, and answers a refused
field by re-sending each refusing attempt once without the mode, through one
helper `decide` shares. Delivers 01f-C1 (part) and 01f-C2 (full).

**Architecture:** the flag is per CALL (`inference._structured_chain`, new
targets), never written into the resolution. `resolve.structured_capable` is
the one rule both `_flag_structured` (decide) and `_structured_chain`
(generate) ask. The facade gains one invariant: `LLMClient._streamed` (and
`single`, which never sends a schema) sends every target unflagged when no
schema is given, so "flagged" means "was sent the envelope" on every path
and `llm._structured_share`'s premise is true by construction. The re-send
leaves `_ask` as `_without_refused_mode(error, send)` with a streamed twin
`_stream_without_refused_mode(error, open_stream)`, sharing the attempt
selection (`_resends`) and the error composition (`llm.routes_failed`).
`generate`'s `send`/`open_stream` closures are nested in `generate`, call
`client.complete`/`client.stream` on the caller's `usage`, and add the
refused call's `attempts` to the re-send's.

**Tech Stack:** Python 3.11 (`contextlib.aclosing`, `typing.TypeVar`),
pytest, the shared fakes (`FakeLLM`, `SequencedProvider`).

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01f-structured-generation-design.md` §3.2-3.6, §4 (C1, C2), §7, Slices (01f-S2).

## Global Constraints

- A `generate` call without a schema is byte-for-byte the call it was:
  `inference.generate` still returns `client.stream(...)` /
  `client.complete(...)` itself, with the resolution's own chain, and
  `schema=` is not passed (the existing `test_inference_generate.py` cases
  pin the chain object and `fake.schemas == [None]`).
- `decide` behaves exactly as before: `_ask` keeps its signature, its rows,
  its records, its `sent`, and the identity of the error it returns when a
  lone re-send fails (`test_a_refused_schema_is_retried_once_only` asserts
  `exc.value is second`). `test_inference_decide*.py` and
  `test_decide_chain_golden.py` pass untouched.
- `client.stream`/`complete` are spelled only in `inference.py`, with the
  receiver named `client` and the holder passed as `usage` (positional third
  argument) inside the top-level `def generate`, so
  `test_usage_guard.FORWARDERS` covers the nested closures.
- Pre-send refusals are `ValueError` (a `schemas.SchemaError` for the
  schema), raised synchronously from `generate`, after the existing
  resolution checks: no request, no holder stamp.
- The schema-in-prompt check: some message whose role is `system` or `user`
  has string content (or a text part) containing `schemas.render(schema)`.
- Tails are asked through a new public `PreparedMessages.tailed` property,
  not by reading `_tails`.
- Nothing passes a schema yet, so no route's request changes.

## Review Focus

- The resolution is never mutated: `_structured_chain` builds new targets
  (`dataclasses.replace`), and its fallback is `attempts[1]` only when
  `resolved.chain.fallback` rides.
- `unknown` and `no` are never flagged; only `yes`.
- The streamed wrapper re-sends only when the first stream raised
  `SchemaRefusalError` (which `_resilient` raises only before prose), and a
  re-send that fails after yielding prose is re-raised, never followed by
  another re-send.
- `attempts`: one row per `generate` call; the refused call's count read
  from the holder before each re-send and added after it (success or
  failure), so two re-sends accumulate.
- The facade invariant does not move decide (it always passes a schema) and
  leaves an unflagged target as the same object.

---

### Task 1: the capability rule and the per-call chain

**Files:**
- Modify: `backend/src/grimoire/store/inference/resolve.py`
  (`structured_capable`; `_flag_structured` uses it)
- Modify: `backend/src/grimoire/inference.py` (`_structured_chain`,
  `_check_structured`, `generate`)
- Modify: `backend/src/grimoire/model_guidance.py` (`PreparedMessages.tailed`)
- Test: `backend/tests/test_inference_generate.py`

**Interfaces:**
- Produces: `resolve.structured_capable(attempt: Attempt) -> bool`;
  `inference._structured_chain(resolved) -> wire.Chain`;
  `PreparedMessages.tailed: bool`.

- [ ] **Step 1: Failing tests** (`test_inference_generate.py`)
  - `test_a_schema_flags_exactly_the_attempts_known_to_take_it`: primary
    `yes`, fallback `unknown` → the sent chain's primary flagged, fallback
    not; `no` primary → unflagged; the resolution's targets are `==` and
    `is` what they were before the call.
  - `test_without_a_schema_nothing_is_flagged_even_when_capable`: the chain
    sent is the resolution's own object and `fake.schemas == [None]`.
  - `test_each_refusal_raises_before_any_client_call` (parametrised, both
    `stream` values): a schema outside the subset; a prompt missing the
    rendered schema (and one that spells it with `tojson`); a prompt ending
    in an assistant turn; a tailed `PreparedMessages` → `ValueError`,
    `fake.calls == 0`, holder `{}`.
  - The existing `test_a_schema_is_passed_through` becomes a conforming
    schema carried in the system message (and the schema reaches the fake).
  - A schema in a user message's text part (content parts) is accepted.
- [ ] **Step 2: Run** → fail. **Step 3: Implement.**

### Task 2: the shared re-send, and the facade invariant

**Files:**
- Modify: `backend/src/grimoire/inference.py` (`_resends`,
  `_without_refused_mode`, `_stream_without_refused_mode`, `_ask`,
  `generate`'s closures)
- Modify: `backend/src/grimoire/llm.py` (`_unflagged`; `_streamed` and
  `single` apply it when no schema; `_structured_share` docstring;
  `SchemaRefusalError`/`_resilient` docstrings name `generate` too)
- Test: `backend/tests/test_inference_generate.py`, `backend/tests/test_llm_structured.py`

- [ ] **Step 1: Failing tests** (a real `LLMClient` over `SequencedProvider`,
  OpenRouter targets, a refusal naming `response_format`, an isolated store
  for the meter cases):
  - a refusing primary with no fallback is answered by its re-send: two
    provider calls, the second without `schema`, same messages; the joined
    text is the re-send's;
  - a fallback refusing after the primary failed is re-sent (primary,
    fallback, fallback-again);
  - both refuse: two re-sends in route order; both re-sends fail → the
    composed `routes_failed` error (not a `SchemaRefusalError`);
  - one ledger row per `generate` call under a real meter, `status == "ok"`,
    `attempts == 2`; the holder's `attempts` sums across two re-sends;
  - no health observation for the refusal (the observer sees only the
    re-send's success);
  - the streamed path re-sends and yields only the re-send's text;
  - a streamed re-send that fails after prose is re-raised and not followed
    by another re-send;
  - a refusal of something other than the field is not re-sent.
  - `test_llm_structured.py`: a flagged target sent no schema is unflagged
    on the wire path: a 400 naming `response_format` is observed as an
    ordinary failure (not a `SchemaRefusalError`), and the holder's
    `ATTEMPTED` target is unflagged; an unflagged target passes through as
    the same object.
- [ ] **Step 2: Run** → fail. **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_inference_generate.py tests/test_llm_structured.py tests/test_inference_decide*.py tests/test_decide_chain_golden.py tests/test_usage_guard.py tests/test_routing_guard.py tests/test_operation_guard.py tests/test_import_guard.py tests/test_llm*.py tests/test_format2_play.py tests/test_evals.py` → PASS; ratchets at baseline.
- [ ] **Step 5: Commit** `01f-S2: structured mode on generate, and the shared refusal re-send`.

## Plan gate (substitute review, 2026-10-10)

No Codex CLI and no subagent tool in this session: a rigorous adversarial
self-review of the plan against the spec and the code stood in for
`/codex:adversarial-review`. Findings, each folded:

- **S1: the facade invariant could move the decide golden.** The golden
  records the `ATTEMPTED` target a capture names. Checked: every structured
  decide call passes `schema=`, so `_streamed` leaves its chain alone, and
  the native path (`decide_native`) is not touched at all -- it sends no
  schema and reads neither `_schema_refusal` nor the flag, so unflagging it
  would only move a golden for nothing. Scoped to `_streamed` and `single`.
- **S2: the in-prompt check reads the primary's list body only.** A
  `PreparedMessages` builds a fallback's variant from the same factory, so a
  template that renders the schema renders it in every variant; a tailed
  prompt (the one case that ends differently per attempt) is refused
  outright. Recorded, no change.
- **S3: identity of the returned error in `_ask`.** The helper raises
  `routes_failed(words)`, which for a lone route is the re-send's own error
  object, so `exc.value is second` still holds. A test-free refactor would
  have hidden a copy; the existing test pins it.
- **M1: the order of refusals.** The existing task/operation/empty checks
  run first (their messages are pinned), then the schema checks.
- **M2: a re-send passes `schema=` with an unflagged target**, as decide's
  `_once` does: the adapter is handed no schema (`_generate` reads the
  flag), and the invariant (flag means sent) holds because the target is
  unflagged.

## Review and final gate (substitute review, 2026-10-10)

An adversarial self-review of the slice diff against spec §3.2-3.6, C1 (the
S2 part) and C2 stood in for `/codex:review` and the final spec gate (no
Codex CLI, no subagent tool). Each acceptance item traced to a test:
flagging per capability (`yes` flagged, `unknown`/`no` not, a non-riding
fallback not sent), the resolution untouched (`is` and `==`), no envelope
without a schema, every pre-send refusal with no request and an empty holder,
the re-sends with `SequencedProvider` (primary, fallback, both, both failing
composed, a lone failure's own error object, another 400 not re-sent, an
unflagged attempt never re-sent), one row with summed `attempts` under a real
meter, no health observation, the streamed path (re-send text only; prose
then death is not re-sent; an early close closes the re-send), and the
`_structured_share` invariant. `test_inference_decide*.py` and
`test_decide_chain_golden.py` pass untouched. Folded:

- **Ratchets:** nesting four closures in `generate` raised its complexity
  (ruff C901). The two bodies moved to module level
  (`_joined_structured`, `_streamed_structured`), handed the first facade
  call (made inside `generate`, so still a forwarder) and the nested
  `resend`/`reopen` closures; the attempt summing is one context manager
  (`_counting_earlier`). mypy's `aclosing` bound needed `AsyncGenerator`
  annotations. Both at baseline with no baseline change.
- **Docstrings** that said only `decide` re-sends (`SchemaRefusalError`,
  `_resilient`) and that the flag is only a decide resolution's
  (`_structured_share`) now say what is true.

Not folded, with reason: the spec's acceptance names a `RecordingProvider`
for "no request sent"; `FakeLLM.calls == 0` with an untouched holder is the
same proof at the facade's own seam, and `RecordingProvider` has no
generation method to record one with.
