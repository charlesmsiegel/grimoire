"""Per-model rate overrides (#158): the table, and what it prices.

The property under test throughout is the one thing this feature must not do —
turn "nobody priced this call" into "$0.00". Every path that cannot produce a
real figure has to answer None, and the tests below are mostly that same
question asked of each way a table or a row can be incomplete.
"""

from __future__ import annotations

import json

import pytest

from grimoire.store import pricing


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


def _write(home, table):
    (home / "pricing.json").write_text(json.dumps(table), encoding="utf-8")


# ---- the table ----
def test_no_file_is_an_empty_table_not_a_failure(home):
    assert pricing.read_pricing() == {}


def test_a_table_round_trips_through_write_and_read(home):
    stored = pricing.write_pricing(
        {"realm/opus": {"prompt_usd_per_1k": 0.003, "completion_usd_per_1k": 0.015}})

    assert stored == {"realm/opus": {"prompt_usd_per_1k": 0.003,
                                     "completion_usd_per_1k": 0.015}}
    assert pricing.read_pricing() == stored


def test_a_rate_typed_as_a_string_is_still_a_rate(home):
    """`pricing.json` is hand-editable, and `"0.002"` is what a hand types."""
    _write(home, {"realm/opus": {"prompt_usd_per_1k": "0.002",
                                 "completion_usd_per_1k": 1}})

    assert pricing.read_pricing()["realm/opus"]["prompt_usd_per_1k"] == 0.002


@pytest.mark.parametrize("bad", [-1, "twelve", float("inf"), float("nan"), None, True])
def test_an_unusable_cache_rate_is_dropped_rather_than_read_as_zero(home, bad):
    """The cache pair is the optional half, so a bad one costs itself and
    leaves the entry standing — priced at the prompt rate, which is what a
    table naming no cache rate says."""
    _write(home, {"realm/opus": {"prompt_usd_per_1k": 0.003,
                                 "completion_usd_per_1k": 0.01,
                                 "cache_read_usd_per_1k": bad}})

    entry = pricing.read_pricing()["realm/opus"]
    assert "cache_read_usd_per_1k" not in entry
    assert entry["prompt_usd_per_1k"] == 0.003


@pytest.mark.parametrize("bad", [-1, "twelve", float("inf"), float("nan"), None, True])
def test_an_unusable_base_rate_drops_the_whole_entry(home, bad):
    """A base rate that will not parse leaves an entry that can only price half
    a call, and half a call priced is a figure that is confidently wrong."""
    _write(home, {"realm/opus": {"prompt_usd_per_1k": bad,
                                 "completion_usd_per_1k": 0.01}})

    assert pricing.read_pricing() == {}


def test_an_entry_missing_a_base_rate_is_dropped_entirely(home):
    """Kept partial it would shadow the default for exactly the model somebody
    was trying to price, and value that model's prompt tokens at nothing."""
    _write(home, {"realm/opus": {"prompt_usd_per_1k": 0.002},
                  "": {"prompt_usd_per_1k": 0.001, "completion_usd_per_1k": 0.002}})

    assert pricing.read_pricing() == {
        "": {"prompt_usd_per_1k": 0.001, "completion_usd_per_1k": 0.002}}


def test_a_completion_only_entry_cannot_price_a_call_at_zero(home):
    """The failure this rule exists for: prompt tokens valued at nothing, the
    call marked priced, and a reply that generated nothing rendering `$0.00`
    for a call the provider never priced at all."""
    assert pricing.estimate({"completion_usd_per_1k": 0.01},
                            prompt_tokens=5000, completion_tokens=0) is None
    assert pricing.estimate({"prompt_usd_per_1k": 0.01},
                            prompt_tokens=5000, completion_tokens=100) is None


def test_a_broken_file_costs_the_estimates_not_the_report(home):
    (home / "pricing.json").write_text("{not json,", encoding="utf-8")

    assert pricing.read_pricing() == {}


