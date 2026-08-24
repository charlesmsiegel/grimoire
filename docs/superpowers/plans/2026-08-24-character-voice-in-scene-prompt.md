# Character Voice in the Scene Prompt — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put each present character's voice anchor and example dialogue into the scene prompt, attributed by name, so two NPCs in a scene stop sounding interchangeable.

**Architecture:** One resolved `cast_blocks` structure is built in `context/assemble.py` and feeds four sections: a fixed-text `voice_policy` at `LOCK_IN`, per-character `voice_anchors` and `voice_examples` at `SPOTLIGHT`, and the existing `character_descriptions`. The old `message_examples` section is removed and its stored layout entry migrated. Separately, the drift judge is given the character's *valid* outstanding correction so it stops flagging the model for obeying an instruction the scene prompt prioritises.

**Tech Stack:** Python 3 / FastAPI / Jinja2 (`templates/`), pytest; React + TypeScript, vitest.

**Spec:** `docs/superpowers/specs/2026-08-24-character-voice-in-scene-prompt-design.md`

## Global Constraints

- **Never commit library data.** No counts, proportions, distributions, or `~/.grimoire` contents in code, comments, tests, or commit messages. Qualitative only. Invented names only — reuse the codebase's placeholders (Seraphine, Mara, Winifred, Realm, Saltmarch). See `CLAUDE.md`.
- **Every store write goes through `store.atomic`** (`test_atomic_guard.py`). No task here adds a store writer, so no new `# atomic-ok:` markers should be needed.
- **Imports at module scope, module graph acyclic** (`test_import_guard.py`). Inside `store/`, cross-package imports bind a *submodule*: `from ..campaigns import read` then `read.world_refs()`.
- **pydantic stays v1/v2-agnostic** — plain `BaseModel` fields, dump via `routes.common._dump`.
- **The three lint gates are ratcheted.** Resolving a finding makes the recorded count stale, so run `make baseline` and commit the smaller `lint-baselines/<tool>.json` with the fix.
- **Run the gate with `make check`.** Backend alone: `cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m pytest <path>` (Windows) or `.venv/bin/python` (macOS/Linux). Frontend tests run **from** `frontend/`.
- **After editing anything in `templates/`**, `make check` must pass — it runs both `scripts/verify_templates.py` and the offline eval suite.
- Caps: `VOICE_ANCHOR_CAP = 1200`, `VOICE_EXAMPLE_CAP = 3000` (characters).

---

### Task 1: The shared truncation helper and `voice_anchors.effective()`

`voice_anchors.write` enforces no length limit, so both per-character values are capped at render. One helper, because the anchor and the example want identical boundary behaviour, and `effective()` must be the single transformation both the prompt and the drift judge see.

**Files:**
- Modify: `backend/src/grimoire/store/voice_anchors.py`
- Test: `backend/tests/test_voice_anchors_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `voice_anchors.VOICE_ANCHOR_CAP: int`, `voice_anchors.VOICE_EXAMPLE_CAP: int`, `voice_anchors.truncate(text: str, cap: int) -> str`, `voice_anchors.effective(text: str) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_voice_anchors_store.py  (append)
from grimoire.store import voice_anchors


def test_truncate_leaves_short_text_alone():
    assert voice_anchors.truncate("short", 100) == "short"


def test_truncate_cuts_before_a_start_marker():
    text = "<START>\nalpha line\n<START>\nbeta line that runs past the cap"
    out = voice_anchors.truncate(text, 30)
    assert out == "<START>\nalpha line"


def test_truncate_prefers_the_latest_boundary_in_the_prefix():
    # a blank line occurring after the last <START> inside the prefix wins
    text = "<START>\nalpha\n\nbeta\ngamma runs past the cap here"
    out = voice_anchors.truncate(text, 20)
    assert out == "<START>\nalpha"


def test_truncate_hard_cuts_when_the_prefix_has_no_boundary():
    text = "x" * 50
    assert voice_anchors.truncate(text, 20) == "x" * 20


def test_truncate_hard_cuts_rather_than_returning_almost_nothing():
    # only boundary is a leading <START>: cutting before it yields "", so the
    # hard cut is used instead
    text = "<START>" + ("y" * 200)
    out = voice_anchors.truncate(text, 100)
    assert len(out) == 100
    assert out.startswith("<START>")


def test_effective_applies_the_anchor_cap():
    long_anchor = "z" * (voice_anchors.VOICE_ANCHOR_CAP + 500)
    assert len(voice_anchors.effective(long_anchor)) == voice_anchors.VOICE_ANCHOR_CAP


def test_effective_strips_and_passes_short_anchors_through():
    assert voice_anchors.effective("  Clipped. Never contracts.  ") == "Clipped. Never contracts."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_voice_anchors_store.py -q`
Expected: FAIL with `AttributeError: module 'grimoire.store.voice_anchors' has no attribute 'truncate'`

- [ ] **Step 3: Write the implementation**

Add to `backend/src/grimoire/store/voice_anchors.py`, below the existing constants:

```python
VOICE_ANCHOR_CAP = 1200
"""Longest anchor text the prompt or the drift judge ever sees, in characters.

An anchor is specified as 3-6 short lines, so this is headroom rather than a
constraint on the author -- but `write` enforces no limit at all, and the
`voice_anchors` section is per-cast, so an unbounded value would be multiplied
by the present cast size on every turn.
"""

VOICE_EXAMPLE_CAP = 3000
"""Longest `mes_example` the prompt ever sees, in characters.

Chosen so a card's examples have room for several full exchanges before
anything is cut: a ceiling on outliers, not a target. Tune against real
prompts once the section is live rather than treating the number as settled.
"""


def truncate(text: str, cap: int) -> str:
    """`text` shortened to at most `cap` characters, cut at a boundary.

    The boundary is a `<START>` marker or a blank line, whichever occurs LATEST
    within the capped prefix; a chosen `<START>` is cut *before* the marker, so
    the partial block it opened is discarded rather than left headerless, and a
    chosen blank line is cut after the last non-blank line.

    Two fallbacks, both deliberate. With no boundary in the prefix this is a
    hard character cut and MAY land mid-line -- a single-line example longer
    than the cap has nowhere else to go. And when the boundary rule would keep
    less than half the cap (the case where the only boundary is a `<START>` at
    position 0), the hard cut is used instead: truncating a long example to
    nothing because it opens with a marker is worse than truncating it mid-line.
    """
    text = text.strip()
    if len(text) <= cap:
        return text
    prefix = text[:cap]
    cut = -1
    marker = prefix.rfind("<START>")
    if marker > 0:
        cut = marker
    blank = prefix.rfind("\n\n")
    if blank > cut:
        cut = blank
    if cut > 0 and cut >= cap // 2:
        return prefix[:cut].rstrip()
    return prefix


def effective(text: str) -> str:
    """The anchor as both the scene prompt and the drift judge see it.

    ONE transformation with TWO consumers, and that is the whole point: if the
    generator received a truncated anchor while the judge read the full one, a
    rule past the cap would be invisible to the writer and still enforced
    against it. Capping is therefore not a source of generator/judge
    divergence -- both copies come from here.
    """
    return truncate(text, VOICE_ANCHOR_CAP)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_voice_anchors_store.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/voice_anchors.py backend/tests/test_voice_anchors_store.py
git commit -m "One capped anchor, for the prompt and the judge alike"
```

---

### Task 2: Display-name disambiguation

Two present NPCs with the same name produce two indistinguishable `## Winifred` headings, which attribute nothing. A cast-order ordinal is guaranteed unique; a version label is not (two characters can both be `Winifred` with both selected versions labelled `Default`) and would need `characters._card_summary`, a private cross-module helper.

**Do NOT use `scenes.serialize.confusable` here.** It was the obvious candidate and it is wrong for two reasons, both verified against the real function:

- It answers a different question — "can a transcript speaker label be resolved to exactly one cast member" — and its `names` argument is the **full roster including the name itself**, as `cast.py:268` passes it. `confusable("Winifred", ["Winifred"])` is `False` and `confusable("Mara", ["Winifred"])` is `True`, which is backwards from what heading-uniqueness wants.
- **Suffixing cannot satisfy it.** `confusable("Winifred #1", ["Winifred #1", "Winifred #2"])` is still `True`, because the bare label "Winifred" is a word-boundary prefix of both. A loop that disambiguated until `confusable` went quiet would never converge, and it would also fire on "Winifred Vance" / "Winifred Vale" — two headings a reader can already tell apart.

The requirement is that no two headings are the *same string*. Compare case-insensitively and keep the original case in the output.

**Files:**
- Modify: `backend/src/grimoire/store/context/cast.py`
- Test: `backend/tests/test_context.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `cast.display_names(names: list[str]) -> list[str]` — takes stripped names (`""` for nameless), returns the display name per entry in the same order. Non-empty results are pairwise distinct; `""` entries stay `""`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_context.py  (append)
from grimoire.store.context import cast as context_cast


def test_display_names_leaves_distinct_names_alone():
    assert context_cast.display_names(["Mara", "Winifred"]) == ["Mara", "Winifred"]


def test_display_names_leaves_merely_similar_names_alone():
    """Two headings a reader can already tell apart are not a collision."""
    assert context_cast.display_names(["Winifred Vance", "Winifred Vale"]) ==         ["Winifred Vance", "Winifred Vale"]


def test_display_names_ordinals_every_member_of_a_collision():
    # both members, not just the later one -- a bare "Winifred" beside
    # "Winifred #2" is still the ambiguous heading this exists to remove
    assert context_cast.display_names(["Winifred", "Winifred"]) ==         ["Winifred #1", "Winifred #2"]


def test_display_names_skips_an_ordinal_that_collides_with_a_literal_name():
    out = context_cast.display_names(["Winifred", "Winifred", "Winifred #2"])
    assert out == ["Winifred #1", "Winifred #3", "Winifred #2"]
    assert len(set(out)) == 3


def test_display_names_matches_case_insensitively():
    out = context_cast.display_names(["Mara", "mara"])
    assert out == ["Mara #1", "mara #2"]


def test_display_names_keeps_nameless_entries_empty():
    assert context_cast.display_names(["", "Mara", ""]) == ["", "Mara", ""]


def test_display_names_never_collides_among_non_empty_results():
    out = context_cast.display_names(["Mara", "Mara", "Mara #1", ""])
    named = [n for n in out if n]
    assert len(set(named)) == len(named)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_context.py -k display_names -q`
Expected: FAIL with `AttributeError: module ... has no attribute 'display_names'`

- [ ] **Step 3: Write the implementation**

```python
def display_names(names: list[str]) -> list[str]:
    """Per-entry heading names for the present cast, pairwise distinct.

    `names` are already stripped, `""` for a card with no name. Nameless
    entries stay `""` and are EXCLUDED from the uniqueness requirement -- they
    render no heading, so there is nothing to collide over, and demanding
    distinctness of two empty strings is unsatisfiable.

    Plain case-folded equality, deliberately NOT `serialize.confusable`. That
    function asks whether a transcript speaker label resolves to exactly one
    cast member, which is a different question with an inverted argument
    convention (it takes the whole roster, name included). It also cannot be
    satisfied by what this does: "Winifred #1" and "Winifred #2" remain
    confusable, because the bare label "Winifred" still prefixes both. Using it
    would fire on "Winifred Vance" beside "Winifred Vale" -- two headings a
    reader can tell apart -- and never converge.

    The ordinal is appended to the ORIGINAL name and INCREMENTED past anything
    already taken -- never appended to an already-suffixed display name, which
    would produce "Winifred #1 #2". A literal card named "Winifred #2" is
    stepped over rather than duplicated, and keeps its own name because it is
    not itself a duplicate.
    """
    folded = [n.casefold() for n in names]
    dupes = {f for f in folded if f and folded.count(f) > 1}
    taken = {n.casefold() for n in names if n}
    out = list(names)
    for i, name in enumerate(names):
        if not name or folded[i] not in dupes:
            continue
        k = 1
        while f"{name} #{k}".casefold() in taken:
            k += 1
        out[i] = f"{name} #{k}"
        taken.add(out[i].casefold())
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_context.py -k display_names -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/context/cast.py backend/tests/test_context.py
git commit -m "A heading that names one character and not two"
```

---

### Task 3: Build `cast_blocks` and `named_npc_count` in `_assemble`

One resolved structure feeds all four affected sections, so name attribution and nameless handling are defined once and cannot diverge. **Nothing is filtered out here** — filtering is each template's business, per-block. An earlier spec revision dropped entries whose anchor and example were both empty, which silently defeated the cast-size rule below.

**Files:**
- Modify: `backend/src/grimoire/store/context/assemble.py` (near the `npc_cards` loop at ~line 129, and the `data` dict at ~line 234)
- Test: `backend/tests/test_context.py`

**Interfaces:**
- Consumes: `voice_anchors.effective`, `voice_anchors.truncate`, `voice_anchors.VOICE_EXAMPLE_CAP` (Task 1); `cast.display_names` (Task 2); `overlay.voice_anchor_record(cid, char_id) -> dict` (existing).
- Produces: template variables `cast_blocks: list[dict]` with keys `name`, `description`, `anchor`, `example` (one per present NPC, cast order), and `named_npc_count: int`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_context.py  (append)
def test_cast_blocks_keeps_every_present_npc_and_filters_nothing(tmp_path, monkeypatch):
    """Two named NPCs with no anchor and no mes_example still produce two
    entries -- the cast-size rule reads `named_npc_count`, and filtering here
    would silently switch the voice policy off in exactly the case it is for."""
    cid, sid = _campaign_with_two_bare_npcs(tmp_path)   # helper below
    a = _assembled(cid, sid)
    assert len(a["data"]["cast_blocks"]) == 2
    assert a["data"]["named_npc_count"] == 2
    assert all(b["anchor"] == "" and b["example"] == "" for b in a["data"]["cast_blocks"])


def test_cast_blocks_strips_a_whitespace_only_name(tmp_path):
    cid, sid = _campaign_with_npc(tmp_path, name="   ")
    a = _assembled(cid, sid)
    assert a["data"]["cast_blocks"][0]["name"] == ""
    assert a["data"]["named_npc_count"] == 0


def test_cast_blocks_caps_the_anchor_and_the_example(tmp_path):
    cid, sid = _campaign_with_npc(
        tmp_path, name="Mara",
        anchor="a" * (voice_anchors.VOICE_ANCHOR_CAP + 200),
        mes_example="b" * (voice_anchors.VOICE_EXAMPLE_CAP + 200))
    block = _assembled(cid, sid)["data"]["cast_blocks"][0]
    assert len(block["anchor"]) == voice_anchors.VOICE_ANCHOR_CAP
    assert len(block["example"]) == voice_anchors.VOICE_EXAMPLE_CAP


def test_cast_blocks_resolves_a_campaign_tombstone_to_no_anchor(tmp_path):
    """A campaign that cleared an inherited anchor means 'none here', not
    'show me the world's again' -- overlay resolves it, this just must not
    read past it."""
    cid, sid = _campaign_with_npc(tmp_path, name="Mara", anchor="World anchor.")
    _tombstone_the_campaign_anchor(cid, "mara")
    assert _assembled(cid, sid)["data"]["cast_blocks"][0]["anchor"] == ""
