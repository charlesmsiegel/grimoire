# Canned LLM replies

Reply bodies for the fakes in `tests/llm_fakes.py`, so a test that needs a
well-formed absorb payload does not carry a 2 KB JSON string inline.

A **cassette** is `{"note": ..., "entries": [{"when": {...}, "reply": ...}]}`.
`when` holds substring predicates over the request (`system_contains`,
`user_contains`, `contains`); entries are tried in order and the first match
wins, so specific entries come first. `reply` is a string, or a list of strings
streamed as deltas. A request that matches nothing raises `CassetteMiss` naming
what was tried — a cassette never falls through to a default, because a silent
default is how a fake keeps a test green after the code stopped calling what the
test thought it called.

## These are not recordings

Nothing here was captured from a provider, and no code in this repository calls
one during tests. Each body is what a *well-formed* reply of that kind looks
like, written by hand and frozen. That makes them worth what a replay fixture is
ever worth:

- **They catch parsing and prompt-shape regressions.** If a parser stops
  accepting a valid payload, or a template moves so far that a cassette matcher
  no longer matches, a test fails.
- **They prove nothing about the model.** No fixture — not even one recorded
  live at temperature 0 — is evidence that a model would answer this way today.
  Model output moves under you, so a cassette hit means "the code handled this
  reply correctly" and never "the model says this".

Whether the model still *follows* an instruction is the question `evals/run.py
--live` answers, and only that one.

## Keeping them honest

`tests/test_llm_fakes.py` renders each prompt template and asserts the phrase
its cassette entry matches on is really in it. That is the link that would
otherwise rot: without it, a reworded system prompt would leave every matcher
silently dead, and the tests would go on passing against replies the code would
never have asked for.
