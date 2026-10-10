"""The decide chain, frozen at the stage level (slice I, Task 9; the 9c
review's M5).

`fixtures/decide_chain_golden.json` holds what `inference.decide` does for a
set of resolutions under a set of failures, through a REAL `LLMClient` over
recording provider clients:

- **resolutions:** a native-only primary with a fallback on another provider
  or on its own, or none and a role preset (alone and with a fallback); a
  structured primary with a structured fallback, with a native fallback, and
  with a role preset; a structured primary over a native fallback on its own
  provider; a structured primary alone;
- **failures:** none; the primary down; every key refused; the structured
  field refused; the primary timing out; the primary rate limited -- each
  for two items and for nine (two structured chunks, native items past
  `NATIVE_CONCURRENCY`).

For each run it records the stages (`inference.stages`), the decision (or
the error raised), every wire call (its method, kind, model, key and keyword
arguments; the messages by count and roles only, so a reworded prompt
template is not a wire change), the health observer's calls, the ledger rows
(less what differs between any two runs) and the prompt-log captures,
sampler report included.

It was recorded from `670a543`, and this test passes unchanged against the
pre-target code (`501fcec`, where `decide` still handed the facade dicts):
the 9c review found the two decide paths identical, and so does this.

It is stored packed (`_encode`): each distinct run once, by a digest, which
each run's key names, and repeated rows and captures counted.
It pins the mode stamp, the native strip, the stops, the fallback stage and
the capture while Task 9d and Task 10 move the facade and delete the
lowering.

**It is never regenerated to make a change pass.** A change that moves a
stage, a wire call, a row or a capture here is argued for on its own. It has
been re-recorded once, by roadmap 01b-S1 (spec
`2026-10-09-roadmap-01b-decision-capture-design.md` §3.2): every capture's
outcome gained `stage` and `at`, and nothing else in any run moved.
`test_record_the_decide_chain_golden` wrote it once: it writes only where no
file exists, and only when `GRIMOIRE_RECORD_DECIDE_GOLDEN=1` asks it to.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import importlib
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import decisions, inference, llm, llm_sampling, wire
from grimoire.decisions import Answer, Item, ItemResult, Predicate
from grimoire.llm import LLMClient
from grimoire.llm_errors import LLMError
from grimoire.main import create_app
from grimoire.routes import character_turns
from grimoire.store.inference import resolve as inf

from . import inference_fixtures as fx

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "decide_chain_golden.json"

NATIVE = decisions.NATIVE_BACKEND

SCENARIOS = ("ok", "primary_down", "auth", "schema_refusal", "timeouts", "rate_limited")
SIZES = (2, 9)

#: Row fields that differ between any two runs of the same call.
VOLATILE = frozenset({"ts", "duration_ms", "id", "round_id"})


@pytest.fixture(autouse=True)
def _instant_backoff(monkeypatch):
    monkeypatch.setattr(llm, "RETRY_BASE", 0.0)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    with TestClient(create_app()) as c:
        yield c


def _items(count: int) -> list[Item]:
    return [Item(f"Winifred turns page {k}.", (Predicate("over", "Is the scene over?"),))
            for k in range(count)]


def _messages(messages) -> list[str]:
    """A prompt as the golden records it: its roles, in order."""
    return [m["role"] for m in messages]


def _plain(value):
    return json.loads(json.dumps(value, default=repr, sort_keys=True))


def _kwargs(kwargs: dict) -> dict:
    """A call's keyword arguments as the golden records them: the usage
    holder left out, and a structured call's schema by the questions it asks
    (`decisions.schema` is the prompt contract, not the wire's business)."""
    shown = {k: v for k, v in sorted(kwargs.items()) if k != "usage"}
    if isinstance(shown.get("schema"), dict):
        shown["schema"] = sorted(shown["schema"].get("properties", {}))
    return _plain(shown)


class _Wire:
    """One provider client, recording each call into the run's shared log
    and answering what `script` says."""

    def __init__(self, kind: str, script, log: list) -> None:
        self.kind, self.script, self.log = kind, script, log

    async def stream(self, messages, *args, **kwargs):
        self.log.append(["stream", self.kind, _messages(messages), _plain(list(args)),
                         _kwargs(kwargs)])
        step = self.script("stream", args[0] if args else kwargs.get("model"), messages, kwargs)
        if isinstance(step, BaseException):
            raise step
        yield step
        usage = kwargs.get("usage")
        if usage is not None:
            usage.update({"prompt_tokens": 50, "completion_tokens": 5})

    async def decide(self, item, model, key, **kwargs):
        self.log.append(["decide", self.kind, item.context, model, key, _kwargs(kwargs)])
        step = self.script("decide", model, item, kwargs)
        if isinstance(step, BaseException):
            raise step
        usage = kwargs.get("usage")
        if usage is not None:
            usage.update({"prompt_tokens": 42, "completion_tokens": 1})
        return step


def _reply_for(messages) -> str:
    """Every item a chunk asks about, answered yes."""
    count = messages[-1]["content"].count("Winifred turns page")
    return decisions.render([ItemResult({"over": Answer(True)})] * count, _items(count),
                            explain=False)


def _structured(kwargs: dict) -> bool:
    return any(("schema" in k or "response_format" in k or k == "strict") and v
               for k, v in kwargs.items())


def _script(scenario: str, primary_model: str):
    def script(method, model, payload, kwargs):
        if scenario == "primary_down" and model == primary_model:
            return LLMError("bad_response", f"{model} is down", status=500)
        if scenario == "auth":
            return LLMError("auth", "key refused", status=401)
        if scenario == "schema_refusal" and method == "stream" and _structured(kwargs):
            return LLMError("bad_response",
                            "response_format: json_schema strict mode is not supported",
                            status=400)
        if scenario == "timeouts" and model == primary_model:
            return LLMError("timeout", "read timed out")
        if scenario == "rate_limited" and model == primary_model:
            return LLMError("rate_limit", "slow down", status=429)
        if method == "stream":
            return _reply_for(payload)
        return ItemResult({"over": Answer(True)})
    return script


def _counted(entries: list) -> list:
    """`entries` as a multiset: each distinct entry once, with how many times
    it came, in a fixed order -- a nine-item run files nine like rows."""
    counts: dict[str, int] = {}
    for entry in entries:
        spelled = json.dumps(entry, sort_keys=True)
        counts[spelled] = counts.get(spelled, 0) + 1
    return [[count, json.loads(spelled)] for spelled, count in sorted(counts.items())]


def _named(attempt) -> list:
    if isinstance(attempt, wire.Target):
        return [attempt.provider_id, attempt.model]
    return [attempt.get("id"), llm.effective_model(attempt)]


def _run(monkeypatch, resolved, scenario: str, count: int) -> dict:
    log: list = []
    script = _script(scenario, resolved.attempts[0].model)
    wires = {k: _Wire(k, script, log) for k in ("openrouter", "openai_compatible",
                                               "anthropic", "claude")}
    observed: list = []
    client = LLMClient(**wires, timeout=7, retries=1,  # type: ignore[arg-type]
                       observer=lambda a, e: observed.append([*_named(a),
                                                              e.kind if e else None]))
    recorded: list = []

    def record(cid, sid, task, breakdown, *, model=None, kind="", messages=None, conn=None):
        recorded.append(_plain({
            "model": model, "kind": kind, "report": llm_sampling.report(conn),
            "outcome": [json.loads(s["text"]) for s in breakdown["sections"]
                        if s["id"] == character_turns.OUTCOME_SECTION_ID],
            "messages": _messages(messages or [])}))

    monkeypatch.setattr(character_turns, "_record_prompt", record)
    monkeypatch.setattr(store.prompt_log, "capturing", lambda: True)

    async def capture(messages, outcome, attempt):
        character_turns._capture("cid", "sid", "scene-break", messages, attempt, outcome)

    before = len(list(store.usage.calls(days=1)))
    try:
        got = asyncio.run(inference.decide("scene-break", _items(count), client=client,
                                           resolved=resolved, capture=capture))
        result = {"items": [[r.backend, {q: a.answer for q, a in r.answers.items()}]
                            for r in got.items],
                  "errors": [[e.kind, e.detail] for e in got.errors],
                  "served": [list(s) for s in got.served],
                  "backend": got.backend, "provider": got.provider, "model": got.model}
    except LLMError as exc:
        result = {"raised": [type(exc).__name__, exc.kind, exc.detail]}
    rows = [{k: v for k, v in sorted(row.items()) if k not in VOLATILE}
            for row in list(store.usage.calls(days=1))[before:]]
    return _plain({
        "stages": [[s.mode, s.retries] for s in inference.stages(resolved)],
        "result": result, "wire": log, "observed": observed,
        "rows": _counted(rows), "captures": _counted(recorded)})


def _resolutions(client) -> list[tuple[str, object]]:
    out = []

    def snap(label: str) -> None:
        out.append((label, inf.resolve("scene-break", operation="decide")))

    fx.decide_only(client, fallback=True)
    snap("native+spare")
    fx.decide_only(client, fallback=True, on=fx.SAME_PROVIDER)
    snap("native+same")
    fx.decide_only(client, fallback=False)
    pid = store.sampler_presets.create_preset("Warm", {"temperature": 0.8})
    fx.put_settings(client, {"roles": {"decision": {
        "selection": {"provider": "openrouter", "model": "vendor/decider", "preset": pid}}}})
    snap("native-preset")
    fx.put_settings(client, {"roles": {"decision": {
        "selection": {"provider": "openrouter", "model": "vendor/decider", "preset": pid},
        "fallback": {"provider": "spare", "model": "vendor/spare"}}}})
    snap("native-preset+spare")
    fx.format2(client)
    for provider, model in (("openrouter", "vendor/active"), ("spare", "vendor/spare")):
        rev = store.llm_connections.read_connection_raw(provider)["rev"]
        store.llm_connections.set_cached_models(
            provider, [{"id": model, "params": ["temperature", "structured_outputs"],
                        "outputs": ["text"]}], rev)
    fx.put_settings(client, {"roles": {"decision": {
        "selection": {"provider": "openrouter", "model": "vendor/active"},
        "fallback": {"provider": "spare", "model": "vendor/spare"}}}})
    snap("structured+spare")
    structured = out[-1][1]
    out.append(("structured+native-fallback", dataclasses.replace(structured, attempts=(
        structured.attempts[0],
        dataclasses.replace(structured.attempts[1], decision_mode=NATIVE)))))
    fx.put_settings(client, {"roles": {"decision": {
        "selection": {"provider": "openrouter", "model": "vendor/active", "preset": pid},
        "fallback": {"provider": "spare", "model": "vendor/spare"}}}})
    snap("structured-preset+spare")
    fx.put_settings(client, {"roles": {"decision": {
        "selection": {"provider": "openrouter", "model": "vendor/active"},
        "fallback": {"provider": "openrouter", "model": "vendor/decider"}}}})
    rev = store.llm_connections.read_connection_raw("openrouter")["rev"]
    store.llm_connections.set_cached_models(
        "openrouter", [{"id": "vendor/decider", "outputs": ["decisions"]},
                       {"id": "vendor/active", "outputs": ["text"],
                        "params": ["structured_outputs"]}], rev)
    snap("structured+native-same")
    fx.put_settings(client, {"roles": {"decision": {
        "selection": {"provider": "openrouter", "model": "vendor/active"},
        "fallback": {"provider": ""}}}})
    snap("structured-alone")
    return out


def _observe(client, monkeypatch) -> dict[str, dict]:
    return {f"{label}|{scenario}|{count}": _run(monkeypatch, resolved, scenario, count)
            for label, resolved in _resolutions(client)
            for scenario in SCENARIOS for count in SIZES}


def _flat(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _encode(runs: dict[str, dict]) -> str:
    """Each distinct run once (`records`, by a digest of it), and each run's
    key naming one (`runs`): one compact line apiece, so a diff names the run
    that moved."""
    records = {hashlib.sha256(_flat(run).encode()).hexdigest()[:16]: run
               for run in runs.values()}
    names = {_flat(run): key for key, run in records.items()}
    index = {at: names[_flat(run)] for at, run in runs.items()}

    def lines(mapping: dict) -> str:
        return "{\n" + ",\n".join(f"{json.dumps(k)}: {_flat(v)}"
                                   for k, v in sorted(mapping.items())) + "\n}"

    return f'{{\n"records": {lines(records)},\n"runs": {lines(index)}\n}}\n'


def _golden() -> dict[str, dict]:
    """`{run: what it did}`, read back from the stored file."""
    stored = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return {at: stored["records"][key] for at, key in stored["runs"].items()}


def _check(golden: dict[str, dict], seen: dict[str, dict]) -> None:
    assert set(seen) == set(golden), sorted(set(seen) ^ set(golden))
    for at, run in seen.items():
        assert run == golden[at], at


def test_the_decide_chain_does_what_it_did(client, monkeypatch):
    _check(_golden(), _observe(client, monkeypatch))


def test_the_decide_chain_golden_can_fail(client, monkeypatch):
    """A native stage that sends its sampler preset is caught."""
    golden = _golden()
    monkeypatch.setattr(wire.Target, "without_sampling", lambda self: self)
    with pytest.raises(AssertionError):
        _check(golden, _observe(client, monkeypatch))


@pytest.mark.skipif(os.environ.get("GRIMOIRE_RECORD_DECIDE_GOLDEN") != "1",
                    reason="records the golden once; it is never regenerated")
def test_record_the_decide_chain_golden(client, monkeypatch):
    """Write the golden where none exists."""
    assert not GOLDEN.exists(), f"{GOLDEN} exists, and it is never regenerated"
    GOLDEN.write_text(_encode(_observe(client, monkeypatch)), encoding="utf-8")
