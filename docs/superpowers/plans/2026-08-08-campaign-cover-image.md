# Campaign Cover Image Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each campaign one cover image, shown on the campaigns list and used as the cover of its exported EPUB.

**Architecture:** A new `store/covers.py` owns `<campaign>/assets/cover.<ext>`, reusing directory-level primitives extracted from `store/assets.py` rather than reimplementing image handling. Three routes on `routes/campaigns.py` serve, upload and remove it; campaign reads gain a `cover` version token. `store/export.py` hands `build_epub` the cover path, and `build_epub` packs it itself — deliberately *not* through the shared image registry, which every other renderer also packs from.

**Tech Stack:** Python 3 / FastAPI / pytest (backend), Jinja2 + stdlib `zipfile` (EPUB), React + TypeScript + vitest (frontend), Pillow (already a base dependency).

**Spec:** `docs/superpowers/specs/2026-08-08-campaign-cover-image-design.md`

## Global Constraints

- **Never commit real campaign/world/character data or names.** Invented names only; reuse existing placeholders (Seraphine, Mara, Winifred, Realm, Saltmarch).
- **Every store write goes through `store.atomic`** (`test_atomic_guard.py`). Clear a genuinely-safe exception with `# atomic-ok: <reason>`.
- **Filesystem access goes through the path resolvers** (`test_paths_guard.py`) — here, `campaigns.paths`.
- **Imports in `backend/src/grimoire/` are module-scope and acyclic** (`test_import_guard.py`). Inside `store/`, a cross-package import binds a *submodule*: `from .campaigns import paths as campaigns_paths`, never `from .campaigns.paths import campaign_root`.
- **pydantic stays v1/v2-agnostic**: plain `BaseModel` fields, dump via `routes.common._dump`.
- **A new module mutating campaign-scoped state must be classified** in `store/locks.py` (`test_lock_domain_guard.py`). `UNREVIEWED` is frozen and may not grow.
- **Run tests from the right place.** Backend: `cd backend && PYTHONPATH=src .venv/Scripts/python -m pytest …` (the `PYTHONPATH` is what shadows the editable install). Frontend: run vitest **from** `frontend/`.
- **Full gate is `make check`.**

---

### Task 1: Directory-level image primitives in `store/assets.py`

Extract the half of each `assets` function that operates on a resolved
directory, so `covers.py` can reuse the extension allowlist, per-image lock,
write-before-cleanup ordering and newest-wins resolution instead of
reimplementing them. Behaviour of the existing record-shaped functions is
unchanged.

**Files:**
- Modify: `backend/src/grimoire/store/assets.py:251-296` (`image_path`), `:337-378` (`put_image`), `:381-396` (`delete_image`)
- Test: `backend/tests/test_assets_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `assets.path_in(d: Path, name: str, *, supported_only: bool = False) -> Path | None`
  - `assets.put_in(d: Path, name: str, data: bytes, ext: str, *, supported_only: bool = False) -> str`
  - `assets.delete_in(d: Path, name: str, *, supported_only: bool = False) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_assets_store.py`:

```python
def test_path_in_returns_newest_and_puts_round_trip(tmp_path):
    d = tmp_path / "assets"
    assert assets.path_in(d, "cover") is None          # directory absent
    assert assets.put_in(d, "cover", b"one", "png") == "png"
    p = assets.path_in(d, "cover")
    assert p is not None and p.name == "cover.png" and p.read_bytes() == b"one"


def test_put_in_replaces_across_extensions(tmp_path):
    d = tmp_path / "assets"
    assets.put_in(d, "cover", b"one", "png")
    assets.put_in(d, "cover", b"two", "jpg")
    assert [p.name for p in sorted(d.iterdir())] == ["cover.jpg"]
    assert assets.path_in(d, "cover").read_bytes() == b"two"


def test_put_in_rejects_unsupported_ext_and_unsafe_name(tmp_path):
    d = tmp_path / "assets"
    with pytest.raises(ValueError):
        assets.put_in(d, "cover", b"x", "svg")
    with pytest.raises(ValueError):
        assets.put_in(d, "../cover", b"x", "png")


def test_supported_only_ignores_and_spares_a_foreign_sibling(tmp_path):
    """A store directory is one a human browses and a sync client writes into:
    a `cover.txt` must neither become the cover nor be deleted by us."""
    d = tmp_path / "assets"
    assets.put_in(d, "cover", b"png", "png")
    (d / "cover.txt").write_text("sync conflict note", encoding="utf-8")
    os.utime(d / "cover.txt", (2 ** 31, 2 ** 31))  # newest by mtime

    assert assets.path_in(d, "cover", supported_only=True).name == "cover.png"
    assert assets.path_in(d, "cover").name == "cover.txt"  # legacy behaviour kept

    assets.put_in(d, "cover", b"jpg", "jpg", supported_only=True)
    assert (d / "cover.txt").exists()

    assets.delete_in(d, "cover", supported_only=True)
    assert assets.path_in(d, "cover", supported_only=True) is None
    assert (d / "cover.txt").exists()


def test_delete_in_is_a_noop_without_the_directory(tmp_path):
    assets.delete_in(tmp_path / "nope", "cover")  # no error
```

- [ ] **Step 2: Run the tests to verify they fail**

```
cd backend && PYTHONPATH=src .venv/Scripts/python -m pytest tests/test_assets_store.py -v
```

Expected: the five new tests FAIL with `AttributeError: module 'grimoire.store.assets' has no attribute 'path_in'`.

- [ ] **Step 3: Add the primitives and rewrite the wrappers around them**

In `backend/src/grimoire/store/assets.py`, add after `_mtime_ns`:

```python
def _siblings(d: Path, name: str, supported_only: bool) -> list[Path]:
    """Every file in `d` whose stem is `name`.

    `supported_only` narrows that to the extensions we actually accept. The
    cover directory is one a human browses and a sync client writes into, so a
    `cover.txt` left beside `cover.png` must neither win resolution (it would
    be served as octet-stream and packed into a book) nor be deleted by a
    replace or a remove -- it is not ours. Record images keep the unfiltered
    behaviour: `promote_image` raises `ValueError` for "an externally-placed
    file whose extension we never accepted", which requires `image_path` to
    still hand one back.
    """
    found = list(d.glob(f"{name}.*"))
    return [p for p in found if _norm_ext(p.suffix)] if supported_only else found


def path_in(d: Path, name: str, *, supported_only: bool = False) -> Path | None:
    """The current file for logical image `name` in directory `d`, or None.

    Newest wins, not alphabetically first -- see `image_path`. Lock-agnostic:
    this takes a directory and no campaign identity, so a caller that mutates
    campaign-scoped state through `put_in`/`delete_in` is the one that must
    hold `locks.campaign_lock` (`store.covers` does).
    """
    if not _safe_name(name) or not d.exists():
        return None
    matches = _siblings(d, name, supported_only)
    if not matches:
        return None
    return max(matches, key=lambda p: (_mtime_ns(p), p.name))


