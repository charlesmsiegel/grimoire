"""Every ledger row says what served it (slice E, Task 2; spec 9.1-9.3).

A row used to say which adapter kind ran (`provider`) and the connection's
display name. Neither is enough to price a call from the rates a user stated
on a provider, nor to say whether it billed a subscription. So the resolver
stamps an account (`wire.Account`) onto each attempt's target -- `operation`,
`role`, `billing` -- and `llm._stamp` files it, with the provider's id, the
sampler preset actually sent and the model the call asked for, into the usage
holder `store.usage.Meter` writes.

An account is never mutated in place: a target is frozen, and a stamp laid on
one (`wire.Target.with_account`) makes a new one and leaves the resolution's
as it was.

Invented provider names, fake keys and `vendor/model-*` models only.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from copy import deepcopy

import pytest

import grimoire.store as store
from grimoire import llm, llm_usage, wire
from grimoire.llm import LLMClient
from grimoire.routes.common import require_inference
from grimoire.store import inference_keys as keys
from grimoire.store import llm_connections, usage
from grimoire.store.inference import resolve
from grimoire.store.inference.cascade import Selection
from tests.llm_fakes import RefusingProvider, ScriptedProvider

MODEL_A = "vendor/model-a"
MODEL_B = "vendor/model-b"
NEW_FIELDS = ("operation", "provider_id", "requested_model", "role", "preset",
              "billing", "decision_mode", "tokens_estimated")


@pytest.fixture(autouse=True)
def _instant_backoff(monkeypatch):
    monkeypatch.setattr(llm, "RETRY_BASE", 0.0)


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


def _openai(name: str = "Saltmarch") -> str:
    """An OpenAI-preset provider: metered."""
    return llm_connections.create_connection(
        "openai_compatible", name, base_url="https://api.openai.com/v1",
        api_key="sk-fake-openai")


def _coding_plan(name: str = "Winifred") -> str:
    """A z.ai Coding Plan provider: subscription-billed."""
    return llm_connections.create_connection(
        "openai_compatible", name, base_url="https://api.z.ai/api/coding/paas/v4",
        api_key="sk-fake-zai")


def _format2(**fields: str) -> None:
    store.write_config(**{keys.FORMAT_KEY: "2", **fields})
    assert keys.is_current(store.read_config())


@pytest.fixture
def primary(home) -> str:
    """Primary: an OpenAI-preset provider on `vendor/model-a`, preset `p1`."""
    pid = _openai()
    assert store.sampler_presets.create_preset("p1", {"temperature": 0.5}) == "p1"
    _format2(role_primary_provider=pid, role_primary_model=MODEL_A,
             role_primary_preset="p1")
    return pid


@pytest.fixture
def with_fallback(primary) -> tuple[str, str]:
    """`primary`, plus a z.ai Coding Plan fallback on `vendor/model-b`."""
    fb = _coding_plan()
    store.write_config(role_primary_fallback_provider=fb,
                       role_primary_fallback_model=MODEL_B)
    return primary, fb


def _filed(conn: wire.Chain | wire.Target, provider, task: str = "chat") -> dict:
    """The ledger row one call through a REAL facade files."""
    client = LLMClient(openai_compatible=provider)

    async def go() -> None:
        with usage.meter(task) as m:
            await client.complete([{"role": "user", "content": "hello"}], conn,
                                  usage=m.usage)
    asyncio.run(go())
    rows = list(usage.calls(days=1))
    assert len(rows) == 1, rows
    return rows[0]


def _ledger_lines(home) -> list[dict]:
    return [json.loads(line) for p in sorted((home / "usage").glob("*.jsonl"))
            for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---- the row ----
def test_record_writes_the_new_fields_only_when_set(home):
    row = usage.record(task="chat", model=MODEL_A)
    assert row is not None
    assert not set(NEW_FIELDS) & set(row)
    assert not set(NEW_FIELDS) & set(_ledger_lines(home)[0])

    full = usage.record(task="chat", model=MODEL_A, operation="generate",
                        provider_id="saltmarch", requested_model=MODEL_B, role="primary",
                        preset="p1", billing="metered", decision_mode="native",
                        tokens_estimated=True)
    assert full is not None
    assert {k: full[k] for k in NEW_FIELDS} == {
        "operation": "generate", "provider_id": "saltmarch", "requested_model": MODEL_B,
        "role": "primary", "preset": "p1", "billing": "metered",
        "decision_mode": "native", "tokens_estimated": True}


def test_requested_model_is_written_only_when_it_differs(home):
    same = usage.record(task="chat", model=MODEL_A, requested_model=MODEL_A)
    assert same is not None and "requested_model" not in same
    other = usage.record(task="chat", model="vendor/model-a-2026-08",
                         requested_model=MODEL_A)
    assert other is not None and other["requested_model"] == MODEL_A


def test_meter_done_survives_a_malformed_holder(home):
    m = usage.Meter("chat")
    m.usage.update({"model": MODEL_A, "attempts": 1, "provider_id": object(),
                    "billing": 3, "role": None})
    row = m.done()
    assert row is not None
    assert row["model"] == MODEL_A
    assert not {"provider_id", "billing", "role"} & set(row)
    assert len(_ledger_lines(home)) == 1


def test_meter_done_files_the_row_when_gathering_raises(home):
    """I3: a holder whose new reads raise costs the new fields, not the row."""

    class Hostile(dict):
        def get(self, key, default=None):
            if key in NEW_FIELDS:
                raise RuntimeError("no")
            return super().get(key, default)

    m = usage.Meter("chat")
    m.usage = Hostile({"model": MODEL_A, "attempts": 1})
    row = m.done()
    assert row is not None and row["model"] == MODEL_A
    assert not set(NEW_FIELDS) & set(row)


# ---- through the resolver and the real facade ----
def test_a_resolved_call_files_operation_role_provider_preset_and_billing(primary):
    row = _filed(require_inference("chat", "").chain, ScriptedProvider(["hi"]))
    assert row["operation"] == "generate"
    assert row["role"] == "primary"
    assert row["provider_id"] == primary
    assert row["preset"] == "p1"
    assert row["billing"] == "metered"
    # `provider` keeps its meaning: the adapter kind (ruling 1).
    assert row["provider"] == "openai_compatible"
    assert "requested_model" not in row


def test_the_fallback_row_names_the_fallbacks_provider_and_billing(with_fallback):
    _primary, fb = with_fallback
    refusing = RefusingProvider(failing={MODEL_A})
    row = _filed(require_inference("chat", "").chain, refusing)
    assert [model for model, _ in refusing.calls] == [MODEL_A, MODEL_B]
    assert row["provider_id"] == fb
    assert row["billing"] == "subscription"
    assert row["attempts"] == 2
    assert row["model"] == MODEL_B
    # The fallback came from the Primary role's slot (ruling 9).
    assert row["role"] == "primary"
    assert row["operation"] == "generate"


def test_a_fallback_row_files_the_route_preset_it_was_sent(with_fallback):
    assert store.sampler_presets.create_preset("p2", {"temperature": 0.7}) == "p2"
    store.write_config(**{keys.preset_key("scene"): "p2"})
    resolved = require_inference("chat", "")
    assert resolved.chain.fallback.sampling.preset_id == "p2"
    row = _filed(resolved.chain, RefusingProvider(failing={MODEL_A}))
    assert row["model"] == MODEL_B
    assert row["preset"] == "p2"


def test_a_dated_snapshot_reply_keeps_the_requested_model(primary):
    row = _filed(require_inference("chat", "").chain,
                 ScriptedProvider(["hi"], usage={"model": "vendor/model-a-2026-08"}))
    assert row["model"] == "vendor/model-a-2026-08"
    assert row["requested_model"] == MODEL_A


def test_a_pinned_route_files_no_role(home):
    pid = _openai()
    _format2(role_primary_provider=pid, role_primary_model=MODEL_A,
             **{keys.use_key("scene"): keys.PIN,
                keys.pin_key("scene", "provider"): pid,
                keys.pin_key("scene", "model"): MODEL_B})
    resolved = require_inference("chat", "")
    assert resolved.via == "route"
    row = _filed(resolved.chain, ScriptedProvider(["hi"]))
    assert row["model"] == MODEL_B
    assert row["provider_id"] == pid
    assert row["operation"] == "generate"
    assert "role" not in row


def test_an_override_that_changes_the_model_files_no_role(primary):
    resolved = resolve.resolve("chat", "", override=Selection(primary, MODEL_B, ""))
    assert resolved.attempts[0].target.model == MODEL_B
    row = _filed(resolved.attempts[0].target, ScriptedProvider(["hi"]))
    assert row["model"] == MODEL_B
    assert row["operation"] == "generate"
    assert "role" not in row


def test_an_override_that_changes_the_provider_files_no_role(primary):
    other = _openai("Mara")
    resolved = resolve.resolve("chat", "", override=Selection(other, "", ""))
    assert resolved.attempts[0].target.provider_id == other
    assert resolved.attempts[0].target.account.role == ""


def test_a_preset_only_override_keeps_the_role(primary):
    assert store.sampler_presets.create_preset("p3", {"temperature": 0.1}) == "p3"
    resolved = resolve.resolve("chat", "", override=Selection("", "", "p3"))
    assert resolved.attempts[0].target.sampling.preset_id == "p3"
    row = _filed(resolved.attempts[0].target, ScriptedProvider(["hi"]))
    assert row["role"] == "primary"
    assert row["preset"] == "p3"


def test_an_override_naming_the_standing_selection_keeps_the_role(primary):
    resolved = resolve.resolve("chat", "", override=Selection(primary, MODEL_A, ""))
    assert resolved.attempts[0].target.account.role == "primary"


def test_a_reroll_naming_the_claude_default_keeps_the_role(home):
    """A Claude selection with no model runs the default, so an override
    naming that default has not left the role's selection -- compared on the
    effective model, as `override_inference` compares it."""
    pid = llm_connections.create_connection("claude", "Seraphine")
    _format2(role_primary_provider=pid, role_primary_model="")
    same = resolve.resolve("chat", "",
                           override=Selection(pid, llm.CLAUDE_DEFAULT_MODEL, ""))
    assert same.attempts[0].target.account.role == "primary"
    other = resolve.resolve("chat", "", override=Selection(pid, "sonnet", ""))
    assert other.attempts[0].target.model == "sonnet"
    assert other.attempts[0].target.account.role == ""


def test_a_target_carries_billing_without_a_resolution(home):
    raw = {"id": "realm", "kind": "openai_compatible", "name": "Realm",
           "base_url": "https://api.openai.com/v1", "model": MODEL_A}
    assert resolve.provider_target(raw).account == wire.Account(billing="metered")
    plan = {**raw, "base_url": "https://api.z.ai/api/coding/paas/v4"}
    assert resolve.provider_target(plan).account == wire.Account(billing="subscription")


def test_a_decision_mode_in_the_account_is_filed(primary):
    chain = require_inference("chat", "").chain
    assert chain is not None
    row = _filed(chain.with_account(decision_mode="structured"), ScriptedProvider(["hi"]))
    assert row["decision_mode"] == "structured"
    assert row["role"] == "primary"


def test_accounts_are_never_mutated_in_place(with_fallback):
    resolved = resolve.resolve("chat", "")
    first, second = resolved.attempts
    # 1. The two attempts' accounts are distinct objects, and the riding
    # fallback is the stamped one.
    assert first.target.account is not second.target.account
    assert resolved.chain.fallback is second.target
    assert second.target.account == wire.Account(
        billing="subscription", operation="generate", role="primary")

    # 2. A stamp makes a new target, and leaves the resolution's as it was.
    target = first.target
    before = target.account
    stamped = target.with_account(decision_mode="native")
    assert target.account is before and target.account.decision_mode == ""
    assert stamped is not target
    assert stamped.account == wire.Account(**{**dataclasses.asdict(before),
                                              "decision_mode": "native"})

    # 3. `_stamp` and `account` read the target they are handed and write
    # nothing to it (a target is frozen).
    snapshot = deepcopy(target)
    holder: dict = {}
    llm._stamp(holder, target, 1)
    llm_usage.account(holder, target)
    assert target == snapshot
    assert holder["operation"] == "generate"
    holder = {}
    llm._stamp(holder, stamped, 1)
    assert holder["decision_mode"] == "native"


def test_account_never_raises_and_copies_only_strings():
    holder: dict = {}
    odd = wire.Target(provider_id=3, kind="openrouter", model="m",  # type: ignore[arg-type]
                      account=wire.Account(role=None, billing="",  # type: ignore[arg-type]
                                           operation="generate"))
    llm_usage.account(holder, odd)
    assert holder == {"operation": "generate"}
    llm_usage.account(holder, "not a target")  # type: ignore[arg-type]
    llm_usage.account(None, wire.Target(provider_id="realm", kind="openrouter", model="m"))


def test_the_account_fields_are_the_ledgers():
    assert llm_usage.ACCOUNT_FIELDS == ("operation", "role", "billing", "decision_mode",
                                        "hop")
    fields = tuple(f.name for f in dataclasses.fields(wire.Account))
    assert fields == llm_usage.ACCOUNT_FIELDS


# ---- the escalation hop's mark (roadmap 01d-S3) ----

def test_a_hop_is_filed_only_when_set(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    row = usage.record(task="scene-break", model="vendor/active", hop="escalation")
    assert row is not None and row["hop"] == "escalation"
    row = usage.record(task="scene-break", model="vendor/active")
    assert row is not None and "hop" not in row


def test_a_meter_files_the_hop_its_target_carried(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    target = wire.Target(provider_id="openrouter", kind="openrouter", model="vendor/active")
    for account, hop in (({"hop": "escalation", "operation": "decide"}, "escalation"),
                         ({"operation": "decide"}, None)):
        with usage.meter("scene-break") as m:
            llm._stamp(m.usage, target.with_account(**account), 1)
        assert m.row is not None
        assert m.row.get("hop") == hop


def test_every_account_field_reaches_the_row():
    """An account field the facade stamps but `Meter.done` does not file would
    be dropped silently: each is in `Meter.SERVED` and a `record` parameter."""
    import inspect
    assert set(llm_usage.ACCOUNT_FIELDS) <= set(usage.Meter.SERVED)
    params = inspect.signature(usage.record).parameters
    assert all(field in params for field in llm_usage.ACCOUNT_FIELDS)


# ---- run attribution (01g-S3; spec 01g §3.10) ----
RUN_FIELDS = ("run_id", "loop_turn", "tool_calls")


def test_record_writes_the_run_fields_only_when_set(home):
    plain = usage.record(task="chat", model=MODEL_A)
    assert plain is not None and not set(RUN_FIELDS) & set(plain)
    assert not set(RUN_FIELDS) & set(_ledger_lines(home)[0])

    usage.record(task="chat", model=MODEL_A, run_id="run-1", loop_turn=2, tool_calls=3)
    row = list(usage.calls(days=1))[-1]
    assert {k: row[k] for k in RUN_FIELDS} == {"run_id": "run-1", "loop_turn": 2,
                                               "tool_calls": 3}


@pytest.mark.parametrize("field, value", [
    ("run_id", ""), ("run_id", object()), ("run_id", 7),
    ("loop_turn", 0), ("loop_turn", -1), ("loop_turn", True), ("loop_turn", "2"),
    ("tool_calls", 0), ("tool_calls", -1), ("tool_calls", False), ("tool_calls", 1.5),
])
def test_run_fields_are_type_tested_not_coerced(home, field, value):
    row = usage.record(task="chat", model=MODEL_A, **{field: value})
    assert row is not None, "a bad run field costs the field, never the row"
    assert field not in row
    assert len(_ledger_lines(home)) == 1


def test_an_older_row_reads_unchanged(home):
    old = {"ts": usage._now(), "kind": "llm", "task": "chat", "model": MODEL_A,
           "prompt_tokens": 10, "completion_tokens": 2, "duration_ms": 5, "status": "ok"}
    path = usage.month_path(old["ts"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(old) + "\n", encoding="utf-8")
    assert list(usage.calls(days=1)) == [old]

    plain = usage.summary(days=1)["totals"]
    usage.record(task="chat", model=MODEL_A, prompt_tokens=10, completion_tokens=2,
                 duration_ms=5, run_id="run-1", loop_turn=1, tool_calls=2)
    rows = list(usage.calls(days=1))
    assert usage._turn(rows[0]).keys() == usage._turn(rows[1]).keys()
    assert not set(RUN_FIELDS) & set(usage._turn(rows[1]))
    doubled = usage.summary(days=1)["totals"]
    assert doubled["calls"] == 2 * plain["calls"]
    assert doubled["total_tokens"] == 2 * plain["total_tokens"]


def test_a_meter_files_its_run_fields(home):
    with usage.meter("chat", run_id="run-1", loop_turn=1) as m:
        assert m.usage == {usage.RUN_KEY: "run-1"}
        m.usage.update(model=MODEL_A, attempts=1)
        m.tool_calls = 2
    assert m.row is not None
    assert {k: m.row[k] for k in RUN_FIELDS} == {"run_id": "run-1", "loop_turn": 1,
                                                 "tool_calls": 2}
    # The seeded holder key is the capture's, never a ledger field.
    assert usage.RUN_KEY not in m.row
    assert all(usage.RUN_KEY not in line for line in _ledger_lines(home))


def test_a_run_row_folds_into_the_rollup_as_any_row(home):
    from grimoire.store import usage_rollup

    usage.record(task="chat", campaign="saltmarch", model=MODEL_A, cost_usd=1.0)
    usage.record(task="chat", campaign="saltmarch", model=MODEL_A, cost_usd=2.0,
                 run_id="run-1", loop_turn=1, tool_calls=1)
    totals = usage_rollup.campaign_totals("saltmarch")
    assert totals["cost_usd"] == 3.0
    assert not set(RUN_FIELDS) & set(totals)


def test_meter_takes_tool_calls_as_a_keyword(home):
    with usage.meter("chat", tool_calls=4) as m:
        m.usage.update(model=MODEL_A, attempts=1)
    assert m.row is not None and m.row["tool_calls"] == 4
    assert "run_id" not in m.row and m.usage.get(usage.RUN_KEY) is None


def test_a_non_string_run_id_seeds_nothing(home):
    m = usage.Meter("chat", run_id=object())  # type: ignore[arg-type]
    assert m.usage == {}
    m.usage.update(model=MODEL_A, attempts=1)
    row = m.done()
    assert row is not None and "run_id" not in row


def test_a_run_meter_that_sent_nothing_files_no_row(home):
    with usage.meter("chat", run_id="run-1") as clean:
        pass
    assert clean.row is None
    with pytest.raises(llm.LLMError), usage.meter("chat", run_id="run-1") as failed:
        raise llm.LLMError("missing_key", "no key for Saltmarch")
    assert failed.row is None
    assert _ledger_lines(home) == []
    assert [r["kind"] for r in store.errors.summary()["rows"]] == ["missing_key"]


def test_sent_ignores_only_the_pre_send_keys():
    from grimoire import llm_reasoning

    assert usage.sent({}) is False
    assert usage.sent({usage.RUN_KEY: "run-1"}) is False
    assert usage.sent({usage.RUN_KEY: "run-1",
                       llm_reasoning.KEY: llm_reasoning.Buffer()}) is False
    assert usage.sent({usage.RUN_KEY: "run-1", "model": MODEL_A}) is True
    assert usage.sent({"model": MODEL_A}) is True
    assert usage.sent(None) is False
