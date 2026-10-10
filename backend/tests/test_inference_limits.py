"""`store.inference.limits` (spec 01i): a model's window and output cap as a
resolved fact, and the prompt ceiling every consumer derives from it.

Pure: every case builds its attempts by hand, so nothing here reads a store.
The resolver's own wiring (an attempt's limits are `limits.of` over the row
and facts it read) is in `test_inference_resolve.py`."""

from __future__ import annotations

import pytest

from grimoire import wire
from grimoire.store.inference import facts, limits
from grimoire.store.inference.resolved import Attempt, ResolvedInference

from . import wire_kit


def _limits(window: int | None = None, max_output: int | None = None) -> wire.Limits:
    def one(value):
        return wire.UNKNOWN_LIMIT if value is None else wire.Limit(value, "catalog")
    return wire.Limits(window=one(window), max_output=one(max_output))


def _attempt(provider_id: str = "saltmarch", model: str = "vendor/m", *,
             window: int | None = None, max_output: int | None = None,
             sent: dict | None = None) -> Attempt:
    target = wire_kit.target(provider_id=provider_id, model=model,
                             limits=_limits(window, max_output))
    return Attempt(provider_id, model, "", target=target,
                   controls={"effective": dict(sent or {})})


def _resolution(*attempts: Attempt, rides: bool = True) -> ResolvedInference:
    return ResolvedInference(task="chat", operation="generate", route="scene", role="primary",
                             via="role", scope="global", attempts=attempts, rides=rides)


# -- limits.of ---------------------------------------------------------------

def test_the_users_word_outranks_the_catalog_for_each_value_alone():
    row = {"context": 131072, "max_output": 16000}
    assert limits.of(row, {"context_window": 8192}) == wire.Limits(
        window=wire.Limit(8192, "user"), max_output=wire.Limit(16000, "catalog"))
    assert limits.of(row, {"max_output": 2048}) == wire.Limits(
        window=wire.Limit(131072, "catalog"), max_output=wire.Limit(2048, "user"))


def test_the_catalog_answers_when_nothing_is_stated():
    assert limits.of({"context": 200000, "max_output": 64000}, {}) == wire.Limits(
        window=wire.Limit(200000, "catalog"), max_output=wire.Limit(64000, "catalog"))


def test_no_source_is_unknown_never_zero():
    got = limits.of(None, None)
    assert got == wire.Limits() == wire.Limits(wire.UNKNOWN_LIMIT, wire.UNKNOWN_LIMIT)
    assert got.window == wire.Limit(None, "unknown")
    assert limits.of({"context": None}, {"context_window": None}) == wire.Limits()


@pytest.mark.parametrize("bad", [0, -1, True, 8192.0, "8192", 2**31, [8192], {"v": 1}])
def test_a_malformed_value_contributes_nothing(bad):
    assert limits.of({"context": bad, "max_output": bad},
                     {"context_window": bad, "max_output": bad}) == wire.Limits()
    # ...and a malformed statement falls through to the catalog's word.
    assert limits.of({"context": 4096}, {"context_window": bad}).window == wire.Limit(
        4096, "catalog")


def test_a_mangled_row_or_facts_never_raises():
    assert limits.of("not a row", ["not", "facts"]) == wire.Limits()   # type: ignore[arg-type]


def test_the_facts_view_and_the_resolver_share_one_bound():
    assert facts.STATED_MAX == limits.STATED_MAX == 2**31


# -- reply_reserve -----------------------------------------------------------

def test_a_per_call_cap_outranks_the_presets():
    a = _attempt(window=32000, sent={"max_tokens": 1000})
    assert limits.reply_reserve(a) == 1000
    assert limits.reply_reserve(a, max_tokens=8192) == 8192


def test_the_preset_is_read_under_either_wire_name():
    assert limits.reply_reserve(_attempt(window=32000, sent={"max_tokens": 700})) == 700
    # The OpenAI API is sent `max_completion_tokens` (`llm_sampling`).
    assert limits.reply_reserve(
        _attempt(window=32000, sent={"max_completion_tokens": 900})) == 900


