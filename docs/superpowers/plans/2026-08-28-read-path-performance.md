# Read-Path Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make navigating to and opening campaign/world pages fast on a large
library by memoizing per-file derivations, deduplicating intra-request reads,
answering unchanged conditional GETs with `304`s, and rendering stale-while-
revalidate on the client.

**Architecture:** Four layers, independently shippable, in dependency order:
(1) extend `store/statcache.py` and apply it to every listing's per-file
parse; (2) remove duplicate reads inside single handlers; (3) an in-memory
per-app read epoch spent as weak ETags on the hot GET routes; (4) a bounded
client payload cache with per-path generations. No store format changes; every
endpoint's JSON is byte-identical to today.

**Tech Stack:** FastAPI + Starlette (raw ASGI middleware), pytest,
React/vitest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-28-read-path-performance-design.md`
— read it first; every design argument lives there, including the
"Implementation notes carried from review" section this plan implements. Four
review threads left open on PR #443 are also inputs; each is resolved by a
named task below.

## Global Constraints

- pydantic usage stays v1/v2-agnostic; nothing here touches models, but new
  route params must not use `Field`/validators (CLAUDE.md).
- Imports at module scope, acyclic; inside `store/`, cross-package imports
  bind a **submodule** (`from . import epoch` then `epoch.bump_all()`), never
  a function by value (CLAUDE.md, `test_import_guard.py`).
- Every store write goes through `store.atomic` — this plan adds **no** store
  writes; `store/epoch.py` is in-memory only.
- Backend tests isolate the store via
  `monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))`.
- Memo tests must back-date fixture mtimes past the racy window; reuse the
  `_age` helper pattern from `backend/tests/test_statcache.py:14-18`.
- Run vitest **from** `frontend/`; `make check-web` does it right.
- Commit messages are plain descriptive sentences in the repo's style (see
  `git log`) — no `feat:`/`fix:` prefixes, no model names.
- The three lint gates are ratcheted: new code must be clean; if a change
  *resolves* a baselined finding, run `make baseline` and commit the smaller
  file with the fix (CONTRIBUTING.md).
- The frozen-campaign sweep (`snapshot.json`) must not change — pure reuse
  may not move any output.
- `EPOCH_TTL_SECONDS = 30.0` (spec: structural guess, tune later).
- Frontend cache budget `MAX_ENTRIES = 64` (spec: small LRU; values are
  whole record lists).
- Scene-head/turns memo pool budget `POOL_ENTRIES = 65536` (spec: sized above
  a library's sweep working set; a head row is a small dict).

---

### Task 1: `statcache` — inode in the signature, per-pool budgets

**Files:**
- Modify: `backend/src/grimoire/store/statcache.py`
- Test: `backend/tests/test_statcache.py`

**Interfaces:**
- Produces: `signature(*paths, absent_ok=False) -> tuple | None` — each
  path's element becomes `(str(p), st_mtime_ns, st_size, st_ino)`; with
  `absent_ok=True` a missing path contributes the sentinel
  `(str(p), "absent")` instead of failing the whole signature.
- Produces: `memo(kind, sig, compute, *, pool=None, max_entries=None)` —
  `max_entries` overrides `MAX_ENTRIES` for caller pools.

- [ ] **Step 1: Write the failing tests** (append to `backend/tests/test_statcache.py`)

```python
def test_signature_changes_on_inode_replacement(tmp_path):
    """A rename-replace that preserves mtime and size still invalidates."""
    p = tmp_path / "a.md"
    p.write_text("aaaa", encoding="utf-8")
    _age(p)
    before = statcache.signature(p)
    st = p.stat()
    q = tmp_path / "a.md.tmp"
    q.write_text("bbbb", encoding="utf-8")   # same size, new inode
    os.utime(q, ns=(st.st_mtime_ns, st.st_mtime_ns))
    os.replace(q, p)
    assert statcache.signature(p) != before


def test_signature_absent_ok_yields_cacheable_sentinel(tmp_path):
    """A deliberately-absent companion file is a valid, cacheable state."""
    p = tmp_path / "present.md"
    p.write_text("x", encoding="utf-8")
    _age(p)
    missing = tmp_path / "missing.json"
    sig = statcache.signature(p, missing, absent_ok=True)
    assert sig is not None
    # ...and its creation invalidates.
    missing.write_text("{}", encoding="utf-8")
    _age(missing)
    assert statcache.signature(p, missing, absent_ok=True) != sig
    # Without the flag, a missing path still voids the signature.
    assert statcache.signature(p, tmp_path / "also-missing") is None


def test_memo_pool_budget_override(tmp_path):
    pool: dict = {}
    paths = []
    for i in range(4):
        p = tmp_path / f"f{i}.md"
        p.write_text(str(i), encoding="utf-8")
        paths.append(p)
    _age(*paths)
    for p in paths:
        statcache.memo("k", statcache.signature(p), lambda p=p: p.name,
                       pool=pool, max_entries=2)
    assert len(pool) <= 2


def test_memo_pool_holds_working_set_larger_than_shared_budget(tmp_path):
    """A repeated sweep bigger than MAX_ENTRIES still hits in its own pool."""
    pool: dict = {}
    n = statcache.MAX_ENTRIES + 8
    paths = []
    for i in range(n):
        p = tmp_path / f"s{i}.md"
        p.write_text("x", encoding="utf-8")
        paths.append(p)
    _age(*paths)
    calls = []
    def sweep():
        for p in paths:
            statcache.memo("s", statcache.signature(p),
                           lambda p=p: calls.append(p) or p.name,
                           pool=pool, max_entries=n + 16)
    sweep()
    first = len(calls)
    sweep()
    assert len(calls) == first   # second sweep: all hits
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_statcache.py -x -q -k "inode or absent_ok or pool_budget or working_set"`
Expected: FAIL (`signature() got an unexpected keyword argument 'absent_ok'`
first; the inode test fails on equal tuples).

- [ ] **Step 3: Implement**

In `statcache.py`, replace `signature` and extend `memo` (keep every existing
comment; the diff below shows the whole new bodies):

```python
def signature(*paths: Path, absent_ok: bool = False) -> tuple | None:
    """Stat signature covering every path; None if any is missing.

    `st_ino` rides along because a rename-replace cannot preserve it: a sync
    client or restore landing a same-size file with the origin's old mtime
    would otherwise produce an identical signature, and nothing would ever
    invalidate. The residual is an in-place same-size rewrite with a restored
    mtime — the racy-clean residual git also accepts.

    `absent_ok` turns a missing path into the cacheable sentinel
    `(path, "absent")` instead of voiding the signature. It exists for
    companion files whose absence is a valid state (an appearances record a
    campaign has not written yet): the sentinel differs from every real stat
    tuple, so the file's creation invalidates naturally. Callers whose missing
    file means "not found" keep the default.
    """
    sig = []
    for p in paths:
        try:
            st = p.stat()
        except OSError:
            if absent_ok:
                sig.append((str(p), "absent"))
                continue
            return None
        sig.append((str(p), st.st_mtime_ns, st.st_size, st.st_ino))
    return tuple(sig)
```

In `memo`, add the parameter and use it in the eviction loop (only the
signature line and the `while` change):

```python
def memo(kind: str, sig: tuple | None, compute: Callable[[], T],
         *, pool: dict | None = None, max_entries: int | None = None) -> T:
```

and

```python
    budget = MAX_ENTRIES if max_entries is None else max_entries
    while len(cache) >= budget:
```

The racy-window check iterates `sig` elements of mixed shape now — guard it:

```python
    if any(len(entry) == 4 and now - entry[1] < RACY_WINDOW_NS for entry in sig):
        return compute()  # too fresh to trust the signature; compute, don't cache
```

(the sentinel tuple has length 2 and no mtime to be racy about).

- [ ] **Step 4: Run the whole statcache suite**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_statcache.py -q`
Expected: PASS (existing tests exercise the old call shape and must keep
passing — `signature` stays backward compatible).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/statcache.py backend/tests/test_statcache.py
git commit -m "The stat signature carries the inode and pools carry their own budgets"
```

---

### Task 2: memoize the campaign and world listing rows

**Files:**
- Modify: `backend/src/grimoire/store/campaigns/read.py:110-148` (`list_campaigns`)
- Modify: `backend/src/grimoire/store/worlds/read.py:22-59` (`list_worlds`)
- Test: `backend/tests/test_read_memos.py` (create)

**Interfaces:**
- Consumes: `statcache.signature` / `statcache.memo` from Task 1.
- Produces: byte-identical `list_campaigns()` / `list_worlds()` payloads;
  the only change is that an unchanged `campaign.md` / `world.md` is parsed
  once per process, not once per request.

- [ ] **Step 1: Write the failing test** (create `backend/tests/test_read_memos.py`)

```python
"""Listing row memos: unchanged files are parsed once, edits invalidate."""

