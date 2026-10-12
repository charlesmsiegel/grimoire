# 02-S7: Decide kits for 11 and 09 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Review gates: substitute review (no Codex CLI in this environment); the Codex gates are owed.**

**Goal:** the two decide kits 11 and 09 wire in, with no route, registered
case, policy row or capture: those land with the consumers' call sites,
because a route may not land before the call site that decides its task.
Delivers 02-C5a (full) and 02-C5b (full).

**Spec:** §9, §10, §11 (C5a, C5b), Slices (02-S7).

## Design

- **`store/epistemic_access.py`.**
  - `AccessQuestion` and `build_items`: one item per pair, a `Choice`
    `access` over `CLASSES`, null allowed.
  - `access_of(questions, decision)` fails closed to `UNKNOWN`: every
    non-class answer, an item the reply never reached, and any item whose
    escalation `outcome` is not `answered`. A pair asked twice keeps the
    withholding reading.
  - `PERMISSIVE` is 01d-C2a's answer filter.
  - `policy()` is the 01d-C1 row 11 registers: `escalate_to="primary"` on
    `refused` and `low_margin` at `DEFAULT_MARGIN` per native kind, filtered
    to permissive answers, never sampled.
  - `ROUTE` is the route entry 11 adds; it is not registered.
- **`store/history_rerank.py`.**
  - `Candidate`, `Grade` and `UNGRADED`.
  - `build_items`: one `Score` per candidate over `LEVELS`.
  - `grades_of`: ungraded is `Grade(None, None)`, never 0; the distribution
    is kept only as reported.
  - 01e's `Rank` has landed, so `build_items_ranked` (2 to `MAX_RANKED` = 8
    candidates, with a pointwise question for a native stage) and
    `positions_of` (an unranked or unread candidate is None) ship too.
  - `ROUTE` is the `history_check` entry 09 adds.
- **Templates:** `epistemic_access/{item,question,class}.j2` and
  `history_rerank/{item,question,rank_context,rank_question,pointwise}.j2`.
  `verify_templates.py` holds them, and the templates README documents them.
- **Eval cases** `decide-epistemic-access` and `decide-history-rerank` are
  held in `evals/cases.KIT_CASES`, outside `CASES`. Their recordings are
  compliant, undecodable, off-option or off-scale, abstained, (leaky,)
  native and native-refused. `test_no_orphan_recordings` claims them through
  `KIT_CASES`.
- Both modules join the store facade's import list, so `dir(store)` does not
  depend on import order (the baseline gains both).

## Tests

`tests/test_epistemic_access.py`:
- the builder, refusing nothing a caller could legally pass;
- every failure withholds;
- a read class stands;
- an unreached item withholds;
- a low-margin `known` whose hop `failed` or was `skipped` is `UNKNOWN`;
- an answered hop stands;
- a twice-asked pair withholds;
- the real trigger evaluation fires only on a permissive low margin;
- the policy row passes `test_task_policy`'s rules over the kit's route;
- no route lands;
- the unregistered case's recordings replay.

`tests/test_history_rerank.py`:
- the builder;
- ungraded is None for each reason;
- a level reads with its reported distribution only;
- an unreached item is ungraded;
- the ranked form and its bounds;
- no route lands;
- the recordings replay.