def put_in(d: Path, name: str, data: bytes, ext: str, *,
           supported_only: bool = False) -> str:
    """Publish `data` as `<name>.<ext>` in `d`, dropping the stale siblings."""
    if not _safe_name(name):
        raise ValueError("unsafe image id")
    ext = _norm_ext(ext)
    if not ext:
        raise ValueError("unsupported image type")
    d.mkdir(parents=True, exist_ok=True)
    written = d / f"{name}.{ext}"
    with _image_lock(d, name):
        # Write BEFORE dropping prior-extension files. The reverse order (which
        # this used to do) loses the image outright if anything fails between
        # the unlink and the write -- atomicity alone cannot fix an ordering
        # bug. path_in() breaks the resulting momentary tie by mtime.
        #
        # Snapshot the siblings' IDENTITY before writing, and delete only those
        # exact files: the lock keeps concurrent callers out, and the identity
        # check keeps anything that reaches the directory another way (an
        # external tool, a sync client) from having its file deleted by path
        # alone.
        stale = []
        for p in _siblings(d, name, supported_only):
            if p == written:
                continue
            try:
                st = p.stat()
                stale.append((p, st.st_dev, st.st_ino))
            except OSError:
                pass  # vanished already; nothing to clean up
        atomic.write_bytes(written, data)
        for p, dev, ino in stale:
            try:
                st = p.stat()
                if (st.st_dev, st.st_ino) != (dev, ino):
                    continue  # not the file we snapshotted; not ours to delete
                p.unlink()
            except OSError:
                pass  # a lost cleanup self-heals: path_in prefers the newest
    return ext


def delete_in(d: Path, name: str, *, supported_only: bool = False) -> None:
    """Remove every file for logical image `name` in `d`.

    Failures are swallowed here, as they always were -- callers that need the
    removal *confirmed* (`covers.delete_cover`) re-resolve afterwards.
    """
    if not _safe_name(name) or not d.exists():
        return
    # Same lock as put_in: a delete racing an upload must not remove the file
    # the upload just published and leave the caller thinking it wrote one, nor
    # half-remove a set the upload is mid-way through replacing.
    with _image_lock(d, name):
        for p in _siblings(d, name, supported_only):
            try:
                p.unlink()
            except OSError:
                pass
```

Then replace the bodies of the three record-shaped functions. `image_path`
(keep its existing docstring and the healing comment):

```python
def image_path(root: Path, cid: str, vid: str, name: str, base: str = "characters") -> Path | None:
    if not (safe_id(cid) and safe_id(vid) and _safe_name(name)):
        return None
    d = _dir(root, cid, vid, base)
    if not d.exists():
        return None
    p = path_in(d, name)
    if p is None and name == AVATAR:
        # A promotion interrupted before #253 may have stranded the avatar under
        # `promote-tmp`; adopt it rather than serve a 404 over a file we have.
        _heal_stranded_promotion(d)
        p = path_in(d, name)
    return p
```

`put_image`:

```python
def put_image(root: Path, cid: str, vid: str, name: str, data: bytes, ext: str,
              base: str = "characters") -> str:
    if not (safe_id(cid) and safe_id(vid)):
        raise ValueError("unsafe image id")
    ext = put_in(_dir(root, cid, vid, base), name, data, ext)
    if name == AVATAR:
        clear_focus(root, cid, vid, base)
    return ext
```

`delete_image`:

```python
def delete_image(root: Path, cid: str, vid: str, name: str, base: str = "characters") -> None:
    if not (safe_id(cid) and safe_id(vid) and _safe_name(name)):
        return
    delete_in(_dir(root, cid, vid, base), name)
    if name == AVATAR:
        clear_focus(root, cid, vid, base)
```

- [ ] **Step 4: Run the full assets suite**

```
cd backend && PYTHONPATH=src .venv/Scripts/python -m pytest tests/test_assets_store.py -v
```

Expected: PASS, including every pre-existing test — those are what prove the
extraction preserved behaviour.

- [ ] **Step 5: Run the guards that read this module's AST**

```
cd backend && PYTHONPATH=src .venv/Scripts/python -m pytest tests/test_atomic_guard.py tests/test_paths_guard.py tests/test_import_guard.py tests/test_lock_domain_guard.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/assets.py backend/tests/test_assets_store.py
git commit -m "Extract directory-level image primitives from assets.py"
```

---

### Task 2: `store/covers.py`

**Files:**
- Create: `backend/src/grimoire/store/covers.py`
- Modify: `backend/src/grimoire/store/locks.py` (`DOMAIN_MODULES`), `backend/src/grimoire/store/__init__.py` (import + `__all__`), `backend/tests/store_api_baseline.json`
- Test: `backend/tests/test_covers_store.py` (create)

**Interfaces:**
- Consumes: `assets.path_in` / `put_in` / `delete_in` from Task 1.
- Produces:
  - `covers.NAME == "cover"`, `covers.MAX_BYTES`, `covers.MAX_PIXELS`
  - `covers.CoverTooLarge`, `covers.CoverInvalid` (both `Exception`)
  - `covers.validate(data: bytes) -> None`
  - `covers.cover_path(cid: str) -> Path | None`
  - `covers.cover_version(cid: str) -> str`
  - `covers.put_cover(cid: str, data: bytes, ext: str) -> str`
  - `covers.delete_cover(cid: str) -> None`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_covers_store.py`:

```python
import io

import pytest
from PIL import Image

from grimoire.store import assets, campaigns, covers, worlds


def _png(size=(4, 4)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def cid(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Saltmarch Nights", wid)


def test_put_read_delete_round_trip(cid):
    assert covers.cover_path(cid) is None
    assert covers.cover_version(cid) == ""

    data = _png()
    assert covers.put_cover(cid, data, "png") == "png"
    p = covers.cover_path(cid)
    assert p is not None and p.read_bytes() == data
    assert p == campaigns.campaign_root(cid) / "assets" / "cover.png"
    assert covers.cover_version(cid) != ""

    covers.delete_cover(cid)
    assert covers.cover_path(cid) is None
    assert covers.cover_version(cid) == ""


def test_replacing_across_extensions_leaves_one_file(cid):
    covers.put_cover(cid, _png(), "png")
    covers.put_cover(cid, _png((5, 5)), "jpg")
    d = campaigns.campaign_root(cid) / "assets"
    assert [p.name for p in sorted(d.iterdir())] == ["cover.jpg"]


def test_unsupported_extension_rejected(cid):
    with pytest.raises(ValueError):
        covers.put_cover(cid, _png(), "svg")


def test_unknown_campaign_raises_and_creates_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    for call in (lambda: covers.cover_path("ghost"),
                 lambda: covers.cover_version("ghost"),
                 lambda: covers.put_cover("ghost", _png(), "png"),
                 lambda: covers.delete_cover("ghost")):
        with pytest.raises(campaigns.CampaignNotFound):
            call()
    assert not (tmp_path / "campaigns" / "ghost").exists()


def test_foreign_sibling_is_ignored_and_kept(cid):
    covers.put_cover(cid, _png(), "png")
    stray = campaigns.campaign_root(cid) / "assets" / "cover.txt"
    stray.write_text("sync conflict", encoding="utf-8")
    import os
    os.utime(stray, (2 ** 31, 2 ** 31))  # newest, so a naive glob would pick it

    assert covers.cover_path(cid).name == "cover.png"
    covers.delete_cover(cid)
    assert stray.exists()


def test_delete_raises_when_the_file_survives(cid, monkeypatch):
    """A held file on Windows must not answer 'removed'."""
    covers.put_cover(cid, _png(), "png")
    monkeypatch.setattr("pathlib.Path.unlink",
                        lambda self, *a, **k: (_ for _ in ()).throw(OSError("held")))
    with pytest.raises(OSError):
        covers.delete_cover(cid)


def test_cover_version_survives_a_vanishing_file(cid, monkeypatch):
    """It runs once per row in GET /campaigns; a stat race may not 500 the list."""
    covers.put_cover(cid, _png(), "png")
    monkeypatch.setattr(assets, "image_version",
                        lambda p: (_ for _ in ()).throw(OSError("gone")))
    assert covers.cover_version(cid) == ""


def test_validate_accepts_a_real_image(cid):
    covers.validate(_png())


def test_validate_rejects_non_image_bytes(cid):
    with pytest.raises(covers.CoverInvalid):
        covers.validate(b"not an image at all")


def test_validate_rejects_an_oversized_body(cid):
    with pytest.raises(covers.CoverTooLarge):
        covers.validate(b"\x89PNG" + b"\0" * covers.MAX_BYTES)


def test_validate_rejects_an_absurd_raster(cid, monkeypatch):
    """A few hundred KB of PNG can describe a billion pixels, and store.thumbs
    is what eventually decodes it -- inside the Android process."""
    data = _png()
    monkeypatch.setattr(covers, "MAX_PIXELS", 4)  # our 4x4 fixture is 16px
    with pytest.raises(covers.CoverInvalid):
        covers.validate(data)
```

