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

## Native decisions bodies (`native/`)

`native/<provider>/*.json` are whole response bodies of a provider's decisions
endpoint (slice H), replayed through `httpx.MockTransport` by
`tests/test_native_decisions.py`. They are not cassettes: the adapter is handed
the body as the endpoint would send it, and nothing matches on a prompt.

`native/openrouter/` -- hand-authored from OpenRouter's API reference,
https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request
(its `DecisionsResponse` schema and example response), and from the Jev
tutorial, https://openrouter.ai/blog/tutorials/how-to-use-jev/, both checked
2026-10-08; never recorded.

- `answered` -- one `noul`, one `choice` with the reserved `none` offered, and
  one `score` whose probability-weighted `score` (1.4) is fractional and whose
  argmax level (2) is not its rounding (1).
- `none` -- a `choice` answered with the reserved `none` key.
- `nullable_one` -- a one-option nullable choice answered with its option.
- `wrong_type` -- a `choice` question answered with a `noul`-typed answer.
- `unanswered` -- the envelope, with an empty `answers`.
- `malformed` -- a 200 with no `answers` envelope.
- `error_400` -- the error body: from the reference's error schema
  (`{"error": {"code", "message", "metadata"}, "user_id"}`), which is the
  chat API's documented error shape.
