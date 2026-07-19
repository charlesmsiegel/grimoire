# Unified LLM connections (OpenRouter / Claude / OpenAI-compatible)

**Date:** 2026-07-18
**Status:** Approved

## Problem

grimoire's LLM config is a single flat slot: `~/.grimoire/config.md` holds one
`provider` (`"openrouter"` or `"claude"`) plus provider-specific fields
(`openrouter_key`, `model`, `claude_model`) directly in the config dict.
Switching `provider` doesn't lose data today only because there happen to be
exactly two providers with disjoint field names — but the design doesn't
generalize, and there's no way to point grimoire at a third kind of backend:
an arbitrary OpenAI-compatible chat-completions endpoint (e.g. z.ai's GLM
coding endpoint), which needs its own base URL, API key, model, and — because
many such endpoints are stricter than OpenRouter about message shape — a
prompt post-processing option.

Modeled on SillyTavern's connection profiles: multiple named, fully-specified
backends that you can freely switch between without re-entering credentials,
each remembering its own model list.

## Decision (user-approved)

Replace the flat provider fields with a unified list of **named connections**.
Each connection is one of three kinds — `openrouter`, `claude`,
`openai_compatible` — and owns its own credentials/model/settings. Exactly one
is "active" at a time (`config.active_connection_id`); switching which one is
active never touches the others' saved fields. All three kinds are symmetric
list entries — no artificial "only one OpenRouter connection" rule.

## Design

### 1. Data model — `store/llm_connections.py`

One file per connection at `<GRIMOIRE_HOME>/llm_connections/<id>.md`,
frontmatter-only (empty body), same flat shape for every kind — fields unused
by a given kind just stay empty:

```
kind: openrouter | claude | openai_compatible
name: "z.ai GLM"
base_url: ""              # openai_compatible only
api_key: ""                # openrouter + openai_compatible (optional for the latter)
model: ""                  # all three — free text for openrouter/openai_compatible,
                            # alias/pinned id for claude
post_process: none|strict  # openai_compatible only
rev: "3f9a1c2b"            # opaque token, regenerated on every write — see §5
```

The frontmatter format (`store/frontmatter.py`) is deliberately
string-scalars-only (see its docstring) — no lists or nested structures. Model
caching (below) therefore does **not** live in this file: it's a separate
plain-JSON sidecar, `<id>.models.json`, matching the existing precedent of
plain-JSON store files living alongside frontmatter records (e.g.
`played.json`, per `store/playing.py`). Absent until the first refresh.

```
{"models": [{"id": "...", "name": "...", "context": 128000, "prompt": "0.000002", "completion": "0.000006"}, ...],
 "fetched_at": "2026-07-18T12:00:00",
 "rev": "3f9a1c2b"}
```

`rev` tags which connection revision this cache was fetched under — see §5,
where it's what makes the model-list cache safe against a refresh racing a
concurrent edit without needing a lock.

`context`/`prompt`/`completion` are `null` (not `0`/`"0"`) when the server's
`/models` response didn't report them — see §4.

Module surface (mirrors `store/styles.py`'s CRUD shape, minus the
built-in/custom split — there are no built-in connections):

```python
_KINDS = ("openrouter", "claude", "openai_compatible")

class ConnectionNotFound(Exception): ...

def list_connections() -> list[dict]        # masked: key_set bool, no api_key
def read_connection(id: str) -> dict         # masked, for GET /llm-connections/{id}
def read_connection_raw(id: str) -> dict     # unmasked — internal use (LLM dispatch, models/refresh)
def create_connection(kind: str, name: str, **fields) -> str  # stamps a fresh `rev`
def update_connection(id: str, **fields) -> None   # kind is immutable — not accepted here; see §6 for api_key/base_url interaction; stamps a fresh `rev`
def delete_connection(id: str) -> None       # also removes the id's .models.json sidecar, if present
def get_active() -> dict | None              # unmasked; resolves config.active_connection_id
def cached_models(id: str) -> dict           # {"models": [], "fetched_at": ""} if no sidecar or its rev is stale — see §5
def set_cached_models(id: str, models: list[dict], rev: str) -> None  # unconditional write, tagged with rev
def ensure_migrated() -> None                # see §2
```