```

**Helper contracts.** Build these on whatever fixture pattern `test_context.py`
already uses — read the top of that file and follow it rather than inventing a
second scaffold. What each must guarantee:

- `_campaign_with_npc(tmp_path, *, name, description="", anchor="", mes_example="")`
  → `(cid, sid)`. A world with one character whose card carries `name`,
  `description` and `mes_example`; when `anchor` is non-empty,
  `voice_anchors.write(world_root, char_id, anchor)`. A campaign on that world
  with one scene, that character present as an **npc** (not a player).
- `_campaign_with_two_bare_npcs(tmp_path)` → `(cid, sid)`. The same, with two
  present npcs, both named, **neither** having an anchor or a `mes_example`.
- `_assembled(cid, sid)` → the dict `context.assemble._assemble` returns.
- `_tombstone_the_campaign_anchor(cid, char_id)` → calls
  `overlay.set_voice_anchor(cid, char_id, "")`, which writes the campaign-side
  tombstone. Postcondition: `overlay.voice_anchor_record(cid, char_id)["text"]`
  is `""` while the world anchor is still non-empty — assert that in the helper
  so a change in overlay semantics fails loudly here rather than silently
  weakening the test that uses it.
- `_section_text(cid, sid, section_id)` → the rendered text of one section, or
  `""` when it did not render.
- `_reorder_sections(ids)` / `_disable_section(id)` → write a
  `prompt_layout.json` for the test home putting `ids` in that order / marking
  `id` disabled.
- `_set_context_budget(n)` → set `context_budget` in the test home's config.
- `_packed_sections(cid, sid)` → the inspector's rows, each with `id` and
  `dropped`. `context.context_sections` is the existing entry point; check its
  real shape and match it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_context.py -k cast_blocks -q`
Expected: FAIL with `KeyError: 'cast_blocks'`

- [ ] **Step 3: Write the implementation**

In `_assemble`, immediately after the existing `npc_cards` loop, collect ids alongside cards so the anchor can be resolved. Change the loop to also record `a["id"]`:

```python
    npc_cards: list[dict] = []
    npc_ids: list[str] = []
    for a in cast:
        if a["role"] != "npc":
            continue
        vid = appearances_versions.locked_version(cid, a["kind"], a["id"])
        try:
            npc_cards.append(characters.read_card(aroot, a["id"], vid)["data"])
        except (characters.CharacterNotFound, characters.VersionNotFound):
            continue
        npc_ids.append(a["id"])
```

Then build the blocks (place beside the other derived data, before the `data` dict):

```python
    # ONE resolved structure for every section that names a character:
    # `character_descriptions`, `voice_policy`, `voice_anchors`,
    # `voice_examples`. Defined here rather than per-template because
    # disambiguation and nameless handling must not be able to diverge between
    # them -- and because a template that reached the store for an anchor would
    # be doing IO from a render.
    raw_names = [(d.get("name") or "").strip() for d in npc_cards]
    shown = cast_data.display_names(raw_names)
    cast_blocks = []
    for name, card, char_id in zip(shown, npc_cards, npc_ids):
        parts = [card.get("description", "").strip(), card.get("personality", "").strip(),
                 card.get("scenario", "").strip()]
        anchor = overlay.voice_anchor_record(cid, char_id)["text"]
        cast_blocks.append({
            "name": name,
            "description": "\n".join(p for p in parts if p),
            "anchor": voice_anchors.effective(anchor),
            "example": voice_anchors.truncate((card.get("mes_example") or "").strip(),
                                              voice_anchors.VOICE_EXAMPLE_CAP),
        })
    # A COUNT, not a length: the voice policy renders on cast size, and reading
    # `len(cast_blocks)` would let a later per-block filter move it.
    named_npc_count = sum(1 for b in cast_blocks if b["name"])
```

Add both to the `data` dict beside `"npc_cards"`:

```python
        "cast_blocks": cast_blocks,
        "named_npc_count": named_npc_count,
```

**Imports.** `assemble.py` already binds `overlay` (in its `from .. import (...)` block) and `cast_data` (`from . import cast as cast_data`, line 50) — verified, use those names. **Add `voice_anchors`** to the same `from .. import (...)` block, alphabetically between `turnstate` and the end. `cast.py` already binds `scenes_serialize` (`from ..scenes import serialize as scenes_serialize`, line 21); Task 2 needs no new import.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_context.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/context/assemble.py backend/tests/test_context.py
git commit -m "One resolved cast structure, for every section that names somebody"
```

---

### Task 4: The three voice sections, and retiring `message_examples`

Each section carries **its own heading**. This is a deliberate departure from `off_scene_cast_active.j2` / `off_scene_cast_known.j2`, which share one via a macro that tier 3 suppresses `if not offscene_active` — `offscene_active` is *data*, not render state, while `layout.py` lets a user disable and reorder any section, so that precedent is broken (issue #423). Any data-derived "am I first?" test has the same defect.

**Files:**
- Create: `templates/scene/sections/voice_policy.j2`, `templates/scene/sections/voice_anchors.j2`, `templates/scene/sections/voice_examples.j2`
- Delete: `templates/scene/sections/message_examples.j2`
- Modify: `backend/src/grimoire/store/context/assemble.py` (the `SECTIONS` catalog, ~line 425)
- Test: `backend/tests/test_context.py`

**Interfaces:**
- Consumes: `cast_blocks`, `named_npc_count` (Task 3).
- Produces: section ids `voice_policy`, `voice_anchors`, `voice_examples`; section id `message_examples` no longer exists.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_context.py  (append)
def test_voice_policy_renders_on_cast_size_with_no_anchors(tmp_path):
    """The day-one case: nobody has an anchor yet, and the differentiation
    rule is the whole benefit until they do."""
    cid, sid = _campaign_with_two_bare_npcs(tmp_path)
    text = _section_text(cid, sid, "voice_policy")
    assert "distinguishable by their dialogue alone" in text


def test_voice_policy_renders_for_an_examples_only_scene(tmp_path):
    """One NPC, no anchor, but a mes_example -- the policy states the
    precedence the examples are read under, so it has to be there."""
    cid, sid = _campaign_with_npc(tmp_path, name="Mara", mes_example="Mara: Fine.")
    assert "distinguishable by their dialogue alone" in _section_text(cid, sid, "voice_policy")


def test_voice_policy_is_silent_for_a_lone_bare_npc(tmp_path):
    cid, sid = _campaign_with_npc(tmp_path, name="Mara")
    assert _section_text(cid, sid, "voice_policy") == ""


def test_a_nameless_npc_triggers_no_voice_section(tmp_path):
    """Render conditions must read the same filtered set the blocks do, or a
    nameless anchored NPC renders a policy whose subject is then suppressed."""
    cid, sid = _campaign_with_npc(tmp_path, name="", anchor="Clipped.")
    for sec in ("voice_policy", "voice_anchors", "voice_examples"):
        assert _section_text(cid, sid, sec) == ""


def test_voice_anchors_names_each_character(tmp_path):
    cid, sid = _campaign_with_npc(tmp_path, name="Mara", anchor="Clipped. Never contracts.")
    text = _section_text(cid, sid, "voice_anchors")
    assert "## Mara" in text
    assert "Clipped. Never contracts." in text


def test_voice_examples_labels_the_sample(tmp_path):
    cid, sid = _campaign_with_npc(tmp_path, name="Mara", mes_example="Mara: Fine.")
    text = _section_text(cid, sid, "voice_examples")
    assert "## Mara" in text
    assert "Mara: Fine." in text


def test_each_voice_section_carries_its_own_heading(tmp_path):
    """Disable one and the other still opens with its heading -- the bug in
    the off-scene precedent (#423), not repeated here."""
    cid, sid = _campaign_with_npc(tmp_path, name="Mara", anchor="Clipped.",
                                  mes_example="Mara: Fine.")
    _disable_section("voice_anchors")
    assert _section_text(cid, sid, "voice_examples").startswith("# Voice — example dialogue")


def test_a_reordered_voice_section_still_carries_its_heading(tmp_path):
    """Not just disabled: reordered, and separated by another section. A
    data-derived 'am I first?' test passes the disabled case and fails these."""
    cid, sid = _campaign_with_npc(tmp_path, name="Mara", anchor="Clipped.",
                                  mes_example="Mara: Fine.")
    _reorder_sections(["voice_examples", "character_state", "voice_anchors"])
    assert _section_text(cid, sid, "voice_examples").startswith("# Voice — example dialogue")
    assert _section_text(cid, sid, "voice_anchors").startswith("# Voice — how they sound")


def test_the_three_newcomers_land_after_character_descriptions():
    """No stored layout: catalog order, all three together."""
    order = [s.id for s in layout.merge(assemble.SECTIONS, [])]
    i = order.index("character_descriptions")
    assert order[i + 1:i + 4] == ["voice_policy", "voice_anchors", "voice_examples"]


def test_the_newcomers_follow_a_disabled_or_repositioned_predecessor():
    """`_ordered` anchors newcomers to their catalog neighbour wherever the
    layout put it, enabled or not -- its docstring says so, and this pins it."""
    stored = [{"id": "plot_threads"}, {"id": "character_descriptions", "enabled": False}]
    order = [s.id for s in layout.describe(assemble.SECTIONS, stored)]
    order_ids = [r["id"] if isinstance(r, dict) else r for r in order]
    i = order_ids.index("character_descriptions")
    assert order_ids[i + 1:i + 4] == ["voice_policy", "voice_anchors", "voice_examples"]


def test_the_policy_survives_a_budget_that_drops_both_other_sections(tmp_path):
    """voice_policy is LOCK_IN and the other two are SPOTLIGHT. This is the
    tier split's whole point and what a later 'simplification' would collapse.

    It does NOT assert examples-before-anchors: pack drops the LARGEST ACTUAL
    section within a tier, so that ordering is a tendency, not an invariant.
    """
    cid, sid = _campaign_with_npc(tmp_path, name="Mara", anchor="Clipped.",
                                  mes_example="Mara: Fine." * 200)
    _set_context_budget(tiny_enough_to_drop_spotlight)
    sections = {r["id"]: r for r in _packed_sections(cid, sid)}
    assert not sections["voice_policy"]["dropped"]
    assert sections["voice_examples"]["dropped"]


def test_examples_drop_before_anchors_when_examples_are_actually_larger(tmp_path):
    """The manufactured case, asserted as a tendency of largest-first packing
    and nothing more."""
    cid, sid = _campaign_with_npc(tmp_path, name="Mara", anchor="Clipped.",
                                  mes_example="Mara: Fine." * 500)
    _set_context_budget(just_tight_enough_for_one_drop)
    sections = {r["id"]: r for r in _packed_sections(cid, sid)}
    assert sections["voice_examples"]["dropped"]
    assert not sections["voice_anchors"]["dropped"]


def test_message_examples_section_is_gone():
    assert not any(s.id == "message_examples" for s in assemble.SECTIONS)
    assert {"voice_policy", "voice_anchors", "voice_examples"} <= {s.id for s in assemble.SECTIONS}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_context.py -k voice -q`
