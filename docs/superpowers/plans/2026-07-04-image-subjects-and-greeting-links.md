# Image Subjects + Greeting Links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Assign characters to greeting images ("subjects"), surface them as an "Appears in" gallery + copy-to-avatar/gallery on the character page, and add navigable "World greetings" links from characters to greetings.

**Architecture:** A `subjects.json` sidecar per greeting asset folder (the `focus.json` pattern) owned by a new `store/image_subjects.py`; four thin routes; frontend renders chips under greeting images via a `GreetingMarkdown` render-prop, a `SubjectsPopover` picker, and two new sections in CharacterEditor's detail view, with cross-tab jumps mirroring the existing `focusChar` wiring.

**Tech Stack:** FastAPI + pytest backend; React + vitest frontend (react-markdown, testing-library, `vi.mock` of `../api/client`).

**Spec:** `docs/superpowers/specs/2026-07-04-image-subjects-and-greeting-links-design.md`

## Global Constraints

- Naming is **subjects** everywhere — never "tags" (player-trait tags already exist).
- Greeting image **bytes** stay read-only over HTTP; only the sidecar is writable.
- Copy-to-character **copies bytes** via `assets.put_image` (avatar slot keeps existing avatar semantics incl. focus reset).
- Backend tests: `backend/.venv/Scripts/python.exe -m pytest backend -q`. Frontend: from `frontend/`, `npx vitest run` and `npx tsc -b` (never `npx --prefix`).
- Tolerant reads (garbled/missing sidecar → `{}`; vanished images/cids drop out); strict writes (unknown image name → `ValueError`; routes map to 404/400).
- Work happens on a branch in a worktree under `.worktrees/` (repo convention).

---

### Task 1: store `image_subjects` — sidecar read/write/set

**Files:**
- Create: `backend/src/grimoire/store/image_subjects.py`
- Modify: `backend/src/grimoire/store/__init__.py` (add `image_subjects` to the import list and `__all__`)
- Test: `backend/tests/test_image_subjects_store.py` (create)

**Interfaces:**
- Consumes: `assets.put_image/list_images/image_path(..., base="greetings")`, `characters.list_characters`.
- Produces (Tasks 2–3 rely on these exact signatures):
  - `SUBJECTS_FILE = "subjects.json"`
  - `read_subjects(root, gid) -> dict[str, list[str]]`
  - `write_subjects(root, gid, subjects: dict[str, list[str]]) -> None` (ValueError on unknown image name)
  - `set_image_subjects(root, gid, name, cids: list[str]) -> None`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_image_subjects_store.py`:

```python
import pytest

from grimoire.store import assets, characters, greetings, image_subjects


def _world(tmp_path, images=("art_1", "art_2")):
    cid, vid = characters.create_character(tmp_path, "Mira", "main")
    gid = greetings.create_greeting(tmp_path, "Opener", cid, vid, "body")
    for name in images:
        assets.put_image(tmp_path, gid, "default", name, b"png", "png", base="greetings")
    return cid, gid


def test_subjects_roundtrip_and_missing_file(tmp_path):
    cid, gid = _world(tmp_path)
    assert image_subjects.read_subjects(tmp_path, gid) == {}
    image_subjects.write_subjects(tmp_path, gid, {"art_1": [cid]})
    assert image_subjects.read_subjects(tmp_path, gid) == {"art_1": [cid]}


def test_write_rejects_unknown_image_and_drops_empty(tmp_path):
    cid, gid = _world(tmp_path)
    with pytest.raises(ValueError):
        image_subjects.write_subjects(tmp_path, gid, {"nope": [cid]})
    image_subjects.write_subjects(tmp_path, gid, {"art_1": [cid], "art_2": []})
    assert image_subjects.read_subjects(tmp_path, gid) == {"art_1": [cid]}


def test_read_drops_vanished_images_and_characters(tmp_path):
    cid, gid = _world(tmp_path)
    image_subjects.write_subjects(tmp_path, gid, {"art_1": [cid, "ghost"], "art_2": [cid]})
    assets.delete_image(tmp_path, gid, "default", "art_2", base="greetings")
    assert image_subjects.read_subjects(tmp_path, gid) == {"art_1": [cid]}


def test_read_tolerates_garbled_sidecar(tmp_path):
    _cid, gid = _world(tmp_path)
    image_subjects.subjects_path(tmp_path, gid).write_text("{not json", encoding="utf-8")
    assert image_subjects.read_subjects(tmp_path, gid) == {}


def test_set_image_subjects_updates_one_entry(tmp_path):
    cid, gid = _world(tmp_path)
    image_subjects.set_image_subjects(tmp_path, gid, "art_1", [cid])
    image_subjects.set_image_subjects(tmp_path, gid, "art_2", [cid])
    image_subjects.set_image_subjects(tmp_path, gid, "art_1", [])
    assert image_subjects.read_subjects(tmp_path, gid) == {"art_2": [cid]}
```

(`assets.delete_image` signature is `delete_image(root, cid, vid, name, base)` — for greeting images the "cid" slot carries the gid and vid is `"default"`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_image_subjects_store.py -q`
Expected: ImportError / AttributeError — `image_subjects` doesn't exist

- [ ] **Step 3: Implement**

Create `backend/src/grimoire/store/image_subjects.py`:

