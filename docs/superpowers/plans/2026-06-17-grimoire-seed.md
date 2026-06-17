# grimoire Seed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the minimum grimoire: an OpenRouter-backed streaming chat whose entire state is markdown files under `~/.grimoire/`, with a three-theme token-based UI and cross-OS install/run/shutdown scripts.

**Architecture:** FastAPI backend (`backend/src/grimoire/`) exposes `/api/*`; `store.py` is the only thing that touches the filesystem (`~/.grimoire/`), `openrouter.py` is a thin streaming client, `routes.py` is the HTTP surface, `main.py` assembles the app. A React 18 + Vite + TypeScript SPA (`frontend/`) renders chat and config, streams replies over SSE, and themes itself from per-file token sets. `scripts/{windows,unix}/` install dependencies, create a pinnable desktop launcher, run dev mode, and shut it down.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, httpx, pytest + pytest-asyncio. React 18, Vite 5, TypeScript, react-router-dom, react-markdown + remark-gfm, vitest + @testing-library/react. PowerShell + bash for scripts.

**Conventions for every task:** the backend virtualenv is `backend/.venv`; run backend commands from `backend/` with that venv active (`backend/.venv/Scripts/python -m ...` on Windows, `backend/.venv/bin/python -m ...` on unix). Run frontend commands from `frontend/`. Commit after each task.

---

## Task 1: Backend project scaffold

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/src/grimoire/__init__.py`
- Create: `backend/tests/__init__.py`
- Modify: `.gitignore`

- [ ] **Step 1: Write `backend/pyproject.toml`**

```toml
[project]
name = "grimoire"
version = "0.0.1"
description = "grimoire seed — OpenRouter chat with markdown state"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/grimoire"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["src"]
```

- [ ] **Step 2: Create empty package + test init files**

`backend/src/grimoire/__init__.py`:
```python
"""grimoire seed backend."""
```

`backend/tests/__init__.py`:
```python
```

- [ ] **Step 3: Append ignores to `.gitignore`**

Append these lines to the existing `.gitignore` (keep the current `OLD/` and `.superpowers/` rules):
```gitignore

# Python
backend/.venv/
__pycache__/
*.pyc
.pytest_cache/

# Node / Vite
frontend/node_modules/
frontend/dist/

# Run-time pidfiles
.run/
```

- [ ] **Step 4: Create the venv and install**

Run (from `backend/`):
```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Windows
# .venv/bin/python -m pip install -e ".[dev]"      # unix
```
Expected: installs fastapi, uvicorn, httpx, pytest, pytest-asyncio with no errors.

- [ ] **Step 5: Verify pytest runs (collects zero tests)**

Run (from `backend/`): `.venv/Scripts/python -m pytest`
Expected: exit 0, "no tests ran".

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/src backend/tests .gitignore
git commit -m "chore(backend): scaffold grimoire package and tooling"
```

---

## Task 2: Frontmatter helpers in store.py

**Files:**
- Create: `backend/src/grimoire/store.py`
- Test: `backend/tests/test_frontmatter.py`

The frontmatter format is string-scalar only. Values are quoted on write only when they need it; reading strips matching surrounding single quotes (with `''` → `'` unescaping).

- [ ] **Step 1: Write the failing test**

`backend/tests/test_frontmatter.py`:
```python
from grimoire.store import parse_frontmatter, dump_frontmatter


def test_roundtrip_plain_values():
    text = "---\nmodel: anthropic/claude-opus-4.1\ntheme: occult\n---\n\nbody here\n"
    meta, body = parse_frontmatter(text)
    assert meta == {"model": "anthropic/claude-opus-4.1", "theme": "occult"}
    assert body == "body here\n"


def test_value_needing_quotes_roundtrips():
    meta = {"title": "Chat: part one's tale", "openrouter_key": ""}
    rebuilt, body = parse_frontmatter(dump_frontmatter(meta, "the body\n"))
    assert rebuilt == meta
    assert body == "the body\n"


def test_missing_frontmatter_returns_empty_meta():
    meta, body = parse_frontmatter("no fences here\n")
    assert meta == {}
    assert body == "no fences here\n"
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `backend/`): `.venv/Scripts/python -m pytest tests/test_frontmatter.py -v`
Expected: FAIL with `ImportError` / `cannot import name 'parse_frontmatter'`.

- [ ] **Step 3: Implement the helpers**

Create `backend/src/grimoire/store.py` with this content (more functions are added in later tasks):
```python
"""Filesystem-as-database for grimoire: markdown files under ~/.grimoire/."""

from __future__ import annotations


def _needs_quotes(value: str) -> bool:
    if value == "":
        return True
    if value != value.strip():
        return True
    return any(c in value for c in ":#'\"")


