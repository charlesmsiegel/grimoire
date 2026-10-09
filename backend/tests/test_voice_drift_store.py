import json

import pytest

from grimoire import decisions, prompts
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
#
# Asked through `decide()` (slice F): the item, its prompt and the mapping back
# are pinned under "the judge as a decision item" below. Today's parse tests
# went with `parse_output`; every input they fed it is a legacy reply in the
# gate corpus (`evals/gate.py`'s `legacy_cases`, held by
# `test_every_legacy_parse_case_is_a_gate_entry`).

def _context(name: str, anchor: str, transcript: str, correction: str = "") -> str:
    return voice_drift.build_item(name, anchor, transcript, correction).context


def test_the_item_context_includes_name_anchor_and_transcript():
    body = _context("Winifred", "Never uses contractions.", "USER: hi\nWINIFRED: I do not.")
    assert "Winifred" in body and "contractions" in body and "I do not" in body


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
def _prompt(name: str, anchor: str, transcript: str, correction: str = "") -> list[dict]:
    """What `decide()` sends for one judge item."""
    from grimoire import inference
    return inference.structured_messages(
        [voice_drift.build_item(name, anchor, transcript, correction)],
        explain=voice_drift.explain())


def test_the_judge_is_told_the_correction_supersedes_the_anchor():
    msgs = _prompt("Mara", "Never uses contractions.", "Mara: I'm fine.",
                   correction="Use contractions; the last scene was too stiff.")
    blob = "\n".join(m["content"] for m in msgs)
    assert "Use contractions; the last scene was too stiff." in blob
    assert "supersede" in blob.lower()


def test_the_judge_prompt_no_longer_defines_drift_against_the_anchor_alone():
    """The NEGATIVE half, and the reason this test exists: an implementation
    that bolts a precedence sentence onto the old absolute wording satisfies
    the positive assertion while still contradicting itself."""
    blob = "\n".join(m["content"] for m in _prompt("Mara", "Never uses contractions.", "x"))
    assert "the anchor rules out" not in blob
    assert "consistent with the anchor" not in blob


def test_no_correction_leaves_the_context_as_it_was():
    """Byte-for-byte against the pre-change shape, not merely "the word
    'correction' is absent" -- that weaker assertion is satisfied by a context
    which has lost the name, the anchor or the transcript entirely."""
    assert _context("Mara", "Clipped.", "Mara: Fine.").splitlines() == [
        "Character: Mara",
        "",
        "Voice anchor:",
        "Clipped.",
        "",
        "Scene transcript:",
        "Mara: Fine.",
    ]


# ---- the judge as a decision item (slice F, spec 7.4) ----
#
# What the absorb phase sends through `decide()`, and how it maps the answer
# back to what `_stage_voice_drift` stores.

def _parse(reply: str, item: decisions.Item) -> decisions.ItemResult:
    (result,) = decisions.parse(reply, (item,), explain=True)
    return result


def _reply(verdict: object, rationale: object = "") -> str:
    return json.dumps({"0": {"answers": {"verdict": verdict}, "rationale": rationale}})


def test_build_item_keeps_the_user_message_and_offers_three_verdicts():
    """The item's context is the legacy user message (`user.j2`) byte for
    byte, with and without a correction; its one choice offers exactly today's three real
    verdicts, described by `option.j2`, and never a none-of-these."""
    for correction in ("", "Keep her answers short; the last scene let her ramble."):
        item = voice_drift.build_item("Mara", "Clipped.", "Mara: Fine.", correction)
        assert item.context == prompts.render("voice_drift/user.j2", name="Mara",
                                              anchor="Clipped.", transcript="Mara: Fine.",
                                              correction=correction)
        decisions.validate([item])
        (q,) = item.questions
        assert isinstance(q, decisions.Choice)
        assert q.id == voice_drift.QUESTION_ID == "verdict"
        assert q.allow_none is False
        assert q.instructions == prompts.render("voice_drift/question.j2")
        assert [o.id for o in q.options] == [voice_drift.DRIFT, voice_drift.IN_VOICE,
                                             voice_drift.NOT_ENOUGH]
        for o in q.options:
            assert o.description == prompts.render("voice_drift/option.j2", verdict=o.id)
            assert o.description.strip(), o.id
            assert o.aliases == voice_drift.ALIASES.get(o.id, ())
        # UNKNOWN is the failed check, never something the judge may answer.
        assert voice_drift.UNKNOWN not in {o.id for o in q.options}
    assert len({o.description for o in q.options}) == 3
    assert voice_drift.explain() == prompts.render("voice_drift/explain.j2")
    assert "corrective" in voice_drift.explain()