def test_a_table_that_is_not_an_object_reads_as_no_table(home):
    _write(home, ["realm/opus", 0.003])

    assert pricing.read_pricing() == {}


def test_writing_a_non_mapping_is_refused(home):
    with pytest.raises(ValueError):
        pricing.write_pricing([{"prompt_usd_per_1k": 1, "completion_usd_per_1k": 1}])


def test_writing_more_entries_than_the_cap_is_refused_not_truncated(home):
    """Truncating would silently discard rates somebody typed and would never
    see again."""
    too_many = {f"m{n}": {"prompt_usd_per_1k": 0.001, "completion_usd_per_1k": 0.002}
                for n in range(pricing.MAX_ENTRIES + 1)}

    with pytest.raises(ValueError):
        pricing.write_pricing(too_many)
    assert not (home / "pricing.json").exists()


# ---- which entry prices a model ----
def test_an_exact_model_id_wins_over_a_wildcard_and_the_default():
    table = {"realm/opus": {"prompt_usd_per_1k": 1.0, "completion_usd_per_1k": 1.0},
             "realm/*": {"prompt_usd_per_1k": 2.0, "completion_usd_per_1k": 2.0},
             "": {"prompt_usd_per_1k": 3.0, "completion_usd_per_1k": 3.0}}

    assert pricing.rate_for(table, "realm/opus")["prompt_usd_per_1k"] == 1.0


def test_the_longest_matching_wildcard_wins():
    """A table naturally holds both a family and a narrower branch of it, and
    the narrower one is the one that was typed second on purpose."""
    table = {"realm/*": {"prompt_usd_per_1k": 2.0, "completion_usd_per_1k": 2.0},
             "realm/opus-*": {"prompt_usd_per_1k": 4.0, "completion_usd_per_1k": 4.0},
             "": {"prompt_usd_per_1k": 3.0, "completion_usd_per_1k": 3.0}}

    assert pricing.rate_for(table, "realm/opus-4")["prompt_usd_per_1k"] == 4.0
    assert pricing.rate_for(table, "realm/haiku")["prompt_usd_per_1k"] == 2.0


def test_a_model_nothing_names_falls_through_to_the_default():
    table = {"realm/*": {"prompt_usd_per_1k": 2.0, "completion_usd_per_1k": 2.0}, "": {"prompt_usd_per_1k": 3.0, "completion_usd_per_1k": 3.0}}

    assert pricing.rate_for(table, "other/thing")["prompt_usd_per_1k"] == 3.0


def test_a_model_nothing_names_with_no_default_is_priced_by_nothing():
    assert pricing.rate_for({"realm/*": {"prompt_usd_per_1k": 2.0, "completion_usd_per_1k": 2.0}}, "other/x") is None


def test_a_row_with_no_model_at_all_can_only_match_the_default():
    """`store.usage` labels a model-less row "unknown"; nobody knows what
    answered, so only a rate claiming to cover everything may price it."""
    table = {"realm/*": {"prompt_usd_per_1k": 2.0, "completion_usd_per_1k": 2.0}, "": {"prompt_usd_per_1k": 3.0, "completion_usd_per_1k": 3.0}}

    assert pricing.rate_for(table, "")["prompt_usd_per_1k"] == 3.0
    assert pricing.rate_for(table, None)["prompt_usd_per_1k"] == 3.0


# ---- what a call would have cost ----
def test_prompt_and_completion_are_priced_at_their_own_rates():
    entry = {"prompt_usd_per_1k": 0.003, "completion_usd_per_1k": 0.015}

    usd = pricing.estimate(entry, prompt_tokens=1000, completion_tokens=2000)

    assert usd == pytest.approx(0.003 + 0.030)