def _quote(value: str) -> str:
    if not _needs_quotes(value):
        return value
    return "'" + value.replace("'", "''") + "'"


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split `---`-fenced frontmatter from the body. String scalars only."""
    if not text.startswith("---\n"):
        return {}, text
    rest = text[4:]
    end = rest.find("\n---")
    if end == -1:
        return {}, text
    block = rest[:end]
    after = rest[end + 4:]
    if after.startswith("\n"):
        after = after[1:]
    meta: dict[str, str] = {}
    for line in block.splitlines():
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        meta[key.strip()] = _unquote(value)
    return meta, after


def dump_frontmatter(meta: dict[str, str], body: str) -> str:
    lines = ["---"]
    for key, value in meta.items():
        lines.append(f"{key}: {_quote('' if value is None else str(value))}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + "\n" + body
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `backend/`): `.venv/Scripts/python -m pytest tests/test_frontmatter.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store.py backend/tests/test_frontmatter.py
git commit -m "feat(store): frontmatter parse/dump helpers"
```

---

## Task 3: Config read/write in store.py

**Files:**
- Modify: `backend/src/grimoire/store.py`
- Test: `backend/tests/test_config_store.py`

`GRIMOIRE_HOME` overrides the base dir. `read_config` returns a dict with keys `openrouter_key`, `model`, `theme`, creating `config.md` with defaults on first read. `write_config(**fields)` merges only provided fields.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_config_store.py`:
```python
import importlib

import grimoire.store as store


def reload_with_home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    return store


def test_first_read_creates_defaults(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cfg = s.read_config()
    assert cfg["openrouter_key"] == ""
    assert cfg["theme"] == "occult"
    assert cfg["model"]  # some non-empty default
    assert (tmp_path / "config.md").exists()


def test_write_merges_without_clearing(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    s.write_config(openrouter_key="sk-or-secret")
    s.write_config(model="anthropic/claude-x")  # must not wipe the key
    cfg = s.read_config()
    assert cfg["openrouter_key"] == "sk-or-secret"
    assert cfg["model"] == "anthropic/claude-x"
    assert cfg["theme"] == "occult"
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `backend/`): `.venv/Scripts/python -m pytest tests/test_config_store.py -v`
Expected: FAIL with `AttributeError: module 'grimoire.store' has no attribute 'read_config'`.

- [ ] **Step 3: Implement config functions**

Add to the top of `backend/src/grimoire/store.py` (after the `from __future__` line), import block:
```python
import os
from pathlib import Path
```

Append to `backend/src/grimoire/store.py`:
```python
DEFAULT_MODEL = "anthropic/claude-opus-4.1"
DEFAULT_THEME = "occult"
_CONFIG_KEYS = ("openrouter_key", "model", "theme")


def home() -> Path:
    return Path(os.environ.get("GRIMOIRE_HOME") or (Path.home() / ".grimoire"))


def _ensure_home() -> Path:
    base = home()
    (base / "conversations").mkdir(parents=True, exist_ok=True)
    return base


def _config_path() -> Path:
    return home() / "config.md"


def read_config() -> dict[str, str]:
    _ensure_home()
    path = _config_path()
    if not path.exists():
        defaults = {"openrouter_key": "", "model": DEFAULT_MODEL, "theme": DEFAULT_THEME}
        path.write_text(dump_frontmatter(defaults, ""), encoding="utf-8")
        return defaults
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    return {
        "openrouter_key": meta.get("openrouter_key", ""),
        "model": meta.get("model", DEFAULT_MODEL),
        "theme": meta.get("theme", DEFAULT_THEME),
    }


def write_config(**fields: str) -> dict[str, str]:
    cfg = read_config()
    for key, value in fields.items():
        if key in _CONFIG_KEYS and value is not None:
            cfg[key] = value
    _config_path().write_text(dump_frontmatter(cfg, ""), encoding="utf-8")
    return cfg
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `backend/`): `.venv/Scripts/python -m pytest tests/test_config_store.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store.py backend/tests/test_config_store.py
git commit -m "feat(store): config read/write with merge and defaults"
```

---

## Task 4: Conversation storage in store.py

**Files:**
- Modify: `backend/src/grimoire/store.py`
- Test: `backend/tests/test_conversation_store.py`

Transcript roles: `user` ↔ `**You:**`, `assistant` ↔ `**Grimoire:**`. The body is parsed back into `[{"role","content"}]`. Filenames are `YYYY-MM-DD-<slug>.md`; the id is the stem.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_conversation_store.py`:
```python
import importlib

import grimoire.store as store


def reload_with_home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    return store


def test_create_list_and_read_empty(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.create_conversation("My First Chat")
    assert cid.endswith("my-first-chat")
    metas = s.list_conversations()
    assert len(metas) == 1
    assert metas[0]["id"] == cid
    assert metas[0]["title"] == "My First Chat"
    conv = s.read_conversation(cid)
    assert conv["messages"] == []


def test_append_and_parse_roundtrip(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.create_conversation("Roundtrip")
    s.append_message(cid, "user", "Describe the keeper.\n\n**Not a real marker** still mine.")
    s.append_message(cid, "assistant", "She is older than the salt.")
    conv = s.read_conversation(cid)
    assert conv["messages"] == [
        {"role": "user", "content": "Describe the keeper.\n\n**Not a real marker** still mine."},
        {"role": "assistant", "content": "She is older than the salt."},
    ]


def test_unknown_conversation_raises(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    try:
        s.read_conversation("nope")
        assert False, "expected error"
    except store.ConversationNotFound:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `backend/`): `.venv/Scripts/python -m pytest tests/test_conversation_store.py -v`
Expected: FAIL — `create_conversation` / `ConversationNotFound` not defined.

- [ ] **Step 3: Implement conversation functions**

Add to the import block at the top of `backend/src/grimoire/store.py`:
```python
import re
from datetime import datetime, timezone
```

Append to `backend/src/grimoire/store.py`:
```python
ROLE_TO_LABEL = {"user": "You", "assistant": "Grimoire"}
LABEL_TO_ROLE = {"You": "user", "Grimoire": "assistant"}
_MARKER = re.compile(r"^\*\*(You|Grimoire):\*\*[ ]?", re.MULTILINE)


class ConversationNotFound(Exception):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "chat"


def _conv_path(cid: str) -> Path:
    return home() / "conversations" / f"{cid}.md"


def create_conversation(title: str) -> str:
    _ensure_home()
    now = _now_iso()
    cid = f"{now[:10]}-{_slugify(title)}"
    path = _conv_path(cid)
    n = 2
    while path.exists():
        cid = f"{now[:10]}-{_slugify(title)}-{n}"
        path = _conv_path(cid)
        n += 1
    meta = {"title": title, "model": read_config()["model"], "created": now, "updated": now}
    path.write_text(dump_frontmatter(meta, ""), encoding="utf-8")
    return cid


def list_conversations() -> list[dict[str, str]]:
    _ensure_home()
    out = []
    for path in (home() / "conversations").glob("*.md"):
        meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        out.append({
            "id": path.stem,
            "title": meta.get("title", path.stem),
            "model": meta.get("model", ""),
            "created": meta.get("created", ""),
            "updated": meta.get("updated", ""),
        })
    out.sort(key=lambda m: m["updated"], reverse=True)
    return out


def _parse_messages(body: str) -> list[dict[str, str]]:
    matches = list(_MARKER.finditer(body))
    messages = []
    for i, m in enumerate(matches):
        label = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        messages.append({"role": LABEL_TO_ROLE[label], "content": body[start:end].strip()})
    return messages


def read_conversation(cid: str) -> dict:
    path = _conv_path(cid)
    if not path.exists():
        raise ConversationNotFound(cid)
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    return {"meta": {"id": cid, **meta}, "messages": _parse_messages(body)}


def append_message(cid: str, role: str, content: str) -> None:
    path = _conv_path(cid)
    if not path.exists():
        raise ConversationNotFound(cid)
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    label = ROLE_TO_LABEL[role]
    block = f"**{label}:** {content.strip()}\n"
    body = (body.rstrip() + "\n\n" + block) if body.strip() else block
    meta["updated"] = _now_iso()
    path.write_text(dump_frontmatter(meta, body), encoding="utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `backend/`): `.venv/Scripts/python -m pytest tests/test_conversation_store.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store.py backend/tests/test_conversation_store.py
git commit -m "feat(store): conversation create/list/read/append with transcript roundtrip"
```

---

## Task 5: OpenRouter client

**Files:**
- Create: `backend/src/grimoire/openrouter.py`
- Test: `backend/tests/test_openrouter.py`

`OpenRouterClient` wraps an injectable `httpx.AsyncClient` so tests use `httpx.MockTransport`. `stream` yields content deltas from OpenRouter's SSE; errors normalize to `OpenRouterError(kind=...)`.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_openrouter.py`:
```python
import httpx
import pytest

from grimoire.openrouter import OpenRouterClient, OpenRouterError

SSE_BODY = (
    'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
    "data: [DONE]\n\n"
)


def make_client(handler):
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://openrouter.ai")
    return OpenRouterClient(http=http)


async def test_stream_yields_deltas():
    def handler(request):
        return httpx.Response(200, text=SSE_BODY)

    client = make_client(handler)
    chunks = [c async for c in client.stream([{"role": "user", "content": "hi"}], "m", "sk-or-x")]
    assert "".join(chunks) == "Hello"


async def test_auth_error_normalized():
    def handler(request):
        return httpx.Response(401, json={"error": "bad key"})

    client = make_client(handler)
    with pytest.raises(OpenRouterError) as exc:
        [c async for c in client.stream([], "m", "sk-or-x")]
    assert exc.value.kind == "auth"
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `backend/`): `.venv/Scripts/python -m pytest tests/test_openrouter.py -v`
Expected: FAIL — module `grimoire.openrouter` does not exist.

- [ ] **Step 3: Implement the client**

Create `backend/src/grimoire/openrouter.py`:
```python
"""Thin OpenRouter (OpenAI-compatible) client with normalized errors."""

from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

API_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterError(Exception):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(detail or kind)
        self.kind = kind  # missing_key | auth | rate_limit | network | bad_response
        self.detail = detail or kind


def _status_kind(status: int) -> str:
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "rate_limit"
    return "bad_response"


class OpenRouterClient:
    def __init__(self, http: httpx.AsyncClient | None = None):
        self._http = http
        self._owns = http is None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0))
        return self._http

    def _payload(self, messages, model, stream):
        return {"model": model, "messages": messages, "stream": stream}

    def _headers(self, key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    async def stream(self, messages, model: str, key: str) -> AsyncIterator[str]:
        if not key:
            raise OpenRouterError("missing_key", "OpenRouter API key is not set")
        http = self._client()
        try:
            async with http.stream(
                "POST", API_URL, headers=self._headers(key),
                json=self._payload(messages, model, True),
            ) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    raise OpenRouterError(_status_kind(resp.status_code), resp.text)
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        return
                    try:
                        obj = json.loads(data)
                        delta = obj["choices"][0]["delta"].get("content")
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    if delta:
                        yield delta
        except httpx.HTTPError as exc:
            raise OpenRouterError("network", str(exc)) from exc

    async def complete(self, messages, model: str, key: str) -> str:
        return "".join([chunk async for chunk in self.stream(messages, model, key)])

    async def aclose(self) -> None:
        if self._owns and self._http is not None:
            await self._http.aclose()
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `backend/`): `.venv/Scripts/python -m pytest tests/test_openrouter.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/openrouter.py backend/tests/test_openrouter.py
git commit -m "feat(openrouter): streaming client with normalized errors"
```

---

## Task 6: HTTP routes (config + conversations + chat SSE)

**Files:**
- Create: `backend/src/grimoire/routes.py`
- Create: `backend/src/grimoire/main.py`
- Test: `backend/tests/test_routes.py`

`main.create_app()` builds the app; the OpenRouter client is a dependency (`get_openrouter`) overridable in tests. Chat streams `data: {"delta": "..."}` then `data: {"done": true}`, or `data: {"error": {...}}` on failure.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_routes.py`:
```python
import importlib
import json

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import routes
from grimoire.main import create_app


class FakeOpenRouter:
    def __init__(self, deltas):
        self.deltas = deltas

    async def stream(self, messages, model, key):
        for d in self.deltas:
            yield d


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    importlib.reload(routes)
    app = create_app()
    app.dependency_overrides[routes.get_openrouter] = lambda: FakeOpenRouter(["Hel", "lo"])
    return TestClient(app)


def test_config_never_leaks_key(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    body = client.get("/api/config").json()
    assert body["key_set"] is True
    assert "openrouter_key" not in body
    assert "sk-or-secret" not in json.dumps(body)


def test_chat_missing_key_returns_409(client):
    cid = client.post("/api/conversations", json={"title": "T"}).json()["id"]
    resp = client.post(f"/api/conversations/{cid}/chat", json={"content": "hi"})
    assert resp.status_code == 409
    assert resp.json()["kind"] == "missing_key"


def test_chat_streams_and_persists(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    cid = client.post("/api/conversations", json={"title": "T"}).json()["id"]
    resp = client.post(f"/api/conversations/{cid}/chat", json={"content": "hi"})
    assert resp.status_code == 200
    assert 'data: {"delta": "Hel"}' in resp.text
    assert 'data: {"done": true}' in resp.text
    conv = client.get(f"/api/conversations/{cid}").json()
    assert conv["messages"][-1] == {"role": "assistant", "content": "Hello"}
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `backend/`): `.venv/Scripts/python -m pytest tests/test_routes.py -v`
Expected: FAIL — `grimoire.main` / `grimoire.routes` do not exist.

- [ ] **Step 3: Implement routes.py**

Create `backend/src/grimoire/routes.py`:
```python
"""HTTP surface for grimoire."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import store
from .openrouter import OpenRouterClient, OpenRouterError