`create_connection`/`update_connection` use the existing `slugify`/`uniquify`
helpers from `store/paths.py` for ids, same as `styles.create_style` — **except**
during migration, which uses the fixed ids `openrouter`/`claude` directly (see
§2, for idempotency).

Every public function in this module calls `ensure_migrated()` first (cheap:
short-circuits on a marker-file check once migrated) — not just
`list_connections()`/`get_active()`. A caller reaching `create_connection()`
directly, before either of those, must not be able to leave the legacy
config fields stranded (see §2's second finding).

### 2. One-time migration

Directory existence is not a safe "already migrated" signal: `mkdir()` and
the two seed connections are three separate writes, and a crash, disk error,
or a direct `create_connection()` call landing between them would leave a
partial directory that an existence-only check would wrongly treat as
complete — stranding the legacy `openrouter_key`/`claude_model` fields
permanently, since `config.py`'s narrowed schema (below) stops exposing them
the moment this change ships. Instead:

- Migration writes a marker file, `<GRIMOIRE_HOME>/llm_connections/.migrated`,
  as its **last** step — only that marker's existence gates a no-op return.
- The two seed connections use **fixed ids** (`openrouter`, `claude`), not
  `uniquify`-generated ones, and each is only (re-)created if its file is
  **missing or fails to parse as a valid connection** (not merely
  "doesn't exist") — so a retry after a partial failure (the process died
  mid-write, leaving a truncated/corrupt `openrouter.md`) redoes that seed
  instead of mistaking garbage for done, while a retry after a clean prior
  success leaves it alone instead of duplicating.
- `active_connection_id` is only written if it's currently unset.

This deliberately does not adopt the atomic temp-file-plus-`os.replace`
pattern `store/rolls.py`/`store/proposals.py` use for concurrent-writer,
gameplay-critical JSON — that machinery exists specifically for high-frequency
writes from multiple simultaneous route handlers during active play.
Connection records are rare, single-user, settings-UI edits, the same
profile as `store/styles.py` and `store/config.py` (both plain
`write_text`, no atomicity) — validate-before-skip closes the realistic
"crash mid-write" gap for a one-time migration without importing locking
infrastructure this class of record has never needed elsewhere.

```python
def ensure_migrated() -> None:
    _dir().mkdir(parents=True, exist_ok=True)
    marker = _dir() / ".migrated"
    if marker.exists():
        return
    # Read the pre-migration fields directly off the frontmatter file — NOT
    # via config.read_config(), whose declared key set drops them the moment
    # this change ships (see _CONFIG_KEYS below). A file that predates this
    # change still has them physically present; parse_frontmatter returns
    # whatever keys exist regardless of the "official" schema.
    path = config._config_path()
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8")) if path.exists() else ({}, "")
    if not _valid_connection_file("openrouter"):
        _write_raw("openrouter", kind="openrouter", name="OpenRouter",
                    api_key=meta.get("openrouter_key", ""),
                    model=meta.get("model", config.DEFAULT_MODEL),
                    base_url="", post_process="none")
    if not _valid_connection_file("claude"):
        _write_raw("claude", kind="claude", name="Claude",
                    model=meta.get("claude_model", config.DEFAULT_CLAUDE_MODEL),
                    base_url="", api_key="", post_process="none")
    if not config.read_config().get("active_connection_id"):
        active = "openrouter" if meta.get("provider", "openrouter") == "openrouter" else "claude"
        config.write_config(active_connection_id=active)
    marker.write_text("1", encoding="utf-8")


def _valid_connection_file(id: str) -> bool:
    """Missing, unreadable, or lacking a recognized `kind` all count as
    "needs (re)seeding" — covers both "never written" and "process died
    mid-write, leaving a truncated file" without requiring atomic writes.
    `dump_frontmatter` writes the closing `---` fence last, after every
    meta line, so `parse_frontmatter` finding a valid fence already implies
    the whole meta block made it to disk intact for any write that's a
    single small write_text() call (a connection record is a few hundred
    bytes, well inside one filesystem block) — a torn/reordered partial
    write producing a fenced-but-incomplete record is not impossible, just
    the same negligible-probability class of risk this codebase already
    accepts for every other frontmatter file (styles, config). Checking
    `kind` is one of the three recognized values (not just present) is the
    cheap, meaningful tightening; full schema/field-completeness validation
    would be diminishing returns against that residual."""
    p = _path(id)
    if not p.exists():
        return False
    try:
        meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return False
    return meta.get("kind") in _KINDS
```

(`_write_raw` is the same frontmatter-dump primitive `create_connection`
uses internally, just called with a caller-chosen id instead of a
`uniquify`d one.)

A brand-new install (no `config.md` at all) takes the same path with empty
defaults, which is now the zero-config bootstrap: an empty-keyed OpenRouter
connection and an empty-keyed Claude connection, OpenRouter active — matching
today's `DEFAULT_PROVIDER`.

`config.py`'s `_CONFIG_KEYS`/defaults drop `provider`, `openrouter_key`,
`model`, `claude_model` and gain `active_connection_id` (default `""`,
resolved to a real id by migration before any caller sees it). Legacy fields
already written to an existing `config.md` are left in the file untouched
(not actively stripped) — inert clutter, never read again except by
`ensure_migrated`'s direct-frontmatter read, and harmless if a user ever
manually deletes `llm_connections/` along with its `.migrated` marker
(migration simply reconstructs the same two connections from the
still-present snapshot).

### 3. Config & dispatch

`routes.py` replaces the `cfg = store.read_config(); _require_key(cfg)`
pattern (used at ~10 call sites before every `client.stream`/`client.complete`
call) with a single `_require_connection()`:

```python
def _require_connection() -> dict:
    conn = store.llm_connections.get_active()
    if conn is None:
        raise HTTPException(status_code=409, detail={"detail": "No LLM connection selected", "kind": "missing_key"})
    if conn["kind"] == "openrouter" and not conn["api_key"]:
        raise HTTPException(status_code=409, detail={"detail": "OpenRouter key not set", "kind": "missing_key"})
    if conn["kind"] == "openai_compatible" and not conn["base_url"]:
        raise HTTPException(status_code=409, detail={"detail": "Endpoint base URL not set", "kind": "missing_key"})
    return conn
```

Every call site changes from `cfg = store.read_config(); _require_key(cfg)`
to `conn = _require_connection()`, and `client.stream(messages, cfg)` /
`client.complete(messages, cfg)` to the same call with `conn`.

`LLMClient` (`llm.py`) dispatches on `conn["kind"]` instead of
`conn["provider"]`, reading fields off the resolved connection dict — it
stays a pure function of its inputs, no coupling to the store:

```python
class LLMClient:
    def __init__(self, openrouter=None, claude=None, openai_compatible=None):
        from .claude_agent import ClaudeAgentClient
        from .openrouter import OpenRouterClient
        from .openai_compatible import OpenAICompatibleClient
        self._openrouter = openrouter or OpenRouterClient()
        self._claude = claude or ClaudeAgentClient()
        self._openai_compatible = openai_compatible or OpenAICompatibleClient()

    def stream(self, messages: list[dict], conn: dict):
        kind = conn.get("kind", "openrouter")
        if kind == "claude":
            return self._claude.stream(messages, conn.get("model") or "opus")
        if kind == "openai_compatible":
            return self._openai_compatible.stream(
                messages, conn.get("model", ""), conn.get("api_key", ""),
                conn["base_url"], strict=conn.get("post_process") == "strict")
        return self._openrouter.stream(messages, conn["model"], conn.get("api_key", ""))

    async def complete(self, messages: list[dict], conn: dict) -> str:
        return "".join([chunk async for chunk in self.stream(messages, conn)])

    async def aclose(self) -> None:
        await self._openrouter.aclose()
        await self._openai_compatible.aclose()
```

`_public_config()` drops the provider-specific fields and adds
`active_connection_id`/`active_connection`/`ready`. `active_connection_id` in
the response is derived from the resolved `active`
connection, not read back off the `cfg` argument — `get_active()` can itself
trigger `ensure_migrated()`, which writes a fresh `active_connection_id` into
`config.md` *after* `cfg` was already read by the caller. Trusting `cfg`'s
copy here would show the stale (pre-migration, empty) id on the very first
request after upgrade, even though `active_connection`/`ready` already
reflect the real, newly-migrated connection.

```python
def _public_config(cfg: dict) -> dict:
    active = store.llm_connections.get_active()
    return {"theme": cfg["theme"], "system_prompt": cfg.get("system_prompt", ""),
            "quote_color": cfg.get("quote_color", "off"),
            "user_label": cfg.get("user_label", "You"),
            "assistant_label": cfg.get("assistant_label", "Grimoire"),
            "default_style_id": cfg.get("default_style_id", ""),
            "active_connection_id": active["id"] if active else "",
            "active_connection": ({"id": active["id"], "kind": active["kind"], "name": active["name"]}
                                   if active else None),
            "ready": _connection_ready(active)}

def _connection_ready(conn: dict | None) -> bool:
    if conn is None:
        return False
    if conn["kind"] == "openrouter":
        return bool(conn["api_key"])
    if conn["kind"] == "openai_compatible":
        return bool(conn["base_url"])
    return True  # claude never needs a key
```

The frontend's `App.tsx` status pill (currently hardcoded
`OPENROUTER · {keySet ? "CONNECTED" : "NO KEY"}`) and `CampaignView.tsx`'s
`!keySet` banner ("No OpenRouter key set") both key off `config.ready` /
`config.active_connection` instead — already slightly wrong for Claude today
(the banner shows regardless of provider), and this change is what puts a
third provider in the same blast radius, so it's fixed here rather than left
to compound.

