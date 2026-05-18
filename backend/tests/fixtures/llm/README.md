# LLM golden fixtures

Checked-in JSON files under `by_hash/<sha256>.json` capture the responses
that golden-path tests (`pytest -m golden`) replay against.
`RecordReplayLLM` (see `backend/src/grimoire/testing/record_replay.py`)
keys each fixture by `request_hash(...)`, which hashes the model, system
prompt, messages, and sampling parameters that materially affect the
output.

## Re-record cadence

Re-record fixtures when **any of these change**:

- the upstream model version (e.g. provider releases a new revision and
  we bump the configured `model`),
- a prompt template used by the code under test (anything in
  `backend/src/grimoire/templates/`),
- the structured-output schema the LLM is asked to emit,
- the request shape itself (new params, reordered messages, etc.) — the
  hash will change, so REPLAY will fail with `FixtureMissingError`.

If REPLAY starts raising `FixtureMissingError` and none of the above
applied intentionally, suspect drift in upstream code that mutated the
request shape — diff before re-recording.

## How to re-record

```sh
cd backend
uv run pytest -m golden --record
```

This sets `RecordReplayLLM(mode=RECORD)` for tests pulling in the
`golden_llm` fixture. Tests that want recording also need a real gateway
wired in — the default fixture intentionally skips in RECORD mode so a
stray `--record` doesn't try to hit a provider. See individual golden
tests for how they construct a recorder.

Recorded fixtures land under `by_hash/`. Inspect them before committing
(anonymize names if the spec calls for it — see open question §5).

## Layout

```
fixtures/llm/
  README.md          (this file)
  by_hash/
    <sha256>.json    # one per recorded request
```

Each JSON has the shape `{"request": {...}, "response": {...}}`.
