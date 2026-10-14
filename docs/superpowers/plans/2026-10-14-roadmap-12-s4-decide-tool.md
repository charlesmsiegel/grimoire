# Roadmap 12-S4: Decision as a tool for maintenance modes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax for tracking.

**Goal:** E2 and E3 may ask a closed sub-question through 01g-C5's decide
tool, on 12's own `investigation_decide` route.
**Architecture:** `routes/investigation._decide_tool` resolves
`investigation-decide` softly (`_soft_resolved`), builds the tool with
`tool_decision.decision_tool` (the run's spend ceiling as its guard, the
seed's bound context, a campaign-level decision capture fenced on the run),
and wraps it to note a trace step per call. `max_decisions` is the budget's
(2 for E2, 1 for E3). A refused resolution, or a stage the ceiling cannot
price, leaves the tool out and the run proceeds.
**Spec:** section 4.4, 12-S4.

- [x] Route `investigation_decide` (`decide`, Decision role, `NO_LEGACY`) with its literal call site.
- [x] Offered in the continuity and question toolsets; absent on a soft refusal (`test_investigation_routes.py`).
- [x] Task policy, fakes, inference settings and routing tests name the new decide task.
