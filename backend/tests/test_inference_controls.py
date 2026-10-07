"""Effective controls (spec 8): one gateway function decides, per control, what
a connection is sent and why -- `llm_sampling.effective` -- and every older
helper (`split`, `sent_names`, `report`) is a view over it.

The existing kinds are held to a frozen copy of the pre-refactor split below:
for them, nothing a reader or a provider sees may move, except the one change
the spec asks for (`max_completion_tokens` at api.openai.com).
"""

from __future__ import annotations

import itertools

import pytest

from grimoire import llm_sampling as ls
from grimoire.llm import LLMClient
from grimoire.llm_errors import LLMError
from grimoire.store import llm_connections, sampler_presets
from grimoire.store.inference import capabilities, controls, providers
from tests.llm_fakes import RefusingProvider

ALL = {"temperature": 0.9, "top_p": 0.95, "top_k": 40, "min_p": 0.05,
       "repetition_penalty": 1.08, "frequency_penalty": 0.1, "presence_penalty": 0.2,
       "max_tokens": 800, "stop": ["\nYou:"]}

#: Claude 4.7+-style: adaptive thinking only, sampling parameters refused.
CURRENT = {"adaptive_thinking": True, "enabled_thinking": False,
           "effort": ["low", "medium", "high", "xhigh", "max"], "max_tokens": 64000}
#: An older Claude: budgeted thinking, sampling parameters taken.
OLDER = {"adaptive_thinking": False, "enabled_thinking": True, "max_tokens": 64000}


def _conn(kind, params=None, **fields):
    conn = {"kind": kind, "model": "m", **fields}
    if params is not None:
        conn["sampling"] = {"preset_id": "p", "preset_name": "P", "scope": "connection",
                            "params": params}
    return conn


def _claude_api(params=None, features=None, **fields):
    conn = _conn("anthropic", params, model="claude-test-1", **fields)
    if features is not None:
        conn["model_features"] = features
    return conn


# ---- the frozen pre-refactor split, the reference for the existing kinds ----
def _today_why_not(kind, extended, listed, name, value):
    if kind == "claude":
        return ls.WHY_CLAUDE
    if kind == "openai_compatible":
        if extended:
            return ""
        if name not in ls.OPENAI_STANDARD:
            return ls.WHY_STANDARD
        if name == "stop" and isinstance(value, list) and len(value) > ls.OPENAI_STOP_MAX:
            return ls.WHY_STOP
        return ""
    return ls.WHY_CATALOG if listed is not None and name not in listed else ""


def _today_split(conn):
    sampling = conn.get("sampling") if isinstance(conn, dict) else None
    stored = sampling.get("params") if isinstance(sampling, dict) else None
    stored = stored if isinstance(stored, dict) else {}
    kind = conn.get("kind", "openrouter") if isinstance(conn, dict) else "openrouter"
    extended = conn.get("sampler_support") == "extended"
    listed = conn.get("model_params")
    listed = {x for x in listed if isinstance(x, str)} if isinstance(listed, list) else None
    applied, dropped = {}, []
    for p in ls.PARAMS:
        if p.name not in stored:
            continue
        try:
            value = ls._check(p, stored[p.name])
        except ValueError:
            dropped.append({"param": p.name, "reason": ls.WHY_INVALID})
            continue
        why = _today_why_not(kind, extended, listed, p.name, value)
        if why:
            dropped.append({"param": p.name, "reason": why})
            continue
        applied[p.name] = value
        if kind == "openai_compatible" and p.name == "repetition_penalty":
            applied["repeat_penalty"] = value
    return applied, dropped


def _today_report(conn):
    if not isinstance(conn, dict) or not isinstance(conn.get("sampling"), dict):
        return None
    sampling = conn["sampling"]
    kind = conn.get("kind", "openrouter")
    applied, dropped = _today_split(conn)
    verified = not (kind not in ("claude", "openai_compatible")
                    and not isinstance(conn.get("model_params"), list) and bool(applied))
    return {"preset_id": sampling.get("preset_id", ""),
            "preset_name": sampling.get("preset_name", ""),
            "scope": sampling.get("scope", ""), "kind": kind,
            "applied": {n: applied[n] for n in ls.NAMES if n in applied},
            "dropped": dropped, "verified": verified}


