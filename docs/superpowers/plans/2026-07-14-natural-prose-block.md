# Natural-Prose Block Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an always-on "Natural prose" section — anti-AI-ism defaults (names, stock phrases, beat words, constructions, rhythm) — to every scene/opener system prompt, visible in the Context inspector.

**Architecture:** One new var-less Jinja2 template (`templates/scene/sections/natural_prose.j2`) included from `scene/system.j2` right after the prose-style section, plus the mirroring `_SECTIONS` entry in `store/context.py` so the Context inspector shows and counts it. No frontend changes; no new config.

**Tech Stack:** Jinja2 templates (grimoire prompt corpus), FastAPI backend, pytest.

**Spec:** `docs/superpowers/specs/2026-07-14-natural-prose-block-design.md` — the block text in Task 1 is copied from it verbatim.

## Global Constraints

- Template render contract (templates/README.md): `StrictUndefined`, no autoescape, no trailing newline; the new template must contain **no** Jinja syntax (it is var-less plain text) so it can never raise on render.
- Never pin template *body* text in tests — the project guarantees "editing a prompt cannot fail a test". Tests may pin only the section heading (`# Natural prose`) and the inspector label (`Natural prose`), matching how `# Story so far` is tested today.
- Test isolation: every backend test sets `monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))` (the existing `_campaign` helper in `backend/tests/test_context.py` does this).
- Commands: backend tests run as `backend/.venv/Scripts/python.exe -m pytest backend -q` from the repo root; the template harness as `backend/.venv/Scripts/python.exe scripts/verify_templates.py`.
- Android packages `templates/` verbatim into the APK — nothing platform-specific to do, and nothing in this plan may assume a desktop `~`.
- CLAUDE.md privacy rule: no real-world personal names anywhere. The name lists in the block are the *AI-default* pools from the sources (Elara, Kael, Chen, Okonkwo…), not private data; do not add other example names.

---

### Task 1: The `natural_prose.j2` section template, wired into `system.j2`

**Files:**
- Create: `templates/scene/sections/natural_prose.j2`
- Modify: `templates/scene/system.j2` (after the `prose_style` include, currently lines 28–29)
- Modify: `templates/README.md` (the `### scene/` entry, after the `prose_style_name`/`prose_style_body` bullet around line 97)
- Test: `backend/tests/test_context.py`

**Interfaces:**
- Consumes: the existing section-include pattern in `scene/system.j2` (`{%- set s -%}{%- include … -%}{%- endset -%}` + append-if-nonempty).
- Produces: a system-prompt section headed `# Natural prose`, present in every chat/retry/regenerate/director/opener prompt. Task 2 relies on the template path `scene/sections/natural_prose.j2` and the heading `# Natural prose`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_context.py` (next to `test_no_setting_block_when_unset`, which shows the same access pattern):

```python
def test_natural_prose_section_in_system_prompt(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "hi")
    msgs = context.build_messages(cid, sid)
    assert msgs[0]["role"] == "system"
    assert "# Natural prose" in msgs[0]["content"]


