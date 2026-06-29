# Character Brief Tiers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the narrator tiered awareness of the off-scene cast — a one-paragraph brief for campaign-active-but-absent characters and a one-sentence tagline for every other world character — backed by an auto-derived, human-editable `brief.md` per character.

**Architecture:** A new `store/briefs.py` owns the `brief.md` artifact (pure file IO + staleness + summarizer prompt/parse — no network). The LLM call lives in a route (`POST .../brief`) that calls the existing `OpenRouterClient`. `context.build_messages` gains a "cast directory" block assembled from three tiers. `appearances._copy_actor` copies `brief.md` into the campaign on first appearance, so tier-2 reads the campaign snapshot and tier-3 reads the world.

**Tech Stack:** Python 3.14, FastAPI, pytest. Store is markdown/JSON files under `~/.grimoire` (env `GRIMOIRE_HOME`). Frontmatter via `store/frontmatter.py` (string scalars + body). LLM via `OpenRouterClient` (`openrouter.py`).

## Global Constraints

- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`. Verbatim.
- Run backend tests: `backend/.venv/Scripts/python.exe -m pytest backend -q`.
- Frontmatter values are **single-line string scalars only** (`store/frontmatter.py`); the paragraph lives in the file **body**, never in a frontmatter value.
- Staleness mechanism mirrors `appearances.json`'s sync `base`: `base` = hash of the default-version card the brief was derived from.
- Context builds **never** trigger an LLM call. Derivation is explicit (the `POST .../brief` route only).
- Characters are directories: `<root>/characters/<cid>/character.md` (frontmatter `name`, `default_version`) + `<vid>.json` cards. The brief is `<root>/characters/<cid>/brief.md`.
- Hashes use `characters.card_hash(root, cid, vid)` (sha256 of the card file text), already defined.
- Frontend editing of briefs is **out of scope** — it belongs to the upcoming campaign-tab redesign (see spec follow-up #3). This plan is backend only.

---

### Task 1: `briefs` store module

**Files:**
- Create: `backend/src/grimoire/store/briefs.py`
- Modify: `backend/src/grimoire/store/__init__.py:5-9` (add `briefs` to the `from . import (...)` tuple)
- Test: `backend/tests/test_briefs_store.py`

**Interfaces:**
- Consumes: `characters.read_character`, `characters.card_hash`, `characters.CharacterNotFound`; `frontmatter.dump_frontmatter`, `frontmatter.parse_frontmatter`.
- Produces:
  - `brief_path(root: Path, cid: str) -> Path`
  - `default_card_hash(root: Path, cid: str) -> str | None`
  - `read_brief(root: Path, cid: str) -> dict | None` → `{"tagline": str, "base": str, "body": str}` or `None`
  - `write_brief(root: Path, cid: str, tagline: str, body: str, base: str) -> None`
  - `is_stale(root: Path, cid: str) -> bool`
  - `build_prompt(card_data: dict) -> list[dict]`
  - `parse_output(text: str) -> tuple[str, str]` → `(tagline, body)`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_briefs_store.py`:

```python
from grimoire.store import briefs, characters, worlds


def _world_with_char(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    root = worlds.world_root(wid)
    card = characters.blank_card("Aese")
    card["data"]["description"] = "a snowleopardgirl"
    characters.create_character(root, "Aese", "main", card)
    return root


def test_read_missing_is_none(monkeypatch, tmp_path):
    root = _world_with_char(monkeypatch, tmp_path)
    assert briefs.read_brief(root, "aese") is None


def test_write_then_read_roundtrip(monkeypatch, tmp_path):
    root = _world_with_char(monkeypatch, tmp_path)
    briefs.write_brief(root, "aese", "A silent snowleopardgirl.", "She keeps house.\nShe is shy.", "h0")
    b = briefs.read_brief(root, "aese")
    assert b == {"tagline": "A silent snowleopardgirl.", "base": "h0",
                 "body": "She keeps house.\nShe is shy."}


def test_missing_brief_is_stale(monkeypatch, tmp_path):
    root = _world_with_char(monkeypatch, tmp_path)
    assert briefs.is_stale(root, "aese") is True


def test_base_match_is_fresh(monkeypatch, tmp_path):
    root = _world_with_char(monkeypatch, tmp_path)
    h = briefs.default_card_hash(root, "aese")
    briefs.write_brief(root, "aese", "t", "body", h)
    assert briefs.is_stale(root, "aese") is False


def test_base_mismatch_is_stale(monkeypatch, tmp_path):
    root = _world_with_char(monkeypatch, tmp_path)
    briefs.write_brief(root, "aese", "t", "body", "stale-hash")
    assert briefs.is_stale(root, "aese") is True


def test_default_card_hash_unknown_char_is_none(monkeypatch, tmp_path):
    root = _world_with_char(monkeypatch, tmp_path)
    assert briefs.default_card_hash(root, "nobody") is None


def test_build_prompt_includes_card_fields(monkeypatch, tmp_path):
    msgs = briefs.build_prompt({"name": "Aese", "description": "a snowleopardgirl",
                                "personality": "shy", "scenario": ""})
    assert msgs[0]["role"] == "system"
    assert "Aese" in msgs[1]["content"] and "snowleopardgirl" in msgs[1]["content"]
    assert "shy" in msgs[1]["content"]


def test_parse_output_splits_tagline_and_body():
    tagline, body = briefs.parse_output("A silent snowleopardgirl.\n\nShe keeps house. She is shy.")
    assert tagline == "A silent snowleopardgirl."
    assert body == "She keeps house. She is shy."


def test_parse_output_skips_leading_blank_lines():
    tagline, body = briefs.parse_output("\n\nTagline here.\nParagraph here.")
    assert tagline == "Tagline here."
    assert body == "Paragraph here."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_briefs_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'grimoire.store.briefs'` (or `AttributeError`).

- [ ] **Step 3: Create the module**

Create `backend/src/grimoire/store/briefs.py`:

```python
"""Per-character 'brief': a one-line tagline + a one-paragraph summary derived from
the character's default-version card. Staleness is tracked by `base` — the hash of
the default card the brief was derived from (mirrors appearances' sync base).

Stored at <root>/characters/<cid>/brief.md:
  ---
  tagline: A silent snowleopardgirl who speaks through written notes.
  base: <sha256 of the default-version card>
  ---
  <paragraph body>

Pure file IO + prompt/parse helpers only — the LLM call lives in the route layer.
"""

from __future__ import annotations

from pathlib import Path

from . import characters
from .frontmatter import dump_frontmatter, parse_frontmatter

SUMMARY_INSTRUCTION = (
    "Summarize the following character for a game master's quick reference. "
    "Reply with exactly two parts: the first line is a single-sentence tagline; "
    "then a blank line; then one short paragraph of 3-4 sentences. "
    "Write in third person, present tense. Do not add headings, labels, or quotes."
)


def brief_path(root: Path, cid: str) -> Path:
    return root / "characters" / cid / "brief.md"


def default_card_hash(root: Path, cid: str) -> str | None:
    """Hash of the character's default-version card, or None if the character is absent."""
    try:
        meta = characters.read_character(root, cid)["meta"]
    except characters.CharacterNotFound:
        return None
    return characters.card_hash(root, cid, meta["default_version"])


def read_brief(root: Path, cid: str) -> dict | None:
    p = brief_path(root, cid)
    if not p.exists():
        return None
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    return {"tagline": meta.get("tagline", ""), "base": meta.get("base", ""), "body": body.strip()}


def write_brief(root: Path, cid: str, tagline: str, body: str, base: str) -> None:
    p = brief_path(root, cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dump_frontmatter({"tagline": tagline, "base": base}, body.strip() + "\n"),
                 encoding="utf-8")


def is_stale(root: Path, cid: str) -> bool:
    """Stale when missing, or when its base != the current default-card hash."""
    b = read_brief(root, cid)
    if b is None:
        return True
    return b["base"] != (default_card_hash(root, cid) or "")


def build_prompt(card_data: dict) -> list[dict]:
    fields = [card_data.get(f, "") for f in ("name", "description", "personality", "scenario")]
    card_text = "\n".join(x for x in fields if x)
    return [{"role": "system", "content": SUMMARY_INSTRUCTION},
            {"role": "user", "content": card_text}]


def parse_output(text: str) -> tuple[str, str]:
    """First non-empty line -> tagline; everything after it -> paragraph body."""
    lines = text.strip().split("\n")
    for i, ln in enumerate(lines):
        if ln.strip():
            return ln.strip(), "\n".join(lines[i + 1:]).strip()
    return "", ""
```

