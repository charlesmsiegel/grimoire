import importlib

import grimoire.store as store


def reload_with_home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    return store


def test_first_read_creates_defaults(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cfg = s.read_config()
    assert cfg["theme"] == "system"
    assert (tmp_path / "config.md").exists()


def test_context_scan_depth_default_and_write(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    assert s.read_config()["context_scan_depth"] == "8"
    s.write_config(context_scan_depth="5")
    assert s.read_config()["context_scan_depth"] == "5"


def test_scan_depth_separates_a_typed_zero_from_a_cleared_field(monkeypatch, tmp_path):
    """The split `_count` exists for, on a setting the UI can now write (#11):
    "0" is an instruction to empty the scan window, while an unparseable value
    -- a field cleared in the Configuration page, a hand-mangled config.md --
    is a mistake to recover from, and falls back to the default rather than
    silently turning keyword activation off on the play path.

    The accessor is under test at all because the builder used to parse this
    key inline against a hardcoded `"8"`, which left `DEFAULT_SCAN_DEPTH` the
    default of nothing -- and the new form field would have been a third copy.
    """
    s = reload_with_home(monkeypatch, tmp_path)
    assert s.config.scan_depth() == 8
    s.write_config(context_scan_depth="3")
    assert s.config.scan_depth() == 3
    s.write_config(context_scan_depth="0")      # a real choice: no scan window
    assert s.config.scan_depth() == 0
    s.write_config(context_scan_depth="-2")     # not an index from the far end
    assert s.config.scan_depth() == 0
    s.write_config(context_scan_depth="abc")    # a cleared field falls back
    assert s.config.scan_depth() == 8


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


# ---- retry + fallback settings (#144) ----

def test_retry_defaults(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cfg = s.read_config()
    assert cfg["llm_retries"] == "2"
    assert cfg["fallback_connection_id"] == ""   # no fallback until one is picked
    assert s.config.llm_retries() == 2


def test_zero_retries_is_the_pre_144_behaviour(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    s.write_config(llm_retries="0")
    assert s.config.llm_retries() == 0


def test_an_unparseable_retry_count_falls_back_to_the_default(monkeypatch, tmp_path):
    """Same posture as every other knob: a hand-edited config.md or a field
    cleared in the UI must not take generation down."""
    s = reload_with_home(monkeypatch, tmp_path)
    s.write_config(llm_retries="lots")
    assert s.config.llm_retries() == 2


def test_a_negative_retry_count_reads_as_none(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    s.write_config(llm_retries="-3")
    assert s.config.llm_retries() == 0


def test_the_retry_count_is_clamped(monkeypatch, tmp_path):
    """Retries are cheap one at a time and expensive in a row, and a streamed
    turn has no total-duration ceiling above them — so a hand-typed 500 must not
    be able to leave a scene apparently hung for an hour."""
    s = reload_with_home(monkeypatch, tmp_path)
    s.write_config(llm_retries="500")
    assert s.config.llm_retries() == s.config.MAX_LLM_RETRIES


def test_the_fallback_connection_round_trips(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    s.write_config(fallback_connection_id="backup")
    assert s.read_config()["fallback_connection_id"] == "backup"



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


def test_the_replay_fork_threshold_round_trips(monkeypatch, tmp_path):
    """The retcon replay's nudge (#80) is configuration rather than a constant:
    what counts as "many turns" depends on what the reader's model charges."""
    s = reload_with_home(monkeypatch, tmp_path)
    assert s.config.replay_fork_threshold() == 10
    s.write_config(replay_fork_threshold="3")
    assert s.config.replay_fork_threshold() == 3


def test_an_unparseable_replay_threshold_falls_back_to_the_default(monkeypatch, tmp_path):
    """A cleared field must not silently turn the guard off — that is the one
    direction where the mistake is expensive."""
    s = reload_with_home(monkeypatch, tmp_path)
    s.write_config(replay_fork_threshold="")
    assert s.config.replay_fork_threshold() == 10


def test_the_advance_fork_threshold_round_trips(monkeypatch, tmp_path):
    """The clock's checkpoint nudge (#107) is configuration for the same reason
    the replay's is: what counts as "a large time skip" is a judgement about
    this campaign's pace, and a story told in seasons and one told in hours do
    not agree about thirty days."""
    s = reload_with_home(monkeypatch, tmp_path)
    assert s.config.advance_fork_threshold() == 30
    s.write_config(advance_fork_threshold="7")
    assert s.config.advance_fork_threshold() == 7


def test_an_unparseable_advance_threshold_falls_back_to_the_default(monkeypatch, tmp_path):
    """Cleared or mangled, the nudge stays on — the same direction the replay
    threshold errs in, and for the same reason."""
    s = reload_with_home(monkeypatch, tmp_path)
    s.write_config(advance_fork_threshold="")
    assert s.config.advance_fork_threshold() == 30


def test_a_negative_advance_threshold_reads_as_zero(monkeypatch, tmp_path):
    """`_count`'s clamp, pinned here because `clock.digest` leans on it: with
    the threshold at 0 the nudge is `hi - lo > 0`, which is what makes a move
    crossing no days still not a skip. A threshold that could go negative would
    make every same-day nudge ask about a checkpoint."""
    s = reload_with_home(monkeypatch, tmp_path)
    s.write_config(advance_fork_threshold="-5")
    assert s.config.advance_fork_threshold() == 0