_PRESETS = [ALL, {}, {"temperature": 0.7, "min_p": 0.1}, {"stop": list("abcde")},
            {"temperature": "hot", "top_k": 40.0, "bogus": 1}, {"max_tokens": 0},
            {"repetition_penalty": 1.1}, {"top_k": 40, "max_tokens": 300.0}]
_CASES = [
    *({"kind": k} for k in ("openrouter", "claude", "openai_compatible")),
    {},                                                         # no kind: openrouter
    {"kind": "openrouter", "model_params": ["temperature", "top_p", "max_tokens", "stop"]},
    {"kind": "openrouter", "model_params": []},
    {"kind": "openrouter", "model_params": ["temperature", {"bad": 1}, 3]},
    {"kind": "openrouter", "model_params": "not a list"},
    {"kind": "openai_compatible", "sampler_support": "extended"},
    {"kind": "openai_compatible", "base_url": "http://localhost:1234/v1"},
    {"kind": "openai_compatible", "base_url": "https://api.z.ai/api/paas/v4",
     "model": "glm-5.3", "reasoning_effort": "high"},
    {"kind": "claude", "model_params": ["temperature"]},
]


@pytest.mark.parametrize("fields, params", list(itertools.product(_CASES, _PRESETS)))
def test_the_existing_kinds_split_exactly_as_before(fields, params):
    conn = {"model": "m", **fields, "sampling": {"preset_id": "p", "preset_name": "P",
                                                 "scope": "connection", "params": params}}
    assert ls.split(conn) == _today_split(conn)
    assert ls.report(conn) == _today_report(conn)
    applied, _ = _today_split(conn)
    assert ls.sent_names(conn) == [n for n in ls.NAMES if n in applied]


def test_no_preset_is_still_no_report_and_no_split():
    assert ls.report({"kind": "openrouter"}) is None
    assert ls.split({"kind": "openrouter"}) == ({}, [])
    assert ls.split(None) == ({}, [])  # type: ignore[arg-type]


# ---- the vocabulary ----
def test_the_table_still_has_exactly_the_nine_rows():
    assert [row["name"] for row in ls.table()] == list(ls.NAMES)
    assert len(ls.table()) == 9


@pytest.mark.parametrize("value", ls.REASONING)
def test_validate_takes_each_reasoning_effort(value):
    assert ls.validate({"reasoning_effort": value}) == {"reasoning_effort": value}


@pytest.mark.parametrize("value", ["max", "", "Low", True, 3, None, ["low"]])
def test_validate_refuses_any_other_reasoning_effort(value):
    with pytest.raises(ValueError, match="reasoning_effort"):
        ls.validate({"reasoning_effort": value})


def test_validate_lists_reasoning_after_the_nine():
    got = ls.validate({"reasoning_effort": "low", "temperature": 1})
    assert list(got) == ["temperature", "reasoning_effort"]


def test_effective_has_the_documented_shape():
    eff = ls.effective(_conn("openrouter", {"temperature": 0.5}))
    assert set(eff) == {"requested", "effective", "controls"}
    assert list(eff["controls"]) == list(ls.CONTROLS)
    for entry in eff["controls"].values():
        assert entry["state"] in ls.STATES
        assert set(entry) == {"state", "wire", "why", "source"}
    assert eff["requested"] == {"temperature": 0.5}


@pytest.mark.parametrize("conn", [None, "x", {}, {"kind": "anthropic"},
                                  {"kind": "anthropic", "model_features": "junk",
                                   "sampling": {"params": {"max_tokens": "lots"}}},
                                  {"kind": "openrouter", "sampling": "junk"},
                                  {"kind": "openai_compatible", "base_url": 7}])
def test_effective_never_raises(conn):
    eff = ls.effective(conn)  # type: ignore[arg-type]
    assert set(eff) == {"requested", "effective", "controls"}


def test_every_state_but_supported_says_why():
    for conn in (_conn("claude", ALL), _claude_api(ALL, CURRENT),
                 _conn("openai_compatible", ALL), _conn("openrouter", ALL),
                 _conn("openai_compatible", ALL, base_url="https://api.openai.com/v1")):
        for name, entry in ls.effective(conn)["controls"].items():
            if entry["state"] != "supported":
                assert entry["why"], (conn["kind"], name)


