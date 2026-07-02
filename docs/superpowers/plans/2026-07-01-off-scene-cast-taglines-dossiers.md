# Off-scene cast: taglines & dossiers — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the never-populated `brief` artifact with two named artifacts — a world-level, character-level **tagline** (feeds the off-scene "Known to exist" tier) and a campaign-level **dossier** (feeds "Active in this campaign, elsewhere") — each generated at a natural, appearance-independent moment.

**Architecture:** Two new leaf store modules (`taglines`, `dossiers`), each pure file-IO + prompt/parse (LLM calls stay in the route layer, mirroring the deleted `briefs.py`). `context._cast_directory` reads them instead of briefs. Taglines are written by an input-or-generate popup at import, hand-edit in `CharacterEditor`, or authored; dossiers are written at absorb. `briefs.py`, its routes, and the appearance-time copy are removed.

**Tech Stack:** FastAPI + pytest (backend), Vite/React + vitest (frontend). Store rooted at `GRIMOIRE_HOME`.

## Global Constraints

- Context builds NEVER make LLM calls — generation is import/absorb/route-triggered only.
- Tagline is **character-level** (`<root>/characters/<cid>/tagline.md`), version-independent, plain text, **no** staleness hash.
- Dossier is **campaign-level** (`<croot>/characters/<cid>/dossier.md`), plain text.
- A character with neither artifact is **skipped** from the directory (never a bare name).
- Generation failures/missing API key must **never** fail the triggering action (import/absorb).
- Backend tests: `backend/.venv/Scripts/python.exe -m pytest backend -q`.
- Frontend tests: from `frontend/`, `npx vitest run` and `npx tsc -b`.

---

### Task 1: `taglines` store module

**Files:**
- Create: `backend/src/grimoire/store/taglines.py`
- Test: `backend/tests/test_taglines_store.py`

**Interfaces:**
- Produces: `tagline_path(root: Path, cid) -> Path`, `read(root: Path, cid) -> str`, `write(root: Path, cid, text) -> None`, `TAGLINE_INSTRUCTION: str`, `build_prompt(card_data: dict) -> list[dict]`, `parse_output(text: str) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_taglines_store.py
from grimoire.store import taglines, characters, worlds


def _world_with_char(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    root = worlds.world_root(wid)
    card = characters.blank_card("Aese")
    card["data"]["description"] = "a snowleopardgirl"
    characters.create_character(root, "Aese", "main", card)
    return root


def test_read_missing_is_empty(monkeypatch, tmp_path):
    root = _world_with_char(monkeypatch, tmp_path)
    assert taglines.read(root, "aese") == ""


def test_write_then_read_roundtrip(monkeypatch, tmp_path):
    root = _world_with_char(monkeypatch, tmp_path)
    taglines.write(root, "aese", "  A silent snowleopardgirl.  ")
    assert taglines.read(root, "aese") == "A silent snowleopardgirl."


def test_build_prompt_includes_card_fields():
    msgs = taglines.build_prompt({"name": "Aese", "description": "a snowleopardgirl",
                                  "personality": "shy", "scenario": ""})
    assert msgs[0]["role"] == "system"
    assert "Aese" in msgs[1]["content"] and "snowleopardgirl" in msgs[1]["content"]


def test_parse_output_takes_first_nonblank_line():
    assert taglines.parse_output("\n\nA silent snowleopardgirl.\nextra") == "A silent snowleopardgirl."
```

- [ ] **Step 2: Run to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_taglines_store.py -q`
Expected: FAIL — `ModuleNotFoundError: grimoire.store.taglines`.

- [ ] **Step 3: Write the module**

```python
# backend/src/grimoire/store/taglines.py
"""Per-character one-line tagline — world-level identity feeding the off-scene cast's
"Known to exist" tier. Character-level (not per-version), plain text, no staleness hash:
a hand-written tagline must not silently expire when a card changes.

Stored at <root>/characters/<cid>/tagline.md as the trimmed sentence. Pure file IO +
prompt/parse; the LLM call lives in the route layer (mirrors the old briefs.py).
"""

from __future__ import annotations

from pathlib import Path

TAGLINE_INSTRUCTION = (
    "Summarize this character in a single vivid sentence for a game master's quick "
    "reference — who they are and their defining trait. Third person, present tense. "
    "Reply with the one sentence only: no headings, labels, or quotes."
)


def tagline_path(root: Path, cid: str) -> Path:
    return root / "characters" / cid / "tagline.md"


def read(root: Path, cid: str) -> str:
    p = tagline_path(root, cid)
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def write(root: Path, cid: str, text: str) -> None:
    p = tagline_path(root, cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text.strip() + "\n", encoding="utf-8")