def test_the_two_synonyms_still_mean_not_enough():
    """Today's parser maps "insufficient" and "unclear" to `not_enough`; they
    survive as the option's aliases (ruling 6), so the conservative outcome
    is still reached by the words a judge reasonably uses for it."""
    assert voice_drift.ALIASES == {voice_drift.NOT_ENOUGH: ("insufficient", "unclear")}
    item = voice_drift.build_item("Mara", "Clipped.", "Mara: Fine.")
    for word in ("insufficient", "unclear", "  Unclear ", "not enough", "not-enough"):
        assert voice_drift.finding_of(_parse(_reply(word), item)) == {
            "verdict": voice_drift.NOT_ENOUGH, "note": "", "native": False}, word
    # An alias may never stand for a second option once normalised: `validate`
    # refuses the item before anything is sent.
    (q,) = item.questions
    clash = decisions.Choice(q.id, q.instructions, (
        *q.options[:2],
        decisions.Option(voice_drift.NOT_ENOUGH, "too little", ("insufficient", "In Voice"))))
    with pytest.raises(decisions.DecideRequestError):
        decisions.validate([decisions.Item(item.context, (clash,))])


def test_an_unreadable_answer_is_unknown_not_in_voice():
    """`None` -- for whatever reason -- is `UNKNOWN`, today's failed check,
    never `in_voice`: a no-drift verdict with a flag standing proposes a
    default-approved CLEAR, so garbage must not read as "they sounded fine"."""
    item = voice_drift.build_item("Mara", "Clipped.", "Mara: Fine.")
    for reply in ("I'm sorry, I can't do that.", _reply(None), _reply("maybe?"),
                  _reply(True), _reply("none"), _reply("ok"), _reply("unknown"),
                  '{"0": {"answers": {}, "rationale": "no verdict"}}'):
        finding = voice_drift.finding_of(_parse(reply, item))
        assert finding["verdict"] == voice_drift.UNKNOWN, reply
        assert voice_drift.check_failure(finding) == "unreadable verdict from the voice judge"
    # A reason other than unreadable is no answer either.
    for reason in decisions.REASONS:
        result = decisions.ItemResult({"verdict": decisions.Answer(None, reason)})
        assert voice_drift.finding_of(result) == {"verdict": voice_drift.UNKNOWN, "note": "",
                                                  "native": False}
    # A readable verdict carries its rationale as the note.
    assert voice_drift.finding_of(_parse(_reply("drift", " She used contractions. "), item)) == {
        "verdict": voice_drift.DRIFT, "note": "She used contractions.", "native": False}


def test_finding_of_says_which_backend_answered():
    """A native answer has no rationale, so `native` is what lets the route
    tell a drift with no corrective from a drift the judge forgot to explain."""
    native = decisions.ItemResult({"verdict": decisions.Answer("drift")},
                                  backend=decisions.NATIVE_BACKEND)
    assert voice_drift.finding_of(native) == {"verdict": voice_drift.DRIFT, "note": "",
                                              "native": True}
    structured = decisions.ItemResult({"verdict": decisions.Answer("drift")},
                                      "She hedged.", backend=decisions.STRUCTURED_BACKEND)
    assert voice_drift.finding_of(structured)["native"] is False