# ---- OpenAI: max_tokens is max_completion_tokens there ----
def test_the_openai_api_is_sent_max_completion_tokens():
    conn = _conn("openai_compatible", {"max_tokens": 800, "temperature": 0.5},
                 base_url="https://api.openai.com/v1")
    eff = ls.effective(conn)
    assert eff["controls"]["max_tokens"]["state"] == "translated"
    assert eff["controls"]["max_tokens"]["wire"] == "max_completion_tokens"
    assert eff["effective"] == {"temperature": 0.5, "max_completion_tokens": 800}
    assert ls.split(conn) == ({"temperature": 0.5, "max_completion_tokens": 800}, [])
    assert ls.sent_names(conn) == ["temperature", "max_tokens"]
    assert ls.report(conn)["applied"] == {"temperature": 0.5, "max_tokens": 800}


@pytest.mark.parametrize("url", ["https://api.openai.com/v1", "https://API.openai.com/v1/",
                                 "http://localhost:1234/v1", "https://api.z.ai/api/paas/v4",
                                 "https://api.openai.com.example.test/v1", "", "::bad::",
                                 "https://proxy.example.test/api.openai.com/v1"])
def test_the_openai_host_rule_agrees_with_the_provider_presets(url):
    conn = _conn("openai_compatible", {"max_tokens": 10}, base_url=url)
    translated = ls.effective(conn)["controls"]["max_tokens"]["wire"] == "max_completion_tokens"
    assert translated == (providers.infer(conn).id == "openai")


async def test_the_openai_endpoint_receives_max_completion_tokens():
    from tests.test_llm import FakeProvider
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = _conn("openai_compatible", {"max_tokens": 300}, base_url="https://api.openai.com/v1",
                 api_key="k")
    [c async for c in client.stream([], conn)]
    assert oc.calls[0][1]["sampling"] == {"max_completion_tokens": 300}


async def test_a_refusal_naming_the_translated_spelling_is_a_preset_refusal():
    provider = RefusingProvider(failing={"primary"}, status=400,
                                why="Unsupported value: 'max_completion_tokens' too large")
    client = LLMClient(openrouter=provider, claude=provider, openai_compatible=provider,
                       timeout=0, retries=0,
                       fallback=lambda: {"id": "b", "kind": "openrouter", "model": "backup",
                                         "api_key": "k"})
    conn = _conn("openai_compatible", {"max_tokens": 99999}, id="a", model="primary",
                 base_url="https://api.openai.com/v1")
    with pytest.raises(LLMError) as exc:
        [c async for c in client.stream([], conn)]
    assert "fallback connection was not tried" in exc.value.detail
    assert [m for m, _ in provider.calls] == ["primary"]


# ---- the Anthropic API ----
def test_review_focus_4_a_current_claude_model_is_sent_no_temperature():
    """Claude 4.7+ refuses sampling parameters: the preset's temperature is not
    sent, and the reader is told why."""
    conn = _claude_api({"temperature": 0.7}, CURRENT)
    eff = ls.effective(conn)
    assert "temperature" not in eff["effective"]
    assert eff["controls"]["temperature"]["state"] == "unsupported"
    assert eff["controls"]["temperature"]["wire"] is None
    assert "sampling" in eff["controls"]["temperature"]["why"]
    report = ls.report(conn)
    assert report["applied"] == {}
    assert report["dropped"] == [{"param": "temperature",
                                  "reason": eff["controls"]["temperature"]["why"]}]
    assert ls.sent_names(conn) == []


@pytest.mark.parametrize("features", [None, {}, {"adaptive_thinking": True}])
def test_sampling_is_unsupported_unless_the_catalog_says_enabled_thinking(features):
    eff = ls.effective(_claude_api({"temperature": 0.7, "top_p": 0.9, "top_k": 5}, features))
    for name in ("temperature", "top_p", "top_k"):
        assert eff["controls"][name]["state"] == "unsupported"
        assert name not in eff["effective"]


