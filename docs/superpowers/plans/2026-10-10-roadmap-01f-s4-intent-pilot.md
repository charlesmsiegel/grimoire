# 01f-S4: The `intent` pilot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the scene-intent extraction (`POST /campaigns/{cid}/scene-intent`)
is the first generation sent with a schema: its system prompt renders the
intent schema with `schema_json`, and the call goes through
`generate(schema=)` so a provider known to take structured mode is held to
it -- the location and cast enums are what the mode buys. Delivers no
contract item (every 01f contract is already delivered); it is spec §3.8's
pilot adopter.

**Architecture:** `store.suggest.intent_schema(snapshot)` builds the schema
from the SAME snapshot the prompt renders (`build_snapshot(drivers=False)`):
`title` and `date` strings; `location` an enum of `""` plus the available
location ids; `cast` an array whose items are an enum of the available cast
tokens. A field with nothing to offer (no locations; no available cast) is a
plain string / array of strings, because an empty enum is refused; a
campaign whose enums would break a strict-mode budget drops the cast enum,
then the location enum, until `schemas.check` passes.
`suggest.build_intent_request(cid, typed, offscreen) -> (messages, schema)`
builds both from one snapshot read; `build_intent_prompt` stays the
messages-only view of it (goldens, the frozen sweep, `verify_templates`).
The route takes both and hands the schema to `common.draft_completion`,
which gains `schema=` and `max_tokens=` keywords passed to `generate` only
when given, so the other fourteen draft routes are unchanged. The reply is
parsed by `parse_intent` exactly as before (it already drops unknown ids).

**Tech Stack:** Jinja2 (`schema_json`), pytest, `scripts/verify_templates.py`,
the frozen-campaign sweep.

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01f-structured-generation-design.md` §3.3, §3.6, §3.8, §7 (the pilot), Slices (01f-S4).

## Global Constraints

- **Drift, on purpose: the schema is built in `store.suggest`, not in the
  route.** The spec says `routes/scenes.post_scene_intent` builds it. Built
  there it would need its own read of the campaign's locations and cast,
  which could list ids the prompt (rendered from the snapshot) does not --
  a race the schema-in-prompt rule exists to prevent. The route still
  obtains the schema and passes it (`build_intent_request`).
- The location enum includes `""`: the prompt asks for `""` when the text
  implies no location, and an enum without it would forbid that answer on a
  provider that enforces the schema. Never an empty enum.
- `templates/scene_intent/system.j2` gains one paragraph at its end (after
  the shared `date_notation.j2` include): the schema, rendered
  `{{ schema | schema_json }}`. Nothing else in the prompt moves; the user
  message is byte-identical.
- `test_suggest_golden.py`'s golden is **not regenerated** (it is promised
  never to be). Its intent check pins the user message exactly and the
  system message as the golden text plus exactly the schema paragraph, so
  the one deliberate change is named in the test rather than absorbed into
  a rewritten file.
- The frozen campaign's `snapshot.json` IS regenerated (CLAUDE.md: when a
  template moved on purpose and the new text was reviewed), and the diff is
  checked to touch only the two `suggest.build_intent_prompt[...]` keys.
- `campaign_flow` and every other cassette hold no intent entry (checked:
  no fixture matches the intent prompt), so no cassette changes; the spec's
  "cassette updated" is moot here, stated rather than skipped.
- The pilot passes no `max_tokens` (spec 3.8 names only the schema for it);
  the keyword lands on `draft_completion` for the next adopter, with a test.

## Review Focus

- The schema in the prompt is the schema on the wire (one snapshot, one
  dict, `schemas.render`), and `generate`'s in-prompt check passes for every
  campaign shape: none, locations only, cast only, both, past the budget.
- Offscreen: the snapshot already leaves the player out of `cast`, so the
  cast enum does too, and `parse_intent(offscreen=True)` still drops a
  player token.
- The other draft routes' requests are unchanged (`draft_completion` passes
  neither keyword unless given).

---

### Task 1: the schema and the prompt

**Files:**
- Modify: `backend/src/grimoire/store/suggest.py` (`intent_schema`,
  `build_intent_request`; `build_intent_prompt` delegates)
- Modify: `templates/scene_intent/system.j2`, `templates/README.md`
- Modify: `scripts/verify_templates.py` (the intent system render passes
  `schema=`)
- Modify: `backend/tests/test_suggest_golden.py` (the system message pinned
  as golden + the schema paragraph)
- Regenerate: `backend/tests/fixtures/frozen_campaign/snapshot.json`
- Test: `backend/tests/test_suggest_store.py`

- [ ] **Step 1: Failing tests** (`test_suggest_store.py`)
  - `intent_schema` over a hand-built snapshot: enums when there is
    something to offer (`""` first in `location`), plain string / string
    array when not; it passes `schemas.check`; a budget-breaking cast falls
    back to a plain string array while the location enum stays.
  - `build_intent_request` returns the messages `build_intent_prompt` does
    and a schema whose `schemas.render` is in the system message.
- [ ] **Step 2: Run** → fail. **Step 3: Implement.**

### Task 2: the route and `draft_completion`

**Files:**
- Modify: `backend/src/grimoire/routes/common.py` (`draft_completion(...,
  schema=None, max_tokens=None)`)
- Modify: `backend/src/grimoire/routes/scenes.py` (`post_scene_intent`)
- Test: `backend/tests/test_routes.py` (intent section)

- [ ] **Step 1: Failing tests**
  - the route's call carries the schema (`FakeOpenRouterComplete.schemas`)
    and its system prompt carries `schemas.render(schema)`;
  - a conforming reply parses as before (the existing
    `test_scene_intent_resolves_names` passes);
  - a reply naming a location and a cast token outside the enum has both
    dropped (`location` None, `cast` without it);
  - `draft_completion` with `max_tokens` sends a capped chain, and without
    either keyword calls `generate` exactly as before.
- [ ] **Step 2: Run** → fail. **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_routes.py -k "intent or suggestion" tests/test_suggest*.py tests/test_llm_fakes.py tests/test_frozen_campaign*.py tests/test_evals.py`, `$PY scripts/verify_templates.py` → PASS; ratchets at baseline.
- [ ] **Step 5: Commit** `01f-S4: the intent pilot`.

