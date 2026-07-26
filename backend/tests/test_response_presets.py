import pytest

from grimoire.store import response_presets as rp


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GRIMOIRE_TEMPLATES", str(tmp_path / "templates"))


def _write(dir_path, pid, **fields):
    dir_path.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{k}: {v}\n" for k, v in fields.items())
    (dir_path / f"{pid}.md").write_text(f"---\n{body}---\n", encoding="utf-8")


def test_named_form_supplies_all_five_knobs(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "home" / "response_presets", "slow-burn",
           name="Slow Burn", style_id="gothic-horror", length_preset="cinematic")
    supplied = rp.supplies(rp.read_preset("slow-burn")["meta"])
    assert supplied["style_id"] == "gothic-horror"
    assert supplied["reply_words"] == 900
    assert supplied["blocks_per_speaker"] == 2


def test_explicit_form_supplies_only_the_knobs_present(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "home" / "response_presets", "clipped",
           name="Clipped", reply_words="220", speakers="2")
    supplied = rp.supplies(rp.read_preset("clipped")["meta"])
    assert supplied == {"reply_words": 220, "speakers": 2}
    # the unnamed knobs are NOT defaulted — they keep resolving outward
    assert "blocks" not in supplied


def test_neither_form_is_a_valid_style_only_preset(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "home" / "response_presets", "just-gothic",
           name="Just Gothic", style_id="gothic-horror")
    supplied = rp.supplies(rp.read_preset("just-gothic")["meta"])
    assert supplied == {"style_id": "gothic-horror"}


def test_unknown_length_preset_invalidates_the_whole_record(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "home" / "response_presets", "broken",
           name="Broken", style_id="gothic-horror",
           length_preset="nonesuch", reply_words="220")
    # invalid: supplies NOTHING, not even its style, and the ignored explicit
    # value must never spring to life because the name was mistyped
    assert rp.supplies(rp.read_preset("broken")["meta"]) is None


def test_named_form_ignores_explicit_keys_entirely(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "home" / "response_presets", "both",
           name="Both", length_preset="terse", reply_words="9999")
    assert rp.supplies(rp.read_preset("both")["meta"])["reply_words"] == 150


def test_malformed_knob_is_absent_not_defaulted(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "home" / "response_presets", "sloppy",
           name="Sloppy", reply_words="lots", speakers="3")
    supplied = rp.supplies(rp.read_preset("sloppy")["meta"])
    assert supplied == {"speakers": 3}


def test_style_none_sentinel_clears(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "home" / "response_presets", "bare",
           name="Bare", style_id="none", length_preset="terse")
    assert rp.supplies(rp.read_preset("bare")["meta"])["style_id"] == ""


def test_empty_style_is_no_opinion(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "home" / "response_presets", "lengthy",
           name="Lengthy", style_id="", length_preset="terse")
    assert "style_id" not in rp.supplies(rp.read_preset("lengthy")["meta"])


def test_list_merges_builtin_and_custom(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "response_presets", "brisk",
           name="Brisk", length_preset="brisk")
    _write(tmp_path / "home" / "response_presets", "mine", name="Mine")
    items = {p["id"]: p for p in rp.list_presets()}
    assert items["brisk"]["built_in"] is True
    assert items["mine"]["built_in"] is False