import os
import time

import pytest

from grimoire.store import statcache
from grimoire.store.campaigns import read as campaigns_read
from grimoire.store.worlds import read as worlds_read


def _age(*paths):
    old = time.time_ns() - 2 * statcache.RACY_WINDOW_NS
    for p in paths:
        os.utime(p, ns=(old, old))


def _age_tree(root):
    _age(*(p for p in root.rglob("*") if p.is_file()))


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    (tmp_path / "campaigns" / "saltmarch").mkdir(parents=True)
    (tmp_path / "campaigns" / "saltmarch" / "campaign.md").write_text(
        '---\nname: "Saltmarch"\nworld: "realm"\ncreated: "2026-01-01T00:00:00Z"\n'
        'updated: "2026-01-02T00:00:00Z"\n---\nA pitch paragraph.\n',
        encoding="utf-8")
    (tmp_path / "worlds" / "realm").mkdir(parents=True)
    (tmp_path / "worlds" / "realm" / "world.md").write_text(
        '---\nname: "Realm"\ncreated: "2026-01-01T00:00:00Z"\n'
        'updated: "2026-01-01T00:00:00Z"\n---\n', encoding="utf-8")
    _age_tree(tmp_path)
    return tmp_path


def test_campaign_rows_parse_once(store, monkeypatch):
    calls = []
    real = campaigns_read.parse_frontmatter
    monkeypatch.setattr(campaigns_read, "parse_frontmatter",
                        lambda text: calls.append(1) or real(text))
    first = campaigns_read.list_campaigns()
    n = len(calls)
    assert n >= 1
    second = campaigns_read.list_campaigns()
    assert calls[n:] == []            # no re-parse for unchanged files
    assert second == first            # payload identical


def test_campaign_row_invalidates_on_edit(store, monkeypatch):
    campaigns_read.list_campaigns()
    mp = store / "campaigns" / "saltmarch" / "campaign.md"
    mp.write_text(mp.read_text(encoding="utf-8").replace("Saltmarch", "Saltmarch II"),
                  encoding="utf-8")
    _age(mp)
    assert campaigns_read.list_campaigns()[0]["name"] == "Saltmarch II"


def test_world_rows_parse_once(store, monkeypatch):
    calls = []
    real = worlds_read.parse_frontmatter
    monkeypatch.setattr(worlds_read, "parse_frontmatter",
                        lambda text: calls.append(1) or real(text))
    worlds_read.list_worlds()
    n = len(calls)
    worlds_read.list_worlds()
    assert calls[n:] == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_read_memos.py -x -q`
Expected: FAIL — `calls[n:]` is non-empty (every call re-parses today).

- [ ] **Step 3: Implement**

In `campaigns/read.py`, add `from .. import statcache` to the imports and
extract the row body of `list_campaigns` into a memoized helper. The memo
key includes only the file's signature; the compute closes over `mp`:

```python
def _campaign_row(d: Path, mp: Path) -> dict:
    """The listing row derived from one campaign.md, memoized by stat.

    Memoized as a WHOLE ROW minus `id` (which is the directory name, not file
    content). Treated as frozen by every caller — `list_campaigns` copies it
    into a fresh dict per call, so nothing downstream can mutate the cached
    value.
    """
    def compute() -> dict:
        meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
        return {
            "name": meta.get("name", d.name),
            "world": meta.get("world", ""),
            "created": meta.get("created", ""),
            "updated": meta.get("updated", ""),
            "parent": meta.get(PARENT_KEY, ""),
            "forked_from_scene": meta.get(FORKED_AT_KEY, ""),
            "blurb": _first_paragraph(body),
        }
    sig = statcache.signature(mp)
    if sig is None:            # vanished between the exists() check and here
        return compute()
    return statcache.memo("campaign_row", sig, compute)
```

and the loop body becomes (keeping every existing comment above the fields
by moving them onto `_campaign_row`):

```python
            out.append({"id": d.name, **_campaign_row(d, mp)})
```

In `worlds/read.py`, same shape — add `from .. import statcache`, and:

```python
def _world_row(d: Path, mp: Path) -> dict:
    def compute() -> dict:
        meta, _ = parse_frontmatter(mp.read_text(encoding="utf-8"))
        return {"name": meta.get("name", d.name),
                "created": meta.get("created", ""),
                "updated": meta.get("updated", "")}
    sig = statcache.signature(mp)
    if sig is None:
        return compute()
    return statcache.memo("world_row", sig, compute)
```

with the loop appending
`{"id": d.name, **_world_row(d, mp), "counts": {...unchanged count sweep...}}`.
The count sweeps stay live reads (spec: correctness-critical, single
`iterdir`s).

- [ ] **Step 4: Run the new tests plus the campaign/world store suites**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_read_memos.py tests/ -q -k "campaign or world" `
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/campaigns/read.py backend/src/grimoire/store/worlds/read.py backend/tests/test_read_memos.py
git commit -m "Campaign and world listing rows are memoized by stat signature"
```

---

### Task 3: memoize scene frontmatter heads, in their own pool

**Files:**
- Modify: `backend/src/grimoire/store/scenes/read.py:19-53` (`list_scenes`)
- Test: `backend/tests/test_read_memos.py` (append)

**Interfaces:**
- Consumes: Task 1's `pool=`/`max_entries=`.
- Produces: `list_scenes(cid)` payload unchanged; module-level
  `_SCENE_POOL: dict` and `POOL_ENTRIES = 65536` in `scenes/read.py`, shared
  with Task 4's turns memo.

- [ ] **Step 1: Write the failing test** (append to `test_read_memos.py`)

```python
def _add_scene(store, sid, title="One", body="Long transcript body\n" * 50):
    d = store / "campaigns" / "saltmarch" / "scenes"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sid}.md"
    p.write_text(f'---\ntitle: "{title}"\ncreated: "2026-01-01T00:00:00Z"\n'
                 f'updated: "2026-01-03T00:00:00Z"\n---\n{body}', encoding="utf-8")
    _age(p)
    return p


def test_scene_heads_parse_once_and_in_their_own_pool(store, monkeypatch):
    from grimoire.store.scenes import read as scenes_read
    _add_scene(store, "scene-1")
    calls = []
    real = scenes_read.parse_frontmatter_head
    monkeypatch.setattr(scenes_read, "parse_frontmatter_head",
                        lambda p: calls.append(1) or real(p))
    scenes_read.list_scenes("saltmarch")
    n = len(calls)
    scenes_read.list_scenes("saltmarch")
    assert calls[n:] == []
    # the entry landed in the scenes pool, not the shared FIFO
    assert any(k[0] == "scene_head" for k in scenes_read._SCENE_POOL)
    assert not any(k[0] == "scene_head" for k in statcache._cache)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_read_memos.py -x -q -k scene_heads`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `scenes/read.py`, add `from .. import statcache` and:

```python
#: Sweep-sized memo families live here rather than in the shared FIFO of
#: 4096: one library listing would otherwise evict every card summary and
#: sync hash (statcache's own `pool` docstring). Sized above any plausible
#: library's scene count — a head row is a small dict, so even the full
#: budget is a few megabytes.
POOL_ENTRIES = 65536
_SCENE_POOL: dict = {}


def _scene_row(p: Path) -> dict:
    """One scene's listing row, memoized by the file's stat signature.
    Frozen by convention: list_scenes copies it into a fresh dict."""
    def compute() -> dict:
        meta = parse_frontmatter_head(p)  # never reads the transcript body
        hist = histories(meta)
        history = hist["times"]
        return {
            "title": meta.get("title", p.stem),
            "model": meta.get("model", ""),
            "created": meta.get("created", ""),
            "updated": meta.get("updated", ""),
            "date": history[0] if history else "",
            "place": hist["locations"][-1] if hist["locations"] else "",
            "pcless": meta.get("pcless") == "true",
            "done": str(meta.get("done", "")).lower() == "true",
        }
    sig = statcache.signature(p)
    if sig is None:
        return compute()
    return statcache.memo("scene_head", sig, compute,
                          pool=_SCENE_POOL, max_entries=POOL_ENTRIES)
