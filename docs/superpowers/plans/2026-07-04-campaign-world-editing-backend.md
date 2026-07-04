# Campaign World Editing — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make campaigns truly self-contained copies of their world (greetings, plot map, all character/PC versions), with explicit version picking (purge + import-to-replace), greeting marks (completed/skipped), and sync coverage for the newly copied kinds.

**Architecture:** Extend the existing copy-on-create + hash-manifest sync pattern. `create_campaign` copies everything; a `world_copy: full` flag drives lazy backfill for old campaigns; `playing.py` and other play-time readers repoint from the world root to the campaign root; `appearances.py` gains an explicit `pick_version` (which the lazy `appear()` lock routes through) and `import_version`; `played.json` grows into a three-set marks structure.

**Tech Stack:** Python 3.12, FastAPI, pytest. Store modules under `backend/src/grimoire/store/`, routes in `backend/src/grimoire/routes.py`.

**Spec:** `docs/superpowers/specs/2026-07-04-campaign-world-editing-design.md`

## Global Constraints

- Run backend tests with: `backend/.venv/Scripts/python.exe -m pytest backend -q` (from repo root). Single test: append `backend/tests/test_x.py::test_name -v`.
- All store tests isolate the filesystem via `monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))`.
- Manifest refs are flat strings `"<kind>/<id>"` (plus the special ref `"plotmap"`); values are content hashes. `sync.md` stores them as frontmatter.
- Never copy locations/lore in the backfill — those arrived at campaign creation; new world entities must flow through sync, not silent backfill.
- **Ordering matters in `routes.py`:** literal campaign sub-paths must be declared before the generic `/campaigns/{cid}/{kind}` routes (see the existing "declared before the generic routes" comments). All new campaign routes in this plan go in the existing "campaign greetings / play" or "campaign cast & suggestions" sections, which already sit before the generic block.
- Commit after every task with the message given in its final step.

---

### Task 1: `SYNCED_KINDS` + `entities.synced_refs`

**Files:**
- Modify: `backend/src/grimoire/store/entities.py` (near `ENTITY_KINDS`, line 16, and `all_refs`, line 115)
- Test: `backend/tests/test_entities_store.py`

**Interfaces:**
- Produces: `entities.SYNCED_KINDS: tuple[str, ...]` = `("locations", "lore", "greetings")`; `entities.synced_refs(root: Path) -> list[tuple[str, str]]` enumerating `<root>/<kind>/*.md` for every synced kind. `entities.entity_hash(root, kind, eid)` already works for `"greetings"` (it has no kind check) — later tasks rely on that.
- Note: greetings do **not** join `ENTITY_KINDS` — generic entity CRUD stays locations/lore.

- [ ] **Step 1: Write the failing test** (append to `backend/tests/test_entities_store.py`)

```python
def test_synced_refs_includes_greetings(tmp_path):
    (tmp_path / "locations").mkdir()
    (tmp_path / "locations" / "inn.md").write_text("---\nname: Inn\n---\n", encoding="utf-8")
    (tmp_path / "greetings").mkdir()
    (tmp_path / "greetings" / "gala.md").write_text("---\nname: Gala\n---\n", encoding="utf-8")
    assert entities.synced_refs(tmp_path) == [("locations", "inn"), ("greetings", "gala")]
    # greetings are synced but not generic-CRUD entities
    assert "greetings" in entities.SYNCED_KINDS
    assert "greetings" not in entities.ENTITY_KINDS
```