def build_prompt(card_data: dict) -> list[dict]:
    fields = [card_data.get(f, "") for f in ("name", "description", "personality", "scenario")]
    card_text = "\n".join(x for x in fields if x)
    return [{"role": "system", "content": TAGLINE_INSTRUCTION},
            {"role": "user", "content": card_text}]


def parse_output(text: str) -> str:
    for ln in text.strip().split("\n"):
        if ln.strip():
            return ln.strip()
    return ""
```

- [ ] **Step 4: Run to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_taglines_store.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/taglines.py backend/tests/test_taglines_store.py
git commit -m "feat(taglines): character-level world tagline store module"
```

---

### Task 2: `dossiers` store module

**Files:**
- Create: `backend/src/grimoire/store/dossiers.py`
- Test: `backend/tests/test_dossiers_store.py`

**Interfaces:**
- Produces: `dossier_path(croot, cid) -> Path`, `read(croot, cid) -> str`, `write(croot, cid, text) -> None`, `DOSSIER_INSTRUCTION: str`, `build_prompt(name: str, prior: str, transcript: str) -> list[dict]`, `parse_output(text: str) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_dossiers_store.py
from grimoire.store import dossiers, characters, worlds


def _root(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    root = worlds.world_root(worlds.create_world("W"))
    characters.create_character(root, "Aese", "main", characters.blank_card("Aese"))
    return root


def test_read_missing_is_empty(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    assert dossiers.read(root, "aese") == ""


def test_write_then_read_roundtrip(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    dossiers.write(root, "aese", "  Aese now trusts the owner.  ")
    assert dossiers.read(root, "aese") == "Aese now trusts the owner."


def test_build_prompt_includes_name_prior_and_transcript():
    msgs = dossiers.build_prompt("Aese", "was shy", "USER: hi\nAESE: *waves*")
    assert msgs[0]["role"] == "system"
    assert "Aese" in msgs[1]["content"]
    assert "was shy" in msgs[1]["content"] and "waves" in msgs[1]["content"]


def test_build_prompt_handles_empty_prior():
    msgs = dossiers.build_prompt("Aese", "", "transcript")
    assert "(none)" in msgs[1]["content"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_dossiers_store.py -q`
Expected: FAIL — `ModuleNotFoundError: grimoire.store.dossiers`.

- [ ] **Step 3: Write the module**

```python
# backend/src/grimoire/store/dossiers.py
"""Per-character campaign "dossier" — a short standing paragraph (who they are + their
current status in this campaign) feeding the off-scene cast's "Active in this campaign,
elsewhere" tier. Campaign-level; written at absorb. Plain text at
<croot>/characters/<cid>/dossier.md. Pure file IO + prompt/parse; the LLM call lives in
the route layer (mirrors the old briefs.py).
"""

from __future__ import annotations

from pathlib import Path

DOSSIER_INSTRUCTION = (
    "You are updating a game master's dossier on a character in an ongoing campaign. "
    "Given the character's prior dossier (may be empty) and the latest scene transcript, "
    "reply with ONE short paragraph (3-4 sentences) on who they are and their current "
    "standing after this scene. Third person, present tense. No headings or labels."
)


def dossier_path(croot: Path, cid: str) -> Path:
    return croot / "characters" / cid / "dossier.md"


def read(croot: Path, cid: str) -> str:
    p = dossier_path(croot, cid)
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def write(croot: Path, cid: str, text: str) -> None:
    p = dossier_path(croot, cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text.strip() + "\n", encoding="utf-8")


def build_prompt(name: str, prior: str, transcript: str) -> list[dict]:
    head = f"Character: {name}\nPrior dossier: {prior or '(none)'}\n\nScene transcript:\n"
    return [{"role": "system", "content": DOSSIER_INSTRUCTION},
            {"role": "user", "content": head + transcript}]


def parse_output(text: str) -> str:
    return text.strip()
```

