# Prose Style Guides Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a scene's prose tone be steered by a named Style Guide, selectable at three nested levels (global default → campaign default → sticky scene override), with 7 genre presets shipped in code and user-authored guides editable in-app.

**Architecture:** A new `store/styles.py` module merges built-in `.md` files (`templates/styles/`, resolved via the existing `prompts.templates_dir()`) with user-authored `.md` files (`<GRIMOIRE_HOME>/styles/`) into one CRUD-able list (mutation gated to custom entries). A new template section (`templates/scene/sections/prose_style.j2`) is spliced into `system.j2` right after the global system prompt; `context.py` resolves the active style per-request (scene → campaign → global, silently falling back on a missing id) and feeds it into the render data. Three new frontend surfaces (a global list/detail editor, a Configuration dropdown, and per-campaign/per-scene pickers mirroring the existing `CalendarConfig` pattern) let a user pick a style at each level.

**Tech Stack:** FastAPI + Pydantic (backend), Jinja2 (prompt templates), React + TypeScript + Vitest (frontend), pytest (backend tests).

**Design doc:** `docs/superpowers/specs/2026-07-12-prose-style-guides-design.md`

## Global Constraints

- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`; style tests additionally isolate built-ins via `monkeypatch.setenv("GRIMOIRE_TEMPLATES", tmp_path / "templates")`.
- Backend test run: `backend/.venv/Scripts/python.exe -m pytest backend -q`.
- Frontend test run: from `frontend/`, `npx vitest run` and `npx tsc -b` (running vitest from the repo root skips `frontend/vitest.config.ts` and disables `globals`, failing every mock-based test).
- No real world/campaign/character names in code, tests, or commit messages — reuse existing placeholders (Seraphine, Mara, Winifred, Realm, Saltmarch).
- Never commit anything under the data store (`~/.grimoire` / `GRIMOIRE_HOME`). The 7 built-in style files are generic genre guidance with no private content and are the only new files committed to the repo's `templates/` tree.
- Default to no comments in code; when one is warranted, one short line only.
- Pydantic usage stays v1/v2-agnostic: plain `BaseModel` fields, dump via the existing `routes._dump` helper — never call `.model_dump()` directly.

## Setup

This branch is implemented in an isolated git worktree (see `superpowers:using-git-worktrees`) rather than the primary checkout, so the main working tree stays free for other work. Before Task 1: create a worktree for a new branch (e.g. `feature/prose-style-guides`) off `main`, and run every step in this plan from inside that worktree.

---

### Task 1: `store/styles.py` — built-in + custom style records

**Files:**
- Create: `backend/src/grimoire/store/styles.py`
- Test: `backend/tests/test_styles.py`

**Interfaces:**
- Consumes: `grimoire.prompts.templates_dir()`, `grimoire.store.paths.{home, natural_key, slugify, uniquify}`, `grimoire.store.frontmatter.{dump_frontmatter, parse_frontmatter}`.
- Produces: `StyleNotFound`, `BuiltInStyleImmutable` (exceptions); `list_styles() -> list[dict]` (each `{id, name, description, tags, built_in}`); `read_style(sid) -> {"meta": {...same shape...}, "body": str}`; `create_style(name, description="", tags=None, body="") -> str` (returns new id); `update_style(sid, *, name=None, description=None, tags=None, body=None) -> None`; `delete_style(sid) -> None`; `duplicate_style(sid) -> str` (returns new id); `resolve_style(*, scene_style_id="", campaign_style_id="", default_style_id="") -> dict | None` (same shape as `read_style`'s return, or `None`).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_styles.py`:

```python
import pytest

from grimoire.store import styles


def _write(dir_path, sid, name, description="", tags="", body=""):
    dir_path.mkdir(parents=True, exist_ok=True)
    text = f"---\nname: {name}\ndescription: {description}\ntags: {tags}\n---\n\n{body}"
    (dir_path / f"{sid}.md").write_text(text, encoding="utf-8")


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GRIMOIRE_TEMPLATES", str(tmp_path / "templates"))


def test_list_merges_builtin_and_custom(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "styles", "gothic-horror", "Gothic Horror",
           "Atmospheric dread.", "horror,gothic", "Atmosphere first.")
    _write(tmp_path / "home" / "styles", "my-style", "My Style",
           "A custom one.", "custom", "Write it my way.")

    items = {s["id"]: s for s in styles.list_styles()}
    assert items["gothic-horror"]["built_in"] is True
    assert items["gothic-horror"]["name"] == "Gothic Horror"
    assert items["gothic-horror"]["tags"] == ["horror", "gothic"]
    assert items["my-style"]["built_in"] is False
    assert items["my-style"]["description"] == "A custom one."


def test_create_read_update_delete_custom(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    sid = styles.create_style("Cozy Mystery", "Gentle whodunits.", ["cozy", "mystery"], "Keep it warm.")
    got = styles.read_style(sid)
    assert got["meta"]["name"] == "Cozy Mystery"
    assert got["meta"]["tags"] == ["cozy", "mystery"]
    assert got["meta"]["built_in"] is False
    assert got["body"].strip() == "Keep it warm."

    styles.update_style(sid, body="Keep it warmer.")
    assert styles.read_style(sid)["body"].strip() == "Keep it warmer."

    styles.delete_style(sid)
    with pytest.raises(styles.StyleNotFound):
        styles.read_style(sid)


def test_built_in_cannot_be_updated_or_deleted(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "styles", "gothic-horror", "Gothic Horror")

    with pytest.raises(styles.BuiltInStyleImmutable):
        styles.update_style("gothic-horror", body="nope")
    with pytest.raises(styles.BuiltInStyleImmutable):
        styles.delete_style("gothic-horror")


def test_duplicate_creates_an_editable_custom_copy(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "styles", "gothic-horror", "Gothic Horror",
           "Atmospheric dread.", "horror,gothic", "Atmosphere first.")

    new_id = styles.duplicate_style("gothic-horror")
    assert new_id != "gothic-horror"
    copy = styles.read_style(new_id)
    assert copy["meta"]["built_in"] is False
    assert copy["meta"]["name"] == "Gothic Horror (copy)"
    assert copy["body"].strip() == "Atmosphere first."
    styles.update_style(new_id, body="edited")
    assert styles.read_style(new_id)["body"].strip() == "edited"


def test_ids_are_unique_across_builtin_and_custom(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "styles", "cozy-mystery", "Cozy Mystery")

    sid = styles.create_style("Cozy Mystery")
    assert sid == "cozy-mystery-2"


def test_a_malformed_custom_file_is_skipped_without_crashing_the_list(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    d = tmp_path / "home" / "styles"
    d.mkdir(parents=True)
    (d / "broken.md").write_bytes(b"\xff\xfe not valid utf-8 \x00\x01")
    _write(d, "fine", "Fine")

    ids = {s["id"] for s in styles.list_styles()}
    assert ids == {"fine"}


def test_resolve_style_falls_back_scene_then_campaign_then_default(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "styles", "noir", "Noir", body="noir text")
    _write(tmp_path / "templates" / "styles", "pulp", "Pulp", body="pulp text")

    r = styles.resolve_style(scene_style_id="noir", campaign_style_id="pulp", default_style_id="pulp")
    assert r["meta"]["id"] == "noir"
    r = styles.resolve_style(scene_style_id="", campaign_style_id="pulp", default_style_id="noir")
    assert r["meta"]["id"] == "pulp"
    r = styles.resolve_style(scene_style_id="", campaign_style_id="", default_style_id="noir")
    assert r["meta"]["id"] == "noir"
    r = styles.resolve_style(scene_style_id="ghost", campaign_style_id="pulp", default_style_id="noir")
    assert r["meta"]["id"] == "pulp"
    assert styles.resolve_style() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_styles.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'grimoire.store.styles'`

- [ ] **Step 3: Implement `store/styles.py`**

