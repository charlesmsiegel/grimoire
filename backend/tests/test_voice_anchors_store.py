import pytest

from grimoire.store import campaigns, characters, overlay, voice_anchors, worlds


def _root(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    root = worlds.world_root(worlds.create_world("Realm"))
    characters.create_character(root, "Winifred", "main", characters.blank_card("Winifred"))
    return root


def test_read_missing_is_empty(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    assert voice_anchors.read(root, "winifred") == ""


def test_write_then_read_roundtrip(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    voice_anchors.write(root, "winifred", "  Clipped. Never uses contractions.  ")
    assert voice_anchors.read(root, "winifred") == "Clipped. Never uses contractions."


def test_blank_write_removes_the_anchor(monkeypatch, tmp_path):
    """Absence is the signal: voice_drift only judges characters that HAVE an
    anchor, so emptying one has to turn drift detection back off rather than
    leave a file that reads as "" and judges against nothing."""
    root = _root(monkeypatch, tmp_path)
    voice_anchors.write(root, "winifred", "Clipped.")
    assert voice_anchors.anchor_path(root, "winifred").exists()
    voice_anchors.write(root, "winifred", "   ")
    assert not voice_anchors.anchor_path(root, "winifred").exists()
    assert voice_anchors.read(root, "winifred") == ""


def test_blank_write_on_a_missing_anchor_is_a_no_op(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    voice_anchors.write(root, "winifred", "")          # must not raise
    assert voice_anchors.read(root, "winifred") == ""


def test_write_rejects_ids_that_escape_the_characters_dir(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    outside = tmp_path / "pwned.md"
    for bad in ("../../pwned", "..\\..\\pwned", "..", ".", ""):
        with pytest.raises(voice_anchors.BadAnchorId):
            voice_anchors.write(root, bad, "owned")
    assert not outside.exists()


def test_read_rejects_ids_that_escape_the_characters_dir(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    assert voice_anchors.read(root, "../../anything") == ""


def test_build_prompt_reads_the_speech_bearing_fields_only(monkeypatch, tmp_path):
    """An anchor describes how a character SOUNDS. Feeding it the description or
    scenario is how anchors turn into biographies, which drift judging cannot
    use."""
    msgs = voice_anchors.build_prompt({
        "name": "Winifred", "personality": "Wry and wary.",
        "mes_example": "**Winifred:** Try me.",
        "system_prompt": "Voice her with dry wit.",
        "description": "Tall, sharp-eyed smuggler.", "scenario": "Runs the night dock."})
    assert msgs[0]["role"] == "system"
    body = msgs[1]["content"]
    assert "Wry and wary" in body and "Try me" in body and "dry wit" in body
    assert "sharp-eyed" not in body and "night dock" not in body


def test_build_prompt_marks_missing_fields(monkeypatch, tmp_path):
    msgs = voice_anchors.build_prompt({"name": "Winifred"})
    assert msgs[1]["content"].count("(none)") == 3


def test_campaign_reads_inherit_the_world_anchor(monkeypatch, tmp_path):
    """The anchor is world-level (a voice is a library property), so a campaign
    that has not diverged sees the world's — the same per-file overlay taglines
    get."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Winifred", "main", characters.blank_card("Winifred"))
    voice_anchors.write(wroot, "winifred", "Clipped. Never uses contractions.")
    cid = campaigns.create_campaign("Run", wid)
    assert overlay.voice_anchor(cid, "winifred") == "Clipped. Never uses contractions."

    # A campaign-side anchor wins over the world's.
    voice_anchors.write(campaigns.campaign_root(cid), "winifred", "Warmer here.")
    assert overlay.voice_anchor(cid, "winifred") == "Warmer here."


def test_saving_a_legacy_anchor_unchanged_does_not_mint_a_nonce(monkeypatch, tmp_path):
    """A pre-nonce anchor saved back unchanged must stay pre-nonce. Minting an
    id moves its fingerprint off the legacy content-only formula, and every flag
    judged against it would then read as citing a replaced standard — retiring
    real correctives on a save where the user changed no text."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    root = tmp_path / "w"
    p = voice_anchors.anchor_path(root, "winifred")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("Clipped. Never uses contractions.\n", encoding="utf-8")   # no frontmatter
    assert voice_anchors.read_record(root, "winifred")["id"] == ""

    voice_anchors.write(root, "winifred", "Clipped. Never uses contractions.")
    assert voice_anchors.read_record(root, "winifred")["id"] == ""     # still legacy
    # whitespace-only differences are the same text, so they are the same anchor
    voice_anchors.write(root, "winifred", "  Clipped. Never uses contractions.  ")
    assert voice_anchors.read_record(root, "winifred")["id"] == ""

    # a real edit is a real anchor, and gets an identity
    voice_anchors.write(root, "winifred", "Warm and rambling now.")
    assert voice_anchors.read_record(root, "winifred")["id"] != ""


def test_anchor_ids_use_the_shared_safe_id_rules(monkeypatch, tmp_path):
    """A colon or a trailing dot ALIASES a real character's directory rather
    than escaping it — `store / "C:evil"` discards the prefix on Windows, and
    Win32 trims a trailing dot so "winifred." and "winifred" are one directory."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    root = tmp_path / "w"
    for bad in ("winifred.", "winifred ", "C:evil", "a:b"):
        with pytest.raises(voice_anchors.BadAnchorId):
            voice_anchors.anchor_path(root, bad)
