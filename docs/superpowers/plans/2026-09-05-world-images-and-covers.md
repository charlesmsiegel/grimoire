# World Images and Covers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a world a cover image and an image library of its own, and let
every campaign on that world see the library's images, hide the ones it does not
want, and get them back.

**Architecture:** Policy is extracted into a new scope-free `store/image_library.py`
while each scope module keeps its own `assets.put_in`/`assets.delete_in` call (a
lock-domain guard requirement, not a style choice). `store/world_images.py` owns
the world's directory; `store/campaign_images.py` is rewritten as a read-through
view over it — campaign files first, then a tombstone check, then the world.
A campaign may hide an inherited image but **never replace one under the same
name**; there is no shadowing anywhere in this design.

**Tech Stack:** FastAPI + pytest (backend), Vite/React + vitest (frontend), Pillow
for the cover decode only.

**Spec:** `docs/superpowers/specs/2026-09-04-world-images-and-covers-design.md`
(revision 4). The plan argues from the spec; read both.

## Global Constraints

- **Run the gate with `make check`.** In this worktree pass `PY` explicitly:
  `make check-py PY=C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe`.
  Run vitest **from** `frontend/`, never from the repo root.
- **Baseline is red before you start:** 7 failed, 8550 passed, 18 skipped, 10
  errors. Known-failing: `test_changes_store.py::test_the_diff_always_reconstructs_both_sides`
  (10 errors), `test_routing_routes`, `test_pending_reviews_store`, `test_logs_store`,
  `test_first_run`, `test_backups_store`, `test_atomic`, and
  `test_campaign_images_store.py::test_a_name_this_store_would_not_serve_back_is_refused_and_hidden[map*]`
  (a Windows-only test bug: it creates a file literally named `map*.png`).
  **Do not "fix" these and do not count them as yours.**
- **Never commit anything from `~/.grimoire`**, and never use a real world,
  campaign or character name in a test or a commit message. Use the existing
  placeholders: Realm, Saltmarch, Seraphine, Mara, Winifred.
- **Every store write goes through `store.atomic`** (`test_atomic_guard.py`).
- **Imports in `store/` bind the submodule**, never a leaf function:
  `from .worlds import paths as worlds_paths`, then `worlds_paths.world_exists(...)`.
  Never `from .worlds.paths import world_exists` (`test_import_guard.py`).
- **pydantic stays v1/v2-agnostic**: plain `BaseModel` fields, dump via
  `routes.common._dump`.
- **Marker budgets are full.** `overlay-ok` 4/4, `lock-domain-ok` 2/2,
  `routing-ok` 3/3, `atomic-ok` 2/3. Task 11 raises the `overlay-ok` cap
  deliberately; do not raise any other cap without saying why in the commit.
- **Commit after every task.** Small commits, present tense, no "fix" or "wip".

---

### Task 1: Extract the scope-free policy into `store/image_library.py`

Pure refactor. `campaign_images` keeps every public name it has today and every
behaviour; the constants and predicates move one file over. **`put`/`delete` do
NOT move** — `test_lock_domain_guard.py:306` recognizes a mutating module by its
`assets.put_in`/`assets.delete_in` call sites, and moving them would drop
`store.covers` and `store.campaign_images` out of the survey.

**Files:**
- Create: `backend/src/grimoire/store/image_library.py`
- Modify: `backend/src/grimoire/store/campaign_images.py`
- Modify: `backend/src/grimoire/store/__init__.py`
- Test: `backend/tests/test_image_library_store.py`

**Interfaces:**
- Consumes: `store.assets.list_in`, `store.assets.storable`.
- Produces: `image_library.MAX_BYTES: int`, `TOO_LARGE: str`,
  `UNADDRESSABLE: frozenset[str]`, `RESERVED: frozenset[str]`,
  `ImageTooLarge(Exception)`, `validate_size(data: bytes) -> None`,
  `addressable(name: str) -> bool`, `listing(d: Path) -> list[dict]`
  (`[{"name","ext","v"}]`, addressable entries only).
  `campaign_images` re-exports `MAX_BYTES`, `TOO_LARGE`, `ImageTooLarge`,
  `validate_size` and `addressable` so `routes/campaigns.py` keeps working
  untouched in this task.

- [ ] **Step 1: Write the failing test**

```python
"""The scope-free half of an image library (store/image_library.py)."""

import io

import pytest
from PIL import Image

from grimoire.store import image_library


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


@pytest.mark.parametrize("name", ["map", "coast-line", "carte_du_monde", "地図"])
def test_a_name_a_link_can_carry_is_addressable(name):
    assert image_library.addressable(name)


@pytest.mark.parametrize("name", ["my map", "map(1)", "a#b", "a?b", "und'quote",
                                  "undescribed", "Undescribed", "promote-tmp"])
def test_a_name_a_link_or_a_route_cannot_carry_is_not(name):
    assert not image_library.addressable(name)


def test_validate_size_refuses_only_past_the_cap():
    image_library.validate_size(b"x" * 10)
    with pytest.raises(image_library.ImageTooLarge):
        image_library.validate_size(b"x" * (image_library.MAX_BYTES + 1))


def test_listing_reports_addressable_images_and_ignores_strays(tmp_path):
    (tmp_path / "map.png").write_bytes(_png())
    (tmp_path / "notes.txt").write_text("not ours")
    (tmp_path / "my map.png").write_bytes(_png())   # unaddressable: never offered

    rows = image_library.listing(tmp_path)
    assert [r["name"] for r in rows] == ["map"]
    assert rows[0]["ext"] == "png" and rows[0]["v"]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && PYTHONPATH=src <PY> -m pytest tests/test_image_library_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'grimoire.store.image_library'`

- [ ] **Step 3: Create the module by moving, not retyping**

Move these from `campaign_images.py` **verbatim, with their docstrings and
comments intact** — they carry the reasoning and it must not be lost:
`MAX_BYTES`, `TOO_LARGE`, `UNADDRESSABLE`, `RESERVED`, `ImageTooLarge`,
`validate_size`, `addressable`. Add:

```python
def listing(d: Path) -> list[dict]:
    """``[{"name", "ext", "v"}, ...]`` for every image a link could carry.

    `assets.list_in`'s newest-wins enumeration, filtered by `addressable` — the
    same conjunction `put` gates on, because offering a tile whose insert 404s
    is #373 wearing a different hat.
    """
    return [i for i in assets.list_in(d) if addressable(i["name"])]
```

The module docstring says what it is and, load-bearingly, why the writes are not
here:

```python
"""An image library's rules, with no idea whose library it is.

The half of `store.campaign_images` that never needed a campaign: what a name
may be, how big an upload may get, and how a flat directory enumerates.
`store.world_images` and `store.campaign_images` are the two scopes over it.

**`put` and `delete` are deliberately NOT here.** `tests/test_lock_domain_guard.py`
recognizes a mutating module by its `assets.put_in`/`assets.delete_in` call
sites (`_ASSETS_WRITERS`), and mutation does not propagate across an import — so
a scope module that wrote through this one would silently leave the lock
domain's survey, taking the guard's grip on it along. Each scope keeps its own
two-line write, next to the lock it is taken under.
"""
```