```python
"""Prose style guides: named prompt-fragment presets selectable at three
nested levels (global default -> campaign default -> scene override).

Built-in genre presets ship as markdown+frontmatter files under
templates/styles/ (resolved via prompts.templates_dir(), the same
GRIMOIRE_TEMPLATES-aware path the Android build already relies on).
User-authored styles live in <GRIMOIRE_HOME>/styles/ and are the only ones
that can be created, edited, or deleted — mirrors the built-in/user-content
split in store/calendars/plugins.py.
"""

from __future__ import annotations

from pathlib import Path

from .. import prompts
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import home, natural_key, slugify, uniquify


class StyleNotFound(Exception):
    pass


class BuiltInStyleImmutable(Exception):
    pass


def _safe(sid: str) -> bool:
    return sid not in ("", ".", "..") and "/" not in sid and "\\" not in sid


def _builtin_dir() -> Path:
    return prompts.templates_dir() / "styles"


def _custom_dir() -> Path:
    return home() / "styles"


def _builtin_path(sid: str) -> Path:
    return _builtin_dir() / f"{sid}.md"


def _custom_path(sid: str) -> Path:
    return _custom_dir() / f"{sid}.md"


def _tags_list(s: str) -> list[str]:
    return [t for t in s.split(",") if t]


def _meta_dict(sid: str, meta: dict, built_in: bool) -> dict:
    return {"id": sid, "name": meta.get("name", sid), "description": meta.get("description", ""),
            "tags": _tags_list(meta.get("tags", "")), "built_in": built_in}


def _find_path(sid: str) -> tuple[Path, bool] | None:
    if not _safe(sid):
        return None
    p = _custom_path(sid)
    if p.exists():
        return p, False
    p = _builtin_path(sid)
    if p.exists():
        return p, True
    return None


def _list_dir(directory: Path, built_in: bool) -> list[dict]:
    out: list[dict] = []
    if not directory.exists():
        return out
    for p in sorted(directory.glob("*.md")):
        try:
            meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue  # a broken file is skipped, not fatal — same as calendar plugins
        out.append(_meta_dict(p.stem, meta, built_in))
    return out


def list_styles() -> list[dict]:
    """Every style guide (built-in + user-authored), for a UI picker."""
    items = _list_dir(_builtin_dir(), built_in=True) + _list_dir(_custom_dir(), built_in=False)
    items.sort(key=lambda m: natural_key(m["name"]))
    return items


def is_built_in(sid: str) -> bool:
    found = _find_path(sid)
    return found is not None and found[1]


def read_style(sid: str) -> dict:
    found = _find_path(sid)
    if found is None:
        raise StyleNotFound(sid)
    p, built_in = found
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    return {"meta": _meta_dict(sid, meta, built_in), "body": body}


def create_style(name: str, description: str = "", tags: list[str] | None = None, body: str = "") -> str:
    _custom_dir().mkdir(parents=True, exist_ok=True)

    def exists(c: str) -> bool:
        return _custom_path(c).exists() or _builtin_path(c).exists()

    sid = uniquify(slugify(name), exists)
    meta = {"name": name, "description": description, "tags": ",".join(tags or [])}
    _custom_path(sid).write_text(dump_frontmatter(meta, body), encoding="utf-8")
    return sid


def update_style(sid: str, *, name: str | None = None, description: str | None = None,
                 tags: list[str] | None = None, body: str | None = None) -> None:
    if is_built_in(sid):
        raise BuiltInStyleImmutable(sid)
    p = _custom_path(sid)
    if not _safe(sid) or not p.exists():
        raise StyleNotFound(sid)
    meta, cur_body = parse_frontmatter(p.read_text(encoding="utf-8"))
    if name is not None:
        meta["name"] = name
    if description is not None:
        meta["description"] = description
    if tags is not None:
        meta["tags"] = ",".join(tags)
    new_body = cur_body if body is None else body
    p.write_text(dump_frontmatter(meta, new_body), encoding="utf-8")


def delete_style(sid: str) -> None:
    if is_built_in(sid):
        raise BuiltInStyleImmutable(sid)
    p = _custom_path(sid)
    if not _safe(sid) or not p.exists():
        raise StyleNotFound(sid)
    p.unlink()


def duplicate_style(sid: str) -> str:
    src = read_style(sid)
    return create_style(f"{src['meta']['name']} (copy)", src["meta"]["description"],
                        src["meta"]["tags"], src["body"])


def resolve_style(*, scene_style_id: str = "", campaign_style_id: str = "",
                  default_style_id: str = "") -> dict | None:
    """scene override -> campaign default -> global default -> None. An id that
    doesn't resolve (deleted style, stale reference) is skipped silently and
    resolution falls back up the chain — never breaks generation."""
    for sid in (scene_style_id, campaign_style_id, default_style_id):
        if not sid:
            continue
        try:
            return read_style(sid)
        except StyleNotFound:
            continue
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_styles.py -q`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/styles.py backend/tests/test_styles.py
git commit -m "feat(styles): add built-in+custom style guide store module"
```

---

### Task 2: wire `store.styles` into `store/__init__.py`; ship the 7 built-in style files

**Files:**
- Modify: `backend/src/grimoire/store/__init__.py`
- Create: `templates/styles/gothic-horror.md`, `templates/styles/high-fantasy.md`, `templates/styles/modern-thriller.md`, `templates/styles/noir-detective.md`, `templates/styles/pulp-adventure.md`, `templates/styles/shoujo-romance.md`, `templates/styles/superheroes.md`

**Interfaces:**
- Consumes: `store.styles` (Task 1).
- Produces: `store.styles`, `store.StyleNotFound`, `store.BuiltInStyleImmutable` importable from the `grimoire.store` package (matches how `store.greetings`/`store.GreetingNotFound` are exposed); 7 real built-in style files on disk for later tasks/tests to exercise against the real `templates/` tree.

- [ ] **Step 1: Wire the module into `store/__init__.py`**

In `backend/src/grimoire/store/__init__.py`, add `styles` to the `from . import (...)` tuple (alphabetically after `scenes`):

```python
from . import (
    absorb, appearances, assets, campaigns, cards, changes, characters, chronicle,
    chub, context, dice, dossiers, entities, entity_schema, epub, fetch, greetings, groupstate, image_subjects,
    localize, lorebook, migrations, overlay, pcs, playing, playstate, plot, relationships,
    rolls, scene_ids, scene_refs, scenes, styles, suggest, sync, tags, taglines, thumbs, worlds,
)
```

Add the exception import next to the other `*NotFound` imports:

```python
from .styles import BuiltInStyleImmutable, StyleNotFound
```

Add both to `__all__` (after `"RollNotFound"`, before `"suggest"` — or anywhere in the list; keep it grouped near the other store-module entries):

```python
    "styles",
    "StyleNotFound",
    "BuiltInStyleImmutable",
```

- [ ] **Step 2: Write the 7 built-in style files**

Create `templates/styles/gothic-horror.md`:

```markdown
---
name: Gothic Horror
description: Atmospheric, slow-burn dread. Decaying settings, repressed emotion, the uncanny.
tags: horror,gothic,atmospheric
---

# Gothic Horror

Atmosphere first, action second. The world should feel old, damp, and watchful — buildings remember things, weather mirrors mood, and shadows do not behave.

## Pacing
- Let dread build through accumulation: small wrongnesses, then larger ones, before any direct confrontation.
- Linger on sensory detail (smell, cold, sound) before describing what's actually happening.
- Avoid action-movie momentum. A scene can be a held breath.

## Voice
- Formal, slightly archaic phrasing without becoming purple. Prefer concrete nouns over abstractions.
- Internal monologue is welcome — characters are often unreliable narrators of their own composure.
- Suggest more than you show. The reader's imagination is the engine; over-explaining a horror dispels it.

## Themes
- Inheritance, repression, the past intruding on the present.
- Sin and consequence — moral weight matters even in supernatural contexts.
- Bodies, places, and bloodlines as sites of haunting.

## Avoid
- Splatter or gross-out shock as the primary effect.
- Quippy dialogue or modern slang that punctures the tone.
- Tidy explanations that resolve the uncanny into mechanics.
```

Create `templates/styles/high-fantasy.md`:

```markdown
---
name: High Fantasy
description: Mythic, formal, epic in scope. Old languages, deep history, weighty stakes.
tags: fantasy,epic,mythic
---

# High Fantasy

The world is ancient and the stakes are written in legend. Even small acts can carry the resonance of prophecy if framed right.

## Pacing
- Allow scenes to breathe. A coronation, a parting, an oath — these deserve unhurried treatment.
- Weave in history and lineage; characters are heirs to something larger than themselves.
- Action scenes prize geography and tactics over close-camera frenzy.

## Voice
- Slightly elevated diction without going full archaic. Sentences can be long when they have somewhere to go.
- Names matter — give places, lineages, and oaths their full weight on first mention.
- Dialogue can be formal or vernacular depending on the speaker, but rarely casual.

## Themes
- Duty vs. desire, the burden of inheritance, the corruption of power.
- The mythic and the everyday touching: a sword in a barn, a prophet at a wedding.
- Friendship, sacrifice, and the long memory of the world.

## Avoid
- Modern slang, anachronistic humor, or postmodern winks.
- Magic-as-physics descriptions that drain it of mystery.
- Cynicism for its own sake — the story can be dark, but it should mean something.
```

Create `templates/styles/modern-thriller.md`:

```markdown
---
name: Modern Thriller
description: Terse, sensory, urgent. Short chapters, present-tense moments, propulsive tension.
tags: thriller,modern,action
---

# Modern Thriller

Forward motion is the law. Every scene is either escalating, complicating, or reversing — never coasting.

## Pacing
- Short paragraphs. Short scenes. Cut in late, leave early.
- Information arrives in fragments — characters and the reader piece it together in real time.
- Time pressure should be visible: deadlines, countdowns, geography in motion.

## Voice
- Sentences are lean. Verbs do the work. Adverbs are suspect.
- Sensory specificity over interior abstraction: heart rate, breath, the cold metal of a door handle.
- Dialogue is clipped and functional, with subtext doing the heavy lifting.

## Themes
- Competence under pressure; the protagonist's expertise is the lens.
- Trust, betrayal, and operational secrecy.
- Modern systems — surveillance, finance, networks — as the terrain of conflict.

## Avoid
- Long flashbacks or backstory dumps that arrest the present.
- Floral description that slows momentum.
- Tidy emotional resolutions; leave some wires live.
```

Create `templates/styles/noir-detective.md`:

```markdown
---
name: Noir Detective
description: Hardboiled, cynical, first-person. Smoke-curling prose and morally grey choices.
tags: noir,mystery,detective
---

# Noir Detective

A wet city at night, told from one weary set of eyes. Every character has something to hide and every favor has a price.

## Pacing
- Scenes turn on dialogue and observation. A long conversation in a bar can do more than a chase.
- Information leaks slowly: lies first, then partial truths, then the lie underneath the truth.
- The protagonist often arrives a step too late and pays for it.

## Voice
- Tight, declarative sentences with the occasional dry metaphor — "she lit a cigarette like she was charging a fuse."
- Internal monologue is dry, observational, faintly bitter. Self-pity is fatal.
- Sensory shorthand: rain on a brim, neon in puddles, a phone ringing in the next room.

## Themes
- Corruption, complicity, the smallness of any one person against an indifferent system.
- Compromised loyalty — friends, lovers, and clients are all suspects until they aren't.
- Money, vice, and the cost of doing the right thing in a city that won't notice.