def test_check_failure_accepts_a_native_drift_without_a_note():
    """A native backend gives a verdict and never a rationale: a drift with
    no note is what it always sends, and is shown rather than failed."""
    finding = {"verdict": voice_drift.DRIFT, "note": "", "native": True}
    assert voice_drift.check_failure(finding) is None


def test_structured_drift_without_a_note_still_fails():
    for finding in ({"verdict": voice_drift.DRIFT, "note": "", "native": False},
                    {"verdict": voice_drift.DRIFT, "note": ""}):
        assert voice_drift.check_failure(finding) == "drift reported with no corrective"


def test_a_native_note_over_the_cap_still_fails():
    long_note = "She hedged. " * 100
    assert voice_drift.check_failure(
        {"verdict": voice_drift.DRIFT, "note": long_note, "native": True}
    ).startswith("the voice judge returned a corrective over")
    assert voice_drift.check_failure(
        {"verdict": voice_drift.UNKNOWN, "note": "", "native": True}
    ) == "unreadable verdict from the voice judge"


def _route_source():
    import ast
    import inspect
    import textwrap

    from grimoire.routes import scenes

    # With `_voice_item`, the helper the route hands to a worker thread so the
    # item renders off the loop: what it calls, the route calls.
    return ast.parse("\n".join(textwrap.dedent(inspect.getsource(fn))
                               for fn in (scenes._stage_voice_drift, scenes._voice_item)))


def _route_reasons() -> set[str]:
    """Every reason string `_stage_voice_drift` appends to `failed` as a
    literal or an f-string over `store` alone, evaluated."""
    import ast

    from grimoire import store

    found = set()
    for node in ast.walk(_route_source()):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if not (isinstance(key, ast.Constant) and key.value == "reason"):
                continue
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                found.add(value.value)
            elif isinstance(value, ast.JoinedStr) and {
                    n.id for n in ast.walk(value) if isinstance(n, ast.Name)} <= {"store"}:
                found.add(eval(compile(ast.Expression(value), "<route>", "eval"),
                               {"store": store}))
    return found


def _route_calls() -> set[str]:
    """The dotted names `_stage_voice_drift` calls."""
    import ast

    return {ast.unparse(node.func) for node in ast.walk(_route_source())
            if isinstance(node, ast.Call)}


def test_check_failure_reasons_match_the_route():
    """The three per-finding failures `_stage_voice_drift` reports are
    `check_failure`'s: the route calls it rather than keeping the checks
    inline, so no reason string of its own survives there -- same order, same
    checks, same words, in one place."""
    unreadable = "unreadable verdict from the voice judge"
    no_note = "drift reported with no corrective"
    too_long = (f"the voice judge returned a corrective over {voice_drift.MAX_NOTE} "
                f"characters, too long to put in front of every following turn")
    assert "store.voice_drift.check_failure" in _route_calls()
    assert not {unreadable, no_note, too_long} & _route_reasons()

    long_note = "She hedged. " * 100
    assert len(long_note.strip()) > voice_drift.MAX_NOTE
    cases = [
        ({"verdict": voice_drift.UNKNOWN, "note": ""}, unreadable),
        ({"verdict": voice_drift.UNKNOWN, "note": "junk"}, unreadable),
        ({"verdict": voice_drift.DRIFT, "note": ""}, no_note),
        ({"verdict": voice_drift.DRIFT, "note": long_note}, too_long),
        ({"verdict": voice_drift.DRIFT, "note": "x" * voice_drift.MAX_NOTE}, None),
        ({"verdict": voice_drift.DRIFT, "note": "She used contractions."}, None),
        # Both note checks are drift-only: no other verdict stores a note.
        ({"verdict": voice_drift.IN_VOICE, "note": ""}, None),
        ({"verdict": voice_drift.IN_VOICE, "note": long_note}, None),
        ({"verdict": voice_drift.NOT_ENOUGH, "note": long_note}, None),
    ]
    for finding, want in cases:
        assert voice_drift.check_failure(finding) == want, finding


