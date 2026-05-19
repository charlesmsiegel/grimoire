from grimoire.auxiliary.prompts import load_template
from grimoire.auxiliary.types import TaskKind


def test_each_kind_has_template_file():
    for kind in TaskKind:
        assert load_template(kind) is not None


def test_impersonate_pc_renders_with_pc_name():
    rendered = load_template(TaskKind.IMPERSONATE_PC).render(
        pc_name="winifred",
        scene_summary="In the parlor at midnight.",
    )
    assert "winifred" in rendered
    assert "parlor at midnight" in rendered


def test_rewrite_post_renders_edit_instruction():
    rendered = load_template(TaskKind.REWRITE_POST).render(
        original_text="The crow lit on the wall.",
        edit_instruction="Make it more menacing.",
    )
    assert "menacing" in rendered
    assert "crow lit on the wall" in rendered


def test_continue_as_includes_character():
    rendered = load_template(TaskKind.CONTINUE_AS).render(character_name="Hyde-Smythe")
    assert "Hyde-Smythe" in rendered


def test_what_would_x_say_minimal():
    rendered = load_template(TaskKind.WHAT_WOULD_X_SAY).render(
        character_name="winifred", snippet="The carriage arrives an hour late."
    )
    assert "winifred" in rendered
    assert "carriage arrives" in rendered


def test_brainstorm_takes_prompt():
    rendered = load_template(TaskKind.BRAINSTORM).render(snippet="next scene ideas")
    assert "next scene ideas" in rendered


def test_edit_prose_takes_instruction():
    rendered = load_template(TaskKind.EDIT_PROSE).render(
        snippet="he runs fast", edit_instruction="more vivid"
    )
    assert "more vivid" in rendered


def test_translate_includes_target_language():
    rendered = load_template(TaskKind.TRANSLATE).render(
        snippet="The crow lit on the wall.",
        target_language="French",
    )
    assert "French" in rendered
