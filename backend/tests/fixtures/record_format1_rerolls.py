"""Records `adapter_wire_format1_rerolls.json` -- against ORIGIN/MAIN's code
(4e822aa), never this tree's: it drives `main`'s dict facade, which slice I
deleted, so it cannot run here.

Why it exists (slice I, fix round 2 of the brutal reviews). The adapter wire
golden's `memory` pass (`adapter_wire_golden.json`) was recorded inside
slice I after a format-1 store's rerolls had taken format-2 meaning. The
user's ruling of 2026-10-09 (spec review F1) restored main's: a reroll naming
a provider runs that connection's own model and preset. So the golden's
records for those cells describe behaviour that was reverted -- and the
golden is never regenerated. This file freezes, from main itself, exactly
the cells whose golden records differ from what main sends: each
`(state, override cell)` whose six legs main records differently from the
golden's `memory` pass, every leg (`ok`, `fallback`, `structured`,
`structured_fallback`, `single`, `native`) in full -- the wire body, the
holder, what was observed and the error. `test_adapter_wire_golden` holds
those cells to it and every other cell to the golden.

How it was run, from a detached worktree at origin/main, with the store's
automatic migration off::

    cd <main worktree>/backend
    PYTHONPATH=src:. python <this file> <out.json> <this tree's adapter_wire_golden.json>

The records are made exactly as `test_adapter_wire_golden._drive` makes
them -- the same recording clients, messages, item and schema -- with the
attempt spelled as main's connection dict (`_named` reads its id and
effective model). Never re-run to make a change pass.
"""

import asyncio
import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ["GRIMOIRE_INFERENCE_AUTOMIGRATE"] = "0"
from fastapi import HTTPException

from grimoire import decisions, llm, routes
from grimoire.decisions import Choice, Item, Option, Predicate, Score
from grimoire.llm_errors import LLMError
from tests import inference_baseline as baseline
from tests import inference_baseline_c as baseline_c

STATES = {**{f"base:{k}": (baseline, k) for k in baseline.STATES},
          **{f"c:{k}": (baseline_c, k) for k in baseline_c.STATES}}
MARA = Option("characters:mara", "Mara, the cartographer.")
WINIFRED = Option("characters:winifred", "Winifred, the harbourmaster.")
ITEM = Item("Mara and Winifred argue over the Saltmarch charts.",
            (Predicate("over", "Has the scene reached its end?"),
             Choice("speaker", "Who speaks next?", (MARA, WINIFRED), allow_none=True),
             Score("tension", "How tense is the exchange?", ("Calm.", "Uneasy.", "Heated."))))
MESSAGES = [{"role": "system", "content": "You narrate Saltmarch."},
            {"role": "user", "content": "Mara unrolls the charts."}]
SCHEMA = {"type": "object", "properties": {"over": {"type": "boolean"}}}
KINDS = ("openrouter", "claude", "openai_compatible", "anthropic")


class _Wire:
    def __init__(self, failing):
        self.calls = []
        self.failing = failing

    def _record(self, method, args, kwargs):
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


def _named(attempt):
    if isinstance(attempt, dict):
        return [attempt.get("id", ""), llm.effective_model(attempt)]
    return [repr(attempt)]


def _drive(case, conn, failing):
    wires = {kind: _Wire(failing) for kind in KINDS}
    observed = []
    client = llm.LLMClient(**wires, retries=0, timeout=7,
                           observer=lambda a, e: observed.append([*_named(a), e.kind if e else None]))
    usage = {}
    error = None
    try:
        if case == "single":
            asyncio.run(client.single(MESSAGES, conn, usage))
        elif case == "native":
            asyncio.run(client.decide_native(ITEM, conn, usage))
        else:
            schema = SCHEMA if case.startswith("structured") else None
            asyncio.run(client.complete(MESSAGES, conn, usage, schema=schema))
    except LLMError as exc:
        error = [type(exc).__name__, exc.kind, exc.detail]
    holder = {k: v for k, v in sorted(usage.items()) if not k.startswith("_")}
    holder["attempted"] = _named(usage.get(llm.ATTEMPTED))
    return {"wire": {k: w.calls for k, w in wires.items() if w.calls},
            "holder": holder, "observed": observed, "error": error}


def _cases(conn):
    primary = frozenset({llm.effective_model(conn)})
    cases = [("ok", frozenset()), ("fallback", primary), ("structured", frozenset()),
             ("structured_fallback", primary), ("single", frozenset())]
    if conn.get("kind", "openrouter") in llm.NATIVE_DECISION_KINDS:
        cases.append(("native", frozenset()))
    return cases


def _golden_records(path):
    """`{"<state>|<where>/<case>": record}` for the golden's memory pass."""
    stored = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = ("ok", "fallback", "structured", "structured_fallback", "single", "native")
    out = {}
    for name_state, index_name in stored["states"].items():
        state, _, stage = name_state.rpartition("|")
        if stage != "memory":
            continue
        for where, keys in stored["indexes"][index_name].items():
            for case, key in zip(cases, keys, strict=False):
                out[f"{state}|{where}/{case}"] = stored["records"][key]
    return out


def main(out_path, golden_path):
    out = {}
    for state, (family, name) in sorted(STATES.items()):
        with tempfile.TemporaryDirectory() as tmp, baseline.client_at(Path(tmp)) as client:
            ctx = family.STATES[name](client)
            for body_name, body in baseline.OVERRIDE_BODIES.items():
                if not body.get("connection_id"):
                    continue
                try:
                    resolved, _routed = routes.common.override_inference(
                        SimpleNamespace(**body), "regenerate", ctx["cid"])
                except HTTPException:
                    continue
                if resolved.conn is None:
                    continue
                for case, failing in _cases(resolved.conn):
                    out[f"{state}|override:{body_name}/{case}"] = _drive(case, resolved.conn, failing)
    golden = _golden_records(golden_path)
    cells = {key.rpartition("/")[0] for key in out}
    differing = {cell for cell in cells
                 if any(golden.get(key) != record for key, record in out.items()
                        if key.rpartition("/")[0] == cell)
                 or any(key.rpartition("/")[0] == cell and key not in out for key in golden)}
    kept = {key: record for key, record in out.items() if key.rpartition("/")[0] in differing}
    Path(out_path).write_text(json.dumps(kept, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    print(len(kept), "records,", len(differing), "cells")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