- [ ] **Step 2: Run the tests to verify they fail**

```
cd backend && PYTHONPATH=src .venv/Scripts/python -m pytest tests/test_covers_store.py -v
```

Expected: FAIL — `ImportError: cannot import name 'covers'`.

- [ ] **Step 3: Write `store/covers.py`**

```python
"""The campaign's cover image: ``<campaign>/assets/cover.<ext>``.

One image per campaign, used as the EPUB cover and as the campaigns-list
thumbnail. Deliberately *not* a key in ``campaign.md``: that file is
read-modify-written unlocked by ``campaigns.read.touch``, ``rename_campaign``
and ``set_campaign_response`` (see ``OUTSIDE_DOMAIN`` in ``locks.py``), so a
cover recorded there could be dropped by a concurrent rename. The file's
presence on disk is the record.

Not under the overlay either: a cover is campaign-local and is never inherited
from the campaign's world, so there is no world-side copy to shadow and
nothing to tombstone. ``store/overlay.py`` does not know about covers.

The image work itself is ``assets``' directory-level primitives -- extension
allowlist, per-image lock, write-before-cleanup, newest-wins -- with
``supported_only``, because this directory is one a human browses and a sync
client writes into.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from . import assets, locks
from .campaigns import paths as campaigns_paths

NAME = "cover"

#: Ceiling on a stored cover. The backend is packaged verbatim into the
#: Android app (Chaquopy), where one upload exists as the request body, as
#: `bytes`, inside the EPUB's in-memory `BytesIO` and again in its
#: `getvalue()`. Nothing is downscaled below it -- the book embeds the
#: full-resolution image.
MAX_BYTES = 25 * 1024 * 1024

#: Ceiling on the DECODED raster, which the byte cap does not bound: a few
#: hundred KB of PNG can describe a billion pixels, and `store.thumbs` decodes
#: it to serve the 96px list thumbnail. Pillow's own DecompressionBombError is
#: a backstop above its ~89 MP default; this is the policy.
MAX_PIXELS = 50_000_000


class CoverTooLarge(Exception):
    """The upload is bigger than `MAX_BYTES` (HTTP 413)."""


class CoverInvalid(Exception):
    """The upload is not a decodable image, or its raster is absurd (HTTP 400)."""


def validate(data: bytes) -> None:
    """Gate an upload before it is stored. Never converts or downscales."""
    if len(data) > MAX_BYTES:
        raise CoverTooLarge("cover image is too large (max 25 MB)")
    try:
        with Image.open(io.BytesIO(data)) as im:
            width, height = im.size   # from the header; decodes no pixels
            im.verify()               # structural integrity, still no full decode
    except Exception as exc:  # noqa: BLE001 -- PIL raises a zoo of types for bad bytes
        raise CoverInvalid("not a readable image") from exc
    if width * height > MAX_PIXELS:
        raise CoverInvalid("cover image has too many pixels (max 50 MP)")


def _assets_dir(cid: str) -> Path:
    """``<campaign>/assets``, after proving the campaign is actually there.

    ``campaign_root`` is a syntax guard, not an existence check -- it only
    rejects ids ``safe_id`` refuses. Without this, a put for an unknown id
    would create a campaign directory holding an image and no ``campaign.md``.
    """
    if not campaigns_paths.campaign_meta_path(cid).exists():
        raise campaigns_paths.CampaignNotFound(cid)
    return campaigns_paths.campaign_root(cid) / "assets"


def cover_path(cid: str) -> Path | None:
    return assets.path_in(_assets_dir(cid), NAME, supported_only=True)


def cover_version(cid: str) -> str:
    """Cache-busting token for the current cover, "" when there is none.

    Swallows `OSError`: this runs once per row in ``GET /campaigns``, and
    ``assets.image_version`` stats unguarded, so a cover deleted between
    resolution and stat must read as "no cover" rather than 500 the listing.
    """
    p = cover_path(cid)
    if p is None:
        return ""
    try:
        return assets.image_version(p)
    except OSError:
        return ""


def put_cover(cid: str, data: bytes, ext: str) -> str:
    """Store `data` as the cover; returns the stored extension."""
    d = _assets_dir(cid)
    with locks.campaign_lock(cid):
        return assets.put_in(d, NAME, data, ext, supported_only=True)


def delete_cover(cid: str) -> None:
    """Remove the cover, and confirm it.

    ``assets.delete_in`` swallows a failed unlink by design -- a lost cleanup
    self-heals there, because resolution prefers the newest file. Here the
    unlink IS the operation: on Windows a sync client or a scanner can hold the
    file, and a swallowed failure would answer "removed" to a Remove that did
    nothing. Same shape as ``assets.promote_image``'s "promoted image could not
    be cleared".
    """
    d = _assets_dir(cid)
    with locks.campaign_lock(cid):
        assets.delete_in(d, NAME, supported_only=True)
        if assets.path_in(d, NAME, supported_only=True) is not None:
            raise OSError("cover could not be removed")
```

- [ ] **Step 4: Classify the module and export it**

In `backend/src/grimoire/store/locks.py`, add to `DOMAIN_MODULES` (keep the
set alphabetical among its neighbours):

```python
    # The campaign's cover image (`<campaign>/assets/cover.<ext>`). A new
    # module mutating campaign-scoped state, so it starts inside the exclusion
    # rather than joining the frozen `UNREVIEWED` backlog: `put_cover` and
    # `delete_cover` take the lock around the publish-then-clean sequence, and
    # `delete_cover` verifies the removal under it.
    "store.covers",
```

In `backend/src/grimoire/store/__init__.py`, add `covers` to the big
`from . import (...)` list (alphabetically, after `context`) and `"covers"` to
`__all__`.

Then regenerate the frozen facade snapshot — **deliberately**, in this same
commit, because the facade genuinely gained a name:

```
cd backend && PYTHONPATH=src .venv/Scripts/python -c "import json, pathlib; from grimoire import store; p = pathlib.Path('tests/store_api_baseline.json'); p.write_text(json.dumps({'all': sorted(store.__all__), 'dir': sorted(n for n in dir(store) if not n.startswith('_'))}, indent=2) + '\n', encoding='utf-8')"
```

- [ ] **Step 5: Run the covers suite and every guard**