### 4. New provider client — `openai_compatible.py`

Same shape as `openrouter.py` (SSE streaming, same error-kind normalization
via a `_status_kind` helper), parameterized on `base_url` instead of a fixed
URL. `key` is optional — the `Authorization` header is simply omitted when
empty, rather than erroring (many self-hosted OpenAI-compatible servers don't
require auth). No `tools` field is ever added to the request payload — true
of every existing provider in this codebase already, so "no tools" needs no
code, just a comment recording the invariant.

```python
class OpenAICompatibleError(LLMError): ...

def _strict_messages(messages: list[dict]) -> list[dict]:
    """Fold system messages into adjacent user turns and guarantee the result
    starts with role=user and alternates strictly — required by chat-completion
    backends (e.g. z.ai's GLM coding endpoint) that reject a system message
    mid-conversation or a non-user opening turn."""
    folded: list[dict] = []
    pending: list[str] = []

    def flush(extra: str = "") -> str:
        nonlocal pending
        parts = [*pending, extra] if extra else list(pending)
        pending = []
        return "\n\n".join(parts)

    def append(role: str, content: str) -> None:
        if folded and folded[-1]["role"] == role:
            folded[-1]["content"] += "\n\n" + content
        else:
            folded.append({"role": role, "content": content})

    for m in messages:
        if m["role"] == "system":
            pending.append(m["content"])
        elif m["role"] == "user":
            append("user", flush(m["content"]))
        elif m["role"] == "assistant":
            if pending:
                append("user", flush())
            append("assistant", m["content"])
        else:
            # grimoire's context.py only ever emits system/user/assistant
            # today, but silently folding an unrecognized role (a future
            # tool/developer message, a malformed record) into "assistant"
            # would misattribute its content as model-authored. Fail closed.
            raise OpenAICompatibleError("bad_response", f"unsupported message role: {m['role']!r}")
    if pending:
        append("user", flush())
    if not folded or folded[0]["role"] != "user":
        folded.insert(0, {"role": "user", "content": "(continue)"})
    return folded


class OpenAICompatibleClient:
    def __init__(self, http: httpx.AsyncClient | None = None): ...

    async def stream(self, messages, model: str, key: str, base_url: str,
                      strict: bool = False) -> AsyncIterator[str]:
        if not base_url:
            raise OpenAICompatibleError("missing_key", "No base URL configured")
        payload_messages = _strict_messages(messages) if strict else messages
        url = base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        # ...same SSE-parsing / error-normalizing loop as OpenRouterClient.stream

    async def complete(self, messages, model, key, base_url, strict=False) -> str: ...

    async def list_models(self, base_url: str, key: str) -> list[dict]:
        """GET {base_url}/models -> [{id, name, context, prompt, completion}],
        matching frontend/src/api/models.ts's OpenRouter parsing exactly so the
        UI can reuse the same Model type and formatting helpers. Fields the
        server doesn't report come back None, not 0/"" — see §5."""
        ...

    async def aclose(self) -> None: ...
```