Expected: FAIL — the templates do not exist

- [ ] **Step 3: Write the templates and edit the catalog**

`templates/scene/sections/voice_policy.j2`:

```jinja
{#- The standing voice rule and the precedence order. Fixed text: no
    per-character content, which is why it can sit at LOCK_IN while the two
    per-cast sections cannot.

    Renders when a NAMED entry has an anchor, a NAMED entry has an example, or
    at least two entries are named. "Named" matters: the voice sections skip
    nameless entries, so a condition counting ANY entry would render this
    policy over a subject that is then entirely suppressed.

    It says "a character's voice description" rather than "the section below"
    because voice_anchors.j2 is SPOTLIGHT and may legitimately be dropped or
    disabled while this survives.
    Vars: cast_blocks, named_npc_count. -#}
{%- set named = cast_blocks | selectattr("name") | list -%}
{%- set any_anchor = named | selectattr("anchor") | list -%}
{%- set any_example = named | selectattr("example") | list -%}
{%- if any_anchor or any_example or named_npc_count >= 2 %}# Voice

Each character present must be distinguishable by their dialogue alone. If
two of their lines could be exchanged without a reader noticing, the reply
has failed, whatever else it got right. Differences in diction, rhythm,
formality and what a person will not say are the point, not decoration.

Where a character's voice description and their example dialogue disagree,
the description wins — it is maintained, the examples are a snapshot. Where
either disagrees with a general prose rule elsewhere in this prompt, the
character's voice wins: those rules exist to stop generic writing, not to
flatten a specific person. An outstanding voice correction outranks all of
it — it is the most recent and most specific feedback about this character.
{%- endif -%}
```

`templates/scene/sections/voice_anchors.j2`:

```jinja
{#- How each present character sounds, one block per NAMED entry that has an
    anchor. A nameless entry contributes nothing: an unattributed anchor is
    the very defect this section exists to fix.
    Vars: cast_blocks. -#}
{%- set blocks = [] -%}
{%- for b in cast_blocks -%}
{%- if b.name and b.anchor -%}
{%- set _ = blocks.append("## " ~ b.name ~ "\n" ~ b.anchor) -%}
{%- endif -%}
{%- endfor -%}
{%- if blocks %}# Voice — how they sound

{{ blocks | join("\n\n") }}
{%- endif -%}
```

`templates/scene/sections/voice_examples.j2`:

```jinja
{#- Example dialogue, name-labelled. Today's message_examples.j2 concatenated
    these with no name attached, so a SillyTavern-style card whose samples lean
    on {{char}} and unattributed narration was attributed by guesswork.
    Vars: cast_blocks. -#}
{%- set blocks = [] -%}
{%- for b in cast_blocks -%}
{%- if b.name and b.example -%}
{%- set _ = blocks.append("## " ~ b.name ~ "\n" ~ b.example) -%}
{%- endif -%}
{%- endfor -%}
{%- if blocks %}# Voice — example dialogue

{{ blocks | join("\n\n") }}
{%- endif -%}
```

In `assemble.py`'s `SECTIONS`, delete the `message_examples` entry and insert immediately after `character_descriptions`:

```python
    # Three sections rather than one, because they are three kinds of thing and
    # pack.py's tiers already name the difference. The policy is fixed-length
    # instruction text, which is LOCK_IN's own description; the other two are
    # per-character information, which is cast-sized and therefore exactly what
    # must NOT be pinned. Anchors are not guaranteed to outlive examples --
    # pack drops the largest ACTUAL section within a tier, and a pinned section
    # never drops at all -- so nothing here may depend on that ordering.
    Section("voice_policy", "Voice · the rule",
            "scene/sections/voice_policy.j2", pack.LOCK_IN),
    Section("voice_anchors", "Voice · how they sound",
            "scene/sections/voice_anchors.j2", pack.SPOTLIGHT),
    Section("voice_examples", "Voice · example dialogue",
            "scene/sections/voice_examples.j2", pack.SPOTLIGHT),
```

Delete `templates/scene/sections/message_examples.j2`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_context.py -q`
Expected: PASS

- [ ] **Step 5: Regenerate the frozen-campaign snapshot, and READ the diff**

This task changes the assembled prompt, and `sweep.py:197-198` captures
`context.build_messages` plus the inspector's section rows — so the fixture
moves **now**, not in a later task. Leaving it stale would hand every
subsequent task's author a knowingly red tree, which is exactly the state that
makes a real regression invisible.

```bash
cd backend
$env:PYTHONPATH="src"; .venv\Scripts\python.exe -m tests.fixtures.frozen_campaign.sweep
git diff backend/tests/fixtures/frozen_campaign/snapshot.json
```

Read the diff. Expect: `message_examples` rows gone, three `voice_*` rows
present, examples now under `## <Name>` headings. Anything else is a bug in
this task. `home/` is never regenerated.

