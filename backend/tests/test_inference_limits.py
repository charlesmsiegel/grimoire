"""01i: the model's window and output cap as a resolved fact
(`store.inference.limits.of`), and the prompt ceiling a consumer derives from
a resolution (`prompt_ceiling`, `reply_reserve`).

Pure: every attempt here is built by hand around a target whose limits and
preset are what the case says, and its controls are the gateway's own answer
for that target (`llm_sampling.effective`), as the resolver builds them.
"""

from __future__ import annotations

import pytest

from grimoire import llm_sampling, wire
from grimoire.store.inference import limits
from grimoire.store.inference.resolved import Attempt, ResolvedInference

from . import wire_kit

U = wire.UNKNOWN_LIMIT


def _limits(window: int | None = None, max_output: int | None = None,
            source: str = "catalog") -> wire.Limits:
    return wire.Limits(U if window is None else wire.Limit(window, source),
                       U if max_output is None else wire.Limit(max_output, source))


def _attempt(window: int | None = None, max_output: int | None = None, *,
             provider_id: str = "saltmarch", model: str = "vendor/m",
             preset: dict | None = None, mode: str = "", **fields) -> Attempt:
    """An attempt on `provider_id` at `model` whose target states `window` and
    `max_output` and is sent `preset` (None: provider defaults), its controls
    `llm_sampling.effective` over that target."""
    sampling = wire.Sampling("p", "P", "global", dict(preset)) if preset else wire.Sampling()
    target = wire_kit.target(provider_id=provider_id, model=model, sampling=sampling,
                             limits=_limits(window, max_output), **fields)
    controls = (llm_sampling.not_applicable(target, llm_sampling.WHY_NATIVE)
                if mode == "native" else llm_sampling.effective(target))
    return Attempt(provider_id, model, "p" if preset else "", provider_kind=target.kind,
                   controls=controls, decision_mode=mode, target=target)


def _resolved(*attempts: Attempt, rides: bool = True,
              operation: str = "generate") -> ResolvedInference:
    return ResolvedInference(task="chat", operation=operation, route="chat", role="primary",
                             via="role", scope="global", attempts=attempts,
                             rides=rides and len(attempts) > 1)


# ---- of: the source order ----
def test_the_users_word_outranks_the_catalog_for_each_value_alone():
    row = {"id": "vendor/m", "context": 131072, "max_output": 16000}
    assert limits.of(row, {"context_window": 8192}) == wire.Limits(
        wire.Limit(8192, "user"), wire.Limit(16000, "catalog"))
    assert limits.of(row, {"max_output": 4096}) == wire.Limits(
        wire.Limit(131072, "catalog"), wire.Limit(4096, "user"))
    assert limits.of(row, {"context_window": 8192, "max_output": 4096}) == wire.Limits(
        wire.Limit(8192, "user"), wire.Limit(4096, "user"))


def test_the_catalog_is_read_when_nothing_is_stated():
    row = {"id": "vendor/m", "context": 200000, "max_output": 64000}
    stated_nothing = {"context_window": None, "max_output": None, "vision": ""}
    assert limits.of(row, stated_nothing) == _limits(200000, 64000)
    # A catalog row that names a window and no cap: the cap is unknown.
    assert limits.of({"id": "m", "context": 8192}, {}) == wire.Limits(wire.Limit(8192, "catalog"), U)


def test_nothing_known_is_unknown_never_zero():
    assert limits.of(None, {}) == wire.Limits() == wire.Limits(U, U)
    assert limits.of({"id": "m", "context": None}, None) == wire.Limits()
    assert limits.of({"id": "m", "context": 0, "max_output": 0}, {}) == wire.Limits()


@pytest.mark.parametrize("bad", [0, -1, True, False, 1.5, "8192", 2**31, None, [8192], {}])
def test_a_malformed_facts_or_row_value_contributes_nothing(bad):
    row = {"id": "m", "context": 32768, "max_output": 4096}
    assert limits.of(row, {"context_window": bad, "max_output": bad}) == _limits(32768, 4096)
    assert limits.of({"id": "m", "context": bad, "max_output": bad}, {}) == wire.Limits()


