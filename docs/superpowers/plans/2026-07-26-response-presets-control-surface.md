# Response Presets, Part 2: Control Surface — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the length budget and prose style settable from the app — per scope, per preset, and for a single turn.

**Architecture:** Part 1 built the data model and the drift counterweight; the resolution cascade, the four built-in presets, and the budget prompt already work. This part adds the write half: preset CRUD in the store, `/response` scope endpoints that generalize and replace the existing `/style` ones, a `ResponsePresetPicker` reused at all three persistent scopes, a `ResponsePresetsView` management page, and a one-shot override chip in the composer.

**Tech Stack:** FastAPI + pydantic (v1/v2-agnostic), React + TypeScript, vitest, pytest.

**Source spec:** `docs/superpowers/specs/2026-07-26-response-presets-design.md` (stages 3–5 of its Build Order)
**Predecessor:** `docs/superpowers/plans/2026-07-26-response-presets-drift-control.md` (stages 1–2, landed)

## What already exists

Do not rebuild these — read them first:

- `store/lengths.py` — `KNOBS`, `PRESETS` (terse/brisk/standard/cinematic), `get`, `coerce`.
- `store/response_presets.py` — `PresetNotFound`, `list_presets`, `read_preset`, `is_built_in`, `supplies`, `resolve`. **Read-only. There is no create/update/delete yet** — that is Task 4 here.
- `store/length_drift.py`, `templates/scene/sections/response_budget.j2`, `templates/scene/length_correction.j2` — measurement and prompts, complete.
- `resolve(turn=…, scene_meta=…, campaign_meta=…, config=…)` already accepts a **turn** scope; nothing passes one yet (Task 8).
- Scope storage works at all three persistent levels: scene and campaign frontmatter, and `config.md` (its six keys are in `_CONFIG_KEYS`).

## Global Constraints

- **Privacy — invented names only.** Never use a real world/campaign/character name in a fixture, doc, or commit message. Reuse: Seraphine, Mara, Winifred, Realm, Saltmarch.
- **pydantic v1/v2-agnostic.** Plain `BaseModel` fields only — no `model_dump()`, `Field`, validators, `ConfigDict`. Dump via `routes._dump`.
- **Built-in immutability returns 400**, not 409 — matching `PUT /api/styles/{sid}` today.
- **Android-safe paths**: built-ins via `prompts.templates_dir()`, user data via `store.paths.home()`.
- **Frontend list/detail pattern is mandatory** for `ResponsePresetsView` — see CLAUDE.md and `GreetingEditor.tsx` / `EntityEditor.tsx`.
- **Every new store module** must be registered in `store/__init__.py` (import tuple **and** `__all__`).
- **Backend tests:** `backend/.venv/Scripts/python.exe -m pytest backend -q` (~2.5 min; baseline after part 1 is 1664 passed).
- **Frontend tests:** from `frontend/`, `npx vitest run` and `npx tsc -b`. Run **from** `frontend/` — `npx --prefix frontend` skips `vitest.config.ts` and breaks every mock-based test.
- **After any prompt/template change:** `backend/.venv/Scripts/python.exe scripts/verify_templates.py`.

---

## File Structure

**Create:**
- `frontend/src/components/ResponsePresetPicker.tsx` + test — the one picker, used at three scopes.
- `frontend/src/routes/ResponsePresetsView.tsx` + test — the management page.

**Modify:**
- `backend/src/grimoire/store/response_presets.py` — add the write half.
- `backend/src/grimoire/store/campaigns.py`, `scenes.py` — scope setters for the bundle.
- `backend/src/grimoire/routes.py` — preset CRUD, `/usage`, `/response` scope routes, retire `/style`, config payload, chat turn override.
- `frontend/src/api/client.ts` — new calls; drop the `/style` ones.
- `frontend/src/routes/ConfigView.tsx`, `CampaignView.tsx`, `components/SceneInspector.tsx` — swap in the picker; add the composer chip.
- `frontend/src/App.tsx` — route for the management page.

**Delete:**
- `frontend/src/components/StyleConfig.tsx` and its test — absorbed by `ResponsePresetPicker`. (`StyleGuidesView` manages style *records* and is untouched.)

---

## Task 1: Preset write operations

**Files:**
- Modify: `backend/src/grimoire/store/response_presets.py`
- Test: `backend/tests/test_response_presets.py`

**Interfaces:**
- Consumes: `_custom_dir`, `_find_path`, `is_built_in`, `read_preset`, `PresetNotFound`, `supplies` (all exist).
- Produces: `BuiltInPresetImmutable`, `create_preset(name, description="", style_id="", length_preset="", knobs=None) -> str`, `update_preset(pid, *, name=None, description=None, style_id=None, length_preset=None, knobs=None) -> None`, `delete_preset(pid) -> None`, `duplicate_preset(pid) -> str`, `validity(meta) -> dict`.

Mirrors `store/styles.py`'s CRUD exactly, including `uniquify(slugify(name), exists)` for ids.

