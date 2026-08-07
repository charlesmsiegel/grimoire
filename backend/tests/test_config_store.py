import importlib

import grimoire.store as store


def reload_with_home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    return store


def test_first_read_creates_defaults(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cfg = s.read_config()
    assert cfg["theme"] == "codex"
    assert (tmp_path / "config.md").exists()


def test_context_scan_depth_default_and_write(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    assert s.read_config()["context_scan_depth"] == "8"
    s.write_config(context_scan_depth="5")
    assert s.read_config()["context_scan_depth"] == "5"


def test_write_merges_without_clearing(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    s.write_config(user_label="Kestrel")
    s.write_config(theme="manuscript")  # must not wipe the label
    cfg = s.read_config()
    assert cfg["user_label"] == "Kestrel"
    assert cfg["theme"] == "manuscript"


def test_recap_depth_default_and_write(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    assert s.read_config()["recap_depth"] == "5"
    s.write_config(recap_depth="3")
    assert s.read_config()["recap_depth"] == "3"


def test_label_defaults_and_write(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cfg = s.read_config()
    assert cfg["user_label"] == "You"
    assert cfg["assistant_label"] == "Grimoire"
    cfg = s.write_config(user_label="Kestrel", assistant_label="Narrator")
    assert cfg["user_label"] == "Kestrel"
    assert s.read_config()["assistant_label"] == "Narrator"


def test_response_and_length_keys_survive_read_config(monkeypatch, tmp_path):
    """The cascade's global scope reads through read_config(), which narrows its
    return to _CONFIG_KEYS — so a key missing from that tuple is silently
    dropped and the whole global scope goes dead with no error."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    store.write_config(response_preset="terse", length_reply_words="120",
                        length_blocks="2", length_paragraphs="1",
                        length_speakers="2", length_blocks_per_speaker="1")
    cfg = store.read_config()
    assert cfg["response_preset"] == "terse"
    assert cfg["length_reply_words"] == "120"
    assert cfg["length_blocks_per_speaker"] == "1"


def test_length_keys_default_to_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cfg = store.read_config()
    assert cfg["response_preset"] == ""
    assert cfg["length_reply_words"] == ""


# ---- LLM duration settings (#243) ----

def test_duration_defaults_and_write(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cfg = s.read_config()
    assert cfg["llm_timeout"] == "120"
    assert cfg["absorb_budget"] == "600"
    assert cfg["llm_call_budget"] == "300"
    s.write_config(llm_timeout="45", absorb_budget="300", llm_call_budget="90")
    assert s.config.llm_timeout() == 45.0
    assert s.config.absorb_budget() == 300.0
    assert s.config.llm_call_budget() == 90.0


def test_durations_fall_back_when_unparseable(monkeypatch, tmp_path):
    """A hand-edited config.md must not take scene generation down with it."""
    s = reload_with_home(monkeypatch, tmp_path)
    s.write_config(llm_timeout="soon", absorb_budget="", llm_call_budget="a while")
    assert s.config.llm_timeout() == 120.0
    assert s.config.absorb_budget() == 600.0
    assert s.config.llm_call_budget() == 300.0


def test_non_finite_durations_fall_back_to_the_default(monkeypatch, tmp_path):
    """float() happily parses "inf"/"nan": inf is an unbounded call that never
    says so, and nan compares false against everything, silently reading as
    "disabled" instead of as the malformed value it is."""
    s = reload_with_home(monkeypatch, tmp_path)
    s.write_config(llm_timeout="inf", absorb_budget="nan", llm_call_budget="inf")
    assert s.config.llm_timeout() == 120.0
    assert s.config.absorb_budget() == 600.0
    assert s.config.llm_call_budget() == 300.0


def test_non_positive_duration_means_no_bound(monkeypatch, tmp_path):
    """The escape hatch for a slow local endpoint: 0 (or anything negative,
    however it got there) disables the bound rather than expiring instantly."""
    s = reload_with_home(monkeypatch, tmp_path)
    s.write_config(llm_timeout="0", absorb_budget="-1", llm_call_budget="0")
    assert s.config.llm_timeout() == 0.0
    assert s.config.absorb_budget() == 0.0
    assert s.config.llm_call_budget() == 0.0


# ---- transient state settings (#120 / #121) ----

def test_transient_state_ships_disabled(monkeypatch, tmp_path):
    """The tracker adds an instruction to every reply, so it is opt-in — an
    existing install must not start being told to emit machine-readable blocks
    because it upgraded."""
    s = reload_with_home(monkeypatch, tmp_path)
    cfg = s.read_config()
    assert cfg["turnstate_depth"] == "0"
    assert cfg["promote_streak"] == "3"
    assert s.config.turnstate_depth() == 0


def test_transient_counts_round_trip(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    s.write_config(turnstate_depth="6", promote_streak="2")
    assert s.config.turnstate_depth() == 6
    assert s.config.promote_streak() == 2


def test_transient_counts_fall_back_when_unparseable(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    s.write_config(turnstate_depth="lots", promote_streak="")
    assert s.config.turnstate_depth() == 0
    assert s.config.promote_streak() == 3


def test_a_negative_count_reads_as_disabled(monkeypatch, tmp_path):
    """Not as an index — history[-N:] with a negative N slices from the wrong
    end, and a negative streak would promote on the first value seen."""
    s = reload_with_home(monkeypatch, tmp_path)
    s.write_config(turnstate_depth="-4", promote_streak="-1")
    assert s.config.turnstate_depth() == 0
    assert s.config.promote_streak() == 0
