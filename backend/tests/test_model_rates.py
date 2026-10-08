"""A model's own rates, stated on its provider (slice E, Task 1).

Rates are a stated fact of a model on a provider (`facts.state(rates=...)`),
and `pricing.rate_for_call` is the one function that decides which rate wins:
the provider's rates for the model first, then the user's `pricing.json`.

The property under test throughout is the Costs rule: a price nobody reported
is never rendered as zero, and a price somebody DID enter -- zero included --
is never rendered as absent. Writing is strict (a partial or unknown-field
entry is refused), reading is fail-soft (a mangled file prices nothing).

Invented ids and the codebase's placeholder names only.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import grimoire.store as store
from grimoire.store import inference_keys as keys
from grimoire.store import llm_connections, pricing
from grimoire.store.inference import facts

from . import inference_baseline as base

MODEL = "vendor/model-a"
BOTH = {"prompt_usd_per_1k": 0.001, "completion_usd_per_1k": 0.002}


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def pid(home):
    return llm_connections.create_connection("openai_compatible", "Saltmarch",
                                             base_url="http://localhost:1/v1")


@pytest.fixture
def client(tmp_path):
    with base.client_at(tmp_path) as c:
        store.write_config(**{keys.FORMAT_KEY: "2"})
        yield c


def _provider(client, name="Saltmarch") -> str:
    got = client.post("/api/llm-connections",
                      json={"name": name, "kind": "openrouter", "api_key": "sk-test"})
    assert got.status_code == 200, got.text
    return got.json()["id"]


def _facts_file(pid: str) -> Path:
    return llm_connections.facts_path(pid)


def _write_facts(pid: str, doc) -> None:
    p = _facts_file(pid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")


# ---- the store ----
def test_facts_rates_round_trip(pid):
    facts.state(pid, MODEL, rates=BOTH)
    assert facts.of(pid, MODEL, "rev")["rates"] == BOTH
    assert facts.of(pid, "vendor/other", "rev")["rates"] is None


def test_a_rates_only_write_is_not_skipped(pid):
    """`state` used to return without writing when no other field was stated."""
    assert not _facts_file(pid).exists()
    facts.state(pid, MODEL, rates=BOTH)
    assert _facts_file(pid).exists()
    assert facts.read(pid)[MODEL]["rates"] == BOTH


def test_a_partial_rate_is_refused_and_nothing_written(pid):
    facts.state(pid, MODEL, prefill=True)
    before = _facts_file(pid).read_bytes()
    with pytest.raises(ValueError, match="both an input and an output"):
        facts.state(pid, MODEL, rates={"prompt_usd_per_1k": 0.001})
    with pytest.raises(ValueError, match="non-negative"):
        facts.state(pid, MODEL, rates={**BOTH, "completion_usd_per_1k": -1})
    with pytest.raises(ValueError):
        facts.state(pid, MODEL, rates="free")
    assert _facts_file(pid).read_bytes() == before


def test_an_unknown_rate_field_is_refused(pid):
    before = _facts_file(pid).exists()
    with pytest.raises(ValueError, match="prompt_usd_per_1K"):
        facts.state(pid, MODEL, rates={"prompt_usd_per_1K": 0.001,
                                       "completion_usd_per_1k": 0.002})
    assert _facts_file(pid).exists() == before


def test_a_refused_rate_does_not_apply_the_other_fields(pid):
    """Everything is checked before the file is touched."""
    with pytest.raises(ValueError):
        facts.state(pid, MODEL, prefill=True, rates={"prompt_usd_per_1k": 1})
    assert not _facts_file(pid).exists()


def test_check_entry_orders_its_refusals():
    with pytest.raises(ValueError, match="unknown rate field"):
        pricing.check_entry({"bogus": 1, "prompt_usd_per_1k": "x"})
    with pytest.raises(ValueError, match="prompt_usd_per_1k must be a non-negative"):
        pricing.check_entry({"prompt_usd_per_1k": "x", "completion_usd_per_1k": 1})
    with pytest.raises(ValueError, match="both an input and an output"):
        pricing.check_entry({"prompt_usd_per_1k": 1})
    with pytest.raises(ValueError, match="must be an object"):
        pricing.check_entry([BOTH])
    assert pricing.check_entry({**BOTH, "cache_read_usd_per_1k": 0}) == {
        **BOTH, "cache_read_usd_per_1k": 0.0}


def test_empty_rates_clear_them(pid):
    facts.state(pid, MODEL, prefill=True, rates=BOTH)
    facts.state(pid, MODEL, rates={})
    assert facts.of(pid, MODEL, "rev")["rates"] is None
    assert "rates" not in facts.read(pid)[MODEL]
    assert facts.read(pid)[MODEL]["prefill"] is True
    # A model that held nothing else leaves no entry behind.
    facts.state(pid, "vendor/model-b", rates=BOTH)
    facts.state(pid, "vendor/model-b", rates={})
    assert "vendor/model-b" not in facts.read(pid)


def test_none_leaves_rates_as_they_are(pid):
    facts.state(pid, MODEL, rates=BOTH)
    facts.state(pid, MODEL, vision="on")
    assert facts.of(pid, MODEL, "rev")["rates"] == BOTH


def test_rates_survive_a_rev_change(pid):
    facts.state(pid, MODEL, rates=BOTH)
    assert facts.of(pid, MODEL, "rev-one")["rates"] == BOTH
    assert facts.of(pid, MODEL, "rev-two")["rates"] == BOTH


@pytest.mark.parametrize("bad", ["x", -1])
def test_a_mangled_rates_entry_reads_as_none(pid, bad):
    """A hand-written bad rate prices nothing: `None`, never `$0`."""
    _write_facts(pid, {MODEL: {"rates": {"prompt_usd_per_1k": bad,
                                         "completion_usd_per_1k": 0.002}}})
    assert facts.of(pid, MODEL, "rev")["rates"] is None
    assert MODEL not in pricing.provider_rates().get(pid, {})


def test_a_partial_hand_written_entry_reads_as_none(pid):
    _write_facts(pid, {MODEL: {"rates": {"prompt_usd_per_1k": 0.001}}})
    assert facts.of(pid, MODEL, "rev")["rates"] is None
    assert pricing.provider_rates().get(pid, {}) == {}


def test_a_non_dict_rates_entry_reads_as_none(pid):
    _write_facts(pid, {MODEL: {"rates": [1, 2]}})
    assert facts.of(pid, MODEL, "rev")["rates"] is None


# ---- the reader over every provider ----
@pytest.mark.parametrize("doc", ["{not json", "[1, 2]", {MODEL: "oops"},
                                 {MODEL: ["oops"]}, "null"])
def test_provider_rates_never_raises_on_a_mangled_file(pid, doc):
    _write_facts(pid, doc)
    assert pricing.provider_rates().get(pid, {}) == {}


def test_a_mangled_file_costs_only_its_own_provider(home):
    good = llm_connections.create_connection("openai_compatible", "Saltmarch Run",
                                             base_url="http://localhost:1/v1")
    bad = llm_connections.create_connection("openai_compatible", "Winifred",
                                            base_url="http://localhost:2/v1")
    facts.state(good, MODEL, rates=BOTH)
    _write_facts(bad, "{not json")
    got = pricing.provider_rates()
    assert got[good] == {MODEL: BOTH}
    assert got.get(bad, {}) == {}


def test_provider_rates_sees_a_rates_edit(pid):
    """Written through `facts.state`, so the writer and this reader are held to
    one schema; the memo follows the file's signature."""
    assert pid not in pricing.provider_rates()
    facts.state(pid, MODEL, rates=BOTH)
    path = _facts_file(pid)
    old = path.stat().st_mtime - 60       # outside statcache's racy window
    os.utime(path, (old, old))
    assert pricing.provider_rates()[pid] == {MODEL: BOTH}
    assert pricing.provider_rates()[pid] == {MODEL: BOTH}   # the memoized read

    other = {"prompt_usd_per_1k": 0.5, "completion_usd_per_1k": 1.5}
    facts.state(pid, MODEL, rates=other)
    assert pricing.provider_rates()[pid] == {MODEL: other}
    facts.state(pid, MODEL, rates={})
    assert pid not in pricing.provider_rates()


