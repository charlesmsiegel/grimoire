from grimoire.store import length_drift, scenes

BUDGET = {"reply_words": 100, "blocks": 3, "paragraphs": 2,
          "speakers": 2, "blocks_per_speaker": 1}
CAST = ["Winifred Vance", "Mara"]


def _msg(speaker, words, paragraphs=1):
    para = " ".join(["word"] * max(words // paragraphs, 1))
    return {"role": "assistant", "speaker": speaker,
            "content": "\n\n".join([para] * paragraphs)}


def test_returns_none_without_turn_sizes():
    msgs = [_msg("Mara", 500)]
    assert length_drift.measure(msgs, [], CAST, BUDGET) is None


def test_returns_none_when_sizes_exceed_the_transcript():
    # a hand-edited file: fail safe rather than measure garbage
    msgs = [_msg("Mara", 50)]
    assert length_drift.measure(msgs, [5], CAST, BUDGET) is None


def test_pre_tracking_history_is_an_ignored_prefix():
    """An upgraded scene must become measurable once new generations land --
    not stay disabled forever because old untracked blocks outnumber them."""
    legacy = [_msg("Mara", 900), _msg(None, 900)]      # written before tracking
    tracked = [_msg("Mara", 40), _msg("Mara", 40)]
    got = length_drift.measure(legacy + tracked, [1, 1], CAST, BUDGET)
    assert got is not None
    assert got["totals"] == [40, 40]                    # the legacy bloat is ignored


def test_compliant_replies_produce_no_tier():
    msgs = [_msg("Mara", 40), _msg(None, 40)]
    got = length_drift.measure(msgs, [2], CAST, BUDGET)
    assert got["tier"] == ""
    assert got["blocks"] is False


def test_tiers_come_from_the_worst_turn_not_the_mean():
    msgs = [_msg("Mara", 130), _msg("Mara", 130), _msg("Mara", 100)]
    got = length_drift.measure(msgs, [1, 1, 1], CAST, BUDGET)
    assert got["tier"] == "trim"
    assert round(got["max_ratio"], 2) == 1.30


def test_no_oscillation_regression():
    """130/130/130 -> 130/130/100 -> 130/100/150 must stay ON throughout.
    Under a mean-driven signal the middle window clears at 1.20x."""
    for totals in ([130, 130, 130], [130, 130, 100], [130, 100, 150]):
        msgs = [_msg("Mara", n) for n in totals]
        got = length_drift.measure(msgs, [1, 1, 1], CAST, BUDGET)
        assert got["tier"] != "", totals


def test_clears_only_after_three_compliant_turns():
    msgs = [_msg("Mara", 300), _msg("Mara", 50), _msg("Mara", 50), _msg("Mara", 50)]
    got = length_drift.measure(msgs, [1, 1, 1, 1], CAST, BUDGET)
    assert got["tier"] == ""      # the 300 has rolled out of the 3-turn window


def test_cut_tier_above_175_percent():
    msgs = [_msg("Mara", 200)]
    assert length_drift.measure(msgs, [1], CAST, BUDGET)["tier"] == "cut"


def test_splitting_across_more_blocks_does_not_evade_the_budget():
    """The regression test for the per-block-budget loophole: six compliant
    blocks still bust a total-words budget."""
    msgs = [_msg("Mara", 40) for _ in range(6)]
    got = length_drift.measure(msgs, [6], CAST, BUDGET)
    assert got["tier"] == "cut"
    assert got["blocks"] is True


def test_speaker_aliases_count_as_one_character():
    msgs = [_msg("Winifred", 20), _msg("Winifred Vance", 20)]
    got = length_drift.measure(msgs, [2], CAST, BUDGET)
    assert got["speakers"] is False            # one character, cap is 2
    assert got["blocks_per_speaker"] is True   # ...who took two blocks, cap is 1


def test_unresolvable_label_counts_as_itself():
    msgs = [_msg("A Stranger", 20), _msg("Mara", 20), _msg("Winifred Vance", 20)]
    got = length_drift.measure(msgs, [3], CAST, BUDGET)
    assert got["speakers"] is True             # three distinct, cap is 2


def test_narration_is_not_a_speaker():
    msgs = [_msg(None, 20), _msg(None, 20), _msg("Mara", 20)]
    got = length_drift.measure(msgs, [3], CAST, BUDGET)
    assert got["speakers"] is False
    assert got["blocks"] is False              # 3 blocks, cap is 3


def test_paragraph_cap_uses_the_longest_block():
    msgs = [_msg("Mara", 30, paragraphs=3)]
    assert length_drift.measure(msgs, [1], CAST, BUDGET)["paragraphs"] is True


def test_reserved_speakers_are_separators_not_blocks():
    msgs = [_msg("Mara", 40),
            {"role": "assistant", "speaker": scenes.TRANSITION_SPEAKER,
             "content": "*Time passes. It is now dusk.*"},
            _msg("Mara", 40)]
    got = length_drift.measure(msgs, [1, 1], CAST, BUDGET)
    assert got["totals"] == [40, 40]     # NOT one merged 80-word turn
    assert got["blocks"] is False


def test_roll_fences_are_stripped_from_word_counts():
    msgs = [{"role": "assistant", "speaker": "Mara",
             "content": "She throws.\n\n```roll\n" + "x " * 200 + "\n```"}]
    got = length_drift.measure(msgs, [1], CAST, BUDGET)
    assert got["totals"] == [2]
    assert got["tier"] == ""


def test_user_messages_are_not_part_of_turns():
    msgs = [{"role": "user", "speaker": "You", "content": "w " * 500},
            _msg("Mara", 40)]
    got = length_drift.measure(msgs, [1], CAST, BUDGET)
    assert got["totals"] == [40]