def test_natural_prose_section_in_opener_prompt(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    msgs = context.build_opener_messages(cid, sid, "A storm rolls in.")
    assert msgs[0]["role"] == "system"
    assert "# Natural prose" in msgs[0]["content"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q -k natural_prose`
Expected: 2 FAILED — `assert '# Natural prose' in …` (the section does not exist yet).

- [ ] **Step 3: Create the template**

Create `templates/scene/sections/natural_prose.j2` with exactly this content (no Jinja syntax, no trailing blank line beyond the final newline; the environment strips the trailing newline on render):

```
# Natural prose

Defaults that keep the writing from sounding machine-generated. Precedence:
the reply format and established facts always win — never rename, avoid, or
misattribute anyone or anything that already has a name. The prose style
guide, when one is set, overrides the rhythm guidance below. Everything else
here holds regardless.

**Names — only when inventing someone or something new.** Names that
already exist in this scene, cast, or world are fixed; reproduce them
exactly, even if they appear below. When you do invent a name, make it fit
the setting and vary in sound and origin. Never reach for the stock AI
pool: Elara, Lyra, Kael, Aria, Seraphina, Selene, Thorne, Voss, Vance,
Blackwood, Ashford, or a tavern called The Gilded-or-Rusty Anything. Don't
solve variety by rotating the same few names either (a Chen, an Okonkwo, a
Kowalski) — vary within an origin, not just across origins.

**Phrases — never use.** A voice barely above a whisper / barely audible;
said in a low voice as a reflex tag; the air thick with (scent, tension,
anything); a smile playing on lips; eyes never leaving; couldn't help but;
couldn't shake the feeling; heart pounding or hammering in a chest or
against ribs; casting long shadows; something else entirely; spreading
across her face; one last time; a deep breath as filler; a testament to; a
tapestry, symphony, or dance of anything; ministrations; the ghost of a
smile; shivers down the spine; knuckles whitening; the smell of ozone; an
unreadable expression; lips swollen with kisses; foreheads pressed
together as the default tender gesture; delve; nestled; moreover,
furthermore, indeed, albeit.

**Beat words — ration.** Flickered, leaned, murmured, muttered, nodded,
gaze, grinned, gestured, glinted, hesitated, whispered, blinked, hummed,
smirked, faintly. Ordinary words, but they are your reflexes: not every
line of dialogue needs a lean, nod, or murmur. When a beat repeats, replace
it with something specific to this character and this moment, or cut it.

**Constructions — never use.** "Not X, but Y" in every disguise ("it
wasn't just X — it was Y", "she didn't X; she Y'd", "no longer X; now Y").
A rhetorical question you immediately answer ("The result? Chaos."). The
reflexive rule of three ("he stopped, stared, listened") — three-part lists
only when the content is genuinely three things. Redundant adjective pairs
("dark and brooding") — pick the stronger word. Explaining an emotion you
just showed ("...which surprised him, because..."). Metaphors that decorate
rather than clarify.

**Rhythm.** Em dashes, ellipses, italics, and one-word dramatic fragments
are seasoning, not structure — if the last paragraph used one, the next
doesn't. Vary sentence length and paragraph shape; let some moments pass
without a dramatic beat. In narration, write lists as prose, no bullet
points or headings — the required speaker markers are reply format, not
headings, and always stay.
```

- [ ] **Step 4: Wire the include into `system.j2`**

In `templates/scene/system.j2`, immediately after the `prose_style` block:

```jinja
{%- set s -%}{%- include "scene/sections/prose_style.j2" -%}{%- endset -%}
{%- if s.strip() -%}{%- set _ = sections.append(s.strip()) -%}{%- endif -%}
```

insert:

```jinja
{%- set s -%}{%- include "scene/sections/natural_prose.j2" -%}{%- endset -%}
{%- if s.strip() -%}{%- set _ = sections.append(s.strip()) -%}{%- endif -%}
```

(Same pattern as every other section; keep the blank line between blocks.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q -k natural_prose`
Expected: 2 passed.

- [ ] **Step 6: Run the template harness and the full backend suite**

Run: `backend/.venv/Scripts/python.exe scripts/verify_templates.py`
Expected: all checks pass (the harness renders `scene/system.j2` on both sides of its comparison, so the new include is symmetric; it never pins template text).

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: all pass. If any test fails by asserting the *absence* of some string now present in the system prompt, fix that test's assertion to target its own section label rather than the whole message (do not weaken the new block).

- [ ] **Step 7: Document the section in `templates/README.md`**

In the `### scene/` entry, right after the `prose_style_name, prose_style_body` bullet, add:

```markdown
- (no vars) `sections/natural_prose.j2` — the always-on anti-AI-ism
  defaults (names at invention, banned stock phrases, beat-word rationing,
  banned constructions, rhythm); sits right after the prose style, which
  may override only its rhythm guidance. Spec:
  docs/superpowers/specs/2026-07-14-natural-prose-block-design.md.
```

- [ ] **Step 8: Commit**

```bash
git add templates/scene/sections/natural_prose.j2 templates/scene/system.j2 templates/README.md backend/tests/test_context.py
git commit -m "feat(prompts): always-on natural-prose section — anti-AI-ism defaults"
```

---

### Task 2: Context-inspector visibility — `_SECTIONS` entry + test

**Files:**
- Modify: `backend/src/grimoire/store/context.py:592` (the `_SECTIONS` list)
- Test: `backend/tests/test_context.py`

**Interfaces:**
- Consumes: `scene/sections/natural_prose.j2` and the `# Natural prose` heading from Task 1; `context.context_sections(cid, sid) -> list[{"label": str, "text": str}]` (existing).
- Produces: a `("Natural prose", "scene/sections/natural_prose.j2", False)` entry in `_SECTIONS`, positioned directly after `("Prose style", …)`. The Context inspector endpoint (`routes.py:3341`) derives labels and token totals from `context_sections`, so no route/frontend change.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_context.py`:

```python
def test_natural_prose_in_context_sections(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import config
    config.write_config(system_prompt="Never speak for the PC.")
    secs = context.context_sections(cid, sid)
    labels = [s["label"] for s in secs]
    # No prose style is configured, so Natural prose lands right after the
    # global prompt — mirroring its position in scene/system.j2.
    assert labels[0] == "Global system prompt"
    assert labels[1] == "Natural prose"
    text = next(s["text"] for s in secs if s["label"] == "Natural prose")
    assert text.startswith("# Natural prose")
    assert context.count_tokens(text) > 0  # it contributes to the token total
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q -k natural_prose_in_context`
Expected: FAIL — `labels[1]` is `"Character descriptions"` (or similar), because `_SECTIONS` has no Natural prose entry yet.

- [ ] **Step 3: Add the `_SECTIONS` entry**

In `backend/src/grimoire/store/context.py`, in `_SECTIONS`, after
`("Prose style", "scene/sections/prose_style.j2", False),` insert:

```python
    ("Natural prose", "scene/sections/natural_prose.j2", False),
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q -k natural_prose_in_context`
Expected: PASS.

- [ ] **Step 5: Run the full backend suite and the harness**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: all pass. `test_context_sections_labels_and_global_prompt` asserts `labels[0] == "Global system prompt"` and that no section is empty — both still hold.

Run: `backend/.venv/Scripts/python.exe scripts/verify_templates.py`
Expected: all checks pass (unchanged from Task 1; `_SECTIONS` is not part of the harness).

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/context.py backend/tests/test_context.py
git commit -m "feat(context): show the Natural prose section in the context inspector"
```

---

## Final verification (after both tasks)

- [ ] `backend/.venv/Scripts/python.exe -m pytest backend -q` — clean.
- [ ] `backend/.venv/Scripts/python.exe scripts/verify_templates.py` — clean.
- [ ] From `frontend/`: `npx tsc -b` and `npx vitest run` — clean (nothing frontend-side changed; this guards against accidental drift).
- [ ] Manual spot-check: open any scene's Context inspector in the app and confirm a "Natural prose" row with a nonzero token count sits right after "Prose style" (or first, when no style/global prompt is set).
- [ ] Codex gates per CLAUDE.md: `/codex:review` against the diff, then the final `/codex:adversarial-review` against diff + spec (does the implementation match the spec — precedence hierarchy present in the block, names scoped to invention, inspector entry, README line).

## Explicitly out of scope (from the spec)

- Reusing the include in other calls (`scene_suggestions`, `tagline`, `dossier`, `absorb`).
- Any config toggle / UI switch (declined as YAGNI; unblocked follow-up).
- Sampler-level suppression; per-model tailoring.