def test_an_older_claude_model_takes_sampling_parameters():
    eff = ls.effective(_claude_api({"temperature": 0.7, "top_k": 5}, OLDER))
    assert eff["effective"] == {"temperature": 0.7, "top_k": 5, "max_tokens": 16000}
    assert {eff["controls"][n]["state"] for n in ("temperature", "top_k")} == {"supported"}
    alone = ls.effective(_claude_api({"top_p": 0.9}, OLDER))
    assert alone["effective"] == {"top_p": 0.9, "max_tokens": 16000}
    # The pair is refused by the models that take sampling at all: temperature wins.
    both = ls.effective(_claude_api({"temperature": 0.7, "top_p": 0.9}, OLDER))
    assert both["effective"] == {"temperature": 0.7, "max_tokens": 16000}
    assert both["controls"]["top_p"]["state"] == "unsupported"


def test_sampling_is_unsupported_whenever_thinking_is_sent():
    eff = ls.effective(_claude_api({"temperature": 0.7, "reasoning_effort": "low"}, OLDER))
    assert "thinking" in eff["effective"]
    assert "temperature" not in eff["effective"]
    assert eff["controls"]["temperature"]["state"] == "unsupported"
    assert "thinking" in eff["controls"]["temperature"]["why"]


def test_the_anthropic_api_has_no_penalties_or_min_p():
    eff = ls.effective(_claude_api(ALL, OLDER))
    for name in ("min_p", "repetition_penalty", "frequency_penalty", "presence_penalty"):
        assert eff["controls"][name]["state"] == "unsupported"
        assert name not in eff["effective"]
    assert "repeat_penalty" not in eff["effective"]


@pytest.mark.parametrize("params, features, sent", [
    ({}, None, 16000),
    ({}, CURRENT, 16000),
    ({}, {"max_tokens": 8192}, 8192),
    ({"max_tokens": 4000}, CURRENT, 4000),
    ({"max_tokens": 100000}, CURRENT, 64000),
    ({"max_tokens": 0}, CURRENT, 16000),                 # invalid: the default stands
    ({}, {"max_tokens": True}, 16000),                   # a bool is not a limit
])
def test_anthropic_max_tokens_is_always_sent(params, features, sent):
    eff = ls.effective(_claude_api(params, features))
    assert eff["effective"]["max_tokens"] == sent


def test_a_capped_max_tokens_says_so_and_is_reported_as_sent():
    conn = _claude_api({"max_tokens": 100000}, CURRENT)
    assert "64000" in ls.effective(conn)["controls"]["max_tokens"]["why"]
    assert ls.report(conn)["applied"] == {"max_tokens": 64000}
    assert ls.split(conn) == ({"max_tokens": 64000}, [])


def test_an_invalid_anthropic_max_tokens_is_dropped_but_the_default_is_sent():
    conn = _claude_api({"max_tokens": 0}, CURRENT)
    assert ls.effective(conn)["controls"]["max_tokens"]["state"] == "unsupported"
    assert ls.effective(_claude_api({}, CURRENT))["controls"]["max_tokens"] == {
        "state": "supported", "wire": "max_tokens", "why": "", "source": "adapter"}
    assert ls.report(conn)["dropped"] == [{"param": "max_tokens", "reason": ls.WHY_INVALID}]
    assert ls.effective(conn)["effective"] == {"max_tokens": 16000}


def test_stop_is_sent_as_stop_sequences_and_reported_canonically():
    conn = _claude_api({"stop": ["\nYou:"]}, CURRENT)
    eff = ls.effective(conn)
    assert eff["controls"]["stop"]["state"] == "translated"
    assert eff["controls"]["stop"]["wire"] == "stop_sequences"
    assert eff["effective"]["stop_sequences"] == ["\nYou:"]
    assert "stop" not in eff["effective"]
    assert ls.report(conn)["applied"] == {"stop": ["\nYou:"]}
    assert ls.sent_names(conn) == ["stop"]


@pytest.mark.parametrize("level", ["low", "medium", "high"])
def test_adaptive_thinking_carries_the_effort(level):
    eff = ls.effective(_claude_api({"reasoning_effort": level}, CURRENT))
    assert eff["effective"]["thinking"] == {"type": "adaptive"}
    assert eff["effective"]["output_config"] == {"effort": level}
    assert "budget_tokens" not in str(eff["effective"])
    assert eff["controls"]["reasoning_effort"]["state"] == "translated"
    assert eff["controls"]["reasoning_effort"]["wire"] == "thinking"