- [ ] **Step 6: Run the backend suite to confirm the tree is green**

Run: `cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m pytest -q`
Expected: PASS — including `test_frozen_campaign.py` and `test_docs_guard.py`.

#### The layout migration (same task, same commit)

A stored entry naming the retired id must not silently return as an enabled section — that is a change to what the model receives, not a preference to re-toggle. It ships in this commit because retiring an id and migrating it are one deliverable; split across two commits there is a window where a deliberately disabled section is back on.

**Additional files:**
- Modify: `backend/src/grimoire/store/context/layout.py`
- Test: `backend/tests/test_context_layout.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `layout._migrate(stored: list[dict]) -> list[dict]` — pure, applied at the head of both `merge` and `describe`.

- [ ] **Step 8: Write the failing tests**

```python
# backend/tests/test_context_layout.py  (append)
from grimoire.store.context import assemble, layout


def test_a_malformed_layout_still_merges_as_empty():
    """The defensive contract `merge`/`describe` document. `_migrate` runs
    ahead of `_ordered`, so it owns this now."""
    for bad in (None, 17, "nonsense", {}):
        assert [s.id for s in layout.merge(assemble.SECTIONS, bad)] ==             [s.id for s in assemble.SECTIONS]


def test_a_disabled_message_examples_stays_disabled_as_voice_examples():
    stored = [{"id": "message_examples", "enabled": False}]
    merged = layout.merge(assemble.SECTIONS, stored)
    assert not any(s.id == "voice_examples" for s in merged)


def test_the_migrated_entry_keeps_its_position():
    stored = [{"id": "message_examples"}, {"id": "character_descriptions"}]
    order = [s.id for s in layout.merge(assemble.SECTIONS, stored)]
    assert order.index("voice_examples") < order.index("character_descriptions")


def test_the_legacy_label_is_dropped():
    stored = [{"id": "message_examples", "label": "My examples"}]
    merged = layout.merge(assemble.SECTIONS, stored)
    sec = next(s for s in merged if s.id == "voice_examples")
    assert sec.label == "Voice · example dialogue"


def test_a_newer_voice_examples_entry_wins_over_the_legacy_one():
    stored = [{"id": "message_examples", "enabled": False}, {"id": "voice_examples"}]
    merged = layout.merge(assemble.SECTIONS, stored)
    assert any(s.id == "voice_examples" for s in merged)


def test_describe_and_merge_agree_about_the_migrated_entry():
    """Same migration on both paths, or the editor reverses the disable on save."""
    stored = [{"id": "message_examples", "enabled": False}]
    shown = {row["id"]: row for row in layout.describe(assemble.SECTIONS, stored)}
    assert shown["voice_examples"]["enabled"] is False
```

Check `layout.describe`'s actual return shape before writing the last test and match it.

- [ ] **Step 9: Run tests to verify they fail**

Run: `cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_context_layout.py -q`
Expected: FAIL — the legacy entry is ignored and `voice_examples` enters enabled

- [ ] **Step 10: Write the implementation**

```python
#: Section ids that were renamed, old -> new. A stored layout naming the old id
#: is rewritten before the merge sees it.
_RENAMED = {"message_examples": "voice_examples"}


def _migrate(stored: list) -> list:
    """`stored` with renamed section ids rewritten, position and `enabled` kept.

    Applied at the head of BOTH `merge` and `describe`, which share `_ordered`:
    migrating on one path only would let the renderer honour a stored
    `enabled: false` while the editor showed the new section switched on --
    and reversed the user's choice the moment they saved.

    The stored LABEL is dropped with the rename. A label is the reader's name
    for a section whose meaning has now narrowed, so carrying it forward would
    caption the new section with a description of the old one.

    A read-time alias, not a persisted rewrite: nothing here writes the layout
    file. When both ids are present the NEW one wins -- it is the one the
    current editor produced, so it is the more recent statement of intent.
    """
    # `merge` and `describe` both promise that malformed stored data merges as
    # EMPTY rather than raising -- the alternative takes scene generation down
    # over a preference. Running ahead of `_ordered` means this function now
    # owns that promise for anything it is handed.
    if not isinstance(stored, list):
        return []
    out, seen = [], set()
    for entry in stored:
        if not isinstance(entry, dict):
            continue
        sid = entry.get("id")
        if sid in _RENAMED:
            new_id = _RENAMED[sid]
            if any(isinstance(e, dict) and e.get("id") == new_id for e in stored):
                continue          # the newer entry is authoritative
            entry = {k: v for k, v in entry.items() if k != "label"}
            entry["id"] = new_id
            sid = new_id
        if sid in seen:
            continue
        seen.add(sid)
        out.append(entry)
    return out
```

Call it at the top of `merge` and `describe`: `stored = _migrate(stored)`.

- [ ] **Step 11: Run tests to verify they pass**

Run: `cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_context_layout.py -q`
Expected: PASS

- [ ] **Step 12: Commit**

```bash
git add templates/scene/sections/ backend/src/grimoire/store/context/ backend/tests
git rm templates/scene/sections/message_examples.j2
git commit -m "Voice reaches the prompt, and a retired section stays retired"
```

---

### Task 5: `character_descriptions.j2` reads `cast_blocks`

Three NPCs on scene currently means three personality paragraphs the model attributes by inference. A nameless card **keeps its description** and loses only its heading — dropping it would remove content the model gets today.

**Files:**
- Modify: `templates/scene/sections/character_descriptions.j2`
- Test: `backend/tests/test_context.py`

**Interfaces:**
- Consumes: `cast_blocks` (Task 3).
- Produces: nothing new.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_context.py  (append)
def test_character_descriptions_are_name_labelled(tmp_path):
    cid, sid = _campaign_with_npc(tmp_path, name="Mara", description="A courier with debts.")
    text = _section_text(cid, sid, "character_descriptions")
    assert "## Mara" in text
    assert "A courier with debts." in text


def test_a_nameless_card_keeps_its_description_without_a_heading(tmp_path):
    cid, sid = _campaign_with_npc(tmp_path, name="", description="A courier with debts.")
    text = _section_text(cid, sid, "character_descriptions")
    assert "A courier with debts." in text
    assert "##" not in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_context.py -k descriptions -q`
Expected: FAIL — no `## Mara` heading

- [ ] **Step 3: Rewrite the template**

```jinja
{#- One block per present NPC: their description / personality / scenario,
    under their name. Reads `cast_blocks` rather than `npc_cards` so
    disambiguation and nameless handling are defined once, in one place, for
    this section and the three voice sections alike.

    A card with no name renders its description with NO heading rather than an
    empty one -- suppress rather than address a stranger, the same call
    `cast.py` makes. That leaves those descriptions unattributed, which is the
    defect this section otherwise fixes; there is no name to attribute them to,
    and a slug in the prompt would read as one.
    Vars: cast_blocks. -#}
{%- set blocks = [] -%}
{%- for b in cast_blocks -%}
{%- if b.description -%}
{%- set _ = blocks.append(("## " ~ b.name ~ "\n" ~ b.description) if b.name else b.description) -%}
{%- endif -%}
{%- endfor -%}
{{ blocks | join("\n\n") }}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_context.py -q`
Expected: PASS

- [ ] **Step 5: Regenerate the snapshot again and read the diff**

This task moves the prompt too — descriptions gain `## <Name>` headings.

