# 01f-S3: The per-call output cap — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `inference.generate(max_tokens=n)` caps each attempt's output at
`min(preset's, n)` on new per-call targets, built with the schema flags by
one pure function a capture can be handed (`inference.call_chain`);
`inference.cap_sent(target)` says whether an attempt's adapter puts the cap
on the wire; a refusal of a cap only the call carried is `CapRefusalError`,
worded as the call's. Completes 01f-C1.

**Architecture:** `wire.Sampling.call_cap` records the cap the CALL asked
for; `wire.Target.with_output_cap(n)` returns a new target whose sampling
`max_tokens` is `min(preset's, n)` (or `n`), with the preset's id, name and
scope kept. `inference.call_chain(resolved, *, schema=None, max_tokens=None)`
composes S2's `_structured_chain` and the cap; with neither it returns the
resolution's own chain object, so a plain call is unchanged. The model's own
maximum output (01i-C1) is not in this tree: the clamp is one named function,
`inference.clamp_to_max_output(target, requested)`, which returns `requested`
today and carries a TODO naming 01i-C1 for the coordinator to wire.
`llm._preset_refusal` raises `CapRefusalError` (a `PresetRefusalError`
subclass, same kind/status/code) when the refusal names `max_tokens` and no
other sent control, and the `max_tokens` the target sends is the call's cap.
`routes.common._record_prompt` adds `structured` and `call_cap` to the
captured sampling report only when set, so the prompt log says what a call
was sent with.