def test_a_stable_facts_file_costs_a_stat_not_a_parse(pid, monkeypatch):
    """The rollup fingerprint reads `provider_rates` on every navigation, so
    the memo is load-bearing: an unchanged file is parsed once, and an edited
    one again."""
    facts.state(pid, MODEL, rates=BOTH)
    path = _facts_file(pid)
    old = path.stat().st_mtime - 60       # outside statcache's racy window
    os.utime(path, (old, old))
    parsed = []
    real = pricing._read_provider_file
    monkeypatch.setattr(pricing, "_read_provider_file",
                        lambda p: parsed.append(p) or real(p))

    for _ in range(3):
        assert pricing.provider_rates()[pid] == {MODEL: BOTH}
    assert parsed == [path]

    other = {"prompt_usd_per_1k": 0.5, "completion_usd_per_1k": 1.5}
    facts.state(pid, MODEL, rates=other)
    os.utime(path, (old + 1, old + 1))
    assert pricing.provider_rates()[pid] == {MODEL: other}
    assert pricing.provider_rates()[pid] == {MODEL: other}
    assert parsed == [path, path]


def test_a_file_that_could_not_be_opened_is_not_remembered_as_empty(pid, monkeypatch):
    """A sync client or antivirus holding the file for one read is an OSError,
    not a file with no rates in it. The memo is keyed on the stat signature,
    and the holder lets go of an *unchanged* file -- so an empty answer cached
    under that signature would hide the rates until the file next changed."""
    facts.state(pid, MODEL, rates=BOTH)
    path = _facts_file(pid)
    old = path.stat().st_mtime - 60       # outside statcache's racy window
    os.utime(path, (old, old))
    real = Path.read_text
    held = [True]

    def read_text(self, *a, **kw):
        if self == path and held:
            held.clear()
            raise PermissionError(13, "held by another process", str(self))
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", read_text)
    assert pid not in pricing.provider_rates()          # held: skipped, not cached
    assert not held
    assert pricing.provider_rates()[pid] == {MODEL: BOTH}