def test_adaptive_is_preferred_where_both_are_listed():
    both = {**OLDER, "adaptive_thinking": True}
    eff = ls.effective(_claude_api({"reasoning_effort": "high"}, both))
    assert eff["effective"]["thinking"] == {"type": "adaptive"}


def test_an_effort_level_the_catalog_does_not_list_is_unsupported():
    features = {**CURRENT, "effort": ["high", "max"]}
    eff = ls.effective(_claude_api({"reasoning_effort": "low"}, features))
    assert eff["controls"]["reasoning_effort"]["state"] == "unsupported"
    assert "thinking" not in eff["effective"] and "output_config" not in eff["effective"]


@pytest.mark.parametrize("level, max_tokens, budget", [
    ("low", None, 1024), ("medium", None, 4096), ("high", None, 8000),
    ("high", 64000, 16000), ("high", 40000, 16000), ("medium", 6000, 3000),
    ("low", 1500, 1024)])
def test_budgeted_thinking_on_an_older_model(level, max_tokens, budget):
    params = {"reasoning_effort": level, **({"max_tokens": max_tokens} if max_tokens else {})}
    eff = ls.effective(_claude_api(params, OLDER))
    sent = eff["effective"]
    assert sent["thinking"] == {"type": "enabled", "budget_tokens": budget}
    assert ls.THINKING_MIN <= sent["thinking"]["budget_tokens"] < sent["max_tokens"]
    assert "output_config" not in sent


def test_no_room_to_think_is_unsupported():
    eff = ls.effective(_claude_api({"reasoning_effort": "low", "max_tokens": 1024}, OLDER))
    assert eff["controls"]["reasoning_effort"]["state"] == "unsupported"
    assert "thinking" not in eff["effective"]


@pytest.mark.parametrize("features", [None, {}, {"max_tokens": 8000}])
def test_thinking_the_catalog_does_not_describe_is_unknown_and_not_sent(features):
    eff = ls.effective(_claude_api({"reasoning_effort": "high"}, features))
    assert eff["controls"]["reasoning_effort"]["state"] == "unknown"
    assert eff["controls"]["reasoning_effort"]["why"]
    assert "thinking" not in eff["effective"]


def test_a_model_the_catalog_says_cannot_think_is_unsupported():
    features = {"adaptive_thinking": False, "enabled_thinking": False}
    eff = ls.effective(_claude_api({"reasoning_effort": "high"}, features))
    assert eff["controls"]["reasoning_effort"]["state"] == "unsupported"
    assert "thinking" not in eff["effective"]


def test_off_omits_thinking():
    older = ls.effective(_claude_api({"reasoning_effort": "off"}, OLDER))
    assert "thinking" not in older["effective"]
    assert older["controls"]["reasoning_effort"]["state"] == "supported"
    current = ls.effective(_claude_api({"reasoning_effort": "off"}, CURRENT))
    assert "thinking" not in current["effective"]
    # Whether omitting it turns thinking off depends on the model, and the
    # catalog does not say: some current models cannot turn it off.
    assert current["controls"]["reasoning_effort"]["state"] == "unknown"
    assert "off" in current["controls"]["reasoning_effort"]["why"]


# ---- reasoning_effort on the other adapters ----
def test_openrouter_sends_reasoning_effort_where_the_catalog_lists_it():
    eff = ls.effective(_conn("openrouter", {"reasoning_effort": "high"},
                             model_params=["reasoning", "temperature"]))
    assert eff["effective"] == {"reasoning": {"effort": "high"}}
    assert eff["controls"]["reasoning_effort"]["state"] == "translated"


def test_openrouter_drops_it_where_the_catalog_does_not():
    conn = _conn("openrouter", {"reasoning_effort": "high"}, model_params=["temperature"])
    assert ls.effective(conn)["effective"] == {}
    assert ls.report(conn)["dropped"] == [{"param": "reasoning_effort",
                                          "reason": ls.WHY_CATALOG}]


def test_openrouter_without_a_catalog_sends_it_unverified():
    conn = _conn("openrouter", {"reasoning_effort": "low"})
    eff = ls.effective(conn)
    assert eff["controls"]["reasoning_effort"]["state"] == "unknown"
    assert eff["effective"] == {"reasoning": {"effort": "low"}}
    assert ls.report(conn)["verified"] is False


