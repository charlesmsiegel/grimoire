import pytest

from grimoire.store import characters, voice_drift, worlds


def _root(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    root = worlds.world_root(worlds.create_world("Realm"))
    characters.create_character(root, "Winifred", "main", characters.blank_card("Winifred"))
    return root


# ------------------------------------------------------------------ the flag

def test_read_missing_is_empty(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    assert voice_drift.read(root, "winifred") == ""


def test_write_then_read_roundtrip(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    voice_drift.write(root, "winifred", "  She hedged twice.  ")
    assert voice_drift.read(root, "winifred") == "She hedged twice."


def test_blank_write_clears_the_flag(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    voice_drift.write(root, "winifred", "She hedged twice.")
    voice_drift.write(root, "winifred", "")
    assert not voice_drift.flag_path(root, "winifred").exists()
    assert voice_drift.read(root, "winifred") == ""


def test_write_rejects_ids_that_escape_the_characters_dir(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    outside = tmp_path / "pwned.md"
    for bad in ("../../pwned", "..\\..\\pwned", "..", ".", ""):
        with pytest.raises(voice_drift.BadDriftId):
            voice_drift.write(root, bad, "owned")
    assert not outside.exists()


def test_read_rejects_ids_that_escape_the_characters_dir(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    assert voice_drift.read(root, "../../anything") == ""


# ----------------------------------------------------------------- the judge

def test_build_prompt_includes_name_anchor_and_transcript():
    msgs = voice_drift.build_prompt("Winifred", "Never uses contractions.",
                                    "USER: hi\nWINIFRED: I do not.")
    assert msgs[0]["role"] == "system"
    body = msgs[1]["content"]
    assert "Winifred" in body and "contractions" in body and "I do not" in body


def test_parse_output_reads_the_enum_not_the_prose():
    """The note's prose is ambiguous by nature -- "no drift, though she was a
    little terse" and "drift: she was a little terse" are the same sentence with
    opposite meanings. Only the verdict decides."""
    got = voice_drift.parse_output('{"verdict": "in_voice", "note": "a little terse"}')
    assert got == {"verdict": voice_drift.IN_VOICE, "note": "a little terse"}


def test_parse_output_tolerates_a_fenced_reply():
    got = voice_drift.parse_output(
        'Here you go:\n```json\n{"verdict": "drift", "note": "She used contractions."}\n```')
    assert got == {"verdict": voice_drift.DRIFT, "note": "She used contractions."}


def test_parse_output_accepts_unambiguous_synonyms():
    """The judge is an LLM; rejecting a reply that plainly said the right thing
    in the wrong word would turn a good judgment into a reported failure."""
    for raw, want in (("in voice", voice_drift.IN_VOICE), ("in-voice", voice_drift.IN_VOICE),
                      ("not enough", voice_drift.NOT_ENOUGH),
                      ("insufficient", voice_drift.NOT_ENOUGH),
                      ("unclear", voice_drift.NOT_ENOUGH),
                      ("  DRIFT  ", voice_drift.DRIFT)):
        assert voice_drift.parse_output('{"verdict": "%s"}' % raw)["verdict"] == want


def test_an_ambiguous_word_never_authorizes_a_clear():
    """Leniency is asymmetric: IN_VOICE is the only verdict that proposes a
    destructive clear, so a word that could mean something else must not reach
    it. "none" can mean "no drift" OR "no judgment"; "ok" can be an
    acknowledgement. Both fall through to UNKNOWN, which preserves the flag."""
    for raw in ("none", "ok", "fine", "n/a", "yes"):
        assert voice_drift.parse_output('{"verdict": "%s"}' % raw)["verdict"] \
            == voice_drift.UNKNOWN


def test_an_unreadable_reply_is_unknown_not_in_voice():
    """The distinction this enum exists for. A no-drift verdict is not inert --
    with a flag standing it proposes a CLEAR -- so a garbled reply must never
    collapse into "they sounded fine"."""
    for bad in ("I'm sorry, I can't do that.", '{"note": "no verdict"}',
                '{"verdict": null}', '{"verdict": "maybe?"}', '{"verdict": true}'):
        assert voice_drift.parse_output(bad)["verdict"] == voice_drift.UNKNOWN


def test_parse_output_nulls_collapse_to_empty():
    assert voice_drift.parse_output('{"verdict": "drift", "note": null}')["note"] == ""


def test_a_non_string_note_is_not_stringified_into_a_corrective():
    """`str()` on an object or a list renders it as Python source -- nonempty
    text that reads as a usable corrective, stages default-approved, and is then
    injected verbatim into every following turn's system prompt. Blank instead,
    which routes it to the caller's "drift reported with no corrective" failure."""
    for bad in ('{"tone": "terse"}', '["terse", "clipped"]', '42', 'true'):
        got = voice_drift.parse_output('{"verdict": "drift", "note": %s}' % bad)
        assert got["verdict"] == voice_drift.DRIFT   # the verdict itself was readable
        assert got["note"] == ""
        # and with no note there is nothing to stage, so no junk reaches disk
        assert voice_drift.stage_edit("winifred", "Winifred", "", got) is None


# ------------------------------------------------------------- stage_edit

def test_drift_stages_a_flag():
    e = voice_drift.stage_edit("winifred", "Winifred", "",
                               {"verdict": voice_drift.DRIFT,
                                "note": "She used contractions."})
    assert e["id"] == "voice_drift:winifred" and e["kind"] == "voice_drift"
    assert e["target"] == {"kind": "characters", "id": "winifred"}
    assert e["before"] == "" and e["after"] == "She used contractions."
    assert e["authored"] is False and "voice drift" in e["label"]


def test_drift_replaces_a_standing_flag_with_the_newer_note():
    e = voice_drift.stage_edit("winifred", "Winifred", "old note",
                               {"verdict": voice_drift.DRIFT, "note": "new note"})
    assert e["before"] == "old note" and e["after"] == "new note"


def test_the_same_note_again_proposes_nothing():
    assert voice_drift.stage_edit("winifred", "Winifred", "same",
                                  {"verdict": voice_drift.DRIFT, "note": "same"}) is None


def test_in_voice_with_a_standing_flag_stages_a_clear():
    """A character who has corrected course must stop being corrected -- the
    clear is the second half of the loop, not an afterthought."""
    e = voice_drift.stage_edit("winifred", "Winifred", "she hedged",
                               {"verdict": voice_drift.IN_VOICE, "note": ""})
    assert e["before"] == "she hedged" and e["after"] == ""
    assert "cleared" in e["label"]


def test_in_voice_with_no_flag_proposes_nothing():
    assert voice_drift.stage_edit("winifred", "Winifred", "",
                                  {"verdict": voice_drift.IN_VOICE, "note": ""}) is None


def test_drift_with_no_note_proposes_nothing():
    """The note IS the corrective, so a verdict without one is unusable. The
    route reports it as a failure; staging a noteless flag would put a blank
    instruction in front of the next generation."""
    assert voice_drift.stage_edit("winifred", "Winifred", "",
                                  {"verdict": voice_drift.DRIFT, "note": ""}) is None


def test_silence_never_clears_a_standing_flag():
    """Staged edits arrive default-approved, so proposing a clear is very nearly
    writing one. A character who simply stayed quiet has demonstrated nothing --
    the corrective holds until a scene actually shows the voice again."""
    assert voice_drift.stage_edit("winifred", "Winifred", "she hedged",
                                  {"verdict": voice_drift.NOT_ENOUGH, "note": ""}) is None


def test_an_unknown_verdict_never_clears_a_standing_flag():
    """Same reasoning, worse cause: no judgment was made at all."""
    assert voice_drift.stage_edit("winifred", "Winifred", "she hedged",
                                  {"verdict": voice_drift.UNKNOWN, "note": ""}) is None
    # and a garbled reply carrying a stray note must not raise one either
    assert voice_drift.stage_edit("winifred", "Winifred", "",
                                  {"verdict": voice_drift.UNKNOWN, "note": "junk"}) is None


# ------------------------------------------------------ anchor fingerprint

def test_the_staged_edit_records_the_anchor_it_was_judged_against():
    """The apply-time guard needs to know which standard produced the note --
    the anchor is editable while the review sits open."""
    e = voice_drift.stage_edit("winifred", "Winifred", "",
                               {"verdict": voice_drift.DRIFT, "note": "n"},
                               "Clipped. Never uses contractions.")
    assert e["payload"]["anchor"] == voice_drift.anchor_fingerprint(
        "Clipped. Never uses contractions.")


def test_reformatting_an_anchor_does_not_invalidate_a_finding():
    """Same standard, different whitespace. Invalidating on that would make an
    innocuous edit throw away a real finding."""
    assert (voice_drift.anchor_fingerprint("  Clipped.\n")
            == voice_drift.anchor_fingerprint("Clipped."))


def test_a_changed_anchor_fingerprints_differently():
    assert (voice_drift.anchor_fingerprint("Clipped.")
            != voice_drift.anchor_fingerprint("Warm and rambling."))


def test_an_absent_anchor_fingerprints_to_empty():
    """Left as "" rather than the hash of "": the absent case stays obviously
    distinct to anyone reading a staged edit."""
    assert voice_drift.anchor_fingerprint("") == ""
    assert voice_drift.anchor_fingerprint("   ") == ""


def test_the_flag_remembers_the_anchor_it_was_judged_against(monkeypatch, tmp_path):
    """absorb's apply-time guard only covers the pending-review window. A
    committed flag outlives it, so the provenance has to survive in the file."""
    root = _root(monkeypatch, tmp_path)
    fp = voice_drift.anchor_fingerprint("Clipped.")
    voice_drift.write(root, "winifred", "She hedged.", fp)
    assert voice_drift.read(root, "winifred") == "She hedged."
    assert voice_drift.judged_anchor(root, "winifred") == fp


def test_a_flag_without_recorded_provenance_reads_as_empty(monkeypatch, tmp_path):
    """Flags written before the field existed must not be invalidated by it --
    that would silently retire real user data on upgrade."""
    root = _root(monkeypatch, tmp_path)
    voice_drift.write(root, "winifred", "She hedged.")
    assert voice_drift.judged_anchor(root, "winifred") == ""


def test_clearing_removes_the_provenance_too(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    voice_drift.write(root, "winifred", "She hedged.", voice_drift.anchor_fingerprint("Clipped."))
    voice_drift.write(root, "winifred", "")
    assert voice_drift.judged_anchor(root, "winifred") == ""


def test_the_same_note_under_a_moved_anchor_stages_a_provenance_refresh():
    """The reader suppresses a flag whose anchor moved. If the next scene
    re-confirms that exact corrective against the NEW anchor and nothing is
    staged, the flag stays suppressed forever while absorb keeps reporting the
    character as flagged -- a corrective that exists, is re-confirmed every
    scene, and never reaches a prompt."""
    stale = voice_drift.anchor_fingerprint("The old anchor.", "old-nonce")
    e = voice_drift.stage_edit("winifred", "Winifred", "She hedged.",
                               {"verdict": voice_drift.DRIFT, "note": "She hedged."},
                               "The new anchor.", "new-nonce", stale)
    assert e is not None and e["before"] == e["after"] == "She hedged."
    assert "re-confirmed" in e["label"]
    assert e["payload"]["anchor"] == voice_drift.anchor_fingerprint("The new anchor.", "new-nonce")


def test_the_same_note_under_the_same_anchor_still_proposes_nothing():
    """The ordinary case must stay quiet -- otherwise every scene stages a
    no-op edit for every standing flag."""
    fp = voice_drift.anchor_fingerprint("Clipped.", "nonce")
    assert voice_drift.stage_edit("winifred", "Winifred", "She hedged.",
                                  {"verdict": voice_drift.DRIFT, "note": "She hedged."},
                                  "Clipped.", "nonce", fp) is None


def test_drift_ids_use_the_shared_safe_id_rules(tmp_path):
    """A blank clear reaches `flag_path` WITHOUT passing the character-existence
    check, so an id that aliases a real directory is enough to unlink someone
    else's flag. The separator checks alone let a colon and a trailing dot by."""
    for bad in ("winifred.", "winifred ", "C:evil", "a:b"):
        with pytest.raises(voice_drift.BadDriftId):
            voice_drift.flag_path(tmp_path, bad)


def test_reformatting_an_anchor_keeps_the_same_fingerprint():
    """Rewrapping a line or closing up a blank one is presentation, not a new
    standard -- and the anchor's own text reaches the judge either way. Under
    `strip()` alone these all differed, which silently retired every flag
    judged against them."""
    same = ["Clipped. Never uses contractions.",
            "  Clipped. Never uses contractions.  ",
            "Clipped.\nNever uses contractions.",
            "Clipped.\n\n   Never uses contractions.",
            "Clipped.\tNever  uses   contractions."]
    fps = {voice_drift.anchor_fingerprint(a, "nonce1") for a in same}
    assert len(fps) == 1
    # ...but different WORDS are still a different anchor
    assert voice_drift.anchor_fingerprint("Warm and rambling.", "nonce1") not in fps


def test_a_flag_digested_under_the_old_formula_still_matches():
    """The formula changed once. Comparing on equality alone would retire every
    flag committed before it, which is the same harm the legacy no-nonce
    formula exists to avoid, arriving by a different route."""
    anchor, nonce = "Clipped.\nNever uses contractions.", "nonce1"
    legacy = voice_drift._digest(anchor.strip(), nonce)      # pre-normalization spelling
    assert legacy != voice_drift.anchor_fingerprint(anchor, nonce)
    assert voice_drift.fingerprint_matches(legacy, anchor, nonce)
    assert voice_drift.fingerprint_matches(
        voice_drift.anchor_fingerprint(anchor, nonce), anchor, nonce)
    # a fingerprint of a genuinely different anchor still does not match
    assert not voice_drift.fingerprint_matches(
        voice_drift.anchor_fingerprint("Warm and rambling.", nonce), anchor, nonce)


def test_a_concurrently_cleared_flag_reads_as_absent(monkeypatch, tmp_path):
    """Same race as voice_anchors.read_record, same hot path: a clear committed
    by another request unlinks the file out from under this read."""
    voice_drift.write(tmp_path, "winifred", "She hedged.")
    assert voice_drift.read(tmp_path, "winifred") == "She hedged."   # positive control

    target = voice_drift.flag_path(tmp_path, "winifred")
    real = type(target).read_text
    def vanished(self, *a, **kw):
        if self == target:
            raise FileNotFoundError(2, "No such file or directory", str(self))
        return real(self, *a, **kw)
    monkeypatch.setattr(type(target), "read_text", vanished)

    assert voice_drift.read_record(tmp_path, "winifred") == {"note": "", "anchor": ""}


def test_clearing_an_already_cleared_flag_succeeds(tmp_path):
    """Same write-side race as the anchor: a clear is idempotent."""
    voice_drift.write(tmp_path, "winifred", "She hedged.")
    voice_drift.write(tmp_path, "winifred", "")
    voice_drift.write(tmp_path, "winifred", "")        # must not raise
    assert voice_drift.read(tmp_path, "winifred") == ""


def test_an_oversized_note_is_not_a_usable_corrective():
    """The flag renders into the post-history message, which the packer reserves
    and cannot trim -- so an unbounded note is charged against every later
    generation with nothing able to give way."""
    assert voice_drift.MAX_NOTE > 200        # room for the two sentences asked for
    long_note = "She hedged. " * 500
    assert len(long_note) > voice_drift.MAX_NOTE


# ---- the judge is shown the correction the writer was actually given ----
def test_the_judge_is_told_the_correction_supersedes_the_anchor():
    msgs = voice_drift.build_prompt(
        "Mara", "Never uses contractions.", "Mara: I'm fine.",
        correction="Use contractions; the last scene was too stiff.")
    blob = "\n".join(m["content"] for m in msgs)
    assert "Use contractions; the last scene was too stiff." in blob
    assert "supersede" in blob.lower()


def test_the_judge_prompt_no_longer_defines_drift_against_the_anchor_alone():
    """The NEGATIVE half, and the reason this test exists: an implementation
    that bolts a precedence sentence onto the old absolute wording satisfies
    the positive assertion while still contradicting itself."""
    system = voice_drift.build_prompt("Mara", "Never uses contractions.", "x")[0]["content"]
    assert "the anchor rules out" not in system
    assert "consistent with the anchor" not in system


def test_no_correction_leaves_the_user_message_as_it_was():
    """Byte-for-byte against the pre-change shape, not merely "the word
    'correction' is absent" -- that weaker assertion is satisfied by a user
    message which has lost the name, the anchor or the transcript entirely."""
    user = voice_drift.build_prompt("Mara", "Clipped.", "Mara: Fine.")[1]["content"]
    assert user.splitlines() == [
        "Character: Mara",
        "",
        "Voice anchor:",
        "Clipped.",
        "",
        "Scene transcript:",
        "Mara: Fine.",
    ]
