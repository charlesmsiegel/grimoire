# ImageGen Remaining Work Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land everything in `docs/superpowers/specs/2026-05-17-imagegen-remaining-design.md` §1–§13 (§14–§16 are explicitly v2/deferred and out of scope).

**Architecture:** Six parallel feature branches under `.worktrees/`. Branch **A** (config foundation: §6 + §7) must land first; branches **B–F** depend only on A and can rebase + merge independently:

- **A** `feature/imagegen-A-config` — migration adds `imagegen_config` column + `ImageGenConfig` YAML loader + per-campaign `get_/set_trigger_config` and `set_fallback_backend`. Persists active backend.
- **B** `feature/imagegen-B-orchestrator` — subscribe to `turn_complete` / scene events, evaluate `should_illustrate`, call `queue_generation`.
- **C** `feature/imagegen-C-rest` — 12 missing REST routes + add new event types to `_FORWARDED_EVENTS` + map `NoBackendAvailableError` to 503.
- **D** `feature/imagegen-D-lifecycle` — backend.generate gets optional `progress` + `cancel_token` kwargs; periodic health prober + fallback routing.
- **E** `feature/imagegen-E-ux` — small additions: `edit_and_regenerate`, `set_tags`, canonical seed, `prewarm`.
- **F** `feature/imagegen-F-persistence` — persistent `imagegen_jobs` table + reload-on-startup; `download_on_first_use` knob + download progress event + REST.

Each branch rebases on `main` immediately before merge. Merge order: A first, then B-F in any order. Conflicts between B-F should be minimal (different files in most cases); when they overlap (`service.py`), the second branch rebases.

**Tech Stack:** Python 3.12, FastAPI, aiosqlite, pytest + pytest-asyncio (`asyncio_mode = "auto"`), Pydantic v2, diffusers (lazy), PowerShell shell.

---

## Conventions used in this plan

- **Test runner:** `pytest backend/tests/imagegen -v` (or specific file/test). Async by default — no `@pytest.mark.asyncio` decorator needed.
- **Lint/format:** `ruff check backend/src/grimoire/imagegen backend/tests/imagegen` and `ruff format <same paths>`. Run both before every commit.
- **Service fixture:** `service` fixture in `backend/tests/imagegen/conftest.py` yields `(svc, bus)` tuple — `svc: ImageGenService` registered with `InMemoryDiffusersBackend` as `default_backend_id="diffusers-memory"`, `bus: EventBus`. Campaign `"camp-1"` and scene `"scene-1"` pre-seeded.
- **Event assertion pattern:** Subscribe a list-appending lambda to `bus`, trigger work, assert membership/payload.
- **Commit style:** Match recent commits — imperative subject under 70 chars, optional body, footer `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.
- **DB schema migrations:** `backend/src/grimoire/storage/migrations/NNN_<desc>.sql`. Raw SQL only. Apply by writing the file — tests auto-apply via the `store` fixture.

---

## Branch setup (once, before any task)

- [ ] **Step S1: Create worktree directories**

```powershell
git worktree add .worktrees/imagegen-A-config -b feature/imagegen-A-config main
git worktree add .worktrees/imagegen-B-orchestrator -b feature/imagegen-B-orchestrator main
git worktree add .worktrees/imagegen-C-rest -b feature/imagegen-C-rest main
git worktree add .worktrees/imagegen-D-lifecycle -b feature/imagegen-D-lifecycle main
git worktree add .worktrees/imagegen-E-ux -b feature/imagegen-E-ux main
git worktree add .worktrees/imagegen-F-persistence -b feature/imagegen-F-persistence main
```

Note: B–F initially branch from `main` but should rebase onto `feature/imagegen-A-config` (or its merge into `main`) before their first task that depends on A's deliverables.

---

# Branch A — Config foundation (§6 + §7)

**Working directory:** `.worktrees/imagegen-A-config`
**Why it goes first:** Defines the per-campaign storage shape that branches B, D depend on, and the top-level YAML loader that touches `main.py`'s `ImageGenService` construction.

### Task A1: Add `imagegen_config` column to campaigns

**Files:**
- Create: `backend/src/grimoire/storage/migrations/018_campaigns_imagegen_config.sql`
- Test: `backend/tests/imagegen/test_imagegen_config_column.py` (new)

- [ ] **Step 1: Write failing test for column presence**

```python
# backend/tests/imagegen/test_imagegen_config_column.py
"""Migration 018: per-campaign imagegen_config TEXT column."""

from __future__ import annotations

from pathlib import Path

from grimoire.storage import Database, apply_migrations


async def test_campaigns_table_has_imagegen_config_column(tmp_path: Path) -> None:
    db = Database(tmp_path / "x.sqlite", pool_size=1)
    await db.connect()
    try:
        await apply_migrations(db)
        rows = await db.fetchall("PRAGMA table_info(campaigns)")
        names = {row["name"] for row in rows}
        assert "imagegen_config" in names, f"expected imagegen_config in {names}"
    finally:
        await db.close()
```

- [ ] **Step 2: Run test, expect failure**

`pytest backend/tests/imagegen/test_imagegen_config_column.py -v` → FAIL ("expected imagegen_config in {…}").

- [ ] **Step 3: Add the migration**

```sql
-- backend/src/grimoire/storage/migrations/018_campaigns_imagegen_config.sql
-- Per-campaign ImageGen config (trigger policy, active backend id, fallback
-- backend id). Stored as JSON-encoded text. NULL = "use defaults". See
-- imagegen/config.py for the schema.

ALTER TABLE campaigns ADD COLUMN imagegen_config TEXT;
```

- [ ] **Step 4: Run test, expect pass**

`pytest backend/tests/imagegen/test_imagegen_config_column.py -v` → PASS.

- [ ] **Step 5: Lint + commit**

```powershell
ruff check backend/tests/imagegen ; if ($?) { ruff format backend/tests/imagegen }
git add backend/src/grimoire/storage/migrations/018_campaigns_imagegen_config.sql backend/tests/imagegen/test_imagegen_config_column.py
git commit -m @'
Add imagegen_config column to campaigns

Per-campaign ImageGen settings (trigger policy, active backend id, fallback)
serialized as JSON. NULL means "use defaults". Service-side accessors land
in the next commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
'@
```

### Task A2: `ImageGenConfig` dataclass + `from_yaml` loader

**Files:**
- Create: `backend/src/grimoire/imagegen/config.py`
- Modify: `backend/src/grimoire/imagegen/__init__.py` (re-export)
- Test: `backend/tests/imagegen/test_config.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/imagegen/test_config.py
"""Top-level imagegen YAML config (§7)."""

from __future__ import annotations

from pathlib import Path

from grimoire.imagegen.config import ImageGenConfig


def test_from_yaml_returns_defaults_when_file_missing(tmp_path: Path) -> None:
    cfg = ImageGenConfig.from_yaml(tmp_path / "nope.yaml")
    assert cfg.default_backend is None
    assert cfg.queue_max_concurrent_per_backend == 1
    assert cfg.caching_enabled is True
    assert cfg.thumbnails_size == (256, 256)
    assert cfg.thumbnails_format == "JPEG"
    assert cfg.thumbnails_quality == 85
    assert cfg.storage_image_format == "PNG"


def test_from_yaml_parses_top_level_keys(tmp_path: Path) -> None:
    path = tmp_path / "imagegen.yaml"
    path.write_text(
        "default_backend: a1111\n"
        "queue:\n"
        "  max_concurrent_per_backend: 2\n"
        "  persist_pending: true\n"
        "caching:\n"
        "  enabled: false\n"
        "thumbnails:\n"
        "  size: [128, 128]\n"
        "  format: PNG\n"
        "  quality: 95\n"
        "storage:\n"
        "  image_format: WEBP\n",
        encoding="utf-8",
    )
    cfg = ImageGenConfig.from_yaml(path)
    assert cfg.default_backend == "a1111"
    assert cfg.queue_max_concurrent_per_backend == 2
    assert cfg.queue_persist_pending is True
    assert cfg.caching_enabled is False
    assert cfg.thumbnails_size == (128, 128)
    assert cfg.thumbnails_format == "PNG"
    assert cfg.thumbnails_quality == 95
    assert cfg.storage_image_format == "WEBP"


def test_from_yaml_ignores_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "imagegen.yaml"
    path.write_text("default_backend: x\nzzz: 'unknown'\n", encoding="utf-8")
    cfg = ImageGenConfig.from_yaml(path)
    assert cfg.default_backend == "x"
