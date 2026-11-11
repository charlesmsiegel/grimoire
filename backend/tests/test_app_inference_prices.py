"""112's token prices: the settings that hold them, and the cost worked out from booked token counts.

Synthetic throughout: a loopback model server from ``provider_servers`` and libraries from ``textapi_fixtures``.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from provider_servers import base_url, sse_lines, sse_server, stream
from scene_fixtures import MARA
from store_helpers import OWNER
from textapi_fixtures import V1_CHAT, Device, desktop_library

from grimoire.app import ConfigError, parse_model_config
from grimoire.app.config import config_json
from grimoire.app.inference import InferenceApi, SettingsFile, _totals
from grimoire.app.pricing import cost_text, cost_view, price_table
from grimoire.core.inference import CallAttempt, CallId, InferenceStage, Measurement, TokenUsage
from grimoire.textapi import ApiRefused
from grimoire.tracing import Price, PriceTable

USAGE = {"prompt_tokens": 120, "completion_tokens": 9, "prompt_tokens_details": {"cached_tokens": 20}}
CHAT = str(V1_CHAT)
BASE = {"provider_id": "p", "base_url": "https://x.example/v1", "model": "m"}
ROUTE = "p@x.example/m"
TABLE = PriceTable({ROUTE: Price(Decimal("3"), Decimal("15"))})
REPORTED = Measurement.PROVIDER_REPORTED


def _attempt(prompt, reply, *, cached=None, route=ROUTE, outcome="completed", measurement=REPORTED, ordinal=1):
    call = CallId("campaign", "turn", "main", InferenceStage.NPC_GENERATION, "actor", ordinal)
    return CallAttempt(call, 1, TokenUsage(prompt, reply, cached), measurement, outcome, route)


@pytest.fixture
def desk(tmp_path):
    folder, _card_chat = desktop_library(tmp_path / "desk")
    return folder


# -- the settings ------------------------------------------------------------------------------------------

def test_a_settings_file_written_before_prices_loads_and_is_written_back_unchanged():
    old = {**BASE, "key_env": "P_API_KEY", "fallbacks": ["n"], "attempts": 2, "director": 0,
           "max_output_tokens": 500, "max_tokens_field": "max_tokens"}
    config = parse_model_config(old)
    assert (config.price_prompt, config.price_reply, config.price_cached) == (None, None, None)
    assert config.priced is False and price_table(config) is None
    assert config_json(config) == old  # no price keys appear: the same file as before


def test_prices_are_kept_as_the_decimal_text_they_were_written_as():
    raw = {**BASE, "price_prompt": "3.50", "price_reply": "15", "price_cached": "0.000001"}
    config = parse_model_config(raw)
    assert (config.price_prompt, config.price_reply, config.price_cached) == (
        Decimal("3.50"), Decimal("15"), Decimal("0.000001"))
    written = config_json(config)
    assert {key: written[key] for key in raw if key.startswith("price_")} == {
        "price_prompt": "3.50", "price_reply": "15", "price_cached": "0.000001"}
    assert parse_model_config(json.loads(json.dumps(written))) == config
    assert price_table(config).prices[ROUTE] == Price(Decimal("3.50"), Decimal("15"), Decimal("0.000001"))


def test_one_price_alone_is_kept_but_prices_nothing():
    config = parse_model_config({**BASE, "price_prompt": "3"})
    assert config.priced is False and price_table(config) is None
    assert config_json(config)["price_prompt"] == "3" and "price_reply" not in config_json(config)
    assert parse_model_config({**BASE, "price_prompt": "0", "price_reply": "0"}).priced is True  # free is a price


@pytest.mark.parametrize("value", [3, 3.5, True, "", " 3", "3 ", "-1", "+1", "1e3", "NaN", "Infinity", "1,5", "1.",
                                   ".5", "1.0000001", "1000000", "0x10", "３", [], {}])
@pytest.mark.parametrize("name", ["price_prompt", "price_reply", "price_cached"])
def test_a_price_that_is_not_bounded_decimal_text_is_refused(name, value):
    with pytest.raises(ConfigError) as refused:
        parse_model_config({**BASE, name: value})
    assert str(refused.value).startswith(name)


def test_prices_are_checked_and_saved_through_the_settings_ops_and_an_empty_field_is_left_out(desk, tmp_path):
    path = tmp_path / "model.json"
    with sse_server(stream(sse_lines(usage=USAGE))) as server, Device(desk, runtime=False) as device:
        inference = InferenceApi(device.api, SettingsFile(path), owner=OWNER, environ={})
        draft = {"provider_id": "local", "base_url": base_url(server), "model": "model-a"}
        bad = inference.call("check_models", {"settings": {**draft, "price_reply": "-2"}})
        assert bad["ok"] is False and bad["problems"][0]["field"] == "price_reply"
        assert inference.call("check_models", {"settings": {**draft, "price_prompt": 3}})["ok"] is False
        with pytest.raises(ApiRefused) as refused:
            inference.call("save_models", {"settings": {**draft, "price_prompt": "1e9"}, "expected": None})
        assert refused.value.reason == "invalid_settings" and not path.exists()

        saved = inference.call("save_models", {"settings": {**draft, "price_prompt": "3", "price_reply": "",
                                                           "price_cached": None}, "expected": None})
        assert saved["settings"]["price_prompt"] == "3" and saved["settings"]["price_reply"] is None
        assert saved["prices"] == {"set": False, "route": None}
        assert "price_reply" not in json.loads(path.read_text()) and json.loads(path.read_text())["price_prompt"] == "3"

        both = inference.call("save_models", {"settings": {**saved["settings"], "price_reply": "15"},
                                              "expected": saved["etag"]})
        assert both["prices"] == {"set": True, "route": f"local@127.0.0.1:{server.server_address[1]}/model-a"}
        stale = pytest.raises(ApiRefused, inference.call, "save_models",
                              {"settings": both["settings"], "expected": saved["etag"]})
        assert stale.value.reason == "changed"
        assert server.requests == []  # setting a price sends nothing


# -- the arithmetic ----------------------------------------------------------------------------------------

def test_a_cost_is_tokens_times_the_price_per_million_exactly():
    view = cost_view([_attempt(120, 9, cached=0)], TABLE)
    assert view == {"cost": "0.000495", "cost_state": "priced", "cost_lower_bound": False, "unpriced_attempts": 0}
    assert cost_view([_attempt(1_000_000, 1_000_000, cached=0)], TABLE)["cost"] == "18.00"
    assert cost_view([_attempt(0, 0, cached=0)], TABLE)["cost"] == "0.00"
    assert cost_view([], TABLE) == {"cost": "0.00", "cost_state": "priced", "cost_lower_bound": False,
                                    "unpriced_attempts": 0}


def test_cached_prompt_tokens_are_charged_at_their_own_price_when_one_is_set():
    cached = PriceTable({ROUTE: Price(Decimal("3"), Decimal("15"), Decimal("0.3"))})
    assert cost_view([_attempt(120, 9, cached=20)], cached)["cost"] == "0.000441"  # 100 at 3, 20 at 0.3, 9 at 15
    assert cost_view([_attempt(120, 9, cached=20)], TABLE)["cost"] == "0.000495"  # no cached price: the prompt's
    # how much was cached is unknown: charged at the cheaper rate, and said to be a floor
    unknown = cost_view([_attempt(120, 9)], cached)
    assert (unknown["cost"], unknown["cost_state"], unknown["cost_lower_bound"]) == ("0.000171", "partial", True)
    assert cost_view([_attempt(120, 9)], TABLE)["cost_lower_bound"] is False  # one rate: nothing is unknown


def test_large_counts_and_fine_prices_lose_nothing_and_round_to_a_millionth():
    fine = PriceTable({ROUTE: Price(Decimal("999999.999999"), Decimal("0.000001"))})
    big = cost_view([_attempt(10**18 + 7, 3, cached=0)], fine)
    # exactly 999999999999000006.999999999996: thirty digits, more than a default decimal context carries
    assert big["cost"] == "999999999999000007.00"
    assert cost_view([_attempt(10**12, 10**12, cached=0)], fine)["cost"] == "1000000000000.00"
    assert cost_view([_attempt(0, 1, cached=0)], fine)["cost"] == "0.00"  # 10^-12 is under a millionth
    assert cost_text(Decimal("0.0000005")) == "0.00" and cost_text(Decimal("0.0000015")) == "0.000002"  # ties to even
    assert cost_text(Decimal("0.0000019"), lower=True) == "0.000001"  # a floor is rounded down, so it stays one
    assert cost_text(Decimal("1.5")) == "1.50" and cost_text(Decimal("12")) == "12.00"
    assert cost_text(Decimal("0.1234567")) == "0.123457"


def test_without_both_prices_every_cost_is_unknown_never_zero():
    assert cost_view([_attempt(120, 9)], None) == {"cost": None, "cost_state": "unpriced", "cost_lower_bound": False,
                                                   "unpriced_attempts": 0}
    assert _totals([_attempt(120, 9)], 0, 0)["cost"] is None


def test_an_unreported_count_or_an_unbooked_call_makes_the_cost_a_floor():
    missing = cost_view([_attempt(None, 9), _attempt(100, 1, cached=0, ordinal=2)], TABLE)
    assert (missing["cost"], missing["cost_state"], missing["cost_lower_bound"]) == ("0.00045", "partial", True)
    lost = _totals([_attempt(100, 1, cached=0)], 1, 0, TABLE)  # a call sent with nothing booked
    assert (lost["cost"], lost["cost_state"], lost["cost_lower_bound"]) == ("0.000315", "partial", True)
    unsent = _totals([_attempt(100, 1, cached=0)], 1, 1, TABLE)  # never sent: it used nothing
    assert (unsent["cost_state"], unsent["cost_lower_bound"]) == ("priced", False)
    never = cost_view([_attempt(0, 0, outcome="not_sent", measurement=Measurement.ESTIMATED)], TABLE)
    assert (never["cost"], never["cost_state"]) == ("0.00", "priced")


def test_counts_the_provider_did_not_report_itself_make_the_cost_estimated():
    view = cost_view([_attempt(120, 9, cached=0, measurement=Measurement.SYNTHETIC)], TABLE)
    assert (view["cost"], view["cost_state"], view["cost_lower_bound"]) == ("0.000495", "estimated", False)
    floor = cost_view([_attempt(None, 9, measurement=Measurement.ESTIMATED)], TABLE)
    assert (floor["cost_state"], floor["cost_lower_bound"]) == ("estimated", True)


def test_attempts_on_a_model_with_no_price_are_left_out_and_said_to_be():
    other = _attempt(500, 50, cached=0, route="p@x.example/fallback", ordinal=2)
    mixed = cost_view([_attempt(120, 9, cached=0), other], TABLE)
    assert mixed == {"cost": "0.000495", "cost_state": "partial", "cost_lower_bound": True, "unpriced_attempts": 1}
    assert cost_view([other], TABLE) == {"cost": None, "cost_state": "unpriced", "cost_lower_bound": False,
                                         "unpriced_attempts": 1}


# -- through a played turn ---------------------------------------------------------------------------------

def test_a_chats_cost_is_worked_out_from_its_booked_tokens_at_the_prices_set_now(desk, tmp_path):
    with sse_server(stream(sse_lines(deltas=("The tide keeps its own ledger.",), usage=USAGE))) as server, \
            Device(desk, runtime=False) as device:
        inference = InferenceApi(device.api, SettingsFile(tmp_path / "model.json"), owner=OWNER, environ={})
        draft = {"provider_id": "local", "base_url": base_url(server), "model": "model-a"}
        saved = inference.call("save_models", {"settings": draft, "expected": None})
        device("continue", chat=CHAT)
        device("post", chat=CHAT, text="Who keeps the ledger?")
        device("generate", chat=CHAT, speakers=[str(MARA)])
        before = inference.call("usage", {"chat": CHAT})
        assert (before["total"]["cost"], before["total"]["cost_state"]) == (None, "unpriced")
        assert before["prices"] == {"set": False, "route": None}

        priced = inference.call("save_models", {"settings": {**saved["settings"], "price_prompt": "3",
                                                             "price_reply": "15"}, "expected": saved["etag"]})
        usage = inference.call("usage", {"chat": CHAT})  # the turn was played before any price was set
        assert usage["prices"]["set"] is True
        assert (usage["total"]["cost"], usage["total"]["cost_state"], usage["total"]["cost_lower_bound"]) == (
            "0.000495", "priced", False)
        (attempt,) = [a for turn in usage["turns"] for call in turn["calls"] for a in call.get("attempts", ())]
        assert (attempt["cost"], attempt["cost_state"]) == ("0.000495", "priced")
        assert usage["by_stage"][0]["cost"] == "0.000495"
        (role,) = [role for trace in inference.call("diagnostics")["traces"] for role in trace["calls"]]
        assert (role["cost"], role["cost_state"]) == ("0.000495", "priced")

        # changing a price re-prices what was already played: no record holds a cost
        inference.call("save_models", {"settings": {**priced["settings"], "price_cached": "0.3"},
                                       "expected": priced["etag"]})
        assert inference.call("usage", {"chat": CHAT})["total"]["cost"] == "0.000441"
        assert len(server.requests) == 1  # only the turn's own call was ever sent
