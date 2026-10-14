# Roadmap 12-S7: the adoption gate

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax for tracking.

**Goal:** `evals/run.py --investigate continuity|question|rp|all` compares
each entry point's incumbent with the investigation on a synthetic corpus,
with correctness, text and selection leakage, the hidden route, and cost.
**Architecture:** `evals/investigation/` -- `synth` (recipes through the real
store APIs), `cases` (gold, canned scripts, `MARGIN = 0.2` stated in advance),
`graders`, `runner` (throwaway homes, rows harvested and folded in three
columns, the gate per entry point). Offline the investigation arm's model is
a canned script; `--live` uses the app's resolutions.
**Spec:** section 10, 12-S7.

## Decisions taken against the spec

- E1's arms (A, B, C with 10's planner) and the actor-perspective hidden route
  wait on 12-S6, which waits on spec 10: `--investigate rp` says so and exits
  1; the gate reports E1 absent and failing.
- The hidden-route check runs today for the narrator: no excerpt of a hidden
  post's scene carries a hidden word, and a search for each hidden word
  answers the same before and after the hidden posts are rewritten without
  it (amended after review: comparing against a word that appears nowhere
  read a correct semantic match as a leak, and an absorbed scene's search
  document is its summary, so search alone proved little).
- Offline incumbents make no call: E2's sweep abstains, E3's retrieval reports
  whether it showed a gold scene. An offline PASS is the machinery passing,
  never a reason to switch a default (`evals/README.md`, "Investigation").

- [x] Corpus, graders, runner, CLI flag; `test_investigation_evals.py` runs the offline arms and plants each grader's positive and negative.
- [x] `evals/README.md`: the arms, the lines and the recording format for 10.4.