def test_read_missing_raises(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    with pytest.raises(rp.PresetNotFound):
        rp.read_preset("nonesuch")


def test_shipped_builtins_are_length_only(monkeypatch, tmp_path):
    # against the REAL templates/ dir: the four shipped presets must not
    # disturb styles, so none of them may specify style_id
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    ids = {p["id"] for p in rp.list_presets() if p["built_in"]}
    assert ids == {"terse", "brisk", "standard", "cinematic"}
    for pid in ids:
        supplied = rp.supplies(rp.read_preset(pid)["meta"])
        assert "style_id" not in supplied, pid
        assert supplied["reply_words"] > 0


def _scope(preset="", style="", **knobs):
    meta = {}
    if preset:
        meta["response_preset"] = preset
    if style:
        meta["style_id"] = style
    for k, v in knobs.items():
        meta[f"length_{k}"] = str(v)
    return meta


def test_no_settings_anywhere_falls_back_to_standard(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    got = rp.resolve()
    assert got["reply_words"] == 550
    assert got["style_id"] == ""
    assert got["provenance"]["reply_words"]["scope"] == "default"


def test_narrower_preset_wins_for_length(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "response_presets", "terse",
           name="Terse", length_preset="terse")
    _write(tmp_path / "templates" / "response_presets", "cinematic",
           name="Cinematic", length_preset="cinematic")
    got = rp.resolve(scene_meta=_scope(preset="terse"),
                     campaign_meta=_scope(preset="cinematic"))
    assert got["reply_words"] == 150
    assert got["provenance"]["reply_words"]["scope"] == "scene"


def test_length_only_preset_does_not_wipe_a_broader_loose_style(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "styles", "gothic-horror", name="Gothic Horror")
    _write(tmp_path / "templates" / "response_presets", "terse",
           name="Terse", length_preset="terse")
    got = rp.resolve(scene_meta=_scope(preset="terse"),
                     campaign_meta=_scope(style="gothic-horror"))
    assert got["style_id"] == "gothic-horror"
    assert got["reply_words"] == 150


def test_length_only_preset_does_not_wipe_a_broader_preset_style(tmp_path, monkeypatch):
    """The case an earlier draft of the design got wrong."""
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "styles", "gothic-horror", name="Gothic Horror")
    _write(tmp_path / "templates" / "response_presets", "terse",
           name="Terse", length_preset="terse")
    _write(tmp_path / "home" / "response_presets", "slow-burn",
           name="Slow Burn", style_id="gothic-horror", length_preset="cinematic")
    got = rp.resolve(scene_meta=_scope(preset="terse"),
                     config=_scope(preset="slow-burn"))
    assert got["style_id"] == "gothic-horror"
    assert got["reply_words"] == 150


def test_global_default_style_id_spelling(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "styles", "gothic-horror", name="Gothic Horror")
    got = rp.resolve(config={"default_style_id": "gothic-horror"})
    assert got["style_id"] == "gothic-horror"


def test_loose_override_beats_its_own_scopes_preset(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "response_presets", "cinematic",
           name="Cinematic", length_preset="cinematic")
    got = rp.resolve(campaign_meta=_scope(preset="cinematic", speakers=3))
    assert got["speakers"] == 3
    assert got["reply_words"] == 900


def test_stale_broad_override_cannot_haunt_a_narrow_preset(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "response_presets", "cinematic",
           name="Cinematic", length_preset="cinematic")
    got = rp.resolve(scene_meta=_scope(preset="cinematic"),
                     config=_scope(reply_words=90))
    assert got["reply_words"] == 900


def test_style_only_preset_leaves_length_resolving_outward(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "styles", "gothic-horror", name="Gothic Horror")
    _write(tmp_path / "home" / "response_presets", "just-gothic",
           name="Just Gothic", style_id="gothic-horror")
    _write(tmp_path / "templates" / "response_presets", "cinematic",
           name="Cinematic", length_preset="cinematic")
    got = rp.resolve(scene_meta=_scope(preset="just-gothic"),
                     campaign_meta=_scope(preset="cinematic"))
    assert got["style_id"] == "gothic-horror"
    assert got["reply_words"] == 900


def test_none_sentinel_clears_a_broader_style(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "styles", "gothic-horror", name="Gothic Horror")
    _write(tmp_path / "home" / "response_presets", "bare",
           name="Bare", style_id="none", length_preset="terse")
    got = rp.resolve(scene_meta=_scope(preset="bare"),
                     campaign_meta=_scope(style="gothic-horror"))
    assert got["style_id"] == ""


def test_unknown_style_id_continues_outward(tmp_path, monkeypatch):
    """styles.resolve_style skips unresolvable ids so a stale reference never
    breaks generation -- the new cascade must not regress that."""
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "styles", "gothic-horror", name="Gothic Horror")
    got = rp.resolve(scene_meta=_scope(style="deleted-style"),
                     campaign_meta=_scope(style="gothic-horror"))
    assert got["style_id"] == "gothic-horror"


def test_missing_or_invalid_preset_is_skipped(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "response_presets", "cinematic",
           name="Cinematic", length_preset="cinematic")
    _write(tmp_path / "home" / "response_presets", "broken",
           name="Broken", length_preset="nonesuch")
    assert rp.resolve(scene_meta=_scope(preset="ghost"),
                      campaign_meta=_scope(preset="cinematic"))["reply_words"] == 900
    assert rp.resolve(scene_meta=_scope(preset="broken"),
                      campaign_meta=_scope(preset="cinematic"))["reply_words"] == 900


def test_turn_scope_is_narrowest(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "response_presets", "terse",
           name="Terse", length_preset="terse")
    _write(tmp_path / "templates" / "response_presets", "cinematic",
           name="Cinematic", length_preset="cinematic")
    got = rp.resolve(turn=_scope(preset="terse"), scene_meta=_scope(preset="cinematic"))
    assert got["reply_words"] == 150


def test_malformed_scope_override_is_ignored(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    got = rp.resolve(scene_meta={"length_reply_words": "-4"})
    assert got["reply_words"] == 550


def test_result_is_always_complete(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    got = rp.resolve(scene_meta=_scope(preset="ghost"))
    for knob in rp.lengths.KNOBS:
        assert isinstance(got[knob], int) and got[knob] > 0
    assert isinstance(got["style_id"], str)


def test_unreadable_preset_falls_through_instead_of_raising(tmp_path, monkeypatch):
    """A damaged or externally-edited file is an invalid record, not a crash:
    one corrupt preset must not take the whole scene down."""
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "response_presets", "cinematic",
           name="Cinematic", length_preset="cinematic")
    d = tmp_path / "home" / "response_presets"
    d.mkdir(parents=True, exist_ok=True)
    (d / "broken.md").write_bytes(b"---\nname: \xff\xfe not utf-8 \xff\n---\n")
    got = rp.resolve(scene_meta=_scope(preset="broken"),
                     campaign_meta=_scope(preset="cinematic"))
    assert got["reply_words"] == 900          # kept walking to the campaign