- [ ] **Step 4: Run to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_dossiers_store.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/dossiers.py backend/tests/test_dossiers_store.py
git commit -m "feat(dossiers): campaign-level character dossier store module"
```

---

### Task 3: Point `_cast_directory` at taglines + dossiers

**Files:**
- Modify: `backend/src/grimoire/store/__init__.py:5-6` (add `dossiers, taglines`)
- Modify: `backend/src/grimoire/store/context.py:11` (imports) and `:135-152` (`_cast_directory`)
- Test: `backend/tests/test_context.py:297-334` (rewrite the two cast-directory tests)

**Interfaces:**
- Consumes: `taglines.read`, `dossiers.read` (Tasks 1-2).

- [ ] **Step 1: Update the cast-directory tests to the new artifacts**

Replace `test_cast_directory_tiers` (currently at `test_context.py:297`) and
`test_cast_directory_absent_when_no_briefs` (`:331`) with:

```python
def test_cast_directory_tiers(monkeypatch, tmp_path):
    from grimoire.store import taglines, dossiers
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    croot = campaigns.campaign_root(cid)

    # present in this scene (full card)
    characters.create_character(wroot, "Aese", "main", _npc_card("Aese", description="present-desc"))
    # appeared elsewhere in the campaign -> tier 2 paragraph from the campaign dossier
    characters.create_character(wroot, "Myval", "main", _npc_card("Myval", description="m"))
    # world-only with a tagline and two versions -> tier 3 sentence + version list
    characters.create_character(wroot, "Akane", "main", _npc_card("Akane", description="a"))
    characters.create_version(wroot, "akane", "futa", _npc_card("Akane", description="a"))
    taglines.write(wroot, "akane", "An eager doggirl.")
    # world-only WITHOUT a tagline (must be skipped)
    characters.create_character(wroot, "Ghost", "main", _npc_card("Ghost", description="g"))

    other = scenes.create_scene(cid, "Other")
    ap.appear(cid, other, "characters", "myval", "main", "npc")
    ap.appear(cid, sid, "characters", "aese", "main", "npc")
    dossiers.write(croot, "myval", "Myval prowls the dusk road.")
    scenes.append_message(cid, sid, "user", "hi")

    sys = context.build_messages(cid, sid)[0]["content"]
    assert "present-desc" in sys                                         # tier 1 full card
    assert "Myval: Myval prowls the dusk road." in sys                   # tier 2 dossier
    assert "Akane: An eager doggirl. (available as: futa, main)" in sys  # tier 3 tagline + versions
    assert "Ghost" not in sys                                            # no tagline -> skipped
    assert "Myval" not in sys.split("## Known to exist")[1]              # roster char not in tier 3