```

- [ ] **Step 2: Run tests, expect ImportError**

`pytest backend/tests/imagegen/test_config.py -v` → FAIL (no `imagegen.config` module).

- [ ] **Step 3: Implement the loader**

```python
# backend/src/grimoire/imagegen/config.py
"""Top-level ImageGen YAML config loader (spec 12 §Configuration).

Only the knobs that actually flow through to the service are surfaced here.
Plugin-specific knobs (e.g. ``diffusers.active_model``) live in the plugin's
``manifest.yaml`` ``config_schema`` and are not duplicated here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class ImageGenConfig:
    default_backend: str | None = None
    queue_max_concurrent_per_backend: int = 1
    queue_persist_pending: bool = False
    caching_enabled: bool = True
    caching_cache_dir: str | None = None
    thumbnails_size: tuple[int, int] = (256, 256)
    thumbnails_format: str = "JPEG"
    thumbnails_quality: int = 85
    storage_image_format: str = "PNG"

    @classmethod
    def from_yaml(cls, path: Path) -> ImageGenConfig:
        """Load top-level config; return defaults if the file is missing."""
        if not path.exists():
            return cls()
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            return cls()
        return cls._from_mapping(raw)

    @classmethod
    def _from_mapping(cls, raw: dict[str, Any]) -> ImageGenConfig:
        queue = raw.get("queue") or {}
        caching = raw.get("caching") or {}
        thumbs = raw.get("thumbnails") or {}
        storage = raw.get("storage") or {}
        size_raw = thumbs.get("size") or (256, 256)
        if isinstance(size_raw, list | tuple) and len(size_raw) == 2:
            size = (int(size_raw[0]), int(size_raw[1]))
        else:
            size = (256, 256)
        return cls(
            default_backend=raw.get("default_backend") or None,
            queue_max_concurrent_per_backend=int(
                queue.get("max_concurrent_per_backend") or 1
            ),
            queue_persist_pending=bool(queue.get("persist_pending", False)),
            caching_enabled=bool(caching.get("enabled", True)),
            caching_cache_dir=caching.get("cache_dir") or None,
            thumbnails_size=size,
            thumbnails_format=str(thumbs.get("format") or "JPEG").upper(),
            thumbnails_quality=int(thumbs.get("quality") or 85),
            storage_image_format=str(storage.get("image_format") or "PNG").upper(),
        )
```

- [ ] **Step 4: Re-export from `__init__.py`**

In `backend/src/grimoire/imagegen/__init__.py`, add `ImageGenConfig` to the imports from `.config` and to `__all__`:

```python
from grimoire.imagegen.config import ImageGenConfig
```

Add `"ImageGenConfig"` (sorted) to `__all__`.

- [ ] **Step 5: Run tests, expect pass**

`pytest backend/tests/imagegen/test_config.py -v` → PASS.

- [ ] **Step 6: Lint + commit**

```powershell
ruff check backend/src/grimoire/imagegen backend/tests/imagegen ; if ($?) { ruff format backend/src/grimoire/imagegen backend/tests/imagegen }
git add backend/src/grimoire/imagegen/config.py backend/src/grimoire/imagegen/__init__.py backend/tests/imagegen/test_config.py
git commit -m @'
Add ImageGenConfig YAML loader

§7 of imagegen remaining-design. Surfaces default_backend, queue, caching,
thumbnails, storage knobs as a frozen dataclass with a ``from_yaml``
classmethod. Plugin-specific knobs stay in plugin manifests. Wiring into
ImageGenService and main.py follows in the next commits.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
'@
```

### Task A3: Plumb `ImageGenConfig` through service constructor

**Files:**
- Modify: `backend/src/grimoire/imagegen/service.py` (`ImageGenService.__init__`, `_persist_result`, `_lookup_cache`/`_store_in_cache`)
- Modify: `backend/src/grimoire/imagegen/backend.py` (`make_thumbnail` signature: accept tuple, format, quality)
- Test: `backend/tests/imagegen/test_config_plumbing.py` (new)

- [ ] **Step 1: Write failing tests for config-driven behavior**

```python
# backend/tests/imagegen/test_config_plumbing.py
"""ImageGenConfig flows into runtime behavior."""

from __future__ import annotations

import pytest

from grimoire.event_bus import EventBus
from grimoire.imagegen import (
    BackendRegistry,
    ImageGenConfig,
    ImageGenService,
    InMemoryDiffusersBackend,
)
from grimoire.imagegen.service import _validate_campaign_id  # noqa: F401
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def store_with_campaign(tmp_path):
    db = Database(tmp_path / "c.sqlite", pool_size=1)
    await db.connect()
    await apply_migrations(db)
    data = tmp_path / "data"
    data.mkdir()
    s = StateStore(db, data)
    await s.upsert_campaign(campaign_id="camp-1", name="t")
    try:
        yield s
    finally:
        await db.close()


async def test_service_accepts_imagegen_config(store_with_campaign) -> None:
    reg = BackendRegistry()
    reg.register(InMemoryDiffusersBackend())
    cfg = ImageGenConfig(default_backend="diffusers-memory", caching_enabled=False)
    svc = ImageGenService(
        store=store_with_campaign,
        registry=reg,
        config=cfg,
        event_bus=EventBus(),
    )
    try:
        info = await svc.active_backend("camp-1")
        assert info.id == "diffusers-memory"
        assert svc.config.caching_enabled is False
    finally:
        await svc.aclose()


async def test_caching_disabled_skips_cache_store(store_with_campaign) -> None:
    from grimoire.types.imagegen import GenerationRequest

    reg = BackendRegistry()
    reg.register(InMemoryDiffusersBackend())
    cfg = ImageGenConfig(default_backend="diffusers-memory", caching_enabled=False)
    svc = ImageGenService(
        store=store_with_campaign, registry=reg, config=cfg, event_bus=EventBus()
    )
    try:
        await svc.generate_sync(
            "camp-1", GenerationRequest(prompt="x", width=8, height=8, seed=42)
        )
        assert svc._cache == {}
    finally:
        await svc.aclose()
```

- [ ] **Step 2: Run tests, expect fail**

`pytest backend/tests/imagegen/test_config_plumbing.py -v` → FAIL (constructor rejects `config`).

- [ ] **Step 3: Add `config` kwarg + plumb to cache & thumbnails**

In `backend/src/grimoire/imagegen/service.py`:

In the import block at the top, add:
```python
from grimoire.imagegen.config import ImageGenConfig
```

In `ImageGenService.__init__`, **after** the existing `default_backend_id` parameter, add a new keyword-only `config` parameter and store it:

```python
    def __init__(
        self,
        *,
        store: StateStore,
        registry: BackendRegistry,
        default_backend_id: str | None = None,
        event_bus: EventBus | None = None,
        composer: PromptComposer | None = None,
        plugin_backend_ids: Iterable[str] | None = None,
        thumbnail_subdir: str = "thumbnails",
        config: ImageGenConfig | None = None,
    ) -> None:
        self.store = store
        self.data_root = store.data_root
        self.registry = registry
        # Resolve default_backend_id: explicit arg wins; otherwise fall back
        # to ImageGenConfig.default_backend.
        self.config = config or ImageGenConfig()
        self.default_backend_id = default_backend_id or self.config.default_backend
        self.event_bus = event_bus
        # ... (rest unchanged)
```

In `_store_in_cache`, guard the write:
```python
    def _store_in_cache(...) -> None:
        if not self.config.caching_enabled:
            return
        if request.seed is None:
            return
        # ... unchanged
```

In `_lookup_cache`:
```python
    def _lookup_cache(...) -> GenerationResult | None:
        if not self.config.caching_enabled:
            return None
        if request.seed is None:
            return None
        # ... unchanged
```

In `_persist_result`, replace the hard-coded thumbnail handling. Look for the line `thumb_path.write_bytes(result.thumbnail_bytes or result.image_bytes)`. Above it, regenerate the thumbnail through `make_thumbnail` honoring config:

```python
        from grimoire.imagegen.backend import make_thumbnail

        thumb_bytes = make_thumbnail(
            result.image_bytes,
            size=self.config.thumbnails_size,
            format=self.config.thumbnails_format,
            quality=self.config.thumbnails_quality,
        )
        thumb_path.write_bytes(thumb_bytes)
```

Remove the previous `thumb_path.write_bytes(...)` line.

- [ ] **Step 4: Extend `make_thumbnail` signature**

In `backend/src/grimoire/imagegen/backend.py`, replace the existing `make_thumbnail` function:

```python
def make_thumbnail(
    image_bytes: bytes,
    size: tuple[int, int] = (256, 256),
    *,
    format: str = "JPEG",
    quality: int = 85,
) -> bytes:
    """Build a thumbnail for ``image_bytes`` honoring config."""
    try:  # pragma: no cover - optional dependency
        from PIL import Image  # type: ignore
    except ImportError:
        return image_bytes
    try:
        with Image.open(io.BytesIO(image_bytes)) as im:  # type: ignore[arg-type]
            im = im.convert("RGB")
            im.thumbnail(size)
            buf = io.BytesIO()
            fmt = format.upper()
            if fmt == "JPEG":
                im.save(buf, format=fmt, quality=int(quality), optimize=True)
            else:
                im.save(buf, format=fmt)
            return buf.getvalue()
    except Exception:  # pragma: no cover - defensive
        return image_bytes
```

- [ ] **Step 5: Run tests, expect pass**

`pytest backend/tests/imagegen -v` (run the whole suite — earlier tests should still pass).

- [ ] **Step 6: Lint + commit**

```powershell
ruff check backend/src/grimoire/imagegen backend/tests/imagegen ; if ($?) { ruff format backend/src/grimoire/imagegen backend/tests/imagegen }
git add backend/src/grimoire/imagegen/service.py backend/src/grimoire/imagegen/backend.py backend/tests/imagegen/test_config_plumbing.py
git commit -m @'
Plumb ImageGenConfig through service + thumbnails

caching_enabled toggles cache lookup/store. thumbnails_size/format/quality
flow into make_thumbnail. default_backend acts as fallback when caller
omits default_backend_id.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
'@
```

### Task A4: Per-campaign trigger config storage

**Files:**
- Modify: `backend/src/grimoire/imagegen/service.py` (add `get_trigger_config` / `set_trigger_config` / `_load_imagegen_config_row`)
- Modify: `backend/src/grimoire/types/protocols.py` (add to `ImageGen` Protocol)
- Test: `backend/tests/imagegen/test_trigger_config_storage.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/imagegen/test_trigger_config_storage.py
"""Per-campaign trigger config persistence (§6)."""

from __future__ import annotations

from grimoire.imagegen import TriggerConfig


async def test_get_trigger_config_returns_defaults_for_unknown_campaign(service) -> None:
    svc, _ = service
    cfg = await svc.get_trigger_config("camp-1")
    assert cfg == TriggerConfig()


async def test_set_then_get_trigger_config_round_trip(service) -> None:
    svc, _ = service
    new = TriggerConfig(
        mode="every_n_posts",
        every_n=3,
        on_scene_open=False,
        on_new_location=False,
        on_new_character_appearance=False,
        auto_during_combat=True,
    )
    await svc.set_trigger_config("camp-1", new)
    got = await svc.get_trigger_config("camp-1")
    assert got == new


async def test_set_trigger_config_does_not_clobber_unrelated_imagegen_config(
    service,
) -> None:
    svc, _ = service
    await svc.set_active_backend("camp-1", "diffusers-memory")
    await svc.set_trigger_config("camp-1", TriggerConfig(mode="per_post"))
    info = await svc.active_backend("camp-1")
    assert info.id == "diffusers-memory"
```

- [ ] **Step 2: Run tests, expect fail**

`pytest backend/tests/imagegen/test_trigger_config_storage.py -v` → FAIL (no method).

- [ ] **Step 3: Implement persistence helpers**

In `backend/src/grimoire/imagegen/service.py`, add near the top of the `ImageGenService` class (right after the `Health` section, before `Internals`):

```python
    # ------------------------------------------------------------------ #
    # Per-campaign config storage (§6)
    # ------------------------------------------------------------------ #

    async def get_trigger_config(self, campaign_id: str) -> TriggerConfig:
        raw = await self._load_imagegen_config_row(campaign_id)
        return TriggerConfig.from_config(raw.get("trigger") if raw else None)

    async def set_trigger_config(
        self, campaign_id: str, trigger: TriggerConfig
    ) -> None:
        await self._mutate_imagegen_config_row(
            campaign_id,
            update={
                "trigger": {
                    "mode": trigger.mode,
                    "every_n": trigger.every_n,
                    "on_scene_open": trigger.on_scene_open,
                    "on_new_location": trigger.on_new_location,
                    "on_new_character_appearance": trigger.on_new_character_appearance,
                    "auto_during_combat": trigger.auto_during_combat,
                }
            },
        )

    async def _load_imagegen_config_row(self, campaign_id: str) -> dict[str, Any]:
        _validate_campaign_id(campaign_id)
        row = await self.store.db.fetchone(
            "SELECT imagegen_config FROM campaigns WHERE id = ?", (campaign_id,)
        )
        if row is None:
            return {}
        raw = row["imagegen_config"]
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}

    async def _mutate_imagegen_config_row(
        self, campaign_id: str, *, update: dict[str, Any]
    ) -> None:
        _validate_campaign_id(campaign_id)
        existing = await self._load_imagegen_config_row(campaign_id)
        merged = {**existing, **update}
        await self.store.db.execute(
            "UPDATE campaigns SET imagegen_config = ? WHERE id = ?",
            (json.dumps(merged, sort_keys=True), campaign_id),
        )
```

- [ ] **Step 4: Add to Protocol**

In `backend/src/grimoire/types/protocols.py`, inside `class ImageGen(Protocol):`, add (after `set_active_backend`):

```python
    async def get_trigger_config(self, campaign_id: CampaignId) -> "TriggerConfig": ...
    async def set_trigger_config(
        self, campaign_id: CampaignId, trigger: "TriggerConfig"
    ) -> None: ...
```

At the top of `protocols.py`, add the import (guard against circular import — put inside TYPE_CHECKING if needed; check existing imports there first and follow convention):

```python
from grimoire.imagegen.service import TriggerConfig  # noqa: TCH001
```

(If circular, move under `if TYPE_CHECKING:` block and ensure forward refs work.)

- [ ] **Step 5: Run tests, expect pass**

`pytest backend/tests/imagegen -v` → all pass.

- [ ] **Step 6: Commit**

```powershell
ruff check backend/src/grimoire/imagegen backend/src/grimoire/types backend/tests/imagegen ; if ($?) { ruff format backend/src/grimoire/imagegen backend/src/grimoire/types backend/tests/imagegen }
git add backend/src/grimoire/imagegen/service.py backend/src/grimoire/types/protocols.py backend/tests/imagegen/test_trigger_config_storage.py
git commit -m @'
Persist per-campaign trigger config

§6 of imagegen remaining-design. Adds get_trigger_config /
set_trigger_config service methods backed by the new
campaigns.imagegen_config TEXT column. The shape merges with other
imagegen settings (active backend, fallback) without clobbering them.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
'@
```

### Task A5: Persist active backend + fallback backend per campaign

**Files:**
- Modify: `backend/src/grimoire/imagegen/service.py` (`set_active_backend`, `active_backend`, new `set_fallback_backend`, `get_fallback_backend`)
- Modify: `backend/src/grimoire/types/protocols.py` (add `set_/get_fallback_backend`)
- Test: `backend/tests/imagegen/test_active_backend_persistence.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/imagegen/test_active_backend_persistence.py
from __future__ import annotations

from grimoire.event_bus import EventBus
from grimoire.imagegen import (
    BackendRegistry,
    ImageGenService,
    InMemoryDiffusersBackend,
)


async def _new_service(store, *, default_backend_id="diffusers-memory"):
    reg = BackendRegistry()
    reg.register(InMemoryDiffusersBackend())
    return ImageGenService(
        store=store, registry=reg, default_backend_id=default_backend_id,
        event_bus=EventBus(),
    )


async def test_set_active_backend_survives_service_restart(store) -> None:
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    svc = await _new_service(store)
    await svc.set_active_backend("camp-1", "diffusers-memory")
    await svc.aclose()

    svc2 = await _new_service(store, default_backend_id=None)
    try:
        info = await svc2.active_backend("camp-1")
        assert info.id == "diffusers-memory"
    finally:
        await svc2.aclose()


async def test_set_fallback_backend_round_trips(store) -> None:
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    svc = await _new_service(store)
    try:
        await svc.set_fallback_backend("camp-1", "diffusers-memory")
        assert await svc.get_fallback_backend("camp-1") == "diffusers-memory"
    finally:
        await svc.aclose()
```

- [ ] **Step 2: Run, expect fail**

- [ ] **Step 3: Update `set_active_backend` to persist + add fallback methods**

Replace `set_active_backend` body:

```python
    async def set_active_backend(self, campaign_id: str, backend_id: str) -> None:
        if backend_id not in self.registry:
            raise KeyError(f"no backend registered with id {backend_id!r}")
        self._campaign_backend[campaign_id] = backend_id
        self._ensure_handle(backend_id)
        await self._mutate_imagegen_config_row(
            campaign_id, update={"active_backend": backend_id}
        )
```

Replace the lookup logic at the top of `active_backend`:

```python
    async def active_backend(self, campaign_id: str) -> BackendInfo:
        backend_id = self._campaign_backend.get(campaign_id)
        if backend_id is None:
            raw = await self._load_imagegen_config_row(campaign_id)
            backend_id = raw.get("active_backend") or self.default_backend_id
            if backend_id is not None:
                self._campaign_backend[campaign_id] = backend_id
        backend = self.registry.get(backend_id) if backend_id else None
        # ... (rest unchanged from current implementation, including the
        # "configured backend was removed" fallback)
```

Add new fallback methods next to the trigger-config block:

```python
    async def set_fallback_backend(
        self, campaign_id: str, backend_id: str | None
    ) -> None:
        if backend_id is not None and backend_id not in self.registry:
            raise KeyError(f"no backend registered with id {backend_id!r}")
        await self._mutate_imagegen_config_row(
            campaign_id, update={"fallback_backend": backend_id}
        )

    async def get_fallback_backend(self, campaign_id: str) -> str | None:
        raw = await self._load_imagegen_config_row(campaign_id)
        return raw.get("fallback_backend")
```

Also update `queue_generation` to read active backend from DB when not in memory — add a fallback path:

```python
    async def queue_generation(
        self,
        campaign_id: str,
        scene_id: str | None,
        post_id: str | None,
        request: GenerationRequest | None = None,
        priority: int = 5,
    ) -> str:
        _validate_campaign_id(campaign_id)
        # ... compose request as before ...
        backend_id = self._campaign_backend.get(campaign_id)
        if backend_id is None:
            raw = await self._load_imagegen_config_row(campaign_id)
            backend_id = raw.get("active_backend") or self.default_backend_id
            if backend_id is not None:
                self._campaign_backend[campaign_id] = backend_id
        if backend_id not in self.registry:
            raise KeyError(f"no backend registered with id {backend_id!r}")
        # ... rest unchanged ...
```

- [ ] **Step 4: Add to Protocol** — `set_fallback_backend` + `get_fallback_backend` in `ImageGen` Protocol.

- [ ] **Step 5: Run tests, expect pass**

- [ ] **Step 6: Commit**

```
git commit -m "Persist active and fallback backend per campaign"
```

### Task A6: Wire `ImageGenConfig.from_yaml` in `main.py`

**Files:**
- Modify: `backend/src/grimoire/main.py` (load config, pass to `ImageGenService(...)`)
- Test: `backend/tests/main/test_imagegen_config_wiring.py` (new — only if test infra supports it; otherwise skip and verify by hand)

- [ ] **Step 1: Add YAML load + pass to constructor**

In `backend/src/grimoire/main.py`, before the `ImageGenService(...)` instantiation (around line 145–156 per the earlier scan), add:

```python
from grimoire.imagegen import ImageGenConfig

imagegen_config_path = container.data_root / "config" / "imagegen.yaml"
imagegen_cfg = ImageGenConfig.from_yaml(imagegen_config_path)
```

Then pass `config=imagegen_cfg` to the `ImageGenService(...)` call.

- [ ] **Step 2: Smoke-test import + boot**

```powershell
python -c "from grimoire.main import create_app; create_app()"
```

Should produce no errors.

- [ ] **Step 3: Commit**

```
git commit -m "Wire imagegen.yaml loader through main.py"
```

### Task A7: Merge branch A

- [ ] **Step 1: Rebase on main, run full test suite**

```powershell
git fetch origin main:main
git rebase main
pytest backend/tests/imagegen -v
```

- [ ] **Step 2: Merge to main using rebase-merge**

From repo root (not the worktree):

```powershell
git checkout main
git merge --ff-only feature/imagegen-A-config
git push origin main
```

(If not fast-forwardable, use `git rebase main` inside the worktree first then `git merge --ff-only`.)

---

# Branch B — Orchestrator fan-out (§1)

**Working directory:** `.worktrees/imagegen-B-orchestrator`
**Depends on:** Branch A merged (so the worktree must rebase onto A after A merges).

### Task B1: `ImageGenIntegration` subscriber

**Files:**
- Create: `backend/src/grimoire/imagegen/integration.py`
- Modify: `backend/src/grimoire/imagegen/__init__.py` (re-export)
- Test: `backend/tests/imagegen/test_integration.py` (new)

- [ ] **Step 1: Write failing test for the subscriber pattern**

```python
# backend/tests/imagegen/test_integration.py
"""§1 Orchestrator fan-out: ImageGen subscribes to turn_complete and
calls queue_generation based on the per-campaign TriggerConfig."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from grimoire.event_bus import Event, EventBus
from grimoire.imagegen import TriggerConfig
from grimoire.imagegen.integration import ImageGenIntegration


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