```bash
cd backend
$env:PYTHONPATH="src"; .venv\Scripts\python.exe -m tests.fixtures.frozen_campaign.sweep
git diff backend/tests/fixtures/frozen_campaign/snapshot.json
```

Expect only description headings to appear. Then: `PYTHONPATH=src .venv/Scripts/python.exe -m pytest -q` → PASS.

- [ ] **Step 6: Commit**

```bash
git add templates/scene/sections/character_descriptions.j2 backend/tests backend/tests/fixtures
git commit -m "A description belongs to whoever it describes"
```

---

### Task 6: The anchorless backlog

The app reports the gap and stops. **Deliberately no bulk-generate button**, unlike the tagline backlog: generating a whole roster's anchors unattended would write inferred voices — which now steer every scene — with the same authority as hand-written ones, at a volume nobody will review.

**Files:**
- Modify: `backend/src/grimoire/store/characters.py:322`
- Modify: `backend/src/grimoire/routes/config.py` (`_public_config`)
- Modify: `frontend/src/components/CharacterEditor.tsx` (~line 263)
- Test: `backend/tests/test_characters_store.py`, `backend/tests/test_routes.py`, `frontend/src/components/CharacterEditor.test.tsx`

**Interfaces:**
- Consumes: `voice_anchors.read_record`, `voice_anchors.VOICE_ANCHOR_CAP` (Task 1).
- Produces: `list_characters` rows gain `has_voice_anchor: bool`; `GET /config` gains `voice_anchor_cap: int`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_characters_store.py  (append)
def test_list_characters_reports_a_written_anchor(tmp_path):
    root = _world_with_character(tmp_path, "mara")
    voice_anchors.write(root, "mara", "Clipped. Never contracts.")
    row = next(c for c in characters.list_characters(root) if c["id"] == "mara")
    assert row["has_voice_anchor"] is True


def test_list_characters_reports_a_missing_anchor(tmp_path):
    root = _world_with_character(tmp_path, "mara")
    row = next(c for c in characters.list_characters(root) if c["id"] == "mara")
    assert row["has_voice_anchor"] is False


def test_a_world_level_tombstone_reads_as_no_anchor(tmp_path):
    """At world level a tombstone and an absence are the same state -- there is
    nothing beneath to inherit from, so both are a gap the backlog should show."""
    root = _world_with_character(tmp_path, "mara")
    voice_anchors.disable(root, "mara")
    row = next(c for c in characters.list_characters(root) if c["id"] == "mara")
    assert row["has_voice_anchor"] is False
```

```python
# backend/tests/test_routes.py  (append)
def test_config_exposes_the_anchor_cap(client):
    assert client.get("/api/config").json()["voice_anchor_cap"] == \
        store.voice_anchors.VOICE_ANCHOR_CAP
```

```tsx
// frontend/src/components/CharacterEditor.test.tsx  (append)
test("world scope counts characters with no voice anchor", async () => {
  renderEditor({ worldScope: true, chars: [
    { id: "mara", name: "Mara", has_voice_anchor: false },
    { id: "winifred", name: "Winifred", has_voice_anchor: true },
  ]});
  expect(await screen.findByText(/1 character has no voice anchor/i)).toBeTruthy();
});

test("no bulk-generate button is offered for anchors", async () => {
  renderEditor({ worldScope: true, chars: [{ id: "mara", name: "Mara", has_voice_anchor: false }] });
  await screen.findByText(/no voice anchor/i);
  expect(screen.queryByRole("button", { name: /derive .*anchors/i })).toBeNull();
});

test("the backlog count is world scope only", async () => {
  renderEditor({ worldScope: false, chars: [{ id: "mara", name: "Mara", has_voice_anchor: false }] });
  await screen.findByText("Mara");
  expect(screen.queryByText(/no voice anchor/i)).toBeNull();
});

test("the over-cap warning uses the cap from /config", async () => {
  renderEditor({ worldScope: true, config: { voice_anchor_cap: 10 },
                 chars: [{ id: "mara", name: "Mara", has_voice_anchor: true }] });
  fireEvent.click(await screen.findByText("Mara"));
  fireEvent.click(await screen.findByText("Edit"));
  fireEvent.change(await screen.findByLabelText("Voice anchor"),
                   { target: { value: "x".repeat(11) } });
  expect(await screen.findByText(/Over 10 characters/)).toBeTruthy();
});

test("astral characters count as one each, not two", async () => {
  // six emoji: six code points but twelve UTF-16 units. A naive .length warns.
  const emoji = String.fromCodePoint(0x1F600).repeat(6);
  renderEditor({ worldScope: true, config: { voice_anchor_cap: 10 },
                 chars: [{ id: "mara", name: "Mara", has_voice_anchor: true }] });
  fireEvent.click(await screen.findByText("Mara"));
  fireEvent.click(await screen.findByText("Edit"));
  fireEvent.change(await screen.findByLabelText("Voice anchor"),
                   { target: { value: emoji } });
  expect(screen.queryByText(/Over 10 characters/)).toBeNull();
});
```

Match `renderEditor`'s real signature in that suite rather than the sketch above.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_characters_store.py tests/test_routes.py -q`
Then, **from `frontend/`** (running vitest from the repo root skips
`vitest.config.ts` and disables `globals`, failing every mock-based test):
`cd frontend && npx vitest run src/components/CharacterEditor.test.tsx`
Expected: FAIL — `KeyError: 'has_voice_anchor'`, and no such text in the DOM

- [ ] **Step 3: Write the implementation**

In `characters.py`'s `list_characters`, beside `"tagline"`:

```python
                # A BOOLEAN, not the body: the listing has no use for the text,
                # and this call already stats every version and every image of
                # every character. One added read per character, and it returns
                # a flag.
                "has_voice_anchor": bool(voice_anchors.read(root, cid)),
```

Import `voice_anchors` at module scope alongside `taglines`.

In `_public_config`'s returned dict:

```python
            # A CONSTANT, not a setting: the editor warns above it, and a
            # duplicated TypeScript literal would drift from the backend's
            # truncation silently.
            "voice_anchor_cap": store.voice_anchors.VOICE_ANCHOR_CAP,
```

**1. The row type** — `frontend/src/api/types.ts:444`, in `CharacterSummary`, beside `tagline?: string`:

```ts
  has_voice_anchor?: boolean;
```

Optional, deliberately: `CharacterSummary` is built in several fixtures and a
required field would fail `tsc` in files this task does not otherwise touch.

**2. The backlog** — `CharacterEditor.tsx`, immediately after `untagged` (line 263):

```tsx
  // The world's voice-anchor backlog. World scope only, exactly like
  // `untagged` — an anchor is a world-level property of the character.
  // REPORTED, never bulk-filled: an inferred anchor now steers every scene,
  // so a roster-wide unattended derive would write voices nobody reviewed.
  const anchorless = worldScope ? chars.filter((c) => !c.has_voice_anchor) : [];
```

**3. The count** — in the same toolbar as the tagline button (~line 1613), a
hint rather than a button:

```tsx
  {worldScope && anchorless.length > 0 && (
    <span className="field-hint">
      {anchorless.length === 1
        ? "1 character has no voice anchor"
        : `${anchorless.length} characters have no voice anchor`}
    </span>
  )}
```

**4. The cap** — `CharacterEditor` reads it from the config the app already
loads via `api.getConfig()`. Add `voice_anchor_cap?: number` to the config type
in `frontend/src/api/types.ts` beside the other numeric settings, and render a
warning under the anchor textarea:

```tsx
  {voiceAnchorCap && [...voiceAnchor].length > voiceAnchorCap && (
    <p className="field-hint">
      Over {voiceAnchorCap} characters — the rest is not sent to the model.
    </p>
  )}
```

`[...voiceAnchor].length`, **not** `.length`: Python counts code points and
JavaScript's `.length` counts UTF-16 units, so an anchor of astral characters
reads as double and would warn at half the real cap.