router = APIRouter()
_openrouter = OpenRouterClient()


def get_openrouter() -> OpenRouterClient:
    return _openrouter


class ConfigUpdate(BaseModel):
    model: str | None = None
    theme: str | None = None
    openrouter_key: str | None = None


class NewConversation(BaseModel):
    title: str | None = None


class ChatTurn(BaseModel):
    content: str


@router.get("/config")
def get_config():
    cfg = store.read_config()
    return {"model": cfg["model"], "theme": cfg["theme"], "key_set": bool(cfg["openrouter_key"])}


@router.put("/config")
def put_config(update: ConfigUpdate):
    fields = {k: v for k, v in update.model_dump().items() if v is not None}
    cfg = store.write_config(**fields)
    return {"model": cfg["model"], "theme": cfg["theme"], "key_set": bool(cfg["openrouter_key"])}


@router.get("/conversations")
def get_conversations():
    return store.list_conversations()


@router.post("/conversations")
def post_conversation(body: NewConversation):
    title = body.title or "New chat"
    return {"id": store.create_conversation(title)}


@router.get("/conversations/{cid}")
def get_conversation(cid: str):
    try:
        return store.read_conversation(cid)
    except store.ConversationNotFound:
        raise HTTPException(status_code=404, detail="conversation not found")


@router.post("/conversations/{cid}/chat")
def post_chat(cid: str, turn: ChatTurn, client: OpenRouterClient = Depends(get_openrouter)):
    try:
        conv = store.read_conversation(cid)
    except store.ConversationNotFound:
        raise HTTPException(status_code=404, detail="conversation not found")
    cfg = store.read_config()
    if not cfg["openrouter_key"]:
        raise HTTPException(status_code=409, detail={"detail": "OpenRouter key not set", "kind": "missing_key"})

    store.append_message(cid, "user", turn.content)
    messages = [{"role": m["role"], "content": m["content"]} for m in conv["messages"]]
    messages.append({"role": "user", "content": turn.content})

    async def event_stream():
        parts: list[str] = []
        try:
            async for delta in client.stream(messages, cfg["model"], cfg["openrouter_key"]):
                parts.append(delta)
                yield f"data: {json.dumps({'delta': delta})}\n\n"
            store.append_message(cid, "assistant", "".join(parts))
            yield f"data: {json.dumps({'done': True})}\n\n"
        except OpenRouterError as exc:
            if parts:
                store.append_message(cid, "assistant", "".join(parts))
            yield f"data: {json.dumps({'error': {'detail': exc.detail, 'kind': exc.kind}})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