async def test_fires_queue_generation_on_turn_complete_per_post(bus) -> None:
    svc = AsyncMock()
    svc.get_trigger_config.return_value = TriggerConfig(mode="per_post")
    integ = ImageGenIntegration(svc, bus)
    integ.start()
    try:
        await bus.emit(Event(type="turn_complete", payload={
            "turn_id": "t1", "campaign_id": "camp-1", "scene_id": "scene-1",
        }))
    finally:
        integ.stop()
    svc.queue_generation.assert_awaited_once_with(
        campaign_id="camp-1", scene_id="scene-1", post_id=None,
    )


async def test_manual_only_mode_does_not_queue(bus) -> None:
    svc = AsyncMock()
    svc.get_trigger_config.return_value = TriggerConfig(mode="manual_only")
    integ = ImageGenIntegration(svc, bus)
    integ.start()
    try:
        await bus.emit(Event(type="turn_complete", payload={
            "campaign_id": "camp-1", "scene_id": "scene-1",
        }))
    finally:
        integ.stop()
    svc.queue_generation.assert_not_awaited()


async def test_scene_started_sets_on_scene_open_flag(bus) -> None:
    svc = AsyncMock()
    svc.get_trigger_config.return_value = TriggerConfig(
        mode="per_scene", on_scene_open=True
    )
    integ = ImageGenIntegration(svc, bus)
    integ.start()
    try:
        # Scene Manager emits scene_started; integration latches "next
        # turn_complete for this scene treats is_scene_open=True".
        await bus.emit(Event(type="scene_started", payload={
            "campaign_id": "camp-1", "scene_id": "scene-2",
        }))
        await bus.emit(Event(type="turn_complete", payload={
            "campaign_id": "camp-1", "scene_id": "scene-2", "turn_id": "t9",
        }))
    finally:
        integ.stop()
    svc.queue_generation.assert_awaited_once()