### 5. Model list caching, with pricing/context

Applies only to `openai_compatible` connections — OpenRouter keeps its
existing separate public-catalog picker (`api/models.ts`'s `getModels()`,
unrelated to any single connection's key), and Claude has no fetchable list
(hardcoded aliases/pinned ids, unchanged).

`list_models()` parses `{base_url}/models` the way `models.ts` already parses
OpenRouter's catalog — `id`, `name` (falls back to `id`), `context_length`,
`pricing.prompt`/`pricing.completion` — but most self-hosted or
subscription-based coding endpoints won't report pricing or context at all,
so **absent fields are stored as `null`**, not defaulted to `0`/`"0"`.
Defaulting to `0` would make the existing `priceLabel()` helper report
"missing data" as "Free", which is wrong.

The `GET /llm-connections/{id}` route handler merges `read_connection(id)`
with `cached_models(id)` into one response body (adding `models`/
`fetched_at` alongside the connection's own fields) — no network call,
instant, possibly empty (`{"models": [], "fetched_at": ""}`) if never
fetched. `list_connections()`/the list route do **not** include cached
models — only the single-connection detail view needs them. `POST
/llm-connections/{id}/models/refresh` does the live fetch and returns the
fresh list; 400 if the connection's kind isn't `openai_compatible`. Whether
the result is *persisted* to the sidecar depends on the `rev` check below —
the response always reflects what was just fetched either way.

Frontend: `frontend/src/routes/ModelCombobox.tsx` is generalized to take
`models: Model[]` as a prop instead of always calling `getModels()`
internally — a small, directly-motivated reuse (not a speculative
refactor): OpenRouter's caller now passes `getModels()`'s result explicitly,
and the new connection editor passes the active connection's cached models.
Rows only render the context/price chip when the value isn't `null`, so a
custom endpoint with no pricing data shows id + name only — no fake "Free".

**Sidecar hygiene** (belt-and-suspenders, not the correctness mechanism —
see the `rev` gate below for that): `update_connection()` clears the
`.models.json` sidecar (back to the empty `{"models": [], "fetched_at": ""}`
state) whenever `base_url` is part of the update and differs from the
stored value; `delete_connection()` always removes the sidecar file; and
`create_connection()` unconditionally deletes any pre-existing sidecar for
the id it's about to write, before writing the record, covering a crash
between `delete_connection`'s two deletes or a sidecar left behind by any
other path. Without the `rev` gate these would be the only thing standing
between a repointed/reused connection and stale cross-endpoint data; with
it, they're just tidiness — an orphaned sidecar left behind despite all
three would still fail the `rev` comparison on read and never surface.

**The remaining race is real even for one user in one tab**, and worth
closing rather than accepting: a slow `models/refresh` network call can
still be in flight when the user edits the same connection's `base_url` or
deletes and recreates it (same name → same freed slug) — ordinary async
interleaving, no second user or device required. "Clear on write" doesn't
help here, because the *stale refresh's own write* happens **after** the
clear.

Closing it doesn't need a lock, but a naive "compare `rev`, then write if it
still matches" check isn't enough either — that's a check-then-act gap of
its own (an update/delete can land in the window between the comparison
succeeding and the sidecar write actually landing). Instead, the freshness
check lives entirely on the **read** side, where there's no gap to race:

- Every connection record carries an opaque `rev` token
  (`secrets.token_hex(8)`), regenerated on every `create_connection`/
  `update_connection` write.
- `set_cached_models(id, models, rev)` tags the sidecar with the `rev`
  that was current *before* the network fetch started, and writes
  **unconditionally** — no comparison, no conditional skip, nothing to race.
- `cached_models(id)` — the sole read path, used by both the
  `GET /llm-connections/{id}` merge and internally — compares the sidecar's
  tagged `rev` against the connection's **current** `rev` at the moment of
  the read, and returns the empty shape (`{"models": [], "fetched_at": ""}`)
  on any mismatch (or if the connection no longer exists). A write that
  lands after a concurrent update/delete is written with the *old* `rev` it
  was fetched under; the very next read compares against the *new* `rev`
  and correctly treats it as stale — regardless of exactly when the write
  happened to land relative to the edit.
- `POST /llm-connections/{id}/models/refresh` captures `rev`, fetches,
  calls `set_cached_models(id, models, rev)` unconditionally, and returns
  `{"models": ..., "fetched_at": ..., "rev": rev}` directly in its HTTP
  response (the request that asked for it gets its answer) regardless of
  whether that write turns out to be the one future reads see — that's
  decided later, per read, by `cached_models()`.
- A delete-then-recreate at the same id gets a fresh `rev` from
  `create_connection`, so it's covered by the same read-side check even
  though the id repeats; this makes the existing "sidecar cleared on
  `base_url` change / deleted on `delete_connection` / cleared on
  `create_connection`" rules **redundant for correctness** (any leftover
  sidecar just fails the rev comparison) — they're kept anyway, purely as
  disk hygiene so stale `.models.json` files don't accumulate indefinitely,
  not because anything still depends on them for correctness.

A genuinely simultaneous edit to the *same* field from two different
requests (e.g. two rapid `update_connection` calls) is still last-write-wins
with no merge — that residual is the ordinary, benign kind every settings
form in this codebase already has, not a correctness or credential-exposure
issue, and isn't worth defending against here.

**One more path the `rev` gate doesn't cover: the refresh response itself.**
The gate protects every *later* read (`GET /llm-connections/{id}`), but the
in-flight refresh call's own HTTP response still hands back whatever it
fetched, even if the connection changed underneath it while the request was
outstanding. If the same edit session saved a `base_url` change (or
deleted/recreated the connection) before that response arrived, the editor
could apply a stale response over the connection it's now actually showing.
This is an ordinary stale-async-response bug, not a new architectural gap —
the same shape `ModelCombobox.tsx`/`StyleGuideEditor.tsx` already guard
against elsewhere in this codebase (an `alive`/mounted-ref check around a
`.then()`). The connection editor's refresh handler applies the same
pattern, comparing the response's `rev` against the connection currently
open in the form and discarding the response if they don't match, rather
than assuming an in-flight request's answer is still relevant when it
lands.

### 6. Routes

```
GET/POST   /llm-connections
GET/PUT/DELETE /llm-connections/{id}
POST       /llm-connections/{id}/models/refresh
```

`ConnectionCreate` requires `kind` (`Literal["openrouter","claude","openai_compatible"]`)
and `name`; `ConnectionUpdate` omits `kind` entirely — immutable after
creation, avoiding partial-field states from switching a connection's kind
mid-life (rename/recreate instead). `api_key` on update follows the existing
"type to replace" convention (`openrouter_key` today): omitted/empty means
keep the stored key.

**Except when `base_url` changes.** OpenRouter's URL is fixed, so this
ambiguity never mattered before — but a custom endpoint's `base_url` is
user-editable, and "omitted means keep the key" would silently carry a
credential over to whatever new host the user just pointed the connection
at, with no way to tell from the update call whether that was intended.
`update_connection()` therefore clears `api_key` automatically whenever the
update changes `base_url` to a different value, unless that same call also
supplies a new `api_key` — repointing a connection always requires
re-entering (or deliberately re-confirming) its credential, never silently
carries the old one to a new host.

### 7. Frontend

- Nav entry **"Connections"** (`/connections`, next to Styles) →
  `ConnectionsView.tsx` → `ConnectionEditor.tsx`, the standard two-pane
  list/detail editor per `CLAUDE.md` (sticky rail, view/edit modes, Edit
  button, `.detail-sidebar` metadata) — closely mirrors
  `StyleGuideEditor.tsx`. Rail rows show name + kind badge; sidebar has a
  "Set as active" button (or an "Active" badge if already active) and Delete.
  `+ New` shows a kind picker first (locked once the form is created), then
  kind-conditional fields: openrouter → API key + `ModelCombobox` fed by
  `getModels()`; claude → the existing `CLAUDE_ALIASES`/`CLAUDE_PINNED`
  select, unchanged; openai_compatible → base URL, API key, `ModelCombobox`
  fed by cached models + a "Refresh" button (shows last-fetched time), and a
  post-processing select (None / Strict).
- `ConfigView.tsx`'s "LLM provider" section (today's provider `<select>` +
  the openrouter/claude conditional block, lines 134–190) shrinks to a single
  dropdown of connections (`active_connection_id`) plus a link to
  `/connections` to manage them. `ModelCombobox` is no longer imported here.