## Plan gate (substitute review, 2026-10-10)

No Codex CLI and no subagent tool: a rigorous adversarial self-review of the
plan against the spec and the code stood in for
`/codex:adversarial-review`. Findings, each folded:

- **B1: the location enum without `""` would forbid the prompt's own "no
  location" answer** on any provider that enforces the schema. Folded:
  `""` is the enum's first value (Global Constraints).
- **B2: two goldens pin the intent prompt.** `test_suggest_golden.py` is
  never regenerated, so its check names the one deliberate change (the
  schema paragraph) instead; the frozen sweep is regenerated deliberately
  and its diff is checked to be the intent keys alone.
- **S1: a schema built in the route could disagree with the prompt.**
  Folded as the drift above: one snapshot, one schema, handed to the route.
- **S2: the budget fallback must end inside the subset.** The last step
  (no enums at all) is two plain fields, which always passes `check`; a test
  forces the fallback.
- **M1: `store.suggest` binding `schemas`.** The import rule allows a store
  module to bind a gateway leaf module (`from ... import schemas`); the
  leaf imports nothing, so the graph stays acyclic (`test_import_guard.py`).

## Review and final gate (substitute review, 2026-10-10)

An adversarial self-review of the slice diff against spec §3.8 and the S4
acceptance list (and, as the last slice, of the whole 01f diff against the
spec's C1-C3 and §7) stood in for `/codex:review` and the final spec gate (no
Codex CLI, no subagent tool). Acceptance traced to tests: the prompt carries
the rendered schema (route and store tests); a conforming reply parses as
before (`test_scene_intent_resolves_names`, unchanged); an out-of-enum
location and cast token are dropped by `parse_intent`; `verify_templates.py`
and `test_llm_fakes.py` green. Folded:

- **The harness's own Jinja environment had no `schema_json`.**
  `verify_templates.py` builds an independent environment; it now registers
  the same `schemas.render` (not a copy), so builders and templates still
  agree on one spelling.
- **The S4 plan file had landed in the S3 commit** (it was written before S3
  was committed). Moved out of S3 by amending that commit before S4.
- **A test helper shadowed another.** The new store tests' `_snap` replaced
  `test_suggest_store.py`'s existing `_snap` at module scope, failing eight
  suggestion tests in the full-suite run; renamed `_intent_snap`.
- **The frozen sweep's diff** was checked mechanically: the same 78 keys,
  only the two `suggest.build_intent_prompt[...]` entries changed, each
  user message identical and each system message the old text plus exactly
  the schema paragraph.

Not folded, with reason:

- The prompt-log viewer (`SamplingSummary.tsx`) does not draw the new
  `structured`/`call_cap` report keys; the capture carries them and the cap
  already shows as the applied `max_tokens`. A display change is frontend
  work outside this backend slice, and no caller records a structured
  generation's prompt yet (`draft_completion` records none).
- `campaign_flow` and every other cassette have no intent entry, so there was
  no cassette to update; the spec's "cassette updated" is moot here.

Full backend suite (`-n 4`) after the fold: every test passes but
`test_atomic.py::test_a_read_only_record_is_not_silently_replaced`, which
fails on `main` too because the container runs as root.