def test_the_anthropic_api_is_always_sent_one_so_its_reserve_is_exact():
    from grimoire import llm_sampling
    target = wire_kit.target(kind="anthropic", provider_id="anthropic", model="claude-x",
                             limits=_limits(200000))
    a = Attempt("anthropic", "claude-x", "", target=target,
                controls=llm_sampling.effective(target))
    assert limits.reply_reserve(a) == llm_sampling.ANTHROPIC_MAX_TOKENS


def test_unset_uses_the_default_or_a_quarter_of_a_small_window():
    assert limits.reply_reserve(_attempt(window=131072)) == limits.DEFAULT_REPLY_RESERVE
    assert limits.reply_reserve(_attempt(window=8192)) == 2048
    # With no window known there is no quarter to take.
    assert limits.reply_reserve(_attempt()) == limits.DEFAULT_REPLY_RESERVE


def test_every_reserve_is_capped_at_a_known_max_output():
    assert limits.reply_reserve(_attempt(window=32000, max_output=500,
                                         sent={"max_tokens": 4000})) == 500
    assert limits.reply_reserve(_attempt(window=32000, max_output=500), max_tokens=900) == 500
    assert limits.reply_reserve(_attempt(window=131072, max_output=1000)) == 1000


def test_max_output_is_never_itself_the_reserve():
    """A model listing a 128k cap in a 131k window still gets a usable
    ceiling: the cap bounds what is asked for, it is not what is held back."""
    a = _attempt(window=131072, max_output=128000)
    assert limits.reply_reserve(a) == limits.DEFAULT_REPLY_RESERVE
    assert limits.prompt_ceiling(_resolution(a)).tokens == 131072 - limits.DEFAULT_REPLY_RESERVE


def test_an_attempt_built_by_hand_reads_unknown():
    a = Attempt("p", "m", "")
    assert a.limits == wire.Limits()
    assert limits.prompt_ceiling(_resolution(a)) == limits.UNKNOWN


@pytest.mark.parametrize("bad", [0, -5, True, 1.5])
def test_a_per_call_cap_must_be_a_positive_whole_number(bad):
    with pytest.raises(ValueError):
        limits.reply_reserve(_attempt(window=1000), max_tokens=bad)
    with pytest.raises(ValueError):
        limits.prompt_ceiling(_resolution(_attempt(window=1000)), max_tokens=bad)


# -- prompt_ceiling ----------------------------------------------------------

def test_the_smaller_window_binds_when_the_fallback_rides():
    primary = _attempt("saltmarch", "vendor/big", window=131072, sent={"max_tokens": 1000})
    spare = _attempt("realm", "vendor/small", window=8192, sent={"max_tokens": 1000})
    got = limits.prompt_ceiling(_resolution(primary, spare))
    assert (got.tokens, got.window, got.reserve) == (7192, 8192, 1000)
    assert got.binding == ("realm", "vendor/small")
    assert got.complete and got.reason == ""


def test_each_attempt_reserves_its_own_reply():
    """Walked attempts, not targets: each one's own controls are read."""
    primary = _attempt("saltmarch", "a", window=10000, sent={"max_tokens": 6000})
    spare = _attempt("realm", "b", window=9000, sent={"max_tokens": 1000})
    got = limits.prompt_ceiling(_resolution(primary, spare))
    assert (got.tokens, got.binding) == (4000, ("saltmarch", "a"))


def test_a_fallback_that_does_not_ride_is_never_sent_the_prompt():
    primary = _attempt("saltmarch", "a", window=131072, sent={"max_tokens": 1000})
    spare = _attempt("realm", "b", window=4096, sent={"max_tokens": 1000})
    # An incapable fallback, or a decide resolution's own stage: `rides` False.
    got = limits.prompt_ceiling(_resolution(primary, spare, rides=False))
    assert (got.tokens, got.binding, got.complete) == (130072, ("saltmarch", "a"), True)
    # An unknown window on a fallback that does not ride leaves it complete.
    got = limits.prompt_ceiling(_resolution(primary, _attempt("realm", "b"), rides=False))
    assert got.complete