def test_cast_directory_absent_when_no_briefs(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Aese", "main", _npc_card("Aese", description="d"))
    ap.appear(cid, sid, "characters", "aese", "main", "npc")
    scenes.append_message(cid, sid, "user", "hi")
    sys = context.build_messages(cid, sid)[0]["content"]
    assert "Other characters in this world" not in sys
```

- [ ] **Step 2: Run to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py::test_cast_directory_tiers -q`
Expected: FAIL — tier 2/3 still read briefs, so the dossier/tagline strings are absent.

- [ ] **Step 3: Register the modules in `store/__init__.py`**

In the `from . import ( ... )` block (`store/__init__.py:5-6`), keep `briefs` for now (removed in Task 4) and add `dossiers` and `taglines` alphabetically. After edit the tuple begins:

```python
from . import (
    absorb, appearances, assets, briefs, campaigns, cards, changes, characters, chronicle,
    ...
)
```

becomes — insert `dossiers` after `context`/before `entities` region and `taglines` near the end; exact placement is cosmetic as long as both names are imported. Simplest: add a line after the closing `)`:

```python
from . import dossiers, taglines  # noqa: F401  (off-scene cast artifacts)
```

- [ ] **Step 4: Update `context.py` imports and `_cast_directory`**

`context.py:11` — change the import tuple to drop `briefs` and add `dossiers, taglines`:

```python
from . import (appearances, calendars, campaigns, characters, chronicle,
```
(remove `briefs`, and add `dossiers, taglines` to this `from . import (...)` group — keep the rest of the names unchanged).

Then replace the two read sites in `_cast_directory` (`context.py:135-152`).

Tier 2 loop body — replace:

```python
        b = briefs.read_brief(croot, a["id"])
        if b and b["body"]:
            active.append(f"{_char_name(croot, a['id'])}: {b['body']}")
```

with:

```python
        body = dossiers.read(croot, a["id"])
        if body:
            active.append(f"{_char_name(croot, a['id'])}: {body}")
```

Tier 3 loop body — replace:

```python
        b = briefs.read_brief(wroot, char_id)
        if not b or not b["tagline"]:
            continue
        versions = ", ".join(v["id"] for v in characters.read_character(wroot, char_id)["versions"])
        suffix = f" (available as: {versions})" if versions else ""
        known.append(f"{_char_name(wroot, char_id)}: {b['tagline']}{suffix}")
```

with:

```python
        tag = taglines.read(wroot, char_id)
        if not tag:
            continue
        versions = ", ".join(v["id"] for v in characters.read_character(wroot, char_id)["versions"])
        suffix = f" (available as: {versions})" if versions else ""
        known.append(f"{_char_name(wroot, char_id)}: {tag}{suffix}")
```

- [ ] **Step 5: Run context tests**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q`
Expected: PASS (all context tests, including the two rewritten).

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/context.py backend/src/grimoire/store/__init__.py backend/tests/test_context.py
git commit -m "feat(context): off-scene tiers read taglines and dossiers"
```

---

### Task 4: Remove `briefs`; add tagline routes; drop appearance-time copy

**Files:**
- Delete: `backend/src/grimoire/store/briefs.py`, `backend/tests/test_briefs_store.py`
- Modify: `backend/src/grimoire/store/__init__.py:5-6` (remove `briefs`)
- Modify: `backend/src/grimoire/routes.py:92-94` (models) and `:569-608` (routes)
- Modify: `backend/src/grimoire/store/appearances.py:91-93` (`_copy_actor`)
- Modify: `backend/tests/test_appearances_store.py:4,136-149`
- Modify: `backend/tests/test_routes.py:991-1025` (brief route tests → tagline route tests)

**Interfaces:**
- Consumes: `store.taglines` (Task 1).
- Produces routes: `GET/PUT/POST /worlds/{wid}/characters/{cid}/tagline[/generate]`.

- [ ] **Step 1: Rewrite the tagline route tests (replace the brief route tests)**

Replace `test_get_brief_absent_is_stale`, `test_put_brief_saves_and_is_fresh`,
`test_post_brief_derives_from_model`, `test_post_brief_requires_key`
(`test_routes.py:991-1025`) with:

```python
def test_get_tagline_absent_is_empty(client):
    wid, cid = _world_char(client)
    assert client.get(f"/api/worlds/{wid}/characters/{cid}/tagline").json() == {"tagline": ""}


def test_put_tagline_saves(client):
    wid, cid = _world_char(client)
    r = client.put(f"/api/worlds/{wid}/characters/{cid}/tagline",
                   json={"tagline": "A snowleopardgirl."})
    assert r.json() == {"ok": True}
    assert client.get(f"/api/worlds/{wid}/characters/{cid}/tagline").json() == {"tagline": "A snowleopardgirl."}


def test_post_tagline_generate_from_model(client):
    wid, cid = _world_char(client)
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    client.app.dependency_overrides[routes.get_openrouter] = \
        lambda: FakeOpenRouterComplete("A silent snowleopardgirl.\nignored second line")
    r = client.post(f"/api/worlds/{wid}/characters/{cid}/tagline/generate")
    assert r.status_code == 200
    assert r.json() == {"tagline": "A silent snowleopardgirl."}
    assert client.get(f"/api/worlds/{wid}/characters/{cid}/tagline").json() == {"tagline": "A silent snowleopardgirl."}


def test_post_tagline_generate_requires_key(client):
    wid, cid = _world_char(client)
    r = client.post(f"/api/worlds/{wid}/characters/{cid}/tagline/generate")
    assert r.status_code == 409
```

- [ ] **Step 2: Run to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k tagline`
Expected: FAIL — tagline routes don't exist (404).

- [ ] **Step 3: Swap the models and routes in `routes.py`**

Replace `BriefSave` (`routes.py:92-94`):

```python
class TaglineSave(BaseModel):
    tagline: str = ""
```

Replace the three brief routes (`routes.py:569-608`) with:

```python
@router.get("/worlds/{wid}/characters/{cid}/tagline")
def get_character_tagline(wid: str, cid: str):
    root = _world_root_or_404(wid)
    try:
        store.characters.read_character(root, cid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"tagline": store.taglines.read(root, cid)}


@router.put("/worlds/{wid}/characters/{cid}/tagline")
def put_character_tagline(wid: str, cid: str, body: TaglineSave):
    root = _world_root_or_404(wid)
    try:
        store.characters.read_character(root, cid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    store.taglines.write(root, cid, body.tagline)
    return {"ok": True}


@router.post("/worlds/{wid}/characters/{cid}/tagline/generate")
async def post_character_tagline_generate(wid: str, cid: str,
                                          client: OpenRouterClient = Depends(get_openrouter)):
    root = _world_root_or_404(wid)
    cfg = store.read_config()
    _require_key(cfg)
    try:
        ch = store.characters.read_character(root, cid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    card = store.characters.read_card(root, cid, ch["meta"]["default_version"])
    messages = store.taglines.build_prompt(card["data"])
    try:
        text = await client.complete(messages, cfg["model"], cfg["openrouter_key"])
    except OpenRouterError as exc:
        raise HTTPException(status_code=502, detail={"detail": exc.detail, "kind": exc.kind})
    tagline = store.taglines.parse_output(text)
    store.taglines.write(root, cid, tagline)
    return {"tagline": tagline}
```

- [ ] **Step 4: Drop the brief copy in `_copy_actor`**

In `appearances.py`, delete these lines (`:91-93`):

```python
        if (src_dir / "brief.md").exists():
            (dst_dir / "brief.md").write_text(
                (src_dir / "brief.md").read_text(encoding="utf-8"), encoding="utf-8")
```

- [ ] **Step 5: Update the appearances test and remove the briefs module/tests**

In `test_appearances_store.py`: change the import (`:4`) to drop `briefs`:

```python
from grimoire.store import campaigns, characters, dossiers, pcs, scenes, worlds
```

Replace `test_appear_copies_brief_into_campaign` (`:136-149`) with:

```python
def test_appear_does_not_copy_dossier_into_campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Aese", "main", characters.blank_card("Aese"))
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "S")

    ap.appear(cid, sid, "characters", "aese", "main", "npc")

    # Dossiers are born campaign-side at absorb, not copied on appearance.
    assert dossiers.read(campaigns.campaign_root(cid), "aese") == ""
```

Then delete the dead module and its test:

```bash
rm backend/src/grimoire/store/briefs.py backend/tests/test_briefs_store.py
```

In `store/__init__.py`, remove `briefs` from the `from . import (...)` tuple.

- [ ] **Step 6: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS. Grep guard for leftover references:
Run: `backend/.venv/Scripts/python.exe -c "import grimoire.routes, grimoire.store"` → no ImportError.

- [ ] **Step 7: Commit**

```bash
git add -A backend
git commit -m "feat(taglines): tagline routes; remove briefs and appearance-time copy"
```

---

### Task 5: Frontend — tagline API + `CharacterEditor` field

**Files:**
- Modify: `frontend/src/api/client.ts` (character section, near `:290`)
- Modify: `frontend/src/components/CharacterEditor.tsx`
- Test: `frontend/src/components/CharacterEditor.test.tsx`

**Interfaces:**
- Consumes: tagline routes (Task 4).
- Produces: `api.getCharacterTagline`, `api.setCharacterTagline`, `api.generateCharacterTagline`.

- [ ] **Step 1: Add the API methods**

In `client.ts`, after `deleteCharacter` (`:284`), add:

```typescript
  getCharacterTagline: (wid: string, cid: string) =>
    request<{ tagline: string }>("GET", `/api/worlds/${wid}/characters/${cid}/tagline`),
  setCharacterTagline: (wid: string, cid: string, tagline: string) =>
    request<{ ok: boolean }>("PUT", `/api/worlds/${wid}/characters/${cid}/tagline`, { tagline }),
  generateCharacterTagline: (wid: string, cid: string) =>
    request<{ tagline: string }>("POST", `/api/worlds/${wid}/characters/${cid}/tagline/generate`),
```

- [ ] **Step 2: Write the failing test**

Add to `CharacterEditor.test.tsx`. Extend the `api` mock (top of file, near `:8`) with the three methods, then:

```typescript
test("detail view shows the character tagline", async () => {
  (api.getCharacterTagline as any).mockResolvedValue({ tagline: "A silent snowleopardgirl." });
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByText("Seraphine"));
  await screen.findByText("A silent snowleopardgirl.");
});

test("edit view saves an edited tagline via PUT", async () => {
  (api.getCharacterTagline as any).mockResolvedValue({ tagline: "old" });
  (api.setCharacterTagline as any).mockResolvedValue({ ok: true });
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getAllByText("Edit")[0]);
  const box = await screen.findByLabelText("Tagline");
  fireEvent.change(box, { target: { value: "A new tagline." } });
  fireEvent.click(screen.getByText("Save tagline"));
  await waitFor(() => expect(api.setCharacterTagline).toHaveBeenCalledWith("w", "seraphine", "A new tagline."));
});
```

(Add the three methods to the mock object literal so `api.getCharacterTagline` etc. exist; mirror how `importCharacterFromChub` is registered at `CharacterEditor.test.tsx:10`. Use the file's existing `render`/`screen`/`fireEvent`/`waitFor` imports.)

- [ ] **Step 3: Run to verify it fails**

Run (from `frontend/`): `npx vitest run CharacterEditor`
Expected: FAIL — no "Tagline" field / `getCharacterTagline` undefined.

- [ ] **Step 4: Wire the tagline into `CharacterEditor`**

Add state and load it when a character is selected. After the `birthdate` state (`:53`):

```typescript
  const [tagline, setTagline] = useState("");