- `App.tsx`/`CampaignView.tsx`: `keySet` becomes `ready`, sourced from
  `config.ready`; the header pill shows `config.active_connection?.name` in
  place of the hardcoded "OPENROUTER".

## Non-impacts

- `context.py` message-building is unchanged — it still produces the same
  `{role, content}` list regardless of provider. The strict-mode transform
  happens only inside `openai_compatible.py`, right before the request is
  sent, never touching persisted history or other providers.
- OpenRouter/Claude behavior is unchanged in substance — the fields just move
  from flat config into a connection record.
- Prose style guides (`store/styles.py`, `StyleGuideEditor.tsx`) are untouched
  and unrelated; they're the architectural precedent, not a dependency.

## Known limitations (accepted)

- **Sync races.** `GRIMOIRE_HOME` can be a synced folder across devices;
  connections created independently on two devices before a sync could
  produce duplicate ids/names. Pre-existing risk class for any store record
  under a folder-sync tool (styles, calendars, etc.), not new here — out of
  scope.
- **Cached models go stale** until manually refreshed — by design, per the
  explicit "store locally, refresh if we want" request.
- **No locking** around `active_connection_id`/`config.md` writes, or around
  two genuinely simultaneous edits to the same connection's fields (plain
  last-write-wins, same as any other settings form in this codebase) —
  consistent with the rest of the store (single-user/local app; the same
  acceptance appears in prior specs, e.g. greeting availability's
  played-mark writes). The one race worth closing —
  `models/refresh` racing an `update`/`delete`/recreate on the *same*
  connection, which could otherwise persist stale cross-endpoint model data
  — is closed via the `rev` token in §5, not accepted as a limitation.