def test_provider_rates_hands_back_a_copy(pid):
    facts.state(pid, MODEL, rates=BOTH)
    path = _facts_file(pid)
    old = path.stat().st_mtime - 60
    os.utime(path, (old, old))
    first = pricing.provider_rates()
    first[pid][MODEL]["prompt_usd_per_1k"] = 99.0
    assert pricing.provider_rates()[pid][MODEL] == BOTH


def test_a_deleted_provider_stops_contributing(pid):
    facts.state(pid, MODEL, rates=BOTH)
    assert pid in pricing.provider_rates()
    llm_connections.delete_connection(pid)
    assert pid not in pricing.provider_rates()


def test_facts_files_maps_ids_to_paths_without_reading_connections(home):
    a = llm_connections.create_connection("openai_compatible", "Seraphine",
                                          base_url="http://localhost:1/v1")
    facts.state(a, MODEL, rates=BOTH)
    (llm_connections._dir() / "bad:id.facts.json").write_text("{}", encoding="utf-8")
    got = llm_connections.facts_files()
    assert got == {a: llm_connections.facts_path(a)}


def test_facts_files_is_empty_with_no_directory(home):
    assert llm_connections.facts_files() == {}


# ---- precedence ----
def test_rate_for_call_precedence():
    r_req = {"prompt_usd_per_1k": 1.0, "completion_usd_per_1k": 1.0}
    r_model = {"prompt_usd_per_1k": 2.0, "completion_usd_per_1k": 2.0}
    t_exact = {"prompt_usd_per_1k": 3.0, "completion_usd_per_1k": 3.0}
    t_prefix = {"prompt_usd_per_1k": 4.0, "completion_usd_per_1k": 4.0}
    t_default = {"prompt_usd_per_1k": 5.0, "completion_usd_per_1k": 5.0}
    providers = {"saltmarch": {"vendor/asked": r_req, "vendor/answered": r_model}}
    table = {"vendor/answered": t_exact, "vendor/*": t_prefix, "": t_default}
    call = {"provider_id": "saltmarch", "model": "vendor/answered",
            "requested_model": "vendor/asked"}

    # 1. facts under the requested model
    assert pricing.rate_for_call(table, providers, **call) is r_req
    # 2. facts under the model
    assert pricing.rate_for_call(table, providers, **{**call, "requested_model": ""}) is r_model
    assert pricing.rate_for_call(table, providers,
                                 **{**call, "requested_model": "vendor/none"}) is r_model
    # 3. table exact
    assert pricing.rate_for_call(table, {}, **call) is t_exact
    assert pricing.rate_for_call(table, providers, **{**call, "provider_id": "other"}) is t_exact
    # 4. table prefix*
    del table["vendor/answered"]
    assert pricing.rate_for_call(table, {}, **call) is t_prefix
    # 5. table ""
    assert pricing.rate_for_call(table, {}, provider_id="saltmarch",
                                 model="elsewhere/x") is t_default
    # 6. nothing
    assert pricing.rate_for_call({}, {}, **call) is None