- [ ] **Step 4: Rewrite `campaign_images` to import them**

Replace the moved definitions with `from . import image_library` plus explicit
re-exports, so `routes/campaigns.py` needs no edit in this task:

```python
MAX_BYTES = image_library.MAX_BYTES
TOO_LARGE = image_library.TOO_LARGE
ImageTooLarge = image_library.ImageTooLarge
validate_size = image_library.validate_size
addressable = image_library.addressable
```

Change `list_images` to `return image_library.listing(images_dir(cid))`.

- [ ] **Step 5: Register the module**

Add `image_library` to the import list and `"image_library"` to `__all__` in
`store/__init__.py`, keeping both alphabetical.

- [ ] **Step 6: Run the new test and the two existing suites**

Run: `cd backend && PYTHONPATH=src <PY> -m pytest tests/test_image_library_store.py tests/test_campaign_images_store.py tests/test_store_api_baseline.py -q`
Expected: the new file PASSES; `test_campaign_images_store` still shows exactly
its one known Windows failure (`[map*]`) and nothing else;
`test_store_api_baseline` FAILS — that is Step 7.

- [ ] **Step 7: Regenerate the frozen facade roster**

The baseline compares both `store.__all__` and the public names in `dir(store)`,
and the second already lists modules nothing re-exports — so it fails on the
import alone. Regenerate it as the reviewed act it is meant to be, and read the
diff before committing: it must show `image_library` added and **nothing else**.

Run: `cd backend && PYTHONPATH=src <PY> -m pytest tests/test_store_api_baseline.py -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/src/grimoire/store/image_library.py backend/src/grimoire/store/campaign_images.py \
        backend/src/grimoire/store/__init__.py backend/tests/test_image_library_store.py \
        backend/tests/store_api_baseline.json
git commit -m "An image library's rules, with no idea whose library it is"
```

---

### Task 2: World faces on `store/covers.py`

**Files:**
- Modify: `backend/src/grimoire/store/covers.py`
- Test: `backend/tests/test_covers_store.py`

**Interfaces:**
- Consumes: `worlds.paths.world_root`, `worlds.paths.world_exists`, `assets.*`.
- Produces: `covers.world_cover_path(wid) -> Path | None`,
  `covers.world_cover_version(wid) -> str`,
  `covers.put_world_cover(wid, data: bytes, ext: str) -> str`,
  `covers.delete_world_cover(wid) -> None`. `validate(data) -> str` is unchanged
  and shared. The existing campaign faces keep their names exactly.

- [ ] **Step 1: Write the failing test**

```python
def test_a_world_cover_round_trips_and_is_confirmed_on_removal(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")

    assert covers.world_cover_path(wid) is None
    assert covers.world_cover_version(wid) == ""

    data = _png()
    assert covers.put_world_cover(wid, data, covers.validate(data)) == "png"
    p = covers.world_cover_path(wid)
    assert p is not None and p.read_bytes() == data
    assert p == worlds.world_root(wid) / "assets" / "cover.png"
    assert covers.world_cover_version(wid)

    covers.delete_world_cover(wid)
    assert covers.world_cover_path(wid) is None


def test_a_cover_for_a_world_that_is_not_there_creates_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    with pytest.raises(worlds.WorldNotFound):
        covers.put_world_cover("nope", _png(), "png")
    assert not (tmp_path / "worlds" / "nope").exists()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && PYTHONPATH=src <PY> -m pytest tests/test_covers_store.py -q`
Expected: FAIL — `AttributeError: module 'grimoire.store.covers' has no attribute 'world_cover_path'`

- [ ] **Step 3: Implement the world faces**

Refactor the campaign faces onto a shared root-taking core, then add the world
faces. The world faces take **no lock** — worlds have no lock domain — and each
keeps its own `assets` write call:

```python
def _world_assets_dir(wid: str) -> Path:
    """``<world>/assets``, after proving the world is actually there.

    `world_root` is a syntax guard, not an existence check. Without this, a put
    for an unknown id would create a world directory holding an image and no
    `world.md` — the #360/#373 shape, reported to the caller as a success.
    """
    if not worlds_paths.world_exists(wid):
        raise worlds_paths.WorldNotFound(wid)
    return worlds_paths.world_root(wid) / "assets"


def world_cover_path(wid: str) -> Path | None:
    return assets.path_in(_world_assets_dir(wid), NAME, supported_only=True)


def world_cover_version(wid: str) -> str:
    p = world_cover_path(wid)
    if p is None:
        return ""
    try:
        return assets.image_version(p)
    except OSError:
        return ""


def put_world_cover(wid: str, data: bytes, ext: str) -> str:
    """No lock: worlds have no lock domain at all, and `focus.json` and
    `subjects.json` race there in exactly the same way (`overlay.set_description`
    names this asymmetry). Not an oversight this closes."""
    return assets.put_in(_world_assets_dir(wid), NAME, data, ext, supported_only=True)


def delete_world_cover(wid: str) -> None:
    d = _world_assets_dir(wid)
    assets.delete_in(d, NAME, supported_only=True)
    if assets.path_in(d, NAME, supported_only=True) is not None:
        raise OSError("cover could not be removed")
```

- [ ] **Step 4: Run the covers suite and the lock-domain guard**

Run: `cd backend && PYTHONPATH=src <PY> -m pytest tests/test_covers_store.py tests/test_lock_domain_guard.py -q`
Expected: PASS. The guard must still survey `store.covers` — if it reports
`store.covers` as a phantom, an `assets` write call was refactored away; put it
back rather than editing `locks.py`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/covers.py backend/tests/test_covers_store.py
git commit -m "A world can have a cover too"
```

---

### Task 3: `store/world_images.py`

The sweep on delete is **not** in this task — it needs Task 4's public tombstone
door. `delete_image` here removes bytes only; Task 5 wires the sweep in.

**Files:**
- Create: `backend/src/grimoire/store/world_images.py`
- Modify: `backend/src/grimoire/store/__init__.py`
- Test: `backend/tests/test_world_images_store.py`

**Interfaces:**
- Consumes: `image_library.*`, `assets.*`, `image_descriptions.*`, `worlds.paths`.
- Produces: `images_dir(wid) -> Path`, `list_images(wid) -> list[dict]`,
  `image_path(wid, name) -> Path | None`, `image_version(wid, name) -> str`,
  `put_image(wid, name, data, ext) -> str`, `delete_image(wid, name) -> None`,
  `read_descriptions(wid) -> dict[str, str]`,
  `set_description(wid, name, text) -> None`,
  `undescribed(wid) -> list[dict]` (`[{"name"}]`, sorted),
  `undescribed_count(wid) -> int`, `has_undescribed(wid) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
def test_put_list_serve_delete_round_trip(wid):
    assert world_images.list_images(wid) == []
    data = _png()
    assert world_images.put_image(wid, "coastline", data, "png") == "png"

    p = world_images.image_path(wid, "coastline")
    assert p is not None and p.read_bytes() == data
    assert p == worlds.world_root(wid) / "assets" / "images" / "coastline.png"
    assert [i["name"] for i in world_images.list_images(wid)] == ["coastline"]

    world_images.delete_image(wid, "coastline")
    assert world_images.image_path(wid, "coastline") is None