```

`list_scenes`'s loop becomes `out.append({"id": p.stem, **_scene_row(p)})`,
moving the existing field comments onto `_scene_row`.

- [ ] **Step 4: Run the scenes suites**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_read_memos.py -q && PYTHONPATH=src .venv/bin/python -m pytest tests -q -k "scene"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/scenes/read.py backend/tests/test_read_memos.py
git commit -m "Scene listing heads are memoized in a pool sized for whole libraries"
```

---

### Task 4: memoize `_scene_turns`; read the appearances record once

**Files:**
- Modify: `backend/src/grimoire/routes/shell.py:92-162` (`_scene_turns`,
  `_campaign_block`)
- Test: `backend/tests/test_shell_memos.py` (create)

**Interfaces:**
- Consumes: `statcache.signature(..., absent_ok=True)` (Task 1),
  `scenes_read._SCENE_POOL` / `POOL_ENTRIES` (Task 3),
  `store.appearances.cast.player_names(cid, sid)` (existing),
  `store.appearances.paths` `_path(cid)` for the record file's path — expose
  it via a small public helper (below) rather than importing a private name.
- Produces: `_scene_turns(cid, sid, players: tuple[str, ...]) -> int | None`
  (signature gains the player tuple); a new
  `store/appearances/paths.py::record_path(cid) -> Path` public resolver.

- [ ] **Step 1: Add the public record-path resolver and memoize the record read**

In `backend/src/grimoire/store/appearances/paths.py`, next to `record`
(add `from .. import statcache` to the module imports, submodule-style):

```python
def record_path(cid: str) -> Path:
    """Where this campaign's appearances record lives — for readers that key
    caches on the file's stat rather than its contents. Absence is a valid
    state (`record` reads it as the empty record)."""
    return _path(cid)
```

and make `record` itself memoized, so the shell's per-open-scene
`player_names` calls parse the JSON once per change rather than once per
call (the spec's "read the record once per request", solved for every
caller at the source):

```python
def record(cid: str) -> dict:
    p = _path(cid)
    sig = statcache.signature(p, absent_ok=True)
    def compute() -> dict:
        if not p.exists():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))
    if sig is None:  # pragma: no cover — absent_ok never yields None
        return compute()
    # Frozen by convention: no caller mutates the record it is handed —
    # writers go through _write. Verify with a grep for `record(` callers
    # before landing; any mutator found copies first.
    return statcache.memo("appearances_record", sig, compute)
```

- [ ] **Step 2: Write the failing tests** (create `backend/tests/test_shell_memos.py`)

```python
"""The shell's turn counts: memoized on success, retried on failure."""

import os
import time

import pytest

from grimoire.routes import shell
from grimoire.store import statcache
from grimoire.store.scenes import read as scenes_read


def _age(*paths):
    old = time.time_ns() - 2 * statcache.RACY_WINDOW_NS
    for p in paths:
        os.utime(p, ns=(old, old))


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    root = tmp_path / "campaigns" / "saltmarch"
    (root / "scenes").mkdir(parents=True)
    (root / "campaign.md").write_text('---\nname: "Saltmarch"\n---\n', encoding="utf-8")
    (root / "scenes" / "s1.md").write_text(
        '---\ntitle: "One"\nidentity: "abc"\n---\n'
        "Narrator: an opening line\n\nYou: a player line\n\nNarrator: a reply\n",
        encoding="utf-8")
    _age(*(p for p in tmp_path.rglob("*") if p.is_file()))
    return tmp_path


def test_turns_memoized_and_invalidated_by_scene_write(campaign, monkeypatch):
    calls = []
    real = shell.store.scenes.read.read_scene
    monkeypatch.setattr(shell.store.scenes.read, "read_scene",
                        lambda cid, sid: calls.append(1) or real(cid, sid))
    first = shell._scene_turns("saltmarch", "s1", players=())
    assert first is not None
    n = len(calls)
    assert shell._scene_turns("saltmarch", "s1", players=()) == first
    assert calls[n:] == []                       # cached: no re-parse
    p = campaign / "campaigns" / "saltmarch" / "scenes" / "s1.md"
    p.write_text(p.read_text(encoding="utf-8") + "\nNarrator: more\n",
                 encoding="utf-8")
    _age(p)
    assert shell._scene_turns("saltmarch", "s1", players=()) != first


def test_turns_key_includes_player_names(campaign):
    a = shell._scene_turns("saltmarch", "s1", players=())
    b = shell._scene_turns("saltmarch", "s1", players=("Mara",))
    # Different player sets are different cache entries — no cross-talk.
    assert isinstance(a, int) and isinstance(b, int)


def test_turns_failure_is_not_memoized(campaign, monkeypatch):
    boom = {"on": True}
    real = shell.store.scenes.read.read_scene
    def flaky(cid, sid):
        if boom["on"]:
            raise OSError("transient")
        return real(cid, sid)
    monkeypatch.setattr(shell.store.scenes.read, "read_scene", flaky)
    assert shell._scene_turns("saltmarch", "s1", players=()) is None
    boom["on"] = False
    assert shell._scene_turns("saltmarch", "s1", players=()) is not None
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_shell_memos.py -x -q`
Expected: FAIL (`_scene_turns() got an unexpected keyword argument 'players'`).

- [ ] **Step 4: Implement**

In `routes/shell.py`, rework `_scene_turns` (keep its docstring, extend it
with the memo argument) and its call site:

```python
def _scene_turns(cid: str, sid: str, players: tuple[str, ...]) -> int | None:
    scene_path = store.scenes.paths._scene_path(cid, sid)   # paths-ok: resolver
    record = store.appearances.paths.record_path(cid)
    # absent_ok: a campaign with no appearances record is the ordinary state
    # of a fresh or PC-less campaign, not a missing input (spec, round four).
    sig = store.statcache.signature(scene_path, record, absent_ok=True)

    def compute() -> int:
        messages = store.scenes.read.read_scene(cid, sid)["messages"]
        return len(store.scenes._model_blocks(messages))

    if sig is None:
        try:
            return compute()
        except (OSError, UnicodeDecodeError, store.CampaignNotFound,
                store.SceneNotFound):
            return None
    # The fail-soft None sits OUTSIDE the memo: a transient read failure under
    # a valid signature must be retried, not pinned (spec, round four).
    try:
        return store.statcache.memo(
            ("scene_turns", players), sig, compute,
            pool=store.scenes.read._SCENE_POOL,
            max_entries=store.scenes.read.POOL_ENTRIES)
    except (OSError, UnicodeDecodeError, store.CampaignNotFound,
            store.SceneNotFound):
        return None
```

(`memo`'s `kind` is only used as a dict-key component, so a tuple carrying
the player names is a valid kind and puts the names in the key.)

In `_campaign_block`, read the players **once** for the campaign and pass
them down — replacing the per-scene `player_names` reads `read_scene` was
doing implicitly:

```python
    open_scenes = []
    for s in scenes:
        if s["done"]:
            continue
        players = tuple(store.appearances.cast.player_names(cid, s["sid" if "sid" in s else "id"]))
        open_scenes.append({"sid": s["id"], "title": s["title"],
                            "turns": _scene_turns(cid, s["id"], players)})
```

(use `s["id"]` — the listing rows carry `id`; the dict shape written to the
payload keeps the `sid` key exactly as today.)

Check what `shell.py` imports: it reaches stores via `store.` — confirm
`store.statcache` and `store.appearances` resolve from the existing
`from .. import store`-style import at the top of the module; if the module
imports names individually, add the submodule imports in the same style.

- [ ] **Step 5: Run the shell suites**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_shell_memos.py -q && PYTHONPATH=src .venv/bin/python -m pytest tests -q -k "shell"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/routes/shell.py backend/src/grimoire/store/appearances/paths.py backend/tests/test_shell_memos.py
git commit -m "Turn counts are memoized by scene and cast signature, successes only"
```

---

### Task 5: memoize the roster listings' per-file reads

**Files:**
- Modify: `backend/src/grimoire/store/characters.py:302-347`
  (`list_characters` — the `character.md` meta parse)
- Modify: `backend/src/grimoire/store/taglines.py:28` (`read`),
  `backend/src/grimoire/store/voice_anchors.py:196` (`read`),
  `backend/src/grimoire/store/assets.py:491` (`read_focus`)
- Modify: `backend/src/grimoire/store/entities.py:100-121` (`list_entities`
  row parse), `backend/src/grimoire/store/pcs.py:103` (`_read_meta`),
  `backend/src/grimoire/store/greetings.py:158-164` (`list_greetings` via
  `_read_record`)
- Test: `backend/tests/test_read_memos.py` (append)

**Interfaces:**
- Consumes: Task 1's `signature`/`memo`.
- Produces: all listing payloads byte-identical; each per-file derivation
  behind a `statcache.memo` in the **shared** cache (these families are
  bounded by roster size, not library scene count — the shared FIFO holds
  them; only the scene sweeps needed their own pool).

- [ ] **Step 1: Write the failing tests** (append to `test_read_memos.py`;
  build a world fixture with one character the way
  `backend/tests/test_characters_store.py` does — copy its minimal-character
  fixture helper rather than inventing a new shape, and `_age_tree` it)

```python
def test_character_meta_parsed_once(world_with_character, monkeypatch):
    """world_with_character: fixture per test_characters_store.py's pattern —
    a world root holding one character with one version, mtimes aged."""
    from grimoire.store import characters
    root = world_with_character
    calls = []
    real = characters.parse_frontmatter
    monkeypatch.setattr(characters, "parse_frontmatter",
                        lambda text: calls.append(1) or real(text))
    characters.list_characters(root)
    n = len(calls)
    characters.list_characters(root)
    assert calls[n:] == []


def test_entity_rows_parsed_once(store, monkeypatch):
    from grimoire.store import entities
    root = store / "worlds" / "realm"
    (root / "locations").mkdir()
    (root / "locations" / "harbor.md").write_text(
        '---\nname: "Harbor"\n---\nA place.\n', encoding="utf-8")
    _age_tree(root)
    calls = []
    real = entities.parse_frontmatter
    monkeypatch.setattr(entities, "parse_frontmatter",
                        lambda text: calls.append(1) or real(text))
    entities.list_entities(root, "locations")
    n = len(calls)
    entities.list_entities(root, "locations")
    assert calls[n:] == []
```

Add matching one-liners for `pcs.list_pcs` and `greetings.list_greetings`
in the same shape (count the underlying read via monkeypatching
`pcs._read_meta`'s parse and `greetings._read_record`).

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_read_memos.py -x -q -k "meta_parsed or entity_rows or pc_ or greeting_"`
Expected: FAIL.

- [ ] **Step 3: Implement, one file at a time, same pattern each**

The pattern (shown for `entities.list_entities`; apply the same wrap to the
others):

```python
            def _row(p: Path = p) -> dict:
                meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
                return {"id": p.stem, "name": meta.get("name", p.stem), **meta,
                        "tokens": tokens.measure(body)}   # keep the exact
                        # existing field construction — copy it verbatim from
                        # the loop body, comments included
            sig = statcache.signature(p)
            out.append(_row() if sig is None
                       else statcache.memo("entity_row", sig, _row))
```

- `characters.list_characters`: wrap only the `parse_frontmatter(...)` of
  `character.md` in a `memo("char_meta", signature(meta_path), ...)` that
  returns the parsed `meta` dict; the version scans, `_card_summary`
  (already memoized) and image scans stay as they are.
- `taglines.read`, `voice_anchors.read`, `assets.read_focus`: each becomes
  `memo("<kind>", signature(sidecar_path), <existing body>)` with a
  `signature is None` fall-through to the existing not-found behavior —
  these files' absence means "empty"/"none", so use `absent_ok=False` and
  keep the current early-return when the signature is `None` **only if** the
  current body also answers the same value for a missing file; otherwise
  compute uncached. Read each function first; preserve its exact
  missing-file answer.
- `pcs._read_meta` and `greetings._read_record`: memoize the parse keyed on
  the file's signature; `_read_record` returns `(record_dict, text)` — memo
  the pair, and treat both as frozen.

- [ ] **Step 4: Run the store suites**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_read_memos.py tests/test_characters_store.py tests/test_entities_store.py tests/test_pcs_store.py tests/test_greetings_store.py -q`
(adjust to the actual test filenames — `ls backend/tests | grep -E "character|entit|pcs|greeting"`)
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/characters.py backend/src/grimoire/store/entities.py backend/src/grimoire/store/pcs.py backend/src/grimoire/store/greetings.py backend/src/grimoire/store/taglines.py backend/src/grimoire/store/voice_anchors.py backend/src/grimoire/store/assets.py backend/tests/test_read_memos.py
git commit -m "Every count-backing listing memoizes its per-file reads"
```

---

### Task 6: intra-request dedup — shell block and campaign GET

**Files:**
- Modify: `backend/src/grimoire/routes/shell.py:134-162` (`_campaign_block`),
  `:259-295` (`get_shell`)
- Modify: `backend/src/grimoire/routes/campaigns.py:556-570` (`get_campaign`)
- Modify: `backend/src/grimoire/store/campaigns/read.py:40-65` (`slim_pending`)
- Test: `backend/tests/test_shell_memos.py` (append)

**Interfaces:**
- Produces: `_campaign_block(cid, row: dict | None = None)` — when `get_shell`
  already holds the campaign's listing row it passes it, and the block skips
  its own `read_campaign`; `slim_pending` memoized by `campaign.md`'s stat.

- [ ] **Step 1: Write the failing tests** (append to `test_shell_memos.py`)

```python
def test_shell_parses_each_campaign_md_once_per_request(campaign, monkeypatch):
    from fastapi.testclient import TestClient
    from grimoire.main import create_app
    from grimoire.store.campaigns import read as campaigns_read
    calls = []
    real = campaigns_read.parse_frontmatter
    monkeypatch.setattr(campaigns_read, "parse_frontmatter",
                        lambda text: calls.append(1) or real(text))
    client = TestClient(create_app())
    # bust any memo from earlier tests: track only this request's parses
    calls.clear()
    client.get("/api/shell", params={"campaign": "saltmarch"})
    # One campaign on disk: at most one campaign.md parse for the whole
    # request (zero when the row memo is warm) — never two.
    assert len(calls) <= 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_shell_memos.py -x -q -k once_per_request`
Expected: FAIL — the row memo from Task 2 makes `list_campaigns` cheap, but
`_campaign_block`'s own `read_campaign` still parses (1 memoized-miss + 1
block parse = 2 on a cold cache… if the memo is warm the test may pass
accidentally: `calls.clear()` after one warm-up GET, then assert `== 0` —
write it that way instead: warm-up request, `calls.clear()`, second request,
`assert calls == []`).

