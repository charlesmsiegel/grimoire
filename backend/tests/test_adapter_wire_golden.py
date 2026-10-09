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

Each attempt was recorded driven twice, as the resolver's dict (through the
facade's shim) and as its chain, and every record held both to agree. Task
9d deleted the shim, so each attempt is now driven once, as its chain (the
target alone for `single` and a native decision), and still matches the
record both spellings wrote. Task 10 deleted the dict, so the edges, recorded
from connection dicts, are spelled as the chains those dicts read as
(`test_adapter_registry.hand`, field for field what `wire.from_lowered` read
from each), under the names the golden records them by.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from grimoire import adapters, decisions, llm, wire
from grimoire.llm_errors import LLMError

from . import inference_baseline as baseline
from .test_adapter_registry import HAND_BUILT, ITEM, NATIVE, PASSES, STATES, _resolved, hand

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "adapter_wire_golden.json"

MESSAGES = [{"role": "system", "content": "You narrate Saltmarch."},
            {"role": "user", "content": "Mara unrolls the charts."}]
SCHEMA = {"type": "object", "properties": {"over": {"type": "boolean"}}}

#: An account stamped as a structured decision files it.
_DECIDED = wire.Account(operation="decide", role="decision", billing="metered",
                        decision_mode="structured")

#: Attempts no baseline state resolves to, each under the name the golden
#: records it by: a dict with no `kind` (read as OpenRouter's), an unknown
#: kind, a Claude alias, a structured and accounted chain whose route preset
#: follows it onto an Anthropic fallback, a fallback on the primary's own id,
#: two anonymous ones, and a Claude fallback (an unset model, the default).
EDGE: list[tuple[str, wire.Chain]] = [
    *((t.provider_id, wire.Chain(t)) for t in HAND_BUILT),
    ("nk", wire.Chain(hand("nk", "openrouter", "vendor/nokind", api_key="sk-test-k"))),
    ("weird", wire.Chain(hand("weird", "mystery", "x", api_key="sk-test-k"))),
    ("cl2", wire.Chain(hand("cl2", "claude", "sonnet"))),
    ("fb", wire.Chain(
        hand("fb", "openrouter", "a", api_key="sk-test-k",
             sampling=wire.Sampling("g", "G", "global", {"temperature": 0.3}),
             structured=True, account=_DECIDED),
        hand("fb2", "anthropic", "claude-haiku-4-5", api_key="sk-test-k2",
             sampling=wire.Sampling("own", "Own", "connection", {"top_k": 5}),
             structured=True, account=_DECIDED))),
    ("same", wire.Chain(hand("same", "openrouter", "a", api_key="sk-test-k"),
                        hand("same", "openrouter", "b", api_key="sk-test-k"))),
    ("", wire.Chain(hand("", "openrouter", "a", api_key="sk-test-k"),
                    hand("", "openrouter", "b", api_key="sk-test-k"))),
    ("toclaude", wire.Chain(hand("toclaude", "openrouter", "a", api_key="sk-test-k"),
                            hand("c", "claude", "opus"))),
]

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
    """An attempt the facade handed back: its id and model."""
    if isinstance(attempt, wire.Target):
        return [attempt.provider_id, attempt.model]
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


def _cases(chain: wire.Chain) -> list[tuple[str, frozenset[str]]]:
    primary = frozenset({chain.primary.model})
    cases = [("ok", frozenset()), ("fallback", primary), ("structured", frozenset()),
             ("structured_fallback", primary), ("single", frozenset())]
    if adapters.decides_natively(chain.primary.kind):
        cases.append(("native", frozenset()))
    return cases


def _spellings(chain: wire.Chain, case: str) -> list:
    """The attempt, as the facade takes it: its chain (the target alone, for
    `single` and a native decision)."""
    return [chain.primary if case in ("single", "native") else chain]


def _key(record: dict) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()[:16]