def test_a_name_a_link_cannot_carry_is_refused(wid):
    with pytest.raises(ValueError):
        world_images.put_image(wid, "my map", _png(), "png")
    with pytest.raises(ValueError):
        world_images.put_image(wid, "undescribed", _png(), "png")


def test_the_describe_backlog_reports_only_unreviewed_images(wid):
    world_images.put_image(wid, "coastline", _png(), "png")
    world_images.put_image(wid, "banner", _png(), "png")
    assert world_images.has_undescribed(wid)
    assert world_images.undescribed_count(wid) == 2

    world_images.set_description(wid, "coastline", "a rocky shore")
    assert [i["name"] for i in world_images.undescribed(wid)] == ["banner"]
    assert world_images.read_descriptions(wid) == {"coastline": "a rocky shore"}

    # Reviewed and deliberately left blank is FINISHED, not unreviewed.
    world_images.set_description(wid, "banner", "")
    assert world_images.undescribed(wid) == []
    assert not world_images.has_undescribed(wid)
    assert world_images.undescribed_count(wid) == 0


def test_an_unknown_world_creates_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    with pytest.raises(worlds.WorldNotFound):
        world_images.put_image("nope", "map", _png(), "png")
    assert not (tmp_path / "worlds" / "nope").exists()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && PYTHONPATH=src <PY> -m pytest tests/test_world_images_store.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement it**

Mirror `campaign_images`' structure, minus the lock. The describe faces are the
flat-directory equivalents of `image_descriptions`' base walkers, which cannot
reach a flat directory:

```python
def undescribed(wid: str) -> list[dict]:
    """Every library image with NO sidecar key — the world backlog's flat half.

    Key ABSENT, never merely empty: an image reviewed and deliberately left
    undescribed is finished, and re-offering it is how a queue never empties.
    """
    d = images_dir(wid)
    reviewed = image_descriptions.read_raw(d)
    return [{"name": i["name"]} for i in image_library.listing(d)
            if i["name"] not in reviewed]


def undescribed_count(wid: str) -> int:
    return len(undescribed(wid))


def has_undescribed(wid: str) -> bool:
    """`undescribed_count` stopping at the first one. Separate because
    `routes/todo.py`'s `_CHEAP` roster exists for chores whose COUNT costs far
    more than their presence, and answering a presence question by summing is
    what that roster is there to avoid."""
    d = images_dir(wid)
    reviewed = image_descriptions.read_raw(d)
    return any(i["name"] not in reviewed for i in image_library.listing(d))
```

The module docstring states the no-lock rule and points at
`overlay.set_description`'s note for why worlds have no domain.

- [ ] **Step 4: Register and run**

Add to `store/__init__.py` (import + `__all__`), regenerate
`store_api_baseline.json`, and read the diff — `world_images` and nothing else.

Run: `cd backend && PYTHONPATH=src <PY> -m pytest tests/test_world_images_store.py tests/test_store_api_baseline.py tests/test_lock_domain_guard.py tests/test_import_guard.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/world_images.py backend/src/grimoire/store/__init__.py \
        backend/tests/test_world_images_store.py backend/tests/store_api_baseline.json
git commit -m "A world's own image library"
```

---

### Task 4: Serialize `deleted.json`, and open a door for dropping a ref

Two changes to `store/overlay.py` and one to `store/campaigns/lifecycle.py`.
This is a **correctness fix the rest of the plan rests on** — do it before any
tombstone is written by new code.

**Files:**
- Modify: `backend/src/grimoire/store/overlay.py`
- Modify: `backend/src/grimoire/store/campaigns/lifecycle.py`
- Modify: `backend/src/grimoire/store/locks.py` (the stale `OUTSIDE_DOMAIN` note)
- Test: `backend/tests/test_overlay_tombstones.py`

**Interfaces:**
- Produces: `overlay.drop_library_tombstone(cid: str, name: str) -> None`.
  `add_deleted` and `_drop_deleted` keep their signatures and gain the lock.

- [ ] **Step 1: Write the failing test**

```python
def test_concurrent_tombstone_writes_both_survive(cid):
    """`deleted.json` is rewritten whole, so an unlocked writer loses one."""
    refs = [f"assets/library/pic{i}" for i in range(24)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda r: overlay.add_deleted(cid, r), refs))
    assert overlay.deleted(cid) == set(refs)


def test_a_library_tombstone_can_be_dropped_again(cid):
    overlay.add_deleted(cid, "assets/library/map")
    assert "assets/library/map" in overlay.deleted(cid)
    overlay.drop_library_tombstone(cid, "map")
    assert "assets/library/map" not in overlay.deleted(cid)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && PYTHONPATH=src <PY> -m pytest tests/test_overlay_tombstones.py -q`
Expected: the concurrency test FAILS with a short set (lost updates); the second
FAILS with `AttributeError: drop_library_tombstone`.

- [ ] **Step 3: Take the lock in both writers**

```python
def add_deleted(cid: str, ref: str) -> None:
    """UNDER `campaign_lock`. `deleted.json` is rewritten whole, so two unlocked
    writers lose one of the two — and what is lost resurrects a record or an
    image the user deleted, which `deleted`'s own fail-soft docstring names as
    the one direction of failure a user cannot spot by looking. Reentrant, so
    the callers already inside a hold pay nothing."""
    with locks.campaign_lock(cid):
        atomic.write_text(_deleted_path(cid),
                          json.dumps(sorted(deleted(cid) | {ref}), indent=2) + "\n")
```

Same for `_drop_deleted`. Then add the public door, which the restore route and
the world-side sweep both need (`_drop_deleted` is private and
`forget_world_record` is record-shaped):

```python
def drop_library_tombstone(cid: str, name: str) -> None:
    """Un-hide one inherited library image. See `store.campaign_images`."""
    _drop_deleted(cid, {_library_ref(name)})


def _library_ref(name: str) -> str:
    """`assets/library/<name>`, the library's tombstone shape.

    Three segments where `_asset_ref`'s are five, and `library` is not a kind —
    so it cannot be misread by anything that parses the record shape.
    """
    return f"assets/library/{name}"
```

- [ ] **Step 4: Give `ensure_campaign_slim` ONE hold**

`campaigns/lifecycle.py:238` loops `add_deleted`, and `:240` immediately calls
`_tombstone_deleted_copied_assets`, which opens at `:348` with
`gone = overlay.deleted(cid)` — the read half of a read-modify-write — and loops
to `:380`. Wrap **both** in a single `with locks.campaign_lock(cid):` spanning
`:238`–`:240`. Two separate holds would leave that read outside one of them and
let the second raise after the first committed. Per-ref locking would be N
advisory file-lock round trips.

Add a comment recording the consequence: `ensure_campaign_slim` can now raise
`CampaignBusy`, and it runs lazily on read paths (`routes/common.py:1015`), so a
contended campaign can answer 409 where it previously could not — a retryable
409 in exchange for a campaign that can no longer be left half-migrated.

- [ ] **Step 5: Update the stale declaration**