def test_a_cache_rate_moves_those_tokens_out_of_the_prompt_subtotal():
    """The cache counts are slices OF the prompt (#148). Priced beside it
    rather than out of it, a cached prefix would be billed twice."""
    entry = {"prompt_usd_per_1k": 1.0, "completion_usd_per_1k": 0.0,
             "cache_read_usd_per_1k": 0.1}

    usd = pricing.estimate(entry, prompt_tokens=1000, completion_tokens=0,
                           cache_read_tokens=800)

    assert usd == pytest.approx((200 * 1.0 + 800 * 0.1) / 1000)


def test_with_no_cache_rate_cached_tokens_stay_at_the_prompt_rate():
    """A table naming no cache rate is saying the provider does not discount
    them — not that they are free."""
    entry = {"prompt_usd_per_1k": 1.0, "completion_usd_per_1k": 0.0}

    usd = pricing.estimate(entry, prompt_tokens=1000, completion_tokens=0,
                           cache_read_tokens=800)

    assert usd == pytest.approx(1.0)


def test_cache_counts_larger_than_the_prompt_cannot_drive_the_estimate_down():
    """A hand-edited row (or a double-reporting provider) must not make the
    prompt subtotal negative and subtract from the bill."""
    entry = {"prompt_usd_per_1k": 1.0, "completion_usd_per_1k": 0.0,
             "cache_read_usd_per_1k": 0.1, "cache_write_usd_per_1k": 0.2}

    usd = pricing.estimate(entry, prompt_tokens=100, completion_tokens=0,
                           cache_read_tokens=9999, cache_write_tokens=9999)

    assert usd == pytest.approx(100 * 0.1 / 1000)


def test_a_call_nobody_counted_is_not_estimated_at_zero():
    """The case this whole feature must not get wrong: an endpoint that sends
    no usage block at all is exactly the one a rate cannot rescue."""
    entry = {"prompt_usd_per_1k": 0.003, "completion_usd_per_1k": 0.015}

    assert pricing.estimate(entry, prompt_tokens=None, completion_tokens=None) is None


def test_a_call_that_genuinely_completed_nothing_is_still_priced():
    """Zero is a count. Only an ABSENT count means nobody measured."""
    entry = {"prompt_usd_per_1k": 1.0, "completion_usd_per_1k": 1.0}

    assert pricing.estimate(entry, prompt_tokens=500, completion_tokens=0) \
        == pytest.approx(0.5)


def test_no_entry_prices_nothing():
    assert pricing.estimate(None, prompt_tokens=100, completion_tokens=100) is None
    assert pricing.estimate({}, prompt_tokens=100, completion_tokens=100) is None


@pytest.mark.parametrize("counts", [
    {"prompt_tokens": 5000, "completion_tokens": None},
    {"prompt_tokens": None, "completion_tokens": 5000},
])
def test_half_a_call_counted_is_not_half_a_call_priced(counts):
    """`from_openai_chunk` reads the two counters independently, so a usage
    block carrying only one is a shape a real provider sends. Pricing the
    uncounted half at zero marks the call modelled and, when the counted half
    is empty, renders `$0.00` for a call nobody priced."""
    entry = {"prompt_usd_per_1k": 1.0, "completion_usd_per_1k": 1.0}

    assert pricing.estimate(entry, **counts) is None


def test_a_broken_table_is_strict_for_the_caller_that_asks(home):
    """A rollup wants a report drawn without estimates; an editor must not be
    handed an empty form it would then save over the real file."""
    (home / "pricing.json").write_text("{not json,", encoding="utf-8")

    assert pricing.read_pricing() == {}
    with pytest.raises(pricing.PricingUnreadableError):
        pricing.read_pricing(strict=True)


def test_a_table_that_is_not_an_object_is_unreadable_too(home):
    _write(home, ["realm/opus", 0.003])

    assert pricing.read_pricing() == {}
    with pytest.raises(pricing.PricingUnreadableError):
        pricing.read_pricing(strict=True)