**`validity` is what makes fail-open behaviour observable.** Resolution deliberately degrades: an unknown `length_preset` makes a record supply nothing, and a malformed knob is treated as absent. That is correct for generation — one corrupt file must not take a scene down — but it means a preset can sit there looking selected while supplying nothing at all, and the user cannot tell that apart from ordinary inheritance. `validity` returns `{"valid": bool, "issues": [str]}` describing exactly what was ignored, and `read_preset` / `list_presets` include it so the management view (Task 7) can surface it. This does not change resolution at all.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_response_presets.py`:

```python
def test_create_read_update_delete_custom(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    pid = rp.create_preset("Slow Burn", "Gothic dread.", style_id="gothic-horror",
                           length_preset="cinematic")
    got = rp.read_preset(pid)["meta"]
    assert got["name"] == "Slow Burn" and got["built_in"] is False
    assert rp.supplies(got)["reply_words"] == 900

    rp.update_preset(pid, length_preset="terse")
    assert rp.supplies(rp.read_preset(pid)["meta"])["reply_words"] == 150

    rp.delete_preset(pid)
    with pytest.raises(rp.PresetNotFound):
        rp.read_preset(pid)


def test_create_with_explicit_knobs(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    pid = rp.create_preset("Clipped", knobs={"reply_words": 220, "speakers": 2})
    supplied = rp.supplies(rp.read_preset(pid)["meta"])
    assert supplied == {"reply_words": 220, "speakers": 2}


def test_create_rejects_both_length_forms(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        rp.create_preset("Both", length_preset="terse", knobs={"reply_words": 220})


def test_update_rejects_both_length_forms(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    pid = rp.create_preset("Named", length_preset="terse")
    with pytest.raises(ValueError):
        rp.update_preset(pid, knobs={"reply_words": 220})


def test_switching_length_form_clears_the_other(tmp_path, monkeypatch):
    """A record must never end up carrying both forms on disk — the tagged
    union is validated on write, so switching form has to erase the old one."""
    _isolate(tmp_path, monkeypatch)
    pid = rp.create_preset("Switcher", length_preset="terse")
    rp.update_preset(pid, length_preset="", knobs={"reply_words": 220})
    meta = rp.read_preset(pid)["meta"]
    assert meta["length_preset"] == ""
    assert rp.supplies(meta) == {"reply_words": 220}


def test_builtins_are_immutable(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "response_presets", "terse",
           name="Terse", length_preset="terse")
    with pytest.raises(rp.BuiltInPresetImmutable):
        rp.update_preset("terse", name="Nope")
    with pytest.raises(rp.BuiltInPresetImmutable):
        rp.delete_preset("terse")


def test_validity_flags_an_unknown_length_preset(tmp_path, monkeypatch):
    """Resolution fails open for these — which is right for generation, and
    exactly why they need to be visible somewhere."""
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "home" / "response_presets", "broken",
           name="Broken", length_preset="nonesuch")
    meta = rp.read_preset("broken")["meta"]
    assert rp.supplies(meta) is None                    # unchanged: still fails open
    v = rp.read_preset("broken")["validity"]
    assert v["valid"] is False
    assert any("nonesuch" in i for i in v["issues"])


def test_validity_flags_malformed_knobs_without_invalidating(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "home" / "response_presets", "sloppy",
           name="Sloppy", reply_words="lots", speakers="3")
    got = rp.read_preset("sloppy")
    assert rp.supplies(got["meta"]) == {"speakers": 3}  # unchanged
    assert got["validity"]["valid"] is True             # usable, just partly ignored
    assert any("reply_words" in i for i in got["validity"]["issues"])


def test_validity_flags_a_dangling_style_reference(tmp_path, monkeypatch):
    """resolve() skips a style that doesn't exist and keeps walking outward, so
    the selection silently does nothing. Degraded, not invalid — the length
    half still applies."""
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "home" / "response_presets", "orphan",
           name="Orphan", style_id="deleted-style", length_preset="terse")
    got = rp.read_preset("orphan")
    assert got["validity"]["valid"] is True
    assert any("deleted-style" in i for i in got["validity"]["issues"])
    assert rp.supplies(got["meta"])["reply_words"] == 150      # unchanged


def test_validity_accepts_the_none_sentinel(tmp_path, monkeypatch):
    """`none` is an explicit clear, not a dangling reference."""
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "home" / "response_presets", "bare",
           name="Bare", style_id="none", length_preset="terse")
    assert rp.read_preset("bare")["validity"]["issues"] == []


def test_a_clean_preset_reports_no_issues(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    pid = rp.create_preset("Fine", length_preset="terse")
    assert rp.read_preset(pid)["validity"] == {"valid": True, "issues": []}


def test_list_presets_carries_validity(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "home" / "response_presets", "broken",
           name="Broken", length_preset="nonesuch")
    row = [p for p in rp.list_presets() if p["id"] == "broken"][0]
    assert row["validity"]["valid"] is False


def test_duplicate_makes_an_editable_copy(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "response_presets", "terse",
           name="Terse", length_preset="terse")
    pid = rp.duplicate_preset("terse")
    assert rp.is_built_in(pid) is False
    assert rp.read_preset(pid)["meta"]["name"] == "Terse (copy)"
    assert rp.supplies(rp.read_preset(pid)["meta"])["reply_words"] == 150
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_response_presets.py -q -k "create or update or delete or duplicate or immutable or switching"`
Expected: FAIL — `AttributeError: module 'grimoire.store.response_presets' has no attribute 'create_preset'`

- [ ] **Step 3: Write the implementation**

Add to `backend/src/grimoire/store/response_presets.py` (imports: add `dump_frontmatter` to the frontmatter import, and `slugify, uniquify` to the paths import):

```python
class BuiltInPresetImmutable(Exception):
    pass


def _length_fields(length_preset: str, knobs: dict | None) -> dict:
    """Frontmatter for the length half, enforcing the tagged union on write.

    Exactly one form is stored. Switching form must ERASE the other, or a record
    ends up carrying both on disk — which read_preset resolves by silently
    preferring length_preset, exactly the ambiguity the union exists to remove.
    """
    if length_preset and knobs:
        raise ValueError("a preset carries either length_preset or explicit knobs, not both")
    out = {"length_preset": length_preset or ""}
    for knob in lengths.KNOBS:
        out[knob] = str((knobs or {}).get(knob, "")) if (knobs or {}).get(knob) else ""
    return out


def create_preset(name: str, description: str = "", style_id: str = "",
                  length_preset: str = "", knobs: dict | None = None) -> str:
    _custom_dir().mkdir(parents=True, exist_ok=True)

    def exists(c: str) -> bool:
        return (_custom_dir() / f"{c}.md").exists() or (_builtin_dir() / f"{c}.md").exists()

    pid = uniquify(slugify(name), exists)
    meta = {"name": name, "description": description, "style_id": style_id,
            **_length_fields(length_preset, knobs)}
    (_custom_dir() / f"{pid}.md").write_text(dump_frontmatter(meta, ""), encoding="utf-8")
    return pid


def update_preset(pid: str, *, name: str | None = None, description: str | None = None,
                  style_id: str | None = None, length_preset: str | None = None,
                  knobs: dict | None = None) -> None:
    if is_built_in(pid):
        raise BuiltInPresetImmutable(pid)
    p = _custom_dir() / f"{pid}.md"
    if not _safe(pid) or not p.exists():
        raise PresetNotFound(pid)
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    if name is not None:
        meta["name"] = name
    if description is not None:
        meta["description"] = description
    if style_id is not None:
        meta["style_id"] = style_id
    if length_preset is not None or knobs is not None:
        meta.update(_length_fields(
            length_preset if length_preset is not None else meta.get("length_preset", ""),
            knobs))
    p.write_text(dump_frontmatter(meta, ""), encoding="utf-8")


def delete_preset(pid: str) -> None:
    if is_built_in(pid):
        raise BuiltInPresetImmutable(pid)
    p = _custom_dir() / f"{pid}.md"
    if not _safe(pid) or not p.exists():
        raise PresetNotFound(pid)
    p.unlink()


def validity(meta: dict) -> dict:
    """What this record's fields were understood to mean — {"valid", "issues"}.

    Reporting only; resolution's fail-open behaviour is unchanged. Without this
    a preset can look selected while supplying nothing, which is
    indistinguishable from ordinary inheritance.
    """
    from . import styles

    issues: list[str] = []
    named = (meta.get("length_preset") or "").strip()
    if named and lengths.get(named) is None:
        return {"valid": False,
                "issues": [f"unknown length preset '{named}' — this preset supplies nothing"]}
    if not named:
        for knob in lengths.KNOBS:
            raw = (meta.get(knob) or "").strip()
            if raw and lengths.coerce(raw) is None:
                issues.append(f"{knob}: '{raw}' is not a positive whole number — ignored")
    # A style id that names nothing is skipped by resolution and the cascade
    # continues outward — correct for generation, invisible to the user. Without
    # this, a preset whose style was deleted reports clean while its style
    # selection quietly does nothing. Degraded, not invalid: the length half
    # still applies.
    style = (meta.get("style_id") or "").strip()
    if style and style != _STYLE_CLEAR and not styles.exists(style):
        issues.append(f"style '{style}' no longer exists — this preset supplies no style")
    # valid=False means "supplies nothing at all" — only an unknown length
    # preset does that (early return above). Everything else is usable with
    # some fields ignored, which is what `issues` describes.
    return {"valid": True, "issues": issues}


def duplicate_preset(pid: str) -> str:
    src = read_preset(pid)["meta"]
    knobs = {k: lengths.coerce(src.get(k, "")) for k in lengths.KNOBS}
    knobs = {k: v for k, v in knobs.items() if v is not None}
    return create_preset(f"{src['name']} (copy)", src.get("description", ""),
                         src.get("style_id", ""), src.get("length_preset", ""),
                         knobs or None)
```

- [ ] **Step 4: Surface validity from the read paths**

`read_preset` returns `{"meta": …, "validity": validity(meta)}`; `_list_dir` adds
`"validity": validity(meta)` to each row. Neither changes `supplies` or `resolve`.

- [ ] **Step 5: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_response_presets.py -q`
Expected: PASS — note the part-1 tests that read `read_preset(pid)["meta"]` keep working unchanged.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/response_presets.py backend/tests/test_response_presets.py
git commit -m "feat(response-presets): write operations and validity reporting"
```

---

## Task 2: Scope setters and the deletion impact preview

**Files:**
- Modify: `backend/src/grimoire/store/scenes.py`, `campaigns.py`, `response_presets.py`
- Test: `backend/tests/test_response_presets.py`, `test_scene_store.py`

**Interfaces:**
- Produces: `scenes.set_response(cid, sid, fields: dict) -> None`, `campaigns.set_campaign_response(cid, fields: dict) -> None`, `response_presets.usage(pid) -> list[dict]`.
- `fields` keys: `response_preset`, `style_id`, `length_reply_words`, `length_blocks`, `length_paragraphs`, `length_speakers`, `length_blocks_per_speaker`. An empty string clears (inherit); an absent key leaves the current value alone.

`usage` is the part that is easy to get wrong. It must report **every scope whose effective bundle changes**, not scopes that name the preset — deleting a campaign-level preset changes all its scenes, and deleting the global default can change everything.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_response_presets.py`:

```python
def _campaign_fixture(tmp_path, monkeypatch):
    from grimoire.store import campaigns, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    return cid, scenes.create_scene(cid, "The Long Dark")


def test_usage_reports_indirectly_affected_scopes(tmp_path, monkeypatch):
    """A campaign-level preset changes every scene that inherits it. Reporting
    only scopes that NAME the preset understates the blast radius."""
    from grimoire.store import campaigns, scenes
    cid, sid = _campaign_fixture(tmp_path, monkeypatch)
    for n in range(3):
        scenes.create_scene(cid, f"Scene {n}")
    pid = rp.create_preset("Slow Burn", length_preset="cinematic")
    campaigns.set_campaign_response(cid, {"response_preset": pid})

    affected = rp.usage(pid)
    kinds = [a["scope"] for a in affected]
    assert "campaign" in kinds
    assert kinds.count("scene") == 4          # every inheriting scene, not zero


def test_usage_shows_post_deletion_values_not_a_blanket_standard(tmp_path, monkeypatch):
    """Deletion RE-RESOLVES; a scene may inherit a campaign preset rather than
    falling back to standard. A false preview before an irreversible delete is
    worse than none."""
    from grimoire.store import campaigns, scenes
    cid, sid = _campaign_fixture(tmp_path, monkeypatch)
    campaigns.set_campaign_response(cid, {"response_preset": "cinematic"})
    pid = rp.create_preset("Scene Only", length_preset="terse")
    scenes.set_response(cid, sid, {"response_preset": pid})

    after = {a["scope"] + ":" + a["id"]: a for a in rp.usage(pid)}
    scene_row = after[f"scene:{sid}"]
    assert scene_row["after"]["reply_words"] == 900        # inherits cinematic
    assert scene_row["before"]["reply_words"] == 150


def test_usage_is_empty_for_an_unused_preset(tmp_path, monkeypatch):
    _campaign_fixture(tmp_path, monkeypatch)
    pid = rp.create_preset("Unused", length_preset="terse")
    assert rp.usage(pid) == []
```

Append to `backend/tests/test_scene_store.py`:

```python
def test_set_response_writes_and_clears_bundle_fields(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Bundle")
    scenes.set_response(cid, sid, {"response_preset": "terse", "length_speakers": "3"})
    meta = scenes.read_scene(cid, sid)["meta"]
    assert meta["response_preset"] == "terse" and meta["length_speakers"] == "3"
    scenes.set_response(cid, sid, {"length_speakers": ""})       # clear one field
    meta = scenes.read_scene(cid, sid)["meta"]
    assert meta["length_speakers"] == ""
    assert meta["response_preset"] == "terse"                    # untouched
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_response_presets.py backend/tests/test_scene_store.py -q -k "usage or set_response"`
Expected: FAIL — `AttributeError: … has no attribute 'usage'` / `'set_response'`

- [ ] **Step 3: Add the scope setters**

In `backend/src/grimoire/store/scenes.py`, replacing `set_response_preset` (which part 1 added as a stopgap — update its two callers in `test_context.py` to `set_response(cid, sid, {"response_preset": …})`):

```python
RESPONSE_FIELDS = ("response_preset", "style_id", "length_reply_words", "length_blocks",
                   "length_paragraphs", "length_speakers", "length_blocks_per_speaker")


def set_response(cid: str, sid: str, fields: dict) -> None:
    """Write scene-scope response settings. An empty value clears the field
    (inherit); a key that is absent from `fields` is left untouched."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    for key in RESPONSE_FIELDS:
        if key in fields:
            meta[key] = str(fields[key] or "")
    p.write_text(dump_frontmatter(meta, body), encoding="utf-8")
```

In `backend/src/grimoire/store/campaigns.py`, beside `set_campaign_style`:

```python
def set_campaign_response(cid: str, fields: dict) -> None:
    """Campaign-scope response settings; same semantics as scenes.set_response."""
    mp = campaign_meta_path(cid)
    if not mp.exists():
        raise CampaignNotFound(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    for key in scenes.RESPONSE_FIELDS:
        if key in fields:
            meta[key] = str(fields[key] or "")
    mp.write_text(dump_frontmatter(meta, body), encoding="utf-8")
```

(`campaigns` already imports lazily where needed; import `scenes` inside the function if a module-level import would cycle.)

- [ ] **Step 4: Add `usage`**

Add to `backend/src/grimoire/store/response_presets.py`:

```python
def usage(pid: str) -> list[dict]:
    """Every scope whose effective bundle would CHANGE if `pid` were deleted.

    Diffs resolutions rather than scanning for references: a campaign-level
    preset changes every scene that inherits it, and the global default can
    change everything. Reporting only scopes that name the preset understates
    the blast radius, and "they fall back to Standard" is false whenever a
    broader scope still supplies something.
    """
    from . import campaigns, config, scenes

    def resolved(scene_meta, campaign_meta, cfg, hide: bool):
        def strip(meta):
            if hide and (meta.get("response_preset") or "") == pid:
                return {**meta, "response_preset": ""}
            return meta
        return resolve(scene_meta=strip(scene_meta), campaign_meta=strip(campaign_meta),
                       config=strip(cfg))

    cfg = config.read_config()
    out: list[dict] = []
    for scope, sid, cid, smeta, cmeta in _all_scopes(cfg):
        before = resolved(smeta, cmeta, cfg, hide=False)
        after = resolved(smeta, cmeta, cfg, hide=True)
        keys = ("style_id",) + lengths.KNOBS
        if any(before[k] != after[k] for k in keys):
            out.append({"scope": scope, "id": sid or cid or "",
                        "name": _scope_label(scope, cid, sid),
                        "before": {k: before[k] for k in keys},
                        "after": {k: after[k] for k in keys}})
    return out
```

with `_all_scopes(cfg)` yielding `("global", "", "", {}, {})`, then one row per campaign, then one per scene of each campaign (using `campaigns.list_campaigns()` / `scenes.list_scenes(cid)` and reading each frontmatter once), and `_scope_label` returning a human name for the dialog. Reading every campaign and scene's frontmatter is already the cost of finding direct references, and this runs only on delete.

- [ ] **Step 5: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_response_presets.py backend/tests/test_scene_store.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/response_presets.py backend/src/grimoire/store/scenes.py backend/src/grimoire/store/campaigns.py backend/tests/
git commit -m "feat(response-presets): scope setters and resolution-diffing usage preview"
```

---

## Task 3: Preset CRUD routes

**Files:**
- Modify: `backend/src/grimoire/routes.py`
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Produces: `GET/POST /api/response-presets`, `GET/PUT/DELETE /api/response-presets/{pid}`, `POST /api/response-presets/{pid}/duplicate`, `GET /api/response-presets/{pid}/usage`, `GET /api/length-presets`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_routes.py`:

```python
def test_response_preset_crud_roundtrip(client):
    r = client.post("/api/response-presets",
                    json={"name": "Slow Burn", "description": "Gothic dread.",
                          "length_preset": "cinematic"})
    assert r.status_code == 200
    pid = r.json()["id"]
    assert client.get(f"/api/response-presets/{pid}").json()["meta"]["name"] == "Slow Burn"
    assert client.put(f"/api/response-presets/{pid}",
                      json={"name": "Slower Burn"}).status_code == 200
    assert client.delete(f"/api/response-presets/{pid}").status_code == 200
    assert client.get(f"/api/response-presets/{pid}").status_code == 404


def test_builtin_preset_edit_and_delete_are_400(client):
    assert client.put("/api/response-presets/terse", json={"name": "No"}).status_code == 400
    assert client.delete("/api/response-presets/terse").status_code == 400


def test_creating_with_both_length_forms_is_400(client):
    r = client.post("/api/response-presets",
                    json={"name": "Both", "length_preset": "terse",
                          "knobs": {"reply_words": 220}})
    assert r.status_code == 400


def test_length_presets_endpoint_exposes_the_numbers(client):
    body = client.get("/api/length-presets").json()
    assert body["terse"]["reply_words"] == 150
    assert body["cinematic"]["blocks_per_speaker"] == 2


def test_duplicate_builtin_yields_an_editable_copy(client):
    pid = client.post("/api/response-presets/terse/duplicate").json()["id"]
    assert client.put(f"/api/response-presets/{pid}",
                      json={"name": "Mine"}).status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k "response_preset or length_presets or duplicate_builtin"`
Expected: FAIL — 404s, routes don't exist

- [ ] **Step 3: Write the routes**

In `backend/src/grimoire/routes.py`, models beside the other `BaseModel`s:

```python
class ResponsePresetCreate(BaseModel):
    name: str
    description: str = ""
    style_id: str = ""
    length_preset: str = ""
    knobs: dict | None = None


class ResponsePresetUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    style_id: str | None = None
    length_preset: str | None = None
    knobs: dict | None = None
```

Routes, mirroring the `/api/styles` block:

```python
# ---- response presets ----
@router.get("/response-presets")
def get_response_presets():
    return store.response_presets.list_presets()


@router.post("/response-presets")
def post_response_preset(body: ResponsePresetCreate):
    try:
        return {"id": store.response_presets.create_preset(
            body.name, body.description, body.style_id, body.length_preset, body.knobs)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/response-presets/{pid}")
def get_response_preset(pid: str):
    try:
        return store.response_presets.read_preset(pid)
    except store.response_presets.PresetNotFound:
        raise HTTPException(status_code=404, detail="response preset not found")


@router.put("/response-presets/{pid}")
def put_response_preset(pid: str, body: ResponsePresetUpdate):
    try:
        store.response_presets.update_preset(
            pid, name=body.name, description=body.description, style_id=body.style_id,
            length_preset=body.length_preset, knobs=body.knobs)
    except store.response_presets.PresetNotFound:
        raise HTTPException(status_code=404, detail="response preset not found")
    except store.response_presets.BuiltInPresetImmutable:
        raise HTTPException(status_code=400,
                            detail="built-in presets can't be edited — duplicate it first")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.delete("/response-presets/{pid}")
def delete_response_preset(pid: str):
    try:
        store.response_presets.delete_preset(pid)
    except store.response_presets.PresetNotFound:
        raise HTTPException(status_code=404, detail="response preset not found")
    except store.response_presets.BuiltInPresetImmutable:
        raise HTTPException(status_code=400, detail="built-in presets can't be deleted")
    return {"ok": True}


@router.post("/response-presets/{pid}/duplicate")
def post_response_preset_duplicate(pid: str):
    try:
        return {"id": store.response_presets.duplicate_preset(pid)}
    except store.response_presets.PresetNotFound:
        raise HTTPException(status_code=404, detail="response preset not found")


@router.get("/response-presets/{pid}/usage")
def get_response_preset_usage(pid: str):
    return {"affected": store.response_presets.usage(pid)}


@router.get("/length-presets")
def get_length_presets():
    return store.lengths.PRESETS
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k "response_preset or length_presets or duplicate_builtin"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(routes): response preset CRUD, duplicate, usage, and length presets"
```

---

## Task 4: Scope endpoints, replacing `/style`

**Files:**
- Modify: `backend/src/grimoire/routes.py`
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Produces: `GET/PUT /api/response` (global), `GET/PUT /api/campaigns/{cid}/response`, `GET/PUT /api/campaigns/{cid}/scenes/{sid}/response` — **all three returning the identical body shape**.
- **Removes:** `GET/PUT /api/campaigns/{cid}/style`, `GET/PUT /api/campaigns/{cid}/scenes/{sid}/style`, and `default_style_id` from `ConfigUpdate` / `_public_config`.

`style_id` becomes one field of the bundle; two endpoints writing one field invites divergence. Safe to remove outright — backend and frontend ship as one artifact (`main.py` serves the built frontend from `dist_dir()`; the APK packages both), so there are no version-skewed clients, and a repo search finds no non-frontend callers.

**The global scope gets a real endpoint, not raw keys on `/api/config`.** Two reasons, and both are the difference between the picker working and the picker lying:

1. The picker's effective-values readout needs server-computed `effective` and `provenance` at *every* scope. If global returned only its own raw keys, the frontend would have to re-implement the cascade in TypeScript to show what actually resolves — a second implementation that would drift from `resolve()` on exactly the cases that took four review rounds to get right: invalid presets, unknown style ids, the `none` sentinel, and the `standard` fallback.
2. The global style key is spelled **`default_style_id`**, not `style_id`. `_response_body` normalizes it so all three scopes hand the picker one spelling; without that the component needs a per-scope special case.

`default_style_id` keeps its spelling **on disk** (renaming it would break every existing install's global style) and is normalized only in the API layer.

- [ ] **Step 1: Write the failing test**

```python
def test_scene_response_roundtrip(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    assert client.put(f"/api/campaigns/{cid}/scenes/{sid}/response",
                      json={"response_preset": "terse",
                            "length_speakers": "3"}).status_code == 200
    body = client.get(f"/api/campaigns/{cid}/scenes/{sid}/response").json()
    assert body["response_preset"] == "terse"
    assert body["length_speakers"] == "3"
    assert body["effective"]["reply_words"] == 150
    assert body["effective"]["speakers"] == 3
    assert body["provenance"]["speakers"]["scope"] == "scene"


def test_campaign_response_roundtrip(client):
    _wid, cid = _campaign(client)
    assert client.put(f"/api/campaigns/{cid}/response",
                      json={"response_preset": "cinematic"}).status_code == 200
    assert client.get(f"/api/campaigns/{cid}/response").json()["effective"]["reply_words"] == 900


def test_global_response_roundtrip_has_the_same_shape(client):
    """The picker needs identical effective/provenance at every scope, or the
    frontend has to re-implement the cascade for global alone."""
    assert client.put("/api/response", json={"response_preset": "brisk"}).status_code == 200
    body = client.get("/api/response").json()
    assert body["response_preset"] == "brisk"
    assert body["effective"]["reply_words"] == 300
    assert body["provenance"]["reply_words"]["scope"] == "global"
    assert set(body) == {*store.scenes.RESPONSE_FIELDS, "effective", "provenance"}


def test_global_style_is_normalized_to_style_id(client):
    """Stored as default_style_id (renaming it would break existing installs),
    exposed as style_id so the picker has one spelling everywhere."""
    client.post("/api/styles", json={"name": "Gothic Horror", "description": "",
                                     "tags": [], "body": "Atmosphere first."})
    sid_style = client.get("/api/styles").json()[0]["id"]
    assert client.put("/api/response", json={"style_id": sid_style}).status_code == 200
    assert client.get("/api/response").json()["style_id"] == sid_style
    assert store.read_config()["default_style_id"] == sid_style      # on disk


def test_global_invalid_preset_still_reports_a_usable_effective(client):
    assert client.put("/api/response", json={"response_preset": "ghost"}).status_code == 200
    body = client.get("/api/response").json()
    assert body["effective"]["reply_words"] == 550        # falls through to standard
    assert body["provenance"]["reply_words"]["scope"] == "default"


def test_old_style_endpoints_and_config_key_are_gone(client):
    _wid, cid = _campaign(client)
    assert client.get(f"/api/campaigns/{cid}/style").status_code == 404
    assert "default_style_id" not in client.get("/api/config").json()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k "response_roundtrip or config_payload or style_endpoints_are_gone"`
Expected: FAIL

- [ ] **Step 3: Write the routes**

Add a `ResponseSettings` model with the six optional string fields, then one body
builder used by all three scopes:

```python
def _response_body(scene_meta: dict, campaign_meta: dict, cfg: dict, own: dict) -> dict:
    """The shape every scope returns. `own` is that scope's raw frontmatter."""
    resolved = store.response_presets.resolve(
        scene_meta=scene_meta, campaign_meta=campaign_meta, config=cfg)
    fields = {k: own.get(k, "") for k in store.scenes.RESPONSE_FIELDS}
    # Global stores the style as default_style_id; normalize so the picker sees
    # one spelling at every scope. The on-disk key is deliberately unchanged.
    if not fields["style_id"]:
        fields["style_id"] = own.get("default_style_id", "")
    return {**fields,
            "effective": {k: resolved[k] for k in ("style_id",) + store.lengths.KNOBS},
            "provenance": resolved["provenance"]}


def _write_response(setter, fields: dict, style_key: str = "style_id") -> None:
    """Map the picker's style_id back onto the scope's own spelling."""
    out = dict(fields)
    if style_key != "style_id" and "style_id" in out:
        out[style_key] = out.pop("style_id")
    setter(out)
```

Global routes:

```python
@router.get("/response")
def get_global_response():
    cfg = store.read_config()
    return _response_body({}, {}, cfg, cfg)


@router.put("/response")
def put_global_response(body: ResponseSettings):
    fields = {k: v for k, v in _dump(body).items() if v is not None}
    _write_response(lambda f: store.write_config(**f), fields,
                    style_key="default_style_id")
    return {"ok": True}
```

Campaign and scene GET/PUT use the same builder (campaign GET passes
`scene_meta={}`). Delete the four `/style` routes, the `StyleSelect` model if
nothing else uses it, and `default_style_id` from `ConfigUpdate` and
`_public_config` — the global bundle endpoint owns that field now, and leaving a
second writer is the divergence this task exists to remove.

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q`
Expected: PASS — existing `/style` route tests will fail until deleted; remove them, since the behavior moved rather than regressed.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(routes): /response scope endpoints replacing /style"
```

---

## Task 5: `ResponsePresetPicker`

**Files:**
- Create: `frontend/src/components/ResponsePresetPicker.tsx`, `ResponsePresetPicker.test.tsx`
- Modify: `frontend/src/api/client.ts`
- Delete: `frontend/src/components/StyleConfig.tsx`, `StyleConfig.test.tsx`

**Interfaces:**
- Produces: `<ResponsePresetPicker scope="global" | "campaign" | "scene" cid?: string sid?: string />`.
- API client: `listResponsePresets`, `getResponsePreset`, `createResponsePreset`, `updateResponsePreset`, `deleteResponsePreset`, `duplicateResponsePreset`, `responsePresetUsage`, `listLengthPresets`, **`getGlobalResponse`, `setGlobalResponse`** (→ `/api/response`), `getCampaignResponse`, `setCampaignResponse`, `getSceneResponse`, `setSceneResponse`. Remove `getCampaignStyle`, `setCampaignStyle`, `getSceneStyle`, `setSceneStyle`.

**All three scopes need a client pair.** `scope="global"` must route to `getGlobalResponse`/`setGlobalResponse`; without them the ConfigView-mounted picker has no read/write path at all, and the obvious wrong fix is to reach for the `/api/config` writer that Task 4 removes.

Contains: a preset `<select>`; an **Overrides** `<details>` holding the style picker and five numeric inputs; and an effective-values readout naming the scope each value came from. Unset override inputs show the inherited value as `placeholder`, so an empty box reads as "inheriting 300" rather than blank.

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ResponsePresetPicker } from "./ResponsePresetPicker";
import { api } from "../api/client";

vi.mock("../api/client");

const PRESETS = [{ id: "terse", name: "Terse", built_in: true },
                 { id: "cinematic", name: "Cinematic", built_in: true }];

beforeEach(() => {
  (api.listResponsePresets as any).mockResolvedValue(PRESETS);
  (api.getSceneResponse as any).mockResolvedValue({
    response_preset: "terse", style_id: "", length_reply_words: "",
    length_blocks: "", length_paragraphs: "", length_speakers: "3",
    length_blocks_per_speaker: "",
    effective: { style_id: "", reply_words: 150, blocks: 3, paragraphs: 1,
                 speakers: 3, blocks_per_speaker: 1 },
    provenance: { reply_words: { scope: "scene" }, speakers: { scope: "scene" } },
  });
  (api.setSceneResponse as any).mockResolvedValue({ ok: true });
});

it("shows the resolved preset and effective values", async () => {
  render(<ResponsePresetPicker scope="scene" cid="run" sid="s1" />);
  expect(await screen.findByLabelText("Response preset")).toHaveValue("terse");
  expect(await screen.findByText(/150 words/)).toBeInTheDocument();
});

it("shows an inherited value as a placeholder, not a value", async () => {
  render(<ResponsePresetPicker scope="scene" cid="run" sid="s1" />);
  await userEvent.click(await screen.findByText("Overrides"));
  const words = screen.getByLabelText("Target words per reply");
  expect(words).toHaveValue(null);
  expect(words).toHaveAttribute("placeholder", "150");
});

it("saves a preset change", async () => {
  render(<ResponsePresetPicker scope="scene" cid="run" sid="s1" />);
  await userEvent.selectOptions(await screen.findByLabelText("Response preset"), "cinematic");
  await waitFor(() => expect(api.setSceneResponse).toHaveBeenCalledWith(
    "run", "s1", expect.objectContaining({ response_preset: "cinematic" })));
});

it("uses the global endpoints when scope is global", async () => {
  (api.getGlobalResponse as any).mockResolvedValue({
    response_preset: "brisk", style_id: "gothic-horror",
    length_reply_words: "", length_blocks: "", length_paragraphs: "",
    length_speakers: "", length_blocks_per_speaker: "",
    effective: { style_id: "gothic-horror", reply_words: 300, blocks: 4,
                 paragraphs: 2, speakers: 3, blocks_per_speaker: 1 },
    provenance: { reply_words: { scope: "global" } },
  });
  render(<ResponsePresetPicker scope="global" />);
  expect(await screen.findByLabelText("Response preset")).toHaveValue("brisk");
  expect(api.getSceneResponse).not.toHaveBeenCalled();
  await userEvent.selectOptions(screen.getByLabelText("Response preset"), "cinematic");
  await waitFor(() => expect(api.setGlobalResponse).toHaveBeenCalledWith(
    expect.objectContaining({ response_preset: "cinematic" })));
});

it("saves a single knob override without leaving the preset", async () => {
  render(<ResponsePresetPicker scope="scene" cid="run" sid="s1" />);
  await userEvent.click(await screen.findByText("Overrides"));
  await userEvent.type(screen.getByLabelText("Max speaking characters"), "2");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setSceneResponse).toHaveBeenCalledWith(
    "run", "s1", expect.objectContaining({ response_preset: "terse",
                                           length_speakers: "2" })));
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run src/components/ResponsePresetPicker.test.tsx`
Expected: FAIL — module not found

- [ ] **Step 3: Write the component and API client changes**

Build it to satisfy the tests above: load presets and the scope's settings on mount; `scope` selects which api pair to call. Labels must match the tests exactly (`Response preset`, `Target words per reply`, `Max blocks per reply`, `Max paragraphs per block`, `Max speaking characters`, `Max blocks per character`). Effective values render as a short readout with provenance (e.g. `150 words per reply — from this scene`).

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/components/ResponsePresetPicker.test.tsx` then `npx tsc -b`
Expected: PASS, no type errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ResponsePresetPicker.tsx frontend/src/components/ResponsePresetPicker.test.tsx frontend/src/api/client.ts
git rm frontend/src/components/StyleConfig.tsx frontend/src/components/StyleConfig.test.tsx
git commit -m "feat(frontend): ResponsePresetPicker replacing StyleConfig"
```

---

## Task 6: Mount the picker at all three scopes

**Files:**
- Modify: `frontend/src/routes/ConfigView.tsx`, `frontend/src/routes/CampaignView.tsx`, `frontend/src/components/SceneInspector.tsx`
- Test: their existing test files

**Interfaces:** consumes `ResponsePresetPicker` (Task 5).

- **ConfigView** — replace the global prose-style `<select>` with `<ResponsePresetPicker scope="global" />`.
- **CampaignView** — replace `<StyleConfig cid={cid} />` (line ~602) with `<ResponsePresetPicker scope="campaign" cid={cid} />`.
- **SceneInspector** — replace the scene style `<select>` and `chooseStyle` with `<ResponsePresetPicker scope="scene" cid={cid} sid={sid} />`.

- [ ] **Step 1: Update the existing tests**

`CampaignView.test.tsx` mocks `StyleConfig` (line ~24) — change that mock to `ResponsePresetPicker`. `SceneInspector.test.tsx` and `ConfigView.test.tsx` assert on the style `<select>`; replace those assertions with a mocked picker rendering a testid, since the picker has its own test file.

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/routes/CampaignView.test.tsx src/routes/ConfigView.test.tsx src/components/SceneInspector.test.tsx`
Expected: FAIL

- [ ] **Step 3: Swap the components in**

- [ ] **Step 4: Run the full frontend suite**

Run (from `frontend/`): `npx vitest run` then `npx tsc -b`
Expected: PASS, no type errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): mount the response preset picker at all three scopes"
```

---

## Task 7: `ResponsePresetsView` and Save as preset…

**Files:**
- Create: `frontend/src/routes/ResponsePresetsView.tsx`, `ResponsePresetsView.test.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/components/ResponsePresetPicker.tsx`

**Interfaces:** consumes the CRUD API client calls (Task 5).

Built on the CLAUDE.md list/detail pattern — `.editor` containing `.editor-list` (a `+ New preset` button plus one `.row` per preset) and `.editor-body` showing a read-only `.detail-view` by default with an explicit **Edit** step. Built-ins show **Duplicate** instead of **Edit**.

The detail view renders `validity.issues` when non-empty. This is the only place a hand-corrupted preset ever becomes visible: generation deliberately fails open, so without it a preset can read as selected while supplying nothing, and the user has no way to tell that from ordinary inheritance. A record with `valid: false` reads as broken; one with issues but `valid: true` reads as usable-with-ignored-fields.

- [ ] **Step 1: Write the failing test**

```tsx
it("clicking a row shows the read-only view", async () => {
  render(<ResponsePresetsView />);
  await userEvent.click(await screen.findByRole("button", { name: "Slow Burn" }));
  expect(await screen.findByRole("heading", { name: "Slow Burn" })).toBeInTheDocument();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
});

it("Edit reveals the form", async () => {
  render(<ResponsePresetsView />);
  await userEvent.click(await screen.findByRole("button", { name: "Slow Burn" }));
  await userEvent.click(screen.getByRole("button", { name: "Edit" }));
  expect(screen.getByLabelText("Name")).toBeInTheDocument();
});

it("+ New preset opens the form directly", async () => {
  render(<ResponsePresetsView />);
  await userEvent.click(await screen.findByRole("button", { name: "+ New preset" }));
  expect(screen.getByLabelText("Name")).toBeInTheDocument();
});

it("a built-in offers Duplicate instead of Edit", async () => {
  render(<ResponsePresetsView />);
  await userEvent.click(await screen.findByRole("button", { name: "Terse" }));
  expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Duplicate" })).toBeInTheDocument();
});

it("flags a broken preset in the detail view", async () => {
  (api.getResponsePreset as any).mockResolvedValue({
    meta: { id: "broken", name: "Broken", built_in: false },
    validity: { valid: false, issues: ["unknown length preset 'nonesuch' — this preset supplies nothing"] },
  });
  render(<ResponsePresetsView />);
  await userEvent.click(await screen.findByRole("button", { name: "Broken" }));
  expect(await screen.findByText(/supplies nothing/)).toBeInTheDocument();
});

it("flags an ignored malformed knob without calling the preset broken", async () => {
  (api.getResponsePreset as any).mockResolvedValue({
    meta: { id: "sloppy", name: "Sloppy", built_in: false },
    validity: { valid: true, issues: ["reply_words: 'lots' is not a positive whole number — ignored"] },
  });
  render(<ResponsePresetsView />);
  await userEvent.click(await screen.findByRole("button", { name: "Sloppy" }));
  expect(await screen.findByText(/ignored/)).toBeInTheDocument();
});

it("delete confirmation lists affected scopes and their post-deletion values", async () => {
  (api.responsePresetUsage as any).mockResolvedValue({ affected: [
    { scope: "campaign", id: "saltmarch", name: "Saltmarch",
      before: { reply_words: 900 }, after: { reply_words: 550 } }] });
  render(<ResponsePresetsView />);
  await userEvent.click(await screen.findByRole("button", { name: "Slow Burn" }));
  await userEvent.click(screen.getByRole("button", { name: "Edit" }));
  await userEvent.click(screen.getByRole("button", { name: "Delete" }));
  expect(await screen.findByText(/Saltmarch/)).toBeInTheDocument();
  expect(screen.getByText(/550/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run src/routes/ResponsePresetsView.test.tsx`
Expected: FAIL — module not found

- [ ] **Step 3: Write the view, the route, and Save as preset…**

Add the route in `App.tsx` beside `StyleGuidesView`'s. Add a **Save as preset…** button inside the picker's Overrides block that calls `createResponsePreset` with the currently-resolved values and then re-selects the new preset at that scope.

- [ ] **Step 4: Run the full frontend suite**

Run (from `frontend/`): `npx vitest run` then `npx tsc -b`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): response preset management view and save-as-preset"
```

---

## Task 8: Per-turn override chip

**Files:**
- Modify: `backend/src/grimoire/routes.py`, `backend/src/grimoire/store/context.py`, `frontend/src/api/client.ts`, `frontend/src/routes/CampaignView.tsx`
- Test: `backend/tests/test_routes.py`, `test_context.py`, `frontend/src/routes/CampaignView.test.tsx`

**`client.ts` is in scope and its three generation calls all need the override.** Today `chat` sends only content, `retry` sends no body, and `regenerate` sends only guidance — none can carry a response override. Add an optional typed argument to all three. A frontend test that only asserts chip *text* will pass while the override is silently dropped from the request, so the tests below assert **request payloads**.

**Interfaces:**
- `ChatTurn` gains `response: dict | None = None` — a scope-shaped dict, unpersisted, exactly like the director note.
- **`RegenerateBody` gains the same field, and `post_retry` gains a `RetryBody | None = None` body** — it currently takes no body at all.
- `context.build_messages(cid, sid, turn=None)` and `build_director_messages(cid, sid, note, turn=None)` thread it into `_assemble` → `resolve(turn=…)`.

`resolve` already accepts `turn`; nothing passes one yet.

**All three generation entry points must carry it, not just chat.** The chip promises "this applies to the next reply", and the UI holds the selection until a reply actually lands. But if streaming fails after the user message is persisted, the user's next action is **retry** — which today takes no body, so the retry would generate with inherited settings while the chip still displays the override. The promise breaks precisely when a dependency fails, which is when surprising behaviour is least welcome. Same for regenerate.

Note the route variables: `post_chat` has `turn: ChatTurn`, `post_regenerate` has `body: RegenerateBody | None`, and `post_retry` will have `body: RetryBody | None`. Read the field off the right one in each — there is no `turn` variable in the regenerate or retry routes.

- [ ] **Step 1: Write the failing test**

```python
def test_turn_override_beats_the_scene_setting(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.set_response(cid, sid, {"response_preset": "cinematic"})
    text = context.build_messages(cid, sid, turn={"response_preset": "terse"})[0]["content"]
    assert "150 words" in text


def test_turn_override_is_not_persisted(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.set_response(cid, sid, {"response_preset": "cinematic"})
    context.build_messages(cid, sid, turn={"response_preset": "terse"})
    assert scenes.read_scene(cid, sid)["meta"]["response_preset"] == "cinematic"
    assert "900 words" in context.build_messages(cid, sid)[0]["content"]
```

And in `backend/tests/test_routes.py`, the failure path the chip's promise depends on:

```python
def test_retry_carries_a_pending_one_shot_override(client):
    """If streaming fails, the user's next action is retry — and the chip still
    shows the override, so retry must honour it rather than silently reverting
    to inherited settings."""
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.put(f"/api/campaigns/{cid}/scenes/{sid}/response",
               json={"response_preset": "cinematic"})
    store.scenes.append_message(cid, sid, "user", "Go on.")
    captured = {}
    _capture_prompt(client, captured)          # existing helper pattern in this file
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/retry",
                json={"response": {"response_preset": "terse"}})
    assert "150 words" in captured["system"]


def test_regenerate_carries_a_pending_one_shot_override(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.put(f"/api/campaigns/{cid}/scenes/{sid}/response",
               json={"response_preset": "cinematic"})
    store.scenes.append_message(cid, sid, "user", "Go on.")
    store.scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "Too long."}])
    captured = {}
    _capture_prompt(client, captured)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/regenerate",
                json={"response": {"response_preset": "terse"}})
    assert "150 words" in captured["system"]


def test_retry_without_an_override_uses_inherited_settings(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.put(f"/api/campaigns/{cid}/scenes/{sid}/response",
               json={"response_preset": "cinematic"})
    store.scenes.append_message(cid, sid, "user", "Go on.")
    captured = {}
    _capture_prompt(client, captured)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/retry")
    assert "900 words" in captured["system"]
```

(`_capture_prompt` stands for whatever this file already uses to inspect the
messages handed to the mocked LLM — reuse it rather than inventing a new hook.)

Frontend:

```tsx
it("the length chip shows the resolved preset and reverts after send", async () => {
  render(<CampaignView />);
  const chip = await screen.findByRole("button", { name: /Response length/ });
  expect(chip).toHaveTextContent("Cinematic");
  await userEvent.click(chip);
  await userEvent.click(screen.getByRole("option", { name: "Terse" }));
  expect(chip).toHaveTextContent("Terse");
  await userEvent.click(screen.getByRole("button", { name: /Send/ }));
  await waitFor(() => expect(chip).toHaveTextContent("Cinematic"));
});

it("sends the one-shot override in the chat request payload", async () => {
  render(<CampaignView />);
  await userEvent.click(await screen.findByRole("button", { name: /Response length/ }));
  await userEvent.click(screen.getByRole("option", { name: "Terse" }));
  await userEvent.type(screen.getByRole("textbox"), "Go on.");
  await userEvent.click(screen.getByRole("button", { name: /Send/ }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith(
    "run", "s1", "Go on.", expect.objectContaining({ response_preset: "terse" })));
});

it("a failed stream keeps the override, and retry carries it", async () => {
  (api.chat as any).mockRejectedValueOnce(new Error("stream failed"));
  render(<CampaignView />);
  const chip = await screen.findByRole("button", { name: /Response length/ });
  await userEvent.click(chip);
  await userEvent.click(screen.getByRole("option", { name: "Terse" }));
  await userEvent.click(screen.getByRole("button", { name: /Send/ }));
  await waitFor(() => expect(chip).toHaveTextContent("Terse"));   // NOT cleared
  await userEvent.click(screen.getByRole("button", { name: /Retry/ }));
  await waitFor(() => expect(api.retry).toHaveBeenCalledWith(
    "run", "s1", expect.objectContaining({ response_preset: "terse" })));
});

it("regenerate carries a pending override", async () => {
  render(<CampaignView />);
  await userEvent.click(await screen.findByRole("button", { name: /Response length/ }));
  await userEvent.click(screen.getByRole("option", { name: "Terse" }));
  await userEvent.click(screen.getByRole("button", { name: "Reroll" }));
  await waitFor(() => expect(api.regenerate).toHaveBeenCalledWith(
    "run", "s1", expect.anything(),
    expect.objectContaining({ response_preset: "terse" })));
});
```

(Match the existing `api.chat` / `api.retry` / `api.regenerate` signatures in `client.ts` when placing the new argument — the assertions above show it last.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q -k turn_override`
Expected: FAIL — `build_messages() got an unexpected keyword argument 'turn'`

- [ ] **Step 3: Thread the turn scope through**

Add `turn: dict | None = None` to `_assemble`, `build_messages`, `build_director_messages`, and `_system_text`'s caller; pass it to `resolve(turn=turn or {}, …)`.

Add `response: dict | None = None` to `ChatTurn` and `RegenerateBody`, and a new `RetryBody` with the same field (`post_retry` currently declares no body — give it `body: RetryBody | None = None`). Each route reads the field off **its own** request object: `turn.response` in `post_chat`, `body.response if body else None` in `post_regenerate` and `post_retry`.

In `CampaignView`, add the chip beside Send, hold the one-shot value in state, send it with the chat request **and with retry/regenerate while it is still pending**, and clear it only once a reply lands.

- [ ] **Step 4: Run both suites**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`, then from `frontend/`: `npx vitest run` and `npx tsc -b`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend frontend
git commit -m "feat(chat): one-shot per-turn response length override"
```

---

## Verification

- [ ] `backend/.venv/Scripts/python.exe -m pytest backend -q` — full suite passes
- [ ] From `frontend/`: `npx vitest run` and `npx tsc -b` — clean
- [ ] `backend/.venv/Scripts/python.exe scripts/verify_templates.py` — 54/54
- [ ] Manual, via the `verify` skill: set a global default in Configuration, override it on a campaign, override one knob on a scene, and confirm the scene inspector's effective readout names the right scope for each value
- [ ] Manual: delete a preset used by a campaign and confirm the dialog lists the affected scenes with their post-deletion values, not a blanket "Standard"

## Review gates

Per CLAUDE.md, before considering this done: `/codex:adversarial-review` against this plan before starting, `/codex:review` against the diff when implementation is complete, and a final `/codex:adversarial-review` against the diff *and* the spec together.

## Notes for the implementer

- **Tune the numbers first if you can.** `lengths.PRESETS` values are untuned guesses. Playing a few real scenes and adjusting them is cheaper before this UI exists than after, and it does not block any task here.
- **The cascade is already tested at the unit level** — but part 1 shipped a bug where `config.read_config()` silently dropped the new keys, which every `resolve()` test missed because they pass a config dict directly. When adding scope endpoints, prefer tests that go through the real read/write path rather than constructing dicts.