def test_the_openai_api_takes_reasoning_effort():
    conn = _conn("openai_compatible", {"reasoning_effort": "medium"},
                 base_url="https://api.openai.com/v1")
    eff = ls.effective(conn)
    assert eff["controls"]["reasoning_effort"]["state"] == "supported"
    assert eff["effective"] == {"reasoning_effort": "medium"}
    assert ls.sent_names(conn) == ["reasoning_effort"]


def test_another_openai_compatible_endpoint_is_unknown():
    eff = ls.effective(_conn("openai_compatible", {"reasoning_effort": "high"},
                             base_url="http://localhost:1234/v1"))
    assert eff["controls"]["reasoning_effort"]["state"] == "unknown"
    assert eff["effective"] == {"reasoning_effort": "high"}


def test_the_agent_sdk_takes_no_reasoning_effort():
    eff = ls.effective(_conn("claude", {"reasoning_effort": "high"}))
    assert eff["controls"]["reasoning_effort"]["state"] == "unsupported"
    assert eff["effective"] == {}


GLM = {"base_url": "https://api.z.ai/api/paas/v4", "model": "glm-5.3"}


@pytest.mark.parametrize("legacy", ["", "low", "high", "max", "medium", "bogus"])
@pytest.mark.parametrize("model", ["glm-5.3", "vendor/GLM-5.3-flash", "another-model"])
def test_the_legacy_glm_setting_applies_exactly_as_before(legacy, model):
    from grimoire import llm_reasoning
    conn = _conn("openai_compatible", {}, base_url=GLM["base_url"], model=model,
                 reasoning_effort=legacy)
    expected = llm_reasoning.glm_effort(conn)
    got = ls.effective(conn)["effective"].get("reasoning_effort", "")
    assert got == expected
    # The legacy setting is the connection's, not the preset's: it is not a
    # sent preset parameter, so a refusal of it is not a preset refusal.
    assert ls.sent_names(conn) == []
    assert ls.report(conn)["applied"] == {}


def test_a_preset_effort_wins_over_the_legacy_glm_setting():
    conn = _conn("openai_compatible", {"reasoning_effort": "low"}, **GLM,
                 reasoning_effort="max")
    assert ls.effective(conn)["effective"] == {"reasoning_effort": "low"}


def test_glm_takes_only_the_levels_it_knows():
    eff = ls.effective(_conn("openai_compatible", {"reasoning_effort": "medium"}, **GLM))
    assert eff["controls"]["reasoning_effort"]["state"] == "unsupported"
    assert eff["effective"] == {}


def test_the_legacy_setting_is_ignored_on_other_kinds():
    conn = _conn("openrouter", {}, model="glm-5.3", reasoning_effort="high")
    assert ls.effective(conn)["effective"] == {}


# ---- the facade sends what `effective` decided ----
async def test_an_openrouter_effort_reaches_the_request():
    from tests.test_llm import FakeProvider
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = _conn("openrouter", {"temperature": 0.5, "reasoning_effort": "high"},
                 api_key="k", model_params=["temperature", "reasoning"])
    [c async for c in client.stream([], conn)]
    assert op.calls[0][1]["sampling"] == {"temperature": 0.5, "reasoning": {"effort": "high"}}


async def test_an_openai_effort_reaches_the_request_as_a_keyword():
    from tests.test_llm import FakeProvider
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = _conn("openai_compatible", {"reasoning_effort": "low", "temperature": 1},
                 base_url="https://api.openai.com/v1", api_key="k")
    [c async for c in client.stream([], conn)]
    assert oc.calls[0][1]["reasoning_effort"] == "low"
    assert oc.calls[0][1]["sampling"] == {"temperature": 1}


async def test_a_refused_effort_is_a_preset_refusal():
    provider = RefusingProvider(failing={"primary"}, status=400,
                                why="Unrecognized request argument: reasoning")
    client = LLMClient(openrouter=provider, claude=provider, openai_compatible=provider,
                       timeout=0, retries=0,
                       fallback=lambda: {"id": "b", "kind": "openrouter", "model": "backup",
                                         "api_key": "k"})
    conn = _conn("openrouter", {"reasoning_effort": "high"}, id="a", model="primary",
                 api_key="k")
    with pytest.raises(LLMError) as exc:
        [c async for c in client.stream([], conn)]
    assert "fallback connection was not tried" in exc.value.detail


