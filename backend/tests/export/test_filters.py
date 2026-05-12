from __future__ import annotations

from grimoire.export import FilterContext, apply_filters


def test_strip_ooc_removes_parenthetical_and_bracket_forms() -> None:
    text = (
        "Alistair turns. (OOC: brb)\n"
        "He raises his hand. [OOC quick question]\n"
        "OOC: by the way\n"
        "He smiles."
    )
    ctx = FilterContext()
    out = apply_filters(text, ctx)
    assert "OOC" not in out
    assert "Alistair turns." in out
    assert "He smiles." in out


def test_strip_mechanics_removes_chips_and_brackets() -> None:
    text = "Roll: 6d10 → 4 successes\nShe lunges forward.\n[roll: perception 8]\nHe recoils."
    ctx = FilterContext(strip_mechanics=True)
    out = apply_filters(text, ctx)
    assert "Roll:" not in out
    assert "[roll" not in out
    assert "She lunges forward." in out
    assert "He recoils." in out


def test_anonymize_replaces_pc_name() -> None:
    text = "Alistair pauses; Alistair Hyde-Smythe inclines his head."
    ctx = FilterContext(anonymize={"Alistair": "A.", "Alistair Hyde-Smythe": "A. H.-S."})
    out = apply_filters(text, ctx)
    assert "Alistair" not in out
    assert "A. H.-S." in out
    assert "A. " in out


def test_apply_filters_collapses_excess_blanks() -> None:
    text = "First paragraph.\n\n\n\nSecond paragraph."
    out = apply_filters(text, FilterContext())
    assert out.count("\n\n") == 1


def test_strip_narrator_scaffolding_removes_scene_break_markers() -> None:
    text = "[scene break]\nA new day. (julian rolls perception)\nHe nods."
    ctx = FilterContext(strip_narrator_scaffolding=True)
    out = apply_filters(text, ctx)
    assert "scene break" not in out.lower()
    assert "rolls" not in out.lower()
    assert "A new day." in out
    assert "He nods." in out