- [ ] **Step 3: Implement**

`_campaign_block(cid, row=None)`: when `row` is not `None`, use
`row["name"]`/`row["world"]` where `meta` was used and skip `read_campaign`;
when `None` (direct callers), keep the current body. `get_shell` finds the
row: `row = next((c for c in campaigns if c["id"] == cid), None)` and passes
it; the not-found `None` case still answers `None` as today (the block's
`read_campaign` `CampaignNotFound` path) — so when `row is None` **and** the
id was asked for, fall through to the existing lookup.

`get_campaign` (`routes/campaigns.py:556`): `ensure_campaign_slim` stays
first (it is a durable migration reached from a GET). Make its
already-migrated fast path cheap instead of parsing: in
`campaigns/read.py`, memoize `slim_pending`:

```python
def slim_pending(cid: str) -> bool:
    mp = paths.campaign_meta_path(cid)
    if not mp.exists():
        return False
    sig = statcache.signature(mp)
    def compute() -> bool:
        meta, _ = parse_frontmatter(mp.read_text(encoding="utf-8"))
        return meta.get("world_copy") != "overlay"
    if sig is None:
        return compute()
    return statcache.memo("slim_pending", sig, compute)
```

then in `campaigns/lifecycle.ensure_campaign_slim` (`lifecycle.py:159-164`),
guard the entry: read the module's current first lines; if it parses
`campaign.md` before deciding, put `if not read.slim_pending(cid): return`
in front (it raises `CampaignNotFound` for a missing file today — preserve
that: `slim_pending` returns `False` for a missing file, so keep the
existing `mp.exists()` raise before the guard).

- [ ] **Step 4: Run**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_shell_memos.py -q && PYTHONPATH=src .venv/bin/python -m pytest tests -q -k "shell or slim or campaign_read or lifecycle"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes/shell.py backend/src/grimoire/routes/campaigns.py backend/src/grimoire/store/campaigns/read.py backend/src/grimoire/store/campaigns/lifecycle.py backend/tests/test_shell_memos.py
git commit -m "One parse per campaign.md per request on the shell and campaign reads"
```

---

### Task 7: `store/epoch.py` and its bump wiring

**Files:**
- Create: `backend/src/grimoire/store/epoch.py`
- Modify: `backend/src/grimoire/main.py:352-375` (`create_app`),
  `backend/src/grimoire/store/revision.py:227` (`bump`),
  `backend/src/grimoire/store/usage.py:212-326` (`record`)
- Test: `backend/tests/test_epoch.py` (create)

**Interfaces:**
- Produces:

```python
# store/epoch.py
EPOCH_TTL_SECONDS = 30.0