`locks.OUTSIDE_DOMAIN`'s entry for `store.campaigns.lifecycle` currently asserts
`ensure_campaign_slim` "is unlocked" and that fixing it "needs its own review".
That review is this task, for that function. Rewrite the entry to say what is
now true; leave the rest of the entry's claims alone.

- [ ] **Step 6: Run the guards and the touched suites**

Run: `cd backend && PYTHONPATH=src <PY> -m pytest tests/test_overlay_tombstones.py tests/test_lock_domain_guard.py tests/test_lock_order_guard.py tests/test_overlay.py tests/test_campaign_lifecycle.py tests/test_sync.py -q`
Expected: PASS. A hang here means a caller of `add_deleted` holds a *different*
campaign's lock — find it and hoist the hold rather than dropping this one.

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/store/overlay.py backend/src/grimoire/store/campaigns/lifecycle.py \
        backend/src/grimoire/store/locks.py backend/tests/test_overlay_tombstones.py
git commit -m "deleted.json is rewritten whole, so its writers take the lock"
```

---

### Task 5: `campaign_images` becomes the read-through view

The heart of the change. Read the spec's *How the campaign's view resolves*
table and its warning about the tombstone filter before writing a line.

**Files:**
- Modify: `backend/src/grimoire/store/campaign_images.py`
- Modify: `backend/src/grimoire/store/world_images.py` (delete sweeps)
- Test: `backend/tests/test_campaign_images_store.py`

**Interfaces:**
- Produces: `list_images(cid)` rows gain `"inherited": bool`;
  `list_hidden(cid) -> list[str]`; `restore_image(cid, name) -> None`;
  `read_descriptions(cid) -> dict[str, str]`; `own_undescribed(cid) -> list[dict]`.
  `images_dir(cid)` now means *the campaign's own directory* and its docstring
  says so.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_campaign_sees_its_worlds_library(cid, wid):
    world_images.put_image(wid, "coastline", _png(), "png")
    rows = campaign_images.list_images(cid)
    assert [(r["name"], r["inherited"]) for r in rows] == [("coastline", True)]
    assert campaign_images.image_path(cid, "coastline") == \
        worlds.world_root(wid) / "assets" / "images" / "coastline.png"


def test_a_campaign_may_not_take_a_name_the_world_holds(cid, wid):
    world_images.put_image(wid, "coastline", _png(), "png")
    with pytest.raises(ValueError):
        campaign_images.put_image(cid, "coastline", _png(), "png")


def test_hiding_an_inherited_image_and_getting_it_back(cid, wid):
    world_images.put_image(wid, "coastline", _png(), "png")

    campaign_images.delete_image(cid, "coastline")
    assert campaign_images.list_images(cid) == []
    assert campaign_images.image_path(cid, "coastline") is None   # not the world's
    assert campaign_images.list_hidden(cid) == ["coastline"]

    campaign_images.restore_image(cid, "coastline")
    assert [r["name"] for r in campaign_images.list_images(cid)] == ["coastline"]
    assert campaign_images.list_hidden(cid) == []


def test_a_campaign_image_under_a_hidden_name_is_listed_and_served(cid, wid):
    """The tombstone filter is the INHERITED half's, never the union's.

    Subtracting tombstones from the whole union hides the campaign's own bytes:
    they serve but never list, so no picker tile, no gallery row, and no way to
    clear the tombstone that hid them.
    """
    world_images.put_image(wid, "coastline", _png(), "png")
    campaign_images.delete_image(cid, "coastline")       # hide the world's
    world_images.delete_image(wid, "coastline")          # and the world drops it

    own = _png(color=(90, 90, 90))
    campaign_images.put_image(cid, "coastline", own, "png")

    assert [(r["name"], r["inherited"]) for r in campaign_images.list_images(cid)] \
        == [("coastline", False)]
    p = campaign_images.image_path(cid, "coastline")
    assert p is not None and p.read_bytes() == own


def test_deleting_a_world_image_clears_the_campaigns_that_hid_it(cid, wid):
    world_images.put_image(wid, "coastline", _png(), "png")
    campaign_images.delete_image(cid, "coastline")
    assert campaign_images.list_hidden(cid) == []  # placeholder; see Step 5

def test_descriptions_come_from_whichever_side_owns_the_image(cid, wid):
    world_images.put_image(wid, "coastline", _png(), "png")
    world_images.set_description(wid, "coastline", "a rocky shore")
    campaign_images.put_image(cid, "handout", _png(), "png")
    campaign_images.set_description(cid, "handout", "the party's map")

    assert campaign_images.read_descriptions(cid) == {
        "coastline": "a rocky shore", "handout": "the party's map"}
    assert [i["name"] for i in campaign_images.own_undescribed(cid)] == []


def test_a_campaign_may_not_describe_an_inherited_image(cid, wid):
    world_images.put_image(wid, "coastline", _png(), "png")
    with pytest.raises(ValueError):
        campaign_images.set_description(cid, "coastline", "mine")
```

Note: the fifth test above is a placeholder assertion — replace it in Step 5
once the sweep exists. Fix `test_deleting_a_world_image_clears_the_campaigns_that_hid_it`
to assert `campaign_images.list_hidden(cid) == []` **after**
`world_images.delete_image(wid, "coastline")`.

- [ ] **Step 2: Run and watch them fail**

Run: `cd backend && PYTHONPATH=src <PY> -m pytest tests/test_campaign_images_store.py -q`
Expected: the new tests FAIL; the pre-existing ones (except the known `[map*]`)
still pass.

- [ ] **Step 3: Implement resolution**

```python
def list_images(cid: str) -> list[dict]:
    """The campaign's own images, plus the world's that it neither holds nor hid.

    The tombstone filter applies to the INHERITED half only — `overlay.list_images`
    is the model. Subtracting it from the union would hide a campaign's own image
    uploaded under a previously-tombstoned name: bytes that serve but never list.
    """
    mine = image_library.listing(images_dir(cid))
    have = {i["name"] for i in mine}
    gone = overlay.deleted(cid)
    wid = _world_of(cid)
    inherited = [i for i in world_images.list_images(wid)
                 if i["name"] not in have
                 and overlay._library_ref(i["name"]) not in gone] if wid else []
    return sorted([{**i, "inherited": False} for i in mine]
                  + [{**i, "inherited": True} for i in inherited],
                  key=lambda i: i["name"])


def image_path(cid: str, name: str) -> Path | None:
    """Campaign file, else a tombstone stops the search, else the world's."""
    mine = assets.path_in(images_dir(cid), name, supported_only=True)
    if mine is not None:
        return mine
    if overlay._library_ref(name) in overlay.deleted(cid):
        return None
    wid = _world_of(cid)
    return world_images.image_path(wid, name) if wid else None
```

`put_image` refuses a name the world holds *now* (`ValueError`), `delete_image`
unlinks campaign-side then tombstones if the world still holds the name
(`overlay.delete_image`'s order — without it the accidental collision's delete is
a revert), `restore_image` calls `overlay.drop_library_tombstone`,
`list_hidden` reports tombstoned names the world still has, `read_descriptions`
unions the world's map for inherited names with the campaign's for its own, and
`set_description` raises `ValueError` for a name the campaign does not own.

`_library_ref` is used across module boundaries, so promote it to public
`overlay.library_ref(name)` and update Task 4's file accordingly.