```

In `select` (`:189`) and `focusCharacter` (`:213`), after `setDetail(d)` add:

```typescript
    setTagline((await api.getCharacterTagline(wid, cid).catch(() => ({ tagline: "" }))).tagline);
```

(In `focusCharacter` the param is named `cid`? it is `focusCharacter(cid, vid)` — use `cid`.)

Add a save handler near `saveBirthdate` (`:197`):

```typescript
  async function saveTagline() {
    if (!detail) return;
    try {
      await api.setCharacterTagline(wid, detail.meta.id, tagline.trim());
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function regenerateTagline() {
    if (!detail) return;
    try {
      const r = await api.generateCharacterTagline(wid, detail.meta.id);
      setTagline(r.tagline);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }
```

In the **detail** view, inside `.detail-meta` after the `<h3>` (`:577`), add:

```tsx
                {tagline && <div className="detail-text tagline">{tagline}</div>}
```

In the **edit** view, after the `Birthdate` `Field` (`:728`), add:

```tsx
          <Field label="Tagline" hint="one-line identity for the off-scene cast">
            <textarea aria-label="Tagline" value={tagline} rows={2}
                      onChange={(e) => setTagline(e.target.value)} />
            <div className="form-actions">
              <button className="subtle" type="button" onClick={regenerateTagline}>Generate</button>
              <button className="subtle" type="button" onClick={saveTagline}>Save tagline</button>
            </div>
          </Field>
```

- [ ] **Step 5: Run tests + typecheck**

Run (from `frontend/`): `npx vitest run CharacterEditor` then `npx tsc -b`
Expected: PASS; no type errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/components/CharacterEditor.tsx frontend/src/components/CharacterEditor.test.tsx
git commit -m "feat(taglines): view/edit/regenerate tagline in CharacterEditor"
```

---

### Task 6: Frontend — input-or-generate popup at import

**Files:**
- Create: `frontend/src/components/TaglinePrompt.tsx`
- Create: `frontend/src/components/TaglinePrompt.test.tsx`
- Modify: `frontend/src/components/CharacterEditor.tsx` (render popup after single new-character import)
- Modify: `frontend/src/index.css` (small centered-modal style)

**Interfaces:**
- Consumes: `api.setCharacterTagline`, `api.generateCharacterTagline` (Task 5).

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/components/TaglinePrompt.test.tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi, test, expect, beforeEach } from "vitest";
import { TaglinePrompt } from "./TaglinePrompt";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { setCharacterTagline: vi.fn(), generateCharacterTagline: vi.fn() },
}));