(Match the existing import style at the top of the file — it already imports `entities`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_entities_store.py::test_synced_refs_includes_greetings -v`
Expected: FAIL with `AttributeError: ... has no attribute 'synced_refs'`

- [ ] **Step 3: Implement** (in `entities.py`, right below `all_refs`)

```python
SYNCED_KINDS: tuple[str, ...] = ENTITY_KINDS + ("greetings",)


def synced_refs(root: Path) -> list[tuple[str, str]]:
    """Everything copy-on-create / sync tracks as a flat `<kind>/<id>.md` file:
    generic entities plus greetings (which keep their own CRUD module)."""
    refs: list[tuple[str, str]] = []
    for kind in SYNCED_KINDS:
        d = _kind_dir(root, kind)
        if d.exists():
            refs.extend((kind, p.stem) for p in sorted(d.glob("*.md")))
    return refs
```

Put `SYNCED_KINDS` next to `ENTITY_KINDS` (line 16) rather than mid-file.

- [ ] **Step 4: Run test to verify it passes**

Run: same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/entities.py backend/tests/test_entities_store.py
git commit -m "feat(store): SYNCED_KINDS + synced_refs (greetings join copy/sync enumeration)"
```

---

### Task 2: content hashes — `greetings.plotmap_hash`, `characters.dir_hash`, `pcs.dir_hash`

**Files:**
- Modify: `backend/src/grimoire/store/greetings.py` (needs `import hashlib`), `backend/src/grimoire/store/characters.py`, `backend/src/grimoire/store/pcs.py`
- Test: `backend/tests/test_greetings_store.py`, `backend/tests/test_characters_store.py`, `backend/tests/test_pcs_store.py`

**Interfaces:**
- Produces: `greetings.plotmap_hash(root) -> str | None` (None when `plotmap.json` absent); `characters.dir_hash(root, cid) -> str | None` and `pcs.dir_hash(root, pid) -> str | None` — whole-actor hash over the meta file + every version file (name-tagged), **excluding assets** (an image-only change never surfaces in sync).

- [ ] **Step 1: Write the failing tests**

Append to `test_greetings_store.py`:

```python
def test_plotmap_hash_roundtrip(tmp_path):
    assert greetings.plotmap_hash(tmp_path) is None
    g = greetings.create_greeting(tmp_path, "A", "c", "v")
    greetings.set_edges(tmp_path, g, leads_to=["b"])
    h1 = greetings.plotmap_hash(tmp_path)
    assert h1
    greetings.set_edges(tmp_path, g, leads_to=["b", "c"])
    assert greetings.plotmap_hash(tmp_path) != h1
```

Append to `test_characters_store.py`:

```python
def test_dir_hash_tracks_meta_and_versions_not_assets(tmp_path):
    assert characters.dir_hash(tmp_path, "nope") is None
    cid, vid = characters.create_character(tmp_path, "Mara")
    h1 = characters.dir_hash(tmp_path, cid)
    assert h1
    characters.create_version(tmp_path, cid, "grim", characters.blank_card("Mara"))
    h2 = characters.dir_hash(tmp_path, cid)
    assert h2 != h1
    # an assets-only change does not move the hash
    (tmp_path / "characters" / cid / "assets").mkdir()
    (tmp_path / "characters" / cid / "assets" / "x.png").write_bytes(b"png")
    assert characters.dir_hash(tmp_path, cid) == h2
```

Append to `test_pcs_store.py`:

```python
def test_dir_hash_tracks_meta_and_versions(tmp_path):
    assert pcs.dir_hash(tmp_path, "nope") is None
    pid, vid = pcs.create_pc(tmp_path, "Elara", [])
    h1 = pcs.dir_hash(tmp_path, pid)
    assert h1
    pcs.set_tags(tmp_path, pid, ["vip"])
    assert pcs.dir_hash(tmp_path, pid) != h1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_greetings_store.py backend/tests/test_characters_store.py backend/tests/test_pcs_store.py -q -k "hash"`
Expected: FAIL with AttributeError for each new function.

- [ ] **Step 3: Implement**

`greetings.py` (add `import hashlib` to the imports; place function next to `read_plotmap`):

```python
def plotmap_hash(root: Path) -> str | None:
    p = _plotmap_path(root)
    if not p.exists():
        return None
    return hashlib.sha256(p.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
```

`characters.py` (next to `card_hash`, line 249):

```python
def dir_hash(root: Path, cid: str) -> str | None:
    """Whole-actor content hash: character.md plus every version card, name-tagged.
    Assets are excluded so an image-only change never surfaces in sync."""
    if not _safe(cid) or not _meta_path(root, cid).exists():
        return None
    h = hashlib.sha256()
    for p in [_meta_path(root, cid)] + [_card_path(root, cid, v) for v in _version_ids(root, cid)]:
        h.update(p.name.encode("utf-8"))
        h.update(p.read_text(encoding="utf-8").encode("utf-8"))
    return h.hexdigest()
```

`pcs.py` (next to `version_hash`, line 177):

```python
def dir_hash(root: Path, pid: str) -> str | None:
    """Whole-actor content hash: pc.md plus every version persona, name-tagged."""
    if not _safe(pid) or not _meta_path(root, pid).exists():
        return None
    h = hashlib.sha256()
    for p in [_meta_path(root, pid)] + [_version_path(root, pid, v) for v in _version_ids(root, pid)]:
        h.update(p.name.encode("utf-8"))
        h.update(p.read_text(encoding="utf-8").encode("utf-8"))
    return h.hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass** (same command)

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/greetings.py backend/src/grimoire/store/characters.py backend/src/grimoire/store/pcs.py backend/tests/test_greetings_store.py backend/tests/test_characters_store.py backend/tests/test_pcs_store.py
git commit -m "feat(store): plotmap + whole-actor content hashes for campaign sync"
```

---

### Task 3: full copy in `create_campaign`

**Files:**
- Modify: `backend/src/grimoire/store/campaigns.py` (imports, line 8; `create_campaign`, lines 66-97)
- Test: `backend/tests/test_campaigns_store.py`

**Interfaces:**
- Consumes: `entities.synced_refs`, `greetings.plotmap_hash`, `characters.dir_hash`/`character_refs`, `pcs.dir_hash`/`pc_refs`.
- Produces: `create_campaign` copies greetings (+assets), `plotmap.json`, and full `characters/`/`pcs/` trees; manifest gains `greetings/<gid>`, `plotmap`, `characters/<aid>`, `pcs/<aid>` refs; campaign.md frontmatter gains `world_copy: full`.

- [ ] **Step 1: Write the failing test** (append to `test_campaigns_store.py`; follow its existing GRIMOIRE_HOME/monkeypatch style)

```python
def test_create_campaign_full_copy(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    char_id, _ = characters.create_character(wroot, "Mara")
    characters.create_version(wroot, char_id, "grim", characters.blank_card("Mara"))
    pid, _ = pcs.create_pc(wroot, "Elara", [])
    g = greetings.create_greeting(wroot, "Gala", char_id, "default", body="Hi.")
    greetings.set_edges(wroot, g, leads_to=[])
    cid = campaigns.create_campaign("Run", wid)
    croot = campaigns.campaign_root(cid)
    assert (croot / "greetings" / f"{g}.md").exists()
    assert (croot / "plotmap.json").exists()
    assert (croot / "characters" / char_id / "default.json").exists()
    assert (croot / "characters" / char_id / "grim.json").exists()
    assert (croot / "pcs" / pid / "default.md").exists()
    manifest = campaigns.read_manifest(cid)
    assert manifest[f"greetings/{g}"] == entities.entity_hash(wroot, "greetings", g)
    assert manifest["plotmap"] == greetings.plotmap_hash(wroot)
    assert manifest[f"characters/{char_id}"] == characters.dir_hash(wroot, char_id)
    assert manifest[f"pcs/{pid}"] == pcs.dir_hash(wroot, pid)
    assert campaigns.read_campaign(cid)["meta"]["world_copy"] == "full"
```

Add any missing imports at the top of the test file (`characters`, `pcs`, `greetings`, `entities`).

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_campaigns_store.py::test_create_campaign_full_copy -v`
Expected: FAIL (missing files / manifest keys).

- [ ] **Step 3: Implement**

In `campaigns.py`, change the import line 8 to:

```python
from . import calendars, characters, entities, greetings, pcs, worlds
```

In `create_campaign`, change the meta write (line 75-78) to include the flag:

```python
    campaign_meta_path(cid).write_text(
        dump_frontmatter({"name": name, "world": world_id, "created": now, "updated": now,
                          "world_copy": "full"}, ""),
        encoding="utf-8",
    )
```

Replace the copy loop (lines 79-91) with:

```python
    # copy-on-create: deep-copy world entities, greetings, plot map, and every
    # actor version + record base hashes so sync can diff against this snapshot
    wroot = worlds.world_root(world_id)
    manifest: dict[str, str] = {}
    for kind, eid in entities.synced_refs(wroot):
        src = wroot / kind / f"{eid}.md"
        dst_dir = root / kind
        dst_dir.mkdir(parents=True, exist_ok=True)
        (dst_dir / f"{eid}.md").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        assets_dir = wroot / kind / eid / "assets"
        if assets_dir.exists():  # entity images (primary/gallery) travel with the copy
            shutil.copytree(assets_dir, root / kind / eid / "assets", dirs_exist_ok=True)
        manifest[f"{kind}/{eid}"] = entities.entity_hash(wroot, kind, eid) or ""
    if (wroot / "plotmap.json").exists():
        (root / "plotmap.json").write_text((wroot / "plotmap.json").read_text(encoding="utf-8"),
                                           encoding="utf-8")
    manifest["plotmap"] = greetings.plotmap_hash(wroot) or ""
    for kind, refs_of, dir_hash in (("characters", characters.character_refs, characters.dir_hash),
                                    ("pcs", pcs.pc_refs, pcs.dir_hash)):
        for aid in refs_of(wroot):
            shutil.copytree(wroot / kind / aid, root / kind / aid)
            manifest[f"{kind}/{aid}"] = dir_hash(wroot, aid) or ""
    write_manifest(cid, manifest)
```

- [ ] **Step 4: Run the full campaigns + sync test files** (existing tests must stay green — the manifest gained refs but `sync.incoming` still filters to `ENTITY_KINDS`, so nothing breaks yet)

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_campaigns_store.py backend/tests/test_sync_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/campaigns.py backend/tests/test_campaigns_store.py
git commit -m "feat(store): campaigns copy greetings, plotmap and all actor versions at creation"
```

---

### Task 4: `ensure_campaign_copy` backfill + route hook

**Files:**
- Modify: `backend/src/grimoire/store/campaigns.py` (add `import json` to imports), `backend/src/grimoire/routes.py` (`get_campaign` line 1148-1153, `_campaign_root_or_404` line 959-962)
- Test: `backend/tests/test_campaigns_store.py`, `backend/tests/test_routes.py`

**Interfaces:**
- Produces: `campaigns.ensure_campaign_copy(cid) -> None` — idempotent; raises `CampaignNotFound`. Every campaign route that goes through `_campaign_root_or_404` (and `GET /campaigns/{cid}`) migrates legacy campaigns on first touch.
- Locked actors (refs present in `appearances.json`) are **not** backfilled — a lock means the pick already happened.

- [ ] **Step 1: Write the failing test** (append to `test_campaigns_store.py`)

```python
def _strip_to_legacy(cid, keep_actor_version=None):
    """Rewind a freshly created campaign to the pre-full-copy on-disk layout."""
    croot = campaigns.campaign_root(cid)
    for sub in ("greetings", "characters", "pcs"):
        if (croot / sub).exists():
            shutil.rmtree(croot / sub)
    (croot / "plotmap.json").unlink(missing_ok=True)
    manifest = {r: h for r, h in campaigns.read_manifest(cid).items()
                if r.split("/")[0] in ("locations", "lore")}
    campaigns.write_manifest(cid, manifest)
    mp = campaigns.campaign_meta_path(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    del meta["world_copy"]
    mp.write_text(dump_frontmatter(meta, body), encoding="utf-8")


def test_ensure_campaign_copy_backfills_legacy(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    char_id, _ = characters.create_character(wroot, "Mara")
    g = greetings.create_greeting(wroot, "Gala", char_id, "default", body="Hi.")
    greetings.set_edges(wroot, g, leads_to=[])
    cid = campaigns.create_campaign("Run", wid)
    _strip_to_legacy(cid)
    campaigns.ensure_campaign_copy(cid)
    croot = campaigns.campaign_root(cid)
    assert (croot / "greetings" / f"{g}.md").exists()
    assert (croot / "plotmap.json").exists()
    assert (croot / "characters" / char_id / "default.json").exists()
    assert campaigns.read_campaign(cid)["meta"]["world_copy"] == "full"
    manifest = campaigns.read_manifest(cid)
    assert manifest["plotmap"] == greetings.plotmap_hash(wroot)
    campaigns.ensure_campaign_copy(cid)  # idempotent


def test_ensure_campaign_copy_skips_locked_actors(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    char_id, _ = characters.create_character(wroot, "Mara")
    characters.create_version(wroot, char_id, "grim", characters.blank_card("Mara"))
    cid = campaigns.create_campaign("Run", wid)
    _strip_to_legacy(cid)
    # legacy lock: the old appear() copied exactly one version
    croot = campaigns.campaign_root(cid)
    (croot / "characters" / char_id).mkdir(parents=True)
    src = wroot / "characters" / char_id
    for fn in ("character.md", "default.json"):
        (croot / "characters" / char_id / fn).write_text(
            (src / fn).read_text(encoding="utf-8"), encoding="utf-8")
    (croot / "appearances.json").write_text(
        json.dumps({f"characters/{char_id}": {"version": "default", "base": "h",
                                              "scenes": ["s1"], "role": "npc"}}),
        encoding="utf-8")
    campaigns.ensure_campaign_copy(cid)
    assert not (croot / "characters" / char_id / "grim.json").exists()  # no version resurrection
    assert f"characters/{char_id}" not in campaigns.read_manifest(cid)
```

Add missing test-file imports (`shutil`, `json`, `parse_frontmatter`, `dump_frontmatter` from `grimoire.store.frontmatter`).

- [ ] **Step 2: Run tests to verify they fail** (AttributeError: no `ensure_campaign_copy`)

- [ ] **Step 3: Implement** (in `campaigns.py`, below `read_campaign`; add `import json` at top)

```python
def ensure_campaign_copy(cid: str) -> None:
    """Backfill a pre-full-copy campaign (greetings, plot map, actors) from its
    world. Idempotent: `world_copy: full` in campaign.md marks it done. Locations
    and lore are never backfilled — they were copied at creation, and anything the
    world added since must arrive through sync, not a silent copy. Locked actors
    keep their purged state: a lock means the pick already happened."""
    mp = campaign_meta_path(cid)
    if not mp.exists():
        raise CampaignNotFound(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    if meta.get("world_copy") == "full":
        return
    root = campaign_root(cid)
    wroot = worlds.world_root(meta.get("world", ""))
    if wroot.exists():
        manifest = read_manifest(cid)
        for kind, eid in entities.synced_refs(wroot):
            if kind in entities.ENTITY_KINDS:
                continue
            dst = root / kind / f"{eid}.md"
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text((wroot / kind / f"{eid}.md").read_text(encoding="utf-8"),
                               encoding="utf-8")
                assets_dir = wroot / kind / eid / "assets"
                if assets_dir.exists():
                    shutil.copytree(assets_dir, root / kind / eid / "assets", dirs_exist_ok=True)
            manifest[f"{kind}/{eid}"] = entities.entity_hash(wroot, kind, eid) or ""
        if (wroot / "plotmap.json").exists() and not (root / "plotmap.json").exists():
            (root / "plotmap.json").write_text(
                (wroot / "plotmap.json").read_text(encoding="utf-8"), encoding="utf-8")
        manifest["plotmap"] = greetings.plotmap_hash(wroot) or ""
        ap = root / "appearances.json"
        locked = set(json.loads(ap.read_text(encoding="utf-8"))) if ap.exists() else set()
        for kind, refs_of, dir_hash in (("characters", characters.character_refs, characters.dir_hash),
                                        ("pcs", pcs.pc_refs, pcs.dir_hash)):
            for aid in refs_of(wroot):
                if f"{kind}/{aid}" in locked:
                    continue
                shutil.copytree(wroot / kind / aid, root / kind / aid, dirs_exist_ok=True)
                manifest[f"{kind}/{aid}"] = dir_hash(wroot, aid) or ""
        write_manifest(cid, manifest)
    meta["world_copy"] = "full"
    mp.write_text(dump_frontmatter(meta, body), encoding="utf-8")
```

(`appearances.json` is read directly here rather than via `store.appearances` — that module imports `campaigns`, so importing it back would be a cycle.)

In `routes.py`, rewrite `_campaign_root_or_404` (line 959):

```python
def _campaign_root_or_404(cid: str):
    try:
        store.campaigns.ensure_campaign_copy(cid)  # lazy backfill of legacy campaigns
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return store.campaigns.campaign_root(cid)
```

And in `get_campaign` (line 1148), call `store.campaigns.ensure_campaign_copy(cid)` as the first line of the `try:` block.

- [ ] **Step 4: Run tests**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_campaigns_store.py backend/tests/test_routes.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/campaigns.py backend/src/grimoire/routes.py backend/tests/test_campaigns_store.py
git commit -m "feat(store): lazy full-copy backfill for pre-existing campaigns"
```

---

### Task 5: greeting marks store (`played.json` → three sets)

**Files:**
- Modify: `backend/src/grimoire/store/playing.py` (lines 20-34)
- Test: `backend/tests/test_playing_store.py`

**Interfaces:**
- Produces: `playing.read_marks(cid) -> dict[str, set[str]]` with keys `played`/`completed`/`skipped` (legacy bare-list `played.json` migrates on read); `playing.mark_greeting(cid, gid, status)` with `status ∈ {"completed", "skipped", "none"}` — raises `greetings.GreetingNotFound` for an unknown campaign greeting, `PlayError` when the greeting was genuinely played or the status is unknown. `read_played(cid)` still returns the played set (kept for existing callers/tests).

- [ ] **Step 1: Write the failing tests** (append to `test_playing_store.py`; the `_campaign` helper at the top of that file already exists)

```python
def test_read_marks_migrates_legacy_list(monkeypatch, tmp_path):
    _wid, cid, _sid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "played.json").write_text('["g1"]', encoding="utf-8")
    marks = playing.read_marks(cid)
    assert marks == {"played": {"g1"}, "completed": set(), "skipped": set()}