```
cd backend && PYTHONPATH=src .venv/Scripts/python -m pytest tests/test_covers_store.py tests/test_lock_domain_guard.py tests/test_import_guard.py tests/test_atomic_guard.py tests/test_paths_guard.py tests/test_pydantic_guard.py tests/test_store_api_baseline.py -v
```

Expected: PASS. If `test_lock_domain_guard.py` fails naming `store.covers`,
the classification above is missing or misspelled.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/covers.py backend/src/grimoire/store/locks.py \
        backend/src/grimoire/store/__init__.py backend/tests/store_api_baseline.json \
        backend/tests/test_covers_store.py
git commit -m "Add store.covers: one cover image per campaign"
```

---

### Task 3: HTTP surface

**Files:**
- Modify: `backend/src/grimoire/routes/common.py:209-234` (`_serve_image`), `backend/src/grimoire/routes/campaigns.py` (new routes near the export routes; `get_campaigns` at `:89`; `get_campaign` at `:164`)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: everything `covers` produces in Task 2.
- Produces:
  - `common._serve_image_file(p: Path, request: Request | None = None) -> Response`
  - `GET|PUT|DELETE /api/campaigns/{cid}/cover`
  - a `cover` string (the version token, `""` when absent) on `GET /api/campaigns` rows and on `GET /api/campaigns/{cid}`'s `meta`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_routes.py` (it already imports `io`; add
`from PIL import Image` at the top of the file if it is not there):

```python
def _png_bytes(size=(4, 4)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


def test_campaign_cover_round_trip(client):
    _wid, cid = _campaign(client)
    url = f"/api/campaigns/{cid}/cover"
    assert client.get(url).status_code == 404

    data = _png_bytes()
    r = client.put(url, files={"file": ("c.png", io.BytesIO(data), "image/png")})
    assert r.status_code == 200
    assert r.json()["ext"] == "png" and r.json()["v"]

    got = client.get(url)
    assert got.status_code == 200 and got.content == data
    assert got.headers["content-type"].startswith("image/png")

    assert client.delete(url).status_code == 200
    assert client.delete(url).status_code == 200      # idempotent
    assert client.get(url).status_code == 404


def test_campaign_cover_unknown_campaign_is_404(client):
    url = "/api/campaigns/ghost/cover"
    assert client.get(url).status_code == 404
    assert client.delete(url).status_code == 404
    r = client.put(url, files={"file": ("c.png", io.BytesIO(_png_bytes()), "image/png")})
    assert r.status_code == 404


def test_campaign_cover_rejects_bad_uploads(client):
    _wid, cid = _campaign(client)
    url = f"/api/campaigns/{cid}/cover"

    bad_ext = client.put(url, files={"file": ("c.svg", io.BytesIO(b"<svg/>"), "image/svg+xml")})
    assert bad_ext.status_code == 400

    not_image = client.put(url, files={"file": ("c.png", io.BytesIO(b"nope"), "image/png")})
    assert not_image.status_code == 400 and not_image.json()["detail"]

    huge = b"\x89PNG" + b"\0" * (25 * 1024 * 1024)
    too_big = client.put(url, files={"file": ("c.png", io.BytesIO(huge), "image/png")})
    assert too_big.status_code == 413

    assert client.get(url).status_code == 404  # nothing was stored


def test_campaign_cover_caching_and_thumbnail(client):
    _wid, cid = _campaign(client)
    url = f"/api/campaigns/{cid}/cover"
    client.put(url, files={"file": ("c.png", io.BytesIO(_png_bytes((80, 80))), "image/png")})

    bare = client.get(url)
    assert bare.headers["cache-control"] == "no-cache" and bare.headers["etag"]
    again = client.get(url, headers={"If-None-Match": bare.headers["etag"]})
    assert again.status_code == 304

    versioned = client.get(f"{url}?v=abc")
    assert "immutable" in versioned.headers["cache-control"]

    thumb = client.get(f"{url}?w=32")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/webp"


def test_campaign_cover_reported_by_campaign_reads(client):
    _wid, cid = _campaign(client)
    assert next(c for c in client.get("/api/campaigns").json() if c["id"] == cid)["cover"] == ""
    assert client.get(f"/api/campaigns/{cid}").json()["meta"]["cover"] == ""

    client.put(f"/api/campaigns/{cid}/cover",
               files={"file": ("c.png", io.BytesIO(_png_bytes()), "image/png")})

    row = next(c for c in client.get("/api/campaigns").json() if c["id"] == cid)
    assert row["cover"] and row["cover"] == client.get(f"/api/campaigns/{cid}").json()["meta"]["cover"]


def test_campaign_cover_delete_failure_is_a_500_with_a_detail(client, monkeypatch):
    _wid, cid = _campaign(client)
    client.put(f"/api/campaigns/{cid}/cover",
               files={"file": ("c.png", io.BytesIO(_png_bytes()), "image/png")})
    monkeypatch.setattr("pathlib.Path.unlink",
                        lambda self, *a, **k: (_ for _ in ()).throw(OSError("held")))
    r = client.delete(f"/api/campaigns/{cid}/cover")
    assert r.status_code == 500 and r.json()["detail"] == "cover could not be removed"


def test_campaign_cover_upload_advances_activity(client):
    """The activity stamp comes from main.py's middleware, which keys on a `cid`
    path parameter -- this pins that the cover routes still have one."""
    _wid, cid = _campaign(client)
    before = next(c for c in client.get("/api/campaigns").json() if c["id"] == cid)["activity"]
    time.sleep(1.1)  # the stamp has one-second resolution
    client.put(f"/api/campaigns/{cid}/cover",
               files={"file": ("c.png", io.BytesIO(_png_bytes()), "image/png")})
    after = next(c for c in client.get("/api/campaigns").json() if c["id"] == cid)["activity"]
    assert after > before
```

- [ ] **Step 2: Run the tests to verify they fail**

```
cd backend && PYTHONPATH=src .venv/Scripts/python -m pytest tests/test_routes.py -k cover -v
```

Expected: FAIL — the cover URLs 404 through the generic entity routes / the
`cover` key is missing.

- [ ] **Step 3: Split the image-serving helper**

In `backend/src/grimoire/routes/common.py`, replace `_serve_image`'s body with
a resolver plus a new file-level function:

```python
def _serve_image(root, cid: str, vid: str, name: str, base: str = "characters",
                 request: Request | None = None):
    p = store.assets.image_path(root, cid, vid, name, base)
    if p is None:
        raise HTTPException(status_code=404, detail="image not found")
    return _serve_image_file(p, request)


def _serve_image_file(p, request: Request | None = None):
    """Serve one image file with the app's caching contract.

    Bare URLs are no-cache: promotions swap file contents under stable URLs,
    so the browser must revalidate — with an ETag that's a 304, not a
    re-download. A `?v=` URL (built from list responses' version tokens) names
    one exact content state, so it caches immutable: zero requests on later
    renders.

    An `OSError` reading the file is a 404, not a 500: an image can be replaced
    or removed between the caller resolving its path and this reading it, and
    that is a missing image rather than a server fault.
    """
    try:
        st = p.stat()
    except OSError:
        raise HTTPException(status_code=404, detail="image not found")
    etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
    versioned = request is not None and "v" in request.query_params
    cache = "public, max-age=31536000, immutable" if versioned else "no-cache"
    headers = {"Cache-Control": cache, "ETag": etag}
    if request is not None and etag in request.headers.get("if-none-match", ""):
        return Response(status_code=304, headers=headers)
    # ?w= asks for a downscaled variant — tiles shouldn't pull multi-MB originals.
    # An undecodable source just serves the original bytes.
    if request is not None and (w := request.query_params.get("w", "")).isdigit():
        tp = store.thumbs.thumbnail(p, max(16, min(1024, int(w))))
        if tp is not None:
            return Response(content=tp.read_bytes(), media_type="image/webp", headers=headers)
    ext = p.suffix.lstrip(".").lower()
    try:
        content = p.read_bytes()
    except OSError:
        raise HTTPException(status_code=404, detail="image not found")
    return Response(content=content,
                    media_type=_IMAGE_MEDIA.get(ext, "application/octet-stream"),
                    headers=headers)
```

