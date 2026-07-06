# Claude Agent SDK Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second LLM provider that routes grimoire's generations through the Claude Agent SDK (billed to the owner's Claude subscription via the local Claude Code login), selectable from the Configuration page alongside OpenRouter.

**Architecture:** A thin provider seam: config gains `provider` + `claude_model`; a new `LLMClient` facade in `llm.py` dispatches per-call to the existing `OpenRouterClient` or a new `ClaudeAgentClient` (`claude_agent.py`) that lazy-imports `claude_agent_sdk`, flattens the message list into one transcript prompt, and yields assistant text. Errors unify under `LLMError` so routes have one except path.

**Tech Stack:** FastAPI backend (pytest), Vite/React frontend (vitest), `claude-agent-sdk` as an optional extra.

**Design doc:** `docs/superpowers/specs/2026-07-05-claude-provider-design.md` — read it first; it records the auth/policy findings and the deferred items.

## Global Constraints

- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`.
- Run backend tests: `backend/.venv/Scripts/python.exe -m pytest backend -q`.
- Run frontend tests **from `frontend/`**: `npx vitest run` and `npx tsc -b` (never `npx --prefix frontend vitest run`).
- `claude-agent-sdk` must be an **optional** dependency (`grimoire[claude]` extra); base install and test suite must pass without it installed. Unit tests fake the SDK module — never call the real SDK in tests.
- Default provider stays `openrouter`; existing config files without the new keys must keep working unchanged.
- Config key names: `provider` (values `openrouter` | `claude`), `claude_model` (default `"opus"`).
- Verify the `claude_agent_sdk` import surface against the installed version before Task 3 (`query`, `ClaudeAgentOptions`, `AssistantMessage`, `TextBlock`, `CLINotFoundError`, `ProcessError`) — the SDK is young and this plan was written 2026-07-05.

---

### Task 1: Config keys `provider` and `claude_model`

**Files:**
- Modify: `backend/src/grimoire/store/config.py`
- Modify: `backend/src/grimoire/routes.py` (the `ConfigUpdate` model near line 23; `_public_config` near line 260)
- Test: `backend/tests/test_config_store.py`, `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: existing `read_config()` / `write_config(**fields)`.
- Produces: `read_config()` always returns `provider` and `claude_model` keys; `GET /api/config` returns them; `PUT /api/config` accepts them (provider validated to the two values).

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_config_store.py` (match the file's existing imports/fixtures — every test there already sets `GRIMOIRE_HOME` via monkeypatch):

```python
def test_provider_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cfg = read_config()
    assert cfg["provider"] == "openrouter"
    assert cfg["claude_model"] == "opus"


def test_provider_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    write_config(provider="claude", claude_model="sonnet")
    cfg = read_config()
    assert cfg["provider"] == "claude"
    assert cfg["claude_model"] == "sonnet"
```

In `backend/tests/test_routes.py` (reuse the existing `client` fixture):

```python
def test_config_provider_roundtrip(client):
    r = client.put("/api/config", json={"provider": "claude", "claude_model": "sonnet"})
    assert r.status_code == 200
    assert r.json()["provider"] == "claude"
    r = client.get("/api/config")
    assert r.json()["provider"] == "claude"
    assert r.json()["claude_model"] == "sonnet"


def test_config_rejects_unknown_provider(client):
    r = client.put("/api/config", json={"provider": "gemini"})
    assert r.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q -k "provider"`
Expected: FAIL with `KeyError: 'provider'` / 200-vs-422 mismatch.

- [ ] **Step 3: Implement**

`backend/src/grimoire/store/config.py`:

```python
DEFAULT_PROVIDER = "openrouter"
DEFAULT_CLAUDE_MODEL = "opus"
_CONFIG_KEYS = ("openrouter_key", "model", "theme", "context_scan_depth", "system_prompt",
                "quote_color", "recap_depth", "user_label", "assistant_label",
                "provider", "claude_model")