def test_a_whole_float_from_a_sidecar_is_read_as_the_catalog_reads_it():
    assert limits.of({"id": "m", "context": 8192.0}, {}).window == wire.Limit(8192, "catalog")
    # The user's word is only ever written as an int: a float is not one.
    assert limits.of({}, {"context_window": 8192.0}).window == U


@pytest.mark.parametrize("row, facts", [("row", "facts"), (["context"], [1]), (3, 4.5)])
def test_of_never_raises_on_a_non_mapping(row, facts):
    assert limits.of(row, facts) == wire.Limits()


# ---- reply_reserve ----
def test_a_per_call_cap_outranks_the_presets():
    a = _attempt(32768, preset={"max_tokens": 2000})
    assert limits.reply_reserve(a) == 2000
    assert limits.reply_reserve(a, max_tokens=8192) == 8192


def test_a_sent_max_tokens_is_the_reserve():
    assert limits.reply_reserve(_attempt(131072, preset={"max_tokens": 800})) == 800


def test_the_openai_api_reserve_is_its_max_completion_tokens():
    a = _attempt(128000, kind="openai_compatible", base_url="https://api.openai.com/v1",
                 preset={"max_tokens": 900})
    assert a.controls["effective"] == {"max_completion_tokens": 900}
    assert limits.reply_reserve(a) == 900


def test_the_anthropic_api_reserve_is_its_default_max_tokens():
    """That API is always sent one, so its reserve is known exactly."""
    a = _attempt(200000, kind="anthropic", model="claude-test-1")
    assert limits.reply_reserve(a) == llm_sampling.ANTHROPIC_MAX_TOKENS


def test_an_unset_max_tokens_reserves_the_default_or_a_quarter_window():
    assert limits.reply_reserve(_attempt(131072)) == limits.DEFAULT_REPLY_RESERVE == 4096
    assert limits.reply_reserve(_attempt(8192)) == 2048
    assert limits.reply_reserve(_attempt(None)) == limits.DEFAULT_REPLY_RESERVE
    # A per-call cap that is not a positive int is no cap at all.
    assert limits.reply_reserve(_attempt(8192), max_tokens=0) == 2048
    assert limits.reply_reserve(_attempt(8192), max_tokens=True) == 2048


def test_every_reserve_is_capped_at_a_known_max_output():
    assert limits.reply_reserve(_attempt(32768, 1000), max_tokens=8192) == 1000
    assert limits.reply_reserve(_attempt(32768, 1000, preset={"max_tokens": 4000})) == 1000
    assert limits.reply_reserve(_attempt(131072, 1000)) == 1000
    assert limits.reply_reserve(_attempt(None, 1000)) == 1000


def test_max_output_is_never_itself_the_reserve():
    """A listing that puts the cap close to the window still leaves a prompt
    room: the cap bounds what is asked, it is not what is held back."""
    a = _attempt(131072, 128000)
    assert limits.reply_reserve(a) == limits.DEFAULT_REPLY_RESERVE
    ceiling = limits.prompt_ceiling(_resolved(a))
    assert ceiling.tokens == 131072 - limits.DEFAULT_REPLY_RESERVE


# ---- prompt_ceiling ----
def test_the_smaller_window_binds_when_a_fallback_rides():
    primary = _attempt(131072, provider_id="saltmarch", model="vendor/large")
    fallback = _attempt(8192, provider_id="realm", model="local/small")
    got = limits.prompt_ceiling(_resolved(primary, fallback))
    assert got == limits.Ceiling(tokens=8192 - 2048, window=8192, reserve=2048, complete=True,
                                 binding=("realm", "local/small"), reason="")


def test_a_fallback_that_does_not_ride_is_ignored():
    primary = _attempt(131072, model="vendor/large")
    fallback = _attempt(8192, provider_id="realm", model="local/small")
    got = limits.prompt_ceiling(_resolved(primary, fallback, rides=False))
    assert (got.tokens, got.binding, got.complete) == (131072 - 4096, ("saltmarch", "vendor/large"),
                                                       True)