- [ ] **Step 4: Update the module docstring**

Its current text asserts "**Not under the overlay** … `store/overlay.py` does not
know about them", which is now false. Rewrite that paragraph to state the
read-through rule, the no-shadowing rule, and the inherited-half tombstone
filter — the three things a future reader must not get wrong.

- [ ] **Step 5: Wire the sweep into `world_images.delete_image`**

Best-effort and per campaign, exactly as `overlay.forget_world_record` is:

```python
def _forget_in_dependents(wid: str, name: str) -> None:
    """Drop this image's tombstone wherever a campaign hid it.

    Per campaign and best-effort, `forget_world_record`'s shape and for its
    stated reason: aborting on one busy campaign would 500 a delete that has
    already happened. A skipped campaign keeps a stale tombstone, which is
    survivable ONLY because `campaign_images.list_hidden` surfaces it with a
    Restore beside it — the two halves are one decision.
    """
    for cid in overlay.dependent_campaigns(worlds_paths.world_root(wid)):
        try:
            overlay.drop_library_tombstone(cid, name)
        except (OSError, ValueError, locks.StoreBusy) as exc:
            log.warning("could not clear the hidden entry for %s in campaign %s "
                        "(%s) -- it stays listed as hidden and can be restored",
                        name, cid, exc)
```

Call it at the end of `delete_image`, after the unlink is confirmed.

- [ ] **Step 6: Run the store suites and the guards**

Run: `cd backend && PYTHONPATH=src <PY> -m pytest tests/test_campaign_images_store.py tests/test_world_images_store.py tests/test_overlay_guard.py tests/test_import_guard.py tests/test_lock_domain_guard.py -q`
Expected: PASS except the known `[map*]` Windows failure.

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/store/campaign_images.py backend/src/grimoire/store/world_images.py \
        backend/src/grimoire/store/overlay.py backend/tests/test_campaign_images_store.py
git commit -m "A campaign sees its world's library, and may hide what it does not want"
```

---

### Task 6: `routes/world_images.py` — the world library's HTTP surface

**Files:**
- Create: `backend/src/grimoire/routes/world_images.py`
- Modify: `backend/src/grimoire/routes/__init__.py`
- Modify: `backend/tests/test_route_order.py` (`CROSSING_PAIRS`)
- Test: `backend/tests/test_world_images_routes.py`

**Interfaces:**
- Consumes: `world_images.*`, `covers.world_*`, `routes.common._serve_image_file`,
  `_upload_image_ext`, `_with_descriptions`.
- Produces: the nine routes in the spec's Routes block.

- [ ] **Step 1: Write the failing test**

```python
def test_the_library_round_trips_over_http(client, wid):
    assert client.get(f"/api/worlds/{wid}/images").json() == []

    r = client.put(f"/api/worlds/{wid}/images/coastline",
                   files={"file": ("anything.jpg", _png(), "image/jpeg")})
    assert r.status_code == 200
    # The BYTES name the type, never the filename (#321).
    assert r.json()["ext"] == "png"

    assert client.get(f"/api/worlds/{wid}/images/coastline").status_code == 200
    assert [i["name"] for i in client.get(f"/api/worlds/{wid}/images").json()] == ["coastline"]
    assert client.delete(f"/api/worlds/{wid}/images/coastline").status_code == 200
    assert client.get(f"/api/worlds/{wid}/images/coastline").status_code == 404


def test_the_describe_backlog_is_not_shadowed_by_the_name_route(client, wid):
    """`/images/undescribed` must keep answering the backlog, not 404 as an image."""
    r = client.get(f"/api/worlds/{wid}/images/undescribed")
    assert r.status_code == 200 and isinstance(r.json(), list)


def test_a_name_a_link_cannot_carry_is_refused_before_any_byte_is_written(client, wid):
    r = client.put(f"/api/worlds/{wid}/images/my%20map",
                   files={"file": ("m.png", _png(), "image/png")})
    assert r.status_code == 400
    assert client.get(f"/api/worlds/{wid}/images").json() == []


def test_an_unknown_world_is_a_404_everywhere(client):
    assert client.get("/api/worlds/nope/images").status_code == 404
    assert client.put("/api/worlds/nope/images/m",
                      files={"file": ("m.png", _png(), "image/png")}).status_code == 404
```

- [ ] **Step 2: Run and watch it fail**

Run: `cd backend && PYTHONPATH=src <PY> -m pytest tests/test_world_images_routes.py -q`
Expected: 404s everywhere — the router does not exist.

- [ ] **Step 3: Write the routes**

Mirror `routes/campaigns.py`'s library block exactly, including the two-stage
size check (`file.size` **before** `await file.read()`, then `validate_size`),
`_upload_image_ext` for the stored extension, and an ungated `DELETE`. The module
docstring carries the ordering constraint:

```python
"""The world's cover and image library (`store/world_images.py`, `store/covers.py`).

Its own module, and included AFTER `characters`, because
`/worlds/{wid}/images/{name}` generalizes `/worlds/{wid}/images/undescribed`,
which `routes/characters.py` owns. Registered any earlier, the `{name}` route
swallows the describe backlog — the break `campaign_images.RESERVED` records on
the campaign side, where it cost a broken picker tile and a broken post.
`test_no_route_is_shadowed_by_an_earlier_one` is what holds this.
"""
```

- [ ] **Step 4: Include it in the right place**

In `routes/__init__.py`, add `world_images` to the tuple **immediately after
`characters`**, with a comment naming the constraint. `entities` stays last.

- [ ] **Step 5: Pin the crossing pair**

`POST /api/worlds/{wid}/images/{name}/description/draft` crosses
`POST /api/worlds/{wid}/{kind}/instantiate/{mid}/{content_id}` at eight
segments. Add the pair to `CROSSING_PAIRS` in `tests/test_route_order.py`,
copying the campaign mirror's reasoning (`:102-103`) — "images" is not an entity
kind, so the instantiate pattern can never legitimately claim a URL under it.

- [ ] **Step 6: Run the route suites**

Run: `cd backend && PYTHONPATH=src <PY> -m pytest tests/test_world_images_routes.py tests/test_route_order.py tests/test_path_guard_store.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/routes/world_images.py backend/src/grimoire/routes/__init__.py \
        backend/tests/test_world_images_routes.py backend/tests/test_route_order.py
git commit -m "The world library gets a door, behind the one that was already there"
```

---

### Task 7: World cover routes, and the cover token on the world payloads

**Files:**
- Modify: `backend/src/grimoire/routes/world_images.py`
- Modify: `backend/src/grimoire/routes/worlds.py`
- Test: `backend/tests/test_world_images_routes.py`

**Interfaces:**
- Produces: `GET|PUT|DELETE /api/worlds/{wid}/cover`; `GET /api/worlds` rows and
  `GET /api/worlds/{wid}` gain `"cover": <version token or "">`.

- [ ] **Step 1: Write the failing test**

```python
def test_the_cover_round_trips_and_shows_on_the_world_payloads(client, wid):
    assert client.get(f"/api/worlds/{wid}").json()["meta"]["cover"] == ""

    r = client.put(f"/api/worlds/{wid}/cover",
                   files={"file": ("c.png", _png(), "image/png")})
    assert r.status_code == 200 and r.json()["v"]

    assert client.get(f"/api/worlds/{wid}/cover").status_code == 200
    assert client.get(f"/api/worlds/{wid}").json()["meta"]["cover"] == r.json()["v"]
    assert [w["cover"] for w in client.get("/api/worlds").json()] == [r.json()["v"]]

    assert client.delete(f"/api/worlds/{wid}/cover").status_code == 200
    assert client.get(f"/api/worlds/{wid}/cover").status_code == 404


