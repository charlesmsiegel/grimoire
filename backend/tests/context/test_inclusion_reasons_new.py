from grimoire.types.inclusion_reasons import InclusionReason


def test_new_block_reasons_exist():
    assert InclusionReason.SYSTEM_PROMPT == "system_prompt"
    assert InclusionReason.SCENE_HEADER == "scene_header"
    assert InclusionReason.VERBATIM_RECENT == "verbatim_recent"
    assert InclusionReason.PLAYER_INPUT == "player_input"