# ---- the gathering the absorb phase does per NPC, as store helpers ----

def test_the_route_gathers_each_npc_through_the_store_helpers():
    """`_stage_voice_drift` builds what it sends from the same helpers the
    eval case does (`evals/cases.py`), so the two cannot drift apart: the
    locked name, the item over the effective anchor and the correction still
    in force (`judge_item`, through `live_correction`), and the answer mapped
    back by `finding_of`. None of that gathering is restated inline."""
    calls = _route_calls()
    assert {"store.voice_drift.locked_name", "store.voice_drift.read_record",
            "store.voice_drift.judge_item", "store.voice_drift.explain",
            "store.voice_drift.finding_of", "operations.decide"} <= calls
    assert not {"store.voice_drift.build_item", "store.voice_drift.live_correction",
                "store.voice_drift.fingerprint_matches", "store.voice_anchors.effective",
                "store.characters.read_card", "client.complete"} & calls


def test_live_correction_keeps_only_a_note_still_in_force():
    """`context/cast.py`'s test, the judge's side: a blank provenance predates
    the field and counts; a note fingerprinted to a replaced anchor does not."""
    record = {"text": "Clipped.\nNever uses contractions.", "id": "nonce1"}
    current = voice_drift.anchor_fingerprint(record["text"], record["id"])
    legacy = voice_drift._digest(record["text"].strip(), record["id"])
    stale = voice_drift.anchor_fingerprint("Warm and rambling.", "nonce1")
    for stored, want in (("", "She hedged."), (current, "She hedged."),
                         (legacy, "She hedged."), (stale, "")):
        assert voice_drift.live_correction({"note": "She hedged.", "anchor": stored},
                                           record) == want, stored
    assert voice_drift.live_correction({"note": "", "anchor": ""}, record) == ""


def test_judge_item_sends_the_effective_anchor_and_the_live_correction(monkeypatch):
    record = {"text": "Clipped.", "id": "nonce1"}
    flag = {"note": "Keep it short.",
            "anchor": voice_drift.anchor_fingerprint("Clipped.", "nonce1")}
    monkeypatch.setattr(voice_drift.voice_anchors, "effective", lambda text: f"<{text}>")
    item = voice_drift.judge_item("Mara", record, "Mara: Fine.", flag)
    assert item == voice_drift.build_item("Mara", "<Clipped.>", "Mara: Fine.",
                                          correction="Keep it short.")
    stale = {**flag, "anchor": voice_drift.anchor_fingerprint("Warm.", "nonce1")}
    assert voice_drift.judge_item("Mara", record, "Mara: Fine.", stale) == \
        voice_drift.build_item("Mara", "<Clipped.>", "Mara: Fine.")


def test_locked_name_reads_the_locked_cards_raw_name(monkeypatch, tmp_path):
    from grimoire.store import appearances, campaigns, scenes

    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    wroot = worlds.world_root(wid)
    sera, _ = characters.create_character(wroot, "Seraphine Vale", "default",
                                          characters.blank_card("Seraphine Vale"))
    cid = campaigns.create_campaign("Saltmarch Nights", wid)
    sid = scenes.create_scene(cid, "The Night Dock")
    appearances.appear(cid, sid, "characters", sera, "default", "npc")
    assert voice_drift.locked_name(cid, sera) == "Seraphine Vale"
    with pytest.raises(LookupError):
        voice_drift.locked_name(cid, "mara")      # never appeared here
    # Raw, never checked or substituted: the caller decides what to do with it.
    monkeypatch.setattr(voice_drift.characters, "read_card",
                        lambda root, cid_, vid: {"data": {"name": 42}})
    assert voice_drift.locked_name(cid, sera) == 42
    monkeypatch.setattr(voice_drift.characters, "read_card",
                        lambda root, cid_, vid: {"data": ["not", "an", "object"]})
    assert voice_drift.locked_name(cid, sera) is None