- [ ] **Step 4: Run tests AND the type gate**

Run both commands from Step 2 — expected PASS. Then the frontend type check,
because this task edits a shared type and one vitest file does not typecheck
the fixtures that build it:

```bash
cd frontend && npm run typecheck
```

Expected: PASS. The new fields are optional precisely so existing fixtures do
not have to be updated; a compile failure here means something else.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/characters.py backend/src/grimoire/routes/config.py frontend/src backend/tests
git commit -m "Show which characters have no voice, and offer to fill none of them"
```

---

### Task 7: The drift judge sees a correction that is actually in force

`voice_policy` tells the generator an outstanding correction outranks the anchor, but `voice_drift/user.j2` supplies only `name`, `anchor` and `transcript`. So a model obeying "use contractions, the last scene was too stiff" gets flagged against an anchor saying "never uses contractions" — and the flag mints another correction, making the false positive self-reinforcing.

Three changes move **together**. Changing only `user.j2` builds an evaluator whose system message says anchor-forbidden contractions are drift while its user message says prefer the correction requiring them.

**Files:**
- Modify: `templates/voice_drift/system.j2`, `templates/voice_drift/user.j2`
- Modify: `backend/src/grimoire/store/voice_drift.py` (`build_prompt`)
- Modify: `backend/src/grimoire/routes/scenes.py:1967`
- Test: `backend/tests/test_voice_drift_store.py`

**Interfaces:**
- Consumes: `voice_drift.fingerprint_matches(stored_fp, text, anchor_id) -> bool` (existing).
- Produces: `voice_drift.build_prompt(name: str, anchor: str, transcript: str, correction: str = "") -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_voice_drift_store.py  (append)
def test_the_judge_is_told_the_correction_supersedes_the_anchor():
    msgs = voice_drift.build_prompt(
        "Mara", "Never uses contractions.", "Mara: I'm fine.",
        correction="Use contractions; the last scene was too stiff.")
    blob = "\n".join(m["content"] for m in msgs)
    assert "Use contractions; the last scene was too stiff." in blob
    # POSITIVE: precedence is stated
    assert "correction" in blob.lower() and "supersede" in blob.lower()


def test_the_judge_prompt_no_longer_defines_drift_against_the_anchor_alone():
    """NEGATIVE, and the reason this test exists: an implementation that bolts
    a precedence sentence onto the old absolute wording satisfies the positive
    assertion while still contradicting itself."""
    system = voice_drift.build_prompt("Mara", "Never uses contractions.", "x")[0]["content"]
    assert "the anchor rules out" not in system
    assert "consistent with the anchor" not in system


def test_the_judge_and_the_prompt_see_the_same_capped_anchor():
    """One transformation, two consumers -- asserted end to end rather than
    trusted. A rule past the cap must be invisible to BOTH."""
    long_anchor = "Clipped. " + ("x" * voice_anchors.VOICE_ANCHOR_CAP) + " NEVER CONTRACTS."
    sent = voice_anchors.effective(long_anchor)
    judged = voice_drift.build_prompt("Mara", sent, "Mara: Fine.")[1]["content"]
    assert "NEVER CONTRACTS." not in sent
    assert "NEVER CONTRACTS." not in judged


def test_no_correction_leaves_the_user_message_as_it_was():
    with_none = voice_drift.build_prompt("Mara", "Clipped.", "Mara: Fine.")
    assert "correction" not in with_none[1]["content"].lower()
```

```python
# backend/tests/test_voice_drift_store.py  (append — a unit test of the rule,
# not the route, so it runs under this task's own commands)
def test_a_correction_whose_anchor_was_replaced_is_not_in_force(tmp_path):
    """The rule `_stage_voice_drift` must apply before building the judge
    prompt. `context/cast.py` already applies it before putting the note in
    the SCENE prompt; unchecked on the judge side, a retired note would be
    presented as overriding the anchor that replaced it -- and would mint a
    fresh flag against that new anchor.

    Asserted on `fingerprint_matches` directly, which is the whole decision:
    the route reduces to `prior if (not prior_fp or fingerprint_matches(...))
    else ""`.
    """
    root = tmp_path
    voice_anchors.write(root, "mara", "Never uses contractions.")
    first = voice_anchors.read_record(root, "mara")
    stale_fp = voice_drift.anchor_fingerprint(first["text"], first["id"])

    # the user replaces the anchor outright: cleared, then rewritten, which
    # mints a NEW anchor id (see voice_anchors.write)
    voice_anchors.write(root, "mara", "")
    voice_anchors.write(root, "mara", "Contractions are habitual.")
    second = voice_anchors.read_record(root, "mara")

    assert not voice_drift.fingerprint_matches(stale_fp, second["text"], second["id"])
    # and the still-current case is honoured, or the rule would suppress
    # every correction
    live_fp = voice_drift.anchor_fingerprint(second["text"], second["id"])
    assert voice_drift.fingerprint_matches(live_fp, second["text"], second["id"])
```

Confirm `voice_drift.anchor_fingerprint`'s real name and arity before writing
this — it is referenced in `voice_drift.py` and `cast.py:282`. If the helper is
private, assert through `stage_edit`/`read_record` instead rather than reaching
past the module's surface.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_voice_drift_store.py -q`
Expected: FAIL — `build_prompt() got an unexpected keyword argument 'correction'`

- [ ] **Step 3: Write the implementation**

Rewrite the opening of `templates/voice_drift/system.j2` so the standard is the anchor *as modified by* a valid correction. Replace the first two paragraphs and the `drift`/`in_voice` bullet definitions:

```
You are checking one character's dialogue in a played scene against their voice standard: their voice anchor, as modified by any outstanding correction shown with it.

Judge ONLY how the character sounds. A character may do anything, feel anything, or change their mind; that is the story, not drift. Drift is the character speaking in a register, diction, or rhythm their voice standard excludes — flattening into generic narrator prose, acquiring vocabulary or formality they do not have, losing a stated verbal habit, or sounding interchangeable with the rest of the cast.

Where a correction is shown, it is the more recent instruction and SUPERSEDES the anchor wherever the two conflict. The writer was given that correction and told to follow it, so lines that obey it are not drift even when the anchor alone would rule them out.

Be conservative. If the lines are consistent with that standard, report `in_voice`. A false alarm costs the next scene a correction it did not need.
```

Update the two bullets to say "sounded wrong against that standard" / "sounded right against that standard".

`templates/voice_drift/user.j2` gains an optional block:

```jinja
Character: {{ name }}

Voice anchor:
{{ anchor }}
{% if correction %}
Outstanding correction (more recent than the anchor; supersedes it on conflict):
{{ correction }}
{% endif %}
Scene transcript:
{{ transcript }}
```

`voice_drift.build_prompt` gains the parameter:

```python
def build_prompt(name: str, anchor: str, transcript: str, correction: str = "") -> list[dict]:
    """The judge's messages.

    `correction` is the character's outstanding drift note, and the CALLER is
    responsible for having validated its provenance -- this module is
    prompt/parse only and does not read the store. Passing a note whose
    fingerprint no longer matches the anchor would tell the judge that a
    retired instruction overrides the current one.
    """
    return [{"role": "system", "content": prompts.render("voice_drift/system.j2")},
            {"role": "user", "content": prompts.render(
                "voice_drift/user.j2", name=name, anchor=anchor,
                transcript=transcript, correction=correction)}]
```

At `routes/scenes.py:1967`, validate before building. `prior` and `prior_fp` are already read one line above:

```python
            # Only a correction that is STILL IN FORCE may reach the judge.
            # `_voice_notes` applies exactly this test before putting a note in
            # the scene prompt (context/cast.py); without it here, a note
            # fingerprinted to a REPLACED anchor is suppressed for the writer
            # and presented to the judge as current -- which mints a fresh flag
            # against the anchor that replaced it. "" is a pre-nonce flag,
            # which counts as valid for the reason voice_drift documents.
            live = prior if (not prior_fp or store.voice_drift.fingerprint_matches(
                prior_fp, record["text"], record["id"])) else ""
            # The FINGERPRINT is taken over the raw stored anchor -- that is
            # what `stage_edit` and `_voice_notes` compare against, and capping
            # it here would retire every correction whose anchor happens to be
            # long. The anchor SENT to the judge is the effective one, because
            # the generator only ever saw that much of it: a rule past the cap
            # is enforced against neither, which is the entire point of
            # `effective()` having two consumers.
            msgs = store.voice_drift.build_prompt(
                name, store.voice_anchors.effective(record["text"]), transcript,
                correction=live)
```

- [ ] **Step 4: Run tests to verify they pass**

Run the two files this task actually changes, by path rather than by `-k`
(the earlier draft filtered on `-k "drift"`, which matched neither the new
route behaviour nor several of these tests — a verification step that could
pass with the implementation absent):

```bash
cd backend
PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_voice_drift_store.py -q
PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_routes.py -q
```

Expected: PASS. Then the whole backend suite, since this task edits a route:
`PYTHONPATH=src .venv/Scripts/python.exe -m pytest -q`

- [ ] **Step 5: Commit**

```bash
git add templates/voice_drift/ backend/src/grimoire/store/voice_drift.py backend/src/grimoire/routes/scenes.py backend/tests
git commit -m "The judge is shown the correction the writer was actually given"
```

---

### Task 8: Documentation, guards, and the full gate

The repo holds prose to code by test, so leaving these is a failing gate rather than untidiness.

**Files:**
- Modify: `backend/src/grimoire/store/voice_anchors.py` (module docstring)
- Modify: `templates/scene/sections/natural_prose.j2` (precedence paragraph)
- Modify: `frontend/src/components/CharacterEditor.tsx:2198` (field hint)
- Modify: `evals/` (require the policy text verbatim)
- Modify: `CLAUDE.md`
- Regenerate: `backend/tests/fixtures/frozen_campaign/snapshot.json`, `lint-baselines/*.json`

- [ ] **Step 1: Rewrite `voice_anchors.py`'s module docstring**

It currently asserts the anchor "is never sent as part of a scene — which is what lets it describe a voice", and builds a paragraph on why it differs from `mes_example`. Both are now false. State instead: the anchor **is** sent, the distinction from `mes_example` is *describe* vs *demonstrate*, and the drift check is an **approximate second opinion** — it compares against the character's *current* anchor, which may differ from what any turn received (substitution, a packer drop, a layout disable, an edit between play and absorb, a locally-edited template). Say so explicitly, so a later reader does not reconstruct a stronger claim from the anchor's new position.

- [ ] **Step 2: Amend `natural_prose.j2`'s precedence paragraph**

It ends "Everything else here holds regardless", excepting only the reply format, established facts and the prose style guide. `voice_policy` says a character's voice overrides general prose rules. Left as-is the prompt contradicts itself — an anchor requiring a signature repetitive construction or the word "indeed" collides head-on. Add the voice policy to that exception list so the two blocks state one precedence order.

- [ ] **Step 3: Update the editor's field hint**

`CharacterEditor.tsx:2198` reads "how they SOUND — absorb checks each scene against this and flags drift; clear it to skip the check". The anchor now steers every turn; say so, since that is where a user decides how much care the text deserves.

- [ ] **Step 4: Require the policy text in the evals**

`evals/run.py` already requires several sections **verbatim** in the assembled
prompt. Find that collection first — `grep -n "verbatim\|REQUIRED\|must appear" evals/run.py`
and read `evals/README.md` — then add the `voice_policy` text to it, as one
entry alongside the budget / reply-format / roll-protocol / active-speaker /
available-art entries.

The string to require is the **first sentence of the differentiation rule**,
not the whole block: it is the load-bearing instruction, and pinning the entire
two-paragraph text would make every wording tweak an eval edit for no gain.

```
Each character present must be distinguishable by their dialogue alone.
```

If that collection turns out to be keyed by section id rather than by literal
text, follow the existing convention instead of introducing a second one.

- [ ] **Step 5: Add the CLAUDE.md line**

Record that the voice anchor is now a **prompt input**, not only a judge input, and that the drift check is advisory — the assumption a future change is most likely to break.

- [ ] **Step 6: Regenerate the frozen-campaign snapshot, reviewing the diff**

```bash
cd backend && $env:PYTHONPATH="src"; .venv\Scripts\python.exe -m tests.fixtures.frozen_campaign.sweep
```

`sweep.py:197-198` captures `context.build_messages` and the inspector's section rows, so this **will** move. **Read the new text before committing** — a blind regenerate is exactly what the fixture exists to prevent. `home/` is never regenerated.

- [ ] **Step 7: Run the full gate and re-baseline**

```bash
make check
```

If a lint gate fails because a finding was **resolved** (the recorded count is
now stale — an improvement fails the ratchet too), re-baseline and then run the
gate **again**, because the baseline files are themselves inputs to it:

```bash
make baseline
git diff lint-baselines/     # the counts should only ever shrink
make check
```

Expected: green on the second run. Claiming green after `make baseline` without
re-running `make check` verifies nothing. If `check-templates` fails,
`scripts/verify_templates.py` disagrees with a builder — fix the template, not
the script.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "The docs say what the anchor now does, and the gate agrees"
```

---

## Self-Review

**Spec coverage.** §1 three sections + tiers → Task 4; layout migration → Task 4 (same commit, so a retired id never returns as an enabled section mid-branch). §2 `cast_blocks` contract → Task 3; disambiguation → Task 2; stripping → Tasks 2–3. §3 render conditions and canonical policy text → Task 4; caps and truncation → Task 1; `character_descriptions` → Task 5. §5 anchorless flag, `/config` cap, no bulk button → Task 6. §7 judge correction + provenance + the effective anchor reaching both consumers → Task 7. §8 known limits → documented, no code. §9 docs and guards → Task 8.

**Type consistency.** `truncate(text, cap)` / `effective(text)` (Task 1) are called with those signatures in Task 3 and Task 7. `display_names(names)` (Task 2) is called on `raw_names` in Task 3. `cast_blocks` keys `name`/`description`/`anchor`/`example` are written in Task 3 and read in Tasks 4 and 5. `build_prompt(name, anchor, transcript, correction="")` (Task 7) matches its call site in the same task. `has_voice_anchor` is spelled identically across Task 6's backend, type, component and tests.

**Verified rather than assumed.** Task 1's `truncate` was traced against all seven of its tests. Task 2's `display_names` was **executed** against all seven of its cases, including the literal-collision case, before being written down — an earlier draft used `scenes.serialize.confusable`, which is the wrong tool: its `names` argument is the full roster *including* the name, so `confusable("Mara", ["Winifred"])` is `True`, and suffixing cannot satisfy it anyway (`confusable("Winifred #1", ["Winifred #1", "Winifred #2"])` stays `True`). Every test-file path in this plan was checked to exist.

**Green at every task boundary.** Tasks 4 and 5 change the assembled prompt, so each regenerates `snapshot.json` and runs the full backend suite *within the task* — the fixture is not left stale for a later task's author to trip over.

**Known gaps carried deliberately.** Anchors are *not* guaranteed to outlive examples (same tier; `pack` drops the largest actual section, and a pin never drops) — Task 4 asserts the ordering only for a manufactured larger-examples case and says so. Neither voice section is bounded in aggregate, which is safe only because both are droppable. Nameless cards' descriptions stay unattributed. All three are recorded in spec §8.