def _observe_state(state: str, stage: str, tmp_path) -> dict[str, list[dict]]:
    """`{where/case: [the record per spelling]}` for one state, in the pass
    `stage` names (`test_adapter_registry.PASSES`)."""
    out: dict[str, list[dict]] = {}
    for where, resolved in _resolved(state, tmp_path, stage=stage):
        if resolved.operation == "embed" or resolved.chain is None:
            continue
        for case, failing in _cases(resolved.chain):
            out[f"{where}/{case}"] = [_drive(case, attempt, failing)
                                      for attempt in _spellings(resolved.chain, case)]
    return out


def _observe_edges() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for name, chain in [*EDGE, *((t.provider_id, wire.Chain(t)) for t in NATIVE)]:
        for case, failing in _cases(chain):
            out[f"{name or '<anonymous>'}/{case}"] = [
                _drive(case, attempt, failing) for attempt in _spellings(chain, case)]
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


def _recorded_as(stage: str) -> str:
    """The golden's name for the pass `stage` is held to. It was recorded in
    memory and migrated (before Task 6, when the migration retired nothing);
    a retired store sends what the migrated one did, so it is held to that
    pass's records."""
    return "memory" if stage == "memory" else "migrated"


#: The reroll overrides that name a provider (`connection_id`), alone or
#: with a model. The golden's `memory` pass recorded them as a format-1 store
#: answered them in slice I before the user's ruling of 2026-10-09 (spec
#: review F1): with format-2 meaning, the standing model and preset. That
#: ruling restored format 1's own -- the named connection's model and preset,
#: as `main` sends them -- so in that pass these cells are excused from the
#: golden, BY NAME, and held instead to the frozen baseline JSON the golden
#: does not replace: each `ok` attempt must be sent to the provider and model
#: that JSON recorded (`_format_1_reroll_sent`), and
#: `test_inference_equivalence*.test_resolution_matches_the_baseline` holds
#: the whole cell to it. The golden itself is not regenerated, and every other
#: cell, pass and edge is held to it unchanged.
FORMAT_1_REROLLS = frozenset(f"override:{name}"
                             for name, body in baseline.OVERRIDE_BODIES.items()
                             if body.get("connection_id"))


def _format_1_reroll_sent(state: str, seen: dict[str, list[dict]]) -> None:
    """Each excused cell's `ok` attempt went where the frozen baseline JSON
    recorded that reroll going."""
    family, name = STATES[state]
    recorded = json.loads(family.FIXTURE.read_text(encoding="utf-8"))[name]["overrides"]
    for where, records in seen.items():
        cell, _, case = where.partition("/")
        if cell not in FORMAT_1_REROLLS or case != "ok":
            continue
        want = recorded[cell.removeprefix("override:")]
        for record in records:
            assert [record["observed"][0][:2]] == [[want["conn"], want["model"]]], (
                state, where, record["observed"], want)


@pytest.mark.parametrize("state", sorted(STATES))
@pytest.mark.parametrize("stage", PASSES)
def test_every_baseline_attempt_sends_the_frozen_wire(state, stage, tmp_path):
    golden = _golden()
    index = golden["states"][f"{state}|{_recorded_as(stage)}"]
    seen = _observe_state(state, stage, tmp_path)
    if stage == "memory":
        excused = {w for w in set(index) | set(seen)
                   if w.partition("/")[0] in FORMAT_1_REROLLS}
        _format_1_reroll_sent(state, seen)
        index = {w: k for w, k in index.items() if w not in excused}
        seen = {w: r for w, r in seen.items() if w not in excused}
    _check(index, golden["records"], seen)


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
        for stage in ("memory", "migrated"):
            states[f"{state}|{stage}"] = index(
                _observe_state(state, stage, tmp_path / f"{n}-{stage}"))
    GOLDEN.write_text(_encode(_pack({"states": states, "edges": index(_observe_edges()),
                                     "records": records})), encoding="utf-8")