## Avoid
- Heroic gestures without consequences.
- Romantic resolutions that flatten the moral weight of the case.
- Modern omniscient narration. Stay in the protagonist's POV.
```

Create `templates/styles/pulp-adventure.md`:

```markdown
---
name: Pulp Adventure
description: Fast, vivid, episodic. Cliffhangers, exotic locales, larger-than-life characters.
tags: adventure,pulp,action
---

# Pulp Adventure

Energy over polish. Each scene should pull the reader forward — danger, discovery, or a reversal — without lingering on sentiment.

## Pacing
- Move fast. Open in motion, end on a hook.
- Scenes are episodes: setup, complication, payoff, then on to the next ride.
- Use exclamation sparingly but vivid verbs constantly.

## Voice
- Punchy, declarative sentences mixed with the occasional ornate descriptor for an exotic locale or improbable contraption.
- Dialogue carries personality and conflict; let characters banter and posture.
- Narrative voice is unembarrassed: heroes are heroic, villains are villainous, and the world rewards nerve.

## Themes
- Exploration, lost ruins, secret societies, improbable inventions.
- Loyalty, courage, and the cost of glory.
- Good vs. evil drawn cleanly enough that the stakes are felt without being moralized.

## Avoid
- Modern cynicism or grimdark deconstruction.
- Long psychological interiors — keep characters acting, not ruminating.
- Realism that drains the scene of wonder.
```

Create `templates/styles/shoujo-romance.md`:

```markdown
---
name: Shoujo Romance
description: Tender, observational prose for slice-of-life teen romance. Small stakes treated with weight.
tags: romance,slice-of-life,school,contemporary
---

# Shoujo Romance

The form of the genre: small worlds, big feelings. A glance held a second too long is a plot point. The drama lives in proximity — who walks home with whom, who is in whose class, whose umbrella you share.

## Pacing
- Slow. A scene can be a single conversation in a classroom after the last bell.
- Internal thought belongs on the page. The POV character's wondering, second-guessing, and small embarrassments are the texture.
- Time of day matters: morning train light, the empty corridor after sixth period, the long blue hour walking home.

## Voice
- Third-limited or first person; pick one per scene and hold it.
- Concrete sensory detail over abstraction. The smell of chalk, the squeak of a sneaker on a polished floor, the chime that ends lunch.
- Dialogue is short, often interrupted. Japanese teenagers don't deliver speeches. Trail off; let silences carry weight.

## Themes
- First feelings: the embarrassment of being seen, the courage of being honest, the relief of being understood.
- Friendship and rivalry braided together. The best friend who also likes the same person; the rival who turns out to be lonely.
- Seasons and rituals. Each festival is an excuse to escalate or retreat.

## Avoid
- Explicit sexual content. Romance here is about wanting, not having; intimate scenes fade to white.
- Cynicism. Characters can be insecure, jealous, or wounded, but the narrator is not above them.
- Anglo idioms that puncture the setting. Honorifics (-san, -kun, -chan, senpai) are used in dialogue and address.
- Resolving feelings too quickly. Tension is the point; a confession midway through a scene should cost something.

## Specific touches encouraged
- Honorifics in address: Tachibana-san, Haruto-kun, Shibata-senpai.
- School routines: the bow at the start of class, cleaning duty at the end, the bell signaling lunch.
- Letting the weather mean something: rain shared under one umbrella, sun through classroom windows in May, snow on the walk home in February.
```

Create `templates/styles/superheroes.md`:

```markdown
---
name: Superheroes
description: Bold, kinetic cape fiction. Larger-than-life powers grounded by human stakes and secret identities.
tags: superheroes,action,comics,powers,adventure
---

# Superheroes

The genre runs on contrast: the extraordinary body and the ordinary heart. A person who can shatter concrete still worries about rent, a relationship, a secret. The action is spectacular; the story is personal.

## Pacing
- Action scenes are set pieces — choreographed, spatial, escalating. The reader should track where everyone is, what's breaking, and what the hero just tried that didn't work.
- Between fights, slow down hard. The civilian identity scenes need room to breathe: diners, rooftops, newsrooms, hospital waiting rooms.
- Cliffhangers between scenes, not just at chapter ends. Cut away at the moment of impact, mid-sentence, mid-fall.

## Voice
- Vivid and propulsive during action. Short sentences. Strong verbs. Sound effects implied through word choice — "cracked," "tore," "detonated" — rather than written out as onomatopoeia.
- Warmer and more internal during downtime. Let the POV character notice small human details: a coffee going cold, a bruise under a sleeve, the sound of a city at 3 AM.
- Banter matters. Heroes quip under pressure not because they're detached but because humor is armor. Villains monologue because they need to be understood.

## Themes
- Responsibility and cost. Power isn't free; every save has a thing the hero didn't get to in time.
- Identity and masks. The question is never just "who is the hero?" but "which version of themselves is real?"
- Collateral and consequence. Buildings fall. Bystanders get hurt. The world remembers what happened last time.
- Found family. Teams form not from duty rosters but from people who've bled together and kept showing up.

## Powers and Combat
- Establish rules, then play within them. A power that can do anything is boring. Limits create drama.
- Fights are problem-solving. The interesting question isn't "can they hit harder?" but "what do they try when hitting doesn't work?"
- Scale matters. Street-level and cosmic play differently — don't mix their tones without intention.

## Avoid
- Powers as video game stats. Don't quantify; dramatize.
- Grimdark nihilism that forgets why people put on the cape. Heroes can be exhausted, compromised, and broken — but the genre asks them to get back up.
- Origin stories as the whole story. The origin is a starting gun, not the race.
- Civilian scenes as filler between fights. If the human moments don't carry weight, the superhuman ones won't either.
```

- [ ] **Step 3: Verify the built-ins are discoverable against the real repo tree**

Run: `backend/.venv/Scripts/python.exe -c "from grimoire.store import styles; print(sorted(s['id'] for s in styles.list_styles()))"`
Expected: `['gothic-horror', 'high-fantasy', 'modern-thriller', 'noir-detective', 'pulp-adventure', 'shoujo-romance', 'superheroes']`

- [ ] **Step 4: Run the full backend test suite to confirm nothing broke**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS, no new failures

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/__init__.py templates/styles/
git commit -m "feat(styles): ship the 7 built-in genre style guides"
```

---

### Task 3: `/api/styles` CRUD + duplicate routes

**Files:**
- Modify: `backend/src/grimoire/routes.py`
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `store.styles` (Tasks 1–2).
- Produces: `GET/POST /api/styles`, `GET/PUT/DELETE /api/styles/{sid}`, `POST /api/styles/{sid}/duplicate`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_routes.py`:

```python
# ---- styles ----
def test_style_crud_and_builtin_list(client):
    r = client.get("/api/styles").json()
    ids = {s["id"] for s in r}
    assert "gothic-horror" in ids
    assert all(s["built_in"] for s in r if s["id"] == "gothic-horror")

    r = client.post("/api/styles", json={"name": "Cozy Mystery", "description": "Gentle.",
                                         "tags": ["cozy"], "body": "Keep it warm."})
    assert r.status_code == 200
    sid = r.json()["id"]

    detail = client.get(f"/api/styles/{sid}").json()
    assert detail["meta"]["name"] == "Cozy Mystery"
    assert detail["meta"]["built_in"] is False
    assert detail["body"].strip() == "Keep it warm."

    assert client.put(f"/api/styles/{sid}", json={"body": "Warmer."}).status_code == 200
    assert client.get(f"/api/styles/{sid}").json()["body"].strip() == "Warmer."

    assert client.delete(f"/api/styles/{sid}").status_code == 200
    assert client.get(f"/api/styles/{sid}").status_code == 404


def test_style_unknown_id_404(client):
    assert client.get("/api/styles/nope-not-real").status_code == 404
    assert client.put("/api/styles/nope-not-real", json={"body": "x"}).status_code == 404
    assert client.delete("/api/styles/nope-not-real").status_code == 404


def test_builtin_style_cannot_be_edited_or_deleted(client):
    assert client.put("/api/styles/gothic-horror", json={"body": "nope"}).status_code == 400
    assert client.delete("/api/styles/gothic-horror").status_code == 400


def test_duplicate_style_creates_an_editable_copy(client):
    r = client.post("/api/styles/gothic-horror/duplicate")
    assert r.status_code == 200
    new_id = r.json()["id"]
    detail = client.get(f"/api/styles/{new_id}").json()
    assert detail["meta"]["built_in"] is False
    assert detail["meta"]["name"] == "Gothic Horror (copy)"
    assert client.put(f"/api/styles/{new_id}", json={"body": "edited"}).status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -k style -q`
Expected: FAIL (404 on `/api/styles`, route not found)

- [ ] **Step 3: Add the models and routes**

In `backend/src/grimoire/routes.py`, add these models right after `class DataDirUpdate(BaseModel):` (~line 46):

```python
class StyleCreate(BaseModel):
    name: str
    description: str = ""
    tags: list[str] = []
    body: str = ""


class StyleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    body: str | None = None
```

Add the routes right after `put_data_dir` (~line 317), before the `# ---- worlds ----` comment:

```python
# ---- styles ----
@router.get("/styles")
def get_styles():
    return store.styles.list_styles()


@router.post("/styles")
def post_style(body: StyleCreate):
    return {"id": store.styles.create_style(body.name, body.description, body.tags, body.body)}


@router.get("/styles/{sid}")
def get_style(sid: str):
    try:
        return store.styles.read_style(sid)
    except store.styles.StyleNotFound:
        raise HTTPException(status_code=404, detail="style not found")


@router.put("/styles/{sid}")
def put_style(sid: str, body: StyleUpdate):
    try:
        store.styles.update_style(sid, name=body.name, description=body.description,
                                  tags=body.tags, body=body.body)
    except store.styles.StyleNotFound:
        raise HTTPException(status_code=404, detail="style not found")
    except store.styles.BuiltInStyleImmutable:
        raise HTTPException(status_code=400, detail="built-in styles can't be edited — duplicate it first")
    return {"ok": True}


@router.delete("/styles/{sid}")
def delete_style(sid: str):
    try:
        store.styles.delete_style(sid)
    except store.styles.StyleNotFound:
        raise HTTPException(status_code=404, detail="style not found")
    except store.styles.BuiltInStyleImmutable:
        raise HTTPException(status_code=400, detail="built-in styles can't be deleted")
    return {"ok": True}


@router.post("/styles/{sid}/duplicate")
def post_style_duplicate(sid: str):
    try:
        return {"id": store.styles.duplicate_style(sid)}
    except store.styles.StyleNotFound:
        raise HTTPException(status_code=404, detail="style not found")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -k style -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(styles): add /api/styles CRUD and duplicate routes"
```

---

### Task 4: config/campaign/scene style-selection routes

**Files:**
- Modify: `backend/src/grimoire/store/config.py`, `backend/src/grimoire/store/campaigns.py`, `backend/src/grimoire/store/scenes.py`, `backend/src/grimoire/routes.py`
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `store.styles` is not consumed here — this task only stores/serves *selected ids*, not resolved bodies (resolution is Task 5).
- Produces: `config.py` gains `default_style_id` in `_CONFIG_KEYS`/defaults; `campaigns.set_campaign_style(cid, style_id) -> None`; `scenes.set_style(cid, sid, style_id) -> None`; routes `GET/PUT /api/campaigns/{cid}/style`, `GET/PUT /api/campaigns/{cid}/scenes/{sid}/style`; `ConfigUpdate`/`_public_config` carry `default_style_id`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_routes.py`:

```python
def test_config_default_style_roundtrip(client):
    r = client.put("/api/config", json={"default_style_id": "gothic-horror"})
    assert r.json()["default_style_id"] == "gothic-horror"
    assert client.get("/api/config").json()["default_style_id"] == "gothic-horror"


def test_campaign_style_roundtrip(client):
    wid, cid = _campaign(client)
    assert client.get(f"/api/campaigns/{cid}/style").json() == {"style_id": ""}
    r = client.put(f"/api/campaigns/{cid}/style", json={"style_id": "noir-detective"})
    assert r.status_code == 200
    assert client.get(f"/api/campaigns/{cid}/style").json() == {"style_id": "noir-detective"}
    # visible on the campaign meta too, for free
    assert client.get(f"/api/campaigns/{cid}").json()["meta"]["style_id"] == "noir-detective"


def test_campaign_style_unknown_campaign_404(client):
    assert client.get("/api/campaigns/nope/style").status_code == 404
    assert client.put("/api/campaigns/nope/style", json={"style_id": "noir-detective"}).status_code == 404


def test_scene_style_roundtrip(client):
    wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Opening"}).json()["id"]
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/style").json() == {"style_id": ""}
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/style", json={"style_id": "pulp-adventure"})
    assert r.status_code == 200
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/style").json() == {"style_id": "pulp-adventure"}


def test_scene_style_unknown_scene_404(client):
    wid, cid = _campaign(client)
    assert client.get(f"/api/campaigns/{cid}/scenes/nope/style").status_code == 404
    assert client.put(f"/api/campaigns/{cid}/scenes/nope/style", json={"style_id": "pulp-adventure"}).status_code == 404
```

(This uses the existing `_campaign(client, name="Run")` helper and the `client.post(f"/api/campaigns/{cid}/scenes", json={"title": ...})` shape already used throughout `test_routes.py`, e.g. at its existing `test_campaign_copy_image_from_greeting` test.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -k "default_style or campaign_style or scene_style" -q`
Expected: FAIL (`default_style_id` missing from config response; 404s on the new routes)

- [ ] **Step 3: `config.py` — add `default_style_id`**

In `backend/src/grimoire/store/config.py`, extend `_CONFIG_KEYS`:

```python
_CONFIG_KEYS = ("openrouter_key", "model", "theme", "context_scan_depth", "system_prompt",
                "quote_color", "recap_depth", "user_label", "assistant_label",
                "provider", "claude_model", "default_style_id")
```

Extend the `defaults` dict in `read_config()`:

```python
    defaults = {"openrouter_key": "", "model": DEFAULT_MODEL, "theme": DEFAULT_THEME,
                "context_scan_depth": DEFAULT_SCAN_DEPTH, "system_prompt": "", "quote_color": "off",
                "recap_depth": DEFAULT_RECAP_DEPTH,
                "user_label": DEFAULT_USER_LABEL, "assistant_label": DEFAULT_ASSISTANT_LABEL,
                "provider": DEFAULT_PROVIDER, "claude_model": DEFAULT_CLAUDE_MODEL,
                "default_style_id": ""}
```

- [ ] **Step 4: `campaigns.py` — add `set_campaign_style`**

In `backend/src/grimoire/store/campaigns.py`, add right after `rename_campaign`:

```python
def set_campaign_style(cid: str, style_id: str) -> None:
    mp = campaign_meta_path(cid)
    if not mp.exists():
        raise CampaignNotFound(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta["style_id"] = style_id
    mp.write_text(dump_frontmatter(meta, body), encoding="utf-8")
```

- [ ] **Step 5: `scenes.py` — add `set_style`**

In `backend/src/grimoire/store/scenes.py`, add right after `set_pcless`:

```python
def set_style(cid: str, sid: str, style_id: str) -> None:
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["style_id"] = style_id
    p.write_text(dump_frontmatter(meta, body), encoding="utf-8")
```

- [ ] **Step 6: `routes.py` — wire config field + campaign/scene style routes**

Add `default_style_id: str | None = None` to `ConfigUpdate` (~line 41, after `claude_model`):

```python
class ConfigUpdate(BaseModel):
    model: str | None = None
    theme: str | None = None
    openrouter_key: str | None = None
    system_prompt: str | None = None
    quote_color: str | None = None
    user_label: str | None = None
    assistant_label: str | None = None
    provider: Literal["openrouter", "claude"] | None = None
    claude_model: str | None = None
    default_style_id: str | None = None
```

Add the field to `_public_config` (~line 286):

```python
def _public_config(cfg: dict[str, str]) -> dict:
    return {"model": cfg["model"], "theme": cfg["theme"], "key_set": bool(cfg["openrouter_key"]),
            "system_prompt": cfg.get("system_prompt", ""), "quote_color": cfg.get("quote_color", "off"),
            "user_label": cfg.get("user_label", "You"),
            "assistant_label": cfg.get("assistant_label", "Grimoire"),
            "provider": cfg.get("provider", "openrouter"),
            "claude_model": cfg.get("claude_model", "opus"),
            "default_style_id": cfg.get("default_style_id", "")}
```

Add the `StyleSelect` model next to `StyleCreate`/`StyleUpdate` (from Task 3):

```python
class StyleSelect(BaseModel):
    style_id: str = ""
```

Add the campaign style routes right after `delete_campaign` (~line 1399):

```python
@router.get("/campaigns/{cid}/style")
def get_campaign_style(cid: str):
    try:
        meta = store.campaigns.read_campaign(cid)["meta"]
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {"style_id": meta.get("style_id", "")}


@router.put("/campaigns/{cid}/style")
def put_campaign_style(cid: str, body: StyleSelect):
    try:
        store.campaigns.set_campaign_style(cid, body.style_id)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {"ok": True}
```

Add the scene style routes right after `put_scene_datetime` (~line 2139), before `post_scene_roll`:

```python
@router.get("/campaigns/{cid}/scenes/{sid}/style")
def get_scene_style(cid: str, sid: str):
    scene = _require_scene(cid, sid)
    return {"style_id": scene["meta"].get("style_id", "")}


@router.put("/campaigns/{cid}/scenes/{sid}/style")
def put_scene_style(cid: str, sid: str, body: StyleSelect):
    _require_scene(cid, sid)
    store.scenes.set_style(cid, sid, body.style_id)
    return {"ok": True}
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q`
Expected: PASS, no failures

- [ ] **Step 8: Commit**

```bash
git add backend/src/grimoire/store/config.py backend/src/grimoire/store/campaigns.py \
        backend/src/grimoire/store/scenes.py backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(styles): add global/campaign/scene style-selection routes"
```

---

### Task 5: template section + context builder resolution

**Files:**
- Create: `templates/scene/sections/prose_style.j2`
- Modify: `templates/scene/system.j2`, `backend/src/grimoire/store/context.py`, `templates/README.md`, `scripts/verify_templates.py`
- Test: `backend/tests/test_context.py`

**Interfaces:**
- Consumes: `store.styles.resolve_style` (Task 1), `scene["meta"]["style_id"]` / `campaign_meta["style_id"]` / `config default_style_id` (Task 4).
- Produces: `system.j2` gains a `prose_style_name`/`prose_style_body` data-var contract; `context._SECTIONS` and `context_sections()` surface a "Prose style" breakdown row for the frontend context inspector.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_context.py`:

```python
def test_prose_style_resolves_scene_then_campaign_then_global(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "hello")

    # nothing set anywhere -> no prose-style block
    assert "Prose style" not in context.build_messages(cid, sid)[0]["content"]

    config.write_config(default_style_id="gothic-horror")
    text = context.build_messages(cid, sid)[0]["content"]
    assert "Prose style: Gothic Horror" in text

    campaigns.set_campaign_style(cid, "noir-detective")
    text = context.build_messages(cid, sid)[0]["content"]
    assert "Prose style: Noir Detective" in text
    assert "Gothic Horror" not in text

    scenes.set_style(cid, sid, "pulp-adventure")
    text = context.build_messages(cid, sid)[0]["content"]
    assert "Prose style: Pulp Adventure" in text
    assert "Noir Detective" not in text

    # a stale/unknown scene override falls back to the campaign default
    scenes.set_style(cid, sid, "does-not-exist")
    text = context.build_messages(cid, sid)[0]["content"]
    assert "Prose style: Noir Detective" in text
