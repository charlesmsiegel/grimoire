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