def test_an_image_that_cannot_be_labelled_honestly_is_refused(client, wid):
    r = client.put(f"/api/worlds/{wid}/cover",
                   files={"file": ("c.png", b"not an image", "image/png")})
    assert r.status_code == 400
```

- [ ] **Step 2: Run and watch it fail**

Run: `cd backend && PYTHONPATH=src <PY> -m pytest tests/test_world_images_routes.py -q`
Expected: FAIL on the cover tests.

- [ ] **Step 3: Add the cover routes**

Mirror `put_campaign_cover` exactly: `file.size` pre-check → 413,
`covers.validate` → 400 or the extension, `put_world_cover`, and a `DELETE` that
maps a failed unlink to 500 rather than reporting a removal that did not happen.

- [ ] **Step 4: Derive the token IN THE ROUTE**

In `routes/worlds.py`, add `cover` to `get_worlds()`' rows and to `get_world()`'s
`meta`, exactly as `routes/campaigns.py:178,567` do. **Do not touch
`store/worlds/read.py`** — the campaigns precedent keeps the cover out of the
meta file and out of the store readers, which is what keeps
`store.worlds.list_worlds` free of a `covers` import and a per-world `stat` that
`routes/todo.py` and `routes/shell.py` would pay for. It is also what keeps
`frozen_campaign/snapshot.json` from moving, since `sweep.py` calls the store
functions rather than the routes.

- [ ] **Step 5: Run, including the frozen fixture**

Run: `cd backend && PYTHONPATH=src <PY> -m pytest tests/test_world_images_routes.py tests/test_worlds_routes.py tests/test_frozen_campaign.py -q`
Expected: PASS, and `test_frozen_campaign` must **not** need regenerating. If it
does, the token leaked into a store function — move it back into the route.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/routes/world_images.py backend/src/grimoire/routes/worlds.py \
        backend/tests/test_world_images_routes.py
git commit -m "A world wears its cover on the shelf"
```

---

### Task 8: The campaign's HTTP surface learns about inheritance

**Files:**
- Modify: `backend/src/grimoire/routes/campaigns.py`
- Test: `backend/tests/test_campaign_images_routes.py`

**Interfaces:**
- Produces: `GET /campaigns/{cid}/images` rows carry `inherited`, and the
  response carries the hidden names; `POST /campaigns/{cid}/images/{name}/restore`.

- [ ] **Step 1: Write the failing test**

```python
def test_a_campaign_serves_and_hides_its_worlds_images(client, cid, wid):
    client.put(f"/api/worlds/{wid}/images/coastline",
               files={"file": ("c.png", _png(), "image/png")})

    listed = client.get(f"/api/campaigns/{cid}/images").json()
    assert [(i["name"], i["inherited"]) for i in listed["images"]] == [("coastline", True)]
    assert client.get(f"/api/campaigns/{cid}/images/coastline").status_code == 200

    assert client.delete(f"/api/campaigns/{cid}/images/coastline").status_code == 200
    hidden = client.get(f"/api/campaigns/{cid}/images").json()
    assert hidden["images"] == [] and hidden["hidden"] == ["coastline"]
    assert client.get(f"/api/campaigns/{cid}/images/coastline").status_code == 404

    assert client.post(f"/api/campaigns/{cid}/images/coastline/restore").status_code == 200
    assert [i["name"] for i in
            client.get(f"/api/campaigns/{cid}/images").json()["images"]] == ["coastline"]


def test_a_campaign_may_not_take_or_describe_a_world_name(client, cid, wid):
    client.put(f"/api/worlds/{wid}/images/coastline",
               files={"file": ("c.png", _png(), "image/png")})
    assert client.put(f"/api/campaigns/{cid}/images/coastline",
                      files={"file": ("c.png", _png(), "image/png")}).status_code == 409
    assert client.put(f"/api/campaigns/{cid}/images/coastline/description",
                      json={"description": "mine"}).status_code == 409


def test_the_campaign_backlog_leaves_inherited_art_to_the_world(client, cid, wid):
    client.put(f"/api/worlds/{wid}/images/coastline",
               files={"file": ("c.png", _png(), "image/png")})
    client.put(f"/api/campaigns/{cid}/images/handout",
               files={"file": ("h.png", _png(), "image/png")})
    names = [i["name"] for i in
             client.get(f"/api/campaigns/{cid}/images/undescribed").json()]
    assert names == ["handout"]
```

- [ ] **Step 2: Run and watch it fail**

Run: `cd backend && PYTHONPATH=src <PY> -m pytest tests/test_campaign_images_routes.py -q`

- [ ] **Step 3: Change the routes**

`GET /images` returns `{"images": [...], "hidden": [...]}` — a shape change, so
update every caller in the same commit (`PostImagePicker` is Task 15; the
frontend does not build until then, which is expected and fine). `PUT` maps the
store's `ValueError` for a world-held name to **409**, not 400: the name is not
malformed, it is taken. `PUT .../description` does the same. Add the `restore`
route. `GET /images/undescribed` switches its library half to `own_undescribed`.

- [ ] **Step 4: Run and commit**

Run: `cd backend && PYTHONPATH=src <PY> -m pytest tests/test_campaign_images_routes.py tests/test_route_order.py -q`

```bash
git add backend/src/grimoire/routes/campaigns.py backend/tests/test_campaign_images_routes.py
git commit -m "A campaign's library answers for its world's pictures too"
```

---

### Task 9: The galleries, the world backlog, and the describe badge

**Files:**
- Modify: `backend/src/grimoire/routes/characters.py` (both galleries + backlog)
- Modify: `backend/src/grimoire/routes/todo.py`
- Modify: `backend/src/grimoire/routes/shell.py`
- Test: `backend/tests/test_gallery_routes.py`, `backend/tests/test_todo_routes.py`

- [ ] **Step 1: Write the failing test**