def test_an_absent_table_is_empty_in_both_modes(home):
    """Nothing to lose in a file that does not exist — that is a form to fill
    in, not a read that failed."""
    assert pricing.read_pricing() == {}
    assert pricing.read_pricing(strict=True) == {}


def test_an_entry_that_is_not_an_object_is_dropped(home):
    _write(home, {"realm/opus": "expensive",
                  "": {"prompt_usd_per_1k": 1, "completion_usd_per_1k": 1}})

    assert list(pricing.read_pricing()) == [""]


def test_a_non_string_key_is_skipped_on_read_and_refused_on_write(home):
    """JSON object keys are strings, so this is a `write_pricing` caller
    handing in a dict Python built — refused there, and skipped on the way in
    rather than failing the read a rollup depends on."""
    (home / "pricing.json").write_text(
        '{"1": {"prompt_usd_per_1k": 1, "completion_usd_per_1k": 1}}', encoding="utf-8")
    assert list(pricing.read_pricing()) == ["1"]

    with pytest.raises(ValueError):
        pricing.write_pricing({7: {"prompt_usd_per_1k": 1, "completion_usd_per_1k": 1}})


def test_a_table_over_the_cap_on_disk_is_read_up_to_it(home):
    """Refused on the way IN (nothing is lost), truncated on the way OUT of a
    file somebody grew by hand — a rollup must still draw."""
    _write(home, {f"m{n}": {"prompt_usd_per_1k": 0.001, "completion_usd_per_1k": 0.002}
                  for n in range(pricing.MAX_ENTRIES + 5)})

    assert len(pricing.read_pricing()) == pricing.MAX_ENTRIES


def test_a_rate_that_overflows_a_real_token_count_answers_nothing(home):
    """`1e308` is finite and the table accepts it, but times a few thousand
    tokens it is `inf` — a value `json.dumps` cannot write, so one absurd rate
    would 500 every cost endpoint until the file was edited by hand."""
    entry = {"prompt_usd_per_1k": 1e308, "completion_usd_per_1k": 1e308}

    assert pricing.estimate(entry, prompt_tokens=5000, completion_tokens=5000) is None
    # A sane rate beside it still works — the guard is on the result, not the
    # magnitude of the input.
    assert pricing.estimate({"prompt_usd_per_1k": 1.0, "completion_usd_per_1k": 1.0},
                            prompt_tokens=5000, completion_tokens=5000) is not None


def test_a_strict_read_refuses_a_table_it_had_to_shorten(home):
    """The editor saves by whole-table replacement, so a form filled in from a
    shortened read deletes what did not survive the read — permanently, under a
    successful save. One hand-edited rate with a missing half triggers it."""
    _write(home, {"realm/opus": {"prompt_usd_per_1k": 0.003},          # half an entry
                  "": {"prompt_usd_per_1k": 0.001, "completion_usd_per_1k": 0.002}})

    assert list(pricing.read_pricing()) == [""], "a rollup still draws"
    with pytest.raises(pricing.PricingUnreadableError):
        pricing.read_pricing(strict=True)


def test_a_strict_read_refuses_a_table_past_the_cap(home):
    _write(home, {f"m{n}": {"prompt_usd_per_1k": 0.001, "completion_usd_per_1k": 0.002}
                  for n in range(pricing.MAX_ENTRIES + 5)})

    assert len(pricing.read_pricing()) == pricing.MAX_ENTRIES
    with pytest.raises(pricing.PricingUnreadableError):
        pricing.read_pricing(strict=True)


def test_a_table_that_survives_whole_is_not_reported_lossy(home):
    _write(home, {"realm/opus": {"prompt_usd_per_1k": 0.003,
                                 "completion_usd_per_1k": 0.015}})

    assert pricing.read_pricing(strict=True) == {
        "realm/opus": {"prompt_usd_per_1k": 0.003, "completion_usd_per_1k": 0.015}}
