"""The rolling per-scene summary's store half (#85): the config knob, the
frontmatter accessors, and the prompt/parse pair.

The route half lives in `test_rolling_summary_routes.py`.
"""

import importlib

import pytest

import grimoire.store as store
from grimoire.store import campaigns, frontmatter, rolling_summary, scenes, worlds
from grimoire.store.scenes import paths as scenes_paths


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Run", wid)


# ---- config knob ----
def test_rolling_summary_every_default_and_write(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    assert store.read_config()["rolling_summary_every"] == "10"
    assert store.config.rolling_summary_every() == 10
    store.write_config(rolling_summary_every="4")
    assert store.config.rolling_summary_every() == 4


@pytest.mark.parametrize("raw", ["", "off", "3.5", "-2", "nan", "1e3000"])
def test_a_malformed_knob_never_raises_into_the_turn_loop(monkeypatch, tmp_path, raw):
    """A hand-edited config.md or a cleared field must degrade to a number, not
    take a scene down. Anything unparseable falls back to the default; anything
    negative means the same thing as 0 — off."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    store.write_config(rolling_summary_every=raw)
    n = store.config.rolling_summary_every()
    assert isinstance(n, int) and n >= 0


def test_zero_is_off_and_survives_the_round_trip(monkeypatch, tmp_path):
    """0 has to be distinguishable from unset — it is the documented way to
    disable the feature, so it must not fall back to the default of 10."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    store.write_config(rolling_summary_every="0")
    assert store.config.rolling_summary_every() == 0


# ---- frontmatter accessors ----
def test_unset_scene_reports_an_empty_summary(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Landing")
    assert scenes.get_rolling_summary(cid, sid) == {"summary": "", "at": 0, "digest": ""}


def test_missing_scene_reports_empty_rather_than_raising(monkeypatch, tmp_path):
    """Same shape every other frontmatter reader in `scenes.read` uses: a scene
    that is not there has no rolling summary, which is not an error."""
    cid = _campaign(monkeypatch, tmp_path)
    assert scenes.get_rolling_summary(cid, "nope")["summary"] == ""


def test_set_then_get_round_trips(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Landing")
    scenes.set_rolling_summary(cid, sid, "Mara reaches the salt gate.", 4, "abc123")
    assert scenes.get_rolling_summary(cid, sid) == {
        "summary": "Mara reaches the salt gate.", "at": 4, "digest": "abc123"}


def test_a_multiline_summary_cannot_corrupt_the_frontmatter(monkeypatch, tmp_path):
    """`store/frontmatter.py` is one line per key and quotes without escaping
    newlines, so a multi-line value writes a second physical line that parses
    back as a junk key, a dropped line, or — beginning `---` — an early end of
    the block. The store collapses to one line so that cannot happen whatever
    the model said."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Landing")
    scenes.append_message(cid, sid, "user", "Body text must survive.")
    scenes.set_rolling_summary(
        cid, sid, "First paragraph.\n---\nkey: injected\n\nSecond paragraph.", 1, "d")

    raw = scenes_paths._scene_path(cid, sid).read_text(encoding="utf-8")
    meta, body = frontmatter.parse_frontmatter(raw)
    assert "injected" not in meta
    assert meta["rolling_summary"] == "First paragraph. --- key: injected Second paragraph."
    assert "Body text must survive." in body
    # and the accessors still agree with each other after the round trip
    assert scenes.get_rolling_summary(cid, sid)["at"] == 1


def test_writing_a_summary_leaves_the_transcript_alone(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Landing")
    scenes.append_message(cid, sid, "user", "Where is the ledger?")
    scenes.append_message(cid, sid, "assistant", "Gone with the tide.")
    before = scenes.read_scene(cid, sid)["messages"]
    scenes.set_rolling_summary(cid, sid, "A ledger goes missing.", 2, "x")
    assert scenes.read_scene(cid, sid)["messages"] == before


def test_a_corrupt_rolling_at_reads_as_zero(monkeypatch, tmp_path):
    """Frontmatter is hand-editable. A non-numeric `rolling_at` must mean "we
    have covered nothing", not an exception on the read path."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Landing")
    p = scenes_paths._scene_path(cid, sid)
    meta, body = frontmatter.parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["rolling_summary"] = "Something happened."
    meta["rolling_at"] = "not-a-number"
    p.write_text(frontmatter.dump_frontmatter(meta, body), encoding="utf-8")
    assert scenes.get_rolling_summary(cid, sid)["at"] == 0


# ---- digest ----
def test_the_digest_changes_when_a_covered_message_is_rewritten():
    """The whole point of the digest: a reroll or an edit can leave the
    transcript exactly as long as it was and change what it says. Length cannot
    see that; this must."""
    before = [{"role": "user", "content": "Where is the ledger?"},
              {"role": "assistant", "content": "Gone with the tide."}]
    after = [{"role": "user", "content": "Where is the ledger?"},
             {"role": "assistant", "content": "Burned, and she watched."}]
    assert len(before) == len(after)
    assert rolling_summary.covered_digest(before) != rolling_summary.covered_digest(after)


def test_the_digest_is_stable_across_calls_and_over_the_speaker():
    a = [{"role": "assistant", "speaker": "Mara", "content": "The gate holds."}]
    b = [{"role": "assistant", "speaker": "Seraphine", "content": "The gate holds."}]
    assert rolling_summary.covered_digest(a) == rolling_summary.covered_digest(a)
    assert rolling_summary.covered_digest(a) != rolling_summary.covered_digest(b)


def test_the_empty_prefix_has_a_digest_of_its_own():
    """`at == 0` is a real covered state (nothing folded yet), so it needs a
    value that a later comparison can match rather than "" standing for both
    "no digest stored" and "digest of nothing"."""
    assert rolling_summary.covered_digest([]) != ""


# ---- prompt / parse ----
def test_build_prompt_is_a_system_user_pair():
    msgs = rolling_summary.build_prompt("", "**You:** Hello.")
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert "**You:** Hello." in msgs[1]["content"]


def test_the_prior_summary_reaches_the_prompt_when_folding():
    msgs = rolling_summary.build_prompt("Mara reached the gate.", "**You:** And then?")
    assert "Mara reached the gate." in msgs[1]["content"]


def test_parse_output_collapses_to_a_single_line():
    text = "  Mara reaches the gate.\n\nSeraphine follows her in.  \n"
    assert rolling_summary.parse_output(text) == "Mara reaches the gate. Seraphine follows her in."


def test_parse_output_of_an_empty_reply_is_empty():
    assert rolling_summary.parse_output("   \n\n  ") == ""