def test_facts_rates_take_no_wildcards():
    providers = {"saltmarch": {"vendor/*": BOTH}}
    assert pricing.rate_for_call({}, providers, provider_id="saltmarch",
                                 model="vendor/model-a") is None


def test_a_row_naming_no_provider_prices_from_the_table_alone():
    providers = {"saltmarch": {MODEL: BOTH}}
    table = {MODEL: {"prompt_usd_per_1k": 9.0, "completion_usd_per_1k": 9.0}}
    assert pricing.rate_for_call(table, providers, model=MODEL) == table[MODEL]


def test_zero_rates_are_a_price():
    zero = {"prompt_usd_per_1k": 0.0, "completion_usd_per_1k": 0.0}
    table = {MODEL: {"prompt_usd_per_1k": 9.0, "completion_usd_per_1k": 9.0}}
    got = pricing.rate_for_call(table, {"saltmarch": {MODEL: zero}},
                                provider_id="saltmarch", model=MODEL)
    assert got == zero
    assert pricing.estimate(got, prompt_tokens=10, completion_tokens=5) == 0.0


def test_a_zero_rate_survives_the_round_trip(pid):
    zero = {"prompt_usd_per_1k": 0, "completion_usd_per_1k": 0}
    facts.state(pid, MODEL, rates=zero)
    got = facts.of(pid, MODEL, "rev")["rates"]
    assert got == {"prompt_usd_per_1k": 0.0, "completion_usd_per_1k": 0.0}
    assert got is not None


def test_entry_is_public_and_keeps_both_base_rates_or_nothing():
    assert pricing.entry(BOTH) == BOTH
    assert pricing.entry({"prompt_usd_per_1k": 1}) is None
    assert pricing.entry("x") is None
    assert not hasattr(pricing, "_entry")


# ---- the API ----
def test_put_facts_rates_then_get(client):
    pid = _provider(client)
    url = f"/api/llm-connections/{pid}/facts"
    put = client.put(url, json={"model": MODEL, "rates": BOTH})
    assert put.status_code == 200, put.text
    assert put.json()["rates"] == BOTH
    got = client.get(url, params={"model": MODEL}).json()
    assert got["rates"] == BOTH
    cleared = client.put(url, json={"model": MODEL, "rates": {}}).json()
    assert cleared["rates"] is None
    assert client.get(url, params={"model": MODEL}).json()["rates"] is None


def test_put_facts_refuses_a_partial_rate(client):
    pid = _provider(client)
    url = f"/api/llm-connections/{pid}/facts"
    assert client.put(url, json={"model": MODEL, "rates": BOTH}).status_code == 200
    r = client.put(url, json={"model": MODEL, "rates": {"prompt_usd_per_1k": 0.5}})
    assert r.status_code == 400, r.text
    assert "both an input and an output" in r.json()["detail"]
    assert client.get(url, params={"model": MODEL}).json()["rates"] == BOTH


def test_put_facts_refuses_an_unknown_rate_field(client):
    pid = _provider(client)
    url = f"/api/llm-connections/{pid}/facts"
    r = client.put(url, json={"model": MODEL, "rates": {
        "prompt_usd_per_1K": 0.001, "completion_usd_per_1k": 0.002}})
    assert r.status_code == 400, r.text
    assert "prompt_usd_per_1K" in r.json()["detail"]
    assert client.get(url, params={"model": MODEL}).json()["rates"] is None
    assert not _facts_file(pid).exists()


def test_put_facts_rates_on_an_unknown_provider_is_404(client):
    r = client.put("/api/llm-connections/nope/facts", json={"model": MODEL, "rates": BOTH})
    assert r.status_code == 404
    assert not _facts_file("nope").exists()


# ---- the import graph ----
def test_pricing_imports_no_inference():
    src = Path(pricing.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base_mod = "." * node.level + (node.module or "")
            names = [base_mod] + [f"{base_mod}.{a.name}" for a in node.names]
        for name in names:
            assert "inference" not in name.split("."), name
    backend = Path(__file__).resolve().parents[1]
    done = subprocess.run([sys.executable, "-c", "import grimoire.store.usage"],
                          cwd=backend, capture_output=True, text=True, check=False,
                          env={**os.environ, "PYTHONPATH": str(backend / "src")})
    assert done.returncode == 0, done.stderr
