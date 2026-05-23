# LLM Tiering + Configurable Summaries (PR 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two-tier (Heavy + Light) LLM routing, per-campaign summary-cadence config, and the UI to drive both, without changing behavior for existing campaigns. PR 2 (integrated narrator+extraction) is a follow-up plan.

**Architecture:** A new `tiers.py` module holds the constant `task → tier` map. `RouteResolver` gets tier-aware fallback: per-task route wins, else tier route, else app default. Gateway reads a new `model_tiers` YAML block out of `campaign.yaml`. Per-campaign summary cadence lives in `campaigns.config["summaries"]`; `SceneManager` reads it per scene write. Frontend gets a redesigned Routing tab (3 tier pickers + Advanced expander), a new Summaries tab, and app-level Heavy/Light defaults.

**Tech Stack:** Python 3.12 (FastAPI, pydantic v2, pytest, pyyaml). TypeScript / React (vitest). SQLite via `aiosqlite`. Frontend axios via `api` client.

**Spec:** `docs/superpowers/specs/2026-05-23-llm-tiering-design.md`.

---

## File Structure

**New files:**
- `backend/src/grimoire/llm_gateway/tiers.py` — `_TASK_TIER` constant + `tier_for_task()` helper.
- `backend/tests/llm_gateway/test_tiers.py` — unit tests for the helper.
- `docs/superpowers/plans/2026-05-23-llm-tiering-pr1.md` — this file.

**Modified files:**
- `backend/src/grimoire/llm_gateway/routing.py` — tier dict + tier-aware `resolve`.
- `backend/src/grimoire/llm_gateway/gateway.py` — read `model_tiers` block.
- `backend/src/grimoire/api/campaigns.py` — `GET/PUT /tiers`, `GET/PUT /summaries`.
- `backend/src/grimoire/api/config.py` — app-level Heavy/Light defaults.
- `backend/src/grimoire/scenes/manager.py` — per-campaign cadence + `final_on_close`.
- `backend/tests/api/test_campaign_settings_routes.py` — endpoint contract tests.
- `backend/tests/scenes/test_manager.py` (or new file) — summary cadence tests.
- `frontend/src/api/campaign.ts` — client methods.
- `frontend/src/routes/CampaignSettings.tsx` — Summaries tab + Routing tab redesign.
- `frontend/src/routes/AppSettings.tsx` — Heavy/Light default pickers.

---

## Task 1: Tier helper + constant map

**Files:**
- Create: `backend/src/grimoire/llm_gateway/tiers.py`
- Test: `backend/tests/llm_gateway/test_tiers.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/llm_gateway/test_tiers.py`:

```python
"""Tier mapping for the LLM gateway."""

from __future__ import annotations

from grimoire.llm_gateway.tiers import Tier, tier_for_task


def test_heavy_tasks() -> None:
    assert tier_for_task("main") == Tier.HEAVY
    assert tier_for_task("scenes.running_summary") == Tier.HEAVY
    assert tier_for_task("scenes.final_summary") == Tier.HEAVY
    assert tier_for_task("auxiliary.rewrite_post") == Tier.HEAVY
    assert tier_for_task("auxiliary.continue_as") == Tier.HEAVY
    assert tier_for_task("auxiliary.brainstorm") == Tier.HEAVY
    assert tier_for_task("world.atmosphere") == Tier.HEAVY


def test_light_tasks() -> None:
    assert tier_for_task("drift_check") == Tier.LIGHT
    assert tier_for_task("scene_break_classifier") == Tier.LIGHT
    assert tier_for_task("auxiliary.translate") == Tier.LIGHT
    assert tier_for_task("auxiliary.what_would_x_say") == Tier.LIGHT
    assert tier_for_task("auxiliary.edit_prose") == Tier.LIGHT
    assert tier_for_task("world.location_generator") == Tier.LIGHT
    assert tier_for_task("extractor") == Tier.LIGHT


def test_embedding_task() -> None:
    assert tier_for_task("library.embed") == Tier.EMBEDDING


def test_unknown_task_returns_none() -> None:
    assert tier_for_task("not.a.real.task") is None
    assert tier_for_task("") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/llm_gateway/test_tiers.py -v`
Expected: `ModuleNotFoundError: No module named 'grimoire.llm_gateway.tiers'`.

- [ ] **Step 3: Write minimal implementation**

Create `backend/src/grimoire/llm_gateway/tiers.py`:

```python
"""Task → tier mapping for the LLM gateway.

Three logical tiers — Heavy (generation), Light (classification +
mechanical transforms), Embedding — provide a coarse routing knob so
users can point cheap and expensive models at the right work without
editing per-task routes by hand.

The mapping is built-in and stable; user overrides live in
``model_routing`` (per-task) and ``model_tiers`` (per-campaign) on
``campaign.yaml``. See ``docs/superpowers/specs/2026-05-23-llm-tiering-design.md``
§1 for the rationale behind each assignment.
"""

from __future__ import annotations

from enum import StrEnum


class Tier(StrEnum):
    HEAVY = "heavy"
    LIGHT = "light"
    EMBEDDING = "embedding"


_TASK_TIER: dict[str, Tier] = {
    # Heavy — generative work
    "main": Tier.HEAVY,
    "scenes.running_summary": Tier.HEAVY,
    "scenes.final_summary": Tier.HEAVY,
    "auxiliary.rewrite_post": Tier.HEAVY,
    "auxiliary.continue_as": Tier.HEAVY,
    "auxiliary.brainstorm": Tier.HEAVY,
    "world.atmosphere": Tier.HEAVY,
    # Light — classification + mechanical transforms
    "drift_check": Tier.LIGHT,
    "scene_break_classifier": Tier.LIGHT,
    "auxiliary.translate": Tier.LIGHT,
    "auxiliary.what_would_x_say": Tier.LIGHT,
    "auxiliary.edit_prose": Tier.LIGHT,
    "world.location_generator": Tier.LIGHT,
    "extractor": Tier.LIGHT,
    # Embedding
    "library.embed": Tier.EMBEDDING,
}


def tier_for_task(task: str) -> Tier | None:
    """Return the tier for ``task`` or ``None`` for an unknown task.

    Unknown tasks fall through to the resolver's default route — they
    are not silently routed to Light or Heavy.
    """
    return _TASK_TIER.get(task)


__all__ = ["Tier", "tier_for_task"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/llm_gateway/test_tiers.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/llm_gateway/tiers.py backend/tests/llm_gateway/test_tiers.py
git commit -m "feat(llm_gateway): tier constant + tier_for_task helper"
```

---

## Task 2: Tier-aware route resolution

**Files:**
- Modify: `backend/src/grimoire/llm_gateway/routing.py`
- Test: `backend/tests/llm_gateway/test_routing.py` (new file)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/llm_gateway/test_routing.py`:

```python
"""Route resolution: per-task → tier → default fallback chain."""

from __future__ import annotations

import pytest

from grimoire.llm_gateway.errors import RouteNotFoundError
from grimoire.llm_gateway.routing import RouteResolver
from grimoire.llm_gateway.tiers import Tier


def test_per_task_wins_over_tier() -> None:
    r = RouteResolver(default_routes={"main": "anthropic.opus"})
    r.set_tier_route("camp-1", Tier.HEAVY, "deepseek.pro")
    r.set_route("main", "openai.gpt-5", campaign_id="camp-1")
    assert r.resolve("main", "camp-1").raw == "openai.gpt-5"


def test_tier_used_when_no_per_task_override() -> None:
    r = RouteResolver(default_routes={"main": "anthropic.opus"})
    r.set_tier_route("camp-1", Tier.HEAVY, "deepseek.pro")
    # No per-task override; tier route wins over the app default.
    assert r.resolve("main", "camp-1").raw == "deepseek.pro"


def test_default_used_when_no_tier_or_per_task() -> None:
    r = RouteResolver(default_routes={"main": "anthropic.opus"})
    assert r.resolve("main", "camp-1").raw == "anthropic.opus"