def test_mark_greeting_roundtrip(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    g = greetings.create_greeting(croot, "Gala", "c", "v")
    playing.mark_greeting(cid, g, "completed")
    assert playing.read_marks(cid)["completed"] == {g}
    playing.mark_greeting(cid, g, "skipped")
    marks = playing.read_marks(cid)
    assert marks["skipped"] == {g} and marks["completed"] == set()
    playing.mark_greeting(cid, g, "none")
    assert playing.read_marks(cid)["skipped"] == set()
    with pytest.raises(playing.PlayError):
        playing.mark_greeting(cid, g, "bogus")
    with pytest.raises(greetings.GreetingNotFound):
        playing.mark_greeting(cid, "nope", "completed")


def test_mark_greeting_refuses_played(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    g = greetings.create_greeting(croot, "Gala", "c", "v")
    playing._mark_played(cid, g)
    with pytest.raises(playing.PlayError):
        playing.mark_greeting(cid, g, "completed")


def test_mark_played_clears_offscreen_marks(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    g = greetings.create_greeting(croot, "Gala", "c", "v")
    playing.mark_greeting(cid, g, "completed")
    playing._mark_played(cid, g)
    marks = playing.read_marks(cid)
    assert g in marks["played"] and g not in marks["completed"]
```

Note the tests create greetings **in the campaign root** — from this task on, campaign play state validates against the campaign copy.

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_playing_store.py -q -k "mark"`
Expected: FAIL (no `read_marks`).

- [ ] **Step 3: Implement** — replace `_played_path`/`read_played`/`_mark_played` (lines 20-34) with:

```python
_MARK_KEYS = ("played", "completed", "skipped")


def _marks_path(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "played.json"


def read_marks(cid: str) -> dict[str, set[str]]:
    p = _marks_path(cid)
    if not p.exists():
        return {k: set() for k in _MARK_KEYS}
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, list):  # legacy format: a bare list of played ids
        data = {"played": data}
    return {k: set(data.get(k, [])) for k in _MARK_KEYS}


def _write_marks(cid: str, marks: dict[str, set[str]]) -> None:
    payload = {k: sorted(marks[k]) for k in _MARK_KEYS}
    _marks_path(cid).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def read_played(cid: str) -> set[str]:
    return read_marks(cid)["played"]


def _mark_played(cid: str, gid: str) -> None:
    marks = read_marks(cid)
    marks["played"].add(gid)
    marks["completed"].discard(gid)  # actually playing supersedes an off-screen mark
    marks["skipped"].discard(gid)
    _write_marks(cid, marks)


def mark_greeting(cid: str, gid: str, status: str) -> None:
    """Set a greeting's off-screen mark: completed / skipped / none (clear)."""
    greetings.read_greeting(campaigns.campaign_root(cid), gid)  # raises GreetingNotFound
    if status not in ("completed", "skipped", "none"):
        raise PlayError(f"unknown mark status: {status}")
    marks = read_marks(cid)
    if gid in marks["played"]:
        raise PlayError("greeting was played in a scene; its mark cannot be changed")
    marks["completed"].discard(gid)
    marks["skipped"].discard(gid)
    if status != "none":
        marks[status].add(gid)
    _write_marks(cid, marks)
```

- [ ] **Step 4: Run the playing test file** — existing `test_played_roundtrip` must still pass.

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_playing_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/playing.py backend/tests/test_playing_store.py
git commit -m "feat(store): greeting marks (played/completed/skipped) with legacy migration"
```

---

### Task 6: `greetings.availability` learns `skipped`

**Files:**
- Modify: `backend/src/grimoire/store/greetings.py` (`availability`, line 187)
- Test: `backend/tests/test_greetings_store.py`

**Interfaces:**
- Produces: `availability(root, plotmap, played, player_tags, skipped=frozenset())` — skipped greetings are dropped from the output entirely and pruned from every predecessor list before joins are evaluated (an `all` join is satisfied by the remaining predecessors; a greeting whose only predecessor was skipped has no predecessor requirement). Callers pass `played ∪ completed` as `played`.

- [ ] **Step 1: Write the failing tests** (append to `test_greetings_store.py`)

```python
def test_availability_skipped_dropped_and_pruned(tmp_path):
    a = greetings.create_greeting(tmp_path, "A", "c", "v")
    b = greetings.create_greeting(tmp_path, "B", "c", "v")
    c = greetings.create_greeting(tmp_path, "C", "c", "v")
    greetings.set_edges(tmp_path, a, leads_to=[c])
    greetings.set_edges(tmp_path, b, leads_to=[c])
    pm = greetings.read_plotmap(tmp_path)
    # skip A: it vanishes from the output, and C's "all" join is satisfied by B alone
    out = {g["id"]: g for g in greetings.availability(tmp_path, pm, {b}, set(), skipped={a})}
    assert a not in out
    assert out[c]["available"] is True


def test_availability_sole_predecessor_skipped_frees_successor(tmp_path):
    a = greetings.create_greeting(tmp_path, "A", "c", "v")
    c = greetings.create_greeting(tmp_path, "C", "c", "v")
    greetings.set_edges(tmp_path, a, leads_to=[c])
    pm = greetings.read_plotmap(tmp_path)
    out = {g["id"]: g for g in greetings.availability(tmp_path, pm, set(), set(), skipped={a})}
    assert out[c]["available"] is True
```

- [ ] **Step 2: Run tests to verify they fail** (TypeError: unexpected keyword `skipped`)

- [ ] **Step 3: Implement** — change the signature and the first lines of `availability`:

```python
def availability(world_root: Path, plotmap: dict, played, player_tags,
                 skipped=frozenset()) -> list[dict]:
    """Pure: which greetings are startable given the played set + player tags.
    Skipped greetings are dropped from the output and pruned from predecessor
    lists — the plot routes around a greeting marked won't-do."""
    played = set(played)
    skipped = set(skipped)
    player_tags = set(player_tags)
    items = [g for g in list_greetings(world_root) if g["id"] not in skipped]
    preds: dict[str, set] = {g["id"]: set() for g in items}
    for src, e in plotmap.items():
        if src in skipped:
            continue
        for tgt in e.get("leads_to", []):
            if tgt in preds:
                preds[tgt].add(src)
```

The rest of the function body (the `for g in items:` loop) is unchanged.

- [ ] **Step 4: Run tests** — `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_greetings_store.py -q` Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/greetings.py backend/tests/test_greetings_store.py
git commit -m "feat(store): availability drops skipped greetings and prunes them from predecessor joins"
```

---

### Task 7: repoint `playing.py` to the campaign copy

**Files:**
- Modify: `backend/src/grimoire/store/playing.py` (`_world_root` line 16, `available_greetings` line 49, `start_from_greeting` line 64)
- Test: `backend/tests/test_playing_store.py` (existing tests need reordering; new isolation tests)

**Interfaces:**
- Consumes: Task 3 (campaign copies exist), Task 5 (`read_marks`), Task 6 (`skipped` param), `appearances.locked_version`.
- Produces: `available_greetings` reads the **campaign** plot map/greetings, feeds `played ∪ completed` and `skipped`, and each item gains `"mark": "played" | "completed" | None`; `start_from_greeting` reads the campaign copy and **the locked version wins** over the greeting's version field.

- [ ] **Step 1: Rewrite the affected existing tests.** Every test in `test_playing_store.py` that seeds world content **after** `campaigns.create_campaign` must seed first, then create the campaign. Replace the `_campaign` helper and update callers:

```python
def _world(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return worlds.create_world("W")


def _campaign_after_seed(wid):
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "S")
    return cid, sid
```

Mechanical rewrite per test: `wid = _world(monkeypatch, tmp_path)` first, seed `wroot`-side content (tags, PCs, characters, greetings, edges), then `cid, sid = _campaign_after_seed(wid)`, then play. Tests that don't seed the world (`test_played_roundtrip`, the marks tests from Task 5) keep an equivalent inline setup: `wid = _world(...)` then `_campaign_after_seed(wid)`. Marks tests from Task 5 that create greetings in `croot` are unaffected.

- [ ] **Step 2: Add the new failing tests**

```python
def test_campaign_play_isolated_from_world_edits(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    g = greetings.create_greeting(wroot, "Open", "s", "default", body="Original.")
    cid, sid = _campaign_after_seed(wid)
    greetings.update_greeting(wroot, g, body="Edited in world.")   # after the fork
    greetings.delete_greeting(wroot, g)                            # even deleted
    assert {x["id"] for x in playing.available_greetings(cid)} == {g}
    playing.start_from_greeting(cid, sid, g)
    assert scenes.read_scene(cid, sid)["messages"][0]["content"] == "Original."


def test_available_greetings_reports_marks_and_hides_skipped(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    g1 = greetings.create_greeting(wroot, "A", "s", "default", body="A.")
    g2 = greetings.create_greeting(wroot, "B", "s", "default", body="B.")
    g3 = greetings.create_greeting(wroot, "C", "s", "default", body="C.")
    greetings.set_edges(wroot, g1, leads_to=[g3])
    cid, _sid = _campaign_after_seed(wid)
    playing.mark_greeting(cid, g1, "completed")   # unlocks g3 like a play would
    playing.mark_greeting(cid, g2, "skipped")
    got = {x["id"]: x for x in playing.available_greetings(cid)}
    assert g2 not in got
    assert got[g1]["mark"] == "completed"
    assert got[g3]["mark"] is None and got[g3]["available"] is True


def test_start_from_greeting_locked_version_wins(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    char_id, _ = characters.create_character(wroot, "Mara", "young", characters.blank_card("Mara"))
    characters.create_version(wroot, char_id, "veteran", characters.blank_card("Mara"))
    g = greetings.create_greeting(wroot, "Open", char_id, "young", body="Hi.")
    cid, sid = _campaign_after_seed(wid)
    other = scenes.create_scene(cid, "S0")
    ap.appear(cid, other, "characters", char_id, "veteran", "npc")  # lock veteran first
    playing.start_from_greeting(cid, sid, g)                        # greeting says young
    assert ap.locked_version(cid, "characters", char_id) == "veteran"
```

- [ ] **Step 3: Run tests to verify the new ones fail** (isolation test fails: play still reads the world)

- [ ] **Step 4: Implement.** In `playing.py`, delete `_world_root` (lines 16-17) and drop `worlds` from the import (line 9). Rewrite:

```python
def available_greetings(cid: str, after: str | None = None) -> list[dict]:
    croot = campaigns.campaign_root(cid)
    plotmap = greetings.read_plotmap(croot)
    marks = read_marks(cid)
    out = greetings.availability(croot, plotmap, marks["played"] | marks["completed"],
                                 player_tags(cid), skipped=marks["skipped"])
    mark_of = {gid: "played" for gid in marks["played"]}
    mark_of.update({gid: "completed" for gid in marks["completed"]})
    for g in out:
        g["mark"] = mark_of.get(g["id"])
    unlocked: set[str] = set()
    if after:
        gid = scenes.read_scene(cid, after)["meta"].get("greeting", "")
        if gid:
            unlocked = set(greetings.edges_of(plotmap, gid)["leads_to"])
    for g in out:
        g["unlocked"] = g["id"] in unlocked
    out.sort(key=lambda g: not g["unlocked"])  # stable: unlocked first, rest keep order
    return out


def start_from_greeting(cid: str, sid: str, gid: str) -> None:
    croot = campaigns.campaign_root(cid)
    g = greetings.read_greeting(croot, gid)["meta"]   # raises GreetingNotFound
    scene = scenes.read_scene(cid, sid)               # raises SceneNotFound
    if scene["messages"]:
        raise PlayError("scene already has messages")
    if not {a["id"]: a["available"] for a in available_greetings(cid)}.get(gid, False):
        raise PlayError(f"greeting {gid} is not available")
    # Cast everyone present at the opener. A locked version always wins; otherwise
    # the primary uses the greeting's version and co-present characters their default.
    for actor in dict.fromkeys([g["character"], *g["present"]]):
        version = appearances.locked_version(cid, "characters", actor)
        if version is None:
            version = g["version"] if actor == g["character"] else \
                characters.read_character(croot, actor)["meta"]["default_version"]
        appearances.appear(cid, sid, "characters", actor, version, "npc")
    _mark_played(cid, gid)
    scenes.stamp_greeting(cid, sid, gid)
    text = context._substitute(greetings.read_greeting(croot, gid)["body"],
                               context.scene_substitutions(cid, sid))
    scenes.append_message(cid, sid, "assistant", text)
```

- [ ] **Step 5: Run the playing tests, then the full suite** — other test files (`test_routes.py`, `test_suggest_store.py`, `test_context.py`, `test_character_sync.py`) may seed worlds after campaign creation too. Fix any failures the same way: **seed the world first, then create the campaign** (or, where a test intends post-fork content, create it in the campaign root instead). Do not change production code to accommodate a test.

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS after test reordering.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/playing.py backend/tests/
git commit -m "feat(store): campaign play reads the campaign copy; marks feed availability; locked version wins casting"
```

---

### Task 8: repoint the remaining play-time world readers

**Files:**
- Modify: `backend/src/grimoire/routes.py` (`_resolve_cast` line 1271, `post_scene_cast` line 1525), `backend/src/grimoire/store/appearances.py` (`suggestions` line 218), `backend/src/grimoire/store/context.py` (`_cast_directory` line 152 and its call at line 400), `backend/src/grimoire/store/suggest.py` (lines 17-18, 80, 108-111, 184-185)
- Test: `backend/tests/test_appearances_store.py`, `backend/tests/test_routes.py`

**Interfaces:**
- Produces: every campaign-context read of characters/PCs/taglines resolves against the campaign root; the world root remains only in sync, `appearances._lock`/`_copy_actor` (copy source), and world-scoped routes.

- [ ] **Step 1: Write a failing test** (append to `test_appearances_store.py`, following its setup style)

```python
def test_suggestions_candidates_come_from_campaign_copy(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    card = characters.blank_card("Mara")
    card["data"]["description"] = "Mara knows Rowan."
    characters.create_character(wroot, "Mara", "default", card)
    characters.create_character(wroot, "Rowan", "default", characters.blank_card("Rowan"))
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "S")
    ap.appear(cid, sid, "characters", "mara", "default", "npc")
    characters.delete_character(wroot, "rowan")  # world diverges after the fork
    got = ap.suggestions(cid, sid)
    assert [s["character"] for s in got] == ["rowan"]  # campaign copy still has Rowan
```

- [ ] **Step 2: Run to verify it fails** (suggestions read the world, where Rowan is gone → `[]`)

- [ ] **Step 3: Implement**

`appearances.py` `suggestions()`: replace `wroot = worlds.world_root(_world_id(cid))` and `characters.list_characters(wroot)` with the campaign root:

```python
    candidates = [c for c in characters.list_characters(croot)
                  if c["id"] not in appeared_chars and c["id"] not in dismissed and c["id"] not in in_scene_chars]
```

(delete the now-unused `wroot` line).

`context.py`: change `_cast_directory(croot, wroot, cid, sid)` to `_cast_directory(croot, cid, sid)` — inside, every `wroot` becomes `croot` (`characters.character_refs(croot)`, `taglines.read(croot, ...)`, `characters.read_character(croot, ...)`). At the call site (line ~400) delete the `wroot = worlds.world_root(...)` line and call `_cast_directory(croot, cid, sid)`. Remove the `worlds` import if it becomes unused.

`suggest.py`: rename `_world_root(cid)` to `_root(cid)` returning `campaigns.campaign_root(cid)`; update the three call sites (`wroot` variables become `croot`); adjust imports (drop `worlds` if unused).

`routes.py` `_resolve_cast` (line 1271): campaign copy first, world as fallback:

```python
def _resolve_cast(cid: str, tokens: list[str]) -> list[dict]:
    croot = store.campaigns.campaign_root(cid)
    wroot = store.worlds.world_root(store.campaigns.read_campaign(cid)["meta"].get("world", ""))
    out = []
    for tok in tokens:
        kind, _, aid = tok.partition(":")
        try:
            if kind == "pcs":
                name = store.pcs.read_pc(croot, aid)["meta"].get("name", aid)
            else:
                try:
                    name = store.characters.read_character(croot, aid)["meta"].get("name", aid)
                except store.characters.CharacterNotFound:
                    name = store.characters.read_character(wroot, aid)["meta"].get("name", aid)
        except (store.characters.CharacterNotFound, store.pcs.PCNotFound):
            name = aid
        out.append({"kind": kind, "id": aid, "name": name})
    return out
```

`routes.py` `post_scene_cast` (line 1525): resolve the default version campaign-first:

```python
    version = body.version
    croot = store.campaigns.campaign_root(cid)
    try:
        if version is None:
            if body.kind == "characters":
                try:
                    version = store.characters.read_character(croot, body.id)["meta"]["default_version"]
                except store.characters.CharacterNotFound:
                    version = store.characters.read_character(wroot, body.id)["meta"]["default_version"]
            else:
                try:
                    version = store.pcs.read_pc(croot, body.id)["meta"]["default_version"]
                except store.pcs.PCNotFound:
                    version = store.pcs.read_pc(wroot, body.id)["meta"]["default_version"]
    except (store.characters.CharacterNotFound, store.pcs.PCNotFound):
        raise HTTPException(status_code=404, detail="actor not found")
```

(The world fallback keeps "cast a brand-new world actor before syncing" working; `appear()` still copies from the world in that case.)

- [ ] **Step 4: Run the full suite; fix test seeding order as in Task 7 Step 5.**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/src/grimoire/store/appearances.py backend/src/grimoire/store/context.py backend/src/grimoire/store/suggest.py backend/tests/
git commit -m "refactor(store): remaining play-time readers use the campaign copy"
```

---

### Task 9: explicit `pick_version` + purge; `appear()` routes through it

**Files:**
- Modify: `backend/src/grimoire/store/appearances.py` (`appear`, line 93; new helpers)
- Test: `backend/tests/test_appearances_store.py`

**Interfaces:**
- Consumes: `campaigns.read_manifest`/`write_manifest`, `characters.set_default_version`, `pcs.set_default_version`.
- Produces: `appearances.pick_version(cid, kind, actor_id, version_id)` — locks without a scene (`scenes: []`, role `player` for pcs / `npc` for characters), purges sibling version files, points the campaign `default_version` at the pick, drops the whole-actor manifest ref; raises `AppearError` when already locked or the version is missing from the campaign. `appear()`'s first-appearance branch uses the same `_lock` helper (lazy pick), keeping its world-copy fallback for actors not yet in the campaign.

- [ ] **Step 1: Write the failing tests** (append to `test_appearances_store.py`)

```python
def _fork(monkeypatch, tmp_path, versions=("young", "veteran")):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    char_id, _ = characters.create_character(wroot, "Mara", versions[0])
    for v in versions[1:]:
        characters.create_version(wroot, char_id, v, characters.blank_card("Mara"))
    cid = campaigns.create_campaign("Run", wid)
    return wid, cid, char_id


def test_pick_version_purges_and_locks(monkeypatch, tmp_path):
    wid, cid, char_id = _fork(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ap.pick_version(cid, "characters", char_id, "veteran")
    assert ap.locked_version(cid, "characters", char_id) == "veteran"
    assert not (croot / "characters" / char_id / "young.json").exists()
    assert (croot / "characters" / char_id / "veteran.json").exists()
    assert characters.read_character(croot, char_id)["meta"]["default_version"] == "veteran"
    assert f"characters/{char_id}" not in campaigns.read_manifest(cid)
    rec = ap.record(cid)[f"characters/{char_id}"]
    assert rec["scenes"] == [] and rec["role"] == "npc"
    with pytest.raises(ap.AppearError):
        ap.pick_version(cid, "characters", char_id, "young")  # already locked


def test_pick_version_unknown_version_raises(monkeypatch, tmp_path):
    wid, cid, char_id = _fork(monkeypatch, tmp_path)
    with pytest.raises(ap.AppearError):
        ap.pick_version(cid, "characters", char_id, "bogus")


def test_lazy_appear_picks_and_purges(monkeypatch, tmp_path):
    wid, cid, char_id = _fork(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    ap.appear(cid, sid, "characters", char_id, "young", "npc")
    croot = campaigns.campaign_root(cid)
    assert not (croot / "characters" / char_id / "veteran.json").exists()
    assert f"characters/{char_id}" not in campaigns.read_manifest(cid)
    assert ap.record(cid)[f"characters/{char_id}"]["scenes"] == [sid]


def test_appear_after_pick_adds_scene(monkeypatch, tmp_path):
    wid, cid, char_id = _fork(monkeypatch, tmp_path)
    ap.pick_version(cid, "characters", char_id, "veteran")
    sid = scenes.create_scene(cid, "S")
    ap.appear(cid, sid, "characters", char_id, "veteran", "npc")
    assert ap.record(cid)[f"characters/{char_id}"]["scenes"] == [sid]
```

Add `scenes` / `campaigns` to the test imports if missing.

- [ ] **Step 2: Run tests to verify they fail** (no `pick_version`)

- [ ] **Step 3: Implement** (in `appearances.py`, below `_copy_actor`)

```python
def _purge_other_versions(croot: Path, kind: str, actor_id: str, keep: str) -> None:
    d = croot / kind / actor_id
    ext = _version_ext(kind)
    for p in d.glob(f"*.{ext}"):
        if p.name not in (f"{keep}.{ext}", _meta_name(kind)):
            p.unlink()


def _set_default(croot: Path, kind: str, actor_id: str, vid: str) -> None:
    if kind == "characters":
        characters.set_default_version(croot, actor_id, vid)
    else:
        pcs.set_default_version(croot, actor_id, vid)


def _drop_manifest_ref(cid: str, kind: str, actor_id: str) -> None:
    manifest = campaigns.read_manifest(cid)
    if manifest.pop(_ref(kind, actor_id), None) is not None:
        campaigns.write_manifest(cid, manifest)


def _lock(cid: str, kind: str, actor_id: str, version_id: str) -> str:
    """Materialize a version lock in the campaign tree: ensure the version file is
    present, purge every sibling version, point default_version at the pick, and
    drop the whole-actor sync ref (the locked per-version flow takes over).
    Returns the sync base hash for the appearance record."""
    wroot = worlds.world_root(_world_id(cid))
    croot = campaigns.campaign_root(cid)
    base = actor_hash(wroot, kind, actor_id, version_id)
    if actor_hash(croot, kind, actor_id, version_id) is None:
        if base is None:
            raise AppearError(f"no {_ref(kind, actor_id)}/{version_id} in world or campaign")
        _copy_actor(wroot, croot, kind, actor_id, version_id)  # actor not yet in the campaign
    _purge_other_versions(croot, kind, actor_id, version_id)
    _set_default(croot, kind, actor_id, version_id)
    _drop_manifest_ref(cid, kind, actor_id)
    return base or ""  # campaign-local actor: empty world-base, sync skips it


def pick_version(cid: str, kind: str, actor_id: str, version_id: str) -> None:
    """Explicit pick from the campaign's world pages: lock without a scene."""
    ref = _ref(kind, actor_id)
    data = record(cid)
    if ref in data:
        raise AppearError(f"{ref} is already locked to version {data[ref]['version']}")
    croot = campaigns.campaign_root(cid)
    if actor_hash(croot, kind, actor_id, version_id) is None:
        raise AppearError(f"no {ref}/{version_id} in campaign")
    base = _lock(cid, kind, actor_id, version_id)
    data[ref] = {"version": version_id, "base": base, "scenes": [],
                 "role": "player" if kind == "pcs" else "npc"}
    _write(cid, data)
    campaigns.touch(cid)
```

Then rewrite `appear()`'s first-appearance branch (everything from `wroot = ...`, lines 107-121) as:

```python
    base = _lock(cid, kind, actor_id, version_id)
    data[ref] = {"version": version_id, "base": base, "scenes": [scene_id], "role": role}
    _write(cid, data)
    campaigns.touch(cid)
```

(The old "no world source" check lives inside `_lock` now — a campaign-local actor whose version is missing from both roots still raises `AppearError`.)

- [ ] **Step 4: Run the appearances + full suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS. (`appear`'s behavior for world actors not yet in the campaign is unchanged: `_lock` falls back to `_copy_actor`.)

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/appearances.py backend/tests/test_appearances_store.py
git commit -m "feat(store): explicit pick_version with purge; lazy appear routes through the same lock"
```

---

### Task 10: `import_version` (replace the pick from the world)

**Files:**
- Modify: `backend/src/grimoire/store/appearances.py`
- Test: `backend/tests/test_appearances_store.py`

**Interfaces:**
- Produces: `appearances.import_version(cid, kind, actor_id, version_id)` — requires an existing lock (else `AppearError`); copies the world version file, points `default_version` and the lock at it, deletes the previously locked file, sets `base` to the world's current hash.

- [ ] **Step 1: Write the failing tests**

```python
def test_import_version_replaces_pick(monkeypatch, tmp_path):
    wid, cid, char_id = _fork(monkeypatch, tmp_path)
    ap.pick_version(cid, "characters", char_id, "young")
    ap.import_version(cid, "characters", char_id, "veteran")
    croot = campaigns.campaign_root(cid)
    assert ap.locked_version(cid, "characters", char_id) == "veteran"
    assert (croot / "characters" / char_id / "veteran.json").exists()
    assert not (croot / "characters" / char_id / "young.json").exists()
    assert characters.read_character(croot, char_id)["meta"]["default_version"] == "veteran"
    wroot = worlds.world_root(wid)
    assert ap.record(cid)[f"characters/{char_id}"]["base"] == \
        characters.card_hash(wroot, char_id, "veteran")


def test_import_version_requires_lock(monkeypatch, tmp_path):
    wid, cid, char_id = _fork(monkeypatch, tmp_path)
    with pytest.raises(ap.AppearError):
        ap.import_version(cid, "characters", char_id, "veteran")


def test_import_version_unknown_world_version(monkeypatch, tmp_path):
    wid, cid, char_id = _fork(monkeypatch, tmp_path)
    ap.pick_version(cid, "characters", char_id, "young")
    with pytest.raises(ap.AppearError):
        ap.import_version(cid, "characters", char_id, "bogus")
```

- [ ] **Step 2: Run to verify failure** (no `import_version`)

- [ ] **Step 3: Implement** (below `pick_version`)

```python
def import_version(cid: str, kind: str, actor_id: str, version_id: str) -> None:
    """Replace the locked version with `version_id` from the source world. The
    one-version-per-locked-actor invariant always holds; unlocked actors take
    world changes via sync instead."""
    data = record(cid)
    ref = _ref(kind, actor_id)
    rec = data.get(ref)
    if rec is None:
        raise AppearError(f"{ref} is not locked; world changes arrive via sync until a version is picked")
    wroot = worlds.world_root(_world_id(cid))
    base = actor_hash(wroot, kind, actor_id, version_id)
    if base is None:
        raise AppearError(f"no {ref}/{version_id} in world")
    croot = campaigns.campaign_root(cid)
    ext = _version_ext(kind)
    d = croot / kind / actor_id
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{version_id}.{ext}").write_text(
        (wroot / kind / actor_id / f"{version_id}.{ext}").read_text(encoding="utf-8"),
        encoding="utf-8")
    _set_default(croot, kind, actor_id, version_id)
    old = rec["version"]
    if old != version_id and (d / f"{old}.{ext}").exists():
        (d / f"{old}.{ext}").unlink()
    rec["version"] = version_id
    rec["base"] = base
    _write(cid, data)
    campaigns.touch(cid)
```

- [ ] **Step 4: Run tests** — appearances file then full suite. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/appearances.py backend/tests/test_appearances_store.py
git commit -m "feat(store): import_version replaces a locked version from the source world"
```

---

### Task 11: sync covers greetings + plot map

**Files:**
- Modify: `backend/src/grimoire/store/sync.py` (`_entity_blob` line 25, `incoming` line 30, `_advance` line 113; import `greetings`)
- Test: `backend/tests/test_sync_store.py`

**Interfaces:**
- Produces: `sync.incoming` reports greetings refs (`{"kind": "greetings", "id": ...}`) with entity semantics and a single plot-map item (`{"kind": "plotmap", "id": "plotmap"}`, blob `{"name": "Plot map", "body": <raw json>}`); `accept`/`reject` advance both. World-side deletions stay skipped, matching entities.

- [ ] **Step 1: Write the failing tests** (append to `test_sync_store.py`, matching its setup style)

```python
def _greeting_world(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    g = greetings.create_greeting(wroot, "Gala", "c", "v", body="Original.")
    greetings.set_edges(wroot, g, leads_to=[])
    cid = campaigns.create_campaign("Run", wid)
    return wid, wroot, cid, g


def test_incoming_world_greeting_edit_is_update(monkeypatch, tmp_path):
    wid, wroot, cid, g = _greeting_world(monkeypatch, tmp_path)
    greetings.update_greeting(wroot, g, body="Changed.")
    items = {(i["ref"]["kind"], i["ref"]["id"]): i for i in sync.incoming(cid)}
    item = items[("greetings", g)]
    assert item["status"] == "update"
    assert item["world"]["body"] == "Changed."
    sync.accept(cid, [{"kind": "greetings", "id": g}])
    croot = campaigns.campaign_root(cid)
    assert greetings.read_greeting(croot, g)["body"] == "Changed."
    assert sync.incoming(cid) == []


def test_incoming_greeting_conflict_and_reject(monkeypatch, tmp_path):
    wid, wroot, cid, g = _greeting_world(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    greetings.update_greeting(wroot, g, body="World edit.")
    greetings.update_greeting(croot, g, body="Campaign edit.")
    items = {(i["ref"]["kind"], i["ref"]["id"]): i for i in sync.incoming(cid)}
    assert items[("greetings", g)]["status"] == "conflict"
    sync.reject(cid, [{"kind": "greetings", "id": g}])
    assert greetings.read_greeting(croot, g)["body"] == "Campaign edit."
    assert sync.incoming(cid) == []


def test_incoming_plotmap_update_accept(monkeypatch, tmp_path):
    wid, wroot, cid, g = _greeting_world(monkeypatch, tmp_path)
    g2 = greetings.create_greeting(wroot, "Next", "c", "v")
    greetings.set_edges(wroot, g, leads_to=[g2])
    items = {(i["ref"]["kind"], i["ref"]["id"]): i for i in sync.incoming(cid)}
    assert items[("plotmap", "plotmap")]["status"] == "update"
    sync.accept(cid, [{"kind": "plotmap", "id": "plotmap"},
                      {"kind": "greetings", "id": g2}])
    croot = campaigns.campaign_root(cid)
    assert greetings.edges_of(greetings.read_plotmap(croot), g)["leads_to"] == [g2]
```

- [ ] **Step 2: Run to verify failure** (greetings/plotmap refs missing from `incoming`)

- [ ] **Step 3: Implement** in `sync.py`:

Add `greetings` to the module import (line 14). Rewrite `_entity_blob`:

```python
def _entity_blob(root: Path, kind: str, eid: str) -> dict:
    if kind == "greetings":
        g = greetings.read_greeting(root, eid)
        return {"name": g["meta"].get("name", eid), "body": g["body"]}
    e = entities.read_entity(root, kind, eid)
    return {"name": e["meta"].get("name", eid), "body": e["body"]}
```

In `incoming()`: switch enumeration to synced refs and let greetings flow through the entity path:

```python
    refs: set[str] = set(manifest)
    if wroot.exists():
        refs |= {_ref_str(k, e) for k, e in entities.synced_refs(wroot)}
    refs |= {_ref_str(k, e) for k, e in entities.synced_refs(croot)}
```

and change the kind filter to:

```python
        if kind not in entities.SYNCED_KINDS:
            continue  # actor refs + plotmap are handled by their own passes
```

Add the plot-map pass and include it in the return:

```python
def _plotmap_blob(root: Path) -> dict:
    p = root / "plotmap.json"
    return {"name": "Plot map", "body": p.read_text(encoding="utf-8") if p.exists() else ""}


def _plotmap_incoming(cid: str) -> list[dict]:
    wroot = worlds.world_root(_world_id(cid))
    croot = campaigns.campaign_root(cid)
    world_h = greetings.plotmap_hash(wroot) if wroot.exists() else None
    base = campaigns.read_manifest(cid).get("plotmap")
    if world_h is None or world_h == base:
        return []
    mine_h = greetings.plotmap_hash(croot)
    status = "new" if mine_h is None else ("update" if mine_h == base else "conflict")
    item: dict = {"ref": {"kind": "plotmap", "id": "plotmap"}, "status": status,
                  "world": _plotmap_blob(wroot)}
    if mine_h is not None:
        item["mine"] = _plotmap_blob(croot)
    return [item]
```

Return `out + _plotmap_incoming(cid) + _actor_incoming(cid)` from `incoming`.

In `_advance`, handle the plot-map ref before the actor/entity branches (inside the `for ref in refs:` loop):

```python
        if kind == "plotmap":
            world_h = greetings.plotmap_hash(wroot) if wroot.exists() else None
            if world_h is None or manifest.get("plotmap") == world_h:
                continue
            if copy:
                (croot / "plotmap.json").write_text(
                    (wroot / "plotmap.json").read_text(encoding="utf-8"), encoding="utf-8")
            manifest["plotmap"] = world_h
            manifest_changed = True
            touched = True
            continue
```

The existing entity branch already copies `<kind>/<eid>.md` and updates the manifest with `entity_hash` — it works for greetings unchanged.

- [ ] **Step 4: Run** `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_sync_store.py backend/tests/test_routes.py -q` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/sync.py backend/tests/test_sync_store.py
git commit -m "feat(store): sync engine covers greetings and the plot map"
```

---

### Task 12: sync covers unpicked actors (whole-actor diffs)

**Files:**
- Modify: `backend/src/grimoire/store/sync.py` (needs `import shutil`)
- Test: `backend/tests/test_sync_store.py`

**Interfaces:**
- Produces: unpicked campaign actors (manifest actor refs with no `appearances.json` lock) diff **whole-actor**: one incoming item per changed actor, blob `{"name": ..., "body": "versions: a, b"}`; accept re-copies the entire actor dir (removing deleted versions), reject advances the base. Locked actors keep the per-locked-version flow; purged versions never resurface. New world actors appear as `new`.

- [ ] **Step 1: Write the failing tests**

```python
def _actor_world(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    char_id, _ = characters.create_character(wroot, "Mara", "young")
    characters.create_version(wroot, char_id, "veteran", characters.blank_card("Mara"))
    cid = campaigns.create_campaign("Run", wid)
    return wid, wroot, cid, char_id


def test_unpicked_actor_world_edit_is_update_and_accept_recopies(monkeypatch, tmp_path):
    wid, wroot, cid, char_id = _actor_world(monkeypatch, tmp_path)
    card = characters.blank_card("Mara")
    card["data"]["description"] = "changed"
    characters.update_version(wroot, char_id, "young", card)
    characters.delete_version(wroot, char_id, "veteran")
    items = {(i["ref"]["kind"], i["ref"]["id"]): i for i in sync.incoming(cid)}
    assert items[("characters", char_id)]["status"] == "update"
    sync.accept(cid, [{"kind": "characters", "id": char_id}])
    croot = campaigns.campaign_root(cid)
    assert characters.read_card(croot, char_id, "young")["data"]["description"] == "changed"
    assert not (croot / "characters" / char_id / "veteran.json").exists()  # deletion propagates
    assert sync.incoming(cid) == []


def test_unpicked_actor_conflict_and_reject(monkeypatch, tmp_path):
    wid, wroot, cid, char_id = _actor_world(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    characters.update_version(wroot, char_id, "young", characters.blank_card("W-Mara"))
    characters.update_version(croot, char_id, "young", characters.blank_card("C-Mara"))
    items = {(i["ref"]["kind"], i["ref"]["id"]): i for i in sync.incoming(cid)}
    assert items[("characters", char_id)]["status"] == "conflict"
    sync.reject(cid, [{"kind": "characters", "id": char_id}])
    assert characters.read_card(croot, char_id, "young")["data"]["name"] == "C-Mara"
    assert sync.incoming(cid) == []


def test_new_world_actor_is_new(monkeypatch, tmp_path):
    wid, wroot, cid, char_id = _actor_world(monkeypatch, tmp_path)
    new_id, _ = characters.create_character(wroot, "Rowan")
    items = {(i["ref"]["kind"], i["ref"]["id"]): i for i in sync.incoming(cid)}
    assert items[("characters", new_id)]["status"] == "new"
    sync.accept(cid, [{"kind": "characters", "id": new_id}])
    croot = campaigns.campaign_root(cid)
    assert (croot / "characters" / new_id / "character.md").exists()


def test_locked_actor_new_world_version_invisible(monkeypatch, tmp_path):
    wid, wroot, cid, char_id = _actor_world(monkeypatch, tmp_path)
    ap.pick_version(cid, "characters", char_id, "young")
    characters.create_version(wroot, char_id, "elder", characters.blank_card("Mara"))
    assert sync.incoming(cid) == []  # only the locked version's own edits would show
```

Add `ap` (`from grimoire.store import appearances as ap`) to the imports if missing.

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement** in `sync.py` (add `import shutil` at top):

```python
def _dir_hash(root: Path, kind: str, actor_id: str) -> str | None:
    return characters.dir_hash(root, actor_id) if kind == "characters" else pcs.dir_hash(root, actor_id)


def _actor_summary_blob(root: Path, kind: str, actor_id: str) -> dict:
    detail = (characters.read_character(root, actor_id) if kind == "characters"
              else pcs.read_pc(root, actor_id))
    versions = ", ".join(v["id"] for v in detail["versions"])
    return {"name": detail["meta"].get("name", actor_id), "body": f"versions: {versions}"}


def _unpicked_incoming(cid: str) -> list[dict]:
    wroot = worlds.world_root(_world_id(cid))
    croot = campaigns.campaign_root(cid)
    manifest = campaigns.read_manifest(cid)
    locked = set(appearances.record(cid))
    refs = {r for r in manifest if r.partition("/")[0] in appearances.ACTOR_KINDS}
    if wroot.exists():
        refs |= {f"characters/{a}" for a in characters.character_refs(wroot)}
        refs |= {f"pcs/{a}" for a in pcs.pc_refs(wroot)}
    out: list[dict] = []
    for ref in sorted(refs):
        if ref in locked:
            continue  # the per-locked-version pass owns this actor
        kind, _, aid = ref.partition("/")
        world_h = _dir_hash(wroot, kind, aid) if wroot.exists() else None
        if world_h is None or world_h == manifest.get(ref):
            continue  # no incoming change (incl. world-side deletions, skipped)
        mine_h = _dir_hash(croot, kind, aid)
        status = ("new" if mine_h is None
                  else "update" if mine_h == manifest.get(ref) else "conflict")
        item: dict = {"ref": {"kind": kind, "id": aid}, "status": status,
                      "world": _actor_summary_blob(wroot, kind, aid)}
        if mine_h is not None:
            item["mine"] = _actor_summary_blob(croot, kind, aid)
        out.append(item)
    return out
```

Change `incoming`'s return to `out + _plotmap_incoming(cid) + _actor_incoming(cid) + _unpicked_incoming(cid)`.

In `_advance`, replace the actor branch:

```python
        if kind in appearances.ACTOR_KINDS:
            if appearances.record(cid).get(_ref_str(kind, eid)) is not None:
                if _advance_actor(cid, kind, eid, copy=copy):
                    touched = True
                continue
            world_h = _dir_hash(wroot, kind, eid) if wroot.exists() else None
            if world_h is None or manifest.get(_ref_str(kind, eid)) == world_h:
                continue  # not pending
            if copy:
                dst = croot / kind / eid
                if dst.exists():
                    shutil.rmtree(dst)  # replace wholesale so deleted versions go too
                shutil.copytree(wroot / kind / eid, dst)
            manifest[_ref_str(kind, eid)] = world_h
            manifest_changed = True
            touched = True
            continue
```

- [ ] **Step 4: Run the sync tests + full suite.** Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/sync.py backend/tests/test_sync_store.py
git commit -m "feat(store): whole-actor sync for unpicked characters and PCs"
```

---

### Task 13: campaign greeting routes (CRUD, edges, marks) + greeting image serving

**Files:**
- Modify: `backend/src/grimoire/routes.py` — new routes in the "campaign greetings / play" section (after `get_available_greetings`, line 1655, and before the generic block at 1698); one model near the others (~line 120); `get_campaign_entity_image` (line 1729)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `playing.read_marks` / `mark_greeting`, `greetings.*` root-parameterized CRUD.
- Produces (all campaign-scoped): `GET /campaigns/{cid}/greetings` (list items gain `"mark"`), `POST /campaigns/{cid}/greetings`, `GET/PUT/DELETE /campaigns/{cid}/greetings/{gid}` (GET includes `edges` + `predecessors` from the campaign plot map), `PUT /campaigns/{cid}/greetings/{gid}/edges`, `POST /campaigns/{cid}/greetings/{gid}/mark` (body `{"status": "completed"|"skipped"|"none"}`; 404 unknown greeting, 409 played). Campaign greeting images serve via the existing generic GET (kind check widened).

- [ ] **Step 1: Write the failing tests** (append to `test_routes.py`, following its `client` fixture style)

```python
def test_campaign_greeting_crud_and_marks(client, monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = client.post("/api/worlds", json={"name": "W"}).json()["id"]
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara"})
    g = client.post(f"/api/worlds/{wid}/greetings",
                    json={"name": "Gala", "character": "mara", "version": "default",
                          "body": "Hi."}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]

    # campaign list carries marks
    out = client.get(f"/api/campaigns/{cid}/greetings").json()
    assert [x["id"] for x in out] == [g] and out[0]["mark"] is None

    # detail includes edges + predecessors from the campaign plot map
    detail = client.get(f"/api/campaigns/{cid}/greetings/{g}").json()
    assert detail["body"] == "Hi." and detail["edges"] == {"leads_to": [], "excludes": []}

    # campaign edit does not touch the world
    r = client.put(f"/api/campaigns/{cid}/greetings/{g}", json={"body": "Campaign version."})
    assert r.status_code == 200
    assert client.get(f"/api/worlds/{wid}/greetings/{g}").json()["body"] == "Hi."

    # marks
    r = client.post(f"/api/campaigns/{cid}/greetings/{g}/mark", json={"status": "skipped"})
    assert r.status_code == 200
    assert client.get(f"/api/campaigns/{cid}/greetings").json()[0]["mark"] == "skipped"
    assert client.get(f"/api/campaigns/{cid}/greetings/available").json() == []
    r = client.post(f"/api/campaigns/{cid}/greetings/nope/mark", json={"status": "skipped"})
    assert r.status_code == 404

    # create + delete campaign-local greeting
    g2 = client.post(f"/api/campaigns/{cid}/greetings",
                     json={"name": "Local", "character": "mara", "version": "default",
                           "body": "Local."}).json()["id"]
    assert client.delete(f"/api/campaigns/{cid}/greetings/{g2}").status_code == 200


def test_campaign_greeting_mark_played_conflicts(client, monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = client.post("/api/worlds", json={"name": "W"}).json()["id"]
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara"})
    g = client.post(f"/api/worlds/{wid}/greetings",
                    json={"name": "Gala", "character": "mara", "version": "default",
                          "body": "Hi."}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/start-from-greeting",
                       json={"greeting": g}).status_code == 200
    r = client.post(f"/api/campaigns/{cid}/greetings/{g}/mark", json={"status": "completed"})
    assert r.status_code == 409
```

- [ ] **Step 2: Run to verify failure** (`GET /campaigns/{cid}/greetings` currently falls into the generic entity route → 404 "unknown kind")

- [ ] **Step 3: Implement**

Model (near the other request models):

```python
class MarkBody(BaseModel):
    status: str  # "completed" | "skipped" | "none" — validated in the store
```

Routes — insert **directly after** `get_available_greetings` (so `/greetings/available` keeps winning over `/greetings/{gid}`):

```python
@router.get("/campaigns/{cid}/greetings")
def get_campaign_greetings(cid: str):
    root = _campaign_root_or_404(cid)
    marks = store.playing.read_marks(cid)
    mark_of = {g: "played" for g in marks["played"]}
    mark_of.update({g: "completed" for g in marks["completed"]})
    mark_of.update({g: "skipped" for g in marks["skipped"]})
    return [{**g, "mark": mark_of.get(g["id"])} for g in store.greetings.list_greetings(root)]


@router.post("/campaigns/{cid}/greetings")
def post_campaign_greeting(cid: str, body: GreetingCreate):
    root = _campaign_root_or_404(cid)
    gid = store.greetings.create_greeting(root, body.name, body.character, body.version,
                                          body.body, body.requires_tags,
                                          body.predecessor_join, present=body.present)
    return {"id": gid}


@router.get("/campaigns/{cid}/greetings/{gid}")
def get_campaign_greeting(cid: str, gid: str):
    root = _campaign_root_or_404(cid)
    try:
        g = store.greetings.read_greeting(root, gid)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    plotmap = store.greetings.read_plotmap(root)
    g["edges"] = store.greetings.edges_of(plotmap, gid)
    g["predecessors"] = store.greetings.predecessors_of(plotmap, gid)
    return g


@router.put("/campaigns/{cid}/greetings/{gid}")
def put_campaign_greeting(cid: str, gid: str, body: GreetingUpdate):
    root = _campaign_root_or_404(cid)
    try:
        store.greetings.update_greeting(root, gid, name=body.name, body=body.body,
                                        requires_tags=body.requires_tags,
                                        predecessor_join=body.predecessor_join,
                                        present=body.present)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    return {"ok": True}


@router.put("/campaigns/{cid}/greetings/{gid}/edges")
def put_campaign_greeting_edges(cid: str, gid: str, body: Edges):
    root = _campaign_root_or_404(cid)
    try:
        store.greetings.read_greeting(root, gid)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    store.greetings.set_edges(root, gid, body.leads_to, body.excludes)
    return {"ok": True}


@router.delete("/campaigns/{cid}/greetings/{gid}")
def delete_campaign_greeting(cid: str, gid: str):
    root = _campaign_root_or_404(cid)
    try:
        store.greetings.delete_greeting(root, gid)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    return {"ok": True}


@router.post("/campaigns/{cid}/greetings/{gid}/mark")
def post_campaign_greeting_mark(cid: str, gid: str, body: MarkBody):
    _campaign_root_or_404(cid)
    try:
        store.playing.mark_greeting(cid, gid, body.status)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    except store.playing.PlayError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True}
```

Widen campaign greeting-image serving: in `get_campaign_entity_image` (line 1729), replace `_entity_kind_or_404(kind)` with `_image_kind_or_404(kind)` (read-only, same as the world route).

- [ ] **Step 4: Run** `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(routes): campaign-scoped greeting CRUD, edges and marks"
```

---

### Task 14: campaign character/PC routes + pick/import

**Files:**
- Modify: `backend/src/grimoire/routes.py` — new routes in the "campaign cast & suggestions" section (after `get_campaign_pcs`/`post_campaign_pc`, line 1501-1511, before the generic block); one model (`PickBody`)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Produces (campaign-scoped):
  - Characters: `GET /campaigns/{cid}/characters`, `GET /campaigns/{cid}/characters/{char}`, `PUT /campaigns/{cid}/characters/{char}` (default version), `POST .../versions` (409 when locked), `PUT .../versions/{vid}`, `DELETE .../versions/{vid}`.
  - PCs: `GET /campaigns/{cid}/pcs/{pid}`, `PUT /campaigns/{cid}/pcs/{pid}` (tags free-form + default version), `POST .../versions` (409 when locked), `PUT .../versions/{vid}`, `DELETE .../versions/{vid}`.
  - Both kinds: `POST /campaigns/{cid}/{kind}/{aid}/pick-version` and `POST /campaigns/{cid}/{kind}/{aid}/import-version` (body `{"version": str}`; kind ∉ actor kinds → 404; unknown actor/version → 404; lock-state violation → 409).

- [ ] **Step 1: Write the failing tests**

```python
def _campaign_with_actor(client, monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = client.post("/api/worlds", json={"name": "W"}).json()["id"]
    client.post(f"/api/worlds/{wid}/characters",
                json={"name": "Mara", "version_name": "young"})
    client.post(f"/api/worlds/{wid}/characters/mara/versions",
                json={"name": "veteran", "card": {"spec": "chara_card_v3",
                                                  "spec_version": "3.0",
                                                  "data": {"name": "Mara"}}})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    return wid, cid


def test_campaign_character_read_and_edit(client, monkeypatch, tmp_path):
    wid, cid = _campaign_with_actor(client, monkeypatch, tmp_path)
    chars = client.get(f"/api/campaigns/{cid}/characters").json()
    assert [c["id"] for c in chars] == ["mara"]
    detail = client.get(f"/api/campaigns/{cid}/characters/mara").json()
    assert {v["id"] for v in detail["versions"]} == {"young", "veteran"}
    # campaign card edit leaves the world untouched
    card = {"spec": "chara_card_v3", "spec_version": "3.0", "data": {"name": "C-Mara"}}
    r = client.put(f"/api/campaigns/{cid}/characters/mara/versions/young", json={"card": card})
    assert r.status_code == 200
    world = client.get(f"/api/worlds/{wid}/characters/mara").json()
    young = next(v for v in world["versions"] if v["id"] == "young")
    assert young["card"]["data"]["name"] == "Mara"


def test_campaign_pick_and_import_version(client, monkeypatch, tmp_path):
    wid, cid = _campaign_with_actor(client, monkeypatch, tmp_path)
    r = client.post(f"/api/campaigns/{cid}/characters/mara/pick-version",
                    json={"version": "young"})
    assert r.status_code == 200
    detail = client.get(f"/api/campaigns/{cid}/characters/mara").json()
    assert [v["id"] for v in detail["versions"]] == ["young"]
    # re-pick -> 409; unknown version -> 404
    assert client.post(f"/api/campaigns/{cid}/characters/mara/pick-version",
                       json={"version": "veteran"}).status_code == 409
    assert client.post(f"/api/campaigns/{cid}/characters/mara/pick-version",
                       json={"version": "bogus"}).status_code == 404
    # import replaces the pick
    r = client.post(f"/api/campaigns/{cid}/characters/mara/import-version",
                    json={"version": "veteran"})
    assert r.status_code == 200
    detail = client.get(f"/api/campaigns/{cid}/characters/mara").json()
    assert [v["id"] for v in detail["versions"]] == ["veteran"]
    # locked actor refuses new campaign versions
    assert client.post(f"/api/campaigns/{cid}/characters/mara/versions",
                       json={"name": "extra", "card": {"spec": "chara_card_v3",
                                                       "spec_version": "3.0",
                                                       "data": {"name": "X"}}}).status_code == 409


def test_campaign_import_requires_lock_and_actor_kind(client, monkeypatch, tmp_path):
    wid, cid = _campaign_with_actor(client, monkeypatch, tmp_path)
    assert client.post(f"/api/campaigns/{cid}/characters/mara/import-version",
                       json={"version": "veteran"}).status_code == 409
    assert client.post(f"/api/campaigns/{cid}/locations/somewhere/pick-version",
                       json={"version": "x"}).status_code == 404


def test_campaign_pc_read_and_versions(client, monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = client.post("/api/worlds", json={"name": "W"}).json()["id"]
    client.post(f"/api/worlds/{wid}/pcs", json={"name": "Elara", "tags": []})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    detail = client.get(f"/api/campaigns/{cid}/pcs/elara").json()
    assert detail["meta"]["id"] == "elara"
    r = client.put(f"/api/campaigns/{cid}/pcs/elara", json={"tags": ["anything-goes"]})
    assert r.status_code == 200
    r = client.post(f"/api/campaigns/{cid}/pcs/elara/versions",
                    json={"name": "older", "persona": {"name": "Elara", "pronouns": "",
                                                       "summary": "", "description": "x"}})
    assert r.status_code == 200
```

- [ ] **Step 2: Run to verify failure** (404s from the generic route / missing endpoints)

- [ ] **Step 3: Implement.** Model:

```python
class PickBody(BaseModel):
    version: str
```

Helper + routes (place after `post_campaign_pc`, line ~1511, still before the generic block):

```python
def _campaign_wroot(cid: str):
    return store.worlds.world_root(store.campaigns.read_campaign(cid)["meta"].get("world", ""))


@router.get("/campaigns/{cid}/characters")
def get_campaign_characters(cid: str):
    return store.characters.list_characters(_campaign_root_or_404(cid))


@router.get("/campaigns/{cid}/characters/{char}")
def get_campaign_character(cid: str, char: str):
    try:
        return store.characters.read_character(_campaign_root_or_404(cid), char)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")


@router.put("/campaigns/{cid}/characters/{char}")
def put_campaign_character(cid: str, char: str, body: DefaultVersion):
    try:
        store.characters.set_default_version(_campaign_root_or_404(cid), char, body.default_version)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.post("/campaigns/{cid}/characters/{char}/versions")
def post_campaign_character_version(cid: str, char: str, body: VersionCreate):
    root = _campaign_root_or_404(cid)
    if store.appearances.locked_version(cid, "characters", char) is not None:
        raise HTTPException(status_code=409, detail="character is locked to one version")
    try:
        vid = store.characters.create_version(root, char, body.name, body.card)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"version": vid}


@router.put("/campaigns/{cid}/characters/{char}/versions/{vid}")
def put_campaign_character_version(cid: str, char: str, vid: str, body: VersionUpdate):
    try:
        store.characters.update_version(_campaign_root_or_404(cid), char, vid, body.card)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.delete("/campaigns/{cid}/characters/{char}/versions/{vid}")
def delete_campaign_character_version(cid: str, char: str, vid: str):
    try:
        store.characters.delete_version(_campaign_root_or_404(cid), char, vid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.get("/campaigns/{cid}/pcs/{pid}")
def get_campaign_pc(cid: str, pid: str):
    try:
        return store.pcs.read_pc(_campaign_root_or_404(cid), pid)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")


@router.put("/campaigns/{cid}/pcs/{pid}")
def put_campaign_pc(cid: str, pid: str, body: PCUpdate):
    # Campaign tags are free strings: no world-vocabulary check on this side.
    root = _campaign_root_or_404(cid)
    try:
        if body.tags is not None:
            store.pcs.set_tags(root, pid, body.tags)
        if body.default_version is not None:
            store.pcs.set_default_version(root, pid, body.default_version)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    except store.pcs.PCVersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.post("/campaigns/{cid}/pcs/{pid}/versions")
def post_campaign_pc_version(cid: str, pid: str, body: PersonaVersionCreate):
    root = _campaign_root_or_404(cid)
    if store.appearances.locked_version(cid, "pcs", pid) is not None:
        raise HTTPException(status_code=409, detail="pc is locked to one version")
    try:
        vid = store.pcs.create_version(root, pid, body.name, body.persona)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    return {"version": vid}


@router.put("/campaigns/{cid}/pcs/{pid}/versions/{vid}")
def put_campaign_pc_version(cid: str, pid: str, vid: str, body: PersonaVersionUpdate):
    try:
        store.pcs.update_version(_campaign_root_or_404(cid), pid, vid, body.persona)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    except store.pcs.PCVersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.delete("/campaigns/{cid}/pcs/{pid}/versions/{vid}")
def delete_campaign_pc_version(cid: str, pid: str, vid: str):
    try:
        store.pcs.delete_version(_campaign_root_or_404(cid), pid, vid)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    except store.pcs.PCVersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.post("/campaigns/{cid}/{kind}/{aid}/pick-version")
def post_pick_version(cid: str, kind: str, aid: str, body: PickBody):
    root = _campaign_root_or_404(cid)
    if kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    if store.appearances.actor_hash(root, kind, aid, body.version) is None:
        raise HTTPException(status_code=404, detail="actor or version not found in campaign")
    try:
        store.appearances.pick_version(cid, kind, aid, body.version)
    except store.appearances.AppearError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True}


@router.post("/campaigns/{cid}/{kind}/{aid}/import-version")
def post_import_version(cid: str, kind: str, aid: str, body: PickBody):
    _campaign_root_or_404(cid)
    if kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    if store.appearances.actor_hash(_campaign_wroot(cid), kind, aid, body.version) is None:
        raise HTTPException(status_code=404, detail="actor or version not found in world")
    try:
        store.appearances.import_version(cid, kind, aid, body.version)
    except store.appearances.AppearError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True}
```

Note: `GET /campaigns/{cid}/characters/{char}` must be declared **after** the existing literal `GET /campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}` in source order is fine — FastAPI prefers the more specific path regardless; just keep everything before the generic `/{kind}` block. The `pick-version`/`import-version` routes use a `{kind}` placeholder but their literal tail segments keep them from colliding with the generic image routes.

- [ ] **Step 4: Run** `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(routes): campaign character/PC CRUD, version pick and import"
```

---

### Task 15: full-suite verification

- [ ] **Step 1: Run the entire backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: all tests pass, no warnings introduced.

- [ ] **Step 2: Fix any stragglers** — the most likely failures are older tests that seed the world after campaign creation (reorder them, Task 7 Step 5 pattern) or assert on `played.json`'s legacy list format (use `read_marks`).

- [ ] **Step 3: Commit any test fixes**

```bash
git add backend/tests/
git commit -m "test(backend): align remaining tests with campaign full-copy semantics"
```

---

## Deviations from the spec (intentional)

- No `GET/PUT /campaigns/{cid}/plotmap` routes: the world has no plot-map routes either — plot-map editing happens through greeting `edges` PUTs, which the campaign now mirrors. The plot map still syncs as its own ref.
- `available_greetings` keeps today's behavior of leaving played greetings `available` (they're repeatable openers); `completed` matches that exactly. The `mark` field lets the UI badge both.
