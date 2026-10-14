# Roadmap 12-S1: the narrator-side toolset

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax for tracking.

**Goal:** Read-only `ToolSpec`s over a campaign's history and ledger, grouped per
mode (`rp_narrator`, `continuity`, `question`), each answering 12's JSON envelope.
**Architecture:** A new read-only package `store/investigation/`: `state.py`
(the run-local `RunState`), `readers.py` (the scene-free, best-effort readers
and the prompt-view transcript readers), `tools.py` (the envelope, the tool
functions, `toolset(mode, state, ...)`). History search is 09's `retrieve`,
asked for a whole campaign with an explicit depth; nothing here calls a model.
**Tech Stack:** Python 3.11+, dataclasses, 01g `tool_calls`.
**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-12-bounded-agentic-investigation-design.md`,
12-S1, sections 4.1-4.3, 12, 14 ("Toolset").

## Re-checked against main (spec section 15)

- 01g's tool type is `tool_calls.ToolSpec`, not `Tool`. A terminal spec is
  never executed by the loop; its `fn` is a stub that raises.
- 08's `expand` has **no admission predicate** (15.2): rp_actor stays without
  `get_scene_excerpt` (S6's concern; not built here).
- 09's `retrieve(cid, sid, query, ...)` scores only scenes before `sid` and
  returns nothing while `history_recall_depth` is 0. 12 needs a whole-campaign,
  setting-independent search, so two narrow additions land here:
  `sid=""` means "no current scene: every live, unclosed scene is eligible",
  and `depth=` (a positive int) stands in for the setting's depth (rerank off,
  the setting's threshold when set). Both keep every existing call byte-identical.
- 01g already exposes the pre-reservation priceable check as
  `inference.tool_run_refusal(task, resolved, budget)` (15.3): no local copy.

## Global Constraints

- Placeholder names only (Seraphine, Mara, Winifred, Realm, Saltmarch); every
  test sets `GRIMOIRE_HOME` to a temporary directory.
- `TOOL_RESULT_CHARS = 6000`, `HISTORY_ROWS = 12`, `search_history.max <= 6`,
  `query <= 200` chars, `get_scene_excerpt.max_posts <= 8`,
  `read_scene_window.limit <= 12`.
- Envelope: `{"ok": true, "items": [{"id", "ref", "title", "text", "truncated"}]}`
  or `{"ok": false, "error": not_found|not_in_mode|bad_args|unavailable}`.
- `not_found` also answers "not visible"; a visible-but-empty search answers
  as a search over nothing.
- No tool takes `campaign_lock`; sync tools take `best_effort_campaign_lock`.
- History retrieval always `tier_limit=2`, perspective `narrator` in N/C/Q.
- The package appears in no `store/locks.py` list (read-only).

## Review Focus

- A path-shaped ref (`scene:../x`, `thread:a/b`) answers `not_found`, never a read.
- A hidden post or director note in a window or excerpt never reaches text.
- A depth-ranged regex rule in a window applies at the whole transcript's depth.
- A gm-only entity's body never appears; its name may (N/C/Q).
- A tool whose reader raises answers `unavailable` and the run continues.

### Task 1: `retrieve` for a whole campaign

**Files:** modify `store/history/retrieval.py`, `store/history/settings.py`;
test `backend/tests/test_investigation_tools.py`.

- [x] Test: with `history_recall_depth` unset, `retrieve(cid, "", Query(texts=("ferry",)), ceiling=1500, embed_client=None, deadline=..., depth=4, origin="investigation")` returns the matching earlier scene; existing callers unchanged (suite).
- [x] Implement `settings.explicit(depth, cfg=None) -> Settings`; `_prepare(..., depth=None)`; `_eligible(cid, "")` admits every unclosed live document; `retrieve(..., depth=None)` passes it through.

### Task 2: readers and tools

**Files:** create `store/investigation/{__init__,state,readers,tools}.py`;
modify `backend/tests/test_regex_prompt_guard.py` (scan the package, pin
`readers.window`, `readers.excerpt`, `readers.summary`); create
`backend/tests/test_investigation_writer_guard.py`; generalise
`backend/tests/test_continuity_writer_guard.py` to `(package, modules)` pairs
with `grimoire.store.investigation`; CONTRIBUTING.md guard table.

**Interfaces:** `state.RunState(cid, sid, mode, perspective, inspected: set[str], evidence: dict, steps: list, embed_client=None)`;
`tools.MODES = ("rp_narrator", "continuity", "question")`;
`tools.toolset(state, *, extra=()) -> tool_calls.Toolset`;
`tools.TERMINAL[mode]`; `tools.envelope_ok(items)`, `tools.envelope_error(code)`.

- [x] Tests (14 "Toolset", N/C/Q): every tool offered only in its modes; unknown and path-shaped refs `not_found`; hidden post absent; regex depth in a window; gm-only name-only; `search_history` passes `tier_limit=2` and narrator; `get_actor`/`get_timeline` with no scene and a patched `campaign_lock` that fails; truncation at a post boundary sets `truncated`; a raising reader answers `unavailable`.
- [x] Guards: the investigation writer guard and the generalised continuity guard each fail on a planted violation.

## Execution record

- `search_history` builds its query with a new `history.query.for_text(cid,
  text, perspective=)` (09's catalog rules over free text, with no scene to
  scan): a hand-built `Query` carries no terms, and `_prepare` takes terms
  only from the query it is handed.
- The summary reader reads no transcript, so the regex guard pins `window`
  (by `PINNED`) and `excerpt` (by `EXPANSION_PINNED`) and not `summary`.
- `test_continuity_writer_guard.py` scans `(package, modules)` pairs; the new
  `test_investigation_writer_guard.py` is an allowlist of readers per watched
  module (so a mutator added tomorrow is caught unnamed), with
  `reconcile.persist_investigation` callable only from the route module.
- Gates: no Codex CLI in this session; `@codex review` on the PR stands in
  (CLAUDE.md). Plan gate, review and final gate stay unticked until it lands.