- [ ] **Step 4: Add the routes and the `cover` field**

In `backend/src/grimoire/routes/campaigns.py`, extend the `.common` import
with `_serve_image_file`, and add the three routes immediately after the
export routes (before `@router.put("/campaigns/{cid}")`):

```python
# ---- the campaign's cover image (store/covers.py) --------------------------
# Declared here, in `campaigns`, which `routes/__init__` includes BEFORE
# `entities` -- `/campaigns/{cid}/{kind}` would otherwise capture `cover`.
@router.get("/campaigns/{cid}/cover")
def get_campaign_cover(cid: str, request: Request):
    _campaign_root_or_404(cid)
    p = store.covers.cover_path(cid)
    if p is None:
        raise HTTPException(status_code=404, detail="cover not found")
    return _serve_image_file(p, request)


@router.put("/campaigns/{cid}/cover")
async def put_campaign_cover(cid: str, file: UploadFile = File(...)):
    _campaign_root_or_404(cid)
    data = await file.read()
    try:
        store.covers.validate(data)
    except store.covers.CoverTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except store.covers.CoverInvalid as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    fn = file.filename or ""
    ext = fn.rsplit(".", 1)[-1] if "." in fn else ""
    try:
        stored = store.covers.put_cover(cid, data, ext)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ext": stored, "v": store.covers.cover_version(cid)}


@router.delete("/campaigns/{cid}/cover")
def delete_campaign_cover(cid: str):
    _campaign_root_or_404(cid)
    try:
        store.covers.delete_cover(cid)
    except OSError:
        # `delete_cover` confirms the removal rather than swallowing a failed
        # unlink, so this is a cover that is genuinely still there -- a held
        # file on Windows, a read-only store. Reporting 200 would be a lie.
        raise HTTPException(status_code=500, detail="cover could not be removed")
    return {"ok": True}
```

In `get_campaigns` (`:89`), add the cover token to each row:

```python
        out.append({**c, "scenes": len(scene_list),
                    "cover": store.covers.cover_version(c["id"]),
                    "last_scene": scene_list[0]["title"] if scene_list else "",
```

In `get_campaign` (`:164`), beside the injected `world_name`:

```python
    out["meta"]["world_name"] = store.worlds.world_name(wid) or wid
    # Derived, like world_name above -- nothing about the cover is written into
    # campaign.md, whose unlocked read-modify-writers would race it.
    out["meta"]["cover"] = store.covers.cover_version(cid)
    return out
```

- [ ] **Step 5: Run the route tests and the ordering guard**

```
cd backend && PYTHONPATH=src .venv/Scripts/python -m pytest tests/test_routes.py tests/test_route_order.py -v
```

Expected: PASS. `test_route_order.py` proves `/campaigns/{cid}/cover` is not
shadowed by `/campaigns/{cid}/{kind}`.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/routes/common.py backend/src/grimoire/routes/campaigns.py \
        backend/tests/test_routes.py