def test_one_unknown_window_makes_it_incomplete():
    primary = _attempt("saltmarch", "a", sent={"max_tokens": 1000})
    spare = _attempt("realm", "b", window=8192, sent={"max_tokens": 1000})
    got = limits.prompt_ceiling(_resolution(primary, spare))
    assert (got.tokens, got.complete, got.binding) == (7192, False, ("realm", "b"))


def test_none_known_is_none_never_zero():
    got = limits.prompt_ceiling(_resolution(_attempt(), _attempt("realm", "b")))
    assert got.tokens is None and got.window is None and got.binding is None
    assert not got.complete and got.reason == ""
    assert limits.prompt_ceiling(_resolution()) == limits.UNKNOWN


def test_a_preset_reserve_past_the_window_floors_at_zero_with_a_reason():
    a = _attempt(window=8192, sent={"max_tokens": 32000})
    got = limits.prompt_ceiling(_resolution(a))
    assert got.tokens == 0 and got.reserve == 32000
    assert got.reason == ("The preset asks for 32,000 reply tokens; this model's window "
                          "is 8,192, which leaves no room for a prompt.")


def test_a_per_call_cap_is_reserved_rather_than_the_default():
    """An 8192 cap with no preset value reserves 8192, not the default."""
    a = _attempt(window=131072)
    assert limits.prompt_ceiling(_resolution(a), max_tokens=8192).tokens == 131072 - 8192
    zero = limits.prompt_ceiling(_resolution(_attempt(window=4096)), max_tokens=8192)
    assert zero.tokens == 0 and zero.reason.startswith("This call asks for 8,192")


def test_an_explicit_reserve_is_honoured_outright():
    a = _attempt(window=10000, max_output=100, sent={"max_tokens": 50})
    got = limits.prompt_ceiling(_resolution(a), reserve=3000)
    assert (got.tokens, got.reserve) == (7000, 3000)
    assert limits.prompt_ceiling(_resolution(a), reserve=0).tokens == 10000
    with pytest.raises(ValueError):
        limits.prompt_ceiling(_resolution(a), reserve=-1)


def test_the_binding_is_a_tuple_a_slashed_model_id_survives():
    a = _attempt("saltmarch", "vendor/family/model-large", window=64000)
    assert limits.prompt_ceiling(_resolution(a)).binding == (
        "saltmarch", "vendor/family/model-large")


def test_the_ceiling_never_overflows_any_walked_window():
    """The facade may send the same messages to either target."""
    for windows in ((1000, 900), (900, 1000), (5000, 5000)):
        walked = tuple(_attempt(f"p{i}", "m", window=w, sent={"max_tokens": 100})
                       for i, w in enumerate(windows))
        got = limits.prompt_ceiling(_resolution(*walked))
        assert all(got.tokens + 100 <= w for w in windows)
        assert got.binding == ("p0" if windows[0] <= windows[1] else "p1", "m")


def test_a_reason_says_what_was_asked_and_the_cap_that_cut_it():
    a = _attempt(window=8192, max_output=8192, sent={"max_tokens": 32000})
    got = limits.prompt_ceiling(_resolution(a))
    assert (got.tokens, got.reserve) == (0, 8192)
    assert got.reason == ("The preset asks for 32,000 reply tokens, capped at the model's max "
                          "output of 8,192; this model's window is 8,192, which leaves no room "
                          "for a prompt.")


def test_a_ceiling_the_fallback_binds_names_the_fallback():
    primary = _attempt("saltmarch", "vendor/big", window=200000)
    spare = _attempt("realm", "vendor/small", window=8192, sent={"max_tokens": 32000})
    got = limits.prompt_ceiling(_resolution(primary, spare))
    assert got.tokens == 0 and got.binding == ("realm", "vendor/small")
    assert got.reason == ("The fallback's preset asks for 32,000 reply tokens; the fallback's "
                          "window (vendor/small on realm) is 8,192, which leaves no room "
                          "for a prompt.")


def test_the_resolution_may_be_passed_by_name():
    a = _attempt(window=10000, sent={"max_tokens": 1000})
    assert limits.prompt_ceiling(resolved=_resolution(a)).tokens == 9000