# ---- the preset store ----
@pytest.fixture()
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


def test_a_preset_keeps_its_reasoning_effort(home):
    pid = sampler_presets.create_preset("Mara's warm", {"temperature": 0.8,
                                                        "reasoning_effort": "high"})
    assert sampler_presets.read_preset(pid)["params"] == {"temperature": 0.8,
                                                          "reasoning_effort": "high"}
    with pytest.raises(ValueError, match="reasoning_effort"):
        sampler_presets.create_preset("Bad", {"reasoning_effort": "max"})


def test_a_sillytavern_reasoning_field_is_ignored():
    params, report = sampler_presets.from_sillytavern(
        {"temperature": 0.8, "reasoning_effort": "high", "openai_reasoning_effort": "low"})
    assert params == {"temperature": 0.8}
    assert "reasoning_effort" not in params
    assert not any(m["param"] == "reasoning_effort" for m in report["mapped"])


# ---- the store's preview, and each attempt's controls ----
def _openrouter(model="vendor/mara-7b", rows=None):
    cid = llm_connections.create_connection("openrouter", "Saltmarch", api_key="k",
                                            model=model)
    conn = llm_connections.read_connection_raw(cid)
    if rows is not None:
        llm_connections.set_cached_models(cid, rows, conn["rev"])
    return conn


def test_preview_is_effective_over_the_lowered_preset(home):
    conn = _openrouter(rows=[{"id": "vendor/winifred-2", "params": ["temperature"]}])
    pid = sampler_presets.create_preset("Warm", {"temperature": 0.8, "top_k": 30,
                                                 "reasoning_effort": "low"})
    got = controls.preview(pid, conn, "vendor/winifred-2")
    assert got["requested"] == {"temperature": 0.8, "top_k": 30, "reasoning_effort": "low"}
    assert got["effective"] == {"temperature": 0.8}
    assert got["controls"]["top_k"]["state"] == "unsupported"
    assert got["controls"]["temperature"]["source"] == "catalog"
    # The same decisions the gateway makes for the same lowered connection.
    eff = ls.effective({**conn, "model": "vendor/winifred-2",
                        "model_params": ["temperature"],
                        "sampling": {"params": {"temperature": 0.8, "top_k": 30,
                                                "reasoning_effort": "low"}}})
    assert {k: {**v, "source": None} for k, v in got["controls"].items()} == {
        k: {**v, "source": None} for k, v in eff["controls"].items()}


def test_preview_defaults_to_the_connections_own_model(home):
    conn = _openrouter(rows=[{"id": "vendor/mara-7b", "params": ["top_p"]}])
    pid = sampler_presets.create_preset("Cool", {"temperature": 0.2})
    got = controls.preview(pid, conn, "")
    assert got["controls"]["temperature"]["state"] == "unsupported"


def test_preview_of_no_preset_requests_nothing(home):
    conn = _openrouter()
    got = controls.preview("", conn, "vendor/mara-7b")
    assert got["requested"] == {} and got["effective"] == {}
    got = controls.preview("no-such-preset", conn, "vendor/mara-7b")
    assert got["requested"] == {}


def test_every_control_names_a_source(home):
    conn = _openrouter()
    got = controls.preview("", conn, "vendor/mara-7b")
    for entry in got["controls"].values():
        assert entry["source"] in capabilities.SOURCES


def test_a_control_mapped_to_a_capability_takes_its_source(home, monkeypatch):
    conn = _openrouter(rows=[{"id": "vendor/mara-7b", "params": ["structured_outputs"]}])
    monkeypatch.setitem(controls.CAPABILITY, "stop", "structured_output")
    got = controls.preview("", conn, "vendor/mara-7b")
    assert got["controls"]["stop"]["source"] == capabilities.caps_for(
        conn)["structured_output"].source == "catalog"


def test_preview_never_raises_on_an_unreadable_connection(home):
    got = controls.preview("", {"kind": "anthropic", "id": "../nope", "model": "claude-test-1"},
                           "claude-test-1")
    assert got["effective"] == {"max_tokens": 16000}
