# World — Design (Shipped)

> Captures the World module design as actually built. The matching "remaining" spec at `2026-05-16-world-remaining-design.md` covers everything from the original `specs/09-world.md` that did **not** land in this work.

**Commit:** `c93fb75` — "Implement Setting module (task 11)" (renamed to "world" in `87e0643`)
**Module:** `backend/src/grimoire/world/`
**Tests:** `backend/tests/world/{test_service.py,test_calendar.py,test_weather.py}`

## Purpose

World is a thin behavior layer on top of `LibraryService`. The library owns markdown + YAML IO, the SQLite `library_index`, and the resolution cascade (emergent → override → snapshot → live). World adds the per-campaign behaviors a generic library can't provide:

- Composition-aware listing with `include` filters
- Spatial queries over locations (`adjacent_locations`, `path_between`, `locations_within`)
- Cross-world variant lookup by shared `asset_id`
- Lore keyword triggers for archive-tier injection
- Deterministic per-campaign procedural weather, with a campaign-local override
- Calendar / season / holiday queries
- Campaign-scoped faction state (CRUD over the `faction_state` SQLite table)
- World fork (deep directory copy + reindex through the library writer)
- Promotion of non-character emergent entities into a library world

Character behaviors (voice, drift, tier, PC role) live in the Characters module and layer over the same Library storage; World refuses character writes on purpose (`create_entity` raises if `kind == "character"`, `service.py:211-212`).

## Module surface

`WorldService` (`world/service.py:80`) is constructed with a `LibraryService` and reuses its `StateStore`:

```python
class WorldService:
    def __init__(self, library: LibraryService) -> None:
        self.library = library
        self.store: StateStore = library.store
```

The service is otherwise stateless; per-campaign behaviors derive from inputs. Owned entity kinds are gated by `_OWNED_KINDS = {"item", "location", "lore", "faction", "greeting"}` (`service.py:61`); a small `_normalize_kind` helper at `service.py:64` accepts both `EntityKind` enums and the plural directory names (`items`, `locations`, …) so callers don't have to care which they hold.

Supporting modules:

- `world/calendar.py` — `parse_calendar`, `season_for`, `holiday_at`, plus a hemisphere-default season fallback when a world has no seasons configured
- `world/weather.py` — `generate_weather` (deterministic), bias tables for climates, hourly seeding via `blake2b(campaign|location|hour)`
- `world/errors.py` — `WorldError`, `WorldNotFoundError`, `CompositionError`

Typed projections (`Location`, `Item`, `Faction`, `LoreEntry`, `WorldCalendar`, `Weather`, `FactionStateData`, …) live in `backend/src/grimoire/types/world.py`.

## Public API

