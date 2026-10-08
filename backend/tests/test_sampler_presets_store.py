"""Sampler presets: the store, the resolution cascade, and the ST import."""

import json

import pytest

from grimoire import store
from grimoire.store import sampler_presets as sp

CLEAR = sp.PRESET_CLEAR


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


# ---- store ----

def test_create_read_list_update_delete(tmp_path):
    pid = sp.create_preset("Warm Prose", {"temperature": 1.1, "min_p": 0.05}, notes="n")
    assert pid == "warm-prose"
    on_disk = json.loads((tmp_path / "sampler_presets" / "warm-prose.json").read_text())
    assert on_disk == {"name": "Warm Prose", "params": {"temperature": 1.1, "min_p": 0.05},
                       "notes": "n", "source": ""}
    assert sp.read_preset(pid)["params"] == {"temperature": 1.1, "min_p": 0.05}
    sp.update_preset(pid, "Warm Prose", {"top_k": 40})
    assert sp.read_preset(pid)["params"] == {"top_k": 40}
    assert [p["id"] for p in sp.list_presets()] == ["warm-prose"]
    sp.delete_preset(pid)
    assert sp.read_preset(pid) is None and sp.list_presets() == []


def test_ids_are_uniquified():
    assert sp.create_preset("Cold") == "cold"
    assert sp.create_preset("cold") == "cold-2"


def test_update_keeps_the_source():
    pid = sp.create_preset("Imported", {}, source="sillytavern")
    sp.update_preset(pid, "Renamed", {"temperature": 0.5})
    got = sp.read_preset(pid)
    assert got["source"] == "sillytavern" and got["name"] == "Renamed"


@pytest.mark.parametrize("name, params", [("", {}), ("  ", {}), ("x" * 121, {}),
                                          ("ok", {"temperature": 9}), ("ok", {"bogus": 1})])
def test_bad_fields_are_refused(name, params):
    with pytest.raises(ValueError):
        sp.create_preset(name, params)


def test_missing_and_unsafe_ids_read_as_none():
    assert sp.read_preset("nope") is None
    assert sp.read_preset("../config") is None
    with pytest.raises(sp.PresetNotFoundError):
        sp.update_preset("nope", "x", {})
    with pytest.raises(sp.PresetNotFoundError):
        sp.delete_preset("../config")


def test_a_malformed_file_reads_as_missing(tmp_path):
    d = tmp_path / "sampler_presets"
    d.mkdir()
    (d / "broken.json").write_text("{not json", encoding="utf-8")
    (d / "array.json").write_text("[1, 2]", encoding="utf-8")
    assert sp.read_preset("broken") is None and sp.read_preset("array") is None
    assert sp.list_presets() == []


def test_a_bad_stored_value_survives_the_read_for_split_to_report(tmp_path):
    d = tmp_path / "sampler_presets"
    d.mkdir()
    (d / "hand.json").write_text(json.dumps(
        {"name": "Hand", "params": {"temperature": "hot", "top_k": 40, "junk": 1}}))
    assert sp.read_preset("hand")["params"] == {"temperature": "hot", "top_k": 40}


def test_delete_clears_global_route_keys_and_nothing_else():
    pid = sp.create_preset("Cold")
    store.write_config(preset_absorb=pid, preset_scene="other")
    cid = store.llm_connections.create_connection("openrouter", "OR", sampler_preset=pid)
    rev = store.llm_connections.read_connection_raw(cid)["rev"]
    sp.delete_preset(pid)
    cfg = store.read_config()
    assert cfg["preset_absorb"] == "" and cfg["preset_scene"] == "other"
    # The connection is NOT rewritten: a new rev would empty its model catalog.
    conn = store.llm_connections.read_connection_raw(cid)
    assert conn["sampler_preset"] == pid and conn["rev"] == rev


# ---- the clear sentinel ----

def test_the_clear_sentinel_survives_a_config_round_trip():
    store.write_config(preset_absorb=CLEAR)
    assert store.read_config()["preset_absorb"] == CLEAR


# ---- SillyTavern import ----

CHAT_COMPLETION = {
    "temperature": 1.05, "frequency_penalty": 0, "presence_penalty": 0.15, "top_p": 0.95,
    "top_k": 0, "top_a": 0, "min_p": 0.05, "repetition_penalty": 1,
    "openai_max_context": 32000, "openai_max_tokens": 300, "wrap_in_quotes": False,
    "names_behavior": 0, "prompts": [], "prompt_order": [], "stream_openai": True,
}

TEXT_COMPLETION = {
    "temp": 0.8, "temperature_last": True, "top_p": 1, "top_k": 40, "typical_p": 1,
    "min_p": 0.1, "rep_pen": 1.1, "rep_pen_range": 0, "freq_pen": 0, "presence_pen": 0,
    "mirostat_mode": 0, "sampler_order": [6, 0, 1], "genamt": 512,
    "custom_stopping_strings": '["\\n###", "{{user}}:"]',
}