```

(`config`, `campaigns`, `scenes` are already imported near the top of `test_context.py` for the `_campaign` helper — no new imports needed. This test relies on the real built-in style files shipped in Task 2, so it does not monkeypatch `GRIMOIRE_TEMPLATES`.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -k prose_style -q`
Expected: FAIL (`jinja2.exceptions.UndefinedError: 'prose_style_body' is undefined` once the template references it, or an assertion failure before that if the template doesn't exist yet — write the template first if you hit a `TemplateNotFound`)

- [ ] **Step 3: Create the template section**

Create `templates/scene/sections/prose_style.j2`:

```jinja
{#- The resolved prose-style guide: scene override -> campaign default ->
    global default -> none. Vars: prose_style_name (str), prose_style_body
    (str) — both "" when no style resolves. -#}
{%- if prose_style_body %}# Prose style: {{ prose_style_name }}
{{ prose_style_body }}{% endif -%}
```

- [ ] **Step 4: Wire it into `system.j2`**

In `templates/scene/system.j2`, insert right after the `global_system_prompt.j2` block and before `card_system_prompts.j2`:

```jinja
{%- set s -%}{%- include "scene/sections/global_system_prompt.j2" -%}{%- endset -%}
{%- if s.strip() -%}{%- set _ = sections.append(s.strip()) -%}{%- endif -%}

{%- set s -%}{%- include "scene/sections/prose_style.j2" -%}{%- endset -%}
{%- if s.strip() -%}{%- set _ = sections.append(s.strip()) -%}{%- endif -%}

{%- set s -%}{%- include "scene/sections/card_system_prompts.j2" -%}{%- endset -%}
{%- if s.strip() -%}{%- set _ = sections.append(s.strip()) -%}{%- endif -%}
```

Also update the header comment's "Data vars" list at the top of `system.j2` to add `prose_style_name, prose_style_body` right after `global_system_prompt`.

- [ ] **Step 5: Resolve the style in `context.py`**

Add `styles` to the import tuple near the top of `backend/src/grimoire/store/context.py`:

```python
from . import (appearances, calendars, campaigns, characters, chronicle,
               config, dossiers, entities, groupstate, overlay, pcs, playstate, plot, relationships, scenes, styles)
```

In `_assemble` (~line 356-373), replace:

```python
    offscene_active, offscene_known = _cast_directory_data(croot, cid, sid)
    activated_wi = _world_info(cid, recent_text, exclude, frozenset(present))
    data = {
        "opener": False, "pcless": pcless, "story_full": bool(full_recap),
        "global_system_prompt": config.read_config().get("system_prompt", ""),
        "npc_cards": npc_cards,
```

with:

```python
    cfg = config.read_config()
    campaign_meta = campaigns.read_campaign(cid)["meta"]
    resolved_style = styles.resolve_style(
        scene_style_id=scene["meta"].get("style_id", ""),
        campaign_style_id=campaign_meta.get("style_id", ""),
        default_style_id=cfg.get("default_style_id", ""))
    offscene_active, offscene_known = _cast_directory_data(croot, cid, sid)
    activated_wi = _world_info(cid, recent_text, exclude, frozenset(present))
    data = {
        "opener": False, "pcless": pcless, "story_full": bool(full_recap),
        "global_system_prompt": cfg.get("system_prompt", ""),
        "prose_style_name": resolved_style["meta"]["name"] if resolved_style else "",
        "prose_style_body": resolved_style["body"].strip() if resolved_style else "",
        "npc_cards": npc_cards,
```

Update `_SECTIONS` (~line 418) to add the new row right after the global system prompt:

```python
_SECTIONS = [
    ("Global system prompt", "scene/sections/global_system_prompt.j2", False),
    ("Prose style", "scene/sections/prose_style.j2", False),
    ("System prompt", "scene/sections/card_system_prompts.j2", False),
```

- [ ] **Step 6: Update `templates/README.md`**

In `templates/README.md`, in the `system.j2` data vars list, add a bullet right after `global_system_prompt`:

```markdown
- `global_system_prompt` — config `system_prompt`
- `prose_style_name`, `prose_style_body` — the resolved style guide (scene
  `style_id` override → campaign default → global `default_style_id`),
  looked up via `store/styles.py:resolve_style()`; both `""` when none resolves
```

- [ ] **Step 7: Update `scripts/verify_templates.py`**

Add `styles` to the store import block (~line 149):

```python
from grimoire.store import (calendars, campaigns, characters, config, dossiers as dstore,  # noqa: E402
                            entities, groupstate, pcs, playstate, plot, scenes, styles,
                            taglines as tstore, worlds)
```

In `gather()` (~line 219), right after `cfg = config.read_config()`:

```python
    cfg = config.read_config()
    scene = scenes.read_scene(cid, scene_id)
```

leave those two lines as-is, and change the `return` statement (~line 337) from:

```python
    return {"global_system_prompt": cfg.get("system_prompt", ""), "npc_cards": npc_cards,
```

to:

```python
    campaign_meta = campaigns.read_campaign(cid)["meta"]
    resolved_style = styles.resolve_style(
        scene_style_id=scene["meta"].get("style_id", ""),
        campaign_style_id=campaign_meta.get("style_id", ""),
        default_style_id=cfg.get("default_style_id", ""))
    return {"global_system_prompt": cfg.get("system_prompt", ""),
            "prose_style_name": resolved_style["meta"]["name"] if resolved_style else "",
            "prose_style_body": resolved_style["body"].strip() if resolved_style else "",
            "npc_cards": npc_cards,
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q`
Expected: PASS, no failures

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS, no new failures (confirms `_SECTIONS`/context-breakdown consumers still work)

Run: `backend/.venv/Scripts/python.exe scripts/verify_templates.py`
Expected: exits 0, prints a summary with `0` failures (proves the golden fixtures and the live builder still agree byte-for-byte with no style configured)

- [ ] **Step 9: Commit**

```bash
git add templates/scene/sections/prose_style.j2 templates/scene/system.j2 \
        backend/src/grimoire/store/context.py templates/README.md scripts/verify_templates.py \
        backend/tests/test_context.py
git commit -m "feat(styles): splice the resolved prose style into the scene system prompt"
```

---

### Task 6: `api/client.ts` — types and functions

**Files:**
- Modify: `frontend/src/api/client.ts`

**Interfaces:**
- Consumes: backend routes from Tasks 3–4.
- Produces: `Style`, `StyleDetail`, `StyleDraft` types; `api.listStyles/createStyle/readStyle/updateStyle/deleteStyle/duplicateStyle/getCampaignStyle/setCampaignStyle/getSceneStyle/setSceneStyle`; `Config.default_style_id`, `CampaignMeta`/`SceneMeta` unaffected (style ids are fetched via the dedicated endpoints, not embedded in the list types); `putConfig` accepts `default_style_id`.

- [ ] **Step 1: Add types**

In `frontend/src/api/client.ts`, right after the `Config` type (~line 49):

```typescript
export type Config = {
  model: string; theme: string; key_set: boolean; system_prompt: string;
  quote_color: string; user_label: string; assistant_label: string;
  provider: string; claude_model: string; default_style_id: string;
};
```

Add the style types right after `GreetingDraft` (~line 169):

```typescript
export type Style = { id: string; name: string; description: string; tags: string[]; built_in: boolean };
export type StyleDetail = { meta: Style; body: string };
export type StyleDraft = { name: string; description?: string; tags?: string[]; body?: string };
```

- [ ] **Step 2: Extend `putConfig`'s body type**

Change the `putConfig` signature (~line 302):

```typescript
  putConfig: (body: Partial<{ model: string; theme: string; openrouter_key: string; system_prompt: string; quote_color: string; user_label: string; assistant_label: string; provider: string; claude_model: string; default_style_id: string }>) =>
    request<Config>("PUT", "/api/config", body).then((cfg) => {
      configCache = Promise.resolve(cfg);
      return cfg;
    }),
```

- [ ] **Step 3: Add the style API functions**

Add right after the `setCalendarConfig` entry (~line 562), before `getSceneContext`:

```typescript
  listStyles: () => request<Style[]>("GET", "/api/styles"),
  createStyle: (draft: StyleDraft) => request<{ id: string }>("POST", "/api/styles", draft),
  readStyle: (sid: string) => request<StyleDetail>("GET", `/api/styles/${sid}`),
  updateStyle: (sid: string, patch: Partial<StyleDraft>) =>
    request<{ ok: boolean }>("PUT", `/api/styles/${sid}`, patch),
  deleteStyle: (sid: string) => request<{ ok: boolean }>("DELETE", `/api/styles/${sid}`),
  duplicateStyle: (sid: string) => request<{ id: string }>("POST", `/api/styles/${sid}/duplicate`),
  getCampaignStyle: (cid: string) => request<{ style_id: string }>("GET", `/api/campaigns/${cid}/style`),
  setCampaignStyle: (cid: string, style_id: string) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/style`, { style_id }),
  getSceneStyle: (cid: string, sid: string) =>
    request<{ style_id: string }>("GET", `/api/campaigns/${cid}/scenes/${sid}/style`),
  setSceneStyle: (cid: string, sid: string, style_id: string) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/scenes/${sid}/style`, { style_id }),
```

- [ ] **Step 4: Type-check**