```python
"""Per-greeting image subjects — which characters appear in each localized
greeting image. Sidecar at <root>/greetings/<gid>/assets/default/subjects.json
(the focus.json pattern): {"<image-name>": ["<cid>", ...]}. Tolerant reads,
strict writes. Deliberately named "subjects", not "tags" — tags mean
player-trait gating elsewhere in the store.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import assets, characters

SUBJECTS_FILE = "subjects.json"
_BASE = "greetings"
_VID = "default"


def subjects_path(root: Path, gid: str) -> Path:
    return root / _BASE / gid / "assets" / _VID / SUBJECTS_FILE


def _image_names(root: Path, gid: str) -> set[str]:
    return {i["name"] for i in assets.list_images(root, gid, _VID, base=_BASE)}


def read_subjects(root: Path, gid: str) -> dict[str, list[str]]:
    """Tolerant: {} on missing/garbled file; entries for vanished images and
    deleted characters drop out silently (no dangling chips)."""
    p = subjects_path(root, gid)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    names = _image_names(root, gid)
    cids = {c["id"] for c in characters.list_characters(root)}
    out: dict[str, list[str]] = {}
    for name, subs in raw.items():
        if name not in names or not isinstance(subs, list):
            continue
        kept = [c for c in subs if c in cids]
        if kept:
            out[name] = kept
    return out


def write_subjects(root: Path, gid: str, subjects: dict[str, list[str]]) -> None:
    """Strict: every key must be a stored image of this greeting. Empty lists
    are dropped; an all-empty map removes the file's entries (writes {})."""
    names = _image_names(root, gid)
    unknown = set(subjects) - names
    if unknown:
        raise ValueError(f"unknown image(s): {sorted(unknown)}")
    trimmed = {n: list(subs) for n, subs in subjects.items() if subs}
    p = subjects_path(root, gid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(trimmed, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def set_image_subjects(root: Path, gid: str, name: str, cids: list[str]) -> None:
    """Read-modify-write of one image's entry (raw read: preserves entries for
    images we aren't touching even if their character was deleted)."""
    p = subjects_path(root, gid)
    cur: dict[str, list[str]] = {}
    if p.exists():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cur = loaded
        except (json.JSONDecodeError, OSError):
            cur = {}
    cur[name] = list(cids)
    write_subjects(root, gid, cur)
```