```

and in `read_config()`'s `defaults` dict add:

```python
"provider": DEFAULT_PROVIDER, "claude_model": DEFAULT_CLAUDE_MODEL,
```

`backend/src/grimoire/routes.py` — `ConfigUpdate` gains (add `from typing import Literal` to imports):

```python
    provider: Literal["openrouter", "claude"] | None = None
    claude_model: str | None = None
```

`_public_config` gains:

```python
    "provider": cfg.get("provider", "openrouter"),
    "claude_model": cfg.get("claude_model", "opus"),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/config.py backend/src/grimoire/routes.py backend/tests/test_config_store.py backend/tests/test_routes.py
git commit -m "feat(backend): provider and claude_model config keys"
```

---

### Task 2: `LLMError` base class

**Files:**
- Create: `backend/src/grimoire/llm.py`
- Modify: `backend/src/grimoire/openrouter.py:16-20`
- Test: `backend/tests/test_llm.py`

**Interfaces:**
- Produces: `LLMError(kind: str, detail: str = "")` with `.kind` / `.detail` attributes (exact shape of today's `OpenRouterError`); `OpenRouterError` subclasses it. Kinds in use: `missing_key | auth | rate_limit | network | bad_response`, plus `missing_dependency` (Task 3).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_llm.py`:

```python
from grimoire.llm import LLMError
from grimoire.openrouter import OpenRouterError


def test_openrouter_error_is_llm_error():
    err = OpenRouterError("auth", "bad key")
    assert isinstance(err, LLMError)
    assert err.kind == "auth"
    assert err.detail == "bad key"


def test_llm_error_detail_defaults_to_kind():
    assert LLMError("network").detail == "network"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_llm.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'grimoire.llm'`.

- [ ] **Step 3: Implement**

Create `backend/src/grimoire/llm.py`:

```python
"""Provider-agnostic LLM surface: shared error type and (Task 4) dispatch facade."""

from __future__ import annotations


class LLMError(Exception):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(detail or kind)
        self.kind = kind  # missing_key | auth | rate_limit | network | bad_response | missing_dependency
        self.detail = detail or kind
```

In `backend/src/grimoire/openrouter.py`, replace the `OpenRouterError` class body with a subclass (delete its `__init__`):

```python
from .llm import LLMError


class OpenRouterError(LLMError):
    pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: all PASS (existing `test_openrouter.py` exercises kind/detail through the subclass).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/llm.py backend/src/grimoire/openrouter.py backend/tests/test_llm.py
git commit -m "refactor(backend): LLMError base for provider errors"
```

---

### Task 3: `ClaudeAgentClient`

**Files:**
- Create: `backend/src/grimoire/claude_agent.py`
- Modify: `backend/pyproject.toml:18-22` (optional extra)
- Test: `backend/tests/test_claude_agent.py`

**Interfaces:**
- Consumes: `LLMError` from Task 2.
- Produces: `ClaudeAgentClient` with `stream(messages: list[dict], model: str) -> AsyncIterator[str]` and `complete(messages, model) -> str`; `ClaudeAgentError(LLMError)`. Note: no `key` parameter — auth comes from the Claude Code login on the host.

> **Before coding:** verify the installed `claude-agent-sdk`'s names match the imports below (see Global Constraints). Adjust the implementation, not the tests' intent, if they drifted.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_claude_agent.py`:

```python
import sys
import types

import pytest

from grimoire.claude_agent import ClaudeAgentClient, ClaudeAgentError


class _TextBlock:
    def __init__(self, text):
        self.text = text


class _AssistantMessage:
    def __init__(self, blocks):
        self.content = blocks


class _CLINotFoundError(Exception):
    pass


class _ProcessError(Exception):
    pass