```python
def test_the_world_gallery_and_backlog_carry_the_library(client, wid):
    client.put(f"/api/worlds/{wid}/images/coastline",
               files={"file": ("c.png", _png(), "image/png")})

    row = next(r for r in client.get(f"/api/worlds/{wid}/gallery").json()
               if r["kind"] == "world")
    assert row["name"] == "coastline" and row["record_name"] == "World library"
    assert row["id"] == "" and row["vid"] == ""

    backlog = client.get(f"/api/worlds/{wid}/images/undescribed").json()
    assert ("world", "coastline") in [(r["kind"], r["name"]) for r in backlog]


def test_the_campaign_gallery_calls_inherited_art_the_worlds_but_serves_it_here(client, cid, wid):
    client.put(f"/api/worlds/{wid}/images/coastline",
               files={"file": ("c.png", _png(), "image/png")})
    row = next(r for r in client.get(f"/api/campaigns/{cid}/gallery").json()
               if r["name"] == "coastline")
    # The KIND names the URL scope, so an inherited row is campaign-scoped;
    # origin rides on record_name.
    assert row["kind"] == "campaign" and row["record_name"] == "World library"
    assert row["url"].startswith(f"/api/campaigns/{cid}/images/coastline")


def test_a_library_only_backlog_still_raises_the_chore(client, wid):
    """The CHEAP probe gates whether the chore is computed at all."""
    client.put(f"/api/worlds/{wid}/images/coastline",
               files={"file": ("c.png", _png(), "image/png")})
    chores = client.get("/api/todo").json()
    describe = next(c for c in chores if c["kind"] == "world_describe")
    assert describe["n"] == 1 and describe["fix_label"] == "Images"
```

- [ ] **Step 2: Run and watch it fail**

- [ ] **Step 3: Implement**

- World gallery: append library rows with `kind="world"`,
  `record_name="World library"`.
- Campaign gallery: append rows from `campaign_images.list_images` with
  `kind="campaign"` and `record_name` chosen by `inherited`.
- `list_undescribed_images`: append `world_images.undescribed(wid)` rows with
  `kind="world"`, `record_name="World library"` and the world serving URL.
- `routes/todo.py`: add the library's count at `:354` and the library's
  **boolean** at `:492` (`has_undescribed`, not a count — the `_CHEAP` roster
  exists for chores whose count costs more than their presence). Restate
  `_DESCRIBE_BASES`' "it must stay that list" comment, which stops being true
  once the backlog holds a non-base. Change `fix_label` from "The cast" to
  "Images".
- `routes/shell.py:130`: add the library's count to the badge.

- [ ] **Step 4: Run and commit**

Run: `cd backend && PYTHONPATH=src <PY> -m pytest tests/test_gallery_routes.py tests/test_todo_routes.py tests/test_shell_routes.py -q`

```bash
git add backend/src/grimoire/routes/characters.py backend/src/grimoire/routes/todo.py \
        backend/src/grimoire/routes/shell.py backend/tests/test_gallery_routes.py \
        backend/tests/test_todo_routes.py
git commit -m "The gallery, the queue and the badge all count the world's own art"
```

---

### Task 10: Export and the narrator's art pool

**Files:**
- Modify: `backend/src/grimoire/store/export.py`
- Modify: `backend/src/grimoire/store/context/art.py`
- Test: `backend/tests/test_export_images.py`, `backend/tests/test_context_art.py`

- [ ] **Step 1: Write the failing test**

```python
def test_a_book_carries_the_worlds_picture_through_a_campaign_url(cid, wid):
    world_images.put_image(wid, "coastline", _png(), "png")
    text = f"![shore](/api/campaigns/{cid}/images/coastline)"
    images = export.Images()
    assert "images/" in export.rewrite_images(text, cid, images)


def test_a_world_shaped_library_url_in_inherited_lore_packs_too(cid, wid):
    world_images.put_image(wid, "coastline", _png(), "png")
    text = f"![shore](/api/worlds/{wid}/images/coastline)"
    images = export.Images()
    assert "images/" in export.rewrite_images(text, cid, images)


@pytest.mark.parametrize("shape", ["campaigns/{cid}", "worlds/{wid}"])
def test_a_hidden_picture_degrades_to_alt_text_in_both_shapes(cid, wid, shape):
    world_images.put_image(wid, "coastline", _png(), "png")
    campaign_images.delete_image(cid, "coastline")     # hidden here
    url = "/api/" + shape.format(cid=cid, wid=wid) + "/images/coastline"
    assert export.rewrite_images(f"![shore]({url})", cid, export.Images()) == "shore"
```

- [ ] **Step 2: Run and watch it fail**

- [ ] **Step 3: Implement**

Give `_IMG_URL` a **second named group** for the world shape — the existing
`(?P<lib>images)` captures the literal segment, not the scope, so a widened
prefix would leave `_resolve_image` unable to tell them apart. Both branches
resolve through `campaign_images.image_path(cid, name)`, so a tombstone is
honoured in either shape: a world-shaped URL in an inherited lore body is still
being exported *for that campaign*, and resolving it world-side would pack the
one picture the reader hid.

In `context/art.py`, point `_library_candidates` and `_resolved` at
`campaign_images.list_images` / `read_descriptions` / `image_path`. Correct the
module docstring's cost note: the pool now includes the world's library, so the
"included whole" half is bigger by the number of *described* world images. Do not
add a limit.

- [ ] **Step 4: Run and commit**

Run: `cd backend && PYTHONPATH=src <PY> -m pytest tests/test_export_images.py tests/test_context_art.py tests/test_epub.py -q`

```bash
git add backend/src/grimoire/store/export.py backend/src/grimoire/store/context/art.py \
        backend/tests/test_export_images.py backend/tests/test_context_art.py
git commit -m "The book and the narrator both reach the world's pictures"
```

---

### Task 11: Close the overlay-guard gap, and seed the fixtures

**Files:**
- Modify: `backend/tests/test_overlay_guard.py`
- Modify: `backend/src/grimoire/store/covers.py`, `store/campaign_images.py` (markers)
- Modify: `backend/tests/world_fixtures.py`
- Test: `backend/tests/test_world_bundle.py`, `backend/tests/test_world_fork.py`

- [ ] **Step 1: Extend the guard**

`INHERITED_SEGMENTS` derives from `overlay.INHERITED_KINDS + INHERITED_FILES`,
so adding `assets` **there** would change overlay semantics. Add a **test-local**
list beside it instead:

```python
#: Campaign-root segments that are inheritable but are not records, so
#: `overlay`'s own constants do not name them. The library (#376 + world
#: images) resolves through `campaign_images`, and a raw
#: `croot / "assets"` is the one shape of this codebase's most repeated bug
#: class that the kind-based roster cannot see.
EXTRA_INHERITABLE_SEGMENTS = frozenset({"assets"})
```

Fold it into `_inheritable_literal`'s membership test.

- [ ] **Step 2: Run it and watch the two legitimate call sites fail**

Run: `cd backend && PYTHONPATH=src <PY> -m pytest tests/test_overlay_guard.py -q`
Expected: FAIL naming `covers.py` and `campaign_images.images_dir`.

- [ ] **Step 3: Mark them and raise the cap**

Add `# overlay-ok: <reason>` to both, with reasons that are actually true —
`covers` is campaign-local and inherits nothing; `campaign_images.images_dir` is
the campaign's own half by definition and the read-through is `list_images`'
job. Raise the marker cap from 4 to 6 and say in the comment why the two new
ones were spent.

- [ ] **Step 4: Seed a world cover and a library image in the fixtures**

`world_fixtures.SEEDED_FILES` has no world-root `assets/` entry, so
`test_world_bundle` and `test_world_fork` would pass over an empty set.
Add `assets/cover.png`, `assets/images/coastline.png` and
`assets/images/descriptions.json`. `tree()` diffs the whole tree, so both
existing tests become load-bearing with no other change.

- [ ] **Step 5: Run and commit**

