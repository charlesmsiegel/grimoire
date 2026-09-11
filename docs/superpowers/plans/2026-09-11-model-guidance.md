# Automatic model guidance implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Select editable scene guidance automatically for each requested model.

**Architecture:** A store-free `PreparedMessages` list owns frozen model variants.
Context packs each variant from one rendered scene snapshot; the facade selects
at dispatch, and the route binds best-effort capture after claiming the turn.

**Tech Stack:** Python >=3.11, existing Jinja2, FastAPI and pytest.

**Spec:** ../specs/2026-09-11-model-guidance.md

## Global constraints

- No dependencies; use `prompts.templates_dir()` and existing atomic store APIs.
- No private campaign content; use the existing placeholder set.
- Unknown models preserve ordinary composition; plain-list utility prompts stay unchanged.
- Freeze random macros and budget before attempts; guidance is packed and inspected.
- Sampling and reasoning API parameters remain unchanged.

## Task 1: Frozen context variants

Files: new `backend/src/grimoire/model_guidance.py`,
`backend/tests/test_model_guidance.py`, `templates/scene/model_guidance/glm-5.3.j2`,
`templates/scene/sections/model_guidance.j2`; modify context `assemble.py`.

Interfaces:

```python
PreparedMessages(primary_model, factory)  # list, .breakdown, .on_variant
prepared.for_model(model)  # copied list; cached (messages, breakdown) from factory
freeze_profiles(**data)  # exact model stem -> rendered text
guidance_for(model, profiles)  # exact lookup plus explicit provider alias
compose_turn(..., model="")  # same addition on opener/director/build/inspector
```

- [x] Write failing tests for exact/unsafe IDs, alternate template root, unknown
  no-op, disabled layout, macro stability, copy isolation and callback failure.
- [x] Implement safe discovery, frozen variants and one optional LOCK_IN section.
- [x] Test a longer fallback that changes packing, and known-to-unknown removal:

```python
assert profile_text in prepared.for_model("glm-5.3")[0]["content"]
assert profile_text not in prepared.for_model("vendor/unknown")[0]["content"]
assert primary_breakdown["sections"] != fallback_breakdown["sections"]
```

- [x] Run `pytest backend/tests/test_model_guidance.py backend/tests/test_context.py`.

## Task 2: Route selection and capture

Files: `llm.py`, routes `common.py`, `scenes.py`, `greetings.py`, `mechanics.py`,
`store/prompt_log.py` documentation, shared `tests/llm_fakes.py`, new
`tests/test_model_guidance_routes.py` and `tests/test_model_guidance_dispatch.py`.

- [x] Add route tests and demonstrate historical-stamp mismatch fails.
- [x] Dispatch prepared messages using the requested connection model:

```python
if isinstance(messages, model_guidance.PreparedMessages):
    messages = messages.for_model(effective_model(conn))
```

- [x] Pass `model=effective_model(conn)` to every scene composer and capture.
  Bind fallback capture only after claim; inspect standing routing with no key check.
- [x] Use shared providers to test actual fallback dispatch, same-model retries,
  strict folding, plain-list isolation and capture; check reroll override stays local.
- [x] Run new tests plus prompt-log, routing, LLM, mechanics and opener suites.

## Task 3: Documentation and verification

- [x] Document adding exact model templates, explicit aliases, fallback behavior,
  layout disabling and evidence limits in templates README and GLM notes.
- [x] Run template harness, frozen snapshot, architecture guards, ruff and mypy
  ratchets; attempt `make check` and record unavailable tooling honestly.
- [x] Review final diff against spec and address concrete findings.

## Review ledger

Spec and plan independently reviewed by prompt_review: PASS. Work continues in
the existing checkout to preserve the user's related, already reviewed prompt
edits; no commit, merge, push, or live model call is part of this task.

Implementation review ruling: prepare the finite empty/profile compositions
before dispatch, then select and capture on demand. This freezes Jinja include
dependencies as well as direct templates without replacing the loader.
The tradeoff is one packing pass per distinct installed profile plus the empty
profile, even when fallback is unused; capture-disabled unbounded composition
still avoids tokenization. Only one nonempty profile ships initially.

## Verification result

- Independent implementation review and final diff-versus-spec review: PASS.
- 414 context/layout/comparison/import/path and model tests passed after eager
  preparation; 400 composition/integration tests also passed.
- 169 route/LLM/prompt-log/routing tests passed.
- Broad scene/run/guard/frozen suite: 1,096 passed; its remaining failure was
  an old response-preset spy rejecting the newly added model keyword. The spy
  now forwards and asserts that keyword; its rerun with the model route and
  dispatch suites passed all 15 cases. No production change was needed.
- Template harness: all 126 checks passed.
- Ruff: 1,180 existing findings, all at baseline.
- Mypy with CI's Linux target: 181 findings, all at baseline. The assembly
  change removed one argument-type finding and the baseline was reduced.
- Native Windows mypy reports platform-specific attribute findings in the
  unchanged atomic/proclock modules; those disappear with the Linux target.
- `make check` could not start because Make is not installed. No full
  frontend, pydantic-v1, APK or coverage gate is claimed.
- No live GLM call or dialogue-quality benchmark was run. Changes are left
  uncommitted in the working tree.