def test_unknown_task_falls_through_to_default_not_tier() -> None:
    r = RouteResolver(default_routes={"weird.task": "anthropic.opus"})
    r.set_tier_route("camp-1", Tier.HEAVY, "deepseek.pro")
    # "weird.task" isn't in _TASK_TIER → tier lookup fails → default used.
    assert r.resolve("weird.task", "camp-1").raw == "anthropic.opus"


def test_no_route_raises() -> None:
    r = RouteResolver()
    with pytest.raises(RouteNotFoundError):
        r.resolve("main", "camp-1")


def test_clear_tier_route() -> None:
    r = RouteResolver(default_routes={"main": "anthropic.opus"})
    r.set_tier_route("camp-1", Tier.HEAVY, "deepseek.pro")
    r.clear_tier_route("camp-1", Tier.HEAVY)
    assert r.resolve("main", "camp-1").raw == "anthropic.opus"


def test_tiers_for_campaign() -> None:
    r = RouteResolver()
    r.set_tier_route("camp-1", Tier.HEAVY, "deepseek.pro")
    r.set_tier_route("camp-1", Tier.LIGHT, "deepseek.flash")
    tiers = r.tiers_for("camp-1")
    assert tiers == {Tier.HEAVY: "deepseek.pro", Tier.LIGHT: "deepseek.flash"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/llm_gateway/test_routing.py -v`
Expected: 6 fails / errors (most likely `AttributeError: 'RouteResolver' object has no attribute 'set_tier_route'`).

- [ ] **Step 3: Write minimal implementation**

Edit `backend/src/grimoire/llm_gateway/routing.py`. Replace the whole file with:

```python
"""Routing: task -> `provider_id.model` resolution.

Resolution chain (highest priority first):
  1. Per-campaign per-task override  (``_campaigns[cid][task]``)
  2. Per-campaign tier route          (``_tiers[cid][tier_for_task(task)]``)
  3. App-level default route          (``_defaults[task]``)
  4. Fallback route                   (``_fallbacks[task]``)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from grimoire.llm_gateway.errors import RouteNotFoundError
from grimoire.llm_gateway.tiers import Tier, tier_for_task
from grimoire.types.common import CampaignId

_SEPARATOR: Final[str] = "."


@dataclass(frozen=True)
class Route:
    raw: str
    provider_id: str
    model: str

    @classmethod
    def parse(cls, raw: str) -> Route:
        if not isinstance(raw, str) or _SEPARATOR not in raw:
            raise ValueError(f"route {raw!r} must be of the form 'provider.model'")
        provider_id, _, model = raw.partition(_SEPARATOR)
        if not provider_id or not model:
            raise ValueError(f"route {raw!r} must be of the form 'provider.model'")
        return cls(raw=raw, provider_id=provider_id, model=model)


class RouteResolver:
    """Owns default + per-campaign per-task + per-campaign per-tier + fallback routes."""

    def __init__(
        self,
        default_routes: dict[str, str] | None = None,
        fallback_routes: dict[str, str] | None = None,
    ) -> None:
        self._defaults: dict[str, str] = {}
        self._fallbacks: dict[str, str] = {}
        self._campaigns: dict[CampaignId, dict[str, str]] = {}
        self._tiers: dict[CampaignId, dict[Tier, str]] = {}
        for task, route in (default_routes or {}).items():
            Route.parse(route)
            self._defaults[task] = route
        for task, route in (fallback_routes or {}).items():
            Route.parse(route)
            self._fallbacks[task] = route

    def resolve(self, task: str, campaign_id: CampaignId | None = None) -> Route:
        raw: str | None = None
        if campaign_id is not None:
            raw = self._campaigns.get(campaign_id, {}).get(task)
            if raw is None:
                tier = tier_for_task(task)
                if tier is not None:
                    raw = self._tiers.get(campaign_id, {}).get(tier)
        if raw is None:
            raw = self._defaults.get(task)
        if raw is None:
            raise RouteNotFoundError(task)
        return Route.parse(raw)

    def fallback(self, task: str) -> Route | None:
        raw = self._fallbacks.get(task)
        return Route.parse(raw) if raw else None

    def set_route(
        self,
        task: str,
        route: str,
        campaign_id: CampaignId | None = None,
    ) -> None:
        Route.parse(route)
        if campaign_id is None:
            self._defaults[task] = route
        else:
            self._campaigns.setdefault(campaign_id, {})[task] = route

    def clear_route(self, task: str, campaign_id: CampaignId | None = None) -> None:
        if campaign_id is None:
            self._defaults.pop(task, None)
        else:
            self._campaigns.get(campaign_id, {}).pop(task, None)

    def set_tier_route(
        self,
        campaign_id: CampaignId,
        tier: Tier,
        route: str,
    ) -> None:
        Route.parse(route)
        self._tiers.setdefault(campaign_id, {})[tier] = route

    def clear_tier_route(self, campaign_id: CampaignId, tier: Tier) -> None:
        self._tiers.get(campaign_id, {}).pop(tier, None)

    def tiers_for(self, campaign_id: CampaignId) -> dict[Tier, str]:
        return dict(self._tiers.get(campaign_id, {}))

    def routes_for(self, campaign_id: CampaignId | None = None) -> dict[str, str]:
        merged = dict(self._defaults)
        if campaign_id is not None:
            merged.update(self._campaigns.get(campaign_id, {}))
        return merged
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/llm_gateway/test_routing.py tests/llm_gateway/test_tiers.py -v`
Expected: 11 passed (7 new + 4 from task 1).

Then run the existing gateway tests to ensure no regression:
Run: `cd backend && pytest tests/llm_gateway/ -v`
Expected: all pre-existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/llm_gateway/routing.py backend/tests/llm_gateway/test_routing.py
git commit -m "feat(llm_gateway): tier-aware resolve in RouteResolver"
```

---

## Task 3: Gateway reads `model_tiers` from campaign.yaml

**Files:**
- Modify: `backend/src/grimoire/llm_gateway/gateway.py` (around line 1411–1456 — `_load_campaign_routing`)

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/llm_gateway/test_routing.py`:

```python
import asyncio
from pathlib import Path

import yaml

from grimoire.llm_gateway.config import GatewayConfig
from grimoire.llm_gateway.gateway import LLMGatewayService
from grimoire.types.llm import RetryPolicy, TimeoutPolicy


class _NoOpPlugins:
    def get_llm_provider(self, _id):
        return None

    def get_embedding_provider(self, _id):
        return None

    def get_imagegen_backend(self, _id):
        return None

    def llm_providers(self):
        return []

    def embedding_providers(self):
        return []


def _make_gateway(tmp_path: Path) -> LLMGatewayService:
    cfg = GatewayConfig(
        retry=RetryPolicy(max_attempts=1),
        timeout=TimeoutPolicy(),
    )
    return LLMGatewayService(
        plugins=_NoOpPlugins(),
        config=cfg,
        data_root=tmp_path,
    )


def test_gateway_loads_model_tiers_from_yaml(tmp_path: Path) -> None:
    camp_dir = tmp_path / "campaigns" / "camp-1"
    camp_dir.mkdir(parents=True)
    (camp_dir / "campaign.yaml").write_text(
        yaml.safe_dump(
            {
                "model_tiers": {
                    "heavy": "deepseek.deepseek-v4-pro",
                    "light": "deepseek.deepseek-v4-flash",
                }
            }
        )
    )
    gw = _make_gateway(tmp_path)
    # Trigger lazy load.
    asyncio.run(gw._load_campaign_routing("camp-1"))
    tiers = gw._router.tiers_for("camp-1")
    assert tiers[Tier.HEAVY] == "deepseek.deepseek-v4-pro"
    assert tiers[Tier.LIGHT] == "deepseek.deepseek-v4-flash"


def test_gateway_skips_unknown_tier_keys(tmp_path: Path) -> None:
    camp_dir = tmp_path / "campaigns" / "camp-1"
    camp_dir.mkdir(parents=True)
    (camp_dir / "campaign.yaml").write_text(
        yaml.safe_dump(
            {"model_tiers": {"heavy": "deepseek.pro", "bogus": "x.y"}}
        )
    )
    gw = _make_gateway(tmp_path)
    asyncio.run(gw._load_campaign_routing("camp-1"))
    tiers = gw._router.tiers_for("camp-1")
    assert Tier.HEAVY in tiers
    assert "bogus" not in [t.value for t in tiers]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/llm_gateway/test_routing.py::test_gateway_loads_model_tiers_from_yaml -v`
Expected: FAIL — `tiers_for` returns empty dict because the loader doesn't parse `model_tiers` yet.

- [ ] **Step 3: Write minimal implementation**

Edit `backend/src/grimoire/llm_gateway/gateway.py`. In `_load_campaign_routing` (currently around line 1411), after the existing `_apply_imagegen_routing` call, add a new `_apply_tier_routing` call. Also add the helper method on the class.

After line 1456 (the existing `await self._apply_imagegen_routing(...)` call), add:

```python
        self._apply_tier_routing(raw.get("model_tiers"), campaign_id, yaml_path)
```

Then add this new method on the class (place it after `_apply_imagegen_routing`):

```python
    def _apply_tier_routing(
        self,
        block: object,
        campaign_id: CampaignId,
        yaml_path: Path,
    ) -> None:
        """Read the ``model_tiers`` block and populate the resolver.

        Block shape: ``{"heavy": "provider.model", "light": "...",
        "embedding": "..."}``. Unknown keys are ignored with a debug log;
        malformed route strings are skipped with a warning. The campaign
        keeps any per-task overrides loaded earlier.
        """
        from grimoire.llm_gateway.tiers import Tier

        if not isinstance(block, dict):
            return
        for key, value in block.items():
            try:
                tier = Tier(str(key))
            except ValueError:
                logger.debug(
                    "llm_gateway: unknown tier %r in %s; skipping",
                    key,
                    yaml_path,
                )
                continue
            if not isinstance(value, str) or not value:
                continue
            try:
                self._router.set_tier_route(campaign_id, tier, value)
            except ValueError as exc:
                logger.warning(
                    "llm_gateway: bad route %r for tier %s in %s: %s",
                    value,
                    tier.value,
                    yaml_path,
                    exc,
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/llm_gateway/test_routing.py -v`
Expected: 9 passed (7 from task 2 + 2 new).

Then run a broader sanity check:
Run: `cd backend && pytest tests/llm_gateway/ tests/api/test_campaign_settings_routes.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/llm_gateway/gateway.py backend/tests/llm_gateway/test_routing.py
git commit -m "feat(llm_gateway): read model_tiers block from campaign.yaml"
```

---

## Task 4: Persist tier routes via Gateway.set_tier_route

**Files:**
- Modify: `backend/src/grimoire/llm_gateway/gateway.py` (add a public `set_tier_route` method that writes to YAML)

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/llm_gateway/test_routing.py`:

```python
def test_gateway_set_tier_route_persists_to_yaml(tmp_path: Path) -> None:
    camp_dir = tmp_path / "campaigns" / "camp-1"
    camp_dir.mkdir(parents=True)
    (camp_dir / "campaign.yaml").write_text(yaml.safe_dump({"id": "camp-1"}))
    gw = _make_gateway(tmp_path)
    asyncio.run(
        gw.set_tier_route("camp-1", Tier.HEAVY, "deepseek.pro")
    )
    raw = yaml.safe_load((camp_dir / "campaign.yaml").read_text())
    assert raw["model_tiers"] == {"heavy": "deepseek.pro"}

    # Setting another tier preserves the first.
    asyncio.run(
        gw.set_tier_route("camp-1", Tier.LIGHT, "deepseek.flash")
    )
    raw = yaml.safe_load((camp_dir / "campaign.yaml").read_text())
    assert raw["model_tiers"] == {"heavy": "deepseek.pro", "light": "deepseek.flash"}


def test_gateway_clear_tier_route_persists(tmp_path: Path) -> None:
    camp_dir = tmp_path / "campaigns" / "camp-1"
    camp_dir.mkdir(parents=True)
    (camp_dir / "campaign.yaml").write_text(
        yaml.safe_dump({"model_tiers": {"heavy": "x.y", "light": "a.b"}})
    )
    gw = _make_gateway(tmp_path)
    asyncio.run(gw.clear_tier_route("camp-1", Tier.HEAVY))
    raw = yaml.safe_load((camp_dir / "campaign.yaml").read_text())
    assert raw["model_tiers"] == {"light": "a.b"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/llm_gateway/test_routing.py::test_gateway_set_tier_route_persists_to_yaml -v`
Expected: FAIL — `LLMGatewayService` has no `set_tier_route` method.

- [ ] **Step 3: Write minimal implementation**

Edit `backend/src/grimoire/llm_gateway/gateway.py`. After the existing `set_route` method (around line 193), add two new methods on the class:

```python
    async def set_tier_route(
        self,
        campaign_id: CampaignId,
        tier: "Tier",
        route: str,
    ) -> None:
        """Apply a tier route and persist to campaign.yaml.

        The route must parse via ``Route.parse``. Persistence preserves
        any other ``model_tiers`` entries and any existing routing
        blocks on the file.
        """
        from grimoire.llm_gateway.tiers import Tier as _Tier  # noqa: F401 (imported above)

        Route.parse(route)
        self._router.set_tier_route(campaign_id, tier, route)
        yaml_path = self._campaign_yaml_path(campaign_id)
        if yaml_path is None:
            return
        await self._write_tier_block(yaml_path, campaign_id)

    async def clear_tier_route(
        self,
        campaign_id: CampaignId,
        tier: "Tier",
    ) -> None:
        """Remove a tier route from in-memory state and campaign.yaml."""
        self._router.clear_tier_route(campaign_id, tier)
        yaml_path = self._campaign_yaml_path(campaign_id)
        if yaml_path is None:
            return
        await self._write_tier_block(yaml_path, campaign_id)

    async def _write_tier_block(
        self,
        yaml_path: Path,
        campaign_id: CampaignId,
    ) -> None:
        """Serialize the resolver's tier state back to ``campaign.yaml``."""
        from grimoire.files.yaml_io import dump_yaml, load_yaml

        raw: dict = {}
        if yaml_path.is_file():
            loaded = load_yaml(yaml_path)
            if isinstance(loaded, dict):
                raw = loaded
        tiers = self._router.tiers_for(campaign_id)
        if tiers:
            raw["model_tiers"] = {tier.value: route for tier, route in tiers.items()}
        else:
            raw.pop("model_tiers", None)
        dump_yaml(yaml_path, raw)
```

Also add the `Tier` import at the top of the file (find the existing imports section near the top — there's already `from grimoire.llm_gateway.routing import Route` somewhere). Add:

```python
from grimoire.llm_gateway.tiers import Tier
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/llm_gateway/test_routing.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/llm_gateway/gateway.py backend/tests/llm_gateway/test_routing.py
git commit -m "feat(llm_gateway): set/clear_tier_route persist to campaign.yaml"
```

---

## Task 5: REST endpoints `GET/PUT /api/campaigns/{id}/tiers`

**Files:**
- Modify: `backend/src/grimoire/api/campaigns.py`
- Modify: `backend/tests/api/test_campaign_settings_routes.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/api/test_campaign_settings_routes.py`:

```python
def test_tiers_default_empty(settings_client) -> None:
    resp = settings_client.get("/api/campaigns/camp-1/tiers")
    assert resp.status_code == 200
    assert resp.json() == {"heavy": None, "light": None, "embedding": None}


def test_tiers_round_trip(settings_client) -> None:
    resp = settings_client.put(
        "/api/campaigns/camp-1/tiers",
        json={
            "heavy": "deepseek.deepseek-v4-pro",
            "light": "deepseek.deepseek-v4-flash",
            "embedding": "voyage.voyage-3",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["heavy"] == "deepseek.deepseek-v4-pro"
    assert body["light"] == "deepseek.deepseek-v4-flash"
    assert body["embedding"] == "voyage.voyage-3"

    resp = settings_client.get("/api/campaigns/camp-1/tiers")
    assert resp.json()["heavy"] == "deepseek.deepseek-v4-pro"


def test_tiers_null_clears(settings_client) -> None:
    settings_client.put(
        "/api/campaigns/camp-1/tiers",
        json={"heavy": "deepseek.pro", "light": "deepseek.flash"},
    )
    resp = settings_client.put(
        "/api/campaigns/camp-1/tiers",
        json={"heavy": None, "light": "deepseek.flash"},
    )
    assert resp.json()["heavy"] is None
    assert resp.json()["light"] == "deepseek.flash"


def test_tiers_bad_route_rejected(settings_client) -> None:
    resp = settings_client.put(
        "/api/campaigns/camp-1/tiers",
        json={"heavy": "missing_dot"},
    )
    assert resp.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/api/test_campaign_settings_routes.py::test_tiers_default_empty -v`
Expected: FAIL with 404 (endpoint doesn't exist).

- [ ] **Step 3: Write minimal implementation**

Edit `backend/src/grimoire/api/campaigns.py`. Add a new payload class. Find the existing `StorageSettingsPayload` definition (around line 594) and add this near the other settings payloads:

```python
class TierSettingsPayload(BaseModel):
    """Per-campaign tier routes.

    Each field is ``"provider.model"`` or ``None``. ``None`` clears the
    override and lets the resolver fall through to the app default
    (or per-task route, if one is set).
    """

    heavy: str | None = None
    light: str | None = None
    embedding: str | None = None
```

Then add the two endpoints. The natural place is alongside the existing routing endpoints — find the existing `@router.put("/{campaign_id}/routing")` block (around line 668) and add immediately after it:

```python
@router.get("/{campaign_id}/tiers")
async def get_campaign_tiers(
    campaign_id: str,
    state_store: StateStoreDep,
) -> Any:
    await _require_campaign_row(state_store, campaign_id)
    # Read directly from campaign.yaml — the gateway is the writer, but
    # we don't want to require the gateway to have lazy-loaded this
    # campaign before answering a GET.
    from grimoire.files.yaml_io import load_yaml

    data_root = getattr(state_store, "data_root", None)
    tiers: dict[str, str | None] = {"heavy": None, "light": None, "embedding": None}
    if data_root is None:
        return tiers
    yaml_path = data_root / "campaigns" / campaign_id / "campaign.yaml"
    if not yaml_path.is_file():
        return tiers
    try:
        raw = load_yaml(yaml_path)
    except Exception:
        return tiers
    if not isinstance(raw, dict):
        return tiers
    block = raw.get("model_tiers") or {}
    if isinstance(block, dict):
        for k in ("heavy", "light", "embedding"):
            v = block.get(k)
            if isinstance(v, str) and v:
                tiers[k] = v
    return tiers


@router.put("/{campaign_id}/tiers")
async def set_campaign_tiers(
    campaign_id: str,
    payload: TierSettingsPayload,
    state_store: StateStoreDep,
    gateway: LLMGatewayDep,
) -> Any:
    from grimoire.llm_gateway.routing import Route
    from grimoire.llm_gateway.tiers import Tier

    await _require_campaign_row(state_store, campaign_id)
    # Validate routes up-front so we don't half-apply.
    for value in (payload.heavy, payload.light, payload.embedding):
        if value is None:
            continue
        try:
            Route.parse(value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    for tier, value in (
        (Tier.HEAVY, payload.heavy),
        (Tier.LIGHT, payload.light),
        (Tier.EMBEDDING, payload.embedding),
    ):
        if value is None:
            await gateway.clear_tier_route(campaign_id, tier)
        else:
            await gateway.set_tier_route(campaign_id, tier, value)

    return {
        "heavy": payload.heavy,
        "light": payload.light,
        "embedding": payload.embedding,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/api/test_campaign_settings_routes.py -v -k tiers`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/api/campaigns.py backend/tests/api/test_campaign_settings_routes.py
git commit -m "feat(api): GET/PUT /campaigns/{id}/tiers for Heavy/Light/Embedding routing"
```

---

## Task 6: Summary cadence helpers + REST endpoints

**Files:**
- Modify: `backend/src/grimoire/api/campaigns.py`
- Modify: `backend/tests/api/test_campaign_settings_routes.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/api/test_campaign_settings_routes.py`:

```python
def test_summaries_default(settings_client) -> None:
    resp = settings_client.get("/api/campaigns/camp-1/summaries")
    assert resp.status_code == 200
    assert resp.json() == {"running_every_n_posts": 5, "final_on_close": True}


def test_summaries_round_trip(settings_client) -> None:
    resp = settings_client.put(
        "/api/campaigns/camp-1/summaries",
        json={"running_every_n_posts": 0, "final_on_close": False},
    )
    assert resp.status_code == 200
    assert resp.json() == {"running_every_n_posts": 0, "final_on_close": False}

    resp = settings_client.get("/api/campaigns/camp-1/summaries")
    assert resp.json()["running_every_n_posts"] == 0
    assert resp.json()["final_on_close"] is False


def test_summaries_negative_n_rejected(settings_client) -> None:
    resp = settings_client.put(
        "/api/campaigns/camp-1/summaries",
        json={"running_every_n_posts": -1, "final_on_close": True},
    )
    assert resp.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/api/test_campaign_settings_routes.py::test_summaries_default -v`
Expected: FAIL with 404.

- [ ] **Step 3: Write minimal implementation**

Edit `backend/src/grimoire/api/campaigns.py`. Add a payload class near the other settings payloads:

```python
class SummariesSettingsPayload(BaseModel):
    """Per-campaign summarization cadence.

    ``running_every_n_posts`` of ``0`` disables in-scene summaries
    entirely. ``final_on_close`` toggles the LLM call that runs when a
    scene closes; when off, the final summary falls back to the
    running summary (or empty string).
    """

    running_every_n_posts: int = Field(default=5, ge=0, le=1000)
    final_on_close: bool = True
```

Then add the endpoints, alongside the existing `/storage` and `/advanced` ones. Find `@router.get("/{campaign_id}/storage")` (around line 734) and add immediately before it:

```python
@router.get("/{campaign_id}/summaries")
async def get_campaign_summaries(
    campaign_id: str,
    state_store: StateStoreDep,
) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    block = cfg.get("summaries") or {}
    return {
        "running_every_n_posts": int(block.get("running_every_n_posts", 5)),
        "final_on_close": bool(block.get("final_on_close", True)),
    }


@router.put("/{campaign_id}/summaries")
async def set_campaign_summaries(
    campaign_id: str,
    payload: SummariesSettingsPayload,
    state_store: StateStoreDep,
) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    cfg["summaries"] = {
        "running_every_n_posts": int(payload.running_every_n_posts),
        "final_on_close": bool(payload.final_on_close),
    }
    await _write_campaign_config(state_store, campaign_id, cfg)
    return cfg["summaries"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/api/test_campaign_settings_routes.py -v -k summar`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/api/campaigns.py backend/tests/api/test_campaign_settings_routes.py
git commit -m "feat(api): GET/PUT /campaigns/{id}/summaries for cadence + final toggle"
```

---

## Task 7: SceneManager honors per-campaign `running_every_n_posts`

**Files:**
- Modify: `backend/src/grimoire/scenes/manager.py`
- Test: `backend/tests/scenes/test_summary_cadence.py` (new file)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/scenes/test_summary_cadence.py`:

```python
"""Per-campaign summary cadence overrides the SceneManager default."""

from __future__ import annotations

import pytest

from grimoire.scenes.events import RUNNING_SUMMARY_DUE
from grimoire.scenes.manager import SceneManager, SceneManagerConfig


class _StubBus:
    def __init__(self) -> None:
        self.events: list = []

    async def emit(self, event) -> None:
        self.events.append(event)

    def subscribe(self, *_a, **_kw) -> None:
        pass


@pytest.fixture
def manager(tmp_path) -> SceneManager:
    cfg = SceneManagerConfig()
    cfg.running_summary_every_n_posts = 5
    return SceneManager(
        data_root=tmp_path,
        event_bus=_StubBus(),
        config=cfg,
    )


def test_should_emit_running_summary_when_count_matches_default(manager: SceneManager) -> None:
    # Default cadence = 5; override = None → use default.
    assert manager._should_emit_running_summary(post_count=5, override=None) is True
    assert manager._should_emit_running_summary(post_count=10, override=None) is True
    assert manager._should_emit_running_summary(post_count=3, override=None) is False


def test_zero_override_disables_running_summary(manager: SceneManager) -> None:
    assert manager._should_emit_running_summary(post_count=5, override=0) is False
    assert manager._should_emit_running_summary(post_count=100, override=0) is False


def test_custom_override_changes_cadence(manager: SceneManager) -> None:
    assert manager._should_emit_running_summary(post_count=3, override=3) is True
    assert manager._should_emit_running_summary(post_count=5, override=3) is False
    assert manager._should_emit_running_summary(post_count=6, override=3) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/scenes/test_summary_cadence.py -v`
Expected: FAIL — `SceneManager` has no `_should_emit_running_summary` method.

- [ ] **Step 3: Write minimal implementation**

Edit `backend/src/grimoire/scenes/manager.py`. Find the cadence check at line 551-560:

```python
            if (
                self.config.running_summary_every_n_posts > 0
                and scene.post_count > 0
                and scene.post_count % self.config.running_summary_every_n_posts == 0
            ):
                await self._emit(
                    RUNNING_SUMMARY_DUE,
                    scene,
                    post_count=scene.post_count,
                )
```

Replace with:

```python
            override = self._campaign_summary_cadence(scene.campaign_id)
            if self._should_emit_running_summary(
                post_count=scene.post_count,
                override=override,
            ):
                await self._emit(
                    RUNNING_SUMMARY_DUE,
                    scene,
                    post_count=scene.post_count,
                )
```

Then add these two new methods to the `SceneManager` class (place them near `_scene_id` for visibility):

```python
    def _should_emit_running_summary(
        self,
        *,
        post_count: int,
        override: int | None,
    ) -> bool:
        """Return True when a RUNNING_SUMMARY_DUE event should fire now.

        ``override`` of ``0`` disables in-scene summaries entirely.
        ``None`` falls back to the manager-wide default
        (``self.config.running_summary_every_n_posts``).
        """
        n = override if override is not None else self.config.running_summary_every_n_posts
        if n <= 0 or post_count <= 0:
            return False
        return post_count % n == 0

    def _campaign_summary_cadence(self, campaign_id: str) -> int | None:
        """Read ``campaigns.config["summaries"]["running_every_n_posts"]``.

        Returns ``None`` when no override is set (caller falls back to
        the manager-wide default). The lookup is best-effort: any error
        returns ``None`` to keep the post path stable.
        """
        store = getattr(self, "_state_store", None)
        if store is None:
            return None
        # Use a cached sync read path; we're inside an async lock so
        # blocking briefly on a SELECT is acceptable.
        try:
            row = store.db.fetchone_sync(  # see step 3a if this helper doesn't exist
                "SELECT config FROM campaigns WHERE id = ?", (campaign_id,)
            )
        except Exception:
            return None
        if not row:
            return None
        raw = row.get("config") if hasattr(row, "get") else row["config"]
        if not raw:
            return None
        import json as _json

        try:
            data = _json.loads(raw)
        except (TypeError, ValueError):
            return None
        block = data.get("summaries") if isinstance(data, dict) else None
        if not isinstance(block, dict):
            return None
        n = block.get("running_every_n_posts")
        return int(n) if isinstance(n, int) else None
```

**Step 3a:** if `StateStore.db.fetchone_sync` doesn't exist (likely), inspect the StateStore at `backend/src/grimoire/state_store/store.py` and use whatever sync read it has. If only async is available, change `_campaign_summary_cadence` to `async def` and update the caller to `await`. Verify by grepping:

```bash
grep -n "def fetchone\|async def fetchone" backend/src/grimoire/state_store/store.py backend/src/grimoire/storage/*.py
```

If async-only, here's the async variant of the helper (use this and `await` it from the cadence check site):

```python
    async def _campaign_summary_cadence(self, campaign_id: str) -> int | None:
        store = getattr(self, "_state_store", None)
        if store is None:
            return None
        try:
            row = await store.db.fetchone(
                "SELECT config FROM campaigns WHERE id = ?", (campaign_id,)
            )
        except Exception:
            return None
        if not row:
            return None
        raw = row.get("config") if hasattr(row, "get") else row["config"]
        if not raw:
            return None
        import json as _json

        try:
            data = _json.loads(raw)
        except (TypeError, ValueError):
            return None
        block = data.get("summaries") if isinstance(data, dict) else None
        if not isinstance(block, dict):
            return None
        n = block.get("running_every_n_posts")
        return int(n) if isinstance(n, int) else None
```

And the call site becomes:
```python
            override = await self._campaign_summary_cadence(scene.campaign_id)
```

The unit test calls `_should_emit_running_summary` directly with the override argument, so it doesn't depend on the state-store wiring — leave the test as-is.

**Wiring the state_store into SceneManager:** find the `SceneManager.__init__` (around line 143) and add an optional `state_store` parameter:

```python
    def __init__(
        self,
        *,
        data_root,
        event_bus,
        config: SceneManagerConfig | None = None,
        scene_break_classifier: SceneBreakClassifier | None = None,
        # ... other existing kwargs ...
        state_store: Any = None,
    ) -> None:
        # ... existing init ...
        self._state_store = state_store
```

And wherever `SceneManager(...)` is constructed in `backend/src/grimoire/main.py` and the test fixtures, pass `state_store=container.state_store` (or the test's state store).

To find these call sites:
```bash
grep -rn "SceneManager(" backend/src/grimoire backend/tests --include="*.py" | grep -v "__pycache__"
```

Update each call site to pass `state_store=...`. The tests in `test_summary_cadence.py` don't need it because `_should_emit_running_summary` takes the override directly.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/scenes/test_summary_cadence.py -v`
Expected: 3 passed.

Then sanity-check that existing scene tests still pass:
Run: `cd backend && pytest tests/scenes/ -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/scenes/manager.py backend/src/grimoire/main.py backend/tests/scenes/test_summary_cadence.py
git commit -m "feat(scenes): per-campaign running_every_n_posts override (0 = never)"
```

---

## Task 8: SceneManager honors `final_on_close == false`

**Files:**
- Modify: `backend/src/grimoire/scenes/manager.py` (the `close_scene` method around line 417)
- Test: `backend/tests/scenes/test_summary_cadence.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/scenes/test_summary_cadence.py`:

```python
def test_should_run_final_summary_default(manager: SceneManager) -> None:
    assert manager._should_run_final_summary(final_on_close_override=None) is True


def test_should_run_final_summary_disabled(manager: SceneManager) -> None:
    assert manager._should_run_final_summary(final_on_close_override=False) is False


def test_should_run_final_summary_enabled(manager: SceneManager) -> None:
    assert manager._should_run_final_summary(final_on_close_override=True) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/scenes/test_summary_cadence.py::test_should_run_final_summary_default -v`
Expected: FAIL — no `_should_run_final_summary` method.

- [ ] **Step 3: Write minimal implementation**

Edit `backend/src/grimoire/scenes/manager.py`. Add the helper near `_should_emit_running_summary`:

```python
    def _should_run_final_summary(
        self,
        *,
        final_on_close_override: bool | None,
    ) -> bool:
        """Return True when ``close_scene`` should invoke the final summarizer.

        Default is True; ``False`` skips the LLM call and uses the
        running summary (or empty string) as the final summary.
        """
        return True if final_on_close_override is None else bool(final_on_close_override)
```

Then in `close_scene` (around line 438), change:

```python
            posts = await self.get_posts(scene_id)
            final_summary, key_beats = await self._final_summary(scene, posts)
            scene.final_summary = final_summary
            scene.key_beats = list(key_beats)
```

to:

```python
            posts = await self.get_posts(scene_id)
            final_override = await self._campaign_final_on_close(scene.campaign_id)
            if self._should_run_final_summary(final_on_close_override=final_override):
                final_summary, key_beats = await self._final_summary(scene, posts)
            else:
                final_summary = scene.running_summary or ""
                key_beats = []
            scene.final_summary = final_summary
            scene.key_beats = list(key_beats)
```

Then add `_campaign_final_on_close` (mirrors `_campaign_summary_cadence`):

```python
    async def _campaign_final_on_close(self, campaign_id: str) -> bool | None:
        store = getattr(self, "_state_store", None)
        if store is None:
            return None
        try:
            row = await store.db.fetchone(
                "SELECT config FROM campaigns WHERE id = ?", (campaign_id,)
            )
        except Exception:
            return None
        if not row:
            return None
        raw = row.get("config") if hasattr(row, "get") else row["config"]
        if not raw:
            return None
        import json as _json

        try:
            data = _json.loads(raw)
        except (TypeError, ValueError):
            return None
        block = data.get("summaries") if isinstance(data, dict) else None
        if not isinstance(block, dict):
            return None
        v = block.get("final_on_close")
        return bool(v) if isinstance(v, bool) else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/scenes/test_summary_cadence.py -v`
Expected: 6 passed (3 from task 7 + 3 new).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/scenes/manager.py backend/tests/scenes/test_summary_cadence.py
git commit -m "feat(scenes): per-campaign final_on_close skips final-summary LLM call"
```

---

## Task 9: App-level Heavy / Light defaults in `api/config.py`

**Files:**
- Modify: `backend/src/grimoire/api/config.py`
- Test: `backend/tests/api/test_config_routes.py`

- [ ] **Step 1: Explore the existing config surface**

Run: `grep -n "router.get\|router.put\|class.*Payload\|BaseModel" backend/src/grimoire/api/config.py | head -30`

Look at the file to understand the existing shape — there should be GET/PUT endpoints (e.g., library directory, backup settings). Match the existing structure.

- [ ] **Step 2: Write the failing test**

Add to `backend/tests/api/test_config_routes.py` (create if it doesn't exist; mirror the test_campaign_settings_routes.py fixture shape):

```python
def test_app_defaults_default_empty(config_client) -> None:
    resp = config_client.get("/api/config/llm-defaults")
    assert resp.status_code == 200
    assert resp.json() == {
        "heavy": "deepseek.deepseek-v4-pro",
        "light": "deepseek.deepseek-v4-flash",
    }


def test_app_defaults_round_trip(config_client) -> None:
    resp = config_client.put(
        "/api/config/llm-defaults",
        json={"heavy": "anthropic.opus-5", "light": "anthropic.haiku-5"},
    )
    assert resp.status_code == 200
    assert resp.json()["heavy"] == "anthropic.opus-5"

    resp = config_client.get("/api/config/llm-defaults")
    assert resp.json()["light"] == "anthropic.haiku-5"


def test_app_defaults_bad_route_rejected(config_client) -> None:
    resp = config_client.put(
        "/api/config/llm-defaults",
        json={"heavy": "missingdot", "light": "anthropic.haiku-5"},
    )
    assert resp.status_code == 422
```

If `config_client` doesn't exist as a fixture, copy the `settings_client` fixture from `test_campaign_settings_routes.py` and rename. Both build a `TestClient` over `ServiceContainer`.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && pytest tests/api/test_config_routes.py::test_app_defaults_default_empty -v`
Expected: FAIL with 404.

- [ ] **Step 4: Write minimal implementation**

Edit `backend/src/grimoire/api/config.py`. Read the file first to learn its conventions, then add:

```python
class LLMDefaultsPayload(BaseModel):
    """App-wide default Heavy and Light routes used as the floor for
    new campaigns' ``model_tiers`` block.
    """

    heavy: str = "deepseek.deepseek-v4-pro"
    light: str = "deepseek.deepseek-v4-flash"


_LLM_DEFAULTS_KEY = "llm_defaults"  # key into the app-config dict


@router.get("/llm-defaults")
async def get_llm_defaults(state_store: StateStoreDep) -> Any:
    cfg = await _load_app_config(state_store)
    block = (cfg.get(_LLM_DEFAULTS_KEY) or {}) if isinstance(cfg, dict) else {}
    return {
        "heavy": block.get("heavy") or "deepseek.deepseek-v4-pro",
        "light": block.get("light") or "deepseek.deepseek-v4-flash",
    }


@router.put("/llm-defaults")
async def set_llm_defaults(
    payload: LLMDefaultsPayload,
    state_store: StateStoreDep,
) -> Any:
    from grimoire.llm_gateway.routing import Route

    for value in (payload.heavy, payload.light):
        try:
            Route.parse(value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    cfg = await _load_app_config(state_store)
    if not isinstance(cfg, dict):
        cfg = {}
    cfg[_LLM_DEFAULTS_KEY] = {"heavy": payload.heavy, "light": payload.light}
    await _save_app_config(state_store, cfg)
    return cfg[_LLM_DEFAULTS_KEY]
```

`_load_app_config` and `_save_app_config` should already exist or be similar to helpers in the file. If not, add them as thin wrappers around the existing app-config persistence (look for `app_config` or `settings` SQL table queries near the top of the file).

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest tests/api/test_config_routes.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/api/config.py backend/tests/api/test_config_routes.py
git commit -m "feat(config): app-level default Heavy/Light routes for new campaigns"
```

---

## Task 10: New-campaign creation seeds `model_tiers` from app defaults

**Files:**
- Modify: `backend/src/grimoire/api/campaigns.py` (the `create_campaign` endpoint, around line 270-320)
- Test: `backend/tests/api/test_campaigns_routes.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/api/test_campaigns_routes.py`:

```python
def test_new_campaign_seeds_tiers_from_app_defaults(campaigns_client, tmp_path) -> None:
    # Set app defaults
    resp = campaigns_client.put(
        "/api/config/llm-defaults",
        json={"heavy": "deepseek.pro", "light": "deepseek.flash"},
    )
    assert resp.status_code == 200

    resp = campaigns_client.post(
        "/api/campaigns",
        json={"id": "new-camp", "name": "New", "greeting_id": None, "composition": {"worlds": []}},
    )
    assert resp.status_code in (200, 201)

    # The new campaign should have its model_tiers populated from the
    # app defaults.
    yaml_path = tmp_path / "campaigns" / "new-camp" / "campaign.yaml"
    import yaml

    raw = yaml.safe_load(yaml_path.read_text())
    assert raw["model_tiers"]["heavy"] == "deepseek.pro"
    assert raw["model_tiers"]["light"] == "deepseek.flash"
```

If `campaigns_client` doesn't exist with these features, port the `settings_client` fixture from `test_campaign_settings_routes.py` and adjust.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/api/test_campaigns_routes.py::test_new_campaign_seeds_tiers_from_app_defaults -v`
Expected: FAIL — `model_tiers` missing from new campaign.yaml.

- [ ] **Step 3: Write minimal implementation**

Edit `backend/src/grimoire/api/campaigns.py`. Find the existing `create_campaign` handler (around line 272). Within the `try:` block, after the campaign row is upserted and worlds upserted, before the greeting handoff, add:

```python
        # Seed model_tiers from app defaults so new campaigns get
        # cheap-by-default routing without the user touching settings.
        try:
            from grimoire.llm_gateway.tiers import Tier
            from grimoire.api.config import _load_app_config

            app_cfg = await _load_app_config(state_store)
            defaults = (app_cfg.get("llm_defaults") if isinstance(app_cfg, dict) else {}) or {}
            heavy = defaults.get("heavy") or "deepseek.deepseek-v4-pro"
            light = defaults.get("light") or "deepseek.deepseek-v4-flash"
            await gateway.set_tier_route(payload.id, Tier.HEAVY, heavy)
            await gateway.set_tier_route(payload.id, Tier.LIGHT, light)
        except Exception:
            logger.warning(
                "seeding default model_tiers for new campaign %s failed",
                payload.id,
                exc_info=True,
            )
```

Add `gateway: LLMGatewayDep` to the handler signature if it isn't there already.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/api/test_campaigns_routes.py::test_new_campaign_seeds_tiers_from_app_defaults -v`
Expected: PASS.

Then sanity-check existing campaign-creation tests:
Run: `cd backend && pytest tests/api/test_campaigns_routes.py -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/api/campaigns.py backend/tests/api/test_campaigns_routes.py
git commit -m "feat(campaigns): seed new campaigns with Heavy/Light tier defaults"
```

---

## Task 11: Frontend API client methods

**Files:**
- Modify: `frontend/src/api/campaign.ts`
- Modify: `frontend/src/api/wizard.ts` *(for app-config endpoint if it lives there)*

- [ ] **Step 1: Add the tier + summaries client methods**

Find the existing `editPostBody` method on the `campaignApi` object in `frontend/src/api/campaign.ts` (the file has a campaignApi object with related methods). Add after it:

```typescript
  getTiers: (campaignId: string) =>
    api.get<{ heavy: string | null; light: string | null; embedding: string | null }>(
      `/api/campaigns/${enc(campaignId)}/tiers`,
    ),

  setTiers: (
    campaignId: string,
    body: { heavy: string | null; light: string | null; embedding: string | null },
  ) =>
    api.put<{ heavy: string | null; light: string | null; embedding: string | null }>(
      `/api/campaigns/${enc(campaignId)}/tiers`,
      body,
    ),

  getSummaries: (campaignId: string) =>
    api.get<{ running_every_n_posts: number; final_on_close: boolean }>(
      `/api/campaigns/${enc(campaignId)}/summaries`,
    ),

  setSummaries: (
    campaignId: string,
    body: { running_every_n_posts: number; final_on_close: boolean },
  ) =>
    api.put<{ running_every_n_posts: number; final_on_close: boolean }>(
      `/api/campaigns/${enc(campaignId)}/summaries`,
      body,
    ),
```

- [ ] **Step 2: Add the app-defaults methods**

In whichever file already holds the app-config HTTP client (search with `grep -rn "/api/config" frontend/src/api --include="*.ts"`), add:

```typescript
export async function fetchLLMDefaults(): Promise<{ heavy: string; light: string }> {
  return api.get<{ heavy: string; light: string }>("/api/config/llm-defaults");
}

export async function setLLMDefaults(
  body: { heavy: string; light: string },
): Promise<{ heavy: string; light: string }> {
  return api.put<{ heavy: string; light: string }>("/api/config/llm-defaults", body);
}
```

- [ ] **Step 3: Verify TypeScript still compiles**

Run: `cd frontend && pnpm tsc --noEmit` (or `npm run typecheck`).
Expected: zero errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/campaign.ts frontend/src/api/wizard.ts
git commit -m "feat(api-client): tier, summaries, and app-default routes"
```

---

## Task 12: Frontend — Summaries tab in CampaignSettings

**Files:**
- Modify: `frontend/src/routes/CampaignSettings.tsx`

- [ ] **Step 1: Add the tab to the type + TABS list**

In `CampaignSettings.tsx`, find the existing `Tab` union (the spec already mentions a `Generation` tab — `Summaries` slots next to it). Replace:

```typescript
type Tab =
  | "general"
  | "routing"
  | "imagegen"
  | "mechanics"
  | "narrator"
  | "generation"
  | "storage"
  | "advanced";

const TABS: { id: Tab; label: string }[] = [
  { id: "general", label: "General" },
  { id: "routing", label: "Model routing" },
  { id: "imagegen", label: "ImageGen" },
  { id: "mechanics", label: "Mechanics" },
  { id: "narrator", label: "Narrator" },
  { id: "generation", label: "Generation" },
  { id: "storage", label: "Storage" },
  { id: "advanced", label: "Advanced" },
];
```

with:

```typescript
type Tab =
  | "general"
  | "routing"
  | "imagegen"
  | "mechanics"
  | "narrator"
  | "generation"
  | "summaries"
  | "storage"
  | "advanced";

const TABS: { id: Tab; label: string }[] = [
  { id: "general", label: "General" },
  { id: "routing", label: "Model routing" },
  { id: "imagegen", label: "ImageGen" },
  { id: "mechanics", label: "Mechanics" },
  { id: "narrator", label: "Narrator" },
  { id: "generation", label: "Generation" },
  { id: "summaries", label: "Summaries" },
  { id: "storage", label: "Storage" },
  { id: "advanced", label: "Advanced" },
];
```

- [ ] **Step 2: Add the panel switch entry**

In the `tab-panel` section of the same file, find the existing `{tab === "generation" && <GenerationTab .../>}` line and add immediately after:

```tsx
          {tab === "summaries" && <SummariesTab key={campaignId} campaignId={campaignId} />}
```

- [ ] **Step 3: Add the SummariesTab component**

Add this component definition near the other tab components in `CampaignSettings.tsx` (just below `GenerationTab`):

```tsx
interface SummariesValue {
  running_every_n_posts: number;
  final_on_close: boolean;
}

function SummariesTab({ campaignId }: { campaignId: string }) {
  const { value, setValue, status, error, ready } = useAutoSavedResource<SummariesValue>(
    campaignId,
    "/summaries",
    { running_every_n_posts: 5, final_on_close: true },
  );

  return (
    <div className="settings-form">
      <p className="wizard-step-help">
        Controls how often the running scene summary is regenerated and
        whether a final summary is produced when the scene closes. Set
        <code> Running every N posts </code> to <code>0</code> to disable
        in-scene summaries entirely.
      </p>
      {!ready && <p className="wizard-meta">Loading saved settings…</p>}
      <label className="wizard-field">
        <span>Running summary every N posts</span>
        <input
          type="number"
          min={0}
          max={1000}
          step={1}
          placeholder="5 (default)"
          value={value.running_every_n_posts}
          onChange={(e) => {
            const n = Number(e.target.value);
            setValue((prev) => ({
              ...prev,
              running_every_n_posts: Number.isFinite(n) && n >= 0 ? n : prev.running_every_n_posts,
            }));
          }}
          disabled={!ready}
        />
      </label>
      <label className="wizard-field wizard-field-inline">
        <input
          type="checkbox"
          checked={value.final_on_close}
          onChange={(e) =>
            setValue((prev) => ({ ...prev, final_on_close: e.target.checked }))
          }
          disabled={!ready}
        />
        <span>Generate final summary when scene closes</span>
      </label>
      <SaveIndicator status={status} error={error} />
    </div>
  );
}
```

- [ ] **Step 4: Verify visually**

Run the frontend dev server, open a campaign's settings, click the Summaries tab. The two inputs should render, save on debounce, and the SaveIndicator should show.

Run: `cd frontend && pnpm dev` (or `npm run dev`).
Open `http://localhost:5173/campaigns/<any-id>/settings`, click Summaries tab.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/CampaignSettings.tsx
git commit -m "feat(campaign-settings): Summaries tab for cadence + final-on-close"
```

---

## Task 13: Frontend — RoutingTab redesigned to 3 tier pickers + Advanced expander

**Files:**
- Modify: `frontend/src/routes/CampaignSettings.tsx` (the existing `RoutingTab`)

- [ ] **Step 1: Read the existing RoutingTab**

Find the existing `function RoutingTab({ campaignId }: { campaignId: string }) {` in `CampaignSettings.tsx`. Note what it does today (renders the LLM / Embedding / ImageGen task-route tables).

- [ ] **Step 2: Build the new tiered RoutingTab**

Replace the existing `RoutingTab` function body with a layout that has three tier pickers stacked on top, then a `<details>` expander wrapping the existing task tables (which gets renamed to `RoutingTabAdvanced`).

```tsx
interface TiersValue {
  heavy: string | null;
  light: string | null;
  embedding: string | null;
}

function RoutingTab({ campaignId }: { campaignId: string }) {
  const { value, setValue, status, error, ready } = useAutoSavedResource<TiersValue>(
    campaignId,
    "/tiers",
    { heavy: null, light: null, embedding: null },
  );

  return (
    <div className="settings-form">
      <p className="wizard-step-help">
        Heavy handles generation (narrator, summaries, rewrites). Light
        handles classification and short transforms (drift checks,
        scene-break, translate). Embedding handles vector embeddings.
        Leave a field blank to use the app-wide default.
      </p>
      {!ready && <p className="wizard-meta">Loading saved settings…</p>}
      <label className="wizard-field">
        <span>Heavy model</span>
        <input
          type="text"
          placeholder="e.g. deepseek.deepseek-v4-pro"
          value={value.heavy ?? ""}
          onChange={(e) =>
            setValue((prev) => ({ ...prev, heavy: e.target.value.trim() || null }))
          }
          disabled={!ready}
        />
      </label>
      <label className="wizard-field">
        <span>Light model</span>
        <input
          type="text"
          placeholder="e.g. deepseek.deepseek-v4-flash"
          value={value.light ?? ""}
          onChange={(e) =>
            setValue((prev) => ({ ...prev, light: e.target.value.trim() || null }))
          }
          disabled={!ready}
        />
      </label>
      <label className="wizard-field">
        <span>Embedding model</span>
        <input
          type="text"
          placeholder="e.g. voyage.voyage-3"
          value={value.embedding ?? ""}
          onChange={(e) =>
            setValue((prev) => ({ ...prev, embedding: e.target.value.trim() || null }))
          }
          disabled={!ready}
        />
      </label>
      <SaveIndicator status={status} error={error} />
      <details className="routing-advanced">
        <summary>Advanced: per-task overrides</summary>
        <RoutingTabAdvanced campaignId={campaignId} />
      </details>
    </div>
  );
}

function RoutingTabAdvanced({ campaignId }: { campaignId: string }) {
  // Body of the OLD RoutingTab moves here verbatim.
  // ... (existing per-task route tables) ...
}
```

Move the entire body of the old `RoutingTab` into `RoutingTabAdvanced` — same imports, same hooks, same JSX. The only change is the function name.

- [ ] **Step 3: Add minimal CSS for the expander**

Add to `frontend/src/index.css` (anywhere near the existing `.settings-form` styles):

```css
.routing-advanced {
  margin-top: var(--space-4);
  padding: var(--space-3);
  border: 1px dashed var(--border-subtle);
  border-radius: var(--radius-md);
}

.routing-advanced summary {
  cursor: pointer;
  font-weight: 600;
  padding: var(--space-1) 0;
}
```

- [ ] **Step 4: Verify visually**

Run the frontend dev server, open Routing tab, confirm:
- Three tier inputs at top (Heavy, Light, Embedding) save on debounce.
- Below: an expander labeled "Advanced: per-task overrides" that opens the old task tables.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/CampaignSettings.tsx frontend/src/index.css
git commit -m "feat(campaign-settings): tier-first Routing tab with Advanced per-task expander"
```

---

## Task 14: Frontend — AppSettings Heavy / Light default pickers

**Files:**
- Modify: `frontend/src/routes/AppSettings.tsx`

- [ ] **Step 1: Read the existing AppSettings**

Inspect `AppSettings.tsx` to learn its layout (likely a tabs or sections structure). Note where to slot a new "LLM defaults" panel.

Run: `grep -n "function\|<section\|<details\|<h" frontend/src/routes/AppSettings.tsx | head -30`

- [ ] **Step 2: Add the LLM defaults section**

Place this near other app-level forms (model-routing-related sections preferred). If AppSettings is a single component with multiple `<section>` children, add a new section:

```tsx
function LLMDefaultsSection() {
  const [heavy, setHeavy] = useState("deepseek.deepseek-v4-pro");
  const [light, setLight] = useState("deepseek.deepseek-v4-flash");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  useEffect(() => {
    void fetchLLMDefaults()
      .then((d) => {
        setHeavy(d.heavy);
        setLight(d.light);
      })
      .catch(() => {
        // first-run / empty config → keep the seeded UI defaults
      });
  }, []);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const updated = await setLLMDefaults({ heavy, light });
      setHeavy(updated.heavy);
      setLight(updated.light);
      setSavedAt(Date.now());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="settings-section">
      <h3>LLM defaults</h3>
      <p className="wizard-step-help">
        New campaigns get these as their Heavy and Light tier routes.
        Existing campaigns are unaffected. Format is{" "}
        <code>provider.model</code>.
      </p>
      <label className="wizard-field">
        <span>Heavy (generation)</span>
        <input
          type="text"
          value={heavy}
          onChange={(e) => setHeavy(e.target.value)}
        />
      </label>
      <label className="wizard-field">
        <span>Light (classification)</span>
        <input
          type="text"
          value={light}
          onChange={(e) => setLight(e.target.value)}
        />
      </label>
      <button
        type="button"
        onClick={() => void save()}
        disabled={saving}
        className="primary"
      >
        {saving ? "Saving…" : "Save"}
      </button>
      {savedAt && <p className="wizard-meta">Saved.</p>}
      {error && (
        <p className="wizard-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
```

Add it to the rendered tree of `AppSettings` and import `fetchLLMDefaults`, `setLLMDefaults` from `../api/wizard` (or wherever you placed them in task 11).

- [ ] **Step 3: Verify visually**

Open `/settings` in the dev server. Confirm the LLM defaults section renders with the two inputs (preloaded with the shipped defaults), the Save button persists changes, and reloading the page shows the saved values.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/AppSettings.tsx
git commit -m "feat(app-settings): default Heavy/Light routes used by new campaigns"
```

---

## Task 15: Smoke test end-to-end

**Files:** none (manual verification).

- [ ] **Step 1: Restart the backend** so all changes load.

- [ ] **Step 2: Verify the routing chain manually**

In the backend, with a campaign that has `model_tiers.heavy = "deepseek.deepseek-v4-pro"` set and no per-task override for `main`, hit:

```bash
curl http://127.0.0.1:8173/api/campaigns/<id>/tiers
```

Should return the heavy/light/embedding triple.

- [ ] **Step 3: Verify summary cadence**

Set `running_every_n_posts` to 0 via the UI Summaries tab. Post 6 turns. Confirm no `RUNNING_SUMMARY_DUE` event fires (check backend logs).

- [ ] **Step 4: Verify a new campaign gets defaults**

Create a new campaign via the wizard. Open its `campaign.yaml` on disk; the `model_tiers` block should contain the app default Heavy / Light values.

- [ ] **Step 5: Commit nothing** — this is verification only. Note any issues and circle back to the relevant task.

---

## Final check

- [ ] All backend tests green:

```bash
cd backend && pytest -x
```

- [ ] All frontend tests green:

```bash
cd frontend && pnpm test   # or npm test
```

- [ ] TypeScript compiles:

```bash
cd frontend && pnpm tsc --noEmit
```

- [ ] PR description ready (link to `docs/superpowers/specs/2026-05-23-llm-tiering-design.md` §1, §3, §4, §5, §6).

---

## What's NOT in this plan

- **Integrated narrator + extraction call** — PR 2, separate plan. Spec §2 + §10 PR 2.
- **Observability events** (`tier_resolved`, `summary_skipped`, `integrated_deltas_fallback`) — spec §7. Defer to PR 2 alongside `integrated_deltas_fallback` so all three land together.
- **Per-task route migration** — keep behavior unchanged for existing campaigns. They opt in by setting `model_tiers` themselves.
