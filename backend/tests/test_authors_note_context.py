"""Author's notes, the placement half (play controls V) --
`store/context/authors_note.py`, pure over hand-built messages."""

from grimoire import prompts
from grimoire.store.context import story
from grimoire.store.context.authors_note import applicable, inject, note_turn, render, split_point
from grimoire.store.scenes import serialize as scenes_serialize


def post(n):
    return {"role": "user", "content": f"P{n}"}


def reply(n):
    return {"role": "assistant", "content": f"R{n}", "speaker": "Mara"}


H = [post(1), reply(1), post(2), reply(2), post(3), reply(3)]
C = {"text": "Storm.", "depth": 4, "every": 1}
S = {"text": "Rain.", "depth": 2, "every": 1}
M = {"text": "Whisper.", "depth": 0, "every": 1}


def test_split_point_depths():
    assert split_point(H, 4) == 2          # before the fourth-most-recent post
    assert split_point(H, 0) == 6          # after the last post
    assert split_point(H, 50) == 0         # clamped to the start
    assert split_point(H, 3) == 2          # R2 snaps back to P2
    assert split_point([reply(1), reply(2)], 1) == 0
    assert split_point([post(1), reply(1), post(2), post(3)], 1) == 2   # P3 is mid-run; snaps to the run's start
    assert split_point([], 4) == 0
    assert split_point([], 0) == 0


def test_note_turn_counts_excluded_and_director_lines():
    msgs = [post(1), {**post(2), "excluded": "2026-10-05T00:00:00"}, reply(1),
            {"role": "assistant", "content": "steer", "speaker": scenes_serialize.DIRECTOR_SPEAKER}]
    assert note_turn(msgs) == 3
    assert note_turn([]) == 0


def test_applicable_order_and_scoping():
    notes = {"campaign": C, "scenes": {"i" * 32: S}, "characters": {"characters:mara": M}}
    applied, _ = applicable(notes, "i" * 32, "characters:mara", "Mara", 1)
    assert [n["level"] for n in applied] == ["campaign", "scene", "character"]
    assert applied[2]["name"] == "Mara" and applied[0]["name"] == ""
    assert applicable(notes, "i" * 32, "grimoire", "Grimoire", 1)[0][-1]["level"] == "scene"
    assert applicable(notes, "i" * 32, None, "", 1)[0][-1]["level"] == "scene"
    assert [n["level"] for n in applicable(notes, None, None, "", 1)[0]] == ["campaign"]
    # Another character's note never applies.
    assert applicable(notes, None, "characters:winifred", "Winifred", 1)[0][-1]["level"] == "campaign"


def test_applicable_skips_by_cadence():
    every3 = {"campaign": {**C, "every": 3}, "scenes": {}, "characters": {}}
    applied, skipped = applicable(every3, None, None, "", 4)
    assert applied == [] and skipped[0]["every"] == 3
    assert applicable(every3, None, None, "", 0)[0] == []
    assert [n["level"] for n in applicable(every3, None, None, "", 6)[0]] == ["campaign"]


def test_render_levels():
    assert render({"level": "campaign", "name": "", "text": "Storm."}) == "[Author's note: Storm.]"
    assert render({"level": "character", "name": "Mara", "text": "Whisper."}) == \
        "[Author's note for Mara: Whisper.]"
    assert render({"level": "scene", "name": "", "text": "Rain."}) == prompts.render(
        "scene/authors_note.j2", level="scene", name="", text="Rain.")


def test_inject_is_its_own_message_and_never_merged():
    projected, pos = inject(H, [{"level": "campaign", "name": "", "text": "Storm.", "depth": 4,
                                 "every": 1}])
    assert projected[pos[0]["index"]] == {"role": "system", "content": "[Author's note: Storm.]"}
    assert "P2" in projected[pos[0]["index"] + 1]["content"]
    assert projected[:pos[0]["index"]] == story._project_history(H[:2])
    assert pos[0]["status"] == "applied"
    # Everything but the note is exactly the plain projection.
    assert [m for i, m in enumerate(projected) if i != pos[0]["index"]] == story._project_history(H)


def test_inject_orders_notes_at_one_point_and_spreads_others():
    notes = [{"level": "campaign", "name": "", "text": "A", "depth": 0, "every": 1},
             {"level": "scene", "name": "", "text": "B", "depth": 0, "every": 1},
             {"level": "character", "name": "Mara", "text": "C", "depth": 50, "every": 1}]
    projected, pos = inject(H, notes)
    by_level = {p["level"]: p["index"] for p in pos}
    assert by_level["character"] == 0
    assert by_level["campaign"] == len(projected) - 2 and by_level["scene"] == len(projected) - 1
    assert [p["level"] for p in pos] == ["campaign", "scene", "character"]
    for p in pos:
        assert projected[p["index"]]["role"] == "system"


def test_inject_counts_only_posts_in_context():
    hidden = {**post(9), "excluded": "2026-10-05T00:00:00"}
    note = {"role": "assistant", "content": "steer", "speaker": scenes_serialize.DIRECTOR_SPEAKER}
    msgs = [post(1), reply(1), post(2), reply(2), post(3), reply(3), hidden, note]
    projected, pos = inject(msgs, [{"level": "campaign", "name": "", "text": "S", "depth": 2,
                                    "every": 1}])
    assert "P3" in projected[pos[0]["index"] + 1]["content"]
    assert "P9" not in str(projected) and "steer" not in str(projected)


def test_inject_without_notes_is_the_plain_projection():
    assert inject(H, []) == (story._project_history(H), [])