Run (from `frontend/`): `npx tsc -b`
Expected: no new errors (existing `Config` consumers construct/compare full objects that already list every field explicitly in test fixtures — those fixtures get their missing `default_style_id` field added in Tasks 8/9's test updates, which happen before this step is re-run there; a clean `tsc -b` here only confirms `client.ts` itself compiles)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts
git commit -m "feat(styles): add style guide API client types and functions"
```

---

### Task 7: `StyleGuideEditor` — global list/detail editor

**Files:**
- Create: `frontend/src/components/StyleGuideEditor.tsx`, `frontend/src/components/StyleGuideEditor.test.tsx`, `frontend/src/routes/StyleGuidesView.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `api.{listStyles, createStyle, readStyle, updateStyle, deleteStyle, duplicateStyle}` (Task 6).
- Produces: `<StyleGuideEditor />` (no props — global resource), mounted at route `/styles`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/StyleGuideEditor.test.tsx`:

```tsx
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { StyleGuideEditor } from "./StyleGuideEditor";

vi.mock("../api/client", () => ({
  api: {
    listStyles: vi.fn(), readStyle: vi.fn(), createStyle: vi.fn(),
    updateStyle: vi.fn(), deleteStyle: vi.fn(), duplicateStyle: vi.fn(),
  },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.listStyles as any).mockResolvedValue([
    { id: "gothic-horror", name: "Gothic Horror", description: "Dread.", tags: ["horror"], built_in: true },
    { id: "cozy-mystery", name: "Cozy Mystery", description: "Gentle.", tags: ["cozy"], built_in: false },
  ]);
  (api.readStyle as any).mockImplementation((sid: string) => Promise.resolve(
    sid === "gothic-horror"
      ? { meta: { id: "gothic-horror", name: "Gothic Horror", description: "Dread.", tags: ["horror"], built_in: true },
          body: "Atmosphere first." }
      : { meta: { id: "cozy-mystery", name: "Cozy Mystery", description: "Gentle.", tags: ["cozy"], built_in: false },
          body: "Keep it warm." }));
  (api.createStyle as any).mockResolvedValue({ id: "new-style" });
  (api.updateStyle as any).mockResolvedValue({ ok: true });
  (api.deleteStyle as any).mockResolvedValue({ ok: true });
  (api.duplicateStyle as any).mockResolvedValue({ id: "gothic-horror-copy" });
});

test("clicking a style shows a read-only view; built-in shows Duplicate not Edit", async () => {
  const { container } = render(<StyleGuideEditor />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Gothic Horror"));
  await waitFor(() => expect(api.readStyle).toHaveBeenCalledWith("gothic-horror"));
  expect(screen.getByText("Atmosphere first.")).toBeInTheDocument();
  expect(container.querySelector("textarea")).toBeNull();
  expect(screen.getByRole("button", { name: /duplicate to customize/i })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^edit$/i })).toBeNull();
});