class Epoch:
    def bump(self) -> None
    def token(self) -> str        # "<uuid32>.<monotonic bucket>"

def register(e: Epoch) -> Epoch   # adds to the live WeakSet, returns e
def bump_all() -> None            # bumps every live instance
```

- `create_app` sets `app.state.epoch = epoch.register(epoch.Epoch())`.
- `revision.bump` and `usage.record` call `epoch.bump_all()`.

- [ ] **Step 1: Write the failing tests** (create `backend/tests/test_epoch.py`)

```python
"""The in-memory read epoch: per-app minting, TTL bucket, detached bumps."""

from grimoire.store import epoch


def test_token_stable_until_bump(monkeypatch):
    monkeypatch.setattr(epoch.time, "monotonic", lambda: 1000.0)
    e = epoch.Epoch()
    assert e.token() == e.token()
    before = e.token()
    e.bump()
    assert e.token() != before


def test_token_rolls_with_the_bucket(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(epoch.time, "monotonic", lambda: clock["t"])
    e = epoch.Epoch()
    before = e.token()
    clock["t"] += epoch.EPOCH_TTL_SECONDS + 0.1
    assert e.token() != before


def test_bump_all_reaches_registered_instances():
    a, b = epoch.register(epoch.Epoch()), epoch.register(epoch.Epoch())
    ta, tb = a.token(), b.token()
    epoch.bump_all()
    assert a.token() != ta and b.token() != tb


def test_two_apps_mint_distinct_tokens():
    assert epoch.Epoch().token() != epoch.Epoch().token()


def test_revision_bump_bumps_the_epoch(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    (tmp_path / "campaigns" / "saltmarch").mkdir(parents=True)
    from grimoire.store import revision
    e = epoch.register(epoch.Epoch())
    before = e.token()
    revision.bump("saltmarch")
    assert e.token() != before


def test_usage_record_bumps_the_epoch(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store import usage
    e = epoch.register(epoch.Epoch())
    before = e.token()
    usage.record(task="chat", campaign="saltmarch", model="m", duration_ms=1)
    assert e.token() != before
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_epoch.py -x -q`
Expected: FAIL (`No module named 'grimoire.store.epoch'`).

- [ ] **Step 3: Implement `store/epoch.py`**

```python
"""The library-wide read epoch: one opaque token per app instance that
changes whenever this process writes the store, spent as an ETag by the hot
GET routes (spec: 2026-08-28-read-path-performance-design.md, layer 3).

In-memory on purpose — never persisted. Wrong only in the safe direction for
this process's writes (the bump is redundant: the revision funnel, the usage
append, and the mutating-request wrapper all bump), and boundedly wrong for
writes it cannot see: the token carries a time bucket, so a 304 is never
believed past EPOCH_TTL_SECONDS, and the recompute it forces reads through
statcache signatures, which external writes DO move.

Module-level REGISTRY, per-app INSTANCES. Store-side writers (revision.bump,
usage.record — including every detached-run path) hold no app and bump every
live instance; over-invalidation is the allowed direction. Instances are
minted in create_app, so "restart" means "the app was rebuilt" — the Android
entry point rebuilds the app inside a surviving interpreter, and a
module-scope uuid would let a WebView's held ETag survive into the rebuilt
app. The WeakSet drops instances with their apps, so TestClient churn cannot
grow it.

The bucket reads time.monotonic(): civil time may step backwards; a held
token must not outlive its TTL because NTP said so.
"""

from __future__ import annotations

import time
import uuid
import weakref

#: How long a 304 may be believed without a recompute. A structural guess —
#: long enough that idle navigation is almost always 304s, short enough that
#: another device's synced edits don't outlive a coffee sip. Tune against
#: real use, not against this comment.
EPOCH_TTL_SECONDS = 30.0


class Epoch:
    __slots__ = ("_uuid", "__weakref__")

    def __init__(self) -> None:
        self._uuid = uuid.uuid4().hex

    def bump(self) -> None:
        # Plain assignment: atomic under the GIL, nothing to lock.
        self._uuid = uuid.uuid4().hex

    def token(self) -> str:
        bucket = int(time.monotonic() / EPOCH_TTL_SECONDS)
        return f"{self._uuid}.{bucket}"


_live: weakref.WeakSet[Epoch] = weakref.WeakSet()


def register(e: Epoch) -> Epoch:
    _live.add(e)
    return e


def bump_all() -> None:
    """Invalidate every live app's cached reads. Called by store-side writers
    that hold no app handle; never raises (iteration over a WeakSet copy)."""
    for e in list(_live):
        e.bump()
```

Wiring:
- `main.py` `create_app`, next to `runs.install_registry(app)`:
  `app.state.epoch = epoch.register(epoch.Epoch())` (import
  `from .store import epoch` at module scope alongside the other store
  imports; match the file's existing import style).
- `revision.py` `bump`: add `from . import epoch` to the module imports and
  call `epoch.bump_all()` as the **first line** of `bump`'s body —
  unconditional: reaching `bump` at all means a mutation committed, and every
  early-return path below (deleted campaign, failed stamp) still accompanied
  one.
- `usage.py` `record`: add `from . import epoch`; inside the existing
  never-raises guard, after the `atomic.append_line(...)` at `:326`, add
  `epoch.bump_all()`.

- [ ] **Step 4: Run**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_epoch.py tests/test_import_guard.py tests/test_lock_domain_guard.py -q`
Expected: PASS. If `test_lock_domain_guard.py` names `epoch`, classify it in
`store/locks.py` `OUTSIDE_DOMAIN` with the reason
`"in-memory read-epoch; writes no campaign state"`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/epoch.py backend/src/grimoire/main.py backend/src/grimoire/store/revision.py backend/src/grimoire/store/usage.py backend/src/grimoire/store/locks.py backend/tests/test_epoch.py
git commit -m "A per-app read epoch, bumped by the revision funnel and the ledger"
```

---

### Task 8: the mutating-request epoch wrapper

**Files:**
- Modify: `backend/src/grimoire/main.py` (new class beside
  `_CampaignActivityStamp`; `add_middleware` after `:397`)
- Test: `backend/tests/test_epoch_middleware.py` (create)

**Interfaces:**
- Produces: `_ReadEpochBump` raw-ASGI middleware — any mutating method under
  `/api/` bumps `scope["app"].state.epoch`, status-blind, skipping
  `@computes_only` endpoints; bumps at `http.response.start`, again at the
  terminal body frame, and again in a `finally` (disconnect/cancel/raise).

- [ ] **Step 1: Write the failing tests**

```python
"""The epoch wrapper: what bumps, what doesn't, and when."""

from fastapi.testclient import TestClient

from grimoire.main import create_app


def _token(app):
    return app.state.epoch.token()


def test_mutating_2xx_bumps(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    app = create_app()
    client = TestClient(app)
    before = _token(app)
    client.post("/api/worlds", json={"name": "Realm"})
    assert _token(app) != before


def test_get_never_bumps(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    app = create_app()
    client = TestClient(app)
    client.post("/api/worlds", json={"name": "Realm"})
    before = _token(app)
    client.get("/api/worlds")
    assert _token(app) == before


def test_mutating_4xx_bumps(tmp_path, monkeypatch):
    """Status-blind: a refused write may have written part-way (spec)."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    app = create_app()
    client = TestClient(app)
    before = _token(app)
    client.put("/api/worlds/not-there", json={"name": "X"})   # 404
    assert _token(app) != before


def test_computes_only_does_not_bump(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    app = create_app()
    client = TestClient(app)
    # find one @computes_only POST: routes/greetings.py's opener is the
    # documented one (revision.py docstring); grep `grimoire_computes_only`
    # for the decorator and pick a route reachable with a minimal fixture.
    # Skip-with-reason is acceptable if none is reachable without an LLM.
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_epoch_middleware.py -x -q`
Expected: FAIL (`_token` unchanged — no middleware yet).

- [ ] **Step 3: Implement** (in `main.py`, beside `_CampaignActivityStamp`,
matching its raw-ASGI shape and comment style)

```python
class _ReadEpochBump:
    """Every mutating request under /api/ invalidates the read epoch.

    A NEW wrapper rather than a widened _CampaignActivityStamp: that
    middleware early-returns for everything outside /api/campaigns/,
    exception path included, so its shape cannot carry this. Status-blind on
    purpose — a partial write answered 4xx is real, and HTTPException never
    reaches the except below (Starlette's ExceptionMiddleware converts it
    inside this wrapper). @computes_only routes are skipped; the ones that DO
    write still bump via the revision funnel or the ledger append.

    Three bump points, all reaching the same instance: the response line
    (before forwarding, so a client navigating on the response revalidates
    against the post-write token), the terminal body frame (a streaming
    mutator persists while its generator runs), and a finally (a disconnect
    is precisely when those generators' cleanup saves what landed).
    """

    _MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope.get("method") not in self._MUTATING
                or not scope.get("path", "").startswith("/api/")):
            return await self.app(scope, receive, send)

        def _bump():
            endpoint = getattr(scope.get("route"), "endpoint", None)
            if getattr(endpoint, "grimoire_computes_only", False):
                return
            scope["app"].state.epoch.bump()

        async def _send(message):
            if message["type"] == "http.response.start":
                _bump()
            elif (message["type"] == "http.response.body"
                    and not message.get("more_body", False)):
                _bump()
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            _bump()
```

Register it **after** `_CampaignActivityStamp` at `main.py:397`:

```python
    app.add_middleware(_CampaignActivityStamp)
    app.add_middleware(_ReadEpochBump)
```

- [ ] **Step 4: Run**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_epoch_middleware.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/main.py backend/tests/test_epoch_middleware.py
git commit -m "Mutating requests bump the read epoch at every terminal"
```

---

### Task 9: `read_cache` and the conditional-GET opt-ins

**Files:**
- Modify: `backend/src/grimoire/routes/common.py` (new helper)
- Modify: route handlers (below)
- Test: `backend/tests/test_conditional_reads.py` (create)

**Interfaces:**
- Produces, in `routes/common.py`:

```python
def read_cache(request: Request, response: Response) -> Response | None:
    """304 when the client's If-None-Match is this app's current epoch token;
    otherwise stamp ETag + Cache-Control: no-cache and return None. Weak
    ETags (GZip re-encodes); no-cache means browsers always revalidate but
    serve their cached body themselves on 304 — the frontend needs no code."""
    etag = f'W/"{request.app.state.epoch.token()}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304,
                        headers={"ETag": etag, "Cache-Control": "no-cache"})
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "no-cache"
    return None
```

- Opt-in set (the hot read surface; each handler gains
  `request: Request, response: Response` params and the three-line prelude):
  - `routes/campaigns.py` `get_campaigns` (:150), `get_campaign` (:556 —
    **after** `ensure_campaign_slim`, the spec's one stated exception),
    `get_changes` (:1085)
  - `routes/worlds.py` `get_worlds` (:66), `get_world` (:80)
  - `routes/shell.py` `get_shell` (:259)
  - `routes/scenes.py` `get_scenes` (:79), the chronicle GET (:1371)
  - the record lists: world- and campaign-scoped characters, PCs, entities,
    greetings, tags — locate every `@router.get` whose handler calls
    `list_characters(`, `list_pcs(`, `list_entities(`, `list_greetings(`,
    or the tags read (`grep -rn "list_characters(\|list_pcs(\|list_entities(\|list_greetings(" backend/src/grimoire/routes/`)
    and apply the same prelude to each **list** handler (not the per-record
    detail handlers).
  - Run-poll routes, config, and everything else: untouched — never ETagged.

  Prelude pattern, identical everywhere:

```python
def get_campaigns(request: Request, response: Response):
    if (hit := read_cache(request, response)) is not None:
        return hit
    ...existing body...
```

  For `get_campaign` only, the order is:

```python
def get_campaign(cid: str, request: Request, response: Response):
    try:
        store.campaigns.ensure_campaign_slim(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    if (hit := read_cache(request, response)) is not None:
        return hit
    try:
        out = store.campaigns.read_campaign(cid)
    ...
```

- [ ] **Step 1: Write the failing tests** (create `backend/tests/test_conditional_reads.py`)

```python
"""ETag/304 on the hot reads: what flips them, what never carries them."""

import pytest
from fastapi.testclient import TestClient

from grimoire.main import create_app
from grimoire.store import epoch


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return TestClient(create_app())


def _etag(res):
    assert res.headers.get("Cache-Control") == "no-cache"
    tag = res.headers.get("ETag")
    assert tag and tag.startswith('W/"')
    return tag


def test_repeat_get_is_304(client):
    tag = _etag(client.get("/api/campaigns"))
    res = client.get("/api/campaigns", headers={"If-None-Match": tag})
    assert res.status_code == 304


def test_any_write_flips_it(client):
    tag = _etag(client.get("/api/campaigns"))
    client.post("/api/worlds", json={"name": "Realm"})   # world-scoped write
    res = client.get("/api/campaigns", headers={"If-None-Match": tag})
    assert res.status_code == 200
    assert _etag(res) != tag


def test_usage_append_flips_it(client, tmp_path):
    from grimoire.store import usage
    tag = _etag(client.get("/api/shell"))
    usage.record(task="chat", campaign="saltmarch", model="m", duration_ms=1)
    assert client.get("/api/shell", headers={"If-None-Match": tag}).status_code == 200


def test_bucket_rollover_flips_it_with_no_write(client, monkeypatch):
    tag = _etag(client.get("/api/worlds"))
    real = epoch.time.monotonic
    monkeypatch.setattr(epoch.time, "monotonic",
                        lambda: real() + epoch.EPOCH_TTL_SECONDS + 1)
    assert client.get("/api/worlds", headers={"If-None-Match": tag}).status_code == 200


def test_304_reads_nothing(client, monkeypatch):
    from grimoire.store.campaigns import read as campaigns_read
    tag = _etag(client.get("/api/campaigns"))
    def boom(*a, **k):
        raise AssertionError("a 304 must not touch the store")
    monkeypatch.setattr(campaigns_read, "list_campaigns", boom)
    assert client.get("/api/campaigns",
                      headers={"If-None-Match": tag}).status_code == 304


def test_run_polls_never_carry_etags(client):
    # any runs route: the global subject's list route exists without fixtures
    res = client.get("/api/runs")
    if res.status_code == 200:
        assert "ETag" not in res.headers
```

(Adjust the runs path to the real one — `grep -rn '@router.get' backend/src/grimoire/routes/runs.py | head`.)

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_conditional_reads.py -x -q`
Expected: FAIL (no ETag header).

- [ ] **Step 3: Implement** the helper and the preludes as specified above.
Two PR-thread dispositions to record as comments where they land:
- On the chronicle and changes handlers (open Codex thread: "memoize the
  aggregate reads"): add above the prelude —
  `# On the ETag list without a layer-1 memo on purpose: the chronicle is
  # one JSON file parsed once per 200, not a per-record sweep, and the
  # changes sweep's hashes are already statcache-memoized. If either shows
  # up in a profile, the memo goes in the store module, not here.`

- [ ] **Step 4: Run the conditional tests plus the route suites**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_conditional_reads.py -q && make check-py PY=.venv/bin/python`
Expected: PASS — the full backend suite proves no handler broke its
signature (FastAPI rejects bad param wiring at import).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes/common.py backend/src/grimoire/routes/campaigns.py backend/src/grimoire/routes/worlds.py backend/src/grimoire/routes/shell.py backend/src/grimoire/routes/scenes.py backend/src/grimoire/routes/characters.py backend/src/grimoire/routes/entities.py backend/src/grimoire/routes/greetings.py backend/tests/test_conditional_reads.py
git commit -m "The hot reads answer 304 against the epoch before doing any work"
```

---

### Task 10: the frontend read cache

**Files:**
- Create: `frontend/src/api/readCache.ts`
- Create: `frontend/src/api/useCachedGet.ts`
- Modify: `frontend/src/api/client.ts:184-201` (`request` — a `fresh` GET
  advances the generation), `:884-891` (`putDataDir` — clear + broadcast)
- Test: `frontend/src/api/readCache.test.ts` (create)

**Interfaces:**
- Produces (`readCache.ts`):

```ts
export const MAX_ENTRIES = 64;
export function cached<T>(path: string): T | undefined;
export function generation(path: string): number;
export function bumpGeneration(path: string): void;
export function settle(path: string, gen: number, payload: unknown): void;
export function evict(path: string): void;
export function invalidatePrefix(prefix: string): void;
export function clearAll(): void;
```

- Produces (`useCachedGet.ts`):

```ts
export function useCachedGet<T>(
  path: string | null,                  // null = nothing to fetch
  fetcher: () => Promise<T>,
): { data: T | null; stale: boolean; error: unknown | null };
```

- Consumes: `appEvents` (`onCampaignsChanged`, `onShellChanged`,
  `onConfigChanged`), `ApiError` from `./errors`.

- [ ] **Step 1: Write the failing tests** (create `frontend/src/api/readCache.test.ts`)

```ts
import { afterEach, describe, expect, it } from "vitest";
import * as rc from "./readCache";
import { campaignsChanged } from "../appEvents";

afterEach(() => rc.clearAll());

describe("readCache", () => {
  it("stores and serves a settled payload", () => {
    const gen = rc.generation("/api/campaigns");
    rc.settle("/api/campaigns", gen, [{ id: "saltmarch" }]);
    expect(rc.cached("/api/campaigns")).toEqual([{ id: "saltmarch" }]);
  });

  it("discards a settle stamped with an older generation", () => {
    const gen = rc.generation("/api/campaigns");
    rc.bumpGeneration("/api/campaigns");
    rc.settle("/api/campaigns", gen, ["stale"]);
    expect(rc.cached("/api/campaigns")).toBeUndefined();
  });

  it("campaignsChanged invalidates campaign and shell paths", () => {
    rc.settle("/api/campaigns", rc.generation("/api/campaigns"), ["x"]);
    rc.settle("/api/shell?campaign=c", rc.generation("/api/shell?campaign=c"), ["y"]);
    campaignsChanged();
    expect(rc.cached("/api/campaigns")).toBeUndefined();
    expect(rc.cached("/api/shell?campaign=c")).toBeUndefined();
  });

  it("evicts least-recently-used beyond the budget", () => {
    for (let i = 0; i < rc.MAX_ENTRIES + 5; i++) {
      const p = `/api/x/${i}`;
      rc.settle(p, rc.generation(p), i);
    }
    expect(rc.cached("/api/x/0")).toBeUndefined();
    expect(rc.cached(`/api/x/${rc.MAX_ENTRIES + 4}`)).toBe(rc.MAX_ENTRIES + 4);
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd frontend && npx vitest run src/api/readCache.test.ts`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `readCache.ts`**

```ts
/** A bounded stale-while-revalidate payload cache for the list pages
 *  (spec: read-path performance, layer 4). Payloads are frozen by
 *  convention; a consumer never mutates what it is handed.
 *
 *  Generations, not timestamps: an invalidation bumps the path's generation,
 *  and a response that settles stamped with an older one is discarded — the
 *  same ordering client.ts's retireInflight solves for its in-flight map.
 *  The generation map survives eviction (it is the fence, not the data). */
import { onCampaignsChanged, onConfigChanged, onShellChanged } from "../appEvents";

export const MAX_ENTRIES = 64;

const payloads = new Map<string, unknown>();   // insertion order = LRU order
const gens = new Map<string, number>();

export function generation(path: string): number { return gens.get(path) ?? 0; }

export function bumpGeneration(path: string): void {
  gens.set(path, generation(path) + 1);
  payloads.delete(path);
}

export function cached<T>(path: string): T | undefined {
  if (!payloads.has(path)) return undefined;
  const v = payloads.get(path) as T;
  payloads.delete(path); payloads.set(path, v);   // refresh LRU position
  return v;
}

export function settle(path: string, gen: number, payload: unknown): void {
  if (gen !== generation(path)) return;           // an invalidation won
  payloads.delete(path);
  payloads.set(path, payload);
  while (payloads.size > MAX_ENTRIES) {
    const oldest = payloads.keys().next().value as string;
    payloads.delete(oldest);
  }
}

export function evict(path: string): void { bumpGeneration(path); }

export function invalidatePrefix(prefix: string): void {
  for (const key of [...payloads.keys()]) {
    if (key.startsWith(prefix)) bumpGeneration(key);
  }
  for (const key of [...gens.keys()]) {
    if (key.startsWith(prefix)) bumpGeneration(key);
  }
}

export function clearAll(): void {
  for (const key of [...new Set([...payloads.keys(), ...gens.keys()])]) {
    bumpGeneration(key);
  }
}

onCampaignsChanged(() => { invalidatePrefix("/api/campaigns"); invalidatePrefix("/api/shell"); });
onShellChanged(() => invalidatePrefix("/api/shell"));
// configChanged covers both connection changes and a store-root move; the
// root move is the one that must never paint another library's numbers, and
// clearing on every config change over-invalidates in the allowed direction.
onConfigChanged(() => clearAll());

// A root move in ANOTHER tab: appEvents is in-memory per tab, so broadcast.
// try/catch: BroadcastChannel is absent in some webviews and in vitest's
// default environment.
try {
  const bc = new BroadcastChannel("grimoire-store-root");
  bc.onmessage = () => clearAll();
  export_broadcast = () => bc.postMessage("moved");   // see note below
} catch { /* no cross-tab signal available; same-tab clearing still holds */ }
```

(Implementation note: `export_broadcast` is pseudocode — export a real
`export function broadcastRootMove(): void` that posts on the channel when
it exists and no-ops otherwise; declare the channel with
`let bc: BroadcastChannel | null = null` above the try.)

Implement `useCachedGet.ts`:

```ts
import { useEffect, useRef, useState } from "react";
import { ApiError } from "./errors";
import * as rc from "./readCache";

/** Render what we had, then revalidate. `data` is the cached payload
 *  immediately (stale=true) and the fresh one when the fetch settles; a 404
 *  or 410 evicts the entry and surfaces as `error` instead of a stale frame
 *  (a deleted record must not be paintable from cache). */
export function useCachedGet<T>(path: string | null, fetcher: () => Promise<T>):
    { data: T | null; stale: boolean; error: unknown | null } {
  const [state, setState] = useState<{ data: T | null; stale: boolean; error: unknown | null }>(
    () => ({ data: path ? (rc.cached<T>(path) ?? null) : null,
             stale: true, error: null }));
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    if (!path) return;
    let live = true;
    const seed = rc.cached<T>(path);
    setState({ data: seed ?? null, stale: true, error: null });
    const gen = rc.generation(path);
    fetcherRef.current().then((fresh) => {
      rc.settle(path, gen, fresh);
      if (live) setState({ data: fresh, stale: false, error: null });
    }).catch((err) => {
      if (err instanceof ApiError && (err.status === 404 || err.status === 410)) {
        rc.evict(path);
        if (live) setState({ data: null, stale: false, error: err });
        return;
      }
      // transient failure: keep the stale frame, surface the error
      if (live) setState((s) => ({ ...s, error: err }));
    });
    return () => { live = false; };
  }, [path]);

  return state;
}
```

Client changes (`client.ts`):
- In `request`, the fresh-GET branch also fences the payload cache:

```ts
  if (method !== "GET" || opts?.fresh) {
    if (opts?.fresh) { retireInflight(path); readCache.bumpGeneration(path); }
```

  (import `* as readCache from "./readCache"` — `readCache` imports only
  `appEvents` and `errors`, so no cycle.)
- In `putDataDir`'s `.then` (`:884-891`), beside `retireAllInflight()`:
  `readCache.clearAll(); readCache.broadcastRootMove();`

- [ ] **Step 4: Run**

Run: `cd frontend && npx vitest run src/api/readCache.test.ts && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/readCache.ts frontend/src/api/useCachedGet.ts frontend/src/api/client.ts frontend/src/api/readCache.test.ts
git commit -m "A bounded generation-fenced payload cache behind the list pages"
```

---

### Task 11: adopt the cache in the list pages; every post-mutation refresh is fresh

**Files:**
- Modify: `frontend/src/routes/CampaignsView.tsx:91-198`
- Modify: `frontend/src/routes/WorldsView.tsx:28-143`
- Test: `frontend/src/routes/CampaignsView.test.tsx`,
  `frontend/src/routes/WorldsView.test.tsx` (append cases)

**Interfaces:**
- Consumes: `useCachedGet` (Task 10); `api.listCampaigns` / `api.listWorlds`
  gain an optional `fresh` arg where missing (`listWorlds` has one;
  `listCampaigns` gets `listCampaigns: (fresh = false) => request<...>("GET", "/api/campaigns", undefined, { fresh })`).

- [ ] **Step 1: Write the failing tests** (append; follow each suite's
  existing mock pattern — both mock `../api/client` wholesale)

```tsx
it("renders the cached campaign list before the refetch settles", async () => {
  // Seed the cache as a previous visit would have:
  const rc = await import("../api/readCache");
  rc.settle("/api/campaigns", rc.generation("/api/campaigns"),
            [{ id: "saltmarch", name: "Saltmarch", world: "", scenes: 1,
               cover: "", last_scene: "", absorbed: 0, activity: "", blurb: "",
               parent: "", forked_from_scene: "", created: "", updated: "" }]);
  let resolve!: (v: unknown) => void;
  mocked.listCampaigns.mockReturnValue(new Promise((r) => { resolve = r; }));
  render(<CampaignsView />);
  expect(await screen.findByText("Saltmarch")).toBeInTheDocument(); // instant
  resolve([]);   // fresh answer: the shelf empties
  await waitFor(() => expect(screen.queryByText("Saltmarch")).toBeNull());
});

it("post-mutation refreshes pass fresh", async () => {
  // rename → listCampaigns(true); assert the mock saw `true`
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd frontend && npx vitest run src/routes/CampaignsView.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement**

- `CampaignsView`: replace the two `useEffect` fetches with
  `useCachedGet("/api/campaigns", () => api.listCampaigns())` and
  `useCachedGet("/api/worlds", () => api.listWorlds())`, feeding the
  existing `campaigns`/`worlds` state (or use the hook's `data` directly and
  keep a `setCampaigns` shim for the mutation paths). Every post-mutation
  refresh (`:164` rename, `:170` delete, `:198` fork) becomes
  `api.listCampaigns(true)`.
- `WorldsView`: same — `useCachedGet("/api/worlds", ...)`; `:46` create,
  `:53` rename, `:64` delete become `api.listWorlds(true)` (`:110` fork and
  `:143` import already pass `true`).

- [ ] **Step 4: Run both suites**

Run: `cd frontend && npx vitest run src/routes/CampaignsView.test.tsx src/routes/WorldsView.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/CampaignsView.tsx frontend/src/routes/WorldsView.tsx frontend/src/routes/CampaignsView.test.tsx frontend/src/routes/WorldsView.test.tsx frontend/src/api/client.ts
git commit -m "The shelves render their last payload and refresh fresh after writes"
```

---

### Task 12: WorldView stops re-asking every count

**Files:**
- Modify: `frontend/src/routes/WorldView.tsx:207-223` (the counts effect),
  `:275-300` (`openEntity`)
- Test: `frontend/src/routes/WorldView.test.tsx` (append)

**Interfaces:**
- Consumes: nothing new — this is scoping which counts refresh when.
- Produces: module-level `countsMemory = new Map<string, Record<string, number | null>>()`
  keyed by `scopeKey`, so a revisited scope paints its previous counts
  instantly.

- [ ] **Step 1: Write the failing tests** (append; the suite's existing
  mocks list per-kind api calls)

```tsx
it("a section switch refreshes only the departed section's count", async () => {
  render(<WorldView />);            // per the suite's route scaffolding
  await screen.findByText("Characters");
  mocked.listEntities.mockClear();
  mocked.listCharacters.mockClear();
  fireEvent.click(screen.getByText("Locations"));   // leave Characters
  await waitFor(() => expect(mocked.listCharacters).toHaveBeenCalledTimes(1));
  expect(mocked.listEntities).not.toHaveBeenCalledWith(
    expect.anything(), "items");                    // untouched kinds stay put
});

it("a reclassify refreshes both the source and destination kinds", async () => {
  // drive onReclassified via the EntityEditor mock: call the prop with
  // ("items", "some-id") while section === "locations"; assert both
  // listEntities(scope, "locations") and listEntities(scope, "items") fire.
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd frontend && npx vitest run src/routes/WorldView.test.tsx`
Expected: FAIL (today every switch refires all rows).

- [ ] **Step 3: Implement**

- Split the counts effect: effect A (full sweep) depends on
  `[campaign, cid, wid, groups, populated]` — **`section` removed** — and
  seeds from `countsMemory.get(scopeKey)` synchronously before fetching;
  every settled count writes through to `countsMemory`.
- Add a `prevSection` ref; a `select(key)` that changes section refreshes
  only `prevSection.current`'s count (one `countOf` call) before updating
  the ref.
- `openEntity(kind, id)` (the reclassify landing): refresh **both** the
  section being left and `kind` — two `countOf` calls — then set section.
  (Spec + open Codex thread: a reclassify decrements the source and
  increments the destination the user is navigating into.)

- [ ] **Step 4: Run**

Run: `cd frontend && npx vitest run src/routes/WorldView.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/WorldView.tsx frontend/src/routes/WorldView.test.tsx
git commit -m "WorldView refreshes only the counts a section change can have moved"
```

---

### Task 13: CampaignHub renders its last block, chronicle stays live

**Files:**
- Modify: `frontend/src/routes/CampaignHub.tsx:170-215`
- Test: `frontend/src/routes/CampaignHub.test.tsx` (append)

**Interfaces:**
- Consumes: `readCache` directly (the hub's block is a `Promise.all`, not a
  single path — cache the assembled block under a synthetic key).

- [ ] **Step 1: Write the failing test**

```tsx
it("paints the cached hub block instantly and never the cached chronicle", async () => {
  const rc = await import("../api/readCache");
  const key = "hub:saltmarch";
  rc.settle(key, rc.generation(key), {
    meta: { id: "saltmarch", name: "Saltmarch", world: "" },
    shell: fixtures.shell, scenes: [],
  });
  // hold the fresh reads open; the hub must already show the campaign name
  ...render with pending mocks...
  expect(await screen.findByText("Saltmarch")).toBeInTheDocument();
  // the recap region renders only after the LIVE chronicle read settles
  expect(screen.queryByTestId("hub-recap")).toBeNull();
});
```

(Shape the assertions to the hub's actual DOM — read the suite first; the
`hub-recap` hook may need a `data-testid` added where the newest chronicle
entry renders.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run src/routes/CampaignHub.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement**

In the hub's main effect (`:170-215`): key `hub:${cid}`. On entry, seed
`meta`/`shell`/`scenes` state from `readCache.cached(key)` when present.
The existing `Promise.all` splits: `getCampaign`+`getShell`+`listScenes`
settle into state **and** `readCache.settle(key, gen, block)`;
`getChronicle` stays exactly as today — fetched live, never seeded, never
stored (spec: the newest entry renders as the current recap, and a
pre-absorb recap painted as current is a stale record body).

- [ ] **Step 4: Run**

Run: `cd frontend && npx vitest run src/routes/CampaignHub.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/CampaignHub.tsx frontend/src/routes/CampaignHub.test.tsx
git commit -m "The hub paints its last block while the chronicle stays live"
```

---

### Task 14: the whole gate, and the frozen sweep unchanged

- [ ] **Step 1: Full backend + frontend + guards**

Run: `make check`
Expected: PASS. If a ratchet gate fails on a *smaller* count (a fix resolved
a baselined finding), run `make baseline` and include the shrunken file. New
findings are fixed, never baselined.

- [ ] **Step 2: Frozen sweep is byte-identical**

Run: `cd backend && git diff --exit-code tests/fixtures/frozen_campaign/snapshot.json`
Expected: no diff (nothing regenerates it; this asserts nobody did).

- [ ] **Step 3: Commit anything the gate touched, push, and hand back**

```bash
git add -A && git status --short
git commit -m "Baseline shrinks from the read-path work"   # only if needed
git push -u origin claude/campaign-world-load-perf-s2rmew
```

Then: the CLAUDE.md pipeline's implementation gates — `/codex:review`
against the diff, and the final `/codex:adversarial-review` against diff +
spec (on this PR, Codex reviews each push automatically; treat its findings
as those gates' findings).

---

## Resolution of the four open PR #443 threads

- **"Memoize the aggregate reads forced by each epoch"** → Task 9 Step 3's
  recorded comment: chronicle is one JSON parse, changes' hashes are already
  memoized; kept on the ETag list, no new memo until a profile says so.
- **"Give store-side writers an app epoch to bump"** → Task 7: the WeakSet
  registry + `bump_all()`.
- **"Propagate store-root invalidation across browser tabs"** → Task 10:
  `BroadcastChannel("grimoire-store-root")`, cleared on message; no-op where
  the API is absent.
- **"Evict stale payloads after authoritative not-found responses"** →
  Task 10: `useCachedGet`'s 404/410 branch evicts and surfaces the error.
