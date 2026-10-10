"""The suggestion prompt's instruction section and the whole intent prompt,
pinned byte for byte as they were before Slice E (capstone spec §15, §15.1).

The golden file was generated on pre-change code by `python -m
tests.suggest_golden` and is never regenerated to make a test pass: these two
prompts are promised unchanged, so a diff here is the promise breaking.
"""

from __future__ import annotations

import json

from grimoire import prompts, schemas
from grimoire.store import suggest
from tests import suggest_golden


def _golden() -> dict:
    return json.loads(suggest_golden.GOLDEN.read_text(encoding="utf-8"))


def test_the_instruction_section_is_pinned():
    golden = _golden()
    for label, (snap, cands, offscreen, direction) in suggest_golden.SYSTEM_SNAPS.items():
        got = [suggest.build_prompt(snap, cands, offscreen, direction)[0]]
        assert got == golden[f"system/{label}"], label


#: What 01f's pilot appends to the intent system message, and the only
#: change to the pinned prompt it makes: the reply's JSON Schema, after
#: everything the golden holds, in the one spelling `inference.generate`
#: checks the prompt for.
SCHEMA_BLOCK = "\n\nThe object matches this JSON Schema:\n\n{}"


def test_the_intent_prompt_is_pinned(monkeypatch, tmp_path):
    """Byte for byte, the golden's user message, and its system message
    followed by exactly the schema block (01f's pilot) -- the golden itself
    is not regenerated."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    golden = _golden()
    for label, (cid, offscreen) in suggest_golden.intent_campaigns().items():
        got = suggest.build_intent_prompt(cid, suggest_golden.TYPED, offscreen=offscreen)
        pinned = golden[f"intent/{label}"]
        block = SCHEMA_BLOCK.format(schemas.render(suggest.intent_schema(cid, offscreen)))
        assert got[1] == pinned[1], label
        assert got[0] == {**pinned[0], "content": pinned[0]["content"] + block}, label


def test_the_golden_file_has_every_variant(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assert set(_golden()) == set(suggest_golden.variants())


def test_the_golden_renders_the_today_lines():
    """Task 2 lifts the calendar block (`today_facts`, `day_facts`, `sooner`)
    into one helper; a pin that never rendered `holidays_today` or
    `events_today` would pass a refactor that dropped either."""
    golden = _golden()
    users = [m["content"] for k, msgs in golden.items() if k.startswith("intent/")
             for m in msgs if m["role"] == "user"]
    assert any(" Today: " in u and " Scheduled today: " in u for u in users)


#: A two-row driver index: enough to switch the drivers addendum on.
_TWO_ROWS = [
    {"ref": "thread:a", "kind": "thread", "label": "Mara's map", "summary": "", "actors": [],
     "status": "open", "pressure": {"state": "stale", "in_days": None, "friendly": ""},
     "time_anchors": [], "links": [], "dormancy": 4},
    {"ref": "commitment:b", "kind": "commitment", "label": "Mara's oath", "summary": "",
     "actors": [], "status": "open",
     "pressure": {"state": "due_soon", "in_days": 2, "friendly": ""},
     "time_anchors": [], "links": [], "dormancy": 1},
]


def test_the_instruction_section_survives_a_driver_index():
    """§28.6: "with no controls, the instruction section is byte-identical" --
    the drivers addendum follows it, and nothing in it moves."""
    golden = _golden()
    for label, (snap, cands, offscreen, direction) in suggest_golden.SYSTEM_SNAPS.items():
        indexed = {**snap, "driver_index": _TWO_ROWS}
        addendum = prompts.render("scene_suggestions/instruction/drivers_addendum.j2",
                                  view=suggest.driver_view(indexed, suggest.NO_CONTROLS))
        assert addendum.startswith(" ") and not addendum.startswith("  ")
        got = suggest.build_prompt(indexed, cands, offscreen, direction)[0]["content"]
        assert got == golden[f"system/{label}"][0]["content"] + addendum, label


def test_controls_append_after_the_drivers_addendum():
    golden = _golden()
    controls = suggest.Controls(focus=("thread:a",))
    for label, (snap, cands, offscreen, direction) in suggest_golden.SYSTEM_SNAPS.items():
        indexed = {**snap, "driver_index": _TWO_ROWS}
        view = suggest.driver_view(indexed, controls)
        drivers = prompts.render("scene_suggestions/instruction/drivers_addendum.j2", view=view)
        tail = prompts.render("scene_suggestions/instruction/controls_addendum.j2", view=view)
        assert tail.startswith(" ") and not tail.startswith("  ")
        got = suggest.build_prompt(indexed, cands, offscreen, direction,
                                   controls=controls)[0]["content"]
        assert got == golden[f"system/{label}"][0]["content"] + drivers + tail, label