**Tech Stack:** Python 3.11 stdlib dataclasses, pytest, `httpx.MockTransport`
for the wire checks (as `test_llm_structured.py`).

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01f-structured-generation-design.md` §3.7, §3.9, §4 (C1), §7, Slices (01f-S3).

## Global Constraints

- `wire.py` stays stdlib-only; `with_output_cap` refuses a non-positive or
  non-int `n` (`bool` excluded) with `ValueError`.
- `generate` refuses `max_tokens` that is not an int in
  `[1, llm_sampling`'s `max_tokens` high bound (200000)]` with `ValueError`
  before any client call. **Drift, on purpose:** the spec says "a positive
  int"; a cap above the preset parameter's bound would be read by
  `llm_sampling.effective` as an invalid stored value and silently not sent
  (on Anthropic, replaced by the default), so it is refused instead.
- Adding `Sampling.call_cap` adds a key to `dataclasses.asdict(sampling)`,
  which four suites compare against frozen baselines recorded before the
  field existed. They project through `wire_kit.sampling_dict`, which drops
  `call_cap` only when it is None (a resolution never sets it). The
  baselines themselves are not regenerated.
- A call with neither `schema` nor `max_tokens` is byte-for-byte unchanged
  (`call_chain` returns `resolved.chain` itself).
- `CapRefusalError` is never re-sent without the cap; like any
  `PresetRefusalError` the fallback is not tried.
- **CapRefusalError's condition, made precise:** the spec says "when
  `sampling.call_cap` is set and the preset itself sent no `max_tokens`".
  After `with_output_cap` the params hold `min(preset's, n)`, so "the preset
  sent none of its own" is read as "the value sent is the call's cap"
  (`params["max_tokens"] == call_cap`): a preset cap below the call's is the
  preset's and keeps the preset's wording; a preset cap above it was not
  what went on the wire. (Equal values are indistinguishable and read as the
  call's, which names the value that was sent.)

## Review Focus

- The resolution is never mutated; each capped target is new.
- The fallback is capped too (`call_chain` caps every attempt), and a
  route-scoped preset that `fallback_sampling` copies onto the fallback is
  the primary's already-capped sampling.
- `cap_sent` is asked of the target as sent (after the cap): Claude SDK
  False, OpenRouter with a catalog omitting `max_tokens` False, OpenRouter
  with no catalog True (`unknown` is sent), Anthropic and OpenAI-compatible
  True.
- The Anthropic API's thinking budget stays held to half the cap
  (`llm_sampling` already does this from the params).
- `call_chain(resolved, schema=s, max_tokens=n) == ` the chain the facade
  was sent, for both stream modes.

---

### Task 1: `wire` and `inference`

**Files:**
- Modify: `backend/src/grimoire/wire.py` (`Sampling.call_cap`, `Target.with_output_cap`)
- Modify: `backend/src/grimoire/inference.py` (`call_chain`,
  `clamp_to_max_output`, `cap_sent`, `generate(max_tokens=)`)
- Modify: `backend/tests/wire_kit.py` (`sampling_dict`) and the four baseline
  projections
- Test: `backend/tests/test_wire.py`, `backend/tests/test_inference_generate.py`

- [ ] **Step 1: Failing tests**
  - `with_output_cap`: no preset → `max_tokens == n`, `call_cap == n`; a
    preset `max_tokens` below/above `n`; preset id/name/scope and other
    params kept; the original target unchanged; `0`, `-1`, `True`, `1.5`
    refused.
  - `generate(max_tokens=)`: both stream modes send every attempt capped,
    the resolution's targets unchanged, `call_chain(...) ==` the chain sent
    (with and without a schema); `0`, `True`, `200001`, `"5"` refused with no
    request; no `max_tokens` and no schema sends the resolution's own chain.
  - `cap_sent` per kind (above).
  - `clamp_to_max_output` returns `requested` (the 01i hook).
- [ ] **Step 2: Run** → fail. **Step 3: Implement.**

### Task 2: the wire, the refusal and the capture

**Files:**
- Modify: `backend/src/grimoire/llm.py` (`CapRefusalError`, `_preset_refusal`)
- Modify: `backend/src/grimoire/routes/common.py` (`_record_prompt`)
- Test: `backend/tests/test_llm_structured.py` (or a new
  `test_output_cap.py`)

- [ ] **Step 1: Failing tests**
  - Through a real `LLMClient` and `OpenRouterClient` over
    `httpx.MockTransport`: a capped OpenRouter attempt's body carries
    `max_tokens: n`; one whose catalog omits `max_tokens` carries none; an
    Anthropic attempt's body carries the cap as its required `max_tokens`;
    a Claude SDK attempt is sent no sampling (`ScriptedProvider` kwargs).
  - A 400 naming `max_tokens` on a call cap with no preset →
    `CapRefusalError` ("this call's output cap (max_tokens) was refused"),
    fallback not tried, an instance of `PresetRefusalError`; with a preset
    `max_tokens` below the cap → plain `PresetRefusalError` naming the
    preset; a refusal naming another sent control beside a call cap → plain
    `PresetRefusalError`.
  - `_record_prompt` (through `prompt_log`'s capture) records
    `structured`/`call_cap` when set and nothing new when not.
- [ ] **Step 2: Run** → fail. **Step 3: Implement.**
- [ ] **Step 4: Run** the S2 set plus `tests/test_inference_*.py tests/test_adapter*.py tests/test_prompt_log*.py tests/test_model_test_call.py` → PASS; ratchets at baseline.
- [ ] **Step 5: Commit** `01f-S3: the per-call output cap`.

## Plan gate (substitute review, 2026-10-10)

No Codex CLI and no subagent tool: a rigorous adversarial self-review of the
plan against the spec and the code stood in for
`/codex:adversarial-review`. Findings, each folded:

- **B1: `Sampling.call_cap` moves four frozen baselines.** Confirmed by
  running them with the field added (27 failures, every one the extra
  `asdict` key). Folded as the `wire_kit.sampling_dict` projection (Global
  Constraints), never a regenerated baseline.
- **S1: a cap past the preset parameter's bound is silently unsent.**
  `llm_sampling._check` marks `max_tokens > 200000` invalid, which
  `effective` drops (and the Anthropic API replaces with its default).
  Folded: `generate` refuses it, recorded as drift.
- **S2: "the preset itself sent no `max_tokens`" cannot be read off the
  capped target literally.** Folded as the precise condition in Global
  Constraints (the value sent is the call's cap).
- **S3: the model test call's own reply cap** (`routes.config`) reads
  `PresetRefusalError` by type. It never sets `call_cap`, so it never sees
  the subclass; `isinstance` still holds for anything that does.
- **M1: 01i is not in this tree.** The clamp is one public, documented
  function returning its input, so the coordinator's change is one line.

## Review and final gate (substitute review, 2026-10-10)

An adversarial self-review of the slice diff against spec §3.7, §3.9, C1 and
the S3 acceptance list stood in for `/codex:review` and the final spec gate
(no Codex CLI, no subagent tool). Each acceptance item traced to
`test_output_cap.py`: the cap on the wire where `cap_sent` is True
(OpenRouter body, the Anthropic API's required `max_tokens`) and absent on
the Claude SDK and on an OpenRouter model whose catalog omits it; the
resolution unchanged; a 400 naming `max_tokens` on a call cap raising
`CapRefusalError` (fallback untried, never re-sent uncapped) while a preset's
own lower cap keeps the preset's wording; `call_chain` equal to the chain the
facade was sent, with and without a schema, in both stream modes. Folded:

- **`call_chain(resolved) is resolved.chain`** cannot hold: `chain` is a
  property built per read. The test asserts equality, which is what "the
  resolution's own chain" means for a value type; the request is byte for
  byte what it was.
- **Ratchets:** mypy could not narrow `min(own, n)` inside a conditional
  expression (now an `if`), and ruff B008 flagged a call in a test default
  (now a `None` default). Both at baseline with no baseline change.
- **A re-send keeps the cap:** a refusing attempt is re-sent as the target
  it was sent (`route.sent()`), which carries the cap; nothing strips it.

Not folded: the 01i clamp is a pass-through by design until 01i-S1 is
integrated (`inference.clamp_to_max_output`, TODO naming 01i-C1).