Run: `cd backend && PYTHONPATH=src <PY> -m pytest tests/test_overlay_guard.py tests/test_world_bundle.py tests/test_world_fork.py -q`

```bash
git add backend/tests/test_overlay_guard.py backend/tests/world_fixtures.py \
        backend/src/grimoire/store/covers.py backend/src/grimoire/store/campaign_images.py
git commit -m "The guard learns the one inheritable thing that is not a kind"
```

---

### Task 12: Route-shape sweep for the new world writes

**Files:**
- Modify: `backend/tests/test_routes.py`

`_actor_image_write_routes` (`:858`) requires a record segment before `/images`,
and `_campaign_library_write_routes` (`:1088`) is `^/api/campaigns/` only — so
neither reaches the new routes. `test_path_guard_store.py`'s generic `_id_routes()`
sweep does, but the targeted enumerations are what catch "route number five,
added later by someone who did not read this file".

- [ ] **Step 1: Add the sibling enumeration**

```python
def _world_library_write_routes(client):
    """Every registered write route on the WORLD image library surface.

    The sibling of `_campaign_library_write_routes`, and separate for its
    reason: this surface has no actor and no version, so the only id it can get
    wrong is the world's.
    """
    surface = re.compile(r"^/api/worlds/\{\w+\}/(images|cover)(/|$)")
    ...
```

Assert each answers 404 (not 500) for an unusable world id.

- [ ] **Step 2: Run and commit**

Run: `cd backend && PYTHONPATH=src <PY> -m pytest tests/test_routes.py tests/test_path_guard_store.py -q`

```bash
git add backend/tests/test_routes.py
git commit -m "Route number five, caught by the file that says it will be"
```

---

### Task 13: The frontend API client

**Files:**
- Modify: `frontend/src/api/client.ts`, `frontend/src/api/types.ts`
- Test: `frontend/src/api/client.test.ts`

- [ ] **Step 1: Add the types**

In `types.ts`: `CampaignImage` gains `inherited?: boolean`; add
`WorldImage = { name: string; ext: string; v: string; description?: string; described?: boolean }`
and `CampaignLibrary = { images: CampaignImage[]; hidden: string[] }`.

- [ ] **Step 2: Add the calls**

`worldCoverUrl(wid, {w?, v?})`, `putWorldCover`, `deleteWorldCover`,
`worldImageUrl(wid, name, {w?, v?})`, `worldImages`, `putWorldImage`,
`deleteWorldImage`, `setWorldImageDescription`, `draftWorldImageDescription`,
`restoreCampaignImage`. Mirror the campaign equivalents including
`encodeSegment` on every name.

- [ ] **Step 3: Run and commit**

Run: `cd frontend && npx vitest run src/api/client.test.ts`

```bash
git add frontend/src/api/client.ts frontend/src/api/types.ts frontend/src/api/client.test.ts
git commit -m "The client learns the world's pictures"
```

---

### Task 14: `CoverPanel`, the world card, and the world header

**Files:**
- Create: `frontend/src/components/CoverPanel.tsx`
- Delete: `frontend/src/components/CampaignCover.tsx` (moved, not rewritten)
- Modify: `frontend/src/routes/{CampaignHub,CampaignView,WorldsView,WorldView}.tsx`,
  `frontend/src/index.css`
- Test: `frontend/src/components/CoverPanel.test.tsx`, `routes/WorldsView.test.tsx`

- [ ] **Step 1: Move, do not rewrite**

`CoverPanel({ scope })` where `scope` is `{kind: "campaign", id} | {kind: "world", id}`.
Keep the `live` ref discipline **verbatim** — it exists because the panel is
reused across navigation and every await can resolve after the reader moved on,
and dropping it strands the new panel's controls disabled forever.

- [ ] **Step 2: CSS**

Mirror `.shelf-cover` for the world card. **Do not name anything
`.campaign-cover`** — `index.css:429` records that taking that name once
redefined a 260px preview into a 104px thumbnail everywhere the component
renders.

- [ ] **Step 3: Test both scopes and the card fallback**

Cover upload, remove, and the `broken`-by-version fallback so a cover that fails
to load shows the placeholder rather than a broken-image glyph beside a Remove
button.

- [ ] **Step 4: Run and commit**

Run: `cd frontend && npx vitest run src/components/CoverPanel.test.tsx src/routes/WorldsView.test.tsx`

```bash
git commit -m "The worlds shelf gets its pictures"
```

---

### Task 15: The World art tab, the queue, and the picker

**Files:**
- Modify: `frontend/src/components/{ImagesView,DescribeQueue,PostImagePicker}.tsx`
- Test: their `.test.tsx` siblings

- [ ] **Step 1: The third tab**

`type Tab = "gallery" | "queue" | "world"`. The World art tab holds the world
`CoverPanel` and the library editor (upload, replace, delete). **It renders
whether or not `forCampaign` is set** — `shell/rail.ts:280` appends `&for=<cid>`
whenever a campaign is open, so hiding it under `for=` would make the feature's
only editing surface unreachable by the app's own navigation for as long as a
campaign is open. Label the controls as the world's, which is what they are.

The gallery stays a browser: add both kinds to `KIND_LABELS`/`KINDS` and nothing
else. `ImagesView.tsx:68` says why in as many words.

- [ ] **Step 2: The queue and the picker**

`DescribeQueue`: a `kind === "world"` branch beside the `"campaign"` one.
`PostImagePicker`: read the new `{images, hidden}` shape, mark inherited rows,
offer "remove from this campaign" for an inherited image (it writes a tombstone,
a different sentence from deleting the campaign's own), and list hidden names
with a Restore.

- [ ] **Step 3: Test**

An inherited tile inserts a **campaign-scoped** URL; hiding one moves it to the
Hidden list; Restore brings it back; the World art tab renders under `?for=`.

- [ ] **Step 4: Full gate**

Run: `make check PY=C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe`
Expected: no failures beyond the eight recorded in Global Constraints. Run
`make baseline` and commit the smaller file if a lint count moved.

- [ ] **Step 5: Commit**

```bash
git commit -m "World art gets a tab, and a hidden picture gets a way back"
```

---

## Self-Review

**Spec coverage.** Every section maps to a task: modules → 1–3; locking and
`add_deleted` → 4; the resolution table, the inherited-half filter, the sweep and
`list_hidden` → 5; routes and ordering → 6–8; galleries, backlog, badge → 9;
export and art → 10; the guard gap, rosters and fixtures → 1, 3, 11, 12;
frontend → 13–15. The no-migration note needs no task by construction.

**Placeholders.** One deliberate marker: Task 5 Step 1's
`test_deleting_a_world_image_clears_the_campaigns_that_hid_it` is written with a
placeholder assertion and Step 5 says to finish it once the sweep exists. That is
called out in both places rather than left to be discovered.

**Type consistency.** `overlay.library_ref(name)` is public from Task 4 (Task 5
Step 3 promotes it and says so); `list_images` rows carry `inherited` everywhere
from Task 5 on; `GET /campaigns/{cid}/images` returns `{images, hidden}` from
Task 8, which Task 15 consumes; `has_undescribed`/`undescribed_count` are
distinct in Task 3 and stay distinct in Task 9.
