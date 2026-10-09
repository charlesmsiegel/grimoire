"""The adapters' wire calls, frozen (slice I, Task 9; the 9a/9b review's M3).

`fixtures/adapter_wire_golden.json` holds what the facade sent each provider
client -- every positional and keyword argument but the usage holder -- and
what it stamped and reported, for every resolved attempt of every frozen
baseline state (`inference_baseline`, `inference_baseline_c`), in memory and
after the settings migration, and for a set of hand-built edge attempts:

- `ok`: the primary answers (`complete`);
- `fallback`: the primary fails and the fallback it carries answers, or --
  with none -- the call fails;
- `structured`: both of those again with a schema, so a flagged attempt is
  sent its provider's structured mode;
- `single`: the model test's one attempt;
- `native`: a native decision, for an attempt whose kind has an endpoint.

It was recorded at `501fcec` (Task 9b), whose facade the 9a/9b review drove
against the pre-registry facade (`1408de6`) with the same recording clients
and found byte-identical across every frozen state, the fallback, single and
native paths; this suite's own dict spelling was also run against that
pre-registry facade before the golden was committed, and matched it. So it
pins the wire the dict-lowering facade sent, and holds
every later step -- 9c, 9d's removal of the dict door, Task 10's removal of
the lowering -- to it.

**It is never regenerated to make a change pass.** Like
`inference_baseline.json`, its value is that today's code did not write it; a
change that moves a wire call is a change in what grimoire sends a provider,
and it is argued for in its own right, not absorbed here. It was recorded by
`test_record_the_wire_golden`, which writes only where no file exists, and
only when `GRIMOIRE_RECORD_WIRE_GOLDEN=1` asks it to.

It is stored packed (`_pack`, `_encode`): each distinct record once, each
resolution's record keys as one list in `CASES` order, and each distinct
per-state index once, which the states name. `_unpack` reads it back.

Each attempt is driven twice where both spellings exist: as the resolver's
dict (through the facade's shim, until 9d deletes it) and as its chain.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from grimoire import decisions, llm, wire
from grimoire.llm_errors import LLMError

from .test_adapter_registry import HAND_BUILT, ITEM, NATIVE, STATES, _resolved

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "adapter_wire_golden.json"

MESSAGES = [{"role": "system", "content": "You narrate Saltmarch."},
            {"role": "user", "content": "Mara unrolls the charts."}]
SCHEMA = {"type": "object", "properties": {"over": {"type": "boolean"}}}

#: Attempts no baseline state resolves to: no `kind`, an unknown one, a
#: Claude alias, a structured and accounted chain whose route preset follows
#: it onto an Anthropic fallback, a fallback on the primary's own id, two
#: anonymous ones, and a Claude fallback.
EDGE = [*HAND_BUILT,
        {"id": "nk", "model": "vendor/nokind", "api_key": "sk-test-k"},
        {"id": "weird", "kind": "mystery", "model": "x", "api_key": "sk-test-k"},
        {"id": "cl2", "kind": "claude", "model": "sonnet"},
        {"id": "fb", "kind": "openrouter", "model": "a", "api_key": "sk-test-k",
         "sampling": {"preset_id": "g", "preset_name": "G", "scope": "global",
                      "params": {"temperature": 0.3}},
         llm.FALLBACK_KEY: {
             "id": "fb2", "kind": "anthropic", "model": "claude-haiku-4-5",
             "api_key": "sk-test-k2",
             "sampling": {"preset_id": "own", "preset_name": "Own", "scope": "connection",
                          "params": {"top_k": 5}},
             llm.STRUCTURED_KEY: True,
             "_account": {"operation": "decide", "role": "decision", "billing": "metered",
                          "decision_mode": "structured"}},
         llm.STRUCTURED_KEY: True,
         "_account": {"operation": "decide", "role": "decision", "billing": "metered",
                      "decision_mode": "structured"}},
        {"id": "same", "kind": "openrouter", "model": "a", "api_key": "sk-test-k",
         llm.FALLBACK_KEY: {"id": "same", "kind": "openrouter", "model": "b",
                            "api_key": "sk-test-k"}},
        {"id": "", "kind": "openrouter", "model": "a", "api_key": "sk-test-k",
         llm.FALLBACK_KEY: {"id": "", "kind": "openrouter", "model": "b",
                            "api_key": "sk-test-k"}},
        {"id": "toclaude", "kind": "openrouter", "model": "a", "api_key": "sk-test-k",
         llm.FALLBACK_KEY: {"id": "c", "kind": "claude", "model": ""}}]

KINDS = ("openrouter", "claude", "openai_compatible", "anthropic")


class _Wire:
    """One provider client: records each call, answers with one chunk and the
    counts a provider reports, and fails the models named in `failing`."""

    def __init__(self, failing: frozenset[str]) -> None:
        self.calls: list = []
        self.failing = failing

    def _record(self, method: str, args: tuple, kwargs: dict) -> None:
        shown = ["<messages>" if a == MESSAGES else "<item>" if a == ITEM else copy.deepcopy(a)
                 for a in args]
        self.calls.append([method, shown,
                           {k: copy.deepcopy(v) for k, v in sorted(kwargs.items()) if k != "usage"}])

    async def stream(self, *args, **kwargs):
        self._record("stream", args, kwargs)
        model = args[1] if len(args) > 1 else kwargs.get("model")
        if model in self.failing:
            raise LLMError("bad_response", f"{model} is down", status=500)
        yield "ok"
        usage = kwargs.get("usage")
        if usage is not None:
            usage.update({"prompt_tokens": 5, "completion_tokens": 2, "cost_usd": 0.001})

    async def decide(self, *args, **kwargs):
        self._record("decide", args, kwargs)
        return decisions.ItemResult(answers={}, backend="native")


def _named(attempt) -> list:
    """An attempt the facade handed back, either spelling: its id and model."""
    if isinstance(attempt, wire.Target):
        return [attempt.provider_id, attempt.model]
    if isinstance(attempt, dict):
        return [attempt.get("id", ""), llm.effective_model(attempt)]
    return [repr(attempt)]


def _drive(case: str, attempt, failing: frozenset[str]) -> dict:
    """One call through a real facade over recording clients: its wire calls,
    the holder it stamped (`ATTEMPTED` by name), what it reported, and its
    error."""
    wires = {kind: _Wire(failing) for kind in KINDS}
    observed: list = []
    client = llm.LLMClient(**wires, retries=0, timeout=7,  # type: ignore[arg-type]
                           observer=lambda a, e: observed.append(
                               [*_named(a), e.kind if e else None]))
    usage: dict = {}
    error = None
    try:
        if case == "single":
            asyncio.run(client.single(MESSAGES, attempt, usage))
        elif case == "native":
            asyncio.run(client.decide_native(ITEM, attempt, usage))
        else:
            schema = SCHEMA if case.startswith("structured") else None
            asyncio.run(client.complete(MESSAGES, attempt, usage, schema=schema))
    except LLMError as exc:
        error = [type(exc).__name__, exc.kind, exc.detail]
    holder = {k: v for k, v in sorted(usage.items()) if not k.startswith("_")}
    holder["attempted"] = _named(usage.get(llm.ATTEMPTED))
    return {"wire": {k: w.calls for k, w in wires.items() if w.calls},
            "holder": holder, "observed": observed, "error": error}


def _cases(conn: dict) -> list[tuple[str, frozenset[str]]]:
    primary = frozenset({llm.effective_model(conn)})
    cases = [("ok", frozenset()), ("fallback", primary), ("structured", frozenset()),
             ("structured_fallback", primary), ("single", frozenset())]
    if conn.get("kind", "openrouter") in llm.NATIVE_DECISION_KINDS:
        cases.append(("native", frozenset()))
    return cases


def _spellings(conn: dict, chain: wire.Chain | None, case: str) -> list:
    """The attempt, as each caller may hand it: the dict and its chain (the
    target alone, for `single`). A native decision takes the dict alone until
    Task 9c moves it onto a target."""
    if case == "native":
        return [conn]
    if chain is None:
        chain = wire.from_lowered(conn)
    return [conn, chain.primary if case == "single" else chain]


def _key(record: dict) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()[:16]


def _observe_state(state: str, migrated: bool, tmp_path) -> dict[str, list[dict]]:
    """`{where/case: [the record per spelling]}` for one state."""
    out: dict[str, list[dict]] = {}
    for where, resolved in _resolved(state, tmp_path, migrated=migrated):
        if resolved.operation == "embed" or not resolved.conn:
            continue
        for case, failing in _cases(resolved.conn):
            out[f"{where}/{case}"] = [_drive(case, attempt, failing)
                                      for attempt in _spellings(resolved.conn, resolved.chain,
                                                                case)]
    return out


def _observe_edges() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for conn in [*EDGE, *NATIVE]:
        for case, failing in _cases(conn):
            out[f"{conn['id'] or '<anonymous>'}/{case}"] = [
                _drive(case, attempt, failing) for attempt in _spellings(conn, None, case)]
    return out


#: Every case, in the order an index lists a resolution's keys: `native` is
#: last, and only there for a kind with a native endpoint.
CASES = ("ok", "fallback", "structured", "structured_fallback", "single", "native")


def _pack(golden: dict) -> dict:
    """The golden as it is stored: each resolution's record keys as one list
    in `CASES` order, and each distinct per-state index once (`indexes`),
    which every state names (`states`). Lossless: `_unpack` reverses it."""
    def packed(index: dict[str, str]) -> dict[str, list[str]]:
        by_where: dict[str, dict[str, str]] = {}
        for at, key in index.items():
            where, case = at.rsplit("/", 1)
            by_where.setdefault(where, {})[case] = key
        out = {}
        for where, cases in by_where.items():
            # The cases a resolution has are always the first of `CASES`.
            assert set(cases) == set(CASES[:len(cases)]), where
            out[where] = [cases[case] for case in CASES[:len(cases)]]
        return out

    indexes: dict[str, dict] = {}
    names: dict[str, str] = {}
    states: dict[str, str] = {}
    for state in sorted(golden["states"]):
        index = packed(golden["states"][state])
        spelled = json.dumps(index, sort_keys=True)
        if spelled not in names:
            names[spelled] = f"i{len(names)}"
            indexes[names[spelled]] = index
        states[state] = names[spelled]
    return {"states": states, "indexes": indexes, "edges": packed(golden["edges"]),
            "records": golden["records"]}


def _unpack(stored: dict) -> dict:
    """`{"states": {state: {where/case: key}}, "edges": {...}, "records"}`."""
    def expanded(index: dict[str, list[str]]) -> dict[str, str]:
        return {f"{where}/{case}": key for where, keys in index.items()
                for case, key in zip(CASES, keys, strict=False)}

    return {"states": {state: expanded(stored["indexes"][name])
                       for state, name in stored["states"].items()},
            "edges": expanded(stored["edges"]), "records": stored["records"]}


def _encode(stored: dict) -> str:
    """The stored golden as text: one line per record, per resolution and per
    state, each written compactly -- a diff names the line that moved."""
    def flat(value) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    def lines(mapping: dict, inner) -> str:
        return "{\n" + ",\n".join(f"{json.dumps(k)}: {inner(v)}"
                                   for k, v in sorted(mapping.items())) + "\n}"

    return "{\n" + ",\n".join([
        f'"edges": {lines(stored["edges"], flat)}',
        f'"indexes": {lines(stored["indexes"], lambda ix: lines(ix, flat))}',
        f'"records": {lines(stored["records"], flat)}',
        f'"states": {lines(stored["states"], flat)}']) + "\n}\n"


def _golden() -> dict:
    return _unpack(json.loads(GOLDEN.read_text(encoding="utf-8")))


def _check(index: dict[str, str], records: dict[str, dict], seen: dict[str, list[dict]]) -> None:
    assert set(seen) == set(index), sorted(set(seen) ^ set(index))
    for where, by_spelling in seen.items():
        for record in by_spelling:
            assert record == records[index[where]], where


@pytest.mark.parametrize("state", sorted(STATES))
@pytest.mark.parametrize("migrated", [False, True])
def test_every_baseline_attempt_sends_the_frozen_wire(state, migrated, tmp_path):
    golden = _golden()
    _check(golden["states"][f"{state}|{'migrated' if migrated else 'memory'}"],
           golden["records"], _observe_state(state, migrated, tmp_path))


def test_every_edge_attempt_sends_the_frozen_wire():
    golden = _golden()
    _check(golden["edges"], golden["records"], _observe_edges())


def test_the_golden_can_fail(monkeypatch):
    """A facade that drops a sampler parameter is caught."""
    golden = _golden()
    real = llm.llm_sampling.split
    monkeypatch.setattr(llm.adapters.llm_sampling, "split",
                        lambda t: ({}, real(t)[1]))
    with pytest.raises(AssertionError):
        _check(golden["edges"], golden["records"], _observe_edges())


@pytest.mark.skipif(os.environ.get("GRIMOIRE_RECORD_WIRE_GOLDEN") != "1",
                    reason="records the golden once; it is never regenerated")
def test_record_the_wire_golden(tmp_path):
    """Write the golden where none exists. Every spelling of an attempt must
    agree before anything is written."""
    assert not GOLDEN.exists(), f"{GOLDEN} exists, and it is never regenerated"
    records: dict[str, dict] = {}

    def index(seen: dict[str, list[dict]]) -> dict[str, str]:
        out = {}
        for where, by_spelling in seen.items():
            first = by_spelling[0]
            assert all(r == first for r in by_spelling), where
            out[where] = _key(first)
            records[out[where]] = first
        return out

    states = {}
    for n, state in enumerate(sorted(STATES)):
        for migrated in (False, True):
            states[f"{state}|{'migrated' if migrated else 'memory'}"] = index(
                _observe_state(state, migrated, tmp_path / f"{n}-{migrated}"))
    GOLDEN.write_text(_encode(_pack({"states": states, "edges": index(_observe_edges()),
                                     "records": records})), encoding="utf-8")