def install_fake_sdk(monkeypatch, replies=(), error=None):
    """Install a stand-in claude_agent_sdk module; returns captured call args."""
    captured = {}

    async def query(*, prompt, options):
        captured["prompt"] = prompt
        captured["options"] = options
        if error is not None:
            raise error
        for reply in replies:
            yield reply

    mod = types.SimpleNamespace(
        query=query,
        ClaudeAgentOptions=lambda **kw: types.SimpleNamespace(**kw),
        AssistantMessage=_AssistantMessage,
        TextBlock=_TextBlock,
        CLINotFoundError=_CLINotFoundError,
        ProcessError=_ProcessError,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", mod)
    return captured


async def test_stream_yields_assistant_text(monkeypatch):
    replies = [
        _AssistantMessage([_TextBlock("Hel"), _TextBlock("lo")]),
        types.SimpleNamespace(),  # non-assistant message (e.g. ResultMessage) is ignored
    ]
    install_fake_sdk(monkeypatch, replies=replies)
    client = ClaudeAgentClient()
    chunks = [c async for c in client.stream([{"role": "user", "content": "hi"}], "opus")]
    assert "".join(chunks) == "Hello"


async def test_system_messages_become_system_prompt(monkeypatch):
    captured = install_fake_sdk(monkeypatch)
    client = ClaudeAgentClient()
    messages = [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "go on"},
    ]
    [c async for c in client.stream(messages, "opus")]
    assert captured["options"].system_prompt == "be brief"
    assert captured["options"].model == "opus"
    assert captured["options"].allowed_tools == []
    assert captured["options"].max_turns == 1
    prompt = captured["prompt"]
    assert "hi" in prompt and "hello" in prompt and "go on" in prompt
    assert prompt.rstrip().endswith("[assistant]")


async def test_missing_sdk_is_normalized(monkeypatch):
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)  # import raises ImportError
    client = ClaudeAgentClient()
    with pytest.raises(ClaudeAgentError) as exc:
        [c async for c in client.stream([{"role": "user", "content": "hi"}], "opus")]
    assert exc.value.kind == "missing_dependency"


async def test_cli_not_found_is_missing_dependency(monkeypatch):
    install_fake_sdk(monkeypatch, error=_CLINotFoundError("claude not installed"))
    client = ClaudeAgentClient()
    with pytest.raises(ClaudeAgentError) as exc:
        [c async for c in client.stream([{"role": "user", "content": "hi"}], "opus")]
    assert exc.value.kind == "missing_dependency"


async def test_process_error_is_bad_response(monkeypatch):
    install_fake_sdk(monkeypatch, error=_ProcessError("exit 1: not logged in"))
    client = ClaudeAgentClient()
    with pytest.raises(ClaudeAgentError) as exc:
        [c async for c in client.stream([{"role": "user", "content": "hi"}], "opus")]
    assert exc.value.kind == "bad_response"
    assert "not logged in" in exc.value.detail


async def test_unexpected_error_is_network(monkeypatch):
    install_fake_sdk(monkeypatch, error=RuntimeError("boom"))
    client = ClaudeAgentClient()
    with pytest.raises(ClaudeAgentError) as exc:
        [c async for c in client.stream([{"role": "user", "content": "hi"}], "opus")]
    assert exc.value.kind == "network"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_claude_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'grimoire.claude_agent'`.

- [ ] **Step 3: Implement**

Create `backend/src/grimoire/claude_agent.py`:

```python
"""Claude Agent SDK provider — routes prompts through the local Claude Code login.

Auth is inherited from the host's Claude Code session (or CLAUDE_CODE_OAUTH_TOKEN);
usage bills against the owner's Claude subscription, not an API key. See
docs/superpowers/specs/2026-07-05-claude-provider-design.md for the policy notes.
"""

from __future__ import annotations

from typing import AsyncIterator

from .llm import LLMError


class ClaudeAgentError(LLMError):
    pass


def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    return system, [m for m in messages if m["role"] != "system"]


def _flatten(turns: list[dict]) -> str:
    # The Agent SDK takes a single prompt string, not a message array; render
    # the conversation as a transcript and cue the next assistant reply.
    lines = [f"[{m['role']}]\n{m['content']}" for m in turns]
    lines.append("[assistant]")
    return "\n\n".join(lines)