- [ ] **Step 4: Register the module in the store package**

In `backend/src/grimoire/store/__init__.py`, change the import tuple (lines 5-9) to include `briefs`:

```python
from . import (
    appearances, assets, briefs, campaigns, cards, characters, context, entities,
    fetch, greetings, localize, lorebook, pcs, playing, scenes, sync, tags,
    worlds,
)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_briefs_store.py -q`
Expected: PASS (9 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/briefs.py backend/src/grimoire/store/__init__.py backend/tests/test_briefs_store.py
git commit -m "feat: briefs store — per-character tagline + paragraph with staleness"
```

---

### Task 2: Copy `brief.md` into the campaign on appearance

**Files:**
- Modify: `backend/src/grimoire/store/appearances.py` (the `_copy_actor` function — the `if kind == "characters"` asset-copy block)
- Test: `backend/tests/test_appearances_store.py`

**Interfaces:**
- Consumes: `briefs.write_brief` / `briefs.read_brief` (Task 1) for test setup/assertions.
- Produces: no new symbols — `appear()` now also snapshots `brief.md` when present.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_appearances_store.py`:

```python
def test_appear_copies_brief_into_campaign(monkeypatch, tmp_path):
    from grimoire.store import briefs
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Aese", "main", characters.blank_card("Aese"))
    briefs.write_brief(wroot, "aese", "A silent snowleopardgirl.", "She keeps house.", "h0")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "S")

    ap.appear(cid, sid, "characters", "aese", "main", "npc")

    croot = campaigns.campaign_root(cid)
    assert briefs.read_brief(croot, "aese") == {
        "tagline": "A silent snowleopardgirl.", "base": "h0", "body": "She keeps house."}
```

(If `worlds`, `campaigns`, `characters`, `scenes`, `ap` are not already imported at the top of this test file, add them — match the existing import style in the file.)

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_appearances_store.py::test_appear_copies_brief_into_campaign -v`
Expected: FAIL — `briefs.read_brief(croot, "aese")` is `None` (brief not copied).

- [ ] **Step 3: Copy the brief in `_copy_actor`**

In `backend/src/grimoire/store/appearances.py`, the tail of `_copy_actor` currently reads:

```python
    if kind == "characters" and (src_dir / "assets").exists():
        shutil.copytree(src_dir / "assets", dst_dir / "assets", dirs_exist_ok=True)
```

Replace it with:

```python
    if kind == "characters":
        if (src_dir / "assets").exists():
            shutil.copytree(src_dir / "assets", dst_dir / "assets", dirs_exist_ok=True)
        if (src_dir / "brief.md").exists():
            (dst_dir / "brief.md").write_text(
                (src_dir / "brief.md").read_text(encoding="utf-8"), encoding="utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_appearances_store.py::test_appear_copies_brief_into_campaign -v`
Expected: PASS.

- [ ] **Step 5: Run the full appearances suite (no regressions)**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_appearances_store.py -q`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/appearances.py backend/tests/test_appearances_store.py
git commit -m "feat: snapshot brief.md into the campaign on appearance"
```

---

### Task 3: Cast-directory tiers in the context builder

**Files:**
- Modify: `backend/src/grimoire/store/context.py` (imports; add `_char_name` + `_cast_directory`; append the block in `build_messages`)
- Test: `backend/tests/test_context.py`

**Interfaces:**
- Consumes: `briefs.read_brief` (Task 1); `characters.character_refs`, `characters.read_character`; `appearances.scene_cast`, `appearances.roster`; `campaigns.read_campaign`; `worlds.world_root`.
- Produces: `_cast_directory(croot, wroot, cid, sid) -> str` (internal); `build_messages` output now contains the directory block after world-info.

**Tier rules** (relative to the current scene):
- Tier 2 ("Active in this campaign, elsewhere"): roster actors with `kind == "characters"` and `role == "npc"` not in the scene; rendered as `"{Name}: {brief body}"` from the **campaign** brief; skip if no brief or empty body.
- Tier 3 ("Known to exist"): `characters.character_refs(wroot)` minus roster-character-ids minus present; rendered as `"{Name}: {tagline} (available as: v1, v2)"` from the **world** brief; skip if no brief or empty tagline.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_context.py`:

```python
def test_cast_directory_tiers(monkeypatch, tmp_path):
    from grimoire.store import briefs
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)

    # present in this scene (full card)
    characters.create_character(wroot, "Aese", "main", _npc_card("Aese", description="present-desc"))
    # appeared elsewhere in the campaign (paragraph) — needs a brief to be snapshotted
    characters.create_character(wroot, "Myval", "main", _npc_card("Myval", description="m"))
    briefs.write_brief(wroot, "myval", "A raccoongirl rogue.", "Myval prowls the dusk road.",
                       briefs.default_card_hash(wroot, "myval"))
    # world-only with a brief and two versions (sentence + version list)
    characters.create_character(wroot, "Akane", "main", _npc_card("Akane", description="a"))
    characters.create_version(wroot, "akane", "futa", _npc_card("Akane", description="a"))
    briefs.write_brief(wroot, "akane", "An eager doggirl.", "Akane wants to please.",
                       briefs.default_card_hash(wroot, "akane"))
    # world-only WITHOUT a brief (must be skipped)
    characters.create_character(wroot, "Ghost", "main", _npc_card("Ghost", description="g"))

    # Myval appears in a different scene -> roster, not in this scene's cast
    other = scenes.create_scene(cid, "Other")
    ap.appear(cid, other, "characters", "myval", "main", "npc")
    # Aese appears in our scene
    ap.appear(cid, sid, "characters", "aese", "main", "npc")
    scenes.append_message(cid, sid, "user", "hi")

    sys = context.build_messages(cid, sid)[0]["content"]
    assert "present-desc" in sys                                  # tier 1 full card
    assert "Myval: Myval prowls the dusk road." in sys           # tier 2 paragraph
    assert "Akane: An eager doggirl. (available as: futa, main)" in sys  # tier 3 sentence + versions
    assert "Ghost" not in sys                                     # un-briefed world char skipped
    assert "Myval" not in sys.split("## Known to exist")[1]       # roster char not in tier 3


def test_cast_directory_absent_when_no_briefs(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Aese", "main", _npc_card("Aese", description="d"))
    ap.appear(cid, sid, "characters", "aese", "main", "npc")
    scenes.append_message(cid, sid, "user", "hi")
    sys = context.build_messages(cid, sid)[0]["content"]
    assert "Other characters in this world" not in sys
```

Note: `_version_ids` sorts alphabetically, so versions render as `futa, main`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py::test_cast_directory_tiers backend/tests/test_context.py::test_cast_directory_absent_when_no_briefs -v`
Expected: FAIL — directory block absent from system content.

- [ ] **Step 3: Add imports**

In `backend/src/grimoire/store/context.py`, change the import line:

```python
from . import appearances, campaigns, characters, config, entities, pcs, scenes
```

to:

```python
from . import appearances, briefs, campaigns, characters, config, entities, pcs, scenes, worlds
```

- [ ] **Step 4: Add the directory helpers**

In `backend/src/grimoire/store/context.py`, add these functions just above `build_messages`:

```python
def _char_name(root, cid: str) -> str:
    try:
        return characters.read_character(root, cid)["meta"]["name"]
    except characters.CharacterNotFound:
        return cid


def _cast_directory(croot, wroot, cid: str, sid: str) -> str:
    """Off-scene cast as two tiers: campaign-active characters (paragraph) and every
    other world character (tagline + available versions). Empty string if neither tier
    has any briefed members."""
    present = {a["id"] for a in appearances.scene_cast(cid, sid) if a["kind"] == "characters"}
    roster = appearances.roster(cid)
    roster_ids = {a["id"] for a in roster if a["kind"] == "characters"}

    active: list[str] = []
    for a in roster:
        if a["kind"] != "characters" or a["role"] != "npc" or a["id"] in present:
            continue
        b = briefs.read_brief(croot, a["id"])
        if b and b["body"]:
            active.append(f"{_char_name(croot, a['id'])}: {b['body']}")

    known: list[str] = []
    for char_id in characters.character_refs(wroot):
        if char_id in roster_ids or char_id in present:
            continue
        b = briefs.read_brief(wroot, char_id)
        if not b or not b["tagline"]:
            continue
        versions = ", ".join(v["id"] for v in characters.read_character(wroot, char_id)["versions"])
        suffix = f" (available as: {versions})" if versions else ""
        known.append(f"{_char_name(wroot, char_id)}: {b['tagline']}{suffix}")

    if not active and not known:
        return ""
    parts = ["# Other characters in this world",
             "# (Not present. Introduce them only if the story calls for it.)"]
    if active:
        parts.append("## Active in this campaign, elsewhere\n" + "\n".join(active))
    if known:
        parts.append("## Known to exist\n" + "\n".join(known))
    return "\n\n".join(parts)
```

- [ ] **Step 5: Append the block in `build_messages`**

In `build_messages`, find:

```python
    wi = _world_info(croot, recent_text)
    if wi:
        parts.append(wi)
    system_text = "\n\n".join(parts).strip()
```

Insert the cast directory between the `wi` append and `system_text`:

```python
    wi = _world_info(croot, recent_text)
    if wi:
        parts.append(wi)
    wroot = worlds.world_root(campaigns.read_campaign(cid)["meta"].get("world", ""))
    cast = _cast_directory(croot, wroot, cid, sid)
    if cast:
        parts.append(cast)
    system_text = "\n\n".join(parts).strip()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q`
Expected: PASS (all, including the pre-existing context tests).

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/store/context.py backend/tests/test_context.py
git commit -m "feat: tiered off-scene cast directory in narrator context"
```

---

### Task 4: Brief routes — read, save (manual override), derive (LLM)

**Files:**
- Modify: `backend/src/grimoire/routes.py` (add a `BriefSave` model near the other models; add three routes near the character routes, ~after line 420)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `store.briefs.*` (Task 1); `store.characters.read_character`/`read_card`; `store.read_config`; `_world_root_or_404`, `_require_key`, `get_openrouter`, `OpenRouterClient`, `OpenRouterError` (all already in `routes.py`).
- Produces three endpoints:
  - `GET  /worlds/{wid}/characters/{cid}/brief` → `{"brief": dict | None, "stale": bool}`
  - `PUT  /worlds/{wid}/characters/{cid}/brief` (body `{tagline, body}`) → `{"ok": True}` (re-stamps `base` to the current default-card hash)
  - `POST /worlds/{wid}/characters/{cid}/brief` → derives via the model, writes, returns `{"brief": dict, "stale": False}`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_routes.py`:

```python
class FakeOpenRouterComplete:
    def __init__(self, text):
        self.text = text

    async def stream(self, messages, model, key):
        yield self.text

    async def complete(self, messages, model, key):
        return self.text


def _world_char(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters",
                      json={"name": "Aese", "version_name": "main"}).json()["character"]
    return wid, cid


def test_get_brief_absent_is_stale(client):
    wid, cid = _world_char(client)
    body = client.get(f"/api/worlds/{wid}/characters/{cid}/brief").json()
    assert body == {"brief": None, "stale": True}


def test_put_brief_saves_and_is_fresh(client):
    wid, cid = _world_char(client)
    r = client.put(f"/api/worlds/{wid}/characters/{cid}/brief",
                   json={"tagline": "A snowleopardgirl.", "body": "She keeps house."})
    assert r.json() == {"ok": True}
    got = client.get(f"/api/worlds/{wid}/characters/{cid}/brief").json()
    assert got["stale"] is False
    assert got["brief"]["tagline"] == "A snowleopardgirl."
    assert got["brief"]["body"] == "She keeps house."


def test_post_brief_derives_from_model(client):
    wid, cid = _world_char(client)
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    client.app.dependency_overrides[routes.get_openrouter] = \
        lambda: FakeOpenRouterComplete("A silent snowleopardgirl.\n\nShe keeps house and is shy.")
    r = client.post(f"/api/worlds/{wid}/characters/{cid}/brief")
    assert r.status_code == 200
    brief = r.json()["brief"]
    assert brief["tagline"] == "A silent snowleopardgirl."
    assert brief["body"] == "She keeps house and is shy."
    assert r.json()["stale"] is False


def test_post_brief_requires_key(client):
    wid, cid = _world_char(client)
    # default fixture key is empty
    r = client.post(f"/api/worlds/{wid}/characters/{cid}/brief")
    assert r.status_code == 409
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -k brief -v`
Expected: FAIL — 404 (routes not defined yet).

- [ ] **Step 3: Add the `BriefSave` model**

In `backend/src/grimoire/routes.py`, add near the other models (e.g. after `class VersionUpdate` / the character models):

```python
class BriefSave(BaseModel):
    tagline: str = ""
    body: str = ""
```

- [ ] **Step 4: Add the three routes**

In `backend/src/grimoire/routes.py`, add after the character version routes (around line 420, before `_EXPORT_MEDIA`):

```python
@router.get("/worlds/{wid}/characters/{cid}/brief")
def get_character_brief(wid: str, cid: str):
    root = _world_root_or_404(wid)
    try:
        store.characters.read_character(root, cid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"brief": store.briefs.read_brief(root, cid), "stale": store.briefs.is_stale(root, cid)}


@router.put("/worlds/{wid}/characters/{cid}/brief")
def put_character_brief(wid: str, cid: str, body: BriefSave):
    root = _world_root_or_404(wid)
    base = store.briefs.default_card_hash(root, cid)
    if base is None:
        raise HTTPException(status_code=404, detail="character not found")
    store.briefs.write_brief(root, cid, body.tagline, body.body, base)
    return {"ok": True}


@router.post("/worlds/{wid}/characters/{cid}/brief")
async def post_character_brief(wid: str, cid: str,
                               client: OpenRouterClient = Depends(get_openrouter)):
    root = _world_root_or_404(wid)
    cfg = store.read_config()
    _require_key(cfg)
    try:
        ch = store.characters.read_character(root, cid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    card = store.characters.read_card(root, cid, ch["meta"]["default_version"])
    messages = store.briefs.build_prompt(card["data"])
    try:
        text = await client.complete(messages, cfg["model"], cfg["openrouter_key"])
    except OpenRouterError as exc:
        raise HTTPException(status_code=502, detail={"detail": exc.detail, "kind": exc.kind})
    tagline, paragraph = store.briefs.parse_output(text)
    store.briefs.write_brief(root, cid, tagline, paragraph,
                             store.briefs.default_card_hash(root, cid) or "")
    return {"brief": store.briefs.read_brief(root, cid), "stale": False}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -k brief -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat: brief routes — read, manual save, and LLM derive"
```

---

### Task 5: Full suite + lint

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS (all tests, no regressions).

- [ ] **Step 2: Lint (matches project tooling)**

Run: `backend/.venv/Scripts/python.exe -m ruff check backend/src/grimoire/store/briefs.py backend/src/grimoire/store/context.py backend/src/grimoire/store/appearances.py backend/src/grimoire/routes.py`
Expected: no errors. Fix any reported issues (line length, import order) and re-run.

- [ ] **Step 3: Commit any lint fixes**

```bash
git add -A
git commit -m "chore: lint brief tier changes"
```

(Skip this commit if Step 2 reported nothing.)

---

## Notes for the implementer

- **Why the LLM call is in the route, not the store:** the `briefs` store stays pure (file IO + prompt/parse) so it tests without network. The route owns the async `OpenRouterClient.complete` call, mirroring how `post_opener`/`post_chat` use `Depends(get_openrouter)` and `_require_key`.
- **`parse_output` contract:** the model is instructed to return tagline / blank line / paragraph. `parse_output` is defensive — first non-empty line is the tagline, the remainder is the body. It does not require the blank line.
- **Version-list ordering** in tier 3 comes from `characters._version_ids` (alphabetical), so `futa` precedes `main`. Tests rely on this.
- **Out of scope (spec follow-ups):** retiring duplicate-character lore (content migration), context-budget instrumentation, and the brief editing UI (campaign-tab redesign). Do not start these here.
