from grimoire.store import lengths


def test_four_presets_with_all_knobs():
    assert set(lengths.PRESETS) == {"terse", "brisk", "standard", "cinematic"}
    for name, preset in lengths.PRESETS.items():
        assert set(preset) == set(lengths.KNOBS), name
        assert all(isinstance(v, int) and v > 0 for v in preset.values()), name


def test_blocks_leave_room_for_narration():
    # narration is a block but is not a speaker, so every preset must allow
    # at least one block beyond its speaker cap
    for name, preset in lengths.PRESETS.items():
        assert preset["blocks"] > preset["speakers"], name


def test_presets_increase_monotonically():
    order = ["terse", "brisk", "standard", "cinematic"]
    for knob in lengths.KNOBS:
        values = [lengths.PRESETS[p][knob] for p in order]
        assert values == sorted(values), knob


def test_get_returns_a_copy():
    got = lengths.get("terse")
    got["reply_words"] = 99999
    assert lengths.PRESETS["terse"]["reply_words"] != 99999


def test_get_unknown_is_none():
    assert lengths.get("nonesuch") is None
    assert lengths.get("") is None


def test_default_is_standard():
    assert lengths.DEFAULT == "standard"
    assert lengths.get(lengths.DEFAULT) is not None


def test_coerce_accepts_positive_ints_only():
    assert lengths.coerce("300") == 300
    assert lengths.coerce(300) == 300
    assert lengths.coerce("0") is None
    assert lengths.coerce("-5") is None
    assert lengths.coerce("many") is None
    assert lengths.coerce("") is None
    assert lengths.coerce(None) is None