git commit -m "Serve, upload and remove a campaign cover over HTTP"
```

---

### Task 4: The cover in the EPUB

**Files:**
- Modify: `backend/src/grimoire/store/export.py:186-224` (`collect`), `backend/src/grimoire/store/epub.py:73-118` (`build_epub`), `templates/epub/package.opf`, `templates/epub/stylesheet.css`
- Create: `templates/epub/cover.xhtml`
- Test: `backend/tests/test_epub_store.py`, `backend/tests/test_export_store.py`

**Interfaces:**
- Consumes: `covers.cover_path(cid)`.
- Produces: `collect(...)["cover"]` — a `Path` or `None`. Manifest item id `cover-img`; packed at `images/cover.<ext>`; page at `text/cover.xhtml`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_epub_store.py` (the module already has
`_fixture_campaign`, `_open` and `OPF_NS`; add `from grimoire.store import covers`
and a `_png` helper like Task 2's):

```python
def test_build_epub_without_a_cover_is_structurally_unchanged(monkeypatch, tmp_path):
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    z = _open(epub.build_epub(cid)[0])
    assert "text/cover.xhtml" not in z.namelist()
    assert "images/cover.png" not in z.namelist()
    opf = ET.fromstring(z.read("package.opf"))
    assert not [i for i in opf.findall(".//opf:item", OPF_NS)
                if i.get("properties") == "cover-image"]
    assert opf.find(".//opf:meta[@name='cover']", OPF_NS) is None
    first = opf.findall(".//opf:itemref", OPF_NS)[0].get("idref")
    assert {i.get("id"): i.get("href") for i in opf.findall(".//opf:item", OPF_NS)}[first] \
        == "text/titlepage.xhtml"


def test_build_epub_with_a_cover(monkeypatch, tmp_path):
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    data = _png()
    covers.put_cover(cid, data, "png")

    z = _open(epub.build_epub(cid)[0])
    assert z.read("images/cover.png") == data
    page = z.read("text/cover.xhtml").decode()
    assert "../images/cover.png" in page and "Run One" in page

    opf = ET.fromstring(z.read("package.opf"))
    items = {i.get("id"): i for i in opf.findall(".//opf:item", OPF_NS)}
    assert items["cover-img"].get("properties") == "cover-image"
    assert items["cover-img"].get("href") == "images/cover.png"
    assert opf.find(".//opf:meta[@name='cover']", OPF_NS).get("content") == "cover-img"

    spine = [ref.get("idref") for ref in opf.findall(".//opf:itemref", OPF_NS)]
    assert items[spine[0]].get("href") == "text/cover.xhtml"
    assert items[spine[1]].get("href") == "text/titlepage.xhtml"
    # not a ToC entry, by convention
    assert "cover.xhtml" not in z.read("nav.xhtml").decode()


def test_build_epub_drops_a_cover_that_vanishes_mid_export(monkeypatch, tmp_path):
    """The panel that replaces a cover sits next to the Export menu, so this
    window is reachable. An export must degrade, not 500."""
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    covers.put_cover(cid, _png(), "png")
    real = pathlib.Path.read_bytes

    def vanishing(self, *a, **k):
        if self.name == "cover.png":
            raise OSError("gone")
        return real(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_bytes", vanishing)
    z = _open(epub.build_epub(cid)[0])
    assert "text/cover.xhtml" not in z.namelist()
    assert "images/cover.png" not in z.namelist()
    opf = ET.fromstring(z.read("package.opf"))
    assert not [i for i in opf.findall(".//opf:item", OPF_NS)
                if i.get("properties") == "cover-image"]
```

Append to `backend/tests/test_export_store.py`:

```python
def test_cover_is_not_packed_into_the_other_exports(monkeypatch, tmp_path):
    """The cover must stay out of the shared image registry: every other
    renderer packs everything in it."""
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    before = sorted(export.collect(cid)["images"].by_path.values())
    covers.put_cover(cid, b"\x89PNG-cover", "png")
    data = export.collect(cid)
    assert data["cover"] is not None
    assert sorted(data["images"].by_path.values()) == before  # no renumbering

    blob, _ = export.build_markdown_bundle(cid)
    names = zipfile.ZipFile(io.BytesIO(blob)).namelist()
    assert not [n for n in names if "cover" in n]

    html, _ = export.build_html(cid)
    assert b"cover" not in html
```

(Use whatever fixture helper `test_export_store.py` already defines; add
`from grimoire.store import covers` and, if absent, `import io, zipfile`.)

- [ ] **Step 2: Run the tests to verify they fail**

```
cd backend && PYTHONPATH=src .venv/Scripts/python -m pytest tests/test_epub_store.py tests/test_export_store.py -v
```

Expected: the new tests FAIL with `KeyError: 'cover'` / missing `cover.xhtml`.

- [ ] **Step 3: Add the cover to `collect`**

In `backend/src/grimoire/store/export.py`, import the new module with the
sibling-package form the import guard requires — the existing line becomes:

```python
from . import assets, calendars, chronicle, characters, covers, entities, overlay, pcs, worlds
```

and `collect`'s return dict gains one key:

```python
    return {
        "title": campaign["meta"].get("name", cid),
        "world_name": world_name,
        # The cover is a PATH, deliberately NOT registered in `images`: every
        # renderer packs everything in that registry (`build_markdown_bundle`
        # zips it, `build_html` base64-inlines it), so registering the cover
        # would ship it into exports that never reference it -- and would
        # renumber every other packed image. Only `epub.build_epub` uses this.
        "cover": covers.cover_path(cid),
        "date_range": date_range,
        ...
    }
```

- [ ] **Step 4: Pack it in `build_epub`**

In `backend/src/grimoire/store/epub.py`, rewrite `build_epub`'s opening so the
cover bytes are staged **before** the manifest is composed:

```python
def build_epub(cid: str) -> tuple[bytes, str]:
    """The whole campaign as an EPUB 3 book: (bytes, suggested filename)."""
    data = _export.collect(cid, image_prefix="../images/")  # raises CampaignNotFound
    images = data["images"]
    chapters = [_chapter_doc(c) for c in data["chapters"]]
    appendix = [_appendix_doc(e) for e in data["appendix"]]
    title = data["title"]

    # Read the cover FIRST, so "there is a cover" is one decision everything
    # downstream follows. The manifest is composed here and the image bytes are
    # written much later, at zip time -- reading it there instead would let a
    # cover deleted mid-export leave a cover page and a manifest entry pointing
    # at a file that never got written. A vanished cover simply produces the
    # no-cover book, which is what an export owes the user over an exception.
    cover_bytes, cover_name = None, ""
    if data["cover"] is not None:
        try:
            cover_bytes = data["cover"].read_bytes()
            cover_name = f"cover{data['cover'].suffix.lower()}"
        except OSError:
            cover_bytes, cover_name = None, ""

    docs = []
    if cover_bytes is not None:
        docs.append(("text/cover.xhtml",
                     _render("cover.xhtml", title=title, src=f"../images/{cover_name}")))
    docs.append(("text/titlepage.xhtml",
                 _render("titlepage.xhtml", title=title, world=data["world_name"],
                         date_range=data["date_range"])))
    docs += [(f"text/{c['file']}", c["doc"]) for c in chapters]
    if appendix:
        docs.append(("text/appendix.xhtml", _render("divider.xhtml", title="Appendix")))
        docs += [(f"text/{e['file']}", e["doc"]) for e in appendix]

    fonts = sorted(FONTS_DIR.glob("*.ttf")) if FONTS_DIR.exists() else []
    # Every item carries `properties` (usually ""): the template environment is
    # StrictUndefined, so an item missing the key raises rather than rendering
    # an empty attribute.
    items = [{"id": f"doc-{i}", "href": href, "media_type": "application/xhtml+xml",
              "properties": ""}
             for i, (href, _) in enumerate(docs)]
    spine = [it["id"] for it in items]
    items.append({"id": "css", "href": "css/stylesheet.css", "media_type": "text/css",
                  "properties": ""})
    items += [{"id": f"font-{i}", "href": f"fonts/{f.name}", "media_type": "font/ttf",
               "properties": ""}
              for i, f in enumerate(fonts)]
    items += [{"id": f"img-{i}", "href": f"images/{name}",
               "media_type": _EXT_MEDIA.get(name.rsplit(".", 1)[-1], "application/octet-stream"),
               "properties": ""}
              for i, name in enumerate(images.by_path.values())]
    if cover_bytes is not None:
        items.append({"id": "cover-img", "href": f"images/{cover_name}",
                      "media_type": _EXT_MEDIA.get(cover_name.rsplit(".", 1)[-1],
                                                   "application/octet-stream"),
                      "properties": "cover-image"})

    opf = _render("package.opf", identifier=f"urn:grimoire:campaign:{cid}", title=title,
                  modified=data["updated"] or now_iso(),
                  cover_id="cover-img" if cover_bytes is not None else "",
                  items=items, spine=spine)
    nav = _render("nav.xhtml", chapters=chapters, appendix=appendix)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", _render("container.xml"))
        z.writestr("package.opf", opf)
        z.writestr("nav.xhtml", nav)
        for href, doc in docs:
            z.writestr(href, doc)
        z.writestr("css/stylesheet.css", _render("stylesheet.css"))
        for f in fonts:
            z.writestr(f"fonts/{f.name}", f.read_bytes())
        for p, name in images.by_path.items():
            z.writestr(f"images/{name}", p.read_bytes())
        if cover_bytes is not None:
            z.writestr(f"images/{cover_name}", cover_bytes)
    return buf.getvalue(), f"{cid}.epub"
```

- [ ] **Step 5: Add and update the templates**

Create `templates/epub/cover.xhtml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>{{ title }}</title>
  <link rel="stylesheet" type="text/css" href="../css/stylesheet.css"/>
</head>
<body class="cover">
  <img src="{{ src }}" alt="{{ title }}"/>
</body>
</html>
```

In `templates/epub/package.opf`, emit the legacy cover meta and the optional
`properties` attribute:

```xml
    <meta property="dcterms:modified">{{ modified }}</meta>
{% if cover_id %}    <meta name="cover" content="{{ cover_id }}"/>
{% endif %}  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
{% for it in items %}    <item id="{{ it.id }}" href="{{ it.href }}" media-type="{{ it.media_type }}"{% if it.properties %} properties="{{ it.properties }}"{% endif %}/>
{% endfor %}  </manifest>
```

Append to `templates/epub/stylesheet.css`, after the `.titlepage` block:

```css
/* Cover page: centred both ways, never upscaled — a stretched low-res cover
   looks worse than a small sharp one. Percentages against an explicit height
   context rather than vh, whose support in older reading systems is uneven. */
html, body.cover { height: 100%; margin: 0; }
body.cover { display: flex; align-items: center; justify-content: center; }
.cover img { max-width: 100%; max-height: 100%; }
```

- [ ] **Step 6: Run the export and template suites**

```
cd backend && PYTHONPATH=src .venv/Scripts/python -m pytest tests/test_epub_store.py tests/test_export_store.py tests/test_frozen_campaign.py -v
```

Expected: PASS. Then the template harnesses:

```
cd backend && PYTHONPATH=src .venv/Scripts/python ../scripts/verify_templates.py
```

Expected: no diff reported.

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/store/export.py backend/src/grimoire/store/epub.py \
        templates/epub/cover.xhtml templates/epub/package.opf templates/epub/stylesheet.css \
        backend/tests/test_epub_store.py backend/tests/test_export_store.py
git commit -m "Use the campaign cover as the EPUB cover"
```

---

### Task 5: The cover panel in the campaign

**Files:**
- Modify: `frontend/src/api/client.ts:197-215` (`CampaignMeta`) and the api object near `putEntityImage` (`:1131`)
- Create: `frontend/src/components/CampaignCover.tsx`, `frontend/src/components/CampaignCover.test.tsx`
- Modify: `frontend/src/routes/CampaignView.tsx` (panel state near `:240`, rail-foot button near `:3019`, panel slot near `:3036`)

**Interfaces:**
- Consumes: the routes from Task 3.
- Produces:
  - `api.campaignCoverUrl(cid: string, opts?: { w?: number; v?: string }): string`
  - `api.putCampaignCover(cid: string, file: File): Promise<{ ext: string; v: string }>`
  - `api.deleteCampaignCover(cid: string): Promise<{ ok: boolean }>`
  - `CampaignMeta.cover?: string`
  - `<CampaignCover cid={cid} />`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/CampaignCover.test.tsx`:

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CampaignCover } from "./CampaignCover";