beforeEach(() => vi.clearAllMocks());

test("typing then Save calls PUT and not generate", async () => {
  (api.setCharacterTagline as any).mockResolvedValue({ ok: true });
  const onClose = vi.fn();
  render(<TaglinePrompt wid="w" cid="aese" name="Aese" onClose={onClose} />);
  fireEvent.change(screen.getByLabelText("Tagline"), { target: { value: "A snowleopardgirl." } });
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() => expect(api.setCharacterTagline).toHaveBeenCalledWith("w", "aese", "A snowleopardgirl."));
  expect(api.generateCharacterTagline).not.toHaveBeenCalled();
  await waitFor(() => expect(onClose).toHaveBeenCalled());
});

test("Generate fills the field from the endpoint", async () => {
  (api.generateCharacterTagline as any).mockResolvedValue({ tagline: "A generated line." });
  render(<TaglinePrompt wid="w" cid="aese" name="Aese" onClose={vi.fn()} />);
  fireEvent.click(screen.getByText("Generate"));
  await screen.findByDisplayValue("A generated line.");
});

test("Skip closes without saving", async () => {
  const onClose = vi.fn();
  render(<TaglinePrompt wid="w" cid="aese" name="Aese" onClose={onClose} />);
  fireEvent.click(screen.getByText("Skip"));
  expect(onClose).toHaveBeenCalled();
  expect(api.setCharacterTagline).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Run to verify it fails**

Run (from `frontend/`): `npx vitest run TaglinePrompt`
Expected: FAIL — module `./TaglinePrompt` not found.

- [ ] **Step 3: Write the component**

```tsx
// frontend/src/components/TaglinePrompt.tsx
import { useState } from "react";
import { api } from "../api/client";

export function TaglinePrompt({ wid, cid, name, onClose }:
  { wid: string; cid: string; name: string; onClose: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      const { tagline } = await api.generateCharacterTagline(wid, cid);
      setText(tagline);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (text.trim()) {
      try {
        await api.setCharacterTagline(wid, cid, text.trim());
      } catch (err: any) {
        setError(err.detail ?? String(err));
        return;
      }
    }
    onClose();
  }

  return (
    <div className="tagline-modal-backdrop" role="dialog" aria-label="Set tagline">
      <div className="tagline-modal">
        <h3>Tagline for {name}</h3>
        <p className="field-hint">
          A one-sentence identity for the off-scene cast. Type your own, or generate one.
        </p>
        <textarea aria-label="Tagline" value={text} rows={2}
                  onChange={(e) => setText(e.target.value)} />
        {error && <div className="banner">{error}</div>}
        <div className="form-actions">
          <button className="subtle" type="button" disabled={busy} onClick={generate}>
            {busy ? "Generating…" : "Generate"}
          </button>
          <button className="primary" type="button" onClick={save}>Save</button>
          <button className="subtle" type="button" onClick={onClose}>Skip</button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run to verify it passes**

Run (from `frontend/`): `npx vitest run TaglinePrompt`
Expected: PASS (3 tests).

- [ ] **Step 5: Render the popup after a single new-character import**

In `CharacterEditor.tsx`, import it (`:4`):

```typescript
import { TaglinePrompt } from "./TaglinePrompt";
```

Add state after `tagline` (Task 5):

```typescript
  const [taglinePrompt, setTaglinePrompt] = useState<{ cid: string; name: string } | null>(null);
```

In `onImport` (`:363-366`), the single-import branch, set the prompt after opening:

```typescript
    else if (imported.length === 1) {
      // single import: open the card so its localize progress shows inline
      await openDetail(imported[0].cid);
      const c = chars.find((x) => x.id === imported[0].cid);
      setTaglinePrompt({ cid: imported[0].cid, name: c?.name ?? imported[0].cid });
      await runLocalize(imported[0].cid, imported[0].version);
    }
```

Note: `chars` is refreshed by the `await reload()` earlier in `onImport` (`:361`), so the new character is present. In `downloadFromChub` (`:378-382`), after `setImportMsg(...)`:

```typescript
      setTaglinePrompt({ cid: result.character, name: result.character });
```

Render the popup at the top of the returned JSX in the **grid** and **detail** branches — simplest is to render it once, unconditionally, before the mode branches. At the start of the component's `return` for the grid branch (`:503`, right inside the outer `<div className="character-editor">`) is mode-specific; instead render it in all three branches by adding, immediately after each `<div className="character-editor">` open tag:

```tsx
        {taglinePrompt && (
          <TaglinePrompt wid={wid} cid={taglinePrompt.cid} name={taglinePrompt.name}
                         onClose={() => setTaglinePrompt(null)} />
        )}
```

(Add this line in all three `return` branches: grid `:505`, detail `:566`, edit `:678`. The backdrop is `position: fixed`, so placement within the tree doesn't affect layout.)

- [ ] **Step 6: Add modal CSS**

Append to `frontend/src/index.css`:

```css
.tagline-modal-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; z-index: 30; }
.tagline-modal { background: var(--panel, #1b1b1b); border: 1px solid var(--muted); border-radius: 8px; padding: 20px; width: min(520px, 92vw); }
.tagline-modal h3 { font-family: var(--font-display); margin: 0 0 6px; }
.tagline-modal textarea { width: 100%; }
```

- [ ] **Step 7: Run frontend tests + typecheck**

Run (from `frontend/`): `npx vitest run` then `npx tsc -b`
Expected: PASS; no type errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/TaglinePrompt.tsx frontend/src/components/TaglinePrompt.test.tsx frontend/src/components/CharacterEditor.tsx frontend/src/index.css
git commit -m "feat(taglines): input-or-generate popup on character import"
```

---

### Task 7: Author taglines for the existing 67 characters (data)

**Files:**
- Create (in the user's store, NOT the repo): `~/.grimoire/worlds/onboarding-sandbox/characters/<c>/tagline.md`

This is a one-time data pass, not committed. It has no automated test — verify by reading back the scene context.

- [ ] **Step 1: List characters and their cards**

Run:

```bash
python - <<'PY'
import json
from pathlib import Path
base = Path.home()/".grimoire"/"worlds"/"onboarding-sandbox"/"characters"
for ch in sorted(p.name for p in base.iterdir() if p.is_dir()):
    print(ch)
PY
```

- [ ] **Step 2: For each character, read the default card, write a one-sentence `tagline.md`**

Using `store.taglines.write`, author each tagline by reading the card's name/description/personality (PList) and distilling one sentence (species + defining trait). Example driver (edit the `TAGLINES` dict as you read each card):

```bash
backend/.venv/Scripts/python.exe - <<'PY'
import os
os.environ.setdefault("GRIMOIRE_HOME", str(__import__("pathlib").Path.home()/".grimoire"))
from grimoire.store import worlds, taglines
wroot = worlds.world_root("onboarding-sandbox")
TAGLINES = {
    "aese": "A silent, servile snowleopardgirl who communicates through written notes.",
    # ... one line per character id ...
}
for cid, text in TAGLINES.items():
    taglines.write(wroot, cid, text)
print(f"wrote {len(TAGLINES)} taglines")
PY
```

- [ ] **Step 3: Verify tier 3 populates**

Run:

```bash
backend/.venv/Scripts/python.exe - <<'PY'
import os
os.environ.setdefault("GRIMOIRE_HOME", str(__import__("pathlib").Path.home()/".grimoire"))
from grimoire.store import context, appearances
# quiet-hand is the campaign; pick its active scene id from the scenes list
from grimoire.store import scenes, campaigns
cid = "quiet-hand"
sid = scenes.list_scenes(cid)[0]["id"]
for label, text in [(s["label"], s["text"]) for s in context.context_sections(cid, sid)]:
    if label == "Off-scene cast":
        print(text[:800])
PY
```

Expected: the "## Known to exist" section lists the briefed characters. (No commit — data only.)

---

### Task 8: Phase 2 — generate dossiers at absorb

**Files:**
- Modify: `backend/src/grimoire/routes.py:1166-1187` (`post_absorb`)
- Test: `backend/tests/test_routes.py` (new absorb dossier test)

**Interfaces:**
- Consumes: `store.dossiers` (Task 2), `store.appearances.scene_cast`, `store.chronicle.transcript_text`.

- [ ] **Step 1: Write the failing test**

Add to `test_routes.py` (near the other absorb tests):

```python
def test_absorb_writes_dossier_for_present_character(client):
    wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    # bring a character into the world and seat it in the scene
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Aese", "version_name": "main"})
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": "aese", "version": "main", "role": "npc"})
    # the fake returns this text for BOTH .stream (seeds a scene message) and .complete
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    client.app.dependency_overrides[routes.get_openrouter] = \
        lambda: FakeOpenRouterComplete("Aese is a shy snowleopardgirl who now trusts the owner.")
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "hi"})  # gives the scene messages
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    assert r.status_code == 200
    croot = store.campaigns.campaign_root(cid)
    assert "Aese is a shy snowleopardgirl" in store.dossiers.read(croot, "aese")
```

Routes confirmed against `test_routes.py`: cast is `POST .../scenes/{sid}/cast` (`test_routes.py:145`), and a scene gets messages via `POST .../scenes/{sid}/chat` (`:497`) — there is no plain message-POST. `FakeOpenRouterComplete` (`:973`) implements both `.stream` and `.complete`, so it serves the chat seed and the absorb/dossier calls.

- [ ] **Step 2: Run to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py::test_absorb_writes_dossier_for_present_character -q`
Expected: FAIL — dossier is empty.

- [ ] **Step 3: Extend `post_absorb`**

In `routes.py`, inside `post_absorb`, after `edits = store.absorb.materialize(cid, sid, parsed)` (`:1184`) and before the `return`, add:

```python
    # Phase 2: refresh each present character's campaign dossier from this scene.
    croot = store.campaigns.campaign_root(cid)
    transcript = store.chronicle.transcript_text(scene["messages"])
    for a in store.appearances.scene_cast(cid, sid):
        if a["kind"] != "characters":
            continue
        try:
            name = store.characters.read_character(croot, a["id"])["meta"].get("name", a["id"])
            msgs = store.dossiers.build_prompt(name, store.dossiers.read(croot, a["id"]), transcript)
            text = await client.complete(msgs, cfg["model"], cfg["openrouter_key"])
            store.dossiers.write(croot, a["id"], store.dossiers.parse_output(text))
        except Exception:  # noqa: BLE001 — a dossier failure must not fail absorb
            continue
```

- [ ] **Step 4: Run the test + full suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py::test_absorb_writes_dossier_for_present_character -q`
Expected: PASS.
Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(dossiers): generate campaign dossiers at absorb (tier 2)"
```

---

### Task 9: File the follow-up issue

- [ ] **Step 1: Create the GitHub issue**

```bash
gh issue create --milestone "Context Building & Inspector" \
  --title "Make off-scene 'Known to exist' cast contextual instead of listing every world character" \
  --body "The off-scene cast tier 3 (\"Known to exist\", context._cast_directory) lists every world character with a tagline. This should be scoped to characters relevant to the current scene (mentioned by present characters, shared location, or shared group/faction). Blocked on groups/factions existing. Cross-refs milestone: Cast & Appearances. See docs/superpowers/specs/2026-07-01-off-scene-cast-taglines-dossiers-design.md."
```

Expected: prints the new issue URL. (No commit.)

---

## Self-Review

**Spec coverage:**
- Tagline artifact (world, character-level, no staleness) → Task 1. ✓
- Dossier artifact (campaign) → Task 2. ✓
- Context tiers read the new artifacts → Task 3. ✓
- Remove briefs + routes + `_copy_actor` copy → Task 4. ✓
- Tagline routes (GET/PUT/POST generate) → Task 4. ✓
- Import input-or-generate popup, "avoid generating if user provides one" → Task 6 (test asserts `generate` not called when typed). ✓
- Hand-edit/regenerate in CharacterEditor → Task 5. ✓
- Author 67 existing taglines → Task 7. ✓
- Dossiers at absorb → Task 8. ✓
- Follow-up issue → Task 9. ✓

**Placeholder scan:** Task 7 (author the 67 taglines) is the only non-literal step — inherent to a data pass, with explicit list/verify steps. All route paths are confirmed against `test_routes.py`. No "TBD/handle errors/similar to" placeholders elsewhere.

**Type consistency:** `taglines.read/write/build_prompt/parse_output` and `dossiers.read/write/build_prompt/parse_output` are used with identical signatures across Tasks 1-3-4-8. Route shapes (`{"tagline": ...}`, `{"ok": true}`) match between backend (Task 4) and frontend (Tasks 5-6). `TaglinePrompt` props (`wid, cid, name, onClose`) match between definition (Task 6 Step 3) and use (Task 6 Step 5).
