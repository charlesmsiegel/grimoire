# Backend test isolation and fixtures

Source: [conftest.py](../../../../../backend/tests/conftest.py), [llm_fakes.py](../../../../../backend/tests/llm_fakes.py), [README.md](../../../../../backend/tests/fixtures/frozen_campaign/README.md).

- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`.
- **The frozen campaign** (`backend/tests/fixtures/frozen_campaign/`) is a whole
  store tree checked in as a fixture — the only store in the repo that today's
  code did not write, which is the only way to catch a change that breaks
  reading what an *older* version wrote. `home/` is **never regenerated**; its
  value is being old. `snapshot.json` (the expected output of the read-only
  sweep) *is* regenerated deliberately, when a template or render moved on
  purpose and the new text was reviewed — `cd backend && PYTHONPATH=src
  .venv/bin/python -m tests.fixtures.frozen_campaign.sweep` (on Windows:
  `cd backend; $env:PYTHONPATH="src"; .venv\Scripts\python.exe -m
  tests.fixtures.frozen_campaign.sweep`), committed with the change that moved
  it. See that directory's README.
- **Every route test builds a fresh app, and FastAPI's analysis of its routes
  is memoised per test process** (`backend/tests/route_memo.py`, installed by
  `conftest.py`). Building that analysis for every app was most of what a
  route test cost; the memo reuses it, and nothing else -- the app, its
  lifespan, its run registry and its dependency overrides stay per test. It
  leans on one private FastAPI function and switches itself off when that
  function is absent or its signature moves, so a FastAPI upgrade can make the
  suite slower without making it wrong; `test_route_memo.py` says which.
  `GRIMOIRE_TEST_ROUTE_MEMO=0` runs without it.
- **Faking the LLM**: use `backend/tests/llm_fakes.py`, injected at
  `app.dependency_overrides[routes.get_llm]` — never write another inline fake.
  Scripted turns (`FakeOpenRouter`, `FakeOpenRouterComplete`, …) answer by call
  order; a *cassette* (`from_cassette("campaign_flow")`) answers by what the
  request looks like, replaying hand-authored bodies from
  `backend/tests/fixtures/llm/`. A request matching no cassette entry raises
  rather than defaulting, and `test_llm_fakes.py` renders every real prompt
  template to prove the matchers still match — reword a system prompt and it
  fails there rather than silently everywhere else. These bodies are canned, not
  recorded: they prove the code handles a reply, never that a model would send
  one (`evals/run.py --live` is the only thing that answers that).


## Library birth and model-settings fixtures

- Nothing creates the store until the first API call that needs it, so the
  installers end by printing where it will land — `python -m grimoire.where`,
  which asks `store.paths` rather than assuming `~/.grimoire` (wrong wherever
  `GRIMOIRE_HOME` or the bootstrap pointer already names somewhere else).
  `scripts/unix/install.sh` and `scripts/windows/install.ps1` also check the
  Python and Node floors up front, and `test_install_scripts.py` holds those
  floors to `requires-python` and `engines.node`. A store this build creates
  is **born at the current model-settings format**: the first write of a
  missing `config.md` stamps the format marker (`config.birth_fields()`),
  so a fresh install is never migrated and no safety archive is taken of an
  empty library. `GRIMOIRE_INFERENCE_AUTOMIGRATE=0`, which `tests/conftest.py`
  sets, turns off the background migration only -- the birth stamp is always
  written, and `config.birth_fields()` is its one spelling. A test that wants
  the migration calls `migrate.ensure` itself, and a test that needs a format-1
  library calls `tests.inference_fixtures.legacy_store()` before anything reads
  the store (a `config.md` that exists without the marker stays legacy).
  The suite is **born an upgraded default library at format 2**:
  `tests/conftest.py` sets `GRIMOIRE_TEST_BIRTH=upgraded-default` at import,
  which adds the default Primary to the birth stamp, so every suite plays at
  the format a fresh install ships. A test about the product's own birth (the
  format and retirement markers alone) takes `@pytest.mark.product_birth`; one
  about the legacy layout takes the `legacy_client` fixture or calls
  `legacy_store()`.
  `test_format2_play.py` is the focused run of the turn path (a chat turn and
  a reroll) at that format.

For settings safety archives and retirement, read [migration](../inference/migration.md).