```python
class WorldService:
    # World management
    async def list_worlds() -> list[WorldMeta]
    async def get_world(world_id) -> WorldMeta
    async def create_world(world_id, meta=None) -> WorldMeta
    async def update_world_meta(world_id, patch) -> WorldMeta
    async def delete_world(world_id) -> None
    async def fork_world(src_world_id, dst_world_id) -> WorldMeta

    # Generic per-kind CRUD (characters rejected; delegates to LibraryService)
    async def list_in_world(world_id, kind) -> list[LibraryEntity]
    async def get_entity(world_id, kind, entity_id) -> LibraryEntity
    async def create_entity(world_id, kind, entity_id, frontmatter, body="", *, source="user") -> LibraryEntity
    async def update_entity(world_id, kind, entity_id, frontmatter_patch=None, body=None, *, source="user") -> LibraryEntity
    async def delete_entity(world_id, kind, entity_id, *, source="user") -> None

    # Typed projections (convenience wrappers over LibraryEntity)
    async def list_locations(world_id) -> list[Location]
    async def get_location(world_id, entity_id) -> Location
    async def list_items(world_id) -> list[Item]
    async def list_lore(world_id) -> list[LoreEntry]
    async def list_factions(world_id) -> list[Faction]

    # Per-campaign resolution
    async def resolve(entity_ref, campaign_id) -> ResolvedEntity
    async def list_for_campaign(campaign_id, kind) -> list[LibraryEntity]

    # Spatial
    async def adjacent_locations(world_id, location_id, campaign_id=None) -> list[Location]
    async def path_between(world_id, src_id, dst_id) -> list[LocationConnection]
    async def locations_within(world_id, parent_id, depth=1) -> list[Location]

    # Cross-world variants
    async def cross_world_lookup(asset_id, kind, exclude_world=None) -> list[LibraryEntity]

    # Lore
    async def search_lore(query, campaign_id, top_k=5) -> list[LoreEntry]
    async def lore_by_keyword(keyword, campaign_id, *, min_length=4) -> list[LoreEntry]
    async def lore_for_post(text, campaign_id, *, min_length=4, max_results=5) -> list[LoreEntry]

    # Greetings (library-only)
    async def list_greetings(world_id) -> list[Greeting]
    async def get_greeting(world_id, greeting_id) -> Greeting

    # Calendar
    async def calendar_for(world_id) -> WorldCalendar
    async def calendar_for_campaign(campaign_id) -> WorldCalendar
    async def season_for(when, campaign_id) -> Season | None
    async def holiday_at(when, campaign_id) -> Holiday | None

    # Weather
    async def weather_for(world_id, location_id, when, campaign_id, *, branch_id=None) -> Weather
    async def override_weather(world_id, location_id, weather, campaign_id, *, branch_id=None, source="user") -> None

    # Faction state (campaign-scoped, SQLite)
    async def faction_state(faction_ref, campaign_id, branch_id=None) -> FactionStateData
    async def update_faction_state(faction_ref, campaign_id, patch, *, branch_id=None, source="user", turn_id=None) -> FactionStateData

    # Composition surfacing (delegates)
    async def get_composition(campaign_id) -> Composition
    async def upgrade_world_ref(campaign_id, world_id) -> UpgradeReport

    # Promotion (non-character kinds)
    async def promote_to_library(campaign_id, kind, campaign_entity_id, target_world_id, *, source="user") -> str
```

## What World stores vs. delegates

| Concern | Owner | Notes |
|---|---|---|
| `world.yaml` IO + `world_meta` row | `LibraryService` | World wraps with `update_world_meta` (round-trips through `create_world` because the YAML write is an upsert) |
| Per-kind library file CRUD | `LibraryService` | World gates on `_OWNED_KINDS` and forbids `character` |
| Resolution cascade | `LibraryService.resolve` | World re-exports `resolve()` and `list_for_campaign()` as a one-line passthrough |
| Composition filters | `LibraryService.list_for_composition` | World wraps for the typed listing methods |
| Variant lookup by `asset_id` | `LibraryService.variants_of` | World adds `exclude_world` filtering |
| Spatial graph | `WorldService` | Pure in-memory BFS / parent-chain over `Location.connections` |
| Lore keyword scan | `WorldService` | Linear scan over composition-filtered entities; FTS is `StateStore.keyword_search` and is not used here yet |
| Calendar / season / holiday | `WorldService` + `world/calendar.py` | Calendar parsed on demand from `WorldMeta.calendar` (no caching) |
| Weather generation | `world/weather.py` | Deterministic, no persistence; override is the only durable record |
| Weather override | `location_state.weather` column | Stored as JSON `{...Weather.model_dump(), "source": "override"}`; bypasses `apply_delta` because the call only touches one column |
| Faction state | `faction_state` SQLite table | One row per `(faction_ref, branch_id)`, state column is a JSON blob |
| World fork | `WorldService.fork_world` | `shutil.copytree` + `_reindex_world_dir` walks files and re-issues `write_library_file` so every row gets a delta entry |
| Promotion | `LibraryService.promote_to_library` | World rejects `character` (returned via Characters module instead) |