## Tests

Backend:

- `test_llm_connections_store.py` — CRUD round-trip per kind; `key_set`
  masking; `cached_models`/`set_cached_models` sidecar round-trip;
  `ensure_migrated()` idempotency, correct seeding from a pre-existing
  `config.md` (including a config file predating this change, with
  `provider`/`openrouter_key`/`model`/`claude_model` present), correct
  zero-config seeding with no `config.md` at all, and that a second call
  after connections already exist is a no-op even if the directory is now
  empty. **Crash-recovery**: create only the `openrouter` connection file
  (simulating a migration that died before `claude`/the marker), then call
  `ensure_migrated()` again — asserts it completes the rest without
  duplicating the OpenRouter connection. **Corrupt-seed recovery**: write a
  truncated/empty `openrouter.md` (no parseable `kind`) before calling
  `ensure_migrated()` — asserts it's treated as unseeded and rewritten from
  the legacy config fields, not skipped. **Preemption**: call
  `create_connection()` directly on a fresh store (skipping
  `list_connections()`/`get_active()`), then call `ensure_migrated()` —
  asserts the legacy config fields are still picked up rather than silently
  dropped. **Key-clearing**: `update_connection(id, base_url=new)` without a
  new `api_key` clears the stored key; supplying both in the same call keeps
  the new key; changing unrelated fields alone leaves the key untouched.
  **Sidecar lifecycle**: `set_cached_models` then `update_connection(id,
  base_url=new)` resets the sidecar to empty; `delete_connection` removes
  the sidecar file; a new connection reusing a deleted one's slug starts
  with no cached models — including when the sidecar is left behind
  deliberately (simulating a crash between the two deletes) rather than via
  the normal delete path, to prove `create_connection`'s clear-before-write
  is what's covering it, not `delete_connection`'s own cleanup. **`rev`
  stamping**: `create_connection`/`update_connection` each produce a `rev`
  different from the previous value; a delete followed by a same-name
  recreate produces a `rev` different from the deleted connection's.
