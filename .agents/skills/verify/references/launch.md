# Launch isolated verification servers

Read the [shared test doubles](../../../../backend/tests/llm_fakes.py),
[current settings example](../../../../backend/tests/test_format2_play.py), and
[Vite proxy config](../../../../frontend/vite.config.ts) when constructing the launcher.

1. Choose unused loopback ports, for example 8199 and 5199. Set `GRIMOIRE_HOME`
   to an empty scratch directory **before importing Grimoire**. Use this worktree's
   `backend/src` on `PYTHONPATH`; include `backend` if importing `tests.llm_fakes`.
2. Build the app with `main.create_app()`. Override
   `app.dependency_overrides[routes.get_llm]` with a shared fake such as
   `from_cassette("campaign_flow")`. It implements the current typed-target
   gateway, including generation, native decisions, model lists and health checks.
   Cassette misses must fail visibly; read the cassette's supported prompts rather
   than inventing fallback replies.
3. Keep the Embedding role unset unless the flow tests embedding. For that flow,
   replace `routes.get_embeddings` with a local fake too. Audit other provider
   clients reached by the chosen routes; an LLM override alone does not mock them.
4. If testing the real HTTP adapters, use a loopback HTTP mock instead. OpenRouter
   has **four** doors: `API_URL`, `MODELS_URL`, `KEY_URL` and `DECISIONS_URL` in
   [openrouter.py](../../../../backend/src/grimoire/openrouter.py). Patch all four;
   generation-only mocking leaves catalog, health or native calls live. Embeddings
   and other adapters have their own endpoints and need their own mocks.
5. Run uvicorn on the chosen loopback port and retain its process handle.
   From `frontend/`, start Vite with a strict, distinct port:

```sh
GRIMOIRE_API=http://127.0.0.1:8199 npx vite --host 127.0.0.1 --port 5199 --strictPort
```

```powershell
$env:GRIMOIRE_API = 'http://127.0.0.1:8199'
npx vite --host 127.0.0.1 --port 5199 --strictPort
```

6. Confirm the launcher's resolved store is the scratch path and
   `GET http://127.0.0.1:8199/api/worlds` returns `[]` before seeding. An occupied
   port or unexpected library is a reason to stop and correct isolation.