## Composition-aware listing

`list_for_campaign(campaign_id, kind)` delegates to `LibraryService.list_for_composition`, which walks the campaign's `campaign_world_refs` in priority order and applies each ref's `include` filter (`None` = include every kind, `[]` = include nothing from this world, list = include only those kinds — see `types/composition.py:14-22` and the explicit comment).

The typed listing helpers (`list_locations`, `list_items`, `list_lore`, `list_factions`) are per-world only (no composition); the composition path is `list_for_campaign(...)` returning untyped `LibraryEntity` rows.

## Spatial queries

`adjacent_locations(world_id, location_id)` returns the location's `parent_id` plus every connection target that resolves in the index. Missing targets are silently skipped (`WorldNotFoundError` caught at `service.py:304,313`). `campaign_id` is accepted for API symmetry but currently unused — the cascade is per-world.

`path_between(world_id, src_id, dst_id)` is a plain BFS over the in-world location set: load every location once, walk `connections.to`, return the list of `LocationConnection` edges traversed. Same-node returns `[]`; no route returns `[]`.

`locations_within(world_id, parent_id, depth=1)` returns descendants up to `depth` levels using the `parent_id` chain. `depth=1` is direct children; `depth=2` adds grandchildren; etc.

Cross-world `parent_id` is unsupported (consistent with the spec's "hierarchy is within a world").

## Lore keyword triggers

Three entry points, all composition-aware:

- `lore_by_keyword(keyword, campaign_id, min_length=4)` — exact match against any entry's `keywords` list (case-insensitive). Short keywords short-circuit to `[]`.
- `lore_for_post(text, campaign_id, min_length=4, max_results=5)` — scans a post body for any configured `keywords` substring; returns the first `max_results` triggered entries. Used by the Context Builder to inject archive-tier lore.
- `search_lore(query, campaign_id, top_k=5)` — substring scoring across `title` (3.0), exact keyword match (2.0), partial keyword (1.0), body (1.0), tags (0.5). No FTS yet; this is the convenience method that respects composition filters so that excluded worlds don't leak content.

`min_length` is hard-coded at 4 (matches spec default); the spec's `world.lore.keyword_min_length` config knob is not wired (see remaining doc §3).

## Calendar

`calendar_for(world_id)` parses `WorldMeta.calendar` on every call (no caching). The parser tolerates loose YAML (`calendar.py:23`): missing keys become defaults, `months: []` is allowed, season `start_month`/`start_day` default to `(1, 1)`.

`season_for(calendar, when)` interprets seasons as starts only: each season runs from its `(start_month, start_day)` until the next season's start, wrapping around the year. When no seasons are configured, falls back to Gregorian hemisphere defaults (`_hemisphere_default` at `calendar.py:104`) so callers always get *something*.

`calendar_for_campaign(campaign_id)` reads the composition and returns the highest-priority world's calendar; raises `CompositionError` if the campaign has no world refs. The spec's `composition.multiple_calendars_policy` setting is not implemented — behavior is `pick` only.

## Weather

`generate_weather(campaign_id, location_ref, when, calendar, climate_zone, indoor=False)` (`weather.py:64`) is deterministic per `(campaign_id, location_ref, hour)`:

1. Indoor → always `WeatherKind.CLEAR` with empty summary, `source="procedural"`
2. Hour bucket = `YYYY-MM-DDTHH` (so weather is stable for a scene)
3. Seed = `blake2b(f"{campaign_id}|{location_ref}|{bucket}", digest_size=8)` → `random.Random(seed)`
4. Bias = `_DEFAULT_BIAS` overlaid with `season.weather_bias` and `_CLIMATE_TWEAKS[climate_zone]` (temperate-oceanic / desert / arctic / tropical)
5. Weighted pick over kinds; summary phrase + season name; temperature derived from season + climate + kind with ±2°C jitter
6. Humidity and wind sampled from the same RNG

`WorldService.weather_for(...)` checks `location_state.weather` first; if a row exists with `{"source": "override"}` payload, that wins. Otherwise it loads the location (best-effort — missing locations fall back to no climate / outdoor) and calls `generate_weather`. `branch_id` defaults to `f"{campaign_id}:main"`.

`override_weather` writes the override JSON into `location_state.weather` via `INSERT ... ON CONFLICT(location_ref, branch_id) DO UPDATE`. It deliberately bypasses `apply_delta` because the operation only touches one column (the rest of the `LocationState` schema isn't populated here — by design). The `source` kwarg is accepted but unused today; everything is tagged `"source": "override"` in the payload.

## Faction state

`faction_state(faction_ref, campaign_id, branch_id=None)` returns a `FactionStateData` decoded from the JSON `state` column of `faction_state`. Missing rows return a blank record (no SQL row created until `update_faction_state`).

`update_faction_state(faction_ref, campaign_id, patch, ...)` performs read-modify-write:

1. Fetch current state (blank if missing)
2. Merge `patch` over `model_dump()`; `goals` accepts both dicts and `FactionGoal` instances
3. Persist `{goals, resources, current_focus, public_perception, secrets}` via `INSERT ... ON CONFLICT(faction_ref, branch_id) DO UPDATE`
4. Return the re-fetched record

`source` is accepted but not threaded into a delta log entry (the write goes direct to SQLite; the spec's "Faction state changes during time ticks via Time Engine" hook is not implemented here).

## World fork

`fork_world(src_world_id, dst_world_id)`:

1. Validate source exists, destination free (raises `WorldNotFoundError` / `WorldError` respectively)
2. `shutil.copytree(src_root, dst_root)` — fast deep copy of the world directory
3. `_reindex_world_dir(dst_world_id)`: walks the copied tree and re-issues `write_library_file(...)` for `world.yaml` and every `<kind>/<id>.md` so every row gets a fresh delta entry and proper version numbers under the new world id

The forked world is fully independent — no shared references back to the source.

## Promotion (non-character kinds)

`promote_to_library(campaign_id, kind, campaign_entity_id, target_world_id)` rejects `character` and forwards the rest to `LibraryService.promote_to_library`. The library is the one that reads the emergent record, renders it to markdown + YAML, writes the file under `<library>/worlds/<target>/<kind>/<id>.md`, and updates the index. The world's job here is just the routing + character refusal.

## Error handling

- `WorldNotFoundError` from `get_world` / `get_location` / `get_entity` (raised by `LibraryService`); also from `fork_world` when source is missing
- `WorldError` for "destination world already exists" and for the explicit refusals (`character` kind on create / promote; unknown kind on create)
- `CompositionError` from `calendar_for_campaign` when a campaign has no world refs
- Best-effort fallthroughs: spatial queries silently skip missing locations; weather generation tolerates a missing location row by defaulting to no climate / outdoor; weather override JSON-parse failure returns `None` (procedural takes over)

## Test wiring

`backend/tests/world/conftest.py` builds a `WorldService` over an in-process SQLite `Database` (with full migrations applied) and a `StateStore` rooted at `tmp_path`. Tests are async (`pytest-asyncio`) and exercise the service through its public API — there are no fakes; the library/state store are real.

Coverage hot-spots:

- `test_service.py` — CRUD per kind, character-rejection, spatial queries (adjacent / path / within-depth), composition filters, cross-world variants, lore triggers (keyword, scan-post, scored search), calendar + season + holiday, weather determinism / per-location variance / indoor / override, faction state round-trip, fork (copy + reindex + collision + missing source), promotion (location promotion + character refusal)
- `test_calendar.py` — parser normalization, season wrap-around, hemisphere fallback, holiday lookup
- `test_weather.py` — determinism, hour variation, indoor invariant, climate-zone distribution shifts (50-sample arctic/desert checks)