- `test_openai_compatible.py` — SSE happy path and error normalization
  (mirrors `test_openrouter.py`'s `httpx.MockTransport` pattern); `key`
  omitted from headers when empty; `_strict_messages()` unit tests: system
  before a user turn folds into it; system before an assistant turn becomes
  its own user turn; a trailing system message (grimoire's `post_history`)
  becomes a final user turn; folding-induced adjacent same-role runs
  re-merge; an assistant-first/empty list gets the defensive placeholder
  user turn; a message with an unrecognized role raises
  `OpenAICompatibleError("bad_response", ...)` rather than being folded in
  as assistant content. `list_models()`: pricing/context present vs. absent
  → `None`, not `0`/`""`.
- `test_llm.py` — dispatch tests rekeyed from `provider`/`openrouter_key`/
  `claude_model` to `kind`/`api_key`/`model`; new `openai_compatible`
  dispatch case (with `strict` passed through correctly).
- Route tests — `_require_connection()`'s three 409 cases (no active
  connection; openrouter missing key; openai_compatible missing base_url);
  `POST /llm-connections/{id}/models/refresh` 400s for openrouter/claude
  kinds; existing scene-generation route tests get their fixtures updated to
  seed a connection instead of flat config fields. **Stale-refresh
  rejection**: mock the outbound `/models` fetch to block on an event the
  test controls; while it's "in flight," call `update_connection` (change
  `base_url`) from the test — this both changes `rev` and clears the
  sidecar; release the fetch, letting `set_cached_models` write its result
  tagged with the now-superseded `rev`; assert the refresh's own HTTP
  response still contains the freshly-fetched models, but a subsequent
  `GET /llm-connections/{id}` shows an empty cache, not the stale write.
  Repeat with delete+recreate at the same id in place of the update, to
  prove the write lands (unconditionally) yet is still filtered on read.
- `test_llm_connections_store.py` also covers `cached_models()`'s
  read-side gate directly, independent of any route/timing setup: write a
  sidecar via `set_cached_models(id, models, rev="stale")` while the
  connection's actual current `rev` is something else — `cached_models(id)`
  returns the empty shape, not `models`; writing with the connection's
  actual current `rev` makes it visible again.

Frontend:

- `ConnectionEditor.test.tsx` (list/detail pattern, per `CLAUDE.md`): row
  click shows the read-only view; Edit reveals the form; `+ New` opens the
  form directly with a kind picker; "Refresh" populates the combobox from a
  mocked API response; Save persists and returns to view; "Set as active"
  updates `config.active_connection_id`.
- `ConfigView.test.tsx` — rewritten around the single active-connection
  dropdown, replacing the old provider-branch assertions.
- `ModelCombobox.test.tsx` — generalized prop-driven behavior: renders
  passed `models` without self-fetching; price/context chip omitted when
  `null`.

Placeholder names in any fixtures follow the repo convention (Seraphine,
Mara, Winifred, Realm, Saltmarch) — never real content.