The 409 detail is a dict so `resp.json()["kind"]` works; FastAPI serializes `HTTPException.detail` verbatim. The test reads `resp.json()["kind"]` — to make that top-level, the route raises with `detail={...}` and we override the handler below in main.

- [ ] **Step 4: Implement main.py**

Create `backend/src/grimoire/main.py`:
```python
"""FastAPI app assembly."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .routes import router

DIST = Path(__file__).resolve().parents[2].parent / "frontend" / "dist"


def create_app() -> FastAPI:
    app = FastAPI(title="grimoire")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(HTTPException)
    async def http_exc_handler(request: Request, exc: HTTPException):
        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    app.include_router(router, prefix="/api")

    if DIST.exists():
        app.mount("/", StaticFiles(directory=str(DIST), html=True), name="static")

    return app


app = create_app()
```

- [ ] **Step 5: Run tests to verify they pass**

Run (from `backend/`): `.venv/Scripts/python -m pytest tests/test_routes.py -v`
Expected: 3 passed.

- [ ] **Step 6: Run the whole backend suite**

Run (from `backend/`): `.venv/Scripts/python -m pytest -v`
Expected: all tests pass (frontmatter, config, conversation, openrouter, routes).

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/routes.py backend/src/grimoire/main.py backend/tests/test_routes.py
git commit -m "feat(routes): config, conversations, and chat SSE endpoints"
```

---

## Task 7: Frontend scaffold + public assets

**Files:**
- Create: `frontend/package.json`, `frontend/tsconfig.json`, `frontend/vite.config.ts`, `frontend/index.html`
- Create: `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/index.css`
- Copy: `OLD/frontend/public/*` → `frontend/public/`

- [ ] **Step 1: Write `frontend/package.json`**

```json
{
  "name": "grimoire-frontend",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "test": "vitest run"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.26.0",
    "react-markdown": "^9.0.1",
    "remark-gfm": "^4.0.0"
  },
  "devDependencies": {
    "@testing-library/react": "^16.0.0",
    "@testing-library/jest-dom": "^6.4.0",
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "jsdom": "^24.1.0",
    "typescript": "^5.5.4",
    "vite": "^5.4.0",
    "vitest": "^2.0.5"
  }
}
```

- [ ] **Step 2: Write `frontend/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noEmit": true,
    "types": ["vitest/globals", "@testing-library/jest-dom"]
  },
  "include": ["src"]
}
```

- [ ] **Step 3: Write `frontend/vite.config.ts`**

```ts
/// <reference types="vitest/config" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
  },
});
```

- [ ] **Step 4: Write `frontend/index.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <link rel="icon" href="/favicon.ico" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>grimoire</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 5: Write `frontend/src/test-setup.ts`**

```ts
import "@testing-library/jest-dom";
```

- [ ] **Step 6: Write `frontend/src/index.css` (token-driven base styles)**

```css
:root {
  --bg: #15110e;
  --surface: #0d0a08;
  --fg: #e8dcc6;
  --muted: #8a7a5c;
  --accent: #caa45a;
  --font-display: Georgia, "Times New Roman", serif;
  --font-body: Georgia, "Times New Roman", serif;
  --radius: 3px;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--fg);
  font-family: var(--font-body);
}
.topbar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 16px; background: var(--surface);
  border-bottom: 1px solid var(--muted);
  font-family: var(--font-display);
}
.topbar a { color: var(--accent); text-decoration: none; }
.layout { display: flex; height: calc(100vh - 49px); }
.sidebar { width: 240px; border-right: 1px solid var(--muted); padding: 12px; overflow-y: auto; }
.sidebar button, .conv-item {
  display: block; width: 100%; text-align: left; margin-bottom: 6px;
  background: transparent; color: var(--fg); border: 1px solid var(--muted);
  border-radius: var(--radius); padding: 6px 8px; cursor: pointer;
}
.conv-item.active { border-color: var(--accent); color: var(--accent); }
.main { flex: 1; display: flex; flex-direction: column; }
.stream { flex: 1; overflow-y: auto; padding: 16px; }
.msg { margin-bottom: 16px; }
.role { font-size: 11px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--accent); margin-bottom: 4px; }
.inputbar { display: flex; gap: 8px; padding: 12px; border-top: 1px solid var(--muted); }
.inputbar textarea { flex: 1; background: var(--surface); color: var(--fg); border: 1px solid var(--muted); border-radius: var(--radius); padding: 8px; font-family: var(--font-body); resize: none; }
button.send, button.primary { background: var(--accent); color: var(--bg); border: none; border-radius: var(--radius); padding: 8px 14px; cursor: pointer; }
.banner { background: var(--surface); border: 1px solid var(--accent); color: var(--accent); padding: 10px 16px; margin: 12px 16px; border-radius: var(--radius); }
.config { padding: 24px; max-width: 560px; }
.config label { display: block; margin: 16px 0 4px; color: var(--muted); }
.config input[type="text"], .config input[type="password"] { width: 100%; background: var(--surface); color: var(--fg); border: 1px solid var(--muted); border-radius: var(--radius); padding: 8px; }
.theme-cards { display: flex; gap: 12px; margin-top: 8px; }
.theme-card { flex: 1; border: 1px solid var(--muted); border-radius: var(--radius); padding: 12px; cursor: pointer; }
.theme-card.active { border-color: var(--accent); }
.cursor { display: inline-block; width: 7px; height: 14px; background: var(--accent); vertical-align: -2px; animation: blink 1s steps(1) infinite; }
@keyframes blink { 50% { opacity: 0; } }
```

- [ ] **Step 7: Write `frontend/src/main.tsx` and a placeholder `App.tsx`**

`frontend/src/main.tsx`:
```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
```

`frontend/src/App.tsx` (placeholder; replaced in Task 11):
```tsx
export default function App() {
  return <div className="topbar"><span>grimoire</span></div>;
}
```

- [ ] **Step 8: Copy the logo/icon assets from OLD**

Run (from repo root):
```bash
mkdir -p frontend/public
cp OLD/frontend/public/favicon.ico OLD/frontend/public/grimoire-32.png OLD/frontend/public/grimoire-128.png OLD/frontend/public/grimoire-256.png OLD/frontend/public/grimoire-512.png frontend/public/
```
Expected: five files now in `frontend/public/`.

- [ ] **Step 9: Install and verify dev server builds**

Run (from `frontend/`): `npm install` then `npm run build`
Expected: install succeeds; `vite build` writes `frontend/dist/` with no TypeScript errors.

- [ ] **Step 10: Commit**

```bash
git add frontend/package.json frontend/tsconfig.json frontend/vite.config.ts frontend/index.html frontend/src/main.tsx frontend/src/App.tsx frontend/src/index.css frontend/src/test-setup.ts frontend/public
git commit -m "chore(frontend): scaffold Vite+React+TS app and copy logo assets"
```

---

## Task 8: Theme system (per-file themes + provider)

**Files:**
- Create: `frontend/src/theme/types.ts`
- Create: `frontend/src/theme/themes/occult.ts`, `terminal.ts`, `ink.ts`, `index.ts`
- Create: `frontend/src/theme/ThemeProvider.tsx`
- Test: `frontend/src/theme/ThemeProvider.test.tsx`

- [ ] **Step 1: Write the failing test**

`frontend/src/theme/ThemeProvider.test.tsx`:
```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { ThemeProvider, useTheme } from "./ThemeProvider";

function Probe() {
  const { name, setTheme } = useTheme();
  return (
    <div>
      <span data-testid="name">{name}</span>
      <button onClick={() => setTheme("terminal")}>switch</button>
    </div>
  );
}

test("applies theme tokens and data-theme, and switches", () => {
  render(
    <ThemeProvider initial="occult">
      <Probe />
    </ThemeProvider>,
  );
  expect(document.documentElement.dataset.theme).toBe("occult");
  expect(document.documentElement.style.getPropertyValue("--accent")).toBe("#caa45a");

  fireEvent.click(screen.getByText("switch"));
  expect(screen.getByTestId("name").textContent).toBe("terminal");
  expect(document.documentElement.dataset.theme).toBe("terminal");
  expect(document.documentElement.style.getPropertyValue("--accent")).toBe("#3fae57");
});

test("unknown theme falls back to default", () => {
  render(<ThemeProvider initial="does-not-exist"><Probe /></ThemeProvider>);
  expect(document.documentElement.dataset.theme).toBe("occult");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run src/theme/ThemeProvider.test.tsx`
Expected: FAIL — modules under `./theme` do not exist.

- [ ] **Step 3: Write `frontend/src/theme/types.ts`**

```ts
export type Theme = {
  name: string;
  label: string;
  tokens: Record<string, string>;
};
```

- [ ] **Step 4: Write the three theme files**

`frontend/src/theme/themes/occult.ts`:
```ts
import type { Theme } from "../types";

const occult: Theme = {
  name: "occult",
  label: "Occult Grimoire",
  tokens: {
    "--bg": "#15110e",
    "--surface": "#0d0a08",
    "--fg": "#e8dcc6",
    "--muted": "#8a7a5c",
    "--accent": "#caa45a",
    "--font-display": 'Georgia, "Times New Roman", serif',
    "--font-body": 'Georgia, "Times New Roman", serif',
    "--radius": "3px",
  },
};
export default occult;
```

`frontend/src/theme/themes/terminal.ts`:
```ts
import type { Theme } from "../types";

const terminal: Theme = {
  name: "terminal",
  label: "Terminal Arcana",
  tokens: {
    "--bg": "#080a08",
    "--surface": "#0d120d",
    "--fg": "#9ff0a8",
    "--muted": "#3fae57",
    "--accent": "#3fae57",
    "--font-display": '"Courier New", ui-monospace, monospace',
    "--font-body": '"Courier New", ui-monospace, monospace',
    "--radius": "0px",
  },
};
export default terminal;
```

`frontend/src/theme/themes/ink.ts`:
```ts
import type { Theme } from "../types";

const ink: Theme = {
  name: "ink",
  label: "Ink & Paper",
  tokens: {
    "--bg": "#f4efe4",
    "--surface": "#ece4d3",
    "--fg": "#23201a",
    "--muted": "#8a8270",
    "--accent": "#7a2e22",
    "--font-display": 'Georgia, "Iowan Old Style", serif',
    "--font-body": 'Georgia, "Iowan Old Style", serif',
    "--radius": "2px",
  },
};
export default ink;
```

- [ ] **Step 5: Write the registry `frontend/src/theme/themes/index.ts`**

```ts
import type { Theme } from "../types";
import occult from "./occult";
import terminal from "./terminal";
import ink from "./ink";

export const DEFAULT_THEME = "occult";

// Register a new theme by adding its import to this array.
const all: Theme[] = [occult, terminal, ink];

export const themes: Record<string, Theme> = Object.fromEntries(
  all.map((t) => [t.name, t]),
);

export const themeList = all;

export function resolveTheme(name: string): Theme {
  return themes[name] ?? themes[DEFAULT_THEME];
}
```

- [ ] **Step 6: Write `frontend/src/theme/ThemeProvider.tsx`**

```tsx
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { resolveTheme } from "./themes";

type ThemeCtx = { name: string; setTheme: (name: string) => void };
const Ctx = createContext<ThemeCtx | null>(null);

function applyTheme(name: string): string {
  const theme = resolveTheme(name);
  const root = document.documentElement;
  for (const [key, value] of Object.entries(theme.tokens)) {
    root.style.setProperty(key, value);
  }
  root.dataset.theme = theme.name;
  return theme.name;
}

export function ThemeProvider({ initial, children }: { initial: string; children: ReactNode }) {
  const [name, setName] = useState(() => applyTheme(initial));

  useEffect(() => {
    setName(applyTheme(initial));
  }, [initial]);

  const setTheme = (next: string) => setName(applyTheme(next));

  return <Ctx.Provider value={{ name, setTheme }}>{children}</Ctx.Provider>;
}

export function useTheme(): ThemeCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
```

- [ ] **Step 7: Run test to verify it passes**

Run (from `frontend/`): `npx vitest run src/theme/ThemeProvider.test.tsx`
Expected: 2 passed.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/theme
git commit -m "feat(frontend): per-file theme registry and ThemeProvider"
```

---

## Task 9: API client + SSE reader + streaming reducer

**Files:**
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/api/stream.ts`
- Test: `frontend/src/api/stream.test.ts`

- [ ] **Step 1: Write the failing test**

`frontend/src/api/stream.test.ts`:
```ts
import { parseSSEChunk } from "./stream";

test("accumulates deltas and detects done", () => {
  const events: any[] = [];
  let buf = "";
  buf = parseSSEChunk(buf, 'data: {"delta": "Hel"}\n\n', (e) => events.push(e));
  buf = parseSSEChunk(buf, 'data: {"delta": "lo"}\n\ndata: {"done": true}\n\n', (e) => events.push(e));
  expect(events).toEqual([{ delta: "Hel" }, { delta: "lo" }, { done: true }]);
  expect(buf).toBe("");
});

test("holds a partial event until its terminator arrives", () => {
  const events: any[] = [];
  let buf = "";
  buf = parseSSEChunk(buf, 'data: {"delta": "Hel', (e) => events.push(e));
  expect(events).toEqual([]);
  buf = parseSSEChunk(buf, 'lo"}\n\n', (e) => events.push(e));
  expect(events).toEqual([{ delta: "Hello" }]);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run src/api/stream.test.ts`
Expected: FAIL — `./stream` does not exist.

- [ ] **Step 3: Write `frontend/src/api/stream.ts`**

```ts
export type ChatEvent = { delta?: string; done?: boolean; error?: { detail: string; kind: string } };

// Appends a chunk to `buffer`, emits each complete `data:` event, returns the leftover buffer.
export function parseSSEChunk(
  buffer: string,
  chunk: string,
  emit: (event: ChatEvent) => void,
): string {
  buffer += chunk;
  let idx: number;
  while ((idx = buffer.indexOf("\n\n")) !== -1) {
    const raw = buffer.slice(0, idx);
    buffer = buffer.slice(idx + 2);
    const line = raw.split("\n").find((l) => l.startsWith("data:"));
    if (!line) continue;
    const data = line.slice("data:".length).trim();
    if (!data) continue;
    try {
      emit(JSON.parse(data) as ChatEvent);
    } catch {
      // ignore malformed event fragments
    }
  }
  return buffer;
}
```

- [ ] **Step 4: Write `frontend/src/api/client.ts`**

```ts
import { parseSSEChunk, type ChatEvent } from "./stream";

export class ApiError extends Error {
  constructor(public status: number, public detail: string, public kind?: string) {
    super(detail);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(res.status, data.detail ?? res.statusText, data.kind);
  }
  return res.json() as Promise<T>;
}

export type Config = { model: string; theme: string; key_set: boolean };
export type ConvMeta = { id: string; title: string; model: string; created: string; updated: string };
export type Message = { role: "user" | "assistant"; content: string };
export type Conversation = { meta: { id: string; title: string }; messages: Message[] };

export const api = {
  getConfig: () => request<Config>("GET", "/api/config"),
  putConfig: (body: Partial<{ model: string; theme: string; openrouter_key: string }>) =>
    request<Config>("PUT", "/api/config", body),
  listConversations: () => request<ConvMeta[]>("GET", "/api/conversations"),
  createConversation: (title?: string) => request<{ id: string }>("POST", "/api/conversations", { title }),
  getConversation: (id: string) => request<Conversation>("GET", `/api/conversations/${id}`),

  async chat(id: string, content: string, onEvent: (e: ChatEvent) => void): Promise<void> {
    const res = await fetch(`/api/conversations/${id}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    if (!res.ok || !res.body) {
      const data = await res.json().catch(() => ({}));
      throw new ApiError(res.status, data.detail ?? res.statusText, data.kind);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer = parseSSEChunk(buffer, decoder.decode(value, { stream: true }), onEvent);
    }
  },
};
```

- [ ] **Step 5: Run test to verify it passes**

Run (from `frontend/`): `npx vitest run src/api/stream.test.ts`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api
git commit -m "feat(frontend): typed API client and SSE stream parser"
```

---

## Task 10: ConfigView

**Files:**
- Create: `frontend/src/routes/ConfigView.tsx`

This view has no dedicated test (covered manually + by the theme test); keep it small and token-driven.

- [ ] **Step 1: Write `frontend/src/routes/ConfigView.tsx`**

```tsx
import { useEffect, useState } from "react";
import { api, type Config } from "../api/client";
import { themeList } from "../theme/themes";
import { useTheme } from "../theme/ThemeProvider";

export default function ConfigView() {
  const { setTheme } = useTheme();
  const [config, setConfig] = useState<Config | null>(null);
  const [model, setModel] = useState("");
  const [key, setKey] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.getConfig().then((c) => {
      setConfig(c);
      setModel(c.model);
    });
  }, []);

  if (!config) return <div className="config">Loading…</div>;

  async function save(fields: Partial<{ model: string; theme: string; openrouter_key: string }>) {
    const next = await api.putConfig(fields);
    setConfig(next);
    setKey("");
    setSaved(true);
    if (fields.theme) setTheme(fields.theme);
    setTimeout(() => setSaved(false), 1500);
  }

  return (
    <div className="config">
      <h2>Configuration</h2>

      <label>OpenRouter API key</label>
      <input
        type="password"
        placeholder={config.key_set ? "A key is set — type to replace" : "sk-or-…"}
        value={key}
        onChange={(e) => setKey(e.target.value)}
      />

      <label>Model</label>
      <input type="text" value={model} onChange={(e) => setModel(e.target.value)} />

      <label>Theme</label>
      <div className="theme-cards">
        {themeList.map((t) => (
          <div
            key={t.name}
            className={"theme-card" + (config.theme === t.name ? " active" : "")}
            onClick={() => save({ theme: t.name })}
          >
            {t.label}
          </div>
        ))}
      </div>

      <p style={{ marginTop: 24 }}>
        <button
          className="primary"
          onClick={() => save({ model, ...(key ? { openrouter_key: key } : {}) })}
        >
          Save
        </button>
        {saved && <span style={{ marginLeft: 12, color: "var(--accent)" }}>Saved</span>}
      </p>
    </div>
  );
}
```

- [ ] **Step 2: Type-check**

Run (from `frontend/`): `npx tsc -b`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/ConfigView.tsx
git commit -m "feat(frontend): ConfigView (key, model, theme picker)"
```

---

## Task 11: ChatView + App wiring + theme hydration

**Files:**
- Create: `frontend/src/routes/ChatView.tsx`
- Replace: `frontend/src/App.tsx`

- [ ] **Step 1: Write `frontend/src/routes/ChatView.tsx`**

```tsx
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type ConvMeta, type Message } from "../api/client";

export default function ChatView({ keySet }: { keySet: boolean }) {
  const [convs, setConvs] = useState<ConvMeta[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [streaming, setStreaming] = useState("");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const streamRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.listConversations().then((list) => {
      setConvs(list);
      if (list.length && !activeId) selectConv(list[0].id);
    });
  }, []);

  useEffect(() => {
    streamRef.current?.scrollTo({ top: streamRef.current.scrollHeight });
  }, [messages, streaming]);

  async function selectConv(id: string) {
    setActiveId(id);
    const conv = await api.getConversation(id);
    setMessages(conv.messages);
    setStreaming("");
  }

  async function newConversation() {
    const { id } = await api.createConversation();
    setConvs(await api.listConversations());
    selectConv(id);
  }

  async function send() {
    if (!input.trim() || busy) return;
    let id = activeId;
    if (!id) {
      id = (await api.createConversation()).id;
      setConvs(await api.listConversations());
      setActiveId(id);
    }
    const content = input.trim();
    setInput("");
    setMessages((m) => [...m, { role: "user", content }]);
    setBusy(true);
    setError(null);
    let acc = "";
    try {
      await api.chat(id, content, (e) => {
        if (e.delta) {
          acc += e.delta;
          setStreaming(acc);
        } else if (e.error) {
          setError(e.error.detail);
        }
      });
      setMessages((m) => [...m, { role: "assistant", content: acc }]);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setStreaming("");
      setBusy(false);
    }
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      send();
    }
  }

  return (
    <div className="layout">
      <aside className="sidebar">
        <button onClick={newConversation}>+ New conversation</button>
        {convs.map((c) => (
          <div
            key={c.id}
            className={"conv-item" + (c.id === activeId ? " active" : "")}
            onClick={() => selectConv(c.id)}
          >
            {c.title}
          </div>
        ))}
      </aside>
      <section className="main">
        {!keySet && (
          <div className="banner">
            No OpenRouter key set. <Link to="/config">Set your key in Config</Link>.
          </div>
        )}
        {error && <div className="banner">{error}</div>}
        <div className="stream" ref={streamRef}>
          {messages.map((m, i) => (
            <div className="msg" key={i}>
              <div className="role">{m.role === "user" ? "You" : "Grimoire"}</div>
              <Markdown remarkPlugins={[remarkGfm]}>{m.content}</Markdown>
            </div>
          ))}
          {streaming && (
            <div className="msg">
              <div className="role">Grimoire</div>
              <Markdown remarkPlugins={[remarkGfm]}>{streaming}</Markdown>
              <span className="cursor" />
            </div>
          )}
        </div>
        <div className="inputbar">
          <textarea
            rows={3}
            placeholder="Speak your intent…  (Ctrl/Cmd+Enter to send)"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <button className="send" onClick={send} disabled={busy}>
            {busy ? "…" : "Send"}
          </button>
        </div>
      </section>
    </div>
  );
}
```

- [ ] **Step 2: Replace `frontend/src/App.tsx`**

```tsx
import { useEffect, useState } from "react";
import { Link, Route, Routes } from "react-router-dom";
import { api } from "./api/client";
import { ThemeProvider } from "./theme/ThemeProvider";
import { DEFAULT_THEME } from "./theme/themes";
import ChatView from "./routes/ChatView";
import ConfigView from "./routes/ConfigView";

export default function App() {
  const [theme, setTheme] = useState<string | null>(null);
  const [keySet, setKeySet] = useState(false);

  useEffect(() => {
    api
      .getConfig()
      .then((c) => {
        setTheme(c.theme);
        setKeySet(c.key_set);
      })
      .catch(() => setTheme(DEFAULT_THEME));
  }, []);

  if (theme === null) return null;

  return (
    <ThemeProvider initial={theme}>
      <div className="topbar">
        <Link to="/" style={{ fontWeight: 600 }}>
          ✦ grimoire
        </Link>
        <Link to="/config">Config</Link>
      </div>
      <Routes>
        <Route path="/" element={<ChatView keySet={keySet} />} />
        <Route path="/config" element={<ConfigView />} />
      </Routes>
    </ThemeProvider>
  );
}
```

- [ ] **Step 3: Type-check and build**

Run (from `frontend/`): `npx tsc -b && npm run build`
Expected: no TypeScript errors; `frontend/dist/` is produced.

- [ ] **Step 4: Run the full frontend test suite**

Run (from `frontend/`): `npm test`
Expected: theme + stream tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/ChatView.tsx frontend/src/App.tsx
git commit -m "feat(frontend): ChatView with SSE streaming and app wiring"
```

---

## Task 12: Unix scripts (install / run / shutdown)

**Files:**
- Create: `scripts/unix/install.sh`, `scripts/unix/run.sh`, `scripts/unix/shutdown.sh`

All scripts resolve the repo root from their own location so they work from any CWD.

- [ ] **Step 1: Write `scripts/unix/install.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

command -v python3 >/dev/null || { echo "Python 3.11+ not found"; exit 1; }
command -v node >/dev/null || { echo "Node 18+ not found"; exit 1; }

echo "Installing backend…"
cd "$ROOT/backend"
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"

echo "Installing frontend…"
cd "$ROOT/frontend"
npm install

echo "Creating desktop launcher…"
RUN="$ROOT/scripts/unix/run.sh"
chmod +x "$ROOT/scripts/unix/"*.sh
DESKTOP="${HOME}/Desktop"
if [ "$(uname)" = "Darwin" ]; then
  mkdir -p "$DESKTOP"
  printf '#!/usr/bin/env bash\nexec "%s"\n' "$RUN" > "$DESKTOP/Grimoire.command"
  chmod +x "$DESKTOP/Grimoire.command"
else
  ENTRY="[Desktop Entry]
Type=Application
Name=Grimoire
Exec=$RUN
Icon=$ROOT/frontend/public/grimoire-256.png
Terminal=false
Categories=Utility;"
  mkdir -p "$DESKTOP" "$HOME/.local/share/applications"
  echo "$ENTRY" > "$DESKTOP/grimoire.desktop"
  echo "$ENTRY" > "$HOME/.local/share/applications/grimoire.desktop"
  chmod +x "$DESKTOP/grimoire.desktop"
fi

echo "Done. Run with: $RUN"
```

- [ ] **Step 2: Write `scripts/unix/run.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNDIR="$ROOT/.run"
PIDFILE="$RUNDIR/pids"
URL="http://localhost:5173"
mkdir -p "$RUNDIR"

if [ -f "$PIDFILE" ] && kill -0 $(head -n1 "$PIDFILE") 2>/dev/null; then
  echo "grimoire is already running ($URL). Use shutdown.sh to stop it."
  exit 0
fi

cd "$ROOT/backend"
.venv/bin/python -m uvicorn grimoire.main:app --reload --port 8000 &
BACK=$!
cd "$ROOT/frontend"
npm run dev -- --port 5173 &
FRONT=$!
echo "$BACK" > "$PIDFILE"
echo "$FRONT" >> "$PIDFILE"

echo "grimoire running at $URL (backend pid $BACK, frontend pid $FRONT)"
sleep 2
if command -v open >/dev/null; then open "$URL"
elif command -v xdg-open >/dev/null; then xdg-open "$URL"
fi
wait
```

- [ ] **Step 3: Write `scripts/unix/shutdown.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PIDFILE="$ROOT/.run/pids"

if [ ! -f "$PIDFILE" ]; then
  echo "grimoire is not running."
  exit 0
fi

while read -r pid; do
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    sleep 1
    kill -9 "$pid" 2>/dev/null || true
  fi
done < "$PIDFILE"
rm -f "$PIDFILE"
echo "grimoire stopped."
```

- [ ] **Step 4: Make executable and sanity-check syntax**

Run (from repo root):
```bash
chmod +x scripts/unix/*.sh
bash -n scripts/unix/install.sh && bash -n scripts/unix/run.sh && bash -n scripts/unix/shutdown.sh
```
Expected: no syntax errors.

- [ ] **Step 5: Commit**

```bash
git add scripts/unix
git commit -m "feat(scripts): unix install/run/shutdown with desktop launcher"
```

---

## Task 13: Windows scripts (install / run / shutdown + pinnable launcher)

**Files:**
- Create: `scripts/windows/install.ps1`, `scripts/windows/run.ps1`, `scripts/windows/shutdown.ps1`, `scripts/windows/launch.vbs`

- [ ] **Step 1: Write `scripts/windows/launch.vbs`**

Runs `run.ps1` with a hidden window so the `.lnk` (targeting `wscript.exe`) is taskbar-pinnable and shows no console flash.
```vbs
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
runPs1 = scriptDir & "\run.ps1"
Set sh = CreateObject("WScript.Shell")
sh.Run "powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & runPs1 & """", 0, False
```

- [ ] **Step 2: Write `scripts/windows/install.ps1`**

```powershell
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Python 3.11+ not found" }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw "Node 18+ not found" }

Write-Host "Installing backend..."
Push-Location "$Root\backend"
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
Pop-Location

Write-Host "Installing frontend..."
Push-Location "$Root\frontend"
npm install
Pop-Location

Write-Host "Creating pinnable desktop launcher..."
$launch = "$Root\scripts\windows\launch.vbs"
$icon = "$Root\frontend\public\favicon.ico"
$wscript = "$env:SystemRoot\System32\wscript.exe"
$shell = New-Object -ComObject WScript.Shell

function New-GrimoireShortcut($path) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $wscript
    $lnk.Arguments = """$launch"""
    $lnk.WorkingDirectory = $Root
    $lnk.IconLocation = $icon
    $lnk.Description = "Grimoire"
    $lnk.Save()
}

$desktop = [Environment]::GetFolderPath("Desktop")
$programs = [Environment]::GetFolderPath("Programs")
New-GrimoireShortcut "$desktop\Grimoire.lnk"
New-GrimoireShortcut "$programs\Grimoire.lnk"

# Note: a wscript-targeted shortcut pins to the taskbar cleanly. Setting an explicit
# System.AppUserModel.ID requires the IPropertyStore COM API, which is brittle from
# PowerShell; the shortcut works and pins without it (it may group under the script host).
# Treated as a future refinement (see plan self-review).
Write-Host "Shortcut created in Desktop and Start Menu. Right-click it to 'Pin to taskbar'."
Write-Host "Done. Launch from the Start Menu / Desktop shortcut, or run scripts\windows\run.ps1"
```

- [ ] **Step 3: Write `scripts/windows/run.ps1`**

```powershell
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$RunDir = "$Root\.run"
$PidFile = "$RunDir\pids"
$Url = "http://localhost:5173"
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

if (Test-Path $PidFile) {
    $existing = Get-Content $PidFile | Select-Object -First 1
    if ($existing -and (Get-Process -Id $existing -ErrorAction SilentlyContinue)) {
        Write-Host "grimoire is already running ($Url). Use shutdown.ps1 to stop it."
        exit 0
    }
}

$back = Start-Process -FilePath "$Root\backend\.venv\Scripts\python.exe" `
    -ArgumentList "-m", "uvicorn", "grimoire.main:app", "--reload", "--port", "8000" `
    -WorkingDirectory "$Root\backend" -PassThru -WindowStyle Hidden
$front = Start-Process -FilePath "npm.cmd" `
    -ArgumentList "run", "dev", "--", "--port", "5173" `
    -WorkingDirectory "$Root\frontend" -PassThru -WindowStyle Hidden
Set-Content -Path $PidFile -Value @($back.Id, $front.Id)

Write-Host "grimoire running at $Url (backend $($back.Id), frontend $($front.Id))"
Start-Sleep -Seconds 2

# Prefer browser app mode for a chromeless, app-like window.
$edge = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
$chrome = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
if (Test-Path $edge) { Start-Process $edge "--app=$Url" }
elseif (Test-Path $chrome) { Start-Process $chrome "--app=$Url" }
else { Start-Process $Url }
```

- [ ] **Step 4: Write `scripts/windows/shutdown.ps1`**

```powershell
$ErrorActionPreference = "SilentlyContinue"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$PidFile = "$Root\.run\pids"

if (-not (Test-Path $PidFile)) {
    Write-Host "grimoire is not running."
    exit 0
}
foreach ($id in Get-Content $PidFile) {
    if ($id) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
}
Remove-Item $PidFile -Force
Write-Host "grimoire stopped."
```

- [ ] **Step 5: Syntax-check the PowerShell scripts**

Run (from repo root, PowerShell):
```powershell
$f = "scripts\windows\install.ps1","scripts\windows\run.ps1","scripts\windows\shutdown.ps1"
foreach ($p in $f) { [void][System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $p), [ref]$null, [ref]$null); Write-Host "$p OK" }
```
Expected: each prints `OK` with no parser errors.

- [ ] **Step 6: Commit**

```bash
git add scripts/windows
git commit -m "feat(scripts): windows install/run/shutdown with pinnable launcher"
```

---

## Task 14: End-to-end smoke test (manual)

**Files:** none (verification only)

- [ ] **Step 1: Install**

Run the platform install script (`scripts/windows/install.ps1` or `scripts/unix/install.sh`).
Expected: backend venv + frontend deps installed; a Grimoire shortcut/launcher created.

- [ ] **Step 2: Run and configure**

Launch via the desktop shortcut (or `run` script). In the browser app window:
- Open **Config**, paste an OpenRouter key, confirm a model id, pick each theme and confirm the UI restyles live, Save.
Expected: theme changes apply immediately; "A key is set" placeholder shows after reload.

- [ ] **Step 3: Chat**

Send a message with Ctrl/Cmd+Enter.
Expected: tokens stream into a live "Grimoire" block with a blinking cursor, then commit. A file appears under `~/.grimoire/conversations/` whose body contains the `**You:**` / `**Grimoire:**` transcript, and `config.md` holds the key/model/theme.

- [ ] **Step 4: Shutdown**

Run the platform shutdown script.
Expected: both processes stop; `.run/pids` removed.

- [ ] **Step 5: Commit (docs note, optional)**

If anything in the spec drifted during implementation, update `docs/superpowers/specs/2026-06-16-seed-design.md` and commit:
```bash
git add docs/superpowers/specs/2026-06-16-seed-design.md
git commit -m "docs: reconcile seed spec with implementation"
```

---

## Self-review notes

- **Spec coverage:** config.md + conversation files (Tasks 2–4); OpenRouter streaming client (Task 5); `/api/config` redaction, conversations, chat SSE with missing-key 409 (Task 6); FastAPI assembly + static serving + CORS (Task 6); three per-file themes + registry + provider (Task 8); typed API client + SSE reader (Task 9); ConfigView key/model/theme (Task 10); ChatView streaming + banners + App wiring/hydration (Task 11); cross-OS scripts + desktop launcher + pinnable Windows shortcut + app-mode run (Tasks 12–13). All spec sections map to a task.
- **AUMID caveat:** the spec calls for setting `System.AppUserModel.ID` on the shortcut. The COM `IPropertyStore` path is verbose and brittle from PowerShell; Task 13 creates a fully pinnable `wscript`-targeted shortcut and treats the explicit AUMID as best-effort (documented inline). If strict AUMID identity becomes required, revisit with a compiled helper. This is the one place the implementation intentionally narrows the spec.
- **Type consistency:** `ChatEvent`, `parseSSEChunk`, `api.*`, `Theme`/`resolveTheme`/`themeList`/`DEFAULT_THEME`, and `store` function names are used consistently across tasks.