In `backend/src/grimoire/store/__init__.py`, add `image_subjects` alphabetically to the `from . import (...)` list and to `__all__` (it sits between `greetings` and `localize`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_image_subjects_store.py -q`
Expected: 5 passed

- [ ] **Step 5: Full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/image_subjects.py backend/src/grimoire/store/__init__.py backend/tests/test_image_subjects_store.py
git commit -m "feat(store): image_subjects sidecar - who appears in each greeting image"
```

---

### Task 2: store — appearances scan + copy_to_character

**Files:**
- Modify: `backend/src/grimoire/store/image_subjects.py` (append)
- Test: `backend/tests/test_image_subjects_store.py` (append)

**Interfaces:**
- Produces (Task 3 relies on):
  - `appearances(root, cid) -> list[dict]` — `[{"gid": str, "name": str}]`, sorted by (gid, name)
  - `copy_to_character(root, gid, name, cid, vid, slot) -> str` — returns stored image name; `FileNotFoundError` if source missing; `ValueError` for bad slot

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_image_subjects_store.py`:

```python
def test_appearances_scans_across_greetings_in_order(tmp_path):
    cid, _vid = characters.create_character(tmp_path, "Mira", "main")
    g1 = greetings.create_greeting(tmp_path, "B scene", cid, "main", "x")
    g2 = greetings.create_greeting(tmp_path, "A scene", cid, "main", "x")
    for gid in (g1, g2):
        assets.put_image(tmp_path, gid, "default", "art_1", b"p", "png", base="greetings")
    image_subjects.set_image_subjects(tmp_path, g1, "art_1", [cid])
    image_subjects.set_image_subjects(tmp_path, g2, "art_1", [cid])
    got = image_subjects.appearances(tmp_path, cid)
    assert got == sorted(got, key=lambda a: (a["gid"], a["name"]))
    assert {a["gid"] for a in got} == {g1, g2}
    assert image_subjects.appearances(tmp_path, "nobody") == []


def test_copy_to_character_gallery_numbers_and_avatar(tmp_path):
    cid, vid = characters.create_character(tmp_path, "Mira", "main")
    gid = greetings.create_greeting(tmp_path, "Opener", cid, vid, "x")
    assets.put_image(tmp_path, gid, "default", "art_1", b"artbytes", "png", base="greetings")
    assets.put_image(tmp_path, cid, vid, "gallery_1", b"old", "png")  # occupy slot 1

    n1 = image_subjects.copy_to_character(tmp_path, gid, "art_1", cid, vid, "gallery")
    assert n1 == "gallery_2"
    p = assets.image_path(tmp_path, cid, vid, "gallery_2")
    assert p is not None and p.read_bytes() == b"artbytes"

    assets.write_focus(tmp_path, cid, vid, 30)
    n2 = image_subjects.copy_to_character(tmp_path, gid, "art_1", cid, vid, "avatar")
    assert n2 == "avatar"
    assert assets.image_path(tmp_path, cid, vid, "avatar").read_bytes() == b"artbytes"
    assert assets.read_focus(tmp_path, cid, vid) is None  # avatar semantics reset the crop

    with pytest.raises(FileNotFoundError):
        image_subjects.copy_to_character(tmp_path, gid, "missing", cid, vid, "gallery")
    with pytest.raises(ValueError):
        image_subjects.copy_to_character(tmp_path, gid, "art_1", cid, vid, "banner")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_image_subjects_store.py -q -k "appearances or copy_to"`
Expected: AttributeError — functions don't exist

- [ ] **Step 3: Implement**

Append to `backend/src/grimoire/store/image_subjects.py`:

```python
def appearances(root: Path, cid: str) -> list[dict]:
    """Every tagged image featuring `cid`, across all greetings — the
    character page's 'Appears in' gallery. Cheap: ~one small file per
    greeting. Sorted by (gid, name) = the greetings tab's order."""
    out: list[dict] = []
    gdir = root / _BASE
    if not gdir.exists():
        return out
    for p in sorted(gdir.glob(f"*/assets/{_VID}/{SUBJECTS_FILE}")):
        gid = p.parents[2].name
        for name, subs in sorted(read_subjects(root, gid).items()):
            if cid in subs:
                out.append({"gid": gid, "name": name})
    return out


def copy_to_character(root: Path, gid: str, name: str, cid: str, vid: str, slot: str) -> str:
    """Copy a greeting image's bytes into a character version's assets.
    slot 'avatar' overwrites the avatar (focus resets, per put_image);
    slot 'gallery' takes the next free gallery_N. Returns the stored name."""
    if slot not in ("avatar", "gallery"):
        raise ValueError(f"unknown slot: {slot}")
    src = assets.image_path(root, gid, _VID, name, base=_BASE)
    if src is None:
        raise FileNotFoundError(name)
    raw, ext = src.read_bytes(), src.suffix.lstrip(".")
    if slot == "avatar":
        assets.put_image(root, cid, vid, assets.AVATAR, raw, ext)
        return assets.AVATAR
    taken = {i["name"] for i in assets.list_images(root, cid, vid)}
    n = 1
    while f"gallery_{n}" in taken:
        n += 1
    assets.put_image(root, cid, vid, f"gallery_{n}", raw, ext)
    return f"gallery_{n}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_image_subjects_store.py -q`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/image_subjects.py backend/tests/test_image_subjects_store.py
git commit -m "feat(store): appearances scan + copy greeting image into character assets"
```

---

### Task 3: routes — subjects GET/PUT, bulk subjects, appearances, copy-from-greeting

**Files:**
- Modify: `backend/src/grimoire/routes.py` (models near `GreetingCreate` ~line 185; routes in the greetings block after `delete_world_greeting` ~line 849)
- Test: `backend/tests/test_routes.py` (append)

**Interfaces:**
- Consumes: Task 1–2 functions; `_world_root_or_404`; `store.greetings.read_greeting/list_greetings`; `store.characters.read_character`.
- Produces (frontend Task 4 relies on these exact paths/shapes):
  - `GET  /api/worlds/{wid}/greetings/{gid}/subjects` → `{"<name>": ["cid", ...]}`
  - `GET  /api/worlds/{wid}/greetings/{gid}/images/{name}/subjects` → `{"subjects": [...]}`
  - `PUT  ...same...` body `{"subjects": [...]}` → `{"ok": true}` (404 unknown greeting/image, 400 unknown cid)
  - `GET  /api/worlds/{wid}/characters/{cid}/appearances` → `[{"gid","greeting_name","name","url"}]`
  - `POST /api/worlds/{wid}/characters/{cid}/versions/{vid}/images/copy-from-greeting` body `{"gid","name","slot"}` → `{"name","ext"}` (404 missing source, 400 bad slot)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_routes.py`:

```python
def test_image_subjects_routes_roundtrip_and_validation(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Mira"}).json()["character"]
    gid = client.post(f"/api/worlds/{wid}/greetings",
                      json={"name": "Opener", "character": cid, "version": "default"}).json()["id"]
    root = store.worlds.world_root(wid)
    store.assets.put_image(root, gid, "default", "embed-abc123def456", b"art", "png",
                           base="greetings")
    base = f"/api/worlds/{wid}/greetings/{gid}/images/embed-abc123def456/subjects"

    assert client.get(base).json() == {"subjects": []}
    assert client.put(base, json={"subjects": [cid]}).status_code == 200
    assert client.get(base).json() == {"subjects": [cid]}
    assert client.get(f"/api/worlds/{wid}/greetings/{gid}/subjects").json() == {
        "embed-abc123def456": [cid]}

    # validation: unknown cid -> 400; unknown image/greeting -> 404
    assert client.put(base, json={"subjects": ["ghost"]}).status_code == 400
    assert client.put(f"/api/worlds/{wid}/greetings/{gid}/images/nope/subjects",
                      json={"subjects": [cid]}).status_code == 404
    assert client.get(f"/api/worlds/{wid}/greetings/missing/images/x/subjects").status_code == 404


def test_appearances_and_copy_from_greeting(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Mira"}).json()["character"]
    gid = client.post(f"/api/worlds/{wid}/greetings",
                      json={"name": "Opener", "character": cid, "version": "default"}).json()["id"]
    root = store.worlds.world_root(wid)
    store.assets.put_image(root, gid, "default", "embed-abc123def456", b"art", "png",
                           base="greetings")
    client.put(f"/api/worlds/{wid}/greetings/{gid}/images/embed-abc123def456/subjects",
               json={"subjects": [cid]})

    apps = client.get(f"/api/worlds/{wid}/characters/{cid}/appearances").json()
    assert apps == [{"gid": gid, "greeting_name": "Opener", "name": "embed-abc123def456",
                     "url": f"/api/worlds/{wid}/greetings/{gid}/images/embed-abc123def456"}]

    copy_url = f"/api/worlds/{wid}/characters/{cid}/versions/default/images/copy-from-greeting"
    r = client.post(copy_url, json={"gid": gid, "name": "embed-abc123def456", "slot": "avatar"})
    assert r.status_code == 200 and r.json() == {"name": "avatar", "ext": "png"}
    assert client.get(f"/api/worlds/{wid}/characters/{cid}/versions/default/images/avatar").content == b"art"
    r = client.post(copy_url, json={"gid": gid, "name": "embed-abc123def456", "slot": "gallery"})
    assert r.json()["name"] == "gallery_1"
    assert client.post(copy_url, json={"gid": gid, "name": "missing", "slot": "gallery"}).status_code == 404
    assert client.post(copy_url, json={"gid": gid, "name": "embed-abc123def456", "slot": "banner"}).status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k "image_subjects or copy_from"`
Expected: FAIL — routes 404 with `{"detail":"Not Found"}`

- [ ] **Step 3: Implement**

In `backend/src/grimoire/routes.py`, next to `GreetingCreate` add:

```python
class SubjectsBody(BaseModel):
    subjects: list[str] = []


class CopyFromGreeting(BaseModel):
    gid: str
    name: str
    slot: str
```

After `delete_world_greeting` add:

```python
# ---- greeting image subjects (who appears in each localized image) ----
def _greeting_or_404(root, gid: str) -> None:
    try:
        store.greetings.read_greeting(root, gid)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")


@router.get("/worlds/{wid}/greetings/{gid}/subjects")
def get_world_greeting_subjects(wid: str, gid: str):
    root = _world_root_or_404(wid)
    _greeting_or_404(root, gid)
    return store.image_subjects.read_subjects(root, gid)


@router.get("/worlds/{wid}/greetings/{gid}/images/{name}/subjects")
def get_world_greeting_image_subjects(wid: str, gid: str, name: str):
    root = _world_root_or_404(wid)
    _greeting_or_404(root, gid)
    if store.assets.image_path(root, gid, "default", name, base="greetings") is None:
        raise HTTPException(status_code=404, detail="image not found")
    return {"subjects": store.image_subjects.read_subjects(root, gid).get(name, [])}


@router.put("/worlds/{wid}/greetings/{gid}/images/{name}/subjects")
def put_world_greeting_image_subjects(wid: str, gid: str, name: str, body: SubjectsBody):
    root = _world_root_or_404(wid)
    _greeting_or_404(root, gid)
    if store.assets.image_path(root, gid, "default", name, base="greetings") is None:
        raise HTTPException(status_code=404, detail="image not found")
    known = {c["id"] for c in store.characters.list_characters(root)}
    bad = [c for c in body.subjects if c not in known]
    if bad:
        raise HTTPException(status_code=400, detail=f"unknown characters: {bad}")
    store.image_subjects.set_image_subjects(root, gid, name, body.subjects)
    return {"ok": True}


@router.get("/worlds/{wid}/characters/{cid}/appearances")
def get_world_character_appearances(wid: str, cid: str):
    root = _world_root_or_404(wid)
    names = {g["id"]: g["name"] for g in store.greetings.list_greetings(root)}
    return [{**a, "greeting_name": names.get(a["gid"], a["gid"]),
             "url": f"/api/worlds/{wid}/greetings/{a['gid']}/images/{a['name']}"}
            for a in store.image_subjects.appearances(root, cid)]


@router.post("/worlds/{wid}/characters/{cid}/versions/{vid}/images/copy-from-greeting")
def post_copy_image_from_greeting(wid: str, cid: str, vid: str, body: CopyFromGreeting):
    root = _world_root_or_404(wid)
    try:
        stored = store.image_subjects.copy_to_character(root, body.gid, body.name, cid, vid, body.slot)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="source image not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    p = store.assets.image_path(root, cid, vid, stored)
    return {"name": stored, "ext": p.suffix.lstrip(".").lower() if p else ""}
```

(Registration order is safe: these templates have more literal segments than the generic `/{kind}/{eid}/images/...` routes and different segment counts where it matters; `copy-from-greeting` has 8 segments vs the 7 of the versioned image GET/PUT, so nothing shadows.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q`
Expected: all PASS (including `test_greeting_images_served_readonly` — bytes still read-only)

- [ ] **Step 5: Full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(routes): greeting image subjects, appearances, copy-from-greeting"
```

---

### Task 4: frontend api client + GreetingMarkdown imageExtras

**Files:**
- Modify: `frontend/src/api/client.ts` (types near `Greeting` ~line 115; methods in the greetings section ~line 392)
- Modify: `frontend/src/components/GreetingMarkdown.tsx`
- Test: `frontend/src/components/GreetingMarkdown.test.tsx` (create)

**Interfaces:**
- Produces (Tasks 5–6 rely on):
  - `type Appearance = { gid: string; greeting_name: string; name: string; url: string }`
  - `api.getGreetingSubjects(wid, gid) -> Promise<Record<string, string[]>>`
  - `api.setImageSubjects(wid, gid, name, subjects: string[]) -> Promise<{ ok: boolean }>`
  - `api.listAppearances(wid, cid) -> Promise<Appearance[]>`
  - `api.copyGreetingImage(wid, cid, vid, body: { gid: string; name: string; slot: "avatar" | "gallery" }) -> Promise<{ name: string; ext: string }>`
  - `GreetingMarkdown` prop `imageExtras?: (src: string) => ReactNode` — rendered inside a `<span className="img-extras">` right after each image.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/GreetingMarkdown.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { GreetingMarkdown } from "./GreetingMarkdown";

test("imageExtras renders per image with its src; absent by default", () => {
  const body = "a ![x](/api/img/one) b ![y](/api/img/two)";
  const { container, rerender } = render(<GreetingMarkdown>{body}</GreetingMarkdown>);
  expect(container.querySelectorAll("img").length).toBe(2);
  expect(container.querySelector(".img-extras")).toBeNull();

  rerender(
    <GreetingMarkdown imageExtras={(src) => <button>tag {src.split("/").pop()}</button>}>
      {body}
    </GreetingMarkdown>,
  );
  expect(screen.getByRole("button", { name: "tag one" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "tag two" })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run src/components/GreetingMarkdown.test.tsx`
Expected: FAIL — `imageExtras` prop not accepted / no `.img-extras`

- [ ] **Step 3: Implement**

`GreetingMarkdown.tsx` — replace the component with:

```tsx
export function GreetingMarkdown({ children, imageExtras }:
    { children: string; imageExtras?: (src: string) => ReactNode }) {
  const text = children.replace(/^#(.+?)#\s*$/gm, (_m, label) => `### ${label.trim()}`);
  const withImages = imageExtras
    ? {
        ...components,
        img: ({ src, alt }: { src?: string; alt?: string }) => (
          <span className="img-block">
            <img src={src} alt={alt ?? ""} />
            <span className="img-extras">{imageExtras(src ?? "")}</span>
          </span>
        ),
      }
    : components;
  return (
    <div className="detail-rendered">
      <Markdown remarkPlugins={[remarkGfm, remarkBreaks]} components={withImages}>
        {text}
      </Markdown>
    </div>
  );
}
```

`client.ts` — add after the `Greeting` types:

```ts
export type Appearance = { gid: string; greeting_name: string; name: string; url: string };
```

and to the greetings section of `api`:

```ts
  getGreetingSubjects: (wid: string, gid: string) =>
    request<Record<string, string[]>>("GET", `/api/worlds/${wid}/greetings/${gid}/subjects`),
  setImageSubjects: (wid: string, gid: string, name: string, subjects: string[]) =>
    request<{ ok: boolean }>("PUT", `/api/worlds/${wid}/greetings/${gid}/images/${name}/subjects`, { subjects }),
  listAppearances: (wid: string, cid: string) =>
    request<Appearance[]>("GET", `/api/worlds/${wid}/characters/${cid}/appearances`),
  copyGreetingImage: (wid: string, cid: string, vid: string,
                      body: { gid: string; name: string; slot: "avatar" | "gallery" }) =>
    request<{ name: string; ext: string }>(
      "POST", `/api/worlds/${wid}/characters/${cid}/versions/${vid}/images/copy-from-greeting`, body),
```

- [ ] **Step 4: Run tests + typecheck**

Run (from `frontend/`): `npx vitest run src/components/GreetingMarkdown.test.tsx` then `npx tsc -b`
Expected: PASS / clean

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/components/GreetingMarkdown.tsx frontend/src/components/GreetingMarkdown.test.tsx
git commit -m "feat(frontend): api for image subjects + GreetingMarkdown imageExtras hook"
```

---

### Task 5: GreetingEditor — subject chips + SubjectsPopover

**Files:**
- Create: `frontend/src/components/SubjectsPopover.tsx`
- Modify: `frontend/src/components/GreetingEditor.tsx`
- Test: `frontend/src/components/GreetingEditor.test.tsx` (append; extend the `vi.mock` api list)

**Interfaces:**
- Consumes: Task 4's api methods + `imageExtras`; existing `onOpenCharacter`.
- Produces: view-mode greeting images show subject chips + an "＋ subjects" button opening the popover. `SubjectsPopover` props:
  `{ chars: CharacterSummary[]; present: string[]; value: string[]; onSave(subjects: string[]): void; onClose(): void }`.

- [ ] **Step 1: Write the failing tests**

In `GreetingEditor.test.tsx`: add `getGreetingSubjects: vi.fn(), setImageSubjects: vi.fn(),` to the `vi.mock` api object, and in `beforeEach`: `(api.getGreetingSubjects as any).mockResolvedValue({});` `(api.setImageSubjects as any).mockResolvedValue({ ok: true });`. Then append:

```tsx
const IMG_BODY = "scene ![M](/api/worlds/w/greetings/open/images/embed-aaa111bbb222)";

function mockOpenWithImage(subjects: Record<string, string[]> = {}) {
  (api.listGreetings as any).mockResolvedValue([
    { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
  ]);
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
    body: IMG_BODY, edges: { leads_to: [], excludes: [] }, predecessors: [],
  });
  (api.getGreetingSubjects as any).mockResolvedValue(subjects);
}

test("greeting image shows subject chips and opens the picker", async () => {
  mockOpenWithImage({ "embed-aaa111bbb222": ["seraphine"] });
  const { container } = render(<GreetingEditor wid="w" />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Open"));
  await waitFor(() => expect(api.getGreetingSubjects).toHaveBeenCalledWith("w", "open"));
  // chip for the assigned subject renders under the image
  const extras = await waitFor(() => container.querySelector(".img-extras") as HTMLElement);
  expect(within(extras).getByText("Seraphine")).toBeInTheDocument();
  // open the picker
  fireEvent.click(within(extras).getByRole("button", { name: /subjects/i }));
  expect(screen.getByText(/present in this greeting/i)).toBeInTheDocument();
});

test("saving the picker PUTs subjects and refreshes", async () => {
  mockOpenWithImage({});
  const { container } = render(<GreetingEditor wid="w" />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Open"));
  const extras = await waitFor(() => container.querySelector(".img-extras") as HTMLElement);
  fireEvent.click(within(extras).getByRole("button", { name: /subjects/i }));
  fireEvent.click(screen.getByRole("button", { name: "Seraphine" })); // toggle on
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() => expect(api.setImageSubjects).toHaveBeenCalledWith(
    "w", "open", "embed-aaa111bbb222", ["seraphine"]));
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/GreetingEditor.test.tsx`
Expected: new tests FAIL (no `.img-extras`)

- [ ] **Step 3: Implement**

Create `frontend/src/components/SubjectsPopover.tsx`:

```tsx
import { useState } from "react";
import type { CharacterSummary } from "../api/client";

// Picker for a greeting image's subjects: the greeting's present cast as
// one-click chips on top, a filterable list of all world characters below.
export function SubjectsPopover({ chars, present, value, onSave, onClose }: {
  chars: CharacterSummary[]; present: string[]; value: string[];
  onSave: (subjects: string[]) => void; onClose: () => void;
}) {
  const [sel, setSel] = useState<string[]>(value);
  const [q, setQ] = useState("");
  const toggle = (cid: string) =>
    setSel((s) => (s.includes(cid) ? s.filter((x) => x !== cid) : [...s, cid]));
  const presentChars = chars.filter((c) => present.includes(c.id));
  const others = chars.filter(
    (c) => !present.includes(c.id) && c.name.toLowerCase().includes(q.toLowerCase()));
  const chip = (c: CharacterSummary) => (
    <button key={c.id} className={"chip" + (sel.includes(c.id) ? " on" : "")}
            onClick={() => toggle(c.id)}>{c.name}</button>
  );
  return (
    <div className="subjects-popover" role="dialog" aria-label="Image subjects">
      {presentChars.length > 0 && (
        <>
          <div className="field-hint">Present in this greeting</div>
          <div className="chips">{presentChars.map(chip)}</div>
        </>
      )}
      <input type="text" placeholder="Search all characters…" value={q}
             aria-label="Search characters" onChange={(e) => setQ(e.target.value)} />
      <div className="chips">{others.map(chip)}</div>
      <div className="form-actions">
        <button className="subtle" onClick={onClose}>Cancel</button>
        <button className="primary" onClick={() => onSave(sel)}>Save</button>
      </div>
    </div>
  );
}
```

In `GreetingEditor.tsx`:

```tsx
// new imports
import { SubjectsPopover } from "./SubjectsPopover";

// new state next to the others
const [subjects, setSubjects] = useState<Record<string, string[]>>({});
const [picking, setPicking] = useState<string | null>(null); // image name being edited

// in select(id): after setMode("view")
api.getGreetingSubjects(wid, id).then(setSubjects).catch(() => setSubjects({}));
setPicking(null);

// helper next to charName/greetName
const imageName = (src: string) => src.split("/").pop() ?? "";

async function saveSubjects(name: string, cids: string[]) {
  await api.setImageSubjects(wid, gid!, name, cids);
  setSubjects(await api.getGreetingSubjects(wid, gid!));
  setPicking(null);
}

// render-prop passed only in view mode; replaces the bare <GreetingMarkdown>
<GreetingMarkdown imageExtras={(src) => {
  const name = imageName(src);
  return (
    <>
      {(subjects[name] ?? []).map((cid) => (
        <button key={cid} className="chip on"
                onClick={() => onOpenCharacter?.(cid, presentVid(cid))}>{charName(cid)}</button>
      ))}
      <button className="chip" onClick={() => setPicking(name)}>＋ subjects</button>
      {picking === name && (
        <SubjectsPopover chars={chars} present={form.present} value={subjects[name] ?? []}
                         onSave={(cids) => saveSubjects(name, cids)}
                         onClose={() => setPicking(null)} />
      )}
    </>
  );
}}>{form.body}</GreetingMarkdown>
```

Add minimal styles to `frontend/src/index.css` (match existing tokens):

```css
.img-block { display: inline-block; }
.img-extras { display: flex; flex-wrap: wrap; gap: 4px; margin: 4px 0 8px; }
.subjects-popover { border: 1px solid var(--border, #444); border-radius: 8px; padding: 10px; margin: 6px 0; display: grid; gap: 8px; max-width: 420px; background: var(--panel, #1b1b1f); }
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/components/GreetingEditor.test.tsx` then `npx tsc -b`
Expected: PASS / clean (existing view/edit tests untouched)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/SubjectsPopover.tsx frontend/src/components/GreetingEditor.tsx frontend/src/components/GreetingEditor.test.tsx frontend/src/index.css
git commit -m "feat(frontend): assign image subjects from the greeting view"
```

---

### Task 6: CharacterEditor — "Appears in" gallery + "World greetings" links

**Files:**
- Modify: `frontend/src/components/CharacterEditor.tsx` (detail view, after the Images shelf ~line 867 and after Alternate greetings ~line 904; props at the component signature)
- Test: `frontend/src/components/CharacterEditor.test.tsx` (append; extend `vi.mock` with `listAppearances`, `copyGreetingImage`, `listGreetings`)

**Interfaces:**
- Consumes: `api.listAppearances`, `api.copyGreetingImage`, `api.listGreetings`, `Appearance`/`Greeting` types.
- Produces: new optional prop `onOpenGreeting?: (gid: string) => void` (Task 7 wires it).

- [ ] **Step 1: Write the failing tests**

In `CharacterEditor.test.tsx`, extend the api mock with `listAppearances: vi.fn(), copyGreetingImage: vi.fn(), listGreetings: vi.fn(),` and default them in `beforeEach` to `[]` / `{ name: "avatar", ext: "png" }` / `[]`. Append (adjust the detail-opening helper to whatever the file's existing tests use to reach the character detail view — reuse their fixtures):

```tsx
test("appears-in gallery copies to avatar and world greetings link with primary star", async () => {
  (api.listAppearances as any).mockResolvedValue([
    { gid: "sol-1", greeting_name: "SoL 1", name: "embed-a", url: "/api/worlds/w/greetings/sol-1/images/embed-a" },
  ]);
  (api.listGreetings as any).mockResolvedValue([
    { id: "sol-1", name: "SoL 1", character: "mira", version: "main", present: ["mira"], requires_tags: [], predecessor_join: "all" },
    { id: "sol-2", name: "SoL 2", character: "other", version: "main", present: ["mira", "other"], requires_tags: [], predecessor_join: "all" },
    { id: "sol-3", name: "SoL 3", character: "other", version: "main", present: ["other"], requires_tags: [], predecessor_join: "all" },
  ]);
  const onOpenGreeting = vi.fn();
  // ...open character "mira" in view mode using this suite's existing fixture flow,
  // passing onOpenGreeting={onOpenGreeting} to <CharacterEditor …>
  // appears-in:
  const strip = await screen.findByText("Appears in");
  const tile = strip.parentElement as HTMLElement;
  fireEvent.click(within(tile).getByRole("button", { name: /set as avatar/i }));
  await waitFor(() => expect(api.copyGreetingImage).toHaveBeenCalledWith(
    "w", "mira", "main", { gid: "sol-1", name: "embed-a", slot: "avatar" }));
  // world greetings: present-only listed, primary starred, absent one missing
  const wg = screen.getByText("World greetings").parentElement as HTMLElement;
  expect(within(wg).getByText(/★\s*SoL 1/)).toBeInTheDocument();
  expect(within(wg).getByText("SoL 2")).toBeInTheDocument();
  expect(within(wg).queryByText("SoL 3")).toBeNull();
  fireEvent.click(within(wg).getByText("SoL 2"));
  expect(onOpenGreeting).toHaveBeenCalledWith("sol-2");
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/CharacterEditor.test.tsx`
Expected: new test FAILS ("Appears in" not found)

- [ ] **Step 3: Implement**

In `CharacterEditor.tsx`:

```tsx
// props: add onOpenGreeting
export function CharacterEditor({ wid, resetSignal, focus, onOpenLore, onOpenGreeting }: {
  /* existing props unchanged */; onOpenGreeting?: (gid: string) => void }) {

// state near the other detail state
const [appearances, setAppearances] = useState<Appearance[]>([]);
const [worldGreetings, setWorldGreetings] = useState<Greeting[]>([]);

// wherever the detail/card loads (the effect or loader that sets `detail`):
api.listAppearances(wid, detail.meta.id).then(setAppearances).catch(() => setAppearances([]));
api.listGreetings(wid).then(setWorldGreetings).catch(() => setWorldGreetings([]));

// actions
async function copyFromGreeting(a: Appearance, slot: "avatar" | "gallery") {
  await api.copyGreetingImage(wid, detail.meta.id, vid, { gid: a.gid, name: a.name, slot });
  refreshImages(); // the same helper the Images shelf uses after upload/promote
}

// detail view, right after the Images shelf block:
{appearances.length > 0 && (
  <div className="detail-field">
    <div className="section-label">Appears in</div>
    <div className="images-shelf">
      {appearances.map((a) => (
        <div className="shelf-tile" key={`${a.gid}/${a.name}`}>
          <a href={a.url} target="_blank" rel="noreferrer"><img alt={`${a.greeting_name} art`} src={a.url} /></a>
          <button className="shelf-promote" onClick={() => copyFromGreeting(a, "avatar")}>Set as avatar</button>
          <button className="shelf-promote" onClick={() => copyFromGreeting(a, "gallery")}>Add to gallery</button>
          {onOpenGreeting && (
            <button className="shelf-promote" onClick={() => onOpenGreeting(a.gid)}>{a.greeting_name}</button>
          )}
        </div>
      ))}
    </div>
  </div>
)}

// detail view, right after the Alternate greetings block:
{(() => {
  const mine = worldGreetings.filter((g) => (g.present ?? []).includes(detail.meta.id));
  if (mine.length === 0) return null;
  return (
    <div className="detail-field">
      <div className="section-label">World greetings</div>
      <div className="chips">
        {mine.map((g) => (
          <button key={g.id} className="chip on" onClick={() => onOpenGreeting?.(g.id)}>
            {g.character === detail.meta.id ? `★ ${g.name}` : g.name}
          </button>
        ))}
      </div>
    </div>
  );
})()}
```

(`refreshImages` = whatever existing function the shelf uses to reload image names / bump `avatarBust` after `promote`/upload — reuse it verbatim rather than inventing a new one.)

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/components/CharacterEditor.test.tsx` then `npx tsc -b`
Expected: PASS / clean

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CharacterEditor.tsx frontend/src/components/CharacterEditor.test.tsx
git commit -m "feat(frontend): appears-in gallery + world-greeting links on character page"
```

---

### Task 7: WorldView wiring — openGreeting + GreetingEditor focus

**Files:**
- Modify: `frontend/src/routes/WorldView.tsx`
- Modify: `frontend/src/components/GreetingEditor.tsx` (add `focus` prop)
- Test: `frontend/src/routes/WorldView.test.tsx` (append), `frontend/src/components/GreetingEditor.test.tsx` (append)

**Interfaces:**
- Consumes: Task 6's `onOpenGreeting`; Task 5's GreetingEditor.
- Produces: `GreetingEditor` prop `focus?: string | null` — selects that greeting (view mode) on mount/change.

- [ ] **Step 1: Write the failing tests**

`GreetingEditor.test.tsx`:

```tsx
test("focus prop opens that greeting in view mode", async () => {
  mockOpenWithImage({});
  render(<GreetingEditor wid="w" focus="open" />);
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalledWith("w", "open"));
  expect(await screen.findByRole("button", { name: /^edit$/i })).toBeInTheDocument();
});
```

`WorldView.test.tsx` (follow the file's existing mocking of api + child components if any; if it renders real children, mock api as in the other suites): assert that clicking a world-greeting chip inside CharacterEditor's view switches to the Greetings tab with the greeting selected — minimal version:

```tsx
test("openGreeting switches to the greetings tab and focuses the greeting", async () => {
  // render WorldView at /worlds/w with mocked api;
  // navigate: characters tab -> character detail -> click a "World greetings" chip
  // then: the Greetings tab button has class "active" and api.readGreeting was
  // called with ("w", "<gid>")
});
```

(Write it concretely against the suite's existing fixtures — the assertion targets are exactly: greetings tab button gains `active`, and `api.readGreeting` called with the chip's gid.)

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/GreetingEditor.test.tsx src/routes/WorldView.test.tsx`
Expected: FAIL — `focus` prop unknown / tab doesn't switch

- [ ] **Step 3: Implement**

`GreetingEditor.tsx`:

```tsx
export function GreetingEditor({ wid, onOpenCharacter, focus }: {
  wid: string; onOpenCharacter?: (cid: string, vid: string) => void; focus?: string | null }) {
  // after the initial-load effect:
  useEffect(() => {
    if (focus) select(focus);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus, wid]);
```

`WorldView.tsx`:

```tsx
const [focusGreeting, setFocusGreeting] = useState<string | null>(null);

function openGreeting(gid: string) {
  setFocusGreeting(gid);
  setTab("greetings");
}
// tab button onClick: when t.key === "greetings", also setFocusGreeting(null)
// (mirror the characters tab clearing focusChar)

{tab === "characters" && <CharacterEditor wid={wid} resetSignal={charReset} focus={focusChar}
                                          onOpenLore={openLore} onOpenGreeting={openGreeting} />}
{tab === "greetings" && <GreetingEditor wid={wid} onOpenCharacter={openCharacter} focus={focusGreeting} />}
```

- [ ] **Step 4: Run the full frontend + backend suites**

Run (from `frontend/`): `npx vitest run` then `npx tsc -b`
Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/WorldView.tsx frontend/src/components/GreetingEditor.tsx frontend/src/components/GreetingEditor.test.tsx frontend/src/routes/WorldView.test.tsx
git commit -m "feat(frontend): cross-tab jump from character page to a focused greeting"
```

---

## Final verification (after Task 7)

- Full suites green (both stacks).
- Manual: open ashgrove → a greeting → click "＋ subjects" under an image → assign → chip appears; character page shows "Appears in" + "World greetings" (★ on primary); "Set as avatar" updates the avatar; chip click jumps to the greeting.