def test_a_decide_resolutions_separate_stage_is_ignored():
    """A native primary is a stage of its own, so the structured fallback is
    sent as another stage (`rides` False) and is not on the chain."""
    native = _attempt(32768, mode="native")
    structured = _attempt(4096, provider_id="realm", model="local/small", mode="structured")
    got = limits.prompt_ceiling(_resolved(native, structured, rides=False, operation="decide"))
    assert (got.window, got.binding) == (32768, ("saltmarch", "vendor/m"))
    assert got.tokens == 32768 - 4096


def test_one_unknown_window_is_incomplete():
    got = limits.prompt_ceiling(_resolved(_attempt(None), _attempt(16384, provider_id="realm")))
    assert (got.tokens, got.window, got.complete) == (16384 - 4096, 16384, False)
    assert got.binding == ("realm", "vendor/m")


def test_no_known_window_is_none_never_zero():
    got = limits.prompt_ceiling(_resolved(_attempt(None, 4000), _attempt(None)))
    assert got == limits.Ceiling(tokens=None, window=None, reserve=0, complete=False,
                                 binding=None, reason="")


def test_nothing_resolved_is_none_and_incomplete():
    got = limits.prompt_ceiling(_resolved())
    assert (got.tokens, got.complete, got.binding) == (None, False, None)


def test_a_preset_larger_than_the_window_floors_at_zero_with_a_reason():
    got = limits.prompt_ceiling(_resolved(_attempt(8192, preset={"max_tokens": 32000})))
    assert (got.tokens, got.window, got.reserve) == (0, 8192, 32000)
    assert got.reason == "The preset asks for 32,000 reply tokens; vendor/m's window is 8,192."
    # Exactly full is nothing more fitting, too.
    full = limits.prompt_ceiling(_resolved(_attempt(8192)), max_tokens=8192)
    assert full.tokens == 0
    assert full.reason == "The call asks for 8,192 reply tokens; vendor/m's window is 8,192."


def test_a_ceiling_with_room_has_no_reason():
    assert limits.prompt_ceiling(_resolved(_attempt(8192, preset={"max_tokens": 1000}))).reason == ""


def test_a_per_call_cap_is_reserved_not_the_default():
    got = limits.prompt_ceiling(_resolved(_attempt(131072)), max_tokens=8192)
    assert (got.tokens, got.reserve) == (131072 - 8192, 8192)


def test_an_explicit_reserve_is_honoured():
    """Outright: not the preset's, not a per-call cap's, not capped."""
    a = _attempt(32768, 1000, preset={"max_tokens": 4000})
    got = limits.prompt_ceiling(_resolved(a), reserve=12000, max_tokens=2000)
    assert (got.tokens, got.reserve) == (32768 - 12000, 12000)
    over = limits.prompt_ceiling(_resolved(a), reserve=40000)
    assert over.tokens == 0
    assert over.reason == "A reply reserve of 40,000 tokens fills vendor/m's window of 32,768."


def test_each_attempt_reserves_by_its_own_controls():
    primary = _attempt(32768, preset={"max_tokens": 30000})
    fallback = _attempt(16384, provider_id="realm", preset={"max_tokens": 1000})
    got = limits.prompt_ceiling(_resolved(primary, fallback))
    assert (got.tokens, got.binding, got.reserve) == (32768 - 30000, ("saltmarch", "vendor/m"),
                                                      30000)


def test_binding_is_a_tuple_for_a_model_id_with_a_slash():
    got = limits.prompt_ceiling(_resolved(_attempt(65536, provider_id="saltmarch",
                                                   model="vendor/family/m-2")))
    assert got.binding == ("saltmarch", "vendor/family/m-2")
    assert isinstance(got.binding, tuple)


def test_an_override_resolution_answers_the_same_way():
    """A reroll's override resolution is just another resolution."""
    from grimoire.routes.common import UsableInference
    a = _attempt(16384)
    usable = UsableInference(task="chat", operation="generate", route="chat", role="",
                             via="", scope="none", attempts=(a,))
    assert limits.prompt_ceiling(usable) == limits.prompt_ceiling(_resolved(a))