class ClaudeAgentClient:
    async def stream(self, messages: list[dict], model: str) -> AsyncIterator[str]:
        try:
            from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions,
                                          CLINotFoundError, ProcessError, TextBlock, query)
        except ImportError as exc:
            raise ClaudeAgentError(
                "missing_dependency",
                "claude-agent-sdk is not installed — pip install 'grimoire[claude]'",
            ) from exc
        system, turns = _split_system(messages)
        options = ClaudeAgentOptions(
            system_prompt=system or None, model=model, allowed_tools=[], max_turns=1,
        )
        try:
            async for message in query(prompt=_flatten(turns), options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            yield block.text
        except CLINotFoundError as exc:
            raise ClaudeAgentError("missing_dependency", str(exc)) from exc
        except ProcessError as exc:
            raise ClaudeAgentError("bad_response", str(exc)) from exc
        except ClaudeAgentError:
            raise
        except Exception as exc:
            raise ClaudeAgentError("network", str(exc)) from exc

    async def complete(self, messages: list[dict], model: str) -> str:
        return "".join([chunk async for chunk in self.stream(messages, model)])
```

In `backend/pyproject.toml`, add to `[project.optional-dependencies]`:

```toml
claude = [
    "claude-agent-sdk>=0.1",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: all PASS (without `claude-agent-sdk` installed — the fakes cover it).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/claude_agent.py backend/pyproject.toml backend/tests/test_claude_agent.py
git commit -m "feat(backend): ClaudeAgentClient provider over the Claude Agent SDK"
```

---

### Task 4: `LLMClient` facade and routes migration

**Files:**
- Modify: `backend/src/grimoire/llm.py`
- Modify: `backend/src/grimoire/routes.py` (imports ~line 12; `get_openrouter` ~line 15; `_require_key` ~line 1292; all `client.stream(...)`/`client.complete(...)` call sites and `except OpenRouterError` handlers — grep for them)
- Test: `backend/tests/test_llm.py`, `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `OpenRouterClient.stream(messages, model, key)` (unchanged), `ClaudeAgentClient.stream(messages, model)` (Task 3), config keys (Task 1).
- Produces: `LLMClient` with `stream(messages: list[dict], cfg: dict) -> AsyncIterator[str]`, `complete(messages, cfg) -> str`, `aclose()`; routes dependency renamed `get_openrouter` → `get_llm`.

- [ ] **Step 1: Write the failing dispatch tests**

Append to `backend/tests/test_llm.py`:

```python
from grimoire.llm import LLMClient


class FakeProvider:
    def __init__(self, tag):
        self.tag = tag
        self.calls = []

    async def stream(self, messages, *args):
        self.calls.append(args)
        yield self.tag


def _cfg(provider):
    return {"provider": provider, "model": "or-model", "openrouter_key": "sk-or-x",
            "claude_model": "opus"}


async def test_dispatches_to_openrouter():
    op, cl = FakeProvider("or"), FakeProvider("cl")
    client = LLMClient(openrouter=op, claude=cl)
    chunks = [c async for c in client.stream([], _cfg("openrouter"))]
    assert chunks == ["or"]
    assert op.calls == [("or-model", "sk-or-x")]
    assert cl.calls == []


async def test_dispatches_to_claude():
    op, cl = FakeProvider("or"), FakeProvider("cl")
    client = LLMClient(openrouter=op, claude=cl)
    chunks = [c async for c in client.stream([], _cfg("claude"))]
    assert chunks == ["cl"]
    assert cl.calls == [("opus",)]
    assert op.calls == []


async def test_missing_provider_key_defaults_to_openrouter():
    op, cl = FakeProvider("or"), FakeProvider("cl")
    client = LLMClient(openrouter=op, claude=cl)
    cfg = {"model": "or-model", "openrouter_key": "sk-or-x"}  # pre-upgrade config
    assert [c async for c in client.stream([], cfg)] == ["or"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_llm.py -v`
Expected: FAIL with `ImportError: cannot import name 'LLMClient'`.

- [ ] **Step 3: Implement the facade**

Append to `backend/src/grimoire/llm.py`:

```python
class LLMClient:
    """Dispatches each call to the provider named in the config dict."""

    def __init__(self, openrouter=None, claude=None):
        # Imported here rather than at module top: openrouter.py imports
        # LLMError from this module, so top-level imports would be circular.
        from .claude_agent import ClaudeAgentClient
        from .openrouter import OpenRouterClient
        self._openrouter = openrouter if openrouter is not None else OpenRouterClient()
        self._claude = claude if claude is not None else ClaudeAgentClient()

    def stream(self, messages: list[dict], cfg: dict):
        if cfg.get("provider", "openrouter") == "claude":
            return self._claude.stream(messages, cfg.get("claude_model", "opus"))
        return self._openrouter.stream(messages, cfg["model"], cfg["openrouter_key"])

    async def complete(self, messages: list[dict], cfg: dict) -> str:
        return "".join([chunk async for chunk in self.stream(messages, cfg)])

    async def aclose(self) -> None:
        await self._openrouter.aclose()
```

- [ ] **Step 4: Run the dispatch tests**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_llm.py -v`
Expected: PASS.

- [ ] **Step 5: Migrate routes.py**

All changes are mechanical; grep rather than trust line numbers:

1. Replace the import `from .openrouter import OpenRouterClient, OpenRouterError` with `from .llm import LLMClient, LLMError`.
2. Replace the module-level client + dependency:

```python
_llm = LLMClient()


def get_llm() -> LLMClient:
    return _llm
```

3. Every `client: OpenRouterClient = Depends(get_openrouter)` → `client: LLMClient = Depends(get_llm)` (also the `client: OpenRouterClient` parameters on `_chat_stream` / `_ephemeral_stream`).
4. Every `client.stream(messages, cfg["model"], cfg["openrouter_key"])` → `client.stream(messages, cfg)`; every `client.complete(<msgs>, cfg["model"], cfg["openrouter_key"])` → `client.complete(<msgs>, cfg)`.
5. Every `except OpenRouterError` → `except LLMError`.
6. Make `_require_key` provider-aware:

```python
def _require_key(cfg: dict[str, str]) -> None:
    if cfg.get("provider", "openrouter") == "openrouter" and not cfg["openrouter_key"]:
        raise HTTPException(
            status_code=409,
            detail={"detail": "OpenRouter key not set", "kind": "missing_key"},
        )
```

- [ ] **Step 6: Update test fakes and add a provider-routing test**

In `backend/tests/test_routes.py`:

1. `routes.get_openrouter` → `routes.get_llm` everywhere (~30 dependency-override lines).
2. Every fake's `stream`/`complete` drops `(model, key)` for `(cfg)` — e.g. `async def stream(self, messages, model, key):` → `async def stream(self, messages, cfg):` (fix any fake that asserts on `model`/`key` to read `cfg["model"]`/`cfg["openrouter_key"]`).
3. Add a test that the missing-key 409 is skipped for the claude provider (adapt to however the existing missing-key test drives a chat endpoint):

```python
def test_chat_missing_key_ok_for_claude_provider(client):
    client.put("/api/config", json={"provider": "claude"})
    # ...create campaign/scene exactly as the existing missing-key test does...
    # POST the chat turn with a FakeOpenRouter-style override on routes.get_llm
    # and assert the response is NOT a 409 missing_key.
```

- [ ] **Step 7: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/src/grimoire/llm.py backend/src/grimoire/routes.py backend/tests/test_llm.py backend/tests/test_routes.py
git commit -m "feat(backend): route generations through provider-dispatching LLMClient"
```

---

### Task 5: Configuration page provider picker

**Files:**
- Modify: `frontend/src/api/client.ts` (the `Config` type and `putConfig` fields)
- Modify: `frontend/src/routes/ConfigView.tsx`
- Test: `frontend/src/routes/ConfigView.test.tsx`

**Interfaces:**
- Consumes: `GET/PUT /api/config` now carrying `provider` and `claude_model` (Task 1).
- Produces: a `select` labeled "LLM provider"; OpenRouter key input + `ModelCombobox` render only when `provider === "openrouter"`; a "Claude model" text input renders only when `provider === "claude"`; Save sends `provider` and `claude_model`.

- [ ] **Step 1: Write the failing tests**

Add to `frontend/src/routes/ConfigView.test.tsx`, following that file's existing api-mock setup (it already mocks `api.getConfig`/`api.putConfig`; extend the mocked config object with `provider: "openrouter", claude_model: "opus"`):

```tsx
it("switching provider to claude swaps key/model fields for a claude model input", async () => {
  render(<ConfigView />);
  const select = await screen.findByLabelText("LLM provider");
  expect(screen.getByLabelText("OpenRouter API key")).toBeInTheDocument();
  fireEvent.change(select, { target: { value: "claude" } });
  expect(screen.queryByLabelText("OpenRouter API key")).toBeNull();
  expect(screen.getByLabelText("Claude model")).toBeInTheDocument();
});

it("save sends provider and claude_model", async () => {
  render(<ConfigView />);
  const select = await screen.findByLabelText("LLM provider");
  fireEvent.change(select, { target: { value: "claude" } });
  fireEvent.change(screen.getByLabelText("Claude model"), { target: { value: "sonnet" } });
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() =>
    expect(api.putConfig).toHaveBeenCalledWith(
      expect.objectContaining({ provider: "claude", claude_model: "sonnet" }),
    ),
  );
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/routes/ConfigView.test.tsx`
Expected: FAIL — no element labeled "LLM provider".

- [ ] **Step 3: Implement**

`frontend/src/api/client.ts` — add to the `Config` type and to `putConfig`'s accepted fields:

```ts
provider: string;
claude_model: string;
```

`frontend/src/routes/ConfigView.tsx`:

1. State + hydration (inside the existing `getConfig().then` callback):

```tsx
const [provider, setProvider] = useState("openrouter");
const [claudeModel, setClaudeModel] = useState("");
// in .then((c) => { ... }):
setProvider(c.provider);
setClaudeModel(c.claude_model);
```

2. Replace the fixed "OpenRouter API key" + "Model" sections with:

```tsx
<div className="section-label">LLM provider</div>
<select
  aria-label="LLM provider"
  value={provider}
  onChange={(e) => setProvider(e.target.value)}
>
  <option value="openrouter">OpenRouter (API key)</option>
  <option value="claude">Claude (subscription via Claude Code)</option>
</select>

{provider === "openrouter" ? (
  <>
    <div className="section-label">OpenRouter API key</div>
    <input
      type="password"
      aria-label="OpenRouter API key"
      placeholder={config.key_set ? "A key is set — type to replace" : "sk-or-…"}
      value={key}
      onChange={(e) => setKey(e.target.value)}
    />
    <div className="section-label">Model</div>
    <ModelCombobox value={model} onChange={setModel} />
  </>
) : (
  <>
    <div className="section-label">Claude model</div>
    <input
      aria-label="Claude model"
      placeholder="opus"
      value={claudeModel}
      onChange={(e) => setClaudeModel(e.target.value)}
    />
    <p className="field-hint">
      Uses the local Claude Code login (run <code>claude setup-token</code> on a
      headless machine) and bills your Claude subscription. Requires the backend
      extra: <code>pip install grimoire[claude]</code>.
    </p>
  </>
)}
```

3. Extend `save`'s field type and the Save button payload:

```tsx
onClick={() => save({
  model, provider, claude_model: claudeModel,
  system_prompt: systemPrompt,
  user_label: userLabel, assistant_label: assistantLabel,
  ...(key ? { openrouter_key: key } : {}),
})}
```

- [ ] **Step 4: Run tests and the type check**

Run (from `frontend/`): `npx vitest run` then `npx tsc -b`
Expected: all PASS, no type errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/routes/ConfigView.tsx frontend/src/routes/ConfigView.test.tsx
git commit -m "feat(frontend): LLM provider picker in configuration"
```

---

### Final verification

- [ ] Full suites: `backend/.venv/Scripts/python.exe -m pytest backend -q`; from `frontend/`: `npx vitest run && npx tsc -b`.
- [ ] Manual smoke (requires Claude Code logged in on this machine): `pip install -e "backend[claude]"`, set provider to Claude on the Configuration page, send a scene turn, confirm a reply arrives and errors surface readably when Claude Code is logged out.
