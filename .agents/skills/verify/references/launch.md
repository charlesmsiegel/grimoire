# Launch isolated verification servers

## Isolate before importing

Set `GRIMOIRE_HOME` to a new scratch directory **before importing Grimoire**.
The environment variable alone does not isolate the bootstrap pointer or
machine-local locks. The shared pytest fixtures in
[conftest.py](../../../../backend/tests/conftest.py) already isolate `DEFAULT_HOME`,
`paths.pointer_path`, process locks and the recycle bin, including after a
test undoes its own monkeypatches. Reuse them for a temporary browser session.

Write the session in ignored scratch, with a
`test_session(client, monkeypatch, tmp_path)` function. Run it explicitly as a pytest file with
`-p tests.conftest -c backend/pyproject.toml -s` and a new `--basetemp` under
scratch. Put this checkout's `backend/src` and `backend` on `PYTHONPATH`; on
Windows separate them with `;`, on Unix with `:`. Use the installed backend
interpreter, explicitly selected in a worktree.

The `client` fixture owns the app's lifespan. To expose that same app in a
temporary uvicorn thread, use `client.app`, bind `127.0.0.1` on the chosen
backend port, and set `lifespan="off"` on this second server. Keep the pytest
session alive while driving the browser; arrange bounded waiting and a stop
signal, then set `server.should_exit` and join the thread before teardown.

## Keep model calls offline

Override `client.app.dependency_overrides[routes.get_llm]` with a shared fake from
[llm_fakes.py](../../../../backend/tests/llm_fakes.py), normally
`from_cassette("campaign_flow")`. Its replies come from
[campaign_flow.json](../../../../backend/tests/fixtures/llm/campaign_flow.json).
Do not invent an inline LLM fake or match prompts with ad hoc substring rules.
An unmatched cassette request should fail visibly.

Patch all four `grimoire.openrouter` globals to loopback mock routes as well:

| Global | Purpose |
|---|---|
| `API_URL` | Generation |
| `MODELS_URL` | Model catalogs |
| `KEY_URL` | Connection checks |
| `DECISIONS_URL` | Native decisions |

The shared LLM fake handles inference. If testing HTTP catalog/check behavior,
the local catalog route returns `{"data": [...]}` with a synthetic `test/model`
entry; key checks return the intended status. If those behaviors are outside
the session, a fail-closed local URL is sufficient. No unmatched request may
fall through to a hosted provider. Configure synthetic credentials only.

Keep the Embedding role unset unless the flow tests embedding; for that flow, replace `routes.get_embeddings` too.
Audit other adapter clients reached by the selected routes and mock their
endpoints. An LLM override alone does not isolate those clients.

Before seeding, require `GET /api/worlds` to return `[]`. If it contains existing
worlds, stop and correct isolation.

## Launch the frontend

From `frontend/`, with dependencies installed:

```sh
GRIMOIRE_API=http://127.0.0.1:8209 npx vite --host 127.0.0.1 --port 5209 --strictPort
```

PowerShell:

```powershell
$env:GRIMOIRE_API = 'http://127.0.0.1:8209'
npx vite --host 127.0.0.1 --port 5209 --strictPort
```