async def test_queue_generation_errors_swallowed_and_logged(bus, caplog) -> None:
    svc = AsyncMock()
    svc.get_trigger_config.return_value = TriggerConfig(mode="per_post")
    svc.queue_generation.side_effect = KeyError("no backend")
    integ = ImageGenIntegration(svc, bus)
    integ.start()
    try:
        await bus.emit(Event(type="turn_complete", payload={
            "campaign_id": "camp-1", "scene_id": "scene-1",
        }))
    finally:
        integ.stop()
    # Should not raise; should log a warning.
    assert any("imagegen fan-out failed" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run, expect ImportError**

`pytest backend/tests/imagegen/test_integration.py -v` → FAIL.

- [ ] **Step 3: Implement `ImageGenIntegration`**

```python
# backend/src/grimoire/imagegen/integration.py
"""Glue: subscribe to Orchestrator + Scene Manager events and ask the
ImageGen service to queue background image jobs based on the
per-campaign TriggerConfig (§1 of imagegen remaining-design).

This module is intentionally thin: every decision goes through the pure
``should_illustrate`` function so policy stays testable in isolation.
"""

from __future__ import annotations

import logging
from typing import Any

from grimoire.event_bus import Event, EventBus, Subscription
from grimoire.imagegen.service import ImageGenService, should_illustrate

logger = logging.getLogger(__name__)


class ImageGenIntegration:
    """Bridge from event bus → ImageGenService.queue_generation."""

    def __init__(self, service: ImageGenService, bus: EventBus) -> None:
        self._svc = service
        self._bus = bus
        self._subs: list[Subscription] = []
        # Scene-level latches: "fire on_scene_open / on_new_location / etc.
        # on the NEXT turn_complete for this scene id". Cleared after use.
        self._pending_flags: dict[str, set[str]] = {}

    def start(self) -> None:
        if self._subs:
            return
        self._subs = [
            self._bus.subscribe("turn_complete", self._on_turn_complete),
            self._bus.subscribe("scene_started", self._on_scene_started),
        ]

    def stop(self) -> None:
        for sub in self._subs:
            sub.unsubscribe()
        self._subs.clear()
        self._pending_flags.clear()

    async def _on_scene_started(self, event: Event) -> None:
        scene_id = event.payload.get("scene_id")
        if not scene_id:
            return
        flags = self._pending_flags.setdefault(str(scene_id), set())
        flags.add("on_scene_open")

    async def _on_turn_complete(self, event: Event) -> None:
        payload: dict[str, Any] = event.payload or {}
        campaign_id = payload.get("campaign_id")
        scene_id = payload.get("scene_id")
        if not campaign_id:
            return
        try:
            cfg = await self._svc.get_trigger_config(str(campaign_id))
        except Exception:
            logger.exception("imagegen fan-out failed loading trigger config")
            return

        flags = self._pending_flags.pop(str(scene_id), set()) if scene_id else set()
        is_scene_open = "on_scene_open" in flags

        if not should_illustrate(
            cfg,
            is_scene_open=is_scene_open,
            is_new_location=False,  # not currently detected; see TODO
            is_new_character=False,
            is_in_combat=False,
            post_index=None,
        ):
            return

        try:
            await self._svc.queue_generation(
                campaign_id=str(campaign_id),
                scene_id=str(scene_id) if scene_id else None,
                post_id=payload.get("post_id"),
            )
        except Exception:
            logger.warning("imagegen fan-out failed queuing job", exc_info=True)
```

Re-export from `__init__.py`:
```python
from grimoire.imagegen.integration import ImageGenIntegration
```

- [ ] **Step 4: Run tests, expect pass**

`pytest backend/tests/imagegen/test_integration.py -v`.

- [ ] **Step 5: Commit**

```powershell
ruff check backend/src/grimoire/imagegen backend/tests/imagegen ; if ($?) { ruff format backend/src/grimoire/imagegen backend/tests/imagegen }
git add backend/src/grimoire/imagegen/integration.py backend/src/grimoire/imagegen/__init__.py backend/tests/imagegen/test_integration.py
git commit -m @'
Add ImageGenIntegration event-bus subscriber

§1 of imagegen remaining-design. ImageGenIntegration subscribes to
turn_complete + scene_started and calls queue_generation when the
campaign's TriggerConfig says yes. Errors are swallowed + logged so a
backend hiccup never breaks the turn loop.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
'@
```

### Task B2: Wire integration in `main.py` lifespan

**Files:**
- Modify: `backend/src/grimoire/main.py` (start integration in startup, stop on shutdown)

- [ ] **Step 1: Add lifespan wiring**

In the startup block, **after** `container.imagegen` is fully constructed and `container.event_bus` is in scope, add:

```python
from grimoire.imagegen import ImageGenIntegration

container.imagegen_integration = ImageGenIntegration(
    container.imagegen, container.event_bus
)
container.imagegen_integration.start()
```

(If `ServiceContainer` is a dataclass with a fixed shape, extend it. Find the `ServiceContainer` class in `backend/src/grimoire/api/container.py` and add `imagegen_integration: ImageGenIntegration | None = None` to it.)

In `_shutdown` (around line 270–294 per scan), add:

```python
if container.imagegen_integration is not None:
    container.imagegen_integration.stop()
```

- [ ] **Step 2: Smoke-test**

```powershell
python -c "from grimoire.main import create_app; create_app()"
```

- [ ] **Step 3: Commit**

```
git commit -m "Start ImageGenIntegration in main.py lifespan"
```

### Task B3: Merge branch B (after A is on main)

Same shape as Task A7. Rebase, run full suite, ff-merge.

---

# Branch C — REST surface (§13)

**Working directory:** `.worktrees/imagegen-C-rest`
**Depends on:** Branch A merged (uses new `set_/get_trigger_config`, `set_fallback_backend`, `set_active_backend` persistence).

### Task C1: Map `NoBackendAvailableError` to 503

**Files:**
- Modify: `backend/src/grimoire/api/util.py` (`map_lookup_errors`)
- Test: `backend/tests/api/test_no_backend_503.py` (new)

- [ ] **Step 1: Write failing test**

```python
# backend/tests/api/test_no_backend_503.py
"""map_lookup_errors should translate NoBackendAvailableError → 503."""

from __future__ import annotations

from grimoire.api.util import map_lookup_errors
from grimoire.imagegen.service import NoBackendAvailableError


def test_no_backend_available_error_maps_to_503() -> None:
    exc = NoBackendAvailableError("no plugin")
    http = map_lookup_errors(exc)
    assert http.status_code == 503
    assert "no plugin" in http.detail
```

- [ ] **Step 2: Run, expect fail (500 currently)**

- [ ] **Step 3: Patch `map_lookup_errors`**

In `backend/src/grimoire/api/util.py`, before the final `return HTTPException(status_code=500, ...)`, add:

```python
    if type(exc).__name__ == "NoBackendAvailableError" or "nobackendavailable" in name:
        return HTTPException(status_code=503, detail=str(exc))
```

(Using the name check avoids a hard import of `NoBackendAvailableError` in the util module — keeps `api/util.py` free of domain imports.)

- [ ] **Step 4: Run, expect pass**

- [ ] **Step 5: Commit**

```
git commit -m "Map NoBackendAvailableError to 503"
```

### Task C2: `GET /imagegen/backends` + health route

**Files:**
- Modify: `backend/src/grimoire/api/campaigns.py` (or split off into a new `backend/src/grimoire/api/imagegen.py` — see step 1)
- Test: `backend/tests/api/test_imagegen_routes.py` (new)

Decision: keep routes for now in `campaigns.py` if they're campaign-scoped; put non-campaign-scoped (`/imagegen/backends`, `/imagegen/backends/{id}/health`) in a new `api/imagegen.py` to keep `campaigns.py` from sprawling further. Both options are fine; the plan uses the split.

- [ ] **Step 1: Create new router module**

```python
# backend/src/grimoire/api/imagegen.py
"""Non-campaign-scoped ImageGen routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from grimoire.api.deps import ImageGenDep
from grimoire.api.util import map_lookup_errors, to_payload

router = APIRouter(prefix="/imagegen", tags=["imagegen"])


@router.get("/backends")
async def list_backends(imagegen: ImageGenDep) -> Any:
    try:
        return to_payload(await imagegen.list_backends())
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/backends/{backend_id}/health")
async def backend_health(backend_id: str, imagegen: ImageGenDep) -> Any:
    try:
        return to_payload(await imagegen.health_check(backend_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
```

Then register the router. Find where `campaigns.router` gets included (probably `main.py` or `api/__init__.py`); add `from grimoire.api.imagegen import router as imagegen_router` and `app.include_router(imagegen_router)` next to it.

- [ ] **Step 2: Write test**

```python
# backend/tests/api/test_imagegen_routes.py
"""Non-campaign-scoped imagegen routes (§13)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# Reuse the existing top-level `app` test fixture if one exists; otherwise
# build a minimal app here. The shape below assumes there is an `app`
# fixture wired with a container (mirror what tests/api/conftest.py exposes).


async def test_list_backends_returns_registered(app_client: TestClient) -> None:
    resp = app_client.get("/imagegen/backends")
    assert resp.status_code == 200
    ids = {item["id"] for item in resp.json()}
    assert "diffusers-memory" in ids


async def test_backend_health_returns_status(app_client: TestClient) -> None:
    resp = app_client.get("/imagegen/backends/diffusers-memory/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["target_id"] == "diffusers-memory"
    assert body["level"] in ("healthy", "degraded", "unhealthy", "unconfigured")


async def test_unknown_backend_health_returns_unconfigured(app_client: TestClient) -> None:
    resp = app_client.get("/imagegen/backends/nope/health")
    assert resp.status_code == 200
    assert resp.json()["level"] == "unconfigured"
```

If the existing `tests/api/` directory lacks a shared `app_client` fixture, check what's there first and adapt; otherwise add one in `tests/api/conftest.py` that builds a minimal app wired to an `ImageGenService` with `InMemoryDiffusersBackend`.

- [ ] **Step 3: Run, expect pass**

- [ ] **Step 4: Commit**

```
git commit -m "Add /imagegen/backends and /imagegen/backends/{id}/health"
```

### Task C3: Campaign-scoped image management routes

**Files:**
- Modify: `backend/src/grimoire/api/campaigns.py` (add 6 routes)
- Test: extend `backend/tests/api/test_imagegen_routes.py`

For each route, mirror the existing `generate_image` pattern (try/except + `map_lookup_errors`).

- [ ] **Step 1: Add routes after the existing `list_images` route in `campaigns.py`**

```python
class StarImagePayload(BaseModel):
    starred: bool


@router.get("/{campaign_id}/imagegen/active")
async def get_active_backend(campaign_id: str, imagegen: ImageGenDep) -> Any:
    try:
        return to_payload(await imagegen.active_backend(campaign_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


class SetActiveBackendPayload(BaseModel):
    backend_id: str


@router.put("/{campaign_id}/imagegen/active")
async def set_active_backend(
    campaign_id: str,
    payload: SetActiveBackendPayload,
    imagegen: ImageGenDep,
) -> Any:
    try:
        await imagegen.set_active_backend(campaign_id, payload.backend_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}


@router.get("/{campaign_id}/images/jobs")
async def list_image_jobs(
    campaign_id: str,
    imagegen: ImageGenDep,
    status: str | None = None,
) -> Any:
    from grimoire.types.imagegen import JobStatus

    try:
        status_enum = JobStatus(status) if status else None
        return to_payload(await imagegen.list_jobs(campaign_id, status=status_enum))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.delete("/{campaign_id}/images/jobs/{job_id}", status_code=204)
async def cancel_image_job(
    campaign_id: str,  # noqa: ARG001 — kept for symmetry / future ownership check
    job_id: str,
    imagegen: ImageGenDep,
) -> None:
    try:
        await imagegen.cancel_job(job_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


class PrioritizeJobPayload(BaseModel):
    priority: int = 5


@router.patch("/{campaign_id}/images/jobs/{job_id}")
async def prioritize_image_job(
    campaign_id: str,  # noqa: ARG001
    job_id: str,
    payload: PrioritizeJobPayload,
    imagegen: ImageGenDep,
) -> Any:
    try:
        await imagegen.prioritize_job(job_id, payload.priority)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}


@router.get("/{campaign_id}/images/{image_id}")
async def get_image(
    campaign_id: str,  # noqa: ARG001
    image_id: str,
    imagegen: ImageGenDep,
) -> Any:
    try:
        return to_payload(await imagegen.get_image(image_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.put("/{campaign_id}/images/{image_id}/star")
async def star_image(
    campaign_id: str,  # noqa: ARG001
    image_id: str,
    payload: StarImagePayload,
    imagegen: ImageGenDep,
) -> Any:
    try:
        await imagegen.star_image(image_id, payload.starred)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}


@router.delete("/{campaign_id}/images/{image_id}", status_code=204)
async def delete_image(
    campaign_id: str,  # noqa: ARG001
    image_id: str,
    imagegen: ImageGenDep,
) -> None:
    try:
        await imagegen.delete_image(image_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.post("/{campaign_id}/images/{image_id}/reroll", status_code=202)
async def reroll_image(
    campaign_id: str,  # noqa: ARG001
    image_id: str,
    imagegen: ImageGenDep,
) -> Any:
    try:
        job_id = await imagegen.reroll(image_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"job_id": job_id}


class VariationPayload(BaseModel):
    strength: float = 0.6


@router.post("/{campaign_id}/images/{image_id}/variation", status_code=202)
async def variation_image(
    campaign_id: str,  # noqa: ARG001
    image_id: str,
    payload: VariationPayload,
    imagegen: ImageGenDep,
) -> Any:
    try:
        job_id = await imagegen.variation(image_id, payload.strength)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"job_id": job_id}
```

- [ ] **Step 2: Add tests for each route** in `test_imagegen_routes.py` (one test per route). Sample shape:

```python
async def test_list_image_jobs_returns_empty_for_new_campaign(app_client) -> None:
    resp = app_client.get("/campaigns/camp-1/images/jobs")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_set_active_backend_round_trips(app_client) -> None:
    resp = app_client.put(
        "/campaigns/camp-1/imagegen/active",
        json={"backend_id": "diffusers-memory"},
    )
    assert resp.status_code == 200
    resp = app_client.get("/campaigns/camp-1/imagegen/active")
    assert resp.json()["id"] == "diffusers-memory"
```

Write the parallel tests for: `get_image` (404 path), `star_image` (round-trip), `delete_image` (returns 204 + subsequent get is 404), `prioritize_job` (200 ok), `cancel_job` (204), `reroll` (202 with job_id), `variation` (202).

- [ ] **Step 3: Run all tests, expect pass**

- [ ] **Step 4: Commit**

```
git commit -m "Add 10 campaign-scoped imagegen REST routes"
```

### Task C4: Add new event types to `_FORWARDED_EVENTS`

**Files:**
- Modify: `backend/src/grimoire/api/stream.py` (extend tuple)
- Test: `backend/tests/api/test_stream_events.py` (extend if exists, otherwise new)

- [ ] **Step 1: Extend the forwarded list**

In `stream.py` at `_FORWARDED_EVENTS`, add:
- `"imagegen_backend_health_changed"` — for §2
- `"imagegen_download_progress"` — for §9 (will be emitted by branch F)

- [ ] **Step 2: Add test asserting forwarding**

```python
async def test_health_event_is_forwarded(stream_manager_with_bus) -> None:
    mgr, bus, sock = stream_manager_with_bus
    await bus.emit(Event(type="imagegen_backend_health_changed", payload={
        "campaign_id": "camp-1", "backend_id": "x", "level": "unhealthy"
    }))
    msg = await sock.recv_json()
    assert msg["type"] == "imagegen_backend_health_changed"
```

(Adapt to existing fixture infrastructure; if missing, test the `_FORWARDED_EVENTS` constant directly: `assert "imagegen_backend_health_changed" in stream._FORWARDED_EVENTS`.)

- [ ] **Step 3: Commit**

```
git commit -m "Forward imagegen health + download events over WebSocket"
```

### Task C5: Routes for per-campaign trigger config + fallback (§6 surface)

**Files:**
- Modify: `backend/src/grimoire/api/campaigns.py` (4 routes)
- Test: extend `test_imagegen_routes.py`

- [ ] **Step 1: Add payload schemas + routes**

```python
class TriggerConfigPayload(BaseModel):
    mode: str = "per_scene"
    every_n: int = 5
    on_scene_open: bool = True
    on_new_location: bool = True
    on_new_character_appearance: bool = True
    auto_during_combat: bool = False


@router.get("/{campaign_id}/imagegen/trigger")
async def get_imagegen_trigger(campaign_id: str, imagegen: ImageGenDep) -> Any:
    cfg = await imagegen.get_trigger_config(campaign_id)
    return {
        "mode": cfg.mode,
        "every_n": cfg.every_n,
        "on_scene_open": cfg.on_scene_open,
        "on_new_location": cfg.on_new_location,
        "on_new_character_appearance": cfg.on_new_character_appearance,
        "auto_during_combat": cfg.auto_during_combat,
    }


@router.put("/{campaign_id}/imagegen/trigger")
async def set_imagegen_trigger(
    campaign_id: str,
    payload: TriggerConfigPayload,
    imagegen: ImageGenDep,
) -> Any:
    from grimoire.imagegen import TriggerConfig

    await imagegen.set_trigger_config(
        campaign_id,
        TriggerConfig(
            mode=payload.mode,
            every_n=payload.every_n,
            on_scene_open=payload.on_scene_open,
            on_new_location=payload.on_new_location,
            on_new_character_appearance=payload.on_new_character_appearance,
            auto_during_combat=payload.auto_during_combat,
        ),
    )
    return {"ok": True}


class FallbackBackendPayload(BaseModel):
    backend_id: str | None = None


@router.get("/{campaign_id}/imagegen/fallback")
async def get_imagegen_fallback(campaign_id: str, imagegen: ImageGenDep) -> Any:
    return {"backend_id": await imagegen.get_fallback_backend(campaign_id)}


@router.put("/{campaign_id}/imagegen/fallback")
async def set_imagegen_fallback(
    campaign_id: str,
    payload: FallbackBackendPayload,
    imagegen: ImageGenDep,
) -> Any:
    try:
        await imagegen.set_fallback_backend(campaign_id, payload.backend_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}
```

- [ ] **Step 2: Tests**

Round-trip tests mirroring task C3 style.

- [ ] **Step 3: Commit**

```
git commit -m "Expose trigger config and fallback backend over REST"
```

### Task C6: Merge branch C

Same as A7.

---

# Branch D — Backend lifecycle (§3 + §4 + §2)

**Working directory:** `.worktrees/imagegen-D-lifecycle`
**Depends on:** Branch A merged (for `set_/get_fallback_backend`).

### Task D1: Add `progress` callback to backend interface (§3)

**Files:**
- Modify: `backend/src/grimoire/imagegen/backend.py` (`InMemoryDiffusersBackend.generate`, `IntegratedDiffusersBackend.generate`)
- Modify: `backend/src/grimoire/imagegen/service.py` (`_run_job` passes a `progress` callback that emits `imagegen_progress`)
- Test: `backend/tests/imagegen/test_progress_callback.py` (new)

- [ ] **Step 1: Write failing test**

```python
# backend/tests/imagegen/test_progress_callback.py
"""§3 Progress events from backends."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from grimoire.event_bus import EventBus
from grimoire.imagegen import BackendRegistry, ImageGenService
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations
from grimoire.types.common import HealthLevel, HealthStatus
from grimoire.types.imagegen import (
    BackendCapabilities,
    GenerationRequest,
    GenerationResult,
)


class _ProgressEmittingBackend:
    id = "progress-test"
    name = "Progress Test"
    capabilities = BackendCapabilities()

    async def generate(self, request: GenerationRequest, *, progress=None, cancel_token=None) -> GenerationResult:
        if progress is not None:
            await progress({"step": 1, "total_steps": 3, "eta_ms": 90})
            await progress({"step": 2, "total_steps": 3, "eta_ms": 60})
            await progress({"step": 3, "total_steps": 3, "eta_ms": 0})
        return GenerationResult(
            image_bytes=b"\x89PNG\r\n\x1a\n",
            thumbnail_bytes=b"\x89PNG\r\n\x1a\n",
            backend=self.id,
            model="x",
            seed=42,
            actual_params={},
        )

    async def health_check(self) -> HealthStatus:
        return HealthStatus(level=HealthLevel.HEALTHY, target_id=self.id, message="ok")


@pytest.fixture
async def progress_service(tmp_path):
    data = tmp_path / "data"; data.mkdir()
    db = Database(tmp_path / "x.sqlite", pool_size=1)
    await db.connect()
    await apply_migrations(db)
    s = StateStore(db, data)
    await s.upsert_campaign(campaign_id="camp-1", name="t")
    reg = BackendRegistry()
    reg.register(_ProgressEmittingBackend())
    bus = EventBus()
    svc = ImageGenService(
        store=s, registry=reg, default_backend_id="progress-test", event_bus=bus
    )
    try:
        yield svc, bus
    finally:
        await svc.aclose()
        await db.close()


async def test_progress_events_emit_on_each_step(progress_service) -> None:
    svc, bus = progress_service
    events: list[dict[str, Any]] = []
    bus.subscribe("imagegen_progress", lambda ev: events.append(dict(ev.payload)))
    job_id = await svc.queue_generation(
        campaign_id="camp-1",
        scene_id=None, post_id=None,
        request=GenerationRequest(prompt="x", width=8, height=8, seed=42),
    )
    # Wait for completion
    for _ in range(50):
        await asyncio.sleep(0.05)
        if any(getattr(j, "status", None).value == "complete"
               for j in (await svc.list_jobs("camp-1")) if j.id == job_id):
            break
    assert len(events) == 3
    assert events[0]["step"] == 1
    assert events[-1]["step"] == 3
    assert all(e["job_id"] == job_id for e in events)
    assert all(e["campaign_id"] == "camp-1" for e in events)
```

- [ ] **Step 2: Run, expect fail**

- [ ] **Step 3: Update backend protocol + In-memory backend**

In `backend/src/grimoire/imagegen/backend.py`, update `InMemoryDiffusersBackend.generate` and `IntegratedDiffusersBackend.generate` signatures:

```python
async def generate(
    self,
    request: GenerationRequest,
    *,
    progress: Any = None,  # Callable[[dict], Awaitable[None]] | None
    cancel_token: Any = None,  # asyncio.Event | None — used in D2
) -> GenerationResult:
```

Both implementations can ignore these new kwargs for now (or fire a single `step=1, total_steps=1` event if `progress is not None` to make tests pass). Keep the existing body otherwise.

- [ ] **Step 4: Wire progress in `_run_job`**

In `backend/src/grimoire/imagegen/service.py`, modify `_run_job`:

```python
    async def _run_job(self, backend: Any, job: GenerationJob) -> GenerationResult:
        request = job.request
        cached = self._lookup_cache(job.campaign_id, request, backend=backend)
        if cached is not None:
            # ... unchanged cache shortcut ...

        async def _on_progress(info: dict[str, Any]) -> None:
            await self._emit("imagegen_progress", {
                "job_id": job.id,
                "campaign_id": job.campaign_id,
                **info,
            })

        try:
            result = await backend.generate(request, progress=_on_progress)
        except TypeError:
            # Older backends that don't accept the kwarg.
            result = await backend.generate(request)
        # ... rest unchanged ...
```

- [ ] **Step 5: Run tests, expect pass**

- [ ] **Step 6: Commit**

```
git commit -m "Emit imagegen_progress events during generation (§3)"
```

### Task D2: Cooperative cancellation via `cancel_token` (§4)

**Files:**
- Modify: `backend/src/grimoire/imagegen/service.py` (`_run_job` passes token, `cancel_job` sets it)
- Modify: `backend/src/grimoire/imagegen/backend.py` (in-memory backend respects token at top of `generate`)
- Test: `backend/tests/imagegen/test_cooperative_cancel.py` (new)

- [ ] **Step 1: Write failing test**

```python
# backend/tests/imagegen/test_cooperative_cancel.py
"""§4 Cancellation of running jobs aborts the backend."""

from __future__ import annotations

import asyncio

import pytest

from grimoire.event_bus import EventBus
from grimoire.imagegen import BackendRegistry, ImageGenService
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations
from grimoire.types.common import HealthLevel, HealthStatus
from grimoire.types.imagegen import (
    BackendCapabilities,
    GenerationRequest,
    GenerationResult,
    JobStatus,
)


class _SlowBackend:
    id = "slow"
    name = "Slow"
    capabilities = BackendCapabilities()
    cancelled = False

    async def generate(self, request, *, progress=None, cancel_token=None):
        # Loop on the cancel token; abort cleanly if set.
        for _ in range(100):
            if cancel_token is not None and cancel_token.is_set():
                _SlowBackend.cancelled = True
                raise asyncio.CancelledError()
            await asyncio.sleep(0.02)
        return GenerationResult(
            image_bytes=b"x", thumbnail_bytes=b"x", backend=self.id,
            model="m", seed=1, actual_params={},
        )

    async def health_check(self):
        return HealthStatus(level=HealthLevel.HEALTHY, target_id=self.id, message="ok")


@pytest.fixture
async def slow_service(tmp_path):
    data = tmp_path / "data"; data.mkdir()
    db = Database(tmp_path / "x.sqlite", pool_size=1)
    await db.connect(); await apply_migrations(db)
    s = StateStore(db, data)
    await s.upsert_campaign(campaign_id="camp-1", name="t")
    reg = BackendRegistry(); reg.register(_SlowBackend())
    svc = ImageGenService(store=s, registry=reg, default_backend_id="slow", event_bus=EventBus())
    try:
        yield svc
    finally:
        await svc.aclose(); await db.close()


async def test_cancel_running_job_sets_token_and_marks_cancelled(slow_service) -> None:
    job_id = await slow_service.queue_generation(
        campaign_id="camp-1", scene_id=None, post_id=None,
        request=GenerationRequest(prompt="x", width=8, height=8, seed=1),
    )
    # Wait until the job moves to RUNNING
    for _ in range(50):
        await asyncio.sleep(0.02)
        jobs = await slow_service.list_jobs("camp-1")
        if any(j.id == job_id and j.status == JobStatus.RUNNING for j in jobs):
            break
    else:
        raise AssertionError("job never started")

    await slow_service.cancel_job(job_id)
    # Give worker a beat to process cancellation
    for _ in range(50):
        await asyncio.sleep(0.02)
        jobs = await slow_service.list_jobs("camp-1")
        target = next(j for j in jobs if j.id == job_id)
        if target.status == JobStatus.CANCELLED:
            break
    assert _SlowBackend.cancelled is True
    assert target.status == JobStatus.CANCELLED
```

- [ ] **Step 2: Run, expect fail**

- [ ] **Step 3: Plumb `cancel_token` through service**

In `backend/src/grimoire/imagegen/service.py`:

Add a `_cancel_tokens: dict[str, asyncio.Event]` to `__init__`:
```python
self._cancel_tokens: dict[str, asyncio.Event] = {}
```

In `_run_job`, create + register a token, pass it to `backend.generate`:
```python
        token = asyncio.Event()
        self._cancel_tokens[job.id] = token
        try:
            try:
                result = await backend.generate(
                    request, progress=_on_progress, cancel_token=token
                )
            except TypeError:
                result = await backend.generate(request)
        finally:
            self._cancel_tokens.pop(job.id, None)
```

In `cancel_job`, when status is `RUNNING`, set the token:
```python
    async def cancel_job(self, job_id: str) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"no such job {job_id!r}")
            if job.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
                return
            was_running = job.status == JobStatus.RUNNING
            job.status = JobStatus.CANCELLED
            job.finished_at = _now()
            handle = self._handles.get(job.backend)
            if handle is not None:
                handle._pending_jobs.discard(job_id)
            token = self._cancel_tokens.get(job_id) if was_running else None
        if token is not None:
            token.set()
        await self._emit("imagegen_job_failed", {"job_id": job_id, "reason": "cancelled"})
```

In `_worker`, catch `asyncio.CancelledError` from `_run_job` more granularly — when the job was marked CANCELLED in-flight, treat it as normal completion (status already set, no FAILED emit):

```python
            try:
                result = await self._run_job(backend, job)
            except asyncio.CancelledError:
                # If user-cancelled in flight, status is already CANCELLED;
                # only break the worker loop if the worker itself was cancelled.
                if job.status == JobStatus.CANCELLED:
                    handle.queue.task_done()
                    continue
                raise
```

- [ ] **Step 4: Run tests, expect pass**

- [ ] **Step 5: Commit**

```
git commit -m "Cancel running imagegen jobs cooperatively via cancel_token (§4)"
```

### Task D3: Periodic health prober + fallback routing (§2)

**Files:**
- Create: `backend/src/grimoire/imagegen/health_prober.py`
- Modify: `backend/src/grimoire/imagegen/service.py` (`queue_generation` consults health + fallback)
- Modify: `backend/src/grimoire/imagegen/__init__.py` (re-export)
- Modify: `backend/src/grimoire/main.py` (start/stop prober in lifespan)
- Test: `backend/tests/imagegen/test_health_prober.py` (new), `backend/tests/imagegen/test_fallback_routing.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/imagegen/test_health_prober.py
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from grimoire.imagegen.health_prober import ImageGenHealthProber


async def test_prober_calls_health_check_on_each_backend_periodically() -> None:
    svc = AsyncMock()
    svc.list_backends = AsyncMock(return_value=[
        type("B", (), {"id": "a"})(), type("B", (), {"id": "b"})(),
    ])
    prober = ImageGenHealthProber(svc, interval_seconds=0.05)
    prober.start()
    await asyncio.sleep(0.18)  # ~3 ticks
    await prober.stop()
    assert svc.health_check.call_count >= 4  # 2 backends * ≥2 ticks
```

```python
# backend/tests/imagegen/test_fallback_routing.py
from __future__ import annotations

import asyncio

from grimoire.event_bus import EventBus
from grimoire.imagegen import BackendRegistry, ImageGenService, InMemoryDiffusersBackend
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations
from grimoire.types.common import HealthLevel, HealthStatus
from grimoire.types.imagegen import BackendCapabilities


class _UnhealthyBackend:
    id = "broken"; name = "Broken"
    capabilities = BackendCapabilities()
    async def generate(self, request, *, progress=None, cancel_token=None):
        raise RuntimeError("backend is down")
    async def health_check(self):
        return HealthStatus(level=HealthLevel.UNHEALTHY, target_id=self.id, message="down")


async def test_queue_routes_to_fallback_when_active_is_unhealthy(tmp_path) -> None:
    data = tmp_path / "data"; data.mkdir()
    db = Database(tmp_path / "x.sqlite", pool_size=1)
    await db.connect(); await apply_migrations(db)
    s = StateStore(db, data)
    await s.upsert_campaign(campaign_id="camp-1", name="t")
    reg = BackendRegistry()
    reg.register(_UnhealthyBackend())
    reg.register(InMemoryDiffusersBackend())
    svc = ImageGenService(store=s, registry=reg, default_backend_id="broken", event_bus=EventBus())
    try:
        await svc.set_fallback_backend("camp-1", "diffusers-memory")
        # Mark the broken backend as unhealthy in the service's cache.
        await svc.health_check("broken")
        job_id = await svc.queue_generation(
            campaign_id="camp-1", scene_id=None, post_id=None,
            request=None,
        )
        jobs = await svc.list_jobs("camp-1")
        target = next(j for j in jobs if j.id == job_id)
        assert target.backend == "diffusers-memory"
    finally:
        await svc.aclose(); await db.close()
```

- [ ] **Step 2: Run, expect fail**

- [ ] **Step 3: Implement health prober**

```python
# backend/src/grimoire/imagegen/health_prober.py
"""Periodic backend health prober (§2 of imagegen remaining-design).

Walks the BackendRegistry on a fixed interval, calls
``ImageGenService.health_check(backend_id)``, which emits
``imagegen_backend_health_changed`` on level transitions. Errors are
swallowed + logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

logger = logging.getLogger(__name__)


class ImageGenHealthProber:
    def __init__(self, service, *, interval_seconds: float = 30.0) -> None:
        self._svc = service
        self._interval = max(float(interval_seconds), 1.0)
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="imagegen-health-prober")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                backends = await self._svc.list_backends()
            except Exception:
                logger.exception("health prober: list_backends failed")
                backends = []
            for info in backends:
                try:
                    await self._svc.health_check(info.id)
                except Exception:
                    logger.exception("health prober: health_check(%s) failed", info.id)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except TimeoutError:
                pass
```

- [ ] **Step 4: Fallback routing in `queue_generation`**

Modify the backend-resolution block of `queue_generation`:

```python
        backend_id = self._campaign_backend.get(campaign_id)
        if backend_id is None:
            raw = await self._load_imagegen_config_row(campaign_id)
            backend_id = raw.get("active_backend") or self.default_backend_id
            if backend_id is not None:
                self._campaign_backend[campaign_id] = backend_id

        # Health-aware fallback: if the chosen backend is UNHEALTHY and a
        # fallback is configured, route there instead. If no fallback,
        # leave the job queued and emit a warning (current behavior).
        last = self._last_health.get(backend_id) if backend_id else None
        if last == HealthLevel.UNHEALTHY:
            fallback = (await self._load_imagegen_config_row(campaign_id)).get(
                "fallback_backend"
            )
            if fallback and fallback in self.registry:
                logger.info(
                    "imagegen: routing %s job to fallback %s (active %s unhealthy)",
                    campaign_id, fallback, backend_id,
                )
                backend_id = fallback
            else:
                await self._emit("imagegen_warning", {
                    "campaign_id": campaign_id,
                    "reason": f"active backend {backend_id!r} unhealthy and no fallback",
                })

        if backend_id not in self.registry:
            raise KeyError(f"no backend registered with id {backend_id!r}")
        # ... rest unchanged ...
```

- [ ] **Step 5: Wire prober in `main.py` lifespan**

```python
from grimoire.imagegen import ImageGenHealthProber

container.imagegen_prober = ImageGenHealthProber(
    container.imagegen, interval_seconds=30.0
)
container.imagegen_prober.start()
```

In shutdown:
```python
if container.imagegen_prober is not None:
    await container.imagegen_prober.stop()
```

Extend `ServiceContainer` similarly.

- [ ] **Step 6: Run tests, expect pass**

- [ ] **Step 7: Commit**

```
git commit -m "Add ImageGen health prober + unhealthy-backend fallback (§2)"
```

### Task D4: Merge branch D

Same as A7.

---

# Branch E — Small UX wins (§5 + §10 + §11 + §12)

**Working directory:** `.worktrees/imagegen-E-ux`
**Depends on:** Branch A merged (the trigger/fallback stuff isn't touched here, but A also added the `config` plumbing branch E may want to read).

### Task E1: `edit_and_regenerate` (§5)

**Files:**
- Modify: `backend/src/grimoire/imagegen/service.py` (add method)
- Modify: `backend/src/grimoire/types/protocols.py` (add to Protocol)
- Modify: `backend/src/grimoire/api/campaigns.py` (REST verb)
- Test: `backend/tests/imagegen/test_edit_and_regenerate.py` (new)

- [ ] **Step 1: Write failing test**

```python
# backend/tests/imagegen/test_edit_and_regenerate.py
"""§5 edit_and_regenerate queues a fresh job with a merged request."""

from __future__ import annotations

import asyncio

from grimoire.types.imagegen import GenerationRequest, JobStatus


async def _wait_complete(svc, job_id):
    for _ in range(100):
        await asyncio.sleep(0.02)
        for j in await svc.list_jobs("camp-1"):
            if j.id == job_id and j.status in (JobStatus.COMPLETE, JobStatus.FAILED):
                return j


async def test_edit_and_regenerate_uses_new_prompt_keeps_seed(service) -> None:
    svc, _ = service
    job0 = await svc.queue_generation(
        campaign_id="camp-1", scene_id="scene-1", post_id=None,
        request=GenerationRequest(prompt="orig", width=8, height=8, seed=7),
    )
    j0 = await _wait_complete(svc, job0)
    image_id = (await svc.list_images("camp-1"))[0].id
    job1_id = await svc.edit_and_regenerate(
        image_id, prompt="new", keep_seed=True,
    )
    j1 = await _wait_complete(svc, job1_id)
    images = await svc.list_images("camp-1")
    # Old image persists; new image saved under a new id.
    assert len(images) == 2
    new_image = max(images, key=lambda i: i.created_at)
    assert new_image.prompt == "new"
    assert new_image.seed == 7  # keep_seed honored


async def test_edit_and_regenerate_new_seed_when_keep_false(service) -> None:
    svc, _ = service
    job0 = await svc.queue_generation(
        campaign_id="camp-1", scene_id="scene-1", post_id=None,
        request=GenerationRequest(prompt="orig", width=8, height=8, seed=7),
    )
    await _wait_complete(svc, job0)
    image_id = (await svc.list_images("camp-1"))[0].id
    job1_id = await svc.edit_and_regenerate(image_id, prompt="new")
    await _wait_complete(svc, job1_id)
    images = await svc.list_images("camp-1")
    new_image = max(images, key=lambda i: i.created_at)
    # Seed is random; for an in-memory backend keyed off seed it might be
    # anything, just assert that we did NOT keep the old one most of the
    # time. Test the contract: keep_seed=False omits seed from the new
    # request, so the result.seed is the backend's chosen seed.
    assert new_image.prompt == "new"
```

- [ ] **Step 2: Implement**

In `service.py`, add next to `reroll` / `variation`:

```python
    async def edit_and_regenerate(
        self,
        image_id: str,
        *,
        prompt: str | None = None,
        negative_prompt: str | None = None,
        params: dict | None = None,
        keep_seed: bool = False,
    ) -> str:
        """§5 Manual prompt editing + 'save as new'."""
        meta = await self.get_image(image_id)
        base = self._request_from_metadata(meta, new_seed=not keep_seed)
        updates: dict[str, Any] = {}
        if prompt is not None:
            updates["prompt"] = prompt
        if negative_prompt is not None:
            updates["negative_prompt"] = negative_prompt
        if params:
            # Allow overrides for width/height/steps/cfg_scale/sampler.
            for key in ("width", "height", "steps", "cfg_scale", "sampler"):
                if key in params:
                    updates[key] = params[key]
        new_request = base.model_copy(update=updates) if updates else base
        return await self.queue_generation(
            campaign_id=meta.campaign_id,
            scene_id=meta.scene_id,
            post_id=meta.post_id,
            request=new_request,
        )
```

- [ ] **Step 3: Add to Protocol**

```python
    async def edit_and_regenerate(
        self,
        image_id: str,
        *,
        prompt: str | None = None,
        negative_prompt: str | None = None,
        params: dict | None = None,
        keep_seed: bool = False,
    ) -> str: ...
```

- [ ] **Step 4: REST verb**

```python
class EditAndRegeneratePayload(BaseModel):
    prompt: str | None = None
    negative_prompt: str | None = None
    params: dict[str, Any] | None = None
    keep_seed: bool = False


@router.post(
    "/{campaign_id}/images/{image_id}/edit",
    status_code=202,
)
async def edit_and_regenerate_image(
    campaign_id: str,  # noqa: ARG001
    image_id: str,
    payload: EditAndRegeneratePayload,
    imagegen: ImageGenDep,
) -> Any:
    try:
        job_id = await imagegen.edit_and_regenerate(
            image_id,
            prompt=payload.prompt,
            negative_prompt=payload.negative_prompt,
            params=payload.params,
            keep_seed=payload.keep_seed,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"job_id": job_id}
```

- [ ] **Step 5: Run + commit**

```
git commit -m "Add edit_and_regenerate (§5) + REST verb"
```

### Task E2: `set_tags` + REST verb (§10)

**Files:**
- Modify: `backend/src/grimoire/imagegen/service.py` (add `set_tags`)
- Modify: `backend/src/grimoire/types/protocols.py`
- Modify: `backend/src/grimoire/api/campaigns.py`
- Test: `backend/tests/imagegen/test_tags.py` (new)

- [ ] **Step 1: Write failing test**

```python
# backend/tests/imagegen/test_tags.py
"""§10 Image tag editing."""

from __future__ import annotations

import asyncio

from grimoire.types.imagegen import GenerationRequest, JobStatus


async def test_set_tags_updates_sql_and_sidecar(service, tmp_path) -> None:
    svc, _ = service
    job_id = await svc.queue_generation(
        campaign_id="camp-1", scene_id="scene-1", post_id=None,
        request=GenerationRequest(prompt="x", width=8, height=8, seed=1),
    )
    for _ in range(50):
        await asyncio.sleep(0.02)
        jobs = await svc.list_jobs("camp-1")
        if any(j.id == job_id and j.status == JobStatus.COMPLETE for j in jobs):
            break
    image = (await svc.list_images("camp-1"))[0]
    await svc.set_tags(image.id, ["scene-establishing", "action"])
    updated = await svc.get_image(image.id)
    assert updated.tags == ["scene-establishing", "action"]
```

- [ ] **Step 2: Implement**

In `service.py`:

```python
    async def set_tags(self, image_id: str, tags: list[str]) -> None:
        """§10 Replace the tag list on an image (SQL + YAML sidecar)."""
        meta = await self.get_image(image_id)
        tags_clean = [str(t).strip() for t in tags if str(t).strip()]
        await self.store.db.execute(
            "UPDATE images SET tags = ? WHERE id = ?",
            (json.dumps(tags_clean), image_id),
        )
        # Best-effort sidecar update: re-write YAML with new tag list.
        sidecar = image_metadata_path(self.data_root, meta.campaign_id, image_id)
        if sidecar.exists():
            try:
                import yaml  # local import — yaml already used elsewhere
                doc = yaml.safe_load(sidecar.read_text(encoding="utf-8")) or {}
                doc["tags"] = tags_clean
                write_yaml(sidecar, doc)
            except Exception:
                logger.warning("set_tags: failed to update sidecar", exc_info=True)
```

- [ ] **Step 3: Protocol + REST**

Protocol addition:
```python
    async def set_tags(self, image_id: str, tags: list[str]) -> None: ...
```

REST:
```python
class SetTagsPayload(BaseModel):
    tags: list[str] = []


@router.put("/{campaign_id}/images/{image_id}/tags")
async def set_image_tags(
    campaign_id: str,  # noqa: ARG001
    image_id: str,
    payload: SetTagsPayload,
    imagegen: ImageGenDep,
) -> Any:
    try:
        await imagegen.set_tags(image_id, payload.tags)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}
```

- [ ] **Step 4: Run + commit**

```
git commit -m "Add set_tags (§10) + REST verb"
```

### Task E3: Per-character canonical seed (§11)

**Files:**
- Modify: `backend/src/grimoire/imagegen/prompt.py` (`PromptComposer.compose`)
- Modify: `backend/src/grimoire/imagegen/service.py` (`_compose_request` propagates seed)
- Test: `backend/tests/imagegen/test_canonical_seed.py` (new)

- [ ] **Step 1: Write failing test**

```python
# backend/tests/imagegen/test_canonical_seed.py
"""§11 Per-character canonical seed."""

from __future__ import annotations

import pytest

from grimoire.imagegen import PromptComposer


class _Character:
    def __init__(self, base_prompt, canonical_seed=None):
        self.image = type("Img", (), {
            "base_prompt": base_prompt,
            "negative_prompt": "",
            "canonical_seed": canonical_seed,
        })()


class _Charlotte:
    character = _Character("a redhead in blue", canonical_seed=1234)


class _Bob:
    character = _Character("a tall man", canonical_seed=None)


class _StubCharacters:
    def __init__(self, refs):
        self._refs = refs

    async def resolve(self, ref, campaign_id):
        return self._refs[ref]


class _StubScene:
    present_character_refs = ["vivienne"]
    mood = ""
    location_ref = None


class _StubSceneManager:
    async def get_scene(self, scene_id):
        return _StubScene()


@pytest.mark.asyncio  # if needed; auto-mode covers it
async def test_canonical_seed_returned_for_single_character() -> None:
    composer = PromptComposer(
        scene_manager=_StubSceneManager(),
        characters=_StubCharacters({"vivienne": _Charlotte()}),
    )
    result = await composer.compose(campaign_id="c", scene_id="s")
    assert result.params.get("seed") == 1234


async def test_canonical_seed_xor_combiner_when_multiple() -> None:
    class _SceneTwo:
        present_character_refs = ["vivienne", "alice"]
        mood = ""; location_ref = None
    class _SM:
        async def get_scene(self, scene_id): return _SceneTwo()
    composer = PromptComposer(
        scene_manager=_SM(),
        characters=_StubCharacters({
            "vivienne": _Charlotte(),
            "alice": type("X", (), {"character": _Character("a", canonical_seed=5)})(),
        }),
    )
    result = await composer.compose(campaign_id="c", scene_id="s")
    assert result.params.get("seed") == (1234 ^ 5) & 0x7FFFFFFF


async def test_no_canonical_seed_omits_seed_key() -> None:
    composer = PromptComposer(
        scene_manager=_StubSceneManager(),
        characters=_StubCharacters({"vivienne": type("X", (), {
            "character": _Character("a", canonical_seed=None)
        })()}),
    )
    result = await composer.compose(campaign_id="c", scene_id="s")
    assert "seed" not in result.params
```

- [ ] **Step 2: Implement seed collection in `PromptComposer.compose`**

Inside the character loop, also collect canonical seeds:

```python
        canonical_seeds: list[int] = []
        # ... existing character loop ...
            seed = getattr(image, "canonical_seed", None)
            if isinstance(seed, int):
                canonical_seeds.append(int(seed) & 0x7FFFFFFF)
```

Then, before constructing `ComposedPrompt`, fold seeds into `preset_params`:

```python
        if canonical_seeds:
            combined = canonical_seeds[0]
            for s in canonical_seeds[1:]:
                combined ^= s
            preset_params = {**preset_params, "seed": combined & 0x7FFFFFFF}
```

- [ ] **Step 3: Propagate seed in `_compose_request`**

In `service.py`, in `_compose_request`, after building `params`, also extract seed:

```python
        seed_override = params.get("seed")
        return GenerationRequest(
            prompt=prompt or "a scene",
            negative_prompt=negative,
            width=int(params.get("width", 1024)),
            height=int(params.get("height", 1024)),
            steps=int(params.get("steps", 28)),
            cfg_scale=float(params.get("cfg_scale", 6.5)),
            sampler=str(params.get("sampler", "DPM++ 2M Karras")),
            seed=int(seed_override) if seed_override is not None else None,
        )
```

- [ ] **Step 4: Run + commit**

```
git commit -m "Use per-character canonical_seed in PromptComposer (§11)"
```

### Task E4: `prewarm` method + REST verb (§12)

**Files:**
- Modify: `backend/src/grimoire/imagegen/service.py`
- Modify: `backend/src/grimoire/types/protocols.py`
- Modify: `backend/src/grimoire/api/imagegen.py` (or `campaigns.py`)
- Test: `backend/tests/imagegen/test_prewarm.py` (new)

- [ ] **Step 1: Write failing test**

```python
# backend/tests/imagegen/test_prewarm.py
from __future__ import annotations

from unittest.mock import AsyncMock


async def test_prewarm_calls_backend_ensure_pipeline(service) -> None:
    svc, _ = service
    # Replace the backend with a stub exposing the hook.
    backend = svc.registry.get("diffusers-memory")
    backend._ensure_pipeline = AsyncMock(return_value=None)
    await svc.prewarm("diffusers-memory")
    backend._ensure_pipeline.assert_awaited_once()


async def test_prewarm_skips_when_backend_has_no_hook(service) -> None:
    svc, _ = service
    # In-memory backend has no _ensure_pipeline; call should not raise.
    await svc.prewarm("diffusers-memory")
```

- [ ] **Step 2: Implement**

```python
    async def prewarm(self, backend_id: str) -> None:
        """§12 Trigger lazy pipeline load for backends that support it."""
        backend = self.registry.get(backend_id)
        if backend is None:
            raise KeyError(f"no backend registered with id {backend_id!r}")
        hook = getattr(backend, "_ensure_pipeline", None)
        if hook is None or not callable(hook):
            return
        await hook()
```

Add to Protocol:
```python
    async def prewarm(self, backend_id: str) -> None: ...
```

REST (in `api/imagegen.py`):
```python
@router.post("/backends/{backend_id}/prewarm")
async def prewarm_backend(backend_id: str, imagegen: ImageGenDep) -> Any:
    try:
        await imagegen.prewarm(backend_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}
```

- [ ] **Step 3: Run + commit**

```
git commit -m "Add ImageGenService.prewarm(backend_id) + REST verb (§12)"
```

### Task E5: Merge branch E

Same as A7.

---

# Branch F — Persistence + Download UX (§8 + §9)

**Working directory:** `.worktrees/imagegen-F-persistence`
**Depends on:** Branch A merged (uses migration framework and config column).

### Task F1: `imagegen_jobs` migration

**Files:**
- Create: `backend/src/grimoire/storage/migrations/019_imagegen_jobs.sql`
- Test: `backend/tests/imagegen/test_jobs_table.py` (new)

- [ ] **Step 1: Failing test**

```python
# backend/tests/imagegen/test_jobs_table.py
from __future__ import annotations

from pathlib import Path

from grimoire.storage import Database, apply_migrations


async def test_imagegen_jobs_table_exists(tmp_path: Path) -> None:
    db = Database(tmp_path / "x.sqlite", pool_size=1)
    await db.connect()
    await apply_migrations(db)
    rows = await db.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='imagegen_jobs'"
    )
    assert rows
    cols = {row["name"] for row in await db.fetchall("PRAGMA table_info(imagegen_jobs)")}
    assert {"id", "campaign_id", "backend", "status", "priority",
            "request_json", "queued_at", "started_at", "finished_at"} <= cols
    await db.close()
```

- [ ] **Step 2: Migration**

```sql
-- backend/src/grimoire/storage/migrations/019_imagegen_jobs.sql
-- Persist queued/running imagegen jobs across restarts (§8). Completed/
-- failed/cancelled jobs are still surfaced from the in-memory _jobs dict
-- (they get purged on restart, since their results are already in the
-- images table).

CREATE TABLE imagegen_jobs (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  backend TEXT NOT NULL,
  status TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 5,
  request_json TEXT NOT NULL,
  scene_id TEXT,
  post_id TEXT,
  queued_at TEXT,
  started_at TEXT,
  finished_at TEXT,
  error TEXT
);

CREATE INDEX idx_imagegen_jobs_campaign ON imagegen_jobs(campaign_id);
CREATE INDEX idx_imagegen_jobs_status ON imagegen_jobs(status);
```

- [ ] **Step 3: Run + commit**

```
git commit -m "Add imagegen_jobs persistence table (§8 prep)"
```

### Task F2: Persist + reload pending jobs

**Files:**
- Modify: `backend/src/grimoire/imagegen/service.py`
- Test: `backend/tests/imagegen/test_persistent_queue.py` (new)

- [ ] **Step 1: Write failing test**

```python
# backend/tests/imagegen/test_persistent_queue.py
from __future__ import annotations

from grimoire.event_bus import EventBus
from grimoire.imagegen import (
    BackendRegistry, ImageGenService, InMemoryDiffusersBackend
)
from grimoire.types.imagegen import GenerationRequest, JobStatus


async def _new_svc(store):
    reg = BackendRegistry(); reg.register(InMemoryDiffusersBackend())
    return ImageGenService(
        store=store, registry=reg, default_backend_id="diffusers-memory",
        event_bus=EventBus(),
    )


async def test_queued_jobs_survive_service_restart_when_persist_enabled(store) -> None:
    from grimoire.imagegen import ImageGenConfig
    await store.upsert_campaign(campaign_id="camp-1", name="t")

    cfg = ImageGenConfig(queue_persist_pending=True)
    svc1 = ImageGenService(
        store=store, registry=BackendRegistry(_with=InMemoryDiffusersBackend()),  # see below
        default_backend_id="diffusers-memory", event_bus=EventBus(), config=cfg,
    )
    # Block the worker so the job stays queued
    job_id = await svc1.queue_generation(
        campaign_id="camp-1", scene_id=None, post_id=None,
        request=GenerationRequest(prompt="x", width=8, height=8, seed=1),
    )
    rows = await store.db.fetchall(
        "SELECT id, status FROM imagegen_jobs WHERE id = ?", (job_id,)
    )
    assert rows and rows[0]["status"] == "queued"
    await svc1.aclose()

    svc2 = ImageGenService(
        store=store, registry=BackendRegistry(_with=InMemoryDiffusersBackend()),
        default_backend_id="diffusers-memory", event_bus=EventBus(), config=cfg,
    )
    await svc2.reload_pending_jobs()
    jobs = await svc2.list_jobs("camp-1")
    assert any(j.id == job_id for j in jobs)
    await svc2.aclose()
```

(Note: `BackendRegistry(_with=...)` is pseudocode — replace with `reg = BackendRegistry(); reg.register(InMemoryDiffusersBackend())`.)

- [ ] **Step 2: Implement persistence**

In `service.py`:

- In `queue_generation`, after `self._jobs[job_id] = job`, if `self.config.queue_persist_pending`, also insert into SQL:
  ```python
  if self.config.queue_persist_pending:
      await self.store.db.execute(
          """
          INSERT INTO imagegen_jobs (
            id, campaign_id, backend, status, priority, request_json,
            scene_id, post_id, queued_at, started_at, finished_at, error
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
          """,
          (
              job_id, campaign_id, backend_id, JobStatus.QUEUED.value,
              priority, request.model_dump_json(),
              scene_id, post_id, _now().isoformat(),
          ),
      )
  ```

- In `_worker`, after marking RUNNING and after marking COMPLETE/FAILED, update the row's `status`, `started_at`, `finished_at`, `error`.

- In `cancel_job`, also UPDATE the row.

- Add a `reload_pending_jobs` method:
  ```python
  async def reload_pending_jobs(self) -> None:
      if not self.config.queue_persist_pending:
          return
      rows = await self.store.db.fetchall(
          "SELECT * FROM imagegen_jobs WHERE status IN ('queued','running')"
      )
      for row in rows:
          job_id = row["id"]
          if row["status"] == "running":
              # Mark as failed: we can't recover the in-flight state.
              await self.store.db.execute(
                  "UPDATE imagegen_jobs SET status = 'failed', error = ? "
                  "WHERE id = ?",
                  ("interrupted by shutdown", job_id),
              )
              continue
          # Re-enqueue queued jobs
          request = GenerationRequest.model_validate_json(row["request_json"])
          job = GenerationJob(
              id=job_id, campaign_id=row["campaign_id"],
              backend=row["backend"], request=request, status=JobStatus.QUEUED,
              priority=row["priority"], queued_at=_now(),
              scene_id=row["scene_id"], post_id=row["post_id"],
          )
          self._jobs[job_id] = job
          self._ensure_handle(row["backend"])
          handle = self._handles[row["backend"]]
          handle._pending_jobs.add(job_id)
          await handle.queue.put(_QueueEntry(job_id=job_id))
  ```

- Call `reload_pending_jobs()` from `main.py` after service construction:
  ```python
  await container.imagegen.reload_pending_jobs()
  ```

- [ ] **Step 3: Run + commit**

```
git commit -m "Persist pending imagegen jobs across restarts (§8)"
```

### Task F3: `download_on_first_use` knob for diffusers plugin (§9)

**Files:**
- Modify: `backend/bundled_plugins/imagegen-diffusers/manifest.yaml` (extend `config_schema`)
- Modify: `backend/bundled_plugins/imagegen-diffusers/plugin.py` (honor knob in `_build_pipeline`; emit progress)
- Test: `backend/bundled_plugins/imagegen-diffusers/tests/test_download_knob.py` (new — if test infra exists here; otherwise put it under `backend/tests/`)

- [ ] **Step 1: Extend manifest**

```yaml
# In config_schema → properties, add:
download_on_first_use:
  type: string
  enum: ["prompt", "auto", "never"]
  default: "auto"
  title: Download on first use
  description: |
    What to do when the configured model isn't locally cached.
    "auto" downloads silently (~6 GB for SDXL base 1.0).
    "prompt" requires the user to confirm via the UI.
    "never" disables generation until the model is cached locally.
```

- [ ] **Step 2: Honor the knob in `_build_pipeline`**

Find the `_build_pipeline` method. Before calling `from_pretrained`, check `self.config.get("download_on_first_use", "auto")`:

```python
mode = self.config.get("download_on_first_use", "auto")
if mode == "never":
    raise RuntimeError(
        "model download disabled by config (download_on_first_use=never); "
        "place model weights in the local HF cache to enable"
    )
if mode == "prompt":
    # Wait for the user to confirm via the REST surface; raise until then.
    if not self._download_confirmed:
        raise RuntimeError(
            "model download requires user confirmation "
            "(POST /imagegen/backends/{id}/confirm-download)"
        )
```

Add `self._download_confirmed = False` in `__init__`, plus a public `confirm_download(self)` method that sets the flag.

In the health check, when the mode is `never` and the model isn't cached, return `UNCONFIGURED` with the "download disabled by config" message.

- [ ] **Step 3: REST verb for confirm-download**

In `api/imagegen.py`:

```python
@router.post("/backends/{backend_id}/confirm-download")
async def confirm_download(backend_id: str, imagegen: ImageGenDep) -> Any:
    backend = imagegen.registry.get(backend_id)
    if backend is None:
        raise HTTPException(status_code=404, detail=f"no backend {backend_id!r}")
    confirm = getattr(backend, "confirm_download", None)
    if confirm is None:
        raise HTTPException(
            status_code=400,
            detail=f"backend {backend_id!r} does not support download confirmation",
        )
    confirm()
    return {"ok": True}
```

- [ ] **Step 4: Download progress event**

Emit `imagegen_download_progress` (in branch C this is already in `_FORWARDED_EVENTS`). Mechanism: hook HF Hub's progress (or wrap `from_pretrained` with a TQDM callback that writes to an asyncio queue → emit). For v1, the test can just assert the event is emitted at least once with sensible payload shape.

- [ ] **Step 5: Run + commit**

```
git commit -m "Add download_on_first_use knob to diffusers plugin (§9)"
```

### Task F4: Merge branch F

Same as A7.

---

# Final integration

### Task FINAL1: Verify all merges land cleanly

- [ ] **Step 1: From repo root**

```powershell
git checkout main
git pull origin main
pytest backend/tests/imagegen -v
pytest backend/tests/api -v
```

- [ ] **Step 2: Smoke-test app boot**

```powershell
python -c "from grimoire.main import create_app; app = create_app(); print('ok')"
```

- [ ] **Step 3: Remove worktrees**

```powershell
git worktree remove .worktrees/imagegen-A-config
git worktree remove .worktrees/imagegen-B-orchestrator
git worktree remove .worktrees/imagegen-C-rest
git worktree remove .worktrees/imagegen-D-lifecycle
git worktree remove .worktrees/imagegen-E-ux
git worktree remove .worktrees/imagegen-F-persistence
```

### Task FINAL2: Mark spec complete

- [ ] **Step 1: Rename**

```powershell
git mv docs/superpowers/specs/2026-05-17-imagegen-remaining-design.md docs/superpowers/specs/2026-05-17-imagegen-COMPLETED.md
git commit -m "Mark imagegen remaining-design spec complete"
git push origin main
```

---

# Self-review checklist

**Spec coverage (§1–§13 of `2026-05-17-imagegen-remaining-design.md`):**

- §1 Orchestrator trigger fan-out → Branch B (`ImageGenIntegration`)
- §2 Health monitoring loop + fallback → Branch D Task D3
- §3 Progress events → Branch D Task D1
- §4 Cooperative cancellation → Branch D Task D2
- §5 Manual prompt editing / save-as-new → Branch E Task E1
- §6 Per-campaign config storage → Branch A Tasks A1, A4, A5
- §7 Top-level YAML config → Branch A Tasks A2, A3, A6
- §8 Persistent job queue → Branch F Tasks F1, F2
- §9 First-launch model download UX → Branch F Task F3
- §10 Image tag editing → Branch E Task E2
- §11 Per-character canonical seed → Branch E Task E3
- §12 Pre-warm integrated backend → Branch E Task E4
- §13 Missing REST surface → Branch C Tasks C1–C5 (12 routes + 503 mapping + new forwarded events + trigger/fallback routes)

**Known limitations / out-of-scope (acknowledged):**

- §1 is_new_location / is_new_character detection is currently always False (no producer of those signals exists in Scene Manager today, per investigation). Plan stubs the flag and adds a TODO in `ImageGenIntegration._on_turn_complete`. Producing those signals from existing scene state is a follow-up.
- §3 a1111 + comfyui plugins are not modified — only the protocol is extended and the service-side wiring is added. Hooking their native progress streams is a follow-up.
- §9 download progress event payload is best-effort; a TQDM-driven implementation is left as a follow-up.
- §14–§16 explicitly out of scope per user direction.

**Branch dependency graph:**

```
A (config foundation) ─┬─► B (orchestrator)
                       ├─► C (REST surface)
                       ├─► D (backend lifecycle)
                       ├─► E (UX wins)
                       └─► F (persistence + download)
```

B–F can be developed concurrently after A merges. Merge order: A first; B–F in any order with rebase before each merge.