def test_a_chat_completion_preset_maps_its_samplers():
    params, report = sp.from_sillytavern(CHAT_COMPLETION)
    assert params == {"temperature": 1.05, "top_p": 0.95, "min_p": 0.05,
                      "presence_penalty": 0.15}
    assert {m["param"] for m in report["neutral"]} == {
        "frequency_penalty", "top_k", "repetition_penalty"}
    assert report["skipped"][0]["param"] == "max_tokens"
    assert "openai_max_context" in report["unmapped"] and "top_a" in report["unmapped"]
    assert "openai_max_tokens" not in report["unmapped"]


def test_max_tokens_is_imported_only_when_asked():
    params, report = sp.from_sillytavern(CHAT_COMPLETION, include_max_tokens=True)
    assert params["max_tokens"] == 300 and report["skipped"] == []


def test_a_text_completion_preset_maps_the_short_spellings():
    params, report = sp.from_sillytavern(TEXT_COMPLETION)
    assert params == {"temperature": 0.8, "top_k": 40, "min_p": 0.1,
                      "repetition_penalty": 1.1, "stop": ["\n###"]}
    assert any("{{user}}" in i["why"] for i in report["invalid"])
    # Sampler order changes what min-p means; it is listed first and noted.
    assert report["unmapped"][:2] == ["temperature_last", "sampler_order"]
    assert any("order" in n for n in report["notes"])


def test_out_of_range_values_are_reported_not_stored():
    params, report = sp.from_sillytavern({"temperature": 12, "top_k": 40})
    assert params == {"top_k": 40}
    assert report["invalid"][0]["key"] == "temperature"


def test_a_file_that_maps_nothing_says_so():
    params, report = sp.from_sillytavern({"wrap_in_quotes": True})
    assert params == {} and report["unmapped"] == ["wrap_in_quotes"]
    assert any("saved empty" in n for n in report["notes"])


def test_a_non_object_is_refused():
    with pytest.raises(ValueError):
        sp.from_sillytavern([1, 2])


def test_a_sampler_only_connection_edit_keeps_the_rev_and_catalog():
    cid = store.llm_connections.create_connection("openrouter", "OR", api_key="k")
    rev = store.llm_connections.read_connection_raw(cid)["rev"]
    store.llm_connections.set_cached_models(cid, [{"id": "m", "name": "m", "context": None,
                                                   "prompt": None, "completion": None}], rev)
    store.llm_connections.update_connection(cid, sampler_preset="warm", sampler_support="")
    conn = store.llm_connections.read_connection(cid)
    assert conn["rev"] == rev and conn["sampler_preset"] == "warm"
    assert [m["id"] for m in conn["models"]] == ["m"]


def test_any_other_edit_still_mints_a_rev():
    cid = store.llm_connections.create_connection("openrouter", "OR", api_key="k")
    rev = store.llm_connections.read_connection_raw(cid)["rev"]
    store.llm_connections.update_connection(cid, sampler_preset="warm", model="x")
    assert store.llm_connections.read_connection_raw(cid)["rev"] != rev



def test_a_second_spelling_of_a_taken_param_is_listed_not_lost():
    params, report = sp.from_sillytavern({"temperature": 0.7, "temp": 1.2})
    assert params == {"temperature": 0.7}
    assert any(u.startswith("temp (unused: temperature") for u in report["unmapped"])


def test_top_k_minus_one_is_off_too():
    params, report = sp.from_sillytavern({"top_k": -1})
    assert params == {} and report["neutral"][0]["value"] == -1


def test_a_macro_only_stop_list_is_invalid_and_not_also_neutral():
    params, report = sp.from_sillytavern({"stopping_strings": ["{{user}}:", "{{char}}:"]})
    assert params == {}
    assert len(report["invalid"]) == 2 and report["neutral"] == []


def test_an_empty_stop_list_is_neutral():
    _, report = sp.from_sillytavern({"stopping_strings": []})
    assert report["neutral"][0]["param"] == "stop"


def test_deleting_an_overlong_id_is_not_found_not_an_oserror():
    sp.create_preset("Warm")   # the directory exists, so the stat really runs
    with pytest.raises(sp.PresetNotFoundError):
        sp.delete_preset("a" * 1000)


@pytest.mark.parametrize("body", ["[1, 2]", "3", '"text"', '{"rev": "x"}', '{"models": null}'])
def test_a_mangled_catalog_sidecar_reads_as_nothing_cached(tmp_path, body):
    cid = store.llm_connections.create_connection("openrouter", "OR", api_key="k")
    (tmp_path / "llm_connections" / f"{cid}.models.json").write_text(body, encoding="utf-8")
    assert store.llm_connections.cached_models(cid)["models"] == []
