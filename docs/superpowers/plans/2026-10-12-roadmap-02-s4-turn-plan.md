# 02-S4: Turn plan, dark — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Review gates: substitute review (no Codex CLI in this environment); the Codex gates are owed.**

**Goal:** `decide_turn_plan=plan` asks the stance plus disclosure and
tension in one item and one call, in place of the intent call, with an
`extra` slot of whole items owned by another feature. Delivers 02-C3 (full).

**Spec:** §7, §11 (02-C3), Slices (02-S4).

## Design

- **`store/turn_plan.py`:**
  - `disclosure_question()`: a `Choice` over `DISCLOSURES`, null allowed;
  - `tension_question()`: a `Score` over `TENSION_LEVELS`;
  - `plan_item(name, conversation, brief)`: the intent's context with
    (stance, disclosure, tension);
  - `Extra(owner, item, read=None)` and `plan_items(plan, extra)`, which
    refuses duplicate or empty owners;
  - `plan_answers(result)`: disclosure and tension as answered, a value the
    vocabulary does not know read as None;
  - `extra_records(extra, results)`: each owner's `read(result)`, or the
    result's capture form, under its owner's key.
- **Routing.** `turn-plan` joins the `turn_plan` route (so
  `NO_LEGACY_TASKS`). Its `TaskPolicy` is `question="stance", samples=True`.
- **The call site.** `_PLAN_TASKS` maps `intent` and `plan` to their tasks.
  `_turn_plan(..., mode)` resolves and decides under the mode's literal
  task. Shared inputs come from `_turn_inputs`, the regex-guarded reader.
  In plan mode `_plan_extras` (the seam 13-C3 fills; 02 returns none) adds
  extra items to the same `decide()`. Only the stance is drawn. The record
  gains `disclosure`, `tension` and, when there are extras, `extra`. The
  block renders the plan's three lines and nothing of the extras.
- **Eval.** `decide-turn-plan` covers compliant, undecodable, forthcoming
  (disclosure and tension wrong) and off-scale (a tension level read as
  None).
- `verify_templates.py` holds the plan item's questions and options. The
  templates README describes the plan.

## Tests

`tests/test_turn_plan.py`:
- one call carrying three questions, and its block and record;
- unread answers render nothing;
- disclosure and tension are the answer beside a report, and only the
  stance is drawn;
- extra items ride the same call and are stored by owner (read and capture
  forms) and rendered by nobody;
- duplicate owners skip the plan as `error:ValueError`;
- intent and plan never both run.