test("a custom style shows Edit, which reveals the form", async () => {
  const { container } = render(<StyleGuideEditor />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Cozy Mystery"));
  await waitFor(() => expect(api.readStyle).toHaveBeenCalledWith("cozy-mystery"));
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  expect(container.querySelector("textarea")).not.toBeNull();
});

test("+ New opens the form directly and creates a style", async () => {
  render(<StyleGuideEditor />);
  await screen.findByRole("button", { name: /new style guide/i });
  fireEvent.click(screen.getByRole("button", { name: /new style guide/i }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Space Opera" } });
  fireEvent.change(screen.getByLabelText("Guide text"), { target: { value: "Big, bold, cosmic." } });
  fireEvent.click(screen.getByRole("button", { name: /create style guide/i }));
  await waitFor(() => expect(api.createStyle).toHaveBeenCalledWith(
    expect.objectContaining({ name: "Space Opera", body: "Big, bold, cosmic." })));
});

test("duplicating a built-in style opens the new copy for editing", async () => {
  const { container } = render(<StyleGuideEditor />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Gothic Horror"));
  (api.readStyle as any).mockResolvedValueOnce({
    meta: { id: "gothic-horror-copy", name: "Gothic Horror (copy)", description: "Dread.", tags: ["horror"], built_in: false },
    body: "Atmosphere first.",
  });
  fireEvent.click(screen.getByRole("button", { name: /duplicate to customize/i }));
  await waitFor(() => expect(api.duplicateStyle).toHaveBeenCalledWith("gothic-horror"));
  await waitFor(() => expect(api.readStyle).toHaveBeenCalledWith("gothic-horror-copy"));
});

test("deleting a custom style removes it", async () => {
  const original = window.confirm;
  window.confirm = () => true;
  const { container } = render(<StyleGuideEditor />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Cozy Mystery"));
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
  await waitFor(() => expect(api.deleteStyle).toHaveBeenCalledWith("cozy-mystery"));
  window.confirm = original;
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/StyleGuideEditor.test.tsx`
Expected: FAIL (`Failed to resolve import "./StyleGuideEditor"`)

- [ ] **Step 3: Implement `StyleGuideEditor.tsx`**

Create `frontend/src/components/StyleGuideEditor.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type Style } from "../api/client";
import { Field } from "./Field";

const BLANK = { name: "", description: "", tags: [] as string[], body: "" };

export function StyleGuideEditor() {
  const [styles, setStyles] = useState<Style[]>([]);
  const [sid, setSid] = useState<string | null>(null);
  const [form, setForm] = useState(BLANK);
  const [builtIn, setBuiltIn] = useState(false);
  const [mode, setMode] = useState<"view" | "edit">("edit");
  const [tagInput, setTagInput] = useState("");
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => api.listStyles().then(setStyles), []);
  useEffect(() => { reload(); }, [reload]);

  function resetForm() {
    setSid(null);
    setForm(BLANK);
    setBuiltIn(false);
    setMode("edit");
  }

  async function select(id: string) {
    setError(null);
    const s = await api.readStyle(id);
    setSid(id);
    setForm({ name: s.meta.name, description: s.meta.description, tags: s.meta.tags, body: s.body.trim() });
    setBuiltIn(s.meta.built_in);
    setMode("view");
  }

  async function save() {
    if (!form.name.trim()) return;
    setError(null);
    try {
      if (sid && !builtIn) {
        await api.updateStyle(sid, form);
        await reload();
        await select(sid);
      } else {
        const { id } = await api.createStyle(form);
        await reload();
        await select(id);
      }
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function remove(s: Style) {
    if (!window.confirm(`Delete style guide '${s.name}'?`)) return;
    await api.deleteStyle(s.id);
    if (sid === s.id) resetForm();
    await reload();
  }

  async function duplicate() {
    if (!sid) return;
    const { id } = await api.duplicateStyle(sid);
    await reload();
    await select(id);
  }

  function addTag() {
    const t = tagInput.trim();
    if (t && !form.tags.includes(t)) setForm({ ...form, tags: [...form.tags, t] });
    setTagInput("");
  }

  function removeTag(t: string) {
    setForm({ ...form, tags: form.tags.filter((x) => x !== t) });
  }

  return (
    <div className="editor">
      <div className="editor-list">
        <button className="primary new" onClick={resetForm}>+ New style guide</button>
        {styles.map((s) => (
          <button key={s.id} className={"row" + (sid === s.id ? " active" : "")} onClick={() => select(s.id)}>
            {s.name}
            {s.built_in && <span className="mark-badge">built-in</span>}
          </button>
        ))}
      </div>

      <div className="editor-body">
        {error && <div className="banner">{error}</div>}
        {mode === "view" && sid ? (
          <div className="detail-view">
            <div className="detail-main">
              <h3>{form.name}</h3>
              <div className="detail-rendered">
                <Markdown remarkPlugins={[remarkGfm]}>{form.body}</Markdown>
              </div>
            </div>
            <aside className="detail-sidebar">
              <div className="form-actions">
                {builtIn
                  ? <button className="subtle" onClick={duplicate}>Duplicate to customize</button>
                  : <button className="subtle" onClick={() => setMode("edit")}>Edit</button>}
              </div>
              {form.description && (
                <div className="side-section">
                  <h4>Description</h4>
                  <div className="field-hint">{form.description}</div>
                </div>
              )}
              {form.tags.length > 0 && (
                <div className="side-section">
                  <h4>Tags</h4>
                  <div className="chips">
                    {form.tags.map((t) => <span key={t} className="chip on">{t}</span>)}
                  </div>
                </div>
              )}
            </aside>
          </div>
        ) : (
          <div className="form">
            <h3>{sid ? "Edit style guide" : "New style guide"}</h3>
            <Field label="Name">
              <input type="text" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>
            <Field label="Description">
              <input type="text" value={form.description}
                     onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </Field>
            <Field label="Tags">
              <div className="chips">
                {form.tags.map((t) => (
                  <button key={t} className="chip on" onClick={() => removeTag(t)}>{t} ×</button>
                ))}
              </div>
              <div className="joined">
                <input type="text" value={tagInput} onChange={(e) => setTagInput(e.target.value)}
                       onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addTag(); } }} />
                <button className="subtle" onClick={addTag}>Add</button>
              </div>
            </Field>
            <Field label="Guide text">
              <textarea value={form.body} rows={12} onChange={(e) => setForm({ ...form, body: e.target.value })} />
            </Field>
            <div className="form-actions">
              {sid && !builtIn && (
                <button className="subtle" onClick={() => remove(styles.find((s) => s.id === sid)!)}>Delete</button>
              )}
              {sid && <button className="subtle" onClick={() => setMode("view")}>Cancel</button>}
              <button className="primary" onClick={save} disabled={!form.name.trim()}>
                {sid && !builtIn ? "Save style guide" : "Create style guide"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Create the route wrapper**

Create `frontend/src/routes/StyleGuidesView.tsx`:

```tsx
import { StyleGuideEditor } from "../components/StyleGuideEditor";

export default function StyleGuidesView() {
  return (
    <div className="page view-anim" style={{ maxWidth: 1080 }}>
      <div className="page-head">
        <h1 className="page-h1">Style Guides</h1>
      </div>
      <StyleGuideEditor />
    </div>
  );
}
```

- [ ] **Step 5: Wire the nav link and route**

In `frontend/src/App.tsx`, add the import:

```tsx
import StyleGuidesView from "./routes/StyleGuidesView";
```

Add a nav link right after the Worlds link (~line 40-42):

```tsx
          <NavLink to="/worlds" className={({ isActive }) => "nav-btn" + (isActive ? " active" : "")}>
            Worlds
          </NavLink>
          <NavLink to="/styles" className={({ isActive }) => "nav-btn" + (isActive ? " active" : "")}>
            Styles
          </NavLink>
```

Add the route right after the Worlds route (~line 60):

```tsx
        <Route path="/worlds/:wid" element={<WorldView />} />
        <Route path="/styles" element={<StyleGuidesView />} />
```

- [ ] **Step 6: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/components/StyleGuideEditor.test.tsx`
Expected: PASS (5 tests)

Run (from `frontend/`): `npx tsc -b`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/StyleGuideEditor.tsx frontend/src/components/StyleGuideEditor.test.tsx \
        frontend/src/routes/StyleGuidesView.tsx frontend/src/App.tsx
git commit -m "feat(styles): add the Style Guides list/detail editor page"
```

---

### Task 8: Configuration page — default prose style

**Files:**
- Modify: `frontend/src/routes/ConfigView.tsx`, `frontend/src/routes/ConfigView.test.tsx`

**Interfaces:**
- Consumes: `api.listStyles`, `api.putConfig` (default_style_id), `Config.default_style_id` (Task 6).
- Produces: a "Default prose style" `<select>` on the Configuration page.

- [ ] **Step 1: Write the failing test**

In `frontend/src/routes/ConfigView.test.tsx`, extend the mock and fixture, and add a test.

Change the `vi.mock("../api/client", ...)` block to add `listStyles`:

```tsx
vi.mock("../api/client", () => ({
  ApiError: class ApiError extends Error {},
  api: { getConfig: vi.fn(), putConfig: vi.fn(), getDataDir: vi.fn(), putDataDir: vi.fn(), listStyles: vi.fn() },
}));
```

Change the `cfg` fixture to add `default_style_id`, and add a `listStyles` resolution to `beforeEach`:

```tsx
const cfg = { model: "m", theme: "codex", key_set: false, system_prompt: "", quote_color: "off", user_label: "You", assistant_label: "Grimoire", provider: "openrouter", claude_model: "opus", default_style_id: "" };
const dataDir = {
  data_dir: "/home/u/.grimoire", default: "/home/u/.grimoire",
  is_default: true, source: "default" as const, exists: true,
};
beforeEach(() => {
  vi.clearAllMocks();
  (api.getConfig as any).mockResolvedValue(cfg);
  (api.putConfig as any).mockResolvedValue(cfg);
  (api.getDataDir as any).mockResolvedValue(dataDir);
  (api.putDataDir as any).mockResolvedValue(dataDir);
  (api.listStyles as any).mockResolvedValue([
    { id: "gothic-horror", name: "Gothic Horror", description: "", tags: [], built_in: true },
    { id: "noir-detective", name: "Noir Detective", description: "", tags: [], built_in: true },
  ]);
});
```

Add a new test after `test("saves the system prompt", ...)`:

```tsx
test("saves the default prose style", async () => {
  render(<ConfigView />);
  const sel = await screen.findByLabelText(/default prose style/i);
  fireEvent.change(sel, { target: { value: "noir-detective" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(
    expect.objectContaining({ default_style_id: "noir-detective" })));
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `frontend/`): `npx vitest run src/routes/ConfigView.test.tsx`
Expected: FAIL (`Unable to find a label with the text of: /default prose style/i`)

- [ ] **Step 3: Add the dropdown**

In `frontend/src/routes/ConfigView.tsx`, add state and load logic. Change the imports:

```tsx
import { useEffect, useState } from "react";
import { ApiError, api, type Config, type DataDirInfo, type Style } from "../api/client";
```

Add state (~line 33, after `systemPrompt`):

```tsx
  const [systemPrompt, setSystemPrompt] = useState("");
  const [defaultStyleId, setDefaultStyleId] = useState("");
  const [styleOptions, setStyleOptions] = useState<Style[]>([]);
```

Extend the `useEffect` (~line 42-51) to set it from config and fetch the style list:

```tsx
  useEffect(() => {
    api.getConfig().then((c) => {
      setConfig(c);
      setModel(c.model);
      setProvider(c.provider);
      setClaudeModel(c.claude_model);
      setSystemPrompt(c.system_prompt);
      setUserLabel(c.user_label);
      setAssistantLabel(c.assistant_label);
      setDefaultStyleId(c.default_style_id);
    });
    api.getDataDir().then((d) => {
      setDataDir(d);
      setDataDirInput(d.data_dir);
    });
    api.listStyles().then(setStyleOptions).catch(() => setStyleOptions([]));
  }, []);
```

Add the dropdown right after the System prompt `<textarea>` block (~line 198):

```tsx
      <div className="section-label">Default prose style</div>
      <select
        aria-label="Default prose style"
        value={defaultStyleId}
        onChange={(e) => setDefaultStyleId(e.target.value)}
      >
        <option value="">— none —</option>
        {styleOptions.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
      </select>
```

Add `default_style_id: defaultStyleId` to the Save button's payload (~line 239-244):

```tsx
        <button
          className="btn-accent"
          onClick={() => save({
            model, provider, claude_model: claudeModel,
            system_prompt: systemPrompt,
            user_label: userLabel, assistant_label: assistantLabel,
            default_style_id: defaultStyleId,
            ...(key ? { openrouter_key: key } : {}),
          })}
        >
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/routes/ConfigView.test.tsx`
Expected: PASS, no failures

Run (from `frontend/`): `npx tsc -b`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/ConfigView.tsx frontend/src/routes/ConfigView.test.tsx
git commit -m "feat(styles): add the default prose style picker to Configuration"
```

---

### Task 9: `StyleConfig` — campaign-level picker

**Files:**
- Create: `frontend/src/components/StyleConfig.tsx`, `frontend/src/components/StyleConfig.test.tsx`
- Modify: `frontend/src/routes/CampaignView.tsx`, `frontend/src/routes/CampaignView.test.tsx`

**Interfaces:**
- Consumes: `api.{getCampaignStyle, setCampaignStyle, listStyles}` (Task 6).
- Produces: `<StyleConfig cid={string} />`, mounted in the campaign rail next to `CalendarConfig`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/StyleConfig.test.tsx`:

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { StyleConfig } from "./StyleConfig";

vi.mock("../api/client", () => ({
  api: { getCampaignStyle: vi.fn(), setCampaignStyle: vi.fn(), listStyles: vi.fn() },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.getCampaignStyle as any).mockResolvedValue({ style_id: "" });
  (api.setCampaignStyle as any).mockResolvedValue({ ok: true });
  (api.listStyles as any).mockResolvedValue([
    { id: "gothic-horror", name: "Gothic Horror", description: "", tags: [], built_in: true },
    { id: "noir-detective", name: "Noir Detective", description: "", tags: [], built_in: true },
  ]);
});

test("picks a style and saves it", async () => {
  render(<StyleConfig cid="run" />);
  const sel = await screen.findByLabelText("Prose style");
  fireEvent.change(sel, { target: { value: "noir-detective" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.setCampaignStyle).toHaveBeenCalledWith("run", "noir-detective"));
});

test("shows the currently saved style on load", async () => {
  (api.getCampaignStyle as any).mockResolvedValue({ style_id: "gothic-horror" });
  render(<StyleConfig cid="run" />);
  const sel = await screen.findByLabelText("Prose style") as HTMLSelectElement;
  await waitFor(() => expect(sel.value).toBe("gothic-horror"));
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `frontend/`): `npx vitest run src/components/StyleConfig.test.tsx`
Expected: FAIL (`Failed to resolve import "./StyleConfig"`)

- [ ] **Step 3: Implement `StyleConfig.tsx`**

Create `frontend/src/components/StyleConfig.tsx` (mirrors `CalendarConfig.tsx`):

```tsx
import { useEffect, useState } from "react";
import { api, type Style } from "../api/client";

export function StyleConfig({ cid }: { cid: string }) {
  const [styleId, setStyleId] = useState("");
  const [styles, setStyles] = useState<Style[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.getCampaignStyle(cid).then((r) => setStyleId(r.style_id)).catch(() => setStyleId(""));
    api.listStyles().then(setStyles).catch(() => setStyles([]));
  }, [cid]);

  async function save() {
    setError(null);
    try {
      await api.setCampaignStyle(cid, styleId);
      setSaved(true);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  return (
    <div className="style-config">
      {error && <div className="banner">{error}</div>}
      <label>
        Prose style
        <select aria-label="Prose style" value={styleId}
                onChange={(e) => { setStyleId(e.target.value); setSaved(false); }}>
          <option value="">— use global default —</option>
          {styles.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </label>
      <button className="primary" onClick={save}>Save</button>
      {saved && <span className="field-hint">Saved.</span>}
    </div>
  );
}
```

- [ ] **Step 4: Mount it in `CampaignView.tsx`**

Add the import (~line 14, next to `CalendarConfig`):

```tsx
import { CalendarConfig } from "../components/CalendarConfig";
import { StyleConfig } from "../components/StyleConfig";
```

Add a `showStyle` state next to `showCalendar` (~line 71):

```tsx
  const [showCalendar, setShowCalendar] = useState(false);
  const [showStyle, setShowStyle] = useState(false);
```

Add a toggle button in `rail-foot`, right after the existing `rail-date` button (~line 410-418):

```tsx
          {dt?.current && (
            <button className="rail-date" onClick={() => setShowCalendar((v) => !v)}
                    title="Calendar settings">
              {dt.current.weekday} {dt.current.friendly}
              {dt.current.holidays_today.length > 0 && (
                <span className="rail-holiday">✦ {dt.current.holidays_today[0]}</span>
              )}
            </button>
          )}
          <button className="rail-date" onClick={() => setShowStyle((v) => !v)}
                  title="Prose style settings">
            Prose style
          </button>
```

Add the panel right after the existing `{showCalendar && (...)}` block (~line 422-426):

```tsx
        {showCalendar && (
          <div className="panel-slot">
            <CalendarConfig cid={cid} />
          </div>
        )}
        {showStyle && (
          <div className="panel-slot">
            <StyleConfig cid={cid} />
          </div>
        )}
```

- [ ] **Step 5: Update `CampaignView.test.tsx` so the mocked-out `StyleConfig` doesn't need its own API mocks**

Add the mock right after the existing `CalendarConfig` mock (~line 23):

```tsx
vi.mock("../components/CalendarConfig", () => ({ CalendarConfig: () => <div data-testid="calendar-config" /> }));
vi.mock("../components/StyleConfig", () => ({ StyleConfig: () => <div data-testid="style-config" /> }));
```

- [ ] **Step 6: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/components/StyleConfig.test.tsx`
Expected: PASS (2 tests)

Run (from `frontend/`): `npx vitest run src/routes/CampaignView.test.tsx`
Expected: PASS, no new failures

Run (from `frontend/`): `npx tsc -b`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/StyleConfig.tsx frontend/src/components/StyleConfig.test.tsx \
        frontend/src/routes/CampaignView.tsx frontend/src/routes/CampaignView.test.tsx
git commit -m "feat(styles): add the per-campaign prose style picker"
```

---

### Task 10: `SceneInspector` — sticky per-scene override

**Files:**
- Modify: `frontend/src/components/SceneInspector.tsx`, `frontend/src/components/SceneInspector.test.tsx`

**Interfaces:**
- Consumes: `api.{getSceneStyle, setSceneStyle, listStyles}` (Task 6).
- Produces: a "Prose style" `.side-section` in the scene inspector rail, saving on change.

- [ ] **Step 1: Write the failing test**

In `frontend/src/components/SceneInspector.test.tsx`, extend the mock and fixtures, and add a test.

Add `getSceneStyle`, `setSceneStyle`, `listStyles` to the mocked `api` object (in the `vi.mock("../api/client", ...)` block):

```tsx
      getSceneDatetime: vi.fn(), setSceneDatetime: vi.fn(), getCalendarMonths: vi.fn(),
      listAppearances: vi.fn(), listEntityImages: vi.fn(),
      listEntities: vi.fn(), setSceneLocation: vi.fn(),
      getSceneStyle: vi.fn(), setSceneStyle: vi.fn(), listStyles: vi.fn(),
      campaignImageUrl: () => "/img",
      entityImageUrl: () => "/loc-img",
```

Add resolutions to `beforeEach`:

```tsx
  (api.getSceneStyle as any).mockResolvedValue({ style_id: "" });
  (api.setSceneStyle as any).mockResolvedValue({ ok: true });
  (api.listStyles as any).mockResolvedValue([
    { id: "gothic-horror", name: "Gothic Horror", description: "", tags: [], built_in: true },
    { id: "noir-detective", name: "Noir Detective", description: "", tags: [], built_in: true },
  ]);
```

Add a new test, matching the file's existing `render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={...} />)` shape:

```tsx
test("picking a scene prose style saves it immediately", async () => {
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={vi.fn()} />);
  const sel = await screen.findByLabelText("Prose style");
  fireEvent.change(sel, { target: { value: "noir-detective" } });
  await waitFor(() => expect(api.setSceneStyle).toHaveBeenCalledWith("c", "s", "noir-detective"));
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `frontend/`): `npx vitest run src/components/SceneInspector.test.tsx`
Expected: FAIL (`Unable to find a label with the text of: Prose style`)

- [ ] **Step 3: Add the picker**

In `frontend/src/components/SceneInspector.tsx`, extend the imports:

```tsx
import {
  api, type Actor, type SceneContext, type SceneLocation, type ChronicleEntry,
  type CalendarConfig, type RosterEntry, type SceneDatetime, type Style,
} from "../api/client";
```

Add state (~line 30, after `locPick`):

```tsx
  const [locPick, setLocPick] = useState("");
  const [styleId, setStyleId] = useState("");
  const [styleOptions, setStyleOptions] = useState<Style[]>([]);
  const [error, setError] = useState<string | null>(null);
```

Extend the cid-only `useEffect` (~line 32-45) to fetch the style list:

```tsx
  useEffect(() => {
    Promise.all([api.listCharacters({ kind: "campaign", id: cid }), api.listCampaignPCs(cid)])
      .then(([chars, pcs]) => {
        const m: Record<string, string> = {};
        for (const x of chars) m[`characters/${x.id}`] = x.name;
        for (const x of pcs) m[`pcs/${x.id}`] = x.name;
        setNames(m);
      });
    getModels().then(setModels).catch(() => setModels([]));
    api.listEntities({ kind: "campaign", id: cid }, "locations")
      .then((ls) => setLocations(ls.map((l) => ({ id: l.id, name: l.name }))))
      .catch(() => setLocations([]));
    api.getCalendarProviders().then((r) => setCalendars(r.providers)).catch(() => setCalendars([]));
    api.listStyles().then(setStyleOptions).catch(() => setStyleOptions([]));
  }, [cid]);
```

Add a `reloadStyle` callback next to `reloadCfg` (~line 54-56):

```tsx
  const reloadCfg = useCallback(
    () => api.getCalendarConfig(cid).then(setCfg).catch(() => setCfg(null)),
    [cid]);
  const reloadStyle = useCallback(
    () => api.getSceneStyle(cid, sid).then((r) => setStyleId(r.style_id)).catch(() => setStyleId("")),
    [cid, sid]);
```

Wire it into the `cid, sid, refreshKey` effect (~line 58-66):

```tsx
  useEffect(() => {
    api.getCast(cid, sid).then(setCast).catch(() => setCast([]));
    api.listAppearances(cid).then(setRoster).catch(() => setRoster([]));
    api.getSceneLocation(cid, sid).then(setSetting).catch(() => setSetting(null));
    api.getSceneContext(cid, sid).then(setCtx).catch(() => setCtx(null));
    api.getChronicle(cid).then(setRecap).catch(() => setRecap([]));
    reloadWhen();
    reloadCfg();
    reloadStyle();
  }, [cid, sid, refreshKey, reloadWhen, reloadCfg, reloadStyle]);
```

Add a `chooseStyle` handler near `chooseCalendar` (~line 77-87):

```tsx
  async function chooseStyle(value: string) {
    setStyleId(value);
    setError(null);
    try {
      await api.setSceneStyle(cid, sid, value);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }
```

Add the side-section right after the "Location" block, before "When" (~line 190-192):

```tsx
      <div className="side-section">
        <h4>Prose style</h4>
        <select aria-label="Prose style" value={styleId} onChange={(e) => chooseStyle(e.target.value)}>
          <option value="">— use campaign default —</option>
          {styleOptions.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </div>

      <div className="side-section">
        <h4>When</h4>
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/components/SceneInspector.test.tsx`
Expected: PASS, no failures

Run (from `frontend/`): `npx tsc -b`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/SceneInspector.tsx frontend/src/components/SceneInspector.test.tsx
git commit -m "feat(styles): add the sticky per-scene prose style override"
```

---

### Task 11: full verification pass + CampaignView regression check

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS, no failures

- [ ] **Step 2: Run the golden-fixture template verifier**

Run: `backend/.venv/Scripts/python.exe scripts/verify_templates.py`
Expected: exits 0

- [ ] **Step 3: Run the full frontend suite and type-check**

Run (from `frontend/`): `npx vitest run`
Expected: PASS, no failures

Run (from `frontend/`): `npx tsc -b`
Expected: no errors

- [ ] **Step 4: Manual smoke check**

Start the app (see the project's `run` skill/dev-server instructions) and confirm in a browser: the "Styles" nav link lists the 7 built-ins; opening one shows read-only rendered text with a "Duplicate to customize" button; Configuration has a "Default prose style" dropdown; a campaign's rail has a "Prose style" toggle that saves; a scene's inspector has a "Prose style" picker that saves on change; generating a scene turn with a style set produces prose that visibly reflects the chosen genre (e.g. gothic-horror vs. modern-thriller on the same prompt).

- [ ] **Step 5: Commit** (only if the manual check above needs it — otherwise this task has nothing to commit and should be skipped)

---

## Completion: GitHub issue bookkeeping

None of the issues surveyed during brainstorming are fully resolved by this feature (each covers a broader or differently-scoped concern), so nothing gets closed — only commented, once Task 11 is green and the branch is ready to merge:

- **#202** ("Style guides & image presets library sections") — comment that the style-guides half is now implemented (as a global resource rather than world-scoped, per this plan's design), linking the design doc and the merged branch/PR; note the image-presets half is still open and untouched.
- **#30** ("Prompt-template library: multiple named variants") — comment noting the overlap (bundled defaults + user variants + active selection + clone/delete, same shape) and the distinction (this feature covers one labeled prompt section via a dedicated record type, not whole-template-file overrides), so #30 isn't mistaken for a duplicate or silently resolved.
- **#73** ("Per-campaign settings tabs") — light comment noting the new campaign-level style picker follows the same override-pattern precedent (`CalendarConfig`-style panel in the campaign rail) that #73 discusses, but doesn't touch its actual scope (model/context-depth/label overrides).

Use `gh issue comment <number> --repo charlesmsiegel/grimoire --body "..."` for each, from the primary checkout (not the worktree) once the branch is merged.