vi.mock("../api/client", () => ({
  api: {
    getCampaign: vi.fn(),
    putCampaignCover: vi.fn(),
    deleteCampaignCover: vi.fn(),
    campaignCoverUrl: (cid: string, o?: { v?: string }) =>
      `/api/campaigns/${cid}/cover${o?.v ? `?v=${o.v}` : ""}`,
  },
}));
import { api } from "../api/client";

const file = () => new File(["png"], "cover.png", { type: "image/png" });

beforeEach(() => {
  vi.clearAllMocks();
  (api.getCampaign as any).mockResolvedValue({ meta: { id: "run", name: "Saltmarch Nights", cover: "" }, body: "" });
  (api.putCampaignCover as any).mockResolvedValue({ ext: "png", v: "abc" });
  (api.deleteCampaignCover as any).mockResolvedValue({ ok: true });
});

test("shows the placeholder when there is no cover", async () => {
  render(<CampaignCover cid="run" />);
  expect(await screen.findByText(/no cover/i)).toBeTruthy();
  expect(screen.queryByRole("img")).toBeNull();
});

test("uploading a file stores it and shows it at the new version", async () => {
  render(<CampaignCover cid="run" />);
  const input = await screen.findByLabelText(/cover image/i);
  fireEvent.change(input, { target: { files: [file()] } });
  await waitFor(() => expect(api.putCampaignCover).toHaveBeenCalledWith("run", expect.any(File)));
  const img = await screen.findByRole("img");
  expect(img.getAttribute("src")).toBe("/api/campaigns/run/cover?v=abc");
});

test("shows an existing cover and removes it", async () => {
  (api.getCampaign as any).mockResolvedValue({ meta: { id: "run", name: "Saltmarch Nights", cover: "v1" }, body: "" });
  render(<CampaignCover cid="run" />);
  expect((await screen.findByRole("img")).getAttribute("src")).toBe("/api/campaigns/run/cover?v=v1");
  fireEvent.click(screen.getByRole("button", { name: /remove/i }));
  await waitFor(() => expect(api.deleteCampaignCover).toHaveBeenCalledWith("run"));
  expect(await screen.findByText(/no cover/i)).toBeTruthy();
});

test("a rejected upload shows the error and keeps the current cover", async () => {
  (api.getCampaign as any).mockResolvedValue({ meta: { id: "run", name: "Saltmarch Nights", cover: "v1" }, body: "" });
  (api.putCampaignCover as any).mockRejectedValue({ detail: "not a readable image" });
  render(<CampaignCover cid="run" />);
  const input = await screen.findByLabelText(/cover image/i);
  fireEvent.change(input, { target: { files: [file()] } });
  expect(await screen.findByText("not a readable image")).toBeTruthy();
  expect((screen.getByRole("img") as HTMLImageElement).getAttribute("src")).toBe("/api/campaigns/run/cover?v=v1");
});
```

- [ ] **Step 2: Run the test to verify it fails**

```
cd frontend && npx vitest run src/components/CampaignCover.test.tsx
```

Expected: FAIL — `Failed to resolve import "./CampaignCover"`.

- [ ] **Step 3: Add the api client functions**

In `frontend/src/api/client.ts`, add `cover?: string` to `CampaignMeta` with a
comment:

```ts
  /** Cache-busting token for the campaign's cover image, "" when it has none.
   *  A token rather than a boolean: it also makes the URL change when the
   *  bytes do, so a replaced cover cannot keep rendering from cache. */
  cover?: string;
```

and, next to `putEntityImage`:

```ts
  campaignCoverUrl: (cid: string, opts?: { w?: number; v?: string }) => {
    const q = new URLSearchParams();
    if (opts?.w) q.set("w", String(opts.w));
    if (opts?.v) q.set("v", opts.v);
    const qs = q.toString();
    return `/api/campaigns/${cid}/cover${qs ? `?${qs}` : ""}`;
  },
  putCampaignCover: (cid: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return requestForm<{ ext: string; v: string }>(`/api/campaigns/${cid}/cover`, form, "PUT");
  },
  deleteCampaignCover: (cid: string) =>
    request<{ ok: boolean }>("DELETE", `/api/campaigns/${cid}/cover`),
```

- [ ] **Step 4: Write the panel**

Create `frontend/src/components/CampaignCover.tsx`:

```tsx
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

/** The campaign's cover image: shown on the campaigns list and used as the
 *  cover of the exported EPUB. A settings panel (the CalendarConfig shape),
 *  not a list/detail editor — there is one image or none. */
export function CampaignCover({ cid }: { cid: string }) {
  const [version, setVersion] = useState<string | null>(null);  // null = loading
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setVersion(null);
    api.getCampaign(cid)
      .then((r) => setVersion(r.meta.cover ?? ""))
      .catch(() => setVersion(""));
  }, [cid]);

  async function upload(file: File) {
    setError(null);
    setBusy(true);
    try {
      const r = await api.putCampaignCover(cid, file);
      setVersion(r.v);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";  // re-picking the same file re-fires
    }
  }

  async function remove() {
    setError(null);
    setBusy(true);
    try {
      await api.deleteCampaignCover(cid);
      setVersion("");
    } catch (err: any) {
      // The backend confirms the unlink, so a failure means the cover is
      // genuinely still there — leave it on screen.
      setError(err.detail ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  if (version === null) return <div className="field-hint">Loading cover…</div>;

  return (
    <div className="campaign-cover">
      {error && <div className="banner">{error}</div>}
      {version
        ? <img className="cover-preview" src={api.campaignCoverUrl(cid, { v: version })} alt="Campaign cover" />
        : <p className="field-hint">No cover set. It is used on the campaigns list and as the cover of the exported EPUB.</p>}
      <label className="field-hint" htmlFor="campaign-cover-file">Cover image</label>
      <input id="campaign-cover-file" ref={input} type="file" disabled={busy}
             accept="image/png,image/jpeg,image/gif,image/webp"
             onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); }} />
      {version && (
        <button className="subtle" disabled={busy} onClick={remove}>Remove cover</button>
      )}
    </div>
  );
}
```

- [ ] **Step 5: Run the panel test**

```
cd frontend && npx vitest run src/components/CampaignCover.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Wire it into `CampaignView`**

In `frontend/src/routes/CampaignView.tsx`:

1. Import it beside the other panel components:
   `import { CampaignCover } from "../components/CampaignCover";`
2. Beside `const [showStyle, setShowStyle] = useState(false);` (`:242`):
   `const [showCover, setShowCover] = useState(false);`
3. In the `rail-foot`, after the Response button:

```tsx
          <button className="rail-date" onClick={() => setShowCover((v) => !v)}
                  title="Cover image, used on the campaigns list and in the EPUB export">
            Cover
          </button>
```

4. After the `showStyle` panel slot:

```tsx
        {showCover && (
          <div className="panel-slot">
            <CampaignCover cid={cid} />
          </div>
        )}
```

- [ ] **Step 7: Run the campaign view suite and the typechecker**

```
cd frontend && npx vitest run src/routes/CampaignView.test.tsx src/components/CampaignCover.test.tsx && npm run typecheck
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/components/CampaignCover.tsx \
        frontend/src/components/CampaignCover.test.tsx frontend/src/routes/CampaignView.tsx
git commit -m "Add the campaign cover panel"
```

---

### Task 6: The cover on the campaigns list

**Files:**
- Modify: `frontend/src/routes/CampaignsView.tsx:48-71`, `frontend/src/index.css` (after the `.list-row-meta` block at `:111`)
- Test: `frontend/src/routes/CampaignsView.test.tsx`

**Interfaces:**
- Consumes: `CampaignMeta.cover`, `api.campaignCoverUrl` from Task 5.
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/routes/CampaignsView.test.tsx` (follow the file's
existing mock setup for `api.listCampaigns` / `api.listWorlds`; add
`campaignCoverUrl` to the mocked `api` object if it is not already there):

```tsx
test("renders a thumbnail for a campaign with a cover and a placeholder without", async () => {
  (api.listCampaigns as any).mockResolvedValue([
    { id: "saltmarch", name: "Saltmarch Nights", world: "realm", scenes: 3, last_scene: "Arrival", cover: "v1" },
    { id: "winifred", name: "Winifred's War", world: "realm", scenes: 1, last_scene: "", cover: "" },
  ]);
  render(<MemoryRouter><CampaignsView /></MemoryRouter>);

  const img = await screen.findByAltText("Saltmarch Nights cover");
  expect(img.getAttribute("src")).toContain("/api/campaigns/saltmarch/cover");
  expect(img.getAttribute("src")).toContain("v=v1");
  expect(screen.queryByAltText("Winifred's War cover")).toBeNull();
  expect(document.querySelectorAll(".list-row-cover").length).toBe(2);  // both boxes, aligned
});
```

- [ ] **Step 2: Run the test to verify it fails**

```
cd frontend && npx vitest run src/routes/CampaignsView.test.tsx
```

Expected: FAIL — no element with alt text `Saltmarch Nights cover`.

- [ ] **Step 3: Render the thumbnail**

In `frontend/src/routes/CampaignsView.tsx`, add state for covers that fail to
load and render the box as the first child of each `.list-row`:

```tsx
  const [broken, setBroken] = useState<Record<string, boolean>>({});
```

```tsx
          <div className="list-row" key={c.id}>
            <div className="list-row-cover">
              {c.cover && !broken[c.id] && (
                <img src={api.campaignCoverUrl(c.id, { w: 96, v: c.cover })}
                     alt={`${c.name} cover`}
                     onError={() => setBroken((b) => ({ ...b, [c.id]: true }))} />
              )}
            </div>
```

The empty box is deliberate: a row without a cover keeps the same layout as
one with it, so the list does not jump between two shapes. `onError` covers
the cover being removed in another tab between the list response and the
image request.

- [ ] **Step 4: Add the CSS**

In `frontend/src/index.css`, after the `.list-row-meta` block:

```css
.list-row-cover { flex: 0 0 auto; width: 64px; height: 64px; margin-left: 20px; background: var(--rule-soft); }
.list-row-cover img { width: 100%; height: 100%; object-fit: cover; display: block; }
```

- [ ] **Step 5: Run the suite and the typechecker**

```
cd frontend && npx vitest run src/routes/CampaignsView.test.tsx && npm run typecheck
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/CampaignsView.tsx frontend/src/routes/CampaignsView.test.tsx \
        frontend/src/index.css
git commit -m "Show campaign covers on the campaigns list"
```

---

### Task 7: Full gate

**Files:** none — this task verifies.

- [ ] **Step 1: Run the whole gate**

```
make check
```

Expected: PASS for `check-py`, `check-web`, `check-lint`, `check-templates`
and `check-pydantic1`. In a worktree, pass `PY` explicitly, e.g.
`make check PY=C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe`.

- [ ] **Step 2: Fix anything the gate reports, then re-run it**

Do not proceed on a partial pass. `check-pydantic1` in particular runs the
suite against the Android dependency set, which is where a Pillow or pydantic
assumption would surface.

- [ ] **Step 3: Drive it in a browser**

Use the `verify` skill to launch the app against an isolated store, then:
set a cover from the campaign's **Cover** panel, confirm the thumbnail appears
on the campaigns list, replace it and confirm the displayed image changes,
export the EPUB and open it in a reader to confirm the cover shows on the
shelf and as the first page, then Remove the cover and confirm both the list
and a fresh export go back to no cover.

- [ ] **Step 4: Run the implementation review gate**

Per `CLAUDE.md`, `/codex:review` against the diff. Note that the Codex sandbox
helper is missing on this machine, so the plugin command silently reviews
GitHub instead of the local diff — pipe the diff to `codex exec` instead:

```bash
git diff main...HEAD > /tmp/cover.patch
cat /tmp/cover.patch | codex exec --sandbox read-only --skip-git-repo-check "Your shell is broken; review ONLY the diff on <stdin>. …"
```

- [ ] **Step 5: Run the final adversarial gate**

`/codex:adversarial-review` (same `codex exec` workaround) against the diff
**and** `docs/superpowers/specs/2026-08-08-campaign-cover-image-design.md`,
asking specifically whether the changes implement the spec — gaps, drift and
quietly-dropped requirements, not style.

- [ ] **Step 6: Commit any fixes and finish the branch**

Use `superpowers:finishing-a-development-branch`. Integrate with a
rebase-merge, not `merge --no-ff`.

---

## Self-Review

**Spec coverage:** storage layout and `covers.py` → Task 2; the `assets.py`
extraction with `supported_only` → Task 1; the three routes, `_serve_image_file`,
upload validation and the `cover` token on both campaign reads → Task 3; the
`collect` key, the packing order, `cover.xhtml`, `package.opf`, the stylesheet
and the degrade-on-vanish behaviour → Task 4; the panel and its rail-foot
button → Task 5; the list thumbnail with its placeholder and `onError` → Task
6; `make check`, browser verification and the Codex gates → Task 7. The spec's
"what needs no change" (activity stamping) is covered by an assertion in Task
3 rather than by code, as intended.

**Placeholders:** none — every step carries the code or the exact command.

**Type consistency:** `path_in` / `put_in` / `delete_in` keep one signature
across Tasks 1–2; `cover_version` returns `str` and is what Task 3 puts in the
`cover` field and Task 5 reads as `CampaignMeta.cover`; the manifest id
`cover-img` and the packed name `images/cover.<ext>` are used identically in
Task 4's code, templates and tests.
